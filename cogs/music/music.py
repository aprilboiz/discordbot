import asyncio
import functools

import discord
from cogs.components.discord_embed import Embed
from cogs.music.controller import PlayerManager
from cogs.music.view.view import QueueView
from core.error_handler import ErrorHandler
from discord import app_commands
from discord.ext import commands
from utils.voice_helpers import ensure_same_channel, get_or_create_audio
from typing import Literal, Optional


# https://github.com/Rapptz/discord.py/discussions/8372#discussioncomment-3459014
def ensure_voice(f):
    @functools.wraps(f)
    async def callback(self, interaction: discord.Interaction, *args, **kwargs) -> None:
        await interaction.response.defer(thinking=True)
        ctx = await self.bot.get_context(interaction)
        if ctx.voice_client is None:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect(self_deaf=True)
            else:
                await ctx.send(
                    "You are not connected to a voice channel.", ephemeral=True
                )
                return
        await f(self, interaction, *args, **kwargs)

    return callback


class Music(commands.Cog):
    """Music cog for my OneNine4 Bot"""

    def __init__(
        self,
        bot: commands.Bot,
        player_manager: PlayerManager,
        error_handler: ErrorHandler,
    ) -> None:
        self.bot = bot
        self.player_manager = player_manager
        self.error_handler = error_handler

    async def set_reply_timeout(
        self, interaction: discord.Interaction, timeout: float = 15.0
    ) -> None:
        await asyncio.sleep(timeout)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                content="This command could not be processed in time. Please try again.",
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        if ctx.command and ctx.command.cog_name == self.__class__.__name__:
            await self.error_handler.handle_command_error(ctx, error)
        else:
            if isinstance(error, commands.CommandError):
                await self.bot.on_command_error(ctx, error)
            else:
                await self.bot.on_command_error(ctx, commands.CommandError(str(error)))

    @app_commands.command(
        name="search", description="Search for a song and play add it to the queue."
    )
    @ensure_voice
    async def search(
        self,
        interaction: discord.Interaction,
        query: str,
        provider: Optional[Literal["youtube", "soundcloud", "spotify"]] = "youtube",
    ) -> None:
        if not interaction.response.is_done():
            await self.set_reply_timeout(interaction)
        ctx = await self.bot.get_context(interaction)

        if not await ensure_same_channel(ctx):
            return

        if interaction.guild_id:
            audio = get_or_create_audio(self.bot, interaction.guild_id)
            await audio.process_search(ctx, query, provider)

    @app_commands.command(
        name="play", description="Adds a song or playlist to the queue and plays it."
    )
    @ensure_voice
    async def p(self, interaction: discord.Interaction, query: str) -> None:
        if not interaction.response.is_done():
            await self.set_reply_timeout(interaction)
        ctx = await self.bot.get_context(interaction)

        if not await ensure_same_channel(ctx):
            return

        if interaction.guild_id:
            audio = get_or_create_audio(self.bot, interaction.guild_id)
            await audio.process_query(ctx, query)

    @app_commands.command(
        name="playnext",
        description="You just found a great song and want to listen it right now.",
    )
    @ensure_voice
    async def pn(self, interaction: discord.Interaction, query: str) -> None:
        if not interaction.response.is_done():
            await self.set_reply_timeout(interaction)
        ctx = await self.bot.get_context(interaction)

        if not await ensure_same_channel(ctx):
            return

        if interaction.guild_id:
            audio = get_or_create_audio(self.bot, interaction.guild_id)
            await audio.process_query(ctx, query, True)

    @app_commands.command(name="queue", description="Show the current playlist.")
    async def queue(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        ctx = await self.bot.get_context(interaction)
        if interaction.guild_id and ctx.voice_client:
            player = self.player_manager.players[interaction.guild_id]
            playlist = player.playlist_manager.playlist

            if playlist.size() == 0:
                await ctx.send(
                    embed=Embed().error(
                        description="There are no songs in the playlist."
                    )
                )
            else:
                # Get queue information
                queue_list = await playlist.get_list()
                current_song_obj = player.playlist_manager.current_song
                current_song_meta = None
                if current_song_obj:
                    # Create a simple object with the essential info for display
                    class CurrentSongDisplay:
                        def __init__(self, title: str, author: str, duration: str):
                            self.title = title
                            self.author = author
                            self.duration = duration

                    current_song_meta = CurrentSongDisplay(
                        title=current_song_obj.title,
                        author=current_song_obj.uploader,
                        duration=current_song_obj.duration,
                    )

                priority_count = playlist.priority_size()

                # Calculate total duration
                total_duration = playlist.time_wait()

                # Create enhanced queue view
                view = QueueView(
                    tracks=queue_list,
                    callback=player.handle_track_selection_in_playlist,
                    current_song=current_song_meta,
                    priority_count=priority_count,
                    total_duration=total_duration,
                    timeout=180,
                )
                view.message = await ctx.send(embed=view.create_embed(), view=view)
        else:
            await ctx.send(
                embed=discord.Embed(
                    color=discord.Color.red(), description="No bot in voice channel!"
                )
            )

    @app_commands.command(
        name="skip",
        description="This song is so terrible? Just use this command to skip.",
    )
    async def skip(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        ctx = await self.bot.get_context(interaction)
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await ctx.send(embed=Embed().ok(description="Song skipped."))
        else:
            await ctx.send(embed=Embed().error(description="No songs are playing."))

    @app_commands.command(
        name="stop",
        description="Clear the playlist, stop playing music, and leave the channel.",
    )
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        ctx = await self.bot.get_context(interaction)
        if ctx.voice_client and interaction.guild_id:
            self.player_manager.players[interaction.guild_id].destroy()
            del self.player_manager.players[interaction.guild_id]
            await ctx.send(embed=Embed().ok("Thanks for using the bot ^^"))
            await ctx.voice_client.disconnect()
        else:
            await ctx.send(embed=Embed().error(description="No songs are playing."))

    @app_commands.command(
        name="come", description="Tell the bot to come to your voice channel"
    )
    @ensure_voice
    async def come(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        ctx = await self.bot.get_context(interaction)
        if ctx.author.voice.channel != ctx.voice_client.channel:
            ctx.voice_client.pause()
            await ctx.voice_client.move_to(ctx.author.voice.channel)
            await ctx.guild.change_voice_state(
                channel=ctx.author.voice.channel, self_deaf=True
            )
            await asyncio.sleep(1)
            ctx.voice_client.resume()
            await ctx.send(embed=Embed().ok("Switched to the new voice channel!"))
        else:
            await ctx.send(embed=Embed().error("No need to change the voice channel!"))

    @app_commands.command(
        name="trending", description="Phát một bài nhạc đang trending trên YouTube"
    )
    @ensure_voice
    async def trending(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await self.set_reply_timeout(interaction)
        ctx = await self.bot.get_context(interaction)

        if not await ensure_same_channel(ctx):
            return

        if interaction.guild_id:
            audio = get_or_create_audio(self.bot, interaction.guild_id)
            await audio.process_trending(ctx)

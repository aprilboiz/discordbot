import asyncio
import functools
import wavelink

import discord
from cogs.components.discord_embed import Embed
from cogs.music.controller import PlayerManager
from cogs.music.view.view import QueueView
from core.error_handler import ErrorHandler
from discord import app_commands
from discord.ext import commands
from utils.voice_helpers import ensure_same_channel, get_or_create_audio
from typing import Literal, Optional
import os


# https://github.com/Rapptz/discord.py/discussions/8372#discussioncomment-3459014
def ensure_voice(f):
    @functools.wraps(f)
    async def callback(self, interaction: discord.Interaction, *args, **kwargs) -> None:
        if not interaction.response.is_done():
             await interaction.response.defer(thinking=True)
        ctx = await self.bot.get_context(interaction)
        if ctx.voice_client is None:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect(self_deaf=True)
            else:
                await interaction.followup.send(
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

    async def cog_load(self) -> None:
        """Called when the cog is loaded. Connects to Lavalink."""
        nodes = [
            wavelink.Node(
                uri=os.getenv("LAVALINK_URI", "http://lavalink:2333"),
                password=os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
            )
        ]
        await wavelink.Pool.connect(nodes=nodes, client=self.bot, cache_capacity=100)

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

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # Ignore if no change in channel
        if before.channel == after.channel:
            return

        guild_id = member.guild.id

        # Handle Bot Disconnection/Movement
        if member.id == self.bot.user.id:
            if after.channel is None:
                # Bot disconnected
                if guild_id in self.player_manager.players:
                    self.player_manager.players[guild_id].destroy()
                    del self.player_manager.players[guild_id]
            elif before.channel is not None and after.channel != before.channel:
                # Bot moved - discord.py handles the voice connection move
                # We might need to check if we should pause? Usually no.
                pass
            return

        # Handle User Disconnection/Movement (AFK Logic)
        # Check the channel where the bot is
        # We need to get the GuildMusicManager wrapper to handle AFK
        player_wrapper = self.player_manager.get_player(guild_id)

        # Also check if voice client exists
        voice_client = member.guild.voice_client
        if not voice_client:
            return

        bot_channel = voice_client.channel

        # If the user left the bot's channel
        if before.channel == bot_channel:
            # Check if bot is alone
            if len(bot_channel.members) == 1: # Only bot
                if player_wrapper:
                    player_wrapper.on_channel_empty()

        # If a user joined the bot's channel
        if after.channel == bot_channel:
            # Cancel AFK timer
            if player_wrapper:
                player_wrapper.on_channel_filled()

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
            player: wavelink.Player = ctx.voice_client

            if player.queue.is_empty and not player.playing:
                await ctx.send(
                    embed=Embed().error(
                        description="There are no songs in the playlist."
                    )
                )
            else:
                # Get queue information
                queue_list = list(player.queue)
                current_song = player.current

                # Format duration for current song if exists
                # wavelink tracks have length in ms
                current_song_meta = None
                if current_song:
                    from utils import convert_to_time
                    duration_str = convert_to_time(current_song.length / 1000)

                    # Mapping Wavelink Playable to simple display object
                    class CurrentSongDisplay:
                        def __init__(self, title: str, author: str, duration: str):
                            self.title = title
                            self.author = author
                            self.duration = duration

                    current_song_meta = CurrentSongDisplay(
                        title=current_song.title,
                        author=current_song.author,
                        duration=duration_str
                    )

                # Create enhanced queue view
                # We need to handle track selection callback
                # Since we don't have our custom playlist manager anymore, we can't easily move tracks
                # For now, callback can just be a dummy or we implement move logic

                async def dummy_callback(interaction, track):
                    await interaction.response.send_message("Track selection not supported in Wavelink mode yet.", ephemeral=True)

                view = QueueView(
                    tracks=queue_list,
                    callback=dummy_callback,
                    current_song=current_song_meta,
                    priority_count=0, # Wavelink queue doesn't easily expose priority segments
                    total_duration="Unknown", # Calculation omitted for brevity
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
        if ctx.voice_client:
            player: wavelink.Player = ctx.voice_client
            if player.playing:
                await player.skip(force=True)
                await ctx.send(embed=Embed().ok(description="Song skipped."))
            else:
                await ctx.send(embed=Embed().error(description="No songs are playing."))
        else:
            await ctx.send(embed=Embed().error(description="Not connected."))

    @app_commands.command(
        name="stop",
        description="Clear the playlist, stop playing music, and leave the channel.",
    )
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        ctx = await self.bot.get_context(interaction)
        if ctx.voice_client:
            # Disconnect cleans up the player in Wavelink
            await ctx.voice_client.disconnect()
            await ctx.send(embed=Embed().ok("Thanks for using the bot ^^"))
        else:
            await ctx.send(embed=Embed().error(description="Not connected."))

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
        ctx = await self.bot.get_context(interaction)

        if not await ensure_same_channel(ctx):
            return

        if interaction.guild_id:
            audio = get_or_create_audio(self.bot, interaction.guild_id)
            await audio.process_trending(ctx)

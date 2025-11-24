import asyncio
import logging
from typing import Optional

import discord
import constants
from cogs.music.core.song import Song
from cogs.music.manager import PlaylistManager
from cogs.components.discord_embed import Embed
from utils import Timer, convert_to_second, get_time

_log = logging.getLogger(__name__)

class MusicPlayer:
    def __init__(self, bot, guild_id: int, playlist_manager: PlaylistManager):
        self.bot = bot
        self.guild_id = guild_id
        self.playlist_manager = playlist_manager
        self.is_playing = False
        self.timer: Optional[Timer] = None
        self.lock = asyncio.Lock()

    def destroy(self):
        if self.timer:
            self.timer.cancel()
        if self.playlist_manager:
            self.playlist_manager.playlist.clear()

    async def play_next(self, ctx: Optional[discord.ext.commands.Context] = None) -> None:
        async with self.lock:
            if not self.is_playing:
                self._start_disconnect_timer(ctx) # Start timer if idle

                max_retries = 5
                retries = 0

                while retries < max_retries:
                    try:
                        song: Optional[Song] = (
                            await self.playlist_manager.playlist.get_next_prepared()
                        )
                        if song is None:
                            self.playlist_manager.current_song = None
                            if ctx is not None:
                                await ctx.send(embed=Embed().end_playlist())
                            # Keep timer running as we are idle
                            break
                        else:
                            self.is_playing = True
                            if self.timer:
                                self.timer.cancel() # Cancel disconnect timer
                            await self.play(song=song)
                            break

                    except Exception as e:
                        retries += 1
                        _log.error(f"Error preparing song (attempt {retries}/{max_retries}): {e}")
                        if retries >= max_retries:
                            self.is_playing = False
                            if ctx:
                                await ctx.send(embed=Embed().error("❌ Unable to play songs from queue."))
                            self._start_disconnect_timer(ctx) # Start timer if failed to play
                            break
                        continue

    async def play(self, song: Song) -> None:
        ctx = song.context
        if not ctx.voice_client:
            _log.error("Cannot play song: No voice client available")
            self.after_play(self.bot, ctx)
            return

        self.playlist_manager.current_song_start_time = convert_to_second(get_time())
        self.playlist_manager.current_song_duration = convert_to_second(song.duration)

        if not isinstance(song.playback_url, str):
            _log.error(f"Cannot load playback URL for '{song.title}'.")
            self.after_play(self.bot, ctx)
            return

        try:
            source = discord.FFmpegPCMAudio(song.playback_url, **constants.FFMPEG_OPTIONS)
            embed = Embed(ctx).now_playing_song(song)
            await ctx.reply(embed=embed)

            self.playlist_manager.current_song = song
            try:
                ctx.voice_client.play(source, after=lambda x: self.after_play(self.bot, ctx))
            except Exception as e:
                _log.error(f"Error calling voice client play method: {e}")
                self.after_play(self.bot, ctx)
        except Exception as e:
            _log.error(f"Error playing song '{song.title}': {e}")
            self.after_play(self.bot, ctx)

    def after_play(self, bot, ctx) -> None:
        try:
            # Check if voice client is still playing (it shouldn't be if this callback is called)
            # But we might want to be double sure or handle manual stops
            if ctx.voice_client and not ctx.voice_client.is_playing():
                self.is_playing = False
                self.playlist_manager.prev_song = self.playlist_manager.current_song
                self.playlist_manager.current_song = None
                bot.loop.create_task(self.play_next(ctx))
            else:
                 # If we are here, something weird happened or it was a manual stop
                 pass
        except Exception as e:
            _log.error(f"Error in after_play callback: {e}")
            self.is_playing = False
            if ctx:
                self._start_disconnect_timer(ctx)

    def _start_disconnect_timer(self, ctx: Optional[discord.ext.commands.Context]):
        if ctx and not self.is_playing:
            if self.timer:
                self.timer.cancel()
            self.timer = Timer(callback=self.timeout_handle, ctx=ctx)

    async def timeout_handle(self, ctx: Optional[discord.ext.commands.Context]) -> None:
        if ctx is None:
            return

        try:
            if ctx.voice_client and ctx.voice_client.is_playing():
                # Should not happen if logic is correct, but safety net
                _log.info("Timer reset because voice client is playing.")
                self.timer = Timer(callback=self.timeout_handle, ctx=ctx)
            else:
                # Disconnect
                if ctx.voice_client:
                    await ctx.voice_client.disconnect(force=True)

                # Cleanup using bot.player_manager
                if self.bot.player_manager:
                     # Access the dictionary directly as we know we are removing ourselves
                    if self.guild_id in self.bot.player_manager.players:
                        del self.bot.player_manager.players[self.guild_id]

                await ctx.send(
                    embed=Embed().leave_channel_message(
                        minutes=constants.VOICE_TIMEOUT // 60
                    )
                )
                _log.info("Disconnected from voice channel due to timeout.")
        except Exception as e:
            _log.error(f"Error in timeout handler: {e}")

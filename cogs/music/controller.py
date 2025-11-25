from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional, Literal, Union

import discord
from discord.ext import commands

from cogs.music.manager import PlaylistManager
from cogs.music.core.song import Song, SongMeta, YouTubeSongMeta, SoundCloudSongMeta
from cogs.music.core.background_playlist_loader import (
    BackgroundPlaylistLoader, 
    PlaylistLoaderProtocol, 
    PlaylistLoadResult, 
    BackgroundLoadProgress
)
from cogs.music.view.view import MusicView
from cogs.components.discord_embed import Embed
from cogs.music.core.playlist import PlaylistObserver
from cogs.music.search import Search
from cogs.music.core.player.music_player import MusicPlayer

_log = logging.getLogger(__name__)

class GuildMusicManager(PlaylistLoaderProtocol):
    """
    Manages music playback and queue for a specific guild.
    Acts as a controller/coordinator.
    """
    def __init__(self, bot: commands.Bot, guild_id: int) -> None:
        self.bot = bot
        self.guild_id = guild_id
        self.playlist_manager = PlaylistManager(bot.settings_manager, guild_id)
        self.music_player = MusicPlayer(bot, guild_id, self.playlist_manager)

        self.ctx: Optional[commands.Context] = None
        
        # Initialize optimized playlist loader
        self.background_loader = BackgroundPlaylistLoader(callback=self, batch_size=8)
        self._loading_message: Optional[discord.Message] = None
        # Track priority setting for background loading
        self._current_priority: bool = False

        self.playlist_manager.playlist.attach(PlaylistObserver(self))

    def update(self, playlist):
        pass

    def destroy(self) -> None:
        self.music_player.destroy()
        self.playlist_manager.playlist.clear()

    def clear_queue(self) -> None:
        """Clear the entire queue and stop playback."""
        try:
            self.playlist_manager.playlist.clear()
            self.music_player.destroy() # This stops timer and effectively resets player
            # But we might need to be more explicit about stopping playback
            if self.ctx and self.ctx.voice_client and self.ctx.voice_client.is_playing():
                self.ctx.voice_client.stop()
            self.music_player.is_playing = False
            self.playlist_manager.current_song = None
            self.playlist_manager.prev_song = None
            _log.info("Queue cleared and playback stopped")
        except Exception as e:
            _log.error(f"Error clearing queue: {e}")

    async def graceful_shutdown(self, ctx: commands.Context) -> None:
        """Gracefully shutdown the audio system."""
        try:
            self.clear_queue()
            if ctx.voice_client:
                await ctx.voice_client.disconnect(force=True)
            _log.info("Audio system gracefully shutdown")
        except Exception as e:
            _log.error(f"Error during graceful shutdown: {e}")

    async def insert_priority_song(self, ctx: commands.Context, query: str) -> None:
        """Insert a priority song to play next."""
        try:
            self.ctx = ctx
            if not ctx.voice_client:
                await ctx.send(embed=Embed().error("Not connected to a voice channel."))
                return

            songs = await self._search_songs(query, priority=True)
            if songs:
                await self.playlist_manager.add_songs(songs, priority=True)
                if len(songs) == 1:
                    await ctx.send(embed=Embed().ok(f"🔥 **{songs[0].title}** will play next!"))
                else:
                    await ctx.send(embed=Embed().ok(f"🔥 Added {len(songs)} priority songs - will play next!"))

                # If not playing, start playing
                if not self.music_player.is_playing:
                    await self.music_player.play_next(ctx)
            else:
                await self._send_no_songs_found_message()
        except Exception as e:
            _log.error(f"Error inserting priority song: {e}")
            await ctx.send(embed=Embed().error("Failed to add priority song."))

    async def process_query(
        self, ctx: commands.Context, query: str, priority: bool = False
    ) -> None:
        self.ctx = ctx
        start_time = time.time()
        
        if not query or not query.strip():
            await self._send_no_songs_found_message()
            return
        
        if self.background_loader._is_playlist_url(query):
            result = await self.background_loader.load_playlist_optimized(query, ctx, priority)
            if result:
                self._log_song_addition(1, ctx.guild.id if ctx.guild else None, query, start_time)
                return
            _log.info("Optimized playlist loading failed, falling back to traditional method")
        
        songs = await self._search_songs(query, priority)
        if songs:
            valid_songs = [song for song in songs if song is not None]
            if valid_songs:
                await self.playlist_manager.add_songs(valid_songs, priority)
                latest_song = valid_songs[-1]
                await self._send_song_added_message(latest_song, priority)
                self._log_song_addition(len(valid_songs), ctx.guild.id if ctx.guild else None, query, start_time)

                # Trigger playback if idle
                if not self.music_player.is_playing:
                    await self.music_player.play_next(ctx)
            else:
                await self._send_no_songs_found_message()
        else:
            await self._send_no_songs_found_message()

    async def process_search(
        self,
        ctx: commands.Context,
        query: str,
        provider: Optional[Literal["youtube", "soundcloud"]] = None,
    ) -> None:
        self.ctx = ctx
        songs: Optional[List[SongMeta]] = await self._search_songs(
            query, provider=provider, limit=10
        )
        if songs:
            view = MusicView(songs, self.handle_track_selection_in_search)
            view.message = await ctx.send(embed=view.create_embed(), view=view)
        else:
            await self._send_no_songs_found_message()

    async def handle_track_selection_in_search(
        self, interaction: discord.Interaction, track: SongMeta
    ):
        await self.playlist_manager.add_songs([track], False)
        await self._send_song_added_message(track, False)
        if not self.music_player.is_playing:
            ctx = await self.bot.get_context(interaction)
            await self.music_player.play_next(ctx)

    async def handle_track_selection_in_playlist(
        self, interaction: discord.Interaction, track: SongMeta
    ):
        idx = self.playlist_manager.playlist.index(track)
        if idx is not None:
            await self.playlist_manager.playlist.remove_by_song(track)
            await self.playlist_manager.playlist.add_next(track)
            await interaction.followup.send(
                f"Moved {track.title} to next in the playlist.", ephemeral=True
            )

    async def _search_songs(
        self,
        query: str,
        priority: bool = False,
        provider: Optional[Literal["youtube", "soundcloud"]] = None,
        limit: int = 1,
    ) -> Optional[List[SongMeta]]:
        if self.ctx is None:
            return None
        try:
            return await Search().query(query, self.ctx, priority, provider, limit)
        except Exception as e:
            _log.error(f"Error searching songs: {e}")
            return None

    def _log_song_addition(
        self, song_count: int, guild_id: Optional[int], query: str, start_time: float
    ) -> None:
        end_time = time.time()
        guild_str = str(guild_id) if guild_id else "unknown"
        _log.info(
            f"Added {song_count} song(s) to guild '{guild_str}' playlist in {round(end_time-start_time,2)} seconds from query '{query}'."
        )

    async def _send_song_added_message(
        self, latest_song: SongMeta, priority: bool
    ) -> None:
        if self.ctx is None:
            return
        try:
            embed = self.playlist_manager.get_song_added_embed(
                self.ctx, latest_song, priority
            )
            if embed is not None:
                await self.ctx.send(embed=embed)
        except Exception as e:
            _log.error(f"Error sending song added message: {e}")

    async def _send_no_songs_found_message(self) -> None:
        if self.ctx is None:
            return
        try:
            await self.ctx.send(embed=Embed().error("No songs were found!"))
        except Exception as e:
            _log.error(f"Error sending no songs found message: {e}")

    # BackgroundPlaylistLoader callback implementations
    async def on_first_song_ready(self, result: PlaylistLoadResult) -> None:
        try:
            if not self.ctx or not result.first_song:
                return
            await self.playlist_manager.add_songs([result.first_song], priority=self._current_priority)
            await self._send_song_added_message(result.first_song, priority=self._current_priority)
            
            # Start playing if not already
            if not self.music_player.is_playing:
                await self.music_player.play_next(self.ctx)

            if result.total_expected > 1:
                remaining_count = result.total_expected - 1
                loading_embed = Embed().ok(
                    f"🎵 **{result.first_song.title}** is now playing!\n"
                    f"📥 Loading {remaining_count} more songs from **{result.playlist_name or 'playlist'}** in the background..."
                )
                self._loading_message = await self.ctx.send(embed=loading_embed)
        except Exception as e:
            _log.error(f"Error handling first song ready: {e}")

    async def on_batch_loaded(self, songs: List[SongMeta], progress: BackgroundLoadProgress) -> None:
        try:
            if not self.ctx:
                return
            await self.playlist_manager.add_songs(songs, priority=self._current_priority)
            if progress.current_batch % 3 == 0 and self._loading_message:
                try:
                    embed = Embed().ok(
                        f"📥 Loading playlist... ({progress.loaded_count}/{progress.total_count} songs loaded)"
                    )
                    await self._loading_message.edit(embed=embed)
                except discord.NotFound:
                    self._loading_message = None
                except Exception as e:
                    _log.debug(f"Could not update loading message: {e}")
        except Exception as e:
            _log.error(f"Error handling batch loaded: {e}")

    async def on_loading_complete(self, total_loaded: int, failed_count: int) -> None:
        try:
            if not self.ctx:
                return
            if self._loading_message:
                try:
                    success_count = total_loaded - failed_count
                    if failed_count > 0:
                        embed = Embed().ok(
                            f"✅ Playlist loaded! Added {success_count} songs to queue.\n"
                            f"⚠️ {failed_count} songs couldn't be loaded."
                        )
                    else:
                        embed = Embed().ok(
                            f"✅ Playlist fully loaded! Added {total_loaded} songs to queue."
                        )
                    await self._loading_message.edit(embed=embed)
                    await asyncio.sleep(10)
                    await self._loading_message.delete()
                except discord.NotFound:
                    pass
                except Exception as e:
                    _log.debug(f"Could not update completion message: {e}")
                finally:
                    self._loading_message = None
            self._current_priority = False
        except Exception as e:
            _log.error(f"Error handling loading complete: {e}")

    async def on_loading_error(self, error: Exception, can_retry: bool) -> None:
        try:
            if not self.ctx:
                return
            error_msg = f"❌ Error loading playlist: {str(error)[:100]}"
            if can_retry:
                error_msg += "\nYou can try again or use a different playlist."
            await self.ctx.send(embed=Embed().error(error_msg), delete_after=15)
            self._current_priority = False
        except Exception as e:
            _log.error(f"Error handling loading error: {e}")

    async def process_trending(self, ctx: commands.Context) -> None:
        try:
            await ctx.send(embed=Embed().ok("🎵 Đang tìm bài nhạc trending..."))
            from cogs.music.services.youtube_service import YouTubeService
            youtube_service = YouTubeService()
            trending_video = await youtube_service.get_trending_music_video(region="VN")
            
            if trending_video:
                from cogs.music.core.song import YouTubeSongMeta
                song_meta = YouTubeSongMeta(
                    title=trending_video.title,
                    author=trending_video.author,
                    duration=self._format_duration(trending_video.length),
                    video_id=trending_video.video_id,
                    webpage_url=trending_video.watch_url,
                    playlist_name="Trending Music",
                    ctx=ctx
                )
                from cogs.music.core.song import createSong
                song = await createSong(song_meta)
                if song:
                    self.playlist_manager.playlist.clear()
                    await self.playlist_manager.playlist.add(song_meta)
                    embed = Embed(ctx).ok(
                        f"🎵 **Đang phát bài nhạc trending:**\n"
                        f"**{trending_video.title}**\n"
                        f"👤 {trending_video.author}\n"
                        f"⏱️ {self._format_duration(trending_video.length)}"
                    )
                    await ctx.send(embed=embed)
                    if not self.music_player.is_playing:
                        await self.music_player.play_next(ctx)
                else:
                    await ctx.send(embed=Embed().error("❌ Không thể tạo bài hát từ video trending."))
            else:
                await ctx.send(embed=Embed().error("❌ Không tìm thấy bài nhạc trending nào."))
        except Exception as e:
            _log.error(f"Error in process_trending: {e}")
            await ctx.send(embed=Embed().error("❌ Có lỗi xảy ra khi tìm bài nhạc trending."))

    def _format_duration(self, seconds: int) -> str:
        if seconds <= 0:
            return "00:00"
        seconds = int(seconds)
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        return f"{minutes:02d}:{remaining_seconds:02d}"

    # Helper for playlist processing
    def _songs_equal(self, song1: SongMeta, song2: SongMeta) -> bool:
        try:
            if type(song1) != type(song2):
                return False
            if isinstance(song1, YouTubeSongMeta) and isinstance(song2, YouTubeSongMeta):
                return song1.video_id == song2.video_id
            elif isinstance(song1, SoundCloudSongMeta) and isinstance(song2, SoundCloudSongMeta):
                return song1.track_id == song2.track_id
            return song1.title == song2.title and song1.author == song2.author
        except Exception:
            return False

    async def _get_first_playlist_song(self, query: str, priority: bool) -> Optional[SongMeta]:
        try:
            search = Search()
            if "music.youtube.com" in query:
                query = query.replace("music.youtube.com", "www.youtube.com")
            if search.is_youtube(query) and ("/playlist?" in query or "&list=" in query):
                songs = await self._search_songs(query, priority, limit=1)
                return songs[0] if songs else None
            elif search.is_soundcloud(query) and "/sets/" in query:
                songs = await self._search_songs(query, priority, limit=1)
                return songs[0] if songs else None
            elif search.is_spotify(query) and ("/playlist/" in query or "/album/" in query):
                songs = await self._search_songs(query, priority, limit=1)
                return songs[0] if songs else None
            return None
        except Exception as e:
            _log.error(f"Error getting first playlist song: {e}")
            return None

    async def _process_remaining_playlist_songs(
        self, ctx: commands.Context, query: str, priority: bool, start_time: float, first_song: SongMeta
    ) -> None:
        try:
            if not ctx.guild:
                return
            all_songs = await self._search_songs(query, priority, limit=0)
            if not all_songs or len(all_songs) <= 1:
                return
            remaining_songs = []
            first_song_found = False
            for song in all_songs:
                if not first_song_found and self._songs_equal(song, first_song):
                    first_song_found = True
                    continue
                remaining_songs.append(song)
            if not first_song_found and all_songs:
                remaining_songs = all_songs[1:]
            if remaining_songs:
                batch_size = 10
                total_batches = (len(remaining_songs) + batch_size - 1) // batch_size
                for i in range(0, len(remaining_songs), batch_size):
                    if not ctx.guild or not ctx.voice_client:
                        break
                    batch = remaining_songs[i:i + batch_size]
                    if priority:
                        for song in reversed(batch):
                            await self.playlist_manager.playlist.add_next(song)
                    else:
                        await self.playlist_manager.add_songs(batch, priority)
                    current_batch = (i // batch_size) + 1
                    if total_batches > 1 and current_batch % 3 == 0:
                        try:
                            progress_msg = f"📥 Loaded {min(i + batch_size, len(remaining_songs))} of {len(remaining_songs)} remaining songs..."
                            await ctx.send(progress_msg, delete_after=10)
                        except Exception as e:
                            _log.warning(f"Could not send progress message: {e}")
                total_songs = len(all_songs)
                guild_id = getattr(ctx.guild, 'id', None) if ctx.guild else None
                self._log_song_addition(total_songs, guild_id, query, start_time)
                if len(remaining_songs) > 5:
                    try:
                        completion_msg = f"✅ Playlist fully loaded! Added {total_songs} songs to queue."
                        await ctx.send(completion_msg, delete_after=15)
                    except Exception as e:
                        _log.warning(f"Could not send completion message: {e}")
        except Exception as e:
            _log.error(f"Error processing remaining playlist songs: {e}")
            try:
                await ctx.send("⚠️ Some playlist songs couldn't be loaded.", delete_after=10)
            except Exception:
                pass

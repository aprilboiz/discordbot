from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional, Union, Literal

from cogs.music.manager import PlayerManager, PlaylistManager
from cogs.music.core.song import Song, SongMeta, YouTubeSongMeta, SoundCloudSongMeta
from cogs.music.core.background_playlist_loader import (
    BackgroundPlaylistLoader, 
    PlaylistLoaderProtocol, 
    PlaylistLoadResult, 
    BackgroundLoadProgress,
    LoadingState
)
from cogs.music.view.view import MusicView
import constants
import discord
from cogs.components.discord_embed import Embed
from cogs.music.core.playlist import PlaylistObserver
from cogs.music.search import Search
from discord.ext import commands
from utils import Timer, convert_to_second, get_time

_log = logging.getLogger(__name__)


class Audio(PlaylistLoaderProtocol):
    def __init__(self, *args, **kwargs) -> None:
        self.bot = None
        self.playlist_manager = PlaylistManager()
        self.is_playing = False

        self.ctx: Optional[commands.Context] = None
        self.timer = Timer(callback=self.timeout_handle, ctx=self.ctx)
        self.lock = asyncio.Lock()
        
        # Initialize optimized playlist loader
        self.background_loader = BackgroundPlaylistLoader(callback=self, batch_size=8)
        self._loading_message: Optional[discord.Message] = None
        # Track priority setting for background loading
        self._current_priority: bool = False

        for arg in args:
            if (
                isinstance(arg, (commands.Bot, commands.AutoShardedBot))
                and not self.bot
            ):
                self.bot = arg

        if "bot" in kwargs and not self.bot:
            self.bot = kwargs["bot"]

        if not self.bot:
            raise RuntimeError("Cannot initialize player, missing 'bot' param.")

        self.playlist_manager.playlist.attach(PlaylistObserver(self))

        if not self.playlist_manager.playlist._observers:
            raise Warning("Missing playlist observers.")

    def destroy(self) -> None:
        self.playlist_manager.playlist.clear()
        self.timer.cancel()

    async def play_next(self, ctx: Optional[commands.Context] = None) -> None:
        """
        Plays the next song in the playlist with enhanced error handling.
        Edge case: Handle song creation failures gracefully without stopping the queue.
        """
        async with self.lock:
            if not self.is_playing:
                max_retries = 5  # Try up to 5 songs before giving up
                retries = 0
                consecutive_soundcloud_failures = 0
                
                while retries < max_retries:
                    try:
                        song: Optional[Song] = (
                            await self.playlist_manager.playlist.get_next_prepared()
                        )
                        if song is None:
                            self.playlist_manager.current_song = None
                            if ctx is not None:
                                await ctx.send(embed=Embed().end_playlist())
                            break
                        else:
                            self.is_playing = True
                            await self.play(song=song)
                            break  # Successfully started playing
                            
                    except Exception as e:
                        retries += 1
                        error_msg = str(e)
                        
                        # Track SoundCloud-specific failures
                        if "soundcloud" in error_msg.lower() or "404" in error_msg:
                            consecutive_soundcloud_failures += 1
                            _log.warning(f"SoundCloud playback error (attempt {retries}/{max_retries}): {e}")
                            
                            # If we have too many consecutive SoundCloud failures, notify the user
                            if consecutive_soundcloud_failures >= 3 and ctx is not None:
                                await ctx.send(
                                    embed=Embed().error("⚠️ Multiple SoundCloud tracks are unavailable. This may be due to region restrictions or expired links."),
                                    delete_after=15
                                )
                        else:
                            consecutive_soundcloud_failures = 0  # Reset counter for non-SoundCloud errors
                            _log.error(f"Error preparing song (attempt {retries}/{max_retries}): {e}")
                        
                        if retries >= max_retries:
                            _log.error("Max retries reached, stopping playback")
                            self.is_playing = False
                            if ctx is not None:
                                if consecutive_soundcloud_failures >= 3:
                                    await ctx.send(embed=Embed().error("❌ Multiple SoundCloud tracks failed to load. Please try adding tracks from other sources or check if the tracks are available in your region."))
                                else:
                                    await ctx.send(embed=Embed().error("❌ Unable to play songs from queue. Please try again."))
                            break
                        
                        # Continue to next song
                        continue

    def after_play(self, bot, ctx) -> None:
        """
        Callback function to be called after a song finishes playing with enhanced error handling.
        """
        try:
            # Check if voice client is still playing
            voice_client_playing = False
            try:
                if ctx.voice_client:
                    voice_client_playing = ctx.voice_client.is_playing()
            except Exception:
                # If we can't check playing status, assume not playing
                voice_client_playing = False
            
            if ctx.voice_client and not voice_client_playing:
                self.is_playing = not self.is_playing
                self.playlist_manager.prev_song = self.playlist_manager.current_song
                self.playlist_manager.current_song = None
                bot.loop.create_task(self.play_next(ctx))
        except Exception as e:
            _log.error(f"Error in after_play callback: {e}")
            # Ensure we reset the playing state even if there's an error
            self.is_playing = False

    async def play(self, song: Song) -> None:
        """
        Plays the given song in the voice channel with comprehensive error handling.
        """
        ctx = song.context
        
        # Edge case: Validate voice client exists
        if not ctx.voice_client:
            _log.error("Cannot play song: No voice client available")
            self.after_play(self.bot, ctx)
            return
        
        self.playlist_manager.current_song_start_time = convert_to_second(get_time())
        self.playlist_manager.current_song_duration = convert_to_second(song.duration)

        self.timer.cancel()
        self.timer = Timer(self.timeout_handle, ctx=ctx)

        if not isinstance(song.playback_url, str):
            _log.error(
                f"Cannot load playback URL when trying to play this song '{song.title}'. Try to load next song."
            )
            self.after_play(self.bot, ctx)
            return

        try:
            source = discord.FFmpegPCMAudio(song.playback_url, **constants.FFMPEG_OPTIONS)  # type: ignore
            embed = Embed(ctx).now_playing_song(song)
            await ctx.reply(embed=embed)

            self.playlist_manager.current_song = song
            # Play the song using voice client
            try:
                ctx.voice_client.play(source, after=lambda x: self.after_play(self.bot, ctx))
            except Exception as e:
                _log.error(f"Error calling voice client play method: {e}")
                self.after_play(self.bot, ctx)
        except Exception as e:
            _log.error(f"Error playing song '{song.title}': {e}")
            self.after_play(self.bot, ctx)

    async def process_query(
        self, ctx: commands.Context, query: str, priority: bool = False
    ) -> None:
        # assign this context for timeout_handler can work.
        self.ctx = ctx
        # Track priority setting for background loading
        self._current_priority = priority

        start_time = time.time()
        
        # Edge case: Validate input parameters
        if not query or not query.strip():
            await self._send_no_songs_found_message()
            return
        
        # Check if this is a playlist URL that can be optimized
        if self.background_loader._is_playlist_url(query):
            result = await self.background_loader.load_playlist_optimized(query, ctx, priority)
            if result:
                # Successfully started optimized playlist loading
                self._log_song_addition(1, ctx.guild.id if ctx.guild else None, query, start_time)
                return
            # If optimized loading fails, fall back to traditional method
            _log.info("Optimized playlist loading failed, falling back to traditional method")
        
        # Traditional single song or non-optimizable playlist handling
        songs = await self._search_songs(query, priority)
        if songs:
            # Filter out None values before adding to playlist
            valid_songs = [song for song in songs if song is not None]
            if valid_songs:
                await self.playlist_manager.add_songs(valid_songs, priority)
                
                latest_song = valid_songs[-1]
                await self._send_song_added_message(latest_song, priority)
                
                self._log_song_addition(len(valid_songs), ctx.guild.id if ctx.guild else None, query, start_time)
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
        """
        Handles the selection of a track from a search result.

        This method adds the selected track to the playlist and sends a message
        indicating that the song has been added.

        Args:
            interaction (discord.Interaction): The interaction object that triggered this method.
            track (SongMeta): The metadata of the selected song.

        Returns:
            None
        """
        await self.playlist_manager.add_songs([track], False)
        await self._send_song_added_message(track, False)

    async def handle_track_selection_in_playlist(
        self, interaction: discord.Interaction, track: SongMeta
    ):
        """
        Handles the selection of a track within a playlist and moves it to the next position in the queue.

        Args:
            interaction (discord.Interaction): The interaction object that triggered this action.
            track (SongMeta): The track that has been selected to move within the playlist.

        Returns:
            None
        """
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
        # Edge case: Ensure context is available
        if self.ctx is None:
            _log.error("Cannot search songs: No context available")
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
        # Edge case: Handle missing guild_id
        guild_str = str(guild_id) if guild_id else "unknown"
        _log.info(
            f"Added {song_count} song(s) to guild '{guild_str}' playlist in {round(end_time-start_time,2)} seconds from query '{query}'."
        )

    async def _send_song_added_message(
        self, latest_song: SongMeta, priority: bool
    ) -> None:
        # Edge case: Ensure context is available before sending messages
        if self.ctx is None:
            _log.warning("Cannot send song added message: No context available")
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
        # Edge case: Ensure context is available before sending messages
        if self.ctx is None:
            _log.warning("Cannot send no songs found message: No context available")
            return
            
        try:
            await self.ctx.send(embed=Embed().error("No songs were found!"))
        except Exception as e:
            _log.error(f"Error sending no songs found message: {e}")

    async def timeout_handle(self, ctx: Union[commands.Context, None]) -> None:
        """
        Handles the timeout for the voice client with comprehensive error handling.
        """
        if ctx is None:
            return
             
        try:
            # Check if voice client is still playing
            voice_client_playing = False
            try:
                if ctx.voice_client:
                    voice_client_playing = ctx.voice_client.is_playing()
            except Exception:
                # If we can't check playing status, assume not playing
                voice_client_playing = False
            
            if ctx.voice_client and voice_client_playing:
                self.timer.cancel()
                self.timer = Timer(callback=self.timeout_handle, ctx=ctx)
                _log.info(
                    "Timer has been reset because discord.voice_client is still playing."
                )
            else:
                # Safely handle player cleanup
                playerManager = PlayerManager()
                guild_id = getattr(ctx.guild, 'id', None) if ctx.guild else None
                if guild_id and guild_id in playerManager.players:
                    del playerManager.players[guild_id]
                
                # Safely disconnect voice client
                try:
                    if ctx.voice_client:
                        await ctx.voice_client.disconnect(force=True)
                except Exception as e:
                    _log.warning(f"Error disconnecting voice client: {e}")
                
                await ctx.send(
                    embed=Embed().leave_channel_message(
                        minutes=constants.VOICE_TIMEOUT // 60
                    )
                )
                _log.info("Disconnected from voice channel due to timeout.")
        except Exception as e:
            _log.error(f"Error in timeout handler: {e}")

    async def _process_playlist_optimized(
        self, ctx: commands.Context, query: str, priority: bool, start_time: float
    ) -> None:
        """
        Optimized playlist processing that plays the first song immediately
        while loading remaining songs in the background.
        """
        try:
            # Step 1: Get the first song quickly for immediate playback
            first_song = await self._get_first_playlist_song(query, priority)
            
            if not first_song:
                await self._send_no_songs_found_message()
                return
            
            # Step 2: Add first song and start playback immediately
            await self.playlist_manager.add_songs([first_song], priority)
            await self._send_song_added_message(first_song, priority)
            
            # Step 3: Process remaining songs in background
            asyncio.create_task(
                self._process_remaining_playlist_songs(ctx, query, priority, start_time, first_song)
            )
            
        except Exception as e:
            _log.error(f"Error processing playlist: {e}")
            await self._send_no_songs_found_message()

    def _songs_equal(self, song1: SongMeta, song2: SongMeta) -> bool:
        """
        Helper method to compare if two songs are the same.
        Edge case: Handle different song metadata types safely.
        """
        try:
            # Compare by type and ID first
            if type(song1) != type(song2):
                return False
                
            if isinstance(song1, YouTubeSongMeta) and isinstance(song2, YouTubeSongMeta):
                return song1.video_id == song2.video_id
            elif isinstance(song1, SoundCloudSongMeta) and isinstance(song2, SoundCloudSongMeta):
                return song1.track_id == song2.track_id
            
            # Fallback to title comparison
            return song1.title == song2.title and song1.author == song2.author
        except Exception:
            return False

    async def _get_first_playlist_song(self, query: str, priority: bool) -> Optional[SongMeta]:
        """
        Quickly extract just the first song from a playlist for immediate playback.
        Edge case: Handle various playlist URL formats and empty results.
        """
        try:
            search = Search()
            
            # Convert YouTube Music URLs if needed
            if "music.youtube.com" in query:
                query = query.replace("music.youtube.com", "www.youtube.com")
            
            if search.is_youtube(query) and ("/playlist?" in query or "&list=" in query):
                # For YouTube playlists, get just the first video
                songs = await self._search_songs(query, priority, limit=1)
                return songs[0] if songs else None
                
            elif search.is_soundcloud(query) and "/sets/" in query:
                # For SoundCloud playlists, get first track
                songs = await self._search_songs(query, priority, limit=1)
                return songs[0] if songs else None
                
            elif search.is_spotify(query) and ("/playlist/" in query or "/album/" in query):
                # For Spotify playlists/albums, get first track
                songs = await self._search_songs(query, priority, limit=1)
                return songs[0] if songs else None
                
            return None
            
        except Exception as e:
            _log.error(f"Error getting first playlist song: {e}")
            return None

    async def _process_remaining_playlist_songs(
        self, ctx: commands.Context, query: str, priority: bool, start_time: float, first_song: SongMeta
    ) -> None:
        """
        Background task to process remaining playlist songs with comprehensive error handling.
        Edge case: Handle priority interruptions, disconnections, and large playlist processing.
        """
        try:
            # Edge case: Check if context/guild is still valid
            if not ctx.guild:
                _log.warning("Guild no longer available, cancelling background playlist processing")
                return
                
            # Get all songs from the playlist with unlimited limit
            all_songs = await self._search_songs(query, priority, limit=0)
            
            if not all_songs or len(all_songs) <= 1:
                return
            
            # Edge case: Handle case where first song is no longer in the list
            remaining_songs = []
            first_song_found = False
            
            for song in all_songs:
                if not first_song_found and self._songs_equal(song, first_song):
                    first_song_found = True
                    continue
                remaining_songs.append(song)
            
            # If first song wasn't found, remove the first item anyway
            if not first_song_found and all_songs:
                remaining_songs = all_songs[1:]
            
            if remaining_songs:
                # Add remaining songs in batches to provide better user feedback
                batch_size = 10  # Process 10 songs at a time
                total_batches = (len(remaining_songs) + batch_size - 1) // batch_size
                
                for i in range(0, len(remaining_songs), batch_size):
                    # Edge case: Check if processing should continue
                    if not ctx.guild or not ctx.voice_client:
                        _log.info("Stopping background playlist processing - bot disconnected")
                        break
                        
                    batch = remaining_songs[i:i + batch_size]
                    
                    # Edge case: Handle priority songs interrupting background processing
                    if priority:
                        # For priority processing, add to front of queue
                        for song in reversed(batch):
                            await self.playlist_manager.playlist.add_next(song)
                    else:
                        await self.playlist_manager.add_songs(batch, priority)
                    
                    # Provide progress feedback for large playlists
                    current_batch = (i // batch_size) + 1
                    if total_batches > 1 and current_batch % 3 == 0:  # Update every 3 batches
                        try:
                            progress_msg = f"📥 Loaded {min(i + batch_size, len(remaining_songs))} of {len(remaining_songs)} remaining songs..."
                            await ctx.send(progress_msg, delete_after=10)
                        except Exception as e:
                            _log.warning(f"Could not send progress message: {e}")
                
                # Log completion
                total_songs = len(all_songs)
                guild_id = getattr(ctx.guild, 'id', None) if ctx.guild else None
                self._log_song_addition(total_songs, guild_id, query, start_time)
                
                # Send final playlist loaded message
                if len(remaining_songs) > 5:  # Only for larger playlists
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
                pass  # Don't log if we can't send the error message

    async def insert_priority_song(self, ctx: commands.Context, query: str) -> None:
        """
        Insert a priority song that will play immediately after the current song.
        Edge case: Handle insertions during playlist loading and playback.
        """
        try:
            self.ctx = ctx
            
            # Edge case: Validate context and voice client
            if not ctx.voice_client:
                await ctx.send(embed=Embed().error("Not connected to a voice channel."))
                return
            
            songs = await self._search_songs(query, priority=True)
            if songs:
                # Add all songs as priority (they'll be inserted at the front)
                await self.playlist_manager.add_songs(songs, priority=True)
                
                if len(songs) == 1:
                    await ctx.send(
                        embed=Embed().ok(f"🔥 **{songs[0].title}** will play next!")
                    )
                else:
                    await ctx.send(
                        embed=Embed().ok(f"🔥 Added {len(songs)} priority songs - will play next!")
                    )
            else:
                await self._send_no_songs_found_message()
                
        except Exception as e:
            _log.error(f"Error inserting priority song: {e}")
            await ctx.send(embed=Embed().error("Failed to add priority song."))

    async def handle_playback_failure(self, failed_song: Song, ctx: commands.Context) -> None:
        """
        Handle playback failures with retry logic and graceful fallback.
        Edge case: Network issues, unavailable songs, codec problems.
        """
        try:
            _log.warning(f"Playback failed for song: {failed_song.title}")
            
            # Try to play the next song
            await self.play_next(ctx)
            
            # Notify user about the failure
            await ctx.send(
                embed=Embed().error(f"⚠️ Could not play **{failed_song.title}** - skipped to next song."),
                delete_after=10
            )
            
        except Exception as e:
            _log.error(f"Error handling playback failure: {e}")
            self.is_playing = False

    def clear_queue(self) -> None:
        """
        Clear the entire queue and cancel background tasks.
        Edge case: Handle cleanup during active processing.
        """
        try:
            self.playlist_manager.playlist.clear()
            self.is_playing = False
            self.playlist_manager.current_song = None
            self.playlist_manager.prev_song = None
            _log.info("Queue cleared and background tasks cancelled")
        except Exception as e:
            _log.error(f"Error clearing queue: {e}")

    async def graceful_shutdown(self, ctx: commands.Context) -> None:
        """
        Gracefully shutdown the audio system with proper cleanup.
        Edge case: Handle shutdown during playlist loading or playback.
        """
        try:
            # Clear queue and cancel background tasks
            self.clear_queue()
            
            # Cancel timer
            if hasattr(self, 'timer'):
                self.timer.cancel()
            
            # Disconnect from voice
            if ctx.voice_client:
                try:
                    ctx.voice_client.stop()
                except Exception as e:
                    _log.warning(f"Error stopping voice client: {e}")
                
                try:
                    await ctx.voice_client.disconnect(force=True)
                except Exception as e:
                    _log.warning(f"Error disconnecting voice client: {e}")
            
            _log.info("Audio system gracefully shutdown")
            
        except Exception as e:
            _log.error(f"Error during graceful shutdown: {e}")

    # BackgroundPlaylistLoader callback implementations
    async def on_first_song_ready(self, result: PlaylistLoadResult) -> None:
        """Handle when the first song is ready for immediate playback"""
        try:
            if not self.ctx or not result.first_song:
                return
            
            # Add the first song to the queue with the original priority setting
            await self.playlist_manager.add_songs([result.first_song], priority=self._current_priority)
            
            # Send first song added message
            await self._send_song_added_message(result.first_song, priority=self._current_priority)
            
            # If there are more songs expected, show loading message
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
        """Handle when a batch of songs is loaded"""
        try:
            if not self.ctx:
                return
            
            # Add the batch to the playlist with the original priority setting
            await self.playlist_manager.add_songs(songs, priority=self._current_priority)
            
            # Update loading message every few batches for large playlists
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
        """Handle when background loading is complete"""
        try:
            if not self.ctx:
                return
            
            # Update or send completion message
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
                    
                    # Auto-delete after 10 seconds
                    await asyncio.sleep(10)
                    await self._loading_message.delete()
                except discord.NotFound:
                    pass
                except Exception as e:
                    _log.debug(f"Could not update completion message: {e}")
                finally:
                    self._loading_message = None
            
            # Reset priority flag after loading is complete
            self._current_priority = False
            
        except Exception as e:
            _log.error(f"Error handling loading complete: {e}")

    async def on_loading_error(self, error: Exception, can_retry: bool) -> None:
        """Handle loading errors"""
        try:
            if not self.ctx:
                return
            
            error_msg = f"❌ Error loading playlist: {str(error)[:100]}"
            if can_retry:
                error_msg += "\nYou can try again or use a different playlist."
            
            await self.ctx.send(embed=Embed().error(error_msg), delete_after=15)
            
            # Reset priority flag after error
            self._current_priority = False
            
        except Exception as e:
            _log.error(f"Error handling loading error: {e}")

    async def process_trending(self, ctx: commands.Context) -> None:
        """Process and play a trending music video from YouTube"""
        try:
            # Send initial message
            await ctx.send(embed=Embed().ok("🎵 Đang tìm bài nhạc trending..."))
            
            # Get trending music video
            from cogs.music.services.youtube_service import YouTubeService
            youtube_service = YouTubeService()
            
            trending_video = await youtube_service.get_trending_music_video(region="VN")
            
            if trending_video:
                # Create YouTubeSongMeta from trending video
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
                
                # Use createSong function to create the song
                from cogs.music.core.song import createSong
                song = await createSong(song_meta)
                
                if song:
                    # Clear current queue and add trending song
                    self.playlist_manager.playlist.clear()
                    await self.playlist_manager.playlist.add(song_meta)
                    
                    # Send success message
                    embed = Embed(ctx).ok(
                        f"🎵 **Đang phát bài nhạc trending:**\n"
                        f"**{trending_video.title}**\n"
                        f"👤 {trending_video.author}\n"
                        f"⏱️ {self._format_duration(trending_video.length)}"
                    )
                    await ctx.send(embed=embed)
                    
                    # Start playing
                    if not self.is_playing:
                        await self.play_next(ctx)
                else:
                    await ctx.send(embed=Embed().error("❌ Không thể tạo bài hát từ video trending."))
            else:
                await ctx.send(embed=Embed().error("❌ Không tìm thấy bài nhạc trending nào."))
                
        except Exception as e:
            _log.error(f"Error in process_trending: {e}")
            await ctx.send(embed=Embed().error("❌ Có lỗi xảy ra khi tìm bài nhạc trending."))

    def _format_duration(self, seconds: int) -> str:
        """Format duration in seconds to MM:SS format"""
        if seconds <= 0:
            return "00:00"
        # Convert to int to handle float values
        seconds = int(seconds)
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        return f"{minutes:02d}:{remaining_seconds:02d}"

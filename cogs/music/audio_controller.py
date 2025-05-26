"""
Music Audio Controller
Simplified audio controller combining player management and audio control
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import weakref

from .core.models import Song, SongMeta
from .core.playlist import Playlist, PlaylistObserver
from .search import Search
from .view.view import MusicView
import constants

_log = logging.getLogger(__name__)


@dataclass
class AudioMetrics:
    """Audio performance metrics"""
    songs_played: int = 0
    search_requests: int = 0
    playback_errors: int = 0
    last_activity: float = field(default_factory=time.time)
    session_start: float = field(default_factory=time.time)


class AudioController:
    """
    Simplified audio controller with integrated player management
    """
    
    def __init__(self, bot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.playlist = Playlist()
        self.is_playing = False
        self.metrics = AudioMetrics()
        
        # Current song tracking
        self.current_song: Optional[Song] = None
        self.current_song_start_time: float = 0
        
        # Context and timer management
        self._ctx_ref: Optional[weakref.ref] = None
        self._timer_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        
        # Setup playlist observer
        observer = PlaylistObserver(self)
        self.playlist.attach(observer)
        
        _log.info(f"Created audio controller for guild {guild_id}")
    
    @property
    def ctx(self):
        """Get current context"""
        if self._ctx_ref:
            return self._ctx_ref()
        return None
    
    @ctx.setter
    def ctx(self, value):
        """Set current context"""
        self._ctx_ref = weakref.ref(value) if value else None
    
    async def search_and_add(self, ctx, query: str, priority: bool = False) -> bool:
        """Search for songs and add to playlist"""
        self.ctx = ctx
        
        try:
            search = Search()
            songs = await search.query(query, ctx, priority, limit=1)
            
            if songs:
                await self.playlist.add_next(songs[0]) if priority else await self.playlist.add(songs[0])
                self.metrics.search_requests += 1
                self.metrics.last_activity = time.time()
                
                # Send confirmation message
                await self._send_song_added_message(songs[0], priority)
                return True
            else:
                await ctx.send("❌ No songs found for your query.")
                return False
                
        except Exception as e:
            _log.error(f"Error in search_and_add: {e}")
            await ctx.send("❌ An error occurred while searching for songs.")
            return False
    
    async def search_interactive(self, ctx, query: str, provider: str = None) -> bool:
        """Interactive search with multiple results"""
        self.ctx = ctx
        
        try:
            search = Search()
            songs = await search.query(query, ctx, provider=provider, limit=10)
            
            if songs:
                view = MusicView(songs, self._handle_track_selection)
                view.message = await ctx.send(embed=view.create_embed(), view=view)
                self.metrics.search_requests += 1
                return True
            else:
                await ctx.send("❌ No songs found for your query.")
                return False
                
        except Exception as e:
            _log.error(f"Error in search_interactive: {e}")
            await ctx.send("❌ An error occurred while searching for songs.")
            return False
    
    async def _handle_track_selection(self, interaction, track: SongMeta):
        """Handle track selection from interactive search"""
        try:
            await self.playlist.add(track)
            await interaction.followup.send(f"✅ Added **{track.title}** to the playlist.", ephemeral=True)
        except Exception as e:
            _log.error(f"Error adding track: {e}")
            await interaction.followup.send("❌ Failed to add track to playlist.", ephemeral=True)
    
    async def play_next(self, ctx=None) -> None:
        """Play the next song in the playlist"""
        async with self._lock:
            if self.is_playing:
                return
            
            try:
                song = await self.playlist.get_next_prepared()
                
                if song is None:
                    self.current_song = None
                    self.is_playing = False
                    if ctx:
                        from cogs.components.discord_embed import Embed
                        await ctx.send(embed=Embed().end_playlist())
                    return
                
                await self._play_song(song)
                
            except Exception as e:
                _log.error(f"Error in play_next: {e}")
                self.metrics.playback_errors += 1
                
                # Try to recover
                if ctx:
                    await ctx.send("❌ Error playing song, trying next...")
                    await asyncio.sleep(1)
                    await self.play_next(ctx)
    
    async def _play_song(self, song: Song) -> None:
        """Play a specific song"""
        try:
            ctx = song.context
            if not ctx or not ctx.voice_client:
                _log.error("No voice client available")
                return
            
            # Ensure song is prepared
            if not song.is_prepared:
                await song.prepare()
            
            if not song.playback_url:
                _log.error(f"No playback URL for song: {song.title}")
                self.metrics.playback_errors += 1
                await self.play_next(ctx)
                return
            
            # Setup FFmpeg source
            import discord
            ffmpeg_options = {
                **constants.FFMPEG_OPTIONS,
                "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            }
            
            source = discord.FFmpegPCMAudio(song.playback_url, **ffmpeg_options)
            
            # Send now playing message
            from cogs.components.discord_embed import Embed
            embed = Embed(ctx).now_playing_song(song)
            await ctx.reply(embed=embed)
            
            # Start playing
            self.current_song = song
            self.current_song_start_time = time.time()
            self.is_playing = True
            
            ctx.voice_client.play(source, after=lambda x: self._after_play(ctx))
            
            # Start timeout timer
            self._start_timeout_timer(ctx)
            
            # Update metrics
            self.metrics.songs_played += 1
            self.metrics.last_activity = time.time()
            
            _log.info(f"Now playing: {song.title} in guild {self.guild_id}")
            
        except Exception as e:
            _log.error(f"Error playing song {song.title}: {e}")
            self.metrics.playback_errors += 1
            raise
    
    def _after_play(self, ctx) -> None:
        """Called after a song finishes playing"""
        try:
            if ctx.voice_client and not ctx.voice_client.is_playing():
                self.is_playing = False
                self.current_song = None
                
                # Schedule next song
                asyncio.create_task(self.play_next(ctx))
                
        except Exception as e:
            _log.error(f"Error in after_play: {e}")
            self.metrics.playback_errors += 1
    
    def _start_timeout_timer(self, ctx) -> None:
        """Start inactivity timeout timer"""
        if self._timer_task:
            self._timer_task.cancel()
        
        self._timer_task = asyncio.create_task(self._timeout_handler(ctx))
    
    async def _timeout_handler(self, ctx) -> None:
        """Handle inactivity timeout"""
        try:
            # Wait for timeout period
            await asyncio.sleep(constants.VOICE_TIMEOUT)
            
            # Check if still playing
            if ctx.voice_client and ctx.voice_client.is_playing():
                # Reset timer
                self._start_timeout_timer(ctx)
                return
            
            # Disconnect due to inactivity
            await self.cleanup()
            
            if ctx.voice_client:
                await ctx.voice_client.disconnect()
            
            from cogs.components.discord_embed import Embed
            await ctx.send(embed=Embed().leave_channel_message(minutes=constants.VOICE_TIMEOUT // 60))
            
            _log.info(f"Disconnected from guild {self.guild_id} due to inactivity")
            
        except asyncio.CancelledError:
            pass
        except Exception as e:
            _log.error(f"Error in timeout handler: {e}")
    
    async def skip_current(self, ctx) -> bool:
        """Skip the currently playing song"""
        try:
            if ctx.voice_client and ctx.voice_client.is_playing():
                ctx.voice_client.stop()
                return True
            return False
        except Exception as e:
            _log.error(f"Error skipping song: {e}")
            return False
    
    async def stop_playback(self, ctx) -> None:
        """Stop playback and clear playlist"""
        try:
            self.playlist.clear()
            self.is_playing = False
            self.current_song = None
            
            if ctx.voice_client:
                if ctx.voice_client.is_playing():
                    ctx.voice_client.stop()
                await ctx.voice_client.disconnect()
            
            await self.cleanup()
            
        except Exception as e:
            _log.error(f"Error stopping playback: {e}")
    
    async def move_to_channel(self, ctx, channel) -> bool:
        """Move bot to a different voice channel"""
        try:
            if not ctx.voice_client:
                return False
            
            was_playing = ctx.voice_client.is_playing()
            
            if was_playing:
                ctx.voice_client.pause()
            
            await ctx.voice_client.move_to(channel)
            await asyncio.sleep(1)  # Wait for connection to stabilize
            
            if was_playing:
                ctx.voice_client.resume()
            
            return True
            
        except Exception as e:
            _log.error(f"Error moving to channel: {e}")
            return False
    
    async def _send_song_added_message(self, song_meta: SongMeta, priority: bool) -> None:
        """Send song added confirmation message"""
        try:
            if not self.ctx:
                return
            
            from cogs.components.discord_embed import Embed
            
            # Calculate position and wait time
            position = self.playlist.index(song_meta)
            if position is not None:
                position += 1
                wait_time = self.playlist.time_wait(position - 1)
                
                embed = Embed(self.ctx).add_song(
                    song_meta,
                    position=position,
                    timewait=wait_time
                )
                
                await self.ctx.send(embed=embed)
                
        except Exception as e:
            _log.error(f"Error sending song added message: {e}")
    
    async def cleanup(self) -> None:
        """Clean up resources"""
        try:
            # Cancel timer
            if self._timer_task:
                self._timer_task.cancel()
                self._timer_task = None
            
            # Clear playlist
            self.playlist.clear()
            
            # Reset state
            self.is_playing = False
            self.current_song = None
            self._ctx_ref = None
            
            _log.info(f"Cleaned up audio controller for guild {self.guild_id}")
            
        except Exception as e:
            _log.error(f"Error during cleanup: {e}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get performance metrics"""
        session_duration = time.time() - self.metrics.session_start
        
        return {
            'guild_id': self.guild_id,
            'songs_played': self.metrics.songs_played,
            'search_requests': self.metrics.search_requests,
            'playback_errors': self.metrics.playback_errors,
            'is_playing': self.is_playing,
            'playlist_size': self.playlist.size(),
            'current_song': self.current_song.title if self.current_song else None,
            'session_duration_seconds': round(session_duration, 1),
            'last_activity': self.metrics.last_activity,
            'playlist_stats': self.playlist.get_stats()
        }


class AudioManager:
    """
    Manages audio controllers for multiple guilds
    """
    
    def __init__(self, bot):
        self.bot = bot
        self.controllers: Dict[int, AudioController] = {}
        self._max_concurrent = 50
        self._global_metrics = {
            'total_controllers_created': 0,
            'total_controllers_destroyed': 0,
            'peak_concurrent': 0,
            'connection_limit_hits': 0
        }
    
    def get_controller(self, guild_id: int) -> Optional[AudioController]:
        """Get audio controller for guild"""
        return self.controllers.get(guild_id)
    
    def create_controller(self, guild_id: int) -> Optional[AudioController]:
        """Create new audio controller for guild"""
        if len(self.controllers) >= self._max_concurrent:
            self._global_metrics['connection_limit_hits'] += 1
            _log.warning(f"Cannot create controller for guild {guild_id}: limit reached")
            return None
        
        if guild_id in self.controllers:
            return self.controllers[guild_id]
        
        controller = AudioController(self.bot, guild_id)
        self.controllers[guild_id] = controller
        
        self._global_metrics['total_controllers_created'] += 1
        current_count = len(self.controllers)
        if current_count > self._global_metrics['peak_concurrent']:
            self._global_metrics['peak_concurrent'] = current_count
        
        _log.info(f"Created audio controller for guild {guild_id} ({current_count}/{self._max_concurrent})")
        return controller
    
    async def destroy_controller(self, guild_id: int) -> None:
        """Destroy audio controller for guild"""
        if guild_id in self.controllers:
            controller = self.controllers[guild_id]
            await controller.cleanup()
            del self.controllers[guild_id]
            
            self._global_metrics['total_controllers_destroyed'] += 1
            _log.info(f"Destroyed audio controller for guild {guild_id}")
    
    async def cleanup_all(self) -> None:
        """Cleanup all controllers"""
        _log.info("Cleaning up all audio controllers...")
        
        guild_ids = list(self.controllers.keys())
        for guild_id in guild_ids:
            await self.destroy_controller(guild_id)
        
        _log.info("All audio controllers cleaned up")
    
    def get_global_metrics(self) -> Dict[str, Any]:
        """Get global metrics"""
        return {
            **self._global_metrics,
            'active_controllers': len(self.controllers),
            'max_concurrent': self._max_concurrent,
            'utilization_percent': round(len(self.controllers) / self._max_concurrent * 100, 2)
        }


# Global audio manager instance
audio_manager: Optional[AudioManager] = None


def init_audio_manager(bot) -> AudioManager:
    """Initialize global audio manager"""
    global audio_manager
    audio_manager = AudioManager(bot)
    return audio_manager


def get_audio_manager() -> Optional[AudioManager]:
    """Get global audio manager"""
    return audio_manager


def get_or_create_controller(guild_id: int) -> Optional[AudioController]:
    """Get or create audio controller for guild"""
    if not audio_manager:
        _log.error("Audio manager not initialized")
        return None
    
    controller = audio_manager.get_controller(guild_id)
    if not controller:
        controller = audio_manager.create_controller(guild_id)
    
    return controller 
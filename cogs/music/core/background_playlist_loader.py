import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Set, Union
from concurrent.futures import ThreadPoolExecutor

from cogs.music.core.song import SongMeta, YouTubeSongMeta, SoundCloudSongMeta, SpotifySongMeta
from cogs.music.services.youtube_service import YouTubeService
from cogs.music.services.soundcloud.service import SoundCloudService
from cogs.music.services.spotify.service import SpotifyService
from discord.ext import commands

_logger = logging.getLogger(__name__)


class LoadingState(Enum):
    """Enumeration of playlist loading states"""
    IDLE = "idle"
    LOADING_FIRST = "loading_first"
    LOADING_BACKGROUND = "loading_background"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class PlaylistLoadResult:
    """Result of playlist loading operation"""
    first_song: Optional[SongMeta]
    total_expected: int
    playlist_name: Optional[str]
    service_type: str
    playlist_id: Optional[str] = None


@dataclass
class BackgroundLoadProgress:
    """Progress tracking for background loading"""
    loaded_count: int
    total_count: int
    current_batch: int
    total_batches: int
    failed_count: int
    last_update: float


class PlaylistLoaderProtocol(Protocol):
    """Protocol for playlist loader callbacks"""
    async def on_first_song_ready(self, result: PlaylistLoadResult) -> None:
        """Called when the first song is ready for immediate playback"""
        ...
    
    async def on_batch_loaded(self, songs: List[SongMeta], progress: BackgroundLoadProgress) -> None:
        """Called when a batch of songs is loaded in the background"""
        ...
    
    async def on_loading_complete(self, total_loaded: int, failed_count: int) -> None:
        """Called when background loading is complete"""
        ...
    
    async def on_loading_error(self, error: Exception, can_retry: bool) -> None:
        """Called when an error occurs during loading"""
        ...


class BasePlaylistExtractor(ABC):
    """Base class for service-specific playlist extractors"""
    
    @abstractmethod
    async def extract_first_item(self, url: str, ctx: commands.Context) -> Optional[SongMeta]:
        """Extract just the first item for immediate playback"""
        pass
    
    @abstractmethod
    async def extract_flat_playlist(self, url: str, ctx: commands.Context) -> Dict[str, Any]:
        """Extract playlist metadata using flat extraction"""
        pass
    
    @abstractmethod
    async def extract_batch_items(self, flat_entries: List[Dict], ctx: commands.Context, 
                                start_idx: int, batch_size: int, playlist_name: Optional[str]) -> List[SongMeta]:
        """Extract a batch of songs from flat playlist entries"""
        pass


class YouTubePlaylistExtractor(BasePlaylistExtractor):
    """YouTube-specific optimized playlist extractor"""
    
    def __init__(self):
        self.youtube_service = YouTubeService()
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="yt-extract")
    
    async def extract_first_item(self, url: str, ctx: commands.Context) -> Optional[SongMeta]:
        """Extract first YouTube video for immediate playback using targeted extraction"""
        try:
            # Use yt-dlp with playlist:1 to get only the first item
            def _extract_first():
                import yt_dlp
                opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': False,
                    'playliststart': 1,
                    'playlistend': 1,
                    'format': 'bestaudio/best',
                }
                
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info and 'entries' in info and info['entries']:
                        first_entry = info['entries'][0]
                        if first_entry and first_entry.get('id'):
                            return first_entry
                    elif info and info.get('id'):  # Single video
                        return info
                    return None
            
            first_info = await asyncio.get_event_loop().run_in_executor(self._executor, _extract_first)
            
            if first_info:
                from cogs.music.services.youtube_service import YouTubeVideo
                video = YouTubeVideo(first_info)
                
                # Format duration as HH:MM:SS to match existing codebase expectations
                duration_seconds = video.length
                hours = duration_seconds // 3600
                minutes = (duration_seconds % 3600) // 60
                seconds = duration_seconds % 60
                duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                
                return YouTubeSongMeta(
                    title=video.title,
                    duration=duration_str,
                    video_id=video.video_id,
                    ctx=ctx,
                    playlist_name=first_info.get('playlist_title'),
                    webpage_url=video.watch_url,
                    author=video.author,
                )
            
        except Exception as e:
            _logger.error(f"Error extracting first YouTube item: {e}")
        
        return None
    
    async def extract_flat_playlist(self, url: str, ctx: commands.Context) -> Dict[str, Any]:
        """Extract YouTube playlist metadata using flat extraction for speed"""
        def _extract_flat():
            import yt_dlp
            opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'playlistend': 1000,  # Reasonable limit
            }
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        try:
            result = await asyncio.get_event_loop().run_in_executor(self._executor, _extract_flat)
            return result if result is not None else {}
        except Exception as e:
            _logger.error(f"Error extracting flat YouTube playlist: {e}")
            return {}
    
    async def extract_batch_items(self, flat_entries: List[Dict], ctx: commands.Context,
                                start_idx: int, batch_size: int, playlist_name: Optional[str]) -> List[SongMeta]:
        """Convert flat YouTube entries to SongMeta objects"""
        songs = []
        end_idx = min(start_idx + batch_size, len(flat_entries))
        
        for i in range(start_idx, end_idx):
            entry = flat_entries[i]
            if not entry or not entry.get('id'):
                continue
            
            try:
                # Format duration as HH:MM:SS to match existing codebase expectations
                duration_str = "00:00:00"
                if entry.get('duration'):
                    dur = int(entry['duration'])
                    hours = dur // 3600
                    minutes = (dur % 3600) // 60
                    seconds = dur % 60
                    duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                
                song = YouTubeSongMeta(
                    title=entry.get('title', 'Unknown Title'),
                    duration=duration_str,
                    video_id=entry['id'],
                    ctx=ctx,
                    playlist_name=playlist_name,
                    webpage_url=f"https://www.youtube.com/watch?v={entry['id']}",
                    author=entry.get('uploader', 'Unknown'),
                )
                songs.append(song)
                
            except Exception as e:
                _logger.warning(f"Error processing YouTube entry {entry.get('id', 'unknown')}: {e}")
        
        return songs


class SoundCloudPlaylistExtractor(BasePlaylistExtractor):
    """SoundCloud-specific playlist extractor"""
    
    def __init__(self):
        self.soundcloud_service = SoundCloudService()
    
    async def extract_first_item(self, url: str, ctx: commands.Context) -> Optional[SongMeta]:
        """Extract first SoundCloud track for immediate playback"""
        try:
            # Use existing SoundCloud service with limit=1
            data = await self.soundcloud_service.extract_song_from_url(url)
            if data and data.get('tracks'):
                first_track = data['tracks'][0]
                
                # Format duration as HH:MM:SS to match existing codebase expectations
                duration_ms = first_track.duration
                duration_seconds = duration_ms // 1000
                hours = duration_seconds // 3600
                minutes = (duration_seconds % 3600) // 60
                seconds = duration_seconds % 60
                duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                
                return SoundCloudSongMeta(
                    title=first_track.title,
                    duration=duration_str,
                    track_id=first_track.id,
                    ctx=ctx,
                    playlist_name=data.get('playlist_name'),
                    webpage_url=first_track.permalink_url,
                    author=first_track.user.username,
                )
        except Exception as e:
            _logger.error(f"Error extracting first SoundCloud item: {e}")
        
        return None
    
    async def extract_flat_playlist(self, url: str, ctx: commands.Context) -> Dict[str, Any]:
        """Extract SoundCloud playlist metadata"""
        try:
            result = await self.soundcloud_service.extract_song_from_url(url)
            return result if result is not None else {}
        except Exception as e:
            _logger.error(f"Error extracting SoundCloud playlist: {e}")
            return {}
    
    async def extract_batch_items(self, flat_entries: List[Any], ctx: commands.Context,
                                start_idx: int, batch_size: int, playlist_name: Optional[str]) -> List[SongMeta]:
        """Convert SoundCloud tracks to SongMeta objects"""
        songs = []
        end_idx = min(start_idx + batch_size, len(flat_entries))
        
        for i in range(start_idx, end_idx):
            track = flat_entries[i]
            try:
                # Format duration as HH:MM:SS to match existing codebase expectations
                duration_ms = track.duration
                duration_seconds = duration_ms // 1000
                hours = duration_seconds // 3600
                minutes = (duration_seconds % 3600) // 60
                seconds = duration_seconds % 60
                duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                
                song = SoundCloudSongMeta(
                    title=track.title,
                    duration=duration_str,
                    track_id=track.id,
                    ctx=ctx,
                    playlist_name=playlist_name,
                    webpage_url=track.permalink_url,
                    author=track.user.username,
                )
                songs.append(song)
            except Exception as e:
                _logger.warning(f"Error processing SoundCloud track {getattr(track, 'id', 'unknown')}: {e}")
        
        return songs


class BackgroundPlaylistLoader:
    """
    High-performance playlist loader that provides immediate first-track playback
    while asynchronously loading remaining tracks in the background.
    """
    
    def __init__(self, callback: PlaylistLoaderProtocol, batch_size: int = 10):
        self.callback = callback
        self.batch_size = batch_size
        self.state = LoadingState.IDLE
        
        # Track active loading tasks
        self._active_tasks: Set[asyncio.Task] = set()
        self._cancelled_playlists: Set[str] = set()
        
        # Service extractors
        self._extractors = {
            'youtube': YouTubePlaylistExtractor(),
            'soundcloud': SoundCloudPlaylistExtractor(),
        }
        
        # Performance metrics
        self._start_time: Optional[float] = None
        self._first_song_time: Optional[float] = None
    
    def _detect_service(self, url: str) -> Optional[str]:
        """Detect which streaming service the URL belongs to"""
        url_lower = url.lower()
        if 'youtube.com' in url_lower or 'youtu.be' in url_lower or 'music.youtube.com' in url_lower:
            return 'youtube'
        elif 'soundcloud.com' in url_lower:
            return 'soundcloud'
        elif 'spotify.com' in url_lower:
            return 'spotify'  # Future implementation
        return None
    
    def _is_playlist_url(self, url: str) -> bool:
        """Check if URL is a playlist/collection URL"""
        url_lower = url.lower()
        return any(pattern in url_lower for pattern in [
            '/playlist?', '&list=', '/sets/', '/album/', '/collection/'
        ])
    
    async def load_playlist_optimized(self, url: str, ctx: commands.Context, 
                                    priority: bool = False) -> Optional[PlaylistLoadResult]:
        """
        Load playlist with optimized first-track-first approach.
        
        Returns the first song immediately while scheduling background loading.
        """
        if not self._is_playlist_url(url):
            return None
        
        service = self._detect_service(url)
        if not service or service not in self._extractors:
            _logger.error(f"Unsupported service for URL: {url}")
            return None
        
        self._start_time = time.time()
        self.state = LoadingState.LOADING_FIRST
        
        try:
            # Step 1: Extract first song immediately
            extractor = self._extractors[service]
            first_song = await extractor.extract_first_item(url, ctx)
            
            if not first_song:
                self.state = LoadingState.FAILED
                await self.callback.on_loading_error(
                    Exception("Could not extract first song from playlist"), False
                )
                return None
            
            self._first_song_time = time.time()
            _logger.info(f"First song extracted in {self._first_song_time - self._start_time:.2f}s")
            
            # Step 2: Get playlist metadata for background loading
            flat_data = await extractor.extract_flat_playlist(url, ctx)
            
            playlist_name = None
            total_count = 0
            playlist_id = None
            
            if service == 'youtube':
                playlist_name = flat_data.get('title') or flat_data.get('playlist_title')
                entries = flat_data.get('entries', [])
                total_count = len([e for e in entries if e and e.get('id')])
                playlist_id = flat_data.get('id')
            elif service == 'soundcloud':
                playlist_name = flat_data.get('playlist_name')
                tracks = flat_data.get('tracks', [])
                total_count = len(tracks)
            
            result = PlaylistLoadResult(
                first_song=first_song,
                total_expected=total_count,
                playlist_name=playlist_name,
                service_type=service,
                playlist_id=playlist_id
            )
            
            # Step 3: Notify callback about first song
            await self.callback.on_first_song_ready(result)
            
            # Step 4: Schedule background loading if there are more songs
            if total_count > 1:
                task = asyncio.create_task(
                    self._load_remaining_songs_background(url, ctx, extractor, flat_data, service, priority)
                )
                self._active_tasks.add(task)
                task.add_done_callback(lambda t: self._active_tasks.discard(t))
            else:
                self.state = LoadingState.COMPLETED
                await self.callback.on_loading_complete(1, 0)
            
            return result
            
        except Exception as e:
            self.state = LoadingState.FAILED
            _logger.error(f"Error in optimized playlist loading: {e}")
            await self.callback.on_loading_error(e, True)
            return None
    
    async def _load_remaining_songs_background(self, url: str, ctx: commands.Context,
                                             extractor: BasePlaylistExtractor, flat_data: Dict[str, Any],
                                             service: str, priority: bool):
        """Background task to load remaining playlist songs"""
        try:
            self.state = LoadingState.LOADING_BACKGROUND
            
            # Prepare entries based on service
            if service == 'youtube':
                entries = flat_data.get('entries', [])
                playlist_name = flat_data.get('title') or flat_data.get('playlist_title')
                # Skip first entry since we already processed it
                remaining_entries = [e for e in entries[1:] if e and e.get('id')]
            elif service == 'soundcloud':
                tracks = flat_data.get('tracks', [])
                playlist_name = flat_data.get('playlist_name')
                # Skip first track
                remaining_entries = tracks[1:]
            else:
                return
            
            if not remaining_entries:
                self.state = LoadingState.COMPLETED
                await self.callback.on_loading_complete(1, 0)
                return
            
            total_batches = (len(remaining_entries) + self.batch_size - 1) // self.batch_size
            loaded_count = 1  # First song already loaded
            failed_count = 0
            
            for batch_idx in range(total_batches):
                # Check if loading was cancelled
                if self.state == LoadingState.CANCELLED:
                    break
                
                start_idx = batch_idx * self.batch_size
                
                try:
                    batch_songs = await extractor.extract_batch_items(
                        remaining_entries, ctx, start_idx, self.batch_size, playlist_name
                    )
                    
                    if batch_songs:
                        loaded_count += len(batch_songs)
                        
                        progress = BackgroundLoadProgress(
                            loaded_count=loaded_count,
                            total_count=len(remaining_entries) + 1,
                            current_batch=batch_idx + 1,
                            total_batches=total_batches,
                            failed_count=failed_count,
                            last_update=time.time()
                        )
                        
                        await self.callback.on_batch_loaded(batch_songs, progress)
                    
                    # Small delay to prevent overwhelming the system
                    if not priority and batch_idx < total_batches - 1:
                        await asyncio.sleep(0.5)
                
                except Exception as e:
                    failed_count += 1
                    _logger.error(f"Error loading batch {batch_idx + 1}: {e}")
                    
                    if failed_count > 3:  # Too many failures
                        await self.callback.on_loading_error(
                            Exception(f"Too many batch failures: {failed_count}"), False
                        )
                        break
            
            if self.state != LoadingState.CANCELLED:
                self.state = LoadingState.COMPLETED
                await self.callback.on_loading_complete(loaded_count, failed_count)
                
                # Log performance metrics
                total_time = time.time() - (self._start_time or 0)
                _logger.info(f"Background playlist loading completed in {total_time:.2f}s. "
                           f"Loaded: {loaded_count}, Failed: {failed_count}")
        
        except Exception as e:
            self.state = LoadingState.FAILED
            _logger.error(f"Error in background loading: {e}")
            await self.callback.on_loading_error(e, False)
    
    def cancel_loading(self, playlist_id: Optional[str] = None):
        """Cancel ongoing background loading"""
        self.state = LoadingState.CANCELLED
        
        if playlist_id:
            self._cancelled_playlists.add(playlist_id)
        
        # Cancel all active tasks
        for task in list(self._active_tasks):
            if not task.done():
                task.cancel()
        
        _logger.info("Background playlist loading cancelled")
    
    def get_loading_state(self) -> LoadingState:
        """Get current loading state"""
        return self.state
    
    def get_performance_metrics(self) -> Dict[str, float]:
        """Get performance metrics for the last loading operation"""
        metrics = {}
        
        if self._start_time:
            current_time = time.time()
            metrics['total_time'] = current_time - self._start_time
            
            if self._first_song_time:
                metrics['first_song_time'] = self._first_song_time - self._start_time
        
        return metrics 
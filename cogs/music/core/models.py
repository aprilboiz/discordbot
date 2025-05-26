"""
Music Core Models
Unified models for the music system including songs, albums, and metadata
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Union
from discord.ext import commands

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class Album:
    """Simple album representation"""
    title: str
    artist: Optional[str] = None
    release_date: Optional[str] = None
    thumbnail: Optional[str] = None


def format_duration(duration: Union[int, str], unit: str = "seconds") -> str:
    """Format duration from various units to MM:SS format"""
    try:
        if isinstance(duration, str):
            duration = int(duration)
        
        if unit == "milliseconds":
            duration = duration // 1000
        
        minutes, seconds = divmod(duration, 60)
        return f"{minutes}:{seconds:02d}"
    except (ValueError, TypeError):
        return "0:00"


@dataclass
class SongMeta:
    """Base song metadata class"""
    title: str
    duration: str
    ctx: commands.Context
    playlist_name: Optional[str] = None
    webpage_url: Optional[str] = None
    author: Optional[str] = None
    thumbnail: Optional[str] = None
    album: Optional[Album] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "title": self.title,
            "duration": self.duration,
            "playlist_name": self.playlist_name,
            "webpage_url": self.webpage_url,
            "author": self.author,
            "thumbnail": self.thumbnail,
            "album": self.album.title if self.album else None,
        }


@dataclass
class YouTubeSongMeta(SongMeta):
    """YouTube-specific song metadata"""
    video_id: str = ""
    
    def __post_init__(self):
        if not self.webpage_url and self.video_id:
            self.webpage_url = f"https://www.youtube.com/watch?v={self.video_id}"


@dataclass
class SoundCloudSongMeta(SongMeta):
    """SoundCloud-specific song metadata"""
    track_id: int = 0


@dataclass
class SpotifySongMeta(SongMeta):
    """Spotify-specific song metadata"""
    track_id: str = ""


class Song:
    """
    Enhanced Song class with async initialization and better resource management
    """

    def __init__(self, song_meta: SongMeta):
        self.title = song_meta.title
        self.duration = song_meta.duration
        self.context = song_meta.ctx
        self.playlist_name = song_meta.playlist_name
        self.webpage_url = song_meta.webpage_url
        self.author = song_meta.author
        self.thumbnail = song_meta.thumbnail
        self.album = song_meta.album
        
        # Internal state
        self._playback_url: Optional[str] = None
        self._is_prepared = False
        self._preparation_lock = asyncio.Lock()

    @property
    def playback_url(self) -> Optional[str]:
        """Get the playback URL"""
        return self._playback_url

    @property
    def is_prepared(self) -> bool:
        """Check if song is prepared for playback"""
        return self._is_prepared

    async def prepare(self) -> bool:
        """
        Prepare song for playback by fetching the actual stream URL
        """
        async with self._preparation_lock:
            if self._is_prepared:
                return True

            try:
                self._playback_url = await self._get_playback_url()
                self._is_prepared = True
                _log.debug(f"Prepared song: {self.title}")
                return True
            except Exception as e:
                _log.error(f"Failed to prepare song '{self.title}': {e}")
                return False

    async def _get_playback_url(self) -> str:
        """Get the actual playback URL based on song type"""
        if isinstance(self, YouTubeSong):
            return await self._get_youtube_url()
        elif isinstance(self, SoundCloudSong):
            return await self._get_soundcloud_url()
        elif isinstance(self, SpotifySong):
            return await self._get_spotify_url()
        else:
            raise ValueError(f"Unknown song type: {type(self)}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert song to dictionary"""
        return {
            "title": self.title,
            "duration": self.duration,
            "author": self.author,
            "webpage_url": self.webpage_url,
            "thumbnail": self.thumbnail,
            "playlist_name": self.playlist_name,
            "album": self.album.title if self.album else None,
            "is_prepared": self._is_prepared,
        }


class YouTubeSong(Song):
    """YouTube-specific song implementation"""
    
    def __init__(self, song_meta: YouTubeSongMeta):
        super().__init__(song_meta)
        self.video_id = song_meta.video_id

    async def _get_youtube_url(self) -> str:
        """Get YouTube stream URL"""
        from pytubefix import YouTube
        
        try:
            yt = YouTube(self.webpage_url, client="WEB")
            # Get the best audio stream
            audio_stream = yt.streams.filter(only_audio=True).first()
            if audio_stream:
                return audio_stream.url
            else:
                raise ValueError("No audio stream available")
        except Exception as e:
            _log.error(f"Failed to get YouTube URL for {self.title}: {e}")
            raise


class SoundCloudSong(Song):
    """SoundCloud-specific song implementation"""
    
    def __init__(self, song_meta: SoundCloudSongMeta):
        super().__init__(song_meta)
        self.track_id = song_meta.track_id

    async def _get_soundcloud_url(self) -> str:
        """Get SoundCloud stream URL"""
        from cogs.music.services.soundcloud.service import SoundCloudService
        from soundcloud import Track, BasicTrack
        
        try:
            sc_service = SoundCloudService()
            # Get track info
            track_data = await sc_service.resolve_url(self.webpage_url)
            
            if isinstance(track_data, (Track, BasicTrack)):
                return await sc_service.get_playback_url(track_data)
            else:
                raise ValueError("Invalid track data")
        except Exception as e:
            _log.error(f"Failed to get SoundCloud URL for {self.title}: {e}")
            raise


class SpotifySong(Song):
    """Spotify-specific song implementation"""
    
    def __init__(self, song_meta: SpotifySongMeta):
        super().__init__(song_meta)
        self.track_id = song_meta.track_id

    async def _get_spotify_url(self) -> str:
        """Get Spotify stream URL (via YouTube search)"""
        from cogs.music.services.spotify.service import SpotifyService
        from pytubefix import Search
        
        try:
            # Search for the song on YouTube
            search_query = f"{self.title} {self.author}"
            search_results = Search(search_query, client="WEB").videos
            
            if search_results:
                yt_video = search_results[0]
                audio_stream = yt_video.streams.filter(only_audio=True).first()
                if audio_stream:
                    return audio_stream.url
            
            raise ValueError("No matching YouTube video found")
        except Exception as e:
            _log.error(f"Failed to get Spotify URL for {self.title}: {e}")
            raise


def create_song_from_meta(song_meta: SongMeta) -> Song:
    """Factory function to create appropriate Song instance from metadata"""
    if isinstance(song_meta, YouTubeSongMeta):
        return YouTubeSong(song_meta)
    elif isinstance(song_meta, SoundCloudSongMeta):
        return SoundCloudSong(song_meta)
    elif isinstance(song_meta, SpotifySongMeta):
        return SpotifySong(song_meta)
    else:
        # Default to base Song class
        return Song(song_meta) 
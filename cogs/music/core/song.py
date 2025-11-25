import logging
from dataclasses import dataclass
from functools import singledispatch
from typing import Any, Dict, List, Optional, Union

from cogs.music.core.album import Album
from cogs.music.services.soundcloud.service import SoundCloudService
from cogs.music.services.spotify import track
from cogs.music.services.spotify.service import SpotifyService
from cogs.music.services.youtube_service import YouTubeService, VideoUnavailable
from discord.ext import commands
from soundcloud import BasicTrack, Track
from utils import format_duration, format_playback_count, safe_format_date, safe_getattr

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Song:
    """
    Represents a song with various attributes and methods to access its information.

    Parameters:
    - title (str): The title of the song.
    - playback_url (str): The URL to play the song.
    - uploader (str): The name of the uploader.
    - playback_count (str): The number of times the song has been played.
    - duration (str): The duration of the song.
    - upload_date (str): The date when the song was uploaded.
    - thumbnail (str): The URL to the thumbnail image of the song.
    - webpage_url (str): The URL to the webpage of the song.
    - category (str): The category of the song.
    - album (Album): Song's album
    - context (commands.Context | None): The context in which the song is being used (if applicable).

    Methods:
    - info(): Returns a dictionary containing the song's information.
    """

    title: str
    playback_url: Optional[str]
    uploader: str
    playback_count: str
    duration: str
    upload_date: str
    thumbnail: str
    webpage_url: str
    album: Optional["Album"]
    context: commands.Context

    def info(self) -> Dict[str, Any]:
        """
        Return a dictionary containing the song's information.
        """
        song: Dict[str, Any] = {
            "title": self.title,
            "playback_url": self.playback_url,
            "uploader": self.uploader,
            "playback_count": self.playback_count,
            "duration": self.duration,
            "upload_date": self.upload_date,
            "thumbnail": self.thumbnail,
            "webpage_url": self.webpage_url,
            "album": self.album,
        }
        return song


@dataclass(slots=True, kw_only=True)
class SongMeta:
    """
    Represents a song metadata, contains data used for extract a song's information.
    Mainly used for song queue, before the song was loaded.
    """

    title: Optional[str]
    duration: str
    playlist_name: Optional[str]
    webpage_url: Optional[str]
    author: Optional[str]
    ctx: commands.Context

    # Lazy Loading Flag
    # If False, the song is not yet fully resolved (e.g. playback_url is missing)
    # createSong will resolve it just-in-time.
    resolved: bool = False

    def update_meta(self, *args, **kwargs) -> None:
        """This method updates the metadata of the song with the given video information.
        The following attributes will be updated:
        - title
        - duration
        - webpage_url
        - author
        """
        raise NotImplementedError("This method must be implemented in a subclass.")


@dataclass(slots=True, kw_only=True)
class YouTubeSongMeta(SongMeta):
    """
    Represents a song metadata for YouTube, contains data used for extract a song's information.
    Mainly used for song queue, before the song was loaded.
    """

    video_id: str

    def update_meta(self, video) -> None:
        self.title = video.title
        self.duration = format_duration(video.length)
        self.webpage_url = video.watch_url
        self.author = video.author


@dataclass(slots=True, kw_only=True)
class SoundCloudSongMeta(SongMeta):
    """
    Represents a song metadata for SoundCloud, contains data used for extract a song's information.
    Mainly used for song queue, before the song was loaded.
    """

    track_id: int

    def update_meta(self, track: Union[Track, BasicTrack]) -> None:
        self.title = track.title
        self.duration = format_duration(track.duration, unit="milliseconds")
        self.webpage_url = track.permalink_url
        self.author = track.user.username


@dataclass(slots=True, kw_only=True)
class SpotifySongMeta(SongMeta):
    """
    Represents a song metadata for Spotify, contains data used for extract a song's information.
    Mainly used for song queue, before the song was loaded.
    """

    track_id: str

    def update_meta(self, track: track.Track) -> None:
        self.title = track.name
        self.duration = format_duration(track.duration_ms, unit="milliseconds")
        self.webpage_url = track.external_urls.spotify
        self.author = track.artists[0].name


@singledispatch
async def createSong(song_meta: SongMeta) -> Union[Song, None]:
    raise NotImplementedError(f"Cannot create song from {type(song_meta)}")


@createSong.register  # type: ignore
async def _(song_meta: YouTubeSongMeta) -> Union[Song, None]:
    url = f"https://www.youtube.com/watch?v={song_meta.video_id}"
    youtube_service = YouTubeService()
    try:
        video = await youtube_service.get_video_info(url)
        video.check_availability()
    except VideoUnavailable:
        _logger.error(f"This YouTube video is unavailable. ID: {song_meta.video_id}. Title: {song_meta.title}")
        return None
    
    playback_url = video.get_audio_url()

    return Song(
        title=video.title,
        playback_url=playback_url,
        uploader=video.author,
        playback_count=format_playback_count(video.views),
        duration=song_meta.duration,
        upload_date=video.publish_date or "Unknown",
        thumbnail=video.thumbnail_url,
        webpage_url=video.watch_url,
        album=Album(song_meta.playlist_name) if song_meta.playlist_name else None,
        context=song_meta.ctx,
    )


@createSong.register  # type: ignore
async def _(song_meta: SoundCloudSongMeta) -> Union[Song, None]:
    """
    Create a SoundCloud song with enhanced error handling and retry logic.
    
    This method includes:
    - Proper error handling for unavailable tracks
    - Graceful handling of playback URL failures
    - Detailed logging for debugging
    """
    sc_service = SoundCloudService()

    try:
        # Get track information first
        track = sc_service.sc.get_track(song_meta.track_id)
        if track is None:
            _logger.error(f"SoundCloud track not found. ID: {song_meta.track_id}. Title: {song_meta.title}")
            return None
        
        # Validate track has required attributes
        if not hasattr(track, 'title') or not track.title:
            _logger.error(f"SoundCloud track missing title. ID: {song_meta.track_id}")
            return None
            
        if not hasattr(track, 'user') or not track.user:
            _logger.error(f"SoundCloud track missing user info. ID: {song_meta.track_id}")
            return None

        # Try to get playback URL with our enhanced service
        playback_url = None
        try:
            playback_url = await sc_service.get_playback_url(track)
        except Exception as e:
            _logger.error(f"Failed to get playback URL for SoundCloud track '{track.title}' (ID: {song_meta.track_id}): {e}")
        
        # If playback URL is None or empty, log and return None
        if not playback_url:
            _logger.error(f"No valid playback URL found for SoundCloud track '{track.title}' (ID: {song_meta.track_id}). Track may be unavailable, region-restricted, or require premium access.")
            return None
        
        # Get thumbnail safely
        thumbnail = ""
        try:
            thumbnail = sc_service.get_thumbnail(track)
        except Exception as e:
            _logger.warning(f"Failed to get thumbnail for SoundCloud track '{track.title}': {e}")
            thumbnail = ""  # Use empty string as fallback

        # Create and return the song object
        return Song(
            title=track.title,
            playback_url=playback_url,
            uploader=track.user.username,
            duration=song_meta.duration,
            playback_count=format_playback_count(safe_getattr(track, "playback_count", 0)),
            upload_date=safe_format_date(track.created_at),
            thumbnail=thumbnail,
            webpage_url=track.permalink_url,
            album=Album(song_meta.playlist_name) if song_meta.playlist_name else None,
            context=song_meta.ctx,
        )
        
    except Exception as e:
        _logger.error(f"Unexpected error creating SoundCloud song. ID: {song_meta.track_id}. Title: {song_meta.title}. Error: {e}")
        return None


@createSong.register  # type: ignore
async def _(song_meta: SpotifySongMeta) -> Union[Song, None]:
    sp_service = SpotifyService()
    song = sp_service.get_track(song_meta.track_id)

    query = f"'{','.join(artist.name for artist in song.artists)}' '{song.name}' Topic YouTube Music"
    video = None  # type: ignore
    youtube_service = YouTubeService()
    videos = await youtube_service.search_videos(query, limit=5)
    _logger.info(f'Creating Spotify song: Searching for "{query}"')

    for vid in videos:
        print(song.name, vid.title, song.duration_ms, vid.length * 1000)
        if abs(song.duration_ms - vid.length * 1000) < (60 * 1000) or song.name in vid.title:
            video = vid
            break

    # If no video match criteria, use the first video
    if video is None and videos:
        video = videos[0]
    
    if video is None:
        _logger.error(f"No YouTube video found for Spotify track: {song.name}")
        return None

    # Get full video info if needed for audio URL
    video = await video.get_full_info(youtube_service)
    playback_url = video.get_audio_url()
    
    if not playback_url:
        _logger.error(f"Could not get audio URL for video: {video.title}")
        return None
    
    _logger.info(
        f'Creating Spotify song: Actual playback of "{song.name}" is from "[{video.title}]({video.watch_url})"'
    )

    return Song(
        title=song.name,
        playback_url=playback_url,
        uploader=", ".join(artist.name for artist in song.artists),
        playback_count="Unknown",
        duration=song_meta.duration,
        upload_date=song.album.release_date,
        thumbnail=song.album.images[0].url,
        webpage_url=song.external_urls.spotify,
        album=Album(song.album.name) if song.album.name else None,
        context=song_meta.ctx,
    )


async def get_songs_info(songs_need_to_update: List[SongMeta]) -> List[SongMeta]:
    sc_songs: List[SoundCloudSongMeta] = []
    yt_songs: List[YouTubeSongMeta] = []
    sp_songs: List[SpotifySongMeta] = []

    # Separate songs by their type
    for song in songs_need_to_update:
        if isinstance(song, SoundCloudSongMeta):
            sc_songs.append(song)
        elif isinstance(song, YouTubeSongMeta):
            yt_songs.append(song)
        elif isinstance(song, SpotifySongMeta):
            sp_songs.append(song)

    # Get YouTube info
    # Optimize: Don't resolve stream URL here. Use simple video info if possible.
    # However, get_video_info in YouTubeService resolves it.
    # We should use batch_get_video_info if possible or ensure we don't block.
    # But for lazy loading, we assume metadata is mostly there.
    # If title is missing, we fetch it.

    youtube_service = YouTubeService()
    for song in yt_songs:
        # We construct a dummy object if we just want to update meta without resolving URL
        # But update_meta expects a YouTubeVideo object.
        # If we really need to update meta (title is None), we have to fetch info.
        # We accept this cost for individual songs added without title.
        video = await youtube_service.get_video_info(f"https://www.youtube.com/watch?v={song.video_id}")
        song.update_meta(video)

    # Get SoundCloud info
    sc_service = SoundCloudService()
    tracks = await sc_service.get_tracks_info([song.track_id for song in sc_songs])
    for track in tracks:
        song = next((s for s in sc_songs if s.track_id == track.id), None)
        if song:
            song.update_meta(track)

    # Get Spotify info
    sp_service = SpotifyService()
    for song in sp_songs:
        track = sp_service.get_track(song.track_id)
        song.update_meta(track)

    # Merge all songs back to the original list
    for song in songs_need_to_update:
        if isinstance(song, SoundCloudSongMeta):
            song = next((s for s in sc_songs if s.track_id == song.track_id), song)
        elif isinstance(song, YouTubeSongMeta):
            song = next((s for s in yt_songs if s.video_id == song.video_id), song)
        elif isinstance(song, SpotifySongMeta):
            song = next((s for s in sp_songs if s.track_id == song.track_id), song)

    return songs_need_to_update

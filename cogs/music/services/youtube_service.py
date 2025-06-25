import asyncio
import logging
import os
from typing import Dict, List, Optional, Union, Any
import yt_dlp
from utils import to_thread

_log = logging.getLogger(__name__)


class YouTubeDLError(Exception):
    """Custom exception for YouTube DL related errors"""
    pass


class VideoUnavailable(YouTubeDLError):
    """Exception raised when a video is unavailable"""
    pass


class YouTubeVideo:
    """Wrapper class to mimic pytubefix YouTube video object structure"""
    
    def __init__(self, info_dict: Dict):
        self._info = info_dict
        
    @property
    def title(self) -> str:
        return self._info.get('title', '')
    
    @property
    def video_id(self) -> str:
        return self._info.get('id', '')
    
    @property
    def author(self) -> str:
        return self._info.get('uploader', '')
    
    @property
    def length(self) -> int:
        """Duration in seconds"""
        return self._info.get('duration', 0) or 0
    
    @property
    def views(self) -> int:
        return self._info.get('view_count', 0) or 0
    
    @property
    def publish_date(self) -> Optional[str]:
        upload_date = self._info.get('upload_date')
        if upload_date:
            # yt-dlp returns date as YYYYMMDD string, convert to DD/MM/YYYY format
            try:
                from datetime import datetime
                date_obj = datetime.strptime(upload_date, '%Y%m%d')
                return date_obj.strftime('%d/%m/%Y')
            except (ValueError, TypeError):
                return None
        return None
    
    @property
    def thumbnail_url(self) -> str:
        return self._info.get('thumbnail', '')
    
    @property
    def watch_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"
    
    def check_availability(self) -> None:
        """Check if video is available, raise VideoUnavailable if not"""
        if self._info.get('availability') in ['private', 'premium_only', 'subscriber_only', 'needs_auth']:
            raise VideoUnavailable(f"Video {self.video_id} is not available")
    
    def get_audio_url(self) -> Optional[str]:
        """Get the best audio-only stream URL"""
        formats = self._info.get('formats', [])
        
        # Look for audio-only formats first (prioritize m4a for better compatibility)
        audio_formats = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') != 'none']
        if audio_formats:
            # Sort by quality and prefer m4a format
            audio_formats.sort(key=lambda x: (
                x.get('ext') == 'm4a',  # Prefer m4a
                x.get('abr', 0) or 0,   # Then by audio bitrate
                x.get('quality', 0) or 0  # Then by general quality
            ), reverse=True)
            return audio_formats[0]['url']
        
        # Fallback to any format with audio
        formats_with_audio = [f for f in formats if f.get('acodec') != 'none']
        if formats_with_audio:
            formats_with_audio.sort(key=lambda x: (
                x.get('ext') == 'm4a',
                x.get('quality', 0) or 0
            ), reverse=True)
            return formats_with_audio[0]['url']
        
        # If we don't have formats info, we need to get full video info first
        if not formats:
            return None
        
        return None
    
    async def get_full_info(self, youtube_service: 'YouTubeService') -> 'YouTubeVideo':
        """Get full video information if this is just a search result"""
        if not self._info.get('formats'):
            # This is just a search result, get full info
            return await youtube_service.get_video_info(self.watch_url)
        return self


class YouTubePlaylist:
    """Wrapper class to mimic pytubefix Playlist object structure"""
    
    def __init__(self, info_dict: Dict):
        self._info = info_dict
        
    @property
    def title(self) -> str:
        return self._info.get('title', '')
    
    @property
    def videos(self) -> List[YouTubeVideo]:
        """Return list of videos in playlist"""
        entries = self._info.get('entries', [])
        return [YouTubeVideo(entry) for entry in entries if entry]


class YouTubeService:
    """Service class for YouTube operations using yt-dlp"""
    
    def __init__(self):
        # Ensure cache directory exists
        self._ensure_cache_directory()
        
        # Base configuration optimized for music bot usage
        base_opts = {
            # Performance optimizations
            'quiet': True,
            'no_warnings': True,
            'no_color': True,
            'extract_flat': False,
            'skip_download': True,
            
            # Network optimizations
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 5,
            'retry_sleep_functions': {
                'http': lambda n: min(2 ** n, 30),  # Cap at 30 seconds
                'fragment': lambda n: min(2 ** n, 10),  # Cap at 10 seconds
            },
            
            # Audio-specific optimizations for music bot
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best[height<=480]',
            'prefer_free_formats': True,
            'noplaylist': True,  # Don't extract playlists unless explicitly requested
            
            # Metadata optimizations - only get what we need
            'writethumbnail': False,
            'writeinfojson': False,
            'writesubtitles': False,
            'writeautomaticsub': False,
            'writedescription': False,
            'writeannotations': False,
            
            # Geographic and age restrictions
            'geo_bypass': True,
            'age_limit': 0,  # No age restrictions for music content
            
            # Error handling
            'ignoreerrors': False,  # We want to handle errors explicitly
            'no_check_certificate': False,  # Keep security checks
            
            # Cache and performance
            'cachedir': './temp_folder/yt_cache',  # Use project's temp folder
            'rm_cachedir': False,  # Keep cache for better performance
            
            # User agent rotation for better reliability
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            
            # Extractor specific options
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],  # Use multiple clients for reliability
                    'player_skip': ['webpage'],  # Skip webpage parsing for speed
                }
            },
        }
        
        # Options for video info extraction (full metadata)
        self.ydl_opts = {
            **base_opts,
            'extract_flat': False,
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
        }
        
        # Options for search (lightweight, flat extraction)
        self.search_opts = {
            **base_opts,
            'extract_flat': True,
            'quiet': True,
            'no_warnings': True,
            'playlistend': 50,  # Limit search results
            'format': None,  # Don't extract format info for search
        }
        
        # Options for playlist extraction
        self.playlist_opts = {
            **base_opts,
            'extract_flat': False,
            'noplaylist': False,  # Allow playlist extraction
            'playlistend': 100,  # Reasonable limit for playlists
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
        }
        
        # Options for quick metadata-only extraction
        self.metadata_opts = {
            **base_opts,
            'extract_flat': True,
            'skip_download': True,
            'format': None,
            'quiet': True,
        }
    
    def _ensure_cache_directory(self) -> None:
        """Ensure the cache directory exists"""
        cache_dir = './temp_folder/yt_cache'
        try:
            os.makedirs(cache_dir, exist_ok=True)
            _log.debug(f"Cache directory ensured: {cache_dir}")
        except OSError as e:
            _log.warning(f"Could not create cache directory {cache_dir}: {e}")
    
    def clear_cache(self) -> None:
        """Clear the yt-dlp cache directory"""
        import shutil
        cache_dir = './temp_folder/yt_cache'
        try:
            if os.path.exists(cache_dir):
                shutil.rmtree(cache_dir)
                os.makedirs(cache_dir, exist_ok=True)
                _log.info("YouTube cache cleared successfully")
        except OSError as e:
            _log.error(f"Failed to clear cache: {e}")
    
    async def get_video_info(self, url: str) -> YouTubeVideo:
        """Get video information from URL"""
        def _get_info():
            try:
                with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if not info:
                        raise VideoUnavailable(f"Could not extract info for {url}")
                    return YouTubeVideo(info)
            except yt_dlp.DownloadError as e:
                error_msg = str(e).lower()
                if any(keyword in error_msg for keyword in ['unavailable', 'private', 'deleted', 'removed']):
                    raise VideoUnavailable(str(e))
                raise YouTubeDLError(f"Error extracting video info: {e}")
            except Exception as e:
                raise YouTubeDLError(f"Unexpected error: {e}")
        
        return await asyncio.to_thread(_get_info)
    
    async def search_videos(self, query: str, limit: int = 10) -> List[YouTubeVideo]:
        """Search for videos on YouTube with optimized performance"""
        def _search():
            try:
                # Optimize search query for music content
                search_query = query
                if not any(keyword in query.lower() for keyword in ['music', 'audio', 'song', 'track']):
                    search_query += " audio"
                
                final_query = f"ytsearch{min(limit, 50)}:{search_query}"  # Cap at 50 for performance
                
                with yt_dlp.YoutubeDL(self.search_opts) as ydl:
                    info = ydl.extract_info(final_query, download=False)
                    if not info or 'entries' not in info:
                        return []
                    
                    # Return optimized video info for search results
                    results = []
                    for entry in info['entries'][:limit]:
                        if entry:
                            # Enhanced search result with better metadata
                            video_info = {
                                'id': entry.get('id', ''),
                                'title': entry.get('title', ''),
                                'uploader': entry.get('uploader', entry.get('channel', '')),
                                'duration': entry.get('duration', 0),
                                'view_count': entry.get('view_count', 0),
                                'thumbnail': entry.get('thumbnail', ''),
                                'upload_date': entry.get('upload_date'),
                                # Additional metadata for better matching
                                'channel': entry.get('channel', ''),
                                'channel_id': entry.get('channel_id', ''),
                            }
                            results.append(YouTubeVideo(video_info))
                    
                    return results
            except yt_dlp.DownloadError as e:
                _log.error(f"Search error for query '{query}': {e}")
                return []
            except Exception as e:
                _log.error(f"Unexpected search error: {e}")
                return []
        
        return await asyncio.to_thread(_search)
    
    async def get_playlist_info(self, url: str) -> YouTubePlaylist:
        """Get playlist information from URL with optimizations"""
        def _get_playlist():
            try:
                with yt_dlp.YoutubeDL(self.playlist_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if not info:
                        raise YouTubeDLError(f"Could not extract playlist info for {url}")
                    
                    # Filter out unavailable entries
                    if 'entries' in info:
                        available_entries = [entry for entry in info['entries'] if entry and entry.get('id')]
                        info['entries'] = available_entries
                        _log.info(f"Playlist extracted: {len(available_entries)} available videos")
                    
                    return YouTubePlaylist(info)
            except yt_dlp.DownloadError as e:
                error_msg = str(e).lower()
                if 'playlist' in error_msg and 'not found' in error_msg:
                    raise YouTubeDLError(f"Playlist not found or unavailable: {url}")
                raise YouTubeDLError(f"Error extracting playlist info: {e}")
            except Exception as e:
                raise YouTubeDLError(f"Unexpected playlist error: {e}")
        
        return await asyncio.to_thread(_get_playlist)
    
    async def get_audio_url_only(self, video_id: str) -> Optional[str]:
        """Fast method to get only the audio URL without full metadata"""
        def _get_audio_url():
            try:
                url = f"https://www.youtube.com/watch?v={video_id}"
                with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if not info:
                        return None
                    
                    # Quick audio URL extraction
                    formats = info.get('formats', [])
                    for fmt in formats:
                        if fmt.get('vcodec') == 'none' and fmt.get('acodec') != 'none':
                            if fmt.get('ext') == 'm4a':  # Prefer m4a
                                return fmt['url']
                    
                    # Fallback to any audio format
                    for fmt in formats:
                        if fmt.get('acodec') != 'none':
                            return fmt['url']
                    
                    return None
            except Exception as e:
                _log.error(f"Error getting audio URL for {video_id}: {e}")
                return None
        
        return await asyncio.to_thread(_get_audio_url)
    
    async def batch_get_video_info(self, urls: List[str]) -> List[Optional[YouTubeVideo]]:
        """Get video information for multiple URLs concurrently"""
        semaphore = asyncio.Semaphore(5)  # Limit concurrent requests
        
        async def _get_with_semaphore(url: str) -> Optional[YouTubeVideo]:
            async with semaphore:
                try:
                    return await self.get_video_info(url)
                except Exception as e:
                    _log.warning(f"Failed to get info for {url}: {e}")
                    return None
        
        return await asyncio.gather(*[_get_with_semaphore(url) for url in urls], return_exceptions=False)
    
    def get_cache_info(self) -> Dict[str, Union[str, int, float]]:
        """Get information about the cache directory"""
        cache_dir = './temp_folder/yt_cache'
        try:
            if not os.path.exists(cache_dir):
                return {'status': 'not_found', 'size': 0, 'files': 0}
            
            total_size = 0
            file_count = 0
            
            for dirpath, dirnames, filenames in os.walk(cache_dir):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    try:
                        total_size += os.path.getsize(filepath)
                        file_count += 1
                    except OSError:
                        continue
            
            return {
                'status': 'exists',
                'path': cache_dir,
                'size_bytes': total_size,
                'size_mb': round(total_size / (1024 * 1024), 2),
                'files': file_count
            }
        except Exception as e:
            return {'status': 'error', 'error': str(e)}
    
    async def get_first_playlist_track_fast(self, url: str) -> Optional[YouTubeVideo]:
        """
        Ultra-fast extraction of just the first track from a playlist for immediate playback.
        Uses optimized yt-dlp options for minimal latency.
        """
        def _get_first():
            try:
                opts = {
                    **self.ydl_opts,
                    'playliststart': 1,
                    'playlistend': 1,
                    'extract_flat': False,  # Need full info for playback
                    'quiet': True,
                    'no_warnings': True,
                }
                
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info and 'entries' in info and info['entries']:
                        first_entry = info['entries'][0]
                        if first_entry and first_entry.get('id'):
                            return YouTubeVideo(first_entry)
                    elif info and info.get('id'):  # Single video
                        return YouTubeVideo(info)
                    
                return None
                
            except yt_dlp.DownloadError as e:
                error_msg = str(e).lower()
                if any(keyword in error_msg for keyword in ['unavailable', 'private', 'deleted']):
                    _log.warning(f"First playlist track unavailable: {e}")
                    return None
                raise YouTubeDLError(f"Error extracting first track: {e}")
            except Exception as e:
                raise YouTubeDLError(f"Unexpected error getting first track: {e}")
        
        try:
            return await asyncio.to_thread(_get_first)
        except Exception as e:
            _log.error(f"Failed to get first playlist track: {e}")
            return None

    async def get_flat_playlist_info(self, url: str) -> Dict[str, Any]:
        """
        Extract playlist metadata using flat extraction for maximum speed.
        Returns basic info about all tracks without downloading full metadata.
        """
        def _get_flat():
            try:
                with yt_dlp.YoutubeDL(self.metadata_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    
                    if not info:
                        return {}
                    
                    # Clean up and filter entries
                    if 'entries' in info:
                        valid_entries = []
                        for entry in info['entries']:
                            if entry and entry.get('id') and entry.get('title'):
                                # Add basic metadata for quick processing
                                valid_entries.append({
                                    'id': entry['id'],
                                    'title': entry.get('title', 'Unknown Title'),
                                    'duration': entry.get('duration'),
                                    'uploader': entry.get('uploader', entry.get('channel', 'Unknown')),
                                    'url': f"https://www.youtube.com/watch?v={entry['id']}"
                                })
                        
                        info['entries'] = valid_entries
                        _log.info(f"Flat playlist extracted: {len(valid_entries)} valid tracks")
                    
                    return info
                    
            except yt_dlp.DownloadError as e:
                _log.error(f"Error extracting flat playlist: {e}")
                return {}
            except Exception as e:
                _log.error(f"Unexpected flat playlist error: {e}")
                return {}
        
        try:
            return await asyncio.to_thread(_get_flat)
        except Exception as e:
            _log.error(f"Failed to get flat playlist info: {e}")
            return {}

    async def batch_create_video_metadata(self, flat_entries: List[Dict[str, Any]]) -> List[YouTubeVideo]:
        """
        Efficiently create YouTubeVideo objects from flat playlist entries.
        Uses minimal processing for faster background loading.
        """
        videos = []
        
        for entry in flat_entries:
            try:
                if not entry.get('id'):
                    continue
                
                # Create minimal video info dict
                video_info = {
                    'id': entry['id'],
                    'title': entry.get('title', 'Unknown Title'),
                    'uploader': entry.get('uploader', 'Unknown'),
                    'duration': entry.get('duration', 0),
                    'view_count': entry.get('view_count', 0),
                    'thumbnail': entry.get('thumbnail', ''),
                    'upload_date': entry.get('upload_date'),
                    'webpage_url': entry.get('url', f"https://www.youtube.com/watch?v={entry['id']}")
                }
                
                videos.append(YouTubeVideo(video_info))
                
            except Exception as e:
                _log.warning(f"Error creating video metadata for {entry.get('id', 'unknown')}: {e}")
        
        return videos 

    async def get_trending_videos(self, category: str = "music", region: str = "VN", limit: int = 10) -> List[YouTubeVideo]:
        """
        Get trending videos from YouTube
        
        Args:
            category (str): Category to get trending videos from (default: "music")
            region (str): Region code for trending (default: "VN" for Vietnam)
            limit (int): Number of videos to return (default: 10)
            
        Returns:
            List[YouTubeVideo]: List of trending videos
        """
        def _get_trending():
            try:
                # Configure yt-dlp for trending videos extraction
                trending_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': True,
                    'playlist_items': f'1-{limit}',
                    'socket_timeout': 30,
                    'retries': 3,
                    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                }
                
                # YouTube trending URL with music category and region
                # Use music category specifically for music trending
                trending_url = f"https://www.youtube.com/feed/trending?bp=4gINGgt5dG1hX2NoYXJ0cw%3D%3D&gl={region}"
                
                with yt_dlp.YoutubeDL(trending_opts) as ydl:
                    try:
                        info = ydl.extract_info(trending_url, download=False)
                        if info and 'entries' in info:
                            videos = []
                            for entry in info['entries']:
                                if entry and entry.get('id'):
                                    videos.append(YouTubeVideo(entry))
                            return videos[:limit]
                        return []
                    except Exception as e:
                        _log.error(f"Error extracting trending videos: {e}")
                        return []
                        
            except Exception as e:
                _log.error(f"Error in get_trending_videos: {e}")
                return []
        
        return await asyncio.to_thread(_get_trending)

    async def get_trending_music_video(self, region: str = "VN") -> Optional[YouTubeVideo]:
        """
        Get a single trending music video
        
        Args:
            region (str): Region code for trending (default: "VN" for Vietnam)
            
        Returns:
            Optional[YouTubeVideo]: A trending music video or None if not found
        """
        trending_videos = await self.get_trending_videos(category="music", region=region, limit=5)
        if trending_videos:
            # Return the first trending music video
            return trending_videos[0]
        return None 
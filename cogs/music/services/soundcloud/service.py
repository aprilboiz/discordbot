import asyncio
import logging
import time
from typing import List, Union, Optional

import requests
from soundcloud import AlbumPlaylist, BasicTrack, SoundCloud, Track

from core.exceptions import ResolveException
from patterns.singleton import SingletonMeta
from utils import to_thread

_log = logging.getLogger(__name__)


class SoundCloudService(metaclass=SingletonMeta):
    def __init__(self) -> None:
        self.sc = SoundCloud()
        self.client_id = self.sc.client_id
        self._last_client_id_refresh = time.time()
        self._client_id_refresh_interval = 3600  # Refresh client_id every hour
        
        # Custom headers to avoid SoundCloud restrictions
        self._custom_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

    def _refresh_client_id_if_needed(self) -> None:
        """Refresh client_id if it's been too long since last refresh."""
        current_time = time.time()
        if current_time - self._last_client_id_refresh > self._client_id_refresh_interval:
            try:
                # Create a new SoundCloud instance to get fresh client_id
                new_sc = SoundCloud()
                self.sc = new_sc
                self.client_id = new_sc.client_id
                self._last_client_id_refresh = current_time
                _log.info("Refreshed SoundCloud client_id")
            except Exception as e:
                _log.warning(f"Failed to refresh client_id: {e}")

    @to_thread
    def search(self, query: str):
        _log.debug(f"Searching for: '{query}'")
        return self.sc.search(query)

    @to_thread
    def resolve_url(self, url: str):
        """
        Resolve SoundCloud URL with enhanced error handling and user-agent spoofing.
        """
        try:
            _log.debug(f"Resolving SoundCloud URL: '{url}'")
            
            # Try with the original method first
            r = self.sc.resolve(url)
            _log.debug(f"Resolved URL: '{url}'. Type: {type(r)}")
            return r
        except Exception as e:
            _log.warning(f"Standard resolve failed for URL '{url}': {e}")
            
            # Try with fresh client_id
            try:
                self._refresh_client_id_if_needed()
                r = self.sc.resolve(url)
                _log.debug(f"Resolved URL with fresh client_id: '{url}'. Type: {type(r)}")
                return r
            except Exception as e2:
                _log.error(f"Failed to resolve SoundCloud URL '{url}' even with fresh client_id: {e2}")
                raise ResolveException(f"Cannot resolve SoundCloud URL: {url}. Error: {e2}")

    def get_thumbnail(self, track: Union[Track, BasicTrack]) -> str:
        return track.artwork_url or track.user.avatar_url

    @to_thread
    def get_playback_url(self, track: Union[Track, BasicTrack]) -> Optional[str]:
        """
        Get the playback URL for a SoundCloud track with comprehensive fallback strategy.
        
        Fallback order:
        1. HLS transcoding (preferred for streaming)
        2. Progressive HTTP MP3 128k
        3. Progressive HTTP MP3 64k
        4. Any other available progressive transcoding
        5. Original file URL if available
        
        Returns:
            str: Valid playback URL or None if all attempts fail
        """
        if not hasattr(track, 'media') or not track.media or not track.media.transcodings:
            _log.error(f"Track '{track.title}' has no media transcodings available")
            return None

        transcodings = track.media.transcodings
        track_authorization = track.track_authorization

        # Try each strategy in order
        strategies = [
            self._try_hls_transcoding,
            self._try_http_mp3_128_transcoding,
            self._try_http_mp3_64_transcoding,
            self._try_any_progressive_transcoding,
            self._try_original_file_url
        ]

        for strategy in strategies:
            try:
                url = strategy(transcodings, track_authorization, track)
                if url:
                    _log.debug(f"Successfully got playback URL for: '{track.title}' using {strategy.__name__}")
                    return url
            except Exception as e:
                _log.warning(f"Strategy {strategy.__name__} failed for track '{track.title}': {e}")
                continue

        _log.error(f"All playback URL strategies failed for track: '{track.title}'")
        return None

    def _try_hls_transcoding(self, transcodings, track_authorization: str, track: Union[Track, BasicTrack]) -> Optional[str]:
        """Try to get HLS streaming URL (preferred)."""
        hls_transcoding = next((t for t in transcodings if 'hls' in t.format.protocol.lower()), None)
        if hls_transcoding:
            return self._get_stream_url_from_transcoding(hls_transcoding, track_authorization, track)
        return None

    def _try_http_mp3_128_transcoding(self, transcodings, track_authorization: str, track: Union[Track, BasicTrack]) -> Optional[str]:
        """Try to get HTTP MP3 128kbps URL."""
        mp3_128_transcoding = next((t for t in transcodings 
                                  if 'progressive' in t.format.protocol.lower() 
                                  and 'mp3' in t.format.mime_type.lower()
                                  and '128' in str(t.quality).lower()), None)
        if mp3_128_transcoding:
            return self._get_stream_url_from_transcoding(mp3_128_transcoding, track_authorization, track)
        return None

    def _try_http_mp3_64_transcoding(self, transcodings, track_authorization: str, track: Union[Track, BasicTrack]) -> Optional[str]:
        """Try to get HTTP MP3 64kbps URL."""
        mp3_64_transcoding = next((t for t in transcodings 
                                 if 'progressive' in t.format.protocol.lower() 
                                 and 'mp3' in t.format.mime_type.lower()
                                 and '64' in str(t.quality).lower()), None)
        if mp3_64_transcoding:
            return self._get_stream_url_from_transcoding(mp3_64_transcoding, track_authorization, track)
        return None

    def _try_any_progressive_transcoding(self, transcodings, track_authorization: str, track: Union[Track, BasicTrack]) -> Optional[str]:
        """Try any progressive transcoding available."""
        progressive_transcodings = [t for t in transcodings 
                                  if 'progressive' in t.format.protocol.lower()]
        
        for transcoding in progressive_transcodings:
            try:
                url = self._get_stream_url_from_transcoding(transcoding, track_authorization, track)
                if url:
                    return url
            except Exception:
                continue
        return None

    def _try_original_file_url(self, transcodings, track_authorization: str, track: Union[Track, BasicTrack]) -> Optional[str]:
        """Try to get the original file URL if available."""
        try:
            # Check if track has downloadable attribute and it's available
            if hasattr(track, 'downloadable') and getattr(track, 'downloadable', False):
                download_url = getattr(track, 'download_url', None)
                if download_url:
                    # Attempt to access original file
                    params = {
                        "client_id": self.client_id,
                        "track_authorization": track_authorization,
                    }
                    response = requests.get(
                        download_url, 
                        headers={**self.sc._get_default_headers(), **self._custom_headers}, 
                        params=params,
                        timeout=10,
                        allow_redirects=False
                    )
                    if response.status_code == 302 and 'Location' in response.headers:
                        return response.headers['Location']
        except Exception as e:
            _log.debug(f"Original file URL not accessible: {e}")
        return None

    def _get_stream_url_from_transcoding(self, transcoding, track_authorization: str, track: Union[Track, BasicTrack]) -> Optional[str]:
        """
        Get stream URL from a specific transcoding with retry logic and client_id rotation.
        """
        max_retries = 2
        
        for attempt in range(max_retries):
            try:
                # Refresh client_id if needed
                if attempt > 0:
                    self._refresh_client_id_if_needed()
                
                params = {
                    "client_id": self.client_id,
                    "track_authorization": track_authorization,
                }
                
                # Use custom headers to avoid restrictions
                headers = {**self.sc._get_default_headers(), **self._custom_headers}
                
                response = requests.get(
                    transcoding.url, 
                    headers=headers, 
                    params=params,
                    timeout=10
                )
                
                if response.status_code == 200:
                    response_data = response.json()
                    if 'url' in response_data and response_data['url']:
                        return response_data['url']
                elif response.status_code == 404:
                    _log.warning(f"404 error for transcoding URL (attempt {attempt + 1}): {transcoding.url}")
                    if attempt < max_retries - 1:
                        # Try refreshing client_id for next attempt
                        self._refresh_client_id_if_needed()
                        time.sleep(0.5)  # Brief delay before retry
                        continue
                else:
                    _log.warning(f"HTTP {response.status_code} for transcoding URL: {transcoding.url}")
                    
            except requests.RequestException as e:
                _log.warning(f"Request error for transcoding (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
            except Exception as e:
                _log.warning(f"Unexpected error for transcoding (attempt {attempt + 1}): {e}")
                break
        
        return None

    @to_thread
    def __get_tracks(self, track_ids: list[int]) -> List[BasicTrack]:
        """Get tracks from track IDs. Maximum 50 tracks per request."""
        return self.sc.get_tracks(track_ids)

    async def get_tracks_info(self, track_ids: list[int], **kwargs) -> List[BasicTrack]:
        """Get tracks from track IDs. Recommended to use this method for getting tracks."""
        MAX_TRACKS_PER_REQUEST = 50
        chunks = [
            track_ids[i : i + MAX_TRACKS_PER_REQUEST]
            for i in range(0, len(track_ids), MAX_TRACKS_PER_REQUEST)
        ]
        tracks = []
        r = await asyncio.gather(*[self.__get_tracks(chunk) for chunk in chunks])
        for res in r:
            tracks.extend(res)
        return tracks

    async def extract_song_from_url(self, url: str):
        """
        Extract song(s) from SoundCloud URL with enhanced playlist/set support.
        """
        try:
            resolve = await self.resolve_url(url)
            if resolve is None or not isinstance(resolve, (AlbumPlaylist, Track)):
                if resolve is None:
                    error = f"Cannot resolve the SoundCloud URL: '{url}'. This may be due to privacy restrictions, region blocking, or the content may no longer be available."
                    _log.error(error)
                    raise ResolveException(error)
                else:
                    error = f"Resolve type is {type(resolve)}. Expected type is Track or AlbumPlaylist for URL: '{url}'"
                    _log.error(error)
                    raise ResolveException(error)

            if isinstance(resolve, AlbumPlaylist):
                _log.info(f"Resolved SoundCloud playlist/set: '{resolve.title}' with {len(resolve.tracks)} tracks")
                return {
                    "playlist_id": resolve.id,
                    "playlist_name": resolve.title,
                    "tracks": resolve.tracks,
                }
            elif isinstance(resolve, Track):
                _log.info(f"Resolved single SoundCloud track: '{resolve.title}'")
                return {
                    "playlist_id": None,
                    "playlist_name": None,
                    "tracks": [resolve],
                }
            else:
                _log.warning(f"Unexpected resolve type for URL '{url}': {type(resolve)}")
                return None
        except Exception as e:
            _log.error(f"Failed to extract song from SoundCloud URL '{url}': {e}")
            raise ResolveException(f"Failed to extract from SoundCloud URL: {e}")

import asyncio
import logging
from typing import List, Union

import aiohttp
from soundcloud import AlbumPlaylist, BasicTrack, SoundCloud, Track

from core.exceptions import ResolveException
from patterns.singleton import SingletonMeta
from utils import to_thread
from utils.network_utils import network_manager

_log = logging.getLogger(__name__)


class SoundCloudService(metaclass=SingletonMeta):
    def __init__(self) -> None:
        self.sc = SoundCloud()
        self.client_id = self.sc.client_id

    @to_thread
    def search(self, query: str):
        _log.debug(f"Searching for: '{query}'")
        return self.sc.search(query)

    @to_thread
    def resolve_url(self, url: str):
        r = self.sc.resolve(url)
        _log.debug(f"Resolved URL: '{url}'. Type: {type(r)}")
        return r

    def get_thumbnail(self, track: Union[Track, BasicTrack]) -> str:
        return track.artwork_url or track.user.avatar_url

    async def get_playback_url(self, track: Union[Track, BasicTrack]):
        # Transcoding involves 3 types of protocols: HLS, progressive and Opus.
        # We prefer to use the 'HLS' protocol because it's the best for streaming.
        stream_url = track.media.transcodings[0].url
        track_authorization = track.track_authorization
        params = {
            "client_id": self.client_id,
            "track_authorization": track_authorization,
        }
        
        try:
            response = await network_manager.request_with_retry(
                "GET", 
                stream_url, 
                headers=self.sc._get_default_headers(), 
                params=params
            )
            response_data = await response.json()
            _log.debug(f"Got playback URL for: '{track.title}'")
            return response_data["url"]
        except Exception as e:
            _log.error(f"Failed to get playback URL for '{track.title}': {e}")
            raise

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
        resolve = await self.resolve_url(url)
        if resolve is None or not isinstance(resolve, (AlbumPlaylist, Track)):
            if resolve is None:
                error = f"Cannot resolve the URL: '{url}'."
                _log.error(error)
                raise ResolveException(error)
            else:
                error = f"Resolve type is {type(resolve)}. Expected type is Track or AlbumPlaylist."
                _log.error(error)
                raise ResolveException(error)

        if isinstance(resolve, AlbumPlaylist):
            return {
                "playlist_id": resolve.id,
                "playlist_name": resolve.title,
                "tracks": resolve.tracks,
            }
        elif isinstance(resolve, Track):
            return {
                "playlist_id": None,
                "playlist_name": None,
                "tracks": [resolve],
            }
        else:
            return None

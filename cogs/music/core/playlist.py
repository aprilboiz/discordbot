import asyncio
import logging
from collections import deque
from typing import Deque, List, Optional, TYPE_CHECKING
import heapq
import time

from cogs.music.core.song import (
    Song,
    SongMeta,
    SoundCloudSongMeta,
    YouTubeSongMeta,
    createSong,
    get_songs_info,
)
from core.exceptions import MusicException
from patterns.observe import Observable, Observer
from utils import convert_to_second, convert_to_time

if TYPE_CHECKING:
    from cogs.music.controller import Audio

_logger = logging.getLogger(__name__)


class PriorityQueueItem:
    """
    Wrapper for priority queue items to handle priority-based ordering.
    Edge case: Handle priority songs that need to play immediately after current song.
    """
    def __init__(self, song: SongMeta, priority: int = 0, timestamp: Optional[float] = None):
        self.song = song
        self.priority = priority  # Lower number = higher priority
        self.timestamp = timestamp or time.time()

    def __lt__(self, other):
        # Higher priority (lower number) comes first, then by timestamp
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.timestamp < other.timestamp


class PlayList(Observable):
    def __init__(self) -> None:
        super().__init__()
        self._q: Deque[SongMeta] = deque()
        self._priority_queue: List[PriorityQueueItem] = []  # Priority heap
        self.lock: asyncio.Lock = asyncio.Lock()
        self._prepared_songs: dict[str, Song] = {}  # Cache for pre-loaded songs
        self._preparation_tasks: dict[str, asyncio.Task] = {}  # Track background tasks
        self._background_loading = False  # Track if background loading is active

    async def add(self, song: SongMeta) -> None:
        """
        Adds a song to the playlist queue and notifies any waiting coroutines.
        Also triggers background preparation of the song if it's near the front of the queue.
        """
        async with self.lock:
            self._q.append(song)
            await self.notify()
            
            # Start background preparation if this is one of the next few songs
            if len(self._q) <= 3:  # Prepare the next 3 songs
                self._schedule_background_preparation(song)

    async def add_next(self, song: SongMeta) -> None:
        """
        Adds a priority song to play immediately after the current song.
        Edge case: Handle priority insertion during active playlist loading.
        """
        async with self.lock:
            # Add to priority queue with high priority
            priority_item = PriorityQueueItem(song, priority=0)  # Highest priority
            heapq.heappush(self._priority_queue, priority_item)
            await self.notify()
            
            # Immediately start preparing this priority song
            self._schedule_background_preparation(song, high_priority=True)

    async def add_priority_batch(self, songs: List[SongMeta]) -> None:
        """
        Add multiple priority songs efficiently.
        Edge case: Handle large priority batches without blocking.
        """
        async with self.lock:
            timestamp = time.time()
            for i, song in enumerate(songs):
                # Add with incrementing priority to maintain order
                priority_item = PriorityQueueItem(song, priority=i, timestamp=timestamp + i * 0.001)
                heapq.heappush(self._priority_queue, priority_item)
            
            await self.notify()
            
            # Prepare the first few priority songs
            for song in songs[:2]:  # Prepare first 2 priority songs
                self._schedule_background_preparation(song, high_priority=True)

    def index(self, song: SongMeta) -> Optional[int]:
        """
        Get the index of a song in the queue.

        Args:
            song (SongMeta): The song to find in the queue.

        Returns:
            int | None: The index of the song if found, otherwise None.
        """
        try:
            return self._q.index(song)
        except ValueError:
            return None

    def get_at(self, index: int) -> Optional[SongMeta]:
        """
        Get the song at a specified index in the queue.

        Args:
            index (int): The index of the song to retrieve.

        Returns:
            SongMeta | None: The song at the specified index, or None if the index is out of range.
        """
        try:
            return self._q[index]
        except IndexError:
            return None

    async def remove_by_index(self, index: int) -> None:
        """
        Remove a song from the queue at a specified index.

        Args:
            index (int): The index of the song to remove.

        Returns:
            None
        """
        async with self.lock:
            try:
                self._q.remove(self._q[index])
            except IndexError:
                pass

    async def remove_by_song(self, song: SongMeta) -> None:
        """
        Remove a song from the queue.

        Args:
            song (SongMeta): The song to remove from the queue.

        Returns:
            None
        """
        async with self.lock:
            try:
                self._q.remove(song)
            except ValueError:
                pass

    def size(self) -> int:
        """
        Returns the total number of items in both queues.
        """
        return len(self._q) + len(self._priority_queue)

    def priority_size(self) -> int:
        """
        Returns the number of priority items in queue.
        """
        return len(self._priority_queue)

    def empty(self) -> bool:
        """
        Check if the playlist is empty.

        Returns:
            bool: True if the playlist is empty, False otherwise.
        """
        return self.size() == 0

    def clear(self) -> None:
        """
        Clears both queues and cancels all background preparation tasks.
        Edge case: Handle cleanup during active background processing.
        """
        self._q.clear()
        self._priority_queue.clear()
        self._background_loading = False
        
        # Cancel all background preparation tasks
        for task in self._preparation_tasks.values():
            if not task.done():
                task.cancel()
        self._preparation_tasks.clear()
        
        # Clear prepared songs cache
        self._prepared_songs.clear()

    def time_wait(self, to_song_index: int | None = None) -> str:
        """
        Calculate the total duration of songs in the queue up to a specified index.
        Args:
            to_song_index (int | None): The index up to which the total duration is calculated.
                                        If None, the total duration of all songs in the queue is calculated.
        Returns:
            str: The total duration in a human-readable format (HH:MM:SS).
        """

        if to_song_index is None:
            to_song_index = len(self._q)

        sec = 0
        for i in range(to_song_index):
            sec += convert_to_second(time=self._q[i].duration)

        return convert_to_time(seconds=sec)

    async def get_list(self, limit: int | None = None) -> List[SongMeta]:
        """
        Retrieve a list of songs from the playlist.
        Args:
            limit (int | None, optional): The maximum number of songs to retrieve.
                                          If None, all songs in the playlist are returned. Defaults to None.
        Returns:
            List[SongMeta]: A list of SongMeta objects representing the songs in the playlist.
        """

        if limit is None:
            return [song for song in self._q]
        else:
            return [song for song in list(self._q)[:limit]]

    def get_next(self) -> SongMeta | None:
        """
        Retrieves the next song from the playlist with proper priority handling.
        Priority songs always play before regular queue songs.
        
        Returns:
            SongMeta | None: The next song to play, or None if the playlist is empty.
        """
        # First check priority queue
        if self._priority_queue:
            priority_item = heapq.heappop(self._priority_queue)
            _logger.debug(f"Returning priority song: {priority_item.song.title}")
            return priority_item.song
        
        # Then check regular queue
        if self._q:
            song = self._q.popleft()
            _logger.debug(f"Returning regular queue song: {song.title}")
            return song
        
        return None

    async def get_next_prepared(self) -> Song | None:
        """
        Asynchronously retrieves the next prepared song with optimized pre-loading.
        Edge case: Handle cache misses and preparation failures gracefully.
        """
        song_meta = self.get_next()
        if song_meta is None:
            return None

        song_key = self._get_song_key(song_meta)
        
        # Check if song is already prepared
        if song_key in self._prepared_songs:
            prepared_song = self._prepared_songs.pop(song_key)
            _logger.debug(f"Using pre-prepared song: {song_meta.title}")
            
            # Trigger preparation of the next few songs
            self._prepare_upcoming_songs()
            
            return prepared_song

        # Song not prepared yet, prepare it now
        _logger.debug(f"Preparing song on-demand: {song_meta.title}")
        song_obj = await createSong(song_meta)
        
        if song_obj is None:
            # Edge case: Handle song creation failure and retry with next song
            await self._handle_song_creation_failure(song_meta)
            
            if self.size() > 0:
                return await self.get_next_prepared()
            else:
                return None

        # Trigger preparation of upcoming songs
        self._prepare_upcoming_songs()
        
        return song_obj

    async def _handle_song_creation_failure(self, song_meta: SongMeta) -> None:
        """
        Handle song creation failures with appropriate logging and error tracking.
        Edge case: Different failure reasons for different platforms.
        """
        id_info = None
        reason = "Unknown error"
        
        if isinstance(song_meta, YouTubeSongMeta):
            id_info = song_meta.video_id
            reason = f"The requested YouTube song '{song_meta.title}' may not be available, age-restricted, or region-blocked"
        elif isinstance(song_meta, SoundCloudSongMeta):
            id_info = song_meta.track_id
            reason = f"The requested SoundCloud song '{song_meta.title}' may not be available, region-restricted, or have expired playback URLs. Try refreshing the playlist or using alternative sources."

        _logger.error(
            f"Failed to create song with type: {type(song_meta)}. Title: {song_meta.title}. ID: {id_info}. Reason: {reason}"
        )
        
        # For SoundCloud failures, add a small delay to avoid rapid retry cycles
        if isinstance(song_meta, SoundCloudSongMeta):
            await asyncio.sleep(0.5)

    def _prepare_upcoming_songs(self) -> None:
        """
        Trigger background preparation of the next few songs in queue.
        Edge case: Handle both priority and regular queue songs.
        """
        try:
            songs_to_prepare = []
            
            # Prepare priority songs first
            for item in self._priority_queue[:2]:  # First 2 priority songs
                songs_to_prepare.append(item.song)
            
            # Then prepare regular queue songs
            for song in list(self._q)[:3]:  # First 3 regular songs
                songs_to_prepare.append(song)
            
            # Schedule preparation for selected songs
            for song in songs_to_prepare:
                self._schedule_background_preparation(song)
                
        except Exception as e:
            _logger.error(f"Error scheduling upcoming song preparation: {e}")

    def _schedule_background_preparation(self, song: SongMeta, high_priority: bool = False) -> None:
        """
        Schedule background preparation of a song to reduce playback latency.
        Edge case: Prevent overwhelming the system with too many concurrent preparations.
        """
        song_key = self._get_song_key(song)
        
        # Don't prepare if already prepared or being prepared
        if song_key in self._prepared_songs or song_key in self._preparation_tasks:
            return
        
        # Limit concurrent preparation tasks to prevent resource exhaustion
        active_tasks = sum(1 for task in self._preparation_tasks.values() if not task.done())
        if not high_priority and active_tasks >= 5:  # Max 5 concurrent preparations
            return
            
        # Create background task for song preparation
        task = asyncio.create_task(self._prepare_song_background(song, high_priority))
        self._preparation_tasks[song_key] = task

    async def _prepare_song_background(self, song: SongMeta, high_priority: bool = False) -> None:
        """
        Background task to prepare a song for playback.
        Edge case: Handle preparation failures and resource management.
        """
        song_key = self._get_song_key(song)
        
        try:
            if not high_priority:
                # Add a small delay for non-priority songs to not overwhelm the system
                await asyncio.sleep(1.0)
            
            prepared_song = await createSong(song)
            if prepared_song:
                async with self.lock:
                    # Edge case: Check if we should still cache this song
                    if len(self._prepared_songs) < 20:  # Limit cache size
                        self._prepared_songs[song_key] = prepared_song
                        _logger.debug(f"Background prepared song: {song.title}")
                    else:
                        _logger.debug(f"Cache full, skipping preparation for: {song.title}")
            
        except Exception as e:
            _logger.error(f"Failed to prepare song in background: {song.title}, Error: {e}")
        finally:
            # Clean up the task reference
            if song_key in self._preparation_tasks:
                del self._preparation_tasks[song_key]

    def _get_song_key(self, song: SongMeta) -> str:
        """
        Generate a unique key for a song based on its metadata.
        Edge case: Handle different song formats and platforms.
        """
        if isinstance(song, YouTubeSongMeta):
            return f"YT_{song.video_id}"
        elif isinstance(song, SoundCloudSongMeta):
            return f"SC_{song.track_id}"
        else:
            raise ValueError(f"Unsupported song format: {type(song)}")

    async def __update_all_song_meta(self) -> None:
        """
        Asynchronously updates the metadata for all songs in the queue.
        This method iterates through the song queue and identifies songs that need
        metadata updates (i.e., songs with a `None` title). It then fetches the
        updated metadata for these songs and applies the updates.
        Steps:
        1. Logs the start of the update process.
        2. Collects songs that need metadata updates.
        3. Fetches updated metadata for the collected songs.
        4. Applies the updated metadata to the songs in the queue.
        5. Logs the number of songs that were updated.
        Returns:
            None
        """

        _logger.debug("Updating all song meta info.")
        # Get all songs that need to update meta info. (title is None)
        # Note: None means that the song is not need to update meta info.
        songs_need_to_update = []
        for song in self._q:
            if song.title is None:
                songs_need_to_update.append(song)
            else:
                songs_need_to_update.append(None)

        songs_with_info = await get_songs_info(songs_need_to_update)
        for song, song_with_info in zip(self._q, songs_with_info):
            if song_with_info is not None:
                song = song_with_info
        _logger.debug(
            f"Updated {len([i for i in songs_with_info if i is not None])} song(s) meta info."
        )

    def trigger_update_all_song_meta(self) -> None:
        """
        Trigger update all song meta info.

        This method should be called when a new song is added. It will initiate the
        `__update_all_song_meta` method in the background to update the metadata for
        all songs.

        Returns:
            None
        """
        _logger.debug("Triggering update all song meta info.")
        asyncio.create_task(self.__update_all_song_meta())


class PlaylistObserver(Observer):
    def __init__(self, player: "Audio") -> None:
        super().__init__()
        self.player = player

    async def update(self, observable: PlayList) -> None:
        await self.player.play_next()

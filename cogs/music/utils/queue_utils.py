"""
Queue management utilities for the music bot.
Provides helper functions for queue operations, pagination, and statistics.
"""

import asyncio
import random
from typing import List, Optional, Tuple
from dataclasses import dataclass

from cogs.music.core.song import SongMeta


@dataclass
class QueueStats:
    """Statistics about the current queue"""
    total_songs: int
    total_duration_seconds: int
    total_duration_formatted: str
    priority_songs: int
    prepared_songs: int
    is_loading: bool


class QueuePaginator:
    """Helper class for paginating queue displays"""
    
    def __init__(self, songs: List[SongMeta], items_per_page: int = 10):
        self.songs = songs
        self.items_per_page = items_per_page
        self.total_pages = max(1, (len(songs) - 1) // items_per_page + 1)
    
    def get_page(self, page_number: int) -> Tuple[List[SongMeta], bool, bool]:
        """
        Get songs for a specific page.
        
        Returns:
            Tuple of (songs_on_page, has_previous, has_next)
        """
        if page_number < 1:
            page_number = 1
        elif page_number > self.total_pages:
            page_number = self.total_pages
            
        start_idx = (page_number - 1) * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.songs))
        
        songs_on_page = self.songs[start_idx:end_idx]
        has_previous = page_number > 1
        has_next = page_number < self.total_pages
        
        return songs_on_page, has_previous, has_next
    
    def get_page_info(self, page_number: int) -> str:
        """Get formatted page information string"""
        return f"Page {page_number}/{self.total_pages}"


class DurationCalculator:
    """Helper class for calculating durations from song metadata"""
    
    @staticmethod
    def parse_duration(duration_str: str) -> int:
        """
        Parse duration string to seconds.
        Supports formats: MM:SS, HH:MM:SS
        
        Args:
            duration_str: Duration in string format
            
        Returns:
            Duration in seconds, 0 if parsing fails
        """
        if not duration_str:
            return 0
            
        try:
            time_parts = duration_str.split(':')
            if len(time_parts) == 2:  # MM:SS
                minutes, seconds = map(int, time_parts)
                return minutes * 60 + seconds
            elif len(time_parts) == 3:  # HH:MM:SS
                hours, minutes, seconds = map(int, time_parts)
                return hours * 3600 + minutes * 60 + seconds
            else:
                return 0
        except (ValueError, IndexError):
            return 0
    
    @staticmethod
    def format_duration(total_seconds: int) -> str:
        """
        Format seconds into HH:MM:SS or MM:SS format.
        
        Args:
            total_seconds: Total duration in seconds
            
        Returns:
            Formatted duration string
        """
        if total_seconds <= 0:
            return "0:00"
            
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"
    
    @staticmethod
    def calculate_total_duration(songs: List[SongMeta]) -> Tuple[int, str]:
        """
        Calculate total duration of a list of songs.
        
        Args:
            songs: List of song metadata
            
        Returns:
            Tuple of (total_seconds, formatted_duration)
        """
        total_seconds = 0
        
        for song in songs:
            if song.duration:
                total_seconds += DurationCalculator.parse_duration(song.duration)
        
        formatted = DurationCalculator.format_duration(total_seconds)
        return total_seconds, formatted


class QueueManager:
    """Advanced queue management operations"""
    
    @staticmethod
    async def shuffle_queue(playlist, preserve_first: bool = False) -> bool:
        """
        Shuffle the queue while optionally preserving the first song.
        
        Args:
            playlist: The playlist object to shuffle
            preserve_first: Whether to keep the first song in place
            
        Returns:
            True if shuffle was successful
        """
        try:
            async with playlist.lock:
                queue_list = list(playlist._q)
                
                if len(queue_list) < 2:
                    return False
                
                if preserve_first and len(queue_list) > 1:
                    # Shuffle everything except the first song
                    first_song = queue_list[0]
                    remaining_songs = queue_list[1:]
                    random.shuffle(remaining_songs)
                    queue_list = [first_song] + remaining_songs
                else:
                    # Shuffle the entire queue
                    random.shuffle(queue_list)
                
                # Clear and rebuild the queue
                playlist._q.clear()
                for song in queue_list:
                    playlist._q.append(song)
                
                return True
                
        except Exception:
            return False
    
    @staticmethod
    async def move_song(playlist, from_position: int, to_position: int) -> bool:
        """
        Move a song from one position to another in the queue.
        
        Args:
            playlist: The playlist object
            from_position: Source position (1-indexed)
            to_position: Target position (1-indexed)
            
        Returns:
            True if move was successful
        """
        try:
            async with playlist.lock:
                queue_list = list(playlist._q)
                queue_size = len(queue_list)
                
                # Validate positions
                if (from_position < 1 or from_position > queue_size or 
                    to_position < 1 or to_position > queue_size or
                    from_position == to_position):
                    return False
                
                # Convert to 0-indexed
                from_idx = from_position - 1
                to_idx = to_position - 1
                
                # Move the song
                song = queue_list.pop(from_idx)
                queue_list.insert(to_idx, song)
                
                # Rebuild the queue
                playlist._q.clear()
                for s in queue_list:
                    playlist._q.append(s)
                
                return True
                
        except Exception:
            return False
    
    @staticmethod
    def get_queue_stats(playlist, current_song=None) -> QueueStats:
        """
        Get comprehensive statistics about the queue.
        
        Args:
            playlist: The playlist object
            current_song: Currently playing song (optional)
            
        Returns:
            QueueStats object with queue information
        """
        try:
            # Get basic queue info
            queue_songs = list(playlist._q)
            total_songs = len(queue_songs)
            priority_songs = getattr(playlist, 'priority_size', lambda: 0)()
            
            # Calculate duration
            total_seconds, formatted_duration = DurationCalculator.calculate_total_duration(queue_songs)
            
            # Get loading stats if available
            is_loading = False
            prepared_songs = 0
            
            if hasattr(playlist, 'get_background_loading_stats'):
                loading_stats = playlist.get_background_loading_stats()
                is_loading = loading_stats.get('is_loading', False)
                prepared_songs = loading_stats.get('prepared_songs', 0)
            
            return QueueStats(
                total_songs=total_songs,
                total_duration_seconds=total_seconds,
                total_duration_formatted=formatted_duration,
                priority_songs=priority_songs,
                prepared_songs=prepared_songs,
                is_loading=is_loading
            )
            
        except Exception:
            return QueueStats(
                total_songs=0,
                total_duration_seconds=0,
                total_duration_formatted="0:00",
                priority_songs=0,
                prepared_songs=0,
                is_loading=False
            )


class SearchResultsFormatter:
    """Helper for formatting search results consistently"""
    
    @staticmethod
    def format_song_info(song: SongMeta, index: int) -> str:
        """
        Format a single song for display in search results or queue.
        
        Args:
            song: Song metadata
            index: Display index (1-indexed)
            
        Returns:
            Formatted string for the song
        """
        title = song.title or "Unknown Title"
        duration = song.duration or "??:??"
        
        # Get uploader/author info
        uploader = (getattr(song, 'uploader', None) or 
                   getattr(song, 'author', None) or 
                   "Unknown Artist")
        
        # Truncate title if too long
        if len(title) > 45:
            title = title[:42] + "..."
        
        return f"`{index}.` **{title}** `({duration})`\n    👤 {uploader}"
    
    @staticmethod
    def format_queue_display(songs: List[SongMeta], start_index: int = 1) -> str:
        """
        Format multiple songs for queue display.
        
        Args:
            songs: List of song metadata
            start_index: Starting index for numbering
            
        Returns:
            Formatted string for all songs
        """
        if not songs:
            return "Queue is empty"
        
        formatted_songs = []
        for i, song in enumerate(songs):
            formatted_songs.append(
                SearchResultsFormatter.format_song_info(song, start_index + i)
            )
        
        return "\n\n".join(formatted_songs)


# Utility functions for backwards compatibility
async def calculate_queue_duration(songs: List[SongMeta]) -> str:
    """Calculate total duration of songs in a queue (legacy function)"""
    _, formatted = DurationCalculator.calculate_total_duration(songs)
    return formatted


async def get_queue_page(songs: List[SongMeta], page: int, per_page: int = 10) -> List[SongMeta]:
    """Get a specific page of songs from the queue (legacy function)"""
    paginator = QueuePaginator(songs, per_page)
    page_songs, _, _ = paginator.get_page(page)
    return page_songs 
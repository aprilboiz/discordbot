# Music System - Restructured Architecture

## Overview

The music system has been completely restructured to eliminate redundancy, improve performance, and simplify maintenance. The new architecture follows a cleaner separation of concerns and reduces complexity.

## Architecture Changes

### Before (Old Structure)
```
cogs/music/
├── core/
│   ├── album.py          # Simple Album dataclass
│   ├── song.py           # Complex Song classes with multiple implementations
│   └── playlist.py       # Complex playlist with observer pattern
├── manager.py            # Player management
├── controller.py         # Audio control
├── music.py              # Main cog
├── extractor.py          # Song extraction
└── search.py             # Search functionality
```

### After (New Structure)
```
cogs/music/
├── core/
│   ├── models.py         # Unified models (Song, Album, SongMeta)
│   └── playlist.py       # Simplified playlist with async preparation
├── audio_controller.py   # Combined audio control and management
├── music_cog.py          # Simplified main cog
├── extractor.py          # Song extraction (unchanged)
└── search.py             # Search functionality (unchanged)
```

## Key Improvements

### 1. Unified Models (`core/models.py`)
- **Combined**: `album.py` + `song.py` → `models.py`
- **Simplified**: Single source of truth for all data models
- **Enhanced**: Async song preparation with better resource management
- **Type Safety**: Better type hints and validation

### 2. Simplified Playlist (`core/playlist.py`)
- **Performance**: Pre-preparation of next 3 songs
- **Memory Efficient**: Deque-based storage with size limits
- **Observer Pattern**: Cleaner observer implementation with weak references
- **Async Operations**: All operations are properly async

### 3. Unified Audio Controller (`audio_controller.py`)
- **Combined**: `manager.py` + `controller.py` → `audio_controller.py`
- **Centralized**: Single point for all audio operations
- **Metrics**: Built-in performance tracking
- **Resource Management**: Proper cleanup and timeout handling
- **Connection Limits**: Built-in connection limiting (50 concurrent)

### 4. Simplified Music Cog (`music_cog.py`)
- **Clean Commands**: All slash commands with proper error handling
- **Decorator Pattern**: Reusable voice connection decorator
- **Better UX**: Improved user feedback and error messages
- **Statistics**: Built-in performance monitoring

## Removed Redundancies

### Files Removed
1. `core/album.py` - Merged into `models.py`
2. `core/song.py` - Replaced by enhanced `models.py`
3. `core/playlist.py` - Replaced by simplified version
4. `manager.py` - Functionality moved to `audio_controller.py`
5. `controller.py` - Functionality moved to `audio_controller.py`
6. `music.py` - Replaced by `music_cog.py`

### Functionality Consolidation
- **Song Management**: All song-related logic in `models.py`
- **Audio Control**: All audio operations in `audio_controller.py`
- **Command Handling**: All commands in `music_cog.py`

## Performance Improvements

### 1. Async Song Preparation
- Songs are prepared asynchronously in background
- Next 3 songs are pre-prepared for instant playback
- Preparation locks prevent race conditions

### 2. Connection Management
- Global audio manager with connection limits
- Automatic cleanup of inactive controllers
- Resource monitoring and metrics

### 3. Memory Optimization
- Weak references to prevent memory leaks
- Deque-based storage for efficient queue operations
- Automatic garbage collection of unused resources

## Usage Examples

### Basic Commands
```python
# Play a song
/play query:your song name

# Add to play next
/playnext query:priority song

# Interactive search
/search query:search term provider:youtube

# Show queue
/queue

# Skip current song
/skip

# Stop and clear
/stop

# Show current song
/now

# Show statistics
/stats
```

### Advanced Features
- **Connection Limits**: Automatically prevents overload
- **Performance Metrics**: Real-time monitoring
- **Error Recovery**: Automatic retry and fallback
- **Resource Cleanup**: Proper cleanup on disconnect

## Migration Notes

### For Developers
1. Import paths have changed:
   ```python
   # Old
   from cogs.music.core.song import Song, SongMeta
   from cogs.music.manager import PlayerManager
   
   # New
   from cogs.music.core.models import Song, SongMeta
   from cogs.music.audio_controller import AudioController
   ```

2. API changes:
   ```python
   # Old
   manager = PlayerManager()
   controller = manager.get_controller(guild_id)
   
   # New
   controller = get_or_create_controller(guild_id)
   ```

### For Users
- All commands remain the same
- Better performance and reliability
- Improved error messages
- New statistics command

## Configuration

The system uses the same configuration as before but with additional options:

```env
# Connection limits
MAX_VOICE_CONNECTIONS=50

# Performance settings
SONG_PREPARATION_LIMIT=3
VOICE_TIMEOUT=300

# Monitoring
ENABLE_METRICS=true
```

## Monitoring

The new system includes comprehensive monitoring:

- **Per-Guild Metrics**: Songs played, errors, session time
- **Global Metrics**: Active controllers, peak usage, connection limits
- **Performance Stats**: Preparation times, queue efficiency
- **Health Checks**: Automatic detection of issues

## Future Enhancements

1. **Database Integration**: Persistent playlists and favorites
2. **Load Balancing**: Multiple bot instances
3. **Advanced Caching**: Redis-based song caching
4. **Analytics**: Detailed usage analytics
5. **Auto-scaling**: Dynamic connection limits based on load

## Troubleshooting

### Common Issues
1. **Import Errors**: Update import paths to new structure
2. **Missing Dependencies**: Ensure all required packages are installed
3. **Connection Limits**: Check if maximum connections reached
4. **Performance Issues**: Monitor metrics via `/stats` command

### Debug Commands
```python
# Check controller status
controller = get_or_create_controller(guild_id)
metrics = controller.get_metrics()

# Check global status
manager = get_audio_manager()
global_metrics = manager.get_global_metrics()
```

This restructured architecture provides a solid foundation for future enhancements while maintaining backward compatibility and improving overall system performance. 
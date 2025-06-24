import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Callable, Awaitable
from concurrent.futures import ThreadPoolExecutor
from enum import Enum

from cogs.music.core.song import SongMeta, Song, createSong
from discord.ext import commands

_logger = logging.getLogger(__name__)


class WorkerState(Enum):
    """Worker states for track resolution"""
    IDLE = "idle"
    WORKING = "working"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass
class TrackResolutionTask:
    """Individual track resolution task"""
    song_meta: SongMeta
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 3
    created_at: float = 0.0
    
    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()


@dataclass
class ResolutionResult:
    """Result of track resolution"""
    task: TrackResolutionTask
    song: Optional[Song]
    success: bool
    error: Optional[Exception] = None
    resolution_time: float = 0.0


class TrackResolverWorker:
    """
    High-performance worker for resolving track metadata to playable songs.
    
    Features:
    - Concurrent processing with configurable workers
    - Rate limiting to prevent service overload
    - Automatic retry logic with exponential backoff
    - Priority-based task scheduling
    - Service-specific optimization
    """
    
    def __init__(self, 
                 max_workers: int = 4,
                 rate_limit_delay: float = 0.2,
                 batch_size: int = 5):
        self.max_workers = max_workers
        self.rate_limit_delay = rate_limit_delay
        self.batch_size = batch_size
        
        # Worker state
        self.state = WorkerState.IDLE
        self._task_queue: asyncio.Queue[TrackResolutionTask] = asyncio.Queue()
        self._result_queue: asyncio.Queue[ResolutionResult] = asyncio.Queue()
        
        # Worker management
        self._workers: List[asyncio.Task] = []
        self._active_tasks: Set[asyncio.Task] = set()
        self._shutdown_event = asyncio.Event()
        
        # Performance tracking
        self._processed_count = 0
        self._failed_count = 0
        self._start_time: Optional[float] = None
        
        # Rate limiting
        self._last_request_time: Dict[str, float] = {}
        self._semaphore = asyncio.Semaphore(max_workers)
        
        # Callbacks
        self._result_callback: Optional[Callable[[ResolutionResult], Awaitable[None]]] = None
        self._batch_callback: Optional[Callable[[List[ResolutionResult]], Awaitable[None]]] = None
    
    def set_result_callback(self, callback: Callable[[ResolutionResult], Awaitable[None]]) -> None:
        """Set callback for individual track resolution results"""
        self._result_callback = callback
    
    def set_batch_callback(self, callback: Callable[[List[ResolutionResult]], Awaitable[None]]) -> None:
        """Set callback for batch resolution results"""
        self._batch_callback = callback
    
    async def start_workers(self) -> None:
        """Start the worker pool"""
        if self.state != WorkerState.IDLE:
            return
        
        self.state = WorkerState.WORKING
        self._start_time = time.time()
        self._shutdown_event.clear()
        
        # Start worker tasks
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker_loop(f"worker-{i}"))
            self._workers.append(worker)
        
        # Start result processor
        result_processor = asyncio.create_task(self._result_processor())
        self._workers.append(result_processor)
        
        _logger.info(f"Started {self.max_workers} track resolver workers")
    
    async def stop_workers(self) -> None:
        """Stop all workers gracefully"""
        if self.state == WorkerState.STOPPED:
            return
        
        self.state = WorkerState.STOPPED
        self._shutdown_event.set()
        
        # Cancel all workers
        for worker in self._workers:
            if not worker.done():
                worker.cancel()
        
        # Wait for workers to finish
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        
        self._workers.clear()
        self._active_tasks.clear()
        
        _logger.info("Track resolver workers stopped")
    
    async def add_task(self, song_meta: SongMeta, priority: int = 0) -> None:
        """Add a track resolution task to the queue"""
        if self.state != WorkerState.WORKING:
            await self.start_workers()
        
        task = TrackResolutionTask(
            song_meta=song_meta,
            priority=priority,
            created_at=time.time()
        )
        
        await self._task_queue.put(task)
    
    async def add_batch(self, song_metas: List[SongMeta], priority: int = 0) -> None:
        """Add multiple tracks for resolution"""
        for song_meta in song_metas:
            await self.add_task(song_meta, priority)
    
    async def _worker_loop(self, worker_name: str) -> None:
        """Main worker loop for processing resolution tasks"""
        _logger.debug(f"Worker {worker_name} started")
        
        try:
            while not self._shutdown_event.is_set():
                try:
                    # Wait for task with timeout
                    task = await asyncio.wait_for(
                        self._task_queue.get(), 
                        timeout=1.0
                    )
                    
                    # Process the task
                    result = await self._resolve_track(task, worker_name)
                    
                    # Put result in queue
                    await self._result_queue.put(result)
                    
                    # Apply rate limiting
                    await asyncio.sleep(self.rate_limit_delay)
                    
                except asyncio.TimeoutError:
                    # No tasks available, continue
                    continue
                except Exception as e:
                    _logger.error(f"Worker {worker_name} error: {e}")
                    
        except asyncio.CancelledError:
            _logger.debug(f"Worker {worker_name} cancelled")
        except Exception as e:
            _logger.error(f"Worker {worker_name} crashed: {e}")
        finally:
            _logger.debug(f"Worker {worker_name} stopped")
    
    async def _resolve_track(self, task: TrackResolutionTask, worker_name: str) -> ResolutionResult:
        """Resolve a single track with error handling and retries"""
        start_time = time.time()
        
        try:
            # Apply service-specific rate limiting
            service_type = type(task.song_meta).__name__
            await self._apply_rate_limit(service_type)
            
            # Attempt to create the song
            async with self._semaphore:
                song = await createSong(task.song_meta)
            
            if song is None:
                raise Exception("Song creation returned None")
            
            self._processed_count += 1
            resolution_time = time.time() - start_time
            
            _logger.debug(f"Worker {worker_name} resolved: {task.song_meta.title} in {resolution_time:.2f}s")
            
            return ResolutionResult(
                task=task,
                song=song,
                success=True,
                resolution_time=resolution_time
            )
            
        except Exception as e:
            task.attempts += 1
            resolution_time = time.time() - start_time
            
            # Check if we should retry
            if task.attempts < task.max_attempts:
                # Exponential backoff
                delay = min(2 ** task.attempts, 10)
                await asyncio.sleep(delay)
                
                # Re-queue for retry
                await self._task_queue.put(task)
                
                _logger.debug(f"Retrying track resolution (attempt {task.attempts}): {task.song_meta.title}")
                
                # Return a retry indicator (not counted as failure yet)
                return ResolutionResult(
                    task=task,
                    song=None,
                    success=False,
                    error=e,
                    resolution_time=resolution_time
                )
            else:
                # Max attempts reached
                self._failed_count += 1
                
                _logger.warning(f"Failed to resolve track after {task.attempts} attempts: {task.song_meta.title} - {e}")
                
                return ResolutionResult(
                    task=task,
                    song=None,
                    success=False,
                    error=e,
                    resolution_time=resolution_time
                )
    
    async def _apply_rate_limit(self, service_type: str) -> None:
        """Apply service-specific rate limiting"""
        current_time = time.time()
        last_time = self._last_request_time.get(service_type, 0)
        
        # Different rate limits for different services
        rate_limits = {
            'YouTubeSongMeta': 0.1,  # YouTube can handle faster requests
            'SoundCloudSongMeta': 0.3,  # SoundCloud needs more spacing
            'SpotifySongMeta': 0.2,  # Spotify moderate rate limit
        }
        
        required_delay = rate_limits.get(service_type, self.rate_limit_delay)
        elapsed = current_time - last_time
        
        if elapsed < required_delay:
            await asyncio.sleep(required_delay - elapsed)
        
        self._last_request_time[service_type] = time.time()
    
    async def _result_processor(self) -> None:
        """Process resolution results and call callbacks"""
        batch_results: List[ResolutionResult] = []
        last_batch_time = time.time()
        
        try:
            while not self._shutdown_event.is_set():
                try:
                    # Wait for result with timeout
                    result = await asyncio.wait_for(
                        self._result_queue.get(),
                        timeout=1.0
                    )
                    
                    # Skip retry indicators (non-final results)
                    if not result.success and result.task.attempts < result.task.max_attempts:
                        continue
                    
                    # Process individual result
                    if self._result_callback:
                        try:
                            await self._result_callback(result)
                        except Exception as e:
                            _logger.error(f"Error in result callback: {e}")
                    
                    # Add to batch
                    batch_results.append(result)
                    
                    # Process batch if full or timeout
                    current_time = time.time()
                    batch_ready = (
                        len(batch_results) >= self.batch_size or
                        (batch_results and current_time - last_batch_time > 2.0)
                    )
                    
                    if batch_ready and self._batch_callback:
                        try:
                            await self._batch_callback(batch_results.copy())
                        except Exception as e:
                            _logger.error(f"Error in batch callback: {e}")
                        
                        batch_results.clear()
                        last_batch_time = current_time
                        
                except asyncio.TimeoutError:
                    # Process any remaining batch on timeout
                    if batch_results and self._batch_callback:
                        try:
                            await self._batch_callback(batch_results.copy())
                        except Exception as e:
                            _logger.error(f"Error in timeout batch callback: {e}")
                        
                        batch_results.clear()
                        last_batch_time = time.time()
                    continue
                    
        except asyncio.CancelledError:
            _logger.debug("Result processor cancelled")
        except Exception as e:
            _logger.error(f"Result processor error: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get worker performance statistics"""
        current_time = time.time()
        uptime = current_time - (self._start_time or current_time)
        
        return {
            'state': self.state.value,
            'processed_count': self._processed_count,
            'failed_count': self._failed_count,
            'success_rate': self._processed_count / max(self._processed_count + self._failed_count, 1),
            'queue_size': self._task_queue.qsize(),
            'active_workers': len([w for w in self._workers if not w.done()]),
            'uptime_seconds': uptime,
            'throughput_per_minute': (self._processed_count / max(uptime / 60, 0.01))
        }
    
    def is_busy(self) -> bool:
        """Check if the worker is currently processing tasks"""
        return self.state == WorkerState.WORKING and self._task_queue.qsize() > 0
    
    async def wait_until_idle(self, timeout: float = 30.0) -> bool:
        """Wait until all tasks are processed or timeout"""
        start_time = time.time()
        
        while self.is_busy() and (time.time() - start_time) < timeout:
            await asyncio.sleep(0.1)
        
        return not self.is_busy() 
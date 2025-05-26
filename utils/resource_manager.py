"""
Resource Manager for Discord Bot
Handles memory, connections, and resource cleanup
"""

import asyncio
import logging
import weakref
import gc
import psutil
import os
from typing import Dict, Any, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta

_log = logging.getLogger(__name__)


@dataclass
class ResourceStats:
    """Resource usage statistics"""

    memory_usage: float = 0.0  # MB
    cpu_usage: float = 0.0  # %
    active_tasks: int = 0
    voice_connections: int = 0
    timestamp: datetime = field(default_factory=datetime.now)


class ResourceManager:
    """Manages bot resources and provides monitoring/cleanup capabilities"""

    def __init__(self):
        self._active_tasks: Set[asyncio.Task] = set()
        self._voice_clients: weakref.WeakSet = weakref.WeakSet()
        self._aiohttp_sessions: Set = set()
        self._cleanup_interval = 300  # 5 minutes
        self._cleanup_task: Optional[asyncio.Task] = None
        self._stats_history: list = []
        self._max_history = 100

    async def start_monitoring(self):
        """Start resource monitoring and cleanup"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._monitoring_loop())
            _log.info("Resource monitoring started")

    async def stop_monitoring(self):
        """Stop resource monitoring"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            _log.info("Resource monitoring stopped")

    async def _monitoring_loop(self):
        """Main monitoring loop"""
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_resources()
                self._collect_stats()
                self._cleanup_stats_history()
            except asyncio.CancelledError:
                break
            except Exception as e:
                _log.error(f"Error in monitoring loop: {e}")

    def register_task(self, task: asyncio.Task, name: str = None) -> asyncio.Task:
        """Register a task for monitoring"""
        self._active_tasks.add(task)
        task.add_done_callback(lambda t: self._active_tasks.discard(t))
        if name:
            task.set_name(name)
        return task

    def register_voice_client(self, voice_client):
        """Register a voice client for monitoring"""
        self._voice_clients.add(voice_client)

    def register_aiohttp_session(self, session):
        """Register an aiohttp session for monitoring"""
        self._aiohttp_sessions.add(session)

    def unregister_aiohttp_session(self, session):
        """Unregister an aiohttp session"""
        self._aiohttp_sessions.discard(session)

    async def _cleanup_resources(self):
        """Perform resource cleanup"""
        # Cleanup completed tasks
        completed_tasks = [task for task in self._active_tasks if task.done()]
        for task in completed_tasks:
            self._active_tasks.discard(task)

        # Cleanup closed aiohttp sessions
        closed_sessions = [
            session for session in self._aiohttp_sessions if session.closed
        ]
        for session in closed_sessions:
            self._aiohttp_sessions.discard(session)

        # Force garbage collection
        collected = gc.collect()
        if collected > 0:
            _log.debug(f"Garbage collector freed {collected} objects")

    def _collect_stats(self):
        """Collect current resource statistics"""
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            cpu_percent = process.cpu_percent()

            stats = ResourceStats(
                memory_usage=memory_mb,
                cpu_usage=cpu_percent,
                active_tasks=len(self._active_tasks),
                voice_connections=len(self._voice_clients),
            )

            self._stats_history.append(stats)

        except Exception as e:
            _log.error(f"Error collecting stats: {e}")

    def _cleanup_stats_history(self):
        """Keep stats history within limits"""
        if len(self._stats_history) > self._max_history:
            self._stats_history = self._stats_history[-self._max_history :]

    def get_current_stats(self) -> ResourceStats:
        """Get current resource statistics"""
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            cpu_percent = process.cpu_percent()

            return ResourceStats(
                memory_usage=memory_mb,
                cpu_usage=cpu_percent,
                active_tasks=len(self._active_tasks),
                voice_connections=len(self._voice_clients),
            )
        except Exception as e:
            _log.error(f"Error getting current stats: {e}")
            return ResourceStats()

    def get_stats_summary(self) -> Dict[str, Any]:
        """Get a summary of resource statistics"""
        if not self._stats_history:
            current = self.get_current_stats()
            return {
                "current_memory_mb": current.memory_usage,
                "current_cpu_percent": current.cpu_usage,
                "active_tasks": current.active_tasks,
                "voice_connections": current.voice_connections,
                "average_memory_mb": current.memory_usage,
                "average_cpu_percent": current.cpu_usage,
                "peak_memory_mb": current.memory_usage,
                "data_points": 1,
            }

        recent_stats = self._stats_history[-50:]  # Last 50 data points

        avg_memory = sum(s.memory_usage for s in recent_stats) / len(recent_stats)
        avg_cpu = sum(s.cpu_usage for s in recent_stats) / len(recent_stats)
        peak_memory = max(s.memory_usage for s in recent_stats)

        current = self.get_current_stats()

        return {
            "current_memory_mb": current.memory_usage,
            "current_cpu_percent": current.cpu_usage,
            "active_tasks": current.active_tasks,
            "voice_connections": current.voice_connections,
            "average_memory_mb": avg_memory,
            "average_cpu_percent": avg_cpu,
            "peak_memory_mb": peak_memory,
            "data_points": len(recent_stats),
        }

    async def force_cleanup(self):
        """Force immediate resource cleanup"""
        await self._cleanup_resources()

        # Cancel all non-essential tasks
        for task in list(self._active_tasks):
            if not task.done() and task.get_name() not in ["monitoring", "bot_main"]:
                task.cancel()

        # Close all aiohttp sessions
        for session in list(self._aiohttp_sessions):
            if not session.closed:
                await session.close()

        # Force garbage collection
        gc.collect()

        _log.info("Forced resource cleanup completed")

    async def check_memory_threshold(self, threshold_mb: float = 500.0) -> bool:
        """Check if memory usage exceeds threshold"""
        current_stats = self.get_current_stats()
        if current_stats.memory_usage > threshold_mb:
            _log.warning(
                f"Memory usage ({current_stats.memory_usage:.1f}MB) exceeds threshold ({threshold_mb}MB)"
            )
            await self.force_cleanup()
            return True
        return False


# Global resource manager instance
resource_manager = ResourceManager()


# Decorators for automatic resource management
def managed_task(name: str = None):
    """Decorator to automatically register tasks with resource manager"""

    def decorator(func):
        async def wrapper(*args, **kwargs):
            task = asyncio.create_task(func(*args, **kwargs))
            return resource_manager.register_task(task, name or func.__name__)

        return wrapper

    return decorator


def managed_aiohttp_session():
    """Context manager for aiohttp sessions with automatic cleanup"""

    class SessionManager:
        def __init__(self):
            self.session = None

        async def __aenter__(self):
            import aiohttp

            self.session = aiohttp.ClientSession()
            resource_manager.register_aiohttp_session(self.session)
            return self.session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            if self.session and not self.session.closed:
                await self.session.close()
                resource_manager.unregister_aiohttp_session(self.session)

    return SessionManager()

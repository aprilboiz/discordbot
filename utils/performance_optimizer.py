"""
Performance Optimizer for Discord Music Bot
Provides advanced caching, connection pooling, and resource optimization
"""

import asyncio
import time
import logging
import weakref
from typing import Dict, Any, Optional, List, Callable, TypeVar, Generic
from dataclasses import dataclass, field
from collections import defaultdict, OrderedDict
import psutil
import gc
from functools import wraps
import hashlib
import json

_log = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class PerformanceMetrics:
    """Performance metrics tracking"""
    
    cache_hits: int = 0
    cache_misses: int = 0
    cache_evictions: int = 0
    connection_pool_hits: int = 0
    connection_pool_misses: int = 0
    memory_usage_mb: float = 0.0
    cpu_usage_percent: float = 0.0
    active_connections: int = 0
    response_times: List[float] = field(default_factory=list)
    last_gc_time: float = 0.0
    gc_collections: int = 0


class AdvancedLRUCache(Generic[T]):
    """Advanced LRU Cache with TTL, size limits, and performance tracking"""
    
    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._access_times: Dict[str, float] = {}
        self._metrics = PerformanceMetrics()
        
    def _is_expired(self, key: str) -> bool:
        """Check if cache entry is expired"""
        if key not in self._cache:
            return True
            
        entry = self._cache[key]
        return time.time() > entry['expires_at']
    
    def _evict_expired(self) -> None:
        """Remove expired entries"""
        current_time = time.time()
        expired_keys = [
            key for key, entry in self._cache.items()
            if current_time > entry['expires_at']
        ]
        
        for key in expired_keys:
            self._remove_entry(key)
            self._metrics.cache_evictions += 1
    
    def _evict_lru(self) -> None:
        """Remove least recently used entries"""
        while len(self._cache) >= self.max_size:
            key, _ = self._cache.popitem(last=False)
            self._access_times.pop(key, None)
            self._metrics.cache_evictions += 1
    
    def _remove_entry(self, key: str) -> None:
        """Remove specific cache entry"""
        self._cache.pop(key, None)
        self._access_times.pop(key, None)
    
    def get(self, key: str) -> Optional[T]:
        """Get value from cache"""
        self._evict_expired()
        
        if key in self._cache and not self._is_expired(key):
            # Move to end (most recently used)
            entry = self._cache[key]
            self._cache.move_to_end(key)
            self._access_times[key] = time.time()
            self._metrics.cache_hits += 1
            return entry['value']
        
        self._metrics.cache_misses += 1
        return None
    
    def set(self, key: str, value: T, ttl: Optional[int] = None) -> None:
        """Set value in cache"""
        ttl = ttl or self.default_ttl
        
        # Remove existing entry
        if key in self._cache:
            self._remove_entry(key)
        
        # Evict expired and LRU entries
        self._evict_expired()
        self._evict_lru()
        
        # Add new entry
        self._cache[key] = {
            'value': value,
            'expires_at': time.time() + ttl,
            'created_at': time.time(),
            'access_count': 0
        }
        self._access_times[key] = time.time()
    
    def delete(self, key: str) -> bool:
        """Delete entry from cache"""
        if key in self._cache:
            self._remove_entry(key)
            return True
        return False
    
    def clear(self) -> None:
        """Clear all cache entries"""
        self._cache.clear()
        self._access_times.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        self._evict_expired()
        
        total_requests = self._metrics.cache_hits + self._metrics.cache_misses
        hit_rate = (self._metrics.cache_hits / total_requests * 100) if total_requests > 0 else 0
        
        return {
            'size': len(self._cache),
            'max_size': self.max_size,
            'hit_rate_percent': round(hit_rate, 2),
            'hits': self._metrics.cache_hits,
            'misses': self._metrics.cache_misses,
            'evictions': self._metrics.cache_evictions,
            'memory_usage_estimate': len(self._cache) * 100  # Rough estimate
        }


class ConnectionPool:
    """Advanced connection pool with health checking and load balancing"""
    
    def __init__(self, max_connections: int = 100, health_check_interval: int = 60):
        self.max_connections = max_connections
        self.health_check_interval = health_check_interval
        self._connections: Dict[str, List[Any]] = defaultdict(list)
        self._connection_health: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self._metrics = PerformanceMetrics()
        self._health_check_task: Optional[asyncio.Task] = None
        
    async def start_health_checks(self) -> None:
        """Start periodic health checks"""
        if self._health_check_task is None:
            self._health_check_task = asyncio.create_task(self._health_check_loop())
    
    async def stop_health_checks(self) -> None:
        """Stop health checks"""
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
    
    async def _health_check_loop(self) -> None:
        """Periodic health check loop"""
        while True:
            try:
                await asyncio.sleep(self.health_check_interval)
                await self._check_connection_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                _log.error(f"Error in health check loop: {e}")
    
    async def _check_connection_health(self) -> None:
        """Check health of all connections"""
        for pool_name, connections in self._connections.items():
            healthy_connections = []
            
            for conn in connections:
                if await self._is_connection_healthy(conn):
                    healthy_connections.append(conn)
                else:
                    await self._close_connection(conn)
            
            self._connections[pool_name] = healthy_connections
    
    async def _is_connection_healthy(self, connection: Any) -> bool:
        """Check if a connection is healthy"""
        try:
            # Basic health check - can be overridden
            if hasattr(connection, 'closed'):
                return not connection.closed
            return True
        except Exception:
            return False
    
    async def _close_connection(self, connection: Any) -> None:
        """Close a connection safely"""
        try:
            if hasattr(connection, 'close'):
                await connection.close()
        except Exception as e:
            _log.warning(f"Error closing connection: {e}")
    
    async def get_connection(self, pool_name: str) -> Optional[Any]:
        """Get a connection from the pool"""
        connections = self._connections[pool_name]
        
        if connections:
            self._metrics.connection_pool_hits += 1
            return connections.pop()
        
        self._metrics.connection_pool_misses += 1
        return None
    
    async def return_connection(self, pool_name: str, connection: Any) -> None:
        """Return a connection to the pool"""
        if len(self._connections[pool_name]) < self.max_connections:
            if await self._is_connection_healthy(connection):
                self._connections[pool_name].append(connection)
            else:
                await self._close_connection(connection)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics"""
        total_connections = sum(len(conns) for conns in self._connections.values())
        
        return {
            'total_connections': total_connections,
            'max_connections': self.max_connections,
            'pool_utilization_percent': round(total_connections / self.max_connections * 100, 2),
            'pool_hits': self._metrics.connection_pool_hits,
            'pool_misses': self._metrics.connection_pool_misses,
            'pools': {name: len(conns) for name, conns in self._connections.items()}
        }


class MemoryOptimizer:
    """Memory optimization and garbage collection management"""
    
    def __init__(self, gc_threshold: float = 100.0, gc_interval: int = 300):
        self.gc_threshold_mb = gc_threshold
        self.gc_interval = gc_interval
        self._last_gc = time.time()
        self._gc_task: Optional[asyncio.Task] = None
        self._weak_refs: weakref.WeakSet = weakref.WeakSet()
        
    async def start_optimization(self) -> None:
        """Start memory optimization"""
        if self._gc_task is None:
            self._gc_task = asyncio.create_task(self._optimization_loop())
    
    async def stop_optimization(self) -> None:
        """Stop memory optimization"""
        if self._gc_task:
            self._gc_task.cancel()
            try:
                await self._gc_task
            except asyncio.CancelledError:
                pass
    
    async def _optimization_loop(self) -> None:
        """Main optimization loop"""
        while True:
            try:
                await asyncio.sleep(self.gc_interval)
                await self._optimize_memory()
            except asyncio.CancelledError:
                break
            except Exception as e:
                _log.error(f"Error in memory optimization: {e}")
    
    async def _optimize_memory(self) -> None:
        """Perform memory optimization"""
        current_memory = self._get_memory_usage()
        
        if current_memory > self.gc_threshold_mb:
            _log.info(f"Memory usage ({current_memory:.1f}MB) above threshold, running GC")
            
            # Force garbage collection
            collected = gc.collect()
            
            new_memory = self._get_memory_usage()
            freed = current_memory - new_memory
            
            _log.info(f"GC freed {freed:.1f}MB, collected {collected} objects")
            self._last_gc = time.time()
    
    def _get_memory_usage(self) -> float:
        """Get current memory usage in MB"""
        try:
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024
        except Exception:
            return 0.0
    
    def register_object(self, obj: Any) -> None:
        """Register object for weak reference tracking"""
        self._weak_refs.add(obj)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get memory optimization statistics"""
        return {
            'current_memory_mb': self._get_memory_usage(),
            'gc_threshold_mb': self.gc_threshold_mb,
            'last_gc_time': self._last_gc,
            'tracked_objects': len(self._weak_refs),
            'gc_interval': self.gc_interval
        }


class PerformanceOptimizer:
    """Main performance optimizer coordinating all optimization strategies"""
    
    def __init__(self):
        self.cache = AdvancedLRUCache(max_size=1000, default_ttl=300)
        self.connection_pool = ConnectionPool(max_connections=100)
        self.memory_optimizer = MemoryOptimizer(gc_threshold=500.0)
        self._metrics = PerformanceMetrics()
        self._optimization_active = False
        
    async def start_optimization(self) -> None:
        """Start all optimization processes"""
        if not self._optimization_active:
            self._optimization_active = True
            await self.connection_pool.start_health_checks()
            await self.memory_optimizer.start_optimization()
            _log.info("Performance optimization started")
    
    async def stop_optimization(self) -> None:
        """Stop all optimization processes"""
        if self._optimization_active:
            self._optimization_active = False
            await self.connection_pool.stop_health_checks()
            await self.memory_optimizer.stop_optimization()
            _log.info("Performance optimization stopped")
    
    def get_comprehensive_stats(self) -> Dict[str, Any]:
        """Get comprehensive performance statistics"""
        return {
            'cache': self.cache.get_stats(),
            'connection_pool': self.connection_pool.get_stats(),
            'memory': self.memory_optimizer.get_stats(),
            'optimization_active': self._optimization_active,
            'timestamp': time.time()
        }


# Global optimizer instance
performance_optimizer = PerformanceOptimizer()


def optimized_cache(ttl: int = 300, key_prefix: str = ""):
    """Decorator for caching function results with performance optimization"""
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate cache key
            key_data = {
                'func': func.__name__,
                'args': str(args),
                'kwargs': str(sorted(kwargs.items())),
                'prefix': key_prefix
            }
            cache_key = hashlib.md5(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
            
            # Try to get from cache
            cached_result = performance_optimizer.cache.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            # Execute function and cache result
            start_time = time.time()
            result = await func(*args, **kwargs)
            execution_time = time.time() - start_time
            
            # Cache the result
            performance_optimizer.cache.set(cache_key, result, ttl)
            
            # Track performance
            performance_optimizer._metrics.response_times.append(execution_time)
            if len(performance_optimizer._metrics.response_times) > 100:
                performance_optimizer._metrics.response_times.pop(0)
            
            return result
        
        return wrapper
    return decorator


def memory_efficient(func: Callable) -> Callable:
    """Decorator for memory-efficient function execution"""
    
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Register objects for tracking
        for arg in args:
            if hasattr(arg, '__dict__'):
                performance_optimizer.memory_optimizer.register_object(arg)
        
        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            # Hint for garbage collection
            if len(performance_optimizer._metrics.response_times) % 10 == 0:
                gc.collect()
    
    return wrapper


async def init_performance_optimization() -> None:
    """Initialize performance optimization"""
    await performance_optimizer.start_optimization()


async def cleanup_performance_optimization() -> None:
    """Cleanup performance optimization"""
    await performance_optimizer.stop_optimization()


def get_performance_stats() -> Dict[str, Any]:
    """Get current performance statistics"""
    return performance_optimizer.get_comprehensive_stats()
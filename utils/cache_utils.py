"""
Caching and Rate Limiting System for Discord Bot
Provides memory-efficient caching and rate limiting for scalability
"""

import asyncio
import time
import hashlib
import json
import logging
from typing import Any, Dict, Optional, Callable, Union
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import defaultdict, OrderedDict
from functools import wraps
import weakref

_log = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Cache entry with TTL and metadata"""

    value: Any
    expires_at: float
    hit_count: int = 0
    created_at: float = field(default_factory=time.time)
    size_bytes: int = 0

    def is_expired(self) -> bool:
        """Check if cache entry is expired"""
        return time.time() > self.expires_at

    def access(self) -> Any:
        """Access cache entry and update hit count"""
        self.hit_count += 1
        return self.value


@dataclass
class RateLimitBucket:
    """Rate limiting bucket"""

    remaining: int
    reset_time: float
    total: int

    def is_available(self) -> bool:
        """Check if rate limit allows request"""
        if time.time() >= self.reset_time:
            self.remaining = self.total
            self.reset_time = time.time() + 60  # Reset every minute

        return self.remaining > 0

    def consume(self) -> bool:
        """Consume one request from bucket"""
        if self.is_available():
            self.remaining -= 1
            return True
        return False


class LRUCache:
    """LRU Cache with TTL and size limits"""

    def __init__(self, max_size: int = 1000, default_ttl: int = 3600):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._total_size = 0
        self._hits = 0
        self._misses = 0

    def _calculate_size(self, value: Any) -> int:
        """Estimate memory size of cached value"""
        try:
            if isinstance(value, (str, bytes)):
                return len(value)
            elif isinstance(value, (dict, list)):
                return len(json.dumps(value, default=str))
            else:
                return len(str(value))
        except:
            return 100  # Default size estimate

    def _evict_expired(self):
        """Remove expired entries"""
        now = time.time()
        expired_keys = [key for key, entry in self._cache.items() if entry.is_expired()]

        for key in expired_keys:
            self._remove_entry(key)

    def _evict_lru(self):
        """Remove least recently used entries to make space"""
        while len(self._cache) >= self.max_size:
            key, entry = self._cache.popitem(last=False)
            self._total_size -= entry.size_bytes
            _log.debug(f"Evicted LRU cache entry: {key}")

    def _remove_entry(self, key: str):
        """Remove specific cache entry"""
        if key in self._cache:
            entry = self._cache.pop(key)
            self._total_size -= entry.size_bytes

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        self._evict_expired()

        if key in self._cache:
            entry = self._cache[key]
            if not entry.is_expired():
                # Move to end (most recently used)
                self._cache.move_to_end(key)
                self._hits += 1
                return entry.access()
            else:
                self._remove_entry(key)

        self._misses += 1
        return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value in cache"""
        ttl = ttl or self.default_ttl
        size = self._calculate_size(value)

        # Remove existing entry if present
        if key in self._cache:
            self._remove_entry(key)

        # Evict expired and LRU entries
        self._evict_expired()
        self._evict_lru()

        # Add new entry
        entry = CacheEntry(value=value, expires_at=time.time() + ttl, size_bytes=size)

        self._cache[key] = entry
        self._total_size += size

        _log.debug(f"Cached entry: {key} (size: {size} bytes, TTL: {ttl}s)")

    def delete(self, key: str) -> bool:
        """Delete entry from cache"""
        if key in self._cache:
            self._remove_entry(key)
            return True
        return False

    def clear(self):
        """Clear all cache entries"""
        self._cache.clear()
        self._total_size = 0
        _log.info("Cache cleared")

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        self._evict_expired()

        total_requests = self._hits + self._misses
        hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0

        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "total_size_bytes": self._total_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_percent": round(hit_rate, 2),
            "entries": list(self._cache.keys())[:10],  # First 10 keys for debugging
        }


class RateLimiter:
    """Rate limiter with per-user and global limits"""

    def __init__(self):
        self._user_buckets: Dict[int, RateLimitBucket] = {}
        self._global_bucket = RateLimitBucket(
            remaining=1000, reset_time=time.time() + 60, total=1000
        )
        self._command_buckets: Dict[str, Dict[int, RateLimitBucket]] = defaultdict(dict)

    def check_rate_limit(
        self,
        user_id: int,
        command: str = None,
        per_user_limit: int = 60,
        per_command_limit: int = 10,
    ) -> bool:
        """Check if user is rate limited"""

        # Check global rate limit
        if not self._global_bucket.consume():
            _log.warning("Global rate limit exceeded")
            return False

        # Check per-user rate limit
        if user_id not in self._user_buckets:
            self._user_buckets[user_id] = RateLimitBucket(
                remaining=per_user_limit,
                reset_time=time.time() + 60,
                total=per_user_limit,
            )

        user_bucket = self._user_buckets[user_id]
        if not user_bucket.consume():
            _log.warning(f"User {user_id} rate limited")
            return False

        # Check per-command rate limit if specified
        if command:
            if user_id not in self._command_buckets[command]:
                self._command_buckets[command][user_id] = RateLimitBucket(
                    remaining=per_command_limit,
                    reset_time=time.time() + 60,
                    total=per_command_limit,
                )

            command_bucket = self._command_buckets[command][user_id]
            if not command_bucket.consume():
                _log.warning(f"User {user_id} rate limited for command {command}")
                return False

        return True

    def get_remaining_requests(
        self, user_id: int, command: str = None
    ) -> Dict[str, int]:
        """Get remaining requests for user"""
        result = {"global": self._global_bucket.remaining, "user": 0, "command": 0}

        if user_id in self._user_buckets:
            result["user"] = self._user_buckets[user_id].remaining

        if command and user_id in self._command_buckets[command]:
            result["command"] = self._command_buckets[command][user_id].remaining

        return result


class CacheManager:
    """Central cache management for the bot"""

    def __init__(self):
        self.api_cache = LRUCache(max_size=500, default_ttl=300)  # API responses
        self.user_cache = LRUCache(max_size=1000, default_ttl=1800)  # User data
        self.guild_cache = LRUCache(max_size=100, default_ttl=3600)  # Guild data
        self.command_cache = LRUCache(max_size=200, default_ttl=600)  # Command results
        self.rate_limiter = RateLimiter()

    def get_cache(self, cache_type: str) -> LRUCache:
        """Get specific cache by type"""
        caches = {
            "api": self.api_cache,
            "user": self.user_cache,
            "guild": self.guild_cache,
            "command": self.command_cache,
        }
        return caches.get(cache_type, self.api_cache)

    def clear_all_caches(self):
        """Clear all caches"""
        for cache in [
            self.api_cache,
            self.user_cache,
            self.guild_cache,
            self.command_cache,
        ]:
            cache.clear()
        _log.info("All caches cleared")

    def get_all_stats(self) -> Dict[str, Any]:
        """Get statistics for all caches"""
        return {
            "api_cache": self.api_cache.get_stats(),
            "user_cache": self.user_cache.get_stats(),
            "guild_cache": self.guild_cache.get_stats(),
            "command_cache": self.command_cache.get_stats(),
        }


# Global cache manager
cache_manager = CacheManager()


def cached(cache_type: str = "api", ttl: int = 300, key_prefix: str = ""):
    """Decorator for caching function results"""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate cache key
            key_parts = [key_prefix, func.__name__]

            # Add args to key (exclude self/cls)
            if args:
                start_idx = 1 if hasattr(args[0], func.__name__) else 0
                key_parts.extend(str(arg) for arg in args[start_idx:])

            # Add sorted kwargs to key
            if kwargs:
                key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))

            cache_key = hashlib.md5(":".join(key_parts).encode()).hexdigest()

            # Try to get from cache
            cache = cache_manager.get_cache(cache_type)
            cached_result = cache.get(cache_key)

            if cached_result is not None:
                _log.debug(f"Cache hit for {func.__name__}: {cache_key}")
                return cached_result

            # Execute function and cache result
            _log.debug(f"Cache miss for {func.__name__}: {cache_key}")
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            # Cache successful results only
            if result is not None:
                cache.set(cache_key, result, ttl)

            return result

        return wrapper

    return decorator


def rate_limited(per_user_limit: int = 60, per_command_limit: int = 10):
    """Decorator for rate limiting commands"""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract user_id from context or interaction
            user_id = None
            command_name = func.__name__

            # Try to get user_id from different sources
            for arg in args:
                if hasattr(arg, "author") and hasattr(arg.author, "id"):
                    user_id = arg.author.id
                    break
                elif hasattr(arg, "user") and hasattr(arg.user, "id"):
                    user_id = arg.user.id
                    break

            if user_id is None:
                _log.warning(
                    f"Could not extract user_id for rate limiting in {command_name}"
                )
                # Allow execution without rate limiting
                return await func(*args, **kwargs)

            # Check rate limit
            if not cache_manager.rate_limiter.check_rate_limit(
                user_id, command_name, per_user_limit, per_command_limit
            ):
                _log.warning(
                    f"Rate limit exceeded for user {user_id} on command {command_name}"
                )
                # You might want to send a rate limit message here
                return None

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def get_cache_stats() -> Dict[str, Any]:
    """Get all cache statistics"""
    return cache_manager.get_all_stats()


def clear_cache(cache_type: str = None):
    """Clear specific cache or all caches"""
    if cache_type:
        cache = cache_manager.get_cache(cache_type)
        cache.clear()
    else:
        cache_manager.clear_all_caches()

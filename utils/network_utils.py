"""
Network utilities for Discord bot
Provides retry mechanisms, connection pooling, and network error handling
"""

import asyncio
import logging
import aiohttp
import backoff
from typing import Optional, Dict, Any, Callable
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

_log = logging.getLogger(__name__)


class NetworkManager:
    """Manages network connections with retry logic and connection pooling"""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._connector: Optional[aiohttp.TCPConnector] = None
        self._timeout = aiohttp.ClientTimeout(total=30, connect=10)
        self._retry_attempts = 3
        self._backoff_factor = 2

    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session with connection pooling"""
        if self._session is None or self._session.closed:
            self._connector = aiohttp.TCPConnector(
                limit=100,  # Total connection pool size
                limit_per_host=30,  # Connections per host
                ttl_dns_cache=300,  # DNS cache TTL
                use_dns_cache=True,
                keepalive_timeout=30,
                enable_cleanup_closed=True,
            )

            self._session = aiohttp.ClientSession(
                connector=self._connector,
                timeout=self._timeout,
                headers={
                    "User-Agent": "Discord Bot/1.0 (https://github.com/discord/discord-api-docs)"
                },
            )
            _log.info("Created new aiohttp session with connection pooling")

        return self._session

    async def close_session(self):
        """Close the aiohttp session and connector"""
        if self._session and not self._session.closed:
            await self._session.close()
            _log.info("Closed aiohttp session")

        if self._connector:
            await self._connector.close()
            _log.info("Closed aiohttp connector")

    @backoff.on_exception(
        backoff.expo,
        (aiohttp.ClientError, asyncio.TimeoutError),
        max_tries=3,
        max_time=60,
    )
    async def request_with_retry(
        self, method: str, url: str, **kwargs
    ) -> aiohttp.ClientResponse:
        """Make HTTP request with exponential backoff retry"""
        session = await self.get_session()

        try:
            async with session.request(method, url, **kwargs) as response:
                # Log failed requests for debugging
                if response.status >= 400:
                    _log.warning(f"HTTP {response.status} for {method} {url}")

                # Raise for status to trigger retry on 5xx errors
                if response.status >= 500:
                    response.raise_for_status()

                return response

        except aiohttp.ClientError as e:
            _log.error(f"Network error for {method} {url}: {e}")
            raise
        except asyncio.TimeoutError as e:
            _log.error(f"Timeout error for {method} {url}: {e}")
            raise

    async def get_json_with_retry(self, url: str, **kwargs) -> Dict[str, Any]:
        """GET request that returns JSON with retry logic"""
        async with await self.request_with_retry("GET", url, **kwargs) as response:
            response.raise_for_status()
            return await response.json()

    async def get_text_with_retry(self, url: str, **kwargs) -> str:
        """GET request that returns text with retry logic"""
        async with await self.request_with_retry("GET", url, **kwargs) as response:
            response.raise_for_status()
            return await response.text()


class DiscordConnectionManager:
    """Manages Discord connection with automatic reconnection"""

    def __init__(self, bot):
        self.bot = bot
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 5  # seconds
        self._last_disconnect = None

    async def handle_disconnect(self):
        """Handle Discord disconnection with retry logic"""
        self._last_disconnect = datetime.now()
        self._reconnect_attempts += 1

        if self._reconnect_attempts <= self._max_reconnect_attempts:
            delay = self._reconnect_delay * (2 ** (self._reconnect_attempts - 1))
            _log.warning(
                f"Discord disconnected. Attempt {self._reconnect_attempts}/{self._max_reconnect_attempts}. Retrying in {delay}s"
            )

            await asyncio.sleep(delay)

            try:
                await self.bot.connect()
                _log.info("Successfully reconnected to Discord")
                self._reconnect_attempts = 0
            except Exception as e:
                _log.error(f"Reconnection failed: {e}")
                if self._reconnect_attempts >= self._max_reconnect_attempts:
                    _log.critical(
                        "Max reconnection attempts reached. Bot may need manual restart."
                    )
        else:
            _log.critical("Max reconnection attempts exceeded")

    def reset_reconnect_counter(self):
        """Reset reconnection counter on successful connection"""
        self._reconnect_attempts = 0


# Global network manager instance
network_manager = NetworkManager()


@asynccontextmanager
async def managed_session():
    """Context manager for aiohttp session"""
    session = await network_manager.get_session()
    try:
        yield session
    finally:
        # Session cleanup is handled by NetworkManager
        pass


def on_network_error(func: Callable) -> Callable:
    """Decorator for handling network errors gracefully"""

    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            _log.error(f"Network error in {func.__name__}: {e}")
            # Return None or default value instead of raising
            return None
        except Exception as e:
            _log.error(f"Unexpected error in {func.__name__}: {e}")
            return None

    return wrapper

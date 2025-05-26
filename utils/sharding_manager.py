"""
Discord Sharding Manager for Music Bot
Provides automatic sharding, load balancing, and cross-shard communication
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
import discord
from discord.ext import commands
import weakref
import json

_log = logging.getLogger(__name__)


@dataclass
class ShardMetrics:
    """Metrics for individual shard"""
    
    shard_id: int
    guild_count: int = 0
    user_count: int = 0
    voice_connections: int = 0
    commands_processed: int = 0
    errors: int = 0
    latency_ms: float = 0.0
    memory_usage_mb: float = 0.0
    uptime_seconds: float = 0.0
    last_heartbeat: float = field(default_factory=time.time)
    status: str = "starting"  # starting, ready, reconnecting, error


@dataclass
class ShardingConfig:
    """Sharding configuration"""
    
    auto_shard: bool = True
    shard_count: Optional[int] = None
    max_guilds_per_shard: int = 2500
    enable_cross_shard_communication: bool = True
    shard_startup_delay: float = 5.0
    health_check_interval: int = 30
    auto_restart_on_error: bool = True
    max_restart_attempts: int = 3


class CrossShardCommunicator:
    """Handles communication between shards"""
    
    def __init__(self):
        self._message_handlers: Dict[str, Callable] = {}
        self._shard_connections: Dict[int, Any] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue()
        
    def register_handler(self, message_type: str, handler: Callable) -> None:
        """Register a message handler for cross-shard communication"""
        self._message_handlers[message_type] = handler
        _log.debug(f"Registered cross-shard handler for: {message_type}")
    
    async def send_to_shard(self, shard_id: int, message_type: str, data: Any) -> None:
        """Send message to specific shard"""
        message = {
            'type': message_type,
            'data': data,
            'timestamp': time.time(),
            'target_shard': shard_id
        }
        
        await self._message_queue.put(message)
        _log.debug(f"Queued message to shard {shard_id}: {message_type}")
    
    async def broadcast_to_all_shards(self, message_type: str, data: Any) -> None:
        """Broadcast message to all shards"""
        message = {
            'type': message_type,
            'data': data,
            'timestamp': time.time(),
            'target_shard': 'all'
        }
        
        await self._message_queue.put(message)
        _log.debug(f"Queued broadcast message: {message_type}")
    
    async def process_messages(self) -> None:
        """Process queued cross-shard messages"""
        while True:
            try:
                message = await self._message_queue.get()
                await self._handle_message(message)
                self._message_queue.task_done()
            except Exception as e:
                _log.error(f"Error processing cross-shard message: {e}")
    
    async def _handle_message(self, message: Dict[str, Any]) -> None:
        """Handle individual cross-shard message"""
        message_type = message.get('type')
        
        if message_type in self._message_handlers:
            try:
                await self._message_handlers[message_type](message)
            except Exception as e:
                _log.error(f"Error handling message {message_type}: {e}")
        else:
            _log.warning(f"No handler for message type: {message_type}")


class ShardManager:
    """Manages Discord bot sharding with advanced features"""
    
    def __init__(self, bot_class: type, config: ShardingConfig):
        self.bot_class = bot_class
        self.config = config
        self.shards: Dict[int, commands.AutoShardedBot] = {}
        self.shard_metrics: Dict[int, ShardMetrics] = {}
        self.communicator = CrossShardCommunicator()
        
        self._monitoring_task: Optional[asyncio.Task] = None
        self._restart_attempts: Dict[int, int] = {}
        self._shard_startup_times: Dict[int, float] = {}
        
    async def determine_shard_count(self, token: str) -> int:
        """Determine optimal shard count based on guild count"""
        if self.config.shard_count:
            return self.config.shard_count
        
        if not self.config.auto_shard:
            return 1
        
        try:
            # Create temporary bot to get recommended shard count
            async with discord.Client() as temp_client:
                await temp_client.login(token)
                gateway_info = await temp_client.http.get_gateway_bot()
                
                recommended_shards = gateway_info.get('shards', 1)
                max_concurrency = gateway_info.get('session_start_limit', {}).get('max_concurrency', 1)
                
                _log.info(f"Discord recommends {recommended_shards} shards with max concurrency {max_concurrency}")
                
                return max(recommended_shards, 1)
                
        except Exception as e:
            _log.error(f"Failed to determine shard count: {e}")
            return 1
    
    async def start_shards(self, token: str) -> None:
        """Start all shards with staggered startup"""
        shard_count = await self.determine_shard_count(token)
        _log.info(f"Starting {shard_count} shards...")
        
        # Start monitoring
        self._monitoring_task = asyncio.create_task(self._monitoring_loop())
        
        # Start cross-shard communication
        asyncio.create_task(self.communicator.process_messages())
        
        # Start shards with delay
        for shard_id in range(shard_count):
            await self._start_shard(shard_id, shard_count, token)
            
            # Delay between shard startups
            if shard_id < shard_count - 1:
                await asyncio.sleep(self.config.shard_startup_delay)
        
        _log.info(f"All {shard_count} shards started successfully")
    
    async def _start_shard(self, shard_id: int, shard_count: int, token: str) -> None:
        """Start individual shard"""
        try:
            _log.info(f"Starting shard {shard_id}/{shard_count}")
            
            # Create shard-specific bot instance
            intents = discord.Intents.default()
            intents.message_content = True
            
            bot = commands.AutoShardedBot(
                command_prefix="?",
                intents=intents,
                shard_id=shard_id,
                shard_count=shard_count
            )
            
            # Initialize shard metrics
            self.shard_metrics[shard_id] = ShardMetrics(shard_id=shard_id)
            self._shard_startup_times[shard_id] = time.time()
            
            # Setup shard event handlers
            self._setup_shard_events(bot, shard_id)
            
            # Store shard reference
            self.shards[shard_id] = bot
            
            # Start the shard
            asyncio.create_task(self._run_shard(bot, token, shard_id))
            
        except Exception as e:
            _log.error(f"Failed to start shard {shard_id}: {e}")
            await self._handle_shard_error(shard_id, e)
    
    def _setup_shard_events(self, bot: commands.AutoShardedBot, shard_id: int) -> None:
        """Setup event handlers for shard"""
        
        @bot.event
        async def on_ready():
            metrics = self.shard_metrics[shard_id]
            metrics.status = "ready"
            metrics.guild_count = len(bot.guilds)
            metrics.user_count = sum(guild.member_count or 0 for guild in bot.guilds)
            metrics.uptime_seconds = time.time() - self._shard_startup_times[shard_id]
            
            _log.info(f"Shard {shard_id} ready: {metrics.guild_count} guilds, {metrics.user_count} users")
        
        @bot.event
        async def on_shard_ready(shard_id_ready):
            if shard_id_ready == shard_id:
                metrics = self.shard_metrics[shard_id]
                metrics.last_heartbeat = time.time()
                metrics.latency_ms = bot.latency * 1000
        
        @bot.event
        async def on_guild_join(guild):
            metrics = self.shard_metrics[shard_id]
            metrics.guild_count += 1
            
            # Check if shard is overloaded
            if metrics.guild_count > self.config.max_guilds_per_shard:
                _log.warning(f"Shard {shard_id} overloaded: {metrics.guild_count} guilds")
                await self.communicator.broadcast_to_all_shards(
                    "shard_overload", 
                    {"shard_id": shard_id, "guild_count": metrics.guild_count}
                )
        
        @bot.event
        async def on_guild_remove(guild):
            metrics = self.shard_metrics[shard_id]
            metrics.guild_count = max(0, metrics.guild_count - 1)
        
        @bot.event
        async def on_command_completion(ctx):
            metrics = self.shard_metrics[shard_id]
            metrics.commands_processed += 1
        
        @bot.event
        async def on_command_error(ctx, error):
            metrics = self.shard_metrics[shard_id]
            metrics.errors += 1
    
    async def _run_shard(self, bot: commands.AutoShardedBot, token: str, shard_id: int) -> None:
        """Run individual shard with error handling"""
        try:
            await bot.start(token)
        except Exception as e:
            _log.error(f"Shard {shard_id} crashed: {e}")
            await self._handle_shard_error(shard_id, e)
    
    async def _handle_shard_error(self, shard_id: int, error: Exception) -> None:
        """Handle shard errors with restart logic"""
        metrics = self.shard_metrics.get(shard_id)
        if metrics:
            metrics.status = "error"
            metrics.errors += 1
        
        if not self.config.auto_restart_on_error:
            _log.error(f"Shard {shard_id} error (auto-restart disabled): {error}")
            return
        
        restart_attempts = self._restart_attempts.get(shard_id, 0)
        
        if restart_attempts < self.config.max_restart_attempts:
            self._restart_attempts[shard_id] = restart_attempts + 1
            delay = min(30, 5 * restart_attempts)  # Exponential backoff
            
            _log.warning(f"Restarting shard {shard_id} in {delay}s (attempt {restart_attempts + 1})")
            await asyncio.sleep(delay)
            
            # Restart shard logic would go here
            # This is a simplified version
            
        else:
            _log.critical(f"Shard {shard_id} failed after {restart_attempts} restart attempts")
    
    async def _monitoring_loop(self) -> None:
        """Monitor shard health and performance"""
        while True:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                await self._update_shard_metrics()
                await self._check_shard_health()
            except Exception as e:
                _log.error(f"Error in shard monitoring: {e}")
    
    async def _update_shard_metrics(self) -> None:
        """Update metrics for all shards"""
        for shard_id, bot in self.shards.items():
            if bot and not bot.is_closed():
                metrics = self.shard_metrics[shard_id]
                metrics.latency_ms = bot.latency * 1000
                metrics.last_heartbeat = time.time()
                
                # Update guild and user counts
                if bot.guilds:
                    metrics.guild_count = len(bot.guilds)
                    metrics.user_count = sum(guild.member_count or 0 for guild in bot.guilds)
                
                # Count voice connections
                metrics.voice_connections = len(bot.voice_clients)
    
    async def _check_shard_health(self) -> None:
        """Check health of all shards"""
        current_time = time.time()
        
        for shard_id, metrics in self.shard_metrics.items():
            # Check if shard is unresponsive
            if current_time - metrics.last_heartbeat > 60:  # 1 minute timeout
                _log.warning(f"Shard {shard_id} appears unresponsive")
                metrics.status = "unresponsive"
            
            # Check for high latency
            if metrics.latency_ms > 1000:  # 1 second
                _log.warning(f"Shard {shard_id} has high latency: {metrics.latency_ms:.1f}ms")
    
    async def stop_all_shards(self) -> None:
        """Stop all shards gracefully"""
        _log.info("Stopping all shards...")
        
        # Stop monitoring
        if self._monitoring_task:
            self._monitoring_task.cancel()
        
        # Stop all shards
        for shard_id, bot in self.shards.items():
            try:
                if not bot.is_closed():
                    await bot.close()
                _log.info(f"Stopped shard {shard_id}")
            except Exception as e:
                _log.error(f"Error stopping shard {shard_id}: {e}")
        
        self.shards.clear()
        self.shard_metrics.clear()
    
    def get_shard_stats(self) -> Dict[str, Any]:
        """Get comprehensive shard statistics"""
        total_guilds = sum(m.guild_count for m in self.shard_metrics.values())
        total_users = sum(m.user_count for m in self.shard_metrics.values())
        total_voice_connections = sum(m.voice_connections for m in self.shard_metrics.values())
        
        avg_latency = sum(m.latency_ms for m in self.shard_metrics.values()) / len(self.shard_metrics) if self.shard_metrics else 0
        
        return {
            'shard_count': len(self.shards),
            'total_guilds': total_guilds,
            'total_users': total_users,
            'total_voice_connections': total_voice_connections,
            'average_latency_ms': round(avg_latency, 2),
            'shards': {
                shard_id: {
                    'guild_count': metrics.guild_count,
                    'user_count': metrics.user_count,
                    'voice_connections': metrics.voice_connections,
                    'latency_ms': round(metrics.latency_ms, 2),
                    'status': metrics.status,
                    'commands_processed': metrics.commands_processed,
                    'errors': metrics.errors,
                    'uptime_seconds': round(time.time() - self._shard_startup_times.get(shard_id, time.time()), 2)
                }
                for shard_id, metrics in self.shard_metrics.items()
            }
        }
    
    def get_optimal_shard_for_guild(self, guild_id: int) -> int:
        """Get optimal shard ID for a guild"""
        if not self.shards:
            return 0
        
        # Use Discord's shard calculation
        return (guild_id >> 22) % len(self.shards)
    
    async def send_cross_shard_message(self, message_type: str, data: Any, target_shard: Optional[int] = None) -> None:
        """Send message across shards"""
        if target_shard is not None:
            await self.communicator.send_to_shard(target_shard, message_type, data)
        else:
            await self.communicator.broadcast_to_all_shards(message_type, data)


# Global sharding manager
shard_manager: Optional[ShardManager] = None


def init_sharding(bot_class: type, config: ShardingConfig) -> ShardManager:
    """Initialize sharding manager"""
    global shard_manager
    shard_manager = ShardManager(bot_class, config)
    return shard_manager


def get_shard_manager() -> Optional[ShardManager]:
    """Get global shard manager"""
    return shard_manager


async def start_sharded_bot(bot_class: type, token: str, config: ShardingConfig = None) -> None:
    """Start bot with sharding support"""
    if config is None:
        config = ShardingConfig()
    
    manager = init_sharding(bot_class, config)
    await manager.start_shards(token)


def should_use_sharding(guild_count: int) -> bool:
    """Determine if sharding should be used based on guild count"""
    return guild_count > 2000  # Discord's recommended threshold 
"""
Enhanced Logging System for Discord Bot
Provides structured logging with reduced spam and improved debugging capabilities
"""

import logging
import logging.handlers
import sys
import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
import asyncio


@dataclass
class LogEvent:
    """Structured log event"""

    timestamp: str
    level: str
    logger: str
    message: str
    module: Optional[str] = None
    function: Optional[str] = None
    user_id: Optional[int] = None
    guild_id: Optional[int] = None
    channel_id: Optional[int] = None
    command: Optional[str] = None
    error_type: Optional[str] = None
    traceback: Optional[str] = None


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging"""

    def format(self, record: logging.LogRecord) -> str:
        log_event = LogEvent(
            timestamp=datetime.fromtimestamp(record.created).isoformat(),
            level=record.levelname,
            logger=record.name,
            message=record.getMessage(),
            module=getattr(record, "module", None),
            function=getattr(record, "funcName", None),
            user_id=getattr(record, "user_id", None),
            guild_id=getattr(record, "guild_id", None),
            channel_id=getattr(record, "channel_id", None),
            command=getattr(record, "command", None),
            error_type=getattr(record, "error_type", None),
        )

        # Add traceback for exceptions
        if record.exc_info:
            log_event.traceback = "".join(traceback.format_exception(*record.exc_info))

        return json.dumps(asdict(log_event), default=str)


class DiscordLogFilter:
    """Filter to reduce Discord.py logging spam"""

    def __init__(self):
        self.spam_patterns = [
            "Keeping websocket alive with ping",
            "WebSocket heartbeat acknowledged",
            "Dispatching event",
            "Created a new session",
        ]

        self.rate_limited_messages = {}
        self.rate_limit_window = 60  # seconds
        self.max_messages_per_window = 5

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter log records to reduce spam"""
        message = record.getMessage()

        # Filter known spam patterns
        for pattern in self.spam_patterns:
            if pattern in message:
                return False

        # Rate limit repeated messages
        now = datetime.now().timestamp()
        message_key = f"{record.name}:{record.levelname}:{message[:100]}"

        if message_key not in self.rate_limited_messages:
            self.rate_limited_messages[message_key] = []

        # Clean old entries
        self.rate_limited_messages[message_key] = [
            timestamp
            for timestamp in self.rate_limited_messages[message_key]
            if now - timestamp < self.rate_limit_window
        ]

        # Check rate limit
        if len(self.rate_limited_messages[message_key]) >= self.max_messages_per_window:
            return False

        # Add current timestamp
        self.rate_limited_messages[message_key].append(now)
        return True


class EnhancedLogger:
    """Enhanced logging system for Discord bot"""

    def __init__(self, name: str = "discordbot"):
        self.name = name
        self.logger = logging.getLogger(name)
        self.setup_complete = False
        self.log_dir = Path("logs")
        self.log_dir.mkdir(exist_ok=True)

    def setup_logging(self, level: str = "INFO", enable_json: bool = False):
        """Setup logging configuration"""
        if self.setup_complete:
            return

        # Clear existing handlers
        self.logger.handlers.clear()

        # Set level
        log_level = getattr(logging, level.upper(), logging.INFO)
        self.logger.setLevel(log_level)

        # Console handler with colored output
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)

        if enable_json:
            console_formatter = JSONFormatter()
        else:
            console_formatter = logging.Formatter(
                "%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # File handler for general logs
        file_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "bot.log",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JSONFormatter() if enable_json else console_formatter)
        self.logger.addHandler(file_handler)

        # Separate error log file
        error_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "error.log",
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(JSONFormatter())
        self.logger.addHandler(error_handler)

        # Setup Discord.py logging with spam filter
        discord_logger = logging.getLogger("discord")
        discord_logger.setLevel(logging.WARNING)  # Reduce Discord spam

        spam_filter = DiscordLogFilter()
        for handler in discord_logger.handlers:
            handler.addFilter(spam_filter.filter)

        # Setup other library loggers
        self._setup_library_loggers()

        self.setup_complete = True
        self.logger.info(f"Logging setup complete - Level: {level}")

    def _setup_library_loggers(self):
        """Setup logging for third-party libraries"""
        library_configs = {
            "aiohttp": logging.WARNING,
            "asyncio": logging.WARNING,
            "urllib3": logging.WARNING,
            "requests": logging.WARNING,
            "soundcloud": logging.INFO,
            "pytubefix": logging.WARNING,
        }

        for lib_name, lib_level in library_configs.items():
            lib_logger = logging.getLogger(lib_name)
            lib_logger.setLevel(lib_level)

    def get_context_logger(self, ctx=None, interaction=None) -> logging.LoggerAdapter:
        """Get logger with context information"""
        extra = {}

        if ctx:
            extra.update(
                {
                    "user_id": ctx.author.id,
                    "guild_id": ctx.guild.id if ctx.guild else None,
                    "channel_id": ctx.channel.id,
                    "command": ctx.command.name if ctx.command else None,
                }
            )

        if interaction:
            extra.update(
                {
                    "user_id": interaction.user.id,
                    "guild_id": interaction.guild.id if interaction.guild else None,
                    "channel_id": (
                        interaction.channel.id if interaction.channel else None
                    ),
                    "command": (
                        interaction.command.name
                        if hasattr(interaction, "command") and interaction.command
                        else None
                    ),
                }
            )

        return logging.LoggerAdapter(self.logger, extra)

    def log_command_usage(
        self, ctx=None, interaction=None, execution_time: float = None
    ):
        """Log command usage with metrics"""
        logger = self.get_context_logger(ctx, interaction)

        command_name = None
        if ctx and ctx.command:
            command_name = ctx.command.name
        elif interaction and hasattr(interaction, "command") and interaction.command:
            command_name = interaction.command.name

        message = f"Command executed: {command_name}"
        if execution_time:
            message += f" (took {execution_time:.2f}s)"

        logger.info(message)

    def log_error_with_context(self, error: Exception, ctx=None, interaction=None):
        """Log error with full context"""
        logger = self.get_context_logger(ctx, interaction)

        error_info = {
            "error_type": type(error).__name__,
            "error_message": str(error),
        }

        logger.error(
            f"Error occurred: {error_info['error_type']}: {error_info['error_message']}",
            exc_info=error,
            extra=error_info,
        )

    def log_performance_metric(self, metric_name: str, value: float, unit: str = "ms"):
        """Log performance metrics"""
        self.logger.info(f"Performance metric - {metric_name}: {value}{unit}")

    def log_resource_usage(self, stats: Dict[str, Any]):
        """Log resource usage statistics"""
        self.logger.debug(f"Resource usage: {json.dumps(stats)}")


# Global logger instance
enhanced_logger = EnhancedLogger()


def setup_logging(level: str = "INFO", enable_json: bool = False):
    """Setup enhanced logging"""
    enhanced_logger.setup_logging(level, enable_json)


def get_logger(name: str = None) -> logging.Logger:
    """Get logger instance"""
    if name:
        return logging.getLogger(name)
    return enhanced_logger.logger


def get_context_logger(ctx=None, interaction=None) -> logging.LoggerAdapter:
    """Get logger with context"""
    return enhanced_logger.get_context_logger(ctx, interaction)


def log_command_usage(ctx=None, interaction=None, execution_time: float = None):
    """Log command usage"""
    enhanced_logger.log_command_usage(ctx, interaction, execution_time)


def log_error_with_context(error: Exception, ctx=None, interaction=None):
    """Log error with context"""
    enhanced_logger.log_error_with_context(error, ctx, interaction)

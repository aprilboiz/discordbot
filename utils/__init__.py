import asyncio
import functools
import logging
import logging.handlers
import os
from datetime import datetime, timedelta
import sys
import shutil
from pathlib import Path
from typing import Any, Callable, Coroutine, Literal, Union

from dotenv import find_dotenv, load_dotenv

import constants

# Import opus loading utilities
from .opus_loader import (
    load_opus_library,
    get_opus_installation_help,
    verify_opus_functionality,
)

# Import enhancement utilities
from .resource_manager import ResourceManager
from .network_utils import network_manager, managed_session
from .config_manager import get_config, get_token, is_admin, is_development
from .logging_utils import setup_logging, get_logger, get_context_logger
from .cache_utils import cache_manager, cached, rate_limited, get_cache_stats
from .monitoring import init_monitoring, start_monitoring, record_command, record_error


def to_thread(func: Callable) -> Coroutine:
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    return wrapper  # type: ignore


def convert_to_second(time: str) -> float:
    """Converts a time string with '%H:%M:%S' format to seconds."""
    parsed_datetime: datetime = datetime.strptime(time, "%H:%M:%S")
    dt: timedelta = parsed_datetime - datetime(1900, 1, 1)
    return dt.total_seconds()


def convert_to_time(seconds: float) -> str:
    """Converts seconds to a time string with '%H:%M:%S' format."""
    return str(timedelta(seconds=seconds))


def get_time() -> str:
    """Get the current time in '%H:%M:%S' format."""
    dt = datetime.now().time()
    timestr = f"{dt.hour}:{dt.minute}:{dt.second}"
    return timestr


def safe_getattr(obj: Any, attr: str, default: Any) -> Any:
    """Safely get an attribute from an object."""
    return getattr(obj, attr, default) if obj else default


def safe_format_date(date: datetime | None) -> str:
    """Safely format a date object to a string."""
    try:
        return date.strftime("%d/%m/%Y")  # type: ignore
    except (ValueError, TypeError):
        return "Unknown Date"


def format_playback_count(playback_count: int) -> str:
    """Format the playback count to a string."""
    return "{:,}".format(playback_count)


def format_duration(
    duration: float, unit: Literal["seconds", "milliseconds"] = "seconds"
) -> str:
    """Format the duration to a string.
    Args:
        duration (float): The duration in seconds.
        unit (str): The unit to format the duration to. Default is 'seconds'.
        Returns:
        str: The formatted duration.
    """
    if unit == "milliseconds":
        duration_fmt = timedelta(milliseconds=duration)
        duration_fmt -= timedelta(microseconds=duration_fmt.microseconds)
    else:  # default to seconds
        duration_fmt = timedelta(seconds=duration)
    return str(duration_fmt)


def get_env(key: str) -> Union[str, None]:
    """Get the environment variable by the key."""
    load_dotenv(dotenv_path=find_dotenv())
    TOKEN: str | None = os.environ.get(key)
    return TOKEN


def setup_logger(name: str, level=logging.DEBUG):
    """Set up logging configuration"""
    # Create logs directory if it doesn't exist
    logs_dir = Path(f"{constants.CUR_PATH}/logs")
    logs_dir.mkdir(exist_ok=True)

    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Create formatters
    dt_fmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(
        "[{asctime}] [{levelname:<8}] {name}: {message}", dt_fmt, style="{"
    )

    # File handler for all logs
    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(logs_dir / "bot.log"),
        encoding="utf-8",
        maxBytes=32 * 1024 * 1024,  # 32 MiB
        backupCount=5,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # File handler for errors only
    error_handler = logging.handlers.RotatingFileHandler(
        filename=str(logs_dir / "error.log"),
        encoding="utf-8",
        maxBytes=32 * 1024 * 1024,  # 32 MiB
        backupCount=5,
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    logger.addHandler(error_handler)

    # Optional console handler
    # console_handler = logging.StreamHandler()
    # console_handler.setFormatter(formatter)
    # console_handler.setLevel(
    #     logging.WARNING
    # )  # Only show warnings and errors in console
    # logger.addHandler(console_handler)


def cleanup() -> None:
    """Clean garbages after bot end."""
    import shutil

    def remove_dirs(curr_dir="./", del_dirs=["temp_folder", "__pycache__"]):
        for del_dir in del_dirs:
            if del_dir in os.listdir(curr_dir):
                shutil.rmtree(os.path.join(curr_dir, del_dir))

        for dir in os.listdir(curr_dir):
            dir = os.path.join(curr_dir, dir)
            if os.path.isdir(dir):
                remove_dirs(dir, del_dirs)

    try:
        remove_dirs(curr_dir=constants.CUR_PATH)
    except Exception as e:
        print("There was some errors when trying to delete garbages:", e)
    finally:
        print("Cleanup completed.")


class Timer:
    """Auto execute a task when the time is out.
    This designed for auto disconnect feature."""

    def __init__(self, callback, ctx) -> None:
        self.__callback = callback
        self.ctx = ctx
        self.__task = asyncio.create_task(self.__job())

    async def __job(self) -> None:
        await asyncio.sleep(constants.VOICE_TIMEOUT)
        await self.__callback(self.ctx)

    def cancel(self) -> None:
        self.__task.cancel()

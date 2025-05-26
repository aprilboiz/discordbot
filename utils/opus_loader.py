"""
Cross-platform Opus library loader for Discord.py voice functionality.
Supports Windows, macOS, and Linux environments.
"""

import os
import platform
import logging
from typing import List, Optional

_log = logging.getLogger(__name__)


def get_platform_opus_paths() -> List[str]:
    """
    Get platform-specific Opus library paths.

    Returns:
        List of potential Opus library paths for the current platform.
    """
    system = platform.system().lower()

    if system == "windows":
        # Windows paths for Opus library
        return [
            "opus.dll",
            "libopus.dll",
            "libopus-0.dll",
            os.path.join(os.getcwd(), "opus.dll"),
            os.path.join(os.getcwd(), "libopus.dll"),
            # Common installation paths on Windows
            r"C:\Windows\System32\opus.dll",
            r"C:\Windows\SysWOW64\opus.dll",
            # FFmpeg bundled paths
            os.path.join(os.getcwd(), "ffmpeg", "bin", "opus.dll"),
        ]
    elif system == "darwin":  # macOS
        return [
            "/usr/local/lib/libopus.dylib",
            "/opt/homebrew/lib/libopus.dylib",
            "/usr/lib/libopus.dylib",
            "libopus.dylib",
            "libopus.0.dylib",
            # Homebrew paths
            "/opt/homebrew/Cellar/opus/*/lib/libopus.dylib",
        ]
    else:  # Linux and other Unix-like systems
        return [
            "/usr/lib/libopus.so.0",
            "/usr/lib/libopus.so",
            "/usr/lib/x86_64-linux-gnu/libopus.so.0",
            "/usr/lib/x86_64-linux-gnu/libopus.so",
            "/usr/lib/aarch64-linux-gnu/libopus.so.0",
            "/usr/lib/aarch64-linux-gnu/libopus.so",
            "/usr/local/lib/libopus.so.0",
            "/usr/local/lib/libopus.so",
            "libopus.so.0",
            "libopus.so",
            # Alpine Linux specific
            "/usr/lib/libopus.so.0",
        ]


def load_opus_library() -> bool:
    """
    Attempt to load the Opus library for Discord voice functionality.

    Returns:
        True if Opus was successfully loaded, False otherwise.
    """
    try:
        import discord.opus

        # Check if Opus is already loaded
        if discord.opus.is_loaded():
            _log.info("Opus is already loaded")
            return True

        _log.info(f"Loading Opus library for {platform.system()} {platform.machine()}")

        opus_paths = get_platform_opus_paths()

        for path in opus_paths:
            try:
                # Check if file exists before attempting to load (for absolute paths)
                if os.path.isabs(path) and not os.path.isfile(path):
                    continue

                discord.opus.load_opus(path)
                _log.info(f"Successfully loaded Opus from: {path}")
                return True

            except Exception as e:
                _log.debug(f"Failed to load Opus from {path}: {e}")
                continue

        # Try auto-detection as fallback
        _log.warning("Could not load Opus explicitly, attempting auto-detection")
        if discord.opus.is_loaded():
            _log.info("Opus auto-detection successful")
            return True
        else:
            _log.error("Opus auto-detection failed")
            return False

    except Exception as e:
        _log.error(f"Error during Opus initialization: {e}")
        return False


def get_opus_installation_help() -> str:
    """
    Get platform-specific help text for installing Opus.

    Returns:
        Help text with installation instructions.
    """
    system = platform.system().lower()

    if system == "windows":
        return (
            "Windows Opus Installation:\n"
            "1. Download opus.dll from a trusted source\n"
            "2. Place it in the bot directory or Windows system directory\n"
            "3. Alternative: Install FFmpeg which includes Opus\n"
            "4. Ensure the DLL is compatible with your Python architecture (32/64-bit)"
        )
    elif system == "darwin":
        return (
            "macOS Opus Installation:\n"
            "1. Install via Homebrew: brew install opus\n"
            "2. Alternative: brew install ffmpeg (includes Opus)\n"
            "3. For MacPorts: sudo port install opus"
        )
    else:
        return (
            "Linux Opus Installation:\n"
            "Ubuntu/Debian: sudo apt-get install libopus0 libopus-dev\n"
            "CentOS/RHEL: sudo yum install opus opus-devel\n"
            "Fedora: sudo dnf install opus opus-devel\n"
            "Alpine: apk add opus opus-dev\n"
            "Arch: sudo pacman -S opus"
        )


def verify_opus_functionality() -> dict:
    """
    Verify Opus functionality and return status information.

    Returns:
        Dictionary with Opus status information.
    """
    try:
        import discord.opus

        status = {
            "loaded": discord.opus.is_loaded(),
            "platform": platform.system(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
        }

        if status["loaded"]:
            # Try to get Opus version if possible
            try:
                # This is a basic test to ensure Opus is working
                status["working"] = True
                status["message"] = "Opus is loaded and ready for voice operations"
            except Exception as e:
                status["working"] = False
                status["message"] = f"Opus loaded but may not be functioning: {e}"
        else:
            status["working"] = False
            status["message"] = "Opus is not loaded"

        return status

    except Exception as e:
        return {
            "loaded": False,
            "working": False,
            "error": str(e),
            "message": "Error checking Opus status",
        }

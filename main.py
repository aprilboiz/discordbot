import sys
import shutil
import traceback


def check_python_compatibility(required_version: tuple) -> None:
    current = sys.version_info[:2]
    if current < required_version:
        raise RuntimeError(
            f"This bot requires Python {'.'.join(map(str, required_version))} or higher. "
            f"You are using Python {'.'.join(map(str, current))}"
        )


def check_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "FFmpeg not found. Please ensure FFmpeg is installed and available in PATH."
        )


def check_deno() -> None:
    """Check if Deno is available for yt-dlp"""
    import subprocess
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        result = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        ytdlp_version = result.stdout.strip() if result.returncode == 0 else "unknown"
        
        # Check for JS runtime in verbose output
        result = subprocess.run(
            ["yt-dlp", "-v"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        
        if result.returncode == 0:
            output = result.stdout + result.stderr
            # Check for deno in the output
            if "deno" in output.lower():
                # Try to extract deno version
                for line in output.split('\n'):
                    if "deno" in line.lower() and "js runtimes" in line.lower():
                        logger.info(f"yt-dlp YouTube support: {line.strip()}")
                        break
                else:
                    # Check for warning
                    if "no supported javascript runtime" in output.lower():
                        logger.warning(
                            "⚠️ Deno not found - yt-dlp will use Python fallback. "
                            "Some YouTube formats may be unavailable. "
                            "Install Deno for full support: https://deno.land/"
                        )
                    else:
                        logger.info("yt-dlp YouTube support: Deno detected")
            else:
                logger.info(f"yt-dlp version: {ytdlp_version}")
    except FileNotFoundError:
        logger.warning("⚠️ yt-dlp not found in PATH")
    except Exception as e:
        logger.warning(f"Could not check yt-dlp status: {e}")


if __name__ == "__main__":
    try:
        check_python_compatibility((3, 12))
        check_ffmpeg()
        check_deno()

        import bot
        bot.run_bot()
    except Exception as e:
        print(f"Error during startup: {e}")
        print(traceback.format_exc())
        sys.exit(1)

import sys
import shutil
import traceback
import asyncio
import logging
import signal

_log = logging.getLogger(__name__)


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


async def main():
    """Enhanced main entry point with full optimization support"""
    try:
        # Import after compatibility checks
        from utils.env_loader import load_environment, validate_environment, create_env_template
        from utils.logging_utils import setup_logging
        from utils.performance_optimizer import init_performance_optimization, cleanup_performance_optimization
        from utils.health_monitor import start_health_monitoring, stop_health_monitoring
        from bot import run_bot_async, cleanup_bot
        
        # Validate environment first
        if not validate_environment():
            print("Environment validation failed. Creating template...")
            create_env_template()
            print("Please configure your .env file and restart the bot")
            sys.exit(1)
        
        # Load environment configuration
        env_config = load_environment()
        print(f"Loaded environment configuration (dev_mode: {env_config.dev_mode})")
        
        # Initialize logging with config
        setup_logging(level=env_config.log_level, enable_json=False)
        _log.info("Enhanced Discord Music Bot starting...")
        
        # Initialize performance optimization
        await init_performance_optimization()
        _log.info("Performance optimization initialized")
        
        # Start health monitoring
        await start_health_monitoring(port=8080)
        _log.info("Health monitoring started on port 8080")
        
        # Setup graceful shutdown
        shutdown_event = asyncio.Event()
        
        def signal_handler(signum, frame):
            _log.info(f"Received signal {signum}, shutting down gracefully...")
            shutdown_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Start the bot
        bot_task = asyncio.create_task(run_bot_async(env_config))
        
        # Wait for shutdown signal or bot completion
        done, pending = await asyncio.wait(
            [bot_task, asyncio.create_task(shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED
        )
        
        # Cancel pending tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Cleanup
        await cleanup_shutdown()
        
    except KeyboardInterrupt:
        _log.info("Received keyboard interrupt, shutting down...")
        await cleanup_shutdown()
    except Exception as e:
        _log.critical(f"Failed to start bot: {e}")
        print(f"Critical error: {e}")
        print(traceback.format_exc())
        sys.exit(1)


async def cleanup_shutdown():
    """Perform cleanup during shutdown"""
    try:
        _log.info("Performing cleanup...")
        
        # Stop health monitoring
        await stop_health_monitoring()
        _log.info("Health monitoring stopped")
        
        # Cleanup performance optimization
        await cleanup_performance_optimization()
        _log.info("Performance optimization cleaned up")
        
        # Cleanup bot
        await cleanup_bot()
        _log.info("Bot cleanup complete")
        
    except Exception as e:
        _log.error(f"Error during cleanup: {e}")


if __name__ == "__main__":
    try:
        check_python_compatibility((3, 12))
        check_ffmpeg()
        
        # Run the enhanced bot
        asyncio.run(main())
        
    except KeyboardInterrupt:
        print("Bot stopped by user")
    except Exception as e:
        print(f"Error during startup: {e}")
        print(traceback.format_exc())
        sys.exit(1)

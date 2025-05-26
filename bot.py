import asyncio
import logging
import traceback

import discord
from discord.ext import commands

from cogs.admin import Admin
from cogs.greetings import Greeting
from cogs.music.music_cog import MusicCog
from cogs.tts.tts import TTS
from core.error_handler import ErrorHandler
from utils import cleanup, get_env, setup_logger
from utils.opus_loader import (
    load_opus_library,
    get_opus_installation_help,
    verify_opus_functionality,
)
from utils.config_manager import get_token
from utils.env_loader import load_environment, validate_environment, create_env_template
from utils.logging_utils import setup_logging
from utils.monitoring import init_monitoring, start_monitoring, stop_monitoring
from utils.network_utils import network_manager
from utils.resource_manager import ResourceManager

_log = logging.getLogger(name=__name__)


class Bot(commands.Bot):
    def __init__(self) -> None:
        intents: discord.Intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True  # Required for voice functionality
        self.error_handler = ErrorHandler(self)
        self.resource_manager = ResourceManager()
        super().__init__(
            command_prefix="?",  # Keep for backward compatibility
            intents=intents,
            help_command=None  # Disable default help command
        )

    async def setup_hook(self) -> None:
        """Setup hook called when bot is ready"""
        # Setup error handlers
        self.tree.error(self.error_handler.handle_interaction_error)
        
        # Start resource monitoring
        await self.resource_manager.start_monitoring()
        
        # Sync slash commands in development mode
        env_config = load_environment()
        if env_config.dev_mode:
            _log.info("Development mode: Syncing slash commands...")
            try:
                synced = await self.tree.sync()
                _log.info(f"Synced {len(synced)} slash commands")
            except Exception as e:
                _log.error(f"Failed to sync slash commands: {e}")

    async def close(self) -> None:
        """Clean shutdown with resource cleanup"""
        _log.info("Shutting down bot...")
        
        # Stop resource monitoring
        await self.resource_manager.stop_monitoring()
        
        # Close network manager
        await network_manager.close_session()
        
        # Stop monitoring
        stop_monitoring()
        
        # Close bot connection
        await super().close()
        
        _log.info("Bot shutdown complete")

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        await self.error_handler.handle_command_error(ctx, error)


bot = Bot()


@bot.event
async def on_ready() -> None:
    """Called when bot is ready"""
    if bot.user:
        msg: str = f"Logged in as {bot.user} (ID: {bot.user.id})"
        print(msg)
        print("-" * len(msg))
        
        # Log slash command status
        slash_commands = len(bot.tree.get_commands())
        _log.info(f"Bot ready with {slash_commands} slash commands")
        
        # Set bot status
        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name="/play | Music & More"
        )
        await bot.change_presence(activity=activity)


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    """Called when bot joins a new guild"""
    _log.info(f"Joined guild: {guild.name} (ID: {guild.id})")
    
    # Sync slash commands for new guild
    try:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        _log.info(f"Synced slash commands for guild: {guild.name}")
    except Exception as e:
        _log.error(f"Failed to sync commands for guild {guild.name}: {e}")


async def init_bot() -> None:
    """Initialize and start the bot"""
    # Validate environment first
    if not validate_environment():
        _log.error("Environment validation failed. Creating template...")
        create_env_template()
        raise ValueError("Please configure your .env file and restart the bot")
    
    # Load environment configuration
    env_config = load_environment()
    _log.info(f"Loaded environment configuration (dev_mode: {env_config.dev_mode})")
    
    # Initialize logging with config
    setup_logging(level=env_config.log_level, enable_json=False)

    # Load Opus library with cross-platform support
    _log.info("Initializing Opus library...")
    if load_opus_library():
        opus_status = verify_opus_functionality()
        _log.info(f"Opus Status: {opus_status['message']}")
    else:
        _log.warning("Failed to load Opus library. Voice features may be limited.")
        _log.info("Installation help:")
        for line in get_opus_installation_help().split("\n"):
            _log.info(f"  {line}")

    # Setup legacy loggers (keeping existing behavior)
    setup_logger(name="bot")
    setup_logger(name="soundcloud")
    setup_logger(name="discord", level=logging.INFO)
    setup_logger(name="cogs")
    setup_logger(name="discordbot")
    setup_logger(name="pytubefix", level=logging.INFO)

    async with bot:
        # Get token using secure config manager
        try:
            token = get_token()
        except ValueError:
            # Fallback to legacy method
            token = get_env(key="TOKEN")
            if token is None:
                raise ValueError("Cannot find token in env.")
        
        # Initialize monitoring
        init_monitoring(bot)
        start_monitoring()
        
        # Load cogs with new structure
        _log.info("Loading cogs...")
        try:
            await bot.add_cog(MusicCog(bot))  # New restructured music cog
            _log.info("✅ Music cog loaded")
        except Exception as e:
            _log.error(f"❌ Failed to load Music cog: {e}")
        
        try:
            await bot.add_cog(Greeting(bot))
            _log.info("✅ Greeting cog loaded")
        except Exception as e:
            _log.error(f"❌ Failed to load Greeting cog: {e}")
        
        try:
            await bot.add_cog(TTS(bot))
            _log.info("✅ TTS cog loaded")
        except Exception as e:
            _log.error(f"❌ Failed to load TTS cog: {e}")
        
        try:
            await bot.add_cog(Admin(bot))
            _log.info("✅ Admin cog loaded")
        except Exception as e:
            _log.error(f"❌ Failed to load Admin cog: {e}")

        _log.info("Starting bot...")
        await bot.start(token=token)


async def run_bot_async(env_config=None):
    """Async version of run_bot for enhanced main.py"""
    try:
        await init_bot()
    except Exception as e:
        _log.critical(f"Failed to start bot: {e}")
        raise


async def cleanup_bot():
    """Enhanced cleanup function"""
    try:
        _log.info("Starting bot cleanup...")
        
        # Disconnect all voice clients
        for vc in bot.voice_clients:
            try:
                await vc.disconnect(force=True)
                _log.info(f"Disconnected from voice channel: {vc.channel}")
            except Exception as e:
                _log.error(f"Error disconnecting voice client: {e}")
        
        # Close bot connection
        if not bot.is_closed():
            await bot.close()
            
        # Legacy cleanup
        cleanup()
        
        _log.info("Bot cleanup completed")
        
    except Exception as e:
        _log.error(f"Error during bot cleanup: {e}")


def run_bot():
    """Main bot runner function"""
    async def shutdown():
        await cleanup_bot()

    try:
        asyncio.run(init_bot())
    except KeyboardInterrupt:
        print("\nReceived interrupt signal...")
        asyncio.run(shutdown())
        print("Bot terminated by user.")
    except Exception as e:
        print(f"Bot terminated because of the following error:\n{traceback.format_exc()}")
        _log.critical(f"Bot crashed: {e}")
    finally:
        cleanup()

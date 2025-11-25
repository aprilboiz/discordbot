import asyncio
import logging
import traceback

import discord
from discord.ext import commands

from cogs.admin.admin import Admin
from cogs.admin.config import Config
from cogs.greetings import Greeting
from cogs.music.manager import PlayerManager
from cogs.music.music import Music
from cogs.tts.tts import TTS
from core.error_handler import ErrorHandler
from core.settings_manager import SettingsManager
from utils import cleanup, get_env, setup_logger

_log = logging.getLogger(name=__name__)


class Bot(commands.Bot):
    def __init__(self) -> None:
        intents: discord.Intents = discord.Intents.default()
        intents.message_content = True
        self.error_handler = ErrorHandler(self)
        self.settings_manager = SettingsManager()
        self.player_manager = PlayerManager()
        # Remove command_prefix to enforce slash commands
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

    async def setup_hook(self) -> None:
        self.tree.error(self.error_handler.handle_interaction_error)

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        await self.error_handler.handle_command_error(ctx, error)


bot = Bot()


@bot.event
async def on_ready() -> None:
    if bot.user:
        msg: str = f"Logged in as {bot.user} (ID: {bot.user.id})"
        print(msg)
        print("-" * len(msg))


async def init_bot() -> None:
    setup_logger(name="bot")
    setup_logger(name="soundcloud")
    setup_logger(name="discord", level=logging.INFO)
    setup_logger(name="cogs")
    setup_logger(name="discordbot")
    setup_logger(name="yt_dlp", level=logging.INFO)

    async with bot:
        token = get_env(key="TOKEN")
        if token is None:
            raise ValueError("Cannot find token in env.")
        await bot.add_cog(Music(bot, bot.player_manager, ErrorHandler(bot)))
        await bot.add_cog(Greeting(bot))
        await bot.add_cog(TTS(bot))
        await bot.add_cog(Admin(bot))
        await bot.add_cog(Config(bot, bot.settings_manager))
        await bot.start(token=token)


def run_bot():
    try:
        asyncio.run(init_bot())
    except KeyboardInterrupt:
        for vc in bot.voice_clients:
            asyncio.create_task(vc.disconnect(force=True))
        print("Bot terminated by host.")
    except Exception:
        print(
            f"Bot terminated because of the following error:\n{traceback.format_exc()}"
        )
    finally:
        cleanup()

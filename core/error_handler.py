import discord
from discord.ext import commands
import traceback
from typing import Optional, Dict, Any
import datetime
import logging
import asyncio
from collections import defaultdict
import time


class ErrorHandler:
    """Enhanced error handler with monitoring and rate limiting"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.error_channel_id: Optional[int] = None
        self.logger = logging.getLogger("bot.error_handler")
        self.logger.setLevel(logging.INFO)

        # Error rate limiting
        self.error_counts = defaultdict(int)
        self.error_timestamps = defaultdict(list)
        self.rate_limit_window = 300  # 5 minutes
        self.max_errors_per_window = 5

        # Error recovery tracking
        self.recovery_attempts = defaultdict(int)
        self.max_recovery_attempts = 3

    def log_error(self, error: Exception, error_source: str, **kwargs):
        """Log error with context information"""
        error_info = {
            "Error Type": type(error).__name__,
            "Error Message": str(error),
            "Error Source": error_source,
            **kwargs,
        }

        # Format the error message
        log_message = "\n".join(f"{k}: {v}" for k, v in error_info.items())

        # Add traceback for unexpected errors
        if not isinstance(error, commands.CommandError):
            tb = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )
            log_message = f"{log_message}\nTraceback:\n{tb}"

        self.logger.error(log_message)

    async def create_error_embed(
        self,
        error: Exception,
        ctx: Optional[commands.Context] = None,
        interaction: Optional[discord.Interaction] = None,
    ) -> discord.Embed:
        """Creates a detailed error embed with information about the error"""

        embed = discord.Embed(
            title="❌ Error Occurred",
            color=discord.Color.red(),
            timestamp=datetime.datetime.utcnow(),
        )

        # Get command information
        command_name = None
        if ctx:
            command_name = ctx.command.qualified_name if ctx.command else "Unknown"
        elif interaction:
            command_name = (
                interaction.command.name if interaction.command else "Unknown"
            )

        # Add error information
        embed.add_field(
            name="Error Type", value=f"```py\n{type(error).__name__}\n```", inline=False
        )
        embed.add_field(
            name="Error Message", value=f"```py\n{str(error)}\n```", inline=False
        )

        # Add command usage context
        if command_name:
            embed.add_field(name="Command Used", value=f"`{command_name}`", inline=True)

        # Add user information
        user = ctx.author if ctx else interaction.user if interaction else None
        if user:
            embed.add_field(
                name="User", value=f"{user.name} (ID: {user.id})", inline=True
            )

        # Add guild information
        guild = ctx.guild if ctx else interaction.guild if interaction else None
        if guild:
            embed.add_field(
                name="Guild", value=f"{guild.name} (ID: {guild.id})", inline=True
            )

        return embed

    async def handle_command_error(
        self, ctx: commands.Context, error: Exception
    ) -> None:
        """Handles errors from traditional command contexts"""

        # Get original error if exists
        error = getattr(error, "original", error)

        # Check rate limiting
        error_key = self._get_error_key(error, "command")
        if self._should_rate_limit_error(error_key):
            return  # Skip if rate limited

        # Record metrics
        await self._record_error_metrics(error, "command")

        # Create error-specific messages
        error_message = None
        if isinstance(error, commands.MissingRequiredArgument):
            error_message = f"Missing required argument: {error.param.name}"
        elif isinstance(error, commands.CommandOnCooldown):
            error_message = f"This command is on cooldown. Try again in {error.retry_after:.2f} seconds."
        elif isinstance(error, commands.MissingPermissions):
            error_message = (
                "You don't have the required permissions to use this command."
            )
        elif isinstance(error, commands.BotMissingPermissions):
            error_message = (
                "I don't have the required permissions to execute this command."
            )
        elif isinstance(error, commands.BadArgument):
            error_message = "Invalid argument provided."
        elif isinstance(error, commands.CommandNotFound):
            return  # Don't respond to unknown commands

        # Log error with context
        self.log_error(
            error,
            "Traditional Command",
            Command=ctx.command.qualified_name if ctx.command else "Unknown",
            User=f"{ctx.author} (ID: {ctx.author.id})",
            Guild=f"{ctx.guild} (ID: {ctx.guild.id})" if ctx.guild else "DM",
            Channel=f"{ctx.channel} (ID: {ctx.channel.id})",
            Message=ctx.message.content,
        )

        # Attempt recovery
        if await self._attempt_error_recovery(error, ctx=ctx):
            return

        if error_message:
            # Send user-friendly error message
            error_embed = discord.Embed(
                title="❌ Error", description=error_message, color=discord.Color.red()
            )
            try:
                await ctx.send(embed=error_embed, delete_after=10)
            except Exception as e:
                self.logger.error(f"Failed to send error message to user: {e}")

        # Send to error channel if it's an unexpected error
        if error_message is None:
            error_embed = await self.create_error_embed(error, ctx=ctx)
            await self._send_to_error_channel(error_embed)

    async def handle_interaction_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        """Handles errors from application commands/interactions"""

        # Get original error if exists
        error = getattr(error, "original", error)

        # Check rate limiting
        error_key = self._get_error_key(error, "interaction")
        if self._should_rate_limit_error(error_key):
            return  # Skip if rate limited

        # Record metrics
        await self._record_error_metrics(error, "interaction")

        # Log error with context
        channel_info = "Unknown"
        if interaction.channel:
            channel_info = f"{interaction.channel} (ID: {interaction.channel.id})"

        self.log_error(
            error,
            "Interaction Command",
            Command=interaction.command.name if interaction.command else "Unknown",
            User=f"{interaction.user} (ID: {interaction.user.id})",
            Guild=(
                f"{interaction.guild} (ID: {interaction.guild.id})"
                if interaction.guild
                else "DM"
            ),
            Channel=channel_info,
        )

        # Attempt recovery
        if await self._attempt_error_recovery(error, interaction=interaction):
            return

        # Create user-friendly error message
        error_message = str(error)
        if isinstance(error, discord.app_commands.CommandInvokeError):
            error_message = str(error.original)

        # Send error message to user
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="❌ Error",
                        description=error_message,
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="❌ Error",
                        description=error_message,
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
        except discord.errors.InteractionResponded:
            pass
        except Exception as e:
            self.logger.error(f"Failed to send error message to user: {e}")

        # Log unexpected errors to error channel
        error_embed = await self.create_error_embed(error, interaction=interaction)
        await self._send_to_error_channel(error_embed)

    async def _send_to_error_channel(self, embed: discord.Embed):
        """Helper method to safely send errors to the error channel"""
        if self.error_channel_id:
            error_channel = self.bot.get_channel(self.error_channel_id)
            if error_channel and isinstance(error_channel, discord.TextChannel):
                try:
                    await error_channel.send(embed=embed)
                except Exception as e:
                    self.logger.error(f"Failed to send error to error channel: {e}")

    def _should_rate_limit_error(self, error_key: str) -> bool:
        """Check if error should be rate limited"""
        current_time = time.time()

        # Clean old timestamps
        self.error_timestamps[error_key] = [
            ts
            for ts in self.error_timestamps[error_key]
            if current_time - ts < self.rate_limit_window
        ]

        # Check if we're over the limit
        if len(self.error_timestamps[error_key]) >= self.max_errors_per_window:
            return True

        # Add current timestamp
        self.error_timestamps[error_key].append(current_time)
        return False

    def _get_error_key(self, error: Exception, context: str) -> str:
        """Generate a unique key for error rate limiting"""
        return f"{type(error).__name__}:{context}"

    async def _record_error_metrics(self, error: Exception, context: str):
        """Record error metrics for monitoring"""
        try:
            from utils.monitoring import record_error

            record_error()  # Call without parameters as expected
        except ImportError:
            pass  # Monitoring not available

    async def _attempt_error_recovery(
        self, error: Exception, ctx=None, interaction=None
    ):
        """Attempt to recover from certain types of errors"""
        recovery_key = f"{type(error).__name__}"

        if self.recovery_attempts[recovery_key] >= self.max_recovery_attempts:
            return False

        self.recovery_attempts[recovery_key] += 1

        # Attempt recovery based on error type
        if isinstance(error, discord.HTTPException):
            # For HTTP errors, wait and retry
            await asyncio.sleep(1)
            return True
        elif isinstance(error, commands.BotMissingPermissions):
            # Log permission issues for admin attention
            self.logger.warning(f"Permission issue detected: {error}")
            return False

        return False

    def set_error_channel(self, channel_id: int):
        """Set the error notification channel"""
        self.error_channel_id = channel_id
        self.logger.info(f"Error channel set to {channel_id}")

    async def get_error_stats(self) -> Dict[str, Any]:
        """Get error statistics for monitoring"""
        current_time = time.time()

        # Count recent errors
        recent_errors = {}
        for error_key, timestamps in self.error_timestamps.items():
            recent_count = len(
                [ts for ts in timestamps if current_time - ts < self.rate_limit_window]
            )
            if recent_count > 0:
                recent_errors[error_key] = recent_count

        return {
            "recent_errors": recent_errors,
            "total_error_types": len(self.error_counts),
            "recovery_attempts": dict(self.recovery_attempts),
            "rate_limit_window": self.rate_limit_window,
            "max_errors_per_window": self.max_errors_per_window,
        }

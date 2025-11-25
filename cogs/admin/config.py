import discord
from discord import app_commands
from discord.ext import commands
from core.settings_manager import SettingsManager

class Config(commands.Cog):
    def __init__(self, bot: commands.Bot, settings_manager: SettingsManager):
        self.bot = bot
        self.settings_manager = settings_manager

    @app_commands.command(name="config", description="Configure bot settings (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        setting="The setting to change",
        value="The new value"
    )
    @app_commands.choices(setting=[
        app_commands.Choice(name="Max Queue Size", value="max_queue_size"),
        app_commands.Choice(name="Max Track Duration (seconds)", value="max_track_duration"),
        app_commands.Choice(name="Volume Limit", value="volume_limit")
    ])
    async def config(self, interaction: discord.Interaction, setting: str, value: int):
        if value < 0:
            await interaction.response.send_message("Value must be positive.", ephemeral=True)
            return

        # Specific validations
        if setting == "max_queue_size" and value > 2000:
             await interaction.response.send_message("Max queue size cannot exceed 2000.", ephemeral=True)
             return

        if setting == "volume_limit" and value > 200:
             await interaction.response.send_message("Volume limit cannot exceed 200%.", ephemeral=True)
             return

        self.settings_manager.set(interaction.guild_id, setting, value)
        await interaction.response.send_message(f"✅ Set **{setting}** to `{value}`.", ephemeral=True)

    @app_commands.command(name="view_config", description="View current bot settings")
    @app_commands.checks.has_permissions(administrator=True)
    async def view_config(self, interaction: discord.Interaction):
        settings = self.settings_manager.get_all(interaction.guild_id)

        embed = discord.Embed(title="⚙️ Server Configuration", color=discord.Color.blue())
        for key, value in settings.items():
            embed.add_field(name=key.replace("_", " ").title(), value=str(value), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config.error
    @view_config.error
    async def config_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need Administrator permissions to use this command.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ An error occurred: {error}", ephemeral=True)

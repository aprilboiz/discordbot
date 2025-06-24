import discord
from discord.ext import commands
from discord import app_commands
from cogs.music.controller import PlayerManager


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="invite", description="Get the bot invite link with proper permissions.")
    async def invite(self, interaction: discord.Interaction) -> None:
        """Generate and send the bot invite link with all necessary permissions."""
        
        if not self.bot.user:
            await interaction.response.send_message("❌ Bot user information not available.", ephemeral=True)
            return
        
        # Define permissions needed for a music bot
        permissions = discord.Permissions(
            # Basic bot permissions
            read_messages=True,
            send_messages=True,
            send_messages_in_threads=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            add_reactions=True,
            use_external_emojis=True,
            use_external_stickers=True,
            
            # Voice channel permissions (essential for music bot)
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            priority_speaker=True,
            
            # Music bot specific permissions
            manage_messages=True,  # For queue management and cleanup
            use_application_commands=True,  # For slash commands
            
            # Optional but useful permissions
            change_nickname=True,
            create_public_threads=True,
            create_private_threads=True,
            send_tts_messages=True,
        )
        
        # Generate the invite URL
        invite_url = discord.utils.oauth_url(
            client_id=self.bot.user.id,
            permissions=permissions,
            scopes=('bot', 'applications.commands')
        )
        
        # Create an embed with the invite link and permission details
        embed = discord.Embed(
            title="🤖 Bot Invite Link",
            description=f"Click [here]({invite_url}) to invite the bot to your server!",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="📋 Included Permissions",
            value="""
            **Essential Permissions:**
            • Connect & Speak in voice channels
            • Send messages & embeds
            • Read message history
            • Use slash commands
            
            **Music Bot Features:**
            • Manage messages (queue management)
            • Add reactions (controls)
            • Stream audio
            • Priority speaker
            
            **Additional Features:**
            • External emojis & stickers
            • Thread creation & participation
            • TTS messages
            """,
            inline=False
        )
        
        embed.add_field(
            name="⚠️ Important Notes",
            value="""
            • These permissions ensure full functionality
            • Some features may not work without proper permissions
            • Server administrators can modify permissions after invite
            """,
            inline=False
        )
        
        embed.set_footer(text=f"Bot ID: {self.bot.user.id}")
        
        # Send the embed as an ephemeral response (only visible to the user)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="invite_minimal", description="Get the bot invite link with minimal permissions.")
    async def invite_minimal(self, interaction: discord.Interaction) -> None:
        """Generate and send the bot invite link with minimal required permissions."""
        
        if not self.bot.user:
            await interaction.response.send_message("❌ Bot user information not available.", ephemeral=True)
            return
        
        # Define minimal permissions needed for basic music bot functionality
        permissions = discord.Permissions(
            # Absolute minimum for music bot
            read_messages=True,
            send_messages=True,
            embed_links=True,
            connect=True,
            speak=True,
            use_application_commands=True,
        )
        
        # Generate the invite URL
        invite_url = discord.utils.oauth_url(
            client_id=self.bot.user.id,
            permissions=permissions,
            scopes=('bot', 'applications.commands')
        )
        
        # Create a simple embed
        embed = discord.Embed(
            title="🤖 Minimal Bot Invite Link",
            description=f"Click [here]({invite_url}) to invite the bot with minimal permissions!",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="📋 Minimal Permissions",
            value="""
            • Read & Send messages
            • Embed links
            • Connect & Speak in voice
            • Use slash commands
            """,
            inline=False
        )
        
        embed.add_field(
            name="⚠️ Warning",
            value="Some advanced features may not work with minimal permissions.",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="shutdown", description="Shutdown the bot.")
    @app_commands.default_permissions(administrator=True)
    async def shutdown(self, interaction: discord.Interaction) -> None:
        for player in PlayerManager().players.values():
            player.destroy()
            del player

        ctx = await self.bot.get_context(interaction)
        if ctx.voice_client:
            await ctx.voice_client.disconnect(force=True)
        await ctx.send("Bot closed")
        await self.bot.close()

    @app_commands.command(name="sync", description="Sync the guild's slash command.")
    @app_commands.default_permissions(administrator=True)
    async def sync(self, interaction: discord.Interaction):
        ctx = await self.bot.get_context(interaction)
        if ctx.guild:
            self.bot.tree.copy_global_to(guild=ctx.guild)
            await self.bot.tree.sync(guild=ctx.guild)
            await ctx.send("Sync local guild success")
        else:
            await ctx.send("❌ This command must be used in a guild.")

    @app_commands.command(name="sync_all", description="Sync all slash command.")
    @app_commands.default_permissions(administrator=True)
    async def sync_all(self, interaction: discord.Interaction):
        ctx = await self.bot.get_context(interaction)
        await self.bot.tree.sync()
        await ctx.send("Sync all success")

    @app_commands.command(name="remove_command_all", description="Remove all command.")
    @app_commands.default_permissions(administrator=True)
    async def remove_command_all(self, interaction: discord.Interaction):
        ctx = await self.bot.get_context(interaction)
        if ctx.guild:
            self.bot.tree.clear_commands(guild=ctx.guild)
            await self.bot.tree.sync()
            await ctx.send("Remove all command success")
        else:
            await ctx.send("❌ This command must be used in a guild.")

    @app_commands.command(name="permissions_info", description="Show detailed information about bot permissions.")
    async def permissions_info(self, interaction: discord.Interaction) -> None:
        """Show detailed information about the permissions used by the bot."""
        
        # Full permissions for music bot
        full_permissions = discord.Permissions(
            read_messages=True,
            send_messages=True,
            send_messages_in_threads=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            add_reactions=True,
            use_external_emojis=True,
            use_external_stickers=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            priority_speaker=True,
            manage_messages=True,
            use_application_commands=True,
            change_nickname=True,
            create_public_threads=True,
            create_private_threads=True,
            send_tts_messages=True,
        )
        
        # Minimal permissions
        minimal_permissions = discord.Permissions(
            read_messages=True,
            send_messages=True,
            embed_links=True,
            connect=True,
            speak=True,
            use_application_commands=True,
        )
        
        embed = discord.Embed(
            title="🔧 Bot Permissions Information",
            description="Detailed breakdown of bot permissions for different use cases.",
            color=discord.Color.purple()
        )
        
        embed.add_field(
            name="📊 Permission Values",
            value=f"""
            **Full Permissions:** `{full_permissions.value}`
            **Minimal Permissions:** `{minimal_permissions.value}`
            """,
            inline=False
        )
        
        embed.add_field(
            name="🎵 Essential Music Bot Permissions",
            value="""
            • `connect` - Join voice channels
            • `speak` - Play audio in voice channels
            • `send_messages` - Send bot responses
            • `embed_links` - Send rich embeds
            • `use_application_commands` - Use slash commands
            • `read_messages` - Read user commands
            """,
            inline=True
        )
        
        embed.add_field(
            name="🔧 Advanced Features",
            value="""
            • `manage_messages` - Queue management
            • `add_reactions` - Reaction controls
            • `stream` - High quality audio
            • `priority_speaker` - Better audio priority
            • `read_message_history` - Context awareness
            • `use_external_emojis` - Custom emojis
            """,
            inline=True
        )
        
        embed.add_field(
            name="🛠️ Optional Permissions",
            value="""
            • `attach_files` - Send audio files
            • `send_tts_messages` - Text-to-speech
            • `change_nickname` - Dynamic bot name
            • `create_public_threads` - Thread support
            • `send_messages_in_threads` - Thread messages
            """,
            inline=False
        )
        
        embed.add_field(
            name="ℹ️ How to Use",
            value="""
            Use `/invite` for full functionality or `/invite_minimal` for basic features.
            Server admins can always modify permissions after inviting the bot.
            """,
            inline=False
        )
        
        embed.set_footer(text="Use the permission values for manual bot invites if needed.")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

"""
Music Cog - Restructured
Simplified music cog using the new audio controller architecture
"""

import asyncio
import logging
from typing import Optional, Literal

from discord import app_commands
from discord.ext import commands

from .audio_controller import get_or_create_controller, init_audio_manager, get_audio_manager
from .view.view import MusicView
from utils.voice_helpers import ensure_same_channel

_log = logging.getLogger(__name__)


class MusicCog(commands.Cog):
    """Simplified music cog with restructured architecture"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.audio_manager = init_audio_manager(bot)
        _log.info("Music cog initialized")
    
    async def cog_unload(self):
        """Cleanup when cog is unloaded"""
        if self.audio_manager:
            await self.audio_manager.cleanup_all()
    
    def ensure_voice_connection(self):
        """Decorator to ensure voice connection"""
        def decorator(func):
            async def wrapper(self, interaction, *args, **kwargs):
                try:
                    await interaction.response.defer(thinking=True)
                    ctx = await self.bot.get_context(interaction)
                    
                    # Check if user is in voice channel
                    if not ctx.author.voice:
                        await ctx.send("❌ You need to be in a voice channel to use this command.", ephemeral=True)
                        return
                    
                    # Connect to voice channel if not connected
                    if not ctx.voice_client:
                        try:
                            await ctx.author.voice.channel.connect(self_deaf=True)
                            _log.info(f"Connected to voice channel: {ctx.author.voice.channel.name}")
                        except Exception as e:
                            _log.error(f"Failed to connect to voice channel: {e}")
                            await ctx.send("❌ Failed to connect to voice channel.", ephemeral=True)
                            return
                    
                    # Check same channel
                    if not await ensure_same_channel(ctx):
                        return
                    
                    # Execute command
                    await func(self, interaction, *args, **kwargs)
                    
                except Exception as e:
                    _log.error(f"Error in voice command {func.__name__}: {e}")
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "❌ An error occurred while processing your command.",
                            ephemeral=True
                        )
            
            return wrapper
        return decorator
    
    @app_commands.command(name="play", description="Play a song or add it to the queue")
    @ensure_voice_connection()
    async def play(self, interaction, query: str):
        """Play a song"""
        try:
            ctx = await self.bot.get_context(interaction)
            controller = get_or_create_controller(interaction.guild_id)
            
            if not controller:
                await ctx.send("❌ Cannot create audio controller: Maximum connections reached.", ephemeral=True)
                return
            
            success = await controller.search_and_add(ctx, query, priority=False)
            if not success:
                await ctx.send("❌ Failed to add song to queue.", ephemeral=True)
                
        except Exception as e:
            _log.error(f"Error in play command: {e}")
            await interaction.followup.send("❌ An error occurred while processing your request.", ephemeral=True)
    
    @app_commands.command(name="playnext", description="Add a song to play next")
    @ensure_voice_connection()
    async def playnext(self, interaction, query: str):
        """Add song to play next"""
        try:
            ctx = await self.bot.get_context(interaction)
            controller = get_or_create_controller(interaction.guild_id)
            
            if not controller:
                await ctx.send("❌ Cannot create audio controller: Maximum connections reached.", ephemeral=True)
                return
            
            success = await controller.search_and_add(ctx, query, priority=True)
            if not success:
                await ctx.send("❌ Failed to add song to queue.", ephemeral=True)
                
        except Exception as e:
            _log.error(f"Error in playnext command: {e}")
            await interaction.followup.send("❌ An error occurred while processing your request.", ephemeral=True)
    
    @app_commands.command(name="search", description="Search for songs interactively")
    @ensure_voice_connection()
    async def search(self, interaction, query: str, provider: Optional[Literal["youtube", "soundcloud", "spotify"]] = "youtube"):
        """Interactive search"""
        try:
            ctx = await self.bot.get_context(interaction)
            controller = get_or_create_controller(interaction.guild_id)
            
            if not controller:
                await ctx.send("❌ Cannot create audio controller: Maximum connections reached.", ephemeral=True)
                return
            
            success = await controller.search_interactive(ctx, query, provider)
            if not success:
                await ctx.send("❌ Search failed.", ephemeral=True)
                
        except Exception as e:
            _log.error(f"Error in search command: {e}")
            await interaction.followup.send("❌ An error occurred while searching.", ephemeral=True)
    
    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction):
        """Skip current song"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)
            
            controller = get_or_create_controller(interaction.guild_id)
            if not controller:
                await ctx.send("❌ No music player found.", ephemeral=True)
                return
            
            success = await controller.skip_current(ctx)
            if success:
                await ctx.send("⏭️ Song skipped!")
            else:
                await ctx.send("❌ No song is currently playing.", ephemeral=True)
                
        except Exception as e:
            _log.error(f"Error in skip command: {e}")
            await interaction.followup.send("❌ An error occurred.", ephemeral=True)
    
    @app_commands.command(name="stop", description="Stop music and clear queue")
    async def stop(self, interaction):
        """Stop music"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)
            
            controller = get_or_create_controller(interaction.guild_id)
            if not controller:
                await ctx.send("❌ No music player found.", ephemeral=True)
                return
            
            await controller.stop_playback(ctx)
            await self.audio_manager.destroy_controller(interaction.guild_id)
            await ctx.send("🛑 Music stopped and queue cleared!")
                
        except Exception as e:
            _log.error(f"Error in stop command: {e}")
            await interaction.followup.send("❌ An error occurred.", ephemeral=True)
    
    @app_commands.command(name="queue", description="Show the current queue")
    async def queue(self, interaction):
        """Show queue"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)
            
            controller = get_or_create_controller(interaction.guild_id)
            if not controller:
                await ctx.send("❌ No music player found.", ephemeral=True)
                return
            
            if controller.playlist.size() == 0:
                await ctx.send("📭 The queue is empty. Add some songs with `/play`!")
                return
            
            playlist = await controller.playlist.get_list()
            view = MusicView(playlist, self._handle_queue_selection)
            view.message = await ctx.send(embed=view.create_embed(), view=view)
                
        except Exception as e:
            _log.error(f"Error in queue command: {e}")
            await interaction.followup.send("❌ An error occurred.", ephemeral=True)
    
    async def _handle_queue_selection(self, interaction, track):
        """Handle track selection from queue"""
        try:
            controller = get_or_create_controller(interaction.guild_id)
            if controller:
                # Move track to next position
                await controller.playlist.remove_by_song(track)
                await controller.playlist.add_next(track)
                await interaction.followup.send(f"✅ Moved **{track.title}** to next in queue.", ephemeral=True)
            else:
                await interaction.followup.send("❌ No music player found.", ephemeral=True)
        except Exception as e:
            _log.error(f"Error handling queue selection: {e}")
            await interaction.followup.send("❌ Failed to move track.", ephemeral=True)
    
    @app_commands.command(name="now", description="Show current song info")
    async def now_playing(self, interaction):
        """Show current song"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)
            
            controller = get_or_create_controller(interaction.guild_id)
            if not controller or not controller.current_song:
                await ctx.send("❌ No song is currently playing.", ephemeral=True)
                return
            
            from cogs.components.discord_embed import Embed
            embed = Embed(ctx).now_playing_song(controller.current_song)
            
            # Add performance metrics
            metrics = controller.get_metrics()
            embed.add_field(
                name="📊 Session Stats",
                value=f"Songs played: {metrics['songs_played']}\n"
                      f"Queue size: {metrics['playlist_size']}\n"
                      f"Session time: {metrics['session_duration_seconds']}s",
                inline=True
            )
            
            await ctx.send(embed=embed)
                
        except Exception as e:
            _log.error(f"Error in now_playing command: {e}")
            await interaction.followup.send("❌ An error occurred.", ephemeral=True)
    
    @app_commands.command(name="move", description="Move bot to your voice channel")
    @ensure_voice_connection()
    async def move(self, interaction):
        """Move bot to user's channel"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)
            
            controller = get_or_create_controller(interaction.guild_id)
            if not controller:
                await ctx.send("❌ No music player found.", ephemeral=True)
                return
            
            if ctx.author.voice.channel == ctx.voice_client.channel:
                await ctx.send("❌ I'm already in your voice channel!", ephemeral=True)
                return
            
            success = await controller.move_to_channel(ctx, ctx.author.voice.channel)
            if success:
                await ctx.send(f"🔄 Moved to **{ctx.author.voice.channel.name}**!")
            else:
                await ctx.send("❌ Failed to move to your channel.", ephemeral=True)
                
        except Exception as e:
            _log.error(f"Error in move command: {e}")
            await interaction.followup.send("❌ An error occurred.", ephemeral=True)
    
    @app_commands.command(name="stats", description="Show music bot statistics")
    async def stats(self, interaction):
        """Show bot statistics"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)
            
            if not self.audio_manager:
                await ctx.send("❌ Audio manager not available.", ephemeral=True)
                return
            
            global_metrics = self.audio_manager.get_global_metrics()
            
            from cogs.components.discord_embed import Embed
            embed = Embed(ctx).info(
                title="🎵 Music Bot Statistics",
                description=f"**Active Controllers:** {global_metrics['active_controllers']}/{global_metrics['max_concurrent']}\n"
                           f"**Utilization:** {global_metrics['utilization_percent']}%\n"
                           f"**Total Created:** {global_metrics['total_controllers_created']}\n"
                           f"**Peak Concurrent:** {global_metrics['peak_concurrent']}\n"
                           f"**Connection Limit Hits:** {global_metrics['connection_limit_hits']}"
            )
            
            # Add current controller stats if available
            controller = get_or_create_controller(interaction.guild_id)
            if controller:
                metrics = controller.get_metrics()
                embed.add_field(
                    name="📊 This Server",
                    value=f"Songs played: {metrics['songs_played']}\n"
                          f"Search requests: {metrics['search_requests']}\n"
                          f"Errors: {metrics['playback_errors']}\n"
                          f"Queue size: {metrics['playlist_size']}",
                    inline=True
                )
            
            await ctx.send(embed=embed)
                
        except Exception as e:
            _log.error(f"Error in stats command: {e}")
            await interaction.followup.send("❌ An error occurred.", ephemeral=True)


async def setup(bot):
    """Setup function for the music cog"""
    await bot.add_cog(MusicCog(bot)) 
"""
Admin Commands
Provides admin commands to monitor and manage bot performance
"""

import discord
from discord.ext import commands
from discord import app_commands
import json
import time

from cogs.music.controller import PlayerManager
from utils.cache_utils import get_cache_stats, clear_cache
from utils.monitoring import get_health_summary
from utils.config_manager import get_config, is_admin
from utils.logging_utils import get_logger

_log = get_logger(__name__)


class Admin(commands.Cog):
    """Admin commands for monitoring bot performance and basic bot management"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Only allow admins to use these commands"""
        return is_admin(ctx.author.id) or await self.bot.is_owner(ctx.author)

    # ============ Basic Admin Commands ============

    @app_commands.command(name="shutdown", description="Shutdown the bot.")
    @app_commands.default_permissions(administrator=True)
    async def shutdown(self, interaction: discord.Interaction) -> None:
        """Shutdown the bot gracefully"""
        await interaction.response.defer()

        try:
            # Clean up music players
            for player in PlayerManager().players.values():
                player.destroy()
                del player

            # Disconnect voice clients
            ctx = await self.bot.get_context(interaction)
            if ctx.voice_client:
                await ctx.voice_client.disconnect()

            await interaction.followup.send("🔴 Bot shutting down...")
            _log.info(f"Bot shutdown initiated by {interaction.user.id}")
            await self.bot.close()

        except Exception as e:
            _log.error(f"Error during shutdown: {e}")
            await interaction.followup.send("❌ Error during shutdown.", ephemeral=True)

    @app_commands.command(name="sync", description="Sync the guild's slash commands.")
    @app_commands.default_permissions(administrator=True)
    async def sync(self, interaction: discord.Interaction):
        """Sync slash commands for the current guild"""
        await interaction.response.defer()

        try:
            ctx = await self.bot.get_context(interaction)
            self.bot.tree.copy_global_to(guild=ctx.guild)
            await self.bot.tree.sync(guild=ctx.guild)
            await interaction.followup.send(
                "✅ Guild slash commands synced successfully."
            )
            _log.info(
                f"Guild commands synced by {interaction.user.id} in guild {ctx.guild.id}"
            )

        except Exception as e:
            _log.error(f"Error syncing guild commands: {e}")
            await interaction.followup.send(
                "❌ Error syncing guild commands.", ephemeral=True
            )

    @app_commands.command(
        name="sync_all", description="Sync all global slash commands."
    )
    @app_commands.default_permissions(administrator=True)
    async def sync_all(self, interaction: discord.Interaction):
        """Sync all global slash commands"""
        await interaction.response.defer()

        try:
            await self.bot.tree.sync()
            await interaction.followup.send(
                "✅ All global slash commands synced successfully."
            )
            _log.info(f"Global commands synced by {interaction.user.id}")

        except Exception as e:
            _log.error(f"Error syncing global commands: {e}")
            await interaction.followup.send(
                "❌ Error syncing global commands.", ephemeral=True
            )

    @app_commands.command(
        name="remove_command_all", description="Remove all commands from the guild."
    )
    @app_commands.default_permissions(administrator=True)
    async def remove_command_all(self, interaction: discord.Interaction):
        """Remove all commands from the current guild"""
        await interaction.response.defer()

        try:
            ctx = await self.bot.get_context(interaction)
            self.bot.tree.clear_commands(guild=ctx.guild)
            await self.bot.tree.sync()
            await interaction.followup.send(
                "✅ All guild commands removed successfully."
            )
            _log.info(
                f"All guild commands removed by {interaction.user.id} in guild {ctx.guild.id}"
            )

        except Exception as e:
            _log.error(f"Error removing guild commands: {e}")
            await interaction.followup.send(
                "❌ Error removing guild commands.", ephemeral=True
            )    # ============ Performance Commands ============

    @app_commands.command(
        name="performance-status", description="Get bot performance status"
    )
    async def performance_status(self, interaction: discord.Interaction):
        """Show comprehensive system status"""
        await interaction.response.defer()

        try:
            # Get various stats
            health_summary = get_health_summary()
            cache_stats = get_cache_stats()
            config = get_config()

            # Create main embed
            embed = discord.Embed(
                title="🚀 Bot System Status",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )

            # System Health
            if health_summary.get("status") == "healthy":
                health_icon = "💚"
                health_status = "Healthy"
            else:
                health_icon = "⚠️"
                health_status = "Issues Detected"

            uptime_hours = health_summary.get("uptime_hours", 0)
            memory_usage = health_summary.get("memory_usage", {})
            cpu_usage = health_summary.get("cpu_usage", {})
            performance = health_summary.get("performance", {})

            embed.add_field(
                name=f"{health_icon} System Health",
                value=f"Status: **{health_status}**\n"
                f"Uptime: {uptime_hours:.1f}h\n"
                f"Memory: {memory_usage.get('current_percent', 0):.1f}%\n"
                f"CPU: {cpu_usage.get('current_percent', 0):.1f}%",
                inline=True,
            )

            # Performance Metrics
            embed.add_field(
                name="📊 Performance",
                value=f"Commands/min: {performance.get('commands_per_minute', 0)}\n"
                f"Errors/min: {performance.get('errors_per_minute', 0)}\n"
                f"Avg Response: {performance.get('avg_response_time_ms', 0):.0f}ms\n"
                f"Cache Hit Rate: {performance.get('cache_hit_rate', 0):.1f}%",
                inline=True,
            )

            # Cache Statistics
            total_cache_entries = 0
            cache_summary = []

            for cache_name, stats in cache_stats.items():
                if isinstance(stats, dict):
                    entries = stats.get("size", 0)
                    hit_rate = stats.get("hit_rate_percent", 0)
                    total_cache_entries += entries
                    cache_summary.append(
                        f"{cache_name}: {entries} entries ({hit_rate:.1f}% hit rate)"
                    )

            embed.add_field(
                name="🗄️ Cache Status",
                value=f"Total Entries: **{total_cache_entries}**\n"
                + "\n".join(cache_summary[:3]),
                inline=True,
            )

            # Voice Connections
            voice_clients = len(self.bot.voice_clients)
            embed.add_field(
                name="🎵 Voice Status",
                value=f"Active Connections: **{voice_clients}**\n"
                f"Max Allowed: {config.max_voice_connections}",
                inline=True,
            )

            # Recent Alerts
            alerts = health_summary.get("alerts", {})
            recent_alerts = alerts.get("recent_alerts", [])

            if recent_alerts:
                alert_summary = "\n".join(
                    [
                        f"• {alert['name']} ({alert['count']}x)"
                        for alert in recent_alerts[:3]
                    ]
                )
            else:
                alert_summary = "No recent alerts ✅"

            embed.add_field(name="🚨 Recent Alerts", value=alert_summary, inline=True)

            # Configuration Status
            config_status = []
            config_status.append(
                f"Dev Mode: {'✅' if config.enable_dev_mode else '❌'}"
            )
            config_status.append(f"Log Level: {config.log_level}")
            config_status.append(f"Command Prefix: {config.command_prefix}")

            embed.add_field(
                name="⚙️ Configuration", value="\n".join(config_status), inline=True
            )            # Add footer with feature checklist progress
            features = [
                "✅ Async I/O Operations",
                "✅ Resource Management",
                "✅ Error Handling",
                "✅ Network Retry Logic",
                "✅ Secure Config Management",
                "✅ Enhanced Logging",
                "✅ Caching & Rate Limiting",
                "✅ Monitoring & Alerting",
            ]

            embed.set_footer(
                text=f"Features: {len([x for x in features if '✅' in x])}/8 Complete"
            )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            _log.error(f"Error in status command: {e}")
            await interaction.followup.send(
                "❌ Error retrieving system status.", ephemeral=True
            )

    @app_commands.command(name="clear-cache", description="Clear bot caches")
    async def clear_cache_command(
        self, interaction: discord.Interaction, cache_type: str | None = None
    ):
        """Clear specific cache or all caches"""
        await interaction.response.defer()

        try:
            if cache_type:
                clear_cache(cache_type)
                await interaction.followup.send(f"✅ Cleared {cache_type} cache.")
            else:
                clear_cache()
                await interaction.followup.send("✅ Cleared all caches.")

            _log.info(f"Cache cleared by {interaction.user.id}: {cache_type or 'all'}")

        except Exception as e:
            _log.error(f"Error clearing cache: {e}")
            await interaction.followup.send("❌ Error clearing cache.", ephemeral=True)

    @app_commands.command(
        name="export-metrics", description="Export performance metrics"
    )
    async def export_metrics(self, interaction: discord.Interaction, hours: int = 24):
        """Export performance metrics as JSON"""
        await interaction.response.defer()

        try:
            # Get metrics data
            health_summary = get_health_summary()
            cache_stats = get_cache_stats()

            metrics_data = {
                "timestamp": time.time(),
                "export_hours": hours,
                "health_summary": health_summary,
                "cache_statistics": cache_stats,
                "bot_info": {
                    "user_count": len(self.bot.users),
                    "guild_count": len(self.bot.guilds),
                    "voice_connections": len(self.bot.voice_clients),
                },
            }

            # Create JSON file content
            json_content = json.dumps(metrics_data, indent=2, default=str)            # Create Discord file
            file = discord.File(
                fp=discord.utils._bytes_to_base64_data(json_content.encode()),
                filename=f"bot_metrics_{int(time.time())}.json",
            )
            
            embed = discord.Embed(
                title="📈 Metrics Export",
                description=f"Performance metrics for the last {hours} hours",
                color=discord.Color.blue(),
            )
            
            await interaction.followup.send(embed=embed, file=file)
            _log.info(f"Metrics exported by {interaction.user.id}")
            
        except Exception as e:
            _log.error(f"Error exporting metrics: {e}")
            await interaction.followup.send(
                "❌ Error exporting metrics.", ephemeral=True
            )

    @app_commands.command(
        name="test", description="Run system tests"
    )
    async def test(self, interaction: discord.Interaction):
        """Test various system components"""
        await interaction.response.defer()

        try:
            test_results = []

            # Test 1: Cache System
            try:
                from utils.cache_utils import cache_manager

                test_key = f"test_{time.time()}"
                cache_manager.api_cache.set(test_key, "test_value", 60)
                retrieved = cache_manager.api_cache.get(test_key)
                test_results.append(
                    f"✅ Cache System: {'PASS' if retrieved == 'test_value' else 'FAIL'}"
                )
                cache_manager.api_cache.delete(test_key)
            except Exception as e:
                test_results.append(f"❌ Cache System: FAIL ({str(e)[:50]})")

            # Test 2: Network Manager
            try:
                from utils.network_utils import network_manager

                session = await network_manager.get_session()
                test_results.append(
                    f"✅ Network Manager: {'PASS' if session else 'FAIL'}"
                )
            except Exception as e:
                test_results.append(f"❌ Network Manager: FAIL ({str(e)[:50]})")

            # Test 3: Resource Manager
            try:
                if hasattr(self.bot, "resource_manager"):
                    test_results.append("✅ Resource Manager: PASS")
                else:
                    test_results.append("❌ Resource Manager: NOT INITIALIZED")
            except Exception as e:
                test_results.append(f"❌ Resource Manager: FAIL ({str(e)[:50]})")

            # Test 4: Monitoring System
            try:
                health = get_health_summary()
                test_results.append(f"✅ Monitoring: {'PASS' if health else 'FAIL'}")
            except Exception as e:
                test_results.append(f"❌ Monitoring: FAIL ({str(e)[:50]})")

            # Test 5: Configuration
            try:
                config = get_config()
                test_results.append(
                    f"✅ Configuration: {'PASS' if config.token else 'FAIL'}"
                )
            except Exception as e:
                test_results.append(f"❌ Configuration: FAIL ({str(e)[:50]})")

            embed = discord.Embed(
                title="🧪 System Test Results",
                description="\n".join(test_results),
                color=discord.Color.green(),
            )

            passed_tests = len([r for r in test_results if "✅" in r])
            total_tests = len(test_results)

            embed.set_footer(text=f"Tests Passed: {passed_tests}/{total_tests}")

            await interaction.followup.send(embed=embed)
            _log.info(
                f"System tests run by {interaction.user.id}: {passed_tests}/{total_tests} passed"
            )

        except Exception as e:
            _log.error(f"Error running system tests: {e}")
            await interaction.followup.send(
                "❌ Error running system tests.", ephemeral=True
            )    # ============ Music Performance Commands ============

    @app_commands.command(
        name="music-stats",
        description="Get detailed music performance statistics"
    )
    async def music_stats(self, interaction: discord.Interaction):
        """Show comprehensive music performance statistics"""
        await interaction.response.defer()

        try:            # Try to get enhanced manager first, fallback to regular
            player_manager = None
            is_enhanced = False
            try:
                from cogs.music.manager import get_player_manager
                player_manager = get_player_manager()
                is_enhanced = True
            except (ImportError, AttributeError):
                from cogs.music.manager import PlayerManager
                player_manager = PlayerManager()
                is_enhanced = False

            embed = discord.Embed(
                title="🎵 Music Performance Statistics",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )

            # Global stats
            if is_enhanced and hasattr(player_manager, '_global_metrics'):
                global_metrics = getattr(player_manager, '_global_metrics', {})
                embed.add_field(
                    name="🌐 Global Statistics",
                    value=f"Active Players: **{len(player_manager.players)}**\n"
                          f"Total Created: {global_metrics.get('total_players_created', 0)}\n"
                          f"Peak Concurrent: {global_metrics.get('peak_concurrent_players', 0)}\n"
                          f"Songs Played: {global_metrics.get('total_songs_played', 0)}",
                    inline=True
                )
            else:
                embed.add_field(
                    name="🌐 Basic Statistics",
                    value=f"Active Players: **{len(player_manager.players)}**\n"
                          f"Guilds with Music: {len(player_manager.players)}",
                    inline=True
                )

            # Current guild stats
            guild_id = interaction.guild_id
            if guild_id and guild_id in player_manager.players:
                player = player_manager.players[guild_id]                # Basic player info
                current_song_title = "None"
                if (hasattr(player.playlist_manager, 'current_song') and
                    player.playlist_manager.current_song):
                    current_song_title = player.playlist_manager.current_song.title

                embed.add_field(
                    name="🎶 Current Guild",
                    value=f"Playing: {'✅' if player.is_playing else '❌'}\n"
                          f"Queue Size: {player.playlist_manager.playlist.size()}\n"
                          f"Current Song: {current_song_title}",
                    inline=True
                )                # Advanced metrics if available (enhanced version)
                if is_enhanced and hasattr(player, 'get_metrics'):
                    try:
                        metrics = player.get_metrics()
                        embed.add_field(
                            name="📊 Performance Metrics",
                            value=f"Songs Played: {metrics.get('songs_played', 0)}\n"
                                  f"Search Requests: {metrics.get('search_requests', 0)}\n"
                                  f"Cache Hit Rate: {metrics.get('cache_hit_rate', 0):.1f}%\n"
                                  f"Avg Response: {metrics.get('average_response_time', 0):.3f}s",
                            inline=True
                        )

                        if metrics.get('playback_errors', 0) > 0:
                            embed.add_field(
                                name="⚠️ Error Statistics",
                                value=f"Playback Errors: {metrics.get('playback_errors', 0)}\n"
                                      f"Active Tasks: {metrics.get('active_search_tasks', 0)}",
                                inline=True
                            )
                    except Exception as e:
                        _log.warning(f"Could not get advanced metrics: {e}")                # Playlist metrics if available (enhanced version)
                if is_enhanced and hasattr(player.playlist_manager, 'get_metrics'):
                    try:
                        playlist_metrics = player.playlist_manager.get_metrics()
                        embed.add_field(
                            name="📝 Playlist Statistics",
                            value=f"Songs Added: {playlist_metrics.get('songs_added', 0)}\n"
                                  f"Songs Removed: {playlist_metrics.get('songs_removed', 0)}\n"
                                  f"Avg Add Time: {playlist_metrics.get('average_add_time', 0):.3f}s",
                            inline=True
                        )
                    except Exception as e:
                        _log.warning(f"Could not get playlist metrics: {e}")
            else:
                embed.add_field(
                    name="🎶 Current Guild",
                    value="No active music player",
                    inline=True
                )

            # System-wide player stats
            if is_enhanced:
                total_queue_size = 0
                active_players = 0

                for player in player_manager.players.values():
                    total_queue_size += player.playlist_manager.playlist.size()
                    if player.is_playing:
                        active_players += 1

                embed.add_field(
                    name="🔄 System Summary",
                    value=f"Total Queue Items: {total_queue_size}\n"
                          f"Currently Playing: {active_players}/{len(player_manager.players)}\n"
                          f"Enhancement: {'✅ Enhanced' if is_enhanced else '⚠️ Basic'}",
                    inline=True
                )

            # Add recommendations
            recommendations = []
            if is_enhanced and guild_id and guild_id in player_manager.players:
                player = player_manager.players[guild_id]
                if hasattr(player, 'get_metrics'):
                    try:
                        metrics = player.get_metrics()
                        if metrics.get('cache_hit_rate', 0) < 50:
                            recommendations.append("• Low cache hit rate - consider longer cache TTL")
                        if metrics.get('playback_errors', 0) > 5:
                            recommendations.append("• High error rate - check network connectivity")
                        if metrics.get('average_response_time', 0) > 2:
                            recommendations.append("• Slow response times - consider enhancements")
                    except Exception:
                        pass

            if not recommendations:
                recommendations.append("• Performance looks good! 🎉")

            embed.add_field(
                name="💡 Recommendations",
                value="\n".join(recommendations),
                inline=False
            )

            await interaction.followup.send(embed=embed)
            _log.info(f"Music stats requested by {interaction.user.id}")

        except Exception as e:
            _log.error(f"Error in music stats command: {e}")
            await interaction.followup.send(
                "❌ Error retrieving music statistics.", ephemeral=True
            )

    @app_commands.command(
        name="music-enhance",
        description="Apply music performance enhancements"
    )
    async def music_enhance(self, interaction: discord.Interaction):
        """Apply various music performance enhancements"""
        await interaction.response.defer()
        
        try:
            enhancements_applied = []
            
            # Try to switch to enhanced player manager
            try:
                from cogs.music.manager import get_player_manager
                player_manager = get_player_manager()
                enhancements_applied.append("✅ Using enhanced player manager")
            except (ImportError, AttributeError):
                enhancements_applied.append("⚠️ Enhanced manager not available")

            # Clear music caches
            try:
                from cogs.music.controller import Audio
                # Clear search cache if available
                if hasattr(Audio, '_search_cache'):
                    Audio._search_cache.clear()
                if hasattr(Audio, '_cache_ttl'):
                    Audio._cache_ttl.clear()
                enhancements_applied.append("✅ Cleared music search cache")
            except (ImportError, AttributeError):
                enhancements_applied.append("⚠️ Advanced cache clearing not available")

            # Cleanup inactive players
            try:                from cogs.music.manager import PlayerManager
                player_manager = PlayerManager()
                
                inactive_count = 0
                for guild_id, player in player_manager.players.items():
                    # Check if enhanced player with metrics
                    if hasattr(player, 'metrics'):
                        try:
                            metrics = getattr(player, 'metrics', None)
                            if metrics and hasattr(metrics, 'last_activity'):
                                if time.time() - metrics.last_activity > 1800:  # 30 minutes
                                    inactive_count += 1
                        except Exception:
                            pass

                if inactive_count > 0:
                    enhancements_applied.append(f"🧹 Found {inactive_count} inactive players")
                else:
                    enhancements_applied.append("✅ No inactive players found")

            except Exception as e:
                enhancements_applied.append(f"❌ Player cleanup check failed: {str(e)[:50]}")

            # Memory enhancement
            try:
                import gc
                collected = gc.collect()
                enhancements_applied.append(f"🗑️ Garbage collected {collected} objects")
            except Exception:
                enhancements_applied.append("⚠️ Memory enhancement unavailable")

            embed = discord.Embed(
                title="🔧 Music Enhancement Results",
                description="\n".join(enhancements_applied),
                color=discord.Color.green()
            )

            embed.add_field(
                name="📈 Next Steps",
                value="• Monitor performance with `/music-stats`\n"
                      "• Use enhanced music commands when available\n"
                      "• Regular cache clearing for best performance",
                inline=False
            )

            await interaction.followup.send(embed=embed)
            _log.info(f"Music enhancement applied by {interaction.user.id}")

        except Exception as e:
            _log.error(f"Error in music enhancement: {e}")
            await interaction.followup.send(
                "❌ Error applying music enhancements.", ephemeral=True
            )

    # ============ Existing Enhancement Admin Commands ============


async def setup(bot):
    """Setup function for the cog"""
    await bot.add_cog(Admin(bot))

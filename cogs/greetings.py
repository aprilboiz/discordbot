import asyncio
import logging
import datetime
import aiohttp
from concurrent.futures import ThreadPoolExecutor
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Literal

from utils.network_utils import managed_session
from utils.cache_utils import cached, rate_limited

_log = logging.getLogger(__name__)


class Greeting(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_member = None
        self.executor = ThreadPoolExecutor(max_workers=4)

    async def cog_unload(self):
        """Clean up resources when cog is unloaded"""
        if hasattr(self, "executor"):
            self.executor.shutdown(wait=True)

    @app_commands.command(name="hello", description="Say hello to someone")
    @app_commands.describe(member="The member to greet (optional)")
    async def hello(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        """Say hello to a member"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)
            
            target_member = member or ctx.author

            if self._last_member is None or self._last_member.id != target_member.id:
                await ctx.send(f"Hello {target_member.name}!")
            else:
                await ctx.send(f"Hey, {target_member.name}. Glad to see you again!")
            self._last_member = target_member
            
        except Exception as e:
            _log.error(f"Error in hello command: {e}")
            await interaction.followup.send("❌ An error occurred.", ephemeral=True)

    @app_commands.command(name="ping", description="Test bot connection.")
    async def ping(self, interaction):
        """Test connection of this bot."""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)

            latency_ms = round(self.bot.latency * 1000)

            if latency_ms <= 50:
                color = 0x44FF44
            elif latency_ms <= 100:
                color = 0xFFD000
            elif latency_ms <= 200:
                color = 0xFF6600
            else:
                color = 0x990000

            embed = discord.Embed(
                title="PING",
                description=f":ping_pong: Ping is **{latency_ms}** milliseconds!",
                color=color,
            )
            await ctx.send(embed=embed)
        except Exception as e:
            _log.error(f"Ping command error: {e}")
            await interaction.followup.send(
                "❌ Error occurred while checking ping.", ephemeral=True
            )

    @app_commands.command(name="sleep", description="Help your sleep better.")
    async def sleep(self, interaction: discord.Interaction):
        """Calculate optimal sleep times based on sleep cycles"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)

            current_time = datetime.datetime.now()
            time_now = current_time.strftime("%H:%M:%S")

            # Sleep cycle times (in hours and minutes)
            cycle_times = [
                (4, 44),  # 1.5 cycles
                (6, 14),  # 2.5 cycles
                (7, 44),  # 3.5 cycles
                (9, 14),  # 4.5 cycles
            ]

            wake_times = []

            for hours, minutes in cycle_times:
                wake_time = current_time + datetime.timedelta(
                    hours=hours, minutes=minutes
                )
                formatted_time = wake_time.strftime("%H:%M")

                # Determine session (morning/afternoon/evening)
                hour = wake_time.hour
                if 0 <= hour < 12:
                    session = "sáng"
                elif 12 <= hour < 18:
                    session = "chiều"
                else:
                    session = "tối"

                wake_times.append(f"{formatted_time} {session}")

            message = (
                f"Bây giờ là {time_now}. Nếu bạn đi ngủ ngay bây giờ, "
                f"bạn nên cố gắng thức dậy vào một trong những thời điểm sau:\n"
                f"🕐 {wake_times[0]} hoặc {wake_times[1]} hoặc {wake_times[2]} hoặc {wake_times[3]}\n\n"
                f"💡 Thức dậy giữa một chu kỳ giấc ngủ khiến bạn cảm thấy mệt mỏi, "
                f"nhưng khi thức dậy vào giữa chu kỳ tỉnh giấc sẽ làm bạn cảm thấy tỉnh táo và minh mẫn.\n\n"
                f"Chúc ngủ ngon! 😴"
            )
            embed = discord.Embed(
                title="💤 Sleep Calculator",
                description=message,
                color=discord.Color.blue(),
            )
            await ctx.send(embed=embed)

        except Exception as e:
            _log.error(f"Sleep command error: {e}")
            await interaction.followup.send(
                "❌ Error occurred while calculating sleep times.", ephemeral=True
            )

    @app_commands.command(name="currency", description="Convert currency using real-time exchange rates")
    @app_commands.describe(
        from_currency="Currency to convert from (e.g., USD)",
        to_currency="Currency to convert to (e.g., EUR)",
        amount="Amount to convert"
    )
    async def currency(self, interaction: discord.Interaction, from_currency: str, to_currency: str, amount: float):
        """Convert currency using async HTTP requests with caching and rate limiting"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)

            # Validate amount
            if amount <= 0:
                await ctx.send("❌ Amount must be a positive number.", ephemeral=True)
                return

            url = f"https://api.exchangerate-api.com/v4/latest/{from_currency.upper()}"

            async with managed_session() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()

                        if to_currency.upper() in data["rates"]:
                            rate = data["rates"][to_currency.upper()]
                            converted_amount = amount * rate

                            embed = discord.Embed(
                                title="💱 Currency Conversion",
                                description=(
                                    f"{amount} {from_currency.upper()} = "
                                    f"{converted_amount:.2f} {to_currency.upper()}"
                                ),
                                color=discord.Color.green(),
                            )
                            embed.add_field(
                                name="Exchange Rate",
                                value=f"1 {from_currency.upper()} = {rate:.4f} {to_currency.upper()}",
                                inline=False,
                            )
                            await ctx.send(embed=embed)
                        else:
                            await ctx.send(
                                f"❌ Currency '{to_currency.upper()}' not found.", ephemeral=True
                            )
                    else:
                        await ctx.send(
                            "❌ Failed to fetch currency data. Please try again later.", ephemeral=True
                        )

        except aiohttp.ClientError:
            await ctx.send("❌ Network error occurred. Please try again later.", ephemeral=True)
        except Exception as e:
            await ctx.send(
                "❌ Error occurred while converting currency. Please check your input and try again.", ephemeral=True
            )
            _log.error(f"Currency conversion error: {e}")

    @app_commands.command(name="speedtest", description="Run internet speed test")
    async def speedtest(self, interaction: discord.Interaction):
        """Run speedtest asynchronously to avoid blocking"""
        try:
            await interaction.response.defer(thinking=True)
            ctx = await self.bot.get_context(interaction)
            
            msg = await ctx.send("🏃‍♂️ Running speed test... This may take a moment.")

            # Run speedtest in executor to avoid blocking
            def run_speedtest():
                import speedtest

                s = speedtest.Speedtest(secure=True)
                s.get_best_server()
                s.download()
                s.upload()
                return s.results.dict()

            try:
                # Run with timeout
                results = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        self.executor, run_speedtest
                    ),
                    timeout=60.0,
                )

                download_mbps = results["download"] / 1_000_000
                upload_mbps = results["upload"] / 1_000_000
                ping_ms = results["ping"]

                embed = discord.Embed(
                    title="🚀 Speed Test Results",
                    color=discord.Color.blue(),
                    timestamp=discord.utils.utcnow(),
                )

                embed.add_field(
                    name="📥 Download",
                    value=f"{download_mbps:.2f} Mbps",
                    inline=True,
                )
                embed.add_field(
                    name="📤 Upload", value=f"{upload_mbps:.2f} Mbps", inline=True
                )
                embed.add_field(name="🏓 Ping", value=f"{ping_ms:.2f} ms", inline=True)

                embed.add_field(
                    name="🌐 Server",
                    value=f"{results['server']['name']} ({results['server']['country']})",
                    inline=False,
                )

                await msg.edit(content=None, embed=embed)

            except asyncio.TimeoutError:
                await msg.edit(content="❌ Speed test timed out. Please try again later.")
            except Exception as e:
                _log.error(f"Speedtest execution error: {e}")
                await msg.edit(content="❌ Error running speed test. Please try again later.")

        except Exception as e:
            _log.error(f"Speedtest command error: {e}")
            await interaction.followup.send(
                "❌ Error occurred while running speed test.", ephemeral=True
            )

    async def _fetch_api_data(
        self, url: str, session: aiohttp.ClientSession
    ) -> dict | None:
        """Helper method to fetch data from API with error handling"""
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    _log.warning(f"API request failed with status {response.status}")
                    return None
        except Exception as e:
            _log.error(f"API fetch error: {e}")
            return None

    @app_commands.command(name="dogimg", description="Get a random dog image.")
    async def dogimg(self, interaction: discord.Interaction):
        """Fetch a random dog image from API"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)

            async with managed_session() as session:
                data = await self._fetch_api_data(
                    "https://dog.ceo/api/breeds/image/random", session
                )

                if data and data.get("status") == "success":
                    embed = discord.Embed(
                        title="🐕 Random Dog",
                        color=discord.Color.orange(),
                        timestamp=discord.utils.utcnow(),
                    )
                    embed.set_image(url=data["message"])
                    embed.set_footer(text="Powered by dog.ceo API")
                    await ctx.send(embed=embed)
                else:
                    await ctx.send(
                        "❌ Failed to fetch dog image. Please try again later.", ephemeral=True
                    )

        except Exception as e:
            _log.error(f"Dog image command error: {e}")
            await interaction.followup.send(
                "❌ Error occurred while fetching dog image.", ephemeral=True
            )

    @app_commands.command(name="catimg", description="Get a random cat image.")
    async def catimg(self, interaction: discord.Interaction):
        """Fetch a random cat image from API"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)

            async with managed_session() as session:
                data = await self._fetch_api_data(
                    "https://api.thecatapi.com/v1/images/search", session
                )

                if data and len(data) > 0:
                    embed = discord.Embed(
                        title="🐱 Random Cat",
                        color=discord.Color.purple(),
                        timestamp=discord.utils.utcnow(),
                    )
                    embed.set_image(url=data[0]["url"])
                    embed.set_footer(text="Powered by thecatapi.com")
                    await ctx.send(embed=embed)
                else:
                    await ctx.send(
                        "❌ Failed to fetch cat image. Please try again later.", ephemeral=True
                    )

        except Exception as e:
            _log.error(f"Cat image command error: {e}")
            await interaction.followup.send(
                "❌ Error occurred while fetching cat image.", ephemeral=True
            )

    @app_commands.command(name="meme", description="Get a random meme.")
    async def meme(self, interaction: discord.Interaction):
        """Fetch a random meme from API"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)

            async with managed_session() as session:
                data = await self._fetch_api_data(
                    "https://meme-api.com/gimme", session
                )

                if data and not data.get("nsfw", True):  # Only SFW memes
                    embed = discord.Embed(
                        title=f"😂 {data.get('title', 'Random Meme')}",
                        color=discord.Color.gold(),
                        timestamp=discord.utils.utcnow(),
                    )
                    embed.set_image(url=data["url"])
                    embed.add_field(
                        name="👍 Upvotes", value=data.get("ups", "N/A"), inline=True
                    )
                    embed.add_field(
                        name="📱 Subreddit",
                        value=f"r/{data.get('subreddit', 'unknown')}",
                        inline=True,
                    )
                    embed.set_footer(text="Powered by meme-api.com")
                    await ctx.send(embed=embed)
                else:
                    await ctx.send(
                        "❌ Failed to fetch appropriate meme. Please try again.", ephemeral=True
                    )

        except Exception as e:
            _log.error(f"Meme command error: {e}")
            await interaction.followup.send(
                "❌ Error occurred while fetching meme.", ephemeral=True
            )

    # Keep old prefix commands for backward compatibility (deprecated)
    @commands.command(hidden=True)
    async def hello_old(self, ctx, member: discord.Member | None = None, *args):
        """[DEPRECATED] Use /hello instead"""
        await ctx.send("⚠️ This command is deprecated. Please use `/hello` instead.")

    @commands.command(hidden=True)
    async def currency_old(self, ctx, *args):
        """[DEPRECATED] Use /currency instead"""
        await ctx.send("⚠️ This command is deprecated. Please use `/currency` instead.")

    @commands.command(hidden=True)
    async def speedtest_old(self, ctx):
        """[DEPRECATED] Use /speedtest instead"""
        await ctx.send("⚠️ This command is deprecated. Please use `/speedtest` instead.")


async def setup(bot):
    await bot.add_cog(Greeting(bot))

import asyncio
import logging
import os
import gtts
from gtts import gTTS
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
from .tts_constants import *

langs = gtts.tts.tts_langs()
_log = logging.getLogger(__name__)

class TTS(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.temp_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), TEMP_FOLDER)
        self.full_path = os.path.join(self.temp_path, TEMP_FILENAME)
        
        # Ensure temp folder exists
        os.makedirs(self.temp_path, exist_ok=True)

    async def tts(self, ctx: commands.Context, text: str, lang: str = DEFAULT_LANG) -> None:
        loop = asyncio.get_event_loop()
        try:
            tts = await loop.run_in_executor(None, lambda: gTTS(text=text, lang=lang))
            await loop.run_in_executor(None, tts.save, self.full_path)
            _log.info(f"'voice.mp3' file has been saved at {self.temp_path}.")
            
            source = discord.FFmpegPCMAudio(self.full_path)
            ctx.voice_client.play(source)
        except Exception as e:
            _log.error(f"Error in TTS: {e}")
            await ctx.send("There was an error processing your TTS request.")

    @app_commands.command(name="speak", description="Convert text to speech and play it in voice channel")
    @app_commands.describe(
        text="The text to convert to speech",
        language="Language code for speech (use /languages to see available options)"
    )
    async def speak(self, interaction: discord.Interaction, text: str, language: Optional[str] = DEFAULT_LANG):
        """Convert text to speech and play it in the voice channel"""
        try:
            await interaction.response.defer(thinking=True)
            ctx = await self.bot.get_context(interaction)
            
            # Check if user is in voice channel
            if not ctx.author.voice:
                await ctx.send("❌ You need to be in a voice channel to use TTS.", ephemeral=True)
                return
            
            # Connect to voice channel if not connected
            if not ctx.voice_client:
                try:
                    await ctx.author.voice.channel.connect(self_deaf=True)
                    _log.info(f"Connected to voice channel for TTS: {ctx.author.voice.channel.name}")
                except Exception as e:
                    _log.error(f"Failed to connect to voice channel: {e}")
                    await ctx.send("❌ Failed to connect to voice channel.", ephemeral=True)
                    return
            
            # Check if bot is already speaking
            if ctx.voice_client.is_playing():
                await ctx.send("❌ Please wait until the current TTS is finished.", ephemeral=True)
                return
            
            # Validate language
            if language and language not in langs.keys():
                await ctx.send(f"❌ Invalid language code '{language}'. Use `/languages` to see available options.", ephemeral=True)
                return
            
            # Check text length
            if len(text) > MAX_TTS_CHARS:
                truncated = text[:TRUNCATED_CHARS]
                _log.error(f"TTS text too long: '{truncated}...' ({MAX_TTS_CHARS} limit)")
                await ctx.send(f"❌ The text exceeds the {MAX_TTS_CHARS} character limit.", ephemeral=True)
                return
            
            # Use default language if not specified
            lang = language or DEFAULT_LANG
            
            _log.info(f"TTS request: '{text[:TRUNCATED_CHARS]}...' in '{lang}' by {ctx.author.id}")
            
            # Send confirmation
            await ctx.send(f"🔊 Speaking: `{text[:50]}{'...' if len(text) > 50 else ''}` in **{langs.get(lang, lang)}**")
            
            # Process TTS
            await self.tts(ctx, text, lang)
            
        except Exception as e:
            _log.error(f"Error in speak command: {e}")
            await interaction.followup.send("❌ An error occurred while processing your TTS request.", ephemeral=True)

    @app_commands.command(name="languages", description="Show available TTS languages")
    async def languages(self, interaction: discord.Interaction):
        """Show a list of available TTS languages"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)
            
            # Create embed with language list
            embed = discord.Embed(
                title="🌍 Available TTS Languages",
                description="Use the language code with `/speak` command",
                color=discord.Color.blue()
            )
            
            # Group languages for better display
            lang_list = []
            for code, name in sorted(langs.items()):
                lang_list.append(f"`{code}` - {name}")
            
            # Split into chunks to avoid embed field limits
            chunk_size = 20
            for i in range(0, len(lang_list), chunk_size):
                chunk = lang_list[i:i + chunk_size]
                field_name = f"Languages ({i+1}-{min(i+chunk_size, len(lang_list))})"
                embed.add_field(
                    name=field_name,
                    value="\n".join(chunk),
                    inline=True
                )
            
            embed.set_footer(text=f"Total: {len(langs)} languages available")
            await ctx.send(embed=embed)
            
        except Exception as e:
            _log.error(f"Error in languages command: {e}")
            await interaction.followup.send("❌ An error occurred while fetching language list.", ephemeral=True)

    @app_commands.command(name="stop-tts", description="Stop current TTS playback")
    async def stop_tts(self, interaction: discord.Interaction):
        """Stop current TTS playback"""
        try:
            await interaction.response.defer()
            ctx = await self.bot.get_context(interaction)
            
            if not ctx.voice_client:
                await ctx.send("❌ Bot is not connected to a voice channel.", ephemeral=True)
                return
            
            if ctx.voice_client.is_playing():
                ctx.voice_client.stop()
                await ctx.send("🛑 TTS playback stopped.")
            else:
                await ctx.send("❌ No TTS is currently playing.", ephemeral=True)
                
        except Exception as e:
            _log.error(f"Error in stop_tts command: {e}")
            await interaction.followup.send("❌ An error occurred while stopping TTS.", ephemeral=True)

    # Keep the old prefix commands for backward compatibility (deprecated)
    @commands.command(hidden=True)
    async def s(self, ctx: commands.Context, *text: str):
        """[DEPRECATED] Use /speak instead"""
        await ctx.send("⚠️ This command is deprecated. Please use `/speak` instead.")

    @commands.command(hidden=True)
    async def lang(self, ctx: commands.Context):
        """[DEPRECATED] Use /languages instead"""
        await ctx.send("⚠️ This command is deprecated. Please use `/languages` instead.")

async def setup(bot):
    await bot.add_cog(TTS(bot))

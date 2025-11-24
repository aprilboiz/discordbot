import discord
from discord.ext import commands
from cogs.music.controller import GuildMusicManager

async def ensure_same_channel(ctx: commands.Context) -> bool:
    """
    Ensures that the bot and the user are in the same voice channel.
    
    Args:
        ctx (commands.Context): The context of the command.
    
    Returns:
        bool: True if in the same channel, False otherwise.
    """
    if ctx.voice_client and ctx.voice_client.channel != ctx.author.voice.channel:
        embed = discord.Embed(
            color=discord.Color.red(),
            description=f"This bot is already in {ctx.voice_client.channel.name}. Use **/come** command if you want it here instead!"
        )
        await ctx.send(embed=embed)
        return False
    return True

def get_or_create_audio(bot: commands.Bot, guild_id: int) -> GuildMusicManager:
    """
    Gets an existing GuildMusicManager instance for the guild or creates a new one.
    
    Args:
        bot (commands.Bot): The bot instance.
        guild_id (int): The ID of the guild.
    
    Returns:
        GuildMusicManager: The GuildMusicManager instance for the guild.
    """
    player_manager = bot.player_manager
    if guild_id not in player_manager.players:
        player_manager.players[guild_id] = GuildMusicManager(bot, guild_id)
    return player_manager.players[guild_id]

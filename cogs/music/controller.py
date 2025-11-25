from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional, Literal, Union

import discord
from discord.ext import commands
import wavelink

from cogs.music.view.view import MusicView
from cogs.components.discord_embed import Embed
from utils import Timer, convert_to_second, get_time

_log = logging.getLogger(__name__)

class GuildMusicManager:
    """
    Manages music playback and queue for a specific guild using Wavelink.
    """
    def __init__(self, bot: commands.Bot, guild_id: int) -> None:
        self.bot = bot
        self.guild_id = guild_id
        self.ctx: Optional[commands.Context] = None
        self.timer: Optional[Timer] = None
        
        # Wavelink handles queue, so we just manage the player interface
        # However, we need to store settings
        self.settings_manager = bot.settings_manager

    async def get_player(self) -> Optional[wavelink.Player]:
        """Get the Wavelink player for this guild."""
        player = wavelink.Pool.get_node().get_player(self.guild_id)
        return player

    def destroy(self) -> None:
        if self.timer:
            self.timer.cancel()
        # Disconnect happens in stop command usually

    async def process_query(
        self, ctx: commands.Context, query: str, priority: bool = False
    ) -> None:
        self.ctx = ctx
        player = ctx.voice_client
        if not player:
            try:
                player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
            except Exception as e:
                await ctx.send(embed=Embed().error(f"Failed to connect: {e}"))
                return

        # Enforce max queue size
        max_queue_size = self.settings_manager.get(ctx.guild.id, "max_queue_size")
        if player.queue.count >= max_queue_size:
             await ctx.send(embed=Embed().error(f"Queue is full! (Max: {max_queue_size})"))
             return

        try:
            tracks: wavelink.Search = await wavelink.Playable.search(query)
        except Exception as e:
             await ctx.send(embed=Embed().error(f"Search failed: {e}"))
             return

        if not tracks:
            await ctx.send(embed=Embed().error("No tracks found."))
            return

        if isinstance(tracks, wavelink.Playlist):
            # Playlist
            added = 0
            max_duration = self.settings_manager.get(ctx.guild.id, "max_track_duration")
            
            for track in tracks:
                if track.length / 1000 > max_duration:
                    continue

                if player.queue.count + added >= max_queue_size:
                    break

                if priority:
                    player.queue.put_at(0, track) # Not exact priority batch insert but simple
                else:
                    await player.queue.put_wait(track)
                added += 1

            await ctx.send(embed=Embed().ok(f"Added playlist **{tracks.name}** ({added} tracks)"))
        else:
            track = tracks[0]
            # Check duration
            max_duration = self.settings_manager.get(ctx.guild.id, "max_track_duration")
            if track.length / 1000 > max_duration:
                 await ctx.send(embed=Embed().error(f"Track too long! (Max: {max_duration}s)"))
                 return

            if priority:
                player.queue.put_at(0, track)
                await ctx.send(embed=Embed().ok(f"Added **{track.title}** to the top of the queue."))
            else:
                await player.queue.put_wait(track)
                await ctx.send(embed=Embed().ok(f"Added **{track.title}** to the queue."))

        if not player.playing:
            await player.play(player.queue.get(), volume=100)

    async def process_search(
        self,
        ctx: commands.Context,
        query: str,
        provider: Optional[Literal["youtube", "soundcloud"]] = "youtube",
    ) -> None:
        # Wavelink handles providers via search prefixes
        source = wavelink.TrackSource.YouTube if provider == "youtube" else wavelink.TrackSource.SoundCloud
        # Wavelink 3.x search takes source arg? or prefix
        # Actually Playable.search(query, source=...)

        # Simple implementation: just use process_query but maybe show selector?
        # For now, standard play behavior
        await self.process_query(ctx, query) # Simplification for now

    async def process_trending(self, ctx: commands.Context) -> None:
        # Use wavelink to search for trending?
        # "ytsearch:trending music"
        await self.process_query(ctx, "ytsearch:trending music")

    def on_channel_empty(self):
        if self.ctx:
             self.timer = Timer(self.timeout_handle, self.ctx, timeout=60)
             if self.ctx.voice_client and self.ctx.voice_client.playing:
                 self.ctx.voice_client.pause(True)
                 self.paused_due_to_afk = True # Need to store this state

    def on_channel_filled(self):
        if self.timer:
            self.timer.cancel()
        if hasattr(self, 'paused_due_to_afk') and self.paused_due_to_afk:
             if self.ctx.voice_client:
                 self.ctx.voice_client.pause(False)
             self.paused_due_to_afk = False

    async def timeout_handle(self, ctx):
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
        await ctx.send(embed=Embed().error("Disconnected due to inactivity."))

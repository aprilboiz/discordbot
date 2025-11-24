import asyncio
from typing import TYPE_CHECKING, List, Optional

import discord
from cogs.components.discord_embed import Embed
from cogs.music.core.playlist import PlayList
from discord.ext import commands
from utils import convert_to_second, convert_to_time, get_time

if TYPE_CHECKING:
    from cogs.music.controller import GuildMusicManager
    from cogs.music.core.song import Song, SongMeta


class PlaylistManager:
    def __init__(self):
        self.playlist: PlayList = PlayList()
        self.current_song: Optional[Song] = None
        self.prev_song: Optional[Song] = None
        self.current_song_start_time: float = 0
        self.current_song_duration: float = 0

    async def add_songs(self, songs: List['SongMeta'], priority: bool = False) -> None:
        add_method = self.playlist.add_next if priority else self.playlist.add
        await asyncio.gather(*(add_method(song) for song in songs))
        self.playlist.trigger_update_all_song_meta()

    def calculate_wait_time(self, latest_song: 'SongMeta', priority: bool) -> float:
        current_time = convert_to_second(get_time())
        time_wait = self.current_song_duration - (
            current_time - self.current_song_start_time
        )
        if not priority:
            time_wait += convert_to_second(
                self.playlist.time_wait(self.playlist.index(latest_song))
            )
        return time_wait

    def get_song_added_embed(
        self, ctx: commands.Context, latest_song: 'SongMeta', priority: bool
    ) -> Optional[discord.Embed]:
        if self.playlist.size() > 0 and self.playlist.index(latest_song) is not None:
            time_wait = self.calculate_wait_time(latest_song, priority)
            return Embed(ctx).add_song(
                latest_song,
                position=self.playlist.index(latest_song) + 1,
                timewait=convert_to_time(time_wait),
            )
        return None


class PlayerManager:
    """
    A class that manages the players for the music controller.

    Attributes:
        players (dict[int, GuildMusicManager]): A dictionary that stores the players, where the key is the guild ID
        and the value is an instance of the GuildMusicManager class.
    """

    def __init__(self) -> None:
        self.players: dict[int, 'GuildMusicManager'] = {}

    def get_player(self, guild_id: int) -> Optional['GuildMusicManager']:
        return self.players.get(guild_id)

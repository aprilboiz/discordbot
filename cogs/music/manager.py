from typing import Optional
from cogs.music.controller import GuildMusicManager

class PlayerManager:
    """
    A class that manages the players for the music controller.
    """

    def __init__(self) -> None:
        self.players: dict[int, 'GuildMusicManager'] = {}

    def get_player(self, guild_id: int) -> Optional['GuildMusicManager']:
        return self.players.get(guild_id)

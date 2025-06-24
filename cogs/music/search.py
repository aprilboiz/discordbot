import logging
import urllib.parse
from typing import List, Literal, Optional

from cogs.music.core.song import SongMeta
from cogs.music.extractor import ExtractorFactory
from discord.ext import commands

_log = logging.getLogger(__name__)


class Search:
    "Methods: query"

    def is_url(self, url_string: str) -> bool:
        try:
            # Attempt to parse the URL
            result = urllib.parse.urlparse(url_string)
            # Check if all parts are defined (excluding fragment)
            return all([result.scheme, result.netloc, result.path])
        except (ValueError, AttributeError):
            # If parsing fails, the string is not a URL
            return False

    def is_soundcloud(self, url: str) -> bool:
        parsed_url = urllib.parse.urlparse(url)
        netloc = parsed_url.netloc
        domain = netloc.split(".")[0]
        return domain.lower() == "soundcloud"

    def is_youtube(self, url: str) -> bool:
        parsed_url = urllib.parse.urlparse(url)
        netloc = parsed_url.netloc
        return netloc.lower() == "www.youtube.com" or netloc.lower() == "youtu.be"

    def is_youtube_music(self, url: str) -> bool:
        parsed_url = urllib.parse.urlparse(url)
        netloc = parsed_url.netloc
        return netloc.lower() == "music.youtube.com"

    def is_spotify(self, url: str) -> bool:
        parsed_url = urllib.parse.urlparse(url)
        netloc = parsed_url.netloc
        return netloc.lower() == "open.spotify.com"

    def is_soundcloud_playlist(self, url: str) -> bool:
        """Check if the URL is a SoundCloud playlist, set, or album."""
        if not self.is_soundcloud(url):
            return False
        return "/sets/" in url or "/albums/" in url or "/playlists/" in url

    async def query(
        self,
        query: str,
        ctx: commands.Context,
        priority: bool = False,
        provider: Optional[Literal["youtube", "soundcloud", "spotify"]] = None,
        limit: int = 1,
    ) -> Optional[List[SongMeta]]:
        """
        Queries the provided string to fetch song metadata from either YouTube or SoundCloud.

        If the query is a URL, it determines whether it is a SoundCloud or YouTube link and fetches the data
        accordingly.

        If the query is not a URL, it first attempts to search SoundCloud, and if no results are found, it searches
        YouTube.
        Args:
            query (str): The search string or URL to query.
            ctx (commands.Context): The context in which the command was invoked.
            priority (bool, optional): If True, the order of the songs will be reversed. Defaults to False.
        Returns:
            List[SongMeta] | None: A list of SongMeta objects if songs are found, otherwise None.
        """
        songs = []

        if provider:
            if provider == "youtube":
                songs = await ExtractorFactory.get_extractor("youtube").get_data(
                    query=query, ctx=ctx, is_search=not self.is_url(query), limit=limit
                )
            elif provider == "soundcloud":
                # For SoundCloud, handle playlists properly
                effective_limit = 0 if (limit == 1 and self.is_soundcloud_playlist(query)) else limit
                songs = await ExtractorFactory.get_extractor("soundcloud").get_data(
                    query=query, ctx=ctx, is_search=not self.is_url(query), limit=effective_limit
                )
            elif provider == "spotify":
                songs = await ExtractorFactory.get_extractor("spotify").get_data(
                    query=query, ctx=ctx, is_search=not self.is_url(query), limit=limit
                )
            else:
                _log.error(f"Invalid provider '{provider}'")
                return ValueError(f"Invalid provider '{provider}'")
        else:
            if self.is_url(query):
                if self.is_youtube(query) or self.is_youtube_music(query):
                    if self.is_youtube_music(query):
                        query = query.replace("music.", "")
                    is_playlist = "/playlist?" in query or "&list=" in query
                    songs = await ExtractorFactory.get_extractor("youtube").get_data(
                        query=query, ctx=ctx, is_playlist=is_playlist, limit=limit
                    )
                elif self.is_soundcloud(query):
                    # For SoundCloud playlists/sets, use special handling
                    if self.is_soundcloud_playlist(query):
                        # If limit is 1, it's likely for first song extraction
                        # Use 0 (unlimited) for background processing
                        effective_limit = limit if limit != 1 else 0
                        _log.debug(f"SoundCloud playlist detected, using limit: {effective_limit}")
                    else:
                        effective_limit = limit
                    
                    songs = await ExtractorFactory.get_extractor("soundcloud").get_data(
                        query=query, ctx=ctx, limit=effective_limit
                    )
                elif self.is_spotify(query):
                    songs = await ExtractorFactory.get_extractor("spotify").get_data(
                        query=query, ctx=ctx, limit=limit
                    )
                else:
                    _log.error(f"Unsupported URL '{query}'")
                    raise ValueError(f"Unsupported URL '{query}'")
            else:
                songs = await ExtractorFactory.get_extractor("youtube").get_data(
                    query=query, ctx=ctx, is_search=True, limit=limit
                )
                if not songs:
                    songs = await ExtractorFactory.get_extractor("soundcloud").get_data(
                        query=query, ctx=ctx, is_search=True, limit=limit
                    )

        if songs:
            if priority:
                songs.reverse()
            return [song for song in songs if song is not None]
        else:
            _log.error(f"No results were found for the query '{query}'")
            return None

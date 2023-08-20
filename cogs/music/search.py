import asyncio
import datetime
import logging

import constants
import yt_dlp as youtube_dl
from cogs.music.song import Song
from requests import get
from discord.ext import commands

_log = logging.getLogger(__name__)
class Search:
    "Methods: query"

    def __init__(self):
        self.__ydl = youtube_dl.YoutubeDL(constants.YDL_OPTS)

    def __format_upload_date(self, upload_date: str) -> str:
        date = datetime.datetime.strptime(upload_date[:4] + "/" + upload_date[4:6] + "/" + upload_date[6:], "%Y/%m/%d")
        date = date.strftime("%d/%m/%Y")
        return date
        
    def __format_duration(self, duration: str) -> str:
        return str(datetime.timedelta(seconds=duration))

    def __format_view_count(self, view_count: str) -> str:
        view_count = int(view_count)
        return "{:,}".format(view_count)

    async def query(self, query: str, ctx: commands.Context) -> Song:
        """Search by a name or URL.
        
        If success, return Song, else, return -1
        
        return: 
            Song object
        or  int: -1"""
        loop = asyncio.get_event_loop()
        _log.info(f"Searching for '{query}'.")
        
        try:
            get(query) 
        except:
            try:
                video = await loop.run_in_executor(None, lambda: self.__ydl.extract_info(f"ytsearch:{query}", download=False)['entries'][0])
                _log.info(f"Extract info from '{query}' successfully.")
            except Exception as e:
                _log.error(e)
                return -1
        else:
            try:
                video = await loop.run_in_executor(None, lambda: self.__ydl.extract_info(query, download=False))
            except Exception as e:
                _log.error(e)
                return -1
                
        song = Song(
            TITLE=video['title'],
            URL=video['url'],
            CHANNEL=video['channel'],
            VIEW_COUNT=self.__format_view_count(video['view_count']),
            DURATION=self.__format_duration(video['duration']),
            UPLOAD_DATE=self.__format_upload_date(video['upload_date']),
            THUMBNAIL=video['thumbnail'],
            YT_URL=video['webpage_url'],
            CTX=ctx
            )

        return song

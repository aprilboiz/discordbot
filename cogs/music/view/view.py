import discord
from typing import List, Callable, Optional, override
from datetime import datetime

from cogs.music.core.song import SongMeta


class MusicView(discord.ui.View):
    """View for displaying search results"""

    def __init__(self, tracks: List[SongMeta], callback: Callable, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.tracks = tracks
        self.callback = callback
        self.current_page = 0
        self.tracks_per_page = 5
        self.total_pages = (len(tracks) - 1) // self.tracks_per_page + 1
        self.message: Optional[discord.Message] = None

        # Add selection buttons for each track on the page
        self.update_buttons()

    def create_embed(self, is_timeout: bool = False) -> discord.Embed:
        if is_timeout:
            return discord.Embed(
                title="🔍 Search Results",
                description="This search has expired. Please perform a new search.",
                color=discord.Color.red(),
            )

        start_idx = self.current_page * self.tracks_per_page
        end_idx = min(start_idx + self.tracks_per_page, len(self.tracks))
        current_tracks = self.tracks[start_idx:end_idx]

        embed = discord.Embed(
            title="🔍 Search Results",
            description="Select a track to add to the queue:",
            color=discord.Color.blue(),
        )

        for idx, track in enumerate(current_tracks, start=1):
            duration = track.duration or "??:??"
            title = track.title or "Unknown"
            author = track.author or "Unknown"

            # Truncate long titles
            if len(title) > 35:
                title = title[:32] + "..."

            embed.add_field(
                name=f"{start_idx + idx}. {title}",
                value=f"👤 {author} • ⏱️ {duration}",
                inline=False,
            )

        embed.set_footer(
            text=f"Page {self.current_page + 1}/{self.total_pages} • {len(self.tracks)} results found"
        )
        return embed

    def update_buttons(self):
        # Clear all existing items
        self.clear_items()

        # Add navigation buttons
        self.add_item(self.previous_button)
        self.add_item(self.next_button)

        # Add selection buttons for current page
        start_idx = self.current_page * self.tracks_per_page
        end_idx = min(start_idx + self.tracks_per_page, len(self.tracks))

        for i in range(start_idx, end_idx):
            button = discord.ui.Button(
                style=discord.ButtonStyle.green,
                label=str(i - start_idx + 1),
                custom_id=f"select_{i}",
                row=1,
            )
            button.callback = lambda interaction, track_idx=i: self.select_track(
                interaction, track_idx
            )
            self.add_item(button)

    async def select_track(self, interaction: discord.Interaction, track_idx: int):
        selected_track = self.tracks[track_idx]

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        # Call the provided callback with the selected track
        await self.callback(interaction, selected_track)
        # Stop listening for further interactions
        self.stop()

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.grey, row=0)
    async def previous_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(
                embed=self.create_embed(), view=self
            )
        else:
            await interaction.response.defer()

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.grey, row=0)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(
                embed=self.create_embed(), view=self
            )
        else:
            await interaction.response.defer()

    @override
    async def on_timeout(self) -> None:
        """Handles view timeout"""
        # Disable all buttons
        for item in self.children:
            item.disabled = True

        try:
            # Update the message with disabled buttons and timeout embed
            await self.message.edit(embed=self.create_embed(is_timeout=True), view=self)
        except discord.errors.NotFound:
            # Message might have been deleted
            pass


class QueueView(discord.ui.View):
    """Enhanced view for displaying the music queue with detailed information"""

    def __init__(
        self,
        tracks: List[SongMeta],
        callback: Callable,
        current_song: Optional[object] = None,
        priority_count: int = 0,
        total_duration: str = "0:00",
        timeout: int = 180,
    ):
        super().__init__(timeout=timeout)
        self.tracks = tracks
        self.callback = callback
        self.current_song = current_song
        self.priority_count = priority_count
        self.total_duration = total_duration
        self.current_page = 0
        self.tracks_per_page = 8  # Show more songs for queue
        self.total_pages = max(1, (len(tracks) - 1) // self.tracks_per_page + 1)
        self.message: Optional[discord.Message] = None

        # Add selection buttons for each track on the page
        self.update_buttons()

    def create_embed(self, is_timeout: bool = False) -> discord.Embed:
        if is_timeout:
            return discord.Embed(
                title="🎵 Queue",
                description="Queue view has expired. Use `/queue` to view again.",
                color=discord.Color.red(),
            )

        if not self.tracks:
            embed = discord.Embed(
                title="🎵 Queue is Empty",
                description="No songs in the queue. Use `/play` to add some music!",
                color=discord.Color.orange(),
            )
            return embed

        start_idx = self.current_page * self.tracks_per_page
        end_idx = min(start_idx + self.tracks_per_page, len(self.tracks))
        current_tracks = self.tracks[start_idx:end_idx]

        # Create embed with queue information
        embed = discord.Embed(
            title="🎵 Music Queue",
            color=discord.Color.green(),
        )

        # Add queue statistics
        stats_text = f"📊 **Queue Stats:**\n"
        stats_text += (
            f"🎵 **{len(self.tracks)}** songs • ⏱️ **{self.total_duration}** total\n"
        )

        if self.priority_count > 0:
            stats_text += f"🔥 **{self.priority_count}** priority songs\n"

        if self.current_song:
            current_title = getattr(self.current_song, "title", None) or "Unknown"
            if len(current_title) > 30:
                current_title = current_title[:27] + "..."
            stats_text += f"▶️ **Now Playing:** {current_title}"

        embed.description = stats_text

        # Add songs
        queue_text = ""
        for idx, track in enumerate(current_tracks):
            display_idx = start_idx + idx + 1
            duration = track.duration or "??:??"
            title = track.title or "Unknown"
            author = track.author or "Unknown"

            # Truncate long titles and authors
            if len(title) > 30:
                title = title[:27] + "..."
            if len(author) > 20:
                author = author[:17] + "..."

            # Add priority indicator
            priority_indicator = ""
            if display_idx <= self.priority_count:
                priority_indicator = " 🔥"

            # Add position and song info
            queue_text += f"`{display_idx:2d}.` **{title}**{priority_indicator}\n"
            queue_text += f"     👤 {author} • ⏱️ {duration}\n\n"

        if queue_text:
            embed.add_field(name="📋 Up Next:", value=queue_text, inline=False)

        # Add instructions
        instructions = "🎯 Click a number to move that song to the top of the queue"
        embed.add_field(name="💡 Instructions:", value=instructions, inline=False)

        embed.set_footer(
            text=f"Page {self.current_page + 1}/{self.total_pages} • Updated {datetime.now().strftime('%H:%M:%S')}"
        )

        return embed

    def update_buttons(self):
        # Clear all existing items
        self.clear_items()

        # Row 0: Navigation and action buttons
        self.add_item(self.previous_button)
        self.add_item(self.refresh_button)
        self.add_item(self.next_button)

        # Row 1: Selection buttons for songs
        start_idx = self.current_page * self.tracks_per_page
        end_idx = min(start_idx + self.tracks_per_page, len(self.tracks))

        for i in range(start_idx, end_idx):
            display_num = i + 1
            button_style = (
                discord.ButtonStyle.red
                if display_num <= self.priority_count
                else discord.ButtonStyle.secondary
            )

            button = discord.ui.Button(
                style=button_style,
                label=str(display_num),
                custom_id=f"queue_select_{i}",
                row=1 if (i - start_idx) < 5 else 2,  # Use two rows for buttons
            )
            button.callback = lambda interaction, track_idx=i: self.select_track(
                interaction, track_idx
            )
            self.add_item(button)

    async def select_track(self, interaction: discord.Interaction, track_idx: int):
        selected_track = self.tracks[track_idx]

        # Don't disable buttons for queue - allow multiple interactions
        await interaction.response.defer()

        # Call the provided callback with the selected track
        await self.callback(interaction, selected_track)

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.grey, row=0)
    async def previous_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(
                embed=self.create_embed(), view=self
            )
        else:
            await interaction.response.defer()

    @discord.ui.button(label="🔄", style=discord.ButtonStyle.green, row=0)
    async def refresh_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Refresh the queue display
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.grey, row=0)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(
                embed=self.create_embed(), view=self
            )
        else:
            await interaction.response.defer()

    @override
    async def on_timeout(self) -> None:
        """Handles view timeout"""
        # Disable all buttons
        for item in self.children:
            item.disabled = True

        try:
            # Update the message with disabled buttons and timeout embed
            await self.message.edit(embed=self.create_embed(is_timeout=True), view=self)
        except discord.errors.NotFound:
            # Message might have been deleted
            pass

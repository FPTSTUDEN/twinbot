"""Voice-related slash commands for the Discord bot.

Mirrors the access_control module's style: module-level functions and a
small `setup(bot)` entry point that registers the commands on the bot.
"""

import os
import asyncio
import discord
from discord.ext import commands

from access_control import check_permission


# Directory where local MP3 files live / where downloads are saved.
MUSIC_DIR = os.getenv("MUSIC_DIR", "music")


def _resolve_local_path(name_or_path: str) -> str:
    """Resolve a user-provided file name or path to a local MP3 path."""
    candidate = name_or_path.strip()

    if os.path.dirname(candidate):
        path = candidate
    else:
        path = os.path.join(MUSIC_DIR, candidate)

    if not path.lower().endswith(".mp3"):
        path += ".mp3"

    return path


async def _download_youtube_mp3(query: str, output_path: str) -> bool:
    """Download the best match for `query` from YouTube as an MP3."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    def _run() -> bool:
        try:
            import yt_dlp
        except ImportError:
            print("[ERROR] yt-dlp is not installed. Run: pip install yt-dlp")
            return False

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_path.rsplit(".", 1)[0] + ".%(ext)s",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "default_search": "ytsearch1",
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([query])
            return os.path.isfile(output_path)
        except Exception as e:
            print(f"[ERROR] yt-dlp download failed for '{query}': {e}")
            return False

    return await asyncio.to_thread(_run)


async def _ensure_voice_client(interaction: discord.Interaction) -> discord.VoiceClient:
    """Connect to (or move to) the user's voice channel and return the client."""
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        voice_client = await interaction.user.voice.channel.connect()
    elif voice_client.channel != interaction.user.voice.channel:
        await voice_client.move_to(interaction.user.voice.channel)
    return voice_client


def _play_file(voice_client: discord.VoiceClient, path: str) -> None:
    """Play a local file, stopping anything already playing."""
    if voice_client.is_playing():
        voice_client.stop()

    audio_source = discord.FFmpegPCMAudio(path)
    voice_client.play(
        audio_source,
        after=lambda e: (
            print(f"Finished playing: {e}") if e else print("Playback complete.")
        ),
    )


class DownloadConfirmView(discord.ui.View):
    """Buttons shown when a requested MP3 isn't found locally.

    Only the user who invoked /play may press the buttons.
    """

    def __init__(self, author_id: int, query: str, local_path: str, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.query = query
        self.local_path = local_path

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who ran `/play` can use these buttons.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        # Disable the buttons when the view expires.
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Download", style=discord.ButtonStyle.success, emoji="⬇️")
    async def download(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Disable the buttons and update the prompt to show progress.
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"⬇️ Downloading **\"{self.query}\"** from YouTube...",
            view=self,
        )

        success = await _download_youtube_mp3(self.query, self.local_path)

        if not success or not os.path.isfile(self.local_path):
            await interaction.followup.send(
                f"❌ Failed to download **\"{self.query}\"** from YouTube."
            )
            return

        # Connect to voice and play.
        voice_client = await _ensure_voice_client(interaction)
        _play_file(voice_client, self.local_path)

        await interaction.followup.send(
            f"✅ Downloaded `{os.path.basename(self.local_path)}` and now playing 🎵"
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="❌ Cancelled. Nothing was downloaded.", view=self
        )


async def join(interaction: discord.Interaction):
    if not await check_permission(interaction, "join"):
        return

    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message(
            "You must be in a voice channel to use this command!",
            ephemeral=True,
        )
        return

    channel = interaction.user.voice.channel
    if interaction.guild.voice_client is not None:
        await interaction.guild.voice_client.move_to(channel)
    else:
        await channel.connect()

    await interaction.response.send_message(f"Joined **{channel.name}**! 🔊")


async def play(interaction: discord.Interaction, file_path: str):
    if not await check_permission(interaction, "play"):
        return

    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message(
            "You must be in a voice channel first!", ephemeral=True
        )
        return

    local_path = _resolve_local_path(file_path)

    # Missing file → offer the download via buttons.
    if not os.path.isfile(local_path):
        query = os.path.splitext(os.path.basename(file_path))[0]
        view = DownloadConfirmView(
            author_id=interaction.user.id,
            query=query,
            local_path=local_path,
        )
        await interaction.response.send_message(
            f"`{os.path.basename(local_path)}` wasn't found locally.\n"
            f"Should I download **\"{query}\"** from YouTube as an MP3?",
            view=view,
        )
        return

    # File exists → connect and play immediately.
    await interaction.response.defer()
    voice_client = await _ensure_voice_client(interaction)
    _play_file(voice_client, local_path)
    await interaction.followup.send(
        f"Playing: `{os.path.basename(local_path)}` 🎵"
    )


async def leave(interaction: discord.Interaction):
    if not await check_permission(interaction, "leave"):
        return

    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
        await interaction.response.send_message("Disconnected from voice!")
    else:
        await interaction.response.send_message(
            "I'm not in a voice channel.", ephemeral=True
        )


def setup(bot: commands.Bot) -> None:
    """Register the voice commands on the given bot instance."""
    bot.tree.add_command(
        discord.app_commands.Command(
            name="join",
            description="Joins your voice channel",
            callback=join,
        )
    )
    bot.tree.add_command(
        discord.app_commands.Command(
            name="play",
            description="Plays a local audio file, or downloads it from YouTube if missing",
            callback=play,
        )
    )
    bot.tree.add_command(
        discord.app_commands.Command(
            name="leave",
            description="Disconnects from the voice channel",
            callback=leave,
        )
    )
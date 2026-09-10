import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

import access_control
from access_control import check_permission, log_permissions

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Setup Intents
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Loaded permissions from '{access_control.PERMISSIONS_FILE}':")
    log_permissions()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


# Slash Command Example: /ping
@bot.tree.command(name="ping", description="Replies with Pong!")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! 🏓")


# Slash Command with an Argument: /greet
@bot.tree.command(name="greet", description="Greets a user")
async def greet(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.send_message(f"Hello, {user.mention}!")


# Command: Join the user's current voice channel
@bot.tree.command(name="join", description="Joins your voice channel")
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


# Command: Play a local audio file
@bot.tree.command(
    name="play", description="Plays a local audio file in your voice channel"
)
async def play(interaction: discord.Interaction, file_path: str):
    if not await check_permission(interaction, "play"):
        return

    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message(
            "You must be in a voice channel first!", ephemeral=True
        )
        return

    if not os.path.isfile(file_path):
        await interaction.response.send_message(
            f"File not found: `{file_path}`", ephemeral=True
        )
        return

    await interaction.response.defer()

    voice_client = interaction.guild.voice_client
    if voice_client is None:
        voice_client = await interaction.user.voice.channel.connect()
    elif voice_client.channel != interaction.user.voice.channel:
        await voice_client.move_to(interaction.user.voice.channel)

    if voice_client.is_playing():
        voice_client.stop()

    audio_source = discord.FFmpegPCMAudio(file_path)
    voice_client.play(
        audio_source,
        after=lambda e: (
            print(f"Finished playing: {e}") if e else print("Playback complete.")
        ),
    )

    await interaction.followup.send(f"Playing: `{file_path}` 🎵")


# Command: Leave the voice channel
@bot.tree.command(name="leave", description="Disconnects from the voice channel")
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


if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN environment variable not set in .env")
    bot.run(TOKEN)
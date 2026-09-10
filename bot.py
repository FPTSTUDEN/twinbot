import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

import access_control
import voice

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Loaded permissions from '{access_control.PERMISSIONS_FILE}':")
    access_control.log_permissions()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


@bot.tree.command(name="ping", description="Replies with Pong!")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! 🏓")


@bot.tree.command(name="greet", description="Greets a user")
async def greet(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.send_message(f"Hello, {user.mention}!")


# Register voice commands from the voice module
voice.setup(bot)


if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN environment variable not set in .env")
    bot.run(TOKEN)
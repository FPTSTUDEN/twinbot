import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

import access_control
import voice
import search
import files
import interaction_shim
import queue_poller
import status_reporter

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

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


# Wire the shim / poller to the real handlers.
interaction_shim.set_bot(bot)
queue_poller.set_bot(bot)

queue_poller.register_handler("join", voice.join)
queue_poller.register_handler("play", voice.play)
queue_poller.register_handler("leave", voice.leave)
queue_poller.register_handler("search", search.search)
queue_poller.register_handler("files", files.files)


# Register commands so Discord knows they exist. The Gateway won't deliver
# interactions (endpoint URL is set), but sync is still required.
voice.setup(bot)
search.setup(bot)
files.setup(bot)


@bot.tree.command(name="ping", description="Replies with Pong!")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! 🏓")


@bot.tree.command(name="greet", description="Greets a user")
async def greet(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.send_message(f"Hello, {user.mention}!")


@bot.tree.command(
    name="set-status",
    description="Manually set the bot's status flag (handled by the edge Worker)",
    # default_member_permissions=discord.Permissions(manage_guild=True),
)
@discord.app_commands.describe(status="online, offline, or auto")
@discord.app_commands.choices(status=[
    discord.app_commands.Choice(name="online", value="online"),
    discord.app_commands.Choice(name="offline", value="offline"),
    discord.app_commands.Choice(name="auto", value="auto"),
])
async def set_status(
    interaction: discord.Interaction,
    status: discord.app_commands.Choice[str],
):
    # Never actually runs — the Worker intercepts this command before it
    # reaches the Gateway. Exists only so Discord registers the command.
    await interaction.response.send_message(
        "This command is handled by the edge Worker.", ephemeral=True
    )


async def main():
    await asyncio.gather(
        queue_poller.pull_loop(),
        status_reporter.status_loop(),
        bot.start(TOKEN),
    )


if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN environment variable not set in .env")
    asyncio.run(main())
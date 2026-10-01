"""Register the bot's Discord application commands.

Run this module when the command definitions change instead of starting the
long-running bot:

    python worker/register_commands.py

The module can also be imported by ``bot.py`` to attach the same definitions
to the running bot.  Importing it does not connect to Discord or sync commands.
"""

import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv


def setup(bot: commands.Bot) -> None:
    """Attach the application commands to ``bot`` without syncing them."""

    @bot.tree.command(name="ping", description="Replies with Pong!")
    async def ping(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Pong! 🏓")

    @bot.tree.command(name="greet", description="Greets a user")
    async def greet(
        interaction: discord.Interaction, user: discord.Member
    ) -> None:
        await interaction.response.send_message(f"Hello, {user.mention}!")

    @bot.tree.command(
        name="set-status",
        description="Manually set the bot's status flag (handled by the edge Worker)",
    )
    @discord.app_commands.describe(status="online, offline, or auto")
    @discord.app_commands.choices(
        status=[
            discord.app_commands.Choice(name="online", value="online"),
            discord.app_commands.Choice(name="offline", value="offline"),
            discord.app_commands.Choice(name="auto", value="auto"),
        ]
    )
    async def set_status(
        interaction: discord.Interaction,
        status: discord.app_commands.Choice[str],
    ) -> None:
        # The Worker intercepts this command before it reaches the Gateway.
        await interaction.response.send_message(
            "This command is handled by the edge Worker.", ephemeral=True
        )

    @bot.tree.command(
        name="quicksearch",
        description="Quick web/image search handled by the edge Worker",
    )
    @discord.app_commands.describe(
        query="What to search for",
        option="Result type (default: image)",
        limit="Number of results (default: 1)",
    )
    @discord.app_commands.choices(
        option=[
            discord.app_commands.Choice(name="text", value="text"),
            discord.app_commands.Choice(name="image", value="image"),
        ]
    )
    async def quicksearch(
        interaction: discord.Interaction,
        query: str,
        option: discord.app_commands.Choice[str] = None,
        limit: discord.app_commands.Range[int, 1, 10] = 1,
    ) -> None:
        # The Worker intercepts this command before it reaches the Gateway.
        await interaction.response.send_message(
            "This command is handled by the edge Worker.", ephemeral=True
        )

    @bot.tree.command(
        name="chat",
        description="Ask the configured AI assistant a question",
    )
    @discord.app_commands.describe(message="What would you like to ask?")
    async def chat(interaction: discord.Interaction, message: str) -> None:
        # The Worker intercepts this command before it reaches the Gateway.
        await interaction.response.send_message(
            "This command is handled by the edge Worker.", ephemeral=True
        )

    @bot.tree.command(
        name="paper",
        description="Generate a fake paper between two Discord users",
    )
    @discord.app_commands.describe(
        user1="First person",
        type="Paper template",
        user2="Optional second person",
        field="Optional field, discipline, or role",
        achievement="Optional achievement or contribution",
    )
    @discord.app_commands.choices(
        type=[
            discord.app_commands.Choice(name="Marriage", value="marriage"),
            discord.app_commands.Choice(name="Divorce", value="divorce"),
            discord.app_commands.Choice(name="Adoption", value="adoption"),
            discord.app_commands.Choice(name="Friendship", value="friendship"),
            discord.app_commands.Choice(name="Doctorate", value="doctorate"),
            discord.app_commands.Choice(
                name="Employee of the Month", value="employee-of-the-month"
            ),
            discord.app_commands.Choice(name="Nobel Prize", value="nobel"),
        ]
    )
    async def paper(
        interaction: discord.Interaction,
        user1: discord.Member,
        type: discord.app_commands.Choice[str],
        user2: discord.Member = None,
        field: str = None,
        achievement: str = None,
    ) -> None:
        # The Worker intercepts this command before it reaches the Gateway.
        await interaction.response.send_message(
            "This command is handled by the edge Worker.", ephemeral=True
        )


async def _register(token: str) -> None:
    """Connect, sync the command tree once, and disconnect."""

    intents = discord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents)
    setup(bot)

    @bot.event
    async def on_ready() -> None:
        try:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} command(s)")
        finally:
            await bot.close()

    await bot.start(token)


def main() -> None:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise ValueError("DISCORD_TOKEN environment variable not set in .env")
    asyncio.run(_register(token))


if __name__ == "__main__":
    main()
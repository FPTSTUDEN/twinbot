import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import signal

import access_control
import voice
import search
import files
import interaction_shim
import queue_poller
import status_reporter
import register_commands as register_commands

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
register_commands.setup(bot)


async def main():
    tasks = [
        asyncio.create_task(queue_poller.pull_loop(), name="poller"),
        asyncio.create_task(status_reporter.status_loop(), name="status"),
        asyncio.create_task(bot.start(TOKEN), name="bot"),
    ]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown(*_):
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            # Windows fallback
            signal.signal(sig, _request_shutdown)

    try:
        # Wait until either a task dies or Ctrl+C is pressed.
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )
        # If we get here, a task exited on its own — likely an error.
        for t in done:
            exc = t.exception()
            if exc is not None:
                print(f"[main] task {t.get_name()} crashed: {exc!r}")
    except asyncio.CancelledError:
        pass
    finally:
        # Cancel any remaining tasks and let them unwind.
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        # Close the Discord client cleanly.
        if not bot.is_closed():
            await bot.close()

        print("[main] shutdown complete")


if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN environment variable not set in .env")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # asyncio.run re-raises KeyboardInterrupt after cancelling main().
        print("[main] interrupted")
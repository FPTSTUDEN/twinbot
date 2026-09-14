"""Polls Cloudflare Queues for forwarded Discord interactions."""

from __future__ import annotations

import os
import json
import asyncio
import traceback
from typing import Awaitable, Callable
import dotenv

import aiohttp

from interaction_shim import InteractionShim

dotenv.load_dotenv()

CF_ACCOUNT_ID = os.environ["CFL_ACCOUNT_ID"]
CF_QUEUE_ID = os.environ["CFL_QUEUE_ID"]
CF_API_TOKEN = os.environ["CFL_API_TOKEN"]

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "2.0"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))
VISIBILITY_TIMEOUT_MS = int(os.getenv("VISIBILITY_TIMEOUT_MS", "300000"))


PULL_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
    f"/queues/{CF_QUEUE_ID}/messages/pull"
)
ACK_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
    f"/queues/{CF_QUEUE_ID}/messages/ack"
)


_HANDLERS: dict[str, Callable[..., Awaitable[None]]] = {}
_BOT = None


def register_handler(name: str, fn: Callable[..., Awaitable[None]]) -> None:
    _HANDLERS[name] = fn


def set_bot(bot) -> None:
    global _BOT
    _BOT = bot


async def _dispatch(interaction_payload: dict) -> None:
    """Build a shim and call the registered handler with the right args."""
    command_name = interaction_payload.get("data", {}).get("name", "")
    handler = _HANDLERS.get(command_name)
    if handler is None:
        print(f"[poller] No handler for /{command_name}")
        return

    shim = InteractionShim(interaction_payload, _BOT)

    options = interaction_payload.get("data", {}).get("options", []) or []
    kwargs = {opt["name"]: opt.get("value") for opt in options}

    try:
        await handler(shim, **kwargs)
    except TypeError as e:
        print(f"[ERROR] /{command_name} argument mismatch: {e}")
        try:
            await shim.followup.send(
                f"⚠️ Command `/{command_name}` has an argument mismatch.",
                ephemeral=True,
            )
        except Exception:
            pass
    except Exception as e:
        print(f"[ERROR] /{command_name} failed: {e}")
        traceback.print_exc()
        try:
            await shim.followup.send(
                f"⚠️ Command `/{command_name}` failed on the backend.",
                ephemeral=True,
            )
        except Exception:
            pass


async def pull_loop() -> None:
    """Continuously pull batches of interactions and dispatch them."""
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            try:
                async with session.post(
                    PULL_URL,
                    json={
                        "batch_size": BATCH_SIZE,
                        "visibility_timeout_ms": VISIBILITY_TIMEOUT_MS,
                    },
                ) as resp:
                    if resp.status >= 300:
                        body = await resp.text()
                        print(f"[poller] Pull failed: {resp.status} {body}")
                        await asyncio.sleep(POLL_INTERVAL)
                        continue
                    data = await resp.json()

                result = data.get("result", {})
                messages = result.get("messages", [])

                if not messages:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                ack_leases = []
                for msg in messages:
                    body = msg.get("body", {})
                    if isinstance(body, str):
                        try:
                            body = json.loads(body)
                        except json.JSONDecodeError:
                            print(f"[poller] Skipping non-JSON message {msg['id']}")
                            continue

                    interaction = body.get("interaction")
                    if interaction:
                        asyncio.create_task(_dispatch(interaction))

                    ack_leases.append({"lease_id": msg["lease_id"]})

                if ack_leases:
                    async with session.post(
                        ACK_URL,
                        json={"acks": ack_leases},
                    ) as ack_resp:
                        if ack_resp.status >= 300:
                            body = await ack_resp.text()
                            print(f"[poller] Ack failed: {ack_resp.status} {body}")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[poller] Unexpected error: {e}")

            await asyncio.sleep(POLL_INTERVAL)
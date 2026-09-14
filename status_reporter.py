"""Maintains the VPS status flag in Cloudflare KV.

Writes "online" with a TTL on startup and refreshes it periodically.
Writes "offline" (no TTL) on graceful shutdown.

If the KV value is already "offline" (set manually via /set-status),
the reporter respects that and does not overwrite it. Use /set-status
auto to clear the override and let the reporter take over again.
"""

from __future__ import annotations

import os
import signal
import asyncio

import aiohttp

import dotenv
dotenv.load_dotenv()


CF_ACCOUNT_ID = os.environ["CFL_ACCOUNT_ID"]
CF_KV_NAMESPACE_ID = os.environ["CF_KV_NAMESPACE_ID"]
CF_API_TOKEN = os.environ["CFL_API_TOKEN"]

REFRESH_INTERVAL = float(os.getenv("VPS_STATUS_REFRESH", "60"))
TTL_SECONDS = int(os.getenv("VPS_STATUS_TTL", "90"))

STATUS_KEY = "vps_status"

KV_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
    f"/storage/kv/namespaces/{CF_KV_NAMESPACE_ID}/values/{STATUS_KEY}"
)


async def _read_status(session: aiohttp.ClientSession) -> str | None:
    async with session.get(KV_URL) as resp:
        if resp.status == 404:
            return None
        if resp.status >= 300:
            body = await resp.text()
            print(f"[status] read failed: {resp.status} {body}")
            return None
        return (await resp.text()).strip()


async def _write_status(
    session: aiohttp.ClientSession, value: str, ttl: int | None = None
) -> None:
    params = {}
    if ttl is not None:
        params["expiration_ttl"] = str(ttl)
    async with session.put(
        KV_URL,
        params=params,
        data=value.encode(),
        headers={"Content-Type": "text/plain"},
    ) as resp:
        if resp.status >= 300:
            body = await resp.text()
            print(f"[status] write failed: {resp.status} {body}")


async def status_loop() -> None:
    """Run until cancelled, maintaining the KV status flag."""
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _shutdown(*_):
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            signal.signal(sig, _shutdown)

    async with aiohttp.ClientSession(headers=headers) as session:
        # Initial write, unless a manual override is in place.
        current = await _read_status(session)
        # if current == "offline":
        #     print("[status] manual override is 'offline'; skipping startup write")
        # else:
        await _write_status(session, "online", ttl=TTL_SECONDS)
        print(f"[status] marked online (TTL {TTL_SECONDS}s)")

        try:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=REFRESH_INTERVAL)
                except asyncio.TimeoutError:
                    pass

                if stop.is_set():
                    break

                # Before refreshing, check for a manual override.
                current = await _read_status(session)
                if current == "offline":
                    print("[status] manual override is 'offline'; skipping refresh")
                    continue

                await _write_status(session, "online", ttl=TTL_SECONDS)
                print(f"[status] refreshed online (TTL {TTL_SECONDS}s)")

        finally:
            # Graceful shutdown: clear the online flag so the Worker
            # stops deferring commands immediately.
            await _write_status(session, "offline")
            print("[status] marked offline")
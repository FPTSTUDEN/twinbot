"""Minimal shim that mimics `discord.Interaction` for HTTP-forwarded payloads.

The existing command handlers use a small subset of the Interaction API.
This module provides just enough to satisfy those calls by routing them
through Discord's followup webhook API instead of the Gateway.
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp
import discord


DISCORD_API = "https://discord.com/api/v10"


class _VoiceState:
    def __init__(self, channel):
        self.channel = channel


class _Guild:
    def __init__(self, guild_id: int, interaction: "InteractionShim"):
        self.id = guild_id
        self._interaction = interaction

    @property
    def voice_client(self):
        bot = _BOT_REF.get("bot")
        if bot is None:
            return None
        guild = bot.get_guild(self.id)
        if guild is None:
            return None
        return guild.voice_client


class _User:
    def __init__(self, user_id: int, username: str, guild: _Guild | None):
        self.id = user_id
        self.name = username
        self._guild = guild

    @property
    def voice(self):
        bot = _BOT_REF.get("bot")
        if bot is None or self._guild is None:
            return None
        guild = bot.get_guild(self._guild.id)
        if guild is None:
            return None
        member = guild.get_member(self.id)
        if member is None or member.voice is None or member.voice.channel is None:
            return None
        return _VoiceState(member.voice.channel)


class _Followup:
    def __init__(self, shim: "InteractionShim"):
        self._shim = shim

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        file: discord.File | None = None,
        files: list[discord.File] | None = None,
        ephemeral: bool = False,
        **kwargs,
    ) -> None:
        payload: dict[str, Any] = {}
        if content is not None:
            payload["content"] = content
        if embeds:
            payload["embeds"] = [e.to_dict() for e in embeds]
        elif embed is not None:
            payload["embeds"] = [embed.to_dict()]
        if ephemeral:
            payload["flags"] = 64
        if view is not None:
            payload["components"] = view.to_components()

        await self._shim._post_followup(payload, file=file, files=files)


class _Response:
    def __init__(self, shim: "InteractionShim"):
        self._shim = shim
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self._done = True

    async def send_message(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        file: discord.File | None = None,
        files: list[discord.File] | None = None,
        ephemeral: bool = False,
        **kwargs,
    ) -> None:
        self._done = True
        await self._shim.followup.send(
            content=content,
            embed=embed,
            embeds=embeds,
            view=view,
            file=file,
            files=files,
            ephemeral=ephemeral,
        )


class InteractionShim:
    """Mimics a subset of discord.Interaction for forwarded payloads."""

    def __init__(self, payload: dict, bot: discord.Client):
        self._payload = payload
        self._bot = bot

        data = payload.get("data", {})
        self.command_name: str = data.get("name", "")
        self.options: list[dict] = data.get("options", [])
        self.token: str = payload["token"]
        self.application_id: str = payload["application_id"]

        self.guild_id: int | None = (
            int(payload["guild_id"]) if payload.get("guild_id") else None
        )
        self.channel_id: int | None = (
            int(payload["channel_id"]) if payload.get("channel_id") else None
        )

        self.guild: _Guild | None = (
            _Guild(self.guild_id, self) if self.guild_id else None
        )
        user_data = payload.get("member", {}).get("user") or payload.get("user", {})
        self.user = _User(
            int(user_data.get("id", 0)),
            user_data.get("username", ""),
            self.guild,
        )

        self.client = bot
        self.response = _Response(self)
        self.followup = _Followup(self)

    def get_option(self, name: str, default: Any = None) -> Any:
        for opt in self.options:
            if opt.get("name") == name:
                return opt.get("value", default)
        return default

    async def _post_followup(
        self,
        payload: dict,
        *,
        file: discord.File | None = None,
        files: list[discord.File] | None = None,
    ) -> None:
        url = f"{DISCORD_API}/webhooks/{self.application_id}/{self.token}"

        all_files: list[discord.File] = []
        if file is not None:
            all_files.append(file)
        if files:
            all_files.extend(files)

        async with aiohttp.ClientSession() as session:
            if all_files:
                form = aiohttp.FormData()
                form.add_field(
                    "payload_json",
                    json.dumps(payload, separators=(",", ":")),
                    content_type="application/json",
                )
                for i, f in enumerate(all_files):
                    form.add_field(
                        f"files[{i}]",
                        f.fp,
                        filename=f.filename,
                        content_type="application/octet-stream",
                    )
                async with session.post(url, data=form) as resp:
                    if resp.status >= 300:
                        body = await resp.text()
                        raise RuntimeError(f"Followup failed: {resp.status} {body}")
            else:
                async with session.post(url, json=payload) as resp:
                    if resp.status >= 300:
                        body = await resp.text()
                        raise RuntimeError(f"Followup failed: {resp.status} {body}")


_BOT_REF: dict[str, discord.Client] = {}


def set_bot(bot: discord.Client) -> None:
    _BOT_REF["bot"] = bot
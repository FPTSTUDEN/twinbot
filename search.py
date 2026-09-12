"""Search-related slash commands for the Discord bot.

Provides a /search command with two modes:
  - text  : searches DuckDuckGo (instant answer + top results)
  - image : searches for images and posts each result as its own message

Supports an `immediate` flag that skips any confirmation and sends
results straight to the channel.

Uses only public endpoints / redirects so no API key is required:
  - DuckDuckGo Instant Answer API  (text instant answers)
  - DuckDuckGo HTML endpoint       (text results fallback)
  - DuckDuckGo image search        (images, via `ddgs`)
"""

import os
import re
import html
import asyncio
import urllib.parse

import aiohttp
import discord
from discord.ext import commands

from access_control import check_permission


# --- Tunables -------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
MAX_RESULTS = 5
MAX_IMAGES = 2


# --- HTTP helpers ---------------------------------------------------------

async def _http_get(url: str, *, params: dict | None = None) -> str:
    """GET a URL and return the body as text. Raises on HTTP errors."""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        async with session.get(url, params=params, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.text()


async def _http_get_json(url: str, *, params: dict | None = None) -> dict:
    """GET a URL and return parsed JSON."""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        async with session.get(url, params=params, headers=headers) as resp:
            resp.raise_for_status()
            # Ignore Content-Type header mismatches
            return await resp.json(content_type=None)


# --- DuckDuckGo text search ----------------------------------------------

async def _ddg_instant_answer(query: str) -> dict:
    """Query the DuckDuckGo Instant Answer API."""
    return await _http_get_json(
        "https://api.duckduckgo.com/",
        params={
            "q": query,
            "format": "json",
            "no_html": "1",
            "no_redirect": "1",
            # "skip_disambig": "1",
        },
    )


async def _ddg_html_results(query: str, limit: int = MAX_RESULTS) -> list[dict]:
    """Scrape the DuckDuckGo HTML endpoint for top text results.

    Returns a list of {"title", "url", "snippet"} dicts.
    """
    raw = await _http_get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
    )

    results: list[dict] = []
    link_re = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.DOTALL,
    )
    snippet_re = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
        re.DOTALL,
    )

    links = link_re.findall(raw)
    snippets = [m.group("snippet") for m in snippet_re.finditer(raw)]

    for i, (href, title) in enumerate(links[:limit]):
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        real_url = qs.get("uddg", [href])[0]

        results.append(
            {
                "title": _strip_tags(title),
                "url": real_url,
                "snippet": _strip_tags(snippets[i]) if i < len(snippets) else "",
            }
        )

    return results


def _strip_tags(text: str) -> str:
    """Remove HTML tags and unescape entities."""
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


# --- DuckDuckGo image search ---------------------------------------------

from ddgs import DDGS

async def _ddg_image_results(query: str, limit: int = MAX_IMAGES) -> list[dict]:
    # Run synchronous DDGS search inside an executor to avoid blocking the asyncio loop
    def fetch_images():
        with DDGS() as ddgs:
            return list(ddgs.images(query, max_results=limit))

    loop = asyncio.get_running_loop()
    results_raw = await loop.run_in_executor(None, fetch_images)

    results = []
    for item in results_raw:
        results.append({
            "title": item.get("title", ""),
            "image": item.get("image", ""),
            "thumbnail": item.get("thumbnail", ""),
            "url": item.get("url", ""),
        })
    return results


# --- Embed builders -------------------------------------------------------

def _text_results_embed(query: str, instant: dict, results: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title=f"🔎 {query}",
        color=discord.Color.blurple(),
    )

    abstract = instant.get("AbstractText") or instant.get("Answer")
    if abstract:
        source = instant.get("AbstractSource") or instant.get("AnswerType") or ""
        embed.add_field(
            name=f"Instant answer{' — ' + source if source else ''}",
            value=abstract[:1024],
            inline=False,
        )

    if not results and not abstract:
        embed.description = "No results found."
        return embed

    lines = []
    for i, r in enumerate(results, start=1):
        title = r["title"] or r["url"]
        snippet = r["snippet"]
        line = f"**{i}. [{title}]({r['url']})**"
        if snippet:
            line += f"\n{snippet[:200]}"
        lines.append(line)

    if lines:
        embed.add_field(
            name="Top results",
            value="\n\n".join(lines)[:1024],
            inline=False,
        )

    return embed


def _image_result_embed(index: int, total: int, query: str, result: dict) -> discord.Embed:
    """Build an embed for a single image result."""
    title = (result.get("title") or "image").strip()
    embed = discord.Embed(
        title=title[:256],
        url=result.get("url") or result.get("image") or None,
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Result {index}/{total} — query: {query}")
    if result.get("image"):
        embed.set_image(url=result["image"])
    elif result.get("thumbnail"):
        # Fall back to the thumbnail if the full-size image URL is missing.
        embed.set_image(url=result["thumbnail"])
    if result.get("url"):
        embed.add_field(name="Source", value=result["url"][:1024], inline=False)
    return embed


# --- Command handlers -----------------------------------------------------

async def search(
    interaction: discord.Interaction,
    query: str,
    mode: str = "text",
    immediate: bool = False,
):
    if not await check_permission(interaction, "search"):
        return

    mode = mode.lower().strip()
    if mode not in ("text", "image"):
        await interaction.response.send_message(
            "`mode` must be either `text` or `image`.", ephemeral=True
        )
        return

    # Ephemeral defer → everything derived from this interaction stays private
    # unless we explicitly post to the channel.
    await interaction.response.defer(ephemeral=True)

    try:
        if mode == "text":
            await _handle_text(interaction, query, immediate)
        else:
            await _handle_image(interaction, query, immediate)

    except aiohttp.ClientError as e:
        await interaction.followup.send(f"⚠️ Search failed: `{e}`", ephemeral=True)
    except Exception as e:
        print(f"[ERROR] /search failed: {e}")
        await interaction.followup.send("⚠️ Search failed unexpectedly.", ephemeral=True)

async def _handle_text(interaction: discord.Interaction, query: str, immediate: bool) -> None:
    instant, results = await asyncio.gather(
        _ddg_instant_answer(query),
        _ddg_html_results(query),
    )
    embed = _text_results_embed(query, instant, results)

    if immediate:
        # Publish publicly via the channel, not the (ephemeral) followup.
        await interaction.channel.send(embed=embed)
    else:
        # Preview privately for the invoker.
        view = _PreviewView(embed=embed, author_id=interaction.user.id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def _handle_image(interaction: discord.Interaction, query: str, immediate: bool) -> None:
    results = await _ddg_image_results(query)
    if not results:
        await interaction.followup.send("No image results found.", ephemeral=True)
        return

    total = len(results)
    embeds = [
        _image_result_embed(i, total, query, r)
        for i, r in enumerate(results, start=1)
    ]

    if immediate:
        # One public message per image result.
        for embed in embeds:
            await interaction.channel.send(embed=embed)
        return

    # Preview mode: one ephemeral message per image, each with its own buttons.
    for embed in embeds:
        view = _PreviewView(embed=embed, author_id=interaction.user.id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class _PreviewView(discord.ui.View):
    """Ephemeral preview view with 'Send to channel' / 'Dismiss' buttons."""

    def __init__(self, embed: discord.Embed, author_id: int, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.embed = embed
        self.author_id = author_id
        self.sent = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who ran `/search` can use this button.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Send to channel", style=discord.ButtonStyle.primary, emoji="📤")
    async def send_to_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.sent:
            await interaction.response.send_message("Already sent.", ephemeral=True)
            return
        self.sent = True

        # Publish publicly — this is the only public side-effect.
        await interaction.channel.send(embed=self.embed)

        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("📤 Sent to channel.", ephemeral=True)

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.secondary, emoji="🗑️")
    async def dismiss(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="Dismissed.", embed=None, view=self
        )

def setup(bot: commands.Bot) -> None:
    """Register the /search command on the given bot instance."""
    bot.tree.add_command(
        discord.app_commands.Command(
            name="search",
            description="Search the web (text) or for images",
            callback=search,
        )
    )
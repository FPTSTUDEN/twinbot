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

import io
import os
import re
import json
import html
import asyncio
import urllib.parse
from datetime import datetime, timezone

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

# Where downloaded images (and their .source.json metadata) are saved.
DOWNLOAD_DIR = os.getenv("SEARCH_DOWNLOAD_DIR", "downloads")


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


async def _http_get_bytes(url: str) -> tuple[bytes, str | None]:
    """GET a URL and return (body_bytes, content_type). Raises on HTTP errors."""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.read(), resp.headers.get("Content-Type")


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
    """Scrape the DuckDuckGo HTML endpoint for top text results."""
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


# --- Image fetching / saving ---------------------------------------------

def _guess_extension(content_type: str | None, url: str) -> str:
    """Pick a file extension based on Content-Type, falling back to the URL."""
    if content_type:
        ctype = content_type.split(";", 1)[0].strip().lower()
        mapping = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/bmp": ".bmp",
            "image/svg+xml": ".svg",
            "image/avif": ".avif",
        }
        if ctype in mapping:
            return mapping[ctype]

    path = urllib.parse.urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext and len(ext) <= 5:
        return ext
    return ".jpg"


def _safe_filename(title: str, fallback: str) -> str:
    """Turn a result title into a filesystem-safe base name."""
    base = _strip_tags(title).strip() or fallback
    base = re.sub(r"[^\w\-. ]+", "_", base, flags=re.UNICODE)
    base = re.sub(r"\s+", "_", base).strip("._")
    return (base or fallback)[:80]


async def _download_image_bytes(url: str) -> tuple[bytes, str]:
    """Download an image URL and return (bytes, content_type)."""
    data, ctype = await _http_get_bytes(url)
    return data, (ctype or "application/octet-stream")


def _save_image_with_source(
    data: bytes,
    content_type: str,
    result: dict,
    query: str,
) -> str:
    """Save the image bytes plus a sidecar .source.json file.

    Returns the path of the saved image file.
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    ext = _guess_extension(content_type, result.get("image") or result.get("thumbnail") or "")
    base = _safe_filename(result.get("title", ""), "image")

    # Ensure a unique name if we've downloaded this before.
    candidate = os.path.join(DOWNLOAD_DIR, base + ext)
    n = 1
    while os.path.exists(candidate) or os.path.exists(candidate + ".source.json"):
        candidate = os.path.join(DOWNLOAD_DIR, f"{base}_{n}{ext}")
        n += 1

    with open(candidate, "wb") as f:
        f.write(data)

    source_meta = {
        "title": result.get("title", ""),
        "source_page": result.get("url", ""),
        "source_image": result.get("image", ""),
        "source_thumbnail": result.get("thumbnail", ""),
        "query": query,
        "content_type": content_type,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "file": os.path.basename(candidate),
    }
    with open(candidate + ".source.json", "w", encoding="utf-8") as f:
        json.dump(source_meta, f, indent=2, ensure_ascii=False)

    return candidate


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
    """Build an embed for a single image result (used for previews)."""
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
    """Search the web (text) or for images, and optionally send right away.

    Parameters
    ----------
    query : str
        What to search for.
    mode : str
        "text" (default) or "image".
    immediate : bool
        If True, skip the preview and send results directly to the channel.
    """
    if not await check_permission(interaction, "search"):
        return

    mode = mode.lower().strip()
    if mode not in ("text", "image"):
        await interaction.response.send_message(
            "`mode` must be either `text` or `image`.", ephemeral=True
        )
        return

    # Ephemeral defer → followups stay private unless we explicitly post
    # to the channel (via interaction.channel.send).
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
        await interaction.channel.send(embed=embed)
    else:
        view = _PreviewView(embed=embed, author_id=interaction.user.id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def _handle_image(interaction: discord.Interaction, query: str, immediate: bool) -> None:
    results = await _ddg_image_results(query)
    if not results:
        await interaction.followup.send("No image results found.", ephemeral=True)
        return

    total = len(results)

    if immediate:
        # Send each image publicly as a real upload (not a link embed).
        for i, r in enumerate(results, start=1):
            try:
                data, ctype = await _download_image_bytes(r["image"] or r["thumbnail"])
            except Exception as e:
                print(f"[WARN] Failed to fetch image {r.get('image')!r}: {e}")
                continue

            ext = _guess_extension(ctype, r["image"])
            filename = _safe_filename(r.get("title", ""), f"result_{i}") + ext
            file = discord.File(io.BytesIO(data), filename=filename)

            embed = discord.Embed(
                title=(r.get("title") or "image")[:256],
                url=r.get("url") or r.get("image") or None,
                color=discord.Color.green(),
            )
            embed.set_image(url=f"attachment://{filename}")
            embed.set_footer(text=f"Result {i}/{total} — query: {query}")
            if r.get("url"):
                embed.add_field(name="Source", value=r["url"][:1024], inline=False)

            await interaction.channel.send(embed=embed, file=file)
        return

    # Preview mode: one ephemeral message per image, each with its own buttons.
    for i, r in enumerate(results, start=1):
        embed = _image_result_embed(i, total, query, r)
        view = _ImagePreviewView(
            embed=embed,
            result=r,
            query=query,
            author_id=interaction.user.id,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class _PreviewView(discord.ui.View):
    """Ephemeral preview view for TEXT results."""

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


class _ImagePreviewView(discord.ui.View):
    """Ephemeral preview view for a single IMAGE result.

    Buttons:
      - Send to channel : uploads the actual image bytes publicly.
      - Download        : saves the image + a .source.json sidecar on disk.
      - Dismiss         : removes the preview.
    """

    def __init__(
        self,
        embed: discord.Embed,
        result: dict,
        query: str,
        author_id: int,
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        self.embed = embed
        self.result = result
        self.query = query
        self.author_id = author_id
        self.sent = False
        self.downloaded = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who ran `/search` can use this button.",
                ephemeral=True,
            )
            return False
        return True

    async def _fetch_bytes(self) -> tuple[bytes, str] | None:
        """Fetch the image bytes, trying the full image then the thumbnail."""
        candidates = [
            u for u in (self.result.get("image"), self.result.get("thumbnail")) if u
        ]
        for url in candidates:
            try:
                return await _download_image_bytes(url)
            except Exception as e:
                print(f"[WARN] image fetch failed for {url!r}: {e}")
        return None

    @discord.ui.button(label="Send to channel", style=discord.ButtonStyle.primary, emoji="📤")
    async def send_to_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.sent:
            await interaction.response.send_message("Already sent.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        fetched = await self._fetch_bytes()
        if not fetched:
            await interaction.followup.send(
                "⚠️ Could not fetch the image to upload.", ephemeral=True
            )
            return

        data, ctype = fetched
        self.sent = True

        ext = _guess_extension(ctype, self.result.get("image") or self.result.get("thumbnail") or "")
        filename = _safe_filename(self.result.get("title", ""), "image") + ext
        file = discord.File(io.BytesIO(data), filename=filename)

        # Public upload — embed references the attachment, not the remote URL.
        embed = discord.Embed(
            title=(self.result.get("title") or "image")[:256],
            url=self.result.get("url") or self.result.get("image") or None,
            color=discord.Color.green(),
        )
        embed.set_image(url=f"attachment://{filename}")
        if self.result.get("url"):
            embed.add_field(name="Source", value=self.result["url"][:1024], inline=False)

        await interaction.channel.send(embed=embed, file=file)

        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("📤 Sent to channel.", ephemeral=True)

    @discord.ui.button(label="Download", style=discord.ButtonStyle.success, emoji="💾")
    async def download(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.downloaded:
            await interaction.response.send_message("Already downloaded.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        fetched = await self._fetch_bytes()
        if not fetched:
            await interaction.followup.send(
                "⚠️ Could not fetch the image to download.", ephemeral=True
            )
            return

        data, ctype = fetched

        try:
            saved_path = _save_image_with_source(data, ctype, self.result, self.query)
        except OSError as e:
            await interaction.followup.send(
                f"⚠️ Could not save the image: `{e}`", ephemeral=True
            )
            return

        self.downloaded = True
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"💾 Saved as `{os.path.basename(saved_path)}` "
            f"(with `{os.path.basename(saved_path)}.source.json`).",
            ephemeral=True,
        )

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
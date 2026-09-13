"""File-management slash commands for the Discord bot.

Scoped to a single directory (DOWNLOAD_DIR). Nothing outside it can be
listed or uploaded.

Commands:
  /files list    — list files in DOWNLOAD_DIR
  /files upload  — upload a file from DOWNLOAD_DIR to the channel
"""

import io
import os
import asyncio

import discord
from discord.ext import commands

from access_control import check_permission


DOWNLOAD_DIR = os.getenv("SEARCH_DOWNLOAD_DIR", "downloads")


def _ensure_within_dir(path: str, root: str) -> str:
    """Return the absolute path if it lives inside `root`, else raise.

    Prevents path traversal (e.g. "../../etc/passwd").
    """
    root_abs = os.path.realpath(root)
    target_abs = os.path.realpath(os.path.join(root_abs, path))
    if os.path.commonpath([root_abs, target_abs]) != root_abs:
        raise ValueError("Path escapes the download directory.")
    return target_abs


def _list_files() -> list[dict]:
    """Return a list of {name, size, mtime} for files in DOWNLOAD_DIR.

    Excludes .source.json sidecars from the count but reports them.
    """
    if not os.path.isdir(DOWNLOAD_DIR):
        return []

    entries = []
    for name in sorted(os.listdir(DOWNLOAD_DIR)):
        full = os.path.join(DOWNLOAD_DIR, name)
        if not os.path.isfile(full):
            continue
        st = os.stat(full)
        entries.append(
            {
                "name": name,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "is_sidecar": name.endswith(".source.json"),
            }
        )
    return entries


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


async def files(
    interaction: discord.Interaction,
    action: str,
    filename: str | None = None,
):
    """File management scoped to the download directory.

    Parameters
    ----------
    action : str
        "list" or "upload".
    filename : str, optional
        For "upload": the file name inside the download directory.
    """
    if not await check_permission(interaction, "files"):
        return

    action = action.lower().strip()

    if action == "list":
        await _files_list(interaction)
    elif action == "upload":
        if not filename:
            await interaction.response.send_message(
                "`filename` is required for the `upload` action.", ephemeral=True
            )
            return
        await _files_upload(interaction, filename)
    else:
        await interaction.response.send_message(
            "`action` must be `list` or `upload`.", ephemeral=True
        )


async def _files_list(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)

    entries = await asyncio.to_thread(_list_files)
    if not entries:
        await interaction.followup.send(
            f"The download directory `{DOWNLOAD_DIR}` is empty or missing.",
            ephemeral=True,
        )
        return

    real_files = [e for e in entries if not e["is_sidecar"]]
    sidecars = [e for e in entries if e["is_sidecar"]]

    lines = []
    for e in real_files:
        lines.append(f"• `{e['name']}` — {_human_size(e['size'])}")
        sidecar = next((s for s in sidecars if s["name"] == e["name"] + ".source.json"), None)
        if sidecar:
            lines.append("  ↳ has metadata")

    # Discord messages cap at 2000 chars; split if needed.
    body = "\n".join(lines) or "(no files)"
    header = f"📁 `{DOWNLOAD_DIR}` — {len(real_files)} file(s)\n"

    chunks = []
    current = header
    for line in body.splitlines():
        if len(current) + len(line) + 1 > 1900:
            chunks.append(current)
            current = line
        else:
            current += "\n" + line
    chunks.append(current)

    for chunk in chunks:
        await interaction.followup.send(chunk, ephemeral=True)


async def _files_upload(interaction: discord.Interaction, filename: str) -> None:
    await interaction.response.defer(ephemeral=True)

    try:
        path = _ensure_within_dir(filename, DOWNLOAD_DIR)
    except ValueError as e:
        await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
        return

    if not os.path.isfile(path):
        await interaction.followup.send(
            f"⚠️ File `{filename}` not found in `{DOWNLOAD_DIR}`.", ephemeral=True
        )
        return

    # Read in a thread to avoid blocking the event loop on large files.
    try:
        with open(path, "rb") as f:
            data = await asyncio.to_thread(f.read)
    except OSError as e:
        await interaction.followup.send(f"⚠️ Could not read file: `{e}`", ephemeral=True)
        return

    # Discord's default upload limit is 8 MB (25 MB boosted). Guard here.
    if len(data) > 24 * 1024 * 1024:
        await interaction.followup.send(
            "⚠️ File is too large to upload (limit ~24 MB).", ephemeral=True
        )
        return

    file = discord.File(io.BytesIO(data), filename=os.path.basename(path))

    # Public upload, since the point is to share the file with the channel.
    await interaction.channel.send(file=file)

    # Confirm to the invoker.
    await interaction.followup.send(
        f"📤 Uploaded `{os.path.basename(path)}` ({_human_size(len(data))}).",
        ephemeral=True,
    )


def setup(bot: commands.Bot) -> None:
    """Register the /files command on the given bot instance."""
    bot.tree.add_command(
        discord.app_commands.Command(
            name="files",
            description="List or upload files from the download directory",
            callback=files,
        )
    )
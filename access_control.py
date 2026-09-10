"""Access control module for the Discord bot.

Reads per-command permissions from a CSV file with the format:

    command,allowed-users-or-everyone
    join,everyone
    play,123456789012345678;987654321098765432
    leave,everyone

- The first column is the command name.
- The second column is either the literal string "everyone" or a
  semicolon-separated list of Discord user IDs.

Commands not listed in the file are denied by default.
"""

import os
import csv
import discord


# Path to the CSV file containing per-command permissions.
# Can be overridden by setting the PERMISSIONS_FILE env var.
PERMISSIONS_FILE = os.getenv("PERMISSIONS_FILE", "users_list.csv")


def mask_user_id(user_id: str) -> str:
    """Return a censored form of a Discord user ID.

    Discord snowflake IDs are 17-20 digits. We show the last 4 digits
    and replace the rest with asterisks, e.g.:
        "123456789012345678" -> "**************5678"
    """
    user_id = str(user_id).strip()
    if len(user_id) <= 4:
        return "*" * len(user_id)
    return "*" * (len(user_id) - 4) + user_id[-4:]


def load_permissions(path: str | None = None) -> dict:
    """Read command permissions from the CSV file.

    Returns a dict: {command_name: ("everyone" | set_of_user_ids)}.
    Missing file → empty dict (all commands denied).
    """
    path = path or PERMISSIONS_FILE
    permissions: dict = {}

    if not os.path.isfile(path):
        print(f"[WARN] '{path}' not found. All commands will be denied.")
        return permissions

    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            for row_num, row in enumerate(reader, start=1):
                # Skip blank lines and comments
                if not row or not any(cell.strip() for cell in row):
                    continue
                if row[0].strip().startswith("#"):
                    continue

                if len(row) < 2:
                    print(f"[WARN] Line {row_num}: missing permission column, skipping.")
                    continue

                command_name = row[0].strip().lower()
                raw_value = row[1].strip()

                if not command_name:
                    print(f"[WARN] Line {row_num}: empty command name, skipping.")
                    continue

                if raw_value.lower() == "everyone":
                    permissions[command_name] = "everyone"
                else:
                    user_ids = {
                        uid.strip()
                        for uid in raw_value.replace(",", ";").split(";")
                        if uid.strip()
                    }
                    permissions[command_name] = user_ids

    except OSError as e:
        print(f"[ERROR] Could not read '{path}': {e}")

    return permissions


def is_user_authorized(command_name: str, user_id: int) -> bool:
    """Return True if the user is allowed to run the given command."""
    permissions = load_permissions()
    entry = permissions.get(command_name.lower())

    if entry is None:
        # Command not listed in the file → deny by default
        return False

    if entry == "everyone":
        return True

    return str(user_id) in entry


async def check_permission(interaction: discord.Interaction, command_name: str) -> bool:
    """Verify the interaction user is allowed to run the given command.

    Sends an ephemeral denial message and returns False if not authorized.
    Returns True if the user is allowed.
    """
    if not is_user_authorized(command_name, interaction.user.id):
        await interaction.response.send_message(
            f"🚫 You are not authorized to use `/{command_name}`.",
            ephemeral=True,
        )
        return False
    return True


def log_permissions() -> None:
    """Print the currently loaded permissions (used at startup).

    User IDs are censored so they don't end up in full in the console log.
    """
    perms = load_permissions()
    if not perms:
        print("  (no permissions loaded)")
        return
    for cmd, entry in perms.items():
        if entry == "everyone":
            shown = "everyone"
        else:
            shown = sorted(mask_user_id(uid) for uid in entry)
        print(f"  - /{cmd}: {shown}")
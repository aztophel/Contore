"""Clickable slash-command mentions (`</command:id>`), the way Discord's
client renders them. Discord only lets you build one of these if you know
the command's snowflake ID, which isn't known until *after* the command
tree has synced -- so `bot.py` populates `CMD_IDS` right after
`tree.sync()`, and every cog that wants to reference "run /pokemon
starter" in a reply calls `cmd_mention("pokemon starter")` instead of
hardcoding a slash-less backtick string that can't be clicked.
"""
from __future__ import annotations

CMD_IDS: dict[str, int] = {}


def register_synced_commands(commands) -> None:
    """Called once per `tree.sync()` result (global and/or per-guild) to
    (re)populate the mention registry. Safe to call more than once --
    later calls simply refresh IDs that may have changed."""
    for c in commands:
        CMD_IDS[c.name] = c.id


def cmd_mention(qualified_name: str) -> str:
    """`cmd_mention("pokemon starter")` -> `</pokemon starter:1234...>`,
    falling back to a plain backtick-quoted string if the top-level
    command hasn't been synced yet (or this process never synced it)."""
    top_level = qualified_name.split(" ", 1)[0]
    cmd_id = CMD_IDS.get(top_level)
    return f"</{qualified_name}:{cmd_id}>" if cmd_id else f"`/{qualified_name}`"

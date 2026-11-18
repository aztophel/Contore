"""Small discord.py-specific helpers with no feature attached to them --
things every cog file would otherwise redefine (or import from each
other) if they weren't centralized here."""
from __future__ import annotations
import re

from discord import Member
from discord.app_commands import AppCommandContext, AppInstallationType

# --- App command install/context presets --------------------------------
# Every command group in the monolith used one of these two combinations;
# centralizing them means a change in install-context policy (e.g. opting
# everything into user installs) is a one-line change here instead of a
# find-and-replace across every cog.
GUILD = AppCommandContext(guild=True)
GUILD_INSTALL = AppInstallationType(guild=True)
ALL = AppCommandContext(guild=True, dm_channel=True, private_channel=True)
ALL_INSTALLS = AppInstallationType(guild=True, user=True)

# Slash command / sub-command names Discord accepts: 1-32 chars, letters,
# digits, spaces, hyphens, underscores, plus a couple of scripts whose
# combining marks Discord explicitly allows.
_THAI_COMBINING = r"\u0E00-\u0E7F"
_DEVANAGARI_COMBINING = r"\u0900-\u097F"
VALID_SLASH_COMMAND_NAME = re.compile(
    r"^[-_\w " + _THAI_COMBINING + _DEVANAGARI_COMBINING + r"]{1,32}$"
)


def get_highest_role(member: Member):
    """The member's highest-position role, used e.g. when deciding how far
    a role can be repositioned without exceeding the bot's own role."""
    return max(member.roles, key=lambda r: r.position)

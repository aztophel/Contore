"""Shared permission checks, used across cogs instead of each command
re-implementing its own `ctx.user.id == OWNER_ID` inline (as the monolith
did in ~15 different places)."""
from __future__ import annotations
from discord import Interaction, Member, app_commands

from config import OWNER_ID

__all__ = ["OWNER_ID", "is_owner", "is_admin"]


def is_owner():
    """Slash-command check: usable as `@is_owner()` on any `app_commands` command."""
    async def predicate(ctx: Interaction) -> bool:
        return ctx.user.id == OWNER_ID
    return app_commands.check(predicate)


def is_admin(ctx: Interaction) -> bool:
    """Non-decorator variant for commands that need to branch on admin
    status rather than reject non-admins outright (mirrors the monolith's
    `_is_admin` helper, used e.g. by role-on-message authorization checks)."""
    if ctx.user.id == OWNER_ID:
        return True
    if not isinstance(ctx.user, Member):
        return False
    perms = ctx.user.guild_permissions
    return (
        perms.manage_guild and perms.manage_channels and perms.manage_nicknames
        and perms.manage_roles and perms.ban_members and perms.kick_members
    )

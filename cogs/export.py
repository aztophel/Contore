"""`/export`: dumps every piece of data Contore has stored about the
calling user as a downloadable JSON file.

The monolith's version inlined Pokémon-table reads (owned/eggs/items/
pokedex/etc.) directly alongside the Track-A-owned tables. Since this
file doesn't own or import Pokémon internals, that section is instead a
pluggable registry -- `EXPORT_PROVIDERS: dict[str, ExportProvider]` --
that the Pokémon cogs populate themselves:

    from cogs.export import EXPORT_PROVIDERS

    async def _pokemon_export(user_id: int) -> dict:
        return {"owned": ..., "eggs": ..., ...}

    EXPORT_PROVIDERS["pokemon"] = _pokemon_export

Each provider contributes one top-level key to the exported JSON, keyed
by its registry name. Until something registers under "pokemon", `/export`
simply omits that section rather than failing.
"""
from __future__ import annotations
import json
from datetime import datetime
from io import BytesIO
from typing import Awaitable, Callable

from discord import File, Interaction, app_commands
from discord.app_commands import allowed_contexts
from discord.ext import commands

import core.db as db

ExportProvider = Callable[[int], Awaitable[dict]]
EXPORT_PROVIDERS: dict[str, ExportProvider] = {}


class ExportCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="export", description="Download a copy of every piece of data Contore has stored about you")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def export_data(self, ctx: Interaction):
        await ctx.response.defer(ephemeral=True)
        uid = ctx.user.id

        fam = await db.db_get_family(uid)
        profile = await db.db_get_user_profile(uid)
        autoproxy = await db.db_get_autoproxy(uid)
        uwu_rows = await db.db_get_uwu_settings_by_user(uid)
        scheduled_roles_rows = await db.db_get_scheduled_roles_by_user(uid)
        submissions_rows = await db.db_get_submissions_by_user(uid)
        tags_created = await db.db_get_tags_by_creator(uid)

        data = {
            "user_id": uid,
            "exported_at": datetime.now().isoformat(),

            "birthday": await db.db_get_birthday(uid),
            "profile": {"custom_bg": profile.get("custom_bg"), "custom_color": profile.get("custom_color")},
            "quotes": await db.db_get_quotes(uid),

            "family": {
                "partners": list(fam["partners"]), "children": list(fam["children"]),
                "parents": list(fam["parents"]), "title": fam["title"],
                "restrictions": {
                    "mode": fam["restrictions"]["mode"],
                    "lists": {k: {"mode": v["mode"], "users": list(v["users"])}
                              for k, v in fam["restrictions"]["lists"].items()}
                },
                "hidden_from_tree": list(fam["hidden"])
            },

            "proxies": await db.db_get_proxies(uid),
            "autoproxy": autoproxy,
            "uwu_settings": [{"guild_id": r["guild_id"], "type": r["type"]} for r in uwu_rows],

            "levelling": await db.db_get_all_levelling_for_user(uid),
            "achievements_earned": await db.db_get_all_achievements_for_user(uid),
            "achievement_progress": await db.db_get_all_progress_for_user(uid),

            "scheduled_roles": [dict(r) for r in scheduled_roles_rows],
            "submissions": [dict(r) for r in submissions_rows],
            "tags_created": [dict(r) for r in tags_created],
        }

        for key, provider in EXPORT_PROVIDERS.items():
            try:
                data[key] = await provider(uid)
            except Exception as e:
                data[key] = {"error": f"export provider failed: {e}"}

        buf = BytesIO(json.dumps(data, indent=2, default=str).encode("utf-8"))
        await ctx.followup.send(
            "Here's your data! Have fun :)",
            file=File(buf, filename=f"contore_export_{uid}.json"))


async def setup(bot: commands.Bot):
    await bot.add_cog(ExportCog(bot))

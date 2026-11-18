"""Thin `/uwuify` and `/invert` command wrapper. The actual text-transform
engines (`uwuify`, `poshify`) live in `core/text.py` since `proxies.py`
needs to call them directly too -- see that module's docstring.
"""
from __future__ import annotations
from discord import Interaction, User, app_commands
from discord.app_commands import default_permissions, describe, guild_only
from discord.ext import commands

import core.db as db
from config import OWNER_ID


class TextFiltersCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="uwuify", description="Uwuify your enemies")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(victim="Your enemy")
    async def uwuify_cmd(self, ctx: Interaction, victim: User):
        if ctx.guild_id is None:
            await ctx.response.send_message("can't uwuify someone outside of a server", ephemeral=True)
            return

        if await db.db_get_uwu_type(ctx.guild_id, victim.id) == 0:
            await db.db_clear_uwu(ctx.guild_id, victim.id)
            await ctx.response.send_message(f"Unuwuified {victim.mention}.")
        else:
            await db.db_set_uwu(ctx.guild_id, victim.id, 0)
            await ctx.response.send_message(f"Successfully uwuified {victim.mention}.")
            if victim.id == OWNER_ID and ctx.user.id != OWNER_ID:
                await victim.send(f"You've been uwuified by {ctx.user.mention}.")

    @app_commands.command(name="invert", description="Invert your enemies")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(victim="Your enemy")
    async def invert_cmd(self, ctx: Interaction, victim: User):
        if ctx.guild_id is None:
            await ctx.response.send_message("can't invert someone outside a server", ephemeral=True)
            return
        if not ctx.user.guild_permissions.manage_guild:
            await ctx.response.send_message("you cannot")
            return

        if await db.db_get_uwu_type(ctx.guild_id, victim.id) == 1:
            await db.db_clear_uwu(ctx.guild_id, victim.id)
            await ctx.response.send_message(f"Reverted {victim.mention}.")
        else:
            await db.db_set_uwu(ctx.guild_id, victim.id, 1)
            await ctx.response.send_message(f"Successfully inverted {victim.mention}.")


async def setup(bot: commands.Bot):
    await bot.add_cog(TextFiltersCog(bot))

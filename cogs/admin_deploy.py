"""Slash commands wrapping deploy/update_command.py. Replaces the
monolith's /update and /open; /stop and /terminate stay roughly as they
were elsewhere (process control, not deployment)."""
from __future__ import annotations
from discord import Interaction, app_commands
from discord.ext import commands

from deploy.update_command import check_for_updates, deploy, rollback, respawn
from core.permissions import is_owner


class DeployCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    deploy_group = app_commands.Group(name="deploy", description="Git-based deployment")

    @deploy_group.command(name="check", description="Check whether the remote repo has unpulled commits")
    @is_owner()
    async def deploy_check(self, ctx: Interaction):
        behind, msg = await check_for_updates()
        await ctx.response.send_message(("🔔 " if behind else "✅ Up to date. ") + msg, ephemeral=True)

    @deploy_group.command(name="pull", description="Pull the latest commit from the given branch and restart")
    @app_commands.describe(branch="Branch to deploy (default: main)")
    @is_owner()
    async def deploy_pull(self, ctx: Interaction, branch: str = "main"):
        await ctx.response.defer(ephemeral=True)
        ok, msg = await deploy(branch)
        await ctx.followup.send(msg, ephemeral=True)
        if ok:
            respawn()

    @deploy_group.command(name="rollback", description="Hard-reset to a specific commit and restart")
    @app_commands.describe(commit="Commit hash to roll back to")
    @is_owner()
    async def deploy_rollback(self, ctx: Interaction, commit: str):
        await ctx.response.defer(ephemeral=True)
        ok, msg = await rollback(commit)
        await ctx.followup.send(msg, ephemeral=True)
        if ok:
            respawn()


async def setup(bot: commands.Bot):
    await bot.add_cog(DeployCog(bot))

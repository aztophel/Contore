"""Role management: `/auto role`, `/message role`, `/date role`,
`/reset roles|role`, `/reposition`, plus the on-join auto-role listener
and the hourly scheduled-role sweep.
"""
from __future__ import annotations
import asyncio
import re
from datetime import datetime
from typing import Literal, Union

from discord import AllowedMentions, Interaction, Member, Message, Role, app_commands
from discord.abc import Snowflake
from discord.app_commands import describe
from discord.ext import commands, tasks

import core.db as db
from config import OWNER_ID
from core.discord_utils import GUILD, GUILD_INSTALL, get_highest_role
from core.permissions import is_admin
from core.scheduling import sleep_until_next_hour


def _format_text(content: str) -> str:
    """Normalizes a message for loose matching against a configured
    role-on-message trigger: strips mentions, lowercases, collapses runs
    of whitespace."""
    return re.sub(r"\s{2,}", " ", re.sub(r" ?<@!?\d+> ?", "", content.lower()))


def _format_message_role(params: dict) -> str:
    action = "Grants" if not params.get("take") else "Takes away"
    t = params["type"]
    if t == "Self":
        target = "the author of the sent message"
    elif t == "Specific User":
        target = f"<@{params.get('target_user', 0)}>"
    else:
        target = "mentioned users/roles"
    at = params["auth_type"]
    if at == "Alfred":
        auth = "Messages must be sent by Alfred"
    elif at == "Administrators":
        auth = "Messages must be sent by an administrator"
    elif at == "Specific Role":
        auth = f"Messages must be sent by someone with <@&{params.get('auth_id', 0)}>"
    elif at == "Specific User":
        auth = f"Messages must be sent by <@{params.get('auth_id', 0)}>"
    else:
        auth = "Anybody can trigger this"
    revoke = f"\nRoles revoked after {params.get('time_limit', 0):.0f}s" if params.get("time_limit", -1) > 0 else ""
    trigger = (f"send \"{params['content']}\"" if not params.get("sub_match")
               else f"message must contain \"{params['content']}\"")
    return f"{action} <@&{params['role']}> to {target}\n{auth}{revoke}\nTo trigger: {trigger}"


async def _add_role(user: Member, *roles: Snowflake, take: bool, time: float):
    if not take:
        await user.add_roles(*roles)
    else:
        await user.remove_roles(*roles)
    if time > 0:
        await asyncio.sleep(time)
        if not take:
            await user.remove_roles(*roles)
        else:
            await user.add_roles(*roles)


class RolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._scheduled_roles_task.start()

    def cog_unload(self):
        self._scheduled_roles_task.cancel()

    # region /auto
    auto_group = app_commands.Group(name="auto", description="Automatic commands", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL)

    @auto_group.command(name="role", description="Automatically apply a role to (specific) users when they join")
    @describe(role="The role to automatically apply to the target", user="The target (leave blank for the role to apply to everyone)")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def auto_role(self, ctx: Interaction, role: Role, user: Member = None):
        if not is_admin(ctx):
            await ctx.response.send_message("you cannot")
            return
        if user is None:
            await db.db_add_auto_role(ctx.guild_id, "everyone", role.id)
            await ctx.response.send_message(f"Every new user will receive `{role.name}` on join.", ephemeral=True)
        else:
            await db.db_add_auto_role(ctx.guild_id, user.id, role.id)
            await ctx.response.send_message(f"{user.display_name} will receive `{role.name}` on join.", ephemeral=True)
    # endregion

    # region /message
    message_group = app_commands.Group(name="message", description="Message commands", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL)

    @message_group.command(name="role", description="Automatically grant/revoke roles when a message is sent")
    @describe(
        role="The role to automatically apply to the target",
        content="The content of the message",
        type="Defines who gets the role when a message matching the defined parameters is sent",
        specified_user="Required if the type is \"Specific User\"",
        time_limit="Removes the role from everyone it was applied to after a certain amount of time is up (leave negative for no limit)",
        take_role="Takes the role away instead of granting it",
        sub_match="Allows matching messages that contain the defined content but also have more text",
        authorized_user="Required if authorization level is \"Specific User\"",
        authorized_role="Required if authorization level is \"Specific Role\"",
    )
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def role_on_message(
        self, ctx: Interaction, role: Role, content: str,
        type: Literal["Self", "Specific User", "Mentions"] = "Mentions",
        specified_user: Member = None, time_limit: float = -1.0,
        take_role: bool = False, sub_match: bool = False,
        authorization_level: Literal["Everyone", "Specific User", "Specific Role", "Administrators"] = "Administrators",
        authorized_user: Member = None, authorized_role: Role = None,
    ):
        if not is_admin(ctx):
            await ctx.response.send_message("you cannot")
            return
        if type == "Specific User" and not specified_user:
            await ctx.response.send_message("You forgot to define a user, dum dum.", ephemeral=True)
            return
        if authorization_level == "Specific User" and not authorized_user:
            await ctx.response.send_message("You forgot to define an authorized user, dum dum.", ephemeral=True)
            return
        if authorization_level == "Specific Role" and not authorized_role:
            await ctx.response.send_message("You forgot to define an authorized role, dum dum.", ephemeral=True)
            return
        params = {"role": role.id, "content": content, "type": type, "auth_type": authorization_level}
        if type == "Specific User":
            params["target_user"] = specified_user.id
        if time_limit != -1:
            params["time_limit"] = time_limit
        if take_role:
            params["take"] = True
        if sub_match:
            params["sub_match"] = True
        if authorization_level == "Specific User":
            params["auth_id"] = authorized_user.id
        elif authorization_level == "Specific Role":
            params["auth_id"] = authorized_role.id
        await db.db_add_role_on_message(ctx.guild_id, params)
        await ctx.response.send_message(_format_message_role(params), ephemeral=True)

    async def _delete_role_on_message_rule(self, ctx: Interaction, rule_id: int):
        if not is_admin(ctx):
            await ctx.response.send_message("you cannot")
            return
        if await db.db_delete_role_on_message(ctx.guild_id, rule_id):
            await ctx.response.send_message(f"Deleted message-role rule `#{rule_id}`.", ephemeral=True)
        else:
            await ctx.response.send_message(f"No rule with ID `#{rule_id}` exists in this server.", ephemeral=True)

    @message_group.command(name="roledelete", description="Delete a message-triggered role rule by ID")
    @describe(rule_id="The ID shown in /message list")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def message_role_roledelete(self, ctx: Interaction, rule_id: int):
        await self._delete_role_on_message_rule(ctx, rule_id)

    @message_group.command(name="delete", description="Delete a message-triggered role rule by ID")
    @describe(rule_id="The ID shown in /message list")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def message_role_delete(self, ctx: Interaction, rule_id: int):
        await self._delete_role_on_message_rule(ctx, rule_id)

    @message_group.command(name="rolelist", description="List all message-triggered role rules in this server")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def message_role_list(self, ctx: Interaction):
        from core.views import SimplePageView
        from discord import Embed, MISSING

        if not is_admin(ctx):
            await ctx.response.send_message("you cannot")
            return
        rules = await db.db_get_roles_on_messages(ctx.guild_id)
        if not rules:
            await ctx.response.send_message("This server has no message-triggered role rules.", ephemeral=True)
            return
        chunks = [rules[i:i + 8] for i in range(0, len(rules), 8)] or [[]]
        embeds = []
        for n, chunk in enumerate(chunks):
            e = Embed(title="Message-Triggered Roles", color=0x5865F2)
            for params in chunk:
                e.add_field(name=f"ID `{params['id']}`", value=_format_message_role(params), inline=False)
            e.set_footer(text=f"Page {n + 1}/{len(chunks)} ​ · ​ Use /message delete to remove one")
            embeds.append(e)
        await ctx.response.send_message(
            embed=embeds[0],
            view=SimplePageView(embeds, ctx.user.id) if len(embeds) > 1 else MISSING,
            ephemeral=True, allowed_mentions=AllowedMentions.none())
    # endregion

    # region /date
    date_group = app_commands.Group(name="date", description="Date commands", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL)

    @date_group.command(name="role", description="Schedule a role to be given to a user on a specific date")
    @describe(
        role="The role to automatically assign", user="The user to assign the role to",
        date_pattern="YYYY-MM-DD | YYYY-MM | YYYY | MM-DD | MMM | DD",
        duration="The duration (in hours) the role should be kept before being removed",
    )
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def schedule_role(self, ctx: Interaction, role: Role, user: Member, date_pattern: str, duration: int = None):
        if not is_admin(ctx):
            await ctx.response.send_message("you cannot")
            return
        try:
            now = datetime.now()
            date_type = None
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_pattern):
                date_type = "one-time"
                target_date = datetime.strptime(date_pattern, "%Y-%m-%d")
                duration = duration or 24
                if target_date < now:
                    await ctx.response.send_message("Date is in the past.")
                    return
            elif re.fullmatch(r"\d{4}-\d{2}", date_pattern):
                date_type = "yearly-month"
                target_month = datetime.strptime(date_pattern, "%Y-%m")
                duration = duration or 730
                if target_month < now.replace(day=1):
                    await ctx.response.send_message("Month is in the past.")
                    return
            elif re.fullmatch(r"\d{4}", date_pattern):
                date_type = "yearly"
                target_year = int(date_pattern)
                duration = duration or 8760
                if target_year < now.year:
                    await ctx.response.send_message("Year is in the past.")
                    return
            elif re.fullmatch(r"\d{2}-\d{2}", date_pattern):
                date_type = "annual"
                duration = duration or 24
            elif re.fullmatch(r"\d{2}M", date_pattern):
                date_pattern = date_pattern[:2]
                date_type = "monthly"
                duration = duration or 730
            elif re.fullmatch(r"\d{2}", date_pattern):
                date_type = "monthly-day"
                duration = duration or 24
            else:
                await ctx.response.send_message("Invalid date pattern.")
                return
            date_info = {"role_id": role.id, "user_id": user.id, "date_pattern": date_pattern, "type": date_type, "duration": duration}
            if date_type == "one-time":
                date_info["target_date"] = (target_date.year, target_date.month, target_date.day)
            elif date_type == "yearly-month":
                date_info["target_month"] = (target_month.year, target_month.month)
            elif date_type == "yearly":
                date_info["target_year"] = target_year
            await db.db_add_scheduled_role(ctx.guild.id, date_info)
            await ctx.response.send_message(f"Scheduled `{role.name}` for `{user.display_name}` on `{date_pattern}` ({duration:,} hrs).")
        except Exception as e:
            await ctx.response.send_message("An error occurred. Check your inputs.")
            print(f"[schedule_role] {e}")
    # endregion

    # region /reset
    reset_group = app_commands.Group(name="reset", description="Reset commands", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL)

    @reset_group.command(name="roles", description="Resets all role data for this server")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def reset_roles(self, ctx: Interaction):
        if not is_admin(ctx) and not ctx.user.guild_permissions.administrator:
            await ctx.response.send_message("no")
            return
        await db.db_clear_auto_roles(ctx.guild_id)
        await db.db_clear_roles_on_messages(ctx.guild_id)
        await ctx.response.send_message("Reset all role data for this server.", ephemeral=True)

    @reset_group.command(name="role", description="Resets auto-role data for a specific user")
    @describe(user="User to reset (leave blank to reset global auto-roles)")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def reset_role(self, ctx: Interaction, user: Member = None):
        uid = user.id if user else "everyone"
        if not is_admin(ctx) and not ctx.user.guild_permissions.administrator:
            await ctx.response.send_message("no")
            return
        await db.db_remove_auto_role(ctx.guild_id, uid)
        await ctx.response.send_message(f"Removed auto-role data for {user.name if user else 'everyone'}.")
    # endregion

    # region /reposition
    @app_commands.command(name="reposition", description="Move a role's position in the hierarchy")
    @describe(role="Role to move", places="Places to move up")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def reposition(self, ctx: Interaction, role: Role, places: int = 9999):
        if not is_admin(ctx):
            await ctx.response.send_message("you cannot")
            return
        await role.edit(position=min(role.position + places, get_highest_role(ctx.guild.get_member(self.bot.user.id)).position - 1))
        await ctx.response.send_message(f"Moved {role.mention}'s position.", ephemeral=True)
    # endregion

    # region on_message (message-triggered role rules)
    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if message.author.id == self.bot.user.id or message.guild is None:
            return
        for criteria in await db.db_get_roles_on_messages(message.guild.id):
            if not all(criteria.get(k) is not None for k in ("content", "type", "role", "auth_type")):
                continue
            role = message.guild.get_role(criteria["role"])
            auth = criteria.get("auth_id", 0)
            if criteria["auth_type"] == "Specific Role":
                authorization = message.guild.get_role(auth) or await message.guild.fetch_role(auth)
            elif criteria["auth_type"] == "Specific User":
                authorization = message.guild.get_member(auth) or await message.guild.fetch_member(auth)
            else:
                authorization = ""

            if role is None or authorization is None:
                continue

            content_match = criteria.get("sub_match", False) and _format_text(criteria["content"]) in _format_text(message.content)
            exact_match = _format_text(criteria["content"]) == _format_text(message.content)
            if (content_match or exact_match) and (
                (criteria["auth_type"] == "Alfred" and message.author.id == OWNER_ID) or
                (criteria["auth_type"] == "Administrators" and message.author.guild_permissions.administrator) or
                (criteria["auth_type"] == "Specific Role" and message.author.get_role(auth) is not None) or
                (criteria["auth_type"] == "Specific User" and message.author.id == auth) or
                criteria["auth_type"] == "Everyone"
            ):
                if criteria["type"] == "Self":
                    await _add_role(message.author, role, take=criteria.get("take", False), time=criteria.get("time_limit", -1))
                elif criteria["type"] == "Specific User":
                    target = message.guild.get_member(criteria.get("target_user", 0))
                    if target:
                        await _add_role(target, role, take=criteria.get("take", False), time=criteria.get("time_limit", -1))
                else:
                    if message.mention_everyone:
                        for member in message.guild.members:
                            await _add_role(member, role, take=criteria.get("take", False), time=criteria.get("time_limit", -1))
                    else:
                        for mrole in message.role_mentions:
                            for member in mrole.members:
                                await _add_role(member, role, take=criteria.get("take", False), time=criteria.get("time_limit", -1))
                        for user in message.mentions:
                            if isinstance(user, Member):
                                await _add_role(user, role, take=criteria.get("take", False), time=criteria.get("time_limit", -1))
    # endregion

    # region on_member_join / scheduled roles task
    @commands.Cog.listener()
    async def on_member_join(self, member: Member):
        roles = await db.db_get_auto_role_targets(member.guild.id)
        for role_id in roles.get("everyone", []):
            if role_id is not None:
                r = member.guild.get_role(role_id)
                if r:
                    await member.add_roles(r)
        if str(member.id) in roles:
            for role_id in roles[member.id]:
                if role_id is not None:
                    r = member.guild.get_role(role_id)
                    if r:
                        await member.add_roles(r)

    @tasks.loop(hours=1)
    async def _scheduled_roles_task(self):
        now = datetime.now()
        for guild in self.bot.guilds:
            schedules = await db.db_get_scheduled_roles(guild.id)
            for schedule in schedules:
                role = guild.get_role(schedule["role_id"])
                user = guild.get_member(schedule["user_id"])
                if not role or not user:
                    await db.db_remove_scheduled_role(schedule["_id"])
                    continue

                apply_role = False
                t = schedule["type"]
                if t == "one-time" and "target_date" in schedule:
                    apply_role = (now.year, now.month, now.day) == tuple(schedule["target_date"])
                elif t == "yearly-month" and "target_month" in schedule:
                    apply_role = (now.year, now.month) == tuple(schedule["target_month"])
                elif t == "yearly" and "target_year" in schedule:
                    apply_role = now.year == schedule["target_year"]
                elif t == "annual":
                    m, d = map(int, schedule["date_pattern"].split("-"))
                    apply_role = (now.month == m and now.day == d)
                elif t == "monthly":
                    apply_role = now.month == int(schedule["date_pattern"])
                elif t == "monthly-day":
                    apply_role = now.day == int(schedule["date_pattern"])
                if apply_role and schedule.get("assigned_at") is None:
                    await user.add_roles(role)
                    await db.db_set_scheduled_role_assigned(schedule["_id"], now)
                    schedule["assigned_at"] = now

                if schedule.get("assigned_at") and schedule.get("duration"):
                    elapsed = (now - schedule["assigned_at"]).total_seconds() / 3600
                    if elapsed >= schedule["duration"]:
                        await user.remove_roles(role)
                        if t in ("one-time", "yearly", "yearly-month"):
                            await db.db_remove_scheduled_role(schedule["_id"])

    @_scheduled_roles_task.before_loop
    async def _align_scheduled_roles_task(self):
        await self.bot.wait_until_ready()
        await sleep_until_next_hour()
    # endregion


async def setup(bot: commands.Bot):
    await bot.add_cog(RolesCog(bot))

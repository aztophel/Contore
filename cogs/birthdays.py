"""Birthdays: `/birthday set|forget|view|remove|forceset|role|channel`,
`/birthdays` (upcoming list), and the hourly role/announcement sweep."""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Union

from discord import AllowedMentions, Embed, Interaction, Member, Role, User, app_commands
from discord.app_commands import describe, guild_only
from discord.abc import GuildChannel, Messageable
from discord.ext import commands, tasks
from discord.ui import View
from discord import Thread

import core.db as db
from core.discord_utils import ALL
from core.permissions import OWNER_ID, is_owner
from core.scheduling import sleep_until_next_hour


def _parse_birthday_input(date: str) -> tuple[str, str]:
    """Parses a `YYYY-MM-DD` or `MM-DD` birthday string, returning
    `(stored_value, display_format)`. Raises `ValueError` on anything
    else -- callers turn that into a user-facing "invalid date" reply."""
    sep = "-" if "-" in date else "/" if "/" in date else None
    if not sep:
        raise ValueError("format")
    parts = date.split(sep)
    if len(parts) == 3:
        y, m, d = parts
        if not ((len(y) in (2, 4)) and len(m) == 2 and 1 <= int(m) <= 12 and len(d) == 2 and 1 <= int(d) <= 31):
            raise ValueError("date")
        bday = datetime(int(str(datetime.now().year)[:2] + y) if len(y) == 2 else int(y), int(m), int(d))
        return bday.strftime("%Y-%m-%d"), "%Y-%m-%d"
    elif len(parts) == 2:
        m, d = parts
        if not (len(m) == 2 and 1 <= int(m) <= 12 and len(d) == 2 and 1 <= int(d) <= 31):
            raise ValueError("date")
        bday = datetime(1, int(m), int(d))
        return "0000-" + bday.strftime("%m-%d"), "%m-%d"
    raise ValueError("format")


class BirthdaysCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.birthdays_sent: list[tuple[int, int]] = []
        self._birthday_task.start()

    def cog_unload(self):
        self._birthday_task.cancel()

    birthday_group = app_commands.Group(name="birthday", description="Birthday commands", allowed_contexts=ALL, allowed_installs=app_commands.AppInstallationType(guild=True, user=True))

    @birthday_group.command(name="set", description="Set your birthday!")
    @describe(date="YYYY-MM-DD or MM-DD")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def birthday_set(self, ctx: Interaction, date: str):
        try:
            stored, fmt = _parse_birthday_input(date)
        except ValueError:
            await ctx.response.send_message("Invalid date." if "date" else "Invalid format. Use YYYY-MM-DD or MM-DD.")
            return
        await db.db_set_birthday(ctx.user.id, stored)
        display = datetime.strptime(stored if not stored.startswith("0000-") else stored[5:],
                                     "%Y-%m-%d" if not stored.startswith("0000-") else "%m-%d")
        await ctx.response.send_message(f"Your birthday has been set to {display.strftime(fmt)}.")

    @birthday_group.command(name="forget", description="Remove your birthday from Contore")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def birthday_forget(self, ctx: Interaction):
        if await db.db_delete_birthday(ctx.user.id):
            await ctx.response.send_message("Your birthday has been forgotten.")
        else:
            await ctx.response.send_message("You don't have a birthday set!")

    @birthday_group.command(name="view", description="View someone's (or your own) birthday")
    @describe(user="The user whose birthday you want to view")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def birthday_view(self, ctx: Interaction, user: User = None):
        user = user or ctx.user
        birthday = await db.db_get_birthday(user.id)
        if birthday is not None:
            bd = datetime.strptime(birthday if not birthday.startswith("0000-") else birthday[5:],
                                    "%Y-%m-%d" if not birthday.startswith("0000-") else "%m-%d")
            await ctx.response.send_message(f"**{user.display_name}**'s birthday is {bd.strftime('%B %d')} 🎉")
        else:
            await ctx.response.send_message(f"**{user.display_name}** hasn't set a birthday :c")

    @birthday_group.command(name="remove", description="Remove a user's birthday (owner only)")
    @describe(user="The user whose birthday you want to remove")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @is_owner()
    async def birthday_remove(self, ctx: Interaction, user: User):
        if await db.db_delete_birthday(user.id):
            await ctx.response.send_message(f"Removed birthday for {user.mention}.", allowed_mentions=AllowedMentions.none())
        else:
            await ctx.response.send_message(f"{user.mention} has no birthday set.", allowed_mentions=AllowedMentions.none())

    @birthday_group.command(name="forceset", description="Set a user's birthday (owner only)")
    @describe(user="The user whose birthday you want to set", date="YYYY-MM-DD or MM-DD")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @is_owner()
    async def birthday_forceset(self, ctx: Interaction, user: User, date: str):
        try:
            stored, fmt = _parse_birthday_input(date)
        except ValueError:
            await ctx.response.send_message("Invalid date.")
            return
        await db.db_set_birthday(user.id, stored)
        display = datetime.strptime(stored if not stored.startswith("0000-") else stored[5:],
                                     "%Y-%m-%d" if not stored.startswith("0000-") else "%m-%d")
        await ctx.response.send_message(f"{user.mention}'s birthday has been set to {display.strftime(fmt)}.")

    @birthday_group.command(name="role", description="Sets the birthday role to give to birthday people on their birthdays")
    @app_commands.checks.has_permissions(manage_roles=True, manage_guild=True)
    @guild_only()
    @describe(role="The role to assign")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def birthday_role(self, ctx: Interaction, role: Role = None):
        # NOTE: kept the original two-permission-check redundancy (decorator
        # + inline) exactly as the monolith had it.
        if not ctx.permissions.manage_guild:
            return
        if not ctx.guild_id:
            await ctx.response.send_message("you cannot use this command in dms or gcs", ephemeral=True)
            return
        if not ctx.permissions.manage_roles or not ctx.permissions.manage_guild:
            await ctx.response.send_message("get out.")
            return
        data = await db.db_get_birthday_data(ctx.guild_id) or [-1, -1]
        await db.db_set_birthday_data(ctx.guild_id, role.id if role else -1, data[1])
        await ctx.response.send_message(f"The birthday role has been set to {role.mention}.", ephemeral=True)

    @birthday_group.command(name="channel", description="Set birthday announcement channel")
    @app_commands.checks.has_permissions(manage_roles=True, manage_guild=True, manage_channels=True)
    @guild_only()
    @describe(channel="Channel for birthday messages")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def birthday_channel(self, ctx: Interaction, channel: Union[GuildChannel, Thread] = None):
        if not ctx.permissions.manage_guild:
            return
        if not ctx.guild_id:
            await ctx.response.send_message("you cannot use this command in dms or gcs", ephemeral=True)
            return
        if not ctx.permissions.manage_roles or not ctx.permissions.manage_guild or not ctx.permissions.manage_channels:
            await ctx.response.send_message("get out.")
            return
        if channel and not isinstance(channel, Messageable):
            await ctx.response.send_message("Please provide a valid text channel.")
            return
        data = await db.db_get_birthday_data(ctx.guild_id) or [-1, -1]
        await db.db_set_birthday_data(ctx.guild_id, data[0], channel.id if channel else -1)
        await ctx.response.send_message(f"Birthday announcements will be sent to {channel.mention}.", ephemeral=True)

    @app_commands.command(name="birthdays", description="Displays upcoming birthdays")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def birthdays(self, ctx: Interaction):
        now = datetime.now()
        upcoming = []
        for uid, info in (await db.db_get_all_birthdays()).items():
            member = ctx.guild.get_member(uid) if ctx.guild else None
            if not member:
                continue
            bd = datetime.strptime(info if not info.startswith("0000-") else info[5:],
                                    "%Y-%m-%d" if not info.startswith("0000-") else "%m-%d")
            this_year = bd.replace(year=now.year)
            if this_year < now:
                this_year = bd.replace(year=now.year + 1)
            upcoming.append((member.display_name, this_year, (this_year - now).days))
        upcoming.sort(key=lambda x: x[2])
        embed = Embed(title="🎉 Upcoming Birthdays 🎉", color=0xEB459F)
        for name, bd, days in upcoming:
            embed.add_field(name=name, value=f"{bd.strftime('%B %d')} (in {days} days)", inline=False)
        if not upcoming:
            embed.description = "No upcoming birthdays found."
        await ctx.response.send_message(embed=embed)

    @tasks.loop(hours=1)
    async def _birthday_task(self):
        now = datetime.now()
        for guild in self.bot.guilds:
            birthday_role_id, birthday_channel_id = await db.db_get_birthday_data(guild.id)
            if birthday_role_id == -1 and birthday_channel_id == -1:
                continue
            try:
                birthday_role = guild.get_role(birthday_role_id) if birthday_role_id != -1 else None
                birthday_channel = guild.get_channel_or_thread(birthday_channel_id) if birthday_channel_id != -1 else None
            except Exception:
                continue

            all_birthdays = await db.db_get_all_birthdays()
            for user_id, birthday_info in all_birthdays.items():
                member = guild.get_member(user_id)
                if member is None:
                    try:
                        member = await guild.fetch_member(user_id)
                    except Exception:
                        member = None
                if not member:
                    continue

                bd = datetime.strptime(birthday_info if not birthday_info.startswith("0000-") else birthday_info[5:],
                                        "%Y-%m-%d" if not birthday_info.startswith("0000-") else "%m-%d")
                is_today = bd.day == now.day and bd.month == now.month
                if birthday_role:
                    try:
                        if is_today and birthday_role not in member.roles:
                            await member.add_roles(birthday_role)
                        elif not is_today and birthday_role in member.roles:
                            await member.remove_roles(birthday_role)
                    except Exception:
                        pass
                if birthday_channel:
                    if is_today and (user_id, birthday_channel_id) not in self.birthdays_sent:
                        try:
                            await birthday_channel.send(f"Happy birthday, {member.mention}! 🎂🎉")
                        except Exception:
                            pass
                        self.birthdays_sent.append((user_id, birthday_channel_id))
                    elif not is_today and (user_id, birthday_channel_id) in self.birthdays_sent:
                        self.birthdays_sent.remove((user_id, birthday_channel_id))

    @_birthday_task.before_loop
    async def _align_birthday_task(self):
        await self.bot.wait_until_ready()
        await sleep_until_next_hour()


async def setup(bot: commands.Bot):
    await bot.add_cog(BirthdaysCog(bot))

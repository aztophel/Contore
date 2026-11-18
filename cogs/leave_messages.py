"""Leave-message system: the `${var}` template engine, `/leave
variables|message|embed|preview`, and the `on_member_remove` listener that
actually sends the configured message/embed.
"""
from __future__ import annotations
from typing import Union

from discord import Attachment, Embed, Interaction, Member, Thread, app_commands
from discord.abc import GuildChannel
from discord.app_commands import Group, default_permissions, describe, guild_only
from discord.ext import commands
from discord.utils import format_dt

import core.db as db
from config import OWNER_ID
from core.attachments import persist_attachment
from core.discord_utils import GUILD, GUILD_INSTALL
from core.emoji import descape
from core.mentions import cmd_mention


def format_message(template: str, member, inviter=None, invite=None) -> str:
    replacements = {
        "${user_id}": str(member.id),
        "${user_username}": member.name + (f"#{member.discriminator}" if member.discriminator and member.discriminator not in ("", "0", "0000") else ""),
        "${user_display}": descape(member.display_name),
        "${user_global}": member.global_name or "n/a",
        "${user_nick}": member.nick or "n/a",
        "${user_mention}": member.mention,
        "${user_created_date}": format_dt(member.created_at),
        "${user_joined_date}": format_dt(member.joined_at) if member.joined_at else "Unknown",
        "${user_created_date_rel}": format_dt(member.created_at, style="R"),
        "${user_joined_date_rel}": format_dt(member.joined_at, style="R") if member.joined_at else "Unknown",
        "${inviter_id}": str(inviter.id) if inviter else "Unknown",
        "${inviter_username}": inviter.name if inviter else "Unknown",
        "${inviter_display}": inviter.display_name if inviter else "Unknown",
        "${inviter_mention}": inviter.mention if inviter else "Unknown",
        "${invite_code}": invite.code if invite else "Unknown",
        "${invite_channel}": invite.channel.mention if invite and invite.channel else "Unknown",
        "${invite_uses}": str(invite.uses) if invite else "Unknown",
        "${guild_name}": member.guild.name,
        "${guild_membercount}": str(member.guild.member_count),
    }
    for k, v in replacements.items():
        template = template.replace(k, v)
    return template


def build_leave_embed(embed_data: dict, member, inviter=None, invite=None) -> Embed:
    title = format_message(embed_data.get("title") or "", member, inviter, invite)
    desc = format_message(embed_data.get("description") or "", member, inviter, invite)
    embed = Embed(title=title or None, description=desc or None, color=embed_data.get("color", 0x2B2D31))
    if embed_data.get("image"):
        embed.set_image(url=embed_data["image"])
    if embed_data.get("thumbnail"):
        embed.set_thumbnail(url=embed_data["thumbnail"])
    elif member is not None:
        embed.set_thumbnail(url=member.display_avatar.url)
    return embed


class LeaveMessagesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # region /leave
    leave_group = Group(name="leave", description="Leave commands", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL)

    @leave_group.command(name="variables", description="View a list of variables usable to configure the leave message")
    async def leave_variables(self, ctx: Interaction):
        owner = self.bot.get_user(OWNER_ID)
        await ctx.response.send_message(f"""`user_id` - The ID of the user who left (example: 1038466644353232967)
`user_username` - The username of the user who left (example: {owner.name if owner else "aztophel"})
`user_display` - The display name of the user who left (example: {owner.display_name if owner else "Jack"}) (recommended to use for names)
`user_global` - The global name of the user who left (n/a if no global name)
`user_nick` - The nickname of the user who left (n/a if no nick)
`user_mention` - The mention of the user who joined (example: <@{owner.id}>)
`user_created_date` - The date when the user's account was created (example: <t:1522306800>)
`user_joined_date` - The date when the user joined
`user_created_date_rel` - The relative date when the user's account was created (example: <t:1522306800:R>)
`user_joined_date_rel` - The relative date when the user joined
`guild_name` - The name of the server
`guild_membercount` - The total number of members present in the server

**Important**: These variables should be enclosed with `${{}}`.""", ephemeral=True)

    @leave_group.command(name="message", description="Set the leave message, channel, and (optionally) an image")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(message="Template (use \\n for newlines)", channel="Channel to send in",
              image="An image to attach to the plain-text message", remove_image="Remove the currently attached image")
    async def leave_message(self, ctx: Interaction, message: str = None, channel: Union[GuildChannel, Thread] = None,
                             image: Attachment = None, remove_image: bool = False):
        if not ctx.permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission.")
            return
        if message:
            message = message.replace("\\n", "\n")
        await ctx.response.defer(ephemeral=True)
        current = await db.db_get_leave_msg(ctx.guild_id)
        msg_image = current.get("msg_image")
        if remove_image:
            msg_image = None
        elif image is not None:
            msg_image = await persist_attachment(self.bot, image)
        await db.db_set_leave_msg(ctx.guild_id, message, channel.id if channel else None, current.get("embed"), msg_image)
        if message is None or channel is None:
            await ctx.followup.send("Leave message removed.")
        else:
            img_note = " (with an attached image)" if msg_image else ""
            await ctx.followup.send(f"Leave messages{img_note} → {channel.mention}\n```\n{message}\n```")

    @leave_group.command(name="embed", description="Configure a rich embed for the leave message instead of/alongside plain text")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(
        title="Embed title (use \\n for newlines, supports ${} variables)",
        description="Embed description (use \\n for newlines, supports ${} variables)",
        color="Hex color, e.g. #FF0000",
        image="Large bottom image (attachment, takes priority over image_url)", image_url="Large bottom image (URL)",
        thumbnail="Small corner image (attachment, takes priority over thumbnail_url)", thumbnail_url="Small corner image (URL)",
        clear="Remove the embed and go back to plain text only",
    )
    async def leave_embed(self, ctx: Interaction, title: str = None, description: str = None, color: str = None,
                           image: Attachment = None, image_url: str = None,
                           thumbnail: Attachment = None, thumbnail_url: str = None, clear: bool = False):
        if not ctx.permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission.", ephemeral=True)
            return
        current = await db.db_get_leave_msg(ctx.guild_id)
        if clear:
            await db.db_set_leave_msg(ctx.guild_id, current["msg"], current["chan"], None, current.get("msg_image"))
            await ctx.response.send_message("Leave embed removed.", ephemeral=True)
            return

        await ctx.response.defer(ephemeral=True)
        embed_data = current.get("embed") or {}
        if title is not None:
            embed_data["title"] = title.replace("\\n", "\n")
        if description is not None:
            embed_data["description"] = description.replace("\\n", "\n")
        if color is not None:
            try:
                embed_data["color"] = int(color.lstrip("#"), 16)
            except ValueError:
                await ctx.followup.send("Invalid hex color.")
                return
        if image is not None:
            embed_data["image"] = await persist_attachment(self.bot, image)
        elif image_url is not None:
            embed_data["image"] = image_url
        if thumbnail is not None:
            embed_data["thumbnail"] = await persist_attachment(self.bot, thumbnail)
        elif thumbnail_url is not None:
            embed_data["thumbnail"] = thumbnail_url

        if not embed_data.get("title") and not embed_data.get("description"):
            await ctx.followup.send("Set at least a title or description.")
            return

        await db.db_set_leave_msg(ctx.guild_id, current["msg"], current["chan"], embed_data, current.get("msg_image"))
        await ctx.followup.send(f"Leave embed updated! Preview with {cmd_mention('leave preview')}.")

    @leave_group.command(name="preview", description="Preview the configured leave message")
    async def preview_leave(self, ctx: Interaction):
        leave = await db.db_get_leave_msg(ctx.guild_id)
        content = format_message(leave["msg"], ctx.user) if leave.get("msg") else None
        if leave.get("embed"):
            await ctx.response.send_message(content=content, embed=build_leave_embed(leave["embed"], ctx.user), ephemeral=True)
        elif leave.get("msg_image"):
            img_embed = Embed(color=0x2B2D31)
            img_embed.set_image(url=leave["msg_image"])
            await ctx.response.send_message(content=content, embed=img_embed, ephemeral=True)
        elif leave.get("msg") and leave.get("chan"):
            await ctx.response.send_message(content=content, ephemeral=True)
        else:
            await ctx.response.send_message("No leave message configured.", ephemeral=True)
    # endregion

    # region listener
    @commands.Cog.listener()
    async def on_member_remove(self, member: Member):
        leave = await db.db_get_leave_msg(member.guild.id)
        if not leave.get("chan"):
            return
        channel = member.guild.get_channel_or_thread(leave["chan"])
        if not channel:
            return
        content = format_message(leave["msg"], member) if leave.get("msg") else None
        if leave.get("embed"):
            await channel.send(content=content, embed=build_leave_embed(leave["embed"], member))
        elif leave.get("msg_image"):
            img_embed = Embed(color=0x2B2D31)
            img_embed.set_image(url=leave["msg_image"])
            await channel.send(content=content, embed=img_embed)
        elif leave.get("msg"):
            await channel.send(content)
    # endregion


async def setup(bot: commands.Bot):
    await bot.add_cog(LeaveMessagesCog(bot))

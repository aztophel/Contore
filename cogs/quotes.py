"""Quotebook: the `Quote` context menu (snapshots a message + its reply
target so it survives message deletion/edits), `/quotes view` (paginated,
filterable, sortable), and the legacy on-the-fly display path used for
quotes saved before snapshotting existed.
"""
from __future__ import annotations
import json
import re
from datetime import datetime
from typing import Literal, Union

from discord import (
    Attachment, ButtonStyle, Color, Embed, File, Interaction, Message,
    NotFound, StickerFormatType, app_commands,
)
from discord.app_commands import ContextMenu, Group, allowed_contexts, describe
from discord.ext import commands
from discord.ui import Button, View
from httpx import AsyncClient

import core.db as db
from core.attachments import persist_attachment
from core.discord_utils import ALL, ALL_INSTALLS

# Only the legacy (pre-snapshot) quote-display fallback still needs raw
# attachment bytes on demand; every other feature that persists media goes
# through `core.attachments.persist_attachment` instead.
_ATTACHMENT_RETRIEVER = AsyncClient()


async def _persist_message_snapshot(bot: commands.Bot, message: Message) -> list[dict]:
    """Freezes a message (and, if it's a reply, the message it replies to)
    into a JSON-able snapshot so the quote survives the original message
    being edited or deleted."""
    snapshot: list[dict] = []

    async def _snap_one(msg: Message, prefix: str = "", color: Color = Color(0x2B2D31)) -> dict:
        content = msg.system_content
        files_meta = []
        image_url = None
        extra_images = []

        if msg.attachments:
            for attachment in msg.attachments:
                try:
                    url = await persist_attachment(bot, attachment)
                except Exception:
                    url = attachment.url
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    if image_url is None:
                        image_url = url
                    else:
                        extra_images.append(url)
                else:
                    files_meta.append({
                        "url": url, "filename": attachment.filename,
                        "spoiler": attachment.is_spoiler(), "description": attachment.description
                    })

        if msg.stickers:
            for sticker in msg.stickers:
                if sticker.format is StickerFormatType.apng:
                    content += f"\n*Sent a sticker: {sticker.name}*"
                elif image_url is None:
                    image_url = sticker.url
                else:
                    extra_images.append(sticker.url)

        return {
            "type": "primary",
            "author_name": f"{prefix}{msg.author.display_name}{' 🤖' if msg.author.bot or msg.author.system else ''}",
            "author_icon": msg.author.display_avatar.url if msg.author.display_avatar else None,
            "jump_url": msg.jump_url,
            "content": content,
            "image_url": image_url,
            "extra_images": extra_images,
            "color": color.value,
            "timestamp": msg.created_at.isoformat(),
            "files": files_meta,
        }

    if message.reference and message.reference.message_id:
        try:
            ref_message = message.reference.cached_message or await message.channel.fetch_message(message.reference.message_id)
            snapshot.append(await _snap_one(ref_message, prefix="Replying to "))
        except Exception:
            pass

    snapshot.append(await _snap_one(message, color=Color(0xEEE2A0)))

    for e in message.embeds:
        if e.type == "rich" or not e.url:
            snapshot.append({"type": "raw_embed", "data": e.to_dict()})

    return snapshot


def _rebuild_quote_display(snapshot: list[dict]) -> tuple[list[Embed], list]:
    embeds: list[Embed] = []
    for entry in snapshot:
        if entry.get("type") == "raw_embed":
            embeds.append(Embed.from_dict(entry["data"]))
            continue
        embed = Embed(description=entry.get("content") or None, color=entry.get("color", 0x2B2D31))
        if entry.get("timestamp"):
            try:
                embed.timestamp = datetime.fromisoformat(entry["timestamp"])
            except Exception:
                pass
        embed.set_author(name=entry.get("author_name", "Unknown"), url=entry.get("jump_url"),
                          icon_url=entry.get("author_icon"))
        if entry.get("image_url"):
            embed.set_image(url=entry["image_url"])
        embeds.append(embed)
        for extra_url in entry.get("extra_images", []):
            embeds.append(Embed(color=entry.get("color", 0x2B2D31)).set_image(url=extra_url))
    return embeds, []


def _safe_guild_and_channel(bot: commands.Bot, guild_id: int, channel_id: int):
    guild = bot.get_guild(guild_id)
    channel = None
    if guild is not None:
        try:
            channel = guild.get_channel_or_thread(channel_id)
        except Exception:
            channel = None
    return guild, channel


async def format_embed(message: Message, prefix="", color=Color(0x2B2D31)):
    """Legacy (pre-snapshot) single-message embed formatter. Only reachable
    via `_get_quote_display`'s fallback for quotes saved before snapshotting
    existed -- new quotes never touch this path."""
    filenames = []
    embeds = []
    embed = Embed(description=message.system_content, color=color, timestamp=message.created_at)
    embed.set_author(
        name=f"{prefix}{message.author.display_name}{' 🤖' if message.author.bot or message.author.system else ''}",
        url=message.jump_url,
        icon_url=message.author.avatar.url,
    )
    if message.attachments:
        for attachment in message.attachments:
            if attachment.content_type.startswith("image/"):
                if getattr(embed, "_image", None) is None:
                    embed.set_image(url=attachment.url)
                else:
                    embeds.append(Embed().set_image(url=attachment.url))
    if message.stickers:
        for sticker in message.stickers:
            if sticker.format is StickerFormatType.apng:
                embed.description += f"\n*Sent a sticker: {sticker.name}*"
            else:
                if getattr(embed, "_image", None) is None:
                    embed.set_image(url=sticker.url)
                else:
                    embeds.append(Embed().set_image(url=sticker.url))
    embeds.append(embed)
    embeds.extend(message.embeds)
    return embeds, filenames


async def copy_attachments(message: Message):
    """Legacy fallback companion to `format_embed` -- re-fetches non-image
    attachment bytes on demand instead of relying on a persisted URL."""
    attachments = []
    if message.attachments:
        for attachment in message.attachments:
            if not attachment.content_type.startswith("image/"):
                async with _ATTACHMENT_RETRIEVER as h:
                    response = await h.get(attachment.url)
                    if response.status_code == 200:
                        from io import BytesIO
                        attachment_bytes = response.read()
                        file = File(BytesIO(attachment_bytes), filename=attachment.filename,
                                    spoiler=attachment.is_spoiler(), description=attachment.description)
                        attachments.append(file)
    return attachments


async def format_message_into_embed(message: Message | None):
    embeds, attachments = [], []
    if message:
        if message.reference:
            try:
                ref_message = await message.channel.fetch_message(message.reference.message_id)
                ref_embeds, _ = await format_embed(ref_message, prefix="Replying to ")
                embeds.extend(ref_embeds)
                attachments.extend(await copy_attachments(ref_message))
            except NotFound:
                pass
        orig_embeds, _ = await format_embed(message, color=Color(0xEEE2A0))
        embeds.extend(orig_embeds)
        attachments.extend(await copy_attachments(message))
    return embeds, attachments


async def _get_quote_display(bot: commands.Bot, quot: dict) -> tuple[list[Embed], list]:
    if quot.get("snapshot"):
        try:
            return _rebuild_quote_display(json.loads(quot["snapshot"]))
        except Exception:
            pass
    # Legacy fallback for quotes saved before snapshots existed.
    _, channel = _safe_guild_and_channel(bot, quot["guild_id"], quot["channel_id"])
    try:
        msg = await channel.fetch_message(quot["message_id"]) if channel else None
    except Exception:
        msg = None
    return await format_message_into_embed(msg)


class QuotePageMessage(View):
    def __init__(self, bot: commands.Bot, quotes: list[dict], embeds: list[Union[tuple, None]],
                 user_id: int, index: int = 0, timeout: float = None):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.quotes = quotes
        self.embeds = embeds
        self.user_id = user_id
        self.current_page = index

        self.left_button = Button(label="◀", style=ButtonStyle.gray, disabled=index == 0)
        self.idx = Button(label=str(index + 1), style=ButtonStyle.gray, disabled=True)
        self.right_button = Button(label="▶", style=ButtonStyle.gray,
                                    disabled=len(self.embeds) < 2 or index >= len(self.embeds) - 1 or index == -1)
        self.remove_quote_button = Button(label="X", style=ButtonStyle.red)
        self.left_button.callback = self.left
        self.right_button.callback = self.right
        self.remove_quote_button.callback = self.remove_quote
        self.add_item(self.left_button)
        self.add_item(self.idx)
        self.add_item(self.right_button)
        self.add_item(self.remove_quote_button)

    async def update_buttons(self, ctx: Interaction):
        self.right_button.disabled = self.current_page >= len(self.embeds) - 1
        self.left_button.disabled = self.current_page - 1 < 0
        self.idx.label = str(self.current_page + 1)

        await ctx.response.defer()
        if self.embeds[self.current_page] is None:
            quot = self.quotes[self.current_page]
            self.embeds[self.current_page] = await _get_quote_display(self.bot, quot)

        await ctx.edit_original_response(embeds=self.embeds[self.current_page][0],
                                          attachments=self.embeds[self.current_page][1], view=self)

    async def left(self, ctx: Interaction):
        if self.current_page >= 1:
            self.current_page -= 1
            await self.update_buttons(ctx)
        else:
            await ctx.response.defer()

    async def right(self, ctx: Interaction):
        if self.current_page < len(self.embeds) - 1:
            self.current_page += 1
            await self.update_buttons(ctx)
        else:
            await ctx.response.defer()

    async def remove_quote(self, ctx: Interaction):
        if ctx.user.id == self.user_id:
            removed = self.quotes.pop(self.current_page)
            self.embeds.pop(self.current_page)
            await db.db_remove_quote(self.user_id, removed["id"])
            self.current_page -= 1
            await self.update_buttons(ctx)
        else:
            await ctx.response.send_message(f"You can't remove quotes from <@{self.user_id}>'s quotebook.", ephemeral=True)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


class QuotesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # `app_commands.context_menu` can't decorate a bound Cog method
        # directly (discord.py raises at class-body-eval time -- it needs
        # `self` to already be bound), so the context menu is built and
        # registered manually here instead, per discord.py's documented
        # Cog + context-menu pattern.
        self.quote_ctx_menu = ContextMenu(name="Quote", callback=self.quote, allowed_contexts=ALL)
        self.bot.tree.add_command(self.quote_ctx_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.quote_ctx_menu.name, type=self.quote_ctx_menu.type)

    # region /quotes
    quotes_group = Group(name="quotes", description="Quote commands", allowed_contexts=ALL, allowed_installs=ALL_INSTALLS)

    @quotes_group.command(name="view", description="View your quotes.")
    @describe(
        sort="The sorting method",
        order="The direction to sort in",
        public="Should the message be ephemeral (only visible to you)?",
        filter_type="How should the quotes be filtered",
        filter="The value for the filter",
        index="The index of the quote to start on",
    )
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def quotes_view(
        self, ctx: Interaction,
        sort: Literal[
            "Date Added", "Display Name (Alphabetical)", "Username (Alphabetical)", "Account ID"
        ] = "Date Added",
        order: Literal["Descending", "Ascending"] = "Ascending",
        public: bool = False,
        filter_type: Literal["Before Date", "After Date", "From User", "From Server"] = None,
        filter: str = None,
        index: int = 1,
    ):
        quotes = await db.db_get_quotes(ctx.user.id)
        if not quotes:
            await ctx.response.send_message("you don't have any quotes!", ephemeral=True)
            return

        if filter_type and filter:
            try:
                if filter_type in ("Before Date", "After Date"):
                    cutoff = datetime.strptime(filter.strip(), "%Y-%m-%d")
                    if filter_type == "Before Date":
                        quotes = [q for q in quotes if datetime.strptime(q["created_at"][:10], "%Y-%m-%d") < cutoff]
                    else:
                        quotes = [q for q in quotes if datetime.strptime(q["created_at"][:10], "%Y-%m-%d") > cutoff]
                elif filter_type == "From User":
                    uid = int(re.sub(r"\D", "", filter))
                    quotes = [q for q in quotes if q["author_id"] == uid]
                elif filter_type == "From Server":
                    gid = int(re.sub(r"\D", "", filter))
                    quotes = [q for q in quotes if q["guild_id"] == gid]
            except (ValueError, TypeError):
                await ctx.response.send_message("Invalid filter value for the selected filter type.", ephemeral=True)
                return

            if not quotes:
                await ctx.response.send_message("No quotes match that filter.", ephemeral=True)
                return

        if sort == "Date Added":
            quotes.sort(key=lambda q: q["added_at"])
        elif sort == "Account ID":
            quotes.sort(key=lambda q: q["author_id"])
        elif sort in ("Display Name (Alphabetical)", "Username (Alphabetical)"):
            keys = []
            for q in quotes:
                try:
                    user = self.bot.get_user(q["author_id"]) or await self.bot.fetch_user(q["author_id"])
                    keys.append((user.name if sort.startswith("Username") else user.display_name).lower())
                except Exception:
                    keys.append("")
            quotes = [q for _, q in sorted(zip(keys, quotes), key=lambda pair: pair[0])]

        if index < 1 or index > len(quotes):
            await ctx.response.send_message(f"cannot use indices outside 1–{len(quotes)}", ephemeral=True)
            return

        embeds = [None for _ in quotes]
        quot = quotes[index - 1]
        embeds[index - 1] = await _get_quote_display(self.bot, quot)

        if order == "Descending":
            quotes.reverse()
            embeds.reverse()
            index = len(quotes) - index + 1

        await ctx.response.send_message(
            embeds=embeds[index - 1][0],
            files=embeds[index - 1][1],
            view=QuotePageMessage(self.bot, quotes, embeds, ctx.user.id, index - 1),
            ephemeral=not public,
        )
    # endregion

    # region context menu
    async def quote(self, ctx: Interaction, msg: Message):
        await ctx.response.defer(ephemeral=True)
        snapshot = await _persist_message_snapshot(self.bot, msg)
        embeds, _ = _rebuild_quote_display(snapshot)
        await ctx.followup.send(f"Added {msg.author.mention}'s message to your quotes.", embeds=embeds[:10])
        await db.db_add_quote(
            ctx.user.id,
            msg.author.id,
            msg.guild.id, msg.channel.id, msg.id,
            msg.system_content,
            msg.created_at.strftime("%Y-%m-%d %H:%M:%S.%f"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
            snapshot=snapshot,
        )
    # endregion


async def setup(bot: commands.Bot):
    await bot.add_cog(QuotesCog(bot))

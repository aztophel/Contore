"""Proxies: per-user "speak as" personas relayed through a per-channel
webhook (PluralKit-style). Handles proxy CRUD, autoproxy (auto-apply the
last-used proxy), and the `on_message` pipeline that matches a message
against a proxy invocation (or autoproxy, or neither) and relays it --
applying the sender's uwu/posh text filter and fake-nitro emoji rendering
along the way, and forwarding any message-snapshot (Discord "forward")
content/attachments/embeds it carries.
"""
from __future__ import annotations
import re
from datetime import datetime
from io import BytesIO

from discord import (
    AllowedMentions, Attachment, Embed, File, Guild, Interaction, Message,
    MessageReferenceType, NotFound, Thread, Webhook, app_commands,
)
from discord.app_commands import Choice, Group, autocomplete, describe
from discord.ext import commands
from discord.utils import MISSING

import core.db as db
from cogs.fake_nitro import convert_emoji_shortcodes, get_animated_emoji_lookup, handle_react_command
from core.attachments import persist_attachment
from core.discord_utils import GUILD, GUILD_INSTALL
from core.emoji import custom_hash_string, descape
from core.text import filter_embeds, format_reply, poshify, uwuify

# Per-channel webhook cache. Keyed by channel ID (a thread and its parent
# share the parent's webhook, resolved via `get_or_create_webhook`).
WEBHOOKS: dict[int, Webhook] = {}


async def get_proxy_name(bot: commands.Bot, guild: Guild, proxy: dict) -> str:
    if "nicks" in proxy and guild is not None and guild.id in proxy["nicks"]:
        return proxy["nicks"][guild.id]
    if "tracked_user_name" in proxy:
        try:
            return (guild.get_member(proxy["tracked_user_name"]) or await guild.fetch_member(proxy["tracked_user_name"])).display_name
        except Exception:
            pass
        try:
            return (bot.get_user(proxy["tracked_user_name"]) or await bot.fetch_user(proxy["tracked_user_name"])).display_name
        except Exception:
            pass
    return proxy.get("name", "Unknown User")


async def get_proxy_avatar(bot: commands.Bot, guild: Guild, proxy: dict) -> str | None:
    if "tracked_user_avatar" in proxy:
        try:
            return (guild.get_member(proxy["tracked_user_avatar"]) or await guild.fetch_member(proxy["tracked_user_avatar"])).display_avatar.url
        except Exception:
            pass
        try:
            return (bot.get_user(proxy["tracked_user_avatar"]) or await bot.fetch_user(proxy["tracked_user_avatar"])).display_avatar.url
        except Exception:
            pass
    return proxy.get("avatar_url")


async def get_or_create_webhook(bot: commands.Bot, cid: int) -> tuple[Webhook, Thread]:
    webhook = WEBHOOKS.get(cid)
    chan = bot.get_channel(cid) or await bot.fetch_channel(cid)
    if webhook is not None:
        return (webhook, chan)
    thread = isinstance(chan, Thread)
    c = chan.parent if thread else chan
    for w in await c.webhooks():
        if w.name == "Contore Proxy" and w.user and w.user.id == bot.user.id:
            WEBHOOKS[cid] = w
            return (w, chan)
    webhook = await c.create_webhook(name="Contore Proxy")
    WEBHOOKS[cid] = webhook
    return (webhook, chan)


def _forward_snapshot_extras(message: Message) -> tuple[str, list, list]:
    snapshots = getattr(message, "message_snapshots", None) or []
    if not snapshots:
        return "", [], []
    snap = snapshots[0]
    return (
        getattr(snap, "content", "") or "",
        list(getattr(snap, "attachments", []) or []),
        list(getattr(snap, "embeds", []) or []),
    )


async def _apply_text_filters(text: str, uwu: int | None, fakenitro_enabled: bool, lookup: dict[str, str]) -> str:
    if uwu == 0:
        text = await uwuify(text)
    elif uwu == 1:
        text = poshify(text)
    if fakenitro_enabled:
        text, _ = convert_emoji_shortcodes(text, lookup)
    return text


async def _prepend_reply_quote(message: Message, text: str) -> str:
    if message.reference is not None and message.reference.type == MessageReferenceType.reply:
        try:
            msg = message.reference.cached_message or await message.channel.fetch_message(message.reference.message_id)
            reply = format_reply(msg)
            text = f"> -# *In [reply to](<{message.reference.jump_url}>): ({msg.author.mention})*\n> -# {reply}\n" + (text or "")
        except Exception:
            pass
    return text


async def _relay_through_webhook(bot: commands.Bot, message: Message, webhook: Webhook, chan, text: str,
                                  username: str, avatar_url: str | None, fwd_attachments: list, fwd_embeds: list):
    """Shared tail end of every relay path: attach the message's own (plus
    any forwarded) attachments/embeds, send through the proxy webhook with
    matching allowed-mentions, then delete the original message."""
    all_attachments = list(message.attachments) + fwd_attachments
    all_embeds = filter_embeds(list(message.embeds) + fwd_embeds)
    try:
        await webhook.send(
            text,
            username=username,
            avatar_url=avatar_url,
            thread=chan if isinstance(chan, Thread) else MISSING,
            files=[await attachment.to_file() for attachment in all_attachments] if all_attachments else MISSING,
            allowed_mentions=AllowedMentions(
                users=message.mentions, everyone=message.mention_everyone,
                replied_user=bool(message.reference and message.reference.cached_message
                                   and message.reference.cached_message.author.id in message.mentions)),
            embeds=all_embeds or MISSING,
        )
    except Exception:
        pass
    try:
        await message.delete()
    except NotFound:
        pass


async def do_proxy_things(bot: commands.Bot, message: Message):
    proxies = await db.db_get_proxies(message.author.id)
    uwu = await db.db_get_uwu_type(message.guild.id, message.author.id)
    fakenitro_enabled = await db.db_get_fake_nitro_enabled(message.guild.id)
    lookup = get_animated_emoji_lookup(bot) if fakenitro_enabled else {}

    fwd_content, fwd_attachments, fwd_embeds = _forward_snapshot_extras(message)

    if proxies:
        for key, proxy in proxies.items():
            m = re.fullmatch("^" + re.escape(proxy["invocation"]).replace("text", r"((.|\s)+)") + "$", message.content)
            if m:
                webhook, chan = await get_or_create_webhook(bot, message.channel.id)
                await db.db_update_last_proxy_use(message.author.id, key)
                text = await _apply_text_filters(m.group(1), uwu, fakenitro_enabled, lookup)
                if fwd_content:
                    text = f"{text}\n\n{fwd_content}" if text else fwd_content
                text = await _prepend_reply_quote(message, text)
                await _relay_through_webhook(
                    bot, message, webhook, chan, text,
                    await get_proxy_name(bot, message.guild, proxy), await get_proxy_avatar(bot, message.guild, proxy),
                    fwd_attachments, fwd_embeds)
                return

    ap = await db.db_get_autoproxy(message.author.id)
    if ap and ap["enabled"] and ap.get("last_proxy_key") and not message.content.startswith("\\"):
        expired = False
        if ap.get("timeout_secs") and ap.get("last_used_at"):
            elapsed = (datetime.now() - datetime.fromisoformat(ap["last_used_at"])).total_seconds()
            expired = elapsed > ap["timeout_secs"]
        proxy = proxies.get(ap["last_proxy_key"])
        if not expired and proxy:
            await db.db_update_last_proxy_use(message.author.id, ap["last_proxy_key"])
            webhook, chan = await get_or_create_webhook(bot, message.channel.id)
            text = await _apply_text_filters(message.content, uwu, fakenitro_enabled, lookup)
            if fwd_content:
                text = f"{text}\n\n{fwd_content}" if text else fwd_content
            text = await _prepend_reply_quote(message, text)
            await _relay_through_webhook(
                bot, message, webhook, chan, text,
                await get_proxy_name(bot, message.guild, proxy), await get_proxy_avatar(bot, message.guild, proxy),
                fwd_attachments, fwd_embeds)
            return

    base_text = message.system_content or message.content
    text = await _apply_text_filters(base_text, uwu, fakenitro_enabled, lookup)

    if text != base_text:
        webhook, chan = await get_or_create_webhook(bot, message.channel.id)
        if fwd_content:
            text = f"{text}\n\n{fwd_content}" if text else fwd_content
        text = await _prepend_reply_quote(message, text)
        await _relay_through_webhook(
            bot, message, webhook, chan, text,
            message.author.display_name, message.author.display_avatar.url,
            fwd_attachments, fwd_embeds)


class ProxiesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # region /proxy
    proxy_group = Group(name="proxy", description="Proxy commands", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL)

    async def _proxy_autocomplete(self, ctx: Interaction, current: str) -> list[Choice[str]]:
        proxies = await db.db_get_proxies(ctx.user.id)

        results = []
        for key, proxy in proxies.items():
            try:
                name = await get_proxy_name(self.bot, ctx.guild, proxy)
            except Exception:
                name = "Unknown User"
            results.append((key, name))

        results.sort(key=lambda x: x[1].lower())
        current = current.lower().strip()

        return [Choice(name=name, value=str(key)) for key, name in results
                if current in name.lower() or current in key.lower()][:25]

    @proxy_group.command(name="create", description="Creates a new proxy")
    @describe(
        invocation="How you will invoke the usage of this proxy",
        name="The name of the proxy (mention a user to copy their name)",
        pfp="The avatar of the proxy",
        pfp_url="The avatar URL of the proxy (mention a user to copy their avatar)",
        description="The description of the proxy",
    )
    async def proxy_create(self, ctx: Interaction, invocation: str, name: str, pfp: Attachment = None, pfp_url: str = None, description: str = None):
        if pfp is not None and pfp_url is not None:
            await ctx.response.send_message("you cannot set both `pfp` and `pfp_url`!", ephemeral=True)
            return
        if name.strip() == "":
            await ctx.response.send_message("name must be at least 1 character", ephemeral=True)
            return

        texts = invocation.count("text")
        if texts <= 0:
            await ctx.response.send_message("the proxy invocation must include the keyword `text` somewhere!", ephemeral=True)
            return
        if texts > 1:
            await ctx.response.send_message("the proxy invocation must not use the keyword `text` more than once!", ephemeral=True)
            return

        proxy = {}
        m = re.fullmatch(r"^<@!?(\d+)>$", name.strip())
        proxy["invocation"] = invocation.strip()

        if m:
            proxy["tracked_user_name"] = int(m.group(1))
        else:
            proxy["name"] = name.strip()

        if pfp:
            proxy["avatar_url"] = await persist_attachment(self.bot, pfp)
        elif pfp_url:
            m = re.fullmatch(r"^<@!?(\d+)>$", pfp_url.strip())
            if m:
                proxy["tracked_user_avatar"] = int(m.group(1))
            else:
                proxy["avatar_url"] = pfp_url.strip()
        else:
            proxy["avatar_url"] = None

        if description:
            if len(description) > 1024:
                await ctx.response.send_message("Please keep descriptions to 1024 characters or fewer.", ephemeral=True)
                return
            proxy["description"] = description.strip()

        key = str(proxy.get("tracked_user_name", proxy.get("name"))) + ("_proxy" if "tracked_user_name" in proxy else "")
        await db.db_save_proxy(ctx.user.id, key, proxy)

        username = descape(await get_proxy_name(self.bot, ctx.guild, proxy))
        avatar = await get_proxy_avatar(self.bot, ctx.guild, proxy)

        embed = Embed(title=username, color=custom_hash_string(username),
                      description=f"**Invocation**: `{descape(invocation)}`\n**Avatar**: "
                      + (f"[link]({avatar})" if avatar is not None else "N/A")
                      + (f"\n{description}" if description else "")
                      + f"\n\nSay hi with {username} by typing:\n> {descape(invocation).replace('text', 'Hello!')}")
        if avatar:
            embed.set_thumbnail(url=avatar)
        proxies = len(await db.db_get_proxies(ctx.user.id))
        await ctx.response.send_message(f"Proxy registered! You now have {proxies} {'proxies' if abs(proxies) != 1 else 'proxy'}.", embed=embed)

    @proxy_group.command(name="delete", description="Deletes a proxy")
    @describe(proxy="The proxy to delete")
    @autocomplete(proxy=_proxy_autocomplete)
    async def proxy_delete(self, ctx: Interaction, proxy: str):
        if not await db.db_delete_proxy(ctx.user.id, proxy):
            await ctx.response.send_message(f"you don't have any registered proxies by the name of `{proxy}`", ephemeral=True)
            return

        proxies = len(await db.db_get_proxies(ctx.user.id))
        await ctx.response.send_message(f"Proxy deleted successfully. You now have {proxies} {'proxies' if abs(proxies) != 1 else 'proxy'}.", ephemeral=True)

    @proxy_group.command(name="edit", description="Edits a proxy")
    @describe(
        proxy="The proxy to edit",
        invocation="The new invocation for the proxy (leave blank to keep unchanged)",
        name="The new name for the proxy (leave blank to keep unchanged)",
        pfp="The new avatar for the proxy (leave blank to keep unchanged)",
        pfp_url="The new avatar URL for the proxy (leave blank to keep unchanged)",
        description="The description of the proxy (leave blank to keep unchanged)",
    )
    @autocomplete(proxy=_proxy_autocomplete)
    async def proxy_edit(self, ctx: Interaction, proxy: str, invocation: str = None, name: str = None,
                          pfp: Attachment = None, pfp_url: str = None, description: str = None):
        p = await db.db_get_proxy(ctx.user.id, proxy)
        if not p:
            await ctx.response.send_message(f"you don't have any registered proxies by the name of `{proxy}`", ephemeral=True)
            return

        if pfp is not None and pfp_url is not None:
            await ctx.response.send_message("you cannot set both `pfp` and `pfp_url`!", ephemeral=True)
            return

        if invocation is not None:
            texts = invocation.count("text")
            if texts <= 0:
                await ctx.response.send_message("the proxy invocation must include the keyword `text` somewhere!", ephemeral=True)
                return
            if texts > 1:
                await ctx.response.send_message("the proxy invocation must not use the keyword `text` more than once!", ephemeral=True)
                return
            p["invocation"] = invocation

        if name is not None:
            if name.strip() == "":
                await ctx.response.send_message("name must be at least 1 character", ephemeral=True)
                return
            m = re.fullmatch(r"^<@!?(\d+)>$", name.strip())
            if m:
                p["tracked_user_name"] = int(m.group(1))
                if "name" in p:
                    del p["name"]
            else:
                p["name"] = name.strip()
                if "tracked_user_name" in p:
                    del p["tracked_user_name"]
            await db.db_rename_proxy(ctx.user.id, proxy, str(p.get("tracked_user_name", p.get("name"))))
            proxy = str(p.get("tracked_user_name", p.get("name")))

        if pfp is not None:
            p["avatar_url"] = await persist_attachment(self.bot, pfp)
            if "tracked_user_avatar" in p:
                del p["tracked_user_avatar"]
        elif pfp_url is not None:
            m = re.fullmatch(r"^<@!?(\d+)>$", pfp_url.strip())
            if m:
                p["tracked_user_avatar"] = int(m.group(1))
                if "avatar_url" in p:
                    del p["avatar_url"]
            else:
                p["avatar_url"] = pfp_url.strip()
                if "tracked_user_avatar" in p:
                    del p["tracked_user_avatar"]

        if description is not None:
            p["description"] = description.strip() or None

        await db.db_save_proxy(ctx.user.id, proxy, p)

        username = descape(await get_proxy_name(self.bot, ctx.guild, p))
        avatar = await get_proxy_avatar(self.bot, ctx.guild, p)
        embed = Embed(title=username, color=custom_hash_string(username),
                      description=f"**Invocation**: `{descape(p['invocation'])}`\n**Avatar**: " + (f"[link]({avatar})" if avatar is not None else "N/A")
                      + f"\n\nSay hi with {username} by typing:\n> {descape(p['invocation']).replace('text', 'Hello!')}")
        if avatar:
            embed.set_thumbnail(url=avatar)
        await ctx.response.send_message("Proxy edited successfully!", embed=embed)

    @proxy_group.command(name="nick", description="Nicknames a proxy")
    @describe(proxy="The proxy to nickname", nick="The nickname for the proxy to use in this server (leave blank to remove)")
    @autocomplete(proxy=_proxy_autocomplete)
    async def proxy_nick(self, ctx: Interaction, proxy: str, nick: str = None):
        p = await db.db_get_proxy(ctx.user.id, proxy)
        if not p:
            await ctx.response.send_message(f"you don't have any registered proxies by the name of `{proxy}`", ephemeral=True)
            return

        if nick is None:
            if "nicks" in p and ctx.guild_id in p["nicks"]:
                del p["nicks"][ctx.guild_id]
                await db.db_save_proxy(ctx.user.id, proxy, p)
                await ctx.response.send_message(f"Removed nickname from `{descape(await get_proxy_name(self.bot, ctx.guild, proxy))}` in this server.", ephemeral=True)
                return
            else:
                await ctx.response.send_message(f"`{descape(await get_proxy_name(self.bot, ctx.guild, proxy))}` doesn't have a nickname to remove in this server.", ephemeral=True)
                return
        if "nicks" not in p:
            p["nicks"] = {}

        name = await get_proxy_name(self.bot, ctx.guild, proxy)
        p["nicks"][ctx.guild_id] = nick
        await db.db_save_proxy(ctx.user.id, proxy, p)
        await ctx.response.send_message(f"Nickname for `{descape(name)}` set to `{descape(nick)}` in this server.", ephemeral=True)

    @proxy_group.command(name="autoproxy", description="Automatically apply your last-used proxy to your messages")
    @describe(enabled="Turn autoproxy on or off", timeout_minutes="Stop autoproxying after this many minutes without a proxied message (blank = never)")
    async def proxy_autoproxy(self, ctx: Interaction, enabled: bool, timeout_minutes: float = None):
        timeout_secs = int(timeout_minutes * 60) if timeout_minutes is not None else None
        await db.db_set_autoproxy(ctx.user.id, enabled, timeout_secs)
        if enabled:
            msg = "Autoproxy **enabled**! Your most recently used proxy will now apply automatically."
            msg += f" It stops after **{timeout_minutes:g}** minute(s) of inactivity." if timeout_secs else " It never expires on its own."
        else:
            msg = "Autoproxy **disabled**."
        await ctx.response.send_message(msg, ephemeral=True)

    @proxy_group.command(name="list", description="Lists your proxies")
    @describe(ephemeral="Whether to make this list visible only to you or not")
    async def proxy_list(self, ctx: Interaction, ephemeral: bool = True):
        proxies = await db.db_get_proxies(ctx.user.id)
        if not proxies:
            await ctx.response.send_message("you don't have any proxies!", ephemeral=True)
            return

        embeds = []
        current_embed = Embed(title="Your Proxies", color=0x2b2d31)
        count = 0

        for proxy in proxies.values():
            name = descape(await get_proxy_name(self.bot, ctx.guild, proxy))
            avatar = await get_proxy_avatar(self.bot, ctx.guild, proxy)
            invocation = descape(proxy["invocation"])

            value = f"**Invocation**: `{invocation}`\n"
            value += f"**Preview**: {invocation.replace('text', 'Hello!')}"

            if avatar:
                value += f"\n[Avatar]({avatar})"

            current_embed.add_field(name=name, value=value, inline=False)
            count += 1

            if count % 25 == 0:
                embeds.append(current_embed)
                current_embed = Embed(title="Your Proxies (cont.)", color=0x2b2d31)

        if current_embed.fields:
            embeds.append(current_embed)

        await ctx.response.send_message(embeds=embeds, ephemeral=ephemeral)
    # endregion

    # region listener
    @commands.Cog.listener()
    async def on_message(self, message: Message):
        # Matches the monolith's own check exactly (`author.id ==
        # CONTORE.user.id`, not a general `.bot` check) -- proxy relay and
        # the !react shorthand are still meant to work for other bots'
        # messages, just never Contore's own.
        if message.author.id == self.bot.user.id or message.guild is None:
            return
        if await handle_react_command(self.bot, message):
            try:
                await message.delete()
            except NotFound:
                pass
            return
        await do_proxy_things(self.bot, message)
    # endregion


async def setup(bot: commands.Bot):
    await bot.add_cog(ProxiesCog(bot))

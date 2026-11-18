"""Fake Nitro: renders `:shortcode:` text as the bot's own animated emoji
when the sender doesn't have Nitro (so `:name:` becomes the real emoji if
the bot can see one by that name), plus the `!react <emoji>` shorthand that
reacts to the previous/replied-to message and auto-removes its own
reaction a few seconds later.

`get_animated_emoji_lookup`, `convert_emoji_shortcodes`, and
`handle_react_command` are public -- `proxies.py` calls all three while
building a proxied message, and `on_raw_reaction_add` in this cog handles
the react-command reaction's self-cleanup independently of proxies.
"""
from __future__ import annotations
import asyncio
import re

from discord import Emoji, Interaction, Message, NotFound, PartialEmoji
from discord.app_commands import Group, default_permissions, guild_only
from discord.ext import commands

import core.db as db
from core.discord_utils import GUILD, GUILD_INSTALL

_CUSTOM_EMOJI_RE = re.compile(r"^<a?:(\w{2,32}):(\d{15,21})>$")
FULL_EMOJI_RE = re.compile(r"<a?:\w{2,32}:\d{15,21}>")
SHORTCODE_RE = re.compile(r":([a-zA-Z0-9_]{2,32}):")
REACT_CMD_RE = re.compile(r"^!react\s+(.+)$", re.IGNORECASE)

_emoji_lookup_cache: dict[str, str] | None = None
_NITRO_REACTIONS: set[tuple[int, str]] = set()


def get_animated_emoji_lookup(bot: commands.Bot) -> dict[str, str]:
    global _emoji_lookup_cache
    if _emoji_lookup_cache is None:
        _emoji_lookup_cache = {e.name.lower(): str(e) for e in bot.emojis if e.animated}
    return _emoji_lookup_cache


def convert_emoji_shortcodes(content: str, lookup: dict[str, str]) -> tuple[str, bool]:
    """Replaces bare :name: shortcodes for animated emoji the bot can see with the real
    rendered emoji, without touching already-valid <a:name:id> tags."""
    placeholders: dict[str, str] = {}

    def _stash(m):
        key = f"\x00FAKENITRO{len(placeholders)}\x00"
        placeholders[key] = m.group(0)
        return key
    safe = FULL_EMOJI_RE.sub(_stash, content)

    changed = False

    def _sub(m):
        nonlocal changed
        emoji = lookup.get(m.group(1).lower())
        if emoji:
            changed = True
            return emoji
        return m.group(0)
    safe = SHORTCODE_RE.sub(_sub, safe)

    for key, val in placeholders.items():
        safe = safe.replace(key, val)
    return safe, changed


def resolve_emoji_token(bot: commands.Bot, token: str):
    token = token.strip()
    m = _CUSTOM_EMOJI_RE.match(token)
    if m:
        return bot.get_emoji(int(m.group(2))) or token
    if token.isdigit():
        emoji = bot.get_emoji(int(token))
        return emoji
    name = token.strip(":").lower()
    for e in bot.emojis:
        if e.name.lower() == name:
            return e
    return token


def _emoji_key(emoji) -> str:
    if isinstance(emoji, (Emoji, PartialEmoji)):
        return str(emoji.id) if emoji.id else emoji.name
    return str(emoji)


async def _expire_nqn_reaction(bot: commands.Bot, channel_id: int, message_id: int, emoji):
    await asyncio.sleep(3)
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        await channel.get_partial_message(message_id).remove_reaction(emoji, bot.user)
    except Exception:
        pass


async def handle_react_command(bot: commands.Bot, message: Message) -> bool:
    m = REACT_CMD_RE.match(message.content.strip())
    if not m:
        return False

    emoji = resolve_emoji_token(bot, m.group(1).strip())
    if emoji is None:
        await message.reply(f"Couldn't find an emoji matching `{m.group(1).strip()}`.", mention_author=False)
        return True

    target = None
    if message.reference and message.reference.message_id:
        try:
            target = message.reference.cached_message or await message.channel.fetch_message(message.reference.message_id)
        except NotFound:
            target = None
    else:
        async for prev in message.channel.history(limit=1, before=message):
            target = prev

    if target is None:
        await message.reply("Couldn't find a message to react to.", mention_author=False)
        return True

    try:
        await target.add_reaction(emoji)
    except Exception:
        await message.reply("I couldn't react with that emoji.", mention_author=False)
        return True

    _NITRO_REACTIONS.add((target.id, _emoji_key(emoji)))
    return True


class FakeNitroCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    nitro_group = Group(name="nitro", description="Fake Nitro settings", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL)

    @nitro_group.command(name="toggle", description="Enable or disable automatic animated-emoji rendering (Fake Nitro)")
    @default_permissions(manage_guild=True)
    @guild_only()
    async def nqn_toggle(self, ctx: Interaction, enabled: bool):
        if not ctx.permissions.manage_guild:
            return
        await db.db_set_fake_nitro_enabled(ctx.guild_id, enabled)
        await ctx.response.send_message(f"Fake Nitro **{'enabled' if enabled else 'disabled'}**.", ephemeral=True)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.user_id != self.bot.user.id:
            key = _emoji_key(payload.emoji)
            bookkey = (payload.message_id, key)
            if bookkey in _NITRO_REACTIONS:
                _NITRO_REACTIONS.discard(bookkey)
                asyncio.create_task(_expire_nqn_reaction(self.bot, payload.channel_id, payload.message_id, payload.emoji))

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after):
        global _emoji_lookup_cache
        _emoji_lookup_cache = None


async def setup(bot: commands.Bot):
    await bot.add_cog(FakeNitroCog(bot))

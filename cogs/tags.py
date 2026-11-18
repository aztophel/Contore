"""Tags: per-server saved snippets (`/tag add|embed|get|edit|delete|list`),
optionally fired automatically when someone sends the exact configured
trigger phrase (`/tag trigger`), with new/edited content checked against
the server's own AutoMod keyword/regex block rules before it's allowed to
save.

`_handle_tag_trigger` is public -- called from this cog's own
`on_message`, kept as a separate function (rather than inlined) since it
returns whether it fired, mirroring how the monolith let the caller decide
what "a tag fired" should short-circuit.
"""
from __future__ import annotations
import json
import re

from discord import AllowedMentions, Attachment, Embed, Guild, Interaction, Member, Message, app_commands
from discord.app_commands import Choice, Group, autocomplete, default_permissions, describe, guild_only
from discord.ext import commands

import core.db as db
from core.attachments import persist_attachment
from core.discord_utils import GUILD, GUILD_INSTALL
from core.mentions import cmd_mention

# Guild-scoped {trigger_phrase: tag_name} cache -- avoids a query on every
# single message just to check "is this exact text a tag trigger?".
_TAG_TRIGGER_CACHE: dict[int, dict[str, str]] = {}


async def _get_guild_tag_triggers(guild_id: int) -> dict[str, str]:
    cached = _TAG_TRIGGER_CACHE.get(guild_id)
    if cached is None:
        rows = await db.get_pool().fetch(
            "SELECT name, trigger_phrase FROM tags WHERE guild_id=$1 AND trigger_phrase IS NOT NULL", guild_id)
        cached = {r["trigger_phrase"]: r["name"] for r in rows}
        _TAG_TRIGGER_CACHE[guild_id] = cached
    return cached


async def _handle_tag_trigger(message: Message) -> bool:
    triggers = await _get_guild_tag_triggers(message.guild.id)
    trig_name = triggers.get(message.content.strip().lower())
    if not trig_name:
        return False
    tag = await db.db_get_tag(message.guild.id, trig_name)
    if not tag:
        return False
    await db.db_bump_tag_uses(message.guild.id, trig_name)
    content, embed = _render_tag(tag)
    try:
        await message.channel.send(content=content, embed=embed, allowed_mentions=AllowedMentions.none())
    except Exception:
        pass
    return True


def _invalidate_tag_cache(guild_id: int):
    _TAG_TRIGGER_CACHE.pop(guild_id, None)


def _render_tag(tag: dict) -> tuple[str | None, Embed | None]:
    embed = None
    if tag.get("embed_data"):
        d = json.loads(tag["embed_data"])
        embed = Embed(title=d.get("title") or None, description=d.get("description") or None,
                      color=d.get("color", 0x5865F2))
        if d.get("image"):
            embed.set_image(url=d["image"])
        if d.get("thumbnail"):
            embed.set_thumbnail(url=d["thumbnail"])
    elif tag.get("image_url"):
        embed = Embed(color=0x2B2D31)
        embed.set_image(url=tag["image_url"])
    return (tag["content"] or None), embed


async def _check_tag_automod_violation(guild: Guild, member: Member, content: str) -> str | None:
    try:
        rules = await guild.fetch_automod_rules()
    except Exception:
        return None
    lowered = content.lower()
    member_role_ids = {r.id for r in getattr(member, "roles", [])}
    for rule in rules:
        try:
            if not rule.enabled:
                continue
            if not any("block" in a.type.name.lower() for a in rule.actions):
                continue
            if member_role_ids & {r.id for r in rule.exempt_roles}:
                continue
            meta = rule.trigger_metadata
            for kw in (getattr(meta, "keyword_filter", None) or []):
                body = re.escape(kw.strip("*"))
                if kw.startswith("*") and kw.endswith("*"):
                    rx = re.compile(body, re.IGNORECASE)
                elif kw.startswith("*"):
                    rx = re.compile(body + r"$", re.IGNORECASE)
                elif kw.endswith("*"):
                    rx = re.compile(r"^" + body, re.IGNORECASE)
                else:
                    rx = re.compile(r"\b" + body + r"\b", re.IGNORECASE)
                if rx.search(lowered):
                    return rule.name
            for pat in (getattr(meta, "regex_patterns", None) or []):
                try:
                    if re.search(pat, content, re.IGNORECASE):
                        return rule.name
                except re.error:
                    continue
        except Exception:
            continue
    return None


async def _tag_content_passes_automod(ctx: Interaction, content: str) -> bool:
    if not content or not ctx.guild:
        return True
    violated = await _check_tag_automod_violation(ctx.guild, ctx.user, content)
    if violated:
        send = ctx.followup.send if ctx.response.is_done() else ctx.response.send_message
        await send(f"This would be blocked by this server's **{violated}** AutoMod rule, so it can't be saved as a tag.", ephemeral=True)
        return False
    return True


class TagsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    tag_group = Group(name="tag", description="Saved snippet commands", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL)

    async def _tag_name_autocomplete(self, ctx: Interaction, current: str) -> list[Choice[str]]:
        if not ctx.guild_id:
            return []
        tags = await db.db_get_all_tags(ctx.guild_id)
        return [Choice(name=t["name"], value=t["name"]) for t in tags if current.lower() in t["name"]][:25]

    @tag_group.command(name="add", description="Save a new tag (canned response)")
    @describe(content="The tag's text content (leave blank for an image-only tag)", image="An image to attach to the tag")
    async def tag_add(self, ctx: Interaction, name: str, content: str = None, image: Attachment = None):
        if len(name) > 32 or not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            await ctx.response.send_message("Tag names can be up to 32 characters, using only letters, numbers, hyphens, and underscores.", ephemeral=True)
            return
        if not content and not image:
            await ctx.response.send_message("Give the tag some content or an image.", ephemeral=True)
            return
        if await db.db_get_tag(ctx.guild_id, name):
            await ctx.response.send_message(f"A tag called `{name.lower()}` already exists. Use {cmd_mention('tag edit')} instead.", ephemeral=True)
            return
        if await db.db_get_tag_restrict(ctx.guild_id) and not ctx.user.guild_permissions.manage_guild:
            await ctx.response.send_message("Tag creation is restricted to server managers here.", ephemeral=True)
            return
        if content and not await _tag_content_passes_automod(ctx, content):
            return

        await ctx.response.defer(ephemeral=True)
        image_url = await persist_attachment(self.bot, image) if image else None
        await db.db_save_tag(ctx.guild_id, name, content or "", ctx.user.id, image_url)
        _invalidate_tag_cache(ctx.guild_id)
        await ctx.followup.send(f"Saved tag `{name.lower()}`. Use {cmd_mention('tag get')} to fetch it.")

    @tag_group.command(name="embed", description="Create or update a rich embed tag")
    @describe(
        title="Embed title", description="Embed description", color="Hex color, e.g. #FF0000",
        image="Image to embed (attachment, takes priority over image_url)", image_url="Image to embed (URL)",
        thumbnail="Thumbnail (attachment, takes priority over thumbnail_url)", thumbnail_url="Thumbnail (URL)",
        plain_content="Plain text sent alongside the embed"
    )
    @autocomplete(name=_tag_name_autocomplete)
    async def tag_embed(self, ctx: Interaction, name: str, title: str = None, description: str = None,
                         color: str = None, image: Attachment = None, image_url: str = None,
                         thumbnail: Attachment = None, thumbnail_url: str = None, plain_content: str = None):
        if len(name) > 32 or not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            await ctx.response.send_message("Invalid tag name.", ephemeral=True)
            return
        existing = await db.db_get_tag(ctx.guild_id, name)
        if not existing and await db.db_get_tag_restrict(ctx.guild_id) and not ctx.user.guild_permissions.manage_guild:
            await ctx.response.send_message("Tag creation is restricted to server managers here.", ephemeral=True)
            return
        if existing and existing["creator_id"] != ctx.user.id and not ctx.user.guild_permissions.manage_guild:
            await ctx.response.send_message("Only this tag's creator or an admin can edit it.", ephemeral=True)
            return
        check_text = " ".join(filter(None, [title, description, plain_content]))
        if check_text and not await _tag_content_passes_automod(ctx, check_text):
            return

        await ctx.response.defer(ephemeral=True)
        embed_data = json.loads(existing["embed_data"]) if existing and existing.get("embed_data") else {}
        if title is not None:
            embed_data["title"] = title
        if description is not None:
            embed_data["description"] = description
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

        if not embed_data.get("title") and not embed_data.get("description") and not embed_data.get("image"):
            await ctx.followup.send("Set at least a title, description, or image.")
            return

        content = plain_content if plain_content is not None else (existing["content"] if existing else "")
        if existing:
            await db.db_update_tag_embed(ctx.guild_id, name, embed_data, content)
        else:
            await db.db_save_tag(ctx.guild_id, name, content, ctx.user.id)
            await db.db_update_tag_embed(ctx.guild_id, name, embed_data)
        _invalidate_tag_cache(ctx.guild_id)
        await ctx.followup.send(f"Saved embed tag `{name.lower()}`.")

    @tag_group.command(name="get", description="Fetch a saved tag")
    @autocomplete(name=_tag_name_autocomplete)
    async def tag_get(self, ctx: Interaction, name: str):
        tag = await db.db_get_tag(ctx.guild_id, name)
        if not tag:
            await ctx.response.send_message(f"No tag called `{name.lower()}` exists.", ephemeral=True)
            return
        await db.db_bump_tag_uses(ctx.guild_id, name)
        content, embed = _render_tag(tag)
        await ctx.response.send_message(content=content, embed=embed, allowed_mentions=AllowedMentions.none())

    @tag_group.command(name="edit", description="Edit a tag's plain content")
    @autocomplete(name=_tag_name_autocomplete)
    async def tag_edit(self, ctx: Interaction, name: str, content: str):
        tag = await db.db_get_tag(ctx.guild_id, name)
        if not tag:
            await ctx.response.send_message(f"No tag called `{name.lower()}` exists.", ephemeral=True)
            return
        if tag["creator_id"] != ctx.user.id and not ctx.user.guild_permissions.manage_guild:
            await ctx.response.send_message("Only the tag's creator or an admin can edit it.", ephemeral=True)
            return
        if not await _tag_content_passes_automod(ctx, content):
            return
        await db.db_bump_tag_uses(ctx.guild_id, name)
        await db.db_update_tag_content(ctx.guild_id, name, content)
        await ctx.response.send_message(f"Updated tag `{name.lower()}`.", ephemeral=True)

    @tag_group.command(name="delete", description="Delete a tag")
    @autocomplete(name=_tag_name_autocomplete)
    async def tag_delete(self, ctx: Interaction, name: str):
        tag = await db.db_get_tag(ctx.guild_id, name)
        if not tag:
            await ctx.response.send_message(f"No tag called `{name.lower()}` exists.", ephemeral=True)
            return
        if tag["creator_id"] != ctx.user.id and not ctx.user.guild_permissions.manage_guild:
            await ctx.response.send_message("Only the tag's creator or an admin can delete it.", ephemeral=True)
            return
        await db.db_delete_tag(ctx.guild_id, name)
        _invalidate_tag_cache(ctx.guild_id)
        await ctx.response.send_message(f"Deleted tag `{name.lower()}`.", ephemeral=True)

    @tag_group.command(name="list", description="List every saved tag in this server")
    async def tag_list(self, ctx: Interaction):
        tags = await db.db_get_all_tags(ctx.guild_id)
        if not tags:
            await ctx.response.send_message("This server doesn't have any tags yet.", ephemeral=True)
            return
        lines = []
        for t in tags:
            flags = []
            if t.get("embed_data") or t.get("image_url"):
                flags.append("has image/embed")
            if t.get("trigger_phrase"):
                flags.append(f"trigger: `{t['trigger_phrase']}`")
            flag_str = f" ({', '.join(flags)})" if flags else ""
            lines.append(f"`{t['name']}` ​ · ​ used {t['uses']:,}× ​ · ​ <@{t['creator_id']}>{flag_str}")
        embed = Embed(title=f"{ctx.guild.name}'s Tags ({len(tags):,})", description="\n".join(lines)[:4000], color=0x5865F2)
        await ctx.response.send_message(embed=embed, ephemeral=True, allowed_mentions=AllowedMentions.none())

    @tag_group.command(name="trigger", description="Set the exact message that fires a tag (Manage Server)")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(phrase="The exact message that should trigger this tag (leave blank to clear)")
    @autocomplete(name=_tag_name_autocomplete)
    async def tag_trigger(self, ctx: Interaction, name: str, phrase: str = None):
        if not ctx.permissions.manage_guild:
            return
        tag = await db.db_get_tag(ctx.guild_id, name)
        if not tag:
            await ctx.response.send_message(f"No tag called `{name.lower()}` exists.", ephemeral=True)
            return
        if phrase and len(phrase) > 64:
            await ctx.response.send_message("Keep trigger phrases to 64 characters or fewer.", ephemeral=True)
            return
        if not await db.db_set_tag_trigger(ctx.guild_id, name, phrase):
            await ctx.response.send_message("Another tag already uses that exact trigger phrase.", ephemeral=True)
            return
        _invalidate_tag_cache(ctx.guild_id)
        await ctx.response.send_message(
            f"`{name.lower()}` now fires whenever someone sends exactly `{phrase}`." if phrase
            else f"Removed the trigger from `{name.lower()}`.", ephemeral=True)

    @tag_group.command(name="config", description="Restrict tag creation to server managers only")
    @default_permissions(manage_guild=True)
    @guild_only()
    async def tag_config(self, ctx: Interaction, restrict_creation: bool):
        if not ctx.permissions.manage_guild:
            return
        await db.db_set_tag_restrict(ctx.guild_id, restrict_creation)
        await ctx.response.send_message(
            f"Tag creation is now **{'restricted to server managers' if restrict_creation else 'open to everyone'}**.",
            ephemeral=True)

    # Per the architectural note in HANDOFF_A2.md: this used to be awaited
    # inline, before the react-command/proxy dispatch, in one big shared
    # on_message. Now it's this cog's own independent listener -- safe
    # since it only reads message content and never touches the message
    # object again afterward.
    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if message.author.id == self.bot.user.id or message.guild is None:
            return
        await _handle_tag_trigger(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(TagsCog(bot))

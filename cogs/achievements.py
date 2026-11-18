"""Achievement system: a small condition-tree engine (AND/OR/XOR/XAND/NOT
gates over leaf conditions like messages-sent, level, role, custom
metrics...), the grant/announce pipeline, automated-achievement checking
hooked off messages/reactions/role changes, and the full `/achievement`
command group plus the top-level `/achievements` viewer.

`grant_achievement`, `check_automated_achievements`, and
`ACHIEVEMENT_PRESETS` are public -- other cogs (levelling, Pokémon) call
into the first two when something they track should be able to unlock an
achievement, and contribute their own `/achievement bulkimport` presets to
the registry instead of this file needing to know about their internals.
"""
from __future__ import annotations
import asyncio
import json
import re
from datetime import datetime
from io import BytesIO
from typing import Callable, Literal, Union

from discord import (
    Attachment, Embed, File, Guild, Interaction, Member, Message, Role,
    User, AllowedMentions, app_commands,
)
from discord.abc import GuildChannel, Messageable
from discord.app_commands import Group, allowed_contexts, default_permissions, describe, guild_only
from discord.ext import commands
from discord.utils import MISSING

import core.db as db
from config import OWNER_ID, PERMS
from core.discord_utils import GUILD, GUILD_INSTALL, VALID_SLASH_COMMAND_NAME, get_highest_role
from core.emoji import custom_hash
from core.imaging import generate_generic_leaderboard_card
from core.mentions import cmd_mention
from core.permissions import is_owner
from core.rarity import RARITY_COLORS, RARITY_EMOJI
from core.views import SimplePageView

_VALID_GATES = {"AND", "OR", "XOR", "XAND", "NOT"}
_RUNTIME_TYPES = {"message", "command"}
_STATIC_TYPES = {"messages", "reactions", "voice_minutes", "role", "level", "xp", "custom", "gate", "achievement"}
_BUILTIN_METRICS = {"messages_sent", "reactions_added", "voice_minutes"}

_MATCH_DESC = {
    "contains": "contains",
    "exact": "exactly matches",
    "starts_with": "starts with",
    "ends_with": "ends with",
    "regex": "matches the regex",
}

# Built-in `/achievement bulkimport` presets. Other tracks (e.g. the
# Pokémon cogs) add their own entries here at import time -- e.g.
# `achievements.ACHIEVEMENT_PRESETS["Pokémon Pack"] = [...]` -- rather than
# this module hardcoding Pokémon-specific icons/metrics. "Role-Based Pack"
# has no achievement list; it's special-cased in the command itself.
ACHIEVEMENT_PRESETS: dict[str, list[dict]] = {
    "Engagement Pack": [
        {"achievement_id": "casual_typer", "name": "Casual Typer", "icon": "📝", "points": 10, "rarity": "COMMON", "type": "automated", "trigger": {"type": "messages", "count": 100}},
        {"achievement_id": "yapper_aspirant", "name": "Yapper Aspirant", "icon": "⌨️", "points": 25, "rarity": "RARE", "type": "automated", "trigger": {"type": "messages", "count": 1000}},
        {"achievement_id": "seasoned_chatter", "name": "Seasoned Chatter", "icon": "💎", "points": 50, "rarity": "EPIC", "type": "automated", "trigger": {"type": "messages", "count": 5000}},
        {"achievement_id": "spam_sovereign", "name": "Spam Sovereign", "icon": "👑", "points": 100, "rarity": "LEGENDARY", "type": "automated", "trigger": {"type": "messages", "count": 10000}},
        {"achievement_id": "professional_yapper", "name": "Professional Yapper", "icon": "🗣️", "points": 200, "rarity": "MYTHIC", "type": "automated", "trigger": {"type": "messages", "count": 100000}},
        {"achievement_id": "give_or_take", "name": "Give or Take", "icon": "👍", "points": 10, "rarity": "COMMON", "type": "automated", "trigger": {"type": "reactions", "count": 10}},
        {"achievement_id": "true_star", "name": "True Star", "icon": "⭐", "points": 25, "rarity": "UNCOMMON", "type": "automated", "trigger": {"type": "reactions", "count": 100}},
        {"achievement_id": "mmm_im_lovin_it", "name": "Mmm I'm Lovin' It", "icon": "🍔", "points": 50, "rarity": "EPIC", "type": "automated", "trigger": {"type": "reactions", "count": 500}},
        {"achievement_id": "this_girl_is_on_fire", "name": "This Girl Is On Fire", "icon": "🔥", "points": 100, "rarity": "LEGENDARY", "type": "automated", "trigger": {"type": "reactions", "count": 1000}},
    ],
    "Voice Activity Pack": [
        {"achievement_id": "still_shy", "name": "Still Shy", "icon": "🤫", "points": 5, "rarity": "COMMON", "type": "automated", "trigger": {"type": "voice_minutes", "minutes": 10}},
        {"achievement_id": "chatty", "name": "Chatty", "icon": "🎶", "points": 15, "rarity": "UNCOMMON", "type": "automated", "trigger": {"type": "voice_minutes", "minutes": 60}},
        {"achievement_id": "talk_the_talk_walk_the_walk", "name": "Talk the Talk, Walk the Walk", "icon": "🎧", "points": 25, "rarity": "RARE", "type": "automated", "trigger": {"type": "voice_minutes", "minutes": 180}},
        {"achievement_id": "all_nighter", "name": "All Nighter", "icon": "🌙", "points": 50, "rarity": "EPIC", "type": "automated", "trigger": {"type": "voice_minutes", "minutes": 720}},
        {"achievement_id": "who_needs_sleep", "name": "Who Needs Sleep?", "icon": "☕", "points": 100, "rarity": "LEGENDARY", "type": "automated", "trigger": {"type": "voice_minutes", "minutes": 1440}},
    ],
    "Levelling Pack": [
        {"achievement_id": "getting_started", "name": "Getting Started", "icon": "🔰", "points": 10, "rarity": "COMMON", "type": "automated", "trigger": {"type": "level", "level": 5}},
        {"achievement_id": "quarter_century", "name": "Quarter Century", "icon": "🔥", "points": 30, "rarity": "UNCOMMON", "type": "automated", "trigger": {"type": "level", "level": 25}},
        {"achievement_id": "halfway_to_legend", "name": "Halfway to Legend", "icon": "⭐", "points": 60, "rarity": "EPIC", "type": "automated", "trigger": {"type": "level", "level": 50}},
        {"achievement_id": "centurion", "name": "Centurion", "icon": "💯", "points": 150, "rarity": "LEGENDARY", "type": "automated", "trigger": {"type": "level", "level": 100}},
    ],
}


def _display_metric_name(metric: str) -> str:
    """Humanizes a progress-metric slug (`pokemon_caught` -> `Pokemon
    Caught`) for use in auto-generated condition descriptions."""
    return metric.replace("_", " ").title()


# region condition-tree engine
def _has_runtime_trigger(cond: dict) -> bool:
    t = cond.get("type")
    if t in _RUNTIME_TYPES:
        return True
    if t == "gate":
        return any(_has_runtime_trigger(c) for c in cond.get("conditions", []))
    return False


async def evaluate_condition_tree(cond: dict, guild_id: int, user_id: int, context: dict = None) -> bool:
    if context is None:
        context = {}

    t = cond.get("type", "")
    if t == "gate":
        gate = cond.get("gate", "AND").upper()
        children = cond.get("conditions", [])
        results = [await evaluate_condition_tree(c, guild_id, user_id, context) for c in children]
        if not results:
            return False

        if gate == "AND":
            return all(results)
        if gate == "OR":
            return any(results)
        if gate == "XOR":
            return results.count(True) == 1
        if gate == "XAND":
            return len(set(results)) == 1
        if gate == "NOT":
            return not results[0]

        return False

    prog = await db.db_get_all_progress(guild_id, user_id)
    if t == "messages":
        return prog.get("messages_sent", 0) >= cond.get("count", 0)
    if t == "reactions":
        return prog.get("reactions_added", 0) >= cond.get("count", 0)
    if t == "voice_minutes":
        return prog.get("voice_minutes", 0) >= cond.get("minutes", 0)
    if t == "role":
        return any(r.id == cond.get("role_id") for r in (context.get("roles") or []))
    if t == "level":
        ud = await db.db_get_level_user(guild_id, user_id)
        return ud.get("level", 0) >= cond.get("level", 0)
    if t == "xp":
        ud = await db.db_get_level_user(guild_id, user_id)
        return ud.get("total_xp", 0) >= cond.get("xp", 0)
    if t == "custom":
        return prog.get(cond.get("metric", ""), 0) >= cond.get("count", 0)
    if t == "achievement":
        return await db.db_has_achievement(guild_id, user_id, cond.get("achievement_id", ""))

    return False


def validate_condition_tree(cond: dict) -> tuple[bool, str]:
    valid_types = _STATIC_TYPES | _RUNTIME_TYPES
    t = cond.get("type")
    if t not in valid_types:
        return False, f"Unknown type: `{t}`"
    if t == "gate":
        gate = cond.get("gate", "").upper()
        if gate not in _VALID_GATES:
            return False, f"Unknown gate: `{gate}`"
        children = cond.get("conditions", [])

        if not children:
            return False, "Gate needs at least one child."
        if gate == "NOT" and len(children) != 1:
            return False, "NOT gate needs exactly one child."

        for child in children:
            ok, err = validate_condition_tree(child)
            if not ok:
                return False, err

    if t == "role" and "role_id" not in cond:
        return False, "type `role` needs `role_id` field."

    if t == "level" and "level" not in cond:
        return False, "type `level` needs `level` field."

    if t == "xp" and "xp" not in cond:
        return False, "type `xp` needs `xp` field."

    if t == "custom" and "metric" not in cond:
        return False, "`custom` needs `metric` field."

    if t == "achievement" and "achievement_id" not in cond:
        return False, "type `achievement` needs `achievement_id` field."

    return True, ""


def _fmt_condition_tree(cond: dict, depth: int = 0) -> str:
    indent = "  " * depth
    t = cond.get("type", "?")
    if t == "gate":
        gate = cond.get("gate", "AND")
        children = cond.get("conditions", [])
        lines = [f"{indent}**{gate}** ("] + [_fmt_condition_tree(c, depth + 1) for c in children] + [f"{indent})"]
        return "\n".join(lines)
    return indent + get_condition_description(cond)


def get_condition_description(trigger: dict) -> str:
    if trigger is None:
        return "Currently unachievable"

    t = trigger.get("type")
    if t == "gate":
        gate = trigger.get("gate", "AND")
        children = trigger.get("conditions", [])
        descs = [get_condition_description(c) for c in children]
        if gate == "NOT":
            return f"NOT ({descs[0]})" if descs else "NOT (nothing)"
        sep = {"AND": " **AND** ", "OR": " **OR** ", "XOR": " **XOR** ", "XAND": " **XAND** "}.get(gate, " **AND** ")
        return "(" + sep.join(descs) + ")"
    if t == "messages":
        return f"*Send {trigger.get('count', 0):,} messages*"
    if t == "reactions":
        return f"*Add reactions to {trigger.get('count', 0):,} messages*"
    if t == "voice_minutes":
        return f"*Spend {trigger.get('minutes', 0):,} minutes in voice channels*"
    if t == "role":
        return f"*Receive the <@&{trigger.get('role_id', 0)}> role*"
    if t == "level":
        return f"*Reach level **{trigger.get('level', 0):,}***"
    if t == "xp":
        return f"*Earn {trigger.get('xp', 0):,} total XP*"
    if t == "custom":
        return f"*Reach {trigger.get('count', 0):,} {_display_metric_name(trigger.get('metric', '?'))}*"
    if t == "message":
        ret = f"*Granted when <@{trigger.get('user_id', 0)}> mentions you with \"{trigger.get('content_match', '')}\" ({trigger.get('match_type', 'contains')})"
        if trigger.get("count"):
            ret += f" {trigger['count']} times"
        return ret + "*"
    if t == "command":
        ret = f"*Granted when you use <@{trigger.get('user_id', 0)}>'s `{trigger.get('command', 'UNKNOWN')}` command"
        if trigger.get("count"):
            ret += f" {trigger['count']} times"
        if trigger.get("content_match"):
            ret += f" and the output {_MATCH_DESC.get(trigger.get('match_type', 'contains'), '')} \"{trigger['content_match']}\""
        return ret + "*"
    if t == "achievement":
        return f"*Has earned the **{trigger.get('achievement_id', '?')}** achievement*"
    return "*(unknown condition)*"


def _leaf_progress(cond: dict, achievement_id: str, progress: dict, level_data: dict, roles: list, user_achievements: dict) -> tuple[float, float] | None:
    """Return (current, required) for a single leaf condition, or None if it isn't a measurable quantity."""
    t = cond.get("type")
    if t == "messages":
        return progress.get("messages_sent", 0), cond.get("count", 0)
    if t == "reactions":
        return progress.get("reactions_added", 0), cond.get("count", 0)
    if t == "voice_minutes":
        return progress.get("voice_minutes", 0), cond.get("minutes", 0)
    if t == "custom":
        return progress.get(cond.get("metric", ""), 0), cond.get("count", 0)
    if t == "level":
        return level_data.get("level", 0), cond.get("level", 0)
    if t == "xp":
        return level_data.get("total_xp", 0), cond.get("xp", 0)
    if t == "role":
        has_role = any(r.id == cond.get("role_id") for r in roles)
        return (1, 1) if has_role else (0, 1)
    if t in ("message", "command") and cond.get("count") is not None:
        return progress.get("a_" + achievement_id, 0), cond["count"]
    if t == "achievement":
        has = cond.get("achievement_id") in (user_achievements or {})
        return (1, 1) if has else (0, 1)
    return None


def _condition_progress_percent(cond: dict, achievement_id: str, progress: dict, level_data: dict, roles: list, user_achievements: dict) -> float | None:
    """Recursively compute overall completion % for a condition tree, or None if it can't be shown as a bar."""
    if cond.get("type") == "gate":
        gate = cond.get("gate", "AND").upper()
        percents = [
            _condition_progress_percent(c, achievement_id, progress, level_data, roles, user_achievements)
            for c in cond.get("conditions", [])
        ]
        percents = [p for p in percents if p is not None]
        if not percents:
            return None
        if gate in ("AND", "XAND"):
            return min(percents)
        if gate in ("OR", "XOR"):
            return max(percents)
        return None

    leaf = _leaf_progress(cond, achievement_id, progress, level_data, roles, user_achievements)
    if leaf is None:
        return None
    current, required = leaf
    if required <= 0:
        return None
    return min(100.0, (current / required) * 100)
# endregion


# region grant / announce / automated-check pipeline
async def _sync_achievement_roles(guild: Guild, user_id: int, guild_id: int, extra_reward_role_ids: set[int] | None = None):
    member = guild.get_member(user_id)
    if not member:
        return

    all_defs = await db.db_get_achievement_defs(guild_id)
    reward_role_ids: set[int] = set(extra_reward_role_ids or ())
    for d in all_defs:
        reward_role_ids.update(d.get("role_rewards") or [])
    if not reward_role_ids:
        return

    earned = await db.db_get_user_achievements(guild_id, user_id)
    desired: set[int] = set()
    for aid in earned:
        d = await db.db_get_achievement_def(guild_id, aid)
        if d:
            desired.update(d.get("role_rewards") or [])

    current_ids = {r.id for r in member.roles}
    to_add = [r for rid in (desired - current_ids) if (r := guild.get_role(rid))]
    to_remove = [r for rid in ((reward_role_ids & current_ids) - desired) if (r := guild.get_role(rid))]

    try:
        if to_add:
            await member.add_roles(*to_add, reason="Achievement role reward")
        if to_remove:
            await member.remove_roles(*to_remove, reason="Achievement role reward")
    except Exception:
        pass


async def grant_achievement(bot: commands.Bot, guild_id: int, user_id: int, ach_def: dict,
                             announce_channel: Messageable = None, default_channel: Messageable = None,
                             send_function: Callable = None) -> bool:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    if not await db.db_grant_achievement(guild_id, user_id, ach_def["achievement_id"], timestamp):
        return False

    secret = ach_def.get("secret")
    if announce_channel or secret:
        asyncio.create_task(announce_achievement(bot, announce_channel, guild_id, user_id, ach_def, default_channel, send_function))

    if "xp_reward" in ach_def:
        guild = bot.get_guild(guild_id)
        asyncio.create_task(_grant_achievement_xp(bot, guild_id, user_id, None if secret else (announce_channel or default_channel),
                                                   guild.get_member(user_id) if guild else None, ach_def["xp_reward"]))

    if ach_def.get("role_rewards"):
        guild = bot.get_guild(guild_id)
        if guild:
            asyncio.create_task(_sync_achievement_roles(guild, user_id, guild_id))

    asyncio.create_task(check_automated_achievements(bot, guild_id, user_id, announce_channel or default_channel))
    return True


async def _grant_achievement_xp(bot: commands.Bot, guild_id: int, user_id: int, channel: Union[Messageable, None],
                                 member: Union[Member, None], xp_reward: Union[float, int]):
    if not member or not member.bot:
        from cogs.levelling import award_xp
        await award_xp(guild_id, user_id, float(xp_reward), channel, member, "achievement")


async def announce_achievement(bot: commands.Bot, channel: Messageable, guild_id: int, user_id: int, ach_def: dict,
                                default_channel: Messageable, send_function: Callable = None):
    guild = bot.get_guild(guild_id)
    if not guild:
        return

    member = guild.get_member(user_id)
    if not member:
        member = bot.get_user(user_id)
    if not member:
        return

    desc = ach_def.get("description")
    if desc is None and ach_def.get("type") == "automated":
        desc = get_condition_description(ach_def.get("trigger"))

    rarity = ach_def.get("rarity", "COMMON")
    secret = ach_def.get("secret")
    embed = Embed(title="Secret Achievement Unlocked!" if secret else "Achievement Unlocked!",
                  description=f"{member.mention} earned **{ach_def['icon']} {ach_def['name']}**",
                  color=RARITY_COLORS.get(rarity, 0x5865F2))
    if desc:
        embed.add_field(name="Description", value=desc, inline=False)

    embed.add_field(name="Rarity", value=f"{RARITY_EMOJI.get(rarity, '⚪')} {rarity}", inline=True)
    embed.add_field(name="Points", value=f"+{ach_def['points']:,} points", inline=True)
    if "xp_reward" in ach_def and ach_def["xp_reward"] and (await db.db_get_levelling_config(guild_id))["enabled"]:
        embed.add_field(name="XP", value=f"+{ach_def['xp_reward']:,}", inline=True)
    role_rewards = ach_def.get("role_rewards") or []
    if role_rewards:
        embed.add_field(
            name="Role Reward" + ("s" if len(role_rewards) > 1 else ""),
            value=" ".join(f"<@&{r}>" for r in role_rewards), inline=False)
    embed.set_footer(text=f"Total Points: {await calculate_total_points(guild_id, user_id):,}")

    if secret:
        try:
            await member.send(f"{member.mention} (`{guild.name}`)", embed=embed)
        except Exception:
            pass
        return

    try:
        if send_function:
            await send_function(member.mention, embed=embed)
        else:
            await channel.send(member.mention, embed=embed)
    except Exception:
        try:
            await default_channel.send(member.mention, embed=embed)
        except Exception:
            try:
                await member.send(member.mention, embed=embed)
            except Exception:
                pass


async def check_automated_achievements(bot: commands.Bot, guild_id: int, user_id: int, channel: Messageable = None,
                                        roles: list[Role] = None, context: dict = None):
    ach_defs = await db.db_get_achievement_defs(guild_id)
    if not ach_defs:
        return

    if context is None:
        context = {}
    if roles is not None:
        context["roles"] = roles
    if channel is not None:
        context["channel"] = channel
    for ach_def in ach_defs:
        if ach_def.get("type") != "automated":
            continue
        if await db.db_has_achievement(guild_id, user_id, ach_def["achievement_id"]):
            continue
        trigger = ach_def.get("trigger")
        if not trigger or _has_runtime_trigger(trigger):
            continue
        if await evaluate_condition_tree(trigger, guild_id, user_id, context):
            default_ch = await get_default_channel(bot, guild_id)
            await grant_achievement(bot, guild_id, user_id, ach_def, channel or default_ch, default_ch)


async def get_default_channel(bot: commands.Bot, guild_id: int):
    cid = await db.db_get_achievement_channel(guild_id)
    if not cid:
        return None
    guild = bot.get_guild(guild_id)
    return guild.get_channel_or_thread(cid) if guild else None


async def check_message_achievements(bot: commands.Bot, message: Message):
    if not message.guild:
        return

    guild_id = message.guild.id
    ach_defs = await db.db_get_achievement_defs(guild_id)
    if not ach_defs:
        return

    for ach_def in ach_defs:
        if ach_def.get("type") != "automated":
            continue

        trigger = ach_def.get("trigger", {})
        if trigger.get("type") not in ("message", "command") or trigger.get("type") != "command" and message.author.id == bot.user.id:
            continue
        if trigger.get("type") == "command" and (message._interaction is None or
           (trigger.get("command") is not None and message._interaction.name != trigger["command"])):
            continue
        if message.author.id != trigger.get("user_id"):
            continue

        content_match = trigger.get("content_match", "")
        match_type = trigger.get("match_type", "contains")
        mc_lower = message.content.lower()
        cm_lower = content_match.lower()
        matches = (cm_lower in mc_lower if match_type == "contains" else
                   mc_lower.strip() == cm_lower.strip() if match_type == "exact" else
                   mc_lower.startswith(cm_lower) if match_type == "starts_with" else
                   mc_lower.endswith(cm_lower) if match_type == "ends_with" else
                   bool(re.search(content_match, message.content)) if match_type == "regex" else False)
        if not matches:
            continue

        default_channel = await get_default_channel(bot, guild_id)
        ach_id = ach_def["achievement_id"]

        if trigger.get("type") == "message":
            for mentioned_user in message.mentions:
                if trigger.get("count") is not None:
                    await db.db_update_progress(guild_id, mentioned_user.id, "a_" + ach_id, 1)
                    if await db.db_get_progress(guild_id, mentioned_user.id, "a_" + ach_id) < trigger["count"]:
                        continue
                if not await db.db_has_achievement(guild_id, mentioned_user.id, ach_id):
                    await grant_achievement(bot, guild_id, mentioned_user.id, ach_def, message.channel, default_channel)
        else:
            user = message.interaction_metadata.user.id
            if trigger.get("count") is not None:
                await db.db_update_progress(guild_id, user, "a_" + ach_id, 1)
                if await db.db_get_progress(guild_id, user, "a_" + ach_id) < trigger["count"]:
                    continue
            if not await db.db_has_achievement(guild_id, user, ach_id):
                await grant_achievement(bot, guild_id, user, ach_def, message.channel, default_channel)


async def calculate_total_points(guild_id: int, user_id: int) -> int:
    achievements = await db.db_get_user_achievements(guild_id, user_id)
    total = 0
    for aid in achievements:
        d = await db.db_get_achievement_def(guild_id, aid)
        if d:
            total += d["points"]
    return total


async def calculate_total_points_from_achievements(achievements: list[str], guild_id: int) -> int:
    total = 0
    for aid in achievements:
        d = await db.db_get_achievement_def(guild_id, aid)
        if d:
            total += d["points"]
    return total


async def scan_guild_achievements(bot: commands.Bot, guild_id: int, *, announce=True, dry_run=False, on_progress=None, concurrency=25):
    guild = bot.get_guild(guild_id)
    if not guild:
        return {"error": "Guild not found", "checked": 0, "granted": 0, "grants": {}, "dry_run": dry_run}
    if not guild.chunked:
        await guild.chunk(cache=True)

    ach_defs = await db.db_get_achievement_defs(guild_id)
    checkable = [d for d in ach_defs if d.get("type") == "automated" and d.get("trigger")
                 and not _has_runtime_trigger(d["trigger"])]
    if not checkable:
        return {"checked": 0, "granted": 0, "grants": {}, "dry_run": dry_run}

    default_channel = await get_default_channel(bot, guild_id)
    members = [m for m in guild.members if not m.bot]
    checked = granted = 0
    grants: dict[int, list[str]] = {}
    lock, sem = asyncio.Lock(), asyncio.Semaphore(concurrency)

    async def _check_member(member):
        nonlocal checked, granted
        context = {"roles": member.roles}
        local: list[str] = []
        async with sem:
            for ach_def in checkable:
                try:
                    if await db.db_has_achievement(guild_id, member.id, ach_def["achievement_id"]):
                        continue
                    if not await evaluate_condition_tree(ach_def["trigger"], guild_id, member.id, context):
                        continue
                    if dry_run:
                        local.append(ach_def["name"])
                    elif await grant_achievement(bot, guild_id, member.id, ach_def,
                                                  default_channel if announce else None, default_channel):
                        local.append(ach_def["name"])
                except Exception:
                    pass
        async with lock:
            checked += 1
            granted += len(local)
            if local:
                grants[member.id] = local
            if on_progress:
                await on_progress(checked, len(members))

    await asyncio.gather(*(_check_member(m) for m in members))
    return {"checked": checked, "granted": granted, "grants": grants, "dry_run": dry_run}
# endregion


class AchievementsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    achievement_group = Group(name="achievement", description="Achievement commands", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL)

    # region management: create / edit / delete / automation
    @achievement_group.command(name="create", description="Create a new achievement")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(
        achievement_id="Unique ID for the achievement (no spaces)", name="Display name of the achievement",
        description="Description of the achievement", type="Type of achievement (manual or automated)",
        icon="Emoji icon for the achievement", points="Points awarded for this achievement",
        rarity="Rarity level of the achievement", secret="Hide this achievement until earned",
    )
    async def achievement_create(self, ctx: Interaction, achievement_id: str, name: str, description: str = None,
                                  type: Literal["Manual", "Automated"] = "Manual", icon: str = "🏆", points: int = None,
                                  rarity: Literal["COMMON", "UNCOMMON", "RARE", "EPIC", "LEGENDARY", "MYTHIC"] = "COMMON",
                                  secret: bool = False):
        if not ctx.permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission to create achievements.", ephemeral=True)
            return

        if " " in achievement_id:
            await ctx.response.send_message("Achievement ID cannot contain spaces.", ephemeral=True)
            return

        if await db.db_get_achievement_def(ctx.guild_id, achievement_id):
            await ctx.response.send_message(f"An achievement with ID `{achievement_id}` already exists.", ephemeral=True)
            return

        if points is None:
            points = {"COMMON": 10, "UNCOMMON": 15, "RARE": 25, "EPIC": 50, "LEGENDARY": 100, "MYTHIC": 200}.get(rarity, 10)

        achievement = {
            "achievement_id": achievement_id, "name": name, "description": description, "icon": icon,
            "points": points, "rarity": rarity, "type": type.lower(), "secret": secret,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
        }

        await db.db_add_achievement_def(ctx.guild_id, achievement)

        desc = description
        if desc is None and type == "automated":
            desc = "Currently unachievable"

        embed = Embed(title="Achievement Created!", color=RARITY_COLORS[rarity])
        embed.add_field(name="ID", value=f"`{achievement_id}`", inline=False) \
            .add_field(name="Name", value=f"{icon} {name}", inline=True) \
            .add_field(name="Type", value=type, inline=True) \
            .add_field(name="Rarity", value=f"{RARITY_EMOJI[rarity]} {rarity}", inline=True) \
            .add_field(name="Points", value=str(points), inline=True) \
            .add_field(name="Secret", value="Yes!" if secret else "No", inline=True)
        if desc is not None:
            embed.add_field(name="Description", value=desc, inline=False)

        await ctx.response.send_message(embed=embed)

    @achievement_group.command(name="reorder", description="Change where an achievement appears in achievement lists")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(achievement_id="The achievement to move", position="New 1-based position")
    async def achievement_reorder(self, ctx: Interaction, achievement_id: str, position: int):
        if not ctx.permissions.manage_guild:
            return
        all_defs = sorted(await db.db_get_achievement_defs(ctx.guild_id), key=lambda d: d.get("sort_order", 10**9))
        idx = next((i for i, d in enumerate(all_defs) if d["achievement_id"] == achievement_id), None)
        if idx is None:
            await ctx.response.send_message(f"Achievement `{achievement_id}` not found.", ephemeral=True)
            return
        moved = all_defs.pop(idx)
        new_idx = max(0, min(len(all_defs), position - 1))
        all_defs.insert(new_idx, moved)
        for i, d in enumerate(all_defs):
            await db.db_update_achievement_def(ctx.guild_id, d["achievement_id"], sort_order=i)
        await ctx.response.send_message(f"Moved **{moved['name']}** to position {new_idx + 1}.", ephemeral=True)

    @achievement_group.command(name="setautomation", description="Set automation trigger for an achievement")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(
        achievement_id="The ID of the achievement", trigger_type="What triggers this achievement",
        count="Number required (for messages/reactions)", minutes="Minutes required (for voice)",
        role="Role required (for role-based achievements)", user="User that sends the trigger message (required for on-message achievements)",
        content_match="Text content to match in user message", match_type="How to match the content (defaults to contains)",
        command_name="The name of the command that a user has to use",
    )
    async def achievement_setautomation(
        self, ctx: Interaction, achievement_id: str,
        trigger_type: Literal["messages", "reactions", "voice_minutes", "role", "message", "command", "level", "xp"],
        count: int = None, minutes: int = None, role: Role = None, user: User = None,
        content_match: str = None, match_type: Literal["contains", "exact", "starts_with", "ends_with", "regex"] = "contains",
        command_name: str = None,
    ):
        if not ctx.permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission.", ephemeral=True)
            return

        ach_def = await db.db_get_achievement_def(ctx.guild_id, achievement_id)
        if not ach_def:
            await ctx.response.send_message(f"Achievement `{achievement_id}` not found.", ephemeral=True)
            return

        if ach_def.get("type") != "automated":
            await ctx.response.send_message("This achievement is not automated. Set type to '`automated`' first.", ephemeral=True)
            return

        trigger = {"type": trigger_type}
        trigger_desc = ""

        if trigger_type == "messages":
            if not count:
                await ctx.response.send_message("You must specify a count for `messages` triggers.", ephemeral=True)
                return
            trigger["count"] = count
            trigger_desc = f"Send {count:,} messages"

        elif trigger_type == "reactions":
            if not count:
                await ctx.response.send_message("You must specify a count for `reactions` triggers.", ephemeral=True)
                return
            trigger["count"] = count
            trigger_desc = f"Add reactions to {count:,} messages"

        elif trigger_type == "voice_minutes":
            if not minutes:
                await ctx.response.send_message("You must specify an amount of minutes for `voice_minutes` triggers.", ephemeral=True)
                return
            trigger["minutes"] = minutes
            trigger_desc = f"Spend {minutes:,} minutes in voice channels"

        elif trigger_type == "role":
            if not role:
                await ctx.response.send_message("You must specify a role for `role` triggers.", ephemeral=True)
                return
            trigger["role_id"] = role.id
            trigger["role_name"] = role.name
            trigger_desc = f"Receive the <@&{role.id}> role"

        elif trigger_type == "message":
            if not user:
                await ctx.response.send_message("You must specify a user for `message` triggers.", ephemeral=True)
                return
            if not content_match:
                await ctx.response.send_message("You must specify content to match for `message` triggers.", ephemeral=True)
                return

            trigger["user_id"] = user.id
            trigger["user_name"] = user.global_name or user.name
            trigger["content_match"] = content_match
            trigger["match_type"] = match_type
            if count:
                trigger["count"] = count

            trigger_desc = f"When {user.mention} sends a message that {_MATCH_DESC[match_type]} \"{content_match}\" and mentions a user"
            if count:
                trigger_desc += f" {count} times"

        elif trigger_type == "command":
            if not user or not user.bot:
                await ctx.response.send_message("You must specify a bot user for `command` triggers.", ephemeral=True)
                return
            if not command_name and not content_match:
                await ctx.response.send_message("You must specify either a command name or content to match for `command` triggers.", ephemeral=True)
                return

            command_name = command_name.lower().strip()
            if command_name.startswith("/"):
                command_name = command_name[1:]
            match = VALID_SLASH_COMMAND_NAME.match(command_name)
            if match is None:
                await ctx.response.send_message("Invalid command name. All characters within a command name must be alphanumerical or one of: `-`, `_`, ` `.")
                return

            trigger["user_id"] = user.id
            trigger["user_name"] = user.global_name or user.name
            if command_name:
                trigger["command"] = command_name
            if content_match:
                trigger["content_match"] = content_match
                trigger["match_type"] = match_type
            if count:
                trigger["count"] = count

            trigger_desc = f"When a user uses {user.mention}'s command `{command_name}`"
            if count:
                trigger_desc += f" {count} times"
            if content_match:
                trigger_desc += f" and the command's output message {_MATCH_DESC[match_type]} \"{content_match}\""

        elif trigger_type == "level":
            if not count:
                await ctx.response.send_message("You must specify a count for `level` triggers.", ephemeral=True)
                return
            trigger["level"] = count
            trigger_desc = f"Reach level **{count:,}**"

        elif trigger_type == "xp":
            if not count:
                await ctx.response.send_message("You must specify a count for `xp` triggers.", ephemeral=True)
                return
            trigger["xp"] = count
            trigger_desc = f" Earn {count:,} total XP"

        else:
            await ctx.response.send_message("Missing required parameter for this trigger type.", ephemeral=True)
            return

        await db.db_update_achievement_def(ctx.guild_id, achievement_id, trigger=trigger)
        ach_def["trigger"] = trigger

        embed = Embed(title="Automation Set Successfully", description=f"**{ach_def['icon']} {ach_def['name']}**", color=0x2ECC71)
        embed.add_field(name="Trigger", value=trigger_desc, inline=False)

        await ctx.response.send_message(embed=embed)

    @achievement_group.command(name="setcondition", description="Set a full condition tree (supports AND/OR/XOR/XAND/NOT gates) via JSON")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(
        achievement_id="The achievement ID (must be type = automated)",
        condition_json="JSON condition tree (leave blank if uploading condition_file instead)",
        condition_file="Upload a .json file with the condition tree instead of typing it",
    )
    async def achievement_setcondition(self, ctx: Interaction, achievement_id: str, condition_json: str = None, condition_file: Attachment = None):
        if not ctx.permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission to grant achievements.")
            return

        ach_def = await db.db_get_achievement_def(ctx.guild_id, achievement_id)
        if not ach_def:
            await ctx.response.send_message(f"Achievement `{achievement_id}` not found.", ephemeral=True)
            return
        if ach_def.get("type") != "automated":
            await ctx.response.send_message("This achievement must be `automated`. Change its type first.", ephemeral=True)
            return
        if not condition_json and not condition_file:
            await ctx.response.send_message("Provide either `condition_json` or `condition_file`.", ephemeral=True)
            return

        if condition_file:
            if condition_file.size > 1_000_000:
                await ctx.response.send_message("That file is too large (1 MB max).", ephemeral=True)
                return
            try:
                condition_json = (await condition_file.read()).decode("utf-8")
            except Exception as e:
                await ctx.response.send_message(f"Couldn't read that file: `{e}`", ephemeral=True)
                return

        try:
            condition = json.loads(condition_json)
        except json.JSONDecodeError as e:
            await ctx.response.send_message(f"Invalid JSON: `{e}`", ephemeral=True)
            return

        ok, err = validate_condition_tree(condition)
        if not ok:
            await ctx.response.send_message(f"Invalid condition tree: {err}", ephemeral=True)
            return

        await db.db_update_achievement_def(ctx.guild_id, achievement_id, trigger=condition)
        ach_def["trigger"] = condition
        readable = _fmt_condition_tree(condition)
        embed = Embed(title="Condition Tree Set", description=f"**{ach_def['icon']} {ach_def['name']}**\n\n{readable}", color=0x2ECC71)
        await ctx.response.send_message(embed=embed, ephemeral=True)

    @achievement_group.command(name="viewjson", description="View the condition tree JSON for an achievement")
    @describe(achievement_id="The achievement ID")
    async def achievement_viewjson(self, ctx: Interaction, achievement_id: str):
        ach_def = await db.db_get_achievement_def(ctx.guild_id, achievement_id)
        if not ach_def:
            await ctx.response.send_message(f"Achievement `{achievement_id}` not found.", ephemeral=True)
            return
        trigger = ach_def.get("trigger")
        if not trigger:
            await ctx.response.send_message(f"**{ach_def['name']}** has no condition set.", ephemeral=True)
            return

        readable = _fmt_condition_tree(trigger)
        json_repr = json.dumps(trigger, indent=2)
        embed = Embed(title=f"Condition Tree - {ach_def['name']}", color=RARITY_COLORS.get(ach_def.get("rarity", "COMMON"), 0x5865F2))
        embed.add_field(name="Human-Readable", value=readable if len(readable) <= 1024 else readable[:1021] + "...", inline=False)

        if len(json_repr) <= 1000:
            embed.add_field(name="JSON", value=f"```json\n{json_repr}\n```", inline=False)
            await ctx.response.send_message(embed=embed, ephemeral=True)
        else:
            file = File(BytesIO(json_repr.encode("utf-8")), filename=f"{achievement_id}_condition.json")
            await ctx.response.send_message(embed=embed, file=file, ephemeral=True)

    @achievement_group.command(name="addcondition", description="Append a simple condition to an existing trigger, combining with a gate")
    @default_permissions(manage_guild=True)
    @describe(
        achievement_id="The achievement ID", gate="How to combine with the existing condition",
        trigger_type="The type of the new condition to append", count="Count threshold (for messages / reactions / custom)",
        minutes="Minutes threshold (for voice_minutes)", role="Role (for role condition)",
        level="Level threshold (for level condition)", xp="XP threshold (for xp condition)",
        custom_metric="Custom metric name (for custom condition)",
    )
    async def achievement_addcondition(
        self, ctx: Interaction, achievement_id: str, gate: Literal["AND", "OR", "XOR", "XAND"],
        trigger_type: Literal["messages", "reactions", "voice_minutes", "role", "level", "xp", "custom"],
        count: int = None, minutes: int = None, role: Role = None, level: int = None, xp: int = None, custom_metric: str = None,
    ):
        if not ctx.permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission to grant achievements.")
            return

        ach_def = await db.db_get_achievement_def(ctx.guild_id, achievement_id)
        if not ach_def:
            await ctx.response.send_message(f"Achievement `{achievement_id}` not found.", ephemeral=True)
            return

        if ach_def.get("type") != "automated":
            await ctx.response.send_message("This achievement must be `automated`. Change its type first.", ephemeral=True)
            return

        new_leaf: dict = {"type": trigger_type}
        if trigger_type == "messages":
            if count is None:
                await ctx.response.send_message("Specify `count`.", ephemeral=True)
                return
            new_leaf["count"] = count
        elif trigger_type == "reactions":
            if count is None:
                await ctx.response.send_message("Specify `count`.", ephemeral=True)
                return
            new_leaf["count"] = count
        elif trigger_type == "voice_minutes":
            if minutes is None:
                await ctx.response.send_message("Specify `minutes`.", ephemeral=True)
                return
            new_leaf["minutes"] = minutes
        elif trigger_type == "role":
            if role is None:
                await ctx.response.send_message("Specify `role`.", ephemeral=True)
                return
            new_leaf["role_id"] = role.id
        elif trigger_type == "level":
            if level is None:
                await ctx.response.send_message("Specify `level`.", ephemeral=True)
                return
            new_leaf["level"] = level
        elif trigger_type == "xp":
            if xp is None:
                await ctx.response.send_message("Specify `xp`.", ephemeral=True)
                return
            new_leaf["xp"] = xp
        elif trigger_type == "custom":
            if custom_metric is None or count is None:
                await ctx.response.send_message("Specify `custom_metric` and `count`.", ephemeral=True)
                return
            new_leaf["metric"] = custom_metric
            new_leaf["count"] = count

        existing = ach_def.get("trigger")
        if existing is None:
            new_trigger = new_leaf
        elif existing.get("type") == "gate" and existing.get("gate") == gate:
            new_trigger = {**existing, "conditions": existing["conditions"] + [new_leaf]}
        else:
            new_trigger = {"type": "gate", "gate": gate, "conditions": [existing, new_leaf]}

        await db.db_update_achievement_def(ctx.guild_id, achievement_id, trigger=new_trigger)
        ach_def["trigger"] = new_trigger
        readable = _fmt_condition_tree(ach_def["trigger"])
        embed = Embed(title="Condition Updated", description=f"**{ach_def['icon']} {ach_def['name']}**\n\n{readable}", color=0x2ECC71)
        await ctx.response.send_message(embed=embed, ephemeral=True)

    @achievement_group.command(name="schema", description="Reference for the achievement condition JSON schema")
    async def achievement_schema(self, ctx: Interaction):
        embed = Embed(title="Achievement Condition Schema", color=0x5865F2,
                       description="Used by `/achievement setcondition`. Combine leaves with a `gate` node.")
        embed.add_field(name="Leaf types", value=(
            "`messages` {\"type\":\"messages\",\"count\":N}\n"
            "`reactions` {\"type\":\"reactions\",\"count\":N}\n"
            "`voice_minutes` {\"type\":\"voice_minutes\",\"minutes\":N}\n"
            "`role` {\"type\":\"role\",\"role_id\":ID}\n"
            "`level` {\"type\":\"level\",\"level\":N}\n"
            "`xp` {\"type\":\"xp\",\"xp\":N}\n"
            "`custom` {\"type\":\"custom\",\"metric\":\"name\",\"count\":N}\n"
            "`achievement` {\"type\":\"achievement\",\"achievement_id\":\"name\"}\n"
            "`message` / `command`: see `/achievement setautomation`"), inline=False)
        embed.add_field(name="Gates", value=(
            "{\"type\":\"gate\",\"gate\":\"AND|OR|XOR|XAND|NOT\",\"conditions\":[...]}\n"
            "NOT takes exactly one child."), inline=False)
        embed.set_footer(text="Prefer /achievement addcondition for quick one-off additions without writing JSON.")
        await ctx.response.send_message(embed=embed, ephemeral=True)

    @achievement_group.command(name="condition", description="View the unlock condition for an achievement")
    @describe(achievement_id="The ID of the achievement to inspect", ephemeral="Send the response only to you")
    async def achievement_condition(self, ctx: Interaction, achievement_id: str, ephemeral: bool = False):
        ach_def = await db.db_get_achievement_def(ctx.guild_id, achievement_id)
        if not ach_def:
            await ctx.response.send_message(f"Achievement `{achievement_id}` not found.", ephemeral=True)
            return

        is_admin = ctx.user.guild_permissions.manage_guild
        is_secret = ach_def.get("secret", False)
        user_achs = await db.db_get_user_achievements(ctx.guild_id, ctx.user.id)
        has_it = achievement_id in user_achs
        trigger = ach_def.get("trigger")
        rarity = ach_def.get("rarity", "COMMON")

        embed = Embed(title=f"{ach_def['icon']} {ach_def['name']}", color=RARITY_COLORS.get(rarity, 0x5865F2))
        embed.add_field(name="Rarity", value=f"{RARITY_EMOJI.get(rarity, '⚪')} {rarity}", inline=True)
        embed.add_field(name="Points", value=f"{ach_def['points']:,} pts", inline=True)
        embed.add_field(name="Type", value=ach_def.get("type", "manual").title(), inline=True)

        role_rewards = ach_def.get("role_rewards") or []
        if role_rewards:
            embed.add_field(
                name="Role Reward" + ("s" if len(role_rewards) > 1 else ""),
                value=" ".join(f"<@&{r}>" for r in role_rewards), inline=False)

        if is_secret and not (has_it or is_admin):
            embed.add_field(name="Condition", value="\\*" * 10 + "\n*Earn this achievement to reveal its condition.*", inline=False)
        else:
            desc = ach_def.get("description")
            if desc:
                embed.description = desc

            if not trigger:
                embed.add_field(name="Condition", value="*No condition set. Currently unachievable.*", inline=False)
            elif ach_def.get("type") != "automated":
                embed.add_field(name="Condition", value="*This achievement is granted manually by an admin.*", inline=False)
            else:
                readable = _fmt_condition_tree(trigger)
                value = readable if len(readable) <= 1024 else readable[:1021] + "..."
                embed.add_field(name="Condition", value=value, inline=False)

        embed.set_footer(text="✅ You have this achievement!" if has_it else "❌ Not yet earned")
        await ctx.response.send_message(embed=embed, ephemeral=ephemeral)

    @achievement_group.command(name="xpreward", description="Set how much XP is awarded when this achievement is earned")
    @default_permissions(manage_guild=True)
    @describe(achievement_id="The achievement ID", xp="XP to award (0 = no XP reward)")
    async def achievement_xpreward(self, ctx: Interaction, achievement_id: str, xp: int):
        if not ctx.permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission to modify achievements.")
            return

        ach_def = await db.db_get_achievement_def(ctx.guild_id, achievement_id)
        if not ach_def:
            await ctx.response.send_message(f"Achievement `{achievement_id}` not found.", ephemeral=True)
            return

        if not (await db.db_get_levelling_config(ctx.guild_id))["enabled"]:
            await ctx.response.send_message("Enable levelling before adding an XP reward to an achievement!", ephemeral=True)
            return

        from cogs.levelling import award_xp
        old_xp = ach_def.get("xp_reward", 0)
        await db.db_update_achievement_def(ctx.guild_id, achievement_id, xp_reward=xp)
        ach_def["xp_reward"] = xp
        for user_id in await db.db_get_all_achievement_users(ctx.guild_id):
            if await db.db_has_achievement(ctx.guild_id, user_id, achievement_id):
                await award_xp(ctx.guild_id, user_id, xp - old_xp, ctx.channel, ctx.guild.get_member(user_id), "achievement_edit")

        if xp != 0:
            await ctx.response.send_message(f"Earning **{ach_def['name']}** will now award **{xp:,} XP**.", ephemeral=True)
        else:
            await ctx.response.send_message(f"**{ach_def['name']}** no longer awards XP.", ephemeral=True)

    @achievement_group.command(name="rolereward", description="Add or remove a role granted when this achievement is earned")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(achievement_id="The achievement ID", role="The role to add or remove", action="Add or remove this role from the reward list")
    async def achievement_rolereward(self, ctx: Interaction, achievement_id: str, role: Role, action: Literal["Add", "Remove"] = "Add"):
        if not ctx.permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission to modify achievements.")
            return

        ach_def = await db.db_get_achievement_def(ctx.guild_id, achievement_id)
        if not ach_def:
            await ctx.response.send_message(f"Achievement `{achievement_id}` not found.", ephemeral=True)
            return

        if role.is_default():
            await ctx.response.send_message("You can't use @everyone as an achievement reward.", ephemeral=True)
            return
        if role.managed:
            await ctx.response.send_message("That role is managed by an integration and can't be assigned manually.", ephemeral=True)
            return

        role_rewards = list(ach_def.get("role_rewards") or [])
        if action == "Add":
            if role.id in role_rewards:
                await ctx.response.send_message(f"{role.mention} is already a reward for this achievement.", ephemeral=True, allowed_mentions=AllowedMentions.none())
                return
            bot_top = get_highest_role(ctx.guild.get_member(self.bot.user.id))
            if role.position >= bot_top.position:
                await ctx.response.send_message(
                    f"{role.mention} is positioned above (or equal to) my highest role, so I won't be able to assign it. Move my role above it first.",
                    ephemeral=True, allowed_mentions=AllowedMentions.none())
                return
            role_rewards.append(role.id)
        else:
            if role.id not in role_rewards:
                await ctx.response.send_message(f"{role.mention} isn't currently a reward for this achievement.", ephemeral=True, allowed_mentions=AllowedMentions.none())
                return
            role_rewards.remove(role.id)

        await db.db_update_achievement_def(ctx.guild_id, achievement_id, role_rewards=role_rewards)
        ach_def["role_rewards"] = role_rewards

        if ctx.guild:
            for uid in await db.db_get_all_achievement_users(ctx.guild_id):
                if await db.db_has_achievement(ctx.guild_id, uid, achievement_id):
                    await _sync_achievement_roles(ctx.guild, uid, ctx.guild_id)

        desc = "\n".join(f"• <@&{r}>" for r in role_rewards) if role_rewards else "*No roles configured.*"
        embed = Embed(title="Role Rewards Updated", description=f"**{ach_def['icon']} {ach_def['name']}**\n\n{desc}", color=0x2ECC71)
        await ctx.response.send_message(embed=embed, ephemeral=True, allowed_mentions=AllowedMentions.none())

    @achievement_group.command(name="delete", description="Delete an achievement definition")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(achievement_id="The ID of the achievement to delete", keep_progress="Keep user progress (don't revoke from users)")
    async def achievement_delete(self, ctx: Interaction, achievement_id: str, keep_progress: bool = False):
        if not ctx.permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission to delete achievements.")
            return

        ach_def = await db.db_get_achievement_def(ctx.guild_id, achievement_id)
        if not ach_def:
            await ctx.response.send_message(f"Achievement with ID `{achievement_id}` not found.", ephemeral=True)
            return

        await db.db_delete_achievement_def(ctx.guild_id, achievement_id)

        revoked_count = 0
        if not keep_progress:
            from cogs.levelling import award_xp
            for user_id in await db.db_get_all_achievement_users(ctx.guild_id):
                if await db.db_has_achievement(ctx.guild_id, user_id, achievement_id):
                    await db.db_revoke_achievement(ctx.guild_id, user_id, achievement_id)
                    if "xp_reward" in ach_def and ach_def["xp_reward"] and (await db.db_get_levelling_config(ctx.guild_id))["enabled"]:
                        await award_xp(ctx.guild_id, user_id, -ach_def["xp_reward"], ctx.channel, ctx.guild.get_member(user_id), "revoked_achievement")
                    revoked_count += 1
                await db.db_delete_progress_metric(ctx.guild_id, user_id, "a_" + achievement_id)

        msg = f"Deleted achievement **{ach_def['name']}**."
        if not keep_progress:
            msg += f" Revoked from {revoked_count} user(s)."

        await ctx.response.send_message(msg)

    @achievement_group.command(name="edit", description="Edit an achievement's properties")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(
        achievement_id="The ID of the achievement to edit", name="New name (leave blank to keep current)",
        description="New description (leave blank to keep current)", type="New type (leave blank to keep current)",
        icon="New icon (leave blank to keep current)", points="New point value (leave blank to keep current)",
        rarity="New rarity (leave blank to keep current)", secret="Change secret status (leave blank to keep current)",
    )
    async def achievement_edit(
        self, ctx: Interaction, achievement_id: str, name: str = None, description: str = None,
        type: Literal["Manual", "Automated"] = None, icon: str = None, points: int = None,
        rarity: Literal["COMMON", "UNCOMMON", "RARE", "EPIC", "LEGENDARY", "MYTHIC"] = None, secret: bool = None,
    ):
        if not ctx.permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission to edit achievements.")
            return

        ach_def = await db.db_get_achievement_def(ctx.guild_id, achievement_id)
        if not ach_def:
            await ctx.response.send_message(f"Achievement with ID `{achievement_id}` not found.", ephemeral=True)
            return

        updates = {}
        if name:
            updates["name"] = name
        if description:
            updates["description"] = description
        if type:
            updates["type"] = type.lower()
        if icon:
            updates["icon"] = icon
        if points is not None:
            updates["points"] = points
        if rarity:
            updates["rarity"] = rarity
        if secret is not None:
            updates["secret"] = secret

        if updates:
            await db.db_update_achievement_def(ctx.guild_id, achievement_id, **updates)
            ach_def = await db.db_get_achievement_def(ctx.guild_id, achievement_id)

        desc = ach_def.get("description")
        if desc is None and type == "automated":
            desc = get_condition_description(ach_def.get("trigger"))

        current_rarity = ach_def.get("rarity", "COMMON")
        embed = Embed(title="Achievement Updated", color=RARITY_COLORS[current_rarity]) \
            .add_field(name="ID", value=f"`{achievement_id}`", inline=False) \
            .add_field(name="Name", value=f"{ach_def['icon']} {ach_def['name']}", inline=True) \
            .add_field(name="Type", value=ach_def["type"], inline=True) \
            .add_field(name="Rarity", value=f"{RARITY_EMOJI[current_rarity]} {current_rarity}", inline=True) \
            .add_field(name="Points", value=str(ach_def["points"]), inline=True) \
            .add_field(name="Secret", value="Yes!" if ach_def["secret"] else "No", inline=True)
        if desc is not None:
            embed.add_field(name="Description", value=desc, inline=False)

        await ctx.response.send_message(embed=embed)

    @achievement_group.command(name="bulkimport", description="Import pre-configured achievements")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(preset="Choose a preset achievement pack")
    async def achievement_bulkimport(self, ctx: Interaction,
                                      preset: Literal["Engagement Pack", "Levelling Pack", "Role-Based Pack", "Voice Activity Pack", "Pokémon Pack"]):
        if not ctx.permissions.manage_guild:
            return
        if not ctx.user.guild_permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission to bulk import achievements.", ephemeral=True)
            return

        if preset == "Role-Based Pack":
            await ctx.response.send_message(
                f"Role-based achievements need to be configured manually. Use {cmd_mention('achievement create')} and {cmd_mention('achievement setautomation')} with your specific role IDs.\n\n"
                "Example achievements:\n- Birthday role achievement\n- Special position roles\n- Tier/level roles\n- Custom server roles",
                ephemeral=True)
            return

        achievements_to_add = [dict(a) for a in ACHIEVEMENT_PRESETS.get(preset, [])]

        added_count = 0
        skipped = []

        for ach in achievements_to_add:
            if not await db.db_get_achievement_def(ctx.guild_id, ach["achievement_id"]):
                ach["secret"] = False
                ach["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
                await db.db_add_achievement_def(ctx.guild_id, ach)
                added_count += 1
            else:
                skipped.append(ach["name"])

        msg = f"Imported {added_count} achievements from **{preset}**!"
        if skipped:
            msg += f"\n\nSkipped (already exist): {', '.join(skipped)}"

        await ctx.response.send_message(msg)
    # endregion

    # region grant / revoke / metrics / channel
    @achievement_group.command(name="grant", description="Grant an achievement to a user")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(user="The user to grant the achievement to", achievement_id="The ID of the achievement to grant", announce="Announce the achievement in the channel")
    async def achievement_grant(self, ctx: Interaction, user: User, achievement_id: str, announce: bool = True):
        if not ctx.permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission to grant achievements.")
            return

        ach_def = await db.db_get_achievement_def(ctx.guild_id, achievement_id)
        if not ach_def:
            await ctx.response.send_message(f"Achievement with ID `{achievement_id}` not found.", ephemeral=True)
            return

        if await db.db_has_achievement(ctx.guild_id, user.id, achievement_id):
            await ctx.response.send_message(f"{user.mention} already has this achievement.", ephemeral=True)
            return

        granted = await grant_achievement(self.bot, ctx.guild_id, user.id, ach_def, ctx.channel if announce else None, None, ctx.response.send_message)

        if granted:
            if not announce or ach_def.get("secret"):
                await ctx.response.send_message(f"Granted **{ach_def['name']}** to {user.mention}.", ephemeral=True)
        else:
            await ctx.response.send_message("Failed to grant achievement.", ephemeral=True)

    @achievement_group.command(name="setmetric", description="Manually override a progress metric for a user")
    @default_permissions(PERMS)
    @guild_only()
    @describe(
        user="The user whose metric to update",
        metric="Metric name: messages_sent · reactions_added · voice_minutes · a_<achievement_id> (per-achievement counter)",
        value="The value to assign (or the amount to add/subtract when relative = True)",
        relative="Add this amount to the existing value instead of replacing it",
    )
    @is_owner()
    async def achievement_setmetric(self, ctx: Interaction, user: User, metric: str, value: float, relative: bool = False):
        if ctx.user.id != OWNER_ID:
            await ctx.response.send_message("Only the bot owner can use this command.", ephemeral=True)
            return

        metric = metric.strip()

        if metric not in _BUILTIN_METRICS and not metric.startswith("a_"):
            await ctx.response.send_message(
                "**Invalid metric.** Accepted values:\n- `messages_sent`\n- `reactions_added`\n- `voice_minutes`\n"
                "- `a_<achievement_id>` - per-achievement progress counter", ephemeral=True)
            return

        if metric.startswith("a_"):
            linked_id = metric[2:]
            if not await db.db_get_achievement_def(ctx.guild_id, linked_id):
                await ctx.response.send_message(
                    f"No achievement with ID `{linked_id}` exists in this server.\n"
                    "Per-achievement counters must reference a valid achievement ID.", ephemeral=True)
                return

        all_progress = await db.db_get_all_progress(ctx.guild_id, user.id)
        old_value = float(all_progress.get(metric) or 0)
        new_value = old_value + value if relative else value

        if new_value < 0:
            await ctx.response.send_message(
                f"Resulting value would be `{new_value:g}`, which is negative. Metric values cannot go below zero.", ephemeral=True)
            return

        await db.db_set_progress(ctx.guild_id, user.id, metric, new_value)

        embed = Embed(title="Metric Updated", color=0x2ECC71)
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Metric", value=f"`{metric}`", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="Old Value", value=f"`{old_value:g}`", inline=True)

        if relative:
            delta_str = f"+{value:g}" if value >= 0 else f"{value:g}"
            embed.add_field(name="Change", value=delta_str, inline=True)

        embed.add_field(name="New Value", value=f"`{new_value:g}`", inline=True)

        await ctx.response.send_message(embed=embed, ephemeral=True)

    @achievement_group.command(name="revoke", description="Revoke an achievement from a user")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(user="The user to revoke the achievement from", achievement_id="The ID of the achievement to revoke")
    async def achievement_revoke(self, ctx: Interaction, user: User, achievement_id: str, revoke_xp: bool = None):
        if not ctx.permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission to revoke achievements.")
            return

        if not await db.db_has_achievement(ctx.guild_id, user.id, achievement_id):
            await ctx.response.send_message(f"{user.mention} doesn't have this achievement.", ephemeral=True)
            return

        if revoke_xp is None:
            revoke_xp = (await db.db_get_levelling_config(ctx.guild_id))["enabled"]

        ach_def = await db.db_get_achievement_def(ctx.guild_id, achievement_id)
        await db.db_revoke_achievement(ctx.guild_id, user.id, achievement_id)
        if revoke_xp and ach_def and "xp_reward" in ach_def and ach_def["xp_reward"]:
            from cogs.levelling import award_xp
            await award_xp(ctx.guild_id, user.id, -ach_def["xp_reward"], ctx.channel, ctx.guild.get_member(user.id), "revoked_achievement")
        if ach_def:
            await db.db_delete_progress_metric(ctx.guild_id, user.id, "a_" + ach_def["achievement_id"])
        name = ach_def["name"] if ach_def else achievement_id

        await ctx.response.send_message(f"Revoked achievement **{name}** from {user.mention}.")

    @achievement_group.command(name="channel", description="Set a channel for Contore to send achievement notifications in")
    @describe(channel="The channel to send achievement notifications into")
    async def set_achievement_channel(self, ctx: Interaction, channel: GuildChannel):
        await db.db_set_achievement_channel(ctx.guild_id, channel.id)
        await ctx.response.send_message(f"Set the achievements channel to {channel.mention}.")

    @achievement_group.command(name="transfer", description="Swap all achievement data between two users")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(user1="First user", user2="Second user", adjust_xp="Adjust each user's levelling XP to reflect the achievement XP rewards they now hold")
    async def achievement_swap(self, ctx: Interaction, user1: User, user2: User, adjust_xp: bool = None):
        if not ctx.permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission.", ephemeral=True)
            return

        if user1.id == user2.id:
            await ctx.response.send_message("You cannot swap a user's achievement data with themselves.", ephemeral=True)
            return

        if adjust_xp is None:
            adjust_xp = (await db.db_get_levelling_config(ctx.guild_id))["enabled"]

        await ctx.response.defer(ephemeral=True)

        db.db_invalidate_progress_cache(ctx.guild_id, user1.id)
        db.db_invalidate_progress_cache(ctx.guild_id, user2.id)

        u1_achs = await db.db_get_user_achievements(ctx.guild_id, user1.id)
        u2_achs = await db.db_get_user_achievements(ctx.guild_id, user2.id)
        u1_prog = dict(await db.db_get_all_progress(ctx.guild_id, user1.id))
        u2_prog = dict(await db.db_get_all_progress(ctx.guild_id, user2.id))

        xp_delta_u1 = xp_delta_u2 = 0.0
        if adjust_xp:
            u1_ach_xp = 0
            for aid in u1_achs:
                d = await db.db_get_achievement_def(ctx.guild_id, aid)
                if d:
                    u1_ach_xp += d.get("xp_reward") or 0

            u2_ach_xp = 0
            for aid in u2_achs:
                d = await db.db_get_achievement_def(ctx.guild_id, aid)
                if d:
                    u2_ach_xp += d.get("xp_reward") or 0

            xp_delta_u1 = float(u2_ach_xp - u1_ach_xp)
            xp_delta_u2 = float(u1_ach_xp - u2_ach_xp)

        for aid in u1_achs:
            await db.db_revoke_achievement(ctx.guild_id, user1.id, aid)
        for aid in u2_achs:
            await db.db_revoke_achievement(ctx.guild_id, user2.id, aid)

        for aid, ts in u2_achs.items():
            await db.db_grant_achievement(ctx.guild_id, user1.id, aid, ts)
        for aid, ts in u1_achs.items():
            await db.db_grant_achievement(ctx.guild_id, user2.id, aid, ts)

        all_metrics = set(u1_prog.keys()) | set(u2_prog.keys())
        for metric in all_metrics:
            await db.db_set_progress(ctx.guild_id, user1.id, metric, u2_prog.get(metric, 0.0))
            await db.db_set_progress(ctx.guild_id, user2.id, metric, u1_prog.get(metric, 0.0))

        if adjust_xp:
            from cogs.levelling import award_xp
            m1 = ctx.guild.get_member(user1.id)
            m2 = ctx.guild.get_member(user2.id)
            if xp_delta_u1 != 0:
                await award_xp(ctx.guild_id, user1.id, xp_delta_u1, None, m1, "achievement_swap")
            if xp_delta_u2 != 0:
                await award_xp(ctx.guild_id, user2.id, xp_delta_u2, None, m2, "achievement_swap")

        def _user_field(gained: dict, lost: dict, xp_delta: float) -> str:
            lines = [f"Achievements: **{len(lost):,}** → **{len(gained):,}**"]
            if xp_delta != 0:
                lines.append(f"XP adjustment: `{xp_delta:+.0f}`")
            return "\n".join(lines)

        metrics_display = "`, `".join(sorted(all_metrics)) if all_metrics else "none"
        if len(metrics_display) > 950:
            shown = []
            total = 0
            for m in sorted(all_metrics):
                if total + len(m) + 4 > 900:
                    shown.append(f"...and {len(all_metrics) - len(shown)} more")
                    break
                shown.append(m)
                total += len(m) + 4
            metrics_display = "`, `".join(shown)

        if ctx.guild:
            await _sync_achievement_roles(ctx.guild, user1.id, ctx.guild_id)
            await _sync_achievement_roles(ctx.guild, user2.id, ctx.guild_id)

        embed = Embed(title="Achievement Data Swapped", color=0x2ECC71)
        embed.add_field(name=user1.display_name, value=_user_field(u2_achs, u1_achs, xp_delta_u1), inline=True)
        embed.add_field(name=user2.display_name, value=_user_field(u1_achs, u2_achs, xp_delta_u2), inline=True)
        embed.add_field(name=f"Progress Metrics Swapped ({len(all_metrics):,})", value=f"`{metrics_display}`", inline=False)
        if adjust_xp and xp_delta_u1 == 0 and xp_delta_u2 == 0:
            embed.set_footer(text="No XP adjustment needed; both users held equal achievement XP rewards.")

        await ctx.followup.send(embed=embed, ephemeral=True)
    # endregion

    # region viewing: list / progress / stats / leaderboard / scan
    @achievement_group.command(name="list", description="List all achievements in this server")
    @describe(show_secret="Show secret achievements (admin only)", filter_rarity="Filter by rarity", filter_type="Filter by type",
              ephemeral="Required to view the descriptions of any secret achievements you have")
    async def achievement_list(self, ctx: Interaction, show_secret: bool = False,
                                filter_rarity: Literal["All", "COMMON", "UNCOMMON", "RARE", "EPIC", "LEGENDARY", "MYTHIC"] = "All",
                                filter_type: Literal["All", "Manual", "Automated"] = "All", ephemeral: bool = False):
        user_achievements = await db.db_get_user_achievements(ctx.guild_id, ctx.user.id)
        achievements_all = await db.db_get_achievement_defs(ctx.guild_id)
        if not achievements_all:
            await ctx.response.send_message("This server has no achievements yet!", ephemeral=True)
            return

        is_admin = ctx.user.guild_permissions.manage_guild
        achievements = list(achievements_all)

        if filter_rarity != "All":
            achievements = [a for a in achievements if a.get("rarity") == filter_rarity]

        if filter_type != "All":
            achievements = [a for a in achievements if a.get("type") == filter_type.lower()]

        if not achievements:
            await ctx.response.send_message("No achievements match your filters.", ephemeral=True)
            return

        grouped = {}
        for ach in achievements:
            if ach["secret"] and (not show_secret or ach["achievement_id"] not in user_achievements and not is_admin):
                continue

            rarity = ach.get("rarity", "COMMON")
            if rarity not in grouped:
                grouped[rarity] = []
            grouped[rarity].append(ach)

        embeds = []
        rarity_order = ["MYTHIC", "LEGENDARY", "EPIC", "RARE", "UNCOMMON", "COMMON"]

        for rarity in rarity_order:
            if rarity not in grouped:
                continue

            embed = Embed(title=f"{RARITY_EMOJI[rarity]} {rarity} Achievements", color=RARITY_COLORS[rarity])

            for ach in sorted(grouped[rarity], key=lambda x: (x.get("sort_order", 10**9), -x["points"])):
                field_name = f"{ach['icon']} **{ach['name']}** ({ach['points']:,} pts)"

                field_value = ach.get("description")
                if field_value is None and ach.get("type") == "automated":
                    field_value = get_condition_description(ach.get("trigger"))

                if ach["secret"] and not ephemeral:
                    field_value = "\\*" * 10

                xp_reward = ach.get("xp_reward", 0) or 0
                if xp_reward > 0:
                    xp_line = f"✨ +{xp_reward:,} XP when unlocked"
                    field_value = f"{field_value}\n{xp_line}" if field_value else xp_line

                role_rewards = ach.get("role_rewards") or []
                if role_rewards:
                    role_line = "🎭 " + " ".join(f"<@&{r}>" for r in role_rewards)
                    field_value = f"{field_value}\n{role_line}" if field_value else role_line

                if field_value is not None:
                    embed.add_field(name=field_name, value=field_value, inline=False)

            embeds.append(embed)

        if not embeds:
            await ctx.response.send_message("No visible achievements yet!")
            return

        total_possible = sum(a["points"] for a in achievements_all
                              if not a["secret"] or show_secret and (a["achievement_id"] in user_achievements or is_admin))
        user_points = await calculate_total_points(ctx.guild_id, ctx.user.id)
        embeds[-1].set_footer(text=f"Your Points: {user_points:,}/{total_possible:,}")

        await ctx.response.send_message(embeds=embeds[:10], ephemeral=ephemeral)

    @app_commands.command(name="achievements", description="View someone's achievements")
    @describe(user="The user to view achievements for (leave blank for yourself)", ephemeral="Required to view the descriptions of any secret achievements you have")
    @allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def achievement_view(self, ctx: Interaction, user: User = None, ephemeral: bool = False):
        target = user or ctx.user

        all_achievements = await db.db_get_achievement_defs(ctx.guild_id)
        if not all_achievements:
            await ctx.response.send_message("This server has no achievements yet!", ephemeral=True)
            return

        user_achievements = await db.db_get_user_achievements(ctx.guild_id, target.id)
        self_achievements = user_achievements if target.id == ctx.user.id else await db.db_get_user_achievements(ctx.guild_id, ctx.user.id)

        total_achievements = len(all_achievements)
        completed_count = len(user_achievements)
        completion_percentage = (completed_count / total_achievements * 100) if total_achievements > 0 else 0

        bar_length = 10
        filled = int(bar_length * completion_percentage / 100)
        completion_bar = "█" * filled + "░" * (bar_length - filled)

        progress = await db.db_get_all_progress(ctx.guild_id, target.id)
        level_data = await db.db_get_level_user(ctx.guild_id, target.id)
        target_member = ctx.guild.get_member(target.id)
        target_roles = target_member.roles if target_member else []

        rarity_order = {"COMMON": 0, "UNCOMMON": 1, "RARE": 2, "EPIC": 3, "LEGENDARY": 4, "MYTHIC": 5}
        sorted_achievements = sorted(all_achievements,
                key=lambda x: (rarity_order.get(x.get("rarity", "COMMON"), 0), x.get("sort_order", 10**9), -x.get("points", 0)))

        total_possible = sum(a["points"] for a in all_achievements if not a["secret"])
        user_points = await calculate_total_points_from_achievements(user_achievements, ctx.guild_id)

        entries: list[str] = []
        for ach in sorted_achievements:
            if ach.get("secret") and ach["achievement_id"] not in user_achievements:
                continue

            has_it = ach["achievement_id"] in user_achievements

            if has_it:
                icon = "✅"
            else:
                trigger = ach.get("trigger", {})
                in_progress = False
                progress_percent = 0

                percent = _condition_progress_percent(trigger, ach["achievement_id"], progress, level_data, target_roles, user_achievements)
                if percent is not None and 0 < percent < 100:
                    in_progress = True
                    progress_percent = percent

                icon = "🔥" if in_progress else "❌"

            desc = ach.get("description")
            if desc is None and ach.get("type") == "automated":
                desc = get_condition_description(ach.get("trigger"))

            if ach.get("secret") and (not ephemeral or ach["achievement_id"] not in self_achievements):
                desc = "\\*" * 10

            line = f"{icon} **{ach['name']}**\n{desc}\n"

            if not has_it and icon == "🔥":
                prog_filled = int(bar_length * progress_percent / 100)
                prog_bar = "█" * prog_filled + "░" * (bar_length - prog_filled)
                line += f"{prog_bar} ({progress_percent:.1f}%)\n"
            entries.append(line)

        PAGE = 12
        pages = [entries[i:i + PAGE] for i in range(0, len(entries), PAGE)] or [[]]
        embeds = []
        header = (f"**{target.mention} has completed {completed_count}/{total_achievements} achievements!**\n"
                  f"{completion_bar} ({completion_percentage:.1f}%)\n\n-\n\n")
        for n, page in enumerate(pages):
            e = Embed(title=f"{target.display_name}'s Achievements", description=header + "\n".join(page), color=custom_hash(target.id))
            e.set_footer(text=f"Points: {user_points:,}/{total_possible:,}   ·   Page {n + 1}/{len(pages)}")
            if target.avatar:
                e.set_thumbnail(url=target.display_avatar.url)
            embeds.append(e)

        await ctx.response.send_message(embed=embeds[0], view=SimplePageView(embeds, ctx.user.id) if len(embeds) > 1 else MISSING, ephemeral=ephemeral)

    @achievement_group.command(name="progress", description="View your progress toward achievements")
    @describe(user="The user to view progress for (leave blank for yourself)")
    async def achievement_progress(self, ctx: Interaction, user: User = None, ephemeral: bool = False):
        target = user or ctx.user

        progress = await db.db_get_all_progress(ctx.guild_id, target.id)
        level_data = await db.db_get_level_user(ctx.guild_id, target.id)
        user_achievements = await db.db_get_user_achievements(ctx.guild_id, target.id)
        target_member = ctx.guild.get_member(target.id)
        target_roles = target_member.roles if target_member else []

        embed = Embed(title=f"{target.display_name}'s Achievement Progress", color=custom_hash(target.id))

        embed.add_field(name="Messages Sent", value=f"**{int(progress['messages_sent']):,}**", inline=True)
        embed.add_field(name="Reactions Added", value=f"**{int(progress['reactions_added']):,}**", inline=True)
        embed.add_field(name="Voice Minutes", value=f"**{float(progress['voice_minutes']):,.1f}**", inline=True)

        all_defs = await db.db_get_achievement_defs(ctx.guild_id)
        if all_defs:
            next_achievements = []
            for ach_def in all_defs:
                if ach_def.get("type") != "automated" or ach_def.get("secret"):
                    continue

                if ach_def["achievement_id"] in user_achievements:
                    continue

                trigger = ach_def.get("trigger")
                if not trigger:
                    continue

                percent = _condition_progress_percent(trigger, ach_def["achievement_id"], progress, level_data, target_roles, user_achievements)
                if percent is None or not (0 < percent < 100):
                    continue

                next_achievements.append({
                    "name": ach_def["name"], "icon": ach_def["icon"],
                    "desc": ach_def.get("description") or get_condition_description(trigger),
                    "percent": percent,
                })

            next_achievements.sort(key=lambda x: x["percent"], reverse=True)

            if next_achievements[:5]:
                progress_text = ""
                for ach in next_achievements[:5]:
                    bar_length = 10
                    filled = int(bar_length * ach["percent"] / 100)
                    bar = "█" * filled + "░" * (bar_length - filled)
                    progress_text += f"{ach['icon']} **{ach['name']}**\n{ach['desc']}\n{bar} ({ach['percent']:.1f}%)\n\n"

                embed.add_field(name="Next Achievements", value=progress_text, inline=False)

        await ctx.response.send_message(embed=embed, ephemeral=ephemeral)

    @achievement_group.command(name="stats", description="View server-wide achievement statistics")
    @describe(achievement_id="View completion stats for a specific achievement")
    async def achievement_stats(self, ctx: Interaction, achievement_id: str = None):
        if achievement_id:
            ach_def = await db.db_get_achievement_def(ctx.guild_id, achievement_id)
            if not ach_def:
                await ctx.response.send_message(f"Achievement `{achievement_id}` not found.", ephemeral=True)
                return

            completed, total = await db.db_get_achievement_completion(ctx.guild_id, achievement_id)
            percentage = (completed / total * 100) if total > 0 else 0

            desc = ach_def.get("description")
            if desc is None and ach_def.get("type") == "automated":
                desc = get_condition_description(ach_def.get("trigger"))

            embed = Embed(title=f"{ach_def['icon']} {ach_def['name']}", description=desc, color=RARITY_COLORS.get(ach_def.get("rarity", "COMMON")))

            xp_reward = ach_def.get("xp_reward", 0) or 0

            embed.add_field(name="Rarity", value=f"{RARITY_EMOJI.get(ach_def.get('rarity', 'COMMON'))} {ach_def.get('rarity', 'COMMON')}", inline=True) \
                .add_field(name="Points", value=str(ach_def["points"]), inline=True) \
                .add_field(name="XP Reward", value=f"+{xp_reward:,} XP" if xp_reward > 0 else "None", inline=True) \
                .add_field(name="Type", value=ach_def.get("type", "manual").title(), inline=True)

            bar_length = 20
            filled = int(bar_length * percentage / 100)
            bar = "█" * filled + "░" * (bar_length - filled)

            embed.add_field(name="Server Completion", value=f"{bar}\n{completed:,}/{total:,} users ({percentage:.1f}%)", inline=False)

            await ctx.response.send_message(embed=embed)
        else:
            all_defs = await db.db_get_achievement_defs(ctx.guild_id)
            if not all_defs:
                await ctx.response.send_message("This server has no achievements yet!", ephemeral=True)
                return

            embed = Embed(title=f"{ctx.guild.name} Achievement Statistics", color=0xF52754)

            total_achievements = len(all_defs)
            total_points = sum(a["points"] for a in all_defs)

            rarity_counts = {}
            for ach in all_defs:
                rarity = ach.get("rarity", "COMMON")
                rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1

            rarity_text = "\n".join(f"{RARITY_EMOJI[r]} {r}: {count:,}"
                                     for r, count in sorted(rarity_counts.items(), key=lambda x: list(RARITY_COLORS.keys()).index(x[0])))

            embed.add_field(name="Total Achievements", value=f"{total_achievements:,}", inline=True) \
                .add_field(name="Total Points Available", value=f"{total_points:,}", inline=True) \
                .add_field(name="Rarity Breakdown", value=rarity_text, inline=False)

            completion_counts = {}
            for ach_def in all_defs:
                completed, total = await db.db_get_achievement_completion(ctx.guild_id, ach_def["achievement_id"])
                if total > 0:
                    completion_counts[ach_def["achievement_id"]] = (completed, total, ach_def)

            if completion_counts:
                sorted_completions = sorted(completion_counts.items(), key=lambda x: (x[1][0] / x[1][1], x[1][0]), reverse=True)[:5]

                most_completed = "\n".join(
                    f"{ach_def['icon']} **{ach_def['name']}**: {completed:,}/{total:,} ({completed/total*100:.1f}%)"
                    for _, (completed, total, ach_def) in sorted_completions)

                embed.add_field(name="Most Completed", value=most_completed, inline=False)

            await ctx.response.send_message(embed=embed)

    @achievement_group.command(name="leaderboard", description="View the achievement leaderboard")
    @describe(top="Number of users to show (default: 10)")
    async def achievement_leaderboard(self, ctx: Interaction, top: int = 10):
        all_users = await db.db_get_all_achievement_users(ctx.guild_id)
        if not all_users:
            await ctx.response.send_message("No one has earned any achievements yet!")
            return

        await ctx.response.defer()

        leaderboard = []
        for user_id in all_users:
            member = ctx.guild.get_member(user_id)
            if member:
                points = await calculate_total_points(ctx.guild_id, user_id)
                achievements = await db.db_get_user_achievements(ctx.guild_id, user_id)
                leaderboard.append((user_id, points, len(achievements)))

        if not leaderboard:
            await ctx.followup.send("No one has earned any achievements yet!")
            return

        leaderboard.sort(key=lambda x: (x[1], x[2]), reverse=True)
        entries = [(uid, f"{pts:,} pts", f"{cnt:,} earned") for uid, pts, cnt in leaderboard[:top]]

        try:
            f = await generate_generic_leaderboard_card(ctx.guild, entries, "Achievement Leaderboard", ctx.guild.name)
            await ctx.followup.send(file=f)
        except Exception as e:
            print(f"[Achievements] leaderboard card error: {e}")
            await ctx.followup.send("Failed to generate leaderboard.")

    @achievement_group.command(name="scan", description="Scan all server members and award any achievements they've silently qualified for")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(announce="Post grant announcements as achievements are awarded (default: True)", dry_run="Preview what would be granted without actually granting anything")
    async def achievement_scan(self, ctx: Interaction, announce: bool = True, dry_run: bool = False):
        if not ctx.permissions.manage_guild:
            return
        if not ctx.user.guild_permissions.manage_guild:
            await ctx.response.send_message("You need the `Manage Server` permission.", ephemeral=True)
            return

        await ctx.response.defer(ephemeral=True)

        if not ctx.guild.chunked:
            await ctx.guild.chunk(cache=True)

        total_members = sum(1 for m in ctx.guild.members if not m.bot)

        progress_msg = await ctx.followup.send(
            f"{'🔍' if dry_run else '⏳'} {'Simulating' if dry_run else 'Scanning'} **{total_members:,}** member(s)...",
            ephemeral=True, wait=True)

        edit_every = max(1, total_members // 20)
        last_edit_at = 0

        async def on_progress(checked: int, total: int) -> None:
            nonlocal last_edit_at
            is_final = checked == total
            if not is_final and (checked - last_edit_at) < edit_every:
                return
            last_edit_at = checked
            pct = (checked / total * 100) if total else 100
            filled = int(10 * pct / 100)
            bar = "█" * filled + "░" * (10 - filled)
            icon = "🔍" if dry_run else "⏳"
            await progress_msg.edit(content=f"{icon} {'Simulating' if dry_run else 'Scanning'}... {bar} **{checked:,} / {total:,}** ({pct:.0f}%)")

        result = await scan_guild_achievements(self.bot, ctx.guild_id, announce=announce and not dry_run, dry_run=dry_run, on_progress=on_progress)

        if "error" in result:
            await progress_msg.edit(content=f"❌ Scan failed: {result['error']}", embed=None)
            return

        verb = "Would Grant" if dry_run else "Granted"
        color = 0xF1C40F if dry_run else 0x2ECC71

        embed = Embed(title=f"{'Dry Run - ' if dry_run else ''}Scan Complete", color=color)
        embed.add_field(name="Members Scanned", value=f"{result['checked']:,}", inline=True)
        embed.add_field(name=f"Achievements {verb}", value=f"{result['granted']:,}", inline=True)

        grants = result["grants"]
        if grants:
            lines: list[str] = []
            for i, (user_id, names) in enumerate(grants.items()):
                if i >= 15:
                    lines.append(f"*...and {len(grants) - 15} more user(s)*")
                    break
                member = ctx.guild.get_member(user_id)
                display = member.mention if member else f"<@{user_id}>"
                joined = ", ".join(f"**{n}**" for n in names)
                lines.append(f"{display}: {joined}")

            embed.add_field(name=f"{'Would-Be ' if dry_run else ''}Recipients ({len(grants):,} user(s))", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="Recipients", value="*Everyone is already up to date - no new achievements to grant.*", inline=False)

        if dry_run:
            embed.set_footer(text="Dry run 0 nothing was granted. Re-run without `dry_run` to commit.")

        await progress_msg.edit(content="", embed=embed)
    # endregion

    # region listeners
    @commands.Cog.listener()
    async def on_message(self, message: Message):
        asyncio.create_task(check_message_achievements(self.bot, message))

        if message.author.id == self.bot.user.id or message.guild is None:
            return

        await db.db_update_progress(message.guild.id, message.author.id, "messages_sent", 1)
        asyncio.create_task(check_automated_achievements(self.bot, message.guild.id, message.author.id, message.channel))

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.guild_id:
            await db.db_update_progress(payload.guild_id, payload.user_id, "reactions_added", 1)
            channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)
            asyncio.create_task(check_automated_achievements(self.bot, payload.guild_id, payload.user_id, channel))

    @commands.Cog.listener()
    async def on_member_update(self, before: Member, after: Member):
        if before.roles != after.roles:
            new_roles = set(after.roles) - set(before.roles)
            if new_roles:
                asyncio.create_task(check_automated_achievements(self.bot, after.guild.id, after.id, roles=new_roles))

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: Guild):
        db.db_clear_achievement_guild_cache(guild.id)
    # endregion


async def setup(bot: commands.Bot):
    await bot.add_cog(AchievementsCog(bot))

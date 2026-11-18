"""Pokédex completion milestones: catch every species in a generation's
(or the whole National Dex's) range and get a one-time money+item reward.
Ported from `POKEDEX_MILESTONES`/`check_pokedex_milestones`/
`_notify_milestone_unlocked`/`_milestone_display_lines`.

Called from wherever a trainer's `dex_seen` set grows: `collection.
pokemon_give`, `trading.pokemon_trade`, `gts.gts_fulfill`,
`evolution.evolve_pokemon`, `starter.pokemon_starter`, and
`breeding.hatch_due_eggs` all wire this in already. A catch itself (the
battle engine's eventual win-resolution hook, once mid-battle catching is
built -- see `pokemon.battle.ai`'s docstring on what's still a
placeholder there) is the one growth path that doesn't yet, since it
doesn't exist to wire into.
"""

from __future__ import annotations

from discord import Embed

from pokemon.commands import repository
from pokemon.commands.display import item_display

POKEDEX_MILESTONES: list[dict] = [
    {"id": 1, "name": "Gen I \u200b\u00b7\u200b Kanto", "range": (1, 151), "reward_money": 10000,
     "rewards": [(1, "Exp. Share"), (3, "Cherish Ball")]},
    {"id": 2, "name": "Gen II \u200b\u00b7\u200b Johto", "range": (152, 251), "reward_money": 10000,
     "rewards": [(5, "Max Revive"), (3, "Cherish Ball")]},
    {"id": 3, "name": "Gen III \u200b\u00b7\u200b Hoenn", "range": (252, 386), "reward_money": 10000,
     "rewards": [(2, "Shiny Stone"), (3, "Cherish Ball")]},
    {"id": 4, "name": "Gen IV \u200b\u00b7\u200b Sinnoh", "range": (387, 493), "reward_money": 10000,
     "rewards": [(1, "IV Booster"), (1, "Gracidea"), (3, "Cherish Ball")]},
    {"id": 5, "name": "Gen V \u200b\u00b7\u200b Unova", "range": (494, 649), "reward_money": 10000,
     "rewards": [(2, "Dream Ball"), (3, "Cherish Ball")]},
    {"id": 6, "name": "Gen VI \u200b\u00b7\u200b Kalos", "range": (650, 721), "reward_money": 10000,
     "rewards": [(20, "HP Up"), (20, "Protein"), (20, "Iron"), (3, "Cherish Ball")]},
    {"id": 0, "name": "National \u200b\u00b7\u200b All", "range": (1, 721), "reward_money": 50000,
     "rewards": [(1, "Master Ball"), (5, "Cherish Ball")]},
]


def milestone_progress(milestone: dict, seen: set[int]) -> tuple[int, int]:
    """Ported verbatim from `_milestone_progress`."""
    lo, hi = milestone["range"]
    total = hi - lo + 1
    caught = sum(1 for sid in seen if lo <= sid <= hi)
    return caught, total


async def check_pokedex_milestones(user_id: int, seen: set[int] | None = None) -> list[dict]:
    """Grants + records any newly-completed milestones, returning the
    ones just unlocked (empty if none). Ported verbatim from
    `check_pokedex_milestones`. Callers that want to notify the user
    should follow up with `notify_milestone_unlocked` for each result."""
    if seen is None:
        seen = await repository.get_pokedex_seen(user_id)
    claimed = await repository.get_dex_milestones(user_id)
    newly_unlocked = []
    for m in POKEDEX_MILESTONES:
        if m["id"] in claimed:
            continue
        caught, total = milestone_progress(m, seen)
        if caught < total:
            continue
        claimed.add(m["id"])
        await repository.add_pokedollars(user_id, m["reward_money"])
        for qty, item in m.get("rewards", []):
            await repository.add_item(user_id, item, qty)
        newly_unlocked.append(m)
    if newly_unlocked:
        await repository.set_dex_milestones(user_id, claimed)
    return newly_unlocked


async def notify_milestone_unlocked(bot, user_id: int, milestone: dict) -> None:
    """DMs the trainer about a newly-unlocked milestone. Ported verbatim
    from `_notify_milestone_unlocked`."""
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        lo, hi = milestone["range"]
        reward_bits = [f"**\u20b1{milestone['reward_money']:,}**"]
        for qty, item in milestone.get("rewards", []):
            reward_bits.append(f"{qty:,}\u00d7 {item_display(item)}")
        embed = Embed(
            title=f"🏆 Pokédex Milestone Unlocked: {milestone['name']}!",
            description=f"You've caught every species from **#{lo}** to **#{hi}**!\n\n"
                        f"**Rewards:** " + ", ".join(reward_bits),
            color=0xF7B71E)
        await user.send(embed=embed)
    except Exception:
        pass


async def check_and_notify_milestones(bot, user_id: int, seen: set[int] | None = None) -> None:
    """Convenience wrapper for the common "just grew dex_seen, handle
    whatever that unlocks" call site -- checks, grants, and DMs in one
    call. Not a direct port (the monolith's call sites each did the
    check-then-loop-and-notify pairing inline); factored out since every
    dex_seen-growing call site wants the exact same two steps."""
    for m in await check_pokedex_milestones(user_id, seen):
        await notify_milestone_unlocked(bot, user_id, m)


def milestone_display_lines(seen: set[int]) -> list[str]:
    """Progress lines for a Pokédex-overview display (`/pokemon species`'s
    eventual summary view). Ported verbatim from `_milestone_display_lines`."""
    lines = []
    for m in POKEDEX_MILESTONES:
        caught, total = milestone_progress(m, seen)
        complete = caught >= total
        mark = "✅" if complete else "▫️"
        lines.append(f"{mark} **{m['name']}** \u2014 {caught}/{total}")
    return lines

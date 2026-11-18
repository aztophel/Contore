"""Passive, time-based regeneration for a trainer's ACTIVE TEAM (not their
whole boxed collection -- see `get_hurt_team_pokemon`/etc.'s `JOIN
pokemon_team_members`): HP and status clear a few hours after taking
damage, PP refills after longer, and a fainted team member revives after
longest of all. Ported from the monolith's `auto_heal_check`/
`auto_revive_check` -- both `@tasks.loop`s registered directly at module
scope there and started once at bot startup.

This was a real gap, not a stylistic one: `repository.py` already had
every DB primitive these two loops need
(`get_hurt_team_pokemon`/`get_pp_needing_team_pokemon`/
`get_fainted_team_pokemon`/`bulk_heal`/`bulk_reset_pp`/`bulk_revive`, all
byte-for-byte the same queries as the monolith's `db_*` equivalents) --
Track B ported the data layer but nothing above it ever called it. Without
this module, `hurt_at`/`fainted_at`/`pp_reset_at` are written by battles
(see `pokemon.commands.battle_bridge.persist_battle_result`) but never
read back by anything, so Pokemon that faint or run low on PP would stay
that way forever instead of slowly recovering.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from discord.ext import tasks

from pokemon.commands import repository
from pokemon.dex.species import get_default_repository as get_species_repo

# Ported verbatim from `_DEFAULT_AUTO_HEAL_HOURS`/`_RARITY_HEAL_MULT`/
# `_level_auto_heal_multiplier`/`_MIN_*_HOURS`.
_DEFAULT_AUTO_HEAL_HOURS = {"revive": 8, "heal": 8, "pp": 30}
_RARITY_HEAL_MULT = {
    "common": 0.9, "uncommon": 0.95, "rare": 1.0,
    "epic": 1.15, "legendary": 1.5, "mythical": 1.5,
}


def _level_auto_heal_multiplier(level: int) -> float:
    return max(0.3, min(3.0, 0.3 + (level / 100) * 2.7))


_MIN_MULT = _level_auto_heal_multiplier(1) * 0.9
_MIN_REVIVE_HOURS = max(1, round(_DEFAULT_AUTO_HEAL_HOURS["revive"] * _MIN_MULT))
_MIN_HEAL_HOURS = max(1, round(_DEFAULT_AUTO_HEAL_HOURS["heal"] * _MIN_MULT))
_MIN_PP_HOURS = max(1, round(_DEFAULT_AUTO_HEAL_HOURS["pp"] * _MIN_MULT))


def _auto_heal_hours_for(species_id: int, level: int = 50) -> dict[str, int]:
    """How many hours each recovery type takes for this species/level --
    rarer and higher-level Pokemon take longer (they're worth more
    trouble to keep healthy yourself rather than just waiting), but never
    less than `_MIN_*_HOURS`' floor even at level 1."""
    species = get_species_repo().get(species_id)
    mult = _level_auto_heal_multiplier(level) * _RARITY_HEAL_MULT.get(species.rarity if species else "common", 1.0)
    return {k: max(1, round(v * mult)) for k, v in _DEFAULT_AUTO_HEAL_HOURS.items()}


@tasks.loop(minutes=30)
async def auto_heal_check() -> None:
    now = datetime.now()

    cutoff_hp = (now - timedelta(hours=_MIN_HEAL_HOURS * _auto_heal_hours_for(1, 1)["heal"])).isoformat()
    candidates = await repository.get_hurt_team_pokemon(cutoff_hp)
    to_heal = [r["id"] for r in candidates
               if now - datetime.fromisoformat(r["hurt_at"]) >= timedelta(hours=_auto_heal_hours_for(r["species_id"], r["level"])["heal"])]
    await repository.bulk_heal(to_heal)

    cutoff_pp = (now - timedelta(hours=_MIN_PP_HOURS)).isoformat()
    pp_candidates = await repository.get_pp_needing_team_pokemon(cutoff_pp)
    reset_rows: list[tuple[int, str]] = []
    for r in pp_candidates:
        if r["pp_reset_at"] is not None:
            hrs = _auto_heal_hours_for(r["species_id"], r["level"])["pp"]
            if now - datetime.fromisoformat(r["pp_reset_at"]) < timedelta(hours=hrs):
                continue
        try:
            moves = json.loads(r["moves"])
        except Exception:
            continue
        for mv in moves:
            mv["pp"] = mv.get("max_pp", mv["pp"])
        reset_rows.append((r["id"], json.dumps(moves)))
    await repository.bulk_reset_pp(reset_rows)


@auto_heal_check.error
async def _auto_heal_check_error(exc: Exception) -> None:
    print(f"[auto_heal_check] crashed: {exc!r}. restarting")
    if not auto_heal_check.is_running():
        auto_heal_check.start()


@tasks.loop(hours=1)
async def auto_revive_check() -> None:
    from pokemon.commands.stats import pokemon_max_hp

    now = datetime.now()
    cutoff = (now - timedelta(hours=_MIN_REVIVE_HOURS * _auto_heal_hours_for(1, 1)["revive"])).isoformat()
    candidates = await repository.get_fainted_team_pokemon(cutoff)
    species_repo = get_species_repo()
    revive_rows: list[tuple[int, int]] = []
    for r in candidates:
        hrs = _auto_heal_hours_for(r["species_id"])["revive"]
        if now - datetime.fromisoformat(r["fainted_at"]) < timedelta(hours=hrs):
            continue
        species = species_repo.get(r["species_id"])
        mhp = pokemon_max_hp(dict(r), species) if species else 1
        revive_rows.append((r["id"], max(1, mhp // 2)))
    await repository.bulk_revive(revive_rows)


@auto_revive_check.error
async def _auto_revive_check_error(exc: Exception) -> None:
    print(f"[auto_revive_check] crashed: {exc!r}. restarting")
    if not auto_revive_check.is_running():
        auto_revive_check.start()


def start_auto_heal_loops() -> None:
    """Call once at bot startup (`bot.py`'s `_load_pokemon_subsystem`,
    alongside `pokemon.commands.breeding.start_egg_hatch_loop`). Both
    loops fire their first iteration immediately on `.start()`, same as
    the monolith's did -- no `before_loop`/`wait_until_ready()` guard
    needed, since neither loop touches the Discord client at all (pure DB
    read/write), unlike the egg-hatch loop's DM-notification path."""
    if not auto_heal_check.is_running():
        auto_heal_check.start()
    if not auto_revive_check.is_running():
        auto_revive_check.start()

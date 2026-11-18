"""Database access for the Pokémon economy: owned Pokémon, trainer profile
flags, the item bag, money, battle teams/presets, friendship memory, the
GTS, and hunt/breed cooldowns.

Ported from the monolith's five `#region Database (pokemon: ...)` blocks
(core/config; items/trainer flags/money; teams/presets; friendship memory;
GTS -- cooldowns folded in here too, same shape). Behavior is unchanged
throughout; what changed is *plumbing*:

- `_pool`/`_affected` (module globals in the monolith) are now
  `pokemon.commands._db.pool()` / `.affected()` -- see that module for why.
- `db_add_pokemon` no longer reaches into monolith-global caches
  (`_POKEDEX_BY_ID`, `_pick_ability`, `_roll_gender`, `assign_initial_moves`)
  for its defaults; it resolves them through `pokemon.dex.species`,
  `pokemon.commands.spawning`, and `pokemon.commands.moveset` instead, and
  its keyword-only defaulted parameters are grouped at the end of the
  signature rather than interleaved with the required ones.
- IVs/EVs use the same full PokeAPI stat names as `Species.base_stats` and
  the frozen `StatChange.stat` (`"special-attack"`, not `"spa"`) instead of
  the monolith's inconsistent shorthand -- one stat-key vocabulary
  everywhere, per the "clean up stylistic inconsistencies" ask. See
  `IV_STAT_KEYS` below; `parse_ivs`/`parse_evs` accept old-style
  three/four-letter keys too so existing rows already in the database
  round-trip correctly.

Every row is still returned as a plain `dict` (via asyncpg's `Record`),
matching how the rest of this codebase (and the not-yet-ported UI layer)
expects to consume a "Pokémon" -- introducing a dataclass for owned
Pokémon rows is a reasonable future cleanup but out of scope for a
behavior-preserving port.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from pokemon.commands import leveling, moveset, spawning
from pokemon.commands._db import affected, pool
from pokemon.dex.species import Species, get_default_repository as get_species_repo

# Canonical IV/EV stat keys -- matches `Species.base_stats` / the frozen
# `StatChange.stat` naming (see module docstring).
IV_STAT_KEYS = ("hp", "attack", "defense", "special-attack", "special-defense", "speed")

# Old monolith shorthand -> canonical key, so rows written before this port
# still parse correctly instead of silently losing IVs/EVs on next read.
_LEGACY_STAT_ALIASES = {
    "atk": "attack", "def": "defense", "spa": "special-attack",
    "spd": "special-defense", "spe": "speed",
}

_FRIENDSHIP_RESTORE_WEEKS = 4  # ported verbatim from the monolith


def parse_ivs(raw: str | dict | None) -> dict[str, int]:
    """Parses a `pokemon_owned.ivs` cell into a canonical-keyed dict,
    defaulting any missing stat to a perfect 31 IV (matches the monolith's
    `parse_ivs`, which used the same default -- a missing IV column entry
    was never meant to mean "0 IV")."""
    if isinstance(raw, str):
        try:
            d = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            d = {}
    elif isinstance(raw, dict):
        d = raw
    else:
        d = {}
    d = {_LEGACY_STAT_ALIASES.get(k, k): v for k, v in d.items()}
    return {k: int(d.get(k, 31)) for k in IV_STAT_KEYS}


def parse_evs(raw: str | dict | None) -> dict[str, int]:
    """Same idea as `parse_ivs`, but EVs default to 0 (unset = untrained),
    matching the monolith's EV handling."""
    if isinstance(raw, str):
        try:
            d = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            d = {}
    elif isinstance(raw, dict):
        d = raw
    else:
        d = {}
    d = {_LEGACY_STAT_ALIASES.get(k, k): v for k, v in d.items()}
    return {k: int(d.get(k, 0)) for k in IV_STAT_KEYS}


# ---------------------------------------------------------------------------
# Core / config
# ---------------------------------------------------------------------------

async def get_pokemon_config(guild_id: int) -> dict:
    p = await pool()
    row = await p.fetchrow("SELECT * FROM pokemon_config WHERE guild_id=$1", guild_id)
    if not row:
        return {
            "enabled": False, "spawn_channel": None, "spawn_min": 15, "spawn_chance": 0.005,
            "catch_xp": 25, "generation": 6, "dex_cap_generation": None, "restrict_mode": "spawn_only",
        }
    return dict(row)


async def save_pokemon_config(guild_id: int, cfg: dict) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_config"
        "(guild_id, enabled, spawn_channel, spawn_min, spawn_chance, catch_xp,"
        " generation, dex_cap_generation, restrict_mode) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT (guild_id) DO UPDATE SET "
        "enabled=excluded.enabled, spawn_channel=excluded.spawn_channel, spawn_min=excluded.spawn_min, "
        "spawn_chance=excluded.spawn_chance, catch_xp=excluded.catch_xp, generation=excluded.generation, "
        "dex_cap_generation=excluded.dex_cap_generation, restrict_mode=excluded.restrict_mode",
        guild_id, int(cfg["enabled"]), cfg.get("spawn_channel"), cfg["spawn_min"], cfg["spawn_chance"],
        cfg["catch_xp"], cfg.get("generation", 6), cfg.get("dex_cap_generation"),
        cfg.get("restrict_mode", "spawn_only"),
    )


async def add_pokemon(
    owner_id: int,
    species_id: int,
    level: int,
    ivs: dict[str, int],
    nature: str,
    *,
    shiny: bool = False,
    caught_guild_id: int | None = None,
    ball_type: str = "Poké Ball",
    initial_moves: list[moveset.MoveSlot] | None = None,
    ability: str = "",
    gender: str | None = None,
    form: str | None = None,
    held_item: str | None = None,
    friendship: int | None = None,
) -> int:
    """Inserts a newly-caught/bred/gifted Pokémon and returns its new id.

    Only `owner_id`/`species_id`/`level`/`ivs`/`nature` are required;
    everything else has a sensible generation-aware default resolved from
    `species_id` (ability/gender/moveset roll, base friendship, starting
    XP for `level`) exactly like the monolith did, just sourced from the
    new dex/spawning modules instead of monolith-global caches. Pass an
    explicit value for any of the keyword args to override a roll (e.g.
    breeding passes an inherited ability/nature/moveset instead of a fresh
    roll).
    """
    species: Species | None = get_species_repo().get(species_id)

    if initial_moves is None:
        initial_moves = moveset.roll_initial_moveset(species_id, level)
    if not ability:
        ability = spawning.pick_ability(species) if species else ""
    if gender is None:
        gender = spawning.roll_gender(species) if species else "genderless"
    if friendship is None:
        friendship = species.base_friendship if species else 70
    growth_rate = species.growth_rate if species else "medium"
    initial_xp = leveling.total_xp_for_level(level, growth_rate)

    moves_json = json.dumps([{"name": m.name, "pp": m.pp, "max_pp": m.max_pp} for m in initial_moves])

    p = await pool()
    return await p.fetchval(
        "INSERT INTO pokemon_owned"
        "(owner_id, original_trainer_id, species_id, form, gender, level, xp, ivs, evs, nature, shiny,"
        " friendship, caught_at, caught_guild_id, ball_type, moves, ability, held_item, pp_reset_at)"
        " VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19) RETURNING id",
        owner_id, owner_id, species_id, form, gender, level, initial_xp, json.dumps(ivs), "{}", nature,
        int(shiny), friendship, datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
        caught_guild_id, ball_type, moves_json, ability, held_item, datetime.now().isoformat(),
    )


async def get_pokemon(pokemon_id: int) -> dict | None:
    p = await pool()
    row = await p.fetchrow("SELECT * FROM pokemon_owned WHERE id=$1", pokemon_id)
    return dict(row) if row else None


async def get_user_pokemon(owner_id: int) -> list[dict]:
    p = await pool()
    rows = await p.fetch(
        "SELECT po.* FROM pokemon_owned po "
        "WHERE po.owner_id = $1 "
        "  AND NOT EXISTS (SELECT 1 FROM pokemon_fused pf WHERE pf.partner_id = po.id) "
        "  AND NOT EXISTS (SELECT 1 FROM pokemon_gts_listings gl WHERE gl.pokemon_id = po.id AND gl.status = 'active') "
        "ORDER BY po.favorite DESC, po.id DESC", owner_id)
    return [dict(r) for r in rows]


async def pokemon_is_gts_listed(pokemon_id: int) -> bool:
    p = await pool()
    row = await p.fetchrow(
        "SELECT 1 FROM pokemon_gts_listings WHERE pokemon_id=$1 AND status='active'", pokemon_id)
    return row is not None


async def delete_pokemon(pokemon_id: int, owner_id: int) -> bool:
    p = await pool()
    status = await p.execute("DELETE FROM pokemon_owned WHERE id=$1 AND owner_id=$2", pokemon_id, owner_id)
    return affected(status) > 0


async def set_pokemon_nickname(pokemon_id: int, owner_id: int, nickname: str | None) -> bool:
    p = await pool()
    status = await p.execute(
        "UPDATE pokemon_owned SET nickname=$1 WHERE id=$2 AND owner_id=$3", nickname, pokemon_id, owner_id)
    return affected(status) > 0


async def set_pokemon_favorite(owner_id: int, pokemon_id: int) -> bool:
    p = await pool()
    async with p.acquire() as conn, conn.transaction():
        await conn.execute("UPDATE pokemon_owned SET favorite=0 WHERE owner_id=$1", owner_id)
        status = await conn.execute(
            "UPDATE pokemon_owned SET favorite=1 WHERE id=$1 AND owner_id=$2", pokemon_id, owner_id)
    return affected(status) > 0


async def mark_left_team(pokemon_id: int) -> None:
    p = await pool()
    await p.execute(
        "UPDATE pokemon_owned SET team_left_at=$1 WHERE id=$2 AND team_left_at IS NULL",
        datetime.now().isoformat(), pokemon_id)


async def mark_rejoined_team(pokemon_id: int) -> None:
    """Shifts `hurt_at`/`fainted_at`/`pp_reset_at` forward by however long
    the Pokémon spent off-team, so leaving/rejoining a team can't be used
    to dodge the passive-healing clock. Ported verbatim."""
    p = await pool()
    row = await p.fetchrow(
        "SELECT team_left_at, hurt_at, fainted_at, pp_reset_at FROM pokemon_owned WHERE id=$1", pokemon_id)
    if not row or not row["team_left_at"]:
        return
    left = datetime.fromisoformat(row["team_left_at"])
    elapsed = datetime.now() - left
    sets, vals = ["team_left_at=NULL"], []
    for col in ("hurt_at", "fainted_at", "pp_reset_at"):
        ts = row[col]
        if ts:
            vals.append((datetime.fromisoformat(ts) + elapsed).isoformat())
            sets.append(f"{col}=${len(vals)}")
    vals.append(pokemon_id)
    await p.execute(f"UPDATE pokemon_owned SET {', '.join(sets)} WHERE id=${len(vals)}", *vals)


async def transfer_pokemon(pokemon_id: int, from_id: int, to_id: int) -> bool:
    p = await pool()
    status = await p.execute(
        "UPDATE pokemon_owned SET owner_id=$1, favorite=0 WHERE id=$2 AND owner_id=$3",
        to_id, pokemon_id, from_id)
    ok = affected(status) > 0
    if ok:
        await set_trainer_flag(from_id, "has_traded_away", True)
        await remove_from_team(from_id, pokemon_id)
    return ok


async def get_pokedex_seen(user_id: int) -> set[int]:
    p = await pool()
    row = await p.fetchrow("SELECT dex_seen FROM pokemon_trainer WHERE user_id=$1", user_id)
    return set(json.loads(row["dex_seen"])) if row else set()


async def set_pokedex_seen(user_id: int, seen: set[int]) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_trainer(user_id, dex_seen) VALUES($1,$2) "
        "ON CONFLICT (user_id) DO UPDATE SET dex_seen=excluded.dex_seen",
        user_id, json.dumps(list(seen)))


async def get_pokemon_leaderboard(user_ids: list[int]) -> list[tuple[int, int, int]]:
    if not user_ids:
        return []
    p = await pool()
    rows = await p.fetch(
        "SELECT owner_id, COUNT(*) as total, SUM(shiny) as shinies FROM pokemon_owned "
        "WHERE owner_id = ANY($1::bigint[]) GROUP BY owner_id ORDER BY total DESC", user_ids)
    return [(r["owner_id"], r["total"], r["shinies"] or 0) for r in rows]


async def record_duel_result(winner_ids: list[int], loser_ids: list[int]) -> None:
    p = await pool()
    async with p.acquire() as conn, conn.transaction():
        for uid in winner_ids:
            await conn.execute(
                "INSERT INTO pokemon_trainer(user_id, duel_wins) VALUES($1,1) "
                "ON CONFLICT (user_id) DO UPDATE SET duel_wins=pokemon_trainer.duel_wins+1", uid)
        for uid in loser_ids:
            await conn.execute(
                "INSERT INTO pokemon_trainer(user_id, duel_losses) VALUES($1,1) "
                "ON CONFLICT (user_id) DO UPDATE SET duel_losses=pokemon_trainer.duel_losses+1", uid)


async def get_duel_record(user_id: int) -> tuple[int, int]:
    p = await pool()
    row = await p.fetchrow("SELECT duel_wins, duel_losses FROM pokemon_trainer WHERE user_id=$1", user_id)
    return (row["duel_wins"], row["duel_losses"]) if row else (0, 0)


async def get_duel_leaderboard(user_ids: list[int]) -> list[tuple[int, int, int]]:
    if not user_ids:
        return []
    p = await pool()
    rows = await p.fetch(
        "SELECT user_id, duel_wins, duel_losses FROM pokemon_trainer WHERE user_id = ANY($1::bigint[])",
        user_ids)
    return [(r["user_id"], r["duel_wins"], r["duel_losses"]) for r in rows]


async def set_pokemon_moves(pokemon_id: int, moves: list[dict]) -> None:
    p = await pool()
    await p.execute("UPDATE pokemon_owned SET moves=$1 WHERE id=$2", json.dumps(moves), pokemon_id)


async def set_pokemon_form(pokemon_id: int, form: str | None) -> None:
    p = await pool()
    await p.execute("UPDATE pokemon_owned SET form=$1 WHERE id=$2", form, pokemon_id)


async def set_pokemon_ivs(pokemon_id: int, ivs: dict) -> None:
    p = await pool()
    await p.execute("UPDATE pokemon_owned SET ivs=$1 WHERE id=$2", json.dumps(ivs), pokemon_id)


async def set_pokemon_evs(pokemon_id: int, evs: dict) -> None:
    p = await pool()
    await p.execute("UPDATE pokemon_owned SET evs=$1 WHERE id=$2", json.dumps(evs), pokemon_id)


async def set_pokemon_ability(pokemon_id: int, ability: str) -> None:
    p = await pool()
    await p.execute("UPDATE pokemon_owned SET ability=$1 WHERE id=$2", ability, pokemon_id)


async def set_pokemon_xp_level(pokemon_id: int, xp: float, level: int) -> None:
    p = await pool()
    await p.execute("UPDATE pokemon_owned SET xp=$1, level=$2 WHERE id=$3", xp, level, pokemon_id)


async def evolve_pokemon_row(
    pokemon_id: int, to_species_id: int, moves: list[dict], current_hp: int | None, ability: str
) -> None:
    p = await pool()
    await p.execute(
        "UPDATE pokemon_owned SET species_id=$1, moves=$2, current_hp=$3, ability=$4 WHERE id=$5",
        to_species_id, json.dumps(moves), current_hp, ability, pokemon_id)


async def set_pokemon_battle_state(
    pokemon_id: int, current_hp: int, hurt_at: str | None, fainted_at: str | None, status: str | None
) -> None:
    p = await pool()
    await p.execute(
        "UPDATE pokemon_owned SET current_hp=$1, hurt_at=$2, fainted_at=$3, status_cond=$4 WHERE id=$5",
        current_hp, hurt_at, fainted_at, status, pokemon_id)


async def set_pokemon_fainted(pokemon_id: int, fainted_at: str) -> None:
    p = await pool()
    await p.execute("UPDATE pokemon_owned SET fainted_at=$1, current_hp=0 WHERE id=$2", fainted_at, pokemon_id)


async def set_pokemon_held_and_cosmetic(
    pokemon_id: int, cosmetic_species_id: int | None, cosmetic_form: str | None
) -> None:
    p = await pool()
    await p.execute(
        "UPDATE pokemon_owned SET held_item=NULL, cosmetic_species_id=$1, cosmetic_form=$2 WHERE id=$3",
        cosmetic_species_id, cosmetic_form, pokemon_id)


async def add_egg(
    owner_id: int, parent_a: int | None, parent_b: int | None, species_id: int, ivs: dict,
    form: str | None, moves: list[dict], created_at: str, hatch_at: str, ball_type: str,
    nature: str, ability: str, hatch_guild_id: int | None,
) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_eggs(owner_id, parent_a, parent_b, species_id, ivs, form, moves, "
        "created_at, hatch_at, ball_type, nature, ability, hatch_guild_id) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
        owner_id, parent_a, parent_b, species_id, json.dumps(ivs), form, json.dumps(moves),
        created_at, hatch_at, ball_type, nature, ability, hatch_guild_id)


async def get_due_eggs(now_iso: str) -> list[dict]:
    p = await pool()
    rows = await p.fetch("SELECT * FROM pokemon_eggs WHERE hatch_at <= $1", now_iso)
    return [dict(r) for r in rows]


async def delete_egg(egg_id: int) -> None:
    p = await pool()
    await p.execute("DELETE FROM pokemon_eggs WHERE id=$1", egg_id)


async def get_user_eggs(user_id: int) -> list[dict]:
    p = await pool()
    rows = await p.fetch("SELECT * FROM pokemon_eggs WHERE owner_id=$1 ORDER BY hatch_at", user_id)
    return [dict(r) for r in rows]


async def set_pokemon_hp_status(pokemon_id: int, current_hp: int, status: str | None) -> None:
    p = await pool()
    await p.execute(
        "UPDATE pokemon_owned SET current_hp=$1, status_cond=$2 WHERE id=$3", current_hp, status, pokemon_id)


async def revive_pokemon(pokemon_id: int, new_hp: int | None) -> None:
    p = await pool()
    await p.execute(
        "UPDATE pokemon_owned SET fainted_at=NULL, current_hp=$1, status_cond=NULL WHERE id=$2",
        new_hp, pokemon_id)


async def set_pokemon_original_trainer(pokemon_id: int, trainer_id: int) -> None:
    p = await pool()
    await p.execute("UPDATE pokemon_owned SET original_trainer_id=$1 WHERE id=$2", trainer_id, pokemon_id)


async def set_pokemon_current_hp(pokemon_id: int, current_hp: int) -> None:
    p = await pool()
    await p.execute("UPDATE pokemon_owned SET current_hp=$1 WHERE id=$2", current_hp, pokemon_id)


async def award_pokemon_xp(pokemon_id: int, amount: int) -> tuple[int, int]:
    """Adds `amount` XP to a stored Pokemon and updates its level if that
    crosses a threshold (`pokemon.commands.leveling`'s growth-rate curves).
    Returns `(old_level, new_level)` so the caller can log a level-up.
    Doesn't touch HP/moves on a level-up itself -- the caller (currently
    `pokemon.commands.battle_bridge`'s post-battle reward flow) is
    responsible for recalculating max HP and applying the difference, the
    same way the monolith's own `_award_xp_to_side._grant` did."""
    from pokemon.commands.leveling import level_from_xp
    from pokemon.dex.species import get_default_repository as get_species_repo

    p = await pool()
    row = await p.fetchrow("SELECT xp, level, species_id FROM pokemon_owned WHERE id=$1", pokemon_id)
    if row is None:
        return 0, 0
    species = get_species_repo().get(row["species_id"])
    growth_rate = species.growth_rate if species else "medium"
    new_xp = row["xp"] + amount
    old_level = row["level"]
    new_level = min(100, max(old_level, level_from_xp(new_xp, growth_rate)))
    await p.execute("UPDATE pokemon_owned SET xp=$1, level=$2 WHERE id=$3", new_xp, new_level, pokemon_id)
    return old_level, new_level


# ---------------------------------------------------------------------------
# Items / trainer flags / money
# ---------------------------------------------------------------------------

async def get_item_qties(user_id: int) -> dict[str, int]:
    p = await pool()
    rows = await p.fetch("SELECT item, qty FROM pokemon_items WHERE user_id=$1", user_id)
    return {row["item"]: row["qty"] for row in rows}


async def get_item_qty(user_id: int, item: str) -> int:
    p = await pool()
    row = await p.fetchrow("SELECT qty FROM pokemon_items WHERE user_id=$1 AND item=$2", user_id, item)
    return row["qty"] if row else 0


async def add_item(user_id: int, item: str, delta: int) -> None:
    qty = await get_item_qty(user_id, item) + delta
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_items(user_id, item, qty) VALUES($1,$2,$3) "
        "ON CONFLICT (user_id, item) DO UPDATE SET qty=excluded.qty", user_id, item, qty)


async def set_held_item(pokemon_id: int, item: str | None) -> None:
    p = await pool()
    await p.execute("UPDATE pokemon_owned SET held_item=$1 WHERE id=$2", item, pokemon_id)


async def get_contore_first_winner() -> int | None:
    p = await pool()
    row = await p.fetchrow("SELECT user_id FROM pokemon_trainer WHERE beaten_contore=1 LIMIT 1")
    return row["user_id"] if row else None


async def claim_contore_first_win(user_id: int) -> bool:
    p = await pool()
    await p.execute("INSERT INTO pokemon_trainer(user_id) VALUES($1) ON CONFLICT (user_id) DO NOTHING", user_id)
    status = await p.execute(
        "UPDATE pokemon_trainer SET beaten_contore=1 WHERE user_id=$1 AND beaten_contore=0", user_id)
    return affected(status) > 0


async def get_pokemon_trainer_flags(user_id: int) -> dict:
    p = await pool()
    row = await p.fetchrow(
        "SELECT has_legendary, has_mythical, has_traded_away, beaten_contore FROM pokemon_trainer WHERE user_id=$1",
        user_id)
    if not row:
        await p.execute("INSERT INTO pokemon_trainer(user_id) VALUES($1) ON CONFLICT DO NOTHING", user_id)
        return {"has_legendary": False, "has_mythical": False, "has_traded_away": False, "beaten_contore": False}
    return {
        "has_legendary": bool(row["has_legendary"]), "has_mythical": bool(row["has_mythical"]),
        "has_traded_away": bool(row["has_traded_away"]), "beaten_contore": bool(row["beaten_contore"]),
    }


async def set_trainer_flag(user_id: int, flag: str, value: bool) -> None:
    p = await pool()
    await p.execute(
        f"INSERT INTO pokemon_trainer(user_id, {flag}) VALUES($1,$2) "
        f"ON CONFLICT (user_id) DO UPDATE SET {flag}=excluded.{flag}", user_id, int(value))


async def get_pokedollars(user_id: int) -> int:
    p = await pool()
    row = await p.fetchrow("SELECT amount FROM pokedollars WHERE user_id=$1", user_id)
    return row["amount"] if row else 0


async def add_pokedollars(user_id: int, amount: int) -> int:
    """Adjusts a trainer's balance by `amount` (negative to spend) and
    returns the new balance. Clamped at 0 server-side so a race between two
    simultaneous spends can't drive a balance negative."""
    p = await pool()
    return await p.fetchval(
        "INSERT INTO pokedollars(user_id, amount) VALUES($1, GREATEST(0, $2)) "
        "ON CONFLICT (user_id) DO UPDATE SET amount = GREATEST(0, pokedollars.amount + $2) "
        "RETURNING amount", user_id, amount)


async def has_caught_legendary_species(user_id: int, species_id: int) -> bool:
    p = await pool()
    row = await p.fetchrow(
        "SELECT 1 FROM legendary_catches WHERE user_id=$1 AND species_id=$2", user_id, species_id)
    return bool(row)


async def record_legendary_catch(user_id: int, species_id: int) -> None:
    p = await pool()
    await p.execute("INSERT INTO legendary_catches VALUES($1,$2) ON CONFLICT DO NOTHING", user_id, species_id)


async def set_pokemon_friendship(pokemon_id: int, friendship: int) -> None:
    p = await pool()
    await p.execute("UPDATE pokemon_owned SET friendship=$1 WHERE id=$2", friendship, pokemon_id)


async def adjust_friendship(pokemon_id: int, delta: int) -> None:
    p = await pool()
    await p.execute(
        "UPDATE pokemon_owned SET friendship = GREATEST(0, LEAST(255, friendship + $1)) WHERE id=$2",
        delta, pokemon_id)


async def set_starter(user_id: int, species_id: int, chosen_at: str) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_starters(user_id, species_id, chosen_at) VALUES($1,$2,$3) "
        "ON CONFLICT (user_id) DO NOTHING", user_id, species_id, chosen_at)


async def has_starter(user_id: int) -> bool:
    p = await pool()
    row = await p.fetchrow("SELECT 1 FROM pokemon_starters WHERE user_id=$1", user_id)
    return bool(row)


async def delete_starter(user_id: int) -> bool:
    p = await pool()
    status = await p.execute("DELETE FROM pokemon_starters WHERE user_id=$1", user_id)
    return affected(status) > 0


async def has_item(user_id: int, item_name: str, *, exclude_pokemon_id: int | None = None) -> bool:
    """True if the trainer has `item_name` either in their bag or held by
    one of their Pokémon (optionally excluding one, e.g. "any *other*
    Pokémon of theirs holding a Mega Stone" checks)."""
    p = await pool()
    bag_qty = await get_item_qty(user_id, item_name)
    if exclude_pokemon_id is not None:
        row = await p.fetchrow(
            "SELECT COUNT(*) AS c FROM pokemon_owned WHERE owner_id=$1 AND held_item=$2 AND id!=$3",
            user_id, item_name, exclude_pokemon_id)
    else:
        row = await p.fetchrow(
            "SELECT COUNT(*) AS c FROM pokemon_owned WHERE owner_id=$1 AND held_item=$2", user_id, item_name)
    return bag_qty + (row["c"] if row else 0) > 0


async def get_dex_milestones(user_id: int) -> set[int]:
    p = await pool()
    row = await p.fetchrow("SELECT dex_milestones FROM pokemon_trainer WHERE user_id=$1", user_id)
    return set(json.loads(row["dex_milestones"])) if row and row["dex_milestones"] else set()


async def set_dex_milestones(user_id: int, milestones: set[int]) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_trainer(user_id, dex_milestones) VALUES($1,$2) "
        "ON CONFLICT (user_id) DO UPDATE SET dex_milestones=excluded.dex_milestones",
        user_id, json.dumps(list(milestones)))


async def get_notify_prefs(user_id: int) -> dict:
    p = await pool()
    row = await p.fetchrow("SELECT notify_hunt, notify_breed FROM pokemon_trainer WHERE user_id=$1", user_id)
    return {"notify_hunt": bool(row["notify_hunt"]), "notify_breed": bool(row["notify_breed"])} \
        if row else {"notify_hunt": False, "notify_breed": False}


async def set_notify_pref(user_id: int, field: str, value: bool) -> None:
    p = await pool()
    await p.execute(
        f"INSERT INTO pokemon_trainer(user_id, {field}) VALUES($1,$2) "
        f"ON CONFLICT (user_id) DO UPDATE SET {field}=excluded.{field}", user_id, int(value))


async def fuse_partner(kyurem_id: int, partner_id: int) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_fused VALUES($1,$2) ON CONFLICT (kyurem_id) DO UPDATE SET partner_id=excluded.partner_id",
        kyurem_id, partner_id)


async def unfuse_partner(kyurem_id: int) -> int | None:
    p = await pool()
    row = await p.fetchrow("SELECT partner_id FROM pokemon_fused WHERE kyurem_id=$1", kyurem_id)
    if row:
        await p.execute("DELETE FROM pokemon_fused WHERE kyurem_id=$1", kyurem_id)
        return row["partner_id"]
    return None


async def set_hurt_at(pokemon_id: int, hurt_at: datetime | None) -> None:
    p = await pool()
    await p.execute(
        "UPDATE pokemon_owned SET hurt_at=$1 WHERE id=$2",
        hurt_at.strftime("%Y-%m-%d %H:%M:%S.%f") if hurt_at else None, pokemon_id)


async def set_pp_reset(pokemon_id: int, reset_at: datetime | None) -> None:
    p = await pool()
    await p.execute(
        "UPDATE pokemon_owned SET pp_reset_at=$1 WHERE id=$2",
        reset_at.strftime("%Y-%m-%d %H:%M:%S.%f") if reset_at else None, pokemon_id)


async def set_cosmetic_appearance(
    pokemon_id: int, owner_id: int, cosmetic_species_id: int | None, cosmetic_form: str | None
) -> bool:
    p = await pool()
    status = await p.execute(
        "UPDATE pokemon_owned SET cosmetic_species_id=$1, cosmetic_form=$2 WHERE id=$3 AND owner_id=$4",
        cosmetic_species_id, cosmetic_form, pokemon_id, owner_id)
    return affected(status) > 0


# ---------------------------------------------------------------------------
# Teams / presets
# ---------------------------------------------------------------------------

async def get_team_slots(user_id: int) -> list[int]:
    p = await pool()
    rows = await p.fetch("SELECT pokemon_id FROM pokemon_team_members WHERE user_id=$1 ORDER BY slot", user_id)
    return [r["pokemon_id"] for r in rows]


async def save_team_slots(user_id: int, slots: list[int]) -> None:
    p = await pool()
    async with p.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM pokemon_team_members WHERE user_id=$1", user_id)
        if slots:
            await conn.executemany(
                "INSERT INTO pokemon_team_members(user_id, pokemon_id, slot) VALUES($1,$2,$3)",
                [(user_id, pid, i) for i, pid in enumerate(slots)])


async def remove_from_team(user_id: int, pokemon_id: int) -> bool:
    p = await pool()
    status = await p.execute(
        "DELETE FROM pokemon_team_members WHERE user_id=$1 AND pokemon_id=$2", user_id, pokemon_id)
    if affected(status) <= 0:
        return False
    await mark_left_team(pokemon_id)
    return True


async def get_hurt_team_pokemon(cutoff_iso: str) -> list[dict]:
    p = await pool()
    rows = await p.fetch(
        "SELECT DISTINCT po.id, po.level, po.species_id, po.hurt_at FROM pokemon_owned po "
        "JOIN pokemon_team_members tm ON tm.pokemon_id = po.id "
        "WHERE po.hurt_at IS NOT NULL AND po.hurt_at <= $1 AND po.fainted_at IS NULL", cutoff_iso)
    return [dict(r) for r in rows]


async def get_pp_needing_team_pokemon(cutoff_iso: str) -> list[dict]:
    p = await pool()
    rows = await p.fetch(
        "SELECT DISTINCT po.id, po.level, po.species_id, po.moves, po.pp_reset_at FROM pokemon_owned po "
        "JOIN pokemon_team_members tm ON tm.pokemon_id = po.id "
        "WHERE po.moves != '[]' AND (po.pp_reset_at IS NULL OR po.pp_reset_at <= $1)", cutoff_iso)
    return [dict(r) for r in rows]


async def get_fainted_team_pokemon(cutoff_iso: str) -> list[dict]:
    p = await pool()
    rows = await p.fetch(
        "SELECT DISTINCT po.id, po.level, po.species_id, po.evs, po.ivs, po.fainted_at FROM pokemon_owned po "
        "JOIN pokemon_team_members tm ON tm.pokemon_id = po.id "
        "WHERE po.fainted_at IS NOT NULL AND po.fainted_at <= $1", cutoff_iso)
    return [dict(r) for r in rows]


async def bulk_heal(pokemon_ids: list[int]) -> None:
    if not pokemon_ids:
        return
    p = await pool()
    await p.execute(
        "UPDATE pokemon_owned SET current_hp=NULL, status_cond=NULL, fainted_at=NULL, hurt_at=NULL "
        "WHERE id = ANY($1::bigint[])", pokemon_ids)


async def bulk_reset_pp(rows: list[tuple[int, str]]) -> None:
    if not rows:
        return
    p = await pool()
    now_iso = datetime.now().isoformat()
    async with p.acquire() as conn, conn.transaction():
        await conn.executemany(
            "UPDATE pokemon_owned SET moves=$1, pp_reset_at=$2 WHERE id=$3",
            [(moves_json, now_iso, pid) for pid, moves_json in rows])


async def bulk_revive(rows: list[tuple[int, int]]) -> None:
    if not rows:
        return
    p = await pool()
    now_iso = datetime.now().isoformat()
    async with p.acquire() as conn, conn.transaction():
        await conn.executemany(
            "UPDATE pokemon_owned SET fainted_at=NULL, status_cond=NULL, current_hp=$1, hurt_at=$2 WHERE id=$3",
            [(hp, now_iso, pid) for pid, hp in rows])


async def save_team_preset(user_id: int, name: str, slots: list[int]) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_team_presets(user_id, name, slots) VALUES($1,$2,$3) "
        "ON CONFLICT (user_id, name) DO UPDATE SET slots=excluded.slots", user_id, name, json.dumps(slots))


async def get_team_presets(user_id: int) -> dict[str, list[int]]:
    p = await pool()
    rows = await p.fetch("SELECT name, slots FROM pokemon_team_presets WHERE user_id=$1 ORDER BY name", user_id)
    return {r["name"]: json.loads(r["slots"]) for r in rows}


async def get_team_preset(user_id: int, name: str) -> list[int] | None:
    p = await pool()
    row = await p.fetchrow(
        "SELECT slots FROM pokemon_team_presets WHERE user_id=$1 AND name=$2", user_id, name)
    return json.loads(row["slots"]) if row else None


async def delete_team_preset(user_id: int, name: str) -> bool:
    p = await pool()
    status = await p.execute("DELETE FROM pokemon_team_presets WHERE user_id=$1 AND name=$2", user_id, name)
    return affected(status) > 0


# ---------------------------------------------------------------------------
# Friendship memory
# ---------------------------------------------------------------------------
# Lets a Pokémon's friendship "come back" if it's re-caught/re-traded to a
# trainer who owned it before, within a window -- see `_FRIENDSHIP_RESTORE_WEEKS`.

async def save_friendship_memory(pokemon_id: int, trainer_id: int, friendship: int) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_friendship_memory VALUES($1,$2,$3,$4) "
        "ON CONFLICT (pokemon_id, trainer_id) DO UPDATE SET friendship=excluded.friendship, stored_at=excluded.stored_at",
        pokemon_id, trainer_id, friendship, datetime.now().isoformat())


async def get_friendship_memory(pokemon_id: int, trainer_id: int) -> int | None:
    p = await pool()
    row = await p.fetchrow(
        "SELECT friendship, stored_at FROM pokemon_friendship_memory WHERE pokemon_id=$1 AND trainer_id=$2",
        pokemon_id, trainer_id)
    if not row:
        return None
    cutoff = datetime.now() - timedelta(weeks=_FRIENDSHIP_RESTORE_WEEKS)
    return row["friendship"] if datetime.fromisoformat(row["stored_at"]) > cutoff else None


async def clear_friendship_memory(pokemon_id: int, trainer_id: int) -> None:
    p = await pool()
    await p.execute(
        "DELETE FROM pokemon_friendship_memory WHERE pokemon_id=$1 AND trainer_id=$2", pokemon_id, trainer_id)


# ---------------------------------------------------------------------------
# GTS
# ---------------------------------------------------------------------------

def listing_conditions(listing: dict) -> list[dict]:
    """Deserializes a `pokemon_gts_listings.want_conditions` cell, tolerant
    of missing/malformed data (matches the monolith's `_gtslisting_conditions`)."""
    raw = listing.get("want_conditions")
    if not raw:
        return []
    try:
        conditions = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return conditions or []


async def create_gts_listing(lister_id: int, pokemon_id: int, want_conditions: list[dict]) -> int:
    p = await pool()
    return await p.fetchval(
        "INSERT INTO pokemon_gts_listings(lister_id, pokemon_id, want_conditions, status, created_at) "
        "VALUES($1,$2,$3, 'active', $4) RETURNING id",
        lister_id, pokemon_id, json.dumps(want_conditions), datetime.now().isoformat())


async def add_gts_condition(listing_id: int, condition: dict) -> bool:
    listing = await get_gts_listing(listing_id)
    if not listing or listing["status"] != "active":
        return False
    conditions = listing_conditions(listing)
    conditions.append(condition)
    p = await pool()
    await p.execute(
        "UPDATE pokemon_gts_listings SET want_conditions=$1 WHERE id=$2", json.dumps(conditions), listing_id)
    return True


async def set_gts_conditions(listing_id: int, conditions: list[dict]) -> None:
    p = await pool()
    await p.execute(
        "UPDATE pokemon_gts_listings SET want_conditions=$1 WHERE id=$2", json.dumps(conditions), listing_id)


async def get_gts_listing(listing_id: int) -> dict | None:
    p = await pool()
    row = await p.fetchrow("SELECT * FROM pokemon_gts_listings WHERE id=$1", listing_id)
    return dict(row) if row else None


async def get_gts_listings_by_lister(user_id: int) -> list[dict]:
    p = await pool()
    rows = await p.fetch("SELECT * FROM pokemon_gts_listings WHERE lister_id=$1", user_id)
    return [dict(r) for r in rows]


async def get_gts_listings_fulfilled_by(user_id: int) -> list[dict]:
    p = await pool()
    rows = await p.fetch("SELECT * FROM pokemon_gts_listings WHERE fulfilled_by=$1", user_id)
    return [dict(r) for r in rows]


async def get_active_gts_listings_page(
    *, limit: int = 25, before_created_at: str | None = None,
    species_id: int | None = None, nature: str | None = None,
    ability: str | None = None, gender: str | None = None,
    shiny: bool | None = None, exclude_lister_id: int | None = None,
) -> list[dict]:
    conditions = ["gl.status = 'active'"]
    params: list = []

    def _p(v):
        params.append(v)
        return f"${len(params)}"

    if before_created_at:
        conditions.append(f"gl.created_at < {_p(before_created_at)}")
    if exclude_lister_id is not None:
        conditions.append(f"gl.lister_id != {_p(exclude_lister_id)}")
    if species_id is not None:
        conditions.append(f"po.species_id = {_p(species_id)}")
    if nature:
        conditions.append(f"po.nature = {_p(nature)}")
    if ability:
        conditions.append(f"po.ability = {_p(ability)}")
    if gender:
        conditions.append(f"po.gender = {_p(gender)}")
    if shiny is not None:
        conditions.append(f"po.shiny = {_p(int(shiny))}")

    where = " AND ".join(conditions)
    query = (
        "SELECT gl.* FROM pokemon_gts_listings gl "
        "JOIN pokemon_owned po ON po.id = gl.pokemon_id "
        f"WHERE {where} ORDER BY gl.created_at DESC LIMIT {_p(limit)}"
    )
    p = await pool()
    rows = await p.fetch(query, *params)
    return [dict(r) for r in rows]


async def get_pokemon_batch(pokemon_ids: list[int]) -> dict[int, dict]:
    """One round trip for a whole page of listings instead of N separate
    `get_pokemon()` calls per listing (fixes an N+1 pattern in the
    monolith's `gts_browse`)."""
    if not pokemon_ids:
        return {}
    p = await pool()
    rows = await p.fetch("SELECT * FROM pokemon_owned WHERE id = ANY($1::bigint[])", list(pokemon_ids))
    return {r["id"]: dict(r) for r in rows}


async def get_user_gts_listings(user_id: int) -> list[dict]:
    p = await pool()
    rows = await p.fetch(
        "SELECT * FROM pokemon_gts_listings WHERE lister_id=$1 AND status='active' ORDER BY created_at DESC",
        user_id)
    return [dict(r) for r in rows]


async def close_gts_listing(listing_id: int, status: str, fulfilled_by: int | None = None) -> None:
    p = await pool()
    await p.execute(
        "UPDATE pokemon_gts_listings SET status=$1, fulfilled_by=$2, fulfilled_at=$3 WHERE id=$4",
        status, fulfilled_by, datetime.now().isoformat() if fulfilled_by else None, listing_id)


# ---------------------------------------------------------------------------
# Cooldowns (hunt / breed)
# ---------------------------------------------------------------------------

async def get_hunt_cooldown(user_id: int) -> float | None:
    p = await pool()
    row = await p.fetchrow("SELECT ready_at FROM pokemon_hunt_cooldowns WHERE user_id=$1", user_id)
    return datetime.fromisoformat(row["ready_at"]).timestamp() if row else None


async def set_hunt_cooldown(user_id: int, ready_at_ts: float) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_hunt_cooldowns(user_id, ready_at) VALUES($1,$2) "
        "ON CONFLICT (user_id) DO UPDATE SET ready_at=excluded.ready_at",
        user_id, datetime.fromtimestamp(ready_at_ts).isoformat())


async def get_all_hunt_cooldowns() -> list[tuple[int, float]]:
    p = await pool()
    rows = await p.fetch("SELECT user_id, ready_at FROM pokemon_hunt_cooldowns")
    return [(r["user_id"], datetime.fromisoformat(r["ready_at"]).timestamp()) for r in rows]


async def get_breed_cooldown(pokemon_id: int) -> float | None:
    p = await pool()
    row = await p.fetchrow("SELECT ready_at FROM pokemon_breed_cooldowns WHERE pokemon_id=$1", pokemon_id)
    return datetime.fromisoformat(row["ready_at"]).timestamp() if row else None


async def set_breed_cooldown(pokemon_id: int, ready_at_ts: float) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_breed_cooldowns(pokemon_id, ready_at) VALUES($1,$2) "
        "ON CONFLICT (pokemon_id) DO UPDATE SET ready_at=excluded.ready_at",
        pokemon_id, datetime.fromtimestamp(ready_at_ts).isoformat())


async def get_all_breed_cooldowns() -> list[tuple[int, int, float]]:
    p = await pool()
    rows = await p.fetch(
        "SELECT pc.pokemon_id, po.owner_id, pc.ready_at "
        "FROM pokemon_breed_cooldowns pc JOIN pokemon_owned po ON po.id = pc.pokemon_id")
    return [(r["pokemon_id"], r["owner_id"], datetime.fromisoformat(r["ready_at"]).timestamp()) for r in rows]


# ---------------------------------------------------------------------------
# Boss battle configs -- a generalized version of the monolith's hardcoded
# "Contore" NPC team. See `pokemon.commands.boss` for the full feature.
# ---------------------------------------------------------------------------

async def get_boss_config(user_id: int) -> dict | None:
    p = await pool()
    row = await p.fetchrow("SELECT * FROM pokemon_boss_configs WHERE user_id=$1", user_id)
    return dict(row) if row else None


async def get_all_boss_user_ids() -> list[int]:
    p = await pool()
    rows = await p.fetch("SELECT user_id FROM pokemon_boss_configs")
    return [r["user_id"] for r in rows]


async def upsert_boss_config(
    user_id: int, *, display_name: str, accept_label: str, decline_label: str, challenge_line: str,
    configured_by: int,
) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_boss_configs"
        "(user_id, display_name, accept_label, decline_label, challenge_line, configured_by, configured_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (user_id) DO UPDATE SET "
        "display_name=excluded.display_name, accept_label=excluded.accept_label, "
        "decline_label=excluded.decline_label, challenge_line=excluded.challenge_line",
        user_id, display_name, accept_label, decline_label, challenge_line, configured_by,
        datetime.now().isoformat())


async def delete_boss_config(user_id: int) -> bool:
    p = await pool()
    status = await p.execute("DELETE FROM pokemon_boss_configs WHERE user_id=$1", user_id)
    return affected(status) > 0


async def get_boss_team_slots(user_id: int) -> list[dict]:
    p = await pool()
    rows = await p.fetch(
        "SELECT * FROM pokemon_boss_team_slots WHERE boss_user_id=$1 ORDER BY slot", user_id)
    return [dict(r) for r in rows]


async def add_boss_team_slot(
    user_id: int, *, species_id: int, level: int, nickname: str | None, gender: str, moves: list[dict],
    held_item: str | None, nature: str, ability: str | None, evs: dict, ivs: dict,
) -> int:
    p = await pool()
    next_slot = await p.fetchval(
        "SELECT COALESCE(MAX(slot), -1) + 1 FROM pokemon_boss_team_slots WHERE boss_user_id=$1", user_id)
    if next_slot >= 6:
        raise ValueError("A boss team can only have 6 Pokémon.")
    await p.execute(
        "INSERT INTO pokemon_boss_team_slots"
        "(boss_user_id, slot, species_id, level, nickname, gender, moves, held_item, nature, ability, evs, ivs) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
        user_id, next_slot, species_id, level, nickname, gender, json.dumps(moves), held_item, nature, ability,
        json.dumps(evs), json.dumps(ivs))
    return next_slot


async def remove_boss_team_slot(user_id: int, slot: int) -> bool:
    p = await pool()
    async with p.acquire() as conn, conn.transaction():
        status = await conn.execute(
            "DELETE FROM pokemon_boss_team_slots WHERE boss_user_id=$1 AND slot=$2", user_id, slot)
        if affected(status) <= 0:
            return False
        # Close the gap so slots stay contiguous (0..n-1) for the next add.
        remaining = await conn.fetch(
            "SELECT slot FROM pokemon_boss_team_slots WHERE boss_user_id=$1 ORDER BY slot", user_id)
        for new_slot, row in enumerate(remaining):
            if row["slot"] != new_slot:
                await conn.execute(
                    "UPDATE pokemon_boss_team_slots SET slot=$1 WHERE boss_user_id=$2 AND slot=$3",
                    new_slot, user_id, row["slot"])
    return True


async def clear_boss_team(user_id: int) -> None:
    p = await pool()
    await p.execute("DELETE FROM pokemon_boss_team_slots WHERE boss_user_id=$1", user_id)


# ---------------------------------------------------------------------------
# /export integration (cogs.export.EXPORT_PROVIDERS["pokemon"] -- see
# pokemon/__init__.py, which registers `pokemon_export` below there)
# ---------------------------------------------------------------------------

async def pokemon_export(user_id: int) -> dict:
    """Every piece of Pokémon data Contore stores about `user_id`, in the
    same shape (and under the same `"pokemon"` top-level key) as the
    monolith's own `export_data`'s inline Pokémon section -- see
    `cogs/export.py`'s module docstring for why this is a separate,
    pluggable function rather than inlined there directly.

    Omits the monolith's `hunt_cooldown_ready_at`/`breed_cooldowns_ready_at`
    (cooldown-clock fields, not stored data -- `get_hunt_cooldown`/
    `get_breed_cooldown`'s return shapes don't map onto them directly and
    it wasn't worth guessing at the conversion for what a GDPR-style data
    export cares about anyway)."""
    wins, losses = await get_duel_record(user_id)
    return {
        "owned": await get_user_pokemon(user_id),
        "eggs": await get_user_eggs(user_id),
        "items": await get_item_qties(user_id),
        "pokedollars": await get_pokedollars(user_id),
        "pokedex_seen": sorted(await get_pokedex_seen(user_id)),
        "pokedex_milestones": sorted(await get_dex_milestones(user_id)),
        "team_slots": await get_team_slots(user_id),
        "team_presets": await get_team_presets(user_id),
        "trainer_flags": await get_pokemon_trainer_flags(user_id),
        "duel_record": {"wins": wins, "losses": losses},
        "notify_preferences": await get_notify_prefs(user_id),
        "gts_listings_created": await get_gts_listings_by_lister(user_id),
        "gts_listings_fulfilled": await get_gts_listings_fulfilled_by(user_id),
    }


# ---------------------------------------------------------------------------
# Achievement integration (cogs.achievements.ACHIEVEMENT_PRESETS["Pokémon
# Pack"] -- see pokemon/__init__.py for where that preset is registered)
# ---------------------------------------------------------------------------

async def sync_achievement_progress(bot, guild_id: int, user_id: int) -> None:
    """Recomputes every Pokémon-side metric `ACHIEVEMENT_PRESETS["Pokémon
    Pack"]`'s "custom"-type triggers check against, and writes each one to
    `core.db`'s generic per-guild progress store (`db_set_progress`) --
    `cogs.achievements.evaluate_condition_tree`'s "custom" branch reads
    from exactly that store, not from anything passed in at check time, so
    this has to run and land in the DB before
    `achievements.check_automated_achievements` is called or a
    newly-eligible achievement won't be seen. Call this (then that) right
    after whatever changed one of these metrics -- currently wired at
    catch time (`pokemon.commands.catching`) and battle end
    (`pokemon.commands.battle_bridge.persist_battle_result`).

    `eggs_hatched` is a known gap: no column/counter for it exists yet
    (`pokemon_trainer` has `duel_wins`/`duel_losses` but nothing
    egg-related) -- always reports 0 until one is added. Every other
    metric is a real, live count, not a placeholder.
    """
    try:
        from cogs import achievements
    except ImportError:
        return  # partial checkout without cogs/ -- nothing to check against

    p = await pool()
    owned = await p.fetch(
        "SELECT species_id, shiny, original_trainer_id FROM pokemon_owned WHERE owner_id=$1", user_id)
    caught = len(owned)
    shinies = sum(1 for r in owned if r["shiny"])

    from pokemon.dex.species import get_default_repository as get_species_repo
    species_repo = get_species_repo()
    legendaries = mythicals = 0
    for r in owned:
        sp = species_repo.get(r["species_id"])
        if sp and sp.rarity == "legendary":
            legendaries += 1
        elif sp and sp.rarity == "mythical":
            mythicals += 1

    seen_count = len(await get_pokedex_seen(user_id))
    trainer_row = await p.fetchrow("SELECT duel_wins FROM pokemon_trainer WHERE user_id=$1", user_id)
    battles_won = trainer_row["duel_wins"] if trainer_row else 0

    progress = {
        "pokemon_caught": caught, "shinies_caught": shinies, "legendaries_caught": legendaries,
        "mythicals_caught": mythicals, "pokedex_seen_count": seen_count, "eggs_hatched": 0,
        "battles_won": battles_won,
    }
    from core.db import db_set_progress
    for metric, value in progress.items():
        await db_set_progress(guild_id, user_id, metric, value)
    await achievements.check_automated_achievements(bot, guild_id, user_id)


async def record_duel_win(user_id: int) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_trainer(user_id, duel_wins) VALUES($1, 1) "
        "ON CONFLICT (user_id) DO UPDATE SET duel_wins = pokemon_trainer.duel_wins + 1", user_id)


async def record_duel_loss(user_id: int) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO pokemon_trainer(user_id, duel_losses) VALUES($1, 1) "
        "ON CONFLICT (user_id) DO UPDATE SET duel_losses = pokemon_trainer.duel_losses + 1", user_id)

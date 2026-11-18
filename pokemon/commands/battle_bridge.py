"""Builds battle-engine `Battler`s (and the `BattleEngine`/`BattleView`
pair around them) from the things `pokemon.commands` already has: an
owned-Pokemon DB row, a `WildEncounter` roll, or a wild boss encounter.

This is the integration point `hunt.py`'s module docstring calls out as
missing ("nothing yet constructs a `BattleEngine` for a fresh ... battle").
It lives in `pokemon.commands` rather than `pokemon.ui.battle_view` even
though `hunt.py`/`duel.py`/`boss.py` reach it THROUGH
`pokemon.ui.battle_view` (see `start_wild_encounter` there) because
everything in here does DB-shaped/species-repo data access, which
`battle_view.py`'s own docstring explicitly says it shouldn't do (rendering
and input collection only). `battle_view.py`'s entry points import from
here rather than duplicating this logic, which is also why it exists as
its own module instead of living directly in `hunt.py`: `duel.py`/`boss.py`
need the exact same owned-Pokemon-row -> Battler conversion `hunt.py`'s
side of a wild encounter already needs.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from pokemon.battle.battler import Battler, Boosts, Identity, Moveset, MoveSlot, Vitals
from pokemon.battle.field import Field
from pokemon.commands import moveset as moveset_mod
from pokemon.commands import spawning
from pokemon.commands.repository import parse_ivs
from pokemon.commands.stats import pokemon_max_hp
from pokemon.dex.species import Species, get_default_repository as get_species_repo

if TYPE_CHECKING:
    from pokemon.commands.spawning import WildEncounter
    from pokemon.generations.ruleset import GenerationRuleset


def _moveslots_from_stored(raw_moves: list[dict] | str | None) -> list[MoveSlot]:
    if isinstance(raw_moves, str):
        raw_moves = json.loads(raw_moves) if raw_moves else []
    return [MoveSlot(move_slug=m["name"], pp=m["pp"], max_pp=m["max_pp"]) for m in (raw_moves or [])]


def _moveslots_from_rolled(rolled: list[moveset_mod.MoveSlot]) -> list[MoveSlot]:
    return [MoveSlot(move_slug=m.name, pp=m.pp, max_pp=m.max_pp) for m in rolled]


def battler_from_owned(battler_id: str, p: dict, species: Species | None = None) -> Battler:
    """One of a trainer's own Pokemon, straight from a `pokemon_owned` row
    (see `pokemon.commands.repository`/`_db.py`'s schema) -- the shape
    `get_battle_team`/`repository.get_user_pokemon` already return.
    `current_hp` NULL means "never been damaged", i.e. full HP, exactly
    like every other display path (`pokemon_max_hp`) already assumes."""
    species = species or get_species_repo().get(p["species_id"])
    ivs = parse_ivs(p.get("ivs"))
    evs = p.get("evs")
    evs = json.loads(evs) if isinstance(evs, str) else (evs or {})
    nature = p.get("nature", "Hardy")
    level = p["level"]
    base_stats = (species.form(p.get("form")).base_stats if p.get("form") and species.form(p.get("form"))
                  else species.base_stats)
    max_hp = pokemon_max_hp(p, species)
    current_hp = p.get("current_hp")
    current_hp = max_hp if current_hp is None else current_hp

    identity = Identity(
        species_id=species.id, species_name=species.name, types=list(species.types),
        base_stats=dict(base_stats), ability_slug=p.get("ability") or "", held_item=p.get("held_item") or "",
        gender=p.get("gender") or "genderless", nickname=p.get("nickname"), level=level, nature=nature,
        ivs=ivs, evs=evs, weight_kg=species.weight_kg, friendship=p.get("friendship", 70),
        can_evolve=bool(species.evolves_to))
    vitals = Vitals(hp=current_hp, max_hp=max_hp, status=p.get("status_cond"))
    return Battler(battler_id, identity, vitals, Boosts(), Moveset(_moveslots_from_stored(p.get("moves"))))


def battler_from_wild_encounter(battler_id: str, encounter: "WildEncounter") -> Battler:
    """The opposing side of a `/pokemon hunt` (or a passive spawn's
    "Fight" option, once that's wired the same way). Wild Pokemon roll a
    fresh moveset/ability the same way a newly-caught one does
    (`roll_initial_moveset`/`pick_ability`) rather than storing one, since
    they don't persist past the encounter unless caught."""
    species = encounter.species
    form = encounter.form()
    base_stats = form.base_stats if form else species.base_stats
    types = list(form.types) if form else list(species.types)
    ability = spawning.pick_ability(species)
    max_hp = int((2 * base_stats.get("hp", 1) + encounter.ivs.get("hp", 0)) * encounter.level / 100) + encounter.level + 10
    if species.id == 292:  # Shedinja -- see pokemon.commands.stats.pokemon_max_hp
        max_hp = 1

    identity = Identity(
        species_id=species.id, species_name=species.name, types=types, base_stats=dict(base_stats),
        ability_slug=ability, held_item=encounter.held_item or "", gender=encounter.gender,
        nickname=None, level=encounter.level, nature=encounter.nature, ivs=dict(encounter.ivs),
        evs={}, weight_kg=species.weight_kg, friendship=70, can_evolve=bool(species.evolves_to))
    vitals = Vitals(hp=max_hp, max_hp=max_hp)
    rolled = moveset_mod.roll_initial_moveset(species.id, encounter.level)
    return Battler(battler_id, identity, vitals, Boosts(), Moveset(_moveslots_from_rolled(rolled)))


def build_wild_battle_engine(bot, user, team: list[dict], encounter: "WildEncounter", ruleset: "GenerationRuleset"):
    """Assembles a ready-to-play `(engine, my_side, opp_side)` for a wild
    encounter: the trainer's current lead active Pokemon (first non-fainted
    team member) against a freshly-built wild Battler. Doubles / full-team
    benching aren't wired here yet -- a wild encounter is always 1v1 with
    the rest of `team` sitting out, matching the original monolith's own
    wild-battle behaviour."""
    from pokemon.battle.engine import BattleEngine

    field = Field("trainer", "wild", side_a_trainer=user.id, side_b_trainer=None)
    usable = [p for p in team if not p["fainted_at"]]
    for i, p in enumerate(usable):
        b = battler_from_owned(f"trainer:{p['id']}", p)
        if i == 0:
            field.side_a.place(0, b)
        else:
            field.side_a.bench.append(b)
        field.side_a.roster.append(b)

    wild = battler_from_wild_encounter("wild:0", encounter)
    field.side_b.place(0, wild)
    field.side_b.roster.append(wild)
    field.side_b.wild_encounter = encounter  # makes this side catchable (battle/bag.py PokeBall)

    engine = BattleEngine(
        field, abilities=bot.pokemon_registries.abilities, moves=bot.pokemon_registries.moves,
        items=bot.pokemon_registries.items, ruleset=ruleset)
    return engine, field.side_a, field.side_b


def _populate_pvp_side(side, rosters: dict[int, list[dict]], user_ids: list[int], active_per_user: int) -> None:
    """Fills one PvP `Side` from one or two trainers' rosters.

    - Singles / Doubles (one trainer per side): `user_ids` has one id,
      `active_per_user` is 1 or 2 -- that many of the trainer's own
      non-fainted Pokemon lead, the rest of their team benched.
    - Doubles 2v2 (two trainers sharing one side): `user_ids` has two ids,
      `active_per_user=1` -- each partner's own lead takes one of the two
      active slots, and BOTH partners' remaining Pokemon share one bench.

    Known simplification, matching this battle engine's current Doubles
    support in general (see `pokemon.battle.MIGRATION_STATUS.md`): a
    shared 2v2 bench doesn't enforce that a partner can only switch in
    their OWN spare Pokemon, since `Side`/`Battler` have no per-battler
    owner distinct from the Side's single `trainer_id`. Each Battler still
    gets a duck-typed `.owner_id` set below for any UI that wants to show
    whose Pokemon is whose; nothing in the engine currently reads it.
    """
    next_slot = 0
    for uid in user_ids:
        usable = [p for p in rosters[uid] if not p["fainted_at"]]
        for i, p in enumerate(usable):
            b = battler_from_owned(f"trainer:{p['id']}", p)
            b.owner_id = uid
            if i < active_per_user:
                side.place(next_slot, b)
                next_slot += 1
            else:
                side.bench.append(b)
            side.roster.append(b)


def build_pvp_battle_engine(
    bot, rosters: dict[int, list[dict]], side_a_ids: list[int], side_b_ids: list[int],
    ruleset: "GenerationRuleset", *, format: str,
):
    """Assembles a ready-to-play `(engine, side_a, side_b)` for a PvP duel
    -- `format` is one of the same `Literal["Singles", "Doubles",
    "Doubles 2v2"]` values `pokemon.commands.duel.pokemon_duel` already
    validates rosters against, so no further legality checking happens
    here (that already happened before the offer/accept view even ran)."""
    from pokemon.battle.engine import BattleEngine

    active_per_user = 2 if format == "Doubles" else 1
    field = Field("side_a", "side_b", side_a_trainer=side_a_ids[0], side_b_trainer=side_b_ids[0])
    _populate_pvp_side(field.side_a, rosters, side_a_ids, active_per_user)
    _populate_pvp_side(field.side_b, rosters, side_b_ids, active_per_user)

    engine = BattleEngine(
        field, abilities=bot.pokemon_registries.abilities, moves=bot.pokemon_registries.moves,
        items=bot.pokemon_registries.items, ruleset=ruleset)
    return engine, field.side_a, field.side_b


def build_boss_battle_engine(
    bot, rosters: dict[int, list[dict]], challenger_ids: list[int], boss_user_id: int,
    boss_team: list[dict], ruleset: "GenerationRuleset", *, format: str,
):
    """Assembles a ready-to-play `(engine, challenger_side, boss_side)` for
    a boss battle -- the challenger side is built exactly like a PvP side
    (one or two human trainers, per `format`), the boss's side from its own
    configured `boss_team` (same row shape as an owned-Pokemon roster --
    see `pokemon.commands.boss`'s data model). Turn selection for
    `boss_side` is the caller's job (`pokemon.ui.battle_view.
    start_boss_encounter` passes it as `ai_side` -- see `pokemon.battle.ai`),
    not this function's."""
    from pokemon.battle.engine import BattleEngine

    is_doubles = format in ("Doubles", "Doubles 2v2")
    active_per_user = 2 if format == "Doubles" else 1
    field = Field("challengers", "boss", side_a_trainer=challenger_ids[0], side_b_trainer=boss_user_id)
    _populate_pvp_side(field.side_a, rosters, challenger_ids, active_per_user)

    boss_active = 2 if is_doubles else 1
    for i, p in enumerate(boss_team):
        b = battler_from_owned(f"boss:{p['id']}", p)
        b.owner_id = boss_user_id
        if i < boss_active:
            field.side_b.place(i, b)
        else:
            field.side_b.bench.append(b)
        field.side_b.roster.append(b)

    engine = BattleEngine(
        field, abilities=bot.pokemon_registries.abilities, moves=bot.pokemon_registries.moves,
        items=bot.pokemon_registries.items, ruleset=ruleset)
    return engine, field.side_a, field.side_b


# ---------------------------------------------------------------------------
# Post-battle persistence and rewards
# ---------------------------------------------------------------------------

def _owned_pokemon_id(battler: Battler) -> int | None:
    """`None` for a synthetic (wild/boss) Battler with no DB row -- the
    `"trainer:{id}"`/`"wild:{n}"`/`"boss:{id}"` id scheme every
    `build_*_battle_engine` above already uses."""
    if isinstance(battler.id, str) and battler.id.startswith("trainer:"):
        return int(battler.id.split(":", 1)[1])
    return None


async def persist_battle_result(*sides) -> None:
    """Writes every real (DB-backed) battler's final HP/status/fainted
    state back to `pokemon_owned` -- for every Pokemon on each side's full
    roster, not just whoever was active at the end, since bench Pokemon can
    take damage/status too (entry hazards, Pain Split, a mid-battle switch
    that then got KO'd, ...). Call this once, right when a battle ends,
    from every entry point (`BattleView._show_battle_end`) -- wild and boss
    sides are silently skipped, same as the monolith's own
    `_persist_pokemon` no-ops for a negative/synthetic id."""
    import json
    from pokemon.commands import repository

    for side in sides:
        for b in side.roster:
            pid = _owned_pokemon_id(b)
            if pid is None:
                continue
            now = datetime.now().isoformat()
            row = await repository.get_pokemon(pid) or {}
            fainted_at = now if b.vitals.hp <= 0 else None
            # Monolith `_sync_all`: a Pokemon below full HP gets a `hurt_at`
            # stamp the FIRST time (an existing one is kept, so the auto-heal
            # timer isn't restarted by every fight) and loses it at full HP.
            # Without this nothing ever auto-healed a Pokemon that was hurt
            # but not fainted (auto_heal.py keys off `hurt_at`).
            hurt_at = (row.get("hurt_at") or now) if b.vitals.hp < b.vitals.max_hp else None
            await repository.set_pokemon_battle_state(
                pid, max(0, b.vitals.hp), hurt_at, fainted_at, b.vitals.status)

            # PP: monolith persisted it as moves were used, stamping
            # `pp_reset_at` the first time any PP dropped (the PP auto-reset
            # loop keys off it). A transformed battler's moveset isn't its own.
            if b.transformed_into is None:
                slots = [{"name": s.move_slug, "pp": s.pp, "max_pp": s.max_pp} for s in b.moves.slots]
                stored = row.get("moves")
                stored = json.loads(stored) if isinstance(stored, str) else (stored or [])
                if slots and slots != [{"name": m["name"], "pp": m["pp"], "max_pp": m["max_pp"]} for m in stored]:
                    await repository.set_pokemon_moves(pid, slots)
                    if not row.get("pp_reset_at") and any(s["pp"] < s["max_pp"] for s in slots):
                        await repository.set_pp_reset(pid, datetime.now())


async def populate_bags(*sides) -> None:
    """Loads each human trainer's bag-usable item quantities (and their
    Pokedex, for Repeat-Ball-style odds) from the DB onto `Side.bags` /
    `Side.dex_seen`, once, before the battle -- the engine itself never
    touches the DB. Sides with no trainer id (wild) are skipped; a Doubles
    2v2 side holds one bag per trainer, keyed by id."""
    from pokemon.battle.bag import usable_bag_names
    from pokemon.commands import repository

    usable = usable_bag_names()
    for side in sides:
        uids = {side.trainer_id, *(getattr(b, "owner_id", None) for b in side.roster)} - {None}
        for uid in uids:
            if not isinstance(uid, int):
                continue
            qties = await repository.get_item_qties(uid)
            side.bags[uid] = {n: q for n, q in qties.items() if q > 0 and n in usable}
            if not side.dex_seen:
                side.dex_seen = await repository.get_pokedex_seen(uid)


async def flush_bag_usage(*sides) -> None:
    """Persists (and clears) every item the engine consumed since the last
    call. Called after each resolved turn, not just at battle end, so a
    battle abandoned mid-way (message deleted, bot restart) doesn't hand
    back balls and potions that were already spent."""
    from pokemon.commands import repository

    for side in sides:
        used, side.bag_used = side.bag_used, {}
        for (uid, item), n in used.items():
            await repository.add_item(uid, item, -n)


def _wild_difficulty_score(species: "Species", level: int, my_level: int) -> float:
    """Ported verbatim from the monolith's `_wild_difficulty_score`."""
    bst_factor = sum(species.base_stats.values()) / 400.0
    level_factor = level / 25.0
    gap = my_level - level
    gap_factor = max(0.5, min(1.8, 1.0 + max(0.0, -gap) * 0.04 - max(0.0, gap) * 0.02))
    raw = bst_factor * 0.6 + level_factor * 0.6
    floor = 1.0 if abs(gap) <= 5 else 0.4
    return max(floor, raw) * gap_factor


_POKEDOLLARS_GAIN = {
    "common": 175, "uncommon": 275, "rare": 400,
    "epic": 1500, "legendary": 3693, "mythical": 6969,
    "shiny_bonus": (1, 4096),
}


def calc_wild_reward(species: "Species", level: int, my_level: int, *, defeated: bool, shiny: bool) -> int:
    """Ported verbatim from the monolith's `calc_wild_reward`."""
    import random

    base = _POKEDOLLARS_GAIN.get(species.rarity, 50)
    difficulty = _wild_difficulty_score(species, level, my_level)
    reward = base * difficulty
    reward *= 1.6 if defeated else 1.0
    if shiny:
        a, b = _POKEDOLLARS_GAIN["shiny_bonus"]
        reward += random.randint(a, b) * (1.5 if defeated else 1.0)
    reward *= random.uniform(0.9, 1.1)
    return max(1, int(reward))


async def _grant_xp(pid: int, amount: int, engine_log: list[str]) -> None:
    """Awards XP to one stored Pokemon, recalculating and applying its new
    max HP (keeping current HP's proportion) on a level-up -- mirrors the
    monolith's `_award_xp_to_side._grant`, minus friendship gain and the
    fainted-participant XP-scaling factor (`fainted_level`), and minus
    Exp. Share splitting -- all documented gaps, see
    `award_wild_defeat_rewards`/`award_duel_win_rewards`'s docstrings."""
    from pokemon.commands import repository
    from pokemon.commands.stats import pokemon_max_hp
    from pokemon.dex.species import get_default_repository as get_species_repo

    old_level, new_level = await repository.award_pokemon_xp(pid, amount)
    p = await repository.get_pokemon(pid)
    if p is None:
        return
    engine_log.append(f"{p.get('nickname') or 'Your Pokemon'} gained **{amount:,}** Exp. Point{'s' if amount != 1 else ''}!")
    if new_level > old_level:
        engine_log.append(f"{p.get('nickname') or 'Your Pokemon'} grew to Lv. {new_level}!")
        species = get_species_repo().get(p["species_id"])
        if species and p.get("current_hp") is not None and not p.get("fainted_at"):
            old_max = pokemon_max_hp({**p, "level": old_level}, species)
            new_max = pokemon_max_hp(p, species)
            gained = max(0, new_max - old_max)
            await repository.set_pokemon_current_hp(pid, min(new_max, p["current_hp"] + gained))


async def award_wild_defeat_rewards(winner_uid: int, winner_pid: int | None, species: "Species",
                                     wild_level: int, my_level: int, shiny: bool) -> list[str]:
    """Money + XP for defeating (not catching) a wild Pokemon -- ported
    from `_conclude_wild_defeat`, minus EV yield and Pickup-ability item
    generation (both real, documented gaps -- see this function's
    docstring in the code review that added it). XP goes to `winner_pid`
    alone (the Pokemon that was active at the end) rather than being split
    across every participant + Exp. Share holder the way the monolith's
    `_award_xp_to_side` did, since a wild encounter here is always 1v1 with
    no switching (`build_wild_battle_engine`) -- there's only ever one
    participant to award it to."""
    from pokemon.commands import repository

    log: list[str] = []
    gain = calc_wild_reward(species, wild_level, my_level, defeated=True, shiny=shiny)
    await repository.add_pokedollars(winner_uid, gain)
    log.append(f"Received **\u20b1{gain:,}**!")

    if winner_pid is not None:
        base_exp = species.base_experience or max(5, wild_level * 4)
        total_xp = max(1, int(base_exp * wild_level / 7))
        await _grant_xp(winner_pid, total_xp, log)
    return log


async def finalize_catch(bot, engine, my_side, *, guild_id: int | None, spawn: dict | None = None,
                         channel_id: int | None = None) -> list[str]:
    """Turns a successful throw (`engine.catch`, set by battle/bag.py's
    PokeBall) into a real owned Pokemon and applies everything a catch
    does. Ported from the monolith's `_finalize_catch`:

      legendary/mythical tracking, the new Pokemon (keeping the wild one's
      REAL moves/PP, ability, held item, gender, form, remaining HP and
      status -- Heal Ball restores PP/HP/status, Friend Ball sets friendship
      to 200), auto-adding it to the team, Pokedex + milestones, the money
      and XP a defeat would have paid, catch-XP, and closing the spawn.

    Deviation from the monolith: it re-rolled the caught Pokemon's ability
    here instead of keeping the one the wild battler visibly had; this keeps
    the battler's, so what was fought is what you get.
    """
    from pokemon.commands import moveset as moveset_mod
    from pokemon.commands import repository
    from pokemon.commands.milestones import check_and_notify_milestones
    from pokemon.commands.transfer import auto_add_to_team

    outcome = engine.catch
    opp_side = next(s for s in engine.sides if s is not my_side)
    enc, wild, uid, ball = opp_side.wild_encounter, outcome.battler, my_side.trainer_id, outcome.ball
    species = enc.species
    log: list[str] = []

    if spawn is not None and spawn.get("claimed"):
        return ["It got away..."]

    if species.rarity in ("legendary", "mythical"):
        await repository.record_legendary_catch(uid, species.id)
        await repository.set_trainer_flag(uid, "has_" + species.rarity, True)

    heal_ball = ball == "Heal Ball"
    moves = [moveset_mod.MoveSlot(name=s.move_slug, pp=s.max_pp if heal_ball else s.pp, max_pp=s.max_pp)
             for s in wild.moves.slots]
    pid = await repository.add_pokemon(
        uid, species.id, enc.level, enc.ivs, enc.nature, shiny=enc.shiny, caught_guild_id=guild_id,
        ball_type=ball, initial_moves=moves, ability=wild.identity.ability_slug, gender=enc.gender,
        form=enc.form_slug, held_item=wild.identity.held_item or None,
        friendship=200 if ball == "Friend Ball" else species.base_friendship)
    if heal_ball:
        await repository.set_pokemon_hp_status(pid, None, None)
    else:
        await repository.set_pokemon_hp_status(pid, max(1, wild.vitals.hp), wild.vitals.status)
    await auto_add_to_team(uid, pid)
    log.append(f"**{species.name}** was added to your collection!")

    seen = await repository.get_pokedex_seen(uid)
    seen.add(species.id)
    await repository.set_pokedex_seen(uid, seen)
    try:
        await check_and_notify_milestones(bot, uid, seen)
    except Exception:
        pass  # a DM failing shouldn't undo a catch

    # The money/XP a defeat pays. Monolith: lowest-level active as the
    # reference level, XP to that same Pokemon.
    mine = sorted((b for b in my_side.active if b is not None), key=lambda b: b.identity.level)
    my_active = mine[0] if mine else None
    my_level = my_active.identity.level if my_active else enc.level
    gain = calc_wild_reward(species, enc.level, my_level, defeated=True, shiny=enc.shiny)
    await repository.add_pokedollars(uid, gain)
    log.append(f"Received **\u20b1{gain:,}**!")
    owned_id = _owned_pokemon_id(my_active) if my_active else None
    if owned_id is not None:
        total_xp = max(1, int((species.base_experience or max(5, enc.level * 4)) * enc.level / 7))
        await _grant_xp(owned_id, total_xp, log)

    if guild_id:
        try:
            cfg = await repository.get_pokemon_config(guild_id)
            if cfg.get("catch_xp", 0) > 0:
                from cogs.levelling import award_xp  # Track A: soft dependency
                guild = bot.get_guild(guild_id)
                member = guild.get_member(uid) if guild else None
                scaled = max(1, round(cfg["catch_xp"] * _wild_difficulty_score(species, enc.level, my_level)))
                await award_xp(guild_id, uid, float(scaled), None, member, "pokemon_catch")
        except Exception:
            pass

    if spawn is not None:
        from pokemon.commands.spawn_message_hook import update_wild_spawn_embed
        await update_wild_spawn_embed(channel_id, spawn.get("message_id"), spawn, "This Pok\u00e9mon was caught!")
    return log


async def award_duel_win_rewards(winner_side, loser_uid: int) -> list[str]:
    """Money + XP for winning a PvP duel -- ported from the monolith's
    `duel_win_reward` (already present in `pokemon.commands.duel`) for the
    Pokedollar prize, plus a flat XP grant to every Pokemon that was active
    at the end of the battle on the winning side (a simplified stand-in for
    `_award_xp_to_side`'s full participant-tracking/Exp.-Share logic --
    documented gap, same as `award_wild_defeat_rewards`'s)."""
    from pokemon.commands import repository
    from pokemon.commands.duel import duel_win_reward

    log: list[str] = []
    gain = await duel_win_reward(loser_uid)
    if winner_side.trainer_id is not None:
        await repository.add_pokedollars(winner_side.trainer_id, gain)
        log.append(f"**<@{winner_side.trainer_id}>** received **\u20b1{gain:,}**!")

    total_xp = 50
    for b in winner_side.active:
        pid = _owned_pokemon_id(b) if b else None
        if pid is not None and b.vitals.hp > 0:
            await _grant_xp(pid, total_xp, log)
    return log

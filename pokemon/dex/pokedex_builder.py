"""Offline Pokédex/move/ability/item data pipeline.

This is a *developer* tool, not a bot command (see `deploy/update_command.py`
and `data/README.md`: `/pokedex rebuild` no longer exists — you run this
locally, review the diff, and commit `data/*.json`. The bot only ever reads
those committed files at runtime; it never talks to PokeAPI or Showdown
itself).

Three independent data sources feed this pipeline:

PokeAPI CACHE pass (offline, fast, PREFERRED -- this is what actually built
the currently-committed data/*.json)
    Reads a previously-saved export of the original monolith's own on-disk
    PokeAPI caches (`pokedex_cache.json`, `moves_cache.json`,
    `evolution_cache.json`, `items_cache.json`, `move_descs.json` -- the
    exact files contore.py's own `_build_entry`/`get_move_data`/etc. read
    from and wrote to at runtime). Since this data was fetched from PokeAPI
    by this exact bot, in the exact shape its own code already expects,
    there's no field-mapping guesswork and no simulator-to-PokeAPI
    reinterpretation involved -- see `run_pokeapi_cache_pass`'s own
    docstring for the full reasoning. Produces every `data/*.json` the bot
    needs EXCEPT `generation_availability.json` (cross-gen legality has no
    equivalent in these caches -- the original monolith never tracked it,
    since it never supported more than one generation's ruleset).

Showdown pass (offline, fast, fallback / cross-gen-only)
    Reads pre-transpiled JSON produced by `tools/showdown_extract.js` from
    a checked-out github.com/smogon/pokemon-showdown. Used alongside the
    PokeAPI cache pass ONLY for `data/generation_availability.json`
    (Showdown's learnsets encode the exact generation each move became
    learnable, per-species -- PokeAPI's cache format doesn't). Can also run
    standalone (no PokeAPI cache available) to produce a serviceable, if
    less complete, `data/pokedex.json`/`data/evolution_chains.json`/etc.
    from Showdown's own data instead -- species records from this pass are
    flagged `needs_pokeapi_enrichment: true` for that reason.

PokeAPI LIVE pass (online, slow, for refreshing the cache pass's inputs)
    Async HTTP fetch, one request per species/move/ability/item, exactly
    mirroring what the original monolith's live `_build_entry` /
    `_fetch_move_from_api` / `_fetch_ability_from_api` / `_fetch_item_from_api`
    did on every `/pokedex rebuild` — except it now runs once, offline, and
    writes files instead of populating an in-memory cache. Only useful for
    producing a *fresh* set of cache files (e.g. after a new generation's
    data is added to PokeAPI) for a future `--pokeapi-cache` run; needs
    actual network access to pokeapi.co, which isn't always available.

Usage
-----
    # preferred: rebuild everything from the original bot's own caches,
    # plus cross-gen data from Showdown:
    python -m pokemon.dex.pokedex_builder \\
        --pokeapi-cache /path/to/caches --showdown-json /tmp/showdown_json

    # PokeAPI cache pass alone (skips generation_availability.json):
    python -m pokemon.dex.pokedex_builder --pokeapi-cache /path/to/caches

    # Showdown-only fallback, no PokeAPI cache available:
    npm install esbuild   # one-time setup, inside pokemon/dex/tools
    node tools/showdown_extract.js /tmp/showdown_json /path/to/pokemon-showdown
    python -m pokemon.dex.pokedex_builder --showdown-json /tmp/showdown_json

    # refresh the caches --pokeapi-cache reads (slow; needs network):
    python -m pokemon.dex.pokedex_builder --pokeapi
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

DEX_DIR = Path(__file__).resolve().parent
DATA_DIR = DEX_DIR.parents[1] / "data"

NATIONAL_DEX_MAX = 721  # Bulbasaur -> Volcanion; Gen I-VI, matching the bot's historical scope

# Generation dex-number cutoffs (inclusive upper bound of each generation).
GEN_DEX_CUTOFFS: list[tuple[int, int]] = [
    (1, 151), (2, 251), (3, 386), (4, 493), (5, 649), (6, 721),
]

# Forme-name substrings that indicate a generation beyond our current
# National-Dex scope; these are filtered out entirely rather than dated,
# since the base species may still be legal even though the alt-form isn't.
_POST_GEN6_FORME_MARKERS = (
    "alola", "galar", "hisui", "paldea", "gmax", "totem", "starter",
    "-cap", "world", "partner", "eternamax", "-z-", "unova-cap",
)

_MEGA_MARKER = "mega"


def species_gen_from_dex_number(dex_id: int) -> int:
    for gen, cutoff in GEN_DEX_CUTOFFS:
        if dex_id <= cutoff:
            return gen
    return GEN_DEX_CUTOFFS[-1][0]


# ---------------------------------------------------------------------------
# Showdown pass
# ---------------------------------------------------------------------------

def _load_showdown_json(showdown_dir: Path, name: str, *, required: bool = True) -> dict:
    path = showdown_dir / f"{name}.json"
    if not path.exists():
        if not required:
            return {}
        raise FileNotFoundError(
            f"{path} not found — run `node tools/showdown_extract.js {showdown_dir}` "
            "against a checked-out pokemon-showdown repo first."
        )
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


_SHOWDOWN_STAT_KEYS = {"hp": "hp", "atk": "attack", "def": "defense",
                        "spa": "special-attack", "spd": "special-defense", "spe": "speed"}


def _map_base_stats(showdown_stats: dict[str, int]) -> dict[str, int]:
    return {_SHOWDOWN_STAT_KEYS[k]: v for k, v in showdown_stats.items() if k in _SHOWDOWN_STAT_KEYS}


def _map_abilities(showdown_abilities: dict[str, str]) -> list[dict]:
    out = []
    for slot, name in showdown_abilities.items():
        if not name:
            continue
        out.append({
            "name": _slugify(name),
            "is_hidden": slot == "H",
        })
    return out


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# Showdown's `eggGroups` are display strings ("Monster", "Water 1",
# "Human-Like", "Ditto", "Undiscovered", ...); every consumer of
# `Species.egg_groups` (breeding compatibility, Ditto detection, the
# "can't breed at all" check) is written against the old PokeAPI-slug
# vocabulary instead ("monster", "water1", "human-like", "ditto",
# "no-eggs"). "Undiscovered" -> "no-eggs" is a genuine vocabulary
# difference (not just casing) and needs an explicit mapping; everything
# else is just PokeAPI's slugification of the same display name.
_EGG_GROUP_OVERRIDES: dict[str, str] = {"Undiscovered": "no-eggs"}


def _normalize_egg_groups(groups: list[str]) -> list[str]:
    return [_EGG_GROUP_OVERRIDES.get(g, _slugify(g)) for g in groups]


def _is_post_gen6_forme(forme: str) -> bool:
    lowered = forme.lower()
    return any(marker in lowered for marker in _POST_GEN6_FORME_MARKERS)


def _gender_rate_from_ratio(entry: dict) -> int:
    fixed = entry.get("gender")
    if fixed == "N":
        return -1
    if fixed == "M":
        return 0
    if fixed == "F":
        return 8
    ratio = entry.get("genderRatio")
    if not ratio:
        return -1
    female_frac = ratio.get("F", 0.0)
    return round(female_frac * 8)


def build_pokedex_from_showdown(showdown_dir: Path) -> tuple[list[dict], dict[str, int]]:
    """Returns (species_records, move_min_gen_by_species) — the second value
    isn't used here but is threaded through to `build_generation_availability`
    so learnsets only need to be parsed once."""
    pokedex = _load_showdown_json(showdown_dir, "Pokedex")

    # Group every dex entry (base species + alternate/Mega forms) by
    # National Dex number so forms can be attached to their base species.
    by_num: dict[int, list[tuple[str, dict]]] = {}
    for sid, entry in pokedex.items():
        num = entry.get("num", -1)
        if num is None or num < 1 or num > NATIONAL_DEX_MAX:
            continue
        by_num.setdefault(num, []).append((sid, entry))

    species_records: list[dict] = []

    for num, entries in sorted(by_num.items()):
        base_sid, base_entry = next(
            ((sid, e) for sid, e in entries if not e.get("forme")), entries[0]
        )

        forms: list[dict] = []
        mega_forms: list[dict] = []
        for sid, entry in entries:
            forme = entry.get("forme")
            if sid == base_sid:
                continue
            if not forme or _is_post_gen6_forme(forme):
                continue
            form_record = {
                "slug": sid,
                "name": entry.get("name", sid),
                "types": entry.get("types", base_entry.get("types", [])),
                "base_stats": _map_base_stats(entry.get("baseStats", base_entry.get("baseStats", {}))),
                "abilities": _map_abilities(entry.get("abilities", {})),
                "is_mega": _MEGA_MARKER in forme.lower(),
                "is_battle_only": bool(entry.get("battleOnly")),
            }
            (mega_forms if form_record["is_mega"] else forms).append(form_record)

        prevo_name = base_entry.get("prevo")
        prevo_id = pokedex.get(_slugify(prevo_name) if prevo_name else "", {}).get("num") if prevo_name else None

        evolves_to = []
        for evo_name in base_entry.get("evos", []):
            evo_entry = pokedex.get(_slugify(evo_name), {})
            if evo_entry.get("num"):
                evolves_to.append({"to_id": evo_entry["num"], "to_name": evo_name})

        species_records.append({
            "id": num,
            "name": base_entry.get("name", base_sid),
            "types": base_entry.get("types", []),
            "base_stats": _map_base_stats(base_entry.get("baseStats", {})),
            "abilities": _map_abilities(base_entry.get("abilities", {})),
            "generation_introduced": species_gen_from_dex_number(num),
            "rarity": "common",  # recomputed by `assign_rarity_tiers` below
            # Showdown carries none of the following; PokeAPI pass fills these in.
            "catch_rate": 45,
            "growth_rate": "medium",
            "base_friendship": 70,
            "hatch_counter": 20,
            "gender_rate": _gender_rate_from_ratio(base_entry),
            "egg_groups": _normalize_egg_groups(base_entry.get("eggGroups", [])),
            "height_m": (base_entry.get("heightm") or 0.0),
            "weight_kg": (base_entry.get("weightkg") or 0.0),
            "effort_yield": {},
            "base_experience": 64,
            "genus": "",
            "flavor_text": None,
            "forms": forms,
            "mega_forms": mega_forms,
            "prevo": prevo_id,
            "evolves_to": evolves_to,
            "needs_pokeapi_enrichment": True,
        })

    assign_rarity_tiers(species_records)
    return species_records, {}


def assign_rarity_tiers(species_records: list[dict]) -> None:
    """Same heuristic the original monolith used at runtime (BST bands with
    a legendary/mythical override), just computed once and stored instead
    of recomputed on every spawn roll."""
    # A species is provisionally "legendary-shaped" if it's the sole member
    # of its evolutionary line and has a very high BST; this is a rough
    # stand-in for PokeAPI's `is_legendary`/`is_mythical` flags, which the
    # PokeAPI enrichment pass should overwrite with the authoritative value.
    evolves_from: dict[int, int] = {}
    for rec in species_records:
        for evo in rec["evolves_to"]:
            evolves_from[evo["to_id"]] = rec["id"]
    has_prevo_or_evo = {
        rec["id"] for rec in species_records if rec["prevo"] or rec["evolves_to"]
    }

    for rec in species_records:
        bst = sum(rec["base_stats"].values())
        standalone = rec["id"] not in has_prevo_or_evo
        if standalone and bst >= 670:
            rec["rarity"] = "mythical" if bst >= 700 and rec["id"] not in (144, 145, 146, 243, 244, 245) else "legendary"
        elif standalone and bst >= 560:
            rec["rarity"] = "legendary"
        elif bst >= 575:
            rec["rarity"] = "epic"
        elif bst >= 500:
            rec["rarity"] = "rare"
        elif bst >= 400:
            rec["rarity"] = "uncommon"
        else:
            rec["rarity"] = "common"


_LEARNSET_CODE_RE = re.compile(r"^(\d)")


def build_generation_availability(showdown_dir: Path, species_records: list[dict]) -> dict[str, Any]:
    """The ground-truth cross-gen data set: per species/move/ability, the
    earliest generation it's known to exist in. Feeds
    `pokemon.generations.overlays`, which turns this into `excluded_moves`
    / `excluded_abilities` on top of the frozen `GenerationRuleset`s."""
    learnsets = _load_showdown_json(showdown_dir, "Learnsets")
    pokedex = _load_showdown_json(showdown_dir, "Pokedex")
    abilities = _load_showdown_json(showdown_dir, "Abilities")
    # IMPORTANT: Showdown's main `learnsets.ts` only carries Gen-3-and-later
    # per-move codes ("3L1", "6M", ...) -- it has ZERO "1"/"2"-prefixed
    # codes. Gen 1/2-origin moves' history lives exclusively in
    # `data/mods/gen2/learnsets.ts` (Gen 1 has no learnsets.ts of its own;
    # its data is folded into Gen 2's file). Skipping this file was an
    # actual bug in the first pass of this builder: it computed e.g. Tackle
    # and Thundershock as "Gen 3+", which would make GEN_1/GEN_2 rulesets
    # wrongly exclude core, always-legal Gen 1 moves. See
    # tools/showdown_extract.js's FILES list for how this JSON is produced.
    gen2_learnsets = _load_showdown_json(showdown_dir, "Gen2Learnsets", required=False)

    species_gen = {rec["id"]: rec["generation_introduced"] for rec in species_records}

    move_min_gen: dict[str, int] = {}
    for source in (learnsets, gen2_learnsets):
        for sid, entry in source.items():
            dex_num = pokedex.get(sid, {}).get("num")
            if dex_num is None or dex_num > NATIONAL_DEX_MAX:
                continue
            for move_slug, codes in entry.get("learnset", {}).items():
                for code in codes:
                    m = _LEARNSET_CODE_RE.match(code)
                    if not m:
                        continue
                    gen = int(m.group(1))
                    if gen < 1 or gen > 9:
                        continue
                    if move_slug not in move_min_gen or gen < move_min_gen[move_slug]:
                        move_min_gen[move_slug] = gen

    # Abilities have no first-class "introduced in" field in Showdown's data;
    # derive it as the earliest generation any species that can have it
    # exists in, with Hidden Abilities floored at Gen 5 (when the mechanic
    # was introduced) regardless of the species' own generation.
    ability_min_gen: dict[str, int] = {}
    for sid, entry in pokedex.items():
        dex_num = entry.get("num")
        if dex_num is None or dex_num > NATIONAL_DEX_MAX:
            continue
        sp_gen = species_gen.get(dex_num, 1)
        for slot, ability_name in entry.get("abilities", {}).items():
            if not ability_name:
                continue
            slug = _slugify(ability_name)
            gen = max(sp_gen, 5) if slot == "H" else sp_gen
            if slug not in ability_min_gen or gen < ability_min_gen[slug]:
                ability_min_gen[slug] = gen
    # Fill in any ability that exists but wasn't seen on a species we kept
    # (shouldn't normally happen, but keeps AbilityRegistry.load() honest).
    for slug in abilities:
        canonical = _slugify(abilities[slug].get("name", slug))
        ability_min_gen.setdefault(canonical, 6)

    return {
        "species": {str(rec["id"]): rec["generation_introduced"] for rec in species_records},
        "moves": move_min_gen,
        "abilities": ability_min_gen,
    }


def _resolve_trade_item_slug(evo_item_name: str | None) -> str | None:
    """Showdown's `evoItem` display names ("DeepSeaTooth", "King's Rock")
    don't always slugify to the same string `items_repo.TRADE_EVOLUTION_ITEMS`
    already uses as its canonical `slug` ("deep-sea-tooth", "kings-rock") --
    apostrophes and Showdown's occasional no-space naming diverge from the
    catalog's own convention. Rather than keep two independent slugifiers
    in sync, resolve through a normalized (letters+digits only) comparison
    against the catalog itself and return *its* canonical slug, so
    `EvolutionCondition.held_item` always matches what
    `pokemon.commands.evolution.check_trade_evolution` compares it against.
    """
    if not evo_item_name:
        return None
    from pokemon.dex.items_repo import TRADE_EVOLUTION_ITEMS

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    target = _norm(evo_item_name)
    for display_name, data in TRADE_EVOLUTION_ITEMS.items():
        if _norm(display_name) == target:
            return data["slug"]
    return _slugify(evo_item_name)  # unrecognized item: best-effort fallback


def build_evolution_chains(showdown_dir: Path, species_records: list[dict]) -> dict[str, list[dict]]:
    """`{from_id: [EvolutionCondition-shaped dict, ...]}`, matching
    `pokemon.dex.evolution.EvolutionCondition`'s field names."""
    pokedex = _load_showdown_json(showdown_dir, "Pokedex")
    by_id = {rec["id"]: rec for rec in species_records}
    chains: dict[str, list[dict]] = {}

    for rec in species_records:
        for evo in rec["evolves_to"]:
            to_entry = pokedex.get(_slugify(evo["to_name"]), {})
            evo_item_slug = _slugify(to_entry["evoItem"]) if to_entry.get("evoItem") else None
            evo_type = to_entry.get("evoType")
            condition = {
                "to_id": evo["to_id"],
                "to_name": evo["to_name"],
                "trigger": _showdown_evo_trigger(to_entry),
                "min_level": to_entry.get("evoLevel"),
                "min_happiness": 220 if to_entry.get("evoType") == "levelFriendship" else None,
                "time_of_day": _time_of_day_from_condition(to_entry.get("evoCondition")),
                # Showdown overloads `evoItem` for both "consume this item to
                # evolve" (evoType "useItem") and "must be *holding* this item
                # when traded" (evoType "trade") -- these are different
                # `EvolutionCondition` fields (`item` vs `held_item`), not
                # optional/either-or, so both need their own type check
                # rather than "if evoItem is present, assume it means X".
                "item": evo_item_slug if evo_type == "useItem" else None,
                "held_item": _resolve_trade_item_slug(to_entry["evoItem"]) if evo_type == "trade" and to_entry.get("evoItem") else None,
                "known_move": _slugify(to_entry["evoMove"]) if to_entry.get("evoMove") else None,
                "known_move_type": None,
                "gender": {"M": 2, "F": 1}.get(to_entry.get("evoGender")),
                "needs_rain": False,
                "relative_physical_stats": None,
                "party_species": None,
                "party_type": None,
                "trade_species": (
                    pokedex.get(_slugify(_trade_partner_species(to_entry.get("evoCondition"))), {}).get("num")
                    if evo_type == "trade" and to_entry.get("evoCondition") else None
                ),
                "location": None,
                "near_special_rock": False,
                "min_beauty": None,
                "min_steps": None,
                "min_damage_taken": None,
                "min_move_count": None,
                "needs_multiplayer": False,
                "turn_upside_down": False,
                "region": None,
            }
            chains.setdefault(str(rec["id"]), []).append(condition)

    return chains


def _trade_partner_species(evo_condition: str | None) -> str:
    """Showdown's `evoCondition` for a "must trade for a specific species"
    evolution (Karrablast<->Shelmet) is free text like `"with a
    Karrablast"`, not a clean species slug -- pull just the name back out
    so it can be looked up in the Pokédex. Returns `""` (no match) for
    every other kind of `evoCondition` text (time-of-day phrases, etc.),
    which safely produces no `trade_species` for those."""
    if not evo_condition:
        return ""
    m = re.search(r"with an? (.+)$", evo_condition, re.IGNORECASE)
    return m.group(1) if m else ""


def _showdown_evo_trigger(entry: dict) -> str:
    evo_type = entry.get("evoType")
    return {
        "levelFriendship": "level-up",
        "levelMove": "level-up",
        "levelExtra": "level-up",
        "trade": "trade",
        "useItem": "use-item",
        "other": "other",
    }.get(evo_type, "level-up")


def _time_of_day_from_condition(condition: str | None) -> str | None:
    if not condition:
        return None
    lowered = condition.lower()
    if "day" in lowered:
        return "day"
    if "night" in lowered:
        return "night"
    return None


def build_items_meta_from_showdown(showdown_dir: Path) -> dict[str, dict]:
    """`{item display name: {"fling_power": int|None, "fling_effect": str|None}}`
    — matches `pokemon.dex.items_repo.get_fling_data()`'s expected shape.
    We key by *display name* (not slug) because that's how the item
    catalog in `items_repo.py` is keyed."""
    from pokemon.dex import items_repo

    items = _load_showdown_json(showdown_dir, "Items")
    by_slug = {_slugify(v.get("name", k)): v for k, v in items.items()}

    _FLING_STATUS_MAP = {
        "brn": "burn", "par": "paralysis", "psn": "poison", "tox": "bad_poison",
        "slp": "sleep", "frz": "freeze",
    }

    out: dict[str, dict] = {}
    for table in items_repo.ALL_CATALOG_TABLES:
        for display_name in table:
            slug = _slugify(display_name)
            raw = by_slug.get(slug)
            if not raw:
                continue
            fling = raw.get("fling") or {}
            effect = None
            if fling.get("status") in _FLING_STATUS_MAP:
                effect = _FLING_STATUS_MAP[fling["status"]]
            elif fling.get("volatileStatus") == "flinch":
                effect = "flinch"
            out[display_name] = {
                "fling_power": fling.get("basePower"),
                "fling_effect": effect,
            }
    return out


_LEVEL_CODE_RE = re.compile(r"^([1-9])L(\d+)$")
_OTHER_CODE_RE = re.compile(r"^([1-9])([EMT])")


def build_learnsets_from_showdown(showdown_dir: Path, species_records: list[dict]) -> dict[str, dict]:
    """Per-species movepool, keyed by national dex number (string, matching
    every other data/*.json file's convention).

    This didn't exist anywhere in the original Showdown-pass drop --
    `pokedex.json` carries no movepool at all (see the `run_pokeapi_pass`
    comment that used to note this gap), which meant nothing could actually
    give a wild/starter/bred Pokémon a starting moveset. Showdown's
    Learnsets.json was already being parsed once for
    `build_generation_availability` (move min-generation); this reuses the
    same source rather than pulling in a second one.

    Showdown's per-move codes (`"6L33"`, `"9M"`, `"7E"`, `"3T"`, ...) are
    `{generation}{method}{level?}`. This bot's battle engine only
    implements Gen VI mechanics today (see ARCHITECTURE.md's "honest
    caveat"), so `level_moves` takes, for each move, the code from the
    highest generation <= 6 available (closest to "current" Gen VI
    learnsets) rather than tracking a separate movepool per generation --
    real per-generation movepool differences are the same kind of follow-up
    work as per-generation battle mechanics generally.

    `egg_moves` / `tm_moves` / `tutor_moves` are flat, deduplicated, and
    gen<=6-only, for breeding egg-move inheritance and TM/tutor teaching
    respectively. Alternate forms (Mega, regional-adjacent, ...) inherit
    their base species' movepool in-game and aren't tracked separately here
    -- `SpeciesForm` has no `level_moves` field of its own.
    """
    learnsets = _load_showdown_json(showdown_dir, "Learnsets")
    gen2_learnsets = _load_showdown_json(showdown_dir, "Gen2Learnsets", required=False)
    pokedex = _load_showdown_json(showdown_dir, "Pokedex")

    dex_num_by_slug = {sid: entry.get("num") for sid, entry in pokedex.items()}
    valid_ids = {rec["id"] for rec in species_records}

    # {dex_id: {move_slug: ((gen, -level) rank, level)}} -- keeps the
    # best-ranked code per move seen so far without a second pass.
    level_best: dict[int, dict[str, tuple[tuple[int, int], int]]] = {}
    # {dex_id: {"E"/"M"/"T": {move_slug, ...}}}
    other_best: dict[int, dict[str, set[str]]] = {}

    for source in (learnsets, gen2_learnsets):
        for sid, entry in source.items():
            dex_id = dex_num_by_slug.get(sid)
            if dex_id not in valid_ids:
                continue
            for move_slug, codes in entry.get("learnset", {}).items():
                for code in codes:
                    m = _LEVEL_CODE_RE.match(code)
                    if m:
                        gen, level = int(m.group(1)), int(m.group(2))
                        if gen > 6:
                            continue
                        by_move = level_best.setdefault(dex_id, {})
                        rank = (gen, -level)
                        existing = by_move.get(move_slug)
                        if existing is None or rank > existing[0]:
                            by_move[move_slug] = (rank, level)
                        continue
                    m2 = _OTHER_CODE_RE.match(code)
                    if not m2:
                        continue
                    gen, method = int(m2.group(1)), m2.group(2)
                    if gen > 6 or method not in ("E", "M", "T"):
                        continue
                    other_best.setdefault(dex_id, {}).setdefault(method, set()).add(move_slug)

    out: dict[str, dict] = {}
    for dex_id in valid_ids:
        lvl_moves = sorted(
            (level, slug) for slug, (_, level) in level_best.get(dex_id, {}).items()
        )
        methods = other_best.get(dex_id, {})
        out[str(dex_id)] = {
            "level_moves": [[level, slug] for level, slug in lvl_moves],
            "egg_moves": sorted(methods.get("E", ())),
            "tm_moves": sorted(methods.get("M", ())),
            "tutor_moves": sorted(methods.get("T", ())),
        }
    return out


# ---------------------------------------------------------------------------
# PokeAPI CACHE pass -- builds every data/*.json the bot needs directly from
# a previously-saved export of the original monolith's own on-disk PokeAPI
# caches (pokedex_cache.json / moves_cache.json / evolution_cache.json /
# items_cache.json / move_descs.json -- exactly the files `POKEDEX_PATH` /
# `MOVE_CACHE_PATH` / `EVOLUTION_CACHE_PATH` / `ITEM_CACHE_PATH` /
# `MOVE_DESCS_PATH` pointed at in contore.py). This is now the PREFERRED
# pipeline -- see `run_pokeapi_cache_pass()`'s docstring for why -- over
# both the Showdown pass above (kept only for `generation_availability.json`,
# which has no PokeAPI-cache equivalent) and the live `run_pokeapi_pass()`
# below (kept only for refreshing these caches later, when network access
# to pokeapi.co is actually available).
# ---------------------------------------------------------------------------

_GENERATION_NAME_TO_NUM = {
    "generation-i": 1, "generation-ii": 2, "generation-iii": 3, "generation-iv": 4,
    "generation-v": 5, "generation-vi": 6, "generation-vii": 7, "generation-viii": 8,
    "generation-ix": 9,
}


def _load_cache_json(cache_dir: Path, filename: str, *, required: bool = True) -> Any:
    path = cache_dir / filename
    if not path.exists():
        if not required:
            return None
        raise FileNotFoundError(
            f"{path} not found -- --pokeapi-cache must point at a directory "
            "containing the original bot's pokedex_cache.json, moves_cache.json, "
            "evolution_cache.json, items_cache.json, and move_descs.json."
        )
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _map_form_record(entry: dict, *, is_mega: bool) -> dict:
    """`pokedex_cache.json`'s `forms`/`mega_forms` entries carry a lot more
    than a `Species` alt-form needs (sprite paths, per-form level_moves,
    ...) -- this keeps only what `build_pokedex_from_showdown`'s form
    records already established as the target shape, so both passes are
    interchangeable from `pokemon.dex.species`'s point of view."""
    return {
        "slug": entry["slug"],
        "name": entry["name"],
        "types": entry.get("types", []),
        "base_stats": entry.get("base_stats", {}),
        "abilities": entry.get("abilities", []),
        "is_mega": is_mega,
        "is_battle_only": bool(entry.get("trigger_only")),
    }


def build_pokedex_from_pokeapi_cache(cache_dir: Path) -> list[dict]:
    """Species records straight from `pokedex_cache.json`. Output shape
    matches `build_pokedex_from_showdown` field-for-field -- every
    downstream consumer is unaffected by which pass produced its data --
    except `needs_pokeapi_enrichment` is always False here: this data
    *is* the enrichment (catch rate, growth rate, friendship, flavor text,
    ...), unlike the Showdown pass which only has battle-relevant fields.

    `prevo` is the one thing `pokedex_cache.json` doesn't carry per-species
    (it only records what a species evolves INTO via the separate
    `evolution_cache.json`, not what evolves into it) -- inverting that
    file's `{from_id: [{to_id, ...}]}` edges gives every species' prevo in
    one pass.
    """
    raw = _load_cache_json(cache_dir, "pokedex_cache.json")
    evo_cache = _load_cache_json(cache_dir, "evolution_cache.json", required=False) or {}

    prevo_by_id: dict[int, int] = {}
    for from_id_str, edges in evo_cache.items():
        for edge in edges:
            prevo_by_id[edge["to_id"]] = int(from_id_str)

    species_records: list[dict] = []
    for entry in raw:
        dex_id = entry["id"]
        if dex_id < 1 or dex_id > NATIONAL_DEX_MAX:
            continue  # the cache may run ahead of this bot's current dex scope

        forms = [_map_form_record(f, is_mega=False) for f in entry.get("forms", [])]
        mega_forms = [_map_form_record(f, is_mega=True) for f in entry.get("mega_forms", [])]
        evolves_to = [{"to_id": e["to_id"], "to_name": e["to_name"]}
                      for e in evo_cache.get(str(dex_id), [])]

        species_records.append({
            "id": dex_id,
            "name": entry["name"],
            "types": entry.get("types", []),
            "base_stats": entry.get("base_stats", {}),
            "abilities": entry.get("abilities", []),
            "generation_introduced": _GENERATION_NAME_TO_NUM.get(
                entry.get("generation"), species_gen_from_dex_number(dex_id)),
            "rarity": "common",  # recomputed by assign_rarity_tiers below
            "catch_rate": entry.get("catch_rate", 45),
            "growth_rate": entry.get("growth_rate", "medium"),
            "base_friendship": entry.get("base_friendship", 70),
            "hatch_counter": entry.get("hatch_counter", 20),
            # Already PokeAPI's own -1..8 scale (-1 genderless, 0 always-male,
            # 8 always-female, else eighths-female) -- no conversion needed,
            # unlike the Showdown pass's `_gender_rate_from_ratio`.
            "gender_rate": entry.get("gender_rate", -1),
            # Likewise already PokeAPI's own egg-group slugs ("humanshape",
            # "no-eggs", "water1", ...) -- the exact vocabulary
            # `pokemon.commands.breeding` checks against, so passed through
            # unlike the Showdown pass's `_normalize_egg_groups`.
            "egg_groups": entry.get("egg_groups", []),
            "height_m": (entry.get("height") or 0) / 10,   # PokeAPI: decimeters
            "weight_kg": (entry.get("weight") or 0) / 10,  # PokeAPI: hectograms
            "effort_yield": entry.get("effort_yield", {}),
            "base_experience": entry.get("base_experience", 64),
            "genus": entry.get("genera", ""),
            "flavor_text": entry.get("flavor_text"),
            "forms": forms,
            "mega_forms": mega_forms,
            "prevo": prevo_by_id.get(dex_id),
            "evolves_to": evolves_to,
            "needs_pokeapi_enrichment": False,
        })

    assign_rarity_tiers(species_records)
    return species_records


# `evolution_cache.json`'s export predates several `EvolutionCondition`
# fields (`known_move_type`, `party_species`, `trade_species`,
# `near_special_rock`, `min_beauty`, ...) -- ported straight through, every
# evolution route in that bucket looks unconditional (every extra field is
# simply absent, not present-and-null), which would wrongly let e.g. any
# Feebas evolve by plain level-up with no Beauty at all. This table restores
# the missing condition on the exact (from_id, to_id, trigger) edge it
# belongs to; `_finalize_evolution_bucket` below then decides, exactly like
# the original monolith's `_sanitize_unfulfillable_evolutions` /
# `_entry_is_unfulfillable` did, whether a now-conditioned route should be
# dropped (an alternate fulfillable route exists) or kept as a plain
# level-up fallback (it doesn't). Sourced from the mainline games directly,
# not from any track's guesswork -- see each comment.
_EVOLUTION_OVERRIDES: dict[tuple[int, int, str], dict[str, Any]] = {
    # Feebas -> Milotic: high Beauty (ORAS also allows trade-holding Prism
    # Scale, already fully specified in the cache as its own edge).
    (349, 350, "level-up"): {"min_beauty": 170},
    # Eevee -> Leafeon/Glaceon: level up near a Moss Rock / Icy Rock.
    # (X/Y+ also added Leaf Stone / Ice Stone alternatives, already fully
    # specified in the cache as their own edges.)
    (133, 470, "level-up"): {"near_special_rock": True},
    (133, 471, "level-up"): {"near_special_rock": True},
    # Eevee -> Sylveon: level up with high friendship, knowing a Fairy move.
    (133, 700, "level-up"): {"min_happiness": 160, "known_move_type": "fairy"},
    # Magneton -> Magnezone / Nosepass -> Probopass: level up in a special
    # magnetic-field area (no in-bot equivalent -- see
    # `_UNFULFILLABLE_FILTER_FIELDS`). Each also already has its own
    # Thunder Stone item-evolution edge in the cache; flagging the
    # location here just lets that alternate route win instead of this one
    # silently having no condition at all.
    (82, 462, "level-up"): {"location": "special-magnetic-field"},
    (299, 476, "level-up"): {"location": "special-magnetic-field"},
    # Karrablast <-> Shelmet: must specifically trade for the other.
    (588, 589, "trade"): {"trade_species": 616},
    (616, 617, "trade"): {"trade_species": 588},
    # Mantyke -> Mantine: needs a Remoraid in the party.
    (458, 226, "level-up"): {"party_species": 223},
}

# Conditions this bot has no gameplay way to fulfil outside of a battle
# (there's no in-Discord "location", "beauty contest", "step counter", or
# "flip your device" -- and weather is battle-only state, meaningless when
# checking evolution outside of one). Matches the original monolith's
# `_UNFULFILLABLE_EVO_FIELDS` with one addition (`near_special_rock`, which
# the original omitted from that list despite it being exactly as
# unfulfillable as the rest -- see this module's ARCHITECTURE notes on
# preferring a fix over a faithfully-ported oversight when the fixed
# version is strictly more correct and doesn't change any *reachable*
# behavior). `known_move_type` / `party_species` / `trade_species` are
# deliberately NOT in this list: `pokemon.commands.evolution` /
# `breeding` / `trading` actually check those against real bot state.
_UNFULFILLABLE_FILTER_FIELDS = (
    "location", "min_beauty", "min_steps", "min_damage_taken", "min_move_count",
    "needs_multiplayer", "needs_rain", "turn_upside_down", "region", "near_special_rock",
)


def _bst_based_evo_level(to_id: int, species_by_id: dict[int, dict]) -> int:
    """Ported verbatim from the original monolith's `_bst_based_evo_level`
    -- a fallback evolution level for the rare case where EVERY route to a
    species is unfulfillable and there's nothing else to fall back to."""
    bst = sum(species_by_id.get(to_id, {}).get("base_stats", {}).values()) or 400
    return max(12, min(42, 16 + int((bst - 300) / 300 * 24)))


def _finalize_evolution_bucket(bucket: list[dict], species_by_id: dict[int, dict]) -> list[dict]:
    blocked = [e for e in bucket if any(e.get(f) for f in _UNFULFILLABLE_FILTER_FIELDS)]
    if not blocked:
        return bucket
    if len(blocked) < len(bucket):
        # At least one alternate, fulfillable route exists -- drop only the
        # unfulfillable one(s) rather than the whole bucket.
        return [e for e in bucket if e not in blocked]
    # Every route is unfulfillable: fall back to a plain level-up, exactly
    # like `_sanitize_unfulfillable_evolutions` did, rather than leaving a
    # species with no achievable evolution at all.
    e = dict(bucket[0])
    e["trigger"] = "level-up"
    if not e.get("min_level"):
        e["min_level"] = _bst_based_evo_level(e["to_id"], species_by_id)
    for f in _UNFULFILLABLE_FILTER_FIELDS:
        e[f] = False if f in ("needs_multiplayer", "needs_rain", "turn_upside_down", "near_special_rock") else None
    return [e]


_EVOLUTION_CONDITION_DEFAULTS: dict[str, Any] = {
    "min_level": None, "min_happiness": None, "time_of_day": None, "item": None,
    "held_item": None, "known_move": None, "known_move_type": None, "gender": None,
    "needs_rain": False, "relative_physical_stats": None, "party_species": None,
    "party_type": None, "trade_species": None, "location": None,
    "near_special_rock": False, "min_beauty": None, "min_steps": None,
    "min_damage_taken": None, "min_move_count": None, "needs_multiplayer": False,
    "turn_upside_down": False, "region": None,
}


def build_evolution_chains_from_pokeapi_cache(
    cache_dir: Path, species_records: list[dict],
) -> dict[str, list[dict]]:
    """`{from_id: [EvolutionCondition-shaped dict, ...]}` from
    `evolution_cache.json`, patched with `_EVOLUTION_OVERRIDES` and run
    through `_finalize_evolution_bucket` -- see both this module's own
    docstring above and the `EvolutionCondition.is_fulfillable` /
    `_sanitize_unfulfillable_evolutions` cross-references there for why
    this isn't just `_load_cache_json(cache_dir, "evolution_cache.json")`
    verbatim."""
    raw = _load_cache_json(cache_dir, "evolution_cache.json")
    valid_ids = {rec["id"] for rec in species_records}
    species_by_id = {rec["id"]: rec for rec in species_records}

    chains: dict[str, list[dict]] = {}
    for from_id_str, edges in raw.items():
        from_id = int(from_id_str)
        if from_id not in valid_ids:
            continue
        bucket: list[dict] = []
        for edge in edges:
            if edge["to_id"] not in valid_ids:
                continue
            full = dict(_EVOLUTION_CONDITION_DEFAULTS)
            full.update(edge)
            override = _EVOLUTION_OVERRIDES.get((from_id, edge["to_id"], edge.get("trigger", "level-up")))
            if override:
                full.update(override)
            bucket.append(full)
        if bucket:
            chains[from_id_str] = _finalize_evolution_bucket(bucket, species_by_id)
    return chains


def build_moves_data_from_pokeapi_cache(cache_dir: Path) -> dict[str, dict]:
    """`data/moves_data.json` -- `{slug: MoveSpec.to_dict()}` -- directly
    from `moves_cache.json`. `MoveSpec.from_pokeapi_response` (move.py) was
    already written field-for-field against exactly this cache's shape
    (see its own docstring), so this is the ETL call site that shape was
    always meant to feed; nothing here reinterprets move data itself."""
    from pokemon.battle.move import MoveSpec

    raw = _load_cache_json(cache_dir, "moves_cache.json")
    out: dict[str, dict] = {}
    for slug, move_dict in raw.items():
        move_dict = dict(move_dict)
        move_dict.setdefault("name", slug)
        out[slug] = MoveSpec.from_pokeapi_response(move_dict).to_dict()
    return out


def build_move_flavor_text_from_pokeapi_cache(cache_dir: Path) -> dict[str, str]:
    """`data/move_flavor_text.json` -- `{slug: description}` for
    `pokemon.dex.moves_repo.MoveFlavorTextRepository`. `moves_cache.json`
    already carries a `description` per move (the same PokeAPI flavor text
    `move_descs.json`'s richer per-name cache also has, keyed differently --
    see `run_pokeapi_cache_pass`'s docstring for why both are written)."""
    raw = _load_cache_json(cache_dir, "moves_cache.json")
    return {slug: d["description"] for slug, d in raw.items() if d.get("description")}


def build_learnsets_from_pokeapi_cache(cache_dir: Path, species_records: list[dict]) -> dict[str, dict]:
    """`{dex_id: {level_moves, egg_moves, tm_moves, tutor_moves}}` from
    `pokedex_cache.json`'s own per-species `level_moves` / `moves_pool`.

    The original monolith's `_build_entry` (contore.py) builds exactly two
    buckets per species -- `moves_pool` (machine + tutor moves, NOT egg
    moves -- see its `elif method in ("machine", "tutor")` branch) and
    `egg_moves` -- with no further TM/Tutor distinction ever kept. Track
    B's newer `Learnset` dataclass added that distinction for nicer
    `/pokemon` display, but every current call site immediately re-unions
    `tm_moves | tutor_moves` back together (`species_info.py`'s "TM" label,
    `move_lookup.py`'s "all learnable moves" set) -- so putting the whole
    machine+tutor pool in `tm_moves` and leaving `tutor_moves` empty is
    functionally identical to a real split, not a loss of information any
    current code can observe.

    `egg_moves` is a known gap: this specific `pokedex_cache.json` export
    has no `egg_moves` key on ANY of its 721 entries, even though
    `_build_entry` builds one -- see `run_pokeapi_cache_pass`'s docstring.
    Every species gets `egg_moves: []` until a cache with that field is
    provided; egg-move breeding inheritance has nothing to inherit until
    then (`pokemon.commands.breeding.inherit_egg_moves` degrades to "no
    egg moves available" rather than crashing, so this is silent unless
    you go looking for it -- flagging it here so it isn't a surprise).
    """
    raw = _load_cache_json(cache_dir, "pokedex_cache.json")
    valid_ids = {rec["id"] for rec in species_records}

    out: dict[str, dict] = {}
    for entry in raw:
        dex_id = entry["id"]
        if dex_id not in valid_ids:
            continue
        level_moves = [[lvl, mv] for lvl, mv in entry.get("level_moves", [])]
        level_slugs = {mv for _, mv in level_moves}
        egg_moves = sorted(set(entry.get("egg_moves", [])))
        moves_pool = set(entry.get("moves_pool", [])) - level_slugs - set(egg_moves)
        out[str(dex_id)] = {
            "level_moves": sorted(level_moves),
            "egg_moves": egg_moves,
            "tm_moves": sorted(moves_pool),
            "tutor_moves": [],
        }
    return out


def build_abilities_slugs_from_pokeapi_cache(species_records: list[dict]) -> list[str]:
    """`data/abilities.json` -- the flat list of every ability slug
    `AbilityRegistry.load(known_slugs)` needs (see `bootstrap.py`) --
    collected from every species/form/mega-form's `abilities` list rather
    than from a dedicated abilities cache (none of the five files provided
    is one; PokeAPI's ability data itself is almost entirely battle
    mechanics anyway, which `pokemon.battle.ability`'s hand-written classes
    already own -- this list only needs to answer "is this slug real")."""
    slugs: set[str] = set()
    for rec in species_records:
        for group in (rec["abilities"], *(f["abilities"] for f in rec["forms"]),
                      *(f["abilities"] for f in rec["mega_forms"])):
            for a in group:
                slugs.add(a["name"])
    return sorted(slugs)


def run_pokeapi_cache_pass(cache_dir: Path) -> None:
    """The preferred data pipeline: rebuilds every `data/*.json` the bot
    needs (except `generation_availability.json`, cross-gen metadata with
    no PokeAPI-cache equivalent -- run `run_showdown_pass` too if you want
    that) from a previously-saved export of the original monolith's own
    PokeAPI response caches, with zero network access needed.

    This exists alongside (and is now preferred over) the Showdown pass
    above for one reason: those caches ARE the original bot's actual,
    already-correct data -- catch rates, growth rates, friendship, flavor
    text, full per-species movepools -- collected the exact same way
    `_build_entry`/`_ensure_move_cached` always did, not re-derived from a
    different simulator's data model and hand-mapped field-by-field. Where
    Showdown's data and this data would ever disagree on a Gen 1-6 detail,
    this is the one this specific bot has always actually run on.

    Known gap in the currently-provided cache: `pokedex_cache.json` has no
    `egg_moves` field on any entry (see `build_learnsets_from_pokeapi_cache`).
    Re-running this pass against a cache export that has it will pick it up
    automatically -- no code change needed, just fresher input files.
    """
    print(f"[pokedex_builder] PokeAPI cache pass, reading from {cache_dir}")

    species_records = build_pokedex_from_pokeapi_cache(cache_dir)
    print(f"[pokedex_builder]   {len(species_records)} species (#1-{NATIONAL_DEX_MAX})")

    evolutions = build_evolution_chains_from_pokeapi_cache(cache_dir, species_records)
    moves_data = build_moves_data_from_pokeapi_cache(cache_dir)
    print(f"[pokedex_builder]   {len(moves_data)} moves")
    move_flavor = build_move_flavor_text_from_pokeapi_cache(cache_dir)
    learnsets = build_learnsets_from_pokeapi_cache(cache_dir, species_records)
    print(f"[pokedex_builder]   learnsets built for {len(learnsets)} species "
          f"({sum(1 for v in learnsets.values() if not v['egg_moves'])} with no egg moves recorded)")
    ability_slugs = build_abilities_slugs_from_pokeapi_cache(species_records)
    items_meta = _load_cache_json(cache_dir, "items_cache.json")
    move_descs = _load_cache_json(cache_dir, "move_descs.json", required=False)

    _write_json(DATA_DIR / "pokedex.json", species_records)
    _write_json(DATA_DIR / "evolution_chains.json", evolutions)
    _write_json(DATA_DIR / "moves_data.json", moves_data)
    _write_json(DATA_DIR / "move_flavor_text.json", move_flavor)
    _write_json(DATA_DIR / "learnsets.json", learnsets)
    _write_json(DATA_DIR / "abilities.json", ability_slugs)
    _write_json(DATA_DIR / "items_meta.json", items_meta)
    if move_descs is not None:
        _write_json(DATA_DIR / "move_descs.json", move_descs)
    print("[pokedex_builder] PokeAPI cache pass complete.")
    return species_records


def run_generation_availability_pass(showdown_dir: Path, species_records: list[dict]) -> None:
    """Just the cross-gen slice of `run_showdown_pass`, for when species/move
    data itself is coming from `run_pokeapi_cache_pass` instead -- calling
    the full `run_showdown_pass` in that combination would overwrite the
    cache pass's (preferred) `pokedex.json`/`evolution_chains.json`/etc.
    with its own, inferior versions right after writing them."""
    print(f"[pokedex_builder] Showdown availability pass, reading from {showdown_dir}")
    availability = build_generation_availability(showdown_dir, species_records)
    print(f"[pokedex_builder]   {len(availability['moves'])} moves, "
          f"{len(availability['abilities'])} abilities dated")
    _write_json(DATA_DIR / "generation_availability.json", availability)
    print("[pokedex_builder] Showdown availability pass complete.")


def run_showdown_pass(showdown_dir: Path) -> None:
    print(f"[pokedex_builder] Showdown pass, reading from {showdown_dir}")
    species_records, _ = build_pokedex_from_showdown(showdown_dir)
    print(f"[pokedex_builder]   {len(species_records)} species (#1-{NATIONAL_DEX_MAX})")

    availability = build_generation_availability(showdown_dir, species_records)
    print(f"[pokedex_builder]   {len(availability['moves'])} moves, "
          f"{len(availability['abilities'])} abilities dated")

    evolutions = build_evolution_chains(showdown_dir, species_records)
    items_meta = build_items_meta_from_showdown(showdown_dir)
    learnsets = build_learnsets_from_showdown(showdown_dir, species_records)
    print(f"[pokedex_builder]   learnsets built for {len(learnsets)} species "
          f"({sum(1 for v in learnsets.values() if not v['level_moves'])} with no gen<=6 level-up moves found)")

    abilities_raw = _load_showdown_json(showdown_dir, "Abilities")
    ability_slugs = sorted({_slugify(v.get("name", k)) for k, v in abilities_raw.items()})

    _write_json(DATA_DIR / "pokedex.json", species_records)
    _write_json(DATA_DIR / "generation_availability.json", availability)
    _write_json(DATA_DIR / "evolution_chains.json", evolutions)
    _write_json(DATA_DIR / "items_meta.json", items_meta)
    _write_json(DATA_DIR / "abilities.json", ability_slugs)
    _write_json(DATA_DIR / "learnsets.json", learnsets)
    print("[pokedex_builder] Showdown pass complete.")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"[pokedex_builder]   wrote {path.relative_to(DATA_DIR.parent)} ({path.stat().st_size:,} bytes)")


# ---------------------------------------------------------------------------
# PokeAPI pass (online enrichment) — adapted from the original monolith's
# proven `_fetch_move_from_api` / `_fetch_ability_from_api` /
# `_fetch_item_from_api` / `_build_entry` functions. Needs network access to
# pokeapi.co, which this sandbox doesn't have, but is otherwise unchanged
# logic — just restructured to *write files* instead of populating
# in-process caches.
# ---------------------------------------------------------------------------

POKEAPI_BASE = "https://pokeapi.co/api/v2"


async def _fetch_json(session, url: str) -> dict | None:
    import asyncio as _asyncio

    for attempt in range(3):
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            pass
        await _asyncio.sleep(1.5 * (attempt + 1))
    return None


async def enrich_species_from_pokeapi(species_records: list[dict]) -> None:
    """Mutates `species_records` in place, filling every economy-only field
    Showdown couldn't provide, and clearing `needs_pokeapi_enrichment`."""
    try:
        from aiohttp import ClientSession
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("aiohttp is required for the PokeAPI pass: pip install aiohttp") from exc

    async with ClientSession() as session:
        for i, rec in enumerate(species_records, 1):
            species = await _fetch_json(session, f"{POKEAPI_BASE}/pokemon-species/{rec['id']}")
            pokemon = await _fetch_json(session, f"{POKEAPI_BASE}/pokemon/{rec['id']}")
            if species:
                rec["catch_rate"] = species.get("capture_rate", rec["catch_rate"])
                rec["growth_rate"] = (species.get("growth_rate") or {}).get("name", rec["growth_rate"])
                rec["base_friendship"] = species.get("base_happiness", rec["base_friendship"])
                rec["hatch_counter"] = species.get("hatch_counter", rec["hatch_counter"])
                rec["genus"] = next(
                    (g["genus"] for g in species.get("genera", []) if g["language"]["name"] == "en"),
                    rec["genus"],
                )
                rec["flavor_text"] = _pick_flavor_text(species.get("flavor_text_entries", [])) or rec["flavor_text"]
                is_legendary = bool(species.get("is_legendary"))
                is_mythical = bool(species.get("is_mythical"))
                if is_mythical:
                    rec["rarity"] = "mythical"
                elif is_legendary:
                    rec["rarity"] = "legendary"
            if pokemon:
                rec["base_experience"] = pokemon.get("base_experience") or rec["base_experience"]
                rec["effort_yield"] = {
                    s["stat"]["name"]: s["effort"] for s in pokemon.get("stats", []) if s["effort"] > 0
                }
            rec["needs_pokeapi_enrichment"] = False
            if i % 25 == 0:
                print(f"[pokedex_builder]   species enrichment {i}/{len(species_records)}")


def _pick_flavor_text(entries: list[dict]) -> str | None:
    for e in entries:
        if e["language"]["name"] == "en":
            return e["flavor_text"].replace("\n", " ").replace("\x0c", " ")
    return None


# The exact move-cache shape the original bot's `_fetch_move_from_api`
# produced, preserved verbatim so `MoveSpec.from_pokeapi_response` (frozen
# contract, `pokemon/battle/move.py`) can consume it unchanged.
_POKEAPI_AILMENT_MAP = {
    "burn": "burn", "paralysis": "paralysis", "sleep": "sleep",
    "freeze": "freeze", "poison": "poison", "badly-poisoned": "bad_poison",
    "confusion": "confusion",
}


async def _fetch_move_from_api(session, move_name: str) -> dict:
    data = await _fetch_json(session, f"{POKEAPI_BASE}/move/{move_name}")
    if not data:
        return {"name": move_name}
    ailment_raw = ((data.get("meta") or {}).get("ailment") or {}).get("name", "none")
    return {
        "name": data["name"],
        "display_name": None,  # filled from pokemon.dex.moves_repo.display_name at load time
        "generation": (data.get("generation") or {}).get("name", "generation-i"),
        "description": _pick_move_flavor_text(data.get("flavor_text_entries", [])),
        "type": data["type"]["name"].title(),
        "power": data.get("power"),
        "accuracy": data.get("accuracy"),
        "pp": data.get("pp", 5),
        "category": data["damage_class"]["name"],
        "priority": data.get("priority", 0),
        "ailment": _POKEAPI_AILMENT_MAP.get(ailment_raw),
        "ailment_chance": (data.get("meta") or {}).get("ailment_chance", 0),
        "drain": (data.get("meta") or {}).get("drain", 0),
        "healing": (data.get("meta") or {}).get("healing", 0),
        "flinch_chance": (data.get("meta") or {}).get("flinch_chance", 0),
        "crit_rate": (data.get("meta") or {}).get("crit_rate", 0),
        "min_hits": (data.get("meta") or {}).get("min_hits") or 1,
        "max_hits": (data.get("meta") or {}).get("max_hits") or 1,
        "stat_changes": [
            {"stat": sc["stat"]["name"], "change": sc["change"]} for sc in data.get("stat_changes", [])
        ],
        "stat_chance": (data.get("meta") or {}).get("stat_chance", 0),
        "always_hits": data["accuracy"] is None,
        "is_ohko": (data.get("meta") or {}).get("category", {}).get("name") == "ohko",
        "target": data["target"]["name"],
        "recharge": any(f["name"] == "recharge" for f in data.get("flags", [])),
        "makes_contact": any(f["name"] == "contact" for f in data.get("flags", [])),
        "powder": any(f["name"] == "powder" for f in data.get("flags", [])),
        "punch": any(f["name"] == "punch" for f in data.get("flags", [])),
        "bite": any(f["name"] == "bite" for f in data.get("flags", [])),
        "bullet": any(f["name"] == "bullet" for f in data.get("flags", [])),
        "sound": any(f["name"] == "sound" for f in data.get("flags", [])),
        "pulse": any(f["name"] == "pulse" for f in data.get("flags", [])),
        "dance": any(f["name"] == "dance" for f in data.get("flags", [])),
    }


def _pick_move_flavor_text(entries: list[dict]) -> str | None:
    for e in entries:
        if e["language"]["name"] == "en":
            return e["flavor_text"].replace("\n", " ").replace("\x0c", " ")
    return None


async def fetch_all_moves(move_names: set[str]) -> dict[str, dict]:
    from pokemon.battle.move import MoveSpec  # frozen contract; safe read-only use
    from aiohttp import ClientSession

    out: dict[str, dict] = {}
    async with ClientSession() as session:
        for i, name in enumerate(sorted(move_names), 1):
            raw = await _fetch_move_from_api(session, name)
            spec = MoveSpec.from_pokeapi_response(raw)
            out[name] = spec.to_dict()
            if i % 50 == 0:
                print(f"[pokedex_builder]   moves {i}/{len(move_names)}")
    return out


async def fetch_all_ability_flavor_text(ability_slugs: list[str]) -> dict[str, str]:
    from aiohttp import ClientSession

    out: dict[str, str] = {}
    async with ClientSession() as session:
        for i, slug in enumerate(ability_slugs, 1):
            data = await _fetch_json(session, f"{POKEAPI_BASE}/ability/{slug}")
            if data:
                text = next(
                    (e.get("short_effect") or e.get("effect") for e in data.get("effect_entries", [])
                     if e["language"]["name"] == "en"),
                    None,
                )
                if not text:
                    text = next(
                        (e["flavor_text"].replace("\n", " ") for e in data.get("flavor_text_entries", [])
                         if e["language"]["name"] == "en"),
                        None,
                    )
                if text:
                    out[slug] = text.strip()
            if i % 50 == 0:
                print(f"[pokedex_builder]   abilities {i}/{len(ability_slugs)}")
    return out


async def fetch_all_move_flavor_text(move_names: set[str]) -> dict[str, str]:
    from aiohttp import ClientSession

    out: dict[str, str] = {}
    async with ClientSession() as session:
        for name in sorted(move_names):
            raw = await _fetch_move_from_api(session, name)
            if raw.get("description"):
                out[name] = raw["description"]
    return out


async def run_pokeapi_pass() -> None:
    pokedex_path = DATA_DIR / "pokedex.json"
    if not pokedex_path.exists():
        print("[pokedex_builder] No data/pokedex.json yet — run the Showdown pass first.")
        return

    with pokedex_path.open(encoding="utf-8") as fh:
        species_records = json.load(fh)

    print(f"[pokedex_builder] PokeAPI pass: enriching {len(species_records)} species...")
    await enrich_species_from_pokeapi(species_records)
    _write_json(pokedex_path, species_records)

    move_names: set[str] = set()
    for rec in species_records:
        for _, name in (rec.get("level_moves") or []):
            move_names.add(name)
    # `pokedex.json` from the Showdown pass doesn't carry movepools today
    # (that lives in Showdown's Learnsets.json, consumed separately by
    # whichever module builds wild movesets); if this list is empty, fall
    # back to every move known to the frozen MoveRegistry's data file so we
    # still refresh flavor text/mechanics for the existing move dex.
    if not move_names:
        moves_data_path = DATA_DIR / "moves_data.json"
        if moves_data_path.exists():
            with moves_data_path.open(encoding="utf-8") as fh:
                move_names = set(json.load(fh).keys())

    if move_names:
        print(f"[pokedex_builder] Fetching {len(move_names)} moves from PokeAPI...")
        moves_data = await fetch_all_moves(move_names)
        _write_json(DATA_DIR / "moves_data.json", moves_data)

        flavor = {slug: d["description"] for slug, d in moves_data.items() if d.get("description")}
        _write_json(DATA_DIR / "move_flavor_text.json", flavor)

    abilities_path = DATA_DIR / "abilities.json"
    if abilities_path.exists():
        with abilities_path.open(encoding="utf-8") as fh:
            ability_slugs = json.load(fh)
        print(f"[pokedex_builder] Fetching flavor text for {len(ability_slugs)} abilities...")
        ability_flavor = await fetch_all_ability_flavor_text(ability_slugs)
        _write_json(DATA_DIR / "ability_flavor_text.json", ability_flavor)

    print("[pokedex_builder] PokeAPI pass complete.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pokeapi-cache", type=Path, default=None,
                         help="Directory containing the original bot's pokedex_cache.json / "
                              "moves_cache.json / evolution_cache.json / items_cache.json / "
                              "move_descs.json. Preferred pipeline -- see module docstring.")
    parser.add_argument("--showdown-json", type=Path, default=None,
                         help="Directory of JSON produced by tools/showdown_extract.js. Only "
                              "needed for generation_availability.json (cross-gen data) if "
                              "--pokeapi-cache is also given; a full standalone pass otherwise.")
    parser.add_argument("--pokeapi", action="store_true",
                         help="Run the online PokeAPI enrichment pass (needs network + aiohttp) "
                              "to refresh the caches --pokeapi-cache reads, for a future run.")
    args = parser.parse_args(argv)

    if not args.showdown_json and not args.pokeapi and not args.pokeapi_cache:
        parser.error("Nothing to do — pass --pokeapi-cache <dir>, --showdown-json <dir>, and/or --pokeapi")

    cache_species_records = None
    if args.pokeapi_cache:
        cache_species_records = run_pokeapi_cache_pass(args.pokeapi_cache)
    if args.showdown_json and cache_species_records is not None:
        # Cache pass already wrote the preferred pokedex.json/etc.; only
        # the cross-gen slice is still worth pulling from Showdown.
        run_generation_availability_pass(args.showdown_json, cache_species_records)
    elif args.showdown_json:
        run_showdown_pass(args.showdown_json)
    if args.pokeapi:
        asyncio.run(run_pokeapi_pass())
    return 0


if __name__ == "__main__":
    sys.exit(main())

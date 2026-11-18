"""Patches real per-generation move/ability legality onto the frozen
`GenerationRuleset`s.

`pokemon/generations/ruleset.py` is frozen (see `CONTRACT.md`) and ships
`GEN_1..GEN_6` with EMPTY `excluded_moves`/`excluded_abilities` -- i.e. as
originally scaffolded, every generation allows every move/ability the bot
knows about, and only the Pokédex cap (`max_dex_number`) actually gates
anything. This module is what turns that into a real legality check, using
`data/generation_availability.json` (built by
`pokemon.dex.pokedex_builder.build_generation_availability`, from Pokémon
Showdown's learnset/pokédex data) as ground truth for "which generation did
this move/ability first exist in".

This is additive, not a contract edit: `ALL_GENERATIONS` is a plain mutable
module-level dict and `GenerationRuleset` is a normal frozen dataclass, so
building new instances via `dataclasses.replace()` and patching them into
the dict (and back onto the module's own `GEN_n` / `DEFAULT_GENERATION`
names, so code that imported those directly also sees the real data) needs
no `CONTRACT_CHANGES.md` entry -- see CONTRACT.md's "additive changes"
rule and TRACK_B_HANDOFF.md §1/§3.

One deliberate departure from that handoff note's original phrasing
("relative to gen 6's full roster"): that phrasing assumed Showdown's data
tops out at Gen 6. It doesn't -- `pokemon-showdown-master` is the current
upstream repo, so `generation_availability.json`'s moves/abilities are
dated across Gen 1-9 (a Gen 1 species like Bulbasaur can learn a Gen 8 TM
move in Showdown's data, since Showdown represents "what can this species
currently learn," not "what existed when this species debuted"). Since
this bot's engine only implements Gen VI mechanics, GEN_6 needs the exact
same "exclude anything introduced after my generation number" treatment as
GEN_1-5, not a free pass -- otherwise selecting Gen VI would wrongly legalize
Gen VII-IX-only moves/abilities (Tera Blast, Grassy Glide, Astral Barrage,
...) that the bot has no battle mechanics for at all. See
TRACK_B_HANDOFF_2.md for the full writeup of why this changed.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from pokemon.generations import ruleset as _ruleset_module
from pokemon.generations.ruleset import ALL_GENERATIONS, GenerationRuleset

DEFAULT_AVAILABILITY_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "generation_availability.json"
)

_loaded = False


def _excluded_for(min_gen_by_slug: dict[str, int], generation_number: int) -> frozenset[str]:
    """Every slug whose earliest known generation is later than
    `generation_number` -- i.e. it doesn't exist yet under that ruleset."""
    return frozenset(
        slug for slug, min_gen in min_gen_by_slug.items() if min_gen > generation_number
    )


def load_availability(path: Path | str = DEFAULT_AVAILABILITY_PATH) -> dict:
    path = Path(path)
    if not path.exists():
        return {"species": {}, "moves": {}, "abilities": {}}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def apply(availability: dict | None = None) -> None:
    """Patch real `excluded_moves`/`excluded_abilities` onto every ruleset
    in `ALL_GENERATIONS` -- the sanctioned access pattern per ruleset.py's
    own docstring ("resolve via ALL_GENERATIONS[n] at point of use") and
    what `ServerPokemonRules.battle_ruleset`/`.dex_ruleset` already use, so
    this is the object every real call site sees.

    This also rebinds `pokemon.generations.ruleset`'s own module-level
    `GEN_1..GEN_6`/`DEFAULT_GENERATION` attributes for anyone doing
    `import pokemon.generations.ruleset as ruleset; ...ruleset.GEN_3...`
    (attribute access resolves at call time, so this works). It can NOT
    retroactively fix a `from pokemon.generations.ruleset import GEN_3`
    done *before* `ensure_loaded()` ran -- that copies the pre-patch object
    reference into the importing module's namespace, same as any Python
    monkey-patch limitation. Don't do that style of import for these names;
    use `ALL_GENERATIONS[n]` (after `ensure_loaded()`) instead.

    Species-level gating (`max_dex_number`) is untouched -- it was already
    real and doesn't need this data (see `species.py`'s
    `for_ruleset`/`allowed_ids_for_ruleset`, which only need
    `generation_introduced` + `max_dex_number`, both already correct
    without this module).

    Safe to call more than once (re-derives and re-applies fresh from
    `availability` / disk each time; it doesn't compound). Prefer
    `ensure_loaded()` at most call sites -- this is exposed separately for
    tests / a future `/deploy pull`-triggered hot-reload of the data files.
    """
    if availability is None:
        availability = load_availability()

    move_min_gen: dict[str, int] = availability.get("moves", {})
    ability_min_gen: dict[str, int] = availability.get("abilities", {})

    for number, gen_ruleset in list(ALL_GENERATIONS.items()):
        patched = replace(
            gen_ruleset,
            excluded_moves=_excluded_for(move_min_gen, number),
            excluded_abilities=_excluded_for(ability_min_gen, number),
        )
        ALL_GENERATIONS[number] = patched
        attr_name = f"GEN_{number}"
        if getattr(_ruleset_module, attr_name, None) is not None:
            setattr(_ruleset_module, attr_name, patched)

    # DEFAULT_GENERATION is a direct object reference (not a dict lookup),
    # captured at ruleset.py's own import time -- repoint it at whichever
    # patched instance now shares its generation number.
    _ruleset_module.DEFAULT_GENERATION = ALL_GENERATIONS[_ruleset_module.DEFAULT_GENERATION.number]


def ensure_loaded() -> None:
    """Idempotent entry point -- call this from any command cog that
    touches generation logic (`pokemon/commands/config.py`,
    `pokemon/commands/spawning.py`, battle-team validation, ...) before
    reading `ServerPokemonRules`/`GenerationRuleset`. First call does the
    real work; every call after that is a no-op."""
    global _loaded
    if _loaded:
        return
    apply()
    _loaded = True


def is_loaded() -> bool:
    return _loaded


def summary() -> dict[int, dict[str, int]]:
    """Small debugging/health-check helper -- e.g. for a future
    `/pokemon config generation` reply confirming real data is loaded, or
    an admin diagnostics command. Not used internally."""
    ensure_loaded()
    out: dict[int, dict[str, int]] = {}
    for number, gen_ruleset in ALL_GENERATIONS.items():
        out[number] = {
            "excluded_moves": len(gen_ruleset.excluded_moves),
            "excluded_abilities": len(gen_ruleset.excluded_abilities),
        }
    return out

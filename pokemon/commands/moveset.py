"""Shared "give this Pokémon a starting moveset" logic.

The monolith had three near-identical copies of this (`assign_initial_moves`
for hatching/gifting, `_build_wild_move_list` for wild spawns, plus a third
partial one inlined in the starter-picker) that had drifted slightly out of
sync with each other. Per the "turn repeated code into a utility function"
instruction, this is the one copy every caller (`spawning.py` today;
`breeding.py`/a starter-pick command later) should use.

Also owns the one soft-dependency point onto the frozen
`pokemon.battle.registry.MoveRegistry` that `pokemon/commands` needs (for a
move's PP): that registry is normally built once at bot startup from
`data/moves_data.json`, which itself only exists after the *online* PokeAPI
enrichment pass runs (see `pokemon/dex/pokedex_builder.py` -- this sandbox
never had network access to do that). Every function here degrades
gracefully (a documented fallback PP, "assume damaging") when that data
isn't loaded yet, exactly like the monolith's own `_move_cache.get(name, {})`
pattern did before the cache was warm.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pokemon.battle.move import MoveCategory
from pokemon.battle.registry import MoveRegistry
from pokemon.dex.learnsets_repo import get_default_repository as get_learnsets

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# The monolith's `_move_cache.get(name, {}).get("pp", 35)` fallback,
# preserved verbatim: 35 is the PP of an "average" damaging move (used
# whenever `data/moves_data.json` hasn't been built/loaded yet).
_FALLBACK_PP = 35

_registry: MoveRegistry | None = None
_registry_load_attempted = False


def get_move_registry() -> MoveRegistry:
    """Process-wide `MoveRegistry`, loaded lazily from
    `data/moves_data.json` on first use. Safe to call before that file
    exists -- an empty, never-populated registry just means every PP/
    "is damaging" lookup below falls back to its documented default,
    the same degraded-but-functional state the monolith was in before its
    own move cache warmed up.
    """
    global _registry, _registry_load_attempted
    if _registry is None:
        _registry = MoveRegistry()
    if not _registry_load_attempted:
        _registry_load_attempted = True
        data_path = DATA_DIR / "moves_data.json"
        if data_path.exists():
            _registry.load(data_path)
    return _registry


def move_pp(move_slug: str) -> int:
    registry = get_move_registry()
    try:
        return registry.spec(move_slug).pp
    except KeyError:
        return _FALLBACK_PP


def move_display_name(move_slug: str) -> str:
    """A move slug's display name (e.g. `"vinewhip"` -> "Vine Whip").
    Prefers the real name from `MoveRegistry` when loaded; falls back to
    `pokemon.dex.moves_repo.display_name` (a curated override list for the
    handful of names that don't title-case cleanly -- "U-turn",
    "King's Shield", etc. -- since `data/moves_data.json` doesn't exist in
    every environment yet; see this module's docstring)."""
    registry = get_move_registry()
    try:
        return registry.spec(move_slug).display_name
    except KeyError:
        from pokemon.dex.moves_repo import display_name
        return display_name(move_slug)


def move_summary(move_slug: str) -> dict:
    """The handful of fields move-picker UIs (`relearn`, TM teaching, the
    level-up learn prompt) all display per move: display name, type,
    category, base power, and PP. Every field degrades to a placeholder
    when `data/moves_data.json` isn't loaded, so a picker can still render
    (just without flavor) instead of crashing -- one place to hold that
    fallback logic instead of every picker view re-deriving it."""
    registry = get_move_registry()
    try:
        spec = registry.spec(move_slug)
        return {
            "display_name": spec.display_name, "type_name": spec.type_name,
            "category": spec.category.value, "base_power": spec.base_power, "pp": spec.pp,
        }
    except KeyError:
        return {
            "display_name": move_display_name(move_slug), "type_name": None,
            "category": "status", "base_power": None, "pp": _FALLBACK_PP,
        }


def is_damaging_move(move_slug: str) -> bool:
    """Mirrors the monolith's `_move_is_damaging`: unknown moves are
    assumed damaging (the original's `{}.get("category") != "status"`
    fallback), since a totally-unrecognized slug is far more likely to be
    a data gap than an intentional status move."""
    registry = get_move_registry()
    try:
        return registry.spec(move_slug).category != MoveCategory.STATUS
    except KeyError:
        return True


@dataclass(frozen=True, slots=True)
class MoveSlot:
    name: str
    pp: int
    max_pp: int


def ensure_attacking_move(selected: list[str], available: list[str]) -> list[str]:
    """If nothing in `selected` actually deals damage, swap in the
    highest-priority damaging move from `available` (last = learned most
    recently) so a wild/starter/bred Pokémon is never stuck with an
    all-status moveset. Ported verbatim from the monolith's
    `_ensure_attacking_move`."""
    if any(is_damaging_move(m) for m in selected):
        return selected
    damaging_learnable = [n for n in dict.fromkeys(available) if is_damaging_move(n)]
    if not damaging_learnable:
        return selected
    replacement = damaging_learnable[-1]
    if replacement in selected:
        return selected
    if len(selected) >= 4:
        return selected[1:] + [replacement]
    return selected + [replacement]


def roll_initial_moveset(species_id: int, level: int) -> list[MoveSlot]:
    """The moveset a newly-encountered/hatched/gifted Pokémon starts with:
    its 4 most-recently-learnable level-up moves at `level` (fewer if it
    hasn't learned 4 yet), falling back to Tackle if it has no gen<=6
    level-up moves on file at all, then guaranteeing at least one damaging
    move via `ensure_attacking_move`. Matches the monolith's
    `assign_initial_moves`/`_build_wild_move_list` behavior exactly, just
    reading from `LearnsetRepository` instead of a live PokeAPI-backed
    per-species dict.
    """
    learnable = get_learnsets().get(species_id).moves_at_or_below(level)
    if not learnable:
        learnable = ["tackle"]
    selected = list(dict.fromkeys(learnable))[-4:]
    selected = ensure_attacking_move(selected, learnable)
    return [MoveSlot(name=slug, pp=move_pp(slug), max_pp=move_pp(slug)) for slug in selected]

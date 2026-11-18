"""Type effectiveness. Owned by Track C.

The base chart below is the full 18-type Gen VI chart (ported verbatim
from the monolith's `TYPE_CHART`). Earlier generations differ in a handful
of specific matchups (no Fairy/Steel/Dark type before their introduction,
Bug was super-effective on Poison pre-Gen6, Ghost didn't resist Psychic
pre-Gen2, etc.) -- rather than duplicating the whole chart per generation,
older `GenerationRuleset`s can list just their *deltas* from this Gen VI
baseline in `type_chart_overrides`, keyed as `(attacking_type, defending_type)
-> multiplier`. See generations/ruleset.py.

Nothing in this file assumes a specific generation is active except by
accepting an optional `ruleset` argument that defaults to Gen VI (today's
only fully-implemented ruleset), so existing single-generation servers see
zero behavioural change.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..generations.ruleset import GenerationRuleset

ALL_TYPES: tuple[str, ...] = (
    "Normal", "Fire", "Water", "Electric", "Grass", "Ice", "Fighting", "Poison",
    "Ground", "Flying", "Psychic", "Bug", "Rock", "Ghost", "Dragon", "Dark",
    "Steel", "Fairy",
)

# attacking type -> {defending type: multiplier}; omitted pairs are 1.0x.
TYPE_CHART: dict[str, dict[str, float]] = {
    "Normal":   {"Rock": 0.5, "Steel": 0.5, "Ghost": 0.0},
    "Fire":     {"Grass": 2.0, "Water": 0.5, "Fire": 0.5, "Ice": 2.0, "Steel": 2.0, "Bug": 2.0, "Rock": 0.5, "Dragon": 0.5},
    "Water":    {"Fire": 2.0, "Grass": 0.5, "Water": 0.5, "Ground": 2.0, "Rock": 2.0, "Dragon": 0.5},
    "Grass":    {"Water": 2.0, "Fire": 0.5, "Grass": 0.5, "Ground": 2.0, "Flying": 0.5, "Poison": 0.5, "Rock": 2.0, "Bug": 0.5, "Dragon": 0.5, "Steel": 0.5},
    "Electric": {"Water": 2.0, "Flying": 2.0, "Electric": 0.5, "Ground": 0.0, "Grass": 0.5, "Dragon": 0.5},
    "Ice":      {"Grass": 2.0, "Ground": 2.0, "Flying": 2.0, "Dragon": 2.0, "Fire": 0.5, "Water": 0.5, "Ice": 0.5, "Steel": 0.5},
    "Fighting": {"Normal": 2.0, "Ice": 2.0, "Rock": 2.0, "Steel": 2.0, "Dark": 2.0, "Flying": 0.5, "Poison": 0.5, "Bug": 0.5, "Psychic": 0.5, "Fairy": 0.5, "Ghost": 0.0},
    "Ground":   {"Steel": 2.0, "Fire": 2.0, "Electric": 2.0, "Poison": 2.0, "Rock": 2.0, "Flying": 0.0, "Grass": 0.5, "Bug": 0.5},
    "Flying":   {"Grass": 2.0, "Fighting": 2.0, "Bug": 2.0, "Electric": 0.5, "Rock": 0.5, "Steel": 0.5},
    "Psychic":  {"Fighting": 2.0, "Poison": 2.0, "Psychic": 0.5, "Steel": 0.5, "Dark": 0.0},
    "Bug":      {"Grass": 2.0, "Psychic": 2.0, "Dark": 2.0, "Fire": 0.5, "Fighting": 0.5, "Flying": 0.5, "Ghost": 0.5, "Steel": 0.5, "Poison": 0.5, "Fairy": 0.5},
    "Rock":     {"Fire": 2.0, "Ice": 2.0, "Flying": 2.0, "Bug": 2.0, "Fighting": 0.5, "Ground": 0.5, "Steel": 0.5},
    "Ghost":    {"Ghost": 2.0, "Psychic": 2.0, "Normal": 0.0, "Dark": 0.5},
    "Dragon":   {"Dragon": 2.0, "Steel": 0.5, "Fairy": 0.0},
    "Dark":     {"Ghost": 2.0, "Psychic": 2.0, "Fighting": 0.5, "Dark": 0.5, "Fairy": 0.5},
    "Steel":    {"Ice": 2.0, "Rock": 2.0, "Fairy": 2.0, "Steel": 0.5, "Fire": 0.5, "Water": 0.5, "Electric": 0.5},
    "Fairy":    {"Fighting": 2.0, "Dragon": 2.0, "Dark": 2.0, "Fire": 0.5, "Poison": 0.5, "Steel": 0.5},
    "Poison":   {"Grass": 2.0, "Fairy": 2.0, "Poison": 0.5, "Ground": 0.5, "Rock": 0.5, "Ghost": 0.5, "Steel": 0.0},
}


def single_type_effectiveness(
    attacking_type: str,
    defending_type: str,
    *,
    ruleset: "GenerationRuleset | None" = None,
) -> float:
    """Effectiveness of one attacking type against one defending type."""
    if ruleset is not None:
        override = ruleset.type_chart_overrides.get((attacking_type, defending_type))
        if override is not None:
            return override
    return TYPE_CHART.get(attacking_type, {}).get(defending_type, 1.0)


def type_effectiveness(
    attacking_type: str,
    defending_types: list[str] | tuple[str, ...],
    *,
    ruleset: "GenerationRuleset | None" = None,
) -> float:
    """Combined effectiveness of one attacking type against a (possibly
    dual-typed) defender. "Typeless" is a Track-C convention (see
    battler.py's effective_types()) for a Pokémon that lost every type it
    had -- it's simply skipped, contributing no multiplier either way."""
    mult = 1.0
    for dt in defending_types:
        if dt == "Typeless":
            continue
        mult *= single_type_effectiveness(attacking_type, dt, ruleset=ruleset)
    return mult

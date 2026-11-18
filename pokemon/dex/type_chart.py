"""The type-effectiveness chart. Ported verbatim from `TYPE_CHART`.

Ownership note: this is fundamental to damage calculation (Track C's
`pokemon.battle` territory) as much as it is to `/pokemon species`'s type
chart image (Track B's). Neither existed anywhere in the new codebase
before this pass, so it's placed here in `pokemon.dex` (static reference
data, no battle-hook involvement) as the one shared source -- if
`pokemon.battle`'s damage calc needs the same table, it should import
`type_effectiveness`/`TYPE_CHART` from here rather than duplicating it.
"""

from __future__ import annotations

TYPE_CHART: dict[str, dict[str, float]] = {
    "Normal": {"Rock": 0.5, "Steel": 0.5, "Ghost": 0.0},
    "Fire": {"Grass": 2.0, "Water": 0.5, "Fire": 0.5, "Ice": 2.0, "Steel": 2.0, "Bug": 2.0, "Rock": 0.5, "Dragon": 0.5},
    "Water": {"Fire": 2.0, "Grass": 0.5, "Water": 0.5, "Ground": 2.0, "Rock": 2.0, "Dragon": 0.5},
    "Grass": {"Water": 2.0, "Fire": 0.5, "Grass": 0.5, "Ground": 2.0, "Flying": 0.5, "Poison": 0.5, "Rock": 2.0,
              "Bug": 0.5, "Dragon": 0.5, "Steel": 0.5},
    "Electric": {"Water": 2.0, "Flying": 2.0, "Electric": 0.5, "Ground": 0.0, "Grass": 0.5, "Dragon": 0.5},
    "Ice": {"Grass": 2.0, "Ground": 2.0, "Flying": 2.0, "Dragon": 2.0, "Fire": 0.5, "Water": 0.5, "Ice": 0.5,
            "Steel": 0.5},
    "Fighting": {"Normal": 2.0, "Ice": 2.0, "Rock": 2.0, "Steel": 2.0, "Dark": 2.0, "Flying": 0.5, "Bug": 0.5,
                 "Psychic": 0.5, "Fairy": 0.5, "Ghost": 0.0},
    "Ground": {"Steel": 2.0, "Fire": 2.0, "Electric": 2.0, "Poison": 2.0, "Rock": 2.0, "Flying": 0.0, "Grass": 0.5},
    "Flying": {"Grass": 2.0, "Fighting": 2.0, "Bug": 2.0, "Electric": 0.5, "Rock": 0.5, "Steel": 0.5},
    "Psychic": {"Fighting": 2.0, "Poison": 2.0, "Psychic": 0.5, "Steel": 0.5, "Dark": 0.0},
    "Bug": {"Grass": 2.0, "Psychic": 2.0, "Dark": 2.0, "Fire": 0.5, "Fighting": 0.5, "Flying": 0.5, "Ghost": 0.5,
            "Steel": 0.5, "Fairy": 0.5},
    "Rock": {"Fire": 2.0, "Ice": 2.0, "Flying": 2.0, "Bug": 2.0, "Fighting": 0.5, "Ground": 0.5, "Steel": 0.5},
    "Ghost": {"Ghost": 2.0, "Psychic": 2.0, "Normal": 0.0, "Dark": 0.5},
    "Dragon": {"Dragon": 2.0, "Steel": 0.5, "Fairy": 0.0},
    "Dark": {"Ghost": 2.0, "Psychic": 2.0, "Fighting": 0.5, "Dark": 0.5, "Fairy": 0.5},
    "Steel": {"Ice": 2.0, "Rock": 2.0, "Fairy": 2.0, "Steel": 0.5, "Fire": 0.5, "Water": 0.5, "Electric": 0.5},
    "Fairy": {"Fighting": 2.0, "Dragon": 2.0, "Dark": 2.0, "Fire": 0.5, "Poison": 0.5, "Steel": 0.5},
    "Poison": {"Grass": 2.0, "Fairy": 2.0, "Poison": 0.5, "Ground": 0.5, "Rock": 0.5, "Ghost": 0.5, "Steel": 0.0},
}

TYPE_ORDER: list[str] = [
    "Normal", "Fire", "Water", "Electric", "Grass", "Ice", "Fighting", "Poison", "Ground",
    "Flying", "Psychic", "Bug", "Rock", "Ghost", "Dragon", "Dark", "Steel", "Fairy",
]

TYPE_COLOR: dict[str, tuple[int, int, int]] = {
    "Normal": (170, 170, 153), "Fire": (255, 68, 34), "Water": (51, 153, 255),
    "Electric": (255, 204, 51), "Grass": (119, 204, 85), "Ice": (102, 204, 255),
    "Fighting": (187, 85, 68), "Poison": (170, 85, 153), "Ground": (221, 187, 85),
    "Flying": (136, 153, 255), "Psychic": (255, 85, 153), "Bug": (170, 187, 34),
    "Rock": (187, 170, 102), "Ghost": (102, 102, 187), "Dragon": (119, 102, 238),
    "Dark": (119, 85, 68), "Steel": (170, 170, 187), "Fairy": (238, 153, 238),
}


def type_effectiveness(move_type: str, defender_types: list[str] | tuple[str, ...]) -> float:
    """Ported verbatim from `type_effectiveness`."""
    mult = 1.0
    for dt in defender_types:
        mult *= TYPE_CHART.get(move_type, {}).get(dt, 1.0)
    return mult

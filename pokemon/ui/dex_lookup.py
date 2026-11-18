"""Isolates the one place the rendering pipeline needs Track B's Pokedex
data (sprite URL fields per species/form, old code's `_POKEDEX_BY_ID`).
Kept as a single-function adapter so a wrong guess about Track B's actual
module path/shape only needs a one-line fix here rather than a hunt
through battle_render.py.

ASSUMES `pokemon.dex.pokedex.POKEDEX_BY_ID: dict[int, dict]` exists,
keyed by species_id, with the same sprite-URL fields old code's dict had
(`sprite`, `shiny`, `b`, `b_shiny`, and the `_f` female variants of each).
If Track B's real shape differs, this is the only function that needs to
change.
"""
from __future__ import annotations


def pokedex_entry(species_id: int) -> dict:
    try:
        from pokemon.dex.pokedex import POKEDEX_BY_ID
    except ImportError:
        return {}
    return POKEDEX_BY_ID.get(species_id, {})

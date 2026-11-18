"""Per-generation species TYPES (the companion of base_stats.py).

`data/pokedex.json` holds each species' CURRENT types, so a Gen 3-5 battle gave Clefable, Jigglypuff, Togetic, Marill, Ralts ... their Fairy typing before Fairy existed (22 species). `data/generation_types.json`
records, per generation, only the species whose types differ from today's, generated from Pokemon Showdown's own mod chain (tests/tools/make_generation_types.js): `{generation: {national_dex_number: [types]}}`.
Applied by `BattleEngine._apply_generation_base_stats`, next to the base stats.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "generation_types.json"


@lru_cache(maxsize=1)
def _table() -> dict[str, dict[str, list[str]]]:
    try:
        return json.loads(_DATA_FILE.read_text())
    except FileNotFoundError:
        return {}


def types_override(generation: int, species_id: int) -> list[str] | None:
    """The species' types in `generation`, or None if they match today's."""
    row = _table().get(str(generation), {}).get(str(species_id))
    return list(row) if row else None


def apply_to_battler(battler, generation: int, current_dex_types) -> bool:
    """Rewrites `battler.identity.types` for `generation`. Only touches a battler whose types are still its species' current ones (a Mega / regional form keeps its own)."""
    ov = types_override(generation, battler.identity.species_id)
    if ov is None or list(battler.identity.types) != list(current_dex_types):
        return False
    battler.identity.types = ov
    return True

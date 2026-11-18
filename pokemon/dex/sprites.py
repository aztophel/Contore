"""Sprite URLs from PokeAPI's public sprite repository.

The monolith's `_sprite_url` preferred per-species/per-form override URLs
(`sprite`/`shiny`/`b`/`b_shiny`/`_f` fields on a species or form dict) and
only fell back to formulaic `{id}.png` construction when those were
missing. Those override fields were never actually carried through
Showdown's data (see `pokedex_builder.py`'s module docstring on
`run_pokeapi_pass`'s dropped scope) -- they'd need a PokeAPI enrichment
pass this sandbox never had network access to run. So this is the
fallback path only, which is enough for every base-form species (the vast
majority); alternate forms with irregular sprite filenames (Unown letters,
Arceus plates, some regional/mega forms) will show a slightly wrong or
missing image until that data exists. Flagged, not hidden -- see
TRACK_B_HANDOFF_2.md.
"""

from __future__ import annotations

_SPRITE_BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"


def sprite_url(species_id: int, shiny: bool = False, back: bool = False) -> str:
    """Ported from the fallback branch of `_sprite_url`."""
    return f"{_SPRITE_BASE}/{'back/' if back else ''}{'shiny/' if shiny else ''}{species_id}.png"


def official_artwork_url(species_id: int, shiny: bool = False) -> str:
    """Ported from the fallback branch of `_official_sprite_url`."""
    url = f"{_SPRITE_BASE}/other/official-artwork/"
    if shiny:
        url += "shiny/"
    return f"{url}{species_id}.png"


def animated_sprite_url(species_id: int, shiny: bool = False) -> str:
    """Ported verbatim from `_get_animated_sprite_url` (Gen V Black/White
    animated sprites -- these only exist for Gen I-V species IDs)."""
    base = f"{_SPRITE_BASE}/versions/generation-v/black-white/animated"
    return f"{base}/{'shiny/' if shiny else ''}{species_id}.gif"

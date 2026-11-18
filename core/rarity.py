"""Shared rarity display scheme (color + emoji per rarity tier).

Used by the achievement system for achievement rarities, and by the
Pokémon side for species/encounter rarity (legendary/mythical display,
etc.) -- both features independently referenced the same two dicts in the
monolith, so this lives in `core/` rather than being owned by one track.
Pokémon cogs: import straight from here rather than redefining these.
"""
from __future__ import annotations

RARITY_COLORS: dict[str, int] = {
    "COMMON": 0x95A5A6,
    "UNCOMMON": 0x2ECC71,
    "RARE": 0x00CCFF,
    "EPIC": 0x9B59B6,
    "LEGENDARY": 0xF7B71E,
    "MYTHIC": 0xE74C3C,
    "MYTHICAL": 0xE74C3C,
}

RARITY_EMOJI: dict[str, str] = {
    "COMMON": "<:common:1513007219036131449>",
    "UNCOMMON": "<:uncommon:1513007224430268568>",
    "RARE": "<:rare:1513007320936878112>",
    "EPIC": "<:epic:1513007331170979840>",
    "LEGENDARY": "<:legendary:1513007344743878696>",
    "MYTHIC": "<:mythical:1513007354285785189>",
    "MYTHICAL": "<:mythical:1513007354285785189>",
}

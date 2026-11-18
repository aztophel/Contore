"""Discord asset constants the rendering pipeline needs: custom emoji IDs,
on-disk cache directories, and the PokeAPI/Showdown sprite source URLs.
Old code: contore.py:2133-2680 (emoji/cache constants), 3375-3432
(scene/platform tables), 2325-2340 (POKEBALL_EMOJI/TYPE_EMOJI).

NOTE: POKEBALL_EMOJI in particular is very likely needed by Track B too
(catching commands show which ball was used). This is a plain data
constant with no behavior attached, so if Track B's data/ ends up with its
own copy, deduplicating later is a one-line import change on whichever
side keeps the canonical copy -- not a design conflict.
"""
from __future__ import annotations
import re

import config
from pokemon.dex.items_repo import POKEBALL_EMOJI  # canonical copy; see this module's own docstring

OWNER_ID = config.OWNER_ID  # used to pick a special duel background

SHINY = "<:shiny:1519556990429565140>"
MEGA_BADGE = "<:mega:1519555367816659127>"  # old code used PartialEmoji(name="mega", id=...); the id is what matters for CDN fetch

STATUS_ABBR = {"burn": "BRN", "poison": "PSN", "bad_poison": "PSN", "paralysis": "PAR", "sleep": "SLP", "freeze": "FRZ"}
_STATUS_BALL_TINT = {
    "burn": (235, 120, 40), "poison": (150, 60, 180), "bad_poison": (120, 30, 155),
    "paralysis": (220, 195, 40), "sleep": (110, 110, 150), "freeze": (120, 195, 225),
}

_CUSTOM_EMOJI_RE = re.compile(r"^<a?:(\w{2,32}):(\d{15,21})>$")
_CDN_POW2_SIZES = (16, 32, 64, 128, 256, 512, 1024, 2048, 4096)

BG_BASE_DIR = "data/Contore/bgs"
PLATFORM_BASE_DIR = "data/Contore/platforms"
SPRITE_CACHE_DIR = "data/Contore/pokemon"
ICON_SPRITE_CACHE_DIR = "data/Contore/icons"
SUBSTITUTE_SPRITE_DIR = "data/Contore/substitute"
SUBSTITUTE_SPRITE_URL = "https://play.pokemonshowdown.com/sprites/gen5/substitute.png"

_BG_SLOT_AVAIL: dict[str, frozenset] = {
    "day": frozenset({"cobble", "deepwater", "forest", "grass", "rocks_brown", "rocks", "sand", "water", "wood"}),
    "afternoon": frozenset({"cobble", "deepwater", "forest", "grass", "rocks_brown", "rocks", "snow", "water"}),
    "night": frozenset({"cobble", "deepwater", "forest", "grass", "rocks_brown", "rocks", "snow", "water"}),
}
_PLATFORM_SLOT_AVAIL: dict[str, frozenset] = {
    "day": frozenset({"cobble", "deepwater", "forest", "grass", "ice", "rocks", "rocks_brown", "sand", "snow", "snowy_grass", "water", "wood"}),
    "afternoon": frozenset({"cobble", "deepwater", "forest", "grass", "ice", "rocks", "rocks_brown", "snow", "snowy_grass", "water"}),
    "night": frozenset({"cobble", "deepwater", "forest", "grass", "ice", "rocks", "rocks_brown", "snow", "snowy_grass", "water"}),
}
_SCENE_FALLBACK: dict[str, str] = {
    "sand": "rocks_brown", "wood": "forest", "ice": "snow", "snowy_grass": "snow",
    "rocks_brown": "rocks", "deepwater": "water",
}
_TYPE_TO_SCENE: dict[str, list[str]] = {
    "Fire": ["rocks", "rocks_brown", "sand"], "Water": ["water", "deepwater"], "Grass": ["forest", "grass", "wood"],
    "Electric": ["cobble", "grass"], "Ice": ["snow"], "Rock": ["rocks", "rocks_brown"],
    "Ground": ["rocks_brown", "sand"], "Ghost": ["deepwater", "forest"], "Dark": ["deepwater", "forest"],
    "Normal": ["grass", "wood"], "Flying": ["grass", "cobble"], "Fighting": ["cobble", "rocks"],
    "Poison": ["forest", "grass"], "Bug": ["forest", "grass"], "Psychic": ["forest", "cobble"],
    "Dragon": ["rocks", "water"], "Steel": ["cobble", "rocks"], "Fairy": ["grass", "forest"],
}
_WEATHER_TO_SCENES: dict[str, list[str]] = {
    "sun": ["sand", "rocks_brown"], "harsh_sun": ["sand", "rocks_brown"],
    "rain": ["water", "deepwater"], "heavy_rain": ["water", "deepwater"],
    "sand": ["rocks_brown", "sand"], "hail": ["snow"],
}
_TERRAIN_TO_SCENES: dict[str, list[str]] = {
    "grassy": ["grass", "forest"], "electric": ["cobble"],
    "misty": ["water", "deepwater"], "psychic": ["forest", "cobble"],
}

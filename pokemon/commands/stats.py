"""The standard Gen III+ stat formulas: base stat + IVs + EVs + level (+
nature for the five non-HP stats). Pure arithmetic, no battle-state
involvement -- as distinct from *effective* in-battle stats (stat stages,
ability/item modifiers, etc.), which is squarely the battle engine's
(`pokemon.battle.*`, Track C/D's) territory and stays there.

This lives in `pokemon/commands` because every non-battle display of a
Pokémon's condition (`/pokemon team view`, `/pokemon info`, a spawn/catch
embed's HP bar, ...) needs it and none of those touch the battle engine at
all. If `pokemon.battle.*` also needs the same base formula for
initializing a `FighterState`'s stats, it should import this rather than
re-deriving it -- there's no hook/ECS involvement here to justify a second
copy.
"""

from __future__ import annotations

import json

from pokemon.commands.repository import parse_ivs
from pokemon.dex.species import STAT_KEYS, Species

# canonical stat key -> (boosted-by, lowered-by), or None for a neutral nature.
NATURE_MODS: dict[str, tuple[str, str] | None] = {
    "Hardy": None, "Docile": None, "Serious": None, "Bashful": None, "Quirky": None,
    "Lonely": ("attack", "defense"), "Brave": ("attack", "speed"),
    "Adamant": ("attack", "special-attack"), "Naughty": ("attack", "special-defense"),
    "Bold": ("defense", "attack"), "Relaxed": ("defense", "speed"),
    "Impish": ("defense", "special-attack"), "Lax": ("defense", "special-defense"),
    "Timid": ("speed", "attack"), "Hasty": ("speed", "defense"),
    "Jolly": ("speed", "special-attack"), "Naive": ("speed", "special-defense"),
    "Modest": ("special-attack", "attack"), "Mild": ("special-attack", "defense"),
    "Quiet": ("special-attack", "speed"), "Rash": ("special-attack", "special-defense"),
    "Calm": ("special-defense", "attack"), "Gentle": ("special-defense", "defense"),
    "Sassy": ("special-defense", "speed"), "Careful": ("special-defense", "special-attack"),
}

STAT_LABEL: dict[str, str] = {
    "hp": "HP", "attack": "Attack", "defense": "Defense",
    "special-attack": "Sp. Atk", "special-defense": "Sp. Def", "speed": "Speed",
}


def calc_max_hp(base_hp: int, level: int, iv: int, ev: int = 0) -> int:
    """Ported verbatim from `calc_max_hp`."""
    return int((2 * base_hp + iv + ev // 4) * level / 100) + level + 10


def iv_avg_percent(ivs: dict) -> float:
    """A single "IV quality" percentage across all 6 stats (31 = 100%).
    Ported verbatim from `iv_avg_percent`."""
    if not ivs:
        return 0.0
    return sum(ivs.get(k, 0) for k in STAT_KEYS) / (31 * len(STAT_KEYS)) * 100


def calc_stat(base: int, level: int, iv: int, ev: int = 0) -> int:
    """Ported verbatim from `calc_stat` -- no nature applied; see
    `calc_stat_with_nature` for the display-ready version."""
    return int((2 * base + iv + ev // 4) * level / 100) + 5


def calc_stat_with_nature(base: int, level: int, iv: int, ev: int, stat: str, nature: str) -> int:
    """`calc_stat` plus the +10%/-10% nature multiplier, applied the same
    way the games do: after the base formula, truncated. `stat` must be
    one of `STAT_KEYS` other than `"hp"` (HP has no nature modifier)."""
    value = calc_stat(base, level, iv, ev)
    mods = NATURE_MODS.get(nature)
    if mods:
        boosted, lowered = mods
        if stat == boosted:
            value = int(value * 1.1)
        elif stat == lowered:
            value = int(value * 0.9)
    return value


def pokemon_max_hp(p: dict, species: Species) -> int:
    """Max HP for an owned-Pokémon row, IV/EV-parsed and Shedinja-cased.
    Ported verbatim from `_pokemon_max_hp` (the Shedinja special case: its
    max HP is always 1, regardless of stats -- a real Gen III+ rule, not a
    bug)."""
    if p["species_id"] == 292:  # Shedinja
        return 1
    evs = p.get("evs")
    evs = json.loads(evs) if isinstance(evs, str) else (evs or {})
    ivs = parse_ivs(p.get("ivs"))
    return calc_max_hp(species.base_stats.get("hp", 1), p["level"], ivs["hp"], evs.get("hp", 0))


def pokemon_stats(p: dict, species: Species) -> dict[str, int]:
    """All six computed stats (HP + the five nature-adjusted others) for
    an owned-Pokémon row -- the "info card" stat spread. Not a direct port
    (the monolith only ever computed HP standalone via `_pokemon_max_hp`
    and left the other five to `make_fighter_state`'s battle-only path);
    this is the equivalent for display contexts that have no live battle
    state (`/pokemon info`)."""
    evs = p.get("evs")
    evs = json.loads(evs) if isinstance(evs, str) else (evs or {})
    ivs = parse_ivs(p.get("ivs"))
    nature = p.get("nature", "Hardy")
    level = p["level"]
    out = {"hp": pokemon_max_hp(p, species)}
    for stat in STAT_KEYS:
        if stat == "hp":
            continue
        out[stat] = calc_stat_with_nature(
            species.base_stats.get(stat, 1), level, ivs[stat], evs.get(stat, 0), stat, nature)
    return out

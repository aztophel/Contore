"""Per-generation species base stats.

The Pokedex data (data/pokedex.json) holds each species' CURRENT base stats, so
a Gen 6 battle used Gen 7+ stats for every species that was later changed
(Arbok Atk 85 -> 95, Exeggutor Sp.Def 65 -> 75, ...); Gen 1 alone differs for
150 species. `data/generation_base_stats.json` records, per generation, only
the species whose stats differ from today's, generated from Pokemon Showdown
itself -- `Dex.mod('genN').species.get(name).baseStats` for every base species,
so Showdown's own mod inheritance chain resolves it, not a reimplementation of
it. `{generation: {national_dex_number: [hp, atk, def, spa, spd, spe]}}`.
Regenerate with /home/claude/work/gen_basestats.js-style script if Showdown's
data changes (see MIGRATION_STATUS.md).

Applied by `BattleEngine.__init__` (the one place every wild/PvP/boss builder
passes through and that already knows the ruleset), NOT in `ruleset.py`:
that dataclass is a frozen contract and gaining a field would be a contract
change; this module needs none.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_KEYS = ("hp", "attack", "defense", "special-attack", "special-defense", "speed")
_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "generation_base_stats.json"


@lru_cache(maxsize=1)
def _table() -> dict[str, dict[str, list[int]]]:
    try:
        return json.loads(_DATA_FILE.read_text())
    except FileNotFoundError:
        return {}


def base_stats_override(generation: int, species_id: int) -> dict[str, int] | None:
    """The species' base stats in `generation`, or None if they match today's."""
    row = _table().get(str(generation), {}).get(str(species_id))
    return dict(zip(_KEYS, row)) if row else None


def apply_to_battler(battler, generation: int, current_dex_stats: dict[str, int]) -> bool:
    """Rewrites `battler.identity.base_stats` for `generation`. Returns True if
    it changed anything.

    Only touches a battler whose stats are still its species' current ones:
    `Identity` has no form field (a form is expressed purely by different base
    stats), so a Mega/regional form must not be given its base species' old
    stats. If the HP stat changed, max HP is recomputed and the damage already
    taken is preserved."""
    ov = base_stats_override(generation, battler.identity.species_id)
    if ov is None or dict(battler.identity.base_stats) != dict(current_dex_stats):
        return False
    old_max = battler.vitals.max_hp
    battler.identity.base_stats = {**battler.identity.base_stats, **ov}
    if ov["hp"] != current_dex_stats.get("hp"):
        new_max = max(1, int(battler._raw_stat("hp")))
        if battler.vitals.hp > 0:
            battler.vitals.hp = max(1, new_max - (old_max - battler.vitals.hp))
        battler.vitals.max_hp = new_max
    return True

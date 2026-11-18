"""Soft dependency onto the battle engine's live-battle tracking.

Several Track B commands (`/pokemon team ...`, `/pokemon duel`, releasing
a Pokémon that's on an active team, ...) need to refuse to act while the
trainer is mid-battle -- the same "don't let the ground shift under a live
battle" rule the monolith enforced via a single `_ACTIVE_BATTLES` set
checked from a dozen call sites. That set is genuinely battle-engine state
(Track C/D's `pokemon.battle`/`pokemon.ui`), so this module only exposes a
soft-dependency accessor -- mirroring `pokemon.commands._db`'s pattern for
`core.db` -- rather than owning the state itself. Until that module
exists, every check here simply passes (nobody's ever "in battle" yet),
which is the correct degraded behavior for testing Track B in isolation.
"""

from __future__ import annotations


def is_in_battle(user_id: int) -> bool:
    try:
        from pokemon.ui.battle_view import ACTIVE_BATTLES  # type: ignore[import-not-found]
    except ImportError:
        return False
    return user_id in ACTIVE_BATTLES

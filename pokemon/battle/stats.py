"""Stat-stage change resolution. Owned by Track C.

The other half of "stat calculation" -- battler.py's stat_async() answers
"what is this Battler's current Sp.Atk (etc.) right now", including the
burn/Guts interaction (see that method's docstring); this module answers
"apply a +/-N stage change to this Battler, respecting Mist/Hyper Cutter/
Contrary/Simple, and produce the log message", which is the other thing
the old monolith's scattered `clamp_stage()` call sites all had to get
right by hand at each of the ~40 places a stat change could originate
from (moves, abilities, held items, entry hazards).

Old code: the many `<state>.clamp_stage(key, delta, name, from_foe=...)`
call sites throughout apply_move, plus the separate ad-hoc
`field.mist[side] > 0` guard duplicated at every one of them
(contore.py:17428-17430, 19230-19232, and others) instead of being part of
clamp_stage itself.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from .side_conditions import Mist

if TYPE_CHECKING:
    from .battler import Battler
    from .context import MoveContext

# PokeAPI's stat-change naming (what MoveSpec.stat_changes and PokeAPI's
# move.stat_changes both use) -> Battler.STAGE_KEYS' short internal form.
POKEAPI_STAT_TO_KEY: dict[str, str] = {
    "attack": "atk", "defense": "def", "special-attack": "spa",
    "special-defense": "spd", "speed": "spe",
    "accuracy": "acc", "evasion": "eva",
    # Already-short forms pass through unchanged, so callers that already
    # have an internal key (abilities, held items, hazards) can use this
    # same dict without a separate branch.
    "atk": "atk", "def": "def", "spa": "spa", "spd": "spd", "spe": "spe",
    "acc": "acc", "eva": "eva",
}


async def apply_stat_change(
    ctx: "MoveContext", recipient: "Battler", stat: str, delta: int,
    *, silent_subject: bool = False,
) -> str:
    """Resolve one stat-stage change and log it. This is the single path
    every stat-changing effect should go through -- moves (via
    BasicMove._apply_stat_changes or a dedicated Move.on_hit), abilities
    (Intimidate, Moxie, Anger Point, Download's non-existent-but-if-added
    case, ...), held items (the pinch-stat berries, see
    held_items/end_of_turn.py), and entry hazards (Sticky Web) all call
    this instead of touching `recipient.stages` or even
    `recipient.clamp_stage()` directly, so Mist only needs implementing
    once.

    `delta < 0` is treated as foe-caused (and therefore checked against
    Mist and the single-stat immunities Hyper Cutter/Big Pecks/Keen Eye
    already wire into clamp_stage) whenever `recipient` isn't the one who
    used the move -- a Pokemon's own self-inflicted drop (Superpower,
    Overheat, Draco Meteor, Close Combat, ...) is never blocked by its own
    Hyper Cutter/Mist, exactly like the old code's callers individually
    passed `from_foe=True` only for the foe-facing case.
    """
    key = POKEAPI_STAT_TO_KEY.get(stat, stat)
    user = getattr(ctx, "user", None)
    from_foe = delta < 0 and user is not None and recipient is not user

    if from_foe:
        side = ctx.target_side(recipient)
        if side.has(Mist):
            msg = f"**{recipient.name}** is protected by Mist!"
            ctx.log(msg)
            return msg

    msg = await recipient.clamp_stage(ctx, key, delta, from_foe=from_foe, silent_subject=silent_subject)
    if msg:
        ctx.log(msg)
    return msg

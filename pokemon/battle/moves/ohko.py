"""Fissure, Guillotine, Horn Drill, Sheer Cold -- MIGRATION_STATUS.md gap
#3. `damage.py`'s `check_accuracy` already implements the real OHKO
accuracy formula (`spec.is_ohko` -- level-difference based, and an
outright miss against a higher-level target) since that's shared,
generic-pipeline logic; what was missing is the on-hit effect once an
OHKO move actually connects, since dealing `target.vitals.max_hp` damage
doesn't fit `BasicMove`'s base_power-driven formula at all. Follows
`moves/fixed_damage.py`'s established pattern (shared `apply_fixed_damage`/
`_check_type_immunity`) since this is the same "skip the normal damage
formula entirely" family, just with the amount computed from the target
instead of a flat number or the user's level/HP.
"""
from __future__ import annotations

from ..move import Move
from ..context import MoveContext
from ..registry import register_move
from .fixed_damage import _check_type_immunity, apply_fixed_damage


def _ohko_move(slug: str):
    @register_move(slug)
    class _OHKOMove(Move):
        async def on_hit(self, ctx: MoveContext, target) -> None:
            if await _check_type_immunity(ctx, self, target):
                return
            await apply_fixed_damage(ctx, target, target.vitals.max_hp)
            ctx.log("It's a one-hit KO!")
    _OHKOMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _OHKOMove


FissureMove = _ohko_move("fissure")
GuillotineMove = _ohko_move("guillotine")
HornDrillMove = _ohko_move("horn-drill")
# Sheer Cold's Gen 7+ "fails against an Ice-type target" clause is
# deliberately NOT applied here -- this bot's ruleset baseline is Gen 6
# (see pokemon/generations/ruleset.py), where that exception didn't exist
# yet. Revisit if/when a Gen 7+ ruleset is actually selectable and this
# move needs to branch on it.
SheerColdMove = _ohko_move("sheer-cold")

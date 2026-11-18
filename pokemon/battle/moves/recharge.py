"""Hyper Beam and its recharge-family siblings (Giga Impact, Rock
Wrecker, Roar of Time) -- MIGRATION_STATUS.md gap #7. Damage already
works correctly via the generic `BasicMove` pipeline (same base_power/
accuracy data these would get anyway); the only missing piece was
attaching `MustRecharge` to the user afterward, which is all this file
adds.

Attached in `on_hit` (fires per target actually hit), which is exactly
right: in Showdown's `moveHit` (sim/battle-actions.ts), a move's `self`
effects -- including Hyper Beam's `mustrecharge` -- are applied at step 4
via `selfDrops`, which skips any target that was missed (`target ===
false`). So a Hyper Beam that misses does NOT force a recharge turn. An
earlier version of this docstring claimed the opposite and called this a
"simplification"; that was wrong -- checked against the source and
corrected. (Gen 1 differs -- there a KO or a Substitute also skips the
recharge -- but this bot's baseline is Gen 6.)
"""
from __future__ import annotations

from ..move import BasicMove
from ..context import MoveContext
from ..registry import register_move
from ..volatile import MustRecharge


def _recharge_move(slug: str):
    @register_move(slug)
    class _RechargeMove(BasicMove):
        async def on_hit(self, ctx: MoveContext, target) -> None:
            await super().on_hit(ctx, target)
            if ctx.user.identity.ability_slug != "truant":  # Truant already forces a rest turn every other turn
                ctx.user.add(MustRecharge(), ctx.events)
    _RechargeMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _RechargeMove


HyperBeamMove = _recharge_move("hyper-beam")
GigaImpactMove = _recharge_move("giga-impact")
RockWreckerMove = _recharge_move("rock-wrecker")
RoarOfTimeMove = _recharge_move("roar-of-time")

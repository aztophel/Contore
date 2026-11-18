"""Minimize -- ported from Pokemon Showdown (data/moves.ts + the gen mods).

The reactive half (double damage / always-hit against a minimized target)
is the `Minimize` volatile (volatile.py). This move applies the boost and
attaches it. Showdown applies the evasion boost every use but the volatile
only once (`onRestart: () => null`), so re-using Minimize keeps stacking
evasion up to +6 without failing."""
from __future__ import annotations

from ..move import BasicMove
from ..context import MoveContext
from ..registry import register_move
from ..stats import apply_stat_change
from ..volatile import Minimize


@register_move("minimize")
class MinimizeMove(BasicMove):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        if ctx.ruleset.number >= 5:
            await super().on_hit(ctx, target)  # spec's data-driven +2 evasion
        else:
            # data/mods/gen4: Minimize is +1 evasion through Gen 4.
            await apply_stat_change(ctx, target, "evasion", 1)
        if not target.has(Minimize):
            target.add(Minimize(), ctx.events)

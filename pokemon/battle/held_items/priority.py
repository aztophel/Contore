"""Pre-move fractional-priority items -- MIGRATION_STATUS.md gap #5
("Quick Claw / Custap Berry ... engine.py's `_order_actions` doesn't have
a hook point for this at all yet"). Ported directly from Pokemon
Showdown's actual `onFractionalPriority` callbacks (`data/items.ts`) --
see `engine.py`'s `_order_actions` for the matching engine-side piece
(a genuinely new, separate sort-key component, since this needed a real
hook point Showdown's own architecture already has and this engine
didn't).

An earlier version of this file gave both items a flat +1 whole priority
BRACKET, on the reasoning that the monolith's Custap Berry
(`effective_priority`) did exactly that. That reasoning was wrong: the
brief for this pass is fidelity to the real games (via Showdown, which
implements them correctly), not to the monolith's own approximations --
"nothing should change implementation-wise" was about preserving what the
OOP/ECS move-and-ability rewrite covers (the actual per-move/per-ability
logic), not about deliberately re-encoding a simplification the monolith
happened to have for a mechanic that was reasonably faithful to get right
here. Fixed to match Showdown exactly: both return a flat +0.1 to a
SEPARATE fractional-priority value that can only break a speed tie within
the user's own priority bracket, never push it into a higher one -- a
slow Quick Claw/Custap user still can't out-prioritize a genuine priority
move from anyone else. Both also only apply at priority <= 0 in the first
place (Showdown's own `if (priority <= 0 ...)` guard) -- there's no
"moving first among your peers" benefit to a move that's already
priority-boosted.
"""
from __future__ import annotations
import random

from ..items import HeldItem, register_item


@register_item("Custap Berry")
class CustapBerry(HeldItem):
    async def on_fractional_priority(self, ctx, owner, bonus: float, priority: int) -> float | None:
        if owner is not ctx.user or owner.fainted or priority > 0:
            return None
        from .end_of_turn import pinch_limit
        if owner.vitals.hp > pinch_limit(ctx, owner):
            return None
        from .end_of_turn import _consume
        _consume(owner, ctx)
        ctx.log(f"**{owner.name}** used its Custap Berry to move first!")
        return 0.1


@register_item("Quick Claw")
class QuickClaw(HeldItem):
    async def on_fractional_priority(self, ctx, owner, bonus: float, priority: int) -> float | None:
        if owner is not ctx.user or owner.fainted or priority > 0:
            return None
        if random.random() >= 0.2:
            return None
        ctx.log(f"**{owner.name}**'s Quick Claw activated, letting it move first!")
        return 0.1



class _MovesLast(HeldItem):
    """Showdown `onFractionalPriority: -0.1` (Lagging Tail, Full Incense): the holder moves LAST within its priority bracket, whatever its Speed. (No `priority <= 0` guard, unlike Quick Claw / Custap.)"""
    async def on_fractional_priority(self, ctx, owner, bonus: float, priority: int) -> float | None:
        if owner is not ctx.user or owner.fainted:
            return None
        return -0.1


@register_item("Lagging Tail")
class LaggingTail(_MovesLast):
    pass


@register_item("Full Incense")
class FullIncense(_MovesLast):
    pass

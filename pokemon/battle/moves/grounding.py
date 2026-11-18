"""Smack Down -- ported from Pokemon Showdown's `smackdown` move + condition.

The grounding itself is the `SmackedDown` volatile (volatile.py), consulted
by `Battler.is_grounded`; see damage.py for why that's what makes Ground
moves hit a Flying type / Levitate user afterwards."""
from __future__ import annotations

from ..move import BasicMove
from ..context import MoveContext
from ..registry import register_move
from ..battler import _Ingrain
from ..volatile import Substitute, SmackedDown, Charging


def _smack_down_applies(target, ctx: MoveContext) -> bool:
    """Showdown's condition `onStart`: it only sticks on a Pokemon that is
    airborne by type or Levitate, and NOT one that Iron Ball / Ingrain /
    Gravity already keep down. (The Fly/Bounce knock-down, which always
    counts, is handled by the caller.)"""
    airborne = "Flying" in target.effective_types() or target.effective_ability() == "levitate"
    forced_down = (target.effective_item() == "Iron Ball" or ctx.field.gravity
                   or target.get(_Ingrain) is not None)
    return airborne and not forced_down


@register_move("smack-down")
class SmackDownMove(BasicMove):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        had_sub = target.has(Substitute)  # a hit a Substitute absorbs applies no volatile
        await super().on_hit(ctx, target)
        if ctx.result.failed or target.fainted or had_sub or target.has(SmackedDown):
            return
        # Knocking a Fly/Bounce user out of the sky: cancels the semi-invulnerable
        # charge (the engine forces the move to continue only while `Charging`
        # exists, so removing it IS cancelling the move) and always counts.
        charging = target.get(Charging)
        knocked_down = charging is not None and charging.move_slug in ("fly", "bounce")
        if knocked_down:
            target.remove(Charging, ctx.events)
        if not (knocked_down or _smack_down_applies(target, ctx)):
            return
        target.add(SmackedDown(), ctx.events)
        ctx.log(f"**{target.name}** fell straight down!")

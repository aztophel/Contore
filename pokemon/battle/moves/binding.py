"""Yawn and the binding-move family -- MIGRATION_STATUS.md gap #7. Same
split as move_restriction.py/volatile_status.py: the effect/duration
logic lives in each Volatile subclass (volatile.py); this file attaches
them.
"""
from __future__ import annotations
import random

from ..move import Move, BasicMove
from ..context import MoveContext
from ..registry import register_move
from ..volatile import Binding, Yawn


@register_move("yawn")
class YawnMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        # Showdown refuses the Yawn volatile UP FRONT when the target can't fall asleep (Insomnia, Vital Spirit, Sweet Veil, Leaf Guard in sun...),
        # rather than letting it tick down and fail the sleep later.
        from ..abilities._util import ally_blocks
        if await ally_blocks(ctx, target, "volatile:yawn"):                    # Sweet Veil
            return
        if target.has(Yawn) or target.vitals.status is not None or await target.consult_first("on_status_immunity", ctx, "sleep"):
            ctx.log("But it failed!")
            return
        rules = ctx.field.terrain_rules
        blocked = rules.blocks_volatile(target, "yawn", ctx.field, ctx.ruleset.number) if rules else None
        if blocked:                                   # Electric Terrain's onTryAddVolatile
            ctx.log(blocked)
            return
        target.add(Yawn(), ctx.events)
        ctx.log(f"**{target.name}** grew drowsy!")


def _binding_move(slug: str):
    @register_move(slug)
    class _BindingMove(BasicMove):
        """A DAMAGING move that also traps (Showdown `volatileStatus: 'partiallytrapped'`). This used to be a plain `Move` that trapped and dealt no damage at all."""
        async def on_hit(self, ctx: MoveContext, target) -> None:
            await super().on_hit(ctx, target)                                    # the damage
            if getattr(ctx, "hits_on_target", 1) <= 0 or target.fainted or ctx.hit_blocked:
                return                                                           # absorbed by a Substitute / the target fainted
            if target.has(Binding):
                return
            band = ctx.user.effective_item() == "Binding Band"
            divisor = (6 if band else 8) if ctx.ruleset.number >= 6 else (8 if band else 16)     # Showdown: 1/8 (Binding Band 1/6) from Gen 6; 1/16 (Band 1/8) before
            # Showdown `durationCallback`: Gen 5+ `random(5, 7)` = 5-6 turns (Grip Claw 8); Gens 3-4 `random(3, 7)` = 3-6 turns (Grip Claw 6) -- it was 5-7 everywhere
            grip = ctx.user.effective_item() == "Grip Claw"
            turns = (8 if grip else random.randint(5, 6)) if ctx.ruleset.number >= 5 else (6 if grip else random.randint(3, 6))
            turns -= 1                    # a Showdown effect of `duration: D` chips D-1 times: the last tick only removes it
            target.add(Binding(
                source_id=ctx.user.id, bound_divisor=divisor, turns_remaining=turns,
            ), ctx.events)
            ctx.log(f"**{target.name}** was trapped by **{ctx.move.spec.display_name}**!")
    _BindingMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _BindingMove


WrapMove = _binding_move("wrap")
BindMove = _binding_move("bind")
FireSpinMove = _binding_move("fire-spin")
ClampMove = _binding_move("clamp")
WhirlpoolMove = _binding_move("whirlpool")
SandTombMove = _binding_move("sand-tomb")
MagmaStormMove = _binding_move("magma-storm")
InfestationMove = _binding_move("infestation")
SnapTrapMove = _binding_move("snap-trap")
ThunderCageMove = _binding_move("thunder-cage")

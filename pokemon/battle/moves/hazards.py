"""Entry hazards that stack in layers (unlike Stealth Rock, which is
single-layer and lives in examples.py as the original demonstration of
this Field/Side-as-Entity pattern). Old code: contore.py's per-move
branches around the `_apply_entry_hazards` call site (the hazards
themselves resolve when a Pokemon SWITCHES IN, which is
side_conditions.py's job -- this file only covers the move that PLACES
them)."""
from __future__ import annotations

from ..move import Move
from ..context import MoveContext
from ..registry import register_move
from ..side_conditions import Spikes, ToxicSpikes, StickyWeb


@register_move("spikes")
class SpikesMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        side = ctx.target_side(target)
        existing = side.get(Spikes)
        if existing is not None:
            if existing.layers >= 3:
                ctx.log("But it failed!")
                return
            existing.layers += 1
        else:
            side.add(Spikes(layers=1), ctx.events)
        ctx.log("Spikes were scattered on the ground around the opposing team!")


@register_move("toxic-spikes")
class ToxicSpikesMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        side = ctx.target_side(target)
        existing = side.get(ToxicSpikes)
        if existing is not None:
            if existing.layers >= 2:
                ctx.log("But it failed!")
                return
            existing.layers += 1
        else:
            side.add(ToxicSpikes(layers=1), ctx.events)
        ctx.log("Poison spikes were scattered on the ground around the opposing team!")


@register_move("sticky-web")
class StickyWebMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        side = ctx.target_side(target)
        if side.has(StickyWeb):
            ctx.log("But it failed!")
            return
        side.add(StickyWeb(), ctx.events)
        ctx.log("A sticky web has been laid out beneath the opposing team's feet!")

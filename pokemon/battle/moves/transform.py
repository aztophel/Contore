"""Transform -- the move half of `volatile.transform_into` (Imposter is the ability half)."""
from __future__ import annotations

from ..move import Move
from ..context import MoveContext
from ..registry import register_move
from ..volatile import transform_into


@register_move("transform")
class TransformMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        if not await transform_into(ctx, ctx.user, target):
            ctx.result.failed = True
            ctx.log("But it failed!")

"""Attract, Nightmare, Perish Song -- MIGRATION_STATUS.md gap #7. Same
split as move_restriction.py: the restriction/effect logic lives in each
Volatile subclass (volatile.py); this file is just each move's `on_hit`
(failure conditions, attaching the volatile).
"""
from __future__ import annotations

from ..move import Move
from ..context import MoveContext
from ..registry import register_move
from ..volatile import Attract, Nightmare, PerishSong


@register_move("attract")
class AttractMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..abilities._util import ally_blocks
        if await ally_blocks(ctx, target, "volatile:attract"):
            return
        user_gender = ctx.user.identity.gender
        target_gender = target.identity.gender
        opposite = {"male": "female", "female": "male"}.get(target_gender)
        if target.has(Attract) or opposite is None or user_gender != opposite:
            ctx.log("But it failed!")
            return
        target.add(Attract(source_id=ctx.user.id), ctx.events)
        ctx.log(f"**{target.name}** fell in love!")
        await target.consult("on_attract", ctx, ctx.user)                 # Destiny Knot


@register_move("nightmare")
class NightmareMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        if target.vitals.status != "sleep" or target.has(Nightmare):
            ctx.log("But it failed!")
            return
        target.add(Nightmare(), ctx.events)
        ctx.log(f"**{target.name}** began having a nightmare!")


@register_move("perish-song")
class PerishSongMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        if target.has(PerishSong):
            return
        target.add(PerishSong(), ctx.events)
        ctx.log(f"**{target.name}**'s perish count is at 3!")

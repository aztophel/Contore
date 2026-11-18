"""Bide -- ported from Showdown's `bide` move/condition (data/moves.ts,
onLockMove/onBeforeMove/onHit). Same two-turn-family shape as
moves/charging.py's Fly/Dig/etc. (check-for-and-remove-my-own-volatile in
`on_try_use`), but the release deals accumulated damage to a dynamically
determined target (`Bide.last_damage_source_id`) rather than attacking
whatever `get_targets` would normally resolve -- this move's own
`target: "user"` data is a PokeAPI/PokeAPI-cache artifact describing
where the CHARGE half points, not the release, so `on_hit`'s `target`
argument is deliberately ignored on the release turn in favor of looking
the real target up directly.
"""
from __future__ import annotations

from ..move import Move
from ..context import MoveContext
from ..registry import register_move
from ..volatile import Bide


@register_move("bide")
class BideMove(Move):
    async def on_try_use(self, ctx: MoveContext) -> str | None:
        user = ctx.user
        existing = user.get(Bide)
        if existing is None:
            user.add(Bide(), ctx.events)
            return f"**{user.name}** began storing energy!"
        existing.turns_remaining -= 1
        if existing.turns_remaining > 0:
            return f"**{user.name}** is storing energy!"
        return None  # release turn

    async def on_hit(self, ctx: MoveContext, target) -> None:
        user = ctx.user
        bide = user.get(Bide)
        total = bide.total_damage if bide else 0
        source = None
        if bide is not None and bide.last_damage_source_id is not None:
            source = next((b for b in ctx.field.all_battlers() if b.id == bide.last_damage_source_id), None)
        if bide is not None:
            user.remove(Bide, ctx.events)

        if total <= 0 or source is None or source.fainted:
            ctx.log("But it failed!")
            return

        ctx.log(f"**{user.name}** unleashed its fury!")
        dealt = min(source.vitals.hp, total * 2)
        source.vitals.hp = max(0, source.vitals.hp - dealt)
        ctx.log(f"**{source.name}** took **{dealt:,}** damage!")
        if source.fainted:
            ctx.log(f"**{source.name}** fainted!")

"""Self-targeted moves that trade HP for a stat boost. Old code: apply_move's
Belly Drum branch.
"""
from __future__ import annotations

from ..move import Move
from ..context import MoveContext
from ..registry import register_move


@register_move("belly-drum")
class BellyDrumMove(Move):
    """Fails outright (no damage, no boost) if the user is already at
    max Attack, at or below half HP, or has 1 max HP (the "Shedinja
    clause" -- Pokemon Showdown's own name for it, since a 1-HP Pokemon
    can never pay a 50% HP cost and survive). Otherwise costs exactly
    50% max HP and sets Attack to +6 outright, not just +2 -- Pokemon
    Showdown's own data still says `boost({atk: 12})`, which merely
    demonstrates the boost call itself clips to +6; asking for +12
    unconditionally is simpler to read as "max it out" than reproducing
    a clip that would happen anyway once it reaches Battler.clamp_stage.
    Bypasses Magic Guard entirely, matching this codebase's existing
    Substitute (moves/examples.py) -- a self-inflicted "cost" to use a
    move, not indirect damage from an outside source."""
    async def on_try_use(self, ctx: MoveContext) -> str | None:
        user = ctx.user
        if user.vitals.max_hp == 1 or user.vitals.hp <= user.vitals.max_hp // 2:
            return "But it failed!"
        if user.stages.get("atk", 0) >= 6:
            return "But it failed!"
        return None

    async def on_hit(self, ctx: MoveContext, target) -> None:
        user = target  # spec.target == MoveTarget.USER, so target IS the user
        user.vitals.hp -= user.vitals.max_hp // 2
        from ..stats import apply_stat_change
        await apply_stat_change(ctx, user, "attack", 12)

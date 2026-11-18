"""Curse -- ported from Showdown's `onModifyMove`/`onHit` for this move
(data/moves.ts): a genuinely different move depending on whether the
USER is Ghost-type, not just a flavor difference. The non-Ghost half
(self stat changes) needed hand-written stat-change calls rather than the
generic `stat_changes` data path (`BasicMove._apply_stat_changes`), since
PokeAPI's own data for this move -- and therefore this bot's
`moves_data.json` -- has `stat_changes: []`; PokeAPI doesn't encode a
move's type-conditional branch, only the (Ghost-type) common case, which
here is exactly the branch with NO stat_changes data. The residual-damage
half is `volatile.Curse`, see its own docstring.
"""
from __future__ import annotations

from ..move import Move
from ..context import MoveContext
from ..registry import register_move
from ..stats import apply_stat_change
from ..volatile import Curse


@register_move("curse")
class CurseMove(Move):
    async def get_targets(self, ctx: MoveContext) -> list:
        if "ghost" in (t.lower() for t in ctx.user.effective_types()):
            from ..targeting import resolve_targets
            from ..move import MoveTarget
            return resolve_targets(MoveTarget.SELECTED_POKEMON, ctx)
        return [ctx.user]

    async def on_hit(self, ctx: MoveContext, target) -> None:
        if "ghost" not in (t.lower() for t in ctx.user.effective_types()):
            # Non-Ghost: self stat changes only, target IS the user (see
            # get_targets above) -- Showdown's own `boosts: {spe: -1, atk:
            # 1, def: 1}` for this branch.
            await apply_stat_change(ctx, target, "speed", -1)
            await apply_stat_change(ctx, target, "attack", 1)
            await apply_stat_change(ctx, target, "defense", 1)
            return

        if target.has(Curse):
            ctx.log("But it failed!")
            return
        cost = max(1, ctx.user.vitals.max_hp // 2)
        ctx.user.vitals.hp = max(0, ctx.user.vitals.hp - cost)
        ctx.log(f"**{ctx.user.name}** cut its own HP to curse **{target.name}**!")
        if ctx.user.fainted:
            return
        target.add(Curse(), ctx.events)

"""Stockpile / Spit Up / Swallow -- ported from Pokemon Showdown (data/moves.ts).

State lives in the `Stockpile` volatile (volatile.py). Spit Up and Swallow
both consume it via `release_stockpile`, which hands back only the Def/SpD
stages the layers really gave."""
from __future__ import annotations

from ..move import BasicMove, Move
from ..context import MoveContext
from ..registry import register_move
from ..volatile import Stockpile, release_stockpile


@register_move("stockpile")
class StockpileMove(Move):
    async def on_try_use(self, ctx: MoveContext) -> str | None:
        sp = ctx.user.get(Stockpile)
        if sp is not None and sp.layers >= 3:
            return "But it failed!"
        return None

    async def on_hit(self, ctx: MoveContext, target) -> None:
        sp = target.get(Stockpile)
        if sp is None:
            sp = Stockpile()
            target.add(sp, ctx.events)
        await sp.add_layer(ctx, target)
        ctx.log(f"**{target.name}** stockpiled **{sp.layers}**!")


@register_move("spit-up")
class SpitUpMove(BasicMove):
    """Power = 100 x layers; fails with none. The stockpile is spent after
    the move whether or not it hit (Showdown `onAfterMove`)."""

    async def on_try_use(self, ctx: MoveContext) -> str | None:
        return None if ctx.user.has(Stockpile) else "But it failed!"

    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        sp = ctx.user.get(Stockpile)
        return sp.layers * 100 if sp is not None else None

    async def on_after_move(self, ctx: MoveContext, connected: bool) -> None:
        await release_stockpile(ctx, ctx.user)


@register_move("swallow")
class SwallowMove(Move):
    """Heals 25% / 50% / 100% of max HP for 1 / 2 / 3 layers and spends the
    stockpile. At full HP it reports so but STILL consumes the stockpile and
    doesn't count as failed (Showdown returns NOT_FAIL)."""
    _FRACTION = (0.25, 0.5, 1.0)

    async def on_try_use(self, ctx: MoveContext) -> str | None:
        return None if ctx.user.has(Stockpile) else "But it failed!"

    async def on_hit(self, ctx: MoveContext, target) -> None:
        sp = target.get(Stockpile)
        layers = sp.layers if sp is not None else 1
        amount = int(target.vitals.max_hp * self._FRACTION[layers - 1])
        healed = target.heal(amount) if target.vitals.hp < target.vitals.max_hp else 0
        if healed:
            ctx.log(f"**{target.name}** regained health! (+**{healed:,}** HP)")
        else:
            ctx.log(f"**{target.name}**'s HP is full!")
        await release_stockpile(ctx, target)

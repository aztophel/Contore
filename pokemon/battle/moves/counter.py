"""Counter/Mirror Coat/Metal Burst -- all redirect to whoever last hit the
user this turn and reflect a multiple of that damage, rather than hitting
a normally-resolved target (Pokemon Showdown: `target: "scripted"`).
Reuses the last_damage_taken/last_damage_category/last_damage_from/
last_damage_turn tracking damage.py already sets after every hit (see
damage.py's _resolve_one_hit) -- built for exactly this family.

Old code: apply_move's Counter/Mirror Coat/Metal Burst branches.
"""
from __future__ import annotations

from ..move import Move, MoveCategory
from ..context import MoveContext
from ..registry import register_move


def _reflect_damage_move(slug: str, category: MoveCategory, multiplier: float):
    @register_move(slug)
    class _ReflectDamageMove(Move):
        async def get_targets(self, ctx: MoveContext) -> list:
            source = getattr(ctx.user, "last_damage_from", None)
            return [source] if source is not None and not source.fainted else []

        async def on_try_use(self, ctx: MoveContext) -> str | None:
            user = ctx.user
            if (getattr(user, "last_damage_turn", None) != ctx.engine.turn_number
                    or getattr(user, "last_damage_category", None) is not category
                    or getattr(user, "last_damage_from", None) is None):
                return "But it failed!"
            return None

        async def on_hit(self, ctx: MoveContext, target) -> None:
            from ..types import type_effectiveness
            if type_effectiveness(self.spec.type_name, target.effective_types(), ruleset=ctx.ruleset) == 0.0:
                ctx.log(f"It doesn't affect **{target.name}**!")
                return
            amount = max(1, int(ctx.user.last_damage_taken * multiplier))
            from .fixed_damage import apply_fixed_damage
            await apply_fixed_damage(ctx, target, amount)
    _ReflectDamageMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _ReflectDamageMove


CounterMove = _reflect_damage_move("counter", MoveCategory.PHYSICAL, 2.0)
MirrorCoatMove = _reflect_damage_move("mirror-coat", MoveCategory.SPECIAL, 2.0)


@register_move("metal-burst")
class MetalBurstMove(Move):
    """Same shape as Counter/Mirror Coat but 1.5x and category-agnostic --
    written separately rather than folded into the factory above since
    its condition doesn't take a `category` parameter at all."""
    async def get_targets(self, ctx: MoveContext) -> list:
        source = getattr(ctx.user, "last_damage_from", None)
        return [source] if source is not None and not source.fainted else []

    async def on_try_use(self, ctx: MoveContext) -> str | None:
        user = ctx.user
        if (getattr(user, "last_damage_turn", None) != ctx.engine.turn_number
                or getattr(user, "last_damage_from", None) is None):
            return "But it failed!"
        return None

    async def on_hit(self, ctx: MoveContext, target) -> None:
        amount = max(1, int(ctx.user.last_damage_taken * 1.5))
        from .fixed_damage import apply_fixed_damage
        await apply_fixed_damage(ctx, target, amount)

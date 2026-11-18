"""Abilities that act when their holder LEAVES the field (Regenerator, Natural Cure) or when it USES a move (Protean,
Libero) -- ported from Pokemon Showdown (data/abilities.ts) and, for the first two, from the monolith's `_apply_switch`.

Regenerator and Natural Cure were only mentioned in a docstring in the merged code: nothing implemented them and nothing
dispatched `on_switch_out`, so both silently did nothing (the monolith healed/cured inline when switching). The engine now
dispatches `on_switch_out` (`BattleEngine._do_switch`) and the new `on_prepare_hit` (Showdown's PrepareHit).
"""
from __future__ import annotations

from ..registry import register_ability
from ..ability import Ability


@register_ability("regenerator")
class Regenerator(Ability):
    """Heals 1/3 of max HP on the way out (Showdown `onSwitchOut`)."""
    async def on_switch_out(self, ctx, owner) -> None:
        v = owner.vitals
        v.hp = min(v.max_hp, v.hp + v.max_hp // 3)


@register_ability("natural-cure")
class NaturalCure(Ability):
    """Cures the holder's status on the way out (Gen 5+: always; the Gen 3-4 30% chance is not modelled)."""
    async def on_switch_out(self, ctx, owner) -> None:
        v = owner.vitals
        if v.status:
            v.status, v.status_turns, v.toxic_counter = None, 0, 0
            ctx.engine.log.append(f"**{owner.name}**'s status was cured!")


def _type_changer(slug: str, display: str):
    @register_ability(slug)
    class _Protean(Ability):
        """The user becomes the type of the move it's about to use (Showdown `onPrepareHit`, after the move's type has been
        resolved -- so it follows Pixilate/Normalize etc.). Every move in Gen 6-8; from Gen 9 only the FIRST move after
        each switch-in. Doesn't apply to Struggle, and does nothing if it already is exactly that type."""
        async def on_prepare_hit(self, ctx, owner) -> None:
            if owner is not ctx.user or ctx.move.slug == "struggle":
                return
            if ctx.ruleset.number >= 9 and getattr(owner, "_protean_used", False):
                return
            new_type = await ctx.events.reduce("on_modify_move_type", ctx, ctx.move.spec.type_name)
            if owner.effective_types() == [new_type]:
                return
            owner.type_override = [new_type]
            owner._protean_used = True
            ctx.log(f"**{owner.name}** transformed into the **{new_type}** type!")
    _Protean.__name__ = display + "Ability"
    return _Protean


Protean = _type_changer("protean", "Protean")
Libero = _type_changer("libero", "Libero")

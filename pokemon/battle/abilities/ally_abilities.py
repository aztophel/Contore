"""Abilities that act on the holder's ALLY (Doubles): Friend Guard, Telepathy, Plus, Minus, Healer, Aroma Veil, Sweet Veil. None existed. Ported from Showdown's data/abilities.ts.

* Friend Guard: damage dealt to the holder's ALLIES is x0.75 (`onAnyModifyDamage`); Mold Breaker ignores it.
* Telepathy: the holder takes no damage from its ALLY's moves (`onTryHit`).
* Plus / Minus: x1.5 Sp. Atk while an ally (a different slot) has Plus or Minus.
* Healer: 30% per ally status at the end of the turn (order 5, sub-order 3).
* Aroma Veil / Sweet Veil: protect the holder AND its allies from Attract / Disable / Encore / Taunt / Torment, resp. from sleep and Yawn (`onAllyTryAddVolatile` / `onAllySetStatus`).
  Callers ask `ally_blocks(ctx, target, kind)` (abilities/_util.py)."""
from __future__ import annotations
import random

from ..registry import register_ability
from ..ability import Ability
from ..fixed import modify_raw
from ._util import attacker_breaks_mold


def _allies(ctx, mon) -> list:
    side = ctx.field.side_of(mon)
    return [b for b in side.active if b is not None and not b.fainted and b is not mon]


@register_ability("friend-guard")
class FriendGuard(Ability):
    async def on_modify_damage_taken(self, ctx, owner, damage: int) -> int | None:
        target = getattr(ctx, "current_target", None)
        if target is None or target is owner or getattr(ctx, "fixed_damage", False) or attacker_breaks_mold(ctx):
            return None
        if ctx.field.side_of(target) is not ctx.field.side_of(owner):
            return None
        return modify_raw(damage, 3072)                          # chainModify(0.75)


@register_ability("telepathy")
class Telepathy(Ability):
    async def on_try_hit(self, ctx, owner) -> bool | None:
        from ..move import MoveCategory
        if (ctx.user is not owner and ctx.field.side_of(ctx.user) is ctx.field.side_of(owner) and ctx.move.category is not MoveCategory.STATUS
                and not attacker_breaks_mold(ctx)):
            ctx.log(f"**{owner.name}** avoids attacks by its ally using Telepathy!")
            return True
        return None


def _plus_minus(slug: str):
    @register_ability(slug)
    class _PlusMinus(Ability):
        async def on_modify_spa(self, ctx, owner, value: int) -> int | None:
            for ally in _allies(ctx, owner):
                if ally.effective_ability() in ("plus", "minus"):
                    return modify_raw(value, 6144)               # chainModify(1.5)
            return None
    _PlusMinus.__name__ = slug.title() + "Ability"
    return _PlusMinus


PlusAbility = _plus_minus("plus")
MinusAbility = _plus_minus("minus")


@register_ability("healer")
class Healer(Ability):
    residual_order = (5, 3)
    residual_order_gen4 = (10, 3)

    async def on_end_turn(self, ctx, owner) -> None:
        if owner.fainted:
            return
        for ally in _allies(ctx, owner):
            if ally.vitals.status and random.randint(1, 100) <= 30:       # `randomChance(3, 10)`
                ctx.log(f"**{owner.name}**'s Healer cured **{ally.name}**'s status!")
                ally.vitals.status = None
                ally.vitals.status_turns = 0


def _ally_veil(kinds: frozenset, label: str):
    """The `onAlly...` half of Aroma Veil / Sweet Veil: the holder's own protection already existed (move_blocking.AromaVeil, status_immunity.SweetVeil); this extends it to its ALLIES."""
    async def on_ally_block(self, ctx, owner, target, kind: str) -> bool | None:
        if kind in kinds and not attacker_breaks_mold(ctx):
            ctx.log(f"**{target.name}** is protected by {label}!")
            return True
        return None
    return on_ally_block


from . import move_blocking as _mb, status_immunity as _si          # (the classes that already existed)
_mb.AromaVeil.on_ally_block = _ally_veil(frozenset({"volatile:attract", "volatile:disable", "volatile:encore", "volatile:taunt", "volatile:torment", "volatile:healblock"}), "Aroma Veil")
_si.SweetVeil.on_ally_block = _ally_veil(frozenset({"status:sleep", "volatile:yawn"}), "Sweet Veil")

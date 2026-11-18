"""Trapping abilities, Damp, Truant, Unnerve, Prankster, Gale Wings. Old
code: _is_trapped_by_opponent (contore.py:15074-15093), Damp (16851-16854),
Truant (16150-16154), Unnerve (15731 + berry-block sites), Prankster/Gale
Wings priority boosts (16445-16447).
"""
from __future__ import annotations

from ..registry import register_ability
from ..ability import Ability
from ._util import attacker_breaks_mold

SELF_DESTRUCT_MOVES = frozenset({"self-destruct", "explosion", "mind-blown", "misty-explosion"})


@register_ability("shadow-tag")
class ShadowTag(Ability):
    """Old code's own comment admits the Gen-6-accurate "traps everything
    including Ghosts" rule was simplified to "traps all non-Shadow-Tag
    holders" (contore.py:15085-15087) -- preserved as coded rather than
    corrected, per the instruction not to change behavior."""
    async def on_check_trapped(self, ctx, owner, fleeing) -> bool | None:
        if fleeing.identity.ability_slug == "shadow-tag":
            return None
        return True


@register_ability("arena-trap")
class ArenaTrap(Ability):
    async def on_check_trapped(self, ctx, owner, fleeing) -> bool | None:
        if not fleeing.is_grounded():
            return None
        return True


@register_ability("magnet-pull")
class MagnetPull(Ability):
    async def on_check_trapped(self, ctx, owner, fleeing) -> bool | None:
        if "Steel" not in fleeing.identity.types:
            return None
        return True


@register_ability("damp")
class Damp(Ability):
    """Showdown's `onAnyTryMove`: while any non-Mold-Broken Damp holder is
    on the field, Explosion/Self-Destruct/Misty Explosion/Mind Blown can't
    be used AT ALL -- the move is stopped at the try-move stage, so the
    would-be exploder does NOT faint. (The monolith instead blocked the
    move AND made the user blow itself up anyway, contore.py:16851-16854,
    and an earlier version of this class preserved that; replaced per the
    "match Showdown where the monolith diverges" instruction.)

    No hook of its own: the check lives in `moves/self_faint.py`'s
    `damp_blocker`, called from those moves' `on_try_use` -- Damp is a
    field-wide ("any") effect on someone ELSE's move, which none of this
    class's per-owner hooks (`on_try_hit` only sees the owner as a TARGET)
    can express. Also blocks Aftermath's damage (contact_abilities.py)."""


@register_ability("truant")
class Truant(Ability):
    async def on_switch_in(self, ctx, owner) -> None:
        owner.truant_loafing = False

    async def on_ability_before_move(self, ctx, owner) -> bool | None:
        if owner is not ctx.user:
            return None
        if getattr(owner, "truant_loafing", False):
            owner.truant_loafing = False
            ctx.log(f"**{owner.name}** is loafing around!")
            return True
        owner.truant_loafing = True
        return None


@register_ability("unnerve")
class Unnerve(Ability):
    """No hook of its own needed for the headline effect (prevents the
    OPPOSING side from eating berries) -- that's checked by whatever code
    resolves berry-eating, consulting `any(b.identity.ability_slug ==
    "unnerve" for b in opposing side)` directly, exactly like Air Lock's
    weather suppression needed no hook. Registered anyway so the slug
    isn't mistaken for unimplemented."""


@register_ability("prankster")
class Prankster(Ability):
    async def on_modify_priority(self, ctx, owner, priority: int) -> int | None:
        from ..move import MoveCategory
        if owner is not ctx.user or ctx.move.category is not MoveCategory.STATUS:
            return None
        return priority + 1


@register_ability("gale-wings")
class GaleWings(Ability):
    async def on_modify_priority(self, ctx, owner, priority: int) -> int | None:
        if owner is not ctx.user or ctx.move.spec.type_name != "Flying":
            return None
        return priority + 1

"""Sturdy, plus the remaining raw-stat-modifying abilities that don't fit
weather_and_recovery.py or damage_modifiers.py. Old code: stat_value's
atk/def/spd/spe branches (contore.py:15625-15680), Guts (16387-16391),
Sturdy (18348, 18855-18859).
"""
from __future__ import annotations

from ._util import attacker_breaks_mold, lose_held_item
from ..registry import register_ability
from ..ability import Ability


@register_ability("sturdy")
class Sturdy(Ability):
    async def on_modify_damage_taken(self, ctx, owner, damage: int) -> int | None:
        if (owner not in ctx.targets or attacker_breaks_mold(ctx)
                or owner.vitals.hp != owner.vitals.max_hp or damage < owner.vitals.hp):
            return None
        ctx.log(f"**{owner.name}** hung on using Sturdy!")
        return owner.vitals.hp - 1


def _double_atk(slug: str, display: str):
    @register_ability(slug)
    class _DoubleAtk(Ability):
        async def on_modify_atk(self, ctx, owner, value: int) -> int | None:
            return value * 2
    _DoubleAtk.__name__ = display + "Ability"
    return _DoubleAtk


HugePower = _double_atk("huge-power", "HugePower")
PurePower = _double_atk("pure-power", "PurePower")


@register_ability("guts")
class Guts(Ability):
    """Old code applies the FULL 1.5x even while burned, explicitly
    skipping the normal burn-halves-Attack rule for Guts holders
    (contore.py:16387-16391's comment: "we handle Guts here instead: skip
    the halving, apply 1.5x directly"). Modeled as a flat 1.5x on top of
    whatever the engine's own burn-halving does -- since 1.5x undoing a
    0.5x halving nets out differently (0.5*1.5=0.75x, not 1.5x) unless the
    engine specifically skips the halving for Guts holders, this is
    flagged for Track C: on_modify_atk needs to run *instead of*, not
    *alongside*, burn's own halving when Guts is present."""
    async def on_modify_atk(self, ctx, owner, value: int) -> int | None:
        if not owner.vitals.status:
            return None
        return int(value * 1.5)

    # -- [ADDITIVE / Track C] answers ability.py's on_negates_burn_penalty,
    # added once stats.py's real burn-halving existed to reconcile against.
    # See that hook's docstring for why this has to run BEFORE on_modify_atk
    # rather than the two stacking (0.5 * 1.5 = 0.75x is not the intended
    # "skip the halving, apply 1.5x directly" behaviour).
    async def on_negates_burn_penalty(self, ctx, owner) -> bool | None:
        return True if owner.vitals.status == "burn" else None


@register_ability("marvel-scale")
class MarvelScale(Ability):
    async def on_modify_def(self, ctx, owner, value: int) -> int | None:
        if not owner.vitals.status:
            return None
        return int(value * 1.5)


@register_ability("fur-coat")
class FurCoat(Ability):
    async def on_modify_def(self, ctx, owner, value: int) -> int | None:
        return value * 2


@register_ability("grass-pelt")
class GrassPelt(Ability):
    async def on_modify_def(self, ctx, owner, value: int) -> int | None:
        from .field_effects import get_terrain
        from ._util import field_of
        if get_terrain(field_of(ctx)) != "grassy":
            return None
        return int(value * 1.5)


@register_ability("quick-feet")
class QuickFeet(Ability):
    async def on_modify_spe(self, ctx, owner, value: int) -> int | None:
        if not owner.vitals.status:
            return None
        return int(value * 1.5)

    # Skips paralysis's Speed penalty entirely rather than stacking the
    # +50% above on top of an already-reduced value -- see
    # ability.py's on_negates_paralysis_penalty docstring.
    async def on_negates_paralysis_penalty(self, ctx, owner) -> bool | None:
        return True


@register_ability("unburden")
class Unburden(Ability):
    async def on_modify_spe(self, ctx, owner, value: int) -> int | None:
        if not getattr(owner, "unburden_active", False):
            return None
        return value * 2

    async def on_switch_in(self, ctx, owner) -> None:
        owner.unburden_active = False


@register_ability("defeatist")
class Defeatist(Ability):
    async def on_modify_atk(self, ctx, owner, value: int) -> int | None:
        if owner.vitals.hp > owner.vitals.max_hp // 2:
            return None
        return value // 2

    async def on_modify_spa(self, ctx, owner, value: int) -> int | None:
        if owner.vitals.hp > owner.vitals.max_hp // 2:
            return None
        return value // 2


@register_ability("flower-gift")
class FlowerGift(Ability):
    """Old code boosts the HOLDER's own Attack and Sp.Def in sun -- not an
    ally's Sp.Def like the real games' Flower Gift. Preserved as-is (see
    PROGRESS.md's note on deliberately not broadening ability scope)."""
    async def on_modify_atk(self, ctx, owner, value: int) -> int | None:
        from ._util import field_of
        from .field_effects import get_weather
        if get_weather(field_of(ctx)) not in ("sun", "harsh-sun"):
            return None
        return int(value * 1.5)

    async def on_modify_spd(self, ctx, owner, value: int) -> int | None:
        from ._util import field_of
        from .field_effects import get_weather
        if get_weather(field_of(ctx)) not in ("sun", "harsh-sun"):
            return None
        return int(value * 1.5)


@register_ability("cheek-pouch")
class CheekPouch(Ability):
    """Old code only interacts with Sitrus Berry specifically (an extra
    max_hp//3 heal on top of its normal max_hp//4), not "any berry" as in
    the real games -- see items.py's Sitrus Berry for where this extra
    amount is actually added; this class exists purely so the slug isn't
    silently unimplemented, matching entry_effects.py's Defiant/Competitive
    precedent for "documented, not duplicated" cross-references."""


@register_ability("toxic-boost")
class ToxicBoost(Ability):
    async def on_modify_atk(self, ctx, owner, value: int) -> int | None:
        from ..move import MoveCategory
        if owner.vitals.status not in ("poison", "bad_poison") or ctx.move.category is not MoveCategory.PHYSICAL:
            return None
        return int(value * 1.5)


@register_ability("flare-boost")
class FlareBoost(Ability):
    async def on_modify_spa(self, ctx, owner, value: int) -> int | None:
        if owner.vitals.status != "burn":
            return None
        return int(value * 1.5)

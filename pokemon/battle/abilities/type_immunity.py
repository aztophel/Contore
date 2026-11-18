"""Elemental-absorb/immunity abilities.

Old code: ABILITY_TYPE_IMMUNITY / ABILITY_ACTS_ON_IMMUNITY dicts
(contore.py:2780-2799), consulted from FighterState.type_immune
(16234-16250). Mold Breaker/Teravolt/Turboblaze themselves need no class
here (or anywhere) -- they only change how *other* abilities' hooks
behave, via `_util.attacker_breaks_mold`, exactly like Air Lock/Cloud Nine
needed no hook of their own for weather suppression.
"""
from __future__ import annotations

from ..registry import register_ability
from ..ability import Ability
from ..volatile import FlashFireBoost
from ._util import attacker_breaks_mold


def _blocked_by_mold_breaker(ctx, owner) -> bool:
    is_defender = owner is not ctx.user
    return is_defender and attacker_breaks_mold(ctx)


def _simple_immunity(slug: str, block_type: str, display: str):
    """Levitate/Well-Baked Body: just blocks the type, no side effect."""
    @register_ability(slug)
    class _SimpleImmunity(Ability):
        async def on_type_immunity(self, ctx, owner, type_name) -> bool | None:
            if type_name != block_type or _blocked_by_mold_breaker(ctx, owner):
                return None
            return True
    _SimpleImmunity.__name__ = display.replace(" ", "") + "Ability"
    return _SimpleImmunity


@register_ability("levitate")
class Levitate(Ability):
    """No hook of its own: Showdown handles Levitate entirely inside
    `isGrounded` (battler.py), so that Gravity / Smack Down / Iron Ball /
    Ingrain can override it and Mold Breaker can suppress it. A per-ability
    `on_type_immunity` here would make Levitate un-overridable."""
WellBakedBody = _simple_immunity("well-baked-body", "Fire", "Well-Baked Body")


def _heal_on_hit(slug: str, block_type: str, display: str):
    """Water Absorb / Volt Absorb / Earth Eater: heal 25% max HP instead
    of taking damage. Old code: ABILITY_ACTS_ON_IMMUNITY[...] == 'heal'."""
    @register_ability(slug)
    class _HealAbsorb(Ability):
        async def on_type_immunity(self, ctx, owner, type_name) -> bool | None:
            if type_name != block_type or _blocked_by_mold_breaker(ctx, owner):
                return None
            if owner.vitals.hp < owner.vitals.max_hp:
                heal = max(1, owner.vitals.max_hp // 4)
                owner.vitals.hp = min(owner.vitals.max_hp, owner.vitals.hp + heal)
                ctx.log(f"{display} restored **{owner.name}**'s HP! (+**{heal:,}**)")
            else:
                ctx.log(f"{display} made **{owner.name}** immune to the attack!")
            return True
    _HealAbsorb.__name__ = display.replace(" ", "") + "Ability"
    return _HealAbsorb


WaterAbsorb = _heal_on_hit("water-absorb", "Water", "Water Absorb")
VoltAbsorb = _heal_on_hit("volt-absorb", "Electric", "Volt Absorb")
EarthEater = _heal_on_hit("earth-eater", "Ground", "Earth Eater")


def _stat_boost_on_hit(slug: str, block_type: str, stat: str, display: str):
    """Storm Drain/Lightning Rod (+1 Sp.Atk, Gen 5+), Motor Drive
    (+1 Speed), Sap Sipper (+1 Attack)."""
    @register_ability(slug)
    class _StatBoostAbsorb(Ability):
        async def on_type_immunity(self, ctx, owner, type_name) -> bool | None:
            if type_name != block_type or _blocked_by_mold_breaker(ctx, owner):
                return None
            if slug in ("lightning-rod", "storm-drain") and ctx.ruleset.number < 5:
                return None                       # Gens 3-4: they only REDIRECT; the immunity and the +1 Sp. Atk came in Gen 5
            label = {"spa": "Sp. Atk", "spe": "Speed", "atk": "Attack"}[stat]
            if owner.stages.get(stat, 0) < 6:
                await owner.clamp_stage(ctx, stat, 1)
                ctx.log(f"{display} raised **{owner.name}**'s {label}!")
            else:
                ctx.log(f"{display} made **{owner.name}** immune to the attack!")
            return True
    _StatBoostAbsorb.__name__ = display.replace(" ", "") + "Ability"
    return _StatBoostAbsorb


StormDrain = _stat_boost_on_hit("storm-drain", "Water", "spa", "Storm Drain")
LightningRod = _stat_boost_on_hit("lightning-rod", "Electric", "spa", "Lightning Rod")
MotorDrive = _stat_boost_on_hit("motor-drive", "Electric", "spe", "Motor Drive")
SapSipper = _stat_boost_on_hit("sap-sipper", "Grass", "atk", "Sap Sipper")


@register_ability("flash-fire")
class FlashFire(Ability):
    async def on_type_immunity(self, ctx, owner, type_name) -> bool | None:
        if type_name != "Fire" or _blocked_by_mold_breaker(ctx, owner):
            return None
        if not owner.has(FlashFireBoost):
            owner.add(FlashFireBoost(), ctx.engine.events)
        ctx.log(f"**{owner.name}**'s Flash Fire raised its Fire power!")
        return True

    async def on_switch_out(self, ctx, owner) -> None:
        owner.remove(FlashFireBoost, ctx.engine.events)
    # The actual x1.5 power boost lives on FlashFireBoost itself
    # (volatile.py) -- it's the Volatile that's attached/detached here,
    # not a separate copy of the multiplier logic.


@register_ability("wonder-guard")
class WonderGuard(Ability):
    """Old code returns (False, 'wonder_guard') from type_immune -- i.e. it
    does NOT block via on_type_immunity like the others; it's checked
    separately, after full type-effectiveness is computed, and blocks only
    non-super-effective hits (contore.py:18742, 19533, 19694). Modeled here
    as an effectiveness override for symmetry with Scrappy below."""
    async def on_modify_type_effectiveness(self, ctx, owner, effectiveness: float) -> float | None:
        if owner is ctx.user or _blocked_by_mold_breaker(ctx, owner):
            return None
        if effectiveness < 2.0 and effectiveness != 0.0 and ctx.move.category.value != "status":
            ctx.log("Wonder Guard blocked the attack!")
            return 0.0
        return None


@register_ability("scrappy")
class Scrappy(Ability):
    """Showdown's `move.ignoreImmunity` for Normal and Fighting: Ghost's immunity to them is ignored. damage.py asks this via
    `on_ignore_type_immunity` and treats the immune COMPONENT as neutral. (The old `on_modify_type_effectiveness` version
    returned a flat 1.0 whenever the result was 0, so Fighting into Gengar (Ghost/Poison) did x1 instead of x0.5 and into
    Spiritomb (Ghost/Dark) x1 instead of x2.)"""
    async def on_ignore_type_immunity(self, ctx, owner, move_type: str) -> bool | None:
        if owner is ctx.user and move_type in ("Normal", "Fighting"):
            return True
        return None

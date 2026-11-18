"""Moves whose power comes from a formula (HP ratio, Speed ratio, weight
comparison) rather than a flat MoveSpec.base_power. Each just overrides
on_base_power -- no engine-level special-casing by move name, which was
the entire point of this refactor (see moves/examples.py's GyroBallMove
for the original demonstration of the pattern this file continues). Old
code: apply_move_full.txt's per-move variable-power branches
(contore.py's equivalent, pre-extraction).

Formulas cross-checked against Pokemon Showdown's data/moves.ts
basePowerCallback implementations, not just re-derived from memory of the
in-game breakpoint tables.
"""
from __future__ import annotations

from ..move import Move, BasicMove
from ..context import MoveContext
from ..registry import register_move


@register_move("electro-ball")
class ElectroBallMove(BasicMove):
    """Old code: apply_move_full.txt's Electro Ball branch. Pokemon
    Showdown: `ratio = floor(userSpe / targetSpe)`, bp = [40,60,80,120,150]
    at ratio index min(ratio, 4)."""
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        user_speed = await ctx.user.stat_async("spe", ctx)
        target_speed = await target.stat_async("spe", ctx)
        ratio = user_speed // target_speed if target_speed > 0 else 0
        return [40, 60, 80, 120, 150][min(ratio, 4)]


def _hp_ratio_move(slug: str):
    """Reversal/Flail: power scales with the USER's own remaining HP
    fraction, via a 6-step breakpoint table (not a smooth curve) -- Pokemon
    Showdown: `ratio = max(floor(hp * 48 / maxhp), 1)`, then a breakpoint
    lookup on that ratio."""
    @register_move(slug)
    class _HPRatioMove(BasicMove):
        async def on_base_power(self, ctx: MoveContext, target) -> int | None:
            user = ctx.user
            ratio = max((user.vitals.hp * 48) // user.vitals.max_hp, 1)
            if ratio < 2:
                return 200
            if ratio < 5:
                return 150
            if ratio < 10:
                return 100
            if ratio < 17:
                return 80
            if ratio < 33:
                return 40
            return 20
    _HPRatioMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _HPRatioMove


ReversalMove = _hp_ratio_move("reversal")
FlailMove = _hp_ratio_move("flail")


def _hp_fraction_power_move(slug: str, base: int = 150):
    """Eruption/Water Spout: power = base_power * user's current HP
    fraction (a smooth curve, unlike Reversal/Flail's breakpoints)."""
    @register_move(slug)
    class _HPFractionPowerMove(BasicMove):
        async def on_base_power(self, ctx: MoveContext, target) -> int | None:
            user = ctx.user
            return max(1, int(base * user.vitals.hp / user.vitals.max_hp))
    _HPFractionPowerMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _HPFractionPowerMove


EruptionMove = _hp_fraction_power_move("eruption")
WaterSpoutMove = _hp_fraction_power_move("water-spout")


def _target_hp_fraction_move(slug: str):
    """Wring Out/Crush Grip: power scales with the TARGET's remaining HP
    fraction (not the user's), max 120, min 1. Pokemon Showdown's formula
    is written in the game's internal 4096ths fixed-point arithmetic;
    reproduced with plain floats here since this bot doesn't otherwise
    model that fixed-point system, which only matters for extremely
    marginal breakpoint-rounding edge cases."""
    @register_move(slug)
    class _TargetHPFractionMove(BasicMove):
        async def on_base_power(self, ctx: MoveContext, target) -> int | None:
            hp, mx = target.vitals.hp, target.vitals.max_hp
            if ctx.ruleset.number <= 4:                                 # Showdown's gen4 mod
                return hp * 120 // mx + 1
            # Showdown's 4096ths formula (the float version was off by one at breakpoints): floor(floor((120 * (100 * floor(hp * 4096 / max)) + 2047) / 4096) / 100) || 1
            return max(1, ((120 * (100 * (hp * 4096 // mx)) + 2047) // 4096) // 100)
    _TargetHPFractionMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _TargetHPFractionMove


WringOutMove = _target_hp_fraction_move("wring-out")
CrushGripMove = _target_hp_fraction_move("crush-grip")


# kg (Showdown's thresholds are in hectograms: 2000 / 1000 / 500 / 250 / 100). They were compared against a weight in KILOGRAMS, so a 460 kg Snorlax got 60 instead of 120.
_WEIGHT_BREAKPOINTS = [(200, 120), (100, 100), (50, 80), (25, 60), (10, 40), (0, 20)]


def _target_weight_move(slug: str):
    """Low Kick/Grass Knot: power depends on the TARGET's weight alone."""
    @register_move(slug)
    class _TargetWeightMove(BasicMove):
        async def on_base_power(self, ctx: MoveContext, target) -> int | None:
            weight = await target.effective_weight(ctx)
            for threshold, bp in _WEIGHT_BREAKPOINTS:
                if weight >= threshold:
                    return bp
            return 20
    _TargetWeightMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _TargetWeightMove


LowKickMove = _target_weight_move("low-kick")
GrassKnotMove = _target_weight_move("grass-knot")


_WEIGHT_RATIO_BREAKPOINTS = [(5, 120), (4, 100), (3, 80), (2, 60)]


def _weight_ratio_move(slug: str):
    """Heavy Slam/Heat Crash: power depends on the RATIO of the user's
    weight to the target's -- heavier user relative to target = more
    power, unlike Low Kick/Grass Knot which only look at the target."""
    @register_move(slug)
    class _WeightRatioMove(BasicMove):
        async def on_base_power(self, ctx: MoveContext, target) -> int | None:
            user_weight = await ctx.user.effective_weight(ctx)
            target_weight = await target.effective_weight(ctx)
            for multiple, bp in _WEIGHT_RATIO_BREAKPOINTS:
                if user_weight >= target_weight * multiple:
                    return bp
            return 40
    _WeightRatioMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _WeightRatioMove


HeavySlamMove = _weight_ratio_move("heavy-slam")
HeatCrashMove = _weight_ratio_move("heat-crash")

"""Pure stat-stage math: per-stat and all-stat drop immunity, Contrary,
Simple. Old code: FighterState.clamp_stage (contore.py:16193-16218) plus
the separate CLEAR_BODY_ABS all-stat check (19204).
"""
from __future__ import annotations

from ..registry import register_ability
from ..ability import Ability
from ._util import PER_STAT_DROP_IMMUNITY, FULL_STAT_DROP_IMMUNITY_SLUGS, attacker_breaks_mold


def _per_stat_immunity(slug: str, stat: str, display: str):
    @register_ability(slug)
    class _PerStatImmunity(Ability):
        async def on_stat_drop_immunity(self, ctx, owner, stat_key) -> bool | None:
            if stat_key != stat or attacker_breaks_mold(ctx):
                return None
            ctx.log(f"**{owner.name}**'s {display} protected its {stat_key.title()}!")
            return True
    _PerStatImmunity.__name__ = display.replace(" ", "") + "Ability"
    return _PerStatImmunity


HyperCutter = _per_stat_immunity("hyper-cutter", "atk", "Hyper Cutter")
# NOTE: `clamp_stage` normalises stage keys to "atk"/"def"/"spa"/"spd"/"spe"/"acc"/"eva" BEFORE dispatching, so these must use those keys:
# Big Pecks used "defense" and Keen Eye "accuracy", which never matched -- both immunities were dead.
BigPecks = _per_stat_immunity("big-pecks", "def", "Big Pecks")
KeenEye = _per_stat_immunity("keen-eye", "acc", "Keen Eye")


async def _keen_eye_ignores_evasion(self, ctx, owner) -> bool | None:
    """Showdown `onModifyMove: move.ignoreEvasion = true` (the gen5 mod removes it, so Gen 6+)."""
    return True if owner is ctx.user and ctx.ruleset.number >= 6 else None


KeenEye.on_ignore_evasion = _keen_eye_ignores_evasion


@register_ability("illuminate")
class Illuminate(Ability):
    """Gen 9 (Showdown base data; the gen8 mod strips it): accuracy can't be lowered, like Keen Eye. (Its `ignoreEvasion` half
    is not modelled.)"""
    async def on_stat_drop_immunity(self, ctx, owner, stat_key) -> bool | None:
        if stat_key != "acc" or ctx.ruleset.number < 9 or attacker_breaks_mold(ctx):
            return None
        ctx.log(f"**{owner.name}**'s Illuminate protected its Accuracy!")
        return True

    async def on_ignore_evasion(self, ctx, owner) -> bool | None:
        return True if owner is ctx.user and ctx.ruleset.number >= 9 else None


@register_ability("minds-eye")
class MindsEye(Ability):
    """Gen 7+: accuracy can't be lowered, the holder's moves ignore the target's evasion, and Normal/Fighting moves ignore Ghost's immunity."""
    async def on_stat_drop_immunity(self, ctx, owner, stat_key) -> bool | None:
        if stat_key != "acc" or attacker_breaks_mold(ctx):
            return None
        ctx.log(f"**{owner.name}**'s Mind's Eye protected its Accuracy!")
        return True

    async def on_ignore_evasion(self, ctx, owner) -> bool | None:
        return True if owner is ctx.user else None

    async def on_ignore_type_immunity(self, ctx, owner, move_type: str) -> bool | None:
        return True if owner is ctx.user and move_type in ("Normal", "Fighting") else None


@register_ability("unaware")
class Unaware(Ability):
    """Showdown `onAnyModifyBoost`, in both directions (and `breakable`): as the ATTACKER it ignores the target's Defense / Sp. Def / evasion
    boosts; as the TARGET it ignores the attacker's Attack / Sp. Atk / Defense / accuracy boosts."""
    async def on_ignore_evasion(self, ctx, owner) -> bool | None:
        return True if owner is ctx.user else None

    async def on_ignore_defensive_boosts(self, ctx, owner) -> bool | None:
        return True if owner is ctx.user else None

    async def on_ignore_accuracy_boost(self, ctx, owner) -> bool | None:
        return True if owner in ctx.targets and not attacker_breaks_mold(ctx) else None

    async def on_ignore_offensive_boosts(self, ctx, owner) -> bool | None:
        return True if owner in ctx.targets and not attacker_breaks_mold(ctx) else None


def _full_immunity(slug: str, display: str):
    @register_ability(slug)
    class _FullImmunity(Ability):
        async def on_stat_drop_immunity(self, ctx, owner, stat_key) -> bool | None:
            if attacker_breaks_mold(ctx):
                return None
            ctx.log(f"**{owner.name}**'s {display} prevents stat loss!")
            return True
    _FullImmunity.__name__ = display.replace(" ", "") + "Ability"
    return _FullImmunity


ClearBody = _full_immunity("clear-body", "Clear Body")
WhiteSmoke = _full_immunity("white-smoke", "White Smoke")
FullMetalBody = _full_immunity("full-metal-body", "Full Metal Body")


@register_ability("contrary")
class Contrary(Ability):
    async def on_modify_stat_change(self, ctx, owner, stat: str, delta: int) -> int | None:
        return -delta


@register_ability("simple")
class Simple(Ability):
    async def on_modify_stat_change(self, ctx, owner, stat: str, delta: int) -> int | None:
        if ctx.ruleset.number == 4:
            return None                      # Gen 4: Simple doubles the EFFECTIVE stage when a stat is computed (battler._staged), not each change
        return delta * 2

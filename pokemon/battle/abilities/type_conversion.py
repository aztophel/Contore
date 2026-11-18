"""The last few ability gaps flagged in PROGRESS.md: move-type-changing
abilities, Suction Cups, Stench, Steadfast, Inner Focus's flinch-block,
and the two weight-modifying abilities (Heavy Metal/Light Metal). Old
code: contore.py:18355-18368 (type conversion), 18280-18288 (suction
cups), 19300-19319 (stench/steadfast/inner-focus), 16691-16698 (weight).
"""
from __future__ import annotations
import random

from ..registry import register_ability
from ..ability import Ability
from ._util import attacker_breaks_mold
from ..fixed import modify_raw

NORMAL_CONVERT_ABILITY = {"aerilate": "Flying", "pixilate": "Fairy", "refrigerate": "Ice"}

# Moves these abilities leave alone (Showdown's `noModifyType` lists). Normalize additionally skips Hidden Power and
# Struggle; the -ate abilities only touch moves that are Normal to begin with.
_ATE_SKIPS = frozenset({"judgment", "multi-attack", "natural-gift", "revelation-dance", "techno-blast",
                        "terrain-pulse", "weather-ball"})
_NORMALIZE_SKIPS = _ATE_SKIPS | {"hidden-power", "struggle"}


def _type_boost(gen: int) -> int:
    """The x/4096 power boost of a type changer: 5325 (x1.3) in Gen 6, 4915 (x1.2) from Gen 7."""
    return 5325 if gen <= 6 else 4915


def _type_convert(slug: str, new_type: str, display: str):
    @register_ability(slug)
    class _TypeConvert(Ability):
        def _converts(self, ctx) -> bool:
            return ctx.move.spec.type_name == "Normal" and ctx.move.slug not in _ATE_SKIPS

        async def on_modify_move_type(self, ctx, owner, move_type: str) -> str | None:
            if owner is not ctx.user or not self._converts(ctx):
                return None
            return new_type

        async def on_modify_power(self, ctx, owner, power: int) -> int | None:
            if owner is not ctx.user or not self._converts(ctx):
                return None
            return modify_raw(power, _type_boost(ctx.ruleset.number))
    _TypeConvert.__name__ = display + "Ability"
    return _TypeConvert


Aerilate = _type_convert("aerilate", "Flying", "Aerilate")
Pixilate = _type_convert("pixilate", "Fairy", "Pixilate")
Refrigerate = _type_convert("refrigerate", "Ice", "Refrigerate")


@register_ability("normalize")
class Normalize(Ability):
    """Every move becomes Normal (bar the `_NORMALIZE_SKIPS`). From Gen 7 it also gets the x1.2 boost; before that
    it had none (Showdown's gen6 mod clears `onBasePower`)."""
    async def on_modify_move_type(self, ctx, owner, move_type: str) -> str | None:
        if owner is not ctx.user or ctx.move.slug in _NORMALIZE_SKIPS:
            return None
        return "Normal"

    async def on_modify_power(self, ctx, owner, power: int) -> int | None:
        if owner is not ctx.user or ctx.move.slug in _NORMALIZE_SKIPS or ctx.ruleset.number < 7:
            return None
        return modify_raw(power, 4915)


@register_ability("suction-cups")
class SuctionCups(Ability):
    async def on_drag_out(self, ctx, owner) -> bool | None:
        """Showdown `onDragOut` (priority 1, breakable): the holder can't be dragged out by Roar / Whirlwind / Dragon Tail / Circle Throw."""
        if owner is not getattr(ctx, "drag_target", None) or attacker_breaks_mold(ctx):
            return None
        ctx.log(f"**{owner.name}**'s Suction Cups held it in place!")
        return True

    async def on_check_trapped(self, ctx, owner, fleeing) -> bool | None:
        """Old code only checks this for Roar/Whirlwind specifically
        (contore.py:18286-18288), not switch-forcing generally (e.g.
        Dragon Tail/Circle Throw/Red Card/Eject Button/Whirlwind's own
        variants aren't covered) -- preserved narrowly as coded rather
        than broadened to every forced-switch source."""
        if fleeing is not owner or attacker_breaks_mold(ctx):
            return None
        if getattr(ctx.move, "slug", None) not in ("roar", "whirlwind"):
            return None
        ctx.log(f"**{owner.name}**'s Suction Cups held it in place!")
        return True


@register_ability("stench")
class Stench(Ability):
    async def on_modify_secondary_chance(self, ctx, owner, chance: int) -> int | None:
        from ..move import MoveCategory
        if owner is not ctx.user or chance or ctx.move.category is MoveCategory.STATUS:
            return None
        return 10


@register_ability("steadfast")
class Steadfast(Ability):
    async def on_flinched(self, ctx, owner) -> None:
        msg = await owner.clamp_stage(ctx, "spe", 1)
        if msg:
            ctx.log(msg)


@register_ability("inner-focus")
class InnerFocus(Ability):
    """Blocks flinching outright (contore.py:19316's `_dab_flinch !=
    "inner-focus"` check) -- separate from its role in ANTI_INTIMIDATE
    (entry_effects.py), which is Intimidate-specific and not expressed
    through this hook."""
    async def on_check_flinch_immune(self, ctx, owner) -> bool | None:
        if owner not in ctx.targets or attacker_breaks_mold(ctx):
            return None
        return True


@register_ability("heavy-metal")
class HeavyMetal(Ability):
    async def on_modify_weight(self, ctx, owner, weight_kg: float) -> float | None:
        return weight_kg * 2.0


@register_ability("light-metal")
class LightMetal(Ability):
    async def on_modify_weight(self, ctx, owner, weight_kg: float) -> float | None:
        return weight_kg / 2.0

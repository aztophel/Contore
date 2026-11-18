"""Held items that had no implementation, ported from Showdown's data/items.ts (Big Root, Power Herb, Covert Cloak, Clear Amulet, Throat Spray, the four Terrain Seeds,
Absorb Bulb / Luminous Moss / Cell Battery / Snowball, Ability Shield). Goes in contore/pokemon/battle/held_items/misc_items.py (auto-discovered)."""
from __future__ import annotations

import math

from ..fixed import modify_raw
from ..items import HeldItem, register_item
from ..abilities._util import lose_held_item


@register_item("Big Root")
class BigRoot(HeldItem):
    async def on_modify_incoming_drain(self, ctx, owner, amount: int) -> int | None:
        if ctx.ruleset.number <= 4:
            return math.floor(amount * 1.3)                       # Showdown gen4 mod: a plain `Math.floor(damage * 1.3)`
        return modify_raw(amount, 5324)


@register_item("Power Herb")
class PowerHerb(HeldItem):
    """The effect is in moves/charging.py (`effective_item() == "Power Herb"`); this just registers the item."""


@register_item("Covert Cloak")
class CovertCloak(HeldItem):
    async def on_modify_secondary_chance(self, ctx, owner, chance: int) -> int | None:
        if owner not in ctx.targets or getattr(ctx, "secondary_on_user", False):
            return None
        return 0


@register_item("Clear Amulet")
class ClearAmulet(HeldItem):
    async def on_stat_drop_immunity(self, ctx, owner, stat_key) -> bool | None:
        ctx.log(f"**{owner.name}**'s Clear Amulet prevents its stats from being lowered!")
        return True


@register_item("Throat Spray")
class ThroatSpray(HeldItem):
    async def on_after_move_self(self, ctx, owner) -> None:
        from ..move import MoveFlag
        if owner is not ctx.user or owner.fainted or not ctx.move.has_flag(MoveFlag.SOUND):
            return
        msg = await owner.clamp_stage(ctx, "spa", 1)
        if msg:
            ctx.log(msg)
        lose_held_item(owner, ctx)


def _terrain_seed(item_name: str, terrain: str, stat: str):
    @register_item(item_name)
    class _Seed(HeldItem):
        async def on_update(self, ctx, owner) -> None:
            if owner.fainted or ctx.field.terrain != terrain:
                return
            msg = await owner.clamp_stage(ctx, stat, 1)
            ctx.log(msg or f"**{owner.name}** used its {item_name}!")
            lose_held_item(owner, ctx)
    _Seed.__name__ = item_name.replace(" ", "") + "Item"
    return _Seed


for _n, _t, _s in (("Electric Seed", "electric", "def"), ("Grassy Seed", "grassy", "def"), ("Misty Seed", "misty", "spd"), ("Psychic Seed", "psychic", "spd")):
    _terrain_seed(_n, _t, _s)


def _hit_booster(item_name: str, move_type: str, stat: str):
    @register_item(item_name)
    class _HitBooster(HeldItem):
        async def on_after_damaged(self, ctx, owner, damage: int) -> None:
            if owner not in ctx.targets or owner.fainted or damage <= 0 or ctx.move.spec.type_name != move_type:
                return
            msg = await owner.clamp_stage(ctx, stat, 1)
            ctx.log(msg or f"**{owner.name}** used its {item_name}!")
            lose_held_item(owner, ctx)
    _HitBooster.__name__ = item_name.replace(" ", "") + "Item"
    return _HitBooster


for _n, _ty, _s in (("Absorb Bulb", "Water", "spa"), ("Luminous Moss", "Water", "spd"), ("Cell Battery", "Electric", "atk"), ("Snowball", "Ice", "atk")):
    _hit_booster(_n, _ty, _s)


@register_item("Ability Shield")
class AbilityShield(HeldItem):
    """A marker: the ability-changing moves check `effective_item() == "Ability Shield"` (moves/ability_moves.py `_shielded`)."""

"""Abilities that read the item-use tracking (`Battler.last_item` / `used_item_this_turn`, set by `lose_held_item` when an item is USED, not taken): Pickup. (Symbiosis lives in `lose_held_item` itself.)"""
from __future__ import annotations

import random

from ..ability import Ability
from ..registry import register_ability


@register_ability("pickup")
class Pickup(Ability):
    """Showdown `pickup` (Gen 5+; Gens 3-4 only pick up AFTER a battle, which the engine doesn't model): at the end of the turn a Pokemon with no item takes the item an adjacent Pokemon USED this turn
    (`lastItem && usedItemThisTurn`); a random one if several."""
    # Showdown order 28 / sub-order 2: after the berries were eaten (their hooks run at the default order 29 here), so this runs a hair later
    residual_order = (29, 9)

    async def on_end_turn(self, ctx, owner) -> None:
        if ctx.ruleset.number < 5 or owner.fainted or owner.identity.held_item:
            return
        pool = [b for b in ctx.field.all_battlers() if b is not owner and b.last_item and b.used_item_this_turn]
        if not pool:
            return
        source = random.choice(pool)
        item, source.last_item = source.last_item, None
        owner.identity.held_item = item
        owner.unburden_active = False
        ctx.engine.sync_ability_and_item(owner)
        ctx.log(f"**{owner.name}** found one {item}!")

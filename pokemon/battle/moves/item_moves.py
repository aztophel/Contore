"""Moves that MOVE a held item: Trick, Switcheroo, Bestow (status) and Thief, Covet (damaging) -- none of them existed (only Knock Off did), so a Trick left both Pokemon with their own items.
Ported from Showdown's data/moves.ts; `can_take_item` is `Pokemon.takeItem()` (Sticky Hold, Gen 4 Multitype). NOT ported: the species-specific un-takeable items (Mega Stones on their own
Pokemon, Arceus' plates, Silvally's memories, Z-Crystals, Booster Energy ...)."""
from __future__ import annotations

from ..move import Move, BasicMove
from ..context import MoveContext
from ..registry import register_move
from ..abilities._util import lose_held_item, attacker_breaks_mold


def _failed(ctx) -> None:
    ctx.log("But it failed!")


def can_take_item(ctx: MoveContext, holder, taker) -> bool:
    """Showdown `takeItem(source)`: the holder must have an item, and Sticky Hold (not suppressed, not mold-broken) keeps it from anyone but itself (Sticky Barb excepted)."""
    item = holder.identity.held_item
    if not item:
        return False
    if holder is not taker:
        if holder.effective_ability() == "sticky-hold" and item != "Sticky Barb" and not attacker_breaks_mold(ctx):
            return False
        if ctx.ruleset.number == 4 and "multitype" in (holder.effective_ability(), taker.effective_ability()):
            return False
    return True


def set_item(ctx: MoveContext, mon, item: str | None) -> None:
    """Give `mon` `item` (or nothing), re-attaching the item's effect; losing one marks Unburden, gaining one ends it."""
    if item:
        mon.identity.held_item = item
        mon.unburden_active = False
        ctx.engine.sync_ability_and_item(mon)
    else:
        lose_held_item(mon, ctx, taken=True)


class _Swap(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        user = ctx.user
        mine, yours = user.identity.held_item, target.identity.held_item
        if (not mine and not yours) or (mine and not can_take_item(ctx, user, user)) or (yours and not can_take_item(ctx, target, user)):
            _failed(ctx); return
        set_item(ctx, target, mine)
        set_item(ctx, user, yours)
        ctx.log(f"**{user.name}** switched items with its target!")


@register_move("trick")
class TrickMove(_Swap):
    pass


@register_move("switcheroo")
class SwitcherooMove(_Swap):
    pass


@register_move("bestow")
class BestowMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        user = ctx.user
        if target.identity.held_item or not can_take_item(ctx, user, user):
            _failed(ctx); return
        item = user.identity.held_item
        set_item(ctx, target, item)
        set_item(ctx, user, None)
        ctx.log(f"**{target.name}** received {item} from **{user.name}**!")


class _Stealer(BasicMove):
    """Thief / Covet: after a damaging hit that reached the target (not a Substitute), the user takes its item if the user has none (Showdown `onAfterHit`)."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        await super().on_hit(ctx, target)
        user = ctx.user
        if user.identity.held_item or user.fainted or getattr(ctx, "hit_blocked", False) or getattr(ctx, "hits_on_target", 1) <= 0:
            return
        if not can_take_item(ctx, target, user):
            return
        item = target.identity.held_item
        set_item(ctx, user, item)
        set_item(ctx, target, None)
        ctx.log(f"**{user.name}** stole **{target.name}**'s {item}!")


@register_move("thief")
class ThiefMove(_Stealer):
    pass


@register_move("covet")
class CovetMove(_Stealer):
    pass


# -- Fling / Natural Gift ------------------------------------------------------------------------------------------------------------------------------------

_ITEM_MOVE_DATA: dict | None = None


def _item_move_data(gen: int) -> dict:
    """data/item_move_data.json (tests/tools/make_item_move_data.js): Showdown's `item.fling` and `item.naturalGift` per generation (Fling / Natural Gift exist from Gen 4)."""
    global _ITEM_MOVE_DATA
    if _ITEM_MOVE_DATA is None:
        import json
        from pathlib import Path
        _ITEM_MOVE_DATA = json.loads((Path(__file__).resolve().parents[3] / "data" / "item_move_data.json").read_text(encoding="utf-8"))
    return _ITEM_MOVE_DATA[str(max(4, min(9, gen)))]


@register_move("fling")
class FlingMove(BasicMove):
    """Throws the held item: its Fling power, plus its status / flinch effect, and the item is used up (even if the move misses). Berry and Herb EFFECTS (the target eating the berry, White Herb,
    Mental Herb) are NOT ported -- only their power and the loss of the item."""
    async def on_try_use(self, ctx: MoveContext):
        user = ctx.user
        item = user.identity.held_item
        data = _item_move_data(ctx.ruleset.number)["fling"].get(item) if item and user.effective_item() else None
        if not data or not can_take_item(ctx, user, user):
            return "But it failed!"
        ctx.fling = (item, data)
        if ctx.ruleset.number <= 4:
            ctx.fling_defer = True                     # Gen 4: the item is still held while the move resolves (Life Orb still recoils); it goes AFTER the move
        else:
            lose_held_item(user, ctx)                  # Showdown: the `fling` volatile removes it at the next update, hit or miss
        return None

    async def on_after_move(self, ctx: MoveContext, connected: bool) -> None:
        await super().on_after_move(ctx, connected)
        if getattr(ctx, "fling_defer", False):
            lose_held_item(ctx.user, ctx)

    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        return ctx.fling[1]["bp"]

    async def on_hit(self, ctx: MoveContext, target) -> None:
        await super().on_hit(ctx, target)
        if ctx.hit_blocked or getattr(ctx, "hits_on_target", 1) <= 0 or target.fainted:
            return
        _, data = ctx.fling
        if data.get("status"):
            from ..status import inflict_status
            await inflict_status(ctx, target, {"psn": "poison", "tox": "bad_poison", "brn": "burn", "par": "paralysis", "slp": "sleep", "frz": "freeze"}[data["status"]])
        elif data.get("volatile") == "flinch":
            from ..volatile import Flinch
            if not getattr(target, "acted_this_turn", False):
                target.add(Flinch(), ctx.engine.events)


@register_move("natural-gift")
class NaturalGiftMove(BasicMove):
    """Power and TYPE come from the held berry, which is used up (Showdown `onModifyType` / `onPrepareHit`); fails without a berry (or with the item suppressed)."""
    async def on_try_use(self, ctx: MoveContext):
        user = ctx.user
        item = user.identity.held_item
        data = _item_move_data(ctx.ruleset.number)["natural_gift"].get(item) if item and user.effective_item() else None
        if not data:
            return "But it failed!"
        ctx.natural_gift = data
        lose_held_item(user, ctx)
        return None

    async def on_modify_type(self, ctx: MoveContext, target, type_name: str) -> str | None:
        return getattr(ctx, "natural_gift", {}).get("type")

    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        return ctx.natural_gift["bp"]

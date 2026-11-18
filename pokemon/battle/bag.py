"""Bag items usable mid-battle: Potions, Revives, status cures, X-items,
Ether/Elixir, and Poke Balls -- ported from the monolith's `_apply_item` /
`_throw_ball`, and the reason this module exists: `ItemAction` used to be
accepted by the engine and then do NOTHING, so the whole Bag (and with it,
catching) was dead in the merged bot.

Design: each usable item is a `BagItem` subclass, built from the existing
data tables (`dex.items_repo.BATTLE_ITEMS` / `POKEBALL_DATA`) by
`resolve_bag_item` -- no per-item hardcoding, and a new item type is a new
subclass plus one line in `_BY_TYPE`. A BagItem

  * `blocked_reason(...)` -- says why it can't be used on a target (the UI
    uses this to only offer valid targets; `use` re-checks as a guard).
  * `use(...)` -- applies the effect, logs to `engine.log`, and returns
    whether the item was CONSUMED. An item that has no effect is refused
    (not consumed) instead of silently wasted as the monolith did, e.g. a
    Potion at full HP.

The engine stays free of DB access: quantities live in `Side.bags`, the
engine decrements them and records the use in `Side.bag_used`, and the UI
layer persists that after each turn (`battle_bridge.flush_bag_usage`).

Catching: `PokeBall.use` rolls the throw with the pure math in
`pokemon.commands.catching` and, on success, sets `engine.catch` and ends the
battle via `engine.conclude(...)`; PERSISTING the new Pokemon is the bridge's
job (`battle_bridge.finalize_catch`), not the engine's.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import TYPE_CHECKING

from .context import MoveContext

if TYPE_CHECKING:
    from .battler import Battler
    from .context import BattleContext
    from .engine import BattleEngine
    from .field import Side
    from ..ui.actions import ItemAction


@dataclass
class CatchOutcome:
    ball: str
    battler: "Battler"   # the wild Pokemon that was caught (final HP/status/moves)
    critical: bool
    shakes: int


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BagItem:
    # "ball" | "medicine" | "battle" -- which Bag tab it lives under.
    category: str = "battle"
    # Who the player picks: "none" (the active Pokemon / the foe, implicit),
    # "alive_ally" or "fainted_ally" (a roster member).
    target_kind: str = "none"

    def __init__(self, name: str, data: dict):
        self.name, self.data = name, data

    def blocked_reason(self, engine: "BattleEngine", side: "Side", target: "Battler") -> str | None:
        return None

    async def use(self, engine: "BattleEngine", ctx: "BattleContext", side: "Side",
                  action: "ItemAction") -> bool:
        raise NotImplementedError

    def _refuse(self, engine: "BattleEngine", reason: str) -> bool:
        engine.log.append(reason)
        return False


NO_EFFECT = "It won't have any effect."


def _rejoin_bench(side: "Side", mon: "Battler") -> None:
    """A revived Pokemon that was previously swapped out is on neither
    `active` nor `bench` (a fainted one is only in `roster`); put it back so
    it can be sent in again."""
    if mon not in side.active and mon not in side.bench:
        side.bench.append(mon)


# ---------------------------------------------------------------------------
# Medicine
# ---------------------------------------------------------------------------

class HealItem(BagItem):
    """Potion family + Full Restore (which also cures status)."""
    category, target_kind = "medicine", "alive_ally"

    def __init__(self, name, data):
        super().__init__(name, data)
        self.full_cure = data["type"] == "full"

    def blocked_reason(self, engine, side, target):
        if target.fainted:
            return f"**{target.name}** has fainted."
        v = target.vitals
        if v.hp >= v.max_hp and not (self.full_cure and v.status):
            return NO_EFFECT
        return None

    async def use(self, engine, ctx, side, action):
        target = action.target or action.user
        if (why := self.blocked_reason(engine, side, target)):
            return self._refuse(engine, why)
        healed = target.heal(self.data["hp"])
        text = f"Used a **{self.name}** on **{target.name}**!"
        if healed:
            text += f" (+**{healed:,}** HP)"
        if self.full_cure and target.vitals.status:
            target.vitals.status, target.vitals.status_turns, target.vitals.toxic_counter = None, 0, 0
            text += " Status cured."
        engine.log.append(text)
        return True


class ReviveItem(BagItem):
    category, target_kind = "medicine", "fainted_ally"

    def blocked_reason(self, engine, side, target):
        return None if target.fainted else NO_EFFECT

    async def use(self, engine, ctx, side, action):
        target = action.target or action.user
        if (why := self.blocked_reason(engine, side, target)):
            return self._refuse(engine, why)
        v = target.vitals
        v.hp = v.max_hp if self.data["type"] == "max_revive" else max(1, v.max_hp // 2)
        v.status, v.status_turns, v.toxic_counter = None, 0, 0
        _rejoin_bench(side, target)
        engine.log.append(f"Used a **{self.name}** on **{target.name}**! (+**{v.hp:,}** HP)")
        return True


# Antidote cures both kinds of poison, as in the games. (The monolith compared
# `status == cures` exactly, so its Antidote ignored "bad_poison".)
_CURES = {"poison": {"poison", "bad_poison"}}


class CureItem(BagItem):
    category, target_kind = "medicine", "alive_ally"

    def _cures(self, status: str | None) -> bool:
        wanted = self.data["cures"]
        return bool(status) and (wanted == "all" or status in _CURES.get(wanted, {wanted}))

    def blocked_reason(self, engine, side, target):
        if target.fainted:
            return f"**{target.name}** has fainted."
        return None if self._cures(target.vitals.status) else NO_EFFECT

    async def use(self, engine, ctx, side, action):
        target = action.target or action.user
        if (why := self.blocked_reason(engine, side, target)):
            return self._refuse(engine, why)
        target.vitals.status, target.vitals.status_turns, target.vitals.toxic_counter = None, 0, 0
        engine.log.append(f"Used a **{self.name}** on **{target.name}**! Status cured.")
        return True


class PPItem(BagItem):
    """Ether/Max Ether restore the move with the LEAST PP; Elixir/Max Elixir
    restore every move (monolith: `min(moves, key=pp)` vs all)."""
    category, target_kind = "medicine", "alive_ally"

    def _slots(self, target):
        slots = [s for s in target.moves.slots if s.pp < s.max_pp]
        if self.data["type"] == "pp" and slots:
            slots = [min(slots, key=lambda s: s.pp)]
        return slots

    def blocked_reason(self, engine, side, target):
        if target.fainted:
            return f"**{target.name}** has fainted."
        return None if self._slots(target) else NO_EFFECT

    async def use(self, engine, ctx, side, action):
        target = action.target or action.user
        if (why := self.blocked_reason(engine, side, target)):
            return self._refuse(engine, why)
        for s in self._slots(target):
            s.pp = min(s.max_pp, s.pp + self.data["pp"])
        engine.log.append(f"Used a **{self.name}** on **{target.name}**!")
        return True


class StatItem(BagItem):
    """X Attack & co. -- always the ACTIVE Pokemon, through the normal
    stat-change path so Contrary/Simple/etc. apply."""
    category = "battle"

    def blocked_reason(self, engine, side, target):
        return NO_EFFECT if target.stages[self.data["stat"]] >= 6 else None

    async def use(self, engine, ctx, side, action):
        target = action.user
        if (why := self.blocked_reason(engine, side, target)):
            return self._refuse(engine, why)
        from .stats import apply_stat_change
        mctx = MoveContext(battle=ctx, move=None, user=target)
        engine.log.append(f"Used an **{self.name}** on **{target.name}**!")
        await apply_stat_change(mctx, target, self.data["stat"], self.data["stages"])
        engine.log.extend(mctx.result.messages)
        return True


# ---------------------------------------------------------------------------
# Poke Balls
# ---------------------------------------------------------------------------

class PokeBall(BagItem):
    category = "ball"

    @staticmethod
    async def _animate_shakes(engine: "BattleEngine", shakes: int, critical: bool) -> None:
        """Monolith `_animate_ball_shakes`, step for step. A critical capture
        gets NO shake line, just a 1.2 s beat of tension (and, like the
        monolith, no frame pushed). Otherwise one line is added up front
        (even for 0 shakes) and rewritten per shake -- "shook once...", "shook
        once... twice...", ... -- pushing a frame and pausing 0.5 s each time.
        The caught/broke-free line is added only AFTER this returns."""
        hook = engine.on_throw
        if critical:
            if hook:
                await hook(1.2, False)
            return
        words = ("once", "twice", "three times")
        idx = len(engine.log)
        engine.log.append("The Pok\u00e9 Ball shook...")
        for i in range(min(max(shakes, 0), 3)):
            engine.log[idx] = "The Pok\u00e9 Ball shook " + "... ".join(words[:i + 1]) + "..."
            if hook:
                await hook(0.5, True)

    async def use(self, engine, ctx, side, action):
        from pokemon.commands import catching

        foe_side = next(s for s in engine.sides if s is not side)
        foes = [b for b in foe_side.active if b is not None and not b.fainted]
        if len(foes) != 1:
            # Monolith: "It's impossible to aim when there are N Pokemon!" --
            # nothing thrown, nothing consumed.
            return self._refuse(engine, f"It's impossible to aim when there are {len(foes)} Pok\u00e9mon!")

        engine.log.append(f"Used **{self.name}**...")
        enc = foe_side.wild_encounter
        if enc is None:
            # A trainer's Pokemon. Faithful to the monolith: the ball is
            # wasted (except a Master Ball, which it never consumed here).
            engine.log.append(f"**{foes[0].name}**'s trainer knocked the ball away! "
                              "You can't catch another trainer's Pok\u00e9mon.")
            return self.name != "Master Ball"

        wild = foes[0]
        mine = action.user
        res = catching.exact_catch_result(
            enc.species, self.name, self.data,
            hp_pct=wild.vitals.hp / wild.vitals.max_hp, status=wild.vitals.status,
            wild_gender=wild.identity.gender, wild_level=wild.identity.level,
            turn=engine.turn_number, is_night=not (6 <= datetime.now().hour < 20),
            dex_seen=side.dex_seen, my_species_id=mine.identity.species_id,
            my_level=mine.identity.level, my_gender=mine.identity.gender,
            my_ref_level=max((b.identity.level for b in side.roster), default=mine.identity.level))

        critical = False
        if res["guaranteed"]:
            caught, shakes = True, 1
        else:
            odds = res["crit_odds_256"]
            critical = odds > 0 and random.randint(0, 255) < odds
            caught, shakes = (catching.catch_check_critical if critical else catching.catch_check)(res["a"])

        await self._animate_shakes(engine, shakes, critical)
        if caught:
            engine.log.append(("\u2728 Critical capture! " if critical else "") +
                              f"Gotcha! **{wild.name}** was caught!")
            engine.catch = CatchOutcome(self.name, wild, critical, shakes)
            engine.conclude(side, "caught")
        elif critical:
            engine.log.append("So close! It broke free from the critical capture!")
        else:
            engine.log.append(("Oh no! The Pok\u00e9mon broke free!", "Aww! It appeared to be caught!",
                               "Aargh! Almost had it!", "Gah! It was so close, too!")[min(shakes, 3)])
        return True


# ---------------------------------------------------------------------------
# Lookup / dispatch
# ---------------------------------------------------------------------------

_BY_TYPE = {
    "heal": HealItem, "full": HealItem, "revive": ReviveItem, "max_revive": ReviveItem,
    "cure": CureItem, "pp": PPItem, "pp_all": PPItem, "stat": StatItem,
}


@lru_cache(maxsize=None)
def resolve_bag_item(name: str) -> BagItem | None:
    """The BagItem for `name`, or None if it isn't usable in battle (held
    items, evolution stones, a Mega Ring, ...)."""
    from pokemon.dex.items_repo import BATTLE_ITEMS, POKEBALL_DATA
    if name in POKEBALL_DATA:
        return PokeBall(name, POKEBALL_DATA[name])
    data = BATTLE_ITEMS.get(name)
    cls = _BY_TYPE.get(data["type"]) if data else None
    return cls(name, data) if cls else None


def usable_bag_names() -> set[str]:
    from pokemon.dex.items_repo import BATTLE_ITEMS, POKEBALL_DATA
    return {n for n in (*POKEBALL_DATA, *BATTLE_ITEMS) if resolve_bag_item(n) is not None}


async def use_bag_item(engine: "BattleEngine", ctx: "BattleContext", action: "ItemAction") -> None:
    """Item phase of a turn (before any move). Validates the quantity,
    delegates to the item, and books the consumption."""
    side = engine.field.side_of(action.user)
    item = resolve_bag_item(action.item_name)
    if item is None:
        engine.log.append(f"**{action.item_name}** can't be used in battle.")
        return
    tid = action.trainer_id
    bag = side.bags.get(tid) if tid is not None else None
    if tid is not None and (bag is None or bag.get(item.name, 0) <= 0):
        engine.log.append(f"No {item.name}s left!")
        return
    if await item.use(engine, ctx, side, action) and bag is not None:
        bag[item.name] -= 1
        side.bag_used[(tid, item.name)] = side.bag_used.get((tid, item.name), 0) + 1

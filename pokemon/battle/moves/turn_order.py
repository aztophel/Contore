"""Moves that change WHO ACTS WHEN (or WHERE) inside a Doubles turn -- After You, Quash, Round, Ally Switch. They edit the engine's remaining action queue (`BattleEngine._queue`, see
`prioritize_action` / `delay_action`) or the `side.active` list itself, the way Showdown's `queue.prioritizeAction` / `action.order = 201` / `swapPosition` do.
Verified against Showdown by tests/t_turn_order.py (Gens 4-9)."""
from __future__ import annotations
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..move import Move, BasicMove
from ..registry import register_move
from ..volatile import Volatile

if TYPE_CHECKING:
    from ..battler import Battler
    from ..context import MoveContext


def _fail(ctx: "MoveContext") -> None:
    ctx.log("But it failed!")
    ctx.result.failed = True


def _singles(ctx: "MoveContext") -> bool:
    """Showdown `activePerHalf === 1`: After You / Quash fail in Singles."""
    return len(ctx.battle.field.side_of(ctx.user).active) < 2


@register_move("after-you")
class AfterYouMove(Move):
    """The target acts right after the user this turn -- ahead of every other Pokemon that has not moved, whatever its priority or speed (Showdown `prioritizeAction`: order 3 at the FRONT of the
    queue). Fails in Singles and when the target has no move left to make (already moved, fainted, switched)."""
    async def on_hit(self, ctx: "MoveContext", target: "Battler") -> None:
        entry = None if _singles(ctx) else ctx.engine.queued_move(target)
        if entry is None:
            _fail(ctx)
            return
        ctx.engine.prioritize_action(entry)
        ctx.log(f"**{target.name}** took the kind offer!")


@register_move("quash")
class QuashMove(Move):
    """The target moves LAST this turn (Showdown `action.order = 201`). Showdown only re-sorts its queue from Gen 8, so before that the order is left as it was."""
    async def on_hit(self, ctx: "MoveContext", target: "Battler") -> None:
        entry = None if _singles(ctx) else ctx.engine.queued_move(target)
        if entry is None:
            _fail(ctx)
            return
        ctx.engine.delay_action(entry)
        ctx.log(f"**{target.name}'s** move was postponed!")


@register_move("round")
class RoundMove(BasicMove):
    """Showdown `onTry`: the FIRST other Round still waiting in the queue is pulled up to act right after this one (order 3, `sourceEffect: round`), and THAT Round has double power."""
    async def on_try_use(self, ctx: "MoveContext") -> str | None:
        queue = ctx.engine._queue
        if queue:
            for entry in queue:
                user, action = entry
                if getattr(action, "move_slug", None) == "round" and not user.fainted and user.side is not None:
                    ctx.engine.prioritize_action(entry, source="round")
                    break
        return await super().on_try_use(ctx)

    async def on_base_power(self, ctx: "MoveContext", target: "Battler") -> int | None:
        return self.spec.base_power * 2 if ctx.source_effect == "round" else None


@dataclass
class AllySwitchStall(Volatile):
    """Showdown's `allyswitch` condition (Gen 9 only; the Gen 8 mod removes it and the older ones inherit that): lasts 2 turns, so a use on the very next turn RESTARTS it -- succeeding with chance
    1 / counter (counter 3, 9, 27 ... capped at 729); a failed roll ends it and the move fails."""
    slug: str = "ally-switch"
    counter: int = 3
    turns_left: int = 2

    async def on_end_turn(self, ctx, owner: "Battler") -> None:
        self.turns_left -= 1
        if self.turns_left <= 0:
            owner.remove(AllySwitchStall, ctx.engine.events)


@register_move("ally-switch")
class AllySwitchMove(Move):
    """The user trades POSITIONS with its ally (priority +2, +1 before Gen 7 -- `data/move_gen_overrides.json`). Moves aimed at either position at the start of the turn hit whoever stands there by then
    (`engine._slot_occupant`)."""
    async def on_try_use(self, ctx: "MoveContext") -> str | None:
        if ctx.ruleset.number < 9:
            return None
        v = ctx.user.get(AllySwitchStall)
        if v is None:
            ctx.user.add(AllySwitchStall(), ctx.engine.events)
            return None
        if random.random() >= 1.0 / (v.counter or 1):            # Showdown `onRestart`: `randomChance(1, counter)`
            ctx.user.remove(AllySwitchStall, ctx.engine.events)
            return "But it failed!"
        if v.counter < 729:
            v.counter *= 3
        v.turns_left = 2
        return None

    async def get_targets(self, ctx: "MoveContext") -> list:
        return [ctx.user]

    async def on_hit(self, ctx: "MoveContext", target: "Battler") -> None:
        user = ctx.user
        side = ctx.battle.field.side_of(user)
        if len(side.active) < 2 or user not in side.active:
            _fail(ctx)
            return
        here = side.active.index(user)
        there = 0 if here == len(side.active) - 1 else len(side.active) - 1
        ally = side.active[there]
        if ally is None or ally.fainted or ally is user:
            _fail(ctx)
            return
        side.active[here], side.active[there] = ally, user
        # a move that was aimed (this turn) at the position the user left now hits the ally standing there, and vice versa -- see `BattleEngine._slot_occupant`
        ctx.engine._left_slot.setdefault(id(user), (side, here))
        ctx.engine._left_slot.setdefault(id(ally), (side, there))
        ctx.log(f"**{user.name}** and **{ally.name}** switched places!")

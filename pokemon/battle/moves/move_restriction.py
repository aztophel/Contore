"""Taunt, Disable, Encore -- MIGRATION_STATUS.md gap #6/7 ("move-legality
family"). The actual restriction logic (duration, what gets blocked) lives
in the matching `Volatile` subclasses in `volatile.py`; this file is just
each move's `on_hit`: the failure conditions and attaching the volatile.
"""
from __future__ import annotations
import random

from ..move import Move
from ..context import MoveContext
from ..registry import register_move
from ..volatile import Disable, Encore, Taunt


@register_move("taunt")
class TauntMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..abilities._util import ally_blocks
        if await ally_blocks(ctx, target, "volatile:taunt"):                  # Aroma Veil
            return
        if target.has(Taunt):
            ctx.log("But it failed!")
            return
        # Showdown's per-generation duration: Gen 3 = 2 turns, Gen 4 = `random(3, 6)` (3-5), Gen 5+ = 3
        gen = ctx.ruleset.number
        turns = 2 if gen <= 3 else random.randint(3, 5) if gen == 4 else 3
        if gen >= 5 and getattr(target, "acted_this_turn", False):      # Showdown `onStart`: `!this.queue.willMove(target)` -> one turn longer when the target has ALREADY moved this turn (not in Gens 3-4)
            turns += 1
        target.add(Taunt(turns_remaining=turns), ctx.events)
        ctx.log(f"**{target.name}** fell for the taunt!")


@register_move("disable")
class DisableMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..abilities._util import ally_blocks
        if await ally_blocks(ctx, target, "volatile:disable"):
            return
        last_slug = getattr(target, "last_move_slug", None)
        if not last_slug or last_slug == "struggle":
            ctx.log("But it failed!")
            return
        slot = next((s for s in target.moves.slots if s.move_slug == last_slug), None)
        if slot is None or slot.pp <= 0 or target.has(Disable):
            ctx.log("But it failed!")
            return
        # Showdown: Gen 5+ `duration: 5`, minus 1 if the target is yet to move (or is the Pokemon moving); Gen 4 `random(4, 8)` / Gen 3 `random(2, 6)`, plus 1 if it already moved.
        # (This engine ticks a volatile at the end of the turn it was applied on, like Showdown, so the numbers carry over as they are.)
        gen, acted = ctx.ruleset.number, getattr(target, "acted_this_turn", False)
        turns = (5 if acted else 4) if gen >= 5 else (random.randint(4, 7) if gen == 4 else random.randint(2, 5)) + (1 if acted else 0)
        target.add(Disable(move_slug=last_slug, turns_remaining=turns), ctx.events)
        ctx.log(f"**{target.name}**'s **{ctx.engine.moves.spec(last_slug).display_name}** was disabled!")


@register_move("encore")
class EncoreMove(Move):
    # Ported verbatim from Showdown's `failencore`-flagged move list
    # (data/moves.ts), restricted to the ones registered in this bot's
    # movedex -- Encore fails against any of these as the target's last
    # move, not just "no last move"/"out of PP".
    _FAIL_ENCORE = frozenset({
        "assist", "copycat", "encore", "me-first", "metronome", "mimic",
        "mirror-move", "nature-power", "sketch", "sleep-talk", "transform",
    })

    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..abilities._util import ally_blocks
        if await ally_blocks(ctx, target, "volatile:encore"):
            return
        last_slug = getattr(target, "last_move_slug", None)
        if not last_slug or last_slug in self._FAIL_ENCORE:
            ctx.log("But it failed!")
            return
        slot = next((s for s in target.moves.slots if s.move_slug == last_slug), None)
        if slot is None or slot.pp <= 0 or target.has(Encore):
            ctx.log("But it failed!")
            return
        gen = ctx.ruleset.number                           # Gen 5+: 3; Gen 4 `random(4, 9)`; Gen 3 `random(3, 7)`; +1 if the target has already moved this turn
        turns = 3 if gen >= 5 else (random.randint(4, 8) if gen == 4 else random.randint(3, 6))
        if gen >= 5 and getattr(target, "acted_this_turn", False):
            turns += 1
        target.add(Encore(move_slug=last_slug, turns_remaining=turns), ctx.events)
        ctx.log(f"**{target.name}** got an encore!")

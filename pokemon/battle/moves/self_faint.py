"""Explosion, Self-Destruct, Memento, Healing Wish, Lunar Dance -- the moves
whose USER faints, ported from Pokemon Showdown (data/moves.ts's
`selfdestruct` field + sim/battle-actions.ts) rather than from the monolith.

Before this file all five were plain `BasicMove`s: the user never fainted at
all (the monolith had this logic inline in `apply_move`; the migration kept
only the generic damage path, and the one place that DID model it -- Damp --
did so by zeroing the user's HP as a side effect).

Two reusable bases, implemented purely through the generic `Move` lifecycle
hooks (`on_move_start` / `on_after_move`, see move.py) -- the engine knows
nothing about self-destruction:

  AlwaysSelfDestructMove   Showdown `selfdestruct: "always"`. The user faints
                           the moment the move is USED, before damage, whether
                           or not it then misses, is Protected against, or
                           hits an immune target. (Gen 4: only once a valid
                           target exists.)
  SelfDestructMove         Showdown `selfdestruct: "ifHit"`. The user faints
                           only if the move landed and didn't fail.

HP is zeroed up front for the "always" kind (not deferred) so faint ORDER
against its targets is right for a mutual KO -- see `BattleEngine.winner`
and `Vitals.faint_seq`. `ctx.user_self_ko` tells the damage loop the
attacker being down is intentional, so the hit still lands.

Damp is checked in `AlwaysSelfDestructMove.on_try_use` (`damp_blocker`)
rather than in the Damp ability class, see that class's docstring.
"""
from __future__ import annotations

from ..move import BasicMove, Move
from ..context import MoveContext
from ..registry import register_move
from ..stats import apply_stat_change
from ..abilities._util import attacker_breaks_mold
from ..abilities.trapping_and_field import SELF_DESTRUCT_MOVES


def _faint_user(ctx: MoveContext) -> None:
    """Take the move's user down and log it (after the move's own lines,
    since hooks append to the same message list)."""
    if ctx.user.fainted:
        return
    ctx.user.vitals.hp = 0
    ctx.log(f"**{ctx.user.name}** fainted!")


def damp_blocker(ctx: MoveContext):
    """The active Damp holder (if any) that stops this move from being used.
    Showdown's Damp is breakable, so a Mold Breaker-family user ignores it."""
    if ctx.move.slug not in SELF_DESTRUCT_MOVES or attacker_breaks_mold(ctx):
        return None
    for b in ctx.battle.field.all_battlers():
        if not b.fainted and b.effective_ability() == "damp":
            return b
    return None


class SelfDestructMove(Move):
    """User faints only if the move connected and didn't fail. Subclasses
    implement `on_hit` as usual (extend `Move`, or `BasicMove` for a damaging
    one)."""

    async def on_after_move(self, ctx: MoveContext, connected: bool) -> None:
        if connected and not ctx.result.failed:
            _faint_user(ctx)


class AlwaysSelfDestructMove(BasicMove):
    """User faints as soon as the move is used, hit or miss. A damaging move
    (BasicMove) by nature -- Explosion, Self-Destruct. Fails outright, with
    NO faint, if a Damp holder is on the field."""

    async def on_try_use(self, ctx: MoveContext) -> str | None:
        holder = damp_blocker(ctx)
        if holder is not None:
            return (f"**{holder.name}**'s Damp prevents **{ctx.user.name}** "
                    f"from using **{self.spec.display_name}**!")
        return None

    async def on_move_start(self, ctx: MoveContext) -> None:
        # Gen 5+ faints even with no valid target; Gen 4 only once one
        # exists (sim/battle-actions.ts).
        if ctx.targets or ctx.ruleset.number != 4:
            ctx.user.vitals.hp = 0
            ctx.user_self_ko = True

    async def on_after_move(self, ctx: MoveContext, connected: bool) -> None:
        if ctx.user_self_ko:
            ctx.log(f"**{ctx.user.name}** fainted!")


@register_move("explosion")
class ExplosionMove(AlwaysSelfDestructMove):
    pass


@register_move("self-destruct")
class SelfDestructAttack(AlwaysSelfDestructMove):
    pass


@register_move("memento")
class MementoMove(SelfDestructMove):
    """Sharply lowers the target's Attack and Sp. Atk, then the user faints
    -- but only if the drop actually happened: at -6/-6 already Showdown's
    boost returns false, so the move fails and the user survives. (A plain
    `Move`, not BasicMove: it deals no damage, and the spec's generic
    stat_changes path would apply the drops to the wrong side.)"""

    async def on_hit(self, ctx: MoveContext, target) -> None:
        before = dict(target.stages)
        await apply_stat_change(ctx, target, "attack", -2)
        await apply_stat_change(ctx, target, "special-attack", -2)
        if dict(target.stages) == before:
            ctx.result.failed = True


class _WishMove(SelfDestructMove):
    """Healing Wish / Lunar Dance. The user faints and the NEXT Pokemon to
    enter its slot is fully healed (+ Lunar Dance restores PP). Fails if
    there's nobody left to switch in (Showdown's `canSwitch` check in
    onTryHit), or if that slot already carries the same pending wish
    (`addSlotCondition` returns false -> the move did nothing -> no faint).
    The healing itself is delivered by BattleEngine._do_switch_in."""
    condition: str = ""

    async def on_try_use(self, ctx: MoveContext) -> str | None:
        side = ctx.user.side
        if not side.alive_bench():
            return "But it failed!"
        if side.slot_conditions.get(side.active.index(ctx.user)) == self.condition:
            return "But it failed!"
        return None

    async def on_hit(self, ctx: MoveContext, target) -> None:
        side = ctx.user.side
        side.slot_conditions[side.active.index(ctx.user)] = self.condition


@register_move("healing-wish")
class HealingWishMove(_WishMove):
    condition = "healing-wish"


@register_move("lunar-dance")
class LunarDanceMove(_WishMove):
    condition = "lunar-dance"

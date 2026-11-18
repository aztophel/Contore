"""Worked examples of Move subclasses, chosen to span the range of
behaviour the hook system needs to cover: attaching a Volatile to yourself,
attaching a SideCondition to the field, variable base power, and a status
move sharing the same immunity-resolution hook abilities use. These are
reference implementations for the migration, not a claim that the whole
movedex is ported yet -- see ARCHITECTURE.md's roadmap.

Each class is registered with its slug via @register_move, so
MoveRegistry.load() picks it up automatically instead of falling back to
BasicMove for that slug -- no other wiring needed.
"""
from __future__ import annotations

from ..move import Move, BasicMove
from ..context import MoveContext
from ..volatile import Substitute, LeechSeed
from ..registry import register_move


@register_move("substitute")
class SubstituteMove(Move):
    """Fails if the user is already behind a substitute or doesn't have
    enough HP; otherwise spends 1/4 max HP and attaches a Substitute
    Volatile, which intercepts incoming hits on its own via
    Substitute.on_try_hit -- this move never needs to know how substitutes
    actually block damage."""

    async def on_try_use(self, ctx: MoveContext) -> str | None:
        user = ctx.user
        if user.has(Substitute):
            return "But it failed!"
        cost = user.vitals.max_hp // 4
        if user.vitals.hp <= cost:
            return "But it failed!"
        return None

    async def on_hit(self, ctx: MoveContext, target) -> None:
        # target is ctx.user here (spec.target == MoveTarget.USER)
        cost = target.vitals.max_hp // 4
        target.vitals.hp -= cost
        target.add(Substitute(hp=cost), ctx.battle.engine.events)
        ctx.log(f"**{target.name}** put in a substitute!")


@register_move("leech-seed")
class LeechSeedMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        if target.has(LeechSeed):
            ctx.log("But it failed!")
            return
        # Grass-type immunity here is a bespoke, Leech-Seed-specific rule
        # (Pokemon Showdown's data/moves.ts: leechseed has its own
        # `onTryImmunity(target) { return !target.hasType('Grass'); }`) --
        # NOT the general move-type-vs-target-type chart (Grass resisting
        # a hypothetical Grass-type attack is merely 1x, not an immunity)
        # and NOT an ability. An earlier draft only checked the
        # ability-hook axis (on_type_immunity, correctly catching Sap
        # Sipper) and missed this -- a plain Grass-type target with no
        # relevant ability wasn't actually immune.
        if "Grass" in target.effective_types():
            ctx.log(f"It doesn't affect **{target.name}**!")
            return
        # Ability-granted immunity (Sap Sipper triggers its own Attack
        # boost instead) uses the same on_type_immunity hook a damaging
        # Grass-type move would.
        immune = await target.consult_first("on_type_immunity", ctx, self.spec.type_name)
        if immune:
            ctx.log(f"It doesn't affect **{target.name}**!")
            return
        target.add(LeechSeed(source_id=ctx.user.id), ctx.battle.engine.events)
        ctx.log(f"**{target.name}** was seeded!")


@register_move("stealth-rock")
class StealthRockMove(Move):
    """Field-effect move: attaches a SideCondition to the *opponent's* side
    rather than to any individual Battler, showing the same Effect/hook
    pattern applies uniformly to Field/Side entities, not just Battlers."""

    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..side_conditions import StealthRock as StealthRockCondition  # Phase 3
        opp_side = ctx.target_side(target)
        if opp_side.has(StealthRockCondition):
            ctx.log("But it failed!")
            return
        opp_side.add(StealthRockCondition(), ctx.battle.engine.events)
        ctx.log("Pointed stones float in the air around the opposing team!")


@register_move("gyro-ball")
class GyroBallMove(BasicMove):
    """Variable-power move: overriding on_base_power is the entire
    implementation. No engine special-casing by move name required, unlike
    the old apply_move's dedicated `_gyro_ball_power()` + an `if move_name
    == "gyro-ball"` branch."""

    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        # stat_async, not stat(): Gyro Ball's whole point is comparing
        # REAL relative Speed (paralysis, Tailwind, abilities, items all
        # matter here), not the hook-free display value stat() gives.
        user_speed = await ctx.user.stat_async("spe", ctx)
        target_speed = await target.stat_async("spe", ctx)
        if user_speed <= 0:
            return 1
        return min(150, 25 * target_speed // user_speed + 1)          # Showdown: floor(25 * target / user) + 1, capped at 150


@register_move("thunder-wave")
class ThunderWaveMove(Move):
    """Status move with TWO independent type-based immunity checks, per
    Pokemon Showdown's data/moves.ts (thunderwave is one of the few status
    moves with `ignoreImmunity: false` -- most status moves skip the
    plain type-chart check entirely, see status.py's docstring on the two
    separate immunity axes):
      1. Ground's ordinary type-chart immunity to Electric-type moves
         (type_effectiveness == 0) -- Ground types have always resisted
         Thunder Wave outright, same as they'd resist a hypothetical
         Electric-type damaging move.
      2. Ability-granted immunity (Volt Absorb/Lightning Rod/Motor Drive/
         Sap Sipper triggering their own effect instead, or Levitate-style
         blocks), via the same on_type_immunity hook a damaging move uses.
    Electric-type's own immunity to paralysis (independent of what move or
    effect tried to cause it) is a THIRD, separate check inflict_status
    itself performs -- see status.py's _TYPE_STATUS_IMMUNITY."""

    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..types import type_effectiveness
        if type_effectiveness(self.spec.type_name, target.effective_types(), ruleset=ctx.ruleset) == 0.0:
            ctx.log(f"It doesn't affect **{target.name}**!")
            return
        immune = await target.consult_first("on_type_immunity", ctx, self.spec.type_name)
        if immune:
            ctx.log(f"It doesn't affect **{target.name}**!")
            return
        from ..status import inflict_status
        await inflict_status(ctx, target, "paralysis")

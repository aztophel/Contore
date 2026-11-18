"""Moves whose power depends on some piece of battle state (friendship,
the user's/target's own status, timing of who moved/fainted/got hit this
turn) rather than an HP/Speed/weight ratio (see variable_power.py for
that family). Old code: apply_move_full.txt's per-move branches.
"""
from __future__ import annotations

from ..move import Move, BasicMove, MoveCategory
from ..context import MoveContext
from ..registry import register_move


@register_move("return")
class ReturnMove(BasicMove):
    """Old code: apply_move's Return branch. Pokemon Showdown:
    `floor(happiness * 10 / 25) || 1` -- max friendship (255) gives 102
    power, 0 friendship gives 1."""
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        return max(1, (ctx.user.identity.friendship * 10) // 25)


@register_move("frustration")
class FrustrationMove(BasicMove):
    """Return's inverse: LOW friendship means high power."""
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        return max(1, ((255 - ctx.user.identity.friendship) * 10) // 25)


@register_move("facade")
class FacadeMove(BasicMove):
    """Doubles power if the USER has a major status OTHER than sleep
    (Pokemon Showdown: `pokemon.status && pokemon.status !== 'slp'`) --
    and, per this bot's Gen 6 baseline, ignores burn's own Attack-halving
    while it's active (a Gen 6+ addition -- see Bulbapedia's burn
    article: "Generation VI: If a burned Pokemon uses Facade, burn's
    damage reduction is now ignored"). The halving-bypass isn't
    implemented yet -- see MIGRATION_STATUS.md -- since stat_async's burn
    check (battler.py) has no per-MOVE hook point to override through,
    only the fixed on_negates_burn_penalty ABILITY hook Guts uses; giving
    a specific move that same override needs a small, deliberate
    extension there rather than a workaround here."""
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        user = ctx.user
        if user.vitals.status and user.vitals.status != "sleep":
            return int((self.spec.base_power or 70) * 2)
        return None


@register_move("hex")
class HexMove(BasicMove):
    """Doubles power if the TARGET has a major status (or Comatose, which
    behaves like a permanent, unremovable status for this exact purpose
    -- Pokemon Showdown checks `target.status || target.hasAbility(
    'comatose')` explicitly)."""
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        if target.vitals.status or target.identity.ability_slug == "comatose":
            return int((self.spec.base_power or 65) * 2)
        return None


@register_move("venoshock")
class VenoshockMove(BasicMove):
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        if target.vitals.status in ("poison", "bad_poison"):
            return int((self.spec.base_power or 65) * 2)
        return None


@register_move("payback")
class PaybackMove(BasicMove):
    """Doubles power if the target has ALREADY taken its move-action this
    turn (Pokemon Showdown: `target.newlySwitched || willMove(target)` ->
    NOT boosted; the inverse of that is exactly "target already acted",
    which a freshly-switched-in Pokemon can never be, so one flag
    (`acted_this_turn`, set by engine.py's _execute_move) covers both of
    Showdown's conditions at once)."""
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        if getattr(target, "acted_this_turn", False):
            return int((self.spec.base_power or 50) * 2)
        return None


@register_move("assurance")
class AssuranceMove(BasicMove):
    """Doubles power if the target was already damaged EARLIER this same
    turn (by anything -- an ally's move, a hazard, residual damage from a
    previous hit of the same multi-hit move, ...). Uses the same
    last_damage_turn tracking damage.py already sets after every hit
    (see damage.py's _resolve_one_hit)."""
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        if getattr(target, "last_damage_turn", None) == ctx.engine.turn_number:
            return int((self.spec.base_power or 60) * 2)
        return None


@register_move("retaliate")
class RetaliateMove(BasicMove):
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        if ctx.user_side.fainted_last_turn:
            return int((self.spec.base_power or 70) * 2)
        return None


@register_move("knock-off")
class KnockOffMove(Move):
    """1.5x power if the target is currently holding a removable item
    (Sticky Hold blocks removal -- checked the same way every other
    theft-capable effect in this codebase does, per Sticky Hold's own
    docstring in status_immunity.py: a direct ability-slug check, since
    that ability has no hook of its own), and actually removes the item
    after a successful, damaging hit. This isn't a BasicMove subclass
    because it needs a step AFTER calculate_and_apply_damage that
    BasicMove's frozen on_hit has no room for -- so it calls that
    pipeline function directly itself instead."""
    def _removable(self, target) -> bool:
        return bool(target.identity.held_item) and target.identity.ability_slug != "sticky-hold"

    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        if self._removable(target) and ctx.ruleset.number >= 6:        # the x1.5 is Gen 6+ (Gens 3-5: plain 20 / 65)
            return int((self.spec.base_power or 65) * 1.5)
        return None

    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..damage import calculate_and_apply_damage
        had_item = self._removable(target)
        item_name = target.identity.held_item
        hp_before = target.vitals.hp
        await calculate_and_apply_damage(ctx, self, target)
        # HP actually going down is a reliable "did this hit really
        # connect" check that a plain accuracy/miss check can't give here
        # (on_hit only ever runs after accuracy already passed -- see
        # engine.py's _execute_move -- so a "missed" check would always
        # read False regardless; what still needs ruling out is type
        # immunity or a Substitute absorbing the hit, neither of which
        # touch target.vitals.hp at all).
        if had_item and target.vitals.hp < hp_before:
            from ..abilities._util import lose_held_item
            lose_held_item(target, ctx, taken=True)
            ctx.log(f"**{target.name}** lost its {item_name}!")

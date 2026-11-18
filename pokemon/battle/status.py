"""Major status (burn/freeze/paralysis/poison/sleep) infliction. Owned by
Track C. Confusion is deliberately NOT handled here -- it's a Volatile
(see volatile.py's Confusion), not a Vitals.status value, exactly like the
old monolith's `defender_state.confused` was already a separate flag from
`defender_state.status` even though both were resolved inside the same
"Ailment" block (contore.py:19260-19305). Dedicated moves that inflict
confusion (Supersonic, Confuse Ray, Swagger, ...) add a Confusion
component directly instead of calling inflict_status.

This is the single path every ailment-inflicting effect should go
through: dedicated status moves (Thunder Wave, Toxic, Spore, ...),
BasicMove.on_secondary_effect's chance-based ailment roll, contact-proc
abilities (Static, Flame Body, Poison Point, Effect Spore, Poison Touch),
and status-setting items (Flame Orb, Toxic Orb) all call this instead of
poking `target.vitals.status` directly, so Safeguard/terrain/Leaf
Guard/Uproar/Synchronize only need implementing once. Old code had this
same "one block handles everyone" property already (contore.py:19260),
just as one 45-line stretch of apply_move rather than a named function
other call sites could reuse -- Static/Flame Body/etc. (contact_abilities.py)
and the status orbs (held_items/end_of_turn.py) currently duplicate a
smaller version of the immunity check inline rather than calling this,
which is flagged in MIGRATION_STATUS.md as a follow-up consolidation
rather than done here, since those call sites read directly from
`ABILITY_STATUS_IMMUNITY` (a plain slug->immunity-set dict) instead of the
fuller on_status_immunity hook this function uses, and reconciling that
is a slightly bigger, more deliberate change than this pass's scope.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from .side_conditions import Safeguard

if TYPE_CHECKING:
    from .battler import Battler
    from .context import MoveContext, BattleContext

# Old code: contore.py's STATUS_EMOJI dict, trimmed to just what messages
# here need (the emoji themselves are cosmetic and already handled by
# whatever UI layer renders ctx.result.messages; kept here as plain text).
_STATUS_DISPLAY = {
    "burn": "burned", "freeze": "frozen", "paralysis": "paralyzed",
    "poison": "poisoned", "bad_poison": "badly poisoned", "sleep": "put to sleep",
}

# Statuses Synchronize passes back to whoever inflicted them. Old code:
# contore.py:19297's inline tuple. Sleep and freeze are excluded in real
# games (Synchronize only mirrors burn/paralysis/poison), matching this.
_SYNCHRONIZE_STATUSES = frozenset({"poison", "bad_poison", "burn", "paralysis"})


# -- [ADDITIVE / Track C] type-based status immunity ------------------
# Verified against Pokemon Showdown's data/typechart.ts, which encodes
# these as `damageTaken` entries keyed by the status's own id (par/brn/
# frz/psn) rather than a real type name, checked via `runStatusImmunity`
# -> `getImmunity(statusId, target)` -- structurally the SAME mechanism
# the type chart uses for ordinary type effectiveness, just reusing it
# for a different axis. This is completely independent of whichever move/
# ability/hazard caused the status: Electric-types can't be paralyzed by
# ANYTHING (not just Thunder Wave), the same way they can't be poisoned
# by Toxic Spikes just because Toxic Spikes isn't itself a "move".
_TYPE_STATUS_IMMUNITY: dict[str, frozenset[str]] = {
    # Electric/paralysis is Gen 6+ ONLY -- confirmed against Bulbapedia's
    # paralysis article ("Generation VI: Electric-type Pokemon are now
    # immune to paralysis") and cross-checked with Pokemon Showdown's
    # (current-gen) type chart, which only reflects the *current* rule.
    # Gen 1-5 Electric-types CAN be paralyzed (just not BY an Electric-
    # type move specifically, which is the separate move-type-chart axis
    # ThunderWaveMove already checks) -- handled by gen-gating this one
    # entry below rather than listing it here unconditionally.
    "burn": frozenset({"Fire"}),
    "freeze": frozenset({"Ice"}),
    "poison": frozenset({"Poison", "Steel"}),
    "bad_poison": frozenset({"Poison", "Steel"}),
}
_ELECTRIC_PARALYSIS_IMMUNITY_GEN = 6


async def inflict_status(
    ctx: "MoveContext | BattleContext", target: "Battler", status: str,
    *, from_foe: bool = True,
) -> bool:
    """Try to inflict `status` on `target`. Returns whether it landed.
    `from_foe` defaults to True since the overwhelming majority of callers
    are exactly that (a move, a contact ability, an opposing status move);
    Rest is the one common exception and passes from_foe=False explicitly.

    Showdown, not the monolith, decides the bypass rules: Mold Breaker-family abilities do NOT bypass terrain or
    Safeguard (the monolith's `and not mold_break` on both was an accident). Terrain blocks come from the terrain's own
    rules object (battle/terrain.py); Safeguard is bypassed only by Infiltrator (and doesn't apply to Yawn's sleep,
    which arrives with `from_foe=False`). Mold Breaker still matters for the ABILITY-based immunities below.

    Immediately checks the newly-afflicted Pokemon's held item for a cure
    (`_try_immediate_cure_berry`, below) -- MIGRATION_STATUS.md gap #9,
    closed. This used to cure at end-of-turn only.
    """
    if target.vitals.status is not None:
        return False
    if status == "sleep" and from_foe:
        from .abilities._util import ally_blocks
        if await ally_blocks(ctx, target, "status:sleep"):                          # Sweet Veil (the holder or its ally)
            return False

    if set(target.effective_types()) & _TYPE_STATUS_IMMUNITY.get(status, frozenset()):
        # This is a SEPARATE axis from a move's own type-vs-type immunity
        # (e.g. Ground blocking Thunder Wave because it resists Electric
        # outright) -- this one applies no matter what caused the status
        # (a move, an ability, a hazard), which is why Electric-types are
        # just as immune to a hypothetical status-inflicting held item as
        # they are to Thunder Wave. Logged here since the status-inducing
        # source may not have its own separate type-immunity message the
        # way Thunder Wave/etc. do for the other axis.
        ctx.log(f"It doesn't affect **{target.name}**!")
        return False

    if (status == "paralysis" and "Electric" in target.effective_types()
            and ctx.ruleset.number >= _ELECTRIC_PARALYSIS_IMMUNITY_GEN):
        ctx.log(f"It doesn't affect **{target.name}**!")
        return False

    field = ctx.field
    if status == "sleep":
        # Uproar: neither side can fall asleep while it's active. Old code:
        # contore.py:19288-19290 checks specifically whether the attacker
        # or defender of *this* move is the one currently locked into
        # Uproar (not "anyone on the field"), matched here. Uproar itself
        # (the move that would set this) is long-tail and not ported yet
        # (see MIGRATION_STATUS.md) -- this checks a plain `locked_move`
        # marker rather than importing a Volatile class that doesn't exist
        # yet, matching this codebase's existing convention for
        # not-yet-implemented long-tail mechanics (e.g.
        # contact_abilities.py's Cursed Body/Cute Charm), so whichever
        # session ports Uproar just needs to set that attribute rather
        # than also coming back to edit this check.
        user = getattr(ctx, "user", None)
        if (getattr(user, "locked_move", None) == "uproar"
                or getattr(target, "locked_move", None) == "uproar"):
            ctx.log("But the uproar kept it from falling asleep!")
            return False

    rules = field.terrain_rules
    if rules is not None:
        blocked = rules.blocks_status(target, status, field)
        if blocked:
            ctx.log(blocked)
            return False

    if from_foe:
        side = ctx.target_side(target) if hasattr(ctx, "target_side") else field.side_of(target)
        infiltrates = getattr(ctx, "user", None) is not None and bool(await ctx.events.first("on_bypass_substitute", ctx))
        if side.has(Safeguard) and not infiltrates:
            ctx.log(f"**{target.name}** is protected by Safeguard!")
            return False

    immune = await target.consult_first("on_status_immunity", ctx, status)
    if immune:
        return False  # the ability itself already logged a message

    target.vitals.status = status
    if status == "sleep":
        import random
        # +1 to convert "turns actually blocked from moving" (what
        # sleep_turns_range means -- see its docstring) into the internal
        # decrement-then-check counter engine.py's _can_act uses (which
        # wakes the Pokemon up ON the turn the counter reaches 0, so a
        # counter of N only actually blocks N-1 move attempts). An earlier
        # draft set this to plain random.randint(1, 3) with no +1, which
        # under that decrement-then-check logic actually produced 0-2
        # blocked turns instead of the intended 1-3 -- fixed.
        lo, hi = ctx.ruleset.mechanics.sleep_turns_range
        target.vitals.status_turns = random.randint(lo, hi) + 1
    ctx.log(f"**{target.name}** was {_STATUS_DISPLAY.get(status, status)}!")

    await _try_immediate_cure_berry(ctx, target)
    await target.consult("on_afflicted_with_status", ctx, status)

    # Synchronize is the `Synchronize` ability's own `on_afflicted_with_status` hook (abilities/status_immunity.py), fired by the consult above on the
    # AFFLICTED Pokemon. (An inline copy used to live here that tested the ATTACKER's ability, so a Synchronize user paralysed itself whenever it
    # afflicted someone -- and the hook version then ran as well.)

    return True


# Cheri/Chesto/Pecha/Rawst/Aspear/Lum Berry: MIGRATION_STATUS.md gap #9.
# `held_items/end_of_turn.py`'s berry classes only had an `on_end_turn`
# hook (correct for Leftovers/Sitrus/etc., which really are end-of-turn-
# only) -- meaning a status cured immediately in the real games, in the
# same instant it's inflicted, was instead sitting uncured until end of
# turn here: one extra tick of poison/burn damage before the berry saved
# them, and (more importantly, since these don't just deal damage) a
# Cheri Berry holder paralyzed earlier in the turn couldn't act normally
# that same turn the way it should. Rather than duplicate each berry's
# cure logic here, this just invokes the item's OWN (stateless -- see
# items.py's `ItemRegistry` docstring) `on_end_turn` immediately; calling
# it again for real at actual end-of-turn is a no-op by then, since the
# status is already gone and the berry already consumed.
_STATUS_CURE_BERRIES = frozenset({"Cheri Berry", "Chesto Berry", "Pecha Berry", "Rawst Berry", "Aspear Berry", "Lum Berry"})


async def _try_immediate_cure_berry(ctx: "MoveContext | BattleContext", battler: "Battler") -> None:
    item_name = battler.identity.held_item
    if item_name not in _STATUS_CURE_BERRIES or battler.fainted:
        return
    item = ctx.engine.items.get(item_name)
    await item.on_end_turn(ctx, battler)

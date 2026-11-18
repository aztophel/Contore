"""Volatile: an Effect attached to a Battler by a move, mid-battle, lasting
until something removes it (switch-out, duration expiry, a cure, the battle
ending, ...).

This is the direct replacement for FighterState's `substitute_hp`,
`leech_seeded`/`leech_slot`, `confused`/`confusion_turns`, `taunt_turns`,
`encore_move`/`encore_turns`, `disabled_move`, `perish_count`, `bind_turns`,
`ingrained`, `aqua_ring`, and dozens more -- each becomes its own small
class here instead of a pair of fields on one 150-slot dataclass that every
Pokémon carried around whether it needed them or not.

Same statelessness caveat as Ability does NOT apply here: a Volatile
instance genuinely belongs to exactly one Battler for exactly one battle
(it's created fresh by a move's on_hit and attached with `.add()`), so it's
fine -- expected, even -- for it to carry its own mutable fields like
`turns_remaining` or `hp`.
"""
from __future__ import annotations
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .move import MoveFlag, bypasses_substitute_move
from ..ecs.component import Effect

if TYPE_CHECKING:
    from .battler import Battler
    from .context import BattleContext, MoveContext


class Volatile(Effect):
    """Shares Ability's hook surface (on_switch_in is simply never fired to
    a Volatile, since nothing dispatches it mid-turn)."""
    slug: str = ""

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None: ...
    async def on_try_hit(self, ctx: "MoveContext", owner: "Battler") -> bool | None: ...
    async def on_modify_damage_taken(self, ctx: "MoveContext", owner: "Battler", damage: int) -> int | None: ...
    async def on_before_move(self, ctx: "MoveContext", owner: "Battler") -> bool | None: ...

    def grants_semi_invulnerability(self) -> bool:
        """Showdown `Pokemon.isSemiInvulnerable()` is "has a volatile with `onInvulnerability`"; this is the same
        thing as a polymorphic query (Fly/Bounce/Dig/Dive's `Charging`). Terrains etc. ask
        `Battler.is_semi_invulnerable()` instead of naming the volatile."""
        return False


@dataclass
class Substitute(Volatile):
    hp: int = 0
    slug: str = "substitute"

    async def on_try_primary_hit(self, ctx: "MoveContext", owner: "Battler") -> bool | None:
        """Showdown `onTryPrimaryHit` -- a LATER phase than `TryHit` (Protect, Magic Bounce, the type-absorb abilities all run first), and after accuracy."""
        move = ctx.move
        if ctx.user is owner:
            return None  # Showdown `onTryPrimaryHit`: `target === source` -- the holder's OWN moves (Swords Dance, Recover, Baton Pass...) are never blocked
        if bypasses_substitute_move(move.slug, ctx.ruleset.number):
            return None  # Showdown `flags.bypasssub` (per generation)
        from .move import MoveTarget
        if move.spec.target is MoveTarget.OPPONENTS_FIELD:
            return None  # hazards go on the foe's SIDE (Showdown `foeSide`): a Substitute can't stop them
        if ctx.ruleset.number >= 6 and await ctx.events.first("on_bypass_substitute", ctx):
            return None  # Infiltrator (checked on the mover's own volatile; Gen 6+ only)
        from .move import MoveCategory
        if move.category is MoveCategory.STATUS:
            # A status move just fails outright against a Substitute.
            # Damaging moves are NOT blocked here -- damage.py's own
            # pipeline redirects the hit into `self.hp` instead of
            # owner.vitals.hp and pops the shell at 0, which needs the
            # move's actual power/atk/def to compute, not just a yes/no.
            return True
        return None


@dataclass
class LeechSeed(Volatile):
    source_id: str = ""
    slug: str = "leech-seed"

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        if owner.fainted:
            return
        # Showdown `leechseed.onResidual`: nothing happens if the seeder is gone; otherwise `damage = this.damage(baseMaxhp / 8)` (indirect damage,
        # so Magic Guard stops it) and `this.heal(damage, seeder)` -- the seeder gains what was ACTUALLY taken, and Liquid Ooze turns that heal
        # into damage to the seeder instead.
        source = ctx.field.battler_by_id(self.source_id)
        if source is None or source.fainted:
            return
        drain = max(1, owner.vitals.max_hp // 8)
        drain = await owner.consult_reduce("on_indirect_damage", ctx, drain, "leech-seed")      # Magic Guard
        if drain <= 0:
            return
        dealt = min(drain, owner.vitals.hp)
        owner.vitals.hp -= dealt
        ctx.log(f"**{owner.name}**'s health was sapped by Leech Seed!")
        if ctx.ruleset.number <= 4:                                                                # Gen 4: Big Root first (it scales the Ooze damage too)
            gain = await source.consult_reduce("on_modify_incoming_drain", ctx, dealt)            # Big Root
            gain = await owner.consult_reduce("on_modify_drain", ctx, gain)                      # Liquid Ooze (negative = damage)
        else:
            gain = await owner.consult_reduce("on_modify_drain", ctx, dealt)                       # Liquid Ooze (negative = damage)
            if gain > 0:
                gain = await source.consult_reduce("on_modify_incoming_drain", ctx, gain)        # Big Root
        if gain > 0:
            source.heal(gain)
        elif gain < 0:
            source.vitals.hp = max(0, source.vitals.hp + gain)
            ctx.log(f"**{source.name}** was hurt by Liquid Ooze!")


@dataclass
class Confusion(Volatile):
    turns_remaining: int = 0
    slug: str = "confusion"

    async def on_before_move(self, ctx: "MoveContext", owner: "Battler") -> bool | None:
        """True means 'the user hits itself instead of acting'; the engine
        rolls the generation-dependent chance and, on a hit, applies the
        fixed-formula typeless self-damage and skips the chosen move
        entirely. Chance is 50% Gen 1-6, 33% Gen 7+ (Pokemon Showdown:
        data/mods/gen6/conditions.ts's `randomChance(1, 2)` vs.
        data/conditions.ts's `randomChance(33, 100)`) -- an earlier draft
        hardcoded 1/3 unconditionally, which is only actually correct for
        Gen 7+; see CONTRACT_CHANGES.md."""
        self.turns_remaining -= 1
        if self.turns_remaining <= 0:
            owner.remove(Confusion, ctx.battle.engine.events)
            ctx.log(f"**{owner.name}** snapped out of confusion!")
            return None
        chance = ctx.ruleset.mechanics.confusion_self_hit_chance
        return True if random.random() < chance else None


@dataclass
class Flinch(Volatile):
    """Lasts exactly until the end of the current turn; the engine clears
    every Battler's Flinch component during end-of-turn cleanup regardless
    of whether it was consumed, since it only ever applies to the turn it
    was attached on."""
    slug: str = "flinch"


@dataclass
class FlashFireBoost(Volatile):
    """Attached by the Flash Fire ability (see abilities_examples.py) the
    first time its holder is hit by a Fire-type move. Boosts the holder's
    own Fire-type moves for the rest of the battle; cleared on switch-out
    like most non-permanent volatiles."""
    slug: str = "flash-fire-boost"

    async def on_modify_power(self, ctx: "MoveContext", owner: "Battler", power: int) -> int | None:
        if ctx.user is owner and ctx.move.spec.type_name == "Fire":
            return int(power * 1.5)
        return None


# -- [ADDITIVE / Track C] -------------------------------------------------
@dataclass
class ProtectionActive(Volatile):
    """One-turn marker set by the Protect family (Protect/Detect/Spiky
    Shield/King's Shield/Baneful Bunker). `kind` distinguishes which one,
    since the contact-punish varieties (Spiky Shield's recoil, King's
    Shield's -1 Atk, Baneful Bunker's poison) all block identically but
    react differently once a contact move actually hits the shield.

    The consecutive-use success-chance formula itself (Pokemon Showdown's
    "stall" condition: 1/3 on the 2nd consecutive use, 1/9 on the 3rd,
    ...; old code: contore.py:16986-16990's `1.0 / (3 ** n)`, an exact
    match) lives in moves/protect.py rather than here, since it's about
    whether this volatile gets ADDED at all, not about the volatile's own
    behavior once active."""
    slug: str = "protection-active"
    kind: str = "protect"

    async def on_try_hit(self, ctx: "MoveContext", owner) -> bool | None:
        from .move import MoveFlag, MoveCategory
        move = ctx.move
        if move.has_flag(MoveFlag.PROTECT_BYPASS):
            return None
        # `breaksProtect` (Showdown `hitStepBreakProtect`, BEFORE the TryHit step): Feint, Phantom Force, Shadow Force, Hyperspace Hole / Fury take the protection away and go on to hit
        if ctx.ruleset.number >= _BREAKS_PROTECT.get(move.slug, 99):
            owner.remove(ProtectionActive, ctx.engine.events)
            if ctx.ruleset.number >= 6 and hasattr(owner, "protect_consecutive_uses"):
                owner.protect_consecutive_uses = 0
            ctx.log(f"**{owner.name if hasattr(owner, 'name') else 'The team'}** fell for the {move.spec.display_name}!")
            return None
        # King's Shield / Obstruct / Silk Trap only stop DAMAGING moves (Showdown's `checkMoveBypassesProtect` lets a Status move through them); Mat Block likewise
        if self.kind in ("king_shield", "obstruct", "silk_trap", "mat_block") and move.category is MoveCategory.STATUS:
            return None
        if self.kind == "wide_guard" and ctx.ruleset.number <= 6 and move.category is MoveCategory.STATUS:
            return None                                                     # Gens 5-6 (gen6 mod): Wide Guard stops only DAMAGING spread moves; Gen 7+ stops status ones too
        if self.kind == "wide_guard" and move.spec.target.name not in ("ALL_OTHER_POKEMON", "ALL_OPPONENTS"):
            return None
        if self.kind == "quick_guard" and move.spec.priority <= 0:
            return None
        if self.kind == "crafty_shield" and move.category is not MoveCategory.STATUS:
            return None
        # `owner` is a Battler for the single-target family (Protect,
        # Spiky Shield, ...) but a Side for the side-wide family (Wide
        # Guard, Quick Guard, Crafty Shield -- see moves/protect.py's
        # _side_protect_move), since those attach this same Volatile to
        # the Side rather than an individual Battler. `display_name`
        # covers both without this needing two near-identical classes.
        display_name = owner.name if hasattr(owner, "name") else str(owner.trainer_id)
        ctx.log(f"**{display_name}** protected itself!" if self.kind == "protect"
                 else f"**{display_name}** protected against the attack using {self.kind.replace('_', ' ').title()}!")
        if self.kind in ("spiky_shield", "king_shield", "baneful_bunker") and move.has_flag(MoveFlag.CONTACT):
            user = ctx.user
            if self.kind == "spiky_shield":
                amount = max(1, user.vitals.max_hp // 8)
                amount = await user.consult_reduce("on_indirect_damage", ctx, amount, "contact-punish")
                if amount > 0:
                    user.vitals.hp = max(0, user.vitals.hp - amount)
                    ctx.log(f"**{user.name}** was hurt by Spiky Shield!")
            elif self.kind == "king_shield":
                from .stats import apply_stat_change
                await apply_stat_change(ctx, user, "attack", -2 if ctx.ruleset.number <= 7 else -1)     # Showdown: -2 in Gens 6-7 (gen7 mod), -1 from Gen 8
            elif self.kind == "baneful_bunker":
                from .status import inflict_status
                await inflict_status(ctx, user, "poison")
        return True


@dataclass
class LockedMove(Volatile):
    """The user is FORCED to repeat `move_slug` (Showdown `onLockMove`): Rollout / Ice Ball (`kind="rollout"`), and the Outrage family (`kind="rampage"`). `turns_left` is Showdown's `duration` (decremented at the
    end of each turn; reaching 0 removes the lock), `hit_count` Rollout's consecutive-hit counter. No PP is spent while locked, and `engine._execute_move` overrides whatever was chosen."""
    slug: str = "locked-move"
    move_slug: str = ""
    kind: str = "rollout"
    hit_count: int = 0
    turns_left: int = 1
    true_duration: int = 0                      # rampage: Showdown `trueDuration` = random(2, 4) turns; the fatigue confusion only follows when it has run out

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        if owner.fainted:
            return
        self.turns_left -= 1                    # Showdown `residualEvent`: the duration ticks FIRST; at 0 the volatile ends (`onEnd`) and its `onResidual` is skipped
        if self.turns_left <= 0:
            owner.remove(LockedMove, ctx.engine.events)
            await self.end(ctx, owner)
            return
        if self.kind == "rampage":
            if owner.vitals.status == "sleep":
                owner.remove(LockedMove, ctx.engine.events)             # `onResidual`: asleep -> the lock is dropped WITHOUT the confusion
                return
            self.true_duration -= 1

    async def end(self, ctx, owner: "Battler") -> None:
        """Showdown lockedmove `onEnd`: a spent Outrage / Thrash / Petal Dance confuses its user."""
        if self.kind != "rampage" or self.true_duration > 1 or owner.fainted or owner.has(Confusion):
            return
        if owner.effective_ability() == "own-tempo":
            return
        import random
        owner.add(Confusion(turns_remaining=random.randint(2, 5)), ctx.engine.events)
        ctx.log(f"**{owner.name}** became confused due to fatigue!")


@dataclass
class DestinyBond(Volatile):
    """Showdown `destinybond` (`noCopy`: Baton Pass doesn't carry it): if the holder is KO'd by a MOVE from a non-ally, that attacker faints too. Ends as soon as the holder acts again
    (`engine._can_act`; using Destiny Bond again just fails and removes it), or on switching out."""
    slug: str = "destiny-bond"


# -- [ADDITIVE / Track C] -------------------------------------------------
@dataclass
class Torment(Volatile):
    """Can't use the move it used LAST turn again (Struggle excepted); lasts until the holder switches out (Showdown `torment`: `onDisableMove` disables `lastMove`)."""
    slug: str = "torment"


_BREAKS_PROTECT = {"feint": 4, "shadow-force": 5, "phantom-force": 6, "hyperspace-hole": 6, "hyperspace-fury": 6}      # move -> first generation in which it breaks Protect


@dataclass
class FocusEnergy(Volatile):
    """+2 crit-rate stages for the rest of the battle (or until Baton
    Passed away/switched out, same as any other volatile). Old code:
    `attacker_state.focus_energy` bool, contore.py's crit-rate block
    (apply_move_full.txt:1903-1904 in this session's extraction) --
    modeled as a Volatile here instead since "does this Pokemon currently
    have Focus Energy's boost active" is exactly the kind of per-Pokemon,
    battle-scoped flag this component system exists to replace bare
    attributes with.

    NOTE: this class was found reconstructed from a corrupted state --
    its body had been left dangling as dead code nested inside
    ProtectionActive.on_try_hit above (missing its own class header
    entirely, but still syntactically valid Python as a nested function
    definition, which is exactly why py_compile never caught it). Fixed
    while adding Charging below; if Focus Energy (the move) still isn't
    working, this is the first place to check."""
    slug: str = "focus-energy"

    async def on_modify_crit_stage(self, ctx: "MoveContext", owner: "Battler", stage: int) -> int | None:
        if ctx.user is owner:
            return stage + 2
        return None


# -- [ADDITIVE / Track C] -------------------------------------------------
# Bypass lists for each semi-invulnerable charge move, verified against
# Pokemon Showdown's data/moves.ts onInvulnerability handlers. Moves not
# in this dict (Solar Beam, Razor Wind, Sky Attack, Freeze Shock, Ice
# Burn, Skull Bash, ...) charge for a turn but grant no invulnerability
# at all -- Charging.on_try_hit below returns None unconditionally for
# those, matching Showdown exactly (no onInvulnerability handler means no
# blocking).
_INVULNERABILITY_BYPASS: dict[str, frozenset[str]] = {
    "fly": frozenset({"gust", "twister", "sky-uppercut", "thunder", "hurricane",
                       "smack-down", "thousand-arrows"}),
    "bounce": frozenset({"gust", "twister", "sky-uppercut", "thunder", "hurricane",
                          "smack-down", "thousand-arrows"}),
    "dig": frozenset({"earthquake", "magnitude"}),
    "dive": frozenset({"surf", "whirlpool"}),
    "phantom-force": frozenset(),          # vanishes: NOTHING bypasses it (Showdown `onInvulnerability: false`)
    "shadow-force": frozenset(),
}


# The moves that DO hit a semi-invulnerable target for DOUBLE damage: Showdown's `onSourceModifyDamage` on the
# charge move's own condition (data/moves.ts dig/dive/fly/bounce; no gen mod changes them). Smack Down, Thunder, Sky
# Uppercut, Hurricane and Thousand Arrows hit through Fly/Bounce but at normal damage.
_SEMI_INVULNERABLE_DOUBLED: dict[str, frozenset[str]] = {
    "fly": frozenset({"gust", "twister"}),
    "bounce": frozenset({"gust", "twister"}),
    "dig": frozenset({"earthquake", "magnitude"}),
    "dive": frozenset({"surf", "whirlpool"}),
}


@dataclass
class Charging(Volatile):
    """The charge-turn marker for two-turn moves (Fly/Dig/Dive/Bounce/
    Solar Beam/Razor Wind/Sky Attack/...). One class covers both the
    semi-invulnerable family and the merely-slow family (see
    _INVULNERABILITY_BYPASS above) -- which one `move_slug` is decides
    whether on_try_hit ever actually blocks anything. The move itself
    (moves/charging.py) is what checks for and removes this on the
    release turn -- this class only needs to grant/deny invulnerability
    while it exists; see BattleEngine._execute_move for how the engine
    forces the SAME move to run again next turn regardless of what's
    submitted, and skips a second PP deduction, while this volatile is
    present."""
    slug: str = "charging"
    move_slug: str = ""

    def grants_semi_invulnerability(self) -> bool:
        return self.move_slug in _INVULNERABILITY_BYPASS   # Solar Beam / Razor Wind / Sky Attack are NOT semi-invulnerable

    async def on_try_hit(self, ctx: "MoveContext", owner: "Battler") -> bool | None:
        bypass = _INVULNERABILITY_BYPASS.get(self.move_slug)
        if bypass is None or ctx.move.slug in bypass:
            return None
        return True

    async def on_modify_damage_taken(self, ctx: "MoveContext", owner: "Battler", damage: int) -> int | None:
        if owner is ctx.current_target and ctx.move.slug in _SEMI_INVULNERABLE_DOUBLED.get(self.move_slug, ()):
            return damage * 2
        return None


@dataclass
class Taunt(Volatile):
    """MIGRATION_STATUS.md gap #6/7. Ported from Pokemon Showdown's
    `taunt` move condition (data/moves.ts) -- blocks status moves (Me
    First, uniquely, is exempt in the real games; skipped here since it
    isn't implemented at all yet, so there's nothing to carve the
    exception out for). `turns_remaining=3` is Showdown's base duration;
    NOT ported is the "+1 if the target hasn't moved yet this turn" turn-
    order-dependent extension (`!this.queue.willMove(target)`) -- this
    engine resolves a whole turn's actions in one pass rather than a live
    per-Pokemon queue, so "has the target's action for this turn already
    happened" isn't something `on_hit` can currently ask. A flat 3 undercounts
    by one turn in the specific case of a slower Pokemon Taunting a
    faster one that hasn't acted yet this turn -- a real but narrow gap,
    not a wrong mechanic."""
    turns_remaining: int = 3
    slug: str = "taunt"

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        if owner.fainted:
            return
        self.turns_remaining -= 1
        if self.turns_remaining <= 0:
            owner.remove(Taunt, ctx.engine.events)
            ctx.log(f"**{owner.name}**'s taunt wore off!")



@dataclass
class Disable(Volatile):
    """MIGRATION_STATUS.md gap #6/7. Ported from Showdown's `disable`
    move condition. `move_slug` is fixed at whatever the target's
    `last_move_slug` was the instant Disable connects (engine.py's
    `_execute_move` is what maintains `last_move_slug`) -- the move
    itself (moves/status_ailments.py or wherever it ends up) is
    responsible for the on_try_hit failure case (no last move yet, or
    last move was Struggle) before ever attaching this.
    `turns_remaining=4`: Showdown's `duration: 5` with its own "-1 if
    the target is mid-move-selection right now" adjustment normally
    nets to 4 actual blocked turns; simplified to a flat 4 for the same
    turn-order-visibility reason `Taunt` above simplifies its own
    duration."""
    move_slug: str = ""
    turns_remaining: int = 4
    slug: str = "disable"

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        if owner.fainted:
            return
        slot = next((s for s in owner.moves.slots if s.move_slug == self.move_slug), None)
        if slot is None or slot.pp <= 0:
            owner.remove(Disable, ctx.engine.events)  # Showdown: onResidual also ends it early once the move hits 0 PP
            return
        self.turns_remaining -= 1
        if self.turns_remaining <= 0:
            owner.remove(Disable, ctx.engine.events)
            ctx.log(f"**{owner.name}** is no longer disabled!")



@dataclass
class Encore(Volatile):
    """MIGRATION_STATUS.md gap #6/7. Ported from Showdown's `encore`
    move condition -- forces the target to repeat `move_slug` (its
    `last_move_slug` at the instant Encore connects) instead of letting
    it choose freely. Enforced the same way Taunt/Disable are, through
    `legal_moves()` only ever showing this one move as selectable, NOT
    through Showdown's `onOverrideAction` (which force-overrides
    whatever WAS chosen, even a random one from Metronome/Sleep Talk) --
    there's no such randomizer-move implementation yet for that
    distinction to matter against. `turns_remaining=3`, same "+1 if not
    yet moved" simplification as Taunt."""
    move_slug: str = ""
    turns_remaining: int = 3
    slug: str = "encore"

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        if owner.fainted:
            return
        slot = next((s for s in owner.moves.slots if s.move_slug == self.move_slug), None)
        if slot is None or slot.pp <= 0:
            owner.remove(Encore, ctx.engine.events)  # Showdown: onResidual ends it early once the encored move hits 0 PP
            return
        self.turns_remaining -= 1
        if self.turns_remaining <= 0:
            owner.remove(Encore, ctx.engine.events)
            ctx.log(f"**{owner.name}**'s encore ended!")


@dataclass
class Attract(Volatile):
    """MIGRATION_STATUS.md gap #7. Ported from Showdown's `attract`
    condition. Unlike Taunt/Disable/Encore, this has no duration --
    it lasts until the affected Pokemon switches out (Volatiles are
    already cleared on switch by this engine's existing switch-handling,
    same as Confusion/Substitute/etc.) or something explicitly cures it.
    The source (whoever caused the infatuation) is needed for the
    opposite-gender check to still make sense after `source` itself
    switches out or faints -- Showdown's `onUpdate` removes Attract the
    instant the source is no longer active; that's simplified away here
    (the 50% block still applies even if the source has since left) since
    reaching back into "is my infatuation source still the active
    Pokemon on their side" from a Volatile's own hook would need a field
    reference this engine's Volatile base doesn't currently carry."""
    source_id: object = None
    slug: str = "attract"



@dataclass
class Nightmare(Volatile):
    """MIGRATION_STATUS.md gap #7. Ported from Showdown's `nightmare`
    condition -- 1/4 max HP damage each end-of-turn while the target
    stays asleep. The move's own `on_hit` (moves/status_ailments.py or
    wherever it ends up) is responsible for the on_try_hit failure case
    (target isn't asleep) before ever attaching this; `on_end_turn` here
    additionally self-removes the instant the target wakes up (Showdown
    relies on a separate global "clear sleep-only volatiles on wake"
    pass this engine doesn't have an equivalent of yet -- checking
    directly here gets the same real-game result: Nightmare ends the
    moment its target wakes up)."""
    slug: str = "nightmare"

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        if owner.fainted:
            return
        if owner.vitals.status != "sleep":
            owner.remove(Nightmare, ctx.engine.events)
            return
        dmg = max(1, owner.vitals.max_hp // 4)
        owner.vitals.hp = max(0, owner.vitals.hp - dmg)
        ctx.log(f"**{owner.name}** is locked in a nightmare!")


@dataclass
class PerishSong(Volatile):
    """MIGRATION_STATUS.md gap #7. Ported from Showdown's `perishsong`
    condition -- every active Pokemon hit gets an independent 4-turn
    countdown; whichever one(s) reach 0 faint, switched out or not
    (fainting via this is unavoidable except by actually switching out
    before the count expires -- Volatiles clear on switch, same as
    Attract above, which is exactly the escape valve). The move's
    `on_hit` (called once per active battler automatically, since Perish
    Song's real `all-pokemon` PokeAPI target already makes the engine's
    normal per-target loop in `_execute_move` visit everyone -- no
    special field-move dispatch needed) is only responsible for not
    re-triggering an already-counting-down target."""
    turns_remaining: int = 4
    slug: str = "perish-song"

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        if owner.fainted:
            return
        self.turns_remaining -= 1
        if self.turns_remaining <= 0:
            ctx.log(f"**{owner.name}**'s perish count fell to 0!")
            owner.vitals.hp = 0
        else:
            ctx.log(f"**{owner.name}**'s perish count is at {self.turns_remaining}!")


@dataclass
class MustRecharge(Volatile):
    """MIGRATION_STATUS.md gap #7. Ported from Showdown's `mustrecharge`
    condition -- Hyper Beam and friends force the user to skip its very
    next turn entirely. Self-clearing, like Showdown's own: the check
    lives in `engine.py`'s `_can_act` (which blocks that one move attempt
    and removes this in the same step), NOT in a hook method here --
    `on_before_move` is only ever dispatched for Confusion, whose return
    value means something different ("hit itself" rather than "blocked"),
    so pretending otherwise on these volatiles would be dead code. Same
    goes for Taunt/Disable/Attract's execution-time checks."""
    slug: str = "must-recharge"


@dataclass
class Yawn(Volatile):
    """MIGRATION_STATUS.md gap #7. Ported from Showdown's `yawn` condition
    -- 2-turn delayed sleep. Duration counts down across two end-of-turns
    (the turn it's applied and the turn after), then on the second tick
    puts the target to sleep via the normal `inflict_status` pipeline
    (so all the usual immunity/ability checks -- Insomnia, Vital Spirit,
    already-has-a-status, ... -- still apply at the moment it actually
    tries to fall asleep, exactly like the real games: Yawn can still
    fail to actually cause sleep even after successfully landing, e.g.
    if the target's ability or status changed in the meantime)."""
    turns_remaining: int = 2
    slug: str = "yawn"

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        if owner.fainted:
            return
        self.turns_remaining -= 1
        if self.turns_remaining > 0:
            return
        owner.remove(Yawn, ctx.engine.events)
        from .status import inflict_status
        await inflict_status(ctx, owner, "sleep", from_foe=False)


@dataclass
class Binding(Volatile):
    """MIGRATION_STATUS.md gap #7. Ported from Showdown's shared
    `partiallytrapped` condition -- Wrap, Bind, Fire Spin, Clamp,
    Whirlpool, Sand Tomb, Magma Storm all attach this (a move-agnostic
    volatile, matching Showdown's own design -- one class for all seven
    moves rather than seven near-identical ones). Random 5-7 turns (8
    with the attacker's Binding Band -- ported since it's a one-line
    check; Grip Claw is NOT, since that's an attacker-side item this
    engine's `ItemRegistry`/effective-item plumbing has no reason to
    reach for from inside the DEFENDER's own volatile). `bound_divisor`
    is fixed at attach time (6 or 8), matching Showdown's own
    `effectState.boundDivisor` being set once in `onStart`, not
    recomputed every turn (so removing Binding Band mid-trap doesn't
    retroactively soften the damage).

    Ends early if `source` (whoever trapped this Pokemon) is no longer
    the active Pokemon on their side -- switched out or fainted. Also
    the enforcement for NOT being able to switch away at all while
    trapped (`engine.py`'s `legal_switches`) -- the first trapping
    mechanic this engine actually blocks switches for; Arena
    Trap/Shadow Tag/Mean Look and similar ability/move-based trapping
    aren't implemented yet, but `legal_switches`' check is written
    generically enough (any Volatile the battler carries that defines
    `traps_switching`) that adding those later doesn't need another
    engine.py change.
    """
    source_id: object = None
    bound_divisor: int = 8
    turns_remaining: int = 5
    slug: str = "binding"
    traps_switching: bool = True

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        if owner.fainted:
            return
        source = self._source(ctx)
        if source is None or source.fainted or source not in ctx.field.side_of(source).active:
            owner.remove(Binding, ctx.engine.events)
            return
        self.turns_remaining -= 1
        dmg = max(1, owner.vitals.max_hp // self.bound_divisor)
        owner.vitals.hp = max(0, owner.vitals.hp - dmg)
        ctx.log(f"**{owner.name}** is hurt by the binding move! (\u2212**{dmg:,}** HP)")
        if self.turns_remaining <= 0 or owner.fainted:
            owner.remove(Binding, ctx.engine.events)
            ctx.log(f"**{owner.name}** was freed from the binding move!")

    def _source(self, ctx: "BattleContext") -> "Battler | None":
        for b in ctx.field.all_battlers():
            if b.id == self.source_id:
                return b
        return None


@dataclass
class Bide(Volatile):
    """MIGRATION_STATUS.md gap. Ported from Showdown's `bide` condition --
    accumulates all move damage taken over 2 turns, then unleashes it
    doubled at whoever dealt the LAST of it (not the most, not the
    first -- Showdown's own `effectState.lastDamageSource` is
    overwritten on every hit, so only the most recent attacker is
    eligible for the retaliation, exactly matched here). `on_after_damaged`
    is the generic post-hit-reaction hook (Moxie, Rocky Helmet, Static,
    ... -- see damage.py's own docstring on why this single dispatch
    covers all of them); Bide's registration on its OWN holder is what
    makes it only ever see damage dealt TO that Pokemon, with no extra
    filtering needed. The move itself (moves/bide.py) owns the
    charge-then-release turn structure (mirroring how moves/charging.py's
    factory owns Fly/Dig/etc.'s own two-turn structure) and the actual
    damage-dealing -- this class only accumulates."""
    total_damage: int = 0
    last_damage_source_id: object = None
    turns_remaining: int = 2
    slug: str = "bide"

    async def on_after_damaged(self, ctx: "MoveContext", owner: "Battler", damage: int) -> None:
        if damage <= 0:
            return
        self.total_damage += damage
        self.last_damage_source_id = ctx.user.id


@dataclass
class Curse(Volatile):
    """MIGRATION_STATUS.md gap. Ported from Showdown's `curse` condition
    -- the Ghost-type-user half of Curse (the non-Ghost half is just a
    self stat change, already handled generically by `stat_changes` data
    the same way any other self-buffing status move is -- see the move's
    own `on_hit`/`get_targets` in moves/curse.py for the type branch).
    1/4 max HP residual damage each end-of-turn, no duration -- lasts
    until the cursed Pokemon switches out or faints (Volatiles already
    clear on switch)."""
    slug: str = "curse"

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        if owner.fainted:
            return
        dmg = max(1, owner.vitals.max_hp // 4)
        owner.vitals.hp = max(0, owner.vitals.hp - dmg)
        ctx.log(f"**{owner.name}** is afflicted by the curse! (\u2212**{dmg:,}** HP)")


# ---------------------------------------------------------------------------
# Smack Down / Minimize / Stockpile -- ported from Pokemon Showdown's move
# conditions (data/moves.ts + data/mods/gen*). Behaviour lives in the Volatile
# (hooks); moves/{grounding,minimize,stockpile}.py only attach them.
# ---------------------------------------------------------------------------

@dataclass
class SmackedDown(Volatile):
    """Showdown's `smackdown` condition: forces the owner to be grounded
    until it switches out. A pure marker -- `Battler.is_grounded` checks for
    it (right after Gravity/Ingrain, BEFORE the Flying/Levitate checks),
    which is what makes Ground moves hit a Flying type or Levitate user."""
    slug: str = "smacked-down"


# Moves Showdown flags `minimize` (they deal double damage to, and always hit,
# a minimized target), BY GENERATION. Base set = current data/moves.ts; the
# older-gen additions come from the data/mods chain (gen3 -> gen4 -> gen5 ->
# gen6 -> gen7 -> ...: a mod's changes apply to its own gen AND every older
# one).
_MINIMIZE_BASE = frozenset({
    "stomp", "body-slam", "steamroller", "dragon-rush", "flying-press",
    "heat-crash", "heavy-slam", "malicious-moonsault", "supercell-slam",
})
_MINIMIZE_GEN6_AND_OLDER = frozenset({"phantom-force", "shadow-force"})
_MINIMIZE_GEN3_ONLY = frozenset({"astonish", "extrasensory", "needle-arm"})


def minimize_flagged(move_slug: str, gen: int) -> bool:
    if move_slug in _MINIMIZE_BASE:
        return True
    if gen <= 6 and move_slug in _MINIMIZE_GEN6_AND_OLDER:
        return True
    return gen <= 3 and move_slug in _MINIMIZE_GEN3_ONLY


@dataclass
class Minimize(Volatile):
    """Showdown's `minimize` condition. The +evasion half is the MOVE's own
    boost; this volatile is the part that reacts to being hit:
      * `onSourceModifyDamage`: flagged moves deal x2 (not in Gen 1,
        whose mod removes it);
      * `onAccuracy`: flagged moves always hit (Gen 6+ only; Gen 5's mod
        removes it).
    Both test `owner is ctx.current_target` so a minimized Pokemon never
    modifies a hit on a different target of a spread move."""
    slug: str = "minimize"

    async def on_modify_damage_taken(self, ctx: "MoveContext", owner: "Battler", damage: int) -> int | None:
        gen = ctx.ruleset.number
        if owner is not ctx.current_target or gen < 2 or not minimize_flagged(ctx.move.slug, gen):
            return None
        return damage * 2

    async def on_set_accuracy(self, ctx: "MoveContext", owner: "Battler", value: float) -> float | None:
        gen = ctx.ruleset.number
        if owner is not ctx.current_target or gen < 6 or not minimize_flagged(ctx.move.slug, gen):
            return None
        return float("inf")   # Showdown returns `true`: the move cannot miss


@dataclass
class Stockpile(Volatile):
    """Showdown's `stockpile` condition: up to 3 layers, each raising Def and
    SpD by 1 -- but it records how many stages it ACTUALLY gained per stat
    (a stat already at +6 gained nothing) and gives back exactly that when
    released by Spit Up / Swallow. Release is `release_stockpile` below;
    leaving the field just drops the volatile along with all stat stages."""
    slug: str = "stockpile"
    layers: int = 0
    def_gained: int = 0
    spd_gained: int = 0

    async def add_layer(self, ctx: "MoveContext", owner: "Battler") -> None:
        from .stats import apply_stat_change
        self.layers += 1
        if ctx.ruleset.number < 4:        # Showdown's Gen 3 Stockpile counts layers (for Spit Up / Swallow) but raises no stats
            return
        before_def, before_spd = owner.stages["def"], owner.stages["spd"]
        await apply_stat_change(ctx, owner, "defense", 1)
        await apply_stat_change(ctx, owner, "special-defense", 1)
        self.def_gained += owner.stages["def"] - before_def
        self.spd_gained += owner.stages["spd"] - before_spd


async def release_stockpile(ctx: "MoveContext", owner: "Battler") -> None:
    """Showdown's stockpile `onEnd`: undo only the stages it really gave."""
    from .stats import apply_stat_change
    sp = owner.get(Stockpile)
    if sp is None:
        return
    owner.remove(Stockpile, ctx.events)
    if sp.def_gained:
        await apply_stat_change(ctx, owner, "defense", -sp.def_gained)
    if sp.spd_gained:
        await apply_stat_change(ctx, owner, "special-defense", -sp.spd_gained)
    ctx.log(f"**{owner.name}**'s stockpiled effect wore off!")


@dataclass
class FocusPunchCharge(Volatile):
    """Showdown's `focuspunch` condition (duration 1): added at the START of
    the turn for a queued Focus Punch ("tightening its focus"); any damaging
    hit before the move executes sets `lost_focus`, which makes the move
    fail without costing PP (`Move.before_move_callback`). It also blocks
    flinch (`Move.on_secondary_effect`'s Flinch application checks for it).
    Gone at end of turn whether or not the move happened."""
    slug: str = "focus-punch"
    lost_focus: bool = False

    async def on_after_damaged(self, ctx: "MoveContext", owner: "Battler", damage: int) -> None:
        from .move import MoveCategory
        if owner is ctx.current_target and ctx.move.category is not MoveCategory.STATUS:
            self.lost_focus = True

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        owner.remove(FocusPunchCharge, ctx.engine.events)


@dataclass
class FuryCutter(Volatile):
    """Showdown's `furycutter` condition (duration 2): each consecutive use
    doubles Fury Cutter's power (x1, x2, x4 -> 40/80/160). It's refreshed on
    every use and so lapses unless the move is used again on the very next
    turn. `moves/power_callbacks.py` starts/restarts it."""
    slug: str = "fury-cutter"
    multiplier: int = 1
    turns_left: int = 2

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        self.turns_left -= 1
        if self.turns_left <= 0:
            owner.remove(FuryCutter, ctx.engine.events)


async def transform_into(ctx, user: "Battler", target: "Battler") -> bool:
    """Showdown's `Pokemon.transformInto` (Transform move and Imposter). The user copies the target's types, calculated
    stats (every stat but HP), ability, stat stages and moves (each with 5 PP, or 1 if the original had 1), keeping its
    own HP, level, gender and form. Everything is undone when it leaves the field (`Battler.clear_temp`).

    Fails against a fainted target, a target behind a Substitute (Gen 5+) or one that is already transformed. Returns
    whether it happened (the caller reports the failure)."""
    from .battler import MoveSlot
    from .abilities._util import engine_of, set_ability
    if target.fainted or user.fainted or target.transformed_into is not None or target.get(Substitute) is not None:
        return False
    user._pre_transform_slots = user.moves.slots
    user.moves.slots = [MoveSlot(move_slug=s.move_slug, pp=1 if s.max_pp == 1 else 5, max_pp=1 if s.max_pp == 1 else 5)
                        for s in target.moves.slots]
    user.stat_override = {k: int(target._raw_stat(k)) for k in ("atk", "def", "spa", "spd", "spe")}
    user.type_override = list(target.effective_types())
    for k, v in target.stages.items():
        user.stages[k] = v
    user.transformed_into = target.name
    set_ability(ctx, user, target.effective_ability())
    engine_of(ctx).log.append(f"**{user.name}** transformed into **{target.name}**!")
    return True


@dataclass
class GastroAcid(Volatile):
    """Ability suppressed (Gastro Acid). A marker: the holder's ability Effect is detached from the bus (the ability id is unchanged, as in Showdown) and
    `BattleEngine.sync_ability_and_item` will not re-attach it while this volatile is present; leaving the field clears the volatile and the next sync restores it."""


# Showdown `onResidualOrder` of each end-of-turn volatile (read by EventBus.run(order_range=...); an effect without one runs last, at 29).
for _cls, _order in ((LeechSeed, (8, 0)), (Nightmare, (11, 0)), (Curse, (12, 0)), (Binding, (13, 0)), (Taunt, (15, 0)), (Encore, (16, 0)), (Disable, (17, 0)),
                     (Yawn, (23, 0)), (PerishSong, (24, 0))):
    _cls.residual_order = _order

# ... and Gens 3-4 (Showdown data/mods/gen4): (order, sub-order)
for _cls, _order in ((LeechSeed, (10, 5)), (Nightmare, (10, 7)), (Curse, (10, 8)), (Binding, (10, 9)), (Taunt, (10, 15)), (Encore, (10, 14)), (Disable, (10, 13)),
                     (Yawn, (10, 19)), (PerishSong, (12, 0))):
    _cls.residual_order_gen4 = _order

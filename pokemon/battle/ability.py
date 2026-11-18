"""Ability base class.

Abilities are Effects (see ecs/component.py) attached to a Battler for the
whole battle -- structurally identical to a Volatile like Substitute, just
attached at switch-in instead of created mid-battle by a move, and only ever
removed by something that explicitly swaps it (Skill Swap, Gastro Acid,
Worry Seed, ...), which is just detaching one Ability component and
attaching another.

IMPORTANT invariant: Ability instances are STATELESS SINGLETONS -- see
registry.py. One `Intimidate()` object is shared by every Pokémon in every
battle that has Intimidate; it must never store per-Pokémon or per-battle
data on `self`. Anything that needs to persist (Slow Start's turn counter,
Flash Fire's "already boosted" flag, ...) lives on a Volatile/Component
attached to the *owner* Battler instead, exactly like a move would.

Hook coverage mirrors Move's, plus the battle-wide moments abilities
specifically care about. Override only what you need; everything defaults
to "no opinion" (return None / do nothing) so an ability with no registered
class still behaves correctly as a complete no-op (see AbilityRegistry).
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from ..ecs.component import Effect

if TYPE_CHECKING:
    from .battler import Battler
    from .context import BattleContext, MoveContext


class Ability(Effect):
    slug: str = ""
    display_name: str = ""

    # -- lifecycle --------------------------------------------------------
    async def on_switch_in(self, ctx: "BattleContext", owner: "Battler") -> None:
        """Intimidate, Drizzle/Drought/Sand Stream/Snow Warning, Download,
        Trace, Frisk, Forewarn, Imposter, Anticipation, ... all hook here."""

    async def on_switch_out(self, ctx: "BattleContext", owner: "Battler") -> None:
        """Regenerator, Natural Cure."""

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        """Speed Boost, Moody, Slow Start countdown, Shed Skin, Harvest, ..."""

    # -- defending against a move ------------------------------------------
    async def on_try_hit(self, ctx: "MoveContext", owner: "Battler") -> bool | None:
        """Return False to fully block the move against this owner
        (Bulletproof, Soundproof, Sap Sipper vs. a *status* Grass move,
        ...). Return None to have no opinion."""

    async def on_type_immunity(self, ctx: "MoveContext", owner: "Battler", type_name: str) -> bool | None:
        """Flash Fire / Water Absorb / Levitate / Wonder Guard-style
        immunities. True means 'immune'; any side effect (Flash Fire's
        boost, Water Absorb's heal) happens right here, since this hook is
        the one place that already knows both the move and the owner."""

    async def on_modify_damage_taken(self, ctx: "MoveContext", owner: "Battler", damage: int) -> int | None:
        """Multiscale, Filter, Solid Rock, Fluffy, Thick Fat, ..."""

    async def on_status_immunity(self, ctx: "BattleContext", owner: "Battler", status: str) -> bool | None:
        """Immunity, Insomnia, Vital Spirit, Limber, Water Veil, Magma
        Armor, Own Tempo (confusion), Sweet Veil, Pastel Veil, Comatose,
        ..."""

    async def on_stat_drop_immunity(self, ctx: "BattleContext", owner: "Battler", stat: str) -> bool | None:
        """Clear Body, White Smoke, Hyper Cutter (Attack only), Keen Eye
        (accuracy only), Big Pecks (Defense only), ..."""

    async def on_modify_stat_change(self, ctx: "MoveContext | BattleContext", owner: "Battler", stat: str, delta: int) -> int | None:
        """Contrary (invert the delta), Simple (double its magnitude).
        Applies to EVERY stat-stage change this owner receives, from any
        source -- old code's `clamp_stage` checked these unconditionally
        before the per-stat Hyper Cutter/Big Pecks/Keen Eye immunity check
        (contore.py:16213-16218), so this hook is expected to run first."""

    async def on_stat_lowered(self, ctx: "MoveContext | BattleContext", owner: "Battler", stat: str, diff: int) -> None:
        """[ADDITIVE / Track C] Fires on owner once one of its OWN stats
        has actually just been lowered by a foe (diff is the negative
        amount that landed, post-clamp -- not fired for a no-op drop
        against an already-minned stat). Defiant (Attack) and Competitive
        (Sp. Atk) live here. Old code hardcoded this reaction only at
        Intimidate's own call site (contore.py's old
        `FighterState._apply_entry_abilities`) and separately again inside
        apply_move's generic stat-changes block (contore.py:19238-19245);
        dispatching it once from inside Battler.clamp_stage() itself
        (battler.py) covers every foe-caused drop uniformly -- Growl,
        Screech, Charm, a held item, anything -- rather than requiring
        each new stat-lowering effect to remember to re-implement it."""

    async def on_afflicted_with_status(self, ctx: "MoveContext | BattleContext", owner: "Battler", status: str) -> None:
        """Fires after a status *successfully* lands on owner (i.e. past
        on_status_immunity) -- Synchronize mirrors poison/burn/paralysis
        back onto whoever inflicted it (never sleep/freeze/confusion; old
        code: contore.py:19295-19296)."""

    async def on_before_move(self, ctx: "MoveContext", owner: "Battler") -> bool | None:
        """Mirrors Volatile.on_before_move (volatile.py) for the one
        ability that needs it: Truant, alternating loaf/act every turn
        (contore.py:16150-16154). Return True to skip the move entirely."""

    async def on_check_trapped(self, ctx: "BattleContext", owner: "Battler", fleeing: "Battler") -> bool | None:
        """Arena Trap, Shadow Tag, Magnet Pull: return True to prevent
        `fleeing` from switching out or fleeing a wild encounter. `owner`
        is whoever holds the trapping ability; `fleeing` is the Pokemon
        trying to leave. Old code: _is_trapped_by_opponent,
        contore.py:15074-15093."""

    async def on_after_damaged(self, ctx: "MoveContext", owner: "Battler", damage: int) -> None:
        """Rough Skin/Iron Barbs recoil, Aftermath, Cursed Body, Static/
        Flame Body/Poison Point/Effect Spore contact procs, Weak Armor, ..."""

    async def on_indirect_damage(self, ctx: "BattleContext | MoveContext", owner: "Battler", damage: int,
                                  source: str = "") -> int | None:
        """Magic Guard: return 0 to block non-move damage entirely (weather
        chip, status ticks, Leech Seed, Curse, entry hazards, Life Orb
        recoil, contact-punish recoil like Rough Skin/Rocky Helmet on the
        *attacker* side, ...). This is deliberately a SEPARATE hook from
        on_modify_damage_taken, which is specifically about a move's own
        direct hit -- Magic Guard leaves that undiminished; only damage
        with no attached move-hit is what it blocks. Every indirect-damage
        source (weather, status, held items, ...) is expected to route
        through this hook rather than each reimplementing an
        `if ability != "magic-guard"` guard the way the old monolith did
        at 15+ separate call sites (contore.py:14850, 15797-15966 im,
        16006-16025, 18951-19192, 20664, 22877, ...).

        `source` distinguishes WHICH kind of indirect damage this is --
        one of "recoil", "weather", "status", "contact-punish", "ability",
        "item" (see damage.py/engine.py/volatile.py's call sites for which
        is which) -- so an implementation that only cares about one kind
        (Rock Head: only "recoil"; Sand Veil/Snow Cloak/Sand Force: only
        "weather", not e.g. poison damage that merely happens to occur
        during a sandstorm) can check it. Most implementations (Magic
        Guard, Poison Heal) don't need to and can ignore the parameter
        entirely.

        [Track C, see CONTRACT_CHANGES.md] Dispatch through this MUST be
        LOCAL -- `owner.consult_reduce("on_indirect_damage", ctx, damage)`,
        where `owner` is specifically the Battler actually taking the
        damage -- never the global event bus. Every implementation of this
        hook (Magic Guard, Poison Heal, Sand Veil, Snow Cloak, Sand Force)
        checks only its own `owner`'s state with no cross-check against
        who the "real" recipient is, which is only correct if `owner` is
        guaranteed to be that recipient. A few call sites originally used
        the global bus (`ctx.events.reduce(...)`, which dispatches to
        EVERY registered effect battle-wide) and were fixed once this was
        noticed -- with the global bus, any Pokemon anywhere in the battle
        having Magic Guard would have blocked every OTHER Pokemon's
        poison/weather/recoil damage too."""

    async def on_modify_drain(self, ctx: "MoveContext", owner: "Battler", amount: int) -> int | None:
        """[ADDITIVE / Track C] Liquid Ooze: return the negated amount to
        turn the ATTACKER's HP-drain heal into damage instead, when
        `owner` (this ability's holder) is the one who got hit and drained
        from. Dispatched globally (`ctx.events.reduce`, self-gated via
        `owner in ctx.targets`) since -- unlike on_indirect_damage above --
        this is inherently about one Pokemon's ability affecting a
        DIFFERENT Pokemon's (the attacker's) HP change, not its own. Old
        code: contore.py's drain-move branch checking
        `defender_state.effective_ability() == "liquid-ooze"` inline at
        each drain-move's own call site."""

    async def on_faint(self, ctx: "BattleContext", owner: "Battler") -> None:
        """Rare for abilities specifically (most on-faint reactions are
        Volatiles like Destiny Bond on the *other* Pokémon)."""

    # -- stat/accuracy calculation hooks (wired up once battle/damage.py and
    #    the real stat pipeline exist -- Phase 3) --------------------------
    async def on_modify_atk(self, ctx: "MoveContext", owner: "Battler", value: int) -> int | None:
        """Huge Power, Pure Power, Hustle, Guts, Slow Start, ..."""

    async def on_modify_def(self, ctx: "MoveContext", owner: "Battler", value: int) -> int | None:
        """Marvel Scale (x1.5 while statused), Fur Coat (x2), Grass Pelt
        (x1.5 in Grassy Terrain). Additive alongside on_modify_atk/spe --
        the frozen contract only shipped those two; Defense/Sp.Atk/Sp.Def
        need the same shape (contore.py:15648-15656)."""

    async def on_modify_spa(self, ctx: "MoveContext", owner: "Battler", value: int) -> int | None:
        """Solar Power (x1.5 in sun), Plus/Minus (x1.5 with its partner
        active), Defeatist (x0.5 below half HP)."""

    async def on_modify_spd(self, ctx: "MoveContext", owner: "Battler", value: int) -> int | None:
        """Flower Gift (x1.5 in sun -- the *ally's* Sp.Def, not the
        holder's own; see misc_abilities.py's FlowerGift for how `owner`
        vs. the boosted Battler are kept straight)."""

    async def on_modify_spe(self, ctx: "BattleContext", owner: "Battler", value: int) -> int | None:
        """Chlorophyll, Swift Swim, Sand Rush, Quick Feet, Unburden, ..."""

    async def on_modify_accuracy(self, ctx: "MoveContext", owner: "Battler", value: float) -> float | None:
        """Compound Eyes, Hustle's accuracy penalty, Sand Veil, Snow Cloak,
        Tangled Feet, ..."""

    # -- additive hooks (Track D, migration batch 1): none of these existed
    # in the frozen contract drop, so adding them is safe per CONTRACT.md --
    # they cover real monolith behaviour (see pokemon/battle/abilities/*.py
    # docstrings for the exact source lines) that had nowhere else to hook.

    async def on_modify_power(self, ctx: "MoveContext", owner: "Battler", power: int) -> int | None:
        """Attacker-side base-power multipliers that apply before the damage
        formula: Technician, Iron Fist, Strong Jaw, Reckless, Tough Claws,
        Mega Launcher, Sand Force, Rivalry, Analytic, the pinch abilities
        (Blaze/Overgrow/Torrent/Swarm), Sheer Force, aura abilities, type-
        boost held items. `owner` is whichever side's Effect this is -- the
        implementation is responsible for checking `owner is ctx.user` vs.
        `owner in ctx.targets` as needed, exactly like Move.on_modify_power
        already does for move-level modifiers. Dispatched via `reduce`
        alongside Move's own on_modify_power, so both chains apply."""

    async def on_modify_damage(self, ctx: "MoveContext", owner: "Battler", damage: int) -> int | None:
        """Final-damage multipliers not already covered by
        on_modify_damage_taken (which is specifically the *defender's own*
        ability reducing damage to itself, e.g. Filter/Solid Rock). This
        hook is for the attacker's own final-damage effects: Tinted Lens,
        Neuroforce, Life Orb / Expert Belt as held items, ..."""

    async def on_modify_crit_multiplier(self, ctx: "MoveContext", owner: "Battler", multiplier: float) -> float | None:
        """Sniper (2.25x instead of 1.5x on a critical hit)."""

    async def on_modify_crit_stage(self, ctx: "MoveContext", owner: "Battler", stage: int) -> int | None:
        """Super Luck (+1 crit stage), held items Scope Lens/Razor Claw
        (+1), Lansat Berry (+2, see the Volatile/held-item that tracks it)."""

    async def on_check_crit_immune(self, ctx: "MoveContext", owner: "Battler") -> bool | None:
        """Battle Armor / Shell Armor: return True to prevent this owner
        from ever being critically hit."""

    async def on_check_accuracy(self, ctx: "MoveContext", owner: "Battler") -> bool | None:
        """No Guard: return True (dispatched via `first`) to make the move
        bypass its accuracy check entirely, from EITHER side -- the old
        code's `aab == "no-guard" or (dab == "no-guard" and not mold_break)`.
        This is exactly the case events.py's own docstring names as the
        motivating example for the `first` dispatch style."""

    async def on_bypass_substitute(self, ctx: "MoveContext", owner: "Battler") -> bool | None:
        """Infiltrator: return True to hit through Substitute/screens as if
        the move itself carried MoveFlag.SUBSTITUTE_BYPASS. `owner` here is
        the attacker (dispatched against the move's user, not the target)."""

    async def on_bounce_move(self, ctx: "MoveContext", owner: "Battler") -> bool | None:
        """Magic Bounce: return True to reflect an incoming status move back
        at its user instead of letting it resolve normally. The engine is
        expected to re-run the move with attacker/target swapped and
        `ctx.is_bounced = True` (see context.py) so this can't loop twice."""

    async def on_suppress_secondary_effect(self, ctx: "MoveContext", owner: "Battler") -> bool | None:
        """Sheer Force: return True to skip the move's normal
        on_secondary_effect roll entirely for this use (its power boost
        happens separately, via on_modify_power)."""

    async def on_modify_secondary_chance(self, ctx: "MoveContext", owner: "Battler", chance: int) -> int | None:
        """Serene Grace (double the % chance) on the attacker; Shield Dust
        (force to 0) on the defender."""

    async def on_modify_pp_cost(self, ctx: "MoveContext", owner: "Battler", cost: int) -> int | None:
        """Pressure: moves used against this owner cost an extra PP."""

    async def on_modify_priority(self, ctx: "MoveContext", owner: "Battler", priority: int) -> int | None:
        """Prankster (+1 to status moves), Gale Wings (+1 to Flying-type
        moves), Triage-style items. Old code: contore.py:16445-16447."""

    async def on_block_mental_move(self, ctx: "MoveContext", owner: "Battler", move_slug: str) -> bool | None:
        """Oblivious (Taunt/Attract/Captivate), Aroma Veil (Taunt/Attract/
        Encore -- a slightly larger set than Oblivious's, per old code's
        own distinction at contore.py:17056/17068/17090). `owner` is the
        would-be target; the move itself is expected to check this before
        applying its effect, the same way Substitute's on_try_hit already
        works in volatile.py."""

    async def on_modify_move_type(self, ctx: "MoveContext", owner: "Battler", move_type: str) -> str | None:
        """Aerilate/Pixilate/Refrigerate (Normal -> Flying/Fairy/Ice, plus
        a separate 1.3x power boost via on_modify_power), Normalize
        (any type -> Normal, no power boost in Gen 6). Old code:
        contore.py:18359-18363."""

    async def on_check_flinch_immune(self, ctx: "MoveContext", owner: "Battler") -> bool | None:
        """Inner Focus: return True to prevent owner from flinching at
        all. Old code: contore.py:19316."""

    async def on_modify_weight(self, ctx: "MoveContext | BattleContext", owner: "Battler", weight_kg: float) -> float | None:
        """Heavy Metal (x2), Light Metal (x0.5) -- consulted by whatever
        computes weight for Low Kick/Grass Knot/Heat Crash/Heavy Slam and
        for Sky Drop/Strength Sap-adjacent checks. Old code:
        _species_weight_kg, contore.py:16691-16698."""

    async def on_flinched(self, ctx: "MoveContext", owner: "Battler") -> None:
        """Steadfast: +1 Speed each time owner actually flinches (only
        fires once flinch immunity above has already been checked and
        failed to apply). Old code: contore.py:19318-19319."""

    async def on_modify_hits(self, ctx: "MoveContext", owner: "Battler", min_hits: int, max_hits: int) -> tuple[int, int] | None:
        """Skill Link: guarantee a multi-hit move's maximum hit count."""

    async def on_modify_type_effectiveness(self, ctx: "MoveContext", owner: "Battler", effectiveness: float) -> float | None:
        """Overrides the pure type-chart result (as opposed to
        on_type_immunity, which is for ability-*granted* immunities like
        Levitate). Scrappy (owner=attacker: Ghost's 0x vs Normal/Fighting
        becomes 1x). Ring Target as a held item does the same generically
        for any 0x match-up -- see items.py."""

    # -- [ADDITIVE / Track C] one hook Track D's batch didn't need yet ------
    async def on_negates_burn_penalty(self, ctx: "MoveContext", owner: "Battler") -> bool | None:
        """Guts: return True to make stats.py skip burn's Attack-halving
        entirely for this calculation, rather than applying the halving and
        then separately multiplying by Guts' own +50% on top of an already
        -halved value. Guts' +50% itself is still granted independently via
        on_modify_atk -- the two are deliberately separate hooks so a
        future ability that *only* wants one half of this behaviour has
        somewhere to hook. See stats.py's Attack calculation for the exact
        ordering this depends on (halving is checked BEFORE the
        on_modify_atk chain runs, using this hook's answer)."""

    async def on_negates_paralysis_penalty(self, ctx: "MoveContext | BattleContext", owner: "Battler") -> bool | None:
        """Quick Feet: return True to skip paralysis's Speed penalty
        entirely for this read, rather than applying the (generation-
        dependent) penalty and then separately multiplying by Quick Feet's
        own +50% on top of an already-reduced value. Same shape as, and
        modeled directly after, on_negates_burn_penalty above -- Pokemon
        Showdown's data/conditions.ts checks `!pokemon.hasAbility(
        'quickfeet')` before applying paralysis's penalty at all, rather
        than applying both unconditionally. See battler.py's stat_async
        for the exact ordering this depends on."""

"""Accuracy checking and the damage-calculation/application pipeline.
Owned by Track C. Old code: apply_move, contore.py:16742-19434 -- the
generic, move-agnostic parts of that 2700-line function live here; the
per-move special cases (Gyro Ball's power formula, Foul Play's use of the
target's own Attack, Freeze-Dry's flipped Water effectiveness, ...) now
live as small overrides on each move's own class instead (see
moves/examples.py) rather than an if/elif chain keyed on move name, which
was the entire point of this refactor.

Two entry points:
  - check_accuracy(ctx, move, target): the miss/hit roll. Called by
    engine.py for EVERY move against EVERY target, damaging or not
    (Thunder Wave needs an accuracy check just as much as Flamethrower
    does) -- BEFORE move.on_hit() runs at all, so a status move's own
    on_hit never has to check this itself.
  - calculate_and_apply_damage(ctx, move, target): everything from "how
    much damage does this hit do" through "is anyone dead now", called
    once per target from BasicMove.on_hit (frozen move.py) once
    check_accuracy has already passed. Handles multi-hit internally
    (Parental Bond / Skill Link / the 2-5-hit Gen5+ distribution), so
    engine.py and move.py only ever call it once per target regardless of
    how many actual hits that turns into.

Known gaps (see MIGRATION_STATUS.md for the full list): OHKO moves get a
correct accuracy roll here but still need a dedicated Move subclass to
actually deal max_hp damage instead of the normal power formula; Rock
Head isn't implemented (needs on_indirect_damage to carry *which* kind of
indirect damage this is before it safely can be, since recoil and, say,
poison damage are currently indistinguishable to that hook).
"""
from __future__ import annotations
import dataclasses
import math
import random
from typing import TYPE_CHECKING

from .move import MoveCategory, MoveFlag, bypasses_substitute_move
from .types import single_type_effectiveness, type_effectiveness
from .side_conditions import screen_mult
from .volatile import Substitute, SmackedDown
from .battler import _Ingrain

if TYPE_CHECKING:
    from .battler import Battler
    from .move import Move
    from .context import MoveContext

# Storm Throw / Frost Breath: guaranteed crit. Old code: the explicit
# `move_name in (...)` check at the crit-rate site
# (apply_move_full.txt:1912-1913 in this session's extraction). Kept as a
# small set here rather than a dedicated class per move -- a move needing
# more than "always crit" should still get its own class instead of
# growing this tuple.
_ALWAYS_CRIT_MOVES = frozenset({"storm-throw", "frost-breath"})

# Psyshock/Psystrike/Secret Sword: special-category moves that use the
# target's physical Defense instead of Sp. Def. Old code:
# apply_move_full.txt:1921-1922 (`uses_phys_def`).
_PSYSHOCK_FAMILY = frozenset({"psyshock", "psystrike", "secret-sword"})

# Non-Fire-type moves that also thaw a frozen target, Gen 6 onward
# (Bulbapedia's freeze article: "Scald, Steam Eruption, Scorching Sands
# and Matcha Gotcha can also be used to thaw a frozen target").
_THAWS_FROZEN_TARGET = frozenset({"scald", "steam-eruption", "scorching-sands", "matcha-gotcha"})


async def check_accuracy(ctx: "MoveContext", move: "Move", target: "Battler") -> bool:
    """The miss/hit roll for one move against one target. Old code: the
    accuracy block at apply_move_full.txt:1540-1600 (contore.py's
    equivalent, pre-extraction). OHKO moves get their real accuracy
    formula here (level-difference based, and outright fail against a
    higher-level target) even though the move that actually deals the
    damage doesn't exist yet -- see this module's docstring."""
    spec = move.spec
    if spec.is_ohko:
        if target.identity.level > ctx.user.identity.level:
            return False
        accuracy = float(spec.accuracy or 30) + ctx.user.identity.level - target.identity.level
        return random.random() * 100 <= accuracy

    from .battler import _Telekinesis
    if target.get(_Telekinesis) is not None:
        return True                      # Showdown Telekinesis `onAccuracy`: any non-OHKO move hits
    accuracy = await compute_accuracy(ctx, move, target)
    if accuracy is None:
        return True
    return random.random() * 100 <= accuracy


_LEGACY_BOOST_TABLE = (1, 4 / 3, 5 / 3, 2, 7 / 3, 8 / 3, 3)


async def _legacy_accuracy(ctx: "MoveContext", move: "Move", target: "Battler", accuracy: float) -> float | None:
    """Gens 3-4 (Showdown's gen4 mod, which gen3 inherits): the attacker's accuracy stage and the defender's evasion stage are applied SEPARATELY as
    ratios from [1, 4/3, 5/3, 2, 7/3, 8/3, 3] in plain floating point, BEFORE the ModifyAccuracy abilities/items -- the reverse order of Gen 5+, with no
    truncation until a modifier is applied."""
    simple = lambda mon: ctx.ruleset.number == 4 and mon.identity.ability_slug == "simple"      # Gen 4 Simple doubles the effective accuracy / evasion stage (`ModifyBoost`)
    if not await ctx.events.first("on_ignore_accuracy_boost", ctx):
        b = max(-6, min(6, ctx.user.stages.get("acc", 0) * (2 if simple(ctx.user) else 1)))
        accuracy = accuracy * _LEGACY_BOOST_TABLE[b] if b > 0 else accuracy / _LEGACY_BOOST_TABLE[-b]
    if not move.ignores_evasion() and not await ctx.events.first("on_ignore_evasion", ctx):
        b = max(-6, min(6, target.stages.get("eva", 0) * (2 if simple(target) else 1)))
        if b > 0:
            accuracy /= _LEGACY_BOOST_TABLE[b]
        elif b < 0:
            accuracy *= _LEGACY_BOOST_TABLE[-b]
    accuracy = await ctx.events.reduce("on_set_accuracy", ctx, accuracy)
    if accuracy == float("inf"):
        return None
    modifier = await ctx.events.reduce("on_accuracy_modifier", ctx, 4096)
    if ctx.field.gravity:
        modifier = chain_mod(modifier, 6840)
    # Showdown's `runEvent` applies the chained modifier ONLY IF the running value is a whole number (`relayVar === Math.abs(Math.floor(relayVar))`). In Gens 3-4 the float stage
    # ratios usually leave a fraction, so Compound Eyes / Hustle / Sand Veil / Snow Cloak / Tangled Feet / Gravity are silently DROPPED after a stage boost. That is a Showdown
    # artifact, matched on purpose (the owner's decision: trust Showdown). The DIRECT float items (Bright Powder, Wide Lens...) are setters and still apply.
    if modifier != 4096 and accuracy == int(accuracy) and accuracy >= 0:
        accuracy = (int(accuracy * modifier) + 2047) // 4096
    return accuracy


async def compute_accuracy(ctx: "MoveContext", move: "Move", target: "Battler") -> float | None:
    """The accuracy percentage `move` rolls against `target`, or None if it can't miss. Mirrors Showdown's `hitStepAccuracy`: No Guard / Lock-On
    first (accuracy becomes `true`), then the move's own accuracy (weather-dependent for Thunder/Hurricane/Blizzard), then ONE combined
    `accuracy stage - evasion stage` boost applied in integer arithmetic, then the ModifyAccuracy chain (Compound Eyes, Wide Lens, Hustle,
    Sand Veil, Snow Cloak, Bright Powder, Gravity ...). Split out of `check_accuracy` so tests can compare the NUMBER with Showdown's."""
    spec = move.spec
    if spec.always_hits or spec.accuracy is None:
        return None
    # No Guard: moves involving either side (as attacker or defender) never miss.
    if await ctx.events.first("on_check_accuracy", ctx):
        return None

    accuracy = float(spec.accuracy)
    accuracy = await move.on_modify_accuracy(ctx, target, accuracy) or accuracy      # the move's own (weather-dependent) accuracy
    if accuracy == float("inf"):      # the move itself never misses in this situation (Thunder in rain, Blizzard in hail/snow)
        return None

    if ctx.ruleset.number <= 4:
        return await _legacy_accuracy(ctx, move, target, accuracy)

    # Showdown's order (`hitStepAccuracy`): 1. ModifyAccuracy -- SETTERS first (Wonder Skin: "50"; Minimize vs Stomp: "true"), then every multiplier
    # CHAINED into one modifier and applied once (Compound Eyes, Hustle, Sand Veil, Bright Powder, Wide Lens, Gravity, ...);  2. only THEN the stage
    # boost, so a setter's value is itself boosted/dropped by the stages;  3. the `Accuracy` event (No Guard) -- handled at the top.
    accuracy = await ctx.events.reduce("on_set_accuracy", ctx, accuracy)
    if accuracy == float("inf"):
        return None
    modifier = await ctx.events.reduce("on_accuracy_modifier", ctx, 4096)
    if ctx.field.gravity:
        modifier = chain_mod(modifier, 6840)             # Gravity's chainModify([6840, 4096])
    accuracy = modify_raw(int(accuracy), int(modifier))

    # ONE combined boost = clamp(accuracy stage) - clamp(evasion stage), clamped again, truncated once. Unaware (as the TARGET) ignores the
    # attacker's accuracy boost; Keen Eye / Mind's Eye / Illuminate / Unaware (as the ATTACKER) and a few moves ignore the target's evasion.
    boost = 0
    if not await ctx.events.first("on_ignore_accuracy_boost", ctx):
        boost = max(-6, min(6, ctx.user.stages.get("acc", 0)))
    if not move.ignores_evasion() and not await ctx.events.first("on_ignore_evasion", ctx):
        boost = max(-6, min(6, boost - max(-6, min(6, target.stages.get("eva", 0)))))
    if boost > 0:
        accuracy = accuracy * (3 + boost) // 3
    elif boost < 0:
        accuracy = accuracy * 3 // (3 - boost)

    return accuracy


async def _resolve_hit_count(ctx: "MoveContext", move: "Move") -> tuple[int, bool]:
    """Returns (n_hits, parental_bond_active). Old code:
    apply_move_full.txt:1737-1745 (contore.py's equivalent, pre-extraction)
    -- Skill Link maxes out min/max-hit moves via on_modify_hits (Ability,
    (ctx, owner, min_hits, max_hits) -> (min, max)|None -- NOT the
    string-returning hook an earlier draft of this function assumed), the
    classic 2-5-hit family uses the real Gen5+ 35/35/15/15 weighting (not
    a flat 1-in-4, a mistake some fan implementations make), everything
    else is a plain randint(min, max). Parental Bond is handled as its own
    special case per abilities/damage_modifiers.py's ParentalBond
    docstring, which explicitly names this the call site responsible for
    invoking `.applies()`."""
    spec = move.spec
    forced = await move.on_hit_count(ctx)
    if forced is not None:
        return forced, False
    min_hits, max_hits = spec.min_hits, spec.max_hits

    if min_hits != max_hits:
        overridden = await ctx.events.first("on_modify_hits", ctx, min_hits, max_hits)  # Skill Link
        if overridden is not None:
            min_hits, max_hits = overridden

    if min_hits == max_hits:
        n_hits = min_hits
    elif min_hits == 2 and max_hits == 5:
        n_hits = random.choices([2, 3, 4, 5], weights=[35, 35, 15, 15])[0]
    else:
        n_hits = random.randint(min_hits, max_hits)

    pb_active = False
    if n_hits == 1 and ctx.user.effective_ability() == "parental-bond":
        pb = ctx.engine.abilities.get("parental-bond")
        if pb.applies(ctx, ctx.user):
            n_hits, pb_active = 2, True
    return n_hits, pb_active


from .fixed import modify as _modify, modify_raw, chain_mod   # Showdown `battle.modify` (see fixed.py)


def _damage_roll() -> int:
    """Showdown's `randomizer`: `100 - random(16)`, i.e. one of the 16
    integers 85..100 (not a continuous 0.85-1.0). A module-level function so
    tests can pin the roll."""
    return random.randint(85, 100)



async def _legacy_damage(ctx, spec, user, target, *, base: int, power_mult: float, is_crit: bool, stab: float,
                         eff: float, weather_mult: float, scr_mult: float, gen: int) -> int:
    """Gen 3 / Gen 4 `modifyDamage` (Showdown data/mods/gen3|gen4/scripts.ts).
    A different ORDER from Gen 5+: burn first, then screens ("phase 1"),
    spread, weather, `+2`, crit, the "phase 2" hooks, and only then STAB and
    type. Gen 4 rolls the random factor before STAB; Gen 3 rolls it LAST,
    after the type step and the final hooks. Gen 3 also halves (not x0.75)
    spread moves and bumps a zero physical hit to 1 before the `+2`."""
    d = base
    if (user.vitals.status == "burn" and d and spec.category is MoveCategory.PHYSICAL
            and not await user.consult_first("on_negates_burn_penalty", ctx)):
        d = _modify(d, 0.5)
    if scr_mult != 1.0:
        d = _modify(d, scr_mult)
    if ctx.spread_modifier != 1.0:
        d = _modify(d, 0.5 if gen == 3 else ctx.spread_modifier)
    if weather_mult != 1.0:
        d = _modify(d, weather_mult)
    if gen == 3 and spec.category is MoveCategory.PHYSICAL and not d:
        d = 1
    d += 2
    if is_crit:
        d = _modify(d, 2.0)
    # "Phase 2" (Showdown `ModifyDamagePhase2`, then floored): in Gens 3-4 ONLY Life Orb (x1.3 as a plain float, floored) and the Gen 4 Metronome item use it. Everything else -- Expert Belt,
    # resist berries, Tinted Lens, Solid Rock ... -- is the FINAL `ModifyDamage` event, after STAB and the type multiplier (this used to run them ALL before the roll).
    d = int(await ctx.events.reduce("on_modify_damage_phase2", ctx, d))
    async def final_hooks(v: int) -> int:
        v = int(await ctx.events.reduce("on_modify_damage", ctx, v))
        return int(await ctx.events.reduce("on_modify_damage_taken", ctx, v))
    if gen == 4:
        d = (d * _damage_roll()) // 100                   # Gen 4 rolls BEFORE STAB / type
    d = _modify(d, stab)
    steps = round(math.log2(eff)) if eff > 0 else 0
    for _ in range(steps):
        d *= 2
    for _ in range(-steps):
        d //= 2
    d = await final_hooks(d)
    if gen == 3:
        d = (d * _damage_roll()) // 100                   # Gen 3 rolls LAST
    return max(1, d)


async def _crit_roll(ctx: "MoveContext", move: "Move", target: "Battler") -> bool:
    """Old code: apply_move_full.txt:1899-1919 (contore.py's equivalent,
    pre-extraction) -- but that code's [24, 8, 2, 1] table is actually
    Gen 7+'s (Pokemon Showdown's sim/battle-actions.ts: critMult = [0, 24,
    8, 2, 1] for gen>=7 vs. [0, 16, 8, 2, 1] for gen 6 specifically, and a
    longer 6-entry table for gen 3-5), not Gen 6's despite the old
    monolith's claimed ruleset -- now read from
    ctx.ruleset.mechanics.crit_stage_denominators instead of hardcoded, so
    each generation gets its own real table. See CONTRACT_CHANGES.md."""
    from .side_conditions import LuckyChant
    target_side = ctx.target_side(target)
    if target_side.has(LuckyChant):
        return False  # Lucky Chant: the defending side can't be crit at all
    if move.never_crits(ctx):
        return False

    denominators = ctx.ruleset.mechanics.crit_stage_denominators
    if move.slug in _ALWAYS_CRIT_MOVES:
        is_crit = True
    else:
        stage = await ctx.events.reduce("on_modify_crit_stage", ctx, move.spec.crit_rate_stage)
        stage = max(0, min(len(denominators) - 1, stage))
        is_crit = random.randint(1, denominators[stage]) == 1
    if is_crit and await ctx.events.first("on_check_crit_immune", ctx):
        is_crit = False
    return is_crit


def _weather_suppressed(user: "Battler", target: "Battler") -> bool:
    """Air Lock/Cloud Nine: blanket "ignore weather's effects" switch,
    checked directly (like Field.damage_mult's other callers already do)
    rather than as a hook, since it isn't a reaction to anything -- it's
    just off. Old code: `weather_suppressed_here`,
    apply_move_full.txt:1886-1888."""
    return user.identity.ability_slug in ("air-lock", "cloud-nine") or \
        target.identity.ability_slug in ("air-lock", "cloud-nine")


async def calculate_and_apply_damage(ctx: "MoveContext", move: "Move", target: "Battler") -> None:
    user = ctx.user
    n_hits, pb_active = await _resolve_hit_count(ctx, move)

    bypasses_walls = bool(await ctx.events.first("on_bypass_substitute", ctx))  # Infiltrator
    # Infiltrator only skips a Substitute from Gen 6 (Showdown `move.infiltrates && this.gen >= 6`); in Gen 5 it skipped screens / Safeguard only.
    bypasses_substitute = bypasses_substitute_move(move.slug, ctx.ruleset.number) or (bypasses_walls and ctx.ruleset.number >= 6)

    for hit_i in range(n_hits):
        if target.fainted or (user.fainted and not ctx.user_self_ko):
            break
        # Parental Bond: second hit at 50% power (Gen 6 value -- see
        # abilities/damage_modifiers.py's ParentalBond docstring for why
        # this bot specifically uses 50%, not later gens' 25%).
        power_mult = 0.5 if (pb_active and hit_i == 1) else 1.0
        ctx.current_hit_index = hit_i
        await _resolve_one_hit(ctx, move, target, hit_i, bypasses_substitute, bypasses_walls, power_mult)
        ctx.result.hits += 1


async def _resolve_one_hit(
    ctx: "MoveContext", move: "Move", target: "Battler", hit_i: int,
    bypasses_substitute: bool, bypasses_screens: bool, power_mult: float = 1.0,
) -> None:
    user = ctx.user
    spec = move.spec

    is_crit = await _crit_roll(ctx, move, target)
    ctx.result.critical = is_crit  # per-hit, not accumulated across a multi-hit move

    # -- type: attacking type, immunity, effectiveness ---------------------
    move_type = await move.on_modify_type(ctx, target, spec.type_name) or spec.type_name
    # The USER's ability may change the type (Pixilate/Aerilate/Refrigerate, Normalize): Showdown's `onModifyType`.
    # damage.py used to consult only the move's own hook, so those abilities never changed a move's type.
    move_type = await ctx.events.reduce("on_modify_move_type", ctx, move_type)
    ctx.move_type = move_type
    new_category = await move.on_modify_category(ctx, target, spec.category)
    if new_category is not None and new_category is not spec.category:
        spec = dataclasses.replace(spec, category=new_category)          # this hit only: every later step below reads the local `spec`

    immune = await target.consult_first("on_type_immunity", ctx, move_type)
    if immune:
        ctx.hit_blocked = True
        return  # the ability itself already logged its own message

    defender_types = target.effective_types()
    if move_type == "Ground":
        # Showdown (`runImmunity`): immunity to Ground is NOT a type-chart
        # fact, it's "airborne" -- so Gravity, Smack Down, Iron Ball and
        # Ingrain can make a Flying type or Levitate user hittable, and
        # `getEffectiveness` then treats the chart's Flying immunity as
        # neutral. Levitate, Air Balloon, Magnet Rise etc. live in
        # `is_grounded`, not as per-ability hooks.
        from .abilities._util import attacker_breaks_mold
        if not target.is_grounded(
            defender_types, ctx.field, ignore_ability=attacker_breaks_mold(ctx),
            negate_immunity=target.effective_item() == "Ring Target",
        ):
            if (target.effective_ability() == "levitate" and "Flying" not in defender_types
                    and not attacker_breaks_mold(ctx)):
                ctx.log(f"**{target.name}** makes Ground moves miss with Levitate!")
            else:
                ctx.log(f"It doesn't affect **{target.name}**!")
            ctx.hit_blocked = True
            return
        defender_types = [t for t in defender_types if t != "Flying"]

    from .moves.volatile_moves import negates_type_immunity
    if negates_type_immunity(target, move_type) or await ctx.events.first("on_ignore_type_immunity", ctx, move_type):
        # Showdown `move.ignoreImmunity[type]` (Scrappy: Normal/Fighting vs Ghost): an IMMUNE type component counts as NEUTRAL, the
        # other component still applies (Fighting into Ghost/Poison is x0.5, into Ghost/Dark x2) -- not "the whole result becomes 1".
        eff = 1.0
        for dt in defender_types:
            if dt != "Typeless":
                eff *= single_type_effectiveness(move_type, dt, ruleset=ctx.ruleset) or 1.0
    else:
        eff = type_effectiveness(move_type, defender_types, ruleset=ctx.ruleset)
    # Iron Ball (Showdown, Gen 5+): `onEffectiveness` runs ONCE PER defending type and tests `target.hasType('Flying')` rather than `type === 'Flying'`, so for a Flying-type
    # holder EVERY type's modifier is zeroed and a Ground move is simply neutral (a Fire/Flying holder takes x1 from Earthquake, not x2). Not when Ingrain / Smack Down /
    # Gravity is what grounds it (the handler returns early and the normal grounding rule applies). A deliberate Showdown quirk, matched on purpose.
    if (move_type == "Ground" and ctx.ruleset.number >= 5 and target.effective_item() == "Iron Ball" and "Flying" in target.effective_types()
            and not ctx.field.gravity and not target.has(SmackedDown) and target.get(_Ingrain) is None):
        eff = 1.0
    eff = await move.on_modify_type_effectiveness(ctx, target, eff) or eff
    eff = await ctx.events.reduce("on_modify_type_effectiveness", ctx, eff)  # Scrappy, Ring Target, Wonder Guard

    weather_suppressed = _weather_suppressed(user, target)
    if (ctx.field.weather == "strong-winds" and not weather_suppressed and "Flying" in target.effective_types()):
        # Delta Stream (Showdown `onEffectiveness`): only the FLYING type's own super-effective component is cancelled.
        # (This used to flatten the whole result to 1.0 -- Fire into Steel/Flying lost its x2 from Steel too.)
        flying_part = single_type_effectiveness(move_type, "Flying", ruleset=ctx.ruleset)
        if flying_part > 1.0:
            eff = eff / flying_part
            if hit_i == 0:
                ctx.log("The mysterious air current weakened the super-effective attack!")

    # Set BEFORE any damage hook runs: Tinted Lens / Filter / Solid Rock / Prism Armor / Neuroforce read it, and it
    # used to be assigned only at the very end, so they always saw the default 1.0 and never did anything.
    ctx.result.effectiveness = eff

    if eff == 0.0:
        ctx.log(f"It doesn't affect **{target.name}**!")
        ctx.hit_blocked = True
        return

    # -- attack/defense stats -----------------------------------------------
    # Unaware (Showdown `onAnyModifyBoost`, breakable): the TARGET's Unaware zeroes the attacker's offensive boosts; the ATTACKER's Unaware zeroes
    # the target's defensive boosts. (It did nothing at all before -- the ability had no implementation.)
    ign_off = bool(await ctx.events.first("on_ignore_offensive_boosts", ctx))
    ign_def = move.ignores_defensive_boosts() or bool(await ctx.events.first("on_ignore_defensive_boosts", ctx))
    if spec.slug == "foul-play":
        # Showdown `overrideOffensivePokemon: 'target'`: the TARGET's Attack (with its own boosts) against the target's normal Defense. This used to
        # divide by the ATTACKER's Defense (Blissey's Foul Play into Steelix did 165 instead of 23).
        atk = await target.stat_async("atk", ctx, crit_ignore_neg=is_crit)
        dfe = await target.stat_async("def", ctx, crit_ignore_pos=is_crit, ignore_stages=ign_def)
    elif spec.slug in _PSYSHOCK_FAMILY:
        atk = await user.stat_async("spa", ctx, crit_ignore_neg=is_crit, ignore_stages=ign_off)
        dfe = await target.stat_async("def", ctx, crit_ignore_pos=is_crit, ignore_stages=ign_def)
    else:
        physical = spec.category is MoveCategory.PHYSICAL
        atk = await user.stat_async("atk" if physical else "spa", ctx, crit_ignore_neg=is_crit, ignore_stages=ign_off)
        def_key = "def" if physical else "spd"  # Wonder Room's swap lives in stat_async
        dfe = await target.stat_async(def_key, ctx, crit_ignore_pos=is_crit, ignore_stages=ign_def)

    # Defender-side stat modifiers -- Showdown's `onSourceModifyAtk` / `onSourceModifySpA` (Thick Fat, Heatproof):
    # they scale the ATTACKER's stat for this hit, so they can't live on the attacker's own hooks.
    atk_hook = "on_source_modify_atk" if (spec.slug == "foul-play" or (
        spec.slug not in _PSYSHOCK_FAMILY and spec.category is MoveCategory.PHYSICAL)) else "on_source_modify_spa"
    atk = int(await target.consult_reduce(atk_hook, ctx, int(atk)))

    # Gen 1-4: Explosion/Self-Destruct halve the TARGET'S Defense
    # (sim/battle-actions.ts, `gen <= 4 && ['explosion','selfdestruct']`,
    # only when the stat actually used is Defense -- not under Wonder Room).
    if (ctx.ruleset.number <= 4 and spec.slug in ("explosion", "self-destruct")
            and spec.category is MoveCategory.PHYSICAL and not ctx.field.wonder_room):
        dfe = max(1, dfe // 2)
    final_stats = await move.on_modify_stats(ctx, target, int(atk), int(dfe))
    if final_stats is not None:
        atk, dfe = final_stats

    crit_mult = await ctx.events.reduce("on_modify_crit_multiplier", ctx, (1.5 if ctx.ruleset.number >= 6 else 2.0) if is_crit else 1.0)
    stab = 1.5 if move_type in user.effective_types() else 1.0
    stab = await ctx.events.reduce("on_modify_stab", ctx, stab)   # Showdown `onModifySTAB` (Adaptability)

    # -- power ----------------------------------------------------------
    power = spec.base_power or 0
    power = await move.on_base_power(ctx, target) or power
    if spec.slug == "triple-kick":
        power = power * (hit_i + 1)
    power = await move.on_modify_power(ctx, target, power) or power
    power = int(await ctx.events.reduce("on_modify_power", ctx, power))
    power = int(await target.consult_reduce("on_source_base_power", ctx, power))   # Dry Skin: Fire x1.25
    if ctx.pursuit_doubled:
        power = (power or 40) * 2
    if ctx.me_first_boost and power:
        power = int(power * 1.5)
    if getattr(user, "helping_hand_boosted", False):
        power = int(power * 1.5)
    # Terrain and Mud/Water Sport are BasePower-stage modifiers in Showdown (weather is a damage-stage one, below).
    # The terrain's own rules object decides boost vs weakening and whose groundedness counts (battle/terrain.py);
    # Cloud Nine / Air Lock do NOT affect terrain, so there is deliberately no `weather_suppressed` gate here.
    if power > 0:
        rules = ctx.field.terrain_rules
        if rules is not None:
            from .abilities._util import attacker_breaks_mold
            mod = rules.base_power_modifier(user, target, move_type, spec.slug, ctx.field, ctx.ruleset.number,
                                            defender_ignores_ability=attacker_breaks_mold(ctx))
            if mod != 4096:
                power = max(1, modify_raw(power, mod))
        if ctx.field.sport_mult(move_type) != 1.0:
            power = max(1, modify_raw(power, 2048 if ctx.ruleset.number <= 4 else 1352))        # Mud/Water Sport: x0.5 in Gens 3-4 (Showdown gen4 mod), x0.33 = 1352/4096 after
    if power <= 0:
        return

    level = user.identity.level
    gen = ctx.ruleset.number

    # -- damage, in Showdown's order (sim/battle-actions.ts getDamage +
    #    modifyDamage), truncating at EVERY step. It used to be one float
    #    product with a continuous 0.85-1.0 roll and a single truncation, which
    #    is neither the games' nor Showdown's rounding.
    atk_i, dfe_i = int(atk), max(1, int(dfe))
    d = ((((2 * level) // 5 + 2) * power * atk_i) // dfe_i) // 50 + 2
    screen_category = "physical" if spec.slug in _PSYSHOCK_FAMILY else spec.category.value
    weather_mult = 1.0 if weather_suppressed else ctx.field.damage_mult(move_type)
    if weather_mult == 0.0:
        # Harsh sunlight vs Water / heavy rain vs Fire: the move FAILS (Showdown `onTryMove` of the primal weather), it
        # doesn't deal "0, bumped up to the 1-damage minimum".
        ctx.log("The Water-type attack evaporated in the harsh sunlight!" if move_type == "Water"
                else "The Fire-type attack fizzled out in the heavy rain!")
        ctx.hit_blocked = True
        return
    if gen <= 4:
        scr = screen_mult(ctx.target_side(target), screen_category, is_crit=is_crit, bypasses_screens=bypasses_screens)
        damage = await _legacy_damage(ctx, spec, user, target, base=d - 2, power_mult=power_mult, is_crit=is_crit, stab=stab,
                                      eff=eff, weather_mult=weather_mult, scr_mult=scr, gen=gen)
    else:
        if ctx.spread_modifier != 1.0:                                   # multi-target
            d = _modify(d, ctx.spread_modifier)
        if power_mult != 1.0:                                            # Parental Bond's 2nd hit
            d = _modify(d, 0.25 if gen > 6 else 0.5)
        if weather_mult != 1.0:
            d = _modify(d, weather_mult)
        if is_crit:
            d = int(d * crit_mult)
        d = (d * _damage_roll()) // 100                                  # random factor
        d = _modify(d, stab)                                             # STAB
        type_steps = round(math.log2(eff)) if eff > 0 else 0             # 2x / 4x / 0.5x / 0.25x
        for _ in range(type_steps):
            d *= 2
        for _ in range(-type_steps):
            d //= 2
        # Burn halves PHYSICAL damage (a damage-stage modifier, not an Attack
        # stat change): Guts negates it, and from Gen 6 so does Facade.
        if (user.vitals.status == "burn" and spec.category is MoveCategory.PHYSICAL
                and not (gen >= 6 and spec.slug == "facade")
                and not await user.consult_first("on_negates_burn_penalty", ctx)):
            d = _modify(d, 0.5)
        if gen == 5 and d == 0:
            d = 1
        scr_mult = screen_mult(ctx.target_side(target), screen_category, is_crit=is_crit,
                                bypasses_screens=bypasses_screens)
        if scr_mult != 1.0:
            d = _modify(d, scr_mult)

        # Attacker-side multipliers (Life Orb, Expert Belt, Tinted Lens, ...)
        # then defender-side ones -- Filter/Solid Rock/Prism Armor/Neuroforce,
        # AND Sturdy/Focus Sash/Focus Band/Multiscale's survival-clamping,
        # which are modeled as the SAME hook returning `owner.vitals.hp - 1`
        # for a would-be-lethal hit (see held_items/combat_items.py's Focus
        # Sash/Band and abilities/misc_abilities.py's Sturdy) rather than a
        # separate survival-check step here.
        # Showdown's `hitSub`: a Substitute is about to absorb this hit, so a resist berry must NOT be eaten for it.
        ctx.hit_substitute = target.get(Substitute) is not None and not bypasses_substitute
        damage = int(await ctx.events.reduce("on_modify_damage", ctx, d))
        damage = int(await ctx.events.reduce("on_modify_damage_taken", ctx, damage))
        if gen != 5 and damage == 0:
            damage = 1
    damage &= 0xFFFF   # Showdown truncates to 16 bits at the very end

    # -- substitute: absorbs the FULL, fully-modified damage (STAB, type,
    #    crit, roll...), which the old bare-formula branch ignored -----------
    sub = target.get(Substitute)
    if sub is not None and not bypasses_substitute:
        sub_dmg = min(damage, sub.hp)
        sub.hp = max(0, sub.hp - sub_dmg)
        broke = sub.hp == 0
        ctx.sub_absorbed = getattr(ctx, "sub_absorbed", 0) + 1     # Showdown `HIT_SUBSTITUTE`: this hit's secondary effects never reach the Pokemon behind it
        if broke:
            target.remove(Substitute, ctx.events)
        ctx.log(f"The substitute took damage for **{target.name}**!" + (" It broke!" if broke else ""))
        return

    if getattr(target, "endure_this_turn", False) and target.vitals.hp > 0 and damage >= target.vitals.hp:
        damage = target.vitals.hp - 1
        ctx.log(f"**{target.name}** endured the hit!")
    if spec.slug in ("false-swipe", "hold-back"):
        damage = max(0, min(damage, target.vitals.hp - 1))

    # -- apply ------------------------------------------------------------
    pre_hp = target.vitals.hp
    target.vitals.hp = max(0, target.vitals.hp - damage)
    dealt = min(damage, pre_hp)
    ctx.result.total_damage += dealt
    ctx.result.effectiveness = eff

    pct = int(dealt * 100 / max(1, target.vitals.max_hp))
    crit_note = " **Critical hit!**" if is_crit else ""
    eff_note = (" It's super effective!" if eff > 1.0 else
                " It's not very effective..." if eff < 1.0 else "")
    ctx.log(f"**{target.name}** took **{dealt:,}** damage! (-{pct}%){crit_note}{eff_note}")

    if target.fainted and target not in ctx.result.fainted:
        ctx.result.fainted.append(target)
        ctx.log(f"**{target.name}** fainted!")

    # Counter/Mirror Coat/Metal Burst all need to know "how much damage,
    # of what category, from whom, how recently" -- plain attributes
    # rather than a Component/Volatile since (like several other small
    # per-Pokemon flags already in this codebase, e.g. contact_abilities.
    # py's Cursed Body) nothing else needs to query this generically
    # through a hook, only these three specific moves read it directly.
    if dealt > 0:
        target.last_damage_taken = dealt
        target.last_damage_category = spec.category
        target.last_damage_from = user
        target.last_damage_turn = ctx.engine.turn_number

    # A damaging Fire-type hit thaws a frozen target -- stable from Gen 3
    # onward (Gen 1's version is narrower: only Fire-type moves that can
    # ALSO inflict burn, excluding Fire Spin; not modeled separately here
    # given how little else about Gen 1 this engine supports yet). A
    # small named set covers the handful of non-Fire-type moves that
    # ALSO thaw from Gen 6 onward (Scald, Steam Eruption, Scorching
    # Sands, Matcha Gotcha) without needing a new MoveFlag for four moves.
    if (dealt > 0 and not target.fainted and target.vitals.status == "freeze"
            and (move_type == "Fire" or (spec.slug in _THAWS_FROZEN_TARGET and ctx.ruleset.number >= 6))):
        target.vitals.status = None
        ctx.log(f"**{target.name}** thawed out!")

    # Single global dispatch covers every post-hit reaction on EITHER side
    # (Moxie/Justified/Rattled/Weak Armor/Anger Point/Static/Flame
    # Body/Rocky Helmet/Rough Skin/Iron Barbs/Shell Bell/Air Balloon/
    # Weakness Policy/Life Orb recoil/...) -- every implementation of
    # on_after_damaged self-gates via `owner is ctx.user` or `owner in
    # ctx.targets` (see abilities/contact_abilities.py's module docstring),
    # so ONE dispatch here is correct, not one per side.
    # Showdown heals a DRAIN move's user as part of the hit, BEFORE the defender's contact reactions (Rough Skin, Rocky Helmet, ...) run;
    # RECOIL (and Life Orb) come after them. Doing the heal after the reactions let it silently undo them for an attacker near full HP.
    if not user.fainted and dealt > 0 and move.spec.drain > 0:
        await _apply_drain_recoil(ctx, move, user, dealt)

    await ctx.events.run("on_after_damaged", ctx, dealt)


async def _apply_drain_recoil(ctx: "MoveContext", move: "Move", user: "Battler", dealt: int) -> None:
    """Old code: the drain/recoil block right after damage application
    (contore.py's equivalent of apply_move_full.txt's post-hit section).
    `spec.drain` is signed: positive = drain (heals the user), negative =
    recoil (hurts the user) -- one field covers both, matching MoveSpec's
    own convention."""
    pct = abs(move.spec.drain)
    # Showdown's recoil fraction is exactly 1/3 in Gens 3-4 and a literal 33/100 from Gen 5 (33% of 150 is 49, 1/3 of it is 50)
    num, den = (1, 3) if (pct == 33 and ctx.ruleset.number <= 4) else (pct, 100)
    if ctx.ruleset.number >= 5:
        amount = max(1, (2 * dealt * num + den) // (2 * den))  # Gen 5+: Math.round (halves up), minimum 1
    else:
        amount = max(1, dealt * num // den)                   # Gens 3-4: Math.floor, minimum 1
    if move.spec.drain > 0:
        # Liquid Ooze (on the TARGET, i.e. whoever the user just drained
        # from) can invert this into damage instead -- dispatched globally
        # since it's a defender's ability affecting the attacker's own HP
        # change, unlike on_indirect_damage's local self-only dispatch.
        if ctx.ruleset.number <= 4:
            # Gen 4 (Showdown: Big Root's `onTryHeal` has no priority there): Big Root scales the drain FIRST, so it also scales the Liquid Ooze damage.
            amount = await user.consult_reduce("on_modify_incoming_drain", ctx, amount)  # Big Root
            amount = await ctx.events.reduce("on_modify_drain", ctx, amount)
        else:
            amount = await ctx.events.reduce("on_modify_drain", ctx, amount)
            if amount > 0:
                amount = await user.consult_reduce("on_modify_incoming_drain", ctx, amount)  # Big Root
        if amount > 0:
            healed = user.heal(amount)
            if healed > 0:
                ctx.log(f"**{user.name}** had its energy drained! (+**{healed:,}** HP)")
        elif amount < 0:
            user.vitals.hp = max(0, user.vitals.hp + amount)  # amount already negative
            ctx.log(f"**{user.name}** was hurt by Liquid Ooze! ({amount:,} HP)")
    else:
        recoil = await user.consult_reduce("on_indirect_damage", ctx, amount, "recoil")  # Rock Head, Magic Guard
        if recoil > 0:
            user.vitals.hp = max(0, user.vitals.hp - recoil)
            ctx.log(f"**{user.name}** was hurt by recoil! (-**{recoil:,}** HP)")
            if user.fainted:
                ctx.log(f"**{user.name}** fainted!")

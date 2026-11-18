"""Shared helpers for the abilities package.

Nothing in here is part of the frozen contract -- it's Track D's own
internal plumbing, free to change as long as the ability family files that
import it stay correct. Centralizing it here is what the top-level
instruction to "turn repeated code into a utility function" means applied
to this directory: mold-breaker interaction and weather/terrain
suppression were each checked ad-hoc, inline, at 10-30 call sites in the
old monolith (see the citations below); every one of those call sites
becomes one of these two helpers instead.

INTEGRATION ASSUMPTIONS (see NOTES_FOR_TRACK_C.md for the full list):
this module assumes `owner.identity.types: list[str]` and
`ctx.field.weather` / `ctx.field.terrain` exist on the real Battler/Field
once Track C fleshes them out. They are not on the illustrative skeleton
yet, so this is written against the shape they're expected to take.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..battler import Battler
    from ..context import BattleContext, MoveContext
    from ..field import Field

# Mold Breaker / Teravolt / Turboblaze: the attacker's ability suppresses
# the DEFENDER's ability for the purposes of type immunities, damage-taken
# reduction, and stat-drop/status immunities. Old code: `if not mold_break`
# guards at contore.py:17424, 17048, 17028, 17056, 17681, 17697, 18259,
# 18287, 18302, 18326, 18391, 18393, 19218, 19224, 19264, 17294-17968
# (the repeated `no_guard = aab == "no-guard" or (dab == "no-guard" and not
# mold_break)` pattern), 19714, plus the ANTI_INTIMIDATE-adjacent check at
# 23366 and CLEAR_BODY_ABS at 19204.
MOLD_BREAKER_SLUGS: frozenset[str] = frozenset({
    "mold-breaker", "teravolt", "turboblaze",
})

# The full set of defending abilities Mold Breaker/Teravolt/Turboblaze
# ignore. Old code checked this piecemeal (a `not mold_break` guard next to
# each individual ability check); this is the authoritative list from
# MOLD_BREAKER_SUPPRESSED, contore.py:2831-2843.
MOLD_BREAKER_SUPPRESSED: frozenset[str] = frozenset({
    "battle-armor", "big-pecks", "bulletproof", "clear-body", "contrary",
    "damp", "dry-skin", "filter", "flash-fire", "flower-gift", "flower-veil",
    "fur-coat", "grass-pelt", "heatproof", "heavy-metal", "hyper-cutter",
    "immunity", "inner-focus", "insomnia", "keen-eye", "leaf-guard",
    "levitate", "light-metal", "lightning-rod", "limber", "magic-bounce",
    "magma-armor", "marvel-scale", "motor-drive", "multiscale", "oblivious",
    "overcoat", "own-tempo", "sand-veil", "sap-sipper", "shell-armor",
    "shield-dust", "simple", "snow-cloak", "solid-rock", "soundproof",
    "sticky-hold", "storm-drain", "sturdy", "suction-cups", "sweet-veil",
    "tangled-feet", "thick-fat", "unaware", "vital-spirit", "volt-absorb",
    "water-absorb", "water-veil", "white-smoke", "wonder-guard", "wonder-skin",
})

# Abilities a copying/swapping effect (Trace, Skill Swap, Role Play,
# Entrainment, Simple Beam, Worry Seed, ...) must never copy FROM or grant
# TO another Pokémon. Old code: ABILITY_CANT_BE_GIVEN, contore.py:2824-2830.
ABILITY_CANT_BE_GIVEN: frozenset[str] = frozenset({
    "flower-gift", "forecast", "illusion", "imposter", "trace", "multitype",
    "rks-system", "stance-change", "schooling", "comatose", "shields-down",
    "disguise", "battle-bond", "power-construct", "zen-mode", "power-of-alchemy",
    "receiver",
})

# Abilities that can never be overwritten on their own holder (Gastro Acid,
# Worry Seed, Skill Swap targeting them, ...). Old code:
# ABILITY_CANT_BE_OVERWRITTEN, contore.py:2820-2823.
ABILITY_CANT_BE_OVERWRITTEN: frozenset[str] = frozenset({
    "multitype", "rks-system", "schooling", "comatose", "shields-down",
    "disguise", "stance-change", "battle-bond", "power-construct", "zen-mode",
})


def attacker_breaks_mold(ctx: "MoveContext") -> bool:
    """True if ctx.user's ability ignores the *defender's* ability-based
    immunities/reductions for this move. Mirrors the old `mold_break`
    local variable that every damage-block ability check consulted."""
    user = getattr(ctx, "user", None)
    if user is None:
        return False
    from ..volatile import GastroAcid
    if user.has(GastroAcid):          # its ability is suppressed
        return False
    return user.identity.ability_slug in MOLD_BREAKER_SLUGS


def defender_ability_active(ctx: "MoveContext", is_attacker_ability: bool) -> bool:
    """Convenience for the extremely common `not mold_break` guard: pass
    True if this hook body is being evaluated as the ability of whoever is
    *receiving* the hit. Attacker-side abilities (Scrappy, Mold Breaker
    itself, ...) are never suppressed by the defender, so this is a no-op
    for them."""
    if not is_attacker_ability:
        return True
    return not attacker_breaks_mold(ctx)


# Weather set/suppress: Air Lock and Cloud Nine turn off all weather's
# secondary effects (damage, stat boosts, accuracy changes, recovery) while
# leaving the weather variable itself set. Old code re-derived this
# `weather_suppressed` boolean independently in apply_end_of_turn (line
# 15730), stat_value (chlorophyll/swift-swim/sand-rush/solar-power block),
# and the entry-effect handler (line ~23430).
WEATHER_SUPPRESSING_SLUGS: frozenset[str] = frozenset({"air-lock", "cloud-nine"})


def weather_suppressed(field: "Field") -> bool:
    """True if any Battler currently on the field has Air Lock/Cloud Nine,
    which turns off weather's gameplay effects (but not the weather
    variable itself -- Drought is still 'active', it just does nothing).

    Takes a `Field` directly rather than a ctx, since BattleContext.field
    and MoveContext.battle.field are spelled differently -- callers pass
    whichever one they have (`ctx.field` or `ctx.battle.field`)."""
    return any(
        b.identity.ability_slug in WEATHER_SUPPRESSING_SLUGS
        for b in field.all_battlers()
    )


# Abilities that block ALL negative stat changes from an outside source
# (as opposed to Hyper Cutter/Big Pecks/Keen Eye, which only block one
# specific stat). Old code: CLEAR_BODY_ABS at contore.py:19204 and the
# equivalent inline tuple at 17424.
FULL_STAT_DROP_IMMUNITY_SLUGS: frozenset[str] = frozenset({
    "clear-body", "white-smoke", "full-metal-body",
})

# Per-stat stat-drop immunity, used by clamp_stage (contore.py:16193-16215).
PER_STAT_DROP_IMMUNITY: dict[str, str] = {
    "atk": "hyper-cutter",
    "def": "big-pecks",
    "acc": "keen-eye",
}

# Status immunities that are fixed per-ability regardless of move source.
# Old code: ABILITY_STATUS_IMMUNITY dict at contore.py:~2810-2845 plus the
# Leaf Guard (sun-only) and Comatose (always-asleep) special cases folded
# in at their use sites.
def field_of(ctx):
    """BattleContext exposes `.field` directly; MoveContext exposes it via
    `.battle.field`. Hooks that need to work from either kind of ctx go
    through this instead of hardcoding one shape."""
    return ctx.field if hasattr(ctx, "field") else ctx.battle.field


def engine_of(ctx):
    """BattleContext exposes `.engine` directly; MoveContext exposes it via
    `.battle.engine` (see context.py). Hooks that need to work from EITHER
    kind of ctx (set_ability is called from on_switch_in, a BattleContext
    hook, AND from on_after_damaged, a MoveContext hook, e.g. Mummy) go
    through this instead of hardcoding one shape."""
    return ctx.engine if hasattr(ctx, "engine") else ctx.battle.engine


_ABILITY_FLAGS: "dict[str, frozenset[str]] | None" = None


def ability_flag(slug: str | None, flag: str) -> bool:
    """Showdown ability flags that gate the ability-changing moves (`cantsuppress`, `failroleplay`, `noentrain`, `failskillswap`, `notrace`, `noreceiver`);
    data/ability_flags.json, from tests/tools/make_ability_flags.js."""
    global _ABILITY_FLAGS
    if _ABILITY_FLAGS is None:
        import json
        from pathlib import Path
        raw = json.loads((Path(__file__).resolve().parents[3] / "data" / "ability_flags.json").read_text(encoding="utf-8"))
        _ABILITY_FLAGS = {k: frozenset(v) for k, v in raw.items()}
    return bool(slug) and flag in _ABILITY_FLAGS.get(slug, ())


def set_ability(ctx, owner: "Battler", new_slug: str) -> None:
    """Swap an owner's active ability (Trace, Skill Swap, Role Play,
    Entrainment, Simple Beam, Worry Seed, Gastro Acid's blank-out, ...).

    ASSUMES `engine.abilities: AbilityRegistry` exists (see
    NOTES_FOR_TRACK_C.md #5a) -- Ability instances are stateless singletons
    (registry.py's own docstring), so changing which one is active means
    detaching the *old concrete instance* from the EventBus and attaching
    the *new* one, not just editing the slug string. A plain
    `identity.ability_slug = new_slug` alone would leave the old ability's
    hooks still registered and the new one's never firing.
    """
    engine = engine_of(ctx)
    registry = engine.abilities
    events = engine.events
    old_slug = owner.identity.ability_slug
    if owner._orig_ability_slug is None:
        owner._orig_ability_slug = old_slug     # restored by Battler.clear_temp when it leaves the field
    if old_slug:
        old_instance = registry.get(old_slug)
        owner.remove(type(old_instance), events)
    owner.identity.ability_slug = new_slug
    new_instance = registry.get(new_slug)
    owner.add(new_instance, events)


def lose_held_item(owner: "Battler", ctx=None, taken: bool = False) -> None:
    """Whoever makes a Battler lose its held item (berries being eaten,
    Pickpocket/Thief/Covet/Knock Off's victim, Fling consuming it, ...)
    should call this afterwards instead of setting `identity.held_item =
    None` directly -- Unburden's 2x Speed depends on knowing a *removal*
    happened, not just that the slot is currently empty (contore.py checks
    this at 15+ call sites; centralizing here is the utility-function
    version of that repetition). Unburden's own on_modify_spe (see
    misc_abilities.py) reads the flag this sets.

    `ctx` (any hook's ctx -- both MoveContext and BattleContext expose
    `.engine`) is needed to also detach the now-stale HeldItem Effect
    component itself, not just clear the identity field: found live-
    testing Knock Off, where Leftovers kept healing every turn AFTER
    being knocked off, because nothing had told BattleEngine to re-sync
    what's actually attached (see BattleEngine.sync_ability_and_item's
    own docstring for the matching bug this is the item-loss mirror of --
    that one was "nothing attaches it in the first place", this one is
    "nothing detaches it once it should be gone"). `ctx=None` is accepted
    (and skips the re-sync) only so call sites that truly have no context
    available don't have to fabricate one; every real call site should
    pass it."""
    if owner.identity.ability_slug == "unburden":
        owner.unburden_active = True
    item = owner.identity.held_item
    owner.identity.held_item = None
    if ctx is not None:
        ctx.engine.sync_ability_and_item(owner)
    if item and not taken:                          # USED (Showdown `useItem` / `eatItem`: `lastItem`, `usedItemThisTurn`, then `AfterUseItem`); `taken=True` for Knock Off / Thief / Trick / Pickpocket ...
        owner.last_item = item
        owner.used_item_this_turn = True
        if ctx is not None:
            _symbiosis(owner, ctx)


def _symbiosis(user: "Battler", ctx) -> None:
    """Showdown `symbiosis.onAllyAfterUseItem` (Gen 6+): after an ally USES its item, the Symbiosis holder hands over its own (Gen 7+: not if the user is about to switch out)."""
    if ctx.ruleset.number < 6:
        return
    eng = ctx.engine
    pivot = getattr(eng, "_pending_pivot", None)
    if ctx.ruleset.number >= 7 and pivot is not None and pivot.get("user") is user:
        return
    for ally in ctx.field.allies_of(user):
        if ally.fainted or ally.identity.ability_slug != "symbiosis" or not ally.identity.held_item or user.identity.held_item:
            continue
        from ..items import is_unstealable
        gift = ally.identity.held_item
        if is_unstealable(gift):
            continue
        lose_held_item(ally, ctx, taken=True)
        user.identity.held_item = gift
        user.unburden_active = False
        eng.sync_ability_and_item(user)
        ctx.log(f"**{ally.name}** shared its **{gift}** with **{user.name}**!")
        return


ABILITY_STATUS_IMMUNITY: dict[str, frozenset[str]] = {
    "immunity": frozenset({"poison", "bad_poison"}),
    "insomnia": frozenset({"sleep"}),
    "vital-spirit": frozenset({"sleep"}),
    "limber": frozenset({"paralysis"}),
    "water-veil": frozenset({"burn"}),
    "magma-armor": frozenset({"freeze"}),
    "own-tempo": frozenset(),  # confusion only; handled separately
    "sweet-veil": frozenset({"sleep"}),
    "pastel-veil": frozenset({"poison", "bad_poison"}),
    "comatose": frozenset({"sleep", "poison", "bad_poison", "burn", "paralysis", "freeze"}),
}


async def ally_blocks(ctx, target, kind: str) -> bool:
    """Does an ability on `target` or one of its ALLIES forbid `kind` ("status:sleep", "volatile:taunt", ...)? Showdown's `onAllySetStatus` / `onAllyTryAddVolatile` (Sweet Veil, Aroma Veil)."""
    field = ctx.field
    for b in field.side_of(target).active:
        if b is not None and not b.fainted and await b.consult_first("on_ally_block", ctx, target, kind):
            return True
    return False

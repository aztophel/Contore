"""Switch-in triggered abilities: Intimidate, Download, Trace, Imposter,
Frisk, Forewarn. Old code: FighterState._apply_entry_abilities,
contore.py:23360-23430. Weather/terrain setters live in field_effects.py
instead, since they're a large enough family (8 abilities, all one-liners
around the same helper) to earn their own file.
"""
from __future__ import annotations
import random

from ..registry import register_ability
from ..ability import Ability
from ._util import ABILITY_CANT_BE_GIVEN, set_ability, engine_of

_STAT_EMOJI = {"atk": "\N{CROSSED SWORDS}", "spa": "\N{SPARKLES}"}

# Old code: ANTI_INTIMIDATE, contore.py:23366-23367. NOT the same set as
# the generic full-stat-drop-immunity abilities (_util.py) -- Inner Focus
# is Intimidate-specific and isn't a general "immune to all stat drops"
# ability, so this stays local rather than merged into _util.
_ANTI_INTIMIDATE = frozenset({
    "clear-body", "white-smoke", "hyper-cutter", "full-metal-body",
})
# Showdown: these four block Intimidate only from Gen 8 on (the Gen 7 mod removes their `onTryBoost`).
_ANTI_INTIMIDATE_GEN8 = frozenset({"inner-focus", "oblivious", "own-tempo", "scrappy"})


@register_ability("intimidate")
class Intimidate(Ability):
    async def on_switch_in(self, ctx, owner) -> None:
        foes = [b for b in ctx.field.all_battlers() if ctx.field.side_of(b) is not ctx.field.side_of(owner)]
        if not foes:
            return
        for foe in foes:
            foe_ab = foe.identity.ability_slug
            if foe_ab in _ANTI_INTIMIDATE or (ctx.ruleset.number >= 8 and foe_ab in _ANTI_INTIMIDATE_GEN8):
                continue
            if foe_ab == "flower-veil" and "Grass" in foe.identity.types:
                ctx.log(f"Flower Veil protected **{foe.name}** from Intimidate!")
                continue
            # Battler.stages is a plain dict (see battler.py's Boosts
            # component) -- it has no .clamp() of its own. Stat-stage
            # changes always go through the async Battler.clamp_stage(),
            # which is also what dispatches Defiant/Competitive generically
            # now (see stats.py's on_stat_lowered) -- but Intimidate's
            # own -1 Attack is still applied directly here first since it's
            # unconditional once the immunities above are cleared.
            msg = await foe.clamp_stage(ctx, "atk", -1, from_foe=True)
            if msg:
                ctx.log(msg)
        ctx.log(f"{owner.name}'s Intimidate lowered the opposing side's Attack!")


@register_ability("download")
class Download(Ability):
    """NOTE (see NOTES_FOR_TRACK_C.md #1): old code compared each foe's
    full effective Defense/Sp.Def at level 50 (`stat_value(...)`, i.e.
    including nature/IVs but not current stat stages). Until Identity
    carries enough to reconstruct that, this compares base stats, which
    gets the right answer for the overwhelming majority of match-ups and
    only misambiguates true you'd need a coin flip on anyway (near-equal
    Def/SpD with a nature pushing it the other way)."""
    async def on_switch_in(self, ctx, owner) -> None:
        foes = [b for b in ctx.field.all_battlers() if ctx.field.side_of(b) is not ctx.field.side_of(owner)]
        if not foes:
            return
        avg_def = sum(f.identity.base_stats["defense"] for f in foes) / len(foes)
        avg_spd = sum(f.identity.base_stats["special-defense"] for f in foes) / len(foes)
        stat = "spa" if avg_def >= avg_spd else "atk"
        msg = await owner.clamp_stage(ctx, stat, 1)
        label = "Sp. Atk" if stat == "spa" else "Attack"
        ctx.log(msg if msg else f"{owner.name}'s Download raised {label}!")


@register_ability("trace")
class Trace(Ability):
    async def on_switch_in(self, ctx, owner) -> None:
        foes = [b for b in ctx.field.all_battlers() if ctx.field.side_of(b) is not ctx.field.side_of(owner)]
        traceable = [f for f in foes if f.identity.ability_slug not in (ABILITY_CANT_BE_GIVEN | {"trace"})]
        if not traceable:
            return
        target = random.choice(traceable)
        traced_slug = target.identity.ability_slug
        set_ability(ctx, owner, traced_slug)
        ctx.log(f"{owner.name} traced {target.name}'s ability!")


@register_ability("imposter")
class Imposter(Ability):
    """Old code (contore.py:23400-23404) transforms into whichever
    opponent is directly across from this Pokémon on entry -- NOT a random
    or "first" opponent. The actual copy-everything-about-the-target
    mechanic (`_perform_transform`) is a Transform-shaped operation that
    needs real engine support (stats/moveset/types all change); this hook
    is the trigger point Track C's Transform implementation should be
    called from once it exists."""
    async def on_switch_in(self, ctx, owner) -> None:
        side = ctx.field.side_of(owner)
        opposite_side = ctx.field.other_side(side)
        my_slot = side.active.index(owner)
        across = len(opposite_side.active) - 1 - my_slot                # Showdown: `foe.active[foe.active.length - 1 - position]` (slot 0 faces the foe's LAST slot)
        if across < 0:
            return
        target = opposite_side.active[across]
        if target is None or target.fainted:
            return
        from ..volatile import transform_into  # Phase 3, see NOTES_FOR_TRACK_C.md #6
        await transform_into(ctx, owner, target)


@register_ability("frisk")
class Frisk(Ability):
    async def on_switch_in(self, ctx, owner) -> None:
        foes = [b for b in ctx.field.all_battlers() if ctx.field.side_of(b) is not ctx.field.side_of(owner)]
        for foe in foes:
            item = foe.identity.held_item
            if item:
                ctx.log(f"{owner.name}'s Frisk revealed that **{foe.name}** holds a **{item}**!")


@register_ability("forewarn")
class Forewarn(Ability):
    async def on_switch_in(self, ctx, owner) -> None:
        from ..registry import MoveRegistry
        registry: MoveRegistry = engine_of(ctx).moves  # see NOTES_FOR_TRACK_C.md #1
        foes = [b for b in ctx.field.all_battlers() if ctx.field.side_of(b) is not ctx.field.side_of(owner)]
        best_slug, best_power, best_foe = None, 0, None
        for foe in foes:
            for slot in foe.moves.slots:
                spec = registry.spec(slot.move_slug)
                power = spec.base_power or (150 if spec.is_ohko else 0)
                if power > best_power:
                    best_slug, best_power, best_foe = slot.move_slug, power, foe
        if best_slug:
            move = registry.get(best_slug)
            ctx.log(f"{owner.name}'s Forewarn revealed **{best_foe.name}**'s **{move.spec.display_name}**!")

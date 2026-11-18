"""Move-blocking abilities that don't fit type_immunity.py or
status_immunity.py because what they block is a move *category* or
*flag*, not a type or a status. Old code: contore.py:18391-18396 (sound/
bullet), 17048-17097 (overcoat/oblivious/aroma-veil/leech-seed),
19216-19224 (shield dust), 17141-17150 (magic bounce).
"""
from __future__ import annotations

from ..registry import register_ability
from ..ability import Ability
from ._util import attacker_breaks_mold


def _flag_blocker(slug: str, flag_name: str, display: str):
    @register_ability(slug)
    class _FlagBlocker(Ability):
        async def on_try_hit(self, ctx, owner) -> bool | None:
            from ..move import MoveFlag
            if owner not in ctx.targets or attacker_breaks_mold(ctx):
                return None
            if not ctx.move.has_flag(getattr(MoveFlag, flag_name)):
                return None
            ctx.log(f"{display} blocked the attack!")
            return True
    _FlagBlocker.__name__ = display.replace(" ", "") + "Ability"
    return _FlagBlocker


Soundproof = _flag_blocker("soundproof", "SOUND", "Soundproof")
Bulletproof = _flag_blocker("bulletproof", "BULLET", "Bulletproof")


@register_ability("overcoat")
class Overcoat(Ability):
    """Blocks powder moves and Leech Seed (contore.py:17048-17097), on top
    of the weather-chip immunity it grants (checked directly in
    field.py's weather_chip alongside Magic Guard, rather than through a
    hook here -- consistent with how that function already checks Sand
    Veil/Sand Force/Sand Rush/Ice Body/Snow Cloak the same way)."""
    async def on_try_hit(self, ctx, owner) -> bool | None:
        from ..move import MoveFlag
        if owner not in ctx.targets or attacker_breaks_mold(ctx) or ctx.ruleset.number < 6:       # Gen 5's Overcoat only stops weather damage
            return None
        if ctx.move.has_flag(MoveFlag.POWDER):     # powder moves ONLY (the monolith also blocked Leech Seed; Showdown does not)
            ctx.log(f"Overcoat protected **{owner.name}** from the powder!")
            return True
        return None


_MENTAL_MOVES_OBLIVIOUS = frozenset({"taunt", "attract", "captivate"})
_MENTAL_MOVES_AROMA_VEIL = frozenset({"attract", "disable", "encore", "heal-block", "taunt", "torment"})   # Showdown onAllyTryAddVolatile


def _mental_blocker(slug: str, move_set: frozenset[str], display: str):
    @register_ability(slug)
    class _MentalBlocker(Ability):
        async def on_block_mental_move(self, ctx, owner, move_slug: str) -> bool | None:
            if owner not in ctx.targets or attacker_breaks_mold(ctx) or move_slug not in move_set:
                return None
            ctx.log(f"{display} prevents {move_slug.replace('-', ' ').title()}!")
            return True
    _MentalBlocker.__name__ = display.replace(" ", "") + "Ability"
    return _MentalBlocker


Oblivious = _mental_blocker("oblivious", _MENTAL_MOVES_OBLIVIOUS, "Oblivious")
AromaVeil = _mental_blocker("aroma-veil", _MENTAL_MOVES_AROMA_VEIL, "Aroma Veil")


@register_ability("flower-veil")
class FlowerVeil(Ability):
    """Blocks both Intimidate (already checked inline in
    entry_effects.Intimidate for Grass-types) AND any stat-lowering
    secondary effect from an opponent's move (contore.py:19222-19224,
    the *general* case this class covers)."""
    async def on_stat_drop_immunity(self, ctx, owner, stat) -> bool | None:
        if attacker_breaks_mold(ctx) or "Grass" not in owner.identity.types:
            return None
        ctx.log(f"**{owner.name}**'s Flower Veil shielded it from the stat drop!")
        return True


@register_ability("shield-dust")
class ShieldDust(Ability):
    """Showdown: `onModifySecondaries` filters out every secondary EXCEPT those with a `self` effect (Flame Charge's +1 Speed,
    Power-Up Punch's +1 Attack are the user's, so the defender's Shield Dust can't stop them), and the ability is `breakable`
    (Mold Breaker ignores it). `ctx.secondary_on_user` is set by the move while it rolls a user-directed secondary."""
    async def on_modify_secondary_chance(self, ctx, owner, chance: int) -> int | None:
        from ._util import attacker_breaks_mold
        if owner not in ctx.targets or getattr(ctx, "secondary_on_user", False) or attacker_breaks_mold(ctx):
            return None
        return 0


@register_ability("serene-grace")
class SereneGrace(Ability):
    async def on_modify_secondary_chance(self, ctx, owner, chance: int) -> int | None:
        if owner is not ctx.user:
            return None
        return chance * 2


@register_ability("magic-bounce")
class MagicBounce(Ability):
    async def on_bounce_move(self, ctx, owner) -> bool | None:
        from ..move import MoveCategory
        if owner not in ctx.targets or ctx.move.category is not MoveCategory.STATUS:
            return None
        if getattr(ctx, "is_bounced", False) or attacker_breaks_mold(ctx):
            return None
        return True


@register_ability("sticky-hold")
class StickyHold(Ability):
    """No hook of its own -- every theft-capable effect (Pickpocket,
    Magician, Thief, Covet, Trick, Knock Off) already checks
    `target.identity.ability_slug == "sticky-hold"` directly before
    moving an item (see contact_abilities.py). Registered as a marker so
    the slug reads as implemented rather than missing."""


@register_ability("klutz")
class Klutz(Ability):
    """GAP: old code's `effective_item()` returns "" whenever the holder's
    ability is Klutz, meaning every held-item check anywhere in the
    monolith was implicitly Klutz-aware for free. The new items.py
    doesn't have an equivalent single choke point yet -- ItemRegistry.get()
    takes a bare slug, not a Battler, so it can't see the holder's
    ability. Whoever wires ItemRegistry into the engine should route
    lookups through something like `get_active_item(owner)` that checks
    Klutz first, rather than calling `.get(owner.identity.held_item)`
    directly at every call site."""


@register_ability("wind-rider")
class WindRider(Ability):
    """Wind moves (Twister, Heat Wave, Hurricane, Tailwind-class...) don't hit it and raise its Attack by 1 instead
    (Showdown `onTryHit`; breakable). Needs `MoveFlag.WIND`, which exists since the move flags were regenerated.
    The Tailwind-activated boost half is not modelled."""
    async def on_try_hit(self, ctx, owner) -> bool | None:
        from ..move import MoveFlag
        from ..stats import apply_stat_change
        if owner is not ctx.current_target or owner is ctx.user or attacker_breaks_mold(ctx):
            return None
        if MoveFlag.WIND not in ctx.move.spec.flags:
            return None
        ctx.log(f"Wind Rider made **{owner.name}** immune to the attack!")
        await apply_stat_change(ctx, owner, "attack", 1)
        return True


@register_ability("stall")
class Stall(Ability):
    """Showdown `onFractionalPriority: -0.1`: moves after every other Pokemon in the same priority bracket (it still loses to a
    higher priority and beats a lower one; Lagging Tail / Full Incense are the item versions)."""
    async def on_fractional_priority(self, ctx, owner, bonus: float, priority: int) -> float | None:
        if owner is not ctx.user:
            return None
        return bonus - 0.1

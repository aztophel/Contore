"""End-of-turn held items: Leftovers, Black Sludge, Flame/Toxic Orb, and
every berry. Old code: FighterState.apply_end_of_turn's item block,
contore.py:15820-15930. Unnerve on the OPPOSING side blocks this entire
block (contore.py:15731, 15820's `not foe_has_unnerve` guard) -- that
check is expected to live in whatever engine code calls these on_end_turn
hooks in the first place, not duplicated in each item below.
"""
from __future__ import annotations
import random

from ..items import HeldItem, register_item
from ..abilities._util import lose_held_item, ABILITY_STATUS_IMMUNITY
from ..volatile import Confusion


def _consume(owner, ctx) -> None:
    owner.berry_used = True
    owner.last_consumed_berry = owner.identity.held_item
    lose_held_item(owner, ctx)


@register_item("Leftovers")
class Leftovers(HeldItem):
    async def on_end_turn(self, ctx, owner) -> None:
        if owner.fainted or owner.vitals.hp >= owner.vitals.max_hp:
            return
        heal = max(1, owner.vitals.max_hp // 16)
        owner.vitals.hp = min(owner.vitals.max_hp, owner.vitals.hp + heal)
        ctx.log(f"**{owner.name}** restored a little HP using its Leftovers! (+**{heal:,}** HP)")


@register_item("Black Sludge")
class BlackSludge(HeldItem):
    async def on_end_turn(self, ctx, owner) -> None:
        if owner.fainted:
            return
        if "Poison" in owner.identity.types:
            if owner.vitals.hp >= owner.vitals.max_hp:
                return
            heal = max(1, owner.vitals.max_hp // 16)
            owner.vitals.hp = min(owner.vitals.max_hp, owner.vitals.hp + heal)
            ctx.log(f"**{owner.name}** restored a little HP using its Black Sludge! (+**{heal:,}** HP)")
        else:
            dmg = max(1, owner.vitals.max_hp // 8)
            dmg = await owner.consult_reduce("on_indirect_damage", ctx, dmg, "item")  # local: see weather_and_recovery.py's Dry Skin note
            if dmg > 0:
                owner.vitals.hp = max(0, owner.vitals.hp - dmg)
                ctx.log(f"**{owner.name}** was hurt by its Black Sludge! (\u2212**{dmg:,}** HP)")


def _status_orb(item_name: str, status: str, display: str):
    @register_item(item_name)
    class _StatusOrb(HeldItem):
        async def on_end_turn(self, ctx, owner) -> None:
            if owner.fainted or owner.vitals.status:
                return
            immune = ABILITY_STATUS_IMMUNITY.get(owner.identity.ability_slug, frozenset())
            if status in immune:
                return
            owner.vitals.status = status
            ctx.log(f"**{owner.name}** was afflicted by its {display}!")
    _StatusOrb.__name__ = display.replace(" ", "") + "Item"
    return _StatusOrb


FlameOrb = _status_orb("Flame Orb", "burn", "Flame Orb")
ToxicOrb = _status_orb("Toxic Orb", "bad_poison", "Toxic Orb")


@register_item("Sitrus Berry")
class SitrusBerry(HeldItem):
    async def on_end_turn(self, ctx, owner) -> None:
        if owner.fainted or getattr(owner, "berry_used", False):
            return
        if not (0 < owner.vitals.hp <= owner.vitals.max_hp // 2):
            return
        heal = 30 if ctx.ruleset.number == 3 else max(1, owner.vitals.max_hp // 4)       # Showdown gen3 mod: `this.heal(30)`
        if owner.identity.ability_slug == "cheek-pouch":
            heal += max(1, owner.vitals.max_hp // 3)
        owner.vitals.hp = min(owner.vitals.max_hp, owner.vitals.hp + heal)
        _consume(owner, ctx)
        ctx.log(f"**{owner.name}**'s Sitrus Berry restored **{heal:,}** HP!")


@register_item("Oran Berry")
class OranBerry(HeldItem):
    async def on_end_turn(self, ctx, owner) -> None:
        if owner.fainted or getattr(owner, "berry_used", False):
            return
        if not (0 < owner.vitals.hp <= owner.vitals.max_hp // 2):
            return
        heal = min(10, owner.vitals.max_hp - owner.vitals.hp)
        owner.vitals.hp = min(owner.vitals.max_hp, owner.vitals.hp + heal)
        _consume(owner, ctx)
        ctx.log(f"**{owner.name}**'s Oran Berry restored **{heal:,}** HP!")


@register_item("Lum Berry")
class LumBerry(HeldItem):
    async def on_end_turn(self, ctx, owner) -> None:
        if owner.fainted or getattr(owner, "berry_used", False):
            return
        has_confusion = owner.has(Confusion)
        if not (owner.vitals.status or has_confusion):
            return
        if owner.vitals.status:
            ctx.log(f"**{owner.name}**'s Lum Berry cured {owner.vitals.status.replace('_', ' ').title()}!")
            owner.vitals.status = None
        if has_confusion:
            owner.remove(Confusion, ctx.engine.events)
            ctx.log(f"**{owner.name}**'s Lum Berry snapped it out of confusion!")
        _consume(owner, ctx)


def _cure_status_berry(item_name: str, status: str, display: str, message: str):
    @register_item(item_name)
    class _CureStatusBerry(HeldItem):
        async def on_end_turn(self, ctx, owner) -> None:
            if owner.fainted or getattr(owner, "berry_used", False) or owner.vitals.status != status:
                return
            owner.vitals.status = None
            _consume(owner, ctx)
            ctx.log(f"**{owner.name}**'s {display} {message}")
    _CureStatusBerry.__name__ = display.replace(" ", "") + "Item"
    return _CureStatusBerry


CheriBerry = _cure_status_berry("Cheri Berry", "paralysis", "Cheri Berry", "cured paralysis!")
ChestoBerry = _cure_status_berry("Chesto Berry", "sleep", "Chesto Berry", "woke it up!")
RawstBerry = _cure_status_berry("Rawst Berry", "burn", "Rawst Berry", "cured the burn!")
AspearBerry = _cure_status_berry("Aspear Berry", "freeze", "Aspear Berry", "thawed it out!")


@register_item("Pecha Berry")
class PechaBerry(HeldItem):
    async def on_end_turn(self, ctx, owner) -> None:
        if owner.fainted or getattr(owner, "berry_used", False) or owner.vitals.status not in ("poison", "bad_poison"):
            return
        owner.vitals.status = None
        _consume(owner, ctx)
        ctx.log(f"**{owner.name}**'s Pecha Berry cured poison!")


@register_item("Persim Berry")
class PersimBerry(HeldItem):
    async def on_end_turn(self, ctx, owner) -> None:
        if owner.fainted or getattr(owner, "berry_used", False) or not owner.has(Confusion):
            return
        owner.remove(Confusion, ctx.engine.events)
        _consume(owner, ctx)
        ctx.log(f"**{owner.name}**'s Persim Berry snapped it out of confusion!")


# Pinch HP berries (Figy/Wiki/Mago/Aguav/Iapapa): heal 1/8 max HP at <=25%,
# with a chance of self-inflicted confusion if the holder's nature dislikes
# the matching flavor. PINCH_BERRY_NATURE_CONFLICT is Track B's flavor/
# nature data (data/natures.json-adjacent) -- imported lazily so this
# module doesn't hard-depend on Track B's data layout at import time.
_PINCH_HP_BERRIES = ("Figy Berry", "Wiki Berry", "Mago Berry", "Aguav Berry", "Iapapa Berry")


def pinch_limit(ctx, owner) -> int:
    """The HP at or below which a PINCH berry (Figy / Wiki / Mago / Aguav / Iapapa, Liechi / Ganlon / Salac / Petaya / Apicot, Custap) is eaten: a quarter of max HP, or HALF with Gluttony (Gen 4+;
    Showdown `pokemon.hp <= pokemon.maxhp / 4 || (pokemon.hp <= pokemon.maxhp / 2 && pokemon.hasAbility('gluttony'))`)."""
    div = 2 if ctx.ruleset.number >= 4 and owner.effective_ability() == "gluttony" else 4
    return owner.vitals.max_hp // div


def _pinch_hp_berry(item_name: str):
    @register_item(item_name)
    class _PinchHPBerry(HeldItem):
        async def on_end_turn(self, ctx, owner) -> None:
            if owner.fainted or getattr(owner, "berry_used", False):
                return
            # Figy / Wiki / Mago / Aguav / Iapapa (Showdown items.ts + data/mods/gen3|gen6|gen7): Gens 3-6 eat at <= 1/2 HP and heal 1/8; Gen 7 eats at <= 1/4 (1/2 with Gluttony) and heals 1/2; Gen 8+ the same
            # threshold and heals 1/3.
            gen, max_hp = ctx.ruleset.number, owner.vitals.max_hp
            limit = max_hp // 2 if gen <= 6 else pinch_limit(ctx, owner)
            if not (0 < owner.vitals.hp <= limit):
                return
            heal = max(1, max_hp // 8 if gen <= 6 else (max_hp // 2 if gen == 7 else max_hp // 3))
            owner.vitals.hp = min(owner.vitals.max_hp, owner.vitals.hp + heal)
            ctx.log(f"**{owner.name}**'s {item_name} restored **{heal:,}** HP!")
            try:
                from ..generations.natures import PINCH_BERRY_NATURE_CONFLICT, NATURE_MODS  # Track B data
                disliked, taste = PINCH_BERRY_NATURE_CONFLICT.get(item_name, (None, None))
                nature = getattr(owner.identity, "nature", None)
                mod = NATURE_MODS.get(nature) if nature else None
                if disliked and mod and mod[1] == disliked and not owner.has(Confusion):
                    owner.add(Confusion(turns_remaining=random.randint(2, 5)), ctx.engine.events)
                    ctx.log(f"**{owner.name}** is confused by the {item_name}'s {taste} taste!")
            except ImportError:
                pass  # nature-flavor conflict is flavor-only; safe to skip if the data isn't wired up yet
            _consume(owner, ctx)
    _PinchHPBerry.__name__ = item_name.replace(" ", "") + "Item"
    return _PinchHPBerry


for _name in _PINCH_HP_BERRIES:
    _pinch_hp_berry(_name)


_PINCH_STAT_BERRIES = {
    # NOTE: keyed with Battler's own STAGE_KEYS spelling ("def", not
    # "defense") -- see battler.py. An earlier draft used "defense" here,
    # which would have silently created a dead, never-read stages["defense"]
    # entry instead of actually boosting Defense.
    "Liechi Berry": "atk", "Ganlon Berry": "def", "Salac Berry": "spe",
    "Petaya Berry": "spa", "Apicot Berry": "spd",
}


def _pinch_stat_berry(item_name: str, stat: str):
    @register_item(item_name)
    class _PinchStatBerry(HeldItem):
        async def on_end_turn(self, ctx, owner) -> None:
            if owner.fainted or getattr(owner, "berry_used", False):
                return
            if not (0 < owner.vitals.hp <= pinch_limit(ctx, owner)):
                return
            # Stat-stage changes always route through Battler.clamp_stage()
            # (never a bare dict/attribute mutation) so Contrary/Simple
            # (on_modify_stat_change) still apply to a berry's own self-
            # boost, exactly as they would to a move's stat change. An
            # earlier draft called a nonexistent owner.stages.clamp() --
            # Battler.stages is a plain dict (see battler.py's Boosts
            # component), it has no such method.
            msg = await owner.clamp_stage(ctx, stat, 1)
            _consume(owner, ctx)
            ctx.log(f"**{owner.name}**'s {item_name} activated! {msg}" if msg else
                     f"**{owner.name}**'s {item_name} activated!")
    _PinchStatBerry.__name__ = item_name.replace(" ", "") + "Item"
    return _PinchStatBerry


for _name, _stat in _PINCH_STAT_BERRIES.items():
    _pinch_stat_berry(_name, _stat)


@register_item("Starf Berry")
class StarfBerry(HeldItem):
    # Stage keys match Battler.STAGE_KEYS exactly ("def"/"acc"/"eva", not
    # "defense"/"accuracy"/"evasion" -- see battler.py and the note in
    # _pinch_stat_berry above about the same class of bug).
    _STATS = ("atk", "def", "spa", "spd", "spe", "acc", "eva")

    async def on_end_turn(self, ctx, owner) -> None:
        if owner.fainted or getattr(owner, "berry_used", False):
            return
        if not (0 < owner.vitals.hp <= pinch_limit(ctx, owner)):
            return
        eligible = [s for s in self._STATS if owner.stages.get(s, 0) < 6]
        if not eligible:
            return
        stat = random.choice(eligible)
        msg = await owner.clamp_stage(ctx, stat, 2)
        _consume(owner, ctx)
        ctx.log(f"**{owner.name}**'s Starf Berry activated! {msg}" if msg else
                 f"**{owner.name}**'s Starf Berry activated!")


@register_item("Lansat Berry")
class LansatBerry(HeldItem):
    async def on_end_turn(self, ctx, owner) -> None:
        if owner.fainted or getattr(owner, "berry_used", False) or getattr(owner, "lansat_active", False):
            return
        if not (0 < owner.vitals.hp <= pinch_limit(ctx, owner)):
            return
        owner.lansat_active = True
        _consume(owner, ctx)
        ctx.log(f"**{owner.name}**'s Lansat Berry heightened its critical-hit ratio!")

    async def on_modify_crit_stage(self, ctx, owner, stage: int) -> int | None:
        if owner is not ctx.user or not getattr(owner, "lansat_active", False):
            return None
        return stage + 2


# Showdown's berries (Sitrus, Oran, Lum, the status-cure berries, every pinch berry, Starf, Lansat...) are `onUpdate` handlers:
# they fire right after the action that crossed the threshold, not at end of turn. Only genuine residual items (Leftovers,
# Black Sludge, Flame/Toxic Orb) are end-of-turn. Each berry's `on_end_turn` is already idempotent (single use via
# `berry_used`), so it simply also answers the engine's `on_update` (BattleEngine._run_update, after every action).
def _berries_answer_update() -> None:
    from ..items import _item_classes
    for name, cls in list(_item_classes.items()):
        if name.endswith(" Berry") and cls.__module__ == __name__ and hasattr(cls, "on_end_turn"):
            cls.on_update = cls.on_end_turn


_berries_answer_update()


Leftovers.residual_order = BlackSludge.residual_order = (5, 4)         # Showdown `onResidualOrder: 5, onResidualSubOrder: 4`
Leftovers.residual_order_gen4 = BlackSludge.residual_order_gen4 = (10, 4)

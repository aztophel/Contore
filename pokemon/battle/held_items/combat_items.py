"""Damage/stat-modifying held items -- everything that isn't an
end-of-turn tick (see end_of_turn.py for those). Old code: the item-
multiplier block at contore.py:18780-18885, King's Rock/Razor Fang
(19309-19311).
"""
from __future__ import annotations

from ..fixed import modify_raw, chain_mod
from ..items import HeldItem, register_item, DRIVE_NAMES, PLATE_NAMES
from ..abilities._util import lose_held_item, attacker_breaks_mold
from ..move import MoveCategory

CHOICE_ITEM_STAT = {"Choice Band": "atk", "Choice Specs": "spa", "Choice Scarf": "spe"}


def _choice_item(item_name: str, stat: str, hook_name: str):
    @register_item(item_name)
    class _ChoiceItem(HeldItem):
        """The 1.5x half is a plain stat hook. The move-lock half needs
        engine support (whatever resolves move selection has to check
        `owner.identity.held_item in CHOICE_ITEM_STAT and owner.locked_move`
        and reject anything else) -- flagged in NOTES_FOR_TRACK_C.md
        rather than faked here, since a real lock can't be expressed as a
        pure stat/damage hook."""
        async def _boost(self, ctx, owner, value: int) -> int | None:
            return int(value * 1.5)
    setattr(_ChoiceItem, hook_name, _ChoiceItem._boost)
    _ChoiceItem.__name__ = item_name.replace(" ", "") + "Item"
    return _ChoiceItem


ChoiceBand = _choice_item("Choice Band", "atk", "on_modify_atk")
ChoiceSpecs = _choice_item("Choice Specs", "spa", "on_modify_spa")
ChoiceScarf = _choice_item("Choice Scarf", "spe", "on_modify_spe")


@register_item("Life Orb")
class LifeOrb(HeldItem):
    async def on_modify_damage(self, ctx, owner, damage: int) -> int | None:
        if owner is not ctx.user or ctx.ruleset.number <= 4:
            return None
        return modify_raw(damage, 5324)   # Life Orb: Showdown uses 5324, not the 5325 of the x1.3 abilities

    async def on_modify_damage_phase2(self, ctx, owner, damage) -> float | None:
        """Gens 3-4 (Showdown gen4 mod `onModifyDamagePhase2`): `damage * 1.3`, floored by the caller -- not the chained 5324/4096, which rounds up on e.g. 16 (20.8 -> 21 instead of 20)."""
        if owner is not ctx.user or ctx.ruleset.number > 4 or getattr(ctx, "future_hit", False):
            return None                    # (Showdown's Gen 3-4 Future Sight / Doom Desire damage is computed at use time without it)
        return damage * 1.3

    async def on_after_move_self(self, ctx, owner) -> None:
        """Runs ONCE per move that landed (Showdown `onAfterMoveSecondarySelf`), not per hit. Old code skips this recoil when Sheer Force suppressed the
        move's secondary effect this use (contore.py:19191's
        `not sheer_force_active`) -- checked here via the same
        on_suppress_secondary_effect hook Sheer Force implements, rather
        than re-deriving "does this move have a secondary" independently."""
        if (owner is not ctx.user or owner.fainted or owner.identity.ability_slug == "magic-guard"
                or ctx.move.category is MoveCategory.STATUS or (getattr(ctx, "future_hit", False) and ctx.ruleset.number < 5)
                or (getattr(ctx, "fixed_damage", False) and ctx.ruleset.number < 5)          # Gens 3-4: recoil needs the damage to have gone through "phase 2" (a fixed-damage move doesn't)
                or (ctx.move.spec.is_ohko and ctx.ruleset.number <= 6)):                      # ... and in Gens 5-6 an OHKO move doesn't recoil either
            return
        suppressed = await ctx.engine.events.first("on_suppress_secondary_effect", ctx)   # (ctx, reg.owner) is how EventBus.first calls each handler
        if suppressed:
            return
        recoil = max(1, owner.vitals.max_hp // 10)
        owner.vitals.hp = max(0, owner.vitals.hp - recoil)
        ctx.log(f"**{owner.name}** was hurt by its Life Orb! (\u2212**{recoil:,}** HP)")


@register_item("Shell Bell")
class ShellBell(HeldItem):
    async def on_after_move_self(self, ctx, owner) -> None:
        total = ctx.result.total_damage
        if owner is not ctx.user or owner.fainted or total <= 0:
            return
        healed = owner.heal(max(1, total // 8))
        if healed:
            ctx.log(f"**{owner.name}** restored a little HP using its Shell Bell! (+**{healed:,}** HP)")


@register_item("Rocky Helmet")
class RockyHelmet(HeldItem):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        from ..move import MoveFlag
        if owner not in ctx.targets or not ctx.move.has_flag(MoveFlag.CONTACT):
            return
        attacker = ctx.user
        if attacker.identity.ability_slug == "magic-guard" or attacker.fainted:
            return
        recoil = max(1, attacker.vitals.max_hp // 6)
        attacker.vitals.hp = max(0, attacker.vitals.hp - recoil)
        ctx.log(f"**{attacker.name}** was hurt by **{owner.name}**'s Rocky Helmet! (\u2212**{recoil:,}** HP)")


@register_item("Focus Sash")
class FocusSash(HeldItem):
    consumable = True

    async def on_modify_damage_taken(self, ctx, owner, damage: int) -> int | None:
        if owner not in ctx.targets or owner.vitals.hp != owner.vitals.max_hp or damage < owner.vitals.hp:
            return None
        lose_held_item(owner, ctx)
        ctx.log(f"**{owner.name}** hung on using its Focus Sash!")
        return owner.vitals.hp - 1


@register_item("Focus Band")
class FocusBand(HeldItem):
    async def on_modify_damage_taken(self, ctx, owner, damage: int) -> int | None:
        import random
        if owner not in ctx.targets or damage < owner.vitals.hp or random.random() >= 0.10:
            return None
        ctx.log(f"**{owner.name}** hung on using its Focus Band!")
        return owner.vitals.hp - 1


@register_item("Weakness Policy")
class WeaknessPolicy(HeldItem):
    consumable = True

    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner not in ctx.targets or ctx.result.effectiveness <= 1.0:
            return
        lose_held_item(owner, ctx)
        atk_msg = await owner.clamp_stage(ctx, "atk", 2)
        spa_msg = await owner.clamp_stage(ctx, "spa", 2)
        ctx.log(f"**{owner.name}**'s Weakness Policy activated!")
        if atk_msg:
            ctx.log(atk_msg)
        if spa_msg:
            ctx.log(spa_msg)


@register_item("Expert Belt")
class ExpertBelt(HeldItem):
    async def on_modify_damage(self, ctx, owner, damage: int) -> int | None:
        if owner is not ctx.user or ctx.result.effectiveness <= 1.0:
            return None
        return modify_raw(damage, 4915)   # Expert Belt


@register_item("Air Balloon")
class AirBalloon(HeldItem):
    consumable = True

    # Ground immunity itself is `Battler.is_grounded` (Air Balloon makes its
    # holder airborne), so Gravity / Smack Down can override it; this class
    # only models the pop.
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner not in ctx.targets:
            return
        lose_held_item(owner, ctx, taken=True)          # a POP, not a use: Showdown sets no `lastItem` for it
        ctx.log(f"**{owner.name}**'s Air Balloon popped!")


@register_item("Eviolite")
class Eviolite(HeldItem):
    """x1.5 Def / Sp.Def while the holder can still evolve (`Identity.can_evolve`; the hook used to read a nonexistent
    `can_still_evolve`, so it never fired and Battler.effective_stat's hardcoded copy did the work)."""
    async def on_modify_def(self, ctx, owner, value: int) -> int | None:
        if not owner.identity.can_evolve:
            return None
        return int(value * 1.5)

    async def on_modify_spd(self, ctx, owner, value: int) -> int | None:
        if not owner.identity.can_evolve:
            return None
        return int(value * 1.5)


@register_item("Assault Vest")
class AssaultVest(HeldItem):
    """The Sp.Def boost is a plain stat hook; "cannot select status
    moves" is a move-legality constraint that needs engine support in
    whatever builds the legal-move list, same category as the Choice
    items' lock above."""
    async def on_modify_spd(self, ctx, owner, value: int) -> int | None:
        return int(value * 1.5)


def _crit_stage_item(item_name: str):
    @register_item(item_name)
    class _CritStageItem(HeldItem):
        async def on_modify_crit_stage(self, ctx, owner, stage: int) -> int | None:
            if owner is not ctx.user:
                return None
            return stage + 1
    _CritStageItem.__name__ = item_name.replace(" ", "") + "Item"
    return _CritStageItem


ScopeLens = _crit_stage_item("Scope Lens")
RazorClaw = _crit_stage_item("Razor Claw")


def _accuracy_item(item_name: str, *, holder_is_target: bool, chain: int, legacy: float, legacy_gen3: float | None = None, needs_target_moved: bool = False):
    """The accuracy items. Gen 5+: a chained fixed-point multiplier (`chainModify([n, 4096])`). Gens 3-4 (Showdown's old item definitions): a DIRECT float
    multiplication of the accuracy as the handler runs -- `accuracy * 0.9` -- so it is a SETTER-style hook (`on_set_accuracy`) rather than a chain element."""
    @register_item(item_name)
    class _AccuracyItem(HeldItem):
        def _applies(self, ctx, owner) -> bool:
            if holder_is_target:
                return owner in ctx.targets
            if owner is not ctx.user:
                return False
            return not (needs_target_moved and any(ctx.engine.will_move(t) is not None for t in ctx.targets))

        async def on_accuracy_modifier(self, ctx, owner, mod: int) -> int | None:
            if ctx.ruleset.number <= 4 or not self._applies(ctx, owner):
                return None
            return chain_mod(mod, chain)

        async def on_set_accuracy(self, ctx, owner, value: float) -> float | None:
            if ctx.ruleset.number > 4 or not self._applies(ctx, owner):
                return None
            return value * (legacy_gen3 if legacy_gen3 is not None and ctx.ruleset.number == 3 else legacy)
    _AccuracyItem.__name__ = item_name.replace(" ", "") + "Item"
    return _AccuracyItem


BrightPowder = _accuracy_item("Bright Powder", holder_is_target=True, chain=3686, legacy=0.9)
LaxIncense = _accuracy_item("Lax Incense", holder_is_target=True, chain=3686, legacy=0.9, legacy_gen3=0.95)
WideLens = _accuracy_item("Wide Lens", holder_is_target=False, chain=4505, legacy=1.1)
ZoomLens = _accuracy_item("Zoom Lens", holder_is_target=False, chain=4915, legacy=1.2, needs_target_moved=True)


TYPE_BOOST_ITEMS = {
    "Charcoal": "Fire", "Mystic Water": "Water", "Miracle Seed": "Grass",
    "Magnet": "Electric", "Never-Melt Ice": "Ice", "Black Belt": "Fighting",
    "Poison Barb": "Poison", "Soft Sand": "Ground", "Sharp Beak": "Flying",
    "Twisted Spoon": "Psychic", "Silver Powder": "Bug", "Hard Stone": "Rock",
    "Spell Tag": "Ghost", "Dragon Fang": "Dragon", "Black Glasses": "Dark",
    "Metal Coat": "Steel", "Silk Scarf": "Normal", "Fairy Feather": "Fairy",
}


# Gen 3 splits physical / special by TYPE, and its type-boost items multiply the matching ATTACKING STAT by 1.1 (Showdown data/mods/gen3/items.ts) instead of the move's power by 1.2.
_GEN3_PHYSICAL_TYPES = frozenset({"Normal", "Fighting", "Flying", "Poison", "Ground", "Rock", "Bug", "Ghost", "Steel"})


def _type_boost_item(item_name: str, move_type: str, gen3_stat: bool = True, gen3_mult: int = 4505):
    @register_item(item_name)
    class _TypeBoostItem(HeldItem):
        async def on_modify_power(self, ctx, owner, power: int) -> int | None:
            if owner is not ctx.user or ctx.move.spec.type_name != move_type or (gen3_stat and ctx.ruleset.number == 3):
                return None
            return modify_raw(power, 4915)

        def _gen3(self, ctx, owner, physical: bool) -> bool:
            return (gen3_stat and ctx.ruleset.number == 3 and owner is ctx.user and ctx.move.spec.type_name == move_type
                    and (move_type in _GEN3_PHYSICAL_TYPES) == physical)

        async def on_modify_atk(self, ctx, owner, value: int) -> int | None:
            return modify_raw(value, gen3_mult) if self._gen3(ctx, owner, True) else None

        async def on_modify_spa(self, ctx, owner, value: int) -> int | None:
            return modify_raw(value, gen3_mult) if self._gen3(ctx, owner, False) else None
    _TypeBoostItem.__name__ = item_name.replace(" ", "").replace("-", "") + "Item"
    return _TypeBoostItem


for _item_name, _move_type in TYPE_BOOST_ITEMS.items():
    _type_boost_item(_item_name, _move_type)

# The Incenses (Showdown items.ts: x1.2 power = 4915/4096, Gen 4+). Sea Incense also exists in Gen 3, where (data/mods/gen3/items.ts) it multiplies the Special Attack stat by 1.05 (= 4301/4096) instead.
_type_boost_item("Sea Incense", "Water", gen3_stat=True, gen3_mult=4301)
for _item_name, _move_type in (("Wave Incense", "Water"), ("Odd Incense", "Psychic"), ("Rock Incense", "Rock"), ("Rose Incense", "Grass")):
    _type_boost_item(_item_name, _move_type, gen3_stat=False)


def _arceus_plate(item_name: str, move_type: str):
    """Plates also change Judgment's own type to match, which is a
    move-level concern for Track C's Judgment implementation -- this class
    only covers the shared 1.2x power boost, same as any other type-boost
    item (contore.py's ARCEUS_PLATES data confirms Plates behave exactly
    like the named type-boost items above, just also holding the
    Judgment-type-change side effect elsewhere)."""
    return _type_boost_item(item_name, move_type, gen3_stat=False)       # Plates are Gen 4+ items: no Gen 3 behaviour of their own


_PLATE_TYPES = {
    "Flame Plate": "Fire", "Splash Plate": "Water", "Zap Plate": "Electric",
    "Meadow Plate": "Grass", "Icicle Plate": "Ice", "Fist Plate": "Fighting",
    "Toxic Plate": "Poison", "Earth Plate": "Ground", "Sky Plate": "Flying",
    "Mind Plate": "Psychic", "Insect Plate": "Bug", "Stone Plate": "Rock",
    "Spooky Plate": "Ghost", "Draco Plate": "Dragon", "Dread Plate": "Dark",
    "Iron Plate": "Steel", "Pixie Plate": "Fairy",
}
for _item_name in PLATE_NAMES:
    _arceus_plate(_item_name, _PLATE_TYPES[_item_name])


def _genesect_drive(item_name: str):
    """Drives are INERT for damage. (They used to be registered as 1.2x type-boost items like Plates, which Showdown does not
    do: a Drive only changes Techno Blast's type -- a move-level concern, not a held-item hook.)"""
    @register_item(item_name)
    class _GenesectDrive(HeldItem):
        pass
    _GenesectDrive.__name__ = item_name.replace(" ", "") + "Item"
    return _GenesectDrive


for _item_name in DRIVE_NAMES:
    _genesect_drive(_item_name)


# ---- Showdown items that were missing entirely (found by diffing Showdown's item data against the registry) ----------------
def _power_item(item_name: str, applies, mult: int):
    """A held item whose `onBasePower` is a fixed 4096ths multiplier under a condition. `applies(ctx, owner)`."""
    @register_item(item_name)
    class _PowerItem(HeldItem):
        async def on_modify_power(self, ctx, owner, power: int) -> int | None:
            if owner is not ctx.user or not applies(ctx, owner):
                return None
            return modify_raw(power, mult)
    _PowerItem.__name__ = item_name.replace(" ", "") + "Item"
    return _PowerItem


def _is_category(name: str):
    return lambda ctx, owner: ctx.move.spec.category.value == name


_power_item("Muscle Band", _is_category("physical"), 4505)     # chainModify([4505, 4096])
_power_item("Wise Glasses", _is_category("special"), 4505)


def _species_type_item(item_name: str, species_ids: frozenset[int], move_types: frozenset[str]):
    """Adamant / Lustrous / Griseous Orb, Soul Dew: x4915/4096 for the named species' moves of the named types
    (Showdown tests `user.baseSpecies.num`; `identity.species_id` is the base form's dex number)."""
    return _power_item(item_name, lambda ctx, owner: owner.identity.species_id in species_ids and ctx.move.spec.type_name in move_types, 4915)


_species_type_item("Adamant Orb", frozenset({483}), frozenset({"Steel", "Dragon"}))
_species_type_item("Lustrous Orb", frozenset({484}), frozenset({"Water", "Dragon"}))
_species_type_item("Griseous Orb", frozenset({487}), frozenset({"Ghost", "Dragon"}))


@register_item("Soul Dew")
class SoulDew(HeldItem):
    """Gen 7+: x4915/4096 to Latios/Latias Psychic and Dragon moves (base power). Gen 3-6 (Showdown's older mods): x1.5 Sp.Atk/Sp.Def."""
    _HOLDERS = frozenset({380, 381})

    async def on_modify_power(self, ctx, owner, power: int) -> int | None:
        if (ctx.engine.ruleset.number < 7 or owner is not ctx.user or owner.identity.species_id not in self._HOLDERS
                or ctx.move.spec.type_name not in ("Psychic", "Dragon")):
            return None
        return modify_raw(power, 4915)

    async def on_modify_spa(self, ctx, owner, value: int) -> int | None:
        if ctx.engine.ruleset.number >= 7 or owner.identity.species_id not in self._HOLDERS:
            return None
        return int(value * 1.5)

    async def on_modify_spd(self, ctx, owner, value: int) -> int | None:
        return await self.on_modify_spa(ctx, owner, value)


@register_item("Punching Glove")
class PunchingGlove(HeldItem):
    """x4506/4096 to punching moves. (It also removes their contact flag -- not modelled yet.)"""
    async def on_modify_power(self, ctx, owner, power: int) -> int | None:
        from ..move import MoveFlag
        if owner is not ctx.user or not ctx.move.has_flag(MoveFlag.PUNCH):
            return None
        return modify_raw(power, 4506)


@register_item("Light Ball")
class LightBall(HeldItem):
    """Pikachu's Attack and Sp. Atk x2 (`baseSpecies.baseSpecies === 'Pikachu'`)."""
    @staticmethod
    def _is_pikachu(owner) -> bool:
        return owner.identity.species_name.lower().startswith("pikachu")

    async def on_modify_atk(self, ctx, owner, value: int) -> int | None:
        return value * 2 if self._is_pikachu(owner) and ctx.ruleset.number != 3 else None       # Gen 3: Sp. Atk only

    async def on_modify_spa(self, ctx, owner, value: int) -> int | None:
        return value * 2 if self._is_pikachu(owner) else None


def _speed_halving_item(item_name: str):
    """Iron Ball and the Power items: Speed x0.5 (`chainModify(0.5)`). (Iron Ball's grounding half lives in Battler.is_grounded.)"""
    @register_item(item_name)
    class _SpeedHalver(HeldItem):
        async def on_modify_spe(self, ctx, owner, value: int) -> int | None:
            return int(value * 0.5)
    _SpeedHalver.__name__ = item_name.replace(" ", "") + "Item"
    return _SpeedHalver


for _n in ("Iron Ball", "Power Anklet", "Power Band", "Power Belt", "Power Bracer", "Power Lens", "Power Weight"):
    _speed_halving_item(_n)


_RESIST_BERRIES = {
    "Occa Berry": "Fire", "Passho Berry": "Water", "Wacan Berry": "Electric", "Rindo Berry": "Grass", "Yache Berry": "Ice",
    "Chople Berry": "Fighting", "Kebia Berry": "Poison", "Shuca Berry": "Ground", "Coba Berry": "Flying", "Payapa Berry": "Psychic",
    "Tanga Berry": "Bug", "Charti Berry": "Rock", "Kasib Berry": "Ghost", "Haban Berry": "Dragon", "Colbur Berry": "Dark",
    "Babiri Berry": "Steel", "Roseli Berry": "Fairy", "Chilan Berry": "Normal",
}


def _resist_berry(item_name: str, move_type: str):
    """Showdown `onSourceModifyDamage`: halves one super-effective hit of `move_type` and is eaten doing so (Chilan: any Normal
    hit). Not eaten when a Substitute takes the hit. (Ripen's x0.25 is not modelled.)"""
    from .end_of_turn import _consume

    @register_item(item_name)
    class _ResistBerry(HeldItem):
        async def on_modify_damage_taken(self, ctx, owner, damage: int) -> int | None:
            if owner is not ctx.current_target or ctx.move.spec.type_name != move_type or getattr(ctx, "hit_substitute", False) or getattr(ctx, "fixed_damage", False):
                return None
            if move_type != "Normal" and ctx.result.effectiveness <= 1:
                return None
            _consume(owner, ctx)
            ctx.log(f"The **{item_name}** weakened the damage to **{owner.name}**!")
            return modify_raw(damage, 2048)
    _ResistBerry.__name__ = item_name.replace(" ", "") + "Item"
    return _ResistBerry


for _n, _t in _RESIST_BERRIES.items():
    _resist_berry(_n, _t)


def _flinch_chance_item(item_name: str):
    @register_item(item_name)
    class _FlinchChanceItem(HeldItem):
        async def on_modify_secondary_chance(self, ctx, owner, chance: int) -> int | None:
            from ..move import MoveCategory
            if owner is not ctx.user or chance or ctx.move.category is MoveCategory.STATUS:
                return None
            return 10
    _FlinchChanceItem.__name__ = item_name.replace(" ", "").replace("'", "") + "Item"
    return _FlinchChanceItem


KingsRock = _flinch_chance_item("King's Rock")
RazorFang = _flinch_chance_item("Razor Fang")


@register_item("Mental Herb")
class MentalHerb(HeldItem):
    """Old code's `_check_mental_herb` cures Taunt/Encore/Attract/Disable/
    Torment/Heal Block the instant one of those is inflicted, not at end
    of turn -- since that's a reactive check tied to each of those
    specific moves rather than a generic hook, `on_afflicted_with_status`
    doesn't fit (those are volatiles, not `status`). Flagged for whichever
    of those five move implementations lands first to call
    `owner.identity.held_item == "Mental Herb"` directly and consume it,
    the same pattern Sticky Hold's docstring uses for theft-blocking."""


@register_item("White Herb")
class WhiteHerb(HeldItem):
    """Restores any lowered stat once. Old code's real trigger is
    immediately after a stat-lowering event; approximated here as an
    end-of-turn check since on_modify_stat_change only sees the delta,
    not a stable "current stages" snapshot to restore from -- flagged as
    an approximation, not exact parity."""
    STATS = ("atk", "defense", "spa", "spd", "spe", "accuracy", "evasion")

    async def on_end_turn(self, ctx, owner) -> None:
        lowered = [s for s in self.STATS if getattr(owner.stages, s, 0) < 0]
        if not lowered:
            return
        for s in lowered:
            setattr(owner.stages, s, 0)
        lose_held_item(owner, ctx)
        ctx.log(f"**{owner.name}**'s White Herb restored its lowered stats!")


@register_item("Ring Target")
class RingTarget(HeldItem):
    async def on_modify_type_effectiveness(self, ctx, owner, effectiveness: float) -> float | None:
        if owner not in ctx.targets or effectiveness != 0.0:
            return None
        return 1.0


@register_item("Red Card")
class RedCard(HeldItem):
    consumable = True
    async def on_after_move_secondary(self, ctx, owner) -> None:
        from ..move import MoveCategory
        attacker = ctx.user
        if (owner not in ctx.targets or owner.fainted or attacker.fainted or attacker.side is None or ctx.move.category is MoveCategory.STATUS
                or getattr(ctx, "hit_substitute", False) or not attacker.side.alive_bench()):
            return
        lose_held_item(owner, ctx)                                   # used up even if the drag is vetoed
        ctx.log(f"**{owner.name}** held up its Red Card against **{attacker.name}**!")
        await ctx.engine._force_switch(ctx, ctx.move, owner, attacker)


@register_item("Destiny Knot")
class DestinyKnot(HeldItem):
    """Showdown `destinyknot` (`onAttract`): when the holder falls in love, whoever attracted it falls in love with the holder too (unless it already is, or Oblivious)."""
    async def on_attract(self, ctx, owner, source) -> None:
        from ..volatile import Attract
        if source is None or source is owner or source.has(Attract) or source.effective_ability() == "oblivious":
            return
        source.add(Attract(source_id=owner.id), ctx.events)
        ctx.log(f"**{source.name}** fell in love with **{owner.name}** too!")


@register_item("Eject Pack")
class EjectPack(HeldItem):
    """Showdown `ejectpack`: ANY stat drop of the holder (not Parting Shot's) arms it; at the next any-move / any-switch-in / residual it is used up and the holder switches out -- unless it cannot
    switch right now (no bench, or another switch is already pending), in which case it stays armed."""
    consumable = True
    async def on_stat_drop_landed(self, ctx, owner, stat: str, diff: int) -> None:
        move = getattr(ctx, "move", None)
        if move is not None and move.spec.slug == "parting-shot":
            return
        owner.eject_pack_pending = True

    async def on_eject_pack_check(self, ctx, owner) -> None:
        eng = ctx.engine
        if not getattr(owner, "eject_pack_pending", False) or owner.fainted or owner.side is None or not owner.side.alive_bench():
            return
        if eng._pending_pivot is not None:                           # Showdown `onUseItem`: false if any active already has a `switchFlag`
            return
        owner.eject_pack_pending = False
        lose_held_item(owner, ctx)
        eng.log.append(f"**{owner.name}** is switched out with the Eject Pack!")
        eng._pending_pivot = {"user": owner, "kind": "switch"}


@register_item("Eject Button")
class EjectButton(HeldItem):
    consumable = True
    async def on_after_move_secondary(self, ctx, owner) -> None:
        from ..move import MoveCategory
        eng = ctx.engine
        if (owner not in ctx.targets or owner.fainted or owner.side is None or ctx.move.category is MoveCategory.STATUS or getattr(ctx, "hit_substitute", False)
                or not owner.side.alive_bench()):
            return
        lose_held_item(owner, ctx)
        ctx.log(f"**{owner.name}** is switched out with the Eject Button!")
        # WINS over the attacker's own pending pivot (Showdown `source.switchFlag = false`): verified vs U-turn.
        eng._pending_pivot = {"user": owner, "kind": "switch"}


# -- [ADDITIVE / Track C] --------------------------------------------------
def _crit_boost_item(item_name: str):
    """Scope Lens / Razor Claw: +1 crit-rate stage. Old code: `crit_rate +=
    1` when `attacker_state.effective_item() in ("Scope Lens", "Razor
    Claw")` (apply_move_full.txt:1907-1908 in this session's extraction)."""
    @register_item(item_name)
    class _CritBoostItem(HeldItem):
        async def on_modify_crit_stage(self, ctx, owner, stage: int) -> int | None:
            if owner is not ctx.user:
                return None
            return stage + 1
    _CritBoostItem.__name__ = item_name.replace(" ", "") + "Item"
    return _CritBoostItem


ScopeLens = _crit_boost_item("Scope Lens")
RazorClaw = _crit_boost_item("Razor Claw")


@register_item("Safety Goggles")
class SafetyGoggles(HeldItem):
    """Blocks powder moves outright and (see field.py's weather_chip)
    Sandstorm/Hail chip damage. A Gen 6+ item -- the powder-immunity
    MECHANISM it uses (MoveFlag.POWDER) only actually matters starting
    Gen 6 anyway, since that's also when the game started checking powder
    immunity at all (Pokemon Showdown: sim/battle-actions.ts's
    `this.battle.gen >= 6 && move.flags['powder']`), so no separate
    generation gate is needed here specifically."""
    async def on_try_hit(self, ctx, owner) -> bool | None:
        from ..move import MoveFlag
        if owner not in ctx.targets or ctx.user is owner:
            return None
        if not ctx.move.has_flag(MoveFlag.POWDER):
            return None
        ctx.log(f"**{owner.name}**'s Safety Goggles protected it from {ctx.move.spec.display_name}!")
        return True

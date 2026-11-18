"""Status moves that were unimplemented (plain BasicMoves that silently did nothing) or implemented as an unconditional version of a conditional move. Found by sweeping
EVERY status move through real Showdown (tests/t_status_sweep.py). Ported from Showdown's data/moves.ts and its per-generation mods.

Wish, Power Trick, Guard Split, Power Split, Camouflage, Conversion, Reflect Type, Forest's Curse, Trick-or-Treat, Ingrain, Aqua Ring, Magnet Rise,
and the CONDITIONAL ones: Flower Shield / Rototiller (grounded Grass types only), Magnetic Flux (Plus / Minus only), Venom Drench (poisoned targets only), Aromatic Mist (an ALLY).
"""
from __future__ import annotations
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..move import Move
from ..context import MoveContext
from ..registry import register_move
from ..volatile import Volatile
from ..battler import _Ingrain, _MagnetRise, _PowerTrickMarker, _PowerSplitOverride
from .field_effects import _FieldMove

if TYPE_CHECKING:
    from ..battler import Battler


def _failed(ctx) -> None:
    ctx.log("But it failed!")


def _can_set_type(mon: "Battler", ctx) -> bool:
    """Showdown `Pokemon.setType`: Arceus / Silvally never change type (Gen 5+; Gen 4: Multitype)."""
    name = mon.identity.species_name.lower()
    if ctx.ruleset.number >= 5 and (name.startswith("arceus") or name.startswith("silvally")):
        return False
    if ctx.ruleset.number == 4 and mon.effective_ability() == "multitype":
        return False
    return True


def _move_type(ctx, slug: str) -> str:
    return ctx.engine.moves.get(slug).spec.type_name


def _alive_allies(ctx, user) -> list:
    side = ctx.battle.field.side_of(user)
    return [b for b in side.active if b is not None and not b.fainted and b is not user]


# -- Wish ----------------------------------------------------------------------------------------------------------------------------------------------

@dataclass
class WishTracker(Volatile):
    """One per SIDE (a side effect): `pending` maps slot -> [turns left, hp to restore]. Showdown's `Wish` slot condition: `hp = source.maxhp / 2` taken when it is made,
    healed to WHOEVER is in that slot at the end of the NEXT turn (Gen 5+ order 4, i.e. before Leftovers; Gen 4 duration 2 / order 7)."""
    slug: str = "wish"
    pending: dict = None
    residual_order = (4, 0)

    def __post_init__(self):
        if self.pending is None:
            self.pending = {}

    async def on_end_turn(self, ctx, owner) -> None:
        for slot in list(self.pending):
            self.pending[slot][0] -= 1
            if self.pending[slot][0] > 0:
                continue
            _, hp = self.pending.pop(slot)
            mon = owner.active[slot] if slot < len(owner.active) else None
            if mon is not None and not mon.fainted:
                healed = mon.heal(hp)
                if healed:
                    ctx.log(f"The wish came true for **{mon.name}**! (+**{healed:,}** HP)")


@register_move("wish")
class WishMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        user = ctx.user
        side = ctx.battle.field.side_of(user)
        slot = side.active.index(user)
        tracker = side.get(WishTracker)
        if tracker is None:
            tracker = WishTracker()
            side.add(tracker, ctx.engine.events)
        if slot in tracker.pending:
            _failed(ctx)                                      # a Wish is already pending for this slot (`addSlotCondition` refuses)
            return
        tracker.pending[slot] = [2, user.vitals.max_hp // 2]
        ctx.log(f"**{user.name}** made a wish!")


# -- Power Trick / Guard Split / Power Split ------------------------------------------------------------------------------------------------------------

def _swap_atk_def_overrides(mon) -> None:
    ov = mon.get(_PowerSplitOverride)
    if ov is not None:
        a, d = ov.values.pop("atk", None), ov.values.pop("def", None)
        if a is not None:
            ov.values["def"] = a
        if d is not None:
            ov.values["atk"] = d


@register_move("power-trick")
class PowerTrickMove(Move):
    """Swaps the user's Attack and Defense stats; using it again swaps them back (Showdown `powertrick` volatile: `onStart` / `onRestart` both swap `storedStats`)."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        user = ctx.user
        if user.get(_PowerTrickMarker) is not None:
            user.remove(_PowerTrickMarker, ctx.engine.events)
        else:
            user.add(_PowerTrickMarker(), ctx.engine.events)
        _swap_atk_def_overrides(user)
        ctx.log(f"**{user.name}** switched its Attack and Defense!")


def _split(stats: tuple[str, ...], label: str):
    class _Split(Move):
        async def on_hit(self, ctx: MoveContext, target) -> None:
            user = ctx.user
            for key in stats:
                # the STORED stat: base+IV+EV+nature with Power Trick / earlier splits / Transform already applied, no stages (Showdown `storedStats`)
                avg = (int(user._raw_stat(key)) + int(target._raw_stat(key))) // 2
                for mon in (user, target):
                    ov = mon.get(_PowerSplitOverride)
                    if ov is None:
                        ov = _PowerSplitOverride()
                        mon.add(ov, ctx.engine.events)
                    # `_raw_stat` swaps atk/def BEFORE looking an override up when Power Trick is active, so store under the key it will actually read
                    k = {"atk": "def", "def": "atk"}.get(key, key) if mon.get(_PowerTrickMarker) is not None else key
                    ov.values[k] = avg
            ctx.log(f"**{user.name}** shared its {label} with the target!")
    return _Split


GuardSplitMove = register_move("guard-split")(_split(("def", "spd"), "guard"))
PowerSplitMove = register_move("power-split")(_split(("atk", "spa"), "power"))


# -- type changes -----------------------------------------------------------------------------------------------------------------------------------------

@register_move("camouflage")
class CamouflageMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        user, gen = ctx.user, ctx.ruleset.number
        if gen >= 6:                                          # by terrain (Showdown base move; Gen 6-7 use it too)
            new = {"electric": "Electric", "grassy": "Grass", "misty": "Fairy", "psychic": "Psychic"}.get(ctx.field.terrain, "Normal")
            if user.effective_types() == [new] or not _can_set_type(user, ctx):
                _failed(ctx); return
        elif gen == 5:                                        # Showdown's Gen 5 mod: always Ground, no "already that type" check
            new = "Ground"
            if not _can_set_type(user, ctx):
                _failed(ctx); return
        else:                                                 # Gen 3-4: Normal, fails if already Normal
            new = "Normal"
            if user.effective_types().__contains__("Normal") or not _can_set_type(user, ctx):
                _failed(ctx); return
        user.type_override = [new]
        ctx.log(f"**{user.name}** transformed into the {new} type!")


@register_move("conversion")
class ConversionMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        user, gen = ctx.user, ctx.ruleset.number
        slugs = [s.move_slug for s in user.moves.slots]
        if gen >= 6:                                          # the type of the FIRST move slot
            new = _move_type(ctx, slugs[0])
            if new in user.effective_types() or not _can_set_type(user, ctx):
                _failed(ctx); return
        else:                                                 # Gens 3-5: a random type among the moves' types the user doesn't already have
            banned = {3: {"curse"}, 4: {"conversion", "curse"}, 5: {"conversion"}}[max(3, gen)]
            options = [t for t in (_move_type(ctx, s) for s in slugs if s not in banned) if t not in user.effective_types()]
            if not options or not _can_set_type(user, ctx):
                _failed(ctx); return
            new = random.choice(options)
        user.type_override = [new]
        ctx.log(f"**{user.name}** transformed into the {new} type!")


@register_move("reflect-type")
class ReflectTypeMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        user = ctx.user
        types = [t for t in target.effective_types() if t != "???"]
        if not _can_set_type(user, ctx) or not types:
            _failed(ctx); return
        user.type_override = list(types)
        ctx.log(f"**{user.name}**'s type changed to match **{target.name}**'s!")


def _add_type_move(slug: str, new: str):
    @register_move(slug)
    class _AddType(Move):
        async def on_hit(self, ctx: MoveContext, target) -> None:
            types = list(target.effective_types())
            if new in types:
                _failed(ctx); return
            target.type_override = types + [new]              # Showdown `addType`: a third type, cleared on switch like any `setType`
            ctx.log(f"{new} type was added to **{target.name}**!")
    _AddType.__name__ = slug.replace("-", " ").title().replace(" ", "") + "Move"
    return _AddType


ForestsCurseMove = _add_type_move("forests-curse", "Grass")
TrickOrTreatMove = _add_type_move("trick-or-treat", "Ghost")


# -- conditional field moves -----------------------------------------------------------------------------------------------------------------------------

class _GrassFieldMove(_FieldMove):
    """Flower Shield / Rototiller: every ACTIVE Grass-type (Rototiller: only a grounded one) is boosted; if there are none the move fails."""
    boosts: tuple = ()
    needs_grounded: bool = False

    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..stats import apply_stat_change
        targets = [b for b in ctx.battle.field.all_battlers()
                   if not b.fainted and "Grass" in b.effective_types() and (not self.needs_grounded or b.is_grounded(field=ctx.field))]
        if not targets:
            _failed(ctx); return
        for b in targets:
            for stat, delta in self.boosts:
                await apply_stat_change(ctx, b, stat, delta)


@register_move("flower-shield")
class FlowerShieldMove(_GrassFieldMove):
    boosts = (("def", 1),)


@register_move("rototiller")
class RototillerMove(_GrassFieldMove):
    boosts = (("atk", 1), ("spa", 1))
    needs_grounded = True


@register_move("magnetic-flux")
class MagneticFluxMove(_FieldMove):
    """The user's side: only Plus / Minus holders are boosted (Defense and Sp. Def); nobody -> the move fails."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..stats import apply_stat_change
        side = ctx.battle.field.side_of(ctx.user)
        targets = [b for b in side.active if b is not None and not b.fainted and b.effective_ability() in ("plus", "minus")]
        if not targets:
            _failed(ctx); return
        for b in targets:
            await apply_stat_change(ctx, b, "def", 1)
            await apply_stat_change(ctx, b, "spd", 1)


@register_move("venom-drench")
class VenomDrenchMove(Move):
    """Lowers Attack, Sp. Atk and Speed of each target that is POISONED (poison or toxic); fails against the others."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..stats import apply_stat_change
        if target.vitals.status not in ("poison", "bad_poison"):
            _failed(ctx); return
        for stat in ("atk", "spa", "spe"):
            await apply_stat_change(ctx, target, stat, -1)


@register_move("aromatic-mist")
class AromaticMistMove(Move):
    """+1 Sp. Def to an ALLY (`adjacentAlly`): there is no target in Singles, so it fails (the data's `ally` target used to resolve to the user)."""
    async def get_targets(self, ctx: MoveContext) -> list:
        return _alive_allies(ctx, ctx.user)[:1]

    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..stats import apply_stat_change
        await apply_stat_change(ctx, target, "spd", 1)


# -- Ingrain / Aqua Ring / Magnet Rise ---------------------------------------------------------------------------------------------------------------------

async def _residual_heal(ctx, owner, why: str) -> None:
    if owner.fainted:
        return
    amount = max(1, owner.vitals.max_hp // 16)
    amount = await owner.consult_reduce("on_modify_incoming_drain", ctx, amount)      # Big Root also boosts Ingrain / Aqua Ring
    healed = owner.heal(amount)
    if healed:
        ctx.log(f"**{owner.name}** {why} (+**{healed:,}** HP)")


@dataclass
class IngrainTick(Volatile):
    """Ingrain: heals 1/16 of max HP each turn (after Aqua Ring), can't switch out, can't be dragged out (the `_Ingrain` marker), and is grounded."""
    slug: str = "ingrain"
    traps_switching: bool = True
    residual_order = (7, 0)

    async def on_end_turn(self, ctx, owner) -> None:
        await _residual_heal(ctx, owner, "absorbed nutrients with its roots!")


@dataclass
class AquaRing(Volatile):
    slug: str = "aqua-ring"
    residual_order = (6, 0)

    async def on_end_turn(self, ctx, owner) -> None:
        await _residual_heal(ctx, owner, "restored a little HP using Aqua Ring!")


@dataclass
class MagnetRiseTick(Volatile):
    slug: str = "magnet-rise"
    turns_remaining: int = 5
    residual_order = (18, 0)

    async def on_end_turn(self, ctx, owner) -> None:
        self.turns_remaining -= 1
        if self.turns_remaining <= 0:
            owner.remove(MagnetRiseTick, ctx.engine.events)
            owner.remove(_MagnetRise, ctx.engine.events)
            if not owner.fainted:
                ctx.log(f"**{owner.name}**'s electromagnetism wore off!")


@register_move("ingrain")
class IngrainMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        user = ctx.user
        if user.has(IngrainTick):
            _failed(ctx); return
        user.add(IngrainTick(), ctx.engine.events)
        user.add(_Ingrain(), ctx.engine.events)
        ctx.log(f"**{user.name}** planted its roots!")


@register_move("aqua-ring")
class AquaRingMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        user = ctx.user
        if user.has(AquaRing):
            _failed(ctx); return
        user.add(AquaRing(), ctx.engine.events)
        ctx.log(f"**{user.name}** surrounded itself with a veil of water!")


@register_move("magnet-rise")
class MagnetRiseMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..volatile import SmackedDown
        user = ctx.user
        if user.has(MagnetRiseTick) or user.get(_Ingrain) is not None or user.has(SmackedDown) or ctx.field.gravity:
            _failed(ctx); return
        user.add(MagnetRiseTick(), ctx.engine.events)
        user.add(_MagnetRise(), ctx.engine.events)
        ctx.log(f"**{user.name}** levitated with electromagnetism!")


@register_move("captivate")
class CaptivateMove(Move):
    """-2 Sp. Atk, but only between OPPOSITE genders (Showdown `onTryImmunity`); same-gender or genderless targets are unaffected. (There was no gender check at all.)"""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..stats import apply_stat_change
        pair = {ctx.user.identity.gender, target.identity.gender}
        if pair != {"male", "female"}:
            _failed(ctx); return
        await apply_stat_change(ctx, target, "spa", -2)


from ..move import BasicMove


@register_move("tri-attack")
class TriAttackMove(BasicMove):
    """20%: burn, paralysis or freeze, one of the three at random (Showdown `secondary.onHit`: `sample(['brn', 'par', 'frz'])`). The data had NO ailment for it, so it never did anything."""
    async def on_secondary_effect(self, ctx: MoveContext, target) -> None:
        await super().on_secondary_effect(ctx, target)                       # King's Rock / Razor Fang flinch etc.
        if await ctx.events.first("on_suppress_secondary_effect", ctx):       # Shield Dust / Sheer Force
            return
        from ..status import inflict_status
        for _ in range(getattr(ctx, "hits_on_target", 1)):
            chance = await ctx.events.reduce("on_modify_secondary_chance", ctx, 20)      # Serene Grace
            if chance and random.randint(1, 100) <= chance:
                await inflict_status(ctx, target, random.choice(["burn", "paralysis", "freeze"]))


@register_move("acupressure")
class AcupressureMove(Move):
    """+2 to ONE random stat (Attack, Defense, Sp. Atk, Sp. Def or Speed) that isn't already at +6; fails if all five are maxed (Showdown: `sample` over `target.boosts`, excluding accuracy / evasion)."""
    async def get_targets(self, ctx: MoveContext) -> list:
        return [ctx.user]

    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..stats import apply_stat_change
        options = [k for k in ("atk", "def", "spa", "spd", "spe") if target.stages.get(k, 0) < 6]
        if not options:
            _failed(ctx); return
        await apply_stat_change(ctx, target, random.choice(options), 2)


# -- stat-stage swaps / copies ------------------------------------------------------------------------------------------------------------------------

_ALL_STAGES = ("atk", "def", "spa", "spd", "spe", "acc", "eva")


def _swap_move(slug: str, keys: tuple[str, ...], label: str):
    @register_move(slug)
    class _Swap(Move):
        async def on_hit(self, ctx: MoveContext, target) -> None:
            user = ctx.user
            for k in keys:
                user.stages[k], target.stages[k] = target.stages.get(k, 0), user.stages.get(k, 0)
            ctx.log(f"**{user.name}** switched {label} with its target!")
    _Swap.__name__ = slug.replace("-", " ").title().replace(" ", "") + "Move"
    return _Swap


GuardSwapMove = _swap_move("guard-swap", ("def", "spd"), "all changes to its defensive stats")
PowerSwapMove = _swap_move("power-swap", ("atk", "spa"), "all changes to its offensive stats")
HeartSwapMove = _swap_move("heart-swap", _ALL_STAGES, "stat changes")


@register_move("psych-up")
class PsychUpMove(Move):
    """Copies every stat stage of the target onto the user, and the critical-hit volatile (Focus Energy)."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..volatile import FocusEnergy
        user = ctx.user
        for k in _ALL_STAGES:
            user.stages[k] = target.stages.get(k, 0)
        if ctx.ruleset.number >= 6:
            user.remove(FocusEnergy, ctx.engine.events)
            if target.has(FocusEnergy):
                user.add(FocusEnergy(), ctx.engine.events)
        ctx.log(f"**{user.name}** copied **{target.name}**'s stat changes!")


@register_move("topsy-turvy")
class TopsyTurvyMove(Move):
    """Inverts every stat stage of the target; fails if it has none."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        changed = False
        for k in _ALL_STAGES:
            v = target.stages.get(k, 0)
            if v:
                target.stages[k] = -v
                changed = True
        ctx.log(f"All of **{target.name}**'s stat changes were inverted!" if changed else "But it failed!")


# -- Conversion 2 ---------------------------------------------------------------------------------------------------------------------------------------

_TYPE_ORDER = tuple(sorted(("Normal", "Fighting", "Flying", "Poison", "Ground", "Rock", "Bug", "Ghost", "Steel", "Fire", "Water", "Grass", "Electric", "Psychic", "Ice", "Dragon", "Dark", "Fairy")))   # Showdown iterates its (alphabetical) typechart


_TYPE_ORDER_OLD = ("Normal", "Fighting", "Flying", "Poison", "Ground", "Rock", "Bug", "Ghost", "Steel", "Fire", "Water", "Grass", "Electric", "Psychic", "Ice", "Dragon", "Dark", "Fairy")


@register_move("conversion-2")
class Conversion2Move(Move):
    """Becomes a type that RESISTS (or is immune to) the type of the move the target last used (Showdown `lastMoveUsed`); fails with no last move or no candidate."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..types import type_effectiveness
        user = ctx.user
        last = getattr(target, "last_move_slug", None)
        if not last or not _can_set_type(user, ctx):
            _failed(ctx); return
        attack = _move_type(ctx, last)
        mine = user.effective_types()
        from .call_moves import _data
        # Showdown iterates that generation's `types.names()` (data/call_move_data.json `type_order`); Gen 3 doesn't skip the user's own types
        options = [t for t in _data(ctx.ruleset.number)["type_order"] if (t not in mine or ctx.ruleset.number == 3) and type_effectiveness(attack, [t], ruleset=ctx.ruleset) < 1.0]
        if not options:
            _failed(ctx); return
        new = random.choice(options)
        user.type_override = [new]
        ctx.log(f"**{user.name}** transformed into the {new} type!")


# -- Imprison -------------------------------------------------------------------------------------------------------------------------------------------

@dataclass
class Imprison(Volatile):
    """The holder's FOES can't use any move the holder also knows (checked in `engine._can_act`); lasts while the holder stays in."""
    slug: str = "imprison"


@register_move("imprison")
class ImprisonMove(Move):
    async def get_targets(self, ctx: MoveContext) -> list:
        return [ctx.user]

    async def on_hit(self, ctx: MoveContext, target) -> None:
        user = ctx.user
        if user.has(Imprison):
            _failed(ctx); return
        user.add(Imprison(), ctx.engine.events)
        ctx.log(f"**{user.name}** sealed any moves its target shares with it!")


# -- Defog ------------------------------------------------------------------------------------------------------------------------------------------------

@register_move("defog")
class DefogMove(Move):
    """Showdown `defog`: -1 evasion (unless a Substitute is in the way), clears the TARGET's side of screens / Safeguard / Mist / hazards, the USER's side of hazards (Gen 6+), and (Gen 8+) the terrain.
    Gens 4-5 only clear the target's own side (reflect, light screen, safeguard, mist, spikes, toxic spikes, stealth rock)."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..stats import apply_stat_change
        from ..side_conditions import (Reflect, LightScreen, AuroraVeil, Safeguard, Mist, StealthRock, Spikes, ToxicSpikes, StickyWeb)
        from ..volatile import Substitute
        from ..abilities.field_effects import Terrain
        gen, user, events = ctx.ruleset.number, ctx.user, ctx.engine.events
        success = False
        if not target.has(Substitute) or (gen >= 6 and await ctx.events.first("on_bypass_substitute", ctx)):
            before = target.stages.get("eva", 0)
            await apply_stat_change(ctx, target, "eva", -1)
            success = target.stages.get("eva", 0) != before
        hazards = (StealthRock, Spikes, ToxicSpikes) + ((StickyWeb,) if gen >= 6 else ())
        field = ctx.battle.field
        tside, uside = field.side_of(target), field.side_of(user)
        for cond in (Reflect, LightScreen, AuroraVeil, Safeguard, Mist) + hazards:
            if tside.remove(cond, events) is not None and cond in hazards:
                ctx.log(f"The {cond.__name__} around **{target.name}**'s side were blown away!")
                success = True
        if gen >= 6:
            for cond in hazards:
                if uside.remove(cond, events) is not None:
                    ctx.log(f"The {cond.__name__} around **{user.name}**'s side were blown away!")
                    success = True
        if gen >= 8 and ctx.field.remove(Terrain, events) is not None:
            ctx.log("The terrain disappeared!")
        if not success:
            _failed(ctx)


# -- Embargo / Telekinesis ----------------------------------------------------------------------------------------------------------------------------------

@dataclass
class EmbargoTick(Volatile):
    slug: str = "embargo"
    turns_remaining: int = 5
    residual_order = (21, 0)

    async def on_end_turn(self, ctx, owner) -> None:
        from ..battler import _EmbargoMarker
        self.turns_remaining -= 1
        marker = owner.get(_EmbargoMarker)
        if marker is not None:
            marker.turns_remaining = self.turns_remaining
        if self.turns_remaining <= 0:
            owner.remove(EmbargoTick, ctx.engine.events)
            owner.remove(_EmbargoMarker, ctx.engine.events)
            if not owner.fainted:
                ctx.log(f"**{owner.name}** can use items again!")


@register_move("embargo")
class EmbargoMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..battler import _EmbargoMarker
        if target.has(EmbargoTick):
            _failed(ctx); return
        target.add(EmbargoTick(), ctx.engine.events)
        target.add(_EmbargoMarker(), ctx.engine.events)
        ctx.log(f"**{target.name}** can't use items anymore!")


@dataclass
class TelekinesisTick(Volatile):
    slug: str = "telekinesis"
    turns_remaining: int = 3
    residual_order = (19, 0)

    async def on_end_turn(self, ctx, owner) -> None:
        from ..battler import _Telekinesis
        self.turns_remaining -= 1
        if self.turns_remaining <= 0:
            owner.remove(TelekinesisTick, ctx.engine.events)
            owner.remove(_Telekinesis, ctx.engine.events)
            if not owner.fainted:
                ctx.log(f"**{owner.name}** was freed from the telekinesis!")


@register_move("telekinesis")
class TelekinesisMove(Move):
    """The target floats for 3 turns: Ground moves miss it and every non-OHKO move hits it (`check_accuracy`). Fails against Diglett / Dugtrio / Sandygast / Palossand / Mega Gengar, an Ingrained or Smacked Down target."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..battler import _Telekinesis
        from ..volatile import SmackedDown
        name = target.identity.species_name.lower()
        if (ctx.field.gravity or target.has(TelekinesisTick) or any(name.startswith(n) for n in ("diglett", "dugtrio", "sandygast", "palossand", "gengar-mega"))
                or target.has(SmackedDown) or target.get(_Ingrain) is not None):
            _failed(ctx); return
        target.add(TelekinesisTick(), ctx.engine.events)
        target.add(_Telekinesis(), ctx.engine.events)
        ctx.log(f"**{target.name}** was hurled into the air!")


# -- Growth ---------------------------------------------------------------------------------------------------------------------------------------------

@register_move("growth")
class GrowthMove(Move):
    """Gen 5+: +1 Attack and Sp. Atk, +2 each in harsh sunlight; Gens 3-4: +1 Sp. Atk only (Showdown `onModifyMove`)."""
    async def get_targets(self, ctx: MoveContext) -> list:
        return [ctx.user]

    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..stats import apply_stat_change
        from ..abilities._util import weather_suppressed
        gen = ctx.ruleset.number
        if gen < 5:
            await apply_stat_change(ctx, ctx.user, "spa", 1)
            return
        amount = 2 if ctx.field.weather in ("sun", "harsh-sun") and not weather_suppressed(ctx.field) else 1
        await apply_stat_change(ctx, ctx.user, "atk", amount)
        await apply_stat_change(ctx, ctx.user, "spa", amount)


# Gens 3-4 residual order (Showdown data/mods/gen4)
WishTracker.residual_order_gen4 = (7, 0)
IngrainTick.residual_order_gen4 = (10, 1)
AquaRing.residual_order_gen4 = (10, 2)
MagnetRiseTick.residual_order_gen4 = (10, 16)
EmbargoTick.residual_order_gen4 = (10, 18)


@register_move("secret-power")
class SecretPowerMove(BasicMove):
    """30% secondary that depends on the terrain (Showdown `onModifyMove`): none / Electric = paralysis (the data's default), Grassy = sleep, Misty = -1 Sp. Atk, Psychic = -1 Speed.
    Gen 5 (accuracy -1) comes from the per-generation data override."""
    async def on_secondary_effect(self, ctx: MoveContext, target) -> None:
        terrain = ctx.field.terrain
        if terrain not in ("grassy", "misty", "psychic") or ctx.ruleset.number < 6:
            await super().on_secondary_effect(ctx, target)
            return
        if await ctx.events.first("on_suppress_secondary_effect", ctx):
            return
        chance = await ctx.events.reduce("on_modify_secondary_chance", ctx, 30)
        if random.randint(1, 100) > chance:
            return
        from ..stats import apply_stat_change
        if terrain == "grassy":
            from ..status import inflict_status
            await inflict_status(ctx, target, "sleep")
        elif terrain == "misty":
            await apply_stat_change(ctx, target, "spa", -1)
        else:
            await apply_stat_change(ctx, target, "spe", -1)


# -- Follow Me / Rage Powder (redirection) ------------------------------------------------------------------------------------------------------------------

@dataclass
class FollowMeActive(Volatile):
    """One-turn marker (Showdown `followme` / `ragepowder` volatile): the FOES' single-target moves are redirected to the holder (`engine._redirect`). Rage Powder is a powder: Grass types,
    Overcoat and Safety Goggles ignore it."""
    slug: str = "follow-me"
    powder: bool = False


def _redirector(slug: str, powder: bool):
    @register_move(slug)
    class _Redirector(Move):
        async def get_targets(self, ctx: MoveContext) -> list:
            return [ctx.user]

        async def on_hit(self, ctx: MoveContext, target) -> None:
            ctx.user.add(FollowMeActive(powder=powder), ctx.engine.events)
            ctx.log(f"**{ctx.user.name}** became the center of attention!")
    _Redirector.__name__ = slug.replace("-", " ").title().replace(" ", "") + "Move"
    return _Redirector


FollowMeMove = _redirector("follow-me", False)
RagePowderMove = _redirector("rage-powder", True)


# -- Doubles-only moves ------------------------------------------------------------------------------------------------------------------------------------------

@dataclass
class HelpingHandActive(Volatile):
    """One turn: the holder's moves have x1.5 power per Helping Hand received (Showdown `helpinghand`: `multiplier *= 1.5` on each restart)."""
    slug: str = "helping-hand"
    multiplier: float = 1.5

    async def on_modify_power(self, ctx, owner, power: int) -> int | None:
        if owner is not ctx.user:
            return None
        from ..fixed import modify_raw
        return modify_raw(power, int(self.multiplier * 4096))


@register_move("helping-hand")
class HelpingHandMove(Move):
    async def get_targets(self, ctx: MoveContext) -> list:
        return _alive_allies(ctx, ctx.user)[:1]

    async def on_hit(self, ctx: MoveContext, target) -> None:
        existing = target.get(HelpingHandActive)
        if existing is not None:
            existing.multiplier *= 1.5
        else:
            target.add(HelpingHandActive(), ctx.engine.events)
        ctx.log(f"**{ctx.user.name}** is ready to help **{target.name}**!")


@register_move("torment")
class TormentMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..volatile import Torment
        from ..abilities._util import ally_blocks
        if await ally_blocks(ctx, target, "volatile:torment"):                  # Aroma Veil
            return
        if target.has(Torment):
            _failed(ctx); return
        target.add(Torment(), ctx.engine.events)
        ctx.log(f"**{target.name}** was subjected to torment!")


@register_move("howl")
class HowlMove(BasicMove):
    """+1 Attack for the user only before Gen 8 (Showdown gen7 mod `target: "self"`); from Gen 8 also its allies (the data's target)."""
    async def get_targets(self, ctx: MoveContext) -> list:
        if ctx.ruleset.number < 8:
            return [ctx.user]
        return await super().get_targets(ctx)

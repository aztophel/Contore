"""Moves whose power, type, damage or effectiveness depends on battle state --
ported from Pokemon Showdown's data/moves.ts (`basePowerCallback`,
`onBasePower`, `onModifyType`, `damageCallback`, `onEffectiveness`,
`ignoreDefensive`/`ignoreEvasion`, `hasCrashDamage`). Same pattern as
variable_power.py: each is a small override of an existing `Move` hook, no
engine-level special-casing by move name.

All of these were plain `BasicMove`s before: Weather Ball never changed
type, Acrobatics/Avalanche/Revenge/Brine/Stored Power never scaled, Hidden
Power was always Normal, Jump Kicks never crashed, and Smelling Salts / Wake-Up
Slap never cured anything.

NOT here (still on the audit list, see MOVE_AUDIT.md): Rollout / Ice Ball
(lock-in moves, belong with Outrage/Thrash), Pursuit (needs a pre-switch
interception hook), Beat Up, Natural Gift (needs a berry table), Round
(doubles-only), Water Shuriken (only Greninja-Ash differs).
"""
from __future__ import annotations

import random

from ..move import BasicMove, Move, MoveCategory
from ..context import MoveContext
from ..registry import register_move
from ..types import single_type_effectiveness
from ..volatile import FuryCutter
from .fixed_damage import apply_fixed_damage, _check_type_immunity


def _positive_boosts(battler) -> int:
    """Showdown `positiveBoosts()`: total of the positive stat stages
    (accuracy and evasion included)."""
    return sum(v for v in battler.stages.values() if v > 0)


def _double_when(slug: str, predicate, *, cure: str | None = None):
    """x2 power while `predicate(ctx, target)` holds; optionally also cures
    the target's `cure` status AFTER the hit (Smelling Salts / Wake-Up Slap:
    the power check sees the status, then the hit removes it)."""
    @register_move(slug)
    class _DoubleWhen(BasicMove):
        async def on_base_power(self, ctx: MoveContext, target) -> int | None:
            return self.spec.base_power * 2 if predicate(ctx, target) else None

        if cure is not None:
            async def on_hit(self, ctx: MoveContext, target) -> None:
                await super().on_hit(ctx, target)
                if not ctx.result.failed and target.vitals.status == cure and not target.fainted:
                    target.vitals.status, target.vitals.status_turns, target.vitals.toxic_counter = None, 0, 0
    _DoubleWhen.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _DoubleWhen


def _hit_by_target_this_turn(ctx: MoveContext, target) -> bool:
    user = ctx.user
    return (getattr(user, "last_damage_from", None) is target
            and getattr(user, "last_damage_turn", None) == ctx.engine.turn_number)


AcrobaticsMove = _double_when("acrobatics", lambda ctx, t: not ctx.user.identity.held_item)
AvalancheMove = _double_when("avalanche", _hit_by_target_this_turn)
RevengeMove = _double_when("revenge", _hit_by_target_this_turn)
BrineMove = _double_when("brine", lambda ctx, t: t.vitals.hp * 2 <= t.vitals.max_hp)
SmellingSaltsMove = _double_when("smelling-salts", lambda ctx, t: t.vitals.status == "paralysis", cure="paralysis")
WakeUpSlapMove = _double_when(
    "wake-up-slap", lambda ctx, t: t.vitals.status == "sleep" or t.effective_ability() == "comatose", cure="sleep")
FusionBoltMove = _double_when(
    "fusion-bolt", lambda ctx, t: ctx.engine.last_successful_move_this_turn == "fusion-flare")
FusionFlareMove = _double_when(
    "fusion-flare", lambda ctx, t: ctx.engine.last_successful_move_this_turn == "fusion-bolt")


@register_move("punishment")
class PunishmentMove(BasicMove):
    """60 + 20 per positive stage on the TARGET, capped at 200."""
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        return min(200, 60 + 20 * _positive_boosts(target))


@register_move("stored-power")
class StoredPowerMove(BasicMove):
    """20 + 20 per positive stage on the USER."""
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        return self.spec.base_power + 20 * _positive_boosts(ctx.user)


@register_move("trump-card")
class TrumpCardMove(BasicMove):
    """By PP left AFTER this use was paid for: 0 -> 200, 1 -> 80, 2 -> 60,
    3 -> 50, more -> 40."""
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        slot = next((s for s in ctx.user.moves.slots if s.move_slug == self.slug), None)
        if slot is None:
            return 40
        return {0: 200, 1: 80, 2: 60, 3: 50}.get(slot.pp, 40)


@register_move("echoed-voice")
class EchoedVoiceMove(BasicMove):
    """Showdown adds the `echoedvoice` pseudo-weather in `onTryMove`: the
    first use starts it at x1, a use on a later turn while it's still up
    raises it (max x5), and two users the same turn count once. It lapses
    after two turn-ends, so it needs back-to-back turns."""
    async def on_try_use(self, ctx: MoveContext) -> str | None:
        f = ctx.field
        if f.echoed_voice_turns == 0:
            f.echoed_voice_mult, f.echoed_voice_turns = 1, 2
        elif f.echoed_voice_turns != 2:
            f.echoed_voice_turns = 2
            f.echoed_voice_mult = min(5, f.echoed_voice_mult + 1)
        return None

    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        return self.spec.base_power * max(1, ctx.field.echoed_voice_mult)


@register_move("fury-cutter")
class FuryCutterMove(BasicMove):
    """x1 -> x2 -> x4 (40/80/160) on consecutive turns, via `FuryCutter`."""
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        v = ctx.user.get(FuryCutter)
        if v is None:
            v = FuryCutter()
            ctx.user.add(v, ctx.engine.events)
        else:
            if v.multiplier < 4:
                v.multiplier <<= 1
            v.turns_left = 2
        return max(1, min(160, self.spec.base_power * v.multiplier))


# -- type changers ------------------------------------------------------------

_WEATHER_BALL_TYPE = {"sun": "Fire", "harsh-sun": "Fire", "rain": "Water", "heavy-rain": "Water",
                      "sand": "Rock", "hail": "Ice", "snow": "Ice"}


@register_move("weather-ball")
class WeatherBallMove(BasicMove):
    """Takes the weather's type and doubles to 100 power in sun, rain, sand
    or hail (Cloud Nine/Air Lock suppress it: `get_weather()` already does)."""
    async def on_modify_type(self, ctx: MoveContext, target, current_type: str) -> str | None:
        return _WEATHER_BALL_TYPE.get(ctx.field.get_weather())

    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        return self.spec.base_power * 2 if ctx.field.get_weather() in _WEATHER_BALL_TYPE else None

    async def on_modify_category(self, ctx: MoveContext, target, category):
        # Gen 3 (Showdown data/mods/gen3/moves.ts): the category follows the weather's type -- Special for Fire / Water / Ice, but Sandstorm's Rock stays Physical.
        if ctx.ruleset.number == 3 and _WEATHER_BALL_TYPE.get(ctx.field.get_weather()) in ("Fire", "Water", "Ice"):
            return MoveCategory.SPECIAL
        return None


_HP_TYPES = ("Fighting", "Flying", "Poison", "Ground", "Rock", "Bug", "Ghost", "Steel",
             "Fire", "Water", "Grass", "Electric", "Psychic", "Ice", "Dragon", "Dark")
# order the six stats feed the formula in (Showdown pokemon.ts `hpType`)
_HP_STAT_ORDER = ("hp", "atk", "def", "spe", "spa", "spd")


def hidden_power_type(ivs: dict[str, int]) -> str:
    """Type from the low bit of each IV (Gen 3+ formula)."""
    n = sum((ivs.get(k, 31) & 1) << i for i, k in enumerate(_HP_STAT_ORDER))
    return _HP_TYPES[n * 15 // 63]


def hidden_power_bp(ivs: dict[str, int]) -> int:
    """Gen 3-5 power from the SECOND-lowest IV bits (Gen 6+ is a flat 60)."""
    n = sum(((ivs.get(k, 31) >> 1) & 1) << i for i, k in enumerate(_HP_STAT_ORDER))
    return n * 40 // 63 + 30


@register_move("hidden-power")
class HiddenPowerMove(BasicMove):
    async def on_modify_type(self, ctx: MoveContext, target, current_type: str) -> str | None:
        return hidden_power_type(ctx.user.identity.ivs)

    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        return hidden_power_bp(ctx.user.identity.ivs) if ctx.ruleset.number <= 5 else None


# Plates (Judgment) and Drives (Techno Blast), by held-item name.
_PLATES = {"Draco Plate": "Dragon", "Dread Plate": "Dark", "Earth Plate": "Ground", "Fist Plate": "Fighting",
           "Flame Plate": "Fire", "Icicle Plate": "Ice", "Insect Plate": "Bug", "Iron Plate": "Steel",
           "Meadow Plate": "Grass", "Mind Plate": "Psychic", "Pixie Plate": "Fairy", "Sky Plate": "Flying",
           "Splash Plate": "Water", "Spooky Plate": "Ghost", "Stone Plate": "Rock", "Toxic Plate": "Poison",
           "Zap Plate": "Electric"}
_DRIVES = {"Burn Drive": "Fire", "Chill Drive": "Ice", "Douse Drive": "Water", "Shock Drive": "Electric"}


def _item_typed_move(slug: str, table: dict[str, str]):
    """Type from the held item; an inert item (Klutz/Embargo/Magic Room) gives
    the move's normal type -- Showdown checks `ignoringItem()` first."""
    @register_move(slug)
    class _ItemTyped(BasicMove):
        async def on_modify_type(self, ctx: MoveContext, target, current_type: str) -> str | None:
            return table.get(ctx.user.effective_item())
    _ItemTyped.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _ItemTyped


JudgmentMove = _item_typed_move("judgment", _PLATES)
TechnoBlastMove = _item_typed_move("techno-blast", _DRIVES)


# -- fixed damage -------------------------------------------------------------

@register_move("psywave")
class PsywaveMove(Move):
    """Level x (50..150)% (Showdown's Gen 5+ roll, `random(50, 151)`); the
    older gens' different spreads are not modelled."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        if await _check_type_immunity(ctx, self, target):
            return
        await apply_fixed_damage(ctx, target, max(1, ctx.user.identity.level * random.randint(50, 150) // 100))


# -- ignoring boosts ------------------------------------------------------------

class _IgnoresTargetBoosts(BasicMove):
    """Chip-Away / Sacred Sword: the target's Def/SpD AND evasion stages are
    disregarded, up or down."""
    def ignores_defensive_boosts(self) -> bool:
        return True

    def ignores_evasion(self) -> bool:
        return True


@register_move("chip-away")
class ChipAwayMove(_IgnoresTargetBoosts):
    pass


@register_move("sacred-sword")
class SacredSwordMove(_IgnoresTargetBoosts):
    pass


# -- crash damage ----------------------------------------------------------------

class _CrashDamageMove(BasicMove):
    """Showdown `hasCrashDamage` + `onMoveFail`: if the move misses, is blocked or has no effect, the user takes half its max HP (Gen 5+). Gens 3-4 (data/mods/gen3|gen4/moves.ts) crash for half of the
    DAMAGE the move would have done, clamped to half the TARGET's max HP; only the IMMUNE-target case is ported (Gen 3: no crash at all; Gen 4: the would-be damage is taken as the target's max HP, so
    half of it) -- the Gens 3-4 crash after a plain miss / Protect still uses the Gen 5+ rule (the scenarios can't script misses: accuracy is pinned)."""
    async def on_after_move(self, ctx: MoveContext, connected: bool) -> None:
        user = ctx.user
        if connected or user.fainted:
            return
        gen = ctx.ruleset.number
        immune = bool(getattr(ctx, "hit_blocked", False)) and bool(ctx.targets)
        if gen == 3 and immune:
            return
        loss = max(1, user.vitals.max_hp // 2)
        if gen == 4 and immune:
            loss = max(1, ctx.targets[0].vitals.max_hp // 2)
        user.vitals.hp = max(0, user.vitals.hp - loss)
        ctx.log(f"**{user.name}** kept going and crashed!")
        if user.fainted:
            ctx.log(f"**{user.name}** fainted!")


@register_move("jump-kick")
class JumpKickMove(_CrashDamageMove):
    pass


@register_move("high-jump-kick")
class HighJumpKickMove(_CrashDamageMove):
    pass


# -- effectiveness overrides ---------------------------------------------------------

@register_move("freeze-dry")
class FreezeDryMove(BasicMove):
    """Super effective against Water (Showdown `onEffectiveness`), whatever
    Ice would normally do to it."""
    async def on_modify_type_effectiveness(self, ctx: MoveContext, target, eff: float) -> float | None:
        out = 1.0
        for t in target.effective_types():
            out *= 2.0 if t == "Water" else single_type_effectiveness("Ice", t, ruleset=ctx.ruleset)
        return out


@register_move("flying-press")
class FlyingPressMove(BasicMove):
    """Fighting AND Flying: effectiveness is the product of both types'."""
    async def on_modify_type_effectiveness(self, ctx: MoveContext, target, eff: float) -> float | None:
        out = 1.0
        for t in target.effective_types():
            out *= (single_type_effectiveness("Fighting", t, ruleset=ctx.ruleset)
                    * single_type_effectiveness("Flying", t, ruleset=ctx.ruleset))
        return out

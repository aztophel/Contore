"""Weather-reactive recovery/stat abilities plus the pure turn-counter
abilities (Speed Boost, Moody, Slow Start). Old code:
FighterState.apply_end_of_turn (contore.py:15718-15995) for end-of-turn
effects; FighterState.stat_value (15625-15680) for the stat-modifying half;
FighterState.weather_chip (15283-15295) for the weather-chip-immunity half
that Sand Veil/Sand Force/Sand Rush/Ice Body/Snow Cloak also grant (in
addition to their headline effect), routed through on_indirect_damage
alongside Magic Guard/Overcoat.
"""
from __future__ import annotations
import random

from ..fixed import modify
from ._util import attacker_breaks_mold
from ..fixed import modify, modify_raw, chain_mod
from ..registry import register_ability
from ..ability import Ability
from ._util import weather_suppressed, field_of
from .field_effects import get_weather, get_raw_weather


def _sun(field) -> bool:
    return get_weather(field) in ("sun", "harsh-sun")


def _rain(field) -> bool:
    return get_weather(field) in ("rain", "heavy-rain")


# -- end-of-turn weather recovery/damage ----------------------------------

@register_ability("dry-skin")
class DrySkin(Ability):
    """Weather recovery/damage (end of turn), x1.25 from Fire, and Water absorption. The Fire weakness used to be a
    x1.25 on DAMAGE; Showdown applies it to the attacker's BASE POWER (`onSourceBasePower`), which rounds differently
    (see `on_source_base_power`). The Water half was a documented gap and is now implemented."""
    async def on_source_base_power(self, ctx, owner, power: int) -> int | None:
        """Fire moves hit a Dry Skin holder at x1.25 (Showdown `onSourceBasePower`)."""
        if owner is not ctx.current_target or ctx.move_type != "Fire" or attacker_breaks_mold(ctx):
            return None
        return modify(power, 1.25)

    async def on_type_immunity(self, ctx, owner, type_name) -> bool | None:
        """Water is absorbed (heals 25%): the half this class used to document as a deliberate gap."""
        if type_name != "Water" or attacker_breaks_mold(ctx):
            return None
        if owner.vitals.hp < owner.vitals.max_hp:
            heal = max(1, owner.vitals.max_hp // 4)
            owner.vitals.hp = min(owner.vitals.max_hp, owner.vitals.hp + heal)
            ctx.log(f"Dry Skin restored **{owner.name}**'s HP! (+**{heal:,}**)")
        else:
            ctx.log(f"Dry Skin made **{owner.name}** immune to the attack!")
        return True

    async def on_end_turn(self, ctx, owner) -> None:
        field = ctx.field
        if weather_suppressed(field) or owner.fainted:
            return
        if _sun(field):
            dmg = max(1, owner.vitals.max_hp // 8)
            # LOCAL dispatch (owner's own attached effects), not the global
            # bus -- "does MY OWN Magic Guard/Poison Heal/etc. block MY OWN
            # indirect damage" is inherently self-referential. Every
            # existing on_indirect_damage implementation (Magic Guard,
            # Poison Heal, Sand Veil, Snow Cloak, Sand Force) is written
            # checking only `owner`'s own state with no cross-check against
            # who the "real" recipient is, which only makes sense under
            # local dispatch -- global dispatch would let ANY OTHER
            # Pokemon's Magic Guard anywhere in the battle block THIS
            # Pokemon's Dry Skin damage, which is wrong. Same fix as
            # moves/examples.py's on_type_immunity bug earlier this
            # session.
            dmg = await owner.consult_reduce("on_indirect_damage", ctx, dmg, "weather")
            if dmg > 0:
                owner.vitals.hp = max(0, owner.vitals.hp - dmg)
                ctx.log(f"**{owner.name}**'s Dry Skin drained HP in the sun! (\u2212**{dmg:,}** HP)")
        elif _rain(field) and owner.vitals.hp < owner.vitals.max_hp:
            heal = max(1, owner.vitals.max_hp // 8)
            owner.vitals.hp = min(owner.vitals.max_hp, owner.vitals.hp + heal)
            ctx.log(f"**{owner.name}**'s Dry Skin restored **{heal:,}** HP in the rain!")


def _weather_recovery(slug: str, weather_check, fraction: int, display: str):
    @register_ability(slug)
    class _WeatherRecovery(Ability):
        async def on_end_turn(self, ctx, owner) -> None:
            field = ctx.field
            if weather_suppressed(field) or owner.fainted or owner.vitals.hp >= owner.vitals.max_hp:
                return
            if not weather_check(field):
                return
            heal = max(1, owner.vitals.max_hp // fraction)
            owner.vitals.hp = min(owner.vitals.max_hp, owner.vitals.hp + heal)
            ctx.log(f"**{owner.name}**'s {display} restored **{heal:,}** HP!")
    _WeatherRecovery.__name__ = display.replace(" ", "") + "Ability"
    return _WeatherRecovery


RainDish = _weather_recovery("rain-dish", _rain, 16, "Rain Dish")
IceBody = _weather_recovery("ice-body", lambda f: get_weather(f) in ("hail", "snow"), 16, "Ice Body")   # Showdown: isWeather(['hail', 'snow'])


@register_ability("solar-power")
class SolarPower(Ability):
    async def on_end_turn(self, ctx, owner) -> None:
        field = ctx.field
        if weather_suppressed(field) or owner.fainted or not _sun(field):
            return
        dmg = max(1, owner.vitals.max_hp // 8)
        dmg = await owner.consult_reduce("on_indirect_damage", ctx, dmg, "weather")  # see Dry Skin's note on why local
        if dmg > 0:
            owner.vitals.hp = max(0, owner.vitals.hp - dmg)
            ctx.log(f"**{owner.name}**'s Solar Power cost it HP! (\u2212**{dmg:,}** HP)")

    async def on_modify_spa(self, ctx, owner, value: int) -> int | None:
        if _sun(field_of(ctx)):
            return int(value * 1.5)
        return None


@register_ability("hydration")
class Hydration(Ability):
    async def on_end_turn(self, ctx, owner) -> None:
        field = ctx.field
        if weather_suppressed(field) or not _rain(field) or not owner.vitals.status:
            return
        ctx.log(f"**{owner.name}**'s Hydration cured {owner.vitals.status.replace('_', ' ').title()}!")
        owner.vitals.status = None


@register_ability("poison-heal")
class PoisonHeal(Ability):
    """Reverses poison/toxic damage into healing by returning a *negative*
    on_indirect_damage value -- the generic status tick is expected to do
    `owner.vitals.hp -= events.reduce("on_indirect_damage", ctx, raw_dmg)`
    uniformly, so a negative result here becomes a heal for free, no
    separate hook needed. Old code: contore.py:15797-15816."""
    async def on_indirect_damage(self, ctx, owner, damage: int, source: str = "") -> int | None:
        if owner.vitals.status not in ("poison", "bad_poison") or source != "status":
            return None
        # Showdown: `this.heal(target.baseMaxhp / 8)` -- a FLAT 1/8 whatever the toxic counter is; the engine logs the heal.
        return -max(1, owner.vitals.max_hp // 8)


@register_ability("shed-skin")
class ShedSkin(Ability):
    """Showdown: `onResidualOrder: 5`, `randomChance(33, 100)` -- it runs BEFORE the burn/poison tick (order 9-10), so a cured Pokemon takes no
    status damage that turn. The engine dispatches `on_early_residual` ahead of the status damage loop for exactly this."""
    async def on_early_residual(self, ctx, owner) -> None:
        if owner.vitals.status and random.random() < 0.33:
            owner.vitals.status = None
            owner.vitals.toxic_counter = 0
            ctx.log(f"**{owner.name}** shed its skin!")


@register_ability("harvest")
class Harvest(Ability):
    async def on_end_turn(self, ctx, owner) -> None:
        used = getattr(owner, "berry_used", False)
        last = getattr(owner, "last_consumed_berry", None)
        if not (used and last) or owner.identity.held_item:
            return
        thresh = 1.0 if (not weather_suppressed(ctx.field) and _sun(ctx.field)) else 0.5
        if random.random() < thresh:
            owner.identity.held_item = last
            ctx.engine.sync_ability_and_item(owner)
            owner.berry_used = False
            ctx.log(f"**{owner.name}**'s Harvest restored the {last}!")


@register_ability("bad-dreams")
class BadDreams(Ability):
    async def on_end_turn(self, ctx, owner) -> None:
        for foe in ctx.field.all_battlers():
            if ctx.field.side_of(foe) is ctx.field.side_of(owner) or foe.vitals.status != "sleep":
                continue
            dmg = max(1, foe.vitals.max_hp // 8)
            dmg = await foe.consult_reduce("on_indirect_damage", ctx, dmg, "ability")  # see Dry Skin's note on why local
            if dmg > 0:
                foe.vitals.hp = max(0, foe.vitals.hp - dmg)
                ctx.log(f"**{foe.name}** was hurt by Bad Dreams! (\u2212**{dmg:,}** HP)")


# -- turn counters ----------------------------------------------------------

@register_ability("speed-boost")
class SpeedBoost(Ability):
    async def on_end_turn(self, ctx, owner) -> None:
        if owner.fainted:
            return
        # Battler.stages is a plain dict (see battler.py's Boosts
        # component) -- it has no .clamp() of its own. Stat-stage changes
        # always go through Battler.clamp_stage(), which also produces the
        # log message and (correctly) says nothing extra when already
        # capped at +6, so we just log whatever it returns instead of
        # assuming success.
        msg = await owner.clamp_stage(ctx, "spe", 1)
        if msg:
            ctx.log(msg)


@register_ability("moody")
class Moody(Ability):
    _STATS = ("atk", "def", "spa", "spd", "spe", "acc", "eva")

    async def on_end_turn(self, ctx, owner) -> None:
        if owner.fainted:
            return
        up = random.choice(self._STATS)
        down = random.choice([s for s in self._STATS if s != up])
        up_msg = await owner.clamp_stage(ctx, up, 2)
        down_msg = await owner.clamp_stage(ctx, down, -1)
        if up_msg:
            ctx.log(up_msg)
        if down_msg:
            ctx.log(down_msg)


@register_ability("slow-start")
class SlowStart(Ability):
    """Old code counts down a fixed 5-turn timer set at switch-in
    (contore.py:15484). Track C's Battler/Identity doesn't have a slot for
    this counter yet -- attaching a plain attribute here rather than
    inventing a whole Component for a single int, matching how the old
    monolith's own `disabled_move`/`infatuated`/etc. get referenced
    elsewhere in this migration batch (see NOTES_FOR_TRACK_C.md #1)."""
    async def on_switch_in(self, ctx, owner) -> None:
        owner.slow_start_turns = 5

    async def on_end_turn(self, ctx, owner) -> None:
        turns = getattr(owner, "slow_start_turns", 0)
        if turns <= 0:
            return
        owner.slow_start_turns = turns - 1
        if owner.slow_start_turns == 0:
            ctx.log(f"**{owner.name}**'s Slow Start wore off!")

    async def on_modify_atk(self, ctx, owner, value: int) -> int | None:
        if getattr(owner, "slow_start_turns", 0) > 0:
            return value // 2
        return None

    async def on_modify_spe(self, ctx, owner, value: int) -> int | None:
        if getattr(owner, "slow_start_turns", 0) > 0:
            return value // 2
        return None


# -- weather-boosted speed abilities ----------------------------------------

def _weather_speed_double(slug: str, weather_check, display: str):
    @register_ability(slug)
    class _WeatherSpeed(Ability):
        async def on_modify_spe(self, ctx, owner, value: int) -> int | None:
            field = field_of(ctx)
            if weather_suppressed(field) or not weather_check(field):
                return None
            return value * 2
    _WeatherSpeed.__name__ = display.replace(" ", "") + "Ability"
    return _WeatherSpeed


Chlorophyll = _weather_speed_double("chlorophyll", _sun, "Chlorophyll")
SwiftSwim = _weather_speed_double("swift-swim", _rain, "Swift Swim")
SandRush = _weather_speed_double("sand-rush", lambda f: get_weather(f) == "sand", "Sand Rush")
SlushRush = _weather_speed_double("slush-rush", lambda f: get_weather(f) in ("hail", "snow"), "Slush Rush")        # Showdown: `['hail', 'snowscape'].includes(effectiveWeather())`, Gen 7+


# -- weather-chip immunity (in addition to each ability's headline effect) -

@register_ability("sand-veil")
class SandVeil(Ability):
    async def on_indirect_damage(self, ctx, owner, damage: int, source: str = "") -> int | None:
        # source == "weather": specifically sandstorm's own chip damage,
        # not e.g. poison/recoil damage that merely happens to occur
        # during a sandstorm turn -- an earlier draft (before `source`
        # existed) blocked ANY indirect damage whenever it was sandy,
        # which would have wrongly no-op'd a sand-veil Pokemon's poison
        # tick or Life Orb recoil too. Same fix applied to Snow
        # Cloak/Sand Force below.
        if source == "weather" and get_raw_weather(field_of(ctx)) == "sand":
            return 0
        return None

    async def on_accuracy_modifier(self, ctx, owner, mod: int) -> int | None:
        if owner not in ctx.targets or get_raw_weather(ctx.battle.field) != "sand":
            return None
        return chain_mod(mod, 3277)


@register_ability("snow-cloak")
class SnowCloak(Ability):
    async def on_indirect_damage(self, ctx, owner, damage: int, source: str = "") -> int | None:
        # See Sand Veil's note above -- same source=="weather" fix.
        if source == "weather" and get_raw_weather(field_of(ctx)) == "hail":
            return 0
        return None

    async def on_accuracy_modifier(self, ctx, owner, mod: int) -> int | None:
        if owner not in ctx.targets or get_raw_weather(ctx.battle.field) not in ("hail", "snow"):
            return None
        return chain_mod(mod, 3277)


@register_ability("sand-force")
class SandForce(Ability):
    async def on_indirect_damage(self, ctx, owner, damage: int, source: str = "") -> int | None:
        # See Sand Veil's note above -- same source=="weather" fix.
        if source == "weather" and get_raw_weather(field_of(ctx)) == "sand":
            return 0
        return None

    async def on_modify_power(self, ctx, owner, power: int) -> int | None:
        if owner is not ctx.user or get_raw_weather(ctx.battle.field) != "sand":
            return None
        if ctx.move.spec.type_name in ("Rock", "Ground", "Steel"):
            return modify_raw(power, 5325)
        return None

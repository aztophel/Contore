"""Weather-setting, terrain-setting, and screen-setting moves.

All three families share one structural quirk: their MoveSpec.target is
ENTIRE_FIELD / USERS_FIELD (PokeAPI's real values for these moves), which
targeting.py's resolve_targets() deliberately resolves to an EMPTY list --
these moves act on a Side or the Field itself, not any individual Battler
(see targeting.py's comment on USERS_FIELD/OPPONENTS_FIELD/ENTIRE_FIELD).
But engine.py's _execute_move treats an empty target list as "no target,
so the move failed", which is wrong for this whole family. Rather than
special-case that generic engine logic for these specific targets, every
class below overrides get_targets() to return `[ctx.user]` -- a
placeholder so the engine's per-target loop runs on_hit() exactly once --
and on_hit() itself never actually uses that `target` argument, reading
`ctx.field`/`ctx.user_side` directly instead.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from ..move import Move
from ..context import MoveContext
from ..registry import register_move

if TYPE_CHECKING:
    from ..battler import Battler


class _FieldMove(Move):
    """Shared by every move in this file: `get_targets` always resolves to
    just the user, regardless of the move's real (field/side-wide)
    PokeAPI target, so the engine's per-target loop still runs on_hit()
    once. See this module's docstring."""
    async def get_targets(self, ctx: MoveContext) -> list["Battler"]:
        return [ctx.user]


# -- weather ---------------------------------------------------------------

def _weather_move(slug: str, kind: str, message: str):
    @register_move(slug)
    class _WeatherMove(_FieldMove):
        async def on_hit(self, ctx: MoveContext, target) -> None:
            from ..abilities.field_effects import set_weather, weather_duration
            # Air Lock/Cloud Nine suppress weather's EFFECTS while active,
            # not whether it can be SET -- using Sunny Day with an Air Lock
            # Pokemon out still changes the weather variable; it just does
            # nothing until Air Lock leaves. See abilities/_util.py's
            # weather_suppressed docstring. No check needed here at all.
            result = set_weather(ctx.field, kind, ctx.user, weather_duration(kind, ctx.user))
            ctx.log(message if result else "But it failed!")
    _WeatherMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _WeatherMove


SunnyDayMove = _weather_move("sunny-day", "sun", "The sunlight turned harsh!")
RainDanceMove = _weather_move("rain-dance", "rain", "It started to rain!")
SandstormMove = _weather_move("sandstorm", "sand", "A sandstorm kicked up!")
HailMove = _weather_move("hail", "hail", "It started to hail!")


# -- terrain -----------------------------------------------------------
# Old code's TERRAIN_SETTING_MOVES branch (contore.py:17041-17042) didn't
# check for grounding at all before applying terrain -- terrain itself
# only ever *affects* grounded Pokemon (see battle/terrain.py), so
# nothing further is needed here; the terrain-setting move itself always
# succeeds regardless of who's grounded.

def _terrain_move(slug: str, kind: str, message: str):
    @register_move(slug)
    class _TerrainMove(_FieldMove):
        async def on_hit(self, ctx: MoveContext, target) -> None:
            from ..abilities.field_effects import set_terrain, terrain_duration
            result = set_terrain(ctx.field, kind, terrain_duration(ctx.user))
            ctx.log(message if result else "But it failed!")
    _TerrainMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _TerrainMove


ElectricTerrainMove = _terrain_move("electric-terrain", "electric",
                                     "An electric current ran across the battlefield!")
GrassyTerrainMove = _terrain_move("grassy-terrain", "grassy",
                                   "Grass grew to cover the battlefield!")
MistyTerrainMove = _terrain_move("misty-terrain", "misty",
                                  "Mist swirled around the battlefield!")
PsychicTerrainMove = _terrain_move("psychic-terrain", "psychic",
                                    "The battlefield got weird!")


# -- screens (user's own side) ------------------------------------------

# Light Clay only extends Reflect/Light Screen/Aurora Veil to 8 turns --
# NOT Safeguard/Mist/Tailwind/Lucky Chant, which are always 5 regardless
# of held item in every generation this bot supports.
_LIGHT_CLAY_SCREENS = frozenset({"reflect", "light_screen"})


def _screen_move(slug: str, screen_name: str):
    @register_move(slug)
    class _ScreenMove(_FieldMove):
        async def on_hit(self, ctx: MoveContext, target) -> None:
            from ..side_conditions import set_screen
            side = ctx.user_side
            turns = 8 if ctx.user.effective_item() == "Light Clay" and screen_name in _LIGHT_CLAY_SCREENS else 5
            if screen_name == "tailwind":                  # Showdown: 4 turns (Gen 5+), 3 in Gen 4; it was 5 here, i.e. one turn too long in EVERY generation
                turns = 4 if ctx.ruleset.number >= 5 else 3
            msg = set_screen(side, screen_name, turns, ctx.events)
            ctx.log(msg)
    _ScreenMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _ScreenMove


ReflectMove = _screen_move("reflect", "reflect")
LightScreenMove = _screen_move("light-screen", "light_screen")
SafeguardMove = _screen_move("safeguard", "safeguard")
MistMove = _screen_move("mist", "mist")
TailwindMove = _screen_move("tailwind", "tailwind")
LuckyChantMove = _screen_move("lucky-chant", "lucky_chant")


@register_move("aurora-veil")
class AuroraVeilMove(_FieldMove):
    """Only usable while Hail/Snow is active -- Pokemon Showdown:
    data/moves.ts's onTry for aurora veil (`this.field.isWeather(['hail',
    'snowscape'])`)."""
    async def on_try_use(self, ctx: MoveContext) -> str | None:
        if ctx.field.weather not in ("hail", "snow"):
            return "But it failed!"
        return None

    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..side_conditions import set_screen
        turns = 8 if ctx.user.effective_item() == "Light Clay" else 5
        ctx.log(set_screen(ctx.user_side, "aurora_veil", turns, ctx.events))


# -- rooms, Gravity, sports, Haze ---------------------------------------------
# Showdown pseudo-weather moves (data/moves.ts). The Field already carried the
# counters and their end-of-turn countdown/messages (`Field.tick_global`) and
# most CONSUMERS (Trick Room's speed reversal in `_order_actions`, Gravity's
# 5/3 accuracy, `sport_mult`); nothing ever SET them, and every one of these
# resolves to zero battlers so they all failed with "But it failed!".

def _room_turns(user) -> int:
    """5 turns, 7 for a Persistent user (the `durationCallback` of Trick
    Room / Gravity / Magic Room / Wonder Room)."""
    return 7 if user.effective_ability() == "persistent" else 5


def _pseudo_weather_move(slug: str, counter: str, start: str, *, toggles: bool,
                         end: str | None = None, turns=lambda user: 5, register: bool = True):
    """`toggles`: using it while active ENDS it (Showdown `onFieldRestart:
    removePseudoWeather` -- Trick/Magic/Wonder Room). Otherwise using it
    while active fails (Gravity, Mud Sport, Water Sport)."""
    class _PseudoWeatherMove(_FieldMove):
        async def on_hit(self, ctx: MoveContext, target) -> None:
            field = ctx.field
            if getattr(field, counter) > 0:
                if toggles:
                    setattr(field, counter, 0)
                    ctx.log(end)
                    await self.after_change(ctx)
                else:
                    ctx.log("But it failed!")
                return
            setattr(field, counter, turns(ctx.user))
            ctx.log(start.format(user=ctx.user.name))
            await self.after_start(ctx)
            await self.after_change(ctx)

        async def after_start(self, ctx: MoveContext) -> None:
            pass

        async def after_change(self, ctx: MoveContext) -> None:
            """Runs after the counter was set OR cleared (a toggle-off)."""
    _PseudoWeatherMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return register_move(slug)(_PseudoWeatherMove) if register else _PseudoWeatherMove


TrickRoomMove = _pseudo_weather_move(
    "trick-room", "trick_room_turns", "**{user}** twisted the dimensions!",
    toggles=True, end="The twisted dimensions returned to normal!", turns=_room_turns)
_MagicRoomBase = _pseudo_weather_move(
    "magic-room", "magic_room_turns",
    "**{user}** created a bizarre area in which Pok\u00e9mon's held items lose their effects!",
    toggles=True, end="The area returned to normal!", turns=_room_turns, register=False)


@register_move("magic-room")
class MagicRoomMove(_MagicRoomBase):
    """Items are attached at switch-in, so starting/ending the room must
    re-sync them (`BattleEngine.resync_items`); expiry does the same."""
    async def after_change(self, ctx: MoveContext) -> None:
        ctx.engine.resync_items()
WonderRoomMove = _pseudo_weather_move(
    "wonder-room", "wonder_room_turns",
    "**{user}** created a bizarre area in which Defense and Sp. Def stats are swapped!",
    toggles=True, end="The area returned to normal!", turns=_room_turns)
MudSportMove = _pseudo_weather_move(
    "mud-sport", "mud_sport_turns", "Electricity's power was weakened!", toggles=False)
WaterSportMove = _pseudo_weather_move(
    "water-sport", "water_sport_turns", "Fire's power was weakened!", toggles=False)


@register_move("gravity")
class GravityMove(_pseudo_weather_move("gravity", "gravity_turns", "Gravity intensified!",
                                       toggles=False, turns=_room_turns, register=False)):
    """On start, everything airborne by a MOVE or effect is pulled down
    (Showdown's gravity `onFieldStart`): Fly/Bounce users lose their charge,
    Magnet Rise and Telekinesis end. (Flying types and Levitate are simply
    grounded -- `Battler.is_grounded` checks Gravity first.) Selecting a
    `GRAVITY_BANNED_MOVES` move is blocked in engine.py."""

    async def after_start(self, ctx: MoveContext) -> None:
        from ..battler import _MagnetRise, _Telekinesis
        from ..volatile import Charging
        for b in ctx.battle.field.all_battlers():
            charging = b.get(Charging)
            fell = False
            if charging is not None and charging.move_slug in ("fly", "bounce"):
                b.remove(Charging, ctx.events)
                fell = True
            for marker in (_MagnetRise, _Telekinesis):
                if b.get(marker) is not None:
                    b.remove(marker, ctx.events)
                    fell = True
            if fell:
                ctx.log(f"**{b.name}** couldn't stay airborne because of gravity!")


@register_move("haze")
class HazeMove(_FieldMove):
    """Showdown `onHitField`: every active Pokemon's stat stages go to 0."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        for b in ctx.battle.field.all_battlers():
            for k in b.stages:
                b.stages[k] = 0
        ctx.log("All stat changes were eliminated!")

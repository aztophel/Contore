"""Weather/terrain components, and the abilities that set them on entry.

Old code: WEATHER_SETTING_ABILITIES / TERRAIN_SETTING_ABILITIES dicts
(contore.py:~3269-3286), consulted from `_apply_entry_abilities`
(contore.py:23437-23441) via `field.set_weather(...)` /
`field.set_terrain(...)` on the old monolithic BattleField.

See NOTES_FOR_TRACK_C.md #2 for why Weather/Terrain are defined here as
plain Components rather than fields on the new Field -- that file doesn't
have weather/terrain slots yet, and this uses only the already-frozen
generic Entity.add()/.get() API, so it needs no cooperation from field.py
to work correctly today.
"""
from __future__ import annotations
from dataclasses import dataclass

from ...ecs.component import Component
from ..registry import register_ability
from ..ability import Ability
from ._util import weather_suppressed


@dataclass
class Weather(Component):
    kind: str                          # "rain" | "sun" | "sand" | "hail" | "snow" (Gen 9) | "harsh-sun" | "heavy-rain" | "strong-winds"
    turns_remaining: int | None = 5    # None = indefinite (primal weather)
    source_id: str | None = None


@dataclass
class Terrain(Component):
    kind: str                          # "electric" | "grassy" | "psychic" | "misty"
    turns_remaining: int = 5


def get_weather(field) -> str | None:
    w = field.get(Weather)
    return w.kind if w and not weather_suppressed(field) else None


def get_raw_weather(field) -> str | None:
    """Weather ignoring Air Lock/Cloud Nine suppression -- Drought is still
    considered 'set' for the purposes of e.g. not letting Drizzle overwrite
    it, even while an Air Lock Pokémon is out."""
    w = field.get(Weather)
    return w.kind if w else None


def get_terrain(field) -> str | None:
    t = field.get(Terrain)
    return t.kind if t else None


PRIMAL_WEATHER = frozenset({"harsh-sun", "heavy-rain", "strong-winds"})


def _set_weather(field, kind: str, owner, turns: int | None = 5) -> str | None:
    current = field.get(Weather)
    if current is not None and current.kind == kind:
        return None
    # Showdown `onAnySetWeather`: while a PRIMAL weather is up, only another primal weather can replace it.
    if current is not None and current.kind in PRIMAL_WEATHER and kind not in PRIMAL_WEATHER:
        return None
    field.add(Weather(kind=kind, turns_remaining=turns, source_id=owner.id))
    return kind


# -- [ADDITIVE / Track C] --------------------------------------------------
# Weather-extending rocks (Heat Rock/Damp Rock/Smooth Rock/Icy Rock -> 8
# turns instead of 5). Old code never checked these for EITHER the
# ability-setters below or move-based weather (contore.py's
# WEATHER_SETTING_MOVES branch just called `field.set_weather(kind)` with
# no turns argument at all) -- a real completeness gap rather than a
# deliberate choice, so fixed here for both, not preserved as a bug.
WEATHER_ROCKS = {"sun": "Heat Rock", "rain": "Damp Rock", "sand": "Smooth Rock", "hail": "Icy Rock", "snow": "Icy Rock"}


def weather_duration(kind: str, owner) -> int:
    return 8 if owner.effective_item() == WEATHER_ROCKS.get(kind) else 5


def _set_terrain(field, kind: str, turns: int = 5) -> str | None:
    current = field.get(Terrain)
    if current is not None and current.kind == kind:
        return None
    field.add(Terrain(kind=kind, turns_remaining=turns))
    return kind


def terrain_duration(owner) -> int:
    """Terrain Extender (Gen 7+ item): 8 turns instead of 5."""
    return 8 if owner.effective_item() == "Terrain Extender" else 5


# -- [ADDITIVE / Track C] --------------------------------------------------
# Public wrappers around the two setters above. Track C's move files (Rain
# Dance, Sunny Day, Sandstorm, Hail, Electric/Grassy/Psychic/Misty Terrain,
# ...) need to set weather/terrain exactly the same way these abilities do,
# but "own" moves.py, not this abilities/ package -- importing a
# leading-underscore name across that boundary works in Python but reads
# like a mistake. These are pure passthroughs, nothing about the private
# versions' behaviour changes. See NOTES_FOR_TRACK_C.md #2.
def set_weather(field, kind: str, owner, turns: int = 5) -> str | None:
    return _set_weather(field, kind, owner, turns)


def set_terrain(field, kind: str, turns: int = 5) -> str | None:
    return _set_terrain(field, kind, turns)
# ---------------------------------------------------------------------------


_WEATHER_MESSAGES = {
    "rain": "It started to rain!",
    "sun": "The sunlight turned harsh!",
    "sand": "A sandstorm kicked up!",
    "hail": "It started to hail!",
    "snow": "It started to snow!",
}
_TERRAIN_MESSAGES = {
    "electric": "An electric current ran across the battlefield!",
    "grassy": "Grass grew to cover the battlefield!",
    "psychic": "The battlefield got weird!",
    "misty": "Mist swirled around the battlefield!",
}


def _weather_setter(slug: str, kind: str, display: str):
    @register_ability(slug)
    class _WeatherSetter(Ability):
        async def on_switch_in(self, ctx, owner) -> None:
            if _set_weather(ctx.field, kind, owner, weather_duration(kind, owner)):
                ctx.log(f"{owner.name}'s {display} {_WEATHER_MESSAGES[kind]}")
    _WeatherSetter.__name__ = f"{display.replace(' ', '')}Ability"
    return _WeatherSetter


def _terrain_setter(slug: str, kind: str, display: str):
    @register_ability(slug)
    class _TerrainSetter(Ability):
        async def on_switch_in(self, ctx, owner) -> None:
            if _set_terrain(ctx.field, kind, terrain_duration(owner)):
                ctx.log(f"{owner.name}'s {display}! {_TERRAIN_MESSAGES[kind]}")
    _TerrainSetter.__name__ = f"{display.replace(' ', '')}Ability"
    return _TerrainSetter


Drizzle = _weather_setter("drizzle", "rain", "Drizzle")
Drought = _weather_setter("drought", "sun", "Drought")
SandStream = _weather_setter("sand-stream", "sand", "Sand Stream")
@register_ability("snow-warning")
class SnowWarning(Ability):
    """Gen 9 Snow Warning summons SNOW (no chip damage; Ice types get +50% Defense); before that it summoned hail."""
    async def on_switch_in(self, ctx, owner) -> None:
        kind = "snow" if ctx.ruleset.number >= 9 else "hail"
        if _set_weather(ctx.field, kind, owner, weather_duration(kind, owner)):
            ctx.log(f"{owner.name}'s Snow Warning {_WEATHER_MESSAGES[kind]}")

ElectricSurge = _terrain_setter("electric-surge", "electric", "Electric Surge")
GrassySurge = _terrain_setter("grassy-surge", "grassy", "Grassy Surge")
PsychicSurge = _terrain_setter("psychic-surge", "psychic", "Psychic Surge")
MistySurge = _terrain_setter("misty-surge", "misty", "Misty Surge")


@register_ability("air-lock")
class AirLock(Ability):
    """Announces suppression on entry (contore.py:23430). The actual
    suppression is implicit: every other ability/hook that cares about
    weather calls `_util.weather_suppressed(field)` or
    `field_effects.get_weather(field)` (which already accounts for this),
    so Air Lock itself needs no other hook."""
    async def on_switch_in(self, ctx, owner) -> None:
        ctx.log(f"{owner.name}'s Air Lock eliminated all weather effects!")


@register_ability("cloud-nine")
class CloudNine(Ability):
    async def on_switch_in(self, ctx, owner) -> None:
        ctx.log(f"{owner.name}'s Cloud Nine eliminated all weather effects!")


# -- primal weather (Desolate Land / Primordial Sea / Delta Stream) ------------------------------------------------
# Indefinite (`turns_remaining=None`), can't be replaced by ordinary weather, and ends when the LAST holder leaves or
# faints (Showdown's `onEnd`; the monolith's `on_pokemon_left_field`, which the merge had dropped). The Fire/Water
# nullification itself already lives in `Field.damage_mult`; only the setters were missing.

def _primal(slug: str, kind: str, display: str, start: str, end: str):
    @register_ability(slug)
    class _Primal(Ability):
        async def on_switch_in(self, ctx, owner) -> None:
            if _set_weather(ctx.field, kind, owner, None):
                ctx.log(start)

        async def _leave(self, ctx, owner) -> None:
            w = ctx.field.get(Weather)
            if w is None or w.kind != kind or w.source_id != owner.id:
                return
            other = next((b for b in ctx.field.all_battlers()
                          if b is not owner and not b.fainted and b.effective_ability() == slug), None)
            if other is not None:
                w.source_id = other.id          # another holder keeps it going
                return
            ctx.field.remove(Weather)
            ctx.log(end)

        async def on_switch_out(self, ctx, owner) -> None:
            await self._leave(ctx, owner)

        async def on_faint(self, ctx, owner) -> None:
            await self._leave(ctx, owner)
    _Primal.__name__ = display.replace(" ", "") + "Ability"
    return _Primal


DesolateLand = _primal("desolate-land", "harsh-sun", "Desolate Land",
                       "The sunlight turned extremely harsh!", "The harsh sunlight faded.")
PrimordialSea = _primal("primordial-sea", "heavy-rain", "Primordial Sea",
                        "A heavy rain began to fall!", "The heavy rain has lifted!")
DeltaStream = _primal("delta-stream", "strong-winds", "Delta Stream",
                      "Mysterious strong winds are protecting Flying-type Pok\u00e9mon!",
                      "The mysterious air current has dissipated!")

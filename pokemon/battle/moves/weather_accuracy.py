"""Moves whose ACCURACY depends on the weather (Showdown `onModifyMove`):
  Thunder / Hurricane : never miss in rain (and heavy rain), 50% accuracy in harsh sunlight (and extremely harsh sun)
  Blizzard            : never misses in hail OR snow
`compute_accuracy` (damage.py) asks `Move.on_modify_accuracy`; `float('inf')` means "can't miss". `ctx.field.weather` already honours Cloud Nine / Air Lock.
None of these had any weather handling before."""
from __future__ import annotations

from ..move import BasicMove
from ..registry import register_move

_RAIN = frozenset({"rain", "heavy-rain"})
_SUN = frozenset({"sun", "harsh-sun"})
_COLD = frozenset({"hail", "snow"})


def _weather_accuracy_move(slug: str, never_misses_in: frozenset[str], half_in: frozenset[str] = frozenset(), min_gen: int = 1):
    @register_move(slug)
    class _WeatherAccuracyMove(BasicMove):
        async def on_modify_accuracy(self, ctx, target, accuracy: float) -> float | None:
            weather = ctx.field.weather
            if min_gen > ctx.ruleset.number:
                return None
            if weather in never_misses_in:
                return float("inf")
            if weather in half_in:
                return 50.0
            return None
    _WeatherAccuracyMove.__name__ = slug.title().replace("-", "") + "Move"
    return _WeatherAccuracyMove


Thunder = _weather_accuracy_move("thunder", _RAIN, _SUN)
Hurricane = _weather_accuracy_move("hurricane", _RAIN, _SUN)
Blizzard = _weather_accuracy_move("blizzard", _COLD, min_gen=4)    # Gen 3 Blizzard still rolls 70% in hail (Showdown)

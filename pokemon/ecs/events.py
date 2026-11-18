"""Central event bus -- see ecs/component.py's `Effect` docstring for the
concept. This module is the dispatcher: given a hook name, it finds every
currently-attached Effect (volatile conditions, abilities, held items,
weather/terrain/side conditions) that implements it and calls them in the
correct order.

This mirrors Pokémon Showdown's `runEvent`, which is the standard, proven
approach to "many small, independently authored effects all need to hook
the same handful of moments in a turn, in a well-defined precedence order."
We're deliberately not reinventing that wheel.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable

from .component import Effect, Entity


@dataclass
class _Registration:
    owner: Entity
    effect: Effect


class EventBus:
    def __init__(self) -> None:
        self._registrations: list[_Registration] = []

    def register(self, owner: Entity, effect: Effect) -> None:
        self._registrations.append(_Registration(owner, effect))

    def unregister(self, owner: Entity, effect: Effect) -> None:
        self._registrations = [
            r for r in self._registrations if not (r.owner is owner and r.effect is effect)
        ]

    @staticmethod
    def _item_inert(r: "_Registration") -> bool:
        """Showdown `ignoringItem()`: an item that Klutz / Embargo / Magic Room suppresses (or that is no longer the holder's item) takes part in NO event. Only a handful of item classes
        used to check `effective_item()` themselves, so Choice Band, Life Orb, Expert Belt ... kept working under Embargo / Klutz / Magic Room."""
        names = getattr(type(r.effect), "item_names", None)
        return bool(names) and hasattr(r.owner, "effective_item") and r.owner.effective_item() not in names

    def _ordered(self, hook_name: str, speed_key: Callable[[Entity], int] | None):
        candidates = [r for r in self._registrations if hasattr(r.effect, hook_name) and not self._item_inert(r)]
        if speed_key is None:
            return sorted(candidates, key=lambda r: -r.effect.priority)
        return sorted(candidates, key=lambda r: (-r.effect.priority, -speed_key(r.owner)))

    async def run(self, hook_name: str, ctx, *args: Any,
                   speed_key: Callable[[Entity], int] | None = None,
                   order_range: tuple[float, float] | None = None,
                   owner_filter: Callable[[Any], bool] | None = None) -> None:
        """Fire-and-forget: e.g. on_switch_in, on_end_turn, on_faint.

        `order_range=(lo, hi)` (end-of-turn only): run just the effects whose Showdown `onResidualOrder` is in [lo, hi), IN that order (then sub-order, then priority). An effect with no
        `residual_order` counts as 29 (after everything Showdown orders explicitly). The engine runs the status-damage step (poison 9 / burn 10) between two such ranges, so the heals
        and drains Showdown orders BEFORE it (Wish 4, Leftovers 5, Aqua Ring 6, Ingrain 7, Leech Seed 8) really do come first."""
        regs = self._ordered(hook_name, speed_key)
        if owner_filter is not None:
            regs = [r for r in regs if owner_filter(r.owner)]
        if order_range is not None:
            lo, hi = order_range
            old = getattr(getattr(ctx, "ruleset", None), "number", 9) <= 4          # Gens 3-4 have their own table: Wish 7, then everything at 10 with sub-orders (data/mods/gen4)
            def key(r):
                o = (getattr(r.effect, "residual_order_gen4", None) if old else None) or getattr(r.effect, "residual_order", (29, 0))
                return o[0] + o[1] / 100                                           # order, then sub-order
            regs = sorted((r for r in regs if lo <= key(r) < hi), key=key)         # stable: ties keep the priority / speed order
        for reg in regs:
            await getattr(reg.effect, hook_name)(ctx, reg.owner, *args)

    async def reduce(self, hook_name: str, ctx, value: Any, *args: Any,
                      speed_key: Callable[[Entity], int] | None = None) -> Any:
        """Chain: e.g. on_modify_damage, on_modify_power, on_modify_accuracy.
        Each handler gets the running value and may return a replacement;
        returning None leaves it unchanged, so most handlers only need to
        `return` early when they have nothing to say."""
        for reg in self._ordered(hook_name, speed_key):
            result = await getattr(reg.effect, hook_name)(ctx, reg.owner, value, *args)
            if result is not None:
                value = result
        return value

    async def first(self, hook_name: str, ctx, *args: Any,
                     speed_key: Callable[[Entity], int] | None = None) -> Any:
        """First non-None answer wins: e.g. on_type_immunity (Wonder Guard
        overriding the normal calc), on_check_accuracy (No Guard)."""
        for reg in self._ordered(hook_name, speed_key):
            result = await getattr(reg.effect, hook_name)(ctx, reg.owner, *args)
            if result is not None:
                return result
        return None

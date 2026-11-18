"""Lightweight Entity-Component core for the battle engine.

This is intentionally *not* a general-purpose game ECS (no archetype
storage, no system scheduler). A battle has at most ~12 entities alive at
once, so a plain dict-of-components per entity is simpler than, and just as
fast as, anything more elaborate.

Components are plain dataclasses. Presence of a component on an entity *is*
the state: a Pokémon that's never been confused has no Confusion component
at all, rather than the old FighterState's `confused: bool = False` sitting
unused on every single Pokémon, in every single battle, forever.
"""
from __future__ import annotations
from typing import TypeVar, Callable, Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from .events import EventBus

C = TypeVar("C", bound="Component")


class Component:
    """Marker base for pure-data components. Subclass with @dataclass."""
    __slots__ = ()


class Effect(Component):
    """A component that is *also* behaviour: while attached to an entity, it
    can implement any of the hook methods documented on Move/Ability
    (on_modify_damage, on_end_turn, on_try_hit, ...) and those hooks fire
    through the battle's EventBus, in priority order, alongside every other
    attached Effect's hooks.

    This is the concrete mechanism behind "moves can dynamically attach
    components to a Pokémon or the field and have that data do something":
    Substitute, Leech Seed, Confusion, Light Screen, Stealth Rock, weather,
    and terrain are all Effect subclasses that a Move attaches mid-battle.
    Abilities and held items are Effects too (see battle/ability.py) --
    structurally identical, they just happen to be attached for the
    Pokémon's whole life instead of created mid-battle by a move.

    `priority` controls hook-ordering when multiple Effects implement the
    same hook (see EventBus._ordered) -- higher runs first.
    """
    priority: int = 0


class Entity:
    """Anything that can hold components: a Battler, a Side, or the Field."""

    __slots__ = ("id", "_components")

    def __init__(self, entity_id: str):
        self.id = entity_id
        self._components: dict[type, Component] = {}

    def add(self, component: Component, bus: "EventBus | None" = None) -> None:
        self._components[type(component)] = component
        if bus is not None and isinstance(component, Effect):
            bus.register(self, component)

    def get(self, comp_type: type[C]) -> C | None:
        return self._components.get(comp_type)  # type: ignore[return-value]

    def require(self, comp_type: type[C]) -> C:
        comp = self._components.get(comp_type)
        if comp is None:
            raise KeyError(f"{self!r} is missing required component {comp_type.__name__}")
        return comp  # type: ignore[return-value]

    def has(self, comp_type: type) -> bool:
        return comp_type in self._components

    def remove(self, comp_type: type, bus: "EventBus | None" = None) -> Component | None:
        comp = self._components.pop(comp_type, None)
        if comp is not None and bus is not None and isinstance(comp, Effect):
            bus.unregister(self, comp)
        return comp

    def ensure(self, comp_type: type[C], factory: Callable[[], C]) -> C:
        """Get a component, creating it with `factory` if absent."""
        comp = self._components.get(comp_type)
        if comp is None:
            comp = factory()
            self._components[comp_type] = comp
        return comp  # type: ignore[return-value]

    def effects(self) -> Iterator[Effect]:
        return (c for c in self._components.values() if isinstance(c, Effect))

    def __repr__(self) -> str:
        names = [type(c).__name__ for c in self._components.values()]
        return f"<{type(self).__name__} {self.id} components={names}>"

"""Move/Ability registries.

Both Move and Ability instances are STATELESS SINGLETONS: all per-use /
per-battle data lives on MoveContext or on Components attached to a
Battler/Field, never on `self`. That means there is exactly ONE Move object
and ONE Ability object per slug for the entire process lifetime -- built
once at startup, looked up by dict from then on. Re-parsing a move's data or
re-instantiating its class on every single use would be pure waste for data
that never changes once the bot has booted, so `load()` is the only place
that ever does that work.

The one-time cost of turning PokeAPI's raw response shape into a MoveSpec
happens exactly once, OFFLINE, in pokemon/dex/pokedex_builder.py, which
writes the already-MoveSpec-shaped result to data/moves_data.json
(committed to the repo -- see the git-based deploy notes in ARCHITECTURE.md
for why this replaces the old runtime PokeAPI-fetching code entirely). At
runtime, `load()` below just deserializes that file directly into MoveSpec
objects via MoveSpec.from_dict -- no reinterpretation of PokeAPI's shape
ever happens while the bot is running.
"""
from __future__ import annotations
import dataclasses
import json
from pathlib import Path

from .move import Move, MoveSpec, MoveCategory, BasicMove
from .ability import Ability

_move_classes: dict[str, type[Move]] = {}
_ability_classes: dict[str, type[Ability]] = {}


def register_move(slug: str):
    """Class decorator: this custom Move subclass is used for `slug`
    instead of the generic BasicMove built from cached data alone."""
    def deco(cls: type[Move]):
        _move_classes[slug] = cls
        return cls
    return deco


def register_ability(slug: str):
    def deco(cls: type[Ability]):
        _ability_classes[slug] = cls
        return cls
    return deco


def _apply_stat_overrides(spec: MoveSpec, ov: dict) -> MoveSpec:
    """`spec` with one generation's stat changes applied (see `MoveRegistry.for_generation`)."""
    changes: dict = {}
    if "base_power" in ov:
        changes["base_power"] = ov["base_power"]
    if "accuracy" in ov:                       # Showdown `accuracy: true` == never misses == our always_hits
        always = ov["accuracy"] == "always"
        changes["accuracy"] = None if always else ov["accuracy"]
        changes["always_hits"] = always
    if "type_name" in ov:
        changes["type_name"] = ov["type_name"]
    if "category" in ov:
        changes["category"] = MoveCategory(ov["category"])
    if "priority" in ov:
        changes["priority"] = ov["priority"]
    if "pp" in ov:
        changes["pp"] = ov["pp"]
    if "target" in ov:                         # a different TARGET in that generation (Surf: foes only in Gen 3, Poison Gas: one target before Gen 6)
        from .move import MoveTarget
        changes["target"] = MoveTarget(ov["target"])
    for k in ("ailment", "ailment_chance", "flinch_chance", "stat_chance"):    # a damaging move's secondary effects in that generation (Poison Fang's toxic, Waterfall ...)
        if k in ov:
            changes[k] = ov[k]
    if "flags_xor" in ov:                      # the flags that differ in that generation (Gen 3: Ancient Power / Overheat make contact, Covet / Fake Out / Feint Attack don't, ...)
        from .move import MoveFlag
        changes["flags"] = spec.flags ^ MoveFlag(ov["flags_xor"])
    if "stat_changes" in ov:                   # a status move's boosts in that generation (String Shot -1 before Gen 6, ...)
        from .move import StatChange
        changes["stat_changes"] = tuple(StatChange(sc["stat"], sc["change"]) for sc in ov["stat_changes"])
    return dataclasses.replace(spec, **changes)


class _GenerationMoves:
    """A read-only, per-generation face of a MoveRegistry: `get`/`spec` like the registry, but a move whose stats changed
    between generations is a separate (cached) instance of the SAME class built from the adjusted spec. Everything else
    is the registry's own object, so there is no per-battle copying."""

    def __init__(self, base: "MoveRegistry", generation: int, overrides: dict[str, dict]) -> None:
        self._base, self.generation, self._ov = base, generation, overrides
        self._specs: dict[str, MoveSpec] = {}
        self._instances: dict[str, Move] = {}

    def spec(self, slug: str) -> MoveSpec:
        spec = self._specs.get(slug)
        if spec is None:
            ov = self._ov.get(slug)
            spec = self._specs[slug] = _apply_stat_overrides(self._base.spec(slug), ov) if ov else self._base.spec(slug)
        return spec

    def get(self, slug: str) -> Move:
        inst = self._instances.get(slug)
        if inst is None:
            base = self._base.get(slug)
            inst = self._instances[slug] = type(base)(self.spec(slug)) if slug in self._ov else base
        return inst

    def __getattr__(self, name: str):          # anything else (iteration helpers, ...) is the registry's
        return getattr(self._base, name)


class MoveRegistry:
    """Owns every Move instance for the process lifetime. `load()` is
    called exactly once, at bot startup; every other call site just does
    `registry.get(slug)` -- an O(1) dict lookup against an already-built
    object, never a re-parse or a re-instantiation."""

    def __init__(self) -> None:
        self._specs: dict[str, MoveSpec] = {}
        self._instances: dict[str, Move] = {}
        self._gen_overrides: dict[int, dict[str, dict]] = {}
        self._gen_views: dict[int, "MoveRegistry | _GenerationMoves"] = {}

    def load(self, data_path: Path) -> None:
        with open(data_path, encoding="utf-8") as f:
            raw: dict[str, dict] = json.load(f)
        for slug, entry in raw.items():
            spec = MoveSpec.from_dict(entry)
            self._specs[slug] = spec
            cls = _move_classes.get(slug, BasicMove)
            self._instances[slug] = cls(spec)
        # data/move_gen_overrides.json (tests/tools/make_move_gen_overrides.js): how move STATS differed in Gens 3-8.
        # Optional -- without it every generation just sees the current stats, as before.
        ov_path = Path(data_path).with_name("move_gen_overrides.json")
        if ov_path.exists():
            with open(ov_path, encoding="utf-8") as f:
                self._gen_overrides = {int(g): m for g, m in json.load(f).items() if g.isdigit()}

    def for_generation(self, generation: int) -> "MoveRegistry | _GenerationMoves":
        """The registry as seen from `generation`: same moves, with that generation's base power / accuracy / type /
        category / priority / PP (Showdown's data/mods chain). Gen 9 (and any generation with no recorded changes) is
        the registry itself. The BattleEngine holds one of these, so every `engine.moves.get/spec` is generation-correct
        without a single call site knowing about generations."""
        view = self._gen_views.get(generation)
        if view is None:
            ov = self._gen_overrides.get(generation)
            view = _GenerationMoves(self, generation, ov) if ov else self
            self._gen_views[generation] = view
        return view

    def get(self, slug: str) -> Move:
        try:
            return self._instances[slug]
        except KeyError:
            raise KeyError(
                f"Move '{slug}' isn't in the loaded registry -- was load() "
                f"called, and does data/moves_data.json contain it?"
            ) from None

    def spec(self, slug: str) -> MoveSpec:
        return self._specs[slug]

    def __contains__(self, slug: str) -> bool:
        return slug in self._instances

    def __len__(self) -> int:
        return len(self._instances)


class AbilityRegistry:
    """Same idea as MoveRegistry. Abilities carry no per-slug data beyond
    their slug/display name, so there's no separate "spec" step -- just a
    cached singleton instance per registered class, plus one shared no-op
    instance reused for every slug without a custom implementation yet
    (so an un-migrated ability is simply inert rather than an error)."""

    def __init__(self) -> None:
        self._instances: dict[str, Ability] = {}
        self._noop = Ability()

    def load(self, known_slugs: list[str]) -> None:
        for slug in known_slugs:
            cls = _ability_classes.get(slug)
            self._instances[slug] = cls() if cls else self._noop

    def get(self, slug: str) -> Ability:
        return self._instances.get(slug, self._noop)

    def __len__(self) -> int:
        return len(self._instances)

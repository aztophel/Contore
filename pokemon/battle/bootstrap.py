"""Boots the process-lifetime battle registries (Move/Ability/Item).

`MoveRegistry`/`AbilityRegistry`/`ItemRegistry` (registry.py, items.py) are
stateless singletons built exactly once and looked up by slug from then on
-- see registry.py's module docstring. Nothing in Track C's own delivery
called `.load()` on any of them; nothing in Track B's delivery did either
(the closest is a one-off, throwaway `MoveRegistry()` in
`pokemon/commands/moveset.py`, which never calls `.load()` and isn't
shared with anything else). This module is that missing wiring: call
`load_registries()` once at bot startup and hand the result to whatever
constructs a `BattleEngine` (currently: nothing does yet -- see
`pokemon.ui.battle_view`'s module docstring for the still-open "who starts
a battle" gap this bootstrap unblocks but doesn't itself close).

Each sub-load is independently tolerant of its data file being absent, so
one missing file doesn't block the other two:
- `data/abilities.json` / `data/items_meta.json` (Track B, both present)
  feed `AbilityRegistry`/`ItemRegistry`'s `known_slugs` -- these registries
  don't need per-slug *data*, just the full list of valid slugs, so an
  unregistered ability/item becomes the shared no-op instance rather than
  a KeyError (see registry.py's `AbilityRegistry` docstring).
- `data/moves_data.json` (Track B's Known Gap #1 -- see
  TRACK_B_HANDOFF.md) does NOT exist yet as of this integration pass.
  `MoveRegistry.get(slug)` raises a clear KeyError against an empty
  registry rather than crashing the whole bot at boot; every call site
  that looks up a move already has to handle "this move isn't
  implemented" in some form (BasicMove is the fallback for *classes*, but
  there is no fallback for "not in the data file at all").
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .items import ItemRegistry, registered_item_slugs
from .registry import AbilityRegistry, MoveRegistry

# Importing these packages is what actually runs every `@register_move` /
# `@register_ability` / `@register_item` class decorator in their
# submodules (auto-discovered by each package's own `__init__.py`) --
# populating the `_move_classes`/`_ability_classes`/`_item_classes` dicts
# `MoveRegistry.load()`/`AbilityRegistry.load()`/`ItemRegistry.load()` read
# from below. Skipping this import doesn't error -- `.load()` just quietly
# hands every single move/ability/item its generic BasicMove/no-op
# fallback instead of its real hand-written behaviour, which is a much
# worse bug precisely because it fails silently. Must happen before any
# `.load()` call, so it's done here rather than left to whatever else in
# the codebase happens to import these packages first.
from . import abilities as _abilities  # noqa: F401
from . import held_items as _held_items  # noqa: F401
from . import moves as _moves  # noqa: F401


@dataclass
class BattleRegistries:
    moves: MoveRegistry
    abilities: AbilityRegistry
    items: ItemRegistry


def _read_json(path: Path) -> object | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_registries(data_dir: Path) -> BattleRegistries:
    """Build and load all three registries from `data_dir` (normally
    `<repo root>/data`). Safe to call more than once (e.g. a future
    `/deploy pull` hot-reload) -- it always returns a fresh set of
    registries rather than mutating shared state, so swap the bot's
    reference to the old one only once the new one has fully loaded."""
    moves = MoveRegistry()
    moves_path = data_dir / "moves_data.json"
    if moves_path.exists():
        moves.load(moves_path)
    # else: left empty -- see module docstring. Not raising here so
    # abilities/items below still load and the rest of the bot still boots.

    abilities = AbilityRegistry()
    ability_slugs = _read_json(data_dir / "abilities.json")
    if isinstance(ability_slugs, list):
        abilities.load(ability_slugs)

    items = ItemRegistry()
    item_names = _read_json(data_dir / "items_meta.json")
    known_item_slugs: set[str] = set(item_names.keys()) if isinstance(item_names, dict) else set()
    # Union with every item that has a real registered class -- see
    # `items.registered_item_slugs`'s docstring. Without this, an item
    # missing from items_meta.json's (fling-data-only, and as it turns
    # out incomplete) key set would silently fall back to ItemRegistry's
    # shared no-op instance even though a real implementation exists.
    known_item_slugs |= registered_item_slugs()
    if known_item_slugs:
        items.load(sorted(known_item_slugs))

    return BattleRegistries(moves=moves, abilities=abilities, items=items)

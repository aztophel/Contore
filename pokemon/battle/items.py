"""HeldItem base class + registry.

Deliberately structured as a mirror of ability.py/registry.py's
AbilityRegistry -- same stateless-singleton invariant, same "unregistered
slug behaves as a complete no-op" default, same registration decorator
shape. See ability.py's module docstring for the reasoning; it all applies
here unchanged, just keyed by `Identity.held_item` instead of
`Identity.ability_slug`.

The economy-facing metadata every item already has (display name, shop
price, emoji, flavor description) stays exactly where it's always been --
Track B's data/items.json -- since that's inventory/shop data, not battle
behavior. This module is ONLY the in-battle behavior half: what a held
item actually *does* once it's on a Pokémon in a fight.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from ..ecs.component import Effect

if TYPE_CHECKING:
    from .battler import Battler
    from .context import BattleContext, MoveContext


class HeldItem(Effect):
    is_held_item = True       # (class attribute, not a field) -- read by EventBus._item_inert
    slug: str = ""          # matches Identity.held_item exactly (display name, e.g. "Leftovers")
    consumable: bool = False

    # -- lifecycle ----------------------------------------------------------
    async def on_switch_in(self, ctx: "BattleContext", owner: "Battler") -> None:
        """Air Balloon's just-arrived state, Booster Energy, ..."""

    async def on_end_turn(self, ctx: "BattleContext", owner: "Battler") -> None:
        """Leftovers, Black Sludge, Flame/Toxic Orb, status/stat/pinch
        berries, Shell Bell already covered by on_after_damaged instead."""

    # -- defending against a move --------------------------------------------
    async def on_modify_damage_taken(self, ctx: "MoveContext", owner: "Battler", damage: int) -> int | None:
        """Nothing in the migrated set needs this yet (Eviolite/Assault
        Vest work by modifying the *stat*, not post-hoc damage) but it's
        here for parity with Ability, e.g. a future Berserk Gene analogue."""

    async def on_after_damaged(self, ctx: "MoveContext", owner: "Battler", damage: int) -> None:
        """Rocky Helmet, Sitrus/pinch berries eaten reactively, Air
        Balloon popping, Red Card, Eject Button, Weakness Policy."""

    async def on_modify_atk(self, ctx: "MoveContext", owner: "Battler", value: int) -> int | None:
        """Choice Band, Thick Club-style items are data-driven so aren't
        needed here, Eviolite (def/spd not atk -- see on_modify_def)."""

    async def on_modify_spe(self, ctx: "BattleContext", owner: "Battler", value: int) -> int | None:
        """Choice Scarf, Iron Ball, Quick Powder."""

    async def on_modify_accuracy(self, ctx: "MoveContext", owner: "Battler", value: float) -> float | None:
        """Bright Powder, Wide Lens, Zoom Lens."""

    async def on_modify_power(self, ctx: "MoveContext", owner: "Battler", power: int) -> int | None:
        """Type-boost items (Charcoal, Mystic Water, ...), Life Orb folded
        in here as a power multiplier for symmetry with Flash Fire's
        equivalent choice in abilities/type_immunity.py."""

    async def on_modify_damage(self, ctx: "MoveContext", owner: "Battler", damage: int) -> int | None:
        """Expert Belt (super-effective hits only)."""

    async def on_modify_crit_stage(self, ctx: "MoveContext", owner: "Battler", stage: int) -> int | None:
        """Scope Lens, Razor Claw (+1)."""

    async def on_modify_secondary_chance(self, ctx: "MoveContext", owner: "Battler", chance: int) -> int | None:
        """King's Rock / Razor Fang (add a flinch chance -- modeled as a
        secondary-chance addition on Tackle-style contact moves)."""

    async def on_indirect_damage(self, ctx: "BattleContext | MoveContext", owner: "Battler", damage: int,
                                  source: str = "") -> int | None:
        """Black Sludge's non-Poison-type damage (source="item") is the
        one migrated item that uses this. `source` distinguishes which
        kind of indirect damage this is -- see `ability.py`'s fuller
        docstring on the same hook for the full value set and why it's
        needed."""

    async def on_modify_def(self, ctx: "MoveContext", owner: "Battler", value: int) -> int | None:
        """Eviolite (1.5x, NFE only), Assault Vest (1.5x Sp.Def -- see
        on_modify_spd instead, listed here just for the Eviolite half)."""

    async def on_modify_spd(self, ctx: "MoveContext", owner: "Battler", value: int) -> int | None:
        """Eviolite, Assault Vest."""

    async def on_stat_drop_immunity(self, ctx: "BattleContext | MoveContext", owner: "Battler", stat: str) -> bool | None:
        """Clear Amulet-style items if ever added; White Herb restores
        rather than prevents, so it's on_end_turn instead -- listed here
        for hook-surface parity with Ability."""

    # -- [ADDITIVE / Track C] items.py didn't mirror this one from
    # ability.py's hook surface yet -- needed for Safety Goggles (blocks
    # powder moves) the same way Ability.on_try_hit blocks Overcoat's.
    async def on_try_hit(self, ctx: "MoveContext", owner: "Battler") -> bool | None:
        """Return True to block the move entirely before it resolves
        (Safety Goggles vs. powder moves)."""


class ItemRegistry:
    """Mirrors AbilityRegistry exactly -- see that class's docstring."""

    def __init__(self) -> None:
        self._instances: dict[str, HeldItem] = {}
        self._noop = HeldItem()

    def load(self, known_slugs: list[str]) -> None:
        for slug in known_slugs:
            cls = _item_classes.get(slug)
            self._instances[slug] = cls() if cls else self._noop

    def get(self, slug: str | None) -> HeldItem:
        if not slug:
            return self._noop
        return self._instances.get(slug, self._noop)

    def __len__(self) -> int:
        return len(self._instances)


_item_classes: dict[str, type[HeldItem]] = {}


def registered_item_slugs() -> frozenset[str]:
    """Every item name with an actual hand-written `HeldItem` subclass
    registered against it -- see `bootstrap.load_registries`'s docstring
    for why `known_slugs` needs this in addition to `data/items_meta.json`'s
    keys: that file only has FLING data, sourced from a cache that turned
    out to be missing several real, commonly-used items entirely (the
    status-cure berries among them) despite this module having real,
    correct classes for them all along."""
    return frozenset(_item_classes.keys())


def register_item(slug: str):
    """Class decorator, exactly like registry.py's register_ability. `slug`
    is the item's exact display name (e.g. "Leftovers", "Choice Band") --
    the old monolith keyed HELD_ITEMS/effective_item() by display name
    rather than a kebab-case slug, and Identity.held_item follows that same
    convention (see battler.py), so items.py does too for consistency."""
    def deco(cls: type[HeldItem]):
        _item_classes[slug] = cls
        cls.item_names = cls.__dict__.get("item_names", frozenset()) | {slug}      # every name this class is registered under (read by EventBus._item_inert)
        return cls
    return deco


# Items a theft/swap effect (Thief, Covet, Trick, Switcheroo, Pickpocket,
# Magician) must never move: Mega Stones, Genesect's Drives, Arceus's
# Plates. Old code: `_is_safe_item`, contore.py:16737-16738 -- exactly
# `item in MEGA_STONES or item in _DRIVE_TYPES.keys() or item in _PLATE_TYPES.keys()`.
# MEGA_STONES/_DRIVE_TYPES/_PLATE_TYPES themselves are Track B's economy
# data (data/items.json); this module keeps only the flat name sets it
# needs to answer "is this one of those", so held-item battle logic
# doesn't have to import Track B's shop-facing data structures.
DRIVE_NAMES: frozenset[str] = frozenset({
    "Douse Drive", "Shock Drive", "Burn Drive", "Chill Drive",
})
PLATE_NAMES: frozenset[str] = frozenset({
    "Flame Plate", "Splash Plate", "Zap Plate", "Meadow Plate", "Icicle Plate",
    "Fist Plate", "Toxic Plate", "Earth Plate", "Sky Plate", "Mind Plate",
    "Insect Plate", "Stone Plate", "Spooky Plate", "Draco Plate", "Dread Plate",
    "Iron Plate", "Pixie Plate",
})
# Mega Stone names themselves are per-species (Charizardite X, ...) and
# live in Track B's MEGA_STONES dict -- `is_unstealable` accepts the full
# set of stone names as an optional parameter so callers with access to
# that dict (or a `mega_stone` flag on Identity, see NOTES_FOR_TRACK_C.md)
# can pass it through; without it, Drives/Plates are still caught correctly.
def is_unstealable(item_name: str | None, mega_stone_names: frozenset[str] = frozenset()) -> bool:
    if not item_name:
        return False
    return item_name in DRIVE_NAMES or item_name in PLATE_NAMES or item_name in mega_stone_names

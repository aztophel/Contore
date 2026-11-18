"""Rules governing when a species' alternate form changes: holding an
Orb/Drive/Plate, using a key item, weather, an HP threshold, or the
"revert automatically at end of turn/battle" cases. Ported verbatim from
the monolith's `FORM_CHANGE_RULES`.

Ownership note: this table has entries three different subsystems each
need a slice of --
  - `kind in ("held_item", "held_item_override", "held_item_removed")` and
    `kind == "item"` (Meteorite, Reveal Glass, Prison Bottle, ...) drive
    `/pokemon item give`/`take`/`use` outside of any battle -- Track B's
    territory, and what `resolve_held_item_form` below answers.
  - `kind in ("weather", "hp_threshold", "auto_revert")` are real-time,
    in-battle-only triggers (Castform's weather forms, Zen Darmanitan,
    Shaymin reverting on freeze) -- Track C/D's battle-hook territory.
  - `battle_only=True` entries (Primal Reversion) apply in *both* contexts
    with different rules, which is exactly why `resolve_held_item_form`
    takes an `in_battle` flag rather than this being two separate tables.

Kept as one shared table (rather than duplicated per-track copies) since
Track D's `held_items/PROGRESS.md` already flags this exact split when
noting which held-item effects are "Track B's territory" -- both sides
importing the same data means a rule only ever needs updating once.
"""

from __future__ import annotations

FORM_CHANGE_RULES: dict[int, list[dict]] = {
    492: [  # Shaymin
        {"from_slug": None, "to_slug": "shaymin-sky", "kind": "item", "item": "Gracidea",
         "hour_range": (4, 20), "blocked_status": {"freeze"}, "consume_item": True},
        {"from_slug": "shaymin-sky", "to_slug": None, "kind": "auto_revert",
         "hour_range": (4, 20), "revert_on_status": {"freeze"}},
    ],
    487: [  # Giratina
        {"from_slug": None, "to_slug": "giratina-origin", "kind": "held_item", "item": "Griseous Orb"},
        {"from_slug": "giratina-origin", "to_slug": None, "kind": "held_item_removed", "item": "Griseous Orb"},
    ],
    382: [{"from_slug": None, "to_slug": "kyogre-primal", "kind": "held_item", "item": "Blue Orb", "battle_only": True}],
    383: [{"from_slug": None, "to_slug": "groudon-primal", "kind": "held_item", "item": "Red Orb", "battle_only": True}],
    555: [  # Darmanitan
        {"from_slug": None, "to_slug": "darmanitan-zen", "kind": "hp_threshold", "below_pct": 0.5},
        {"from_slug": "darmanitan-zen", "to_slug": None, "kind": "hp_threshold", "above_pct": 0.5},
    ],
    351: [  # Castform
        {"kind": "weather", "weathers": frozenset({"sun", "harsh_sun"}), "to_slug": "castform-sunny"},
        {"kind": "weather", "weathers": frozenset({"rain", "heavy_rain"}), "to_slug": "castform-rainy"},
        {"kind": "weather", "weathers": frozenset({"hail"}), "to_slug": "castform-snowy"},
    ],
    386: [  # Deoxys
        {"kind": "item", "item": "Meteorite", "from_slug": None, "to_slug": "deoxys-attack", "consume_item": False},
        {"kind": "item", "item": "Meteorite", "from_slug": "deoxys-attack", "to_slug": "deoxys-defense", "consume_item": False},
        {"kind": "item", "item": "Meteorite", "from_slug": "deoxys-defense", "to_slug": "deoxys-speed", "consume_item": False},
        {"kind": "item", "item": "Meteorite", "from_slug": "deoxys-speed", "to_slug": None, "consume_item": False},
    ],
    421: [{"kind": "weather", "weathers": frozenset({"sun", "harsh_sun"}), "to_slug": "cherrim-sunshine"}],  # Cherrim
    493: [  # Arceus
        {"kind": "held_item_override", "item": "Flame Plate", "to_slug": "arceus-fire"},
        {"kind": "held_item_override", "item": "Splash Plate", "to_slug": "arceus-water"},
        {"kind": "held_item_override", "item": "Zap Plate", "to_slug": "arceus-electric"},
        {"kind": "held_item_override", "item": "Meadow Plate", "to_slug": "arceus-grass"},
        {"kind": "held_item_override", "item": "Icicle Plate", "to_slug": "arceus-ice"},
        {"kind": "held_item_override", "item": "Fist Plate", "to_slug": "arceus-fighting"},
        {"kind": "held_item_override", "item": "Toxic Plate", "to_slug": "arceus-poison"},
        {"kind": "held_item_override", "item": "Earth Plate", "to_slug": "arceus-ground"},
        {"kind": "held_item_override", "item": "Sky Plate", "to_slug": "arceus-flying"},
        {"kind": "held_item_override", "item": "Mind Plate", "to_slug": "arceus-psychic"},
        {"kind": "held_item_override", "item": "Insect Plate", "to_slug": "arceus-bug"},
        {"kind": "held_item_override", "item": "Stone Plate", "to_slug": "arceus-rock"},
        {"kind": "held_item_override", "item": "Draco Plate", "to_slug": "arceus-dragon"},
        {"kind": "held_item_override", "item": "Dread Plate", "to_slug": "arceus-dark"},
        {"kind": "held_item_override", "item": "Iron Plate", "to_slug": "arceus-steel"},
        {"kind": "held_item_override", "item": "Pixie Plate", "to_slug": "arceus-fairy"},
    ],
    641: [  # Tornadus
        {"kind": "item", "item": "Reveal Glass", "from_slug": None, "to_slug": "tornadus-therian", "consume_item": False},
        {"kind": "item", "item": "Reveal Glass", "from_slug": "tornadus-therian", "to_slug": None, "consume_item": False},
    ],
    642: [  # Thundurus
        {"kind": "item", "item": "Reveal Glass", "from_slug": None, "to_slug": "thundurus-therian", "consume_item": False},
        {"kind": "item", "item": "Reveal Glass", "from_slug": "thundurus-therian", "to_slug": None, "consume_item": False},
    ],
    645: [  # Landorus
        {"kind": "item", "item": "Reveal Glass", "from_slug": None, "to_slug": "landorus-therian", "consume_item": False},
        {"kind": "item", "item": "Reveal Glass", "from_slug": "landorus-therian", "to_slug": None, "consume_item": False},
    ],
    646: [],  # Kyurem
    649: [  # Genesect
        {"kind": "held_item_override", "item": "Burn Drive", "to_slug": "genesect-burn"},
        {"kind": "held_item_override", "item": "Chill Drive", "to_slug": "genesect-chill"},
        {"kind": "held_item_override", "item": "Douse Drive", "to_slug": "genesect-douse"},
        {"kind": "held_item_override", "item": "Shock Drive", "to_slug": "genesect-shock"},
    ],
    720: [  # Hoopa
        {"kind": "item", "item": "Prison Bottle", "from_slug": None, "to_slug": "hoopa-unbound", "consume_item": False},
        {"kind": "item", "item": "Prison Bottle", "from_slug": "hoopa-unbound", "to_slug": None, "consume_item": False},
    ],
}


def resolve_item_form_change(
    species_id: int, current_form: str | None, status: str | None, item_name: str, *, hour: int | None = None,
) -> tuple[str | None, bool, str | None]:
    """The `kind == "item"` rules (Gracidea, Deoxys' Meteorite, Reveal
    Glass, Prison Bottle) -- a key-item-triggered form change, as opposed
    to `resolve_held_item_form`'s "form follows whatever's currently
    held". Returns `(new_form_slug, consume_item, error_message)`: on
    success `error_message` is `None` and the caller should persist
    `new_form_slug`; on failure `new_form_slug` is `None` and
    `error_message` explains why. `hour` defaults to the current local
    hour if not given (tests can pass one explicitly). Ported from
    `try_use_form_item`, with the live-battle `FighterState` it operated
    on replaced by plain `current_form`/`status` strings -- this only
    ever runs outside of battle (`/pokemon item use`), so nothing here
    actually needed live battle state.
    """
    from datetime import datetime

    rules = FORM_CHANGE_RULES.get(species_id, [])
    rule = next(
        (r for r in rules if r.get("kind") == "item" and r["item"] == item_name and r["from_slug"] == current_form),
        None)
    if rule is None:
        return None, False, f"{item_name} has no effect right now."

    if status in rule.get("blocked_status", set()):
        return None, False, f"It can't change form while affected by that status condition!"

    lo, hi = rule.get("hour_range", (0, 24))
    check_hour = hour if hour is not None else datetime.now().hour
    if not (lo <= check_hour < hi):
        return None, False, f"{item_name} has no effect right now. Try again between {lo}:00 and {hi}:00."

    return rule["to_slug"], rule.get("consume_item", True), None


def resolve_held_item_form(
    species_id: int, held_item: str | None, current_form: str | None, *, in_battle: bool = True
) -> str | None:
    """What form a Pokémon should be wearing right now, purely as a
    function of its species and currently-held item (Arceus/Genesect's
    "always exactly matches whatever's held" override, and Giratina's
    "toggles on Griseous Orb attach/detach"). Ignores the `"item"`/
    `"weather"`/`"hp_threshold"` rule kinds entirely -- those are
    key-item-triggered or in-battle-only and not a function of held item
    alone. Ported verbatim from `resolve_held_item_form`.
    """
    rules = FORM_CHANGE_RULES.get(species_id, [])

    override_rules = [r for r in rules if r.get("kind") == "held_item_override"]
    if override_rules:
        for r in override_rules:
            if held_item == r["item"]:
                return r["to_slug"]
        return None

    if not in_battle:
        battle_only_slugs = {r["to_slug"] for r in rules if r.get("battle_only") and r.get("kind") == "held_item"}
        if current_form in battle_only_slugs:
            return None

    for r in rules:
        kind = r.get("kind")
        if kind not in ("held_item", "held_item_removed"):
            continue
        if r.get("battle_only") and not in_battle:
            continue
        if kind == "held_item" and r["from_slug"] == current_form and held_item == r["item"]:
            return r["to_slug"]
        if kind == "held_item_removed" and r["from_slug"] == current_form and held_item != r["item"]:
            return r["to_slug"]
    return current_form

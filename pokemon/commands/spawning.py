"""Rolls a wild Pokémon encounter: species (weighted by rarity, respecting
a server's configured generation cap), shininess, level, IVs, nature,
gender, alternate form, and held item.

Deliberately pure and synchronous -- no Discord, no database. The monolith
had this logic fused directly into `spawn_pokemon(channel)`, which rolled
an encounter *and* built+sent the silhouette embed *and* tracked it in
`_ACTIVE_SPAWNS` all in one function; that's why every other subsystem
that also needed "roll a wild Pokémon" (breeding's egg species selection
never did, but the catch-flow's re-roll-on-flee and admin `/pokemon spawn`
debug command both ended up half-copying pieces of it). Splitting the roll
out means every caller -- the on_message spawn hook, `/pokemon hunt`, any
future admin debug command -- shares one implementation and only differs
in what it does with the resulting `WildEncounter`.

Cross-generation integration point: `roll_wild_encounter` takes a
`GenerationRuleset` (a server's configured `ServerPokemonRules.dex_ruleset`)
and rolls only from `SpeciesRepository.for_ruleset(ruleset)` -- species
above the server's configured generation cap simply aren't in the weighted
pool, which is exactly the "SPAWN_ONLY" restriction mode
`ruleset.py`'s module docstring describes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime

from pokemon.dex.evolution import get_default_repository as get_evolution_repo
from pokemon.dex.species import STAT_KEYS, Species, SpeciesForm, get_default_repository as get_species_repo
from pokemon.generations.ruleset import GenerationRuleset

# ---------------------------------------------------------------------------
# Constants -- ported verbatim from the monolith's top-level constant block
# and the "Battling" region (where `_roll_*` lived despite not being
# battle-specific). Values unchanged; only `IV_STATS`'s shorthand keys
# became `STAT_KEYS`'s canonical ones (see `pokemon.commands.repository`'s
# module docstring for why).
# ---------------------------------------------------------------------------

RARITY_WEIGHTS: dict[str, float] = {
    "common": 1000, "uncommon": 450, "rare": 120, "epic": 69, "legendary": 20, "mythical": 5,
}
_COMMON_UPGRADE_CHANCE = 0.004

LEVEL_RANGES_BY_RARITY: dict[str, tuple[int, int]] = {
    "common": (2, 15), "uncommon": (8, 25), "rare": (15, 35),
    "epic": (35, 55), "legendary": (50, 50), "mythical": (50, 50),
}
LEGENDARY_MYTHICAL_LEVELS: dict[int, int] = {
    144: 50, 145: 50, 146: 50,
    150: 70, 151: 70,
    243: 40, 244: 40, 245: 40,
    249: 40, 250: 40, 251: 30,
    377: 40, 378: 40, 379: 40,
    380: 45, 381: 45,
    382: 45, 383: 45, 384: 70,
    385: 35, 386: 80,
    480: 50, 481: 50, 482: 50,
    483: 47, 484: 47, 485: 70, 486: 70, 487: 47, 488: 50,
    489: 5, 490: 5, 491: 40, 492: 30, 493: 100, 494: 35,
    638: 42, 639: 42, 640: 42,
    641: 40, 642: 40, 643: 50, 644: 50, 645: 40, 646: 75,
    647: 35, 648: 35, 649: 35,
    716: 50, 717: 50, 718: 70, 719: 50, 720: 50, 721: 70,
}

NATURES: tuple[str, ...] = (
    "Hardy", "Lonely", "Brave", "Adamant", "Naughty", "Bold", "Docile", "Relaxed",
    "Impish", "Lax", "Timid", "Hasty", "Serious", "Jolly", "Naive", "Modest", "Mild",
    "Quiet", "Bashful", "Rash", "Calm", "Gentle", "Sassy", "Careful", "Quirky",
)

SHINY_ODDS = 4096  # 1-in-N, both wild spawns and hatched eggs

_STARTER_BASE_IDS: frozenset[int] = frozenset({
    1, 4, 7, 152, 155, 158, 252, 255, 258,
    387, 390, 393, 495, 498, 501, 650, 653, 656,
})
_ALL_STARTER_IDS: frozenset[int] = frozenset({
    1, 2, 3, 4, 5, 6, 7, 8, 9,
    152, 153, 154, 155, 156, 157, 158, 159, 160,
    252, 253, 254, 255, 256, 257, 258, 259, 260,
    387, 388, 389, 390, 391, 392, 393, 394, 395,
    495, 496, 497, 498, 499, 500, 501, 502, 503,
    650, 651, 652, 653, 654, 655, 656, 657, 658,
})

_WILD_HELD_ITEM_BY_TYPE: dict[str, list[str]] = {
    "Normal": ["Sitrus Berry"],
    "Fire": ["Sitrus Berry", "Life Orb"],
    "Water": ["Sitrus Berry", "Leftovers"],
    "Grass": ["Sitrus Berry", "Leftovers"],
    "Electric": ["Sitrus Berry"],
    "Ice": ["Sitrus Berry"],
    "Fighting": ["Sitrus Berry", "Focus Sash"],
    "Poison": ["Black Sludge", "Sitrus Berry"],
    "Ground": ["Sitrus Berry"],
    "Flying": ["Sitrus Berry"],
    "Psychic": ["Sitrus Berry", "Focus Sash"],
    "Bug": ["Sitrus Berry"],
    "Rock": ["Sitrus Berry"],
    "Ghost": ["Sitrus Berry"],
    "Dragon": ["Sitrus Berry", "Life Orb"],
    "Dark": ["Sitrus Berry"],
    "Steel": ["Sitrus Berry", "Eviolite"],
    "Fairy": ["Sitrus Berry"],
}
_RARITY_ITEM_CHANCE: dict[str, float] = {
    "common": 0.05, "uncommon": 0.07, "rare": 0.10, "epic": 0.14, "legendary": 0.20, "mythical": 0.25,
}
_RARITY_PREMIUM_CHANCE: dict[str, float] = {
    "common": 0.0, "uncommon": 0.05, "rare": 0.12, "epic": 0.22, "legendary": 0.35, "mythical": 0.45,
}
_PREMIUM_HELD_ITEMS: tuple[str, ...] = (
    "Life Orb", "Choice Band", "Choice Specs", "Choice Scarf",
    "Expert Belt", "Weakness Policy", "Rocky Helmet", "Assault Vest",
    "Leftovers", "Focus Sash",
)
_GLASS_CANNON_ITEMS: frozenset[str] = frozenset({"Life Orb", "Choice Band", "Choice Specs"})

SEASONAL_FORM_SPECIES: dict[int, str] = {585: "deerling", 586: "sawsbuck"}
SEASON_SUFFIX_BY_MONTH: dict[int, str] = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn",
}
RANDOM_FORM_WEIGHTS: dict[int, list[tuple[str | None, float]]] = {
    201: [(None, 1.0)]
         + [(f"unown-{c}", 1.0) for c in "bcdefghijklmnopqrstuvwxyz"]
         + [("unown-exclamation", 1.0), ("unown-question", 1.0)],
    422: [("west-sea", 0.5), ("east-sea", 0.5)],
    423: [("west-sea", 0.5), ("east-sea", 0.5)],
    412: [("plant", 0.5), ("sandy", 0.25), ("trash", 0.25)],
    413: [("plant", 0.5), ("sandy", 0.25), ("trash", 0.25)],
    550: [("red-striped", 0.5), ("blue-striped", 0.5)],
    669: [("red", .2), ("yellow", .2), ("orange", .2), ("blue", .2), ("white", .2)],
    670: [("red", .2), ("yellow", .2), ("orange", .2), ("blue", .2), ("white", .2), ("eternal", 0.001)],
    671: [("red", .2), ("yellow", .2), ("orange", .2), ("blue", .2), ("white", .2)],
    710: [("average", .55), ("small", .2), ("large", .2), ("super", .05)],
    711: [("average", .55), ("small", .2), ("large", .2), ("super", .05)],
}
# Meowstic: the female form isn't cosmetic (different ability/moveset), so
# it's rolled the same way any other gendered-appearance species would be
# if this bot had more of them.
GENDER_FORM_SPECIES: dict[int, dict[str, str | None]] = {
    678: {"male": None, "female": "meowstic-female"},
}


# ---------------------------------------------------------------------------
# Non-wild-evolved suppression: species that are (almost) never found in
# the wild because in the real games they only exist post-evolution via a
# stone/trade/friendship trigger. Computed once from `EvolutionRepository`
# rather than requiring a species-data flag.
# ---------------------------------------------------------------------------

_non_wild_evolved_cache: frozenset[int] | None = None


def _non_wild_evolved_ids() -> frozenset[int]:
    global _non_wild_evolved_cache
    if _non_wild_evolved_cache is None:
        evo_repo = get_evolution_repo()
        ids: set[int] = set()
        for sp in get_species_repo().all():
            for cond in evo_repo.evolutions_from(sp.id):
                if cond.trigger in ("use-item", "trade"):
                    ids.add(cond.to_id)
                elif cond.trigger == "level-up" and cond.min_happiness:
                    ids.add(cond.to_id)
        _non_wild_evolved_cache = frozenset(ids)
    return _non_wild_evolved_cache


def is_unevolved(species: Species) -> bool:
    """Nothing evolves into this species."""
    return species.prevo is None


def can_hold_eviolite(species: Species) -> bool:
    """This species itself still has somewhere left to evolve."""
    return bool(species.evolves_to)


# ---------------------------------------------------------------------------
# The roll
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WildEncounter:
    species: Species
    level: int
    fixed_level: bool  # True for legendaries/mythicals (LEGENDARY_MYTHICAL_LEVELS)
    shiny: bool
    ivs: dict[str, int]
    nature: str
    gender: str
    form_slug: str | None
    held_item: str | None
    leveled_as_rarity: str
    """The rarity tier used for the level-range roll -- usually
    `species.rarity`, but a "common upgraded to something rarer" roll (see
    `roll_wild_species`) still levels like a common spawn even though the
    species itself reports a higher rarity everywhere else (IV rolls,
    catch-rate math, ...). Mirrors the monolith's `_leveled_as` field."""

    def form(self) -> SpeciesForm | None:
        return self.species.form(self.form_slug)

    def fuzzy_level_label(self) -> str:
        return f"Lv. {self.level}" if self.fixed_level else f"~{fuzzy_level_label(self.level)}"


def roll_wild_species(
    ruleset: GenerationRuleset,
    type_hints: list[str] | None = None,
    rarity_bias: dict[str, float] | None = None,
    cr_neutral_legendary: bool = False,
) -> tuple[Species, str]:
    """Weighted-random species pick, restricted to `ruleset`'s Pokédex
    (see module docstring for the cross-gen integration point). Returns
    `(species, leveled_as_rarity)` -- see `WildEncounter.leveled_as_rarity`.

    `type_hints` (e.g. a fishing rod's Water bias) triples the weight of
    any matching-typed species; `rarity_bias` is an optional per-rarity
    multiplier (e.g. a Lure's rare/epic boost); `cr_neutral_legendary`
    skips the catch-rate weighting for legendary/mythical species (used by
    guaranteed-legendary encounters, where "hard to catch" shouldn't also
    mean "hard to *find*"). All ported verbatim from `_roll_species`.
    """
    non_wild_evolved = _non_wild_evolved_ids()
    pool = get_species_repo().for_ruleset(ruleset)

    weights: list[float] = []
    for sp in pool:
        w = float(RARITY_WEIGHTS[sp.rarity])
        if sp.id in _STARTER_BASE_IDS:
            w *= 0.33
        elif sp.id in _ALL_STARTER_IDS:
            w *= 0.04
        elif sp.id in non_wild_evolved:
            w *= 0.33
        if not (cr_neutral_legendary and sp.rarity in ("legendary", "mythical")):
            w *= 0.85 + 0.30 * (sp.catch_rate / 255)
        if type_hints and (set(sp.types) & set(type_hints)):
            w *= 3.0
        if rarity_bias:
            w *= rarity_bias.get(sp.rarity, 1.0)
        weights.append(max(0.01, w))

    chosen = random.choices(pool, weights=weights, k=1)[0]
    if chosen.rarity == "common" and random.random() < _COMMON_UPGRADE_CHANCE:
        upgrade_pool = [
            sp for sp in pool
            if sp.rarity in ("uncommon", "rare") and is_unevolved(sp)
            and (not type_hints or (set(sp.types) & set(type_hints)))
        ]
        if upgrade_pool:
            return random.choice(upgrade_pool), "common"
    return chosen, chosen.rarity


def roll_wild_level(species: Species, leveled_as_rarity: str) -> tuple[int, bool]:
    """Returns `(level, fixed)`; `fixed` is True for legendaries/mythicals
    with a hardcoded level (no fuzzy-range display). Ported verbatim from
    `_roll_wild_level` -- the monolith's dead `species.get("wild_level_range")`
    override branch (never actually populated by any data-building path)
    is dropped rather than ported; see TRACK_B_HANDOFF_2.md."""
    fixed = LEGENDARY_MYTHICAL_LEVELS.get(species.id)
    if fixed is not None:
        return fixed, True
    lo, hi = LEVEL_RANGES_BY_RARITY.get(leveled_as_rarity, (2, 40))
    return (lo if lo == hi else random.randint(lo, hi)), False


def fuzzy_level_label(level: int) -> str:
    """A deliberately-imprecise level range for the spawn embed (real level
    is only revealed once battled/caught). Ported verbatim from
    `_fuzzy_level_label`."""
    span = 6 if level < 15 else 7 if level < 35 else 8 if level < 55 else 10
    offset = random.randint(0, min(min(span, level - 1), 100 - level))
    lo = level - offset
    return f"{lo}\u2013{lo + span}"


def roll_ivs(floor: int = 0, ceiling: int = 31, rarity: str | None = None) -> dict[str, int]:
    """Ported verbatim from `_roll_ivs`: legendaries/mythicals always get 3
    guaranteed-31 stats on top of the normal roll."""
    ivs = {k: random.randint(floor, ceiling) for k in STAT_KEYS}
    if rarity in ("legendary", "mythical"):
        for stat in random.sample(STAT_KEYS, 3):
            ivs[stat] = 31
    return ivs


def pick_ability(species: Species, force_hidden: bool = False) -> str:
    """10% chance of the hidden ability (or guaranteed, if `force_hidden`
    -- used by e.g. an Ability Capsule/Patch flow). Ported verbatim from
    `_pick_ability`."""
    non_hidden = [a.name for a in species.abilities if not a.is_hidden]
    hidden = [a.name for a in species.abilities if a.is_hidden]
    if force_hidden and hidden:
        return random.choice(hidden)
    if hidden and random.randint(1, 10) == 1:
        return random.choice(hidden)
    return random.choice(non_hidden) if non_hidden else ""


def roll_gender(species: Species) -> str:
    """Ported verbatim from `_roll_gender` (PokeAPI's eighths-female convention)."""
    gr = species.gender_rate
    if gr is None or gr == -1:
        return "genderless"
    if gr <= 0:
        return "male"
    if gr >= 8:
        return "female"
    return "female" if random.randint(1, 8) <= gr else "male"


def roll_form(species: Species, gender: str) -> str | None:
    """Returns a form slug (or `None` for the base form). Ported verbatim
    from `_roll_form`, plus the gendered-appearance reconciliation that
    `spawn_pokemon` used to do as a separate step right after calling it
    (folded in here since it's still just "what form did we land on")."""
    if species.id in SEASONAL_FORM_SPECIES:
        suffix = SEASON_SUFFIX_BY_MONTH[datetime.now().month]
        match = next((f for f in species.forms if f.slug.endswith(suffix)), None)
        return match.slug if match else None

    weights = RANDOM_FORM_WEIGHTS.get(species.id)
    if weights:
        chosen_slug = random.choices([w[0] for w in weights], weights=[w[1] for w in weights], k=1)[0]
        if chosen_slug is not None and species.form(chosen_slug) is not None:
            return chosen_slug
        return None

    gender_forms = GENDER_FORM_SPECIES.get(species.id)
    if gender_forms:
        slug = gender_forms.get(gender)
        if slug and species.form(slug) is not None:
            return slug

    return None


def roll_wild_held_item(species: Species) -> str | None:
    """Ported verbatim from `_roll_wild_held_item`."""
    from pokemon.dex.items_repo import EVOLUTION_ITEMS  # local import: avoids a
    # module-load-order dependency for a rarely-hit branch (a 3% stone-drop
    # chance) rather than importing the whole item catalog up front.

    evo_repo = get_evolution_repo()
    if random.random() < 0.03:
        stone_evo = next(
            (e for e in evo_repo.evolutions_from(species.id) if e.trigger == "use-item" and e.item), None)
        if stone_evo:
            item_name = stone_evo.item.replace("-", " ").title()
            if item_name in EVOLUTION_ITEMS:
                return item_name

    if random.random() >= _RARITY_ITEM_CHANCE.get(species.rarity, 0.05):
        return None

    if random.random() < _RARITY_PREMIUM_CHANCE.get(species.rarity, 0.0):
        pool = list(_PREMIUM_HELD_ITEMS)
        if species.bst < 350:
            pool = [i for i in pool if i not in _GLASS_CANNON_ITEMS]
        if can_hold_eviolite(species) and random.random() < 0.3:
            pool.append("Eviolite")
        if pool:
            return random.choice(pool)

    primary_type = species.types[0] if species.types else "Normal"
    pool = list(_WILD_HELD_ITEM_BY_TYPE.get(primary_type, ["Sitrus Berry"]))
    if "Eviolite" in pool and not can_hold_eviolite(species):
        pool.remove("Eviolite")
    return random.choice(pool) if pool else None


def roll_wild_encounter(
    ruleset: GenerationRuleset,
    *,
    type_hints: list[str] | None = None,
    rarity_bias: dict[str, float] | None = None,
    cr_neutral_legendary: bool = False,
) -> WildEncounter:
    """The single entry point every spawn source should call. Equivalent
    to the roll half of the monolith's `spawn_pokemon` (the embed/message/
    view half is Discord-facing and lives in `spawn_message_hook.py`)."""
    species, leveled_as = roll_wild_species(
        ruleset, type_hints=type_hints, rarity_bias=rarity_bias, cr_neutral_legendary=cr_neutral_legendary)
    shiny = random.randint(1, SHINY_ODDS) == 1
    level, fixed = roll_wild_level(species, leveled_as)
    ivs = roll_ivs(rarity=species.rarity)
    nature = random.choice(NATURES)
    gender = roll_gender(species)
    form_slug = roll_form(species, gender)
    held_item = roll_wild_held_item(species)
    return WildEncounter(
        species=species, level=level, fixed_level=fixed, shiny=shiny, ivs=ivs, nature=nature,
        gender=gender, form_slug=form_slug, held_item=held_item, leveled_as_rarity=leveled_as,
    )

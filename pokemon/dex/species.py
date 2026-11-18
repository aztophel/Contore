"""Pokédex species data: the `Species` record shape and the repository that
loads/filters it.

This module owns the *data* representation of a species (base stats, types,
abilities, breeding info, alternate forms, ...). It knows nothing about
Discord, battles, or the database — it is a pure, synchronous, in-memory
lookup layer over the committed `data/pokedex.json` produced by
`pokedex_builder.py`.

Cross-generation support: `SpeciesRepository.for_ruleset()` /
`.allowed_ids_for_ruleset()` are the integration points the rest of the bot
(spawning, team validation, `/pokedex`) should use instead of iterating
`.all()` directly, so that a server's configured
`pokemon.generations.ruleset.GenerationRuleset` (via
`ServerPokemonRules.dex_ruleset` / `.battle_ruleset`) is always respected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from pokemon.generations.ruleset import GenerationRuleset

DEFAULT_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "pokedex.json"

# Canonical base-stat keys, matching the naming already used by the frozen
# `pokemon.battle.move.StatChange` contract ("attack", "special-attack", ...)
# so nothing downstream has to translate between two stat-key vocabularies.
STAT_KEYS = ("hp", "attack", "defense", "special-attack", "special-defense", "speed")

RARITY_TIERS = ("common", "uncommon", "rare", "epic", "legendary", "mythical")


@dataclass(frozen=True, slots=True)
class SpeciesForm:
    """An alternate or Mega form of a species (Deoxys-Defense, Charizard-Mega-X, ...)."""

    slug: str
    name: str
    types: tuple[str, ...]
    base_stats: dict[str, int]
    abilities: tuple[AbilitySlot, ...]
    is_mega: bool = False
    is_battle_only: bool = False  # trigger-only forms (Aegislash-Blade, weather forms, ...)

    @property
    def bst(self) -> int:
        return sum(self.base_stats.values())


@dataclass(frozen=True, slots=True)
class AbilitySlot:
    name: str
    is_hidden: bool = False


@dataclass(frozen=True, slots=True)
class EvolutionLink:
    """One outgoing evolution edge; the richer condition data lives in
    `pokemon.dex.evolution.EvolutionRepository` — this is just enough to
    walk the chain for display/breeding purposes without a second lookup."""

    to_id: int
    to_name: str


@dataclass(frozen=True, slots=True)
class Species:
    id: int
    name: str
    types: tuple[str, ...]
    base_stats: dict[str, int]
    abilities: tuple[AbilitySlot, ...]

    generation_introduced: int
    rarity: str = "common"

    catch_rate: int = 45
    growth_rate: str = "medium"
    base_friendship: int = 70
    hatch_counter: int = 20
    gender_rate: int = -1  # -1 genderless, 0-8 eighths female, per PokeAPI convention
    egg_groups: tuple[str, ...] = ()

    height_m: float = 0.0
    weight_kg: float = 0.0
    effort_yield: dict[str, int] = field(default_factory=dict)
    base_experience: int = 64
    genus: str = ""
    flavor_text: str | None = None

    forms: tuple[SpeciesForm, ...] = ()
    mega_forms: tuple[SpeciesForm, ...] = ()

    prevo: int | None = None
    evolves_to: tuple[EvolutionLink, ...] = ()

    needs_pokeapi_enrichment: bool = False
    """True for entries whose economy-only fields (catch rate, growth rate,
    flavor text, ...) are still builder defaults because they were sourced
    from the offline Showdown pass rather than a live PokeAPI run. See
    `pokemon/dex/pokedex_builder.py`."""

    @property
    def bst(self) -> int:
        return sum(self.base_stats.values())

    def is_genderless(self) -> bool:
        return self.gender_rate == -1

    def form(self, slug: str | None) -> SpeciesForm | None:
        if not slug:
            return None
        for f in (*self.forms, *self.mega_forms):
            if f.slug == slug:
                return f
        return None

    def display_types(self, form_slug: str | None = None) -> tuple[str, ...]:
        f = self.form(form_slug)
        return f.types if f else self.types

    def display_base_stats(self, form_slug: str | None = None) -> dict[str, int]:
        f = self.form(form_slug)
        return f.base_stats if f else self.base_stats


def _abilities_from_dict(raw: list[dict]) -> tuple[AbilitySlot, ...]:
    return tuple(AbilitySlot(name=a["name"], is_hidden=bool(a.get("is_hidden"))) for a in raw)


def _form_from_dict(raw: dict) -> SpeciesForm:
    return SpeciesForm(
        slug=raw["slug"],
        name=raw["name"],
        types=tuple(raw["types"]),
        base_stats=dict(raw["base_stats"]),
        abilities=_abilities_from_dict(raw.get("abilities", [])),
        is_mega=bool(raw.get("is_mega")),
        is_battle_only=bool(raw.get("is_battle_only")),
    )


def _species_from_dict(raw: dict) -> Species:
    return Species(
        id=raw["id"],
        name=raw["name"],
        types=tuple(raw["types"]),
        base_stats=dict(raw["base_stats"]),
        abilities=_abilities_from_dict(raw.get("abilities", [])),
        generation_introduced=raw.get("generation_introduced", 1),
        rarity=raw.get("rarity", "common"),
        catch_rate=raw.get("catch_rate", 45),
        growth_rate=raw.get("growth_rate", "medium"),
        base_friendship=raw.get("base_friendship", 70),
        hatch_counter=raw.get("hatch_counter", 20),
        gender_rate=raw.get("gender_rate", -1),
        egg_groups=tuple(raw.get("egg_groups", ())),
        height_m=raw.get("height_m", 0.0),
        weight_kg=raw.get("weight_kg", 0.0),
        effort_yield=dict(raw.get("effort_yield", {})),
        base_experience=raw.get("base_experience", 64),
        genus=raw.get("genus", ""),
        flavor_text=raw.get("flavor_text"),
        forms=tuple(_form_from_dict(f) for f in raw.get("forms", ())),
        mega_forms=tuple(_form_from_dict(f) for f in raw.get("mega_forms", ())),
        prevo=raw.get("prevo"),
        evolves_to=tuple(EvolutionLink(**e) for e in raw.get("evolves_to", ())),
        needs_pokeapi_enrichment=bool(raw.get("needs_pokeapi_enrichment")),
    )


class SpeciesRepository:
    """In-memory Pokédex lookup, loaded once from `data/pokedex.json`.

    Not a singleton by design — tests (and the builder's own self-check)
    can construct one against an arbitrary path. The bot process should
    still only ever build one instance; see `get_default_repository()`.
    """

    def __init__(self, species: Iterable[Species]):
        self._by_id: dict[int, Species] = {}
        self._by_name: dict[str, Species] = {}
        self._ruleset_cache: dict[tuple[int, int], frozenset[int]] = {}
        for sp in species:
            self._by_id[sp.id] = sp
            self._by_name[sp.name.lower()] = sp

    @classmethod
    def load(cls, path: Path | str = DEFAULT_DATA_PATH) -> "SpeciesRepository":
        path = Path(path)
        with path.open(encoding="utf-8") as fh:
            raw_list = json.load(fh)
        return cls(_species_from_dict(r) for r in raw_list)

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Species]:
        return iter(self._by_id.values())

    def all(self) -> tuple[Species, ...]:
        return tuple(self._by_id.values())

    def get(self, dex_id: int) -> Species | None:
        return self._by_id.get(dex_id)

    def get_or_raise(self, dex_id: int) -> Species:
        sp = self._by_id.get(dex_id)
        if sp is None:
            raise KeyError(f"No species with national dex number {dex_id}")
        return sp

    def by_name(self, name: str) -> Species | None:
        return self._by_name.get(name.strip().lower())

    def search(self, query: str, limit: int = 25) -> list[Species]:
        """Autocomplete-friendly fuzzy-ish search: id prefix, exact name,
        then substring name matches, in that priority order."""
        query = query.strip().lower()
        if not query:
            return list(self.all())[:limit]

        results: list[Species] = []
        seen: set[int] = set()

        if query.isdigit():
            for sp in self._by_id.values():
                if str(sp.id).startswith(query) and sp.id not in seen:
                    results.append(sp)
                    seen.add(sp.id)

        exact = self._by_name.get(query)
        if exact and exact.id not in seen:
            results.append(exact)
            seen.add(exact.id)

        for sp in self._by_id.values():
            if len(results) >= limit:
                break
            if sp.id in seen:
                continue
            if query in sp.name.lower():
                results.append(sp)
                seen.add(sp.id)

        return results[:limit]

    # -- generation-ruleset-aware views -------------------------------------

    def allowed_ids_for_ruleset(self, ruleset: GenerationRuleset) -> frozenset[int]:
        key = (ruleset.number, ruleset.max_dex_number)
        cached = self._ruleset_cache.get(key)
        if cached is not None:
            return cached
        allowed = frozenset(
            sp.id
            for sp in self._by_id.values()
            if sp.id <= ruleset.max_dex_number and sp.generation_introduced <= ruleset.number
        )
        self._ruleset_cache[key] = allowed
        return allowed

    def for_ruleset(self, ruleset: GenerationRuleset) -> list[Species]:
        """Every species legal for the given ruleset (used for spawning /
        Pokédex listings). Order matches insertion (dex-number) order."""
        allowed = self.allowed_ids_for_ruleset(ruleset)
        return [sp for sp in self._by_id.values() if sp.id in allowed]

    def is_allowed(self, dex_id: int, ruleset: GenerationRuleset) -> bool:
        return ruleset.species_allowed(dex_id) and dex_id in self._by_id


_default_repository: SpeciesRepository | None = None


def get_default_repository() -> SpeciesRepository:
    """Process-wide lazily-loaded repository over `data/pokedex.json`.

    Cogs should call this once in `cog_load`/`setup` and hold the reference
    rather than calling it per-command; it's cheap after the first call but
    there's no reason to re-look-up a module global repeatedly.
    """
    global _default_repository
    if _default_repository is None:
        _default_repository = SpeciesRepository.load()
    return _default_repository

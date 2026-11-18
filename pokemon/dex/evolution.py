"""Evolution chain lookups, loaded from the committed `data/evolution_chains.json`.

Kept separate from `species.py` (which only stores the flat prevo/next-id
links needed to *walk* a chain) because the full condition data — level,
item, held item, time of day, known move, happiness, trade, region, and so
on — is bulky and mostly only needed when actually deciding "can this
Pokémon evolve right now", not every time a `Species` is loaded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "evolution_chains.json"

# Mirrors the trigger vocabulary the original monolith derived from PokeAPI
# evolution-chain data (`level-up`, `use-item`, `trade`, `shed`, ...).
EvolutionTrigger = str


@dataclass(frozen=True, slots=True)
class EvolutionCondition:
    to_id: int
    to_name: str
    trigger: EvolutionTrigger

    min_level: int | None = None
    min_happiness: int | None = None
    time_of_day: str | None = None  # "day" | "night" | None
    item: str | None = None
    held_item: str | None = None
    known_move: str | None = None
    known_move_type: str | None = None
    gender: int | None = None  # 1 female, 2 male (PokeAPI convention)
    needs_rain: bool = False
    relative_physical_stats: int | None = None  # -1 def>atk, 0 equal, 1 atk>def
    party_species: int | None = None
    party_type: str | None = None
    trade_species: int | None = None

    # Conditions Contore has no gameplay way to fulfil (region checks,
    # location checks, beauty, step counters, ...). Kept for completeness /
    # display; `pokemon.commands.breeding` and friends should treat any
    # condition with one of these set as "not currently achievable" the same
    # way the original monolith's `_sanitize_unfulfillable_evolutions` did.
    location: str | None = None
    near_special_rock: bool = False
    min_beauty: int | None = None
    min_steps: int | None = None
    min_damage_taken: int | None = None
    min_move_count: int | None = None
    needs_multiplayer: bool = False
    turn_upside_down: bool = False
    region: str | None = None

    @property
    def is_fulfillable(self) -> bool:
        return not (
            self.location
            or self.near_special_rock
            or self.min_beauty
            or self.min_steps
            or self.min_damage_taken
            or self.min_move_count
            or self.needs_multiplayer
            or self.turn_upside_down
            or self.region
        )


class EvolutionRepository:
    def __init__(self, chains: dict[int, list[EvolutionCondition]]):
        self._chains = chains

    @classmethod
    def load(cls, path: Path | str = DEFAULT_DATA_PATH) -> "EvolutionRepository":
        path = Path(path)
        if not path.exists():
            return cls({})
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        chains = {
            int(from_id): [EvolutionCondition(**entry) for entry in entries]
            for from_id, entries in raw.items()
        }
        return cls(chains)

    def evolutions_from(self, species_id: int) -> list[EvolutionCondition]:
        return list(self._chains.get(species_id, ()))

    def full_chain_text(self, species_id: int, species_names: dict[int, str]) -> list[tuple[int, str, int]]:
        """Flattened (species_id, display_name, depth) walk of the whole
        chain containing `species_id`, useful for `/pokemon species`."""
        # find the root of the chain
        reverse: dict[int, int] = {}
        for from_id, entries in self._chains.items():
            for e in entries:
                reverse[e.to_id] = from_id
        root = species_id
        seen: set[int] = set()
        while root in reverse and root not in seen:
            seen.add(root)
            root = reverse[root]

        out: list[tuple[int, str, int]] = []

        def walk(sid: int, depth: int, visited: set[int]):
            if sid in visited or depth > 6:
                return
            visited.add(sid)
            out.append((sid, species_names.get(sid, f"#{sid}"), depth))
            for e in self._chains.get(sid, ()):
                walk(e.to_id, depth + 1, visited)

        walk(root, 0, set())
        return out


_default_repository: EvolutionRepository | None = None


def get_default_repository() -> EvolutionRepository:
    global _default_repository
    if _default_repository is None:
        _default_repository = EvolutionRepository.load()
    return _default_repository

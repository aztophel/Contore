"""Per-species movepool lookups, loaded from the committed
`data/learnsets.json` (see `pokedex_builder.build_learnsets_from_showdown`
for how it's produced -- it didn't exist as a data file before this pass;
nothing in the original scaffold could actually assign a wild/starter/bred
Pokémon a starting moveset without it).

Deliberately its own small repository (mirroring `evolution.py`'s split
from `species.py`) rather than a field on `Species`: a movepool is bulky
and, unlike base stats/types/abilities, is never needed just to *display*
a species -- only when actually rolling or teaching a moveset.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "learnsets.json"


@dataclass(frozen=True, slots=True)
class Learnset:
    species_id: int
    level_moves: tuple[tuple[int, str], ...] = ()  # (level, move_slug), sorted by level
    egg_moves: tuple[str, ...] = ()
    tm_moves: tuple[str, ...] = ()
    tutor_moves: tuple[str, ...] = ()

    def moves_at_or_below(self, level: int) -> list[str]:
        """Level-up moves learnable by `level`, in level order (ties broken
        by original ordering) -- the shape `moveset.roll_initial_moveset`
        and `/pokemon relearn` both want."""
        return [slug for lvl, slug in self.level_moves if lvl <= level]

    def moves_at_exactly(self, level: int) -> list[str]:
        """Moves newly learnable at exactly this level (level-up notifications)."""
        return [slug for lvl, slug in self.level_moves if lvl == level]

    def all_learnable(self) -> set[str]:
        """Every move this species can ever know by any method this bot
        tracks -- used for breeding egg-move validation and the movedex
        autocomplete on `/pokemon relearn`."""
        return {slug for _, slug in self.level_moves} | set(self.egg_moves) \
            | set(self.tm_moves) | set(self.tutor_moves)


def _learnset_from_dict(species_id: int, raw: dict) -> Learnset:
    return Learnset(
        species_id=species_id,
        level_moves=tuple((lvl, slug) for lvl, slug in raw.get("level_moves", ())),
        egg_moves=tuple(raw.get("egg_moves", ())),
        tm_moves=tuple(raw.get("tm_moves", ())),
        tutor_moves=tuple(raw.get("tutor_moves", ())),
    )


_EMPTY = Learnset(species_id=-1)


class LearnsetRepository:
    def __init__(self, learnsets: dict[int, Learnset]):
        self._by_id = learnsets

    @classmethod
    def load(cls, path: Path | str = DEFAULT_DATA_PATH) -> "LearnsetRepository":
        path = Path(path)
        if not path.exists():
            return cls({})
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls({int(sid): _learnset_from_dict(int(sid), entry) for sid, entry in raw.items()})

    def get(self, species_id: int) -> Learnset:
        """Never raises -- a species with no data on file (e.g. one added
        to the dex before a builder re-run) just has an empty movepool,
        which `moveset.roll_initial_moveset`'s Tackle fallback already
        handles the same way the monolith did."""
        return self._by_id.get(species_id, _EMPTY)

    def __len__(self) -> int:
        return len(self._by_id)


_default_repository: LearnsetRepository | None = None


def get_default_repository() -> LearnsetRepository:
    global _default_repository
    if _default_repository is None:
        _default_repository = LearnsetRepository.load()
    return _default_repository

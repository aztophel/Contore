"""Ability *display* metadata (short effect blurb for `/pokemon species`).

Same split as `moves_repo.py`: `pokemon.battle.ability.AbilityRegistry` (a
frozen-contract file, owned by Track D) is the source of truth for how an
ability actually behaves in battle; this module just has words to print.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "ability_flavor_text.json"
DEFAULT_SLUGS_PATH = Path(__file__).resolve().parents[2] / "data" / "abilities.json"


def display_name(slug: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", slug.lower()).strip().title()


@dataclass(frozen=True, slots=True)
class AbilityFlavorText:
    slug: str
    flavor_text: str | None = None


class AbilityFlavorTextRepository:
    def __init__(self, entries: dict[str, str]):
        self._entries = entries

    @classmethod
    def load(cls, path: Path | str = DEFAULT_DATA_PATH) -> "AbilityFlavorTextRepository":
        path = Path(path)
        if not path.exists():
            return cls({})
        with path.open(encoding="utf-8") as fh:
            return cls(json.load(fh))

    def get(self, slug: str) -> str | None:
        return self._entries.get(slug)


def load_known_ability_slugs(path: Path | str = DEFAULT_SLUGS_PATH) -> list[str]:
    """The flat slug list `pokemon.battle.ability.AbilityRegistry.load()`
    expects. Kept here (rather than duplicated at every call site) since
    dex/ is the module that owns `data/abilities.json`."""
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


_default_repository: AbilityFlavorTextRepository | None = None


def get_default_repository() -> AbilityFlavorTextRepository:
    global _default_repository
    if _default_repository is None:
        _default_repository = AbilityFlavorTextRepository.load()
    return _default_repository

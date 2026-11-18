"""Move *display* metadata: flavor text and a human-friendly name.

Deliberately separate from `pokemon.battle.registry.MoveRegistry`, which
owns move *mechanics* (power, accuracy, the `Move` hook object, ...) and is
frozen-contract territory. This module only answers "what should `/pokemon
move` print", and is safe for `pokemon/commands` to depend on without ever
reaching into battle internals.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "move_flavor_text.json"

# A handful of move names PokeAPI/Showdown slugs don't title-case cleanly
# (hyphenated proper nouns, apostrophes). Anything not listed here just
# falls back to `slug.replace("-", " ").title()`.
_DISPLAY_NAME_OVERRIDES: dict[str, str] = {
    "u-turn": "U-turn",
    "x-scissor": "X-Scissor",
    "will-o-wisp": "Will-O-Wisp",
    "soft-boiled": "Soft-Boiled",
    "double-edge": "Double-Edge",
    "self-destruct": "Self-Destruct",
    "freeze-dry": "Freeze-Dry",
    "lock-on": "Lock-On",
    "wake-up-slap": "Wake-Up Slap",
    "kings-shield": "King's Shield",
    "forests-curse": "Forest's Curse",
    "trick-or-treat": "Trick-or-Treat",
    "topsy-turvy": "Topsy-Turvy",
    "baby-doll-eyes": "Baby-Doll Eyes",
    "power-up-punch": "Power-Up Punch",
}


def display_name(slug: str) -> str:
    if slug in _DISPLAY_NAME_OVERRIDES:
        return _DISPLAY_NAME_OVERRIDES[slug]
    return re.sub(r"[^a-z0-9]+", " ", slug.lower()).strip().title()


@dataclass(frozen=True, slots=True)
class MoveFlavorText:
    slug: str
    flavor_text: str | None = None


class MoveFlavorTextRepository:
    def __init__(self, entries: dict[str, str]):
        self._entries = entries

    @classmethod
    def load(cls, path: Path | str = DEFAULT_DATA_PATH) -> "MoveFlavorTextRepository":
        path = Path(path)
        if not path.exists():
            return cls({})
        with path.open(encoding="utf-8") as fh:
            return cls(json.load(fh))

    def get(self, slug: str) -> str | None:
        return self._entries.get(slug)


_default_repository: MoveFlavorTextRepository | None = None


def get_default_repository() -> MoveFlavorTextRepository:
    global _default_repository
    if _default_repository is None:
        _default_repository = MoveFlavorTextRepository.load()
    return _default_repository

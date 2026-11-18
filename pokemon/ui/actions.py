"""What BattleView submits to BattleEngine each turn. engine.py doesn't
exist yet (it's Track C's Phase-3 file), so this is Track D's proposal for
its input contract -- small and data-only on purpose, so adopting it
wholesale is a five-minute job and adjusting it is a one-file diff, not a
redesign. See pokemon/battle/abilities/NOTES_FOR_TRACK_C.md for the
matching notes on BattleEngine's other assumed surface (engine.abilities,
engine.moves, engine.events, engine.log, engine.field, engine.turn_number
-- all already implied by context.py).

Assumed engine methods this module's callers (battle_view.py) rely on:
  - engine.legal_moves(battler) -> list[LegalMove]
  - engine.legal_switches(side) -> list[Battler]
  - await engine.submit_action(side, action: BattleAction) -> None
  - engine.ready_to_resolve -> bool  (True once every side has submitted)
  - await engine.resolve_turn() -> None  (mutates state, appends to engine.log)
  - engine.is_over -> bool
  - engine.winner -> Side | None
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..battle.battler import Battler
    from ..battle.move import MoveCategory


@dataclass(frozen=True)
class LegalMove:
    """What legal_moves() returns per move slot -- everything BattleView
    needs to render a move button/option without reaching into
    battler.py/move.py internals itself."""
    slug: str
    display_name: str
    type_name: str
    category: "MoveCategory"
    pp_current: int
    pp_max: int
    disabled: bool = False
    disabled_reason: str | None = None   # "Disabled", "Choice-locked", "Taunted", ...
    needs_target_prompt: bool = False    # from targeting.needs_target_prompt()


@dataclass(frozen=True)
class MoveAction:
    user: "Battler"
    move_slug: str
    targets: tuple["Battler", ...] = ()  # empty = let targeting.py auto-resolve


@dataclass(frozen=True)
class SwitchAction:
    user: "Battler"
    replacement: "Battler"


@dataclass(frozen=True)
class ItemAction:
    user: "Battler"
    item_name: str
    target: "Battler | None" = None
    # The human whose bag the item comes out of (a side can hold two
    # trainers in Doubles 2v2). None only for AI-side use.
    trainer_id: int | None = None


@dataclass(frozen=True)
class ForfeitAction:
    user: "Battler"


BattleAction = MoveAction | SwitchAction | ItemAction | ForfeitAction

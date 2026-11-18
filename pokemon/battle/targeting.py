"""Target resolution -- the single implementation of "who does this move
hit," replacing the old `mode` string ('single' / 'auto' / 'ally') that
BattleView threaded by hand through _on_move_selected, _execute_move, and a
handful of other call sites, each keeping its own slightly-different copy
of the same branching logic.

Both the UI (deciding whether to prompt the player for a target) and the
engine (deciding who actually gets hit when the move resolves) call this one
function, driven entirely by the move's `MoveTarget` -- itself just
PokeAPI's `move.target` field, so a migrated move needs zero extra
targeting code as long as its MoveSpec came from the cached data.
"""
from __future__ import annotations
import random
from typing import TYPE_CHECKING

from .move import MoveTarget

if TYPE_CHECKING:
    from .battler import Battler
    from .context import MoveContext
    from .field import Side


def needs_target_prompt(target: MoveTarget, side: "Side") -> bool:
    """Should the UI ask the player to pick a target, or is it implied?
    (Singles never prompts even for SELECTED_POKEMON, since there's only
    one legal opponent; Doubles does.)"""
    if target not in (MoveTarget.SELECTED_POKEMON, MoveTarget.USER_OR_ALLY):
        return False
    return len([b for b in side.active if b is not None]) > 1


def target_choices(target: MoveTarget, user: "Battler", user_side: "Side", opp_side: "Side") -> list["Battler"]:
    """Who the player may AIM a prompted move at (the picker's buttons): any other living Pokemon for a selected-target move (foes first, then the ally), the living ally for an
    ally-target move. `[]` / one entry means there is nothing to choose (the caller submits without asking; `resolve_targets` then picks / fails as Showdown does)."""
    def alive(side: "Side") -> list["Battler"]:
        return [b for b in side.active if b is not None and not b.fainted]
    allies = [b for b in alive(user_side) if b is not user]
    if target is MoveTarget.USER_OR_ALLY:
        return allies
    return alive(opp_side) + allies


def resolve_targets(target: MoveTarget, ctx: "MoveContext") -> list["Battler"]:
    field = ctx.battle.field
    user = ctx.user
    user_side = field.side_of(user)
    opp_side = field.other_side(user_side)

    def alive(battlers: list["Battler | None"]) -> list["Battler"]:
        return [b for b in battlers if b is not None and not b.fainted]

    if target is MoveTarget.SELECTED_POKEMON:
        # If the UI already collected an explicit choice, ctx.targets is
        # pre-populated -- just re-validate it's still legal (the original
        # target may have fainted/switched since the player picked it).
        # Otherwise (AI opponents, or Singles with only one legal target)
        # pick automatically.
        if ctx.targets:
            live = [t for t in ctx.targets if not t.fainted]
            if live:
                return live
            # Showdown `getTarget`: a chosen FOE that already fainted this turn is replaced by a random living adjacent foe (a fainted ALLY is not: the move just fails)
            if all(field.side_of(t) is opp_side for t in ctx.targets):
                foes = alive(opp_side.active)
                return [random.choice(foes)] if foes else []
            return []
        return alive(opp_side.active)[:1]

    if target is MoveTarget.RANDOM_OPPONENT:
        opponents = alive(opp_side.active)
        return [random.choice(opponents)] if opponents else []

    if target is MoveTarget.ALL_OPPONENTS:
        return alive(opp_side.active)

    if target is MoveTarget.ALL_OTHER_POKEMON:
        return alive(opp_side.active) + [b for b in alive(user_side.active) if b is not user]

    if target is MoveTarget.USER:
        return [user]

    if target is MoveTarget.USER_OR_ALLY:
        if ctx.targets:
            return [t for t in ctx.targets if not t.fainted]
        allies = [b for b in alive(user_side.active) if b is not user]
        return allies[:1] or [user]

    if target is MoveTarget.USER_AND_ALLIES:
        return alive(user_side.active)

    if target is MoveTarget.ALL_POKEMON:
        return alive(user_side.active) + alive(opp_side.active)

    if target is MoveTarget.OPPONENTS_FIELD:
        # Stealth Rock / Spikes / Toxic Spikes / Sticky Web (Showdown `target: "foeSide"`): they act on the FOE'S SIDE, but their `on_hit` finds that side through `ctx.target_side(target)`,
        # so they need one living foe to point at. This returned [] before, which the engine reads as "no target": EVERY entry hazard just printed "But it failed!".
        return alive(opp_side.active)[:1]

    # USERS_FIELD / ENTIRE_FIELD act on a Side/Field, not
    # individual Battlers -- moves with these targets don't call
    # get_targets() for hit-resolution purposes; they act directly on
    # ctx.user_side / ctx.battle.field inside on_hit (see StealthRockMove).
    return []

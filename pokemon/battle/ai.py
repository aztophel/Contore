"""A minimal, honest placeholder for non-player-controlled sides (wild
Pokemon, boss trainers) to pick a turn action, so a battle's turn loop
actually completes without a human sitting on both sides of it.

This is NOT a port of the monolith's boss AI (`choose_boss_action`/
`_boss_matchup_score`/`_boss_target_score`/`_move_can_likely_ko`/
`_ai_expected_damage`, contore.py) -- that heuristic is tightly coupled to
the old `FighterState`/`BattleField` model (expected-damage rolls
accounting for STAB/crit/weather/ability modifiers, matchup-scored
switching, weather counter-play, mega-evolution timing) and porting it
faithfully onto `Battler`/`Field`/`BattleEngine` is real, scoped work of
its own -- not something to rush as a side effect of wiring up
`hunt.py`/`duel.py`'s battle-start integration. Until that port happens,
every non-player side -- wild encounters AND boss battles alike -- uses
this same simple heuristic: every legal damaging move is scored by
(base power x type effectiveness x STAB), the best-scoring one is used;
a moveset of nothing but status moves (or ties) falls back to the first
legal move. When a faint forces a replacement the engine is waiting on,
`choose_replacement` picks the bench Pokemon with the best offensive +
defensive type matchup against the foe. It never VOLUNTARILY switches
mid-battle, uses items, or reads weather/ability/HP -- those remain the
open gap vs. the monolith's `choose_boss_action`.

Battles are playable end-to-end with this -- they're just not smart
opponents yet. `choose_action` is the one seam `pokemon.ui.battle_view`
calls for every non-player side, so replacing this with a real port later
(wild and boss will likely want different aggression levels once ported)
is a one-file change, not a redesign.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pokemon.battle.move import MoveCategory, MoveTarget
from pokemon.battle.types import type_effectiveness
from pokemon.ui.actions import BattleAction, MoveAction, SwitchAction

if TYPE_CHECKING:
    from pokemon.battle.engine import BattleEngine
    from pokemon.battle.field import Side


def choose_action(engine: "BattleEngine", side: "Side", opp_side: "Side") -> BattleAction:
    active = next((b for b in side.active if b is not None and not b.fainted), None)
    if active is None:
        raise ValueError("choose_action called for a side with no living active battler"
                         " (use choose_next_action, which handles a pending replacement)")
    opp = next((b for b in opp_side.active if b is not None and not b.fainted), None)

    legal = engine.legal_moves(active)
    usable = [m for m in legal if not m.disabled] or list(legal)
    if not usable:
        raise ValueError("engine.legal_moves() returned nothing at all, not even Struggle")

    def score(m) -> float:
        if m.category is MoveCategory.STATUS:
            return 1.0  # keep status moves on the table, just deprioritized
        move = engine.moves.get(m.slug)
        power = move.spec.base_power or 60  # variable-power moves: assume moderate
        eff = type_effectiveness(m.type_name, opp.identity.types, ruleset=engine.ruleset) if opp else 1.0
        stab = 1.5 if m.type_name in active.identity.types else 1.0
        return power * eff * stab

    best = max(usable, key=score)
    return MoveAction(user=active, move_slug=best.slug)


def _move_score(engine: "BattleEngine", slug: str, type_name: str, user, opp) -> float:
    spec = engine.moves.spec(slug)
    if spec.category is MoveCategory.STATUS:
        return 0.0
    power = spec.base_power or 60
    eff = type_effectiveness(type_name, opp.identity.types, ruleset=engine.ruleset) if opp else 1.0
    stab = 1.5 if type_name in user.identity.types else 1.0
    return power * eff * stab


def _replacement_score(engine: "BattleEngine", cand, opp) -> float:
    """How good `cand` looks coming in against `opp`: its best offensive move
    score, scaled by how badly `opp`'s own types hurt it (a bench mon that
    resists the foe's STAB beats one that just hits slightly harder)."""
    offense = max(
        (_move_score(engine, s.move_slug, engine.moves.spec(s.move_slug).type_name, cand, opp)
         for s in cand.moves.slots if s.pp > 0),
        default=0.0,
    )
    if opp is None:
        return offense
    # Worst incoming hit among the foe's own types (STAB approximation).
    incoming = max(
        (type_effectiveness(t, cand.identity.types, ruleset=engine.ruleset) for t in opp.identity.types),
        default=1.0,
    )
    hp_frac = cand.vitals.hp / cand.vitals.max_hp if cand.vitals.max_hp else 1.0
    return offense * (1.0 / max(incoming, 0.25)) * (0.5 + 0.5 * hp_frac)


def choose_replacement(engine: "BattleEngine", side: "Side", opp_side: "Side") -> SwitchAction:
    """Pick which bench Pokemon to send in after a faint. Raises ValueError
    if there's nobody left (callers should only ask while the engine says
    this side is awaiting a replacement, which requires a living bench)."""
    options = engine.legal_switches(side)
    if not options:
        raise ValueError("choose_replacement called but no legal replacement exists")
    opp = next((b for b in opp_side.active if b is not None and not b.fainted), None)
    best = max(options, key=lambda c: _replacement_score(engine, c, opp))
    # SwitchAction.user is the slot being vacated: the fainted active
    # (engine._awaiting_replacement only cares about action.replacement).
    vacated = next((b for b in side.active if b is not None and b.fainted), None) or best
    return SwitchAction(user=vacated, replacement=best)


def choose_next_action(engine: "BattleEngine", side: "Side", opp_side: "Side") -> BattleAction:
    """Singles entry point: a forced replacement when the engine is waiting on one, a normal move otherwise.
    (A Doubles side owes one action per living active: use `choose_next_actions`.)"""
    if engine.is_awaiting_replacement(side):
        return choose_replacement(engine, side, opp_side)
    return choose_action(engine, side, opp_side)


def _living(battlers) -> list:
    return [b for b in battlers if b is not None and not b.fainted]


def choose_actions(engine: "BattleEngine", side: "Side", opp_side: "Side") -> list[BattleAction]:
    """One MoveAction per LIVING active on `side` (the engine resolves a turn only once every living active has one). With a single living active this is exactly `[choose_action(...)]`.
    With two actives, each picks its best (move, foe) pair; a single-target move gets that foe as an explicit target only when there are two foes to choose between."""
    actives = _living(side.active)
    if len(actives) <= 1:
        return [choose_action(engine, side, opp_side)]
    foes = _living(opp_side.active)
    actions = []
    for active in actives:
        legal = engine.legal_moves(active)
        usable = [m for m in legal if not m.disabled] or list(legal)
        if not usable:
            raise ValueError("engine.legal_moves() returned nothing at all, not even Struggle")
        best = None                                   # (score, move, foe)
        for m in usable:
            if m.category is MoveCategory.STATUS:
                cand = (1.0, m, None)
            else:
                power = engine.moves.get(m.slug).spec.base_power or 60
                stab = 1.5 if m.type_name in active.identity.types else 1.0
                scored = [(power * stab * (type_effectiveness(m.type_name, f.identity.types, ruleset=engine.ruleset)), m, f) for f in foes] or [(power * stab, m, None)]
                cand = max(scored, key=lambda t: t[0])
            if best is None or cand[0] > best[0]:
                best = cand
        _, move, foe = best
        spec = engine.moves.get(move.slug).spec
        pick_foe = foe is not None and len(foes) > 1 and spec.target is MoveTarget.SELECTED_POKEMON
        actions.append(MoveAction(user=active, move_slug=move.slug, targets=(foe,) if pick_foe else ()))
    return actions


def choose_replacements(engine: "BattleEngine", side: "Side", opp_side: "Side") -> list[SwitchAction]:
    """One SwitchAction per awaited slot (Doubles can owe two), each with a DIFFERENT bench Pokemon, the vacated slot's Pokemon as `user` (the engine keys actions by it)."""
    options = list(engine.legal_switches(side))
    if not options:
        raise ValueError("choose_replacements called but no legal replacement exists")
    foes = _living(opp_side.active)
    foe = foes[0] if foes else None
    slots = list(engine._awaiting_replacement.get(id(side)) or [])
    actions = []
    for slot in slots:
        if not options:
            break
        best = max(options, key=lambda c: _replacement_score(engine, c, foe))
        options.remove(best)
        vacated = side.active[slot] if slot < len(side.active) and side.active[slot] is not None else best
        actions.append(SwitchAction(user=vacated, replacement=best))
    return actions


def choose_next_actions(engine: "BattleEngine", side: "Side", opp_side: "Side") -> list[BattleAction]:
    """The entry point BattleView should call for an AI side: every forced replacement the engine is waiting on, or one move per living active."""
    if engine.is_awaiting_replacement(side):
        return choose_replacements(engine, side, opp_side)
    return choose_actions(engine, side, opp_side)

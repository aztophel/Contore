"""BattleEngine: the top-level turn loop. Owned by Track C. Old code:
the giant `run_battle_turn`-shaped block in contore.py that drove
apply_move once per combatant per turn, plus BattleField.tick() for the
end-of-turn phase.

Built directly against the contract pokemon/ui/actions.py and
battle_view.py already assume (legal_moves/legal_switches/submit_action/
ready_to_resolve/resolve_turn/is_over/winner) -- Track D wrote the UI
layer expecting this exact shape, which made this file's public surface
mostly a given rather than a design decision of its own.

Known simplifications (see MIGRATION_STATUS.md for the full list):
  - ItemAction (a bag item mid-battle) runs `battle/bag.py`'s `BagItem.use`
    in the item phase, before any move -- Potions, Revives, status cures,
    X-items, Ether/Elixir and Poke Balls (catching ends the battle via
    `conclude()`).
  - Doubles/multi-battler sides work structurally (Side.active is a list)
    but spread-damage reduction, ally-targeting prompts, and Follow
    Me/Rage Powder-style redirection aren't wired into the turn loop yet.
  - Quick Claw/Custap Berry's random pre-turn priority roll isn't
    implemented (no held item or ability currently touches this file's
    ordering beyond the normal priority/Speed sort).
"""
from __future__ import annotations
import random
from typing import TYPE_CHECKING

from ..ecs.events import EventBus
from .context import BattleContext, MoveContext
from .move import MoveCategory, MoveFlag, MoveTarget, is_reflectable

# Showdown targets `all` / `foeSide` / `allySide` / `allyTeam`: these moves go through `tryMoveHit` ("used only for moves that target sides/fields rather than pokemon"), which never records `attackedBy`.
_SIDE_OR_FIELD_TARGETS = frozenset((MoveTarget.ENTIRE_FIELD, MoveTarget.ALL_POKEMON, MoveTarget.OPPONENTS_FIELD, MoveTarget.USERS_FIELD, MoveTarget.USER_AND_ALLIES))


def attacker_breaks_mold_for(ctx, holder) -> bool:
    """Mold Breaker & co. ignore a BREAKABLE ability such as Lightning Rod / Storm Drain even for its redirection (Showdown's `breakable` flag)."""
    from .abilities._util import attacker_breaks_mold
    return bool(attacker_breaks_mold(ctx))
FORCE_SWITCH_MOVES = frozenset({"roar", "whirlwind", "dragon-tail", "circle-throw"})   # Showdown `forceSwitch: true`
SELF_SWITCH_MOVES = frozenset({"u-turn", "volt-switch", "flip-turn", "parting-shot", "teleport", "baton-pass"})  # Showdown `selfSwitch`
from .damage import check_accuracy, calculate_and_apply_damage, _apply_drain_recoil
from .field import GRAVITY_BANNED_MOVES
from .volatile import Attract, Confusion, Disable, Flinch, MustRecharge, ProtectionActive, Taunt

if TYPE_CHECKING:
    from .field import Field, Side
    from .battler import Battler
    from .registry import MoveRegistry, AbilityRegistry
    from .items import ItemRegistry
    from ..generations.ruleset import GenerationRuleset
    from ..ui.actions import BattleAction, LegalMove


class BattleEngine:
    def __init__(self, field: "Field", abilities: "AbilityRegistry", moves: "MoveRegistry",
                 items: "ItemRegistry", ruleset: "GenerationRuleset"):
        self.field = field
        self.abilities = abilities
        # The registry as this generation sees it (Gen 6 Tackle is 50 BP, Gen 4's is 35 BP / 95 acc, ...).
        self.moves = moves.for_generation(ruleset.number) if hasattr(moves, "for_generation") else moves
        self.items = items
        self.ruleset = ruleset
        self.events = EventBus()
        self._apply_generation_base_stats()
        self.log: list[str] = []
        self.turn_number = 0
        # Set by `conclude()`: why the battle ended early ("caught" /
        # "forfeit") and who won it; `catch` carries a successful throw's
        # details (battle/bag.py CatchOutcome) for the bridge to persist.
        self.conclusion: str | None = None
        # Optional UI hook for animations that need to show a MID-TURN
        # frame (Poke Ball shakes): `await on_throw(pause_seconds, push)`.
        # The engine neither sleeps nor knows about Discord; with no hook
        # (tests, AI-only battles) the animation collapses to its end state.
        self.on_throw = None
        self._concluded_winner: "Side | None" = None
        self.catch = None
        # Move actions still to be executed THIS turn, by battler id: what
        # Showdown's `queue.willMove(target)` answers (Sucker Punch).
        self._queued: dict[int, object] = {}
        # Showdown `lastSuccessfulMoveThisTurn` (Fusion Bolt / Fusion Flare).
        self.last_successful_move_this_turn: str | None = None
        # Showdown `battle.lastMove`: the last move that did not fail, ANYONE's (Copycat). A move that CALLED another leaves the called move here.
        self.last_move_global: str | None = None

        # This turn's chosen actions, one per ACTING POKEMON: (id(side), id(action.user)) -> action. (It used to be one per SIDE, so in Doubles only the last
        # submitted Pokemon per side ever acted. For Singles nothing changes: one acting Pokemon per side is one entry.)
        self._pending: dict[tuple[int, int], "BattleAction"] = {}
        self._awaiting_replacement: dict[int, list[int]] = {}  # id(side) -> fainted active slot indices
        # Pivot moves (U-turn, Volt Switch, Parting Shot, Teleport, Baton Pass): the move ends the user's action, the PLAYER chooses a replacement, and the
        # rest of the turn then RESUMES. `_pivot_slots` marks which awaited slots are pivots (the old Pokemon is alive: a normal switch-out, not a
        # fainted-slot fill); `_mid_turn` holds the actions still to run; `_pending_pivot` is set by the move, consumed after it finishes.
        self._pivot_slots: dict[tuple[int, int], str] = {}       # (id(side), slot) -> "switch" | "baton-pass"
        self._mid_turn: tuple | None = None
        self._pending_pivot: dict | None = None
        # Showdown targets are slot LOCATIONS: a move aimed at a Pokemon that has since left its slot this turn (fainted and replaced mid-turn in Gen 3, or pivoted out) hits the slot's new occupant.
        self._left_slot: dict[int, tuple] = {}          # id(Battler that left) -> (side, slot index); cleared at the end of each turn
        self._qmeta: dict[int, dict] = {}               # id(MoveAction) -> {order, tie, source}: Showdown's action `order` (200 = a move; After You 3, Quash 201), the speed-tie roll, the `sourceEffect` (Round)
        self._queue: list | None = None                 # the turn's REMAINING (user, action) list while `_run_actions` walks it, so After You / Quash / Round can edit it
        # [ADDITIVE] Whether the opening lineup's entry abilities
        # (Intimidate, Drought, Download, Frisk, Forewarn, ...) have
        # fired yet -- see resolve_turn's handling of this. Found missing
        # via this session's own final smoke test: __init__ already
        # attaches each opening Battler's ability (below), but attaching
        # it is not the same as dispatching on_switch_in for it, and
        # nothing else was doing that for the battle's initial lineup
        # specifically (only _do_switch_in, for a LATER switch, did).
        self._battle_started = False

        # Every Battler already placed before the engine exists (i.e. each
        # side's opening lead) needs its ability/item wired up too -- see
        # sync_ability_and_item()'s docstring for why nothing does this
        # automatically.
        for battler in self.field.all_battlers():
            self.sync_ability_and_item(battler)

    def sync_ability_and_item(self, battler: "Battler") -> None:
        """Attach `battler`'s CURRENT ability/held item as dispatchable
        Effect components, so their hooks actually participate in
        consult()/consult_first()/consult_reduce() and the global event
        bus. Nothing does this automatically: Ability/HeldItem instances
        are process-lifetime singletons looked up by slug (see
        registry.py's and items.py's docstrings), never created per-
        Battler, so something has to explicitly attach the right one the
        moment a Pokemon is actually on the field -- there is no other
        code path that would do it. Safe to call again any time
        ability/item might have changed (Skill Swap, Trick, a fresh bench
        Pokemon switching in, ...): Entity.add() overwrites the previous
        Ability/HeldItem Component for that slot, and the OLD one is
        explicitly unregistered from the bus first so a stale instance
        doesn't keep reacting to events for a Pokemon that no longer has
        it.

        Found via live-testing this session: Sturdy and Intimidate (and
        every other ability/item hook) were silently inert without this --
        `_ordered_effects()` only ever walks components actually attached
        to that specific Battler, and nothing was attaching them."""
        from .ability import Ability
        from .items import HeldItem
        # _components is keyed by the CONCRETE class (Sturdy, Intimidate,
        # ...), never the Ability/HeldItem base -- so finding "whatever
        # ability this Battler currently has" has to walk effects() and
        # remove it by its own real type, not by the base class.
        old_ability = next((c for c in battler.effects() if isinstance(c, Ability)), None)
        if old_ability is not None:
            battler.remove(type(old_ability), self.events)
        from .volatile import GastroAcid
        if not battler.has(GastroAcid):          # a suppressed ability stays detached
            battler.add(self.abilities.get(battler.effective_ability()), self.events)

        old_item = next((c for c in battler.effects() if isinstance(c, HeldItem)), None)
        if old_item is not None:
            battler.remove(type(old_item), self.events)
        battler.add(self.items.get(battler.effective_item()), self.events)

    async def _process_faints(self, battle_ctx: BattleContext) -> None:
        """Fire `on_faint` once for each Pokemon that has fainted since the last call (keyed on `Vitals.faint_seq`, which
        changes on every faint, so a revived-then-fainted-again Pokemon fires again and nothing fires twice)."""
        for b in list(self.field.all_battlers()):
            seq = b.vitals.faint_seq
            if b.fainted and seq and getattr(b, "_faint_seq_done", 0) != seq:
                b._faint_seq_done = seq
                await b.consult("on_faint", battle_ctx)

    async def _run_update(self, battle_ctx: BattleContext) -> None:
        """Showdown's `Update` event (`eachEvent('Update')`, run after every action): lets effects react to the state the
        action just left -- chiefly berries (Sitrus below half, Liechi at 25%, status-cure berries) -- immediately rather
        than at end of turn."""
        for b in self.field.all_battlers():
            if not b.fainted:
                await b.consult("on_update", battle_ctx)

    def resync_items(self) -> None:
        """Re-attach every active Pokemon's item to match `effective_item()`.
        Items are attached once, at switch-in (see `sync_ability_and_item`),
        so anything that suppresses or restores items MID-battle -- Magic
        Room starting, being toggled off, or expiring -- has to call this or
        Leftovers keeps working through it (and stays dead after it)."""
        for b in self.field.all_battlers():
            self.sync_ability_and_item(b)

    # -- setup -------------------------------------------------------------
    def _battle_ctx(self) -> BattleContext:
        return BattleContext(engine=self, field=self.field)

    @property
    def sides(self) -> list["Side"]:
        return [self.field.side_a, self.field.side_b]

    # -- UI contract (pokemon/ui/actions.py, battle_view.py) ---------------
    def legal_moves(self, battler: "Battler") -> list["LegalMove"]:
        from ..ui.actions import LegalMove
        from .targeting import needs_target_prompt
        from .volatile import Disable, Encore, MustRecharge, Taunt
        side = self.field.side_of(battler)
        choice_lock = getattr(battler, "choice_locked_move", None)
        taunt = battler.get(Taunt)
        disable = battler.get(Disable)
        encore = battler.get(Encore)
        must_recharge = battler.has(MustRecharge)
        out: list[LegalMove] = []
        for slot in battler.moves.slots:
            spec = self.moves.spec(slot.move_slug)
            disabled, reason = slot.disabled, slot.disabled_reason
            if must_recharge:
                disabled, reason = True, "Must recharge"
            elif slot.pp <= 0:
                disabled, reason = True, "No PP"
            elif encore and encore.move_slug != slot.move_slug:
                disabled, reason = True, "Encored"
            elif choice_lock and choice_lock != slot.move_slug:
                disabled, reason = True, "Choice-locked"
            elif disable is not None and disable.move_slug == slot.move_slug:
                disabled, reason = True, "Disabled"
            elif self.field.gravity and slot.move_slug in GRAVITY_BANNED_MOVES:
                disabled, reason = True, "Gravity"
            elif taunt is not None and spec.category is MoveCategory.STATUS:
                disabled, reason = True, "Taunted"
            out.append(LegalMove(
                slug=slot.move_slug, display_name=spec.display_name, type_name=spec.type_name,
                category=spec.category, pp_current=slot.pp, pp_max=slot.max_pp,
                disabled=disabled, disabled_reason=reason,
                needs_target_prompt=needs_target_prompt(spec.target, side) if side is not None else False,
            ))
        return out

    def legal_switches(self, side: "Side") -> list["Battler"]:
        if any(key[0] == id(side) for key in self._pivot_slots):    # a pivot move is how a trapped Pokemon gets out
            return side.alive_bench()
        # Trapping: any Volatile the active battler carries that declares
        # `traps_switching = True` (currently just Binding -- see its own
        # docstring for why this check is written generically rather than
        # hardcoded to that one class) blocks switching entirely this turn.
        # A FAINTED active is skipped: a leftover trapping volatile on a
        # Pokemon that's already down must not block choosing its replacement.
        for b in side.active:
            if b is not None and not b.fainted and any(getattr(v, "traps_switching", False) for v in b.effects()):
                return []
        return side.alive_bench()

    async def submit_action(self, side: "Side", action: "BattleAction") -> None:
        self._pending[self._pending_key(side, action)] = action

    def _apply_generation_base_stats(self) -> None:
        """Give every Pokemon the base stats its species had in THIS ruleset's
        generation (see generations/base_stats.py). Done here because every
        builder (wild / PvP / boss) funnels through the engine constructor."""
        from ..generations.base_stats import apply_to_battler
        from ..generations.species_types import apply_to_battler as apply_types
        from ..dex.species import get_default_repository
        repo = get_default_repository()
        for side in self.sides:
            for b in side.roster:
                sp = repo.get(b.identity.species_id)
                if sp is not None:
                    apply_to_battler(b, self.ruleset.number, sp.base_stats)
                    apply_types(b, self.ruleset.number, sp.types)               # the Gen 3-5 pre-Fairy typings

    def will_move(self, battler: "Battler"):
        """The MoveAction `battler` is still going to execute this turn, or
        None (it already moved, switched, or isn't acting)."""
        return self._queued.get(id(battler))

    @staticmethod
    def _pending_key(side: "Side", action: "BattleAction") -> tuple[int, int]:
        return (id(side), id(getattr(action, "user", None)))

    def has_pending(self, side: "Side") -> bool:
        """Has `side` submitted ANY action this turn?"""
        return any(key[0] == id(side) for key in self._pending)

    def has_action(self, side: "Side", battler: "Battler") -> bool:
        """Has `battler` (an active of `side`) already been given an action this turn? (Doubles: the view walks the slots one by one.)"""
        return (id(side), id(battler)) in self._pending

    def retract_actions(self, side: "Side", battlers) -> int:
        """Withdraw the queued actions of `battlers` (Doubles: "change my choice"). Only meaningful before the turn resolves; returns how many were removed."""
        return sum(1 for b in battlers if self._pending.pop((id(side), id(b)), None) is not None)

    def chosen_replacements(self, side: "Side") -> list["Battler"]:
        """The bench Pokemon `side` has already queued to switch IN this turn, so a second slot's picker never offers the same one twice."""
        from ..ui.actions import SwitchAction
        return [a.replacement for k, a in self._pending.items() if k[0] == id(side) and isinstance(a, SwitchAction)]

    def _side_ready(self, side: "Side") -> bool:
        """Every living active Pokemon on `side` has an action (a forfeit, which has no acting Pokemon, covers the whole side)."""
        if (id(side), id(None)) in self._pending:
            return True
        return all((id(side), id(b)) in self._pending for b in side.active if b is not None and not b.fainted) and self.has_pending(side)

    def is_awaiting_replacement(self, side: "Side") -> bool:
        """True while `side` has a fainted active slot the engine is waiting
        for a SwitchAction to fill (see `_awaiting_replacement`)."""
        return bool(self._awaiting_replacement.get(id(side)))

    @property
    def ready_to_resolve(self) -> bool:
        if self._awaiting_replacement:
            return all(sum(1 for k in self._pending if k[0] == id(s)) >= len(self._awaiting_replacement[id(s)]) for s in self.sides if id(s) in self._awaiting_replacement)
        return all(self._side_ready(s) for s in self.sides if s.has_any_left())

    def conclude(self, winner: "Side | None", reason: str) -> None:
        """End the battle NOW with an explicit result, instead of waiting for
        a side to run out of Pokemon -- a successful catch, or Run/Forfeit
        (which, before this existed, only zeroed the active Pokemon's HP and
        so didn't actually end a battle whose side still had a bench)."""
        if self.conclusion is None:
            self.conclusion, self._concluded_winner = reason, winner

    @property
    def is_over(self) -> bool:
        return self.conclusion is not None or any(not s.has_any_left() for s in self.sides)

    @property
    def winner(self) -> "Side | None":
        if self.conclusion is not None:
            return self._concluded_winner
        alive = [s for s in self.sides if s.has_any_left()]
        if len(alive) == 1:
            return alive[0]
        if alive:
            return None  # still going
        # Mutual KO. Showdown (sim/battle.ts `checkWin`): Gen 5+ awards the
        # win to the side of the Pokemon that fainted LAST (so e.g. a
        # Pokemon that KOs the final foe with recoil, and faints to it a
        # moment later, wins; one that Explodes -- fainting first -- loses
        # to its own target); Gen 1-4 call it a draw.
        if self.ruleset.number <= 4:
            return None
        last = max((b for s in self.sides for b in s.roster), key=lambda b: b.vitals.faint_seq, default=None)
        if last is None or last.vitals.faint_seq == 0:
            return None
        return next((s for s in self.sides if last in s.roster), None)

    # -- turn resolution -----------------------------------------------
    async def resolve_turn(self) -> None:
        from ..ui.actions import MoveAction, SwitchAction, ItemAction, ForfeitAction
        battle_ctx = self._battle_ctx()

        if not self._battle_started:
            # Opening-lineup entry abilities (Intimidate, Drought, Download,
            # Frisk, Forewarn, ...) fire once, before the very first turn's
            # actions resolve -- same on_switch_in dispatch _do_switch_in
            # uses for a later switch, just applied to whoever's already
            # placed when the engine was constructed. Order here
            # (side-by-side, not interleaved by Speed) is a reasonable
            # approximation for the rare double-Intimidate-style edge
            # case; singles doesn't have more than one lead per side
            # anyway in this bot's current scope.
            self._battle_started = True
            for side in self.sides:
                for b in side.active:
                    if b is not None:
                        await b.consult("on_switch_in", battle_ctx)

        actions = dict(self._pending)
        self._pending.clear()

        # Snapshot who's already fainted BEFORE this turn's own processing,
        # so the end of this method can tell who fainted DURING it (for
        # Retaliate's "fainted last turn" check on the NEXT turn -- see
        # Side.fainted_last_turn's docstring). The "awaiting replacement"
        # branch just below returns early without comparing this at
        # all -- correct, since that branch only places a replacement
        # Pokemon and can't cause a NEW faint, so fainted_last_turn should
        # just keep whatever the last real turn set.
        was_fainted = {b.id: b.fainted for b in self.field.all_battlers()}

        if self._awaiting_replacement:
            # Not a real turn -- just place whichever replacements were
            # just chosen for fainted active slots, then let a normal turn
            # happen next.
            for side in self.sides:
                slots = self._awaiting_replacement.get(id(side))
                if not slots:
                    continue
                # one SwitchAction per awaited slot (Doubles can have two): pair each with the slot its acting Pokemon occupies, else the next one in order
                for action in [a for k, a in actions.items() if k[0] == id(side) and isinstance(a, SwitchAction)]:
                    if not slots:
                        break
                    user = getattr(action, "user", None)
                    idx = side.active.index(user) if user in side.active else None
                    slot = idx if idx in slots else slots[0]
                    slots.remove(slot)
                    if (id(side), slot) in self._pivot_slots:
                        kind = self._pivot_slots.pop((id(side), slot))
                        await self._do_switch(battle_ctx, side, slot, action.replacement, baton=(kind == "baton-pass"))
                    else:
                        await self._do_switch_in(battle_ctx, side, slot, action.replacement)
                if not slots:
                    del self._awaiting_replacement[id(side)]
            if self._pending_pivot is not None and not self._awaiting_replacement and self._begin_pivot([], was_fainted, resume=False):
                return                                       # a replacement's entry armed an Eject Pack: that holder switches out next (the suspended turn, if any, resumes after)
            if self._mid_turn is not None and not self._awaiting_replacement:
                rest, saved_fainted = self._mid_turn
                self._mid_turn = None
                await self._run_actions(battle_ctx, rest, saved_fainted)       # the turn picks up where the pivot left it
            return

        self.turn_number += 1
        for b in self.field.all_battlers():
            # Payback (moves/conditional_power.py) needs to know whether
            # its target has already taken its move-action this turn --
            # reset here, set True the instant _execute_move starts
            # running for a given Battler.
            b.acted_this_turn = False
        for b in self.field.all_battlers():
            b.used_item_this_turn = False
        self._left_slot = {}                          # a new turn: slots vacated earlier no longer redirect anything
        self._qmeta = {}
        active = self.field.all_battlers()
        for b in active:
            b.attacked_by = [e for e in b.attacked_by if e[0] in active]       # Showdown: `if (attack.source.isActive) thisTurn = false else splice`
        for a in actions.values():
            if isinstance(a, ForfeitAction):
                forfeiter = self.field.side_of(a.user)
                foe = next((s for s in self.sides if s is not forfeiter), None)
                # Wild encounter = "Run" (you got away); anything else = forfeit.
                self.log.append("Got away safely!" if foe is not None and foe.wild_encounter is not None
                                else f"{forfeiter.trainer_id or 'The trainer'} forfeited the battle!")
                self.conclude(foe, "forfeit")
                return

        for a in actions.values():
            if isinstance(a, SwitchAction):
                side = self.field.side_of(a.user)
                slot = side.active.index(a.user) if a.user in side.active else 0
                await self._do_switch(battle_ctx, side, slot, a.replacement)

        for a in actions.values():
            if isinstance(a, ItemAction):
                from .bag import use_bag_item
                await use_bag_item(self, battle_ctx, a)

        move_actions = [a for a in actions.values() if isinstance(a, MoveAction)]
        self._queued = {id(a.user): a for a in move_actions}
        self.last_successful_move_this_turn = None
        for a in move_actions:
            mv = self.moves.get(a.move_slug)
            hctx = MoveContext(battle=battle_ctx, move=mv, user=a.user)
            await mv.on_before_turn(hctx)
            self.log.extend(hctx.result.messages)
        await self._run_update(battle_ctx)     # Showdown runs `Update` after `beforeTurn` too (berries react to state carried in)
        ordered = await self._order_actions(battle_ctx, move_actions)
        await self._run_actions(battle_ctx, ordered, was_fainted)

    async def _run_actions(self, battle_ctx: BattleContext, ordered: list, was_fainted: dict) -> None:
        """Run the ordered move actions, then end the turn -- unless a pivot move suspends it (see `_begin_pivot`), in which case `resolve_turn`
        resumes here with the remaining actions once the replacement has been chosen."""
        if self._begin_pivot(ordered, was_fainted):          # a switch requested before any move ran (an Eject Pack armed by a switch-in's Intimidate)
            return
        queue = list(ordered)                                        # the REMAINING actions; After You / Quash / Round edit it through `self._queue`
        self._queue = queue
        try:
            while queue:
                user, action = queue.pop(0)
                self._queued.pop(id(user), None)
                if user.fainted or self.is_over or user.side is None:    # `side is None`: dragged out / switched out earlier this turn
                    continue
                await self._execute_move(battle_ctx, user, action)
                await self._eject_packs(battle_ctx)                      # Showdown `onAnyAfterMove`
                await self._process_faints(battle_ctx)
                await self._run_update(battle_ctx)
                if self._begin_pivot(list(queue), was_fainted):
                    return
                if battle_ctx.ruleset.number <= 3 and self._begin_faint_replacement(list(queue), was_fainted):       # (also when it was the LAST action: replaced BEFORE the residual)
                    return
                if battle_ctx.ruleset.number >= 8 and queue:
                    await self._resort_queue(battle_ctx, queue)
        finally:
            self._queue = None
        self._queued = {}
        await self._finish_turn(battle_ctx, was_fainted)

    def _begin_faint_replacement(self, rest: list, was_fainted: dict) -> bool:
        """Gens 1-3 (Showdown `runAction`: `this.gen <= 3 && ['move', 'residual'].includes(next action)` -> `checkFainted()`): fainted Pokemon are replaced right after EVERY move (and before the
        residual), not only at the end of the turn. SUSPENDS the turn like a pivot does when a side has a fainted slot and a Pokemon to send in."""
        if self.is_over:
            return False
        started = False
        for side in self.sides:
            fainted_slots = [i for i, b in enumerate(side.active) if b is not None and b.fainted]
            if fainted_slots and side.alive_bench():
                self._awaiting_replacement[id(side)] = fainted_slots[:len(side.alive_bench())]
                started = True
        if started:
            self._mid_turn = (rest, was_fainted)
        return started

    def _begin_pivot(self, rest: list, was_fainted: dict, resume: bool = True) -> bool:
        """After a move that asked to switch its user out (`_pending_pivot`): if the user is still standing and has a healthy bench and the battle isn't
        over, SUSPEND the turn and wait for the replacement choice. (Showdown's mid-turn `switchFlag` request.)"""
        pivot, self._pending_pivot = self._pending_pivot, None
        if pivot is None:
            return False
        user = pivot["user"]
        side = user.side
        if user.fainted or side is None or self.is_over or not side.alive_bench() or user not in side.active:
            return False
        slot = side.active.index(user)
        self._awaiting_replacement[id(side)] = [slot]
        self._pivot_slots[(id(side), slot)] = pivot["kind"]
        if resume:
            self._mid_turn = (rest, was_fainted)         # (resume=False: the turn is already over, or a suspended one stays as it is)
        return True

    async def _finish_turn(self, battle_ctx: BattleContext, was_fainted: dict) -> None:
        if not self.is_over:
            await self._end_of_turn(battle_ctx)
            await self._eject_packs(battle_ctx)      # Showdown `onResidual` (order 29)
            await self._process_faints(battle_ctx)   # residual KOs (poison, weather chip, ...)

        for side in self.sides:
            newly_fainted = any(
                not was_fainted.get(b.id, False) and b.fainted
                for b in side.roster if b is not None
            )
            side.fainted_last_turn = newly_fainted

        for side in self.sides:
            fainted_slots = [i for i, b in enumerate(side.active) if b is not None and b.fainted]
            if fainted_slots and side.alive_bench():
                # Showdown only asks for as many switches as there are Pokemon to send in (two fainted slots, one bench Pokemon: the first slot is filled, the other stays empty)
                self._awaiting_replacement[id(side)] = fainted_slots[:len(side.alive_bench())]
        if self._pending_pivot is not None and not self._awaiting_replacement:
            self._begin_pivot([], was_fainted, resume=False)         # an Eject Pack used at the residual

    async def _order_actions(self, battle_ctx: BattleContext, move_actions: list) -> list[tuple]:
        entries = []
        for a in move_actions:
            entries.append((self._qkey(a, await self._sort_parts(battle_ctx, a)), a))
        entries.sort(key=lambda e: e[0])
        return [(e[1].user, e[1]) for e in entries]

    def _qkey(self, a, parts: tuple) -> tuple:
        """Showdown `comparePriority`: action ORDER first (200 for a move; After You pulls an action to 3, Quash pushes it to 201), then priority (+ the fractional Quick Claw nudge), then speed
        (reversed under Trick Room), then the speed-tie roll (rolled once per action, so a re-sort keeps ties where they were)."""
        m = self._meta(a)
        priority, frac_bonus, speed = parts
        return (m["order"], -priority, -frac_bonus, speed if self.field.trick_room else -speed, m["tie"])

    def _meta(self, a) -> dict:
        m = self._qmeta.get(id(a))
        if m is None:
            m = self._qmeta[id(a)] = dict(order=200, tie=random.random(), source=None)
        return m

    async def _resort_queue(self, battle_ctx: BattleContext, queue: list) -> None:
        """Gen 8+ (Showdown `runAction`: "speed is updated dynamically, so update the queue's speed properties and sort it"): after every action the REMAINING moves are re-ordered by their
        CURRENT priority / speed (Icy Wind, Tailwind, Trick Room, a Choice Scarf pick-up ... take effect for the rest of the turn). Before Gen 8 the order is fixed when the turn starts."""
        keyed = [(self._qkey(a, await self._sort_parts(battle_ctx, a)), u, a) for u, a in queue]
        keyed.sort(key=lambda e: e[0])
        queue[:] = [(u, a) for _, u, a in keyed]

    def queued_move(self, battler):
        """Showdown `queue.willMove(pokemon)`: the (user, action) entry of a Pokemon that has not moved yet this turn (and is still standing), else None."""
        if self._queue is None or battler.fainted:
            return None
        return next((e for e in self._queue if e[0] is battler), None)

    def prioritize_action(self, entry, source: str | None = None) -> None:
        """Showdown `queue.prioritizeAction` (After You, Round): the action jumps to the FRONT of the remaining queue with order 3."""
        self._queue.remove(entry)
        m = self._meta(entry[1])
        m["order"], m["source"] = 3, source
        self._queue.insert(0, entry)

    def delay_action(self, entry) -> None:
        """Quash: the action moves AFTER every other move. Gen 8+: `action.order = 201` (the re-sort after each action puts it behind all the order-200 moves, ordered among several quashed ones
        by priority / speed). Gens 5-7 (the gen7 mod): the action is cancelled and re-inserted right before the residual step, i.e. at the END of the queue, in the order the Quashes happened."""
        if self.ruleset.number >= 8:
            self._meta(entry[1])["order"] = 201
        else:
            self._queue.remove(entry)
            self._queue.append(entry)

    async def _sort_parts(self, battle_ctx: BattleContext, a) -> tuple:
        """The three sort components of a move action: (priority, Quick Claw fraction, speed)."""
        move = self.moves.get(a.move_slug)
        ctx = MoveContext(battle=battle_ctx, move=move, user=a.user)
        priority = move.spec.priority
        priority = await move.on_modify_priority(ctx, priority) or priority
        priority = int(await self.events.reduce("on_modify_priority", ctx, priority))  # Prankster, Gale Wings
        # Quick Claw / Custap Berry: a SEPARATE, fractional-only hook --
        # Showdown's real `onFractionalPriority` (items.ts) returns a
        # +0.1 nudge, not a whole extra priority point, specifically so
        # it can only break a speed tie WITHIN the acting Pokemon's own
        # priority bracket and can never let a slow move act before a
        # genuinely higher-priority one. Kept as a distinct sort-key
        # component rather than folded into `priority` above (which
        # stays a plain int throughout the codebase) for exactly that
        # reason -- folding it in would risk exactly the whole-bracket
        # jump this is designed to avoid.
        # Showdown computes `fractionalPriority` ONCE, when the action is queued (`resolveAction`); `getActionSpeed` (the Gen 8+ re-sort) only re-reads the whole priority and the speed. The hook has a
        # side effect (Custap Berry is EATEN when it fires), so it must not run again on every re-sort.
        m = self._meta(a)
        if "frac" not in m:
            m["frac"] = float(await self.events.reduce("on_fractional_priority", ctx, 0.0, priority))
        frac_bonus = m["frac"]
        speed = await a.user.stat_async("spe", battle_ctx)
        return priority, frac_bonus, speed

    async def _can_act(self, ctx: MoveContext, user: "Battler") -> bool:
        """Pre-move status/volatile checks -- sleep, freeze, paralysis,
        flinch, confusion's self-hit. Old code: the top of apply_move's
        per-combatant block (contore.py's status-prevents-move checks,
        just before dispatching to the chosen move)."""
        # Recharge (Showdown onBeforeMovePriority 11 -- above sleep/freeze at
        # 10): skips the turn entirely and clears itself in the same step.
        if user.has(MustRecharge):
            user.remove(MustRecharge, self.events)
            self.log.append(f"**{user.name}** must recharge!")
            return False

        if user.vitals.status == "sleep":
            from .volatile import GastroAcid
            # Early Bird: Showdown's sleep `onBeforeMove` decrements the counter a SECOND time for the holder (it had no implementation at all).
            early_bird = user.effective_ability() == "early-bird" and not user.has(GastroAcid)
            user.vitals.status_turns -= 2 if early_bird else 1
            if user.vitals.status_turns <= 0:
                user.vitals.status = None
                self.log.append(f"**{user.name}** woke up!")
            else:
                self.log.append(f"**{user.name}** is fast asleep.")
                if not ctx.move.can_use_while_asleep():   # Snore / Sleep Talk
                    return False

        if user.vitals.status == "freeze":
            if random.random() < 0.20:
                user.vitals.status = None
                self.log.append(f"**{user.name}** thawed out!")
            else:
                self.log.append(f"**{user.name}** is frozen solid!")
                return False

        # Truant (Showdown `onBeforeMovePriority: 9`: after sleep/freeze, before Disable/Taunt/paralysis). Its `on_before_move` hook was defined but NEVER
        # dispatched (only Confusion's was, by name), so Truant never loafed. Returns True when the holder loafs this turn.
        if await user.consult_first("on_ability_before_move", ctx):      # (not `on_before_move`: Confusion's hook of that name is called separately below)
            return False

        # Flinch (Showdown `onBeforeMovePriority: 8`): AFTER sleep / freeze (10) and Truant (9) -- so a flinched, frozen Pokemon still gets its thaw roll, and a flinched sleeper still loses a sleep turn.
        if user.has(Flinch):
            self.log.append(f"**{user.name}** flinched and couldn't move!")
            return False

        # Disable (7) / Taunt (5): defense in depth behind `legal_moves()`,
        # which is what normally stops these moves ever being chosen --
        # this only matters for a move that got submitted anyway (a future
        # Metronome/Assist/Sleep Talk calling a random move, or any caller
        # that skips `legal_moves()`).
        disable = user.get(Disable)
        if disable is not None and ctx.move.slug == disable.move_slug:
            self.log.append(f"**{user.name}**'s **{ctx.move.spec.display_name}** is disabled!")
            return False
        if self.field.gravity and ctx.move.slug in GRAVITY_BANNED_MOVES:   # Showdown priority 6
            self.log.append(f"**{user.name}** can't use **{ctx.move.spec.display_name}** because of gravity!")
            return False
        if user.has(Taunt) and ctx.move.category is MoveCategory.STATUS:
            self.log.append(f"**{user.name}** can't use **{ctx.move.spec.display_name}** after the taunt!")
            return False

        from .volatile import Torment
        if user.has(Torment) and ctx.move.slug != "struggle" and getattr(user, "last_move_slug", None) == ctx.move.slug:
            self.log.append(f"**{user.name}** can't use **{ctx.move.spec.display_name}** twice in a row because of the torment!")
            return False

        if ctx.move.slug != "struggle":                     # Imprison (Showdown `onFoeBeforeMove`, priority 4)
            from .moves.misc_status import Imprison
            for foe in self.field.other_side(self.field.side_of(user)).active:
                if foe is not None and not foe.fainted and foe.has(Imprison) and any(s.move_slug == ctx.move.slug for s in foe.moves.slots):
                    self.log.append(f"**{user.name}** can't use the sealed **{ctx.move.spec.display_name}**!")
                    return False

        if user.vitals.status == "paralysis" and random.random() < 0.25:
            self.log.append(f"**{user.name}** is fully paralyzed!")
            return False

        confusion = user.get(Confusion)
        if confusion is not None:
            hits_self = await confusion.on_before_move(ctx, user)
            if hits_self:
                self.log.append(f"**{user.name}** hurt itself in its confusion!")
                # Old code: fixed-formula typeless physical self-hit --
                # 40 power, no STAB/type effectiveness/crit, using the
                # user's own Attack and Defense.
                # Showdown `getConfusionDamage`: `calculateStat` -- the raw stat with its STAGES but NO ability/item modifier (so Huge Power,
                # Pure Power, Fur Coat, Slow Start, Choice Band... do not change it) -- integer steps, truncated to 16 bits, then the normal
                # 85-100 randomizer. (The old float formula also used the ability-modified stats.)
                from . import damage as _damage_mod
                level = user.identity.level
                no_mods = dict(ignore_stages=False, crit_ignore_neg=False, crit_ignore_pos=False)
                atk = user._staged(int(user._raw_stat("atk")), "atk", **no_mods)
                dfe = user._staged(int(user._raw_stat("def")), "def", **no_mods)
                base = (((2 * level // 5 + 2) * 40 * atk) // max(1, dfe)) // 50 + 2
                dmg = max(1, ((base % 65536) * _damage_mod._damage_roll()) // 100)
                user.vitals.hp = max(0, user.vitals.hp - dmg)
                if user.fainted:
                    self.log.append(f"**{user.name}** fainted!")
                return False

        if user.has(Attract):
            self.log.append(f"**{user.name}** is in love!")
            if random.random() < 0.5:
                self.log.append(f"**{user.name}** is immobilized by love!")
                return False
        if ctx.move.slug != "destiny-bond":
            from .volatile import DestinyBond
            if user.has(DestinyBond):
                user.remove(DestinyBond, self.events)          # Showdown `onBeforeMove` (priority -1): acting again ends it
        return True

    async def _execute_move(self, battle_ctx: BattleContext, user: "Battler", action) -> None:
        # A Pokemon mid-charge (Fly/Dig/Solar Beam/...) is FORCED to
        # continue that exact move on the release turn, regardless of
        # what was actually submitted -- real games don't let you change
        # your mind mid-charge (you can still be prevented from acting by
        # something else, e.g. paralysis, which _can_act below still
        # checks normally). Checked and captured here, BEFORE the move's
        # own on_try_use runs (which is what actually removes the
        # Charging volatile on this release turn) so PP is correctly
        # skipped on release turns without the move itself needing to
        # know about PP at all. See volatile.py's Charging docstring.
        from .volatile import Charging
        charging = user.get(Charging)
        if charging is not None:
            from ..ui.actions import MoveAction
            action = MoveAction(user=user, move_slug=charging.move_slug, targets=())

        # Encore: forces whatever was actually chosen back to the encored
        # move -- matches Showdown's `OverrideAction` event (sim/battle-
        # actions.ts's `runMove`), not just `legal_moves()`'s UI-level
        # filtering (which is all Taunt/Disable/MustRecharge/Attract get,
        # since none of those REPLACE a choice, they only ever block one --
        # see `_can_act` below for those). Needed for the same reason the
        # Charging override above is: a raw submission (or a future
        # Metronome/Sleep Talk picking a random move) has to be corrected
        # here, not just discouraged at the picker.
        from .volatile import Encore
        encore = user.get(Encore)
        if encore is not None and action.move_slug != encore.move_slug:
            from ..ui.actions import MoveAction
            action = MoveAction(user=user, move_slug=encore.move_slug, targets=())

        from .volatile import LockedMove
        locked = user.get(LockedMove)
        if locked is not None:                      # Showdown `onLockMove`: Rollout / Ice Ball (and the Outrage family) repeat until the lock ends
            from ..ui.actions import MoveAction
            action = MoveAction(user=user, move_slug=locked.move_slug, targets=())

        move = self.moves.get(action.move_slug)
        ctx = MoveContext(battle=battle_ctx, move=move, user=user)
        ctx.source_effect = self._qmeta.get(id(action), {}).get("source")        # Showdown `move.sourceEffect` (Round: pulled up behind another Round)
        user.acted_this_turn = True
        user.move_actions += 1  # at the very top, like Showdown's runMove: a lost turn still counts

        # Reset the Protect family's consecutive-use counter (see
        # moves/protect.py) the instant a DIFFERENT move is chosen --
        # matching how old code reset `protect_consecutive_uses = 0` at
        # every non-protect move branch (contore.py had this scattered
        # across ~8 separate call sites; one check here replaces all of
        # them). A protect-family move manages its own counter/increment
        # in its on_try_use/on_hit instead of being reset here.
        _PROTECT_SLUGS = ("protect", "detect", "spiky-shield", "kings-shield", "baneful-bunker",
                          "wide-guard", "quick-guard", "crafty-shield")
        if action.move_slug not in _PROTECT_SLUGS:
            user.protect_consecutive_uses = 0

        if not await self._can_act(ctx, user):
            self.log.extend(ctx.result.messages)
            return

        # Focus Punch's lost focus: cancels the move BEFORE PP is spent.
        if await move.before_move_callback(ctx):
            self.log.extend(ctx.result.messages)
            return

        from .volatile import Bide
        already_biding = action.move_slug == "bide" and user.has(Bide)
        slot = next((s for s in user.moves.slots if s.move_slug == action.move_slug), None)
        if charging is None and locked is None and not already_biding and slot is not None and slot.pp > 0:
            slot.pp -= 1  # release turn (charging) / later bide turns cost no additional PP
            slot.used = True

        await self._resolve_move(ctx, move, user, action.move_slug, list(action.targets))

    async def _redirect(self, ctx: MoveContext, move, user: "Battler", target: "Battler") -> "Battler":
        """Showdown's `RedirectTarget` event for a single-target move: a FOE's Follow Me / Rage Powder (priority 1) takes it first, then a Lightning Rod / Storm Drain holder (anyone's move of
        that type, ally or foe, not the holder's own). Not for Stalwart / Propeller Tail users, and Rage Powder skips a powder-immune user. Doubles only: in Singles nothing can redirect."""
        from .moves.misc_status import FollowMeActive
        field = self.field
        if user.effective_ability() in ("stalwart", "propeller-tail"):
            return target
        side = field.side_of(user)
        for b in field.all_battlers():
            if b.fainted or field.side_of(b) is side:
                continue
            fm = b.get(FollowMeActive)
            if fm is None or b is user:
                continue
            if fm.powder and ctx.ruleset.number >= 6 and ("Grass" in user.effective_types() or user.effective_ability() == "overcoat" or user.effective_item() == "Safety Goggles"):
                continue
            return b
        if ctx.ruleset.number >= 3:
            wanted = {"lightning-rod": "Electric", "storm-drain": "Water"}
            for b in field.all_battlers():
                if b.fainted or b is user:
                    continue
                if ctx.ruleset.number == 3 and field.side_of(b) is side:
                    continue                                   # Gen 3's Lightning Rod only draws FOES' moves (`onFoeRedirectTarget`)
                if wanted.get(b.effective_ability()) == move.spec.type_name and not attacker_breaks_mold_for(ctx, b):
                    return b
        return target

    async def _use_called_move(self, caller_ctx: MoveContext, user: "Battler", slug: str, targets=()) -> None:
        """Showdown `useMove(move, pokemon, target, sourceEffect)`: a move CALLED by another one (Metronome, Nature Power, Sleep Talk, Copycat, ...). It goes through the very same
        resolution as a chosen move (targets, immunities, accuracy, hit, secondaries, Life Orb ...) but skips what belongs to CHOOSING a move: no PP, no `_can_act` (sleep / paralysis / flinch
        were already checked for the caller), no Encore / charging override."""
        move = self.moves.get(slug)
        ctx = MoveContext(battle=caller_ctx.battle, move=move, user=user)
        ctx.called_by = caller_ctx.move.slug
        await self._resolve_move(ctx, move, user, slug, list(targets))

    async def _resolve_move(self, ctx: MoveContext, move, user: "Battler", move_slug: str, preset_targets: list) -> None:
        """Everything of a move's use from `on_try_use` on: shared by `_execute_move` (a chosen move) and `_use_called_move`."""
        fail_msg = await move.on_try_use(ctx)
        if fail_msg:
            ctx.log(fail_msg)
            self.log.extend(ctx.result.messages)
            return

        if preset_targets:
            preset_targets = [self._slot_occupant(t) for t in preset_targets]
        targets = preset_targets or await move.get_targets(ctx)
        if preset_targets and move.spec.target is MoveTarget.SELECTED_POKEMON and all(t.fainted for t in preset_targets):
            ctx.targets = list(preset_targets)          # the chosen target fainted earlier this turn: Showdown `getTarget` picks a living foe instead (a fainted ALLY just fails)
            targets = await move.get_targets(ctx)
        if len(targets) == 1 and move.spec.target in (MoveTarget.SELECTED_POKEMON, MoveTarget.RANDOM_OPPONENT):
            targets = [await self._redirect(ctx, move, user, targets[0])]
        ctx.targets = targets
        # Before the no-target bail-out on purpose: Showdown's useMove runs
        # its pre-hit effects (Explosion's faint, Gen 5+) even when there's
        # nobody to hit. Hooks needing a target check `ctx.targets` themselves.
        await move.on_move_start(ctx)
        await user.consult("on_prepare_hit", ctx)   # Showdown `PrepareHit`: Protean / Libero
        if not targets:
            ctx.log("But it failed!")
            await move.on_after_move(ctx, False)
            self.log.extend(ctx.result.messages)
            return

        self.log.append(f"**{user.name}** used **{move.spec.display_name}**!")
        # Tracked here (the move was actually used, not merely selected --
        # matches when the real games consider it your "last move" for
        # Disable/Encore/Mirror Move/Torment, even if it goes on to miss
        # or fail against every target) rather than at selection time.
        # Following `acted_this_turn`'s precedent above: a plain attribute,
        # not a dataclass field -- see battler.py's own "no __slots__"
        # docstring note on why that's fine here.
        user.last_move_slug = move_slug
        connected = False  # did the move actually land on anyone? (selfdestruct "if_hit")
        # Showdown steps over ALL targets for each stage (invulnerability, TryHit / Protect, immunity, accuracy) and only then deals the damage. The spread modifier (x0.75, Gen 3 x0.5) is decided
        # from the INITIAL target list (`trySpreadMoveHit`: `targets.length > 1`), BEFORE any filtering: a Protected, immune or missing target still counts. (Only a fainted / absent one doesn't.)
        survivors = []
        for target in targets:
            if await self._target_gate(ctx, move, user, target):
                survivors.append(target)
        multi = (MoveTarget.ALL_OPPONENTS,) if ctx.ruleset.number == 3 else (MoveTarget.ALL_OPPONENTS, MoveTarget.ALL_OTHER_POKEMON)       # Gen 3: only the moves that hit just the FOES are reduced (x0.5)
        spread = move.spec.target in multi and len(targets) > 1
        ctx.spread_modifier = 0.75 if spread else 1.0
        for target in survivors:
            if await self._target_hit(ctx, move, user, target):
                connected = True

        if connected and not ctx.result.failed:
            # Showdown `AfterMoveSecondary`: once per TARGET THAT WAS HIT after the whole move (Eject Button, Red Card, ...). An immune, missing, Protected or Substitute-absorbed target is not in that list.
            for t in list(getattr(ctx, "after_secondary_targets", [])):
                if t is not user:
                    await t.consult("on_after_move_secondary", ctx)
        if connected and not ctx.result.failed and not user.fainted:
            # Showdown `AfterMoveSecondarySelf`: ONCE per move, only if it landed -- Life Orb's recoil, Shell Bell's heal. RECOIL once, from the move's TOTAL damage.
            if move.spec.drain < 0 and ctx.result.total_damage > 0:
                await _apply_drain_recoil(ctx, move, user, ctx.result.total_damage)
            if not user.fainted:
                await user.consult("on_after_move_self", ctx)
        if not (connected and not ctx.result.failed) and not user.fainted:
            await user.consult("on_move_aborted", ctx)          # Showdown `onMoveAborted` (Charge ends on an Electric move that missed / failed)
        if connected and not ctx.result.failed:
            self.last_successful_move_this_turn = move.slug
            # Showdown `battle.lastMove` (Copycat). Gens 5+: the nested `useMove` of a called move clears the active move itself, so it is the CALLED move; Gens 3-4: the active move is still the
            # CALLER when the outer move finishes, so a Metronome'd move leaves `lastMove = metronome` (which Copycat then refuses: `failcopycat`).
            if not getattr(ctx, "called_move_used", False) or ctx.ruleset.number <= 4:
                self.last_move_global = move.slug
        await move.on_after_move(ctx, connected)
        self.log.extend(ctx.result.messages)

    async def _target_gate(self, ctx: MoveContext, move, user: "Battler", target: "Battler") -> bool:
        """Does `move` GET THROUGH to `target`? (immunities, TryHit / Protect / Wide Guard, Magic Bounce, absorbing abilities, accuracy, Substitute veto.) Split from the hit itself so a
        multi-target move can count how many targets survive BEFORE any damage is done (Showdown applies the x0.75 spread modifier to those). Returns True if it did. Factored out of `_execute_move` so a bounced move (Magic Bounce) replays
        through exactly the same pipeline. (Showdown: `hitStepTryImmunity/TryHit/Accuracy/MoveHitLoop`.)"""
        if target.fainted:
            return False
        ctx.current_target = target
        # Grass-type's powder immunity is a Gen 6+ mechanic (Pokemon
        # Showdown: sim/battle-actions.ts's `this.battle.gen >= 6 &&
        # move.flags['powder']`) and, unlike Overcoat/Safety Goggles
        # (both already handled generically by the on_try_hit consult
        # right below -- they're attached Effects), isn't tied to any
        # ability/item component that consult_first could find; it's
        # inherent to the target's typing, so it's checked directly
        # here instead.
        if (move.has_flag(MoveFlag.POWDER) and ctx.ruleset.number >= 6
                and "Grass" in target.effective_types() and target is not user):
            ctx.log(f"It doesn't affect **{target.name}**!")
            return False
        if not await move.on_try_immunity(ctx, target):
            ctx.log(f"It doesn't affect **{target.name}**!")
            return False   # never reaches on_hit, so it isn't "connected" either
        blocked = await target.consult_first("on_try_hit", ctx)
        if not blocked:
            # Psychic Terrain (Showdown onTryHit, priority 4): stops priority moves aimed at a grounded Pokemon.
            terrain = ctx.field.terrain_rules
            if terrain is not None and terrain.blocks_priority_move(user, target, move.spec, ctx.field):
                ctx.log(f"**{target.name}** surrounds itself with Psychic Terrain!")
                return False
            # Wide Guard/Quick Guard/Crafty Shield attach to the
            # SIDE, not the individual Battler (see moves/protect.py),
            # so they're invisible to the consult_first call above
            # (which only walks `target`'s own attached effects) --
            # checked separately here.
            side_guard = ctx.target_side(target).get(ProtectionActive)
            if side_guard is not None:
                blocked = await side_guard.on_try_hit(ctx, ctx.target_side(target))
        if blocked:
            ctx.log(f"But it failed against **{target.name}**!")
            return False
        # Magic Bounce (Showdown onTryHit, priority 1: after Protect, before accuracy): a reflectable move aimed at the holder is
        # used again by the holder at the original user. `ctx.is_bounced` stops a bounced move bouncing back.
        if (not ctx.is_bounced and target is not user and is_reflectable(move.slug) and not target.is_semi_invulnerable()
                and await target.consult_first("on_bounce_move", ctx)):
            ctx.log(f"**{target.name}** bounced the **{move.spec.display_name}** back!")
            bounced = MoveContext(battle=ctx.battle, move=move, user=target)
            bounced.is_bounced = True
            bounced.targets = [user]
            await self._hit_one_target(bounced, move, target, user)
            ctx.result.messages.extend(bounced.result.messages)
            return False
        # Type-absorbing abilities (Flash Fire, Sap Sipper, Water/Volt Absorb, Storm Drain, ...) are `onTryHit` handlers in Showdown, so they
        # stop STATUS moves of their type too (Will-O-Wisp, Spore, Leech Seed, Thunder Wave, Soak). The damaging path already consults
        # `on_type_immunity` itself (damage.py); this is the same check for status moves aimed at a Pokemon.
        if (move.category is MoveCategory.STATUS and target is not user
                and move.spec.target in (MoveTarget.SELECTED_POKEMON, MoveTarget.ALL_OPPONENTS, MoveTarget.ALL_OTHER_POKEMON, MoveTarget.RANDOM_OPPONENT)
                and await target.consult_first("on_type_immunity", ctx, move.spec.type_name)):
            return False   # the ability already logged its own message
        if move.slug in ("teleport", "baton-pass"):
            # Showdown Teleport `onTry` / Baton Pass `onTryHit`: no healthy bench Pokemon -> "But it failed". (Before Gen 8 Teleport flees a WILD battle
            # instead of switching and fails in a trainer battle.)
            if user.side is None or not user.side.alive_bench() or (move.slug == "teleport" and ctx.ruleset.number < 8):
                ctx.log("But it failed!")
                return False
        # Oblivious / Aroma Veil: `on_block_mental_move` (Taunt, Attract, Encore, ...) had no caller anywhere, so neither ever blocked anything.
        if (move.category is MoveCategory.STATUS and target is not user
                and await target.consult_first("on_block_mental_move", ctx, move.slug)):
            return False   # the ability already logged its own message
        if not await check_accuracy(ctx, move, target):
            ctx.log(f"**{user.name}**'s attack missed **{target.name}**!")
            return False
        if await target.consult_first("on_try_primary_hit", ctx):          # Substitute: after Magic Bounce / absorbs / accuracy, as in Showdown
            ctx.log(f"But it failed against **{target.name}**!")
            # Gen 3's `tryMoveHit` has already passed `TryHit` here (the Substitute check lives in `moveHit`), so `gotAttacked` still records the blocked move; Gen 4+ nulls the target first.
            if ctx.ruleset.number == 3 and target is not user and move.spec.target not in _SIDE_OR_FIELD_TARGETS:
                target.attacked_by.append((user, move.slug))
            return False
        return True

    async def _target_hit(self, ctx: MoveContext, move, user: "Battler", target: "Battler") -> bool:
        """The second half of `_hit_one_target`: `on_hit`, secondaries, force-switch / pivot bookkeeping, faint bookkeeping. Runs only for targets that got through `_target_gate`."""
        if target.fainted:
            return False
        ctx.current_target = target
        landed = False
        ctx.hit_blocked = False
        sub_absorbed_before = getattr(ctx, "sub_absorbed", 0)           # the damage pipeline bumps `ctx.sub_absorbed` when a Substitute takes a hit
        msgs_before = len(ctx.result.messages)
        stages_before = dict(target.stages) if move.slug == "parting-shot" else None
        await move.on_hit(ctx, target)
        if not ctx.result.failed and not ctx.hit_blocked:
            landed = True
            await move.on_secondary_effect(ctx, target)
            if move.slug in FORCE_SWITCH_MOVES:           # Showdown spreadMoveHit step 6: `if (moveData.forceSwitch) ...`
                await self._force_switch(ctx, move, user, target)
            if move.slug in SELF_SWITCH_MOVES and (stages_before is None or dict(target.stages) != stages_before
                                                   or target.effective_ability() == "mirror-armor"):
                # Showdown `selfSwitch`: the user leaves after the move; consumed by `_begin_pivot` once the move (and any faints) are done.
                self._pending_pivot = {"user": user, "kind": "baton-pass" if move.slug == "baton-pass" else "switch"}
        if target.fainted and target not in ctx.result.fainted:
            ctx.result.fainted.append(target)
            await move.on_target_faint(ctx, target)
        from .volatile import DestinyBond
        if target.fainted and target is not user and target.has(DestinyBond) and not user.fainted and self.field.side_of(target) is not self.field.side_of(user):
            ctx.log(f"**{target.name}** took **{user.name}** down with it!")                  # Showdown `destinybond.onFaint`: a MOVE KO by a non-ally
            user.vitals.hp = 0
        # Showdown `target.gotAttacked(move, damage, pokemon)` (after the hit loop). NOT recorded for: moves that target a SIDE or the FIELD (they run `tryMoveHit`, which never calls it:
        # Spikes, Stealth Rock, Reflect, weather, Perish Song, Heal Bell ...); a hit that failed outright (except in Gen 3, which records regardless); a hit a Substitute absorbed (Gen 4+: the target is nulled).
        # Most moves report their own internal failure ("But it failed!": Disable with nothing to disable, Role Play with the same ability ...) by logging it, without setting `result.failed`.
        failed_in_hit = any(m.startswith("But it failed") for m in ctx.result.messages[msgs_before:])
        if (target is not user and move.spec.target not in _SIDE_OR_FIELD_TARGETS and (ctx.ruleset.number == 3 or (landed and not failed_in_hit))
                and not (getattr(ctx, "sub_absorbed", 0) > sub_absorbed_before and ctx.ruleset.number >= 4)):
            target.attacked_by.append((user, move.slug))
        if landed and not (getattr(ctx, "sub_absorbed", 0) > sub_absorbed_before):      # the targets Showdown's `AfterMoveSecondary` runs for (see `_resolve_move`)
            if not hasattr(ctx, "after_secondary_targets"):
                ctx.after_secondary_targets = []
            ctx.after_secondary_targets.append(target)
        return landed

    async def _hit_one_target(self, ctx: MoveContext, move, user: "Battler", target: "Battler") -> bool:
        """Gate + hit for ONE target (a bounced move replays through exactly this)."""
        if not await self._target_gate(ctx, move, user, target):
            return False
        return await self._target_hit(ctx, move, user, target)

    async def _force_switch(self, ctx: MoveContext, move, user: "Battler", target: "Battler") -> None:
        """Roar / Whirlwind / Dragon Tail / Circle Throw (Showdown `forceSwitch` + `dragIn`): if both are still standing and the target's side has a
        healthy Pokemon on the bench, the `DragOut` event may veto it (Suction Cups, Ingrain; a vetoed STATUS move reports "But it failed"); otherwise a
        RANDOM bench Pokemon replaces the target through the normal switch path (hazards, switch-in abilities). A hit absorbed by a Substitute drags nothing."""
        side = target.side
        if target.fainted or user.fainted or side is None or not side.alive_bench() or getattr(ctx, "hit_substitute", False):
            return
        from .battler import _Ingrain
        ctx.drag_target = target
        if await target.consult_first("on_drag_out", ctx) or target.get(_Ingrain) is not None:
            if move.category is MoveCategory.STATUS:
                ctx.log("But it failed!")
            return
        slot = side.active.index(target)
        replacement = random.choice(side.alive_bench())
        ctx.log(f"**{target.name}** was dragged out!")
        await self._do_switch(ctx.battle, side, slot, replacement)

    # -- switching --------------------------------------------------------
    async def _do_switch(self, battle_ctx: BattleContext, side: "Side", slot: int, replacement: "Battler", *, baton: bool = False) -> None:
        # Showdown's `onSwitchOut`: Regenerator, Natural Cure, a primal weather's source leaving, Flash Fire's boost.
        # Nothing ever dispatched this hook, so all of them were dead (the monolith's `_apply_switch` did the first
        # three inline). Runs BEFORE the slot is cleared so the holder still has its side/effects.
        departing = side.active[slot]
        if departing is not None and not departing.fainted:
            await departing.consult("on_switch_out", battle_ctx)
        passed_stages, passed_effects = None, []
        if baton and departing is not None:
            # Baton Pass (Showdown `copyVolatileFrom`): the stat stages and every volatile that isn't `noCopy`. Detached BEFORE `clear_temp` wipes them.
            from .volatile import Substitute, LeechSeed, Confusion, Taunt, FocusEnergy, PerishSong, Curse
            passed_stages = dict(departing.stages)
            from .moves.misc_status import IngrainTick, AquaRing, MagnetRiseTick
            from .battler import _Ingrain, _MagnetRise, _PowerTrickMarker, _PowerSplitOverride
            passed_effects = [v for v in list(departing.effects()) if isinstance(v, (Substitute, LeechSeed, Confusion, Taunt, FocusEnergy, PerishSong, Curse,
                                                                                      IngrainTick, AquaRing, MagnetRiseTick))]
            # the marker COMPONENTS that go with them (Showdown copies Ingrain / Magnet Rise / Power Trick; a Power Split is a stored-stat change, not a volatile: it stays)
            passed_effects += [c for c in (departing.get(_Ingrain), departing.get(_MagnetRise), departing.get(_PowerTrickMarker)) if c is not None]
            for v in passed_effects:
                departing.remove(type(v), self.events)
        leaving = side.clear_slot(slot)
        if leaving is not None:
            # Showdown targets POSITIONS: a move aimed at this slot later this turn hits whoever stands there by then. `_do_switch_in` only records a leaver it still finds in the slot
            # (a fainted / pivoting one), and this path has just emptied it, so a VOLUNTARY switch (or Baton Pass) left a stale, side-less target behind: the foe's move crashed.
            if leaving is not replacement:
                self._left_slot[id(leaving)] = (side, slot)
            leaving.clear_temp(self.events)
            # The bad-poison counter resets to 1/16 on switch-out even
            # though the "badly poisoned" status itself persists (Gen 3+;
            # Bulbapedia's poison article: "If the Pokemon switches out
            # and reenters battle, the damage restarts at 1/16"). Vitals
            # isn't touched by clear_temp() above (status has to survive
            # a switch), so this needs its own explicit reset.
            leaving.vitals.toxic_counter = 0
            if leaving in side.bench:
                side.bench.remove(leaving)
            side.bench.append(leaving)
        if passed_stages is not None:
            replacement.stages.update(passed_stages)
            for v in passed_effects:
                replacement.add(v, self.events)
        await self._do_switch_in(battle_ctx, side, slot, replacement)

    def _deliver_slot_condition(self, side: "Side", slot: int, mon: "Battler") -> None:
        """Healing Wish / Lunar Dance (Showdown `slotCondition` + `onSwap`).
        Applied only if the arriving Pokemon actually needs it -- otherwise
        the wish STAYS on the slot for a later arrival, exactly like the
        real games. Runs before the arrival's own switch-in hooks, so the
        heal lands before entry hazards chip at it."""
        kind = side.slot_conditions.get(slot)
        if kind is None or mon.fainted:
            return
        v = mon.vitals
        needs_pp = kind == "lunar-dance" and any(m.pp < m.max_pp for m in mon.moves.slots)
        if not (v.hp < v.max_hp or v.status or needs_pp):
            return
        v.hp = v.max_hp
        v.status, v.status_turns, v.toxic_counter = None, 0, 0
        if kind == "lunar-dance":
            for m in mon.moves.slots:
                m.pp = m.max_pp
        del side.slot_conditions[slot]
        self.log.append(f"**{mon.name}** became cloaked in mystical moonlight!" if kind == "lunar-dance"
                        else f"The healing wish came true for **{mon.name}**!")

    async def _do_switch_in(self, battle_ctx: BattleContext, side: "Side", slot: int, replacement: "Battler") -> None:
        if replacement in side.bench:
            side.bench.remove(replacement)
        leaving = side.active[slot] if slot < len(side.active) else None
        if leaving is not None and leaving is not replacement:
            self._left_slot[id(leaving)] = (side, slot)
        side.place(slot, replacement)
        self.sync_ability_and_item(replacement)
        replacement._protean_used = False  # Gen 9 Protean/Libero: once per switch-in
        replacement.move_actions = 0  # Showdown switchIn: activeMoveActions = 0, moveSlot.used = false
        for s in replacement.moves.slots:
            s.used = False
        self.log.append(f"{side.trainer_id} sent out **{replacement.name}**!")
        # Entry hazards (Showdown `SwitchIn` event of the side conditions: AFTER Healing Wish's / Lunar Dance's heal from Gen 5, BEFORE it in Gens 3-4 (`onSwitchInPriority: -1` in the gen4 mod),
        # and BEFORE the entrant's own ability / item `onStart`). `apply_entry_hazards` existed but was called from nowhere, so Stealth Rock / Spikes / Toxic Spikes / Sticky Web never affected anyone.
        from .side_conditions import apply_entry_hazards
        if battle_ctx.ruleset.number >= 5:
            self._deliver_slot_condition(side, slot, replacement)
        for msg in await apply_entry_hazards(battle_ctx, replacement, side):
            self.log.append(msg)
        if replacement.fainted:
            return
        if battle_ctx.ruleset.number < 5:
            self._deliver_slot_condition(side, slot, replacement)
        await replacement.consult("on_switch_in", battle_ctx)
        await self._eject_packs(battle_ctx)                          # Showdown `onAnySwitchIn` (priority -4: after the entrant's own Intimidate & co.)

    def _slot_occupant(self, t: "Battler") -> "Battler":
        """Showdown targets POSITIONS: a move aimed (at the start of the turn) at the slot `t` stood in hits whoever stands there NOW -- the replacement of a Pokemon that left the slot, or the ally
        that Ally Switch traded places with. `_left_slot` holds the slot a Pokemon was in when it left / was swapped away from it."""
        rec = self._left_slot.get(id(t))
        if rec is not None:
            side, slot = rec
            if slot < len(side.active) and side.active[slot] is not None and side.active[slot] is not t:
                return side.active[slot]
        return t

    async def _eject_packs(self, battle_ctx: BattleContext) -> None:
        for b in self.field.all_battlers():
            await b.consult("on_eject_pack_check", battle_ctx)

    # -- end of turn --------------------------------------------------------
    async def _status_residual(self, battle_ctx: BattleContext, battler: "Battler") -> None:
        """Poison / toxic / burn damage for ONE Pokemon (Showdown `psn` / `tox` / `brn` `onResidual`)."""
        if battler.fainted:
            return
        status = battler.vitals.status
        if status in ("poison", "bad_poison"):
            if status == "bad_poison":
                # Showdown's base `tox` condition (Gens 3+; the gen1/gen2 mods use a different per-move schedule that
                # this engine doesn't model): `if (stage < 15) stage++` then
                # `damage(clampIntRange(baseMaxhp / 16, 1) * stage)`. Two details matter:
                #  * the cap at 15 -- without it a badly poisoned Pokemon surviving 16+ turns would take MORE than
                #    its own max HP in one tick;
                #  * `clampIntRange` FLOORS before the multiply, so the unit is `max_hp // 16` and THEN x stage.
                #    (`max_hp * stage // 16` is up to stage-1 HP too much: 235 HP at stage 2 is 28, not 29.)
                if battler.vitals.toxic_counter < 15:
                    battler.vitals.toxic_counter += 1
                dmg = max(1, battler.vitals.max_hp // 16) * battler.vitals.toxic_counter
            else:
                dmg = max(1, battler.vitals.max_hp // 8)
            dmg = await battler.consult_reduce("on_indirect_damage", battle_ctx, dmg, "status")
            if dmg > 0:
                battler.vitals.hp = max(0, battler.vitals.hp - dmg)
                self.log.append(f"**{battler.name}** is hurt by poison!")
            elif dmg < 0:      # a NEGATIVE result is a heal (Poison Heal); this branch did not exist, so Poison Heal never healed
                healed = battler.heal(-dmg)
                if healed:
                    self.log.append(f"**{battler.name}**'s Poison Heal restored some HP! (+**{healed:,}** HP)")
        elif status == "burn":
            # Fraction is generation-dependent: 1/8 Gen 2-6, 1/16 Gen 1
            # and Gen 7+ (verified against Pokemon Showdown's explicit
            # Gen 6 override of the current-gen 1/16 value back up to
            # 1/8) -- this bot's Gen 6 baseline needs 1/8, which an
            # earlier draft had wrong as a hardcoded 1/16 (exactly
            # half the intended damage).
            fraction = battle_ctx.ruleset.mechanics.burn_damage_fraction
            dmg = max(1, int(battler.vitals.max_hp * fraction))
            dmg = await battler.consult_reduce("on_indirect_damage", battle_ctx, dmg, "status")
            if dmg > 0:
                battler.vitals.hp = max(0, battler.vitals.hp - dmg)
                self.log.append(f"**{battler.name}** is hurt by its burn!")
        if battler.fainted:
            self.log.append(f"**{battler.name}** fainted!")

    async def _end_of_turn(self, battle_ctx: BattleContext) -> None:
        """Old code: BattleField.tick() plus the residual-damage section of
        apply_move's caller loop (weather chip, status damage, Leech Seed/
        held items/abilities' on_end_turn, screen/hazard/weather/terrain
        countdown)."""
        for battler in self.field.all_battlers():
            if battler.fainted:
                continue
            dmg, template = self.field.weather_chip(battler, battler.effective_types())
            if dmg:
                dmg = await battler.consult_reduce("on_indirect_damage", battle_ctx, dmg, "weather")
                if dmg > 0:
                    battler.vitals.hp = max(0, battler.vitals.hp - dmg)
                    self.log.append(template.format(name=f"**{battler.name}**"))

        # Showdown orders every residual by (order, subOrder): Wish 4 | Grassy Terrain 5.2, Shed Skin / Healer / Hydration 5.3, Leftovers / Black Sludge 5.4 | Aqua Ring 6, Ingrain 7,
        # Leech Seed 8 | poison / toxic 9, burn 10 | Nightmare 11, Curse 12, trapping 13 ... Taunt 15 ... Yawn 23, Perish Song 24 | Speed Boost / Orbs 28 | the rest 29.
        # (The old code did ALL status damage first and every other `on_end_turn` after it, so a heal at full HP was wasted and a Pokemon near KO died instead of being healed.)
        await self.events.run("on_end_turn", battle_ctx, order_range=(0, 5))

        # Terrain residual (Showdown `onResidual`, order 5: after weather chip, before status damage) -- Grassy Terrain's heal.
        terrain = self.field.terrain_rules
        if terrain is not None:
            for battler in self.field.all_battlers():
                await terrain.on_residual(battle_ctx, battler)

        # Residual order 5 (before the status tick): Shed Skin cures the status first.
        for battler in self.field.all_battlers():
            if not battler.fainted:
                await battler.consult("on_early_residual", battle_ctx)

        status_key = 10.06 if self.ruleset.number <= 4 else 9              # where poison / burn sit in this generation's table (Gen 4: order 10, sub-order 6)
        if self.ruleset.number <= 4:
            from .battler import Battler
            # Showdown sorts residuals by (order, priority, SPEED, subOrder): in Gens 3-4 everything is order 10, so the FASTER Pokemon's whole chain (Ingrain, Aqua Ring, Leftovers, Leech Seed,
            # status, Curse ... see data/mods/gen4) runs before the slower one's.
            not_battler = lambda o: not isinstance(o, Battler)
            await self.events.run("on_end_turn", battle_ctx, order_range=(0, 10), owner_filter=not_battler)       # sides / field below order 10 (Wish is order 7)
            alive = [b for b in self.field.all_battlers() if not b.fainted]
            speeds = {id(b): await b.stat_async("spe", battle_ctx) for b in alive}
            for b in sorted(alive, key=lambda x: -speeds[id(x)]):
                await self.events.run("on_end_turn", battle_ctx, order_range=(0, status_key), owner_filter=lambda o, b=b: o is b)
                await self._status_residual(battle_ctx, b)
                await self.events.run("on_end_turn", battle_ctx, order_range=(status_key, 1000), owner_filter=lambda o, b=b: o is b)
            await self.events.run("on_end_turn", battle_ctx, order_range=(10, 1000), owner_filter=not_battler)      # the rest of the sides' / field's effects
        else:
            await self.events.run("on_end_turn", battle_ctx, order_range=(5, status_key))

            for battler in self.field.all_battlers():
                await self._status_residual(battle_ctx, battler)

        # One global dispatch covers every remaining on_end_turn reactor:
        # Leftovers/Black Sludge/status-cure berries/Speed Boost/Moody/
        # Bad Dreams/etc. (Battler-owned) AND Reflect/Light Screen/
        # Safeguard/Mist/Tailwind/Lucky Chant countdown (Side-owned) --
        # see side_conditions.py's _TimedScreen.on_end_turn docstring.
            await self.events.run("on_end_turn", battle_ctx, order_range=(status_key, 1000))

        for battler in self.field.all_battlers():
            battler.remove(Flinch, self.events)
            battler.remove(ProtectionActive, self.events)
            from .moves.misc_status import FollowMeActive, HelpingHandActive
            battler.remove(FollowMeActive, self.events)
            battler.remove(HelpingHandActive, self.events)
        for side in self.sides:
            side.remove(ProtectionActive, self.events)

        for msg in self.field.tick_weather_terrain():
            self.log.append(msg)
        was_magic_room = self.field.magic_room
        ticked = self.field.tick_global()
        if was_magic_room and not self.field.magic_room:
            self.resync_items()  # Magic Room just expired: items work again
        for msg in ticked:
            self.log.append(msg)

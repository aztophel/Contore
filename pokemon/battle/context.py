"""Context objects that replace the old apply_move(...) signature:

    apply_move(move_name, attacker_sp, attacker_state, attacker_lvl,
               atk_name, defender_sp, defender_state, defender_lvl,
               def_name, turn_number, _bounced, field, spread_mult,
               defender_side, beat_up_base_attacks, own_moveset,
               current_pp, pursuit_doubled, me_first_boost,
               target_moveset, def_slot, atk_slot)

21 positional parameters, several of which are just different views of the
same two Pokémon (species dict + state + level + display name, twice over).
Every hook below takes ONE argument -- `ctx` -- and everything that used to
be a separate parameter is now either:
  - a field on BattleContext/MoveContext (turn number, field, bounced-flag,
    spread modifier, ...), or
  - retrievable from `user`/`target` directly, since Battler is now the
    single source of truth for its own species/level/name/moveset instead
    of that data being smeared across parallel arguments, or
  - a Component temporarily attached to the Battler (e.g. a `Bounced`
    marker instead of a `_bounced` bool parameter).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import BattleEngine
    from .battler import Battler
    from .field import Field, Side
    from .move import Move
    from ..ecs.events import EventBus
    from ..generations.ruleset import GenerationRuleset


@dataclass
class BattleContext:
    """Long-lived, once-per-battle context, owned by the BattleEngine and
    handed to every hook so effects can read/mutate shared battle state
    without a dozen separate function parameters for "field", "turn",
    "rng", etc."""
    engine: "BattleEngine"
    field: "Field"

    @property
    def turn(self) -> int:
        return self.engine.turn_number

    @property
    def ruleset(self) -> "GenerationRuleset":
        """[ADDITIVE / Track C] Mirrors MoveContext's own `.ruleset`
        property -- needed so code that's meant to work from EITHER
        context type (status.py's inflict_status, for instance, which a
        status-setting held item's on_end_turn hook calls with a bare
        BattleContext rather than a MoveContext) doesn't have to branch on
        which one it got. Found missing via live-testing a generation-
        gated sleep-duration check that assumed this already existed."""
        return self.engine.ruleset

    def log(self, message: str) -> None:
        self.engine.log.append(message)


@dataclass
class MoveResult:
    """Mutable accumulator that hooks read from and write to while resolving
    one move use. Inspected afterwards to decide what the UI prints and
    what downstream systems (EV yield, Pickup, achievements, ...) observe."""
    messages: list[str] = field(default_factory=list)
    total_damage: int = 0
    hits: int = 0
    critical: bool = False
    effectiveness: float = 1.0
    failed: bool = False
    missed: bool = False
    fainted: list["Battler"] = field(default_factory=list)

    def log(self, message: str) -> None:
        self.messages.append(message)


@dataclass
class MoveContext:
    """Everything about *this specific use* of a move."""
    battle: BattleContext
    move: "Move"
    user: "Battler"
    targets: list["Battler"] = field(default_factory=list)

    source_effect: str | None = None    # Showdown `move.sourceEffect` for a queued move: "round" when Round pulled this action up behind another Round (it then has double power)
    is_bounced: bool = False        # Magic Bounce / Magic Coat replay
    is_snatched: bool = False
    spread_modifier: float = 1.0    # multi-target damage reduction
    pursuit_doubled: bool = False
    me_first_boost: bool = False
    # Set by the engine when this move's USER has already been zeroed on
    # purpose (Explosion / Self-Destruct, `Move.self_destruct == "always"`).
    # The damage loop normally stops once the ATTACKER is down (a Rocky
    # Helmet KO between hits of a multi-hit move, say); this tells it the
    # attacker being down is the move's own doing and the hit must still land.
    user_self_ko: bool = False
    # The ONE target the engine is currently resolving (accuracy, damage,
    # on_hit...) inside a possibly multi-target move. Hooks that react to
    # being the target (Minimize's always-hit / double damage) must test
    # `owner is ctx.current_target`, NOT `owner in ctx.targets`: the event
    # bus consults every component on the field, so in a spread move a
    # minimized Pokemon in `ctx.targets` would otherwise modify the hit
    # on its un-minimized neighbour.
    current_target: "Battler | None" = None
    # Set by the damage step when the current target turned out to be IMMUNE
    # (type chart, an ability, airborne vs a Ground move): the hit "did
    # nothing". The engine then skips secondary effects and doesn't count the
    # move as having connected -- before this, Thunderbolt could paralyze a
    # Ground type and Jump Kick never crashed into a Ghost.
    hit_blocked: bool = False
    # The move's FINAL type for this hit (after the move's own and the user's
    # ability's type change, e.g. Pixilate/Normalize), set by the damage step.
    # Defender-side hooks (Thick Fat, Heatproof, Dry Skin) test this, not
    # `ctx.move.spec.type_name`.
    move_type: str | None = None

    result: MoveResult = field(default_factory=MoveResult)

    def log(self, message: str) -> None:
        self.result.log(message)

    # --- [ADDITIVE / Track C] ---------------------------------------------
    # Convenience passthroughs to the engine, mirroring what BattleContext
    # already exposes directly. Added so move/ability/volatile code can write
    # `ctx.events.reduce(...)` instead of `ctx.battle.engine.events.reduce(...)`
    # everywhere. Pure additions -- nothing above this line changed.
    @property
    def engine(self) -> "BattleEngine":
        return self.battle.engine

    @property
    def events(self) -> "EventBus":
        return self.battle.engine.events

    @property
    def ruleset(self) -> "GenerationRuleset":
        return self.battle.engine.ruleset
    # ------------------------------------------------------------------ ---

    @property
    def user_side(self) -> "Side":
        return self.battle.field.side_of(self.user)

    def target_side(self, battler: "Battler") -> "Side":
        return self.battle.field.side_of(battler)

    # --- [ADDITIVE / Track C] -----------------------------------------
    # Track D's abilities/held_items batch (see abilities/_util.py's
    # docstring) was written against `ctx.field.*` directly -- shorter than
    # `ctx.battle.field.*` and matches how `ctx.engine`/`ctx.events` above
    # already flatten one level of indirection. Confirmed safe: nothing
    # above this line is touched, this only adds a read-only passthrough.
    @property
    def field(self) -> "Field":
        return self.battle.field
    # ------------------------------------------------------------------

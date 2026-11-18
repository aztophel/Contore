"""Move base class + BasicMove.

Every move in the game is a subclass of `Move`. Only moves with genuinely
bespoke behaviour need a dedicated subclass (see moves_examples.py);
everything else is `BasicMove`, built straight from the same PokeAPI move
cache the bot already fetches and stores (no new data-fetching code needed
to migrate a move -- only new *behaviour* when BasicMove's generic handling
isn't enough).

Hook categories:
    Legality / targeting     on_try_use, get_targets, on_modify_priority
    Accuracy                 on_modify_accuracy
    Damage calc (per target) on_modify_type, on_base_power, on_modify_power,
                              on_modify_damage
    Resolution (per target)  on_hit, on_secondary_effect
    Reactive                 on_target_faint, on_move_fail_message

All hooks are async, take a MoveContext first, and default to "no opinion"
(return None / do nothing) so a subclass only overrides what it needs. Value
-modifying hooks return the new value or None to leave it unchanged -- this
lets the EventBus chain modifiers from the move itself, both battlers'
abilities, held items, and field/side conditions through one call shape.
"""
from __future__ import annotations
from abc import ABC
from dataclasses import dataclass, field
from enum import Enum, Flag, auto
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import MoveContext
    from .battler import Battler


class MoveCategory(Enum):
    PHYSICAL = "physical"
    SPECIAL = "special"
    STATUS = "status"


class MoveTarget(Enum):
    """Mirrors PokeAPI's move.target field. This *replaces* the old `mode`
    string ('single' / 'auto' / 'ally') that BattleView threaded by hand
    through several call sites, each with a slightly different copy of the
    same branching logic. See targeting.py for the single implementation."""
    SELECTED_POKEMON = "selected-pokemon"
    ALL_OPPONENTS = "all-opponents"
    ALL_OTHER_POKEMON = "all-other-pokemon"
    RANDOM_OPPONENT = "random-opponent"
    USER = "user"
    USER_OR_ALLY = "ally"
    USERS_FIELD = "users-field"
    OPPONENTS_FIELD = "opponents-field"
    ENTIRE_FIELD = "entire-field"
    USER_AND_ALLIES = "user-and-allies"
    ALL_POKEMON = "all-pokemon"

    @classmethod
    def from_pokeapi(cls, raw: str) -> "MoveTarget":
        try:
            return cls(raw)
        except ValueError:
            return cls.SELECTED_POKEMON


class MoveFlag(Flag):
    NONE = 0
    CONTACT = auto()
    SOUND = auto()
    BULLET = auto()
    PUNCH = auto()
    BITE = auto()
    PULSE = auto()
    POWDER = auto()
    DANCE = auto()
    RECHARGE = auto()
    SUBSTITUTE_BYPASS = auto()   # sound moves & similar hit through Substitute
    PROTECT_BYPASS = auto()      # Feint and friends
    SLICING = auto()             # Sharpness (Showdown `slicing`)
    WIND = auto()                # Wind Rider / Wind Power / Tailwind-adjacent (Showdown `wind`)


@dataclass
class StatChange:
    stat: str       # PokeAPI naming: 'attack', 'defense', 'special-attack', ...
    change: int


@dataclass
class MoveSpec:
    slug: str
    display_name: str
    type_name: str
    category: MoveCategory
    base_power: int | None
    accuracy: int | None
    pp: int
    priority: int = 0
    target: MoveTarget = MoveTarget.SELECTED_POKEMON
    flags: MoveFlag = MoveFlag.NONE
    min_hits: int = 1
    max_hits: int = 1
    crit_rate_stage: int = 0
    always_hits: bool = False
    is_ohko: bool = False

    ailment: str | None = None
    ailment_chance: int = 0
    stat_changes: tuple[StatChange, ...] = field(default_factory=tuple)
    stat_chance: int = 0
    flinch_chance: int = 0
    drain: int = 0        # % of damage dealt; negative = recoil
    healing: int = 0      # % of user's max HP

    # -- runtime path: exact, cheap, called ONCE per slug at bot startup ----
    def to_dict(self) -> dict[str, Any]:
        """Our own on-disk shape (data/moves_data.json), matching this
        dataclass's fields 1:1. Written by the offline pipeline; read back
        by MoveRegistry.load() at boot. Never touched mid-battle."""
        return {
            "slug": self.slug, "display_name": self.display_name,
            "type_name": self.type_name, "category": self.category.value,
            "base_power": self.base_power, "accuracy": self.accuracy,
            "pp": self.pp, "priority": self.priority, "target": self.target.value,
            "flags": self.flags.value, "min_hits": self.min_hits, "max_hits": self.max_hits,
            "crit_rate_stage": self.crit_rate_stage, "always_hits": self.always_hits,
            "is_ohko": self.is_ohko, "ailment": self.ailment,
            "ailment_chance": self.ailment_chance,
            "stat_changes": [{"stat": sc.stat, "change": sc.change} for sc in self.stat_changes],
            "stat_chance": self.stat_chance, "flinch_chance": self.flinch_chance,
            "drain": self.drain, "healing": self.healing,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MoveSpec":
        """Direct, exact reconstruction from our own data/moves_data.json.
        This is what actually runs at bot startup -- a plain field-for-field
        rebuild, with no reinterpretation of anyone else's data shape."""
        return cls(
            slug=data["slug"], display_name=data["display_name"], type_name=data["type_name"],
            category=MoveCategory(data["category"]), base_power=data["base_power"],
            accuracy=data["accuracy"], pp=data["pp"], priority=data.get("priority", 0),
            target=MoveTarget(data.get("target", "selected-pokemon")),
            flags=MoveFlag(data.get("flags", 0)),
            min_hits=data.get("min_hits", 1), max_hits=data.get("max_hits", 1),
            crit_rate_stage=data.get("crit_rate_stage", 0),
            always_hits=data.get("always_hits", False), is_ohko=data.get("is_ohko", False),
            ailment=data.get("ailment"), ailment_chance=data.get("ailment_chance", 0),
            stat_changes=tuple(StatChange(sc["stat"], sc["change"]) for sc in data.get("stat_changes", [])),
            stat_chance=data.get("stat_chance", 0), flinch_chance=data.get("flinch_chance", 0),
            drain=data.get("drain", 0), healing=data.get("healing", 0),
        )

    # -- offline-pipeline path: runs ONCE EVER per move, not at bot runtime -
    @classmethod
    def from_pokeapi_response(cls, move_dict: dict[str, Any]) -> "MoveSpec":
        """ETL from the shape the *existing* monolith's PokeAPI move cache
        (get_move_data()) produces. Called exclusively by the offline data
        pipeline (pokemon/dex/pokedex_builder.py) when regenerating
        data/moves_data.json for a commit -- the live bot process never
        calls this. Kept next to the dataclass it targets purely so the
        field mapping has one obvious home; the interpretation work itself
        (which raw keys mean what) happens exactly once, ahead of time."""
        flags = MoveFlag.NONE
        flag_map = {
            "makes_contact": MoveFlag.CONTACT, "sound": MoveFlag.SOUND,
            "bullet": MoveFlag.BULLET, "punch": MoveFlag.PUNCH,
            "bite": MoveFlag.BITE, "pulse": MoveFlag.PULSE,
            "powder": MoveFlag.POWDER, "dance": MoveFlag.DANCE,
            "recharge": MoveFlag.RECHARGE,
        }
        for key, flag in flag_map.items():
            if move_dict.get(key):
                flags |= flag
        if move_dict.get("sound"):
            flags |= MoveFlag.SUBSTITUTE_BYPASS

        return cls(
            slug=move_dict["name"],
            display_name=move_dict.get("display_name", move_dict["name"]),
            type_name=move_dict["type"],
            category=MoveCategory(move_dict["category"]),
            base_power=move_dict.get("power"),
            accuracy=move_dict.get("accuracy"),
            pp=move_dict.get("pp", 5),
            priority=move_dict.get("priority", 0),
            target=MoveTarget.from_pokeapi(move_dict.get("target", "selected-pokemon")),
            flags=flags,
            min_hits=move_dict.get("min_hits", 1),
            max_hits=move_dict.get("max_hits", 1),
            crit_rate_stage=move_dict.get("crit_rate", 0),
            always_hits=bool(move_dict.get("always_hits")),
            is_ohko=bool(move_dict.get("is_ohko")),
            ailment=move_dict.get("ailment"),
            ailment_chance=move_dict.get("ailment_chance", 0),
            stat_changes=tuple(
                StatChange(sc["stat"], sc["change"]) for sc in move_dict.get("stat_changes", [])
            ),
            stat_chance=move_dict.get("stat_chance", 0),
            flinch_chance=move_dict.get("flinch_chance", 0),
            drain=move_dict.get("drain", 0),
            healing=move_dict.get("healing", 0),
        )


class Move(ABC):
    """Base class for every move. **IMPORTANT, hand-learned the hard way
    (see CONTRACT_CHANGES.md's 2026-09-15 "20 moves silently dealt zero
    damage" entry): this base class's own on_hit does NOTHING.** A
    variable-power damaging move (one that overrides on_base_power/
    on_modify_power to compute its power some other way -- HP ratio,
    weight, whatever) MUST extend `BasicMove` below, not this class
    directly, or it will silently deal zero damage forever with no error
    of any kind -- it'll log "used <Move>!" and then just stop, looking
    exactly like nothing happened. Extend `Move` directly ONLY when
    writing your OWN complete on_hit (status moves, hazards, screens,
    weather, Substitute, Knock Off's post-damage item removal, ...) --
    i.e. only when you do NOT want BasicMove's default damage-then-stat-
    changes behavior at all. When in doubt: if the move deals damage via
    the normal formula (even a variable one), it's BasicMove."""
    def __init__(self, spec: MoveSpec):
        self.spec = spec

    @property
    def slug(self) -> str: return self.spec.slug
    @property
    def category(self) -> MoveCategory: return self.spec.category

    def has_flag(self, flag: MoveFlag) -> bool:
        return bool(self.spec.flags & flag)

    # -- legality / targeting --------------------------------------------
    async def on_try_use(self, ctx: "MoveContext") -> str | None:
        """Return a failure message to abort before targets are even chosen
        (Belly Drum below half HP, Explosion vs. an opponent with Damp,
        ...). None = proceed."""
        return None

    async def get_targets(self, ctx: "MoveContext") -> list["Battler"]:
        from .targeting import resolve_targets
        return resolve_targets(self.spec.target, ctx)

    async def on_before_turn(self, ctx: "MoveContext") -> None:
        """Called for every move SUBMITTED this turn, before any action is
        ordered or executed (Showdown's `priorityChargeCallback` /
        `beforeTurnCallback`). Focus Punch's "tightening its focus" setup."""

    async def before_move_callback(self, ctx: "MoveContext") -> bool:
        """Called once the user is actually able to act (status checks
        passed) and BEFORE PP is spent. Return True to cancel the move
        without costing PP (Showdown `beforeMoveCallback`: Focus Punch's
        lost focus)."""
        return False

    async def on_try_immunity(self, ctx: "MoveContext", target: "Battler") -> bool:
        """Return False if `target` is immune to THIS move for a move-specific
        reason (Showdown `onTryImmunity`: Dream Eater needs a sleeping
        target) -- reported as "It doesn't affect ..."."""
        return True

    def ignores_defensive_boosts(self) -> bool:
        """Showdown `ignoreDefensive` (Chip-Away, Sacred Sword): the target's
        Def/SpD stages are not applied to this move's damage."""
        return False

    def ignores_evasion(self) -> bool:
        """Showdown `ignoreEvasion`: the target's evasion stage isn't applied
        to this move's accuracy."""
        return False

    def can_use_while_asleep(self) -> bool:
        """Showdown's `sleepUsable` (Snore, Sleep Talk)."""
        return False

    async def on_move_start(self, ctx: "MoveContext") -> None:
        """Called once the move is committed to -- PP spent, on_try_use
        passed, targets resolved (`ctx.targets`, possibly empty) -- and
        BEFORE any target is checked for Protect/accuracy or hit. Showdown's
        `useMove` stage between TryMove and the hit loop. Explosion-style
        moves use it to take their user down regardless of what happens
        next (see moves/self_faint.py)."""

    async def on_after_move(self, ctx: "MoveContext", connected: bool) -> None:
        """Called once after the hit loop (also on the no-target bail-out,
        with connected=False). `connected` = the move landed on at least one
        target without failing -- misses, Protect and immunity leave it
        False. Anything logged here appears after the move's own messages."""

    async def on_modify_priority(self, ctx: "MoveContext", priority: int) -> int | None:
        return None

    # -- accuracy -----------------------------------------------------------
    async def on_modify_accuracy(self, ctx: "MoveContext", target: "Battler", accuracy: float) -> float | None:
        return None

    # -- damage calculation (called once per target) ------------------------
    async def on_modify_type(self, ctx: "MoveContext", target: "Battler", type_name: str) -> str | None:
        return None

    def never_crits(self, ctx: "MoveContext") -> bool:
        """Showdown `willCrit: false`: this hit can never be a critical hit (Gens 3-4 Future Sight / Doom Desire)."""
        return False

    async def on_modify_category(self, ctx: "MoveContext", target: "Battler", category: "MoveCategory") -> "MoveCategory | None":
        """Showdown's `move.category = ...` in an `onModifyMove`: the category THIS hit uses (Gen 3 Weather Ball follows its weather type). None keeps the spec's."""
        return None

    async def on_modify_stats(self, ctx: "MoveContext", target: "Battler", atk: int, dfe: int) -> "tuple[int, int] | None":
        """The FINAL attack and defense this hit uses, after every stage / item / ability modifier (Showdown's priority -101 `onModifyAtk` / `onFoeModifyDef` with `event.modifier = 1`:
        Gens 3-4 Beat Up uses base stats). None keeps them."""
        return None

    async def on_modify_type_effectiveness(self, ctx: "MoveContext", target: "Battler", effectiveness: float) -> float | None:
        """[ADDITIVE / Track C] Move-level effectiveness override --
        Freeze-Dry (always super-effective on Water), Thousand Arrows
        (Ground hits Flying), Flying Press (also Flying-type damage rolled
        in), Foresight/Miracle Eye's Normal-vs-Ghost & Fighting-vs-Ghost.
        Distinct from Ability.on_modify_type_effectiveness (Scrappy, Ring
        Target, Wonder Guard) below -- this one lives on the MOVE, so it's
        called directly once rather than dispatched through the event bus.
        Old code: contore.py:19985-19990's per-move-name branches."""
        return None

    async def on_hit_count(self, ctx: "MoveContext") -> int | None:
        """A move whose NUMBER of hits isn't `min_hits..max_hits` (Beat Up: one per healthy party member) returns it here; None = use the data."""
        return None

    async def on_base_power(self, ctx: "MoveContext", target: "Battler") -> int | None:
        """Variable-power moves (Gyro Ball, Low Kick, Eruption, Reversal,
        Trump Card, ...) override this instead of the engine special-casing
        their names in one giant if/elif chain, as the monolith did."""
        return None

    async def on_modify_power(self, ctx: "MoveContext", target: "Battler", power: int) -> int | None:
        return None

    async def on_modify_damage(self, ctx: "MoveContext", target: "Battler", damage: int) -> int | None:
        return None

    # -- resolution -----------------------------------------------------------
    async def on_hit(self, ctx: "MoveContext", target: "Battler") -> None:
        """Status moves implement their whole effect here (see any
        StatusMove subclass). Damaging moves use this for effects layered
        on top of the damage already applied (attaching Leech Seed, making
        a Substitute, applying stat drops, ...)."""

    async def on_secondary_effect(self, ctx: "MoveContext", target: "Battler") -> None:
        """Chance-based ailment/flinch/stat-change rolls. Kept separate from
        on_hit so Serene Grace / Sheer Force have exactly one place to
        modify the odds (or, for Sheer Force, suppress this entirely while
        boosting on_modify_power instead)."""

    async def on_move_fail_message(self, ctx: "MoveContext") -> str:
        return "But it failed!"

    async def on_target_faint(self, ctx: "MoveContext", target: "Battler") -> None:
        """Move-level (not ability/volatile) reactions to a target fainting
        from this specific move, e.g. Grudge's PP drain."""


_REFLECTABLE_MOVES: "frozenset[str] | None" = None


def is_reflectable(slug: str) -> bool:
    """Showdown `flags.reflectable` (data/reflectable_moves.json, from tests/tools/make_reflectable_moves.js): the status moves
    Magic Bounce / Magic Coat send back at the user."""
    global _REFLECTABLE_MOVES
    if _REFLECTABLE_MOVES is None:
        import json
        from pathlib import Path
        _REFLECTABLE_MOVES = frozenset(json.loads((Path(__file__).resolve().parents[2] / "data" / "reflectable_moves.json").read_text(encoding="utf-8")))
    return slug in _REFLECTABLE_MOVES


_SUB_BYPASS: "dict[str, frozenset[str]] | None" = None


def bypasses_substitute_move(slug: str, gen: int) -> bool:
    """Showdown `flags.bypasssub` IN THIS GENERATION (data/substitute_bypass.json, from tests/tools/make_substitute_bypass.js): sound moves only from Gen 6,
    Taunt / Disable / Haze / Attract / Whirlwind ... throughout. (The `SUBSTITUTE_BYPASS` MoveFlag, derived from a `sound` key, was wrong for several moves and every
    generation before 6.)"""
    global _SUB_BYPASS
    if _SUB_BYPASS is None:
        import json
        from pathlib import Path
        raw = json.loads((Path(__file__).resolve().parents[2] / "data" / "substitute_bypass.json").read_text(encoding="utf-8"))
        _SUB_BYPASS = {g: frozenset(v) for g, v in raw.items()}
    return slug in _SUB_BYPASS.get(str(max(3, min(9, gen))), frozenset())


_SELF_BOOST_MOVES: "frozenset[str] | None" = None


def _self_boost_moves() -> "frozenset[str]":
    """data/self_boost_moves.json (tests/tools/make_self_boost_moves.js): damaging moves whose stat change is Showdown's primary
    `self: {boosts}`. PokeAPI can't say whose stats a stat change is, and the engine's "positive => user, negative => target"
    heuristic sent every self-inflicted DROP to the target."""
    global _SELF_BOOST_MOVES
    if _SELF_BOOST_MOVES is None:
        import json
        from pathlib import Path
        _SELF_BOOST_MOVES = frozenset(json.loads((Path(__file__).resolve().parents[2] / "data" / "self_boost_moves.json").read_text(encoding="utf-8")))
    return _SELF_BOOST_MOVES


class BasicMove(Move):
    """The generic, data-driven Move used for anything whose behaviour is
    fully described by its MoveSpec -- i.e. most of the movedex. Handles
    standard damage application, a single %-chance ailment, a single
    %-chance flinch, %-chance stat changes, and drain/recoil/healing
    percentages. Anything else (Substitute, hazards, weather, two-turn
    charging, fixed/variable damage, OHKO, ...) needs a dedicated subclass;
    see moves_examples.py.
    """

    async def on_hit(self, ctx: "MoveContext", target: "Battler") -> None:
        if self.category is not MoveCategory.STATUS:
            # Every Move (Basic or custom) shares one damage calculator;
            # only the *inputs* differ per move, and those already flow
            # through on_base_power/on_modify_power/on_modify_type above.
            from .damage import calculate_and_apply_damage  # Phase 3
            hits_before = ctx.result.hits
            ctx.sub_absorbed = 0
            await calculate_and_apply_damage(ctx, self, target)
            # Showdown rolls a multi-hit move's secondary effects on EVERY hit that lands (Twineedle's poison, Double Iron Bash's flinch, Parental Bond's
            # second hit...), not once per move.
            # A hit a Substitute absorbed rolls NO secondary AIMED AT THE TARGET (status, flinch, its stat drops) -- Showdown `HIT_SUBSTITUTE` -- but the user's own
            # secondary boosts (Flame Charge, Power-Up Punch, ...) still apply, so both counts are kept.
            ctx.hits_landed = max(1, ctx.result.hits - hits_before)
            ctx.hits_on_target = max(0, ctx.hits_landed - ctx.sub_absorbed)
        if ctx.hit_blocked:       # absorbed (Flash Fire, Sap Sipper...), nullified (Primordial Sea) or immune: no secondary stat change either
            return
        landed = getattr(ctx, "hits_landed", 1)
        for i in range(1 if self.spec.slug in _self_boost_moves() else landed):
            await self._apply_stat_changes(ctx, target, on_body=i < getattr(ctx, "hits_on_target", landed), hit_index=i)

    async def on_secondary_effect(self, ctx: "MoveContext", target: "Battler") -> None:
        import random
        spec = self.spec
        # [Track C fix, see CONTRACT_CHANGES.md] on_suppress_secondary_effect
        # (Sheer Force) and on_modify_secondary_chance (Serene Grace,
        # Shield Dust) were declared on ability.py from the start but
        # nothing called them yet -- this was always meant to be the call
        # site (see ability.py's own docstrings for both hooks, and
        # NOTES_FOR_TRACK_C.md). Old code: contore.py:18472-18474 (Sheer
        # Force's has_secondary guard) and the `_sc_serene`/Shield-Dust
        # checks around contore.py:19200-19222.
        if not spec.ailment and not spec.flinch_chance and self.category is MoveCategory.STATUS:    # (a DAMAGING move may still gain a flinch: King's Rock / Razor Fang / Stench)
            return
        if await ctx.events.first("on_suppress_secondary_effect", ctx):
            return
        for _ in range(getattr(ctx, "hits_on_target", 1)):        # one roll per landed hit
            if spec.ailment and spec.ailment_chance:
                chance = await ctx.events.reduce("on_modify_secondary_chance", ctx, spec.ailment_chance)
                if random.randint(1, 100) <= chance:
                    if spec.ailment == "confusion":               # a VOLATILE, not a status: Psybeam, Hurricane, Water Pulse, Dizzy Punch, Dynamic Punch, Chatter ...
                        from .moves.status_ailments import apply_confusion
                        await apply_confusion(ctx, target, announce_failure=False)
                    else:
                        from .status import inflict_status
                        await inflict_status(ctx, target, spec.ailment)
            if spec.flinch_chance or self.category is not MoveCategory.STATUS:
                # A damaging move with NO flinch chance still asks the hook: King's Rock / Razor Fang / Stench add a 10% flinch to it (Showdown pushes a
                # `{chance: 10, volatileStatus: 'flinch'}` secondary). The old `if spec.flinch_chance:` guard meant those three never worked.
                chance = await ctx.events.reduce("on_modify_secondary_chance", ctx, spec.flinch_chance)
                if chance and random.randint(1, 100) <= chance:
                    from .volatile import Flinch
                    from .volatile import FocusPunchCharge
                    if not target.has(FocusPunchCharge):   # Showdown: focuspunch `onTryAddVolatile` blocks flinch
                        target.add(Flinch(), ctx.battle.engine.events)

    async def _apply_stat_changes(self, ctx: "MoveContext", target: "Battler", on_body: bool = True, hit_index: int = 0) -> None:
        import random
        spec = self.spec
        if not spec.stat_changes:
            return
        if spec.slug in _self_boost_moves():
            # Showdown `move.self.boosts` (Close Combat, Superpower, Overheat, Leaf Storm, ...): the drop is the USER'S, applied every time
            # the move hits -- no chance roll, and not a "secondary effect", so Sheer Force / Serene Grace / Shield Dust don't touch it.
            from .stats import apply_stat_change
            for sc in spec.stat_changes:
                await apply_stat_change(ctx, ctx.user, sc.stat, sc.change)
            return
        # Same suppression/chance-modification pipeline as
        # on_secondary_effect above, and for the same reason (Sheer Force/
        # Shield Dust apply to a move's stat-change roll exactly like they
        # do to its ailment/flinch roll -- old code's Shield Dust check at
        # contore.py:19220-19222 explicitly names "the stat effect").
        if await ctx.events.first("on_suppress_secondary_effect", ctx):
            return
        if spec.stat_chance and self.category is not MoveCategory.STATUS:   # a STATUS move's stat change is its primary effect: no chance roll, Shield Dust can't stop it
            # whose stats this secondary changes (see the recipient rule below): Shield Dust only stops secondaries aimed at ITS owner
            ctx.secondary_on_user = self.category is not MoveCategory.STATUS and all(sc.change > 0 for sc in spec.stat_changes)
            chance = await ctx.events.reduce("on_modify_secondary_chance", ctx, spec.stat_chance)
            ctx.secondary_on_user = False
            if random.randint(1, 100) > chance:
                return
        for sc in spec.stat_changes:
            # NOTE: PokeAPI doesn't explicitly say which side a stat change
            # applies to for every move -- this self-vs-target heuristic
            # (positive changes on damaging moves buff the user; everything
            # else hits the target) covers the overwhelming majority, but a
            # handful of moves need an explicit override. Flagged here
            # rather than silently assumed correct.
            recipient = ctx.user if sc.change > 0 and self.category is not MoveCategory.STATUS else target
            if recipient is target and not on_body:
                continue                      # this hit went into a Substitute
            if recipient is ctx.user and self.category is not MoveCategory.STATUS:
                done = ctx.__dict__.setdefault("_self_boosts_done", set())          # the USER's own secondary is applied ONCE per move use, however many targets it hit (Diamond Storm)
                if ctx.ruleset.number >= 7 and (sc.stat, sc.change, hit_index) in done:       # (Gens <= 6: once per TARGET hit)
                    continue
                done.add((sc.stat, sc.change, hit_index))
            from .stats import apply_stat_change
            await apply_stat_change(ctx, recipient, sc.stat, sc.change)

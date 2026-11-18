"""Battler: the ECS Entity representing one Pokemon in battle.

Battler extends ecs.component.Entity but deliberately does NOT declare
__slots__ (see component.py's Entity docstring): moves and abilities
routinely need to stash a simple one-shot flag or counter on a specific
Battler (a Sheer-Force-style suppression flag, Choice-lock state, Truant's
loaf-this-turn toggle, a berry-already-eaten marker, ...) and forcing every
one of those through a formal Component class would multiply the file count
for no real benefit -- they're identical in spirit to the monolith's
FighterState just having a bare instance attribute for each. Anything with
real *behaviour* attached (a hook that needs to fire, a duration that ticks
down and needs to be inspected generically by the UI) is still a proper
Component via volatile.py; anything that's just "does this Battler currently
have this flag" stays a plain dynamic attribute, set directly by whichever
move/ability/item file needs it. Old code: FighterState (contore.py, the
`class FighterState` block) plus the free-floating stat helper functions
right above it (calc_max_hp, calc_stat, nature_multiplier, STAGE_MULT,
ACC_EVA_MULT).
"""
from __future__ import annotations
import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..ecs.component import Entity, Component, Effect

if TYPE_CHECKING:
    from ..ecs.events import EventBus
    from .context import MoveContext
    from .field import Field

STAGE_KEYS: tuple[str, ...] = ("atk", "def", "spa", "spd", "spe", "acc", "eva")

# Old code: STAGE_MULT / ACC_EVA_MULT (contore.py, just above BattleField).
STAGE_MULT: dict[int, float] = {
    -6: 2/8, -5: 2/7, -4: 2/6, -3: 2/5, -2: 2/4, -1: 2/3,
     0: 1.0,  1: 3/2,  2: 4/2,  3: 5/2,  4: 6/2,  5: 7/2,  6: 8/2,
}
ACC_EVA_MULT: dict[int, float] = {
    -6: 3/9, -5: 3/8, -4: 3/7, -3: 3/6, -2: 3/5, -1: 3/4,
     0: 1.0,  1: 4/3,  2: 5/3,  3: 6/3,  4: 7/3,  5: 8/3,  6: 9/3,
}

# Showdown `ignoreKlutz: true` (data/items.ts): these items keep working for a Klutz holder (Embargo / Magic Room still suppress them).
_IGNORE_KLUTZ = frozenset({"Ability Shield", "Macho Brace", "Power Anklet", "Power Band", "Power Belt", "Power Bracer", "Power Lens", "Power Weight"})
_STAGE_ALIASES = {"accuracy": "acc", "evasion": "eva", "evasiveness": "eva"}

_STAT_CHANGE_LABELS = {
    1: "rose", 2: "sharply rose", 3: "drastically rose",
    -1: "fell", -2: "harshly fell", -3: "severely fell",
}
_STAT_DISPLAY = {"atk": "Attack", "def": "Defense", "spa": "Sp. Atk",
                  "spd": "Sp. Def", "spe": "Speed", "acc": "Accuracy", "eva": "Evasiveness"}

NATURE_MODS: dict[str, tuple[str, str] | None] = {
    "Hardy": None, "Docile": None, "Serious": None, "Bashful": None, "Quirky": None,
    "Lonely": ("atk", "def"), "Brave": ("atk", "spe"), "Adamant": ("atk", "spa"), "Naughty": ("atk", "spd"),
    "Bold": ("def", "atk"), "Relaxed": ("def", "spe"), "Impish": ("def", "spa"), "Lax": ("def", "spd"),
    "Timid": ("spe", "atk"), "Hasty": ("spe", "def"), "Jolly": ("spe", "spa"), "Naive": ("spe", "spd"),
    "Modest": ("spa", "atk"), "Mild": ("spa", "def"), "Quiet": ("spa", "spe"), "Rash": ("spa", "spd"),
    "Calm": ("spd", "atk"), "Gentle": ("spd", "def"), "Sassy": ("spd", "spe"), "Careful": ("spd", "spa"),
}


def nature_multiplier(nature: str, stat_key: str) -> float:
    """Old code: nature_multiplier(). "Perfect"/"Wretch" are Contore-custom
    training-item natures (not one of the real 25), kept for parity."""
    if nature == "Perfect":
        return 1.1
    if nature == "Wretch":
        return 0.9
    mod = NATURE_MODS.get(nature)
    if not mod:
        return 1.0
    up, down = mod
    return 1.1 if stat_key == up else 0.9 if stat_key == down else 1.0


@dataclass
class Identity(Component):
    """Static-for-the-battle identity data. `held_item` and `ability_slug`
    can still change mid-battle (Trick, Skill Swap, ...) -- Battler exposes
    the *effective* versions through effective_item()/effective_ability(),
    which layer temporary overrides on top rather than mutating this."""
    species_id: int
    species_name: str
    types: list[str]
    base_stats: dict[str, int]
    ability_slug: str
    held_item: str = ""
    gender: str = "genderless"
    nickname: str | None = None
    level: int = 50
    nature: str = "Hardy"
    ivs: dict[str, int] = field(default_factory=dict)
    evs: dict[str, int] = field(default_factory=dict)
    weight_kg: float = 10.0
    friendship: int = 70
    can_evolve: bool = False  # for Eviolite eligibility

    @property
    def name(self) -> str:
        return self.nickname or self.species_name


_FAINT_CLOCK = itertools.count(1)


@dataclass
class Vitals(Component):
    """Old code: FighterState.hp/max_hp/status/status_turns/toxic_counter.
    Status is deliberately NOT a Volatile component: a Pokemon can only
    ever have one major status at a time, unlike volatiles which stack
    freely, so a couple of plain fields here is a better fit than an
    Effect that would need singleton enforcement."""
    hp: int
    max_hp: int
    status: str | None = None
    status_turns: int = 0
    toxic_counter: int = 0
    # Monotonic stamp of WHEN hp last dropped to 0 (0 = not fainted). Set
    # centrally in __setattr__ so EVERY path that KOs a Pokemon -- damage,
    # recoil, residuals, Bide, fixed-damage moves, self-destruct -- is
    # ordered for free, without each call site having to remember to record
    # it. Needed because Showdown decides a mutual KO by who fainted LAST
    # (sim/battle.ts `checkWin`), see BattleEngine.winner.
    faint_seq: int = field(default=0, repr=False, compare=False)

    def __setattr__(self, name, value):
        if name == "hp":
            old = self.__dict__.get("hp")
            object.__setattr__(self, "hp", value)
            if value <= 0:
                if old is None or old > 0:
                    object.__setattr__(self, "faint_seq", next(_FAINT_CLOCK))
            else:
                object.__setattr__(self, "faint_seq", 0)
            return
        object.__setattr__(self, name, value)


@dataclass
class Boosts(Component):
    stages: dict[str, int] = field(default_factory=lambda: {k: 0 for k in STAGE_KEYS})


# -- [ADDITIVE / Track C] --------------------------------------------------
@dataclass
class MoveSlot:
    """One of a Battler's up-to-four known moves. Old code: FighterState's
    parallel `moves`/`pp`/`pp_max` lists, indexed together by position --
    kept as a list of small objects here instead so `legal_moves()`
    (pokemon/ui/actions.py's contract) can hand back one self-contained
    object per slot rather than zipping three lists back together."""
    move_slug: str
    pp: int
    max_pp: int
    disabled: bool = False
    disabled_reason: str | None = None
    # Showdown `moveSlot.used`: set when PP is deducted, cleared on
    # switch-in. Last Resort needs every OTHER move to have been used.
    used: bool = False


@dataclass
class Moveset(Component):
    """See abilities/entry_effects.py's Forewarn (`foe.moves.slots`) for
    the field name this is already written against."""
    slots: list[MoveSlot] = field(default_factory=list)


class Battler(Entity):
    """No __slots__ -- see module docstring."""

    def __init__(self, battler_id: str, identity: Identity, vitals: Vitals,
                 boosts: Boosts | None = None, moves: "Moveset | None" = None):
        super().__init__(battler_id)
        self.add(identity)
        self.add(vitals)
        self.add(boosts or Boosts())
        self.add(moves or Moveset())
        # Layered overrides -- set by Skill Swap / Entrainment / Role Play /
        # Trace / Mummy / Gastro Acid (ability), Soak / Forest's Curse /
        # Trick-or-Treat / Burn Up (type). Old code:
        # FighterState.ability_override / type_override.
        self.ability_override: str | None = None
        self.type_override: list[str] | None = None
        # Old code: FighterState.mega_evolved / current_species -- when a
        # Battler Mega Evolves or Transforms, its *effective* species data
        # (name/types/base_stats/ability) changes without touching Identity.
        self.mega_evolved: bool = False
        self.current_species: dict | None = None
        self.transformed_into: str | None = None
        # Transform / Imposter (see volatile.transform_into): the target's calculated stats (all but HP), and the
        # moveset to restore on switch-out. `_orig_ability_slug` is set by `set_ability` the first time an ability
        # is swapped (Trace, Skill Swap, Role Play, Transform...) so leaving the field can put it back.
        self.stat_override: dict[str, int] | None = None
        self._pre_transform_slots = None
        self._orig_ability_slug: str | None = None
        # Showdown `activeMoveActions`: move actions taken since this Pokemon
        # last switched in, counted even for a turn lost to sleep/para/flinch
        # (Fake Out "only works on your first turn out").
        self.move_actions: int = 0
        # Showdown `Pokemon.attackedBy`: [(source Battler, move slug)] for every move that HIT this Pokemon, oldest first (`gotAttacked`). Gens 3-4 Mirror Move copies the
        # LAST one. Reset when this Pokemon leaves the field (`clearVolatile`); entries whose source is no longer active are pruned at the start of each turn (`engine`).
        self.attacked_by: list = []
        # Showdown `lastItem` / `usedItemThisTurn`: set when an item is USED (eaten, a herb, Fling, an Eject item ...), not when it is taken (Knock Off, Thief, Trick). Recycle, Pickup and Symbiosis read them.
        self.last_item: str | None = None
        self.used_item_this_turn: bool = False
        # Set by Side.place() the moment this Battler takes an active slot;
        # field.py's Field.side_of() relies on this being non-None for any
        # Battler currently on the field. A benched/not-yet-placed Battler
        # is simply None here.
        self.side: "object | None" = None

    # -- identity / vitals convenience ----------------------------------
    @property
    def identity(self) -> Identity:
        return self.require(Identity)

    @property
    def vitals(self) -> Vitals:
        return self.require(Vitals)

    @property
    def stages(self) -> dict[str, int]:
        return self.ensure(Boosts, Boosts).stages

    @property
    def moves(self) -> Moveset:
        return self.ensure(Moveset, Moveset)

    @property
    def name(self) -> str:
        return self.identity.name

    @property
    def fainted(self) -> bool:
        return self.vitals.hp <= 0

    def heal(self, amount: int) -> int:
        """Heal by `amount`, capped at max HP and at 0 (never heals a
        fainted Pokemon back up -- that's revival, a completely separate
        mechanic). Returns the ACTUAL amount restored, which every caller
        needs for its own message ("healed 20 HP!") rather than the
        requested amount, since those differ whenever this clips at the
        cap. Old code inlined `min(amount, max_hp - hp)` at every single
        recovery move, Leftovers, Wish, and drain-move call site
        separately -- one shared method here instead, per the
        instruction to turn a pattern repeated across the codebase into a
        utility function once it's noticed."""
        if self.fainted:
            return 0
        healed = max(0, min(amount, self.vitals.max_hp - self.vitals.hp))
        self.vitals.hp += healed
        return healed

    @property
    def level(self) -> int:
        return self.identity.level

    async def effective_weight(self, ctx) -> float:
        """Weight in kg after Autotomize (halves per use, additive stacks
        handled by whatever adds the modifier)/Light Metal/Heavy Metal-
        style hooks. Local dispatch (this Battler's own attached effects
        only) since it's "what does MY OWN ability/item say my weight
        is", the same shape as on_type_immunity. Used by the Low Kick/
        Grass Knot/Heavy Slam/Heat Crash family (moves/variable_power.py)
        -- old code: `_species_weight_kg` plus per-move weight-threshold
        tables, contore.py:16691-16698 and the Low Kick/Heavy Slam
        branches further down."""
        weight = float(self.identity.weight_kg)
        weight = float(await self.consult_reduce("on_modify_weight", ctx, weight))
        return max(0.1, weight)

    # -- effective (post-override) accessors -----------------------------
    def effective_ability(self) -> str:
        if self.ability_override is not None:
            return self.ability_override
        if self.current_species and self.current_species.get("ability"):
            return self.current_species["ability"]
        return self.identity.ability_slug

    def effective_types(self, base_types: list[str] | None = None) -> list[str]:
        if self.type_override is not None:
            return self.type_override
        if self.current_species and self.current_species.get("types"):
            return self.current_species["types"]
        return base_types if base_types is not None else self.identity.types

    def effective_item(self) -> str:
        """Old code: FighterState.effective_item() -- Klutz and Embargo both
        suppress the held item without actually removing it, so Trick still
        swaps the *real* item even while one side can't use it."""
        if self.effective_ability() == "klutz" and self.identity.held_item not in _IGNORE_KLUTZ:
            return ""
        # Magic Room (Showdown `ignoringItem`): every item is inert while it
        # lasts. Like Klutz/Embargo the item isn't removed, so Trick still
        # swaps the real one.
        if self.side is not None and self.side.field is not None and self.side.field.magic_room:
            return ""
        embargo = self.get(_EmbargoMarker)
        if embargo is not None and embargo.turns_remaining > 0:
            return ""
        return self.identity.held_item

    def base_stats_source(self) -> dict[str, int]:
        if self.current_species and self.current_species.get("base_stats"):
            return self.current_species["base_stats"]
        return self.identity.base_stats

    # -- local (owner-scoped) hook dispatch -------------------------------
    # Mirrors ecs/events.py's EventBus, but walks only THIS Battler's own
    # attached Effects (ability + held item + volatiles) instead of every
    # Effect in the battle. Used for hooks that are inherently "does MY
    # ability/item/volatile have an opinion about this", e.g. an attacker's
    # own on_modify_atk, or a defender's own on_type_immunity -- as opposed
    # to genuinely global hooks (field-wide weather triggers, "did anyone
    # on the field learn this move") which go through the real EventBus.
    def _ordered_effects(self):
        # a held item that Klutz / Embargo / Magic Room suppresses (or that is no longer the holder's) takes part in nothing -- same rule as EventBus._item_inert
        held = self.effective_item()
        return sorted((e for e in self.effects() if not (getattr(type(e), "item_names", None) and held not in type(e).item_names)), key=lambda e: -e.priority)

    async def consult(self, hook_name: str, ctx: "MoveContext", *args) -> None:
        """Fire-and-forget: every attached Effect that implements the hook
        gets called, in priority order. Return values are ignored."""
        for effect in self._ordered_effects():
            fn = getattr(effect, hook_name, None)
            if fn is not None:
                await fn(ctx, self, *args)

    async def consult_first(self, hook_name: str, ctx: "MoveContext", *args):
        """First non-None answer wins (Wonder Guard's on_type_immunity,
        No Guard's on_check_accuracy, ...)."""
        for effect in self._ordered_effects():
            fn = getattr(effect, hook_name, None)
            if fn is not None:
                result = await fn(ctx, self, *args)
                if result is not None:
                    return result
        return None

    async def consult_reduce(self, hook_name: str, ctx: "MoveContext", value, *args):
        """Chain: each handler receives the running `value` as its first
        argument after (ctx, owner) -- matching EventBus.reduce's
        convention -- and may replace it by returning non-None."""
        for effect in self._ordered_effects():
            fn = getattr(effect, hook_name, None)
            if fn is not None:
                result = await fn(ctx, self, value, *args)
                if result is not None:
                    value = result
        return value

    # -- stat calculation --------------------------------------------------
    def _raw_stat(self, stat_key: str) -> float:
        """Base+IV+EV+nature only, no ability hooks and no stages. Split out
        so both the sync display path and the async battle path share one
        formula. Old code: FighterState.stat_value()'s first half."""
        key = stat_key
        if self.get(_PowerTrickMarker) is not None:
            if key == "atk":
                key = "def"
            elif key == "def":
                key = "atk"

        override = self.get(_PowerSplitOverride)
        if override is not None and key in override.values:
            return float(override.values[key])
        # Transformed: copy the TARGET's calculated stats (Showdown copies `storedStats`, HP excepted).
        if self.stat_override is not None and key != "hp" and key in self.stat_override:
            return float(self.stat_override[key])

        full_key = {"atk": "attack", "def": "defense", "spa": "special-attack",
                    "spd": "special-defense", "spe": "speed", "hp": "hp"}.get(key, key)
        base = self.base_stats_source().get(full_key, self.base_stats_source().get(key, 50))
        iv = self.identity.ivs.get(key, 31)
        ev = self.identity.evs.get(key, 0)
        level = self.identity.level
        if key == "hp":
            return float(int((2 * base + iv + ev // 4) * level / 100) + level + 10)
        raw = int((2 * base + iv + ev // 4) * level / 100) + 5
        # Showdown `statModify`: the nature is applied with INTEGER truncation
        # (trunc(stat * 110 / 100) -- 105 -> 115, not 115.5). Keeping the
        # fraction fed a fractional Attack/Defense into every damage roll.
        mult = nature_multiplier(self.identity.nature, key)
        if mult > 1.0:
            raw = raw * 110 // 100
        elif mult < 1.0:
            raw = raw * 90 // 100
        return float(raw)

    def _staged(self, stat: int, key: str, *, ignore_stages: bool, crit_ignore_neg: bool, crit_ignore_pos: bool, double: bool = False) -> int:
        """Showdown's `calculateStat` stage step, in INTEGER arithmetic:
        `floor(stat * (2+s)/2)` for a boost, `floor(stat * 2/(2-s))` for a drop.
        The float table (`STAGE_MULT`) gave 99 * (2/3) = 65.99999999999999 -> 65
        where Showdown's floor(99 / 1.5) is 66."""
        stage = max(-6, min(6, self.stages.get(key, 0)))
        if double:                                         # Gen 4 Simple (Showdown `onModifyBoost`): the EFFECTIVE stage is doubled, not each change
            stage = max(-6, min(6, stage * 2))
        if ignore_stages:
            stage = 0
        elif crit_ignore_neg:
            stage = max(0, stage)
        elif crit_ignore_pos:
            stage = min(0, stage)
        return stat * (2 + stage) // 2 if stage >= 0 else stat * 2 // (2 - stage)

    def _stage_mult(self, key: str, *, ignore_stages: bool, crit_ignore_neg: bool, crit_ignore_pos: bool) -> float:
        stage = max(-6, min(6, self.stages.get(key, 0)))
        if key in ("acc", "eva"):
            return ACC_EVA_MULT[stage]
        if ignore_stages:
            return 1.0
        if crit_ignore_neg:
            return STAGE_MULT[max(0, stage)]
        if crit_ignore_pos:
            return STAGE_MULT[min(0, stage)]
        return STAGE_MULT[stage]

    def stat(
        self, stat_key: str, *,
        ignore_stages: bool = False,
        crit_ignore_neg: bool = False,
        crit_ignore_pos: bool = False,
    ) -> int:
        """Synchronous stat read with item multipliers and stat stages
        applied, but WITHOUT running any ability on_modify_* hook chain
        (those are async; see stat_async()). Good enough for UI/display
        purposes (rank cards, /pokemon info, the battle HP-panel renderer)
        where nothing needs the chance to react. Old code:
        FighterState.stat_value()."""
        key = stat_key
        raw = self._raw_stat(key)
        if key == "hp":
            return max(1, int(raw))
        # Showdown order: stage first (floor), THEN every Modify* event.
        raw = float(self._staged(int(raw), key, ignore_stages=ignore_stages,
                                 crit_ignore_neg=crit_ignore_neg, crit_ignore_pos=crit_ignore_pos))

        item = self.effective_item()
        from .held_items.combat_items import CHOICE_ITEM_STAT  # local import: avoids a cycle at module load
        if CHOICE_ITEM_STAT.get(item) == key:
            raw *= 1.5
        if item == "Eviolite" and self.identity.can_evolve and key in ("def", "spd"):
            raw *= 1.5

        return max(1, int(raw))

    async def stat_async(
        self, stat_key: str, ctx: "MoveContext", *,
        ignore_stages: bool = False,
        crit_ignore_neg: bool = False,
        crit_ignore_pos: bool = False,
    ) -> int:
        """The real in-battle stat read: same base formula as stat(), plus
        the on_modify_atk/def/spa/spd/spe ability hook chain (Huge Power,
        Guts, Marvel Scale, weather-speed abilities, Slow Start, Unburden,
        ...) which now live in the actual ability classes instead of being
        hardcoded here by ability-slug name. damage.py and stats.py use
        this; plain stat() above stays for non-battle display code."""
        key = stat_key
        # Wonder Room (sim/pokemon.ts `getStat`): Def and SpD are swapped
        # BEFORE anything else is computed, boosts included. Done here, not
        # at each call site, so Psyshock-family/Foul Play can't bypass it.
        if key in ("def", "spd") and getattr(getattr(ctx, "field", None), "wonder_room", False):
            key = "spd" if key == "def" else "def"
        raw = self._raw_stat(key)
        if key == "hp":
            return max(1, int(raw))
        # Showdown order: stage first (floor), THEN every Modify* event.
        raw = float(self._staged(int(raw), key, ignore_stages=ignore_stages,
                                 crit_ignore_neg=crit_ignore_neg, crit_ignore_pos=crit_ignore_pos,
                                 double=ctx.engine.ruleset.number == 4 and self.identity.ability_slug == "simple"))

        # Held-item stat multipliers (Choice Band/Specs/Scarf, Eviolite, Assault Vest) are NOT applied here: they are the items'
        # own `on_modify_*` hooks, run by the consult_reduce below like Showdown's `onModify*` handlers. (They used to be
        # hardcoded here AND hooked, so Choice items applied x1.5 twice. The sync `stat()` display path keeps its own copy
        # because it can't run async hooks.)

        # NOTE: burn is deliberately NOT applied here. Showdown halves PHYSICAL
        # DAMAGE for a burned attacker (damage.py), it does not change the
        # Attack stat -- so Foul Play/Body Press/anything else reading Attack
        # sees the true value, Guts' +50% no longer needs ordering around a
        # halving, and Gen 6+ Facade can exempt itself. (This used to halve
        # the stat, ported from the monolith.)

        # Sandstorm boosts a Rock type's Sp. Def by 50% from Gen 4 (Showdown's
        # sandstorm `onModifySpD`, priority 10 = before the other modifiers;
        # the Gen 3 mod removes it). `get_weather()` already honours Cloud Nine.
        if (key == "spd" and "Rock" in self.effective_types()
                and ctx.engine.ruleset.number >= 4 and ctx.field.get_weather() == "sand"):
            raw = float(int(raw * 1.5))
        # Gen 9 Snow: an Ice type's Defense x1.5 (Showdown's snow `onModifyDef`, same priority/position as the sandstorm boost above).
        if (key == "def" and "Ice" in self.effective_types()
                and ctx.engine.ruleset.number >= 9 and ctx.field.get_weather() == "snow"):
            raw = float(int(raw * 1.5))

        hook = {"atk": "on_modify_atk", "def": "on_modify_def", "spa": "on_modify_spa",
                "spd": "on_modify_spd", "spe": "on_modify_spe"}.get(key)
        if hook is not None:
            raw = float(await self.consult_reduce(hook, ctx, int(raw)))

        # Tailwind and paralysis are both checked directly rather than via
        # the ability hook chain above: Tailwind lives on this Battler's
        # SIDE, not on the Battler itself, so the local consult_reduce
        # dispatch above (which only walks this Battler's OWN attached
        # effects) can never reach it -- same reasoning as Mist's direct
        # side check in stats.py and weather/terrain's direct Field checks
        # in damage.py, rather than inventing a cross-entity hook dispatch
        # just for this.
        #
        # Paralysis's penalty size is generation-dependent (0.25x Gen 3-6,
        # 0.5x Gen 7+ -- see MechanicsFlags.paralysis_speed_mult's
        # docstring) and, unlike burn/Guts above, can be skipped entirely
        # rather than just having its own separate bonus stack on top --
        # Quick Feet answers on_negates_paralysis_penalty for exactly this
        # reason (Pokemon Showdown's data/conditions.ts:
        # `if (!pokemon.hasAbility('quickfeet')) { spe *= penalty }`).
        if key == "spe":
            from .side_conditions import Tailwind
            if self.side is not None and self.side.has(Tailwind):
                raw *= 2
            if self.vitals.status == "paralysis":
                negated = await self.consult_first("on_negates_paralysis_penalty", ctx)
                if not negated:
                    raw *= ctx.engine.ruleset.mechanics.paralysis_speed_mult

        return max(1, int(raw))

    # -- grounding -----------------------------------------------------
    def is_grounded(
        self, base_types: list[str] | None = None, field: "Field | None" = None,
        *, ignore_ability: bool = False, negate_immunity: bool = False,
    ) -> bool:
        """Showdown's `Pokemon.isGrounded` (sim/pokemon.ts), same order:
        Gravity, Ingrain, Smack Down and Iron Ball FORCE grounded; only then
        do Flying type, Levitate, Magnet Rise, Telekinesis and Air Balloon
        make a Pokemon airborne. That ordering is what lets Smack Down /
        Gravity / Iron Ball ground a Flying type or a Levitate user.

        Ground-move immunity is exactly `not is_grounded()` (see damage.py),
        so this is the single source of truth for it too.

        `ignore_ability`: the attacker's Mold Breaker-family suppresses
        Levitate. `negate_immunity`: Ring Target skips the Flying-type check
        (Showdown's `negateImmunity` argument)."""
        from .volatile import SmackedDown
        if field is not None and getattr(field, "gravity", False):
            return True
        if self.get(_Ingrain) is not None or self.get(SmackedDown) is not None:
            return True
        if self.effective_item() == "Iron Ball":
            return True
        if not negate_immunity and "Flying" in self.effective_types(base_types):
            return False
        if not ignore_ability and self.effective_ability() == "levitate":
            return False
        if self.get(_MagnetRise) is not None or self.get(_Telekinesis) is not None:
            return False
        return self.effective_item() != "Air Balloon"

    def is_semi_invulnerable(self) -> bool:
        """Showdown's `Pokemon.isSemiInvulnerable()`: any attached effect granting it (see
        `Volatile.grants_semi_invulnerability`)."""
        return any(getattr(e, "grants_semi_invulnerability", None) is not None and e.grants_semi_invulnerability()
                   for e in self.effects())

    # -- stat stages -----------------------------------------------------
    async def clamp_stage(
        self, ctx: "MoveContext", key: str, delta: int,
        *, from_foe: bool = False, silent_subject: bool = False,
    ) -> str:
        """Old code: FighterState.clamp_stage(). The ability-specific
        blocking/inversion logic (Hyper Cutter, Big Pecks, Keen Eye, Clear
        Body/White Smoke/Full Metal Body, Contrary, Simple) has moved into
        the real ability classes (abilities/stat_interactions.py),
        triggered here via on_stat_drop_immunity and on_modify_stat_change
        instead of being hardcoded by ability-slug name."""
        key = _STAGE_ALIASES.get(key, key)

        # Contrary/Simple run BEFORE the single-stat immunity check below --
        # see ability.py's on_modify_stat_change docstring, which is
        # explicit that this has to go first (old code: contore.py's
        # clamp_stage checked Hyper Cutter/Big Pecks/Keen Eye using the
        # pre-Contrary delta too, but never needed to get this right since
        # no Pokemon can have two abilities at once -- Contrary and Hyper
        # Cutter never coexist. Kept in the documented order regardless,
        # since a foe-caused move that Contrary turns into a *rise* clearly
        # shouldn't ever be able to trip a stat-DROP immunity in the first
        # place, and getting the general rule right costs nothing here).
        for effect in self._ordered_effects():
            fn = getattr(effect, "on_modify_stat_change", None)
            if fn is not None:
                result = await fn(ctx, self, key, delta)
                if result is not None:
                    delta = result

        if delta < 0 and from_foe:
            blocked = await self.consult_first("on_stat_drop_immunity", ctx, key)
            if blocked:
                return ""  # the ability itself already logged a message

        boosts = self.ensure(Boosts, Boosts)
        old = boosts.stages.get(key, 0)
        boosts.stages[key] = max(-6, min(6, old + delta))
        diff = boosts.stages[key] - old

        # -- [ADDITIVE / Track C] on_stat_lowered: fires once a foe-caused
        # drop has actually landed (diff < 0; skipped if it was clamped to
        # a no-op at -6, same as old code's callers implicitly got for
        # free since clamp_stage's message already says "won't go lower").
        # This is the single dispatch point for Defiant/Competitive
        # (abilities/reactive_stat_boosts.py) -- old code hardcoded that
        # reaction only at Intimidate's own call site
        # (contore.py:23366-23369 pre-refactor / entry_effects.py before
        # this fix) and would have missed every OTHER foe-caused stat drop
        # (Screech, Growl, Charm, Sticky Web, ...); putting it here instead
        # makes it apply uniformly to all of them, matching real games.
        if from_foe and diff < 0:
            await self.consult("on_stat_lowered", ctx, key, diff)
        if diff < 0:
            await self.consult("on_stat_drop_landed", ctx, key, diff)         # ANY source, incl. the holder's own move (Showdown `onAfterBoost`: Eject Pack)

        subject = "" if silent_subject else f"**{self.name}**'s "
        if diff == 0:
            return f"{subject}{_STAT_DISPLAY.get(key, key.upper())} won't go {'higher' if delta > 0 else 'lower'}!"
        mag = max(-3, min(3, diff))
        return f"{subject}{_STAT_DISPLAY.get(key, key.upper())} {_STAT_CHANGE_LABELS.get(mag, 'changed')}!"

    def clear_temp(self, bus: "EventBus | None" = None) -> None:
        """Reset everything that doesn't survive a switch-out. Old code:
        FighterState.clear_temp(). Because almost all of the monolith's
        transient fields are now Volatile Effect components rather than
        bare attributes, "clear temp state" becomes "remove every
        Component that isn't Identity/Vitals" instead of a 40-line list of
        individual field resets -- new Volatile types automatically get
        cleared on switch without this method needing to know they exist."""
        for comp_type in list(self._components.keys()):
            if comp_type in (Identity, Vitals, Moveset):
                continue
            self.remove(comp_type, bus)
        self.ability_override = None
        self.type_override = None
        self.last_move_slug = None            # Showdown `clearVolatile`: `lastMove = null` (Mirror Move / Disable / Encore can't see a move from before a switch)
        self.attacked_by = []                 # Showdown `clearVolatile`: `attackedBy = []`
        self.used_item_this_turn = False      # (`lastItem` itself survives a switch, as in Showdown)
        self.eject_pack_pending = False       # Eject Pack: a stat was lowered and the holder is to be switched out at the next trigger
        self.mega_evolved = False
        self.current_species = None
        self.transformed_into = None
        self.stat_override = None
        if self._pre_transform_slots is not None:        # undo Transform's moveset
            self.moves.slots = self._pre_transform_slots
            self._pre_transform_slots = None
        if self._orig_ability_slug is not None:          # undo Trace / Skill Swap / Role Play / Transform's ability
            self.identity.ability_slug = self._orig_ability_slug
            self._orig_ability_slug = None
        self.add(Boosts())


# -- small internal marker components --------------------------------------
# These back a few of the hooks above (is_grounded, Power Trick, Power
# Split) without requiring every move that sets them to import from a
# separate module; the real, externally-referenced Volatile definitions
# (Confusion, LeechSeed, Substitute, Taunt, Encore, ...) still live in the
# frozen volatile.py per CONTRACT.md.
@dataclass
class _Ingrain(Component):
    pass


@dataclass
class _MagnetRise(Component):
    turns_remaining: int = 5


@dataclass
class _Telekinesis(Component):
    turns_remaining: int = 3


@dataclass
class _EmbargoMarker(Component):
    turns_remaining: int = 5


@dataclass
class _PowerTrickMarker(Component):
    pass


@dataclass
class _PowerSplitOverride(Component):
    values: dict[str, int] = field(default_factory=dict)

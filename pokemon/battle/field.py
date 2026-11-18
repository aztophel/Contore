"""Field & Side: the ECS replacement for the old monolithic `BattleField`
dataclass (weather/terrain/screens/hazards as parallel two-element lists
indexed by side). Screens, hazards, weather, and terrain are now Effect
components attached to the relevant entity (a Side for screens/hazards, the
Field itself for weather/terrain) instead of dedicated fields -- adding a
brand-new side condition no longer means touching BattleField's constructor,
tick(), and every place that reads it. Old code: `class BattleField`.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from ..ecs.component import Entity

if TYPE_CHECKING:
    from .terrain import TerrainRules
    from .battler import Battler

# Weather/Terrain themselves are still defined as Field-attached Effect
# components in abilities/field_effects.py (Track D), since setting them is
# something both moves (Track C) and abilities (Track D) need to do. Track D
# left public wrappers around their private _set_weather/_set_terrain per
# NOTES_FOR_TRACK_C.md; Field.get_weather()/get_terrain() below just give UI
# code (which only wants "what's the current weather string, if any") a
# single obvious place to look instead of having to know that's a
# component-lookup one level down in a different package.


class Side(Entity):
    """One trainer's (or one wild Pokémon's) side of the field. Screens and
    entry hazards attach here as Effect components -- see side_conditions.py
    -- since they're inherently per-side, not per-Pokémon or global."""

    def __init__(self, entity_id: str, trainer_id: int | str | None = None):
        super().__init__(entity_id)
        self.trainer_id = trainer_id
        # Active battle slots; None = empty slot (fainted with nothing left
        # to send out, or a wild encounter's second slot in a would-be
        # Doubles field that's simply never filled).
        self.active: list["Battler | None"] = []
        # Everyone not currently active but still eligible to be sent out.
        self.bench: list["Battler"] = []
        # Every Battler that started the battle on this side, active,
        # benched, or fainted -- used for the UI's party-dots strip, which
        # needs to show fainted party members too instead of just what's
        # left on the bench. Old code: BattleSide.rosters[uid] (the never-
        # mutated original list, as opposed to .bench which got drained).
        self.roster: list["Battler"] = []
        # [ADDITIVE / Track C] Whether ANY Pokemon on this side fainted
        # during the PREVIOUS turn (not this one) -- Retaliate's whole
        # power-doubling condition (moves/variable_power.py). Set by
        # BattleEngine.resolve_turn at the end of each turn's processing,
        # for the NEXT turn's moves to read.
        self.fainted_last_turn: bool = False
        # Per-SLOT (not per-Pokemon, not per-side) pending effects waiting
        # for the next Pokemon to arrive in that slot: Healing Wish / Lunar
        # Dance. Showdown's `slotCondition`. slot index -> "healing-wish" |
        # "lunar-dance". Consumed by BattleEngine._do_switch_in.
        self.slot_conditions: dict[int, str] = {}
        # Bag-item state (see battle/bag.py). The ENGINE stays free of DB
        # access: the bridge fills `bags` ({trainer_id: {item: qty}}) from
        # `pokemon_items` before the battle, the engine decrements it as
        # items are used and records every use in `bag_used`
        # ({(trainer_id, item): n}) for the UI layer to persist and clear
        # after each turn.
        self.bags: dict[int, dict[str, int]] = {}
        self.bag_used: dict[tuple[int, str], int] = {}
        # Species ids the trainer has seen -- Repeat Ball/critical-capture odds.
        self.dex_seen: set[int] = set()
        # Set on the OPPOSING side of a wild encounter (a `WildEncounter`);
        # its presence is what makes that side catchable.
        self.wild_encounter = None
        # Back-reference, set by Field.__init__: lets a Battler (which only
        # knows its `side`) reach battle-wide state -- Magic Room's item
        # suppression is checked from `Battler.effective_item()`.
        self.field: "Field | None" = None

    def place(self, slot_index: int, battler: "Battler") -> None:
        """Put `battler` into an active slot, growing `active` if needed,
        and stamp the back-reference this Side/Field pair relies on."""
        while len(self.active) <= slot_index:
            self.active.append(None)
        self.active[slot_index] = battler
        battler.side = self

    def clear_slot(self, slot_index: int) -> "Battler | None":
        if slot_index >= len(self.active):
            return None
        leaving = self.active[slot_index]
        self.active[slot_index] = None
        if leaving is not None:
            leaving.side = None
        return leaving

    def alive_active(self) -> list["Battler"]:
        return [b for b in self.active if b is not None and not b.fainted]

    def alive_bench(self) -> list["Battler"]:
        return [b for b in self.bench if not b.fainted]

    def has_any_left(self) -> bool:
        return bool(self.alive_active() or self.alive_bench())


# Showdown's `gravity` move flag (data/moves.ts): while Gravity is up these
# can't be selected or used. (`floatyfall` is a Pokemon Showdown-only move.)
GRAVITY_BANNED_MOVES = frozenset({
    "bounce", "fly", "flying-press", "high-jump-kick", "jump-kick",
    "magnet-rise", "sky-drop", "splash", "telekinesis",
})


class Field(Entity):
    """Owns both Sides plus battle-wide (not per-side) state: weather,
    terrain, Trick Room, Gravity, Magic Room, Wonder Room. Old code:
    `class BattleField` -- everything that used to be a parallel
    `[side0_value, side1_value]` list on BattleField (screens, hazards) has
    moved to being a Component directly on the relevant Side instead; see
    side_conditions.py."""

    def __init__(self, side_a_id: str = "side_a", side_b_id: str = "side_b",
                 side_a_trainer: int | str | None = None, side_b_trainer: int | str | None = None):
        super().__init__("field")
        self.side_a = Side(side_a_id, side_a_trainer)
        self.side_b = Side(side_b_id, side_b_trainer)
        self.side_a.field = self.side_b.field = self
        # Echoed Voice's 2-turn field effect (Showdown pseudo-weather
        # `echoedvoice`): consecutive uses by anyone raise a 1..5 multiplier.
        self.echoed_voice_turns = 0
        self.echoed_voice_mult = 0
        # Old code: BattleField.trick_room / gravity / magic_room /
        # wonder_room (+ their *_turns counters). These are genuinely
        # global (not per-side) and have no ability/item hook needing to
        # attach behaviour to them the way weather/terrain do (nothing
        # answers "on_trick_room_set"), so plain counters are the simplest
        # honest representation -- 0 means inactive.
        self.trick_room_turns: int = 0
        self.gravity_turns: int = 0
        self.magic_room_turns: int = 0
        self.wonder_room_turns: int = 0
        self.ion_deluge_turn: int = -1
        self.fairy_lock_turns: int = 0
        self.mud_sport_turns: int = 0
        self.water_sport_turns: int = 0
        self.last_move_used: str | None = None
        self.last_move_turn: int = -1

    # -- side / battler lookups -------------------------------------------
    def side_of(self, battler: "Battler") -> Side:
        assert battler.side is not None, f"{battler} has not been placed on a side"
        return battler.side

    def other_side(self, side: Side) -> Side:
        return self.side_b if side is self.side_a else self.side_a

    def all_battlers(self) -> list["Battler"]:
        return [b for s in (self.side_a, self.side_b) for b in s.active if b is not None]

    def battler_by_id(self, entity_id: str) -> "Battler | None":
        return next((b for b in self.all_battlers() if b.id == entity_id), None)

    def opponents_of(self, battler: "Battler") -> list["Battler"]:
        return [b for b in self.other_side(self.side_of(battler)).active
                if b is not None and not b.fainted]

    def allies_of(self, battler: "Battler") -> list["Battler"]:
        return [b for b in self.side_of(battler).active
                if b is not None and b is not battler and not b.fainted]

    # -- global state flags used all over move.py/battler.py -------------
    @property
    def trick_room(self) -> bool:
        return self.trick_room_turns > 0

    @property
    def gravity(self) -> bool:
        return self.gravity_turns > 0

    @property
    def magic_room(self) -> bool:
        return self.magic_room_turns > 0

    @property
    def wonder_room(self) -> bool:
        return self.wonder_room_turns > 0

    # -- weather / terrain convenience ------------------------------------
    # Deferred imports: abilities/field_effects.py (Track D) imports this
    # module for the Field type hint, so importing it back at module load
    # would be circular. Safe here since these are only ever called well
    # after both modules have finished loading.
    def get_weather(self) -> str | None:
        from .abilities.field_effects import get_weather
        return get_weather(self)

    def get_terrain(self) -> str | None:
        from .abilities.field_effects import get_terrain
        return get_terrain(self)

    @property
    def weather(self) -> str | None:
        return self.get_weather()

    @property
    def terrain(self) -> str | None:
        return self.get_terrain()

    def damage_mult(self, move_type: str) -> float:
        """Old code: BattleField.damage_mult() -- Sun/Rain's Fire/Water
        multiplier. Harsh Sun/Heavy Rain (Primal weather) additionally
        zero out the opposing element entirely rather than just halving
        it."""
        w = self.weather
        if w in ("sun", "harsh-sun"):
            if move_type == "Fire":
                return 1.5
            if move_type == "Water":
                return 0.0 if w == "harsh-sun" else 0.5
        if w in ("rain", "heavy-rain"):
            if move_type == "Water":
                return 1.5
            if move_type == "Fire":
                return 0.0 if w == "heavy-rain" else 0.5
        return 1.0

    @property
    def terrain_rules(self) -> "TerrainRules | None":
        """The active terrain's behaviour object (battle/terrain.py), or None. Replaces the boost-only `terrain_mult`:
        a terrain also WEAKENS moves, heals, blocks status/volatiles and priority moves."""
        from .terrain import rules_for
        return rules_for(self.terrain)

    def sport_mult(self, move_type: str) -> float:
        """Old code: BattleField.sport_mult() (Mud Sport / Water Sport)."""
        if move_type == "Electric" and self.mud_sport_turns > 0:
            return 0.33
        if move_type == "Fire" and self.water_sport_turns > 0:
            return 0.33
        return 1.0

    def weather_chip(self, battler: "Battler", types: list[str]) -> tuple[int, str | None]:
        """Old code: BattleField.weather_chip() -- Sandstorm/Hail end-of-turn
        damage, respecting type immunity and the various weather-immune
        abilities. Returns (damage, message_template_or_None)."""
        w = self.weather
        if w not in ("sand", "hail"):
            return 0, None
        immune_types = {"sand": {"Rock", "Ground", "Steel"}, "hail": {"Ice"}}.get(w, set())
        if set(types) & immune_types:
            return 0, None
        if battler.effective_item() == "Safety Goggles":
            return 0, None
        ab = battler.effective_ability()
        if ab in ("overcoat", "magic-guard") or \
           (w == "sand" and ab in ("sand-veil", "sand-force", "sand-rush")) or \
           (w == "hail" and ab in ("ice-body", "snow-cloak")):
            return 0, None
        dmg = max(1, battler.vitals.max_hp // 16)
        return dmg, ("{name} is buffeted by the sandstorm!" if w == "sand"
                     else "{name} is buffeted by hail!")

    def tick_weather_terrain(self) -> list[str]:
        """Weather/Terrain (abilities/field_effects.py) are plain
        Components, not Effects -- Track D deliberately kept them hook-free
        since nothing needs to *react* to "a turn of weather passed", only
        to weather's current existence (get_weather()/damage_mult()/etc).
        That means their duration has to be ticked down by hand here,
        unlike side conditions (side_conditions.py) which self-tick through
        the normal on_end_turn Effect dispatch. Old code: BattleField.tick()'s
        weather/terrain block."""
        from .abilities.field_effects import Weather, Terrain
        msgs: list[str] = []
        w = self.get(Weather)
        if w is not None and w.turns_remaining is not None:
            w.turns_remaining -= 1
            if w.turns_remaining <= 0:
                msgs.append(f"The {w.kind.replace('_', ' ')} subsided.")
                self.remove(Weather)
        t = self.get(Terrain)
        if t is not None:
            t.turns_remaining -= 1
            if t.turns_remaining <= 0:
                msgs.append(f"The {t.kind} terrain faded.")
                self.remove(Terrain)
        return msgs

    def tick_global(self) -> list[str]:
        """Advances the battle-wide (non-per-side, non-weather/terrain --
        those already tick via their own Component's on_end_of_turn, fired
        through the normal Effect dispatch) counters. Old code:
        BattleField.tick()'s Trick Room/Gravity/Magic Room/Wonder Room/Fairy
        Lock/Mud Sport/Water Sport block."""
        msgs: list[str] = []
        if self.echoed_voice_turns > 0:
            self.echoed_voice_turns -= 1
            if self.echoed_voice_turns == 0:
                self.echoed_voice_mult = 0
        if self.trick_room_turns > 0:
            self.trick_room_turns -= 1
            if self.trick_room_turns == 0:
                msgs.append("The twisted dimensions returned to normal!")
        if self.gravity_turns > 0:
            self.gravity_turns -= 1
            if self.gravity_turns == 0:
                msgs.append("The gravity returned to normal!")
        if self.magic_room_turns > 0:
            self.magic_room_turns -= 1
            if self.magic_room_turns == 0:
                msgs.append("Magic Room wore off!")
        if self.wonder_room_turns > 0:
            self.wonder_room_turns -= 1
            if self.wonder_room_turns == 0:
                msgs.append("Wonder Room wore off!")
        if self.fairy_lock_turns > 0:
            self.fairy_lock_turns -= 1
            if self.fairy_lock_turns == 0:
                msgs.append("The grip of Fairy Lock wore off!")
        if self.mud_sport_turns > 0:
            self.mud_sport_turns -= 1
            if self.mud_sport_turns == 0:
                msgs.append("The effects of Mud Sport faded.")
        if self.water_sport_turns > 0:
            self.water_sport_turns -= 1
            if self.water_sport_turns == 0:
                msgs.append("The effects of Water Sport faded.")
        return msgs

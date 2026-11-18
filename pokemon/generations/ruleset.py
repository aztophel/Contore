"""Per-generation battle & Pokédex rulesets.

The monolith hard-coded Gen VI rules everywhere (type chart, move
availability, Mega Evolution, the works) with exactly one inconsistency --
breeding ball inheritance used Gen VII+ rules because that's what the
original author preferred at the time. This module makes "which
generation's rules apply" an explicit, first-class setting instead of an
implicit assumption baked into scattered code.

Two independent knobs exist per server, because a server might want e.g.
Gen V battle mechanics but still let a member-owned Gen VI Fairy-type fight
if it was traded in rather than caught locally:

    generation           which GenerationRuleset governs BATTLES (type
                          chart, move/ability availability, whether Mega
                          Evolution or certain items exist, etc.)
    dex_cap_generation    which generation's Pokédex governs what can
                          SPAWN/BE CAUGHT here (defaults to `generation`)
    restrict_mode         SPAWN_ONLY (default): a Pokémon above the dex cap
                          simply won't spawn/be caught wild in this server,
                          but one brought in via trade/gift can still battle
                          here.
                          FULL: such a Pokémon additionally cannot be placed
                          on a battle team in this server at all.

Cross-server systems (breeding, GTS, trading, a *user's* overall Pokédex)
are intentionally NOT gated by any server's ruleset -- a Pokémon a player
legitimately owns is theirs everywhere. Only "can this spawn in server X"
and "can this fight in server X" are server-scoped.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class RestrictMode(Enum):
    SPAWN_ONLY = "spawn_only"   # default
    FULL = "full"


@dataclass(frozen=True)
class MechanicsFlags:
    physical_special_split: bool = True   # False pre-Gen4 (category is per-type, not per-move)
    has_abilities: bool = True            # False pre-Gen3
    has_held_items: bool = True           # False in Gen1
    has_fairy_type: bool = False          # True from Gen6 onward
    has_mega_evolution: bool = False      # True Gen6 only, in this bot's current scope

    # -- [ADDITIVE / Track C] concrete numeric mechanics, added once actual
    # cross-generation battle behaviour (not just dex/type-chart gating)
    # became in scope. Verified against Pokemon Showdown's sim source
    # (data/conditions.ts + data/mods/genN/conditions.ts,
    # sim/battle-actions.ts) rather than assumed -- the pre-existing
    # engine code this bot inherited had silently baked in a mix of
    # different generations' values under a single "Gen 6" label (the
    # crit-stage table and confusion's self-hit chance were both actually
    # Gen 7+ values; paralysis's Speed penalty was neither gen's real
    # value). See CONTRACT_CHANGES.md for the specifics of what changed.
    paralysis_speed_mult: float = 0.25
    """Gen 3-6: 0.25 (quarters Speed). Gen 7+: 0.5 (halves it). Skipped
    entirely (not just a different number) by Quick Feet -- see
    Ability.on_negates_paralysis_penalty. Gen 1-2 use a fundamentally
    different raw-stat-halving mechanic pre-dating the modifier-chain
    system entirely; 0.25 is the right practical number for either but
    isn't derived the same way, which matters if Gen 1/2 mechanics are
    ever modeled more precisely than this bot currently attempts."""

    confusion_self_hit_chance: float = 0.50
    """Gen 1-6: 50% (`randomChance(1, 2)`). Gen 7+: 33% (`randomChance(33,
    100)`) -- Pokemon Showdown's data/mods/gen6/conditions.ts vs.
    data/conditions.ts."""

    crit_stage_denominators: tuple[int, ...] = (16, 8, 2, 1)
    """Index i = the 1-in-N chance at crit stage i (stage 0 is the
    baseline every move has by default, before Super Luck/Focus
    Energy/Scope Lens/high-crit-ratio moves add to it). This is Gen 6's
    table. Gen 3-5's is longer (one extra stage) and has different values
    entirely -- see GEN_3/4/5 below. Gen 1-2's critical hit mechanic isn't
    stage-based on this axis at all (it's derived from the user's base
    Speed stat), so this field doesn't meaningfully apply there; GEN_1/2
    keep Gen 6's table as a placeholder rather than a claim of accuracy,
    since neither generation's moves/abilities are otherwise wired up in
    this engine yet either."""

    full_paralysis_chance: float = 0.25
    """1/4, stable from Gen 3 onward (Gen 1-2 technically use 63/256 ~=
    24.6%, not exactly 1/4 -- kept as 0.25 here too as a practical
    simplification until Gen 1/2 are modeled more precisely elsewhere)."""

    freeze_thaw_chance: float = 0.20
    """1/5 per turn before moving, stable from Gen 4 onward (confirmed:
    Gen 3 has NO natural thaw roll on its own -- only a Fire-type hit
    cures it -- and Gen 2's is a 25/256 END-OF-TURN roll rather than a
    pre-move one, and Gen 1 has no natural thaw at all. This bot's move
    execution order currently only has one hook point for this -- see
    engine.py's _can_act -- so Gen 1-3's differences aren't represented
    yet, only flagged here for whoever tackles it.)"""

    sleep_turns_range: tuple[int, int] = (1, 3)
    """How many CONSECUTIVE turns sleep prevents the Pokemon from moving
    (inclusive both ends) -- Gen 5 onward. Gen 3-4 is (1, 4). Verified
    against Bulbapedia's sleep article, which states these ranges in
    exactly these terms (not the internal "sleep counter" value some
    implementations use, which is one higher than this -- see status.py's
    inflict_status, which converts this range into that internal
    representation itself; nothing outside this module needs to know the
    difference). Gen 1-2 use much more irregular formulas (1-7 turns in
    Gen 1, can't act on the wake turn; 1-7 in Gen 2 but CAN act when
    waking) not modeled by a single range at all -- not implemented,
    since neither generation's abilities/items exist in this engine
    either."""

    burn_damage_fraction: float = 1 / 8
    """Max HP fraction lost to burn each turn. 1/16 in Gen 1 AND Gen 7
    onward, but 1/8 specifically for Gen 2 through Gen 6 (verified against
    Pokemon Showdown's data/mods/gen6/conditions.ts, which explicitly
    overrides the current/Gen 7+ value of 1/16 back up to 1/8) -- found
    via live cross-referencing against detailed per-generation status-
    condition documentation the user supplied; this bot's old code and
    this engine's own first draft both used 1/16 unconditionally, which
    happens to match Gen 1 and Gen 7+ but is exactly double the intended
    damage for this bot's actual Gen 6 baseline."""


@dataclass(frozen=True)
class GenerationRuleset:
    number: int
    display_name: str
    max_dex_number: int
    mechanics: MechanicsFlags = field(default_factory=MechanicsFlags)
    excluded_moves: frozenset[str] = frozenset()
    excluded_abilities: frozenset[str] = frozenset()
    # Lets a generation patch specific type matchups without needing an
    # entirely separate chart -- e.g. Gen I had no Steel/Dark/Fairy types
    # and Poison was super-effective against Bug.
    type_chart_overrides: dict[tuple[str, str], float] = field(default_factory=dict)

    def species_allowed(self, dex_id: int) -> bool:
        return 1 <= dex_id <= self.max_dex_number

    def move_allowed(self, move_slug: str) -> bool:
        return move_slug not in self.excluded_moves

    def ability_allowed(self, ability_slug: str) -> bool:
        return ability_slug not in self.excluded_abilities


# --- Built-in rulesets -------------------------------------------------
# Only Gen VI is fully accurate today -- it's what the existing engine
# already implements end-to-end. Earlier generations are real, selectable
# options (server owners can already cap their Pokédex there right now)
# but their `mechanics` flags are the main lever until someone teaches the
# engine e.g. "no abilities exist" or "Special isn't split into two stats"
# for real -- that's tracked follow-up work per generation, not a blocker
# for shipping generation *selection* today.

# Per-generation type-chart differences from the (Gen 6+) base chart in
# pokemon/battle/types.py, taken from Pokemon Showdown's data/mods/gen{5,2,1}/
# typechart.ts (Gens 3-4 inherit Gen 5's, Gen 2 builds on those, Gen 1 on Gen 2).
# Verified cell-by-cell against Showdown for every generation.
_GEN2_TO_5_CHART = {  # Steel still resisted Ghost and Dark before Gen 6
    ("Ghost", "Steel"): 0.5, ("Dark", "Steel"): 0.5,
}
_GEN1_CHART = {       # Gen 1's famous quirks, on top of the above
    **_GEN2_TO_5_CHART,
    ("Bug", "Poison"): 2.0, ("Poison", "Bug"): 2.0,
    ("Ghost", "Psychic"): 0.0,   # the bug: Ghost moves did NOTHING to Psychic
    ("Ice", "Fire"): 1.0,
}


GEN_1 = GenerationRuleset(
    number=1, display_name="Generation I (Kanto)", max_dex_number=151,
    mechanics=MechanicsFlags(physical_special_split=False, has_abilities=False,
                              has_held_items=False, has_fairy_type=False,
                              full_paralysis_chance=63/256, freeze_thaw_chance=0.0,
                              burn_damage_fraction=1/16),
    type_chart_overrides=_GEN1_CHART,
)
GEN_2 = GenerationRuleset(
    number=2, display_name="Generation II (Johto)", max_dex_number=251,
    mechanics=MechanicsFlags(physical_special_split=False, has_abilities=False, has_fairy_type=False,
                              full_paralysis_chance=63/256, freeze_thaw_chance=25/256),
    type_chart_overrides=_GEN2_TO_5_CHART,
)
GEN_3 = GenerationRuleset(
    number=3, display_name="Generation III (Hoenn)", max_dex_number=386,
    mechanics=MechanicsFlags(physical_special_split=False, has_fairy_type=False,
                              crit_stage_denominators=(16, 8, 4, 3, 2), freeze_thaw_chance=0.0,
                              sleep_turns_range=(1, 4)),
    type_chart_overrides=_GEN2_TO_5_CHART,
)
GEN_4 = GenerationRuleset(
    number=4, display_name="Generation IV (Sinnoh)", max_dex_number=493,
    mechanics=MechanicsFlags(has_fairy_type=False, crit_stage_denominators=(16, 8, 4, 3, 2),
                              sleep_turns_range=(1, 4)),
    type_chart_overrides=_GEN2_TO_5_CHART,
)
GEN_5 = GenerationRuleset(
    number=5, display_name="Generation V (Unova)", max_dex_number=649,
    mechanics=MechanicsFlags(has_fairy_type=False, crit_stage_denominators=(16, 8, 4, 3, 2)),
    type_chart_overrides=_GEN2_TO_5_CHART,
)
GEN_6 = GenerationRuleset(
    number=6, display_name="Generation VI (Kalos)", max_dex_number=721,
    mechanics=MechanicsFlags(has_fairy_type=True, has_mega_evolution=True),
    # crit_stage_denominators=(16,8,2,1), paralysis_speed_mult=0.25,
    # confusion_self_hit_chance=0.50 -- all just the MechanicsFlags
    # defaults above, which now reflect Gen 6's actual values specifically
    # (this is the generation the rest of the engine is built against).
)
GEN_7 = GenerationRuleset(
    number=7, display_name="Generation VII (Alola)", max_dex_number=809,
    mechanics=MechanicsFlags(has_fairy_type=True, has_mega_evolution=True,
                              paralysis_speed_mult=0.5, confusion_self_hit_chance=1/3,
                              crit_stage_denominators=(24, 8, 2, 1), burn_damage_fraction=1/16),
)
GEN_8 = GenerationRuleset(
    number=8, display_name="Generation VIII (Galar)", max_dex_number=898,
    mechanics=MechanicsFlags(has_fairy_type=True, has_mega_evolution=False,
                              paralysis_speed_mult=0.5, confusion_self_hit_chance=1/3,
                              crit_stage_denominators=(24, 8, 2, 1), burn_damage_fraction=1/16),
)
GEN_9 = GenerationRuleset(
    number=9, display_name="Generation IX (Paldea)", max_dex_number=1025,
    mechanics=MechanicsFlags(has_fairy_type=True, has_mega_evolution=False,
                              paralysis_speed_mult=0.5, confusion_self_hit_chance=1/3,
                              crit_stage_denominators=(24, 8, 2, 1), burn_damage_fraction=1/16),
)

ALL_GENERATIONS: dict[int, GenerationRuleset] = {
    g.number: g for g in (GEN_1, GEN_2, GEN_3, GEN_4, GEN_5, GEN_6, GEN_7, GEN_8, GEN_9)
}
DEFAULT_GENERATION = GEN_6  # preserves current behaviour for existing servers


@dataclass
class ServerPokemonRules:
    """Per-guild settings row (see ARCHITECTURE.md for the accompanying DB
    migration). Stores plain generation numbers rather than
    GenerationRuleset objects so new generations can be added later without
    a schema change -- resolve via ALL_GENERATIONS[n] at point of use."""
    guild_id: int
    generation: int = DEFAULT_GENERATION.number
    dex_cap_generation: int | None = None
    restrict_mode: RestrictMode = RestrictMode.SPAWN_ONLY

    @property
    def battle_ruleset(self) -> GenerationRuleset:
        return ALL_GENERATIONS.get(self.generation, DEFAULT_GENERATION)

    @property
    def dex_ruleset(self) -> GenerationRuleset:
        gen = self.dex_cap_generation or self.generation
        return ALL_GENERATIONS.get(gen, DEFAULT_GENERATION)

    def can_spawn(self, dex_id: int) -> bool:
        return self.dex_ruleset.species_allowed(dex_id)

    def can_battle_with(self, dex_id: int) -> bool:
        if self.restrict_mode is RestrictMode.SPAWN_ONLY:
            return True  # already-owned Pokémon are always battle-legal
        return self.dex_ruleset.species_allowed(dex_id)

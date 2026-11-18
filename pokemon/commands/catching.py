"""Pure Poké Ball catch-odds math: the Gen III+ formula plus Contore's own
additions (critical captures, a Heavy Ball weight bonus, and a level-gap
penalty discouraging catching things wildly above your team's level). No
Discord, no database, no battle-state coupling.

Catching, in this bot, only ever happens *inside* a wild battle -- there's
no standalone `/pokemon catch` command in the monolith. Poké Balls are
thrown as a battle action against the opposing wild Pokémon's live HP and
status, so consuming the ball, rolling the shakes, and persisting a
successful catch inherently lives with the rest of turn resolution, in the
battle engine (Track C/D's `pokemon.battle.*`). What belongs here is the
odds math itself -- pure Pokémon-domain arithmetic with no ECS/hook
involvement -- which the battle engine calls into, the same way it (or
whatever ends up owning the spawn trigger) calls into `spawning.py`'s roll
functions. A successful catch's actual persistence is
`pokemon.commands.repository.add_pokemon`.
"""

from __future__ import annotations

import random

from pokemon.dex.species import Species

STATUS_CATCH_BONUS: dict[str, float] = {
    "sleep": 2.5, "freeze": 2.5,
    "paralysis": 1.5, "poison": 1.5, "bad_poison": 1.5, "burn": 1.5,
}

# Species that evolve via Moon Stone -- the one evolution-item condition
# `_calc_ball_mult`'s "moon_evo" Poké Ball (Moon Ball) cares about.
MOON_STONE_SPECIES_IDS: frozenset[int] = frozenset({30, 33, 35, 37, 44, 102, 120})


def catch_value(
    *, catch_rate: int, ball_mult: float, current_hp: int, max_hp: int,
    status: str | None, heavy_bonus: float = 0.0,
) -> float:
    """The classic Gen III+ "a" value fed into `catch_check`/
    `catch_check_critical`: lower current HP, an inflicted status, and a
    stronger ball all raise it. Ported verbatim from the arithmetic inlined
    at the `_calc_ball_mult` call site (it was never its own function in
    the monolith, just repeated at both catch call sites)."""
    effective_catch_rate = max(1.0, catch_rate + heavy_bonus)
    hp_mod = (3 * max_hp - 2 * current_hp) / (3 * max_hp) if max_hp else 1.0
    status_mult = STATUS_CATCH_BONUS.get(status, 1.0)
    return effective_catch_rate * ball_mult * hp_mod * status_mult


def level_gap_penalty(my_level: int, wild_level: int) -> float:
    """Ported verbatim from `_level_gap_catch_penalty`: no penalty until
    the wild Pokémon's level clears `my_level * 1.10 + 5`, then the odds
    fall off quadratically past that threshold."""
    threshold = my_level * 1.10 + 5
    if wild_level <= threshold:
        return 1.0
    overage = wild_level - threshold
    return 1.0 / (1.0 + (overage ** 2) / 90.0)


def critical_capture_multiplier(dex_registered: int) -> float:
    """Ported verbatim from `_critical_capture_multiplier` -- the more of
    the Pokédex a trainer has registered, the better their crit-capture odds."""
    if dex_registered < 30:
        return 0
    if dex_registered < 150:
        return 0.5
    if dex_registered < 300:
        return 1
    if dex_registered < 450:
        return 1.5
    if dex_registered < 600:
        return 2
    return 2.5


def critical_capture_odds(a: float, dex_registered: int) -> int:
    """Ported verbatim from `_critical_capture_odds`."""
    mult = critical_capture_multiplier(dex_registered)
    return 0 if mult == 0 else min(255, int(a * mult / 6))


def _shake_threshold(a: float) -> int:
    a = max(0.0001, a)
    b = int(1048560 / ((16711680 / a) ** 0.25))
    return max(0, min(65535, b))


def exact_shake_probability(a: float) -> float:
    """The exact (closed-form, not simulated) probability a single shake
    succeeds at catch value `a`. Ported verbatim from
    `_exact_critical_shake_prob`."""
    if a >= 255:
        return 1.0
    a = max(0.0001, a)
    b = 1048560.0 / ((16711680.0 / a) ** 0.25)
    return min(1.0, max(0.0, b / 65536.0))


def exact_catch_probability(a: float) -> float:
    """The exact probability a normal 4-shake check succeeds at catch
    value `a` (`exact_shake_probability(a) ** 4`). Ported verbatim from
    `_exact_ball_shake_prob`."""
    return exact_shake_probability(a) ** 4


def catch_check(a: float) -> tuple[bool, int]:
    """Rolls the standard 4-shake catch check. Returns `(caught, shakes)`.
    Ported verbatim from `_catch_check`."""
    if a >= 255:
        return True, 4
    b = _shake_threshold(a)
    shakes = 0
    for _ in range(4):
        if random.randint(0, 65535) < b:
            shakes += 1
        else:
            break
    return shakes >= 4, shakes


def catch_check_critical(a: float) -> tuple[bool, int]:
    """A critical capture: a single shake check instead of four. Returns
    `(caught, shakes)` (`shakes` always 1, kept for a uniform return shape
    with `catch_check`). Ported verbatim from `_catch_check_critical`."""
    if a >= 255:
        return True, 1
    b = _shake_threshold(a)
    return (random.randint(0, 65535) < b), 1


def heavy_ball_bonus(species: Species) -> float:
    """Ported from `_heavy_ball_bonus`, adjusted for a real correctness
    fix: the monolith did `species.get("weight", 0) / 10`, dividing a
    PokeAPI-hectogram weight down to kg -- but `Species.weight_kg` (see
    `pokemon.dex.species`) is already stored in kg, so that division would
    silently 10x-undershoot every threshold here if applied again. Using
    `weight_kg` directly restores the intended thresholds (e.g. Snorlax,
    ~460kg, correctly gets the full +30 bonus instead of falling into the
    ~46kg/no-bonus band)."""
    weight_kg = species.weight_kg
    if weight_kg >= 300:
        return 30.0
    if weight_kg >= 200:
        return 20.0
    if weight_kg >= 100:
        return 10.0
    if weight_kg < 10:
        return -20.0
    return 0.0


def exact_catch_result(
    species: Species,
    ball: str,
    ball_data: dict,
    *,
    hp_pct: float = 1.0,
    status: str | None = None,
    wild_gender: str = "genderless",
    wild_level: int | None = None,
    turn: int = 1,
    is_night: bool = False,
    dex_seen: set[int] | None = None,
    my_species_id: int | None = None,
    my_level: int | None = None,
    my_gender: str | None = None,
    my_ref_level: int | None = None,
) -> dict:
    """The full breakdown `/pokemon cr` displays: catch value, whether
    it's a guaranteed catch, and the exact overall/critical/normal-shake
    probabilities. Ported verbatim from `calc_exact_catch_probability`,
    adapted to `ball_multiplier`'s cleaned-up keyword signature (see that
    function's docstring) -- `my_ref_level`, when given alongside
    `wild_level`, applies `level_gap_penalty` exactly as the original did
    when a caller wants that check (hunt/team-level-aware tools) without
    every caller having to apply it themselves.
    """
    dex_seen = dex_seen or set()
    mult = ball_multiplier(
        ball, ball_data, species, wild_level=wild_level or 1, wild_gender=wild_gender, wild_status=status,
        turn=turn, is_night=is_night, dex_seen=dex_seen, my_species_id=my_species_id, my_level=my_level,
        my_gender=my_gender)
    heavy_bonus = heavy_ball_bonus(species) if ball_data.get("cond") == "heavy" else 0.0

    if mult >= 255:
        return {"a": 255.0, "guaranteed": True, "overall_prob": 1.0, "crit_odds_256": 0,
                "crit_prob": 0.0, "normal_prob": 1.0}

    hp_mod = (3 - 2 * max(0.0, min(1.0, hp_pct))) / 3.0
    status_mult = STATUS_CATCH_BONUS.get(status, 1.0)
    effective_catch_rate = max(1.0, species.catch_rate + heavy_bonus)
    a = effective_catch_rate * mult * hp_mod * status_mult
    if my_ref_level is not None and wild_level is not None:
        a *= level_gap_penalty(my_ref_level, wild_level)
    a = max(0.0, a)

    crit_odds = critical_capture_odds(a, len(dex_seen))
    p_crit_happens = crit_odds / 256.0
    p_crit_catch = exact_shake_probability(a)
    p_normal_catch = exact_catch_probability(a)
    overall = p_crit_happens * p_crit_catch + (1 - p_crit_happens) * p_normal_catch

    return {
        "a": a, "guaranteed": a >= 255, "overall_prob": min(1.0, overall),
        "crit_odds_256": crit_odds, "crit_prob": p_crit_catch, "normal_prob": p_normal_catch,
    }


def ball_multiplier(
    ball: str,
    ball_data: dict,
    species: Species,
    *,
    wild_level: int,
    wild_gender: str,
    wild_status: str | None,
    turn: int,
    is_night: bool,
    dex_seen: set[int],
    my_species_id: int | None = None,
    my_level: int | None = None,
    my_gender: str | None = None,
) -> float:
    """The multiplier a specific Poké Ball contributes to the catch value,
    given the conditional bonus `pokemon.dex.items_repo.POKEBALL_DATA[ball]`
    describes (e.g. Net Ball's Water/Bug bonus, Timer Ball scaling with
    turn count, Dusk Ball at night, ...).

    Ported from `_calc_ball_mult`, restructured per the "fix the horrific
    method arguments" ask: the original took whole `spawn`/`my_pokemon`
    row dicts (mixing in a dozen unrelated fields) plus a positional
    `caught_ids` set; this takes only the specific scalars each condition
    actually reads, all keyword-only past `ball`/`ball_data`/`species` so
    call sites are self-documenting. `my_species_id`/`my_level`/
    `my_gender` are `None` when there's no partner Pokémon in play (the
    "same_sp"/"level" conditions then simply don't apply, matching the
    monolith's `if cond == ... and my_pokemon:` guards).

    `ball_data` is passed in (rather than looked up here from `ball`) so a
    caller that already has `POKEBALL_DATA[ball]` on hand (as the battle
    engine will, to also read e.g. `base_mult` for display) doesn't do a
    second dict lookup; pass `pokemon.dex.items_repo.POKEBALL_DATA.get(ball, {})`.
    """
    mult = ball_data.get("base_mult", 1.0)
    cond = ball_data.get("cond")
    if cond is None:
        return mult
    if cond == "fast":
        return mult if species.base_stats.get("speed", 0) >= 100 else 1.0
    if cond == "heavy":
        return 1.0  # heavy ball's bonus is additive (see `heavy_ball_bonus`), not a mult
    if cond == "level" and my_level is not None:
        ratio = my_level / max(1, wild_level)
        return 8.0 if ratio >= 4 else 4.0 if ratio >= 2 else 2.0 if ratio > 1 else 1.0
    if cond == "same_sp" and my_species_id is not None:
        same_species = my_species_id == species.id
        opposite_gender = (
            my_gender in ("male", "female") and wild_gender in ("male", "female") and my_gender != wild_gender)
        return mult if same_species and opposite_gender else 1.0
    if cond == "water":
        return mult if "Water" in species.types else 1.0
    if cond == "moon_evo":
        return mult if species.id in MOON_STONE_SPECIES_IDS else 1.0
    if cond == "water_bug":
        return mult if any(t in species.types for t in ("Water", "Bug")) else 1.0
    if cond == "weak":
        return max(1.0, min(4.0, (41 - wild_level) / 10))
    if cond == "caught":
        return mult if species.id in dex_seen else 1.0
    if cond == "turns":
        return min(4.0, 1.0 + (turn - 1) * (1229 / 4096))
    if cond == "night":
        return mult if is_night else 1.0
    if cond == "first_turn":
        return mult if turn == 1 else 1.0
    if cond == "asleep":
        return mult if wild_status == "sleep" else 1.0
    return mult

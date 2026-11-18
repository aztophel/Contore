"""Pokémon XP <-> level formulas (the standard growth-rate curves).

Pure and stateless on purpose -- these are needed both by
`repository.db_add_pokemon` (to compute a newly-caught/bred Pokémon's
starting XP for its rolled level) and, later, by whatever cog handles
post-battle/post-catch XP awards and level-up notifications. Splitting them
out here (rather than leaving them as free functions the way the monolith
did) is what lets both call sites share one implementation.
"""

from __future__ import annotations


def total_xp_for_level(level: int, growth_rate: str = "medium") -> int:
    """Total XP needed to reach `level` under the given growth rate.
    Ported verbatim from the monolith's `pokemon_total_xp_for_level`."""
    n = max(1, min(level, 100))
    gr = growth_rate.lower()
    if gr in ("medium", "medium-fast", "mediumfast"):
        return n ** 3
    if gr == "erratic":
        if n <= 50:
            return int(n**3 * (100 - n) / 50)
        if n <= 68:
            return int(n**3 * (150 - n) / 100)
        if n <= 98:
            return int(n**3 * ((1911 - 10 * n) // 3) / 500)
        return int(n**3 * (160 - n) / 100)
    if gr == "fast":
        return int(4 * n**3 / 5)
    if gr in ("medium-slow", "mediumslow"):
        return max(0, int(6 * n**3 / 5 - 15 * n**2 + 100 * n - 140))
    if gr == "slow":
        return int(5 * n**3 / 4)
    if gr == "fluctuating":
        if n <= 15:
            return int(n**3 * ((n + 1) // 3 + 24) / 50)
        if n <= 35:
            return int(n**3 * (n + 14) / 50)
        return int(n**3 * (n // 2 + 32) / 50)
    return n ** 3


def level_from_xp(xp: float, growth_rate: str = "medium") -> int:
    """Inverse of `total_xp_for_level` via binary search (the curves aren't
    all monotonically invertible in closed form). Ported verbatim from the
    monolith's `pokemon_level_from_xp`."""
    lo, hi = 1, 100
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if total_xp_for_level(mid, growth_rate) <= xp:
            lo = mid
        else:
            hi = mid - 1
    return lo

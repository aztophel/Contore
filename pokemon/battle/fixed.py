"""Showdown's fixed-point damage arithmetic, shared by damage.py and the ability/item hooks.

`modify` is `battle.modify` from sim/battle.ts: the multiplier is truncated to 1/4096ths, the product is truncated, and
the result is rounded half DOWN. It is NOT `int(value * mult)`: for value=33, mult=0.75 Showdown gets 25 where the
float version gives 24, so a hook that does `int(damage * 0.75)` is off by one on many inputs."""
from __future__ import annotations


def modify(value: int, multiplier: float) -> int:
    modifier = int(multiplier * 4096)
    return (int(value * modifier) + 2047) // 4096


def modify_raw(value: int, numerator: int, denominator: int = 4096) -> int:
    """Same, for the exact 4096ths Showdown writes out (e.g. `chainModify([5325, 4096])`, Life Orb's 5324)."""
    return (int(value * numerator * 4096 // denominator) + 2047) // 4096


def chain_mod(current: int, numerator: int, denominator: int = 4096) -> int:
    """Showdown `chainModify`: fold one more multiplier into an event's running modifier (4096 = x1). The chain is rounded as it grows
    (`(prev * next + 2048) >> 12`) and applied to the value ONCE at the end with `modify_raw(value, chain)` -- which is NOT the same as
    rounding after every multiplier, and is why stacked modifiers can differ by 1 from a step-by-step implementation."""
    nxt = numerator * 4096 // denominator
    return (current * nxt + 2048) >> 12

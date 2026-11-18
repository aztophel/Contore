"""Abilities that weaken (or strengthen) what an ATTACKER does to their holder via Showdown's `onSourceModify*` events:
Thick Fat and Heatproof halve the attacker's Atk/SpA for the move types they resist, Dry Skin takes x1.25 from Fire.
They can't sit on the attacker's own hooks, so damage.py consults the TARGET's `on_source_modify_atk` /
`on_source_modify_spa` / `on_source_base_power` after the attacker's own. All are breakable (Mold Breaker).
(Thick Fat and Heatproof had no implementation at all before this file.)
"""
from __future__ import annotations

from ..registry import register_ability
from ..ability import Ability
from ..fixed import modify, modify_raw
from ._util import attacker_breaks_mold


def _halve_vs(slug: str, display: str, types: frozenset[str]):
    @register_ability(slug)
    class _Resist(Ability):
        async def _weaken(self, ctx, owner, stat: int) -> int | None:
            if owner is not ctx.current_target or ctx.move_type not in types or attacker_breaks_mold(ctx):
                return None
            return modify(stat, 0.5)

        async def on_source_modify_atk(self, ctx, owner, stat: int) -> int | None:
            return await self._weaken(ctx, owner, stat)

        async def on_source_modify_spa(self, ctx, owner, stat: int) -> int | None:
            return await self._weaken(ctx, owner, stat)
    _Resist.__name__ = display.replace(" ", "") + "Ability"
    return _Resist


ThickFat = _halve_vs("thick-fat", "Thick Fat", frozenset({"Fire", "Ice"}))
Heatproof = _halve_vs("heatproof", "Heatproof", frozenset({"Fire"}))


async def _heatproof_burn(self, ctx, owner, damage: int, source: str = "") -> int | None:
    """Showdown `onDamage`: burn damage is halved (`effect.id === 'brn'` -> damage / 2, truncated)."""
    if source == "status" and owner.vitals.status == "burn":
        return max(1, damage // 2)
    return None


Heatproof.on_indirect_damage = _heatproof_burn

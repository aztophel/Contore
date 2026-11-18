"""Status/confusion immunity, Magic Guard, Leaf Guard, Synchronize.

Old code: ABILITY_STATUS_IMMUNITY dict (contore.py:2792-2799), consulted
from wherever a status is about to be applied. Leaf Guard (19266) and
Synchronize (19295-19296) are the two special-cased exceptions that don't
fit the flat table. Magic Guard is its own class at the bottom -- see
ability.py's on_indirect_damage docstring for why it's a separate hook
from the others.
"""
from __future__ import annotations

from ..registry import register_ability
from ..ability import Ability
from ._util import ABILITY_STATUS_IMMUNITY, attacker_breaks_mold, field_of


def _fixed_status_immunity(slug: str, display: str):
    statuses = ABILITY_STATUS_IMMUNITY[slug]

    @register_ability(slug)
    class _FixedStatusImmunity(Ability):
        async def on_status_immunity(self, ctx, owner, status) -> bool | None:
            if status not in statuses:
                return None
            if attacker_breaks_mold(ctx):
                return None
            ctx.log(f"**{owner.name}**'s {display} protects it!")
            return True
    _FixedStatusImmunity.__name__ = display.replace(" ", "").replace(".", "") + "Ability"
    return _FixedStatusImmunity


Immunity = _fixed_status_immunity("immunity", "Immunity")
Insomnia = _fixed_status_immunity("insomnia", "Insomnia")
VitalSpirit = _fixed_status_immunity("vital-spirit", "Vital Spirit")
Limber = _fixed_status_immunity("limber", "Limber")
WaterVeil = _fixed_status_immunity("water-veil", "Water Veil")
MagmaArmor = _fixed_status_immunity("magma-armor", "Magma Armor")
SweetVeil = _fixed_status_immunity("sweet-veil", "Sweet Veil")
PastelVeil = _fixed_status_immunity("pastel-veil", "Pastel Veil")


@register_ability("own-tempo")
class OwnTempo(Ability):
    """Confusion isn't in `self.status` in the old model -- it's its own
    boolean -- but is dispatched through the same on_status_immunity hook
    with status='confusion' here for a single consistent check point."""
    async def on_status_immunity(self, ctx, owner, status) -> bool | None:
        if status != "confusion" or attacker_breaks_mold(ctx):
            return None
        ctx.log(f"**{owner.name}**'s Own Tempo prevents confusion!")
        return True


@register_ability("comatose")
class Comatose(Ability):
    """Comatose blocks every status AND makes the holder permanently
    behave as if asleep for move-legality purposes (Dream Eater etc. can
    still target it, old code: contore.py:18398 `dab != "comatose"`
    exempting it from the sleep-only-move check). The permanently-asleep
    part needs engine support to expose 'counts as asleep'; the pure
    immunity half is complete here."""
    async def on_status_immunity(self, ctx, owner, status) -> bool | None:
        if status not in ABILITY_STATUS_IMMUNITY["comatose"]:
            return None
        return True


@register_ability("leaf-guard")
class LeafGuard(Ability):
    async def on_status_immunity(self, ctx, owner, status) -> bool | None:
        if attacker_breaks_mold(ctx):
            return None
        field = field_of(ctx)
        from .field_effects import get_weather
        if get_weather(field) not in ("sun", "harsh-sun"):
            return None
        ctx.log(f"**{owner.name}**'s Leaf Guard protects it from status in the sun!")
        return True


@register_ability("synchronize")
class Synchronize(Ability):
    _MIRRORED = frozenset({"poison", "bad_poison", "burn", "paralysis"})

    async def on_afflicted_with_status(self, ctx, owner, status) -> None:
        if status not in self._MIRRORED:
            return
        source = getattr(ctx, "user", None)
        if source is None or source is owner or source.fainted:
            return
        from ..status import inflict_status  # Phase 3
        await inflict_status(ctx, source, status)
        ctx.log(f"**{owner.name}**'s Synchronize passed the status to **{source.name}**!")


@register_ability("magic-guard")
class MagicGuard(Ability):
    async def on_indirect_damage(self, ctx, owner, damage: int, source: str = "") -> int | None:
        return 0

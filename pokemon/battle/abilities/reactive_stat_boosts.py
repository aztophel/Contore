"""Defiant and Competitive -- react to ANY of the holder's stats being lowered
by a foe (Showdown `onAfterEachBoost`). Also Liquid Ooze, which doesn't fit that description but is
small enough not to need a file of its own either. Old code: hardcoded twice, once inside Intimidate's own
switch-in handler (contore.py's old FighterState._apply_entry_abilities)
and again inside apply_move's generic stat-changes block
(contore.py:19238-19245). Neither copy covered every foe-caused stat drop
uniformly (a stray one from a held item or a future move would have
silently missed it), so this is implemented once against the new
on_stat_lowered hook (battler.py's clamp_stage dispatches it for every
foe-caused drop that actually lands, from any source) instead of being
duplicated per call site again.

This is a new file rather than an edit to any of Track D's existing
ability files -- new files under abilities/ are an explicitly-safe,
additive way to extend the registry (see CONTRACT.md), and Track D's
session is finished, so there's no in-flight work here to conflict with.
"""
from __future__ import annotations

from ..registry import register_ability
from ..ability import Ability


def _reactive_boost(slug: str, stat: str, display: str, label: str):
    @register_ability(slug)
    class _Reactive(Ability):
        """Showdown: ANY stat lowered by an opponent (not just the one this ability boosts) raises `stat` by two stages -- once PER LOWERED STAT (Parting
        Shot / Venom Drench drop two stats and Defiant gives +4: verified against Showdown, which fires `onAfterEachBoost` for each stat)."""
        async def on_stat_lowered(self, ctx, owner, lowered: str, diff: int) -> None:
            msg = await owner.clamp_stage(ctx, stat, 2)
            ctx.log(msg if msg else f"**{owner.name}**'s {display} raised its {label}!")
    _Reactive.__name__ = display + "Ability"
    return _Reactive


Defiant = _reactive_boost("defiant", "atk", "Defiant", "Attack")
Competitive = _reactive_boost("competitive", "spa", "Competitive", "Sp. Atk")


@register_ability("liquid-ooze")
class LiquidOoze(Ability):
    async def on_modify_drain(self, ctx, owner, amount: int) -> int | None:
        targets = getattr(ctx, "targets", None)          # a drain MOVE has targets; Leech Seed's end-of-turn tick (a BattleContext) does not
        if targets is not None and owner not in targets:
            return None
        move = getattr(ctx, "move", None)
        if ctx.ruleset.number <= 4 and move is not None and move.spec.slug == "dream-eater":
            return None                                          # Showdown gen4 mod: `this.activeMove?.id !== 'dreameater'`
        return -amount

"""Heal Bell, Aromatherapy, Refresh -- none was registered, so all three silently did nothing. Ported from Showdown's data/moves.ts and the gen4/gen5/gen7 mods:
  Heal Bell     Gens 8-9: cures the whole party except allies with Soundproof / Good as Gold (the USER is always cured); Gens 6-7: Soundproof only; Gen 5: everyone, no failure; Gens 3-4: Soundproof skips.
  Aromatherapy  Gens 6-9: skips allies with Sap Sipper / Good as Gold, or behind a Substitute; Gens 3-5: everyone.
  Refresh       cures poison / burn / paralysis of the user; fails with no status, or sleep / freeze.
Both team moves fail ("But it failed!") when nobody had a status to cure, except in Gen 5 and earlier."""
from __future__ import annotations

from ..move import Move
from ..context import MoveContext
from ..registry import register_move
from ..abilities._util import attacker_breaks_mold


def _cure(mon) -> bool:
    if mon.fainted or not mon.vitals.status:
        return False
    mon.vitals.status = None
    mon.vitals.status_turns = 0
    return True


def _party(ctx: MoveContext) -> list:
    side = ctx.battle.field.side_of(ctx.user)
    return [b for b in list(side.active) + list(side.bench) if b is not None]


class _TeamCure(Move):
    async def get_targets(self, ctx: MoveContext) -> list:
        return [ctx.user]

    def skips(self, ctx: MoveContext, ally) -> bool:
        return False

    async def on_hit(self, ctx: MoveContext, target) -> None:
        ctx.log(f"A bell chimed!" if ctx.move.slug == "heal-bell" else "A soothing aroma wafted through the area!")
        cured = False
        for ally in _party(ctx):
            if ally is not ctx.user and not attacker_breaks_mold(ctx) and self.skips(ctx, ally):
                continue
            cured = _cure(ally) or cured
        if not cured and ctx.ruleset.number >= 6:
            ctx.log("But it failed!")


@register_move("heal-bell")
class HealBellMove(_TeamCure):
    def skips(self, ctx: MoveContext, ally) -> bool:
        ab, gen = ally.effective_ability(), ctx.ruleset.number
        if gen >= 8:
            return ab in ("soundproof", "good-as-gold")
        if gen >= 6 or gen <= 4:
            return ab == "soundproof"                    # (Gens 6-7 also check the user itself; it can't have a status-immune bell anyway)
        return False                                      # Gen 5: everybody


@register_move("aromatherapy")
class AromatherapyMove(_TeamCure):
    def skips(self, ctx: MoveContext, ally) -> bool:
        if ctx.ruleset.number < 6:
            return False
        from ..volatile import Substitute
        return ally.effective_ability() in ("sap-sipper", "good-as-gold") or (ally.has(Substitute) and ctx.ruleset.number >= 6)


@register_move("refresh")
class RefreshMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        user = ctx.user
        if user.vitals.status in (None, "sleep", "freeze") or not _cure(user):
            ctx.log("But it failed!"); return
        ctx.log(f"**{user.name}** became healthy!")

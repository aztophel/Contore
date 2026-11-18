"""Protect and its whole family. Old code: contore.py:16983-17000, whose
`1.0 / (3 ** n)` consecutive-use formula is an exact match for Pokemon
Showdown's "stall" condition (data/conditions.ts) -- first use always
succeeds, second consecutive use has a 1/3 chance, third 1/9, and so on,
resetting to 0 the instant either a non-protect move is used or a roll
fails. `Battler.protect_consecutive_uses` is a plain int attribute (not a
Component) matching this codebase's established convention for small,
single-purpose counters that don't need their own Component/Volatile
class (see e.g. contact_abilities.py's Cursed Body) -- reset generically
in engine.py's _execute_move for ANY non-protect move, rather than
scattered across many move branches the way old code's many
`protect_consecutive_uses = 0` call sites were.
"""
from __future__ import annotations
import random
from typing import TYPE_CHECKING

from ..move import Move
from ..context import MoveContext
from ..registry import register_move
from ..volatile import ProtectionActive

if TYPE_CHECKING:
    from ..battler import Battler


def _protect_move(slug: str, kind: str):
    @register_move(slug)
    class _ProtectMove(Move):
        async def on_try_use(self, ctx: MoveContext) -> str | None:
            user = ctx.user
            n = getattr(user, "protect_consecutive_uses", 0)
            if n > 0 and random.random() >= 1.0 / (3 ** n):
                user.protect_consecutive_uses = 0
                return "But it failed!"
            return None

        async def get_targets(self, ctx: MoveContext) -> list["Battler"]:
            return [ctx.user]

        async def on_hit(self, ctx: MoveContext, target) -> None:
            user = ctx.user
            user.protect_consecutive_uses = getattr(user, "protect_consecutive_uses", 0) + 1
            user.add(ProtectionActive(kind=kind), ctx.battle.engine.events)
            # ProtectionActive's own on_try_hit logs "protected itself"
            # once something actually tries to hit it this turn -- no
            # message needed here for the setup itself, matching how a
            # screen-setting move logs on activation but Protect doesn't
            # announce anything until it's actually tested.
    _ProtectMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _ProtectMove


ProtectMove = _protect_move("protect", "protect")
DetectMove = _protect_move("detect", "protect")
SpikyShieldMove = _protect_move("spiky-shield", "spiky_shield")
KingsShieldMove = _protect_move("kings-shield", "king_shield")
BanefulBunkerMove = _protect_move("baneful-bunker", "baneful_bunker")


def _side_protect_move(slug: str, kind: str, message: str):
    """Wide Guard / Quick Guard / Crafty Shield -- side-wide, one turn,
    same consecutive-use formula as the single-target family (Pokemon
    Showdown's "stall" volatile is added to the USER in both cases, the
    side condition itself doesn't carry its own separate counter)."""
    @register_move(slug)
    class _SideProtectMove(Move):
        async def on_try_use(self, ctx: MoveContext) -> str | None:
            user = ctx.user
            n = getattr(user, "protect_consecutive_uses", 0)
            if n > 0 and random.random() >= 1.0 / (3 ** n):
                user.protect_consecutive_uses = 0
                return "But it failed!"
            return None

        async def get_targets(self, ctx: MoveContext) -> list["Battler"]:
            return [ctx.user]

        async def on_hit(self, ctx: MoveContext, target) -> None:
            user = ctx.user
            user.protect_consecutive_uses = getattr(user, "protect_consecutive_uses", 0) + 1
            ctx.user_side.add(ProtectionActive(kind=kind), ctx.battle.engine.events)
            ctx.log(message)
    _SideProtectMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _SideProtectMove


WideGuardMove = _side_protect_move("wide-guard", "wide_guard", "Wide Guard protected the team!")
QuickGuardMove = _side_protect_move("quick-guard", "quick_guard", "Quick Guard protected the team!")
CraftyShieldMove = _side_protect_move("crafty-shield", "crafty_shield", "Crafty Shield protected the team!")
MatBlockMove = _side_protect_move("mat-block", "mat_block", "**{name}** intends to flip up a mat and block incoming attacks!")

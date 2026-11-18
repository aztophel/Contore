"""Moves that can fail (or be cancelled) for a reason that isn't about the
target's type or the move's accuracy -- ported from Pokemon Showdown's
data/moves.ts. All of these used to run as plain `BasicMove`s, i.e. Fake Out
flinched EVERY turn, Sucker Punch hit anything, Snore worked while awake, ...

Each is expressed through the generic `Move` hooks (move.py): `on_try_use`
(fail before anything happens), `on_try_immunity` (immune target),
`before_move_callback` / `on_before_turn` (Focus Punch's setup/cancel),
`can_use_while_asleep` (`sleepUsable`). The engine supplies the state they
read: `Battler.move_actions` (Showdown `activeMoveActions`),
`MoveSlot.used`, `BattleEngine.will_move` (`queue.willMove`).
"""
from __future__ import annotations

from ..move import BasicMove, MoveCategory
from ..context import MoveContext
from ..registry import register_move
from ..volatile import FocusPunchCharge, MustRecharge

FAIL = "But it failed!"


@register_move("fake-out")
class FakeOutMove(BasicMove):
    """`onTry: activeMoveActions > 1` -> fails. The count includes this
    action and any turn lost to sleep/paralysis/flinch, and resets on
    switch-in. (Flinch itself is the move's ordinary data-driven secondary.)"""
    async def on_try_use(self, ctx: MoveContext) -> str | None:
        return FAIL if ctx.user.move_actions > 1 else None


@register_move("sucker-punch")
class SuckerPunchMove(BasicMove):
    """Only works if the target is ABOUT to use a damaging move this turn:
    fails if it already moved, is switching, picked a status move (Me First
    excepted), or must recharge."""
    async def on_try_use(self, ctx: MoveContext) -> str | None:
        targets = await self.get_targets(ctx)
        if not targets:
            return None  # the engine reports "no target" itself
        target = targets[0]
        engine = ctx.battle.engine
        queued = engine.will_move(target)
        if queued is None or target.has(MustRecharge):
            return FAIL
        if engine.moves.spec(queued.move_slug).category is MoveCategory.STATUS and queued.move_slug != "me-first":
            return FAIL
        return None


@register_move("dream-eater")
class DreamEaterMove(BasicMove):
    """Drain is the ordinary data-driven 50%; the move-specific part is that
    only a sleeping (or Comatose) target can be hit."""
    async def on_try_immunity(self, ctx: MoveContext, target) -> bool:
        return target.vitals.status == "sleep" or target.effective_ability() == "comatose"


@register_move("snore")
class SnoreMove(BasicMove):
    """`sleepUsable` + `onTry: user must be asleep`. (Flinch is its normal
    secondary.) Usable while asleep, fails otherwise."""
    def can_use_while_asleep(self) -> bool:
        return True

    async def on_try_use(self, ctx: MoveContext) -> str | None:
        u = ctx.user
        return None if (u.vitals.status == "sleep" or u.effective_ability() == "comatose") else FAIL


@register_move("last-resort")
class LastResortMove(BasicMove):
    """Fails unless the user knows Last Resort plus at least one other move,
    and has used every other move since switching in."""
    async def on_try_use(self, ctx: MoveContext) -> str | None:
        slots = ctx.user.moves.slots
        if len(slots) < 2:
            return FAIL
        has_self = False
        for s in slots:
            if s.move_slug == "last-resort":
                has_self = True
            elif not s.used:
                return FAIL
        return None if has_self else FAIL


@register_move("focus-punch")
class FocusPunchMove(BasicMove):
    """Priority -3. At the start of the turn the user "tightens its focus";
    if it's hit by a damaging move before it acts, it loses focus and the
    move is cancelled with no PP spent."""
    async def on_before_turn(self, ctx: MoveContext) -> None:
        ctx.user.add(FocusPunchCharge(), ctx.battle.engine.events)
        ctx.log(f"**{ctx.user.name}** is tightening its focus!")

    async def before_move_callback(self, ctx: MoveContext) -> bool:
        charge = ctx.user.get(FocusPunchCharge)
        if charge is not None and charge.lost_focus:
            ctx.log(f"**{ctx.user.name}** lost its focus and couldn't move!")
            return True
        return False

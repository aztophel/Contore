"""Moves whose whole effect is a VOLATILE that only matters on a LATER turn (found by tests/t_deferred_probe.py: they used to be generic `BasicMove`s that did nothing, and every one-turn sweep passed):
Charge, Foresight, Odor Sleuth, Miracle Eye. Ported from Showdown's move conditions (data/moves.ts).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..move import BasicMove, Move
from ..registry import register_move
from ..volatile import Volatile


def _failed(ctx) -> None:
    ctx.log("But it failed!")


def _type_of(ctx) -> str:
    return getattr(ctx, "move_type", None) or ctx.move.spec.type_name


@dataclass
class Charge(Volatile):
    """Showdown `charge`: the holder's next Electric move has x2 power (`chainModify(2)`); it ends after the next move that is not Charge itself (Gens 3-8: of any type; Gen 9: only an Electric one), hit or abort."""
    slug: str = "charge"

    async def on_modify_power(self, ctx, owner, power: int) -> int | None:
        if owner is ctx.user and _type_of(ctx) == "Electric":
            from ..fixed import modify_raw
            return modify_raw(power, 8192)
        return None

    async def _end(self, ctx, owner) -> None:
        # Gens 3-8 (data/mods/gen8/moves.ts): it is removed after the NEXT move of ANY type (the boost only applies to an Electric one); Gen 9: only an Electric move ends it.
        if ctx.user is owner and ctx.move.slug != "charge" and (ctx.ruleset.number <= 8 or _type_of(ctx) == "Electric"):
            owner.remove(Charge, ctx.events)

    async def on_after_move_self(self, ctx, owner) -> None:
        await self._end(ctx, owner)

    async def on_move_aborted(self, ctx, owner) -> None:
        await self._end(ctx, owner)


@register_move("charge")
class ChargeMove(BasicMove):
    """The generic part (Sp. Def +1 from Gen 5) plus the volatile."""
    async def on_hit(self, ctx, target) -> None:
        await super().on_hit(ctx, target)
        user = ctx.user
        if not user.has(Charge):
            user.add(Charge(), ctx.events)
        ctx.log(f"**{user.name}** began charging power!")


@dataclass
class Foresight(Volatile):
    """Showdown `foresight` (Foresight, Odor Sleuth): the holder's Ghost type no longer makes it immune to Normal and Fighting moves (`onNegateImmunity`), and its raised evasion is ignored.
    `damage.py` asks via `identified_immune_type`."""
    slug: str = "foresight"


@dataclass
class MiracleEye(Volatile):
    """Showdown `miracleeye`: a Dark type is no longer immune to Psychic moves, and its raised evasion is ignored."""
    slug: str = "miracle-eye"


@dataclass
class DefenseCurled(Volatile):
    """Showdown `defensecurl`: no effect of its own, but Rollout and Ice Ball do double power for a holder (until it switches out)."""
    slug: str = "defense-curl"


@register_move("defense-curl")
class DefenseCurlMove(BasicMove):
    async def on_hit(self, ctx, target) -> None:
        await super().on_hit(ctx, target)
        if not ctx.user.has(DefenseCurled):
            ctx.user.add(DefenseCurled(), ctx.events)


class _RollingMove(BasicMove):
    """Rollout / Ice Ball (Showdown `rollout` / `iceball`): the first use locks the user in (`duration: 1`, raised to 2 each time the move actually computes damage while fewer than 5 hits have been made);
    power 30 x 2^(hits so far), x2 more with Defense Curl. A miss / immunity / Protect never reaches the power callback, so the lock simply runs out."""
    async def on_try_use(self, ctx) -> str | None:
        from ..volatile import LockedMove
        user = ctx.user
        if not user.has(LockedMove) and user.vitals.status != "sleep":
            user.add(LockedMove(move_slug=self.spec.slug, kind="rollout"), ctx.events)
        return await super().on_try_use(ctx)

    async def on_base_power(self, ctx, target) -> int | None:
        from ..volatile import LockedMove
        user = ctx.user
        lock = user.get(LockedMove)
        power = 30
        if lock is not None and lock.move_slug == self.spec.slug:
            if lock.hit_count:
                power *= 2 ** lock.hit_count
            if user.vitals.status != "sleep":
                lock.hit_count += 1
                if lock.hit_count < 5:
                    lock.turns_left = 2
        if user.has(DefenseCurled):
            power *= 2
        return power


@register_move("rollout")
class RolloutMove(_RollingMove):
    pass


@register_move("ice-ball")
class IceBallMove(_RollingMove):
    pass


class _RampageMove(BasicMove):
    """Outrage / Thrash / Petal Dance (Showdown `self: {volatileStatus: 'lockedmove'}`): every HIT starts the lock for random(2, 4) turns or refreshes it while `trueDuration >= 2`; a spent lock confuses the user
    (`LockedMove.end`, at the residual). (Showdown's Gen 5+ `onAfterMove` removal one step earlier is indistinguishable from that in every scenario I can build, so it is not ported.)"""
    async def on_hit(self, ctx, target) -> None:
        from ..volatile import LockedMove
        await super().on_hit(ctx, target)
        if ctx.hit_blocked:                         # immune / absorbed / nullified: the move never hit, so Showdown applies no `self` volatile
            return
        user = ctx.user
        lock = user.get(LockedMove)
        if lock is None:
            import random
            user.add(LockedMove(move_slug=self.spec.slug, kind="rampage", turns_left=2, true_duration=random.randint(2, 3)), ctx.events)
        elif lock.true_duration >= 2:
            lock.turns_left = 2

@register_move("outrage")
class OutrageMove(_RampageMove):
    pass


@register_move("thrash")
class ThrashMove(_RampageMove):
    pass


@register_move("petal-dance")
class PetalDanceMove(_RampageMove):
    pass


def negates_type_immunity(target, move_type: str) -> bool:
    """True if a Foresight / Miracle Eye on `target` removes the immunity its type gives against `move_type`."""
    if move_type in ("Normal", "Fighting") and target.has(Foresight):
        return True
    return move_type == "Psychic" and target.has(MiracleEye)


def ignores_raised_evasion(target) -> bool:
    return target.has(Foresight) or target.has(MiracleEye)


class _IdentifyMove(Move):
    volatile_cls: type = Foresight

    async def on_hit(self, ctx, target) -> None:
        if target.has(self.volatile_cls):
            _failed(ctx)
            return
        target.add(self.volatile_cls(), ctx.events)
        ctx.log(f"**{target.name}** was identified!")


@register_move("recycle")
class RecycleMove(Move):
    """Showdown `recycle`: the user gets back the item it last USED (`lastItem`); fails if it already holds one or has none to recover. (Knock Off / Thief / Trick never set `lastItem`.)"""
    async def on_hit(self, ctx, target) -> None:
        user = ctx.user
        if user.identity.held_item or not user.last_item:
            _failed(ctx)
            return
        item, user.last_item = user.last_item, None
        user.identity.held_item = item
        user.unburden_active = False
        ctx.engine.sync_ability_and_item(user)
        ctx.log(f"**{user.name}** found one {item}!")


@register_move("foresight")
class ForesightMove(_IdentifyMove):
    volatile_cls = Foresight


@register_move("odor-sleuth")
class OdorSleuthMove(_IdentifyMove):
    volatile_cls = Foresight


@register_move("miracle-eye")
class MiracleEyeMove(_IdentifyMove):
    volatile_cls = MiracleEye

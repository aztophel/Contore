"""Damaging moves whose EFFECT was missing or unconditional, found by sweeping every damaging move through eleven battle contexts against Showdown (tests/t_conditional_sweep.py KIND=damaging):
Rapid Spin (hazards, Leech Seed, trapping), Brick Break / Psychic Fangs (screens), Clear Smog (stat reset), Hyperspace Fury (Hoopa Unbound only), Synchronoise (shared type only)."""
from __future__ import annotations

from ..move import BasicMove
from ..context import MoveContext
from ..registry import register_move


def _landed(ctx: MoveContext, target) -> bool:
    return not ctx.hit_blocked and getattr(ctx, "hits_on_target", 1) >= 0


@register_move("rapid-spin")
class RapidSpinMove(BasicMove):
    """After the hit (also through a Substitute): the USER sheds Leech Seed, its side's entry hazards (Spikes, Toxic Spikes, Stealth Rock, Sticky Web) and any trapping move. The +1 Speed is the data's
    secondary (Gen 8+). (Showdown `onAfterHit` / `onAfterSubDamage`.)"""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        await super().on_hit(ctx, target)
        if ctx.hit_blocked:
            return
        from ..volatile import LeechSeed, Binding
        from ..side_conditions import StealthRock, Spikes, ToxicSpikes, StickyWeb
        user, events = ctx.user, ctx.engine.events
        if user.fainted or await ctx.events.first("on_suppress_secondary_effect", ctx):       # Sheer Force
            return
        if user.has(LeechSeed):
            user.remove(LeechSeed, events)
            ctx.log(f"**{user.name}** shed Leech Seed!")
        side = ctx.battle.field.side_of(user)
        for cond in (Spikes, ToxicSpikes, StealthRock, StickyWeb):
            if side.remove(cond, events) is not None:
                ctx.log(f"The {cond.__name__} around **{user.name}**'s side were blown away!")
        if user.has(Binding):
            user.remove(Binding, events)
            ctx.log(f"**{user.name}** broke free!")


def _screen_breaker(slug: str):
    @register_move(slug)
    class _ScreenBreaker(BasicMove):
        """Shatters Reflect / Light Screen / Aurora Veil on the TARGET's side BEFORE the damage (Showdown `onTryHit`), through a Substitute."""
        async def on_hit(self, ctx: MoveContext, target) -> None:
            from ..side_conditions import Reflect, LightScreen, AuroraVeil
            side = ctx.battle.field.side_of(target)
            for cond in (Reflect, LightScreen, AuroraVeil):
                if side.remove(cond, ctx.engine.events) is not None:
                    ctx.log(f"**{ctx.user.name}** shattered the {cond.__name__} barrier!")
            await super().on_hit(ctx, target)
    _ScreenBreaker.__name__ = slug.replace("-", " ").title().replace(" ", "") + "Move"
    return _ScreenBreaker


BrickBreakMove = _screen_breaker("brick-break")
PsychicFangsMove = _screen_breaker("psychic-fangs")


@register_move("clear-smog")
class ClearSmogMove(BasicMove):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        await super().on_hit(ctx, target)
        if ctx.hit_blocked or getattr(ctx, "hits_on_target", 1) <= 0 or target.fainted:
            return
        if any(target.stages.get(k, 0) for k in target.stages):
            for k in list(target.stages):
                target.stages[k] = 0
            ctx.log(f"**{target.name}**'s stat changes were removed!")


@register_move("hyperspace-fury")
class HyperspaceFuryMove(BasicMove):
    """Only Hoopa Unbound can use it (Showdown `onTry`); everyone else just fails."""
    async def on_try_use(self, ctx: MoveContext):
        if "unbound" not in ctx.user.identity.species_name.lower():
            return "But it failed!"
        return None


@register_move("synchronoise")
class SynchronoiseMove(BasicMove):
    """Only hits a target that shares a type with the user (Showdown `onTryImmunity`)."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        if not set(target.effective_types()) & set(ctx.user.effective_types()):
            ctx.log(f"It doesn't affect **{target.name}**...")
            ctx.hit_blocked = True
            return
        await super().on_hit(ctx, target)


# -- Future Sight / Doom Desire ---------------------------------------------------------------------------------------------------------------------------

from dataclasses import dataclass
from ..volatile import Volatile
from ..context import MoveContext as _MC


@dataclass
class FutureMoveTracker(Volatile):
    """A SIDE effect (Showdown's `futuremove` slot condition): `pending` maps the TARGET's slot -> {slug, source, turns}. The hit lands at the end of the SECOND turn after the one it was used on (T+2), against
    whatever stands in that slot then, using the SOURCE's stats at that moment (Showdown order 3 in Gen 5+, 10.11 in Gens 3-4)."""
    slug: str = "futuremove"
    pending: dict = None
    residual_order = (3, 0)
    residual_order_gen4 = (10, 11)

    def __post_init__(self):
        if self.pending is None:
            self.pending = {}

    async def on_end_turn(self, ctx, owner) -> None:
        for slot in list(self.pending):
            entry = self.pending[slot]
            entry["turns"] -= 1
            if entry["turns"] > 0:
                continue
            del self.pending[slot]
            target = owner.active[slot] if slot < len(owner.active) else None
            source = entry["source"]
            if target is None or target.fainted or target is source:
                continue
            ctx.log(f"**{target.name}** took the {entry['display']} attack!")
            move = ctx.engine.moves.get(entry["slug"])
            mctx = _MC(battle=ctx, move=move, user=source)
            mctx.future_hit = True
            mctx.user_self_ko = source.fainted        # the attack still lands if its user has fainted since (Showdown only skips a fainted TARGET)
            await ctx.engine._resolve_move(mctx, move, source, entry["slug"], [target])


def _delayed_move(slug: str):
    @register_move(slug)
    class _Delayed(BasicMove):
        # Showdown data/mods/gen4: the delayed hit is typeless (`???`: no STAB, no weakness / immunity) and `willCrit: false` in Gens 3-4. (There the damage is also FIXED when the move is used -- not ported:
        # this engine has no side-effect-free damage calculation -- so a stat change between use and hit still shows.)
        def never_crits(self, ctx: MoveContext) -> bool:
            return ctx.ruleset.number <= 4

        async def on_modify_type(self, ctx: MoveContext, target, current_type: str) -> str | None:
            return "???" if ctx.ruleset.number <= 4 and getattr(ctx, "future_hit", False) else None

        async def on_try_use(self, ctx: MoveContext):
            if getattr(ctx, "future_hit", False):
                return None                                                    # the delayed hit itself: a normal damaging move
            targets = await self.get_targets(ctx)
            if not targets:
                return "But it failed!"
            target = targets[0]
            side = ctx.battle.field.side_of(target)
            slot = side.active.index(target)
            tracker = side.get(FutureMoveTracker)
            if tracker is None:
                tracker = FutureMoveTracker()
                side.add(tracker, ctx.engine.events)
            if slot in tracker.pending:
                return "But it failed!"                                        # one pending future attack per slot (Showdown `addSlotCondition` refuses)
            tracker.pending[slot] = {"slug": slug, "source": ctx.user, "turns": 3, "display": self.spec.display_name}
            ctx.user.last_move_slug = slug
            return f"**{ctx.user.name}** foresaw an attack!"
    _Delayed.__name__ = slug.replace("-", " ").title().replace(" ", "") + "Move"
    return _Delayed


FutureSightMove = _delayed_move("future-sight")
DoomDesireMove = _delayed_move("doom-desire")


# -- Magnitude / Present / Beat Up ------------------------------------------------------------------------------------------------------------------------

import random as _random


@register_move("magnitude")
class MagnitudeMove(BasicMove):
    """Power 10 / 30 / 50 / 70 / 90 / 110 / 150 at 5 / 10 / 20 / 30 / 20 / 10 / 5 % (Showdown `onModifyMove`: `random(100)`)."""
    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        if not hasattr(ctx, "magnitude_power"):
            i = _random.randint(0, 99)
            level, power = next((m, p) for lim, m, p in ((5, 4, 10), (15, 5, 30), (35, 6, 50), (65, 7, 70), (85, 8, 90), (95, 9, 110), (100, 10, 150)) if i < lim)
            ctx.magnitude_power = power
            ctx.log(f"Magnitude **{level}**!")
        return ctx.magnitude_power


@register_move("present")
class PresentMove(BasicMove):
    """20%: heals the TARGET by a quarter of its max HP instead of hurting it; otherwise 40 (40%), 80 (30%) or 120 (10%) power (Showdown `onModifyMove`: `random(10)`)."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        r = _random.randint(0, 9)
        if r < 2:
            ctx.hit_blocked = True                                      # no damage; the move "didn't land" as an attack
            amount = (2 * target.vitals.max_hp + 4) // 8 if ctx.ruleset.number >= 5 else target.vitals.max_hp // 4
            healed = target.heal(amount)
            ctx.log(f"**{target.name}** had its HP restored! (+**{healed:,}** HP)" if healed else "But it failed!")
            return
        ctx.present_power = 40 if r < 6 else 80 if r < 9 else 120
        await super().on_hit(ctx, target)

    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        return getattr(ctx, "present_power", 40)


@register_move("beat-up")
class BeatUpMove(BasicMove):
    """Gen 5+: one hit per healthy, status-free party member (the user always counts), each of power 5 + base Attack / 10 of THAT member, all using the USER's Attack.
    Gens 3-4 (Showdown data/mods/gen3|gen4/moves.ts): typeless (`???`), a flat power 10 per hit, a statused user does NOT count, and each hit's damage is computed from that ally's BASE Attack
    against the target's BASE Defense (every stage / item / ability modifier overridden). Physical in Gen 4, Special in Gen 3 (already in the per-generation category data), but in both the two base stats above."""
    def _allies(self, ctx: MoveContext) -> list:
        if not hasattr(ctx, "beat_up_allies"):
            side = ctx.battle.field.side_of(ctx.user)
            party = [b for b in list(side.active) + list(side.bench) if b is not None]
            user_counts = ctx.ruleset.number >= 5
            ctx.beat_up_allies = [b for b in party if (user_counts and b is ctx.user) or (not b.fainted and not b.vitals.status)]
        return ctx.beat_up_allies

    def _hit_ally(self, ctx: MoveContext):
        allies = self._allies(ctx)
        return allies[min(getattr(ctx, "current_hit_index", 0), len(allies) - 1)]

    async def on_hit_count(self, ctx: MoveContext) -> int | None:
        return len(self._allies(ctx))

    async def on_base_power(self, ctx: MoveContext, target) -> int | None:
        if ctx.ruleset.number <= 4:
            return 10
        stats = self._hit_ally(ctx).identity.base_stats
        return 5 + stats.get("attack", stats.get("atk", 50)) // 10

    async def on_modify_type(self, ctx: MoveContext, target, current_type: str) -> str | None:
        return "???" if ctx.ruleset.number <= 4 else None

    async def on_modify_stats(self, ctx: MoveContext, target, atk: int, dfe: int):
        if ctx.ruleset.number > 4:
            return None
        mine, theirs = self._hit_ally(ctx).identity.base_stats, target.identity.base_stats
        return (mine.get("attack", mine.get("atk", 50)), theirs.get("defense", theirs.get("def", 50)))


@register_move("feint")
class FeintMove(BasicMove):
    """Gens 5+: breaks through Protect (the data's PROTECT_BYPASS flag). Gens 3-4 (Showdown gen4 mod `onTry`): only works on a target that IS protecting."""
    async def on_try_use(self, ctx: MoveContext):
        if ctx.ruleset.number <= 4:
            from ..volatile import ProtectionActive
            targets = await self.get_targets(ctx)
            if not any(t.has(ProtectionActive) for t in targets):
                return "But it failed!"
        return None

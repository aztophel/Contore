"""HP recovery moves: flat 50% (Recover/Roost/Soft-Boiled/Milk Drink),
weather-dependent (Synthesis/Moonlight/Morning Sun), and Rest (its own
thing entirely -- full heal plus a fixed-length sleep, not just a bigger
heal). Old code: apply_move's healing-move branches.

Roost's temporary Flying-type removal for the turn (Pokemon Showdown:
`onTypePriority`/a volatile clearing on residual) isn't implemented here
yet -- it heals correctly but doesn't currently strip Flying/change
Normal-monotype-Flying to typeless for the turn. Noted rather than
silently skipped; see MIGRATION_STATUS.md.
"""
from __future__ import annotations

from ..move import Move
from ..context import MoveContext
from ..registry import register_move


def _flat_heal(ctx, max_hp: int, num: int = 1, den: int = 2) -> int:
    """Showdown's generic `heal: [num, den]`: `Math.floor` before Gen 5, `Math.round` (halves UP) from Gen 5 -- the old `int()` healed 1 HP too little on every odd max HP."""
    if ctx.ruleset.number >= 5:
        return (2 * max_hp * num + den) // (2 * den)
    return max_hp * num // den


def _flat_recovery_move(slug: str, fraction: float = 0.5):
    @register_move(slug)
    class _FlatRecoveryMove(Move):
        async def on_try_use(self, ctx: MoveContext) -> str | None:
            if ctx.user.fainted or ctx.user.vitals.hp == ctx.user.vitals.max_hp:
                return "But it failed!"
            return None

        async def on_hit(self, ctx: MoveContext, target) -> None:
            healed = ctx.user.heal(_flat_heal(ctx, ctx.user.vitals.max_hp))
            ctx.log(f"**{ctx.user.name}** restored **{healed:,}** HP!" if healed else "But it failed!")
    _FlatRecoveryMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _FlatRecoveryMove


RecoverMove = _flat_recovery_move("recover")
RoostMove = _flat_recovery_move("roost")
SoftBoiledMove = _flat_recovery_move("soft-boiled")
MilkDrinkMove = _flat_recovery_move("milk-drink")
SlackOffMove = _flat_recovery_move("slack-off")
HealOrderMove = _flat_recovery_move("heal-order")             # was unregistered: it did nothing at all


@register_move("heal-pulse")
class HealPulseMove(Move):
    """Heals the TARGET (an ally or a foe) by half its max HP rounded UP, three quarters with Mega Launcher; fails at full HP (Showdown `healpulse`)."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..fixed import modify_raw
        max_hp = target.vitals.max_hp
        amount = modify_raw(max_hp, 3072) if ctx.user.effective_ability() == "mega-launcher" else -(-max_hp // 2)
        healed = target.heal(amount)
        ctx.log(f"**{target.name}** had its HP restored! (+**{healed:,}** HP)" if healed else "But it failed!")


def _weather_recovery_move(slug: str):
    """Old code: apply_move's Synthesis/Moonlight/Morning Sun branch --
    2/3 max HP in sun, 1/4 in any other weather, 1/2 with none. Pokemon
    Showdown: data/moves.ts's synthesis onHit (moonlight/morningsun are
    identical apart from type/name)."""
    @register_move(slug)
    class _WeatherRecoveryMove(Move):
        async def on_try_use(self, ctx: MoveContext) -> str | None:
            if ctx.user.fainted or ctx.user.vitals.hp == ctx.user.vitals.max_hp:
                return "But it failed!"
            return None

        async def on_hit(self, ctx: MoveContext, target) -> None:
            from ..abilities._util import weather_suppressed
            weather = None if weather_suppressed(ctx.field) else ctx.field.weather
            max_hp = ctx.user.vitals.max_hp
            if ctx.ruleset.number >= 5:                   # `this.modify(maxhp, factor)`: the chained 4096ths, rounding halves DOWN (0.667 -> 2732)
                from ..fixed import modify_raw
                amount = modify_raw(max_hp, 2732 if weather in ("sun", "harsh-sun") else 1024 if weather in ("rain", "heavy-rain", "sand", "hail", "snow") else 2048)
            else:                                          # Gens 3-4: `this.heal(maxhp * 2 / 3)` etc., truncated
                amount = max_hp * 2 // 3 if weather in ("sun", "harsh-sun") else max_hp // 4 if weather in ("rain", "heavy-rain", "sand", "hail", "snow") else max_hp // 2
            healed = ctx.user.heal(amount)
            ctx.log(f"**{ctx.user.name}** restored **{healed:,}** HP!" if healed else "But it failed!")
    _WeatherRecoveryMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _WeatherRecoveryMove


SynthesisMove = _weather_recovery_move("synthesis")
MoonlightMove = _weather_recovery_move("moonlight")
MorningSunMove = _weather_recovery_move("morning-sun")


@register_move("rest")
class RestMove(Move):
    """Old code: apply_move's Rest branch. Fully heals AND sets a fixed
    3-turn sleep (not the usual random 1-3 -- Pokemon Showdown:
    data/moves.ts's rest onHit sets `statusState.time = 3` explicitly,
    distinct from a normal sleep infliction's random roll), and fails
    outright if already at full HP or already asleep -- checked here in
    on_try_use rather than letting inflict_status's normal
    already-statused check produce a slightly different failure message.

    Rest's whole point is CURING an existing status (poison/burn/
    paralysis) and replacing it with sleep -- so any pre-existing status
    OTHER than sleep must be cleared before calling inflict_status, not
    left for that function's normal "already has a status" guard to
    reject. An earlier draft only checked for existing SLEEP specifically
    in on_try_use and then called inflict_status unmodified, which
    silently made Rest fail outright against a poisoned/burned/paralyzed
    Pokemon instead of curing it -- inflict_status's blocking check has
    no way to distinguish "already has a different status that this
    move is explicitly supposed to overwrite" from the ordinary case."""
    async def on_try_use(self, ctx: MoveContext) -> str | None:
        user = ctx.user
        if user.fainted or user.vitals.status == "sleep" or user.identity.ability_slug == "comatose":
            return "But it failed!"
        if user.vitals.hp == user.vitals.max_hp:
            return "But it failed!"
        # Insomnia/Vital Spirit must be checked HERE, before anything else
        # runs -- if Rest is going to fail because the user can never
        # sleep, it must fail WITHOUT having already cleared an existing
        # poison/burn/paralysis first (Pokemon Showdown's rest.onTry does
        # this ability check before any status is touched, for exactly
        # this reason).
        if await user.consult_first("on_status_immunity", ctx, "sleep"):
            return "But it failed!"
        return None

    async def on_hit(self, ctx: MoveContext, target) -> None:
        from ..status import inflict_status
        user = ctx.user
        user.vitals.status = None  # cure poison/burn/paralysis before trying to sleep
        landed = await inflict_status(ctx, user, "sleep", from_foe=False)
        if not landed:
            if not ctx.result.messages:
                ctx.log("But it failed!")
            return
        user.vitals.status_turns = 3
        user.heal(user.vitals.max_hp)
        ctx.log(f"**{user.name}** went to sleep and became healthy!")

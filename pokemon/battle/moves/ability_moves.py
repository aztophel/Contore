"""Moves that change a Pokemon's ABILITY or TYPE: Entrainment, Role Play, Simple Beam, Skill Swap, Worry Seed, Gastro Acid and Soak. All seven used to be plain
`BasicMove`s that did nothing. Ported from Showdown's move definitions (data/moves.ts) and `Pokemon.setAbility` / `Battle.skillSwap`; which abilities can be
copied, given, swapped or suppressed comes from Showdown's ability flags (`cantsuppress`, `failroleplay`, `noentrain`, `failskillswap`, data/ability_flags.json).

Changing an ability = ending the old one (Showdown's `End` event: only primal-weather abilities need it here) -> `set_ability` (detaches the old effect, attaches the
new) -> starting the new one (Showdown's `Start` event, which for Gen 4+ is our `on_switch_in`), so Entrainment onto Intimidate really does intimidate.
"""
from __future__ import annotations

from ..move import Move
from ..registry import register_move
from ..abilities._util import set_ability, ability_flag


def _pretty(slug: str) -> str:
    return slug.replace("-", " ").title()


async def _end_ability(ctx, owner) -> None:
    """Showdown `singleEvent('End', ability)`: primal-weather abilities clear their weather (they do it through `_leave`)."""
    inst = ctx.engine.abilities.get(owner.identity.ability_slug)
    leave = getattr(inst, "_leave", None)
    if leave is not None:
        await leave(ctx, owner)


async def _start_ability(ctx, owner) -> None:
    """Showdown `singleEvent('Start', ability)`: the new ability's switch-in effect (Intimidate, weather setters, Download...)."""
    inst = ctx.engine.abilities.get(owner.identity.ability_slug)
    start = getattr(inst, "on_switch_in", None)
    if start is not None:
        await start(ctx, owner)


async def _give_ability(ctx, target, new_slug: str, *, announce: bool = True) -> bool:
    """`target.setAbility(new_slug)`: refused if either the current or the new ability can't be suppressed/replaced."""
    old_slug = target.identity.ability_slug
    if _shielded(target) or ability_flag(old_slug, "cantsuppress") or ability_flag(new_slug, "cantsuppress"):
        return False
    await _end_ability(ctx, target)
    set_ability(ctx, target, new_slug)
    if announce:
        ctx.log(f"**{target.name}**'s ability became **{_pretty(new_slug)}**!")
    await _start_ability(ctx, target)
    return True


def _shielded(mon) -> bool:
    """Ability Shield: the holder's ability can't be changed, swapped or suppressed (Showdown `ignoringAbility` / `setAbility` / `skillSwap`)."""
    return mon.effective_item() == "Ability Shield"


def _failed(ctx) -> None:
    ctx.log("But it failed!")


@register_move("entrainment")
class Entrainment(Move):
    """The TARGET gets the user's ability."""
    async def on_hit(self, ctx, target) -> None:
        user = ctx.user
        mine, theirs = user.identity.ability_slug, target.identity.ability_slug
        if (target is user or mine == theirs or ability_flag(theirs, "cantsuppress") or theirs == "truant" or ability_flag(mine, "noentrain")
                or not await _give_ability(ctx, target, mine)):
            _failed(ctx)


@register_move("role-play")
class RolePlay(Move):
    """The USER copies the target's ability."""
    async def on_hit(self, ctx, target) -> None:
        user = ctx.user
        mine, theirs = user.identity.ability_slug, target.identity.ability_slug
        if (mine == theirs or ability_flag(theirs, "failroleplay") or ability_flag(mine, "cantsuppress")
                or not await _give_ability(ctx, user, theirs)):
            _failed(ctx)


@register_move("simple-beam")
class SimpleBeam(Move):
    async def on_hit(self, ctx, target) -> None:
        theirs = target.identity.ability_slug
        if ability_flag(theirs, "cantsuppress") or theirs in ("simple", "truant") or not await _give_ability(ctx, target, "simple"):
            _failed(ctx)


@register_move("worry-seed")
class WorrySeed(Move):
    """Insomnia, which also wakes the target (Truant and Insomnia holders fail before accuracy in Showdown)."""
    async def on_hit(self, ctx, target) -> None:
        theirs = target.identity.ability_slug
        if theirs in ("truant", "insomnia") or ability_flag(theirs, "cantsuppress") or not await _give_ability(ctx, target, "insomnia"):
            _failed(ctx)
            return
        if target.vitals.status == "sleep":
            target.vitals.status = None
            target.vitals.status_turns = 0
            ctx.log(f"**{target.name}** woke up!")


@register_move("skill-swap")
class SkillSwap(Move):
    """User and target trade abilities (`Battle.skillSwap`)."""
    async def on_hit(self, ctx, target) -> None:
        user = ctx.user
        mine, theirs = user.identity.ability_slug, target.identity.ability_slug
        if (target is user or _shielded(user) or _shielded(target)
                or ability_flag(mine, "failskillswap") or ability_flag(theirs, "failskillswap")
                or (ctx.ruleset.number <= 5 and mine == theirs)):            # Showdown `skillSwap`: `if (this.gen <= 5 && sourceAbility.id === targetAbility.id) return false`
            _failed(ctx)
            return
        await _end_ability(ctx, user)
        await _end_ability(ctx, target)
        set_ability(ctx, user, theirs)
        set_ability(ctx, target, mine)
        ctx.log(f"**{user.name}** swapped abilities with **{target.name}**!")
        await _start_ability(ctx, user)
        await _start_ability(ctx, target)


@register_move("gastro-acid")
class GastroAcidMove(Move):
    """Suppresses the target's ability until it leaves the field. The ability id is unchanged (Showdown keeps it); its effect is detached from the bus."""
    async def on_hit(self, ctx, target) -> None:
        from ..volatile import GastroAcid
        slug = target.identity.ability_slug
        if _shielded(target) or ability_flag(slug, "cantsuppress") or target.has(GastroAcid):
            _failed(ctx)
            return
        await _end_ability(ctx, target)
        inst = ctx.engine.abilities.get(slug)
        target.remove(type(inst), ctx.events)
        target.add(GastroAcid(), ctx.events)
        ctx.log(f"**{target.name}**'s ability was suppressed!")


@register_move("soak")
class Soak(Move):
    """The target becomes pure Water type."""
    async def on_hit(self, ctx, target) -> None:
        if target.effective_types() == ["Water"]:
            _failed(ctx)
            return
        target.type_override = ["Water"]
        ctx.log(f"**{target.name}** transformed into the Water type!")


@register_move("destiny-bond")
class DestinyBondMove(Move):
    """Showdown `destinybond`: `onPrepareHit` is `!pokemon.removeVolatile('destinybond')` -- using it while it is already active removes it and FAILS. Otherwise it arms the volatile."""
    async def on_hit(self, ctx, target) -> None:
        from ..volatile import DestinyBond
        user = ctx.user
        if user.has(DestinyBond):
            user.remove(DestinyBond, ctx.events)
            _failed(ctx)
            return
        user.add(DestinyBond(), ctx.events)
        ctx.log(f"**{user.name}** is trying to take its foe down with it!")


@register_move("focus-energy")
class FocusEnergyMove(Move):
    """+2 critical-hit stages (the `FocusEnergy` volatile, which passes with Baton Pass). The volatile existed and the crit code reads it, but nothing
    ever applied it. Fails if it's already active."""
    async def on_hit(self, ctx, target) -> None:
        from ..volatile import FocusEnergy
        user = ctx.user
        if user.has(FocusEnergy):
            _failed(ctx)
            return
        user.add(FocusEnergy(), ctx.events)
        ctx.log(f"**{user.name}** is getting pumped!")

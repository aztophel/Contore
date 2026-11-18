"""Post-damage reactive abilities -- everything that triggers off "I was
just hit" or "I just hit something". Old code: the tail of apply_move's
per-hit resolution, contore.py:18913-19030. All of these hook
on_after_damaged; each implementation checks whether `owner` is the
attacker (`owner is ctx.user`) or the defender (`owner in ctx.targets`)
since the EventBus fires this hook for every registered Effect regardless
of side.
"""
from __future__ import annotations
import random

from ..registry import register_ability
from ..ability import Ability
from ._util import ABILITY_STATUS_IMMUNITY, attacker_breaks_mold, ABILITY_CANT_BE_OVERWRITTEN, set_ability, lose_held_item


def _is_contact(ctx) -> bool:
    from ..move import MoveFlag
    return ctx.move.has_flag(MoveFlag.CONTACT)


def _defender_ability_suppressed(ctx) -> bool:
    return attacker_breaks_mold(ctx)


# -- contact status procs: Static / Flame Body / Poison Point / Effect Spore
_CONTACT_STATUS = {
    "static": ("paralysis", 30),
    "flame-body": ("burn", 30),
    "poison-point": ("poison", 30),
}


def _contact_status_proc(slug: str):
    status, chance = _CONTACT_STATUS[slug]

    @register_ability(slug)
    class _ContactStatusProc(Ability):
        async def on_after_damaged(self, ctx, owner, damage: int) -> None:
            if owner not in ctx.targets or not _is_contact(ctx) or _defender_ability_suppressed(ctx):
                return
            attacker = ctx.user
            if attacker.vitals.status is not None or attacker.fainted or random.randint(1, 100) > chance:
                return
            # through the REAL status pipeline (Showdown `source.trySetStatus(status, target)`): type immunities, Safeguard, Misty Terrain, Leaf Guard, status berries ...
            # This used to write `vitals.status` directly, so a Fire-type could be burned, Safeguard / Misty Terrain did nothing, and no berry ever cured it.
            from ..status import inflict_status
            await inflict_status(ctx, attacker, status, from_foe=True)
    _ContactStatusProc.__name__ = slug.title().replace("-", "") + "Ability"
    return _ContactStatusProc


Static = _contact_status_proc("static")
FlameBody = _contact_status_proc("flame-body")
PoisonPoint = _contact_status_proc("poison-point")


@register_ability("effect-spore")
class EffectSpore(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner not in ctx.targets or not _is_contact(ctx) or _defender_ability_suppressed(ctx):
            return
        attacker = ctx.user
        if attacker.vitals.status is not None or attacker.fainted:
            return
        # a powder: Grass types (Gen 6+), Overcoat and Safety Goggles are immune (Showdown `runStatusImmunity('powder')`)
        if ((ctx.ruleset.number >= 6 and "Grass" in attacker.effective_types()) or attacker.effective_ability() == "overcoat"
                or attacker.effective_item() == "Safety Goggles"):
            return
        # Showdown: r = random(100): < 11 sleep, < 21 paralysis, < 30 poison (so 11% / 10% / 9%, nothing otherwise)
        r = random.randint(0, 99)
        status = "sleep" if r < 11 else "paralysis" if r < 21 else "poison" if r < 30 else None
        if status is None:
            return
        from ..status import inflict_status
        await inflict_status(ctx, attacker, status, from_foe=True)


def _contact_recoil(slug: str, display: str, fraction: int):
    @register_ability(slug)
    class _ContactRecoil(Ability):
        async def on_after_damaged(self, ctx, owner, damage: int) -> None:
            if owner not in ctx.targets or not _is_contact(ctx) or _defender_ability_suppressed(ctx):
                return
            attacker = ctx.user
            if attacker.identity.ability_slug == "magic-guard" or attacker.fainted:
                return
            recoil = max(1, attacker.vitals.max_hp // fraction)
            attacker.vitals.hp = max(0, attacker.vitals.hp - recoil)
            ctx.log(f"Hurt by {display}! (\u2212**{recoil:,}** HP)")
    _ContactRecoil.__name__ = display.replace(" ", "") + "Ability"
    return _ContactRecoil


RoughSkin = _contact_recoil("rough-skin", "Rough Skin", 8)
IronBarbs = _contact_recoil("iron-barbs", "Iron Barbs", 8)


@register_ability("aftermath")
class Aftermath(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner not in ctx.targets or not owner.fainted or not _is_contact(ctx):
            return
        attacker = ctx.user
        if attacker.identity.ability_slug in ("damp", "magic-guard") or attacker.fainted:
            return
        recoil = max(1, attacker.vitals.max_hp // 4)
        attacker.vitals.hp = max(0, attacker.vitals.hp - recoil)
        ctx.log(f"**{owner.name}**'s Aftermath caused **{attacker.name}** to take **{recoil:,}** damage!")


@register_ability("innards-out")
class InnardsOut(Ability):
    """Showdown `onDamagingHit`: if the hit KO'd the holder, the attacker takes damage equal to the HP the holder had just lost (not
    contact-dependent). It's ability damage, so Magic Guard stops it."""
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner not in ctx.targets or not owner.fainted or damage <= 0 or _defender_ability_suppressed(ctx):
            return
        attacker = ctx.user
        if attacker.fainted:
            return
        hurt = await attacker.consult_reduce("on_indirect_damage", ctx, damage, "ability")
        if hurt > 0:
            attacker.vitals.hp = max(0, attacker.vitals.hp - hurt)
            ctx.log(f"**{owner.name}**'s Innards Out hurt **{attacker.name}** for **{hurt:,}** damage!")


@register_ability("cursed-body")
class CursedBody(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner not in ctx.targets or damage <= 0 or _defender_ability_suppressed(ctx):
            return
        from ..volatile import Disable
        attacker = ctx.user
        # Showdown onDamagingHit: not if the attacker is already disabled or the move is Struggle; 30%; NOT contact-dependent.
        if attacker.has(Disable) or ctx.move.slug == "struggle" or random.random() >= 0.3:
            return
        attacker.add(Disable(move_slug=ctx.move.slug), ctx.engine.events)   # the real volatile (legal_moves filters on it, it times out)
        ctx.log(f"**{owner.name}**'s Cursed Body disabled {ctx.move.spec.display_name}!")


@register_ability("mummy")
class Mummy(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner not in ctx.targets or not _is_contact(ctx):
            return
        attacker = ctx.user
        if attacker.identity.ability_slug in (ABILITY_CANT_BE_OVERWRITTEN | {"mummy"}):
            return
        set_ability(ctx, attacker, "mummy")
        ctx.log(f"**{owner.name}**'s Mummy turned **{attacker.name}**'s Ability into Mummy!")


@register_ability("gooey")
class Gooey(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner not in ctx.targets or not _is_contact(ctx):
            return
        attacker = ctx.user
        # Old code: from_foe=True here specifically (contore.py:19008) --
        # unlike the defender-side reactions below, this speed drop lands
        # on the ATTACKER, so it's legitimately foe-caused from the
        # attacker's point of view and the attacker's own Clear Body/Mist
        # can block it.
        msg = await attacker.clamp_stage(ctx, "spe", -1, from_foe=True)
        if msg:
            ctx.log(msg)


@register_ability("cute-charm")
class CuteCharm(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner not in ctx.targets or not _is_contact(ctx):
            return
        attacker = ctx.user
        ag, dg = attacker.identity.gender, owner.identity.gender
        if getattr(attacker, "infatuated", False) or ag == "genderless" or dg == "genderless" or ag == dg:
            return
        if random.random() < 0.30:
            attacker.infatuated = True  # see NOTES_FOR_TRACK_C.md #1 (Infatuation volatile)
            ctx.log(f"**{owner.name}**'s Cute Charm caused infatuation!")


@register_ability("pickpocket")
class Pickpocket(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner not in ctx.targets or not _is_contact(ctx):
            return
        attacker = ctx.user
        if owner.identity.held_item or not attacker.identity.held_item:
            return
        from ..items import is_unstealable
        if is_unstealable(attacker.identity.held_item) or attacker.identity.ability_slug == "sticky-hold":
            return
        stolen = attacker.identity.held_item
        lose_held_item(attacker, ctx, taken=True)
        owner.identity.held_item = stolen
        ctx.engine.sync_ability_and_item(owner)
        ctx.log(f"**{owner.name}**'s Pickpocket stole **{attacker.name}**'s item!")


@register_ability("color-change")
class ColorChange(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        # Showdown `onAfterMoveSecondary`: only if it's still active and doesn't ALREADY have the move's type. The change is TEMPORARY (`type_override`,
        # reset on switch-out) -- this used to overwrite `identity.types`, permanently retyping the Pokemon for the rest of the battle.
        move_type = ctx.move.spec.type_name
        if owner not in ctx.targets or owner.fainted or move_type in owner.effective_types():
            return
        owner.type_override = [move_type]
        ctx.log(f"**{owner.name}** transformed into the **{move_type}** type!")


@register_ability("poison-touch")
class PoisonTouch(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner is not ctx.user or not _is_contact(ctx):
            return
        for target in ctx.targets:
            if target.vitals.status is not None or target.fainted:
                continue
            immune = ABILITY_STATUS_IMMUNITY.get(target.identity.ability_slug, frozenset())
            if "poison" in immune or _defender_ability_suppressed(ctx):
                continue
            if random.random() < 0.30:
                target.vitals.status = "poison"
                ctx.log(f"**{owner.name}**'s Poison Touch poisoned **{target.name}**!")


# -- defender stat reactions to being hit --------------------------------

@register_ability("justified")
class Justified(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner not in ctx.targets or ctx.move.spec.type_name != "Dark":
            return
        # Self-triggered, not "foe-caused" (from_foe defaults to False) --
        # old code never passed from_foe here either (contore.py:18999), so
        # this can't be blocked by the owner's own Clear Body/Mist.
        msg = await owner.clamp_stage(ctx, "atk", 1)
        if msg:
            ctx.log(msg)


@register_ability("rattled")
class Rattled(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner not in ctx.targets or ctx.move.spec.type_name not in ("Bug", "Ghost", "Dark"):
            return
        msg = await owner.clamp_stage(ctx, "spe", 1)
        if msg:
            ctx.log(msg)


@register_ability("weak-armor")
class WeakArmor(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        from ..move import MoveCategory
        if owner not in ctx.targets or ctx.move.category is not MoveCategory.PHYSICAL:
            return
        def_msg = await owner.clamp_stage(ctx, "def", -1)
        spe_msg = await owner.clamp_stage(ctx, "spe", 2 if ctx.ruleset.number >= 7 else 1)   # Showdown: `spe: this.gen >= 7 ? 2 : 1`
        if def_msg:
            ctx.log(def_msg)
        if spe_msg:
            ctx.log(spe_msg)


@register_ability("anger-point")
class AngerPoint(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner not in ctx.targets or not ctx.result.critical or _defender_ability_suppressed(ctx):
            return
        msg = await owner.clamp_stage(ctx, "atk", 6 - owner.stages.get("atk", 0))
        ctx.log(msg if msg else f"**{owner.name}**'s Anger Point maxed its Attack!")


# -- attacker-side reactions after dealing damage -------------------------

@register_ability("moxie")
class Moxie(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner is not ctx.user:
            return
        if any(t.fainted for t in ctx.targets):
            msg = await owner.clamp_stage(ctx, "atk", 1)
            if msg:
                ctx.log(msg)


@register_ability("magician")
class Magician(Ability):
    async def on_after_damaged(self, ctx, owner, damage: int) -> None:
        if owner is not ctx.user or owner.identity.held_item:
            return
        for target in ctx.targets:
            if not target.identity.held_item:
                continue
            from ..items import is_unstealable
            if is_unstealable(target.identity.held_item) or target.identity.ability_slug == "sticky-hold":
                continue
            stolen = target.identity.held_item
            lose_held_item(target, ctx, taken=True)
            owner.identity.held_item = stolen
            ctx.engine.sync_ability_and_item(owner)
            ctx.log(f"**{owner.name}**'s Magician stole **{target.name}**'s item!")
            return

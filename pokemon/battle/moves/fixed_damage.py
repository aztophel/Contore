"""Moves that deal a fixed amount of damage computed from something other
than the normal power/Atk/Def formula entirely -- no crit, no STAB, no
type effectiveness multiplier beyond the plain immunity check (Pokemon
Showdown: these all skip straight to `damageCallback`, bypassing the
usual damage() pipeline's modifiers). Old code: apply_move's per-move
`if move_name == "seismic-toss": dmg = attacker_sp["level"]` -style
branches.

Each of these still needs the ordinary type-immunity check (a Ghost-type
Night Shade fails outright against a Normal-type, same as it would if it
dealt normal damage) since `damageCallback` moves are not exempt from
that -- only from the power-formula math.
"""
from __future__ import annotations

from ..move import Move
from ..context import MoveContext
from ..registry import register_move


async def _check_type_immunity(ctx: MoveContext, move: Move, target) -> bool:
    """Returns True if immune (caller should stop). Shared by every move in this file (they don't go through damage.py's formula, which would do this itself)."""
    from ..types import type_effectiveness
    if type_effectiveness(move.spec.type_name, target.effective_types(), ruleset=ctx.ruleset) == 0.0:
        ctx.log(f"It doesn't affect **{target.name}**!")
        ctx.hit_blocked = True                       # an immune target means the move did NOT land (no Life Orb recoil, no secondaries)
        return True
    immune = await target.consult_first("on_type_immunity", ctx, move.spec.type_name)
    if immune:
        ctx.hit_blocked = True
        return True
    return False


async def apply_fixed_damage(ctx: MoveContext, target, amount: int) -> None:
    """Shared by this file's own moves AND moves/counter.py's Counter / Mirror Coat / Metal Burst and the OHKO moves. Showdown's `getDamage` returns a fixed amount BEFORE `modifyDamage`, so no
    multiplier (Life Orb, Solid Rock, Multiscale, a resist berry) applies -- but everything AFTER the amount is known does: a Substitute takes it, Focus Sash / Sturdy / Focus Band / Endure
    clamp it, and every `on_after_damaged` reaction (contact abilities, Rocky Helmet, Rough Skin, Weakness Policy, Life Orb recoil, Shell Bell ...) runs. This function used to skip all of that."""
    from ..volatile import Substitute
    from ..move import bypasses_substitute_move
    ctx.fixed_damage = True                                  # read by the multiplier hooks (Filter, Multiscale, resist berries) so they stay out of it
    amount = max(1, amount)
    sub_ = target.get(Substitute)
    if sub_ is not None and not bypasses_substitute_move(ctx.move.slug, ctx.ruleset.number) and not await ctx.events.first("on_bypass_substitute", ctx):
        taken = min(amount, sub_.hp)
        sub_.hp = max(0, sub_.hp - taken)
        broke = sub_.hp == 0
        ctx.sub_absorbed = getattr(ctx, "sub_absorbed", 0) + 1
        ctx.hit_substitute = True
        if broke:
            target.remove(Substitute, ctx.events)
        ctx.log(f"The substitute took damage for **{target.name}**!" + (" It broke!" if broke else ""))
        return
    ctx.hit_substitute = False
    amount = int(await ctx.events.reduce("on_modify_damage_taken", ctx, amount))      # the survival clamps (Sturdy, Focus Sash / Band)
    if getattr(target, "endure_this_turn", False) and target.vitals.hp > 0 and amount >= target.vitals.hp:
        amount = target.vitals.hp - 1
        ctx.log(f"**{target.name}** endured the hit!")
    pre_hp = target.vitals.hp
    target.vitals.hp = max(0, target.vitals.hp - amount)
    dealt = min(amount, pre_hp)
    ctx.result.total_damage += dealt
    ctx.log(f"**{target.name}** took **{dealt:,}** damage!")
    if dealt > 0:
        target.last_damage_taken = dealt
        target.last_damage_category = ctx.move.category
        target.last_damage_from = ctx.user
        target.last_damage_turn = ctx.engine.turn_number
    if target.fainted and target not in ctx.result.fainted:
        ctx.result.fainted.append(target)
        ctx.log(f"**{target.name}** fainted!")
    await ctx.events.run("on_after_damaged", ctx, dealt)


@register_move("seismic-toss")
class SeismicTossMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        if await _check_type_immunity(ctx, self, target):
            return
        await apply_fixed_damage(ctx, target, ctx.user.identity.level)


@register_move("night-shade")
class NightShadeMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        if await _check_type_immunity(ctx, self, target):
            return
        await apply_fixed_damage(ctx, target, ctx.user.identity.level)


def _flat_amount_move(slug: str, amount: int):
    @register_move(slug)
    class _FlatAmountMove(Move):
        async def on_hit(self, ctx: MoveContext, target) -> None:
            if await _check_type_immunity(ctx, self, target):
                return
            await apply_fixed_damage(ctx, target, amount)
    _FlatAmountMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _FlatAmountMove


DragonRageMove = _flat_amount_move("dragon-rage", 40)
SonicBoomMove = _flat_amount_move("sonic-boom", 20)


@register_move("super-fang")
class SuperFangMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        if await _check_type_immunity(ctx, self, target):
            return
        await apply_fixed_damage(ctx, target, max(1, target.vitals.hp // 2))


@register_move("natures-madness")
class NaturesMadnessMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        if await _check_type_immunity(ctx, self, target):
            return
        await apply_fixed_damage(ctx, target, max(1, target.vitals.hp // 2))


@register_move("endeavor")
class EndeavorMove(Move):
    """Old code / Pokemon Showdown agree: fails outright unless the
    user's CURRENT HP is strictly less than the target's -- not a type
    immunity, a dedicated onTryImmunity of its own (data/moves.ts's
    endeavor)."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        if await _check_type_immunity(ctx, self, target):
            return
        if ctx.user.vitals.hp >= target.vitals.hp:
            ctx.log("But it failed!")
            return
        await apply_fixed_damage(ctx, target, target.vitals.hp - ctx.user.vitals.hp)


@register_move("final-gambit")
class FinalGambitMove(Move):
    """Deals damage equal to the user's current HP, then the user faints. Showdown's `selfdestruct: "ifHit"`: the user only faints when the move actually HITS -- an immune target (Fighting vs Ghost)
    means no damage and no self-KO (a miss / Protect never reaches here at all)."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        amount = ctx.user.vitals.hp
        if await _check_type_immunity(ctx, self, target):
            return
        ctx.user.vitals.hp = 0
        ctx.log(f"**{ctx.user.name}** fainted!")
        await apply_fixed_damage(ctx, target, amount)


@register_move("pain-split")
class PainSplitMove(Move):
    async def on_hit(self, ctx: MoveContext, target) -> None:
        user = ctx.user
        average = max(1, (target.vitals.hp + user.vitals.hp) // 2)
        target.vitals.hp = min(target.vitals.max_hp, average)
        user.vitals.hp = min(user.vitals.max_hp, average)
        ctx.log(f"**{user.name}** shared its pain with **{target.name}**!")

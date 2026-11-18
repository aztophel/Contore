"""Status moves whose entire effect is inflicting a guaranteed major
status (paralysis/burn/poison/sleep) or confusion. These are all
`ignoreImmunity: true` in Pokemon Showdown's data (the default for any
Status-category move) -- meaning NONE of them check the ordinary move-
type-vs-target-type chart, unlike Thunder Wave (moves/examples.py), which
is one of the rare exceptions with `ignoreImmunity: false` explicitly
set. What they DO still need:
  - status.py's inflict_status handles type-based status immunity
    (Electric/paralysis, Fire/burn, Poison+Steel/poison, Ice/freeze) and
    ability-based immunity (on_status_immunity) uniformly on its own.
  - The four powder moves ALSO get Grass-type/Overcoat/Safety Goggles
    immunity, but that's handled generically by engine.py's
    _execute_move for any move carrying MoveFlag.POWDER -- nothing
    move-specific needed here beyond setting the flag on the MoveSpec.
  - Confusion has no type-based or ability-based immunity of its own in
    this bot's scope (Own Tempo is the only relevant ability and already
    answers on_status_immunity... except Confusion isn't a `status` at
    all, it's a Volatile -- see the _ConfusionMove note below).
"""
from __future__ import annotations
import random
from typing import TYPE_CHECKING

from ..move import Move, MoveCategory
from ..context import MoveContext
from ..registry import register_move
from ..volatile import Confusion

if TYPE_CHECKING:
    from ..battler import Battler


def _status_ailment_move(slug: str, status: str):
    @register_move(slug)
    class _StatusAilmentMove(Move):
        async def on_hit(self, ctx: MoveContext, target: "Battler") -> None:
            from ..status import inflict_status
            landed = await inflict_status(ctx, target, status, from_foe=target is not ctx.user)
            if not landed and not ctx.result.messages:
                ctx.log("But it failed!")
    _StatusAilmentMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _StatusAilmentMove


PoisonPowderMove = _status_ailment_move("poison-powder", "poison")
StunSporeMove = _status_ailment_move("stun-spore", "paralysis")
SleepPowderMove = _status_ailment_move("sleep-powder", "sleep")
SporeMove = _status_ailment_move("spore", "sleep")
class GlareMove(_status_ailment_move("glare", "paralysis")):
    """Showdown gen3 `glare: ignoreImmunity: false`: in Gen 3 a Ghost type is immune to it (Normal-type); from Gen 4 it paralyzes Ghosts."""
    async def on_hit(self, ctx: MoveContext, target: "Battler") -> None:
        if ctx.ruleset.number == 3 and "Ghost" in target.effective_types():
            ctx.log(f"It doesn't affect **{target.name}**...")
            return
        await super().on_hit(ctx, target)


register_move("glare")(GlareMove)
WillOWispMove = _status_ailment_move("will-o-wisp", "burn")
# These were plain BasicMoves, and BasicMove only applies an ailment through a % chance (0 for a status move), so they did NOTHING at all.
SingMove = _status_ailment_move("sing", "sleep")
HypnosisMove = _status_ailment_move("hypnosis", "sleep")
LovelyKissMove = _status_ailment_move("lovely-kiss", "sleep")
GrassWhistleMove = _status_ailment_move("grass-whistle", "sleep")
PoisonGasMove = _status_ailment_move("poison-gas", "poison")


@register_move("dark-void")
class DarkVoidMove(Move):
    """Sleep. From Gen 7 only a Darkrai can use it (Showdown `onTry`: `source.species.baseSpecies === 'Darkrai'`)."""
    async def on_try_use(self, ctx: MoveContext):
        if ctx.ruleset.number >= 7 and not ctx.user.identity.species_name.lower().startswith("darkrai"):
            return "But it failed!"
        return None

    async def on_hit(self, ctx: MoveContext, target: "Battler") -> None:
        from ..status import inflict_status
        landed = await inflict_status(ctx, target, "sleep", from_foe=target is not ctx.user)
        if not landed and not ctx.result.messages:
            ctx.log("But it failed!")


@register_move("toxic")
class ToxicMove(Move):
    """Badly poisons (a separate status from plain poison -- see
    status.py's docstring on why Toxic isn't just poison-with-a-counter).
    NOT given Poison-type users' Gen 8+ always-hit treatment (Pokemon
    Showdown: sim/battle-actions.ts's `this.battle.gen >= 8 &&
    pokemon.hasType('Poison')` -- explicitly gated to Gen 8+, so Gen 6/7
    Toxic uses its plain listed accuracy no matter who uses it). Once
    this bot's ruleset actually varies per-server (see ARCHITECTURE.md),
    this becomes `if ctx.ruleset.number >= 8 and "Poison" in
    ctx.user.effective_types(): always hits`."""
    async def on_hit(self, ctx: MoveContext, target: "Battler") -> None:
        from ..status import inflict_status
        landed = await inflict_status(ctx, target, "bad_poison", from_foe=target is not ctx.user)
        if not landed and not ctx.result.messages:
            ctx.log("But it failed!")


async def apply_confusion(ctx: MoveContext, target: "Battler", *, announce_failure: bool = True, from_foe: bool = True) -> bool:
    """Make `target` confused (Showdown `volatileStatus: 'confusion'`): not if it already is, Own Tempo / status immunity, Misty Terrain (Gen 7+), or Safeguard (a foe's confusion).
    Shared by the confusion STATUS moves and by every damaging move with a confusion secondary (Psybeam, Hurricane, Water Pulse, Dizzy Punch ...)."""
    import random
    if target.has(Confusion):
        if announce_failure:
            ctx.log("But it failed!")
        return False
    immune = await target.consult_first("on_status_immunity", ctx, "confusion")
    if immune:
        return False                              # Own Tempo already logged its own message
    rules = ctx.field.terrain_rules
    blocked = rules.blocks_volatile(target, "confusion", ctx.field, ctx.ruleset.number) if rules else None
    if blocked:                                   # Misty Terrain (Gen 7+)
        ctx.log(blocked)
        return False
    from ..side_conditions import Safeguard
    if from_foe and target is not ctx.user and ctx.field.side_of(target).get(Safeguard) is not None and not await ctx.events.first("on_bypass_substitute", ctx):
        ctx.log(f"**{target.name}** is protected by Safeguard!")
        return False
    target.add(Confusion(turns_remaining=random.randint(2, 5)), ctx.battle.engine.events)
    ctx.log(f"**{target.name}** became confused!")
    return True


def _confusion_move(slug: str, *, target_boost: tuple[str, int] | None = None):
    """Confusion isn't a Vitals.status value -- it's the Confusion
    Volatile (see volatile.py) -- so it can't go through the same
    _status_ailment_move factory or inflict_status at all. `target_boost`
    covers Swagger (Attack +2) and Flatter (Sp. Atk +1), which land their
    stat change on the TARGET regardless of BasicMove's usual "positive
    change on a non-status move buffs the user" heuristic, since these
    ARE status moves boosting the *opponent* -- an intentional exception
    a plain MoveSpec.stat_changes entry can't express here since these
    aren't BasicMove subclasses.

    Ordering matters and is independent, not gated on each other (Pokemon
    Showdown: sim/battle-actions.ts's moveHit -- `moveData.boosts` is
    applied unconditionally before `moveData.volatileStatus` is even
    attempted): Swagger on an already-confused target (or one with Own
    Tempo) still raises its Attack, it just doesn't ALSO re-confuse it or
    print a second confusion message. An earlier draft checked
    already-confused FIRST and skipped the entire move, including the
    boost, if so -- wrong on both counts."""
    @register_move(slug)
    class _ConfusionMove(Move):
        async def on_hit(self, ctx: MoveContext, target: "Battler") -> None:
            if target_boost:
                from ..stats import apply_stat_change
                await apply_stat_change(ctx, target, *target_boost)
            applied = await apply_confusion(ctx, target, announce_failure=not target_boost)
    _ConfusionMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _ConfusionMove


ConfuseRayMove = _confusion_move("confuse-ray")
SupersonicMove = _confusion_move("supersonic")
SweetKissMove = _confusion_move("sweet-kiss")
SwaggerMove = _confusion_move("swagger", target_boost=("attack", 2))
FlatterMove = _confusion_move("flatter", target_boost=("special-attack", 1))
TeeterDanceMove = _confusion_move("teeter-dance")

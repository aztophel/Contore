"""Moves that CALL another move: Metronome, Nature Power, Sleep Talk, Copycat, Mirror Move. Ported from Showdown's data/moves.ts and its per-generation mods; the called move goes
through `engine._use_called_move` (same pipeline as a chosen move, no PP / `_can_act`). Per-generation flag lists (Metronome's pool in Showdown's order, `nosleeptalk`, `failcopycat`,
`charge`, `mirror`) are data/call_move_data.json (tests/tools/make_call_move_data.js).

NOT ported: Assist, Me First, Instruct, Snatch (still unimplemented); Gen 3-4 Mirror Move (`lastAttackedBy`); Heal Block's effect on Metronome / Copycat in Gens 3-4 (no Heal Block volatile exists).
"""
from __future__ import annotations
import json
import random
from pathlib import Path

from ..move import Move
from ..context import MoveContext
from ..registry import register_move

_DATA: dict | None = None


def _data(gen: int) -> dict:
    global _DATA
    if _DATA is None:
        raw = json.loads((Path(__file__).resolve().parents[3] / "data" / "call_move_data.json").read_text(encoding="utf-8"))
        _DATA = {int(g): {k: (v if k in ("metronome_pool", "type_order") else frozenset(v)) for k, v in d.items()} for g, d in raw.items()}
    return _DATA[max(3, min(9, gen))]


def _failed(ctx) -> None:
    ctx.log("But it failed!")


async def _call(ctx: MoveContext, slug: str, targets=()) -> None:
    ctx.called_move_used = True                  # Showdown's `lastMove` ends up as the CALLED move (engine._resolve_move skips recording the caller)
    await ctx.engine._use_called_move(ctx, ctx.user, slug, targets)


@register_move("metronome")
class MetronomeMove(Move):
    async def get_targets(self, ctx: MoveContext) -> list:
        return [ctx.user]

    async def on_hit(self, ctx: MoveContext, target) -> None:
        gen = ctx.ruleset.number
        pool = list(_data(gen)["metronome_pool"])
        if gen in (3, 4):                        # Showdown's Gen 4 mod (which Gen 3 inherits): not Gravity-banned moves; (Gen 4 only) not a move the user already knows
            d = _data(gen)
            known = {s.move_slug for s in ctx.user.moves.slots} if gen == 4 else set()
            pool = [m for m in pool if m not in known and not (ctx.field.gravity and m in d["gravity"])]
        if not pool:
            _failed(ctx); return
        await _call(ctx, random.choice(pool))


@register_move("nature-power")
class NaturePowerMove(Move):
    async def get_targets(self, ctx: MoveContext) -> list:
        if ctx.ruleset.number <= 5:              # Gens 3-5: `target: "self"`, the move it calls picks its own target
            return [ctx.user]
        return await super().get_targets(ctx)

    async def on_hit(self, ctx: MoveContext, target) -> None:
        gen = ctx.ruleset.number
        if gen <= 5:
            slug = {3: "swift", 4: "tri-attack", 5: "earthquake"}[gen]
            await _call(ctx, slug)
            return
        slug = {"electric": "thunderbolt", "grassy": "energy-ball", "misty": "moonblast", "psychic": "psychic"}.get(ctx.field.terrain, "tri-attack")
        await _call(ctx, slug, [target])


@register_move("sleep-talk")
class SleepTalkMove(Move):
    def can_use_while_asleep(self) -> bool:
        return True

    async def get_targets(self, ctx: MoveContext) -> list:
        return [ctx.user]

    async def on_try_use(self, ctx: MoveContext):
        user = ctx.user
        if user.vitals.status != "sleep" and user.effective_ability() != "comatose":
            return "But it failed!"
        if ctx.ruleset.number == 4:              # Showdown's Gen 4 `onTryHit`: not while choice-locked or Encored
            from ..volatile import Encore
            if user.has(Encore):                 # (a Choice-locked user also fails there; Choice locking isn't modelled that way)
                return "But it failed!"
        return None

    async def on_hit(self, ctx: MoveContext, target) -> None:
        d = _data(ctx.ruleset.number)
        slots = [s for s in ctx.user.moves.slots if s.move_slug and s.move_slug not in d["nosleeptalk"] and s.move_slug not in d["charge"]]
        if not slots:
            _failed(ctx); return
        chosen = random.choice(slots)
        if ctx.ruleset.number == 3 and chosen.pp <= 0:      # Showdown's Gen 3 Sleep Talk can pick a move with no PP left, and then does nothing
            ctx.log(f"**{ctx.user.name}** has no PP left for **{ctx.engine.moves.spec(chosen.move_slug).display_name}**!")
            return
        await _call(ctx, chosen.move_slug)


@register_move("copycat")
class CopycatMove(Move):
    async def get_targets(self, ctx: MoveContext) -> list:
        return [ctx.user]

    async def on_hit(self, ctx: MoveContext, target) -> None:
        last = ctx.engine.last_move_global
        if last is None:
            return                                # Showdown: `if (!move) return;` (neither success text nor failure)
        d = _data(ctx.ruleset.number)
        if last in d["failcopycat"] or (ctx.ruleset.number == 4 and ctx.field.gravity and last in d["gravity"]):
            _failed(ctx); return
        await _call(ctx, last)


# Showdown `data/mods/gen3|gen4/moves.ts` `mirrormove.onHit`: moves a Gen 3-4 Mirror Move can't copy (ids, no dashes).
_NO_MIRROR_GEN3 = frozenset((
    "assist curse doomdesire focuspunch futuresight magiccoat metronome mimic mirrormove naturepower psychup roleplay sketch sleeptalk spikes spitup taunt teeterdance transform").split())
_NO_MIRROR_GEN4 = frozenset((
    "acupressure aromatherapy assist chatter copycat counter curse doomdesire feint focuspunch futuresight gravity hail haze healbell helpinghand lightscreen luckychant magiccoat mefirst "
    "metronome mimic mirrorcoat mirrormove mist mudsport naturepower perishsong psychup raindance reflect roleplay safeguard sandstorm sketch sleeptalk snatch spikes spitup stealthrock "
    "struggle sunnyday tailwind toxicspikes transform watersport").split())


@register_move("mirror-move")
class MirrorMoveMove(Move):
    """Gens 5-8 (Showdown's base `onTryHit`): uses the TARGET's last move if it can be mirrored. Gens 3-4 (`target: self`): copies the move that last HIT the user (`getLastAttackedBy`)
    if its attacker still knows it and has used a move since switching in, and it isn't on the generation's no-mirror list."""
    async def on_hit(self, ctx: MoveContext, target) -> None:
        if ctx.ruleset.number <= 4:
            hits = getattr(ctx.user, "attacked_by", None)
            if not hits:
                _failed(ctx); return
            source, slug = hits[-1]
            no_mirror = _NO_MIRROR_GEN3 if ctx.ruleset.number == 3 else _NO_MIRROR_GEN4
            if (not getattr(source, "last_move_slug", None) or slug.replace("-", "") in no_mirror
                    or slug not in {s.move_slug for s in source.moves.slots}):
                _failed(ctx); return
            await _call(ctx, slug)
            return
        last = getattr(target, "last_move_slug", None)
        if not last or last not in _data(ctx.ruleset.number)["mirror"]:
            _failed(ctx); return
        await _call(ctx, last, [target])

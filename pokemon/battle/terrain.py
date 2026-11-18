"""Terrain behaviour, one class per terrain, ported from Pokemon Showdown's `condition` blocks of Electric / Grassy /
Misty / Psychic Terrain (data/moves.ts, with the Gen 6/7 overrides in data/mods/gen7/moves.ts and gen6/moves.ts).

The `Terrain` COMPONENT on the Field (abilities/field_effects.py) stays plain data (`kind`, `turns_remaining`); the
rules live here and the engine only DISPATCHES to them (`Field.terrain_rules`) at the same five moments Showdown's
condition handlers fire:

  * `base_power_modifier`   <- `onBasePower`          (damage.py; boosts AND weakening)
  * `on_residual`           <- `onResidual`           (engine end of turn; Grassy heal)
  * `blocks_status`         <- `onSetStatus`          (status.py)
  * `blocks_volatile`       <- `onTryAddVolatile`     (Yawn / confusion)
  * `blocks_priority_move`  <- `onTryHit`             (engine move resolution; Psychic Terrain)

Everything keys off GROUNDEDNESS and SEMI-INVULNERABILITY of the right Pokemon -- the ATTACKER for boosts, the
DEFENDER / TARGET for weakening and blocks -- exactly as Showdown does. Terrain is NOT affected by Cloud Nine / Air Lock.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from .move import MoveTarget

if TYPE_CHECKING:
    from .battler import Battler
    from .context import BattleContext
    from .field import Field

NEUTRAL = 4096


def _affected(mon: "Battler", field: "Field", *, ignore_ability: bool = False) -> bool:
    """`mon.isGrounded() && !mon.isSemiInvulnerable()`: the test every terrain handler repeats."""
    return mon.is_grounded(field=field, ignore_ability=ignore_ability) and not mon.is_semi_invulnerable()


class TerrainRules:
    kind: str = ""
    display: str = ""

    @staticmethod
    def boost_modifier(gen: int) -> int:
        """`chainModify(1.5)` in Showdown's gen7 mod (so Gens 6-7), `chainModify([5325, 4096])` from Gen 8."""
        return 6144 if gen <= 7 else 5325

    def base_power_modifier(self, attacker: "Battler", defender: "Battler", move_type: str, move_slug: str,
                            field: "Field", gen: int, *, defender_ignores_ability: bool = False) -> int:
        """4096ths multiplier for this hit's base power (4096 = no change)."""
        return NEUTRAL

    async def on_residual(self, ctx: "BattleContext", battler: "Battler") -> None: ...

    def blocks_status(self, target: "Battler", status: str, field: "Field") -> str | None:
        """A log line if this terrain stops `status` landing on `target`, else None."""
        return None

    def blocks_volatile(self, target: "Battler", volatile: str, field: "Field", gen: int) -> str | None:
        """A log line if this terrain stops the volatile ("yawn" / "confusion") being added, else None."""
        return None

    def blocks_priority_move(self, user: "Battler", target: "Battler", spec, field: "Field") -> bool:
        return False


class ElectricTerrain(TerrainRules):
    kind, display = "electric", "Electric Terrain"

    def base_power_modifier(self, attacker, defender, move_type, move_slug, field, gen, *, defender_ignores_ability=False):
        if move_type == "Electric" and _affected(attacker, field):
            return self.boost_modifier(gen)
        return NEUTRAL

    def blocks_status(self, target, status, field):
        if status == "sleep" and _affected(target, field):
            return "Electric Terrain prevented sleep!"
        return None

    def blocks_volatile(self, target, volatile, field, gen):
        if volatile == "yawn" and _affected(target, field):
            return f"**{target.name}** is protected by Electric Terrain!"
        return None


class GrassyTerrain(TerrainRules):
    kind, display = "grassy", "Grassy Terrain"
    WEAKENED = frozenset({"earthquake", "bulldoze", "magnitude"})

    def base_power_modifier(self, attacker, defender, move_type, move_slug, field, gen, *, defender_ignores_ability=False):
        if move_slug in self.WEAKENED and _affected(defender, field, ignore_ability=defender_ignores_ability):
            return 2048                                            # chainModify(0.5)
        if move_type == "Grass" and _affected(attacker, field):
            return self.boost_modifier(gen)
        return NEUTRAL

    async def on_residual(self, ctx, battler):
        # `this.heal(pokemon.baseMaxhp / 16, ...)` -- the heal truncates; a full-HP Pokemon heals nothing and logs nothing.
        if battler.fainted or not _affected(battler, ctx.field) or battler.is_semi_invulnerable():       # Showdown: `isGrounded() && !isSemiInvulnerable()`
            return
        healed = battler.heal(max(1, battler.vitals.max_hp // 16))
        if healed:
            ctx.engine.log.append(f"**{battler.name}**'s HP was restored by Grassy Terrain!")


class MistyTerrain(TerrainRules):
    kind, display = "misty", "Misty Terrain"

    def base_power_modifier(self, attacker, defender, move_type, move_slug, field, gen, *, defender_ignores_ability=False):
        if move_type == "Dragon" and _affected(defender, field, ignore_ability=defender_ignores_ability):
            return 2048
        return NEUTRAL

    def blocks_status(self, target, status, field):
        if _affected(target, field):
            return "Misty Terrain blocked the status condition!"
        return None

    def blocks_volatile(self, target, volatile, field, gen):
        # gen6/moves.ts removes `onTryAddVolatile` (no confusion block in Gen 6); Yawn is stopped later, by `onSetStatus`.
        if volatile == "confusion" and gen >= 7 and _affected(target, field):
            return f"**{target.name}** surrounds itself with a protective mist!"
        return None


class PsychicTerrain(TerrainRules):
    kind, display = "psychic", "Psychic Terrain"

    def base_power_modifier(self, attacker, defender, move_type, move_slug, field, gen, *, defender_ignores_ability=False):
        if move_type == "Psychic" and _affected(attacker, field):
            return self.boost_modifier(gen)
        return NEUTRAL

    def blocks_priority_move(self, user, target, spec, field):
        # onTryHit: `effect.priority <= 0.1 || effect.target === 'self'` -> not blocked; semi-invulnerable or ALLY
        # targets are skipped; airborne targets are unaffected. (Queue-only priority changes such as Prankster /
        # Gale Wings don't count: Showdown reads the move's own `priority`.)
        if spec.priority <= 0 or spec.target is MoveTarget.USER:
            return False
        if target.is_semi_invulnerable() or target.side is user.side:   # `target.isAlly(source)` (a Pokemon is its own ally)
            return False
        return target.is_grounded(field=field)


_RULES: dict[str, TerrainRules] = {r.kind: r for r in (ElectricTerrain(), GrassyTerrain(), MistyTerrain(), PsychicTerrain())}


def rules_for(kind: str | None) -> TerrainRules | None:
    return _RULES.get(kind) if kind else None

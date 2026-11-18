"""Two-turn charge moves. Old code: apply_move's per-move charge-turn
branches, each hand-tracking a `charging_move`/`invulnerable` pair of
fields on FighterState. Here it's one shared factory plus the Charging
volatile (volatile.py) that grants (or, for Solar Beam's family, doesn't
grant) semi-invulnerability while it's attached.

The engine forces the SAME move to run again on the release turn
regardless of what's actually submitted, and skips PP for that release
turn -- see BattleEngine._execute_move's handling of the Charging
volatile. This file only needs to decide, each time on_try_use runs,
whether THIS is the charge turn (add Charging, show the message, don't
attack) or the release turn (remove Charging, attack normally) -- which
is exactly the same "check for and remove my own volatile" shape Pokemon
Showdown's onTryMove uses for this whole family.
"""
from __future__ import annotations

from ..move import BasicMove
from ..context import MoveContext
from ..registry import register_move
from ..volatile import Charging


def _charge_move(slug: str, charge_message: str, *, weather_skip: str | None = None, charge_boost: tuple[str, int] | None = None):
    """`weather_skip`: a Field.weather value that lets the move skip
    charging entirely and attack the same turn it's used -- Solar Beam in
    sun being the only case in this bot's current move set (Pokemon
    Showdown's data/moves.ts: `if (['sunnyday', 'desolateland'].includes(
    ...))`; this bot doesn't have a separate Desolate Land weather kind,
    only "sun", so just that one string)."""
    @register_move(slug)
    class _ChargeMove(BasicMove):
        async def on_try_use(self, ctx: MoveContext) -> str | None:
            user = ctx.user
            existing = user.get(Charging)
            if existing is not None and existing.move_slug == slug:
                user.remove(Charging, ctx.battle.engine.events)
                return None  # release turn: attack normally

            if charge_boost is not None:               # Skull Bash: +1 Defense on the charge turn, Power Herb or not (Showdown boosts BEFORE the `ChargeMove` event)
                from ..stats import apply_stat_change
                await apply_stat_change(ctx, user, *charge_boost)

            if user.effective_item() == "Power Herb":
                from ..abilities._util import lose_held_item
                lose_held_item(user, ctx)
                return None  # Power Herb: skip the charge turn entirely

            if weather_skip is not None:
                from ..abilities._util import weather_suppressed
                if ctx.field.weather == weather_skip and not weather_suppressed(ctx.field):
                    return None  # e.g. Solar Beam in real sunlight: no charge needed

            user.add(Charging(move_slug=slug), ctx.battle.engine.events)
            return charge_message.format(name=user.name)
    _ChargeMove.__name__ = slug.replace("-", "_").title().replace("_", "") + "Move"
    return _ChargeMove


FlyMove = _charge_move("fly", "**{name}** flew up high!")
DigMove = _charge_move("dig", "**{name}** dug a hole!")
DiveMove = _charge_move("dive", "**{name}** hid underwater!")
BounceMove = _charge_move("bounce", "**{name}** sprang up!")
PhantomForceMove = _charge_move("phantom-force", "**{name}** vanished instantly!")           # these two were plain attacks: no charge turn, no invulnerability
ShadowForceMove = _charge_move("shadow-force", "**{name}** vanished instantly!")
RazorWindMove = _charge_move("razor-wind", "**{name}** whipped up a whirlwind!")
SkyAttackMove = _charge_move("sky-attack", "**{name}** is glowing!")
SolarBeamMove = _charge_move("solar-beam", "**{name}** took in sunlight!", weather_skip="sun")
SkullBashMove = _charge_move("skull-bash", "**{name}** tucked in its head!", charge_boost=("def", 1))
FreezeShockMove = _charge_move("freeze-shock", "**{name}** became cloaked in a freezing light!")
IceBurnMove = _charge_move("ice-burn", "**{name}** became cloaked in freezing air!")
GeomancyMove = _charge_move("geomancy", "**{name}** is absorbing power!")                    # a two-turn STATUS move (Power Herb skips the charge)

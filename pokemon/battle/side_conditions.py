"""Screens and entry hazards. Owned by Track C.

Both are inherently per-Side, not per-Pokemon (a whole team shares one
Stealth Rock) and not global (the *other* side's hazards don't affect you)
-- so they're Effect components attached to a `Side` entity rather than to
individual Battlers or to the Field. Screens self-tick via the normal
on_end_turn Effect dispatch (see ecs/events.py); hazards have no duration
at all (they persist until Rapid Spin/Defog removes them), so they carry no
timer and implement no hooks -- they're pure data, read directly by
apply_entry_hazards() below when a new Battler takes the field.

Old code: the four `field.stealth_rock/spikes/toxic_spikes/sticky_web`
lists plus `field.reflect/light_screen/aurora_veil/tailwind/safeguard/
mist/lucky_chant` lists on `class BattleField`, `BattleField.set_screen()`,
`BattleField.screen_mult()`, and the free function `_apply_entry_hazards()`.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..ecs.component import Effect
from .types import type_effectiveness

if TYPE_CHECKING:
    from .battler import Battler
    from .context import BattleContext
    from .field import Side

_SCREEN_EXPIRE_MSG = {
    "reflect": "Reflect wore off!",
    "light_screen": "Light Screen wore off!",
    "aurora_veil": "Aurora Veil wore off!",
    "tailwind": "The tailwind faded!",
    "safeguard": "Safeguard wore off!",
    "mist": "Mist faded!",
    "lucky_chant": "Lucky Chant wore off!",
}


@dataclass
class _TimedScreen(Effect):
    """Shared base for every duration-based side condition. Subclasses just
    set `kind` (used for the wear-off message) and their constructor's
    default `turns_remaining`."""
    kind: str = ""
    turns_remaining: int = 5

    async def on_end_turn(self, ctx: "BattleContext", owner: "Side") -> None:
        self.turns_remaining -= 1
        if self.turns_remaining <= 0:
            owner.remove(type(self), ctx.engine.events if hasattr(ctx, "engine") else None)
            ctx.log(_SCREEN_EXPIRE_MSG.get(self.kind, f"{self.kind.replace('_', ' ').title()} wore off!"))


@dataclass
class Reflect(_TimedScreen):
    kind: str = "reflect"


@dataclass
class LightScreen(_TimedScreen):
    kind: str = "light_screen"


@dataclass
class AuroraVeil(_TimedScreen):
    kind: str = "aurora_veil"


@dataclass
class Tailwind(_TimedScreen):
    kind: str = "tailwind"
    turns_remaining: int = 4


@dataclass
class Safeguard(_TimedScreen):
    kind: str = "safeguard"


@dataclass
class Mist(_TimedScreen):
    kind: str = "mist"


@dataclass
class LuckyChant(_TimedScreen):
    kind: str = "lucky_chant"


# -- entry hazards: no duration, no hooks -- pure data ----------------------
@dataclass
class StealthRock(Effect):
    pass


@dataclass
class Spikes(Effect):
    layers: int = 1  # 1-3


@dataclass
class ToxicSpikes(Effect):
    layers: int = 1  # 1-2


@dataclass
class StickyWeb(Effect):
    pass


_SCREEN_TYPES: dict[str, type[_TimedScreen]] = {
    "reflect": Reflect, "light_screen": LightScreen, "aurora_veil": AuroraVeil,
    "tailwind": Tailwind, "safeguard": Safeguard, "mist": Mist, "lucky_chant": LuckyChant,
}

_SCREEN_SET_MSG = {
    "reflect": "A wall of light was put up to reduce damage!",
    "light_screen": "A mystical veil was created!",
    "aurora_veil": "Aurora Veil reduced the damage received by the ally Pokémon!",
    "tailwind": "A Tailwind started to blow!",
    "safeguard": "The team became cloaked in a mystical veil!",
    "mist": "The ally team became shrouded in mist!",
    "lucky_chant": "The ally team became shielded from critical hits!",
}


def set_screen(side: "Side", screen_name: str, turns: int | None = None, bus=None) -> str:
    """Old code: BattleField.set_screen(). Returns the log message; "But it
    failed!" if that screen is already up."""
    cls = _SCREEN_TYPES[screen_name]
    if side.get(cls) is not None:
        return "But it failed!"
    kwargs = {} if turns is None else {"turns_remaining": turns}
    side.add(cls(**kwargs), bus)
    return _SCREEN_SET_MSG.get(screen_name, f"{screen_name.replace('_', ' ').title()} activated!")


def screen_mult(side: "Side", category: str, *, is_crit: bool = False, bypasses_screens: bool = False) -> float:
    """Old code: BattleField.screen_mult(). `side` is the DEFENDER's side.
    Crits and Infiltrator both bypass screens entirely, same as the
    monolith's inline `if field and not is_crit and not infiltrator:` guard
    at the damage-calc call site -- callers should still check those
    themselves before calling this (kept as explicit kwargs here rather
    than silently no-op'ing so the damage calculator's intent stays
    visible at the call site)."""
    if is_crit or bypasses_screens:
        return 1.0
    if side.get(AuroraVeil) is not None:
        return 0.5
    if category == "physical" and side.get(Reflect) is not None:
        return 0.5
    if category == "special" and side.get(LightScreen) is not None:
        return 0.5
    return 1.0


# -- entry hazards ------------------------------------------------------
async def apply_entry_hazards(ctx: "BattleContext", battler: "Battler", side: "Side") -> list[str]:
    """Old code: `_apply_entry_hazards()`. Called by engine.py right after
    a Battler takes an active slot (switch-in, initial send-out, Baton
    Pass, ...). `side` is the Battler's OWN side (hazards are set on the
    opponent's side by the move that placed them, same as the real games:
    Stealth Rock on your side hurts the Pokemon YOU send out)."""
    msgs: list[str] = []
    ability = battler.effective_ability()
    immune_indirect = ability == "magic-guard"
    # Heavy-Duty Boots skip the DAMAGE / status / speed drop of every hazard -- but a grounded Poison type still ABSORBS Toxic Spikes (Showdown `toxicspikes.onSwitchIn`). Showdown's Gen 3-4
    # hazard definitions don't know the item at all; Gens 5+ share the one that checks it.
    has_boots = battler.effective_item() == "Heavy-Duty Boots"
    boots = has_boots                                                  # Stealth Rock / Spikes / Sticky Web: every generation
    boots_tox = ctx.ruleset.number >= 5 and has_boots                  # Toxic Spikes: Gen 5+ (the gen4 mod's version ignores the item)

    types = battler.effective_types()
    name = f"**{battler.name}**"

    if side.get(StealthRock) is not None and not immune_indirect and not boots:
        eff = type_effectiveness("Rock", types)
        frac = eff / 8.0
        dmg = max(1, int(battler.vitals.max_hp * frac))
        battler.vitals.hp = max(0, battler.vitals.hp - dmg)
        msgs.append(f"Pointed stones dug into {name}! (\u2212**{dmg:,}** HP)")

    grounded = battler.is_grounded(types, ctx.field if hasattr(ctx, "field") else None)

    spikes = side.get(Spikes)
    if spikes is not None and spikes.layers > 0 and grounded and not immune_indirect and not boots:
        frac = {1: 1/8, 2: 1/6, 3: 1/4}.get(spikes.layers, 1/8)
        dmg = max(1, int(battler.vitals.max_hp * frac))
        battler.vitals.hp = max(0, battler.vitals.hp - dmg)
        msgs.append(f"{name} was hurt by the spikes! (\u2212**{dmg:,}** HP)")

    t_spikes = side.get(ToxicSpikes)
    if t_spikes is not None and t_spikes.layers > 0 and grounded:
        if "Steel" not in types and "Poison" not in types:
            if battler.vitals.status is None and not boots_tox:
                immune_set = await battler.consult_first("on_status_immunity", ctx, "poison")
                if not immune_set:
                    if t_spikes.layers >= 2:
                        battler.vitals.status = "bad_poison"
                        msgs.append(f"{name} was badly poisoned by the Toxic Spikes!")
                    else:
                        battler.vitals.status = "poison"
                        msgs.append(f"{name} was poisoned by the Toxic Spikes!")
        elif "Poison" in types:
            side.remove(ToxicSpikes)
            msgs.append(f"Toxic Spikes were absorbed by {name}!")

    web = side.get(StickyWeb)
    if web is not None and grounded and not boots:
        msgs.append(await battler.clamp_stage(ctx, "spe", -1, from_foe=True))
        msgs.append(f"Sticky Web slowed {name}!")

    return [m for m in msgs if m]

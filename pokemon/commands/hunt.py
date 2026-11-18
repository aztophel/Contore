"""`/pokemon hunt` -- an on-demand wild encounter (as opposed to the
passive on_message spawn trigger, which is `spawn_message_hook.py`'s
territory). One-hour cooldown per trainer; an optional `area` biases which
types can appear.

Ported from the monolith's `pokemon_hunt` command. The "open an interactive
battle" step hands off to `pokemon.ui.battle_view.start_wild_encounter`
(added by Track E -- see `pokemon.commands.battle_bridge` for the actual
owned-Pokemon-row/WildEncounter -> Battler construction); `start_wild_battle`
below still degrades to a roll-only preview embed if that import ever fails
(e.g. a partial checkout missing pokemon/ui entirely), so this command never
hard-crashes just because the battle engine isn't present.
"""

from __future__ import annotations

import asyncio
import random
import time

from discord import Embed, Interaction, app_commands
from discord.app_commands import Choice

from pokemon.commands import repository
from pokemon.commands.battle_state import is_in_battle
from pokemon.commands.display import SHINY, cmd_mention, gender_str, poketypes
from pokemon.commands.groups import pokemon_group
from pokemon.commands.spawning import (
    LEGENDARY_MYTHICAL_LEVELS, LEVEL_RANGES_BY_RARITY, NATURES, WildEncounter,
    roll_form, roll_gender, roll_ivs, roll_wild_held_item, roll_wild_species,
)
from pokemon.commands.team import get_battle_team
from pokemon.dex.species import Species
from pokemon.generations.ruleset import ALL_GENERATIONS, GEN_6

HUNT_COOLDOWN_SECONDS = 3600

# Ported verbatim from `HUNT_AREAS`.
HUNT_AREAS: dict[str, list[str]] = {
    "forest": ["Bug", "Grass", "Normal", "Poison", "Flying"],
    "ocean": ["Water", "Flying", "Ice"],
    "cave": ["Rock", "Ground", "Poison", "Dark", "Ghost"],
    "mountain": ["Rock", "Ground", "Fighting", "Flying", "Steel"],
    "plains": ["Normal", "Grass", "Flying", "Bug"],
    "city": ["Normal", "Electric", "Poison", "Steel", "Psychic"],
    "volcano": ["Fire", "Rock", "Ground"],
    "arctic": ["Ice", "Water", "Normal", "Rock"],
    "swamp": ["Water", "Grass", "Poison", "Bug", "Ground"],
    "sky": ["Flying", "Dragon", "Psychic"],
    "ruins": ["Psychic", "Ghost", "Rock", "Steel", "Fairy"],
}

_hunt_notify_tasks: dict[int, asyncio.Task] = {}


def _team_reference_level(team: list[dict]) -> int:
    """Strongest Pokémon on the full roster -- what hunt's level/rarity
    biases scale against. Ported verbatim from `_team_reference_level`."""
    levels = [p["level"] for p in team]
    return max(levels) if levels else 5


def hunt_rarity_bias(team_ref_level: int) -> dict[str, float]:
    """Weaker teams see more common/uncommon spawns; strong teams see
    sharply more rare-and-up. Ported verbatim from `_hunt_rarity_bias`."""
    if team_ref_level < 30:
        strength = (30 - team_ref_level) / 30
        return {
            "common": 1.0 + 1.5 * strength, "uncommon": 1.0 + 0.5 * strength,
            "rare": 1.0 - 0.35 * strength, "epic": 1.0 - 0.5 * strength,
        }
    progress = min(1.0, (team_ref_level - 30) / 70)
    return {
        "rare": 1.0 + 0.3 * progress, "epic": 1.0 + 1.5 * progress,
        "legendary": 1.0 + 7.7 * progress, "mythical": 1.0 + 7.7 * progress,
    }


def roll_hunt_level(species: Species, leveled_as_rarity: str, team_ref_level: int) -> tuple[int, bool]:
    """Like `spawning.roll_wild_level`, but skews toward the bottom of the
    range for low-level teams (a beta distribution, not uniform) so a
    fresh trainer doesn't regularly hunt into something at the top of a
    rarity's range. Ported verbatim from `_roll_hunt_level` (its dead
    `species.get("wild_level_range")` branch dropped for the same reason
    as `spawning.roll_wild_level` -- see that function's docstring)."""
    fixed = LEGENDARY_MYTHICAL_LEVELS.get(species.id)
    if fixed is not None:
        return fixed, True
    lo, hi = LEVEL_RANGES_BY_RARITY.get(leveled_as_rarity, (2, 40))
    if lo == hi or leveled_as_rarity not in ("common", "uncommon") or team_ref_level >= 30:
        return (lo if lo == hi else random.randint(lo, hi)), False
    strength = max(0, (30 - team_ref_level) / 30)
    t = random.betavariate(1.0, 1.0 + 3.0 * strength)
    return max(lo, min(hi, lo + round(t * (hi - lo)))), False


def roll_hunt_encounter(team: list[dict], type_hints: list[str] | None, ruleset) -> WildEncounter:
    """Assembles a `WildEncounter` the same way `spawning.roll_wild_encounter`
    does, but with hunt's team-strength-scaled rarity/level bias in place
    of the flat roll -- the one place hunt.py can't just call that
    function directly."""
    team_ref_level = _team_reference_level(team)
    species, leveled_as = roll_wild_species(
        ruleset, type_hints=type_hints, rarity_bias=hunt_rarity_bias(team_ref_level),
        cr_neutral_legendary=team_ref_level >= 30)
    shiny = random.randint(1, 4096) == 1
    level, fixed = roll_hunt_level(species, leveled_as, team_ref_level)
    ivs = roll_ivs(rarity=species.rarity)
    nature = random.choice(NATURES)
    gender = roll_gender(species)
    form_slug = roll_form(species, gender)
    held_item = roll_wild_held_item(species)
    return WildEncounter(
        species=species, level=level, fixed_level=fixed, shiny=shiny, ivs=ivs, nature=nature,
        gender=gender, form_slug=form_slug, held_item=held_item, leveled_as_rarity=leveled_as)


async def start_wild_battle(
    bot, user, team: list[dict], encounter: WildEncounter, *, channel_id: int, guild_id: int | None,
    spawn: dict | None = None,
):
    """Hands off to the battle engine to actually open an interactive wild
    encounter. Soft dependency: `pokemon.ui.battle_view` currently only
    exposes rendering/input views, not a "begin a session" entry point
    (see this module's docstring), so this degrades to a plain
    "you encountered X" embed -- still useful for testing the roll logic
    end to end -- until that entry point exists. Whoever adds it should
    match this signature so `hunt.py`/`spawn_message_hook.py` don't need
    to change.
    """
    try:
        from pokemon.ui.battle_view import start_wild_encounter
    except ImportError:
        gender_txt = gender_str(encounter.gender)
        embed = Embed(
            title="⚔️ Wild Encounter",
            description=(
                f"A wild {SHINY + ' ' if encounter.shiny else ''}**{encounter.species.name}** "
                f"({poketypes(encounter.species.types)}) appeared!\n"
                f"{encounter.fuzzy_level_label()}"
                f"{(' \u200b\u00b7\u200b ' + gender_txt) if gender_txt else ''}\n\n"
                "-# The battle engine isn't wired up yet, so this is a preview of the roll only."),
            color=0x3B4252)
        return embed, None, None
    return await start_wild_encounter(
        bot, user, team, encounter, channel_id=channel_id, guild_id=guild_id, spawn=spawn)


def _server_ruleset(cfg: dict):
    return ALL_GENERATIONS.get(cfg.get("generation", 6), GEN_6)


@pokemon_group.command(
    name="hunt", description="Go hunting for a Pokémon (1h cooldown). Specify an area to bias the encounter.")
@app_commands.describe(
    area="forest · ocean · cave · mountain · plains · city · volcano · arctic · swamp · sky · ruins")
@app_commands.choices(area=[Choice(name=k.title(), value=k) for k in HUNT_AREAS])
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def pokemon_hunt(ctx: Interaction, area: Choice[str] | None = None):
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("Finish your current battle first!", ephemeral=True)
        return
    if not await repository.has_starter(ctx.user.id):
        await ctx.response.send_message(
            f"You need a starter Pokémon first! Use {cmd_mention('pokemon starter')} to get one.", ephemeral=True)
        return

    full_team = await get_battle_team(ctx.user.id)
    usable = [p for p in full_team if not p["fainted_at"]]
    if not usable:
        await ctx.response.send_message(
            "All your Pokémon have fainted! Heal them before heading out.", ephemeral=True)
        return

    now = time.time()
    existing_ready = await repository.get_hunt_cooldown(ctx.user.id)
    if existing_ready and existing_ready > now:
        await ctx.response.send_message(
            f"You need to rest before hunting again. You can hunt again <t:{int(existing_ready)}:R>.",
            ephemeral=True)
        return

    await ctx.response.defer()

    hunt_ready_at = now + HUNT_COOLDOWN_SECONDS
    await repository.set_hunt_cooldown(ctx.user.id, hunt_ready_at)
    if ctx.client:
        from pokemon.commands.collection import notify_hunt_ready

        old = _hunt_notify_tasks.get(ctx.user.id)
        if old and not old.done():
            old.cancel()
        _hunt_notify_tasks[ctx.user.id] = asyncio.create_task(
            notify_hunt_ready(ctx.client, ctx.user.id, HUNT_COOLDOWN_SECONDS))

    type_hints = HUNT_AREAS.get(area.value if area else "", [])
    cfg = await repository.get_pokemon_config(ctx.guild_id) if ctx.guild_id else {}
    ruleset = _server_ruleset(cfg)
    encounter = roll_hunt_encounter(usable, type_hints, ruleset)

    area_label = f" in the **{area.name}**" if area else ""
    embed, file, view = await start_wild_battle(
        ctx.client, ctx.user, full_team, encounter, channel_id=ctx.channel_id, guild_id=ctx.guild_id)
    if view is None:
        embed.title = f"⚔️ Wild Encounter{area_label}"
        await ctx.followup.send(embed=embed)
    else:
        message = await ctx.followup.send(embed=embed, file=file, view=view, wait=True)
        view.message = message


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

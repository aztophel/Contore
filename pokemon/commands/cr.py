"""`/pokemon cr` -- an exact catch-probability calculator for a species
under arbitrary conditions (ball, HP%, status, level gap, ...), independent
of any live battle. Thin wrapper over `pokemon.commands.catching`'s pure
math -- ported from the monolith's `pokemon_cr`.
"""

from __future__ import annotations

from typing import Literal

from discord import Embed, Interaction, app_commands

from pokemon.commands import repository
from pokemon.commands.catching import exact_catch_result
from pokemon.commands.dex_search import resolve_species, species_autocomplete
from pokemon.commands.groups import pokemon_group
from pokemon.commands.resolve import resolve_owned_pokemon
from pokemon.dex.items_repo import POKEBALL_DATA, POKEBALL_EMOJI

_BALL_NAMES = tuple(POKEBALL_DATA.keys())


@pokemon_group.command(name="cr", description="Calculate the exact catch probability for a Pokémon under specific conditions")
@app_commands.describe(
    pokemon="Species name or dex number",
    ball="Poké Ball to calculate odds for (default: Poké Ball)",
    hp_percent="Wild Pokémon's remaining HP as a percentage (default 100)",
    status="Status condition currently affecting the wild Pokémon",
    level="The wild Pokémon's level (only needed for the level-gap penalty)",
    turn="Battle turn number (affects Timer Ball / Quick Ball)",
    is_night="Whether it's currently night out (affects Dusk Ball)",
    already_caught="Whether you've caught this species before (affects Repeat Ball)",
    my_active_pokemon="Your active Pokémon, for Level/Love/Fast Ball conditions",
    use_my_team_level="Apply the level-gap penalty using your team's strongest level")
@app_commands.autocomplete(pokemon=species_autocomplete)
@app_commands.choices(
    ball=[app_commands.Choice(name=n, value=n) for n in _BALL_NAMES],
    status=[app_commands.Choice(name=n, value=n) for n in
            ("None", "Burn", "Poison", "Bad Poison", "Paralysis", "Sleep", "Freeze")])
async def pokemon_cr(
    ctx: Interaction,
    pokemon: str,
    ball: app_commands.Choice[str] | None = None,
    hp_percent: float = 100.0,
    status: app_commands.Choice[str] | None = None,
    turn: int = 1,
    is_night: bool = False,
    level: int | None = None,
    already_caught: bool | None = None,
    my_active_pokemon: str | None = None,
    use_my_team_level: bool = True,
):
    species = resolve_species(pokemon)
    if species is None:
        await ctx.response.send_message(f"No Pokémon found matching `{pokemon}`.", ephemeral=True)
        return

    ball_name = ball.value if ball else "Poké Ball"
    ball_data = POKEBALL_DATA.get(ball_name)
    if ball_data is None:
        await ctx.response.send_message(f"`{ball_name}` isn't a valid Poké Ball name.", ephemeral=True)
        return

    status_key = None if not status or status.value == "None" else status.value.lower().replace(" ", "_")
    hp_pct = max(0.0, min(100.0, hp_percent)) / 100.0

    dex_seen = await repository.get_pokedex_seen(ctx.user.id)
    caught_before = already_caught if already_caught is not None else (species.id in dex_seen)
    caught_ids = dex_seen if caught_before else (dex_seen - {species.id})

    my_pokemon = None
    if my_active_pokemon:
        resolved = await resolve_owned_pokemon(ctx, my_active_pokemon)
        if resolved and resolved["owner_id"] == ctx.user.id:
            my_pokemon = resolved

    my_ref_level = None
    if use_my_team_level and level is not None:
        team = await repository.get_user_pokemon(ctx.user.id)
        if team:
            my_ref_level = max(p["level"] for p in team)

    result = exact_catch_result(
        species, ball_name, ball_data, hp_pct=hp_pct, status=status_key,
        wild_gender=my_pokemon.get("gender", "genderless") if my_pokemon else "genderless",
        my_species_id=my_pokemon["species_id"] if my_pokemon else None,
        my_level=my_pokemon["level"] if my_pokemon else None,
        my_gender=my_pokemon.get("gender") if my_pokemon else None,
        dex_seen=caught_ids, turn=max(1, turn), is_night=is_night, my_ref_level=my_ref_level, wild_level=level)

    embed = Embed(
        title=f"Catch Odds \u200b\u00b7\u200b {POKEBALL_EMOJI.get(ball_name, '🔴')} {ball_name} \u200b\u00b7\u200b {species.name}",
        color=0x2ECC71 if result["overall_prob"] >= 0.5 else 0xE67E22 if result["overall_prob"] >= 0.15 else 0xE74C3C)
    embed.add_field(name="Catch Rate", value=f"{species.catch_rate}/255", inline=True)
    embed.add_field(name="HP", value=f"{hp_percent:.0f}%", inline=True)
    embed.add_field(name="Status", value=status.value if status and status.value != "None" else "—", inline=True)
    if level is not None:
        embed.add_field(name="Wild Level", value=str(level), inline=True)
        if my_ref_level is not None:
            embed.add_field(name="Your Reference Level", value=str(my_ref_level), inline=True)

    if result["guaranteed"]:
        embed.description = "**100%** — this ball guarantees a catch."
    else:
        crit_pct = result["crit_odds_256"] / 256 * 100
        embed.add_field(name="Catch Value (a)", value=f"{result['a']:.2f}", inline=True)
        embed.add_field(name="Critical Capture Chance", value=f"{crit_pct:.2f}%", inline=True)
        embed.description = (
            f"**Overall catch probability: `{result['overall_prob'] * 100:.2f}%`**\n"
            f"-# `{result['normal_prob'] * 100:.2f}%` if it's a normal 4-shake check \u200b\u00b7\u200b "
            f"`{result['crit_prob'] * 100:.2f}%` if it's a critical capture")
    await ctx.response.send_message(embed=embed, ephemeral=True)


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

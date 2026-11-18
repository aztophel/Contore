"""`/pokemon move` -- detailed info about a single move. Ported from
`pokemon_move`.

Nearly everything this command shows (power, accuracy, PP, category,
ailments, stat changes, drain/healing, hit counts) lives on
`pokemon.battle.move.MoveSpec`, which is only populated once
`data/moves_data.json` exists -- the PokeAPI enrichment pass this sandbox
can't run over the network (see `pokedex_builder.py`'s module docstring).
Until that file exists, this command can still resolve a move name/
autocomplete against `pokemon.dex.moves_repo`'s curated name list and say
so plainly, rather than either refusing to build the command at all or
fabricating placeholder stats.

The monolith's "Smogon mechanics" effect-description lookup
(`get_move_desc`) doesn't exist anywhere in this codebase and needs a
whole separate data source this sandbox has no access to; that section is
dropped entirely rather than stubbed, the same way genera/EV-yield are
dropped from the species embed.
"""

from __future__ import annotations

from discord import Embed, Interaction, app_commands
from discord.app_commands import Choice

from pokemon.commands.display import TYPE_EMOJI
from pokemon.commands.groups import pokemon_group
from pokemon.commands.moveset import get_move_registry, move_display_name
from pokemon.dex.type_chart import TYPE_COLOR

_CATEGORY_COLOR = {"physical": 0xC0392B, "special": 0x2980B9, "status": 0x27AE60}
_CATEGORY_EMOJI = {"physical": "\u2694\ufe0f", "special": "\u2728", "status": "\U0001f300"}


async def move_name_autocomplete(ctx: Interaction, current: str) -> list[Choice[str]]:
    """Ported from `move_name_autocomplete`, reading from the real
    `MoveRegistry` when it's loaded and from `moves_repo`'s curated
    display-name overrides (still real move slugs, just without
    power/accuracy/etc. to show) when it isn't -- either way the
    autocomplete list is real move names, not guesses."""
    cur = current.lower()
    registry = get_move_registry()
    # MoveRegistry has no public "list every loaded slug" method (only
    # per-slug lookups) -- this reaches into its private `_specs` dict
    # rather than adding one, since that's a call Track C/D should make if
    # they want it added to the frozen contract, not something to bolt on
    # unilaterally from here.
    slugs = list(getattr(registry, "_specs", {}).keys())
    if not slugs:
        from pokemon.dex.learnsets_repo import get_default_repository as get_learnsets

        seen: set[str] = set()
        for sid in range(1, 722):
            ls = get_learnsets().get(sid)
            seen.update(mv for _, mv in ls.level_moves)
            seen.update(ls.egg_moves)
            seen.update(ls.tm_moves)
            seen.update(ls.tutor_moves)
        slugs = sorted(seen)

    matches = [
        Choice(name=move_display_name(slug)[:100], value=slug)
        for slug in slugs if cur in move_display_name(slug).lower()
    ]
    matches.sort(key=lambda c: c.name)
    return matches[:25]


def _resolve_move_slug(text: str) -> str | None:
    """Ported from `_resolve_move_slug`/`_guess_move_slug`, matching
    against `MoveRegistry` when loaded, else the same learnset-derived
    slug set `move_name_autocomplete` builds."""
    import re
    from difflib import get_close_matches

    guess = re.sub(r"[^a-z0-9]+", "-", re.sub(r"['.]", "", text.strip().lower())).strip("-")

    registry = get_move_registry()
    slugs = list(getattr(registry, "_specs", {}).keys())
    if not slugs:
        from pokemon.dex.learnsets_repo import get_default_repository as get_learnsets

        seen: set[str] = set()
        for sid in range(1, 722):
            ls = get_learnsets().get(sid)
            seen.update(mv for _, mv in ls.level_moves)
            seen.update(ls.egg_moves)
            seen.update(ls.tm_moves)
            seen.update(ls.tutor_moves)
        slugs = sorted(seen)

    if guess in slugs:
        return guess
    target_norm = re.sub(r"[^a-z0-9]", "", text.lower())
    for slug in slugs:
        if re.sub(r"[^a-z0-9]", "", slug) == target_norm:
            return slug
    display_to_slug = {move_display_name(slug).lower(): slug for slug in slugs}
    close = get_close_matches(text.lower().strip(), display_to_slug.keys(), n=1, cutoff=0.6)
    return display_to_slug[close[0]] if close else None


@pokemon_group.command(name="move", description="View detailed info about a Pokemon move")
@app_commands.describe(move="The move to look up", ephemeral="Whether to make this move visible only to you or not")
@app_commands.autocomplete(move=move_name_autocomplete)
async def pokemon_move(ctx: Interaction, move: str, ephemeral: bool = True):
    slug = _resolve_move_slug(move)
    if slug is None:
        await ctx.response.send_message(f"Couldn't find a move called `{move}`.", ephemeral=True)
        return

    registry = get_move_registry()
    try:
        spec = registry.spec(slug)
    except KeyError:
        await ctx.response.send_message(
            f"Found **{move_display_name(slug)}**, but its full move data (power/accuracy/effects) hasn't been "
            "loaded yet on this bot.", ephemeral=True)
        return

    cat = spec.category.value
    type_emoji = TYPE_EMOJI.get(spec.type_name, "\u2754")
    rgb = TYPE_COLOR.get(spec.type_name)
    color = (rgb[0] << 16) | (rgb[1] << 8) | rgb[2] if rgb else _CATEGORY_COLOR.get(cat, 0x5865F2)

    embed = Embed(title=f"{type_emoji} {spec.display_name}", color=color)

    power = "OHKO" if spec.is_ohko else (spec.base_power if spec.base_power else "\u2014")
    acc = "\u2014" if spec.always_hits or spec.accuracy is None else f"{spec.accuracy}%"

    embed.add_field(name="Type", value=spec.type_name, inline=True)
    embed.add_field(name="Category", value=f"{_CATEGORY_EMOJI.get(cat, '')} {cat.title()}", inline=True)
    embed.add_field(name="Power", value=str(power), inline=True)
    embed.add_field(name="Accuracy", value=acc, inline=True)
    embed.add_field(name="PP", value=str(spec.pp), inline=True)
    embed.add_field(name="Priority", value=f"{spec.priority:+d}" if spec.priority else "0", inline=True)

    if spec.min_hits != 1 or spec.max_hits != 1:
        embed.add_field(
            name="Hits", value=f"{spec.min_hits}\u2013{spec.max_hits}" if spec.min_hits != spec.max_hits
            else str(spec.min_hits), inline=True)
    if spec.drain:
        embed.add_field(
            name="Drain/Recoil",
            value=(f"Heals {spec.drain}% of damage dealt" if spec.drain > 0 else f"{abs(spec.drain)}% recoil"),
            inline=True)
    if spec.healing:
        embed.add_field(name="Healing", value=f"{spec.healing}% of max HP", inline=True)
    if spec.ailment and spec.ailment_chance:
        embed.add_field(
            name="Ailment Chance", value=f"{spec.ailment_chance}% {spec.ailment.replace('_', ' ').title()}",
            inline=True)
    if spec.flinch_chance:
        embed.add_field(name="Flinch Chance", value=f"{spec.flinch_chance}%", inline=True)
    if spec.stat_changes:
        sc_lines = [f"{sc.stat.replace('-', ' ').title()} {sc.change:+d}" for sc in spec.stat_changes]
        chance = f" ({spec.stat_chance}% chance)" if spec.stat_chance else ""
        embed.add_field(name="Stat Changes", value="\n".join(sc_lines) + chance, inline=True)

    await ctx.response.send_message(embed=embed, ephemeral=ephemeral)


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

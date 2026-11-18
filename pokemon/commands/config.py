"""`/pokemon config ...` -- server-admin controls for spawning and, new in
this port, per-server generation rules (see ARCHITECTURE.md /
`pokemon.generations.ruleset` for the design). Ported from the monolith's
`poke_config_commands` group (`enable`/`channel`/`rate`/`catchxp`
unchanged in behavior; `generation`/`dexcap`/`restrictmode` are new).

Free-function command style, not Cog methods -- see
`pokemon.commands.groups` for why.
"""

from __future__ import annotations

import discord
from discord import Interaction, app_commands

from pokemon.commands import repository
from pokemon.commands.groups import config_group
from pokemon.generations.ruleset import ALL_GENERATIONS, RestrictMode

_GENERATION_CHOICES = [
    app_commands.Choice(name=ruleset.display_name, value=number)
    for number, ruleset in sorted(ALL_GENERATIONS.items())
]

# The dev/testing server this bot originally ran on gets to ignore its own
# rate-limit floors. Ported as-is from the monolith rather than silently
# dropped, but flagged the same way `core/permissions.py` flags OWNER_ID:
# this should come from config/env, not a literal, next time anyone's
# already in this file for an unrelated reason.
_UNTHROTTLED_GUILD_ID = 1225102857431420988


async def _has_levelling_enabled(guild_id: int) -> bool:
    """Soft dependency on Track A's server-wide (non-Pokémon) levelling
    feature, exactly like `pokemon.commands._db`'s pool bootstrap:
    `catchxp` only makes sense once that system exists and is turned on
    for this server, but Track B shouldn't have to wait on Track A to
    land it to keep testing."""
    try:
        from core.leveling import get_levelling_config  # type: ignore[import-not-found]
    except ImportError:
        return True  # nothing to gate on yet
    cfg = await get_levelling_config(guild_id)
    return bool(cfg.get("enabled"))


@config_group.command(name="enable", description="Enable or disable Pokémon spawning in this server")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_enable(ctx: Interaction, enabled: bool):
    cfg = await repository.get_pokemon_config(ctx.guild_id)
    cfg["enabled"] = enabled
    await repository.save_pokemon_config(ctx.guild_id, cfg)
    await ctx.response.send_message(
        f"Pokémon spawning **{'enabled' if enabled else 'disabled'}**.", ephemeral=True)


@config_group.command(name="channel", description="Restrict spawns to a single channel (blank = any channel)")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_channel(ctx: Interaction, channel: discord.TextChannel | None = None):
    cfg = await repository.get_pokemon_config(ctx.guild_id)
    cfg["spawn_channel"] = channel.id if channel else None
    await repository.save_pokemon_config(ctx.guild_id, cfg)
    await ctx.response.send_message(
        f"Pokémon will now only spawn in {channel.mention}." if channel
        else "Pokémon can now spawn in any channel.",
        ephemeral=True)


@config_group.command(name="rate", description="Set the message-count range between spawns")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
@app_commands.describe(
    min_messages="Lowest number of messages between spawns",
    spawn_chance="Chance for a Pokémon to spawn on any given message (0-100%)")
async def config_rate(ctx: Interaction, min_messages: int, spawn_chance: float):
    unthrottled = ctx.guild_id == _UNTHROTTLED_GUILD_ID
    if min_messages < 1 or spawn_chance < 0 or spawn_chance > 100:
        await ctx.response.send_message("Illegal values provided.", ephemeral=True)
        return
    if min_messages < 15 and not unthrottled:
        await ctx.response.send_message("`min_messages` must be at least **`15`**.", ephemeral=True)
        return
    if spawn_chance > 1 and not unthrottled:
        await ctx.response.send_message("`spawn_chance` cannot be greater than **`1%`**.", ephemeral=True)
        return
    cfg = await repository.get_pokemon_config(ctx.guild_id)
    cfg["spawn_min"] = min_messages
    cfg["spawn_chance"] = spawn_chance / 100.0
    await repository.save_pokemon_config(ctx.guild_id, cfg)
    await ctx.response.send_message(
        f"Pokémon will now spawn after **{min_messages}** messages since the last spawn, "
        f"with a {spawn_chance:.2f}% chance per message.", ephemeral=True)


@config_group.command(name="catchxp", description="Set how much levelling XP a catch grants (0 to disable)")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_catchxp(ctx: Interaction, xp: int):
    if not await _has_levelling_enabled(ctx.guild_id):
        await ctx.response.send_message(
            "Levelling is not enabled on this server. Enable it first.", ephemeral=True)
        return
    cfg = await repository.get_pokemon_config(ctx.guild_id)
    cfg["catch_xp"] = xp
    await repository.save_pokemon_config(ctx.guild_id, cfg)
    await ctx.response.send_message(f"Catching a Pokémon now grants **{xp:,} XP**.", ephemeral=True)


# -- new: per-server generation rules (see ARCHITECTURE.md) -----------------

@config_group.command(name="generation", description="Set which generation's rules govern battles here")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
@app_commands.choices(generation=_GENERATION_CHOICES)
async def config_generation(ctx: Interaction, generation: app_commands.Choice[int]):
    cfg = await repository.get_pokemon_config(ctx.guild_id)
    cfg["generation"] = generation.value
    await repository.save_pokemon_config(ctx.guild_id, cfg)
    await ctx.response.send_message(
        f"Battles in this server now follow **{generation.name}** rules.", ephemeral=True)


@config_group.command(
    name="dexcap",
    description="Cap which generation's Pokémon can spawn/be caught here (blank = match the battle generation)")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
@app_commands.choices(generation=_GENERATION_CHOICES)
async def config_dexcap(ctx: Interaction, generation: app_commands.Choice[int] | None = None):
    cfg = await repository.get_pokemon_config(ctx.guild_id)
    cfg["dex_cap_generation"] = generation.value if generation else None
    await repository.save_pokemon_config(ctx.guild_id, cfg)
    if generation:
        await ctx.response.send_message(
            f"Only Pokémon up to **{generation.name}** will spawn/be catchable here now.", ephemeral=True)
    else:
        await ctx.response.send_message(
            "The Pokédex cap now matches this server's battle generation.", ephemeral=True)


@config_group.command(
    name="restrictmode",
    description="Whether an above-the-cap Pokémon can still battle here if it was brought in, not caught here")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
@app_commands.choices(mode=[
    app_commands.Choice(name="Spawn only — above-cap Pokémon can still battle if brought in (default)",
                         value=RestrictMode.SPAWN_ONLY.value),
    app_commands.Choice(name="Full — above-cap Pokémon can't battle here at all",
                         value=RestrictMode.FULL.value),
])
async def config_restrictmode(ctx: Interaction, mode: app_commands.Choice[str]):
    cfg = await repository.get_pokemon_config(ctx.guild_id)
    cfg["restrict_mode"] = mode.value
    await repository.save_pokemon_config(ctx.guild_id, cfg)
    label = "Spawn only" if mode.value == RestrictMode.SPAWN_ONLY.value else "Full"
    await ctx.response.send_message(f"Dex-cap restriction mode set to **{label}**.", ephemeral=True)


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility: this module's
    commands attach to the shared `config_group` at import time (see the
    `@config_group.command(...)` decorators above), and `config_group`
    itself gets registered exactly once by `pokemon.commands.groups.register`."""

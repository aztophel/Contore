"""Generalized boss battles: any user (real or a placeholder bot-style
account) can be configured, by the bot owner, to have a custom team and
themed challenge UI that `/pokemon duel` uses automatically when they're
challenged -- a direct generalization of the monolith's hardcoded
"Contore" NPC fight (`BOSS_TEAM`/`_build_boss_team`/the `opponent.id ==
CONTORE.user.id` branch in `pokemon_duel`).

What's built here: the full data model (`pokemon_boss_configs` +
`pokemon_boss_team_slots`), owner-only commands to define/edit/remove a
boss's team and themed labels, and `get_boss_team`/`is_boss` for
`duel.py` to call. `start_boss_battle` hands off to
`pokemon.ui.battle_view.start_boss_encounter`, which drives the boss's
side with `pokemon.battle.ai`'s interim heuristic -- NOT the monolith's
real `choose_boss_action`/`_boss_matchup_score`/etc., which is tightly
coupled to the old `FighterState`/`BattleField` model and is a genuinely
separate, scoped porting task (see `pokemon.battle.ai`'s own docstring for
the full reasoning). Boss battles are playable end-to-end today; the boss
just isn't the same sharp opponent the monolith's was yet.
"""

from __future__ import annotations

import json

from discord import Embed, Interaction, User, app_commands
from discord.app_commands import Choice

from core.permissions import is_owner
from pokemon.commands import repository
from pokemon.commands.dex_search import resolve_species, species_autocomplete
from pokemon.commands.display import pokedisplay_naked, species_emoji
from pokemon.commands.groups import pokemon_group
from pokemon.commands.leveling import total_xp_for_level
from pokemon.commands.moveset import move_display_name, move_pp
from pokemon.commands.spawning import NATURES
from pokemon.commands.stats import pokemon_max_hp
from pokemon.dex.species import STAT_KEYS, get_default_repository as get_species_repo

boss_group = app_commands.Group(
    name="boss", description="Configure boss battles", parent=pokemon_group)

# Synthetic Pokemon-row IDs for boss teams stay well clear of any real
# `pokemon_owned.id` (a BIGSERIAL starting at 1) -- ported from the
# monolith's `_BOSS_PID_START` convention, just negative instead of a
# large positive offset so there's no plausible future collision at all.
_BOSS_PID_START = -1


_boss_user_ids_cache: set[int] | None = None


async def _boss_user_ids() -> set[int]:
    global _boss_user_ids_cache
    if _boss_user_ids_cache is None:
        _boss_user_ids_cache = set(await repository.get_all_boss_user_ids())
    return _boss_user_ids_cache


def _invalidate_cache() -> None:
    global _boss_user_ids_cache
    _boss_user_ids_cache = None


async def is_boss(user_id: int) -> bool:
    """Whether `/pokemon duel`-ing this user should trigger the boss-battle
    flow instead of a normal PvP offer."""
    return user_id in await _boss_user_ids()


def _boss_pokedata(slot_index: int, row: dict) -> dict:
    """The boss-team equivalent of `repository.get_pokemon` -- an
    owned-Pokemon-shaped dict good enough for every display helper
    (`pokedisplay`, `pokemon_max_hp`, `build_pokemon_full_embed`, ...) to
    treat like a real row, without it actually being one. Ported from
    `_make_boss_pokedata`, generalized off DB rows instead of a hardcoded
    tuple."""
    species_repo = get_species_repo()
    species = species_repo.get(row["species_id"])
    growth_rate = species.growth_rate if species else "medium"
    moves = json.loads(row["moves"]) if isinstance(row["moves"], str) else row["moves"]
    ivs = {k: 31 for k in STAT_KEYS}
    stored_ivs = json.loads(row["ivs"]) if isinstance(row["ivs"], str) else row["ivs"]
    ivs.update(stored_ivs or {})
    evs = json.loads(row["evs"]) if isinstance(row["evs"], str) else (row["evs"] or {})

    p = {
        "id": _BOSS_PID_START - slot_index,
        "owner_id": row["boss_user_id"],
        "species_id": row["species_id"],
        "form": None,
        "nickname": row.get("nickname"),
        "gender": row.get("gender", "genderless"),
        "level": row["level"],
        "xp": float(total_xp_for_level(row["level"], growth_rate)),
        "current_hp": None,
        "fainted_at": None,
        "hurt_at": None,
        "pp_reset_at": None,
        "ivs": json.dumps(ivs),
        "evs": json.dumps(evs),
        "nature": row.get("nature", "Hardy"),
        "ability": row.get("ability"),
        "shiny": 1,
        "cosmetic_species_id": None,
        "cosmetic_form": None,
        "caught_at": None,
        "caught_guild_id": None,
        "ball_type": "Cherish Ball",
        "held_item": row.get("held_item"),
        "moves": json.dumps(moves),
        "friendship": 255,
        "status_cond": None,
        "favorite": 0,
        "team_left_at": None,
    }
    p["current_hp"] = pokemon_max_hp(p, species) if species else 1
    return p


async def get_boss_team(user_id: int) -> list[dict]:
    """The boss's full roster as owned-Pokemon-shaped dicts, at full HP.
    Ported from `_build_boss_team`, generalized off DB-configured slots
    instead of the hardcoded `BOSS_TEAM` list."""
    rows = await repository.get_boss_team_slots(user_id)
    return [_boss_pokedata(i, row) for i, row in enumerate(rows)]


async def start_boss_battle(
    bot, challengers: list[User], boss_user_id: int, boss_team: list[dict], *,
    format: str, allow_items: bool, channel_id: int, guild_id: int | None,
):
    """Soft dependency onto the battle engine's AI-driven session start --
    see this module's docstring for why. Degrades to a roster-preview
    embed, matching `hunt.start_wild_battle`/`duel.start_pvp_battle`'s
    fallback shape exactly."""
    try:
        from pokemon.ui.battle_view import start_boss_encounter
    except ImportError:
        cfg = await repository.get_boss_config(boss_user_id)
        boss_name = cfg["display_name"] if cfg else f"<@{boss_user_id}>"
        challenger_line = ", ".join(u.mention for u in challengers)
        team_line = ", ".join(str(species_emoji(p["species_id"], True)) + f" {pokedisplay_naked(p)}"
                               for p in boss_team) or "*(no team configured)*"
        embed = Embed(
            title=f"Boss Battle: {boss_name}",
            description=(
                f"{challenger_line} vs **{boss_name}**\n{team_line}\n\n"
                "-# The battle engine's AI opponent isn't wired up yet, so this is a preview of the "
                "matchup only."),
            color=0x3B0000)
        return embed, None, None
    return await start_boss_encounter(
        bot, challengers, boss_user_id, boss_team, format=format, allow_items=allow_items,
        channel_id=channel_id, guild_id=guild_id)


# ---------------------------------------------------------------------------
# Owner-only configuration commands
# ---------------------------------------------------------------------------

@boss_group.command(name="set", description="[Owner] Set a user up as a boss battle (or update their theming)")
@app_commands.describe(
    user="The user to configure as a boss",
    display_name="How the boss is named in embeds/messages (e.g. 'Contore')",
    accept_label="Button label for accepting the challenge (e.g. 'DEFY')",
    decline_label="Button label for declining (e.g. 'BACK DOWN')",
    challenge_line="Flavor text shown when someone challenges this boss solo")
@is_owner()
async def boss_set(
    ctx: Interaction, user: User, display_name: str,
    accept_label: str = "DEFY", decline_label: str = "BACK DOWN", challenge_line: str = "You will fail.",
):
    if user.bot and user.id != ctx.client.user.id:
        await ctx.response.send_message("Other bots can't be configured as bosses.", ephemeral=True)
        return
    await repository.upsert_boss_config(
        user.id, display_name=display_name, accept_label=accept_label, decline_label=decline_label,
        challenge_line=challenge_line, configured_by=ctx.user.id)
    _invalidate_cache()
    await ctx.response.send_message(
        f"{user.mention} is now a boss battle (**{display_name}**). Use `/pokemon boss addmon` to build "
        "their team.", ephemeral=True)


@boss_group.command(name="unset", description="[Owner] Remove a user's boss-battle configuration")
@app_commands.describe(user="The user to remove boss status from")
@is_owner()
async def boss_unset(ctx: Interaction, user: User):
    if await repository.delete_boss_config(user.id):
        _invalidate_cache()
        await ctx.response.send_message(f"{user.mention} is no longer a boss battle.", ephemeral=True)
    else:
        await ctx.response.send_message(f"{user.mention} wasn't configured as a boss.", ephemeral=True)


@boss_group.command(name="list", description="[Owner] List every configured boss battle")
@is_owner()
async def boss_list(ctx: Interaction):
    ids = await repository.get_all_boss_user_ids()
    if not ids:
        await ctx.response.send_message("No boss battles are configured.", ephemeral=True)
        return
    lines = []
    for uid in ids:
        cfg = await repository.get_boss_config(uid)
        team = await repository.get_boss_team_slots(uid)
        lines.append(f"<@{uid}> - **{cfg['display_name']}** ({len(team)}/6 Pokemon)")
    await ctx.response.send_message(
        embed=Embed(title="Configured Boss Battles", description="\n".join(lines), color=0x3B0000),
        ephemeral=True)


@boss_group.command(name="addmon", description="[Owner] Add a Pokemon to a boss's team")
@app_commands.describe(
    user="The boss to add this Pokemon to", species="Species name or dex number", level="Level (1-255)",
    moves="Up to 4 moves, comma-separated (e.g. 'calm mind, scald, ice beam, recover')",
    nickname="Optional nickname", held_item="Optional held item name", nature="Nature",
    ability="Ability (defaults to the species' first standard ability)",
    gender="Gender", evs="EVs as 'stat:value' pairs, e.g. 'hp:252 spa:252 spd:4'",
    ivs="IVs as 'stat:value' pairs (defaults to all 31); use to intentionally lower a stat, e.g. 'attack:0'")
@app_commands.autocomplete(species=species_autocomplete)
@app_commands.choices(
    nature=[Choice(name=n, value=n) for n in NATURES],
    gender=[Choice(name=n, value=n) for n in ("Male", "Female", "Genderless")])
@is_owner()
async def boss_addmon(
    ctx: Interaction, user: User, species: str, level: app_commands.Range[int, 1, 255], moves: str,
    nickname: str | None = None, held_item: str | None = None, nature: Choice[str] | None = None,
    ability: str | None = None, gender: Choice[str] | None = None, evs: str | None = None, ivs: str | None = None,
):
    cfg = await repository.get_boss_config(user.id)
    if not cfg:
        await ctx.response.send_message(
            f"{user.mention} isn't set up as a boss yet. Use `/pokemon boss set` first.", ephemeral=True)
        return

    sp = resolve_species(species)
    if not sp:
        await ctx.response.send_message(f"No species found matching `{species}`.", ephemeral=True)
        return

    existing = await repository.get_boss_team_slots(user.id)
    if len(existing) >= 6:
        await ctx.response.send_message(
            f"**{cfg['display_name']}**'s team is already full (6/6). Remove one first.", ephemeral=True)
        return

    move_names = [m.strip() for m in moves.split(",") if m.strip()][:4]
    if not move_names:
        await ctx.response.send_message("Give at least one move.", ephemeral=True)
        return
    from pokemon.commands.move_lookup import _resolve_move_slug
    resolved_moves = []
    for m in move_names:
        slug = _resolve_move_slug(m)
        if not slug:
            await ctx.response.send_message(f"Couldn't resolve move `{m}`.", ephemeral=True)
            return
        resolved_moves.append({"name": slug, "pp": move_pp(slug), "max_pp": move_pp(slug)})

    def _parse_stat_pairs(text: str | None) -> dict[str, int]:
        out = {}
        if not text:
            return out
        for pair in text.split():
            if ":" not in pair:
                continue
            k, v = pair.split(":", 1)
            k = {"atk": "attack", "def": "defense", "spa": "special-attack",
                 "spd": "special-defense", "spe": "speed"}.get(k.lower(), k.lower())
            if k in STAT_KEYS:
                out[k] = int(v)
        return out

    ev_dict = _parse_stat_pairs(evs)
    if sum(ev_dict.values()) > 510:
        await ctx.response.send_message("Total EVs can't exceed 510.", ephemeral=True)
        return
    iv_dict = {k: 31 for k in STAT_KEYS}
    iv_dict.update(_parse_stat_pairs(ivs))

    ability_slug = None
    if ability:
        ability_slug = ability.strip().lower().replace(" ", "-")
        if ability_slug not in {a.name for a in sp.abilities}:
            await ctx.response.send_message(
                f"**{sp.name}** doesn't have an ability called `{ability}`.", ephemeral=True)
            return
    elif sp.abilities:
        ability_slug = next((a.name for a in sp.abilities if not a.is_hidden), sp.abilities[0].name)

    gender_key = gender.value.lower() if gender else "genderless"

    slot = await repository.add_boss_team_slot(
        user.id, species_id=sp.id, level=level, nickname=nickname, gender=gender_key, moves=resolved_moves,
        held_item=held_item, nature=(nature.value if nature else "Hardy"), ability=ability_slug,
        evs=ev_dict, ivs=iv_dict)

    move_list = ", ".join(move_display_name(m["name"]) for m in resolved_moves)
    await ctx.response.send_message(
        f"Added **{nickname or sp.name}** (Lv.{level} {sp.name}) to **{cfg['display_name']}**'s team, "
        f"slot {slot + 1}/6.\nMoves: {move_list}", ephemeral=True)


@boss_group.command(name="removemon", description="[Owner] Remove a Pokemon from a boss's team")
@app_commands.describe(user="The boss to remove from", slot="Team slot to remove (1-6, see /pokemon boss team)")
@is_owner()
async def boss_removemon(ctx: Interaction, user: User, slot: app_commands.Range[int, 1, 6]):
    if await repository.remove_boss_team_slot(user.id, slot - 1):
        await ctx.response.send_message(f"Removed slot {slot} from {user.mention}'s boss team.", ephemeral=True)
    else:
        await ctx.response.send_message(f"{user.mention} doesn't have a Pokemon in slot {slot}.", ephemeral=True)


@boss_group.command(name="team", description="View a configured boss's current team")
@app_commands.describe(user="The boss to view")
async def boss_team(ctx: Interaction, user: User):
    cfg = await repository.get_boss_config(user.id)
    if not cfg:
        await ctx.response.send_message(f"{user.mention} isn't configured as a boss.", ephemeral=True)
        return
    team = await get_boss_team(user.id)
    if not team:
        await ctx.response.send_message(f"**{cfg['display_name']}** doesn't have a team configured yet.",
                                        ephemeral=True)
        return
    lines = [
        f"{i + 1}. {species_emoji(p['species_id'], True)} **{pokedisplay_naked(p)}** \u00b7 "
        f"Lv.{p['level']} \u00b7 {p['nature']}"
        + (f" \u00b7 {p['held_item']}" if p.get("held_item") else "")
        for i, p in enumerate(team)
    ]
    await ctx.response.send_message(
        embed=Embed(title=f"{cfg['display_name']}'s Team ({len(team)}/6)", description="\n".join(lines),
                    color=0x3B0000),
        ephemeral=True)


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

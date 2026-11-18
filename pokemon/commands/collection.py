"""Collection-management commands: `nickname`, `favorite`, `release`,
`wallet`, `notifications`, `list`, `leaderboard`, `eggs`. Ported from the
monolith's `poke_commands` group; behavior unchanged.

`/pokemon info` and `/pokemon species` are NOT in this file -- they're
large, deeply cross-cutting (PIL hexagon stat charts, evolution-chain
rendering, mega-stone lookups, move category/power display that also
needs `data/moves_data.json`) and share a lot of machinery with each other
that's cleanest to build together in one pass. See TRACK_B_HANDOFF_2.md
for the concrete breakdown of what's left there.

Free-function command style -- see `pokemon.commands.groups` for why.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from discord import AllowedMentions, Embed, Interaction, User, app_commands
from discord.app_commands import Choice
from discord.utils import MISSING

from pokemon.commands import repository
from pokemon.commands import transfer
from pokemon.commands.battle_state import is_in_battle
from pokemon.commands.confirm import Yes
from pokemon.commands.dex_search import resolve_species, species_autocomplete
from pokemon.commands.display import (
    FAINTED, HOLDING_ITEM, cmd_mention, gender_str, item_display, pokedisplay, pokedisplay_naked, pokemoji,
    pokemon_display_name,
)
from pokemon.commands.groups import pokemon_group, profile_group
from pokemon.commands.paging import SimplePageView
from pokemon.commands.resolve import resolve_owned_pokemon, respond
from pokemon.commands.spawning import NATURES
from pokemon.commands.stats import iv_avg_percent
from pokemon.dex.items_repo import MEGA_STONES, POKEBALL_EMOJI
from pokemon.dex.species import get_default_repository as get_species_repo

COLLECTION_COLOR = 0xF52754


# ---------------------------------------------------------------------------
# Cooldown-ready DM notifications -- scheduled by hunt.py/breeding.py,
# gated by the `notifications` command below. Kept here (rather than in
# each of those files) since both read the same `notify_hunt`/
# `notify_breed` prefs this command writes.
# ---------------------------------------------------------------------------

async def notify_hunt_ready(bot, user_id: int, delay: float) -> None:
    """Ported verbatim from `_notify_hunt_ready`."""
    await asyncio.sleep(delay)
    if not (await repository.get_notify_prefs(user_id))["notify_hunt"]:
        return
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await user.send("Your hunting cooldown is over. You can go hunt for a Pokémon again!")
    except Exception:
        pass


async def notify_breed_ready(bot, user_id: int, pokemon_id: int, delay: float) -> None:
    """Ported verbatim from `_notify_breed_ready`."""
    await asyncio.sleep(delay)
    if not (await repository.get_notify_prefs(user_id))["notify_breed"]:
        return
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        p = await repository.get_pokemon(pokemon_id)
        name = pokemon_display_name(p) if p else "Your Pokémon"
        await user.send(f"💞 {name} is rested and ready to breed again!")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# nickname / favorite / release
# ---------------------------------------------------------------------------

@pokemon_group.command(name="nickname", description="Give one of your Pokémon a nickname")
@app_commands.describe(pokemon="Pokémon ID, nickname, or species name")
async def pokemon_nickname(ctx: Interaction, pokemon: str, nickname: str | None = None):
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("Finish your current battle first!", ephemeral=True)
        return
    p = await resolve_owned_pokemon(ctx, pokemon)
    if not p or p["owner_id"] != ctx.user.id:
        await ctx.response.send_message(f"You don't own any Pokémon matching `{pokemon}`.", ephemeral=True)
        return
    pokemon_id = p["id"]
    old_name = pokedisplay(p)
    if nickname and len(nickname) > 32:
        await respond(ctx, "Nicknames must be 32 characters or fewer.", ephemeral=True)
        return
    if not await repository.set_pokemon_nickname(pokemon_id, ctx.user.id, nickname):
        await respond(ctx, "You don't own a Pokémon with that ID.", ephemeral=True)
        return
    await respond(
        ctx, f"Nickname for {old_name} {f'set to **`{nickname}`**' if nickname else 'removed'}.", ephemeral=True)


@pokemon_group.command(name="favorite", description="Mark a Pokémon as your favorite (shown first in your list)")
@app_commands.describe(pokemon="Pokémon ID, nickname, or species name")
async def pokemon_favorite(ctx: Interaction, pokemon: str):
    p = await resolve_owned_pokemon(ctx, pokemon)
    if not p or p["owner_id"] != ctx.user.id:
        await ctx.response.send_message(f"You don't own any Pokémon matching `{pokemon}`.", ephemeral=True)
        return
    if not await repository.set_pokemon_favorite(ctx.user.id, p["id"]):
        await ctx.response.send_message("You don't own that Pokémon.", ephemeral=True)
        return
    await ctx.response.send_message("Favorite updated!", ephemeral=True)


@pokemon_group.command(name="release", description="Release one of your Pokémon back into the wild")
@app_commands.describe(pokemon="Pokémon ID, nickname, or species name")
async def pokemon_release(ctx: Interaction, pokemon: str):
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("Finish your current battle first!", ephemeral=True)
        return

    p = await resolve_owned_pokemon(ctx, pokemon)
    if not p or p["owner_id"] != ctx.user.id:
        await ctx.response.send_message(f"You don't own any Pokémon matching `{pokemon}`.", ephemeral=True)
        return
    pokemon_id = p["id"]

    flags = await repository.get_pokemon_trainer_flags(ctx.user.id)
    if flags["has_traded_away"] and len(await repository.get_user_pokemon(ctx.user.id)) <= 1:
        await respond(ctx, "You can't release your last Pokémon!", ephemeral=True)
        return

    species = get_species_repo().get(p["species_id"])
    display = f"{pokedisplay(p)} (`#{pokemon_id}`)"

    yes = Yes(users=[ctx.user.id], confirm_text=f"Released {display}. Farewell!", deny_text="Phew, that was close.")
    await respond(ctx, f"Release {display}? This cannot be undone.", view=yes, ephemeral=True)
    await yes.wait()
    if not yes.value:
        return

    await repository.delete_pokemon(pokemon_id, ctx.user.id)

    slots = await repository.get_team_slots(ctx.user.id)
    if pokemon_id in slots:
        slots.remove(pokemon_id)
        await repository.save_team_slots(ctx.user.id, slots)

    remaining = await repository.get_user_pokemon(ctx.user.id)
    if species and species.rarity in ("legendary", "mythical"):
        flag = "has_legendary" if species.rarity == "legendary" else "has_mythical"
        still_has = any(
            (get_species_repo().get(r["species_id"]) or object()).rarity == species.rarity for r in remaining)
        if not still_has:
            await repository.set_trainer_flag(ctx.user.id, flag, False)

    if not remaining:
        # Releasing your very last Pokémon undoes the starter kit, letting
        # you pick a starter fresh -- ported verbatim, including the exact
        # refund amounts, from the monolith's own "reset" behavior here.
        await repository.add_item(ctx.user.id, "Poké Ball", -8)
        await repository.add_item(ctx.user.id, "Potion", -5)
        await repository.add_item(ctx.user.id, "Revive", -3)
        await repository.add_pokedollars(ctx.user.id, -1500)
        await repository.delete_starter(ctx.user.id)


# ---------------------------------------------------------------------------
# wallet / notifications
# ---------------------------------------------------------------------------

@profile_group.command(name="wallet", description="View your Pokédollar balance")
# Under `profile`, not directly on `pokemon_group` like its 8 siblings in
# this file -- purely to keep `pokemon_group` at Discord's 25-child cap
# once `dex_progress.py` added two more commands; see `groups.py`'s
# `profile_group` comment. No behavior change, just which group it's
# nested under.
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def pokemon_wallet(ctx: Interaction, user: User | None = None):
    target = user or ctx.user
    name = "Your" if target.id == ctx.user.id else target.mention + "'s"
    balance = await repository.get_pokedollars(target.id)
    await ctx.response.send_message(
        f"{name} balance: **₽{balance:,}**", ephemeral=(user is None), allowed_mentions=AllowedMentions.none())


@pokemon_group.command(name="notifications", description="Toggle DMs for when your hunt/breed cooldowns finish")
@app_commands.describe(hunt="DM me when my hunt cooldown finishes", breed="DM me when a bred Pokémon can breed again")
async def pokemon_notifications(ctx: Interaction, hunt: bool | None = None, breed: bool | None = None):
    prefs = await repository.get_notify_prefs(ctx.user.id)
    if hunt is not None:
        await repository.set_notify_pref(ctx.user.id, "notify_hunt", hunt)
        prefs["notify_hunt"] = hunt
    if breed is not None:
        await repository.set_notify_pref(ctx.user.id, "notify_breed", breed)
        prefs["notify_breed"] = breed
    await ctx.response.send_message(
        f"🎣 Hunt cooldown DMs: **{'On' if prefs['notify_hunt'] else 'Off'}**\n"
        f"💞 Breed cooldown DMs: **{'On' if prefs['notify_breed'] else 'Off'}**",
        ephemeral=True)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@pokemon_group.command(name="list", description="View your (or someone else's) Pokémon collection")
@app_commands.describe(
    user="Whose collection to view (leave blank for your own)",
    species="Only show Pokémon of this species", gender="Only show Pokémon of this gender",
    nature="Only show Pokémon with this nature", shiny="Only show shiny (or only non-shiny) Pokémon",
    favorite_only="Only show your favorited Pokémon", sort="How to sort the list", order="Sort direction")
@app_commands.autocomplete(species=species_autocomplete)
@app_commands.choices(
    gender=[Choice(name=n, value=n) for n in ("Male", "Female", "Genderless")],
    nature=[Choice(name=n, value=n) for n in NATURES],
    sort=[Choice(name=n, value=n) for n in ("ID", "Level", "IV", "Name", "Species", "BST")],
    order=[Choice(name=n, value=n) for n in ("Ascending", "Descending")],
)
async def pokemon_boxes(
    ctx: Interaction, user: User | None = None, species: str | None = None,
    gender: Choice[str] | None = None, nature: Choice[str] | None = None,
    shiny: bool | None = None, favorite_only: bool | None = None,
    sort: Choice[str] | None = None, order: Choice[str] | None = None,
):
    await ctx.response.defer(ephemeral=(user is None))
    target = user or ctx.user
    species_repo = get_species_repo()
    owned = await repository.get_user_pokemon(target.id)
    if not owned:
        await ctx.followup.send(
            f"{target.mention} hasn't caught any Pokémon yet.", allowed_mentions=AllowedMentions.none())
        return

    species_id = None
    if species:
        sp_match = resolve_species(species)
        if sp_match is None:
            await ctx.followup.send(f"No species found matching `{species}`.", ephemeral=True)
            return
        species_id = sp_match.id

    sort_key = sort.value if sort else "ID"
    order_desc = (order.value if order else "Descending") == "Descending"

    filtered = owned
    if species_id is not None:
        filtered = [p for p in filtered if p["species_id"] == species_id]
    if gender is not None:
        gkey = gender.value.lower()
        filtered = [p for p in filtered if (p.get("gender") or "genderless") == gkey]
    if nature is not None:
        filtered = [p for p in filtered if p.get("nature") == nature.value]
    if shiny is not None:
        filtered = [p for p in filtered if bool(p["shiny"]) == shiny]
    if favorite_only:
        filtered = [p for p in filtered if p["favorite"]]

    if not filtered:
        await ctx.followup.send("No Pokémon match those filters.", ephemeral=True)
        return

    def _species_name(pid: int) -> str:
        sp = species_repo.get(pid)
        return sp.name if sp else ""

    def _sort_key(p: dict):
        if sort_key == "Level":
            return p["level"]
        if sort_key == "IV":
            return iv_avg_percent(repository.parse_ivs(p.get("ivs")))
        if sort_key == "Name":
            return (p.get("nickname") or _species_name(p["species_id"])).lower()
        if sort_key == "Species":
            return _species_name(p["species_id"]).lower()
        if sort_key == "BST":
            sp = species_repo.get(p["species_id"])
            return sp.bst if sp else 0
        return p["id"]

    filtered = sorted(filtered, key=_sort_key, reverse=order_desc)
    filtered.sort(key=lambda p: not p["favorite"])

    BOX_SIZE = 24
    boxes = [filtered[i:i + BOX_SIZE] for i in range(0, len(filtered), BOX_SIZE)]
    embeds = []
    for n, box in enumerate(boxes):
        title = f"{target.display_name}'s Pokémon ("
        title += f"{len(filtered):,}/{len(owned):,} shown)" if len(filtered) != len(owned) else f"{len(owned):,} total)"
        embed = Embed(title=title, color=COLLECTION_COLOR)
        embed.set_footer(text=f"Page {n + 1}/{len(boxes)}")
        for p in box:
            star = "\\*" if p["favorite"] else ""
            ball = POKEBALL_EMOJI.get(p.get("ball_type", "Poké Ball"), "🔴")
            g = gender_str(p.get("gender")) or ""
            if g:
                g = f"{g} "
            faint = f"\n{FAINTED} fainted" if p["fainted_at"] else "\n\u200b"
            item_str = f" {HOLDING_ITEM}" if p.get("held_item") else ""
            name_line = f"{ball} {pokedisplay_naked(p)}{star}{item_str}\t"
            value_line = f"{g}Lv.{p['level']} \u200b\u00b7\u200b `#{p['id']}`{faint}"
            embed.add_field(name=name_line[:256], value=value_line[:1024], inline=True)
        if len(box) % 3 != 0:
            for _ in range(3 - (len(box) % 3)):
                embed.add_field(name="\u200b", value="\u200b", inline=True)
        embeds.append(embed)

    await ctx.followup.send(
        embed=embeds[0], view=SimplePageView(embeds, ctx.user.id) if len(embeds) > 1 else MISSING)


# ---------------------------------------------------------------------------
# leaderboard
# ---------------------------------------------------------------------------

@pokemon_group.command(name="leaderboard", description="See who in this server has caught the most Pokémon")
@app_commands.describe(top="Number of users to show (default: 10)")
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def pokemon_leaderboard(ctx: Interaction, top: int = 10):
    member_ids = [m.id for m in ctx.guild.members if not m.bot]
    data = await repository.get_pokemon_leaderboard(member_ids)
    if not data:
        await ctx.response.send_message("No one here has caught any Pokémon yet.")
        return

    await ctx.response.defer()
    entries = [
        (uid, f"{total:,} caught", f"{shinies:,} {'shiny' if shinies == 1 else 'shinies'}")
        for uid, total, shinies in data[:top]
    ]

    try:
        # Generic (non-Pokémon) leaderboard-card rendering is Track A's
        # `core.imaging` territory, per its OWNER.md; soft-dependency, same
        # pattern as `pokemon.commands._db`.
        from core.imaging import generate_generic_leaderboard_card  # type: ignore[import-not-found]
    except ImportError:
        lines = [f"**{i + 1}.** <@{uid}> — {total} \u200b\u00b7\u200b {shiny_txt}"
                 for i, (uid, total, shiny_txt) in enumerate(entries)]
        await ctx.followup.send(
            embed=Embed(title=f"{ctx.guild.name} Pokémon Leaderboard", description="\n".join(lines),
                        color=COLLECTION_COLOR),
            allowed_mentions=AllowedMentions.none())
        return

    try:
        f = await generate_generic_leaderboard_card(ctx.guild, entries, "Pokémon Leaderboard", ctx.guild.name)
        await ctx.followup.send(file=f)
    except Exception as e:
        print(f"[Pokémon] leaderboard card error: {e}")
        await ctx.followup.send("Failed to generate leaderboard.")


# ---------------------------------------------------------------------------
# eggs
# ---------------------------------------------------------------------------

@pokemon_group.command(name="eggs", description="View your pending Pokémon eggs")
async def pokemon_eggs(ctx: Interaction):
    rows = await repository.get_user_eggs(ctx.user.id)
    if not rows:
        await ctx.response.send_message("You don't have any eggs incubating right now.", ephemeral=True)
        return
    now = datetime.now()
    lines = []
    for r in rows:
        hatch_dt = datetime.fromisoformat(r["hatch_at"])
        status = "Ready to hatch any moment now!" if hatch_dt <= now else f"Hatches <t:{int(hatch_dt.timestamp())}:R>"
        lines.append(f"🥚 Egg `#{r['id']}` — {status}")
    await ctx.response.send_message(
        embed=Embed(title=f"{ctx.user.display_name}'s Eggs", description="\n".join(lines), color=0xF7E4C1),
        ephemeral=True)


@pokemon_group.command(name="give", description="Gift one of your Pokémon to another trainer")
@app_commands.describe(pokemon="Pokémon ID, nickname, or species name")
async def pokemon_give(ctx: Interaction, user: User, pokemon: str):
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("Finish your current battle first!", ephemeral=True)
        return
    if user.id == ctx.user.id:
        await ctx.response.send_message("You can't give a Pokémon to yourself.", ephemeral=True)
        return
    if not await repository.has_starter(ctx.user.id):
        await ctx.response.send_message(
            f"You haven't started your Pokémon journey yet! Use {cmd_mention('pokemon starter')} first.",
            ephemeral=True)
        return
    if not await repository.has_starter(user.id):
        await ctx.response.send_message(
            f"{user.mention} hasn't started their Pokémon journey yet! Tell them to use "
            f"{cmd_mention('pokemon starter')} first.", ephemeral=True)
        return

    p = await resolve_owned_pokemon(ctx, pokemon)
    if not p or p["owner_id"] != ctx.user.id:
        await ctx.response.send_message(f"You don't own any Pokémon matching `{pokemon}`.", ephemeral=True)
        return
    pokemon_id = p["id"]

    flags = await repository.get_pokemon_trainer_flags(ctx.user.id)
    if flags["has_traded_away"] and len(await repository.get_user_pokemon(ctx.user.id)) <= 1:
        await respond(ctx, "You can't give away your last Pokémon!", ephemeral=True)
        return

    species = get_species_repo().get(p["species_id"])
    species_name = species.name if species else "Unknown"
    yes = Yes(
        users=[user.id],
        confirm={"embed": Embed(colour=0xFFD500, description=f"{user.mention} accepted the **{species_name}**!")},
        deny={"embed": Embed(colour=0xFF0000, description=f"{user.mention} declined the gift.")},
        allow_cancel=True, cancel_users=[ctx.user.id],
        cancel={"embed": Embed(colour=0x888888, description=f"{ctx.user.mention} retracted the gift.")},
        timeout=None)
    await respond(
        ctx, f"{user.mention}, {ctx.user.mention} wants to give you their "
        f"**{pokemoji(p)} {species_name}** (`#{pokemon_id}`)! Accept?",
        view=yes, allowed_mentions=AllowedMentions(users=True))
    await yes.wait()
    if not yes.value:
        return

    base_friendship = species.base_friendship if species else 70
    await transfer.apply_transfer_friendship(base_friendship, pokemon_id, ctx.user.id, user.id)
    await repository.transfer_pokemon(pokemon_id, ctx.user.id, user.id)
    dex_seen = await repository.get_pokedex_seen(user.id)
    dex_seen.add(p["species_id"])
    await repository.set_pokedex_seen(user.id, dex_seen)
    from pokemon.commands.milestones import check_and_notify_milestones
    await check_and_notify_milestones(ctx.client, user.id, dex_seen)

    if p.get("held_item") in MEGA_STONES:
        stone = item_display(p["held_item"])
        if await repository.has_item(user.id, p["held_item"], exclude_pokemon_id=pokemon_id):
            await repository.set_held_item(pokemon_id, None)
            await ctx.followup.send(f"⚠️ The {stone} the Pokémon was holding was destroyed!")

    await transfer.auto_add_to_team(user.id, pokemon_id)
    from pokemon.commands.evolution import maybe_trigger_trade_evolution
    await maybe_trigger_trade_evolution(ctx.client, pokemon_id)


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

"""`/pokemon breed` and the background egg-hatching loop. Ported from
`pokemon_breed` and the many small `_inherit_egg_*`/`_egg_*` helpers.

Ball inheritance here is genuinely Gen VII+ (the monolith's own
`_egg_ball_type`, which this ports) even though the rest of the bot's
battle mechanics are Gen VI -- exactly the one deliberate exception the
user's original brief called out, so it's kept as-is rather than
"corrected" to Gen VI rules. The dead, never-called `_egg_ball_type_gen6`
function isn't ported (confirmed unreferenced anywhere in the monolith).

Two things are worth knowing about this port:
`breed_ready_at`'s Naughty-nature discount is applied to the cooldown
*duration* (25% shorter wait), not the absolute ready-at timestamp -- the
monolith itself multiplied the timestamp directly, which (since
`time.time()` is on the order of 1.7 billion) put the "discounted"
ready-at decades in the past, i.e. instantly ready rather than 25% sooner.
Fixed here at the user's request; see this file's git history / the
duration-based computation in `make_egg` below for the corrected version.
And Pokédex-milestone / cross-server-achievement notifications on hatch
(`check_pokedex_milestones`, `check_automated_achievements`, generic
server "progress" tracking) are a separate, not-yet-built subsystem this
sandbox has no visibility into; the hatch loop below does the actual
Pokémon-creation/data work but skips those notification calls, flagged
inline.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime, timedelta

from discord import Embed, Interaction, app_commands
from discord.ext import tasks

from pokemon.commands import repository, transfer
from pokemon.commands.battle_state import is_in_battle
from pokemon.commands.confirm import Yes
from pokemon.commands.dex_search import resolve_species
from pokemon.commands.display import cmd_mention, pokedisplay, species_emoji
from pokemon.commands.groups import pokemon_group
from pokemon.commands.moveset import move_pp
from pokemon.commands.resolve import (
    PokemonPickView, find_pokemon_by_query, resolve_owned_pokemon, respond,
)
from pokemon.commands.spawning import NATURES, pick_ability, roll_gender, roll_ivs
from pokemon.dex.learnsets_repo import get_default_repository as get_learnsets
from pokemon.dex.species import Species, get_default_repository as get_species_repo

BREED_COOLDOWN_SECONDS = 21600  # 6h per parent
BREED_COST_BY_RARITY: dict[str, int] = {
    "common": 500, "uncommon": 1000, "rare": 3000, "epic": 5000, "legendary": 10000, "mythical": 10000,
}
_UNINHERITABLE_BALLS: frozenset[str] = frozenset({"Master Ball", "Cherish Ball"})

# canonical-keyed, unlike the monolith's shorthand -- see repository.py's
# module docstring for the canonical-stat-key rationale.
POWER_ITEM_IV_STAT: dict[str, str] = {
    "Power Weight": "hp", "Power Bracer": "attack", "Power Belt": "defense",
    "Power Lens": "special-attack", "Power Band": "special-defense", "Power Anklet": "speed",
}

_INHERITABLE_FORM_LINES: dict[int, set[int]] = {
    201: {201},
    422: {422, 423}, 423: {422, 423},
    412: {412, 413}, 413: {412, 413},
    550: {550},
    669: {669, 670, 671}, 670: {669, 670, 671}, 671: {669, 670, 671},
    710: {710, 711}, 711: {710, 711},
    585: {585, 586}, 586: {585, 586},
}
_GENDER_COUNTERPART_PAIRS: dict[int, tuple[int, int]] = {
    29: (29, 32), 30: (29, 32), 31: (29, 32),
    32: (29, 32), 33: (29, 32), 34: (29, 32),
    313: (313, 314), 314: (313, 314),
}
# base_species_id -> (required_held_item, non-baby fallback id)
_INCENSE_BABY_OVERRIDE: dict[int, tuple[str, int]] = {
    298: ("Sea Incense", 183), 360: ("Lax Incense", 202), 406: ("Rose Incense", 315),
    433: ("Pure Incense", 358), 438: ("Rock Incense", 185), 439: ("Odd Incense", 122),
    440: ("Luck Incense", 113), 458: ("Wave Incense", 226), 446: ("Full Incense", 143),
}
_MANAPHY_PHIONE_IDS: frozenset[int] = frozenset({489, 490})

_breed_notify_tasks: dict[int, asyncio.Task] = {}


def _species_for(p: dict) -> Species | None:
    return get_species_repo().get(p["species_id"])


def _is_ditto(p: dict) -> bool:
    sp = _species_for(p)
    return bool(sp and "ditto" in sp.egg_groups)


def find_base_species_id(species_id: int) -> int:
    """Walks `Species.prevo` to the root of the evolutionary line. Ported
    from `_find_base_species_id`, simplified: the monolith rebuilt a
    reverse evolution-chain map on every call since it only had
    "evolves-from" edges; `Species.prevo` already stores the reverse
    pointer directly."""
    species_repo = get_species_repo()
    current = species_id
    seen: set[int] = set()
    while current not in seen:
        seen.add(current)
        sp = species_repo.get(current)
        if not sp or sp.prevo is None:
            return current
        current = sp.prevo
    return current


def breeding_mother(mine: dict, partner: dict) -> dict | None:
    """Ported verbatim from `_breeding_mother`."""
    ditto_mine, ditto_partner = _is_ditto(mine), _is_ditto(partner)
    if ditto_mine and ditto_partner:
        return None
    if ditto_mine:
        return partner
    if ditto_partner:
        return mine
    if mine.get("gender") == "female":
        return mine
    if partner.get("gender") == "female":
        return partner
    return None


def can_breed(species_a: Species, species_b: Species, gender_a: str = "genderless",
              gender_b: str = "genderless") -> tuple[bool, str]:
    """Ported verbatim from `can_breed`."""
    a_grps, b_grps = set(species_a.egg_groups), set(species_b.egg_groups)
    if (species_a.id in _MANAPHY_PHIONE_IDS and "ditto" in b_grps) or \
       (species_b.id in _MANAPHY_PHIONE_IDS and "ditto" in a_grps):
        return True, ""
    if "no-eggs" in a_grps or "no-eggs" in b_grps:
        return False, "One of these Pokémon cannot breed (Undiscovered egg group)."
    is_ditto_a, is_ditto_b = "ditto" in a_grps, "ditto" in b_grps
    if is_ditto_a or is_ditto_b:
        return True, ""
    if not (a_grps & b_grps):
        return False, "These two don't share an egg group."
    if gender_a == "genderless" or gender_b == "genderless":
        return False, "Both Pokémon need a gender to breed (unless one is Ditto)."
    if gender_a == gender_b:
        return False, "These two are the same gender and can't breed with each other."
    return True, ""


def determine_egg_species(mine: dict, partner: dict) -> int:
    """Ported verbatim from `_determine_egg_species`."""
    if mine["species_id"] in _MANAPHY_PHIONE_IDS or partner["species_id"] in _MANAPHY_PHIONE_IDS:
        if _is_ditto(mine) or _is_ditto(partner):
            return 489  # Phione

    for parent in (mine, partner):
        base = find_base_species_id(parent["species_id"])
        pair = _GENDER_COUNTERPART_PAIRS.get(base) or _GENDER_COUNTERPART_PAIRS.get(parent["species_id"])
        if pair is not None:
            return random.choice(pair)

    mother = breeding_mother(mine, partner)
    base_id = find_base_species_id(mother["species_id"] if mother else mine["species_id"])

    incense_data = _INCENSE_BABY_OVERRIDE.get(base_id)
    if incense_data is not None:
        required, fallback = incense_data
        if mine.get("held_item") == required or partner.get("held_item") == required:
            return base_id
        return fallback

    return base_id


def egg_ball_type(mine: dict, partner: dict) -> str:
    """Gen VII+ ball inheritance -- see this module's docstring. Ported
    verbatim from `_egg_ball_type`."""
    def _clean(ball: str | None) -> str:
        ball = ball or "Poké Ball"
        return ball if ball not in _UNINHERITABLE_BALLS else "Poké Ball"

    ditto_mine, ditto_partner = _is_ditto(mine), _is_ditto(partner)
    if ditto_mine and ditto_partner:
        return "Poké Ball"
    if ditto_mine:
        return _clean(partner.get("ball_type"))
    if ditto_partner:
        return _clean(mine.get("ball_type"))

    if mine["species_id"] == partner["species_id"]:
        return _clean(random.choice((mine, partner)).get("ball_type"))
    if mine.get("gender") == "female":
        return _clean(mine.get("ball_type"))
    if partner.get("gender") == "female":
        return _clean(partner.get("ball_type"))
    return "Poké Ball"


def inherit_egg_nature(mine: dict, partner: dict) -> str:
    """Ported verbatim from `_inherit_egg_nature`."""
    mother = breeding_mother(mine, partner)
    father = partner if (mother is not None and mother is mine) else mine

    mother_holds = bool(mother and mother.get("held_item") == "Everstone" and mother.get("nature"))
    father_holds = bool(father and father.get("held_item") == "Everstone" and father.get("nature"))
    if mother_holds and father_holds:
        return random.choice((mother["nature"], father["nature"]))
    if mother_holds:
        return mother["nature"]
    if father_holds:
        return father["nature"]
    return random.choice(NATURES)


def inherit_egg_ability(mine: dict, partner: dict, child_species: Species) -> str:
    """Ported verbatim from `_inherit_egg_ability`."""
    ditto_mine, ditto_partner = _is_ditto(mine), _is_ditto(partner)
    if ditto_mine and ditto_partner:
        return pick_ability(child_species)

    if ditto_mine:
        ability_parent, ability_parent_sp = partner, _species_for(partner)
    elif ditto_partner:
        ability_parent, ability_parent_sp = mine, _species_for(mine)
    elif mine.get("gender") == "female":
        ability_parent, ability_parent_sp = mine, _species_for(mine)
    elif partner.get("gender") == "female":
        ability_parent, ability_parent_sp = partner, _species_for(partner)
    else:
        return pick_ability(child_species)

    if not ability_parent_sp:
        return pick_ability(child_species)
    parent_entry = next((a for a in ability_parent_sp.abilities if a.name == ability_parent.get("ability")), None)
    if parent_entry is None:
        return pick_ability(child_species)

    child_abilities = child_species.abilities
    if parent_entry.is_hidden:
        if random.random() < 0.60:
            hidden_pool = [a.name for a in child_abilities if a.is_hidden]
            if hidden_pool:
                return random.choice(hidden_pool)
        return pick_ability(child_species)

    if random.random() < 0.80:
        child_non_hidden = [a.name for a in child_abilities if not a.is_hidden]
        if parent_entry.name in child_non_hidden:
            return parent_entry.name
        parent_non_hidden = [a.name for a in ability_parent_sp.abilities if not a.is_hidden]
        try:
            idx = parent_non_hidden.index(parent_entry.name)
            if child_non_hidden:
                return child_non_hidden[min(idx, len(child_non_hidden) - 1)]
        except ValueError:
            pass

    return pick_ability(child_species)


def inherit_egg_ivs(mine: dict, partner: dict) -> dict[str, int]:
    """3 inherited IVs (5 with a Destiny Knot on either parent); a Power
    item on a parent guarantees that stat from that parent first. Ported
    verbatim from `_inherit_egg_ivs`."""
    ivs_a = repository.parse_ivs(mine.get("ivs"))
    ivs_b = repository.parse_ivs(partner.get("ivs"))
    num_slots = 5 if "Destiny Knot" in (mine.get("held_item"), partner.get("held_item")) else 3

    child_ivs = roll_ivs()
    guaranteed: dict[str, int] = {}
    for parent, parent_ivs in ((mine, ivs_a), (partner, ivs_b)):
        stat = POWER_ITEM_IV_STAT.get(parent.get("held_item"))
        if stat and stat not in guaranteed:
            guaranteed[stat] = parent_ivs[stat]
    child_ivs.update(guaranteed)

    remaining_slots = max(0, num_slots - len(guaranteed))
    remaining_stats = [s for s in repository.IV_STAT_KEYS if s not in guaranteed]
    for stat in random.sample(remaining_stats, min(remaining_slots, len(remaining_stats))):
        child_ivs[stat] = random.choice((ivs_a, ivs_b))[stat]
    return child_ivs


def inherit_egg_form(mine: dict, partner: dict, child_species_id: int) -> str | None:
    """Ported verbatim from `_inherit_egg_form`."""
    family = _INHERITABLE_FORM_LINES.get(child_species_id)
    if not family:
        return None

    for parent in (mine, partner):
        if parent.get("held_item") == "Everstone" and parent["species_id"] in family and parent.get("form"):
            return parent["form"]

    mother = breeding_mother(mine, partner)
    if mother and mother["species_id"] in family and mother.get("form"):
        return mother["form"]

    for parent in (mine, partner):
        if parent["species_id"] in family and parent.get("form"):
            return parent["form"]
    return None


def inherit_egg_moves(mine: dict, partner: dict, child_species: Species) -> list[dict]:
    """Ported verbatim from `_inherit_egg_moves`, reading from
    `LearnsetRepository` instead of a monolith-global species dict."""
    learnset = get_learnsets().get(child_species.id)
    base_moves = learnset.moves_at_exactly(1) or learnset.moves_at_or_below(1)
    if not base_moves:
        base_moves = ["tackle"]
    base_moves = list(dict.fromkeys(base_moves))[-4:]

    egg_move_pool = set(learnset.egg_moves)
    inherited: list[str] = []
    for parent in (mine, partner):
        if _is_ditto(parent):
            continue
        for m in json.loads(parent.get("moves") or "[]"):
            if m["name"] in egg_move_pool and m["name"] not in inherited:
                inherited.append(m["name"])

    final = list(base_moves)
    for mv in inherited:
        if mv in final:
            continue
        if len(final) < 4:
            final.append(mv)
        else:
            final.pop(0)
            final.append(mv)

    return [{"name": name, "pp": move_pp(name), "max_pp": move_pp(name)} for name in final[-4:]]


def egg_hatch_hours(species_id: int) -> int:
    """Converts PokéAPI hatch_counter cycles to real hours. Ported
    verbatim from `_egg_hatch_hours`."""
    sp = get_species_repo().get(species_id)
    cycles = sp.hatch_counter if sp else 20
    return max(2, round(cycles * 0.6))


def breed_cost_for_species(species_id: int) -> int:
    """Ported verbatim from `_breed_cost_for_species`."""
    sp = get_species_repo().get(species_id)
    rarity = sp.rarity if sp else "common"
    return BREED_COST_BY_RARITY.get(rarity, BREED_COST_BY_RARITY["common"])


def _schedule_breed_notification(bot, owner_id: int, pokemon_id: int, ready_at_ts: float) -> None:
    from pokemon.commands.collection import notify_breed_ready

    old = _breed_notify_tasks.get(pokemon_id)
    if old and not old.done():
        old.cancel()
    delay = max(0.0, ready_at_ts - time.time())
    _breed_notify_tasks[pokemon_id] = asyncio.create_task(notify_breed_ready(bot, owner_id, pokemon_id, delay))


@pokemon_group.command(name="breed", description="Breed two compatible Pokémon (yours, or yours + a partner's)")
@app_commands.describe(
    your_pokemon="Pokémon ID, nickname, or species name",
    partner_pokemon="Your Pokémon (ID/nickname/species), or another trainer's Pokémon ID")
async def pokemon_breed(ctx: Interaction, your_pokemon: str, partner_pokemon: str):
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("Finish your current battle first!", ephemeral=True)
        return

    partner_pokemon = partner_pokemon.strip()
    partner = None
    if partner_pokemon.isdigit():
        partner = await repository.get_pokemon(int(partner_pokemon))
        if not partner:
            await ctx.response.send_message("That partner Pokémon doesn't exist.", ephemeral=True)
            return
        if await repository.pokemon_is_gts_listed(partner["id"]):
            await ctx.response.send_message(
                "That Pokémon is currently listed on the GTS and can't be bred.", ephemeral=True)
            return
    else:
        matches = await find_pokemon_by_query(ctx.user.id, partner_pokemon)
        if not matches:
            await ctx.response.send_message(
                f"Couldn't find a Pokémon of yours matching `{partner_pokemon}`.\nTo breed with another "
                "trainer's Pokémon, use its specific ID number instead of a name.", ephemeral=True)
            return
        if len(matches) == 1:
            partner = matches[0]
        else:
            view = PokemonPickView(ctx.user.id, matches)
            await respond(
                ctx, f"Found **{len(matches):,}** of your Pokémon matching `{partner_pokemon}`. Which one did "
                "you mean?\nTo breed with another trainer's Pokémon, use its specific ID number instead of a "
                "name.", view=view, ephemeral=True)
            await view.wait()
            partner = view.chosen
    if not partner:
        return

    mine = await resolve_owned_pokemon(ctx, your_pokemon)
    if not mine or mine["owner_id"] != ctx.user.id:
        await respond(ctx, f"You don't own any Pokémon matching `{your_pokemon}`.", ephemeral=True)
        return

    now = time.time()
    for parent, label in ((mine, "Your Pokémon"), (partner, "That Pokémon")):
        ready_at = await repository.get_breed_cooldown(parent["id"])
        if ready_at and ready_at > now:
            await respond(ctx, f"{label} needs to rest before breeding again. Ready <t:{int(ready_at)}:R>.",
                          ephemeral=True)
            return

    cross_trainer = partner["owner_id"] != ctx.user.id
    species_repo = get_species_repo()
    sp_a, sp_b = species_repo.get(mine["species_id"]), species_repo.get(partner["species_id"])
    if not sp_a or not sp_b:
        await respond(ctx, "Unknown species data.", ephemeral=True)
        return
    ok, reason = can_breed(sp_a, sp_b, mine.get("gender", "genderless"), partner.get("gender", "genderless"))
    if not ok:
        await respond(ctx, reason, ephemeral=True)
        return
    if mine["fainted_at"]:
        await respond(ctx, "Your Pokémon can't breed while it's fainted!", ephemeral=True)
        return
    if partner["fainted_at"]:
        await respond(ctx, "That Pokémon can't breed while it's fainted!", ephemeral=True)
        return

    predicted_child = determine_egg_species(mine, partner)
    predicted_cost = breed_cost_for_species(predicted_child)
    predicted_sp = species_repo.get(predicted_child)

    balance = await repository.get_pokedollars(ctx.user.id)
    if balance < predicted_cost:
        await respond(
            ctx, f"Breeding these two would produce a **{predicted_sp.name if predicted_sp else '?'}** egg, "
            f"costing **\u20b1{predicted_cost:,}**. You only have **\u20b1{balance:,}**.", ephemeral=True)
        return

    async def make_egg(owner_id: int, my_id: int, their_id: int) -> tuple[int, int] | None:
        mine2 = await repository.get_pokemon(my_id)
        partner2 = await repository.get_pokemon(their_id)
        if not mine2 or not partner2:
            return None
        child = determine_egg_species(mine2, partner2)
        cost = breed_cost_for_species(child)
        if await repository.get_pokedollars(ctx.user.id) < cost:
            return None
        await repository.add_pokedollars(ctx.user.id, -cost)

        breed_ready_at1 = time.time() + BREED_COOLDOWN_SECONDS
        breed_ready_at2 = time.time() + BREED_COOLDOWN_SECONDS
        # Fixed at the user's request: the monolith multiplied the
        # absolute ready-at *timestamp* by 0.75 for a Naughty-natured
        # parent instead of scaling the cooldown *duration*, which (since
        # `time.time()` is ~1.7 billion) put the "discounted" ready-at
        # decades in the past -- i.e. instantly ready, not 25% sooner.
        # This applies the 25% discount to the duration instead, which is
        # what a "Naughty parents breed faster" nature bonus should mean.
        if mine2.get("nature") == "Naughty":
            breed_ready_at1 = time.time() + BREED_COOLDOWN_SECONDS * 0.75
        if partner2.get("nature") == "Naughty":
            breed_ready_at2 = time.time() + BREED_COOLDOWN_SECONDS * 0.75
        await repository.set_breed_cooldown(mine2["id"], breed_ready_at1)
        await repository.set_breed_cooldown(partner2["id"], breed_ready_at2)
        _schedule_breed_notification(ctx.client, mine2["owner_id"], mine2["id"], breed_ready_at1)
        _schedule_breed_notification(ctx.client, partner2["owner_id"], partner2["id"], breed_ready_at2)

        child_sp = species_repo.get(child)
        ball_type = egg_ball_type(mine2, partner2)
        child_ivs = inherit_egg_ivs(mine2, partner2)
        child_form = inherit_egg_form(mine2, partner2, child)
        child_moves = inherit_egg_moves(mine2, partner2, child_sp) if child_sp else []
        child_nature = inherit_egg_nature(mine2, partner2)
        child_ability = inherit_egg_ability(mine2, partner2, child_sp) if child_sp else ""
        hatch_hours = egg_hatch_hours(child)
        now2 = datetime.now()
        await repository.add_egg(
            owner_id, mine2["id"], partner2["id"], child, child_ivs, child_form, child_moves,
            now2.isoformat(), (now2 + timedelta(hours=hatch_hours)).isoformat(), ball_type, child_nature,
            child_ability, ctx.guild_id)
        return hatch_hours, cost

    if not cross_trainer:
        yes = Yes(
            users=[ctx.user.id],
            confirm={"embed": Embed(description="Breeding accepted! 💖", colour=0xFFD500)},
            deny={"embed": Embed(description="Breeding cancelled.", colour=0xFF0000)})
        await respond(
            ctx, f"Breed {pokedisplay(mine)} with {pokedisplay(partner)} for **~\u20b1{predicted_cost:,}**? "
            f"You'll receive the **{species_emoji(predicted_child)} "
            f"{predicted_sp.name if predicted_sp else '?'}** egg.", view=yes, ephemeral=True)
        await yes.wait()
        if not yes.value:
            return
        result = await make_egg(ctx.user.id, mine["id"], partner["id"])
        if result is None:
            await respond(ctx, f"You can no longer afford the **\u20b1{predicted_cost:,}** breeding fee.",
                          ephemeral=True)
            return
        hatch_hours, cost = result
        await respond(ctx, f"💖 Paid **\u20b1{cost:,}**. An egg will hatch for you in ~{hatch_hours}h.",
                      ephemeral=True)
        return

    from discord import AllowedMentions

    yes = Yes(
        users=[partner["owner_id"]],
        confirm={"embed": Embed(description="Breeding accepted! 💖", colour=0xFFD500)},
        deny={"embed": Embed(description="Declined.", colour=0xFF0000)})
    await respond(
        ctx, f"<@{partner['owner_id']}>, {ctx.user.mention} wants to breed their {pokedisplay(mine)} with your "
        f"{pokedisplay(partner)}! This will cost {ctx.user.mention} ~**\u20b1{predicted_cost:,}**. Do you accept?",
        view=yes, allowed_mentions=AllowedMentions(users=True))
    await yes.wait()
    if not yes.value:
        return

    result = await make_egg(ctx.user.id, mine["id"], partner["id"])
    if result is None:
        await respond(
            ctx, "Either the breeding fee is no longer affordable, or one of the Pokémon involved has been "
            "released and is no longer eligible for breeding.")
        return
    hatch_hours, cost = result
    await respond(
        ctx, f"💖 Paid **\u20b1{cost:,}**. An egg was conceived! It'll hatch for you in ~{hatch_hours}h.",
        allowed_mentions=AllowedMentions.none())


async def hatch_due_eggs(bot) -> None:
    """Hatches every egg whose `hatch_at` has passed. Ported from
    `egg_hatch_check`'s body (its `@tasks.loop(minutes=15)` wrapper is
    `start_egg_hatch_loop` below, so this stays independently testable/
    callable). Pokédex-milestone and cross-server-achievement
    notifications on hatch are a separate, not-yet-built subsystem -- see
    this module's docstring -- so those calls are skipped rather than
    guessed at; the Pokémon itself is still fully, correctly created and
    the Pokédex is still marked as seen.
    """
    species_repo = get_species_repo()
    for egg in await repository.get_due_eggs(datetime.now().isoformat()):
        sp = species_repo.get(egg["species_id"])
        if not sp:
            continue
        stored_moves = json.loads(egg["moves"]) if egg["moves"] else []
        shiny = random.randint(1, 4096) == 1
        from pokemon.commands.moveset import MoveSlot

        initial_moves = [MoveSlot(**m) for m in stored_moves] if stored_moves else None
        await repository.add_pokemon(
            egg["owner_id"], egg["species_id"], 1, json.loads(egg["ivs"]),
            egg.get("nature") or random.choice(NATURES),
            shiny=shiny, caught_guild_id=None, ball_type=egg["ball_type"],
            initial_moves=initial_moves, ability=egg.get("ability") or pick_ability(sp),
            gender=roll_gender(sp), form=egg.get("form"), friendship=sp.base_friendship)
        await repository.delete_egg(egg["id"])

        dex = await repository.get_pokedex_seen(egg["owner_id"])
        if egg["species_id"] not in dex:
            dex.add(egg["species_id"])
            await repository.set_pokedex_seen(egg["owner_id"], dex)
            from pokemon.commands.milestones import check_and_notify_milestones
            await check_and_notify_milestones(bot, egg["owner_id"], dex)


def start_egg_hatch_loop(bot) -> None:
    """Registers and starts the 15-minute egg-hatch loop against `bot`.
    Whoever owns bot startup should call this once after the bot is ready
    (mirroring how `auto_heal_check`/`auto_revive_check` were started in
    the monolith) -- `bot.py`'s `_load_pokemon_subsystem` is that call
    site now."""

    @tasks.loop(minutes=15)
    async def _loop():
        await hatch_due_eggs(bot)

    @_loop.before_loop
    async def _wait_ready():
        # Without this, `.start()` fires the first iteration immediately
        # -- before the client has actually logged in -- and
        # `hatch_due_eggs`'s any DM/channel-notification path would hit a
        # not-yet-connected client. Same guard `cogs/roles.py`'s
        # `_scheduled_roles_task` uses for the same reason.
        await bot.wait_until_ready()

    _loop.start()


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why. `start_egg_hatch_loop(bot)` still needs to be called
    explicitly once the bot is ready; loading this extension alone doesn't
    start the background loop."""

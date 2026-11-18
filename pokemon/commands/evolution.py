"""Actually evolving a Pokémon (species/ability/HP/move transition,
offering newly-learnable moves, Pokédex-milestone notification, the
Nincada->Shedinja special case) and checking whether a trade should
trigger one. Ported from `evolve_pokemon`/`check_trade_evolution`/
`_try_spawn_shedinja`/`_offer_move_learn`.

This was deferred out of the original `give`/`trade`/`gts fulfill` ports;
now that it exists, `transfer.apply_transfer_friendship`'s callers should
also call `maybe_trigger_trade_evolution` right after transferring
ownership -- see that function's docstring for the exact call shape.

What's still NOT here: evolution triggered by *leveling up* in battle
(`_process_level_up`/`_offer_evolution`'s level-up path) and evolution via
an item used through `/pokemon item use` (stones, King's Rock, etc.) --
both call this same `evolve_pokemon`, but the surrounding flows that
*decide* an evolution should happen (a level-up check inside the battle
engine; the item-use command itself) are still deferred, per
TRACK_B_HANDOFF_2.md.
"""

from __future__ import annotations

import asyncio
import json

from pokemon.commands import repository
from pokemon.commands.display import pokedisplay, pokedisplay_naked
from pokemon.commands.milestones import check_and_notify_milestones
from pokemon.commands.moveset import roll_initial_moveset
from pokemon.commands.spawning import pick_ability
from pokemon.commands.stats import pokemon_max_hp
from pokemon.dex.items_repo import TRADE_EVOLUTION_ITEMS
from pokemon.dex.learnsets_repo import get_default_repository as get_learnsets
from pokemon.dex.species import Species, get_default_repository as get_species_repo


async def check_item_evolution(pokemon: dict, item_name: str):
    """The evolution condition using `item_name` directly (a stone, etc.)
    should trigger for `pokemon`, or `None`. Ported verbatim from
    `check_item_evolution`."""
    from pokemon.dex.evolution import get_default_repository as get_evolution_repo

    item_slug = item_name.lower().replace(" ", "-")
    for cond in get_evolution_repo().evolutions_from(pokemon["species_id"]):
        if cond.trigger == "use-item" and cond.item == item_slug:
            return cond
    return None


async def check_trade_evolution(pokemon: dict, partner_species_id: int | None = None):
    """The evolution condition a trade should trigger for `pokemon`, or
    `None`. Ported verbatim from `check_trade_evolution`, reading from
    `EvolutionRepository` instead of a live-fetched PokeAPI chain."""
    from pokemon.dex.evolution import get_default_repository as get_evolution_repo

    held_name = pokemon.get("held_item")
    if held_name == "Everstone":
        return None
    held_slug = TRADE_EVOLUTION_ITEMS.get(held_name, {}).get("slug") if held_name else None
    for cond in get_evolution_repo().evolutions_from(pokemon["species_id"]):
        if cond.trigger != "trade":
            continue
        if cond.held_item and cond.held_item != held_slug:
            continue
        if cond.trade_species and partner_species_id != cond.trade_species:
            continue
        return cond
    return None


async def maybe_trigger_trade_evolution(bot, pokemon_id: int, partner_species_id: int | None = None) -> None:
    """Call right after a trade/gift/GTS-fulfillment transfers ownership
    of `pokemon_id`. If a trade-evolution condition is met, consumes the
    triggering held item (if any), evolves the Pokémon in the background,
    and DMs its new owner about it. Ported from the trade-evolution
    handling inlined at each of the monolith's three trade-evolution call
    sites (`give`, `trade`, `gts fulfill`) -- consolidated into one
    function since all three did the exact same three steps."""
    p = await repository.get_pokemon(pokemon_id)
    if not p:
        return
    cond = await check_trade_evolution(p, partner_species_id)
    if not cond:
        return

    async def _do_evolve():
        old_name = pokedisplay(p)
        if cond.held_item:
            await repository.set_held_item(pokemon_id, None)
        await evolve_pokemon(bot, pokemon_id, cond.to_id)
        new_sp = get_species_repo().get(cond.to_id)
        new_name = new_sp.name if new_sp else cond.to_name
        try:
            user = bot.get_user(p["owner_id"]) or await bot.fetch_user(p["owner_id"])
            await user.send(f"✨ {old_name} evolved into **{new_name}** through the magic of trading!")
        except Exception:
            pass

    asyncio.create_task(_do_evolve())


async def _offer_move_learn(bot, pokemon_id: int, owner_id: int, move_name: str) -> bool:
    """DMs the trainer that their Pokémon wants to learn `move_name`,
    auto-learning it if there's a free slot or prompting a swap (via
    `relearn.MoveLearningView`) if not. Ported verbatim from
    `_offer_move_learn`."""
    from pokemon.commands.moveset import move_summary
    from pokemon.commands.relearn import MoveLearningView

    p = await repository.get_pokemon(pokemon_id)
    if not p or p["owner_id"] != owner_id:
        return False

    summary = move_summary(move_name)
    moves = json.loads(p.get("moves") or "[]")
    if any(m["name"] == move_name for m in moves):
        return False

    if len(moves) < 4:
        moves.append({"name": move_name, "pp": summary["pp"], "max_pp": summary["pp"]})
        await repository.set_pokemon_moves(pokemon_id, moves)
        try:
            user = bot.get_user(owner_id) or await bot.fetch_user(owner_id)
            await user.send(f"{pokedisplay(p)} learned **{summary['display_name']}**!")
        except Exception:
            pass
        return True

    try:
        user = bot.get_user(owner_id) or await bot.fetch_user(owner_id)
        name = pokedisplay_naked(p)
        view = MoveLearningView(pokemon_id, name, moves, move_name, summary, require_max_pp=False)
        await user.send(
            content=f"**{name}** wants to learn **{summary['display_name']}**, but already knows 4 moves. "
            "Which one should be forgotten?",
            view=view)
        await view.wait()
        return view.learned
    except Exception:
        pass
    return False


async def try_spawn_shedinja(bot, nincada_before: dict) -> None:
    """Gen III mechanic: when Nincada evolves into Ninjask, if the trainer
    has an open party slot and a spare Poké Ball, they also receive a
    Shedinja, consuming that Poké Ball. Ported verbatim from
    `_try_spawn_shedinja`."""
    owner_id = nincada_before["owner_id"]
    if not get_species_repo().get(292):
        return
    if await repository.get_item_qty(owner_id, "Poké Ball") <= 0:
        return
    if len(await repository.get_team_slots(owner_id)) >= 6:
        return

    await repository.add_item(owner_id, "Poké Ball", -1)

    ivs = repository.parse_ivs(nincada_before.get("ivs"))
    moves = json.loads(nincada_before.get("moves") or "[]")
    initial_moves = None
    if moves:
        from pokemon.commands.moveset import MoveSlot
        initial_moves = [MoveSlot(**m) for m in moves]
    shiny = bool(nincada_before["shiny"])
    pid = await repository.add_pokemon(
        owner_id, 292, nincada_before["level"], ivs, nincada_before.get("nature") or "Hardy",
        shiny=shiny, caught_guild_id=nincada_before.get("caught_guild_id"), ball_type="Poké Ball",
        initial_moves=initial_moves, ability="wonder-guard", gender="genderless")
    await repository.set_pokemon_original_trainer(pid, nincada_before.get("original_trainer_id", owner_id))

    from pokemon.commands.transfer import auto_add_to_team
    await auto_add_to_team(owner_id, pid)

    dex = await repository.get_pokedex_seen(owner_id)
    if 292 not in dex:
        dex.add(292)
        await repository.set_pokedex_seen(owner_id, dex)
        await check_and_notify_milestones(bot, owner_id, dex)

    try:
        from pokemon.commands.display import species_emoji
        user = bot.get_user(owner_id) or await bot.fetch_user(owner_id)
        await user.send(
            f"A Poké Ball trembled just as your **{species_emoji(290, shiny)} Nincada** evolved...\n"
            f"**{species_emoji(292, shiny)} Shedinja** appeared, leaving behind an empty shell!")
    except Exception:
        pass


async def evolve_pokemon(bot, pokemon_id: int, to_species_id: int) -> None:
    """Evolves a Pokémon in place: species/ability/HP transition, keeps
    its existing moveset (evolution never resets moves in these games),
    offers any moves it's now old enough to know that it doesn't already
    have, unlocks Pokédex milestones for the newly-seen species, and
    handles the Nincada->Shedinja special case. Ported verbatim from
    `evolve_pokemon`.
    """
    species_repo = get_species_repo()
    new_sp: Species | None = species_repo.get(to_species_id)
    if not new_sp:
        return
    p = await repository.get_pokemon(pokemon_id)
    if not p:
        return
    old_sp = species_repo.get(p["species_id"])

    old_max = pokemon_max_hp(p, old_sp) if old_sp else p.get("current_hp") or 1
    new_max = pokemon_max_hp(p, new_sp)
    hp_gain = max(0, new_max - old_max)
    cur_hp = p.get("current_hp")
    new_hp = (cur_hp + hp_gain) if (cur_hp is not None and not p.get("fainted_at") and cur_hp > 0) else cur_hp

    old_abilities = old_sp.abilities if old_sp else ()
    new_abilities = new_sp.abilities
    cur_entry = next((a for a in old_abilities if a.name == p.get("ability")), None)
    if cur_entry and new_abilities:
        same_slot = [a for a in new_abilities if a.is_hidden == cur_entry.is_hidden]
        if same_slot:
            old_same = [a for a in old_abilities if a.is_hidden == cur_entry.is_hidden]
            idx = old_same.index(cur_entry) if cur_entry in old_same else 0
            new_ability = same_slot[min(idx, len(same_slot) - 1)].name
        else:
            new_ability = pick_ability(new_sp)
    else:
        new_ability = pick_ability(new_sp)

    existing = json.loads(p.get("moves") or "[]")
    if existing:
        new_moves = existing
    else:
        new_moves = [
            {"name": m.name, "pp": m.pp, "max_pp": m.max_pp}
            for m in roll_initial_moveset(to_species_id, p["level"])
        ]
    await repository.evolve_pokemon_row(pokemon_id, to_species_id, new_moves, new_hp, new_ability)

    known = {m["name"] for m in new_moves}
    learnset = get_learnsets().get(to_species_id)
    new_move_names = [
        mv for mv in learnset.moves_at_or_below(p["level"]) if mv not in known
    ] + [mv for mv in learnset.moves_at_exactly(0) if mv not in known]

    if new_move_names:
        async def _teach_evolution_moves(names: list[str]):
            for mv_name in names:
                await _offer_move_learn(bot, pokemon_id, p["owner_id"], mv_name)

        asyncio.create_task(_teach_evolution_moves(new_move_names))

    owner_id = p["owner_id"]
    dex = await repository.get_pokedex_seen(owner_id)
    if to_species_id not in dex:
        dex.add(to_species_id)
        await repository.set_pokedex_seen(owner_id, dex)
        await check_and_notify_milestones(bot, owner_id, dex)

    if p["species_id"] == 290 and to_species_id == 291:  # Nincada -> Ninjask
        asyncio.create_task(try_spawn_shedinja(bot, p))

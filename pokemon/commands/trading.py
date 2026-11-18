"""`/pokemon trade` -- a direct Pokémon-for-Pokémon trade between two
trainers. Ported from `pokemon_trade`, including trade-evolution
triggering via `pokemon.commands.evolution.maybe_trigger_trade_evolution`.
"""

from __future__ import annotations

from discord import AllowedMentions, Embed, Interaction, User, app_commands

from pokemon.commands import repository, transfer
from pokemon.commands.battle_state import is_in_battle
from pokemon.commands.confirm import Yes
from pokemon.commands.display import cmd_mention, item_display, pokedisplay
from pokemon.commands.groups import pokemon_group
from pokemon.commands.resolve import resolve_owned_pokemon, respond
from pokemon.dex.items_repo import MEGA_STONES
from pokemon.dex.species import get_default_repository as get_species_repo


@pokemon_group.command(name="trade", description="Offer one of your Pokémon in exchange for one of theirs")
@app_commands.describe(your_pokemon="Pokémon ID, nickname, or species name", their_pokemon_id="Pokémon ID")
async def pokemon_trade(ctx: Interaction, user: User, your_pokemon: str, their_pokemon_id: int):
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("Finish your current battle first!", ephemeral=True)
        return
    if user.id == ctx.user.id:
        await ctx.response.send_message("You can't trade Pokémon with yourself.", ephemeral=True)
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

    mine = await resolve_owned_pokemon(ctx, your_pokemon)
    if not mine or mine["owner_id"] != ctx.user.id:
        await ctx.response.send_message(f"You don't own any Pokémon matching `{your_pokemon}`.", ephemeral=True)
        return
    your_pokemon_id = mine["id"]

    theirs = await repository.get_pokemon(their_pokemon_id)
    if not theirs or theirs["owner_id"] != user.id:
        await respond(ctx, f"{user.display_name} doesn't own that Pokémon.", ephemeral=True)
        return

    species_repo = get_species_repo()
    mine_sp = species_repo.get(mine["species_id"])
    theirs_sp = species_repo.get(theirs["species_id"])
    mine_name = mine_sp.name if mine_sp else "?"
    theirs_name = theirs_sp.name if theirs_sp else "?"

    embed = Embed(title="Trade Proposal", color=0xF52754)
    embed.add_field(
        name=f"{ctx.user.display_name} offers",
        value=f"{pokedisplay(mine)} ({mine_name}) \u200b\u00b7\u200b Lv.{mine['level']}")
    embed.add_field(
        name=f"{user.display_name} gives",
        value=f"{pokedisplay(theirs)} ({theirs_name}) \u200b\u00b7\u200b Lv.{theirs['level']}")

    yes = Yes(
        users=[user.id],
        confirm={"embed": Embed(description="Trade accepted!", colour=0xFFD500)},
        deny={"embed": Embed(description="Trade declined.", colour=0xFF0000)},
        allow_cancel=True, cancel_users=[ctx.user.id],
        cancel={"embed": Embed(colour=0x888888, description=f"{ctx.user.mention} retracted the trade offer.")},
        timeout=None)
    await respond(ctx, user.mention, embed=embed, view=yes, allowed_mentions=AllowedMentions(users=True))
    await yes.wait()
    if not yes.value:
        return

    mine = await repository.get_pokemon(your_pokemon_id)
    theirs = await repository.get_pokemon(their_pokemon_id)
    if not mine or mine["owner_id"] != ctx.user.id or not theirs or theirs["owner_id"] != user.id:
        await respond(ctx, "This trade is no longer valid.")
        return

    base_friendship_mine = mine_sp.base_friendship if mine_sp else 70
    base_friendship_theirs = theirs_sp.base_friendship if theirs_sp else 70
    await transfer.apply_transfer_friendship(base_friendship_mine, your_pokemon_id, ctx.user.id, user.id)
    await transfer.apply_transfer_friendship(base_friendship_theirs, their_pokemon_id, user.id, ctx.user.id)
    await repository.transfer_pokemon(your_pokemon_id, ctx.user.id, user.id)
    await repository.transfer_pokemon(their_pokemon_id, user.id, ctx.user.id)

    dex1 = await repository.get_pokedex_seen(ctx.user.id)
    dex1.add(theirs["species_id"])
    await repository.set_pokedex_seen(ctx.user.id, dex1)
    dex2 = await repository.get_pokedex_seen(user.id)
    dex2.add(mine["species_id"])
    await repository.set_pokedex_seen(user.id, dex2)
    from pokemon.commands.milestones import check_and_notify_milestones
    await check_and_notify_milestones(ctx.client, ctx.user.id, dex1)
    await check_and_notify_milestones(ctx.client, user.id, dex2)
    await check_and_notify_milestones(ctx.client, user.id, dex2)

    for pid, new_owner_id in ((your_pokemon_id, user.id), (their_pokemon_id, ctx.user.id)):
        p_check = await repository.get_pokemon(pid)
        if p_check and p_check.get("held_item") in MEGA_STONES:
            stone_name = p_check["held_item"]
            if await repository.has_item(new_owner_id, stone_name, exclude_pokemon_id=pid):
                await repository.set_held_item(pid, None)
                sp = species_repo.get(p_check["species_id"])
                await ctx.followup.send(
                    f"⚠️ The {item_display(stone_name)} held by the **{sp.name if sp else '?'}** was destroyed!")

    await transfer.auto_add_to_team(user.id, your_pokemon_id)
    await transfer.auto_add_to_team(ctx.user.id, their_pokemon_id)
    from pokemon.commands.evolution import maybe_trigger_trade_evolution
    await maybe_trigger_trade_evolution(ctx.client, your_pokemon_id, partner_species_id=theirs["species_id"])
    await maybe_trigger_trade_evolution(ctx.client, their_pokemon_id, partner_species_id=mine["species_id"])

    await ctx.channel.send(f"Trade complete! {ctx.user.mention} \u2194 {user.mention}")


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

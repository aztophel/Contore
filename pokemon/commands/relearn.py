"""`/pokemon relearn` -- teach one of your Pokémon a level-up move it's
old enough to know but has forgotten, swapping out an existing move if its
moveset is already full. Ported from `pokemon_relearn` /
`_MoveRelearnView` / `_MoveLearningView`.

`_sync_keldeo_form` (Keldeo's Resolute form ties to knowing Secret Sword)
is a narrow cosmetic edge case that's a documented no-op here rather than
built out -- see the TODO on `_sync_keldeo_form_stub`.
"""

from __future__ import annotations

import json

from discord import Interaction, SelectOption, app_commands
from discord.ui import Select, View

from pokemon.commands import repository
from pokemon.commands.battle_state import is_in_battle
from pokemon.commands.display import TYPE_EMOJI, pokedisplay, pokemon_display_name
from pokemon.commands.groups import pokemon_group
from pokemon.commands.moveset import move_display_name, move_summary
from pokemon.commands.resolve import resolve_owned_pokemon, respond
from pokemon.dex.learnsets_repo import get_default_repository as get_learnsets
from pokemon.dex.species import Species, get_default_repository as get_species_repo

RELEARN_COST = 0


async def _sync_keldeo_form_stub(pokemon_id: int) -> None:
    """TODO: Keldeo (species 647) should switch to/from its "Resolute"
    form depending on whether it currently knows Secret Sword -- narrow
    enough (one species) that it's a documented gap rather than built out
    in this pass. Ported location noted from `_sync_keldeo_form`."""


def relearnable_moves(p: dict, species: Species) -> list[str]:
    """Level-up moves this Pokémon is old enough to know but doesn't
    currently have. Ported verbatim from `_relearnable_moves`, reading
    from `LearnsetRepository` (the form-specific override this used to
    check no longer applies -- forms don't carry a separate movepool; see
    `pokemon.dex.learnsets_repo`'s module docstring)."""
    learnable = get_learnsets().get(species.id).moves_at_or_below(p["level"])
    known = {m["name"] for m in json.loads(p.get("moves") or "[]")}
    return sorted({mv for mv in learnable if mv not in known})


class MoveLearningView(View):
    """"Your moveset is full -- forget one to make room?" picker. Shared
    by `relearn` today; TM teaching and the level-up learn prompt will
    want this same view later. Ported verbatim from `_MoveLearningView`."""

    def __init__(
        self, pokemon_id: int, pokemon_name: str, current_moves: list[dict],
        new_move_name: str, new_move_summary: dict, *, require_max_pp: bool = False,
    ):
        super().__init__(timeout=None)
        self.pokemon_id = pokemon_id
        self.pokemon_name = pokemon_name
        self.current_moves = current_moves
        self.new_move_name = new_move_name
        self.new_summary = new_move_summary
        self.require_max_pp = require_max_pp
        self.learned = False

        options = []
        for i, m in enumerate(current_moves):
            summary = move_summary(m["name"])
            power = summary["base_power"] if summary["base_power"] else "—"
            options.append(SelectOption(
                label=f"Forget {move_display_name(m['name'])}", value=str(i),
                emoji=TYPE_EMOJI.get(summary["type_name"], "❔"),
                description=f"\u200b\u00b7\u200b {power} BP \u200b\u00b7\u200b PP {m['pp']}/{m['max_pp']}"))
        options.append(SelectOption(label=f"Don't learn {move_display_name(new_move_name)}", value="skip"))
        sel = Select(placeholder="Replace which move?", options=options)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, ctx: Interaction):
        val = ctx.data["values"][0]
        if val == "skip":
            await ctx.response.edit_message(
                content=f"**{self.pokemon_name}** did not learn **{self.new_summary['display_name']}**.",
                embed=None, view=None)
            self.stop()
            return

        idx = int(val)
        if any(m["name"] == self.new_move_name for i, m in enumerate(self.current_moves) if i != idx):
            await ctx.response.edit_message(
                content=f"**{self.pokemon_name}** already knows **{move_display_name(self.new_move_name)}**!",
                embed=None, view=None)
            self.stop()
            return

        mv = self.current_moves[idx]
        if self.require_max_pp and mv["pp"] < mv["max_pp"]:
            await ctx.response.edit_message(
                content=f"**{move_display_name(mv['name'])}** hasn't fully recovered its PP yet, "
                        "so it can't be forgotten right now.",
                embed=None, view=None)
            self.stop()
            return

        old_move = mv["name"]
        self.current_moves[idx] = {
            "name": self.new_move_name, "pp": self.new_summary["pp"], "max_pp": self.new_summary["pp"]}
        await repository.set_pokemon_moves(self.pokemon_id, self.current_moves)
        await _sync_keldeo_form_stub(self.pokemon_id)
        await ctx.response.edit_message(
            content=f"1, 2, and... Ta-da! **{self.pokemon_name}** forgot **{move_display_name(old_move)}** "
                    f"and learned **{self.new_summary['display_name']}**!",
            embed=None, view=None)
        self.learned = True
        self.stop()


class MoveRelearnView(View):
    """Ported verbatim from `_MoveRelearnView`."""

    def __init__(self, user_id: int, pokemon_id: int, moves: list[str]):
        super().__init__(timeout=None)
        self.user_id, self.pokemon_id = user_id, pokemon_id
        options = []
        for mv in moves[:25]:
            summary = move_summary(mv)
            power = summary["base_power"] if summary["base_power"] else "—"
            options.append(SelectOption(
                label=move_display_name(mv)[:25], value=mv,
                emoji=TYPE_EMOJI.get(summary["type_name"], "❔"),
                description=f"{power} BP \u200b\u00b7\u200b {summary['category'].title()}"[:100]))
        sel = Select(placeholder="Choose a move to relearn...", options=options)
        sel.callback = self._on_select
        self.add_item(sel)

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.user_id:
            await ctx.response.send_message("This isn't your Pokémon!", ephemeral=True)
            return False
        return True

    async def _on_select(self, ctx: Interaction):
        move_name = ctx.data["values"][0]
        if await repository.get_pokedollars(self.user_id) < RELEARN_COST:
            await ctx.response.edit_message(content=f"You can no longer afford \u20b1{RELEARN_COST:,}.", view=None)
            return
        p = await repository.get_pokemon(self.pokemon_id)
        if not p or p["owner_id"] != self.user_id:
            await ctx.response.edit_message(content="That Pokémon is no longer available.", view=None)
            return
        moves = json.loads(p.get("moves") or "[]")
        if any(m["name"] == move_name for m in moves):
            await ctx.response.edit_message(content="It already knows that move!", view=None)
            return

        summary = move_summary(move_name)
        await repository.add_pokedollars(self.user_id, -RELEARN_COST)
        name = pokedisplay(p)

        if len(moves) < 4:
            moves.append({"name": move_name, "pp": summary["pp"], "max_pp": summary["pp"]})
            await repository.set_pokemon_moves(self.pokemon_id, moves)
            await _sync_keldeo_form_stub(self.pokemon_id)
            await ctx.response.edit_message(
                content=f"1, 2, and... Ta-da! {name} learned **{summary['display_name']}**!", view=None)
            return

        learn_view = MoveLearningView(self.pokemon_id, name, moves, move_name, summary, require_max_pp=True)
        await ctx.response.edit_message(
            content=f"{name} already knows 4 moves. Which one should be forgotten to make room for "
                    f"**{summary['display_name']}**?",
            view=learn_view)


@pokemon_group.command(name="relearn", description="Have one of your Pokémon relearn a previously learnable move")
@app_commands.describe(pokemon="Pokémon ID, nickname, or species name")
async def pokemon_relearn(ctx: Interaction, pokemon: str):
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("Finish your current battle first!", ephemeral=True)
        return
    p = await resolve_owned_pokemon(ctx, pokemon)
    if not p or p["owner_id"] != ctx.user.id:
        await ctx.response.send_message(f"You don't own any Pokémon matching `{pokemon}`.", ephemeral=True)
        return

    species = get_species_repo().get(p["species_id"])
    if not species:
        await respond(ctx, "Unknown species data.", ephemeral=True)
        return

    moves = relearnable_moves(p, species)
    if not moves:
        await respond(
            ctx, f"**{pokemon_display_name(p)}** doesn't have any forgotten level-up moves to relearn.",
            ephemeral=True)
        return

    balance = await repository.get_pokedollars(ctx.user.id)
    if balance < RELEARN_COST:
        await respond(
            ctx, f"The move relearner charges **\u20b1{RELEARN_COST:,}**, but you only have "
            f"**\u20b1{balance:,}**.", ephemeral=True)
        return

    view = MoveRelearnView(ctx.user.id, p["id"], moves)
    await respond(
        ctx, f"The move relearner can teach {pokedisplay(p)} any move it's forgotten"
        f"{f', for **\u20b1{RELEARN_COST:,}**' if RELEARN_COST > 0 else ''}. Which move?",
        view=view, ephemeral=True)


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

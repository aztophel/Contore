"""`/pokemon starter` -- the one-time starter-Pokémon pick every new
trainer needs before most other commands (`hunt`, `give`, ...) will work.
Ported from `_StarterGenView`/`_StarterPickView`.

The monolith's fallback message for "this starter's data isn't loaded"
pointed at a `/pokedex rebuild` command; per the user's stated plan for
this whole rewrite (PokeAPI/Showdown data now ships committed in the repo
instead of being fetched live by the bot), that command no longer exists,
so the message here doesn't reference it.
"""

from __future__ import annotations

import random
from datetime import datetime

from discord import ButtonStyle, Embed, Interaction, SelectOption
from discord.ui import Button, Select, View

from pokemon.commands import repository, transfer
from pokemon.commands.display import SHINY, POTION_EMOJI, REVIVE_EMOJI, gender_str, poketypes, species_emoji
from pokemon.commands.groups import pokemon_group
from pokemon.commands.moveset import move_display_name, roll_initial_moveset
from pokemon.commands.spawning import NATURES, roll_gender, roll_ivs
from pokemon.commands.stats import iv_avg_percent
from pokemon.dex.items_repo import POKEBALL_EMOJI
from pokemon.dex.species import Species, get_default_repository as get_species_repo

STARTER_SHINY_CHANCE = 4096

# Ported verbatim from `STARTER_INFO`.
STARTER_INFO: dict[int, dict] = {
    1: {"ids": (1, 4, 7), "names": ("Bulbasaur", "Charmander", "Squirtle"), "gen_name": "Kanto"},
    2: {"ids": (152, 155, 158), "names": ("Chikorita", "Cyndaquil", "Totodile"), "gen_name": "Johto"},
    3: {"ids": (252, 255, 258), "names": ("Treecko", "Torchic", "Mudkip"), "gen_name": "Hoenn"},
    4: {"ids": (387, 390, 393), "names": ("Turtwig", "Chimchar", "Piplup"), "gen_name": "Sinnoh"},
    5: {"ids": (495, 498, 501), "names": ("Snivy", "Tepig", "Oshawott"), "gen_name": "Unova"},
    6: {"ids": (650, 653, 656), "names": ("Chespin", "Fennekin", "Froakie"), "gen_name": "Kalos"},
}

_ROMAN = ["O", "I", "II", "III", "IV", "V", "VI"]


class StarterGenView(View):
    """Step 1: pick a generation. Ported verbatim from `_StarterGenView`."""

    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        options = [
            SelectOption(
                label=f"Generation {_ROMAN[g]} — {info['gen_name']}", value=str(g),
                description=", ".join(info["names"]))
            for g, info in STARTER_INFO.items()
        ]
        sel = Select(placeholder="Choose a generation...", options=options)
        sel.callback = self._on_gen
        self.add_item(sel)

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.user_id:
            await ctx.response.send_message("This isn't your selection!", ephemeral=True)
            return False
        return True

    async def _on_gen(self, ctx: Interaction):
        gen = int(ctx.data["values"][0])
        view = StarterPickView(self.user_id, gen, STARTER_INFO[gen])
        await ctx.response.edit_message(embed=await view.build_embed(), view=view)


class StarterPickView(View):
    """Step 2: pick one of the three starters for that generation. Ported
    verbatim from `_StarterPickView`."""

    def __init__(self, user_id: int, gen: int, info: dict):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.gen = gen
        self.info = info
        species_repo = get_species_repo()
        options = []
        for i, (sid, name) in enumerate(zip(info["ids"], info["names"])):
            sp = species_repo.get(sid)
            types = "/".join(sp.types) if sp else "?"
            bst = sp.bst if sp else "?"
            options.append(SelectOption(
                label=name, value=str(i), emoji=species_emoji(sid),
                description=f"{types} type \u200b\u00b7\u200b Base Stat Total: {bst}"))
        sel = Select(placeholder="Choose your starter...", options=options)
        sel.callback = self._on_pick
        back = Button(label="Back", style=ButtonStyle.gray, emoji="↩️")
        back.callback = self._back
        self.add_item(sel)
        self.add_item(back)

    async def build_embed(self) -> Embed:
        species_repo = get_species_repo()
        embed = Embed(
            title=f"Gen {self.gen} ({self.info['gen_name']}) Starters",
            description="Choose your partner wisely!", color=0x2ECC71)
        for sid, name in zip(self.info["ids"], self.info["names"]):
            sp = species_repo.get(sid)
            if not sp:
                embed.add_field(name=name, value="*(data not loaded)*", inline=False)
                continue
            bs = sp.base_stats
            embed.add_field(
                name=f"{species_emoji(sid)} {name}",
                value=(f"{poketypes(sp.types)}\nBST: {sp.bst} | HP {bs.get('hp', '?')} / "
                       f"Atk {bs.get('attack', '?')} / Spe {bs.get('speed', '?')}"),
                inline=False)
        return embed

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.user_id:
            await ctx.response.send_message("This isn't your selection!", ephemeral=True)
            return False
        return True

    async def _back(self, ctx: Interaction):
        view = StarterGenView(self.user_id)
        embed = Embed(
            title="Choose a Region", description="Pick which generation's starters you want to see.",
            color=0xF52754)
        await ctx.response.edit_message(embed=embed, view=view)

    async def _on_pick(self, ctx: Interaction):
        if await repository.has_starter(ctx.user.id):
            await ctx.response.send_message("You've already chosen a starter!", ephemeral=True)
            return

        idx = int(ctx.data["values"][0])
        species_id = self.info["ids"][idx]
        name = self.info["names"][idx]
        species: Species | None = get_species_repo().get(species_id)
        if not species:
            await ctx.response.send_message(
                "That starter's data isn't loaded yet -- ask a bot admin to check the Pokédex build.",
                ephemeral=True)
            return

        shiny = random.randint(1, STARTER_SHINY_CHANCE) == 1
        gender = roll_gender(species)
        ivs = roll_ivs(floor=20)  # starters get good IVs
        nature = random.choice(NATURES)
        initial_moves = roll_initial_moveset(species_id, 5)

        await repository.set_starter(ctx.user.id, species_id, datetime.now().isoformat())
        pid = await repository.add_pokemon(
            ctx.user.id, species_id, 5, ivs, nature,
            shiny=shiny, caught_guild_id=ctx.guild_id if ctx.guild else None, ball_type="Poké Ball",
            initial_moves=initial_moves, gender=gender, friendship=species.base_friendship)

        await repository.set_pokemon_favorite(ctx.user.id, pid)
        await repository.add_item(ctx.user.id, "Poké Ball", 8)
        await repository.add_item(ctx.user.id, "Potion", 5)
        await repository.add_item(ctx.user.id, "Revive", 3)
        await repository.add_pokedollars(ctx.user.id, 1500)
        await transfer.auto_add_to_team(ctx.user.id, pid)

        dex_seen = await repository.get_pokedex_seen(ctx.user.id)
        dex_seen.add(species_id)
        await repository.set_pokedex_seen(ctx.user.id, dex_seen)
        from pokemon.commands.milestones import check_and_notify_milestones
        await check_and_notify_milestones(ctx.client, ctx.user.id, dex_seen)

        if ctx.guild_id is not None:
            await repository.sync_achievement_progress(ctx.client, ctx.guild_id, ctx.user.id)

        gender_txt = gender_str(gender)
        shiny_line = "\n**It's shiny!**" if shiny else ""
        embed = Embed(
            title=f"You chose {SHINY + ' ' if shiny else ''}{name} ({gender_txt or 'Genderless'})!",
            description=(
                f"**Level 5** \u00b7 {poketypes(species.types)}\n"
                f"**IV:** {iv_avg_percent(ivs):.1f}% avg \u00b7 **Nature:** {nature}\n"
                f"**Moves:** {', '.join(move_display_name(m.name) for m in initial_moves)}{shiny_line}\n\n"
                f"You also received **8 {POKEBALL_EMOJI['Poké Ball']} Poké Balls**, "
                f"**{POTION_EMOJI} 5 Potions**, **{REVIVE_EMOJI} 3 Revives**, and **\u20b11,500**!"),
            color=0xF7B71E if shiny else 0x2ECC71)
        await ctx.response.edit_message(embed=embed, view=None)


@pokemon_group.command(name="starter", description="Pick your starter Pokémon (one-time)")
async def pokemon_starter(ctx: Interaction):
    if await repository.has_starter(ctx.user.id):
        await ctx.response.send_message("You've already chosen a starter!", ephemeral=True)
        return
    view = StarterGenView(ctx.user.id)
    embed = Embed(
        title="Choose a Region", description="Pick a generation to see its starters.", color=0xF52754)
    await ctx.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

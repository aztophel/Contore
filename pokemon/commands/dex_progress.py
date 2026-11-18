"""Two commands the monolith registered directly on `CTREE` (i.e. bare
`/pokedex` and `/poketrainer`, not nested under `/pokemon`) that no track
ever built: a paginated Pokédex-progress view and a trainer summary.
Nested here as `/pokemon profile dex` and `/pokemon profile trainer`
instead of restoring the bare top-level names -- every other Pokémon
command Track B built lives under the one `/pokemon` tree
(`pokemon/commands/groups.py`), and these two are a discoverability
improvement, not a functional change, so following that established
convention beats preserving an inconsistency the original only had for
its own historical reasons. They're under a small `profile` subgroup
rather than directly on `pokemon_group` for an unrelated, purely
mechanical reason -- see `groups.py`'s `profile_group` comment (Discord's
25-children-per-group cap).

The milestone-reward data/logic this displays progress for already exists
in `pokemon.commands.milestones` (Track B's own port, complete with the DM
notification) -- this module only adds the missing display command, not a
second copy of that system.
"""
from __future__ import annotations

from discord import Embed, Interaction, User
from discord.utils import MISSING

from pokemon.commands import repository
from pokemon.commands.display import species_emoji
from pokemon.commands.groups import profile_group
from pokemon.commands.milestones import milestone_display_lines
from pokemon.commands.paging import SimplePageView
from pokemon.dex.pokedex_builder import NATIONAL_DEX_MAX
from pokemon.dex.species import get_default_repository as get_species_repo


def _build_pokedex_embeds(target_name: str, seen: set[int]) -> list[Embed]:
    """Ported from `_build_pokedex_embeds`. Iterates the live species
    repository (Gen 1-6, `NATIONAL_DEX_MAX` species) rather than a frozen
    `POKEDEX` list, so this stays correct if the dex's scope ever grows."""
    PAGE_SIZE = 21
    species_repo = get_species_repo()
    all_species = sorted(species_repo.all(), key=lambda s: s.id)
    pages = [all_species[i:i + PAGE_SIZE] for i in range(0, len(all_species), PAGE_SIZE)] or [[]]

    embeds = []
    for n, page in enumerate(pages):
        embed = Embed(color=0x2C2F33)
        if n == 0:
            embed.title = f"{target_name}'s Pok\u00e9dex"
            embed.description = (
                f"You've caught **{len(seen):,}** out of **{len(all_species):,}** Pok\u00e9mon!\n\n"
                "**Pok\u00e9dex Entry Milestones**\n" + "\n".join(milestone_display_lines(seen)) + "\n\u200b")
        for sp in page:
            owned = sp.id in seen
            status = "\u2705 Caught!" if owned else "\u274c Not caught yet!"
            embed.add_field(name=f"{species_emoji(sp.id)} {sp.name} `#{sp.id}`", value=status, inline=True)
        start = n * PAGE_SIZE + 1
        end = min((n + 1) * PAGE_SIZE, len(all_species))
        embed.set_footer(text=f"Showing {start}\u2013{end} out of {len(all_species):,}")
        embeds.append(embed)
    return embeds


@profile_group.command(name="dex", description="View your Pok\u00e9dex, including completion milestones")
async def pokemon_dex_progress(ctx: Interaction, user: User = None):
    target = user or ctx.user
    seen = await repository.get_pokedex_seen(target.id)
    embeds = _build_pokedex_embeds(target.display_name, seen)
    await ctx.response.send_message(
        embed=embeds[0], view=SimplePageView(embeds, ctx.user.id) if len(embeds) > 1 else MISSING)


@profile_group.command(name="trainer", description="View your (or someone else's) trainer summary")
async def pokemon_trainer_card(ctx: Interaction, user: User = None):
    """A text/embed trainer summary -- NOT a port of `_generate_trainer_card`,
    which rendered a full Pillow graphic (background art, badge icons, a
    custom-font stat block). That's a real, separate art/layout task
    (closer in scope to `pokemon.ui.battle_render`'s PNG work than to
    anything else in `pokemon.commands`) rather than something to rush as
    a byproduct of a feature-inventory pass -- every stat it showed is
    still here, just as embed fields instead of a drawn card."""
    target = user or ctx.user
    await ctx.response.defer()

    dex = await repository.get_pokedex_seen(target.id)
    owned = await repository.get_user_pokemon(target.id)
    money = await repository.get_pokedollars(target.id)
    wins, losses = await repository.get_duel_record(target.id)
    team_ids = await repository.get_team_slots(target.id)
    ach_points = await _achievement_points(ctx.guild_id, target.id) if ctx.guild_id else 0
    member = ctx.guild.get_member(target.id) if ctx.guild else None
    total_species = len(get_species_repo().all()) or NATIONAL_DEX_MAX

    winrate = (wins / max(1, wins + losses)) * 100
    dex_pct = (len(dex) / max(1, total_species)) * 100
    shinies = sum(1 for p in owned if p["shiny"])

    embed = Embed(
        title=f"{target.display_name}'s Trainer Card",
        color=0xF7B71E,
        description=(
            f"**Pok\u00e9dex:** {len(dex):,}/{total_species:,} ({dex_pct:.1f}%)\n"
            f"**Pok\u00e9mon Owned:** {len(owned):,} ({shinies:,} shiny)\n"
            f"**Team Size:** {len(team_ids)}/6\n"
            f"**Pok\u00e9dollars:** \u20b1{money:,}\n"
            f"**Duel Record:** {wins:,}W \u2013 {losses:,}L ({winrate:.1f}%)\n"
            f"**Achievement Points:** {ach_points:,}"))
    if member and member.joined_at:
        embed.set_footer(text=f"Joined {member.joined_at.strftime('%b %Y')}")
    embed.set_thumbnail(url=target.display_avatar.url)
    await ctx.followup.send(embed=embed)


async def _achievement_points(guild_id: int, user_id: int) -> int:
    try:
        from cogs.achievements import calculate_total_points
    except ImportError:
        return 0
    return await calculate_total_points(guild_id, user_id)


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `POKEMON_COMMAND_MODULES` comment for why."""

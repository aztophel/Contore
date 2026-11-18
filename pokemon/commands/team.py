"""`/pokemon team ...` -- managing a trainer's active 6-Pokémon battle
team and named presets. Ported from the monolith's `team_commands` group;
behavior unchanged.

Free-function command style -- see `pokemon.commands.groups` for why.
"""

from __future__ import annotations

from discord import ButtonStyle, Embed, Interaction, SelectOption, app_commands
from discord.app_commands import Choice
from discord.ui import Button, Select, View

from pokemon.commands import repository
from pokemon.commands.battle_state import is_in_battle
from pokemon.commands.display import (
    FAINTED, HEALTHY, HOLDING_ITEM, gender_str, pokedisplay_naked, pokemoji, pokemon_display_name,
)
from pokemon.commands.groups import team_group
from pokemon.commands.resolve import resolve_owned_pokemon, respond
from pokemon.commands.stats import pokemon_max_hp
from pokemon.dex.items_repo import POKEBALL_EMOJI
from pokemon.dex.species import get_default_repository as get_species_repo

TEAM_COLOR = 0xF52754


async def get_battle_team(user_id: int) -> list[dict]:
    """A trainer's active battle team, falling back to their single
    highest-id Pokémon if they've never set one up (so a brand-new trainer
    can always field *something*). Ported verbatim from `get_battle_team`;
    used by team.py itself and, later, duel.py's roster building."""
    ids = await repository.get_team_slots(user_id)
    batch = await repository.get_pokemon_batch(ids) if ids else {}
    team = [batch[pid] for pid in ids if pid in batch and batch[pid]["owner_id"] == user_id]
    if team:
        return team
    owned = await repository.get_user_pokemon(user_id)
    return owned[:1]


def _team_line(index: int, p: dict | None, pokemon_id: int) -> str:
    if not p:
        return f"{index}. `#{pokemon_id}` *(unavailable)*"
    species = get_species_repo().get(p["species_id"])
    mhp = pokemon_max_hp(p, species) if species else p["level"]
    star = "\\*" if p["favorite"] else ""
    status = (
        f"{FAINTED} fainted" if p["fainted_at"]
        else f"{HEALTHY} **{p.get('current_hp') if p.get('current_hp') is not None else mhp}/{mhp}**")
    ball = POKEBALL_EMOJI.get(p.get("ball_type", "Poké Ball"), "🔴")
    gender = gender_str(p.get("gender"))
    item = f" {HOLDING_ITEM}" if p.get("held_item") else ""
    return (
        f"{index}. {ball} **{pokedisplay_naked(p)}{star}**{item}"
        f"{f' {gender}' if gender else ''} \u200b\u00b7\u200b Lv.{p['level']} \u200b\u00b7\u200b {status}")


async def render_team_embed(ctx: Interaction, slots: list[int]) -> Embed:
    """Ported verbatim from `_render_team_embed`."""
    lines = []
    for i, pid in enumerate(slots, 1):
        p = await repository.get_pokemon(pid)
        lines.append(_team_line(i, p, pid))
    return Embed(
        title=f"{ctx.user.display_name}'s Team ({len(slots)}/6)",
        description="\n".join(lines) or "*Empty.*", color=TEAM_COLOR)


class TeamReorderView(View):
    """Two-step picker (which Pokémon, then which position to swap it
    with) for `/pokemon team reorder`. Ported verbatim from
    `_TeamReorderView`."""

    def __init__(self, user_id: int, display_name: str, slots: list[int], pmap: dict[int, dict]):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.display_name = display_name
        self.slots = list(slots)
        self.pmap = pmap
        self.moving_idx: int | None = None
        self._step1()

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.user_id:
            await ctx.response.send_message("This isn't your team!", ephemeral=True)
            return False
        return True

    def _short_label(self, idx: int) -> str:
        pid = self.slots[idx]
        p = self.pmap.get(pid)
        name = pokemon_display_name(p) if p else f"#{pid}"
        lvl = p["level"] if p else "?"
        return f"#{idx + 1}  {name}  Lv.{lvl}"

    def _emoji(self, idx: int):
        p = self.pmap.get(self.slots[idx])
        return pokemoji(p) if p else None

    def _embed(self, note: str = "") -> Embed:
        desc = "\n".join(_team_line(i + 1, self.pmap.get(pid), pid) for i, pid in enumerate(self.slots))
        if note:
            desc += f"\n\n{note}"
        return Embed(title=f"{self.display_name}'s Team ({len(self.slots)}/6)", description=desc, color=TEAM_COLOR)

    def _step1(self):
        self.clear_items()
        sel = Select(
            placeholder="Pick a Pokémon to move...",
            options=[
                SelectOption(label=self._short_label(i)[:25], value=str(i), emoji=self._emoji(i))
                for i in range(len(self.slots))
            ])
        sel.callback = self._pick_pokemon
        self.add_item(sel)

    async def _pick_pokemon(self, ctx: Interaction):
        self.moving_idx = int(ctx.data["values"][0])
        self.clear_items()
        sel = Select(
            placeholder="Swap with which position?",
            options=[
                SelectOption(
                    label=f"Position #{i + 1}", value=str(i),
                    description=self._short_label(i)[:50], emoji=self._emoji(i))
                for i in range(len(self.slots)) if i != self.moving_idx
            ])
        sel.callback = self._pick_position
        back = Button(label="Back", style=ButtonStyle.gray, emoji="↩️")
        back.callback = self._back
        self.add_item(sel)
        self.add_item(back)
        await ctx.response.edit_message(
            embed=self._embed(f"Moving **{self._short_label(self.moving_idx)}**. Pick who to swap with."),
            view=self)

    async def _back(self, ctx: Interaction):
        self.moving_idx = None
        self._step1()
        await ctx.response.edit_message(embed=self._embed(), view=self)

    async def _pick_position(self, ctx: Interaction):
        target = int(ctx.data["values"][0])
        self.slots[self.moving_idx], self.slots[target] = self.slots[target], self.slots[self.moving_idx]
        await repository.save_team_slots(self.user_id, self.slots)
        self.moving_idx = None
        self._step1()
        await ctx.response.edit_message(
            embed=self._embed("Swapped! Pick another Pokémon to keep reordering."), view=self)


async def _team_preset_autocomplete(ctx: Interaction, current: str) -> list[Choice[str]]:
    presets = await repository.get_team_presets(ctx.user.id)
    return [Choice(name=n, value=n) for n in presets if current.lower() in n.lower()][:25]


async def _refuse_if_in_battle(ctx: Interaction) -> bool:
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("Finish your current battle first!", ephemeral=True)
        return True
    return False


@team_group.command(name="view", description="View your active battle team")
async def team_view(ctx: Interaction):
    await ctx.response.send_message(
        embed=await render_team_embed(ctx, await repository.get_team_slots(ctx.user.id)), ephemeral=True)


@team_group.command(name="add", description="Add a Pokémon to your battle team")
@app_commands.describe(pokemon="The Pokémon to add (ID, nickname, or species name)")
async def team_add(ctx: Interaction, pokemon: str):
    if await _refuse_if_in_battle(ctx):
        return
    slots = await repository.get_team_slots(ctx.user.id)
    if len(slots) >= 6:
        await ctx.response.send_message("Your team is full (6/6).", ephemeral=True)
        return
    owned = await repository.get_user_pokemon(ctx.user.id)
    candidates = [p for p in owned if p["id"] not in slots]
    if not candidates:
        await ctx.response.send_message("All your Pokémon are already on your team.", ephemeral=True)
        return
    p = await resolve_owned_pokemon(ctx, pokemon, candidates=candidates)
    if not p:
        await respond(ctx, content=f"None of your other Pokémon match `{pokemon}`.", ephemeral=True)
        return
    slots.append(p["id"])
    await repository.save_team_slots(ctx.user.id, slots)
    await repository.mark_rejoined_team(p["id"])
    await respond(ctx, embed=await render_team_embed(ctx, slots), ephemeral=True)


@team_group.command(name="remove", description="Remove a Pokémon from your battle team")
@app_commands.describe(pokemon="The Pokémon to remove (ID, nickname, or species name)")
async def team_remove(ctx: Interaction, pokemon: str):
    if await _refuse_if_in_battle(ctx):
        return
    slots = await repository.get_team_slots(ctx.user.id)
    if not slots:
        await ctx.response.send_message("Your team is empty.", ephemeral=True)
        return
    team_pokemon = [p for pid in slots if (p := await repository.get_pokemon(pid))]
    p = await resolve_owned_pokemon(ctx, pokemon, candidates=team_pokemon)
    if not p:
        await respond(ctx, content=f"None of your teammates match `{pokemon}`.", ephemeral=True)
        return
    slots.remove(p["id"])
    await repository.save_team_slots(ctx.user.id, slots)
    await repository.mark_left_team(p["id"])
    await respond(ctx, embed=await render_team_embed(ctx, slots), ephemeral=True)


@team_group.command(name="clear", description="Clear your entire battle team")
async def team_clear(ctx: Interaction):
    if await _refuse_if_in_battle(ctx):
        return
    old_slots = await repository.get_team_slots(ctx.user.id)
    await repository.save_team_slots(ctx.user.id, [])
    for pid in old_slots:
        await repository.mark_left_team(pid)
    await ctx.response.send_message(embed=await render_team_embed(ctx, []), ephemeral=True)


@team_group.command(name="reorder", description="Reorder your battle team")
async def team_reorder(ctx: Interaction):
    if await _refuse_if_in_battle(ctx):
        return
    slots = await repository.get_team_slots(ctx.user.id)
    if len(slots) < 2:
        await ctx.response.send_message("You need at least 2 Pokémon on your team to reorder.", ephemeral=True)
        return
    pmap = {pid: p for pid in slots if (p := await repository.get_pokemon(pid))}
    view = TeamReorderView(ctx.user.id, ctx.user.display_name, slots, pmap)
    await ctx.response.send_message(embed=view._embed(), view=view, ephemeral=True)


@team_group.command(name="save", description="Save your current team as a named preset")
@app_commands.describe(name="A short name for this preset (e.g. 'Rain Team')")
async def team_save_preset(ctx: Interaction, name: str):
    if await _refuse_if_in_battle(ctx):
        return
    if len(name) > 32:
        await ctx.response.send_message("Preset names must be 32 characters or fewer.", ephemeral=True)
        return
    slots = await repository.get_team_slots(ctx.user.id)
    if not slots:
        await ctx.response.send_message("Your team is empty; there is nothing to save.", ephemeral=True)
        return
    await repository.save_team_preset(ctx.user.id, name, slots)
    await ctx.response.send_message(
        f"Saved current team as preset **`{name}`** ({len(slots)} Pokémon).", ephemeral=True)


@team_group.command(name="load", description="Swap your active team to a saved preset")
@app_commands.describe(name="The preset to load")
@app_commands.autocomplete(name=_team_preset_autocomplete)
async def team_load_preset(ctx: Interaction, name: str):
    if await _refuse_if_in_battle(ctx):
        return
    slots = await repository.get_team_preset(ctx.user.id, name)
    if slots is None:
        await ctx.response.send_message(f"You don't have a preset named **`{name}`**.", ephemeral=True)
        return

    owned_ids = {p["id"] for p in await repository.get_user_pokemon(ctx.user.id)}
    valid_slots = [pid for pid in slots if pid in owned_ids][:6]
    missing = len(slots) - len(valid_slots)

    old_slots = await repository.get_team_slots(ctx.user.id)
    await repository.save_team_slots(ctx.user.id, valid_slots)
    for pid in old_slots:
        if pid not in valid_slots:
            await repository.mark_left_team(pid)
    for pid in valid_slots:
        await repository.mark_rejoined_team(pid)

    note = f"\n⚠️ {missing} Pokémon from this preset are no longer available and were skipped." if missing else ""
    await ctx.response.send_message(
        embed=await render_team_embed(ctx, valid_slots), content=f"Loaded preset **`{name}`**!{note}",
        ephemeral=True)


@team_group.command(name="presets", description="List your saved team presets")
async def team_list_presets(ctx: Interaction):
    presets = await repository.get_team_presets(ctx.user.id)
    if not presets:
        await ctx.response.send_message("You don't have any saved team presets yet.", ephemeral=True)
        return

    lines = []
    for name, slots in presets.items():
        emojis = []
        for pid in slots:
            p = await repository.get_pokemon(pid)
            emojis.append(str(pokemoji(p)) if p else "❔")
        makeup = " ".join(emojis) if emojis else "*(empty)*"
        lines.append(f"**`{name}`** — {makeup}")

    await ctx.response.send_message(
        embed=Embed(title=f"{ctx.user.display_name}'s Team Presets",
                    description="\n\n".join(lines), color=TEAM_COLOR),
        ephemeral=True)


@team_group.command(name="delete", description="Delete a saved team preset")
@app_commands.describe(name="The preset to delete")
@app_commands.autocomplete(name=_team_preset_autocomplete)
async def team_delete_preset(ctx: Interaction, name: str):
    if await repository.delete_team_preset(ctx.user.id, name):
        await ctx.response.send_message(f"Deleted preset **`{name}`**.", ephemeral=True)
    else:
        await ctx.response.send_message(f"You don't have a preset named **`{name}`**.", ephemeral=True)


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

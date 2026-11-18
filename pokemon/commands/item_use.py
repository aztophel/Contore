"""`/pokemon item use` -- the single largest item command, since almost
every consumable in the game funnels through it: evolution stones and
trade-evolution items, EV vitamins/berries, the EV Reorganizer and IV
Booster tools, Ability Capsule, PP Up/PP Max, Revive/Max Revive, the
general battle-item catalog (heal/cure/PP-restore types used outside
battle), TMs, Deoxys' Meteorite/Gracidea/Reveal Glass/Prison Bottle form
items, and Kyurem's DNA Splicers fusion. Ported from `pokemon_item_use`
and its dozen or so small supporting views/helpers.

This was deferred in earlier passes because most of its branches needed
things that didn't exist yet (`evolve_pokemon`, `form_rules`,
`MoveLearningView`'s TM-teaching reuse) -- now that those are built, this
is a mechanical port. The `/pokedex rebuild` reference in the original
DNA Splicers error message is dropped for the same reason `starter.py`'s
is: that command no longer exists in this rewrite.
"""

from __future__ import annotations

import asyncio
import json

from discord import ButtonStyle, Embed, Interaction, SelectOption, TextStyle, app_commands
from discord.app_commands import Choice
from discord.ui import Button, Modal, Select, TextInput, View, button

from pokemon.commands import repository
from pokemon.commands.battle_state import is_in_battle
from pokemon.commands.display import item_display, item_display_plural, pokedisplay, pokedisplay_naked
from pokemon.commands.evolution import check_item_evolution, evolve_pokemon
from pokemon.commands.groups import item_group
from pokemon.commands.moveset import move_display_name, move_pp
from pokemon.commands.resolve import resolve_owned_pokemon, respond
from pokemon.commands.stats import STAT_LABEL, iv_avg_percent, pokemon_max_hp
from pokemon.dex.form_rules import resolve_item_form_change
from pokemon.dex.items_repo import (
    BATTLE_ITEMS, CUSTOM_TRAINING_ITEMS, EV_BERRIES, EV_VITAMINS, EVOLUTION_ITEMS, FORM_ITEMS,
    TRADE_EVOLUTION_ITEMS,
)
from pokemon.dex.species import STAT_KEYS, get_default_repository as get_species_repo


def _move_pp_ups_cap(ups: int) -> int:
    return max(0, min(3, ups))


def _boosted_max_pp(base_pp: int, ups: int) -> int:
    ups = _move_pp_ups_cap(ups)
    return base_pp + (base_pp * ups) // 5


# ---------------------------------------------------------------------------
# DNA Splicers (Kyurem <-> Reshiram/Zekrom fusion)
# ---------------------------------------------------------------------------

class DNASplicersChoiceView(View):
    """Ported verbatim from `_DNASplicersChoiceView`."""

    def __init__(self, kyurem_id: int):
        super().__init__(timeout=None)
        self.kyurem_id = kyurem_id
        w = Button(label="White Kyurem (fuse with Reshiram)", style=ButtonStyle.primary, emoji="⬜")
        b = Button(label="Black Kyurem (fuse with Zekrom)", style=ButtonStyle.danger, emoji="⬛")
        w.callback = self._fuse_white
        b.callback = self._fuse_black
        self.add_item(w)
        self.add_item(b)

    async def _fuse_white(self, ctx: Interaction):
        await self._fuse(ctx, "kyurem-white", "White Kyurem", 643)

    async def _fuse_black(self, ctx: Interaction):
        await self._fuse(ctx, "kyurem-black", "Black Kyurem", 644)

    async def _fuse(self, ctx: Interaction, target_form: str, form_name: str, partner_sid: int):
        species = get_species_repo().get(646)
        if not species or not species.form(target_form):
            await ctx.response.edit_message(content=f"Kyurem's **{form_name}** form isn't loaded.", view=None)
            return
        user_pokemon = await repository.get_user_pokemon(ctx.user.id)
        partner = next((q for q in user_pokemon if q["species_id"] == partner_sid and not q.get("fainted_at")), None)
        if not partner:
            await ctx.response.edit_message(content="The required Pokémon couldn't be found.", view=None)
            return
        await repository.set_pokemon_form(self.kyurem_id, target_form)
        await repository.fuse_partner(self.kyurem_id, partner["id"])
        slots = await repository.get_team_slots(ctx.user.id)
        if partner["id"] in slots:
            slots.remove(partner["id"])
            await repository.save_team_slots(ctx.user.id, slots)
        pname = "Reshiram" if partner_sid == 643 else "Zekrom"
        await ctx.response.edit_message(content=f"Kyurem fused with **{pname}** into **{form_name}**!", view=None)


async def _handle_dna_splicers(ctx: Interaction, p: dict) -> tuple[bool, str]:
    """Ported verbatim from `_handle_dna_splicers`."""
    if p.get("form") in ("kyurem-white", "kyurem-black"):
        partner_id = await repository.unfuse_partner(p["id"])
        await repository.set_pokemon_form(p["id"], None)
        suffix = " The absorbed Pokémon has returned!" if partner_id else ""
        return True, f"The DNA Splicers separated Kyurem! It reverted to its original form.{suffix}"

    user_pokemon = await repository.get_user_pokemon(ctx.user.id)
    reshiram = next((q for q in user_pokemon if q["species_id"] == 643 and not q.get("fainted_at")), None)
    zekrom = next((q for q in user_pokemon if q["species_id"] == 644 and not q.get("fainted_at")), None)
    if not reshiram and not zekrom:
        return False, "DNA Splicers needs Reshiram or Zekrom in your collection to fuse with Kyurem."
    if reshiram and zekrom:
        view = DNASplicersChoiceView(p["id"])
        await ctx.response.send_message(
            "Both Reshiram and Zekrom are available. Which should Kyurem fuse with?", view=view, ephemeral=True)
        return True, ""

    partner = reshiram or zekrom
    form, pname = ("kyurem-white", "Reshiram") if reshiram else ("kyurem-black", "Zekrom")
    species = get_species_repo().get(646)
    if not species or not species.form(form):
        return False, "Kyurem's fused form data isn't loaded."

    await repository.set_pokemon_form(p["id"], form)
    await repository.fuse_partner(p["id"], partner["id"])
    slots = await repository.get_team_slots(ctx.user.id)
    if partner["id"] in slots:
        slots.remove(partner["id"])
        await repository.save_team_slots(ctx.user.id, slots)
    fname = "White" if reshiram else "Black"
    return True, f"🧬 Kyurem fused with **{pname}** into **{fname} Kyurem**! {pname} has been absorbed."


# ---------------------------------------------------------------------------
# PP Up / PP Max
# ---------------------------------------------------------------------------

class PPUpMoveSelectView(View):
    """Ported verbatim from `_PPUpMoveSelectView`."""

    def __init__(self, user_id: int, pokemon_id: int, item: str, moves: list[dict]):
        super().__init__(timeout=60)
        self.user_id, self.pokemon_id, self.item = user_id, pokemon_id, item
        options = []
        for i, mv in enumerate(moves):
            base_pp = move_pp(mv["name"])
            ups = _move_pp_ups_cap(mv.get("pp_ups", 0))
            if ups >= 3:
                continue
            target_ups = 3 if item == "PP Max" else ups + 1
            options.append(SelectOption(
                label=move_display_name(mv["name"])[:25], value=str(i),
                description=f"Max PP {mv['max_pp']} \u2192 {_boosted_max_pp(base_pp, target_ups)} "
                            f"\u200b\u00b7\u200b {ups}/3 used"[:100]))
        if not options:
            options = [SelectOption(label="No eligible moves", value="__none__")]
        sel = Select(placeholder="Choose a move...", options=options)
        sel.callback = self._on_select
        self.add_item(sel)

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.user_id:
            await ctx.response.send_message("This isn't your Pokémon!", ephemeral=True)
            return False
        return True

    async def _on_select(self, ctx: Interaction):
        val = ctx.data["values"][0]
        if val == "__none__":
            await ctx.response.edit_message(content="No eligible moves.", view=None)
            return
        idx = int(val)
        if await repository.get_item_qty(self.user_id, self.item) <= 0:
            await ctx.response.edit_message(content=f"You no longer have a {self.item}.", view=None)
            return
        p = await repository.get_pokemon(self.pokemon_id)
        if not p or p["owner_id"] != self.user_id:
            await ctx.response.edit_message(content="That Pokémon is no longer available.", view=None)
            return
        moves = json.loads(p.get("moves") or "[]")
        if idx >= len(moves):
            await ctx.response.edit_message(content="That move is no longer available.", view=None)
            return
        mv = moves[idx]
        base_pp = move_pp(mv["name"])
        cur_ups = _move_pp_ups_cap(mv.get("pp_ups", 0))
        new_ups = 3 if self.item == "PP Max" else _move_pp_ups_cap(cur_ups + 1)
        if new_ups <= cur_ups:
            await ctx.response.edit_message(content="That move already has maximum PP Ups.", view=None)
            return
        old_max = mv["max_pp"]
        new_max = _boosted_max_pp(base_pp, new_ups)
        gained = new_max - old_max
        mv["max_pp"], mv["pp"], mv["pp_ups"] = new_max, min(new_max, mv["pp"] + gained), new_ups
        await repository.add_item(self.user_id, self.item, -1)
        await repository.set_pokemon_moves(self.pokemon_id, moves)
        await ctx.response.edit_message(
            content=f"Used **{self.item}**! **{move_display_name(mv['name'])}**'s max PP rose to "
                    f"**{new_max}** ({new_ups}/3 PP Ups).", view=None)


# ---------------------------------------------------------------------------
# IV Booster
# ---------------------------------------------------------------------------

class IVBoosterView(View):
    """Lets the player spend a pool of IV points one at a time on whichever
    stats actually need it, instead of blanket-applying to all six.
    Ported verbatim from `IVBoosterView`."""

    def __init__(self, user_id: int, pokemon_id: int, pname: str, ivs: dict):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.pokemon_id = pokemon_id
        self.pname = pname
        self.ivs = dict(ivs)
        self.pool = 10
        self._build_select()

    def _build_select(self):
        for item in list(self.children):
            if isinstance(item, Select):
                self.remove_item(item)
        if self.pool > 0:
            options = [
                SelectOption(label=f"{STAT_LABEL[stat]}: {self.ivs.get(stat, 0)} \u2192 {self.ivs.get(stat, 0) + 1}",
                             value=stat)
                for stat in STAT_KEYS if self.ivs.get(stat, 0) < 31
            ]
            if options:
                sel = Select(placeholder=f"Spend a point ({self.pool} left)...", options=options)
                sel.callback = self._on_pick
                self.add_item(sel)

    def build_embed(self) -> Embed:
        lines = [f"**{STAT_LABEL[s]:<7}** {self.ivs.get(s, 0):>2}/31" for s in STAT_KEYS]
        embed = Embed(
            title=f"Distributing IV Booster points for {self.pname}",
            description="\n".join(lines) + f"\n\n**Points remaining:** {self.pool}/10", color=0xF52754)
        embed.set_footer(text="Spend each point on whichever stat needs it most, then press Confirm.")
        return embed

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.user_id:
            await ctx.response.send_message("This isn't your Pokémon!", ephemeral=True)
            return False
        return True

    async def _on_pick(self, ctx: Interaction):
        stat = ctx.data["values"][0]
        if self.pool <= 0:
            await ctx.response.send_message("No points left to spend.", ephemeral=True)
            return
        self.ivs[stat] = min(31, self.ivs.get(stat, 0) + 1)
        self.pool -= 1
        self._build_select()
        await ctx.response.edit_message(embed=self.build_embed(), view=self)

    @button(label="Confirm", style=ButtonStyle.success, emoji="✅")
    async def _confirm(self, ctx: Interaction, _: Button):
        if self.pool == 10:
            await ctx.response.send_message("You haven't spent any points yet.", ephemeral=True)
            return
        if await repository.get_item_qty(self.user_id, "IV Booster") <= 0:
            await ctx.response.edit_message(content="You no longer have an IV Booster.", embed=None, view=None)
            return
        p = await repository.get_pokemon(self.pokemon_id)
        if not p or p["owner_id"] != self.user_id:
            await ctx.response.edit_message(content="That Pokémon is no longer available.", embed=None, view=None)
            return
        await repository.add_item(self.user_id, "IV Booster", -1)
        await repository.set_pokemon_ivs(self.pokemon_id, self.ivs)
        await ctx.response.edit_message(
            content=f"🧬 {self.pname}'s IVs were updated! (Overall: {iv_avg_percent(self.ivs):.1f}%)",
            embed=None, view=None)
        self.stop()

    @button(label="Cancel", style=ButtonStyle.danger, emoji="✖️")
    async def _cancel(self, ctx: Interaction, _: Button):
        await ctx.response.edit_message(content="Cancelled \u2014 no IV Booster was used.", embed=None, view=None)
        self.stop()


# ---------------------------------------------------------------------------
# EV Reorganizer
# ---------------------------------------------------------------------------

class EVStatInputModal(Modal):
    """Ported verbatim from `_EVStatInputModal`."""

    def __init__(self, view: "EVReorganizeView", stat: str):
        super().__init__(title=f"Set {STAT_LABEL[stat]} EVs")
        self.view_ref = view
        self.stat = stat
        current = view.evs.get(stat, 0)
        other_total = view.total_used - current
        remaining_room = min(255, 510 - other_total)
        self.input = TextInput(
            label=f"{STAT_LABEL[stat]} (0-{remaining_room}, currently {current})",
            style=TextStyle.short, default=str(current), max_length=3)
        self.add_item(self.input)

    async def on_submit(self, ctx: Interaction):
        raw = self.input.value.strip()
        if not raw.isdigit():
            await ctx.response.send_message("Please enter a whole number.", ephemeral=True)
            return
        val = int(raw)
        if val < 0 or val > 255:
            await ctx.response.send_message("Each stat must be between 0 and 255.", ephemeral=True)
            return
        other_total = self.view_ref.total_used - self.view_ref.evs.get(self.stat, 0)
        if other_total + val > 510:
            room = max(0, 510 - other_total)
            await ctx.response.send_message(
                f"That would put you over the 510 total EV cap. You have **{room}** points free for this stat.",
                ephemeral=True)
            return
        self.view_ref.evs[self.stat] = val
        self.view_ref.total_used = sum(self.view_ref.evs.values())
        self.view_ref._build_select()
        await ctx.response.edit_message(embed=self.view_ref.build_embed(), view=self.view_ref)


class EVReorganizeView(View):
    """Ported verbatim from `EVReorganizeView`."""

    def __init__(self, user_id: int, pokemon_id: int, pname: str, current_evs: dict):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.pokemon_id = pokemon_id
        self.pname = pname
        self.original_evs = {k: current_evs.get(k, 0) for k in STAT_KEYS}
        self.evs = dict(self.original_evs)
        self.total_used = sum(self.evs.values())
        self._build_select()

    def _build_select(self):
        for item in list(self.children):
            if isinstance(item, Select):
                self.remove_item(item)
        options = [SelectOption(label=f"{STAT_LABEL[stat]}: {self.evs.get(stat, 0)}/255", value=stat)
                   for stat in STAT_KEYS]
        sel = Select(placeholder="Choose a stat to edit...", options=options)
        sel.callback = self._on_pick_stat
        self.add_item(sel)

    def build_embed(self) -> Embed:
        lines = []
        for stat in STAT_KEYS:
            v = self.evs.get(stat, 0)
            filled = v * 20 // 255
            bar = "\u2588" * filled + "\u2591" * (20 - filled)
            lines.append(f"**{STAT_LABEL[stat]:<7}** `{bar}` {v:>3}/255")
        remaining = 510 - self.total_used
        embed = Embed(
            title=f"Reorganizing EVs for {self.pname}",
            description="\n".join(lines) + f"\n\n**Total:** {self.total_used}/510 \u200b\u00b7\u200b "
                                            f"**Free:** {remaining}", color=0xF52754)
        embed.set_footer(text="Pick a stat below to change its value, then press Save.")
        return embed

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.user_id:
            await ctx.response.send_message("This isn't your Pokémon!", ephemeral=True)
            return False
        return True

    async def _on_pick_stat(self, ctx: Interaction):
        stat = ctx.data["values"][0]
        await ctx.response.send_modal(EVStatInputModal(self, stat))

    @button(label="Save", style=ButtonStyle.success, emoji="✅")
    async def _save(self, ctx: Interaction, _: Button):
        if await repository.get_item_qty(self.user_id, "EV Reorganizer") <= 0:
            await ctx.response.edit_message(content="You no longer have an EV Reorganizer.", embed=None, view=None)
            return
        p = await repository.get_pokemon(self.pokemon_id)
        if not p or p["owner_id"] != self.user_id:
            await ctx.response.edit_message(content="That Pokémon is no longer available.", embed=None, view=None)
            return
        if self.total_used > 510:
            await ctx.response.send_message("Total EVs can't exceed 510.", ephemeral=True)
            return
        await repository.add_item(self.user_id, "EV Reorganizer", -1)
        await repository.set_pokemon_evs(self.pokemon_id, self.evs)
        breakdown = " / ".join(f"{STAT_LABEL[k]} {v}" for k, v in self.evs.items() if v) or "all zero"
        await ctx.response.edit_message(
            content=f"{self.pname}'s EVs were saved! ({self.total_used}/510 used)\n{breakdown}",
            embed=None, view=None)
        self.stop()

    @button(label="Reset", style=ButtonStyle.gray, emoji="↩️")
    async def _reset(self, ctx: Interaction, _: Button):
        self.evs = dict(self.original_evs)
        self.total_used = sum(self.evs.values())
        self._build_select()
        await ctx.response.edit_message(embed=self.build_embed(), view=self)

    @button(label="Cancel", style=ButtonStyle.danger, emoji="✖️")
    async def _cancel(self, ctx: Interaction, _: Button):
        await ctx.response.edit_message(content="Cancelled \u2014 no changes were made.", embed=None, view=None)
        self.stop()


# ---------------------------------------------------------------------------
# The command itself
# ---------------------------------------------------------------------------

async def _item_use_autocomplete(ctx: Interaction, current: str) -> list[Choice[str]]:
    qties = await repository.get_item_qties(ctx.user.id)
    usable_catalogs = (
        BATTLE_ITEMS, EV_VITAMINS, EV_BERRIES, CUSTOM_TRAINING_ITEMS, EVOLUTION_ITEMS,
        TRADE_EVOLUTION_ITEMS, FORM_ITEMS,
    )
    names = {n for cat in usable_catalogs for n in cat} | {"DNA Splicers", "PP Up", "PP Max"}
    options = []
    for name in sorted(names):
        qty = qties.get(name, 0)
        if qty > 0 and current.lower() in name.lower():
            options.append(Choice(name=f"{name} (\u00d7{qty})", value=name))
    for name, qty in qties.items():
        if name.startswith("TM: ") and qty > 0 and current.lower() in move_display_name(name[4:]).lower():
            options.append(Choice(name=f"{move_display_name(name[4:])} (\u00d7{qty})", value=name))
    return options[:25]


@item_group.command(name="use", description="Use any item from your bag on one of your Pokémon")
@app_commands.describe(
    pokemon="The Pokémon to use the item on (ID, nickname, or species name)", item="The item to use")
@app_commands.autocomplete(item=_item_use_autocomplete)
async def item_use(ctx: Interaction, pokemon: str, item: str):
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message(
            "Finish your current battle first! Items used mid-battle go through the Bag menu there.",
            ephemeral=True)
        return

    p = await resolve_owned_pokemon(ctx, pokemon)
    if not p or p["owner_id"] != ctx.user.id:
        await ctx.response.send_message(f"You don't own any Pokémon matching `{pokemon}`.", ephemeral=True)
        return
    pokemon_id = p["id"]

    species = get_species_repo().get(p["species_id"])
    if not species:
        await respond(ctx, "Unknown species data.", ephemeral=True)
        return
    mx = pokemon_max_hp(p, species)

    item_clean = item.strip()
    all_named = (
        list(BATTLE_ITEMS) + list(EV_VITAMINS) + list(EV_BERRIES) + list(CUSTOM_TRAINING_ITEMS)
        + list(EVOLUTION_ITEMS) + list(TRADE_EVOLUTION_ITEMS) + list(FORM_ITEMS) + ["DNA Splicers"]
    )
    matched = next((n for n in all_named if n.lower() == item_clean.lower()), None)
    if not matched and item_clean.startswith("TM: "):
        matched = item_clean
    if not matched:
        await respond(ctx, "That isn't a usable item.", ephemeral=True)
        return
    item_clean = matched

    if await repository.get_item_qty(ctx.user.id, item_clean) <= 0:
        await respond(ctx, f"You have no {item_display_plural(item_clean)}.", ephemeral=True)
        return

    if item_clean == "DNA Splicers" and species.id == 646:
        ok, msg = await _handle_dna_splicers(ctx, p)
        if msg:
            await respond(ctx, msg, ephemeral=True)
        return

    if item_clean in FORM_ITEMS:
        new_form, consume, err = resolve_item_form_change(species.id, p.get("form"), p.get("status_cond"), item_clean)
        if err:
            await respond(ctx, err, ephemeral=True)
            return
        if consume:
            if await repository.get_item_qty(ctx.user.id, item_clean) <= 0:
                await respond(ctx, f"You don't have a {item_display(item_clean)}.", ephemeral=True)
                return
            await repository.add_item(ctx.user.id, item_clean, -1)
        await repository.set_pokemon_form(pokemon_id, new_form)
        new_form_obj = species.form(new_form) if new_form else None
        form_name = new_form_obj.name if new_form_obj else species.name
        msg = f"**{pokedisplay_naked(p)}** changed form to **{form_name}**!" if new_form else \
            f"**{pokedisplay_naked(p)}** reverted to its base form!"
        await respond(ctx, msg, ephemeral=True)
        return

    if item_clean in EVOLUTION_ITEMS or item_clean in TRADE_EVOLUTION_ITEMS:
        evo = await check_item_evolution(p, item_clean)
        if not evo:
            await respond(
                ctx, f"{item_display_plural(item_clean)} don't trigger an evolution for {pokedisplay(p)}.",
                ephemeral=True)
            return
        await repository.add_item(ctx.user.id, item_clean, -1)
        old_display = pokedisplay(p)
        await evolve_pokemon(ctx.client, pokemon_id, evo.to_id)
        new_sp = get_species_repo().get(evo.to_id)
        embed = Embed(
            title=f"🎉 {old_display} evolved into {new_sp.name if new_sp else evo.to_name}!", color=0xF7B71E)
        await respond(ctx, embed=embed)
        return

    if item_clean in EV_VITAMINS:
        evs = json.loads(p.get("evs") or "{}")
        stat = EV_VITAMINS[item_clean]["stat"]
        total = sum(evs.values())
        if evs.get(stat, 0) >= 100:
            await respond(ctx, f"That stat is already maxed at {evs[stat]} EVs from vitamins.", ephemeral=True)
            return
        if total >= 510:
            await respond(ctx, "This Pokémon's total EVs are already capped at 510.", ephemeral=True)
            return
        gain = min(10, 100 - evs.get(stat, 0), 510 - total)
        await repository.add_item(ctx.user.id, item_clean, -1)
        evs[stat] = evs.get(stat, 0) + gain
        await repository.set_pokemon_evs(pokemon_id, evs)
        await respond(
            ctx, f"Used {item_display(item_clean)} on {pokedisplay(p)}! {STAT_LABEL[stat]} EVs: "
            f"**{evs[stat]}/100** (Total: {sum(evs.values())}/510)", ephemeral=True)
        return

    if item_clean in EV_BERRIES:
        evs = json.loads(p.get("evs") or "{}")
        stat = EV_BERRIES[item_clean]["stat"]
        if evs.get(stat, 0) <= 0:
            await respond(ctx, "That stat has no EVs to remove.", ephemeral=True)
            return
        await repository.add_item(ctx.user.id, item_clean, -1)
        evs[stat] = max(0, evs.get(stat, 0) - 10)
        await repository.set_pokemon_evs(pokemon_id, evs)
        await respond(ctx, f"Used {item_display(item_clean)}! {STAT_LABEL[stat]} EVs now **{evs[stat]}/255**.",
                      ephemeral=True)
        return

    if item_clean == "EV Reorganizer":
        evs = json.loads(p.get("evs") or "{}")
        if sum(evs.values()) <= 0:
            await respond(ctx, f"{pokedisplay(p)} has no EVs to reorganize yet.", ephemeral=True)
            return
        view = EVReorganizeView(ctx.user.id, pokemon_id, pokedisplay_naked(p), evs)
        await respond(ctx, embed=view.build_embed(), view=view, ephemeral=True)
        return

    if item_clean == "IV Booster":
        ivs = repository.parse_ivs(p.get("ivs"))
        if all(v >= 31 for v in ivs.values()):
            await respond(ctx, "This Pokémon already has perfect IVs.", ephemeral=True)
            return
        view = IVBoosterView(ctx.user.id, pokemon_id, pokedisplay_naked(p), ivs)
        await respond(ctx, embed=view.build_embed(), view=view, ephemeral=True)
        return

    if item_clean == "Ability Capsule":
        non_hidden = [a.name for a in species.abilities if not a.is_hidden]
        current_ability = p.get("ability")
        if len(non_hidden) < 2:
            await respond(ctx, f"{pokedisplay(p)} doesn't have a second standard Ability to switch to.",
                          ephemeral=True)
            return
        if current_ability not in non_hidden:
            await respond(ctx, f"{pokedisplay(p)}'s current Ability can't be swapped with an Ability Capsule.",
                          ephemeral=True)
            return
        new_ability = next(a for a in non_hidden if a != current_ability)
        await repository.add_item(ctx.user.id, item_clean, -1)
        await repository.set_pokemon_ability(pokemon_id, new_ability)
        await respond(
            ctx, f"Used {item_display(item_clean)} on {pokedisplay(p)}! Its Ability changed from "
            f"**{current_ability.replace('-', ' ').title()}** to **{new_ability.replace('-', ' ').title()}**.",
            ephemeral=True)
        return

    if item_clean in ("PP Up", "PP Max"):
        moves = json.loads(p.get("moves") or "[]")
        if not moves:
            await respond(ctx, "This Pokémon has no moves to upgrade.", ephemeral=True)
            return
        view = PPUpMoveSelectView(ctx.user.id, pokemon_id, item_clean, moves)
        await respond(ctx, f"Which move should receive the **{item_clean}**?", view=view, ephemeral=True)
        return

    if item_clean in ("Revive", "Max Revive"):
        if not p["fainted_at"]:
            await respond(ctx, "That Pokémon hasn't fainted.", ephemeral=True)
            return
        new_hp = None if item_clean == "Max Revive" else mx // 2
        await repository.add_item(ctx.user.id, item_clean, -1)
        if new_hp is None or new_hp >= mx:
            await repository.set_hurt_at(pokemon_id, None)
        await repository.revive_pokemon(pokemon_id, new_hp)
        await respond(
            ctx, f"Used a {item_display(item_clean)}! {pokedisplay(p)} revived with "
            f"**{new_hp if new_hp is not None else mx}/{mx}** HP.", ephemeral=True)
        return

    if item_clean in BATTLE_ITEMS:
        if p["fainted_at"]:
            await respond(
                ctx, f"{item_display(item_clean)} can't be used on a fainted Pokémon. Use a Revive or Max Revive "
                "instead.", ephemeral=True)
            return
        d = BATTLE_ITEMS[item_clean]
        if d.get("type") not in ("heal", "full", "cure", "pp", "pp_all"):
            await respond(ctx, f"{item_display(item_clean)} can't be used outside of battle.", ephemeral=True)
            return
        cur = p["current_hp"] if p["current_hp"] is not None else mx
        status = p.get("status_cond")
        if d["type"] in ("heal", "full") and cur >= mx and not (d["type"] == "full" and status):
            await respond(ctx, "Already at full HP.", ephemeral=True)
            return
        if d["type"] == "cure" and status != d["cures"] and d["cures"] != "all":
            await respond(ctx, f"{item_display(item_clean)} does not cure **{status.title()}**.", ephemeral=True)
            return

        await repository.add_item(ctx.user.id, item_clean, -1)
        parts = []
        moves = json.loads(p.get("moves") or "[]")
        if d["type"] in ("heal", "full"):
            healed = min(d["hp"], mx - cur)
            cur = min(mx, cur + d["hp"])
            parts.append(f"restored **{healed:,}** HP")
            if d["type"] == "full" and status:
                status = None
                parts.append("cured all status conditions")
        elif d["type"] == "cure":
            status = None
            parts.append(f"cured {'all status conditions' if d['cures'] == 'all' else d['cures']}")
        elif d["type"] == "pp":
            if not moves:
                await respond(ctx, f"{pokedisplay(p)} has no moves.", ephemeral=True)
                return
            mv = min(moves, key=lambda m: m["pp"])
            mv["pp"] = min(mv["max_pp"], mv["pp"] + d["pp"])
            parts.append(f"restored PP to **{move_display_name(mv['name'])}**")
            await repository.set_pokemon_moves(pokemon_id, moves)
            await repository.set_pp_reset(pokemon_id, None)
        elif d["type"] == "pp_all":
            for mv in moves:
                mv["pp"] = min(mv["max_pp"], mv["pp"] + d["pp"])
            parts.append("restored PP to all moves")
            await repository.set_pokemon_moves(pokemon_id, moves)
            await repository.set_pp_reset(pokemon_id, None)

        if cur >= mx:
            await repository.set_hurt_at(pokemon_id, None)
        await repository.set_pokemon_hp_status(pokemon_id, cur, status)
        await respond(ctx, f"Used {item_display(item_clean)} on {pokedisplay(p)}. "
                     + (", ".join(parts)).capitalize() + ".", ephemeral=True)
        return

    if item_clean.startswith("TM: "):
        move_name = item_clean[4:]

        async def handle_move_learn():
            from pokemon.commands.evolution import _offer_move_learn
            learned = await _offer_move_learn(ctx.client, pokemon_id, ctx.user.id, move_name)
            if learned:
                await repository.add_item(ctx.user.id, item_clean, -1)

        await respond(
            ctx, f"Used **{move_display_name(move_name)}** on {pokedisplay(p)}.", ephemeral=True)
        asyncio.create_task(handle_move_learn())
        return

    await respond(ctx, f"{item_display(item_clean)} can't be used this way.", ephemeral=True)


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

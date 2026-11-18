"""`/pokemon item give`/`take` (held items), `/pokemon bag`, `/pokemon
shop`. Ported from the monolith's `item_commands` group plus the `bag`/
`shop` top-level commands and `ShopView`.

`/pokemon item use` (teaching TMs, evolution stones, EV vitamins/berries,
IV Booster, Ability Capsule, form items, DNA Splicers, ...) is NOT in this
file -- it's the single largest remaining command, and most of its
branches need either `data/moves_data.json` (which doesn't exist in this
sandbox -- the PokeAPI enrichment pass needs network access this sandbox
never had) or a live `FighterState`/evolution-trigger check that belongs
with the battle engine. See TRACK_B_HANDOFF_2.md for the exact remaining
scope. The shop's TM sub-menu has the same `data/moves_data.json`
dependency and degrades gracefully (see `_render_tms` below) rather than
being left half-built.

Free-function command style -- see `pokemon.commands.groups` for why.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from discord import ButtonStyle, Embed, Interaction, SelectOption, app_commands
from discord.app_commands import Choice
from discord.ui import Button, Select, View

from pokemon.commands import repository
from pokemon.commands.battle_state import is_in_battle
from pokemon.commands.display import item_display
from pokemon.commands.groups import item_group, pokemon_group
from pokemon.commands.resolve import resolve_owned_pokemon, respond
from pokemon.dex.form_rules import resolve_held_item_form
from pokemon.dex.items_repo import (
    ARCEUS_PLATES, EVOLUTION_ITEMS, FORM_ITEMS, GENESECT_DRIVES, HELD_ITEMS, MEGA_STONES,
    SHOP_CATEGORIES, TRADE_EVOLUTION_ITEMS,
)

BAG_CATEGORIES: list[str] = [c for c, _ in SHOP_CATEGORIES if c != "TMs"]


def _bag_category_items(category: str) -> dict[str, dict]:
    """What `/pokemon bag <category>` shows -- the *display* view (no
    price filtering). Ported verbatim from `_bag_category_items`."""
    from pokemon.dex.items_repo import BATTLE_ITEMS, CUSTOM_TRAINING_ITEMS, EV_BERRIES, EV_VITAMINS, POKEBALL_DATA

    if category == "Poké Balls":
        return POKEBALL_DATA
    if category == "Battle Items":
        return BATTLE_ITEMS
    if category == "Held Items":
        return HELD_ITEMS
    if category == "Evolution Items":
        return {**EVOLUTION_ITEMS, **TRADE_EVOLUTION_ITEMS}
    if category == "Training Items":
        return {**EV_VITAMINS, **EV_BERRIES, **CUSTOM_TRAINING_ITEMS}
    if category == "Mega Stones":
        return MEGA_STONES
    if category == "Miscellaneous":
        return {**FORM_ITEMS, **{k: HELD_ITEMS[k] for k in ("Blue Orb", "Red Orb", "Griseous Orb")},
                **ARCEUS_PLATES, **GENESECT_DRIVES}
    return {}


def _shop_category_items(category: str) -> dict[str, dict]:
    """What `/pokemon shop` actually sells -- restricted to items with a
    `price` set (some categories overlap with `_bag_category_items` but
    exclude non-purchasable entries, e.g. quest-only items). Ported
    verbatim from `_category_items`."""
    from pokemon.dex.items_repo import BATTLE_ITEMS, CUSTOM_TRAINING_ITEMS, EV_BERRIES, EV_VITAMINS, POKEBALL_DATA

    if category == "Poké Balls":
        return {n: d for n, d in POKEBALL_DATA.items() if d.get("price") is not None}
    if category == "Battle Items":
        return {n: d for n, d in BATTLE_ITEMS.items() if d.get("price") is not None}
    if category == "Held Items":
        return {n: {"price": d["price"], "desc": d["desc"], "emoji": d.get("emoji", "🎒")}
                for n, d in HELD_ITEMS.items() if d.get("price") is not None}
    if category == "Evolution Items":
        return {n: {"price": d["price"], "desc": d["desc"], "emoji": d.get("emoji", "🔮")}
                for n, d in {**EVOLUTION_ITEMS, **TRADE_EVOLUTION_ITEMS}.items()}
    if category == "Training Items":
        merged = {}
        for src, emoji_fallback in ((EV_VITAMINS, "💊"), (EV_BERRIES, "🍒"), (CUSTOM_TRAINING_ITEMS, "🧬")):
            for n, d in src.items():
                merged[n] = {"price": d["price"], "desc": d["desc"], "emoji": d.get("emoji", emoji_fallback)}
        return merged
    if category == "Miscellaneous":
        return {**FORM_ITEMS, **{k: HELD_ITEMS[k] for k in ("Blue Orb", "Red Orb", "Griseous Orb")},
                **ARCEUS_PLATES, **GENESECT_DRIVES}
    return {}


async def _usable_mega_stones(user_id: int) -> list[str]:
    """Mega Stones not already sitting in the bag or held by a Pokémon.
    Ported verbatim from `_usable_mega_stones` -- including the fact that
    its species-eligibility filter is commented out in the original (every
    stone the trainer doesn't already have is "usable," regardless of
    whether they own the matching species). That's the monolith's actual
    shipped behavior, not an oversight in this port, so it's preserved
    rather than "fixed."
    """
    owned = await repository.get_user_pokemon(user_id)
    held_counts: dict[str, int] = defaultdict(int)
    for p in owned:
        if p.get("held_item") in MEGA_STONES:
            held_counts[p["held_item"]] += 1

    usable = []
    for name in MEGA_STONES:
        already_have = await repository.get_item_qty(user_id, name) + held_counts.get(name, 0)
        if already_have > 0:
            continue
        usable.append(name)
    return usable


# ---------------------------------------------------------------------------
# item give / take (held items)
# ---------------------------------------------------------------------------

async def held_item_autocomplete(ctx: Interaction, current: str) -> list[Choice[str]]:
    """Ported verbatim from `held_item_autocomplete`."""
    qties = await repository.get_item_qties(ctx.user.id)
    all_holdable = {**HELD_ITEMS, **MEGA_STONES, **TRADE_EVOLUTION_ITEMS, **ARCEUS_PLATES, **GENESECT_DRIVES}
    options = []
    for name in all_holdable:
        qty = qties.get(name, 0)
        if qty > 0 and current.lower() in name.lower():
            options.append(Choice(name=f"{name} (\u00d7{qty})", value=name))
    return options[:25]


async def _apply_held_item_form(pokemon_id: int, held_item: str | None) -> None:
    p_after = await repository.get_pokemon(pokemon_id)
    if not p_after:
        return
    new_form = resolve_held_item_form(p_after["species_id"], held_item, p_after.get("form"), in_battle=False)
    if new_form != p_after.get("form"):
        await repository.set_pokemon_form(pokemon_id, new_form)


@item_group.command(name="give", description="Give one of your Pokémon an item to hold")
@app_commands.describe(pokemon="The Pokémon to give the item to (ID, nickname, or species name)", item="The item to give")
@app_commands.autocomplete(item=held_item_autocomplete)
async def item_give(ctx: Interaction, pokemon: str, item: str):
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("You can't give items to your Pokémon mid-battle.", ephemeral=True)
        return
    p = await resolve_owned_pokemon(ctx, pokemon)
    if not p or p["owner_id"] != ctx.user.id:
        await ctx.response.send_message(f"You don't own any Pokémon matching `{pokemon}`.", ephemeral=True)
        return
    pokemon_id = p["id"]

    all_holdable = {**HELD_ITEMS, **MEGA_STONES, **TRADE_EVOLUTION_ITEMS, **ARCEUS_PLATES, **GENESECT_DRIVES}
    matched = next((n for n in all_holdable if n.lower() == item.lower()), None)
    if not matched:
        await respond(ctx, "That item doesn't exist or can't be held.", ephemeral=True)
        return
    if await repository.get_item_qty(ctx.user.id, matched) <= 0:
        await respond(ctx, f"You don't have a{'n' if matched[0].lower() in 'aeiou' else ''} {item_display(matched)} in your bag.", ephemeral=True)
        return

    current = p.get("held_item")
    await repository.add_item(ctx.user.id, matched, -1)
    if current:
        await repository.add_item(ctx.user.id, current, 1)
    await repository.set_held_item(pokemon_id, matched)
    await _apply_held_item_form(pokemon_id, matched)

    from pokemon.commands.display import pokedisplay
    await respond(
        ctx, f"{pokedisplay(p)} is now holding {item_display(matched)}."
        + (f" (returned the {item_display(current)} to your bag)" if current else ""),
        ephemeral=True)


@item_group.command(name="take", description="Take the held item back from one of your Pokémon")
@app_commands.describe(pokemon="The Pokémon to take the item from (ID, nickname, or species name)")
async def item_take(ctx: Interaction, pokemon: str):
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("You can't take items from your Pokémon mid-battle.", ephemeral=True)
        return
    p = await resolve_owned_pokemon(ctx, pokemon)
    if not p or p["owner_id"] != ctx.user.id:
        await ctx.response.send_message(f"You don't own any Pokémon matching `{pokemon}`.", ephemeral=True)
        return
    pokemon_id = p["id"]

    current = p.get("held_item")
    from pokemon.commands.display import pokedisplay
    if not current:
        await respond(ctx, f"{pokedisplay(p)} isn't holding anything.", ephemeral=True)
        return
    await repository.add_item(ctx.user.id, current, 1)
    await repository.set_held_item(pokemon_id, None)
    await _apply_held_item_form(pokemon_id, None)
    await respond(ctx, f"Took the {item_display(current)} back from {pokedisplay(p)}.", ephemeral=True)


# ---------------------------------------------------------------------------
# bag
# ---------------------------------------------------------------------------

@pokemon_group.command(name="bag", description="View the items in your bag")
@app_commands.describe(category="Only show one category (leave blank to show everything you own)")
@app_commands.choices(category=[Choice(name=c, value=c) for c in
                                 ("Poké Balls", "Battle Items", "Held Items", "Evolution Items",
                                  "TMs", "Training Items", "Mega Stones", "Miscellaneous")])
async def pokemon_bag(ctx: Interaction, category: Choice[str] | None = None):
    qties = await repository.get_item_qties(ctx.user.id)
    owned = {name: qty for name, qty in qties.items() if qty > 0}
    if not owned:
        await ctx.response.send_message("Your bag is empty.", ephemeral=True)
        return

    cat_value = category.value if category else None
    embed = Embed(title=f"{ctx.user.display_name}'s Bag", color=0xF52754)
    shown_any = False
    for cat in ([cat_value] if cat_value else BAG_CATEGORIES):
        cat_items = _bag_category_items(cat)
        lines = [
            f"**{cat_items[name].get('emoji', '❔')} {name}** \u00d7{qty}"
            for name, qty in sorted(owned.items()) if name in cat_items and qty > 0
        ]
        if not lines:
            continue
        shown_any = True
        chunk, size = [], 0
        for line in lines:
            if size + len(line) + 1 > 1000 and chunk:
                embed.add_field(name=cat, value="\n".join(chunk), inline=False)
                chunk, size = [], 0
            chunk.append(line)
            size += len(line) + 1
        if chunk:
            embed.add_field(name=cat, value="\n".join(chunk), inline=False)

    if not cat_value or cat_value == "TMs":
        tm_owned = {n: q for n, q in owned.items() if n.startswith("TM: ")}
        if tm_owned:
            shown_any = True
            try:
                from pokemon.commands.moveset import get_move_registry
                registry = get_move_registry()
                tm_lines = [f"**\u00b7 {n[4:].replace('-', ' ').title()}** \u00d7{q}" for n, q in sorted(tm_owned.items())]
            except Exception:
                tm_lines = [f"**\u00b7 {n[4:].replace('-', ' ').title()}** \u00d7{q}" for n, q in sorted(tm_owned.items())]
            embed.add_field(name="TMs", value="\n".join(tm_lines)[:1000], inline=False)

    if not shown_any:
        msg = f"You don't have any {cat_value.lower()}." if cat_value else "Your bag is empty."
        await ctx.response.send_message(msg, ephemeral=True)
        return

    balance = await repository.get_pokedollars(ctx.user.id)
    embed.set_footer(text=f"Balance: \u20b1{balance:,}")
    await ctx.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# shop
# ---------------------------------------------------------------------------

class ShopView(View):
    """Ported from `ShopView`. The TM sub-menu (`_render_tms`/`_buy_tm`)
    needs move display data (`data/moves_data.json`) this sandbox never
    built; it shows a clear "not available yet" state instead of a
    half-working move list -- see this module's docstring."""

    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.category: str | None = None
        self.page = 0
        self.purchase_log: list[list] = []
        self._show_categories()

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.user_id:
            await ctx.response.send_message("This isn't your shop menu!", ephemeral=True)
            return False
        return True

    def _log_purchase(self, display: str, price: int) -> None:
        if self.purchase_log and self.purchase_log[-1][0] == display:
            self.purchase_log[-1][1] += 1
            self.purchase_log[-1][2] += price
        else:
            self.purchase_log.append([display, 1, price])

    def _log_content(self) -> str | None:
        if not self.purchase_log:
            return None
        lines = []
        for display, count, total_price in self.purchase_log[-10:]:
            if count > 1:
                lines.append(f"Bought **{display}** \u00d7{count} for **\u20b1{total_price:,}**.")
            else:
                lines.append(f"Bought **{display}** for **\u20b1{total_price:,}**.")
        return "\n".join(lines)

    async def render_embed(self) -> Embed:
        balance = await repository.get_pokedollars(self.user_id)
        embed = Embed(title="Pokémon Shop", color=0xF7B71E, description="Pick a category below to browse and buy.")
        embed.set_footer(text=f"Your balance: \u20b1{balance:,}")
        return embed

    def _show_categories(self):
        self.clear_items()
        sel = Select(placeholder="Browse a category...",
                     options=[SelectOption(label=c, value=c, emoji=e) for c, e in SHOP_CATEGORIES])
        sel.callback = self._on_category
        self.add_item(sel)

    def _back_button(self) -> Button:
        back = Button(label="Back", style=ButtonStyle.gray, emoji="↩️")

        async def _cb(ctx: Interaction):
            self.page = 0
            self._show_categories()
            await ctx.response.edit_message(content=self._log_content(), embed=await self.render_embed(), view=self)

        back.callback = _cb
        return back

    def _add_pager(self, total_pages: int):
        prev_btn = Button(label="◀", style=ButtonStyle.gray, disabled=self.page <= 0)
        next_btn = Button(label="▶", style=ButtonStyle.gray, disabled=self.page >= total_pages - 1)

        async def _prev(ctx: Interaction):
            self.page -= 1
            await self._render_category(ctx)

        async def _next(ctx: Interaction):
            self.page += 1
            await self._render_category(ctx)

        prev_btn.callback, next_btn.callback = _prev, _next
        self.add_item(prev_btn)
        self.add_item(next_btn)

    async def _on_category(self, ctx: Interaction):
        self.category = ctx.data["values"][0]
        self.page = 0
        await self._render_category(ctx)

    async def _render_category(self, ctx: Interaction):
        if self.category == "Mega Stones":
            await self._render_mega_stones(ctx)
            return
        if self.category == "TMs":
            await self._render_tms(ctx)
            return

        items = _shop_category_items(self.category)
        names = list(items.keys())
        pages = [names[i:i + 24] for i in range(0, len(names), 24)] or [[]]
        page = pages[min(self.page, len(pages) - 1)]
        self.clear_items()
        options = [
            SelectOption(label=n[:25], value=n,
                         description=f"{items[n].get('desc', '')} \u200b\u00b7\u200b \u20b1{items[n]['price']:,}"[:100],
                         emoji=items[n].get("emoji", "🛍️"))
            for n in page
        ] or [SelectOption(label="Nothing here yet", value="__none__")]
        sel = Select(placeholder=f"Buy from {self.category}...", options=options)
        sel.callback = self._buy_simple
        self.add_item(sel)
        if len(pages) > 1:
            self._add_pager(len(pages))
        self.add_item(self._back_button())
        await ctx.response.edit_message(content=self._log_content(), embed=await self.render_embed(), view=self)

    async def _render_mega_stones(self, ctx: Interaction):
        usable = await _usable_mega_stones(self.user_id)
        self.clear_items()
        if not usable:
            self.add_item(self._back_button())
            await ctx.response.edit_message(
                content=(self._log_content() or "") + "\nYou already own every Mega Stone.",
                embed=await self.render_embed(), view=self)
            return
        pages = [usable[i:i + 24] for i in range(0, len(usable), 24)]
        page = pages[min(self.page, len(pages) - 1)]
        options = [
            SelectOption(label=name[:25], value=name, description=f"\u20b1{MEGA_STONES[name]['price']:,}"[:100],
                         emoji=MEGA_STONES[name].get("emoji", "💎"))
            for name in page
        ]
        sel = Select(placeholder="Choose a Mega Stone...", options=options)
        sel.callback = self._buy_simple
        self.add_item(sel)
        if len(pages) > 1:
            self._add_pager(len(pages))
        self.add_item(self._back_button())
        await ctx.response.edit_message(content=self._log_content(), embed=await self.render_embed(), view=self)

    async def _render_tms(self, ctx: Interaction):
        self.clear_items()
        self.add_item(self._back_button())
        await ctx.response.edit_message(
            content=(self._log_content() or "")
            + "\n-# TMs aren't available in the shop yet -- move data hasn't been loaded.",
            embed=await self.render_embed(), view=self)

    async def _buy_simple(self, ctx: Interaction):
        item_name = ctx.data["values"][0]
        if item_name == "__none__":
            await ctx.response.defer()
            return
        d = MEGA_STONES.get(item_name) if self.category == "Mega Stones" else _shop_category_items(self.category).get(item_name)
        if not d:
            await ctx.response.send_message("That item is no longer available.", ephemeral=True)
            return
        price = d["price"]
        balance = await repository.get_pokedollars(self.user_id)
        if balance < price:
            await ctx.response.send_message(f"You need **\u20b1{price:,}** but only have **\u20b1{balance:,}**.", ephemeral=True)
            return
        await repository.add_pokedollars(self.user_id, -price)
        await repository.add_item(self.user_id, item_name, 1)
        emoji = d.get("emoji", "❔")
        self._log_purchase(f"{emoji} {item_name}", price)
        await ctx.response.edit_message(content=self._log_content(), embed=await self.render_embed(), view=self)


@pokemon_group.command(name="shop", description="Browse and buy items for your Pokémon journey")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def pokemon_shop(ctx: Interaction):
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("You can't shop mid-battle. Use the items you've got.", ephemeral=True)
        return
    view = ShopView(ctx.user.id)
    await ctx.response.send_message(embed=await view.render_embed(), view=view, ephemeral=True)


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

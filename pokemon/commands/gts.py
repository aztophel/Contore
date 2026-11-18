"""`/gts ...` -- the Global Trade Station: list a Pokémon with one or more
OR'd "want" conditions (a species, min IVs, nature, ability, gender,
shininess, and/or money), browse and filter other trainers' listings, and
fulfill them. Ported from the monolith's `gts_commands` group, including
trade-evolution triggering via
`pokemon.commands.evolution.maybe_trigger_trade_evolution`.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from discord import AllowedMentions, ButtonStyle, Embed, Interaction, app_commands
from discord.ui import Button, View, button

from pokemon.commands import repository, transfer
from pokemon.commands.battle_state import is_in_battle
from pokemon.commands.dex_search import resolve_species
from pokemon.commands.display import SHINY, cmd_mention, gender_str, item_display, pokedisplay, pokedisplay_naked
from pokemon.commands.groups import gts_group
from pokemon.commands.resolve import resolve_owned_pokemon, respond
from pokemon.commands.spawning import NATURES
from pokemon.commands.stats import STAT_LABEL, iv_avg_percent
from pokemon.dex.items_repo import MEGA_STONES
from pokemon.dex.species import STAT_KEYS, get_default_repository as get_species_repo

# Command-facing shorthand (`"atk:31 spe:31"`) -> the canonical stat keys
# `repository.parse_ivs` and `Species.base_stats` use everywhere else. Kept
# local to this module since it's purely a UX-string-parsing concern, not
# a data-shape one -- see `repository.py`'s module docstring for the
# canonical-key rationale.
_IV_SHORTHAND = {
    "hp": "hp", "atk": "attack", "attack": "attack", "def": "defense", "defense": "defense",
    "spa": "special-attack", "special-attack": "special-attack",
    "spd": "special-defense", "special-defense": "special-defense", "spe": "speed", "speed": "speed",
}


def _parse_min_ivs(text: str | None) -> dict[str, int] | None:
    """Ported from the `want_min_ivs`-parsing inlined at each GTS command's
    call site (`"atk:31 spe:31"` -> canonical-keyed dict)."""
    if not text:
        return None
    parsed = {}
    for k, v in re.findall(r"([a-z-]+)\s*:\s*(\d+)", text.lower()):
        canonical = _IV_SHORTHAND.get(k)
        if canonical:
            parsed[canonical] = int(v)
    return parsed or None


def _achievable_genders(species) -> set[str]:
    """Ported verbatim from `_achievable_genders`."""
    gr = species.gender_rate
    if gr is None or gr == -1:
        return {"genderless"}
    if gr == 0:
        return {"male"}
    if gr == 8:
        return {"female"}
    return {"male", "female"}


def _validate_condition(species, min_ivs: dict | None, nature: str | None, ability: str | None,
                         gender: str | None) -> tuple[bool, str]:
    """Ported verbatim from `_gts_validate_condition`."""
    if min_ivs:
        for stat, val in min_ivs.items():
            if stat not in STAT_KEYS:
                return False, f"`{stat}` isn't a valid stat. Use hp/atk/def/spa/spd/spe."
            if not (0 <= val <= 31):
                return False, f"IV requests must be between 0 and 31 (got {val} for {stat})."
    if nature and nature not in NATURES:
        return False, f"`{nature}` isn't a real nature."
    if gender and gender not in ("male", "female", "genderless"):
        return False, "Gender must be `male`, `female`, or `genderless`."
    if species:
        if gender and gender not in _achievable_genders(species):
            return False, f"**{species.name}** can't be {gender}; its gender ratio doesn't allow it."
        if ability and ability not in {a.name for a in species.abilities}:
            return False, f"**{species.name}** can't have the ability **{ability.title()}**."
    return True, ""


def _condition_matches(cond: dict, candidate: dict, money: int | None = None) -> bool:
    """Ported verbatim from `_gts_condition_matches`."""
    if cond.get("species_id") and candidate["species_id"] != cond["species_id"]:
        return False
    if cond.get("nature") and candidate["nature"] != cond["nature"]:
        return False
    if cond.get("ability") and candidate.get("ability") != cond["ability"]:
        return False
    if cond.get("gender") and candidate.get("gender") != cond["gender"]:
        return False
    if cond.get("shiny") is not None and bool(candidate["shiny"]) != bool(cond["shiny"]):
        return False
    if cond.get("min_ivs"):
        cand_ivs = repository.parse_ivs(candidate.get("ivs"))
        for stat, val in cond["min_ivs"].items():
            if cand_ivs.get(stat, 0) < val:
                return False
    if cond.get("money") and money is not None and money < cond["money"]:
        return False
    return True


def _matches_want(candidate: dict, conditions: list[dict]) -> dict | bool:
    """Ported verbatim from `_gts_matches_want`: `True` for an
    unconditional listing, the (preferably money-free) matching condition
    dict otherwise, or falsy if nothing matches."""
    if not conditions:
        return True
    needs_money = None
    current = None
    for c in conditions:
        if _condition_matches(c, candidate):
            if c.get("money"):
                if current is None or needs_money is None or needs_money > c["money"]:
                    needs_money = c["money"]
                    current = c
            else:
                return c
    return current


def _condition_desc(cond: dict) -> list[str]:
    """Ported verbatim from `_gts_condition_desc`."""
    species_repo = get_species_repo()
    parts = []
    if cond.get("species_id"):
        sp = species_repo.get(cond["species_id"])
        parts.append(f"a **{sp.name if sp else '?'}**")
    if cond.get("min_ivs"):
        parts.append("IVs: " + ", ".join(f"{STAT_LABEL[k]}\u2265{v}" for k, v in cond["min_ivs"].items()))
    if cond.get("nature"):
        parts.append(f"{cond['nature']} nature")
    if cond.get("ability"):
        parts.append(f"{cond['ability'].title()} ability")
    if cond.get("gender"):
        parts.append(cond["gender"])
    if cond.get("shiny"):
        parts.append("shiny")
    if cond.get("money"):
        parts.append(f"\u20b1{cond['money']:,}")
    return parts


def _condition_needs_pokemon(cond: dict) -> bool:
    """Ported verbatim from `_condition_needs_pokemon`."""
    return bool(
        cond.get("species_id") or cond.get("min_ivs") or cond.get("nature")
        or cond.get("ability") or cond.get("gender")) or cond.get("shiny") is not None


def _pokemon_line(p: dict) -> str:
    """Ported verbatim from `_gts_pokemon_line`."""
    from pokemon.dex.items_repo import POKEBALL_EMOJI

    ball_emoji = POKEBALL_EMOJI.get(p.get("ball_type", "Poké Ball"), "🔴")
    g = gender_str(p.get("gender"))
    return f"{ball_emoji} {pokedisplay(p)}{f' {g}' if g else ''} \u200b\u00b7\u200b Lv.{p['level']}"


def _build_detail_embed(listing: dict, p: dict) -> Embed:
    """Ported verbatim from `_build_gts_detail_embed`."""
    ivs = repository.parse_ivs(p.get("ivs"))
    moves = json.loads(p.get("moves") or "[]")
    from pokemon.commands.moveset import move_display_name

    move_lines = "\n".join(f"\u2022 {move_display_name(m['name'])}" for m in moves) or "*None*"
    g = gender_str(p.get("gender")) or "Genderless"
    from pokemon.dex.items_repo import POKEBALL_EMOJI

    ball = POKEBALL_EMOJI.get(p.get("ball_type", "Poké Ball"), "🔴")

    embed = Embed(title=f"{pokedisplay_naked(p)} \u200b\u00b7\u200b Listing `#{listing['id']}`", color=0x2ECC71)
    embed.add_field(name="Level", value=str(p["level"]), inline=True)
    embed.add_field(name="Nature", value=p.get("nature", "?"), inline=True)
    embed.add_field(name="Gender", value=g, inline=True)
    embed.add_field(name="Ability", value=p["ability"].title() if p.get("ability") else "—", inline=True)
    embed.add_field(name="Caught In", value=f"{ball} {p.get('ball_type', 'Poké Ball')}", inline=True)
    embed.add_field(name="Shiny", value=f"Yes {SHINY}" if p["shiny"] else "No", inline=True)
    if p.get("held_item"):
        embed.add_field(name="Held Item", value=item_display(p["held_item"]), inline=True)
    iv_line = " / ".join(f"{STAT_LABEL[k]} {ivs.get(k, 0)}" for k in STAT_KEYS)
    embed.add_field(name=f"IVs (avg {iv_avg_percent(ivs):.1f}%)", value=iv_line, inline=False)
    embed.add_field(name="Moves", value=move_lines, inline=False)
    return embed


async def _user_can_fulfill_listing(user_id: int, listing: dict) -> bool:
    """Ported verbatim from `_user_can_fulfill_listing`."""
    conditions = repository.listing_conditions(listing)
    if not conditions:
        return True
    money = await repository.get_pokedollars(user_id)
    owned = await repository.get_user_pokemon(user_id)
    return any(any(_condition_matches(c, p, money) for c in conditions) for p in owned)


class GTSBrowseView(View):
    """Ported from `_GTSBrowseView`. The "Details" button shows
    `_build_detail_embed` (a plain stat/move readout) rather than the
    full PIL-rendered `/pokemon info` card, which isn't built yet -- see
    TRACK_B_HANDOFF_2.md."""

    def __init__(self, user_id: int, listings: list[dict], pokemons: dict[int, dict], filters: dict):
        super().__init__(timeout=180)
        self.user_id, self.listings, self.pokemons, self.filters = user_id, listings, pokemons, filters
        self.page = 0
        self.exhausted = not listings or len(listings) < filters["_page_size"]
        self._before_created_at = listings[-1]["created_at"] if listings else None
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= len(self.listings) - 1 and self.exhausted

    async def _fetch_more(self) -> int:
        f, added, cursor = self.filters, 0, self._before_created_at
        for _ in range(4):
            page = await repository.get_active_gts_listings_page(
                limit=f["_page_size"], before_created_at=cursor,
                species_id=f.get("species_id"), nature=f.get("nature"), ability=f.get("ability"),
                gender=f.get("gender"), shiny=f.get("shiny"), exclude_lister_id=self.user_id)
            if not page:
                self.exhausted = True
                break
            cursor = self._before_created_at = page[-1]["created_at"]
            batch = await repository.get_pokemon_batch([listing["pokemon_id"] for listing in page])
            for listing in page:
                p = batch.get(listing["pokemon_id"])
                if not p or p["owner_id"] != listing["lister_id"]:
                    continue
                if f.get("min_ivs"):
                    p_ivs = repository.parse_ivs(p.get("ivs"))
                    if any(p_ivs.get(s, 0) < v for s, v in f["min_ivs"].items()):
                        continue
                if f.get("fulfillable_only") and not await _user_can_fulfill_listing(self.user_id, listing):
                    continue
                self.listings.append(listing)
                self.pokemons[listing["pokemon_id"]] = p
                added += 1
            if len(page) < f["_page_size"]:
                self.exhausted = True
            if added:
                break
        return added

    def current_embed(self) -> Embed:
        listing = self.listings[self.page]
        p = self.pokemons[listing["pokemon_id"]]
        conds = [", ".join(_condition_desc(c)) or "anything" for c in repository.listing_conditions(listing)]
        want = " **OR** ".join(f"({c})" for c in conds) if conds else "anything reasonable"
        embed = Embed(
            title=f"GTS Listing `#{listing['id']}`", color=0x5865F2,
            description=f"{_pokemon_line(p)}\n<@{listing['lister_id']}> wants: {want}")
        embed.set_footer(text=f"Listing {self.page + 1}/{len(self.listings)}{'' if self.exhausted else '+'}")
        return embed

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.user_id:
            await ctx.response.send_message("This browser isn't yours!", ephemeral=True)
            return False
        return True

    @button(label="◀", style=ButtonStyle.gray)
    async def prev_btn(self, ctx: Interaction, _: Button):
        self.page -= 1
        self._update_buttons()
        await ctx.response.edit_message(embed=self.current_embed(), view=self)

    @button(label="▶", style=ButtonStyle.gray)
    async def next_btn(self, ctx: Interaction, _: Button):
        if self.page >= len(self.listings) - 1 and not self.exhausted:
            await ctx.response.defer()
            await self._fetch_more()
            self._update_buttons()
            if self.page < len(self.listings) - 1:
                self.page += 1
            await ctx.edit_original_response(embed=self.current_embed(), view=self)
            return
        self.page += 1
        self._update_buttons()
        await ctx.response.edit_message(embed=self.current_embed(), view=self)

    @button(label="Details", style=ButtonStyle.blurple, emoji="🔍")
    async def details_btn(self, ctx: Interaction, _: Button):
        listing = self.listings[self.page]
        p = self.pokemons[listing["pokemon_id"]]
        await ctx.response.send_message(embed=_build_detail_embed(listing, p), ephemeral=True)


@gts_group.command(name="list", description="Put one of your Pokémon up on the GTS")
@app_commands.describe(
    pokemon="The Pokémon you're offering",
    want_pokemon="Species you want in return (leave blank if you only want money/traits)",
    want_min_ivs="Minimum IVs required, e.g. 'atk:31 spe:31'",
    want_nature="Required nature", want_ability="Required ability",
    want_gender="Required gender", want_shiny="Require the trade to be shiny",
    want_money="Pokédollars you want instead of/alongside a Pokémon")
async def gts_list(
    ctx: Interaction, pokemon: str, want_pokemon: str | None = None, want_min_ivs: str | None = None,
    want_nature: str | None = None, want_ability: str | None = None,
    want_gender: Literal["Male", "Female", "Genderless"] | None = None,
    want_shiny: bool | None = None, want_money: int | None = None,
):
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("Finish your current battle first!", ephemeral=True)
        return
    p = await resolve_owned_pokemon(ctx, pokemon)
    if not p or p["owner_id"] != ctx.user.id:
        await respond(ctx, f"You don't own any Pokémon matching `{pokemon}`.", ephemeral=True)
        return
    if p.get("held_item") in MEGA_STONES:
        await respond(ctx, "Take back the Mega Stone before listing this Pokémon.", ephemeral=True)
        return

    want_species = resolve_species(want_pokemon) if want_pokemon else None
    if want_pokemon and not want_species:
        await respond(ctx, f"Couldn't find a species called `{want_pokemon}`.", ephemeral=True)
        return

    min_ivs = _parse_min_ivs(want_min_ivs)
    gender_key = want_gender.lower() if want_gender else None
    ok, err = _validate_condition(want_species, min_ivs, want_nature, want_ability, gender_key)
    if not ok:
        await respond(ctx, f"❌ {err}", ephemeral=True)
        return
    if not any((want_species, want_money, min_ivs, want_nature, want_ability, want_shiny is not None, gender_key)):
        await respond(ctx, "You need to ask for *something* in return.", ephemeral=True)
        return

    condition = {
        "species_id": want_species.id if want_species else None, "min_ivs": min_ivs, "nature": want_nature,
        "ability": want_ability, "gender": gender_key, "shiny": want_shiny, "money": want_money or None,
    }
    listing_id = await repository.create_gts_listing(ctx.user.id, p["id"], [condition])
    want_desc = _condition_desc(condition)
    await respond(
        ctx, f"Listed {pokedisplay(p)} on the GTS (`#{listing_id}`), wanting: "
        f"{', '.join(want_desc) or 'anything reasonable'}.", ephemeral=True)


@gts_group.command(name="addcondition", description="Add an alternative (OR) way to fulfill one of your GTS listings")
@app_commands.describe(
    listing_id="The GTS listing ID", want_pokemon="Species that would satisfy this alternative condition",
    want_min_ivs="Minimum IVs required, e.g. 'atk:31 spe:31'",
    want_nature="Required nature", want_ability="Required ability",
    want_gender="Required gender", want_shiny="Require the trade to be shiny")
async def gts_add_condition(
    ctx: Interaction, listing_id: int, want_pokemon: str | None = None, want_min_ivs: str | None = None,
    want_nature: str | None = None, want_ability: str | None = None,
    want_gender: Literal["Male", "Female", "Genderless"] | None = None,
    want_shiny: bool | None = None, want_money: int | None = None,
):
    listing = await repository.get_gts_listing(listing_id)
    if not listing or listing["lister_id"] != ctx.user.id or listing["status"] != "active":
        await ctx.response.send_message("That's not an active listing of yours.", ephemeral=True)
        return

    want_species = resolve_species(want_pokemon) if want_pokemon else None
    if want_pokemon and not want_species:
        await respond(ctx, f"Couldn't find a species called `{want_pokemon}`.", ephemeral=True)
        return

    min_ivs = _parse_min_ivs(want_min_ivs)
    gender_key = want_gender.lower() if want_gender else None
    ok, err = _validate_condition(want_species, min_ivs, want_nature, want_ability, gender_key)
    if not ok:
        await respond(ctx, f"❌ {err}", ephemeral=True)
        return
    if not any((want_species, min_ivs, want_nature, want_ability, want_shiny is not None, gender_key)):
        await respond(ctx, "That condition doesn't ask for anything.", ephemeral=True)
        return

    condition = {
        "species_id": want_species.id if want_species else None, "min_ivs": min_ivs, "nature": want_nature,
        "ability": want_ability, "gender": gender_key, "shiny": want_shiny, "money": want_money,
    }
    await repository.add_gts_condition(listing_id, condition)
    await respond(
        ctx, f"Added an alternative fulfillment option to listing `#{listing_id}`: "
        f"{', '.join(_condition_desc(condition))}.", ephemeral=True)


@gts_group.command(name="removecondition", description="Remove one of the fulfillment conditions from your GTS listing")
@app_commands.describe(listing_id="The GTS listing ID", index="The condition index to remove (1-based)")
async def gts_remove_condition(ctx: Interaction, listing_id: int, index: int):
    listing = await repository.get_gts_listing(listing_id)
    if not listing or listing["lister_id"] != ctx.user.id or listing["status"] != "active":
        await ctx.response.send_message("That's not an active listing of yours.", ephemeral=True)
        return
    conditions = repository.listing_conditions(listing)
    if not conditions:
        await ctx.response.send_message("This listing has no conditions to remove.", ephemeral=True)
        return
    if index < 1 or index > len(conditions):
        await ctx.response.send_message(f"Invalid index (1\u2013{len(conditions)}, got '{index}').", ephemeral=True)
        return
    if len(conditions) == 1:
        await ctx.response.send_message(
            f"This is the listing's only condition. Use {cmd_mention('gts cancel')} if you no longer want to ask "
            f"for anything, or {cmd_mention('gts addcondition')} first to add a replacement before removing this "
            "one.", ephemeral=True)
        return
    removed = conditions.pop(index - 1)
    await repository.set_gts_conditions(listing_id, conditions)
    await ctx.response.send_message(
        f"Removed condition #{index} ({', '.join(_condition_desc(removed)) or 'anything'}) from listing "
        f"`#{listing_id}`.", ephemeral=True)


@gts_group.command(name="browse", description="Browse active GTS listings")
async def gts_browse(
    ctx: Interaction, species: str | None = None, min_ivs: str | None = None, nature: str | None = None,
    ability: str | None = None, gender: Literal["Male", "Female", "Genderless"] | None = None,
    shiny: bool | None = None, fulfillable_only: bool = False,
):
    parsed_min_ivs = _parse_min_ivs(min_ivs)
    gender_key = gender.lower() if gender else None
    species_id = None
    if species:
        sp_match = resolve_species(species)
        if not sp_match:
            await ctx.response.send_message(f"No species found matching `{species}`.", ephemeral=True)
            return
        species_id = sp_match.id

    await ctx.response.defer(ephemeral=True)
    filters = {
        "_page_size": 10, "species_id": species_id, "min_ivs": parsed_min_ivs, "nature": nature,
        "ability": ability, "gender": gender_key, "shiny": shiny, "fulfillable_only": fulfillable_only,
    }

    view = GTSBrowseView(ctx.user.id, [], {}, filters)
    if not await view._fetch_more():
        has_filters = any((species, min_ivs, nature, ability, gender_key, shiny is not None))
        msg = (
            "None of the active listings matching your filters are something you can currently fulfill."
            if fulfillable_only else
            "No listings match your filters." if has_filters else "No listings match right now.")
        await ctx.followup.send(msg, ephemeral=True)
        return

    view._update_buttons()
    await ctx.followup.send(
        embed=view.current_embed(), view=view, ephemeral=True, allowed_mentions=AllowedMentions.none())


@gts_group.command(name="mine", description="View your own active GTS listings")
async def gts_mine(ctx: Interaction):
    listings = await repository.get_user_gts_listings(ctx.user.id)
    if not listings:
        await ctx.response.send_message("You don't have any active listings.", ephemeral=True)
        return
    lines = []
    for listing in listings:
        p = await repository.get_pokemon(listing["pokemon_id"])
        if not p:
            continue
        conditions = repository.listing_conditions(listing)
        cond_lines = "\n".join(
            f"  \u200b\u200b `{i + 1}.` {', '.join(_condition_desc(c)) or 'anything'}"
            for i, c in enumerate(conditions)
        ) or "  \u200b\u200b *(anything)*"
        lines.append(f"`#{listing['id']}` {_pokemon_line(p)}\n{cond_lines}")
    await ctx.response.send_message(
        embed=Embed(title="Your GTS Listings", description="\n\n".join(lines), color=0x5865F2), ephemeral=True)


@gts_group.command(name="cancel", description="Cancel one of your GTS listings")
async def gts_cancel(ctx: Interaction, listing_id: int):
    listing = await repository.get_gts_listing(listing_id)
    if not listing or listing["lister_id"] != ctx.user.id or listing["status"] != "active":
        await ctx.response.send_message("That's not an active listing of yours.", ephemeral=True)
        return
    await repository.close_gts_listing(listing_id, "cancelled")
    await ctx.response.send_message(f"Cancelled listing `#{listing_id}`.", ephemeral=True)


@gts_group.command(name="fulfill", description="Complete a GTS trade")
@app_commands.describe(listing_id="The GTS listing ID", offer_pokemon="The Pokémon you're offering (if the listing wants one)")
async def gts_fulfill(ctx: Interaction, listing_id: int, offer_pokemon: str | None = None):
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("Finish your current battle first!", ephemeral=True)
        return
    listing = await repository.get_gts_listing(listing_id)
    if not listing or listing["status"] != "active":
        await ctx.response.send_message("That listing is no longer active.", ephemeral=True)
        return
    if listing["lister_id"] == ctx.user.id:
        await ctx.response.send_message("You can't fulfill your own listing.", ephemeral=True)
        return

    conditions = repository.listing_conditions(listing)
    money_only_conditions = [c for c in conditions if not _condition_needs_pokemon(c)]
    want_money = None
    offered = None

    if offer_pokemon:
        offered = await resolve_owned_pokemon(ctx, offer_pokemon)
        if not offered or offered["owner_id"] != ctx.user.id:
            await respond(ctx, f"You don't own any Pokémon matching `{offer_pokemon}`.", ephemeral=True)
            return
        matched = _matches_want(offered, conditions)
        if not matched:
            await respond(ctx, "That Pokémon doesn't meet any of this listing's requirements.", ephemeral=True)
            return
        matched_cond = matched if isinstance(matched, dict) else {}
        if matched_cond.get("money") and await repository.get_pokedollars(ctx.user.id) < matched_cond["money"]:
            await respond(ctx, f"You need **\u20b1{matched_cond['money']:,}** to fulfill this listing.", ephemeral=True)
            return
        want_money = matched_cond.get("money")
    elif conditions:
        if not money_only_conditions:
            await ctx.response.send_message(
                "This listing wants a specific Pokémon. Specify `offer_pokemon`.", ephemeral=True)
            return
        cheapest = min(money_only_conditions, key=lambda cond: cond.get("money") or 0)
        if cheapest.get("money") and await repository.get_pokedollars(ctx.user.id) < cheapest["money"]:
            await respond(ctx, f"You need **\u20b1{cheapest['money']:,}** to fulfill this listing.", ephemeral=True)
            return
        want_money = cheapest.get("money")

    target_p = await repository.get_pokemon(listing["pokemon_id"])
    if not target_p or target_p["owner_id"] != listing["lister_id"]:
        await repository.close_gts_listing(listing_id, "invalid")
        await respond(ctx, "This Pokémon is no longer available.", ephemeral=True)
        return

    listing = await repository.get_gts_listing(listing_id)
    if not listing or listing["status"] != "active":
        await respond(ctx, "This listing was already closed.", ephemeral=True)
        return

    species_repo = get_species_repo()
    target_sp = species_repo.get(target_p["species_id"])
    await transfer.apply_transfer_friendship(
        target_sp.base_friendship if target_sp else 70, target_p["id"], listing["lister_id"], ctx.user.id)
    await repository.transfer_pokemon(target_p["id"], listing["lister_id"], ctx.user.id)
    dex = await repository.get_pokedex_seen(ctx.user.id)
    dex.add(target_p["species_id"])
    await repository.set_pokedex_seen(ctx.user.id, dex)
    await transfer.auto_add_to_team(ctx.user.id, target_p["id"])
    from pokemon.commands.milestones import check_and_notify_milestones
    await check_and_notify_milestones(ctx.client, ctx.user.id, dex)

    lister = ctx.client.get_user(listing["lister_id"]) or await ctx.client.fetch_user(listing["lister_id"])
    if offered:
        offered_sp = species_repo.get(offered["species_id"])
        await transfer.apply_transfer_friendship(
            offered_sp.base_friendship if offered_sp else 70, offered["id"], ctx.user.id, listing["lister_id"])
        await repository.transfer_pokemon(offered["id"], ctx.user.id, listing["lister_id"])
        dex2 = await repository.get_pokedex_seen(listing["lister_id"])
        dex2.add(offered["species_id"])
        await repository.set_pokedex_seen(listing["lister_id"], dex2)
        await transfer.auto_add_to_team(listing["lister_id"], offered["id"])
        await check_and_notify_milestones(ctx.client, listing["lister_id"], dex2)

        offered_name = offered_sp.name if offered_sp else "?"
        article = "n" if offered_name[:1].lower() in "aeiou" else ""
        await lister.send(
            f"Your GTS listing of {pokedisplay(target_p)} (`{listing['pokemon_id']}`) for a{article} "
            f"**{offered_name}** was fulfilled!")
        from pokemon.commands.evolution import maybe_trigger_trade_evolution
        await maybe_trigger_trade_evolution(ctx.client, target_p["id"], partner_species_id=offered["species_id"])
        await maybe_trigger_trade_evolution(ctx.client, offered["id"], partner_species_id=target_p["species_id"])
    else:
        await lister.send(f"Your GTS listing of {pokedisplay(target_p)} (`{listing['pokemon_id']}`) was fulfilled!")
        from pokemon.commands.evolution import maybe_trigger_trade_evolution
        await maybe_trigger_trade_evolution(ctx.client, target_p["id"])

    if want_money is not None:
        await repository.add_pokedollars(ctx.user.id, -want_money)
        await repository.add_pokedollars(listing["lister_id"], want_money)

    await repository.close_gts_listing(listing_id, "fulfilled", ctx.user.id)
    await ctx.channel.send(
        f"GTS trade `#{listing_id}` complete! <@{listing['lister_id']}> \u2194 {ctx.user.mention}",
        allowed_mentions=AllowedMentions.none())


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

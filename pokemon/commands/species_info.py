"""`/pokemon species` and `/pokemon info` -- deliberately one file since
they share almost all of their rendering machinery (rarity coloring, the
type-effectiveness chart image, the "jump to species info" link button).
Ported from `_build_species_embed`/`SpeciesView`/`pokemon_species` and
`_build_pokemon_full_embed`/`_generate_iv_ev_hexagon`/`pokemon_info`.

PIL rendering (the type chart and the IV/EV hexagon) is owned directly
here rather than soft-dependency-stubbed through `core.imaging`: unlike a
generic silhouette or leaderboard card, both charts are entirely
Pokemon-domain (type chart cells, stat hexagon axes) with no reuse value
outside this file, so there's no real "generic utility" to defer to.

Two real data gaps, both already flagged where the underlying repositories
were built: species flavor text / genera (`Species.flavor_text` exists but
is always `None` -- needs a PokeAPI enrichment pass this sandbox can't
run) and the learnable-moves button's per-move stats (needs
`data/moves_data.json`, same gap). Both degrade to an honest "not loaded"
state rather than fabricating placeholder numbers.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime
from io import BytesIO
from math import cos, pi, sin

from discord import ButtonStyle, Embed, File, Interaction, app_commands
from discord.ui import Button, View, button
from discord.utils import MISSING
from PIL import Image, ImageDraw, ImageFont

from core.rarity import RARITY_COLORS, RARITY_EMOJI
from pokemon.commands import repository
from pokemon.commands.dex_search import resolve_species, species_autocomplete
from pokemon.commands.display import (
    FAINTED, FEMALE, MALE, SHINY, STATUS_EMOJI, TYPE_EMOJI, gender_str, item_display, pokemoji, poketypes,
    species_emoji,
)
from pokemon.commands.groups import pokemon_group
from pokemon.commands.moveset import move_display_name
from pokemon.commands.paging import SimplePageView
from pokemon.commands.resolve import resolve_owned_pokemon, respond
from pokemon.commands.stats import STAT_LABEL, calc_stat_with_nature, iv_avg_percent, pokemon_max_hp
from pokemon.dex.evolution import EvolutionCondition, get_default_repository as get_evolution_repo
from pokemon.dex.items_repo import MEGA_STONES, POKEBALL_EMOJI
from pokemon.dex.species import STAT_KEYS, Species, get_default_repository as get_species_repo
from pokemon.dex.type_chart import TYPE_COLOR, TYPE_ORDER, type_effectiveness

MEGA = "<:mega:1519555367816659127>"

# RARITY_COLORS/RARITY_EMOJI: see core.rarity -- built there first (for the
# achievement-card/leaderboard use case) with an explicit note that
# Pokemon rarity display should import from it rather than redefine it;
# this file used to do exactly that redefinition (byte-identical values,
# just duplicated) before Track E deduplicated it.
_GEN_ROMAN: dict[int, str] = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI"}
_EGG_GROUP_LABELS: dict[str, str] = {"water-1": "Water 1", "water-2": "Water 2", "water-3": "Water 3"}


def _species_mega_stones() -> dict[int, list[str]]:
    """Built once from `items_repo.MEGA_STONES`'s own `species_id` field,
    equivalent to the monolith's module-level `_SPECIES_MEGA_STONES`
    (there it was built the same way, just eagerly at import time)."""
    out: dict[int, list[str]] = {}
    for name, data in MEGA_STONES.items():
        out.setdefault(data["species_id"], []).append(name)
    return out


_SPECIES_MEGA_STONES = _species_mega_stones()


def _humanize_egg_group(g: str) -> str:
    """Ported from `_humanize_egg_group`, adjusted to this codebase's
    actual egg-group slug convention -- see `pokedex_builder._normalize_egg_groups`."""
    if g == "no-eggs":
        return "Undiscovered"
    return _EGG_GROUP_LABELS.get(g, g.replace("-", " ").title())


def visual_rarity(species: Species) -> str:
    """A species' rarity, nudged up a tier when its catch rate and BST are
    both unusually high for that tier (so e.g. a tough, high-BST
    "common" reads as uncommon). Ported verbatim from `_visual_rarity`."""
    order = ["common", "uncommon", "rare", "epic", "legendary", "mythical"]
    bst_floor = {"common": 0, "uncommon": 400, "rare": 500, "epic": 575}
    max_nudge_idx = order.index("epic")

    base = species.rarity
    idx = order.index(base) if base in order else 0
    if idx >= max_nudge_idx:
        return base

    catch_rate, bst = species.catch_rate, species.bst
    floor = bst_floor.get(base, 0)
    next_floor = bst_floor.get(order[idx + 1], floor + 175) if idx + 1 < len(order) else floor + 175
    gap = max(1, next_floor - floor)
    bst_progress = max(0.0, min(1.5, (bst - floor) / gap))

    catch_factor = max(0.0, min(1.0, (90 - catch_rate) / 90))
    combined = 0.5 * catch_factor + 0.5 * min(1.0, bst_progress)

    min_catch_factor = 0.15
    nudge_threshold = 0.5 if idx < max_nudge_idx - 2 else 0.7 if idx < max_nudge_idx - 1 else 0.9
    if catch_factor >= min_catch_factor and combined >= nudge_threshold:
        idx += 1
    return order[idx]


def catch_prob_pokeball_fullhp(catch_rate: int) -> float:
    """Catch probability (%) with a Poke Ball at full HP, no status.
    Ported verbatim from `_catch_prob_pokeball_fullhp`."""
    a = catch_rate / 3.0
    if a >= 255:
        return 100.0
    a = max(1.0, a)
    b = 1_048_560.0 / ((16_711_680.0 / a) ** 0.25)
    return min(100.0, (b / 65_536.0) ** 4 * 100.0)


def gender_display(gender_rate: int | None) -> str:
    """Ported verbatim from `_gender_display`."""
    if gender_rate is None or gender_rate == -1:
        return "Genderless"
    if gender_rate == 0:
        return f"{MALE} Male only"
    if gender_rate == 8:
        return f"{FEMALE} Female only"
    f_pct = gender_rate * 12.5
    return f"{MALE} {100 - f_pct:.4g}% \u00b7 {FEMALE} {f_pct:.4g}%"


def ev_yield_display(effort_yield: dict) -> str:
    """Ported verbatim from `_ev_yield_display`."""
    parts = [f"+{v:,} {STAT_LABEL.get(k, k)}" for k, v in effort_yield.items() if v > 0]
    return "  ".join(parts) or "\u2014"


def evo_condition_desc(e: EvolutionCondition) -> str:
    """Ported verbatim from `_evo_condition_desc`."""
    parts: list[str] = []
    if e.min_level:
        parts.append(f"Lv. {e.min_level}")
    elif e.trigger == "trade":
        held = e.held_item
        parts.append("Trade" + (f" + {held.replace('-', ' ').title()}" if held else ""))
    elif e.item:
        parts.append(e.item.replace("-", " ").title())
    if e.min_happiness:
        parts.append(f"Bond >={e.min_happiness}")
    if e.time_of_day:
        parts.append(e.time_of_day.title())
    if e.known_move:
        parts.append(f"knows {move_display_name(e.known_move)}")
    if e.known_move_type:
        parts.append(f"knows a {e.known_move_type.title()}-type move")
    if e.gender == 1:
        parts.append("Female")
    elif e.gender == 2:
        parts.append("Male")
    return ", ".join(parts) or (e.trigger or "").replace("-", " ").title()


def evo_chain_text(species_id: int, shiny: bool = False) -> str:
    """Ported verbatim from `_evo_chain_text`."""
    species_repo = get_species_repo()
    evo_repo = get_evolution_repo()

    base = species_id
    seen: set[int] = set()
    while True:
        sp = species_repo.get(base)
        if not sp or sp.prevo is None or sp.prevo in seen:
            break
        seen.add(base)
        base = sp.prevo

    lines: list[str] = []

    def _walk(sid: int, depth: int, cond: str | None = None) -> None:
        if depth > 6 or len(lines) > 24:
            return
        sp2 = species_repo.get(sid)
        name = sp2.name if sp2 else f"#{sid}"
        em = species_emoji(sid, shiny)
        pad = "  " * depth
        hi = "**" if sid == species_id else ""
        suffix = f" *({cond})*" if cond else ""
        lines.append(f"{pad}  L {em} {hi}{name}{hi}{suffix}")
        for evo in evo_repo.evolutions_from(sid):
            _walk(evo.to_id, depth + 1, evo_condition_desc(evo))

    _walk(base, 0)
    return "\n".join(lines) if len(lines) > 1 else "Does not evolve."


def _stat_col(v: int, max_val: int) -> int:
    """ANSI color code for a stat bar. Ported verbatim from `_stat_col`."""
    return (35 if v >= max_val else 34 if v >= 150 else 36 if v >= 120 else
            32 if v >= 90 else 33 if v >= 60 else 31)


def _stat_bar_lines(base_stats: dict, bst: int) -> str:
    max_val = 255
    lines = []
    for key in STAT_KEYS:
        v = base_stats.get(key, 0)
        filled = max(0, v * 16 // max_val)
        color = _stat_col(v, max_val)
        bar = f"\u001b[{color}m" + "#" * filled + "." * (16 - filled) + "\u001b[0m"
        lines.append(f"{STAT_LABEL[key][:3]:<3} {v:>3}  {bar}")
    return "```ansi\n" + "\n".join(lines) + "\n```"


_FONT_PATHS = {
    "bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
    "reg": ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"],
}
_font_cache: dict[tuple[str, int], "ImageFont.FreeTypeFont"] = {}


def _get_font(style: str, size: int):
    """Ported from `_get_font`, cached (the original re-opened the font
    file on every call)."""
    key = (style, size)
    if key not in _font_cache:
        for path in _FONT_PATHS.get(style, _FONT_PATHS["reg"]):
            try:
                _font_cache[key] = ImageFont.truetype(path, size)
                break
            except Exception:
                continue
        else:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def _draw_shadowed_text(img, xy: tuple[float, float], text: str, font, fill=(255, 255, 255, 255)) -> None:
    """Ported verbatim from `_draw_shadowed_text`."""
    if len(fill) == 3:
        fill = (*fill, 255)
    x, y = xy
    pad = 4
    tw = int(font.getlength(text)) if hasattr(font, "getlength") else 0
    th = font.size
    lw, lh = tw + pad * 2 + 4, th + pad * 2 + 4
    ox, oy = int(x) - pad, int(y) - pad
    layer = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    lx, ly = x - ox, y - oy
    for dx, dy in ((0, 2), (-1, -1), (0, -1), (-1, 0), (0, 1), (1, 0)):
        ld.text((lx + dx, ly + dy), text, font=font, fill=(0, 0, 0, 80))
    ld.text((lx + 1, ly + 1), text, font=font, fill=(0, 0, 0, 120))
    ld.text((lx, ly), text, font=font, fill=fill)
    img.alpha_composite(layer, dest=(ox, oy))


_MULT_COLOR: dict[float, tuple[int, int, int]] = {
    0.0: (27, 27, 38), 0.25: (124, 0, 0), 0.5: (164, 0, 0), 2.0: (78, 154, 6), 4.0: (115, 210, 22),
}
_MULT_LABEL: dict[float, str] = {0.0: "0", 0.25: "1/4", 0.5: "1/2", 1.5: "1.5", 2.0: "2", 3.0: "3", 4.0: "4"}
_CELL_BORDER = (60, 60, 68, 255)
_CELL_BLANK_FILL = (255, 255, 255, 18)


def _generate_type_chart_image_sync(defender_types: list[str]) -> File:
    """Ported verbatim from `_generate_type_chart_image`."""
    mults = {t: type_effectiveness(t, defender_types) for t in TYPE_ORDER}
    cols = 9
    rows_of_types = [TYPE_ORDER[:cols], TYPE_ORDER[cols:]]

    cell_x, cell_y, gap_x, gap_y_inner, gap_y_between, pad = 46, 28, 3, 2, 12, 8
    width = pad * 2 + cols * cell_x + (cols - 1) * gap_x
    row_block_h = cell_y + gap_y_inner + cell_y
    height = pad * 2 + 2 * row_block_h + gap_y_between

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    f_head, f_val = _get_font("bold", 13), _get_font("bold", 14)

    for r, row_types in enumerate(rows_of_types):
        y0 = pad + r * (row_block_h + gap_y_between)
        for c, t in enumerate(row_types):
            x0 = pad + c * (cell_x + gap_x)
            hcol = TYPE_COLOR.get(t, (120, 120, 120))
            draw.rectangle([x0, y0, x0 + cell_x, y0 + cell_y], fill=hcol + (255,), outline=_CELL_BORDER, width=1)
            abbr = t[:3].upper()
            tw = draw.textlength(abbr, font=f_head)
            _draw_shadowed_text(img, (x0 + (cell_x - tw) / 2, y0 + (cell_y - f_head.size) / 2 - 1), abbr, f_head)

            vy0 = y0 + cell_y + gap_y_inner
            mult = mults[t]
            if mult == 1.0:
                draw.rectangle([x0, vy0, x0 + cell_x, vy0 + cell_y], fill=_CELL_BLANK_FILL, outline=_CELL_BORDER, width=1)
            else:
                vcol = _MULT_COLOR.get(mult, (90, 90, 90))
                draw.rectangle([x0, vy0, x0 + cell_x, vy0 + cell_y], fill=vcol + (255,), outline=_CELL_BORDER, width=1)
                label = _MULT_LABEL.get(mult, f"{mult:g}")
                lw = draw.textlength(label, font=f_val)
                _draw_shadowed_text(
                    img, (x0 + (cell_x - lw) / 2, vy0 + (cell_y - f_val.size) / 2 - 1), label, f_val, (255, 221, 87))

    buf = BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return File(buf, filename="type_chart.png")


def _hex_point(cx, cy, radius, index, total=6):
    angle = -pi / 2 + index * (2 * pi / total)
    return cx + radius * cos(angle), cy + radius * sin(angle)


_HEX_STAT_ORDER = ["hp", "attack", "defense", "special-defense", "speed", "special-attack"]


def _generate_iv_ev_hexagon_sync(ivs: dict, evs: dict, base_stats: dict, level: int, nature: str, size: int = 320) -> File:
    """Ported verbatim from `_generate_iv_ev_hexagon`, using canonical
    stat keys throughout instead of the monolith's shorthand."""
    pad = 60
    cx, cy = size // 2, size // 2
    max_r = size // 2 - pad

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    font, font_sm, font_val = _get_font("bold", 13), _get_font("reg", 11), _get_font("reg", 10)

    for frac in (0.25, 0.5, 0.75, 1.0):
        pts = [_hex_point(cx, cy, max_r * frac, i) for i in range(6)]
        draw.polygon(pts, outline=(120, 120, 130, 140))

    for i, key in enumerate(_HEX_STAT_ORDER):
        x, y = _hex_point(cx, cy, max_r, i)
        draw.line([(cx, cy), (x, y)], fill=(120, 120, 130, 140), width=1)
        lx, ly = _hex_point(cx, cy, max_r + 24, i)
        label = "HP" if key == "hp" else STAT_LABEL[key][:3]
        if key == "hp":
            stat_val = int((2 * base_stats.get(key, 1) + ivs.get(key, 0) + evs.get(key, 0) // 4) * level / 100) + level + 10
        else:
            stat_val = calc_stat_with_nature(base_stats.get(key, 1), level, ivs.get(key, 0), evs.get(key, 0), key, nature)
        tw = draw.textlength(label, font=font)
        draw.text((lx - tw / 2, ly - 11), label, fill=(230, 230, 235), font=font)
        vtxt = str(stat_val)
        vw = draw.textlength(vtxt, font=font_val)
        draw.text((lx - vw / 2, ly + 3), vtxt, fill=(165, 190, 225), font=font_val)

    def _poly_for(values: dict, scale: float):
        return [_hex_point(cx, cy, max_r * max(0.0, min(1.0, values.get(k, 0) / scale)), i)
                for i, k in enumerate(_HEX_STAT_ORDER)]

    iv_pts, ev_pts = _poly_for(ivs, 31), _poly_for(evs, 252)

    ev_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(ev_layer, "RGBA").polygon(ev_pts, fill=(231, 76, 60, 90), outline=(231, 76, 60, 230))
    img = Image.alpha_composite(img, ev_layer)

    iv_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(iv_layer, "RGBA").polygon(iv_pts, fill=(52, 152, 219, 90), outline=(52, 152, 219, 230))
    img = Image.alpha_composite(img, iv_layer)

    draw = ImageDraw.Draw(img, "RGBA")
    for (x, y), (x2, y2) in zip(iv_pts, ev_pts):
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(52, 152, 219, 255))
        draw.ellipse([x2 - 3, y2 - 3, x2 + 3, y2 + 3], fill=(231, 76, 60, 255))

    draw.rectangle([10, size - 26, 24, size - 16], fill=(52, 152, 219, 220))
    draw.text((28, size - 28), f"IV ({iv_avg_percent(ivs):.0f}%)", fill=(230, 230, 235), font=font_sm)
    draw.rectangle([10, size - 44, 24, size - 34], fill=(231, 76, 60, 220))
    draw.text((28, size - 46), f"EV ({sum(evs.values())}/510)", fill=(230, 230, 235), font=font_sm)

    buf = BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return File(buf, filename="stats_hex.png")


# ---------------------------------------------------------------------------
# /pokemon species
# ---------------------------------------------------------------------------

def build_species_embed(species: Species, shiny: bool = False) -> Embed:
    """Ported from `_build_species_embed`. `genera` (a species' PokeAPI
    "genus" tag, e.g. "Seed Pokemon") is omitted -- it needs the same
    PokeAPI enrichment pass `flavor_text` does, which this sandbox can't
    run; the description just drops that line rather than showing a
    placeholder."""
    rarity = visual_rarity(species).upper()
    flavor = (species.flavor_text or "").strip()
    gen_label = _GEN_ROMAN.get(species.generation_introduced, "?")

    desc_parts = [
        f"{RARITY_EMOJI.get(rarity, chr(0x26AA))} {rarity} \u00b7 Generation {gen_label}",
        poketypes(species.types),
    ]
    if flavor:
        desc_parts += ["\u200b", f"*{flavor}*", "\u200b"]

    embed = Embed(
        title=f"#{species.id:03d}  {species_emoji(species.id, shiny)} {species.name}",
        description="\n".join(desc_parts), color=RARITY_COLORS.get(rarity, 0x5865F2))
    embed.set_thumbnail(url=f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/"
                             f"official-artwork/{'shiny/' if shiny else ''}{species.id}.png")

    embed.add_field(name="Height", value=f"{species.height_m:,.1f} m", inline=True)
    embed.add_field(name="Weight", value=f"{species.weight_kg:,.1f} kg", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    catch_pct = catch_prob_pokeball_fullhp(species.catch_rate)
    embed.add_field(
        name="Catch Rate",
        value=f"{species.catch_rate}/255\n> -# `{catch_pct:.1f}%` \u00b7 Poke Ball, full HP", inline=True)
    embed.add_field(name="Base Friendship", value=str(species.base_friendship), inline=True)
    embed.add_field(name="Growth Rate", value=species.growth_rate.replace("-", " ").title(), inline=True)

    embed.add_field(name="Gender", value=gender_display(species.gender_rate), inline=True)
    egg_grps = ", ".join(_humanize_egg_group(g) for g in species.egg_groups) or "Unknown"
    embed.add_field(name="Egg Groups", value=egg_grps, inline=True)
    from pokemon.commands.breeding import egg_hatch_hours
    embed.add_field(
        name="Egg Cycles", value=f"{species.hatch_counter:,} (~{egg_hatch_hours(species.id):,} h)", inline=True)

    embed.add_field(name="EV Yield", value=ev_yield_display(species.effort_yield), inline=True)
    embed.add_field(name="Base Exp.", value=f"{species.base_experience:,}", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    ab_lines = [f"**{move_display_name(a.name)}**{'  *(H)*' if a.is_hidden else ''}" for a in species.abilities]
    embed.add_field(name="Abilities", value="\n".join(ab_lines) or "\u2014", inline=False)

    embed.add_field(name=f"Base Stats (BST: {species.bst:,})",
                     value=_stat_bar_lines(species.base_stats, species.bst), inline=False)
    embed.add_field(name="Evolution Chain", value=evo_chain_text(species.id, shiny), inline=False)

    if species.mega_forms:
        stones = _SPECIES_MEGA_STONES.get(species.id, [])
        stone_str = "  ".join(f"{MEGA_STONES[s]['emoji']} **{s}**" for s in stones)
        mega_names = "  /  ".join(f.name for f in species.mega_forms)
        embed.add_field(
            name=f"Mega Evolution{'s' if len(species.mega_forms) > 1 else ''}",
            value=stone_str or mega_names, inline=False)

    embed.set_image(url="attachment://type_chart.png")
    embed.set_footer(text=f"#{species.id:03d} {species.name}")
    return embed


class SpeciesLinkView(View):
    """A single "Species Info" button, added under `/pokemon info`'s card
    so a trainer can jump straight from one of their own Pokemon to its
    species page. Ported from `_SpeciesLinkView`."""

    def __init__(self, species: Species, shiny: bool = False):
        super().__init__(timeout=300)
        self.species = species
        self.shiny = shiny

    @button(label="Species Info", emoji="\U0001f4d6", style=ButtonStyle.blurple)
    async def _view_species(self, ctx: Interaction, _: Button):
        embed = build_species_embed(self.species, self.shiny)
        chart_file = await asyncio.to_thread(_generate_type_chart_image_sync, list(self.species.types))
        view = SpeciesView(ctx.user.id, self.species, self.shiny)
        await ctx.response.send_message(embed=embed, file=chart_file, view=view, ephemeral=True)


class SpeciesView(View):
    """Ported from `SpeciesView`. The Move List button's per-move stat
    columns need `data/moves_data.json`; when that isn't loaded it still
    lists every learnable move (level/egg/TM sections), just without
    power/PP/type -- an honest partial view rather than a hidden button."""

    def __init__(self, user_id: int, species: Species, shiny: bool = False):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.species = species
        self.shiny = shiny
        if not species.forms:
            self.remove_item(self.btn_forms)
        if not species.mega_forms:
            self.remove_item(self.btn_megas)

    @button(label="Move List", emoji="\U0001f4cb", style=ButtonStyle.blurple, custom_id="sv:moves")
    async def btn_moves(self, ctx: Interaction, _: Button):
        from pokemon.commands.moveset import move_summary
        from pokemon.dex.learnsets_repo import get_default_repository as get_learnsets

        learnset = get_learnsets().get(self.species.id)

        def _line(mv_name: str, tag: str) -> str:
            summary = move_summary(mv_name)
            power = summary["base_power"] if summary["base_power"] else "\u2014"
            typ = TYPE_EMOJI.get(summary["type_name"], "\u2754")
            cat_e = "\u2694\ufe0f" if summary["category"] == "physical" else \
                "\u2728" if summary["category"] == "special" else "\U0001f300"
            return (f"`{tag:<6}` {cat_e} {typ} **{move_display_name(mv_name)}** \u00b7 {power} BP "
                    f"\u00b7 {summary['pp']} PP")

        evo_lines = [_line(mv, "Evo") for lvl, mv in learnset.level_moves if lvl == 0]
        lv_lines = [_line(mv, f"Lv.{lvl:>3}") for lvl, mv in learnset.level_moves if lvl > 0]
        egg_lines = [_line(mv, "Egg") for mv in sorted(learnset.egg_moves)]
        oth_lines = [_line(mv, "TM") for mv in sorted(set(learnset.tm_moves) | set(learnset.tutor_moves))]

        sections = [s for s in (evo_lines, lv_lines, egg_lines, oth_lines) if s]
        all_lines: list[str] = []
        for i, sec in enumerate(sections):
            if i > 0:
                all_lines.append("-" * 34)
            all_lines.extend(sec)
        all_lines = all_lines or ["*No move data loaded.*"]

        page_size = 18
        chunks = [all_lines[i:i + page_size] for i in range(0, len(all_lines), page_size)] or [[]]

        def _embed_for(idx: int) -> Embed:
            return Embed(
                title=f"{species_emoji(self.species.id, self.shiny)} {self.species.name} \u00b7 Learnable Moves",
                description="\n".join(chunks[idx]), color=0xF52754,
            ).set_footer(
                text=f"Page {idx + 1}/{len(chunks)} \u00b7 {len(evo_lines):,} evolution "
                     f"\u00b7 {len(lv_lines):,} level-up \u00b7 {len(egg_lines):,} egg "
                     f"\u00b7 {len(oth_lines):,} TM/tutor")

        embeds = [_embed_for(i) for i in range(len(chunks))]
        await ctx.response.send_message(
            embed=embeds[0], view=SimplePageView(embeds, ctx.user.id) if len(embeds) > 1 else MISSING,
            ephemeral=True)

    @button(label="Ability Info", emoji="\u2728", style=ButtonStyle.blurple, custom_id="sv:abilities")
    async def btn_abilities(self, ctx: Interaction, _: Button):
        from pokemon.dex.abilities_repo import get_default_repository as get_ability_flavor

        await ctx.response.defer(ephemeral=True, thinking=True)
        if not self.species.abilities:
            await ctx.edit_original_response(content="No ability data available.")
            return
        flavor_repo = get_ability_flavor()
        embed = Embed(
            title=f"{species_emoji(self.species.id, self.shiny)} {self.species.name} \u00b7 Abilities",
            color=0x5865F2)
        for ab in self.species.abilities:
            label = move_display_name(ab.name)
            if ab.is_hidden:
                label += "  *(Hidden Ability)*"
            embed.add_field(name=label, value=flavor_repo.get(ab.name) or "\u2014", inline=False)
        await ctx.edit_original_response(embed=embed)

    @button(label="Alternate Forms", emoji="\U0001f500", style=ButtonStyle.blurple, custom_id="sv:forms")
    async def btn_forms(self, ctx: Interaction, _: Button):
        embeds = []
        for f in self.species.forms:
            e = Embed(title=f"{species_emoji(self.species.id, self.shiny, f.slug)} {self.species.name} - {f.name}",
                       color=0x5865F2)
            e.add_field(name="Type", value=poketypes(f.types), inline=True)
            e.add_field(name=f"Base Stats (BST: {f.bst:,})", value=_stat_bar_lines(f.base_stats, f.bst), inline=False)
            abilities = ", ".join(move_display_name(a.name) + (" (H)" if a.is_hidden else "") for a in f.abilities)
            if abilities:
                e.add_field(name="Abilities", value=abilities, inline=False)
            embeds.append(e)
        await ctx.response.send_message(
            embed=embeds[0], view=SimplePageView(embeds, ctx.user.id) if len(embeds) > 1 else MISSING,
            ephemeral=True)

    @button(label="Mega Evolutions", emoji=MEGA, style=ButtonStyle.blurple, custom_id="sv:megas")
    async def btn_megas(self, ctx: Interaction, _: Button):
        stones = _SPECIES_MEGA_STONES.get(self.species.id, [])
        embeds = []
        for f in self.species.mega_forms:
            e = Embed(title=f"{species_emoji(self.species.id, self.shiny)} {f.name}", color=0x9B59B6)
            e.add_field(name="Type", value=poketypes(f.types), inline=True)
            e.add_field(name=f"Base Stats (BST: {f.bst:,})", value=_stat_bar_lines(f.base_stats, f.bst), inline=False)
            if f.abilities:
                e.add_field(name="Ability", value=move_display_name(f.abilities[0].name), inline=True)
            stone = next((s for s in stones if MEGA_STONES[s].get("form_suffix", "") in f.slug or len(stones) == 1),
                         None)
            if stone:
                e.add_field(name="Required Stone", value=f"{MEGA_STONES[stone]['emoji']} {stone}", inline=False)
            embeds.append(e)
        await ctx.response.send_message(
            embed=embeds[0], view=SimplePageView(embeds, ctx.user.id) if len(embeds) > 1 else MISSING,
            ephemeral=True)


@pokemon_group.command(name="species", description="View detailed information about any Pokemon species")
@app_commands.describe(
    query="National Dex number (e.g. 25) or name (e.g. Pikachu)",
    ephemeral="Whether to make this species visible only to you or not")
@app_commands.autocomplete(query=species_autocomplete)
async def pokemon_species(ctx: Interaction, query: str, ephemeral: bool = True):
    species = resolve_species(query)
    if species is None:
        await ctx.response.send_message(
            f"No Pokemon found matching **`{query}`**. Try a name like **`Pikachu`** or a dex number like "
            "**`25`**.", ephemeral=True)
        return

    shiny = random.randint(1, 4096) == 1
    embed = build_species_embed(species, shiny)
    chart_file = await asyncio.to_thread(_generate_type_chart_image_sync, list(species.types))
    view = SpeciesView(ctx.user.id, species, shiny)
    await ctx.response.send_message(embed=embed, file=chart_file, view=view, ephemeral=ephemeral)


# ---------------------------------------------------------------------------
# /pokemon info
# ---------------------------------------------------------------------------

async def build_pokemon_full_embed(p: dict) -> tuple[Embed, File, View]:
    """The exact embed/hex-chart/view used by `/pokemon info`, factored
    out so other surfaces (like the GTS detail button, eventually) can
    render an identical card. Ported from `_build_pokemon_full_embed`."""
    species_repo = get_species_repo()
    species = species_repo.get(p["species_id"])
    if not species:
        species = Species(id=p["species_id"], name="?", types=(), base_stats={}, abilities=(),
                           height_m=0, weight_kg=0, gender_rate=-1, catch_rate=45, base_experience=64,
                           growth_rate="medium", base_friendship=70, egg_groups=(), color="", shape="",
                           generation_introduced=1, hatch_counter=20, rarity="common")

    form = species.form(p.get("form"))
    display_types = form.types if form else species.types
    display_stats = form.base_stats if form else species.base_stats
    display_bst = form.bst if form else species.bst
    base_name = form.name if form else species.name
    rarity = visual_rarity(species).upper()
    shiny = bool(p["shiny"])

    max_hp = pokemon_max_hp(p, species)
    cur_hp = p.get("current_hp") or max_hp

    ball = POKEBALL_EMOJI.get(p.get("ball_type", "Poke Ball"), "\U0001f534")
    embed = Embed(
        title=f"{ball} {SHINY + ' ' if shiny else ''}{p.get('nickname') or base_name}",
        description=(
            f"{pokemoji(p)} {'**' + (p.get('nickname') or 'Unnamed') + '** \u00b7 '}{base_name}\n"
            f"{poketypes(display_types)}\n{RARITY_EMOJI.get(rarity, chr(0x26AA))} {rarity}"
            + (f"\n\u200b\n**Ability**: {move_display_name(p['ability'])}" if p.get("ability") else "")),
        color=RARITY_COLORS.get(rarity, 0x5865F2))

    from pokemon.commands.leveling import total_xp_for_level
    xp = total_xp_for_level(p["level"], species.growth_rate)
    needed = max(1, total_xp_for_level(p["level"] + 1, species.growth_rate) - xp)
    current = int(p["xp"] - xp)
    filled = max(0, min(6, current * 6 // needed))
    embed.add_field(name="Level", value=str(p["level"]), inline=True)
    embed.add_field(name="Exp.", value=f"`{'#' * filled}{'.' * (6 - filled)}`", inline=True)
    embed.add_field(name="HP", value=f"{0 if p.get('fainted_at') else cur_hp}/{max_hp}", inline=True)
    g = gender_str(p.get("gender"))
    if g:
        embed.add_field(name="Gender", value=f"{g} {p.get('gender', 'genderless').title()}", inline=True)
    embed.add_field(name="Nature", value=p["nature"], inline=True)
    embed.add_field(name="Bond", value=str(p.get("friendship", 70)), inline=True)

    status = p.get("status_cond")
    if status:
        embed.add_field(name="Status", value=f"{STATUS_EMOJI.get(status, '')} {status.replace('_', ' ').title()}",
                         inline=True)
    if p.get("held_item"):
        embed.add_field(name="Held Item", value=item_display(p["held_item"]), inline=True)
    if p.get("fainted_at"):
        dt = datetime.fromisoformat(p["fainted_at"])
        embed.add_field(name=f"{FAINTED} fainted", value=f"<t:{int(dt.timestamp())}:R>", inline=False)

    moves_raw = json.loads(p.get("moves") or "[]")
    if moves_raw:
        from pokemon.commands.moveset import move_summary
        move_lines = []
        for mv in moves_raw:
            summary = move_summary(mv["name"])
            power = summary["base_power"] if summary["base_power"] else "\u2014"
            typ = TYPE_EMOJI.get(summary["type_name"], "\u2754")
            cat_emoji = "\u2694\ufe0f" if summary["category"] == "physical" else \
                "\u2728" if summary["category"] == "special" else "\U0001f300"
            move_lines.append(
                f"{typ} **{move_display_name(mv['name'])}** ({cat_emoji}) \u00b7 {power} BP\n"
                f"        \u00b7 PP {mv['pp']}/{mv['max_pp']}")
        embed.add_field(name="Moves", value="\n".join(move_lines) + "\n\u200b", inline=False)
    else:
        embed.add_field(name="Moves", value="*None assigned yet*\n\u200b", inline=False)

    if display_stats:
        embed.add_field(name=f"Base Stats (BST: {display_bst:,})",
                         value=_stat_bar_lines(display_stats, display_bst), inline=False)

    evos = get_evolution_repo().evolutions_from(p["species_id"])
    if evos:
        evo_lines = [
            f"    L {species_emoji(e.to_id, shiny)} **{e.to_name}** ({evo_condition_desc(e)})"
            for e in evos]
        embed.add_field(name="Evolutions", value="\n".join(evo_lines), inline=False)

    stones = _SPECIES_MEGA_STONES.get(p["species_id"], [])
    if stones:
        form_str = "  /  ".join(f.name for f in species.mega_forms) if species.mega_forms else f"Mega {species.name}"
        stone_str = " / ".join(f"{MEGA_STONES[s]['emoji']} {s}" for s in stones)
        embed.add_field(name="Mega Evolution", value=f"{form_str}\n{stone_str}", inline=False)

    ivs = repository.parse_ivs(p.get("ivs"))
    evs = json.loads(p.get("evs") or "{}")
    hex_file = await asyncio.to_thread(
        _generate_iv_ev_hexagon_sync, ivs, evs, display_stats, p["level"], p["nature"])

    thumb_id = p.get("cosmetic_species_id") or p["species_id"]
    if thumb_id != p["species_id"] and thumb_id:
        pass
    embed.set_thumbnail(
        url=f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/"
            f"official-artwork/{'shiny/' if shiny else ''}{thumb_id}.png")
    embed.set_image(url="attachment://stats_hex.png")
    caught_dt = datetime.fromisoformat(p["caught_at"]) if p.get("caught_at") else None
    caught_str = caught_dt.strftime("%d/%m/%Y %H:%M:%S") if caught_dt else "Unknown"
    embed.set_footer(text=f"Caught {caught_str}  \u00b7  ID #{p['id']}")

    return embed, hex_file, SpeciesLinkView(species, shiny)


@pokemon_group.command(name="info", description="View detailed info about one of your Pokemon")
@app_commands.describe(pokemon="Pokemon ID, nickname, or species name")
async def pokemon_info(ctx: Interaction, pokemon: str):
    p = await resolve_owned_pokemon(ctx, pokemon)
    if not p or p["owner_id"] != ctx.user.id:
        await ctx.response.send_message(f"You don't own any Pokemon matching `{pokemon}`.", ephemeral=True)
        return
    embed, hex_file, view = await build_pokemon_full_embed(p)
    await respond(ctx, embed=embed, file=hex_file, view=view, ephemeral=True)


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

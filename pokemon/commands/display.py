"""Shared display helpers -- turning an owned-Pokémon row (or a `Species`)
into the emoji/name/type text every command's embeds need. Split out
because the monolith's `_pokemoji`/`_pokemon_display_name`/`_pokedisplay`/
etc. were each called from a dozen-plus places across every Pokémon
command; one copy here instead of each cog re-deriving its own.
"""

from __future__ import annotations

from discord import PartialEmoji

from pokemon.dex.species import get_default_repository as get_species_repo

SHINY = "<:shiny:1519556990429565140>"
MALE = "<:male:1519155469795463252>"
FEMALE = "<:female:1519155454784049212>"
HEALTHY = "<:healthy:1519552426992996352>"
FAINTED = "<:fainted:1519552447477973003>"
HOLDING_ITEM = "<:holding_item:1535552388767678545>"
POTION_EMOJI = "<:potion:1519185975195074730>"
REVIVE_EMOJI = "<:revive:1519185994769891471>"

STATUS_EMOJI: dict[str, str] = {
    "burn": "🔥", "paralysis": "⚡", "sleep": "💤",
    "freeze": "🧊", "poison": "☠️", "bad_poison": "💜",
}

TYPE_EMOJI: dict[str, str] = {
    "Normal": "<:type_normal:1518559897799622706>", "Fighting": "<:type_fighting:1518559901398470798>",
    "Flying": "<:type_flying:1518559906620248245>", "Poison": "<:type_poison:1518559912513372170>",
    "Ground": "<:type_ground:1518559916825251850>", "Rock": "<:type_rock:1518559921233199178>",
    "Bug": "<:type_bug:1518559925578760264>", "Ghost": "<:type_ghost:1518559929508560896>",
    "Steel": "<:type_steel:1518559933979824248>", "Fire": "<:type_fire:1518559938253684767>",
    "Water": "<:type_water:1518559943232323615>", "Grass": "<:type_grass:1518559948542574662>",
    "Electric": "<:type_electric:1518559952497676288>", "Psychic": "<:type_psychic:1518559956293521428>",
    "Ice": "<:type_ice:1518559961137938564>", "Dragon": "<:type_dragon:1518559966116450387>",
    "Dark": "<:type_dark:1518559970835042314>", "Fairy": "<:type_fairy:1518559975318884393>",
    "Typeless": "<:type_typeless:1543587296153174116>",
}

# Populated once at startup (see `load_pokemon_emojis`) from the bot's
# uploaded application emojis, keyed (species_id, shiny, form_slug).
# `pokemoji` degrades to a plain "?" ball if this hasn't been populated
# yet (e.g. commands invoked before the loader task finishes), the same
# way the monolith's `.get(..., "❔")` fallback did.
_POKEMON_EMOJIS: dict[tuple[int, bool, str | None], object] = {}


async def load_pokemon_emojis(bot) -> None:
    """Populates `_POKEMON_EMOJIS` from the bot's uploaded application
    emojis. Call once at startup (after the species repository is loaded).
    Ported from `load_pokemon_emojis`, generalized to take a `bot` instead
    of a module-global client instance."""
    from pokemon.dex.species import get_default_repository

    emojis = await bot.fetch_application_emojis()
    by_name = {e.name: e for e in emojis}
    for sp in get_default_repository().all():
        for slug in (None, *(f.slug for f in sp.forms)):
            base = slug or sp.name
            emoji = by_name.get(_emoji_name(base))
            shiny_emoji = by_name.get(_emoji_name("s_" + base))
            if emoji:
                _POKEMON_EMOJIS[(sp.id, False, slug)] = emoji
            if shiny_emoji:
                _POKEMON_EMOJIS[(sp.id, True, slug)] = shiny_emoji


def _emoji_name(text: str) -> str:
    """Ported verbatim from `emoji_name`: Discord emoji names are
    ASCII-only, 2-32 chars, `[a-zA-Z0-9_]`."""
    import re
    from unicodedata import normalize

    base = normalize("NFKD", text).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", base.replace("-", "_").replace("'", "")).strip("_")
    name = re.sub(r"_+", "_", base.strip("_"))
    if len(name) < 2:
        name = (name + "_x")[:2]
    return name[:32].lower()


def pokemoji(p: dict) -> "PartialEmoji | str":
    """Ported verbatim from `_pokemoji`, including the cosmetic-appearance
    override (a Pokémon holding a costume item displays as a different
    species/form than it mechanically is)."""
    cosmetic_sid = p.get("cosmetic_species_id")
    if cosmetic_sid:
        return _POKEMON_EMOJIS.get(
            (cosmetic_sid, bool(p["shiny"]), p.get("cosmetic_form")),
            _POKEMON_EMOJIS.get((cosmetic_sid, bool(p["shiny"]), None), "❔"))
    return _POKEMON_EMOJIS.get(
        (p["species_id"], bool(p["shiny"]), p.get("form")),
        _POKEMON_EMOJIS.get((p["species_id"], bool(p["shiny"]), None), "❔"))


def species_emoji(species_id: int, shiny: bool = False, form_slug: str | None = None) -> "PartialEmoji | str":
    """Like `pokemoji`, but for contexts with no owned-Pokémon row yet --
    e.g. a starter-selection preview. Not a direct port (the monolith
    inlined this exact `_POKEMON_EMOJIS.get(...)` lookup at every such call
    site instead of naming it), but it's the same lookup `pokemoji` does."""
    return _POKEMON_EMOJIS.get((species_id, shiny, form_slug), _POKEMON_EMOJIS.get((species_id, shiny, None), "❔"))


def poketypes(types: tuple[str, ...] | list[str]) -> str:
    """Ported verbatim from `_poketypes`, taking a plain type list/tuple
    instead of a species dict (every call site already has just the types
    on hand -- `species.types` or `species.display_types(form_slug)`)."""
    return " / ".join(f"{TYPE_EMOJI.get(t, '❔')} **{t}**" for t in (types or ["?"]))


def gender_str(gender: str) -> str | None:
    """Ported verbatim from `_gender_str`."""
    return MALE if gender == "male" else FEMALE if gender == "female" else None


def get_pronouns(gender: str) -> dict[str, str]:
    """Ported verbatim from `_get_pronouns`."""
    if gender == "male":
        return {"s": "he", "S": "He", "o": "him", "O": "Him", "p": "his", "P": "His"}
    if gender == "female":
        return {"s": "she", "S": "She", "o": "her", "O": "Her", "p": "her", "P": "Her"}
    return {"s": "it", "S": "It", "o": "it", "O": "It", "p": "its", "P": "Its"}


def pokemon_display_name(p: dict) -> str:
    """A Pokémon's nickname (or species/form name if unnamed), prefixed
    with the shiny sparkle if applicable. Ported verbatim from
    `_pokemon_display_name`."""
    species = get_species_repo().get(p["species_id"])
    base_name = "Unknown"
    if species:
        form = species.form(p.get("form"))
        base_name = form.name if form else species.name
    base = p.get("nickname") or base_name
    return f"{SHINY + ' ' if p['shiny'] else ''}{base}"


def pokedisplay(p: dict) -> str:
    """Bold emoji + name, for prose ("You caught **{pokedisplay}**!").
    Ported verbatim from `_pokedisplay`."""
    return f"**{pokemoji(p)} {pokemon_display_name(p)}**"


def pokedisplay_naked(p: dict) -> str:
    """Same as `pokedisplay` but unbolded, for list rows that apply their
    own formatting. Ported verbatim from `_pokedisplay_naked`."""
    return f"{pokemoji(p)} {pokemon_display_name(p)}"


def item_display(item: str) -> str:
    """Ported verbatim from `_item_display`."""
    from pokemon.dex.items_repo import find_item

    info = find_item(item)
    emoji = info.emoji if info else "🎁"
    return f"**{emoji} {item}**"


def item_display_plural(item: str) -> str:
    """Ported verbatim from `_item_display_plural`."""
    from pokemon.dex.items_repo import find_item

    info = find_item(item)
    emoji = info.emoji if info else "🎁"
    return f"**{emoji} {item}{'s' if item[-1] != 's' else ''}**"


def cmd_mention(qualified_name: str) -> str:
    """A clickable slash-command mention if the command's Discord-assigned
    id is known, else a plain `/name` code span. Ported from `cmd_mention`;
    soft-depends on Track A's eventual `core.commands.CMD_IDS` (populated
    after the bot syncs its command tree) the same way
    `pokemon.commands._db` soft-depends on `core.db`."""
    try:
        from core.commands import CMD_IDS  # type: ignore[import-not-found]
    except ImportError:
        CMD_IDS = {}
    top_level = qualified_name.split(" ", 1)[0]
    cmd_id = CMD_IDS.get(top_level)
    return f"</{qualified_name}:{cmd_id}>" if cmd_id else f"`/{qualified_name}`"

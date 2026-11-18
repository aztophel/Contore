"""Resolving a Pokémon a user refers to by a free-text `pokemon:` command
argument (an id, a nickname, a species name, or a fuzzy match on either)
into an actual owned-Pokémon row -- and, when more than one thing matches,
a picker view so the user disambiguates instead of the command just
guessing wrong.

This is exactly the "repeated code -> shared utility" case: the monolith
had `resolve_owned_pokemon` used from team/collection/trading/breeding/item
commands alike. One copy here, same behavior.
"""

from __future__ import annotations

from difflib import get_close_matches

from discord import Interaction, SelectOption
from discord.ui import Select, View
from discord.utils import MISSING

from pokemon.commands import repository
from pokemon.commands.display import pokemoji, pokemon_display_name
from pokemon.dex.species import get_default_repository as get_species_repo


async def find_pokemon_by_query(
    user_id: int, query: str, candidates: list[dict] | None = None
) -> list[dict]:
    """All of a trainer's Pokémon matching `query`, most-confident match
    first. `candidates`, when given, restricts the search to that list
    (e.g. team.py's `remove` only searches the current team) instead of
    the trainer's whole collection. Ported verbatim from
    `_find_pokemon_by_query`."""
    query = query.strip()
    species_repo = get_species_repo()

    if query.isdigit():
        if candidates is not None:
            p = next((c for c in candidates if c["id"] == int(query)), None)
        else:
            p = await repository.get_pokemon(int(query))
            if p and await repository.pokemon_is_gts_listed(p["id"]):
                p = None
        return [p] if p and p["owner_id"] == user_id else []

    owned = candidates if candidates is not None else await repository.get_user_pokemon(user_id)
    q_lower = query.lower()

    def species_name(p: dict) -> str:
        sp = species_repo.get(p["species_id"])
        return sp.name if sp else ""

    exact = [p for p in owned if p.get("nickname") and p["nickname"].lower() == q_lower]
    if exact:
        return exact
    exact = [p for p in owned if species_name(p).lower() == q_lower]
    if exact:
        return exact

    labels: dict[str, dict] = {}
    for p in owned:
        sp_name = species_name(p)
        if p.get("nickname"):
            labels[p["nickname"]] = p
        if sp_name:
            labels.setdefault(sp_name, p)
        if sp_name and p.get("nickname"):
            labels.setdefault(f"{p['nickname']} ({sp_name})", p)

    close = get_close_matches(query, list(labels.keys()), n=15, cutoff=0.5)
    if not close:
        close = [k for k in labels if q_lower in k.lower()]

    seen: set[int] = set()
    results: list[dict] = []
    for k in close:
        p = labels[k]
        if p["id"] not in seen:
            seen.add(p["id"])
            results.append(p)
    return results


class PokemonPickView(View):
    """A dropdown of up to 25 ambiguous matches; `resolve_owned_pokemon`
    awaits `.wait()` on this and reads back `.chosen`. Ported verbatim
    from `_PokemonPickView`."""

    def __init__(self, user_id: int, matches: list[dict]):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.chosen: dict | None = None
        species_repo = get_species_repo()

        def _species_name(pid: int) -> str:
            sp = species_repo.get(pid)
            return sp.name if sp else "?"

        options = [
            SelectOption(
                label=pokemon_display_name(p)[:25], value=str(p["id"]), emoji=pokemoji(p),
                description=(
                    f"{_species_name(p['species_id'])} \u200b\u00b7\u200b "
                    f"Lv.{p['level']} \u200b\u00b7\u200b #{p['id']}")[:100],
            )
            for p in matches[:25]
        ]
        sel = Select(placeholder="Multiple matches found. Choose one...", options=options)
        sel.callback = self._on_pick
        self.add_item(sel)

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.user_id:
            await ctx.response.send_message("This isn't your selection!", ephemeral=True)
            return False
        return True

    async def _on_pick(self, ctx: Interaction):
        self.chosen = await repository.get_pokemon(int(ctx.data["values"][0]))
        await ctx.response.defer()
        self.stop()


async def respond(ctx: Interaction, content: str | None = None, **kwargs):
    """Use instead of `ctx.response.send_message` after
    `resolve_owned_pokemon()`: the disambiguation picker (if it fired) may
    have already consumed the interaction's initial response, so a second
    `send_message` would fail. Ported verbatim from `_respond`."""
    if ctx.response.is_done():
        if kwargs.get("ephemeral") is not None:
            ephemeral = kwargs["ephemeral"]
            if ctx.response._parent.message and ctx.response._parent.message.flags.ephemeral != ephemeral:
                await ctx.followup.send(content or MISSING, **kwargs)
                return
        ephemeral = kwargs.get("ephemeral", False)
        attachments = []
        try:
            kwargs.pop("ephemeral", None)
            if kwargs.get("file") is not None:
                attachments.append(kwargs.pop("file"))
            if kwargs.get("files") is not None:
                attachments.extend(kwargs.pop("files"))
            await ctx.edit_original_response(content=content, attachments=attachments, view=None, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- matches the monolith's own catch-all here
            print(exc)
            await ctx.followup.send(content or MISSING, ephemeral=ephemeral, files=attachments, **kwargs)
    else:
        await ctx.response.send_message(content, **kwargs)


async def resolve_owned_pokemon(
    ctx: Interaction, query: str, candidates: list[dict] | None = None
) -> dict | None:
    """The main entry point: resolves to a single Pokémon row, prompting
    with `PokemonPickView` if `query` is ambiguous, or `None` if nothing
    matches at all. Ported verbatim from `resolve_owned_pokemon`."""
    matches = await find_pokemon_by_query(ctx.user.id, query, candidates)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    view = PokemonPickView(ctx.user.id, matches)
    await respond(
        ctx, f"Found **{len(matches):,}** Pokémon matching `{query}`. Which one did you mean?",
        view=view, ephemeral=True)
    await view.wait()
    return view.chosen

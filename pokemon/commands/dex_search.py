"""Resolving a free-text `pokemon:`/`species:` command argument (a dex
number or a fuzzy species name) to a `Species` -- and the matching
autocomplete callback. The monolith had this exact body duplicated at
`_pokemon_list_species_autocomplete` and `_species_query_autocomplete`
(and the equivalent match-logic inlined a third time in `/pokemon cr`);
one copy here per the "repeated code -> utility function" ask.
"""

from __future__ import annotations

from discord import Interaction
from discord.app_commands import Choice

from pokemon.dex.species import Species, get_default_repository as get_species_repo


def resolve_species(query: str) -> Species | None:
    """A dex number matches exactly; otherwise an exact (case-insensitive)
    name match wins, falling back to the first substring match. Ported
    verbatim from the matching logic in `/pokemon cr` and `/pokemon list`."""
    repo = get_species_repo()
    q = query.strip()
    if q.isdigit():
        sp = repo.get(int(q))
        if sp:
            return sp
    q_lower = q.lower().replace("-", " ")
    all_species = repo.all()
    return (
        next((s for s in all_species if s.name.lower() == q_lower), None)
        or next((s for s in all_species if q_lower in s.name.lower()), None)
    )


async def species_autocomplete(ctx: Interaction, current: str) -> list[Choice[str]]:
    """Ported verbatim from `_species_query_autocomplete` /
    `_pokemon_list_species_autocomplete` (identical bodies in the
    monolith)."""
    repo = get_species_repo()
    all_species = repo.all()
    q = current.lower().strip()
    results: list[Choice[str]] = []
    if q.isdigit():
        for sp in all_species:
            if str(sp.id).startswith(q):
                results.append(Choice(name=f"#{sp.id:03d} {sp.name}", value=str(sp.id)))
                if len(results) >= 25:
                    return results
    exact = [sp for sp in all_species if sp.name.lower() == q]
    partial = [sp for sp in all_species if q in sp.name.lower() and sp not in exact]
    for sp in (exact + partial)[:25 - len(results)]:
        results.append(Choice(name=f"#{sp.id:03d} {sp.name}", value=str(sp.id)))
    return results[:25]

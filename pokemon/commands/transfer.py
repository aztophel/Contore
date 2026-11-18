"""Shared logic for handing a Pokémon from one trainer to another --
gifting (`/pokemon give`) and `trading.py`'s actual trade command both
need exactly this. Split out per the "repeated code -> utility function"
ask rather than duplicated between the two.

Trade-evolution triggering itself lives in `pokemon.commands.evolution`
(`maybe_trigger_trade_evolution`) -- call it after transferring ownership,
the way `give`/`trade`/`gts fulfill` all do.
"""

from __future__ import annotations

from pokemon.commands import repository


async def apply_transfer_friendship(base_friendship: int, pokemon_id: int, old_owner: int, new_owner: int) -> None:
    """Stores the Pokémon's current friendship under its old trainer (so
    it's restored if it ever comes back to them), and either restores a
    remembered friendship value for the new owner or resets to the
    species' base friendship. Ported verbatim from
    `_apply_transfer_friendship`."""
    p = await repository.get_pokemon(pokemon_id)
    if not p:
        return
    await repository.save_friendship_memory(pokemon_id, old_owner, p.get("friendship") or 70)
    restored = await repository.get_friendship_memory(pokemon_id, new_owner)
    if restored is not None:
        new_friendship = restored
        await repository.clear_friendship_memory(pokemon_id, new_owner)
    else:
        new_friendship = base_friendship
    await repository.set_pokemon_friendship(pokemon_id, new_friendship)


async def auto_add_to_team(user_id: int, pokemon_id: int) -> bool:
    """Slots a newly-received Pokémon onto the recipient's team if they
    have room. Ported verbatim from `_auto_add_to_team`."""
    slots = await repository.get_team_slots(user_id)
    if len(slots) < 6 and pokemon_id not in slots:
        slots.append(pokemon_id)
        await repository.save_team_slots(user_id, slots)
        await repository.mark_rejoined_team(pokemon_id)
        return True
    return False

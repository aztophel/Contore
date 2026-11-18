"""Shared slash-command namespace for `pokemon/commands/*`.

Track B's commands span many files but need to present as one coherent
`/pokemon ...` namespace (plus a separate top-level `/gts ...`). A
`discord.ext.commands.Cog`'s `app_commands.Group` class-attribute is
auto-registered when that specific cog is added -- great for a
self-contained command cluster (see `cogs/admin_deploy.py`'s `deploy`
group), but it doesn't compose across *multiple* cogs that need to hang
commands off the *same* parent group: two cogs each declaring their own
`pokemon` Group would either collide on registration or silently produce
two independent top-level trees instead of one merged `/pokemon ...`.

So instead, this module is the one place the actual `Group` objects get
constructed (with `parent=` wiring done here, once). Every
`pokemon/commands/*.py` file that adds commands imports the group it needs
from here and attaches plain, free-function coroutines to it via
`@group.command(...)` -- not Cog methods -- which is exactly the style the
monolith itself used for `poke_commands`/`poke_config_commands`/
`item_commands`/`team_commands`/`gts_commands` (all module-level `Group`s
with free `async def` handlers). It isn't a style deviation for its own
sake: free functions sidestep the `self`-binding Cog machinery entirely,
which is what makes sharing one group across files unambiguous.

Whichever module ends up owning the bot's actual startup sequence (no
`main.py`/`bot.py` exists in this repo yet -- likely Track A's territory)
just needs to import every `pokemon.commands.*` command-bearing module (so
their decorators run and populate these groups), then call `register(bot)`
once.
"""

from __future__ import annotations

from discord import app_commands
from discord.ext import commands

pokemon_group = app_commands.Group(name="pokemon", description="Pokémon commands")
gts_group = app_commands.Group(name="gts", description="Global Trade Station")

config_group = app_commands.Group(
    name="config", description="Configure Pokémon spawning for this server", parent=pokemon_group)
item_group = app_commands.Group(name="item", description="Use and manage items", parent=pokemon_group)
team_group = app_commands.Group(name="team", description="Manage your battle team", parent=pokemon_group)
# A subgroup rather than two more direct `pokemon_group` children: Discord
# caps a single command/group at 25 children, and `pokemon_group` was
# already one slot away from that limit (21 direct commands + config/item/
# team = 24) before `dex_progress.py` added these two -- as a subgroup
# (itself just ONE more child of `pokemon_group`) they fit exactly at 25;
# as two more direct commands they would have pushed it to 26 and broken
# every `pokemon.commands.*` registration, not just these two.
profile_group = app_commands.Group(
    name="profile", description="Pokédex progress, trainer summary, and wallet balance",
    parent=pokemon_group)


def register(bot: commands.Bot) -> None:
    """Adds the two top-level trees to the bot's command tree. Call once,
    after every `pokemon.commands.*` command module has been imported (so
    their `@group.command(...)` decorators have already attached
    everything they own)."""
    bot.tree.add_command(pokemon_group)
    bot.tree.add_command(gts_group)

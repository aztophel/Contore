"""Contore's entrypoint. Loads every extension in `config.EXTENSIONS`
(tolerating any that fail to import, so a partial checkout -- e.g. only
some tracks merged so far -- still boots with whatever's available),
initializes the database, and syncs the command tree.

Run with: `python bot.py`
"""
from __future__ import annotations
import asyncio
import importlib
import sys
import traceback
from pathlib import Path

import discord
from discord import Object
from discord.ext import commands

import config
from core.db import db_init
from core.db_bootstrap import ensure_postgres_ready
from core.mentions import register_synced_commands

INTENTS = discord.Intents.all()

DATA_DIR = Path(__file__).resolve().parent / "data"


class ContoreBot(commands.Bot):
    def __init__(self):
        # No prefix commands exist anywhere in this codebase (everything is
        # a slash command) -- command_prefix is required by the base class
        # but is otherwise unused.
        super().__init__(command_prefix=commands.when_mentioned, intents=INTENTS)
        self._synced = False

    async def setup_hook(self):
        await db_init()

        for extension in config.EXTENSIONS:
            try:
                await self.load_extension(extension)
                print(f"[bot] loaded {extension}")
            except Exception as e:
                print(f"[bot] SKIPPED {extension}: {e.__class__.__name__}: {e}")
                traceback.print_exception(type(e), e, e.__traceback__, limit=3)

        await self._load_pokemon_subsystem()

    async def _load_pokemon_subsystem(self) -> None:
        """Everything Pokémon needs at boot that isn't a plain cog:

        1. Import every `pokemon.commands.*` command module once (NOT via
           `load_extension` -- see `config.POKEMON_COMMAND_MODULES`'s
           comment), then register their shared `/pokemon` and `/gts`
           command trees exactly once.
        2. Load `pokemon.commands.spawn_message_hook`'s Cog via the normal
           extension path (it doesn't touch the shared Groups, so it's
           exempt from the double-registration hazard).
        3. Build the process-lifetime Move/Ability/Item registries
           (`pokemon.battle.registry`/`items`) and generation overlays, and
           hand the loaded registries to whichever Pokémon module starts a
           battle (see `pokemon.commands.battle_state` -- this is also
           where a future `/deploy pull` would need to trigger a re-load if
           `data/*.json` changed).

        Every step is independently tolerant of failure (a partial
        checkout, or -- today -- `data/moves_data.json` not existing yet;
        see MIGRATION_STATUS.md) so the rest of the bot still boots."""
        try:
            from pokemon.commands._db import ensure_schema
            await ensure_schema()
            print("[bot] pokemon_owned/etc. schema ensured")
        except Exception as e:
            print(f"[bot] SKIPPED pokemon schema: {e.__class__.__name__}: {e}")

        try:
            for module_name in config.POKEMON_COMMAND_MODULES:
                importlib.import_module(module_name)
            from pokemon.commands import groups
            groups.register(self)
            print(f"[bot] registered {len(config.POKEMON_COMMAND_MODULES)} "
                  f"pokemon.commands modules under /pokemon and /gts")
        except Exception as e:
            print(f"[bot] SKIPPED pokemon.commands.*: {e.__class__.__name__}: {e}")
            traceback.print_exception(type(e), e, e.__traceback__, limit=3)

        for extension in config.POKEMON_EVENT_EXTENSIONS:
            try:
                await self.load_extension(extension)
                print(f"[bot] loaded {extension}")
            except Exception as e:
                print(f"[bot] SKIPPED {extension}: {e.__class__.__name__}: {e}")
                traceback.print_exception(type(e), e, e.__traceback__, limit=3)

        try:
            from pokemon.generations import overlays
            overlays.ensure_loaded()
            print(f"[bot] generation overlays: {overlays.summary()}")
        except Exception as e:
            print(f"[bot] SKIPPED generation overlays: {e.__class__.__name__}: {e}")

        try:
            from pokemon.battle import bootstrap as battle_bootstrap
            self.pokemon_registries = battle_bootstrap.load_registries(DATA_DIR)
            print(f"[bot] battle registries: "
                  f"{len(self.pokemon_registries.moves)} moves, "
                  f"{len(self.pokemon_registries.abilities)} abilities, "
                  f"{len(self.pokemon_registries.items)} items")
        except Exception as e:
            self.pokemon_registries = None
            print(f"[bot] SKIPPED battle registries: {e.__class__.__name__}: {e}")

        try:
            from pokemon.commands.auto_heal import start_auto_heal_loops
            start_auto_heal_loops()
            print("[bot] auto_heal/auto_revive loops started")
        except Exception as e:
            print(f"[bot] SKIPPED auto-heal loops: {e.__class__.__name__}: {e}")

        try:
            from pokemon.commands.breeding import start_egg_hatch_loop
            start_egg_hatch_loop(self)
            print("[bot] egg-hatch loop started")
        except Exception as e:
            print(f"[bot] SKIPPED egg-hatch loop: {e.__class__.__name__}: {e}")

    async def on_ready(self):
        if self._synced:
            print(f"{self.user.display_name} has reconnected to Discord.")
            return

        global_cmds = await self.tree.sync()
        guild_cmds = await self.tree.sync(guild=Object(config.HOME_GUILD_ID))
        register_synced_commands(global_cmds + guild_cmds)

        self._synced = True
        print(f"{self.user.display_name} has connected to Discord! "
              f"({len(global_cmds)} global, {len(guild_cmds)} guild-scoped commands synced)")


async def main():
    if not ensure_postgres_ready(config.DATABASE_URL):
        print("Could not automatically prepare Postgres.")
        sys.exit(1)

    if not config.DISCORD_TOKEN:
        print("DISCORD_TOKEN isn't set. Put it in your environment (or a .env "
              "file config.py loads) before running the bot.")
        sys.exit(1)

    bot = ContoreBot()
    async with bot:
        await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())

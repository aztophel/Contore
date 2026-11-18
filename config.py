"""Environment variables and shared constants.

Centralizes everything that used to be a bare literal scattered across the
monolith (`OWNER_ID`, `STORAGE_CHANNEL_ID`, ...). Nothing here should ever
import from `cogs/` or `pokemon/` -- this module sits at the bottom of the
dependency graph so anything can import it safely.

Secrets (the bot token, `DATABASE_URL`) belong in a gitignored `.env` file
loaded here via `os.environ`, never committed as literals.
"""
from __future__ import annotations
import os
from discord import Permissions, Object

# --- Secrets / environment -------------------------------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://contore:contore@localhost:5432/contore")

# Bearer token for the one-off `/level sync-arcane` migration helper (calls
# arcane.bot's leaderboard API to import XP from a server that used to run
# Arcane). The monolith had this hardcoded as a literal JWT in source --
# pulled into an env var here since this file is headed for a public git
# repo. Get a fresh one from your own browser's network tab while logged
# into arcane.bot if you still need this command; leave unset otherwise.
ARCANE_MIGRATION_TOKEN = os.environ.get("ARCANE_MIGRATION_TOKEN", "")

# --- Fixed Discord IDs -------------------------------------------------
OWNER_ID = int(os.environ.get("OWNER_ID", 657079914079715329))
HOME_GUILD_ID = int(os.environ.get("HOME_GUILD_ID", 1225102857431420988))
STORAGE_CHANNEL_ID = int(os.environ.get("STORAGE_CHANNEL_ID", 1498957290202464348))
SUBMISSION_REVIEW_CHANNEL_ID = int(os.environ.get("SUBMISSION_REVIEW_CHANNEL_ID", 1507307115591766127))

# Servers historically whitelisted for certain admin-only surfaces. Not
# currently read anywhere load-bearing, but preserved from the monolith
# rather than silently dropped.
WHITELISTED_ADMIN_SERVERS = [Object(1096833116422799503), Object(1059607635499962408)]

# The permission bundle Contore's own moderation-adjacent admin commands
# are gated behind (`@default_permissions(PERMS)`).
PERMS = Permissions(
    manage_messages=True, manage_threads=True, manage_expressions=True,
    view_audit_log=True, manage_guild=True, manage_nicknames=True,
    kick_members=True, ban_members=True, create_expressions=True,
    moderate_members=True, create_events=True, manage_events=True,
)

# Every top-level extension module (`cogs.<name>`) the bot loads on start.
# Pokémon-side tracks append their own extensions here once those packages
# exist; bot.py tolerates any of these failing to import so a partial
# checkout (e.g. only Track A merged so far) still boots.
EXTENSIONS: list[str] = [
    "cogs.admin_deploy",
    "cogs.admin_tools",
    "cogs.roles",
    "cogs.birthdays",
    "cogs.quotes",
    "cogs.family",
    "cogs.leave_messages",
    "cogs.achievements",
    "cogs.proxies",
    "cogs.text_filters",
    "cogs.fake_nitro",
    "cogs.truth_or_dare",
    "cogs.levelling",
    "cogs.haiku",
    "cogs.tags",
    "cogs.polls",
    "cogs.export",
    "cogs.misc",
    "cogs.help",
]

# Pokémon command modules (Track B). These are NOT loaded via
# `bot.load_extension()` -- see bot.py's `setup_hook` docstring for why:
# unlike cogs/*, several of these modules import each other directly at
# module scope (e.g. duel.py and hunt.py both import team.py for
# get_battle_team), and discord.py's load_extension always re-executes a
# module's top-level code via `exec_module`, even if it's already sitting
# in sys.modules from an earlier plain import. Loading a cross-imported
# module a second time this way re-runs its `@group.command(...)`
# decorators against the same persistent Group object and raises
# CommandAlreadyRegistered. A plain `importlib.import_module()` doesn't
# have this problem (it's a no-op for anything already imported), which is
# what pokemon/commands/groups.py's own docstring says to do: import every
# command-bearing module once, then call `groups.register(bot)`.
#
# Each of these still defines a `setup(bot)` -- a deliberate no-op, kept
# only so a stray `bot.load_extension()` on one of them doesn't hard-crash
# with "no setup function" while debugging; it is not how they're meant to
# be loaded in normal operation.
POKEMON_COMMAND_MODULES: list[str] = [
    "pokemon.commands.boss",
    "pokemon.commands.breeding",
    "pokemon.commands.collection",
    "pokemon.commands.config",
    "pokemon.commands.cr",
    "pokemon.commands.dex_progress",
    "pokemon.commands.duel",
    "pokemon.commands.gts",
    "pokemon.commands.hunt",
    "pokemon.commands.item",
    "pokemon.commands.item_use",
    "pokemon.commands.move_lookup",
    "pokemon.commands.relearn",
    "pokemon.commands.species_info",
    "pokemon.commands.starter",
    "pokemon.commands.team",
    "pokemon.commands.trading",
]

# Loaded via bot.load_extension() (not plain-imported like the list above)
# because its whole job is registering a Cog (SpawnHookCog), not attaching
# commands to a shared Group -- see its own setup()'s docstring. Must be
# loaded AFTER POKEMON_COMMAND_MODULES so the hunt.py/team.py it imports
# from are already populated rather than re-executed.
POKEMON_EVENT_EXTENSIONS: list[str] = [
    "pokemon.commands.spawn_message_hook",
]

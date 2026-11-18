"""Database plumbing for `pokemon/commands/*`.

`core/db.py` (Track A's territory, per `core/OWNER.md`) is meant to
eventually expose a shared `asyncpg` pool for the whole bot, but doesn't
exist yet. Rather than block every Pokémon economy command on Track A
landing that file, this module tries it first and falls back to a small
self-contained pool using the exact same `DATABASE_URL` env-var convention
the monolith used -- so `pokemon/commands` is fully usable today, and
transparently starts sharing Track A's pool the moment `core.db` exists
(same connection string, same database, so either pool sees the same rows;
this module just stops calling `asyncpg.create_pool` itself).

Every `pokemon/commands` module that touches the database should do
`from pokemon.commands._db import pool, affected` rather than importing
`asyncpg`/`os.environ` directly, so this soft-dependency swap only has to
happen in one place.
"""

from __future__ import annotations

import os

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://contore:contore@localhost:5432/contore")

_fallback_pool: asyncpg.Pool | None = None

# The tables `pokemon/commands/repository.py` owns (see ARCHITECTURE.md's
# migration notes and TRACK_B_HANDOFF.md §3 item 3). `CREATE TABLE IF NOT
# EXISTS` + `ALTER ... ADD COLUMN IF NOT EXISTS` throughout so this is safe
# to run against a fresh database or one already populated by the monolith.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS pokemon_config (
    guild_id           BIGINT PRIMARY KEY,
    enabled            INTEGER NOT NULL DEFAULT 0,
    spawn_channel      BIGINT,
    spawn_min          INTEGER NOT NULL DEFAULT 15,
    spawn_chance       DOUBLE PRECISION NOT NULL DEFAULT 0.005,
    catch_xp           INTEGER NOT NULL DEFAULT 25
);
ALTER TABLE pokemon_config ADD COLUMN IF NOT EXISTS generation INTEGER NOT NULL DEFAULT 6;
ALTER TABLE pokemon_config ADD COLUMN IF NOT EXISTS dex_cap_generation INTEGER;
ALTER TABLE pokemon_config ADD COLUMN IF NOT EXISTS restrict_mode TEXT NOT NULL DEFAULT 'spawn_only';

CREATE TABLE IF NOT EXISTS pokemon_owned (
    id                  BIGSERIAL PRIMARY KEY,
    owner_id            BIGINT NOT NULL,
    original_trainer_id BIGINT NOT NULL,
    species_id          INTEGER NOT NULL,
    form                TEXT,
    nickname            TEXT,
    gender              TEXT DEFAULT 'genderless',
    level               INTEGER NOT NULL DEFAULT 1,
    xp                  DOUBLE PRECISION NOT NULL DEFAULT 0,
    current_hp          INTEGER,
    fainted_at          TEXT,
    hurt_at             TEXT,
    pp_reset_at         TEXT,
    ivs                 TEXT NOT NULL DEFAULT '{}',
    evs                 TEXT DEFAULT '{}',
    nature              TEXT NOT NULL,
    ability             TEXT,
    shiny               INTEGER NOT NULL DEFAULT 0,
    cosmetic_species_id INTEGER DEFAULT NULL,
    cosmetic_form       TEXT DEFAULT NULL,
    caught_at           TEXT NOT NULL,
    caught_guild_id     BIGINT,
    ball_type           TEXT DEFAULT 'Poké Ball',
    held_item           TEXT,
    moves               TEXT DEFAULT '[]',
    friendship          INTEGER DEFAULT 70,
    status_cond         TEXT,
    favorite            INTEGER NOT NULL DEFAULT 0,
    team_left_at        TEXT
);
CREATE TABLE IF NOT EXISTS pokemon_trainer (
    user_id         BIGINT PRIMARY KEY,
    dex_seen        TEXT NOT NULL DEFAULT '[]',
    dex_milestones  TEXT NOT NULL DEFAULT '[]',
    has_legendary   INTEGER NOT NULL DEFAULT 0,
    has_mythical    INTEGER NOT NULL DEFAULT 0,
    has_traded_away INTEGER NOT NULL DEFAULT 0,
    beaten_contore  INTEGER NOT NULL DEFAULT 0,
    notify_hunt     INTEGER NOT NULL DEFAULT 0,
    notify_breed    INTEGER NOT NULL DEFAULT 0,
    duel_wins       INTEGER NOT NULL DEFAULT 0,
    duel_losses     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pokemon_items (
    user_id BIGINT NOT NULL, item TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, item)
);
CREATE TABLE IF NOT EXISTS pokemon_team_members (
    user_id    BIGINT  NOT NULL,
    pokemon_id BIGINT  NOT NULL,
    slot       INTEGER NOT NULL,
    PRIMARY KEY (user_id, slot)
);
CREATE TABLE IF NOT EXISTS pokemon_team_presets (
    user_id BIGINT NOT NULL,
    name    TEXT   NOT NULL,
    slots   TEXT   NOT NULL DEFAULT '[]',
    PRIMARY KEY (user_id, name)
);
CREATE TABLE IF NOT EXISTS pokemon_starters (
    user_id BIGINT PRIMARY KEY, species_id INTEGER NOT NULL, chosen_at TEXT
);
CREATE TABLE IF NOT EXISTS pokemon_gts_listings (
    id              BIGSERIAL PRIMARY KEY,
    lister_id       BIGINT NOT NULL,
    pokemon_id      BIGINT NOT NULL,
    want_conditions TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    fulfilled_by    BIGINT,
    fulfilled_at    TEXT
);
CREATE TABLE IF NOT EXISTS pokemon_hunt_cooldowns (
    user_id  BIGINT PRIMARY KEY,
    ready_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pokemon_breed_cooldowns (
    pokemon_id BIGINT PRIMARY KEY,
    ready_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pokemon_eggs (
    id BIGSERIAL PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    parent_a BIGINT, parent_b BIGINT,
    species_id INTEGER NOT NULL,
    ball_type TEXT NOT NULL DEFAULT 'Poké Ball',
    ivs TEXT NOT NULL DEFAULT '{}',
    moves TEXT NOT NULL DEFAULT '[]',
    form TEXT,
    nature TEXT,
    ability TEXT,
    created_at TEXT NOT NULL,
    hatch_at TEXT NOT NULL,
    hatch_guild_id BIGINT
);
CREATE TABLE IF NOT EXISTS pokemon_friendship_memory (
    pokemon_id  BIGINT NOT NULL,
    trainer_id  BIGINT NOT NULL,
    friendship  INTEGER NOT NULL,
    stored_at   TEXT    NOT NULL,
    PRIMARY KEY (pokemon_id, trainer_id)
);
CREATE TABLE IF NOT EXISTS pokedollars (
    user_id BIGINT PRIMARY KEY,
    amount  BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS legendary_catches (
    user_id    BIGINT NOT NULL,
    species_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, species_id)
);
CREATE TABLE IF NOT EXISTS pokemon_fused (
    kyurem_id  BIGINT PRIMARY KEY,
    partner_id BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS pokemon_boss_configs (
    user_id       BIGINT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    accept_label  TEXT NOT NULL DEFAULT 'DEFY',
    decline_label TEXT NOT NULL DEFAULT 'BACK DOWN',
    challenge_line TEXT NOT NULL DEFAULT 'You will fail.',
    first_win_role_message TEXT,
    configured_by BIGINT NOT NULL,
    configured_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pokemon_boss_team_slots (
    boss_user_id BIGINT  NOT NULL REFERENCES pokemon_boss_configs(user_id) ON DELETE CASCADE,
    slot         INTEGER NOT NULL,
    species_id   INTEGER NOT NULL,
    level        INTEGER NOT NULL DEFAULT 100,
    nickname     TEXT,
    gender       TEXT NOT NULL DEFAULT 'genderless',
    moves        TEXT NOT NULL DEFAULT '[]',
    held_item    TEXT,
    nature       TEXT NOT NULL DEFAULT 'Hardy',
    ability      TEXT,
    evs          TEXT NOT NULL DEFAULT '{}',
    ivs          TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (boss_user_id, slot)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_contore_first_winner
    ON pokemon_trainer(beaten_contore) WHERE beaten_contore = 1;
CREATE INDEX IF NOT EXISTS idx_pokemon_owned_owner ON pokemon_owned(owner_id);
CREATE INDEX IF NOT EXISTS idx_team_members_pokemon ON pokemon_team_members(pokemon_id);
CREATE INDEX IF NOT EXISTS idx_pokemon_owned_hurt ON pokemon_owned(hurt_at) WHERE hurt_at IS NOT NULL AND fainted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pokemon_owned_fainted ON pokemon_owned(fainted_at) WHERE fainted_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pokemon_owned_pp_reset ON pokemon_owned(pp_reset_at);
CREATE INDEX IF NOT EXISTS idx_gts_active_created ON pokemon_gts_listings(status, created_at DESC) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_gts_pokemon ON pokemon_gts_listings(pokemon_id);
CREATE INDEX IF NOT EXISTS idx_gts_lister_active ON pokemon_gts_listings(lister_id, status);
CREATE INDEX IF NOT EXISTS idx_pokemon_owned_team_scan ON pokemon_owned(id) WHERE fainted_at IS NULL;
"""


async def _bootstrap_fallback_pool() -> asyncpg.Pool:
    global _fallback_pool
    if _fallback_pool is None:
        _fallback_pool = await asyncpg.create_pool(
            dsn=DATABASE_URL, min_size=2, max_size=10, command_timeout=30
        )
        await _fallback_pool.execute(_SCHEMA)
    return _fallback_pool


async def pool() -> asyncpg.Pool:
    """The pool every `pokemon/commands` query function should use.

    Tries `core.db.get_pool()` first (Track A's eventual shared pool);
    falls back to a private pool against the same `DATABASE_URL` if that
    module doesn't exist yet or doesn't expose that name. Both paths point
    at the same database, so this is purely about *which process-level
    connection pool* serves the query, not which data it sees.
    """
    try:
        from core.db import get_pool  # type: ignore[import-not-found]
    except ImportError:
        return await _bootstrap_fallback_pool()
    return await get_pool()


async def ensure_schema() -> None:
    """Runs `_SCHEMA` against whichever pool `pool()` resolves to.

    `_bootstrap_fallback_pool()` already does this the one time it's used
    (no `core.db` present), but the moment `core.db` DOES exist -- true for
    this merged tree, since it's Track A's module -- `pool()` returns
    `core.db.get_pool()`'s shared pool instead, which was never told about
    any of `pokemon_owned`/`pokemon_eggs`/`pokemon_gts_listings`/etc.
    `core.db.db_init()` only creates ITS OWN tables. Call this once at
    startup (see `bot.py`'s `_load_pokemon_subsystem`) whenever `core.db`
    is present, or every `pokemon.commands.repository` query will fail
    with `UndefinedTableError` the moment it runs against a fresh
    database -- which is exactly what happens without this call."""
    p = await pool()
    await p.execute(_SCHEMA)


def affected(status: str) -> int:
    """Parses asyncpg's `execute()` return value (`"UPDATE 3"`,
    `"DELETE 1"`, ...) into the row count, mirroring the monolith's
    `_affected()` so every ported `db_*` function's `bool`/`int` return
    semantics stay identical."""
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0

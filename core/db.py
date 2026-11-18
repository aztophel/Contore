"""Database layer for every non-Pokémon table.

Owns the shared `asyncpg` connection pool (`get_pool()` / `db_init()`).
Pokémon-side packages reuse the *same* pool -- they call `get_pool()`
after `db_init()` has run and create their own tables against it, they
just don't get a say in this module's schema. This keeps exactly one
pool for the whole process without core/ needing to know Pokémon's
schema, or Pokémon needing to know core's.

`bot.py` is the only caller of `db_init()`.
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta

import asyncpg

from config import DATABASE_URL

_pool: asyncpg.Pool | None = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool used before db_init() was called.")
    return _pool


def _affected(status: str) -> int:
    """asyncpg's `execute()` returns a status string like "DELETE 3"; this
    pulls the row count back out of it."""
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def db_init() -> None:
    global _pool
    _pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=4, max_size=20, command_timeout=30)
    await _pool.execute("""
    CREATE TABLE IF NOT EXISTS auto_roles (
        guild_id BIGINT NOT NULL,
        target   TEXT   NOT NULL,
        role_id  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, target, role_id)
    );
    CREATE TABLE IF NOT EXISTS roles_on_messages (
        id          BIGSERIAL PRIMARY KEY,
        guild_id    BIGINT NOT NULL,
        role_id     BIGINT NOT NULL,
        content     TEXT   NOT NULL,
        auth_type   TEXT   NOT NULL DEFAULT 'Administrators',
        auth_id     BIGINT,
        target_type TEXT   NOT NULL DEFAULT 'Mentions',
        target_user BIGINT,
        time_limit  DOUBLE PRECISION DEFAULT -1,
        take_role   INTEGER DEFAULT 0,
        sub_match   INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS scheduled_roles (
        id           BIGSERIAL PRIMARY KEY,
        guild_id     BIGINT NOT NULL,
        role_id      BIGINT NOT NULL,
        user_id      BIGINT NOT NULL,
        date_pattern TEXT   NOT NULL,
        type         TEXT   NOT NULL,
        duration     INTEGER,
        extra        TEXT,
        assigned_at  TEXT
    );
    CREATE TABLE IF NOT EXISTS birthdays (
        user_id  BIGINT PRIMARY KEY,
        birthday TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS birthday_data (
        guild_id   BIGINT PRIMARY KEY,
        role_id    BIGINT DEFAULT -1,
        channel_id BIGINT DEFAULT -1
    );
    CREATE TABLE IF NOT EXISTS quotes (
        id         BIGSERIAL PRIMARY KEY,
        user_id    BIGINT NOT NULL,
        author_id  BIGINT NOT NULL,
        guild_id   BIGINT NOT NULL,
        channel_id BIGINT NOT NULL,
        message_id BIGINT NOT NULL,
        content    TEXT,
        created_at TEXT,
        added_at   TEXT,
        snapshot   TEXT
    );
    CREATE TABLE IF NOT EXISTS family_partners (
        user_id    BIGINT NOT NULL,
        partner_id BIGINT NOT NULL,
        PRIMARY KEY (user_id, partner_id)
    );
    CREATE TABLE IF NOT EXISTS family_parent_child (
        parent_id BIGINT NOT NULL,
        child_id  BIGINT NOT NULL,
        PRIMARY KEY (parent_id, child_id)
    );
    CREATE TABLE IF NOT EXISTS family_data (
        user_id      BIGINT PRIMARY KEY,
        title        TEXT,
        restrictions TEXT,
        hidden       TEXT
    );
    CREATE TABLE IF NOT EXISTS leave_msgs (
        guild_id      BIGINT PRIMARY KEY,
        msg           TEXT,
        chan          BIGINT,
        embed_data    TEXT,
        msg_image_url TEXT
    );
    CREATE TABLE IF NOT EXISTS achievements_earned (
        guild_id       BIGINT NOT NULL,
        user_id        BIGINT NOT NULL,
        achievement_id TEXT   NOT NULL,
        timestamp      TEXT   NOT NULL,
        PRIMARY KEY (guild_id, user_id, achievement_id)
    );
    CREATE TABLE IF NOT EXISTS achievement_channels (
        guild_id   BIGINT PRIMARY KEY,
        channel_id BIGINT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS achievement_defs (
        guild_id       BIGINT NOT NULL,
        achievement_id TEXT   NOT NULL,
        name           TEXT   NOT NULL,
        description    TEXT,
        icon           TEXT   NOT NULL DEFAULT '🏆',
        points         INTEGER NOT NULL DEFAULT 10,
        rarity         TEXT   NOT NULL DEFAULT 'COMMON',
        type           TEXT   NOT NULL DEFAULT 'manual',
        secret         INTEGER NOT NULL DEFAULT 0,
        trigger_data   TEXT,
        created_at     TEXT,
        xp_reward      INTEGER NOT NULL DEFAULT 0,
        role_rewards   TEXT,
        sort_order     INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, achievement_id)
    );
    CREATE TABLE IF NOT EXISTS achievement_progress (
        guild_id  BIGINT NOT NULL,
        user_id   BIGINT NOT NULL,
        metric    TEXT   NOT NULL,
        value     DOUBLE PRECISION NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id, metric)
    );
    CREATE TABLE IF NOT EXISTS proxies (
        user_id             BIGINT NOT NULL,
        proxy_key           TEXT   NOT NULL,
        invocation          TEXT   NOT NULL,
        name                TEXT,
        tracked_user_name   BIGINT,
        tracked_user_avatar BIGINT,
        avatar_url          TEXT,
        nicks               TEXT,
        description         TEXT,
        PRIMARY KEY (user_id, proxy_key)
    );
    CREATE TABLE IF NOT EXISTS proxy_autoproxy (
        user_id        BIGINT PRIMARY KEY,
        enabled        INTEGER NOT NULL DEFAULT 0,
        last_proxy_key TEXT,
        last_used_at   TEXT,
        timeout_secs   INTEGER
    );
    CREATE TABLE IF NOT EXISTS uwu (
        guild_id BIGINT NOT NULL,
        user_id  BIGINT NOT NULL,
        type     INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS submissions (
        sub_id          TEXT   PRIMARY KEY,
        type            TEXT   NOT NULL,
        question        TEXT   NOT NULL,
        submitter_id    BIGINT NOT NULL,
        submitter_name  TEXT   NOT NULL,
        message_id      BIGINT,
        guild_id        BIGINT,
        origin_guild_id BIGINT,
        status          TEXT   NOT NULL DEFAULT 'pending'
    );
    CREATE TABLE IF NOT EXISTS levelling_config (
        guild_id  BIGINT PRIMARY KEY,
        enabled   INTEGER NOT NULL DEFAULT 0,
        xp_config TEXT    NOT NULL,
        levelup   TEXT    NOT NULL,
        roles     TEXT    NOT NULL
    );
    CREATE TABLE IF NOT EXISTS levelling_users (
        guild_id        BIGINT NOT NULL,
        user_id         BIGINT NOT NULL,
        xp              DOUBLE PRECISION NOT NULL DEFAULT 0,
        level           INTEGER NOT NULL DEFAULT 0,
        total_xp        DOUBLE PRECISION NOT NULL DEFAULT 0,
        last_message_ts DOUBLE PRECISION NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS user_profiles (
        user_id      BIGINT PRIMARY KEY,
        custom_bg    TEXT,
        custom_color TEXT
    );
    CREATE TABLE IF NOT EXISTS haiku_config (
        guild_id BIGINT PRIMARY KEY,
        enabled  INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS nitro_config (
        guild_id BIGINT PRIMARY KEY,
        enabled  INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS tags (
        guild_id       BIGINT NOT NULL,
        name           TEXT   NOT NULL,
        content        TEXT   NOT NULL,
        creator_id     BIGINT NOT NULL,
        created_at     TEXT   NOT NULL,
        uses           INTEGER NOT NULL DEFAULT 0,
        image_url      TEXT,
        embed_data     TEXT,
        trigger_phrase TEXT,
        PRIMARY KEY (guild_id, name)
    );
    CREATE TABLE IF NOT EXISTS tag_config (
        guild_id          BIGINT PRIMARY KEY,
        restrict_creation INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS polls (
        message_id   BIGINT PRIMARY KEY,
        channel_id   BIGINT NOT NULL,
        guild_id     BIGINT,
        requester_id BIGINT NOT NULL,
        question     TEXT   NOT NULL,
        options      TEXT   NOT NULL,
        votes        TEXT   NOT NULL DEFAULT '{}',
        ended        INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT   NOT NULL,
        anonymous    INTEGER NOT NULL DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_roles_on_messages_guild ON roles_on_messages(guild_id);
    CREATE INDEX IF NOT EXISTS idx_levelling_users_guild_xp ON levelling_users(guild_id, total_xp DESC);
    CREATE INDEX IF NOT EXISTS idx_quotes_user ON quotes(user_id);
    """)


async def db_close() -> None:
    """Closes the connection pool. Called from `admin_tools.py` during a
    graceful shutdown (`/stop`, `/terminate`) so connections aren't just
    dropped out from under Postgres."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


_lvl_cfg_cache: dict[int, dict] = {}
_lvl_user_cache: dict[tuple, dict] = {}
_ach_def_cache: dict[int, list] = {}
_prog_cache: dict[tuple, dict] = {}


# region Roles
async def db_get_auto_role_targets(guild_id: int) -> dict[str, list[int]]:
    rows = await get_pool().fetch("SELECT target, role_id FROM auto_roles WHERE guild_id=$1", guild_id)
    result: dict[str, list[int]] = {}
    for r in rows:
        result.setdefault(r["target"], []).append(r["role_id"])
    return result


async def db_add_auto_role(guild_id: int, target: str, role_id: int):
    await get_pool().execute(
        "INSERT INTO auto_roles VALUES($1,$2,$3) ON CONFLICT DO NOTHING", guild_id, str(target), role_id)


async def db_remove_auto_role(guild_id: int, target: str, role_id: int | None = None) -> bool:
    if role_id is None:
        status = await get_pool().execute("DELETE FROM auto_roles WHERE guild_id=$1 AND target=$2", guild_id, str(target))
    else:
        status = await get_pool().execute(
            "DELETE FROM auto_roles WHERE guild_id=$1 AND target=$2 AND role_id=$3", guild_id, str(target), role_id)
    return _affected(status) > 0


async def db_clear_auto_roles(guild_id: int) -> bool:
    status = await get_pool().execute("DELETE FROM auto_roles WHERE guild_id=$1", guild_id)
    return _affected(status) > 0


def _rom_to_params(row: dict) -> dict:
    return {"id": row["id"], "role": row["role_id"], "content": row["content"],
            "auth_type": row["auth_type"], "auth_id": row["auth_id"],
            "type": row["target_type"], "target_user": row["target_user"],
            "time_limit": row["time_limit"] if row["time_limit"] != -1 else -1,
            "take": bool(row["take_role"]), "sub_match": bool(row["sub_match"])}


async def db_get_roles_on_messages(guild_id: int) -> list[dict]:
    rows = await get_pool().fetch("SELECT * FROM roles_on_messages WHERE guild_id=$1 ORDER BY id", guild_id)
    return [_rom_to_params(dict(r)) for r in rows]


async def db_add_role_on_message(guild_id: int, p: dict):
    await get_pool().execute(
        "INSERT INTO roles_on_messages(guild_id, role_id, content, auth_type, auth_id, "
        "target_type, target_user, time_limit, take_role, sub_match) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        guild_id, p["role"], p["content"], p["auth_type"], p.get("auth_id"),
        p["type"], p.get("target_user"), p.get("time_limit", -1),
        int(p.get("take", False)), int(p.get("sub_match", False)))


async def db_delete_role_on_message(guild_id: int, rom_id: int) -> bool:
    status = await get_pool().execute("DELETE FROM roles_on_messages WHERE id=$1 AND guild_id=$2", rom_id, guild_id)
    return _affected(status) > 0


async def db_clear_roles_on_messages(guild_id: int) -> bool:
    status = await get_pool().execute("DELETE FROM roles_on_messages WHERE guild_id=$1", guild_id)
    return _affected(status) > 0


async def db_get_scheduled_roles(guild_id: int) -> list[dict]:
    rows = await get_pool().fetch("SELECT * FROM scheduled_roles WHERE guild_id=$1", guild_id)
    result = []
    for r in rows:
        d = dict(r)
        d["_id"] = d.pop("id")
        if d.get("extra"):
            d.update(json.loads(d.pop("extra")))
        else:
            d.pop("extra", None)
        if d.get("assigned_at"):
            d["assigned_at"] = datetime.fromisoformat(d["assigned_at"])
        result.append(d)
    return result


async def db_get_scheduled_roles_by_user(user_id: int) -> list[dict]:
    rows = await get_pool().fetch("SELECT * FROM scheduled_roles WHERE user_id=$1", user_id)
    return [dict(r) for r in rows]


async def db_add_scheduled_role(guild_id: int, s: dict) -> int:
    extra_keys = {k: v for k, v in s.items()
                  if k not in ("role_id", "user_id", "date_pattern", "type", "duration", "assigned_at")}
    return await get_pool().fetchval(
        "INSERT INTO scheduled_roles(guild_id, role_id, user_id, date_pattern, type, duration, extra) "
        "VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING id",
        guild_id, s["role_id"], s["user_id"], s["date_pattern"], s["type"],
        s.get("duration"), json.dumps(extra_keys) if extra_keys else None)


async def db_remove_scheduled_role(schedule_id: int) -> bool:
    status = await get_pool().execute("DELETE FROM scheduled_roles WHERE id=$1", schedule_id)
    return _affected(status) > 0


async def db_set_scheduled_role_assigned(schedule_id: int, dt):
    await get_pool().execute("UPDATE scheduled_roles SET assigned_at=$1 WHERE id=$2", dt.isoformat(), schedule_id)
# endregion


# region Birthdays
async def db_get_birthday(user_id: int) -> str | None:
    row = await get_pool().fetchrow("SELECT birthday FROM birthdays WHERE user_id=$1", user_id)
    return row["birthday"] if row else None


async def db_set_birthday(user_id: int, birthday: str):
    await get_pool().execute(
        "INSERT INTO birthdays VALUES($1,$2) ON CONFLICT (user_id) DO UPDATE SET birthday=excluded.birthday",
        user_id, birthday)


async def db_delete_birthday(user_id: int) -> bool:
    status = await get_pool().execute("DELETE FROM birthdays WHERE user_id=$1", user_id)
    return _affected(status) > 0


async def db_get_all_birthdays() -> dict[int, str]:
    rows = await get_pool().fetch("SELECT user_id, birthday FROM birthdays")
    return {r["user_id"]: r["birthday"] for r in rows}


async def db_get_birthday_data(guild_id: int) -> tuple[int, int]:
    row = await get_pool().fetchrow("SELECT role_id, channel_id FROM birthday_data WHERE guild_id=$1", guild_id)
    return (row["role_id"], row["channel_id"]) if row else (-1, -1)


async def db_set_birthday_data(guild_id: int, role_id: int, channel_id: int):
    await get_pool().execute(
        "INSERT INTO birthday_data VALUES($1,$2,$3) ON CONFLICT (guild_id) "
        "DO UPDATE SET role_id=excluded.role_id, channel_id=excluded.channel_id",
        guild_id, role_id, channel_id)
# endregion


# region Quotes
async def db_get_quotes(user_id: int) -> list[dict]:
    rows = await get_pool().fetch("SELECT * FROM quotes WHERE user_id=$1 ORDER BY id", user_id)
    return [dict(r) for r in rows]


async def db_add_quote(user_id: int, author_id: int, guild_id: int, channel_id: int, message_id: int,
                        content: str, created_at: str, added_at: str, snapshot: list[dict] | None = None):
    await get_pool().execute(
        "INSERT INTO quotes(user_id, author_id, guild_id, channel_id, message_id, content, created_at, added_at, snapshot)"
        " VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        user_id, author_id, guild_id, channel_id, message_id, content, created_at, added_at,
        json.dumps(snapshot) if snapshot is not None else None)


async def db_remove_quote(user_id: int, quote_id: int) -> bool:
    status = await get_pool().execute("DELETE FROM quotes WHERE id=$1 AND user_id=$2", quote_id, user_id)
    return _affected(status) > 0
# endregion


# region Family
def _load_restrictions(raw: str | None) -> dict:
    if not raw:
        return {"mode": "blacklist", "lists": {}}
    data = json.loads(raw)
    data["lists"] = {
        int(k): {"mode": v.get("mode", "blacklist"), "users": set(v.get("users", []))}
        for k, v in data.get("lists", {}).items()
    }
    data.setdefault("mode", "blacklist")
    return data


async def db_ensure_family(user_id: int):
    await get_pool().execute("INSERT INTO family_data(user_id) VALUES($1) ON CONFLICT DO NOTHING", user_id)


async def db_get_family(user_id: int) -> dict:
    row = await get_pool().fetchrow("SELECT * FROM family_data WHERE user_id=$1", user_id)
    partners = await get_pool().fetch("SELECT partner_id FROM family_partners WHERE user_id=$1", user_id)
    children = await get_pool().fetch("SELECT child_id FROM family_parent_child WHERE parent_id=$1", user_id)
    parents = await get_pool().fetch("SELECT parent_id FROM family_parent_child WHERE child_id=$1", user_id)
    return {
        "partners": set(r["partner_id"] for r in partners),
        "children": set(r["child_id"] for r in children),
        "parents": set(r["parent_id"] for r in parents),
        "title": row["title"] if row else None,
        "restrictions": _load_restrictions(row["restrictions"] if row else None),
        "hidden": set(json.loads(row["hidden"])) if row and row["hidden"] else set(),
    }


async def db_get_family_parents(user_id: int) -> set[int]:
    rows = await get_pool().fetch("SELECT parent_id FROM family_parent_child WHERE child_id=$1", user_id)
    return set(r["parent_id"] for r in rows)


async def db_get_family_restrictions(user_id: int) -> dict:
    row = await get_pool().fetchrow("SELECT * FROM family_data WHERE user_id=$1", user_id)
    return _load_restrictions(row["restrictions"] if row else None)


async def db_add_family_partner(a: int, b: int):
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("INSERT INTO family_partners VALUES($1,$2) ON CONFLICT DO NOTHING", a, b)
            await conn.execute("INSERT INTO family_partners VALUES($1,$2) ON CONFLICT DO NOTHING", b, a)


async def db_remove_family_partner(a: int, b: int) -> bool:
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            status = await conn.execute("DELETE FROM family_partners WHERE user_id=$1 AND partner_id=$2", a, b)
            await conn.execute("DELETE FROM family_partners WHERE user_id=$1 AND partner_id=$2", b, a)
    return _affected(status) > 0


async def db_is_family_partner(a: int, b: int) -> bool:
    row = await get_pool().fetchrow("SELECT 1 FROM family_partners WHERE user_id=$1 AND partner_id=$2", a, b)
    return row is not None


async def db_add_family_child(parent: int, child: int):
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("INSERT INTO family_data(user_id) VALUES($1) ON CONFLICT DO NOTHING", parent)
            await conn.execute("INSERT INTO family_data(user_id) VALUES($1) ON CONFLICT DO NOTHING", child)
            await conn.execute("INSERT INTO family_parent_child VALUES($1,$2) ON CONFLICT DO NOTHING", parent, child)


async def db_remove_family_child(parent: int, child: int) -> bool:
    status = await get_pool().execute("DELETE FROM family_parent_child WHERE parent_id=$1 AND child_id=$2", parent, child)
    return _affected(status) > 0


async def db_is_family_child(parent: int, child: int) -> bool:
    row = await get_pool().fetchrow("SELECT 1 FROM family_parent_child WHERE parent_id=$1 AND child_id=$2", parent, child)
    return row is not None


async def db_set_family_title(user_id: int, title: str | None):
    await db_ensure_family(user_id)
    await get_pool().execute("UPDATE family_data SET title=$1 WHERE user_id=$2", title, user_id)


async def db_set_family_restrictions(user_id: int, restrictions: dict):
    await db_ensure_family(user_id)
    await get_pool().execute(
        "UPDATE family_data SET restrictions=$1 WHERE user_id=$2", json.dumps(restrictions, default=list), user_id)


async def db_set_family_hidden(user_id: int, hidden: set):
    await db_ensure_family(user_id)
    await get_pool().execute("UPDATE family_data SET hidden=$1 WHERE user_id=$2", json.dumps(list(hidden)), user_id)


async def db_get_all_family_user_ids() -> list[int]:
    rows = await get_pool().fetch("SELECT user_id FROM family_data")
    return [r["user_id"] for r in rows]
# endregion


# region Leave messages
async def db_get_leave_msg(guild_id: int) -> dict:
    row = await get_pool().fetchrow("SELECT msg, chan, embed_data, msg_image_url FROM leave_msgs WHERE guild_id=$1", guild_id)
    if not row:
        return {"msg": None, "chan": None, "embed": None, "msg_image": None}
    return {"msg": row["msg"], "chan": row["chan"],
            "embed": json.loads(row["embed_data"]) if row["embed_data"] else None,
            "msg_image": row["msg_image_url"]}


async def db_set_leave_msg(guild_id: int, msg: str | None, chan: int | None,
                            embed: dict | None = None, msg_image: str | None = None):
    await get_pool().execute(
        "INSERT INTO leave_msgs(guild_id, msg, chan, embed_data, msg_image_url) VALUES($1,$2,$3,$4,$5) "
        "ON CONFLICT (guild_id) DO UPDATE SET msg=excluded.msg, chan=excluded.chan, "
        "embed_data=excluded.embed_data, msg_image_url=excluded.msg_image_url",
        guild_id, msg, chan, json.dumps(embed) if embed else None, msg_image)
# endregion


# region Achievements
def _row_to_ach(row) -> dict:
    d = dict(row)
    d["secret"] = bool(d["secret"])
    if d.get("trigger_data"):
        d["trigger"] = json.loads(d["trigger_data"])
    d.pop("trigger_data", None)
    d["role_rewards"] = json.loads(d["role_rewards"]) if d.get("role_rewards") else []
    return d


async def db_get_user_achievements(guild_id: int, user_id: int) -> dict[str, str]:
    rows = await get_pool().fetch(
        "SELECT achievement_id, timestamp FROM achievements_earned WHERE guild_id=$1 AND user_id=$2", guild_id, user_id)
    return {r["achievement_id"]: r["timestamp"] for r in rows}


async def db_has_achievement(guild_id: int, user_id: int, achievement_id: str) -> bool:
    row = await get_pool().fetchrow(
        "SELECT 1 FROM achievements_earned WHERE guild_id=$1 AND user_id=$2 AND achievement_id=$3",
        guild_id, user_id, achievement_id)
    return row is not None


async def db_grant_achievement(guild_id: int, user_id: int, achievement_id: str, timestamp: str) -> bool:
    row = await get_pool().fetchrow(
        "INSERT INTO achievements_earned VALUES($1,$2,$3,$4) "
        "ON CONFLICT (guild_id, user_id, achievement_id) DO NOTHING RETURNING achievement_id",
        guild_id, user_id, achievement_id, timestamp)
    return row is not None


async def db_revoke_achievement(guild_id: int, user_id: int, achievement_id: str):
    await get_pool().execute(
        "DELETE FROM achievements_earned WHERE guild_id=$1 AND user_id=$2 AND achievement_id=$3",
        guild_id, user_id, achievement_id)


async def db_get_achievement_channel(guild_id: int) -> int | None:
    row = await get_pool().fetchrow("SELECT channel_id FROM achievement_channels WHERE guild_id=$1", guild_id)
    return row["channel_id"] if row else None


async def db_set_achievement_channel(guild_id: int, channel_id: int):
    await get_pool().execute(
        "INSERT INTO achievement_channels VALUES($1,$2) ON CONFLICT (guild_id) "
        "DO UPDATE SET channel_id=excluded.channel_id", guild_id, channel_id)


async def db_get_all_achievement_users(guild_id: int) -> list[int]:
    rows = await get_pool().fetch("SELECT DISTINCT user_id FROM achievements_earned WHERE guild_id=$1", guild_id)
    return [r["user_id"] for r in rows]


async def db_get_achievement_completion(guild_id: int, achievement_id: str) -> tuple[int, int]:
    total_row = await get_pool().fetchrow(
        "SELECT COUNT(DISTINCT user_id) as c FROM achievements_earned WHERE guild_id=$1", guild_id)
    comp_row = await get_pool().fetchrow(
        "SELECT COUNT(*) as c FROM achievements_earned WHERE guild_id=$1 AND achievement_id=$2", guild_id, achievement_id)
    return (comp_row["c"] if comp_row else 0), (total_row["c"] if total_row else 0)


async def db_get_achievement_defs(guild_id: int) -> list[dict]:
    if guild_id in _ach_def_cache:
        return _ach_def_cache[guild_id]
    rows = await get_pool().fetch("SELECT * FROM achievement_defs WHERE guild_id=$1 ORDER BY achievement_id", guild_id)
    result = [_row_to_ach(r) for r in rows]
    _ach_def_cache[guild_id] = result
    return result


async def db_get_achievement_def(guild_id: int, achievement_id: str) -> dict | None:
    defs = await db_get_achievement_defs(guild_id)
    return next((d for d in defs if d["achievement_id"] == achievement_id), None)


async def db_add_achievement_def(guild_id: int, ach: dict):
    trigger = ach.get("trigger")
    role_rewards = ach.get("role_rewards")
    sort_order = ach.get("sort_order")
    if sort_order is None:
        sort_order = await get_pool().fetchval(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM achievement_defs WHERE guild_id=$1", guild_id)
    await get_pool().execute(
        "INSERT INTO achievement_defs(guild_id, achievement_id, name, description, icon, points, "
        "rarity, type, secret, trigger_data, created_at, xp_reward, role_rewards, sort_order) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)",
        guild_id, ach["achievement_id"], ach["name"], ach.get("description"), ach.get("icon", "🏆"),
        ach.get("points", 10), ach.get("rarity", "COMMON"), ach.get("type", "manual"),
        int(ach.get("secret", False)), json.dumps(trigger) if trigger else None,
        ach.get("created_at"), ach.get("xp_reward", 0),
        json.dumps(role_rewards) if role_rewards else None, sort_order)
    _ach_def_cache.pop(guild_id, None)


async def db_update_achievement_def(guild_id: int, achievement_id: str, **kwargs):
    col_map = {"name": "name", "description": "description", "icon": "icon", "points": "points",
               "rarity": "rarity", "type": "type", "secret": "secret", "xp_reward": "xp_reward",
               "trigger": "trigger_data", "role_rewards": "role_rewards", "sort_order": "sort_order"}
    sets, vals = [], []
    for k, v in kwargs.items():
        col = col_map.get(k)
        if col:
            if k == "secret":
                v = int(v)
            if k in ("trigger", "role_rewards"):
                v = json.dumps(v) if v else None
            vals.append(v)
            sets.append(f"{col}=${len(vals)}")
    if not sets:
        return
    vals += [guild_id, achievement_id]
    await get_pool().execute(
        f"UPDATE achievement_defs SET {', '.join(sets)} WHERE guild_id=${len(vals) - 1} AND achievement_id=${len(vals)}", *vals)
    _ach_def_cache.pop(guild_id, None)


async def db_delete_achievement_def(guild_id: int, achievement_id: str):
    await get_pool().execute("DELETE FROM achievement_defs WHERE guild_id=$1 AND achievement_id=$2", guild_id, achievement_id)
    _ach_def_cache.pop(guild_id, None)


async def db_get_all_progress(guild_id: int, user_id: int) -> dict:
    key = (guild_id, user_id)
    if key in _prog_cache:
        return _prog_cache[key]
    rows = await get_pool().fetch(
        "SELECT metric, value FROM achievement_progress WHERE guild_id=$1 AND user_id=$2", guild_id, user_id)
    d = {"messages_sent": 0.0, "reactions_added": 0.0, "voice_minutes": 0.0}
    for r in rows:
        d[r["metric"]] = r["value"]
    _prog_cache[key] = d
    return d


async def db_update_progress(guild_id: int, user_id: int, metric: str, delta: float):
    prog = await db_get_all_progress(guild_id, user_id)
    new_val = prog.get(metric, 0.0) + delta
    prog[metric] = new_val
    await get_pool().execute(
        "INSERT INTO achievement_progress(guild_id, user_id, metric, value) VALUES($1,$2,$3,$4)"
        " ON CONFLICT (guild_id, user_id, metric) DO UPDATE SET value = excluded.value",
        guild_id, user_id, metric, new_val)


async def db_get_progress(guild_id: int, user_id: int, metric: str) -> float:
    return (await db_get_all_progress(guild_id, user_id)).get(metric, 0.0)


async def db_set_progress(guild_id: int, user_id: int, metric: str, value: float):
    prog = await db_get_all_progress(guild_id, user_id)
    prog[metric] = value
    await get_pool().execute(
        "INSERT INTO achievement_progress(guild_id, user_id, metric, value) VALUES($1,$2,$3,$4)"
        " ON CONFLICT (guild_id, user_id, metric) DO UPDATE SET value = excluded.value",
        guild_id, user_id, metric, value)


async def db_delete_progress_metric(guild_id: int, user_id: int, metric: str):
    _prog_cache.pop((guild_id, user_id), None)
    await get_pool().execute(
        "DELETE FROM achievement_progress WHERE guild_id=$1 AND user_id=$2 AND metric=$3", guild_id, user_id, metric)


def db_invalidate_progress_cache(guild_id: int, user_id: int):
    """Drops a user's cached progress row so the next `db_get_all_progress`
    call re-reads from Postgres. Needed by anything that mutates progress
    rows through a path other than `db_update_progress`/`db_set_progress`
    (e.g. achievement-data swaps, which bulk-write many metrics at once)."""
    _prog_cache.pop((guild_id, user_id), None)


def db_clear_achievement_guild_cache(guild_id: int):
    """Called from `achievements.py`'s `on_guild_remove` to drop this
    guild's cached achievement defs and every user's cached progress row
    for it, so a rejoin starts from a clean read."""
    _ach_def_cache.pop(guild_id, None)
    for key in [k for k in list(_prog_cache.keys()) if k[0] == guild_id]:
        _prog_cache.pop(key, None)


def db_clear_levelling_guild_cache(guild_id: int):
    """Called from `levelling.py`'s `on_guild_remove` to drop this guild's
    cached levelling config and every user's cached level row for it."""
    _lvl_cfg_cache.pop(guild_id, None)
    for key in [k for k in list(_lvl_user_cache.keys()) if k[0] == guild_id]:
        _lvl_user_cache.pop(key, None)


async def db_get_all_achievements_for_user(user_id: int) -> list[dict]:
    rows = await get_pool().fetch("SELECT * FROM achievements_earned WHERE user_id=$1", user_id)
    return [dict(r) for r in rows]


async def db_get_all_progress_for_user(user_id: int) -> list[dict]:
    rows = await get_pool().fetch("SELECT * FROM achievement_progress WHERE user_id=$1", user_id)
    return [dict(r) for r in rows]
# endregion


# region Proxies
async def db_get_proxies(user_id: int) -> dict[str, dict]:
    rows = await get_pool().fetch("SELECT * FROM proxies WHERE user_id=$1", user_id)
    result = {}
    for r in rows:
        d = dict(r)
        key = d.pop("proxy_key")
        d.pop("user_id")
        if d.get("nicks"):
            d["nicks"] = {int(k): v for k, v in json.loads(d["nicks"]).items()}
        else:
            d.pop("nicks", None)
        for f in ("tracked_user_name", "tracked_user_avatar"):
            if not d[f]:
                d.pop(f)
        if not d.get("avatar_url"):
            d.pop("avatar_url", None)
        if not d.get("name"):
            d.pop("name", None)
        if not d.get("description"):
            d.pop("description", None)
        result[key] = d
    return result


async def db_get_proxy(user_id: int, key: str) -> dict[str, dict] | None:
    row = await get_pool().fetchrow("SELECT * FROM proxies WHERE user_id=$1 AND proxy_key=$2", user_id, key)
    if not row:
        return None
    d = dict(row)
    d.pop("proxy_key")
    d.pop("user_id")
    if d.get("nicks"):
        d["nicks"] = {int(k): v for k, v in json.loads(d["nicks"]).items()}
    else:
        d.pop("nicks", None)
    for f in ("tracked_user_name", "tracked_user_avatar"):
        if not d[f]:
            d.pop(f)
    if not d.get("avatar_url"):
        d.pop("avatar_url", None)
    if not d.get("name"):
        d.pop("name", None)
    if not d.get("description"):
        d.pop("description", None)
    return d


async def db_save_proxy(user_id: int, key: str, proxy: dict):
    nicks = json.dumps({str(k): v for k, v in proxy.get("nicks", {}).items()}) if proxy.get("nicks") else None
    await get_pool().execute(
        "INSERT INTO proxies(user_id, proxy_key, invocation, name, tracked_user_name, "
        "tracked_user_avatar, avatar_url, nicks, description) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) "
        "ON CONFLICT (user_id, proxy_key) DO UPDATE SET invocation=excluded.invocation, name=excluded.name, "
        "tracked_user_name=excluded.tracked_user_name, tracked_user_avatar=excluded.tracked_user_avatar, "
        "avatar_url=excluded.avatar_url, nicks=excluded.nicks, description=excluded.description",
        user_id, key, proxy["invocation"], proxy.get("name"), proxy.get("tracked_user_name"),
        proxy.get("tracked_user_avatar"), proxy.get("avatar_url"), nicks, proxy.get("description"))


async def db_delete_proxy(user_id: int, key: str) -> bool:
    status = await get_pool().execute("DELETE FROM proxies WHERE user_id=$1 AND proxy_key=$2", user_id, key)
    return _affected(status) > 0


async def db_rename_proxy(user_id: int, old_key: str, new_key: str):
    await get_pool().execute("UPDATE proxies SET proxy_key=$1 WHERE user_id=$2 AND proxy_key=$3", new_key, user_id, old_key)


async def db_get_autoproxy(user_id: int) -> dict | None:
    row = await get_pool().fetchrow("SELECT * FROM proxy_autoproxy WHERE user_id=$1", user_id)
    return dict(row) if row else None


async def db_set_autoproxy(user_id: int, enabled: bool, timeout_secs: int | None):
    await get_pool().execute(
        "INSERT INTO proxy_autoproxy(user_id, enabled, timeout_secs) VALUES($1,$2,$3) "
        "ON CONFLICT (user_id) DO UPDATE SET enabled=excluded.enabled, timeout_secs=excluded.timeout_secs",
        user_id, int(enabled), timeout_secs)


async def db_update_last_proxy_use(user_id: int, proxy_key: str):
    await get_pool().execute(
        "INSERT INTO proxy_autoproxy(user_id, enabled, last_proxy_key, last_used_at) VALUES($1,0,$2,$3) "
        "ON CONFLICT (user_id) DO UPDATE SET last_proxy_key=excluded.last_proxy_key, last_used_at=excluded.last_used_at",
        user_id, proxy_key, datetime.now().isoformat())
# endregion


# region Text filters (uwu)
async def db_get_guild_uwu(guild_id: int) -> dict[int, int]:
    rows = await get_pool().fetch("SELECT user_id, type FROM uwu WHERE guild_id=$1", guild_id)
    return {r["user_id"]: r["type"] for r in rows}


async def db_get_uwu_type(guild_id: int, user_id: int) -> int | None:
    row = await get_pool().fetchrow("SELECT type FROM uwu WHERE guild_id=$1 AND user_id=$2", guild_id, user_id)
    return row["type"] if row else None


async def db_set_uwu(guild_id: int, user_id: int, uwu_type: int):
    await get_pool().execute(
        "INSERT INTO uwu VALUES($1,$2,$3) ON CONFLICT (guild_id, user_id) DO UPDATE SET type=excluded.type",
        guild_id, user_id, uwu_type)


async def db_clear_uwu(guild_id: int, user_id: int):
    await get_pool().execute("DELETE FROM uwu WHERE guild_id=$1 AND user_id=$2", guild_id, user_id)


async def db_get_uwu_settings_by_user(user_id: int) -> list[dict]:
    rows = await get_pool().fetch("SELECT guild_id, type FROM uwu WHERE user_id=$1", user_id)
    return [dict(r) for r in rows]
# endregion


# region Submissions
def _row_to_sub(row) -> dict:
    d = dict(row)
    try:
        d["question"] = json.loads(d["question"])
    except Exception:
        pass
    return d


async def db_get_submission(sub_id: str) -> dict | None:
    row = await get_pool().fetchrow("SELECT * FROM submissions WHERE sub_id=$1", sub_id)
    return _row_to_sub(row) if row else None


async def db_get_submissions_by_user(user_id: int) -> list[dict]:
    rows = await get_pool().fetch("SELECT * FROM submissions WHERE submitter_id=$1", user_id)
    return [_row_to_sub(r) for r in rows]


async def db_get_all_submissions() -> dict[str, dict]:
    rows = await get_pool().fetch("SELECT * FROM submissions")
    return {r["sub_id"]: _row_to_sub(r) for r in rows}


async def db_save_submission(sub: dict):
    q = sub["question"]
    await get_pool().execute(
        "INSERT INTO submissions"
        "(sub_id, type, question, submitter_id, submitter_name, message_id, guild_id, origin_guild_id, status)"
        " VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        sub["sub_id"], sub["type"], json.dumps(q) if isinstance(q, (list, tuple)) else q,
        sub["submitter_id"], sub["submitter_name"], sub.get("message_id"), sub.get("guild_id"),
        sub.get("origin_guild_id"), sub["status"])


async def db_update_submission(sub_id: str, **kwargs):
    sets, vals = [], []
    for k, v in kwargs.items():
        if k == "question" and isinstance(v, (list, tuple)):
            v = json.dumps(v)
        vals.append(v)
        sets.append(f"{k}=${len(vals)}")
    vals.append(sub_id)
    await get_pool().execute(f"UPDATE submissions SET {', '.join(sets)} WHERE sub_id=${len(vals)}", *vals)


async def db_delete_submission(sub_id: str):
    status = await get_pool().execute("DELETE FROM submissions WHERE sub_id=$1", sub_id)
    return _affected(status) > 0
# endregion


# region Levelling
_LVL_XP_CFG_DEFAULT = {
    "level_formula": "level * 100 + 75",
    "message": {"enabled": True, "min_xp": 15, "max_xp": 40, "cooldown": 60, "min_length": 0,
                "multipliers": [], "blacklisted_channels": [], "whitelisted_channels": [], "blacklisted_roles": [],
                "no_xp_prefixes": [], "effort_xp": {"enabled": True, "bonus_percent": 10, "min_words": 25}},
    "voice": {"enabled": True, "xp_per_minute": 35.0, "require_others": True, "muted_multiplier": 0.5,
              "deafened_multiplier": 0.0, "afk_no_xp": True, "blacklisted_channels": []},
    "reaction": {"enabled": False, "xp_per_reaction": 1.0, "cooldown": 30},
    "allow_bots": False,
    "allow_webhooks": False,
}
_LVL_LEVELUP_DEFAULT = {"enabled": True, "message": "${user_mention}, you just reached level **${level}**! 🎉\n${earned:You earned the ${mention} role!}", "channel": None, "dm": False, "image": True}
_LVL_ROLES_DEFAULT = {"first_place": None, "rewards": []}


async def db_ensure_levelling(guild_id: int):
    if guild_id in _lvl_cfg_cache:
        return
    row = await get_pool().fetchrow("SELECT * FROM levelling_config WHERE guild_id=$1", guild_id)
    if not row:
        from copy import deepcopy
        cfg = {"enabled": False, "xp_config": deepcopy(_LVL_XP_CFG_DEFAULT),
               "levelup": deepcopy(_LVL_LEVELUP_DEFAULT), "roles": deepcopy(_LVL_ROLES_DEFAULT)}
        await get_pool().execute(
            "INSERT INTO levelling_config VALUES($1,$2,$3,$4,$5)",
            guild_id, 0, json.dumps(cfg["xp_config"]), json.dumps(cfg["levelup"]), json.dumps(cfg["roles"]))
        _lvl_cfg_cache[guild_id] = cfg
    else:
        cfg = {"enabled": bool(row["enabled"]), "xp_config": json.loads(row["xp_config"]),
               "levelup": json.loads(row["levelup"]), "roles": json.loads(row["roles"])}
        cfg["xp_config"].setdefault("level_formula", _LVL_XP_CFG_DEFAULT["level_formula"])
        cfg["xp_config"].setdefault("allow_webhooks", False)
        mc = cfg["xp_config"].setdefault("message", {})
        mc.setdefault("min_xp", max(1, mc.get("base_xp", 30) // 2))
        mc.setdefault("max_xp", mc.get("base_xp", 30))
        mc.setdefault("effort_xp", {"enabled": True, "bonus_percent": 10, "min_words": 25})
        mc.setdefault("no_xp_prefixes", [])
        _lvl_cfg_cache[guild_id] = cfg


async def db_get_levelling_config(guild_id: int) -> dict:
    await db_ensure_levelling(guild_id)
    return _lvl_cfg_cache[guild_id]


async def db_save_levelling_config(guild_id: int, cfg: dict):
    _lvl_cfg_cache[guild_id] = cfg
    await get_pool().execute(
        "INSERT INTO levelling_config VALUES($1,$2,$3,$4,$5) ON CONFLICT (guild_id) DO UPDATE SET "
        "enabled=excluded.enabled, xp_config=excluded.xp_config, levelup=excluded.levelup, roles=excluded.roles",
        guild_id, int(cfg["enabled"]), json.dumps(cfg["xp_config"]), json.dumps(cfg["levelup"]), json.dumps(cfg["roles"]))


async def db_get_level_user(guild_id: int, user_id: int) -> dict:
    key = (guild_id, user_id)
    if key in _lvl_user_cache:
        return _lvl_user_cache[key]
    row = await get_pool().fetchrow("SELECT * FROM levelling_users WHERE guild_id=$1 AND user_id=$2", guild_id, user_id)
    d = dict(row) if row else {"guild_id": guild_id, "user_id": user_id, "xp": 0.0, "level": 0, "total_xp": 0.0, "last_message_ts": 0.0}
    _lvl_user_cache[key] = d
    return d


async def db_ensure_level_user(guild_id: int, user_id: int):
    await db_ensure_levelling(guild_id)
    key = (guild_id, user_id)
    if key not in _lvl_user_cache:
        await db_get_level_user(guild_id, user_id)
    await get_pool().execute("INSERT INTO levelling_users(guild_id, user_id) VALUES($1,$2) ON CONFLICT DO NOTHING", guild_id, user_id)
    await get_pool().execute("INSERT INTO user_profiles(user_id) VALUES($1) ON CONFLICT DO NOTHING", user_id)


async def db_update_level_user(guild_id: int, user_id: int, xp: float, level: int, total_xp: float,
                                last_message_ts: float | None = None):
    key = (guild_id, user_id)
    cached = _lvl_user_cache.get(key, {})
    cached.update({"xp": xp, "level": level, "total_xp": total_xp})
    if last_message_ts is not None:
        cached["last_message_ts"] = last_message_ts
    _lvl_user_cache[key] = cached
    ts = last_message_ts if last_message_ts is not None else cached.get("last_message_ts", 0.0)
    await get_pool().execute(
        "INSERT INTO levelling_users(guild_id, user_id, xp, level, total_xp, last_message_ts) VALUES($1,$2,$3,$4,$5,$6)"
        " ON CONFLICT (guild_id, user_id) DO UPDATE SET xp = excluded.xp, level = excluded.level, "
        "total_xp = excluded.total_xp, last_message_ts = excluded.last_message_ts",
        guild_id, user_id, xp, level, total_xp, ts)


async def db_get_leaderboard(guild_id: int, limit: int | None = None) -> list[tuple[int, dict]]:
    q = "SELECT * FROM levelling_users WHERE guild_id=$1 ORDER BY total_xp DESC"
    params = [guild_id]
    if limit:
        q += " LIMIT $2"
        params.append(limit)
    rows = await get_pool().fetch(q, *params)
    result = []
    for r in rows:
        d = dict(r)
        _lvl_user_cache[(guild_id, d["user_id"])] = d
        result.append((d["user_id"], d))
    return result


async def db_reset_level_user(guild_id: int, user_id: int):
    _lvl_user_cache.pop((guild_id, user_id), None)
    await get_pool().execute("DELETE FROM levelling_users WHERE guild_id=$1 AND user_id=$2", guild_id, user_id)


async def db_reset_all_level_users(guild_id: int):
    for key in list(_lvl_user_cache):
        if key[0] == guild_id:
            _lvl_user_cache.pop(key)
    status = await get_pool().execute("DELETE FROM levelling_users WHERE guild_id=$1", guild_id)
    return _affected(status)


async def db_rank_in_guild(guild, user_id: int) -> int | None:
    """Takes the resolved `Guild` object rather than fetching it itself --
    the monolith reached into a global `Client` from inside this db
    function, which meant this module (data access) transitively depended
    on the bot's own gateway connection. The caller already has the guild
    from the interaction context, so it's simplest to just pass it in."""
    rows = await get_pool().fetch("SELECT user_id FROM levelling_users WHERE guild_id=$1 ORDER BY total_xp DESC", guild.id)
    rank = 0
    for row in rows:
        if guild.get_member(row["user_id"]) is None:
            continue
        rank += 1
        if row["user_id"] == user_id:
            return rank
    return None


async def db_get_user_profile(user_id: int) -> dict:
    row = await get_pool().fetchrow("SELECT * FROM user_profiles WHERE user_id=$1", user_id)
    return {"custom_bg": row["custom_bg"], "custom_color": row["custom_color"]} if row else {"custom_bg": None, "custom_color": None}


async def db_save_user_profile(user_id: int, custom_bg: str | None, custom_color: str | None):
    await get_pool().execute(
        "INSERT INTO user_profiles VALUES($1,$2,$3) ON CONFLICT (user_id) "
        "DO UPDATE SET custom_bg=excluded.custom_bg, custom_color=excluded.custom_color",
        user_id, custom_bg, custom_color)


async def db_get_all_levelling_for_user(user_id: int) -> list[dict]:
    rows = await get_pool().fetch("SELECT * FROM levelling_users WHERE user_id=$1", user_id)
    return [dict(r) for r in rows]


async def db_get_top_level_user(guild_id: int) -> tuple[int, dict] | None:
    row = await get_pool().fetchrow("SELECT * FROM levelling_users WHERE guild_id=$1 ORDER BY total_xp DESC LIMIT 1", guild_id)
    return (row["user_id"], dict(row)) if row else None
# endregion


# region Haiku / Fake Nitro
async def db_get_haiku_enabled(guild_id: int) -> bool:
    row = await get_pool().fetchrow("SELECT enabled FROM haiku_config WHERE guild_id=$1", guild_id)
    return bool(row["enabled"]) if row else False


async def db_set_haiku_enabled(guild_id: int, enabled: bool):
    await get_pool().execute(
        "INSERT INTO haiku_config VALUES($1,$2) ON CONFLICT (guild_id) DO UPDATE SET enabled=excluded.enabled",
        guild_id, int(enabled))


async def db_get_fake_nitro_enabled(guild_id: int) -> bool:
    row = await get_pool().fetchrow("SELECT enabled FROM nitro_config WHERE guild_id=$1", guild_id)
    return bool(row["enabled"]) if row else False


async def db_set_fake_nitro_enabled(guild_id: int, enabled: bool):
    await get_pool().execute(
        "INSERT INTO nitro_config VALUES($1,$2) ON CONFLICT (guild_id) DO UPDATE SET enabled=excluded.enabled",
        guild_id, int(enabled))
# endregion


# region Tags
async def db_get_tag(guild_id: int, name: str) -> dict | None:
    row = await get_pool().fetchrow("SELECT * FROM tags WHERE guild_id=$1 AND name=$2", guild_id, name.lower())
    return dict(row) if row else None


async def db_get_tags_by_creator(user_id: int) -> list[dict]:
    rows = await get_pool().fetch("SELECT * FROM tags WHERE creator_id=$1", user_id)
    return [dict(r) for r in rows]


async def db_get_all_tags(guild_id: int) -> list[dict]:
    rows = await get_pool().fetch("SELECT * FROM tags WHERE guild_id=$1 ORDER BY name", guild_id)
    return [dict(r) for r in rows]


async def db_save_tag(guild_id: int, name: str, content: str, creator_id: int, image_url: str | None = None):
    await get_pool().execute(
        "INSERT INTO tags(guild_id, name, content, creator_id, created_at, uses, image_url) VALUES($1,$2,$3,$4,$5,0,$6)",
        guild_id, name.lower(), content, creator_id, datetime.now().isoformat(), image_url)


async def db_set_tag_trigger(guild_id: int, name: str, trigger: str | None) -> bool:
    if trigger:
        row = await get_pool().fetchrow(
            "SELECT name FROM tags WHERE guild_id=$1 AND trigger_phrase=$2 AND name!=$3",
            guild_id, trigger.lower(), name.lower())
        if row:
            return False
    await get_pool().execute(
        "UPDATE tags SET trigger_phrase=$1 WHERE guild_id=$2 AND name=$3",
        trigger.lower() if trigger else None, guild_id, name.lower())
    return True


async def db_get_tag_restrict(guild_id: int) -> bool:
    row = await get_pool().fetchrow("SELECT restrict_creation FROM tag_config WHERE guild_id=$1", guild_id)
    return bool(row["restrict_creation"]) if row else False


async def db_set_tag_restrict(guild_id: int, restrict: bool):
    await get_pool().execute(
        "INSERT INTO tag_config VALUES($1,$2) ON CONFLICT (guild_id) DO UPDATE SET restrict_creation=excluded.restrict_creation",
        guild_id, int(restrict))


async def db_delete_tag(guild_id: int, name: str) -> bool:
    status = await get_pool().execute("DELETE FROM tags WHERE guild_id=$1 AND name=$2", guild_id, name.lower())
    return _affected(status) > 0


async def db_bump_tag_uses(guild_id: int, name: str):
    await get_pool().execute("UPDATE tags SET uses=uses+1 WHERE guild_id=$1 AND name=$2", guild_id, name.lower())


async def db_update_tag_content(guild_id: int, name: str, content: str):
    await get_pool().execute("UPDATE tags SET content=$1 WHERE guild_id=$2 AND name=$3", content, guild_id, name.lower())


async def db_update_tag_embed(guild_id: int, name: str, embed_data: dict | None, content: str | None = None):
    if content is not None:
        await get_pool().execute(
            "UPDATE tags SET content=$1, embed_data=$2 WHERE guild_id=$3 AND name=$4",
            content, json.dumps(embed_data) if embed_data else None, guild_id, name.lower())
    else:
        await get_pool().execute(
            "UPDATE tags SET embed_data=$1 WHERE guild_id=$2 AND name=$3",
            json.dumps(embed_data) if embed_data else None, guild_id, name.lower())
# endregion


# region Polls
async def db_create_poll(message_id: int, channel_id: int, guild_id: int | None,
                          requester_id: int, question: str, options: list[str], anonymous: bool = False):
    await get_pool().execute(
        "INSERT INTO polls(message_id, channel_id, guild_id, requester_id, question, options, votes, ended, created_at, anonymous)"
        " VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        message_id, channel_id, guild_id, requester_id, question, json.dumps(options),
        json.dumps({}), 0, datetime.now().isoformat(), int(anonymous))


async def db_get_poll(message_id: int) -> dict | None:
    row = await get_pool().fetchrow("SELECT * FROM polls WHERE message_id=$1", message_id)
    if not row:
        return None
    d = dict(row)
    d["options"] = json.loads(d["options"])
    d["votes"] = {int(k): v for k, v in json.loads(d["votes"]).items()}
    d["ended"] = bool(d["ended"])
    return d


async def db_set_poll_vote(message_id: int, user_id: int, option_idx: int) -> dict | None:
    poll = await db_get_poll(message_id)
    if poll is None:
        return None
    poll["votes"][user_id] = option_idx
    await get_pool().execute(
        "UPDATE polls SET votes=$1 WHERE message_id=$2",
        json.dumps({str(k): v for k, v in poll["votes"].items()}), message_id)
    return poll


async def db_clear_poll_vote(message_id: int, user_id: int) -> dict | None:
    poll = await db_get_poll(message_id)
    if poll is None:
        return None
    del poll["votes"][user_id]
    await get_pool().execute(
        "UPDATE polls SET votes=$1 WHERE message_id=$2",
        json.dumps({str(k): v for k, v in poll["votes"].items()}), message_id)
    return poll


async def db_end_poll(message_id: int) -> dict | None:
    poll = await db_get_poll(message_id)
    if poll is None:
        return None
    await get_pool().execute("UPDATE polls SET ended=1 WHERE message_id=$1", message_id)
    poll["ended"] = True
    return poll
# endregion

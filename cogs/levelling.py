"""Levelling: message/voice/reaction XP, rank cards, leaderboard cards,
level-up announcements with role rewards, and a small sandboxed-formula
engine (`/xp config formula`) that lets each server define its own
XP-per-level curve as an arbitrary expression in `level` -- evaluated
exactly (via rational-arithmetic polynomial analysis + Faulhaber's
formula) wherever a closed form exists, and via checkpointed numpy-
vectorized summation otherwise, so `total_xp_for_level` stays fast even
for made-up formulas at extreme levels.

`award_xp` and `clear_voice_tracking` are public: `achievements.py` calls
the former when an achievement carries an XP reward (and this cog calls
back into `achievements.check_automated_achievements` on level-up, so the
import is lazy on both sides to dodge the cycle), and `admin_tools.py`
calls the latter before a bot restart so nobody's voice session is lost.
"""
from __future__ import annotations
import ast
import asyncio
import random
import re
import time
from datetime import datetime
from fractions import Fraction
from io import BytesIO
from math import ceil, floor, inf, isqrt, log, log2, log10, pi, sqrt
from typing import Any, Literal, Union

from discord import (
    AllowedMentions, Embed, File, Guild, Interaction, Member, Message,
    Object, RawReactionActionEvent, Role, Thread, User, app_commands,
)
from discord.abc import GuildChannel, Messageable
from discord.app_commands import Group, allowed_contexts, default_permissions, describe, guild_only
from discord.ext import commands, tasks
from PIL import Image, ImageDraw

import core.db as db
from core.discord_utils import ALL, ALL_INSTALLS, GUILD, GUILD_INSTALL
from core.imaging import (
    draw_text, fetch_image_bytes, get_font, gradient_ring, gradient_rounded_bar,
    gradient_rounded_border, lerp, lerp_color, lerp_color_wrap, parse_accent_colors,
)

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False

# Per-(guild, user) message/reaction XP cooldown timestamps (monotonic
# clock). In-memory by design -- worst case on a restart is one user gets
# one extra XP tick slightly early, not worth persisting for.
_msg_cd: dict = {}
_react_cd: dict = {}

# {guild_id: {user_id: join_datetime}} -- who's currently sitting in a
# voice channel and when they joined it, for the 5-minute XP tick and the
# on-leave settlement in `grant_voice_stuff`.
VOICE_TRACKING: dict[int, dict[int, datetime]] = {}

# Module-level Bot reference, set by `LevellingCog.__init__`. Needed
# because `award_xp` (and the level-up/first-place helpers it kicks off)
# keeps the exact `(gid, uid, xp_amount, channel=None, member=None,
# reason="message")` signature other cogs already call it with -- no
# `bot` parameter -- matching how the monolith reached the global client.
_bot: commands.Bot | None = None


# region rank / level-up / leaderboard card rendering
def _render_rank_card_sync(
    avatar_bytes: bytes | None, bg_bytes: bytes | None,
    display_name: str, level: int, rank: int | None,
    xp_in: float, xp_need: float, total_xp: float,
    accent_colors: list[tuple[int, int, int]]
) -> File:
    W, H = 900, 260
    img = Image.new("RGBA", (W, H))
    draw = ImageDraw.Draw(img)

    bg_loaded = False
    if bg_bytes:
        try:
            raw = Image.open(BytesIO(bg_bytes)).convert("RGBA")
            rr, tr = raw.width / raw.height, W / H
            if rr > tr:
                nw = int(tr * raw.height)
                cx = (raw.width - nw) // 2
                raw = raw.crop((cx, 0, cx + nw, raw.height))
            else:
                nh = int(raw.width / tr)
                cy = (raw.height - nh) // 2
                raw = raw.crop((0, cy, raw.width, cy + nh))
            img = raw.resize((W, H))
            img = Image.alpha_composite(img, Image.new("RGBA", (W, H), (17, 18, 20, 170)))
            draw = ImageDraw.Draw(img, "RGBA")
            bg_loaded = True
        except Exception:
            bg_loaded = False
    if not bg_loaded:
        draw.rectangle([0, 0, W, H], fill=(22, 23, 26, 255))

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle([20, 20, W - 20, H - 20], radius=16, fill=(30, 31, 34, 140))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img, "RGBA")
    gradient_rounded_border(img, 20, 20, W - 20, H - 20, accent_colors, radius=16, width=2, alpha=220)

    AV = 130
    ax, ay = 45, (H - AV) // 2
    if avatar_bytes:
        try:
            av = Image.open(BytesIO(avatar_bytes)).convert("RGBA").resize((AV, AV))
            mask = Image.new("L", (AV, AV), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, AV, AV], fill=255)
            av.putalpha(mask)
            img.paste(av, (ax, ay), av)
        except Exception:
            draw.ellipse([ax, ay, ax + AV, ay + AV], fill=(43, 45, 49, 255))
    else:
        draw.ellipse([ax, ay, ax + AV, ay + AV], fill=(43, 45, 49, 255))
    gradient_ring(img, ax + AV // 2, ay + AV // 2, AV // 2 + 5, 4, accent_colors)

    f_lg, f_md, f_sm = get_font("bold", 32), get_font("bold", 20), get_font("reg", 15)
    tx = ax + AV + 30
    name_str = display_name
    while draw.textlength(name_str, font=f_lg) > 450 and len(name_str) > 1:
        name_str = name_str[:-1]
    if name_str != display_name:
        name_str += "..."
    draw_text(img, (tx, 50), name_str, f_lg, (255, 255, 255), draw)

    medal_colors = {1: (255, 215, 0), 2: (192, 192, 192), 3: (205, 127, 50)}
    col = lerp_color_wrap(accent_colors, 0.5)
    stats = [
        (tx, "RANK", f"#{rank:,}" if rank is not None else "N/A", medal_colors.get(rank, (255, 255, 255))),
        (tx + 140, "LEVEL", f"{level:,}", (*col, 255)),
        (tx + 290, "TOTAL XP", f"{int(total_xp):,}", (230, 233, 239))
    ]
    for sx, label, val, c in stats:
        draw.text((sx, 112), label, fill=(148, 155, 164), font=f_sm)
        draw.text((sx, 127), val, fill=c, font=f_md)

    bx, by, bw, bh = tx, 172, W - tx - 45, 16
    pct = min(xp_in / xp_need, 1.0) if xp_need else 1.0
    draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8, fill=(43, 45, 49, 255))
    fw = int(bw * pct)
    if fw > 4:
        draw.rounded_rectangle([bx, by, bx + fw, by + bh], radius=8, fill=(*col, 255))
    draw.text((bx + 2, by + bh + 5), f"{int(xp_in):,} / {int(xp_need):,} XP  ·  {pct*100:.1f}%",
               fill=(148, 155, 164), font=f_sm)

    buf = BytesIO()
    img.convert("RGB").save(buf, "PNG")
    buf.seek(0)
    return File(buf, filename="rankcard.png")


async def _generate_rank_card(member: User, level: int, rank: int, xp_in: float, xp_need: float, total_xp: float):
    g_profile = await db.db_get_user_profile(member.id)
    accent_colors = parse_accent_colors(g_profile.get("custom_color"))
    bg_url = g_profile.get("custom_bg", "https://alfuwu.github.io/Images/contore-bg2.png")

    avatar_bytes, bg_bytes = await asyncio.gather(
        fetch_image_bytes(str(member.display_avatar.with_size(256).url), timeout=4),
        fetch_image_bytes(bg_url, timeout=5) if bg_url else asyncio.sleep(0, result=None)
    )

    return await asyncio.to_thread(
        _render_rank_card_sync, avatar_bytes, bg_bytes,
        member.display_name, level, rank, xp_in, xp_need, total_xp, accent_colors)


def _render_levelup_card_sync(
    avatar_bytes: bytes | None, bg_bytes: bytes | None,
    old_level: int, new_level: int, accent_colors: list[tuple[int, int, int]]
) -> File:
    W, H = 512, 128
    img = Image.new("RGBA", (W, H))
    draw = ImageDraw.Draw(img, "RGBA")

    bg_loaded = False
    if bg_bytes:
        try:
            raw = Image.open(BytesIO(bg_bytes)).convert("RGBA")
            rr, tr = raw.width / raw.height, W / H
            if rr > tr:
                nw = int(tr * raw.height)
                cx = (raw.width - nw) // 2
                raw = raw.crop((cx, 0, cx + nw, raw.height))
            else:
                nh = int(raw.width / tr)
                cy = (raw.height - nh) // 2
                raw = raw.crop((0, cy, raw.width, cy + nh))
            img = raw.resize((W, H))
            img = Image.alpha_composite(img, Image.new("RGBA", (W, H), (17, 18, 20, 170)))
            draw = ImageDraw.Draw(img, "RGBA")
            bg_loaded = True
        except Exception:
            bg_loaded = False
    if not bg_loaded:
        draw.rectangle([0, 0, W, H], fill=(22, 23, 26, 255))

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle([20, 20, W - 20, H - 20], radius=16, fill=(30, 31, 34, 140))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img, "RGBA")
    gradient_rounded_border(img, 15, 15, W - 15, H - 15, accent_colors, radius=16, width=2, alpha=220)

    AV = 96
    ax, ay = 24, (H - AV) // 2
    if avatar_bytes:
        try:
            av = Image.open(BytesIO(avatar_bytes)).convert("RGBA").resize((AV, AV))
            mask = Image.new("L", (AV, AV), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, AV, AV], fill=255)
            av.putalpha(mask)
            img.paste(av, (ax, ay), av)
        except Exception:
            draw.ellipse([ax, ay, ax + AV, ay + AV], fill=(43, 45, 49, 255))
    else:
        draw.ellipse([ax, ay, ax + AV, ay + AV], fill=(43, 45, 49, 255))
    gradient_ring(img, ax + AV // 2, ay + AV // 2, AV // 2 + 4, 3, accent_colors)

    f_head, f_sub = get_font("bold", 26), get_font("reg", 18)
    tx = ax + AV + 18
    draw_text(img, (tx, 25), "LEVEL UP!" if new_level > old_level else "LEVEL DOWN!",
              f_head, (*lerp_color(accent_colors, 0.3), 255), draw)
    draw_text(img, (tx, 62), f"{old_level:,}  ·  {new_level:,}", f_sub, (200, 200, 200), draw)
    gradient_rounded_bar(img, tx, 92, W - 25, 98, accent_colors, radius=3)

    buf = BytesIO()
    img.convert("RGB").save(buf, "PNG")
    buf.seek(0)
    return File(buf, filename="levelup.png")


async def _generate_levelup_card(member: User, old_level: int, new_level: int):
    g_profile = await db.db_get_user_profile(member.id)
    accent_colors = parse_accent_colors(g_profile.get("custom_color"))
    bg_url = g_profile.get("custom_bg", "https://alfuwu.github.io/Images/contore-bg2.png")

    avatar_bytes, bg_bytes = await asyncio.gather(
        fetch_image_bytes(str(member.display_avatar.with_size(128).url), timeout=5),
        fetch_image_bytes(bg_url, timeout=5) if bg_url else asyncio.sleep(0, result=None)
    )

    return await asyncio.to_thread(
        _render_levelup_card_sync, avatar_bytes, bg_bytes, old_level, new_level, accent_colors)


async def _generate_leaderboard_card(guild: Guild, ranked_entries: list[tuple[int, dict]], formula: str):
    avatar_urls = []
    for uid, _ in ranked_entries:
        member = guild.get_member(uid)
        avatar_urls.append(str(member.display_avatar.with_size(64).url) if member else None)

    avatar_bytes_list = await asyncio.gather(
        *(fetch_image_bytes(u, timeout=3) if u else asyncio.sleep(0, result=None) for u in avatar_urls)
    )

    names = [(guild.get_member(uid).display_name if guild.get_member(uid) else f"User {uid}") for uid, _ in ranked_entries]

    return await asyncio.to_thread(
        _render_leaderboard_card_sync, guild.name, ranked_entries, names, avatar_bytes_list, formula)


def _render_leaderboard_card_sync(
    guild_name: str, ranked_entries: list[tuple[int, dict]], names: list[str],
    avatar_bytes_list: list[bytes | None], formula: str
) -> File:
    row_h, header_h = 72, 107
    W = 820
    H = header_h + len(ranked_entries) * row_h + 20
    img = Image.new("RGBA", (W, H), (17, 18, 20, 255))
    draw = ImageDraw.Draw(img)
    f_head, f_sub, f_row = get_font("bold", 35), get_font("reg", 20), get_font("bold", 25)

    draw_text(img, (35, 25), guild_name.upper(), f_head, (255, 255, 255), draw)
    draw.text((37, 69), "Server Activity Leaderboard", fill=(148, 155, 164), font=f_sub)
    draw.line([(35, 95), (W - 35, 95)], fill=(43, 45, 49, 255), width=2)

    medal_colors = {1: (255, 215, 0), 2: (192, 192, 192), 3: (205, 127, 50)}

    for idx, ((uid, udata), name, av_bytes) in enumerate(zip(ranked_entries, names, avatar_bytes_list)):
        rank = idx + 1
        y = header_h + idx * row_h
        row_fill = (35, 36, 39, 255) if rank % 2 == 0 else (43, 45, 49, 120)
        draw.rounded_rectangle([25, y, W - 25, y + row_h - 8], radius=8, fill=row_fill)

        pc = medal_colors.get(rank, (148, 155, 164))
        rs = f"#{rank}"
        rw = draw.textlength(rs, font=f_row)
        draw.text((62 - rw // 2, y + 18), rs, fill=pc, font=f_row)

        av_x, av_y, av_sz = 100, y + 10, 44
        if av_bytes:
            try:
                av_img = Image.open(BytesIO(av_bytes)).convert("RGBA").resize((av_sz, av_sz))
                av_mask = Image.new("L", (av_sz, av_sz), 0)
                ImageDraw.Draw(av_mask).ellipse([0, 0, av_sz, av_sz], fill=255)
                av_img.putalpha(av_mask)
                img.paste(av_img, (av_x, av_y), av_img)
            except Exception:
                draw.ellipse([av_x, av_y, av_x + av_sz, av_y + av_sz], fill=(88, 101, 242, 255))
        else:
            draw.ellipse([av_x, av_y, av_x + av_sz, av_y + av_sz], fill=(88, 101, 242, 255))

        disp = name
        while draw.textlength(disp, font=f_row) > 450 and len(disp) > 1:
            disp = disp[:-1]
        if disp != name:
            disp += "..."
        draw_text(img, (160, y + 18), disp, f_row, (255, 255, 255), draw)

        lv = udata.get("level", 0)
        lv_str = f"LV. {lv:,}"
        lw = draw.textlength(lv_str, font=f_row)
        draw.text((W - 55 - lw, y + 18), lv_str, fill=(255, 215, 0), font=f_row)

        xp = udata.get("xp", 0)
        req_xp = xp_for_level(formula, lv)
        pct = xp / req_xp if req_xp else 0
        draw.line([(160, y + 55), (W - 55, y + 55)], fill=(128, 128, 128), width=4)
        fill_end = int(lerp(160, W - 55, pct))
        if fill_end > 160:
            draw.line([(160, y + 55), (fill_end, y + 55)], fill=(216, 180, 30), width=4)

    buf = BytesIO()
    img.convert("RGB").save(buf, "PNG")
    buf.seek(0)
    return File(buf, filename="leaderboard.png")
# endregion

# region level-formula evaluation (public wrappers; engine is below)
def total_xp_for_arcane_level(level: int) -> int:
    """Arcane bot used this exact XP curve; `/level admin arcanesync`
    needs it to convert an imported Arcane level into a total-XP figure
    Contore's own (possibly custom) formula can then re-derive a level
    from. (The monolith also carried `arcane_xp_for_level`,
    `xp_for_level_old`, `total_xp_for_level_old`, `level_from_xp_old`, and
    `_xp_at_level_boundary_old` alongside this -- all dead code with zero
    remaining call sites, so they were dropped rather than ported.)"""
    if level <= 0:
        return 0
    return level * (50 * level + 25)


_level_boundary_cache: dict[str, list[float]] = {}


def xp_for_level(formula: str, level: int) -> float:
    return _eval_level_formula(formula, max(0, level))


def total_xp_for_level(formula: str, level: int) -> float:
    if level <= 0:
        return 0.0
    poly = _formula_polynomial(formula)
    if poly is not None:
        return float(_exact_polynomial_total_xp(poly, level))
    return _exact_nonpolynomial_total_xp(formula, level)


def level_from_xp(formula: str, total_xp: float) -> tuple[int, float, float]:
    total_xp = max(0.0, total_xp)
    poly = _formula_polynomial(formula)
    level = _polynomial_level_from_xp(poly, total_xp) if poly is not None \
        else _nonpolynomial_level_from_xp(formula, total_xp)
    cur_boundary = total_xp_for_level(formula, level)
    remaining = total_xp - cur_boundary
    needed = xp_for_level(formula, level)
    return level, remaining, needed


def _formula_computation_mode(formula: str) -> str:
    poly = _formula_polynomial(formula)
    if poly is not None:
        return "exact closed-form (instant at any level, including extreme ones)"
    if _nonpoly_formula_has_branching(formula) or not _NUMPY_AVAILABLE:
        return "exact term-by-term (no closed form exists; fine up to a few million levels)"
    return "exact vectorized (no closed form exists; fine up to several million levels)"
# endregion


# region the sandboxed-formula engine itself
# Pure math -- no bot/db/discord dependencies -- so this is a verbatim
# port. Parses a formula into an AST, validates it against an allowlist of
# safe operators/functions, and then, where the formula turns out to be a
# genuine polynomial in `level` (degree <= 12), computes exact total-XP
# sums via Faulhaber's formula in exact rational (Fraction) arithmetic --
# so `total_xp_for_level`/`level_from_xp` are O(log level) instead of
# O(level) even for formulas with no simple closed form of their own.
# Formulas with branching (ternaries) or that aren't polynomials fall back
# to checkpointed numpy-vectorized (or, without numpy, plain looped)
# summation.
class FormulaError(Exception):
    pass

_FORMULA_ALLOWED_FUNCS = {
    "abs": abs, "min": min, "max": max, "round": round, "pow": pow,
    "sqrt": sqrt, "log": log, "log2": log2, "log10": log10,
    "floor": floor, "ceil": ceil
}
_FORMULA_ALLOWED_CONSTS = {"pi": pi, "e": 2.718281828459045}

def _validate_formula_ast(node):
    if isinstance(node, ast.Expression):
        _validate_formula_ast(node.body)
    elif isinstance(node, ast.BinOp):
        if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv)):
            raise FormulaError(f"Operator `{type(node.op).__name__}` isn't allowed.")
        _validate_formula_ast(node.left)
        _validate_formula_ast(node.right)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.UAdd, ast.USub)):
            raise FormulaError("Unary operator not allowed.")
        _validate_formula_ast(node.operand)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FORMULA_ALLOWED_FUNCS:
            raise FormulaError("Only these functions are allowed: " + ", ".join(_FORMULA_ALLOWED_FUNCS))
        for arg in node.args:
            _validate_formula_ast(arg)
        if node.keywords:
            raise FormulaError("Keyword arguments aren't allowed.")
    elif isinstance(node, ast.Name):
        if node.id != "level" and node.id not in _FORMULA_ALLOWED_FUNCS and node.id not in _FORMULA_ALLOWED_CONSTS:
            raise FormulaError(f"Unknown name `{node.id}`. Only `level` is available as a variable.")
    elif isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise FormulaError("Only numeric constants are allowed.")
    elif isinstance(node, ast.Compare):
        _validate_formula_ast(node.left)
        for op in node.ops:
            if not isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)):
                raise FormulaError("Comparison operator not allowed.")
        for c in node.comparators:
            _validate_formula_ast(c)
    elif isinstance(node, ast.IfExp):
        _validate_formula_ast(node.test)
        _validate_formula_ast(node.body)
        _validate_formula_ast(node.orelse)
    else:
        raise FormulaError(f"Expression type `{type(node).__name__}` isn't allowed.")

_compiled_formula_cache: dict[str, Any] = {}

def _get_compiled_formula(formula: str):
    code = _compiled_formula_cache.get(formula)
    if code is None:
        tree = ast.parse(formula, mode="eval")
        _validate_formula_ast(tree)
        code = compile(tree, "<level_formula>", "eval")
        _compiled_formula_cache[formula] = code
    return code

def _eval_level_formula(formula: str, level: int) -> float:
    code = _get_compiled_formula(formula)
    env = dict(_FORMULA_ALLOWED_FUNCS)
    env.update(_FORMULA_ALLOWED_CONSTS)
    env["level"] = level
    return eval(code, {"__builtins__": {}}, env)

def validate_level_formula(formula: str) -> tuple[bool, str]:
    formula = formula.strip()
    if not formula or len(formula) > 200:
        return False, "Formula must be non-empty and under 200 characters."
    try:
        tree = ast.parse(formula, mode="eval")
        _validate_formula_ast(tree)
    except FormulaError as e:
        return False, str(e)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"
    _compiled_formula_cache.pop(formula, None)
    _formula_poly_cache.pop(formula, None)
    _nonpoly_branch_cache.pop(formula, None)
    _nonpoly_checkpoint_cache.pop(formula, None)
    try:
        for test_level in (0, 1, 2, 5, 10, 25, 50, 100):
            val = _eval_level_formula(formula, test_level)
            if not isinstance(val, (int, float)) or val != val or val in (inf, -inf):
                return False, f"Formula produced an invalid value at level {test_level}."
            if val <= 0:
                return False, f"Formula must always return a positive number (got {val:g} at level {test_level})."
    except ZeroDivisionError:
        return False, "Formula divides by zero for a level in the 0-100 range."
    except Exception as e:
        return False, f"Formula failed to evaluate: {e}"

    poly = _formula_polynomial(formula)
    if poly is not None:
        for test_level in (0, 3, 17, 42):
            direct = sum(_eval_level_formula(formula, L) for L in range(test_level))
            exact = float(_exact_polynomial_total_xp(poly, test_level))
            if abs(direct - exact) > max(1e-6, abs(direct) * 1e-9):
                _formula_poly_cache[formula] = None
                break
    return True, ""

_MAX_POLY_DEGREE = 12  # generous headroom; real formulas use degree 1-3

def _poly_add(a: dict[int, Fraction], b: dict[int, Fraction]) -> dict[int, Fraction]:
    result = dict(a)
    for k, v in b.items():
        result[k] = result.get(k, Fraction(0)) + v
    result = {k: v for k, v in result.items() if v != 0}
    return result or {0: Fraction(0)}

def _poly_sub(a, b):
    return _poly_add(a, {k: -v for k, v in b.items()})

def _poly_mul(a: dict[int, Fraction], b: dict[int, Fraction]) -> dict[int, Fraction]:
    result: dict[int, Fraction] = {}
    for ka, va in a.items():
        for kb, vb in b.items():
            result[ka + kb] = result.get(ka + kb, Fraction(0)) + va * vb
    result = {k: v for k, v in result.items() if v != 0}
    return result or {0: Fraction(0)}

def _poly_is_constant(p: dict[int, Fraction]) -> bool:
    return all(k == 0 for k, v in p.items() if v != 0)

def _poly_const_value(p: dict[int, Fraction]) -> Fraction:
    return p.get(0, Fraction(0))

def _extract_polynomial(node) -> dict[int, Fraction] | None:
    if isinstance(node, ast.Constant):
        try:
            return {0: Fraction(node.value)}
        except (ValueError, OverflowError):
            return {0: Fraction(node.value).limit_denominator(10**9)}

    if isinstance(node, ast.Name):
        if node.id == "level":
            return {1: Fraction(1)}
        return None  # pi/e: irrational, can't hold exactly in a Fraction poly

    if isinstance(node, ast.UnaryOp):
        operand = _extract_polynomial(node.operand)
        if operand is None:
            return None
        return {k: -v for k, v in operand.items()} if isinstance(node.op, ast.USub) else operand

    if isinstance(node, ast.BinOp):
        if isinstance(node.op, (ast.Mod, ast.FloorDiv)):
            return None # inherently a step function of level; no closed form

        left = _extract_polynomial(node.left)
        right = _extract_polynomial(node.right)
        if left is None or right is None:
            return None

        if isinstance(node.op, ast.Add):
            return _poly_add(left, right)
        if isinstance(node.op, ast.Sub):
            return _poly_sub(left, right)
        if isinstance(node.op, ast.Mult):
            result = _poly_mul(left, right)
            return result if max(result, default=0) <= _MAX_POLY_DEGREE else None
        if isinstance(node.op, ast.Div):
            if not _poly_is_constant(right):
                return None
            denom = _poly_const_value(right)
            if denom == 0:
                return None
            return {k: v / denom for k, v in left.items()}
        if isinstance(node.op, ast.Pow):
            if not _poly_is_constant(right):
                return None
            exp_val = _poly_const_value(right)
            if exp_val.denominator != 1 or exp_val < 0 or exp_val > _MAX_POLY_DEGREE:
                return None
            result: dict[int, Fraction] = {0: Fraction(1)}
            for _ in range(int(exp_val)):
                result = _poly_mul(result, left)
            return result if max(result, default=0) <= _MAX_POLY_DEGREE else None
        return None

    if isinstance(node, ast.Call):
        fname = node.func.id
        if fname == "pow" and len(node.args) == 2:
            base = _extract_polynomial(node.args[0])
            exp = _extract_polynomial(node.args[1])
            if base is None or exp is None or not _poly_is_constant(exp):
                return None
            exp_val = _poly_const_value(exp)
            if exp_val.denominator != 1 or exp_val < 0 or exp_val > _MAX_POLY_DEGREE:
                return None
            result: dict[int, Fraction] = {0: Fraction(1)}
            for _ in range(int(exp_val)):
                result = _poly_mul(result, base)
            return result

        # abs/min/max/round/floor/ceil/sqrt/log*: only exactly representable
        # if every argument is level-independent, in which case the whole
        # call is just a constant.
        arg_polys = [_extract_polynomial(a) for a in node.args]
        if any(p is None or not _poly_is_constant(p) for p in arg_polys):
            return None
        try:
            args = [float(_poly_const_value(p)) for p in arg_polys]
            value = _FORMULA_ALLOWED_FUNCS[fname](*args)
            return {0: Fraction(value).limit_denominator(10**9)}
        except Exception:
            return None

    return None  # Compare / IfExp: inherently branchy, no closed form

_formula_poly_cache: dict[str, dict[int, Fraction] | None] = {}

def _formula_polynomial(formula: str) -> dict[int, Fraction] | None:
    if formula in _formula_poly_cache:
        return _formula_poly_cache[formula]
    try:
        tree = ast.parse(formula, mode="eval")
        poly = _extract_polynomial(tree.body)
    except Exception:
        poly = None
    _formula_poly_cache[formula] = poly
    return poly

_FAULHABER_CACHE: dict[int, list[Fraction]] = {} # k -> coeffs of S_k(n) = Σ_{L=0}^{n-1} L^k

def _lagrange_interpolate_exact(xs: list[int], ys: list[Fraction]) -> list[Fraction]:
    n = len(xs)
    result = [Fraction(0)] * n
    for i in range(n):
        basis = [Fraction(1)]
        denom = Fraction(1)
        for j in range(n):
            if j == i:
                continue
            new_basis = [Fraction(0)] * (len(basis) + 1)
            for p, c in enumerate(basis):
                new_basis[p] += c * (-xs[j])
                new_basis[p + 1] += c
            basis = new_basis
            denom *= (xs[i] - xs[j])
        scale = ys[i] / denom
        for p, c in enumerate(basis):
            result[p] += c * scale
    return result

def _get_faulhaber_poly(k: int) -> list[Fraction]:
    if k in _FAULHABER_CACHE:
        return _FAULHABER_CACHE[k]
    deg = k + 1
    xs = list(range(deg + 1))
    ys = [Fraction(0)]
    running = Fraction(0)
    for n in range(1, deg + 1):
        running += (Fraction(n - 1) ** k) if k > 0 else Fraction(1)
        ys.append(running)
    coeffs = _lagrange_interpolate_exact(xs, ys)
    _FAULHABER_CACHE[k] = coeffs
    return coeffs

def _eval_poly_fraction(coeffs: list[Fraction], n: int) -> Fraction:
    total = Fraction(0)
    npow = 1
    for c in coeffs:
        if c:
            total += c * npow
        npow *= n
    return total

def _exact_polynomial_total_xp(poly: dict[int, Fraction], n: int) -> Fraction:
    total = Fraction(0)
    for k, c in poly.items():
        if c == 0:
            continue
        total += c * _eval_poly_fraction(_get_faulhaber_poly(k), n)
    return total

def _polynomial_level_from_xp(poly: dict[int, Fraction], total_xp: float) -> int:
    if total_xp <= 0:
        return 0
    lo, hi = 0, 1
    while float(_exact_polynomial_total_xp(poly, hi)) <= total_xp:
        lo, hi = hi, hi * 2
        if hi > 10**18:
            break
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if float(_exact_polynomial_total_xp(poly, mid)) <= total_xp:
            lo = mid
        else:
            hi = mid - 1
    return lo

_nonpoly_branch_cache: dict[str, bool] = {}
_nonpoly_checkpoint_cache: dict[str, dict[int, float]] = {}
_NONPOLY_CHECKPOINT_INTERVAL = 10_000

def _nonpoly_formula_has_branching(formula: str) -> bool:
    if formula in _nonpoly_branch_cache:
        return _nonpoly_branch_cache[formula]
    tree = ast.parse(formula, mode="eval")
    result = any(isinstance(n, ast.IfExp) for n in ast.walk(tree))
    _nonpoly_branch_cache[formula] = result
    return result

if _NUMPY_AVAILABLE:
    def _np_min(*a):
        r = a[0]
        for x in a[1:]:
            r = np.minimum(r, x)
        return r
    def _np_max(*a):
        r = a[0]
        for x in a[1:]:
            r = np.maximum(r, x)
        return r
    _NUMPY_FORMULA_FUNCS = {
        "abs": np.abs, "min": _np_min, "max": _np_max, "round": np.round,
        "pow": np.power, "sqrt": np.sqrt, "log": np.log, "log2": np.log2,
        "log10": np.log10, "floor": np.floor, "ceil": np.ceil,
    }
    _NUMPY_FORMULA_CONSTS = {"pi": pi, "e": 2.718281828459045}

    def _eval_formula_vectorized(formula: str, levels):
        code = _get_compiled_formula(formula)
        env = dict(_NUMPY_FORMULA_FUNCS)
        env.update(_NUMPY_FORMULA_CONSTS)
        env["level"] = levels
        return eval(code, {"__builtins__": {}}, env)

def _exact_nonpolynomial_total_xp(formula: str, level: int) -> float:
    """True (non-approximated) discrete sum for formulas with no closed
    form. Computes every term, but via numpy vectorization where safe, plus
    checkpoint caching, instead of a plain interpreted loop."""
    checkpoints = _nonpoly_checkpoint_cache.setdefault(formula, {0: 0.0})
    start = max(l for l in checkpoints if l <= level)
    total = checkpoints[start]
    if start >= level:
        return checkpoints.get(level, total)

    branching = _nonpoly_formula_has_branching(formula)
    cur = start
    while cur < level:
        chunk_end = min(level, cur + _NONPOLY_CHECKPOINT_INTERVAL)
        if branching or not _NUMPY_AVAILABLE:
            for L in range(cur, chunk_end):
                total += _eval_level_formula(formula, L)
        else:
            arr = np.arange(cur, chunk_end, dtype=np.float64)
            total += float(_eval_formula_vectorized(formula, arr).sum())
        cur = chunk_end
        checkpoints[cur] = total
        if len(checkpoints) > 2000:
            for old_key in sorted(checkpoints)[1:-1000]:
                if old_key != 0:
                    checkpoints.pop(old_key, None)
    return total

def _nonpolynomial_level_from_xp(formula: str, total_xp: float) -> int:
    if total_xp <= 0:
        return 0
    lo, hi = 0, 1
    while _exact_nonpolynomial_total_xp(formula, hi) <= total_xp:
        lo, hi = hi, hi * 2
        if hi > 5_000_000: # practical ceiling
            break
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _exact_nonpolynomial_total_xp(formula, mid) <= total_xp:
            lo = mid
        else:
            hi = mid - 1
    return lo
# endregion

# region XP economy
async def award_xp(gid: int, uid: int, xp_amount: float, channel: Messageable = None, member: Member = None, reason: str = "message") -> bool:
    await db.db_ensure_level_user(gid, uid)
    cfg = await db.db_get_levelling_config(gid)
    if not cfg["enabled"]:
        return False

    ud = await db.db_get_level_user(gid, uid)
    old_level = ud["level"]

    new_total = ud.get("total_xp", 0.0) + xp_amount
    formula = cfg["xp_config"]["level_formula"]
    new_level, xp_in, _ = level_from_xp(formula, new_total)
    await db.db_update_level_user(gid, uid, xp_in, new_level, new_total)

    levelled_up = new_level != old_level
    if levelled_up:
        asyncio.create_task(_handle_level_up(gid, uid, new_level, old_level, channel, member))
        if new_level > old_level:
            asyncio.create_task(_update_first_place_role(gid))
            from cogs.achievements import check_automated_achievements
            asyncio.create_task(check_automated_achievements(_bot, gid, uid, channel))
    return levelled_up


async def _handle_level_up(gid: int, uid: int, new_level: int, old_level: int, channel: Messageable, member: Member):
    cfg = await db.db_get_levelling_config(gid)
    guild = _bot.get_guild(gid)
    roles_gained = []

    if guild and member:
        for reward in cfg["roles"]["rewards"]:
            role = guild.get_role(reward["role_id"])
            if not role:
                continue

            if old_level < reward["level"] <= new_level:
                try:
                    await member.add_roles(role)
                    roles_gained.append(role)
                except Exception:
                    pass

                exc = reward.get("exclusive_group")
                if exc:
                    for other in cfg["roles"]["rewards"]:
                        if other["role_id"] == reward["role_id"]:
                            continue

                        if other.get("exclusive_group") == exc and other["level"] < reward["level"]:
                            or2 = guild.get_role(other["role_id"])
                            if or2 and or2 in member.roles:
                                try:
                                    await member.remove_roles(or2)
                                except Exception:
                                    pass
            elif (not reward.get("keep", True) and reward["level"] != new_level
                  and not reward.get("exclusive_group")):
                if role in member.roles:
                    try:
                        await member.remove_roles(role)
                    except Exception:
                        pass

    if not cfg["levelup"]["enabled"]:
        return

    ud = await db.db_get_level_user(gid, uid)
    formula = cfg["xp_config"]["level_formula"]

    rg_mentions = " ".join(r.mention for r in roles_gained) if roles_gained else ""
    rg_names = ", ".join(r.name for r in roles_gained) if roles_gained else "none"

    text = cfg["levelup"]["message"]
    text = text.replace("${user_mention}", member.mention if member else f"<@{uid}>")
    text = text.replace("${user_display}", member.display_name if member else str(uid))
    text = text.replace("${level}", f"{new_level:,}")
    text = text.replace("${old_level}", f"{old_level:,}")
    text = text.replace("${xp}", f"{int(ud.get('xp', 0)):,}")
    text = text.replace("${total_xp}", f"{int(ud.get('total_xp', 0)):,}")
    text = text.replace("${needed_xp}", f"{xp_for_level(formula, new_level):,}")
    text = text.replace("${roles_gained}", rg_mentions)
    text = text.replace("${roles_gained_names}", rg_names)
    text = re.sub(
        r"\$\{earned:(.*)\}",
        lambda m: "\n".join(
            m.group(1).replace("${name}", role.name).replace("${mention}", role.mention)
            for role in roles_gained
        ) if roles_gained else "",
        text,
        flags=re.DOTALL
    )
    text = re.sub(r"(?<!\\)\\n", "\n", text)

    card = None
    if cfg["levelup"]["image"] and member:
        try:
            card = await _generate_levelup_card(member, old_level, new_level)
        except Exception as e:
            print(f"[Levelling] levelup card error: {e}")

    target = None
    if cfg["levelup"]["dm"] and member:
        try:
            target = await member.create_dm()
        except Exception:
            pass
    elif cfg["levelup"]["channel"] and guild:
        target = guild.get_channel_or_thread(cfg["levelup"]["channel"])
    else:
        target = channel

    if target:
        try:
            kwargs = {}
            if card:
                kwargs["file"] = card
            await target.send(text, allowed_mentions=AllowedMentions(users=[Object(uid)], roles=False), **kwargs)
        except Exception as e:
            print(f"[Levelling] level-up send error: {e}")


async def _update_first_place_role(gid: int):
    await db.db_ensure_levelling(gid)
    cfg = await db.db_get_levelling_config(gid)
    fp = cfg["roles"].get("first_place")

    if not fp:
        return
    guild = _bot.get_guild(gid)
    if not guild:
        return
    role = guild.get_role(fp)
    if not role:
        return
    top = await db.db_get_top_level_user(gid)
    if not top:
        return

    top_uid = top[0]
    for m in list(role.members):
        if m.id != top_uid:
            try:
                await m.remove_roles(role)
            except Exception:
                pass

    tm = guild.get_member(top_uid)
    if tm and role not in tm.roles:
        try:
            await tm.add_roles(role)
        except Exception:
            pass


async def process_message_xp(message: Message):
    if not message.guild:
        return

    gid, uid = message.guild.id, message.author.id
    await db.db_ensure_levelling(gid)
    cfg = await db.db_get_levelling_config(gid)
    if not cfg["enabled"]:
        return

    if message.author.bot and not cfg["xp_config"]["allow_bots"]:
        return

    if message.webhook_id is not None and not cfg["xp_config"].get("allow_webhooks", False):
        return

    mc = cfg["xp_config"]["message"]
    if not mc["enabled"]:
        return

    for pfx in mc["no_xp_prefixes"]:
        if message.content.startswith(pfx):
            return

    if len(message.content) < mc["min_length"]:
        return

    key = (gid, uid)
    now = time.monotonic()

    if now - _msg_cd.get(key, 0) < mc["cooldown"]:
        return

    _msg_cd[key] = now

    cid = message.channel.id
    parent_id = getattr(message.channel, "parent_id", None)
    eff_id = parent_id or cid

    if eff_id in mc["blacklisted_channels"] or cid in mc["blacklisted_channels"]:
        return

    if mc["whitelisted_channels"] and cid not in mc["whitelisted_channels"] and eff_id not in mc["whitelisted_channels"]:
        return

    member = message.author
    if isinstance(member, Member) and any(r.id in mc["blacklisted_roles"] for r in member.roles):
        return

    min_xp = mc.get("min_xp", 15)
    max_xp = mc.get("max_xp", 40)
    xp = random.uniform(float(min_xp), float(max_xp))

    effort = mc.get("effort_xp", {"enabled": True, "bonus_percent": 10, "min_words": 25})
    if effort.get("enabled", True):
        wc = len(message.content.split())
        if wc >= effort.get("min_words", 25):
            xp *= 1.0 + effort.get("bonus_percent", 10) / 100.0

    mult = 1.0
    for m2 in mc["multipliers"]:
        mt = m2.get("type")
        if mt == "global":
            mult *= m2["multiplier"]
        elif mt == "channel" and cid == m2.get("id"):
            mult *= m2["multiplier"]
        elif mt == "role" and isinstance(member, Member):
            if any(r.id == m2.get("id") for r in member.roles):
                mult *= m2["multiplier"]
        elif mt == "time":
            tnow = datetime.now().strftime("%H:%M")
            if m2.get("start", "00:00") <= tnow <= m2.get("end", "23:59"):
                mult *= m2["multiplier"]
    xp *= mult
    await award_xp(gid, uid, xp, message.channel, member)


async def process_reaction_xp(gid: int, uid: int, channel: Messageable = None, member: User = None):
    await db.db_ensure_levelling(gid)
    cfg = await db.db_get_levelling_config(gid)
    if not cfg["enabled"]:
        return

    rc = cfg["xp_config"]["reaction"]
    if not rc["enabled"]:
        return

    key = (gid, uid)
    now = time.monotonic()
    if now - _react_cd.get(key, 0) < rc["cooldown"]:
        return

    _react_cd[key] = now
    await award_xp(gid, uid, rc["xp_per_reaction"], channel, member, "reaction")


async def process_voice_xp(gid: int, uid: int, effective_minutes: float, member: User = None, channel: Messageable = None):
    await db.db_ensure_levelling(gid)
    cfg = await db.db_get_levelling_config(gid)
    if not cfg["enabled"]:
        return

    vc = cfg["xp_config"]["voice"]
    if not vc["enabled"] or effective_minutes <= 0:
        return

    xp = effective_minutes * vc["xp_per_minute"]
    if xp > 0:
        await award_xp(gid, uid, xp, channel, member, "voice")


async def grant_voice_stuff(bot: commands.Bot, channel, guild_id: int, user_id: int, member: User):
    from cogs.achievements import check_automated_achievements
    join_time = VOICE_TRACKING[guild_id].pop(user_id)
    duration_minutes = (datetime.now() - join_time).total_seconds() / 60.0

    await db.db_update_progress(guild_id, user_id, "voice_minutes", duration_minutes)
    asyncio.create_task(check_automated_achievements(bot, guild_id, user_id))

    await db.db_ensure_levelling(guild_id)
    vc = (await db.db_get_levelling_config(guild_id))["xp_config"]["voice"]
    if vc["enabled"]:
        mult = 1.0
        if channel.self_mute or channel.mute:
            mult *= vc.get("muted_multiplier", 0.5)
        if channel.self_deaf or channel.deaf:
            mult *= vc.get("deafened_multiplier", 0.0)

        if vc.get("afk_no_xp") and member.guild.afk_channel and channel.channel.id == member.guild.afk_channel.id:
            mult = 0.0
        if vc.get("require_others"):
            others = [m for m in channel.channel.members if not m.bot and m.id != user_id]
            if not others:
                mult = 0.0
        if channel.channel.id in vc.get("blacklisted_channels", []):
            mult = 0.0

        if mult > 0:
            await process_voice_xp(guild_id, user_id, duration_minutes * mult, member, channel.channel)


async def clear_voice_tracking(bot: commands.Bot):
    """Settles every currently-tracked voice session (awarding whatever
    partial voice XP/progress is owed) as if each member had just left.
    Called by `admin_tools.py` right before a bot restart so nobody's
    in-progress voice session silently loses its minutes."""
    for guild_id, users in list(VOICE_TRACKING.items()):
        guild = bot.get_guild(guild_id) or await bot.fetch_guild(guild_id)
        for user_id in list(users.keys()):
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
            if member and member.voice and member.voice.channel:
                await grant_voice_stuff(bot, member.voice, guild_id, user_id, member)


async def sync_arcane_leaderboard(gid: int, pages: int = 1):
    from aiohttp import ClientSession, ClientTimeout
    from config import ARCANE_MIGRATION_TOKEN

    await db.db_ensure_levelling(gid)
    cfg = await db.db_get_levelling_config(gid)
    formula = cfg["xp_config"]["level_formula"]

    synced = 0
    synced_uids = []
    errors = []

    if not ARCANE_MIGRATION_TOKEN:
        return synced, synced_uids, ["No ARCANE_MIGRATION_TOKEN configured -- set it in the environment to use this command."]

    url = f"https://arcane.bot/api/guilds/{gid}/levels/leaderboard"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:151.0) Gecko/20100101 Firefox/151.0",
        "Accept": "application/json, text/plain, */*",
        "Authorization": ARCANE_MIGRATION_TOKEN,
        "x-user-agent": "Arcane-Bot-5.0",
        "Referer": "https://arcane.bot/lb/new-hyleus",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }

    async with ClientSession() as sess:
        for page in range(pages):
            try:
                async with sess.get(
                    url + f"?page={page}&limit=100", headers=headers,
                    timeout=ClientTimeout(total=None)
                ) as resp:
                    if resp.status != 200:
                        raise Exception("non-200")

                    payload = await resp.json(content_type=None)
                    for entry in payload.get("levels", []):
                        uid = int(entry.get("id"))
                        txp = float(entry.get("xp", 0)) + total_xp_for_arcane_level(int(entry.get("level", 0)))

                        await db.db_ensure_level_user(gid, uid)
                        lv, xp_in, _ = level_from_xp(formula, txp)
                        await db.db_update_level_user(gid, uid, float(xp_in), lv, txp)

                        synced += 1
                        synced_uids.append(uid)
            except Exception:
                errors.append(f"Page {page}: failed.")
                break

    return synced, synced_uids, errors
# endregion

async def _rank_card_response(bot: commands.Bot, ctx: Interaction, user: User = None, color: str = None, bg_url: str = None):
    target = user or ctx.user

    await db.db_ensure_level_user(ctx.guild_id, target.id)
    cfg = await db.db_get_levelling_config(ctx.guild_id)

    if not cfg["enabled"]:
        await ctx.response.send_message("Levelling is not enabled on this server.", ephemeral=True)
        return

    if ctx.user.id == target.id:
        profile = await db.db_get_user_profile(ctx.user.id)
        updated = False

        if color is not None:
            profile["custom_color"] = None if color.lower() in ("none", "clear", "reset", "unset", "del", "delete", "remove") else ("#" + color.lstrip("#")) if color.strip() and not color.startswith("#") else color.strip()
            updated = True
        if bg_url is not None:
            profile["custom_bg"] = None if bg_url.lower() in ("none", "clear", "reset", "unset", "del", "delete", "remove") else bg_url.strip()
            updated = True

        if updated:
            await db.db_save_user_profile(ctx.user.id, profile.get("custom_bg"), profile.get("custom_color"))

    await ctx.response.defer()

    ud = await db.db_get_level_user(ctx.guild_id, target.id)
    formula = cfg["xp_config"]["level_formula"]
    lv, xp_in, xp_need = level_from_xp(formula, ud.get("total_xp", 0))
    rank = await db.db_rank_in_guild(ctx.guild_id, target.id)

    try:
        f = await _generate_rank_card(target, lv, rank, xp_in, xp_need, ud.get("total_xp", 0))
        await ctx.followup.send(file=f)
    except Exception as e:
        print(f"[Levelling] rank card error: {e}")
        await ctx.followup.send("Failed to generate rank card.")


class LevellingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        global _bot
        self.bot = bot
        _bot = bot

    async def cog_load(self):
        self.voice_xp_tick.start()

    async def cog_unload(self):
        self.voice_xp_tick.cancel()

    # region /level
    level_group = Group(name="level", description="Levelling commands", allowed_contexts=ALL, allowed_installs=ALL_INSTALLS)
    level_config_group = Group(name="config", description="Configure the levelling system", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL, parent=level_group)
    level_reward_group = Group(name="reward", description="Level reward role management", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL, parent=level_group)
    level_admin_group = Group(name="admin", description="Admin XP management", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL, parent=level_group)

    @level_group.command(name="rank", description="View your rank card")
    @describe(user="User to view", color="Accent color hex(es), comma-separated for gradient (e.g. #FF6B6B,#4ECDC4)", bg_url="Custom background URL (or 'reset')")
    @allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def level_rank(self, ctx: Interaction, user: User = None, color: str = None, bg_url: str = None):
        await _rank_card_response(self.bot, ctx, user, color, bg_url)

    @app_commands.command(name="rank", description="View your rank card")
    @describe(user="User to view", color="Accent color hex(es), comma-separated for gradient (e.g. #FF6B6B,#4ECDC4)", bg_url="Custom background URL (or 'reset')")
    @allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def rank(self, ctx: Interaction, user: User = None, color: str = None, bg_url: str = None):
        await _rank_card_response(self.bot, ctx, user, color, bg_url)

    @level_group.command(name="leaderboard", description="View the server leaderboard")
    @describe(top="Number of users to show (min 3)")
    @allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def level_leaderboard(self, ctx: Interaction, top: int = 10):
        if not ctx.permissions.manage_guild:
            return
        await db.db_ensure_levelling(ctx.guild_id)
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        if not cfg["enabled"]:
            await ctx.response.send_message("Levelling is not enabled.", ephemeral=True)
            return

        await ctx.response.defer()

        top = max(top, 3)
        ranked = await db.db_get_leaderboard(ctx.guild_id, limit=top * 4)
        valid = [(uid, ud) for uid, ud in ranked if ctx.guild.get_member(uid)][:top]

        if not valid:
            await ctx.followup.send("No users have earned XP yet.")
            return
        formula = cfg["xp_config"]["level_formula"]
        try:
            f = await _generate_leaderboard_card(ctx.guild, valid, formula)
            await ctx.followup.send(file=f)
        except Exception as e:
            print(f"[Levelling] leaderboard card error: {e}")
            await ctx.followup.send("Failed to generate leaderboard.")
    # endregion

    # region /level config
    @level_config_group.command(name="enable", description="Enable or disable the levelling system")
    @default_permissions(manage_guild=True)
    @guild_only()
    async def level_cfg_enable(self, ctx: Interaction, enabled: bool):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        cfg["enabled"] = enabled
        await db.db_save_levelling_config(ctx.guild_id, cfg)

        await ctx.response.send_message(f"Levelling **{'enabled' if enabled else 'disabled'}**.", ephemeral=True)

    @level_config_group.command(name="levelup", description="Configure level-up announcements")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(
        message="Template vars: ${user_mention} ${user_display} ${level} ${old_level} ${xp} ${total_xp} ${roles_gained} ${roles_gained_names} {earned:...}; use {name} and {mention} inside {earned:} for a per-role message (omitted if no roles gained)",
        channel="Channel for announcements (blank = same channel as trigger)",
        reset_channel="Clear the configured channel back to 'same channel as trigger'",
        dm="DM the user instead of posting in a channel",
        image="Attach a level-up card image",
        enabled="Enable/disable announcements"
    )
    async def level_cfg_levelup(self, ctx: Interaction, message: str = None, channel: Union[GuildChannel, Thread] = None,
                                 reset_channel: bool = False, dm: bool = None, image: bool = None, enabled: bool = None):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)

        lu = cfg["levelup"]
        if message is not None:
            lu["message"] = message
        if reset_channel:
            lu["channel"] = None
        if channel is not None:
            lu["channel"] = channel.id
        if dm is not None:
            lu["dm"] = dm
        if image is not None:
            lu["image"] = image
        if enabled is not None:
            lu["enabled"] = enabled

        await db.db_save_levelling_config(ctx.guild_id, cfg)

        embed = Embed(title="Level-up Announcements", color=0xF52754)
        embed.add_field(name="Enabled", value=str(lu["enabled"]), inline=True)
        embed.add_field(name="Channel", value=f"<#{lu['channel']}>" if lu.get("channel") else "same as trigger", inline=True)
        embed.add_field(name="DM", value=str(lu["dm"]), inline=True)
        embed.add_field(name="Image", value=str(lu["image"]), inline=True)
        embed.add_field(name="Message", value=f"```{lu['message']}```", inline=False)
        await ctx.response.send_message(embed=embed, ephemeral=True)

    @level_config_group.command(name="firstplace", description="Set a role for the #1 leaderboard user")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(role="The role to grant to the #1 user (leave blank to disable)")
    async def level_cfg_firstplace(self, ctx: Interaction, role: Role = None):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        cfg["roles"]["first_place"] = role.id if role else None
        await db.db_save_levelling_config(ctx.guild_id, cfg)
        await _update_first_place_role(ctx.guild_id)
        await ctx.response.send_message(
            f"First-place role set to {role.mention}." if role else "First-place role disabled.",
            ephemeral=True, allowed_mentions=AllowedMentions.none())

    @level_config_group.command(name="dashboard", description="View the full levelling configuration")
    @default_permissions(manage_guild=True)
    @guild_only()
    async def level_cfg_dashboard(self, ctx: Interaction):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)

        mc = cfg["xp_config"]["message"]
        vc = cfg["xp_config"]["voice"]
        rc = cfg["xp_config"]["reaction"]
        lu = cfg["levelup"]
        ro = cfg["roles"]

        embed = Embed(title=f"⚙️ Levelling Dashboard ​ · ​ {ctx.guild.name}", color=0x9B59B6)
        embed.add_field(name="System", value="✅ Enabled" if cfg["enabled"] else "❌ Disabled", inline=False)

        effort = mc.get("effort_xp", {})
        msg_lines = [
            f"{'✅' if mc['enabled'] else '❌'} XP: **{mc.get('min_xp', 15):,} – {mc.get('max_xp', 40):,}** per message",
            f"Effort XP: {'✅' if effort.get('enabled', True) else '❌'} +{effort.get('bonus_percent', 10):,}% if ≥{effort.get('min_words', 25):,} words",
            f"Cooldown: **{mc['cooldown']:,}s** | Min length: **{mc['min_length']:,}**",
            f"Multipliers: **{len(mc['multipliers']):,}**"
        ]

        embed.add_field(name="Message XP", value="\n".join(msg_lines), inline=False)
        embed.add_field(name="Voice XP", value=f"{'✅' if vc['enabled'] else '❌'} **{vc['xp_per_minute']:,}** XP/min · Muted: **{vc['muted_multiplier']:,}x** · Deaf: **{vc['deafened_multiplier']:,}x**", inline=False)
        embed.add_field(name="Reaction XP", value=f"{'✅' if rc['enabled'] else '❌'} **{rc['xp_per_reaction']:,}** XP/reaction | Cooldown: **{rc['cooldown']:,}s**", inline=False)

        lu_ch = f"<#{lu['channel']}>" if lu.get('channel') else 'same channel'
        lu_enabled = '✅' if lu['enabled'] else '❌'
        lu_msg = lu['message'][:80] + ('...' if len(lu['message']) > 80 else '')

        embed.add_field(
            name="🎉 Level-up",
            value=f"{lu_enabled} ch:{lu_ch} | dm:{lu['dm']} | img:{lu['image']}\n`{lu_msg}`",
            inline=False
        )

        fp = ro["first_place"]
        rewards = ro["rewards"]
        rwd = "\n".join(f"LV. **{r['level']:,}** ​ · ​ <@&{r['role_id']}> ({'kept' if r.get('keep', True) else 'temp'})" + (f" · group `{r['exclusive_group']}`" if r.get('exclusive_group') else "") for r in rewards) or "None"

        embed.add_field(name="🏆 Roles", value=f"First place: {f'<@&{fp}>' if fp else 'None'}\n{rwd}", inline=False)
        lb = await db.db_get_leaderboard(ctx.guild_id)
        embed.set_footer(text=f"{len(lb)} users tracked")

        await ctx.response.send_message(embed=embed, ephemeral=True)
    # endregion

    # region /level reward
    @level_reward_group.command(name="add", description="Add a role reward for reaching a level")
    @default_permissions(manage_guild=True)
    @describe(level="Level at which to grant the role", role="Role to grant",
              keep="Keep the role when the user surpasses this level (default: True)",
              exclusive_group="Roles in the same group are mutually exclusive (only highest active)")
    async def level_reward_add(self, ctx: Interaction, level: int, role: Role, keep: bool = True, exclusive_group: str = None):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        rewards = cfg["roles"]["rewards"]

        if any(r["level"] == level and r["role_id"] == role.id for r in rewards):
            await ctx.response.send_message("This exact reward already exists.", ephemeral=True)
            return

        entry = {"level": level, "role_id": role.id, "keep": keep}
        if exclusive_group:
            entry["exclusive_group"] = exclusive_group.strip().lower()

        rewards.append(entry)
        rewards.sort(key=lambda r: r["level"])

        await db.db_save_levelling_config(ctx.guild_id, cfg)

        msg = f"{role.mention} granted at level **{level:,}** ({'kept' if keep else 'removed at next milestone'})"

        if exclusive_group:
            msg += f"\nExclusive group: `{exclusive_group.strip().lower()}`."

        await ctx.response.send_message(msg, ephemeral=True, allowed_mentions=AllowedMentions.none())

    @level_reward_group.command(name="remove", description="Remove a level role reward")
    @default_permissions(manage_guild=True)
    @describe(level="Level of the reward to remove", role="Specific role (required if multiple rewards exist at this level)")
    async def level_reward_remove(self, ctx: Interaction, level: int, role: Role = None):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        rewards = cfg["roles"]["rewards"]

        at_level = [r for r in rewards if r["level"] == level]
        if not at_level:
            await ctx.response.send_message(f"No rewards at level {level:,}.", ephemeral=True)
            return

        if len(at_level) > 1 and role is None:
            roles_str = "\n".join(f"• <@&{r['role_id']}>" + (f" (group `{r['exclusive_group']}`)" if r.get("exclusive_group") else "") for r in at_level)
            await ctx.response.send_message(f"**{len(at_level):,} rewards** at level {level:,}. Use `role` to pick one:\n{roles_str}", ephemeral=True, allowed_mentions=AllowedMentions.none())
            return

        before = len(rewards)
        if role:
            cfg["roles"]["rewards"] = [r for r in rewards if not (r["level"] == level and r["role_id"] == role.id)]
        else:
            cfg["roles"]["rewards"] = [r for r in rewards if r["level"] != level]

        removed = before - len(cfg["roles"]["rewards"])
        await db.db_save_levelling_config(ctx.guild_id, cfg)

        await ctx.response.send_message(f"Removed {removed} reward(s) at level {level:,}." if removed else "No matching reward found.", ephemeral=True)

    @level_reward_group.command(name="list", description="List all level rewards")
    async def level_reward_list(self, ctx: Interaction):
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        rewards = cfg["roles"]["rewards"]
        if not rewards:
            await ctx.response.send_message("No level rewards configured.", ephemeral=True)
            return

        desc = "\n".join(f"Level **{r['level']:,}**: <@&{r['role_id']}> ({'kept' if r.get('keep', True) else 'temporary'})" + (f" · group `{r['exclusive_group']}`" if r.get("exclusive_group") else "") for r in rewards)

        await ctx.response.send_message(embed=Embed(title="Level Rewards", description=desc, color=0xF52754), allowed_mentions=AllowedMentions.none(), ephemeral=True)
    # endregion

    # region /level admin
    @level_admin_group.command(name="transferxp", description="Swap two users' XP and level data")
    @default_permissions(manage_guild=True)
    async def level_admin_transferxp(self, ctx: Interaction, user1: User, user2: User):
        if not ctx.permissions.manage_guild:
            return
        await db.db_ensure_level_user(ctx.guild_id, user1.id)
        await db.db_ensure_level_user(ctx.guild_id, user2.id)

        u1 = await db.db_get_level_user(ctx.guild_id, user1.id)
        u2 = await db.db_get_level_user(ctx.guild_id, user2.id)

        ts1, ts2 = u1.get("last_message_ts", 0), u2.get("last_message_ts", 0)

        await db.db_update_level_user(ctx.guild_id, user1.id, u2["xp"], u2["level"], u2["total_xp"], ts1)
        await db.db_update_level_user(ctx.guild_id, user2.id, u1["xp"], u1["level"], u1["total_xp"], ts2)

        await ctx.response.send_message(
            f"✅ Swapped XP: {user1.mention} → Level **{u2['level']:,}** ({int(u2['total_xp']):,} XP), "
            f"{user2.mention} → Level **{u1['level']:,}** ({int(u1['total_xp']):,} XP).", ephemeral=True)

    @level_admin_group.command(name="setxp", description="Set a user's total XP directly")
    @default_permissions(manage_guild=True)
    async def level_admin_setxp(self, ctx: Interaction, user: User, total_xp: int):
        if not ctx.permissions.manage_guild:
            return
        await db.db_ensure_level_user(ctx.guild_id, user.id)
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        formula = cfg["xp_config"]["level_formula"]

        txp = total_xp
        lv, xp_in, _ = level_from_xp(formula, txp)
        await db.db_update_level_user(ctx.guild_id, user.id, xp_in, lv, txp)

        await ctx.response.send_message(f"Set {user.mention}'s XP to **{int(txp):,}** (Level **{lv:,}**).", ephemeral=True)

    @level_admin_group.command(name="addxp", description="Add XP to a user")
    @default_permissions(manage_guild=True)
    @guild_only()
    async def level_admin_addxp(self, ctx: Interaction, user: User, xp: int):
        if not ctx.permissions.manage_guild:
            return
        await db.db_ensure_level_user(ctx.guild_id, user.id)
        await award_xp(ctx.guild_id, user.id, float(xp), ctx.channel, user)

        ud = await db.db_get_level_user(ctx.guild_id, user.id)

        await ctx.response.send_message(f"Added **{xp:,} XP** to {user.mention}. Now Level **{ud['level']:,}** with **{int(ud['total_xp']):,}** total XP.", ephemeral=True)

    @level_admin_group.command(name="resetxp", description="Reset a user's XP to zero")
    @default_permissions(manage_guild=True)
    async def level_admin_resetxp(self, ctx: Interaction, user: User):
        if not ctx.permissions.manage_guild:
            return
        await db.db_reset_level_user(ctx.guild_id, user.id)
        await ctx.response.send_message(f"Reset {user.mention}'s XP.", ephemeral=True)

    @level_admin_group.command(name="resetall", description="Reset all XP data for this server")
    @default_permissions(administrator=True)
    async def level_admin_resetall(self, ctx: Interaction):
        if not ctx.permissions.manage_guild:
            return
        count = await db.db_reset_all_level_users(ctx.guild_id)
        await ctx.response.send_message(f"Reset all XP/level data for this server. Deleted {count:,} users' levels.", ephemeral=True)

    @level_admin_group.command(name="arcanesync", description="Sync XP data from the Arcane bot's leaderboard")
    @default_permissions(manage_guild=True)
    @describe(pages="Pages to fetch (100 users/page, max 10)")
    async def level_admin_arcane_sync(self, ctx: Interaction, pages: int = 1, grant_achievement_xp: bool = True, announce_level_ups: bool = False):
        if not ctx.permissions.manage_guild:
            return
        await ctx.response.defer(ephemeral=True)
        pages = min(max(1, pages), 10)

        synced, synced_uids, errors = await sync_arcane_leaderboard(ctx.guild_id, pages)

        msg = f"✅ Synced **{synced}** user(s) from Arcane."

        if grant_achievement_xp and synced_uids:
            ach_xp_total = 0
            users_with_ach_xp = 0

            for uid in synced_uids:
                user_achievements = await db.db_get_user_achievements(ctx.guild_id, uid)
                if not user_achievements:
                    continue

                user_ach_xp = 0
                for aid in user_achievements:
                    ach_def = await db.db_get_achievement_def(ctx.guild_id, aid)
                    if ach_def is not None:
                        user_ach_xp += ach_def.get("xp_reward", 0) or 0

                if user_ach_xp <= 0:
                    continue

                member = ctx.guild.get_member(uid)
                await award_xp(ctx.guild_id, uid, float(user_ach_xp), ctx.channel if announce_level_ups else None, member, "achievement_sync")
                ach_xp_total += user_ach_xp
                users_with_ach_xp += 1

            msg += f"\nGranted achievement XP to **{users_with_ach_xp}** user(s) (**{ach_xp_total:,}** total XP added)."

        if errors:
            msg += "\n\n**Errors:**\n" + "\n".join(f"· {e}" for e in errors)

        await ctx.followup.send(msg, ephemeral=True)
    # endregion

    # region listeners
    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if message.author.id == self.bot.user.id:
            return
        await process_message_xp(message)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: RawReactionActionEvent):
        if not payload.guild_id:
            return
        await db.db_ensure_levelling(payload.guild_id)
        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id) if guild else None
        if member and not member.bot:
            channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)
            await process_reaction_xp(payload.guild_id, payload.user_id, channel, member)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: Member, before, after):
        guild_id, user_id = member.guild.id, member.id
        VOICE_TRACKING.setdefault(guild_id, {})
        if before.channel is None and after.channel is not None:
            VOICE_TRACKING[guild_id][user_id] = datetime.now()
        elif before.channel is not None and after.channel is None:
            if user_id in VOICE_TRACKING[guild_id]:
                await grant_voice_stuff(self.bot, before, guild_id, user_id, member)

    @tasks.loop(minutes=5)
    async def voice_xp_tick(self):
        from cogs.achievements import check_automated_achievements
        now = datetime.now()

        for gid in list(VOICE_TRACKING.keys()):
            guild = self.bot.get_guild(gid)
            if not guild:
                continue

            await db.db_ensure_levelling(gid)

            cfg = await db.db_get_levelling_config(gid)
            vc_cfg = cfg["xp_config"]["voice"]

            for uid in list(VOICE_TRACKING[gid].keys()):
                member = guild.get_member(uid)
                if not member or not member.voice or not member.voice.channel:
                    VOICE_TRACKING[gid].pop(uid, None)
                    continue

                join_time = VOICE_TRACKING[gid][uid]
                elapsed = (now - join_time).total_seconds() / 60.0
                VOICE_TRACKING[gid][uid] = now  # reset for next tick

                # Progress + achievements
                await db.db_update_progress(gid, uid, "voice_minutes", elapsed)
                asyncio.create_task(check_automated_achievements(self.bot, gid, uid))

                # Voice XP
                if cfg["enabled"] and vc_cfg["enabled"]:
                    vc = member.voice
                    mult = 1.0

                    if vc.self_mute or vc.mute:
                        mult *= vc_cfg.get("muted_multiplier", 0.5)
                    if vc.self_deaf or vc.deaf:
                        mult *= vc_cfg.get("deafened_multiplier", 0.0)
                    if vc_cfg.get("afk_no_xp") and guild.afk_channel and vc.channel.id == guild.afk_channel.id:
                        mult = 0.0

                    if vc_cfg.get("require_others"):
                        others = [m for m in vc.channel.members if not m.bot and m.id != uid]
                        if not others:
                            mult = 0.0

                    if vc.channel.id in vc_cfg.get("blacklisted_channels", []):
                        mult = 0.0

                    if mult > 0:
                        await process_voice_xp(gid, uid, elapsed * mult, member, vc.channel)
    # endregion


class XpConfigCog(commands.Cog):
    """Houses the top-level `/xp` group, which is a sibling of `/level`
    rather than nested under it (matches the monolith's own command tree)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    xp_group = Group(name="xp", description="XP settings", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL)
    xp_config_group = Group(name="config", description="Configure the XP system", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL, parent=xp_group)
    xp_mult_group = Group(name="multiplier", description="XP multiplier management", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL, parent=xp_group)

    @xp_config_group.command(name="message", description="Configure message XP settings")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(min_xp="Minimum XP per message", max_xp="Maximum XP per message",
              cooldown="Seconds between XP-eligible messages per user", min_length="Minimum character count required to earn XP",
              enabled="Enable or disable message XP")
    async def xp_config_message(self, ctx: Interaction, min_xp: int = None, max_xp: int = None, cooldown: int = None, min_length: int = None, enabled: bool = None):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        mc = cfg["xp_config"]["message"]

        if enabled is not None:
            mc["enabled"] = enabled
        if min_xp is not None:
            mc["min_xp"] = max(1, min_xp)
        if max_xp is not None:
            mc["max_xp"] = max(1, max_xp)
        if cooldown is not None:
            mc["cooldown"] = max(0, cooldown)
        if min_length is not None:
            mc["min_length"] = max(0, min_length)

        await db.db_save_levelling_config(ctx.guild_id, cfg)

        embed = Embed(title="Message XP", color=0xF52754)
        embed.add_field(name="Enabled", value=str(mc["enabled"]), inline=True)
        embed.add_field(name="XP Range", value=f"{mc.get('min_xp', 15):,} – {mc.get('max_xp', 40):,}", inline=True)
        embed.add_field(name="Cooldown", value=f"{mc['cooldown']:,}s", inline=True)
        embed.add_field(name="Min Length", value=f"{mc['min_length']:,}", inline=True)

        await ctx.response.send_message(embed=embed, ephemeral=True)

    @xp_config_group.command(name="effort", description="Configure effort XP bonus for longer messages")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(enabled="Enable or disable bonus effort XP", bonus_percent="% XP bonus (e.g. 10 is +10%)", min_words="Minimum word count to qualify")
    async def xp_config_effort(self, ctx: Interaction, bonus_percent: int = None, min_words: int = None, enabled: bool = None):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        mc = cfg["xp_config"]["message"]
        effort = mc.setdefault("effort_xp", {"enabled": True, "bonus_percent": 10, "min_words": 25})

        if enabled is not None:
            effort["enabled"] = enabled
        if bonus_percent is not None:
            effort["bonus_percent"] = bonus_percent
        if min_words is not None:
            effort["min_words"] = max(1, min_words)

        await db.db_save_levelling_config(ctx.guild_id, cfg)

        embed = Embed(title="Effort XP", color=0xF52754)
        embed.add_field(name="Enabled", value="✅" if effort["enabled"] else "❌", inline=True)
        embed.add_field(name="Bonus", value=f"+{effort['bonus_percent']:,}%", inline=True)
        embed.add_field(name="Min Words", value=f"{effort['min_words']:,}", inline=True)
        embed.add_field(name="Effect", value=f"Messages with ≥{effort['min_words']:,} words earn {effort['bonus_percent']:,}% extra XP", inline=False)

        await ctx.response.send_message(embed=embed, ephemeral=True)

    @xp_config_group.command(name="voice", description="Configure voice XP")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(enabled="Enable or disable voice XP", xp_per_minute="XP awarded per minute in a voice channel",
              require_others="Only grant XP if at least one other non-bot user is present",
              muted_multiplier="XP multiplier when self-muted (0 = no XP)", deafened_multiplier="XP multiplier when self-deafened (0 = no XP)",
              afk_no_xp="Grant no XP while in the AFK channel")
    async def xp_config_voice(self, ctx: Interaction, xp_per_minute: float = None, require_others: bool = None, muted_multiplier: float = None, deafened_multiplier: float = None, afk_no_xp: bool = None, enabled: bool = None):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        vc = cfg["xp_config"]["voice"]

        if enabled is not None:
            vc["enabled"] = enabled
        if xp_per_minute is not None:
            vc["xp_per_minute"] = xp_per_minute
        if require_others is not None:
            vc["require_others"] = require_others
        if muted_multiplier is not None:
            vc["muted_multiplier"] = muted_multiplier
        if deafened_multiplier is not None:
            vc["deafened_multiplier"] = deafened_multiplier
        if afk_no_xp is not None:
            vc["afk_no_xp"] = afk_no_xp
        await db.db_save_levelling_config(ctx.guild_id, cfg)

        embed = Embed(title="Voice XP", color=0xF52754)
        embed.add_field(name="Enabled", value=str(vc["enabled"]), inline=True)
        embed.add_field(name="XP/min", value=f"{vc['xp_per_minute']:,}", inline=True)
        embed.add_field(name="Require others", value=str(vc["require_others"]), inline=True)
        embed.add_field(name="Muted ×", value=f"{vc['muted_multiplier']:,}", inline=True)
        embed.add_field(name="Deafened ×", value=f"{vc['deafened_multiplier']:,}", inline=True)
        embed.add_field(name="No AFK XP", value=str(vc["afk_no_xp"]), inline=True)

        await ctx.response.send_message(embed=embed, ephemeral=True)

    @xp_config_group.command(name="reaction", description="Configure reaction XP")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(enabled="Enable or disable reaction XP", xp_per_reaction="XP awarded per reaction added",
              cooldown="Seconds between XP-eligible reactions per user")
    async def xp_config_reaction(self, ctx: Interaction, xp_per_reaction: float = None, cooldown: int = None, enabled: bool = None):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        rc = cfg["xp_config"]["reaction"]

        if enabled is not None:
            rc["enabled"] = enabled
        if xp_per_reaction is not None:
            rc["xp_per_reaction"] = xp_per_reaction
        if cooldown is not None:
            rc["cooldown"] = max(0, cooldown)
        await db.db_save_levelling_config(ctx.guild_id, cfg)

        embed = Embed(title="Reaction XP", color=0xF52754)
        embed.add_field(name="Enabled", value=str(rc["enabled"]), inline=True)
        embed.add_field(name="XP/reaction", value=str(rc["xp_per_reaction"]), inline=True)
        embed.add_field(name="Cooldown", value=f"{rc['cooldown']}s", inline=True)

        await ctx.response.send_message(embed=embed, ephemeral=True)

    @xp_config_group.command(name="allowbots", description="Allows/denies bots the ability to gain XP and level up")
    @default_permissions(manage_guild=True)
    async def xp_config_allowbots(self, ctx: Interaction, enabled: bool):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        old = cfg["xp_config"]["allow_bots"]
        if old != enabled:
            cfg["xp_config"]["allow_bots"] = enabled
            await db.db_save_levelling_config(ctx.guild_id, cfg)

        await ctx.response.send_message(("Bots **can now earn XP and level up**." if enabled else "Bots **can no longer earn XP or level up.**") if old != enabled else "No change recorded.", ephemeral=True)

    @xp_config_group.command(name="allowwebhooks", description="Allows/denies webhook messages the ability to earn XP and level up")
    @default_permissions(manage_guild=True)
    async def xp_config_allowwebhooks(self, ctx: Interaction, enabled: bool):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        old = cfg["xp_config"].get("allow_webhooks", False)
        if old != enabled:
            cfg["xp_config"]["allow_webhooks"] = enabled
            await db.db_save_levelling_config(ctx.guild_id, cfg)

        await ctx.response.send_message(
            ("Webhook messages **can now earn XP and level up**." if enabled
             else "Webhook messages **can no longer earn XP or level up.**")
            if old != enabled else "No change recorded.", ephemeral=True)

    @xp_config_group.command(name="blacklist", description="Manage channel/role XP blacklists and whitelists")
    @default_permissions(manage_guild=True)
    @describe(action="Add or remove an entry", list_type="Which list to modify", channel="Channel target", role="Role target")
    async def xp_config_blacklist(self, ctx: Interaction, action: Literal["add", "remove"],
                                   list_type: Literal["blacklisted_channels", "whitelisted_channels", "blacklisted_roles"],
                                   channel: Union[GuildChannel, Thread] = None, role: Role = None):
        if not ctx.permissions.manage_guild:
            return
        tid = (channel.id if channel else None) or (role.id if role else None)
        if tid is None:
            await ctx.response.send_message("Specify a channel or role.", ephemeral=True)
            return

        cfg = await db.db_get_levelling_config(ctx.guild_id)
        lst = cfg["xp_config"]["message"][list_type]

        if action == "add":
            if tid not in lst:
                lst.append(tid)
            msg = f"Added to **{list_type.replace('_', ' ')}**."
        else:
            if tid in lst:
                lst.remove(tid)
            msg = f"Removed from **{list_type.replace('_', ' ')}**."

        await db.db_save_levelling_config(ctx.guild_id, cfg)
        await ctx.response.send_message(msg, ephemeral=True)

    @xp_config_group.command(name="noprefix", description="Messages starting with this prefix won't earn XP")
    @default_permissions(manage_guild=True)
    @describe(action="Add or remove", prefix="The prefix string")
    async def xp_config_noprefix(self, ctx: Interaction, action: Literal["add", "remove"], prefix: str):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        lst = cfg["xp_config"]["message"]["no_xp_prefixes"]

        if action == "add":
            if prefix not in lst:
                lst.append(prefix)
            msg = f"Messages starting with `{prefix}` won't earn XP."
        else:
            if prefix in lst:
                lst.remove(prefix)
            msg = f"Prefix `{prefix}` removed."

        await db.db_save_levelling_config(ctx.guild_id, cfg)
        await ctx.response.send_message(msg, ephemeral=True)

    @xp_config_group.command(name="voiceblacklist", description="Blacklist a voice channel from granting XP")
    @default_permissions(manage_guild=True)
    @describe(action="Add or remove", channel="The voice channel")
    async def xp_config_voiceblacklist(self, ctx: Interaction, action: Literal["add", "remove"], channel: GuildChannel):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        lst = cfg["xp_config"]["voice"]["blacklisted_channels"]

        if action == "add":
            if channel.id not in lst:
                lst.append(channel.id)
            msg = f"{channel.mention} will no longer grant voice XP."
        else:
            if channel.id in lst:
                lst.remove(channel.id)
            msg = f"{channel.mention} will now grant voice XP."

        await db.db_save_levelling_config(ctx.guild_id, cfg)
        await ctx.response.send_message(msg, ephemeral=True)

    @xp_config_group.command(name="formula", description="Configure the XP-per-level formula used for levelling")
    @default_permissions(manage_guild=True)
    @guild_only()
    @describe(formula="XP needed to go from `level` to `level+1`, e.g. 'level * 100 + 75'. Leave blank to preview current.",
              reset="Reset to the default formula")
    async def xp_config_formula(self, ctx: Interaction, formula: str = None, reset: bool = False):
        if not ctx.permissions.manage_guild:
            return
        await db.db_ensure_levelling(ctx.guild_id)
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        current = cfg["xp_config"]["level_formula"]

        if reset:
            from core.db import _LVL_XP_CFG_DEFAULT
            cfg["xp_config"]["level_formula"] = _LVL_XP_CFG_DEFAULT["level_formula"]
            await db.db_save_levelling_config(ctx.guild_id, cfg)
            await ctx.response.send_message(
                f"Reset the level formula to the default: `{_LVL_XP_CFG_DEFAULT['level_formula']}`", ephemeral=True)
            return

        if formula is None:
            preview = "\n".join(
                f"Lv.{lv}→{lv+1}: **{_eval_level_formula(current, lv):,.0f} XP** ​ · ​ total to reach: **{total_xp_for_level(current, lv+1):,.0f}**"
                for lv in (0, 1, 4, 9, 24, 49, 99)
            )
            embed = Embed(title="Current Level Formula", color=0x5865F2)
            embed.add_field(name="Formula", value=f"```{current}```", inline=False)
            embed.add_field(name="Preview", value=preview, inline=False)
            embed.set_footer(text=_formula_computation_mode(current).capitalize())
            await ctx.response.send_message(embed=embed, ephemeral=True)
            return

        formula = formula.strip()
        ok, err = validate_level_formula(formula)
        if not ok:
            await ctx.response.send_message(f"❌ Invalid formula: {err}", ephemeral=True)
            return

        cfg["xp_config"]["level_formula"] = formula
        await db.db_save_levelling_config(ctx.guild_id, cfg)

        preview = "\n".join(
            f"Lv.{lv}→{lv+1}: **{_eval_level_formula(formula, lv):,.0f} XP** ​ · ​ total to reach: **{total_xp_for_level(formula, lv+1):,.0f}**"
            for lv in (0, 1, 4, 9, 24, 49, 99)
        )
        embed = Embed(title="Level Formula Updated", color=0x2ECC71)
        embed.add_field(name="Formula", value=f"```{formula}```", inline=False)
        embed.add_field(name="Preview", value=preview, inline=False)
        embed.set_footer(text=f"{_formula_computation_mode(formula).capitalize()} · Existing users' total XP is unchanged; only how it maps to a level/progress bar changes.")
        await ctx.response.send_message(embed=embed, ephemeral=True)

    @xp_mult_group.command(name="add", description="Add an XP multiplier")
    @default_permissions(manage_guild=True)
    @describe(type="Type of multiplier", multiplier="Multiplier value (e.g. 2.0 = double XP)",
              channel="Channel (required for type = channel)", role="Role (required for type = role)",
              time_start="HH:MM start (required for type = time)", time_end="HH:MM end (required for type = time)")
    async def xp_mult_add(self, ctx: Interaction, type: Literal["global", "channel", "role", "time"], multiplier: float,
                           channel: Union[GuildChannel, Thread] = None, role: Role = None, time_start: str = None, time_end: str = None):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)

        entry = {"type": type, "multiplier": multiplier}
        if type == "channel":
            if not channel:
                await ctx.response.send_message("Specify a channel.", ephemeral=True)
                return
            entry["id"] = channel.id
        elif type == "role":
            if not role:
                await ctx.response.send_message("Specify a role.", ephemeral=True)
                return
            entry["id"] = role.id
        elif type == "time":
            if not time_start or not time_end:
                await ctx.response.send_message("Specify time_start and time_end.", ephemeral=True)
                return
            entry["start"] = time_start
            entry["end"] = time_end

        mults = cfg["xp_config"]["message"]["multipliers"]
        mults.append(entry)
        await db.db_save_levelling_config(ctx.guild_id, cfg)

        await ctx.response.send_message(f"Added **{multiplier:,}×** `{type}` multiplier.", ephemeral=True)

    @xp_mult_group.command(name="remove", description="Remove an XP multiplier by index")
    @default_permissions(manage_guild=True)
    async def xp_mult_remove(self, ctx: Interaction, index: int):
        if not ctx.permissions.manage_guild:
            return
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        mults = cfg["xp_config"]["message"]["multipliers"]

        if index < 1 or index > len(mults):
            await ctx.response.send_message(f"Invalid index (1–{len(mults)}).", ephemeral=True)
            return

        removed = mults.pop(index - 1)
        await db.db_save_levelling_config(ctx.guild_id, cfg)

        await ctx.response.send_message(f"Removed multiplier #{index:,} ({removed['type']}, {removed['multiplier']:,}×).", ephemeral=True)

    @xp_mult_group.command(name="list", description="List all active XP multipliers")
    async def xp_mult_list(self, ctx: Interaction):
        cfg = await db.db_get_levelling_config(ctx.guild_id)
        mults = cfg["xp_config"]["message"]["multipliers"]

        if not mults:
            await ctx.response.send_message("No multipliers configured.", ephemeral=True)
            return

        lines = []
        for i, m in enumerate(mults, 1):
            if m["type"] == "global":
                lines.append(f"**{i}.** Global ​ · ​ **{m['multiplier']}×**")
            elif m["type"] == "channel":
                lines.append(f"**{i}.** Channel <#{m['id']}> ​ · ​ **{m['multiplier']}×**")
            elif m["type"] == "role":
                lines.append(f"**{i}.** Role <@&{m['id']}> ​ · ​ **{m['multiplier']}×**")
            elif m["type"] == "time":
                lines.append(f"**{i}.** Time {m['start']}–{m['end']} ​ · ​ **{m['multiplier']:,}×** (PST)")

        await ctx.response.send_message(embed=Embed(title="XP Multipliers", description="\n".join(lines), color=0xF52754), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LevellingCog(bot))
    await bot.add_cog(XpConfigCog(bot))

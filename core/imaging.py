"""Pillow-based rendering primitives shared by every card-generating
feature (achievement leaderboards, level-up cards, rank cards, ...).

None of this knows what a "level" or an "achievement" is -- it's pure
colour math, font loading, and gradient drawing. Feature-specific card
layouts stay in their own cog and call into here for the boring parts.
"""
from __future__ import annotations
import asyncio
from io import BytesIO
from typing import Callable

from aiohttp import ClientSession, ClientTimeout
from discord import File, Guild
from PIL import Image, ImageDraw, ImageFont

try:
    from pilmoji import Pilmoji as _Pilmoji
    _PILMOJI_AVAILABLE = True
except ImportError:
    _PILMOJI_AVAILABLE = False

import re

_PIX_FONT_CACHE: dict[tuple[str, int], ImageFont.ImageFont] = {}
_sprite_http_session: ClientSession | None = None


async def get_http_session() -> ClientSession:
    """A single shared `ClientSession` for image downloads, created lazily
    on first use (there's no bot-lifecycle hook that's convenient to tie
    session creation to from a plain utility module)."""
    global _sprite_http_session
    if _sprite_http_session is None or _sprite_http_session.closed:
        _sprite_http_session = ClientSession()
    return _sprite_http_session


async def close_http_session() -> None:
    if _sprite_http_session and not _sprite_http_session.closed:
        await _sprite_http_session.close()


async def fetch_image_bytes(url: str, timeout: int = 5) -> bytes | None:
    try:
        sess = await get_http_session()
        async with sess.get(url, timeout=ClientTimeout(total=timeout)) as r:
            if r.status == 200:
                return await r.read()
    except Exception:
        pass
    return None


def _make_silhouette_sync(raw_bytes: bytes) -> BytesIO | None:
    """Ported verbatim from the monolith's `_make_silhouette_sync`: keeps
    the source image's alpha channel (so the silhouette is still the right
    shape) and flattens RGB to near-black."""
    try:
        img = Image.open(BytesIO(raw_bytes)).convert("RGBA")
    except Exception:
        return None
    _r, _g, _b, a = img.split()
    dark = Image.new("L", img.size, 15)
    img = Image.merge("RGBA", (dark, dark, dark, a))
    buf = BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


async def make_silhouette(url: str) -> File | None:
    """A same-shaped, near-black `discord.File` for `url`'s image -- the
    "who's that Pokemon" teaser a spawn embed shows before it's been
    caught/identified. Ported from the monolith's `_make_silhouette`,
    split into the fetch (this function, using `fetch_image_bytes` above
    rather than its own one-off session/request) and the CPU-bound Pillow
    work (`_make_silhouette_sync`, still run in a thread so a slow/large
    image doesn't block the event loop)."""
    raw = await fetch_image_bytes(url)
    if raw is None:
        return None
    buf = await asyncio.to_thread(_make_silhouette_sync, raw)
    return File(buf, filename="silhouette.png") if buf else None


def get_font(style: str, size: int) -> ImageFont.ImageFont:
    """Loads a system font by rough style name ("bold"/"reg"), trying a
    few common Linux font package locations and falling back to Pillow's
    bundled default if none are installed."""
    key = (style, size)
    if key in _PIX_FONT_CACHE:
        return _PIX_FONT_CACHE[key]

    paths = {
        "bold": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ],
        "reg": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ],
    }
    for path in paths.get(style, paths["reg"]):
        try:
            font = ImageFont.truetype(path, size)
            _PIX_FONT_CACHE[key] = font
            return font
        except Exception:
            continue
    font = ImageFont.load_default()
    _PIX_FONT_CACHE[key] = font
    return font


def draw_text(img: Image.Image, xy: tuple[int, int], text: str, font,
              fill: tuple[int, int, int], draw: ImageDraw.ImageDraw = None) -> None:
    """Draws `text`, rendering emoji glyphs via Pilmoji when available and
    silently stripping them (rather than showing tofu boxes) otherwise."""
    if _PILMOJI_AVAILABLE:
        try:
            with _Pilmoji(img) as p:
                p.text(xy, text, fill, font)
                return
        except Exception:
            pass

    if draw is None:
        draw = ImageDraw.Draw(img)

    clean = re.sub(
        "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF\U00002500-\U00002BEF\U00002702-\U000027B0"
        "\U000024C2-\U0001F251\U0001f926-\U0001f937\U00010000-\U0010ffff"
        "\u2640-\u2642\u2600-\u2B55\u200d\u23cf\u23e9\u231a\ufe0f\u3030]+",
        "", text, flags=re.UNICODE,
    )
    draw.text(xy, clean, fill=fill, font=font)


# --- Colour math -----------------------------------------------------------
def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def parse_hex_color(hex_str: str, default: tuple[int, int, int] = (0, 180, 216)) -> tuple[int, int, int]:
    if not hex_str:
        return default
    hex_str = hex_str.lstrip("#")
    try:
        return tuple(int(hex_str[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def parse_accent_colors(color_str: str) -> list[tuple[int, int, int]]:
    """A user-supplied "#RRGGBB,#RRGGBB,..." string -> a list of RGB
    tuples, falling back to a single default teal if unset/unparseable."""
    if not color_str:
        return [(0, 180, 216)]
    colors = [parse_hex_color(c.strip(), (0, 180, 216))
              for c in color_str.replace(", ", ",").replace(" ", ",").split(",") if c.strip()]
    return colors if colors else [(0, 180, 216)]


def rgb_to_hsv(r: int, g: int, b: int) -> tuple[float, float, float]:
    r_, g_, b_ = r / 255.0, g / 255.0, b / 255.0
    max_c, min_c = max(r_, g_, b_), min(r_, g_, b_)
    delta = max_c - min_c

    if delta == 0:
        h = 0.0
    elif max_c == r_:
        h = ((g_ - b_) / delta) % 6
    elif max_c == g_:
        h = (b_ - r_) / delta + 2
    else:
        h = (r_ - g_) / delta + 4
    h /= 6

    s = 0.0 if max_c == 0 else delta / max_c
    return h, s, max_c


def hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    h *= 6
    i = int(h)
    f = h - i
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    i %= 6

    if i == 0:
        r, g, b = v, t, p
    elif i == 1:
        r, g, b = q, v, p
    elif i == 2:
        r, g, b = p, v, t
    elif i == 3:
        r, g, b = p, q, v
    elif i == 4:
        r, g, b = t, p, v
    else:
        r, g, b = v, p, q
    return int(r * 255), int(g * 255), int(b * 255)


def merge_colors(colors: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    """Averages a list of colours in HSV space (so e.g. red+green doesn't
    collapse to muddy brown the way naive RGB averaging would)."""
    if not colors:
        return (0, 0, 0)
    if len(colors) == 1:
        return colors[0]

    h = s = v = 0.0
    for r, g, b in colors:
        hh, ss, vv = rgb_to_hsv(r, g, b)
        h += hh
        s += ss
        v += vv
    n = len(colors)
    return hsv_to_rgb(h / n, s / n, v / n)


def lerp_color(colors: list[tuple[int, int, int]], t: float) -> tuple[int, int, int]:
    """Interpolates along a colour gradient defined by `colors` at
    position `t` in [0, 1], clamped at the ends (no wraparound)."""
    n = len(colors) - 1
    if n <= 0:
        return colors[0]
    seg = t * n
    idx = min(int(seg), n - 1)
    frac = seg - idx
    c1, c2 = colors[idx], colors[min(idx + 1, n)]
    return tuple(int(c1[i] + (c2[i] - c1[i]) * frac) for i in range(3))


def lerp_color_wrap(colors: list[tuple[int, int, int]], t: float) -> tuple[int, int, int]:
    """Like `lerp_color`, but the gradient wraps around (colours[-1]
    blends back into colours[0]) -- used for conic/ring gradients."""
    n = len(colors)
    if n <= 1:
        return colors[0]
    pos = t * n
    i0 = int(pos) % n
    i1 = (i0 + 1) % n
    frac = pos - int(pos)
    c0, c1 = colors[i0], colors[i1]
    return tuple(int(a + (b - a) * frac) for a, b in zip(c0, c1))


# --- Gradient drawing --------------------------------------------------
def make_radial_gradient(size: tuple[int, int], colors: list[tuple[int, int, int]],
                          t_func: Callable[[int, int], float], alpha: int = 255) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            c = lerp_color_wrap(colors, t_func(x, y))
            px[x, y] = (*c, alpha)
    return img


def conic_t(cx: int, cy: int):
    from math import atan2, pi

    def fn(x: int, y: int) -> float:
        return (atan2(y - cy, x - cx) + pi) / (2 * pi)
    return fn


def perimeter_t(x1: int, y1: int, x2: int, y2: int):
    w, h = x2 - x1, y2 - y1
    perim = 2 * (w + h)

    def fn(x, y):
        left, right = x - x1, x2 - x
        top, bottom = y - y1, y2 - y
        m = min(left, right, top, bottom)
        if m == top:
            t = left
        elif m == right:
            t = w + top
        elif m == bottom:
            t = w + h + (w - left)
        else:
            t = 2 * w + h + (h - top)
        return t / perim
    return fn


def gradient_rounded_bar(img: Image.Image, x1: int, y1: int, x2: int, y2: int,
                          colors: list[tuple[int, int, int]], radius: int = 8) -> None:
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    for px in range(w):
        t = px / (w - 1) if w > 1 else 0
        c = lerp_color(colors, t)
        gdraw.line([(px, 0), (px, h - 1)], fill=(*c, 255))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    grad.putalpha(mask)
    img.paste(grad, (x1, y1), grad)


def gradient_ring(img: Image.Image, cx: int, cy: int, outer_r: int, ring_w: int,
                   colors: list[tuple[int, int, int]]) -> None:
    size = outer_r * 2
    grad = make_radial_gradient((size, size), colors, conic_t(size / 2, size / 2), alpha=220)
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([0, 0, size - 1, size - 1], fill=255)
    md.ellipse([ring_w, ring_w, size - 1 - ring_w, size - 1 - ring_w], fill=0)
    grad.putalpha(mask)
    img.alpha_composite(grad, (cx - outer_r, cy - outer_r))


def gradient_rounded_border(img: Image.Image, x1: int, y1: int, x2: int, y2: int,
                             colors: list[tuple[int, int, int]], radius: int = 16,
                             width: int = 2, alpha: int = 220) -> None:
    w, h = x2 - x1, y2 - y1
    grad = make_radial_gradient((w, h), colors, perimeter_t(x1, y1, x2, y2), alpha)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, outline=255, width=width)
    grad.putalpha(mask)
    img.alpha_composite(grad, (x1, y1))


# --- Generic leaderboard card -----------------------------------------
def render_generic_leaderboard_card_sync(
    title: str, subtitle: str, entries: list[tuple[int, str, str]],
    names: list[str], avatar_bytes_list: list[bytes | None],
) -> File:
    """Runs on a worker thread (Pillow is blocking). `entries` is
    `(user_id, primary_stat_text, secondary_stat_text)` triples, already
    sorted best-first."""
    row_h, header_h = 72, 107
    w = 820
    h = header_h + len(entries) * row_h + 20
    img = Image.new("RGBA", (w, h), (17, 18, 20, 255))
    draw = ImageDraw.Draw(img)
    f_head, f_sub, f_row = get_font("bold", 35), get_font("reg", 20), get_font("bold", 25)

    draw_text(img, (35, 25), title.upper(), f_head, (255, 255, 255), draw)
    draw.text((37, 69), subtitle, fill=(148, 155, 164), font=f_sub)
    draw.line([(35, 95), (w - 35, 95)], fill=(43, 45, 49, 255), width=2)

    medal_colors = {1: (255, 215, 0), 2: (192, 192, 192), 3: (205, 127, 50)}

    for idx, ((uid, primary, secondary), name, av_bytes) in enumerate(zip(entries, names, avatar_bytes_list)):
        rank = idx + 1
        y = header_h + idx * row_h
        row_fill = (35, 36, 39, 255) if rank % 2 == 0 else (43, 45, 49, 120)
        draw.rounded_rectangle([25, y, w - 25, y + row_h - 8], radius=8, fill=row_fill)

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
        while draw.textlength(disp, font=f_row) > 340 and len(disp) > 1:
            disp = disp[:-1]
        if disp != name:
            disp += "..."
        draw_text(img, (160, y + 18), disp, f_row, (255, 255, 255), draw)

        pw = draw.textlength(primary, font=f_row)
        draw.text((w - 55 - pw, y + 8), primary, fill=(255, 215, 0), font=f_row)
        sw = draw.textlength(secondary, font=f_sub)
        draw.text((w - 55 - sw, y + 38), secondary, fill=(148, 155, 164), font=f_sub)

    buf = BytesIO()
    img.convert("RGB").save(buf, "PNG")
    buf.seek(0)
    return File(buf, filename="leaderboard.png")


async def generate_generic_leaderboard_card(
    guild: Guild, entries: list[tuple[int, str, str]], title: str, subtitle: str,
) -> File:
    """`entries` is `(user_id, primary_stat, secondary_stat)`. Fetches
    avatars concurrently, then renders on a worker thread."""
    import asyncio

    avatar_urls = []
    for uid, _, _ in entries:
        member = guild.get_member(uid)
        avatar_urls.append(str(member.display_avatar.with_size(64).url) if member else None)

    avatar_bytes_list = await asyncio.gather(
        *(fetch_image_bytes(u, timeout=3) if u else asyncio.sleep(0, result=None) for u in avatar_urls)
    )
    names = [(guild.get_member(uid).display_name if guild.get_member(uid) else f"User {uid}") for uid, _, _ in entries]

    return await asyncio.to_thread(
        render_generic_leaderboard_card_sync, title, subtitle, entries, names, avatar_bytes_list)

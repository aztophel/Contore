"""Pure PNG battle-scene rendering. Old code: contore.py:13573-14360 (the
rendering pipeline itself) plus its font helper at 4246.

This entire module operates on plain dicts (`slot_data`) rather than any
battle-engine object -- it always did, even in the old monolith, which is
why it ports over almost unchanged: `render_battle_png` never reached into
FighterState internals directly either, it consumed a dict built by
`_battle_render_slot` (old code, contore.py:14311+). The equivalent
adapter for the new Battler model is `_battler_to_slot_data` in
battle_view.py -- keeping that conversion there and this module
Battler-agnostic is what lets this file be a straight port with no
redesign, matching the "talks to BattleEngine's public interface only"
boundary from pokemon/ui/OWNER.md.

`slot_data` dict shape (per active Pokemon slot, or None for an empty
slot): {name, level, hp, max_hp, status, gender, species_id,
render_species_id, shiny, mega_evolved, substitute_active, caught_ball,
form, render_form}. `party` lists (for the row of Poké Balls under each
side's panel): [{fainted, status, active}, ...].
"""
from __future__ import annotations
import asyncio
import os
import random
from datetime import datetime
from io import BytesIO

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from discord import File, PartialEmoji
from PIL import Image, ImageDraw, ImageFont

from .assets import (
    BG_BASE_DIR, PLATFORM_BASE_DIR, SPRITE_CACHE_DIR, ICON_SPRITE_CACHE_DIR,
    SUBSTITUTE_SPRITE_DIR, SUBSTITUTE_SPRITE_URL, STATUS_ABBR, OWNER_ID,
    SHINY, MEGA_BADGE, POKEBALL_EMOJI, _STATUS_BALL_TINT, _CUSTOM_EMOJI_RE,
    _CDN_POW2_SIZES, _BG_SLOT_AVAIL, _PLATFORM_SLOT_AVAIL, _SCENE_FALLBACK,
    _TYPE_TO_SCENE, _WEATHER_TO_SCENES, _TERRAIN_TO_SCENES,
)

_bg_img_cache: dict[str, Image.Image] = {}
_ICON_SPRITE_CACHE: dict[tuple[str, int], Image.Image] = {}
_SUBSTITUTE_SPRITE_CACHE: dict[int, Image.Image] = {}
_BATTLE_SPRITE_CACHE: dict[tuple, Image.Image] = {}
_SPRITE_HTTP_SESSION: ClientSession | None = None


def _get_font(style: str, size: int) -> ImageFont.ImageFont:
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
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


async def _sprite_session() -> ClientSession:
    global _SPRITE_HTTP_SESSION
    if _SPRITE_HTTP_SESSION is None or _SPRITE_HTTP_SESSION.closed:
        connector = TCPConnector(limit=50, limit_per_host=10, ttl_dns_cache=300)
        _SPRITE_HTTP_SESSION = ClientSession(connector=connector)
    return _SPRITE_HTTP_SESSION


def _sprite_url(species_id: int, shiny: bool, gender: str = "genderless",
                 form: dict | None = None, back: bool = False) -> str:
    """ASSUMES a Pokedex lookup importable as
    `pokemon.dex.pokedex.POKEDEX_BY_ID` (Track B's data) -- see
    NOTES_FOR_TRACK_C.md-adjacent note at the bottom of this file for the
    UI-side equivalent of that assumptions list."""
    base = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"
    key = ("b_shiny" if shiny else "b") if back else ("shiny" if shiny else "sprite")
    fem_key = "f" if key == "sprite" else f"{key}_f"
    if form:
        if gender == "female" and (url := form.get(fem_key)):
            return f"{base}/{url}"
        if url := form.get(key):
            return f"{base}/{url}"
    from .dex_lookup import pokedex_entry  # thin wrapper, see bottom of file
    sp = pokedex_entry(species_id)
    if gender == "female" and (url := sp.get(fem_key)):
        return f"{base}/{url}"
    if url := sp.get(key):
        return f"{base}/{url}"
    return f"{base}/{'back/' if back else ''}{'shiny/' if shiny else ''}{species_id}.png"


# -- background scene selection --------------------------------------------

def _battle_time_slot() -> str:
    h = datetime.now().hour
    if 6 <= h < 16:
        return "day"
    if 16 <= h < 20:
        return "afternoon"
    return "night"


def _pick_bg_scene(weather: str | None, terrain: str | None, type_hint: str | None,
                    slot: str, rng: random.Random | None = None) -> str:
    """Choose a background scene name that exists for the current time
    slot. Picks randomly among several plausible candidates so back-to-back
    wild encounters don't all look identical."""
    rng = rng or random
    if weather in _WEATHER_TO_SCENES:
        candidates = _WEATHER_TO_SCENES[weather]
    elif terrain in _TERRAIN_TO_SCENES:
        candidates = _TERRAIN_TO_SCENES[terrain]
    else:
        candidates = _TYPE_TO_SCENE.get(type_hint or "", ["grass"])

    scene = rng.choice(candidates)
    avail = _BG_SLOT_AVAIL[slot]
    seen: set[str] = set()
    while scene not in avail:
        if scene in seen:
            scene = "grass"
            break
        seen.add(scene)
        scene = _SCENE_FALLBACK.get(scene, "grass")
    return scene


def _load_bg_img(slot: str, scene: str) -> Image.Image | None:
    key = f"{slot}/{scene}"
    if key in _bg_img_cache:
        return _bg_img_cache[key]
    path = (os.path.join(BG_BASE_DIR, f"{scene}.png") if slot == "duel"
            else os.path.join(BG_BASE_DIR, slot, f"{scene}.png"))
    if os.path.isfile(path):
        try:
            img = Image.open(path).convert("RGBA")
            _bg_img_cache[key] = img
            return img
        except Exception:
            pass
    return None


def _load_platform_img(slot: str, scene: str, back: bool = False) -> Image.Image | None:
    suffix = "_back" if back else ""
    if slot == "duel":
        path = os.path.join(PLATFORM_BASE_DIR, f"{scene}{suffix}.png")
        if os.path.isfile(path):
            try:
                return Image.open(path).convert("RGBA")
            except Exception:
                pass
        return None

    avail = _PLATFORM_SLOT_AVAIL.get(slot, _PLATFORM_SLOT_AVAIL["day"])
    plat = scene
    seen: set[str] = set()
    while plat not in avail:
        if plat in seen:
            plat = "grass"
            break
        seen.add(plat)
        plat = _SCENE_FALLBACK.get(plat, "grass")

    for try_slot in ([slot] if slot == "day" else [slot, "day"]):
        path = os.path.join(PLATFORM_BASE_DIR, try_slot, f"{plat}{suffix}.png")
        if os.path.isfile(path):
            try:
                return Image.open(path).convert("RGBA")
            except Exception:
                pass
    return None


def _composite_platform(img: Image.Image, plat_raw: Image.Image | None,
                         cx: int, cy_center: int, target_w: int,
                         bottom_align: bool = False, bottom_y: int = 0) -> None:
    """Composite a (possibly clipped) platform image onto the battle canvas."""
    if plat_raw is None:
        return
    pw = max(1, target_w)
    ph = max(1, int(pw * plat_raw.height / max(1, plat_raw.width)))
    plat = plat_raw.resize((pw, ph), Image.NEAREST)

    px = cx - pw // 2
    py = (bottom_y - ph) if bottom_align else (cy_center - ph // 2)

    iw, ih = img.size
    sx0, sy0 = max(0, -px), max(0, -py)
    sx1, sy1 = min(pw, iw - px), min(ph, ih - py)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    img.alpha_composite(plat.crop((sx0, sy0, sx1, sy1)), (px + sx0, py + sy0))


def _build_battle_bg(
    w: int, h: int, weather: str | None = None, terrain: str | None = None,
    type_hint: str | None = None, trainer_ids: list[int] | None = None,
    duel_bg_idx: int | None = None, bg_seed: int | None = None,
) -> tuple[Image.Image, str, str]:
    """Return (image, slot, scene). slot == 'duel' for trainer battles."""
    slot = _battle_time_slot()
    scene_rng = random.Random(bg_seed) if bg_seed is not None else random

    if trainer_ids is not None:
        if OWNER_ID in trainer_ids:
            scene = "alfred"
        else:
            n = duel_bg_idx or random.randint(1, 8)
            scene = f"fight{n}"
        raw = _load_bg_img("duel", scene)
        img = (raw.resize((w, h), Image.NEAREST) if raw else Image.new("RGBA", (w, h), (60, 60, 60, 255)))
        return img, "duel", scene

    scene = _pick_bg_scene(weather, terrain, type_hint, slot, rng=scene_rng)
    raw = _load_bg_img(slot, scene)
    img = (raw.resize((w, h), Image.NEAREST) if raw else Image.new("RGBA", (w, h), (80, 120, 80, 255)))

    draw = ImageDraw.Draw(img, "RGBA")
    rng = random.Random()
    if weather in ("rain", "heavy_rain"):
        for _ in range(90 if weather == "heavy_rain" else 45):
            rx, ry = rng.randint(0, w), rng.randint(0, h)
            draw.line([(rx, ry), (rx - 2, ry + 9)], fill=(150, 178, 230, 148), width=1)
    elif weather == "sand":
        hz = int(h * 0.56)
        for _ in range(35):
            rx = rng.randint(0, w); ry = rng.randint(hz // 2, h)
            draw.line([(rx, ry), (rx + rng.randint(9, 18), ry)], fill=(212, 162, 62, 128), width=1)
    elif weather == "hail":
        for _ in range(28):
            rx, ry = rng.randint(0, w), rng.randint(0, h * 3 // 4)
            r2 = rng.randint(2, 4)
            draw.ellipse([rx - r2, ry - r2, rx + r2, ry + r2], fill=(228, 245, 255, 192))

    tint = {"electric": (240, 220, 50, 26), "grassy": (50, 200, 80, 26),
            "misty": (220, 160, 210, 26), "psychic": (180, 50, 200, 26)}.get(terrain or "")
    if tint:
        img = Image.alpha_composite(img, Image.new("RGBA", (w, h), tint))

    return img, slot, scene


# -- badges / icons ----------------------------------------------------------

def _emoji_ref(emoji_ref) -> tuple[str, bool] | None:
    if isinstance(emoji_ref, PartialEmoji):
        return (str(emoji_ref.id), bool(emoji_ref.animated)) if emoji_ref.id else None
    if isinstance(emoji_ref, str):
        m = _CUSTOM_EMOJI_RE.match(emoji_ref)
        if not m:
            return None
        return (m.group(2), emoji_ref.startswith("<a:"))
    return None


def _cdn_pow2_size(size: int) -> int:
    size = max(64, min(4096, size))
    for v in _CDN_POW2_SIZES:
        if v >= size:
            return v
    return 4096


def _load_or_process_icon_sync(disk_path: str, size: int, raw_bytes: bytes | None) -> Image.Image | None:
    if os.path.isfile(disk_path):
        try:
            return Image.open(disk_path).convert("RGBA")
        except Exception:
            pass
    if raw_bytes is None:
        return None
    try:
        img = Image.open(BytesIO(raw_bytes)).convert("RGBA")
    except Exception:
        return None
    img = img.resize((size, size), Image.LANCZOS)
    try:
        os.makedirs(os.path.dirname(disk_path), exist_ok=True)
        img.save(disk_path)
    except Exception:
        pass
    return img


async def _get_icon_img(emoji_ref, size: int) -> Image.Image | None:
    parsed = _emoji_ref(emoji_ref)
    if not parsed:
        return None
    emoji_id, animated = parsed
    key = (emoji_id, size)
    cached = _ICON_SPRITE_CACHE.get(key)
    if cached is not None:
        return cached

    disk_path = f"{ICON_SPRITE_CACHE_DIR}/{emoji_id}_{size}.png"
    img = await asyncio.to_thread(_load_or_process_icon_sync, disk_path, size, None)
    if img is not None:
        _ICON_SPRITE_CACHE[key] = img
        return img

    ext = "gif" if animated else "png"
    url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}?size={_cdn_pow2_size(size)}"
    raw_bytes = None
    try:
        sess = await _sprite_session()
        async with sess.get(url, timeout=ClientTimeout(total=4)) as r:
            if r.status == 200:
                raw_bytes = await r.read()
    except Exception:
        raw_bytes = None
    if raw_bytes is None:
        return None

    img = await asyncio.to_thread(_load_or_process_icon_sync, disk_path, size, raw_bytes)
    if img is not None:
        _ICON_SPRITE_CACHE[key] = img
    return img


async def _gather_hp_panel_badges(shiny: bool, mega_evolved: bool, size: int = 16) -> list[Image.Image]:
    tasks = []
    if shiny:
        tasks.append(_get_icon_img(SHINY, size))
    if mega_evolved:
        tasks.append(_get_icon_img(MEGA_BADGE, size))
    if not tasks:
        return []
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


# -- sprites -----------------------------------------------------------------

def _process_substitute_sprite_sync(disk_path: str, target_w: int, raw_bytes: bytes | None) -> Image.Image | None:
    if os.path.isfile(disk_path):
        try:
            img = Image.open(disk_path).convert("RGBA")
            if img.getbbox() is not None:
                return img
        except Exception:
            pass
        try:
            os.remove(disk_path)
        except Exception:
            pass

    if raw_bytes is None:
        return None
    try:
        img = Image.open(BytesIO(raw_bytes)).convert("RGBA")
    except Exception:
        return None

    bbox = img.getbbox()
    if not bbox:
        return None
    img = img.crop(bbox)
    w, h = img.size
    new_h = max(1, int(target_w * h / max(1, w)))
    img = img.resize((target_w, new_h), Image.LANCZOS)
    try:
        os.makedirs(os.path.dirname(disk_path), exist_ok=True)
        img.save(disk_path)
    except Exception:
        pass
    return img


async def _get_substitute_sprite(target_w: int) -> Image.Image | None:
    cached = _SUBSTITUTE_SPRITE_CACHE.get(target_w)
    if cached is not None:
        return cached

    disk_path = f"{SUBSTITUTE_SPRITE_DIR}/substitute_{target_w}.png"
    img = await asyncio.to_thread(_process_substitute_sprite_sync, disk_path, target_w, None)
    if img is not None:
        _SUBSTITUTE_SPRITE_CACHE[target_w] = img
        return img

    raw_bytes = None
    try:
        sess = await _sprite_session()
        async with sess.get(SUBSTITUTE_SPRITE_URL, timeout=ClientTimeout(total=6)) as r:
            if r.status == 200:
                raw_bytes = await r.read()
    except Exception:
        raw_bytes = None
    if raw_bytes is None:
        return None

    img = await asyncio.to_thread(_process_substitute_sprite_sync, disk_path, target_w, raw_bytes)
    if img is not None:
        _SUBSTITUTE_SPRITE_CACHE[target_w] = img
    return img


def _process_battle_sprite_sync(disk_path: str, size: int, raw_bytes: bytes | None) -> Image.Image | None:
    if os.path.isfile(disk_path):
        try:
            img = Image.open(disk_path).convert("RGBA")
            if img.getbbox() is not None:
                return img
        except Exception:
            pass
        try:
            os.remove(disk_path)
        except Exception:
            pass

    if raw_bytes is None:
        return None
    try:
        img = Image.open(BytesIO(raw_bytes)).convert("RGBA")
    except Exception:
        return None

    img = img.resize((size, size), Image.NEAREST)
    bbox = img.getbbox()
    if not bbox:
        return None
    img = img.crop(bbox)
    try:
        os.makedirs(os.path.dirname(disk_path), exist_ok=True)
        img.save(disk_path)
    except Exception:
        pass
    return img


async def _get_battle_sprite(species_id: int, shiny: bool, size: int,
                              gender: str = "genderless", form: dict | None = None,
                              back: bool = False) -> Image.Image:
    form_key = form.get("slug") if form else None
    key = (species_id, shiny, size, gender, form_key, back)
    cached = _BATTLE_SPRITE_CACHE.get(key)
    if cached is not None:
        if cached.getbbox() is not None:
            return cached
        del _BATTLE_SPRITE_CACHE[key]

    suffix = f"_{form_key}" if form_key else ""
    backsuf = "_back" if back else ""
    disk_path = f"{SPRITE_CACHE_DIR}/{species_id}{suffix}{backsuf}_{gender}_{int(shiny)}_{size}.png"

    img = await asyncio.to_thread(_process_battle_sprite_sync, disk_path, size, None)
    if img is not None:
        _BATTLE_SPRITE_CACHE[key] = img
        return img

    raw_bytes = None
    for _ in range(2):
        try:
            sess = await _sprite_session()
            async with sess.get(_sprite_url(species_id, shiny, gender, form, back=back),
                                 timeout=ClientTimeout(total=8)) as r:
                if r.status == 200:
                    raw_bytes = await r.read()
                    break
        except Exception:
            raw_bytes = None

    if raw_bytes is None:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))

    img = await asyncio.to_thread(_process_battle_sprite_sync, disk_path, size, raw_bytes)
    if img is None:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    _BATTLE_SPRITE_CACHE[key] = img
    return img


# -- HP panels / party balls / substitute doll -------------------------------

def _hp_color(pct: float) -> tuple[int, int, int]:
    if pct > 0.5:
        return (96, 200, 96)
    if pct > 0.2:
        return (232, 196, 56)
    return (220, 76, 76)


def _draw_party_balls(img: Image.Image, x: int, y: int, party: list[dict] | None, align: str = "left") -> None:
    """Draws a row of small Poké Ball icons representing a trainer's full
    roster: filled (alive), tinted by status condition, greyed-out with an
    X (fainted), or ringed in yellow (currently active). `party` is None to
    skip entirely (wild encounters have no roster)."""
    if not party:
        return
    draw = ImageDraw.Draw(img, "RGBA")
    n = len(party)
    r = max(4, min(8, 80 // max(1, n)))
    gap = 4
    total_w = n * (r * 2) + max(0, n - 1) * gap
    sx = x if align == "left" else x - total_w
    for i, info in enumerate(party):
        cx = sx + r + i * (r * 2 + gap)
        cy = y
        if info.get("fainted"):
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(85, 85, 90, 255), outline=(35, 35, 38, 255), width=1)
            o = max(2, r - 4)
            draw.line([cx - o, cy - o, cx + o, cy + o], fill=(225, 225, 228, 255), width=2)
            draw.line([cx - o, cy + o, cx + o, cy - o], fill=(225, 225, 228, 255), width=2)
        else:
            top_col = _STATUS_BALL_TINT.get(info.get("status"), (214, 64, 58))
            draw.pieslice([cx - r, cy - r, cx + r, cy + r], 180, 360, fill=top_col + (255,))
            draw.pieslice([cx - r, cy - r, cx + r, cy + r], 0, 180, fill=(248, 248, 246, 255))
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(20, 20, 20, 255), width=1)
            draw.line([cx - r, cy, cx + r, cy], fill=(20, 20, 20, 255), width=1)
            ir = max(2, r // 3)
            draw.ellipse([cx - ir, cy - ir, cx + ir, cy + ir], fill=(248, 248, 246, 255), outline=(20, 20, 20, 255), width=1)
        if info.get("active"):
            draw.ellipse([cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3], outline=(255, 225, 70, 230), width=2)


def _draw_caught_ball_sync(img: Image.Image, cx: int, ground_y: int, ball_type: str,
                            scale: int, ball_img: Image.Image | None) -> None:
    size = max(40, min(96, scale // 4))
    draw = ImageDraw.Draw(img, "RGBA")
    cy = ground_y - size // 2 - 6
    if ball_img is not None:
        if ball_img.size != (size, size):
            ball_img = ball_img.resize((size, size), Image.LANCZOS)
        img.alpha_composite(ball_img, (cx - size // 2, cy - size // 2))
        return

    r = size // 2
    top_col = (214, 64, 58)
    draw.pieslice([cx - r, cy - r, cx + r, cy + r], 180, 360, fill=top_col + (255,))
    draw.pieslice([cx - r, cy - r, cx + r, cy + r], 0, 180, fill=(248, 248, 246, 255))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(20, 20, 20, 255), width=max(2, r // 14))
    draw.line([cx - r, cy, cx + r, cy], fill=(20, 20, 20, 255), width=max(2, r // 14))
    ir = max(6, r // 3)
    draw.ellipse([cx - ir, cy - ir, cx + ir, cy + ir], fill=(248, 248, 246, 255), outline=(20, 20, 20, 255), width=max(2, r // 16))
    ir2 = max(3, r // 7)
    draw.ellipse([cx - ir2, cy - ir2, cx + ir2, cy + ir2], fill=(225, 225, 228, 255), outline=(20, 20, 20, 255), width=1)


def _draw_hp_panel_sync(
    img: Image.Image, x: int, y: int, panel_w: int, name: str, level: int, hp: int, max_hp: int,
    status: str | None, gender: str, is_player: bool, f_name, f_small, f_smaller,
    badge_imgs: list[Image.Image] | None = None,
) -> None:
    badge_imgs = badge_imgs or []
    PAD = 8
    PANEL_H = 70 if is_player else 56
    BADGE_SZ = 16

    shadow = Image.new("RGBA", (panel_w + 4, PANEL_H + 4), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([3, 3, panel_w + 1, PANEL_H + 1], radius=8, fill=(0, 0, 0, 90))
    img.alpha_composite(shadow, (x - 1, y - 1))

    panel = Image.new("RGBA", (panel_w, PANEL_H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle([0, 0, panel_w - 1, PANEL_H - 1], radius=8, fill=(246, 244, 238, 240), outline=(44, 42, 36, 255), width=2)

    lv_str = f"Lv.{level}"
    lv_w = int(pd.textlength(lv_str, font=f_name))

    status_reserve = 0
    if status:
        abbr = STATUS_ABBR.get(status, status[:3].upper())
        status_reserve = int(pd.textlength(abbr, font=f_small)) + 16

    badge_reserve = sum(b.width + 3 for b in badge_imgs)

    max_nm = panel_w - PAD * 2 - lv_w - status_reserve - badge_reserve - 30
    nm = name
    while len(nm) > 1 and pd.textlength(nm, font=f_name) > max_nm:
        nm = nm[:-1]
    if nm != name:
        nm = nm.rstrip() + "."
    pd.text((PAD, 6), nm, fill=(28, 26, 22), font=f_name)

    nm_w = int(pd.textlength(nm, font=f_name))
    cursor_x = PAD + nm_w + 3
    if gender == "male":
        pd.text((cursor_x, 6), "\u2642", fill=(65, 105, 210), font=f_name)
        cursor_x += int(pd.textlength("\u2642", font=f_name)) + 3
    elif gender == "female":
        pd.text((cursor_x, 6), "\u2640", fill=(210, 60, 110), font=f_name)
        cursor_x += int(pd.textlength("\u2640", font=f_name)) + 3

    badge_y = 6 + max(0, (f_name.size - BADGE_SZ) // 2)
    for bi in badge_imgs:
        panel.alpha_composite(bi, (cursor_x, badge_y))
        cursor_x += bi.width + 3

    pd.text((panel_w - PAD - lv_w, 6), lv_str, fill=(60, 54, 46), font=f_name)

    if status:
        STATUS_BG = {
            "burn": (215, 74, 28), "paralysis": (195, 175, 8), "sleep": (95, 95, 125),
            "freeze": (72, 155, 215), "poison": (145, 45, 175), "bad_poison": (125, 25, 155),
        }
        sw = int(pd.textlength(abbr, font=f_smaller)) + 8
        sx = panel_w - PAD - lv_w - sw - 8
        pd.rounded_rectangle([sx, 7, sx + sw, 22], radius=3, fill=STATUS_BG.get(status, (155, 55, 55)))
        pd.text((sx + 4, 6), abbr, fill=(255, 255, 255), font=f_smaller)

    bar_y = 30
    bar_h = 12
    bx1 = PAD + 26
    bx2 = panel_w - PAD
    bw = bx2 - bx1
    pct = max(0.0, min(1.0, hp / max_hp if max_hp else 0.0))
    fw = int(bw * pct)

    pd.text((PAD - 1, bar_y - 4), "HP", fill=(60, 54, 46), font=f_small)
    pd.rounded_rectangle([bx1, bar_y, bx2, bar_y + bar_h], radius=5, fill=(52, 48, 42))
    if fw > 3:
        col = _hp_color(pct)
        pd.rounded_rectangle([bx1, bar_y, bx1 + fw, bar_y + bar_h], radius=5, fill=col)
        hl = tuple(min(255, c + 55) for c in col)
        pd.rectangle([bx1 + 2, bar_y + 2, bx1 + fw - 2, bar_y + 4], fill=hl)

    if is_player:
        hp_str = f"{hp}/{max_hp}"
        hw = int(pd.textlength(hp_str, font=f_small))
        pd.text((panel_w - PAD - hw, bar_y + bar_h + 5), hp_str, fill=(56, 50, 42), font=f_small)

    img.alpha_composite(panel, (x, y))


def _draw_substitute_doll(img: Image.Image, cx: int, ground_y: int, scale: int, sub_img: Image.Image | None = None) -> None:
    w = max(28, int(scale * 0.42))
    draw = ImageDraw.Draw(img, "RGBA")
    shadow_r = int(w * 0.55)
    draw.ellipse([cx - shadow_r, ground_y - 6, cx + shadow_r, ground_y + 8], fill=(0, 0, 0, 90))

    if sub_img is not None:
        sw, sh = sub_img.size
        img.alpha_composite(sub_img, (cx - sw // 2, ground_y - sh))
        return

    h = int(w * 1.3)
    x0, y0 = cx - w // 2, ground_y - h
    x1, y1 = cx + w // 2, ground_y
    body_col = (199, 161, 100, 255)
    outline_col = (108, 78, 42, 255)
    head_r = int(w * 0.34)

    draw.rounded_rectangle([x0, y0 + head_r, x1, y1], radius=int(w * 0.18), fill=body_col, outline=outline_col, width=2)
    draw.ellipse([cx - head_r, y0, cx + head_r, y0 + head_r * 2], fill=body_col, outline=outline_col, width=2)

    eye_off = head_r // 2
    eye_y = y0 + head_r
    for ox in (-eye_off, eye_off):
        draw.line([cx + ox - 3, eye_y - 3, cx + ox + 3, eye_y + 3], fill=outline_col, width=2)
        draw.line([cx + ox - 3, eye_y + 3, cx + ox + 3, eye_y - 3], fill=outline_col, width=2)

    stitch_y = y0 + int(h * 0.62)
    step = max(7, w // 4)
    for sx in range(int(x0 + step * 0.5), int(x1 - step * 0.3), step):
        draw.line([sx, stitch_y, sx + 5, stitch_y + 9], fill=outline_col, width=1)


# -- top-level entry point ----------------------------------------------------

async def render_battle_png(
    my_slots: list[dict | None], opp_slots: list[dict | None],
    weather: str | None = None, terrain: str | None = None,
    width: int = 720, height: int = 460, type_hint: str | None = None,
    my_party: list[dict] | None = None, opp_party: list[dict] | None = None,
    trainer_ids: list[int] | None = None, duel_bg_idx: int | None = None,
    bg_seed: int | None = None,
) -> File:
    opp_sz, my_sz = 288, 384

    async def _fetch_assets(slot_data: dict | None, size: int, back: bool):
        if slot_data is None:
            return None
        if slot_data.get("caught_ball"):
            return (await _get_icon_img(POKEBALL_EMOJI.get(slot_data["caught_ball"]), 96),)
        sprite = await _get_battle_sprite(
            slot_data.get("render_species_id", slot_data["species_id"]),
            slot_data["shiny"], size, slot_data.get("gender", "genderless"),
            slot_data.get("render_form", slot_data.get("form")), back=back)
        badges = await _gather_hp_panel_badges(slot_data.get("shiny", False), slot_data.get("mega_evolved", False))
        return (sprite, badges)

    opp_assets = await asyncio.gather(*(_fetch_assets(s, opp_sz, False) for s in opp_slots))
    my_assets = await asyncio.gather(*(_fetch_assets(s, my_sz, True) for s in my_slots))

    needs_opp_sub = any(s and s.get("substitute_active") for s in opp_slots)
    needs_my_sub = any(s and s.get("substitute_active") for s in my_slots)
    opp_sub_img = await _get_substitute_sprite(max(28, int(opp_sz * 0.42))) if needs_opp_sub else None
    my_sub_img = await _get_substitute_sprite(max(28, int(my_sz * 0.42))) if needs_my_sub else None

    return await asyncio.to_thread(
        _compose_battle_png_sync, my_slots, opp_slots, my_assets, opp_assets,
        my_party, opp_party, trainer_ids, weather, terrain, width, height,
        type_hint, duel_bg_idx, bg_seed, my_sub_img, opp_sub_img,
    )


def _compose_battle_png_sync(
    my_slots: list[dict | None], opp_slots: list[dict | None], my_assets: list[tuple], opp_assets: list[tuple],
    my_party: list[dict], opp_party: list[dict], trainer_ids: list[int],
    weather: str | None = None, terrain: str | None = None, width: int = 720, height: int = 460,
    type_hint: str | None = None, duel_bg_idx: int | None = None, bg_seed: int | None = None,
    my_sub_img: Image.Image | None = None, opp_sub_img: Image.Image | None = None,
) -> File:
    img, bg_slot, scene = _build_battle_bg(width, height, weather, terrain, type_hint, trainer_ids, duel_bg_idx, bg_seed=bg_seed)

    f_name = _get_font("bold", 18)
    f_small = _get_font("reg", 18)
    f_smaller = _get_font("reg", 16)

    PANEL_W = 250
    P_PANEL_H = 70
    O_PANEL_H = 56
    horizon = int(height * 0.65)

    n_opp = sum(1 for s in opp_slots if s)
    n_my = sum(1 for s in my_slots if s)
    opp_sz = 288
    my_sz = 384

    opp_cx = [(int(width * 0.70), 0)] if n_opp == 1 else [(int(width * 0.45), -16), (int(width * 0.815), 0)]
    my_cx = [(int(width * 0.255), 0)] if n_my == 1 else [(int(width * 0.08), -8), (int(width * 0.43), 25)]

    plat_front = _load_platform_img(bg_slot, scene, back=False)
    plat_back = _load_platform_img(bg_slot, scene, back=True)
    opp_plat_w = 270

    for cx, oy in opp_cx[:n_opp]:
        _composite_platform(img, plat_front, cx, horizon - 34 + oy, opp_plat_w)

    _composite_platform(img, plat_back, int(width * 0.255), 0, 390, bottom_align=True, bottom_y=height)

    for i, (slot_data, (cx, oy)) in enumerate(zip(opp_slots, opp_cx)):
        if slot_data is None:
            continue
        ball_caught = slot_data.get("caught_ball")
        if ball_caught:
            _draw_caught_ball_sync(img, cx, horizon - 34, ball_caught, opp_sz, opp_assets[i][0])
            continue
        sprite = opp_assets[i][0]
        hp = slot_data.get("hp", 0)
        if hp > 0 or trainer_ids is None:
            if slot_data.get("hp") <= 0:
                r, g, b, a = sprite.split()
                lum = Image.merge("RGB", (r, g, b)).convert("L")
                sprite = Image.merge("RGBA", (lum, lum, lum, a))
            if slot_data.get("substitute_active"):
                r, g, b, a = sprite.split()
                sprite = Image.merge("RGBA", (r, g, b, a.point(lambda v: v // 2)))
            img.alpha_composite(sprite, (cx - sprite.width // 2, horizon - 22 - sprite.height + oy))
            if slot_data.get("substitute_active"):
                _draw_substitute_doll(img, cx, horizon - 22 + oy, opp_sz, opp_sub_img)
        _draw_hp_panel_sync(
            img, 14, 14 + i * (O_PANEL_H + 8), PANEL_W,
            slot_data["name"], slot_data["level"], slot_data["hp"], slot_data["max_hp"],
            slot_data["status"], slot_data.get("gender", "genderless"), False,
            f_name, f_small, f_smaller, badge_imgs=opp_assets[i][1])

    if opp_party:
        opp_bottom = 24 + n_opp * (O_PANEL_H + 8)
        _draw_party_balls(img, 16, opp_bottom, opp_party, align="left")

    for i, (slot_data, (cx, oy)) in enumerate(zip(my_slots, my_cx)):
        if slot_data is None:
            continue
        sprite = my_assets[i][0]
        if slot_data.get("hp") <= 0:
            r, g, b, a = sprite.split()
            lum = Image.merge("RGB", (r, g, b)).convert("L")
            sprite = Image.merge("RGBA", (lum, lum, lum, a))
        if slot_data.get("substitute_active"):
            r, g, b, a = sprite.split()
            sprite = Image.merge("RGBA", (r, g, b, a.point(lambda v: v // 2)))
        sprite_top = height - int(sprite.height * 0.95) + oy
        if slot_data.get("substitute_active"):
            _draw_substitute_doll(img, cx, sprite_top + sprite.height, my_sz, my_sub_img)
        img.alpha_composite(sprite, (cx - sprite.width // 2, sprite_top))
        _draw_hp_panel_sync(
            img, width - PANEL_W - 25, height - P_PANEL_H - 14 - i * (P_PANEL_H + 8), PANEL_W,
            slot_data["name"], slot_data["level"], slot_data["hp"], slot_data["max_hp"],
            slot_data["status"], slot_data.get("gender", "genderless"), True,
            f_name, f_small, f_smaller, badge_imgs=my_assets[i][1])

    if my_party:
        top_y = height - P_PANEL_H - 14 - max(0, n_my - 1) * (P_PANEL_H + 8) - 16
        _draw_party_balls(img, width - PANEL_W - 25, top_y, my_party, align="left")

    parts = []
    if weather:
        parts.append(weather.replace("_", " ").title())
    if terrain:
        parts.append(terrain.title() + " Terrain")
    if parts:
        lbl = " \u00b7 ".join(parts)
        draw = ImageDraw.Draw(img, "RGBA")
        f_tiny = _get_font("reg", 11)
        tw = int(draw.textlength(lbl, font=f_tiny))
        draw.rounded_rectangle([6, height - 22, tw + 14, height - 6], radius=4, fill=(0, 0, 0, 140))
        draw.text((10, height - 21), lbl, fill=(255, 255, 255, 220), font=f_tiny)

    buf = BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    buf.seek(0)
    return File(buf, filename="battle.png")

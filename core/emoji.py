"""Markdown escaping and deterministic colour hashing.

`descape` and `custom_hash`/`custom_hash_string` were each duplicated by
feel across the monolith's embed-heavy commands (family trees, quotes,
achievements, tags, polls, truth-or-dare...); every one of those now
imports from here instead.
"""
from __future__ import annotations


def descape(name: str) -> str:
    """Escapes markdown control characters and neuters @everyone/@here/@user
    pings, for safely interpolating arbitrary user-provided text (usernames,
    tag content, proxy names, ...) into a message Contore sends itself."""
    return (name.replace("\\", "\\\\")
                .replace("*", "\\*")
                .replace("`", "\\`")
                .replace("-", "\\-")
                .replace("_", "\\_")
                .replace("|", "\\|")
                .replace("~", "\\~")
                .replace("#", "\\#")
                .replace("[", "\\[")
                .replace("@everyone", "@\u200beveryone")
                .replace("@here", "@\u200bhere")
                .replace("<@", "\\<@"))


def custom_hash(user_id: int) -> int:
    """Deterministically maps a snowflake ID to a pastel RGB colour packed
    as a 24-bit int, suitable for `discord.Embed(color=...)`. The same ID
    always produces the same colour, so a user's embeds are visually
    consistent across commands/sessions without storing anything."""
    x = (user_id + 0x8b9d2434e465e150) & 0xFFFFFFFFFFFFFFFF
    x ^= (x >> 33)
    x *= 0xff51afd7ed558ccd
    x &= 0xFFFFFFFFFFFFFFFF
    x ^= (x >> 33)

    hue = x % 360
    saturation = 0.65
    lightness = 0.85

    def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
        c = (1 - abs(2 * l - 1)) * s
        h_prime = h / 60
        xx = c * (1 - abs(h_prime % 2 - 1))

        if 0 <= h_prime < 1:
            r1, g1, b1 = c, xx, 0
        elif 1 <= h_prime < 2:
            r1, g1, b1 = xx, c, 0
        elif 2 <= h_prime < 3:
            r1, g1, b1 = 0, c, xx
        elif 3 <= h_prime < 4:
            r1, g1, b1 = 0, xx, c
        elif 4 <= h_prime < 5:
            r1, g1, b1 = xx, 0, c
        else:
            r1, g1, b1 = c, 0, xx

        m = l - c / 2
        return int((r1 + m) * 255), int((g1 + m) * 255), int((b1 + m) * 255)

    r, g, b = _hsl_to_rgb(hue, saturation, lightness)
    return (r << 16) | (g << 8) | b


def custom_hash_string(text: str) -> int:
    """`custom_hash`, but keyed off an arbitrary string instead of a
    snowflake (e.g. a proxy name, which has no numeric ID of its own)."""
    x = 0
    for char in text:
        x = (x * 69 + ord(char) * 0x69696969) & 0xFFFFFFFF
    return custom_hash(x)

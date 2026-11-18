"""Dice notation engine (`2d20+5`, `4d6kh3`, `6(4d6dl1)`, advantage/
disadvantage suffixes, ...). Pure logic, no Discord objects -- the
`/roll` command and the passive `d20`-style message trigger both live in
`cogs/misc.py` and call into this.
"""
from __future__ import annotations
import random
import re

MAX_DICE_PER_TERM = 1000
MAX_DIE_SIDES = 100000
MAX_TOTAL_DICE = 20000
MAX_GROUP_REPEATS = 100

_GROUP_RE = re.compile(r"^\s*(\d{1,3})\s*\((.+)\)\s*(kh\d+|kl\d+|dh\d+|dl\d+)?\s*$", re.IGNORECASE | re.DOTALL)
_DICE_TOKEN_RE = re.compile(r"([+-]?)(\d*)d(\d+)(kh\d+|kl\d+|dh\d+|dl\d+)?|([+-]?\d+)(?!d)", re.IGNORECASE)
_FULL_EXPR_RE = re.compile(
    r"^[+-]?(?:\d*d\d+(?:kh\d+|kl\d+|dh\d+|dl\d+)?|\d+)"
    r"(?:[+-](?:\d*d\d+(?:kh\d+|kl\d+|dh\d+|dl\d+)?|\d+))*$",
    re.IGNORECASE,
)
_ADV_SUFFIX_RE = re.compile(r"-\s*([ad])\s*$", re.IGNORECASE)

# Simple `d20`/`2d6-a` etc. embedded naturally in chat messages (as opposed
# to an explicit `/roll` invocation), handled passively in on_message.
DICE_MATCH = r"^(\d+|)([dD])(\d+)( |)(-[aAdD]|)$"


class DiceParseError(Exception):
    pass


def _roll_dice_term(count: int, sides: int, keep: str | None) -> tuple[list[int], int]:
    if count <= 0 or count > MAX_DICE_PER_TERM:
        raise DiceParseError(f"Dice count per term must be between 1 and {MAX_DICE_PER_TERM}.")
    if sides <= 0 or sides > MAX_DIE_SIDES:
        raise DiceParseError(f"Die sides must be between 1 and {MAX_DIE_SIDES:,}.")

    rolls = [random.randint(1, sides) for _ in range(count)]
    kept = rolls

    if keep:
        kind = keep[:2].lower()
        n = max(1, min(int(keep[2:]), count))
        ordered = sorted(range(count), key=lambda i: rolls[i], reverse=(kind in ("kh", "dh")))
        if kind in ("kh", "kl"):
            keep_idx = set(ordered[:n])
        else:
            drop_idx = set(ordered[:n])
            keep_idx = {i for i in range(count) if i not in drop_idx}
        kept = [rolls[i] for i in keep_idx]

    return rolls, sum(kept)


def _roll_expression_once(expr: str) -> tuple[int, list[str]]:
    if not _FULL_EXPR_RE.fullmatch(expr):
        raise DiceParseError("Couldn't parse that. Try something like `2d20+5`, `4d6kh3`, or `1d20-a`.")

    total = 0
    detail_lines: list[str] = []
    total_dice_used = 0

    for m in _DICE_TOKEN_RE.finditer(expr):
        if m.group(3):
            sign = -1 if m.group(1) == "-" else 1
            count = int(m.group(2)) if m.group(2) else 1
            sides = int(m.group(3))
            keep = m.group(4)

            total_dice_used += count
            if total_dice_used > MAX_TOTAL_DICE:
                raise DiceParseError(f"Too many dice. Max {MAX_TOTAL_DICE} across the whole expression.")

            rolls, kept_sum = _roll_dice_term(count, sides, keep)
            total += sign * kept_sum

            roll_str = ", ".join(str(r) for r in rolls)
            keep_note = f" ({keep})" if keep else ""
            sign_str = "−" if sign < 0 else ""
            detail_lines.append(f"{sign_str}{count}d{sides}{keep_note}: [{roll_str}] = {kept_sum}")
        else:
            val = int(m.group(5))
            total += val
            detail_lines.append(f"{'+' if val >= 0 else ''}{val}")

    if not detail_lines:
        raise DiceParseError("Couldn't find any dice terms in that expression.")

    return total, detail_lines


def _roll_expression_with_adv(expr: str, adv_mode: str | None) -> tuple[int, list[str]]:
    """Rolls a single dice expression once, or twice-and-picks for
    advantage/disadvantage. Shared by both a top-level roll and each
    repetition inside an N(...) group, so advantage/disadvantage behaves
    identically whether applied to the whole roll or to just one
    repetition within a batch."""
    if adv_mode is None:
        return _roll_expression_once(expr)

    total_a, details_a = _roll_expression_once(expr)
    total_b, details_b = _roll_expression_once(expr)
    if adv_mode == "a":
        chosen_total, chosen_label, other_total = (
            (total_a, "Roll A", total_b) if total_a >= total_b else (total_b, "Roll B", total_a))
        mode_label = "Advantage (highest kept)"
    else:
        chosen_total, chosen_label, other_total = (
            (total_a, "Roll A", total_b) if total_a <= total_b else (total_b, "Roll B", total_a))
        mode_label = "Disadvantage (lowest kept)"

    combined_details = [
        f"**{mode_label}**",
        f"Roll A: " + " ​ · ​ ".join(details_a) + f" ​ · ​ Total {total_a:,}",
        f"Roll B: " + " ​ · ​ ".join(details_b) + f" ​ · ​ Total {total_b:,}",
        f"Kept **{chosen_label}** ({chosen_total:,} vs {other_total:,})",
    ]
    return chosen_total, combined_details


def roll_dice_group(count_str: str, inner_expr: str, group_keep: str | None = None) -> dict:
    count = int(count_str)
    if count < 1 or count > MAX_GROUP_REPEATS:
        raise DiceParseError(f"Group repeat count must be between 1 and {MAX_GROUP_REPEATS}.")

    inner = inner_expr.strip().lower().replace(" ", "")
    if not inner:
        raise DiceParseError("The group's dice expression is empty.")

    adv_match = _ADV_SUFFIX_RE.search(inner)
    adv_mode = adv_match.group(1).lower() if adv_match else None
    base_expr = inner[:adv_match.start()] if adv_match else inner
    if not base_expr:
        raise DiceParseError("Nothing to roll inside the group before the advantage/disadvantage suffix.")

    total_dice_used = 0
    for m in _DICE_TOKEN_RE.finditer(base_expr):
        if m.group(3):
            total_dice_used += (int(m.group(2)) if m.group(2) else 1) * count * (2 if adv_mode else 1)
    if total_dice_used > MAX_TOTAL_DICE:
        raise DiceParseError(f"Too many dice across the whole group. Max {MAX_TOTAL_DICE}.")

    results = []
    running_sum = 0
    for _ in range(count):
        total, detail = _roll_expression_with_adv(base_expr, adv_mode)
        results.append({"total": total, "detail": detail})
        running_sum += total

    kept_indices = set(range(count))
    if group_keep:
        kind = group_keep[:2].lower()
        n = max(1, min(int(group_keep[2:]), count))
        ordered = sorted(range(count), key=lambda i: results[i]["total"], reverse=(kind in ("kh", "dh")))
        if kind in ("kh", "kl"):
            kept_indices = set(ordered[:n])
        else:
            drop_idx = set(ordered[:n])
            kept_indices = {i for i in range(count) if i not in drop_idx}

    for i, r in enumerate(results):
        r["kept"] = i in kept_indices
    kept_sum = sum(r["total"] for r in results if r["kept"])

    return {"mode": "group", "count": count, "base_expr": base_expr, "adv_mode": adv_mode,
            "results": results, "sum": kept_sum, "raw_sum": running_sum, "group_keep": group_keep}


def roll_dice_expression(raw: str) -> dict:
    stripped = raw.strip()
    group_match = _GROUP_RE.match(stripped)
    if group_match:
        return roll_dice_group(group_match.group(1), group_match.group(2), group_match.group(3))

    expr = stripped.lower().replace(" ", "")
    if not expr:
        raise DiceParseError("Give me something to roll, like `2d20+5`.")
    if len(expr) > 100:
        raise DiceParseError("That expression is too long.")

    adv_match = _ADV_SUFFIX_RE.search(expr)
    adv_mode = adv_match.group(1).lower() if adv_match else None
    base_expr = expr[:adv_match.start()] if adv_match else expr
    if not base_expr:
        raise DiceParseError("Nothing to roll before the advantage/disadvantage suffix.")

    total, details = _roll_expression_with_adv(base_expr, adv_mode)
    return {"mode": "single", "total": total, "rolls_detail": details, "adv_mode": adv_mode}

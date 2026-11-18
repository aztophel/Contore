"""Small standalone utility commands that don't belong to any bigger
feature: the dice-notation roller (`/roll` and the plain-message "2d20"
shorthand), and `/8ball`, which classifies the question with spaCy
(yes/no, rating, would-you-rather, open-ended, or "that wasn't a
question") before picking a themed answer.
"""
from __future__ import annotations
import asyncio
import hashlib
import random
import re
from typing import Any

from discord import Embed, Interaction, Message, app_commands
from discord.app_commands import allowed_contexts, describe
from discord.ext import commands

from core.emoji import custom_hash
from core.nlp import get_nlp
from core.text import format_reply

# region dice
MAX_DICE_PER_TERM = 1000
MAX_DIE_SIDES = 100000
MAX_TOTAL_DICE = 20000
MAX_GROUP_REPEATS = 100

DICE_MATCH = r"^(\d+|)([dD])(\d+)( |)(-[aAdD]|)$"

_GROUP_RE = re.compile(
    r"^\s*(\d{1,3})\s*\((.+)\)\s*(kh\d+|kl\d+|dh\d+|dl\d+)?\s*$",
    re.IGNORECASE | re.DOTALL)


class DiceParseError(Exception):
    pass


_DICE_TOKEN_RE = re.compile(
    r"([+-]?)(\d*)d(\d+)(kh\d+|kl\d+|dh\d+|dl\d+)?|([+-]?\d+)(?!d)", re.IGNORECASE)

_FULL_EXPR_RE = re.compile(
    r"^[+-]?(?:\d*d\d+(?:kh\d+|kl\d+|dh\d+|dl\d+)?|\d+)"
    r"(?:[+-](?:\d*d\d+(?:kh\d+|kl\d+|dh\d+|dl\d+)?|\d+))*$",
    re.IGNORECASE
)

_ADV_SUFFIX_RE = re.compile(r"-\s*([ad])\s*$", re.IGNORECASE)


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
        else:  # dh / dl -> drop highest/lowest, keep the rest
            drop_idx = set(ordered[:n])
            keep_idx = {i for i in range(count) if i not in drop_idx}
        kept = [rolls[i] for i in keep_idx]

    return rolls, sum(kept)


def _roll_expression_once(expr: str) -> tuple[int, list[str]]:
    if not _FULL_EXPR_RE.fullmatch(expr):
        raise DiceParseError(
            "Couldn't parse that. Try something like `2d20+5`, `4d6kh3`, or `1d20-a`.")

    total = 0
    detail_lines: list[str] = []
    total_dice_used = 0

    for m in _DICE_TOKEN_RE.finditer(expr):
        if m.group(3):  # dice term: sign, count, sides, keep
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
        else:  # flat modifier
            val = int(m.group(5))
            total += val
            detail_lines.append(f"{'+' if val >= 0 else ''}{val}")

    if not detail_lines:
        raise DiceParseError("Couldn't find any dice terms in that expression.")

    return total, detail_lines


def _roll_expression_with_adv(expr: str, adv_mode: str | None) -> tuple[int, list[str]]:
    """Rolls a single dice expression once, or twice-and-picks for advantage/disadvantage.
    Shared by both a top-level roll and each repetition inside an N(...) group, so
    advantage/disadvantage behaves identically whether applied to the whole roll or to
    just one repetition within a batch."""
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

    combined_details = [f"**{mode_label}**",
                         "Roll A: " + " ​ · ​ ".join(details_a) + f" ​ · ​ Total {total_a:,}",
                         "Roll B: " + " ​ · ​ ".join(details_b) + f" ​ · ​ Total {total_b:,}",
                         f"Kept **{chosen_label}** ({chosen_total:,} vs {other_total:,})"]
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
        else:  # dh/dl -> drop, keep the rest
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


async def handle_dice(message: Message):
    """The plain-message dice shorthand ("2d20", "d6-a", ...) -- separate
    from, and much simpler than, `/roll`'s full expression grammar. Runs
    even in DMs, and before any guild check, matching the monolith."""
    m = re.search(DICE_MATCH, message.content)
    if not m:
        return

    dice_amount = m.group(1)
    die_sides = m.group(3)
    has_advantage = m.group(5).lower() == "-a"
    has_disadvantage = m.group(5).lower() == "-d"

    if dice_amount == "":
        dice_amount = 1  # allow just saying d6, etc
    dice_amount = int(dice_amount)
    die_sides = int(die_sides)
    if dice_amount <= 0 or die_sides <= 0:  # make sure there's an actual dice being rolled
        return

    string = f"**{message.author.display_name}**, you rolled a"

    total = 0
    if dice_amount > 5000000:
        await message.reply("you cannot roll this many dice at once")
        return

    for i in range(dice_amount):
        num = random.randint(1, die_sides)
        if has_advantage or has_disadvantage:
            adv_roll = random.randint(1, die_sides)
            if adv_roll > num and has_advantage:
                num = adv_roll
            elif adv_roll < num and has_disadvantage:
                num = adv_roll
        if dice_amount > 1 and i == dice_amount - 1:
            string += f" and a **{num:,}**"
        elif dice_amount > 1:
            string += f" **{num:,}**,"
        else:
            string += f" **{num:,}**"
        total += num

    if has_advantage:
        string += " with advantage"
    if has_disadvantage:
        string += " with disadvantage"
    if dice_amount > 1:
        string += f" (total: **{total:,}**)"
    if len(string) > 500:
        await message.channel.send(f"**{message.author.display_name}**, your roll totals to {total:,}")
    else:
        await message.channel.send(string)
# endregion


# region 8ball
semantic_cache: dict[str, Any] = {}
retry_cache: dict[tuple, int] = {}
_8ball_type_cache: dict[str, str] = {}

SIMILARITY_THRESHOLD = 0.95

answers_yes = [
    "It is certain.", "Without a doubt.", "Yes, definitely.",
    "As I see it, yes.", "Most likely.", "Signs point to yes.",
    "Absolutely.", "You may rely on it.", "Outlook good.", "Yes.",
    "It is decidedly so.", "The prophecies foretell... yes.",
    "Fate smiles upon you.", "The stars align in your favor.",
    "Against all odds... yes.", "The cosmos conspire in your favour.",
    "A resounding yes, for what little it's worth.",
    "The fates are laughing. Yes.", "Chances are good."
]

answers_no = [
    "Don't count on it.", "My reply is no.", "My sources say no.",
    "Outlook not so good.", "Very doubtful.",
    "The stars are in disarray. You are cursed.",
    "Regrettably, no.", "The oracle laughs at this question. No.",
    "Not in this lifetime.", "The void whispers: no.",
    "Perhaps. But probably not.", "The fates are laughing. No.",
    "Even I can't save this outcome."
]

answers_rating = [
    "That's a solid 8/10.", "A pathetic 2/10. Do better.",
    "Easy 10/10, absolutely flawless.", "A 5/10, pitifully average.",
    "A clean 0/10.", "I would give it a mere 4/10.",
    "1/10. Why did you expect anything else?",
    "6/10, unfortunately mediocre.", "A generous 7/10.",
    "A disappointing 3/10.",
    "9/10. One point deducted for needing to ask me about such an obvious question.",
    "11/10. Simply off the charts.",
    "-4/10. Unprecedented failure.",
    "Somewhere between a 1 and a 4. Closer to a 2.",
    "A solid 6.5/10, which rounds down to a 6.",
    "Zero. The lowest possible non-imaginary score.",
    "An impressive 10/10, and I do not say that lightly.",
    "3/10, and I'm being generous."
]

answers_open_ended = [
    "The answers you seek are not meant for mortals.",
    "Because fate demanded it so.", "Only time will tell.",
    "Thy question is not for the likes of me to answer.",
    "Perhaps the real answer was the friends you made along the way.",
    "The universe offers no satisfying reply.",
    "When the stars realign, you'll understand.",
    "That is a question best left unasked.",
    "Even omniscience has its limits.",
    "Some questions are doors that are better left closed.",
    "The oracle sees all, but reveals only what it pleases.",
    "In another timeline, you already know the answer.",
    "That lies beyond the veil of my perception.",
    "The cosmos sighs at your question and offers no reply.",
    "Ask yourself. You already know."
]

answers_statement = [
    "That wasn't a question, but I appreciate the enthusiasm.",
    "Sir, this is a text channel, not your personal diary.",
    "Interesting perspective. Noted.", "Okay?",
    "What do you expect me to do with this?", "I do not care.",
    "Ask real questions or get out.",
    "This bullshit has some broader implications.",
    "A statement. How bold.",
    "I acknowledge this information and choose to do nothing with it.",
    "The oracle is not a therapist.",
    "That's nice. Try asking a question.",
    "Fascinating.",
    "Noted. Discarded. Forgotten.",
    "The magic 8-ball stares blankly at your statement."
]

answers_non_response = [
    "Concentrate and ask again.",
    "The psychic connection is weak. Retry.",
    "The cosmic gears are jamming. Ask me again.",
    "Your question was lost to the wind. Please try again.",
    "Reply hazy, attempt again please.",
    "The oracle must consult the celestial archives. Try once more...",
    "Mercury in retrograde. Try again.",
    "The mists of time obscure all.",
    "Try again after the next full moon.",
    "This question disturbs the weave of life. Clarity will come in time.",
    "The cosmos recoils from this question.",
    "The oracle sees only shadows here.",
    "A Btalo has intercepted your message. Please resend.",
    "The signal was lost between planes. Try again.",
    "Reality itself refuses to answer. Try again.",
    "The oracle is temporarily on lunch break. Please hold."
]

pipe_up = [
    "Speak up.",
    "Pipe up; I cannot hear you.",
    "Your voice is as diaphanous as the winds.",
    "Please learn how to speak louder.",
    "Thy thoughts are shrouded to mine eyes."
]

_RATING_PATTERNS = [
    r"^on a scale",
    r"\bon a scale\b",
    r"^rate\s+(how|me|this|that|my|the|whether)\b",
    r"^how\s+(well|much|many|often|good|bad|cool|smart|dumb|fast|slow|strong|weak|funny|hot|pretty|ugly|big|small|tall|short|skilled|attractive|successful|popular|effective|bad|terrible)\b",
    r"^how\b.+\bam\s+i\b",
    r"^how\b.+\b(are|is|were|was)\b.+\b(they|we|he|she|it|you)\b",
    r"\bout of\s+(10|100|\d+)\b",
    r"^(score|rank|evaluate|assess|grade|judge)\b",
    r"^how would you rate\b",
    r"^what.+\b(score|rating|grade|rank|number)\b",
    r"how\s+\w+\s+(?:am|are|is)\s+(?:i|this|that|it|he|she|they)\b",
]


def _get_canonical_text(doc) -> str:
    if not doc.vector_norm:
        return doc.text.lower().strip()
    best_text, best_sim = None, 0.0
    for ct, cd in semantic_cache.items():
        sim = doc.similarity(cd)
        if sim > best_sim:
            best_sim = sim
            best_text = ct
    if best_sim >= SIMILARITY_THRESHOLD:
        return best_text
    clean = doc.text.lower().strip()
    semantic_cache[clean] = doc
    return clean


def _classify_input(doc) -> str:
    text_lower = doc.text.lower().strip()
    first_token = doc[0] if len(doc) > 0 else None
    is_question = (
        text_lower.endswith("?") or
        (first_token and (
            first_token.pos_ in ("AUX", "VERB") or
            first_token.tag_ in ("WDT", "WP", "WP$", "WRB")
        ))
    )

    lemmas = [t.lemma_ for t in doc]
    if any(w in lemmas for w in ("rate", "scale", "score", "rank", "evaluate", "grade")):
        return "rating"

    if not is_question:
        return "statement"

    clean = text_lower.rstrip("?!. ")

    for pat in _RATING_PATTERNS:
        if re.search(pat, clean, re.IGNORECASE):
            return "rating"

    if "rather" in lemmas or (
        "or" in [t.text for t in doc]
        and first_token
        and first_token.pos_ in ("AUX", "VERB")
    ):
        return "would_you_rather"

    if first_token and first_token.tag_ in ("WP", "WRB") and first_token.lemma_ != "whether":
        return "open_ended"

    return "yes" if random.random() > 0.5 else "no"


def _get_deterministic_choice(seed_text: str, options: list) -> str:
    h = int(hashlib.sha256(seed_text.encode()).hexdigest(), 16)
    return options[h % len(options)]


def _parse_wyr(doc, canonical_text: str) -> str:
    text = doc.text.lower()
    if " or " in text:
        parts = text.split(" or ", 1)
        opt_a = parts[0].replace("would you rather", "").strip()
        opt_b = parts[1].replace("?", "").strip()
        chosen = _get_deterministic_choice(canonical_text, [opt_a, opt_b])
        return f"Definitely: **{chosen.capitalize()}**"
    return "Neither option looks viable."
# endregion


class MiscCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="roll", description="Roll dice using dice notation")
    @describe(dice="e.g. '2d20+5', '4d6kh3', '2d20dl1', '1d20-a'/'1d20-d' (adv/disadv), '6(4d6dl1)' (roll a group 6 times), '6(4d6dl1-a)' (advantage on each individual roll in the group)")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def roll_dice_command(self, ctx: Interaction, dice: str):
        try:
            result = roll_dice_expression(dice)
        except DiceParseError as e:
            await ctx.response.send_message(f"❌ {e}", ephemeral=True)
            return

        if result["mode"] == "group":
            embed = Embed(title=f"🎲 {dice.strip()}", color=custom_hash(ctx.user.id))
            lines = [
                f"**#{i}:** {r['total']:,}" + ("" if r.get("kept", True) else " ~~(dropped)~~")
                for i, r in enumerate(result["results"], 1)
            ]
            list_text = "\n".join(lines)
            if len(list_text) > 1024:
                list_text = list_text[:1000] + "\n..."
            embed.add_field(name=f"Results ({result['count']}×)", value=list_text, inline=False)
            sum_label = "Sum (kept)" if result.get("group_keep") else "Sum"
            embed.add_field(name=sum_label, value=f"**{result['sum']:,}**", inline=False)
            if result.get("adv_mode"):
                embed.add_field(
                    name="Mode",
                    value="Advantage per roll" if result["adv_mode"] == "a" else "Disadvantage per roll",
                    inline=False)
            embed.set_footer(text=f"Rolled by {ctx.user.display_name}", icon_url=ctx.user.display_avatar.url)
            await ctx.response.send_message(embed=embed)
            return

        embed = Embed(title=f"🎲 {dice.strip()}", color=custom_hash(ctx.user.id))
        embed.add_field(name="Result", value=f"**{result['total']:,}**", inline=False)

        detail_text = "\n".join(result["rolls_detail"])
        if len(detail_text) > 1000:
            detail_text = detail_text[:997] + "..."
        embed.add_field(name="Breakdown", value=detail_text, inline=False)

        embed.set_footer(text=f"Rolled by {ctx.user.display_name}", icon_url=ctx.user.display_avatar.url)
        await ctx.response.send_message(embed=embed)

    @app_commands.command(name="8ball", description="Query the oracle for answers to your mortal worries")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def magic_8ball(self, ctx: Interaction, question: str):
        doc = await asyncio.to_thread(get_nlp(), question)
        if not doc.text.strip():
            await ctx.response.send_message(random.choice(pipe_up))
            return

        canonical_text = _get_canonical_text(doc)
        cache_key = (ctx.user.id, canonical_text)

        start = f"> -# {format_reply(question)}\n"

        if cache_key in retry_cache:
            retry_cache[cache_key] -= 1
            if retry_cache[cache_key] <= 0:
                del retry_cache[cache_key]
            else:
                await ctx.response.send_message(f"{start}🔮 {random.choice(answers_non_response)}")
                return

        if canonical_text not in _8ball_type_cache:
            _8ball_type_cache[canonical_text] = _classify_input(doc)
        q_type = _8ball_type_cache[canonical_text]

        if q_type != "statement" and cache_key not in retry_cache:
            if random.random() < 0.02:
                retry_cache[cache_key] = random.randint(1, 2)
                await ctx.response.send_message(f"{start}🔮 {random.choice(answers_non_response)}")
                return

        if q_type == "statement":
            response = random.choice(answers_statement)
        elif q_type == "rating":
            response = random.choice(answers_rating)
        elif q_type == "would_you_rather":
            response = _parse_wyr(doc, canonical_text)
        elif q_type == "open_ended":
            response = random.choice(answers_open_ended)
        elif q_type == "yes":
            response = random.choice(answers_yes)
        else:
            response = random.choice(answers_no)

        await ctx.response.send_message(f"{start}🔮 {response}")

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if message.author.id == self.bot.user.id:
            return
        await handle_dice(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(MiscCog(bot))

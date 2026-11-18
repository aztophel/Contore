"""Haiku detection: counts syllables (English via CMUdict + a vowel-group
heuristic fallback, Japanese via mora-counting, Cyrillic/Greek via
pyphen-or-vowel-counting, Hangul by counting syllable blocks directly)
across the last three messages sent in a channel, and celebrates when
they land on 5-7-5.
"""
from __future__ import annotations
import re
from collections import defaultdict, deque

import pyphen
from discord import AllowedMentions, Embed, Interaction, Message, app_commands
from discord.app_commands import Group, default_permissions, guild_only
from discord.ext import commands

import core.db as db
from core.discord_utils import GUILD, GUILD_INSTALL
from core.text import _TAG_STRIP_RE, _URL_RE

try:
    import pykakasi
except ImportError:
    pykakasi = None

try:
    import nltk
    try:
        nltk.data.find("corpora/cmudict")
    except LookupError:
        try:
            nltk.download("cmudict", quiet=True)
        except Exception:
            pass
    from nltk.corpus import cmudict as _cmudict_corpus
    _CMUDICT: dict[str, list[list[str]]] = _cmudict_corpus.dict()
except Exception:
    _CMUDICT = {}

_KAKASI = pykakasi.kakasi() if pykakasi else None

_SYLLABLE_EXCEPTIONS = {
    "the": 1, "are": 1, "were": 1, "one": 1, "their": 1, "fire": 1, "hour": 1,
    "every": 2, "different": 3, "poem": 2, "create": 2, "people": 2, "queue": 1,
    "ppl": 2, "pls": 1, "plz": 1, "thx": 1, "cuz": 1, "coz": 1, "bc": 2, "bcz": 2,
    "u": 1, "ur": 1, "r": 1, "rn": 2, "y'all": 1, "gonna": 2, "wanna": 2,
    "gotta": 2, "kinda": 2, "sorta": 2, "lol": 1, "lmao": 3, "omg": 3,
    "idk": 3, "smh": 3, "btw": 3, "brb": 3, "tbh": 3, "imo": 3, "imho": 4,
    "afaik": 4, "w/e": 2, "w/o": 2, "nvm": 3, "jk": 2, "jkjk": 4, "np": 2, "ppl's": 2
}
_VOWEL_GROUPS = re.compile(r"[aeiouy]+", re.IGNORECASE)
_WORD_TOKEN_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*")  # letters, allows internal ' (don't, y'all)

_PYPHEN_DICS: dict[str, object] = {}

# {channel_id: [(syllables, author_id), ...]} / {channel_id: [line_text, ...]},
# each capped at the last 3 messages -- this is where "does the room's last
# three lines scan as 5-7-5" actually gets tracked.
HAIKU_BUFFERS: dict[int, deque] = {}
HAIKU_LINES: dict[int, deque] = {}


def _get_pyphen_dic(lang_code: str):
    if lang_code in _PYPHEN_DICS:
        return _PYPHEN_DICS[lang_code]
    code = lang_code if lang_code in pyphen.LANGUAGES else next(
        (l for l in pyphen.LANGUAGES if l.split("_")[0] == lang_code), None)
    dic = pyphen.Pyphen(lang=code) if code else None
    _PYPHEN_DICS[lang_code] = dic
    return dic


def _pyphen_syllables(word: str, lang_code: str) -> int | None:
    dic = _get_pyphen_dic(lang_code)
    if dic is None:
        return None
    try:
        hyphenated = dic.inserted(word)
    except Exception:
        return None
    return hyphenated.count("-") + 1 if hyphenated else None


def _heuristic_english_syllables(word: str) -> int:
    # Original vowel-group heuristic - kept as a last resort. Works surprisingly well on
    # phonetically-spelled internet slang that trips up both cmudict and pyphen.
    word = re.sub(r"[^a-z']", "", word.lower())
    if not word:
        return 0
    count = len(_VOWEL_GROUPS.findall(word))
    if word.endswith("e") and not word.endswith("le") and count > 1:
        count -= 1
    if word.endswith("le") and len(word) > 2 and word[-3] not in "aeiou":
        count += 1
    return max(count, 1)


def _count_english_syllables(word: str) -> int:
    lw = word.lower()
    if lw in _SYLLABLE_EXCEPTIONS:
        return _SYLLABLE_EXCEPTIONS[lw]
    pron = _CMUDICT.get(lw)
    if pron:
        # CMU dict marks each vowel phoneme with a trailing stress digit (0/1/2) -
        # one vowel phoneme = one syllable. Far more reliable than counting letters.
        return sum(1 for ph in pron[0] if ph[-1].isdigit())
    heuristic = _heuristic_english_syllables(lw)
    pyphen_count = _pyphen_syllables(lw, "en")
    if pyphen_count is None:
        return heuristic
    # Pyphen is usually better for real-but-rare words, but for phonetic slang
    # ("skibidi", "uwu", "pikachu"...) it often finds no break point at all and
    # silently under-counts. Never trust it below the heuristic floor.
    return max(pyphen_count, heuristic)


_CYRILLIC_VOWELS = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")


def _count_cyrillic_syllables(word: str) -> int:
    pyphen_count = _pyphen_syllables(word.lower(), "ru")
    if pyphen_count is not None:
        return pyphen_count
    # Slavic languages don't merge adjacent vowels into diphthongs - one vowel
    # letter = one syllable, reliably, even without a dictionary.
    count = sum(1 for ch in word if ch in _CYRILLIC_VOWELS)
    return max(count, 1) if word else 0


_GREEK_VOWEL_GROUPS = re.compile(r"[αεηιουωΑΕΗΙΟΥΩ]+")


def _count_greek_syllables(word: str) -> int:
    pyphen_count = _pyphen_syllables(word.lower(), "el")
    if pyphen_count is not None:
        return pyphen_count
    # Greek vowel runs *do* form single-syllable diphthongs (αι, ει, οι, ου...),
    # so group consecutive vowels like the English heuristic does.
    count = len(_GREEK_VOWEL_GROUPS.findall(word))
    return max(count, 1) if word else 0


# Small kana that only modify the preceding mora (youon ゃゅょ, and the small vowels used
# in katakana loanwords like ファ "fa") never add a mora of their own. Everything else in
# the kana ranges - including っ sokuon, ー chōonpu, and ん - counts as one mora each.
_JP_SMALL_KANA = set("ゃゅょぁぃぅぇぉャュョァィゥェォ")


def _is_kana(ch: str) -> bool:
    return 0x3040 <= ord(ch) <= 0x30FF


def _count_japanese_morae(word: str) -> int:
    text = word
    if _KAKASI is not None and any(0x3400 <= ord(c) <= 0x9FFF for c in word):
        try:
            text = "".join(item["hira"] for item in _KAKASI.convert(word))
        except Exception:
            pass
    morae = 0
    for ch in text:
        if ch in _JP_SMALL_KANA:
            continue
        if _is_kana(ch):
            morae += 1
        elif 0x3400 <= ord(ch) <= 0x9FFF:
            morae += 1  # leftover kanji pykakasi couldn't resolve (or isn't installed)
    return max(morae, 1) if word else 0


def _count_hangul_syllables(word: str) -> int:
    count = sum(1 for ch in word if 0xAC00 <= ord(ch) <= 0xD7A3)
    return max(count, 1) if word else 0


def _classify_word_script(word: str) -> str:
    counts: dict[str, int] = defaultdict(int)
    for ch in word:
        cp = ord(ch)
        if 0x3040 <= cp <= 0x30FF:
            counts["japanese"] += 1
        elif 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            counts["cjk"] += 1
        elif 0xAC00 <= cp <= 0xD7A3:
            counts["hangul"] += 1
        elif 0x0400 <= cp <= 0x04FF:
            counts["cyrillic"] += 1
        elif 0x0370 <= cp <= 0x03FF:
            counts["greek"] += 1
        elif ch.isalpha():
            counts["latin"] += 1
    if counts.get("japanese"):
        return "japanese"
    if not counts:
        return "latin"
    return max(counts, key=counts.get)


def count_syllables(word: str) -> int:
    if not word:
        return 0
    script = _classify_word_script(word)
    if script == "latin":
        return _count_english_syllables(word)
    if script == "cyrillic":
        return _count_cyrillic_syllables(word)
    if script == "greek":
        return _count_greek_syllables(word)
    if script in ("japanese", "cjk"):
        return _count_japanese_morae(word)
    if script == "hangul":
        return _count_hangul_syllables(word)
    # Best-effort fallback for scripts without a dedicated counter (Arabic, Hebrew,
    # Devanagari, Thai...). Many of these omit vowel marks in everyday writing, which
    # makes rule-based syllabification unreliable without a much heavier model - this
    # is a rough estimate, not a precise count.
    return max(len(word) // 2, 1)


def count_line_syllables(text: str) -> int:
    clean = _TAG_STRIP_RE.sub(" ", text)
    clean = _URL_RE.sub(" ", clean)
    return sum(count_syllables(w) for w in _WORD_TOKEN_RE.findall(clean))


class HaikuCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    haiku_group = Group(name="haiku", description="Haiku detection settings", allowed_contexts=GUILD, allowed_installs=GUILD_INSTALL)

    @haiku_group.command(name="toggle", description="Enable or disable haiku detection in this server")
    @default_permissions(manage_guild=True)
    @guild_only()
    async def haiku_toggle(self, ctx: Interaction, enabled: bool):
        if not ctx.permissions.manage_guild:
            return
        await db.db_set_haiku_enabled(ctx.guild_id, enabled)
        await ctx.response.send_message(f"Haiku detection **{'enabled' if enabled else 'disabled'}**.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if message.author.bot or not message.guild:
            return
        if not await db.db_get_haiku_enabled(message.guild.id):
            return
        content = message.content.strip()
        if not content or content.startswith(("!", "/", "$", "?")):
            return

        syllables = count_line_syllables(content)
        if syllables <= 0:
            return

        cid = message.channel.id
        buf = HAIKU_BUFFERS.setdefault(cid, deque(maxlen=3))
        lines = HAIKU_LINES.setdefault(cid, deque(maxlen=3))
        buf.append((syllables, message.author.id))
        lines.append(content)

        if len(buf) == 3 and [s for s, _ in buf] == [5, 7, 5]:
            authors = list(dict.fromkeys(a for _, a in buf))  # preserve order, dedupe
            poem = "\n".join(lines)
            embed = Embed(
                title="🌸 A haiku has formed! 🌸",
                description=f"*{poem}*",
                color=0xFFC0CB
            )
            embed.set_footer(text=f"5-7-5, composed{' collectively' if len(authors) > 1 else ''}")
            try:
                await message.channel.send(
                    content=" ".join(f"<@{a}>" for a in authors),
                    embed=embed,
                    allowed_mentions=AllowedMentions(users=True)
                )
            except Exception:
                pass
            buf.clear()
            lines.clear()


async def setup(bot: commands.Bot):
    await bot.add_cog(HaikuCog(bot))

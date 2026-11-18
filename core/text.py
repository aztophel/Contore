"""Text transformation utilities.

`uwuify` and `poshify` are the full port of the monolith's text-filter
engine (multi-script uwu transform driven by sentiment classification,
plus a "posh"/Victorian-English filter that the monolith itself shipped
disabled -- see `poshify`'s docstring for why it stays a no-op here too).
`cogs/text_filters.py` just wires these two up to `/uwuify` and `/invert`;
`cogs/proxies.py` calls them directly in its message-relay pipeline.
"""
from __future__ import annotations
import random
import re
from difflib import get_close_matches

from discord import Embed, Message
from discord.utils import escape_markdown  # noqa: F401  (re-exported for convenience)

from core.nlp import get_classifier

# Strips custom emoji / mentions / channel refs / role mentions out of text
# before further processing -- used by both the translate command and haiku
# syllable counting, which is why it lives here instead of either of those.
_TAG_STRIP_RE = re.compile(r"<a?:\w+:\d+>|<@!?\d+>|<#\d+>|<@&\d+>")
_URL_RE = re.compile(
    r"(?:(?:https?|ftp)://|www\.)\S+|(?<!\w)[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}(?!\w)",
    re.IGNORECASE,
)


def split_string_by_new_line(text: str, max_length: int = 4000) -> list[str]:
    """Splits `text` into chunks no longer than `max_length`, preferring
    to break on a newline near the boundary rather than mid-line."""
    parts = []
    while len(text) > max_length:
        idx = text.rfind("\n", 0, max_length)
        if idx == -1:
            parts.append(text[:max_length])
            text = text[max_length:]
        else:
            parts.append(text[:idx + 1])
            text = text[idx + 1:]
    if text:
        parts.append(text)
    return parts


def filter_embeds(embeds: list[Embed]) -> list[Embed]:
    """Drops link-preview embeds (those with a `url` and a non-rich type,
    e.g. auto-generated image/video previews) while keeping rich embeds
    (bot-authored ones) -- used when forwarding a message's embeds through
    a proxy webhook, so we don't duplicate Discord's own preview."""
    return [e for e in embeds if e.type == "rich" or not e.url]


def vote_bar(count: int, total: int, bar_length: int = 20) -> str:
    """Renders a filled/empty block bar plus a percentage and vote count --
    shared by `/poll` and Would You Rather's live vote tallies."""
    pct = round(100 * count / total) if total else 0
    filled = round(bar_length * count / total) if total else 0
    bar = "█" * filled + "░" * (bar_length - filled)
    noun = "vote" if count == 1 else "votes"
    return f"{bar} **{pct}%** · {count} {noun}"


def format_reply(msg: Message | str) -> str:
    """Renders a message (or raw string) as a quoted `> -#` blockquote,
    describing attachments/embeds in place of empty text content. Used
    both for proxy reply-context headers and the 8ball's echoed question."""
    is_msg = isinstance(msg, Message)
    content = (msg.system_content or msg.content) if is_msg else msg
    if content.strip() == "":
        if is_msg:
            if len(msg.attachments) > 0:
                return "*Sent an attachment*"
            elif len(msg.embeds) > 0:
                if len(msg.embeds) == 1 and msg.embeds[0].description:
                    return msg.embeds[0].description
                return "*Sent an embed*"
        return "*Sent an unknown message*"

    content = re.sub(
        r"> -# \*In (?:\[reply to\]\(<[^>]*>\): \(<@[^>]*>\)|reply to a deleted message:|reply to \[an ancient message\]\(<[^>]*>\): \(<@[^>]*>\))\*\n(?:> -# .*\n?|> .*\n?)*",
        "", content, flags=re.MULTILINE,
    )
    content = re.sub(r"(?m)^(?:[>\-#]+\s*)+", "", content).strip()
    return content.replace("\n", "\n> -# ").replace("\n> -# \n", "\n\n")


# --- uwuify ------------------------------------------------------------
_ACTIONS = {
    "latin": {
        "general": ["***wags tail***", "***looks at u***", "***hugs tightly***", "***runs away***", "***blushes***", "***nuzzles ur neck***", "***pounces on u***", "***tilts head***", "***flops over***", "***wiggles***", "***licks ur face***", "***curls into a ball***", "***ears perk up***", "***swishes tail***", "***gives headpats***", "***rolls around***", "***squeaks***", "***boops ur nose***", "***clings to u***", "***spins in circles***", "***trips and falls***", "***peeks at u***", "***hides behind u***", "***mews softly***", "***paws at u***", "***starts purring***", "***glomps u***", "***does a little twirl***", "***bumps head against ur hand***", "***makes grabby hands***", "***ears twitch***", "***rolls over and shows tummy***", "***chases own tail***", "***sits on ur lap uninvited***"],
        "sad": ["***cries softly***", "***sniffles***", "***curls into a ball***", "***clings to u***", "***wipes tears***", "***whimpers***", "***trembles***", "***hugs u and doesn't let go***", "***buries face in ur shoulder***", "***lets out a tiny sob***", "***big sad eyes***"],
        "excited": ["***pounces on u***", "***spins in circles***", "***zooms away***", "***wags tail furiously***", "***jumps up and down***", "***squeaks excitedly***", "***bounces off the walls***", "***does zoomies***", "***vibrates with excitement***", "***flails***"],
        "shy": ["***blushes furiously***", "***looks away***", "***hides face in paws***", "***fidgets nervously***", "***peeks at u***", "***ears flatten***", "***paws at ground***", "***mumbles quietly***", "***avoids eye contact***", "***twiddles thumbs***"],
    },
    "cyrillic": {
        "general": ["***виляет хвостиком***", "***смотрит на тебя***", "***крепко обнимает***", "***убегает***", "***краснеет***", "***трётся об твою шею***", "***набрасывается***", "***наклоняет голову***", "***падает***", "***виляет***", "***лижет твоё личико***", "***сворачивается в клубочек***", "***ушки поднялись***", "***машет хвостиком***", "***гладит по головке***", "***катается***", "***пищит***", "***тык в носик***", "***цепляется за тебя***", "***кружится вокруг***", "***спотыкается и падает***", "***подглядывает***", "***прячется за тобой***", "***тихо мяукает***", "***трогает лапкой***", "***начинает мурчать***", "***гломпает тебя***", "***кружится***", "***бьётся головкой о руку***", "***тянет ручки***", "***шевелит ушками***", "***показывает животик***", "***гоняется за хвостом***", "***садится на коленки***"],
        "sad": ["***тихо плачет***", "***шмыгает носиком***", "***сворачивается в клубочек***", "***держится за тебя***", "***вытирает слёзки***", "***скулит***", "***дрожит***", "***обнимает и не отпускает***", "***уткнулся в плечо***", "***всхлипывает***", "***грустные глазки***"],
        "excited": ["***набрасывается***", "***кружится***", "***убегает быстро***", "***бешено виляет хвостом***", "***прыгает от радости***", "***восторженно пищит***", "***прыгает по стенам***", "***бегает туда-сюда***", "***дрожит от восторга***", "***машет лапками***"],
        "shy": ["***сильно краснеет***", "***отводит взгляд***", "***прячет мордочку***", "***нервно ёрзает***", "***подглядывает***", "***прижимает ушки***", "***копает лапкой землю***", "***тихо бормочет***", "***избегает взгляда***", "***крутит пальчики***"],
    },
    "arabic": {
        "general": ["***يهز ذيله***", "***ينظر إليك***", "***يعانقك بقوة***", "***يهرب بعيداً***", "***يخجل***", "***يداعب عنقك***", "***يقفز عليك***", "***يميل رأسه***", "***ينبطح***", "***يتمايل***", "***يلعق وجهك***", "***يتكور كالكرة***", "***تنتصب أذناه***", "***يحرك ذيله***", "***يمسح على رأسك***", "***يتدحرج***", "***يصدر صوتاً***", "***يلمس أنفك***", "***يتشبث بك***", "***يدور حول نفسه***", "***يتعثر ويقع***", "***يتلصص عليك***", "***يختبئ خلفك***", "***يموء بنعومة***", "***يداعبك بيده***", "***يبدأ بالخرخرة***", "***يحتضنك بقوة***", "***يدور دورة صغيرة***", "***يصدم رأسه بيدك***", "***يمد يديه***", "***يهز أذنيه***", "***ينام على ظهره***", "***يطارد ذيله***", "***يجلس في حضنك***"],
        "sad": ["***يبكي بهدوء***", "***يشمشه***", "***يتكور***", "***يتشبث بك***", "***يمسح دموعه***", "***ينوح***", "***يرتجف***", "***يعانقك ولا يتركك***", "***يدفن وجهه في كتفك***", "***يصدر شهقة صغيرة***", "***عيون حزينة كبيرة***"],
        "excited": ["***يقفز عليك***", "***يدور بسرعة***", "***ينطلق بعيداً***", "***يهز ذيله بجنون***", "***يقفز للأعلى والأسفل***", "***يصرخ بحماس***", "***يقفز على الجدران***", "***يركض بجنون***", "***يهتز من الحماس***", "***يلوح بيديه***"],
        "shy": ["***يخجل بشدة***", "***ينظر بعيداً***", "***يخفي وجهه***", "***يتحرك بتوتر***", "***يتلصص عليك***", "***تخفض أذناه***", "***يحفر في الأرض***", "***يهمس بصوت منخفض***", "***يتجنب النظر***", "***يشبك أصابعه***"],
    },
    "japanese": {
        "general": ["***しっぽをふる***", "***じーっとみる***", "***ぎゅーってする***", "***にげちゃう***", "***ぽっとなる***", "***すりすりする***", "***とびかかる***", "***くびをかしげる***", "***ごろんとする***", "***むにむに***", "***ぺろぺろする***", "***まるくなる***", "***おみみがぴくっ***", "***しっぽをぶんぶん***", "***なでなでする***", "***ころころする***", "***きゅうきゅう***", "***つんつんする***", "***ぎゅっとしてる***", "***くるくるまわる***", "***ころんじゃった***", "***ちらっ***", "***うしろにかくれる***", "***にゃ～んとなく***", "***てしてしする***", "***ごろごろいう***", "***とびつく***", "***くるん***", "***てにすりすり***", "***おててをだす***", "***おみみをぴくぴく***", "***おなかをみせる***", "***しっぽをおいかける***", "***ひざにのる***"],
        "sad": ["***うるうる***", "***ぐすん***", "***まるまってる***", "***はなさない***", "***めをこする***", "***くぅ～ん***", "***ぷるぷる***", "***ずっとぎゅっ***", "***かたにがおー***", "***ひっく***", "***かなしいおめめ***"],
        "excited": ["***とびかかる***", "***くるくる***", "***びゅーん***", "***しっぽふりふり***", "***ぴょんぴょん***", "***きゅうきゅう***", "***はねまわる***", "***うろうろ***", "***わくわく***", "***ばたばた***"],
        "shy": ["***まっか***", "***ぷいっ***", "***おかおをかくす***", "***もじもじ***", "***ちらっ***", "***おみみがぺたん***", "***あしでつんつん***", "***もごもご***", "***めをあわせない***", "***ゆびをいじる***"],
    },
    "cjk": {
        "general": ["***摇尾巴***", "***盯着你看***", "***抱紧紧***", "***跑掉***", "***脸红***", "***蹭蹭你的脖子***", "***扑在你身上***", "***歪头***", "***倒地***", "***扭动***", "***舔你的脸***", "***缩成一团***", "***耳朵竖起来***", "***甩尾巴***", "***摸摸头***", "***打滚***", "***吱吱叫***", "***戳你的鼻子***", "***粘着你***", "***转圈圈***", "***摔倒了***", "***偷看***", "***躲在你身后***", "***轻轻喵喵叫***", "***用爪子抓你***", "***开始打呼噜***", "***扑过去***", "***转个小圈***", "***用头蹭你的手***", "***伸手要抱抱***", "***动动耳朵***", "***翻过来露肚皮***", "***追自己的尾巴***", "***坐在你腿上***"],
        "sad": ["***轻轻哭泣***", "***吸鼻子***", "***缩成一团***", "***紧紧抱着你***", "***擦眼泪***", "***抽泣***", "***发抖***", "***抱着你不撒手***", "***埋在你的肩膀***", "***小声呜咽***", "***委屈的大眼睛***"],
        "excited": ["***扑向你***", "***原地转圈***", "***飞快跑掉***", "***疯狂摇尾巴***", "***跳上跳下***", "***兴奋地叫***", "***上蹿下跳***", "***到处乱跑***", "***兴奋得发抖***", "***挥动手臂***"],
        "shy": ["***满脸通红***", "***看向别处***", "***用爪爪遮住脸***", "***局促不安***", "***偷瞄一眼***", "***耳朵耷拉下来***", "***抓地***", "***小声嘟囔***", "***躲闪眼神***", "***对手指***"],
    },
    "hangul": {
        "general": ["***꼬리 살랑***", "***빤히 바라봐***", "***꼭 껴안아***", "***도망가***", "***발그레***", "***목에 부비부비***", "***덮쳐버려***", "***고개 갸웃***", "***벌러덩***", "***꼬물꼬물***", "***얼굴 핥짝***", "***동그랗게 몸 말기***", "***귀가 쫑긋***", "***꼬리 살랑살랑***", "***쓰다듬어줘***", "***데굴데굴***", "***뀨우***", "***코 톡***", "***매달려***", "***빙글빙글***", "***콰당***", "***힐끗***", "***뒤에 숨어***", "***야옹***", "***앞발 툭툭***", "***가르릉***", "***와락***", "***빙그르르***", "***손에 머리 부비***", "***손 뻗기***", "***귀 쫑긋쫑긋***", "***배 보여주기***", "***꼬리 잡기***", "***무릎에 앉아***"],
        "sad": ["***훌쩍훌쩍***", "***코 훌쩍***", "***몸을 웅크려***", "***매달려***", "***눈물 슥슥***", "***낑낑***", "***덜덜***", "***안고 안 놔줘***", "***어깨에 얼굴 묻기***", "***흐응***", "***슬픈 눈망울***"],
        "excited": ["***와락 덮쳐***", "***뱅글뱅글***", "***슈슉***", "***꼬리 폭풍 살랑***", "***방방 뛰어***", "***신나서 뀨잉***", "***벽 타고 달려***", "***우다다***", "***부르르***", "***파닥파닥***"],
        "shy": ["***완전 발그레***", "***먼 산***", "***얼굴 가리기***", "***안절부절***", "***힐끗***", "***귀가 축***", "***바닥 긁기***", "***중얼중얼***", "***눈 피하기***", "***손가락 만지작***"],
    },
    "greek": {
        "general": ["***κουνάει ουρά***", "***σε κοιτάει***", "***σε αγκαλιάζει σφιχτά***", "***τρέχει μακριά***", "***κοκκινίζει***", "***τρίβεται στον λαιμό***", "***πηδάει πάνω σου***", "***γέρνει το κεφάλι***", "***πέφτει κάτω***", "***κουνιέται***", "***σου γλύφει το πρόσωπο***", "***γίνεται μπαλίτσα***", "***τα αυτιά σηκώνονται***", "***κουνάει την ουρά***", "***σε χαϊδεύει***", "***κυλιέται***", "***τσιρίζει***", "***σου ακουμπά τη μύτη***", "***κολλάει πάνω σου***", "***γυρίζει γύρω-γύρω***", "***σκοντάφτει***", "***σε κοιτάζει κρυφά***", "***κρύβεται από πίσω σου***", "***νιαουρίζει***", "***σε ακουμπά με την πατούσα***", "***αρχίζει να γουργουρίζει***", "***σε γραπώνει***", "***κάνει μια στροφή***", "***τρίβει το κεφάλι στο χέρι***", "***απλώνει τα χέρια***", "***κουνάει τα αυτιά***", "***δείχνει την κοιλίτσα***", "***κυνηγάει την ουρά***", "***κάθεται στην αγκαλιά σου***"],
        "sad": ["***κλαίει σιγανά***", "***ρουφάει τη μύτη***", "***κουλουριάζεται***", "***κολλάει πάνω σου***", "***σκουπίζει τα δάκρυα***", "***κλαψουρίζει***", "***τρέμει***", "***σε αγκαλιάζει σφιχτά***", "***κρύβει το πρόσωπο***", "***βγάζει έναν λυγμό***", "***μεγάλα λυπημένα μάτια***"],
        "excited": ["***πηδάει πάνω σου***", "***γυρίζει γύρω-γύρω***", "***τρέχει γρήγορα***", "***κουνάει την ουρά τρελά***", "***χοροπηδάει***", "***τσιρίζει από χαρά***", "***τρέχει παντού***", "***κάνει ζουμιές***", "***τρέμει από ενθουσιασμό***", "***κουνάει τα χέρια***"],
        "shy": ["***κοκκινίζει πολύ***", "***κοιτάζει αλλού***", "***κρύβει το πρόσωπο***", "***κουνιέται νευρικά***", "***σε κοιτάζει κρυφά***", "***τα αυτιά πέφτουν***", "***σκάβει το χώμα***", "***μουρμουρίζει***", "***αποφεύγει το βλέμμα***", "***παίζει με τα δάκτυλα***"],
    },
    "hebrew": {
        "general": ["***מכשכש בזנב***", "***מסתכל עליך***", "***מחבק חזק***", "***בורח***", "***מסמיק***", "***מתחכך בצוואר***", "***קופץ עליך***", "***מטה את הראש***", "***נופל על הרצפה***", "***מתפתל***", "***מלקק לך את הפנים***", "***מתכרבל לכדור***", "***האוזניים מזדקפות***", "***מניע את הזנב***", "***מלטף אותך***", "***מתגלגל***", "***מצייץ***", "***נוגע לך באף***", "***נצמד אליך***", "***מסתובב במעגלים***", "***מועד ונופל***", "***מציץ עליך***", "***מתחבא מאחוריך***", "***מיילל בשקט***", "***נוגע בך עם הכפה***", "***מתחיל לגרגר***", "***מתנפל בחיבוק***", "***עושה סיבוב קטן***", "***דוחף ראש ליד שלך***", "***מושיט ידיים***", "***מזיז את האוזניים***", "***מראה את הבטן***", "***רודף אחרי הזנב***", "***יושב לך על הברכיים***"],
        "sad": ["***בוכה בשקט***", "***מושך באף***", "***מתכרבל***", "***נצמד אליך***", "***מנגב דמעות***", "***מייבב***", "***רועד***", "***מחבק ולא עוזב***", "***קובר פנים בכתף***", "***מתייפח מעט***", "***עיניים עצובות גדולות***"],
        "excited": ["***קופץ עליך***", "***מסתובב במעגלים***", "***טס משם***", "***מכשכש בזנב בטירוף***", "***קופץ למעלה ולמטה***", "***מצייץ בהתרגשות***", "***קופץ על הקירות***", "***מתרוצץ בטירוף***", "***רועד מהתרגשות***", "***מנופף בידיים***"],
        "shy": ["***מסמיק נורא***", "***מסתכל הצידה***", "***מסתיר פנים עם הכפות***", "***מתנועע בחוסר נוחות***", "***מציץ עליך***", "***האוזניים נצמדות***", "***חופר ברצפה***", "***ממלמל בשקט***", "***נמנע מקשר עין***", "***משחק באצבעות***"],
    },
}

_LATIN_MATCHES = [
    (r"^((th|d)at'?s\s+)?tuff(\s+as[lf])?$", ["onii-chan, dat's coow...", "sugoi...", "kakkoii!", "yabai...", "majide??", "uwaah... dat's sewiouswy so cool!", "sugoi... ur auwa is ovewwhewming...", "nyo way, dat's kinda bad ass..."]),
    (r"\btuff?(\s+as[lf])?\b", ["kawaii", "kakkoii"]),
]
_LATIN_WORDS = [
    ("you're", "ur"), ("your", "ur"), ("you", "u"), ("hi", "hai"), ("what", "wut"), ("good", "gud"),
    ("okay", "kay"), ("ok", "kay"), ("the", "da"), ("was", "waws"), ("stop", "stawp"), ("hubby", "husbando"),
    ("husband", "husbando"), ("wife", "waifu"), ("wifey", "waifu"), ("one", "wan"), ("everyone", "everynyan"),
    ("anyone", "anynyan"), ("someone", "somenyan"), ("no-one", "no-nyan"), ("noone", "no nyan"),
    ("@everyone", "@everynyan"), ("to", "tu"), ("youre", "ur"), ("two", "twu"), ("new", "nyu"),
    ("hola", "howah"), ("ciao", "ciaow"),
]
_LATIN_EMOTES = ["x3", ":3", "owo", "uwu", ">w<", "^w^", "~", "rawr", "nya~", "(^・ω・^)", "(*^ω^*)", "hehe~", "teehee~", "<3", "murr~", "(uwu)"]
_PROTECTED_UWU = {"rawr", "rawr~", "murr", "murr~", "purr", "purr~", "everynyan", "@everynyan", "ur", "onii-chan", "onee-chan", "onii", "onee", "over", "wrong"}
_CYR_WORDS = [("нет", "няет"), ("привет", "пвивет"), ("люблю", "вюбвю"), ("пожалуйста", "пожавуйста"), ("красиво", "квасиво")]
_CYR_VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
_CYR_EMOTES = ["уву", "ово", "ниа~", "мяу~", "хехе", "~", "ня~"]
_AR_EMOTES = ["أووو~", "هيهي", "ياه~", "~", "هههه", "كيوت~", "أوووه"]
_HIRA_R2W = str.maketrans("らりるれろ", "わゐうゑを")
_KATA_R2W = str.maketrans("ラリルレロ", "ワヰウヱヲ")
_JP_EMOTES = ["にゃ～", "うわあ", "うゆ", "(*´ω`*)", "(=^･ω･^=)", "ふふ～", "きゃ～", "ぽわ～", "(≧ω≦)"]
_ZH_PARTICLES = ["嗷呜～", "哇哇", "呜呜", "哼哼", "么么哒", "啾啾～", "嘤嘤", "慕了～", "好可爱哦～"]
_KO_EMOTES = ["우우～", "냥～", "뇨～", "에헤헤", "히히", "냐옹", "꾸잉～"]
_GR_WORDS = [("όχι", "όνχι"), ("γεια", "γειαα"), ("αγάπη", "αγάπηη"), ("παρακαλώ", "παρακιαλώ")]
_GR_EMOTES = ["ουωυ", "νια~", "χαχα~", "~", "ωωω", "κιαου~"]
_HE_WORDS = [("לא", "ניא"), ("שלום", "שולום"), ("אהבה", "אהבהה"), ("בבקשה", "בבקשהה")]
_HE_EMOTES = ["אווו", "ניה~", "חחח", "~", "יאי~", "מיאו~"]

EMOTION_BUCKETS = {
    "sad": {"sadness", "grief", "loneliness", "remorse", "disappointment", "disapproval"},
    "excited": {"joy", "excitement", "amusement", "pride", "approval", "relief", "surprise"},
    "shy": {"embarrassment", "nervousness", "awkwardness", "admiration", "gratitude", "love", "desire", "caring", "optimism", "confusion"},
    "angry": {"anger", "annoyance", "frustration", "disgust"},
}
STUTTER_RATE = {"sad": 0.05, "excited": 0.05, "shy": 0.25, "angry": 0.03, "general": 0.04}
EMOTE_RATE = {"sad": 0.20, "excited": 0.60, "shy": 0.35, "angry": 0.05, "general": 0.35}


def pick_action(ctx_type: str, script: str = "latin") -> str:
    actions = _ACTIONS.get(script, _ACTIONS["latin"])
    pool = list(actions["general"])
    if ctx_type in actions:
        pool += actions[ctx_type] * 2
    return random.choice(pool)


async def uwuify(text: str) -> str:
    def map_label(label):
        label = label.lower()
        for bucket, labels in EMOTION_BUCKETS.items():
            if label in labels:
                return bucket
        return "general"

    async def classify_context(text):
        import asyncio
        results = (await asyncio.to_thread(get_classifier(), text))[0]
        return map_label(max(results, key=lambda x: x["score"])["label"])

    _ctx = await classify_context(text)

    def _p(prob):
        return random.random() < prob

    def _script(s):
        c = dict(cjk=0, hangul=0, japanese=0, cyrillic=0, arabic=0, greek=0, hebrew=0, latin=0)
        for ch in s:
            n = ord(ch)
            if 0x4E00 <= n <= 0x9FFF or 0x3400 <= n <= 0x4DBF:
                c["cjk"] += 1
            elif 0xAC00 <= n <= 0xD7A3 or 0x1100 <= n <= 0x11FF:
                c["hangul"] += 1
            elif 0x3040 <= n <= 0x309F or 0x30A0 <= n <= 0x30FF:
                c["japanese"] += 1
            elif 0x0400 <= n <= 0x04FF:
                c["cyrillic"] += 1
            elif 0x0600 <= n <= 0x06FF or 0xFB50 <= n <= 0xFEFF:
                c["arabic"] += 1
            elif 0x0370 <= n <= 0x03FF:
                c["greek"] += 1
            elif 0x0590 <= n <= 0x05FF:
                c["hebrew"] += 1
            elif ch.isalpha():
                c["latin"] += 1
        return max(c, key=c.get) if any(c.values()) else "latin"

    def _stutter(word):
        for i, ch in enumerate(word):
            if ch.isalpha():
                reps = random.randint(1, 4)
                stub = "-".join([ch] * reps) + "-"
                return word[:i] + stub + word[i:]
        return word

    def _repl(s, src, dst):
        def _fix(m):
            o = m.group()
            if o.isupper():
                return dst.upper()
            if o[0].isupper():
                return dst[0].upper() + dst[1:]
            return dst
        return re.sub(r"(?i)\b" + re.escape(src) + r"\b", _fix, s)

    def _uwu_latin(s):
        for reg, replacements in _LATIN_MATCHES:
            s = re.sub(reg, random.choice(replacements), s, flags=re.IGNORECASE)
        for src, dst in _LATIN_WORDS:
            s = _repl(s, src, dst)
        s = re.sub(r"\b[Tt][Hh](?=[AEIOUaeiou])(?![Oo]\b)", lambda m: "D" if m.group()[0].isupper() else "d", s)
        s = re.sub(r"\B[Tt][Hh]|[Tt][Hh](?![Oo]\b)", lambda m: "F" if m.group()[0].isupper() else "f", s)
        protected = {}

        def _protect(m):
            key = f"\x00UWU{len(protected)}\x00"
            protected[key] = m.group(0)
            return key
        pattern = r"(?i)\b(?:" + "|".join(map(re.escape, _PROTECTED_UWU)) + r")\b"
        s = re.sub(pattern, _protect, s)
        s = re.sub(r"[RrLl]", lambda m: "W" if m.group().isupper() else "w", s)
        s = re.sub(r"([Nn])([AEIOUaeiou])", lambda m: (m.group(1) + ("Y" if m.group(2).isupper() else "y")) + m.group(2), s)
        s = re.sub(r"([Mm])([OUou])", lambda m: (m.group(1) + ("Y" if m.group(2).isupper() else "y")) + m.group(2), s)
        s = re.sub(r"[Oo][Vv][Ee]\b", lambda m: "UV" if m.group().isupper() else "uv", s)
        for key, value in protected.items():
            s = s.replace(key, value)
        tokens = re.split(r"(\s+)", s)
        out = []
        for tok in tokens:
            if re.match(r"[a-zA-Z]", tok) and "\x00" not in tok and _p(STUTTER_RATE[_ctx]):
                tok = _stutter(tok)
            out.append(tok)
        s = "".join(out)
        if _p(EMOTE_RATE[_ctx]):
            s = s.rstrip() + " " + random.choice(_LATIN_EMOTES)
        return s

    def _uwu_cyrillic(s):
        for src, dst in _CYR_WORDS:
            s = re.sub(r"\b" + src + r"\b", dst, s, flags=re.IGNORECASE)
        s = s.replace("р", "в").replace("Р", "В")
        s = s.replace("л", "в").replace("Л", "В")
        s = re.sub(f"([НнN])([{_CYR_VOWELS}])", lambda m: ("НЯ" if m.group(1).isupper() else "ня") + m.group(2), s)
        tokens = re.split(r"(\s+)", s)
        out = []
        for tok in tokens:
            if re.match(r"[а-яА-ЯёЁ]", tok) and "\x00" not in tok and _p(STUTTER_RATE[_ctx] * 0.75):
                tok = _stutter(tok)
            out.append(tok)
        s = "".join(out)
        if _p(EMOTE_RATE[_ctx]):
            s = s.rstrip() + " " + random.choice(_CYR_EMOTES)
        return s

    def _uwu_arabic(s):
        s = s.replace("ر", "و")
        s = s.replace("ل", "و")
        tokens = re.split(r"(\s+)", s)
        out = []
        for tok in tokens:
            if re.match(r"[\u0600-\u06FF]", tok) and "\x00" not in tok and _p(STUTTER_RATE[_ctx] * 0.75):
                tok = _stutter(tok)
            out.append(tok)
        s = "".join(out)
        if _p(EMOTE_RATE[_ctx]):
            s = s.rstrip() + " " + random.choice(_AR_EMOTES)
        return s

    def _uwu_japanese(s):
        s = s.translate(_HIRA_R2W)
        s = s.translate(_KATA_R2W)
        s = s.replace("の", "にょ").replace("ノ", "ニョ")
        s = s.replace("な", "にゃ").replace("ナ", "ニャ")
        if _p(EMOTE_RATE[_ctx]):
            s = s.rstrip() + random.choice(_JP_EMOTES)
        return s

    def _uwu_chinese(s):
        out, chars = [], list(s)
        for i, ch in enumerate(chars):
            out.append(ch)
            if 0x4E00 <= ord(ch) <= 0x9FFF and i < len(chars) - 1 and _p(0.07):
                out.append("～")
        s = "".join(out)
        if _p(EMOTE_RATE[_ctx]):
            s = s.rstrip() + random.choice(_ZH_PARTICLES)
        return s

    def _uwu_korean(s):
        out = []
        for ch in s:
            cp = ord(ch)
            if 0xAC00 <= cp <= 0xD7A3:
                off = cp - 0xAC00
                fin = off % 28
                vow = (off // 28) % 21
                ini = off // 28 // 21
                if ini == 5:
                    ini = 11
                if fin == 8:
                    fin = 0
                out.append(chr(0xAC00 + (ini * 21 + vow) * 28 + fin))
            else:
                out.append(ch)
        s = "".join(out)
        if _p(EMOTE_RATE[_ctx]):
            s = s.rstrip() + " " + random.choice(_KO_EMOTES)
        return s

    def _uwu_greek(s):
        for src, dst in _GR_WORDS:
            s = re.sub(r"\b" + src + r"\b", dst, s, flags=re.IGNORECASE)
        s = s.replace("ρ", "ω").replace("Ρ", "Ω")
        s = s.replace("λ", "ω").replace("Λ", "Ω")
        tokens = re.split(r"(\s+)", s)
        out = []
        for tok in tokens:
            if re.match(r"[α-ωΑ-Ω]", tok) and "\x00" not in tok and _p(STUTTER_RATE[_ctx] * 0.75):
                tok = _stutter(tok)
            out.append(tok)
        s = "".join(out)
        if _p(EMOTE_RATE[_ctx]):
            s = s.rstrip() + " " + random.choice(_GR_EMOTES)
        return s

    def _uwu_hebrew(s):
        for src, dst in _HE_WORDS:
            s = re.sub(r"\b" + src + r"\b", dst, s)
        s = s.replace("ר", "ו")
        s = s.replace("ל", "ו")
        tokens = re.split(r"(\s+)", s)
        out = []
        for tok in tokens:
            if re.match(r"[\u0590-\u05FF]", tok) and "\x00" not in tok and _p(STUTTER_RATE[_ctx] * 0.75):
                tok = _stutter(tok)
            out.append(tok)
        s = "".join(out)
        if _p(EMOTE_RATE[_ctx]):
            s = s.rstrip() + " " + random.choice(_HE_EMOTES)
        return s

    def _inject_inline(s, script):
        parts = s.split(" ")
        if len(parts) < 7 or not _p(0.20):
            return s
        idx = random.randint(2, len(parts) - 2)
        parts.insert(idx, pick_action(_ctx, script))
        return " ".join(parts)

    _dispatch = {
        "latin": _uwu_latin, "cyrillic": _uwu_cyrillic, "arabic": _uwu_arabic,
        "cjk": _uwu_chinese, "japanese": _uwu_japanese, "hangul": _uwu_korean,
        "greek": _uwu_greek, "hebrew": _uwu_hebrew,
    }
    out_lines = []
    for line in text.split("\n"):
        if not line.strip():
            out_lines.append(line)
            continue
        placeholders = {}

        def _stash(m):
            key = f"\x00+(||)*__*(||)+{len(placeholders)}\x00"
            placeholders[key] = m.group()
            return key
        safe_line = _URL_RE.sub(_stash, line)
        sc = _script(safe_line)
        fn = _dispatch.get(sc, _uwu_latin)
        res = fn(safe_line)
        res = _inject_inline(res, sc)
        if _p(0.20):
            res = res.rstrip() + " " + pick_action(_ctx, sc)
        for key, url in placeholders.items():
            res = res.replace(key, url)
        out_lines.append(res)
    return "\n".join(out_lines)


# --- poshify (currently disabled) ---------------------------------
# The tables and helper functions below implement a "posh"/formal-English
# text transform, but the monolith shipped it disabled (the function body
# was commented out, leaving only `return text`) because the output
# quality wasn't good enough for regular use. Preserved verbatim as
# dormant, callable machinery rather than deleted outright, in case
# someone wants to pick this back up later -- flip `poshify` to call
# `_poshify_impl` instead of returning `text` unchanged.
_COPULAR_NEXT = frozenset({"so", "very", "rly", "really", "too", "quite", "rather", "pretty", "super", "hella", "absolutely", "definitely", "totally", "kinda", "sorta", "literally", "actually", "honestly", "truly", "just", "not", "never", "always", "basically", "lowkey", "highkey", "truthfully", "already", "completely", "here", "there", "where", "whereabouts", "good", "bad", "great", "fine", "wrong", "right", "crazy", "cool", "awesome", "amazing", "dumb", "stupid", "smart", "nice", "mean", "late", "early", "ready", "free", "safe", "lost", "done", "dead", "welcome", "correct", "perfect", "beautiful", "brilliant", "mad", "tired", "bored", "stressed", "hungry", "sick", "okay", "ok", "superb", "delectable", "the", "such", "my", "our", "their", "his", "her"})
_COPULAR_VERB_NEXT = frozenset({"gonna", "gotta", "tryna", "about", "supposed", "going", "coming", "making", "being", "getting", "doing", "trying", "saying", "telling", "thinking", "feeling", "looking", "taking", "giving", "killing", "missing", "joking", "kidding", "lying", "winning", "losing"})
_GERUND_RE = re.compile(r"[a-z]{3,}ing$", re.IGNORECASE)

PHRASE_REPLACEMENTS = [
    (r"\btouch\s+base\b", "initiate a brief consultation"), (r"\bcircle\s+back\b", "revisit the matter at a more civilized hour"),
    (r"\bthink\s+outside\s+the\s+box\b", "engage in unconventional cognitive exploration"), (r"\b(a\s+)?win\s+win\b", "mutually advantageous to both parties"),
    (r"\bmove\s+the\s+needle\b", "effect a discernable consequence"), (r"\bit'?s\s+giving\b", "it bears a striking resemblance to"),
    (r"\bbuilt\s+different\b", "constructed of an entirely superior constitution"), (r"\bsay\s+less\b", "say no more, I comprehend entirely"),
    (r"\bsecure\s+the\s+bag\b", "acquire one's financial fortunes"), (r"\bunderstood\s+the\s+assignment\b", "executed the task with exemplary precision"),
    (r"\bliving\s+rent\s+free\b", "occupying one's thoughts without just compensation"), (r"\bhits\s+different\b", "evokes a uniquely profound sensation"),
    (r"\blet\s+(?:him|her|them)\s+cook\b", "allow the honorable individual to proceed with their masterful endeavors"),
    (r"\bcaught\s+in\s+4k\b", "apprehended with undeniable photographic evidence"), (r"^yucky?$", "how ghastly"),
]


def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original and original[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


def _apply_phrases(text: str) -> str:
    for pattern, replacement in PHRASE_REPLACEMENTS:
        text = re.sub(pattern, lambda m, r=replacement: _match_case(m.group(0), r), text, flags=re.IGNORECASE)
    return text


WORD_REPLACEMENTS = {
    "lol": "I am positively beside myself with laughter", "lmao": "I am positively beside myself with laughter",
    "rofl": "I am positively beside myself with laughter", "lmfao": "I am positively beside myself with laughter",
    "omg": "goodness gracious", "wtf": "what in the world", "wth": "what in the world",
    "idk": "I confess I am uncertain", "smh": "I shake my head in quiet disapproval",
    "tbh": "to be entirely forthright", "imo": "in my humble estimation", "imho": "in my humble estimation",
    "fyi": "for your edification", "btw": "incidentally", "brb": "I shall return momentarily",
    "gtg": "I must take my leave", "ttyl": "until we speak again", "np": "think nothing of it",
    "yw": "you are most welcome", "thx": "much obliged", "ty": "much obliged", "pls": "I entreat you",
    "plz": "I entreat you", "gonna": "going to", "wanna": "desirous of", "gotta": "must",
    "kinda": "somewhat", "sorta": "somewhat", "dunno": "I have not the faintest idea",
    "cuz": "on account of", "coz": "on account of", "ppl": "individuals", "u": "you",
    "ur": "your", "r": "are", "y": "why", "k": "very well", "ok": "very well", "okay": "very well",
    "yeah": "indeed", "yea": "indeed", "yep": "indeed", "yup": "indeed", "nah": "I think not",
    "nope": "I think not", "cool": "rather splendid", "awesome": "truly magnificent",
    "great": "most excellent", "nice": "quite pleasant", "sucks": "is thoroughly disagreeable",
    "sick": "remarkably impressive", "lit": "remarkably impressive", "fire": "remarkably impressive",
    "dope": "remarkably impressive", "cringe": "excruciatingly embarrassing", "weird": "peculiar",
    "gross": "positively revolting", "stupid": "remarkably foolish", "dumb": "remarkably foolish",
    "crazy": "utterly extraordinary", "insane": "utterly extraordinary", "wild": "utterly extraordinary",
    "guy": "gentleman", "guys": "gentlemen and ladies", "dude": "good sir", "bro": "good fellow",
    "girl": "young lady", "kid": "young one", "kids": "young ones", "stuff": "various matters",
    "things": "various matters", "job": "occupation", "boss": "employer", "house": "residence",
    "store": "shop", "food": "sustenance", "phone": "telephone", "computer": "computing apparatus",
    "car": "automobile", "money": "funds", "cash": "currency", "buy": "procure", "get": "obtain",
    "give": "bestow", "make": "fashion", "help": "assistance", "fix": "remedy", "fast": "with great haste",
    "quick": "expeditious", "slow": "unhurried", "big": "substantial", "small": "diminutive",
    "little": "diminutive", "old": "aged", "young": "youthful", "new": "novel", "hard": "arduous",
    "easy": "effortless", "fun": "delightful", "boring": "tedious", "tired": "fatigued",
    "hungry": "in want of sustenance", "thirsty": "in want of drink", "happy": "in good spirits",
    "sad": "in low spirits", "mad": "vexed", "angry": "incensed", "scared": "apprehensive",
    "worried": "troubled", "sorry": "my sincerest apologies", "please": "if it would please you",
    "thanks": "much obliged", "hi": "good day", "hey": "good day", "hello": "good day",
    "bye": "farewell", "cya": "until we meet again", "later": "at a later hour",
    "now": "presently", "soon": "shortly", "later": "at a later hour",
}


def _classify_ur(text: str, match_end: int) -> str:
    rest = text[match_end:].lstrip()
    nw_m = re.match(r"[a-zA-Z']+", rest)
    if not nw_m:
        return "your"
    nw = nw_m.group(0).lower().rstrip("'")
    if nw in _COPULAR_NEXT or nw in _COPULAR_VERB_NEXT:
        return "you are"
    if _GERUND_RE.match(nw):
        return "you are"
    return "your"


def _resolve_ur(text: str) -> str:
    def _sub(m):
        matched = m.group(0)
        rep = _classify_ur(text, m.end())
        return _match_case(matched, rep)
    return re.sub(r"\bur\b", _sub, text, flags=re.IGNORECASE)


def _apply_words(text: str) -> str:
    keys = sorted(WORD_REPLACEMENTS.keys(), key=len, reverse=True)
    pattern = r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b"

    def _sub(m):
        matched = m.group(0)
        rep = WORD_REPLACEMENTS.get(matched.lower(), matched)
        return _match_case(matched, rep)
    return re.sub(pattern, _sub, text, flags=re.IGNORECASE)


def _fix_i_pronoun(s: str) -> str:
    return re.sub(r"(?<!\w)i(?!\w)", "I", s)


def _fix_an_determinator(s: str) -> str:
    return re.sub(r"(?<!\w)([Aa])\b(\s+)([AEIOUaeiou])",
                   lambda m: m.group(1) + ("N" if m.group(3).isupper() else "n") + m.group(2) + m.group(3), s)


def _clean_whitespace(s: str) -> str:
    s = re.sub(r" {2,}", " ", s)
    s = re.sub(r" ([.,!?;:])", r"\1", s)
    s = re.sub(r"([-–—])\s*([-–—])+", " — ", s)
    return s.strip()


def _capitalise_first(s: str) -> str:
    for i, ch in enumerate(s):
        if ch.isalpha():
            return s[:i] + ch.upper() + s[i + 1:]
    return s


def _fix_capitalization(s: str) -> str:
    return re.sub(r"([.?!])(\s*[a-z])", lambda m: m.group(1) + m.group(2).upper(), s)


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.strip()]


def poshify(text: str) -> str:
    """The "posh"/Victorian-English filter behind `/invert`. Disabled in
    the monolith (short-circuited to a no-op before the punctuation-restore
    step) -- likely because the `PUNCTUATOR` dependency it originally
    called out to was dropped, or the pass just wasn't reliable enough to
    ship. Preserved as-is (still a no-op) rather than silently re-enabled,
    since flipping it back on is a behavior change, not a refactor. The
    original pipeline is kept below, commented, in case a future pass
    wants to pick it back up -- it only needs a working punctuation
    restorer to be viable again.
    """
    return text
    # if not text or not text.strip():
    #     return text
    # text = text.replace(".", "").replace(", ", "").replace("?", "").replace("!", "")
    # result = _resolve_ur(text)
    # result = re.sub(r"[^\w\s'\".,!?;:\--]", "", result)
    # result = _apply_phrases(result); result = _apply_words(result)
    # sentences = _split_sentences(result); polished = []
    # for s in sentences:
    #     s = _fix_i_pronoun(s); s = _fix_an_determinator(s); s = _clean_whitespace(s); s = _capitalise_first(s); polished.append(s)
    # fixed_text = _fix_capitalization(" ".join(polished))
    # try:
    #     return PUNCTUATOR.restore_punctuation(fixed_text)
    # except Exception:
    #     return fixed_text

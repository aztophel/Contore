"""Truth or Dare, Would You Rather, and the community submission-review
pipeline that feeds new questions into both pools. The three big question
lists (`TRUTHS`, `DARES`, `WOULD_YOU_RATHER`) are plain data; everything
else is game state (which question a channel saw recently, live WYR vote
tallies) and the moderation workflow for user-submitted questions
(`/submit truth|dare|wyr`, reviewed via buttons in a dedicated channel).

Registers four persistent (`timeout=None`, fixed `custom_id`) views on
load: `TruthOrDareView`, `WouldYouRatherView`, plus one
`SubmissionReviewView` per still-pending submission found in the database.
"""
from __future__ import annotations
import random
import re
import time
from collections import defaultdict
from typing import Literal
from uuid import uuid4

from discord import (
    ButtonStyle, Embed, Guild, Interaction, Message, TextStyle, User, app_commands,
)
from discord.app_commands import Group, describe
from discord.ext import commands
from discord.ui import Button, Modal, TextInput, View, button

import core.db as db
from config import OWNER_ID, SUBMISSION_REVIEW_CHANNEL_ID
from core.discord_utils import ALL, ALL_INSTALLS
from core.emoji import custom_hash, descape
from core.text import vote_bar as _vote_bar

TRUTHS = [
    "What's the biggest lie you've ever told and gotten away with?",
    "What's the most embarassing thing in your phone's gallery?",
    "What's a belief you used to hold but now find embarassing?",
    "Where was the strangest place you ever farted?",
    "Have you ever lied to one of your friends to get out of making plans with them?",
    "What's your most embarassing childhood memory?",
    "What's your silliest fear?",
    "Who in this server would you trust least in a crisis?",
    "Who was your first crush and do you still think about them?",
    "Have you ever stood someone up?",
    "Have you ever eavesdropped on a conversation you weren't supposed to hear?",
    "What's the most embarrassing thing you've done while trying to impress someone?",
    "Have you ever ghosted someone? Why?",
    "Who do you text the most, and what do you mostly talk about?",
    "What's the longest you've gone without showering, and why?",
    "Have you ever purposely ignored someone's message?",
    "What's the most embarrassing nickname someone has given you?",
    "What's a fight you had that you now realise you were completely wrong about?",
    "Have you ever lied about dietary restrictions to avoid eating something?",
    "What's the most embarrassing account you follow on social media?",
    "Do you follow Donald Trump, Elon Musk, etc on any social media platform?",
    "Who do you like most in this server?",
    "What is the weirdest conversation you've screenshotted?",
    "Have you ever cheated on a test or assignment?",
    "What's the most you've ever procrastinated and what happened?",
    "Have you ever done something illegal? What was it?",
    "What's the closest you've come to getting seriously hurt doing something stupid?",
    "What's an opinion you have that most people around you would disagree with?",
    "What's a popular movie or show that you think is genuinely overrated?",
    "Which person in your life do you secretly find most annoying?",
    "If you had to eliminate one holiday forever, which one and why?",
    "If you could read one person's mind, whose would it be and why?",
    "If you had to delete all your social media except one platform, which would you keep?",
    "If you had to live without one of your senses, which would you choose?",
    "If you could erase one memory, what would it be?",
    "If you could relive one day of your life, which would it be?",
    "What three things would you bring to a deserted island?",
    "If you had a week left to live, who's the first person you'd call?",
    "If you had to swap bodies with someone in this room for a month, who would you choose?",
    "Have you ever fallen asleep in public? If so, where?",
    "Have you ever drunk-texted someone and regretted it?",
    "What fictional universe would you least want to actually live in?",
    "Have you ever told a white lie that got seriously out of hand?",
    "Have you ever accidentally sent a message to the wrong person?",
    "Do you believe in any supernatural entity?",
    "Have you ever peed in a pool?",
    "Name something you do only when you're alone",
    "Do you prefer cuddling or making out?",
    "What is the first thing you notice about a person?",
    "What's something you're attracted to that most people aren't?",
    "Describe me (Contore) using three hot sexy words",
    "Have you ever had a crush on a friend?",
    "Do you think you're a good flirt?",
    "What's one thing you struggle with in relationships?",
    "Who here would you let check your phone?",
    "Do you believe in soulmates",
    "If you became the opposite gender for a day, what would you do?",
    "If you could travel anywhere in the world, where would you want to go?",
    "Who here gave you the silliest first impression, and what was it?",
    "What is one thing you would change about your physical appearance if you could?",
    "Do you sleep with a stuffed animal?",
    "Who are you most jealous of?",
    "If you could be invisible for a day, what's the first thing you would do?",
    "If you could pick one superpower, which one would it be and why?",
    "What's one thing you love most about yourself?",
    "If you could be any fictional character, who would you choose?",
    "If you were marooned on an island with just a single person, who would you like it to be?",
    "If you could have anything in the world, what would it be?",
    "What do you like the most about everyone here?",
    "What's your favorite song?",
    "List your three most favorite colours",
    "If you met a genie, what would your three wishes be?",
    "If you could write anyone on Earth in for President of the United States, who would it be and why?",
    "What's one thing you'd do if you knew there'd be no consequences?",
    "How many times a week do you wear the same pants?",
    "How often do you shower/bathe?",
    "What is the youngest age partner you'd date?",
    "Which of your family members annoys you the most and why?",
    "Who would you hate to see naked?",
    "Would you break up with your partner for $million?",
    "If you had to pick one person here to be your significant other til the end of times, who would it be?",
    "Name one thing you'd change about every person in this room",
    "What's one thing in your life you wish you could change?",
    "If you could date two people at once, would you do it? If so, who?",
    "What's something that overwhelms you?",
    "What's your number? 😏😏😏😏",
    "Who in this server would you list as your emergency contact?",
    "If I shown a blacklight in your room, how much of a goner would you be?",
    "Have you ever sent a sext?",
    "Have you ever sent a sext to the wrong person? Who?",
    "What do you want on your tombstone?",
    "How many languages do you know? Which?",
    "Who's the most surprising person to ever slide into your DMs?",
    "Is there anything non-family-related that you would literally die for?",
    "Would you ever consider being a nudist? Asking for a friend, of course",
    "What do you think happens when you die?",
    "Would you marry someone rich even if you weren't in love with them?",
    "How many countries have you been to?",
    "Would you volunteer as a human sacrifice for the dark ritual of anyone in this server?",
    "Have you ever had a near-death experience?",
    "If you could pick one person to have a near-death experience, who would it be and why?",
    "What's a skill you wish you had?",
    "Have you ever pissed in the shower?",
    "Who in this room would you trust with your life the least?",
    "What was your most humbling moment?",
    "What are you most proud of in your life?",
    "What do you think would be a good truth or dare question?",
    "Are you in any 18+ NSFW Discord servers?",
    "What's your weirdest kink?",
    "Do Americans drive you into a murderous rage? If not, then do French people do so?",
    "Are you... secretly gay?",
    "Are you secretly trans?",
    "What's your favourite food?",
    "What's your dream car?",
    "What's your favourite movie?",
    "What's your favourite book?",
    "If you could become the absolute ruler of any country, which one would it be?",
    "Which country do you desire the citizenship of most?",
    "Are you at all patriotic?",
    "Do you believe in human rights?",
    "How much money would it take for you to agree to end someone's life? Would it be moral to do so, in order to put that money towards saving more lives in the future?",
    "Do you think yourself stupid?",
    "What's your political ideology?",
    "Are you a communist, socialist, or capitalist?",
    "If you had a button that killed every person on the planet with more than $1bn dollars in net worth, would you press it? Would you still press it if it killed your parents too?",
    "Do you think Alfred is skilled at programimng?",
    "Who do you believe is the most intelligent person here?",
    "Have you ever downloaded a virus onto your computer trying to look at pornographic content?",
    "On a scale of 1 to 10, how freaky would you rate your pubescent self?",
    "Do you think you should be a moderator in this server?",
    "Are you sexually attracted to anyone here?",
    "Which person here do you wanna meet up with most IRL?",
    "Do you own any pets? If so, what species are they and what are their names? If not, do you want to own pets in the future?",
    "Have you ever almost seriously harmed someone on accident?",
    "Do you think people are scared of you?",
    "If you had to kiss one person here on the lips, French-style, who would it be?",
    "How much wood would a woodchuck chuck if a woodchuck could chuck wood?",
    "What is the cringiest phase you ever went through (e.g., rawr xD emo, horse girl, alpha gamer, indie poet)?",
    "If this server was getting banned tomorrow and you could only save three people to bring to a new server, who are they?",
    "Have you ever scrolled through someone in this room's post history just to find something to judge them for?",
    "If you had to blame one person in this server for a crime you committed, who are you throwing under the bus?",
    "If Alfred and Contore got into a physical fistfight, who do you think would actually win and why?",
    "Have you ever left a group chat because you couldn't stand one specific person, only to join back later? Who was it?",
    "If you had to pick one person here to live in your basement for a year, rent-free, who are you tolerating?",
    "If this Discord server was a trashy reality TV show, who would be voted off the island first, and who would be edited to look like the absolute villain?",
    "What is the absolute worst piece of advice you have ever confidently given someone while pretending to know what you were talking about?",
    "If your entire internet search and browsing history from when you were 13 to 15 years old was projected on a screen for your family to see, what specific query would require the most frantic explanation?",
    "If an advanced alien civilization landed tomorrow and offered to take you across the galaxy to explore the cosmos, but you could never communicate with anyone on Earth ever again, would you step onto the ship?",
    "If this server got leaked to the public, who do you think would have the most explaining to do?",
    "What is the weirdest or most specific turn-off that instantly ruins someone for you?",
    "If you could permanently mute one person in this server in real life, who would it be?",
    "Which two people here do you ship the most?",
    "Which two people here do you think would make the worst couple?",
    "Is there anyone in this server you're genuinely considering pursuing a relationship with, if they'd agree?"
]

DARES = [
    "Call the first online person in your friends list and sing them happy birthday",
    "Narrate the next five minutes of your life as if it were a nature documentary",
    "Tell the most profound story you can manage using only one-syllable words",
    "Do 50 jumping jacks right now",
    "Do 50 burpees right now",
    "Say that you hate Alfred",
    "Stand on one leg for the next 60 seconds",
    "Hold a plank position for the next 3 minutes",
    "Text your mum or dad \"I have something to confess\"",
    "Send a voice message to the first offline person in your friends list saying \"I've been thinking about you a lot lately\"",
    "Change your phone wallpaper to a photo of someone in this server for the next week",
    "Send a message to the fifth person in your DMs list a three word summary of them",
    "Write a haiku about the last person (who is not you) who sent a message",
    "Speak only as a stereotypical wizened one for the next 10 minutes",
    "Do your best attempt to describe the color blue",
    "Pretend you're a game show host and interview the last person (who is not you) who talked in chat",
    "Say \"red sorry, yellow lorry\" out loud ten times fast ten times",
    "Let someone (who is not you) pick a word that you must find a use for naturally in the next three messages you send",
    "Pick someone (who is not you) and do whatever they tell you to for the rest of the day",
    "Change your profile picture to whatever the group decides for the next week",
    "Compliment every single person in the chat, sincerely and specifically",
    "Show the chat the last DM you sent",
    "Speak like a catgirl for the next ten minutes",
    "Greet your parents (or whoever you're living with currently) as though you haven't seen them in 10 years",
    "Say the word \"fluffy\" in every sentence for the next two minutes",
    "Describe the plot of your favorite movie to the last person you DM'd in the style of a 1-star Yelp review",
    "Give everyone here an animal nickname and explain your reasoning (or don't)",
    "Speak in rhymes for the next five messages",
    "Use only links to Spotify songs to communicate for the next two minutes",
    "Invent a new word, explain what it means, and then use it in your next three messages",
    "Set your status to whatever the group decides for the next 24 hours",
    "Change your global display name to whatever the group decides for the rest of the day",
    "Send a flirty text to your crush, partner, or closest friend",
    "DM one of your secrets to someone currently active here",
    "Read out the last five things on your browser's search history",
    "Send someone here a flirty message using only emojis",
    "Steal the body of the next person you see",
    "Rate the personality of everyone here from 1 to 10",
    "Wear your socks on your hands for the next 15 minutes",
    "Try to say the alphabet backward out loud",
    "Say you HATE the person you most recently DM'd and that you HATE the way they eat bananas",
    "Speak like a caveman for the next 5 minutes",
    "Tell the last person you talked to in DMs that you've secretly had a crush on them for years",
    "Ask to kiss one of your IRL friends of the opposite sex (ignore this if you have no friends)",
    "Tell Alfred how rakishly charming he is (unbiased)",
    "Rate everyone here on a scale of 1 to 10 for kinkiness",
    "Match profile pictures with someone here of your choosing",
    "For the next 10 minutes, you must CAPITALIZE EVERY SINGLE WORD you type",
    "Send a voice note to the chat screaming your favorite movie quote with zero context and no explanation afterward",
    "Change your Discord status to a vague 2000s emo lyric for the next 24 hours",
    "Send a DM to a random person consisting entirely of a keyboard smash (e.g., asdfghjkl), and if they question it, fiercely defend it as a deep, philosophical statement",
    "Write a four-line poem praising Contore's supreme elegance and send it to all of your friends",
    "Find the oldest photo currently saved on your phone and post it",
    "Post a random photo from your gallery",
    "Text a random contact in your phone: \"It's done. They don't suspect a thing.\" Screenshot the response if they reply before the game ends",
    "Let the person who spoke right before you choose your new Discord nickname for the next 24 hours",
    "React with a 💀 emoji to every single message sent in the chat for the next 10 minutes, no matter how serious or lighthearted the topic is",
    "Go onto your social media of choice and leave a genuinely nice, hypeman-style comment on the most recent post of a celebrity you absolutely despise",
    "Post a picture of your feet to the chat",
    "The next time you go outside, wear thigh-highs and a miniskirt, if available",
    "Change your server nickname to \"[User]'s Footstool\", where [User] is the name of the last person who talked that wasn't you, for the next 24 hours",
    "Drop a controversial opinion in another server (e.g., \"Cereal is better with water\"), and then refuse to elaborate or defend it",
    "Go to the oldest active DM in your list and send a single, contextless \"?\" and do not reply when they text back",
    "Draw a terrible 30-second MS Paint portrait of someone in this server and set it as your profile picture for the next two hours",
    "Text a random contact in your phone \"They know. Run.\" and screenshot their reaction to show the chat",
    "Reveal what you did on the eve of 1999",
    "Choose one person currently active in chat and then either heads or tails, then flip a coin; if you guessed correctly, you get to dictate what that person does for the next 10 minutes; if wrong, they get to dictate what you do for the next 20 minutes"
]

WOULD_YOU_RATHER: list[tuple[str, str] | tuple[str, str, str]] = [
    ("10 minutes late", "45 minutes early", "always be"),
    ("music", "TV", "live without"),
    ("whisper", "shout", "always have to"),
    ("cold", "hot", "always be"),
    ("sleep again (but always rested)", "eat or drink again (but always satiated & slaked)", "never be able to"),
    ("fly", "breathe underwater", "be able to"),
    ("money", "time", "have unlimited"),
    ("how you die", "when & where you die", "know (with the possibility of preventing)"),
    ("lose all your memories from birth to now", "never be able to make new memories"),
    ("smartest person in every room", "most attractive person in every room", "be the"),
    ("a nobody while alive but famous after death", "famous while alive but forgotten after death", "be"),
    ("speak every language", "play every instrument", "be able to"),
    ("have an eidetic memory", "be able to forget anything on demand"),
    ("teleport anywhere you've been to before", "read minds", "be able to"),
    ("turn invisible", "fly", "be able to"),
    ("super strength", "super speed", "have"),
    ("stop time (you & everything you touch can move about still)", "rewind time", "be able to"),
    ("heal others but not yourself", "heal yourself but not others", "be able to"),
    ("talk to animals", "speak every human language", "be able to"),
    ("knowledge", "power (sans the power to gain knowledge)", "have unlimited"),
    ("learn everything 100x faster", "instantly master 100 things (not including the art of learning itself)"),
    ("get hurt from falling down again", "have to breathe again", "never"),
    ("house", "form of transport", "have your ideal"),
    ("physically", "sexually", "age only"),
    ("age past 12 physically", "mentally mature past 25", "never"),
    ("one close best friend", "a dozen regular friends", "have"),
    ("always know when someone is lying to you", "be able to lie (within reason) without anyone ever knowing"),
    ("loved but never respected", "respected but never loved", "be"),
    ("know everyone's deepest secret", "have everyone know yours"),
    ("text, but you have a strict limit of 50 characters per message and able to send 3 messages every 20 seconds", "call", "only be able to"),
    ("have your best friend read all your thoughts for a day", "read all your best friend's thoughts for a day"),
    ("never argues but never apologises", "argues constantly but always makes up", "be with someone who"),
    ("eat the same meal every day for a year", "never eat the same meal twice ever again but be able to eat whatever you want for free"),
    ("find true love but be broke forever", "be a billionaire but remain perpetually single"),
    ("social media forever", "showering/bathing for two weeks", "give up"),
    ("green foods", "red foods", "only be able to eat"),
    ("speak your every passing thought out loud", "never be able to speak at all"),
    ("go to prison for four years for something you didn't do", "get away with something terrible you did do (like murder)"),
    ("on a desert island alone for a year", "with someone you absolutely cannot stand", "be stranded"),
    ("said out loud made public", "searched online made public", "have everything you've ever"),
    ("haunted by a friendly but annoying ghost only you can see", "occasionally visited by a terrifying but harmless one everyone can see", "be"),
    ("your mood is visible as a colour above your head", "everyone's net worth floats above theirs", "live in a world where"),
    ("fully conscious, alert, and trapped alone with your thoughts for the first 3 years after you die", "unaware for the last 5 years of your life", "be"),
    ("create world peace but cause a global recession", "end poverty but cause one giant war", "be able to"),
    ("always do the right thing but occasionally be misunderstood", "occasionally do the wrong thing but always be seen as a good person"),
    ("the Truth but be alone", "comfortable lies but be surrounded by people who love you", "have"),
    ("justice without mercy", "mercy without justice", "have"),
    ("have every fish you carry make you 1% stronger", "give one person a crippling cocaine addiction (not yourself)"),
    ("gain $1,000,000,000", "cause 1,000,000,000,000 people to stub their toe within the next fifteen seconds"),
    ("be trapped in a lift with Alfred for an hour", "give a public speech in front of a thousand people"),
    ("Pixie plan your entire social life for a week", "Kiro narrate your daily routine for a month", "have"),
    ("lose admin in every server you're in", "become a Discord moderator in truth"),
    ("have your entire message history in this server read aloud in a courtroom", "have your Google search history printed and mailed to your family"),
    ("by something your 8 year old self came up with", "with your Discord username", "have your real name in all official documents replaced"),
    ("Tarik write your wedding vows", "Rina manage your personal bank account for a year", "have"),
    ("a world with no suffering but also no joy", "one with both in full", "live in"),
    ("mermaid", "vampire", "be a"),
    ("weirdest rabbit hole", "most fascinating rabbit hole", "go down the"),
    ("mayonnaise", "a woman", "be"),
    ("make a random person happy", "not"),
    ("rather big (keeping your current proportions)", "very small (keeping your current proportions)", "be"),
    ("very ugly", "very stupid", "be"),
    ("stung by a hundred bees every month for the rest of your life", "bald for the rest of your life", "be"),
    ("fall hopelessly in love with me (Alfred 😏😏😏😏)", "go to Hell and burn forever (bad)"),
    ("be humiliated in school after proposing to your crush and getting rejected", "get caught by your parents watching questionable anime"),
    ("vertically by 1 inch every day", "horizontally by 1 centimeter every day", "expand"),
    ("milk", "blood", "sweat"),
    ("suck someone's ass", "have your ass sucked"),
    ("feel as though you're about to sneeze", "have an itch you can never scratch", "always"),
    ("nose by 90 degrees", "ass by 90 degrees", "rotate your"),
    ("hot women", "lizards", "be in a room full of"),
    ("hot men", "lizards", "be in a room full of"),
    ("hot Btalos", "lizards", "be in a room full of"),
    ("your own arm and feel no pain", "someone else's arm but they feel all the pain", "eat"),
    ("punched daily", "shot in the foot at a random point in time every year", "be"),
    ("bones in your fruit", "apricot cores in your meat", "have"),
    ("4 inches tall (keeping your current proportions)", "8 feet tall (keeping your current proportions, ignoring the square-cube law)", "be"),
    ("dick and eat a cake", "cake and eat a dick", "sit on a"),
    ("fuck a dog with only you and the dog knowing", "not fuck a dog but have everyone in the world think you did"),
    ("best friend walk in on you fucking a dog", "dog walk in on you fucking your best friend", "have your"),
    ("1 bloodthirsty German shepherd", "10 bloodthirsty cats in an enclosed space", "fight"),
    ("extreme cold (<-30 deg Celsius)", "extreme heat (>500 deg Celsius)", "die to"),
    ("cum in the sink", "sink in the cum"),
    ("admit to what you did on the eve of 1999", "be a fugitive wanted by the entire world"),
    ("on your vision", "on your hearing", "have a permanent, uncurable 3-second delay"),
    ("whenever someone talks about you behind your back", "what they actually think of you when looking at your face", "instantly know"),
    ("use light mode on every app for the rest of your life", "only use the internet with a 3G connection speed"),
    ("profile picture permanently set to a horribly unflattering childhood photo", "custom status permanently set to your most embarrassing text message", "have your"),
    ("teeth feel fuzzy and flaccid forever", "fingernails feel like wet cardboard", "have your"),
    ("state your bank account balance out loud every time you greet someone", "bark like a dog before every meal"),
    ("kangaroo wearing boxing gloves", "giraffe that is permanently blindfolded", "fight a"),
    ("sweat taste exactly like maple syrup (sticky included)", "tears taste exactly like hot sauce", "have your"),
    ("feet for hands", "hands for feet", "have"),
    ("have any socks you wear always be wet", "always feel like you have a single eyelash in your right eye"),
    ("third eye on the palm of your hand that you cannot close", "mouth on the front of your neck", "have a"),
    ("medieval fantasy world but be unable to use magic", "sci-fi world but be unable to use technology", "live in a"),
    ("die painlessly immediately", "die a long, slow, excruciating death 10 years from now")
]

EXPIRY_SECONDS = 3600


class RecentTracker:
    def __init__(self):
        self._seen: dict[int, dict[str, list[tuple[str, float]]]] = defaultdict(
            lambda: {"truth": [], "dare": [], "would_you_rather": []}
        )

    def _purge(self, channel_id: int):
        cutoff = time.monotonic() - EXPIRY_SECONDS
        for key in ("truth", "dare", "would_you_rather"):
            self._seen[channel_id][key] = [
                (q, t) for q, t in self._seen[channel_id][key] if t > cutoff
            ]

    def pick(self, channel_id: int, kind: Literal["truth", "dare", "would_you_rather"], guild_id: int | None = None):
        self._purge(channel_id)
        pool = TRUTHS if kind == "truth" else DARES if kind == "dare" else WOULD_YOU_RATHER

        def _visible(entry):
            if isinstance(entry, dict):
                gid = entry.get("guild_id")
                return gid is None or gid == guild_id
            return True
        pool = [q for q in pool if _visible(q)]

        recent = {tuple(q) for q, _ in self._seen[channel_id][kind]}
        available = [q for q in pool if tuple(extract_question(q)) not in recent]
        was_reset = False
        if not available:
            self._seen[channel_id][kind].clear()
            available = pool
            was_reset = True

        entry = random.choice(available)
        self._seen[channel_id][kind].append((extract_question(entry), time.monotonic()))

        return entry, was_reset


TRACKER = RecentTracker()


# region submission review pipeline
def _new_sub_id() -> str:
    return uuid4().hex[:12]


_TYPE_LISTS: dict[str, list] = {
    "Truth": TRUTHS,
    "Dare": DARES,
    "Would You Rather": WOULD_YOU_RATHER
}


def make_submission_entry(sub: dict) -> dict:
    return {
        "sub_id": sub["sub_id"],
        "question": sub["question"],
        "submitted_by": sub["submitter_name"],
        "submitter_id": sub["submitter_id"],
        "guild_id": sub.get("guild_id")
    }


def extract_question(entry) -> str:
    if isinstance(entry, dict):
        return entry["question"]
    return entry


async def extract_submitter(bot: commands.Bot, entry, guild: Guild | None) -> tuple[str | None, str | None]:
    if isinstance(entry, dict):
        uid = entry.get("submitter_id")
        user = uid and (guild and (guild.get_member(uid) or await guild.fetch_member(uid)) or bot.get_user(uid) or await bot.fetch_user(uid))
        return (user and user.display_name or entry.get("submitted_by"), user and user.display_avatar.url)
    return (None, None)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _submission_question_str(sub: dict) -> str:
    q = sub["question"]
    if sub["type"] == "Would You Rather" and isinstance(q, (list, tuple)) and len(q) >= 2:
        preamble = q[2] if len(q) > 2 else ""
        prefix = f"Would you rather {preamble} " if preamble else "Would you rather "
        return f"{prefix}{q[0]} or {q[1]}?"
    return str(q)


def _is_duplicate(kind: str, entry) -> bool:
    question = extract_question(entry)

    if kind == "Would You Rather":
        if isinstance(question, str):
            wyr = [_normalize(p) for p in _format_wyr(question)]
        elif isinstance(question, (list, tuple)) and len(question) >= 2:
            wyr = [_normalize(str(p)) for p in question]
        else:
            return False

        wyr_a, wyr_b = wyr[0], wyr[1]
        wyr_p = wyr[2] if len(wyr) > 2 else ""

        for q in _TYPE_LISTS[kind]:
            eq = extract_question(q)
            if isinstance(eq, str):
                ep = [_normalize(p) for p in _format_wyr(eq)]
            elif isinstance(eq, (list, tuple)) and len(eq) >= 2:
                ep = [_normalize(str(p)) for p in eq]
            else:
                continue

            ep_a, ep_b = ep[0], ep[1]
            ep_p = ep[2] if len(ep) > 2 else ""

            opts_match = (
                (ep_a == wyr_a and ep_b == wyr_b)
                or (ep_a == wyr_b and ep_b == wyr_a)
            )
            if opts_match and ep_p == wyr_p:
                return True

        return False

    if isinstance(question, (list, tuple)):
        return False
    norm = _normalize(question)
    return any(
        _normalize(extract_question(q)) == norm
        for q in _TYPE_LISTS[kind]
        if not isinstance(extract_question(q), (list, tuple))
    )


async def load_submissions(bot: commands.Bot):
    for sub_id, sub in (await db.db_get_all_submissions()).items():
        if sub["status"] != "approved":
            if sub["status"] == "pending":
                bot.add_view(SubmissionReviewView(sub_id))
            continue
        _TYPE_LISTS[sub["type"]].append(
            make_submission_entry(sub)
        )


def build_submission_embed(
    sub: dict,
    status_label: str = "⏳ Pending review",
    color: int = 0xFFA500
) -> Embed:
    q = sub["question"]
    if sub["type"] == "Would You Rather" and isinstance(q, (list, tuple)) and len(q) >= 2:
        preamble = q[2] if len(q) > 2 else ""
        prefix = f"Would you rather {preamble} " if preamble else "Would you rather "
        display_q = f"{prefix}**{q[0]}** or **{q[1]}**?"
    else:
        display_q = str(q)

    embed = Embed(
        title=f"Submission - {sub['type']}",
        description=f">>> {display_q}",
        color=color
    )
    embed.add_field(
        name="Submitted by",
        value=f"<@{sub['submitter_id']}> ({descape(sub['submitter_name'])})",
        inline=True
    )
    embed.add_field(name="Status", value=status_label, inline=True)
    embed.set_footer(text=f"Submission ID: {sub['sub_id']}")
    return embed


async def _validate_wyr(question: str, ctx: Interaction) -> bool:
    question = question.strip()
    if not question.lower().startswith("would you rather"):
        await ctx.response.send_message("Would You Rather questions must start with \"would you rather\"", ephemeral=True)
        return False

    ors = len(re.findall(r"(,\s*)?\bor\b", question, re.IGNORECASE))
    if ors > 1:
        await ctx.response.send_message("Please only include a single \"or\" within your question", ephemeral=True)
        return False
    elif ors < 1:
        await ctx.response.send_message("Please include an \"or\" within your question to delineate the two options", ephemeral=True)
        return False
    return True


def _format_wyr(question: str) -> tuple[str, str] | tuple[str, str, str]:
    if question.endswith("?"):
        question = question[:-1]
    if question.lower().startswith("would you rather"):
        question = question[16:]
    question = question.strip()

    parts = re.split(r"(?:,\s*)?\bor\b", question, maxsplit=1, flags=re.IGNORECASE)
    opt1 = parts[0].strip()
    opt2 = re.sub(r"^as\s+", "", parts[1].strip(), flags=re.IGNORECASE).strip()

    words1 = opt1.split()
    words2 = opt2.split()

    w1, w2 = words1[:], words2[:]
    prefix_words: list[str] = []
    while (
        w1 and w2
        and w1[0].lower() == w2[0].lower()
        and w1[0].lower() not in ("very", "die", "who", "the", "a")
    ):
        prefix_words.append(w1.pop(0))
        w2.pop(0)
    preamble1 = " ".join(prefix_words)
    u1_m1, u2_m1 = " ".join(w1), " ".join(w2)

    n2 = len(words2)
    if n2 <= 3 and len(words1) >= n2 * 2:
        preamble2 = " ".join(words1[:-n2])
        u1_m2 = " ".join(words1[-n2:])
        u2_m2 = opt2
    else:
        preamble2 = ""
        u1_m2, u2_m2 = opt1, opt2

    if len(preamble2) > len(preamble1):
        preamble, u1, u2 = preamble2, u1_m2, u2_m2
    elif preamble1:
        preamble, u1, u2 = preamble1, u1_m1, u2_m1
    else:
        return (opt1, opt2)

    return (u1, u2, preamble)


def _locked_view(sub_id: str) -> View:
    v = View(timeout=None)
    v.add_item(Button(label="Approve", style=ButtonStyle.green, disabled=True, custom_id=f"sub:locked_a:{sub_id}", emoji="✅"))
    v.add_item(Button(label="Revise", style=ButtonStyle.blurple, disabled=True, custom_id=f"sub:locked_r:{sub_id}", emoji="✏️"))
    v.add_item(Button(label="Deny", style=ButtonStyle.danger, disabled=True, custom_id=f"sub:locked_d:{sub_id}", emoji="❌"))
    v.add_item(Button(label="Toggle Server-Only", style=ButtonStyle.gray, disabled=True, custom_id=f"sub:locked_s:{sub_id}", emoji="🌐"))
    return v


class SubmissionReviseModal(Modal, title="Revise Submission"):
    def __init__(self, sub_id: str, current_question):
        super().__init__()
        self.sub_id = sub_id

        if isinstance(current_question, (list, tuple)) and len(current_question) >= 2:
            preamble = current_question[2] if len(current_question) > 2 else ""
            prefix = f"Would you rather {preamble} " if preamble else "Would you rather "
            display = f"{prefix}{current_question[0]} or {current_question[1]}?"
        else:
            display = str(current_question)

        self.question_input = TextInput(
            label="Revised question",
            style=TextStyle.paragraph,
            default=display,
            placeholder="Write a question...",
            min_length=1,
            max_length=500
        )
        self.add_item(self.question_input)

    async def on_submit(self, ctx: Interaction):
        sub = await db.db_get_submission(self.sub_id)
        if sub is None:
            await ctx.response.send_message("Submission not found", ephemeral=True)
            return
        if sub["status"] != "pending":
            await ctx.response.send_message(
                "This submission has already been reviewed", ephemeral=True
            )
            return

        if sub["type"] == "Would You Rather":
            if not await _validate_wyr(self.question_input.value, ctx):
                return

        original_question = sub["question"]
        new_question = self.question_input.value.strip()
        await db.db_update_submission(self.sub_id, question=new_question, status="approved")
        sub["question"] = new_question

        await ctx.response.edit_message(
            embed=build_submission_embed(sub, "✅ Approved (revised)", 0x57F287),
            view=_locked_view(self.sub_id)
        )

        try:
            user = ctx.client.get_user(sub["submitter_id"]) or await ctx.client.fetch_user(sub["submitter_id"])
            revised = sub["question"] != original_question
            dm_parts = [
                f"✅ Your **{sub['type']}** submission was **approved** and added to the question pool!"
            ]
            if revised:
                dm_parts.append(
                    f"It was lightly edited before approval:\n> {_submission_question_str(sub)}"
                )
            else:
                dm_parts.append(f"> {_submission_question_str(sub)}")
            await user.send("\n".join(dm_parts))
        except Exception:
            pass

        # Only run _format_wyr if the question is still a raw string; pre-parsed
        # list submissions are already in the right shape.
        if sub["type"] == "Would You Rather" and isinstance(sub["question"], str):
            sub["question"] = _format_wyr(sub["question"])

        _TYPE_LISTS[sub["type"]].append(make_submission_entry(sub))


class SubmissionReviewView(View):
    def __init__(self, sub_id: str):
        super().__init__(timeout=None)
        approve_btn = Button(
            label="Approve", style=ButtonStyle.green,
            custom_id=f"sub:approve:{sub_id}", emoji="✅"
        )
        revise_btn = Button(
            label="Revise", style=ButtonStyle.blurple,
            custom_id=f"sub:revise:{sub_id}", emoji="✏️"
        )
        deny_btn = Button(
            label="Deny", style=ButtonStyle.danger,
            custom_id=f"sub:deny:{sub_id}", emoji="❌"
        )
        scope_btn = Button(
            label="Toggle Server-Only", style=ButtonStyle.gray,
            custom_id=f"sub:scope:{sub_id}", emoji="🌐"
        )
        approve_btn.callback = self._handle_approve
        revise_btn.callback = self._handle_revise
        deny_btn.callback = self._handle_deny
        scope_btn.callback = self._handle_toggle_scope
        self.add_item(approve_btn)
        self.add_item(revise_btn)
        self.add_item(deny_btn)
        self.add_item(scope_btn)

    async def _handle_approve(self, ctx: Interaction): await self._review(ctx, approved=True)
    async def _handle_deny(self, ctx: Interaction): await self._review(ctx, approved=False)

    async def _handle_revise(self, ctx: Interaction):
        if ctx.user.id != OWNER_ID:
            await ctx.response.send_message(
                "Only **Contore**'s acting owner can review submissions.", ephemeral=True
            )
            return
        sub_id = ctx.data["custom_id"].split(":", 2)[2]
        sub = await db.db_get_submission(sub_id)
        if sub is None:
            await ctx.response.send_message("Submission not found.", ephemeral=True)
            return
        if sub["status"] != "pending":
            await ctx.response.send_message(
                "This submission has already been reviewed.", ephemeral=True
            )
            return

        if sub["type"] == "Would You Rather":
            q = sub["question"]
            if isinstance(q, (list, tuple)) and len(q) >= 2:
                option_a, option_b = q[0], q[1]
                preamble = q[2] if len(q) > 2 else ""
            else:
                parsed = _format_wyr(str(q))
                option_a, option_b = parsed[0], parsed[1]
                preamble = parsed[2] if len(parsed) > 2 else ""

            view = ReviewWyrPreviewView(
                sub_id, option_a, option_b, preamble,
                ctx.user.id, ctx.message
            )
            embed = _build_wyr_preview_embed(option_a, option_b, preamble)
            await ctx.response.send_message(embed=embed, view=view, ephemeral=True)
            view.message = await ctx.original_response()
        else:
            await ctx.response.send_modal(SubmissionReviseModal(sub_id, sub["question"]))

    async def _handle_toggle_scope(self, ctx: Interaction):
        if ctx.user.id != OWNER_ID:
            await ctx.response.send_message(
                "Only **Contore**'s acting owner can review submissions.", ephemeral=True
            )
            return

        sub_id = ctx.data["custom_id"].split(":", 2)[2]
        sub = await db.db_get_submission(sub_id)
        if sub is None or sub["status"] != "pending":
            await ctx.response.send_message("Submission not found or already reviewed.", ephemeral=True)
            return

        origin = sub.get("origin_guild_id")
        if not origin:
            await ctx.response.send_message(
                "This submission wasn't submitted from inside a server, so it can't be made server-specific.",
                ephemeral=True)
            return

        new_scope = None if sub.get("guild_id") else origin
        await db.db_update_submission(sub_id, guild_id=new_scope)
        sub["guild_id"] = new_scope
        await ctx.response.edit_message(embed=build_submission_embed(sub), view=self)

    async def _review(self, ctx: Interaction, *, approved: bool):
        if ctx.user.id != OWNER_ID:
            await ctx.response.send_message(
                "Only **Contore**'s acting owner can review submissions.", ephemeral=True
            )
            return
        sub_id = ctx.data["custom_id"].split(":", 2)[2]
        sub = await db.db_get_submission(sub_id)
        if sub is None:
            await ctx.response.send_message("Submission not found.", ephemeral=True)
            return
        if sub["status"] != "pending":
            await ctx.response.send_message(
                "This submission has already been reviewed.", ephemeral=True
            )
            return

        if approved and _is_duplicate(sub["type"], sub["question"]):
            approved = False
            status_label, color = "❌ Denied (duplicate)", 0xED4245
        elif approved:
            status_label, color = "✅ Approved", 0x57F287
        else:
            status_label, color = "❌ Denied", 0xED4245

        await db.db_update_submission(sub_id, status="approved" if approved else "denied")

        await ctx.response.edit_message(
            embed=build_submission_embed(sub, status_label, color),
            view=_locked_view(sub_id)
        )

        try:
            user = ctx.client.get_user(sub["submitter_id"]) or await ctx.client.fetch_user(
                sub["submitter_id"]
            )
            q_str = _submission_question_str(sub)
            dm_text = (
                f"✅ Your **{sub['type']}** submission was **approved** and added to the question pool!\n> {q_str}"
                if approved
                else f"❌ Your **{sub['type']}** submission was **denied**.\n> {q_str}"
            )
            await user.send(dm_text)
        except Exception:
            pass

        # Only run _format_wyr for raw-string WYR questions; pre-parsed lists
        # (submitted through the preview flow) are already in the right shape.
        if approved:
            if sub["type"] == "Would You Rather" and isinstance(sub["question"], str):
                sub["question"] = _format_wyr(sub["question"])

            _TYPE_LISTS[sub["type"]].append(make_submission_entry(sub))


async def _handle_submission(ctx: Interaction, kind: str, question: str, server_specific: bool = False):
    question = question.strip()
    if _is_duplicate(kind, question):
        await ctx.response.send_message(
            f"❌ That **{kind}** question already exists in the pool. Try something a little different!",
            ephemeral=True)
        return
    if server_specific and ctx.guild_id is None:
        await ctx.response.send_message(
            "You can only mark a submission as server-specific from inside a server.", ephemeral=True)
        return

    sub_id = _new_sub_id()
    sub = {
        "sub_id": sub_id, "type": kind, "question": question,
        "submitter_id": ctx.user.id, "submitter_name": str(ctx.user),
        "message_id": None, "status": "pending",
        "guild_id": ctx.guild_id if server_specific else None,
        "origin_guild_id": ctx.guild_id
    }
    review_channel = (
        ctx.client.get_channel(SUBMISSION_REVIEW_CHANNEL_ID)
        or await ctx.client.fetch_channel(SUBMISSION_REVIEW_CHANNEL_ID)
    )
    view = SubmissionReviewView(sub_id)
    embed = build_submission_embed(sub)
    msg = await review_channel.send(embed=embed, view=view)
    sub["message_id"] = msg.id
    await db.db_save_submission(sub)
    await ctx.response.send_message(
        f"✅ Your **{sub['type']}** submission has been sent for review. "
        f"You'll receive a DM once it's been approved or denied.", ephemeral=True)


def _build_wyr_preview_embed(option_a: str, option_b: str, preamble: str) -> Embed:
    """Build a preview embed that mirrors the live WYR embed layout so the
    submitter can see exactly how their question will be displayed."""
    embed = Embed(
        title=f"Would you rather{' ' + preamble if preamble else ''}...",
        description="*Preview - does this look right?*",
        color=0x5865F2
    )
    embed.add_field(name=f"🅰\u200b  {option_a}", value="\u200b", inline=False)
    embed.add_field(name=f"🅱\u200b  {option_b}", value="\u200b", inline=False)
    return embed


class WyrEditModal(Modal, title="Edit Would You Rather"):
    """Three-field modal for separately adjusting the shared preamble,
    Option A, and Option B of a WYR question. On submit the parent
    view (WyrPreviewView / ReviewWyrPreviewView / EditApprovedWyrView) is
    updated in-place and the ephemeral preview message is refreshed."""

    def __init__(self, option_a: str, option_b: str, preamble: str, view):
        super().__init__()
        self._view = view

        self.preamble_input = TextInput(
            label="Shared preamble (optional)",
            style=TextStyle.short,
            default=preamble or "",
            placeholder="e.g. be known for the rest of your life as",
            required=False,
            max_length=200
        )
        self.option_a_input = TextInput(
            label="Option A",
            style=TextStyle.short,
            default=option_a,
            placeholder="First option",
            min_length=1,
            max_length=200
        )
        self.option_b_input = TextInput(
            label="Option B",
            style=TextStyle.short,
            default=option_b,
            placeholder="Second option",
            min_length=1,
            max_length=200
        )
        self.add_item(self.preamble_input)
        self.add_item(self.option_a_input)
        self.add_item(self.option_b_input)

    async def on_submit(self, ctx: Interaction):
        preamble = self.preamble_input.value.strip()
        a = self.option_a_input.value.strip()
        b = self.option_b_input.value.strip()

        self._view.option_a = a
        self._view.option_b = b
        self._view.preamble = preamble

        embed = _build_wyr_preview_embed(a, b, preamble)
        await ctx.response.defer()
        await self._view.message.edit(embed=embed, view=self._view)


class WyrPreviewView(View):
    """Ephemeral view shown to a submitter so they can confirm (or tweak)
    how their WYR question will be split before it enters the review
    queue. The parsed question is stored as a JSON-safe list on the
    submission so the approval flow never needs to re-run _format_wyr."""

    def __init__(self, option_a: str, option_b: str, preamble: str, submitter_id: int, guild_id: int | None = None):
        super().__init__(timeout=300)
        self.option_a = option_a
        self.option_b = option_b
        self.preamble = preamble
        self.submitter_id = submitter_id
        self.guild_id = guild_id
        self.message: Message | None = None

        confirm_btn = Button(label="Looks good!", style=ButtonStyle.green, emoji="✅")
        edit_btn = Button(label="Edit", style=ButtonStyle.blurple, emoji="✏️")
        confirm_btn.callback = self._handle_confirm
        edit_btn.callback = self._handle_edit
        self.add_item(confirm_btn)
        self.add_item(edit_btn)

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.submitter_id:
            await ctx.response.send_message(
                "This preview belongs to someone else.", ephemeral=True
            )
            return False
        return True

    async def _handle_confirm(self, ctx: Interaction):
        parsed: tuple = (
            (self.option_a, self.option_b, self.preamble)
            if self.preamble
            else (self.option_a, self.option_b)
        )

        if _is_duplicate("Would You Rather", parsed):
            await ctx.response.edit_message(
                embed=Embed(
                    description=(
                        "❌ That **Would You Rather** question already exists in the pool. "
                        "Try submitting something a little different!"
                    ),
                    color=0xED4245
                ),
                view=None
            )
            return

        sub_id = _new_sub_id()
        sub = {
            "sub_id": sub_id,
            "type": "Would You Rather",
            # Store as a list so it round-trips through JSON without becoming a string.
            "question": list(parsed),
            "submitter_id": ctx.user.id,
            "submitter_name": str(ctx.user),
            "message_id": None,
            "status": "pending",
            "guild_id": self.guild_id,
            "origin_guild_id": ctx.guild_id
        }

        review_channel = (
            ctx.client.get_channel(SUBMISSION_REVIEW_CHANNEL_ID)
            or await ctx.client.fetch_channel(SUBMISSION_REVIEW_CHANNEL_ID)
        )
        review_view = SubmissionReviewView(sub_id)
        embed = build_submission_embed(sub)
        msg = await review_channel.send(embed=embed, view=review_view)
        sub["message_id"] = msg.id
        await db.db_save_submission(sub)

        await ctx.response.edit_message(
            embed=Embed(
                description=(
                    "✅ Your **Would You Rather** submission has been sent for review. "
                    "You'll receive a DM once it's been approved or denied."
                ),
                color=0x57F287
            ),
            view=None
        )

    async def _handle_edit(self, ctx: Interaction):
        await ctx.response.send_modal(
            WyrEditModal(self.option_a, self.option_b, self.preamble, self)
        )

    async def on_timeout(self):
        if self.message and self.message.embeds and self.message.embeds[0].color.value != 0x57F287:
            try:
                await self.message.edit(
                    embed=Embed(
                        description="⏰ Submission timed out - feel free to try again.",
                        color=0x808080
                    ),
                    view=None
                )
            except Exception:
                pass


class ReviewWyrPreviewView(View):
    def __init__(self, sub_id: str, option_a: str, option_b: str, preamble: str, reviewer_id: int, review_message: Message):
        super().__init__(timeout=300)
        self.sub_id = sub_id
        self.option_a = option_a
        self.option_b = option_b
        self.preamble = preamble
        self.reviewer_id = reviewer_id
        self.review_message = review_message
        self.message: Message | None = None  # ephemeral preview

        confirm_btn = Button(label="Looks good!", style=ButtonStyle.green, emoji="✅")
        edit_btn = Button(label="Edit", style=ButtonStyle.blurple, emoji="✏️")
        confirm_btn.callback = self._handle_confirm
        edit_btn.callback = self._handle_edit
        self.add_item(confirm_btn)
        self.add_item(edit_btn)

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.reviewer_id:
            await ctx.response.send_message("This preview belongs to someone else.", ephemeral=True)
            return False
        return True

    async def _handle_confirm(self, ctx: Interaction):
        sub = await db.db_get_submission(self.sub_id)
        if sub is None:
            await ctx.response.send_message("Submission not found.", ephemeral=True)
            return
        if sub["status"] != "pending":
            await ctx.response.send_message("This submission has already been reviewed.", ephemeral=True)
            return

        parsed = (
            (self.option_a, self.option_b, self.preamble)
            if self.preamble
            else (self.option_a, self.option_b)
        )

        if _is_duplicate("Would You Rather", parsed):
            await ctx.response.edit_message(
                embed=Embed(
                    description="❌ That **Would You Rather** question already exists in the pool.",
                    color=0xED4245
                ),
                view=None
            )
            return

        original_question = sub["question"]
        new_question = list(parsed)
        await db.db_update_submission(self.sub_id, question=new_question, status="approved")
        sub["question"] = new_question

        revised = sub["question"] != (
            list(original_question) if isinstance(original_question, (list, tuple))
            else original_question
        )
        status_label = "✅ Approved (revised)" if revised else "✅ Approved"

        await self.review_message.edit(
            embed=build_submission_embed(sub, status_label, 0x57F287),
            view=_locked_view(self.sub_id)
        )
        await ctx.response.edit_message(
            embed=Embed(description="✅ Submission approved.", color=0x57F287),
            view=None
        )

        try:
            user = await ctx.client.fetch_user(sub["submitter_id"])
            dm_parts = [
                "✅ Your **Would You Rather** submission was **approved** and added to the question pool!"
            ]
            if revised:
                dm_parts.append(f"It was lightly edited before approval:\n> {_submission_question_str(sub)}")
            else:
                dm_parts.append(f"> {_submission_question_str(sub)}")
            await user.send("\n".join(dm_parts))
        except Exception:
            pass

        _TYPE_LISTS["Would You Rather"].append(make_submission_entry(sub))

    async def _handle_edit(self, ctx: Interaction):
        await ctx.response.send_modal(
            WyrEditModal(self.option_a, self.option_b, self.preamble, self)
        )

    async def on_timeout(self):
        if self.message and self.message.embeds and self.message.embeds[0].color.value != 0x57F287:
            try:
                await self.message.edit(
                    embed=Embed(description="⏰ Preview timed out.", color=0x808080),
                    view=None
                )
            except Exception:
                pass


class EditApprovedModal(Modal, title="Edit Submission"):
    def __init__(self, sub_id: str, question: str):
        super().__init__()
        self.sub_id = sub_id
        self.question_input = TextInput(
            label="Question",
            style=TextStyle.paragraph,
            default=str(question),
            min_length=1,
            max_length=500
        )
        self.add_item(self.question_input)

    async def on_submit(self, ctx: Interaction):
        sub = await db.db_get_submission(self.sub_id)
        if sub is None:
            await ctx.response.send_message("Submission not found.", ephemeral=True)
            return

        lst = _TYPE_LISTS[sub["type"]]
        for i, entry in enumerate(lst):
            if isinstance(entry, dict) and entry.get("sub_id") == self.sub_id:
                lst.pop(i)
                break

        new_question = self.question_input.value.strip()
        await db.db_update_submission(self.sub_id, question=new_question)
        sub["question"] = new_question
        lst.append(make_submission_entry(sub))

        await ctx.response.send_message("✅ Submission updated.", ephemeral=True)


class EditApprovedWyrView(View):
    def __init__(self, sub_id: str, option_a: str, option_b: str, preamble: str, editor_id: int):
        super().__init__(timeout=300)
        self.sub_id = sub_id
        self.option_a = option_a
        self.option_b = option_b
        self.preamble = preamble
        self.editor_id = editor_id
        self.message: Message | None = None

        save_btn = Button(label="Save changes", style=ButtonStyle.green, emoji="✅")
        edit_btn = Button(label="Edit", style=ButtonStyle.blurple, emoji="✏️")
        save_btn.callback = self._handle_save
        edit_btn.callback = self._handle_edit
        self.add_item(save_btn)
        self.add_item(edit_btn)

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.editor_id:
            await ctx.response.send_message("This preview belongs to someone else.", ephemeral=True)
            return False
        return True

    async def _handle_save(self, ctx: Interaction):
        sub = await db.db_get_submission(self.sub_id)
        if sub is None:
            await ctx.response.send_message("Submission not found.", ephemeral=True)
            return

        parsed = (
            (self.option_a, self.option_b, self.preamble)
            if self.preamble
            else (self.option_a, self.option_b)
        )

        lst = _TYPE_LISTS["Would You Rather"]
        for i, entry in enumerate(lst):
            if isinstance(entry, dict) and entry.get("sub_id") == self.sub_id:
                lst.pop(i)
                break

        new_question = list(parsed)
        await db.db_update_submission(self.sub_id, question=new_question)
        sub["question"] = new_question
        lst.append(make_submission_entry(sub))

        await ctx.response.edit_message(
            embed=Embed(description="✅ Submission updated.", color=0x57F287),
            view=None
        )

    async def _handle_edit(self, ctx: Interaction):
        await ctx.response.send_modal(
            WyrEditModal(self.option_a, self.option_b, self.preamble, self)
        )

    async def on_timeout(self):
        if self.message and self.message.embeds and self.message.embeds[0].color.value != 0x57F287:
            try:
                await self.message.edit(
                    embed=Embed(description="⏰ Edit timed out.", color=0x808080),
                    view=None
                )
            except Exception:
                pass
# endregion


class TruthOrDareView(View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _send_question(self, ctx: Interaction, kind: Literal["truth", "dare"]):
        channel_id = ctx.channel_id
        entry, was_reset = TRACKER.pick(channel_id, kind, ctx.guild.id if ctx.guild else None)

        question = extract_question(entry)
        submitted_by, submitted_pfp = await extract_submitter(ctx.client, entry, ctx.guild)

        embed = build_embed(
            question,
            kind,
            ctx.user,
            was_reset=was_reset,
            submitted_by=submitted_by,
            submitted_pfp=submitted_pfp
        )
        await ctx.response.send_message(embed=embed, view=self)

    @button(label="Truth", style=ButtonStyle.green, custom_id="tod:truth")
    async def truth_button(self, ctx: Interaction, _: Button):
        await self._send_question(ctx, "truth")

    @button(label="Dare", style=ButtonStyle.danger, custom_id="tod:dare")
    async def dare_button(self, ctx: Interaction, _: Button):
        await self._send_question(ctx, "dare")

    @button(emoji="🎲", style=ButtonStyle.secondary, custom_id="tod:random")
    async def random_button(self, ctx: Interaction, _: Button):
        await self._send_question(ctx, random.choice(["truth", "dare"]))


def build_embed(
    question: str, kind: Literal["truth", "dare"], requester: User, *,
    submitted_by: str | None = None, submitted_pfp: str | None = None, was_reset: bool = False
) -> Embed:
    embed = Embed(title=question, color=custom_hash(requester.id))
    embed.set_author(
        name=f"Requested by {requester.display_name}",
        icon_url=requester.display_avatar.url
    )

    footer_parts = [f"Type: {kind.upper()}"]

    if submitted_by:
        footer_parts.insert(0, f"Submitted by {submitted_by}")

    if was_reset:
        footer_parts.append("All questions used - starting fresh!")

    embed.set_footer(text="  ·  ".join(footer_parts), icon_url=submitted_pfp)
    return embed


class WouldYouRatherView(View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _record_vote(self, ctx: Interaction, choice: str):
        msg_id = ctx.message.id
        previous = _wyr_votes[msg_id].get(ctx.user.id)

        if previous == choice:
            await ctx.response.defer()
            return

        _wyr_votes[msg_id][ctx.user.id] = choice

        cmd_user = ctx.message.interaction_metadata.user
        cmd_id = cmd_user.id
        option_a, option_b, preamble, submitted_by, submitted_pfp = _wyr_options.get(msg_id, ("Option A", "Option B", "", None, None))
        embed = build_wyr_embed(
            option_a, option_b,
            _wyr_votes[msg_id],
            (ctx.guild and ctx.guild.get_member(cmd_id) or await ctx.guild.fetch_member(cmd_id)) or cmd_user,
            preamble=preamble,
            submitted_by=submitted_by,
            submitted_pfp=submitted_pfp
        )
        await ctx.response.edit_message(embed=embed)

    @button(label="Vote A", style=ButtonStyle.green, custom_id="wyr:vote_a", emoji="🅰")
    async def vote_a(self, ctx: Interaction, _: Button):
        await self._record_vote(ctx, "A")

    @button(label="Vote B", style=ButtonStyle.red, custom_id="wyr:vote_b", emoji="🅱")
    async def vote_b(self, ctx: Interaction, _: Button):
        await self._record_vote(ctx, "B")

    @button(label="Next Question", style=ButtonStyle.grey, custom_id="wyr:next", emoji="🎲")
    async def next_button(self, ctx: Interaction, _: Button):
        msg_id = ctx.message.id
        cmd_user = ctx.message.interaction_metadata.user
        cmd_id = cmd_user.id
        option_a, option_b, preamble, submitted_by, submitted_pfp = _wyr_options.pop(msg_id, ("Option A", "Option B", "", None, None))
        votes = _wyr_votes.pop(msg_id, {})

        final_embed = build_wyr_embed(
            option_a, option_b, votes,
            (ctx.guild and ctx.guild.get_member(cmd_id) or await ctx.guild.fetch_member(cmd_id)) or cmd_user,
            preamble=preamble, submitted_by=submitted_by, submitted_pfp=submitted_pfp, final=True
        )
        await ctx.response.edit_message(embed=final_embed, view=WouldYouRatherLockedView())

        entry, was_reset = TRACKER.pick(ctx.channel_id, "would_you_rather", ctx.guild.id if ctx.guild else None)

        question = extract_question(entry)
        submitted_by, submitted_pfp = await extract_submitter(ctx.client, entry, ctx.guild)
        new_a, new_b, new_preamble = unpack_wyr(question)

        new_embed = build_wyr_embed(
            new_a, new_b, {}, ctx.user,
            preamble=new_preamble, submitted_by=submitted_by, submitted_pfp=submitted_pfp, was_reset=was_reset
        )
        new_msg = await ctx.followup.send(embed=new_embed, view=WouldYouRatherView(), wait=True)
        _wyr_options[new_msg.id] = (new_a, new_b, new_preamble, submitted_by, submitted_pfp)


class WouldYouRatherLockedView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(label="Vote A", style=ButtonStyle.green, disabled=True, custom_id="wyr:locked_a", emoji="🅰"))
        self.add_item(Button(label="Vote B", style=ButtonStyle.red, disabled=True, custom_id="wyr:locked_b", emoji="🅱"))
        self.add_item(Button(label="Next Question", style=ButtonStyle.grey, disabled=True, custom_id="wyr:locked_next", emoji="🎲"))


_wyr_votes: dict[int, dict[int, str]] = defaultdict(dict)                   # msg_id -> {user_id -> "A"|"B"}
_wyr_options: dict[int, tuple[str, str, str, str | None, str | None]] = {}  # msg_id -> (option_a, option_b, preamble, submitted_by, submitted_pfp)


def unpack_wyr(question: tuple[str, str] | tuple[str, str, str]) -> tuple[str, str, str]:
    if len(question) == 3:
        return question[0], question[1], question[2]
    return question[0], question[1], ""


def build_wyr_embed(
    option_a: str, option_b: str, votes: dict[int, str], requester: User, *,
    preamble: str = "", submitted_by: str | None = None, submitted_pfp: str | None = None,
    was_reset: bool = False, final: bool = False
) -> Embed:
    count_a = sum(1 for v in votes.values() if v == "A")
    count_b = sum(1 for v in votes.values() if v == "B")
    total = count_a + count_b

    embed = Embed(title=f"Would you rather{' ' if preamble else ''}{preamble}...", color=custom_hash(requester.id))

    embed.add_field(name=f"🅰\u200b  {option_a}", value=_vote_bar(count_a, total), inline=False)
    embed.add_field(name=f"🅱\u200b  {option_b}", value=_vote_bar(count_b, total), inline=False)
    embed.set_author(
        name=f"Requested by {requester.display_name}",
        icon_url=requester.display_avatar.url
    )

    status = "Final results" if final else "Cast your vote below!"
    footer_parts = [status, f"{total} vote{'s' if total != 1 else ''} cast"]

    if submitted_by:
        footer_parts.insert(0, f"Submitted by {submitted_by}")

    if was_reset:
        footer_parts.append("All questions used - starting fresh!")

    embed.set_footer(text="  ·  ".join(footer_parts), icon_url=submitted_pfp)
    return embed


class TruthOrDareCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await load_submissions(self.bot)
        self.bot.add_view(TruthOrDareView())
        self.bot.add_view(WouldYouRatherView())

    # region /truth, /would, /submit
    truth_group = Group(name="truth", description="Truth commands", allowed_contexts=ALL, allowed_installs=ALL_INSTALLS)
    truth_or_group = Group(name="or", description="Or commands", allowed_contexts=ALL, allowed_installs=ALL_INSTALLS, parent=truth_group)
    would_group = Group(name="would", description="Would commands", allowed_contexts=ALL, allowed_installs=ALL_INSTALLS)
    would_you_group = Group(name="you", description="You commands", allowed_contexts=ALL, allowed_installs=ALL_INSTALLS, parent=would_group)
    submit_group = Group(name="submit", description="Submit a Truth, Dare, or Would You Rather question for review",
                          allowed_contexts=ALL, allowed_installs=ALL_INSTALLS)

    @truth_or_group.command(name="dare", description="Do a truth or dare")
    async def truth_or_dare(self, ctx: Interaction):
        await TruthOrDareView().random_button.callback(ctx)

    @would_you_group.command(name="rather", description="Get a random Would you Rather question")
    async def would_you_rather(self, ctx: Interaction) -> None:
        entry, was_reset = TRACKER.pick(ctx.channel_id, "would_you_rather", ctx.guild.id if ctx.guild else None)

        question = extract_question(entry)
        submitted_by, submitted_pfp = await extract_submitter(self.bot, entry, ctx.guild)

        option_a, option_b, preamble = unpack_wyr(question)

        embed = build_wyr_embed(
            option_a, option_b, {}, ctx.user,
            preamble=preamble, submitted_by=submitted_by, submitted_pfp=submitted_pfp, was_reset=was_reset
        )
        await ctx.response.send_message(embed=embed, view=WouldYouRatherView())
        msg = await ctx.original_response()
        _wyr_options[msg.id] = (option_a, option_b, preamble, submitted_by, submitted_pfp)

    @submit_group.command(name="truth", description="Submit a Truth question for review")
    @describe(question="The truth question you'd like to suggest",
              server_specific="Restrict this to your current server (e.g. inside jokes)")
    async def submit_truth(self, ctx: Interaction, question: str, server_specific: bool = False):
        await _handle_submission(ctx, "Truth", question, server_specific)

    @submit_group.command(name="dare", description="Submit a Dare for review")
    @describe(question="The dare you'd like to suggest",
              server_specific="Restrict this to your current server (e.g. inside jokes)")
    async def submit_dare(self, ctx: Interaction, question: str, server_specific: bool = False):
        await _handle_submission(ctx, "Dare", question, server_specific)

    @submit_group.command(name="wyr", description="Submit a Would You Rather question for review")
    @describe(question="The Would You Rather question you'd like to suggest",
              server_specific="Restrict this to your current server (e.g. inside jokes)")
    async def submit_wyr(self, ctx: Interaction, question: str, server_specific: bool = False):
        if not await _validate_wyr(question, ctx):
            return
        if server_specific and ctx.guild_id is None:
            await ctx.response.send_message(
                "You can only mark a submission as server-specific from inside a server.", ephemeral=True)
            return
        parsed = _format_wyr(question.strip())
        option_a, option_b = parsed[0], parsed[1]
        preamble = parsed[2] if len(parsed) > 2 else ""
        view = WyrPreviewView(option_a, option_b, preamble, ctx.user.id,
                              ctx.guild_id if server_specific else None)
        embed = _build_wyr_preview_embed(option_a, option_b, preamble)
        await ctx.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await ctx.original_response()

    @submit_group.command(name="edit", description="Edit an approved submission by ID")
    @describe(submission_id="The submission ID shown in the review embed footer")
    async def submission_edit(self, ctx: Interaction, submission_id: str):
        if ctx.user.id != OWNER_ID:
            await ctx.response.send_message(
                "Only **Contore**'s acting owner can edit submissions.", ephemeral=True
            )
            return

        sub = await db.db_get_submission(submission_id)
        if sub is None:
            await ctx.response.send_message(
                f"No submission found with ID `{submission_id}`.", ephemeral=True
            )
            return

        if sub["status"] != "approved":
            await ctx.response.send_message(
                "Only approved submissions can be edited. "
                "Use the `Revise` button on the review message for pending ones.",
                ephemeral=True
            )
            return

        if sub["type"] == "Would You Rather":
            q = sub["question"]
            if isinstance(q, (list, tuple)) and len(q) >= 2:
                option_a, option_b = q[0], q[1]
                preamble = q[2] if len(q) > 2 else ""
            else:
                parsed = _format_wyr(str(q))
                option_a, option_b = parsed[0], parsed[1]
                preamble = parsed[2] if len(parsed) > 2 else ""

            view = EditApprovedWyrView(submission_id, option_a, option_b, preamble, ctx.user.id)
            embed = _build_wyr_preview_embed(option_a, option_b, preamble)
            await ctx.response.send_message(embed=embed, view=view, ephemeral=True)
            view.message = await ctx.original_response()
        else:
            await ctx.response.send_modal(EditApprovedModal(submission_id, sub["question"]))

    @submit_group.command(name="delete", description="Delete an approved submission by ID")
    @describe(submission_id="The submission ID shown in the review embed footer")
    async def submission_delete(self, ctx: Interaction, submission_id: str):
        if ctx.user.id != OWNER_ID:
            await ctx.response.send_message(
                "Only **Contore**'s acting owner can delete submissions.", ephemeral=True
            )
            return

        sub = await db.db_get_submission(submission_id)
        if sub is None:
            await ctx.response.send_message(
                f"No submission found with ID `{submission_id}`.", ephemeral=True
            )
            return

        if sub["status"] != "approved":
            await ctx.response.send_message(
                "Only approved submissions can be deleted via this command. "
                "Use the `Deny` button on the review message for pending ones.",
                ephemeral=True
            )
            return

        lst = _TYPE_LISTS[sub["type"]]
        for i, entry in enumerate(lst):
            if isinstance(entry, dict) and entry.get("sub_id") == submission_id:
                lst.pop(i)
                break

        await db.db_delete_submission(submission_id)

        await ctx.response.send_message(
            f"✅ Submission `{submission_id}` ({sub['type']}) has been removed from the pool.",
            ephemeral=True
        )
    # endregion


async def setup(bot: commands.Bot):
    await bot.add_cog(TruthOrDareCog(bot))

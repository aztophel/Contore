"""`/help`'s browsable documentation (lazily built on first use, since it
needs `cmd_mention()` to resolve real slash-command IDs, which only exist
once the tree has synced) and the periodic custom-status rotation task.
"""
from __future__ import annotations
import asyncio
import random

from discord import Activity, ActivityType, ButtonStyle, Embed, Interaction, SelectOption, app_commands
from discord.app_commands import allowed_contexts
from discord.ext import commands
from discord.ui import Button, Select, View

from core.mentions import cmd_mention


def _paginate_help_sections(category_title: str, category_summary: str, sections: list[tuple[str, str]]) -> list[Embed]:
    embeds: list[Embed] = []
    current = Embed(title=category_title, description=category_summary, color=0x2C2F33)
    current_len = len(category_title) + len(category_summary)
    for heading, body in sections:
        body = body.strip()
        chunks = [body[i:i + 1000] for i in range(0, len(body), 1000)] or [""]
        for i, chunk in enumerate(chunks):
            field_name = heading if i == 0 else f"{heading} (cont.)"
            field_len = len(field_name) + len(chunk)
            if len(current.fields) >= 24 or current_len + field_len > 5500:
                embeds.append(current)
                current = Embed(title=category_title, color=0x2C2F33)
                current_len = len(category_title)
            current.add_field(name=field_name, value=chunk or "\u200b", inline=False)
            current_len += field_len
    embeds.append(current)
    for i, e in enumerate(embeds):
        e.set_footer(text=f"Page {i + 1}/{len(embeds)}")
    return embeds


HELP_DOCS: dict[str, dict] | None = None


def _setup_help_docs():
    global HELP_DOCS
    HELP_DOCS = {
    "pokemon_basics": {
        "title": "Pokémon: Getting Started",
        "summary": "Starters, wild spawns, catching, and the Pokédex.",
        "sections": [
            ("Starting out", (
                f"Run {cmd_mention('pokemon starter')} once to pick a starter from any of the six "
                "generations available. You'll get it at level 5 along with 8 Poké Balls, 5 Potions, "
                "3 Revives, and 1,500 Pokedollars to get moving. This can only be done once per user. "
                "If you release your very last Pokémon later, that starting kit (minus the Pokémon "
                "itself) is automatically deducted back from you.")),
            ("Wild spawns", (
                "If a server has spawning enabled, Pokémon appear as their own message with a silhouette "
                "and a Battle! button after enough activity has passed. An admin may control how often this "
                f"happens with {cmd_mention('pokemon config rate')} (a minimum message count between spawns "
                "plus a per-message chance once that threshold is hit), which channel spawns are sent "
                f"to via {cmd_mention('pokemon config channel')}, and whether spawning is even on at all via "
                f"{cmd_mention('pokemon config enable')}. Spawns expire after an hour if nobody engages.")),
            ("Fighting and catching", (
                "Pressing Battle! starts a normal encounter against your active team. Once the wild Pokémon "
                "is weak (or asleep, frozen, etc.) throw a ball from the in-battle Bag menu. Better balls, "
                "lower HP, and status conditions all raise your odds, and some balls have situational bonuses "
                "(Net Ball vs Water/Bug, Dusk Ball at night, Quick Ball on the first turn, and so on). Master "
                f"Balls always work. Use {cmd_mention('pokemon cr')} to get an exact catch percentage for a "
                "specific species/ball/HP/status combination before you commit a rare ball to it.")),
            ("Catch progression", (
                "Critical captures (a single, near-guaranteed shake instead of the usual four checks) unlock and "
                "get more likely as your Pokédex grows (thresholds at 30, 150, 300, 450, and 600 species "
                "seen). Hitting certain Pokédex ranges also pays out a one-time cash and item reward; check "
                f"{cmd_mention('pokedex')} for your progress toward each milestone.")),
            ("Hunting", (
                f"{cmd_mention('pokemon hunt')} skips the passive spawn wait and starts an encounter directly, "
                "optionally biased toward a habitat (forest, ocean, cave, and so on) that shifts which types "
                "show up. It has a one-hour cooldown, but that shortens automatically if you demolish the "
                "wild Pokémon fast; beating it in a single hit drops the cooldown to 40 minutes, two hits to "
                f"50. Toggle a DM reminder for when it's ready with {cmd_mention('pokemon notifications')}.")),
        ],
    },
    "pokemon_battling": {
        "title": "Pokeéon: Battling",
        "summary": "Duels, formats, the in-battle menu, and fighting Contore.",
        "sections": [
            ("Starting a duel", (
                f"{cmd_mention('pokemon duel')} challenges another trainer. Formats are Singles (1v1), "
                "Doubles (you and your opponent each field 2 Pokémon), and Doubles 2v2 (four separate "
                "trainers, two per side, set your_partner and opponent_partner for this one). Set practice "
                "to true to spar without HP loss, PP loss, or money changing hands; useful for testing a "
                "team without burning resources, or for mere spars.")),
            ("The in-battle menu", (
                "Fight opens your active Pokémon's moveset (locked moves like charging attacks, Choice-item "
                "locks, and Encore are handled automatically and shown pre-selected). Bag lets you use held "
                "items, healing items, or throw a ball at a wild Pokémon. Switch swaps to a benched party "
                "member, unless something's actively trapping you. Run/Forfeit ends the encounter. Running "
                "from a wild Pokémon isn't guaranteed if it out-speeds you or has a trapping ability.")),
            ("Mechanics coverage", (
                "The battle engine models most of what you'd expect from the actual games through Gen VI: "
                "weather, terrain, entry hazards, screens, abilities (including ability-suppression and "
                "Mold Breaker interactions), held item effects, status conditions, and stat stages. Mega "
                "Evolution needs a Mega Ring in your bag plus the correct stone held by that specific "
                "Pokémon (Rayquaza is the one exception; it just needs to know Dragon Ascent).")),
            ("Fighting Contore", (
                "You may challenge Contore itself the same way you'd challenge a trainer. "
                "Note that Contore may or may not play by the same rules you yourself are beholden to. "
                "Expect an unfair fight.")),
            ("Rewards", (
                "Winning a real duel pays out a cut of the loser's wallet (bigger cut for lower-net-worth "
                "opponents so grinding someone into the ground has diminishing returns); losing costs you "
                "the same amount. Wild encounters instead pay a rarity/level-scaled bounty and Exp. for your "
                "team on a win, and nothing at all on a loss or flee.")),
        ],
    },
    "pokemon_breeding": {
        "title": "Pokémon: Breeding & Eggs",
        "summary": "Egg groups, inheritance rules, and hatching.",
        "sections": [
            ("Compatibility", (
                f"{cmd_mention('pokemon breed')} takes two Pokémon. The second can be your own or, by ID "
                "number, another trainer's (which requires their confirmation). They need to share an egg "
                "group and be opposite genders, unless one of them is Ditto, which can pair with almost "
                "anything with a gender. Undiscovered-group Pokémon can't breed at all.")),
            ("Cost and cooldown", (
                "Breeding costs money scaled to the resulting egg's species rarity, charged up front. Each "
                "parent then needs 6 hours of rest before it can breed again. This cooldown applies per-Pokémon, not "
                "per-trainer, so you can run multiple pairs in parallel with different individuals. You may"
                f"toggle a breeding-ready notification with {cmd_mention('pokemon notifications')}, so that"
                "you know when your Pokémon are ready to breed again.")),
            ("What gets inherited", (
                "3 of the egg's 6 IVs are inherited from the parents at random (5 instead of 3 if either "
                "parent holds a Destiny Knot); a parent holding a Power item guarantees its own value for "
                "that one specific stat, consuming one of those inherited slots. Nature passes down if a "
                "parent holds an Everstone. Ability inheritance favors the mother's (or the non-Ditto "
                "parent's) ability with reasonably high odds, scaled down for hidden abilities. The egg "
                "hatches holding the mother's Poké Ball type, with a few ball types that can't be passed "
                "down at all. Move inheritance pulls in anything either parent currently knows that the "
                "baby's species can also legally learn.")),
            ("Hatching", (
                "Hatch time is derived from the species' real hatch-cycle count and is usually somewhere "
                "between 6 and 40-ish hours depending on rarity. Check outstanding eggs with "
                f"{cmd_mention('pokemon eggs')}.")),
        ],
    },
    "pokemon_growth": {
        "title": "Pokémon: Stats, EVs, and Evolution",
        "summary": "IVs, EVs, move relearning, and how evolution triggers work.",
        "sections": [
            ("IVs and EVs", (
                "Every Pokémon has 6 IVs (0-31 each, rolled on catch/hatch and fixed for its lifetime unless "
                "you spend an IV Booster or Perfect IV Serum) and 6 EVs (0-255 per stat, 510 total, earned "
                "automatically from wild battle wins based on species). Vitamins raise a single EV stat "
                "directly (capped at 100 EVs from vitamins alone per stat); berries lower one. An EV "
                "Reorganizer lets you freely redistribute your current EV total across stats via a modal "
                "instead of grinding them back down one berry at a time.")),
            ("Move PP and relearning", (
                f"PP Up/PP Max permanently raise a specific move's max PP (capped at 3 PP Ups per move). "
                f"{cmd_mention('pokemon relearn')} lets a Pokémon relearn any level-up move it's since "
                "forgotten, replacing one of its current 4 if it's already full.")),
            ("Evolution", (
                "Evolutions trigger automatically and are always offered as a DM confirmation rather than "
                "happening silently. This covers level-up evolutions (with whatever extra conditions apply: "
                "happiness, time of day, a specific known move, relative Attack/Defense), item-triggered "
                "evolutions, and trade evolutions (which fire automatically once ownership actually changes "
                "hands, including for held-item trade evolutions). A Guise Charm held at evolution time locks "
                "the Pokémon's outward appearance to its pre-evolution look while keeping the new stats and "
                "movepool.")),
        ],
    },
    "pokemon_economy": {
        "title": "Pokémon: Shop, Items, Teams, and Trading",
        "summary": "The economy side: buying gear, managing your roster, and moving Pokémon between trainers.",
        "sections": [
            ("Shop and bag", (
                f"{cmd_mention('pokemon shop')} is organized by category (Poké Balls, Battle Items, Held "
                "Items, Evolution Items, Training Items, Mega Stones, TMs sorted by type, and a "
                "miscellaneous catch-all for form items and plates). TM prices are computed from the actual "
                f"move's power, accuracy, and secondary effects rather than being flat. {cmd_mention('pokemon bag')} "
                f"shows what you're carrying, and {cmd_mention('pokemon item give')} / {cmd_mention('pokemon item take')} "
                "manage what a specific Pokémon is holding.")),
            ("Team management", (
                f"Your active battle team (up to 6) is separate from your full collection. Manage it with "
                f"{cmd_mention('pokemon team view')}, add, remove, clear, and reorder. "
                f"{cmd_mention('pokemon team save')} and {cmd_mention('pokemon team load')} let you keep "
                "named presets for whatever reason you wish and swap between them instantly "
                "instead of rebuilding by hand.")),
            ("Giving and trading", (
                f"{cmd_mention('pokemon give')} hands a Pokémon to someone for free, with confirmation. "
                f"{cmd_mention('pokemon trade')} swaps two specific Pokémon between two trainers, also with "
                "confirmation on both sides. Either can trigger a trade evolution automatically. If the "
                "receiving trainer would end up holding a duplicate Mega Stone they can't legally have two "
                "of, the extra one is destroyed rather than silently vanishing into their bag.")),
            ("Global Trading System", (
                f"{cmd_mention('gts list')} puts one of your Pokémon up for a specified price. You may ask "
                "for a species, minimum IVs, a nature, an ability, a gender, shininess, money, or any "
                "combination. Contore verifies your ask is actually achievable before accepting it, and the "
                "listed Pokémon is pulled out of your team and collection listings until the listing is "
                f"cancelled or fulfilled. A Pokémon can't be used, traded, or bred while it's on the GTS. "
                f"{cmd_mention('gts addcondition')} adds another (OR) way to satisfy the listing, and "
                f"{cmd_mention('gts removecondition')} takes one back off by its number as shown in "
                f"{cmd_mention('gts mine')}. {cmd_mention('gts browse')} shows active listings and can be "
                "narrowed down by species, a minimum-IV string, nature, ability, gender, and shininess all "
                "at once, plus a `fulfillable_only` filter that only shows listings you can currently "
                f"satisfy with what's in your own collection and wallet. {cmd_mention('gts fulfill')} "
                "completes a trade with confirmation from the original lister and returns the Pokémon to "
                f"normal availability, and {cmd_mention('gts mine')} / {cmd_mention('gts cancel')} manage "
                "your own open listings. Cancelling returns the Pokémon to you immediately.")),
        ],
    },
    "levelling": {
        "title": "Levelling and XP",
        "summary": "How XP is earned, level-up behavior, and admin configuration.",
        "sections": [
            ("XP sources", (
                "XP can come from messages, voice activity, and reactions, each independently toggleable and "
                "tunable by an admin. Message XP has a per-user cooldown, a minimum message length, an "
                "optional bonus for longer/effortful messages, and can be restricted to or excluded from "
                "specific channels and roles. Voice XP scales per minute and is reduced or zeroed for being "
                "muted, deafened, alone in the channel, or sitting in the AFK channel. Multipliers (global, "
                "per-channel, per-role, or time-of-day) stack multiplicatively on top of all of this.")),
            ("Ranks and leaderboards", (
                f"{cmd_mention('rank')} generates an image card showing level, rank, and progress; you can "
                "personalize its accent color and background with the same command. "
                f"{cmd_mention('level leaderboard')} renders a server-wide standings image.")),
            ("Level-up behavior", (
                f"{cmd_mention('level config levelup')} controls the announcement message (with template "
                "variables for the user, their new/old level, XP totals, and any roles they just earned), "
                "which channel it posts in (or the triggering channel by default, or a DM), and whether an "
                f"image card accompanies it. {cmd_mention('level reward add')} assigns roles at specific "
                "levels, optionally as a mutually-exclusive tier group (so hitting the next tier removes the "
                "previous one) or as a temporary grant rather than a permanent one.")),
            ("Custom level formulas", (
                f"{cmd_mention('xp config formula')} lets an admin replace the default XP curve with a "
                "formula of their own, written in terms of `level` (the XP needed to go from that level to "
                "the next). It supports basic arithmetic, comparisons, a small ternary conditional, and a "
                "short whitelist of math functions (abs, min, max, round, pow, sqrt, log/log2/log10, floor, "
                "ceil) plus the constants pi and e. Nothing else runs, so there's no risk in letting members "
                "suggest formulas. Contore test-evaluates it across several sample levels before accepting "
                "it, rejecting anything that errors, returns something non-numeric, or ever produces a "
                "non-positive requirement. Leave the formula blank to preview the current one, or pass reset "
                "to restore the default (`level * 100 + 75`). Changing the formula never touches anyone's "
                "stored total XP; it only changes how that XP maps to a level and progress bar going forward.")),
            ("Admin tools", (
                f"{cmd_mention('level admin setxp')}, addxp, resetxp, and resetall give direct control over "
                f"a user's XP for corrections or events. {cmd_mention('level admin transferxp')} swaps two "
                f"users' progress outright, and {cmd_mention('level admin arcanesync')} can import existing "
                "standings from the Arcane bot's public leaderboard if a server is migrating over.")),
        ],
    },
    "achievements": {
        "title": "Achievements",
        "summary": "Manual and automated achievement tracking, condition trees, and rewards.",
        "sections": [
            ("Manual vs. automated", (
                f"{cmd_mention('achievement create')} makes an achievement either manual (an admin grants it "
                f"by hand with {cmd_mention('achievement grant')}) or automated (Contore checks a condition "
                "against a user continuously and grants it the moment it's satisfied).")),
            ("Conditions", (
                "Automated conditions come in a handful of leaf types: message count, reaction count, voice "
                "minutes, holding a specific role, reaching a level, reaching a total XP amount, or an "
                "arbitrary custom numeric metric, plus two runtime types tied to a specific user's message "
                "or command usage. Leaves combine into trees using AND/OR/XOR/XAND/NOT gates, so you can "
                f"build genuinely complex requirements. {cmd_mention('achievement addcondition')} appends a "
                f"single condition to an achievement's existing tree without hand-writing JSON; "
                f"{cmd_mention('achievement setcondition')} accepts a full tree directly for anything more "
                f"elaborate. {cmd_mention('achievement schema')} documents the exact JSON shape.")),
            ("Custom metrics for other systems", (
                "The custom condition type is the hook other parts of the bot use to feed into achievements "
                "without any special-casing. Pokémon progress (catches, shinies, legendaries/mythicals "
                "caught, Pokedex species seen, eggs hatched, duels won) is tracked this way, so an admin can "
                "build an achievement against `pokemon_caught`, `shinies_caught`, `legendaries_caught`, "
                "`mythicals_caught`, `pokedex_seen_count`, `eggs_hatched`, or `battles_won` the same way they would "
                "for anything else.")),
            ("Secrets, rewards, and upkeep", (
                "Marking an achievement secret hides its name and description until earned (or from anyone "
                f"but an admin, otherwise). {cmd_mention('achievement xpreward')} ties an XP payout to earning "
                f"it, retroactively adjusting anyone who already has it if you change the value later. "
                f"{cmd_mention('achievement scan')} sweeps the whole server and grants anything members have "
                "silently already qualified for. Useful after adding a new achievement retroactively, or "
                "with dry_run to preview first.")),
        ],
    },
    "family": {
        "title": "Family Trees",
        "summary": "Marriages, adoptions, restrictions, and tree rendering.",
        "sections": [
            ("Relationships", (
                f"{cmd_mention('marry')}, {cmd_mention('divorce')}, {cmd_mention('adopt')}, {cmd_mention('disown')}, {cmd_mention('makeparent')}, and {cmd_mention('runaway')} build and unwind partner and "
                "parent-child relationships, each with confirmation from the other party where it matters. "
                "These work across servers and in DMs, since the family graph isn't server-scoped.")),
            ("Restrictions", (
                f"{cmd_mention('restriction set')} lets a parent whitelist or blacklist specific users from "
                "marrying or adopting one of their descendants, and that restriction is inherited down the "
                f"tree from whoever set it. {cmd_mention('restriction mode')} sets your own default posture "
                "(blacklist vs. whitelist) for any restriction lists you create.")),
            ("Viewing the tree", (
                f"{cmd_mention('family tree')} renders your (or someone else's) family as an image, with options to use straight "
                "connecting lines instead of the default routed ones and to show real usernames alongside "
                "display names. /hidefromtree lets you exclude yourself and your descendants from someone "
                f"else's rendered tree without breaking the underlying relationships. {cmd_mention('title')} sets a short "
                "label (like a nickname or role) shown under your name in the tree.")),
        ],
    },
    "proxies": {
        "title": "Proxies",
        "summary": "Speaking as a custom persona, linked to a real user or fully custom.",
        "sections": [
            ("Creating a proxy", (
                f"{cmd_mention('proxy create')} needs an invocation pattern containing the literal word "
                "\"`text`\" exactly once (e.g. `[Alter] text`). Any message matching that pattern gets deleted "
                "and re-sent through a webhook under the proxy's name and avatar, with the text portion "
                "extracted. A proxy's name/avatar can be a fixed value, a fixed URL, or linked live to a "
                "real Discord user by mentioning them, in which case it always mirrors their current display "
                f"name/avatar. Add a short description with the description field to remind yourself (or "
                "explain to others) what a proxy is for.")),
            ("Per-server nicknames", (
                f"{cmd_mention('proxy nick')} overrides just the display name of a proxy in one specific "
                "server without touching its global name, useful if a linked user goes by something "
                "different there.")),
            ("Autoproxy", (
                f"{cmd_mention('proxy autoproxy')} makes your most recently used proxy apply automatically to "
                "ordinary messages without needing the invocation pattern each time, optionally expiring "
                "after a period of inactivity you set.")),
        ],
    },
    "social": {
        "title": "Birthdays, Quotes, and Games",
        "summary": "Birthday tracking, saved quotes, Truth or Dare, Would You Rather, and community submissions.",
        "sections": [
            ("Birthdays", (
                f"{cmd_mention('birthday set')} accepts either a full date or just month/day if you'd rather "
                f"not share your birth year. {cmd_mention('birthday role')} and {cmd_mention('birthday channel')} "
                "let an admin configure a role applied on the day and/or an announcement channel; /birthdays "
                "lists everyone's upcoming birthday in a server, soonest first.")),
            ("Quotes", (
                "Right-click (or long-press) any message and choose Quote to save it, attachments and all, "
                f"to your personal quotebook. {cmd_mention('quotes view')} supports sorting (by date, "
                "alphabetically, by account ID) and filtering (by date range, by author, or by server).")),
            ("Truth or Dare and Would You Rather", (
                "The `Truth`, `Dare`, and `🎲` buttons pull from a shared question pool that avoids repeats within "
                "a channel until it's exhausted. Would You Rather renders as a live poll with vote bars that "
                "update in place, plus a `Next Question` button to keep a session going without re-running the "
                "command.")),
            ("Submitting your own questions", (
                f"{cmd_mention('submit truth')}, {cmd_mention('submit dare')}, and {cmd_mention('submit wyr')} "
                "send a proposed question to the mysterious old wizard at the top of the Tower Of Endless Solemnity And Infinite Contemplation for review before it enters the shared pool, with "
                "an option to mark it server-specific (for inside jokes) rather than global. You'll get a DM "
                "once it has been approved or denied.")),
        ],
    },
    "tags": {
        "title": "Tags",
        "summary": "Saved snippets, canned responses, and message-triggered replies.",
        "sections": [
            ("Basic tags", (
                f"{cmd_mention('tag add')} saves a name plus text content and, optionally, an image you "
                f"attach directly. {cmd_mention('tag get')} fetches it back on demand, "
                f"and {cmd_mention('tag list')} shows everything saved in a server along with "
                "usage counts.")),
            ("Rich embed tags", (
                f"{cmd_mention('tag embed')} turns a tag into a full embed, with atitle, description, color, and "
                "an image/thumbnail that can either be uploaded directly or given as a URL, and an "
                "optional plain-text message alongside it. Running it again on an existing tag updates "
                "whichever fields you specify and leaves the rest as they were.")),
            ("Triggers", (
                f"{cmd_mention('tag trigger')} (`Manage Server`) binds a tag to an exact message; send that "
                "phrase verbatim anywhere in the server and Contore will reply with the tag automatically. "
                "Two tags can't share the same trigger phrase in one server.")),
            ("Moderation", (
                f"{cmd_mention('tag config')} (`Manage Server`) can restrict tag creation to server managers "
                "only, if you'd rather curate them centrally than let anyone add one. Tag content is also "
                "checked on creation and edit against the server's own AutoMod keyword and regex rules, so a "
                "tag can't be used as a workaround for a filter its creator is themselves subject to. Note "
                "that this is not infallible, as Contore does not have access to the lists Discord uses "
                "for rules such as \"Block Commonly Flagged Words\".")),
        ],
    },
    "server_setup": {
        "title": "Server Setup and Admin Tools",
        "summary": "Roles, leave messages, and the smaller moderation utilities.",
        "sections": [
            ("Automatic roles", (
                f"{cmd_mention('auto role')} assigns a role to every new member, or to one specific user, on "
                f"join. {cmd_mention('message role')} grants or revokes a role whenever a matching message is "
                "sent, gated by who's allowed to trigger it (everyone, admins, a specific role, or a specific "
                f"user) and who it targets (the sender, mentioned users, or one fixed user), with an optional "
                f"auto-expiry. {cmd_mention('message list')} shows every message-role rule currently "
                f"configured along with the ID needed to remove one, and {cmd_mention('message delete')} "
                f"deletes a single rule by that ID without wiping every other rule in the server. "
                f"{cmd_mention('date role')} schedules a role for a specific date, month, "
                "recurring day-of-month, or recurring annual date.")),
            ("Leave messages", (
                f"{cmd_mention('leave message')} sets a plain-text message and channel, and also allows "
                f"a directly-uploaded image to send alongside it. {cmd_mention('leave embed')} instead builds "
                "a full rich embed version (title, description, color, image, thumbnail; images can be "
                f"uploaded directly or given as URLs) which takes priority over the plain version when both "
                f"are configured. {cmd_mention('leave variables')} lists every `${{}}` template variable "
                "available for either.")),
            ("Haiku detection and Fake Nitro", (
                f"{cmd_mention('haiku toggle')} watches for three consecutive messages in a channel that "
                "scan as a 5-7-5 syllable haiku (supports multiple languages, not just "
                f"English) and calls it out when one forms. {cmd_mention('nitro toggle')} lets anyone use "
                ":shortcode: for animated emoji the bot can see, even without Nitro, by rendering the real "
                "emoji in place of the shortcode.")),
        ],
    },
}

class HelpView(View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.category: str | None = None
        self.pages: list[Embed] = []
        self.page_idx = 0
        self._build_menu()

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.user_id:
            await ctx.response.send_message(f"Run {cmd_mention('help')} yourself to browse.", ephemeral=True)
            return False
        return True

    def _build_menu(self):
        self.clear_items()
        options = [SelectOption(label=doc["title"], value=key, description=doc["summary"][:100])
                   for key, doc in HELP_DOCS.items()]
        sel = Select(placeholder="Choose a topic...", options=options)
        sel.callback = self._on_category
        self.add_item(sel)
        if self.category:
            prev_btn = Button(label="Previous", style=ButtonStyle.gray, disabled=self.page_idx <= 0)
            next_btn = Button(label="Next", style=ButtonStyle.gray, disabled=self.page_idx >= len(self.pages) - 1)
            prev_btn.callback = self._prev
            next_btn.callback = self._next
            self.add_item(prev_btn)
            self.add_item(next_btn)

    async def _on_category(self, ctx: Interaction):
        self.category = ctx.data["values"][0]
        doc = HELP_DOCS[self.category]
        self.pages = _paginate_help_sections(doc["title"], doc["summary"], doc["sections"])
        self.page_idx = 0
        self._build_menu()
        await ctx.response.edit_message(embed=self.pages[0], view=self)

    async def _prev(self, ctx: Interaction):
        self.page_idx = max(0, self.page_idx - 1)
        self._build_menu()
        await ctx.response.edit_message(embed=self.pages[self.page_idx], view=self)

    async def _next(self, ctx: Interaction):
        self.page_idx = min(len(self.pages) - 1, self.page_idx + 1)
        self._build_menu()
        await ctx.response.edit_message(embed=self.pages[self.page_idx], view=self)


async def change_status_periodically(bot: commands.Bot, statuses: list[str], status_update: tuple[int, int]):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await bot.change_presence(
                activity=Activity(name="custom", state=random.choice(statuses), type=ActivityType.custom))
        except Exception:
            pass
        await asyncio.sleep(random.randint(status_update[0], status_update[1]))


_STATUSES: list[str] = [
    # alfred
    "children are either angels or the spawn of satan",
    "i thought i was biting his ass",
    "omg a snack 🐀",
    "bros a lovecraftian horror that \"cannot be described with mere words\" 😭🙏😭",
    "now all i need is the weed",
    "she only likes fat 60 year olds",
    "grug night. don't let the bed grugs bite",
    "sybau",
    "i have a basement",
    "French porn is like the worst torture method there is",
    "sending 10 crates of lube rn",
    "average batman victim would be looking for gay help services",
    "mmmm ejaculine milk",
    "she's the sniggle at the funeral..",
    "tu coño es mi amor",
    "what if we brted in the Mariana trench..",
    "🍆👉🍑💦",
    "i like the heat daddy",
    "so now i am become brocken",
    "everyokem's a nice guy",
    "proud sperm buster",
    "scott. his ass hair's stench terrifies me.",
    "your ass looks 30 😒",
    "IT'S JERKING OFF",
    "39 buried. 0 found.",
    "you are fingering yourself",
    "sex toes",
    "bob Ross lookin at me after i draw the sex",
    "your dastardly dastard",
    "can i eat your pancreas & can i put my balls in ur jaw",
    "\"Gn (get naked)\" - pep 2024, chrmms eve/chrimms",
    "man crucified after teacher reads discord message to entire class",
    "sneaky self reac",
    "alrighty tighty lefty loosey like ur poosey",
    "thas 𝓯𝓻𝓮𝓪𝓴𝔂",
    "scrub me daddy",
    "the aliussy",
    "ich bin 𝓰𝓮𝓲𝓵",

    # tarik
    "my dad just farted in the shower",
    "Thats a huge cock",
    "Im gonna perk my tits up and spray my milk all over yall",
    "I opened my headphones and a fucking butterfly appeared outta nowhere",
    "wham bam shangalang",
    "My dad just farded and blamed it on the dawg",
    "I can imagine his asscheeks clapping as he runs away",
    "\"Questioning me\" is such an alpha male reason to mute someone",
    "You axtually suck poopoo ass",
    "Happi birthdayy alfyyy! TOP OF MORNINGG TOP OF MORNINGGG! I HOPE YOU GET A WOMAN THIS YEARRRR",
    "We need a sigma edit for Jack",
    "CHOOSE YOUR HOE ALREADY🗣🗣🗣🗣",
    "Audio Jungle.",
    "Myy demeentia is prgoersing",
    "\"I ONLY WANTED TO STAIN MY JEANS A LIL BROWN\"",
    "Wait since when are you in here",
    "I am appeased.",
    "Your mother",
    "IF YOU LOVE MY DICK SO MUCH JUST MARRY IT",
    "Im the child who got farted on their head",
    "What if we kissed in the mcdonalds..",
    "YOU CANT JUST IMPLODE YOUR BLOOD VESSELS WITH SUGAR",
    "My brain will do a 180 in my skull",
    "Hey girl, are you genshin? Cus id never play you",
    "WHY ARE THERE CONDOM ADS IN MY EARBUDS RN",
    "You bombaclat",
    "I thought you were an old grandma with no teeth",
    "slayyyyy💅💅",
    "UHUH, OF COURSE IF ITS ANAL",
    "My asshole burns. *insert trap music here*",
    "She be gettin sum robo BUSSY",

    # pep
    "Chat my mum keeps hitting me and thinks its funny ✌️ 🥹",
    "I AINT FROM ALABAMA",
    "Me gusta muchaches grande",
    "I will peg you boob",
    "The bus driver is such a fucking ragebaiter",
    "Im gonna call it Leon and smack it",
    "Dad...just dad to daughter..her tittiez kinda empty...",
    "WASPS ARE SURROUNDING ME",
    "Count your days Alfred",
    "\"PLEASE PLEASE JUST ONE TOE OKAY?!\" - probably lilly",
    "This fuckass server pmo so bad EVERY 5 minutes @everyone @everyone SHUT THE RUCK UP",
    "I dont think genderman liked that",
    "Im gonna be a good person and send r34 to people from UK to make them feel better",
    "Fuck you Leon get jumped",
    "No wonder hes a femboy",
    "GUYS LET ME OUT OF THE BRIG",
    "Let's just brt together",
    "@Alfred is the rainbow for the gayness",
    "We locked 2 of our classmates in the closet",
    "\"Dont fuck with me, youll only end up fucking yourself\" saying ahh",
    "What in the aids carrying disease is happening",
    "Trump x putin canon anywsy",
    "My sinuses are blowing up",
    "Fuck you discord",

    # alfred erms
    "you might have a new friend to play with in nine months",
    "i'm boutta commit bacon mumcher genocide",
    "im gonna goon",
    "i'm watching you 🫶",
    "my cat is currently sitting on my balls uncomfortably.",
    "I'll sleep with you baby boy",
    "ill just take a bit of your pp while i wait",
    "you will have to testicular torsion me if you want me to stop",
    "im on it babe",
    "maybe I'll be fatter after i eat u",
    "shut your pussy virgin femboy",
    "sedx",
    "when one hole closes, another opens",

    # tarik erms
    "MEEEEENSTRUAAAATEEE AAAAARGUHHHJ",
    "Ambattakaaaaaaaaaaam",
    "I give snacks to good girls",
    "Fuck, the pipe is leaking.",
    "Imma glorp the fuck out of you",
    "Im gonna have a shoegasm",
    "Literally suck every speck of my asshole rn",
    "Today alfy is freeuse guys, prepare your schlongs",
    "Ill always enjoy an energy snack consisting of a testicle or 2",
    "Can I eat your vulva",
    "Your genitals will explode at exactly 12:37 AM on the 9th of august, 2037",
    "(I had a shoegasm)",
    "𝘴𝘵𝘢𝘳𝘦𝘴 𝘪𝘯𝘵𝘰 𝘦𝘹𝘱𝘰𝘴𝘦𝘥 𝘵𝘩𝘳𝘰𝘢𝘵 𝘣𝘰𝘯𝘦 god thats sexy",
    "My uterus is gonna burst",
    "icame🗣‼️",
    "A POOP AND A CHOOCKLE",
    "Jarvis, annihilate this cunt",
    "She is technically breedable",
    "𝘶𝘯𝘻𝘪𝘱𝘴 𝘶𝘳𝘦𝘵𝘩𝘳𝘢",
    "We must sacrifice the fetus.",
    "Goodluck being buttfucked tomorrow",
    "Maybe checkmate but with only the mating",
    "Lilly has a feet addiction",
    "Finally, someone new to goon on",

    # pep erms
    "Is this a reference to the limited edition all members dick size dildos",
    "I'm jerking off",
    "Some bdsm makes the day light",
    "im clearly the alpha daddy",
    "I have a bigger USB to shove into him",
    "Epstein I told you not to come back",
    "Nya..let daddy take care of this one, kitten.",
    "I'm going back into the womb and leaving the universe",
    "Im gonna go goon just to feel better",
    "where is lilly to suck that fart into her fart jar 🥀",
    "Did you abort my baby??",
    "You dipped my dih in soy sauce last night 🤤",
    "Yes ill leave ur ass alone and bite your dick js like alfy",
    "Can someone hold my hand while im shitting",
    "Im single and ready to pickle buckle up"
]


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._status_task: asyncio.Task | None = None

    async def cog_load(self):
        self._status_task = asyncio.create_task(change_status_periodically(self.bot, _STATUSES, (600, 3600)))

    async def cog_unload(self):
        if self._status_task:
            self._status_task.cancel()

    @app_commands.command(name="help", description="Browse Contore's full documentation")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def help_command(self, ctx: Interaction):
        if HELP_DOCS is None:
            _setup_help_docs()
        overview = Embed(
            title="Contore Documentation",
            description=(
                "Contore provides many features, some of which might need a bit of explaining to get the hand of."
                "Click a topic below to view an in-depth explanation of it.\n\n"
                + "\n\n".join(f"**{doc['title']}**\n{doc['summary']}" for doc in HELP_DOCS.values())
            ),
            color=0x2C2F33
        )
        await ctx.response.send_message(embed=overview, view=HelpView(ctx.user.id), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))

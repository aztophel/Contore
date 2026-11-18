"""The passive wild-Pokémon spawn trigger: every non-bot message in an
enabled server counts toward that server's spawn threshold, and once
enough have accumulated, there's a chance to post a silhouetted "a wild
Pokémon appeared!" embed with a Battle! button. Ported from
`handle_pokemon_spawn`/`spawn_pokemon`/`WildSpawnView`/`_expire_spawn`.

This is the piece `hunt.py`'s docstring referred to as "whoever ends up
owning the on_message spawn hook" -- that's this module. It shares
`hunt.start_wild_battle` for the actual "open a battle" step rather than
duplicating that soft-dependency stub (same battle-engine integration gap
applies here as there).

Silhouetting the sprite before posting is Track A's `core.imaging`
territory (image processing, not Pokémon-specific) -- soft dependency,
same pattern as everywhere else; degrades to posting the un-silhouetted
sprite directly, exactly like the monolith's own fallback when silhouette
generation raised.
"""

from __future__ import annotations

import asyncio
import random
from collections import defaultdict

from discord import ButtonStyle, Embed, Interaction, Message
from discord.ext import commands
from discord.ui import Button, View, button
from discord.utils import MISSING

from pokemon.commands import repository
from pokemon.commands.battle_state import is_in_battle
from pokemon.commands.display import cmd_mention
from pokemon.commands.hunt import start_wild_battle
from pokemon.commands.spawning import WildEncounter, roll_wild_encounter
from pokemon.commands.team import get_battle_team
from pokemon.dex.sprites import sprite_url
from pokemon.generations.ruleset import ALL_GENERATIONS, GEN_6

_message_count_by_guild: dict[int, int] = defaultdict(int)
# (channel_id, message_id) -> spawn state dict, mirroring the monolith's
# `_ACTIVE_SPAWNS` shape (`species`/`level`/etc. + `claimed`/`in_battle`
# bookkeeping + a back-reference to the view/message for expiry).
active_spawns: dict[tuple[int, int], dict] = {}


async def _make_silhouette(url: str):
    """Soft dependency onto Track A's `core.imaging`. Returns `(file,
    fallback_url)` -- exactly one of the two is set, matching the
    monolith's own try/except-and-fall-back-to-the-plain-sprite-URL
    behavior at this exact call site."""
    try:
        from core.imaging import make_silhouette  # type: ignore[import-not-found]
    except ImportError:
        return None, url
    try:
        return await make_silhouette(url), None
    except Exception:
        return None, url


def _notify_channel_battles(channel_id: int) -> None:
    """Soft dependency: lets any battle currently running in this channel
    know a new message came in (the monolith used this to, e.g., pause an
    auto-forfeit-on-inactivity timer). No-op until the battle engine
    exposes this -- see `pokemon.commands.battle_state`'s module docstring
    for the same kind of soft dependency."""
    try:
        from pokemon.ui.battle_view import CHANNEL_BATTLES  # type: ignore[import-not-found]
    except ImportError:
        return
    for bv in list(CHANNEL_BATTLES.get(channel_id, set())):
        bv.on_channel_message()


class WildSpawnView(View):
    """The "Battle!" button under a wild-spawn embed. Ported verbatim from
    `WildSpawnView`."""

    def __init__(self, channel_id: int):
        super().__init__(timeout=3600)
        self.channel_id = channel_id
        self.message: Message | None = None

    @button(label="Battle!", style=ButtonStyle.green, emoji="⚔️", custom_id="wildmon:battle")
    async def battle_btn(self, ctx: Interaction, _: Button):
        spawn = active_spawns.get((self.channel_id, self.message.id))
        if not spawn or spawn["claimed"]:
            await ctx.response.send_message("This Pokémon is already gone.", ephemeral=True)
            return
        if is_in_battle(ctx.user.id):
            await ctx.response.send_message("Finish your current battle first!", ephemeral=True)
            return
        if not await repository.has_starter(ctx.user.id):
            await ctx.response.send_message(
                f"You need a starter first! Use {cmd_mention('pokemon starter')}.", ephemeral=True)
            return
        if spawn["in_battle"]:
            await ctx.response.send_message("Someone is already fighting this Pokémon!", ephemeral=True)
            return
        spawn["in_battle"] = True

        full_team = await get_battle_team(ctx.user.id)
        usable = [p for p in full_team if not p["fainted_at"]]
        if not usable:
            spawn["in_battle"] = False
            await ctx.response.send_message("All of your team's Pokémon have fainted.", ephemeral=True)
            return

        await ctx.response.defer()
        # `start_wild_battle` returns (embed, file, view) -- this used to
        # unpack two, which raised ValueError on every press of Battle!, and
        # never attached the view (no buttons) even without the crash.
        # The spawn itself is deliberately NOT touched at battle start (the
        # monolith's `_update_wild_spawn_embed` was only for RESOLVED
        # encounters; calling it here marked the spawn `claimed` up front and
        # made every catch fail with "It got away..."): `in_battle` guards
        # against a second fighter, and the battle closes/re-opens the spawn
        # itself when it ends (BattleView._show_battle_end).
        embed, file, view = await start_wild_battle(
            ctx.client, ctx.user, full_team, spawn["encounter"],
            channel_id=self.channel_id, guild_id=ctx.guild_id if ctx.guild else None, spawn=spawn)
        if view is None:  # battle UI unavailable: preview embed only
            spawn["in_battle"] = False
            await ctx.followup.send(embed=embed)
            return
        kwargs = {"embed": embed, "view": view, "wait": True}
        if file is not None:
            kwargs["file"] = file
        view.message = await ctx.followup.send(**kwargs)

    async def on_timeout(self) -> None:
        spawn = active_spawns.get((self.channel_id, self.message.id))
        if not spawn or spawn["claimed"]:
            return
        for item in self.children:
            item.disabled = True
        embed = self.message.embeds[0]
        embed.color = 0x888888
        embed.description = "The Pokémon wandered off."
        try:
            await self.message.edit(embed=embed, view=self, attachments=[])
        except Exception:
            pass


async def update_wild_spawn_embed(channel_id: int | None, message_id: int | None, spawn: dict, outcome: str) -> None:
    """Edits the original spawn embed and disables its Battle! button once
    the encounter has been resolved (caught, fainted, or claimed for
    battle), so no one else can try to fight something that's no longer
    up for grabs. Ported verbatim from `_update_wild_spawn_embed`."""
    spawn["claimed"] = True
    spawn["in_battle"] = False  # monolith did both; the port had dropped this line
    if channel_id and active_spawns.get((channel_id, message_id)) is spawn:
        active_spawns.pop((channel_id, message_id), None)

    view = spawn.get("view")
    if not view or not view.message:
        return
    for item in view.children:
        item.disabled = True
    try:
        embed = view.message.embeds[0]
        embed.color = 0x888888
        embed.description = outcome
        await view.message.edit(embed=embed, view=view, attachments=[])
    except Exception:
        pass


async def _expire_spawn(channel_id: int, message_id: int, ospawn: dict, timeout: float = 3600) -> None:
    """Ported verbatim from `_expire_spawn` -- belt-and-suspenders
    alongside `WildSpawnView.on_timeout`'s own discord.py-managed timeout;
    kept even though the two overlap, matching the monolith exactly."""
    await asyncio.sleep(timeout)
    spawn = active_spawns.get((channel_id, message_id))
    if spawn and spawn["message_id"] == ospawn["message_id"] and not spawn["claimed"]:
        del active_spawns[(channel_id, message_id)]
        view = ospawn["view"]
        for item in view.children:
            item.disabled = True
        message = ospawn["view"].message
        try:
            embed = message.embeds[0]
            embed.color = 0x888888
            embed.description = "The Pokémon wandered off."
            await message.edit(embed=embed, view=view, attachments=[])
        except Exception:
            pass


async def spawn_pokemon(channel, ruleset) -> None:
    """Rolls a wild encounter and posts the silhouette embed + Battle!
    button. Ported from `spawn_pokemon`."""
    encounter: WildEncounter = roll_wild_encounter(ruleset)

    file, fallback_url = await _make_silhouette(sprite_url(encounter.species.id, encounter.shiny))

    embed = Embed(
        title="A wild Pokémon has appeared!",
        description=f"Press **Battle!** to take it on. ({encounter.fuzzy_level_label()})", color=0x2ECC71)
    if file:
        embed.set_image(url="attachment://silhouette.png")
    elif fallback_url:
        embed.set_image(url=fallback_url)

    view = WildSpawnView(channel.id)
    msg = await channel.send(embed=embed, file=file if file else MISSING, view=view)
    spawn_state = {
        "encounter": encounter, "message_id": msg.id, "view": view, "claimed": False, "in_battle": False,
    }
    active_spawns[(channel.id, msg.id)] = spawn_state
    view.message = msg
    asyncio.create_task(_expire_spawn(channel.id, msg.id, spawn_state))


def _server_ruleset(cfg: dict):
    return ALL_GENERATIONS.get(cfg.get("generation", 6), GEN_6)


async def handle_pokemon_spawn(message: Message) -> None:
    """The `on_message` listener body. Ported verbatim from
    `handle_pokemon_spawn`; whoever owns the bot's event dispatch should
    call this from an `on_message` handler."""
    if not message.guild or message.author.bot:
        return

    _notify_channel_battles(message.channel.id)

    cfg = await repository.get_pokemon_config(message.guild.id)
    if not cfg["enabled"]:
        return

    gid = message.guild.id
    _message_count_by_guild[gid] += 1
    if _message_count_by_guild[gid] < cfg["spawn_min"]:
        return
    if random.random() >= cfg["spawn_chance"]:
        return
    _message_count_by_guild[gid] = 0

    spawn_channel_id = cfg["spawn_channel"] or message.channel.id
    if (spawn_channel_id, message.id) in active_spawns:
        return

    channel = message.guild.get_channel_or_thread(spawn_channel_id) or message.channel
    await spawn_pokemon(channel, _server_ruleset(cfg))


def reset_guild_message_count(guild_id: int) -> None:
    """Ported from the monolith's `_POKE_MSG_COUNT.pop(gid, None)` cleanup
    on guild removal -- call from an `on_guild_remove` handler."""
    _message_count_by_guild.pop(guild_id, None)


class SpawnHookCog(commands.Cog):
    """The actual `on_message`/`on_guild_remove` wiring this module's own
    docstring flagged as missing. A thin Cog rather than a bare listener
    function so it composes with every other feature's independent
    `on_message` cog (roles/achievements/tags/proxies/...) the same way --
    see HANDOFF_A2.md's "on_message is no longer one function" note for
    why that's the established pattern here, not a central dispatcher."""

    def __init__(self, bot: "commands.Bot") -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: Message) -> None:
        await handle_pokemon_spawn(message)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild) -> None:
        reset_guild_message_count(guild.id)


async def setup(bot) -> None:
    """Unlike its sibling `pokemon.commands.*` modules (see `config.py`'s
    `POKEMON_COMMAND_MODULES` comment), this one IS meant to be loaded via
    `bot.load_extension()` rather than a plain import: it defines no
    `@group.command(...)`s of its own (so no double-registration risk) and
    its whole purpose -- attaching `SpawnHookCog` -- only happens through
    the extension's `setup()`. bot.py loads it after the plain-import batch
    so `pokemon.commands.hunt`/`team` (which it imports from) are already
    populated rather than re-executed."""
    await bot.add_cog(SpawnHookCog(bot))

"""`/pokemon duel` -- challenge another trainer (or trainers, for Doubles
2v2) to a battle, or a configured boss battle (see `pokemon.commands.boss`)
if the opponent is one. Ported from `pokemon_duel`, with the monolith's
hardcoded "Contore" branch generalized into `pokemon.commands.boss`'s
any-user boss-battle system.

The "open a live battle" step hands off to
`pokemon.ui.battle_view.start_pvp_encounter` (added by Track E -- see
`pokemon.commands.battle_bridge.build_pvp_battle_engine` for the actual
roster -> Battler/Side construction, including the Doubles/Doubles 2v2
active-slot split). `start_pvp_battle` below still degrades to a
roster-summary preview embed if that import ever fails.

One thing remains a deliberately documented gap: the `private` (secure
DM'd choices) duel option is accepted as a parameter and passed through to
`start_pvp_battle`, but nothing downstream implements it yet -- the new
`BattleView` has no DM-fallback path the way the monolith's
`viewer_messages`/`secure_fallback` internals did. `allow_items` is
similarly accepted but not yet enforced (the Bag button has no
"disabled because this duel excludes items" state).
"""

from __future__ import annotations

import json
from math import inf
from typing import Literal

from discord import AllowedMentions, ButtonStyle, Embed, Interaction, User, app_commands
from discord.ui import Button, View

from pokemon.commands import repository
from pokemon.commands.battle_state import is_in_battle
from pokemon.commands.display import cmd_mention
from pokemon.commands.groups import pokemon_group
from pokemon.commands.stats import pokemon_max_hp
from pokemon.commands.team import get_battle_team
from pokemon.dex.items_repo import BATTLE_ITEMS
from pokemon.dex.species import get_default_repository as get_species_repo

# (low_frac, high_frac, min_flat, max_flat) -- ported verbatim from
# `POKEDOLLARS_GAIN["duel_win"]`.
DUEL_WIN_REWARD_RANGE: tuple[float, float, float, float] = (0.03, 0.08, 50, 400)


def normalize_pokemon_for_practice(p: dict, *, set_level: bool, restore_condition: bool) -> dict:
    """Ported verbatim from `_normalize_pokemon_for_practice`."""
    q = dict(p)
    if set_level:
        q["level"] = 50
    if restore_condition:
        q["current_hp"] = None
        q["fainted_at"] = None
        q["hurt_at"] = None
        q["status_cond"] = None
        q["pp_reset_at"] = None
        moves = json.loads(q.get("moves") or "[]")
        for mv in moves:
            mv["pp"] = mv.get("max_pp", mv.get("pp", 0))
        q["moves"] = json.dumps(moves)
    elif set_level and q.get("current_hp") is not None and not q.get("fainted_at"):
        species = get_species_repo().get(q["species_id"])
        if species:
            old_max = pokemon_max_hp(p, species)
            new_max = pokemon_max_hp(q, species)
            pct = (p["current_hp"] / old_max) if old_max > 0 else 1.0
            q["current_hp"] = max(1, min(new_max, round(new_max * pct)))
    return q


async def duel_win_reward(loser_uid: int) -> int:
    """A winner's Pokédollar prize, scaled off the loser's own balance
    (capped so a duel can never bankrupt the loser). Ported verbatim from
    `_duel_win_reward`. Exported for the battle engine's eventual win-
    resolution hook to call -- see this module's docstring."""
    import random

    wallet = await repository.get_pokedollars(loser_uid)
    lf, hf, lo, hi = DUEL_WIN_REWARD_RANGE
    return min(max(int(wallet * random.uniform(lf, hf)), random.randint(int(lo), int(hi))), wallet)


def find_cheapest_heal_plan(deficit: int, item_qties: dict[str, int]) -> tuple[float, list]:
    """What combination of a trainer's healing items most cheaply covers
    `deficit` HP -- used by the battle engine to offer an auto-heal
    suggestion between duel rounds. Ported verbatim from
    `_find_cheapest_heal_plan`."""
    if deficit <= 0:
        return 0, []

    heal_items = [
        name for name, data in BATTLE_ITEMS.items()
        if data.get("type") in {"heal", "full"} and item_qties.get(name, 0) > 0
    ]
    if not heal_items:
        return inf, []

    chunks: list[tuple[str, int, int, int]] = []
    for name in heal_items:
        data = BATTLE_ITEMS[name]
        heal = data.get("heal_amount", 20)
        cost = data.get("price", 100)
        chunks.append((name, heal, cost, item_qties.get(name, 0)))

    chunks.sort(key=lambda c: c[2] / max(1, c[1]))
    remaining = deficit
    plan: list[tuple[str, int]] = []
    total_cost = 0
    for name, heal, cost, qty in chunks:
        if remaining <= 0:
            break
        needed = min(qty, -(-remaining // heal))
        if needed <= 0:
            continue
        plan.append((name, needed))
        total_cost += needed * cost
        remaining -= needed * heal
    if remaining > 0:
        return inf, []
    return total_cost, plan


class DuelOfferView(View):
    """Accept/decline for the recipients, plus a Cancel the challenger can
    use to retract the offer at any time. Ported verbatim from
    `_DuelOfferView`."""

    def __init__(self, challenger_id: int, required: list[int],
                 accept_label: str = "Accept", decline_label: str = "Decline"):
        super().__init__(timeout=120)
        self.challenger_id = challenger_id
        self.required = set(required)
        self.accepted: set[int] = set()
        self.declined = False
        self.cancelled = False

        accept_btn = Button(label=accept_label, style=ButtonStyle.success, emoji="✅")
        decline_btn = Button(label=decline_label, style=ButtonStyle.danger, emoji="❌")
        cancel_btn = Button(label="Cancel", style=ButtonStyle.gray, emoji="🚫")
        accept_btn.callback = self._on_accept
        decline_btn.callback = self._on_decline
        cancel_btn.callback = self._on_cancel
        self.add_item(accept_btn)
        self.add_item(decline_btn)
        self.add_item(cancel_btn)

    async def _on_accept(self, ctx: Interaction):
        if ctx.user.id not in self.required:
            await ctx.response.send_message("This offer isn't addressed to you.", ephemeral=True)
            return
        self.accepted.add(ctx.user.id)
        if self.accepted >= self.required:
            for item in self.children:
                item.disabled = True
            await ctx.response.edit_message(content="Offer accepted!", view=self)
            self.stop()
        else:
            await ctx.response.send_message("Accepted! Waiting on the others...", ephemeral=True)

    async def _on_decline(self, ctx: Interaction):
        if ctx.user.id not in self.required:
            await ctx.response.send_message("This offer isn't addressed to you.", ephemeral=True)
            return
        self.declined = True
        for item in self.children:
            item.disabled = True
        await ctx.response.edit_message(content="Offer declined.", view=self)
        self.stop()

    async def _on_cancel(self, ctx: Interaction):
        if ctx.user.id != self.challenger_id:
            await ctx.response.send_message("Only the person who made the offer can cancel it.", ephemeral=True)
            return
        self.cancelled = True
        self.declined = True
        for item in self.children:
            item.disabled = True
        await ctx.response.edit_message(content="Offer cancelled.", view=self)
        self.stop()

    async def on_timeout(self):
        self.declined = True


async def start_pvp_battle(
    bot, rosters: dict[int, list[dict]], side_a_ids: list[int], side_b_ids: list[int], *,
    format: str, practice: bool, allow_items: bool, private: bool, channel_id: int, guild_id: int | None,
):
    """Hands off to the battle engine to actually open a live PvP battle.
    Soft dependency -- see this module's docstring for why. Degrades to a
    plain roster-summary embed (still useful for testing the offer/roster-
    gathering flow end to end) until the real integration lands; whoever
    adds it should match this signature.
    """
    try:
        from pokemon.ui.battle_view import start_pvp_encounter
    except ImportError:
        def _side_line(ids: list[int]) -> str:
            return ", ".join(f"<@{uid}> ({len(rosters[uid])} Pokémon)" for uid in ids)

        embed = Embed(
            title=f"⚔️ {format} Duel{' (Practice)' if practice else ''}",
            description=(
                f"{_side_line(side_a_ids)}\n**vs**\n{_side_line(side_b_ids)}\n\n"
                "-# The battle engine isn't wired up yet, so this is a preview of the matchup only."),
            color=0x3B4252)
        return embed, None, None
    return await start_pvp_encounter(
        bot, rosters, side_a_ids, side_b_ids, format=format, practice=practice, allow_items=allow_items,
        private=private, channel_id=channel_id, guild_id=guild_id)


async def _handle_boss_duel(
    ctx: Interaction, opponent: User, format: str, your_partner: User | None, practice: bool, allow_items: bool,
) -> None:
    """The boss-battle branch of `/pokemon duel` -- generalized from the
    monolith's hardcoded `opponent.id == CONTORE.user.id` branch. Ported
    behavior: practice mode is disabled against any boss, the boss never
    needs to accept (only the challenger's optional Doubles 2v2 partner
    does), and the challenge/accept UI uses the boss's configured
    DEFY/BACK-DOWN-style labels instead of the generic duel offer."""
    from pokemon.commands.boss import get_boss_team, is_boss, start_boss_battle

    if practice:
        await ctx.response.send_message("Practice battles aren't available against a boss.", ephemeral=True)
        return
    if not await repository.has_starter(ctx.user.id):
        await ctx.response.send_message(
            f"You haven't started your Pokémon journey yet! Use {cmd_mention('pokemon starter')} first.",
            ephemeral=True)
        return
    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("Finish your current battle first!", ephemeral=True)
        return

    cfg = await repository.get_boss_config(opponent.id)
    boss_team = await get_boss_team(opponent.id)
    if not boss_team:
        await ctx.response.send_message(
            f"**{cfg['display_name']}** doesn't have a team configured yet.", ephemeral=True)
        return

    is_doubles = format in ("Doubles", "Doubles 2v2")
    has_partner = (
        format == "Doubles 2v2" and your_partner is not None
        and your_partner.id != ctx.user.id and not await is_boss(your_partner.id))
    if format == "Doubles 2v2" and not has_partner:
        await ctx.response.send_message(
            "Doubles 2v2 against a boss needs a valid `your_partner` (a different, non-boss trainer).",
            ephemeral=True)
        return

    participants = [ctx.user] + ([your_partner] if has_partner else [])
    for u in participants:
        if not await repository.has_starter(u.id):
            await ctx.response.send_message(
                f"{u.mention} hasn't started their Pokémon journey yet.", ephemeral=True,
                allowed_mentions=AllowedMentions.none())
            return
        if is_in_battle(u.id):
            await ctx.response.send_message(
                f"{u.mention} is already in a battle.", ephemeral=True, allowed_mentions=AllowedMentions.none())
            return

    need_each = 2 if is_doubles and not has_partner else 1
    for u in participants:
        team = [p for p in await get_battle_team(u.id) if not p["fainted_at"]]
        if len(team) < need_each:
            await ctx.response.send_message(
                f"{u.mention} needs at least **{need_each}** healthy Pokémon for this format.", ephemeral=True,
                allowed_mentions=AllowedMentions.none())
            return

    required = [u.id for u in participants if u.id != ctx.user.id]
    accept_label = cfg.get("accept_label", "DEFY")
    decline_label = cfg.get("decline_label", "BACK DOWN")
    view = DuelOfferView(ctx.user.id, required or [ctx.user.id], accept_label, decline_label)
    if required:
        await ctx.response.send_message(
            f"{your_partner.mention}, {ctx.user.mention} wants your help against **{cfg['display_name']}**! "
            "Do you dare?",
            view=view, allowed_mentions=AllowedMentions(users=True))
    else:
        await ctx.response.send_message(
            cfg.get("challenge_line", "You will fail."), view=view, allowed_mentions=AllowedMentions.none())
    await view.wait()
    needed = set(required) if required else {ctx.user.id}
    if view.declined or view.accepted < needed:
        return

    rosters: dict[int, list[dict]] = {}
    for u in participants:
        team = [p for p in await get_battle_team(u.id) if not p["fainted_at"]]
        if len(team) < need_each:
            await ctx.followup.send(
                f"{u.mention} no longer has at least **{need_each}** healthy Pokémon. Battle cancelled.",
                allowed_mentions=AllowedMentions.none())
            return
        rosters[u.id] = await get_battle_team(u.id)

    embed, file, view = await start_boss_battle(
        ctx.client, participants, opponent.id, boss_team, format=format, allow_items=allow_items,
        channel_id=ctx.channel_id, guild_id=ctx.guild_id)
    if view is None:
        await ctx.followup.send(embed=embed)
    else:
        message = await ctx.followup.send(embed=embed, file=file, view=view, wait=True)
        view.message = message


@pokemon_group.command(name="duel", description="Challenge trainer(s) to a Pokémon battle")
@app_commands.describe(
    opponent="The trainer you're challenging (or one of the two opposing trainers, for 2v2)",
    format="Singles (1v1), Doubles (you & opponent each run 2 Pokémon), or Doubles 2v2 (4 separate trainers)",
    your_partner="Your tag partner (required for Doubles 2v2)",
    opponent_partner="Your opponent's tag partner (required for Doubles 2v2)",
    practice="Disables HP and PP loss during the duel",
    private="Hide your move/item/switch choices from your opponent (not yet supported -- see module docs)",
    allow_items="Allow using Bag items during the duel",
    normalize_levels="Practice only: sets every Pokémon's level to 50",
    normalize_condition="Practice only: full HP, no status, no faints, full PP for every Pokémon")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def pokemon_duel(
    ctx: Interaction, opponent: User,
    format: Literal["Singles", "Doubles", "Doubles 2v2"] = "Singles",
    your_partner: User | None = None, opponent_partner: User | None = None,
    practice: bool = False, private: bool = False, allow_items: bool = True,
    normalize_levels: bool = False, normalize_condition: bool = False,
):
    if opponent.id == ctx.user.id:
        await ctx.response.send_message("Pick a real opponent.", ephemeral=True)
        return

    from pokemon.commands.boss import is_boss
    if await is_boss(opponent.id):
        await _handle_boss_duel(ctx, opponent, format, your_partner, practice, allow_items)
        return

    if opponent.bot:
        await ctx.response.send_message(
            "Challenging a bot account isn't available unless it's set up as a boss battle "
            "(see `/pokemon boss set`).", ephemeral=True)
        return
    if (normalize_levels or normalize_condition) and not practice:
        await ctx.response.send_message(
            "`normalize_levels`/`normalize_condition` only apply to practice duels. Enable `practice` mode to "
            "use them.", ephemeral=True)
        return
    if not await repository.has_starter(ctx.user.id):
        await ctx.response.send_message(
            f"You haven't started your Pokémon journey yet! Use {cmd_mention('pokemon starter')} first.",
            ephemeral=True)
        return

    if is_in_battle(ctx.user.id):
        await ctx.response.send_message("Finish your current battle first!", ephemeral=True)
        return

    for u in [opponent] + ([your_partner, opponent_partner] if format == "Doubles 2v2" else []):
        if u and not await repository.has_starter(u.id):
            await ctx.response.send_message(
                f"{u.mention} hasn't started their Pokémon journey yet! Tell them to use "
                f"{cmd_mention('pokemon starter')} first.", ephemeral=True, allowed_mentions=AllowedMentions.none())
            return

    if format == "Doubles 2v2":
        if not your_partner or not opponent_partner:
            await ctx.response.send_message(
                "Doubles 2v2 needs both `your_partner` and `opponent_partner` set.", ephemeral=True)
            return
        squad = [ctx.user, opponent, your_partner, opponent_partner]
        if len({u.id for u in squad}) < 4 or any(u.bot for u in squad):
            await ctx.response.send_message("All four trainers must be different, non-bot users.", ephemeral=True)
            return
        side_a_trainers, side_b_trainers, need_each = [ctx.user, your_partner], [opponent, opponent_partner], 1
    else:
        side_a_trainers, side_b_trainers = [ctx.user], [opponent]
        need_each = 2 if format == "Doubles" else 1

    all_trainers = side_a_trainers + side_b_trainers
    for u in all_trainers:
        if is_in_battle(u.id):
            await ctx.response.send_message(
                f"{u.mention} is already in a battle.", ephemeral=True, allowed_mentions=AllowedMentions.none())
            return

    async def _gather_roster(u: User) -> list[dict]:
        full_team = await get_battle_team(u.id)
        if practice and (normalize_levels or normalize_condition):
            full_team = [
                normalize_pokemon_for_practice(p, set_level=normalize_levels, restore_condition=normalize_condition)
                for p in full_team]
        return full_team

    for u in all_trainers:
        team = [p for p in await _gather_roster(u) if not p["fainted_at"]]
        if len(team) < need_each:
            await ctx.response.send_message(
                f"{u.mention} needs at least **{need_each}** healthy Pokémon on their team for this format.",
                ephemeral=True, allowed_mentions=AllowedMentions.none())
            return

    required = [u.id for u in all_trainers if u.id != ctx.user.id]
    view = DuelOfferView(ctx.user.id, required)
    await ctx.response.send_message(
        f"{ctx.user.mention} is proposing a **{'Practice ' if practice else ''}{format}** battle!\n"
        + " ".join(f"<@{u}>" for u in required) + ", do you accept?",
        view=view, allowed_mentions=AllowedMentions(users=True))
    await view.wait()
    if view.declined or view.accepted < set(required):
        return

    rosters: dict[int, list[dict]] = {}
    for u in all_trainers:
        full_team = await _gather_roster(u)
        team = [p for p in full_team if not p["fainted_at"]]
        if len(team) < need_each:
            await ctx.followup.send(
                f"{u.mention} no longer has at least **{need_each}** healthy Pokémon. Battle cancelled.",
                allowed_mentions=AllowedMentions.none())
            return
        rosters[u.id] = full_team

    embed, file, view = await start_pvp_battle(
        ctx.client, rosters, [u.id for u in side_a_trainers], [u.id for u in side_b_trainers],
        format=format, practice=practice, allow_items=allow_items, private=private,
        channel_id=ctx.channel_id, guild_id=ctx.guild_id)
    if view is None:
        await ctx.followup.send(embed=embed)
    else:
        message = await ctx.followup.send(embed=embed, file=file, view=view, wait=True)
        view.message = message


async def setup(bot) -> None:
    """No-op for `bot.load_extension()` compatibility -- see `config.py`'s
    `setup` for why."""

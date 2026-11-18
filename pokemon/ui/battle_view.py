"""The Discord-facing battle View. Old code's BattleView (contore.py:
20937-24462, ~3500 lines) mixed rendering, input collection, turn
resolution, move execution, catching, XP awarding, and DB persistence all
in one god-class -- a direct violation of the "talks to BattleEngine's
public interface only" boundary in pokemon/ui/OWNER.md. This version keeps
only rendering and input collection; everything else is delegated to
BattleEngine (see actions.py for the exact assumed interface).

Flow: BattleView shows the main menu -> Fight opens MoveSelectView ->
picking a move that needs a target prompt (doubles only, see
targeting.needs_target_prompt) opens TargetSelectView -> the resulting
action is submitted to the engine. Switch/Bag follow the same
submit-and-return-to-BattleView pattern.
"""
from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING

from discord import ButtonStyle, Embed, Interaction, SelectOption
from discord.ui import View, Button, Select

from ..battle.targeting import needs_target_prompt, target_choices
from .actions import MoveAction, SwitchAction, ItemAction, ForfeitAction
from .battle_render import render_battle_png

if TYPE_CHECKING:
    from ..battle.battler import Battler
    from ..battle.field import Side


# -- Battler -> render_battle_png's plain-dict slot_data ---------------------

def _battler_to_slot_data(battler: "Battler | None") -> dict | None:
    """The one adapter function bridging the new Battler model to
    battle_render.py's dict-shaped input, replacing old code's
    `_battle_render_slot` (contore.py:14311+). Keeping this conversion
    here (rather than inside battle_render.py) is what keeps that module
    Battler-agnostic -- see its module docstring.

    ASSUMES a handful of Identity fields beyond what battler.py's
    illustrative skeleton currently has (species_id, shiny, gender, form,
    mega_evolved) -- see abilities/NOTES_FOR_TRACK_C.md #1 for the same
    "Identity needs more fields eventually" note this echoes.
    """
    if battler is None:
        return None
    identity = battler.identity
    return {
        "name": identity.name,
        "level": identity.level,
        "hp": battler.vitals.hp,
        "max_hp": battler.vitals.max_hp,
        "status": battler.vitals.status,
        "gender": getattr(identity, "gender", "genderless"),
        "species_id": identity.species_id,
        "render_species_id": getattr(identity, "render_species_id", identity.species_id),
        "shiny": getattr(identity, "shiny", False),
        "mega_evolved": getattr(identity, "mega_evolved", False),
        "substitute_active": battler.has(_substitute_type()),
        "form": getattr(identity, "form", None),
        "render_form": getattr(identity, "render_form", None),
        "caught_ball": getattr(battler, "caught_ball", None),
    }


def _substitute_type():
    from ..battle.volatile import Substitute
    return Substitute


def _party_summary(side: "Side") -> list[dict] | None:
    """Old code's `party` list for the row of Poké Balls under each side's
    HP panel; None for wild encounters, which have no roster to show."""
    roster = getattr(side, "roster", None)
    if roster is None:
        return None
    return [
        {"fainted": b.fainted, "status": b.vitals.status, "active": b in side.active}
        for b in roster
    ]


async def render_battle_message(engine, my_side: "Side", opp_side: "Side", *, is_wild: bool) -> tuple[Embed, "any"]:
    """Builds the (embed, file) pair for the current battle state. Shared
    by BattleView and every sub-view so switching menus never has to
    re-derive this independently."""
    my_slots = [_battler_to_slot_data(b) for b in my_side.active]
    opp_slots = [_battler_to_slot_data(b) for b in opp_side.active]
    weather = engine.field.get_weather() if hasattr(engine.field, "get_weather") else None
    terrain = engine.field.get_terrain() if hasattr(engine.field, "get_terrain") else None

    file = await render_battle_png(
        my_slots, opp_slots, weather=weather, terrain=terrain,
        my_party=_party_summary(my_side), opp_party=_party_summary(opp_side),
        trainer_ids=None if is_wild else [getattr(opp_side, "trainer_id", 0)],
    )

    return battle_embed(engine), file


def battle_embed(engine) -> Embed:
    """The embed half of `render_battle_message`, with no PNG: animation
    frames (Poke Ball shakes) only change the log text, and re-rendering the
    battle image for each would be slow for nothing -- the image already on
    the message is kept because the edit doesn't touch attachments."""
    embed = Embed(title=f"Turn {engine.turn_number}", color=0x3B4252)
    recent_log = engine.log[-6:] if engine.log else []
    if recent_log:
        embed.description = "\n".join(recent_log)
    embed.set_image(url="attachment://battle.png")
    return embed


async def edit_message(interaction: Interaction, **kwargs) -> None:
    """Edit the battle message from `interaction`, whichever way the
    interaction can currently be answered: the first reply is
    `response.edit_message`, anything after a defer/earlier edit has to be
    `edit_original_response`."""
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    else:
        await interaction.response.edit_message(**kwargs)


# -- main menu ----------------------------------------------------------------

async def start_wild_encounter(
    bot, user, team: list[dict], encounter, *, channel_id: int, guild_id: int | None,
    spawn: dict | None = None,
):
    """The entry point `pokemon.commands.hunt`'s `start_wild_battle` soft-
    imports (see that function's docstring -- this fulfills the exact
    signature contract it already documented). Data access (owned-Pokemon
    rows -> Battler, server ruleset lookup) is delegated to
    `pokemon.commands.battle_bridge` rather than done here, matching this
    module's own "rendering and input collection only" boundary.

    Returns `(embed, file, view)`: the initial battle screen plus its
    `BattleView`, already attached to `engine`. The caller (`hunt.py`) is
    responsible for actually sending it and then setting `view.message` to
    the sent `discord.Message` (needed for the `View`'s timeout handler to
    edit it later) -- see `hunt.py`'s call site for the exact pattern.
    """
    from pokemon.commands import repository
    from pokemon.commands.battle_bridge import build_wild_battle_engine, populate_bags
    from pokemon.generations.ruleset import ALL_GENERATIONS, GEN_6

    cfg = await repository.get_pokemon_config(guild_id) if guild_id else {}
    ruleset = ALL_GENERATIONS.get(cfg.get("generation", 6), GEN_6)

    engine, my_side, opp_side = build_wild_battle_engine(bot, user, team, encounter, ruleset)
    await populate_bags(my_side)  # the Bag (and catching) reads these
    view = BattleView(engine, my_side, opp_side, is_wild=True, ai_side=opp_side, bot=bot, guild_id=guild_id,
                      spawn=spawn, channel_id=channel_id)
    embed, file = await render_battle_message(engine, my_side, opp_side, is_wild=True)
    return embed, file, view


async def start_pvp_encounter(
    bot, rosters: dict[int, list[dict]], side_a_ids: list[int], side_b_ids: list[int], *,
    format: str, practice: bool, allow_items: bool, private: bool, channel_id: int, guild_id: int | None,
):
    """The entry point `pokemon.commands.duel`'s `start_pvp_battle` soft-
    imports -- fulfills that function's already-documented signature, the
    same way `start_wild_encounter` above fulfills `hunt.py`'s.

    `guild_id` can be `None` (duels are allowed in DMs -- see
    `pokemon_duel`'s `@app_commands.allowed_contexts`); the ruleset falls
    back to Gen 6 in that case, same as `start_wild_encounter` falls back
    when a wild encounter's own guild has no configured generation.

    `practice`'s HP/PP normalization already happened before this was
    called (`normalize_pokemon_for_practice`, applied to `rosters` by
    `pokemon_duel` itself) -- nothing further to do with that flag here.
    `allow_items` is enforced (no bag is loaded and the Bag button is
    hidden when False). `private` would need the DM'd-choices flow this
    module's own docstring flags as not ported -- accepted but not enforced.
    """
    from pokemon.commands import repository
    from pokemon.commands.battle_bridge import build_pvp_battle_engine
    from pokemon.generations.ruleset import ALL_GENERATIONS, GEN_6

    cfg = await repository.get_pokemon_config(guild_id) if guild_id else {}
    ruleset = ALL_GENERATIONS.get(cfg.get("generation", 6), GEN_6)

    engine, side_a, side_b = build_pvp_battle_engine(
        bot, rosters, side_a_ids, side_b_ids, ruleset, format=format)

    # Two independent messages, one per side's perspective, kept in sync
    # via `sibling` -- see `BattleView.submit_and_advance`/`_refresh`.
    # `render_battle_message` is called twice (not once, reused) since
    # each needs its own `discord.File`: a `File` object is single-use
    # once attached to a sent message.
    if allow_items:
        from pokemon.commands.battle_bridge import populate_bags
        await populate_bags(side_a, side_b)
    view_a = BattleView(engine, side_a, side_b, is_wild=False, practice=practice, bot=bot, guild_id=guild_id,
                        allow_items=allow_items)
    view_b = BattleView(engine, side_b, side_a, is_wild=False, sibling=view_a, practice=practice,
                         bot=bot, guild_id=guild_id, allow_items=allow_items)
    view_a.sibling = view_b

    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    embed_b, file_b = await render_battle_message(engine, side_b, side_a, is_wild=False)
    view_b.message = await channel.send(embed=embed_b, file=file_b, view=view_b)

    embed_a, file_a = await render_battle_message(engine, side_a, side_b, is_wild=False)
    return embed_a, file_a, view_a


async def start_boss_encounter(
    bot, challengers: list, boss_user_id: int, boss_team: list[dict], *,
    format: str, allow_items: bool, channel_id: int, guild_id: int | None,
):
    """The entry point `pokemon.commands.boss`'s `start_boss_battle` soft-
    imports -- fulfills that function's already-documented signature.

    Unlike `start_pvp_encounter`, this only ever needs ONE message: the
    boss's side is `ai_side`-driven (see `pokemon.battle.ai` -- the same
    interim heuristic wild encounters use, not the monolith's real,
    much richer `choose_boss_action`), so there's no second human to mirror
    state to. `challengers` is 1 or 2 `discord.User`s (2 only for
    "Doubles 2v2" against a boss, per `pokemon.commands.duel._handle_boss_duel`);
    their rosters are re-fetched here rather than passed in, since the
    caller only ever had them as separate per-user lists, not the
    `{uid: roster}` shape `build_boss_battle_engine` needs.
    """
    from pokemon.commands import repository
    from pokemon.commands.battle_bridge import build_boss_battle_engine
    from pokemon.commands.team import get_battle_team
    from pokemon.generations.ruleset import ALL_GENERATIONS, GEN_6

    cfg = await repository.get_pokemon_config(guild_id) if guild_id else {}
    ruleset = ALL_GENERATIONS.get(cfg.get("generation", 6), GEN_6)

    rosters = {u.id: await get_battle_team(u.id) for u in challengers}
    engine, challenger_side, boss_side = build_boss_battle_engine(
        bot, rosters, [u.id for u in challengers], boss_user_id, boss_team, ruleset, format=format)
    if allow_items:
        from pokemon.commands.battle_bridge import populate_bags
        await populate_bags(challenger_side)
    view = BattleView(engine, challenger_side, boss_side, is_wild=False, ai_side=boss_side,
                       bot=bot, guild_id=guild_id, allow_items=allow_items)
    embed, file = await render_battle_message(engine, challenger_side, boss_side, is_wild=False)
    return embed, file, view


class BattleView(View):
    def __init__(self, engine, my_side: "Side", opp_side: "Side", *, is_wild: bool,
                 ai_side: "Side | None" = None, sibling: "BattleView | None" = None,
                 practice: bool = False, bot=None, guild_id: int | None = None, timeout: float = 180,
                 allow_items: bool = True, spawn: dict | None = None, channel_id: int | None = None):
        super().__init__(timeout=timeout)
        self.engine = engine
        # False hides the Bag entirely (PvP/boss `allow_items=False`).
        self.allow_items = allow_items
        # A passive spawn this wild battle was started from (see
        # `spawn_message_hook`): lets a catch/defeat close that spawn and a
        # Run/loss re-open it. None for `/pokemon hunt`, which has no spawn.
        self.spawn = spawn
        self.channel_id = channel_id
        self.my_side = my_side
        self.opp_side = opp_side
        self.is_wild = is_wild
        # A practice duel: no persistence, no rewards, on battle end -- see
        # `_show_battle_end`. Never true for a wild encounter or boss
        # battle (there's no "practice wild encounter" concept).
        self.practice = practice
        # For achievement-progress syncing on battle end (needs a guild_id
        # to check against, and a bot to actually grant/announce one) --
        # `None` is fine, it just means that step is silently skipped
        # (e.g. a duel in DMs, which has no guild to check achievements
        # against in the first place).
        self.bot = bot
        self.guild_id = guild_id
        # Set for wild encounters and boss battles (opp_side); left None
        # for a human-vs-human PvP duel, where BOTH sides submit through
        # their own BattleView instance instead -- see
        # `start_pvp_encounter`/`start_wild_encounter`/`start_boss_encounter`
        # below for who passes what.
        self.ai_side = ai_side
        # A human PvP opponent's OWN BattleView on their OWN message,
        # sharing this same `engine` -- see `start_pvp_encounter`. Needed
        # because `submit_and_advance` only ever edits *its own* message
        # via the interaction that triggered it; when a shared turn
        # actually resolves, the other player's message also needs
        # refreshing, and there's no interaction available to do that with
        # (they didn't click anything just now) -- hence `_refresh()`
        # editing `sibling.message` directly instead.
        self.sibling = sibling
        self.message = None
        self._build_menu()

    async def interaction_check(self, interaction: Interaction) -> bool:
        """Restricts every button on this message to whoever actually
        controls `my_side` -- without this, a wild-encounter message or
        either half of a PvP duel's mirrored messages could be clicked (and
        the battle controlled) by any bystander, since discord.py's `View`
        doesn't restrict who can press its buttons by default."""
        allowed = {self.my_side.trainer_id, *(getattr(b, "owner_id", None) for b in self.my_side.roster)}
        allowed.discard(None)
        if interaction.user.id not in allowed:
            await interaction.response.send_message("This isn't your battle to control.", ephemeral=True)
            return False
        return True

    def _build_menu(self) -> None:
        self.clear_items()
        if self.engine.is_awaiting_replacement(self.my_side):
            # Forced replacement: the only legal choice is who comes in next.
            pick = Button(label="Send out next Pok\u00e9mon", emoji="\U0001F504",
                          style=ButtonStyle.success, custom_id="bv:switch")
            pick.callback = self._on_switch
            self.add_item(pick)
            if self.engine.has_pending(self.my_side) and not self._choices_done():
                # two replacements owed and one already chosen: let the trainer take it back
                undo = Button(label="Undo", emoji="\u21A9\ufe0f", style=ButtonStyle.grey, custom_id="bv:undo")
                undo.callback = self._on_undo
                self.add_item(undo)
            return
        if self.engine._awaiting_replacement:
            # The OPPONENT (a human, in PvP) is choosing a replacement.
            wait = Button(label="Waiting for opponent to send out their next Pok\u00e9mon...",
                          style=ButtonStyle.grey, disabled=True, custom_id="bv:wait")
            self.add_item(wait)
            return
        actor = self._menu_actor()
        fight = Button(label=f"Fight ({actor.identity.name})" if actor is not None else "Fight", emoji="\u2694\ufe0f",
                       style=ButtonStyle.danger, custom_id="bv:fight")
        fight.callback = self._on_fight
        self.add_item(fight)

        if self.allow_items:
            bag = Button(label="Bag", emoji="\U0001F392", style=ButtonStyle.blurple, custom_id="bv:bag")
            bag.callback = self._on_bag
            self.add_item(bag)

        switch = Button(label="Switch", emoji="\U0001F504", style=ButtonStyle.blurple, custom_id="bv:switch")
        switch.callback = self._on_switch
        self.add_item(switch)

        if self.engine.has_pending(self.my_side) and not self._choices_done():
            # Doubles: some slots already have an action, the rest do not -- let the trainer take theirs back and choose again.
            undo = Button(label="Undo", emoji="\u21A9\ufe0f", style=ButtonStyle.grey, custom_id="bv:undo")
            undo.callback = self._on_undo
            self.add_item(undo)

        exit_label = "Run" if self.is_wild else "Forfeit"
        exit_btn = Button(label=exit_label, emoji="\U0001F6AA", style=ButtonStyle.grey, custom_id="bv:exit")
        exit_btn.callback = self._on_exit
        self.add_item(exit_btn)

    async def render_and_show(self, interaction: Interaction) -> None:
        embed, file = await render_battle_message(self.engine, self.my_side, self.opp_side, is_wild=self.is_wild)
        await edit_message(interaction, embed=embed, attachments=[file], view=self)

    async def _on_fight(self, interaction: Interaction) -> None:
        active = await self._actor_for(interaction)
        if active is None:
            return
        moves = self.engine.legal_moves(active)
        view = MoveSelectView(self, active, moves)
        embed, file = await render_battle_message(self.engine, self.my_side, self.opp_side, is_wild=self.is_wild)
        await interaction.response.edit_message(embed=embed, attachments=[file], view=view)

    async def _on_bag(self, interaction: Interaction) -> None:
        active = await self._actor_for(interaction)
        if active is None:
            return
        view = BagCategoryView(self, active)
        view._build_for(interaction.user.id)
        embed, file = await render_battle_message(self.engine, self.my_side, self.opp_side, is_wild=self.is_wild)
        await interaction.response.edit_message(embed=embed, attachments=[file], view=view)

    async def _on_switch(self, interaction: Interaction) -> None:
        forced = self.engine.is_awaiting_replacement(self.my_side)
        uid = interaction.user.id
        active = self._vacated_battler(uid) if forced else await self._actor_for(interaction)
        if active is None:
            return
        # a Pokemon already queued to come in for the OTHER slot is not on offer again; in Doubles 2v2 a trainer only sends out their own bench
        taken = self.engine.chosen_replacements(self.my_side)
        options = [b for b in self.engine.legal_switches(self.my_side) if b not in taken and self._owns(b, uid)]
        if forced and not options:
            await interaction.response.send_message("You have no Pok\u00e9mon left to send out \u2014 your partner will choose the replacement.", ephemeral=True)
            return
        view = SwitchSelectView(self, active, options)
        embed, file = await render_battle_message(self.engine, self.my_side, self.opp_side, is_wild=self.is_wild)
        await interaction.response.edit_message(embed=embed, attachments=[file], view=view)

    async def _on_exit(self, interaction: Interaction) -> None:
        if self.is_wild:
            await interaction.response.edit_message(content="Got away safely!", embed=None, attachments=[], view=None)
            return
        living = [b for b in self.my_side.active if b is not None and not b.fainted] or [self._my_active_battler()]
        if not self.engine._awaiting_replacement:
            # a forfeit has to cover EVERY living active (the engine resolves a side only once each one has an action); the last one goes through the normal path below
            for b in living[1:]:
                await self.engine.submit_action(self.my_side, ForfeitAction(user=b))
        await self.submit_and_advance(interaction, ForfeitAction(user=living[0]))

    async def _on_undo(self, interaction: Interaction) -> None:
        """Doubles: withdraw this trainer's queued choices for the turn (not the AI's) and show the menu for the first of their slots again."""
        uid = interaction.user.id
        self.engine.retract_actions(self.my_side, [b for b in self.my_side.active if b is not None and self._owns(b, uid)])
        self._build_menu()
        await self.render_and_show(interaction)

    def _my_active_battler(self) -> "Battler":
        # Prefer a living active (doubles: one slot may be down while the
        # other still fights); fall back to any occupied slot.
        return (next((b for b in self.my_side.active if b is not None and not b.fainted), None)
                or next(b for b in self.my_side.active if b is not None))

    # -- Doubles: whose turn to choose ------------------------------------------
    # The engine keys actions per acting Pokemon and resolves a side only once EVERY living active has one, so the view walks the slots: each click acts for the
    # next living active that has no action yet (in Doubles 2v2, the next one the CLICKING trainer owns).
    @staticmethod
    def _owns(battler, uid) -> bool:
        owner = getattr(battler, "owner_id", None)
        return uid is None or owner is None or owner == uid

    def _living(self) -> list:
        return [b for b in self.my_side.active if b is not None and not b.fainted]

    def _unchosen(self, uid=None) -> list:
        """Living actives that still owe an action this turn (with `uid`: only that trainer's)."""
        return [b for b in self._living() if not self.engine.has_action(self.my_side, b) and self._owns(b, uid)]

    def _menu_actor(self):
        """The Pokemon the main menu acts for next, shown on the Fight button -- only when every remaining slot belongs to the same trainer (Singles and 2v2 show a plain label)."""
        todo = self._unchosen()
        if len(self._living()) < 2 or not todo or len({getattr(b, "owner_id", None) for b in self._living()}) != 1:
            return None
        return todo[0]

    def _choices_done(self) -> bool:
        """Has this side given every action the engine is waiting on (so only the OPPONENT is outstanding)?"""
        eng = self.engine
        slots = eng._awaiting_replacement.get(id(self.my_side))
        if slots:
            return sum(1 for k in eng._pending if k[0] == id(self.my_side)) >= len(slots)
        return eng._side_ready(self.my_side)

    async def _actor_for(self, interaction: Interaction):
        """The Pokemon this click chooses for, or None after telling the trainer there is nothing left for them to choose."""
        todo = self._unchosen(interaction.user.id)
        if todo:
            return todo[0]
        if len(self._living()) > 1 and any(self._owns(b, interaction.user.id) for b in self._living()):
            await interaction.response.send_message("You've already chosen for your Pok\u00e9mon this turn \u2014 use Undo to change it, or wait for the others.", ephemeral=True)
            return None
        return self._my_active_battler()

    def _vacated_battler(self, uid=None) -> "Battler":
        """The Pokemon whose slot the next forced replacement fills: an awaited slot with no replacement queued yet (the clicking trainer's own Pokemon first)."""
        side, eng = self.my_side, self.engine
        slots = [i for i in (eng._awaiting_replacement.get(id(side)) or []) if i < len(side.active) and side.active[i] is not None]
        free = [side.active[i] for i in slots if not eng.has_action(side, side.active[i])]
        mine = [b for b in free if self._owns(b, uid)]
        for group in (mine, free):
            if group:
                return group[0]
        fainted = next((b for b in side.active if b is not None and b.fainted), None)
        return fainted if fainted is not None else self._my_active_battler()

    async def _throw_frame(self, interaction: Interaction, pause: float, push: bool) -> None:
        """Engine callback for Poke Ball shake animation: show the log as it
        stands right now (embed only, buttons hidden so nothing can be
        clicked mid-turn), then wait `pause`. A Discord hiccup on a frame must
        never abort the turn half-way through a throw -- the catch result is
        already decided -- so it's logged and swallowed."""
        if push:
            try:
                await edit_message(interaction, embed=battle_embed(self.engine), view=None)
            except Exception:
                logging.getLogger(__name__).exception("throw animation frame failed")
        await asyncio.sleep(pause)

    async def _flush_bags(self) -> None:
        """Persist bag items the engine consumed this turn (never in a
        practice duel, where nothing is spent). A DB hiccup must not take
        down the turn -- the usage stays in `bag_used` and is retried on
        the next flush."""
        if self.practice:
            return
        try:
            from pokemon.commands.battle_bridge import flush_bag_usage
            await flush_bag_usage(self.my_side, self.opp_side)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("flush_bag_usage failed; will retry next turn")

    async def _ai_submit_if_needed(self) -> None:
        """Submit the AI side's action (a move, or a forced replacement if
        the engine is waiting on one) unless it already has one pending.
        No-op for PvP views (`ai_side is None`) and while only the HUMAN
        side owes a replacement -- a stray AI MoveAction there would just
        be discarded by the engine."""
        eng = self.engine
        if self.ai_side is None or eng.has_pending(self.ai_side) or not self.ai_side.has_any_left():
            return
        if eng._awaiting_replacement and not eng.is_awaiting_replacement(self.ai_side):
            return
        from pokemon.battle import ai as battle_ai
        for action in battle_ai.choose_next_actions(eng, self.ai_side, self.my_side):       # one per living active (two in Doubles), or one per awaited replacement slot
            await eng.submit_action(self.ai_side, action)

    async def _resolve_and_settle(self) -> None:
        """Resolve the turn, then immediately settle any forced AI
        replacement(s) -- the AI needs no human input to choose one, so the
        player should never see a menu in which the opposing side is still
        "waiting". If the HUMAN also owes a replacement, the AI's choice is
        left pending and both are placed together once they submit."""
        eng = self.engine
        await eng.resolve_turn()
        for _ in range(8):  # bound: at most a couple of slots per side in doubles
            if eng.is_over or self.ai_side is None or not eng.is_awaiting_replacement(self.ai_side):
                break
            await self._ai_submit_if_needed()
            if not eng.ready_to_resolve:
                break
            await eng.resolve_turn()

    async def submit_and_advance(self, interaction: Interaction, action) -> None:
        """Shared tail end for every menu path: submit the chosen action,
        auto-submit the opposing side's action too if it's AI-controlled
        (see `ai_side`), resolve the turn once every side is in, then
        either show the battle-end screen or redraw the main menu for the
        next turn -- on THIS message via `interaction`, and (for a PvP
        duel with a `sibling`) also on the other player's message, since
        one shared `engine.resolve_turn()` call just changed what both of
        them should be looking at."""
        # Guard against a stale/forged choice for the wrong phase: while a
        # forced replacement is pending, ONLY the side that owes one may act
        # and ONLY with a switch -- anything else would be silently
        # discarded by the engine and leave the player staring at a menu
        # that does nothing.
        from ..ui.actions import SwitchAction as _Switch
        if self.engine.is_awaiting_replacement(self.my_side) and not isinstance(action, _Switch):
            await interaction.response.send_message(
                "Choose which Pok\u00e9mon to send out next.", ephemeral=True)    # (a fainted slot OR a pivot move: U-turn, Volt Switch, Baton Pass...)
            return
        if self.engine._awaiting_replacement and not self.engine.is_awaiting_replacement(self.my_side):
            await interaction.response.send_message(
                "Waiting for your opponent to send out their next Pok\u00e9mon.", ephemeral=True)
            return
        # Acknowledge now, then answer via `edit_message` (which picks
        # edit_original_response once deferred). A turn can do real work
        # before the final edit -- a catch's DB writes, duel rewards,
        # animation pauses -- and Discord invalidates an unanswered
        # interaction after 3 s.
        if not interaction.response.is_done():
            await interaction.response.defer()
        await self.engine.submit_action(self.my_side, action)
        await self._ai_submit_if_needed()
        if self.engine.ready_to_resolve:
            self.engine.on_throw = lambda pause, push=True: self._throw_frame(interaction, pause, push)
            try:
                await self._resolve_and_settle()
            finally:
                self.engine.on_throw = None
            await self._flush_bags()
            if self.engine.is_over:
                await self._show_battle_end(interaction)
                if self.sibling is not None:
                    await self.sibling._show_battle_end(None)
                return
            self._build_menu()
            await self.render_and_show(interaction)
            if self.sibling is not None:
                self.sibling._build_menu()
                await self.sibling._refresh()
        elif self.sibling is not None and self._choices_done():
            # Only this side has submitted so far -- show them a waiting
            # state rather than the normal actionable menu again (nothing
            # stops them clicking Fight a second time otherwise, which
            # would just silently overwrite their own pending choice).
            self.clear_items()
            embed, file = await render_battle_message(self.engine, self.my_side, self.opp_side, is_wild=False)
            embed.description = (embed.description + "\n\n" if embed.description else "") + \
                "-# Waiting for your opponent to choose..."
            await edit_message(interaction, embed=embed, attachments=[file], view=self)
        else:
            self._build_menu()
            await self.render_and_show(interaction)

    async def _refresh(self) -> None:
        """Like `render_and_show`, but for updating a message OTHER than
        the one whose interaction is currently being responded to (there
        isn't one) -- used to push a shared turn's result to a PvP
        `sibling`'s message after `submit_and_advance` resolves it."""
        if self.message is None:
            return
        embed, file = await render_battle_message(self.engine, self.my_side, self.opp_side, is_wild=self.is_wild)
        await self.message.edit(embed=embed, attachments=[file], view=self)

    async def _show_battle_end(self, interaction: Interaction | None) -> None:
        winner = self.engine.winner
        caught = self.engine.conclusion == "caught"
        if caught:
            result_line = f"You caught **{self.engine.catch.battler.name}**!"
        elif self.engine.conclusion == "forfeit":
            if self.is_wild:
                result_line = "You got away safely!"
            elif winner is self.my_side:
                result_line = "Your opponent forfeited \u2014 you win!"
            else:
                result_line = "You forfeited the battle."
        elif winner is None:
            result_line = "The battle ended in a draw."
        elif winner is self.my_side:
            result_line = "You won the battle!"
        else:
            result_line = "You lost the battle."

        reward_lines: list[str] = []
        if not self.practice:
            from pokemon.commands import repository
            from pokemon.commands.battle_bridge import (
                award_duel_win_rewards, award_wild_defeat_rewards, persist_battle_result)

            await persist_battle_result(self.my_side, self.opp_side)
            await self._flush_bags()
            # Only one of the two mirrored PvP views (or the one view a
            # wild/boss battle has) should actually grant the reward, not
            # both -- `self.ai_side is not None` (wild/boss) or `self is
            # the winner's own view` (PvP) each uniquely identify that one.
            try:
                if caught:
                    from pokemon.commands.battle_bridge import finalize_catch
                    reward_lines = await finalize_catch(
                        self.bot, self.engine, self.my_side, guild_id=self.guild_id,
                        spawn=self.spawn, channel_id=self.channel_id)
                elif self.is_wild and winner is self.my_side and self.engine.conclusion is None:
                    wild = next((b for b in self.opp_side.active if b is not None), None) or self.opp_side.roster[0]
                    from pokemon.dex.species import get_default_repository as get_species_repo
                    species = get_species_repo().get(wild.identity.species_id)
                    my_active = next((b for b in self.my_side.active if b is not None), None)
                    if species and self.my_side.trainer_id is not None:
                        reward_lines = await award_wild_defeat_rewards(
                            self.my_side.trainer_id,
                            int(my_active.id.split(":", 1)[1]) if my_active else None,
                            species, wild.identity.level,
                            my_active.identity.level if my_active else wild.identity.level,
                            shiny=bool(getattr(self.opp_side.wild_encounter, "shiny", False)))
                    if self.spawn is not None:
                        from pokemon.commands.spawn_message_hook import update_wild_spawn_embed
                        await update_wild_spawn_embed(
                            self.channel_id, self.spawn.get("message_id"), self.spawn, "This Pok\u00e9mon was defeated!")
                elif not self.is_wild and self.ai_side is None and winner is not None and winner is self.my_side:
                    # Only the winner's own view grants the reward -- not
                    # its sibling too -- since both views' submit_and_advance
                    # calls share the same `engine` and would otherwise
                    # double-grant it.
                    loser_side = self.opp_side
                    if loser_side.trainer_id is not None:
                        reward_lines = await award_duel_win_rewards(winner, loser_side.trainer_id)
                        await repository.record_duel_win(winner.trainer_id)
                        await repository.record_duel_loss(loser_side.trainer_id)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("battle-end rewards/catch failed")
                # A failed CATCH must not silently eat the player's Pokemon: say so.
                if caught:
                    reward_lines = ["\u26a0\ufe0f Something went wrong saving your catch \u2014 please report this."]

            # Run / loss against a passive spawn: it's still out there, so
            # let the next person (or a retry) fight it.
            if self.spawn is not None and not caught and not self.spawn.get("claimed"):
                self.spawn["in_battle"] = False

            if self.bot is not None and self.guild_id is not None and self.my_side.trainer_id is not None:
                try:
                    await repository.sync_achievement_progress(self.bot, self.guild_id, self.my_side.trainer_id)
                except Exception:
                    pass  # same reasoning: a bonus, not the battle result itself

        embed, file = await render_battle_message(self.engine, self.my_side, self.opp_side, is_wild=self.is_wild)
        extra = "\n".join([f"**{result_line}**", *reward_lines])
        embed.description = (embed.description + "\n\n" if embed.description else "") + extra
        self.clear_items()
        if interaction is not None:
            await edit_message(interaction, embed=embed, attachments=[file], view=None)
        elif self.message is not None:
            await self.message.edit(embed=embed, attachments=[file], view=None)
        self.stop()


# -- move selection -------------------------------------------------------

class _ChildBattleView(View):
    """Shared by every temporary sub-menu (`MoveSelectView`/
    `TargetSelectView`/`SwitchSelectView`/`ItemSelectView`) so each one
    doesn't repeat the same `interaction_check` delegating to its
    `BattleView` parent's -- see that method's docstring for why it's
    needed at all."""

    def __init__(self, parent: "BattleView", timeout: float = 180):
        super().__init__(timeout=timeout)
        self.parent = parent

    async def interaction_check(self, interaction: Interaction) -> bool:
        return await self.parent.interaction_check(interaction)


class MoveSelectView(_ChildBattleView):
    def __init__(self, parent: BattleView, user: "Battler", moves: list, timeout: float = 180):
        super().__init__(parent, timeout=timeout)
        self.user = user
        self.moves = moves
        self._build()

    def _build(self) -> None:
        self.clear_items()
        options = []
        for m in self.moves[:4]:
            label = f"{m.display_name} ({m.pp_current}/{m.pp_max})"
            description = m.disabled_reason if m.disabled else f"{m.type_name} \u00b7 {m.category.value.title()}"
            options.append(SelectOption(label=label, value=m.slug, description=description, default=False))
        doubles = len(self.parent._living()) > 1
        select = Select(placeholder=f"Choose a move for {self.user.identity.name}..." if doubles else "Choose a move...",
                        options=options or [SelectOption(label="No moves available", value="__none__")])
        select.callback = self._on_pick
        self.add_item(select)

        back = Button(label="Back", style=ButtonStyle.grey, custom_id="bv:back")
        back.callback = self._on_back
        self.add_item(back)

    async def _on_pick(self, interaction: Interaction) -> None:
        slug = interaction.data["values"][0]
        chosen = next((m for m in self.moves if m.slug == slug), None)
        if chosen is None or chosen.disabled:
            await interaction.response.defer()
            return
        if chosen.needs_target_prompt:
            parent = self.parent
            # any other living Pokemon for a selected-target move (both foes AND the ally), the ally for an ally move; a single legal choice is not worth a prompt
            targets = target_choices(parent.engine.moves.spec(chosen.slug).target, self.user, parent.my_side, parent.opp_side)
            if len(targets) > 1:
                view = TargetSelectView(parent, self.user, chosen.slug, targets)
                embed, file = await render_battle_message(parent.engine, parent.my_side, parent.opp_side, is_wild=parent.is_wild)
                await interaction.response.edit_message(embed=embed, attachments=[file], view=view)
                return
            await parent.submit_and_advance(interaction, MoveAction(user=self.user, move_slug=chosen.slug, targets=tuple(targets)))
            return
        action = MoveAction(user=self.user, move_slug=chosen.slug)
        await self.parent.submit_and_advance(interaction, action)

    async def _on_back(self, interaction: Interaction) -> None:
        self.parent._build_menu()
        await self.parent.render_and_show(interaction)


class TargetSelectView(_ChildBattleView):
    """Only ever shown in Doubles -- Singles never prompts, per
    targeting.needs_target_prompt's own docstring. Offers every legal target
    (`targeting.target_choices`): both foes AND the ally."""
    def __init__(self, parent: BattleView, user: "Battler", move_slug: str, targets: list["Battler"], timeout: float = 180):
        super().__init__(parent, timeout=timeout)
        self.user = user
        self.move_slug = move_slug
        ally_side = parent.my_side.active
        names = [t.identity.name for t in targets]
        for t in targets:
            # two Pokemon of one species on the field: tell them apart by position (1 = left, 2 = right)
            name = t.identity.name
            if names.count(name) > 1:
                side = parent.my_side if t in ally_side else parent.opp_side
                name = f"{name} #{side.active.index(t) + 1}"
            if t is user:
                label, style = f"{name} (self)", ButtonStyle.blurple
            elif t in ally_side:
                label, style = f"{name} (ally)", ButtonStyle.success
            else:
                label, style = f"{name} (foe)", ButtonStyle.danger
            btn = Button(label=label, style=style)
            btn.callback = self._make_callback(t)
            self.add_item(btn)
        back = Button(label="Back", style=ButtonStyle.grey)
        back.callback = self._on_back
        self.add_item(back)

    def _make_callback(self, target: "Battler"):
        async def _cb(interaction: Interaction) -> None:
            action = MoveAction(user=self.user, move_slug=self.move_slug, targets=(target,))
            await self.parent.submit_and_advance(interaction, action)
        return _cb

    async def _on_back(self, interaction: Interaction) -> None:
        self.parent._build_menu()
        await self.parent.render_and_show(interaction)


# -- switch selection -------------------------------------------------------

class SwitchSelectView(_ChildBattleView):
    def __init__(self, parent: BattleView, user: "Battler", options: list["Battler"], timeout: float = 180):
        super().__init__(parent, timeout=timeout)
        self.user = user
        self.options = options
        self._build()

    def _build(self) -> None:
        self.clear_items()
        opts = [
            SelectOption(label=f"{b.identity.name} (Lv.{b.identity.level})",
                         value=str(i), description=f"{b.vitals.hp}/{b.vitals.max_hp} HP")
            for i, b in enumerate(self.options)
        ]
        select = Select(placeholder="Choose a Pok\u00e9mon...", options=opts or [SelectOption(label="No other Pok\u00e9mon", value="__none__")])
        select.callback = self._on_pick
        self.add_item(select)

        back = Button(label="Back", style=ButtonStyle.grey)
        back.callback = self._on_back
        self.add_item(back)

    async def _on_pick(self, interaction: Interaction) -> None:
        idx = interaction.data["values"][0]
        if idx == "__none__":
            await interaction.response.defer()
            return
        replacement = self.options[int(idx)]
        action = SwitchAction(user=self.user, replacement=replacement)
        await self.parent.submit_and_advance(interaction, action)

    async def _on_back(self, interaction: Interaction) -> None:
        self.parent._build_menu()
        await self.parent.render_and_show(interaction)


# -- item selection -----------------------------------------------------------

_BAG_TABS = (("ball", "Pok\u00e9 Balls", "\U0001F534"), ("medicine", "Medicine", "\U0001F48A"),
             ("battle", "Battle Items", "\u2694\ufe0f"))


def _queued_items(parent: "BattleView", uid: int) -> list:
    """The ItemActions this trainer has already queued for the turn (Doubles: one slot's Bag choice is waiting for the other slot's)."""
    return [a for (sid, _), a in parent.engine._pending.items()
            if sid == id(parent.my_side) and isinstance(a, ItemAction) and a.trainer_id == uid]


def _my_bag(parent: "BattleView", uid: int) -> dict[str, int]:
    """The trainer's bag MINUS the items already queued this turn, so two slots cannot both spend the last Revive."""
    bag = dict(parent.my_side.bags.get(uid, {}))
    for a in _queued_items(parent, uid):
        bag[a.item_name] = bag.get(a.item_name, 0) - 1
    return bag


class BagCategoryView(_ChildBattleView):
    """First screen of the Bag: one button per tab that actually has
    something in it. Poke Balls are offered in trainer battles too, as in
    the monolith -- the throw is just knocked away (see PokeBall.use)."""

    def __init__(self, parent: "BattleView", user: "Battler", timeout: float = 180):
        super().__init__(parent, timeout=timeout)
        self.user = user
        self._built_for: int | None = None

    def _build_for(self, uid: int) -> None:
        from ..battle.bag import resolve_bag_item
        self.clear_items()
        bag = _my_bag(self.parent, uid)
        for cat, label, emoji in _BAG_TABS:
            n = sum(q for name, q in bag.items() if q > 0 and (it := resolve_bag_item(name)) and it.category == cat)
            btn = Button(label=f"{label} ({n})", emoji=emoji, style=ButtonStyle.blurple, disabled=n == 0)
            btn.callback = self._tab_callback(cat)
            self.add_item(btn)
        back = Button(label="Back", style=ButtonStyle.grey)
        back.callback = self._on_back
        self.add_item(back)

    def _tab_callback(self, cat: str):
        async def cb(interaction: Interaction) -> None:
            view = ItemSelectView(self.parent, self.user, cat, interaction.user.id)
            embed, file = await render_battle_message(
                self.parent.engine, self.parent.my_side, self.parent.opp_side, is_wild=self.parent.is_wild)
            await interaction.response.edit_message(embed=embed, attachments=[file], view=view)
        return cb

    async def _on_back(self, interaction: Interaction) -> None:
        self.parent._build_menu()
        await self.parent.render_and_show(interaction)


class ItemSelectView(_ChildBattleView):
    """One Bag tab: a select of the items the clicking player owns, with
    quantities. Items needing a target go on to `ItemTargetSelectView`."""

    def __init__(self, parent: "BattleView", user: "Battler", category: str, uid: int, timeout: float = 180):
        super().__init__(parent, timeout=timeout)
        self.user, self.category, self.uid = user, category, uid
        self._build()

    def _items(self):
        from ..battle.bag import resolve_bag_item
        from pokemon.dex.items_repo import BATTLE_ITEMS, POKEBALL_DATA
        order = {n: i for i, n in enumerate((*POKEBALL_DATA, *BATTLE_ITEMS))}
        owned = [(resolve_bag_item(n), q) for n, q in _my_bag(self.parent, self.uid).items() if q > 0]
        return sorted(((it, q) for it, q in owned if it and it.category == self.category),
                      key=lambda t: order.get(t[0].name, 999))

    def _build(self) -> None:
        self.clear_items()
        opts = [SelectOption(label=f"{it.name} \u00d7{q}", value=it.name,
                             description=(it.data.get("desc") or "")[:100] or None)
                for it, q in self._items()[:25]]
        select = Select(placeholder="Choose an item...",
                        options=opts or [SelectOption(label="Nothing here", value="__none__")])
        select.callback = self._on_pick
        self.add_item(select)
        back = Button(label="Back", style=ButtonStyle.grey)
        back.callback = self._on_back
        self.add_item(back)

    async def _on_pick(self, interaction: Interaction) -> None:
        from ..battle.bag import resolve_bag_item
        name = interaction.data["values"][0]
        item = resolve_bag_item(name) if name != "__none__" else None
        if item is None:
            await interaction.response.defer()
            return
        if item.target_kind == "none":
            action = ItemAction(user=self.user, item_name=name, trainer_id=self.uid)
            await self.parent.submit_and_advance(interaction, action)
            return
        view = ItemTargetSelectView(self.parent, self.user, item, self.uid)
        embed, file = await render_battle_message(
            self.parent.engine, self.parent.my_side, self.parent.opp_side, is_wild=self.parent.is_wild)
        await interaction.response.edit_message(embed=embed, attachments=[file], view=view)

    async def _on_back(self, interaction: Interaction) -> None:
        view = BagCategoryView(self.parent, self.user)
        view._build_for(interaction.user.id)
        embed, file = await render_battle_message(
            self.parent.engine, self.parent.my_side, self.parent.opp_side, is_wild=self.parent.is_wild)
        await interaction.response.edit_message(embed=embed, attachments=[file], view=view)


class ItemTargetSelectView(_ChildBattleView):
    """Which of YOUR Pokemon to use a medicine on. Only offers targets the
    item would actually do something to (`BagItem.blocked_reason`), so a
    Revive never lists a healthy Pokemon and a Potion never lists a full-HP
    one."""

    def __init__(self, parent: "BattleView", user: "Battler", item, uid: int, timeout: float = 180):
        super().__init__(parent, timeout=timeout)
        self.user, self.item, self.uid = user, item, uid
        side = parent.my_side
        queued = {id(a.target) for a in _queued_items(parent, uid) if a.item_name == item.name}     # the other slot already queued this item for them
        self.candidates = [b for b in side.roster
                           if getattr(b, "owner_id", uid) == uid and id(b) not in queued
                           and item.blocked_reason(parent.engine, side, b) is None][:25]
        self._build()

    def _build(self) -> None:
        self.clear_items()
        opts = [SelectOption(label=f"{b.name} (Lv. {b.identity.level})",
                             value=str(i), description=f"HP {b.vitals.hp}/{b.vitals.max_hp}"
                             + (f" \u00b7 {b.vitals.status}" if b.vitals.status else ""))
                for i, b in enumerate(self.candidates)]
        select = Select(placeholder=f"Use {self.item.name} on...",
                        options=opts or [SelectOption(label="Nothing to use it on", value="__none__")])
        select.callback = self._on_pick
        self.add_item(select)
        back = Button(label="Back", style=ButtonStyle.grey)
        back.callback = self._on_back
        self.add_item(back)

    async def _on_pick(self, interaction: Interaction) -> None:
        val = interaction.data["values"][0]
        if val == "__none__":
            await interaction.response.defer()
            return
        target = self.candidates[int(val)]
        action = ItemAction(user=self.user, item_name=self.item.name, target=target, trainer_id=self.uid)
        await self.parent.submit_and_advance(interaction, action)

    async def _on_back(self, interaction: Interaction) -> None:
        view = BagCategoryView(self.parent, self.user)
        view._build_for(interaction.user.id)
        embed, file = await render_battle_message(
            self.parent.engine, self.parent.my_side, self.parent.opp_side, is_wild=self.parent.is_wild)
        await interaction.response.edit_message(embed=embed, attachments=[file], view=view)

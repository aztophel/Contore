"""Polls: `/poll` creates either an anonymous poll (vote via buttons,
tally hidden until you vote) or a public one (vote via reactions, live
tally visible to everyone). Both persist across restarts via fixed
`custom_id`s registered in `cog_load`.
"""
from __future__ import annotations
import asyncio
from collections import defaultdict

from discord import (
    ButtonStyle, Embed, Interaction, Object, RawReactionActionEvent, app_commands,
)
from discord.app_commands import allowed_contexts, describe
from discord.ext import commands
from discord.ui import Button, View, button

import core.db as db
from core.emoji import custom_hash
from core.text import vote_bar as _vote_bar

_POLL_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
_POLL_VOTE_LOCKS: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def build_poll_embed(
    question: str, options: list[str], votes: dict[int, int], requester_id: int, requester_name: str,
    requester_icon: str | None = None, *, final: bool = False, anonymous: bool = True
) -> Embed:
    counts = [0] * len(options)
    for opt_idx in votes.values():
        if 0 <= opt_idx < len(counts):
            counts[opt_idx] += 1
    total = sum(counts)

    embed = Embed(title=question, color=custom_hash(requester_id))
    for i, (opt, count) in enumerate(zip(options, counts)):
        embed.add_field(name=f"{_POLL_EMOJIS[i]}\u200b  {opt}", value=_vote_bar(count, total), inline=False)
    embed.set_author(name=f"Requested by {requester_name}", icon_url=requester_icon)

    if final:
        status = "Final results"
    elif not anonymous:
        status = "React below to vote!"
    else:
        status = "Cast your vote below!"
    embed.set_footer(text=f"{status}  ·  {total} vote{'s' if total != 1 else ''} cast")
    return embed


async def _resolve_poll_display(bot: commands.Bot, guild, user_id: int) -> tuple[str, str | None]:
    member = None
    if guild:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                member = None
    if member:
        return member.display_name, member.display_avatar.url
    user = bot.get_user(user_id)
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except Exception:
            user = None
    if user:
        return user.display_name, user.display_avatar.url
    return "Unknown User", None


class PollView(View):
    def __init__(self, options: list[str] | None = None):
        super().__init__(timeout=None)
        n = len(options) if options else 10
        for i in range(n, 10):
            btn = next((c for c in self.children if getattr(c, "custom_id", None) == f"poll:vote:{i}"), None)
            if btn:
                self.remove_item(btn)
        if options:
            for i, opt in enumerate(options):
                btn = next((c for c in self.children if getattr(c, "custom_id", None) == f"poll:vote:{i}"), None)
                if btn:
                    btn.label = opt[:80]

    async def _handle_vote(self, ctx: Interaction, idx: int):
        poll = await db.db_get_poll(ctx.message.id)
        if poll is None:
            await ctx.response.send_message("This poll's data couldn't be found.", ephemeral=True)
            return
        if poll["ended"]:
            await ctx.response.send_message("This poll has ended.", ephemeral=True)
            return
        if idx >= len(poll["options"]):
            await ctx.response.defer()
            return
        if poll["votes"].get(ctx.user.id) == idx:
            await ctx.response.defer()
            return

        poll = await db.db_set_poll_vote(ctx.message.id, ctx.user.id, idx)
        name, icon = await _resolve_poll_display(ctx.client, ctx.guild, poll["requester_id"])
        embed = build_poll_embed(poll["question"], poll["options"], poll["votes"], poll["requester_id"], name, icon)
        await ctx.response.edit_message(embed=embed)

    async def _handle_end(self, ctx: Interaction):
        poll = await db.db_get_poll(ctx.message.id)
        if poll is None:
            await ctx.response.send_message("This poll's data couldn't be found.", ephemeral=True)
            return
        if ctx.user.id != poll["requester_id"]:
            await ctx.response.send_message("Only the poll's creator can end it early.", ephemeral=True)
            return
        if poll["ended"]:
            await ctx.response.defer()
            return

        poll = await db.db_end_poll(ctx.message.id)
        name, icon = await _resolve_poll_display(ctx.client, ctx.guild, poll["requester_id"])
        embed = build_poll_embed(poll["question"], poll["options"], poll["votes"], poll["requester_id"], name, icon, final=True)

        locked = View(timeout=None)
        for i, opt in enumerate(poll["options"]):
            locked.add_item(Button(label=opt[:80], emoji=_POLL_EMOJIS[i], style=ButtonStyle.blurple, disabled=True))
        locked.add_item(Button(label="Poll Ended", style=ButtonStyle.red, emoji="🔒", disabled=True))
        await ctx.response.edit_message(embed=embed, view=locked)

    @button(label="Option 1", emoji=_POLL_EMOJIS[0], style=ButtonStyle.blurple, custom_id="poll:vote:0")
    async def _vote_0(self, ctx: Interaction, _: Button): await self._handle_vote(ctx, 0)

    @button(label="Option 2", emoji=_POLL_EMOJIS[1], style=ButtonStyle.blurple, custom_id="poll:vote:1")
    async def _vote_1(self, ctx: Interaction, _: Button): await self._handle_vote(ctx, 1)

    @button(label="Option 3", emoji=_POLL_EMOJIS[2], style=ButtonStyle.blurple, custom_id="poll:vote:2")
    async def _vote_2(self, ctx: Interaction, _: Button): await self._handle_vote(ctx, 2)

    @button(label="Option 4", emoji=_POLL_EMOJIS[3], style=ButtonStyle.blurple, custom_id="poll:vote:3")
    async def _vote_3(self, ctx: Interaction, _: Button): await self._handle_vote(ctx, 3)

    @button(label="Option 5", emoji=_POLL_EMOJIS[4], style=ButtonStyle.blurple, custom_id="poll:vote:4")
    async def _vote_4(self, ctx: Interaction, _: Button): await self._handle_vote(ctx, 4)

    @button(label="Option 6", emoji=_POLL_EMOJIS[5], style=ButtonStyle.blurple, custom_id="poll:vote:5")
    async def _vote_5(self, ctx: Interaction, _: Button): await self._handle_vote(ctx, 5)

    @button(label="Option 7", emoji=_POLL_EMOJIS[6], style=ButtonStyle.blurple, custom_id="poll:vote:6")
    async def _vote_6(self, ctx: Interaction, _: Button): await self._handle_vote(ctx, 6)

    @button(label="Option 8", emoji=_POLL_EMOJIS[7], style=ButtonStyle.blurple, custom_id="poll:vote:7")
    async def _vote_7(self, ctx: Interaction, _: Button): await self._handle_vote(ctx, 7)

    @button(label="Option 9", emoji=_POLL_EMOJIS[8], style=ButtonStyle.blurple, custom_id="poll:vote:8")
    async def _vote_8(self, ctx: Interaction, _: Button): await self._handle_vote(ctx, 8)

    @button(label="Option 10", emoji=_POLL_EMOJIS[9], style=ButtonStyle.blurple, custom_id="poll:vote:9")
    async def _vote_9(self, ctx: Interaction, _: Button): await self._handle_vote(ctx, 9)

    @button(label="End Poll", style=ButtonStyle.red, emoji="🔒", custom_id="poll:end")
    async def _end(self, ctx: Interaction, _: Button): await self._handle_end(ctx)


class PollEndView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @button(label="End Poll", style=ButtonStyle.red, emoji="🔒", custom_id="poll:end:reaction")
    async def _end(self, ctx: Interaction, _: Button):
        poll = await db.db_get_poll(ctx.message.id)
        if poll is None:
            await ctx.response.send_message("This poll's data couldn't be found.", ephemeral=True)
            return
        if ctx.user.id != poll["requester_id"]:
            await ctx.response.send_message("Only the poll's creator can end it early.", ephemeral=True)
            return
        if poll["ended"]:
            await ctx.response.defer()
            return

        poll = await db.db_end_poll(ctx.message.id)
        name, icon = await _resolve_poll_display(ctx.client, ctx.guild, poll["requester_id"])
        embed = build_poll_embed(
            poll["question"], poll["options"], poll["votes"], poll["requester_id"], name, icon,
            final=True, anonymous=False
        )

        locked = View(timeout=None)
        locked.add_item(Button(label="Poll Ended", style=ButtonStyle.red, emoji="🔒", disabled=True))
        await ctx.response.edit_message(embed=embed, view=locked)


class PollsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(PollView())
        self.bot.add_view(PollEndView())

    @app_commands.command(name="poll", description="Create a poll with up to 10 options")
    @describe(
        question="The poll question",
        option1="First option", option2="Second option",
        option3="Third option (optional)", option4="Fourth option (optional)",
        option5="Fifth option (optional)", option6="Sixth option (optional)",
        option7="Seventh option (optional)", option8="Eighth option (optional)",
        option9="Ninth option (optional)", option10="Tenth option (optional)",
        anonymous="Hide who voted for what (default: no)",
    )
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def poll_command(
        self, ctx: Interaction, question: str, option1: str, option2: str,
        option3: str = None, option4: str = None, option5: str = None,
        option6: str = None, option7: str = None, option8: str = None,
        option9: str = None, option10: str = None,
        anonymous: bool = False
    ):
        options = [o for o in (option1, option2, option3, option4, option5, option6, option7, option8, option9, option10) if o]
        if len(options) < 2:
            await ctx.response.send_message("Provide at least two options.", ephemeral=True)
            return
        if any(len(o) > 80 for o in options):
            await ctx.response.send_message("Keep each option under 80 characters.", ephemeral=True)
            return

        embed = build_poll_embed(
            question, options, {}, ctx.user.id, ctx.user.display_name, ctx.user.display_avatar.url,
            anonymous=anonymous
        )

        if anonymous:
            await ctx.response.send_message(embed=embed, view=PollView(options))
            msg = await ctx.original_response()
        else:
            await ctx.response.send_message(embed=embed, view=PollEndView())
            msg = await ctx.original_response()
            for i in range(len(options)):
                try:
                    await msg.add_reaction(_POLL_EMOJIS[i])
                except Exception:
                    break  # e.g. missing Add Reactions permission

        await db.db_create_poll(msg.id, msg.channel.id, ctx.guild_id, ctx.user.id, question, options, anonymous)

    # region reaction listeners (public, non-anonymous polls only)
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return  # ignore the bot adding its own option reactions

        poll = await db.db_get_poll(payload.message_id)
        if poll is None or poll["anonymous"]:
            return  # not a public poll, ignore

        emoji = str(payload.emoji)
        if emoji not in _POLL_EMOJIS:
            return  # unrelated reaction some user added, leave it alone
        idx = _POLL_EMOJIS.index(emoji)
        if idx >= len(poll["options"]):
            return

        channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)

        async with _POLL_VOTE_LOCKS[payload.message_id]:
            poll = await db.db_get_poll(payload.message_id)
            if poll["ended"]:
                try:
                    await message.remove_reaction(payload.emoji, Object(id=payload.user_id))
                except Exception:
                    pass  # can't strip it (e.g. DMs), vote isn't recorded either way
                return

            prev_idx = poll["votes"].get(payload.user_id)
            if prev_idx == idx:
                return

            if prev_idx is not None:
                try:
                    await message.remove_reaction(_POLL_EMOJIS[prev_idx], Object(id=payload.user_id))
                except Exception:
                    pass

            poll = await db.db_set_poll_vote(payload.message_id, payload.user_id, idx)
            guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
            name, icon = await _resolve_poll_display(self.bot, guild, poll["requester_id"])
            embed = build_poll_embed(poll["question"], poll["options"], poll["votes"], poll["requester_id"], name, icon, anonymous=False)
            await message.edit(embed=embed)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return

        poll = await db.db_get_poll(payload.message_id)
        if poll is None or poll["anonymous"] or poll["ended"]:
            return

        emoji = str(payload.emoji)
        if emoji not in _POLL_EMOJIS:
            return
        idx = _POLL_EMOJIS.index(emoji)

        async with _POLL_VOTE_LOCKS[payload.message_id]:
            poll = await db.db_get_poll(payload.message_id)
            if poll is None or poll["anonymous"] or poll["ended"]:
                return

            if poll["votes"].get(payload.user_id) != idx:
                return

            poll = await db.db_clear_poll_vote(payload.message_id, payload.user_id)

            channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
            name, icon = await _resolve_poll_display(self.bot, guild, poll["requester_id"])
            embed = build_poll_embed(poll["question"], poll["options"], poll["votes"], poll["requester_id"], name, icon, anonymous=False)
            await message.edit(embed=embed)
    # endregion


async def setup(bot: commands.Bot):
    await bot.add_cog(PollsCog(bot))

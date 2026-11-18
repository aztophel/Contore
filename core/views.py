"""Generic `discord.ui.View` building blocks with no feature attached to
them. Every one of these is instantiated by several unrelated cogs, so it
lives here instead of being copy-pasted (the monolith had 3 near-identical
pagination views before this consolidation)."""
from __future__ import annotations
import asyncio
import re

from discord import ButtonStyle, Embed, Interaction
from discord.ui import Button, View

# Used by `MultipageMessage` to pull a `title="..."` override out of the
# leading `<args>` tag some multi-page text blobs are stored with.
ARGS_MATCH = r"^<[aA][rR][gG][sS]>title=['\"](.+)['\"]</[aA][rR][gG][sS]>"


class Yes(View):
    """The bot's canonical yes/no confirmation prompt. Optionally supports
    a third "Cancel" button restricted to a different set of users (e.g.
    the person who initiated the action, distinct from who's confirming
    it)."""

    def __init__(self, users, confirm_text="", deny_text="", confirm=None, deny=None,
                 allow_cancel=False, cancel_users=[], cancel_text="", cancel=None, timeout=180):
        super().__init__(timeout=timeout)
        self.users = users
        self.value = None
        self._cd = confirm or {}
        self._dd = deny or {}
        self._ct = confirm_text
        self._dt = deny_text
        self._cau = cancel_users
        self._cat = cancel_text
        self._cad = cancel or {}
        yb = Button(label="Yes", style=ButtonStyle.green, emoji="✅")
        nb = Button(label="No", style=ButtonStyle.danger, emoji="❌")
        cn = Button(label="Cancel", style=ButtonStyle.gray, emoji="❕")
        yb.callback = self._yes
        nb.callback = self._no
        cn.callback = self._cancel
        self.add_item(yb)
        self.add_item(nb)
        if allow_cancel:
            self.add_item(cn)

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id not in self.users:
            await ctx.response.send_message("This isn't for you!", ephemeral=True)
            return False
        return True

    async def _yes(self, ctx: Interaction):
        if ctx.user.id not in self.users:
            await ctx.response.send_message("This isn't for you!", ephemeral=True)
            return
        self.value = True
        self.stop()
        await ctx.response.edit_message(content=self._ct or None, embed=self._cd.get("embed"), view=None)

    async def _no(self, ctx: Interaction):
        if ctx.user.id not in self.users:
            await ctx.response.send_message("This isn't for you!", ephemeral=True)
            return
        self.value = False
        self.stop()
        await ctx.response.edit_message(content=self._dt or None, embed=self._dd.get("embed"), view=None)

    async def _cancel(self, ctx: Interaction):
        if ctx.user.id not in self._cau:
            await ctx.response.send_message("This isn't for you!", ephemeral=True)
            return
        self.value = False
        self.stop()
        await ctx.response.edit_message(content=self._cat or None, embed=self._cad.get("embed"), view=None)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class MultiAcceptView(View):
    """Requires every one of `required_ids` to press Accept before
    resolving (used for e.g. multi-trainer duel proposals); any single
    Decline cancels the whole thing."""

    def __init__(self, required_ids: list[int], *, accept_label: str = "Accept", decline_label: str = "Decline"):
        super().__init__(timeout=90)
        self.required = set(required_ids)
        self.accepted: set[int] = set()
        self.declined = False
        accept = Button(label=accept_label, style=ButtonStyle.green, emoji="✅")
        decline = Button(label=decline_label, style=ButtonStyle.danger, emoji="❌")
        accept.callback, decline.callback = self._accept, self._decline
        self.add_item(accept)
        self.add_item(decline)

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id not in self.required:
            await ctx.response.send_message("This isn't for you!", ephemeral=True)
            return False
        return True

    async def _accept(self, ctx: Interaction):
        self.accepted.add(ctx.user.id)
        if self.accepted >= self.required:
            self.stop()
            await ctx.response.edit_message(content="✅ Everyone's in! Starting the battle...", view=None)
        else:
            remaining = ", ".join(f"<@{u}>" for u in self.required - self.accepted)
            await ctx.response.edit_message(content=f"{ctx.user.mention} is in! Still waiting on: {remaining}", view=self)

    async def _decline(self, ctx: Interaction):
        self.declined = True
        self.stop()
        await ctx.response.edit_message(content=f"❌ {ctx.user.mention} declined. Battle cancelled.", view=None)

    async def on_timeout(self):
        self.declined = True


class SimplePageView(View):
    """A `<` / `>` pager over a pre-rendered list of embeds, with optional
    `<<` / `>>` jump-to-ends buttons."""

    def __init__(self, embeds: list[Embed], user_id: int, index: int = 0, timeout: float = None, big_jumps: bool = False):
        super().__init__(timeout=timeout)
        self.embeds, self.user_id, self.current_page = embeds, user_id, index
        self.left = Button(label="◀", style=ButtonStyle.gray, disabled=index == 0)
        self.idx_btn = Button(label=str(index + 1), style=ButtonStyle.gray, disabled=True)
        self.right = Button(label="▶", style=ButtonStyle.gray, disabled=index >= len(embeds) - 1)
        self.left.callback = self._go(lambda: self.current_page - 1)
        self.right.callback = self._go(lambda: self.current_page + 1)
        if big_jumps:
            self.all_left = Button(label="❮❮", style=ButtonStyle.gray, disabled=index == 0)
            self.all_right = Button(label="❯❯", style=ButtonStyle.gray, disabled=index >= len(embeds) - 1)
            self.all_left.callback = self._go(lambda: 0)
            self.all_right.callback = self._go(lambda: len(embeds) - 1)
        else:
            self.all_left = self.all_right = None
        for b in (self.all_left, self.left, self.idx_btn, self.right, self.all_right):
            if b is not None:
                self.add_item(b)

    async def interaction_check(self, ctx: Interaction) -> bool:
        if ctx.user.id != self.user_id:
            await ctx.response.send_message("This isn't your menu!", ephemeral=True)
            return False
        return True

    def _go(self, target):
        async def cb(ctx: Interaction):
            self.current_page = max(0, min(len(self.embeds) - 1, target()))
            self.left.disabled = self.current_page == 0
            self.right.disabled = self.current_page >= len(self.embeds) - 1
            if self.all_left is not None:
                self.all_left.disabled = self.left.disabled
            if self.all_right is not None:
                self.all_right.disabled = self.right.disabled
            self.idx_btn.label = str(self.current_page + 1)
            await ctx.response.edit_message(embed=self.embeds[self.current_page], view=self)
        return cb

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class MultipageMessage(View):
    """Pager over raw text pages instead of pre-rendered embeds; each page
    is wrapped in a fresh `Embed` on the fly, with an optional
    `<args>title="..."</args>` prefix on a page overriding that page's title."""

    def __init__(self, pages, title="Page {pagenum}", color=0x111111, timeout=None):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.title = title
        self.color = color
        self.current_page = 0
        self.left_button = Button(label="◀", style=ButtonStyle.gray, disabled=True)
        self.right_button = Button(label="▶", style=ButtonStyle.gray, disabled=len(pages) < 2)
        self.left_button.callback = self.left
        self.right_button.callback = self.right
        self.add_item(self.left_button)
        self.add_item(self.right_button)

    def update_buttons(self):
        self.right_button.disabled = self.current_page >= len(self.pages) - 1
        self.left_button.disabled = self.current_page - 1 < 0

    async def _show(self, ctx, page_idx):
        self.current_page = page_idx
        self.update_buttons()
        args = re.search(ARGS_MATCH, self.pages[page_idx])
        embed = Embed(
            title=self.title.replace("{pagenum}", str(page_idx + 1)).replace("{pagetitle}", args.group(1) if args else ""),
            description=self.pages[page_idx].replace(args.group(0) if args else "{pagetitle}", ""),
            color=self.color,
        )
        await ctx.response.edit_message(embed=embed, view=self)

    async def left(self, ctx: Interaction):
        if self.current_page >= 1:
            await self._show(ctx, self.current_page - 1)
        else:
            await ctx.response.defer()

    async def right(self, ctx: Interaction):
        if self.current_page < len(self.pages) - 1:
            await self._show(ctx, self.current_page + 1)
        else:
            await ctx.response.defer()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class SelfTimeoutView(View):
    """A `View` with no fixed Discord-side timeout, but which disables
    itself after `idle_timeout` seconds of *inactivity* (any authorized
    interaction resets the clock). Used for long-running interactive
    surfaces -- e.g. Pokémon battles -- where a fixed timeout would cut
    off an otherwise-active back-and-forth."""

    def __init__(self, idle_timeout: float = 300):
        super().__init__(timeout=None)
        self.idle_timeout = idle_timeout
        self._timer_task: asyncio.Task | None = None
        self._bump_timer()

    def _bump_timer(self):
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._timer_task = asyncio.create_task(self._idle_watch())

    def cancel_timer(self):
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()

    async def _idle_watch(self):
        try:
            await asyncio.sleep(self.idle_timeout)
        except asyncio.CancelledError:
            return
        await self.on_idle_timeout()

    async def on_idle_timeout(self):
        for item in self.children:
            item.disabled = True
        self.stop()

    async def interaction_check(self, ctx: Interaction) -> bool:
        ok = await self._authorized(ctx)
        if ok:
            self._bump_timer()
        return ok

    async def _authorized(self, ctx: Interaction) -> bool:
        return True

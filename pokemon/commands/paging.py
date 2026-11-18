"""A generic prev/next paged-embed view (collection lists, shop
categories, GTS browsing, ... all need this). Generic non-Pokémon UI --
`core`'s territory per its OWNER.md, which doesn't exist yet.
Soft-dependency onto `core.views.SimplePageView`, falling back to a local
copy (ported verbatim from the monolith) -- see `pokemon.commands.confirm`
for the same pattern applied to `Yes`.
"""

from __future__ import annotations

from discord import ButtonStyle, Embed, Interaction
from discord.ui import Button, View

try:
    from core.views import SimplePageView  # type: ignore[import-not-found]
except ImportError:

    class SimplePageView(View):  # noqa: N801 -- matches the monolith's class name
        def __init__(
            self, embeds: list[Embed], user_id: int, index: int = 0,
            timeout: float | None = None, big_jumps: bool = False,
        ):
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

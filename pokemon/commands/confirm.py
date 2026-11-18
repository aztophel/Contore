"""A generic Yes/No/Cancel confirmation view (`release`, `trade`, `breed`,
`give`, ... all need "are you sure?" before an irreversible action).

This is generic, non-Pokémon UI -- squarely `core`'s territory per its
OWNER.md ("Generic bot utilities... views..."), which doesn't exist yet.
Soft-dependency, same pattern as `pokemon.commands._db`: try `core.views`
first, fall back to a local copy (ported verbatim from the monolith's
`Yes`) so Track B stays testable standalone. Whichever module ends up
defining the real `core.views.Yes` should keep this exact
constructor/attribute shape (`.value`, `.users`, `confirm_text=`/
`deny_text=`) so no call site needs to change when the swap happens.
"""

from __future__ import annotations

from discord import ButtonStyle, Interaction
from discord.ui import Button, View

try:
    from core.views import Yes  # type: ignore[import-not-found]
except ImportError:

    class Yes(View):  # noqa: N801 -- matches the monolith's class name
        def __init__(
            self, users, confirm_text="", deny_text="", confirm=None, deny=None,
            allow_cancel=False, cancel_users=(), cancel_text="", cancel=None, timeout=180,
        ):
            super().__init__(timeout=timeout)
            self.users = users
            self.value: bool | None = None
            self._cd = confirm or {}
            self._dd = deny or {}
            self._ct = confirm_text
            self._dt = deny_text
            self._cau = cancel_users
            self._cat = cancel_text
            self._cad = cancel or {}
            yes_btn = Button(label="Yes", style=ButtonStyle.green, emoji="✅")
            no_btn = Button(label="No", style=ButtonStyle.danger, emoji="❌")
            cancel_btn = Button(label="Cancel", style=ButtonStyle.gray, emoji="❕")
            yes_btn.callback = self._yes
            no_btn.callback = self._no
            cancel_btn.callback = self._cancel
            self.add_item(yes_btn)
            self.add_item(no_btn)
            if allow_cancel:
                self.add_item(cancel_btn)

        async def interaction_check(self, ctx: Interaction) -> bool:
            if ctx.user.id not in self.users:
                await ctx.response.send_message("This isn't for you!", ephemeral=True)
                return False
            return True

        async def _yes(self, ctx: Interaction):
            self.value = True
            self.stop()
            await ctx.response.edit_message(content=self._ct or None, embed=self._cd.get("embed"), view=None)

        async def _no(self, ctx: Interaction):
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

"""Auto-imports every module placed in this package so their @register_move
decorators run at startup.

Deliberately NOT a manually maintained list of imports: with several people
adding new item files in parallel, a hand-edited import list here would be
the single most contended file in the whole migration for merge conflicts.
Just drop a new .py file in this directory (or a subpackage with its own
__init__.py) and it's picked up automatically -- no other file needs to
change.
"""
from __future__ import annotations
import importlib
import pkgutil

for _mod_info in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_mod_info.name}")


# Gen 3 (Showdown data/mods/gen3/items.ts): every HP-threshold berry acts at the END OF THE TURN (`onResidual`, order 10 / sub-order 4) instead of the instant `onUpdate` of later generations.
# Applied here, AFTER every item module is imported, so it covers whichever file defines each berry.
def _gen3_residual_berries() -> None:
    from ..items import _item_classes
    names = ("Aguav Berry", "Apicot Berry", "Berry Juice", "Figy Berry", "Ganlon Berry", "Iapapa Berry", "Lansat Berry", "Liechi Berry", "Mago Berry", "Oran Berry", "Petaya Berry",
             "Salac Berry", "Sitrus Berry", "Starf Berry", "Wiki Berry")
    for name in names:
        cls = _item_classes.get(name)
        orig = getattr(cls, "on_update", None) if cls else None
        if orig is None or getattr(cls, "_gen3_residual", False):
            continue

        async def on_update(self, ctx, owner, _orig=orig):
            if ctx.ruleset.number == 3:
                return None
            return await _orig(self, ctx, owner)

        async def on_end_turn(self, ctx, owner, _orig=orig):
            if ctx.ruleset.number == 3:
                await _orig(self, ctx, owner)

        cls.on_update, cls.on_end_turn = on_update, on_end_turn
        cls.residual_order_gen4 = (10, 4)
        cls._gen3_residual = True


_gen3_residual_berries()

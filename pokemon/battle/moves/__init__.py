"""Auto-imports every module placed in this package so their @register_move
decorators run at startup.

Deliberately NOT a manually maintained list of imports: with several people
adding new move files in parallel, a hand-edited import list here would be
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

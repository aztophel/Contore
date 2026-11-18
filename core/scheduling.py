"""Time-alignment helpers for `tasks.loop`-based background jobs.

The monolith re-implemented "sleep until the top of the next hour" as a
`before_loop` callback on three separate loops (birthdays/scheduled roles,
egg hatching, auto-revive) with a copy-pasted body each time. Centralized
here since any future periodic job wants the same alignment.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta


async def sleep_until_next_hour() -> None:
    now = datetime.now()
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    await asyncio.sleep((next_hour - now).total_seconds())


async def sleep_until_next_half_hour() -> None:
    now = datetime.now()
    next_half_hour = now.replace(second=0, microsecond=0) + timedelta(minutes=30 - now.minute % 30)
    await asyncio.sleep((next_half_hour - now).total_seconds())


async def sleep_until_next_quarter_hour() -> None:
    now = datetime.now()
    next_quarter = now.replace(minute=now.minute - (now.minute % 15), second=0, microsecond=0) + timedelta(minutes=15)
    await asyncio.sleep((next_quarter - now).total_seconds())

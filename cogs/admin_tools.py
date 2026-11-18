"""Owner-only operational commands, all restricted to the home server:
database backup/raw-SQL access, installing a pip package on the fly,
running an arbitrary uploaded script, and stopping/restarting the bot.

`/update` is a deliberate redesign, not a port. The monolith's version
uploaded a replacement .py file and relaunched it in a new terminal window
-- which assumed the whole bot was one file and that a desktop GUI was
available to spawn a terminal into. Neither holds anymore: the bot is a
git checkout of many files now, and "spawn a terminal window" doesn't
make sense on a headless server. `/update` now does `git fetch` + a hard
reset to the tracking branch's remote HEAD, then restarts the current
process in place via `os.execv` (works headless, no new PID, no new
window). **This is a proposed design, not settled** -- happy to adjust
(specific branch/tag targeting, a dry-run/diff-only mode, requiring a
clean working tree first, etc.) once it's been looked at.

`SHUTDOWN_HOOKS` is a public registry other cogs can append an
`async def () -> None` cleanup callable to (e.g. Pokémon's sprite-fetch
HTTP session close) -- replaces the monolith reaching into one hardcoded
global (`_SPRITE_HTTP_SESSION`) directly.
"""
from __future__ import annotations
import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Awaitable, Callable

import psutil
from discord import Attachment, File, Interaction, app_commands
from discord.app_commands import default_permissions, describe, guilds
from discord.ext import commands

import core.db as db
from config import DATABASE_URL, HOME_GUILD_ID, OWNER_ID, PERMS
from core.permissions import is_owner

SHUTDOWN_HOOKS: list[Callable[[], Awaitable[None]]] = []


async def _run(*args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors="ignore"), err.decode(errors="ignore")


async def _run_git_update() -> tuple[bool, str]:
    """git fetch + hard reset to the current branch's remote HEAD. Reports
    what changed; doesn't restart anything -- that's the caller's job."""
    rc, _, err = await _run("git", "fetch", "--quiet")
    if rc != 0:
        return False, f"`git fetch` failed:\n```\n{err[:1500]}\n```"

    rc, branch_out, err = await _run("git", "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0:
        return False, f"Couldn't determine the current branch:\n```\n{err[:1500]}\n```"
    branch = branch_out.strip()

    rc, log_out, _ = await _run("git", "log", f"HEAD..origin/{branch}", "--oneline")
    up_to_date = rc == 0 and not log_out.strip()

    rc, _, err = await _run("git", "reset", "--hard", f"origin/{branch}")
    if rc != 0:
        return False, f"`git reset` failed:\n```\n{err[:1500]}\n```"

    if up_to_date:
        return True, f"Already up to date with `origin/{branch}`."
    summary = log_out.strip()[:1500]
    return True, f"Updated `{branch}` → `origin/{branch}`:\n```\n{summary}\n```"


async def _graceful_shutdown(bot: commands.Bot):
    """Shared teardown for `/stop` and `/update`: settle voice-XP tracking,
    close the gateway connection, run every registered shutdown hook, then
    close the DB pool. Extracted since the monolith had this exact
    four-step sequence duplicated between the two commands."""
    from cogs.levelling import clear_voice_tracking
    await clear_voice_tracking(bot)
    await bot.close()
    for hook in SHUTDOWN_HOOKS:
        try:
            await hook()
        except Exception as e:
            print(f"[AdminTools] shutdown hook {hook!r} failed: {e}")
    await db.db_close()


class AdminToolsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="data", description="Retrieve Contore's database")
    @guilds(HOME_GUILD_ID)
    @default_permissions(PERMS)
    @is_owner()
    async def get_database(self, ctx: Interaction):
        if ctx.user.id != OWNER_ID:
            await ctx.response.send_message("nuh", ephemeral=True)
            return
        await ctx.response.defer(ephemeral=True)

        dump_path = f"/tmp/contore_backup_{int(time.time())}.sql"
        try:
            rc, _, err = await _run("pg_dump", DATABASE_URL, "-f", dump_path, "--no-owner", "--no-privileges")
            if rc != 0 or not os.path.isfile(dump_path) or os.path.getsize(dump_path) == 0:
                await ctx.followup.send(f"❌ Backup failed: `{err[:1500]}`", ephemeral=True)
                return
            await ctx.followup.send(
                "Database backup:",
                file=File(dump_path, filename=f"contore_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"),
                ephemeral=True)
        except FileNotFoundError:
            await ctx.followup.send("❌ `pg_dump` isn't available in this environment.", ephemeral=True)
        finally:
            try:
                os.remove(dump_path)
            except OSError:
                pass

    @app_commands.command(name="sql", description="Execute a raw SQL query against Contore's database")
    @guilds(HOME_GUILD_ID)
    @default_permissions(PERMS)
    @is_owner()
    async def sql_execute(self, ctx: Interaction, query: str):
        if ctx.user.id != OWNER_ID:
            await ctx.response.send_message("nuh", ephemeral=True)
            return
        is_select = query.strip().lower().startswith(("select", "with", "show", "explain"))
        try:
            if is_select:
                rows = await db.get_pool().fetch(query)
                if not rows:
                    await ctx.response.send_message("✅ Query returned no rows.", ephemeral=True)
                    return
                cols = list(rows[0].keys())
                header = " | ".join(cols)
                body = "\n".join(" | ".join(str(row[c])[:25] if row[c] is not None else "NULL" for c in cols)
                                  for row in rows[:15])
                result = f"{header}\n{'-' * min(len(header), 60)}\n{body}"
                if len(rows) > 15:
                    result += f"\n... ({len(rows):,} total rows)"
                await ctx.response.send_message(f"```\n{result[:1900]}\n```", ephemeral=True)
            else:
                status = await db.get_pool().execute(query)
                await ctx.response.send_message(f"✅ Executed. `{status}`", ephemeral=True)
        except Exception as e:
            await ctx.response.send_message(f"❌ SQL Error: `{e}`", ephemeral=True)

    @app_commands.command(name="update", description="Pull the latest code from git and restart")
    @guilds(HOME_GUILD_ID)
    @default_permissions(PERMS)
    @is_owner()
    async def update(self, ctx: Interaction):
        if ctx.user.id != OWNER_ID:
            await ctx.response.send_message("nuh", ephemeral=True)
            return
        await ctx.response.defer(ephemeral=True)

        ok, message = await _run_git_update()
        if not ok:
            await ctx.followup.send(f"❌ {message}")
            return

        await ctx.followup.send(f"✅ {message}\nRestarting...")
        await _graceful_shutdown(self.bot)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    @app_commands.command(name="open", description="Download and run an arbitrary Python file (dev tool, not part of the bot itself)")
    @guilds(HOME_GUILD_ID)
    @default_permissions(PERMS)
    @is_owner()
    async def open_python(self, ctx: Interaction, program: Attachment):
        if ctx.user.id != OWNER_ID or not program.filename.endswith(".py"):
            await ctx.response.send_message("nuh", ephemeral=True)
            return
        await ctx.response.send_message(f"Starting {program.filename}...", ephemeral=True)
        await program.save(program.filename)
        if sys.platform.startswith("win"):
            subprocess.Popen(
                f"start python -c \"from time import sleep; from subprocess import run; "
                f"sleep(5); run('python \\\"{program.filename}\\\"')\"", shell=True)
        else:
            os.system(f"lxterminal --title=\"{program.filename}\" -e \"source venv/bin/activate; python './{program.filename}'\"")

    @app_commands.command(name="install", description="Install a Python package")
    @guilds(HOME_GUILD_ID)
    @default_permissions(PERMS)
    @is_owner()
    async def install(self, ctx: Interaction, package: str):
        if ctx.user.id != OWNER_ID:
            await ctx.response.send_message("nuh", ephemeral=True)
            return
        await ctx.response.defer(ephemeral=True)
        try:
            rc, out, err = await _run(sys.executable, "-m", "pip", "install", package)
            if rc == 0:
                await ctx.followup.send(f"✅ Installed `{package}`.")
            else:
                await ctx.followup.send(f"❌ Failed:\n```\n{err[:1500]}\n```")
        except Exception as e:
            await ctx.followup.send(f"Failed: {e}")

    @app_commands.command(name="stop", description="Kill Contore")
    @guilds(HOME_GUILD_ID)
    @default_permissions(PERMS)
    @is_owner()
    async def stop(self, ctx: Interaction):
        if ctx.user.id != OWNER_ID:
            await ctx.response.send_message("nuh", ephemeral=True)
            return
        await ctx.response.send_message("Exiting...", ephemeral=True)
        await _graceful_shutdown(self.bot)
        sys.exit()

    @app_commands.command(name="terminate", description="Stop all other bots")
    @guilds(HOME_GUILD_ID)
    @default_permissions(PERMS)
    @is_owner()
    async def terminate(self, ctx: Interaction):
        if ctx.user.id != OWNER_ID:
            await ctx.response.send_message("nuh", ephemeral=True)
            return
        await ctx.response.send_message("Starting termination sweep...")
        try:
            current_pid = os.getpid()
            terminal_names = (
                ["cmd.exe", "powershell.exe", "pwsh.exe"] if os.name == "nt"
                else ["python", "bash", "sh", "zsh", "fish", "xterm", "gnome-terminal-server", "konsole"])
            for process in psutil.process_iter(attrs=["pid", "name"]):
                try:
                    pid, name = process.info["pid"], process.info["name"]
                    if pid != current_pid and name in terminal_names:
                        process.terminate()
                        process.wait(timeout=5)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                    continue
        except BaseException as e:
            await ctx.channel.send(str(e))


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminToolsCog(bot))

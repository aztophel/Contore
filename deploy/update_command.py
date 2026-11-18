"""Git-based deployment, replacing the old /update (upload a raw .py file
over Discord and respawn) and /open commands.

With the codebase split across dozens of files, "attach the new file to a
slash command" no longer works -- there isn't one file anymore. The
canonical source of truth is a git repository; deploying means pulling it.
This also solves the Pokédex-rebuild problem: the generated data/*.json
caches (Pokédex, move data, evolution chains, ability/item descriptions)
are committed to the repo, so a fresh checkout never needs to re-hit
PokeAPI at all -- `/pokedex rebuild` becomes an OFFLINE maintenance script
(pokemon/dex/pokedex_builder.py) a developer runs locally and commits the
result, not a bot command that touches the network at runtime.
"""
from __future__ import annotations
import asyncio
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


async def _run(*args: str, cwd: Path = REPO_ROOT) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors="ignore"), err.decode(errors="ignore")


async def check_for_updates(branch: str = "main") -> tuple[bool, str]:
    """Fetches without touching the working tree, so this is safe to call
    on a schedule/health-check without risking a half-applied deploy."""
    rc, _, err = await _run("git", "fetch", "origin", branch)
    if rc != 0:
        return False, f"git fetch failed:\n{err}"
    rc, out, _ = await _run("git", "rev-list", f"HEAD..origin/{branch}", "--count")
    behind = int(out.strip() or "0")
    return behind > 0, f"{behind} commit(s) behind origin/{branch}"


async def deploy(branch: str = "main") -> tuple[bool, str]:
    """Fetch + hard reset to the remote branch. Deliberately does NOT touch
    untracked files (so a local-only `.env` with secrets survives), but
    DOES discard any local modifications to tracked files -- this process
    should never carry hand-edited source; all changes flow through git."""
    rc, _, err = await _run("git", "fetch", "origin", branch)
    if rc != 0:
        return False, f"❌ `git fetch` failed:\n```\n{err.strip()}\n```"

    rc, out, err = await _run("git", "reset", "--hard", f"origin/{branch}")
    if rc != 0:
        return False, f"❌ `git reset` failed:\n```\n{err.strip()}\n```"

    rc, head, _ = await _run("git", "rev-parse", "--short", "HEAD")
    return True, f"✅ Deployed `{head.strip()}` on `{branch}`.\n```\n{out.strip()[:1500]}\n```"


async def rollback(commit: str) -> tuple[bool, str]:
    rc, _, err = await _run("git", "reset", "--hard", commit)
    if rc != 0:
        return False, f"❌ Rollback failed:\n```\n{err.strip()}\n```"
    return True, f"✅ Rolled back to `{commit}`."


def respawn() -> None:
    """Same spirit as the old /update's respawn logic, just no longer needs
    to `.save()` an uploaded file first -- the working tree already IS the
    new code, courtesy of `deploy()` above."""
    subprocess.Popen([sys.executable, str(REPO_ROOT / "bot.py")])
    sys.exit(0)

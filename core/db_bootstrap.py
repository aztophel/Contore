"""Local-Postgres auto-provisioning, run once before `bot.py` connects to
Discord at all. If `DATABASE_URL` points at localhost and nothing's
listening there yet, this installs Postgres via whichever of apt/pacman/
dnf is available, creates a cluster, and creates the configured role/
database -- all via passwordless `sudo`, meant for a fresh dev/personal-
server setup. If the host isn't local, or sudo isn't configured
non-interactively, this just checks reachability and otherwise gets out
of the way.

Worth reconsidering now that deployment is moving to a proper git-managed
server rather than the ad-hoc single-machine setup this was written for --
a bot process silently running `sudo apt install` and creating database
roles on first boot is an unusual pattern for anything but a personal
box. Left in as-is (preserving behavior) rather than removed unasked.
"""
from __future__ import annotations
import os
import shutil
import socket
import subprocess
import time
from urllib.parse import urlparse


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _sudo_noninteractive_ok() -> bool:
    return _run(["sudo", "-n", "true"]).returncode == 0


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _postgres_service_active() -> bool:
    return _run(["systemctl", "is-active", "--quiet", "postgresql"]).returncode == 0


def _detect_pkg_mgr() -> str | None:
    if shutil.which("apt-get"):
        return "apt"
    if shutil.which("pacman"):
        return "pacman"
    if shutil.which("dnf"):
        return "dnf"
    return None


def _apt_ensure_cluster() -> bool:
    if not shutil.which("pg_lsclusters"):
        print("[PG Bootstrap] Installing postgresql via apt...")
        _run(["sudo", "apt-get", "update"])
        r = _run(["sudo", "apt-get", "install", "-y", "postgresql", "postgresql-contrib"])
        if r.returncode != 0:
            print(f"[PG Bootstrap] apt install failed:\n{r.stderr}")
            return False

    listing = _run(["pg_lsclusters", "--no-header"]).stdout.strip()
    if not listing:
        versions = sorted(os.listdir("/usr/lib/postgresql")) if os.path.isdir("/usr/lib/postgresql") else []
        if not versions:
            print("[PG Bootstrap] No postgresql version found under /usr/lib/postgresql.")
            return False
        version = versions[-1]
        print(f"[PG Bootstrap] No cluster found. Creating {version}/main...")
        r = _run(["sudo", "pg_createcluster", version, "main", "--start"])
        if r.returncode != 0:
            print(f"[PG Bootstrap] pg_createcluster failed:\n{r.stderr}")
            return False
    elif "online" not in listing:
        print("[PG Bootstrap] Cluster exists but isn't running. Starting it...")
        _run(["sudo", "systemctl", "start", "postgresql"])

    return True


def _pacman_ensure_cluster() -> bool:
    """Arch: raw initdb + hardcoded data dir (as before)."""
    if not shutil.which("initdb"):
        print("[PG Bootstrap] Installing postgresql via pacman...")
        r = _run(["sudo", "pacman", "-S", "--noconfirm", "postgresql"])
        if r.returncode != 0:
            print(f"[PG Bootstrap] Install failed:\n{r.stderr}")
            return False

    data_dir = "/var/lib/postgres/data"
    if not os.path.exists(os.path.join(data_dir, "PG_VERSION")):
        print(f"[PG Bootstrap] Initializing cluster at {data_dir}...")
        r = _run([
            "sudo", "-u", "postgres", "initdb",
            "--locale=C.UTF-8", "--encoding=UTF8",
            "--auth-local=md5", "--auth-host=md5",
            "-D", data_dir,
        ])
        if r.returncode != 0:
            print(f"[PG Bootstrap] initdb failed:\n{r.stderr}")
            return False

    if not _postgres_service_active():
        print("[PG Bootstrap] Starting postgresql.service...")
        if _run(["sudo", "systemctl", "enable", "--now", "postgresql"]).returncode != 0:
            print("[PG Bootstrap] Failed to start. Check journalctl -xeu postgresql.service")
            return False
    return True


def _dnf_ensure_cluster() -> bool:
    """Fedora/RHEL: postgresql-setup wraps initdb, like Debian's wrapper style."""
    if not shutil.which("psql"):
        print("[PG Bootstrap] Installing postgresql-server via dnf...")
        r = _run(["sudo", "dnf", "install", "-y", "postgresql-server"])
        if r.returncode != 0:
            print(f"[PG Bootstrap] Install failed:\n{r.stderr}")
            return False
        r = _run(["sudo", "postgresql-setup", "--initdb"])
        if r.returncode != 0:
            print(f"[PG Bootstrap] postgresql-setup --initdb failed:\n{r.stderr}")
            return False

    if not _postgres_service_active():
        if _run(["sudo", "systemctl", "enable", "--now", "postgresql"]).returncode != 0:
            print("[PG Bootstrap] Failed to start postgresql.service")
            return False
    return True


def ensure_postgres_ready(database_url: str) -> bool:
    parsed = urlparse(database_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    user = parsed.username
    password = parsed.password
    dbname = (parsed.path or "/").lstrip("/")

    if host not in ("localhost", "127.0.0.1", "::1"):
        print(f"[PG Bootstrap] {host} isn't local. Skipping auto-setup; "
              f"set that server up yourself.")
        return _port_open(host, port)

    if not _sudo_noninteractive_ok():
        print("[PG Bootstrap] Passwordless sudo isn't configured for the "
              "required commands. See the sudoers snippet in the setup notes, "
              "or run `sudo -v` once in this shell before launching the bot.")
        return False

    if _port_open(host, port) and not _postgres_service_active():
        print(f"[PG Bootstrap] {host}:{port} is already in use by something "
              f"other than postgresql.service (check `sudo ss -tlnp | grep "
              f"{port}`). Refusing to auto-configure around it.")
        return False

    pkg_mgr = _detect_pkg_mgr()
    handlers = {"apt": _apt_ensure_cluster, "pacman": _pacman_ensure_cluster, "dnf": _dnf_ensure_cluster}
    handler = handlers.get(pkg_mgr)
    if handler is None:
        print("[PG Bootstrap] No supported package manager found (apt/pacman/dnf).")
        return False
    if not handler():
        return False

    for _ in range(20):
        if _run(["sudo", "-u", "postgres", "pg_isready"]).returncode == 0:
            break
        time.sleep(0.5)
    else:
        print("[PG Bootstrap] Server didn't come up in time.")
        return False

    role_exists = _run([
        "sudo", "-u", "postgres", "psql", "-tAc",
        f"SELECT 1 FROM pg_roles WHERE rolname='{user}'"
    ]).stdout.strip() == "1"

    if not role_exists:
        print(f"[PG Bootstrap] Creating role '{user}'...")
        r = _run(["sudo", "-u", "postgres", "psql", "-c",
                   f"CREATE ROLE {user} WITH LOGIN PASSWORD '{password}';"])
        if r.returncode != 0:
            print(f"[PG Bootstrap] Role creation failed:\n{r.stderr}")
            return False
    else:
        _run(["sudo", "-u", "postgres", "psql", "-c",
              f"ALTER ROLE {user} WITH PASSWORD '{password}';"])

    db_exists = _run([
        "sudo", "-u", "postgres", "psql", "-tAc",
        f"SELECT 1 FROM pg_database WHERE datname='{dbname}'"
    ]).stdout.strip() == "1"

    if not db_exists:
        print(f"[PG Bootstrap] Creating database '{dbname}'...")
        r = _run(["sudo", "-u", "postgres", "createdb", "-O", user, dbname])
        if r.returncode != 0:
            print(f"[PG Bootstrap] Database creation failed:\n{r.stderr}")
            return False

    return True

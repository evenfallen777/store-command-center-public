"""Dev test store controls — start/stop the already-configured ``store-dev``
instance (REPO_DEV, port 8788) and pull ``origin/dev`` into it for smoke tests.

The dev instance is the SAME checkout the swarm/Engineers build in
(``REPO_DEV``); its own ``.env`` sets STORE_PORT=8788 and a separate
data dir/DB, so running it never touches the live store's data.

SAFETY: the dev instance will not run the dev-swarm autonomously — its fresh DB
defaults engineers_auto / oracle_auto / money_auto to off. The owner must NOT
enable engineers_auto on it: it shares REPO_DEV with the live app's swarm, and
two swarms writing the same worktree would corrupt each other's work.

Process-control rules (hard invariants):
- We only ever signal the PID recorded in our own pidfile (and its process
  group), after checking it is not our own process/group. The live app on
  8787 can therefore never be killed from here.
- If something holds port 8788 but there is no pidfile, we REPORT it and do
  nothing — never guess-and-kill.
"""
import os
import signal
import socket
import subprocess
import time

from fastapi import Request

from deps import *   # config (REPO_DEV, GIT_BIN), httpx, HTTPException, logger
from ._base import router, _gitc

DEV_PORT = 8788
_RUN_DIR = os.path.join(REPO_DEV, "logs")          # gitignored in store-dev
_PIDFILE = os.path.join(_RUN_DIR, "dev-store.pid")
_LOGFILE = os.path.join(_RUN_DIR, "dev-store.log")

_dev_fetch = {"t": 0.0}   # throttle origin/dev fetches (status is polled by the UI)


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers (pidfile / port / health)
# ─────────────────────────────────────────────────────────────────────────────
def _read_pid():
    """PID from the pidfile, or None if missing/garbage."""
    try:
        with open(_PIDFILE) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _pid_alive(pid) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists, just not ours
    except Exception:
        return False


def _port_listening(port: int = DEV_PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except Exception:
        return False


def _healthy() -> bool:
    """Best-effort: does the dev store answer HTTP (<500) within ~2s?"""
    for path in ("/api/health/pulse", "/"):
        try:
            r = httpx.get(f"http://127.0.0.1:{DEV_PORT}{path}", timeout=2.0,
                          follow_redirects=True)
            if r.status_code < 500:
                return True
        except Exception:
            continue
    return False


def _running_state():
    """(running, pid) — running when the pidfile PID is alive OR 8788 listens."""
    pid = _read_pid()
    alive = _pid_alive(pid)
    return (alive or _port_listening(), pid if alive else pid)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/github/dev-store/status")
def dev_store_status():
    """State of the dev test store: process, port 8788 health, and how the
    store-dev checkout relates to origin/dev (fetched at most once a minute)."""
    running, pid = _running_state()
    healthy = _healthy() if running else False

    now = time.time()
    if now - _dev_fetch["t"] > 60:
        _dev_fetch["t"] = now
        _gitc(REPO_DEV, "fetch", "origin", "dev", timeout=30)   # best-effort

    _, head = _gitc(REPO_DEV, "rev-parse", "--short", "HEAD")
    _, branch = _gitc(REPO_DEV, "rev-parse", "--abbrev-ref", "HEAD")
    rc, porc = _gitc(REPO_DEV, "status", "--porcelain")
    dirty = len([l for l in porc.splitlines() if l.strip()]) if rc == 0 else 0
    ahead = behind = 0
    rc, counts = _gitc(REPO_DEV, "rev-list", "--left-right", "--count",
                       "HEAD...origin/dev")
    if rc == 0 and "\t" in counts:
        a, b = counts.split("\t")[:2]
        ahead, behind = int(a.strip() or 0), int(b.strip() or 0)

    return {"running": running, "pid": pid if _pid_alive(pid) else None,
            "port": DEV_PORT, "healthy": healthy,
            "head": head.strip() or None, "branch": branch.strip() or None,
            "dirty": dirty, "behind": behind, "ahead": ahead}


def _start_dev(host: str) -> dict:
    """Shared start body (endpoint + pull-restart). Never blocks on readiness."""
    url = f"http://{host}:{DEV_PORT}"
    running, pid = _running_state()
    if running:
        return {"ok": True, "already": True, "pid": pid, "url": url}
    os.makedirs(_RUN_DIR, exist_ok=True)
    logf = open(_LOGFILE, "a")
    try:
        proc = subprocess.Popen(
            ["bash", os.path.join(REPO_DEV, "run.sh")],
            cwd=REPO_DEV, stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True)   # own process group — stop signals only it
    finally:
        logf.close()   # the child holds its own fd
    with open(_PIDFILE, "w") as f:
        f.write(str(proc.pid))
    logger.info(f"dev-store started pid={proc.pid} on :{DEV_PORT}")
    return {"ok": True, "pid": proc.pid, "url": url,
            "note": "starting — give it ~10s"}


@router.post("/api/github/dev-store/start")
def dev_store_start(request: Request):
    """Start the dev test store (store-dev's own run.sh → uvicorn on :8788).

    Idempotent — if it is already running you get ``already: true``. The dev
    instance won't run the dev-swarm autonomously (engineers_auto/oracle_auto/
    money_auto default off in its own fresh DB); do NOT enable engineers_auto
    on it — it shares REPO_DEV with the live app's swarm."""
    return _start_dev(request.url.hostname or "localhost")


def _stop_dev() -> dict:
    """Shared stop body. Acts ONLY on the pidfile PID + its process group —
    never on whatever happens to hold a port — so the live app (8787) is
    untouchable from here."""
    pid = _read_pid()
    if not _pid_alive(pid):
        # stale/missing pidfile — clean it up, but never guess at 8788's owner
        try:
            os.remove(_PIDFILE)
        except OSError:
            pass
        if _port_listening():
            return {"ok": False,
                    "note": "a process holds 8788 but no pidfile — stop it manually"}
        return {"ok": True, "already_stopped": True}

    # Paranoia: refuse to signal ourselves / our own process group (the live app).
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        pgid = None
    if pid == os.getpid() or (pgid is not None and pgid == os.getpgid(0)):
        return {"ok": False, "note": "pidfile points at the live app — refusing"}

    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.time() + 5
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.2)
    if _pid_alive(pid):
        try:
            os.killpg(pgid, signal.SIGKILL) if pgid is not None else os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        os.remove(_PIDFILE)
    except OSError:
        pass
    logger.info(f"dev-store stopped pid={pid}")
    return {"ok": True, "stopped": True}


@router.post("/api/github/dev-store/stop")
def dev_store_stop():
    """Stop the dev test store. Signals only the pidfile PID and its process
    group (SIGTERM, then SIGKILL after ~5s) — NEVER the live app on 8787. If
    8788 is held with no pidfile, reports it instead of guessing."""
    return _stop_dev()


@router.post("/api/github/dev-store/pull")
def dev_store_pull(request: Request):
    """Sync store-dev to origin/dev (fetch + ff-only merge — never resets, so
    local swarm work is never clobbered; dirty/diverged → 409). If the dev
    store is running, restarts it so it serves the pulled code."""
    rc, porc = _gitc(REPO_DEV, "status", "--porcelain")
    if rc == 0 and porc.strip():
        n = len([l for l in porc.splitlines() if l.strip()])
        raise HTTPException(409, f"store-dev has {n} uncommitted change(s) — "
                                 "won't pull over local swarm work. Commit or stash first.")
    rc, out = _gitc(REPO_DEV, "fetch", "origin", "dev", timeout=60)
    if rc != 0:
        raise HTTPException(409, f"fetch origin dev failed: {out[:300]}")
    rc, out = _gitc(REPO_DEV, "merge", "--ff-only", "origin/dev")
    if rc != 0:
        raise HTTPException(409, "store-dev could not fast-forward onto origin/dev "
                                 f"(diverged?): {out[:300]}")
    _dev_fetch["t"] = 0.0   # next status reflects the new HEAD immediately
    _, head = _gitc(REPO_DEV, "rev-parse", "--short", "HEAD")

    restarted = False
    if _pid_alive(_read_pid()):
        _stop_dev()
        _start_dev(request.url.hostname or "localhost")
        restarted = True
    return {"ok": True, "updated_to": head.strip() or None, "restarted": restarted}

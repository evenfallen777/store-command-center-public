"""Shared base for the github package: the single router, cross-domain git/gh
helpers, the idempotent schema migrations, and the import-time side effects.

Imported first (via ``__init__``), so ``_ensure_schema()`` and the
``swarm.reconcile_on_start()`` reconcile run EXACTLY ONCE, before any submodule's
routes are registered.
"""
import json
import subprocess

from fastapi import APIRouter, HTTPException

from deps import *   # get_conn, get_setting, config (GH_BIN, GIT_BIN, REPO_*), httpx, logger
import swarm          # the Phase-2 engine (safe: swarm does not import github)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Idempotent schema extensions (kept HERE, not in db.py, to stay decoupled)
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_schema():
    conn = get_conn()
    for mig in [
        "ALTER TABLE swarm_jobs ADD COLUMN scope TEXT DEFAULT 'project'",   # project|folder|file
        "ALTER TABLE swarm_jobs ADD COLUMN paths TEXT",                     # json list of file/folder paths
        "ALTER TABLE swarm_jobs ADD COLUMN agent_count INTEGER",            # per-job dynamic N (NULL=global)
        "ALTER TABLE swarm_jobs ADD COLUMN decision TEXT",                  # approved|rejected (user's final call)
        "ALTER TABLE swarm_jobs ADD COLUMN decision_comment TEXT",
        "ALTER TABLE swarm_jobs ADD COLUMN parent_id INTEGER",             # architect-spawned subtask → parent job
        "ALTER TABLE swarm_jobs ADD COLUMN enhanced_spec TEXT",            # architect's clarified spec
        "ALTER TABLE swarm_jobs ADD COLUMN project_id INTEGER",            # dev_projects.id (NULL = legacy/primary store)
        "ALTER TABLE swarm_jobs ADD COLUMN deployed_at TEXT",              # set when Apply-main→live pulls it live
        "ALTER TABLE dev_projects ADD COLUMN review_mode TEXT DEFAULT 'human'",  # human|swarm|either at the review gate
        "ALTER TABLE dev_projects ADD COLUMN auto_go_live INTEGER DEFAULT 0",    # auto-apply main→live after promote (store only)
    ]:
        try:
            conn.execute(mig); conn.commit()
        except Exception:
            pass
    # dev_projects registry (created here too so this package works even if the
    # router is imported before init_db runs — same idempotent DDL as db_schema).
    try:
        from db_schema import create_dev_projects_tables
        create_dev_projects_tables(conn)
    except Exception:
        pass
    conn.execute("""CREATE TABLE IF NOT EXISTS swarm_directives (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id      INTEGER NOT NULL,
        text        TEXT NOT NULL,      -- owner directive, injected at the next stage boundary
        consumed    INTEGER DEFAULT 0,
        created_at  TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS swarm_system_tasks (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id     INTEGER,
        request    TEXT NOT NULL,      -- what the swarm needs installed/configured
        command    TEXT,               -- proposed command (never auto-run without gating)
        status     TEXT DEFAULT 'requested',  -- requested|approved|installing|done|verified|failed
        report     TEXT,               -- what the system agent did + verification result
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()
    conn.close()

_ensure_schema()
try:
    swarm.reconcile_on_start()   # fail/pause any job left mid-run by a restart
except Exception:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Shared git helper — used by both repos (setup-own) and jobs (promote)
# ─────────────────────────────────────────────────────────────────────────────
def _gitc(path, *args, timeout=120):
    """git command in a worktree → (returncode, output). (Distinct from the workflow
    endpoint's string-returning _git — do not merge; the collision broke /workflow.)"""
    import subprocess
    try:
        r = subprocess.run([GIT_BIN, "-C", path, *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)

"""Domain D — the per-project registry for the dev swarm + The Engineers.

One `dev_projects` row per software project the swarm can work: the store
itself (kind='store', the always-present primary), registered external repos,
and brand-new projects the Engineers start (kind='engineer'). Every project
enforces the same policy: the swarm auto-builds on the WORK branch with no
human step, but a human approval (the existing /approve → /promote gate) is
required before code lands on the LIVE branch. The per-project `work_branch`
toggle only selects WHERE the swarm edits — never whether the gate applies.

Also hosts the Engineers settings surface (GET|POST /api/github/engineers-settings)
that the God Console block reads/writes — same settings-row pattern as
/api/oracle/settings. Routes register on the shared router.
"""
import json
import re
from pathlib import Path
from typing import Optional

from deps import *   # get_conn, get_setting, config (GH_BIN, GIT_BIN, REPO_*), HTTPException, BaseModel
from ._base import router, _gitc
from .repos import _run, github_create_repo, CreateRepoIn


# The board-column groups the workboard shows — job counts per project use these.
_COUNT_GROUPS = {
    "proposed": ("proposed",),
    "working": ("planning", "coding", "reviewing", "testing", "decomposed"),
    "needs_you": ("awaiting_input", "awaiting_review", "awaiting_system"),
    "approved": ("approved",),
    "done": ("done",),
    "paused": ("paused", "failed"),
}

# ── Engineers settings (God Console block + Workboard read these) ────────────
ENGINEERS_SETTINGS_DEFAULTS = {
    "engineers_auto": "off",            # master switch for the autonomous proposer loop
    "engineers_interval_hours": "12",   # cadence between Engineer proposals per project
    "engineers_auto_answer": "off",     # full-auto: LLM auto-answers the swarm's blocking questions
}


def _project_dict(conn, row) -> dict:
    d = dict(row)
    primary = bool(d.get("is_primary")) or d.get("kind") == "store"
    counts = {}
    for key, statuses in _COUNT_GROUPS.items():
        ph = ",".join("?" * len(statuses))
        if primary:
            # legacy jobs with project_id NULL belong to the primary store project
            q = (f"SELECT COUNT(*) c FROM swarm_jobs WHERE status IN ({ph}) "
                 "AND (project_id=? OR project_id IS NULL)")
        else:
            q = f"SELECT COUNT(*) c FROM swarm_jobs WHERE status IN ({ph}) AND project_id=?"
        counts[key] = conn.execute(q, (*statuses, d["id"])).fetchone()["c"]
    d["job_counts"] = counts
    return d


@router.get("/api/github/projects")
def list_projects():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM dev_projects ORDER BY is_primary DESC, id").fetchall()
    out = [_project_dict(conn, r) for r in rows]
    conn.close()
    return {"projects": out}


class ProjectIn(BaseModel):
    name: str
    repo: Optional[str] = ""            # owner/name of an EXISTING repo to register
    kind: Optional[str] = "external"    # external | engineer  (store is seeded, never created here)
    create_repo: Optional[bool] = False  # make a brand-new GitHub repo + local clone
    private: Optional[bool] = True
    work_branch: Optional[str] = "dev"
    engineers_enabled: Optional[bool] = False


@router.post("/api/github/projects")
def create_project(body: ProjectIn):
    """Register an existing external repo OR create a brand-new project.

    With create_repo: reuses the /api/github/repo/create logic to make the
    GitHub repo, clones it under ~/projects/engineer/<name>, and records the
    default branch as the live branch. Either way the row starts with the
    merge gate ON — human approval is always required before live."""
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Project name required.")
    kind = body.kind if body.kind in ("external", "engineer") else "external"
    wb = body.work_branch if body.work_branch in ("dev", "main") else "dev"
    repo_full = (body.repo or "").strip() or None
    local_path = None
    live_branch = "main"

    if body.create_repo:
        safe = re.sub(r"[^A-Za-z0-9._-]", "-", name) or "engineer-project"
        github_create_repo(CreateRepoIn(name=safe, private=bool(body.private),
                                        add_readme=True, gitignore="Python"))
        rc, out, _ = _run([GH_BIN, "api", "user", "--jq", ".login"])
        login = out.strip() if rc == 0 else ""
        repo_full = f"{login}/{safe}" if login else safe
        dest = Path.home() / "projects" / "engineer" / safe
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            rc, out, err = _run([GH_BIN, "repo", "clone", repo_full, str(dest)], timeout=300)
            if rc != 0:
                raise HTTPException(502, f"Repo created but clone failed: {(err or out).strip()[:250]}")
        local_path = str(dest)
        rc, head = _gitc(local_path, "rev-parse", "--abbrev-ref", "HEAD")
        if rc == 0 and head.strip():
            live_branch = head.strip()
    elif repo_full and "/" in repo_full:
        # registering an existing repo: clone locally if we don't have it yet
        dest = Path.home() / "projects" / "engineer" / repo_full.split("/")[-1]
        if dest.exists():
            local_path = str(dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            rc, out, err = _run([GH_BIN, "repo", "clone", repo_full, str(dest)], timeout=300)
            if rc != 0:
                raise HTTPException(502, f"Clone failed: {(err or out).strip()[:250]}")
            local_path = str(dest)
        rc, head = _gitc(local_path, "rev-parse", "--abbrev-ref", "HEAD")
        if rc == 0 and head.strip():
            live_branch = head.strip()

    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO dev_projects (kind,name,repo,local_path,live_branch,work_branch,"
        "engineers_enabled,merge_gate,autonomy) VALUES (?,?,?,?,?,?,?,1,'auto')",
        (kind, name, repo_full, local_path, live_branch, wb, int(bool(body.engineers_enabled))))
    conn.commit()
    row = conn.execute("SELECT * FROM dev_projects WHERE id=?", (cur.lastrowid,)).fetchone()
    d = _project_dict(conn, row)
    conn.close()
    return d


@router.patch("/api/github/projects/{pid}")
def update_project(pid: int, body: dict):
    """Update the whitelisted per-project toggles. `work_branch` only changes
    WHERE the swarm edits (dev worktree vs live worktree) — the merge-to-live
    approval gate applies regardless."""
    allowed = ("work_branch", "engineers_enabled", "merge_gate", "autonomy", "is_primary",
               "name", "review_mode", "auto_go_live")
    fields = {k: v for k, v in (body or {}).items() if k in allowed}
    if not fields:
        raise HTTPException(400, "Nothing to update.")
    if "work_branch" in fields and fields["work_branch"] not in ("dev", "main"):
        raise HTTPException(400, "work_branch must be 'dev' or 'main'.")
    if "review_mode" in fields and fields["review_mode"] not in ("human", "swarm", "either"):
        raise HTTPException(400, "review_mode must be human|swarm|either.")
    if "autonomy" in fields and fields["autonomy"] not in ("gate", "auto", "step", None, ""):
        raise HTTPException(400, "autonomy must be gate|auto|step.")
    for k in ("engineers_enabled", "merge_gate", "is_primary", "auto_go_live"):
        if k in fields:
            fields[k] = 1 if str(fields[k]).lower() in ("1", "true", "on") else 0
    conn = get_conn()
    row = conn.execute("SELECT * FROM dev_projects WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, "Project not found")
    # Policy: the STORE's local dev swarm ALWAYS works on 'dev' — main is Claude's lane
    # (local models → dev, Claude → master). Side projects keep the dev/main toggle.
    if fields.get("work_branch") == "main" and (row["is_primary"] or (row["kind"] or "") == "store"):
        conn.close()
        raise HTTPException(400, "The store's local dev swarm always works on 'dev' (main is Claude's "
                                 "lane; their work reaches main via the review→promote pipeline). "
                                 "work_branch=main is only for side projects.")
    if fields.get("is_primary") == 1:
        conn.execute("UPDATE dev_projects SET is_primary=0 WHERE id!=?", (pid,))
    sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=datetime('now')"
    conn.execute(f"UPDATE dev_projects SET {sets} WHERE id=?", (*fields.values(), pid))
    conn.commit()
    row = conn.execute("SELECT * FROM dev_projects WHERE id=?", (pid,)).fetchone()
    d = _project_dict(conn, row)
    conn.close()
    return d


@router.delete("/api/github/projects/{pid}")
def delete_project(pid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM dev_projects WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, "Project not found")
    if row["kind"] == "store":
        conn.close(); raise HTTPException(400, "The store project is permanent — it can't be deleted.")
    conn.execute("DELETE FROM dev_projects WHERE id=?", (pid,))
    conn.commit(); conn.close()
    return {"ok": True}


# ── Engineers settings surface (God Console + Workboard) ─────────────────────
def engineers_setting(key: str) -> str:
    return get_setting(key, ENGINEERS_SETTINGS_DEFAULTS.get(key, ""))


@router.get("/api/github/engineers-settings")
def get_engineers_settings():
    conn = get_conn()
    projects = [dict(r) for r in conn.execute(
        "SELECT id,kind,name,work_branch,is_primary,engineers_enabled,merge_gate,autonomy,"
        "review_mode,auto_go_live FROM dev_projects ORDER BY is_primary DESC, id").fetchall()]
    conn.close()
    return {
        "settings": {k: engineers_setting(k) for k in ENGINEERS_SETTINGS_DEFAULTS},
        "defaults": dict(ENGINEERS_SETTINGS_DEFAULTS),
        "projects": projects,
    }


@router.post("/api/github/engineers-settings")
def save_engineers_settings(body: dict):
    updates = (body or {}).get("settings", body or {})
    if not isinstance(updates, dict):
        raise HTTPException(400, "settings must be an object")
    clean = {}
    for k, v in updates.items():
        if k not in ENGINEERS_SETTINGS_DEFAULTS:
            continue
        v = str(v).strip()
        if k in ("engineers_auto", "engineers_auto_answer"):
            v = "on" if v.lower() in ("1", "true", "on") else "off"
        elif k == "engineers_interval_hours":
            try:
                v = str(max(1, min(168, int(float(v)))))
            except Exception:
                raise HTTPException(400, "engineers_interval_hours must be 1–168")
        clean[k] = v
    conn = get_conn()
    for k, v in clean.items():
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, v))
    conn.commit(); conn.close()
    return get_engineers_settings()

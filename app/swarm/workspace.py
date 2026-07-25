"""Dev Swarm — git sandboxing + file scoping in the working worktree.

The working directory is a function of the job's project (dev_projects) and its
`work_branch` toggle: the store-on-dev default is config.REPO_DEV (unchanged);
`work_branch='main'` edits the project's LIVE worktree instead — but the human
merge gate (/approve → /promote or a gated push) still applies before anything
reaches the live remote. Scoped jobs may only touch their listed paths.
Parsing the coder's strict fenced FILE format and reading real code for context
also lives here.

No intra-package dependencies (db/config only).
"""
import json
import re
import subprocess
from pathlib import Path

from config import REPO_DEV, GIT_BIN
from db import get_conn


# ─────────────────────────────────────────────────────────────────────────────
# worktree helpers
# ─────────────────────────────────────────────────────────────────────────────
def _git_dev(*args, timeout=60) -> tuple[int, str]:
    try:
        r = subprocess.run([GIT_BIN, "-C", REPO_DEV, *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _git_ws(base: str, *args, timeout=60) -> tuple[int, str]:
    """git in an arbitrary project worktree (same contract as _git_dev)."""
    try:
        r = subprocess.run([GIT_BIN, "-C", str(base), *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


# ─────────────────────────────────────────────────────────────────────────────
# project resolution — which worktree does this job edit?
# ─────────────────────────────────────────────────────────────────────────────
def _job_project(job: dict) -> dict | None:
    """The job's dev_projects row: via swarm_jobs.project_id, else the primary
    store project (legacy jobs with project_id NULL belong to the store)."""
    try:
        conn = get_conn()
        row = None
        pid = (job or {}).get("project_id")
        if pid:
            row = conn.execute("SELECT * FROM dev_projects WHERE id=?", (pid,)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM dev_projects WHERE is_primary=1 ORDER BY id LIMIT 1").fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM dev_projects WHERE kind='store' ORDER BY id LIMIT 1").fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _job_workdir(job: dict) -> str:
    """Base directory the swarm edits for this job. work_branch='dev' → the
    project's dev worktree (store = REPO_DEV, the historical default);
    work_branch='main' → the project's live worktree. The merge gate is about
    pushing to the LIVE remote and applies in both cases."""
    p = _job_project(job)
    if not p:
        return REPO_DEV
    wb = (p.get("work_branch") or "dev").strip() or "dev"
    if wb == "dev":
        return p.get("dev_path") or p.get("local_path") or REPO_DEV
    return p.get("local_path") or p.get("dev_path") or REPO_DEV


def _push_ws(base: str, branch: str = None) -> tuple[bool, str, str]:
    """Best-effort push of the workdir's branch to origin, so swarm commits land
    on the GitHub dev branch (pipeline stage 2). Never raises: returns
    (ok, branch, detail) and the caller logs the outcome either way."""
    try:
        if not branch:
            rc, cur = _git_ws(base, "rev-parse", "--abbrev-ref", "HEAD")
            branch = cur.strip() if rc == 0 and cur.strip() else "dev"
        rc, out = _git_ws(base, "push", "origin", branch, timeout=120)
        return rc == 0, branch, out[:300]
    except Exception as e:
        return False, branch or "?", str(e)[:300]


def _prepare_workdir(job: dict) -> str:
    """Resolve the job's working directory and, for non-store projects with a
    single checkout, put it on the branch the work_branch toggle selects
    ('main' → the project's live branch; 'dev' → a dev branch, created on
    first use). The store's worktrees are already pinned to their branches."""
    p = _job_project(job)
    base = _job_workdir(job)
    if not p or (p.get("kind") or "") == "store":
        return base
    wb = (p.get("work_branch") or "dev").strip() or "dev"
    target = (p.get("live_branch") or "main") if wb == "main" else "dev"
    rc, cur = _git_ws(base, "rev-parse", "--abbrev-ref", "HEAD")
    if rc == 0 and cur.strip() and cur.strip() != target:
        rc2, _out = _git_ws(base, "checkout", target)
        if rc2 != 0 and target == "dev":
            _git_ws(base, "checkout", "-b", "dev")
    return base


def _scoped_paths(job: dict) -> list[str]:
    try:
        return json.loads(job.get("paths") or "[]")
    except Exception:
        return []


def _path_allowed(rel: str, job: dict) -> bool:
    """For scoped jobs, only allow writes within the listed files/folders."""
    scope = job.get("scope") or "project"
    if scope == "project":
        return True
    paths = _scoped_paths(job)
    rel = rel.lstrip("/")
    for p in paths:
        p = p.strip().lstrip("/")
        if scope == "file" and rel == p:
            return True
        if scope == "folder" and (rel == p or rel.startswith(p.rstrip("/") + "/")):
            return True
    return False


_FILE_RE = re.compile(r"<<<FILE\s+(.+?)>>>\s*\n(.*?)\n?<<<END>>>", re.DOTALL)


def _parse_files(text: str) -> list[tuple[str, str]]:
    out = []
    for m in _FILE_RE.finditer(text):
        path = m.group(1).strip()
        content = m.group(2)
        # strip a leading ```lang fence and trailing ``` if the model added them
        content = re.sub(r"^```[\w.-]*\n", "", content)
        content = re.sub(r"\n```\s*$", "", content)
        out.append((path, content))
    return out


def _read_scoped_context(job: dict, limit_bytes=12000) -> str:
    """Current contents of the scoped files (so the coder edits the real code)."""
    scope = job.get("scope") or "project"
    if scope == "project":
        return "(whole-project scope — no specific files preloaded)"
    base = _job_workdir(job)
    chunks = []
    for p in _scoped_paths(job):
        fp = Path(base) / p.strip().lstrip("/")
        if fp.is_file():
            try:
                txt = fp.read_text(errors="replace")[:limit_bytes]
                chunks.append(f"<<<FILE {p}>>>\n{txt}\n<<<END>>>")
            except Exception:
                pass
    return "\n\n".join(chunks) or "(scoped files not found in the working worktree)"


def _fallback_single_file(out: str, job: dict) -> list[tuple[str, str]]:
    """When a coder ignores the FILE format but the job targets ONE file, salvage the
    content: prefer a fenced code block, else the cleaned output."""
    paths = _scoped_paths(job)
    if (job.get("scope") == "file") and len(paths) == 1:
        m = re.search(r"```[\w.-]*\n(.*?)```", out, re.DOTALL)
        content = (m.group(1) if m else out).strip()
        if content and len(content) < 20000:
            return [(paths[0].strip().lstrip("/"), content)]
    return []


def _repo_tree(base: str = None) -> str:
    """A compact map of the working worktree's real layout so the architect scopes
    subtasks to paths that actually exist (not invented src/… paths)."""
    rc, out = _git_ws(base or REPO_DEV, "ls-files")
    files = out.split()
    if not files:
        return "(repo layout unavailable)"
    top_dirs = sorted({f.split("/")[0] for f in files if "/" in f})
    root_files = [f for f in files if "/" not in f][:20]
    app_sub = sorted({"/".join(f.split("/")[:2]) for f in files if f.startswith("app/")})[:40]
    static_sub = sorted({"/".join(f.split("/")[:3]) for f in files if f.startswith("static/js/")})[:30]
    return ("Top-level dirs: " + ", ".join(top_dirs) +
            "\nRoot files: " + ", ".join(root_files) +
            "\napp/ layout: " + ", ".join(app_sub) +
            "\nstatic/js: " + ", ".join(static_sub))


def _read_files(paths: list, per: int = 2500, total: int = 12000, base: str = None) -> tuple[str, list]:
    """Read a few existing files from the working worktree (truncated) so the architect
    plans against the REAL code. Returns (bundle_text, actually_read_paths)."""
    chunks, read, used = [], [], 0
    for p in paths[:6]:
        fp = Path(base or REPO_DEV) / p.strip().lstrip("/")
        if not fp.is_file():
            continue
        try:
            t = fp.read_text(errors="replace")[:per]
        except Exception:
            continue
        if used + len(t) > total:
            break
        used += len(t)
        chunks.append(f"=== {p} ===\n{t}")
        read.append(p)
    return ("\n\n".join(chunks), read)

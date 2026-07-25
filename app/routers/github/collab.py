"""Shared ``collab`` branch for HUMAN collaborators — collab → dev → main.

One branch (``collab``, cut from ``dev``) where collaborators added via the
GitHub tab push their work. The owner reviews and merges ``collab → dev``
here; from ``dev`` the work reaches ``main`` through the EXISTING
review → approve → promote pipeline (``jobs.py`` promote_job) — this module
deliberately adds NO collab→main path.

All git operations run in ``REPO_DEV`` (the store-dev worktree, checked out
on ``dev``). Origin fetches are throttled to once/60s like devstore's
status endpoint, because the UI polls status.

The merge endpoint mirrors promote_job's conflict handling: on any merge
conflict it runs ``git merge --abort`` and returns HTTP 409 with the
conflicting files — dev is never left half-merged.
"""
import time

from deps import *   # config (REPO_DEV, GIT_BIN), HTTPException, logger
from ._base import router, _gitc

_collab_fetch = {"t": 0.0}   # throttle `git fetch origin collab` (UI polls status)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _branch_exists(ref: str) -> bool:
    rc, _ = _gitc(REPO_DEV, "rev-parse", "--verify", "-q", ref)
    return rc == 0


def _collab_ref() -> str | None:
    """Freshest collab ref to compare/merge: origin/collab (collaborators push
    there) if present, else the local branch, else None."""
    if _branch_exists("origin/collab"):
        return "origin/collab"
    if _branch_exists("collab"):
        return "collab"
    return None


def _waiting_commits(ref: str, limit: int = 20):
    """Commits on collab not yet in dev → [{sha, subject}]."""
    rc, out = _gitc(REPO_DEV, "log", f"dev..{ref}", "--format=%h%x09%s", f"-{limit}")
    if rc != 0:
        return []
    commits = []
    for ln in out.splitlines():
        if "\t" in ln:
            sha, subject = ln.split("\t", 1)
            commits.append({"sha": sha.strip(), "subject": subject.strip()})
    return commits


def _dev_dirty_count() -> int:
    rc, porc = _gitc(REPO_DEV, "status", "--porcelain")
    return len([l for l in porc.splitlines() if l.strip()]) if rc == 0 else 0


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/github/collab/status")
def collab_status():
    """State of the shared collab branch vs dev: does it exist, what's waiting
    to merge (commits/contributors/diff), and whether the dev worktree is
    clean enough to merge into. origin/collab is fetched at most once/60s."""
    now = time.time()
    if now - _collab_fetch["t"] > 60:
        _collab_fetch["t"] = now
        _gitc(REPO_DEV, "fetch", "origin", "collab", timeout=30)   # best-effort

    ref = _collab_ref()
    if ref is None:
        return {"exists": False, "ahead": 0, "behind": 0, "commits": [],
                "contributors": [], "diff_stat": "", "dirty": _dev_dirty_count()}

    ahead = behind = 0
    rc, counts = _gitc(REPO_DEV, "rev-list", "--left-right", "--count",
                       f"dev...{ref}")
    if rc == 0 and "\t" in counts:
        b, a = counts.split("\t")[:2]          # left = dev-only, right = collab-only
        behind, ahead = int(b.strip() or 0), int(a.strip() or 0)

    contributors = []
    rc, out = _gitc(REPO_DEV, "shortlog", "-sne", f"dev..{ref}")
    if rc == 0:
        for ln in out.splitlines():
            parts = ln.strip().split("\t", 1)
            if len(parts) == 2 and parts[0].strip().isdigit():
                contributors.append({"name": parts[1].strip(), "count": int(parts[0])})

    rc, diff_stat = _gitc(REPO_DEV, "diff", "--stat", f"dev...{ref}")
    if rc != 0:
        diff_stat = ""

    return {"exists": True, "ref": ref, "ahead": ahead, "behind": behind,
            "commits": _waiting_commits(ref), "contributors": contributors,
            "diff_stat": diff_stat.strip()[-1500:], "dirty": _dev_dirty_count()}


@router.post("/api/github/collab/ensure")
def collab_ensure():
    """Create the shared collab branch off dev (and publish it to origin) if it
    doesn't exist yet. Idempotent — ``already: true`` when it's already there."""
    if _collab_ref() is not None:
        return {"ok": True, "already": True,
                "note": "collab branch already exists — add collaborators and share it."}
    steps = []
    rc, out = _gitc(REPO_DEV, "branch", "collab", "dev")
    steps.append({"step": "branch collab (off dev)", "ok": rc == 0, "detail": out[:300]})
    if rc != 0:
        raise HTTPException(409, f"Could not create collab off dev: {out[:300]}")
    rc, out = _gitc(REPO_DEV, "push", "-u", "origin", "collab", timeout=60)
    steps.append({"step": "push -u origin collab", "ok": rc == 0, "detail": out[:300]})
    if rc != 0:
        # branch exists locally; the push can be retried — surface it clearly
        raise HTTPException(409, "collab created locally but push to origin failed — "
                                 f"retry Create collab branch. {out[:300]}")
    _collab_fetch["t"] = 0.0   # next status sees origin/collab immediately
    logger.info("collab branch created off dev and pushed to origin")
    return {"ok": True, "already": False, "steps": steps,
            "note": "collab created off dev and pushed. Collaborators can push to it now."}


@router.post("/api/github/collab/merge-to-dev")
def collab_merge_to_dev():
    """Conflict-safe merge collab → dev (mirrors promote_job's merge handling):
    dev worktree must be on ``dev`` and clean; on conflict the merge is ABORTED
    and 409 returned with the conflicting files — dev is never left half-merged.
    On success origin/dev is pushed. dev → main stays on the normal promote
    pipeline — nothing here touches master/main."""
    steps = []

    def log_step(name, rc, out):
        steps.append({"step": name, "ok": rc == 0, "detail": out[:300]})

    # 0. dev worktree must actually be on dev
    rc, branch = _gitc(REPO_DEV, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or branch.strip() != "dev":
        raise HTTPException(409, f"store-dev is on '{branch.strip() or '?'}', not dev — "
                                 "check the dev worktree out on dev first.")

    # 1. dev must be clean — we won't merge over uncommitted swarm/local work
    dirty = _dev_dirty_count()
    if dirty:
        raise HTTPException(409, f"dev worktree has {dirty} uncommitted change(s) — "
                                 "commit/stash dev first, then merge collab.")

    # 2. fresh collab (best-effort fetch; local branch is the fallback)
    rc, out = _gitc(REPO_DEV, "fetch", "origin", "collab", timeout=60)
    log_step("fetch origin collab", rc, out)
    _collab_fetch["t"] = time.time()
    ref = _collab_ref()
    if ref is None:
        raise HTTPException(409, "No collab branch found (locally or on origin) — "
                                 "create it first with Create collab branch.")

    merged_commits = _waiting_commits(ref)
    if not merged_commits:
        return {"ok": True, "steps": steps, "merged_commits": [],
                "note": "Nothing to merge — dev already has everything on collab."}

    # 3. merge collab → dev (abort + 409 on conflict, like promote_job's dev→master)
    rc, out = _gitc(REPO_DEV, "merge", ref, "--no-edit")
    log_step(f"merge {ref}→dev", rc, out)
    if rc != 0:
        rc2, conflicts = _gitc(REPO_DEV, "diff", "--name-only", "--diff-filter=U")
        files = [f for f in conflicts.splitlines() if f.strip()] if rc2 == 0 else []
        _gitc(REPO_DEV, "merge", "--abort")
        raise HTTPException(409, "Merge conflict — aborted, dev unchanged. Conflicting: "
                                 f"{', '.join(files[:12]) or out[:200]}. Resolve on collab "
                                 "(merge dev INTO collab there), push, then retry.")

    # 4. push dev so origin/dev carries the merged work
    rc, out = _gitc(REPO_DEV, "push", "origin", "dev", timeout=60)
    log_step("push dev", rc, out)

    logger.info(f"collab→dev merged ({len(merged_commits)} commit(s))")
    return {"ok": True, "steps": steps, "merged_commits": merged_commits,
            "note": "Now on dev — promote dev→main via the normal pipeline "
                    "(review→approve→promote) when ready."}

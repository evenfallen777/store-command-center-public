"""Domain A — GitHub status/auth/collaborators, repo browsing/creation, and the
store's own dev→master→retail worktree status. Routes register on the shared router.
"""
import json
import re
import subprocess
from typing import Optional

from deps import *   # get_conn, get_setting, config (GH_BIN, GIT_BIN, REPO_*), httpx, logger
from ._base import router, _gitc


# ─────────────────────────────────────────────────────────────────────────────
# gh / git subprocess helpers
# ─────────────────────────────────────────────────────────────────────────────
def _run(cmd: list, cwd: str = None, timeout: int = 60) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        raise HTTPException(500, f"'{cmd[0]}' not found — is it installed and on PATH?")
    except subprocess.TimeoutExpired:
        raise HTTPException(504, f"'{' '.join(cmd[:2])}' timed out")


def _gh_json(args: list, timeout: int = 60):
    rc, out, err = _run([GH_BIN, *args], timeout=timeout)
    if rc != 0:
        msg = (err or out).strip()
        if "gh auth login" in msg or "authentication" in msg.lower():
            raise HTTPException(401, "GitHub CLI not authenticated — run `gh auth login`.")
        raise HTTPException(502, f"gh error: {msg[:300]}")
    return json.loads(out) if out.strip() else None


def _git(path: str, args: list, timeout: int = 30) -> str:
    rc, out, err = _run([GIT_BIN, "-C", path, *args], timeout=timeout)
    if rc != 0:
        return ""   # best-effort; worktree status is informational
    return out.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Status + repositories
# ─────────────────────────────────────────────────────────────────────────────
def _remote_owner(url: str):
    """GitHub owner from a remote URL (https or ssh), or None."""
    m = re.search(r"github\.com[:/]([^/]+)/", url or "")
    return m.group(1) if m else None


@router.get("/api/github/status")
def github_status():
    rc, out, err = _run([GH_BIN, "auth", "status"])
    authed = rc == 0
    login = None
    if authed:
        rc2, o2, _ = _run([GH_BIN, "api", "user", "--jq", ".login"])
        login = o2.strip() if rc2 == 0 else None
    origin = _git(REPO_MASTER, ["remote", "get-url", "origin"])
    upstream = _git(REPO_MASTER, ["remote", "get-url", "upstream"])
    owner = _remote_owner(origin)
    return {"authenticated": authed, "login": login,
            "origin": origin or None, "origin_owner": owner,
            "owned": bool(login and owner and owner.lower() == login.lower()),
            "has_upstream": bool(upstream),
            "detail": (err or out).strip().splitlines()[:4] if not authed else None}


class GhLoginIn(BaseModel):
    token: str


@router.post("/api/github/auth/login")
def github_auth_login(body: GhLoginIn):
    """Sign the GitHub CLI in with a Personal Access Token (Settings → GitHub).
    The token is piped to `gh auth login --with-token` and kept by gh (system
    keyring) — it is never written to this app's database or logs. Afterwards
    `gh auth setup-git` wires plain git push/fetch (Updates, Promote) to the
    same credentials."""
    tok = (body.token or "").strip()
    if not tok:
        raise HTTPException(400, "Paste a GitHub Personal Access Token.")
    try:
        p = subprocess.run([GH_BIN, "auth", "login", "--hostname", "github.com", "--with-token"],
                           input=tok, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise HTTPException(500, f"'{GH_BIN}' not found — install the GitHub CLI (cli.github.com).")
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "gh auth login timed out")
    if p.returncode != 0:
        raise HTTPException(400, f"GitHub sign-in failed: {(p.stderr or p.stdout).strip()[:300]}")
    subprocess.run([GH_BIN, "auth", "setup-git"], stdin=subprocess.DEVNULL,
                   capture_output=True, text=True, timeout=30)
    rc, out, _ = _run([GH_BIN, "api", "user", "--jq", ".login"])
    return {"ok": True, "login": out.strip() if rc == 0 else None}


@router.post("/api/github/auth/logout")
def github_auth_logout():
    """Sign the GitHub CLI out of github.com."""
    try:
        p = subprocess.run([GH_BIN, "auth", "logout", "--hostname", "github.com"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise HTTPException(500, f"'{GH_BIN}' not found — is it installed and on PATH?")
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "gh auth logout timed out")
    if p.returncode != 0:
        raise HTTPException(400, f"Sign-out failed: {(p.stderr or p.stdout).strip()[:200]}")
    return {"ok": True}


class CollabIn(BaseModel):
    username: str
    permission: str = "push"    # pull | push | admin


@router.post("/api/github/repo/collaborator")
def github_add_collaborator(body: CollabIn):
    """Invite a GitHub user as a collaborator on THIS install's origin repo — the
    easy way to give your buddy access to a private repo (GitHub emails the invite)."""
    user = (body.username or "").strip().lstrip("@")
    if not user:
        raise HTTPException(400, "GitHub username required.")
    perm = body.permission if body.permission in ("pull", "push", "admin") else "push"
    origin = _git(REPO_MASTER, ["remote", "get-url", "origin"])
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", origin or "")
    if not m:
        raise HTTPException(400, "No GitHub origin remote configured on this install.")
    full = f"{m.group(1)}/{m.group(2)}"
    rc, out, err = _run([GH_BIN, "api", f"repos/{full}/collaborators/{user}",
                         "-X", "PUT", "-f", f"permission={perm}"])
    if rc != 0:
        raise HTTPException(502, f"GitHub said: {(err or out).strip()[:250]}")
    return {"ok": True,
            "message": f"Invited {user} to {full} ({perm}) — GitHub sends them an email invite."}


class SetupOwnIn(BaseModel):
    name: Optional[str] = "store-command-center"
    private: bool = True


@router.post("/api/github/repo/setup-own")
def github_setup_own(body: SetupOwnIn = None):
    """Fresh-install onboarding: FORK the repo this install was cloned from (your
    public repo) under the signed-in account, then wire the fork as `origin` and the
    original as `upstream` (updates keep flowing from it), and give you your own `dev`
    branch to work on. NO retail branch — retail is the OWNER's publish/scrub step; an
    install never ships a public version. A real fork (not a fresh copy) preserves the
    GitHub link so you can pull upstream updates and open PRs back."""
    body = body or SetupOwnIn()
    rc, out, _ = _run([GH_BIN, "api", "user", "--jq", ".login"])
    if rc != 0:
        raise HTTPException(401, "Sign in to GitHub first (GitHub tab → Sign in).")
    login = out.strip()

    origin = _git(REPO_MASTER, ["remote", "get-url", "origin"])
    if not origin:
        raise HTTPException(400, "No 'origin' remote to fork — this install has no source repo.")
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", origin)
    if not m:
        raise HTTPException(400, f"Could not parse a GitHub owner/repo from origin ({origin[:80]}).")
    up_owner, up_repo = m.group(1), m.group(2)
    if up_owner.lower() == login.lower():
        return {"ok": True, "already": True,
                "message": f"origin already belongs to {login} — this install is already yours."}

    fork_name = re.sub(r"[^A-Za-z0-9._-]", "-", (body.name or "").strip()) or up_repo
    steps = []

    def st(step, rc, detail):
        steps.append({"step": step, "ok": rc == 0, "detail": (detail or "")[:200]})

    # 1. Fork upstream under the signed-in account (server-side; keeps the GitHub fork
    #    relationship so updates pull from upstream and PRs flow back). Idempotent: gh
    #    reports an existing fork rather than failing.
    fork_args = [GH_BIN, "repo", "fork", f"{up_owner}/{up_repo}", "--clone=false"]
    if fork_name != up_repo:
        fork_args += ["--fork-name", fork_name]
    rc, o, err = _run(fork_args, timeout=300)
    detail = (err or o or "")
    forked_ok = rc == 0 or "already exists" in detail.lower()
    st(f"fork {up_owner}/{up_repo} → {login}/{fork_name}", 0 if forked_ok else 1, detail)
    if not forked_ok:
        raise HTTPException(502, f"gh repo fork failed: {detail.strip()[:300]}")

    # 2. Remotes: original → upstream (update source), fork → origin (your pushes).
    if _git(REPO_MASTER, ["remote", "get-url", "upstream"]):
        rc, ro = _gitc(REPO_MASTER, "remote", "remove", "origin")
        st("drop old origin (upstream already set)", rc, ro)
    else:
        rc, ro = _gitc(REPO_MASTER, "remote", "rename", "origin", "upstream")
        st("origin → upstream (your update source)", rc, ro)
    fork_url = f"https://github.com/{login}/{fork_name}.git"
    rc, ro = _gitc(REPO_MASTER, "remote", "add", "origin", fork_url)
    st("add origin = your fork", rc, ro)

    # 3. Your own dev branch off the current default. NO retail.
    cur = (_git(REPO_MASTER, ["rev-parse", "--abbrev-ref", "HEAD"]) or "main").strip()
    if not _git(REPO_MASTER, ["rev-parse", "--verify", "-q", "dev"]):
        rc, ro = _gitc(REPO_MASTER, "branch", "dev", cur)
        st("create your dev branch", rc, ro)

    # 4. Push the default branch + dev to your fork (never retail).
    rc, ro = _gitc(REPO_MASTER, "push", "-u", "origin", cur, timeout=300)
    st(f"push {cur} → your fork", rc, ro)
    rc, ro = _gitc(REPO_MASTER, "push", "-u", "origin", "dev", timeout=300)
    st("push dev → your fork", rc, ro)

    return {"ok": True, "steps": steps,
            "origin": f"https://github.com/{login}/{fork_name}",
            "upstream": f"https://github.com/{up_owner}/{up_repo}",
            "message": (f"Forked to {login}/{fork_name} with your own 'dev' branch. Updates flow "
                        f"from upstream ({up_owner}/{up_repo}); push your work to your fork and "
                        "open a PR to contribute back."),
            "warning": ("Editing store core files will conflict when you update — keep your changes "
                        "on your fork's 'dev' branch, and build add-ons as plugins where possible.")}


@router.get("/api/github/repos")
def github_repos(limit: int = 100):
    repos = _gh_json(["repo", "list", "--limit", str(limit), "--json",
                      "name,owner,description,visibility,isPrivate,pushedAt,url,"
                      "defaultBranchRef,isFork,stargazerCount"]) or []
    out = []
    for r in repos:
        out.append({
            "name": r.get("name"),
            "owner": (r.get("owner") or {}).get("login"),
            "full": f"{(r.get('owner') or {}).get('login')}/{r.get('name')}",
            "description": r.get("description") or "",
            "visibility": r.get("visibility"),
            "private": r.get("isPrivate"),
            "fork": r.get("isFork"),
            "stars": r.get("stargazerCount", 0),
            "pushed_at": r.get("pushedAt"),
            "url": r.get("url"),
            "default_branch": (r.get("defaultBranchRef") or {}).get("name"),
        })
    out.sort(key=lambda x: x.get("pushed_at") or "", reverse=True)
    return {"count": len(out), "repos": out}


@router.get("/api/github/repo")
def github_repo(full: str):
    """Detail for owner/name: description, branches, open PRs, open issues."""
    if "/" not in full:
        raise HTTPException(400, "Use owner/name.")
    view = _gh_json(["repo", "view", full, "--json",
                     "name,owner,description,url,defaultBranchRef,pushedAt,"
                     "visibility,isPrivate,diskUsage,repositoryTopics"]) or {}
    prs = _gh_json(["pr", "list", "--repo", full, "--state", "open", "--limit", "30",
                    "--json", "number,title,author,headRefName,url,createdAt"]) or []
    issues = _gh_json(["issue", "list", "--repo", full, "--state", "open", "--limit", "30",
                       "--json", "number,title,author,url,createdAt,labels"]) or []
    rc, out, _ = _run([GH_BIN, "api", f"repos/{full}/branches", "--jq",
                       "[.[].name]"])
    branches = json.loads(out) if rc == 0 and out.strip() else []
    return {
        "name": view.get("name"),
        "full": full,
        "description": view.get("description") or "",
        "url": view.get("url"),
        "visibility": view.get("visibility"),
        "default_branch": (view.get("defaultBranchRef") or {}).get("name"),
        "pushed_at": view.get("pushedAt"),
        "branches": branches,
        "pulls": [{"number": p["number"], "title": p["title"],
                   "author": (p.get("author") or {}).get("login"),
                   "branch": p.get("headRefName"), "url": p["url"]} for p in prs],
        "issues": [{"number": i["number"], "title": i["title"],
                    "author": (i.get("author") or {}).get("login"),
                    "url": i["url"], "labels": [l["name"] for l in (i.get("labels") or [])]}
                   for i in issues],
    }


@router.get("/api/github/repo/contents")
def github_repo_contents(full: str, path: str = "", ref: str = ""):
    """List files/folders at a path in a repo (for the file browser)."""
    if "/" not in full:
        raise HTTPException(400, "Use owner/name.")
    api_path = f"repos/{full}/contents/{path}".rstrip("/")
    args = ["api", api_path]
    if ref:
        args += ["-f", f"ref={ref}"]
    rc, out, err = _run([GH_BIN, *args])
    if rc != 0:
        raise HTTPException(502, f"gh error: {(err or out).strip()[:200]}")
    data = json.loads(out) if out.strip() else []
    if isinstance(data, dict):   # a single file was requested
        return {"path": path, "type": "file", "items": []}
    items = [{"name": i["name"], "type": i["type"], "path": i["path"], "size": i.get("size", 0)}
             for i in data]
    # dirs first, then files, alphabetical
    items.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    return {"path": path, "items": items}


@router.get("/api/github/repo/readme")
def github_repo_readme(full: str):
    """Fetch + render the repo README (markdown → HTML) if present."""
    if "/" not in full:
        raise HTTPException(400, "Use owner/name.")
    import base64
    rc, out, err = _run([GH_BIN, "api", f"repos/{full}/readme"])
    if rc != 0:
        return {"has_readme": False, "html": ""}
    try:
        data = json.loads(out)
        raw = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
    except Exception:
        return {"has_readme": False, "html": ""}
    try:
        html = render_markdown_simple(raw)   # from library, via deps
    except Exception:
        html = "<pre>" + raw.replace("<", "&lt;") + "</pre>"
    return {"has_readme": True, "name": data.get("name", "README.md"), "html": html}


@router.get("/api/github/repo/file")
def github_repo_file(full: str, path: str):
    """Fetch a single file's text content (for viewing / scoping a job to it)."""
    if "/" not in full or not path:
        raise HTTPException(400, "Need full=owner/name and path.")
    import base64
    rc, out, err = _run([GH_BIN, "api", f"repos/{full}/contents/{path}"])
    if rc != 0:
        raise HTTPException(502, f"gh error: {(err or out).strip()[:200]}")
    data = json.loads(out)
    if isinstance(data, list):
        raise HTTPException(400, "Path is a directory.")
    try:
        text = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
    except Exception:
        text = "(binary file)"
    return {"path": path, "size": data.get("size", 0), "content": text}


class CreateRepoIn(BaseModel):
    name: str
    description: Optional[str] = ""
    private: bool = True
    gitignore: Optional[str] = ""     # e.g. "Python", "Node"
    license: Optional[str] = ""       # e.g. "mit"
    add_readme: bool = True


@router.post("/api/github/repo/create")
def github_create_repo(body: CreateRepoIn):
    """Start a new project repo on the authenticated account."""
    if not body.name.strip():
        raise HTTPException(400, "Repo name required.")
    args = ["repo", "create", body.name.strip(),
            "--private" if body.private else "--public"]
    if body.description:
        args += ["--description", body.description]
    if body.gitignore:
        args += ["--gitignore", body.gitignore]
    if body.license:
        args += ["--license", body.license]
    if body.add_readme:
        args += ["--add-readme"]
    rc, out, err = _run([GH_BIN, *args])
    if rc != 0:
        raise HTTPException(502, f"gh repo create failed: {(err or out).strip()[:300]}")
    return {"ok": True, "url": out.strip() or None,
            "message": f"Created {'private' if body.private else 'public'} repo {body.name}"}


# ─────────────────────────────────────────────────────────────────────────────
# The store's own dev → master → retail worktree workflow status
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/github/workflow")
def github_workflow():
    worktrees = [("master", REPO_MASTER, 8787, "/store"),
                 ("dev", REPO_DEV, 8788, "/store-dev"),
                 ("retail", REPO_RETAIL, None, None)]
    out = []
    for branch, path, port, base in worktrees:
        head = _git(path, ["rev-parse", "--short", "HEAD"])
        cur = _git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
        dirty = _git(path, ["status", "--porcelain"])
        # ahead/behind vs master (skip for master itself)
        ahead = behind = None
        if branch != "master" and head:
            counts = _git(path, ["rev-list", "--left-right", "--count", f"master...{cur}"])
            if counts and "\t" in counts:
                b, a = counts.split("\t")[:2]
                behind, ahead = b.strip(), a.strip()
        out.append({"branch": branch, "path": path, "port": port, "base": base,
                    "head": head, "checked_out": cur,
                    "dirty": bool(dirty), "changed_files": len(dirty.splitlines()) if dirty else 0,
                    "ahead": ahead, "behind": behind, "exists": bool(head)})
    return {"worktrees": out}

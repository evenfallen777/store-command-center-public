"""Domain C — jobs / proposals data model, questions, approve/reject, system-agent
tasks, run, and the dev→master→retail promote. Routes register on the shared router.
"""
import json
from typing import Optional

from deps import *   # get_conn, threading, config (GIT_BIN, REPO_*, RESTART_CMD), HTTPException
import swarm
from ._base import router, _gitc


# ─────────────────────────────────────────────────────────────────────────────
# Jobs / proposals (data model; engine is Phase 2)
# ─────────────────────────────────────────────────────────────────────────────
class JobIn(BaseModel):
    title: str
    spec: Optional[str] = ""
    repo: Optional[str] = ""
    branch: Optional[str] = None      # default: the project's work_branch (else 'dev')
    autonomy: Optional[str] = None    # override global; gate|auto|step
    scope: Optional[str] = "project"  # project | folder | file
    paths: Optional[list] = None      # file/folder paths the job is scoped to
    agent_count: Optional[int] = None # per-job dynamic N (NULL = global default)
    project_id: Optional[int] = None  # dev_projects.id (NULL = the primary store project)


def _is_primary_project(conn, pid: int) -> bool:
    """True when pid is the primary store project — its board also owns the
    legacy jobs whose project_id is NULL (the pre-registry backfill rule)."""
    row = conn.execute("SELECT is_primary, kind FROM dev_projects WHERE id=?", (pid,)).fetchone()
    return bool(row and (row["is_primary"] or row["kind"] == "store"))


def _job_dict(row) -> dict:
    d = dict(row)
    conn = get_conn()
    d["open_questions"] = conn.execute(
        "SELECT COUNT(*) c FROM swarm_questions WHERE job_id=? AND status='open'", (d["id"],)
    ).fetchone()["c"]
    conn.close()
    return d


@router.get("/api/github/jobs")
def list_jobs(status: Optional[str] = None, project_id: Optional[int] = None):
    conn = get_conn()
    where, args = [], []
    if status:
        where.append("status=?"); args.append(status)
    if project_id is not None:
        if _is_primary_project(conn, project_id):
            where.append("(project_id=? OR project_id IS NULL)")
        else:
            where.append("project_id=?")
        args.append(project_id)
    q = "SELECT * FROM swarm_jobs" + (" WHERE " + " AND ".join(where) if where else "") \
        + " ORDER BY updated_at DESC"
    rows = conn.execute(q, tuple(args)).fetchall()
    conn.close()
    return [_job_dict(r) for r in rows]


@router.post("/api/github/jobs")
def create_job(body: JobIn):
    if not body.title.strip():
        raise HTTPException(400, "Job title required.")
    conn = get_conn()
    branch = body.branch
    if not branch:
        # default the working branch from the project's work_branch toggle
        branch = "dev"
        if body.project_id:
            row = conn.execute("SELECT work_branch FROM dev_projects WHERE id=?",
                               (body.project_id,)).fetchone()
            if row and row["work_branch"]:
                branch = row["work_branch"]
    cur = conn.execute(
        "INSERT INTO swarm_jobs (title,spec,repo,branch,autonomy,scope,paths,agent_count,project_id,status) "
        "VALUES (?,?,?,?,?,?,?,?,?,'proposed')",
        (body.title.strip(), body.spec, body.repo, branch, body.autonomy,
         body.scope or "project", json.dumps(body.paths or []), body.agent_count, body.project_id))
    conn.commit()
    jid = cur.lastrowid
    scope_note = ""
    if body.scope and body.scope != "project" and body.paths:
        scope_note = f" Scoped to {body.scope}: {', '.join(body.paths)}."
    conn.execute("INSERT INTO swarm_events (job_id,agent,kind,content) VALUES (?,?,?,?)",
                 (jid, "system", "system", "Job proposed." + scope_note))
    conn.commit()
    row = conn.execute("SELECT * FROM swarm_jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    return _job_dict(row)


@router.get("/api/github/jobs/{jid}")
def get_job(jid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM swarm_jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, "Job not found")
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM swarm_events WHERE job_id=? ORDER BY id", (jid,)).fetchall()]
    questions = [dict(r) for r in conn.execute(
        "SELECT * FROM swarm_questions WHERE job_id=? ORDER BY id", (jid,)).fetchall()]
    children = [dict(r) for r in conn.execute(
        "SELECT id,title,status,scope,paths FROM swarm_jobs WHERE parent_id=? ORDER BY id", (jid,)).fetchall()]
    conn.close()
    d = _job_dict(row)
    d["events"] = events
    d["questions"] = questions
    d["children"] = children
    return d


@router.patch("/api/github/jobs/{jid}")
def update_job(jid: int, body: dict):
    if "paths" in body and isinstance(body["paths"], list):
        body["paths"] = json.dumps(body["paths"])
    fields = {k: v for k, v in body.items()
              if k in ("title", "spec", "repo", "branch", "autonomy", "status",
                       "cron_enabled", "cron_interval", "scope", "paths", "agent_count",
                       "project_id")}
    if not fields:
        raise HTTPException(400, "Nothing to update.")
    sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=datetime('now')"
    conn = get_conn()
    conn.execute(f"UPDATE swarm_jobs SET {sets} WHERE id=?", (*fields.values(), jid))
    conn.commit()
    row = conn.execute("SELECT * FROM swarm_jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Job not found")
    return _job_dict(row)


@router.delete("/api/github/jobs/{jid}")
def delete_job(jid: int):
    conn = get_conn()
    conn.execute("DELETE FROM swarm_jobs WHERE id=?", (jid,))
    conn.execute("DELETE FROM swarm_events WHERE job_id=?", (jid,))
    conn.execute("DELETE FROM swarm_questions WHERE job_id=?", (jid,))
    conn.commit()
    conn.close()
    return {"ok": True}


class AnswerIn(BaseModel):
    answer: str


@router.post("/api/github/questions/{qid}/answer")
def answer_question(qid: int, body: AnswerIn):
    conn = get_conn()
    q = conn.execute("SELECT * FROM swarm_questions WHERE id=?", (qid,)).fetchone()
    if not q:
        conn.close(); raise HTTPException(404, "Question not found")
    conn.execute("UPDATE swarm_questions SET answer=?, status='answered', answered_at=datetime('now') WHERE id=?",
                 (body.answer, qid))
    conn.execute("INSERT INTO swarm_events (job_id,agent,kind,content) VALUES (?,?,?,?)",
                 (q["job_id"], "you", "answer", body.answer))
    conn.commit()
    # A blocking question (any stage) parked the job at awaiting_input — when the
    # LAST open question is answered, resume the drive automatically.
    remaining = conn.execute("SELECT COUNT(*) c FROM swarm_questions WHERE job_id=? AND status='open'",
                             (q["job_id"],)).fetchone()["c"]
    jrow = conn.execute("SELECT status FROM swarm_jobs WHERE id=?", (q["job_id"],)).fetchone()
    conn.close()
    resumed = False
    if remaining == 0 and jrow and jrow["status"] == "awaiting_input":
        resumed = swarm.start_job(q["job_id"])
        if resumed:
            _swarm_event(q["job_id"], "system", "system",
                         "▶️ All questions answered — resuming the swarm with your answers.")
    return {"ok": True, "resumed": resumed}


# ── Two-way Q&A around a job: owner directives + pure questions ─────────────
class DirectIn(BaseModel):
    text: str


@router.post("/api/github/jobs/{jid}/direct")
def direct_job(jid: int, body: DirectIn):
    """Inject an OWNER DIRECTIVE into a job. The engine picks up unconsumed
    directives at the next stage boundary (architect/coder/reviewer turn) and
    incorporates them into that turn's prompt — not mid-LLM-turn, so expect the
    change at the next stage. If the job is paused/awaiting input, the
    directive also resumes it (directing instead of answering unblocks it)."""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "Directive text required.")
    conn = get_conn()
    row = conn.execute("SELECT status FROM swarm_jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, "Job not found")
    conn.execute("INSERT INTO swarm_directives (job_id,text) VALUES (?,?)", (jid, text))
    conn.execute("INSERT INTO swarm_events (job_id,agent,kind,content) VALUES (?,?,?,?)",
                 (jid, "you", "directive", "🧭 " + text))
    conn.commit(); conn.close()
    resumed = False
    if row["status"] in ("paused", "awaiting_input") and not swarm.is_running(jid):
        resumed = swarm.start_job(jid)
        if resumed:
            _swarm_event(jid, "system", "system", "▶️ Resumed by your directive.")
    return {"ok": True, "resumed": resumed,
            "note": "The crew incorporates this at its next stage boundary."}


class AskIn(BaseModel):
    question: str


@router.post("/api/github/jobs/{jid}/ask")
def ask_job(jid: int, body: AskIn):
    """Ask the swarm ABOUT a job — one LM Studio turn answering from the job's
    context (spec, plan, latest commit, recent timeline). Pure Q&A: it never
    changes the job's status, plan, or code."""
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(400, "Question required.")
    conn = get_conn()
    row = conn.execute("SELECT * FROM swarm_jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, "Job not found")
    job = dict(row)
    events = conn.execute(
        "SELECT agent,kind,content FROM swarm_events WHERE job_id=? ORDER BY id DESC LIMIT 12",
        (jid,)).fetchall()
    conn.close()
    try:
        base = swarm._job_workdir(job)
        rc, head = _gitc(base, "show", "--stat", "--oneline", "-s", "HEAD")
    except Exception:
        head = ""
    ctx = (f"JOB #{jid}: {job['title']}  (status: {job['status']}, branch: {job.get('branch')})\n"
           f"SPEC:\n{(job.get('enhanced_spec') or job.get('spec') or '')[:3000]}\n"
           f"PLAN: {(job.get('plan') or '')[:1200]}\n"
           f"LATEST COMMIT: {head[:400]}\n"
           "RECENT TIMELINE (newest first):\n"
           + "\n".join(f"[{e['agent']}/{e['kind']}] {(e['content'] or '')[:220]}" for e in events))
    _swarm_event(jid, "you", "user_q", question)
    sys_p = ("You are a dev-swarm agent answering the OWNER's question about one of your jobs. "
             "Answer concisely and concretely from the provided job context. This is informational "
             "only — do NOT propose new work, do NOT change course, just explain.")
    try:
        cfg = swarm._config()
        roster = swarm._roster(cfg, 1)
        if not roster:
            raise RuntimeError("no local models configured")
        answer = swarm._turn(roster["planner"], sys_p,
                             ctx + f"\n\nOWNER'S QUESTION: {question}", max_tokens=800).strip()
    except Exception as e:
        answer = f"(The crew couldn't answer right now: {e})"
    _swarm_event(jid, "swarm", "agent_a", answer)
    return {"ok": True, "answer": answer}


# ── User final approval — only YOU approve/reject; reject needs a comment ────
@router.post("/api/github/jobs/{jid}/approve")
def approve_job(jid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM swarm_jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, "Job not found")
    conn.execute("UPDATE swarm_jobs SET decision='approved', status='approved', "
                 "updated_at=datetime('now') WHERE id=?", (jid,))
    conn.execute("INSERT INTO swarm_events (job_id,agent,kind,content) VALUES (?,?,?,?)",
                 (jid, "you", "system", "APPROVED by you. Ready to promote dev → master → retail."))
    conn.commit()
    try:                              # a leader-filed upgrade charges the company fund on approval
        import world_leader
        world_leader.charge_on_approval(conn, jid)
    except Exception:
        pass
    conn.close()
    return {"ok": True, "decision": "approved"}


class RejectIn(BaseModel):
    comment: str


@router.post("/api/github/jobs/{jid}/reject")
def reject_job(jid: int, body: RejectIn):
    if not (body.comment or "").strip():
        raise HTTPException(400, "A reject comment (what's wrong) is required.")
    conn = get_conn()
    row = conn.execute("SELECT * FROM swarm_jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, "Job not found")
    conn.execute("UPDATE swarm_jobs SET decision='rejected', decision_comment=?, status='paused', "
                 "updated_at=datetime('now') WHERE id=?", (body.comment.strip(), jid))
    conn.execute("INSERT INTO swarm_events (job_id,agent,kind,content) VALUES (?,?,?,?)",
                 (jid, "you", "comment", "REJECTED: " + body.comment.strip()))
    conn.commit(); conn.close()
    return {"ok": True, "decision": "rejected"}


# ── System agent tasks (install/configure deps the swarm needs) ──────────────
@router.get("/api/github/jobs/{jid}/system-tasks")
def list_system_tasks(jid: int):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM swarm_system_tasks WHERE job_id=? ORDER BY id", (jid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


class SystemTaskIn(BaseModel):
    job_id: Optional[int] = None
    request: str
    command: Optional[str] = ""


@router.post("/api/github/system-tasks")
def create_system_task(body: SystemTaskIn):
    """Record a system install/config request from the swarm. Execution is gated to
    Phase 2 (the system agent runs it, verifies, then signals the swarm to resume)."""
    if not body.request.strip():
        raise HTTPException(400, "request required")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO swarm_system_tasks (job_id,request,command,status) VALUES (?,?,?,'requested')",
        (body.job_id, body.request.strip(), body.command))
    conn.commit()
    if body.job_id:
        conn.execute("INSERT INTO swarm_events (job_id,agent,kind,content) VALUES (?,?,?,?)",
                     (body.job_id, "system-agent", "system",
                      "System install requested: " + body.request.strip()))
        conn.commit()
    row = conn.execute("SELECT * FROM swarm_system_tasks WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return dict(row)


@router.post("/api/github/system-tasks/{tid}/approve")
def approve_system_task(tid: int):
    """You approve the exact command; it then executes, verifies, and resumes the swarm.
    Ask-before-every-command: nothing runs until this endpoint is hit."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM swarm_system_tasks WHERE id=?", (tid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "System task not found")
    if row["status"] not in ("requested", "failed"):
        raise HTTPException(400, f"Task is '{row['status']}'.")
    conn = get_conn()
    conn.execute("UPDATE swarm_system_tasks SET status='approved', updated_at=datetime('now') WHERE id=?", (tid,))
    conn.commit(); conn.close()
    if row["job_id"]:
        _swarm_event(row["job_id"], "you", "system", f"Approved system command: {row['command']}")
    threading.Thread(target=swarm.run_system_task, args=(tid,), daemon=True,
                     name=f"systask-{tid}").start()
    return {"ok": True, "message": "Approved — running & verifying; watch the timeline."}


@router.post("/api/github/system-tasks/{tid}/reject")
def reject_system_task(tid: int):
    conn = get_conn()
    row = conn.execute("SELECT job_id, command FROM swarm_system_tasks WHERE id=?", (tid,)).fetchone()
    conn.execute("UPDATE swarm_system_tasks SET status='failed', report='Rejected by user', "
                 "updated_at=datetime('now') WHERE id=?", (tid,))
    conn.commit(); conn.close()
    if row and row["job_id"]:
        _swarm_event(row["job_id"], "you", "comment", f"Rejected system command: {row['command']}")
        _set_job_status(row["job_id"], "paused", "System command rejected")
    return {"ok": True}


class AskSystemIn(BaseModel):
    need: str


@router.post("/api/github/jobs/{jid}/ask-system")
def ask_system(jid: int, body: AskSystemIn):
    """Manually ask the system agent to propose a command for a stated need."""
    if not body.need.strip():
        raise HTTPException(400, "Describe what's needed.")
    threading.Thread(target=swarm.propose_system_task, args=(jid, body.need.strip()),
                     daemon=True, name=f"ask-system-{jid}").start()
    return {"ok": True, "message": "System agent is proposing a command — watch the timeline."}


@router.post("/api/github/jobs/{jid}/run")
def run_job(jid: int):
    """Launch (or resume) the swarm engine on this job in a background thread."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM swarm_jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Job not found")
    if swarm.is_running(jid):
        return {"ok": True, "message": "Already running — watch the timeline."}
    started = swarm.start_job(jid)
    return {"ok": started, "message": "Swarm started — planner is working. Watch the timeline."
            if started else "Already running."}


# ── Promote an APPROVED job: dev → master → retail (user-triggered) ──────────
# The shared git helper _gitc lives in _base (also used by repos' setup-own).


# ── Retail (PUBLIC branch) scrub lives in app/retail_scrub.py ────────────────
# (imported lazily in promote_job; that module is itself dropped from retail so the
# public branch never ships the real identifiers it maps from).


def _job_project_row(job_row):
    """The job's dev_projects row (dict) via project_id; NULL = primary store."""
    conn = get_conn()
    row = None
    try:
        pid = job_row["project_id"]
    except (KeyError, IndexError):
        pid = None
    if pid:
        row = conn.execute("SELECT * FROM dev_projects WHERE id=?", (pid,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM dev_projects WHERE is_primary=1 OR kind='store' "
                           "ORDER BY is_primary DESC, id LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def _promote_external(project: dict, jid: int) -> list:
    """Promote a NON-store project to its main: merge its dev branch into the
    live branch in the project's single checkout, push origin. Same policy as
    the store — reaching main is NOT going live."""
    base = project.get("local_path") or project.get("dev_path")
    if not base:
        raise HTTPException(400, "Project has no local checkout to promote in.")
    live = project.get("live_branch") or "main"
    steps = []

    def log_step(name, rc, out):
        steps.append({"step": name, "ok": rc == 0, "detail": out[:300]})

    rc, out = _gitc(base, "status", "--porcelain")
    if out.strip():
        _gitc(base, "add", "-A")
        _gitc(base, "commit", "-m", f"swarm job #{jid}: finalize")
    rc, out = _gitc(base, "push", "origin", "dev"); log_step("push dev", rc, out)
    rc, out = _gitc(base, "checkout", live); log_step(f"checkout {live}", rc, out)
    if rc != 0:
        raise HTTPException(409, f"Could not checkout {live}: {out[:200]}")
    rc, out = _gitc(base, "merge", "dev", "--no-edit"); log_step(f"merge dev→{live}", rc, out)
    if rc != 0:
        _gitc(base, "merge", "--abort")
        _gitc(base, "checkout", "dev")
        _swarm_event(jid, "system", "error", f"Promote failed at merge; {live} unchanged.\n" + out)
        raise HTTPException(409, f"Merge conflict — aborted. {live} unchanged. {out[:200]}")
    rc, out = _gitc(base, "push", "origin", live); log_step(f"push {live}", rc, out)
    _gitc(base, "checkout", "dev")   # leave the checkout back on the working branch
    return steps


def _bump_version(part: str = "patch") -> tuple:
    """Bump /VERSION in REPO_MASTER (semver). The version is the PUBLIC release
    number and per policy changes ONLY here (the public/retail publish step) —
    never on ordinary master/dev commits. Returns (old, new). Defaults to a
    patch bump; pass 'minor' or 'major' via the promote body's version_bump."""
    import os, re
    vpath = os.path.join(REPO_MASTER, "VERSION")
    try:
        old = open(vpath).read().strip()
    except Exception:
        old = "0.1.0"
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", old or "")
    M, mi, p = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 1, 0)
    if part == "major":   M, mi, p = M + 1, 0, 0
    elif part == "minor": mi, p = mi + 1, 0
    else:                 p = p + 1
    new = f"{M}.{mi}.{p}"
    with open(vpath, "w") as f:
        f.write(new + "\n")
    return old, new


@router.post("/api/github/jobs/{jid}/promote")
def promote_job(jid: int, body: dict = None):
    """Promote an approved job's dev work to MAIN: merge dev→master, push origin
    dev + master, then resync + re-genericize retail (+push). Reaching main is
    NOT going live — the running app is untouched until 'Apply main → live store'
    (POST /api/github/update-live) runs: your click, or automatically right after
    this promote when the project opted into auto_go_live (store/primary only)."""
    body = body or {}
    conn = get_conn()
    row = conn.execute("SELECT * FROM swarm_jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Job not found")
    if row["decision"] != "approved":
        raise HTTPException(400, "Approve the job first (you, or the reviewer swarm in swarm/either review mode).")
    project = _job_project_row(row)
    if project and (project.get("kind") or "store") != "store":
        # non-store project: merge its dev branch into its live branch + push
        steps = _promote_external(project, jid)
        _set_job_status(jid, "done",
                        f"On {project.get('live_branch') or 'main'} (pushed). Not live/deployed by this app.")
        _swarm_event(jid, "system", "system",
                     f"✅ On main ({project.get('live_branch') or 'main'} pushed). "
                     + "; ".join(f"{s['step']}={'ok' if s['ok'] else 'FAIL'}" for s in steps))
        return {"ok": True, "steps": steps, "restarting": False,
                "note": f"Merged and pushed to {project.get('live_branch') or 'main'}."}
    steps = []

    def log_step(name, rc, out):
        steps.append({"step": name, "ok": rc == 0, "detail": out[:300]})

    # 1. dev must be clean (all swarm changes committed)
    rc, out = _gitc(REPO_DEV, "status", "--porcelain")
    if out.strip():
        _gitc(REPO_DEV, "add", "-A")
        _gitc(REPO_DEV, "commit", "-m", f"swarm job #{jid}: finalize")
    # 1b. make sure the GitHub dev branch has everything (stage 2 of the pipeline)
    rc, out = _gitc(REPO_DEV, "push", "origin", "dev"); log_step("push dev", rc, out)
    # 2. master may be dirty with UNRELATED concurrent work (other tabs being built).
    #    If the swarm's changes don't overlap those files, safely stash them, merge,
    #    and restore afterwards. If they overlap, refuse and say exactly what's blocking.
    stashed = False
    rc, dirty = _gitc(REPO_MASTER, "status", "--porcelain")
    dirty_files = {ln[3:].strip() for ln in dirty.splitlines() if ln.strip()}
    if dirty_files:
        rc, devfiles = _gitc(REPO_MASTER, "diff", "--name-only", "master...dev")
        dev_files = {f for f in devfiles.split() if f}
        overlap = dirty_files & dev_files
        if overlap:
            raise HTTPException(409, "master has uncommitted changes that overlap this promotion "
                                     f"({', '.join(sorted(overlap))}). Commit or stash them first.")
        rc, out = _gitc(REPO_MASTER, "stash", "push", "-u", "-m", f"promote-job-{jid} autostash")
        stashed = rc == 0 and "No local changes" not in out
        log_step("stash unrelated work", rc, out)
        if not stashed:
            raise HTTPException(409, "Could not stash master's uncommitted changes — commit/stash "
                                     f"them manually, then promote. {out[:200]}")
    # 3. merge dev → master
    rc, out = _gitc(REPO_MASTER, "merge", "dev", "--no-edit")
    log_step("merge dev→master", rc, out)
    if rc != 0:
        _gitc(REPO_MASTER, "merge", "--abort")
        if stashed:
            _gitc(REPO_MASTER, "stash", "pop")   # restore the unrelated work
        _swarm_event(jid, "system", "error", "Promote failed at merge; master unchanged.\n" + out)
        raise HTTPException(409, f"Merge conflict — aborted. master unchanged. {out[:200]}")
    # 4. push master
    rc, out = _gitc(REPO_MASTER, "push", "origin", "master"); log_step("push master", rc, out)
    # 4b. restore the unrelated work we stashed (no overlap → clean pop)
    if stashed:
        rc, out = _gitc(REPO_MASTER, "stash", "pop")
        log_step("restore stashed work", rc, out)
        if rc != 0:
            _swarm_event(jid, "system", "error",
                         "Stashed work could not auto-restore — recover it with `git stash pop` "
                         f"in {REPO_MASTER}. {out[:200]}")
    # 5a. VERSION bumps ONLY here — the public release. Bump master's /VERSION so the
    #     scrubbed retail tree below carries the new number, and advance master (commit
    #     the single VERSION file only — never -A on the shared worktree).
    old_v, new_v = _bump_version((body.get("version_bump") or "patch"))
    _gitc(REPO_MASTER, "add", "VERSION")
    rc, out = _gitc(REPO_MASTER, "commit", "-m", f"release: v{new_v}")
    log_step(f"version {old_v} → {new_v}", rc, out)
    rc, out = _gitc(REPO_MASTER, "push", "origin", "master"); log_step("push master (release)", rc, out)
    # 5. resync retail to master, scrub for PUBLIC release, republish. retail must carry
    #    no real IPs/domains/identity/private docs, AND none of master's history (which
    #    still holds them). We CHAIN each publish onto the PREVIOUS public commit (never
    #    onto master): the public branch gets a normal linear history that consumers can
    #    `git pull --ff-only` (that's what /api/system/update-apply does) — while master's
    #    dirty history is never an ancestor. The tree is still rebuilt fresh from master +
    #    scrub each time, so no un-scrubbed blob is ever referenced.
    prev_retail = (_gitc(REPO_RETAIL, "rev-parse", "--verify", "-q", "retail")[1].strip()
                   or _gitc(REPO_RETAIL, "rev-parse", "--verify", "-q", "origin/retail")[1].strip())
    rc, out = _gitc(REPO_RETAIL, "reset", "--hard", "master"); log_step("retail reset→master", rc, out)
    import retail_scrub  # lazy: this module is dropped from retail, so import only here
    try:
        for line in retail_scrub.genericize_retail_tree(REPO_RETAIL):
            log_step("retail scrub", 0, line)
    except Exception as e:
        log_step("retail genericize", 1, str(e))
    _gitc(REPO_RETAIL, "add", "-A")
    # SAFETY GATE: never publish if any identifier survived the scrub.
    leaks = retail_scrub.verify_retail_clean(REPO_RETAIL)
    if leaks:
        log_step("retail VERIFY", 1, "LEAK — retail NOT pushed: " + ", ".join(leaks[:12]))
        _swarm_event(jid, "system", "error",
                     "Retail publish BLOCKED — identifiers survived the scrub in: "
                     + ", ".join(leaks[:20]) + ". Update retail_scrub.RETAIL_REPLACEMENTS/RETAIL_DROP.")
    else:
        log_step("retail VERIFY", 0, "clean — no identifiers survived the scrub")
        # Publish the scrubbed tree, chained onto the PREVIOUS public commit (prev_retail)
        # so consumers can fast-forward. The parent is a prior SCRUBBED commit, never
        # master, so master's real-identifier history is still never an ancestor. The
        # very first publish (no prev_retail) is parentless.
        rc, tree = _gitc(REPO_RETAIL, "write-tree")
        ct_args = ["commit-tree", tree.strip()]
        if prev_retail:
            ct_args += ["-p", prev_retail]
        ct_args += ["-m", f"Store Command Center v{new_v} — public release (genericized, job #{jid})"]
        rc2, commit = _gitc(REPO_RETAIL, *ct_args)
        if rc2 == 0 and commit.strip():
            rc, out = _gitc(REPO_RETAIL, "reset", "--hard", commit.strip())
            log_step("retail publish commit", rc, out)
            # Chained onto the prior public tip → a normal fast-forward push. force-with-lease
            # stays as a guard (we reset the local branch) and covers the rare re-root case.
            rc, out = _gitc(REPO_RETAIL, "push", "--force-with-lease", "origin", "retail")
            log_step("push retail", rc, out)
        else:
            log_step("retail commit-tree", 1, commit)

    # Reaching main is NOT going live. The merge wrote new code to the master
    # worktree on disk and origin/master is updated, but the running app is
    # deliberately untouched — NO restart here (restart_after_promote is ignored
    # by design). 'Apply main → live store' (POST /api/github/update-live) is the
    # only path that pulls main into the running store — by your click, or
    # automatically below when the project opted into auto_go_live.
    _set_job_status(jid, "done",
                    "On main (dev→master pushed). Not applied to the live store yet — "
                    "click '⬆️ Apply main → live store' to run it.")
    _swarm_event(jid, "system", "system",
                 "✅ On main (dev→master pushed). Not applied to the live store yet — click "
                 "'⬆️ Apply main → live store' to run it. Steps: "
                 + "; ".join(f"{s['step']}={'ok' if s['ok'] else 'FAIL'}" for s in steps))
    # Opt-in auto go-live (store/primary only — the one running instance we control):
    # review_mode=swarm + auto_go_live=1 is the fully-autonomous chain; both default off.
    auto_applied = False
    if project and project.get("auto_go_live"):
        auto_applied = _auto_go_live(jid)
    return {"ok": True, "steps": steps, "restarting": auto_applied, "auto_go_live": auto_applied,
            "note": ("Auto go-live: applying main to the live store and restarting…" if auto_applied else
                     "On main — not live. Click '⬆️ Apply main → live store' (Workboard or God "
                     "Console) when you want the running store to pull main and restart.")}


def _restart_live():
    """Restart the live app so it runs freshly-promoted code. Delayed slightly so the
    HTTP response returns first (mirrors system.py's restart)."""
    import threading as _t, shlex, subprocess as _sp
    def _do():
        if RESTART_CMD:
            _sp.Popen(shlex.split(RESTART_CMD))
        else:  # no supervisor cmd → re-exec in place
            import os, sys
            os.execv(sys.executable, [sys.executable] + sys.argv)
    _t.Timer(1.0, _do).start()


@router.post("/api/github/restart-live")
def restart_live():
    """Restart the live app now (loads any promoted code sitting in the master worktree)."""
    _restart_live()
    return {"ok": True, "message": "Restarting… the app will be back in a few seconds."}


# ── Update to LIVE — the ONLY path that pulls main into the running app ──────
_live_fetch = {"t": 0.0}   # throttle origin fetches from the 5s board poll


@router.get("/api/github/live-status")
def live_status():
    """Is an update available? Compares the LIVE worktree HEAD to origin/master
    (fetched at most once a minute — the board polls this every 5s). Also counts
    promoted-but-not-deployed jobs so the UI can say 'N approved changes are on
    main but not yet live.'"""
    import time as _t
    now = _t.time()
    if now - _live_fetch["t"] > 60:
        _live_fetch["t"] = now
        _gitc(REPO_MASTER, "fetch", "origin", "master", timeout=30)   # best-effort
    rc, head = _gitc(REPO_MASTER, "rev-parse", "--short", "HEAD")
    rc2, remote = _gitc(REPO_MASTER, "rev-parse", "--short", "origin/master")
    behind = ahead = 0
    rc3, counts = _gitc(REPO_MASTER, "rev-list", "--left-right", "--count", "HEAD...origin/master")
    if rc3 == 0 and "\t" in counts:
        a, b = counts.split("\t")[:2]
        ahead, behind = int(a.strip() or 0), int(b.strip() or 0)
    conn = get_conn()
    pending = conn.execute(
        "SELECT COUNT(*) c FROM swarm_jobs WHERE status='done' AND deployed_at IS NULL").fetchone()["c"]
    conn.close()
    return {"head": head.strip() or None, "remote_head": remote.strip() or None,
            "ahead": ahead, "behind": behind,
            "update_available": behind > 0 or pending > 0,
            "pending_done": pending}


def _apply_main_to_live() -> dict:
    """The shared body of 'Apply main → live store': fetch origin/master and
    fast-forward the LIVE worktree onto it (idempotent parity with GitHub main),
    stamp the promoted jobs deployed. Does NOT restart — callers decide how
    (endpoint: immediately; auto-go-live: deferred after commits + God note).
    Raises HTTPException(409) when the live worktree can't fast-forward."""
    steps = []

    def log_step(name, rc, out):
        steps.append({"step": name, "ok": rc == 0, "detail": out[:300]})

    rc, out = _gitc(REPO_MASTER, "fetch", "origin", "master", timeout=60)
    log_step("fetch origin master", rc, out)
    rc, out = _gitc(REPO_MASTER, "merge", "--ff-only", "origin/master")
    log_step("ff-merge origin/master", rc, out)
    if rc != 0:
        raise HTTPException(409, "Live worktree could not fast-forward onto origin/master "
                                 f"(dirty or diverged): {out[:200]}")
    was_current = "up to date" in out.lower()
    rc, head = _gitc(REPO_MASTER, "rev-parse", "--short", "HEAD")
    # Mark every promoted ('done') job as deployed: everything on main is, by
    # definition of the ff-merge above, now contained in the live HEAD. (Simple
    # mark-all-done approach — jobs don't record their own commit SHAs.)
    conn = get_conn()
    cur = conn.execute("UPDATE swarm_jobs SET deployed_at=datetime('now') "
                       "WHERE status='done' AND deployed_at IS NULL")
    marked = cur.rowcount
    conn.commit(); conn.close()
    _live_fetch["t"] = 0.0   # next live-status reflects the new HEAD immediately
    return {"steps": steps, "updated_to": head.strip() or None,
            "was_current": was_current, "marked_live": marked}


def _auto_go_live(jid: int) -> bool:
    """Opt-in auto path (dev_projects.auto_go_live=1, store/primary only — the
    one running instance we control; non-store projects have no separate live
    to apply, so this is never called for them). Applies main→live, commits all
    DB writes and posts the God-Console note FIRST, then defers the restart
    (threading.Timer, like system.update_apply) so everything flushes before
    the process goes down. Returns True when the apply succeeded."""
    try:
        r = _apply_main_to_live()
    except Exception as e:
        detail = getattr(e, "detail", None) or str(e)
        _swarm_event(jid, "system", "error", f"Auto go-live failed (still on main, not applied): {detail}")
        return False
    _swarm_event(jid, "system", "system",
                 f"🚀 Auto go-live: applied main → live store ({r['updated_to']}), "
                 f"{r['marked_live']} change(s) marked live. Restarting…")
    try:
        import world_ops as wo
        wo.note(f"🚀 Auto go-live: the approved work went live on the store ({r['updated_to']}) "
                "and the app is restarting. (Per-project toggle: Auto go-live after approval.)",
                kind="info")
    except Exception:
        pass
    threading.Timer(1.0, _restart_live).start()   # after commits + note are flushed
    return True


@router.post("/api/github/update-live")
def update_live():
    """⬆️ Apply main → live store: pulls the approved code on main into THIS
    running store (fetch + ff-only merge in the live worktree) and restarts it.
    Distinct from Settings → Updates (/api/system/update-apply), which pulls a
    published release/public channel. This is the ONLY action that takes 'on
    main' code live — promote never does (unless auto_go_live opted in)."""
    r = _apply_main_to_live()
    _restart_live()          # reuse the existing restart mechanism
    return {"ok": True, **r, "restarting": True,
            "note": "Live store is restarting on the new code — back in ~10s."}


def _swarm_event(jid, agent, kind, content):
    conn = get_conn()
    conn.execute("INSERT INTO swarm_events (job_id,agent,kind,content) VALUES (?,?,?,?)",
                 (jid, agent, kind, content))
    conn.commit(); conn.close()


def _set_job_status(jid, status, msg):
    conn = get_conn()
    conn.execute("UPDATE swarm_jobs SET status=?, progress_msg=?, updated_at=datetime('now') WHERE id=?",
                 (status, msg, jid))
    conn.commit(); conn.close()

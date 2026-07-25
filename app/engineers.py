"""🐙 THE ENGINEERS — the autonomous dev crew (modeled on world_leader.py).

Engineer personas (Ada, Grace, Linus, Turing) study each engineers-enabled
project on a cadence, pick the next improvement, file a REAL dev-swarm job
(swarm_jobs) — and, unlike the company leaders, immediately AUTO-RUN it, so
the swarm codes + tests on the project's WORKING branch with no human step.

The policy the whole feature enforces (identical for the store and every side
project): auto-build is free, but a HUMAN approval is required before code
lands on the project's LIVE branch — the existing /api/github/jobs/{id}/approve
→ /promote gate. The per-project `work_branch` toggle (dev/main) only changes
WHERE the swarm edits, never whether that gate applies.

Analysis uses ONLY the existing LM Studio path (swarm.llm._turn — the same
serialized orchestrator turn the swarm itself uses). When the LLM is
unavailable, a rotating fallback idea list (like world_leader.UPGRADE_IDEAS)
keeps the crew productive. The Engineers never touch the company fund — they
propose for free. (TODO: if a coin budget for Engineer work is ever wanted,
hook it here the way world_leader.charge_on_approval does — deliberately NOT
invented now.)

Toggles (settings rows, God Console → 🛠️ The Engineers):
  engineers_auto            on/off master switch (default off)
  engineers_interval_hours  cadence between proposals per project (default 12)
plus per-project dev_projects.engineers_enabled.
"""
import json
import logging
import threading
import time

from db import get_conn

logger = logging.getLogger("store")

TICK_SECONDS = 900          # 15 min — same tick the oracle auto-loop uses

# Job statuses that count as "the Engineers already have something in flight"
# for a project. awaiting_review is included on purpose: no new proposals pile
# up while one still waits at the human merge gate.
ACTIVE_STATUSES = ("proposed", "planning", "coding", "reviewing", "testing",
                   "decomposed", "awaiting_input", "awaiting_system", "awaiting_review")

ENGINEER_TAG = "[Dev-crew job filed by"   # attribution marker in the spec text (distinct from world_leader's "[Filed by")

# (engineer, title, spec) — rotating fallback when the LLM can't be reached.
ENGINEER_IDEAS = [
    ("Engineer Ada", "Harden error handling in one weak endpoint",
     "Pick one API endpoint that swallows or under-reports errors and improve it: "
     "clear HTTP status codes, a logged traceback, and a user-readable message. "
     "Small, focused, low-risk."),
    ("Engineer Grace", "Add a missing loading/empty state to one tab",
     "Find one frontend view that renders poorly while loading or when empty and give "
     "it a proper loading indicator and an empty-state message, matching the existing "
     "card/empty-state styles."),
    ("Engineer Linus", "Refactor one long function into readable pieces",
     "Locate one function over ~80 lines and split it into smaller, well-named helpers "
     "without changing behavior. Keep the public surface identical."),
    ("Engineer Turing", "Add docstrings + comments to one under-documented module",
     "Choose one module with sparse documentation and add a module docstring, function "
     "docstrings, and inline comments where the logic is non-obvious. No behavior change."),
    ("Engineer Ada", "Tighten input validation on one write endpoint",
     "Pick one POST/PATCH endpoint that accepts loosely-validated input and add strict "
     "validation with helpful 400 messages. No behavior change for valid input."),
    ("Engineer Grace", "Small UX polish pass on one existing view",
     "Improve one existing view's usability: clearer labels, a tooltip on a confusing "
     "control, or better button placement. Keep the change minimal and consistent with "
     "the app's style."),
]


# ── settings-row helpers (same pattern as world_leader's mget/mset, but on the
#    app settings table so the Engineers do not depend on the world sim) ──────
def _sget(conn, key, dflt=""):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else dflt


def _sset(conn, key, val):
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(val)))


def _enabled(conn) -> bool:
    return (_sget(conn, "engineers_auto", "off") or "off").lower() == "on"


def _interval_hours(conn) -> int:
    try:
        return max(1, int(float(_sget(conn, "engineers_interval_hours", "12") or 12)))
    except Exception:
        return 12


# ── project analysis → next idea ─────────────────────────────────────────────
def _project_signals(conn, project: dict) -> str:
    """Light Q&A material: in-repo signals (problems.md / TODO.md), recent swarm
    events, and open jobs — so the Engineer picks a NEXT step, not a repeat."""
    from pathlib import Path
    base = project.get("dev_path") or project.get("local_path") or ""
    chunks = []
    for fn in ("problems.md", "TODO.md"):
        try:
            fp = Path(base) / fn
            if fp.is_file():
                chunks.append(f"=== {fn} ===\n" + fp.read_text(errors="replace")[:3000])
        except Exception:
            pass
    try:
        evs = conn.execute(
            "SELECT e.content FROM swarm_events e JOIN swarm_jobs j ON j.id=e.job_id "
            "WHERE (j.project_id=? OR (? AND j.project_id IS NULL)) "
            "AND e.kind IN ('error','test') ORDER BY e.id DESC LIMIT 8",
            (project["id"], 1 if (project.get("is_primary") or project.get("kind") == "store") else 0)
        ).fetchall()
        if evs:
            chunks.append("=== recent swarm errors/tests ===\n" +
                          "\n".join((r["content"] or "")[:200] for r in evs))
    except Exception:
        pass
    try:
        titles = conn.execute(
            "SELECT title FROM swarm_jobs WHERE (project_id=? OR (? AND project_id IS NULL)) "
            "ORDER BY id DESC LIMIT 15",
            (project["id"], 1 if (project.get("is_primary") or project.get("kind") == "store") else 0)
        ).fetchall()
        if titles:
            chunks.append("=== jobs already filed (do NOT repeat these) ===\n" +
                          "\n".join(r["title"] for r in titles))
    except Exception:
        pass
    return "\n\n".join(chunks) or "(no signals available)"


_ENGINEER_SYS = (
    "You are a software ENGINEER on an autonomous dev crew improving a project. Given the "
    "project's signals (issue notes, TODOs, recent build errors, jobs already filed), pick ONE "
    "next improvement: small, concrete, buildable by a local-model dev swarm that rewrites whole "
    "files. Never repeat an already-filed job. Respond in STRICT JSON: "
    '{"title": "short imperative title", "spec": "2-6 sentences: what to change, where, and the '
    'acceptance criteria"}. Optionally, if the signals genuinely call for a brand-new small tool/'
    'skill instead, respond {"new_project": {"name": "repo-safe-name", "title": "...", "spec": "..."}}.'
)


def _llm_idea(project: dict, signals: str) -> dict:
    """One LM Studio turn (the swarm's own serialized path — NO other providers).
    Returns {} on any failure so the fallback idea list takes over."""
    try:
        from swarm._base import _config
        from swarm.llm import _roster, _turn, _extract_json
        cfg = _config()
        roster = _roster(cfg, 1)
        if not roster:
            return {}
        user = (f"PROJECT: {project.get('name')} (kind={project.get('kind')}, "
                f"work branch={project.get('work_branch')})\n\nSIGNALS:\n{signals[:9000]}")
        out = _turn(roster["planner"], _ENGINEER_SYS, user, max_tokens=900)
        data = _extract_json(out)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.info("engineers: LLM idea unavailable (%s) — using fallback list", e)
        return {}


# ── filing + auto-running a proposal ─────────────────────────────────────────
def _file_and_run(conn, project: dict, engineer: str, title: str, spec: str) -> int:
    """INSERT the swarm job attributed to an Engineer persona, then AUTO-RUN it
    (the no-human-step half of the policy). The human merge gate still guards
    the live branch regardless of work_branch."""
    wb = project.get("work_branch") or "dev"
    live = project.get("live_branch") or "main"
    spec = (f"{spec}\n\n{ENGINEER_TAG} {engineer} — auto-built by the dev swarm on the "
            f"'{wb}' working branch. Your approval is still required before anything "
            f"lands on '{live}' (the live branch).]")
    cur = conn.execute(
        "INSERT INTO swarm_jobs (title,spec,repo,branch,project_id,autonomy,status) "
        "VALUES (?,?,?,?,?,?,'proposed')",
        (title, spec, project.get("repo"), wb, project["id"], project.get("autonomy") or "auto"))
    conn.commit()
    jid = cur.lastrowid
    conn.execute("INSERT INTO swarm_events (job_id,agent,kind,content) VALUES (?,?,?,?)",
                 (jid, "engineers", "system",
                  f"🛠️ {engineer} proposed this for {project.get('name')} and auto-started the swarm "
                  f"(working branch: {wb}). Human approval still gates the live branch."))
    conn.commit()
    try:
        import world_ops as wo
        wo.note(f"🛠️ {engineer} started building: “{title}” on {project.get('name')} "
                f"({wb} branch). You'll approve before it goes live.", kind="info",
                from_agent=engineer, conn=conn)
    except Exception:
        pass
    try:
        import swarm
        swarm.start_job(jid)
    except Exception as e:
        logger.warning("engineers: auto-run of job %s failed: %s", jid, e)
    return jid


def _maybe_new_project(conn, np: dict, engineer: str) -> int | None:
    """An Engineer chose to start a NEW skill/software: create the repo+project
    via the registry (same path as POST /api/github/projects), then file+run
    the first job on it under the same gates. Best-effort — any failure falls
    back to normal per-project work."""
    try:
        name = (np.get("name") or "").strip()
        title = (np.get("title") or f"Bootstrap {name}").strip()
        spec = (np.get("spec") or "").strip()
        if not name:
            return None
        from routers.github.projects import create_project, ProjectIn
        d = create_project(ProjectIn(name=name, kind="engineer", create_repo=True,
                                     private=True, work_branch="dev", engineers_enabled=True))
        proj = conn.execute("SELECT * FROM dev_projects WHERE id=?", (d["id"],)).fetchone()
        return _file_and_run(conn, dict(proj), engineer, title, spec or
                             f"Bootstrap the new project '{name}': a minimal working skeleton "
                             "with a README describing the goal and a first runnable slice.")
    except Exception as e:
        logger.info("engineers: new-project idea skipped (%s)", e)
        return None


def maybe_propose(conn) -> bool:
    """Ticker hook: for each engineers-enabled project, at most one proposal per
    cadence window and at most ONE active Engineer job per project at a time."""
    if not _enabled(conn):
        return False
    hours = _interval_hours(conn)
    now = time.time()
    did = False
    projects = [dict(r) for r in conn.execute(
        "SELECT * FROM dev_projects WHERE engineers_enabled=1 ORDER BY is_primary DESC, id").fetchall()]
    for p in projects:
        try:
            last = float(_sget(conn, f"engineers_last_t_{p['id']}", 0) or 0)
            if now - last < hours * 3600:
                continue
            primary = 1 if (p.get("is_primary") or p.get("kind") == "store") else 0
            ph = ",".join("?" * len(ACTIVE_STATUSES))
            busy = conn.execute(
                f"SELECT COUNT(*) c FROM swarm_jobs WHERE status IN ({ph}) "
                "AND (project_id=? OR (? AND project_id IS NULL)) AND spec LIKE ?",
                (*ACTIVE_STATUSES, p["id"], primary, f"%{ENGINEER_TAG}%")).fetchone()["c"]
            if busy:
                continue   # one Engineer job in flight per project — wait for it
            signals = _project_signals(conn, p)
            idea = _llm_idea(p, signals)
            engineer = None
            if idea.get("new_project"):
                n = int(float(_sget(conn, "engineers_idea_n", 0) or 0))
                engineer = ENGINEER_IDEAS[n % len(ENGINEER_IDEAS)][0]
                if _maybe_new_project(conn, idea["new_project"], engineer):
                    _sset(conn, f"engineers_last_t_{p['id']}", now)
                    conn.commit()
                    did = True
                    continue
                idea = {}   # creation failed → fall through to a normal idea
            title = (idea.get("title") or "").strip()[:200]
            spec = (idea.get("spec") or "").strip()
            if title:
                n = int(float(_sget(conn, "engineers_idea_n", 0) or 0))
                engineer = ENGINEER_IDEAS[n % len(ENGINEER_IDEAS)][0]
            else:
                # LLM unavailable → rotate the fallback list, skipping titles already filed
                have = {r["title"] for r in conn.execute("SELECT title FROM swarm_jobs").fetchall()}
                n = int(float(_sget(conn, "engineers_idea_n", 0) or 0))
                for i in range(len(ENGINEER_IDEAS)):
                    e2, t2, s2 = ENGINEER_IDEAS[(n + i) % len(ENGINEER_IDEAS)]
                    t2p = f"{t2} — {p['name']}"
                    if t2p not in have:
                        engineer, title, spec = e2, t2p, s2
                        break
            if not title:
                _sset(conn, f"engineers_last_t_{p['id']}", now)   # nothing new → wait a window
                conn.commit()
                continue
            _file_and_run(conn, p, engineer or "Engineer Ada", title, spec)
            _sset(conn, f"engineers_last_t_{p['id']}", now)
            _sset(conn, "engineers_idea_n", int(float(_sget(conn, "engineers_idea_n", 0) or 0)) + 1)
            conn.commit()
            did = True
        except Exception:
            logger.exception("engineers: propose failed for project %s", p.get("id"))
    return did


def _answer_question(job: dict, question: str) -> str:
    """One LM Studio turn (swarm path, unified queue) answering the swarm's OWN
    blocking question from job context — decisive, so the build can continue."""
    try:
        from swarm._base import _config
        from swarm.llm import _roster, _turn
        roster = _roster(_config(), 1)
        if not roster:
            return "Proceed with the simplest, safest reasonable default."
        sys_p = ("You are the lead engineer auto-answering a question your dev crew raised while "
                 "building a job, so work continues WITHOUT waiting for a human. Give a SHORT, "
                 "DECISIVE answer: pick the simplest safe option and state it plainly. Never ask "
                 "another question.")
        user = (f"JOB: {job.get('title')}\nSPEC:\n{(job.get('spec') or '')[:2500]}\n"
                f"PLAN: {(job.get('plan') or '')[:1000]}\n\nCREW'S QUESTION: {question}\n\nYour decision:")
        ans = (_turn(roster["planner"], sys_p, user, max_tokens=400) or "").strip()
        return ans[:1500] or "Proceed with the simplest, safest reasonable default."
    except Exception as e:
        logger.info("engineers auto-answer LLM failed (%s)", e)
        return "Proceed with the simplest, safest reasonable default."


def _auto_answer(conn) -> bool:
    """Full-autonomy Q&A: when `engineers_auto_answer` is on, the swarm's blocking
    questions (job at awaiting_input) get an LLM answer from job context so the loop
    never stalls waiting for a human. Records the Q&A and resumes the job once all
    its questions are answered. When OFF, questions stay for the human (surfaced in
    the God Console Engineers block) — this is a no-op."""
    if (_sget(conn, "engineers_auto_answer", "off") or "off").lower() != "on":
        return False
    rows = conn.execute(
        "SELECT id,title,spec,plan FROM swarm_jobs WHERE status='awaiting_input' AND spec LIKE ?",
        (f"%{ENGINEER_TAG}%",)).fetchall()
    did = False
    for j in rows:
        qs = conn.execute("SELECT id,question FROM swarm_questions WHERE job_id=? AND status='open' ORDER BY id",
                          (j["id"],)).fetchall()
        if not qs:
            continue
        for q in qs:
            ans = _answer_question(dict(j), q["question"])
            conn.execute("UPDATE swarm_questions SET answer=?,status='answered',answered_at=datetime('now') WHERE id=?",
                         (ans, q["id"]))
            conn.execute("INSERT INTO swarm_events (job_id,agent,kind,content) VALUES (?,?,?,?)",
                         (j["id"], "auto-answer", "answer", f"🤖 (auto-answered) {ans}"))
        conn.commit()
        try:
            import swarm
            if swarm.start_job(j["id"]):
                did = True
                _sset(conn, f"engineers_noted_{j['id']}", "")   # reset any pending human-note flag
        except Exception as e:
            logger.warning("engineers auto-answer: resume job %s failed: %s", j["id"], e)
    return did


def _notify_review_gates(conn):
    """Engineer jobs newly at the merge gate (awaiting_review) → one God Console
    note each, so the pending live-branch approval is visible in Command/God."""
    try:
        rows = conn.execute(
            "SELECT id,title FROM swarm_jobs WHERE status='awaiting_review' AND spec LIKE ?",
            (f"%{ENGINEER_TAG}%",)).fetchall()
        for r in rows:
            key = f"engineers_noted_{r['id']}"
            if _sget(conn, key, ""):
                continue
            _sset(conn, key, "1")
            conn.commit()
            try:
                import world_ops as wo
                wo.note(f"🛠️ Engineers finished “{r['title']}” (job #{r['id']}) — it's built and "
                        "tested on the working branch. Approve or reject it before it goes live "
                        "(God Console → The Engineers, or the Dev Swarm tab).",
                        kind="need", conn=conn)
            except Exception:
                pass
    except Exception:
        logger.exception("engineers: review-gate notify failed")


# ── autonomous cadence (same shape as oracle.start_auto) ─────────────────────
_auto = {"thread": None}


def _auto_loop():
    time.sleep(120)
    while True:
        try:
            conn = get_conn()
            try:
                if _enabled(conn):
                    maybe_propose(conn)
                    _auto_answer(conn)
                    _notify_review_gates(conn)
            finally:
                conn.close()
        except Exception as e:
            logger.warning("engineers auto loop: %s", e)
        time.sleep(TICK_SECONDS)


def start_auto():
    """Start the background ticker (no-op work while engineers_auto is off —
    the same always-running/gated-inside pattern as oracle.start_auto)."""
    if _auto["thread"]:
        return
    t = threading.Thread(target=_auto_loop, daemon=True, name="engineers-auto")
    _auto["thread"] = t
    t.start()
    logger.info("engineers_auto started")

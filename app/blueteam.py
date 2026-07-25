"""🔵 THE BLUE-TEAM ENGINEER — defender / fixer.

The constructive mirror of the 🔴 red-team static auditor (app/redteam.py). Blue
consumes TRIAGED findings, drafts a fix spec, and files it as a REAL swarm job
through the EXISTING gated Engineers dev-swarm — gate-the-merge, human approval
before anything reaches the live store. It also answers red-team Q&A.

HARD SAFETY (non-negotiable):
  • Blue NEVER applies a change to live. It ONLY files a swarm_jobs row on the
    store's primary project WORK branch (dev) and starts the swarm — exactly the
    path engineers._file_and_run uses. The existing /api/github/jobs/{id}/approve
    → /promote merge gate (human approval before the live branch) is unchanged and
    still guards everything. There is no auto-merge and no auto-apply-to-live path
    in this module.
  • The fix job is SCOPED to the finding's file (swarm scope=file, paths=[file]) so
    the swarm can only touch the flagged file on the dev branch.
  • DEFAULT OFF. blueteam_enabled defaults to 0 — while off the auto loop is inert
    and the manual "run blue-team" endpoint refuses (mirrors world_jesus's gate).
  • No money, no gate/floor changes: this module writes only swarm_jobs/events (the
    normal dev-swarm surface) and the red-team findings' own lifecycle columns.

Toggles (settings rows, DEFAULT OFF):
  blueteam_enabled        0  master switch for the blue-team auto-fixer
  blueteam_interval_hours 6  cadence between auto fix passes
"""
import logging
import threading
import time

from deps import get_conn, get_setting

import redteam

logger = logging.getLogger("store")

TICK_SECONDS = 1800          # 30 min tick (gated inside — inert while off)

BLUE_TAG = "[Blue-team fix filed for red-team finding"   # attribution marker in the spec


def _sget(conn, key, dflt=""):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else dflt


def _sset(conn, key, val):
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(val)))


def _truthy(v):
    return str(v).lower() in ("1", "true", "yes", "on")


def enabled(conn=None):
    if conn is not None:
        return _truthy(_sget(conn, "blueteam_enabled", "0"))
    return _truthy(get_setting("blueteam_enabled", "0"))


def interval_hours(conn):
    try:
        return max(1, int(float(_sget(conn, "blueteam_interval_hours", "6") or 6)))
    except Exception:
        return 6


# ── the store's primary dev project (where blue files its fix jobs) ───────────
def _store_project(conn):
    row = conn.execute(
        "SELECT * FROM dev_projects WHERE is_primary=1 OR kind='store' ORDER BY is_primary DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


# ── file ONE fix through the gated dev swarm (mirrors engineers._file_and_run) ─
def file_fix(conn, fid):
    """Draft a fix spec for finding #fid and file it as a swarm job on the store's
    dev branch, scoped to the flagged file. Starts the swarm (auto-build on the
    WORK branch) — the human merge gate still guards the live branch. Links the
    finding → job and moves it to 'fixing'. Blue NEVER touches live directly."""
    redteam.ensure(conn)
    f = conn.execute("SELECT * FROM redteam_findings WHERE id=?", (int(fid),)).fetchone()
    if not f:
        return {"ok": False, "error": "finding not found"}
    if f["swarm_job_id"]:
        return {"ok": False, "error": f"a fix job (#{f['swarm_job_id']}) is already filed"}
    proj = _store_project(conn)
    if not proj:
        return {"ok": False, "error": "no store dev project registered"}
    wb = proj.get("work_branch") or "dev"
    live = proj.get("live_branch") or "master"
    loc = f["location"] or ""
    # scope the swarm to the single flagged file on the dev branch (so it can only
    # touch the flagged file); fall back to project scope if there's no clean path
    file_scoped = bool(loc) and (("/" in loc) or loc.endswith((".py", ".js", ".html", ".php", ".sh")))
    scope = "file" if file_scoped else "project"
    title = f"Security fix: {f['title']} ({loc}:{f['line']})"[:200]
    spec = (
        f"A red-team STATIC audit flagged a {f['severity'].upper()} issue.\n\n"
        f"FINDING: {f['title']}\n"
        f"LOCATION: {loc}:{f['line']}\n"
        f"CATEGORY: {f['category']}\n"
        f"REFERENCE: {f['reference']}\n"
        f"FLAGGED LINE: {f['snippet']}\n\n"
        f"RECOMMENDED FIX: {f['recommendation']}\n\n"
        f"Apply the smallest, focused fix that closes this finding without changing unrelated "
        f"behaviour. Keep the public surface identical. {BLUE_TAG} #{f['id']} — auto-built by the "
        f"dev swarm on the '{wb}' working branch. Human approval is still required before anything "
        f"lands on '{live}' (the live branch).]"
    )
    import json as _json
    cur = conn.execute(
        "INSERT INTO swarm_jobs (title,spec,repo,branch,project_id,autonomy,scope,paths,status) "
        "VALUES (?,?,?,?,?,?,?,?, 'proposed')",
        (title, spec, proj.get("repo"), wb, proj["id"], proj.get("autonomy") or "auto",
         scope, _json.dumps([loc] if scope == "file" and loc else [])))
    conn.commit()
    jid = cur.lastrowid
    conn.execute("INSERT INTO swarm_events (job_id,agent,kind,content) VALUES (?,?,?,?)",
                 (jid, "blueteam", "system",
                  f"🔵 Blue-team filed this fix for red-team finding #{f['id']} on the '{wb}' branch. "
                  f"Human approval still gates the live branch."))
    conn.execute("UPDATE redteam_findings SET swarm_job_id=?, status='fixing', updated_at=datetime('now') "
                 "WHERE id=?", (jid, int(fid)))
    conn.execute("INSERT INTO redteam_audit_log(finding_id,event,actor,detail) VALUES(?,?,?,?)",
                 (int(fid), "fix_filed", "blue", f"swarm job #{jid} filed on '{wb}' (scope={scope})"))
    conn.commit()
    # surface the constructive side (Jesus) + the dev-swarm gate note
    try:
        conn.execute("INSERT INTO world_events (agent_key, kind, text) VALUES (?,?,?)",
                     ("", "security", f"🔵 Blue-team filed a fix (job #{jid}) for finding #{fid}: {f['title']}."))
        conn.commit()
    except Exception:
        pass
    try:
        import world_ops as wo
        wo.note(f"🔵 Blue-team is building a security fix for finding #{fid} “{f['title']}” on the "
                f"'{wb}' branch (job #{jid}). You'll approve before it goes live.",
                kind="info", from_agent="Blue-team", conn=conn)
    except Exception:
        pass
    # auto-run the swarm (build on the WORK branch; the live gate is untouched)
    try:
        import swarm
        swarm.start_job(jid)
    except Exception as e:
        logger.warning("blueteam: auto-run of job %s failed: %s", jid, e)
    return {"ok": True, "finding_id": int(fid), "job_id": jid, "branch": wb, "scope": scope}


# ── answer a red-team Q&A question ────────────────────────────────────────────
def answer_qa(conn, qid, answer):
    return redteam.qa_answer(conn, qid, answer, answered_by="blue")


# ── the blue-team pass: pick triaged findings and file fixes ──────────────────
def run_pass(conn, force=False, limit=3):
    """Pick up to `limit` TRIAGED findings without a fix job and file a swarm fix
    for each. Refused while blueteam_enabled is off unless force=True (a manual
    owner trigger). Every fix goes through the gated dev swarm — no auto-deploy."""
    redteam.ensure(conn)
    if not enabled(conn) and not force:
        return {"ok": False, "skipped": "blue-team is disabled (blueteam_enabled is off)"}
    rows = conn.execute(
        "SELECT id FROM redteam_findings WHERE status='triaged' AND swarm_job_id IS NULL "
        "ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 "
        "WHEN 'low' THEN 3 ELSE 4 END, id ASC LIMIT ?", (int(limit),)).fetchall()
    filed = []
    for r in rows:
        res = file_fix(conn, r["id"])
        if res.get("ok"):
            filed.append({"finding_id": r["id"], "job_id": res["job_id"]})
    _sset(conn, "blueteam_last_t", time.time())
    conn.commit()
    return {"ok": True, "filed": filed, "count": len(filed)}


def status(conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        redteam.ensure(conn)
        triaged = conn.execute(
            "SELECT COUNT(*) c FROM redteam_findings WHERE status='triaged' AND swarm_job_id IS NULL"
        ).fetchone()["c"]
        in_fix = [dict(r) for r in conn.execute(
            "SELECT id, title, severity, swarm_job_id FROM redteam_findings "
            "WHERE status IN ('fixing','fixed') ORDER BY id DESC LIMIT 50").fetchall()]
        return {
            "enabled": enabled(conn),
            "interval_hours": interval_hours(conn),
            "last_pass": float(_sget(conn, "blueteam_last_t", 0) or 0),
            "triaged_waiting": triaged,
            "in_fix": in_fix,
            "safety": {
                "note": "Blue-team fixes route ONLY through the gated Engineers dev swarm (auto-build "
                        "on the dev branch; human approval before live). No auto-merge, no auto-apply.",
            },
        }
    finally:
        if own:
            conn.close()


def set_config(body, conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        redteam.ensure(conn)
        _on = (True, 1, "1", "true", "on", "yes")
        if "enabled" in body:
            _sset(conn, "blueteam_enabled", "1" if body["enabled"] in _on else "0")
        if "interval_hours" in body:
            try:
                _sset(conn, "blueteam_interval_hours", max(1, int(body["interval_hours"])))
            except (TypeError, ValueError):
                pass
        conn.commit()
        return status(conn)
    finally:
        if own:
            conn.close()


# ── autonomous cadence (gated inside — inert while off) ───────────────────────
_auto = {"thread": None}


def _auto_loop():
    time.sleep(180)
    while True:
        try:
            conn = get_conn()
            try:
                if enabled(conn):
                    last = float(_sget(conn, "blueteam_last_t", 0) or 0)
                    if time.time() - last >= interval_hours(conn) * 3600:
                        run_pass(conn)
                    conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("blueteam auto loop: %s", e)
        time.sleep(TICK_SECONDS)


def start_auto():
    """Start the background ticker (inert while blueteam_enabled is off)."""
    if _auto["thread"]:
        return
    t = threading.Thread(target=_auto_loop, daemon=True, name="blueteam-auto")
    _auto["thread"] = t
    t.start()
    logger.info("blueteam_auto started")

"""🔴🔵 Red-team / Blue-team security tier — endpoints (all under /api/security/redblue/*).

Thin routes over app/redteam.py (adversarial STATIC auditor + auditor lifecycle +
Q&A) and app/blueteam.py (defender that files fixes through the gated dev swarm).

Everything here is gated + defaults OFF:
  • The red-team auditor is read-only/look-only; the manual "run audit" refuses
    while redteam_enabled is off (an explicit ?force preview still does ONLY
    read-only static analysis).
  • Blue-team NEVER applies to live — /blue/run and /findings/{id}/fix file a
    swarm job on the dev branch through the existing gate-the-merge pipeline.
"""
from fastapi import HTTPException, Body

import redteam
import blueteam
from deps import get_conn
from ._base import router


# ── status + config ───────────────────────────────────────────────────────────
@router.get("/api/security/redblue/status")
def redblue_status():
    """Combined board: red-team findings/lifecycle/audit-trail + blue-team state."""
    conn = get_conn()
    try:
        return {"red": redteam.status(conn), "blue": blueteam.status(conn)}
    finally:
        conn.close()


@router.post("/api/security/redblue/red/config")
def red_config(body: dict = Body(default={})):
    """Red-team toggles: {enabled?: bool (default off), interval_hours?: int,
    llm_assist?: bool (ride the GPU queue for a one-line summary, default off)}."""
    return redteam.set_config(body or {})


@router.post("/api/security/redblue/blue/config")
def blue_config(body: dict = Body(default={})):
    """Blue-team toggles: {enabled?: bool (default off), interval_hours?: int}."""
    return blueteam.set_config(body or {})


# ── targets (configurable audit scope) ────────────────────────────────────────
@router.get("/api/security/redblue/targets")
def list_targets():
    conn = get_conn()
    try:
        return {"targets": redteam.list_targets(conn)}
    finally:
        conn.close()


@router.post("/api/security/redblue/targets")
def add_target(body: dict = Body(...)):
    """Add an audit target path. {path: str, label?: str, kind?: store|wordpress|frontend|system|other}"""
    conn = get_conn()
    try:
        return redteam.add_target(conn, (body or {}).get("path"),
                                  (body or {}).get("label"), (body or {}).get("kind", "other"))
    finally:
        conn.close()


@router.post("/api/security/redblue/targets/{tid}/toggle")
def toggle_target(tid: int, body: dict = Body(default={})):
    conn = get_conn()
    try:
        return redteam.toggle_target(conn, tid, bool((body or {}).get("enabled", True)))
    finally:
        conn.close()


@router.delete("/api/security/redblue/targets/{tid}")
def remove_target(tid: int):
    conn = get_conn()
    try:
        return redteam.remove_target(conn, tid)
    finally:
        conn.close()


# ── run an audit (manual) ─────────────────────────────────────────────────────
@router.post("/api/security/redblue/audit")
def run_audit(body: dict = Body(default={})):
    """Run a read-only static audit now. Refused while red-team is disabled unless
    {force: true} (which still performs ONLY read-only analysis)."""
    conn = get_conn()
    try:
        res = redteam.run_audit(conn, force=bool((body or {}).get("force")))
        if not res.get("ok"):
            raise HTTPException(409, res.get("skipped") or "red-team could not run")
        return res
    finally:
        conn.close()


# ── findings + lifecycle ──────────────────────────────────────────────────────
@router.get("/api/security/redblue/findings")
def list_findings(status: str = "", limit: int = 200):
    conn = get_conn()
    try:
        return redteam.findings(conn, status=status or None, limit=limit)
    finally:
        conn.close()


@router.post("/api/security/redblue/findings/{fid}/triage")
def triage_finding(fid: int):
    conn = get_conn()
    try:
        r = redteam.triage(conn, fid, actor="owner")
        if not r.get("ok"):
            raise HTTPException(404, r.get("error") or "not found")
        return r
    finally:
        conn.close()


@router.post("/api/security/redblue/findings/{fid}/transition")
def transition_finding(fid: int, body: dict = Body(...)):
    """Move a finding through the lifecycle: {status: open|triaged|fixing|fixed|verified|dismissed}."""
    conn = get_conn()
    try:
        r = redteam.transition(conn, fid, (body or {}).get("status", ""),
                               actor=(body or {}).get("actor", "owner"),
                               detail=(body or {}).get("detail"))
        if not r.get("ok"):
            raise HTTPException(400, r.get("error") or "bad transition")
        return r
    finally:
        conn.close()


@router.post("/api/security/redblue/findings/{fid}/dismiss")
def dismiss_finding(fid: int, body: dict = Body(default={})):
    conn = get_conn()
    try:
        return redteam.dismiss(conn, fid, detail=(body or {}).get("detail"))
    finally:
        conn.close()


@router.post("/api/security/redblue/findings/{fid}/fix")
def fix_finding(fid: int):
    """Blue-team: file a fix for this finding through the gated dev swarm (dev
    branch; human approval before live). Does NOT apply to live."""
    conn = get_conn()
    try:
        r = blueteam.file_fix(conn, fid)
        if not r.get("ok"):
            raise HTTPException(409, r.get("error") or "could not file fix")
        return r
    finally:
        conn.close()


@router.post("/api/security/redblue/findings/{fid}/verify")
def verify_finding(fid: int):
    """Auditor: re-read the flagged location (read-only) and confirm the fix closed it."""
    conn = get_conn()
    try:
        r = redteam.verify(conn, fid)
        if not r.get("ok") and r.get("error"):
            raise HTTPException(404, r["error"])
        return r
    finally:
        conn.close()


@router.get("/api/security/redblue/findings/{fid}/trail")
def finding_trail(fid: int):
    conn = get_conn()
    try:
        return {"trail": redteam.audit_trail(conn, finding_id=fid),
                "qa": redteam.qa_thread(conn, finding_id=fid)}
    finally:
        conn.close()


# ── blue-team pass (manual) ───────────────────────────────────────────────────
@router.post("/api/security/redblue/blue/run")
def blue_run(body: dict = Body(default={})):
    """Run a blue-team pass: file swarm fixes for triaged findings. Refused while
    blue-team is disabled unless {force: true}."""
    conn = get_conn()
    try:
        res = blueteam.run_pass(conn, force=bool((body or {}).get("force")),
                                limit=int((body or {}).get("limit", 3)))
        if not res.get("ok"):
            raise HTTPException(409, res.get("skipped") or "blue-team could not run")
        return res
    finally:
        conn.close()


# ── auditor sweep (advance fixed→verified for landed swarm jobs) ──────────────
@router.post("/api/security/redblue/audit/sweep")
def audit_sweep():
    conn = get_conn()
    try:
        n = redteam.auditor_sweep(conn)
        conn.commit()
        return {"ok": True, "advanced": n}
    finally:
        conn.close()


# ── Q&A channel (red ↔ blue, with owner escalation) ───────────────────────────
@router.get("/api/security/redblue/qa")
def qa_list(finding_id: int = 0):
    conn = get_conn()
    try:
        return {"qa": redteam.qa_thread(conn, finding_id=finding_id or None)}
    finally:
        conn.close()


@router.post("/api/security/redblue/qa")
def qa_ask(body: dict = Body(...)):
    """Post a Q&A question. {finding_id?: int, question: str, asker?: red|blue}."""
    conn = get_conn()
    try:
        r = redteam.qa_ask(conn, (body or {}).get("finding_id"), (body or {}).get("question", ""),
                          asker=(body or {}).get("asker", "red"))
        if not r.get("ok"):
            raise HTTPException(400, r.get("error") or "bad question")
        return r
    finally:
        conn.close()


@router.post("/api/security/redblue/qa/{qid}/answer")
def qa_answer(qid: int, body: dict = Body(...)):
    """Answer a Q&A question (blue by default). {answer: str, answered_by?: blue|red|owner}."""
    conn = get_conn()
    try:
        by = (body or {}).get("answered_by", "blue")
        r = (blueteam.answer_qa(conn, qid, (body or {}).get("answer", ""))
             if by == "blue" else redteam.qa_answer(conn, qid, (body or {}).get("answer", ""), answered_by=by))
        if not r.get("ok"):
            raise HTTPException(400, r.get("error") or "bad answer")
        return r
    finally:
        conn.close()


@router.post("/api/security/redblue/qa/{qid}/escalate")
def qa_escalate(qid: int):
    conn = get_conn()
    try:
        r = redteam.qa_escalate(conn, qid)
        if not r.get("ok"):
            raise HTTPException(404, r.get("error") or "not found")
        return r
    finally:
        conn.close()

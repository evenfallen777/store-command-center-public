"""Proposal review gate — the missing weed-out step between trend scan and generation.

An LLM judge scores each pending proposal 0-100 on marketability, originality,
print-on-demand fit and policy safety — after a cheap LOCAL near-duplicate check
against other proposals and existing designs (dups never burn a GPU call).
The score maps to a verdict, and the verdict drives the gate per mode:

  verdict   when                                  action (by proposal_gate_mode)
  reject    score <  proposal_gate_reject_below   score_only: recorded only
                                                  auto_weed/full_auto: status='rejected'
  hold      in between                            always left 'pending' for a human
  approve   score >= proposal_gate_approve_above  full_auto only: status='approved' +
                                                  generations queued (same inserts as
                                                  the manual Approve path)

Settings (all rows in the `settings` table; UI in Settings → Store & Content):
  proposal_gate_enabled        "1"/"0"   scheduler automation master switch (default off)
  proposal_gate_mode           score_only | auto_weed | full_auto  (default auto_weed)
  proposal_gate_reject_below   int 0-100, default 40
  proposal_gate_approve_above  int 0-100, default 80
  proposal_gate_interval_min   minutes between scheduled batch reviews (default 360)
  proposal_gate_batch_size     max proposals judged per batch run (default 10, 0=unlimited)
                               — bounded bites instead of one endless task

Wiring: scheduler.py calls gate_tick() on its 30s loop (same pattern as the research
recheck); the manual endpoints live in routers/proposals.py. Every LLM call goes
through orch.submit_llm → _call_lmstudio, so the unified GPU queue stays the single
authority. Import-safe: module level touches only stdlib + db (deps/services are
imported lazily inside functions).
"""
import json
import logging
import re
import threading

from db import get_conn

log = logging.getLogger("store")

MODES = ("score_only", "auto_weed", "full_auto")

JUDGE_SYSTEM = """You are the product-line curator for a print-on-demand store (Etsy via Printify).
Score the proposed design concept 0-100, weighing equally:
- Marketability: would real shoppers search for and buy this on a shirt/mug/poster?
- Originality: a fresh, specific angle beats a generic cliché.
- Print-on-demand fit: works as bold, readable apparel art with a strong silhouette; not photo-dependent, not stale news-cycle noise.
- Policy safety: NO trademarked brands, celebrity names/likenesses, sports teams, logos, or copyrighted characters — any such concept must score below 30.
If a MARKET SIGNAL line is provided (live Etsy hot terms + our own listing performance), weigh alignment with that real demand into the marketability score.
Calibration: 80+ = strong, produce it now; 40-79 = maybe, needs a human look; below 40 = weed it out.
Return ONLY compact JSON, no markdown, no code fences, no explanation:
{"score": <0-100>, "reason": "<one short sentence, max 20 words>"}"""


# ── settings ──────────────────────────────────────────────────────────────────
def _setting(key, default):
    try:
        conn = get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        if row and row["value"] not in (None, ""):
            return row["value"]
    except Exception:
        pass
    return default


def _int_setting(key, default):
    try:
        return int(float(_setting(key, str(default))))
    except Exception:
        return default


def enabled() -> bool:
    return str(_setting("proposal_gate_enabled", "0")).strip().lower() in ("1", "true", "on", "yes")


def mode() -> str:
    m = str(_setting("proposal_gate_mode", "auto_weed")).strip()
    return m if m in MODES else "auto_weed"


def thresholds() -> tuple:
    """(reject_below, approve_above), sanity-clamped so approve can never sit
    below reject even with a bad manual settings edit."""
    lo = max(0, min(100, _int_setting("proposal_gate_reject_below", 40)))
    hi = max(0, min(100, _int_setting("proposal_gate_approve_above", 80)))
    return lo, max(hi, lo)


def batch_size() -> int:
    """Per-run cap on judged proposals (proposal_gate_batch_size, default 10;
    0 = unlimited). Bounds every batch — scheduled or manual — so a 100+
    backlog is chewed in bites instead of one endless LLM task."""
    return max(0, _int_setting("proposal_gate_batch_size", 10))


# ── dedup (local, no LLM) ─────────────────────────────────────────────────────
_WORD_RE = re.compile(r"[a-z0-9']+")


def _words(text) -> set:
    return set(_WORD_RE.findall((text or "").lower()))


def _near_duplicate(conn, row):
    """Cheap near-duplicate check, same heuristic family as the trend-scan insert
    dedup (services._run_trend_scan): 4+ shared title words with another LIVE
    proposal. The OLDER proposal always survives — only the newer row gets
    flagged, so a batch can never weed both halves of a pair. Also flags a
    proposal whose title is essentially contained in an existing design's prompt
    (the concept already made it through the funnel). Returns a human-readable
    match label, or None."""
    mine = _words(row["title"])
    for o in conn.execute(
        "SELECT id, title FROM proposals WHERE id<>? AND status IN ('pending','approved')",
        (row["id"],),
    ).fetchall():
        if o["id"] < row["id"] and len(mine & _words(o["title"])) >= 4:
            return f"proposal #{o['id']} “{(o['title'] or '')[:60]}”"
    strong = {w for w in mine if len(w) >= 4}
    if len(strong) >= 3:
        for d in conn.execute(
            "SELECT id, prompt FROM designs WHERE status IN ('review','approved','published')"
        ).fetchall():
            dwords = {w for w in _words(d["prompt"]) if len(w) >= 4}
            # near-total containment of the title's meaningful words = same concept
            if len(strong & dwords) >= max(3, len(strong) - 1):
                return f"design #{d['id']} (already generated)"
    return None


# ── the judge ─────────────────────────────────────────────────────────────────
def _judge_system() -> str:
    try:
        from deps import get_prompt
        return get_prompt("proposal_judge")
    except Exception:
        return JUDGE_SYSTEM


def _judge_llm(system: str, user: str) -> str:
    """Thin seam over the shared LM Studio helper (monkeypatched in tests).
    Must run inside an orchestrator LLM worker — same rule as every other call."""
    from deps import _call_lmstudio
    return _call_lmstudio(system, user, max_tokens=800)


def _parse_judge(raw):
    """Pull the LAST parseable {"score":…} object out of a possibly chatty reply.
    Returns (score 0-100 | None, reason str)."""
    score, reason = None, ""
    for cand in re.findall(r"\{[^{}]*\}", raw or "", re.S):
        try:
            d = json.loads(cand)
        except Exception:
            continue
        if "score" not in d:
            continue
        try:
            score = max(0, min(100, int(float(d["score"]))))
        except Exception:
            continue
        reason = str(d.get("reason", "") or "")[:300]
    return score, reason


def review_proposal_sync(proposal_id: int) -> dict:
    """Judge ONE proposal and apply the gate action for the current mode.
    Runs inside an orchestrator LLM worker (submit via submit_review /
    submit_review_all). Returns {proposal_id, score, verdict, reason, action}."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if not row:
        conn.close()
        return {"proposal_id": proposal_id, "error": "not found"}

    dup = _near_duplicate(conn, row)
    if dup:
        score, reason = 5, f"Near-duplicate of {dup}"
    else:
        lane = row["lane"] if "lane" in row.keys() else None
        concept = (
            f"TITLE: {row['title']}\n"
            f"DESCRIPTION: {row['description'] or '(none)'}\n"
            f"TAGS: {row['tags'] or '(none)'}\n"
            f"SOURCE: {row['source_label'] or row['source'] or 'manual'}"
            + (f"\nLANE: {lane}" if lane else "")
        )
        # Live Etsy demand context for the marketability axis (cached snapshot;
        # empty string when nothing is configured — never blocks the judge).
        try:
            import etsy_signal
            sig = etsy_signal.signal_text()
            if sig:
                concept += f"\nMARKET SIGNAL: {sig[:500]}"
        except Exception:
            pass
        try:
            raw = _judge_llm(_judge_system(), concept)
        except Exception:
            conn.close()
            raise                      # orchestrator marks the task failed; nothing written
        score, reason = _parse_judge(raw)
        if score is None:
            conn.close()
            return {"proposal_id": proposal_id,
                    "error": f"unparseable judge reply: {(raw or '')[:120]}"}

    lo, hi = thresholds()
    verdict = "reject" if score < lo else ("approve" if score >= hi else "hold")
    conn.execute(
        "UPDATE proposals SET score=?, score_reason=?, verdict=?, "
        "reviewed_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
        (score, reason, verdict, proposal_id),
    )
    conn.commit()
    action = _apply_gate(conn, row, verdict)
    conn.close()
    return {"proposal_id": proposal_id, "title": row["title"], "score": score,
            "verdict": verdict, "reason": reason, "action": action}


def _apply_gate(conn, row, verdict) -> str:
    """Act on a verdict per proposal_gate_mode. score_only never touches status;
    hold is always left pending for a human; auto-approve is full_auto ONLY."""
    m = mode()
    if m == "score_only":
        return "scored"
    if verdict == "reject":
        conn.execute("UPDATE proposals SET status='rejected',updated_at=datetime('now') WHERE id=?",
                     (row["id"],))
        conn.commit()
        return "auto_rejected"
    if verdict == "approve" and m == "full_auto":
        return _queue_generations(conn, row)
    return "held"


def _start_generation_async(gen_id: int):
    """What BackgroundTasks does for the manual approve path — a daemon thread
    running services.run_generation (which itself waits on orch.image_acquire).
    A seam so tests can stub the GPU out."""
    from services import run_generation
    threading.Thread(target=run_generation, args=(gen_id,), daemon=True,
                     name=f"gate-gen-{gen_id}").start()


def _queue_generations(conn, row, variations: int = 2) -> str:
    """Full-auto approve: same inserts as routers/proposals.approve_proposal,
    plus status='approved' so the funnel shows the proposal moved on."""
    from deps import _resolve_model
    model = _resolve_model(conn, None)
    ptype = ((row["tags"] or "T-Shirt").split(",")[0].strip()) or "T-Shirt"
    gen_ids = []
    for _ in range(max(1, variations)):
        cur = conn.execute(
            "INSERT INTO generations (proposal_id,prompt,product_type,model) VALUES (?,?,?,?)",
            (row["id"], row["title"], ptype, model),
        )
        gen_ids.append(cur.lastrowid)
    conn.execute("UPDATE proposals SET status='approved',updated_at=datetime('now') WHERE id=?",
                 (row["id"],))
    conn.commit()
    for gid in gen_ids:
        _start_generation_async(gid)
    return f"auto_approved:{len(gen_ids)}"


# ── batch + automation ────────────────────────────────────────────────────────
def _pending_unscored_ids() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM proposals WHERE status='pending' AND score IS NULL "
        "ORDER BY created_at DESC, id DESC"
    ).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def review_all_sync(limit: int = 0) -> dict:
    """Judge every pending, unscored proposal (one LLM call each) inside ONE
    orchestrator task. Each proposal commits as it finishes, so the UI can show
    scores landing incrementally while the batch runs."""
    ids = _pending_unscored_ids()
    if limit:
        ids = ids[:limit]
    out = {"reviewed": 0, "auto_rejected": 0, "auto_approved": 0, "held": 0,
           "errors": 0, "items": []}
    for pid in ids:
        try:
            r = review_proposal_sync(pid)
        except Exception as ex:
            log.warning("proposal gate: review %s failed: %s", pid, ex)
            out["errors"] += 1
            continue
        if r.get("error"):
            out["errors"] += 1
            continue
        out["reviewed"] += 1
        a = r.get("action", "")
        if a == "auto_rejected":
            out["auto_rejected"] += 1
        elif a.startswith("auto_approved"):
            out["auto_approved"] += 1
        else:
            out["held"] += 1
        out["items"].append({k: r.get(k) for k in ("proposal_id", "score", "verdict", "action")})
    log.info("proposal gate: batch done — %s", {k: out[k] for k in
             ("reviewed", "auto_rejected", "auto_approved", "held", "errors")})
    return out


def submit_review(proposal_id: int) -> int:
    """One-proposal judge as an orchestrator LLM task. Returns task_id
    (poll via /api/task/{id}). task="proposal_judge" so the Settings → Prompts
    per-task model picker applies."""
    from deps import orch
    return orch.submit_llm(
        lambda: review_proposal_sync(proposal_id),
        desc=f"Judge proposal {proposal_id}",
        task="proposal_judge",
    )


_batch_task_id = None      # last submitted batch, so we never stack a second one


def submit_review_all(priority: int = 1, limit: int = None) -> dict:
    """Submit ONE orchestrator task that judges pending unscored proposals, up
    to `limit` (None → the proposal_gate_batch_size setting, default 10; 0 =
    unlimited). Returns {task_id, queued, pending}; refuses to stack while a
    batch is still going."""
    global _batch_task_id
    from deps import orch
    if _batch_task_id is not None:
        try:
            st = orch.poll(_batch_task_id)
        except Exception:
            st = {}
        if st.get("status") in ("pending", "running"):
            return {"task_id": _batch_task_id, "queued": 0, "already_running": True}
    cap = batch_size() if limit is None else max(0, int(limit))
    n = len(_pending_unscored_ids())
    if n == 0:
        return {"task_id": None, "queued": 0}
    queued = min(n, cap) if cap else n
    _batch_task_id = orch.submit_llm(
        lambda: review_all_sync(limit=cap),
        desc=f"Proposal gate: judge {queued} of {n} pending",
        priority=priority,
        task="proposal_judge",
    )
    return {"task_id": _batch_task_id, "queued": queued, "pending": n}


def gate_tick() -> dict:
    """Scheduler entry (scheduler.py, every proposal_gate_interval_min).
    Submits ONE bounded batch (proposal_gate_batch_size) at background priority
    and returns immediately — the backlog drains a bite per interval."""
    if not enabled():
        return {"skipped": "disabled"}
    r = submit_review_all(priority=2)
    if not r.get("queued") and not r.get("already_running"):
        return {"skipped": "nothing pending"}
    try:
        import time as _t
        conn = get_conn()
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                     ("proposal_gate_last_run", str(_t.time())))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return r

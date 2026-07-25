"""THE COMPANY — general AI security (Phase-2 chunk 2).

Turns the Pi-hole-only security into a whole-store security desk: every large
subsystem has a LOG that gets scanned for trouble (failures, errors, stuck jobs),
every agent is assigned a BEAT (a system to watch) so there's always real work,
and when a raid fires a real scan runs — reviewing logs + Pi-hole activity — with
an actual MODEL review of anything suspicious submitted to the orchestrator queue
(so model work never fights image/video gen on the GPU).

Real analysis of real rows — no fabrication. Findings persist to security_findings
so they also become raid "bug" monsters (system D).
"""
import json
import logging
import time

from world_defs import mget, mset, run_llm_job, log_town

log = logging.getLogger("store")


# ── the REAL Command Center, game-readable ────────────────────────────────────
# The store's actual security stack (secaudit snapshots, security_events, the 14
# defenses in defense.py, live attackers) is the town's true threat model. The
# heavy probes run on the ticker (refresh_real_posture) and land in world_meta;
# real_posture() is the instant read every state poll uses.
def refresh_real_posture(c):
    out = {"grade": None, "score": None, "shield": None, "on": 0, "total": 0,
           "warn": [], "events_24h": 0, "attackers": 0, "at": time.time()}
    try:
        r = c.execute("SELECT score, grade FROM security_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if r:
            out["score"], out["grade"] = r["score"], r["grade"]
        out["events_24h"] = int(c.execute(
            "SELECT COUNT(*) FROM security_events WHERE created_at > datetime('now','-1 day')"
        ).fetchone()[0] or 0)
    except Exception:
        pass
    try:
        import defense
        from cache import cached
        # Long TTL + own key: the game's threat model only needs a coarse read, and this
        # runs on the background ticker — a short TTL here re-probes the network (incl. the
        # LM Studio /v1/models auth check) every couple of minutes forever, spamming logs.
        # The frontend security tab keeps its own fresh "sec:defenses" (45s) cache.
        # 30-min TTL: the game shield doesn't need fresher, and this is the only
        # BACKGROUND caller — a shorter TTL just re-probes the node (incl. LM Studio
        # /v1/models) and spams its log.
        d = cached("sec:defenses-world", 1800, defense.defenses)
        out["total"] = len(d["defenses"])
        out["on"] = d["counts"].get("on", 0)
        out["warn"] = [x["name"] for x in d["defenses"] if x["status"] == "warn"]
        out["shield"] = round(out["on"] / out["total"], 2) if out["total"] else None
    except Exception as ex:
        log.info("[world] posture defenses read failed: %s", ex)
    try:
        import secaudit
        from cache import cached
        out["attackers"] = int(cached("sec:threats-lite", 300, secaudit.threats).get("count", 0))
    except Exception:
        pass
    mset(c, "real_posture", json.dumps(out))
    return out


def real_posture(c):
    """Instant read of the last refreshed posture (ticker keeps it ≤2 min stale)."""
    try:
        v = mget(c, "real_posture", None)
        return json.loads(v) if v else {}
    except Exception:
        return {}

# store subsystems that have a scannable log, in priority order.
# fail  = the status value that means "broken" for that table.
SYSTEMS = {
    "image":    {"label": "Image Studio",  "table": "generations",      "dept": "image",    "fail": "failed"},
    "video":    {"label": "Video Studio",  "table": "videos",           "dept": "video",    "fail": "failed"},
    "audio":    {"label": "Audio Studio",  "table": "audio_clips",      "dept": "audio",    "fail": "failed"},
    "models3d": {"label": "3D Studio",     "table": "models3d",         "dept": "models3d", "fail": "error"},
    "resell":   {"label": "Resell Ops",    "table": "automation_log",   "dept": "resell",   "fail": "failed"},
    "netsec":   {"label": "Network Sec",   "table": "security_findings", "dept": "netsec",   "fail": "pending"},
}
SYS_KEYS = list(SYSTEMS.keys())


def _ensure(c):
    c.execute("""CREATE TABLE IF NOT EXISTS world_beats(
        agent_key TEXT PRIMARY KEY, system TEXT, assigned_at TEXT DEFAULT (datetime('now')))""")


def _count(c, sql, args=()):
    try:
        return int(c.execute(sql, args).fetchone()[0] or 0)
    except Exception:
        return 0


# ── scan one subsystem's recent log for trouble ──
def _scan_one(c, key):
    s = SYSTEMS[key]
    tbl, fail = s["table"], s["fail"]
    recent = _count(c, f"SELECT COUNT(*) FROM {tbl} WHERE created_at > datetime('now','-1 day')")
    failed = _count(c, f"SELECT COUNT(*) FROM {tbl} WHERE status=? AND created_at > datetime('now','-1 day')", (fail,))
    stuck = 0
    if tbl == "automation_log":
        stuck = _count(c, "SELECT COUNT(*) FROM automation_log WHERE status='running' AND created_at < datetime('now','-30 minutes')")
    issues = failed + stuck
    health = "critical" if issues >= 3 else ("warn" if issues else "ok")
    # a representative issue line (real row) for the debugging task
    sample = None
    if issues:
        try:
            r = c.execute(f"SELECT * FROM {tbl} WHERE status=? ORDER BY created_at DESC LIMIT 1", (fail,)).fetchone()
            if r:
                rd = dict(r)
                sample = rd.get("error") or rd.get("publish_error") or rd.get("issue") or f"{fail} job #{rd.get('id')}"
        except Exception:
            pass
    return {"key": key, "label": s["label"], "dept": s["dept"], "recent": recent,
            "issues": issues, "failed": failed, "stuck": stuck, "health": health,
            "sample": (sample or "")[:80]}


def scan_systems(c):
    return {k: _scan_one(c, k) for k in SYS_KEYS}


# ── beats: every agent watches a system (so there's always work) ──
def assign_beats(c, agents):
    """Give each agent a beat — prefer their own department's system, else spread."""
    _ensure(c)
    have = {r[0]: r[1] for r in c.execute("SELECT agent_key, system FROM world_beats").fetchall()}
    dept2sys = {v["dept"]: k for k, v in SYSTEMS.items()}
    for i, a in enumerate(agents):
        if a["key"] in have:
            continue
        sys = dept2sys.get(a.get("dept")) or SYS_KEYS[i % len(SYS_KEYS)]
        c.execute("INSERT OR REPLACE INTO world_beats(agent_key, system) VALUES(?,?)", (a["key"], sys))
        have[a["key"]] = sys
    return have


def agent_beat(c, key):
    _ensure(c)
    r = c.execute("SELECT system FROM world_beats WHERE agent_key=?", (key,)).fetchone()
    return r[0] if r else None


def agent_tasks(c, key):
    """How many open debugging tasks this agent has on their beat (real issue count)."""
    sys = agent_beat(c, key)
    if not sys:
        return {"system": None, "label": None, "tasks": 0, "health": "ok"}
    h = _scan_one(c, sys)
    return {"system": sys, "label": h["label"], "tasks": h["issues"], "health": h["health"], "sample": h["sample"]}


def _event(c, kind, text):
    try:
        c.execute("INSERT INTO world_events (agent_key, kind, text) VALUES (?,?,?)", ("", kind, text))
    except Exception:
        pass


# ── the real scan: analyse every log + Pi-hole, persist findings, queue a model review ──
def run_security_scan(c, verbose=True, llm_review=True):
    """Returns {systems, total_issues, pihole_blocked}. Persists anomalies to
    security_findings (so they become raid monsters) and queues an LLM review."""
    _ensure(c)
    health = scan_systems(c)
    total = 0
    suspicious = []
    for k, h in health.items():
        if verbose:
            _event(c, "security", f"🔍 Scanning {h['label']} log… {h['recent']} recent, {h['issues']} issue(s).")
        if h["issues"]:
            total += h["issues"]
            suspicious.append(f"{h['label']}: {h['issues']} failing ({h['sample']})")
            _persist_finding(c, f"{k}:{h['sample']}", f"{h['label']} — {h['sample']}",
                             "High" if h["health"] == "critical" else "Medium")
    # Pi-hole review
    blocked = 0
    try:
        import pihole
        if pihole.configured():
            doms = {}
            for q in pihole.get_queries(200):
                if q.get("blocked") and q.get("domain"):
                    doms[q["domain"]] = doms.get(q["domain"], 0) + 1
            blocked = sum(doms.values())
            top = sorted(doms.items(), key=lambda kv: -kv[1])[:5]
            if verbose:
                _event(c, "security", f"🔍 Reviewing Pi-hole: {blocked} blocked queries, {len(doms)} suspect domains.")
            for dom, n in top:
                suspicious.append(f"Pi-hole blocked {dom} ×{n}")
    except Exception:
        pass
    # The REAL security stack joins the sweep: unseen audit alerts become findings
    # (→ raid monsters that, once defeated, acknowledge the alert), and the posture
    # (grade + weakened defenses + live attackers) briefs the AI reviewer.
    posture = real_posture(c)
    try:
        rows = c.execute("SELECT id, severity, text FROM security_events WHERE seen=0 "
                         "AND created_at > datetime('now','-1 day') ORDER BY id DESC LIMIT 4").fetchall()
        for r in rows:
            suspicious.append(f"Audit alert [{r['severity']}]: {r['text']}")
            if r["severity"] in ("high", "critical"):
                total += 1
                _persist_finding(c, f"event:{r['id']}", (r["text"] or "audit regression")[:180], "High")
        if rows and verbose:
            _event(c, "security", f"🔍 Reviewing audit alerts: {len(rows)} unacknowledged.")
    except Exception:
        pass
    if posture.get("warn"):
        suspicious.append("Defenses needing attention: " + ", ".join(posture["warn"][:4]))
    if posture.get("attackers"):
        suspicious.append(f"{posture['attackers']} live attacker(s) probing the perimeter (see Network Security → Threats)")
    grade_note = f", posture {posture['grade']} ({posture['score']})" if posture.get("grade") else ""
    log_town(f"Security scan: {total} log issue(s), {blocked} blocked DNS queries across {len(health)} systems{grade_note}.")
    # 😈 Satan red-team pass alongside the constructive review: a worst-case
    # read of the same findings (world_satan.security_redteam). No-op while
    # world_satan_enabled is OFF (the default); read-only beyond its own log
    # lines; a failure here never blocks the scan.
    try:
        import world_satan
        world_satan.security_redteam(c, suspicious, posture)
    except Exception:
        log.exception("satan security red-team pass failed")
    if llm_review:
        # ALWAYS run the model review on a scan — even a clean system gets an AI
        # posture read ("all nominal"), so a drill always shows a model reviewing.
        _queue_llm_review(c, suspicious or [
            f"Routine security sweep: {len(health)} subsystems scanned, {total} log issue(s), "
            f"{blocked} blocked DNS queries. No critical anomalies — confirm the posture is nominal."])
    return {"systems": health, "total_issues": total, "pihole_blocked": blocked}


def _persist_finding(c, fkey, issue, priority):
    try:
        c.execute("""INSERT INTO security_findings(fkey, issue, priority, status)
            VALUES(?,?,?, 'pending') ON CONFLICT(fkey) DO UPDATE SET updated_at=datetime('now')""",
                  (fkey[:120], issue[:200], priority))
    except Exception:
        pass


# ── NON-COMBAT remediation: the NetSec desk works its own backlog ─────────────
# The only path that ever flipped a finding to 'remediated' was combat in a raid
# (world_raid._defeat), and raids fire only a few times/day on UNRELATED triggers
# — so findings piled up faster than raids could ever clear them (queue
# starvation). LOW/MEDIUM findings don't need a raid: on the normal work cadence
# (world_work._wg_operate, dept="netsec"), the netsec agent closes a small batch
# of the OLDEST pending low/med findings — FIFO, same ordering fix as the raid's
# scan_threats(). HIGH/CRITICAL findings are left for combat (they're the ones
# that spawn as raid monsters). Throttled so the backlog drains steadily instead
# of being wiped in one tick.
NETSEC_REMEDIATE_BATCH = 3            # oldest low/med findings closed per non-combat pass
NETSEC_REMEDIATE_COOLDOWN_SEC = 120   # min gap between passes (ticker runs every few sec)


def remediate_backlog(c, agent=None, batch=NETSEC_REMEDIATE_BATCH):
    """Close a small FIFO batch of oldest pending LOW/MEDIUM findings — no raid
    needed. Returns the number remediated (0 if throttled or nothing to do)."""
    now = time.time()
    last = float(mget(c, "netsec_remediate_t", 0) or 0)
    if last and now - last < NETSEC_REMEDIATE_COOLDOWN_SEC:
        return 0
    mset(c, "netsec_remediate_t", now)
    rows = c.execute(
        "SELECT id, issue FROM security_findings WHERE status='pending' "
        "AND priority IN ('Low','Medium') ORDER BY id ASC LIMIT ?", (batch,)).fetchall()
    if not rows:
        return 0
    name = (agent or {}).get("name") or "NetSec"
    key = (agent or {}).get("key") or ""
    for r in rows:
        c.execute("UPDATE security_findings SET status='remediated', updated_at=datetime('now') WHERE id=?",
                  (r["id"],))
        _event(c, "security", f"🛠️ {name} patched: {(r['issue'] or 'finding')[:70]}.")
    try:
        import world_skills as WS
        WS.add_xp(c, key, "knowledge", 12 * len(rows))
    except Exception:
        pass
    log_town(f"NetSec: {name} closed {len(rows)} low/med finding(s) off the backlog (non-combat).")
    return len(rows)


def _sec_model():
    """The configured security-analyst model (Settings → 🧠 Models) — passing it makes
    the orchestrator LOAD it in LM Studio (verified resident) before the review, so a
    model actually runs. Falls back to the global Text LLM when unset."""
    try:
        import model_registry
        return model_registry.resolve("security_model") or None
    except Exception:
        return None


def _queue_llm_review(c, suspicious):
    """A real model reviews the suspicious activity on the orchestrator queue (the orch
    LOADS the store model first, so it shows up working in LM Studio). The 'analysing'
    note is written on the CALLER's cursor `c` (same transaction — opening a second
    connection here dead-locks SQLite while the caller's write txn is open). The async
    job runs after commit, so it safely opens its own connection."""
    from deps import get_conn, _call_lmstudio, orch
    items = "\n".join(f"- {s}" for s in suspicious[:8])
    system = "You are a SOC analyst for a small self-hosted studio. Given anomalies, reply in ONE terse sentence: the top risk + one action. If nothing is critical, say so. No preamble."
    prompt = f"Security scan results:\n{items}\n\nTop risk and recommended action (one sentence):"
    model = _sec_model()
    _event(c, "security", f"🧠 Security AI is reviewing {len(suspicious)} item(s)…")

    def _job():
        verdict, real = None, False
        try:
            line = (orch.llm_borrow(lambda: _call_lmstudio(system, prompt, 60)) or "").strip().split("\n")[0]
            if line and len(line.split()) >= 4:
                verdict, real = line[:180], True
        except Exception as ex:
            log.warning("[world] security review LLM call failed: %s", ex)
            verdict = None
        if not verdict:
            verdict = f"{len(suspicious)} anomalies flagged — prioritise the failing subsystems and review the blocked domains."
        tag = "" if real else " (offline)"
        c2 = get_conn()
        try:
            c2.execute("INSERT INTO world_events (agent_key, kind, text) VALUES (?,?,?)",
                       ("", "security", f"🧠 Security AI{tag}: {verdict}"))
            cur = c2.cursor()
            mset(cur, "sec_last_verdict", f"{verdict}{tag}")     # persistent (survives feed pruning) → raid HUD
            c2.commit()
        finally:
            c2.close()
        log.info("[world] security review done (real=%s): %s", real, verdict[:80])
        return {"verdict": verdict, "real": real}

    log.info("[world] queued security review of %d anomalies (model=%s)", len(suspicious), model)
    # Ask the orchestrator to make the store model resident for the review, so it's
    # visibly loaded/working in LM Studio. If the GPU is busy (ComfyUI / another model)
    # the load fails and the job degrades to an offline posture read — never blocks.
    run_llm_job(_job, "world:security-review", wait=0, model=model)

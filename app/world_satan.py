"""
The Company — "Satan": the owner's OPTIONAL adversarial lieutenant (red team).

The mirror of "Jesus" (world_jesus.py). Where Jesus is the CONSTRUCTIVE,
best-case delegate of the owner's operator seat, Satan is the ADVERSARIAL,
worst-case one: the devil on the other shoulder. Together they give every
prediction/review/forecast a hi-lo band (world_duality.py) so the owner and
the agents reason across a RANGE — best case AND worst case — instead of a
single optimistic number. Satan is the risk predictor.

  • ONE toggle, world_satan_enabled, DEFAULT OFF. Only the owner flips it.
    Off ⇒ Satan is completely inert (every entry point checks it, even the
    manual "act now" endpoint).
  • When ON, on a throttled loop (world_satan_interval_min, one action per
    tick, riding the existing world_auto daemon within the same active hours),
    Satan performs the ADVERSARIAL half of the operator's routine:
      (a) invoke ONLY capabilities the owner has ALREADY ENABLED — via
          world_caps.invoke(actor="satan"), gated EXACTLY like an agent
          (catalog master + per-cap gate), so the delegation adds ZERO
          privilege. Satan never flips a gate: this module only ever writes
          its own settings keys and its own ledger.
      (b) make the routine ADVERSARIAL calls on pending prayers: REJECT when
          both leaders blocked it (the same reject half of the owner's routine
          bar Jesus uses — Satan NEVER approves anything: blessing is the
          constructive side's job, and the approval gate is unchanged), and
          FLAG (read-only ledger entry, no state change) the risks he finds
          on everything else — cost, irreversibility, low taste, blocked
          endorsements. Predict and red-team; never execute.

THE HARD SAFETY FLOOR IS UNCHANGED and applies to Satan exactly as to every
other actor — and Satan cannot grant himself an exception:
  • Real money NEVER moves through Satan: he refuses every prayer in
    world_ops.ALWAYS_GATE (payouts, wallet sends, secret export — gates that
    are not even toggleable for anyone) AND any prayer that costs real cents.
    Satan may PREDICT/red-team a money action; he can never execute one —
    there is no approve path in this module at all.
  • add_software prayers stay human: approved code could alter the floor.
  • THE ILLEGAL-CONTENT FLOOR IS NOT SATAN'S TO TOUCH: the minors/CSAM
    detection and the real-person deepfake/NCII detection (nsfw/aishield at
    the generation endpoints) are ALWAYS-ON, NOT toggleable, NOT under
    Satan's (or anyone's) control, and are enforced against Satan identically
    to every other actor. Nothing here ever sets nsfw=true (world_caps
    guarantees that for every cap), and the world_satan_nsfw_domain gate
    below explicitly does NOT include — and cannot reach — those floors.
  • Satan's verdicts are stamped "auto (Satan): …" so world_ops._learn_verdict
    skips them — only REAL human verdicts train the taste model.

Every action/decision lands in world_satan_ledger, surfaced in the God Panel
(😈 Satan tab) — fully auditable.

Two OPTIONAL extras, both DEFAULT OFF:
  • world_satan_nsfw_domain — when the (separate, layered) NSFW mode is
    active AND the owner flips this gate ON, the OVERSIGHT/REVIEW of the
    private studio's output becomes Satan's domain: he audits recent NSFW
    generation rows (counts, failures, rejections) into his ledger as
    read-only risk notes. He reviews; he never generates, never approves,
    and the always-on minors/CSAM + real-person/NCII floors sit UNDER this
    gate untouched — they refuse content for every actor regardless of any
    toggle, this one included. DEFAULT OFF: Satan does not touch NSFW.
  • world_satan_shadow — while Satan is DISABLED, an observe-only shadow
    records what he WOULD have rejected/flagged/invoked (ledger rows with
    shadow=1). It takes no action ever — a read-only "what Satan would do"
    stream for the owner.
"""
import json
import logging
import random
import time

from deps import get_conn, get_setting

logger = logging.getLogger("store")

ENABLED_KEY = "world_satan_enabled"          # DEFAULT OFF — only the owner turns it on
INTERVAL_KEY = "world_satan_interval_min"
LAST_KEY = "world_satan_last"
INTERVAL_DEFAULT = 30                        # at most one adversarial action / 30 min
# NSFW-domain gate (DEFAULT OFF): route private-studio OVERSIGHT/review to
# Satan. Review-only. The always-on illegal-content floors (minors/CSAM +
# real-person deepfake/NCII) are NOT part of this gate and stay enforced for
# every actor regardless — this key cannot weaken, bypass or reach them.
NSFW_DOMAIN_KEY = "world_satan_nsfw_domain"
# SHADOW mode: while Satan is DISABLED, record what he WOULD have done into a
# read-only shadow stream (world_satan_ledger rows with shadow=1) — observe
# only, takes NO action ever. DEFAULT OFF.
SHADOW_KEY = "world_satan_shadow"
SHADOW_LAST_KEY = "world_satan_shadow_last"


# ── settings io ──────────────────────────────────────────────────────────────
def _truthy(v):
    return str(v).lower() in ("1", "true", "yes", "on")


def _set(k, v):
    c = get_conn()
    try:
        c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, str(v)))
        c.commit()
    finally:
        c.close()


def enabled():
    return _truthy(get_setting(ENABLED_KEY, "0"))


def nsfw_domain_on():
    return _truthy(get_setting(NSFW_DOMAIN_KEY, "0"))


def shadow_on():
    return _truthy(get_setting(SHADOW_KEY, "0"))


def interval_min():
    try:
        return max(5, int(get_setting(INTERVAL_KEY, str(INTERVAL_DEFAULT))))
    except Exception:
        return INTERVAL_DEFAULT


def _last():
    try:
        return float(get_setting(LAST_KEY, "0") or 0)
    except Exception:
        return 0.0


def _shadow_last():
    try:
        return float(get_setting(SHADOW_LAST_KEY, "0") or 0)
    except Exception:
        return 0.0


# ── the auditable ledger ─────────────────────────────────────────────────────
def ensure(conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS world_satan_ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action   TEXT NOT NULL,          -- prayer | capability | risk | nsfw_review | refused
            kind     TEXT,                   -- prayer kind / capability id / risk key
            target   TEXT,                   -- '#12 title…' / cap label
            decision TEXT,                   -- reject | flag | invoke | review | refused
            detail   TEXT,
            created_at TEXT DEFAULT (datetime('now')))""")
        # shadow=1 rows are OBSERVE-ONLY "what Satan WOULD do" entries recorded
        # while the toggle is OFF (world_satan_shadow) — never actions taken.
        try:
            conn.execute("ALTER TABLE world_satan_ledger ADD COLUMN shadow INTEGER DEFAULT 0")
        except Exception:
            pass
        conn.commit()
    finally:
        if own:
            conn.close()


def _log(conn, action, kind=None, target=None, decision=None, detail=None, shadow=0):
    conn.execute("INSERT INTO world_satan_ledger (action,kind,target,decision,detail,shadow) "
                 "VALUES (?,?,?,?,?,?)",
                 (action, kind, (target or "")[:160], decision, (detail or "")[:300],
                  1 if shadow else 0))
    conn.commit()


# ── the unchanged hard floor, as seen from the adversary's side ──────────────
def skip_kinds():
    """Prayer kinds the adversary may NEVER action (the human-only floor) —
    identical to Jesus's floor: ALWAYS_GATE money/secret kinds + code."""
    import world_ops as wo
    return set(wo.ALWAYS_GATE) | {"add_software"}


def _floor_blocked(p):
    """Why this prayer is off-limits to Satan (None = actionable). Satan may
    still FLAG floor-blocked prayers (prediction), but never touch them."""
    if p["kind"] in skip_kinds():
        return "human-only kind (real money / secrets / code)"
    if int(p["cost_cents"] or 0) > 0:
        return "costs real money — spend needs an explicit human"
    return None


# ── (a) the adversarial routine on pending prayers ───────────────────────────
def _verdict_for(conn, p):
    """Satan's ADVERSARIAL routine call on ONE pending prayer — the inverse
    polarity of Jesus's constructive call:
      ('reject', why)  when both leaders blocked it (the same reject half of
                       the owner's routine bar — never a new standard),
      ('flag', risks)  when his worst-case checklist finds real downside,
      None             when even the red team finds nothing (leave it be).
    Satan NEVER returns 'approve' — blessing is not in his vocabulary, so the
    approval gate is structurally unchanged. Shared by the REAL tick and the
    observe-only shadow so both apply the same bar."""
    import world_duality
    import world_ops as wo
    taste, bok, mok = p["taste"], p["boss_ok"], p["mayor_ok"]
    if taste is None:                              # filed before endorsements ran
        taste, bok, mok = wo._endorse(conn, p["id"])
        p = dict(p, taste=taste, boss_ok=(1 if bok else 0), mayor_ok=(1 if mok else 0))
    bok = 1 if p["boss_ok"] else 0 if p["boss_ok"] is not None else None
    mok = 1 if p["mayor_ok"] else 0 if p["mayor_ok"] is not None else None
    if bok == 0 and mok == 0:
        return "reject", f"both leaders blocked it · {p.get('endorse_note') or ''}".strip(" ·")
    risks = world_duality._prayer_risks(p)
    if risks:
        return "flag", "; ".join(risks)[:280]
    return None


def _action_prayer(conn, prayer_id=None):
    """ONE adversarial pass over pending prayers: REJECT the first one both
    leaders blocked (stamped "auto (Satan): …" so the taste model ignores it),
    else FLAG the first unflagged risky one (read-only ledger note — the
    prayer itself is untouched and still waits for the human). Floor-blocked
    prayers are never resolved by Satan — at most flagged. Returns an action
    dict or None."""
    import world_ops as wo
    wo.ensure(conn)
    if prayer_id:
        rows = conn.execute("SELECT * FROM world_prayers WHERE id=? AND status='pending'",
                            (int(prayer_id),)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM world_prayers WHERE status='pending' "
                            "ORDER BY id ASC LIMIT 25").fetchall()
    flag_candidate = None
    for r in rows:
        p = dict(r)
        blocked = _floor_blocked(p)
        v = _verdict_for(conn, p)
        if not v:
            continue
        decision, why = v
        if decision == "reject" and not blocked:
            # the one state change Satan may make: the routine REJECT the owner
            # would make anyway (both leaders blocked). Never an approve.
            res = wo._resolve(conn, p["id"], approve=False,
                              god_comment=f"auto (Satan): {why}")
            _log(conn, "prayer", kind=p["kind"], target=f"#{p['id']} {p['title']}",
                 decision="reject", detail=why)
            return {"action": "prayer", "prayer_id": p["id"], "decision": "reject",
                    "title": p["title"], "why": why,
                    "status": (res or {}).get("status")}
        if flag_candidate is None:
            # flag (read-only) — includes floor-blocked prayers: Satan may
            # PREDICT a money action's risk, never execute or resolve it.
            why = f"{why} · {blocked}" if (blocked and decision == "reject") else why
            flag_candidate = (p, why)
    if flag_candidate:
        p, why = flag_candidate
        if not conn.execute("SELECT 1 FROM world_satan_ledger WHERE action='risk' "
                            "AND target LIKE ?", (f"#{p['id']} %",)).fetchone():
            _log(conn, "risk", kind=p["kind"], target=f"#{p['id']} {p['title']}",
                 decision="flag", detail=why)
            return {"action": "risk", "prayer_id": p["id"], "decision": "flag",
                    "title": p["title"], "why": why}
    return None


# ── (b) pressing an already-enabled capability button ────────────────────────
def _cap_pool(wc):
    """The capability pool Satan may draw from: the SAME routine agent_safe set
    the agent auto-loop and Jesus use (free, local, non-publishing,
    non-spending, args auto-buildable) — nothing more. Satan gets NO
    publishing extension (that's the constructive side's optional extra);
    his natural draw is the audit/scan flavor of the same pool."""
    return [c for c in wc.CATALOG
            if c.get("agent_safe") and wc.gate_on(c)
            and (not c.get("args") or c["id"] in wc.AUTO_ARGS)]


def _action_cap(conn, cap_id=None):
    """Invoke ONE capability the owner has already enabled. Every invocation
    goes through world_caps.invoke(actor='satan'), which enforces the catalog
    master + per-cap gate exactly like an agent — Satan never enables
    anything and never rides per-agent grants."""
    import world_caps as wc
    if cap_id:
        cap = wc._CAP.get(cap_id)
        if not cap:
            _log(conn, "refused", kind=cap_id, decision="refused", detail="unknown capability")
            return None
        pool = [cap]
    else:
        pool = _cap_pool(wc)
    if not pool:
        return None
    if wc._content_busy(conn):
        return None                                    # the single-3060 guard wins
    cap = random.choice(pool)
    args = {}
    if cap.get("args"):
        if cap["id"] not in wc.AUTO_ARGS:
            _log(conn, "refused", kind=cap["id"], target=cap["label"], decision="refused",
                 detail="capability needs arguments Satan cannot auto-build")
            return None
        try:
            args = wc.AUTO_ARGS[cap["id"]](conn) or {}
        except Exception:
            logger.exception("satan auto-args failed for %s", cap["id"])
    res = wc.invoke(cap["id"], args, actor="satan", agent="Satan")
    if not res.get("ok"):
        # the gate said no (e.g. a cap whose gate is OFF) — audit the refusal
        _log(conn, "refused", kind=cap["id"], target=cap["label"], decision="refused",
             detail=res.get("error") or "refused")
        return None
    _log(conn, "capability", kind=cap["id"], target=cap["label"], decision="invoke",
         detail=json.dumps({k: str(v)[:80] for k, v in args.items()}) if args else "")
    try:
        import world_ops as wo
        # display string only (naming_theme): themed default is the exact
        # current line; from_agent stays "Satan" — it's the audited actor id.
        import naming_theme as _nt
        wo.note(f"{_nt.display('satan')} (red-teaming for {_nt.label('god')}) "
                f"triggered: {cap['label']}",
                kind="info", from_agent="Satan", conn=conn)
    except Exception:
        pass
    return {"action": "capability", "cap_id": cap["id"], "label": cap["label"],
            "run_id": res.get("run_id"), "args": args}


# ── (c) NSFW-domain OVERSIGHT (gated, default OFF, review-only) ──────────────
def _action_nsfw_review(conn):
    """When (and only when) the layered NSFW mode is active AND the owner
    flipped world_satan_nsfw_domain ON: one read-only oversight review of the
    private studio's recent output — counts + failure/rejection rates into
    Satan's ledger. Review only: no generation, no approval, no file access.
    THE ALWAYS-ON FLOORS ARE OUTSIDE THIS GATE: the minors/CSAM and
    real-person deepfake/NCII detection run at the generation endpoints for
    every actor regardless of this (or any) toggle — this review can only
    OBSERVE their results, never alter them."""
    if not nsfw_domain_on():
        return None
    try:
        import nsfw
        if not nsfw.world_active():
            return None
    except Exception:
        return None
    try:
        row = conn.execute(
            "SELECT COUNT(*) n, "
            "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed, "
            "SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) rejected "
            "FROM generations WHERE COALESCE(nsfw,0)=1 "
            "AND created_at > datetime('now','-1 day')").fetchone()
    except Exception:
        return None
    n = int(row["n"] or 0)
    if not n:
        return None
    if conn.execute("SELECT 1 FROM world_satan_ledger WHERE action='nsfw_review' "
                    "AND created_at > datetime('now','-6 hours')").fetchone():
        return None                                    # reviewed recently — don't spam
    failed, rejected = int(row["failed"] or 0), int(row["rejected"] or 0)
    detail = (f"{n} private-studio piece(s) in 24h · {failed} failed · {rejected} rejected. "
              "Always-on floors (minors/CSAM + real-person/NCII) enforced at the endpoints "
              "for every actor — outside this gate, not toggleable.")
    _log(conn, "nsfw_review", kind="oversight", target="private studio (24h)",
         decision="review", detail=detail)
    return {"action": "nsfw_review", "detail": detail}


# ── the tick ─────────────────────────────────────────────────────────────────
def tick(force=False, action=None, cap_id=None, prayer_id=None):
    """ONE adversarial action. force=True (the owner's 'act now' button) skips
    the interval + active-hours throttles but NEVER the enabled toggle or the
    company master pause. `action`/`cap_id`/`prayer_id` let the owner steer a
    controlled single action; every gate still applies."""
    if not enabled():
        return {"ok": False, "skipped": "Satan is disabled (world_satan_enabled is off)"}
    try:
        import world_control
        if not world_control.master_on():
            return {"ok": False, "skipped": "company master is paused"}
    except Exception:
        pass
    if not force:
        try:
            import world_auto
            if not world_auto._active_now():
                return {"ok": False, "skipped": "outside active hours"}
        except Exception:
            pass
        if time.time() - _last() < interval_min() * 60:
            return {"ok": False, "skipped": "interval not elapsed"}
    _set(LAST_KEY, str(time.time()))
    conn = get_conn()
    try:
        ensure(conn)
        act = None
        if action == "cap":
            act = _action_cap(conn, cap_id=cap_id)
        elif action == "prayer":
            act = _action_prayer(conn, prayer_id=prayer_id)
        elif action == "nsfw_review":
            act = _action_nsfw_review(conn)
        else:
            act = _action_prayer(conn) or _action_nsfw_review(conn) or _action_cap(conn)
        return {"ok": True, "acted": bool(act), "did": act}
    finally:
        conn.close()


def maybe_tick(now=None):
    """Rides the existing world_auto daemon (~1/min, inside active hours) —
    no second loop. No-op unless the owner's toggle is on and the interval
    has elapsed. While the toggle is OFF, the (separately toggled, default
    OFF) SHADOW observer may record what Satan WOULD have done — observe
    only, it never takes an action."""
    if not enabled():
        try:
            return shadow_tick(now)                # observe-only; acts on nothing
        except Exception:
            logger.exception("world_satan shadow tick failed")
            return None
    now = now or time.time()
    if now - _last() < interval_min() * 60:
        return None
    try:
        return tick(force=False)
    except Exception:
        logger.exception("world_satan tick failed")
        return None


# ── SHADOW: the read-only "what Satan would do" stream ───────────────────────
# Runs ONLY while Satan is DISABLED and world_satan_shadow is ON (default
# OFF). Re-uses the exact same adversarial bar (_verdict_for) and cap pool as
# the real tick, but takes NO action: no _resolve, no invoke, no note — every
# would-be decision lands in world_satan_ledger with shadow=1.
def shadow_tick(now=None):
    if enabled() or not shadow_on():
        return None
    now = now or time.time()
    if now - _shadow_last() < interval_min() * 60:
        return None
    _set(SHADOW_LAST_KEY, str(now))
    conn = get_conn()
    try:
        ensure(conn)
        logged = _shadow_prayers(conn)
        if not logged:
            _shadow_cap(conn)
        return {"ok": True, "shadow": True, "logged": logged}
    finally:
        conn.close()


def _shadow_prayers(conn, limit=5):
    """Record would-reject / would-flag for pending prayers (same floor + same
    adversarial bar as the real tick), once per prayer."""
    import world_ops as wo
    wo.ensure(conn)
    logged = 0
    for r in conn.execute("SELECT * FROM world_prayers WHERE status='pending' "
                          "ORDER BY id ASC LIMIT 25").fetchall():
        if logged >= limit:
            break
        p = dict(r)
        if conn.execute("SELECT 1 FROM world_satan_ledger WHERE shadow=1 "
                        "AND action IN ('prayer','risk') AND target LIKE ?",
                        (f"#{p['id']} %",)).fetchone():
            continue                                   # already shadow-judged
        v = _verdict_for(conn, p)
        if not v:
            continue                                   # even the red team passes
        decision, why = v
        blocked = _floor_blocked(p)
        if decision == "reject" and blocked:
            decision, why = "flag", f"{why} · {blocked}"
        _log(conn, "prayer" if decision == "reject" else "risk", kind=p["kind"],
             target=f"#{p['id']} {p['title']}",
             decision=f"would-{decision}", detail=why, shadow=1)
        logged += 1
    return logged


def _shadow_cap(conn):
    """Record the ONE capability Satan would have pressed this tick (same pool
    + same busy guard), throttled so the stream doesn't repeat itself."""
    import world_caps as wc
    pool = _cap_pool(wc)
    if not pool or wc._content_busy(conn):
        return None
    cap = random.choice(pool)
    if conn.execute("SELECT 1 FROM world_satan_ledger WHERE shadow=1 AND action='capability' "
                    "AND kind=? AND created_at > datetime('now','-6 hours')",
                    (cap["id"],)).fetchone():
        return None                                    # said so recently — don't spam
    args = {}
    if cap.get("args") and cap["id"] in wc.AUTO_ARGS:
        try:
            args = wc.AUTO_ARGS[cap["id"]](conn) or {}
        except Exception:
            pass
    _log(conn, "capability", kind=cap["id"], target=cap["label"], decision="would-invoke",
         detail=json.dumps({k: str(v)[:80] for k, v in args.items()}) if args else "",
         shadow=1)
    return cap["id"]


def shadow_stream(limit=40, conn=None):
    """The read-only shadow entries, newest first."""
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        return [dict(r) for r in conn.execute(
            "SELECT id, action, kind, target, decision, detail, created_at, "
            "CAST(strftime('%s', created_at) AS INTEGER) AS ts "
            "FROM world_satan_ledger WHERE COALESCE(shadow,0)=1 "
            "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]
    finally:
        if own:
            conn.close()


def shadow_stats(conn=None):
    """Agreement score: of the shadow's would-rejects whose prayers a HUMAN
    later resolved, how often did the human also reject? (Auto/duplicate
    resolutions are excluded — only your real verdicts are ground truth.)"""
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        rows = conn.execute(
            "SELECT target, decision FROM world_satan_ledger "
            "WHERE COALESCE(shadow,0)=1 AND action='prayer'").fetchall()
        total = len(rows)
        resolved = agreed = 0
        for r in rows:
            try:
                pid = int(str(r["target"]).split()[0].lstrip("#"))
            except Exception:
                continue
            p = conn.execute("SELECT status, god_comment FROM world_prayers WHERE id=?",
                             (pid,)).fetchone()
            if not p or p["status"] == "pending":
                continue
            gc = p["god_comment"] or ""
            if gc.startswith("auto") or gc.startswith("duplicate"):
                continue                               # not a human verdict
            resolved += 1
            human = "approve" if p["status"] in ("approved", "done", "failed") else "reject"
            if r["decision"] == f"would-{human}":
                agreed += 1
        return {"evaluated": total, "human_resolved": resolved, "agreed": agreed,
                "agreement_pct": round(100.0 * agreed / resolved) if resolved else None}
    finally:
        if own:
            conn.close()


# ── the risk report (feeds the two-lieutenant digest + security red team) ────
def risk_report(conn, pend=None, bal=None, spend=None, cap=None):
    """Satan's deterministic 'what could go wrong' briefing — read-only.
    Reported even while disabled (the digest marks the seat inert)."""
    import world_duality
    import world_ops as wo
    risks = []
    if pend is None:
        wo.ensure(conn)
        pend = [dict(r) for r in conn.execute(
            "SELECT id, kind, title, cost_cents, taste, boss_ok, mayor_ok "
            "FROM world_prayers WHERE status='pending' ORDER BY id ASC LIMIT 40").fetchall()]
    money = [p for p in pend if int(p.get("cost_cents") or 0) > 0
             or p.get("kind") in wo.ALWAYS_GATE]
    if money:
        total = sum(int(p.get("cost_cents") or 0) for p in money)
        risks.append(f"{len(money)} pending prayer(s) would move real money "
                     f"(≥ ${total / 100:.2f}) — each needs YOUR explicit hand; "
                     "neither lieutenant can (or will) execute them")
    blocked = [p for p in pend if p.get("boss_ok") == 0 and p.get("mayor_ok") == 0]
    if blocked:
        risks.append(f"{len(blocked)} pending prayer(s) both leaders blocked — "
                     "likely rejects clogging the queue")
    low = [p for p in pend if p.get("taste") is not None
           and not world_duality._clears_taste(p["taste"])]
    if low:
        risks.append(f"{len(low)} pending prayer(s) score under your taste bar")
    try:
        if bal is None:
            bal = wo.balance_cents(conn)
        if spend is None:
            spend = wo.cycle_spend_cents(conn)
        if cap is None:
            cap = wo.cap_cents()
        if bal < 0:
            risks.append(f"the treasury is ${-bal / 100:.2f} in the red — the bill is due")
        if cap and spend >= 0.8 * cap:
            risks.append(f"monthly spend ${spend / 100:.2f} is at "
                         f"{int(100 * spend / cap)}% of the ${cap / 100:.2f} cap")
    except Exception:
        pass
    try:
        import world_security
        posture = world_security.real_posture(conn) or {}
        if posture.get("warn"):
            risks.append("weakened defenses: " + ", ".join(posture["warn"][:4]))
        if posture.get("attackers"):
            risks.append(f"{posture['attackers']} live attacker(s) probing the perimeter")
    except Exception:
        pass
    if not risks:
        risks.append("no material risk found — worst case right now is wasted GPU time")
    return risks


def security_redteam(c, suspicious, posture=None):
    """The Satan red-team pass alongside world_security's constructive review:
    a deterministic worst-case one-liner on the scan's findings, written to the
    security feed + Satan's ledger. No-op while Satan is disabled; read-only
    beyond its own log lines; never blocks the scan (caller wraps in try)."""
    if not enabled():
        return None
    items = list(suspicious or [])
    posture = posture or {}
    if items:
        worst = (f"assume the worst on {len(items)} finding(s): treat '{items[0][:70]}' "
                 "as an active compromise until proven otherwise")
    else:
        worst = "clean scan — worst case is a blind spot, not an all-clear; rotate what you can"
    if posture.get("warn"):
        worst += f"; {len(posture['warn'])} defense(s) already weakened"
    try:
        # display string only (naming_theme): themed default = the exact
        # current "😈 Satan (red team):" line. The ledger/actor ids are untouched.
        try:
            import naming_theme as _nt
            _who = _nt.display("satan")
        except Exception:
            _who = "😈 Satan"
        c.execute("INSERT INTO world_events (agent_key, kind, text) VALUES (?,?,?)",
                  ("", "security", f"{_who} (red team): {worst[:220]}"))
    except Exception:
        pass
    try:
        ensure(c)
        _log(c, "risk", kind="security", target="security scan", decision="flag",
             detail=worst)
    except Exception:
        logger.exception("satan security redteam log failed")
    return worst


# ── panel data ───────────────────────────────────────────────────────────────
def ledger(limit=40, conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        # real actions only — shadow (would-do) rows have their own stream
        return [dict(r) for r in conn.execute(
            "SELECT id, action, kind, target, decision, detail, created_at, "
            "CAST(strftime('%s', created_at) AS INTEGER) AS ts "
            "FROM world_satan_ledger WHERE COALESCE(shadow,0)=0 "
            "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]
    finally:
        if own:
            conn.close()


def status():
    last = _last()
    iv = interval_min()
    try:
        import nsfw
        nsfw_world_active = bool(nsfw.world_active())
    except Exception:
        nsfw_world_active = False
    return {
        "enabled": enabled(),
        "interval_min": iv,
        "last": last,
        "next_due_sec": max(0, int(last + iv * 60 - time.time())) if (enabled() and last) else 0,
        "nsfw_domain": {
            "enabled": nsfw_domain_on(),
            "nsfw_world_active": nsfw_world_active,
            "note": "Review-only oversight of the private studio, and ONLY when both this "
                    "gate and the layered NSFW mode are on. The minors/CSAM and real-person "
                    "deepfake/NCII floors are ALWAYS-ON at the generation endpoints, are NOT "
                    "toggleable, are NOT part of this gate, and are enforced against Satan "
                    "exactly as against every other actor.",
        },
        "shadow": {
            "enabled": shadow_on(),
            "last": _shadow_last(),
            "stats": shadow_stats(),
            "stream": shadow_stream(30),
        },
        "floor": {
            "human_only_kinds": sorted(skip_kinds()),
            "note": "Satan predicts/red-teams; he never executes a money action — payouts, "
                    "wallet sends and secret exports stay ALWAYS_GATE human-only, anything "
                    "costing cents is refused, and he has NO approve path at all. The "
                    "illegal-content floors (minors/CSAM + real-person deepfake/NCII) are "
                    "always-on at the endpoints for every actor and are not under Satan's "
                    "— or anyone's — control.",
        },
        "ledger": ledger(40),
    }


def set_config(body):
    _on = (True, 1, "1", "true", "on")
    if "enabled" in body:
        _set(ENABLED_KEY, "1" if body["enabled"] in _on else "0")
    if "nsfw_domain" in body:
        _set(NSFW_DOMAIN_KEY, "1" if body["nsfw_domain"] in _on else "0")
    if "shadow" in body:
        _set(SHADOW_KEY, "1" if body["shadow"] in _on else "0")
    if "interval_min" in body:
        try:
            _set(INTERVAL_KEY, max(5, int(body["interval_min"])))
        except (TypeError, ValueError):
            pass
    return status()

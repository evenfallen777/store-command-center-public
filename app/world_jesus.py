"""
The Company — "Jesus": the owner's OPTIONAL stand-in operator (pure delegation).

The human owner ("God") runs the platform day to day: they click the capability
buttons they've already enabled, and they approve/reject the agents' pending
prayers. Jesus is a toggle-gated AI teammate that can be handed THAT EXACT SEAT
so the platform runs hands-off — nothing more:

  • ONE toggle, world_jesus_enabled, DEFAULT OFF. Only the owner flips it.
    Off ⇒ Jesus is completely inert (every entry point checks it, even the
    manual "act now" endpoint).
  • When ON, on a throttled loop (world_jesus_interval_min, one action per
    tick, riding the existing world_auto daemon within the same active hours),
    Jesus performs the SAME operator actions the owner already performs:
      (a) invoke ONLY capabilities the owner has ALREADY ENABLED — the
          invocation goes through world_caps.invoke(actor="jesus"), which is
          gated EXACTLY like an agent (catalog master + per-cap gate), so the
          delegation adds ZERO privilege. Jesus never flips a gate: this module
          only ever writes its own three settings keys and its own ledger.
      (b) make the routine approve/reject calls on pending prayers the owner
          would otherwise action — approve when Boss + Mayor both endorse and
          taste clears the owner's own minimum; reject when both leaders
          blocked it; anything split stays pending for the human.

THE HARD SAFETY FLOOR IS UNCHANGED and applies to Jesus exactly as to every
other actor — and Jesus cannot grant itself an exception:
  • Real money never moves without an explicit human: Jesus refuses to touch
    any prayer in world_ops.ALWAYS_GATE (payouts, wallet sends, secret export —
    those gates are not even toggleable for anyone) AND any prayer that costs
    real cents (Etsy/Printify spends stay human).
  • add_software prayers stay human too: approved code could alter the floor
    itself, so the delegate may never bless code.
  • The illegal-content floor (minors/CSAM + real-person deepfakes) lives in
    the generation endpoints' own gates (nsfw/aishield); Jesus rides the same
    endpoints as everyone and is refused identically. Nothing here ever sets
    nsfw=true (world_caps already guarantees that for every cap).
  • Jesus's verdicts are stamped "auto (Jesus): …" so world_ops._learn_verdict
    skips them — only REAL human verdicts train the taste model (no
    self-reinforcing feedback loop) and only God's touch blesses an agent.

Every action/decision lands in world_jesus_ledger, surfaced in the God Panel
(✝️ Jesus tab) — fully auditable.

Two OPTIONAL extras, both DEFAULT OFF:
  • world_jesus_publish_caps — adds the free publishing-prayer buttons
    (world_caps.JESUS_PUBLISH_CAPS: social_publish / publish_3d) to the
    delegated pool. Those caps only FILE a still-gated prayer, are refused
    whenever their agent-gate is off, and paid / ALWAYS_GATE kinds can never
    enter the pool (_publish_pool re-checks the floor per cap).
  • world_jesus_shadow — while Jesus is DISABLED, an observe-only shadow
    records what it WOULD have approved/rejected/invoked (ledger rows with
    shadow=1) plus an agreement score vs your real verdicts. It takes no
    action ever — a read-only "what Jesus would do" stream for the owner.
"""
import json
import logging
import random
import time

from deps import get_conn, get_setting

logger = logging.getLogger("store")

ENABLED_KEY = "world_jesus_enabled"          # DEFAULT OFF — only the owner turns it on
INTERVAL_KEY = "world_jesus_interval_min"
LAST_KEY = "world_jesus_last"
INTERVAL_DEFAULT = 30                        # at most one delegated action / 30 min
# Extend the delegated cap pool with the publishing-prayer buttons
# (world_caps.JESUS_PUBLISH_CAPS: free caps that only FILE gated publish
# prayers). DEFAULT OFF; when on, each cap is STILL refused unless its own
# agent-gate is on (invoke(actor='jesus') enforces master + per-cap gate).
PUBLISH_CAPS_KEY = "world_jesus_publish_caps"
# SHADOW mode: while Jesus is DISABLED, record what it WOULD have done into a
# read-only shadow stream (world_jesus_ledger rows with shadow=1) — observe
# only, takes NO action ever. DEFAULT OFF.
SHADOW_KEY = "world_jesus_shadow"
SHADOW_LAST_KEY = "world_jesus_shadow_last"


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


def publish_caps_enabled():
    return _truthy(get_setting(PUBLISH_CAPS_KEY, "0"))


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
        conn.execute("""CREATE TABLE IF NOT EXISTS world_jesus_ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action   TEXT NOT NULL,          -- prayer | capability | refused
            kind     TEXT,                   -- prayer kind / capability id
            target   TEXT,                   -- '#12 title…' / cap label
            decision TEXT,                   -- approve | reject | invoke | refused
            detail   TEXT,
            created_at TEXT DEFAULT (datetime('now')))""")
        # shadow=1 rows are OBSERVE-ONLY "what Jesus WOULD do" entries recorded
        # while the toggle is OFF (world_jesus_shadow) — never actions taken.
        try:
            conn.execute("ALTER TABLE world_jesus_ledger ADD COLUMN shadow INTEGER DEFAULT 0")
        except Exception:
            pass
        conn.commit()
    finally:
        if own:
            conn.close()


def _log(conn, action, kind=None, target=None, decision=None, detail=None, shadow=0):
    conn.execute("INSERT INTO world_jesus_ledger (action,kind,target,decision,detail,shadow) "
                 "VALUES (?,?,?,?,?,?)",
                 (action, kind, (target or "")[:160], decision, (detail or "")[:300],
                  1 if shadow else 0))
    conn.commit()


# ── the unchanged hard floor, as seen from the delegate's side ───────────────
def skip_kinds():
    """Prayer kinds the delegate may NEVER action (the human-only floor)."""
    import world_ops as wo
    return set(wo.ALWAYS_GATE) | {"add_software"}


def _floor_blocked(p):
    """Why this prayer is off-limits to Jesus (None = actionable)."""
    if p["kind"] in skip_kinds():
        return "human-only kind (real money / secrets / code)"
    if int(p["cost_cents"] or 0) > 0:
        return "costs real money — spend needs an explicit human"
    return None


# ── (a) the owner's routine prayer verdicts ──────────────────────────────────
def _verdict_for(conn, p, tmin):
    """The delegate's routine call on ONE pending prayer: ('approve', why) when
    both leaders endorse and taste clears the owner's minimum, ('reject', why)
    when both leaders blocked it, None on a split (waits for the human).
    Shared by the REAL tick and the observe-only shadow so both always apply
    the exact same bar. Never called on floor-blocked prayers."""
    import world_ops as wo
    taste, bok, mok = p["taste"], p["boss_ok"], p["mayor_ok"]
    if taste is None:                              # filed before endorsements ran
        taste, bok, mok = wo._endorse(conn, p["id"])
    bok, mok = (1 if bok else 0 if bok is not None else None), \
               (1 if mok else 0 if mok is not None else None)
    if bok == 1 and mok == 1 and float(taste) >= tmin:
        return "approve", f"both leaders endorse · taste {int(float(taste) * 100)}%"
    if bok == 0 and mok == 0:
        return "reject", f"both leaders blocked it · {p.get('endorse_note') or ''}".strip(" ·")
    return None


def _action_prayer(conn, prayer_id=None):
    """Approve/reject ONE pending prayer using the owner's own routine bar:
    both leaders endorse + taste ≥ the owner's minimum → approve; both leaders
    blocked → reject; split decisions stay pending for the human. Floor-blocked
    prayers are never touched. Returns an action dict or None."""
    import world_ops as wo
    wo.ensure(conn)
    if prayer_id:
        rows = conn.execute("SELECT * FROM world_prayers WHERE id=? AND status='pending'",
                            (int(prayer_id),)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM world_prayers WHERE status='pending' "
                            "ORDER BY id ASC LIMIT 25").fetchall()
    tmin = wo.taste_min()
    for r in rows:
        p = dict(r)
        if _floor_blocked(p):
            continue                                   # stays pending for the human
        v = _verdict_for(conn, p, tmin)
        if not v:
            continue                                   # split call → wait for the human
        decision, why = v
        # "auto (Jesus): …" — the auto prefix keeps taste-training + blessings
        # strictly human (world_ops._learn_verdict skips auto verdicts).
        res = wo._resolve(conn, p["id"], approve=(decision == "approve"),
                          god_comment=f"auto (Jesus): {why}")
        _log(conn, "prayer", kind=p["kind"], target=f"#{p['id']} {p['title']}",
             decision=decision, detail=why)
        return {"action": "prayer", "prayer_id": p["id"], "decision": decision,
                "title": p["title"], "why": why,
                "status": (res or {}).get("status")}
    return None


# ── (b) pressing an already-enabled capability button ────────────────────────
def _publish_pool(wc):
    """The publishing-prayer buttons (wc.JESUS_PUBLISH_CAPS) the delegate may
    ALSO press when the owner flips world_jesus_publish_caps ON (default OFF).
    Belt-and-braces floor re-check per cap: refused unless its agent-gate is
    on, and dropped outright if its downstream prayer kind is ALWAYS_GATE /
    human-only or costs real cents — so the pool can only ever contain free
    caps that FILE a still-gated prayer. Zero authority beyond the gate rules."""
    import world_ops as wo
    human_only = skip_kinds()
    out = []
    for cid, kind in getattr(wc, "JESUS_PUBLISH_CAPS", {}).items():
        cap = wc._CAP.get(cid)
        if not cap or not wc.gate_on(cap):
            continue                               # the owner's per-cap gate is off
        if kind in wo.ALWAYS_GATE or kind in human_only:
            continue                               # human-only kind — never delegated
        if int(wo.KIND_COST.get(kind, 0)) > 0 or kind in wo.PAID_KINDS:
            continue                               # costs real money — stays human
        if cap.get("args") and cid not in wc.AUTO_ARGS:
            continue                               # can't auto-build the args
        out.append(cap)
    return out


def _action_cap(conn, cap_id=None):
    """Invoke ONE capability the owner has already enabled. The pool is the
    same routine set the agent auto-loop uses (agent_safe: free, local,
    non-publishing, non-spending, args auto-buildable) — plus, ONLY when the
    owner flips world_jesus_publish_caps ON, the free publishing-prayer buttons
    (see _publish_pool). Every invocation goes through
    world_caps.invoke(actor='jesus'), which enforces the catalog master
    + per-cap gate exactly like an agent — Jesus never enables anything."""
    import world_caps as wc
    if cap_id:
        cap = wc._CAP.get(cap_id)
        if not cap:
            _log(conn, "refused", kind=cap_id, decision="refused", detail="unknown capability")
            return None
        pool = [cap]
    else:
        pool = [c for c in wc.CATALOG
                if c.get("agent_safe") and wc.gate_on(c)
                and (not c.get("args") or c["id"] in wc.AUTO_ARGS)]
        if publish_caps_enabled():
            pool += _publish_pool(wc)
    if not pool:
        return None
    if wc._content_busy(conn):
        return None                                    # the single-3060 guard wins
    cap = random.choice(pool)
    args = {}
    if cap.get("args"):
        if cap["id"] not in wc.AUTO_ARGS:
            _log(conn, "refused", kind=cap["id"], target=cap["label"], decision="refused",
                 detail="capability needs arguments Jesus cannot auto-build")
            return None
        try:
            args = wc.AUTO_ARGS[cap["id"]](conn) or {}
        except Exception:
            logger.exception("jesus auto-args failed for %s", cap["id"])
    if cap["id"] in getattr(wc, "JESUS_PUBLISH_CAPS", {}) and not args:
        return None                                    # nothing ready to publish — no-op
    res = wc.invoke(cap["id"], args, actor="jesus", agent="Jesus")
    if not res.get("ok"):
        # the gate said no (e.g. a forced cap whose gate is OFF) — audit the refusal
        _log(conn, "refused", kind=cap["id"], target=cap["label"], decision="refused",
             detail=res.get("error") or "refused")
        return None
    _log(conn, "capability", kind=cap["id"], target=cap["label"], decision="invoke",
         detail=json.dumps({k: str(v)[:80] for k, v in args.items()}) if args else "")
    try:
        import world_ops as wo
        # display string only (naming_theme): themed default is the exact
        # current line; from_agent stays "Jesus" — it's the audited actor id.
        import naming_theme as _nt
        wo.note(f"{_nt.display('jesus')} (operating for {_nt.label('god')}) "
                f"triggered: {cap['label']}",
                kind="info", from_agent="Jesus", conn=conn)
    except Exception:
        pass
    return {"action": "capability", "cap_id": cap["id"], "label": cap["label"],
            "run_id": res.get("run_id"), "args": args}


# ── the tick ─────────────────────────────────────────────────────────────────
def tick(force=False, action=None, cap_id=None, prayer_id=None):
    """ONE delegated action. force=True (the owner's 'act now' button) skips the
    interval + active-hours throttles but NEVER the enabled toggle or the
    company master pause. `action`/`cap_id`/`prayer_id` let the owner steer a
    controlled single action; every gate still applies."""
    if not enabled():
        return {"ok": False, "skipped": "Jesus is disabled (world_jesus_enabled is off)"}
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
        else:
            act = _action_prayer(conn) or _action_cap(conn)
        return {"ok": True, "acted": bool(act), "did": act}
    finally:
        conn.close()


def maybe_tick(now=None):
    """Rides the existing world_auto daemon (~1/min, inside active hours) —
    no second loop. No-op unless the owner's toggle is on and the interval
    has elapsed. While the toggle is OFF, the (separately toggled, default
    OFF) SHADOW observer may record what Jesus WOULD have done — observe
    only, it never takes an action."""
    if not enabled():
        try:
            return shadow_tick(now)                # observe-only; acts on nothing
        except Exception:
            logger.exception("world_jesus shadow tick failed")
            return None
    now = now or time.time()
    if now - _last() < interval_min() * 60:
        return None
    try:
        return tick(force=False)
    except Exception:
        logger.exception("world_jesus tick failed")
        return None


# ── SHADOW: the read-only "what Jesus would do" stream ───────────────────────
# Runs ONLY while Jesus is DISABLED and world_jesus_shadow is ON (default
# OFF). It re-uses the exact same routine bar (_verdict_for) and cap pools as
# the real tick, but takes NO action: no _resolve, no invoke, no note — every
# would-be decision lands in world_jesus_ledger with shadow=1, surfaced to the
# owner as a read-only stream + an agreement score against the human verdicts
# that eventually resolve the same prayers (world_taste-style: your real
# verdicts are the ground truth the shadow is measured against).
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
    """Record would-approve / would-reject for pending prayers Jesus could
    action (same floor + same bar as the real tick), once per prayer."""
    import world_ops as wo
    wo.ensure(conn)
    tmin = wo.taste_min()
    logged = 0
    for r in conn.execute("SELECT * FROM world_prayers WHERE status='pending' "
                          "ORDER BY id ASC LIMIT 25").fetchall():
        if logged >= limit:
            break
        p = dict(r)
        if _floor_blocked(p):
            continue                                   # human-only — the shadow skips it too
        if conn.execute("SELECT 1 FROM world_jesus_ledger WHERE shadow=1 "
                        "AND action='prayer' AND target LIKE ?",
                        (f"#{p['id']} %",)).fetchone():
            continue                                   # already shadow-judged
        v = _verdict_for(conn, p, tmin)
        if not v:
            continue                                   # split — Jesus would wait for you too
        decision, why = v
        _log(conn, "prayer", kind=p["kind"], target=f"#{p['id']} {p['title']}",
             decision=f"would-{decision}", detail=why, shadow=1)
        logged += 1
    return logged


def _shadow_cap(conn):
    """Record the ONE capability Jesus would have pressed this tick (same pool
    + same busy guard), throttled so the stream doesn't repeat itself."""
    import world_caps as wc
    pool = [c for c in wc.CATALOG
            if c.get("agent_safe") and wc.gate_on(c)
            and (not c.get("args") or c["id"] in wc.AUTO_ARGS)]
    if publish_caps_enabled():
        pool += _publish_pool(wc)
    if not pool or wc._content_busy(conn):
        return None
    cap = random.choice(pool)
    if conn.execute("SELECT 1 FROM world_jesus_ledger WHERE shadow=1 AND action='capability' "
                    "AND kind=? AND created_at > datetime('now','-6 hours')",
                    (cap["id"],)).fetchone():
        return None                                    # said so recently — don't spam
    args = {}
    if cap.get("args") and cap["id"] in wc.AUTO_ARGS:
        try:
            args = wc.AUTO_ARGS[cap["id"]](conn) or {}
        except Exception:
            pass
    if cap["id"] in getattr(wc, "JESUS_PUBLISH_CAPS", {}) and not args:
        return None                                    # nothing it would publish
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
            "FROM world_jesus_ledger WHERE COALESCE(shadow,0)=1 "
            "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]
    finally:
        if own:
            conn.close()


def shadow_stats(conn=None):
    """Agreement score: of the shadow's would-verdicts whose prayers a HUMAN
    later resolved, how often did the human agree? (Auto/duplicate resolutions
    are excluded — only your real verdicts are ground truth.)"""
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        rows = conn.execute(
            "SELECT target, decision FROM world_jesus_ledger "
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
            "FROM world_jesus_ledger WHERE COALESCE(shadow,0)=0 "
            "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]
    finally:
        if own:
            conn.close()


def status():
    last = _last()
    iv = interval_min()
    try:
        import world_caps as wc
        publish_pool = [c["label"] for c in _publish_pool(wc)]
        publish_all = [wc._CAP[cid]["label"] for cid in getattr(wc, "JESUS_PUBLISH_CAPS", {})
                       if cid in wc._CAP]
    except Exception:
        publish_pool, publish_all = [], []
    return {
        "enabled": enabled(),
        "interval_min": iv,
        "last": last,
        "next_due_sec": max(0, int(last + iv * 60 - time.time())) if (enabled() and last) else 0,
        "publish_caps": {
            "enabled": publish_caps_enabled(),
            "caps": publish_all,               # the buttons this toggle covers
            "eligible_now": publish_pool,      # …of those, currently agent-gated ON
        },
        "shadow": {
            "enabled": shadow_on(),
            "last": _shadow_last(),
            "stats": shadow_stats(),
            "stream": shadow_stream(30),
        },
        "floor": {
            "human_only_kinds": sorted(skip_kinds()),
            "note": "Real-money transfers/withdrawals/spend always need an explicit human; "
                    "the illegal-content floor is always refused at the endpoints. Jesus "
                    "cannot alter either — it only operates what the owner already enabled.",
        },
        "ledger": ledger(40),
    }


def set_config(body):
    _on = (True, 1, "1", "true", "on")
    if "enabled" in body:
        _set(ENABLED_KEY, "1" if body["enabled"] in _on else "0")
    if "publish_caps" in body:
        _set(PUBLISH_CAPS_KEY, "1" if body["publish_caps"] in _on else "0")
    if "shadow" in body:
        _set(SHADOW_KEY, "1" if body["shadow"] in _on else "0")
    if "interval_min" in body:
        try:
            _set(INTERVAL_KEY, max(5, int(body["interval_min"])))
        except (TypeError, ValueError):
            pass
    return status()

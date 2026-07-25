"""
The Company — God Console / operations backbone.

The safety layer that lets the world autonomously run the store WITHOUT anything
real or costly happening behind your back. Three pieces:

  • Prayers  — an approval queue. When an agent wants to do something real
               (publish to WordPress, post to Etsy, generate a batch), it calls
               pray(); depending on `automation_mode` that either runs now
               (free / within budget) or waits for the god (you) to approve.
  • Budget   — a postpaid ledger, exactly like Etsy bills you. Spends push the
               balance negative (you OWE); Cults3D revenue / manual funding /
               PayPal payments push it back up. A monthly `cap` is the furthest
               you let the bill run before even auto-mode has to ask.
  • Messages — the company community talking to you: warnings, praise, needs
               ("we need more affiliates", "N prayers await approval").

WordPress (example.com) publishing is FREE, so those prayers cost 0 and, in
budget mode, just run. Only Etsy/Printify ($0.20/listing) draws the budget.

This module is intentionally decoupled from the world sim (world_sim/ticker are
another agent's domain). It owns its own tables and is safe to call from
anywhere via pray() / note().
"""
import json, logging, time
from deps import get_conn, get_setting
from crypto import enc as _enc, is_secret as _is_secret

logger = logging.getLogger("store")

# ── which prayer kinds actually cost real money (draw the budget) ─────────────
PAID_KINDS = {"post_etsy", "post_printify"}
# default cent-cost when a caller doesn't specify one
KIND_COST = {"post_etsy": 20, "post_printify": 20}
# IRREVERSIBLE money-out / secret-export kinds: these ALWAYS need an explicit human
# blessing and are the ONE deliberate exception to "every gate gets a toggle" — they
# can NEVER be un-gated. gated_kinds() unions them in regardless of the per-kind
# toggles, and the gate-set endpoint refuses to turn any of them off. (Etsy/Printify/
# add_software stay toggle-gated by default via GATEABLE, per the owner's rule.)
ALWAYS_GATE = {"paypal_payout", "wallet_send", "secret_export"}
# Kinds whose EXECUTOR writes its own ledger entry (only on real success). _resolve
# must NOT pre-charge these as a generic "spend" or it would double-debit the budget;
# their cost_cents is used only for the can_spend / affordability GATES.
SELF_LEDGER_KINDS = {"paypal_payout", "wallet_send"}
# CREATIVE work the agents MAKE (art/products) vs pure OPERATIONS. The God Console
# shows creations in their own "judge" section (with thumbnails) so approving/rejecting
# them teaches the town's taste — separate from operational permission requests.
CREATION_KINDS = {"publish_wordpress", "publish_cults3d", "post_etsy", "post_printify"}

DEFAULTS = {
    "world_ops_automation_mode": "review",   # "review" = queue everything real; "budget" = auto within budget
    "world_ops_cap_cents":       "2000",     # monthly bill ceiling ($20) before auto-mode must ask
    "world_taste_min":           "0.35",     # min predicted-approval for anything to auto-run
    "world_paypal_client_id":    "",
    "world_paypal_secret":       "",
    "world_paypal_mode":         "sandbox",   # sandbox | live
    "world_paypal_email":        "",
    # ── Gates (each is a toggle) ─────────────────────────────────────────────
    # world_ops_gate_creations: ON = agents' creative pieces ALWAYS wait for your
    # 👍/👎 before publishing, even in budget mode (default off = mode decides).
    "world_ops_gate_creations":       "0",
    # Per-kind "always need a blessing" gates — default ON (today's behavior), each flippable.
    "world_ops_gate_paypal_payout":   "1",
    "world_ops_gate_add_software":    "1",
    "world_ops_gate_post_etsy":       "1",
    "world_ops_gate_post_printify":   "1",
    "world_ops_gate_cashapp_request":  "1",
    "world_ops_gate_cashapp_checkout": "1",
    # social publishing (YouTube/TikTok via world_caps) — public + irreversible
    # once live, so it defaults to always-ask; flippable like the other gates.
    "world_ops_gate_social_publish":   "1",
}

# Kinds that CAN be always-gated, each with its own toggle + label (gates get a toggle).
GATEABLE = [
    ("paypal_payout", "💸 Real-money payouts"),
    ("add_software",  "💾 Code changes"),
    ("post_etsy",     "🛍️ Etsy listings"),
    ("post_printify", "👕 Printify listings"),
    ("cashapp_request",  "💵 Cash App payment-request links"),
    ("cashapp_checkout", "🟩 Cash App Pay checkout links"),
    ("social_publish",   "📣 Social publishing (YouTube/TikTok)"),
]


def gated_kinds():
    """The EFFECTIVE set of kinds that always need a blessing (never auto-run), built
    from the per-kind toggles + the optional 'always judge creations' gate. The
    irreversible money-out / secret-export kinds (ALWAYS_GATE) are ALWAYS unioned in
    regardless of any toggle, so they can never be silently un-gated."""
    g = {kind for kind, _label in GATEABLE if cfg(f"world_ops_gate_{kind}") == "1"}
    if cfg("world_ops_gate_creations") == "1":
        g |= CREATION_KINDS
    g |= ALWAYS_GATE          # irreversible money-out / secret export — never toggleable
    return g


# ── schema ────────────────────────────────────────────────────────────────────
def ensure(conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS world_prayers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kind        TEXT NOT NULL,
            title       TEXT NOT NULL,
            detail      TEXT DEFAULT '',
            cost_cents  INTEGER DEFAULT 0,
            payload     TEXT DEFAULT '{}',      -- json args for the executor
            agent_name  TEXT,                    -- who prayed (null = the system)
            status      TEXT DEFAULT 'pending',  -- pending|approved|rejected|done|failed
            god_comment TEXT,
            result      TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            resolved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS world_messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kind        TEXT DEFAULT 'info',     -- warning|praise|need|info
            text        TEXT NOT NULL,
            from_agent  TEXT,
            seen        INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS world_ops_ledger (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            amount_cents INTEGER NOT NULL,       -- + credit (fund/revenue/payment), - debit (spend)
            kind        TEXT NOT NULL,           -- fund|revenue|spend|payment
            source      TEXT,                    -- manual|cults3d|paypal|etsy|printify
            note        TEXT,
            prayer_id   INTEGER,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        """)
        # endorsement columns (Boss/Mayor checks + the learned taste score)
        for col, typ in (("taste", "REAL"), ("boss_ok", "INTEGER"),
                         ("mayor_ok", "INTEGER"), ("endorse_note", "TEXT")):
            try:
                conn.execute(f"ALTER TABLE world_prayers ADD COLUMN {col} {typ}")
            except Exception:
                pass
        conn.commit()
    finally:
        if own:
            conn.close()


# ── config helpers ──────────────────────────────────────────────────────────
def cfg(key):
    return get_setting(key, DEFAULTS.get(key))


def _save_cfg(conn, updates):
    for k, v in updates.items():
        if k not in DEFAULTS:
            continue
        val = _enc(str(v)) if _is_secret(k) else str(v)   # encrypt paypal creds at rest
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, val))
    conn.commit()


def paypal_cfg():
    return {"client_id": cfg("world_paypal_client_id"), "secret": cfg("world_paypal_secret"),
            "mode": cfg("world_paypal_mode"), "email": cfg("world_paypal_email")}


def automation_mode():
    # TEST run mode = a safe dry run: force 'budget' so the FREE internal loops (art/music/
    # agent work/publishing to WordPress+Cults3D) auto-run and the world visibly evolves —
    # money + code kinds are in ALWAYS_GATE/gated_kinds(), so they STILL wait for you. So
    # test mode can never auto-spend or auto-post anything real.
    try:
        import world_run
        if world_run.is_test():
            return "budget"
    except Exception:
        pass
    m = cfg("world_ops_automation_mode")
    return m if m in ("review", "budget") else "review"


def cap_cents():
    try:
        return int(cfg("world_ops_cap_cents") or 0)
    except Exception:
        return 0


# ── budget (postpaid ledger) ─────────────────────────────────────────────────
def balance_cents(conn):
    """Running balance. Negative = the company owes money (unpaid bill)."""
    row = conn.execute("SELECT COALESCE(SUM(amount_cents),0) AS b FROM world_ops_ledger").fetchone()
    return int(row["b"] or 0)


def cycle_spend_cents(conn):
    """Money spent this calendar month (for the monthly cap gate)."""
    row = conn.execute(
        "SELECT COALESCE(SUM(-amount_cents),0) AS s FROM world_ops_ledger "
        "WHERE kind='spend' AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now')"
    ).fetchone()
    return int(row["s"] or 0)


def can_spend(conn, cost):
    """Budget mode gate: a spend is allowed only if it stays within the monthly cap."""
    if cost <= 0:
        return True
    return (cycle_spend_cents(conn) + cost) <= cap_cents()


def _ledger(conn, amount_cents, kind, source=None, note=None, prayer_id=None):
    conn.execute(
        "INSERT INTO world_ops_ledger (amount_cents,kind,source,note,prayer_id) VALUES (?,?,?,?,?)",
        (int(amount_cents), kind, source, note, prayer_id))
    conn.commit()


# ── community messages ───────────────────────────────────────────────────────
def note(text, kind="info", from_agent=None, conn=None):
    """Post a message from the company community to the god (you)."""
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        conn.execute("INSERT INTO world_messages (kind,text,from_agent) VALUES (?,?,?)",
                     (kind, text, from_agent))
        conn.commit()
    finally:
        if own:
            conn.close()


# ── checks & balances: the Boss and the Mayor endorse every prayer ────────────
# Two independent reviews BEFORE anything reaches automation:
#   💼 Boss Kane  — production standards + finances: is this on-brand for god's
#                   learned taste, and can the budget bear it?
#   🏛️ Mayor Vex — the people + the treasury: are the crew in shape for new
#                   ventures, and can the town's balance take the hit?
# Auto-run (budget mode) requires BOTH endorsements AND the taste model's
# confidence; anything they doubt waits for god. God's overriding verdicts feed
# world_taste — so every time you overrule them, the whole town learns.
def taste_min():
    try:
        return float(cfg("world_taste_min") or 0.35)
    except Exception:
        return 0.35


def _endorse(conn, pid):
    """Boss + Mayor review a prayer; stamps taste/boss_ok/mayor_ok/endorse_note."""
    p = _get(conn, pid)
    if not p:
        return 0.5, False, False
    try:
        import world_taste
        taste = world_taste.score(conn, f"{p['title']}. {p['detail'] or ''}", p["kind"])
    except Exception:
        logger.exception("taste score failed")
        taste = 0.5
    cost = int(p["cost_cents"] or 0)
    boss_ok, boss_why = True, "on brand, finances fine"
    if taste < 0.30:
        boss_ok, boss_why = False, f"doubts god will like it ({int(taste * 100)}%)"
    elif cost > 0 and not can_spend(conn, cost):
        boss_ok, boss_why = False, "over the monthly budget cap"
    mayor_ok, mayor_why = True, "the town supports it"
    try:
        row = conn.execute("SELECT AVG(COALESCE(energy,60)) e, AVG(COALESCE(fun,60)) f "
                           "FROM world_agents WHERE kind IN ('worker','openclaw')").fetchone()
        morale = ((row["e"] or 60) + (row["f"] or 60)) / 2
    except Exception:
        morale = 60
    if cost > 0 and balance_cents(conn) - cost < -cap_cents():
        mayor_ok, mayor_why = False, "the treasury can't bear it"
    elif morale < 30:
        mayor_ok, mayor_why = False, "the crew is exhausted — pause new ventures"
    # DUALITY: when either lieutenant (✝️ Jesus / 😈 Satan) is on, the single
    # taste number gains a best/worst/expected band on the endorse note — a
    # hi-lo range instead of one optimistic point. Presentational only: the
    # stored taste score, the endorsements and every gate are unchanged, and
    # with both lieutenants off (the default) the note is exactly as before.
    band_txt = ""
    try:
        import world_duality
        bn = world_duality.band_note(world_duality.band(taste))
        if bn:
            band_txt = f" · ⚖️ {bn}"
    except Exception:
        pass
    conn.execute("UPDATE world_prayers SET taste=?, boss_ok=?, mayor_ok=?, endorse_note=? WHERE id=?",
                 (round(taste, 3), int(boss_ok), int(mayor_ok),
                  f"💼 {boss_why} · 🏛️ {mayor_why}{band_txt}", pid))
    conn.commit()
    return taste, boss_ok, mayor_ok


# ── prayers (the approval queue) ─────────────────────────────────────────────
def pray(kind, title, detail="", cost_cents=None, payload=None, agent_name=None, conn=None):
    """An agent (or the system) asks to do something real.

    Returns the resulting prayer row (dict). Behaviour by automation_mode:
      review  → always queued 'pending' for the god to approve.
      budget  → free actions (cost 0) and within-budget paid actions run now;
                over-budget actions are queued 'pending'.
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        if cost_cents is None:
            cost_cents = KIND_COST.get(kind, 0)
        payload_s = json.dumps(payload or {})
        cur = conn.execute(
            "INSERT INTO world_prayers (kind,title,detail,cost_cents,payload,agent_name,status) "
            "VALUES (?,?,?,?,?,?, 'pending')",
            (kind, title, detail, int(cost_cents), payload_s, agent_name))
        conn.commit()
        pid = cur.lastrowid

        taste, boss_ok, mayor_ok = _endorse(conn, pid)
        auto = (automation_mode() == "budget" and kind not in gated_kinds()
                and (cost_cents == 0 or can_spend(conn, cost_cents))
                and boss_ok and mayor_ok and taste >= taste_min())
        if auto:
            _resolve(conn, pid, approve=True, god_comment="auto (budget mode)")
        return _get(conn, pid)
    finally:
        if own:
            conn.close()


def _get(conn, pid):
    row = conn.execute("SELECT * FROM world_prayers WHERE id=?", (pid,)).fetchone()
    return dict(row) if row else None


def _resolve(conn, pid, approve, god_comment=None):
    p = _get(conn, pid)
    if not p or p["status"] not in ("pending",):
        return p
    if not approve:
        # Atomically claim the pending→rejected transition. If two callers race, only
        # the one whose UPDATE actually flips a 'pending' row proceeds; the loser no-ops.
        cur = conn.execute("UPDATE world_prayers SET status='rejected', god_comment=?, "
                           "resolved_at=datetime('now') WHERE id=? AND status='pending'",
                           (god_comment, pid))
        conn.commit()
        if cur.rowcount != 1:
            return _get(conn, pid)                     # lost the race — someone else resolved it
        # A rejection is god's taste too: teach the model (-1) + let the creator feel it,
        # then — for a creative piece — kick a reworked version with the feedback baked in.
        _learn_verdict(conn, pid, approve=False, god_comment=god_comment)
        _maybe_rework(conn, p, god_comment)
        return _get(conn, pid)

    # Atomically claim the pending→approved transition BEFORE charging or executing,
    # so a double-approve can never double-charge or double-execute.
    cur = conn.execute("UPDATE world_prayers SET status='approved', god_comment=?, "
                       "resolved_at=datetime('now') WHERE id=? AND status='pending'",
                       (god_comment, pid))
    conn.commit()
    if cur.rowcount != 1:
        return _get(conn, pid)                         # lost the race — already resolved

    # approved → charge the budget (paid ops) then execute. Kinds whose executor writes
    # its own ledger entry on success (payouts, wallet sends) are NOT pre-charged here.
    cost = int(p["cost_cents"] or 0)
    charged = cost > 0 and p["kind"] not in SELF_LEDGER_KINDS
    if charged:
        src = "etsy" if p["kind"] == "post_etsy" else ("printify" if p["kind"] == "post_printify" else "spend")
        _ledger(conn, -cost, "spend", source=src, note=p["title"], prayer_id=pid)

    try:
        result = _execute(conn, _get(conn, pid))
        conn.execute("UPDATE world_prayers SET status='done', result=? WHERE id=?",
                     (str(result)[:1000], pid))
    except Exception as e:
        logger.exception("prayer %s execute failed", pid)
        conn.execute("UPDATE world_prayers SET status='failed', result=? WHERE id=?",
                     (f"error: {e}"[:1000], pid))
        # The budget was debited before execute — on failure, refund it so a failed
        # payment doesn't leave a phantom debit on the ledger.
        if charged:
            _ledger(conn, cost, "refund", source="refund",
                    note=f"refund (execute failed): {p['title']}", prayer_id=pid)
    conn.commit()
    _learn_verdict(conn, pid, approve, god_comment)
    return _get(conn, pid)


def _learn_verdict(conn, pid, approve, god_comment):
    """A HUMAN verdict (not the auto sweep / dedupe) is god's taste made visible:
    feed the taste model, and let the creator FEEL the judgement (mood + journal),
    so approval literally trains the town."""
    gc = (god_comment or "")
    if gc.startswith("auto") or gc.startswith("duplicate"):
        return
    try:
        p = _get(conn, pid)
        if not p:
            return
        import world_taste
        world_taste.add_example(conn, f"prayer:{pid}", p["kind"],
                                f"{p['title']}. {p['detail'] or ''}",
                                1.0 if approve else -1.0, "god_verdict")
        conn.commit()
        if p.get("agent_name"):
            row = conn.execute("SELECT key, name FROM world_agents WHERE name=?",
                               (p["agent_name"],)).fetchone()
            if row:
                import world_mood
                from world_defs import log_agent
                from world_balance import BLESS_BUFF_SEC, BLESS_NEED_LIFT
                if approve:
                    # GOD LIFTS THEM UP: needs surge toward green + a 1h blessed
                    # buff (+25% pay/xp) — the counterweight to the company grind.
                    conn.execute(
                        "UPDATE world_agents SET blessed_until=?, "
                        "energy=MIN(100,COALESCE(energy,60)+?), fun=MIN(100,COALESCE(fun,60)+?), "
                        "social=MIN(100,COALESCE(social,60)+?), fulfillment=MIN(100,COALESCE(fulfillment,60)+?) "
                        "WHERE key=?",
                        (time.time() + BLESS_BUFF_SEC, BLESS_NEED_LIFT, BLESS_NEED_LIFT,
                         BLESS_NEED_LIFT, BLESS_NEED_LIFT, row["key"]))
                    world_mood.add_thought(conn, row["key"], "god blessed my work", 10, hours=24, unique=True)
                    log_agent(row["key"], row["name"], f"🙌 God BLESSED my work: “{p['title'][:60]}”. I'll make more like this.")
                    # a light morale ripple through the whole town — god walked among them
                    for r2 in conn.execute("SELECT key FROM world_agents WHERE key!=? AND kind IN ('worker','openclaw')",
                                           (row["key"],)).fetchall():
                        world_mood.add_thought(conn, r2["key"], "god walks among us", 2, hours=6, unique=True)
                else:
                    conn.execute("UPDATE world_agents SET fulfillment=MAX(0,COALESCE(fulfillment,60)-10) WHERE key=?",
                                 (row["key"],))
                    world_mood.add_thought(conn, row["key"], "god turned my work down", -6, hours=18, unique=True)
                    log_agent(row["key"], row["name"], f"😞 God passed on “{p['title'][:60]}”"
                              + (f" — “{gc[:80]}”" if gc not in ("approved", "rejected") else "") + ". Adjusting my approach.")
                conn.commit()
    except Exception:
        logger.exception("verdict learning failed")


def _maybe_rework(conn, p, reason):
    """On rejecting a CREATIVE piece, have the agent rework it with your feedback and
    re-file it for judging (the reject → tweak loop). Images only, for now."""
    try:
        if not p or p["kind"] not in ("publish_wordpress",):
            return
        gc = (reason or "")
        if gc.startswith("auto") or gc.startswith("duplicate"):
            return
        import json as _json
        payload = _json.loads(p["payload"]) if p.get("payload") else {}
        if payload.get("type") != "image":
            return
        base = payload.get("prompt") or p["title"]
        ptype = "Art"
        gid = payload.get("gen_id")
        if gid:
            r = conn.execute("SELECT product_type FROM generations WHERE id=?", (gid,)).fetchone()
            if r and r["product_type"]:
                ptype = r["product_type"]
        import threading
        import world_auto
        threading.Thread(target=world_auto.rework_image,
                         args=(base, ptype, p.get("agent_name"), reason or ""),
                         daemon=True).start()
    except Exception:
        logger.exception("rework-on-reject failed")


# ── budget-mode sweep (drains the pending queue) ─────────────────────────────
# pray() only auto-runs at filing time, so anything filed while the mode was
# "review" (or while over budget) sits 'pending' forever even after you flip to
# budget mode. The sweep is the missing half of autonomy: called on a cadence
# (world_auto loop), it deduplicates then auto-approves the oldest free /
# affordable non-gated prayers a few at a time, so a backlog drains gradually
# instead of slamming WordPress in one burst.
def dedupe_pending(conn=None):
    """Reject older pending duplicates (same kind+title), keeping the newest."""
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        dupes = conn.execute(
            "SELECT id FROM world_prayers p WHERE status='pending' AND EXISTS ("
            "  SELECT 1 FROM world_prayers q WHERE q.status='pending'"
            "  AND q.kind=p.kind AND q.title=p.title AND q.id>p.id)").fetchall()
        for r in dupes:
            conn.execute("UPDATE world_prayers SET status='rejected', "
                         "god_comment='duplicate of a newer request', "
                         "resolved_at=datetime('now') WHERE id=?", (r["id"],))
        conn.commit()
        return len(dupes)
    finally:
        if own:
            conn.close()


def sweep_pending(limit=2, conn=None):
    """In budget mode, auto-run up to `limit` of the oldest pending prayers that
    are free (or affordable) and not ALWAYS_GATE. Returns how many ran."""
    if automation_mode() != "budget":
        return 0
    own = conn is None
    if own:
        conn = get_conn()
    try:
        dedupe_pending(conn)
        gk = gated_kinds() or {"__none__"}
        gate = ",".join("?" * len(gk))
        rows = conn.execute(
            f"SELECT id, cost_cents, taste, boss_ok, mayor_ok FROM world_prayers "
            f"WHERE status='pending' AND kind NOT IN ({gate}) ORDER BY id ASC LIMIT 12",
            tuple(gk)).fetchall()
        ran = 0
        tmin = taste_min()
        for r in rows:
            if ran >= limit:
                break
            taste, bok, mok = r["taste"], r["boss_ok"], r["mayor_ok"]
            if taste is None:                              # filed before endorsements existed
                taste, bok, mok = _endorse(conn, r["id"])
            if not (bok and mok and float(taste) >= tmin):
                continue                                   # the checks say: wait for god
            cost = int(r["cost_cents"] or 0)
            if cost == 0 or can_spend(conn, cost):
                _resolve(conn, r["id"], approve=True, god_comment="auto (budget sweep)")
                ran += 1
        return ran
    finally:
        if own:
            conn.close()


# ── executor registry ────────────────────────────────────────────────────────
# Chunk 1 ships the framework + a safe default. Later chunks register real
# handlers (WordPress publish, media generation, Etsy post) by adding to EXECUTORS.
def _exec_default(conn, prayer):
    note(f"🙏 Answered: {prayer['title']}", kind="info",
         from_agent=prayer.get("agent_name"), conn=conn)
    return "acknowledged (no live handler yet)"


EXECUTORS = {}   # kind -> fn(conn, prayer) -> result str


def register_executor(kind, fn):
    EXECUTORS[kind] = fn


def _execute(conn, prayer):
    fn = EXECUTORS.get(prayer["kind"], _exec_default)
    return fn(conn, prayer)


# ── public API surface (dicts for JSON) ──────────────────────────────────────
def summary(conn):
    ensure(conn)
    bal = balance_cents(conn)
    pend = conn.execute("SELECT COUNT(*) AS n FROM world_prayers WHERE status='pending'").fetchone()["n"]
    unseen = conn.execute("SELECT COUNT(*) AS n FROM world_messages WHERE seen=0").fetchone()["n"]
    ledger = [dict(r) for r in conn.execute(
        "SELECT * FROM world_ops_ledger ORDER BY id DESC LIMIT 12").fetchall()]
    return {
        "balance_cents": bal,
        "owed_cents": max(0, -bal),
        "cap_cents": cap_cents(),
        "cycle_spend_cents": cycle_spend_cents(conn),
        "available_cents": bal + cap_cents(),        # headroom before hitting the cap
        "mode": automation_mode(),
        "pending_prayers": pend,
        "unseen_messages": unseen,
        "ledger": ledger,
        "paypal": {
            "configured": bool(cfg("world_paypal_client_id") and cfg("world_paypal_secret")),
            "mode": cfg("world_paypal_mode"),
            "email": cfg("world_paypal_email"),
        },
    }

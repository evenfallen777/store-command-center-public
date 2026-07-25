"""
The Company — The Republic (survival strategy engine).

The nation's brain. Each cycle it ASSESSES the national state, agents PROPOSE
survival strategies, the republic VOTES, the winner becomes the national plan,
and the plan SPAWNS REAL ACTIONS the company executes. Then it measures and
adapts. This is "compete to survive on the web → make better stuff → more gold
→ learn → plan → act", governed democratically.

Two ways the nation can die, and the engine is built around the tension:
  ☢️  Catastrophe — a reckless change (one bad line nukes the project). Fatal.
      So risky/code strategies NEVER auto-run: they route to ALWAYS_GATE prayers
      (add_software) needing vote + review + the god's approval.
  💀  Stagnation — self-preserving by doing nothing. Worse. Cycles that produce
      no real action decay `standing` (accelerating) and escalate warnings.

Felt-not-fatal economics: a treasury Crisis triggers unrest events + panic
messages + urgency, but destroys nothing.

Decoupled from the world sim (world_sim/world_gov are another agent's). Reads
world_agents read-only to attribute proposals + tally votes; writes only its own
tables + via world_ops / world_auto.
"""
import json, logging, random, threading, time
from deps import get_conn
import world_ops as wo
import world_auto

logger = logging.getLogger("store")

# ── strategy templates by threat. Each: category, title, why, risk, actions ──
# action types: create (make sellable media), research (grow the Bible), affiliate
# (more income streams), cost_cut (defensive), code (RISKY → gated), watch (scout).
POOL = {
    "crisis": [
        {"category": "cost-cut", "title": "Freeze paid listings, lean on free channels", "risk": "low",
         "why": "The reserve is breached. Stop the bleed — publish only to free WordPress/Cults3D until gold recovers.",
         "actions": [{"type": "cost_cut"}, {"type": "create"}]},
        {"category": "hustle", "title": "Emergency art drop to raise gold", "risk": "low",
         "why": "Make sellable pieces fast and get them live to bring money in.",
         "actions": [{"type": "create"}, {"type": "create"}]},
        {"category": "platform", "title": "Court more affiliate partners", "risk": "low",
         "why": "Diversify income so one dry channel can't starve us.",
         "actions": [{"type": "affiliate", "title": "Sign up 2 new affiliate programs"}]},
    ],
    "deficit": [
        {"category": "hustle", "title": "Ship a themed product line", "risk": "low",
         "why": "Turn the studio's output into listed products to close the gap.",
         "actions": [{"type": "create"}, {"type": "affiliate", "title": "Add a matching affiliate bundle"}]},
        {"category": "skill", "title": "Study our best sellers, make more like them", "risk": "low",
         "why": "Learn what already earns and double down.",
         "actions": [{"type": "research", "title": "Research our top-performing styles", "detail": "Analyse what sold and why."}, {"type": "create"}]},
        {"category": "platform", "title": "Expand onto a new free platform", "risk": "med",
         "why": "More shelves = more chances to sell without new cost.",
         "actions": [{"type": "research", "title": "Scout a new free selling platform"}]},
    ],
    "stagnation": [
        {"category": "tool", "title": "Adopt a new creation tool/model", "risk": "med",
         "why": "We've gone quiet. New capability breaks the standstill.",
         "actions": [{"type": "research", "title": "Evaluate a new open model/tool"}, {"type": "create"}]},
        {"category": "hustle", "title": "Launch an experimental hustle", "risk": "med",
         "why": "Try something new — a themed collection, a bundle, a trend piece.",
         "actions": [{"type": "create"}, {"type": "create"}]},
        {"category": "code", "title": "Automate our weakest workflow", "risk": "high",
         "why": "Build a small tool to remove a bottleneck — but code can nuke us, so it goes through full review.",
         "actions": [{"type": "code", "title": "Propose a workflow-automation script"}]},
        {"category": "watch", "title": "Scout stocks / crypto / web trends", "risk": "low",
         "why": "Know what the world is doing so we don't fall behind.",
         "actions": [{"type": "watch", "title": "Research current market + web trends"}]},
    ],
    "growth": [
        {"category": "skill", "title": "Level up the crew's craft", "risk": "low",
         "why": "We're stable — invest in getting better while we can.",
         "actions": [{"type": "research", "title": "Study an advanced technique for the Bible"}, {"type": "create"}]},
        {"category": "platform", "title": "Grow the catalog aggressively", "risk": "low",
         "why": "Momentum is ours. Flood the shelves with quality.",
         "actions": [{"type": "create"}, {"type": "create"}]},
        {"category": "watch", "title": "Explore a bold new frontier (crypto/stocks/AI tools)", "risk": "med",
         "why": "Surplus lets us scout the next big survival edge.",
         "actions": [{"type": "watch", "title": "Deep-dive a new frontier opportunity"}]},
    ],
}

THREAT_LABEL = {"crisis": "🔴 Treasury Crisis", "deficit": "🟡 Running a deficit",
                "stagnation": "💀 Stagnation — we're falling behind", "growth": "🟢 Growth"}


def ensure(conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS world_strategy_state (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS world_strategies (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle       INTEGER,
            threat      TEXT,
            category    TEXT,
            title       TEXT,
            why         TEXT,
            risk        TEXT,
            proposer    TEXT,
            votes_for   INTEGER DEFAULT 0,
            plan        TEXT DEFAULT '{}',
            status      TEXT DEFAULT 'proposed',  -- proposed|adopted|rejected|acted|failed
            actions_run INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now')),
            resolved_at TEXT
        );
        """)
        conn.commit()
    finally:
        if own:
            conn.close()


def _get(conn, key, default=None):
    r = conn.execute("SELECT value FROM world_strategy_state WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def _set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO world_strategy_state (key,value) VALUES (?,?)", (key, str(value)))
    conn.commit()


def _standing(conn):
    try:
        return int(_get(conn, "standing", 50))
    except Exception:
        return 50


def _assess(conn):
    s = wo.summary(conn)
    bal, cap = s["balance_cents"], max(1, s["cap_cents"])
    stag = int(_get(conn, "stagnation", 0))
    if bal < -cap:
        threat = "crisis"
    elif bal < 0:
        threat = "deficit"
    elif stag >= 2:
        threat = "stagnation"
    else:
        threat = "growth"
    return threat, s


def _voters(conn):
    return [dict(r) for r in conn.execute("SELECT name,dept FROM world_agents").fetchall()]


def _oracle_view(conn):
    """(strength, brief) from the oracle tournament's accuracy-weighted consensus.
    ADVISORY ONLY — cited in notes/votes, never an action. (0.0, "") when the
    `oracle_company_hookup` toggle is off, nothing is open, or the oracle errs."""
    try:
        from routers.oracle import consensus as _oc
        if not _oc.hookup_enabled():
            return 0.0, ""
        assets = _oc.compute(conn).get("assets") or []
        strength = sum(abs(a["bias"]) for a in assets) / len(assets) if assets else 0.0
        return strength, _oc.brief(conn=conn)
    except Exception:
        return 0.0, ""


def _with_ops_context(detail, topic=None):
    """Ground a research/scout commission in the Bible's VERIFIED operational
    notes (world_bible.ops_context) so the agents doing the work start from
    real, checked facts about this system — where files live, what changed,
    which tools exist — instead of guessing. Fail-soft: the detail is returned
    unchanged when no verified notes exist or the Bible errs."""
    try:
        import world_bible
        ops = world_bible.ops_context(topic=topic, limit=4)
        if ops:
            return f"{detail}\n\n[Verified system notes]\n{ops}"
    except Exception:
        logger.exception("ops context injection failed")
    return detail


def _spawn(conn, strat, plan):
    """Turn an adopted strategy into real actions. Returns count executed now
    (gated actions like code count as 'queued for your approval', not executed)."""
    executed = 0
    proposer = strat["proposer"]
    for a in plan.get("actions", []):
        t = a.get("type")
        try:
            if t == "create":
                threading.Thread(target=world_auto.run_cycle,
                                 args=(world_auto.pick_kind(), True), daemon=True).start()
                executed += 1
            elif t == "research":
                title = a.get("title", "Research for the Bible")
                wo.pray("library_research", title,
                        detail=_with_ops_context(
                            a.get("detail", "Add findings to the company Bible."), title),
                        cost_cents=0, agent_name=proposer)
                executed += 1
            elif t == "watch":
                title = a.get("title", "Scout the market")
                wo.pray("library_research", title,
                        detail=_with_ops_context(
                            "Scout stocks / crypto / web trends for a survival edge.", title),
                        cost_cents=0, agent_name=proposer)
                executed += 1
            elif t == "affiliate":
                wo.pray("add_affiliate", a.get("title", "Add an affiliate program"),
                        detail="Diversify our income streams.", cost_cents=0, agent_name=proposer)
                executed += 1
            elif t == "cost_cut":
                wo._save_cfg(conn, {"world_ops_automation_mode": "review"})
                wo.note("🛡️ Austerity: paid listings frozen, all spending under review.",
                        kind="warning", from_agent="The Republic", conn=conn)
                executed += 1
            elif t == "code":
                # ☢️ RISKY — one bad line can nuke the project. Full gauntlet.
                wo.pray("add_software", a.get("title", "Proposed code change"),
                        detail="☢️ Risky change — one wrong line could break everything. "
                               "Needs the dev swarm's review AND your approval before it runs.",
                        cost_cents=0, agent_name=proposer)
                wo.note("☢️ A code change is proposed. The elders demand review before we crack this atom.",
                        kind="warning", from_agent="The Republic", conn=conn)
                # gated — not counted as executed (nothing ran yet)
        except Exception:
            logger.exception("strategy action %s failed", t)
    return executed


def run_cycle(conn=None):
    """Assess → propose → vote → adopt → act → measure. Returns the new state."""
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        cycle = int(_get(conn, "cycle", 0)) + 1
        _set(conn, "cycle", cycle)
        threat, summ = _assess(conn)
        voters = _voters(conn)

        # ── propose: 3 strategies from the threat's pool, attributed to agents ──
        templates = list(POOL.get(threat, POOL["growth"]))
        random.shuffle(templates)
        picks = templates[:3]
        strat_ids = []
        for tpl in picks:
            proposer = random.choice(voters)["name"] if voters else "The Assembly"
            plan = {"actions": tpl["actions"]}
            cur = conn.execute(
                "INSERT INTO world_strategies (cycle,threat,category,title,why,risk,proposer,plan,status) "
                "VALUES (?,?,?,?,?,?,?,?, 'proposed')",
                (cycle, threat, tpl["category"], tpl["title"], tpl["why"], tpl["risk"],
                 proposer, json.dumps(plan)))
            strat_ids.append(cur.lastrowid)
        conn.commit()

        # ── vote: each citizen backs the strategy that best fits the moment ──
        # score = threat-fit base + risk aversion in crisis + a little personal variance.
        tally = {sid: 0 for sid in strat_ids}
        rows = {r["id"]: dict(r) for r in conn.execute(
            "SELECT * FROM world_strategies WHERE id IN (%s)" %
            ",".join("?" * len(strat_ids)), strat_ids).fetchall()}
        # god's learned taste tilts the assembly — plans god would bless get a lift
        try:
            import world_taste
            taste_by = {sid: world_taste.score(conn, f"{rows[sid]['title']}. {rows[sid]['why']}")
                        for sid in strat_ids} if world_taste.stats(conn)["trained"] else {}
        except Exception:
            taste_by = {}
        # the oracle's read on the market — a moving market makes scouting timely
        orc_strength, orc_brief = _oracle_view(conn)
        for _v in voters or [{"name": "Assembly"}]:
            best, bscore = None, -1
            for sid in strat_ids:
                st = rows[sid]
                score = 1.0
                if threat in ("crisis", "deficit") and st["risk"] == "low":
                    score += 1.2                       # play it safe when broke
                if threat == "stagnation" and st["risk"] in ("med", "high"):
                    score += 1.0                       # boldness breaks standstill
                if st["category"] in ("hustle", "create", "platform"):
                    score += 0.4
                if st["category"] == "watch" and orc_strength > 0.25:
                    score += 0.8               # oracle consensus says the market's moving — scout it
                score += (taste_by.get(sid, 0.5) - 0.5) * 1.6   # lean toward god's taste
                score += random.random() * 0.8         # personal conviction
                if score > bscore:
                    best, bscore = sid, score
            tally[best] += 1
        for sid, v in tally.items():
            conn.execute("UPDATE world_strategies SET votes_for=? WHERE id=?", (v, sid))
        conn.commit()

        winner_id = max(tally, key=tally.get)
        winner = dict(conn.execute("SELECT * FROM world_strategies WHERE id=?", (winner_id,)).fetchone())

        # losers → rejected
        for sid in strat_ids:
            if sid != winner_id:
                conn.execute("UPDATE world_strategies SET status='rejected', resolved_at=datetime('now') WHERE id=?", (sid,))
        conn.commit()

        # ── act on the mandate ──
        if orc_brief:
            wo.note(f"🔮 {orc_brief}", kind="info", from_agent="The Oracle", conn=conn)
        plan = json.loads(winner["plan"] or "{}")
        executed = _spawn(conn, winner, plan)
        conn.execute("UPDATE world_strategies SET status='acted', actions_run=?, resolved_at=datetime('now') WHERE id=?",
                     (executed, winner_id))
        _set(conn, "current_plan_id", winner_id)
        conn.commit()

        # ── measure: standing + stagnation + felt stakes ──
        standing = _standing(conn)
        stag = int(_get(conn, "stagnation", 0))
        if executed > 0:
            standing = min(100, standing + 3 + executed)
            stag = 0
            wo.note(f"🏛️ The Republic adopted: “{winner['title']}” — and we act. ({executed} moves underway)",
                    kind="praise", from_agent="The Republic", conn=conn)
        else:
            stag += 1
            standing = max(0, standing - (2 + stag))   # doing nothing is worse — accelerating decay
            wo.note(f"💀 We deliberated but did nothing real. Standing falls. Stagnation {stag}.",
                    kind="warning", from_agent="The Republic", conn=conn)
        if threat == "crisis":
            standing = max(0, standing - 4)
            wo.note("🔴 Treasury breached — unrest in the streets. Raiders eye a weak nation. We must earn, now.",
                    kind="warning", from_agent="The People", conn=conn)
        _set(conn, "standing", standing)
        _set(conn, "stagnation", stag)
        _set(conn, "last_cycle", int(time.time()))
        return state(conn)
    finally:
        if own:
            conn.close()


def override(conn, strategy_id, decision):
    """God overrides the vote: force-adopt (and act) or reject a strategy."""
    ensure(conn)
    st = conn.execute("SELECT * FROM world_strategies WHERE id=?", (strategy_id,)).fetchone()
    if not st:
        return None
    st = dict(st)
    if decision == "reject":
        conn.execute("UPDATE world_strategies SET status='rejected', resolved_at=datetime('now') WHERE id=?", (strategy_id,))
        conn.commit()
    elif decision == "adopt":
        plan = json.loads(st["plan"] or "{}")
        executed = _spawn(conn, st, plan)
        conn.execute("UPDATE world_strategies SET status='acted', actions_run=?, resolved_at=datetime('now') WHERE id=?",
                     (executed, strategy_id))
        _set(conn, "current_plan_id", strategy_id)
        conn.commit()
    return state(conn)


def state(conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        threat, summ = _assess(conn)
        standing = _standing(conn)
        stag = int(_get(conn, "stagnation", 0))
        cur_id = _get(conn, "current_plan_id")
        current = None
        if cur_id:
            r = conn.execute("SELECT * FROM world_strategies WHERE id=?", (cur_id,)).fetchone()
            current = dict(r) if r else None
        recent = [dict(r) for r in conn.execute(
            "SELECT * FROM world_strategies ORDER BY id DESC LIMIT 12").fetchall()]
        last_cycle = _get(conn, "last_cycle")
        _, orc_brief = _oracle_view(conn)
        return {
            "oracle_brief": orc_brief,          # advisory oracle consensus ('' when hookup off)
            "standing": standing,
            "stagnation": stag,
            "threat": threat,
            "threat_label": THREAT_LABEL.get(threat, threat),
            "cycle": int(_get(conn, "cycle", 0)),
            "treasury": {"balance_cents": summ["balance_cents"], "owed_cents": summ["owed_cents"],
                         "cap_cents": summ["cap_cents"]},
            "current_plan": current,
            "recent": recent,
            "last_cycle": int(last_cycle) if last_cycle else 0,
        }
    finally:
        if own:
            conn.close()

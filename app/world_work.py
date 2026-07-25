"""THE COMPANY — Work-Priority scheduler (RimWorld-style).

The town is run by POLICY, not micro-management. Each agent has a priority for
every WORK TYPE (0 = never, 1 = highest … 4 = lowest). When an agent is free it
scans work types in priority order and takes the FIRST one that has an available
job right now — real department work, construction, research, or a gathering
skill, else it relaxes. This replaces the opaque `world_learn` bandit with a
player-legible control surface (the bandit survives as the optional "Auto" policy).

Columns are ordered left→right by default importance; the priority NUMBER dominates,
column order only breaks ties (RimWorld §2.3). Storage: world_work_priority.
"""
from deps import get_setting
from world_defs import mget
import world_skills as WS
import world_tech as WT
import world_construct as WC

# work types in default left→right importance order
WORK_TYPES = ["operate", "produce", "build", "research", "mine", "woodcut", "farm", "fish", "hunt", "help", "create", "relax"]
WT_META = {
    "operate":  {"label": "Operate",   "icon": "💼", "skill": None},          # run their department's real job
    "produce":  {"label": "Produce",    "icon": "🏭", "skill": None},          # fill open production BILLS (world_bills)
    "build":    {"label": "Construct",  "icon": "🔨", "skill": "construction"},
    "research": {"label": "Research",   "icon": "📖", "skill": "knowledge"},
    "mine":     {"label": "Mine",       "icon": "⛏️", "skill": "mining"},
    "woodcut":  {"label": "Woodcut",    "icon": "🪓", "skill": "woodcutting"},
    "farm":     {"label": "Farm",       "icon": "🌾", "skill": "farming"},
    "fish":     {"label": "Fish",       "icon": "🎣", "skill": "fishing"},
    "hunt":     {"label": "Hunt",       "icon": "🏹", "skill": "hunting"},
    "help":     {"label": "Assist",     "icon": "🤝", "skill": "construction"},   # lend a hand on the town works
    "create":   {"label": "Create",     "icon": "🎨", "skill": None},             # commission their own art
    "relax":    {"label": "Relax",      "icon": "🍺", "skill": None},
}
GATHER_WT = {"mine": "mine", "woodcut": "woodcut", "farm": "farm", "fish": "fish", "hunt": "hunt"}
LEISURE = ["bar", "arcade", "tv", "park", "cafe"]


def _ensure(c):
    c.execute("""CREATE TABLE IF NOT EXISTS world_work_priority(
        agent_key TEXT, work_type TEXT, priority INTEGER,
        PRIMARY KEY(agent_key, work_type))""")
    # God-Panel WorkGiver directives: the OWNER pins a priority for a work type
    # on a whole DEPARTMENT or a single AGENT. Overlaid on top of the normal
    # priorities ONLY while world_workgiver_enabled is on (default OFF =
    # behavior identical to before). Agent directives beat dept directives.
    c.execute("""CREATE TABLE IF NOT EXISTS world_work_directives(
        scope TEXT NOT NULL,               -- 'dept' | 'agent'
        target TEXT NOT NULL,              -- dept key / agent_key
        work_type TEXT NOT NULL,
        priority INTEGER NOT NULL,         -- 0 = never … 4 = lowest
        note TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY(scope, target, work_type))""")


# ── God-Panel WorkGiver (owner work-priority directives; default OFF) ────────
WORKGIVER_KEY = "world_workgiver_enabled"


def workgiver_enabled():
    return str(get_setting(WORKGIVER_KEY, "0")).lower() in ("1", "true", "yes", "on")


def directives(c):
    _ensure(c)
    return [dict(r) for r in c.execute(
        "SELECT scope, target, work_type, priority, note, created_at "
        "FROM world_work_directives ORDER BY scope, target, work_type").fetchall()]


def set_directive(c, scope, target, work_type, priority, note=None):
    _ensure(c)
    if scope not in ("dept", "agent") or work_type not in WORK_TYPES or not target:
        return False
    priority = max(0, min(4, int(priority)))
    c.execute("""INSERT INTO world_work_directives(scope,target,work_type,priority,note)
        VALUES(?,?,?,?,?)
        ON CONFLICT(scope,target,work_type) DO UPDATE SET priority=?, note=?""",
              (scope, target, work_type, priority, note, priority, note))
    return True


def clear_directive(c, scope, target, work_type):
    _ensure(c)
    c.execute("DELETE FROM world_work_directives WHERE scope=? AND target=? AND work_type=?",
              (scope, target, work_type))
    return True


# ── default priorities: real work first, then the agent's primary skill, then chores ──
def default_priority(agent, wt):
    if wt == "operate":
        return 1
    if wt == "produce":
        return 2
    if wt == "research":
        return 3
    if wt == "build":
        return 3
    if wt == "relax":
        return 4
    # gathering skills: the agent's primary gets 2, the rest 3
    sk = WT_META[wt]["skill"]
    return 2 if (sk and sk == WS.primary_skill(agent)) else 3


def get_priorities(c, agent):
    _ensure(c)
    stored = {r[0]: int(r[1]) for r in c.execute(
        "SELECT work_type, priority FROM world_work_priority WHERE agent_key=?", (agent["key"],)).fetchall()}
    prio = {wt: stored.get(wt, default_priority(agent, wt)) for wt in WORK_TYPES}
    # WorkGiver overlay — ONLY when the owner's master toggle is on (default
    # OFF ⇒ exactly the pre-WorkGiver behavior). Dept directives apply first,
    # then agent directives override them.
    if workgiver_enabled():
        try:
            rows = c.execute(
                "SELECT scope, work_type, priority FROM world_work_directives "
                "WHERE (scope='dept' AND target=?) OR (scope='agent' AND target=?)",
                (agent.get("dept") or "", agent["key"])).fetchall()
            for r in sorted(rows, key=lambda r: r["scope"] != "dept"):   # dept first, agent wins
                if r["work_type"] in prio:
                    prio[r["work_type"]] = max(0, min(4, int(r["priority"])))
        except Exception:
            pass
    return prio


def set_priority(c, agent_key, work_type, priority):
    _ensure(c)
    priority = max(0, min(4, int(priority)))
    if work_type not in WORK_TYPES:
        return
    c.execute("""INSERT INTO world_work_priority(agent_key, work_type, priority) VALUES(?,?,?)
        ON CONFLICT(agent_key, work_type) DO UPDATE SET priority=?""", (agent_key, work_type, priority, priority))


# ── WorkGivers: is there an available job of this type for this agent, right now? ──
def _wg_operate(c, agent, ctx):
    dept = agent.get("dept") or "trends"
    if dept == "netsec":
        # NON-COMBAT remediation: the netsec desk works its own low/med backlog on
        # the normal cadence instead of waiting on a raid (world_security fix for
        # the queue-starvation bug — raids fire rarely and only clear 4/raid LIFO).
        try:
            import world_security
            n = world_security.remediate_backlog(c, agent)
            if n:
                return {"work_type": "operate", "state": "working", "location": "desk:netsec",
                        "goal": f"patching {n} low/med finding(s)", "skill": None}
        except Exception:
            pass
    # CONTENT DEPTS (social / image / video / audio / 3D): "operating" means
    # actually MAKING something. Draw one enabled dept-appropriate capability
    # from the catalog (world_caps.agent_work — gated on world_caps_auto + the
    # per-cap gates, active hours, and throttled to one product in flight on
    # the single GPU) → a real generation / draft post / storyboard / mesh.
    # This kills the idle-desk bug: no more social "on the clock" with nothing
    # completing, no more visuals "working" over an empty queue.
    try:
        import world_caps
        if dept in world_caps.DEPT_CAPS:
            job = world_caps.agent_work(c, agent)
            if job:
                return {"work_type": "operate", "state": "working", "location": f"desk:{dept}",
                        "goal": job["goal"], "skill": None}
            if ctx.get("has_work"):
                # something REAL is in flight for this dept (queued render, a
                # fresh draft in its window) — tending it is honest desk work.
                return {"work_type": "operate", "state": "working", "location": f"desk:{dept}",
                        "goal": f"tending the {dept} queue", "skill": None}
            return None       # gated/throttled + empty queue → next priority, not fake work
    except Exception:
        pass
    if ctx.get("has_work"):
        return {"work_type": "operate", "state": "working", "location": f"desk:{dept}", "goal": "on the clock", "skill": None}
    return None


def _wg_produce(c, agent, ctx):
    """Fill an open production bill (world_bills): agents work their dept desk
    while stock is below target. Also gives bills their (toggled, throttled)
    chance to kick a REAL world_auto creation."""
    try:
        import world_bills
        bill = world_bills.job_for(c, agent)
        if not bill:
            return None
        world_bills.maybe_drive(c, ctx.get("t"))
        dept = world_bills.KINDS[bill["kind"]]["dept"]
        return {"work_type": "produce", "state": "working", "location": f"desk:{dept}",
                "goal": f"filling the bill: {bill['label']} ({bill['count']}/{bill['target']})",
                "skill": None}
    except Exception:
        return None


def _wg_build(c, agent, ctx):
    try:
        if WC.has_work(c):
            snap = WC.snapshot(c)
            cur = snap.get("current")
            n = snap.get("in_flight", 0)
            goal = (f"working the {cur['name']}" if cur else "on the build site") + (f" (+{n-1} more)" if n > 1 else "")
            return {"work_type": "build", "state": "skilling", "location": "build", "goal": goal, "skill": "construction"}
    except Exception:
        pass
    return None


def _wg_research(c, agent, ctx):
    try:
        if WT.tier_index(c) < len(WT.TIERS) - 1:               # tech not maxed → research pays
            return {"work_type": "research", "state": "studying", "location": "library", "goal": "researching at the library", "skill": "knowledge"}
    except Exception:
        pass
    return None


def _wg_gather(wt):
    skill = WT_META[wt]["skill"]
    node, phrase, _res, _emoji = WS.SKILL_META[skill]
    def giver(c, agent, ctx):
        return {"work_type": wt, "state": "skilling", "location": node, "goal": phrase, "skill": skill}
    return giver


def _wg_relax(c, agent, ctx):
    spot = LEISURE[(agent["id"] + int(ctx.get("t", 0) / 45)) % len(LEISURE)]
    return {"work_type": "relax", "state": "leisure", "location": spot, "goal": "taking a break in town", "skill": None}


def _wg_help(c, agent, ctx):
    """ASSIST: pitch in on the town's construction project — helping is a
    pickable job, credited on the assist board (rate-limited by the dwell)."""
    try:
        import world_rank
        world_rank.add_assist(c, agent["key"], "townwork")
    except Exception:
        pass
    return {"work_type": "help", "state": "skilling", "location": "build",
            "goal": "lending a hand on the town works", "skill": "construction"}


def _wg_create(c, agent, ctx):
    """CREATE: commission a personal piece (their coins; all GPU guards inside).
    Refusals (cooldown / broke / queue busy) fall through to the next priority.
    The learn ledger records that creating paid, so it spreads when it works."""
    try:
        import world_build, world_learn
        if world_build.personal_create(c, agent):
            world_learn.record_reward(c, agent["key"], "create", 12)
            return {"work_type": "create", "state": "leisure", "location": "arcade",
                    "goal": "celebrating a fresh commission", "skill": None}
    except Exception:
        pass
    return None


WORKGIVERS = {
    "operate": _wg_operate, "produce": _wg_produce, "build": _wg_build, "research": _wg_research,
    "mine": _wg_gather("mine"), "woodcut": _wg_gather("woodcut"),
    "farm": _wg_gather("farm"), "fish": _wg_gather("fish"), "hunt": _wg_gather("hunt"),
    "help": _wg_help, "create": _wg_create, "relax": _wg_relax,
}


# ── the scheduler: first available job in priority order (number dominates, column breaks ties) ──
# Gather work is STOCK-AWARE (Colony-Manager style): a resource below its floor becomes
# urgent (effective priority 1); at/above its ceiling that gather is skipped (stop hoarding).
def choose_work(c, agent, ctx):
    prio = get_priorities(c, agent)
    sp = WS.stockpile(c)
    targets = WS.stock_targets(c)

    def eff(wt):
        p = prio.get(wt, 0)
        if p == 0:
            return None
        sk = WT_META[wt]["skill"]
        if wt in GATHER_WT and sk:
            res = WS.resource_of(sk)
            t = targets.get(res) if res else None
            if t:
                have = int(sp.get(res, 0))
                if have >= t["ceil"]:
                    return None                                # stock full → don't gather this
                if have < t["floor"]:
                    return 1                                   # stock low → urgent
        return p

    order = sorted((wt for wt in WORK_TYPES if eff(wt) is not None),
                   key=lambda wt: (eff(wt), WORK_TYPES.index(wt)))
    for wt in order:
        job = WORKGIVERS[wt](c, agent, ctx)
        if job:
            return job
    return _wg_relax(c, agent, ctx)                             # nothing enabled/available → relax

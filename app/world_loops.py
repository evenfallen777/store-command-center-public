"""
The Company — the agent-loops GRAPH.

One read-only endpoint's worth of logic: assemble the live orchestration
picture — every autonomous LOOP (who drives it, its cadence/schedule, whether
it's on), every NODE (the operators, the leaders, the departments with their
resident agents + capabilities, the products), and every EDGE (command /
delegation / prayer→approval / endorsement / review / vote / drive / produce)
— from the REAL settings + tables. Nothing here mutates anything; it's the
map, not the territory. Consumed by static/js/world-loops.js (the 🕸 Loops
console tab).

Operator tier (layer 0): the human owner "God" sits in the CENTER with the
TWO LIEUTENANTS mirrored on either side — "Jesus" above (world_jesus.py, the
constructive/best-case delegate, world_jesus_enabled DEFAULT OFF) and "Satan"
below (world_satan.py, the adversarial/worst-case red team,
world_satan_enabled DEFAULT OFF). Both mirror God's position edge-for-edge:
same authority/approval/review relationships, peers whose only differences
are their toggles, their opposite polarities (best case vs worst case — the
hi-lo band of world_duality.py), and the unchanged human-only hard floor.

LIVE FLOW: every node carries `activity` (last action + when, count in the
last hour, active-vs-idle) and the payload carries a time-anchored `flow`
stream — who prayed/approved/rejected/endorsed/invoked/voted WHAT, WHEN — so
the static counts read as live movement. Loops carry `last_ts` from their
persisted cadence stamps.

Data model (JSON):
  now   : server epoch seconds (anchor for every ts below)
  loops : [{id,label,icon,in_charge,cadence:{enabled,label,detail},desc,
            drives:[node ids],last_ts?}]
  nodes : [{id,type:'human'|'leader'|'system'|'dept'|'role'|'product',label,icon,layer,
            agents?:[…], caps?:[…], stats?:{…},
            activity?:{last_ts,ago_sec,count_1h,active,last}}]
  edges : [{from,to,kind:'commands'|'endorses'|'approves'|'prays'|'reviews'|
            'votes'|'drives'|'produces'|'delegates',label?}]
  flow  : [{ts,ago_sec,who,node?,verb,what,status?}]   (newest first)
"""
import json
import time

import naming_theme as nt          # display-label theming ONLY (app/naming_theme.py):
                                   # themed default = today's strings exactly; ids unchanged
from deps import get_conn, get_setting

_T = lambda v: str(v).lower() in ("1", "true", "yes", "on")


def _g(k, d=""):
    return get_setting(k, d)


def _f(k, d=0.0):
    try:
        return float(_g(k, str(d)) or d)
    except Exception:
        return d


# dept → the product node it ships to (only depts that ship a concrete product)
_DEPT_PRODUCT = {
    "image": "p_image", "video": "p_video", "audio": "p_music", "models3d": "p_3d",
    "social": "p_social", "storefront": "p_listing", "resell": "p_listing",
    "publishing": "p_publish", "research": "p_research", "trends": "p_research",
}

# prayer kind → the product node a DONE prayer shipped to
_KIND_PRODUCT = {
    "publish_wordpress": "p_publish", "publish_cults3d": "p_publish",
    "post_etsy": "p_listing", "post_printify": "p_listing",
    "social_publish": "p_social", "library_research": "p_research",
}

# dev-swarm ROLE nodes (the coding pipeline as first-class graph citizens):
# id, icon+label, what the seat does. Activity comes from swarm_events.agent.
_SWARM_ROLES = [
    ("r_architect", "🏗️", "Architect",
     "Decomposes the request: enhanced spec, scoped subtasks, the build plan."),
    ("r_planner", "🗺️", "Planner",
     "Turns the spec into ordered build steps (asks clarifying questions first)."),
    ("r_coder", "⌨️", "Coder",
     "Writes the diff on the dev branch — in-scope paths only."),
    ("r_reviewer", "🧐", "Reviewer",
     "Votes approve/reject on every diff (plus an auditor seat on big jobs)."),
    ("r_tester", "🧪", "Tester",
     "Runs the suite; failures bounce the job back to the coder."),
]


def _swarm_role_node(agent):
    """swarm_events.agent (planner|coder1|reviewer2|auditor|tester|…) → role node id."""
    a = (agent or "").lower()
    for prefix, node in (("architect", "r_architect"), ("plan", "r_planner"),
                         ("cod", "r_coder"), ("review", "r_reviewer"),
                         ("audit", "r_reviewer"), ("test", "r_tester")):
        if a.startswith(prefix):
            return node
    return None


_PRODUCTS = [
    ("p_image",    "🖼️ Images"),
    ("p_video",    "🎬 Video / Studio films"),
    ("p_music",    "🎵 Music & audio"),
    ("p_3d",       "🧊 3D models"),
    ("p_social",   "📣 Social posts (YT/TikTok)"),
    ("p_listing",  "🛍️ Listings (Etsy/Printify/resell)"),
    ("p_publish",  "🌐 Gallery publishes (WP/Cults3D)"),
    ("p_research", "📖 Research & trends"),
]


def _loops(c):
    """Every autonomous loop, with its LIVE schedule from the native settings
    + its last-fired stamp (the same persisted stamps the daemons use)."""
    interval = _g("world_auto_interval_min", "120")
    a_s, a_e = _g("world_auto_active_start", "8"), _g("world_auto_active_end", "22")
    kinds = _g("world_auto_kinds", "image")
    govern = _g("world_auto_govern_min", "360")
    sell_int = _g("world_sell_interval_min", "180")
    caps_int = _g("world_caps_auto_interval_min", "240")
    jesus_int = _g("world_jesus_interval_min", "30")
    satan_int = _g("world_satan_interval_min", "30")
    loops = [
        {"id": "loop_create", "label": "Creation loop", "icon": "🎨",
         "in_charge": "world_auto daemon (the Studio autopilot)",
         "cadence": {"enabled": _T(_g("world_auto_enabled", "0")),
                     "label": f"every {interval} min, {a_s}–{a_e}h",
                     "detail": f"makes: {kinds} · backpressure: pauses at 6 pending publish prayers"},
         "desc": "Creative agents make media on the GPU and file publish prayers.",
         "drives": ["d_image", "d_audio", "d_video", "d_models3d"],
         "last_ts": _f("world_auto_t_last_run")},
        {"id": "loop_jesus",
         "label": nt.pick("Jesus — delegated operator",
                          "Constructive Lieutenant — delegated operator"),
         "icon": nt.icon("jesus"),
         "in_charge": nt.pick("Jesus (the owner's toggle-gated stand-in)",
                              "the Constructive Lieutenant (the owner's toggle-gated stand-in)"),
         "cadence": {"enabled": _T(_g("world_jesus_enabled", "0")),
                     "label": f"≤1 action per {jesus_int} min, {a_s}–{a_e}h",
                     "detail": "routine prayer verdicts + already-enabled caps only; "
                               "real money / code / illegal-content floor stays human"},
         "desc": "The owner's operator seat on autopilot — pure delegation, zero new privilege.",
         "drives": ["sys_gate"],
         "last_ts": _f("world_jesus_last")},
        {"id": "loop_satan",
         "label": nt.pick("Satan — adversarial red team",
                          "Adversarial Lieutenant — red team"),
         "icon": nt.icon("satan"),
         "in_charge": nt.pick("Satan (the owner's toggle-gated worst-case lieutenant)",
                              "the Adversarial Lieutenant (the owner's toggle-gated worst-case seat)"),
         "cadence": {"enabled": _T(_g("world_satan_enabled", "0")),
                     "label": f"≤1 action per {satan_int} min, {a_s}–{a_e}h",
                     "detail": "routine rejects + risk flags + already-enabled caps only; "
                               "never approves, never moves money; the illegal-content "
                               "floors are always-on and outside his reach"},
         "desc": nt.pick("Jesus's mirror: worst-case predictions and red-team reviews — the low "
                         "side of every hi-lo band.",
                         "The constructive seat's mirror: worst-case predictions and red-team "
                         "reviews — the low side of every hi-lo band."),
         "drives": ["sys_gate"],
         "last_ts": _f("world_satan_last")},
        {"id": "loop_govern", "label": "Republic self-governance", "icon": "⚖️",
         "in_charge": "The Republic (assembly of all agents)",
         "cadence": {"enabled": int(govern or 0) > 0 if str(govern).isdigit() else False,
                     "label": f"every {govern} min" if str(govern) != "0" else "never",
                     "detail": "assess → propose → vote → plan → real actions (risky ones gated)"},
         "desc": "The nation's brain: survival strategy voted democratically.",
         "drives": ["sys_republic"],
         "last_ts": _f("world_auto_t_last_govern")},
        {"id": "loop_cognition", "label": "Crew cognition", "icon": "💭",
         "in_charge": "world_gov (batched on the sim ticker)",
         "cadence": {"enabled": _T(_g("world_llm_enabled", "1")),
                     "label": "batched with the sim tick",
                     "detail": "thoughts + opinions ride the orch LLM queue on a single model load"},
         "desc": "Agents form thoughts and improvement ideas.",
         "drives": ["sys_meeting"]},
        {"id": "loop_meetings", "label": "Town meetings", "icon": "🗳️",
         "in_charge": "Mayor Vex (civic)",
         "cadence": {"enabled": _T(_g("world_meetings_enabled", "1")),
                     "label": "periodic (sim ticker)",
                     "detail": "each agent casts one weighted vote → active directive"},
         "desc": "The crew votes the single top priority into the mandate.",
         "drives": ["sys_meeting"]},
        {"id": "loop_desk", "label": "Proposal desk", "icon": "🗞️",
         "in_charge": "Proposal desk (feeds + crew suggestions + Etsy signal)",
         "cadence": {"enabled": _T(_g("proposal_desk_enabled", "0")),
                     "label": f"every {_g('proposal_desk_interval_min', '240')} min",
                     "detail": "lanes: " + _g("proposal_lanes_enabled", "humor,news,tech,gaming,evergreen,market")
                               + " · news/Reddit/Google feeds + agent 'Suggested:' ideas + Etsy demand → lane-tagged proposals"},
         "desc": "The intake desk: categorized feeds, the crew's ideas and live Etsy demand become product proposals.",
         "drives": ["sys_desk"],
         "last_ts": _f("proposal_desk_last_run")},
        {"id": "loop_propgate", "label": "Proposal review gate", "icon": "🚦",
         "in_charge": "LLM judge (proposal_gate)",
         "cadence": {"enabled": _T(_g("proposal_gate_enabled", "0")),
                     "label": f"every {_g('proposal_gate_interval_min', '360')} min · "
                              f"{_g('proposal_gate_mode', 'auto_weed')} · "
                              f"batch {_g('proposal_gate_batch_size', '10')}",
                     "detail": "local dedup, then a 0-100 judge score weighing the live Etsy market signal; "
                               "weeds, holds for a human, or (full_auto) queues generations"},
         "desc": "Every pending proposal is judged before it can burn a GPU minute.",
         "drives": ["sys_propgate"],
         "last_ts": _f("proposal_gate_last_run")},
        {"id": "loop_mail", "label": "Mail auto-reply gate", "icon": "📬",
         "in_charge": "Mail desk (Miles, w_mail_1)",
         "cadence": {"enabled": _T(_g("mail_gate_enabled", "0")),
                     "label": f"every {_g('mail_gate_interval_min', '15')} min · "
                              f"{_g('mail_gate_mode', 'manual')} · "
                              f"batch {_g('mail_gate_batch_size', '5')}",
                     "detail": "classify → FAQ/order match → profile-driven draft; full_auto "
                               f"sends only routine mail at ≥{_g('mail_gate_confidence', '80')} "
                               "confidence — quotes/pricing ALWAYS hold for a human"},
         "desc": "The mail room triages customer email; every action leaves a review trail.",
         "drives": ["d_mail"],
         "last_ts": _f("mail_gate_last_run")},
        {"id": "loop_sell", "label": "Autonomous listing", "icon": "🛍️",
         "in_charge": "world_auto daemon → Sell desk",
         "cadence": {"enabled": _T(_g("world_sell_auto", "0")),
                     "label": f"every {sell_int} min → {_g('world_sell_channel', 'etsy')}",
                     "detail": "each listing files an ALWAYS-gated prayer (~$0.20 Etsy)"},
         "desc": "Drafts real paid listings for your blessing.",
         "drives": ["d_storefront"],
         "last_ts": _f("world_auto_t_last_sell")},
        {"id": "loop_bills", "label": "Production bills", "icon": "🏭",
         "in_charge": "Work-priority scheduler (RimWorld bills)",
         "cadence": {"enabled": _T(_g("world_bills_drive", "0")),
                     "label": "hysteresis targets · ≥20 min between kicks",
                     "detail": "below-target stock offers Produce jobs; drive-toggle can kick real creation"},
         "desc": "Standing 'keep N ready' targets on the real pipelines.",
         "drives": ["d_image", "d_audio", "d_video", "d_models3d"]},
        {"id": "loop_security", "label": "Security watch", "icon": "🛡️",
         "in_charge": "Netsec desk (Gale) + AI Shield",
         "cadence": {"enabled": _T(_g("security_autoscan_enabled", "0")) or _T(_g("security_monitor_enabled", "0")),
                     "label": "monitor + periodic scans + nightly audit",
                     "detail": "every subsystem log has a beat; findings become raid bugs + remediation work"},
         "desc": "Agents review the store's own systems for trouble.",
         "drives": ["d_netsec"]},
        {"id": "loop_swarm", "label": "Dev-swarm cron", "icon": "🤖",
         "in_charge": "Boss Kane → swarm crew",
         "cadence": {"enabled": _T(_g("swarm_cron_enabled", "0")),
                     "label": "cron-scheduled coding jobs",
                     "detail": "architect → planner → coder → tester; code ships only via the gated add_software prayer"},
         "desc": "The real coding pipeline as citizens.",
         "drives": ["d_devlab"]},
        {"id": "loop_social_sched", "label": "Social scheduler", "icon": "⏰",
         "in_charge": "Social desk (Sunny)",
         "cadence": {"enabled": _T(_g("social_scheduler_enabled", "0")),
                     "label": "publishes due scheduled posts",
                     "detail": "own master switch; per-platform connections required"},
         "desc": "Time-based publishing of already-approved posts.",
         "drives": ["d_social"]},
        {"id": "loop_caps", "label": "Capability auto-loop", "icon": "⚡",
         "in_charge": "Capability catalog (world_caps)",
         "cadence": {"enabled": _T(_g("world_caps_auto", "0")),
                     "label": f"≤1 per {caps_int} min",
                     "detail": "one random agent invokes one enabled agent-safe capability; publishing/spending never eligible"},
         "desc": "Agents reach for the full toolkit — only what you gated on.",
         "drives": [],
         "last_ts": _f("world_caps_auto_last")},
        {"id": "loop_sweep", "label": "Prayer sweep + syncs", "icon": "🙏",
         "in_charge": "world_auto daemon",
         "cadence": {"enabled": True,
                     "label": "sweep ~2/min (budget mode) · taste 5 min · revenue 15 min",
                     "detail": "drains free/affordable non-gated prayers; harvests verdicts; pulls real sales"},
         "desc": "The housekeeping heartbeat around the approval queue.",
         "drives": ["sys_gate"]},
    ]
    return loops


def _res_actor(god_comment):
    """Who resolved a prayer, from its stamped comment: the human owner ('god'),
    a lieutenant ('jesus'/'satan'), or the budget automation ('auto')."""
    gc = god_comment or ""
    if gc.startswith("auto (Jesus"):
        return "jesus"
    if gc.startswith("auto (Satan"):
        return "satan"
    if gc.startswith("auto") or gc.startswith("duplicate"):
        return "auto"
    return "god"


def graph():
    conn = get_conn()
    c = conn.cursor()
    try:
        now = time.time()
        # ── per-agent build/review stats ───────────────────────────────────
        built = {r["agent_name"]: r["n"] for r in c.execute(
            "SELECT agent_name, COUNT(*) n FROM world_prayers "
            "WHERE agent_name IS NOT NULL GROUP BY agent_name").fetchall()}
        agents = [dict(r) for r in c.execute(
            "SELECT key,name,kind,job_class,dept,level,jobs_done,state,coins "
            "FROM world_agents").fetchall()]
        name2dept = {a["name"]: a["dept"] for a in agents if a.get("name")}
        try:
            import world_caps
            cap_list = [{"id": cp["id"], "label": cp["label"], "dept": cp.get("dept"),
                         "gate_on": world_caps.gate_on(cp)} for cp in world_caps.CATALOG]
        except Exception:
            cap_list = []
        caps_by_dept = {}
        cap_info = {}
        for cp in cap_list:
            caps_by_dept.setdefault(cp.get("dept") or "other", []).append(cp)
            cap_info[cp["id"]] = cp

        # god review stats (HUMAN verdicts only — auto/Jesus stamps excluded)
        god_stats = dict(c.execute(
            "SELECT COALESCE(SUM(CASE WHEN status IN ('approved','done') AND god_comment NOT LIKE 'auto%' THEN 1 ELSE 0 END),0) approved, "
            "COALESCE(SUM(CASE WHEN status='rejected' AND COALESCE(god_comment,'') NOT LIKE 'auto%' AND COALESCE(god_comment,'') NOT LIKE 'duplicate%' THEN 1 ELSE 0 END),0) rejected, "
            "COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),0) pending "
            "FROM world_prayers").fetchone())
        # Jesus's delegated verdicts + cap invocations
        jesus_stats = dict(c.execute(
            "SELECT COALESCE(SUM(CASE WHEN status IN ('approved','done','failed') THEN 1 ELSE 0 END),0) approved, "
            "COALESCE(SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END),0) rejected "
            "FROM world_prayers WHERE god_comment LIKE 'auto (Jesus%'").fetchone())
        try:
            jesus_stats["caps_invoked"] = c.execute(
                "SELECT COUNT(*) FROM world_cap_runs WHERE actor='jesus'").fetchone()[0]
        except Exception:
            jesus_stats["caps_invoked"] = 0
        # Satan's adversarial verdicts (rejects only — he has no approve path),
        # flagged risks, and delegated cap invocations
        satan_stats = dict(c.execute(
            "SELECT COALESCE(SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END),0) rejected "
            "FROM world_prayers WHERE god_comment LIKE 'auto (Satan%'").fetchone())
        try:
            satan_stats["caps_invoked"] = c.execute(
                "SELECT COUNT(*) FROM world_cap_runs WHERE actor='satan'").fetchone()[0]
        except Exception:
            satan_stats["caps_invoked"] = 0
        try:
            satan_stats["risks_flagged"] = c.execute(
                "SELECT COUNT(*) FROM world_satan_ledger WHERE action='risk' "
                "AND COALESCE(shadow,0)=0").fetchone()[0]
        except Exception:
            satan_stats["risks_flagged"] = 0
        endorse = dict(c.execute(
            "SELECT COALESCE(SUM(CASE WHEN boss_ok=1 THEN 1 ELSE 0 END),0) boss_ok, "
            "COALESCE(SUM(CASE WHEN boss_ok=0 THEN 1 ELSE 0 END),0) boss_no, "
            "COALESCE(SUM(CASE WHEN mayor_ok=1 THEN 1 ELSE 0 END),0) mayor_ok, "
            "COALESCE(SUM(CASE WHEN mayor_ok=0 THEN 1 ELSE 0 END),0) mayor_no "
            "FROM world_prayers WHERE boss_ok IS NOT NULL").fetchone())

        # recent per-agent activity (drill-down)
        recent = {}
        for r in c.execute(
                "SELECT agent_name, title, status FROM world_prayers "
                "WHERE agent_name IS NOT NULL ORDER BY id DESC LIMIT 60").fetchall():
            recent.setdefault(r["agent_name"], []).append(
                {"title": r["title"], "status": r["status"]})

        # ── LIVE FLOW: recent time-anchored events across the real tables ──
        act = {}      # node id → {"last_ts","count_1h","last"}
        flow = []     # the moving stream, newest first

        def bump(node_id, ts, label):
            if not node_id or not ts:
                return
            a = act.setdefault(node_id, {"last_ts": 0, "count_1h": 0, "last": ""})
            if ts > a["last_ts"]:
                a["last_ts"] = ts
                a["last"] = label[:110]
            if now - ts <= 3600:
                a["count_1h"] += 1

        def ev(ts, who, verb, what, status=None, node=None):
            if ts:
                flow.append({"ts": int(ts), "who": who, "verb": verb,
                             "what": (what or "")[:90],
                             **({"status": status} if status else {}),
                             **({"node": node} if node else {})})

        # prayers: filings, endorsements, resolutions — who did what, when
        pr = c.execute(
            "SELECT id, kind, title, agent_name, status, god_comment, cost_cents, "
            "CAST(strftime('%s', created_at) AS INTEGER) c_ts, boss_ok, mayor_ok, "
            "CAST(strftime('%s', COALESCE(resolved_at, created_at)) AS INTEGER) r_ts "
            "FROM world_prayers ORDER BY id DESC LIMIT 150").fetchall()
        for p in pr:
            dept_node = f"d_{name2dept[p['agent_name']]}" if p["agent_name"] in name2dept else None
            title = f"#{p['id']} {p['title']}"
            bump("sys_gate", p["c_ts"], f"🙏 {p['agent_name'] or 'system'}: {p['title']}")
            if dept_node:
                bump(dept_node, p["c_ts"], f"prayed: {p['title']}")
            ev(p["c_ts"], p["agent_name"] or "The system", "prayed", title,
               status=("pending" if p["status"] == "pending" else None), node=dept_node)
            if p["boss_ok"] is not None:
                bump("boss", p["c_ts"], f"{'endorsed' if p['boss_ok'] else 'blocked'}: {p['title']}")
                bump("mayor", p["c_ts"], f"{'endorsed' if p['mayor_ok'] else 'blocked'}: {p['title']}")
            if p["status"] in ("approved", "done", "rejected", "failed") and p["r_ts"]:
                actor = _res_actor(p["god_comment"])
                verb = "rejected" if p["status"] == "rejected" else "approved"
                # display names only — the actor ids ('god'/'jesus'/'satan'/'auto')
                # and the god_comment stamps they're parsed from are unchanged
                who = {"god": nt.label("god"), "jesus": nt.label("jesus"),
                       "satan": nt.label("satan"), "auto": "Auto (budget)"}[actor]
                node = actor if actor in ("god", "jesus", "satan") else "sys_gate"
                bump("sys_gate", p["r_ts"], f"{who} {verb}: {p['title']}")
                if actor in ("god", "jesus", "satan"):
                    bump(actor, p["r_ts"], f"{verb}: {p['title']}")
                ev(p["r_ts"], who, verb, title, status=p["status"], node=node)
                if p["status"] == "done":
                    prod = _KIND_PRODUCT.get(p["kind"])
                    if prod:
                        bump(prod, p["r_ts"], f"shipped: {p['title']}")

        # capability runs: God's clicks, Jesus's delegated clicks, agent draws
        try:
            for r in c.execute(
                    "SELECT cap_id, actor, agent, status, "
                    "CAST(strftime('%s', created_at) AS INTEGER) ts "
                    "FROM world_cap_runs ORDER BY id DESC LIMIT 120").fetchall():
                info = cap_info.get(r["cap_id"], {})
                label = info.get("label", r["cap_id"])
                who = (nt.label("god") if r["actor"] == "god"
                       else nt.label("jesus") if r["actor"] == "jesus"
                       else nt.label("satan") if r["actor"] == "satan"
                       else (r["agent"] or "agent"))
                node = ("god" if r["actor"] == "god"
                        else "jesus" if r["actor"] == "jesus"
                        else "satan" if r["actor"] == "satan"
                        else (f"d_{info['dept']}" if info.get("dept") else None))
                bump(node, r["ts"], f"invoked: {label}")
                if info.get("dept"):
                    bump(f"d_{info['dept']}", r["ts"], f"cap run: {label}")
                    prod = _DEPT_PRODUCT.get(info["dept"])
                    if prod and r["status"] == "done":
                        bump(prod, r["ts"], f"from cap: {label}")
                ev(r["ts"], who, "invoked", label, status=r["status"], node=node)
        except Exception:
            pass

        # proposal funnel: intake landing on the desk, verdicts leaving the gate
        prop_stats = {"pending": 0, "scored": 0, "weeded": 0, "approved": 0}
        try:
            prop_stats = dict(c.execute(
                "SELECT COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),0) pending, "
                "COALESCE(SUM(CASE WHEN score IS NOT NULL THEN 1 ELSE 0 END),0) scored, "
                "COALESCE(SUM(CASE WHEN status='rejected' AND verdict='reject' THEN 1 ELSE 0 END),0) weeded, "
                "COALESCE(SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END),0) approved "
                "FROM proposals").fetchone())
            for r in c.execute(
                    "SELECT title, lane, source_label, verdict, score, "
                    "CAST(strftime('%s', created_at) AS INTEGER) c_ts, "
                    "CAST(strftime('%s', reviewed_at) AS INTEGER) r_ts "
                    "FROM proposals ORDER BY id DESC LIMIT 40").fetchall():
                lane = r["lane"] or "unlaned"
                bump("sys_desk", r["c_ts"], f"[{lane}] {r['title']}")
                ev(r["c_ts"], r["source_label"] or "Proposal desk", "proposed",
                   f"[{lane}] {r['title']}", node="sys_desk")
                if r["r_ts"]:
                    bump("sys_propgate", r["r_ts"],
                         f"judged {r['score']} ({r['verdict']}): {r['title']}")
                    ev(r["r_ts"], "Proposal gate", "judged",
                       f"{r['title']} → {r['score']}", status=r["verdict"], node="sys_propgate")
        except Exception:
            pass

        # town meetings: the votes moving
        try:
            for r in c.execute(
                    "SELECT decision, CAST(strftime('%s', created_at) AS INTEGER) ts "
                    "FROM world_meetings ORDER BY id DESC LIMIT 6").fetchall():
                bump("sys_meeting", r["ts"], f"voted: {r['decision']}")
                ev(r["ts"], "Town meeting", "voted", r["decision"], node="sys_meeting")
        except Exception:
            pass

        # Jesus's own auditable ledger (also covers refusals/skips)
        try:
            import world_jesus
            for r in world_jesus.ledger(20, conn=conn):
                bump("jesus", r.get("ts"), f"{r.get('decision')}: {r.get('target') or r.get('kind')}")
        except Exception:
            pass

        # Satan's own auditable ledger (rejects, risk flags, reviews, refusals)
        try:
            import world_satan
            for r in world_satan.ledger(20, conn=conn):
                bump("satan", r.get("ts"), f"{r.get('decision')}: {r.get('target') or r.get('kind')}")
        except Exception:
            pass

        # dev-swarm role activity (+ per-role event counts for the node stats)
        swarm_counts = {}
        try:
            for r in c.execute(
                    "SELECT agent, kind, content, "
                    "CAST(strftime('%s', created_at) AS INTEGER) ts "
                    "FROM swarm_events ORDER BY id DESC LIMIT 200").fetchall():
                node = _swarm_role_node(r["agent"])
                if not node:
                    continue
                swarm_counts[node] = swarm_counts.get(node, 0) + 1
                what = " ".join(str(r["content"] or "").split())[:70]
                bump(node, r["ts"], f"{r['kind']}: {what}" if what else (r["kind"] or ""))
        except Exception:
            pass

        flow.sort(key=lambda e: -e["ts"])
        flow = flow[:30]
        for e in flow:
            e["ago_sec"] = max(0, int(now - e["ts"]))

        # ── nodes ──────────────────────────────────────────────────────────
        # Layer 0 — THE OPERATOR TIER: the owner (God) in the CENTER with the
        # two lieutenants mirrored on either side. Node order IS the vertical
        # layout: ✝️ Jesus (angel side) above, 🌟 God centered, 😈 Satan
        # (devil side) below — both lieutenants same tier, edge-for-edge
        # mirrors, opposite polarities.
        nodes, edges = [], []
        jesus_on = _T(_g("world_jesus_enabled", "0"))
        satan_on = _T(_g("world_satan_enabled", "0"))

        # Jesus — the CONSTRUCTIVE lieutenant: the same operator seat, stacked
        # at the SAME tier, with IDENTICAL edges. A toggleable peer: off ⇒
        # inert, on ⇒ performs the owner's routine actions within the exact
        # same gates + hard floor.
        nodes.append({"id": "jesus", "type": "human", "layer": 0, "icon": nt.icon("jesus"),
                      "label": nt.pick("Jesus (AI operator)", "Constructive Lt. (AI operator)"),
                      "enabled": jesus_on,
                      "stats": {"approved": jesus_stats["approved"],
                                "rejected": jesus_stats["rejected"],
                                "caps_invoked": jesus_stats["caps_invoked"]},
                      "detail": nt.pick(
                          "The owner's toggle-gated stand-in (world_jesus_enabled, DEFAULT OFF) — the same operator seat as God, "
                          "mirrored edge-for-edge; the CONSTRUCTIVE/best-case side of the owner's hi-lo band. Acts only within "
                          "already-enabled gates: routine prayer verdicts + enabled capabilities. Real money, code changes and the "
                          "illegal-content floor stay human/refused — the delegation adds zero privilege and cannot alter the floor.",
                          "The owner's toggle-gated stand-in (world_jesus_enabled, DEFAULT OFF) — the same operator seat as the owner, "
                          "mirrored edge-for-edge; the CONSTRUCTIVE/best-case side of the owner's hi-lo band. Acts only within "
                          "already-enabled gates: routine prayer verdicts + enabled capabilities. Real money, code changes and the "
                          "illegal-content floor stay human/refused — the delegation adds zero privilege and cannot alter the floor.")})
        nodes.append({"id": "god", "type": "human", "layer": 0, "icon": nt.icon("god"),
                      "label": nt.pick("You (God)", "You (Owner)"),
                      "stats": {"approved": god_stats["approved"],
                                "rejected": god_stats["rejected"],
                                "pending_now": god_stats["pending"]},
                      "detail": nt.pick(
                          "The human owner-operator, centered between the two lieutenants (✝️ best case / 😈 worst case). "
                          "Final approval on every gated prayer; every verdict trains the town's taste model.",
                          "The human owner-operator, centered between the two lieutenants (🔺 best case / 🔻 worst case). "
                          "Final approval on every gated prayer; every verdict trains the town's taste model.")})
        # Satan — the ADVERSARIAL lieutenant: Jesus's mirror at the same tier
        # with the same edges and the INVERSE polarity (worst-case/red-team).
        # He never approves, never moves money; the illegal-content floors are
        # always-on at the endpoints and outside his (or anyone's) control.
        nodes.append({"id": "satan", "type": "human", "layer": 0, "icon": nt.icon("satan"),
                      "label": nt.pick("Satan (AI red team)", "Adversarial Lt. (AI red team)"),
                      "enabled": satan_on,
                      "stats": {"rejected": satan_stats["rejected"],
                                "risks_flagged": satan_stats["risks_flagged"],
                                "caps_invoked": satan_stats["caps_invoked"]},
                      "detail": nt.pick(
                          "The owner's toggle-gated adversarial lieutenant (world_satan_enabled, DEFAULT OFF) — Jesus's mirror, "
                          "edge-for-edge at the same tier, with the WORST-CASE polarity: routine rejects (both leaders blocked), "
                          "risk flags, red-team reviews, and the low side of every prediction band. NO approve path, no money "
                          "path (ALWAYS_GATE + any real cents refused), and the minors/CSAM + real-person deepfake/NCII floors "
                          "are always-on at the endpoints — not toggleable, not under Satan's control.",
                          "The owner's toggle-gated adversarial lieutenant (world_satan_enabled, DEFAULT OFF) — the constructive "
                          "seat's mirror, edge-for-edge at the same tier, with the WORST-CASE polarity: routine rejects (both "
                          "leaders blocked), risk flags, red-team reviews, and the low side of every prediction band. NO approve "
                          "path, no money path (ALWAYS_GATE + any real cents refused), and the minors/CSAM + real-person "
                          "deepfake/NCII floors are always-on at the endpoints — not toggleable, not under this seat's control.")})
        nodes.append({"id": "boss", "type": "leader", "layer": 1, "icon": "💼",
                      "label": "Boss Kane",
                      "stats": {"endorsed": endorse["boss_ok"], "blocked": endorse["boss_no"]},
                      "detail": "Reviews every prayer for brand-fit (taste) + budget before automation may run it. Mood mirrors the workers."})
        nodes.append({"id": "mayor", "type": "leader", "layer": 1, "icon": "🏛️",
                      "label": "Mayor Vex",
                      "stats": {"endorsed": endorse["mayor_ok"], "blocked": endorse["mayor_no"]},
                      "detail": "Reviews every prayer for treasury capacity + crew morale. Mood mirrors the whole town."})
        nodes.append({"id": "sys_gate", "type": "system", "layer": 1, "icon": "🔒",
                      "label": "Prayer gate (God Console)",
                      "stats": {"pending": god_stats["pending"]},
                      "detail": "The approval queue. Gated kinds NEVER auto-run; free/affordable non-gated ones may, in budget mode, after Boss+Mayor endorse + taste ≥ minimum. Real-money kinds always need the human."})
        nodes.append({"id": "sys_republic", "type": "system", "layer": 1, "icon": "⚖️",
                      "label": "The Republic",
                      "detail": "Agents propose survival strategies, vote, and the winning plan spawns real actions. Risky/code actions route to gated prayers."})
        nodes.append({"id": "sys_meeting", "type": "system", "layer": 1, "icon": "🗳️",
                      "label": "Town meeting",
                      "detail": "Each agent casts one weighted vote; the winner becomes the town's active directive."})
        # The proposal funnel: intake desk → review gate → generations → listings.
        nodes.append({"id": "sys_desk", "type": "system", "layer": 1, "icon": "🗞️",
                      "label": "Proposal desk",
                      "stats": {"pending": prop_stats["pending"]},
                      "detail": "Multi-lane proposal intake (proposal_desk.py + the trend scan): categorized "
                                "news feeds, Google/Reddit trends, the crew's 'Suggested:' ideas and the live "
                                "Etsy demand signal become lane-tagged rows in the proposals backlog. Lanes: "
                                "humor, news, tech, gaming, evergreen, market — each with its own editable "
                                "prompt + model pick (Settings → Prompts)."})
        nodes.append({"id": "sys_propgate", "type": "system", "layer": 1, "icon": "🚦",
                      "label": "Proposal review gate",
                      "stats": {"scored": prop_stats["scored"], "weeded": prop_stats["weeded"],
                                "approved": prop_stats["approved"]},
                      "detail": "The weed-out step (proposal_gate.py): local near-duplicate check, then an LLM "
                                "judge scores 0-100 on marketability (weighing the Etsy market signal), "
                                "originality, POD fit and policy safety. Per mode it records, auto-weeds, or "
                                "full-auto queues image generations — in bounded batches "
                                "(proposal_gate_batch_size)."})

        # departments with members + capabilities
        try:
            from world_defs import DEPARTMENTS
            dept_label = {k: v[0] for k, v in DEPARTMENTS.items()}
        except Exception:
            dept_label = {}
        by_dept = {}
        for a in agents:
            by_dept.setdefault(a["dept"] or "other", []).append(a)
        for dept, members in sorted(by_dept.items()):
            if dept in ("civic", "exec"):        # leaders already have nodes
                continue
            nodes.append({
                "id": f"d_{dept}", "type": "dept", "layer": 2, "icon": "🏢",
                "label": dept_label.get(dept, dept.title()),
                "agents": [{"key": a["key"], "name": a["name"], "level": a["level"],
                            "jobs": a["jobs_done"], "state": a["state"],
                            "built": built.get(a["name"], 0),
                            "recent": recent.get(a["name"], [])[:4]} for a in members],
                "caps": caps_by_dept.get(dept, []),
            })

        # dev-swarm role seats (layer 3, between the departments and the products)
        for rid, ricon, rlabel, rdetail in _SWARM_ROLES:
            nodes.append({"id": rid, "type": "role", "layer": 3, "icon": ricon,
                          "label": rlabel, "detail": rdetail,
                          **({"stats": {"events": swarm_counts[rid]}} if swarm_counts.get(rid) else {})})

        for pid, plabel in _PRODUCTS:
            nodes.append({"id": pid, "type": "product", "layer": 4,
                          "icon": plabel.split(" ")[0], "label": plabel})

        # attach live activity/recency to every node
        for n in nodes:
            a = act.get(n["id"])
            if a:
                n["activity"] = {"last_ts": a["last_ts"],
                                 "ago_sec": max(0, int(now - a["last_ts"])),
                                 "count_1h": a["count_1h"],
                                 "active": a["count_1h"] > 0 or (now - a["last_ts"]) < 3600,
                                 "last": a["last"]}
            else:
                n["activity"] = {"last_ts": 0, "ago_sec": None, "count_1h": 0,
                                 "active": False, "last": ""}
            if n["id"] == "jesus" and not jesus_on:
                n["activity"]["active"] = False        # toggle off ⇒ inert, whatever history says
            if n["id"] == "satan" and not satan_on:
                n["activity"]["active"] = False        # toggle off ⇒ inert, whatever history says

        have = {n["id"] for n in nodes}

        # ── edges ──────────────────────────────────────────────────────────
        def edge(f, t, kind, label=None):
            if f in have and t in have:
                edges.append({"from": f, "to": t, "kind": kind, **({"label": label} if label else {})})

        # The operator seat — God and BOTH lieutenants get IDENTICAL edges
        # (same authority, same approval loop, same review relationships).
        # Jesus and Satan are edge-for-edge mirrors of the owner's seat wired
        # to the same systems; only the toggles, their opposite polarities and
        # the human-only floor differ.
        for op in ("god", "jesus", "satan"):
            edge(op, "boss", "commands", "oversees")
            edge(op, "mayor", "commands", "oversees")
            edge("sys_gate", op, "approves", "gated → operator blessing")
            edge(op, "sys_gate", "approves", "verdicts")
        edge("boss", "sys_gate", "endorses", "brand + budget check")
        edge("mayor", "sys_gate", "endorses", "treasury + morale check")
        for dept in by_dept:
            did = f"d_{dept}"
            if did not in have:
                continue
            edge(did, "sys_gate", "prays", "real actions need approval")
            edge(did, "sys_republic", "votes")
            edge(did, "sys_meeting", "votes")
            prod = _DEPT_PRODUCT.get(dept)
            if prod:
                edge(did, prod, "produces")
        edge("sys_republic", "sys_gate", "prays", "risky actions gated")
        # the proposal funnel, end to end: feeds/crew → desk → gate → studio → listing
        edge("d_trends", "sys_desk", "delegates", "trend scan + news feed lanes")
        edge("d_research", "sys_desk", "delegates", "crew 'Suggested:' ideas → proposals")
        edge("d_storefront", "sys_desk", "delegates", "Etsy demand signal steers the market lane")
        edge("sys_desk", "sys_propgate", "delegates", "pending proposals → LLM judge")
        edge("sys_propgate", "d_image", "delegates", "full-auto approvals queue generations")
        edge("sys_propgate", "p_listing", "produces", "survivors become published listings")
        edge("boss", "d_devlab", "delegates", "spends fund via dev-swarm")
        edge("mayor", "sys_meeting", "commands", "chairs")
        # netsec reviews the studios' logs (world_security beats)
        for target in ("d_image", "d_video", "d_audio", "d_models3d", "d_devlab"):
            edge("d_netsec", target, "reviews", "log beat")
        # dev-swarm internal delegation chain (as one dept, annotated)
        edge("d_devlab", "sys_gate", "prays", "add_software — always gated")
        # …and the swarm's ROLE seats, spelled out: architect → planner →
        # coder → reviewer → tester, with the review/test bounce-backs, ending
        # at the same gated add_software prayer — the roles add no new exit.
        edge("d_devlab", "r_architect", "delegates", "each swarm job starts here")
        edge("r_architect", "r_planner", "delegates", "spec → build steps")
        edge("r_planner", "r_coder", "delegates", "steps → diff")
        edge("r_coder", "r_reviewer", "delegates", "diff → vote")
        edge("r_reviewer", "r_coder", "reviews", "approve/reject (+ auditor on big jobs)")
        edge("r_reviewer", "r_tester", "delegates", "approved → test suite")
        edge("r_tester", "r_coder", "reviews", "failures bounce back")
        edge("r_tester", "sys_gate", "prays", "ships only via the gated add_software prayer")

        loops = _loops(c)
        for lp in loops:
            if lp.get("last_ts"):
                lp["ago_sec"] = max(0, int(now - lp["last_ts"]))

        return {"generated_at": now, "now": now,
                "loops": loops, "nodes": nodes, "edges": edges, "flow": flow,
                "legend": {
                    "commands": "who's in charge of whom",
                    "delegates": "hands work down",
                    "prays": "asks permission (approval queue)",
                    "endorses": "reviews before automation may run it",
                    "approves": nt.pick(
                        "operator blessing / verdict (God; Jesus approves+rejects, Satan rejects/flags only)",
                        "operator blessing / verdict (Owner; the constructive seat approves+rejects, "
                        "the adversarial seat rejects/flags only)"),
                    "reviews": "watches + audits the other's output",
                    "votes": "has a vote in that body",
                    "produces": "ships this product",
                    "drives": "loop schedules this work"}}
    finally:
        conn.close()

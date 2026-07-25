"""The Company — read/settings surface: town settings, manual cognition, the big
read-only ``/state`` snapshot, and the compact LLM ``/snapshot``."""
import time, json, logging
from fastapi import HTTPException, Body, BackgroundTasks

from deps import get_conn
import world_defs as wd
import world_gov, world_build, world_systems, world_settings as ws
import world_skills, world_orchestra, world_raid, world_learn, world_security, world_tech, world_construct, world_work, world_mood, world_research, world_schedule, world_items
import world_balance as wb
from ._base import router


@router.get("/api/world/settings")
def get_world_settings():
    """The Company settings: LLM schedule + real-world guardrails."""
    return {"settings": ws.get_all(), "defaults": ws.DEFAULTS}


@router.post("/api/world/settings")
def save_world_settings(body: dict = Body(...)):
    ws.save(body.get("settings") or body)
    return {"ok": True, "settings": ws.get_all()}


@router.get("/api/world/theme")
def get_naming_theme():
    """READ-ONLY: the active naming theme ('themed' default | 'neutral') + its
    resolved role→{label,icon} map (app/naming_theme.py). Display labels only —
    saving goes through the normal PATCH /api/settings flow (key `naming_theme`);
    no internal id/actor/endpoint is affected by any value here."""
    import naming_theme
    return naming_theme.client_state()


@router.post("/api/world/cognition")
def run_cognition_now():
    """Manually trigger a cognition batch (respects the enabled/guardrail flags)."""
    if not ws.b("world_llm_enabled"):
        raise HTTPException(400, "Cognition is disabled in Company settings.")
    conn = get_conn()
    try:
        n = world_gov.run_cognition(conn)
    finally:
        conn.close()
    return {"ok": True, "queued": n}


@router.get("/api/world/state")
def world_state():
    """Read-only snapshot. The background ticker (world_ticker) owns advancement;
    this endpoint never mutates the sim, so any number of viewers is cheap."""
    conn = get_conn()
    wd.seed(conn)              # idempotent safety (first boot before the ticker runs)
    c = conn.cursor()
    agents = [dict(r) for r in c.execute("SELECT * FROM world_agents ORDER BY id").fetchall()]
    for a in agents:                                   # RuneScape-style skills (system A)
        a["skills"] = world_skills.skills_for(c, a["key"])
        a["primary_skill"] = world_skills.primary_skill(a)
        a["competence"] = round(world_learn.competence(c, a), 2)   # merit weight (system E)
        _pol = world_learn.policy_for(c, a["key"])                  # what the bandit has learned it likes
        a["prefers"] = max(_pol, key=lambda k: _pol[k]["value"]) if _pol else None
        a["beat"] = world_security.agent_tasks(c, a["key"])         # their security beat + open debug tasks
        a["work_priority"] = world_work.get_priorities(c, a)        # Work-Priority row (work_type → 0-4)
        a["mood_value"] = round(world_mood.mood_of(c, a))          # thought-ledger mood 0-100 (chunk 4)
        a["thoughts"] = world_mood.thoughts_for(c, a["key"])       # active mood modifiers
        a["broken"] = world_mood.is_broken(a)
        a["inventory"] = world_items.inventory_for(c, a["key"])   # what they carry (item economy)
        a["thriving"] = all((a.get(n) or 0) >= 68 for n in wd.NEEDS)   # all-green = max-reward state
        a["blessed"] = (a.get("blessed_until") or 0) > time.time()     # god's temp buff active
        try:
            import world_genome
            _g = world_genome.genome(a)
            a["style"] = {"label": world_genome.STYLE(_g), "epsilon": round(_g["epsilon"], 2),
                          "focus": round(_g["focus"], 2), "spend": round(_g["spend"], 2)}
        except Exception:
            a["style"] = None
        _streak = time.time() - (a.get("streak_since") or time.time())  # monotony readout
        a["streak_min"] = int(_streak / 60)
        a["output_pct"] = int(max(wb.MONOTONY_FLOOR,
                                  1 - max(0.0, _streak - wb.MONOTONY_GRACE_SEC) * wb.MONOTONY_SLOPE) * 100)
    props  = [dict(r) for r in c.execute("SELECT * FROM world_props ORDER BY id").fetchall()]
    events = [dict(r) for r in c.execute("SELECT * FROM world_events ORDER BY id DESC LIMIT 24").fetchall()]
    suggestions = [dict(r) for r in c.execute(
        "SELECT * FROM world_suggestions WHERE status='open' ORDER BY id DESC LIMIT 8").fetchall()]
    meeting = c.execute("SELECT * FROM world_meetings ORDER BY id DESC LIMIT 1").fetchone()
    directive = c.execute("SELECT * FROM world_directives WHERE status='active' "
                          "ORDER BY id DESC LIMIT 1").fetchone()
    achievements = [dict(r) for r in c.execute(
        "SELECT * FROM world_achievements ORDER BY earned_at DESC").fetchall()]
    company = world_systems.company_summary(c)
    company["stockpile"] = world_skills.stockpile(c)   # company-wide gathered resources
    company["stock_targets"] = world_skills.stock_targets(c)   # keep floor..ceil per resource (chunk 3)
    company["intelligence"] = world_learn.intelligence(c)   # collective learning score (system E)
    company["specialists"] = world_learn.leaderboard(c)     # top agent per skill
    company["tech"] = world_tech.snapshot(c)                # material/research tier (chunk 4)
    company["research"] = world_research.snapshot(c)        # research TREE with prerequisites (#7)
    try:
        import world_rank
        company["rankings"] = world_rank.standings(c)       # the leaderboard — they compete to top it
    except Exception:
        company["rankings"] = []
    company["schedule"] = world_schedule.snapshot(c, __import__("time").strftime("%H"))  # town timetable (#6)
    company["construction"] = world_construct.snapshot(c)   # ghost-build projects + finished structures
    orchestra = world_orchestra.snapshot(c)            # season + town phase (system C)
    raid = world_raid.snapshot(c)                      # active threats / combat (system D)
    security = {"systems": world_security.scan_systems(c),   # whole-store log health (chunk 2)
                "posture": world_security.real_posture(c)}   # the REAL Command Center (grade/shield/attackers)
    try:
        import world_space
        space = world_space.snapshot(conn)                   # JASA space program overlay (never raises)
    except Exception:
        space = None
    try:
        import world_era
        eras = world_era.snapshot(conn)                      # per-building civilization-era overlay (never raises)
    except Exception:
        eras = None
    try:
        import world_run
        run_mode = world_run.mode()                          # normal|fast|test — the client preview-cycles in test
    except Exception:
        run_mode = "normal"
    placements_out = world_items.placements(c)          # furniture/yard pieces agents bought (item economy)
    art = []                                            # real generated images → wall art in houses (chunk 5)
    try:
        for r in c.execute("SELECT image_path FROM generations WHERE status='done' AND image_path IS NOT NULL "
                           "ORDER BY id DESC LIMIT 12").fetchall():
            p = r[0] or ""
            if "/designs/" in p:
                art.append("/store/designs/" + p.split("/designs/", 1)[1])
    except Exception:
        pass
    conn.close()
    # 😈 NSFW video store (map building): visible/open ONLY when BOTH existing gates
    # agree — Satan's world_satan_nsfw_domain toggle AND the layered nsfw.world_active()
    # (master + world switches). This merely READS the gates; nothing here (or in the
    # client) can flip them. Any failure → False (fail closed → boarded-up shop).
    try:
        import world_satan
        import nsfw
        nsfw_store_open = bool(world_satan.nsfw_domain_on() and nsfw.world_active())
    except Exception:
        nsfw_store_open = False
    activity, _ = wd.live_activity()
    # naming theme (display labels only — see app/naming_theme.py); the client
    # keeps its WTheme mirror synced from this on every poll. Fail ⇒ default.
    try:
        import naming_theme
        _naming = naming_theme.theme()
    except Exception:
        _naming = "themed"
    return {
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        "naming_theme": _naming,
        "nsfw_store_open": nsfw_store_open,
        "clock_hour": int(time.strftime("%H")),
        "departments": [{"key": k, "label": v[0], "color": v[1]} for k, v in wd.DEPARTMENTS.items()],
        "work_types": [{"key": wt, **world_work.WT_META[wt]} for wt in world_work.WORK_TYPES],
        "agents": agents, "props": props, "activity": activity, "events": events,
        "economy": {"item_cost": wd.ITEM_COST, "upgrades": wd.UPGRADES,
                    "treasury": company["treasury"], "debt": company["total_debt"],
                    "company_fund": company["company_fund"]},
        "company": company,
        "orchestra": orchestra,
        "raid": raid,
        "security": security,
        "space": space,
        "eras": eras,
        "run_mode": run_mode,
        "art": art,
        "placements": placements_out,
        "achievements": achievements,
        "governance": {"priority": directive["text"] if directive else None,
                       "directive": dict(directive) if directive else None,
                       "suggestions": suggestions,
                       "last_meeting": dict(meeting) if meeting else None},
    }


@router.get("/api/world/snapshot")
def world_snapshot():
    """Compact JSON the LLM world-builder can reason over."""
    conn = get_conn()
    wd.seed(conn)
    c = conn.cursor()
    agents = [{"name": r["name"], "job": r["dept"], "state": r["state"], "mood": r["mood_label"],
               "coins": r["coins"], "level": r["level"]}
              for r in c.execute("SELECT * FROM world_agents ORDER BY id").fetchall()]
    props = [{"label": r["label"], "at": r["location"], "status": r["status"]}
             for r in c.execute("SELECT * FROM world_props ORDER BY id").fetchall()]
    conn.close()
    activity, _ = wd.live_activity()
    return {"agents": agents, "props": props, "activity": activity}

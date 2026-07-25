"""The Company — production/economy controls: placement editor, stock targets,
schedule, research, build orders, work priorities, and the production bills."""
import time, json, logging
from fastapi import HTTPException, Body, BackgroundTasks

from deps import get_conn
import world_defs as wd
import world_gov, world_build, world_systems, world_settings as ws
import world_skills, world_orchestra, world_raid, world_learn, world_security, world_tech, world_construct, world_work, world_mood, world_research, world_schedule, world_items
import world_balance as wb
from ._base import router


@router.post("/api/world/placement/move")
def world_move_placement(body: dict = Body(...)):
    """Play-god editor: drag a bought furniture/yard piece to an exact spot.
    {agent_key, spot, slot, ox, oy} — ox/oy null resets to the default slot."""
    key, spot = body.get("agent_key"), body.get("spot")
    if not key or spot not in ("house", "yard") or body.get("slot") is None:
        raise HTTPException(400, "agent_key, spot and slot required")
    ox, oy = body.get("ox"), body.get("oy")
    if (ox is None) != (oy is None):
        raise HTTPException(400, "ox and oy go together")
    conn = get_conn()
    try:
        c = conn.cursor()
        moved = world_items.move_placement(c, key, spot, int(body["slot"]),
                                           None if ox is None else float(ox),
                                           None if oy is None else float(oy))
        if not moved:
            raise HTTPException(404, "no such placement")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.post("/api/world/structure/move")
def world_move_structure(body: dict = Body(...)):
    """Play-god editor: drag an agent-built structure to an exact spot.
    {id, x, y} — x/y null resets it to its computed slot position."""
    sid = body.get("id")
    if sid is None:
        raise HTTPException(400, "id required")
    x, y = body.get("x"), body.get("y")
    if (x is None) != (y is None):
        raise HTTPException(400, "x and y go together")
    conn = get_conn()
    try:
        c = conn.cursor()
        moved = world_construct.move_structure(c, int(sid),
                                               None if x is None else float(x),
                                               None if y is None else float(y))
        if not moved:
            raise HTTPException(404, "no such structure")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.post("/api/world/stock/target")
def world_set_stock_target(body: dict = Body(...)):
    """Set keep floor..ceil for a resource. Agents auto-gather it when below floor, stop at ceil."""
    conn = get_conn()
    try:
        world_skills.set_stock_target(conn.cursor(), body.get("resource"), body.get("floor", 0), body.get("ceil", 200))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.post("/api/world/schedule")
def world_set_schedule(body: dict = Body(...)):
    """Set the town timetable (#6). {hour, band} sets one hour; {schedule:[...24]} sets all.
    band ∈ sleep|work|rec|any."""
    conn = get_conn()
    try:
        c = conn.cursor()
        if isinstance(body.get("schedule"), list):
            world_schedule.set_all(c, body["schedule"])
        elif body.get("hour") is not None:
            world_schedule.set_hour(c, body["hour"], body.get("band"))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.post("/api/world/research")
def world_set_research(body: dict = Body(...)):
    """Pick the active research project (#7). {key: <project>}. Must have its prereqs done."""
    conn = get_conn()
    try:
        ok = world_research.set_active(conn.cursor(), body.get("key"))
        conn.commit()
    finally:
        conn.close()
    return {"ok": ok}


@router.post("/api/world/build/order")
def world_build_order(body: dict = Body(...)):
    """Production orders (RimWorld bills) for the construction queue.
    action=add {kind, mode:'make'|'keep', target}; action=update {id, paused|target|mode|order_idx};
    action=remove {id}. Active orders decide WHAT the town builds (else it auto-grows)."""
    action = body.get("action", "add")
    conn = get_conn()
    try:
        c = conn.cursor()
        if action == "add":
            world_construct.add_order(c, body.get("kind"), body.get("mode", "make"), body.get("target", 1))
        elif action == "remove":
            world_construct.update_order(c, int(body["id"]), delete=True)
        elif action == "update":
            world_construct.update_order(c, int(body["id"]),
                mode=body.get("mode"), target=body.get("target"),
                paused=body.get("paused"), order_idx=body.get("order_idx"))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.post("/api/world/work/priority")
def world_set_work_priority(body: dict = Body(...)):
    """Set an agent's priority for a work type (0=off, 1-4). {agent_key|agent_id, work_type, priority}.
    Pass work_type + priority with no agent to set the WHOLE COLUMN for every worker/openclaw agent."""
    wt, prio = body.get("work_type"), body.get("priority")
    conn = get_conn()
    try:
        c = conn.cursor()
        if body.get("agent_key") or body.get("agent_id"):
            key = body.get("agent_key")
            if not key:
                r = c.execute("SELECT key FROM world_agents WHERE id=?", (body["agent_id"],)).fetchone()
                key = r["key"] if r else None
            if key:
                world_work.set_priority(c, key, wt, prio)
        else:                                              # whole column
            for r in c.execute("SELECT key FROM world_agents WHERE kind IN ('worker','openclaw')").fetchall():
                world_work.set_priority(c, r["key"], wt, prio)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ── God-Panel WorkGiver: owner directives on depts/agents (default OFF) ──────
def _workgiver_state(c):
    depts = sorted({r["dept"] for r in c.execute(
        "SELECT DISTINCT dept FROM world_agents WHERE dept IS NOT NULL").fetchall() if r["dept"]})
    agents = [{"key": r["key"], "name": r["name"], "dept": r["dept"]} for r in c.execute(
        "SELECT key, name, dept FROM world_agents WHERE kind IN ('worker','openclaw') "
        "ORDER BY dept, name").fetchall()]
    return {"enabled": world_work.workgiver_enabled(),
            "directives": world_work.directives(c),
            "work_types": [{"key": wt, **world_work.WT_META[wt]} for wt in world_work.WORK_TYPES],
            "depts": depts, "agents": agents}


@router.get("/api/world/work/givers")
def world_workgivers():
    """The WorkGiver control surface: master toggle + owner directives +
    the depts/agents/work-types to aim them at. Read-only."""
    conn = get_conn()
    try:
        return _workgiver_state(conn.cursor())
    finally:
        conn.close()


@router.post("/api/world/work/giver")
def world_workgiver_set(body: dict = Body(...)):
    """Set or clear ONE owner directive: {scope:'dept'|'agent', target,
    work_type, priority: 0-4 | null to clear, note?}. Directives only bite
    while the master toggle is on (default OFF = unchanged behavior)."""
    scope, target, wt = body.get("scope"), body.get("target"), body.get("work_type")
    conn = get_conn()
    try:
        c = conn.cursor()
        if body.get("priority") is None:
            world_work.clear_directive(c, scope, target, wt)
        elif not world_work.set_directive(c, scope, target, wt,
                                          body["priority"], body.get("note")):
            raise HTTPException(400, "scope must be dept|agent, work_type must be valid, target required")
        conn.commit()
        return _workgiver_state(c)
    finally:
        conn.close()


@router.post("/api/world/work/giver-master")
def world_workgiver_master(body: dict = Body(...)):
    """Flip the WorkGiver master (world_workgiver_enabled). OFF (default) ⇒
    directives are stored but inert — behavior identical to before."""
    on = body.get("on") in (True, 1, "1", "true", "on")
    conn = get_conn()
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                     (world_work.WORKGIVER_KEY, "1" if on else "0"))
        conn.commit()
        return _workgiver_state(conn.cursor())
    finally:
        conn.close()


@router.get("/api/world/bills")
def world_bills_list():
    """All production bills + live stock counts and active/paused state."""
    import world_bills
    conn = get_conn()
    try:
        c = conn.cursor()
        snap = world_bills.snapshot(c)
        conn.commit()                       # refresh() may advance hysteresis state
        return {"bills": snap, "kinds": [{"key": k, **{f: v[f] for f in ("label", "icon", "dept")}}
                                         for k, v in world_bills.KINDS.items()]}
    finally:
        conn.close()


@router.post("/api/world/bills")
def world_bills_create(body: dict = Body(...)):
    """Create a bill: {kind, target, unpause_at?, label?, min_level?}."""
    import world_bills
    if body.get("kind") not in world_bills.KINDS:
        raise HTTPException(400, f"kind must be one of {list(world_bills.KINDS)}")
    if not body.get("target"):
        raise HTTPException(400, "target (how many to keep ready) is required")
    conn = get_conn()
    try:
        c = conn.cursor()
        bid = world_bills.create(c, body["kind"], body["target"], body.get("unpause_at"),
                                 body.get("label") or "", body.get("min_level") or 1)
        conn.commit()
        return {"ok": True, "id": bid}
    finally:
        conn.close()


@router.post("/api/world/bills/{bid}")
def world_bills_update(bid: int, body: dict = Body(...)):
    """Update a bill: any of {target, unpause_at, suspended, min_level, order_idx, label}."""
    import world_bills
    conn = get_conn()
    try:
        c = conn.cursor()
        ok = world_bills.update(c, bid, **{k: body.get(k) for k in
                                           ("target", "unpause_at", "suspended",
                                            "min_level", "order_idx", "label")})
        if not ok:
            raise HTTPException(404, "no such bill (or nothing to change)")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/api/world/bills/{bid}")
def world_bills_delete(bid: int):
    import world_bills
    conn = get_conn()
    try:
        c = conn.cursor()
        if not world_bills.delete(c, bid):
            raise HTTPException(404, "no such bill")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

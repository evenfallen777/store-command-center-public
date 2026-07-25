"""God Console — the FULL capability catalog (/api/world/caps/*) and the
agent-loops orchestration graph (/api/world/loops/graph).

Thin endpoints over world_caps (the gated catalog: trigger an action → get a
product back) and world_loops (the read-only orchestration map). The old
/api/world/control/trigger endpoint (10-cap stub) stays untouched in
control.py for compatibility; this is its full-surface successor."""
from fastapi import HTTPException, Body

import world_caps
import world_loops
from ._base import router


@router.get("/api/world/caps")
def caps_panel():
    return world_caps.panel()


@router.get("/api/world/caps/runs")
def caps_runs(limit: int = 30):
    return {"runs": world_caps.runs(min(100, max(1, limit)))}


@router.post("/api/world/caps/gate")
def caps_gate(body: dict = Body(...)):
    """Toggle a capability's AGENT gate ('master' / 'auto' / a cap id)."""
    res = world_caps.set_gate(body.get("id"), bool(body.get("on")))
    if res is None:
        raise HTTPException(404, f"unknown capability '{body.get('id')}'")
    return res


@router.post("/api/world/caps/auto-interval")
def caps_auto_interval(body: dict = Body(...)):
    try:
        return world_caps.set_auto_interval(int(body.get("interval_min", 240)))
    except (TypeError, ValueError):
        raise HTTPException(400, "interval_min must be an integer")


@router.get("/api/world/caps/grants")
def caps_grants():
    """Per-agent capability grants (owner-set; default empty = global gates only)."""
    return {"grants": world_caps.grants()}


@router.post("/api/world/caps/grant")
def caps_grant(body: dict = Body(...)):
    """REPLACE one capability's per-agent grant set: {id, agents: [names]}.
    An empty list clears it (the default — behavior identical to before).
    Grants refine the PER-CAP gate only for actor='agent': the catalog master
    still applies, Jesus never rides grants, and prayer gates stay downstream."""
    res = world_caps.set_grants(body.get("id"), body.get("agents") or [])
    if res is None:
        raise HTTPException(404, f"unknown capability '{body.get('id')}'")
    return res


@router.post("/api/world/caps/invoke")
def caps_invoke(body: dict = Body(...)):
    """A god click from the panel — the click IS the explicit human gate."""
    res = world_caps.invoke(body.get("id"), body.get("args") or {}, actor="god")
    if not res.get("ok") and "unknown capability" in (res.get("error") or ""):
        raise HTTPException(404, res["error"])
    return res


@router.post("/api/world/caps/agent-invoke")
def caps_agent_invoke(body: dict = Body(...)):
    """Programmatic/agent invocation — enforces the catalog master + per-cap gate."""
    return world_caps.invoke(body.get("id"), body.get("args") or {},
                             actor="agent", agent=body.get("agent"))


@router.get("/api/world/loops/graph")
def loops_graph():
    return world_loops.graph()

"""God Console — ✝️ Jesus (/api/world/jesus/*): the owner's OPTIONAL stand-in
operator. Thin endpoints over world_jesus: status + auditable ledger, the
single enable toggle (world_jesus_enabled, DEFAULT OFF) + cadence, and a
manual "act now" that performs ONE delegated action — refused outright while
the toggle is off, and always confined to the same gates + unchanged hard
safety floor as every other actor (see world_jesus.py)."""
from fastapi import HTTPException, Body

import world_jesus
from ._base import router


@router.get("/api/world/jesus")
def jesus_status():
    return world_jesus.status()


@router.post("/api/world/jesus/config")
def jesus_config(body: dict = Body(...)):
    """Flip the toggles / set the cadence. {enabled?: bool, interval_min?: int,
    publish_caps?: bool (delegated publishing-prayer buttons, default off),
    shadow?: bool (observe-only would-do stream while disabled, default off)}"""
    return world_jesus.set_config(body or {})


@router.get("/api/world/jesus/shadow")
def jesus_shadow():
    """READ-ONLY: the 'what Jesus would do' shadow stream + agreement score.
    Recorded only while Jesus is disabled AND world_jesus_shadow is on;
    observing never takes an action."""
    return {"enabled": world_jesus.shadow_on(),
            "stats": world_jesus.shadow_stats(),
            "stream": world_jesus.shadow_stream(80)}


@router.post("/api/world/jesus/tick-now")
def jesus_tick_now(body: dict = Body(default={})):
    """One delegated action NOW (skips the interval/active-hours throttle only;
    the enable toggle, catalog gates and the hard floor all still apply).
    Optional steering: {action: 'prayer'|'cap', prayer_id?, cap_id?}."""
    body = body or {}
    res = world_jesus.tick(force=True, action=body.get("action"),
                           cap_id=body.get("cap_id"), prayer_id=body.get("prayer_id"))
    if not res.get("ok"):
        raise HTTPException(409, res.get("skipped") or "Jesus could not act")
    return res

"""God Console — 😈 Satan (/api/world/satan/*): the owner's OPTIONAL
adversarial red-team lieutenant, the mirror of ✝️ Jesus. Thin endpoints over
world_satan: status + auditable ledger, the single enable toggle
(world_satan_enabled, DEFAULT OFF) + cadence + the (default OFF) NSFW-domain
oversight gate, and a manual "act now" that performs ONE adversarial action —
refused outright while the toggle is off, and always confined to the same
gates + unchanged hard safety floors as every other actor (see world_satan.py:
no approve path, no money path, and the minors/CSAM + real-person
deepfake/NCII floors are always-on at the endpoints, outside any toggle here).

Also hosts /api/world/lieutenants — the two-lieutenant owner digest (✝️ what's
happening / what I'd do · 😈 what could go wrong), via world_duality."""
from fastapi import HTTPException, Body

import world_duality
import world_satan
from ._base import router


@router.get("/api/world/satan")
def satan_status():
    return world_satan.status()


@router.post("/api/world/satan/config")
def satan_config(body: dict = Body(...)):
    """Flip the toggles / set the cadence. {enabled?: bool, interval_min?: int,
    nsfw_domain?: bool (private-studio oversight domain, default off — the
    always-on illegal-content floors are NOT part of this gate),
    shadow?: bool (observe-only would-do stream while disabled, default off)}"""
    return world_satan.set_config(body or {})


@router.get("/api/world/satan/shadow")
def satan_shadow():
    """READ-ONLY: the 'what Satan would do' shadow stream + agreement score.
    Recorded only while Satan is disabled AND world_satan_shadow is on;
    observing never takes an action."""
    return {"enabled": world_satan.shadow_on(),
            "stats": world_satan.shadow_stats(),
            "stream": world_satan.shadow_stream(80)}


@router.post("/api/world/satan/tick-now")
def satan_tick_now(body: dict = Body(default={})):
    """One adversarial action NOW (skips the interval/active-hours throttle
    only; the enable toggle, catalog gates and the hard floors all still
    apply — off ⇒ refused). Optional steering:
    {action: 'prayer'|'cap'|'nsfw_review', prayer_id?, cap_id?}."""
    body = body or {}
    res = world_satan.tick(force=True, action=body.get("action"),
                           cap_id=body.get("cap_id"), prayer_id=body.get("prayer_id"))
    if not res.get("ok"):
        raise HTTPException(409, res.get("skipped") or "Satan could not act")
    return res


@router.get("/api/world/lieutenants")
def lieutenants_digest():
    """READ-ONLY owner digest from BOTH lieutenants: ✝️ Jesus summarizes
    what's happening and what he'd do; 😈 Satan summarizes what could go
    wrong. Each seat reports even while disabled (clearly marked inert);
    nothing here acts, approves, or spends."""
    return world_duality.lieutenants_digest()

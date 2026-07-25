"""The Company — HQ progression stages (era snapshots of the headquarters).

Thin HTTP surface over ``world_hq``: list the stages (with full layouts so the
client can render ANY age, including a preserved earlier one), advance up the
ladder (tech-tier gated), and switch the active/rendered stage."""
from fastapi import HTTPException, Body

from deps import get_conn
import world_hq
from ._base import router


@router.get("/api/world/hq/stages")
def hq_stages():
    """Every HQ stage + the active id + the live tech-tier gate."""
    conn = get_conn()
    try:
        out = world_hq.snapshot(conn.cursor())
        conn.commit()          # first touch seeds the stage list
    finally:
        conn.close()
    return out


@router.post("/api/world/hq/advance")
def hq_advance():
    """Advance the HQ to the next stage (409 when the tech tier isn't there yet).
    The look being left is snapshotted into its stage first — nothing is lost."""
    conn = get_conn()
    try:
        ok, res = world_hq.advance(conn.cursor())
        conn.commit()
    finally:
        conn.close()
    if not ok:
        raise HTTPException(409, res)
    return {"ok": True, "stage": res}


@router.post("/api/world/hq/stage")
def hq_set_stage(body: dict = Body(...)):
    """Switch the rendered stage: {id}. Backward (viewing an earlier era) is
    always allowed; forward re-checks the tech-tier gate."""
    sid = body.get("id")
    if sid is None:
        raise HTTPException(400, "id is required")
    conn = get_conn()
    try:
        ok, res = world_hq.set_active(conn.cursor(), int(sid))
        conn.commit()
    finally:
        conn.close()
    if not ok:
        raise HTTPException(409, res)
    return {"ok": True, "stage": res}

"""God Console — the prayers approval queue (list/file/approve/reject) and the
gate toggles. These are the gate-enforcing endpoints; kept VERBATIM from the
former monolith so no approval/budget behaviour changes."""
import json, logging, threading, os
from fastapi import HTTPException, Body

from deps import get_conn
import world_ops as wo
from ._base import router, _prayer_thumb


@router.get("/api/world/ops/prayers")
def ops_prayers(status: str = "", limit: int = 50):
    conn = get_conn()
    try:
        wo.ensure(conn)
        if status:
            rows = conn.execute("SELECT * FROM world_prayers WHERE status=? ORDER BY id DESC LIMIT ?",
                                (status, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM world_prayers ORDER BY "
                                "CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC LIMIT ?",
                                (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # split creations (art/products the agents MAKE → judge/like) from operations
            d["group"] = "creation" if d.get("kind") in wo.CREATION_KINDS else "operation"
            d["thumb"] = _prayer_thumb(conn, d.get("payload")) if d["group"] == "creation" else None
            # DUALITY (presentational only, gate unchanged): on PENDING prayers,
            # when a lieutenant is on/shadowing, attach ✝️ Jesus's case FOR and
            # 😈 Satan's case AGAINST + the taste hi-lo band, so the owner sees
            # both sides BEFORE approving. Empty {} (and omitted) while both
            # lieutenants are off — the default — and never an error.
            if d.get("status") == "pending":
                try:
                    import world_duality
                    dual = world_duality.prayer_arguments(conn, d)
                    if dual:
                        d["duality"] = dual
                except Exception:
                    pass
            out.append(d)
        return {"prayers": out}
    finally:
        conn.close()


@router.post("/api/world/ops/prayers")
def ops_pray(body: dict = Body(...)):
    """Raise a prayer (used by the UI to test, or by agents/automation)."""
    kind = body.get("kind")
    title = body.get("title")
    if not kind or not title:
        raise HTTPException(400, "kind and title required")
    # Irreversible money-out / secret-export prayers may ONLY be filed by their
    # dedicated endpoints (which validate amount, recipient and balance) — never
    # through this generic path with an arbitrary payload/recipient/cost.
    if kind in wo.ALWAYS_GATE:
        raise HTTPException(403, f"'{kind}' must be filed via its dedicated endpoint, "
                                 "not the generic prayer API")
    p = wo.pray(kind, title, detail=body.get("detail", ""),
                cost_cents=body.get("cost_cents"), payload=body.get("payload"),
                agent_name=body.get("agent_name"))
    return {"prayer": p}


@router.post("/api/world/ops/prayers/{pid}/approve")
def ops_approve(pid: int, body: dict = Body(default={})):
    conn = get_conn()
    try:
        wo.ensure(conn)
        p = wo._get(conn, pid)
        if not p:
            raise HTTPException(404, "prayer not found")
        if p["status"] != "pending":
            raise HTTPException(400, f"prayer already {p['status']}")
        if p["cost_cents"] and not wo.can_spend(conn, p["cost_cents"]) and not body.get("force"):
            raise HTTPException(400, "over the monthly budget cap — raise the cap or pass force=true")
        return {"prayer": wo._resolve(conn, pid, approve=True, god_comment=body.get("comment") or "approved")}
    finally:
        conn.close()


@router.post("/api/world/ops/prayers/{pid}/reject")
def ops_reject(pid: int, body: dict = Body(default={})):
    conn = get_conn()
    try:
        wo.ensure(conn)
        p = wo._get(conn, pid)
        if not p:
            raise HTTPException(404, "prayer not found")
        return {"prayer": wo._resolve(conn, pid, approve=False, god_comment=body.get("comment") or "rejected")}
    finally:
        conn.close()


# ── gates (each a toggle) ────────────────────────────────────────────────────
@router.get("/api/world/ops/gates")
def ops_gates():
    """Every gate and whether it's on: the 'always judge creations' gate + the per-kind
    always-need-a-blessing gates. Each is user-toggleable."""
    return {
        "creations": wo.cfg("world_ops_gate_creations") == "1",
        "kinds": [{"kind": k, "label": lbl, "gated": wo.cfg(f"world_ops_gate_{k}") == "1"}
                  for k, lbl in wo.GATEABLE],
    }


@router.post("/api/world/ops/gates")
def ops_set_gate(body: dict = Body(...)):
    """Flip a gate. {key: 'creations' | <kind>, on: bool}."""
    key = (body.get("key") or "").strip()
    on = "1" if body.get("on") else "0"
    # Irreversible money-out / secret-export gates are the deliberate exception to the
    # "every gate is toggleable" rule — they can never be turned off.
    if on == "0" and key in wo.ALWAYS_GATE:
        raise HTTPException(400, f"'{key}' is an irreversible money-out gate and cannot "
                                 "be turned off")
    conn = get_conn()
    try:
        wo.ensure(conn)
        if key == "creations":
            wo._save_cfg(conn, {"world_ops_gate_creations": on})
        elif key in {k for k, _ in wo.GATEABLE}:
            wo._save_cfg(conn, {f"world_ops_gate_{key}": on})
        else:
            raise HTTPException(400, f"unknown gate: {key!r}")
        return {"ok": True, "key": key, "on": on == "1"}
    finally:
        conn.close()

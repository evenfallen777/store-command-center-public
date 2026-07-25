"""God Console — The Republic (survival strategy engine: state/convene/override)
and The Bible (BOOK.md is the Word; agents grow the teachings)."""
import json, logging, threading, os
from fastapi import HTTPException, Body

from deps import get_conn
import world_strategy
import world_bible
from ._base import router


# ── The Republic (survival strategy engine) ──────────────────────────────────
@router.get("/api/world/republic/state")
def republic_state():
    return world_strategy.state()


@router.post("/api/world/republic/convene")
def republic_convene():
    """Convene the assembly: assess → propose → vote → adopt → act → measure."""
    conn = get_conn()
    try:
        return world_strategy.run_cycle(conn)
    finally:
        conn.close()


# ── The Bible / Field Manual (BOOK.md reference doc + teachings + verified ops
#    notes; user-facing labels route through the naming theme) ─────────────────
@router.get("/api/world/bible/word")
def bible_word():
    return world_bible.word()


@router.get("/api/world/bible/teachings")
def bible_teachings(limit: int = 100, kind: str = None):
    import naming_theme
    return {
        "teachings": world_bible.teachings(limit, kind=kind),
        "label": naming_theme.pick("📖 The Company Bible", "📖 Field Manual & Ops Notes"),
        "ops_label": naming_theme.pick("🧭 Ops Notes", "🧭 Field Manual"),
    }


@router.post("/api/world/bible/ops/refresh")
def bible_ops_refresh(body: dict = Body(default={})):
    """Harvest ground-truth operational facts from the live tree; every fact
    passes the verification gate (dead references are rejected, never stored)."""
    res = world_bible.refresh_ops(include_recent=bool(body.get("include_recent", True)))
    res["revalidated"] = world_bible.revalidate()
    return res


@router.post("/api/world/bible/ops/revalidate")
def bible_ops_revalidate():
    """Re-check every operational teaching's references; flag stale, restore fixed."""
    return world_bible.revalidate()


@router.get("/api/world/bible/ops/context")
def bible_ops_context(topic: str = None, limit: int = 6):
    """The exact verified-notes block agents get injected (inspection surface)."""
    return {"context": world_bible.ops_context(topic=topic, limit=limit)}


@router.post("/api/world/republic/strategy/{sid}/override")
def republic_override(sid: int, body: dict = Body(...)):
    """God overrides the vote: 'adopt' (force + act) or 'reject' a strategy."""
    decision = body.get("decision")
    if decision not in ("adopt", "reject"):
        raise HTTPException(400, "decision must be 'adopt' or 'reject'")
    conn = get_conn()
    try:
        res = world_strategy.override(conn, sid, decision)
        if res is None:
            raise HTTPException(404, "strategy not found")
        return res
    finally:
        conn.close()

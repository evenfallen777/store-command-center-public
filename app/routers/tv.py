"""📺 TV routes — prompt-to-television: shows, episodes, canon (docs/TV-DESIGN.md, P1).

Create/list/get/edit/delete shows and episodes. Heavy work never runs
in-request: the showrunner / episode-writer / continuity LLM turns ride
orch.submit_llm (services_tv), and produce runs the unmodified studio pipeline
(services_studio.produce_project) as a background task. Statuses reconcile
LAZILY in the GET handlers (no polling threads): an episode flips
writing → ready when its studio project's storyboard lands ('draft'), and a
restart-orphaned 'producing' episode adopts its project's terminal state.

NSFW: creating an nsfw show requires the master toggle (else 404, same
convention as /api/nsfw/* and studio); nsfw shows are filtered from listings
unless nsfw.visible(); the safety floor screens the owner's prompt and every
model-authored bible/synopsis unconditionally (services_tv).
"""
import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional

import nsfw
from db import get_conn
import services_tv as _tv
from services_studio import _wipe_storyboard_rows

# ── schema (defined in db_schema.py; ensured once here at import — the same
#    pattern as routers/money/bills.py, so db.py needs no wiring) ─────────────
from db_schema import create_tv_tables as _create_tv_tables


def _ensure_tv_schema():
    conn = get_conn()
    try:
        _create_tv_tables(conn)   # commits internally
    finally:
        conn.close()


_ensure_tv_schema()

router = APIRouter()


# ── request bodies (studio.py style) ─────────────────────────────────────────

class ShowCreate(BaseModel):
    prompt: str                          # "what I want to watch" — small or large
    style: str = ""
    format_seconds: int = 150            # ladder rung 1 (2-3 min pilot) by default
    nsfw: bool = False


class ShowPatch(BaseModel):
    title: Optional[str] = None
    style: Optional[str] = None
    format_seconds: Optional[int] = None
    bible: Optional[dict] = None         # the owner can edit the bible anytime (§3.1)


class EpisodeCreate(BaseModel):
    notes: str = ""                      # optional owner steering for the writer


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_show_or_404(conn, show_id: int) -> dict:
    row = conn.execute("SELECT * FROM tv_shows WHERE id=?", (show_id,)).fetchone()
    if not row or ((row["nsfw"] or 0) and not nsfw.visible()):
        conn.close()
        raise HTTPException(404, "Show not found")
    return dict(row)


def _get_episode_or_404(conn, episode_id: int) -> dict:
    row = conn.execute("SELECT * FROM tv_episodes WHERE id=?", (episode_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Episode not found")
    _get_show_or_404(conn, row["show_id"])   # nsfw visibility gate
    return dict(row)


# ── shows ────────────────────────────────────────────────────────────────────

@router.post("/api/tv/shows")
def create_show(req: ShowCreate):
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "Tell the TV what you want to watch first")
    if req.nsfw:
        nsfw.require_enabled()           # 404 when the master toggle is off
    nsfw.refuse_unsafe(prompt)           # unconditional safety floor on the ask
    secs = min(max(int(req.format_seconds or 150), 30), 1800)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO tv_shows (prompt,style,format_seconds,nsfw,status) "
        "VALUES (?,?,?,?,'new')",
        (prompt, (req.style or "").strip(), secs, 1 if req.nsfw else 0))
    show_id = cur.lastrowid
    conn.commit()
    conn.close()
    _tv.submit_showrunner(show_id)       # async: status new → bible → ready
    return {"show_id": show_id}


@router.get("/api/tv/shows")
def list_shows():
    conn = get_conn()
    where = "" if nsfw.visible() else "WHERE COALESCE(s.nsfw,0)=0"
    rows = conn.execute(
        f"SELECT s.id,s.title,s.status,s.style,s.format_seconds,s.nsfw,s.error,"
        f"s.created_at,s.description,s.boxart_path,s.banner_path,s.art_status,"
        f"s.art_error,"
        f"(SELECT COUNT(*) FROM tv_episodes e WHERE e.show_id=s.id) AS episodes,"
        f"(SELECT COUNT(*) FROM tv_episodes e WHERE e.show_id=s.id AND "
        f"e.status='done') AS episodes_done "
        f"FROM tv_shows s {where} ORDER BY s.id DESC").fetchall()
    conn.close()
    return [_art_urls(_tv.reconcile_show_art(dict(r))) for r in rows]


def _art_urls(show: dict) -> dict:
    """boxart/banner as /designs URLs (the immutable-cached static mount) —
    the UI never sees filesystem paths."""
    for k in ("boxart_path", "banner_path"):
        p = show.get(k)
        show[k.replace("_path", "_url")] = f"/designs/tv/{Path(p).name}" if p else None
        show.pop(k, None)
    return show


@router.get("/api/tv/shows/{show_id}")
def get_show(show_id: int):
    conn = get_conn()
    show = _get_show_or_404(conn, show_id)
    eps = [dict(r) for r in conn.execute(
        "SELECT id,season,number,title,status,final_path,project_id,synopsis,error,"
        "created_at FROM tv_episodes WHERE show_id=? ORDER BY season, number",
        (show_id,))]
    conn.close()
    show["bible"] = _tv.bible_of(show) or None
    show["episodes"] = [_tv.reconcile_episode(e) for e in eps]
    return _art_urls(_tv.reconcile_show_art(show))


@router.post("/api/tv/shows/{show_id}/art")
def regen_show_art(show_id: int, background_tasks: BackgroundTasks):
    """Generate (or redo) the show's description + box art + banner. Queues an
    art-director LLM turn and two image renders on the unified queue — during
    an episode render they politely wait and drain between scene chains."""
    conn = get_conn()
    show = _get_show_or_404(conn, show_id)
    conn.close()
    if show["status"] in ("new", "bible"):
        raise HTTPException(409, "Show bible isn't written yet — art needs it")
    if _tv.art_busy(show):
        raise HTTPException(409, "Show art is already generating")
    _tv._upd_show(show_id, art_status="queued", art_error=None)
    background_tasks.add_task(_tv.art_task, show_id)
    return {"ok": True, "status": "queued"}


@router.patch("/api/tv/shows/{show_id}")
def patch_show(show_id: int, req: ShowPatch):
    conn = get_conn()
    _get_show_or_404(conn, show_id)
    updates = {}
    if req.title is not None:
        updates["title"] = req.title.strip()
    if req.style is not None:
        updates["style"] = req.style.strip()
    if req.format_seconds is not None:
        updates["format_seconds"] = min(max(int(req.format_seconds), 30), 1800)
    if req.bible is not None:
        # owner edit: stored as given, but the safety floor still screens it
        blob = json.dumps(req.bible, ensure_ascii=False)
        reason = nsfw.safety_check(blob)
        if reason:
            conn.close()
            raise HTTPException(400, f"Refused: {reason}")
        updates["bible"] = blob
    if updates:
        sets = ",".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE tv_shows SET {sets},updated_at=datetime('now') WHERE id=?",
                     (*updates.values(), show_id))
        conn.commit()
    conn.close()
    return {"ok": True}


@router.delete("/api/tv/shows/{show_id}")
def delete_show(show_id: int):
    conn = get_conn()
    _get_show_or_404(conn, show_id)
    eps = [dict(r) for r in conn.execute(
        "SELECT id,status,project_id FROM tv_episodes WHERE show_id=?", (show_id,))]
    if any(_tv.episode_busy(e) for e in eps):
        conn.close()
        raise HTTPException(409, "An episode is producing — wait for it to finish")
    # the episodes' studio projects were created BY this show: they go too
    for e in eps:
        if e["project_id"]:
            _wipe_storyboard_rows(conn, e["project_id"])
            conn.execute("DELETE FROM studio_projects WHERE id=?", (e["project_id"],))
    conn.execute("DELETE FROM tv_episodes WHERE show_id=?", (show_id,))
    conn.execute("DELETE FROM tv_shows WHERE id=?", (show_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── episodes ─────────────────────────────────────────────────────────────────

@router.post("/api/tv/shows/{show_id}/episodes")
def create_episode(show_id: int, req: EpisodeCreate):
    """Write the next episode: auto-assigns (season, number), then the
    episode-writer LLM turn → synopsis/beats → a studio project sized to the
    show's format, storyboarded with the bible's style/character lock."""
    conn = get_conn()
    show = _get_show_or_404(conn, show_id)
    bible = _tv.bible_of(show)
    if show["status"] != "ready" or not bible:
        conn.close()
        raise HTTPException(409, "Show has no bible yet — wait for the showrunner"
                            if show["status"] in ("new", "bible") else
                            "Show is not ready (showrunner failed — fix or recreate it)")
    season, number = _tv.next_slot(conn, show_id, bible)
    cur = conn.execute(
        "INSERT INTO tv_episodes (show_id,season,number,status) VALUES (?,?,?,'draft')",
        (show_id, season, number))
    episode_id = cur.lastrowid
    conn.commit()
    conn.close()
    _tv.submit_episode_writer(episode_id, notes=(req.notes or "").strip())
    return {"episode_id": episode_id}


@router.get("/api/tv/episodes/{episode_id}")
def get_episode(episode_id: int):
    conn = get_conn()
    ep = _get_episode_or_404(conn, episode_id)
    ep = _tv.reconcile_episode(ep)
    proj = None
    if ep.get("project_id"):
        row = conn.execute(
            "SELECT status,progress_msg,error,final_path FROM studio_projects WHERE id=?",
            (ep["project_id"],)).fetchone()
        proj = dict(row) if row else None
    conn.close()
    ep["project"] = proj
    return ep


@router.post("/api/tv/episodes/{episode_id}/produce")
def produce_episode_ep(episode_id: int, background_tasks: BackgroundTasks):
    """PRODUCE the episode: the whole unmodified studio pipeline (scene chains
    on the unified GPU queue → audio → assemble & mix) as one background task;
    on success the final is copied onto the episode and the continuity editor
    updates memory/canon. Manual per-episode — season mode is P2."""
    conn = get_conn()
    ep = _get_episode_or_404(conn, episode_id)
    ep = _tv.reconcile_episode(ep)
    if not ep.get("project_id"):
        conn.close()
        raise HTTPException(409, "Episode has no storyboard project yet — wait for the writer")
    if ep["status"] == "writing":
        conn.close()
        raise HTTPException(409, "Episode is still being written/storyboarded")
    if _tv.episode_busy(ep):
        conn.close()
        raise HTTPException(409, "Episode is already producing")
    conn.execute("UPDATE tv_episodes SET status='producing',error=NULL,"
                 "updated_at=datetime('now') WHERE id=?", (episode_id,))
    conn.commit()
    conn.close()
    background_tasks.add_task(_tv.produce_episode, episode_id)
    return {"ok": True}


@router.delete("/api/tv/episodes/{episode_id}")
def delete_episode(episode_id: int):
    conn = get_conn()
    ep = _get_episode_or_404(conn, episode_id)
    if _tv.episode_busy(ep):
        conn.close()
        raise HTTPException(409, "Episode is producing — wait for it to finish")
    if ep.get("project_id"):
        _wipe_storyboard_rows(conn, ep["project_id"])
        conn.execute("DELETE FROM studio_projects WHERE id=?", (ep["project_id"],))
    conn.execute("DELETE FROM tv_episodes WHERE id=?", (episode_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

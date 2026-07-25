"""AI Video Studio ("Director") routes — storyboard core (Phase 1) + render
slice (Phase 2).

docs/VIDEO-STUDIO-DESIGN.md §5. Create/list/get/edit a storyboard project and
edit its scenes/shots/audio cues. Heavy work never runs in-request: the
storyboard LLM turn rides orch.submit_llm; scene renders / audio / assemble
run as background tasks whose inner engines (run_chain_generation,
run_audio_clip) take the unified queue's GPU primitives themselves.

Phase 2 endpoints: scene/project render (+resume), audio, assemble & mix,
export-to-Social (draft post), owner footage upload (usable as a shot's
source clip and attachable on export).

NSFW: creating an nsfw project requires the master toggle (else 404, same
convention as /api/nsfw/*); nsfw projects are filtered from listings unless
nsfw.visible(); the safety floor screens the dropped idea unconditionally
(model output is screened in services_studio before rows are saved); export
to Social is blocked for nsfw projects (Social has no NSFW lane).
"""
import re
import time
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from typing import Optional

import nsfw
from config import VIDEOS_DIR
from db import get_conn
import services_studio as _studio
from services_studio import submit_storyboard, _snap_seconds, _wipe_storyboard_rows

router = APIRouter()

# project statuses during which no new heavy stage may start
_BUSY = ("storyboarding", "rendering", "assembling", "mixing")


# ── request bodies (videos.py style) ─────────────────────────────────────────

class ProjectCreate(BaseModel):
    idea: str
    kind: str = "short"                 # short (1 scene) | long (2-6 scenes)
    style: str = ""
    target_seconds: int = 20
    model_id: str = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
    width: int = 480
    height: int = 832
    fps: int = 16
    steps: int = 20
    nsfw: bool = False


class ProjectPatch(BaseModel):
    title: Optional[str] = None
    style: Optional[str] = None
    script: Optional[str] = None
    captions: Optional[str] = None
    music_engine: Optional[str] = None
    voice_engine: Optional[str] = None
    model_id: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[int] = None
    steps: Optional[int] = None
    target_seconds: Optional[int] = None


class StoryboardReq(BaseModel):
    notes: str = ""


class ScenePatch(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    voiceover: Optional[str] = None
    caption: Optional[str] = None


class ShotCreate(BaseModel):
    video_prompt: str
    seconds: float = 3.0
    caption: str = ""


class ShotPatch(BaseModel):
    video_prompt: Optional[str] = None
    seconds: Optional[float] = None
    caption: Optional[str] = None
    seed: Optional[int] = None
    source_path: Optional[str] = None    # own footage ('' clears back to AI-generated)


class CueCreate(BaseModel):
    project_id: int
    scene_id: Optional[int] = None
    kind: str                            # voiceover | music | sfx
    text: str
    engine: Optional[str] = None
    offset_s: float = 0.0
    duration_s: Optional[float] = None
    gain: Optional[float] = None


class CuePatch(BaseModel):
    text: Optional[str] = None
    engine: Optional[str] = None
    offset_s: Optional[float] = None
    duration_s: Optional[float] = None
    gain: Optional[float] = None
    scene_id: Optional[int] = None       # move an sfx cue to another scene


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_project_or_404(conn, project_id: int) -> dict:
    row = conn.execute("SELECT * FROM studio_projects WHERE id=?", (project_id,)).fetchone()
    if not row or ((row["nsfw"] or 0) and not nsfw.visible()):
        conn.close()
        raise HTTPException(404, "Project not found")
    return dict(row)


def _patch(conn, table: str, row_id: int, fields: dict):
    """Apply the non-None fields of a Pydantic patch to one row."""
    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        return
    sets = ",".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE {table} SET {sets},updated_at=datetime('now') WHERE id=?",
                 (*updates.values(), row_id))


def _touch_project(conn, project_id: int):
    conn.execute("UPDATE studio_projects SET updated_at=datetime('now') WHERE id=?",
                 (project_id,))


# ── projects / storyboard ────────────────────────────────────────────────────

@router.post("/api/studio/projects")
def create_project(req: ProjectCreate):
    idea = (req.idea or "").strip()
    if not idea:
        raise HTTPException(400, "Drop an idea first")
    if req.nsfw:
        nsfw.require_enabled()           # 404 when the master toggle is off
    # Safety floor: the dropped idea is screened UNCONDITIONALLY (sfw included —
    # the floor only trips on minors / real-person / non-consent themes).
    nsfw.refuse_unsafe(idea)
    kind = "long" if (req.kind or "").lower() == "long" else "short"
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO studio_projects (idea,kind,style,target_seconds,model_id,"
        "width,height,fps,steps,nsfw,status) VALUES (?,?,?,?,?,?,?,?,?,?,'new')",
        (idea, kind, req.style.strip(), req.target_seconds, req.model_id,
         req.width, req.height, req.fps, req.steps, 1 if req.nsfw else 0))
    project_id = cur.lastrowid
    conn.commit()
    conn.close()
    task_id = submit_storyboard(project_id)
    return {"id": project_id, "task_id": task_id}


@router.get("/api/studio/projects")
def list_projects():
    conn = get_conn()
    where = "" if nsfw.visible() else "WHERE COALESCE(nsfw,0)=0"
    rows = conn.execute(
        f"SELECT p.*, (SELECT COUNT(*) FROM studio_scenes s WHERE s.project_id=p.id) "
        f"AS scene_count FROM studio_projects p {where} ORDER BY p.id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/api/studio/projects/{project_id}")
def get_project(project_id: int):
    conn = get_conn()
    proj = _get_project_or_404(conn, project_id)
    scenes = [dict(s) for s in conn.execute(
        "SELECT * FROM studio_scenes WHERE project_id=? ORDER BY idx", (project_id,))]
    for sc in scenes:
        sc["shots"] = [dict(sh) for sh in conn.execute(
            "SELECT * FROM studio_shots WHERE scene_id=? ORDER BY idx", (sc["id"],))]
        # live render telemetry: the scene's chain + the in-flight segment (the UI
        # progress bar is completed_segments + the current segment's progress)
        if sc.get("chain_id"):
            ch = conn.execute(
                "SELECT id,status,completed_segments,total_segments,compiled_path,error "
                "FROM video_chains WHERE id=?", (sc["chain_id"],)).fetchone()
            if ch:
                info = dict(ch)
                cur = conn.execute(
                    "SELECT chain_index,progress,progress_msg FROM videos "
                    "WHERE chain_id=? AND status='generating' ORDER BY id DESC LIMIT 1",
                    (sc["chain_id"],)).fetchone()
                info["current"] = dict(cur) if cur else None
                sc["chain"] = info
    # Phase-2 audio artifacts: surface the music/VO clip lifecycle for the UI chips
    for key in ("music_clip_id", "vo_clip_id"):
        if proj.get(key):
            r = conn.execute("SELECT id,status,error,audio_path FROM audio_clips WHERE id=?",
                             (proj[key],)).fetchone()
            proj[key.replace("_id", "")] = dict(r) if r else None
    cues = [dict(c) for c in conn.execute(
        "SELECT * FROM studio_cues WHERE project_id=? "
        "ORDER BY CASE kind WHEN 'music' THEN 0 WHEN 'voiceover' THEN 1 ELSE 2 END, id",
        (project_id,))]
    conn.close()
    proj["scenes"] = scenes
    proj["cues"] = cues
    return proj


@router.patch("/api/studio/projects/{project_id}")
def patch_project(project_id: int, req: ProjectPatch):
    conn = get_conn()
    _get_project_or_404(conn, project_id)
    _patch(conn, "studio_projects", project_id, req.dict())
    conn.commit()
    conn.close()
    return {"ok": True}


@router.delete("/api/studio/projects/{project_id}")
def delete_project(project_id: int):
    conn = get_conn()
    _get_project_or_404(conn, project_id)
    _wipe_storyboard_rows(conn, project_id)
    conn.execute("DELETE FROM studio_projects WHERE id=?", (project_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/studio/projects/{project_id}/storyboard")
def rerun_storyboard(project_id: int, req: StoryboardReq):
    """(Re)run the storyboard LLM: wipes draft scenes/shots/cues, keeps settings."""
    conn = get_conn()
    proj = _get_project_or_404(conn, project_id)
    if proj["status"] == "storyboarding":
        conn.close()
        raise HTTPException(409, "Storyboard is already running")
    conn.close()
    task_id = submit_storyboard(project_id, notes=req.notes)
    return {"task_id": task_id}


# ── Quick Meme fast lane ─────────────────────────────────────────────────────

class MemeReq(BaseModel):
    text: str                            # the one-line meme idea (becomes the caption)
    style: str = ""
    seconds: float = 5.0                 # clamped ~3-6 s, snapped by the engine
    model_id: str = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
    width: int = 480
    height: int = 832
    fps: int = 16
    steps: int = 15                      # fast lane: lighter than the studio default 20
    nsfw: bool = False


@router.post("/api/studio/meme")
def quick_meme(req: MemeReq, background_tasks: BackgroundTasks):
    """😂 Quick Meme: one-line meme text → instant short, no storyboard editing.
    A THIN preset over the normal engine: seed_meme_storyboard writes a
    deterministic 1-scene/1-shot board (NO LLM turn) through the same
    validator+writer as the LLM path, then ONE background task runs the
    unmodified produce pipeline (render → audio → assemble & mix) and burns
    the caption on with a CPU drawtext pass. Same NSFW gates as create."""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "Drop a meme idea first")
    if len(text) > 300:
        raise HTTPException(400, "Keep the meme under 300 characters")
    if req.nsfw:
        nsfw.require_enabled()           # 404 when the master toggle is off
    nsfw.refuse_unsafe(text)             # unconditional safety floor on the idea
    secs = min(max(float(req.seconds or 5.0), 3.0), 6.0)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO studio_projects (idea,kind,style,target_seconds,model_id,"
        "width,height,fps,steps,nsfw,status,title) VALUES (?,?,?,?,?,?,?,?,?,?,'new',?)",
        (text, "short", req.style.strip(), int(round(secs)), req.model_id,
         req.width, req.height, req.fps, req.steps, 1 if req.nsfw else 0,
         ("😂 " + text)[:80]))
    project_id = cur.lastrowid
    conn.commit()
    conn.close()
    try:
        _studio.seed_meme_storyboard(project_id)    # instant — no LLM
    except ValueError as ex:
        conn = get_conn()
        conn.execute("UPDATE studio_projects SET status='failed',error=?,"
                     "updated_at=datetime('now') WHERE id=?",
                     (str(ex)[:500], project_id))
        conn.commit()
        conn.close()
        raise HTTPException(400, f"Meme refused: {ex}")
    conn = get_conn()
    conn.execute("UPDATE studio_projects SET status='rendering',error=NULL,"
                 "progress_msg='😂 Cooking the meme…',updated_at=datetime('now') "
                 "WHERE id=?", (project_id,))
    conn.commit()
    conn.close()
    background_tasks.add_task(_studio.produce_meme, project_id)
    return {"id": project_id, "status": "rendering"}


# ── trends → storyboard ──────────────────────────────────────────────────────

class TrendStoryboardReq(BaseModel):
    proposal_id: Optional[int] = None    # a proposals row the trend scanner wrote
    text: Optional[str] = None           # or raw trend text (extra angle when both)
    kind: str = "short"
    style: str = ""
    target_seconds: int = 20


@router.post("/api/studio/from-trend")
def storyboard_from_trend(req: TrendStoryboardReq):
    """Trend → Studio in one click: turn a trend proposal (or raw trend text)
    into a storyboard project. Reads the proposals row the scanner wrote (the
    scanner itself is untouched); the storyboard rides the normal
    submit_storyboard → orch.submit_llm path, safety floor included."""
    extra = (req.text or "").strip()
    prop = None
    if req.proposal_id:
        conn = get_conn()
        row = conn.execute("SELECT * FROM proposals WHERE id=?",
                           (req.proposal_id,)).fetchone()
        conn.close()
        if not row:
            raise HTTPException(404, "Proposal not found")
        prop = dict(row)
    if not prop and not extra:
        raise HTTPException(400, "Pass a proposal_id or trend text")

    if prop:
        bits = [f"Trending now ({prop.get('source_label') or prop.get('source') or 'trend'}): "
                f"{prop['title']}"]
        if (prop.get("description") or "").strip():
            bits.append(prop["description"].strip())
        if (prop.get("tags") or "").strip():
            bits.append(f"Related tags: {prop['tags'].strip()}")
        if extra:
            bits.append(f"Owner's angle: {extra}")
        bits.append("Make a punchy social video riding this trend.")
        idea = "\n".join(bits)
    else:
        idea = f"Trending now: {extra}\nMake a punchy social video riding this trend."

    out = create_project(ProjectCreate(
        idea=idea[:2000], kind=req.kind, style=req.style.strip(),
        target_seconds=req.target_seconds))
    if prop:
        out["proposal_id"] = prop["id"]
    return out


# ── scenes ───────────────────────────────────────────────────────────────────

def _scene_or_404(conn, scene_id: int) -> dict:
    row = conn.execute("SELECT * FROM studio_scenes WHERE id=?", (scene_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Scene not found")
    _get_project_or_404(conn, row["project_id"])   # nsfw visibility gate
    return dict(row)


@router.patch("/api/studio/scenes/{scene_id}")
def patch_scene(scene_id: int, req: ScenePatch):
    conn = get_conn()
    scene = _scene_or_404(conn, scene_id)
    _patch(conn, "studio_scenes", scene_id, req.dict())
    # keep the scene's voiceover cue in sync with an edited voiceover
    if req.voiceover is not None:
        vo = req.voiceover.strip()
        cue = conn.execute("SELECT id FROM studio_cues WHERE scene_id=? AND "
                           "kind='voiceover'", (scene_id,)).fetchone()
        if cue and vo:
            conn.execute("UPDATE studio_cues SET text=?,updated_at=datetime('now') "
                         "WHERE id=?", (vo, cue["id"]))
        elif cue and not vo:
            conn.execute("DELETE FROM studio_cues WHERE id=?", (cue["id"],))
        elif vo:
            conn.execute("INSERT INTO studio_cues (project_id,scene_id,kind,text) "
                         "VALUES (?,?,'voiceover',?)", (scene["project_id"], scene_id, vo))
    _touch_project(conn, scene["project_id"])
    conn.commit()
    conn.close()
    return {"ok": True}


# ── shots ────────────────────────────────────────────────────────────────────

@router.post("/api/studio/scenes/{scene_id}/shots")
def add_shot(scene_id: int, req: ShotCreate):
    prompt = (req.video_prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "video_prompt is required")
    conn = get_conn()
    scene = _scene_or_404(conn, scene_id)
    n = conn.execute("SELECT COUNT(*) c FROM studio_shots WHERE scene_id=?",
                     (scene_id,)).fetchone()["c"]
    if n >= 5:
        conn.close()
        raise HTTPException(400, "A scene holds at most 5 shots")
    secs, frames = _snap_seconds(req.seconds)
    cur = conn.execute(
        "INSERT INTO studio_shots (scene_id,idx,video_prompt,seconds,num_frames,caption) "
        "VALUES (?,?,?,?,?,?)", (scene_id, n, prompt, secs, frames, req.caption))
    _resync_scene_est(conn, scene_id)
    _touch_project(conn, scene["project_id"])
    conn.commit()
    shot_id = cur.lastrowid
    conn.close()
    return {"id": shot_id, "seconds": secs, "num_frames": frames}


def _shot_or_404(conn, shot_id: int) -> dict:
    row = conn.execute("SELECT * FROM studio_shots WHERE id=?", (shot_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Shot not found")
    return dict(row)


def _resync_scene_est(conn, scene_id: int):
    conn.execute("UPDATE studio_scenes SET est_seconds=(SELECT COALESCE(SUM(seconds),0) "
                 "FROM studio_shots WHERE scene_id=?),updated_at=datetime('now') "
                 "WHERE id=?", (scene_id, scene_id))


@router.patch("/api/studio/shots/{shot_id}")
def patch_shot(shot_id: int, req: ShotPatch):
    conn = get_conn()
    shot = _shot_or_404(conn, shot_id)
    scene = _scene_or_404(conn, shot["scene_id"])
    fields = req.dict()
    if fields.get("seconds") is not None:
        secs, frames = _snap_seconds(fields["seconds"])
        fields["seconds"] = secs
        fields["num_frames"] = frames
    # own footage: '' clears; a path must be a real file under VIDEOS_DIR
    sp = fields.pop("source_path", None)
    if sp is not None:
        sp = sp.strip()
        if sp:
            p = Path(sp).resolve()
            if not (str(p).startswith(str(Path(VIDEOS_DIR).resolve()) + "/") and p.is_file()):
                conn.close()
                raise HTTPException(400, "source_path must be an uploaded studio file")
            conn.execute("UPDATE studio_shots SET source_path=?,"
                         "updated_at=datetime('now') WHERE id=?", (str(p), shot_id))
        else:
            conn.execute("UPDATE studio_shots SET source_path=NULL,"
                         "updated_at=datetime('now') WHERE id=?", (shot_id,))
    _patch(conn, "studio_shots", shot_id, fields)
    _resync_scene_est(conn, shot["scene_id"])
    _touch_project(conn, scene["project_id"])
    conn.commit()
    conn.close()
    return {"ok": True}


@router.delete("/api/studio/shots/{shot_id}")
def delete_shot(shot_id: int):
    conn = get_conn()
    shot = _shot_or_404(conn, shot_id)
    scene = _scene_or_404(conn, shot["scene_id"])
    conn.execute("DELETE FROM studio_shots WHERE id=?", (shot_id,))
    # re-pack shot order so idx stays 0..n-1
    rows = conn.execute("SELECT id FROM studio_shots WHERE scene_id=? ORDER BY idx",
                        (shot["scene_id"],)).fetchall()
    for i, r in enumerate(rows):
        conn.execute("UPDATE studio_shots SET idx=? WHERE id=?", (i, r["id"]))
    _resync_scene_est(conn, shot["scene_id"])
    _touch_project(conn, scene["project_id"])
    conn.commit()
    conn.close()
    return {"ok": True}


# ── audio cues ───────────────────────────────────────────────────────────────

@router.post("/api/studio/cues")
def add_cue(req: CueCreate):
    kind = (req.kind or "").strip().lower()
    if kind not in ("voiceover", "music", "sfx"):
        raise HTTPException(400, "kind must be voiceover, music or sfx")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    conn = get_conn()
    _get_project_or_404(conn, req.project_id)
    if req.scene_id is not None:
        _scene_or_404(conn, req.scene_id)
    gain = req.gain if req.gain is not None else (0.25 if kind == "music" else
                                                  0.9 if kind == "sfx" else 1.0)
    cur = conn.execute(
        "INSERT INTO studio_cues (project_id,scene_id,kind,text,engine,offset_s,"
        "duration_s,gain) VALUES (?,?,?,?,?,?,?,?)",
        (req.project_id, req.scene_id, kind, text, req.engine, req.offset_s,
         req.duration_s, gain))
    _touch_project(conn, req.project_id)
    conn.commit()
    cue_id = cur.lastrowid
    conn.close()
    return {"id": cue_id}


def _cue_or_404(conn, cue_id: int) -> dict:
    row = conn.execute("SELECT * FROM studio_cues WHERE id=?", (cue_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Cue not found")
    _get_project_or_404(conn, row["project_id"])
    return dict(row)


@router.patch("/api/studio/cues/{cue_id}")
def patch_cue(cue_id: int, req: CuePatch):
    conn = get_conn()
    cue = _cue_or_404(conn, cue_id)
    if req.scene_id is not None:
        scene = _scene_or_404(conn, req.scene_id)
        if scene["project_id"] != cue["project_id"]:
            conn.close()
            raise HTTPException(400, "Scene belongs to a different project")
    _patch(conn, "studio_cues", cue_id, req.dict())
    _touch_project(conn, cue["project_id"])
    conn.commit()
    conn.close()
    return {"ok": True}


@router.delete("/api/studio/cues/{cue_id}")
def delete_cue(cue_id: int):
    conn = get_conn()
    cue = _cue_or_404(conn, cue_id)
    conn.execute("DELETE FROM studio_cues WHERE id=?", (cue_id,))
    _touch_project(conn, cue["project_id"])
    conn.commit()
    conn.close()
    return {"ok": True}


# ═══ Phase 2 — rendering / audio / assemble / export / own footage ═══════════

def _require_idle(proj: dict, conn):
    """409 while the project is genuinely busy. A busy STATUS alone is not
    proof: a restart kills the background threads and strands rows in
    'rendering'/'mixing' forever (and 409ing those made the project
    unrecoverable). If no thread in this process owns the project — with a
    90 s grace so a just-queued task can register — recover it to 'failed'
    and let the new request proceed."""
    if proj["status"] not in _BUSY:
        return
    row = conn.execute(
        "SELECT (strftime('%s','now') - strftime('%s', COALESCE(updated_at, "
        "created_at))) AS age FROM studio_projects WHERE id=?", (proj["id"],)).fetchone()
    age = (row["age"] if row and row["age"] is not None else 0)
    if _studio.project_active(proj["id"]) or age < 90:
        conn.close()
        raise HTTPException(409, f"Project is busy ({proj['status']}) — wait for it to finish")
    conn.execute("UPDATE studio_projects SET status='failed',progress_msg=NULL,"
                 "error='Interrupted mid-run (server restarted) — recovered; run again',"
                 "updated_at=datetime('now') WHERE id=?", (proj["id"],))
    conn.commit()
    proj["status"] = "failed"


def _all_scenes_done(conn, project_id: int) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN status='done' AND scene_path IS NOT NULL "
        "THEN 1 ELSE 0 END) AS done FROM studio_scenes WHERE project_id=?",
        (project_id,)).fetchone()
    return bool(row["n"]) and row["n"] == (row["done"] or 0)


@router.post("/api/studio/scenes/{scene_id}/render")
def render_scene_ep(scene_id: int, background_tasks: BackgroundTasks):
    """Render ONE scene: creates a fresh video_chains row from its shots and runs
    it through the unmodified chain engine (orch.video_exclusive)."""
    conn = get_conn()
    scene = _scene_or_404(conn, scene_id)
    proj = _get_project_or_404(conn, scene["project_id"])
    _require_idle(proj, conn)
    if scene["status"] in ("queued", "rendering") and _studio.project_active(proj["id"]):
        conn.close()
        raise HTTPException(409, "Scene is already rendering")
    n = conn.execute("SELECT COUNT(*) c FROM studio_shots WHERE scene_id=?",
                     (scene_id,)).fetchone()["c"]
    if not n:
        conn.close()
        raise HTTPException(400, "Scene has no shots")
    conn.execute("UPDATE studio_scenes SET status='queued',error=NULL,"
                 "updated_at=datetime('now') WHERE id=?", (scene_id,))
    _touch_project(conn, scene["project_id"])
    conn.commit()
    conn.close()
    background_tasks.add_task(_studio.render_scene_task, scene_id)
    return {"ok": True}


@router.post("/api/studio/scenes/{scene_id}/resume")
def resume_scene_ep(scene_id: int, background_tasks: BackgroundTasks):
    """Retry a failed scene from its last completed segment (resume_chain_generation)."""
    conn = get_conn()
    scene = _scene_or_404(conn, scene_id)
    proj = _get_project_or_404(conn, scene["project_id"])
    _require_idle(proj, conn)
    if not scene["chain_id"]:
        conn.close()
        raise HTTPException(400, "Scene has no chain yet — use render")
    if scene["status"] in ("queued", "rendering") and _studio.project_active(proj["id"]):
        conn.close()
        raise HTTPException(409, "Scene is already rendering")
    conn.execute("UPDATE studio_scenes SET status='queued',error=NULL,"
                 "updated_at=datetime('now') WHERE id=?", (scene_id,))
    conn.commit()
    conn.close()
    background_tasks.add_task(_studio.resume_scene_task, scene_id)
    return {"ok": True}


@router.post("/api/studio/projects/{project_id}/render")
def render_project_ep(project_id: int, background_tasks: BackgroundTasks):
    """PRODUCE the whole video in one background pipeline: render every
    not-done scene sequentially (one 3060 — chains serialize on the unified
    queue's video_exclusive), then generate the music/voiceover cues via
    run_audio_clip, then assemble & mix the final (with stream verification).
    The per-stage endpoints below remain for manually redoing one stage."""
    conn = get_conn()
    proj = _get_project_or_404(conn, project_id)
    _require_idle(proj, conn)
    n = conn.execute("SELECT COUNT(*) c FROM studio_scenes WHERE project_id=?",
                     (project_id,)).fetchone()["c"]
    if not n:
        conn.close()
        raise HTTPException(400, "No scenes — storyboard first")
    conn.execute("UPDATE studio_projects SET status='rendering',error=NULL,"
                 "progress_msg='Queued…',updated_at=datetime('now') WHERE id=?",
                 (project_id,))
    conn.commit()
    conn.close()
    background_tasks.add_task(_studio.produce_project, project_id)
    return {"ok": True}


@router.post("/api/studio/projects/{project_id}/audio")
def render_audio_ep(project_id: int, background_tasks: BackgroundTasks):
    """Phase-2 audio: ONE music bed + ONE whole-script voiceover, both via
    run_audio_clip on the unified queue. Needs every scene rendered."""
    conn = get_conn()
    proj = _get_project_or_404(conn, project_id)
    _require_idle(proj, conn)
    if not _all_scenes_done(conn, project_id):
        conn.close()
        raise HTTPException(409, "Render all scenes first — audio needs measured durations")
    conn.execute("UPDATE studio_projects SET status='mixing',error=NULL,"
                 "progress_msg='Queued audio generation…',updated_at=datetime('now') "
                 "WHERE id=?", (project_id,))
    conn.commit()
    conn.close()
    background_tasks.add_task(_studio.render_audio_task, project_id)
    return {"ok": True}


@router.post("/api/studio/projects/{project_id}/assemble")
def assemble_ep(project_id: int, background_tasks: BackgroundTasks):
    """Stitch scene clips → full video, mix the generated audio on → final_path.
    CPU-only ffmpeg (no GPU acquire, same policy as the chain auto-compile)."""
    conn = get_conn()
    proj = _get_project_or_404(conn, project_id)
    _require_idle(proj, conn)
    if not _all_scenes_done(conn, project_id):
        conn.close()
        raise HTTPException(409, "Render all scenes first")
    conn.execute("UPDATE studio_projects SET status='assembling',error=NULL,"
                 "progress_msg='Queued…',updated_at=datetime('now') WHERE id=?",
                 (project_id,))
    conn.commit()
    conn.close()
    background_tasks.add_task(_studio.assemble_task, project_id)
    return {"ok": True}


# ── export to Social ─────────────────────────────────────────────────────────

class ExportReq(BaseModel):
    platforms: list[str] = ["youtube"]
    title: Optional[str] = None
    media_path: Optional[str] = None    # optional override (e.g. own uploaded footage)


@router.post("/api/studio/projects/{project_id}/export")
def export_project(project_id: int, req: ExportReq):
    """Create a DRAFT social post with the final rendered video attached (reuses
    the social_posts/media path — publish stays a Social-tab action)."""
    import json as _json
    from routers.social import PLATFORMS as _SOCIAL
    conn = get_conn()
    proj = _get_project_or_404(conn, project_id)
    if proj["nsfw"]:
        conn.close()
        raise HTTPException(404, "Not found")   # no NSFW lane on Social (design §2.5)
    media = (req.media_path or proj["final_path"] or "").strip()
    if not media:
        conn.close()
        raise HTTPException(409, "No final video yet — assemble & mix first")
    mp = Path(media).resolve()
    if not (str(mp).startswith(str(Path(VIDEOS_DIR).resolve()) + "/") and mp.is_file()):
        conn.close()
        raise HTTPException(400, "Media file not found")
    platforms = [p for p in (req.platforms or []) if p in _SOCIAL] or ["youtube"]

    # captions = "social caption\n#hash #tags" (written by the storyboarder)
    lines = [ln.strip() for ln in (proj["captions"] or "").splitlines() if ln.strip()]
    hashtags = " ".join(ln for ln in lines if ln.startswith("#"))
    caption = "\n".join(ln for ln in lines if not ln.startswith("#")) \
        or (proj["logline"] or proj["idea"] or "")[:300]
    title = (req.title or proj["title"] or caption or "Studio video").strip()[:95]
    scenes = conn.execute("SELECT chain_id FROM studio_scenes WHERE project_id=? "
                          "ORDER BY idx", (project_id,)).fetchall()
    chain_id = scenes[0]["chain_id"] if len(scenes) == 1 else None
    per_platform = {p: {"title": title} for p in platforms}
    cur = conn.execute(
        "INSERT INTO social_posts (title,caption,hashtags,platforms,media_type,"
        "media_path,status,source,chain_id,per_platform) "
        "VALUES (?,?,?,?,'video',?,'draft','studio',?,?)",
        (title, caption, hashtags, _json.dumps(platforms), str(mp), chain_id,
         _json.dumps(per_platform)))
    post_id = cur.lastrowid
    conn.execute("UPDATE studio_projects SET social_post_id=?,updated_at=datetime('now') "
                 "WHERE id=?", (post_id, project_id))
    conn.commit()
    conn.close()
    return {"post_id": post_id, "platforms": platforms}


# ── own footage (manual local-media upload) ──────────────────────────────────

_UP_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
_UP_MAX = 300 * 1024 * 1024   # 300 MB


@router.post("/api/studio/upload")
async def studio_upload(file: UploadFile = File(...), shot_id: int = Form(0)):
    """Bring the owner's OWN footage into the studio. Stored under VIDEOS_DIR
    (served by the existing /videos static route). Optional shot_id attaches it
    as that shot's source clip in the same call. Type/size validated; the file
    must actually be a playable video (ffprobe)."""
    from services_media_audio import _video_duration
    name = file.filename or "footage.mp4"
    ext = Path(name).suffix.lower()
    if ext not in _UP_EXT:
        raise HTTPException(400, f"Use one of: {', '.join(sorted(_UP_EXT))}")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem)[:50] or "footage"
    dest = Path(VIDEOS_DIR) / f"studio_upload_{int(time.time())}_{safe}{ext}"
    size = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > _UP_MAX:
                    raise HTTPException(400, "File too large (max 300 MB)")
                f.write(chunk)
        if not size:
            raise HTTPException(400, "That file was empty")
        dur = _video_duration(str(dest))
        if dur <= 0:
            raise HTTPException(400, "That file is not a playable video")
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    out = {"ok": True, "path": str(dest), "filename": dest.name,
           "url": f"/videos/{dest.name}", "duration_s": round(dur, 2), "size": size}
    if shot_id:
        conn = get_conn()
        shot = _shot_or_404(conn, shot_id)
        _scene_or_404(conn, shot["scene_id"])   # nsfw visibility gate
        conn.execute("UPDATE studio_shots SET source_path=?,status='draft',"
                     "updated_at=datetime('now') WHERE id=?", (str(dest), shot_id))
        conn.commit()
        conn.close()
        out["shot_id"] = shot_id
    return out


@router.get("/api/studio/uploads")
def list_uploads():
    """The owner's uploaded footage (newest first) for the shot-source picker."""
    out = []
    try:
        files = sorted(Path(VIDEOS_DIR).glob("studio_upload_*"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        files = []
    for p in files[:100]:
        out.append({"filename": p.name, "path": str(p), "url": f"/videos/{p.name}",
                    "size": p.stat().st_size})
    return out

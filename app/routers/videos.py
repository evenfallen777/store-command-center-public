"""videos routes."""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Form, UploadFile, File
from deps import *
from services import *
import services as _svc

router = APIRouter()


# Omitted numeric fields (None) resolve through video_gen_settings(model_id) —
# per-model catalog defaults + the owner's saved overrides. The catalog defaults
# equal the old hardcoded ones (832×480 · 49f · 20 steps · 16 fps · strength 0.7),
# so requests behave exactly as before unless the owner tunes a model. Explicit
# values in the request always win (the UI sends explicit values).
class VideoRequest(BaseModel):
    prompt: str
    width: int | None = None
    height: int | None = None
    num_frames: int | None = None
    steps: int | None = None
    fps: int | None = None
    seed: int = 0
    model_id: str = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
    # "Generate with audio" (opt-in, default OFF = today's behavior): on success
    # the existing add_video_audio bridge runs automatically (music + narration).
    audio_enabled: bool = False
    music_prompt: str = ""    # empty → add_video_audio derives one from the prompt
    narration: str = ""       # empty → music only, no voice
    # Full layered settings (chain parity): music/voice/sfx toggles + per-layer
    # volumes + engines — same keys as services_media_chain.DEFAULT_CHAIN_AUDIO.
    # None = legacy behavior (music + voice-if-narration, fixed volumes).
    audio_settings: dict | None = None

class VideoChainRequest(BaseModel):
    title: str = ""
    concept: str = ""
    prompts: list[str]
    model_id: str = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
    width: int | None = None
    height: int | None = None
    num_frames: int | None = None
    steps: int | None = None
    fps: int | None = None
    strength: float | None = None   # 0=copy prev video, 1=ignore it. 0.6-0.75 = smooth continuation
    # Layered chain audio (opt-in, default OFF = today's behavior). Settings
    # keys mirror services_media_chain.DEFAULT_CHAIN_AUDIO (music/voice/sfx
    # toggles + per-layer volumes + engines + music_prompt/narration overrides).
    audio_enabled: bool = False
    audio_settings: dict | None = None


def _resolve_video_params(req, keys=("width", "height", "num_frames", "steps", "fps")):
    """Explicit request value wins; omitted fields take the model's effective
    per-model setting; final fallback = the old hardcoded defaults."""
    hard = {"width": 832, "height": 480, "num_frames": 49, "steps": 20,
            "fps": 16, "strength": 0.7}
    s = video_gen_settings(req.model_id)
    out = {}
    for k in keys:
        v = getattr(req, k)
        out[k] = v if v is not None else s.get(k, hard[k])
    return out

class ChainPromptsRequest(BaseModel):
    concept: str
    num_segments: int = 3
    style: str = ""


# "Audio that matches the video": appended to the base 'video_chain' system
# prompt so the SAME LLM pass also writes the matching audio — a narration
# line per scene, one overall music vibe, and per-scene SFX hints. This text
# lives HERE (router layer) on purpose: the prompt registry (prompts.py) is
# shared with other live sessions. The addendum overrides the base prompt's
# "return a JSON array" instruction with a JSON object that still carries the
# same prompts array, and the old array/line parsers below stay as fallbacks,
# so a model that ignores the addendum degrades to today's prompts-only shape.
_CHAIN_AUDIO_ADDENDUM = """

ADDITIONALLY: this video gets a generated soundtrack, so write the matching audio in the SAME response.
Instead of a bare JSON array, return ONLY one JSON object (no markdown, no code fences, no other text) with exactly these keys:
  "prompts": the JSON array of scene prompt strings described above
  "narrations": array of the SAME length — for each scene, ONE short spoken voice-over line (max 20 words) matching that scene's action; natural spoken language, present tense, no camera/visual-prompt jargon
  "music": ONE short background-music description for the WHOLE video (instrumental; genre, tempo, mood — max 15 words)
  "sfx": array of the SAME length — for each scene, a 3-8 word sound-effect hint matching its action (foley, no music, no melody)"""

@router.get("/api/video-chains")
def list_video_chains():
    conn = get_conn()
    chains = conn.execute(
        # studio_scene_id chains belong to the Director tab, not this gallery
        "SELECT * FROM video_chains WHERE COALESCE(nsfw,0)=0 AND studio_scene_id IS NULL "
        "ORDER BY created_at DESC"
    ).fetchall()
    result = []
    for c in chains:
        row = dict(c)
        row["prompts"] = json.loads(row["prompts"]) if row["prompts"] else []
        # A DB path whose file is gone (cleanup, failed verify, mid-copy race)
        # must not reach the UI — it renders a dead full-width player showing
        # "No video with supported format and MIME type found".
        for k in ("compiled_path", "final_path"):
            if row.get(k) and not Path(row[k]).exists():
                row[k] = None
        segs = conn.execute(
            "SELECT id,chain_index,status,video_path,prompt,progress,progress_msg FROM videos WHERE chain_id=? ORDER BY chain_index",
            (row["id"],)
        ).fetchall()
        row["segments"] = [dict(s) for s in segs]
        result.append(row)
    conn.close()
    return result

@router.get("/api/video-chains/{chain_id}")
def get_video_chain(chain_id: int):
    conn = get_conn()
    chain = conn.execute("SELECT * FROM video_chains WHERE id=?", (chain_id,)).fetchone()
    if not chain:
        conn.close()
        raise HTTPException(404, "Chain not found")
    row = dict(chain)
    row["prompts"] = json.loads(row["prompts"]) if row["prompts"] else []
    segs = conn.execute(
        "SELECT id,chain_index,status,video_path,prompt,progress,progress_msg FROM videos WHERE chain_id=? ORDER BY chain_index",
        (chain_id,)
    ).fetchall()
    row["segments"] = [dict(s) for s in segs]
    conn.close()
    return row

@router.post("/api/video-chains")
def create_video_chain(req: VideoChainRequest, background_tasks: BackgroundTasks):
    if len(req.prompts) < 1:
        raise HTTPException(400, "Need at least 1 prompt to create a chain")
    check_video_vram_or_raise(req.model_id)   # fail fast if this model can't fit on the node's GPU

    title = req.title or f"Chain: {req.prompts[0][:40]}"
    p = _resolve_video_params(req, keys=("width", "height", "num_frames",
                                         "steps", "fps", "strength"))
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO video_chains (title,concept,model_id,width,height,num_frames,steps,fps,strength,prompts,total_segments,audio_enabled,audio_settings) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (title, req.concept, req.model_id, p["width"], p["height"], p["num_frames"],
         p["steps"], p["fps"], p["strength"], json.dumps(req.prompts), len(req.prompts),
         1 if req.audio_enabled else 0,
         json.dumps(req.audio_settings) if req.audio_settings else None)
    )
    chain_id = cur.lastrowid
    conn.commit()
    conn.close()
    background_tasks.add_task(run_chain_generation, chain_id)
    return {"chain_id": chain_id, "message": f"Chain started with {len(req.prompts)} segments"}

@router.delete("/api/video-chains/{chain_id}")
def delete_video_chain(chain_id: int):
    conn = get_conn()
    chain = conn.execute("SELECT * FROM video_chains WHERE id=?", (chain_id,)).fetchone()
    if not chain:
        conn.close()
        raise HTTPException(404, "Chain not found")
    segs = conn.execute("SELECT video_path FROM videos WHERE chain_id=?", (chain_id,)).fetchall()
    for s in segs:
        if s["video_path"]:
            try:
                Path(s["video_path"]).unlink(missing_ok=True)
            except Exception:
                pass
    if chain["compiled_path"]:
        try:
            Path(chain["compiled_path"]).unlink(missing_ok=True)
        except Exception:
            pass
    conn.execute("DELETE FROM videos WHERE chain_id=?", (chain_id,))
    conn.execute("DELETE FROM video_chains WHERE id=?", (chain_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.post("/api/video-chains/{chain_id}/compile")
def compile_video_chain(chain_id: int, background_tasks: BackgroundTasks):
    """Compile all done segments into a single video with xfade transitions."""
    conn = get_conn()
    chain = conn.execute("SELECT * FROM video_chains WHERE id=?", (chain_id,)).fetchone()
    if not chain:
        conn.close()
        raise HTTPException(404, "Chain not found")
    if chain["status"] not in ("done", "failed"):
        conn.close()
        raise HTTPException(400, "Chain is still generating")
    segs = conn.execute(
        "SELECT video_path FROM videos WHERE chain_id=? AND status='done' ORDER BY chain_index",
        (chain_id,)
    ).fetchall()
    paths = [s["video_path"] for s in segs if s["video_path"]]
    chain_fps = chain["fps"] or 16
    # Normalize to the chain's own size (falls back to the old defaults) so a
    # mismatched segment can't dictate the compiled video's dimensions.
    chain_wh = (chain["width"] or 832, chain["height"] or 480)
    conn.close()
    if not paths:
        raise HTTPException(400, "No completed segments to compile")

    def _do_compile():
        out = str(_chain_compiled_path(chain_id))
        try:
            _compile_chain_video(paths, out, fps=chain_fps, target=chain_wh)
            c = get_conn()
            c.execute(
                "UPDATE video_chains SET compiled_path=?,updated_at=datetime('now') WHERE id=?",
                (out, chain_id)
            )
            c.commit()
            c.close()
            logger.info("Chain %d compiled: %s", chain_id, out)
        except Exception as ex:
            logger.error("Chain %d compile failed: %s", chain_id, ex)
            return
        # Audio-enabled chains get their layered soundtrack after a manual
        # compile too (no-op unless the chain opted in — default OFF).
        try:
            render_chain_audio(chain_id)
        except Exception as ex:
            logger.error("Chain %d audio pass crashed: %s", chain_id, ex)

    background_tasks.add_task(_do_compile)
    return {"message": "Compiling chain video…"}

@router.post("/api/videos/chain-prompts")
def generate_chain_prompts(req: ChainPromptsRequest):
    """Use LLM to generate sequential scene prompts for video chaining."""
    if req.num_segments < 1:
        raise HTTPException(400, "num_segments must be at least 1")

    user_msg = f"Concept: {req.concept}\nNumber of segments: {req.num_segments}"
    if req.style:
        user_msg += f"\nStyle/mood: {req.style}"

    def _work():
        raw = _call_lmstudio(get_prompt('video_chain') + _CHAIN_AUDIO_ADDENDUM,
                             user_msg, max_tokens=2000)
        import re as _re

        def _strlist(v, limit):
            return [str(x).strip() for x in v][:limit] if isinstance(v, list) else []

        # Preferred shape (audio addendum): one JSON object
        # {prompts, narrations, music, sfx}. Audio keys are optional — anything
        # missing/empty is simply omitted from the result, so callers that only
        # know "prompts" behave exactly as before. <think> blocks stripped
        # first (reasoning models wrap their JSON in them).
        cleaned = _re.sub(r'<think>.*?</think>', '', raw, flags=_re.DOTALL).strip()
        mo = _re.search(r'\{.*\}', cleaned, _re.DOTALL)
        if mo:
            try:
                data = json.loads(mo.group())
            except Exception:
                data = None
            if isinstance(data, dict):
                prompts = [p for p in _strlist(data.get("prompts"), req.num_segments) if p]
                if prompts:
                    out = {"prompts": prompts}
                    narrs = [x for x in _strlist(data.get("narrations"), len(prompts)) if x]
                    if narrs:
                        out["narrations"] = narrs
                    music = str(data.get("music") or "").strip()
                    if music:
                        out["music"] = music
                    sfx = [x for x in _strlist(data.get("sfx"), len(prompts)) if x]
                    if sfx:
                        out["sfx"] = sfx
                    return out
        # Legacy shape: bare JSON array of prompt strings (pre-audio models /
        # models that ignored the addendum) — unchanged behavior.
        m = _re.search(r'\[.*\]', raw, _re.DOTALL)
        if m:
            try:
                prompts = json.loads(m.group())
                if isinstance(prompts, list) and len(prompts) >= 1:
                    return {"prompts": [str(p) for p in prompts[:req.num_segments]]}
            except Exception:
                pass
        # Fallback: split by newlines, strip bullets/quotes
        lines = [l.strip().strip('"').strip("'").lstrip("-0123456789. ").strip()
                 for l in raw.splitlines() if l.strip()]
        lines = [l for l in lines if len(l) > 20]
        return {"prompts": lines[:req.num_segments], "raw": raw[:200]}

    tid = orch.submit_llm(_work, desc=f"Chain prompts: {req.concept[:50]}", task="video_chain")
    return {"task_id": tid}

@router.get("/api/videos")
def list_videos():
    conn = get_conn()
    # nsfw-flagged videos never appear here — /api/nsfw/library only.
    # chain_id IS NULL: chain/studio/TV SEGMENTS live inside their chain's own
    # card (segments list) — without this the singles gallery floods with every
    # segment of every long render.
    rows = conn.execute(
        "SELECT * FROM videos WHERE COALESCE(nsfw,0)=0 AND chain_id IS NULL "
        "ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.get("/api/videos/{vid_id}")
def get_video(vid_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM videos WHERE id=?", (vid_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")
    if row["nsfw"]:
        import nsfw as _nsfw
        if not _nsfw.visible():
            raise HTTPException(status_code=404, detail="Video not found")
    return dict(row)

def _run_video_then_audio(vid_id: int, music_prompt: str, narration: str,
                          settings: dict | None = None):
    """'Generate with audio' (opt-in): the normal generation, then the EXISTING
    add_video_audio bridge on success — one background task; every model call
    inside rides the unified GPU queue exactly as it does today."""
    run_video_generation(vid_id)
    conn = get_conn()
    row = conn.execute("SELECT status FROM videos WHERE id=?", (vid_id,)).fetchone()
    conn.close()
    if row and row["status"] == "done":
        conn = get_conn()
        conn.execute("UPDATE videos SET audio_status='queued',audio_error=NULL WHERE id=?", (vid_id,))
        conn.commit()
        conn.close()
        add_video_audio(vid_id, music_prompt, narration, settings)


@router.post("/api/videos/generate")
def create_video(req: VideoRequest, background_tasks: BackgroundTasks):
    check_video_vram_or_raise(req.model_id)   # fail fast if this model can't fit on the node's GPU
    p = _resolve_video_params(req)
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO videos (prompt,width,height,num_frames,steps,fps,seed,status,model_id,audio_settings) "
        "VALUES (?,?,?,?,?,?,?,'queued',?,?)",
        (req.prompt, p["width"], p["height"], p["num_frames"], p["steps"], p["fps"], req.seed, req.model_id,
         json.dumps(req.audio_settings) if req.audio_settings else None),
    )
    vid_id = c.lastrowid
    conn.commit()
    conn.close()
    if req.audio_enabled:
        background_tasks.add_task(_run_video_then_audio, vid_id,
                                  req.music_prompt.strip(), req.narration.strip(),
                                  req.audio_settings)
    else:
        background_tasks.add_task(run_video_generation, vid_id)
    return {"id": vid_id, "status": "queued"}

@router.delete("/api/videos/{vid_id}")
def delete_video(vid_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM videos WHERE id=?", (vid_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Video not found")
    conn.close()
    # If it's mid-generation, kill the subprocess first so we don't leave an orphan.
    _svc.cancel_video(vid_id)
    conn = get_conn()
    if row["video_path"]:
        try:
            Path(row["video_path"]).unlink(missing_ok=True)
        except Exception:
            pass
    conn.execute("DELETE FROM videos WHERE id=?", (vid_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/api/video-health")
def videos_health():
    """Is the GPU node reachable and the generator wired up? Surfaced in the UI so
    the user knows before queueing whether a job can even run."""
    ok, msg = _svc._video_preflight()
    return {"ok": ok, "message": msg, "gpu_host": globals().get("GPU_HOST", "")}


@router.post("/api/videos/{vid_id}/cancel")
def cancel_video_route(vid_id: int):
    """Stop a queued/generating video: kill its subprocess and mark it failed."""
    conn = get_conn()
    row = conn.execute("SELECT status FROM videos WHERE id=?", (vid_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Video not found")
    if row["status"] not in ("queued", "generating"):
        conn.close()
        raise HTTPException(400, f"Video is '{row['status']}', not running")
    killed = _svc.cancel_video(vid_id)
    conn.execute("UPDATE videos SET status='failed',error='Cancelled by user',updated_at=datetime('now') WHERE id=?", (vid_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "killed_process": killed}


class AddAudioRequest(BaseModel):
    music_prompt: str = ""
    narration: str = ""
    # Optional full layered settings (chain parity). None = the video's stored
    # audio_settings (saved by "Generate with audio"), else legacy music+voice.
    settings: dict | None = None


@router.post("/api/videos/{vid_id}/add-audio")
def add_audio(vid_id: int, req: AddAudioRequest, background_tasks: BackgroundTasks):
    """Bridge: generate a layered soundtrack (music bed + optional narration/SFX)
    for a video and mux it on. The result is served from videos.audio_path when done."""
    conn = get_conn()
    row = conn.execute("SELECT status, video_path FROM videos WHERE id=?", (vid_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Video not found")
    if row["status"] != "done" or not row["video_path"]:
        conn.close()
        raise HTTPException(400, "Video isn't finished yet")
    if req.settings is not None:
        conn.execute("UPDATE videos SET audio_status='queued',audio_error=NULL,audio_settings=? WHERE id=?",
                     (json.dumps(req.settings), vid_id))
    else:
        conn.execute("UPDATE videos SET audio_status='queued',audio_error=NULL WHERE id=?", (vid_id,))
    conn.commit()
    conn.close()
    background_tasks.add_task(add_video_audio, vid_id, req.music_prompt, req.narration, req.settings)
    return {"ok": True, "status": "queued"}


@router.post("/api/videos/{vid_id}/retry")
def retry_video(vid_id: int, background_tasks: BackgroundTasks):
    """Re-queue a failed video with the same settings (no need to retype the prompt)."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM videos WHERE id=?", (vid_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Video not found")
    if row["status"] in ("queued", "generating"):
        conn.close()
        raise HTTPException(400, "Video is already running")
    try:
        check_video_vram_or_raise(row["model_id"])   # fail fast if this model can't fit on the node's GPU
    except HTTPException:
        conn.close()
        raise
    conn.execute("UPDATE videos SET status='queued',error=NULL,video_path=NULL,updated_at=datetime('now') WHERE id=?", (vid_id,))
    conn.commit()
    conn.close()
    background_tasks.add_task(run_video_generation, vid_id)
    return {"ok": True, "status": "queued"}


class ChainAudioRequest(BaseModel):
    settings: dict | None = None   # None = keep the chain's stored settings/defaults


@router.post("/api/video-chains/{chain_id}/audio")
def chain_audio(chain_id: int, req: ChainAudioRequest, background_tasks: BackgroundTasks):
    """Generate (or redo) the layered audio for a compiled chain: music bed +
    TTS narration (+ optional SFX), mixed and muxed onto the compiled video →
    video_chains.final_path. Runs the same engines as the studio audio path;
    every model call rides the unified GPU queue."""
    conn = get_conn()
    chain = conn.execute(
        "SELECT compiled_path,audio_status FROM video_chains WHERE id=?",
        (chain_id,)).fetchone()
    if not chain:
        conn.close()
        raise HTTPException(404, "Chain not found")
    if not chain["compiled_path"] or not Path(chain["compiled_path"]).exists():
        conn.close()
        raise HTTPException(400, "Chain has no compiled video yet — compile it first")
    if chain["audio_status"] in ("queued", "generating"):
        conn.close()
        raise HTTPException(400, "Chain audio is already generating")
    if req.settings is not None:
        conn.execute(
            "UPDATE video_chains SET audio_enabled=1,audio_status='queued',audio_error=NULL,"
            "audio_settings=?,updated_at=datetime('now') WHERE id=?",
            (json.dumps(req.settings), chain_id))
    else:
        conn.execute(
            "UPDATE video_chains SET audio_enabled=1,audio_status='queued',audio_error=NULL,"
            "updated_at=datetime('now') WHERE id=?", (chain_id,))
    conn.commit()
    conn.close()
    background_tasks.add_task(render_chain_audio, chain_id)
    return {"ok": True, "message": "Generating chain audio…"}


@router.post("/api/video-chains/{chain_id}/cancel")
def cancel_chain(chain_id: int):
    """Stop a generating chain: kill the active segment's subprocess, mark failed."""
    conn = get_conn()
    chain = conn.execute("SELECT status FROM video_chains WHERE id=?", (chain_id,)).fetchone()
    if not chain:
        conn.close()
        raise HTTPException(404, "Chain not found")
    active = conn.execute("SELECT id FROM videos WHERE chain_id=? AND status IN ('queued','generating')", (chain_id,)).fetchall()
    killed = False
    for a in active:
        if _svc.cancel_video(a["id"]):
            killed = True
        conn.execute("UPDATE videos SET status='failed',error='Cancelled by user' WHERE id=?", (a["id"],))
    conn.execute("UPDATE video_chains SET status='failed',error='Cancelled by user',updated_at=datetime('now') WHERE id=?", (chain_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "killed_process": killed}

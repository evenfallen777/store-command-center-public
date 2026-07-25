"""Private Studio (NSFW) routes — every route is gated by the layered toggles in
app/nsfw.py (master → 404-invisible; display → content redaction; world → agents).

These endpoints REUSE the normal Studio pipelines (run_generation /
run_video_generation / run_audio_clip / the 3D generator): an NSFW job is a
normal row flagged nsfw=1 (images also source='nsfw' so the designs row inherits
it), which is exactly what keeps it OUT of every regular listing surface and IN
/api/nsfw/library only. The safety floor (app/nsfw.py) screens every prompt
before anything is written — it is not toggleable.
"""
import re as _re
from fastapi import APIRouter, HTTPException, BackgroundTasks
from deps import *
import services as _svc
import nsfw as core

router = APIRouter()

# Reasoning models (e.g. qwen3.5-*-uncensored-aggressive) ignore reasoning_effort:none
# and emit a plain-text chain-of-thought as their answer instead of the prompt. That CoT
# (a) isn't the prompt and (b) quotes the safety rules ("no minors…"), which then
# false-trips the OUTPUT safety filter → empty result → UI shows the input unchanged.
# Strip any reasoning and keep only the final prompt line, whatever model is selected.
_COT_MARK    = _re.compile(r"(?im)^\s*(thinking process|analy[sz]e the request|reasoning|let me think|step\s+\d+|\d+\.\s+\*\*)")
_ANSWER_MARK = _re.compile(r"(?is)\b(final\s+(?:prompt|answer)|enhanced\s+prompt|here'?s?\s+(?:the|your)[^:\n]{0,40}|prompt|output)\s*[:\-]\s*")

def _clean_line(t: str) -> str:
    t = _re.sub(r"(?i)^\s*(\*?draft\s*\d*\*?\s*[:\-]?|prompt\s*[:\-]|output\s*[:\-])\s*", "", t)
    return t.strip().strip('"*`').strip()

def _extract_prompt(raw: str) -> str:
    if not raw:
        return ""
    t = _re.sub(r"(?is)<(think|thought|reasoning)>.*?</\1>", "", raw).strip()
    # Reasoning models that write several labelled "Draft N:" attempts → take the LAST
    # draft's prose (their refined answer), up to the next blank line / word-count tally.
    drafts = list(_re.finditer(
        r"(?is)\*?\s*draft\s*\d*\*?\s*[:\-]?\s*(.+?)(?:\n\s*\n|\*?\s*word\s*count|\*?\s*refin|$)", t))
    for d in reversed(drafts):
        cand = _clean_line(d.group(1))
        if len(cand) >= 40:
            return cand
    ms = list(_ANSWER_MARK.finditer(t))
    if ms:                                   # explicit "Final prompt:"-style marker wins
        return _clean_line(t[ms[-1].end():])
    if _COT_MARK.search(t):                  # reasoning present, no marker → last real paragraph
        paras = [p.strip() for p in _re.split(r"\n\s*\n", t) if len(p.strip()) >= 40
                 and not _re.match(r"(?i)^\s*[\d*]", p.strip())]
        if paras:
            return _clean_line(paras[-1])
    return _clean_line(t)


@router.get("/api/nsfw/status")
def nsfw_status():
    """Toggle state only (no content) — the frontend uses this to show/hide the tab."""
    return core.status()


# ── image ─────────────────────────────────────────────────────────────────────
class NsfwGenerateRequest(BaseModel):
    prompt: str
    product_type: str = "Art"
    width: int = 1024
    height: int = 1024
    steps: int = 20
    variations: int = 1
    model: Optional[str] = None


@router.post("/api/nsfw/generate")
def nsfw_generate(req: NsfwGenerateRequest, background_tasks: BackgroundTasks):
    core.require_enabled()
    if not req.prompt.strip():
        raise HTTPException(400, "prompt required")
    core.refuse_unsafe(req.prompt)
    conn = get_conn()
    model = req.model or (get_setting('nsfw_image_model', '') or None) or _resolve_model(conn, None)
    gen_ids = []
    for _ in range(max(1, min(4, req.variations))):
        cur = conn.execute(
            "INSERT INTO generations (prompt,product_type,width,height,steps,model,source,nsfw) "
            "VALUES (?,?,?,?,?,?, 'nsfw', 1)",
            (req.prompt, req.product_type, req.width, req.height, req.steps, model))
        gen_ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    for gid in gen_ids:
        background_tasks.add_task(_svc.run_generation, gid)
    return {"ok": True, "generation_ids": gen_ids}


# ── video ─────────────────────────────────────────────────────────────────────
class NsfwVideoRequest(BaseModel):
    prompt: str
    width: int = 832
    height: int = 480
    num_frames: int = 49
    steps: int = 20
    fps: int = 16
    seed: int = 0
    model_id: str = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"


@router.post("/api/nsfw/video")
def nsfw_video(req: NsfwVideoRequest, background_tasks: BackgroundTasks):
    core.require_enabled()
    if not req.prompt.strip():
        raise HTTPException(400, "prompt required")
    core.refuse_unsafe(req.prompt)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO videos (prompt,width,height,num_frames,steps,fps,seed,status,model_id,nsfw) "
        "VALUES (?,?,?,?,?,?,?,'queued',?,1)",
        (req.prompt, req.width, req.height, req.num_frames, req.steps, req.fps,
         req.seed, req.model_id))
    vid_id = cur.lastrowid
    conn.commit()
    conn.close()
    background_tasks.add_task(_svc.run_video_generation, vid_id)
    return {"id": vid_id, "status": "queued"}


# ── audio ─────────────────────────────────────────────────────────────────────
class NsfwAudioRequest(BaseModel):
    prompt: str
    engine: str = "musicgen"
    duration: int = 8
    model_id: str = ""
    lyrics: str = ""


@router.post("/api/nsfw/audio")
def nsfw_audio(req: NsfwAudioRequest, background_tasks: BackgroundTasks):
    core.require_enabled()
    if not req.prompt.strip():
        raise HTTPException(400, "prompt required")
    core.refuse_unsafe(req.prompt, req.lyrics)
    if req.engine not in _svc.AUDIO_ENGINES:
        raise HTTPException(400, f"unknown engine {req.engine}")
    kind = _svc.AUDIO_ENGINES[req.engine]["kind"]
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO audio_clips (kind,prompt,engine,model_id,duration,lyrics,status,nsfw) "
        "VALUES (?,?,?,?,?,?, 'queued', 1)",
        (kind, req.prompt.strip(), req.engine, req.model_id or None,
         max(3, min(240, req.duration)), (req.lyrics or "").strip() or None))
    cid = cur.lastrowid
    conn.commit()
    conn.close()
    background_tasks.add_task(_svc.run_audio_clip, cid)
    return {"id": cid, "status": "queued"}


# ── 3D ────────────────────────────────────────────────────────────────────────
class Nsfw3dRequest(BaseModel):
    prompt: str
    title: Optional[str] = None
    generator: str = "triposr"
    image_model: Optional[str] = None


@router.post("/api/nsfw/3d")
def nsfw_3d(req: Nsfw3dRequest, background_tasks: BackgroundTasks):
    core.require_enabled()
    if not req.prompt.strip():
        raise HTTPException(400, "prompt required")
    core.refuse_unsafe(req.prompt)
    # Delegate to the normal 3D pipeline with the nsfw flag (it re-checks the gate).
    from routers import models3d as _m3d
    m3d_req = _m3d.Generate3dRequest(prompt=req.prompt, title=req.title,
                                     generator=req.generator,
                                     image_model=req.image_model, nsfw=True)
    return _m3d.generate_model3d_ep(m3d_req, background_tasks)


# ── prompt enhance (LLM via the prompt registry) ─────────────────────────────
class NsfwEnhanceRequest(BaseModel):
    prompt: str


@router.post("/api/nsfw/enhance")
def nsfw_enhance(req: NsfwEnhanceRequest):
    """Rough idea → full generation prompt (mirrors the music-enhance flow: server
    task, pollable, result lands back in the editable prompt box). Runs on the
    `nsfw_model` registry slot; the safety floor screens the INPUT here and the
    model's OUTPUT inside the task. Returns {task_id}; poll /api/task/{id}.
    The queue label is ALWAYS generic (never the prompt)."""
    core.require_enabled()
    idea = req.prompt.strip()
    if not idea:
        raise HTTPException(400, "prompt required")
    core.refuse_unsafe(idea)
    system = get_prompt("nsfw_enhance")

    def _work():
        raw = _call_lmstudio(system, idea, max_tokens=1200)
        txt = _extract_prompt(raw)   # discard chain-of-thought; keep only the prompt line
        if not txt or txt.upper().startswith("REFUSED"):
            return {"enhanced": "", "refused": txt.upper().startswith("REFUSED"), "original": idea}
        # safety floor screens the MODEL's OUTPUT (the extracted prompt only — never the
        # CoT, which quotes the rules and would false-trip the minors filter).
        reason = core.safety_check(txt)
        if reason:
            return {"enhanced": "", "refused": True, "reason": reason, "original": idea}
        return {"enhanced": txt, "original": idea}

    # desc deliberately contains NO prompt content — the queue label stays discreet.
    tid = orch.submit_llm(_work, desc="Private studio: enhance", priority=0,
                          model=core.pick_model() or None, task="nsfw_enhance")
    return {"task_id": tid}


# ── categories (model-authored at bootstrap, user-editable, CRUD persisted) ──
class CategoryCreate(BaseModel):
    name: str


class CategoryPatch(BaseModel):
    name: Optional[str] = None
    gen_prompt: Optional[str] = None


@router.get("/api/nsfw/categories")
def nsfw_categories():
    """Category rows incl. their generator prompts (content — needs display on)."""
    core.require_visible()
    conn = get_conn()
    try:
        core.seed_categories(conn)
        cats = core.list_categories(conn)
        rejects = {r["category"]: r["n"] for r in conn.execute(
            "SELECT category, COUNT(*) n FROM nsfw_rejects GROUP BY category").fetchall()}
    finally:
        conn.close()
    for c in cats:
        c["rejects"] = rejects.get(c["name"], 0)
    return {"categories": cats, "model": core.pick_model()}


@router.post("/api/nsfw/categories")
def nsfw_category_create(req: CategoryCreate):
    core.require_enabled()
    name = req.name.strip()[:60]
    if not name:
        raise HTTPException(400, "name required")
    core.refuse_unsafe(name)
    conn = get_conn()
    try:
        core.ensure(conn)
        try:
            cur = conn.execute("INSERT INTO nsfw_categories (name) VALUES (?)", (name,))
            conn.commit()
        except Exception:
            raise HTTPException(400, "category already exists")
        return {"ok": True, "id": cur.lastrowid, "name": name}
    finally:
        conn.close()


@router.patch("/api/nsfw/categories/{cat_id}")
def nsfw_category_patch(cat_id: int, req: CategoryPatch):
    """Rename a category / edit its generator prompt (user-editable by design)."""
    core.require_enabled()
    conn = get_conn()
    try:
        core.ensure(conn)
        row = conn.execute("SELECT id FROM nsfw_categories WHERE id=?", (cat_id,)).fetchone()
        if not row:
            raise HTTPException(404, "category not found")
        if req.name is not None:
            name = req.name.strip()[:60]
            if not name:
                raise HTTPException(400, "name cannot be empty")
            core.refuse_unsafe(name)
            conn.execute("UPDATE nsfw_categories SET name=?,updated_at=datetime('now') WHERE id=?",
                         (name, cat_id))
        if req.gen_prompt is not None:
            core.refuse_unsafe(req.gen_prompt)   # the floor screens edits too
            conn.execute("UPDATE nsfw_categories SET gen_prompt=?,updated_at=datetime('now') WHERE id=?",
                         (req.gen_prompt.strip(), cat_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.delete("/api/nsfw/categories/{cat_id}")
def nsfw_category_delete(cat_id: int):
    core.require_enabled()
    conn = get_conn()
    try:
        core.ensure(conn)
        conn.execute("DELETE FROM nsfw_categories WHERE id=?", (cat_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.post("/api/nsfw/categories/{cat_id}/generate")
def nsfw_category_generate(cat_id: int):
    """Queue ONE creation for this category: the nsfw model authors a concrete
    prompt from the category brief (avoiding recent rejects), then the normal
    image pipeline runs it flagged nsfw=1."""
    core.require_enabled()
    conn = get_conn()
    try:
        core.ensure(conn)
        row = conn.execute("SELECT * FROM nsfw_categories WHERE id=?", (cat_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "category not found")
    tid = core.submit_category_job(dict(row))
    return {"ok": True, "task_id": tid}


@router.post("/api/nsfw/generate-all")
def nsfw_generate_all():
    """'Generate every category' batch: one queued category job per category.
    Everything respects the toggles + the safety floor like single jobs."""
    core.require_enabled()
    conn = get_conn()
    try:
        core.seed_categories(conn)
        cats = core.list_categories(conn)
    finally:
        conn.close()
    tids = [core.submit_category_job(c) for c in cats]
    return {"ok": True, "queued": len(tids), "task_ids": tids}


# ── bootstrap: the nsfw model authors/refreshes the category generator prompts ─
@router.post("/api/nsfw/bootstrap")
def nsfw_bootstrap():
    """One-time (re-runnable) bootstrap: seeds the default categories, then a
    single queued task has the nsfw model WRITE each category's generator prompt.
    The authored prompts are saved as editable rows — nothing is hard-coded —
    and each one passes the safety floor before being stored."""
    core.require_enabled()
    conn = get_conn()
    try:
        core.seed_categories(conn)
    finally:
        conn.close()
    tid = core.submit_bootstrap()
    return {"ok": True, "task_id": tid, "model": core.pick_model()}


# ── reject feedback loop ─────────────────────────────────────────────────────
@router.post("/api/nsfw/item/{design_id}/reject")
def nsfw_reject(design_id: int):
    """'Badly generated' — deletes the image AND feeds the negative signal back:
    logged per prompt/category (future jobs steer away), a deny example into the
    god-taste model, and a generic journal line for the world agent if one made it."""
    core.require_visible()
    return core.reject_design(design_id)


# ── library (the ONLY listing surface for nsfw content) ──────────────────────
@router.get("/api/nsfw/library")
def nsfw_library():
    """Everything nsfw-flagged across all modalities. Requires master AND display —
    with display off the archive stays redacted even though jobs keep running."""
    core.require_visible()
    conn = get_conn()
    try:
        images = [dict(r) for r in conn.execute(
            "SELECT d.id, d.generation_id, d.image_path, d.prompt, d.status, "
            "       d.nsfw_category, d.created_at "
            "FROM designs d WHERE COALESCE(d.nsfw,0)=1 OR d.source='nsfw' "
            "ORDER BY d.created_at DESC LIMIT 200").fetchall()]
        pending = [dict(r) for r in conn.execute(
            "SELECT id, prompt, status, nsfw_category, created_at FROM generations "
            "WHERE COALESCE(nsfw,0)=1 AND status IN ('queued','generating','failed') "
            "ORDER BY created_at DESC LIMIT 50").fetchall()]
        videos = [dict(r) for r in conn.execute(
            "SELECT * FROM videos WHERE COALESCE(nsfw,0)=1 ORDER BY created_at DESC LIMIT 100"
        ).fetchall()]
        audio = [dict(r) for r in conn.execute(
            "SELECT * FROM audio_clips WHERE COALESCE(nsfw,0)=1 ORDER BY created_at DESC LIMIT 100"
        ).fetchall()]
        m3d = [dict(r) for r in conn.execute(
            "SELECT id, title, status, gen_prompt, progress_msg, render_paths, hero_paths, "
            "primary_image, created_at FROM models3d WHERE COALESCE(nsfw,0)=1 "
            "ORDER BY created_at DESC LIMIT 100").fetchall()]
    finally:
        conn.close()
    return {"images": images, "generating": pending, "videos": videos,
            "audio": audio, "models3d": m3d}

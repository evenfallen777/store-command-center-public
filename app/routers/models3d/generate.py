"""models3d — local 3D generation (text/image → mesh via TripoSR & friends on the
box) and the 3D-tuned prompt enhancer.
"""
from fastapi import HTTPException, BackgroundTasks

from deps import *
from services import generate_model3d_mesh, check_3d_vram_or_raise

from ._base import router


# ── local 3D generation (text/image → mesh via TripoSR on the box) ───────────

class Generate3dRequest(BaseModel):
    prompt: Optional[str] = None       # if set: SDXL image first, then image→3D
    image_path: Optional[str] = None   # or point at an existing image on disk
    title: Optional[str] = None
    image_model: Optional[str] = None
    generator: str = "triposr"         # which image→3D model (key in RECOMMENDED_3D_MODELS)
    nsfw: bool = False                 # Private-Studio job (gated + safety-screened below)


def _resolve_generator(key: str) -> dict:
    """Return the catalog entry for a generator key; raise if unknown."""
    cat = {m["key"]: m for m in RECOMMENDED_3D_MODELS}
    if key not in cat:
        raise HTTPException(400, f"Unknown 3D generator '{key}'")
    return cat[key]


def _m3d_progress(model_id: int, msg: str):
    """Surface a human progress line on the model row so the UI isn't a black box."""
    try:
        c = get_conn()
        c.execute("UPDATE models3d SET progress_msg=?,updated_at=datetime('now') WHERE id=?", (msg, model_id))
        c.commit(); c.close()
    except Exception:
        pass


@router.post("/api/models3d/generate")
def generate_model3d_ep(req: Generate3dRequest, background_tasks: BackgroundTasks):
    """Create a generated 3D model. From a prompt (SDXL→mesh) or an existing image."""
    if not req.prompt and not req.image_path:
        raise HTTPException(400, "Provide a prompt or an image_path")
    if req.nsfw:
        # Private-Studio job: only exists when the master toggle is on, and the
        # (non-configurable) safety floor screens the prompt first.
        import nsfw as _nsfw
        _nsfw.require_enabled()
        _nsfw.refuse_unsafe(req.prompt or "", req.title or "")
    gen = _resolve_generator(req.generator)
    check_3d_vram_or_raise(gen)   # fail fast if this generator can't fit on the node's GPU
    gen_script = gen["script"]
    gen_label = gen["label"].split(" (")[0]
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO models3d (file_path,file_name,file_ext,title,status,source,gen_prompt,"
        "rel_dir,category,progress_msg,nsfw,generator) VALUES ('','','',?,'generating','generated',?,"
        "'generated','Generated','⏳ Queued…',?,?)",
        (req.title or (req.prompt or "Generated model")[:80],
         req.prompt or f"from {req.image_path}", 1 if req.nsfw else 0, req.generator))
    model_id = cur.lastrowid
    conn.commit(); conn.close()

    def _pipeline():
        img = req.image_path
        if req.prompt and not img:
            # 1. render a clean product image with SDXL, 2. TripoSR it into a mesh
            MODELS3D_HERO.mkdir(parents=True, exist_ok=True)
            out = MODELS3D_HERO / f"gensrc_{model_id}_{int(datetime.now().timestamp())}.png"
            _m3d_progress(model_id, "🎨 Rendering a source image with SDXL…")
            orch.image_acquire()
            try:
                seed = str(random.randint(1, 2**31 - 1))
                mdl = req.image_model or DEFAULT_IMAGE_MODEL
                sd_prompt = (f"{req.prompt}, single centered object, plain white background, "
                             "studio product shot, full object visible, no shadows")
                r = subprocess.run([str(GENERATE_SCRIPT), sd_prompt, str(out), "1024", "1024", "20", seed, mdl],
                                   capture_output=True, text=True, timeout=300)
            finally:
                orch.image_release()
            if not out.exists():
                err = (r.stderr or r.stdout or "")[-200:] if 'r' in dir() else ""
                c = get_conn()
                c.execute("UPDATE models3d SET status='error',progress_msg='❌ Source image failed',"
                          "publish_error=? WHERE id=?", (f"source image gen failed: {err}", model_id))
                c.commit(); c.close(); return
            img = str(out)
            c = get_conn()
            c.execute("UPDATE models3d SET hero_paths=?,primary_image=? WHERE id=?",
                      (json.dumps([img]), img, model_id)); c.commit(); c.close()
        _m3d_progress(model_id, f"🧊 Building 3D mesh on the GPU box ({gen_label})…")
        generate_model3d_mesh(model_id, img, gen_script)

    background_tasks.add_task(_pipeline)
    return {"ok": True, "model_id": model_id, "generator": req.generator}


class EnhanceReq(BaseModel):
    prompt: str


@router.post("/api/models3d/enhance")
def enhance_3d(req: EnhanceReq):
    """3D-tuned prompt enhancement. Returns {task_id}; poll /api/task/{id} → {enhanced}."""
    idea = req.prompt.strip()
    if not idea:
        raise HTTPException(400, "prompt required")
    SYS = ("You improve short ideas into prompts for image-to-3D generation of a SINGLE "
           "printable object. Return ONE vivid line (30-60 words): the object, its form, "
           "style, and surface — centered, full object visible, clean silhouette, no scene, "
           "no background, no text. Output ONLY the prompt line, nothing else.")
    def _work():
        out = _call_lmstudio(get_prompt('threed_enhance'), idea, max_tokens=400)
        import re as _re
        out = _re.sub(r'<think>.*?</think>', '', out, flags=_re.DOTALL).strip().strip('"')
        return {"enhanced": out, "original": idea}
    tid = orch.submit_llm(_work, desc=f"Enhance 3D: {idea[:40]}", task="threed_enhance")
    return {"task_id": tid}

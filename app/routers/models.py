"""models routes."""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Form, UploadFile, File, Body
from deps import *
from services import *
import model_registry
import model_paths as _mp

router = APIRouter()


def _dest_dir(m):
    """Download destination for a catalog entry: its own dest_dir (loras/upscalers/
    controlnet keep their ComfyUI subfolders) — but entries on the CHECKPOINT default
    follow the live 📁 Storage setting so users can relocate their models drive."""
    d = m.get("dest_dir") or COMFY_CKPT
    return _mp.primary("image") if d == COMFY_CKPT else d


@router.get("/api/models/storage")
def model_storage():
    """📁 Storage locations per model kind (setting + effective + fallback)."""
    return {"kinds": _mp.snapshot()}


@router.get("/api/models/registry")
def model_registry_list():
    """Every model-using feature, its description, and the model it's set to —
    powers Settings → 🧠 Models. Option lists are fetched separately by the UI
    (/api/settings/llm-models for llm/vision, /api/models for image)."""
    try:
        ttl = int(get_setting("model_idle_ttl", "1800") or 1800)
    except Exception:
        ttl = 1800
    return {"slots": model_registry.slots(), "idle_ttl": ttl}


@router.post("/api/models/idle-ttl")
def model_idle_ttl_set(body: dict = Body(...)):
    """Seconds a loaded LLM may sit idle before the node auto-unloads it (0 = never).
    Applied on the next model load via `lms load --ttl`."""
    try:
        ttl = max(0, int(body.get("seconds", 1800)))
    except Exception:
        raise HTTPException(400, "seconds must be an integer")
    conn = get_conn()
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('model_idle_ttl', ?)", (str(ttl),))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "seconds": ttl}


@router.post("/api/models/registry")
def model_registry_set(body: dict = Body(...)):
    """Set one slot's model. {key, value} — value '' clears to the slot's fallback."""
    key = (body.get("key") or "").strip()
    value = (body.get("value") or "").strip()
    try:
        model_registry.set_model(key, value)
    except KeyError:
        raise HTTPException(400, f"Unknown model slot: {key!r}")
    return {"ok": True, "key": key, "value": value, "effective": model_registry.resolve(key)}


@router.post("/api/models/{filename}/download")
def start_model_download(filename: str):
    """Trigger auto-download of a model to the box via SSH/wget."""
    # checkpoints + LoRAs + specialty models share one download flow; each entry's
    # dest_dir decides where it lands (checkpoints/ loras/ upscale_models/ controlnet/ …)
    catalogue = {m["filename"]: m for m in all_downloadable_models()}
    if filename not in catalogue:
        raise HTTPException(404, "Unknown model")
    m = catalogue[filename]
    if not m.get("auto_download") or not m.get("download_url"):
        raise HTTPException(400, "Auto-download not available for this model — use the manual wget command")
    if _dl_jobs.get(filename, {}).get("status") == "downloading":
        return {"ok": True, "status": "already_downloading"}

    url  = m["download_url"]
    dest = f"{_dest_dir(m)}/{filename}"
    # Use /tmp on the box so ComfyUI never sees the partial file
    tmp  = f"/tmp/.dl_{filename}"
    # mkdir the dest dir first — specialty dirs (rmbg/, sometimes controlnet/) may not
    # exist yet on a fresh box, which would make the final `mv` fail.
    dest_dir = _dest_dir(m)
    # VALIDATE before install: Civitai (and others) gate some downloads behind a
    # login/token and return an HTML error page with HTTP 200 — blindly moving
    # that into models/ leaves a corrupt ".safetensors" that fails at load time
    # with no clue why. Reject tiny files and files that start like HTML, and
    # surface the need for CIVITAI_TOKEN loudly.
    cmd  = (f"mkdir -p {dest_dir} && wget -q -O {tmp} '{url}' 2>&1 && "
            f"S=$(stat -c%s {tmp} 2>/dev/null || echo 0) && "
            f"H=$(head -c 64 {tmp} | tr -d '\\0') && "
            f"if [ \"$S\" -lt 1048576 ] || echo \"$H\" | grep -qiE '<!DOCTYPE|<html|\"error\"'; then "
            f"  echo 'DOWNLOAD INVALID: got a non-model response (size='$S' bytes) — "
            f"the source likely requires a login/API token (Civitai: set CIVITAI_TOKEN and use ?token=)'; "
            f"  rm -f {tmp}; exit 1; "
            f"else mv {tmp} {dest}; fi")

    proc = subprocess.Popen(
        BOX_SSH + [cmd],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    _dl_jobs[filename] = {"status": "downloading", "error": None, "proc": proc}

    def _monitor(fn: str, p):
        out, _ = p.communicate()
        job = _dl_jobs.get(fn, {})
        if job.get("status") == "cancelled":
            return  # cancel already handled
        if p.returncode == 0:
            _dl_jobs[fn] = {"status": "done", "error": None, "proc": None}
        else:
            err = (out or "").strip()[-400:] or "wget failed"
            _dl_jobs[fn] = {"status": "error", "error": err, "proc": None}

    threading.Thread(target=_monitor, args=(filename, proc), daemon=True).start()
    return {"ok": True, "status": "downloading"}

@router.delete("/api/models/{filename}/download")
def cancel_model_download(filename: str):
    """Cancel an in-progress download — kills wget on the box and cleans up temp file."""
    job = _dl_jobs.get(filename)
    if not job or job.get("status") != "downloading":
        return {"ok": True, "status": "not_downloading"}
    # Kill the local SSH process (drops connection → wget on box stops)
    proc = job.get("proc")
    if proc:
        try:
            proc.kill()
        except Exception:
            pass
    _dl_jobs[filename] = {"status": "cancelled", "error": None, "proc": None}
    # Also clean up the temp file on the box
    tmp = f"/tmp/.dl_{filename}"
    try:
        subprocess.run(BOX_SSH + [f"rm -f {tmp}"], timeout=8, capture_output=True)
    except Exception:
        pass
    return {"ok": True, "status": "cancelled"}

@router.get("/api/models/{filename}/download-status")
def download_status(filename: str):
    """Poll download progress — returns status + bytes_downloaded for in-flight jobs."""
    job = _dl_jobs.get(filename)
    if not job:
        return {"status": "idle"}
    result = {"status": job["status"], "error": job.get("error")}
    if job["status"] == "downloading":
        # Check partial file size in /tmp on the box
        try:
            tmp = f"/tmp/.dl_{filename}"
            r = subprocess.run(
                BOX_SSH + [f"stat -c%s {tmp} 2>/dev/null || echo 0"],
                capture_output=True, text=True, timeout=5
            )
            result["bytes_downloaded"] = int(r.stdout.strip() or 0)
        except Exception:
            result["bytes_downloaded"] = 0
    return result

@router.get("/api/models")
def list_models():
    """List available ComfyUI checkpoint models, with recommended catalogue."""
    installed: list[str] = []
    source = "fallback"
    try:
        r = httpx.get(f"{COMFYUI_URL}/object_info/CheckpointLoaderSimple", timeout=5)
        data = r.json()
        raw = data["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        # Filter out temp download files and hidden files
        installed = [f for f in raw if not f.startswith(".") and not f.startswith("put_")]
        source = "comfyui"
    except Exception:
        installed = [DEFAULT_IMAGE_MODEL]

    # Merge installed flag into recommended catalogue
    rec_copy = []
    for m in RECOMMENDED_MODELS:
        rec_copy.append({**m, "installed": m["filename"] in installed})

    # Add any installed models not in our catalogue
    known_filenames = {m["filename"] for m in RECOMMENDED_MODELS}
    for f in installed:
        if f not in known_filenames:
            rec_copy.append({
                "filename": f, "label": f.replace(".safetensors","").replace(".ckpt",""),
                "style": "Custom", "vram": "?", "source": "Unknown",
                "installed": True, "download": "",
            })

    return {"installed": installed, "recommended": rec_copy, "source": source}


@router.get("/api/loras")
def list_loras():
    """Installed ComfyUI LoRAs (via LoraLoader) + the recommended-LoRA catalogue,
    with an `installed` flag. Downloads reuse /api/models/{filename}/download (each
    LoRA entry's dest_dir sends it to the loras dir, not checkpoints)."""
    installed: list[str] = []
    try:
        r = httpx.get(f"{COMFYUI_URL}/object_info/LoraLoader", timeout=5)
        raw = r.json()["LoraLoader"]["input"]["required"]["lora_name"][0]
        installed = [f for f in raw if not f.startswith(".") and not f.startswith("put_")]
    except Exception:
        pass
    rec = [{**m, "installed": m["filename"] in installed} for m in RECOMMENDED_LORAS]
    known = {m["filename"] for m in RECOMMENDED_LORAS}
    for f in installed:
        if f not in known:
            rec.append({"filename": f, "label": f.replace(".safetensors", ""),
                        "style": "Custom LoRA", "vram": "?", "source": "Unknown",
                        "kind": "lora", "installed": True, "download": ""})
    return {"installed": installed, "recommended": rec}


def _extra_models_compute():
    # ONE SSH round-trip lists every group dir (marked), so the tab isn't blocked by
    # three sequential ls calls. Installed = file present in the group's dir.
    present_by_dir: dict = {}
    try:
        script = "; ".join(f"echo '::{g['dir']}::'; ls -1 {g['dir']} 2>/dev/null" for g in EXTRA_MODEL_GROUPS)
        r = subprocess.run(BOX_SSH + [script], capture_output=True, text=True, timeout=10)
        cur = None
        for ln in (r.stdout or "").splitlines():
            ln = ln.strip()
            if ln.startswith("::") and ln.endswith("::"):
                cur = ln[2:-2]; present_by_dir[cur] = set()
            elif cur is not None and ln:
                present_by_dir[cur].add(ln)
    except Exception:
        pass
    groups = []
    for g in EXTRA_MODEL_GROUPS:
        present = present_by_dir.get(g["dir"], set())
        models = [{**m, "source": m.get("source", "HuggingFace (no auth)"),
                   "auto_download": m.get("auto_download", True),
                   "installed": m["filename"] in present} for m in g["models"]]
        groups.append({"key": g["key"], "label": g["label"], "sub": g["sub"], "models": models})
    return {"groups": groups}


@router.get("/api/extra-models")
def list_extra_models():
    """Specialty ComfyUI models (upscalers / background-removal / controlnet), grouped
    with an installed flag. Cached ~60s (one SSH ls of all group dirs). Downloads reuse
    /api/models/{filename}/download — each entry's dest_dir routes it correctly."""
    from cache import cached
    return cached("extra-models", 60, _extra_models_compute)

@router.get("/api/video-models")
def list_video_models():
    """List recommended video models with HF-cache installed status."""
    result = []
    for m in RECOMMENDED_VIDEO_MODELS:
        key = _hf_model_key(m["model_id"])
        installed = False
        try:
            r = subprocess.run(
                BOX_SSH + [f"ls {_mp.primary("video")}/hub/models--{key}/snapshots/ 2>/dev/null | wc -l"],
                capture_output=True, text=True, timeout=8,
            )
            installed = int((r.stdout or "0").strip()) > 0
        except Exception:
            pass
        entry = {**m, "installed": installed, "key": key}
        # Per-model tunables: catalog defaults ride along via {**m} (gen_defaults);
        # add the owner's saved overrides + the effective merge for the UI.
        entry["gen_overrides"] = video_gen_overrides(m["model_id"])
        entry["gen_settings"] = video_gen_settings(m["model_id"])
        dl = _dl_video_jobs.get(key, {})
        if dl.get("status") in ("downloading", "done", "error", "cancelled"):
            entry["dl_status"] = dl["status"]
        result.append(entry)
    return result


def _save_video_gen_overrides(model_id: str, overrides: dict):
    """Persist a model's override dict into the shared video_model_settings JSON
    (settings table). An empty dict removes the model's entry entirely."""
    raw = get_setting(VIDEO_SETTINGS_KEY)
    try:
        saved = json.loads(raw) if raw else {}
    except Exception:
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    if overrides:
        saved[model_id] = overrides
    else:
        saved.pop(model_id, None)
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                 (VIDEO_SETTINGS_KEY, json.dumps(saved)))
    conn.commit()
    conn.close()


@router.put("/api/video-models/{key}/gen-settings")
def save_video_gen_settings(key: str, body: dict = Body(...)):
    """Save the owner's per-model video-gen tuning (width/height/num_frames/steps/
    fps/guidance/strength). Values equal to the catalog default are dropped —
    submitting an untouched form stores nothing, and clearing every field resets
    the model to stock behavior. Bad keys 400 instead of being silently kept."""
    from model_catalog import VIDEO_GEN_TUNABLES, video_model_gen_defaults
    catalogue = {_hf_model_key(m["model_id"]): m for m in RECOMMENDED_VIDEO_MODELS}
    if key not in catalogue:
        raise HTTPException(404, "Unknown video model")
    model_id = catalogue[key]["model_id"]
    defaults = video_model_gen_defaults(model_id)
    clean = {}
    for k, v in (body or {}).items():
        spec = VIDEO_GEN_TUNABLES.get(k)
        if not spec:
            raise HTTPException(400, f"Unknown setting '{k}'")
        if v in (None, ""):
            continue   # blank = use the default
        typ, lo, hi = spec
        try:
            val = typ(float(v)) if typ is int else typ(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{k} must be a number")
        if not (lo <= val <= hi):
            raise HTTPException(400, f"{k} must be between {lo} and {hi}")
        if val != defaults.get(k):
            clean[k] = val
    _save_video_gen_overrides(model_id, clean)
    return {"ok": True, "overrides": clean,
            "effective": video_gen_settings(model_id)}


@router.delete("/api/video-models/{key}/gen-settings")
def reset_video_gen_settings(key: str):
    """Reset a video model's tuning back to the catalog defaults."""
    catalogue = {_hf_model_key(m["model_id"]): m for m in RECOMMENDED_VIDEO_MODELS}
    if key not in catalogue:
        raise HTTPException(404, "Unknown video model")
    model_id = catalogue[key]["model_id"]
    _save_video_gen_overrides(model_id, {})
    return {"ok": True, "overrides": {},
            "effective": video_gen_settings(model_id)}

@router.post("/api/video-models/{key}/download")
def start_video_model_download(key: str):
    """Trigger download of a video model to the box via HF snapshot_download."""
    catalogue = {_hf_model_key(m["model_id"]): m for m in RECOMMENDED_VIDEO_MODELS}
    if key not in catalogue:
        raise HTTPException(404, "Unknown video model")
    if _dl_video_jobs.get(key, {}).get("status") == "downloading":
        return {"ok": True, "status": "already_downloading"}

    model_id = catalogue[key]["model_id"]
    cmd = (
        f"HF_HOME={_mp.primary("video")} {BOX_VENV_PYTHON} -c \""
        f"from huggingface_hub import snapshot_download; "
        f"snapshot_download('{model_id}')\""
    )
    proc = subprocess.Popen(
        BOX_SSH + [cmd],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    _dl_video_jobs[key] = {"status": "downloading", "error": None, "proc": proc}

    def _monitor(k: str, p):
        out, _ = p.communicate()
        job = _dl_video_jobs.get(k, {})
        if job.get("status") == "cancelled":
            return
        if p.returncode == 0:
            _dl_video_jobs[k] = {"status": "done", "error": None, "proc": None}
        else:
            err = (out or "").strip()[-400:] or "Download failed"
            _dl_video_jobs[k] = {"status": "error", "error": err, "proc": None}

    threading.Thread(target=_monitor, args=(key, proc), daemon=True).start()
    return {"ok": True, "status": "downloading"}

@router.delete("/api/video-models/{key}/download")
def cancel_video_model_download(key: str):
    """Cancel an in-progress video model download."""
    job = _dl_video_jobs.get(key)
    if not job or job.get("status") != "downloading":
        return {"ok": True, "status": "not_downloading"}
    proc = job.get("proc")
    if proc:
        try:
            proc.kill()
        except Exception:
            pass
    _dl_video_jobs[key] = {"status": "cancelled", "error": None, "proc": None}
    return {"ok": True, "status": "cancelled"}

@router.get("/api/video-models/{key}/download-status")
def video_model_download_status(key: str):
    """Poll video model download progress."""
    job = _dl_video_jobs.get(key)
    if not job:
        return {"status": "idle"}
    result: dict = {"status": job["status"], "error": job.get("error")}
    if job["status"] == "downloading":
        try:
            r = subprocess.run(
                BOX_SSH + [f"du -sb {_mp.primary("video")}/hub/models--{key}/ 2>/dev/null | cut -f1 || echo 0"],
                capture_output=True, text=True, timeout=5,
            )
            result["bytes_downloaded"] = int((r.stdout or "0").strip() or 0)
        except Exception:
            result["bytes_downloaded"] = 0
    return result

"""Standalone Music/Audio generation — music (MusicGen/ACE-Step/Stable Audio) and
voice (MMS-TTS) clips, generated on the GPU node."""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from deps import *
import services as _svc
import subprocess, threading, re as _re

router = APIRouter()

# Downloadable audio models, shown in the Models tab like image/video models.
AUDIO_MODELS = [
    {"key": "musicgen",     "engine": "musicgen",     "label": "MusicGen Small (music)",
     "repo": "facebook/musicgen-small",  "kind": "music", "vram": "~4 GB",  "size": "~2 GB",
     "note": "Fast instrumental music. Downloads on first use."},
    {"key": "musicgen_med", "engine": "musicgen_med", "label": "MusicGen Medium (music)",
     "repo": "facebook/musicgen-medium", "kind": "music", "vram": "~8 GB",  "size": "~6 GB",
     "note": "Richer instrumental than Small."},
    {"key": "mms_tts",      "engine": "mms_tts",      "label": "Voice narration (MMS-TTS)",
     "repo": "facebook/mms-tts-eng",     "kind": "voice", "vram": "~2 GB",  "size": "~150 MB",
     "note": "Text-to-speech narration."},
    {"key": "stable_audio", "engine": "stable_audio", "label": "Stable Audio Open (hi-fi)",
     "repo": "stabilityai/stable-audio-open-1.0", "kind": "music", "vram": "~8 GB", "size": "~5 GB",
     "note": "Hi-fi instrumental. GATED — accept the license on Hugging Face and set an HF token on the node first.",
     "gated": True},
    {"key": "acestep",      "engine": "acestep",      "label": "ACE-Step (songs w/ vocals + lyrics)",
     "repo": "ACE-Step/ACE-Step-v1-3.5B", "kind": "music", "vram": "~10-12 GB", "size": "~8 GB",
     "note": "Full songs with vocals & lyrics. Installs its own venv + model (large).",
     "install": True},
]
_dl_audio_jobs: dict = {}


def _hf_dir(repo: str) -> str:
    return "models--" + repo.replace("/", "--")


@router.get("/api/audio-models")
def list_audio_models():
    """Catalog + installed status (checks the node's HF cache / ACE-Step venv)."""
    _adir = _svc.audio_models_dir()
    hub = f'"{_adir}/hub"' if _adir else "$HOME/.cache/huggingface/hub"
    lines = [f"hf={hub}"]
    for m in AUDIO_MODELS:
        if m.get("install"):
            # Fast filesystem check (importing acestep loads torch → ~5s, made the Models
            # tab crawl). Repo module + venv present == installed.
            lines.append("([ -f ~/ACE-Step/acestep/pipeline_ace_step.py ] && "
                         "[ -x ~/ace-venv/venv/bin/python3 ]) "
                         f"&& echo {m['key']}=ok || echo {m['key']}=no")
        else:
            d = _hf_dir(m["repo"])
            lines.append(f"([ -d \"$hf/{d}/snapshots\" ] && ls \"$hf/{d}/snapshots\" 2>/dev/null "
                         f"| grep -q . ) && echo {m['key']}=ok || echo {m['key']}=no")
    snippet = "\n".join(lines)
    inst = {}
    try:
        r = subprocess.run(BOX_SSH + ["bash -s"], input=snippet, capture_output=True, text=True, timeout=25)
        for ln in (r.stdout or "").splitlines():
            if "=" in ln:
                k, v = ln.strip().split("=", 1)
                inst[k] = (v == "ok")
    except Exception:
        pass
    out = []
    for m in AUDIO_MODELS:
        e = {**m, "installed": inst.get(m["key"], False)}
        job = _dl_audio_jobs.get(m["key"], {})
        if job.get("status"):
            e["dl_status"] = job["status"]
            if job.get("error"):
                e["dl_error"] = job["error"]
        out.append(e)
    return out


def _audio_dl_cmd(m: dict) -> str:
    if m.get("install"):
        # ACE-Step: clone + own venv (NOT named 'acestep' — would shadow the package) +
        # editable install + full requirements. Idempotent. Uses python -m pip throughout.
        return (
            "bash -lc 'set -e; cd ~; "
            "[ -d ~/ACE-Step ] || git clone --depth 1 https://github.com/ace-step/ACE-Step.git ~/ACE-Step; "
            "[ -x ~/ace-venv/venv/bin/python3 ] || { mkdir -p ~/ace-venv; python3 -m venv ~/ace-venv/venv; }; "
            "P=~/ace-venv/venv/bin/python3; $P -m pip install -q --upgrade pip; "
            "$P -m pip install -q torch --index-url https://download.pytorch.org/whl/cu121; "
            "cd ~/ACE-Step && $P -m pip install -q -e . && $P -m pip install -q -r requirements.txt; "
            # torchaudio 2.9+ saves via torchcodec; soundfile is a fallback. FFmpeg must be present.
            "$P -m pip install -q soundfile torchcodec; "
            "$P -c \"from acestep.pipeline_ace_step import ACEStepPipeline\"'"
        )
    adir = _svc.audio_models_dir()
    hfenv = f"HF_HOME={adir} " if adir else ""
    return (f"{hfenv}{BOX_VENV_PYTHON} -c \"from huggingface_hub import snapshot_download; "
            f"snapshot_download('{m['repo']}')\"")


@router.post("/api/audio-models/{key}/download")
def start_audio_model_download(key: str):
    m = next((x for x in AUDIO_MODELS if x["key"] == key), None)
    if not m:
        raise HTTPException(404, "Unknown audio model")
    if _dl_audio_jobs.get(key, {}).get("status") == "downloading":
        return {"ok": True, "status": "already_downloading"}
    proc = subprocess.Popen(BOX_SSH + [_audio_dl_cmd(m)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    _dl_audio_jobs[key] = {"status": "downloading", "error": None, "proc": proc}

    def _mon(k, p, gated):
        out, _ = p.communicate()
        if _dl_audio_jobs.get(k, {}).get("status") == "cancelled":
            return
        if p.returncode == 0:
            _dl_audio_jobs[k] = {"status": "done", "error": None, "proc": None}
        else:
            err = (out or "").strip()[-400:] or "Download failed"
            if gated and ("gated" in err.lower() or "401" in err or "403" in err or "restricted" in err.lower()):
                err = ("This model is gated — accept the license at huggingface.co and put an HF "
                       "token on the node (huggingface-cli login), then retry. " + err[-160:])
            _dl_audio_jobs[k] = {"status": "error", "error": err, "proc": None}

    threading.Thread(target=_mon, args=(key, proc, m.get("gated")), daemon=True).start()
    return {"ok": True, "status": "downloading"}


@router.delete("/api/audio-models/{key}/download")
def cancel_audio_model_download(key: str):
    job = _dl_audio_jobs.get(key)
    if not job or job.get("status") != "downloading":
        return {"ok": True, "status": "not_downloading"}
    if job.get("proc"):
        try: job["proc"].kill()
        except Exception: pass
    _dl_audio_jobs[key] = {"status": "cancelled", "error": None, "proc": None}
    return {"ok": True, "status": "cancelled"}


@router.get("/api/audio-models/{key}/download-status")
def audio_model_download_status(key: str):
    job = _dl_audio_jobs.get(key)
    if not job:
        return {"status": "idle"}
    return {"status": job.get("status", "idle"), "error": job.get("error")}


class AudioRequest(BaseModel):
    prompt: str
    engine: str = "musicgen"
    duration: int = 8
    model_id: str = ""
    lyrics: str = ""


class EnhancePromptReq(BaseModel):
    prompt: str
    kind: str = "music"


@router.post("/api/audio/enhance-prompt")
def enhance_audio_prompt(req: EnhancePromptReq):
    """Enhance the user's raw idea into a rich prompt. Returns {task_id}; poll
    /api/task/{id} → {enhanced}."""
    idea = req.prompt.strip()
    if not idea:
        raise HTTPException(400, "prompt required")
    SYS = get_prompt('audio_voice') if req.kind == "voice" else get_prompt('audio_music')

    def _work():
        # 2000 tokens (was 900): the QAT enhance model spends ~500+ on <think> before
        # writing FINAL:, so 900 truncated it → empty result → "Could not enhance".
        raw = _call_lmstudio(SYS, idea, max_tokens=2000)
        import re as _re
        txt = _re.sub(r'<think>.*?</think>', '', raw, flags=_re.DOTALL)
        # Reasoning models ramble; take the last 'FINAL:' line, else last quote/line.
        marks = list(_re.finditer(r'FINAL:\s*(.+)', txt))
        if marks:
            cleaned = marks[-1].group(1)
        else:
            quotes = _re.findall(r'"([^"]{20,400})"', txt)
            if quotes:
                cleaned = quotes[-1]
            else:
                lines = [l.strip() for l in txt.splitlines() if len(l.strip()) > 25]
                cleaned = lines[-1] if lines else txt
        cleaned = cleaned.strip().strip('"*').strip()
        return {"enhanced": cleaned, "original": idea}

    tid = orch.submit_llm(_work, desc=f"Enhance audio: {idea[:40]}", priority=0, task=("audio_voice" if req.kind == "voice" else "audio_music"))   # user waiting
    return {"task_id": tid}


class LyricsReq(BaseModel):
    prompt: str = ""          # the song idea / theme (or an existing draft to rework)
    style: str = ""           # the music prompt: genre / mood / voice context


@router.post("/api/audio/generate-lyrics")
def generate_lyrics(req: LyricsReq):
    """Write ACE-Step-formatted lyrics from a song idea (+ the genre/mood prompt for
    context). Rides the unified queue; returns {task_id}, poll /api/task/{id} → the
    result drops into the lyrics field."""
    idea = (req.prompt or "").strip()
    style = (req.style or "").strip()
    if not idea and not style:
        raise HTTPException(400, "Describe the song (theme and/or genre/mood) first")
    SYS = get_prompt('audio_lyrics')
    # User turn = the theme plus the style context so the words match the track.
    user = idea or f"A song in this style: {style}"
    if style and idea:
        user = f"Song idea / theme:\n{idea}\n\nMusical style (match this):\n{style}"

    def _work():
        # 4000 tokens: a full song (intro/verses/chorus×2/bridge/outro) is far longer
        # than the single 'FINAL:' line the enhancer emits (2000), and reasoning models
        # still burn a slice on <think> even with reasoning_effort=none — give the words
        # room so the song never truncates mid-verse.
        raw = _call_lmstudio(SYS, user, max_tokens=4000)
        import re as _re
        txt = _re.sub(r'<think>.*?</think>', '', raw, flags=_re.DOTALL).strip()
        # Strip a ``` fenced block if the model wrapped the lyrics in one.
        m = _re.search(r'```[a-zA-Z]*\s*(.+?)\s*```', txt, flags=_re.DOTALL)
        if m:
            txt = m.group(1).strip()
        # Drop any preamble before the first section tag ([verse]/[intro]/…).
        tag = _re.search(r'\[[a-zA-Z][a-zA-Z0-9 _-]*\]', txt)
        if tag:
            txt = txt[tag.start():].strip()
        # `text` is the field the shared enhance helper drops into the target textarea.
        return {"lyrics": txt, "text": txt, "style": style}

    tid = orch.submit_llm(_work, desc=f"Write lyrics: {(idea or style)[:40]}",
                          priority=0, task="audio_lyrics")   # user waiting
    return {"task_id": tid}


@router.get("/api/audio/engines")
def audio_engines():
    return [{"key": k, **v} for k, v in _svc.AUDIO_ENGINES.items()]


@router.get("/api/audio")
def list_audio():
    conn = get_conn()
    # nsfw-flagged clips never appear here — /api/nsfw/library only.
    # studio_cue_id clips belong to the Director tab, chain_id clips to their
    # video chain — neither shows in this gallery.
    rows = conn.execute("SELECT * FROM audio_clips WHERE COALESCE(nsfw,0)=0 "
                        "AND studio_cue_id IS NULL AND chain_id IS NULL "
                        "ORDER BY created_at DESC LIMIT 200").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/audio/generate")
def generate_audio(req: AudioRequest, background_tasks: BackgroundTasks):
    if not req.prompt.strip():
        raise HTTPException(400, "prompt required")
    if req.engine not in _svc.AUDIO_ENGINES:
        raise HTTPException(400, f"unknown engine {req.engine}")
    kind = _svc.AUDIO_ENGINES[req.engine]["kind"]
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO audio_clips (kind,prompt,engine,model_id,duration,lyrics,status) "
        "VALUES (?,?,?,?,?,?, 'queued')",
        (kind, req.prompt.strip(), req.engine, req.model_id or None,
         max(3, min(240, req.duration)), (req.lyrics or "").strip() or None))
    cid = cur.lastrowid
    conn.commit()
    conn.close()
    background_tasks.add_task(_svc.run_audio_clip, cid)
    return {"id": cid, "status": "queued"}


@router.delete("/api/audio/{clip_id}")
def delete_audio(clip_id: int):
    conn = get_conn()
    row = conn.execute("SELECT audio_path FROM audio_clips WHERE id=?", (clip_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Clip not found")
    if row["audio_path"]:
        try:
            Path(row["audio_path"]).unlink(missing_ok=True)
        except Exception:
            pass
    conn.execute("DELETE FROM audio_clips WHERE id=?", (clip_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

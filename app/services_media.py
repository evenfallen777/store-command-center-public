"""Video + audio generation on the GPU node — diffusers video (Wan/LTX/CogVideoX),
MusicGen + MMS-TTS + Stable Audio + ACE-Step music/voice, and the video→audio bridge.
Split out of services.py for size; re-exported by it (from services_media import *)."""
import signal
import re as _re
from deps import *
# Audio (music/voice) + video-chain pipeline split out for size; re-exported so
# `from services_media import *` keeps an identical export surface.
from services_media_audio import *
from services_media_chain import *


# ─── Video generation infrastructure ─────────────────────────────────────────
# Registry of running gen subprocesses so we can cancel a stuck job, + a mutex so
# two videos never hit the single GPU node at once (they'd fight over VRAM).
_VIDEO_PROCS: dict = {}          # vid_id -> Popen
_VIDEO_PROCS_LOCK = threading.Lock()
_VIDEO_RUN_LOCK = threading.Lock()   # only one video subprocess at a time

# First-run downloads are big; heavy models take far longer than the 1.3B default.
_VIDEO_TIMEOUTS = {
    "Wan-AI/Wan2.1-T2V-14B-Diffusers": 3600,
    "THUDM/CogVideoX-5b":              3600,
    "tencent/HunyuanVideo":            5400,
}
def _video_timeout(model_id: str) -> int:
    try:
        base = int(_env("STORE_VIDEO_TIMEOUT", "0")) if "_env" in globals() else 0
    except Exception:
        base = 0
    return base or _VIDEO_TIMEOUTS.get(model_id or "", 1800)


# ── Video model VRAM preflight ───────────────────────────────────────────────
# Mirror of the 3D gate (services_3d.check_3d_vram_or_raise): the heavy video
# models (Wan2.1-14B ~20 GB, CogVideoX-5b ~16 GB, Hunyuan ~24 GB) can never fit
# the node's 12 GB card — without this gate, picking one launches a job that
# downloads tens of GB and then dies in a cryptic CUDA OOM deep in the pipeline.
# Fail fast at request time instead, with a clear message.

def _video_model_vram(model_id: str):
    """(min_vram_mb, label) for a catalog video model, or (None, model_id) when the
    model is unknown / has no declared floor — unknown models are never blocked."""
    try:
        from model_catalog import RECOMMENDED_VIDEO_MODELS
        for m in RECOMMENDED_VIDEO_MODELS:
            if m.get("model_id") == model_id:
                return m.get("min_vram_mb"), m.get("label", model_id)
    except Exception:
        pass
    return None, model_id


def check_video_vram_or_raise(model_id: str) -> None:
    """Preflight for the video generate/chain paths: if `model_id`'s min_vram_mb
    exceeds the node's GPU capacity, raise a clear HTTPException instead of letting
    the job launch into a multi-GB download followed by a mid-pipeline CUDA OOM.
    Unknown capacity (node unreachable / nvidia-smi missing) is NOT a reason to
    block — we only refuse when we POSITIVELY know it won't fit. Never raises
    anything other than the intended HTTPException."""
    need, label = _video_model_vram(model_id or "")
    if not need:
        return
    try:
        from services_3d import gpu_capacity_mb
        cap = gpu_capacity_mb()
    except Exception:
        cap = None
    if cap is not None and cap < need:
        raise HTTPException(
            400,
            f"{label} needs ~{need // 1000} GB VRAM; the GPU node has "
            f"~{cap // 1000} GB — pick a smaller model (Wan2.1 1.3B fits)."
        )


# ── Per-model video-gen settings (catalog defaults + owner overrides) ────────
# The catalog's gen_defaults block (model_catalog.RECOMMENDED_VIDEO_MODELS) holds
# each model's tunable params at exactly the values used before tuning existed;
# the owner's saved overrides live in the settings table under this key as JSON
# {model_id: {param: value}}. video_gen_settings() is the ONE merge point — the
# request routers resolve omitted fields through it, and the run paths resolve
# `guidance` through it at launch time.
VIDEO_SETTINGS_KEY = "video_model_settings"


def video_gen_overrides(model_id: str) -> dict:
    """The owner's saved per-model overrides — validated against
    model_catalog.VIDEO_GEN_TUNABLES (unknown keys dropped, values clamped),
    {} when none/invalid. Never raises."""
    try:
        from model_catalog import VIDEO_GEN_TUNABLES
        raw = get_setting(VIDEO_SETTINGS_KEY)
        saved = json.loads(raw) if raw else {}
        out = {}
        for k, v in (saved.get(model_id) or {}).items():
            spec = VIDEO_GEN_TUNABLES.get(k)
            if not spec:
                continue
            typ, lo, hi = spec
            try:
                out[k] = min(max(typ(v), lo), hi)
            except (TypeError, ValueError):
                continue
        return out
    except Exception:
        return {}


def video_gen_settings(model_id: str) -> dict:
    """Effective per-model video-gen params: catalog gen_defaults overlaid with
    the owner's overrides. With no overrides saved this returns exactly the
    pre-tuning values, so an untouched install behaves identically."""
    from model_catalog import video_model_gen_defaults
    s = video_model_gen_defaults(model_id or "")
    s.update(video_gen_overrides(model_id or ""))
    return s


def _video_guidance_args(model_id: str) -> list:
    """Optional trailing generator arg for per-model guidance/CFG. Catalog models
    always resolve a guidance value (default == what the node hardcodes today);
    off-catalog models resolve none → no extra arg, exactly the old command line.
    Older copies of generate_video*.sh simply ignore the extra positional, so
    passing it is safe even before the scripts/node are synced."""
    g = video_gen_settings(model_id).get("guidance")
    return [str(g)] if g is not None else []


def _video_preflight() -> tuple:
    """Fast checks so a doomed job fails instantly with a clear reason instead of
    burning 30 minutes. Returns (ok: bool, message: str)."""
    if not Path(VIDEO_GEN_SCRIPT).exists():
        return False, (f"Video generator script not found at {VIDEO_GEN_SCRIPT}. "
                       "Set STORE_VIDEO_GEN_SCRIPT in Settings to point at it.")
    host = globals().get("GPU_HOST", "")
    if host and "BOX_SSH" in globals():
        try:
            r = subprocess.run(
                BOX_SSH + ["true"],
                capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                return False, (f"GPU node {host} is unreachable over SSH "
                               f"({(r.stderr or '').strip()[:120] or 'no route / auth'}). "
                               "Is the box on and reachable?")
        except subprocess.TimeoutExpired:
            return False, f"GPU node {host} did not respond within 15s (box off or network down?)."
        except Exception as ex:
            return False, f"Could not reach GPU node {host}: {ex}"
    return True, "ok"


import re as _re

def _parse_gen_line(line: str, state: dict, steps: int):
    """Turn a generator output line into (percent, message) for the progress bar, or
    None. Uses the remote script's flushed '[videogen] <phase>' markers to know which
    phase we're in, then maps the denoising step counter (the tqdm bar whose total ==
    num_inference_steps, or an explicit '[progress] X/Y') across the 10–95% band.
    Model-loading / VAE tqdm bars (different totals) are ignored so the bar doesn't
    jump around. `state` carries the 'in generation phase' flag between lines."""
    l = line.strip()
    low = l.lower()
    # Phase markers (authoritative about where we are)
    if "[videogen]" in l:
        if "loading previous" in low:
            return 6, "Loading previous segment…"
        if "loading" in low and "loaded" not in low:
            return 3, "Loading model from cache…"
        if l.lower().startswith("[videogen] loaded") or "cpu offload" in low:
            return 8, "Model loaded — preparing GPU…"
        if "generating" in low:
            state["gen"] = True
            return 10, "Generating…"
        if "done:" in low:
            state["gen"] = False
            return 97, "Encoding video…"
        return None
    # Step counters — explicit [progress] (authoritative) or the denoise tqdm bar.
    m = _re.search(r"\[progress\]\s*(\d+)\s*/\s*(\d+)", l)
    if not m:
        m = _re.search(r"(\d+)\s*/\s*(\d+)\s*\[", l)   # tqdm: "9/15 [00:12<..]"
    if m:
        cur, tot = int(m.group(1)), max(1, int(m.group(2)))
        # Only the denoising loop (total == requested steps) drives the bar; ignore
        # loading/VAE bars with other totals. Once generating, allow it.
        if state.get("gen") and steps and tot == int(steps):
            return min(95, 10 + int(85 * min(cur, tot) / tot)), f"Generating — step {cur}/{tot}"
    return None


def _run_gen(cmd: list, timeout: int, vid_id: int, on_progress=None, steps: int = 0):
    """Run a generation subprocess in its own process group so a cancel can kill the
    whole tree (ssh + remote python). Streams output line-by-line to drive a live
    progress bar via on_progress(pct, msg). Registers it under vid_id for cancellation.
    Returns a CompletedProcess-like object with returncode/stdout/stderr."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, start_new_session=True)
    with _VIDEO_PROCS_LOCK:
        _VIDEO_PROCS[vid_id] = proc

    timed_out = {"v": False}
    def _on_timeout():
        timed_out["v"] = True
        _kill_proc(proc)
    timer = threading.Timer(timeout, _on_timeout)
    timer.daemon = True
    timer.start()

    tail = []   # keep the last N lines for error reporting
    pstate = {"gen": False}
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            tail.append(line)
            if len(tail) > 300:
                tail = tail[-300:]
            if on_progress:
                try:
                    pm = _parse_gen_line(line, pstate, steps)
                    if pm:
                        on_progress(pm[0], pm[1])
                except Exception:
                    pass
        proc.wait()
    finally:
        timer.cancel()
        with _VIDEO_PROCS_LOCK:
            _VIDEO_PROCS.pop(vid_id, None)

    if timed_out["v"]:
        raise subprocess.TimeoutExpired(cmd, timeout)

    class _R:
        pass
    r = _R()
    r.returncode = proc.returncode
    r.stdout = "\n".join(tail)
    r.stderr = ""   # merged into stdout
    return r


def _kill_proc(proc):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def cancel_video(vid_id: int) -> bool:
    """Kill a running video subprocess (if any). Returns True if one was killed."""
    with _VIDEO_PROCS_LOCK:
        proc = _VIDEO_PROCS.get(vid_id)
    if proc and proc.poll() is None:
        _kill_proc(proc)
        return True
    return False


def reconcile_stuck_media():
    """On startup, any video/chain left 'generating' or 'queued' is orphaned (the
    process died with the previous server). Mark it failed with a clear reason so the
    gallery doesn't poll forever on a job that will never finish."""
    try:
        conn = get_conn()
        conn.execute(
            "UPDATE videos SET status='failed', error='Interrupted by a server restart — "
            "re-generate to try again.', updated_at=datetime('now') "
            "WHERE status IN ('queued','generating')")
        conn.execute(
            "UPDATE video_chains SET status='failed', "
            "error=COALESCE(error,'Interrupted by a server restart'), updated_at=datetime('now') "
            "WHERE status IN ('pending','generating')")
        conn.execute(
            "UPDATE videos SET audio_status='failed', "
            "audio_error='Interrupted by a server restart' "
            "WHERE audio_status IN ('queued','generating')")
        conn.execute(
            "UPDATE audio_clips SET status='failed', "
            "error='Interrupted by a server restart' WHERE status IN ('queued','generating')")
        conn.commit()
        conn.close()
    except Exception as ex:
        logger.error("reconcile_stuck_media failed: %s", ex)


def _set_video_progress(vid_id: int, pct: int, msg: str):
    try:
        conn = get_conn()
        conn.execute("UPDATE videos SET progress=?,progress_msg=? WHERE id=?",
                     (max(0, min(100, int(pct))), msg[:120], vid_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _fail_video(vid_id: int, reason: str):
    try:
        conn = get_conn()
        conn.execute("UPDATE videos SET status='failed',error=?,updated_at=datetime('now') WHERE id=?",
                     (reason[:800], vid_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


def run_video_generation(vid_id: int):
    """Generate a video on the GPU node via SSH. One video at a time (GPU mutex),
    with a preflight check and the failure reason surfaced to the UI."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM videos WHERE id=?", (vid_id,)).fetchone()
    if not row:
        conn.close()
        return
    row = dict(row)
    conn.close()
    model_id = row["model_id"] or "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"

    # Preflight before touching VRAM — fail fast with a clear reason.
    ok, msg = _video_preflight()
    if not ok:
        logger.error("Video %d preflight failed: %s", vid_id, msg)
        _fail_video(vid_id, msg)
        return

    # Serialize: only one video subprocess against the single GPU node at a time.
    with _VIDEO_RUN_LOCK:
        # Cancelled/deleted while we waited for the lock?
        c0 = get_conn()
        cur = c0.execute("SELECT status FROM videos WHERE id=?", (vid_id,)).fetchone()
        c0.close()
        if not cur or cur["status"] not in ("queued", "generating"):
            return

        try:
            # frees ComfyUI + LLM VRAM and VERIFIES it via nvidia-smi;
            # T5-XXL text encoder alone needs ~9.5 GB. model_id lets the unified
            # queue batch same-model video work and drain resident-LLM work first.
            orch.video_acquire(model=model_id, desc=f"Video {vid_id}")
        except RuntimeError as ex:
            # GPU could not be freed (offender named in the message) — fail fast
            # and clearly. A failed acquire holds nothing: no video_release().
            logger.error("Video %d GPU acquire failed: %s", vid_id, ex)
            _fail_video(vid_id, str(ex))
            return
        conn = get_conn()
        conn.execute("UPDATE videos SET status='generating',error=NULL,progress=1,progress_msg='Starting…',updated_at=datetime('now') WHERE id=?", (vid_id,))
        conn.commit()

        seed = row["seed"] if row["seed"] else random.randint(1, 2**31 - 1)
        out_path = VIDEOS_DIR / f"vid_{vid_id}_{int(datetime.now().timestamp())}.mp4"
        try:
            result = _run_gen(
                [str(VIDEO_GEN_SCRIPT), row["prompt"], str(out_path),
                 str(row["width"] or 832), str(row["height"] or 480),
                 str(row["num_frames"] or 49), str(row["steps"] or 20),
                 str(seed), str(row["fps"] or 16), model_id]
                + _video_guidance_args(model_id),
                timeout=_video_timeout(model_id), vid_id=vid_id,
                on_progress=lambda p, m: _set_video_progress(vid_id, p, m),
                steps=int(row["steps"] or 20),
            )
            if result.returncode == 0 and out_path.exists():
                conn.execute(
                    "UPDATE videos SET status='done',video_path=?,seed=?,error=NULL,progress=100,progress_msg='Done',updated_at=datetime('now') WHERE id=?",
                    (str(out_path), seed, vid_id),
                )
                logger.info("Video %d done: %s", vid_id, out_path)
            else:
                err = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()[-800:] or "unknown error"
                logger.error("Video %d failed (rc=%d): %s", vid_id, result.returncode, err)
                conn.execute("UPDATE videos SET status='failed',error=?,updated_at=datetime('now') WHERE id=?",
                             (err, vid_id))
        except subprocess.TimeoutExpired:
            logger.error("Video %d timed out", vid_id)
            conn.execute("UPDATE videos SET status='failed',error=?,updated_at=datetime('now') WHERE id=?",
                         (f"Timed out after {_video_timeout(model_id)//60} min — try fewer frames/steps or a lighter model.", vid_id))
        except Exception as ex:
            logger.error("Video %d exception: %s", vid_id, ex)
            conn.execute("UPDATE videos SET status='failed',error=?,updated_at=datetime('now') WHERE id=?",
                         (str(ex)[:800], vid_id))
        finally:
            conn.commit()
            conn.close()
            orch.video_release()

# Export everything (incl. single-underscore helpers used across modules).
__all__ = [n for n in dir() if not n.startswith('__')]

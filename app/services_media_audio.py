"""Audio (music + voice) generation on the GPU node + the video→audio bridge:
MusicGen / MMS-TTS / Stable Audio / ACE-Step, and muxing generated music+narration
onto silent videos. Split out of services_media.py for size; re-exported by it
(from services_media_audio import *)."""
from deps import *

# ─── Audio (music + voice) on the node + video→audio bridge ──────────────────
_SCP = ["scp", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]


def audio_models_dir() -> str:
    """Node-side directory the audio models live in. The live `models_dir_audio`
    setting (Settings → 🧠 Models → 📁 Storage) wins; falls back to the
    STORE_AUDIO_MODELS_DIR / STORE_HF_AUDIO env values. Empty = the node's default
    HF cache. Sets HF_HOME so MusicGen/MMS/Stable-Audio cache there; ACE-Step
    uses <dir>/ace-step."""
    try:
        import model_paths
        return model_paths.primary("audio")
    except Exception:
        return (os.environ.get("STORE_AUDIO_MODELS_DIR") or "").strip().rstrip("/")


def _audio_env(engine: str = "") -> str:
    parts = ["HF_HUB_OFFLINE=0", "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True"]
    d = audio_models_dir()
    if d:
        parts.append(f"HF_HOME={d}")
        if engine == "acestep":
            parts.append(f"STORE_ACE_STORAGE={d}/ace-step")
    # Gated models (e.g. Stable Audio Open) need an HF token whose account has accepted
    # the model's license — else the download raises GatedRepoError. Pass it through when
    # configured (Settings → hf_token, stored encrypted at rest). Single-quoted for the
    # remote shell; tokens are hf_[A-Za-z0-9_] so stripping quotes is safe.
    tok = (get_setting("hf_token") or "").strip().replace("'", "")
    if tok:
        parts.append(f"HF_TOKEN='{tok}'")
        parts.append(f"HUGGING_FACE_HUB_TOKEN='{tok}'")
    return " ".join(parts)


# Engines whose model is GATED on Hugging Face (license must be accepted + an HF token
# provided). Without a token these fail with GatedRepoError, so we fall back to a public
# engine instead of spamming failures (which the world-security monitor then flags).
GATED_ENGINES = {"stable_audio"}


def _node_audio(mode: str, prompt: str, out_wav_local: str, duration: int = 8,
                model_id: str = "", seed: int = 0, engine: str = "", lyrics: str = "",
                timeout: int = 1200):
    """Run store_audiogen.py on the GPU node (music|voice) and copy the wav back.
    Caller must hold the GPU (orch.video_acquire) — audio needs the VRAM the LLM uses."""
    tgt = f"{GPU_SSH_USER}@{GPU_HOST}"
    ts = int(datetime.now().timestamp())
    r_args = f"/tmp/store_aud_args_{ts}.json"
    r_wav  = f"/tmp/store_aud_out_{ts}.wav"
    args = {"mode": mode, "prompt": prompt, "output": r_wav}
    if mode == "music":
        args["duration"] = duration
    if model_id:
        args["model_id"] = model_id
    if engine:
        args["engine"] = engine
    if lyrics:
        args["lyrics"] = lyrics
    if seed:
        args["seed"] = seed
    l_args = Path(VIDEOS_DIR) / f".aud_args_{ts}.json"
    l_args.write_text(json.dumps(args))
    try:
        subprocess.run(_SCP + [str(l_args), f"{tgt}:{r_args}"], check=True, capture_output=True, timeout=30)
        # ACE-Step runs in its own venv from its repo dir (import needs cwd=~/ACE-Step);
        # everything else uses the ComfyUI venv. Models cache under STORE_AUDIO_MODELS_DIR.
        env = _audio_env(engine)
        if engine == "acestep":
            cmd = f"cd ~/ACE-Step && {env} ~/ace-venv/venv/bin/python3 ~/store_audiogen.py {r_args}"
        else:
            cmd = f"{env} ~/ComfyUI/venv/bin/python3 ~/store_audiogen.py {r_args}"
        r = subprocess.run(BOX_SSH + [cmd], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "audio generation failed").strip()[-500:])
        cp = subprocess.run(_SCP + [f"{tgt}:{r_wav}", str(out_wav_local)], capture_output=True, text=True, timeout=90)
        if cp.returncode != 0 or not Path(out_wav_local).exists():
            raise RuntimeError("generated audio but couldn't copy it back from the node")
    finally:
        try: l_args.unlink(missing_ok=True)
        except Exception: pass
        subprocess.run(BOX_SSH + [f"rm -f {r_args} {r_wav}"], capture_output=True, timeout=15)
    return out_wav_local


def _video_duration(path: str) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], capture_output=True, text=True, timeout=20)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _mux_audio(video: str, music: str, voice: str, out: str):
    """Mux background music (looped/trimmed, quiet) + optional voice (front, full) onto
    a silent video with ffmpeg. Output length = the video's length."""
    dur = _video_duration(video) or 5.0
    inputs = ["-i", video, "-stream_loop", "-1", "-i", music]
    if voice:
        inputs += ["-i", voice]
        fc = ("[1:a]volume=0.28[bg];[2:a]volume=1.0[vo];"
              "[bg][vo]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        fc = "[1:a]volume=0.6[a]"
    cmd = (["ffmpeg", "-y"] + inputs +
           ["-filter_complex", fc, "-map", "0:v", "-map", "[a]",
            "-t", f"{dur:.2f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", out])
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    return out


def add_video_audio(vid_id: int, music_prompt: str, narration: str = ""):
    """Background task: generate music (+ optional narration voice) for a video and mux
    it on. Sets videos.audio_path / audio_status / audio_error."""
    # Video-infra helpers live in services_media; import lazily to avoid an import cycle.
    from services_media import _video_preflight, _set_video_progress, _VIDEO_RUN_LOCK
    conn = get_conn()
    row = conn.execute("SELECT * FROM videos WHERE id=?", (vid_id,)).fetchone()
    conn.close()
    if not row or not row["video_path"] or not Path(row["video_path"]).exists():
        _set_audio(vid_id, "failed", err="Video file not found")
        return
    video = row["video_path"]
    _set_audio(vid_id, "generating")
    ok, msg = _video_preflight()
    if not ok:
        _set_audio(vid_id, "failed", err=msg)
        return
    ts = int(datetime.now().timestamp())
    music_wav = str(VIDEOS_DIR / f"aud_{vid_id}_music_{ts}.wav")
    voice_wav = ""
    with _VIDEO_RUN_LOCK:
        try:
            orch.video_acquire()
        except RuntimeError as ex:
            # GPU could not be freed — fail clearly; failed acquire holds nothing.
            _set_audio(vid_id, "failed", err=str(ex)[:500])
            return
        try:
            dur = max(4, int(_video_duration(video)) + 1)
            _set_video_progress(vid_id, 15, "Composing music…")
            _node_audio("music", music_prompt or (row["prompt"] or "gentle background music"),
                        music_wav, duration=dur)
            if narration.strip():
                voice_wav = str(VIDEOS_DIR / f"aud_{vid_id}_voice_{ts}.wav")
                _set_video_progress(vid_id, 55, "Recording narration…")
                _node_audio("voice", narration.strip(), voice_wav)
            _set_video_progress(vid_id, 85, "Mixing audio into video…")
            out = str(VIDEOS_DIR / f"vid_{vid_id}_sound_{ts}.mp4")
            _mux_audio(video, music_wav, voice_wav, out)
            _set_audio(vid_id, "done", path=out)
            _set_video_progress(vid_id, 100, "Done")
            logger.info("Video %d sounded: %s", vid_id, out)
        except subprocess.TimeoutExpired:
            _set_audio(vid_id, "failed", err="Audio generation timed out")
        except Exception as ex:
            logger.error("Video %d add-audio failed: %s", vid_id, ex)
            _set_audio(vid_id, "failed", err=str(ex)[:500])
        finally:
            for w in (music_wav, voice_wav):
                try:
                    if w: Path(w).unlink(missing_ok=True)
                except Exception:
                    pass
            orch.video_release()


# Standalone audio engines exposed in the Music/Audio tab. (mode, default model)
AUDIO_ENGINES = {
    "musicgen":     {"kind": "music", "model": "facebook/musicgen-small",  "label": "MusicGen (instrumental, fast)"},
    "musicgen_med": {"kind": "music", "model": "facebook/musicgen-medium", "label": "MusicGen Medium (richer)"},
    "acestep":      {"kind": "music", "model": "ACE-Step/ACE-Step-v1-3.5B", "label": "ACE-Step (songs w/ vocals+lyrics)"},
    "stable_audio": {"kind": "music", "model": "stabilityai/stable-audio-open-1.0", "label": "Stable Audio Open (hi-fi)"},
    "mms_tts":      {"kind": "voice", "model": "facebook/mms-tts-eng",      "label": "Voice narration (MMS-TTS)"},
}


def run_audio_clip(clip_id: int):
    """Background task: generate a standalone music/voice clip on the node."""
    # Video-infra helpers live in services_media; import lazily to avoid an import cycle.
    from services_media import _video_preflight, _VIDEO_RUN_LOCK
    conn = get_conn()
    row = conn.execute("SELECT * FROM audio_clips WHERE id=?", (clip_id,)).fetchone()
    conn.close()
    if not row:
        return
    row = dict(row)
    engine = row["engine"] or "musicgen"
    fell_back = False
    # A gated engine with no HF token WILL GatedRepoError — fall back to public MusicGen so
    # the clip succeeds (same "music" kind) instead of failing on every attempt.
    if engine in GATED_ENGINES and not (get_setting("hf_token") or "").strip():
        logger.warning("Audio clip %d: engine '%s' is gated and no hf_token is set → using musicgen",
                       clip_id, engine)
        engine, fell_back = "musicgen", True
    eng = AUDIO_ENGINES.get(engine, AUDIO_ENGINES["musicgen"])
    mode = "voice" if eng["kind"] == "voice" else "music"
    # after a fallback the row's model_id points at the gated model, so use the engine default
    model_id = eng["model"] if fell_back else (row["model_id"] or eng["model"])

    ok, msg = _video_preflight()
    if not ok:
        _set_clip(clip_id, "failed", err=msg)
        return
    _set_clip(clip_id, "generating",
              pmsg=("Stable Audio is gated (no HF token) — using MusicGen…" if fell_back else "Loading model…"))
    ts = int(datetime.now().timestamp())
    out = str(VIDEOS_DIR / f"clip_{clip_id}_{ts}.wav")
    with _VIDEO_RUN_LOCK:
        try:
            orch.video_acquire()
        except RuntimeError as ex:
            # GPU could not be freed — fail clearly; failed acquire holds nothing.
            _set_clip(clip_id, "failed", err=str(ex)[:500])
            return
        try:
            _node_audio(mode, row["prompt"], out, duration=int(row["duration"] or 8),
                        model_id=model_id, engine=engine,
                        lyrics=(row["lyrics"] if "lyrics" in row.keys() else "") or "")
            _set_clip(clip_id, "done", path=out, pmsg="Done")
            logger.info("Audio clip %d done: %s", clip_id, out)
        except subprocess.TimeoutExpired:
            _set_clip(clip_id, "failed", err="Generation timed out")
        except Exception as ex:
            m = str(ex)
            if "GatedRepo" in m or "gated repo" in m.lower():
                m = ("Model is gated on Hugging Face — accept its license at huggingface.co "
                     "and set an HF token (Settings → hf_token), or use MusicGen.")
            logger.error("Audio clip %d failed: %s", clip_id, ex)
            _set_clip(clip_id, "failed", err=m[:500])
        finally:
            orch.video_release()


def _set_clip(clip_id: int, status: str, path: str = None, err: str = None, pmsg: str = None):
    try:
        conn = get_conn()
        conn.execute("UPDATE audio_clips SET status=?, audio_path=COALESCE(?,audio_path), "
                     "error=?, progress_msg=COALESCE(?,progress_msg), updated_at=datetime('now') WHERE id=?",
                     (status, path, err, pmsg, clip_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _set_audio(vid_id: int, status: str, path: str = None, err: str = None):
    try:
        conn = get_conn()
        conn.execute("UPDATE videos SET audio_status=?, audio_path=COALESCE(?,audio_path), "
                     "audio_error=?, updated_at=datetime('now') WHERE id=?",
                     (status, path, err, vid_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


# Export everything (incl. single-underscore helpers used across modules).
__all__ = [n for n in dir() if not n.startswith('__')]

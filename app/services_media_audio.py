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
            "-t", f"{dur:.2f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", out])
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    return out


def _video_audio_settings(row, settings: dict | None) -> dict:
    """Resolve the layered audio settings for ONE video (chain parity):
    explicit caller settings win, else the video's stored audio_settings JSON,
    else None → the caller falls back to the legacy music+voice behavior.
    Returns (settings dict based on DEFAULT_CHAIN_AUDIO, explicit?) — the
    dict always has every DEFAULT_CHAIN_AUDIO key."""
    from services_media_chain import DEFAULT_CHAIN_AUDIO
    s = dict(DEFAULT_CHAIN_AUDIO)
    src = settings if isinstance(settings, dict) else None
    if src is None:
        try:
            raw = row["audio_settings"] if "audio_settings" in row.keys() else None
            src = json.loads(raw) if raw else None
        except Exception:
            src = None
    explicit = isinstance(src, dict)
    if explicit:
        for k, v in src.items():
            if k in s and v is not None:
                s[k] = v
    return s, explicit


def add_video_audio(vid_id: int, music_prompt: str, narration: str = "",
                    settings: dict | None = None):
    """Background task: generate a layered soundtrack for a video and mux it on —
    music bed + optional TTS narration + optional SFX, with the same engine and
    per-layer volume choices the chain builder offers (settings keys mirror
    DEFAULT_CHAIN_AUDIO). No settings anywhere (legacy callers) = the old
    behavior exactly: music + voice-only-if-narration at the old fixed volumes.
    Sets videos.audio_path / audio_status / audio_error."""
    # Video-infra helpers live in services_media; import lazily to avoid an import cycle.
    from services_media import _video_preflight, _set_video_progress, _VIDEO_RUN_LOCK
    from services_media_chain import _mux_chain_audio
    conn = get_conn()
    row = conn.execute("SELECT * FROM videos WHERE id=?", (vid_id,)).fetchone()
    conn.close()
    if not row or not row["video_path"] or not Path(row["video_path"]).exists():
        _set_audio(vid_id, "failed", err="Video file not found")
        return
    video = row["video_path"]
    s, explicit = _video_audio_settings(row, settings)
    # Explicit caller args (the card form / generate payload) override saved text.
    if (music_prompt or "").strip():
        s["music_prompt"] = music_prompt.strip()
    if (narration or "").strip():
        s["narration"] = narration.strip()
    if not explicit:
        # Legacy shape: always music, voice only when narration was given, no
        # SFX — at the exact volumes the old fixed _mux_audio used.
        s["music"], s["sfx"] = True, False
        s["voice"] = bool(s["narration"])
        s["music_volume"] = 0.28 if s["voice"] else 0.6
        s["voice_volume"] = 1.0
        s["music_engine"], s["voice_engine"] = "musicgen", "mms_tts"
    if not (s["music"] or s["voice"] or s["sfx"]):
        _set_audio(vid_id, "failed", err="All audio layers are switched off")
        return
    _set_audio(vid_id, "generating")
    ok, msg = _video_preflight()
    if not ok:
        _set_audio(vid_id, "failed", err=msg)
        return

    def _engine(e):
        # Same gated-engine fallback as run_audio_clip: no HF token → MusicGen.
        if e in GATED_ENGINES and not (get_setting("hf_token") or "").strip():
            logger.warning("Video %d audio: engine '%s' is gated and no hf_token is set → using musicgen",
                           vid_id, e)
            return "musicgen"
        return e or "musicgen"

    ts = int(datetime.now().timestamp())
    music_wav = voice_wav = sfx_wav = ""
    with _VIDEO_RUN_LOCK:
        try:
            orch.video_acquire()
        except RuntimeError as ex:
            # GPU could not be freed — fail clearly; failed acquire holds nothing.
            _set_audio(vid_id, "failed", err=str(ex)[:500])
            return
        try:
            dur = max(4, int(_video_duration(video)) + 1)
            errors = []
            if s["music"]:
                _set_video_progress(vid_id, 15, "Composing music…")
                music_wav = str(VIDEOS_DIR / f"aud_{vid_id}_music_{ts}.wav")
                try:
                    _node_audio("music",
                                str(s["music_prompt"] or "").strip()
                                or (row["prompt"] or "gentle background music"),
                                music_wav, duration=dur, engine=_engine(s["music_engine"]))
                except Exception as ex:
                    errors.append(f"music: {str(ex)[:160]}")
                    music_wav = ""
            if s["voice"]:
                # Empty narration = the video's prompt is the script (the chain
                # reads each scene's prompt the same way).
                text = str(s["narration"] or "").strip() or (row["prompt"] or "").strip()
                if text:
                    voice_wav = str(VIDEOS_DIR / f"aud_{vid_id}_voice_{ts}.wav")
                    _set_video_progress(vid_id, 50, "Recording narration…")
                    try:
                        _node_audio("voice", text, voice_wav, engine=s["voice_engine"])
                    except Exception as ex:
                        errors.append(f"voice: {str(ex)[:160]}")
                        voice_wav = ""
            if s["sfx"]:
                sfx_wav = str(VIDEOS_DIR / f"aud_{vid_id}_sfx_{ts}.wav")
                _set_video_progress(vid_id, 70, "Making sound effect…")
                try:
                    _node_audio("music",
                                f"sound effect matching: {(row['prompt'] or '').strip()[:160]}, "
                                "foley, no music, no melody",
                                sfx_wav, duration=4, engine=_engine(s["music_engine"]))
                except Exception as ex:
                    errors.append(f"sfx: {str(ex)[:160]}")
                    sfx_wav = ""
            if not (music_wav or voice_wav or sfx_wav):
                _set_audio(vid_id, "failed",
                           err=("Audio failed — " + "; ".join(errors))[:400]
                           if errors else "No audio layers generated")
                return
            _set_video_progress(vid_id, 85, "Mixing audio into video…")
            out = str(VIDEOS_DIR / f"vid_{vid_id}_sound_{ts}.mp4")
            _mux_chain_audio(video, music_wav,
                             [(0.0, voice_wav)] if voice_wav else [],
                             [(0.0, sfx_wav)] if sfx_wav else [], s, out)
            _set_audio(vid_id, "done", path=out,
                       err=("partial: " + "; ".join(errors))[:400] if errors else None)
            _set_video_progress(vid_id, 100, "Done")
            logger.info("Video %d sounded: %s (music=%s voice=%s sfx=%s)",
                        vid_id, out, bool(music_wav), bool(voice_wav), bool(sfx_wav))
        except subprocess.TimeoutExpired:
            _set_audio(vid_id, "failed", err="Audio generation timed out")
        except Exception as ex:
            logger.error("Video %d add-audio failed: %s", vid_id, ex)
            _set_audio(vid_id, "failed", err=str(ex)[:500])
        finally:
            for w in (music_wav, voice_wav, sfx_wav):
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

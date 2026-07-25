"""Video-chain pipeline: generate multi-segment video chains sequentially (T2V for
segment 0, V2V continuation after), resume a partially-done chain, and xfade-compile
the segments. Split out of services_media.py for size; re-exported by it
(from services_media_chain import *)."""
from deps import *


def _chain_segment_path(chain_id: int, idx: int, ts: int) -> Path:
    return VIDEOS_DIR / f"chain_{chain_id}_seg{idx}_{ts}.mp4"

def _chain_compiled_path(chain_id: int) -> Path:
    return VIDEOS_DIR / f"chain_{chain_id}_compiled.mp4"


def _video_dims(path) -> tuple[int, int] | None:
    """(width, height) of a video's first stream via ffprobe, or None on failure."""
    import subprocess as _sp
    import re as _re
    try:
        pr = _sp.run(["ffprobe", "-v", "quiet", "-select_streams", "v:0",
                      "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
                     capture_output=True, text=True)
        m = _re.findall(r"\d+", pr.stdout or "")
        if len(m) >= 2:
            return int(m[0]), int(m[1])
    except Exception:
        pass
    return None


def _media_has_audio(path) -> bool:
    """True when ffprobe sees at least one audio stream in the file."""
    import subprocess as _sp
    try:
        pr = _sp.run(["ffprobe", "-v", "quiet", "-select_streams", "a",
                      "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                     capture_output=True, text=True, timeout=20)
        return "audio" in (pr.stdout or "")
    except Exception:
        return False


def _conform_segment(path, width: int, height: int) -> bool:
    """Uniform-segment fix: a V2V continuation can come back at the MODEL's native
    size instead of the chain's (Wan's V2V pipeline defaults to 832×480 when it
    isn't told a size), which used to leave a chain with mismatched segments that
    the compile then letterboxed differently per segment. If this segment's size
    differs from the chain target, re-encode it IN PLACE to exactly width×height
    (aspect-preserving scale + centered pad, square SAR) right after generation —
    so every stored segment is size-uniform, the next segment's V2V input is
    already at the chain size, and the compile concat never has to letterbox.
    Segments that already match are left byte-identical (no re-encode — today's
    behavior). Returns True only when the file was actually conformed; any
    failure keeps the original file (the compile-time normalization still
    catches it) and never raises."""
    import subprocess as _sp
    tmp = str(path) + ".conform.mp4"
    try:
        dims = _video_dims(path)
        if not dims or dims == (int(width), int(height)):
            return False
        vf = (f"scale={int(width)}:{int(height)}:force_original_aspect_ratio=decrease,"
              f"pad={int(width)}:{int(height)}:(ow-iw)/2:(oh-ih)/2,setsar=1")
        # crf 18: near-lossless — this only ever touches mismatched segments, and
        # they get re-encoded once more at compile time.
        # PRESERVE any audio the segment carries (this used to pass -an, which
        # silently STRIPPED audio from every conformed segment — owner bug A):
        # copy the audio stream bit-for-bit when one exists; a segment with no
        # audio stream simply gets none (no -an needed).
        audio_args = ["-c:a", "copy"] if _media_has_audio(path) else []
        _sp.run(["ffmpeg", "-y", "-i", str(path), "-vf", vf,
                 "-c:v", "libx264", "-preset", "fast", "-crf", "18"] + audio_args + [tmp],
                check=True, capture_output=True)
        os.replace(tmp, str(path))
        logger.info("Chain segment %s conformed %dx%d → %dx%d",
                    path, dims[0], dims[1], int(width), int(height))
        return True
    except Exception as ex:
        try:
            Path(tmp).unlink(missing_ok=True)
        except Exception:
            pass
        logger.warning("Segment conform failed for %s (%s) — compile will normalize instead",
                       path, ex)
        return False


def _compile_chain_video(video_paths: list[str], output: str, fps: int = 16,
                         target: tuple[int, int] | None = None) -> str:
    """Concatenate segment videos with xfade transitions using ffmpeg.
    `target` is the (width, height) every input is normalized to before the
    crossfade — the chain callers pass the chain's own size so a stray
    mismatched segment can't drag the whole compile to its dimensions; when
    omitted, the first segment's size is used (the original behavior).

    AUDIO (owner bug A): this used to map only [v], so a chain video
    structurally could not carry sound. Now, when ANY input has an audio
    stream, every input gets a normalized audio lane of EXACTLY its video's
    duration (real audio resampled+padded+trimmed; silent segments synthesized
    with anullsrc — so mixed with/without-audio chains stay in A/V sync, no
    drift), the lanes are acrossfaded with the same 0.5 s overlap as the video
    xfade (plain a=1 concat in the fallback), and the result is AAC-encoded
    alongside the video. Chains whose segments are all silent compile exactly
    as before (video-only output)."""
    import subprocess as _sp
    import shutil

    if len(video_paths) == 1:
        shutil.copy2(video_paths[0], output)   # audio (if any) rides along untouched
        return output

    fade_duration = 0.5

    # Get durations via ffprobe
    durations = []
    for vp in video_paths:
        r = _sp.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", vp],
            capture_output=True, text=True,
        )
        try:
            durations.append(float(r.stdout.strip()))
        except ValueError:
            durations.append(3.0)   # fallback if probe fails

    # Build inputs
    inputs = []
    for vp in video_paths:
        inputs.extend(["-i", vp])

    # Segments can differ in resolution / SAR / fps (e.g. a Wan V2V continuation
    # comes back at the model's native size, not the chain's — now conformed at
    # generation time by _conform_segment, but old/foreign segments can still
    # mismatch) — and xfade REQUIRES identical frames, so it used to die with
    # exit 234 on any mismatched chain. Normalize every input to the target WxH
    # (letterbox, no distortion) + square SAR + common fps before the crossfade.
    if target:
        W, H = int(target[0]), int(target[1])
    else:
        W, H = _video_dims(video_paths[0]) or (832, 480)
    n_in = len(video_paths)
    norm = [f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[n{i}]"
            for i in range(n_in)]

    # Audio lanes: one [aI] per input, each exactly its video's duration so the
    # audio timeline mirrors the video timeline segment-for-segment.
    has_audio = [_media_has_audio(vp) for vp in video_paths]
    any_audio = any(has_audio)
    afmt = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    anorm = []
    if any_audio:
        for i, d in enumerate(durations):
            if has_audio[i]:
                anorm.append(f"[{i}:a]aresample=44100:async=1:first_pts=0,{afmt},"
                             f"apad,atrim=0:{d:.3f},asetpts=PTS-STARTPTS[a{i}]")
            else:
                anorm.append(f"anullsrc=r=44100:cl=stereo,{afmt},"
                             f"atrim=0:{d:.3f},asetpts=PTS-STARTPTS[a{i}]")
    audio_out = ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"] if any_audio else []

    # xfade chain over the NORMALIZED streams (+ matching acrossfade audio lane)
    # offset[i] = sum(durations[0..i-1]) - i * fade_duration
    aparts = []
    if any_audio:
        prev_a = "[a0]"
        for i in range(1, n_in):
            out_a = "[a]" if i == n_in - 1 else f"[ax{i}]"
            aparts.append(f"{prev_a}[a{i}]acrossfade=d={fade_duration}{out_a}")
            prev_a = out_a
    if n_in == 2:
        offset = max(0.1, durations[0] - fade_duration)
        fc = ";".join(norm + anorm + [
            f"[n0][n1]xfade=transition=fade:duration={fade_duration}:offset={offset:.3f}[v]"]
            + aparts)
    else:
        parts = list(norm) + list(anorm)
        prev = "[n0]"
        for i in range(1, n_in):
            offset = max(0.1, sum(durations[:i]) - i * fade_duration)
            out = "[v]" if i == n_in - 1 else f"[x{i}]"
            parts.append(f"{prev}[n{i}]xfade=transition=fade:duration={fade_duration}:offset={offset:.3f}{out}")
            prev = out
        fc = ";".join(parts + aparts)
    map_arg = "[v]"

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", fc,
        "-map", map_arg] + audio_out + [
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        output
    ]
    try:
        _sp.run(cmd, check=True, capture_output=True)
        return output
    except _sp.CalledProcessError as e:
        # xfade is finicky (size/SAR/fps/duration/offset). Fall back to a plain
        # normalized concat so a chain ALWAYS merges into a single file instead of
        # leaving unmerged segment parts. Same normalization, no crossfade.
        _tail = (e.stderr[-400:].decode("utf-8", "ignore") if isinstance(e.stderr, bytes)
                 else str(e.stderr or "")[-400:])
        logger.warning("chain xfade compile failed (rc=%s) — falling back to concat: %s",
                       e.returncode, _tail)
        if any_audio:
            # v+a interleaved concat: each [aI] lane is already padded/trimmed to
            # its video's length, so sync is inherent.
            concat_fc = ";".join(norm + anorm + [
                "".join(f"[n{i}][a{i}]" for i in range(n_in))
                + f"concat=n={n_in}:v=1:a=1[v][a]"])
        else:
            concat_fc = ";".join(norm + [
                "".join(f"[n{i}]" for i in range(n_in))
                + f"concat=n={n_in}:v=1:a=0[v]"])
        _sp.run(["ffmpeg", "-y"] + inputs + [
            "-filter_complex", concat_fc, "-map", "[v]"] + audio_out + [
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", output,
        ], check=True, capture_output=True)
        return output

def chain_resume_point(chain_id: int) -> tuple[int, str | None]:
    """Where a partially-completed chain should pick back up: (start_idx, prev_path).
    start_idx is the first segment NOT done; prev_path is the last done segment's
    video for V2V continuity (None when resuming from segment 0)."""
    conn = get_conn()
    last = conn.execute(
        "SELECT chain_index, video_path FROM videos "
        "WHERE chain_id=? AND status='done' AND video_path IS NOT NULL "
        "ORDER BY chain_index DESC LIMIT 1", (chain_id,)).fetchone()
    conn.close()
    if not last:
        return 0, None
    return int(last["chain_index"]) + 1, last["video_path"]


def resume_chain_generation(chain_id: int):
    """Continue a failed/interrupted chain from its last completed segment instead
    of starting over (used by the gpu-guard after a game/heavy-app pause killed the
    in-flight segment). Done segments are kept; the killed segment's row is removed
    so the redo doesn't leave a dangling 'failed' card in the gallery."""
    start_idx, prev_path = chain_resume_point(chain_id)
    conn = get_conn()
    conn.execute("DELETE FROM videos WHERE chain_id=? AND status IN ('failed','generating')",
                 (chain_id,))
    # completed_segments tracks reality (rows may have been pruned/failed oddly)
    conn.execute("UPDATE video_chains SET completed_segments=?,updated_at=datetime('now') "
                 "WHERE id=?", (start_idx, chain_id))
    conn.commit()
    conn.close()
    logger.info("Chain %d resuming at segment %d (prev=%s)", chain_id, start_idx, prev_path)
    run_chain_generation(chain_id, _start_idx=start_idx, _prev_video_path=prev_path)


def run_chain_generation(chain_id: int, _start_idx: int = 0,
                         _prev_video_path: str | None = None):
    """Background task: generate all segments of a video chain sequentially.
    `_start_idx`/`_prev_video_path` are for resume_chain_generation — segments
    below _start_idx are skipped and the first generated one continues (V2V)
    from _prev_video_path instead of a fresh T2V."""
    # Video-infra helpers live in services_media; import lazily to avoid an import cycle.
    from services_media import (_video_preflight, _video_timeout, _run_gen,
                                _set_video_progress, _video_guidance_args)
    conn = get_conn()
    chain = conn.execute("SELECT * FROM video_chains WHERE id=?", (chain_id,)).fetchone()
    if not chain:
        conn.close()
        return

    prompts    = json.loads(chain["prompts"])
    # Director scenes carry per-segment lengths (frames_json = JSON list of
    # num_frames, one per segment); NULL/absent = uniform num_frames as always.
    try:
        frames_list = [int(x) for x in json.loads(chain["frames_json"])] \
            if ("frames_json" in chain.keys() and chain["frames_json"]) else None
    except Exception:
        frames_list = None
    model_id   = chain["model_id"] or "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
    width      = chain["width"]  or 832
    height     = chain["height"] or 480
    num_frames = chain["num_frames"] or 49
    steps      = chain["steps"]  or 20
    fps        = chain["fps"]    or 16
    strength   = chain["strength"] if chain["strength"] is not None else 0.7
    # Segments must inherit the chain's nsfw flag — without this an NSFW chain's
    # per-segment videos rows land nsfw=0 and leak into GET /api/videos.
    chain_nsfw = chain["nsfw"] or 0

    conn.close()

    # Preflight once for the whole chain — don't queue N doomed segments.
    ok, msg = _video_preflight()
    if not ok:
        c = get_conn()
        c.execute("UPDATE video_chains SET status='failed',error=?,updated_at=datetime('now') WHERE id=?", (msg[:400], chain_id))
        c.commit(); c.close()
        logger.error("Chain %d preflight failed: %s", chain_id, msg)
        return

    conn = get_conn()
    conn.execute(
        "UPDATE video_chains SET status='generating',error=NULL,updated_at=datetime('now') WHERE id=?",
        (chain_id,)
    )
    conn.commit()
    conn.close()

    tmo = _video_timeout(model_id)
    # Per-model guidance/CFG rides as an optional trailing generator arg (empty
    # list for off-catalog models → old command line; older script copies ignore
    # the extra positional). Resolved once — a chain renders with one setting.
    guidance_args = _video_guidance_args(model_id)
    prev_video_path: str | None = _prev_video_path
    segment_video_ids: list[int] = []

    # Hold GPU exclusivity for the ENTIRE chain, not just per segment. Without
    # this, _active_images drops to 0 between segments and the orchestrator's
    # LLM worker can start a task whose LM Studio call loads an ~8 GB model onto
    # the node GPU mid-chain — the next segment then OOMs at VAE-decode time.
    # video_exclusive() releases the hold in a finally, so a failed/raising
    # segment can never wedge the LLM queue. Passing model_id lets the unified
    # queue batch this chain by model affinity (and drain resident-LLM work first).
    with orch.video_exclusive(model=model_id, desc=f"Video chain {chain_id}"):
        for idx, prompt in enumerate(prompts):
            if idx < _start_idx:
                continue   # resume: this segment already completed before the interrupt
            try:
                # frees ComfyUI + LLM VRAM and VERIFIES it via nvidia-smi;
                # T5-XXL text encoder alone is ~9.5 GB. (Nested inside
                # video_exclusive → the unified-queue gate is skipped; this
                # thread already owns the GPU for the whole chain.)
                orch.video_acquire(model=model_id)
            except RuntimeError as ex:
                # GPU could not be freed (offending process is in the message) —
                # fail the chain clearly instead of launching into a certain OOM.
                # A failed acquire holds nothing: no video_release() here.
                logger.error("Chain %d seg %d GPU acquire failed: %s", chain_id, idx, ex)
                conn = get_conn()
                conn.execute(
                    "UPDATE video_chains SET status='failed',error=?,updated_at=datetime('now') WHERE id=?",
                    (f"Segment {idx+1}: {str(ex)[:350]}", chain_id)
                )
                conn.commit()
                conn.close()
                return
            conn = get_conn()
            seed = random.randint(1, 2**31 - 1)
            ts   = int(datetime.now().timestamp())
            out_path = _chain_segment_path(chain_id, idx, ts)
            # the ONE point where a segment's length is decided (per-shot lengths)
            seg_frames = frames_list[idx] if frames_list and idx < len(frames_list) else num_frames

            # Insert a video row for this segment
            cur = conn.execute(
                "INSERT INTO videos (prompt,width,height,num_frames,steps,fps,seed,status,model_id,chain_id,chain_index,nsfw) "
                "VALUES (?,?,?,?,?,?,?,'generating',?,?,?,?)",
                (prompt, width, height, seg_frames, steps, fps, seed, model_id, chain_id, idx, chain_nsfw),
            )
            vid_id = cur.lastrowid
            conn.commit()
            conn.close()

            try:
                _cb = lambda p, m, _v=vid_id: _set_video_progress(_v, p, m)
                if idx == 0 or prev_video_path is None:
                    # Segment 0: standard T2V
                    result = _run_gen(
                        [str(VIDEO_GEN_SCRIPT), prompt, str(out_path),
                         str(width), str(height), str(seg_frames), str(steps),
                         str(seed), str(fps), model_id] + guidance_args,
                        timeout=tmo, vid_id=vid_id, on_progress=_cb, steps=int(steps),
                    )
                else:
                    # Subsequent segments: V2V continuation from previous segment
                    result = _run_gen(
                        [str(VIDEO_CONT_SCRIPT), prompt, prev_video_path, str(out_path),
                         str(width), str(height), str(seg_frames), str(steps),
                         str(seed), str(fps), model_id, str(strength)] + guidance_args,
                        timeout=tmo, vid_id=vid_id, on_progress=_cb, steps=int(steps),
                    )

                if result.returncode == 0 and out_path.exists():
                    # Uniform-segment fix: force this segment to the chain's WxH
                    # before it's stored / fed to the next V2V — no-op when it
                    # already matches, never raises.
                    _conform_segment(out_path, int(width), int(height))
                    conn = get_conn()
                    conn.execute(
                        "UPDATE videos SET status='done',video_path=?,seed=?,progress=100,progress_msg='Done',updated_at=datetime('now') WHERE id=?",
                        (str(out_path), seed, vid_id)
                    )
                    conn.execute(
                        "UPDATE video_chains SET completed_segments=completed_segments+1,"
                        "updated_at=datetime('now') WHERE id=?", (chain_id,)
                    )
                    conn.commit()
                    conn.close()
                    prev_video_path = str(out_path)
                    segment_video_ids.append(vid_id)
                    logger.info("Chain %d seg %d done: %s", chain_id, idx, out_path)
                else:
                    err = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()[-800:] or "unknown"
                    logger.error("Chain %d seg %d failed (rc=%d): %s", chain_id, idx, result.returncode, err)
                    conn = get_conn()
                    conn.execute("UPDATE videos SET status='failed',error=? WHERE id=?", (err, vid_id))
                    conn.execute(
                        "UPDATE video_chains SET status='failed',error=?,updated_at=datetime('now') WHERE id=?",
                        (f"Segment {idx+1} failed: {err[:200]}", chain_id)
                    )
                    conn.commit()
                    conn.close()
                    return   # finally releases the GPU (was double-released here before)

            except subprocess.TimeoutExpired:
                logger.error("Chain %d seg %d timed out", chain_id, idx)
                conn = get_conn()
                conn.execute("UPDATE videos SET status='failed',error=? WHERE id=?",
                             (f"Timed out after {tmo//60} min", vid_id))
                conn.execute(
                    "UPDATE video_chains SET status='failed',error=?,updated_at=datetime('now') WHERE id=?",
                    (f"Segment {idx+1} timed out", chain_id)
                )
                conn.commit()
                conn.close()
                return   # finally releases the GPU
            except Exception as ex:
                logger.error("Chain %d seg %d exception: %s", chain_id, idx, ex)
                conn = get_conn()
                conn.execute("UPDATE videos SET status='failed',error=? WHERE id=?", (str(ex)[:800], vid_id))
                conn.execute(
                    "UPDATE video_chains SET status='failed',error=?,updated_at=datetime('now') WHERE id=?",
                    (str(ex)[:200], chain_id)
                )
                conn.commit()
                conn.close()
                return   # finally releases the GPU
            finally:
                orch.video_release()

    # All segments done — mark chain done, then AUTO-COMPILE into one video.
    conn = get_conn()
    conn.execute(
        "UPDATE video_chains SET status='done',updated_at=datetime('now') WHERE id=?", (chain_id,)
    )
    chain = conn.execute("SELECT fps FROM video_chains WHERE id=?", (chain_id,)).fetchone()
    segs = conn.execute(
        "SELECT video_path FROM videos WHERE chain_id=? AND status='done' ORDER BY chain_index",
        (chain_id,)
    ).fetchall()
    conn.commit()
    conn.close()
    logger.info("Chain %d complete — %d segments; compiling…", chain_id, len(prompts))

    # A chain must always yield ONE playable video, not loose segments. Compilation
    # used to be a separate manual endpoint that nothing called, so chains "finished"
    # as unmerged parts. xfade-compile here (CPU-only ffmpeg, GPU already released).
    paths = [s["video_path"] for s in segs if s["video_path"]]
    chain_fps = (chain["fps"] if chain else None) or 16
    if paths:
        out = str(_chain_compiled_path(chain_id))
        try:
            # Normalize to the CHAIN's size, not segment 0's — with the conform
            # step above these match, but old chains' segments may not.
            _compile_chain_video(paths, out, fps=chain_fps,
                                 target=(int(width), int(height)))
            c = get_conn()
            c.execute(
                "UPDATE video_chains SET compiled_path=?,updated_at=datetime('now') WHERE id=?",
                (out, chain_id)
            )
            c.commit()
            c.close()
            logger.info("Chain %d compiled → %s", chain_id, out)
        except Exception as ex:
            logger.error("Chain %d auto-compile failed: %s", chain_id, ex)
            return
        # Opt-in layered audio (music / narration / SFX) — default OFF, so plain
        # chains behave exactly as before. Runs AFTER the video_exclusive hold is
        # released; each generated clip re-acquires the GPU through the unified
        # queue (run_audio_clip). A failed audio pass never fails the chain — the
        # silent compiled video stays valid and audio_status/audio_error report it.
        try:
            render_chain_audio(chain_id)
        except Exception as ex:
            logger.error("Chain %d audio pass crashed: %s", chain_id, ex)
            try:
                _set_chain_audio(chain_id, "failed", err=str(ex)[:400])
            except Exception:
                pass


# ═══ Chain layered audio (owner bug A) ═══════════════════════════════════════
#
# Wires the EXISTING layered audio system (the studio-project engines:
# audio_clips rows + run_audio_clip → _VIDEO_RUN_LOCK + orch.video_acquire on
# the unified GPU queue) to video CHAINS. Layers:
#   music — ONE bed for the whole chain (settings prompt, else concept/title)
#   voice — TTS narration: one whole-chain read of settings["narration"] when
#           given, else ONE line PER SEGMENT (the segment's prompt is the
#           chain's script) placed at that segment's compiled-timeline offset
#   sfx   — optional short per-segment effect clips at each segment's offset
# The mixed result muxes onto compiled_path → video_chains.final_path.
# Everything is opt-in per chain (audio_enabled, default OFF); the studio
# project path (render_audio/_mux_audio) is untouched.

DEFAULT_CHAIN_AUDIO = {
    "music": True, "voice": True, "sfx": False,
    "music_volume": 0.28, "voice_volume": 1.0, "sfx_volume": 0.6,
    "music_engine": "musicgen", "voice_engine": "mms_tts",
    "music_prompt": "", "narration": "",
}


def _chain_audio_settings(chain) -> dict:
    """Owner settings (video_chains.audio_settings JSON) over the defaults.
    Unknown keys are ignored; a missing/broken JSON yields pure defaults."""
    s = dict(DEFAULT_CHAIN_AUDIO)
    try:
        raw = chain["audio_settings"] if "audio_settings" in chain.keys() else None
        if raw:
            for k, v in (json.loads(raw) or {}).items():
                if k in s and v is not None:
                    s[k] = v
    except Exception:
        pass
    return s


def _set_chain_audio(chain_id: int, status: str, err: str | None = None,
                     final: str | None = None):
    conn = get_conn()
    conn.execute("UPDATE video_chains SET audio_status=?,audio_error=?,"
                 "final_path=COALESCE(?,final_path),updated_at=datetime('now') WHERE id=?",
                 (status, err, final, chain_id))
    conn.commit()
    conn.close()


def _chain_timeline(chain_id: int) -> list[tuple[float, float]]:
    """(offset_s, duration_s) of each done segment on the COMPILED timeline —
    the same 0.5 s xfade-overlap arithmetic _compile_chain_video uses, so
    per-segment voice/SFX cues land where their segment actually plays."""
    from services_media_audio import _video_duration
    conn = get_conn()
    segs = conn.execute(
        "SELECT video_path FROM videos WHERE chain_id=? AND status='done' "
        "AND video_path IS NOT NULL ORDER BY chain_index", (chain_id,)).fetchall()
    conn.close()
    out, t = [], 0.0
    for i, s in enumerate(segs):
        d = _video_duration(s["video_path"]) or 3.0
        off = max(0.0, t - (0.5 if i else 0.0))
        out.append((off, d))
        t = off + d
    return out


def _chain_make_clip(chain_id: int, engine: str, prompt: str, duration: int,
                     nsfw: int, errors: list) -> str:
    """One audio_clips row (tagged chain_id, so it stays out of the Audio-tab
    gallery) run through the UNMODIFIED run_audio_clip — same shape as the
    studio's _make_clip. Returns the wav path, or '' with the error collected."""
    from services_media_audio import run_audio_clip, AUDIO_ENGINES
    kind = AUDIO_ENGINES.get(engine, AUDIO_ENGINES["musicgen"])["kind"]
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO audio_clips (kind,prompt,engine,duration,status,nsfw,chain_id) "
        "VALUES (?,?,?,?,'queued',?,?)",
        (kind, (prompt or "").strip()[:800], engine, int(duration), nsfw, chain_id))
    clip_id = cur.lastrowid
    conn.commit()
    conn.close()
    run_audio_clip(clip_id)      # blocking: _VIDEO_RUN_LOCK + orch.video_acquire
    conn = get_conn()
    clip = conn.execute("SELECT status,audio_path,error FROM audio_clips WHERE id=?",
                        (clip_id,)).fetchone()
    conn.close()
    if clip and clip["status"] == "done" and clip["audio_path"] \
            and Path(clip["audio_path"]).exists():
        return clip["audio_path"]
    errors.append(f"{kind}: {((clip['error'] if clip else '') or 'generation failed')[:160]}")
    return ""


def _mux_chain_audio(video: str, music_wav: str, voice_wavs: list, sfx_wavs: list,
                     s: dict, out: str):
    """Mix the generated layers and mux them onto the compiled chain video —
    the _mux_audio shape (video is the master clock, -t clamps, -c:v copy),
    generalized to N inputs: looped music bed at music_volume + each voice/SFX
    clip adelay-placed at its segment's timeline offset, amixed (no
    normalization so the set volumes hold), apad so the audio lane can never
    end before the video."""
    from services_media_audio import _video_duration
    dur = _video_duration(video) or 5.0
    inputs = ["-i", video]
    parts, mix = [], []
    idx = 1
    if music_wav:
        inputs += ["-stream_loop", "-1", "-i", music_wav]
        parts.append(f"[{idx}:a]volume={float(s['music_volume']):.3f}[bg]")
        mix.append("[bg]")
        idx += 1
    for n, (off, w) in enumerate(voice_wavs):
        inputs += ["-i", w]
        ms = max(0, int(round(float(off) * 1000)))
        parts.append(f"[{idx}:a]adelay={ms}:all=1,volume={float(s['voice_volume']):.3f}[vo{n}]")
        mix.append(f"[vo{n}]")
        idx += 1
    for n, (off, w) in enumerate(sfx_wavs):
        inputs += ["-i", w]
        ms = max(0, int(round(float(off) * 1000)))
        parts.append(f"[{idx}:a]adelay={ms}:all=1,volume={float(s['sfx_volume']):.3f}[fx{n}]")
        mix.append(f"[fx{n}]")
        idx += 1
    if not mix:
        raise ValueError("no audio layers to mix")
    fc = ";".join(parts + [
        "".join(mix) + f"amix=inputs={len(mix)}:duration=longest:normalize=0,apad[a]"])
    subprocess.run(
        ["ffmpeg", "-y"] + inputs +
        ["-filter_complex", fc, "-map", "0:v", "-map", "[a]",
         "-t", f"{dur:.2f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", out],
        check=True, capture_output=True, timeout=300)


def render_chain_audio(chain_id: int):
    """Generate + mix the opt-in layered audio for a compiled chain (see the
    section comment above). No-ops unless the chain has audio_enabled=1. Sets
    audio_status queued→generating→done/failed and final_path on success; the
    compiled (silent) video is never touched, so a failed audio pass degrades
    to today's behavior instead of breaking the chain."""
    from services_media_audio import _video_duration
    conn = get_conn()
    chain = conn.execute("SELECT * FROM video_chains WHERE id=?", (chain_id,)).fetchone()
    conn.close()
    if not chain:
        return
    cols = chain.keys()
    if "audio_enabled" not in cols or not chain["audio_enabled"]:
        return
    video = (chain["compiled_path"] or "") if "compiled_path" in cols else ""
    if not video or not Path(video).exists():
        _set_chain_audio(chain_id, "failed", err="No compiled video to add audio to")
        return
    s = _chain_audio_settings(chain)
    if not (s["music"] or s["voice"] or s["sfx"]):
        _set_chain_audio(chain_id, "failed", err="All audio layers are switched off")
        return
    _set_chain_audio(chain_id, "generating")
    nsfw = chain["nsfw"] or 0
    total = _video_duration(video) or 5.0
    timeline = _chain_timeline(chain_id)
    try:
        prompts = json.loads(chain["prompts"] or "[]")
    except Exception:
        prompts = []
    errors: list[str] = []

    music_wav = ""
    if s["music"]:
        mp = str(s["music_prompt"] or "").strip() or (
            f"{((chain['concept'] or chain['title'] or 'cinematic'))[:200]}, "
            "background music, instrumental")
        dur = int(min(max(total + 1, 8), 60))   # short bed is fine — the mixer loops it
        music_wav = _chain_make_clip(chain_id, s["music_engine"], mp, dur, nsfw, errors)

    voice_wavs: list[tuple[float, str]] = []
    if s["voice"]:
        narration = str(s["narration"] or "").strip()
        if narration:
            lines = [(0.0, narration)]           # one whole-chain read (studio shape)
        else:
            # per-SECTION narration: each segment's prompt is its script line,
            # spoken at that segment's offset on the compiled timeline
            lines = [((timeline[i][0] if i < len(timeline) else 0.0), p)
                     for i, p in enumerate(prompts) if (p or "").strip()]
        for off, text in lines:
            w = _chain_make_clip(chain_id, s["voice_engine"], text, 8, nsfw, errors)
            if w:
                voice_wavs.append((off, w))

    sfx_wavs: list[tuple[float, str]] = []
    if s["sfx"]:
        for i, p in enumerate(prompts):
            if not (p or "").strip():
                continue
            off = timeline[i][0] if i < len(timeline) else 0.0
            w = _chain_make_clip(
                chain_id, s["music_engine"],
                f"sound effect matching: {p.strip()[:160]}, foley, no music, no melody",
                4, nsfw, errors)
            if w:
                sfx_wavs.append((off, w))

    if not music_wav and not voice_wavs and not sfx_wavs:
        _set_chain_audio(chain_id, "failed",
                         err=("Audio failed — " + "; ".join(errors))[:400]
                         if errors else "No audio layers generated")
        return

    out = str(VIDEOS_DIR / f"chain_{chain_id}_final.mp4")
    try:
        _mux_chain_audio(video, music_wav, voice_wavs, sfx_wavs, s, out)
        if not Path(out).exists() or Path(out).stat().st_size < 1024 \
                or not _media_has_audio(out):
            raise RuntimeError("mix produced a broken/silent final mp4")
        note = ("partial: " + "; ".join(errors))[:400] if errors else None
        _set_chain_audio(chain_id, "done", err=note, final=out)
        logger.info("Chain %d audio done → %s (music=%s voice=%d sfx=%d)",
                    chain_id, out, bool(music_wav), len(voice_wavs), len(sfx_wavs))
    except subprocess.CalledProcessError as ex:
        Path(out).unlink(missing_ok=True)
        tail = (ex.stderr[-300:].decode("utf-8", "ignore") if isinstance(ex.stderr, bytes)
                else str(ex.stderr or "")[-300:])
        _set_chain_audio(chain_id, "failed", err=f"Audio mix failed: {tail}"[:400])
        logger.error("Chain %d audio mix failed: %s", chain_id, tail)
    except Exception as ex:
        Path(out).unlink(missing_ok=True)
        _set_chain_audio(chain_id, "failed", err=f"Audio mix failed: {str(ex)[:300]}")
        logger.error("Chain %d audio mix failed: %s", chain_id, ex)


# Export everything (incl. single-underscore helpers used across modules).
__all__ = [n for n in dir() if not n.startswith('__')]

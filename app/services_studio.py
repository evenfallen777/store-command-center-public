"""AI Video Studio ("Director") — storyboard worker (Phase 1) + render slice (Phase 2).

docs/VIDEO-STUDIO-DESIGN.md §2. One dropped idea → one studio_projects row →
ONE LLM call (through orch.submit_llm, task="studio_storyboard" so the NSFW
model routing + per-prompt picker apply) → hard server-side validation/clamping
(§2.3, never trust the model, NO auto-retry loops) → scene/shot/cue rows.

Phase 2 (§3/§4/§8): the smallest end-to-end slice that actually PRODUCES a
video. render_scene() turns a scene's shots into ONE video_chains row and runs
it through the UNMODIFIED chain engine (run_chain_generation → orch.video_
exclusive, per-shot lengths via the new frames_json column); render_project()
renders scenes sequentially; render_audio() generates a music bed + ONE
whole-script voiceover via the unmodified run_audio_clip (orch.video_acquire);
assemble_project() stitches scene clips (_compile_chain_video) and mixes the
audio on with the existing _mux_audio ffmpeg path → project.final_path.
Every GPU touch rides the unified queue; assembly/mix is CPU-only ffmpeg.
Phase 3 (deferred): per-scene VO adelay timeline, SFX cues, full amix +
sidechain ducking, atempo reconciliation, burned captions, mid-chain uploads.
"""
import json
import logging
import re
import shutil
import subprocess
import threading
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from db import get_conn
from orchestrator import orch

logger = logging.getLogger("store")

# ── in-process worker registry ───────────────────────────────────────────────
# Project statuses like 'rendering'/'mixing' are only meaningful while THIS
# process has a background thread working the project. The store gets restarted
# (deploys, the social agent, crashes) and those threads die, stranding rows in
# busy statuses forever — and _require_idle would then 409 every retry. The
# registry lets the routers tell "actually busy" from "stale busy" (re-entrant:
# produce_project nests the per-stage functions, hence a Counter, not a set).
_ACTIVE_LOCK = threading.Lock()
_ACTIVE: Counter = Counter()


@contextmanager
def _working(project_id: int):
    with _ACTIVE_LOCK:
        _ACTIVE[project_id] += 1
    try:
        yield
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE[project_id] -= 1
            if _ACTIVE[project_id] <= 0:
                del _ACTIVE[project_id]


def project_active(project_id: int) -> bool:
    """True while a background task in THIS process is working the project."""
    with _ACTIVE_LOCK:
        return _ACTIVE.get(project_id, 0) > 0

# seconds → num_frames at 16 fps — the exact table tab-videos.js exposes.
_SNAP = [(1.5, 25), (3.0, 49), (5.0, 81), (7.5, 121)]


def _snap_seconds(val) -> tuple[float, int]:
    """Snap a requested shot length to the nearest allowed (seconds, num_frames)."""
    try:
        s = float(val)
    except (TypeError, ValueError):
        s = 3.0
    return min(_SNAP, key=lambda p: abs(p[0] - s))


def _repair_brackets(s: str) -> str:
    """Deterministic bracket healer for model JSON: walk the text (string-aware,
    honoring escapes) and replace any closing bracket that mismatches the open
    stack with the expected one (']' where '}' was needed and vice versa — e.g.
    the observed '…"sfx":[{…}]],"music"' sin), drop stray closers, close an
    unterminated string, and close whatever is still open at EOF. Identity on
    well-formed JSON."""
    out, stack = [], []
    in_str = esc = False
    for ch in s:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
        elif ch in "[{":
            stack.append(ch)
            out.append(ch)
        elif ch in "]}":
            if not stack:
                continue                       # stray closer: drop it
            out.append("]" if stack.pop() == "[" else "}")
        else:
            out.append(ch)
    if in_str:
        out.append('"')
    while stack:
        out.append("]" if stack.pop() == "[" else "}")
    return "".join(out)


def _extract_json(raw: str) -> dict:
    """First {...} block of the model output (chain-prompts pattern) → dict.
    Local models wrap JSON in prose/fences and commit small syntax sins, so a
    few DETERMINISTIC repairs are tried (fences stripped, trailing commas
    removed, raw_decode ignoring trailing junk) — but this is parsing, not
    retrying: on failure it raises ValueError with the raw tail and the caller
    fails the project loudly. Deliberately NO LLM retry loop (GPU is expensive)."""
    txt = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
    txt = re.sub(r"```(?:json)?", "", txt)          # markdown fences
    m = re.search(r"\{.*\}", txt, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object in model output: …{(raw or '')[-300:]}")
    cand = m.group()
    # Missing close-quote before a known schema key — the local model's most
    # common sin ('"prompt":"rain patter, at_shot":0' for '"prompt":"rain
    # patter", "at_shot":0'). Safe: valid JSON always has an OPENING quote
    # after the comma, so this pattern can only match the broken form.
    _keys = (r"title|logline|style|scenes|summary|voiceover|caption|shots|"
             r"video_prompt|seconds|sfx|prompt|at_shot|offset_s|music|mood|hashtags")
    fix_q = lambda s: re.sub(rf',\s*({_keys})"\s*:', r'", "\1":', s)
    fix_c = lambda s: re.sub(r",\s*([}\]])", r"\1", s)   # trailing commas
    # Two observed bracket sins right before the top-level music key (the model
    # bungles closing the scenes array): '…}]},{"sfx":[…]},"music":' (missing ])
    # and '…"sfx":[…]],"music":' (]] where }] was meant). Attempt-ordered:
    # correct JSON parses on attempt 0 and never reaches these; on JSON where
    # the shape doesn't occur the regexes don't match / the attempt just fails.
    fix_m = lambda s: re.sub(r'\}\s*,\s*"music"\s*:', '}],"music":', s, count=1)
    fix_b = lambda s: re.sub(r'\]\s*\]\s*,\s*"music"\s*:', ']}],"music":', s, count=1)
    fq = fix_q(cand)
    rb = _repair_brackets(fq)
    attempts = []
    for base in (cand, fq, fix_m(fq), fix_b(fq), rb, fix_m(rb)):
        for att in (base, fix_c(base)):
            if att not in attempts:
                attempts.append(att)
    err = None
    for att in attempts:
        try:
            data = json.loads(att)
            break
        except Exception as ex:
            err = ex
            data = None
    if data is None:
        # last resort: parse the FIRST complete object, ignoring trailing junk
        try:
            data = json.JSONDecoder().raw_decode(txt[txt.find("{"):])[0]
        except Exception:
            raise ValueError(f"model JSON did not parse ({err}): …{(raw or '')[-300:]}")
    if not isinstance(data, dict):
        raise ValueError("model returned JSON that is not an object")
    return data


def _as_text(v) -> str:
    """Model fields that should be strings sometimes arrive as lists (e.g.
    hashtags as ["#a","#b"]) — join instead of str()-ing the repr."""
    if isinstance(v, (list, tuple)):
        return " ".join(str(x).strip() for x in v if str(x).strip())
    return str(v or "").strip()


def _validate_storyboard(data: dict, kind: str, style_hint: str) -> dict:
    """§2.3 hard clamp: scene/shot counts, seconds snapping, prompt back-fill,
    music fallback. Also runs the UNCONDITIONAL safety floor over every
    model-authored video_prompt/voiceover — a hit hard-fails the storyboard
    regardless of any toggle. Returns the normalized structure to persist."""
    import nsfw

    style = _as_text(data.get("style")) or str(style_hint or "").strip()
    scenes_in = data.get("scenes")
    if not isinstance(scenes_in, list) or not scenes_in:
        raise ValueError("storyboard has no scenes")
    scenes_in = [s for s in scenes_in if isinstance(s, dict)]
    if not scenes_in:
        raise ValueError("storyboard has no usable scenes")
    # SHORT → exactly 1 scene (take first); LONG → clamp to at most 6.
    scenes_in = scenes_in[:1] if kind == "short" else scenes_in[:6]

    scenes = []
    for s in scenes_in:
        summary = _as_text(s.get("summary"))
        shots_in = [sh for sh in (s.get("shots") or []) if isinstance(sh, dict)][:5]
        if not shots_in:
            # clamp shots to a minimum of 1: synthesize one from the scene itself
            shots_in = [{"video_prompt": "", "seconds": 3, "caption": ""}]
        shots = []
        for sh in shots_in:
            prompt = _as_text(sh.get("video_prompt"))
            if len(prompt) < 20:   # too thin to render — prepend scene summary + style
                prompt = " ".join(p for p in (prompt, summary, style) if p).strip()
            secs, frames = _snap_seconds(sh.get("seconds"))
            shots.append({"video_prompt": prompt, "seconds": secs,
                          "num_frames": frames, "caption": _as_text(sh.get("caption"))})
        sfx = []
        for fx in [f for f in (s.get("sfx") or []) if isinstance(f, dict)][:2]:
            fx_prompt = _as_text(fx.get("prompt"))
            if not fx_prompt:
                continue
            try:
                at_shot = max(0, min(int(fx.get("at_shot") or 0), len(shots) - 1))
            except (TypeError, ValueError):
                at_shot = 0
            try:
                off = max(0.0, float(fx.get("offset_s") or 0))
            except (TypeError, ValueError):
                off = 0.0
            # offset within the SCENE = lengths of the shots before at_shot + offset
            scene_off = sum(sh["seconds"] for sh in shots[:at_shot]) + off
            sfx.append({"prompt": fx_prompt, "offset_s": round(scene_off, 2)})
        scenes.append({
            "title":     _as_text(s.get("title")),
            "summary":   summary,
            "voiceover": _as_text(s.get("voiceover")),
            "caption":   _as_text(s.get("caption")),
            "shots":     shots,
            "sfx":       sfx,
            "est_seconds": round(sum(sh["seconds"] for sh in shots), 2),
        })

    # ── safety floor (unconditional — mirrors the Private Studio's output screen)
    for sc in scenes:
        reason = nsfw.safety_check(sc["summary"], sc["voiceover"],
                                   *[sh["video_prompt"] for sh in sc["shots"]])
        if reason:
            raise ValueError(f"safety floor refused the storyboard: {reason}")

    music = data.get("music") if isinstance(data.get("music"), dict) else {}
    music_prompt = _as_text(music.get("prompt")) or \
        f"{style or 'cinematic'} background music, instrumental"
    return {
        "title":    _as_text(data.get("title")),
        "logline":  _as_text(data.get("logline")),
        "style":    style,
        "scenes":   scenes,
        "music":    {"prompt": music_prompt, "mood": _as_text(music.get("mood"))},
        "caption":  _as_text(data.get("caption")),
        "hashtags": _as_text(data.get("hashtags")),
    }


def _wipe_storyboard_rows(conn, project_id: int):
    """Delete a project's draft scenes/shots/cues (re-storyboard keeps settings)."""
    scene_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM studio_scenes WHERE project_id=?", (project_id,)).fetchall()]
    if scene_ids:
        qs = ",".join("?" * len(scene_ids))
        conn.execute(f"DELETE FROM studio_shots WHERE scene_id IN ({qs})", scene_ids)
    conn.execute("DELETE FROM studio_cues WHERE project_id=?", (project_id,))
    conn.execute("DELETE FROM studio_scenes WHERE project_id=?", (project_id,))


def _write_storyboard(project_id: int, norm: dict, proj: dict):
    """Persist the validated storyboard: scene/shot/cue rows + project-level text.
    Replaces any previous draft rows (re-storyboard path)."""
    conn = get_conn()
    try:
        _wipe_storyboard_rows(conn, project_id)
        for i, sc in enumerate(norm["scenes"]):
            cur = conn.execute(
                "INSERT INTO studio_scenes (project_id,idx,title,summary,voiceover,"
                "caption,est_seconds) VALUES (?,?,?,?,?,?,?)",
                (project_id, i, sc["title"], sc["summary"], sc["voiceover"],
                 sc["caption"], sc["est_seconds"]))
            scene_id = cur.lastrowid
            for j, sh in enumerate(sc["shots"]):
                conn.execute(
                    "INSERT INTO studio_shots (scene_id,idx,video_prompt,seconds,"
                    "num_frames,caption) VALUES (?,?,?,?,?,?)",
                    (scene_id, j, sh["video_prompt"], sh["seconds"],
                     sh["num_frames"], sh["caption"]))
            if sc["voiceover"]:
                conn.execute(
                    "INSERT INTO studio_cues (project_id,scene_id,kind,text,engine) "
                    "VALUES (?,?,'voiceover',?,?)",
                    (project_id, scene_id, sc["voiceover"], proj["voice_engine"]))
            for fx in sc["sfx"]:
                conn.execute(
                    "INSERT INTO studio_cues (project_id,scene_id,kind,text,offset_s,"
                    "duration_s,gain) VALUES (?,?,'sfx',?,?,3,0.9)",
                    (project_id, scene_id, fx["prompt"], fx["offset_s"]))
        conn.execute(
            "INSERT INTO studio_cues (project_id,scene_id,kind,text,engine,gain) "
            "VALUES (?,NULL,'music',?,?,0.25)",
            (project_id, norm["music"]["prompt"], proj["music_engine"]))

        script = "\n\n".join(sc["voiceover"] for sc in norm["scenes"] if sc["voiceover"])
        captions = (norm["caption"] + "\n" + norm["hashtags"]).strip()
        audio_plan = json.dumps({"music": norm["music"],
                                 "music_engine": proj["music_engine"],
                                 "voice_engine": proj["voice_engine"]})
        conn.execute(
            "UPDATE studio_projects SET title=COALESCE(NULLIF(?,''),title),logline=?,"
            "style=COALESCE(NULLIF(?,''),style),script=?,captions=?,audio_plan=?,"
            "status='draft',progress_msg=NULL,error=NULL,updated_at=datetime('now') "
            "WHERE id=?",
            (norm["title"], norm["logline"], norm["style"], script, captions,
             audio_plan, project_id))
        conn.commit()
    finally:
        conn.close()


def submit_storyboard(project_id: int, notes: str = "") -> int:
    """Queue the storyboard LLM turn for a project on the unified queue.
    Returns the orchestrator task id (also stored on the project row).
    The project must already exist; status flips to 'storyboarding' here."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM studio_projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"studio project {project_id} not found")
    proj = dict(row)
    _wipe_storyboard_rows(conn, project_id)   # re-storyboard: old draft rows go now
    conn.execute("UPDATE studio_projects SET status='storyboarding',error=NULL,"
                 "updated_at=datetime('now') WHERE id=?", (project_id,))
    conn.commit()
    conn.close()

    kind = (proj.get("kind") or "short").lower()
    scene_rule = "exactly 1 scene" if kind == "short" else "2-6 scenes"
    user_msg = (
        f"Idea: {proj['idea']}\n"
        f"Video kind: {kind} ({scene_rule})\n"
        f"Target length: ~{proj.get('target_seconds') or 20} seconds total\n"
        f"Style/mood: {proj.get('style') or 'your choice — match the idea'}\n"
        "Constraints: each shot is 1.5-7.5 seconds; a scene has 1-5 shots; "
        "allowed shot lengths (seconds): 1.5, 3, 5, 7.5")
    if notes.strip():
        user_msg += f"\nOwner notes for this rewrite: {notes.strip()}"

    def _work():
        from llm_client import _call_lmstudio
        from prompts import get_prompt
        raw = ""
        with _working(project_id):
            try:
                raw = _call_lmstudio(get_prompt("studio_storyboard"), user_msg,
                                     max_tokens=4000)
                data = _extract_json(raw)
                norm = _validate_storyboard(data, kind, proj.get("style") or "")
                _write_storyboard(project_id, norm, proj)
                return {"project_id": project_id, "scenes": len(norm["scenes"])}
            except Exception as ex:
                # Fail LOUDLY, no auto-retry: the owner gets the reason + raw tail.
                err = f"{ex}"
                if raw and "model output" not in err and "did not parse" not in err:
                    err += f" · raw tail: …{raw[-200:]}"
                c = get_conn()
                c.execute("UPDATE studio_projects SET status='failed',error=?,"
                          "updated_at=datetime('now') WHERE id=?", (err[:800], project_id))
                c.commit()
                c.close()
                # Full raw output in the log — parse failures are diagnosed from
                # here (the DB error keeps only the tail).
                logger.error("Studio storyboard %d failed: %s\nRAW MODEL OUTPUT:\n%s",
                             project_id, ex, raw)
                raise

    tid = orch.submit_llm(_work, desc=f"Storyboard: {(proj['idea'] or '')[:40]}",
                          priority=0, task="studio_storyboard", source="studio")
    conn = get_conn()
    conn.execute("UPDATE studio_projects SET storyboard_task=? WHERE id=?", (tid, project_id))
    conn.commit()
    conn.close()
    return tid


# ═══ Phase 2 — render slice: scene chains → assembly → simple mix ════════════
#
# Everything below REUSES the existing engines unmodified:
#   scene render  → run_chain_generation (orch.video_exclusive for the chain,
#                   nested video_acquire per segment, auto-xfade-compile)
#   audio         → run_audio_clip (audio_clips lifecycle, _VIDEO_RUN_LOCK +
#                   orch.video_acquire per clip)
#   stitch/mix    → _compile_chain_video + _mux_audio (CPU-only ffmpeg, no GPU)
# These functions BLOCK and are meant to run as FastAPI background tasks
# (starlette runs sync background tasks in its threadpool).

def _upd_project(project_id: int, **fields):
    """Set columns on a project row (always touches updated_at)."""
    conn = get_conn()
    sets = ",".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE studio_projects SET {sets},updated_at=datetime('now') WHERE id=?",
                 (*fields.values(), project_id))
    conn.commit()
    conn.close()


def _upd_scene(scene_id: int, **fields):
    conn = get_conn()
    sets = ",".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE studio_scenes SET {sets},updated_at=datetime('now') WHERE id=?",
                 (*fields.values(), scene_id))
    conn.commit()
    conn.close()


def _link_scene_shots(scene_id: int, chain_id: int):
    """Shot ↔ rendered-segment linking via (chain_id, chain_index=shot idx):
    each studio_shots row gets the videos.id of its chain segment + a status
    derived from the segment row (design §1.1 / §3.1 step 5)."""
    conn = get_conn()
    shots = conn.execute("SELECT id, idx FROM studio_shots WHERE scene_id=? ORDER BY idx",
                         (scene_id,)).fetchall()
    for sh in shots:
        seg = conn.execute(
            "SELECT id, status FROM videos WHERE chain_id=? AND chain_index=? "
            "ORDER BY id DESC LIMIT 1", (chain_id, sh["idx"])).fetchone()
        if seg:
            st = {"done": "done", "failed": "failed"}.get(seg["status"], "rendering")
            conn.execute("UPDATE studio_shots SET video_id=?,status=?,"
                         "updated_at=datetime('now') WHERE id=?", (seg["id"], st, sh["id"]))
        else:
            conn.execute("UPDATE studio_shots SET status='draft',"
                         "updated_at=datetime('now') WHERE id=?", (sh["id"],))
    conn.commit()
    conn.close()


def _finish_scene(scene_id: int, chain_id: int) -> bool:
    """After the chain ran: copy its outcome onto the scene + link shots.
    Returns True when the scene ended 'done' with a playable clip."""
    from services_media_audio import _video_duration
    conn = get_conn()
    chain = conn.execute("SELECT * FROM video_chains WHERE id=?", (chain_id,)).fetchone()
    conn.close()
    _link_scene_shots(scene_id, chain_id)
    compiled = (chain["compiled_path"] or "") if chain else ""
    if chain and chain["status"] == "done" and compiled and Path(compiled).exists():
        _upd_scene(scene_id, status="done", scene_path=compiled,
                   duration_s=round(_video_duration(compiled), 3), error=None)
        return True
    err = (chain["error"] if chain else None) or "Chain did not produce a compiled clip"
    _upd_scene(scene_id, status="failed", error=str(err)[:500])
    return False


def render_scene(scene_id: int) -> int | None:
    """Render ONE scene as ONE video_chains row (design §3.1): copy the project
    settings, prompts = the ordered shots' video_prompts, per-shot lengths via
    frames_json, nsfw + studio_scene_id set — then run the UNMODIFIED
    run_chain_generation (whole chain under orch.video_exclusive). Owner-uploaded
    footage (studio_shots.source_path) is supported as the LEADING shots: those
    segments are pre-seeded as done videos rows and the chain resumes after them
    (V2V continues from the last uploaded clip). Returns the chain id."""
    from services_media_chain import (run_chain_generation, _compile_chain_video,
                                      _chain_compiled_path)
    conn = get_conn()
    scene = conn.execute("SELECT * FROM studio_scenes WHERE id=?", (scene_id,)).fetchone()
    if not scene:
        conn.close()
        return None
    proj = conn.execute("SELECT * FROM studio_projects WHERE id=?",
                        (scene["project_id"],)).fetchone()
    shots = [dict(r) for r in conn.execute(
        "SELECT * FROM studio_shots WHERE scene_id=? ORDER BY idx", (scene_id,))]
    conn.close()
    if not proj:
        return None
    if not shots:
        _upd_scene(scene_id, status="failed", error="Scene has no shots")
        return None

    prompts = [sh["video_prompt"] for sh in shots]
    frames  = [int(sh["num_frames"] or 49) for sh in shots]
    # Owner footage: longest LEADING run of shots with a real source_path.
    # (An upload after a generated shot is Phase 3 — the chain engine picks
    # prev_video_path from what it generates, so mid-chain seeds don't fit yet.)
    prefix_paths = []
    for sh in shots:
        sp = (sh.get("source_path") or "").strip()
        if sp and Path(sp).exists():
            prefix_paths.append(sp)
        else:
            break
    prefix = len(prefix_paths)

    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO video_chains (title,concept,model_id,width,height,num_frames,"
        "steps,fps,strength,prompts,total_segments,nsfw,studio_scene_id,frames_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"Studio scene {scene['idx'] + 1}: {(scene['title'] or proj['title'] or '')[:60]}",
         scene["summary"] or "", proj["model_id"], proj["width"], proj["height"],
         frames[0], proj["steps"], proj["fps"],
         proj["strength"] if proj["strength"] is not None else 0.7,
         json.dumps(prompts), len(prompts), proj["nsfw"] or 0, scene_id,
         json.dumps(frames)))
    chain_id = cur.lastrowid
    # Pre-seed uploaded leading shots as completed segments of this chain so
    # (chain_id, chain_index) linking + compile treat them like rendered ones.
    for i, sp in enumerate(prefix_paths):
        conn.execute(
            "INSERT INTO videos (prompt,width,height,num_frames,steps,fps,seed,status,"
            "model_id,chain_id,chain_index,nsfw,video_path,progress,progress_msg) "
            "VALUES (?,?,?,?,?,?,0,'done',?,?,?,?,?,100,'Own footage')",
            (prompts[i], proj["width"], proj["height"], frames[i], proj["steps"],
             proj["fps"], proj["model_id"], chain_id, i, proj["nsfw"] or 0, sp))
    if prefix:
        conn.execute("UPDATE video_chains SET completed_segments=? WHERE id=?",
                     (prefix, chain_id))
    conn.execute("UPDATE studio_scenes SET chain_id=?,status='rendering',error=NULL,"
                 "updated_at=datetime('now') WHERE id=?", (chain_id, scene_id))
    conn.commit()
    conn.close()
    logger.info("Studio scene %d → chain %d (%d shots, %d own-footage)",
                scene_id, chain_id, len(shots), prefix)

    if prefix == len(shots):
        # Pure own-footage scene: nothing to generate — compile directly (CPU,
        # no GPU acquire, no node preflight needed).
        out = str(_chain_compiled_path(chain_id))
        conn = get_conn()
        try:
            _compile_chain_video(prefix_paths, out, fps=proj["fps"] or 16)
            conn.execute("UPDATE video_chains SET status='done',compiled_path=?,"
                         "updated_at=datetime('now') WHERE id=?", (out, chain_id))
        except Exception as ex:
            conn.execute("UPDATE video_chains SET status='failed',error=?,"
                         "updated_at=datetime('now') WHERE id=?",
                         (f"Own-footage compile failed: {str(ex)[:300]}", chain_id))
        conn.commit()
        conn.close()
    else:
        run_chain_generation(chain_id, _start_idx=prefix,
                             _prev_video_path=(prefix_paths[-1] if prefix else None))
    _finish_scene(scene_id, chain_id)
    return chain_id


def resume_scene(scene_id: int) -> bool:
    """Retry a failed scene from its last completed segment (free — reuses
    resume_chain_generation) instead of re-rendering everything."""
    from services_media_chain import resume_chain_generation
    conn = get_conn()
    scene = conn.execute("SELECT * FROM studio_scenes WHERE id=?", (scene_id,)).fetchone()
    conn.close()
    if not scene or not scene["chain_id"]:
        return False
    _upd_scene(scene_id, status="rendering", error=None)
    resume_chain_generation(scene["chain_id"])
    return _finish_scene(scene_id, scene["chain_id"])


def _adopt_finished_chain(sc: dict) -> bool:
    """A restart can kill the studio thread AFTER its chain completed on the
    node: the video_chains row is 'done' but the scene never got
    _finish_scene()'d and sits in 'rendering'/'queued' forever. Adopt that
    finished chain (free, CPU-only linking) instead of re-rendering."""
    if not sc.get("chain_id") or sc.get("status") == "done":
        return False
    conn = get_conn()
    ch = conn.execute("SELECT status,compiled_path FROM video_chains WHERE id=?",
                      (sc["chain_id"],)).fetchone()
    conn.close()
    if ch and ch["status"] == "done" and ch["compiled_path"] \
            and Path(ch["compiled_path"]).exists():
        logger.info("Studio scene %d: adopting already-finished chain %d",
                    sc["id"], sc["chain_id"])
        return _finish_scene(sc["id"], sc["chain_id"])
    return False


def render_project(project_id: int):
    """Render every not-done scene SEQUENTIALLY (design §3.4 — one 3060, each
    chain takes video_exclusive; parallelism would just deadlock in the gate)."""
    conn = get_conn()
    scenes = [dict(r) for r in conn.execute(
        "SELECT * FROM studio_scenes WHERE project_id=? ORDER BY idx", (project_id,))]
    conn.close()
    total = len(scenes)
    if not total:
        _upd_project(project_id, status="failed", error="No scenes to render")
        return
    _upd_project(project_id, status="rendering", error=None,
                 progress_msg=f"Rendering scenes (0/{total})…")
    for n, sc in enumerate(scenes, 1):
        if sc["status"] == "done" and sc["scene_path"] and Path(sc["scene_path"]).exists():
            continue   # already rendered — a failed scene only re-renders itself
        _upd_project(project_id, progress_msg=f"Rendering scene {n}/{total}…")
        try:
            ok = (_adopt_finished_chain(sc)
                  or (resume_scene(sc["id"]) if sc["status"] == "failed" and sc["chain_id"]
                      else bool(render_scene(sc["id"])) and _scene_done(sc["id"])))
        except Exception as ex:
            # A raising engine must NOT strand the project in 'rendering' — that
            # status blocks every later stage (root cause of the stuck smoke run).
            logger.exception("Studio scene %d render crashed", sc["id"])
            _upd_scene(sc["id"], status="failed", error=str(ex)[:400])
            ok = False
        if not ok:
            conn = get_conn()
            err = conn.execute("SELECT error FROM studio_scenes WHERE id=?",
                               (sc["id"],)).fetchone()
            conn.close()
            _upd_project(project_id, status="failed",
                         error=f"Scene {n} failed: {(err['error'] if err else '') or 'render error'}"[:500],
                         progress_msg=None)
            return
    _upd_project(project_id, status="draft",
                 progress_msg="All scenes rendered — generate audio, then assemble & mix")


def _scene_done(scene_id: int) -> bool:
    conn = get_conn()
    r = conn.execute("SELECT status FROM studio_scenes WHERE id=?", (scene_id,)).fetchone()
    conn.close()
    return bool(r and r["status"] == "done")


def _project_video_seconds(scenes: list[dict]) -> float:
    """Expected assembled length: measured scene durations minus the 0.5 s xfade
    overlaps (_compile_chain_video's offset formula)."""
    from services_media_audio import _video_duration
    durs = [_video_duration(sc["scene_path"]) for sc in scenes if sc["scene_path"]]
    if not durs:
        return 0.0
    return max(1.0, sum(durs) - 0.5 * (len(durs) - 1))


def render_audio(project_id: int):
    """Phase-2 audio (design §8 Phase 2): ONE music bed from the project's music
    cue + ONE voiceover clip of the whole script (TTS), both as audio_clips rows
    run through the UNMODIFIED run_audio_clip (unified queue, sequential).
    Per-scene VO timeline / SFX cues / ducking = Phase 3."""
    from services_media_audio import run_audio_clip
    conn = get_conn()
    proj = conn.execute("SELECT * FROM studio_projects WHERE id=?", (project_id,)).fetchone()
    if not proj:
        conn.close()
        return
    scenes = [dict(r) for r in conn.execute(
        "SELECT * FROM studio_scenes WHERE project_id=? ORDER BY idx", (project_id,))]
    music_cue = conn.execute(
        "SELECT * FROM studio_cues WHERE project_id=? AND kind='music' ORDER BY id LIMIT 1",
        (project_id,)).fetchone()
    vo_cues = conn.execute(
        "SELECT * FROM studio_cues WHERE project_id=? AND kind='voiceover' ORDER BY id",
        (project_id,)).fetchall()
    conn.close()
    if not scenes or any(sc["status"] != "done" or not sc["scene_path"] for sc in scenes):
        _upd_project(project_id, status="failed",
                     error="Audio needs every scene rendered first")
        return
    total_s = _project_video_seconds(scenes)
    nsfw_flag = proj["nsfw"] or 0
    errors = []

    def _make_clip(kind: str, engine: str, prompt: str, duration: int,
                   cue_id: int | None) -> int | None:
        """One audio_clips row + one run_audio_clip pass; returns the clip id on
        success, None on failure (error collected)."""
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO audio_clips (kind,prompt,engine,duration,status,nsfw,studio_cue_id) "
            "VALUES (?,?,?,?,'queued',?,?)",
            (kind, prompt, engine, duration, nsfw_flag, cue_id))
        clip_id = cur.lastrowid
        if cue_id:
            conn.execute("UPDATE studio_cues SET clip_id=?,status='queued',error=NULL,"
                         "updated_at=datetime('now') WHERE id=?", (clip_id, cue_id))
        conn.commit()
        conn.close()
        run_audio_clip(clip_id)      # blocking: _VIDEO_RUN_LOCK + orch.video_acquire
        conn = get_conn()
        clip = conn.execute("SELECT status,audio_path,error FROM audio_clips WHERE id=?",
                            (clip_id,)).fetchone()
        if cue_id:
            conn.execute("UPDATE studio_cues SET status=?,error=?,"
                         "updated_at=datetime('now') WHERE id=?",
                         (clip["status"], clip["error"], cue_id))
            conn.commit()
        conn.close()
        if clip["status"] == "done" and clip["audio_path"] and Path(clip["audio_path"]).exists():
            return clip_id
        errors.append(f"{kind}: {(clip['error'] or 'generation failed')[:200]}")
        return None

    _upd_project(project_id, status="mixing", error=None, progress_msg="Composing music bed…")
    if music_cue:
        dur = int(min(max(total_s + 1, 8), 60))   # short bed is fine — the mixer loops it
        cid = _make_clip("music", music_cue["engine"] or proj["music_engine"] or "musicgen",
                         music_cue["text"], dur, music_cue["id"])
        if cid:
            _upd_project(project_id, music_clip_id=cid)

    script = (proj["script"] or "").strip()
    if script:
        _upd_project(project_id, progress_msg="Recording narration…")
        # Phase-2 simple shape: ONE whole-script read from t=0, tagged onto the
        # FIRST voiceover cue (single-scene: they are literally the same; multi-
        # scene: the tag keeps the clip out of the normal Audio gallery — the
        # true per-scene VO timeline is Phase 3).
        cue_id = vo_cues[0]["id"] if vo_cues else None
        cid = _make_clip("voice", proj["voice_engine"] or "mms_tts", script, 8, cue_id)
        if cid:
            _upd_project(project_id, vo_clip_id=cid)

    conn = get_conn()
    proj2 = conn.execute("SELECT music_clip_id,vo_clip_id FROM studio_projects WHERE id=?",
                         (project_id,)).fetchone()
    conn.close()
    if not proj2["music_clip_id"] and not proj2["vo_clip_id"]:
        _upd_project(project_id, status="failed", progress_msg=None,
                     error=("Audio failed — " + "; ".join(errors))[:500] if errors
                     else "No music cue and no script — nothing to generate")
        return
    msg = "Audio ready — assemble & mix"
    if errors:
        msg += " (partial: " + "; ".join(errors)[:200] + ")"
    _upd_project(project_id, status="draft", progress_msg=msg)


def _clip_wav(clip_id) -> str:
    """audio_path of a done audio_clips row, '' otherwise."""
    if not clip_id:
        return ""
    conn = get_conn()
    r = conn.execute("SELECT status,audio_path FROM audio_clips WHERE id=?",
                     (clip_id,)).fetchone()
    conn.close()
    if r and r["status"] == "done" and r["audio_path"] and Path(r["audio_path"]).exists():
        return r["audio_path"]
    return ""


def _stream_types(path: str) -> set[str]:
    """codec_type of every stream in a media file ({'video','audio',…})."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", path], capture_output=True, text=True, timeout=20)
        return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    except Exception:
        return set()


def _mux_voice_only(video: str, voice: str, out: str):
    """Voiceover with no music bed — same shape as _mux_audio (video is the
    master clock, -t clamps, -c:v copy) minus the looped bed input."""
    from services_media_audio import _video_duration
    dur = _video_duration(video) or 5.0
    subprocess.run(
        ["ffmpeg", "-y", "-i", video, "-i", voice,
         "-filter_complex", "[1:a]volume=1.0,apad[a]", "-map", "0:v", "-map", "[a]",
         "-t", f"{dur:.2f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", out],
        check=True, capture_output=True, timeout=180)


def assemble_project(project_id: int):
    """Stitch the scene clips into the full video and mix the generated audio on
    (design §3.2 + Phase-2 simple mix). CPU-only ffmpeg — no GPU acquire, same
    policy as the chain auto-compile. Single scene → the scene clip is copied
    (that IS what _compile_chain_video does for one input); multi-scene → xfade
    stitch with concat fallback. Sets video_path, then final_path (with audio)."""
    from config import VIDEOS_DIR
    from services_media_chain import _compile_chain_video
    from services_media_audio import _mux_audio, _video_duration
    conn = get_conn()
    proj = conn.execute("SELECT * FROM studio_projects WHERE id=?", (project_id,)).fetchone()
    scenes = [dict(r) for r in conn.execute(
        "SELECT * FROM studio_scenes WHERE project_id=? ORDER BY idx", (project_id,))]
    conn.close()
    if not proj:
        return
    paths = [sc["scene_path"] for sc in scenes]
    if not scenes or any(sc["status"] != "done" or not p or not Path(p).exists()
                         for sc, p in zip(scenes, paths)):
        _upd_project(project_id, status="failed",
                     error="Assemble needs every scene rendered (done) first")
        return

    _upd_project(project_id, status="assembling", error=None,
                 progress_msg="Stitching scenes…")
    full = str(VIDEOS_DIR / f"studio_{project_id}_full.mp4")
    try:
        _compile_chain_video(paths, full, fps=proj["fps"] or 16)
    except Exception as ex:
        _upd_project(project_id, status="failed", progress_msg=None,
                     error=f"Assembly failed: {str(ex)[:400]}")
        return
    # Never carry an empty/broken stitch into the mix (a killed ffmpeg leaves a
    # 0-byte file behind) — and re-probe the REAL length from disk: xfade vs the
    # concat fallback give different totals (design §3.2 risk note), so nothing
    # downstream may trust the scenes' est/DB durations.
    full_dur = _video_duration(full)
    if not Path(full).exists() or Path(full).stat().st_size < 1024 or full_dur <= 0:
        Path(full).unlink(missing_ok=True)
        _upd_project(project_id, status="failed", progress_msg=None,
                     error="Assembly produced an empty video file")
        return
    _upd_project(project_id, video_path=full, progress_msg="Mixing audio…",
                 status="mixing")

    music = _clip_wav(proj["music_clip_id"])
    voice = _clip_wav(proj["vo_clip_id"])
    final = str(VIDEOS_DIR / f"studio_{project_id}_final.mp4")
    try:
        if music:
            _mux_audio(full, music, voice, final)     # looped bed + optional VO, -t clamps
        elif voice:
            _mux_voice_only(full, voice, final)
        else:
            shutil.copy2(full, final)                  # silent final (no audio generated)
        # Hard verification — never mark 'done' on a hollow file. This is what
        # used to slip through: a swallowed/killed mix left a partial final (or
        # a video-only one) and the project still said done.
        kinds = _stream_types(final)
        if not Path(final).exists() or Path(final).stat().st_size < 1024 \
                or "video" not in kinds or _video_duration(final) <= 0:
            raise RuntimeError("mix produced an empty/broken final mp4")
        if (music or voice) and "audio" not in kinds:
            raise RuntimeError("mix produced a final with NO audio stream")
        note = "Final video ready" if (music or voice) else \
               "Final video ready (SILENT — generate audio, then assemble again)"
        _upd_project(project_id, final_path=final, status="done", progress_msg=note)
        logger.info("Studio project %d assembled → %s (%.1fs, streams=%s)",
                    project_id, final, _video_duration(final), ",".join(sorted(kinds)))
    except subprocess.CalledProcessError as ex:
        Path(final).unlink(missing_ok=True)   # no 0-byte/partial final left on disk
        tail = (ex.stderr[-300:].decode("utf-8", "ignore") if isinstance(ex.stderr, bytes)
                else str(ex.stderr or "")[-300:])
        _upd_project(project_id, status="failed", progress_msg=None, final_path=None,
                     error=f"Audio mix failed: {tail}"[:500])
    except Exception as ex:
        Path(final).unlink(missing_ok=True)
        _upd_project(project_id, status="failed", progress_msg=None, final_path=None,
                     error=f"Audio mix failed: {str(ex)[:400]}")


# ═══ Meme Quick-Mode (fast lane) ═════════════════════════════════════════════
#
# One-line meme text → instant single-scene short. A THIN preset OVER the
# normal engine: seed_meme_storyboard() builds a deterministic storyboard (no
# LLM turn — that's the whole speed win) and pushes it through the SAME
# validator (_validate_storyboard: clamps + the unconditional safety floor)
# and writer (_write_storyboard) the LLM path uses; produce_meme() then runs
# the UNMODIFIED produce_project pipeline (render → audio → assemble & mix)
# and finishes with ONE CPU-only ffmpeg drawtext pass burning the meme caption
# onto the final. Nothing in the render/audio engines is forked.

_MEME_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)


def _meme_font() -> str:
    for f in _MEME_FONTS:
        if Path(f).exists():
            return f
    raise RuntimeError("no usable bold font found for the meme caption")


def _wrap_caption(text: str, width: int = 16) -> str:
    """Classic meme look: UPPERCASE, word-wrapped to short centered lines."""
    import textwrap
    return "\n".join(textwrap.wrap((text or "").strip().upper(), width=width,
                                   max_lines=4, placeholder="…"))


def _burn_caption(video: str, text: str, out: str):
    """Burn the meme caption onto a finished video: bold white DejaVu with a
    black border, top-center (the classic meme block). textfile= (not text=)
    sidesteps every ffmpeg filter-escaping game; the audio stream is COPIED
    untouched so the mixed bed/VO from _mux_audio survives bit-for-bit."""
    import tempfile
    font = _meme_font()
    wrapped = _wrap_caption(text)
    if not wrapped:
        raise ValueError("empty caption")
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0", "-show_entries",
             "stream=width", "-of", "csv=p=0", video],
            capture_output=True, text=True, timeout=20)
        vid_w = int(float(r.stdout.strip().split(",")[0]))
    except Exception:
        vid_w = 480
    # DejaVu Sans Bold averages ~0.62 em advance: 16-char lines at w/12 leave a
    # comfortable side margin at any resolution (w/10 clipped on 480-wide).
    fs = max(20, vid_w // 12)
    tf = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8")
    try:
        tf.write(wrapped)
        tf.close()
        vf = (f"drawtext=fontfile={font}:textfile={tf.name}:fontcolor=white:"
              f"borderw={max(2, fs // 12)}:bordercolor=black:fontsize={fs}:"
              f"x=(w-text_w)/2:y=h*0.05:line_spacing={max(4, fs // 6)}")
        subprocess.run(
            ["ffmpeg", "-y", "-i", video, "-vf", vf, "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "copy", out],
            check=True, capture_output=True, timeout=300)
    finally:
        Path(tf.name).unlink(missing_ok=True)


def seed_meme_storyboard(project_id: int) -> dict:
    """Write the preset 'meme' storyboard for a project: 1 scene, 1 punchy shot
    (~3-6 s, snapped), caption = the meme text, a short VO read of the text and
    a punchy music cue. NO LLM call — instant — but the board still goes
    through _validate_storyboard (safety floor included) and _write_storyboard
    exactly like an LLM-authored one. Raises ValueError on a refused idea."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM studio_projects WHERE id=?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise ValueError(f"studio project {project_id} not found")
    proj = dict(row)
    text = (proj["idea"] or "").strip()
    if not text:
        raise ValueError("meme text is empty")
    style = (proj.get("style") or "").strip() \
        or "bold internet-meme style, vivid colors, exaggerated comedic energy"
    secs = min(max(float(proj.get("target_seconds") or 5), 3.0), 6.0)
    data = {
        "title": text[:60],
        "logline": text,
        "style": style,
        "scenes": [{
            "title": "Meme",
            "summary": text,
            # keep the VO a one-breath punchline; long pastes get no narration
            "voiceover": text if len(text) <= 220 else "",
            "caption": text,
            "shots": [{
                "video_prompt": (f"{text}. {style}, punchy meme short, dynamic "
                                 f"camera, high contrast, crisp lighting"),
                "seconds": secs,
                "caption": text,
            }],
            "sfx": [],
        }],
        "music": {"prompt": "punchy upbeat comedic meme music, quirky, "
                            "energetic, catchy hook, instrumental",
                  "mood": "meme energy"},
        "caption": text,
        "hashtags": "#meme #funny #shorts",
    }
    norm = _validate_storyboard(data, "short", style)
    _write_storyboard(project_id, norm, proj)
    logger.info("Studio meme %d seeded: 1 scene, %.1fs shot", project_id,
                norm["scenes"][0]["shots"][0]["seconds"])
    return norm


def produce_meme(project_id: int):
    """Quick-Meme background pipeline: the UNMODIFIED produce_project (scene
    render → music+VO → assemble & mix, all statuses/verification included)
    plus the caption burn. A failed burn demotes to a progress note instead of
    failing the project — the un-captioned final is still a valid export."""
    from config import VIDEOS_DIR
    with _working(project_id):
        produce_project(project_id)
        conn = get_conn()
        proj = conn.execute("SELECT status,final_path,idea FROM studio_projects "
                            "WHERE id=?", (project_id,)).fetchone()
        cap = conn.execute("SELECT caption FROM studio_scenes WHERE project_id=? "
                           "ORDER BY idx LIMIT 1", (project_id,)).fetchone()
        conn.close()
        if not proj or proj["status"] != "done" or not proj["final_path"] \
                or not Path(proj["final_path"]).exists():
            return                          # pipeline already failed loudly
        text = ((cap["caption"] if cap else "") or proj["idea"] or "").strip()
        if not text:
            return
        out = str(Path(VIDEOS_DIR) / f"studio_{project_id}_meme.mp4")
        try:
            _burn_caption(proj["final_path"], text, out)
            kinds = _stream_types(out)
            had_audio = "audio" in _stream_types(proj["final_path"])
            if not Path(out).exists() or Path(out).stat().st_size < 1024 \
                    or "video" not in kinds or (had_audio and "audio" not in kinds):
                raise RuntimeError("caption burn produced a broken file")
            _upd_project(project_id, final_path=out,
                         progress_msg="😂 Meme ready — caption burned, audio mixed")
            logger.info("Studio meme %d captioned → %s", project_id, out)
        except subprocess.CalledProcessError as ex:
            Path(out).unlink(missing_ok=True)
            tail = (ex.stderr[-200:].decode("utf-8", "ignore")
                    if isinstance(ex.stderr, bytes) else str(ex.stderr or "")[-200:])
            logger.error("Studio meme %d caption burn failed: %s", project_id, tail)
            _upd_project(project_id,
                         progress_msg=f"Final ready (caption burn failed: {tail})"[:300])
        except Exception as ex:
            Path(out).unlink(missing_ok=True)
            logger.exception("Studio meme %d caption burn failed", project_id)
            _upd_project(project_id,
                         progress_msg=f"Final ready (caption burn failed: {str(ex)[:200]})")


# ═══ Full pipeline + endpoint task wrappers ══════════════════════════════════

def _project_status(project_id: int) -> str:
    conn = get_conn()
    r = conn.execute("SELECT status FROM studio_projects WHERE id=?",
                     (project_id,)).fetchone()
    conn.close()
    return r["status"] if r else "missing"


def _has_audio_plan(project_id: int) -> bool:
    """True when there is anything to generate audio FROM: a music cue or a
    non-empty script (voiceover)."""
    conn = get_conn()
    has_music = conn.execute(
        "SELECT 1 FROM studio_cues WHERE project_id=? AND kind='music' LIMIT 1",
        (project_id,)).fetchone()
    proj = conn.execute("SELECT script FROM studio_projects WHERE id=?",
                        (project_id,)).fetchone()
    conn.close()
    return bool(has_music) or bool(proj and (proj["script"] or "").strip())


def produce_project(project_id: int):
    """The whole Phase-2 pipeline as ONE background task (this was the bug: the
    stages existed but nothing chained them, so 'rendering a project' ended at
    a silent, audio-less final): render every scene → generate the music bed +
    voiceover cues via run_audio_clip (unless usable clips already exist) →
    assemble & mix, with the post-mix stream verification. Every stage keeps
    its own status writes; a failing stage stops the pipeline with the project
    marked 'failed' loudly."""
    with _working(project_id):
        try:
            render_project(project_id)
            if _project_status(project_id) != "draft":
                return                                   # a scene failed — stop
            conn = get_conn()
            proj = conn.execute("SELECT music_clip_id,vo_clip_id FROM studio_projects "
                                "WHERE id=?", (project_id,)).fetchone()
            conn.close()
            have_audio = bool(_clip_wav(proj["music_clip_id"]) or
                              _clip_wav(proj["vo_clip_id"]))
            if not have_audio and _has_audio_plan(project_id):
                render_audio(project_id)
                if _project_status(project_id) != "draft":
                    return                               # audio hard-failed — stop
            assemble_project(project_id)
        except Exception as ex:
            # Belt & braces: nothing may strand the project in a busy status.
            logger.exception("Studio produce %d crashed", project_id)
            _upd_project(project_id, status="failed", progress_msg=None,
                         error=f"Pipeline crashed: {str(ex)[:400]}")


def _scene_project_id(scene_id: int) -> int | None:
    conn = get_conn()
    r = conn.execute("SELECT project_id FROM studio_scenes WHERE id=?",
                     (scene_id,)).fetchone()
    conn.close()
    return r["project_id"] if r else None


def render_scene_task(scene_id: int):
    """Endpoint entry: register the owning project + never leave 'rendering'."""
    pid = _scene_project_id(scene_id)
    if pid is None:
        return
    with _working(pid):
        try:
            render_scene(scene_id)
        except Exception as ex:
            logger.exception("Studio scene %d render crashed", scene_id)
            _upd_scene(scene_id, status="failed", error=str(ex)[:400])


def resume_scene_task(scene_id: int):
    pid = _scene_project_id(scene_id)
    if pid is None:
        return
    with _working(pid):
        try:
            resume_scene(scene_id)
        except Exception as ex:
            logger.exception("Studio scene %d resume crashed", scene_id)
            _upd_scene(scene_id, status="failed", error=str(ex)[:400])


def render_audio_task(project_id: int):
    with _working(project_id):
        try:
            render_audio(project_id)
        except Exception as ex:
            logger.exception("Studio audio %d crashed", project_id)
            _upd_project(project_id, status="failed", progress_msg=None,
                         error=f"Audio crashed: {str(ex)[:400]}")


def assemble_task(project_id: int):
    with _working(project_id):
        try:
            assemble_project(project_id)
        except Exception as ex:
            logger.exception("Studio assemble %d crashed", project_id)
            _upd_project(project_id, status="failed", progress_msg=None,
                         error=f"Assemble crashed: {str(ex)[:400]}")

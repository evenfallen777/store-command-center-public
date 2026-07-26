"""GPU guard — auto-pause the unified queue while the GPU node is in interactive use
(Steam game, Wine/Bottles app, emulator, Blender, OBS, a VM…).

The node runs ~/gpu-guard.sh (systemd user unit `gpu-guard.service`) which POSTs a
heartbeat here every few seconds with busy=true/false plus the app names it saw:
  busy=true  → orch.pause()  — in-flight jobs finish, nothing new starts
  busy=false → orch.resume() — ONLY if the guard is what paused the queue, so a
               manual Dashboard pause is never clobbered by the heartbeat.

Safety: if the node dies/reboots while the queue is guard-paused, heartbeats stop.
maybe_unstick() (called from the Dashboard's /api/queue/status poll and from this
router's GET) auto-resumes after STALE_SEC without a beat, so a dead guard can
never wedge the queue shut.
"""
import time
import logging
import threading

from fastapi import APIRouter, Request, Body

from orchestrator import orch
from routers.jellycoin import _check_miner   # same LAN-box token the miner uses

log = logging.getLogger("gpu_guard")
router = APIRouter()

STALE_SEC = 300   # guard-paused + no heartbeat this long → assume guard dead, resume

# ── Settings (all optional; sensible defaults when unset) ─────────────────────
#   gpu_guard_enabled   (1) — 0/off: node stays hands-off (no pause, no kills);
#                             lets a light game share the GPU with the AI queue.
#   gpu_guard_auto_resume (1) — 0/off: jobs killed for a game stay 'failed'
#                             instead of auto-re-running on resume.
#   gpu_guard_stale_sec (300) — heartbeat staleness before a dead guard's pause
#                             is auto-released.
#   gpu_miner_exclusive (1) — 0/off: node stops gating JellyMiner on queue
#                             activity (back to always-on throttled coexistence).
#   gpu_guard_editor_mode (1) — CO-OP: a game ENGINE EDITOR (Unreal/Unity/Godot)
#                             does NOT pause the queue. Instead the node reports
#                             mode="editor" and the Store keeps serving LLM work
#                             on a small model (games_mcp_model) that fits
#                             ALONGSIDE the editor, while VRAM-hungry job kinds
#                             (image/video/3D) are held. 0/off: editors are
#                             treated as ordinary heavy apps (full pause).
#
# Why co-op exists: the guard's original premise is "AI yields to the desktop".
# That breaks when the AI is DRIVING the editor over MCP — pausing the queue is
# exactly the wrong move. Co-op shrinks the AI footprint to fit instead of
# stopping it. It is NOT a licence to ignore VRAM: a 12 GB card cannot hold the
# 12B keep-warm default AND an Unreal editor, which is why the model used while
# an editor is up is a separate, deliberately small setting.

def _setting(key: str, default):
    try:
        from deps import get_setting
        v = get_setting(key, default)
        return default if v in (None, "") else v
    except Exception:
        return default


def _flag(key: str, default: str = "1") -> bool:
    return str(_setting(key, default)).strip().lower() not in ("0", "off", "false", "no")


def _stale_sec() -> int:
    try:
        return max(60, int(_setting("gpu_guard_stale_sec", STALE_SEC)))
    except Exception:
        return STALE_SEC

_state = {
    "guard_paused": False,   # WE paused the queue (vs. a manual Dashboard pause)
    "busy": False,
    "apps": [],
    "last_beat": 0.0,
    "since": 0.0,
    "interrupted": {},       # {generations/videos/chains/audio: [ids]} mid-flight at pause
    "mode": "",              # "" | heavy | editor | tdarr — what KIND of busy
    "editor_active": False,  # a game-engine editor is up and we are co-operating
}


# ── editor co-op ──────────────────────────────────────────────────────────────

def editor_active() -> bool:
    """True while a game-engine editor holds the GPU under co-op mode.

    Callers that are about to queue VRAM-hungry work (image/video/3D) should
    check this and hold off — the editor owns the headroom those jobs need."""
    return bool(_state["editor_active"])


def mcp_model() -> str:
    """Model to use for editor/MCP-driven LLM work. Deliberately separate from
    the orchestrator default: the keep-warm 12B will not fit beside an Unreal
    editor on a 12 GB card, so this is where you name something that does
    (a 4B QAT, a 7B at Q4, or a CPU-hosted model). Blank = orchestrator default,
    which is the right answer for Godot but NOT for Unreal."""
    return str(_setting("games_mcp_model", "") or "").strip()


def coop_blocks_media() -> bool:
    """Under co-op we keep LLM work flowing but hold image/video/3D — those are
    the kinds that would race the editor for the same VRAM the guard just chose
    not to reclaim."""
    return editor_active()


def _snapshot_generating() -> dict:
    """Everything mid-flight right now — the node's kill switch is about to abort
    it (ComfyUI /interrupt for images, pkill of store_videogen/store_audiogen for
    video/audio), and the aborted rows will land as 'failed'."""
    snap = {"generations": [], "videos": [], "chains": [], "audio": []}
    try:
        from db import get_conn
        conn = get_conn()
        q = lambda sql: [r["id"] for r in conn.execute(sql).fetchall()]
        snap["generations"] = q("SELECT id FROM generations WHERE status='generating'")
        snap["videos"] = q("SELECT id FROM videos WHERE status='generating' AND chain_id IS NULL")
        snap["chains"] = q("SELECT id FROM video_chains WHERE status='generating'")
        snap["audio"]  = q("SELECT id FROM audio_clips WHERE status='generating'")
        conn.close()
    except Exception:
        pass
    return snap


def _now_failed(conn, table: str, ids: list) -> list:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return [r["id"] for r in conn.execute(
        f"SELECT id FROM {table} WHERE status='failed' AND id IN ({marks})", ids).fetchall()]


def _resume_interrupted():
    """Re-launch the jobs the kill switch aborted. Only rows that actually ended
    'failed' are redone (one that finished before the kill stays 'done').
    Images + single videos re-run from scratch; CHAINS resume from their last
    completed segment (services_media.resume_chain_generation). 3D jobs are the
    one type never killed, so there is nothing to redo for them. Every rerun
    serializes through the orchestrator as usual."""
    snap, _state["interrupted"] = _state["interrupted"], {}
    if not any(snap.get(k) for k in ("generations", "videos", "chains", "audio")):
        return
    if not _flag("gpu_guard_auto_resume"):
        log.info("auto-resume disabled in Settings — interrupted jobs stay failed")
        return
    try:
        from db import get_conn
        import services
        import services_media
        conn = get_conn()
        gens   = _now_failed(conn, "generations", snap.get("generations", []))
        vids   = _now_failed(conn, "videos",      snap.get("videos", []))
        chains = _now_failed(conn, "video_chains", snap.get("chains", []))
        clips  = _now_failed(conn, "audio_clips", snap.get("audio", []))
        for table, ids in (("generations", gens), ("videos", vids)):
            if ids:
                conn.execute(
                    f"UPDATE {table} SET status='queued', updated_at=datetime('now') "
                    f"WHERE id IN ({','.join('?' * len(ids))})", ids)
        conn.commit()
        conn.close()
        reruns = (
            [(services.run_generation, gid, "generation") for gid in gens] +
            [(services_media.run_video_generation, vid, "video") for vid in vids] +
            [(services_media.resume_chain_generation, cid, "chain") for cid in chains] +
            [(services_media.run_audio_clip, aid, "audio clip") for aid in clips]
        )
        for fn, jid, kind in reruns:
            log.info("re-running %s %d (interrupted for interactive GPU use)", kind, jid)
            threading.Thread(target=fn, args=(jid,), daemon=True,
                             name=f"guard-redo-{kind}-{jid}").start()
    except Exception as ex:
        log.error("resume of interrupted jobs failed: %s", ex)


def maybe_unstick():
    """Auto-resume if the guard paused the queue but stopped heartbeating."""
    stale = _stale_sec()
    if _state["guard_paused"] and time.time() - _state["last_beat"] > stale:
        log.warning("gpu-guard heartbeat stale (>%ds) — auto-resuming queue", stale)
        _state.update(guard_paused=False, busy=False, apps=[], since=0.0)
        orch.resume()
        _resume_interrupted()


def guard_info() -> dict:
    """Snapshot for the Dashboard: is the node interactively busy, and with what."""
    return {"busy": _state["busy"], "apps": list(_state["apps"]),
            "since": _state["since"], "guard_paused": _state["guard_paused"],
            "mode": _state["mode"], "editor_active": _state["editor_active"]}


def _store_busy() -> bool:
    """Is the Store actually doing (or about to do) GPU work? The node's guard
    uses this to gate JellyMiner — mining and inference never share the GPU;
    the miner only runs when this has been False for a while."""
    s = orch.status()
    if s["llm"] != "idle" or s["active_images"] > 0 or s["queue"]:
        return True
    try:
        from db import get_conn
        conn = get_conn()
        n = conn.execute(
            "SELECT (SELECT COUNT(*) FROM generations  WHERE status='generating') +"
            "       (SELECT COUNT(*) FROM videos       WHERE status='generating') +"
            "       (SELECT COUNT(*) FROM video_chains WHERE status='generating') +"
            "       (SELECT COUNT(*) FROM audio_clips  WHERE status='generating') +"
            "       (SELECT COUNT(*) FROM models3d     WHERE status='generating')"
        ).fetchone()[0]
        conn.close()
        return n > 0
    except Exception:
        return False


@router.post("/api/gpu/guard/state")
def guard_beat(request: Request, payload: dict = Body(...)):
    """Heartbeat from the node's gpu-guard. Idempotent — safe to repeat every tick."""
    _check_miner(request)
    busy = bool(payload.get("busy"))
    apps = [str(a)[:60] for a in (payload.get("apps") or [])][:10]
    mode = str(payload.get("mode") or "heavy").strip().lower()
    if mode not in ("heavy", "editor", "tdarr"):
        mode = "heavy"
    _state["last_beat"] = time.time()

    # ── co-op: an engine editor is up, and we choose NOT to pause ────────────
    # Distinct from the guard-disabled branch below: there we are hands-off
    # everywhere, here we stay in control (media held, small model) and simply
    # decline to stop LLM work the editor itself may be waiting on.
    if busy and mode == "editor" and _flag("gpu_guard_enabled") \
            and _flag("gpu_guard_editor_mode"):
        if not _state["editor_active"]:
            log.info("engine editor up (%s) — co-op mode: queue keeps running on %s, "
                     "media jobs held", ", ".join(apps) or "?",
                     mcp_model() or "the default model")
            _state["since"] = time.time()
        # A pause we set earlier (e.g. the editor was launched from a Steam
        # shell that tripped the heavy path first) is released — co-op supersedes.
        if _state["guard_paused"]:
            _state["guard_paused"] = False
            if orch.is_paused():
                orch.resume()
            _resume_interrupted()
        _state.update(busy=True, apps=apps, mode="editor", editor_active=True)
        return {"ok": True, "paused": orch.is_paused(), "mode": "editor",
                "guard_paused": False, "editor_active": True,
                "mcp_model": mcp_model()}

    if busy and not _flag("gpu_guard_enabled"):
        # Guard disabled in Settings: keep the node's state visible on the
        # Dashboard but never pause — and release a pause we set before the
        # setting was flipped mid-game.
        if _state["guard_paused"]:
            log.info("gpu_guard_enabled turned off — releasing guard pause")
            _state["guard_paused"] = False
            orch.resume()
        if not _state["busy"]:
            _state["since"] = time.time()
        _state.update(busy=True, apps=apps, mode=mode, editor_active=False)
    elif busy:
        if not _state["busy"]:
            log.info("node busy (%s) — pausing GPU queue", ", ".join(apps) or "?")
            _state["since"] = time.time()
            # the node is about to /interrupt ComfyUI — remember what was mid-flight
            _state["interrupted"] = _snapshot_generating()
        if not orch.is_paused():
            orch.pause()
            _state["guard_paused"] = True
        _state.update(busy=True, apps=apps, mode=mode, editor_active=False)
    else:
        if _state["busy"]:
            log.info("node free — resuming GPU queue")
        was_ours = _state["guard_paused"]
        _state.update(busy=False, apps=[], since=0.0, guard_paused=False,
                      mode="", editor_active=False)
        if was_ours and orch.is_paused():
            orch.resume()
            _resume_interrupted()
    return {"ok": True, "paused": orch.is_paused(), "mode": _state["mode"],
            "guard_paused": _state["guard_paused"],
            "editor_active": _state["editor_active"]}


@router.get("/api/gpu/guard/state")
def guard_status(request: Request):
    """Node-side guard polls this to learn when in-flight work has drained (so it
    can free VRAM for the game). Also handy for debugging."""
    _check_miner(request)
    maybe_unstick()
    s = orch.status()
    return {"paused": orch.is_paused(), **guard_info(),
            "llm": s["llm"], "active_images": s["active_images"],
            "queue_len": len(s["queue"]), "store_busy": _store_busy(),
            "guard_enabled": _flag("gpu_guard_enabled"),
            "miner_exclusive": _flag("gpu_miner_exclusive"),
            # the node reads these to decide whether an editor gets the co-op
            # path (no kills, no unload) or the ordinary heavy path
            "editor_mode": _flag("gpu_guard_editor_mode"),
            "mcp_model": mcp_model()}

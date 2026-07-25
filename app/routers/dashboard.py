"""dashboard routes."""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Form, UploadFile, File
from deps import *
from services import *

router = APIRouter()


@router.get("/api/stats")
def get_stats():
    conn = get_conn()
    c = conn.cursor()
    proposals_pending = c.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0]
    review_count      = c.execute("SELECT COUNT(*) FROM designs WHERE status='review' AND (source IS NULL OR source='pipeline')").fetchone()[0]
    approved_count    = c.execute("SELECT COUNT(*) FROM designs WHERE status IN ('approved','published')").fetchone()[0]
    published_count   = c.execute("SELECT COUNT(*) FROM designs WHERE status='published'").fetchone()[0]
    generating_count  = c.execute("SELECT COUNT(*) FROM generations WHERE status='generating'").fetchone()[0]
    conn.close()
    return {
        "proposals_pending": proposals_pending,
        "review_count":      review_count,
        "approved_count":    approved_count,
        "published_count":   published_count,
        "generating_count":  generating_count,
    }

@router.get("/api/status")
def get_status():
    s = orch.status()
    try:
        r = httpx.get("http://127.0.0.1:8188/queue", timeout=3)
        q = r.json()
        s["comfyui_running"] = len(q.get("queue_running", []))
        s["comfyui_pending"] = len(q.get("queue_pending", []))
    except Exception:
        s["comfyui_running"] = 0
        s["comfyui_pending"] = 0
    return s

@router.get("/api/queue")
def get_queue():
    """Universal queue — every in-flight GPU/generation job merged into one live view.

    Sources: the 5 DB-backed job tables (images, videos, video chains, audio, 3D) for
    row-level detail, plus the in-memory LLM/vision orchestrator queue, plus ComfyUI's
    live running/pending counts. Read-only; safe to poll. Feeds the bottom-left strip,
    the header bar, and the Studio GPU view.
    """
    jobs = []
    # NSFW jobs run through the same queue but are redacted to a discreet generic
    # chip ("Private job", no prompt/detail/kind) unless the display toggle is on.
    import nsfw as _nsfw
    _show_nsfw = _nsfw.display_on()
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT id, 'image' AS kind, prompt AS label, status,
                   NULL AS progress, NULL AS detail, created_at, COALESCE(nsfw,0) AS nsfw
              FROM generations  WHERE status IN ('queued','generating')
            UNION ALL
            SELECT id, 'video' AS kind, prompt AS label, status,
                   progress AS progress, progress_msg AS detail, created_at, COALESCE(nsfw,0) AS nsfw
              FROM videos       WHERE status IN ('queued','generating')
            UNION ALL
            SELECT id, 'video chain' AS kind, COALESCE(NULLIF(title,''), concept) AS label, status,
                   CASE WHEN total_segments>0
                        THEN CAST(completed_segments*100.0/total_segments AS INT) END AS progress,
                   NULL AS detail, created_at, COALESCE(nsfw,0) AS nsfw
              FROM video_chains WHERE status IN ('pending','generating')
            UNION ALL
            SELECT id, 'audio' AS kind, prompt AS label, status,
                   NULL AS progress, progress_msg AS detail, created_at, COALESCE(nsfw,0) AS nsfw
              FROM audio_clips  WHERE status IN ('queued','generating')
            UNION ALL
            SELECT id, '3d' AS kind, COALESCE(NULLIF(title,''), gen_prompt) AS label, status,
                   NULL AS progress, progress_msg AS detail, created_at, COALESCE(nsfw,0) AS nsfw
              FROM models3d     WHERE status='generating'
            ORDER BY created_at DESC
        """).fetchall()
        for r in rows:
            st = r["status"]
            redact = bool(r["nsfw"]) and not _show_nsfw
            jobs.append({
                "id": r["id"], "kind": ("private" if redact else r["kind"]),
                "label": (_nsfw.PRIVATE_LABEL if redact
                          else (r["label"] or "").strip() or f'{r["kind"]} #{r["id"]}'),
                "status": st,
                "phase": "running" if st in ("generating", "running") else "queued",
                "progress": r["progress"], "detail": (None if redact else r["detail"]),
                "origin": "store", "created_at": r["created_at"],
            })
    finally:
        conn.close()

    # LLM / vision jobs live only in memory (orchestrator), so merge them in Python.
    try:
        for q in orch.status().get("queue", []):
            mdl = q.get("model")
            jobs.append({
                "id": q.get("id"), "kind": "llm",
                "label": q.get("desc") or q.get("type") or "LLM task",
                "status": q.get("status"),
                "phase": "running" if q.get("status") == "running" else "queued",
                "progress": None, "detail": (f"model: {mdl}" if mdl else None),
                "model": mdl,
                "origin": "store", "created_at": None,
            })
    except Exception:
        pass

    # ComfyUI exposes counts only (no per-job labels).
    comfy = {"running": 0, "pending": 0}
    try:
        cq = httpx.get("http://127.0.0.1:8188/queue", timeout=3).json()
        comfy["running"] = len(cq.get("queue_running", []))
        comfy["pending"] = len(cq.get("queue_pending", []))
    except Exception:
        pass

    # Running jobs first, queued after; stable sort keeps created_at-desc within each group.
    jobs.sort(key=lambda j: 0 if j["phase"] == "running" else 1)
    running = sum(1 for j in jobs if j["phase"] == "running")
    by_kind = {}
    for j in jobs:
        by_kind[j["kind"]] = by_kind.get(j["kind"], 0) + 1
    return {
        "jobs": jobs,
        "counts": {"total": len(jobs), "running": running,
                   "queued": len(jobs) - running, "by_kind": by_kind},
        "comfyui": comfy,
        "paused": orch.is_paused(),
        "node_guard": _node_guard_info(),
        "busy": bool(jobs) or comfy["running"] > 0 or comfy["pending"] > 0,
    }


@router.get("/api/queue/history")
def get_queue_history(kind: str = None, source: str = None, status: str = None,
                      limit: int = 50):
    """Persistent completion history for the unified queue — what finished, what
    it was, when, and which system asked for it. Survives restarts.

    LLM rows come from the queue_history table (written by the orchestrator at
    every terminal transition). Media jobs (image/video/audio/3D) already keep
    their lifecycle in their own tables, so they are unioned in here at read
    time (source='studio') instead of being double-written. Includes a small
    counts-by-source/status summary over the last 24h.
    """
    limit = max(1, min(int(limit or 50), 200))
    import nsfw as _nsfw
    _show_nsfw = _nsfw.display_on()
    items = []
    conn = get_conn()
    try:
        # ── LLM history (the persistent table) ────────────────────────────────
        for r in conn.execute(
                "SELECT id, kind, label, task, source, model, status, error,"
                "       enqueued_at, started_at, finished_at, duration_s"
                "  FROM queue_history ORDER BY id DESC LIMIT 400").fetchall():
            it = dict(r)
            if it.get("source") == "private" and not _show_nsfw:
                it.update(label=_nsfw.PRIVATE_LABEL, task=None, model=None, error=None)
            items.append(it)

        # ── Media jobs — already persisted in their own tables; union, don't copy ──
        for r in conn.execute("""
            SELECT id, 'image' AS kind, prompt AS label, model AS model, status, NULL AS error,
                   created_at, updated_at, COALESCE(nsfw,0) AS nsfw
              FROM generations WHERE status IN ('done','failed')
            UNION ALL
            SELECT id, 'video' AS kind, prompt AS label, model_id AS model, status, error,
                   created_at, updated_at, COALESCE(nsfw,0) AS nsfw
              FROM videos WHERE status IN ('done','failed','cancelled')
            UNION ALL
            SELECT id, 'video chain' AS kind, COALESCE(NULLIF(title,''), concept) AS label,
                   model_id AS model, status, error, created_at, updated_at, COALESCE(nsfw,0) AS nsfw
              FROM video_chains WHERE status IN ('done','failed','cancelled')
            UNION ALL
            SELECT id, 'audio' AS kind, prompt AS label, model_id AS model, status, error,
                   created_at, updated_at, COALESCE(nsfw,0) AS nsfw
              FROM audio_clips WHERE status IN ('done','failed','cancelled')
             ORDER BY updated_at DESC LIMIT 200
        """).fetchall():
            redact = bool(r["nsfw"]) and not _show_nsfw
            st = {"done": "done", "failed": "error", "cancelled": "cancelled"}[r["status"]]
            dur = None
            try:
                row = conn.execute(
                    "SELECT (julianday(?) - julianday(?)) * 86400.0 AS d",
                    (r["updated_at"], r["created_at"])).fetchone()
                if row and row["d"] is not None and 0 < row["d"] < 86400:
                    dur = round(row["d"], 1)
            except Exception:
                pass
            items.append({
                "id": r["id"],
                "kind": ("private" if redact else r["kind"]),
                "label": (_nsfw.PRIVATE_LABEL if redact
                          else (r["label"] or "").strip() or f'{r["kind"]} #{r["id"]}'),
                "task": None, "source": ("private" if redact else "studio"),
                "model": (None if redact else r["model"]), "status": st,
                "error": (None if redact else r["error"]),
                "enqueued_at": r["created_at"], "started_at": None,
                "finished_at": r["updated_at"], "duration_s": dur,
            })

        # ── Summary: counts by source/status over the last 24h ────────────────
        by_source, by_status = {}, {}
        for r in conn.execute(
                "SELECT source, status, COUNT(*) AS n FROM queue_history "
                "WHERE finished_at >= datetime('now','-1 day') "
                "GROUP BY source, status").fetchall():
            src = r["source"] or "other"
            by_source[src] = by_source.get(src, 0) + r["n"]
            by_status[r["status"]] = by_status.get(r["status"], 0) + r["n"]
        for tbl, terminal in (("generations", ("done", "failed")),
                              ("videos", ("done", "failed", "cancelled")),
                              ("video_chains", ("done", "failed", "cancelled")),
                              ("audio_clips", ("done", "failed", "cancelled"))):
            ph = ",".join("?" * len(terminal))
            for r in conn.execute(
                    f"SELECT status, COUNT(*) AS n FROM {tbl} "
                    f"WHERE status IN ({ph}) AND updated_at >= datetime('now','-1 day') "
                    f"GROUP BY status", terminal).fetchall():
                st = {"done": "done", "failed": "error", "cancelled": "cancelled"}[r["status"]]
                by_source["studio"] = by_source.get("studio", 0) + r["n"]
                by_status[st] = by_status.get(st, 0) + r["n"]
    finally:
        conn.close()

    # Filters + newest-first merge (finished_at is UTC text in one format everywhere).
    if kind:
        items = [i for i in items if i["kind"] == kind]
    if source:
        items = [i for i in items if i["source"] == source]
    if status:
        items = [i for i in items if i["status"] == status]
    items.sort(key=lambda i: i.get("finished_at") or "", reverse=True)
    return {
        "items": items[:limit],
        "summary": {"window_h": 24, "by_source": by_source, "by_status": by_status},
    }


def _node_guard_info() -> dict:
    """Node interactive-use state (Steam game etc.) from the gpu-guard heartbeat.
    Piggybacks the Dashboard's status poll to auto-resume a stale guard pause."""
    try:
        from routers import gpu_guard
        gpu_guard.maybe_unstick()
        return gpu_guard.guard_info()
    except Exception:
        return {"busy": False, "apps": [], "since": 0.0, "guard_paused": False}


@router.post("/api/queue/pause")
def queue_pause():
    """Pause the queue — no NEW GPU job (LLM/image/video) starts; running ones finish."""
    orch.pause()
    return {"ok": True, "paused": True}


@router.post("/api/queue/resume")
def queue_resume():
    """Resume (start) dispatching queued jobs."""
    orch.resume()
    return {"ok": True, "paused": False}


@router.post("/api/queue/clear")
def queue_clear():
    """Clear the queue: cancel every QUEUED (not-yet-running) job — orchestrator LLM
    tasks, DB media jobs still waiting, and ComfyUI's pending queue. Jobs already
    running on the GPU are left to finish."""
    cleared = {"llm": orch.clear_pending()}
    conn = get_conn()
    try:
        cur = conn.cursor()
        # Cancel only the WAITING media rows; the 'generating' ones are on the GPU now.
        for table, states in (("videos", ("queued",)),
                              ("video_chains", ("pending",)),
                              ("audio_clips", ("queued",))):
            placeholders = ",".join("?" * len(states))
            r = cur.execute(
                f"UPDATE {table} SET status='cancelled' WHERE status IN ({placeholders})",
                states)
            cleared[table] = r.rowcount
        conn.commit()
    finally:
        conn.close()
    # ComfyUI holds its own pending queue — clear it too (best-effort).
    try:
        httpx.post("http://127.0.0.1:8188/queue", json={"clear": True}, timeout=5)
        cleared["comfyui"] = "cleared"
    except Exception:
        cleared["comfyui"] = "unreachable"
    return {"ok": True, "cleared": cleared}


@router.get("/api/store-stats")
def get_combined_stats():
    """Combined Printify + Etsy stats for the dashboard."""
    result = {"printify": None, "etsy": None, "error": {}}
    conn = get_conn()
    s = _dec_secrets({r["key"]: r["value"] for r in conn.execute("SELECT key,value FROM settings").fetchall()})
    conn.close()
    # Printify
    pk = s.get("printify_key", "")
    ps = s.get("printify_shop_id", "")
    if pk and ps:
        try:
            result["printify"] = PrintifyClient(pk, ps).get_shop_stats()
        except Exception as e:
            result["error"]["printify"] = str(e)
    # Etsy
    ek      = s.get("etsy_key", "")
    etok    = s.get("etsy_access_token", "")
    etref   = s.get("etsy_refresh_token", "")
    eid     = s.get("etsy_shop_id", "")
    esecret = s.get("etsy_shared_secret", "")
    eexp    = int(s.get("etsy_token_expires", "0"))
    if ek and etok and eid:
        import etsy_client as _ec
        try:
            # Auto-refresh token if expired or expiring soon. Known-stale auth
            # short-circuits to the reconnect CTA instead of re-spamming Etsy.
            if time.time() >= eexp - 120 and etref:
                if _ec.auth_needs_reconnect():
                    raise EtsyAuthError("Etsy authorization expired")
                tokens = refresh_access_token(ek, etref, client_secret=esecret or None)
                etok   = tokens["access_token"]
                new_exp = int(time.time()) + tokens.get("expires_in", 3600)
                _c = get_conn()
                _c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_access_token", _enc(etok)))
                _c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_token_expires", str(new_exp)))
                _c.commit()
                _c.close()
            result["etsy"] = EtsyClient(ek, etok, eid, shared_secret=esecret).get_shop_stats()
        except EtsyAuthError:
            # Stale OAuth (logged once by etsy_client) — clean reconnect CTA, no raw 400.
            result["error"]["etsy"] = "Etsy authorization expired — reconnect Etsy."
            result["etsy_reconnect"] = True
        except Exception as e:
            err_str = str(e)
            if "401" in err_str or "Unauthorized" in err_str:
                err_str = "Etsy token expired — please reconnect in Settings."
                result["etsy_reconnect"] = True
            result["error"]["etsy"] = err_str
    return result

"""
Social auto-scheduler + post-performance analytics.

Two jobs, one background thread (started once from main.py, same daemon pattern
as world_auto):

  1) SCHEDULER — social_posts has always had a "schedule (optional)" field that
     was only a reminder. This loop makes it real: every tick it finds posts
     whose status is 'scheduled', whose scheduled_at is due, and publishes them
     through the EXISTING publish path (routers/social._PUBLISHERS + _do_publish
     — the exact same code the manual Publish button runs, so NSFW blocking,
     privacy defaults, TikTok audit downgrade etc. all still apply).

  2) ANALYTICS — after a post is live on YouTube/TikTok, refresh_analytics()
     pulls its performance (YouTube Data API videos.list?part=statistics;
     TikTok best-effort + gated — see tiktok.TikTokAdapter.stats) onto the
     social_publications row, then feeds the store's god-taste model
     (world_taste) so future proposals/prompts lean toward what actually
     performed.

SAFETY — everything defaults OFF / inert:
  • social_scheduler_enabled   "0"  master switch; loop is a no-op until "1".
  • only EXPLICITLY scheduled posts qualify (status='scheduled' + scheduled_at);
    drafts are never touched.
  • per-platform gates respected: the platform must be marked connected in the
    Social tab AND have stored OAuth tokens; everything else rides the manual
    publish path's own gates (NSFW hard-block, YouTube privacy default
    'private', TikTok SELF_ONLY until audited).
  • never double-posts: a post is CLAIMED (auto_state set) before anything is
    uploaded, and a claimed post is never picked again — success or failure.
    Re-arm explicitly via POST /api/social/posts/{id}/schedule-reset.
  • social_scheduler_dry_run   "0"  when "1", logs what WOULD publish and marks
    the post auto_state='dry_run' without uploading anything.
  • social_analytics_auto_hours "0" periodic analytics refresh; 0 = manual only
    (POST /api/social/analytics/refresh).
  • every action is logged (logger 'store' → Settings → Logs) and kept in a
    small ring buffer for GET /api/social/scheduler/status.
"""
import json
import logging
import math
import statistics
import threading
import time
from datetime import datetime

from deps import get_conn, get_setting

logger = logging.getLogger("store")

DEFAULTS = {
    "social_scheduler_enabled":      "0",    # master switch — OFF until you turn it on
    "social_scheduler_interval_sec": "60",   # how often the loop looks for due posts
    "social_scheduler_dry_run":      "0",    # 1 = log/mark only, upload nothing
    "social_analytics_auto_hours":   "0",    # auto stats refresh cadence; 0 = manual only
    "social_taste_feed":             "1",    # feed fetched stats into world_taste
    "social_taste_ref_views":        "50",   # engagement baseline until enough history exists
}

_state = {
    "thread": None,
    "last_tick": 0.0,
    "last_publish": None,     # summary of the most recent auto-publish (or dry-run)
    "last_analytics": 0.0,    # epoch of the last automatic analytics refresh
    "log": [],                # ring buffer of recent scheduler actions
}
_LOG_MAX = 50


def cfg(k):
    return get_setting(k, DEFAULTS.get(k))


def enabled() -> bool:
    return str(cfg("social_scheduler_enabled")).strip().lower() in ("1", "true", "yes", "on")


def dry_run() -> bool:
    return str(cfg("social_scheduler_dry_run")).strip().lower() in ("1", "true", "yes", "on")


def _interval_sec() -> int:
    try:
        return max(15, int(cfg("social_scheduler_interval_sec")))
    except Exception:
        return 60


def _note(msg: str):
    """Log an action + remember it for the status endpoint."""
    logger.info("social_scheduler: %s", msg)
    _state["log"].append({"at": datetime.now().isoformat(timespec="seconds"), "msg": msg})
    del _state["log"][:-_LOG_MAX]


def save_config(updates: dict, conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        for k, v in updates.items():
            if k in DEFAULTS:
                conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                             (k, str(v)))
        conn.commit()
    finally:
        if own:
            conn.close()


def _due_posts(conn):
    """Explicitly-scheduled, due, UNCLAIMED posts — the only things auto-post
    ever touches. auto_state IS NULL is the double-post guard: once a post is
    claimed (publishing/done/dry_run/failed:*) it is never picked again."""
    now = datetime.now()
    out = []
    rows = conn.execute(
        "SELECT * FROM social_posts WHERE status='scheduled' AND scheduled_at IS NOT NULL "
        "AND scheduled_at != '' AND auto_state IS NULL ORDER BY scheduled_at").fetchall()
    for r in rows:
        try:
            when = datetime.fromisoformat(str(r["scheduled_at"]).strip())
        except Exception:
            continue                     # unparseable date — leave it as a reminder
        if when <= now:
            out.append(r)
    return out


def status() -> dict:
    conn = get_conn()
    try:
        due = len(_due_posts(conn))
    except Exception:
        due = 0
    finally:
        conn.close()
    return {
        "enabled": enabled(),
        "dry_run": dry_run(),
        "interval_sec": _interval_sec(),
        "analytics_auto_hours": float(cfg("social_analytics_auto_hours") or 0),
        "taste_feed": str(cfg("social_taste_feed")) in ("1", "true", "on"),
        "running": bool(_state["thread"] and _state["thread"].is_alive()),
        "last_tick": _state["last_tick"],
        "due_posts": due,
        "last_publish": _state["last_publish"],
        "recent": list(reversed(_state["log"][-15:])),
    }


# ── the auto-publish cycle ───────────────────────────────────────────────────
def _eligible_platforms(post) -> tuple[list[str], list[str]]:
    """(publishable platforms, skip reasons) for a due post. A platform counts
    only when auto-publish supports it AND it's marked connected in the Social
    tab AND its adapter actually holds OAuth tokens."""
    from routers import social as _social
    plats, skipped = [], []
    for p in json.loads(post["platforms"] or "[]"):
        if p not in _social._PUBLISHERS:
            skipped.append(f"{p}: no auto-publish adapter")
            continue
        if get_setting(f"social_{p}_connected", "") != "1":
            skipped.append(f"{p}: not marked connected")
            continue
        if not _social._PUBLISHERS[p]["adapter"].connected():
            skipped.append(f"{p}: no OAuth tokens stored")
            continue
        plats.append(p)
    return plats, skipped


def _mark(conn, pid: int, state: str):
    conn.execute("UPDATE social_posts SET auto_state=?, updated_at=datetime('now') WHERE id=?",
                 (state[:200], pid))
    conn.commit()


def _publish_due_post(post) -> dict:
    """Publish ONE due post via the existing manual-publish machinery.
    Runs in the scheduler thread; the post is claimed before any upload."""
    from routers import social as _social
    pid = post["id"]
    conn = get_conn()
    try:
        # CLAIM first — whatever happens next, this post is never re-picked.
        cur = conn.execute("UPDATE social_posts SET auto_state='publishing', "
                           "updated_at=datetime('now') WHERE id=? AND auto_state IS NULL", (pid,))
        conn.commit()
        if cur.rowcount != 1:
            return {"post": pid, "skipped": "already claimed"}

        plats, skipped = _eligible_platforms(post)
        if not plats:
            _mark(conn, pid, "failed: no connected auto-publish platform "
                             f"({'; '.join(skipped) or 'none on post'})")
            _note(f"post {pid} due but SKIPPED — {'; '.join(skipped) or 'no platforms'}")
            return {"post": pid, "error": "no eligible platform", "skipped": skipped}

        video_path, dims, nsfw = _social._resolve_post_video(conn, post)
        if not video_path:
            _mark(conn, pid, "failed: no publishable video attached")
            _note(f"post {pid} due but SKIPPED — no publishable video attached")
            return {"post": pid, "error": "no video"}
        if nsfw:
            # Same HARD refusal as manual publish: Private Studio media never auto-posts.
            _mark(conn, pid, "failed: video is NSFW (blocked)")
            _note(f"post {pid} due but BLOCKED — attached video is NSFW")
            return {"post": pid, "error": "nsfw blocked"}

        if dry_run():
            _mark(conn, pid, "dry_run")
            _note(f"DRY RUN: would publish post {pid} ({(post['title'] or post['caption'] or '')[:40]!r}) "
                  f"to {', '.join(plats)} — video {video_path}")
            return {"post": pid, "dry_run": True, "platforms": plats}

        # Same rows + same worker the Publish button uses (status/URL/error land
        # on social_publications; _do_publish flips the post to posted when done).
        jobs = []
        for plat in plats:
            meta = _social._PUBLISHERS[plat]["meta"](post, dims)
            existing = conn.execute(
                "SELECT * FROM social_publications WHERE post_id=? AND platform=?",
                (pid, plat)).fetchone()
            if existing and existing["status"] == "live":
                continue          # belt & braces: never re-post something already live
            if existing:
                conn.execute("UPDATE social_publications SET status='pending', error=NULL, "
                             "updated_at=datetime('now') WHERE id=?", (existing["id"],))
                pub_id = existing["id"]
            else:
                pub_id = conn.execute(
                    "INSERT INTO social_publications (post_id, platform, status) "
                    "VALUES (?, ?, 'pending')", (pid, plat)).lastrowid
            conn.commit()
            jobs.append((plat, pub_id, meta))
    finally:
        conn.close()

    results = []
    for plat, pub_id, meta in jobs:
        _note(f"auto-publishing post {pid} → {plat} (publication {pub_id})")
        _social._do_publish(plat, pub_id, video_path, meta)   # sync in this thread
        conn = get_conn()
        row = conn.execute("SELECT status, error, platform_url FROM social_publications "
                           "WHERE id=?", (pub_id,)).fetchone()
        conn.close()
        results.append({"platform": plat, "publication_id": pub_id,
                        "status": row["status"] if row else "?",
                        "url": row["platform_url"] if row else None,
                        "error": row["error"] if row else None})

    ok = [r for r in results if r["status"] == "live"]
    bad = [r for r in results if r["status"] != "live"]
    conn = get_conn()
    if bad:
        _mark(conn, pid, "failed: " + "; ".join(f"{r['platform']}: {r['error'] or r['status']}"
                                                for r in bad))
    else:
        _mark(conn, pid, "done")
    conn.close()
    _note(f"post {pid} auto-publish finished — {len(ok)} live, {len(bad)} failed")
    return {"post": pid, "results": results}


def run_once() -> dict:
    """One scheduler pass (also the manual 'run now' endpoint). Honors every gate."""
    _state["last_tick"] = time.time()
    if not enabled():
        return {"ok": False, "skipped": "scheduler disabled (social_scheduler_enabled=0)"}
    conn = get_conn()
    try:
        due = _due_posts(conn)
    finally:
        conn.close()
    out = []
    for post in due[:3]:                 # bounded per tick — uploads are slow
        try:
            res = _publish_due_post(post)
        except Exception as e:
            logger.exception("social_scheduler: post %s failed", post["id"])
            try:
                c2 = get_conn()
                _mark(c2, post["id"], f"failed: {e}")
                c2.close()
            except Exception:
                pass
            res = {"post": post["id"], "error": str(e)}
        out.append(res)
    if out:
        _state["last_publish"] = {"at": datetime.now().isoformat(timespec="seconds"),
                                  "results": out}
    return {"ok": True, "due": len(due), "processed": out}


# ═════════════════════════════════════════════════════════════════════════════
# Analytics → taste model
# ═════════════════════════════════════════════════════════════════════════════
def _engagement(st: dict) -> float:
    """One comparable number per publication. Weights favour active engagement
    over passive views (a like is worth ~5 views, a comment/share more)."""
    return (float(st.get("views") or 0) + 5.0 * float(st.get("likes") or 0)
            + 10.0 * float(st.get("comments") or 0) + 5.0 * float(st.get("shares") or 0))


def _label(eng: float, ref: float) -> float:
    """Map engagement → a world_taste label in [-1, +1], relative to the
    platform's own median (log2 scale: 4× the median ⇒ +1, ¼ ⇒ -1, median ⇒ 0).
    Honest with tiny channels: everything is judged against ITS OWN baseline."""
    ref = max(ref, 1.0)
    return max(-1.0, min(1.0, math.log2((eng + 1.0) / (ref + 1.0)) / 2.0))


def _feed_taste(conn) -> int:
    """Feed every stats-bearing publication into world_taste as a labelled
    example (skey social:<platform>:<pub_id>), so world_creators' prompt picks,
    world_ops endorsements and world_sell listing choices — everything that
    calls world_taste.score() — lean toward content that actually performed."""
    if str(cfg("social_taste_feed")).strip().lower() not in ("1", "true", "yes", "on"):
        return 0
    import world_taste
    rows = conn.execute(
        "SELECT p.id, p.platform, p.stats, sp.title, sp.caption, sp.hashtags, "
        "       (julianday('now') - julianday(p.created_at)) * 24.0 AS age_hours "
        "FROM social_publications p JOIN social_posts sp ON sp.id = p.post_id "
        "WHERE p.stats IS NOT NULL AND p.status='live'").fetchall()
    by_platform: dict[str, list] = {}
    parsed = []
    for r in rows:
        try:
            st = json.loads(r["stats"] or "{}")
        except Exception:
            continue
        if not st.get("available"):
            continue
        eng = _engagement(st)
        # GRACE WINDOW: zero engagement on a fresh post is "too early to tell",
        # not a verdict — don't teach the taste model a -1 it hasn't earned.
        if eng <= 0 and float(r["age_hours"] or 0) < 48:
            continue
        parsed.append((r, eng))
        by_platform.setdefault(r["platform"], []).append(eng)
    try:
        default_ref = float(cfg("social_taste_ref_views") or 50)
    except Exception:
        default_ref = 50.0
    n = 0
    for r, eng in parsed:
        peers = by_platform.get(r["platform"]) or []
        ref = statistics.median(peers) if len(peers) >= 3 else default_ref
        label = _label(eng, ref)
        text = " ".join(x for x in (r["title"], r["caption"], r["hashtags"]) if x).strip()
        if not text:
            continue
        n += world_taste.add_or_update_example(
            conn, f"social:{r['platform']}:{r['id']}", "social", text, label,
            "social_analytics")
    if n:
        conn.commit()
        logger.info("social_scheduler: taste model updated from %d publication(s)", n)
    return n


def refresh_analytics(platform: str | None = None, post_id: int | None = None) -> dict:
    """Fetch performance stats for live publications and store them on the row,
    then feed the taste model. Per-row failures degrade gracefully (recorded,
    never raised) — TikTok stats especially may be unavailable for an unaudited
    app (Display API scope video.list; gated by tiktok_stats_enabled)."""
    from routers import social as _social
    conn = get_conn()
    q = "SELECT * FROM social_publications WHERE status='live' AND platform_post_id IS NOT NULL"
    args: list = []
    if platform:
        q += " AND platform=?"; args.append(platform)
    if post_id:
        q += " AND post_id=?"; args.append(post_id)
    rows = conn.execute(q, args).fetchall()
    results, refreshed = [], 0
    for r in rows:
        plat = r["platform"]
        entry = {"publication_id": r["id"], "post_id": r["post_id"], "platform": plat}
        pub = _social._PUBLISHERS.get(plat)
        if not pub or not hasattr(pub["adapter"], "stats"):
            entry["detail"] = "no stats support"
            results.append(entry)
            continue
        try:
            if plat == "tiktok":
                # the public video ids live in upload_state.post_ids (publish_id
                # alone is not a video id the Display API can query)
                try:
                    ids = (json.loads(r["upload_state"] or "{}") or {}).get("post_ids") or []
                except Exception:
                    ids = []
                st = pub["adapter"].stats(r["platform_post_id"], post_ids=ids)
            else:
                st = pub["adapter"].stats(r["platform_post_id"])
        except Exception as e:      # best-effort by contract — never break the pipeline
            st = {"available": False, "detail": f"stats fetch failed: {e}"}
        entry.update(st)
        if st.get("available"):
            conn.execute("UPDATE social_publications SET stats=?, stats_at=datetime('now'), "
                         "updated_at=datetime('now') WHERE id=?",
                         (json.dumps(st), r["id"]))
            conn.commit()
            refreshed += 1
        results.append(entry)
    fed = 0
    try:
        fed = _feed_taste(conn)
    except Exception:
        logger.exception("social_scheduler: taste feed failed")
    conn.close()
    if refreshed or fed:
        _note(f"analytics refresh: {refreshed}/{len(rows)} publication(s) updated, "
              f"{fed} taste example(s) fed")
    return {"ok": True, "publications": len(rows), "refreshed": refreshed,
            "taste_fed": fed, "results": results}


# ── background loop ──────────────────────────────────────────────────────────
def _loop():
    time.sleep(25)                       # let the app settle (same as world_auto)
    while True:
        try:
            if enabled():
                run_once()
                # optional periodic analytics refresh (default OFF: 0 hours)
                try:
                    hours = float(cfg("social_analytics_auto_hours") or 0)
                except Exception:
                    hours = 0.0
                if hours > 0 and (time.time() - _state["last_analytics"]) >= hours * 3600:
                    _state["last_analytics"] = time.time()
                    try:
                        refresh_analytics()
                    except Exception:
                        logger.exception("social_scheduler: auto analytics refresh failed")
            else:
                _state["last_tick"] = time.time()
        except Exception:
            logger.exception("social_scheduler loop tick failed")
        time.sleep(_interval_sec())


def start():
    if _state["thread"]:
        return
    t = threading.Thread(target=_loop, daemon=True, name="social-scheduler")
    _state["thread"] = t
    t.start()
    logger.info("social_scheduler started (enabled=%s, dry_run=%s)", enabled(), dry_run())

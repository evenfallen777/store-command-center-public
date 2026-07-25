"""Persistent completion history for the unified GPU queue.

The orchestrator's task dict (orchestrator.py `self._tasks`) is in-memory, so
completed work vanished from the queue display the moment it finished — and
everything was lost on restart. Every LLM task now writes ONE row to the
`queue_history` table at its terminal transition (done | error | cancelled);
the single write path is Orchestrator._record_history → record() here.

Media jobs (image/video/audio/3D) already persist their full lifecycle in
generations/videos/video_chains/audio_clips, so they are NOT duplicated here —
GET /api/queue/history (routers/dashboard.py) unions them in at read time.

Source attribution: callers already tag submissions via desc prefixes
("proxy:", "world:", "jelly:mission-draft", "swarm turn", …) and the prompt-
registry `task` key. derive_source() maps those to a system/feature name
server-side, so no caller changes are required; an explicit `source=` kwarg on
submit_llm wins when provided.

Logging must NEVER break the job itself — record() swallows every exception.
"""
import logging
import time
from datetime import datetime, timezone

log = logging.getLogger("queue-history")

HISTORY_MAX = 2000   # retention cap — oldest rows pruned on insert


# ── Source attribution ────────────────────────────────────────────────────────
# 1) prompt-registry task key → system (exact match, then prefix)
_TASK_MAP = {
    "image_enhance": "studio", "image_research": "studio",
    "listing_copy": "studio", "pricing": "studio",
    "video_chain": "studio", "audio_music": "studio", "audio_voice": "studio",
    "threed_listing": "3d", "threed_enhance": "3d",
    "social_caption": "social", "mail_quote": "mail", "mail_classify": "mail",
    "security_analyze": "security",
}
_TASK_PREFIX = (
    ("library_", "library"), ("money_", "money"), ("jelly", "jellycoin"),
    ("world", "world"), ("research", "research"), ("nsfw", "private"),
    ("image_", "studio"), ("audio_", "studio"), ("video_", "studio"),
    ("threed_", "3d"), ("security_", "security"),
)
# 2) desc written as "<system>:<detail>" → system
_DESC_PREFIX = {
    "proxy": "proxy", "world": "world", "jelly": "jellycoin",
    "research": "research", "rip": "library",
}
# 3) recognisable substrings of a lowercased desc → system (order matters)
_DESC_KEYWORDS = (
    ("private studio", "private"), ("assistant agent", "assistant"),
    ("swarm", "dev-swarm"), ("peer", "peers"), ("trend scan", "trends"),
    ("haggle", "resell"), ("marketplace", "resell"), ("resell", "resell"),
    ("stocks", "crypto"), ("ft strategy", "crypto"), ("hunt gen", "crypto"),
    ("oracle", "oracle"), ("forecast", "oracle"), ("quote", "mail"),
    ("chain prompts", "studio"), ("price suggest", "studio"),
    ("listing", "studio"), ("enhance", "studio"), ("research", "research"),
)


def derive_source(desc: str = "", task: str = None, explicit: str = None) -> str:
    """System/feature name for a queue submission. Explicit wins; then the
    prompt-registry task key; then desc prefix/keywords; fallback = first token."""
    if explicit:
        return str(explicit)
    t = (task or "").strip().lower()
    if t:
        if t in _TASK_MAP:
            return _TASK_MAP[t]
        for pre, src in _TASK_PREFIX:
            if t.startswith(pre):
                return src
    d = (desc or "").strip().lower()
    if ":" in d:
        head = d.split(":", 1)[0].strip()
        if head in _DESC_PREFIX:
            return _DESC_PREFIX[head]
    for kw, src in _DESC_KEYWORDS:
        if kw in d:
            return src
    first = d.split(":", 1)[0].split()[0] if d else ""
    return first.rstrip(":") or "other"


# ── Writer ────────────────────────────────────────────────────────────────────

def _iso(ts) -> str:
    """Unix seconds → 'YYYY-MM-DD HH:MM:SS' UTC (matches sqlite datetime('now'))."""
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def record(kind: str, label: str, status: str, *, task: str = None,
           source: str = None, model: str = None, error: str = None,
           enqueued_at: float = None, started_at: float = None,
           finished_at: float = None):
    """Insert one terminal row (+ prune past HISTORY_MAX). Never raises."""
    try:
        from db import get_conn
        fin = finished_at or time.time()
        dur = round(max(0.0, fin - started_at), 2) if started_at else None
        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO queue_history
                       (kind, label, task, source, model, status, error,
                        enqueued_at, started_at, finished_at, duration_s)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (kind, (label or "")[:300], task,
                 derive_source(label, task, source), model, status,
                 (str(error)[:500] if error else None),
                 _iso(enqueued_at), _iso(started_at), _iso(fin), dur))
            # Retention: cap the table; cheap enough to run on every insert.
            conn.execute(
                "DELETE FROM queue_history WHERE id NOT IN "
                "(SELECT id FROM queue_history ORDER BY id DESC LIMIT ?)",
                (int(HISTORY_MAX),))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:                                    # noqa: BLE001
        log.debug("queue_history.record skipped: %s", e)

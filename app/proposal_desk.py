"""Proposal desk — multi-lane, multi-source proposal origination.

The intake side of the funnel the review gate (proposal_gate.py) weeds:

  feeds (Google/Reddit/RSS + categorized news groups)  ─┐
  the crew's "Suggested:" ideas (world_suggestions)     ├─→ proposals (lane-tagged)
  the Etsy demand signal (etsy_signal.py)               ─┘        │
                                                            proposal_gate → generations

Two jobs, both scheduler-driven (scheduler.py, every proposal_desk_interval_min,
master switch proposal_desk_enabled — also a Company control-plane system, so
the company master pause stops it like everything else):

  • harvest_suggestions(): world_agents crew members (Trent the trend scout,
    Etta on the storefront, the w_res_* researchers, the oracle_* forecasters)
    already emit "Suggested: …" improvement ideas via world_gov.generate_opinion
    into the world_suggestions table. Qualifying product/marketing suggestions
    become real `proposals` rows (source='agent', lane from the agent's dept),
    tracked by a high-water mark so nothing is double-imported. No LLM call.

  • desk_tick(): harvest + keep the Etsy demand snapshot warm + kick the
    multi-lane trend scan (services._run_trend_scan) if one isn't running.

Import-safe: module level touches only stdlib + db; deps/services are imported
lazily inside functions (same pattern as proposal_gate.py).
"""
import logging
import re
import threading
import time

from db import get_conn

log = logging.getLogger("store")

# agent dept → the lane its suggestions land in (trends.LANES ids)
DEPT_LANE = {
    "trends":     "news",        # Trent watches the zeitgeist
    "research":   "evergreen",   # the w_res_* researchers dig durable niches
    "storefront": "market",      # Etta lives in the Etsy numbers
    "devlab":     "tech",        # oracles + engineers
    "image":      "humor",
    "video":      "humor",
    "social":     "humor",
}


# ── settings (same helpers as proposal_gate) ─────────────────────────────────
def _setting(key, default):
    try:
        conn = get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        if row and row["value"] not in (None, ""):
            return row["value"]
    except Exception:
        pass
    return default


def _int_setting(key, default):
    try:
        return int(float(_setting(key, str(default))))
    except Exception:
        return default


def _save(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()


def enabled() -> bool:
    return str(_setting("proposal_desk_enabled", "0")).strip().lower() in ("1", "true", "on", "yes")


def lanes_enabled() -> list[str]:
    from trends import parse_lanes_enabled
    return parse_lanes_enabled(_setting("proposal_lanes_enabled", ""))


def dept_lane(dept) -> str:
    return DEPT_LANE.get((dept or "").strip().lower(), "evergreen")


# ── agent / oracle / researcher suggestions → proposals ──────────────────────
def _recent_title_words(conn) -> list[set]:
    return [set((r["title"] or "").lower().split()) for r in conn.execute(
        "SELECT title FROM proposals WHERE created_at > datetime('now','-14 days')"
    ).fetchall()]


# products/marketing suggestions always qualify; LLM-authored ones land as
# category 'ops' (world_gov.generate_opinion), so those qualify only when the
# text is clearly a PRODUCT idea, not a process tweak.
_PRODUCT_HINT = re.compile(
    r"\b(design|shirt|t-?shirt|tee|hoodie|mug|poster|sticker|merch|product|listing|"
    r"collection|theme|niche|graphic|apparel|tote|print)\b", re.I)


def qualifies(text: str, category: str) -> bool:
    text = " ".join((text or "").split())
    if len(text.split()) < 4:
        return False
    if category in ("products", "marketing"):
        return True
    return bool(_PRODUCT_HINT.search(text))


def harvest_suggestions(limit: int = 12) -> dict:
    """Turn qualifying crew suggestions into proposal rows (see qualifies()).
    High-water mark (proposal_desk_last_suggestion_id) makes this idempotent —
    each suggestion is considered exactly once. No LLM call."""
    conn = get_conn()
    last = _int_setting("proposal_desk_last_suggestion_id", 0)
    rows = conn.execute(
        "SELECT s.id, s.text, s.category, a.name, a.dept, a.job_class "
        "FROM world_suggestions s LEFT JOIN world_agents a ON a.key = s.agent_key "
        "WHERE s.id > ? ORDER BY s.id ASC LIMIT ?", (last, limit)).fetchall()
    recent = _recent_title_words(conn)
    inserted, max_id = 0, last
    for r in rows:
        max_id = max(max_id, r["id"])
        text = " ".join((r["text"] or "").split())
        if not qualifies(text, r["category"]):
            continue
        words = set(text.lower().split())
        if any(len(words & rt) >= 4 for rt in recent):
            continue                                   # crew re-suggested a known concept
        lane = dept_lane(r["dept"])
        who = r["name"] or "The crew"
        label = f"{who} ({r['dept']})" if r["dept"] else who
        conn.execute(
            "INSERT INTO proposals (title,description,source,source_label,tags,lane) "
            "VALUES (?,?,?,?,?,?)",
            (text[:120], text[:400], "agent", label[:60], "T-Shirt", lane))
        recent.append(words)
        inserted += 1
    if max_id != last:
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                     ("proposal_desk_last_suggestion_id", str(max_id)))
    conn.commit()
    conn.close()
    if inserted:
        log.info("proposal desk: %d crew suggestion(s) became proposals", inserted)
    return {"scanned": len(rows), "inserted": inserted}


# ── scheduler entry ───────────────────────────────────────────────────────────
def desk_tick() -> dict:
    """Scheduler entry (scheduler.py, every proposal_desk_interval_min).
    Harvest crew suggestions, keep the Etsy demand snapshot warm, and kick the
    multi-lane feed scan (unless one is already running or proposal_desk_scan=0)."""
    if not enabled():
        return {"skipped": "disabled"}
    out = {"suggestions": harvest_suggestions()}
    try:
        import etsy_signal
        etsy_signal.snapshot()                     # refreshes only when stale (TTL)
    except Exception as e:
        log.warning("proposal desk: etsy signal refresh failed: %s", e)
    if str(_setting("proposal_desk_scan", "1")).strip() == "1":
        try:
            import services
            if services._trend_scan.get("status") != "running":
                threading.Thread(target=services._run_trend_scan, daemon=True,
                                 name="desk-trend-scan").start()
                out["scan"] = "started"
            else:
                out["scan"] = "already_running"
        except Exception as e:
            log.warning("proposal desk: trend scan kick failed: %s", e)
            out["scan"] = f"failed: {e}"
    _save("proposal_desk_last_run", time.time())
    return out

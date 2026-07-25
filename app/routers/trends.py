"""trends routes."""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Form, UploadFile, File
from deps import *
from services import *

router = APIRouter()


@router.get("/api/trends/status")
def trend_status():
    return _trend_scan

@router.get("/api/trends/config")
def trend_config():
    conn = get_conn()
    rows = conn.execute("SELECT key,value FROM settings WHERE key LIKE 'trend_%'").fetchall()
    lanes_row = conn.execute("SELECT value FROM settings WHERE key='proposal_lanes_enabled'").fetchone()
    conn.close()
    cfg = {r["key"]: r["value"] for r in rows}
    fg = feed_group_config(cfg)
    return {
        "google_enabled":  cfg.get("trend_google_enabled",  "true") == "true",
        "reddit_enabled":  cfg.get("trend_reddit_enabled",  "true") == "true",
        "rss_enabled":     cfg.get("trend_rss_enabled",     "true") == "true",
        "google_region":   cfg.get("trend_google_region",   "US"),
        "reddit_subs":     cfg.get("trend_reddit_subs",     ",".join(DEFAULT_SUBS)),
        "rss_urls":        cfg.get("trend_rss_urls",        "\n".join(DEFAULT_RSS_FEEDS)),
        "last_run":        cfg.get("trend_last_run",        ""),
        "last_count":      int(cfg.get("trend_last_count",  "0")),
        # categorized feed groups (world/local/USA/game/tech news — extensible)
        "feeds": {cat: {"label": g["label"], "icon": g["icon"],
                        "enabled": g["enabled"], "urls": "\n".join(g["urls"])}
                  for cat, g in fg.items()},
        # proposal lanes (which themed generators run on a scan)
        "lanes": [{"id": l["id"], "label": l["label"]} for l in LANES],
        "lanes_enabled": ",".join(parse_lanes_enabled(lanes_row["value"] if lanes_row else "")),
    }

@router.patch("/api/trends/config")
def save_trend_config(data: dict):
    conn = get_conn()
    mapping = {
        "google_enabled":  "trend_google_enabled",
        "reddit_enabled":  "trend_reddit_enabled",
        "rss_enabled":     "trend_rss_enabled",
        "google_region":   "trend_google_region",
        "reddit_subs":     "trend_reddit_subs",
        "rss_urls":        "trend_rss_urls",
        "lanes_enabled":   "proposal_lanes_enabled",
    }
    for cat in FEED_GROUPS:                       # feed_world_news_enabled / _urls, …
        mapping[f"feed_{cat}_enabled"] = f"trend_feed_{cat}_enabled"
        mapping[f"feed_{cat}_urls"] = f"trend_feed_{cat}_urls"
    for k, dbk in mapping.items():
        if k in data:
            v = data[k]
            if isinstance(v, bool):
                v = "true" if v else "false"
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (dbk, str(v)))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.post("/api/trends/scan")
def trigger_trend_scan(background_tasks: BackgroundTasks):
    if _trend_scan["status"] == "running":
        return {"ok": False, "message": "Scan already running"}
    background_tasks.add_task(_run_trend_scan)
    return {"ok": True, "message": "Scan started"}

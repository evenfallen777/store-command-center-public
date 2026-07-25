"""Social — draft & schedule posts for Instagram / TikTok / YouTube / Facebook.

Phase 1 (now): a compose + queue workflow. You write (or LLM-generate) a caption,
attach one of your generated images/videos, pick platforms, and schedule it. Then
"copy caption → open the app → mark posted". No platform API keys required.

Phase 2 (now, YouTube first): real auto-posting. Google OAuth (connect/callback/
disconnect mirroring routers/etsy.py), then POST /api/social/posts/{id}/publish
runs the resumable upload in the background via app/social_publish/youtube.py and
tracks per-platform results in the social_publications table. TikTok Content
Posting (Direct Post) now rides the same adapter interface via
app/social_publish/tiktok.py — SELF_ONLY-gated until the app passes TikTok's
audit. Meta Graph (Instagram Reels + Facebook Page video) rides the same
interface via app/social_publish/instagram.py — one Meta OAuth connect covers
both platforms; publishes are PUBLIC (Meta offers no privacy knob).
"""
import json as _json
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from deps import *          # get_conn, get_setting, orch, _call_lmstudio, config
from services import *      # (kept consistent with sibling routers)

from social_publish import youtube as _yt_pub
from social_publish import tiktok as _tt_pub
from social_publish import instagram as _ig_pub

router = APIRouter()

# Static platform catalog. `auto` is the Phase-2 auto-post status (planned|beta|live).
PLATFORMS = {
    "instagram": {"name": "Instagram", "icon": "\U0001F4F8", "caption_limit": 2200,
                  "media": ["image", "video"], "aspect": "square / 4:5 / 9:16 reel",
                  "upload_url": "https://www.instagram.com/", "auto": "beta",
                  "api": "Meta Graph API (Reels via IG Business account; posts are public)"},
    "tiktok":    {"name": "TikTok", "icon": "\U0001F3B5", "caption_limit": 2200,
                  "media": ["video"], "aspect": "9:16 vertical video",
                  "upload_url": "https://www.tiktok.com/upload", "auto": "beta",
                  "api": "TikTok Content Posting API (direct post; SELF_ONLY until audited)"},
    "youtube":   {"name": "YouTube", "icon": "\U0001F3AC", "caption_limit": 5000,
                  "media": ["video"], "aspect": "16:9 or 9:16 Shorts",
                  "upload_url": "https://studio.youtube.com/", "auto": "beta",
                  "api": "YouTube Data API v3 (Google OAuth)"},
    "facebook":  {"name": "Facebook", "icon": "\U0001F310", "caption_limit": 63206,
                  "media": ["image", "video"], "aspect": "any",
                  "upload_url": "https://www.facebook.com/", "auto": "beta",
                  "api": "Meta Graph API (Page video; connects with Instagram)"},
}


def _row(r) -> dict:
    d = dict(r)
    d["platforms"] = _json.loads(d.get("platforms") or "[]")
    d["posted_on"] = _json.loads(d.get("posted_on") or "[]")
    try:
        d["per_platform"] = _json.loads(d.get("per_platform") or "{}")
    except Exception:
        d["per_platform"] = {}
    return d


# ── platform catalog + per-platform connection settings ─────────────────────
@router.get("/api/social/platforms")
def social_platforms():
    out = []
    for key, meta in PLATFORMS.items():
        out.append({
            "key": key, **meta,
            "handle": get_setting(f"social_{key}_handle", "") or "",
            "connected": (get_setting(f"social_{key}_connected", "") == "1"),
        })
    return {"platforms": out}


class SocialConfigIn(BaseModel):
    platform: str
    handle: Optional[str] = None
    connected: Optional[bool] = None


@router.post("/api/social/config")
def social_config(cfg: SocialConfigIn):
    if cfg.platform not in PLATFORMS:
        raise HTTPException(400, f"Unknown platform '{cfg.platform}'.")
    conn = get_conn()
    if cfg.handle is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                     (f"social_{cfg.platform}_handle", cfg.handle.strip()))
    if cfg.connected is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                     (f"social_{cfg.platform}_connected", "1" if cfg.connected else "0"))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── posts CRUD ──────────────────────────────────────────────────────────────
class SocialPostIn(BaseModel):
    title: Optional[str] = ""
    caption: Optional[str] = ""
    hashtags: Optional[str] = ""
    platforms: list[str] = []
    media_type: Optional[str] = "none"
    media_path: Optional[str] = ""
    media_url: Optional[str] = ""
    status: Optional[str] = "draft"       # draft | scheduled | posted
    scheduled_at: Optional[str] = None
    notes: Optional[str] = ""
    source: Optional[str] = "manual"
    # Phase-0 publishing linkage: which generated video this post publishes
    video_id: Optional[int] = None        # videos.id (single video)
    chain_id: Optional[int] = None        # video_chains.id (compiled chain)
    per_platform: Optional[dict] = None   # {youtube: {title, description, tags, privacy}, …}


@router.get("/api/social/posts")
def list_posts(status: Optional[str] = None):
    conn = get_conn()
    if status:
        rows = conn.execute("SELECT * FROM social_posts WHERE status=? ORDER BY "
                            "COALESCE(scheduled_at, updated_at) DESC", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM social_posts ORDER BY "
                            "CASE status WHEN 'scheduled' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END, "
                            "COALESCE(scheduled_at, updated_at) DESC").fetchall()
    conn.close()
    posts = [_row(r) for r in rows]
    counts = {"draft": 0, "scheduled": 0, "posted": 0}
    for p in posts:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    return {"posts": posts, "counts": counts}


@router.post("/api/social/posts")
def create_post(p: SocialPostIn):
    if not (p.caption or "").strip() and not (p.media_path or p.media_url):
        raise HTTPException(400, "Add a caption or attach media.")
    bad = [x for x in p.platforms if x not in PLATFORMS]
    if bad:
        raise HTTPException(400, f"Unknown platform(s): {', '.join(bad)}")
    status = p.status if p.status in ("draft", "scheduled", "posted") else "draft"
    if status == "scheduled" and not p.scheduled_at:
        status = "draft"
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO social_posts (title,caption,hashtags,platforms,media_type,media_path,
           media_url,status,scheduled_at,notes,source,video_id,chain_id,per_platform)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (p.title, p.caption, p.hashtags, _json.dumps(p.platforms), p.media_type or "none",
         p.media_path, p.media_url, status, p.scheduled_at, p.notes, p.source or "manual",
         p.video_id, p.chain_id, _json.dumps(p.per_platform) if p.per_platform else None))
    conn.commit()
    row = conn.execute("SELECT * FROM social_posts WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return _row(row)


@router.patch("/api/social/posts/{pid}")
def update_post(pid: int, p: SocialPostIn):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM social_posts WHERE id=?", (pid,)).fetchone():
        conn.close()
        raise HTTPException(404, "Post not found")
    status = p.status if p.status in ("draft", "scheduled", "posted") else "draft"
    if status == "scheduled" and not p.scheduled_at:
        status = "draft"
    conn.execute(
        """UPDATE social_posts SET title=?,caption=?,hashtags=?,platforms=?,media_type=?,
           media_path=?,media_url=?,status=?,scheduled_at=?,notes=?,video_id=?,chain_id=?,
           per_platform=?,updated_at=datetime('now')
           WHERE id=?""",
        (p.title, p.caption, p.hashtags, _json.dumps(p.platforms), p.media_type or "none",
         p.media_path, p.media_url, status, p.scheduled_at, p.notes, p.video_id, p.chain_id,
         _json.dumps(p.per_platform) if p.per_platform else None, pid))
    conn.commit()
    row = conn.execute("SELECT * FROM social_posts WHERE id=?", (pid,)).fetchone()
    conn.close()
    return _row(row)


class MarkPostedIn(BaseModel):
    platforms: Optional[list[str]] = None   # which platforms just went live; None = all on the post


@router.post("/api/social/posts/{pid}/mark-posted")
def mark_posted(pid: int, body: MarkPostedIn):
    conn = get_conn()
    row = conn.execute("SELECT * FROM social_posts WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Post not found")
    on_post = _json.loads(row["platforms"] or "[]")
    already = set(_json.loads(row["posted_on"] or "[]"))
    add = body.platforms if body.platforms else on_post
    already.update([x for x in add if x in on_post])
    all_done = set(on_post).issubset(already) and on_post
    conn.execute(
        "UPDATE social_posts SET posted_on=?, status=?, posted_at=?, updated_at=datetime('now') WHERE id=?",
        (_json.dumps(sorted(already)), "posted" if all_done else row["status"],
         datetime.now().isoformat(timespec="minutes") if all_done else row["posted_at"], pid))
    conn.commit()
    out = conn.execute("SELECT * FROM social_posts WHERE id=?", (pid,)).fetchone()
    conn.close()
    return _row(out)


@router.delete("/api/social/posts/{pid}")
def delete_post(pid: int):
    conn = get_conn()
    conn.execute("DELETE FROM social_posts WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── available generated media to attach ─────────────────────────────────────
@router.get("/api/social/media")
def social_media(kind: Optional[str] = None, limit: int = 60):
    """Generated images + videos you can attach to a post."""
    conn = get_conn()
    out = []
    if kind in (None, "image"):
        for r in conn.execute(
            "SELECT id, image_path, prompt FROM designs WHERE image_path IS NOT NULL AND image_path!='' "
            "AND COALESCE(nsfw,0)=0 "   # never expose Private Studio media in the social picker
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall():
            parts = str(r["image_path"]).split("/")
            fn = parts[-1]; sub = parts[-2] if len(parts) > 1 else "approved"
            out.append({"type": "image", "id": r["id"], "title": (r["prompt"] or fn)[:70],
                        "local_path": r["image_path"], "url": f"/designs/{sub}/{fn}"})
    if kind in (None, "video"):
        # single generated videos — prefer the audio-muxed copy when one exists
        for r in conn.execute(
            "SELECT id, video_path, audio_path, prompt FROM videos WHERE status='done' AND video_path IS NOT NULL AND video_path!='' "
            "AND COALESCE(nsfw,0)=0 "   # never expose Private Studio media in the social picker
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall():
            path = (r["audio_path"] or "").strip() or r["video_path"]
            fn = str(path).split("/")[-1]
            out.append({"type": "video", "id": r["id"], "title": (r["prompt"] or fn)[:70],
                        "local_path": path, "url": f"/videos/{fn}", "video_id": r["id"]})
        # compiled multi-segment chains (video_chains.compiled_path), nsfw-filtered too
        for r in conn.execute(
            "SELECT id, compiled_path, title, concept FROM video_chains "
            "WHERE compiled_path IS NOT NULL AND compiled_path!='' AND COALESCE(nsfw,0)=0 "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall():
            fn = str(r["compiled_path"]).split("/")[-1]
            out.append({"type": "video", "id": r["id"],
                        "title": ("Chain: " + (r["title"] or r["concept"] or fn))[:70],
                        "local_path": r["compiled_path"], "url": f"/videos/{fn}", "chain_id": r["id"]})
    conn.close()
    return {"count": len(out), "media": out}


# ── LLM caption + hashtag helper ────────────────────────────────────────────
class CaptionIn(BaseModel):
    topic: str
    platforms: list[str] = []
    tone: Optional[str] = "fun, playful, on-brand for Acme"


@router.post("/api/social/generate")
def generate_caption(req: CaptionIn):
    topic = (req.topic or "").strip()
    if not topic:
        raise HTTPException(400, "topic required")
    plats = ", ".join(PLATFORMS[x]["name"] for x in req.platforms if x in PLATFORMS) or "Instagram, TikTok"
    SYS = ("You are the social media manager for Acme, a playful indie shop selling geeky "
           "graphic tees, 3D-printable models, free software, and curated gadget deals. Write ONE "
           f"short, scroll-stopping caption for {plats} in a {req.tone} tone, then 8-12 relevant "
           "hashtags. Keep the caption under 300 characters, add 1-3 tasteful emoji. "
           'Return STRICT JSON: {"caption": "...", "hashtags": "#a #b #c"} and nothing else.')
    def _work():
        import re as _re
        raw = _call_lmstudio(get_prompt('social_caption').format(plats=plats, tone=req.tone),
                             topic, max_tokens=400, json_mode=True)
        raw = _re.sub(r"<think>.*?</think>", "", raw, flags=_re.DOTALL).strip()
        try:
            data = _json.loads(raw)
            cap = str(data.get("caption", "")).strip()
            tags = data.get("hashtags", "")
            tags = " ".join(tags) if isinstance(tags, list) else str(tags).strip()
        except Exception:
            # model didn't return clean JSON — salvage: first block = caption, #-words = tags
            cap = _re.sub(r"#\w+", "", raw).strip().strip('"')[:300]
            tags = " ".join(_re.findall(r"#\w+", raw))
        return {"caption": cap, "hashtags": tags, "topic": topic}
    tid = orch.submit_llm(_work, desc=f"Social caption: {topic[:40]}", task="social_caption")
    return {"task_id": tid}


# ═════════════════════════════════════════════════════════════════════════════
# YouTube auto-publishing (Phase 1) — Google OAuth mirrors routers/etsy.py,
# uploads run through app/social_publish/youtube.py (resumable protocol).
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/api/social/youtube/status")
def youtube_status():
    """Connection state for the Social tab (never leaks token values)."""
    return {
        "has_client": bool(get_setting("yt_client_id", "")),
        "connected":  _yt_pub.adapter.connected(),
        "privacy":    get_setting("social_youtube_privacy", "private") or "private",
    }


@router.get("/api/social/youtube/connect")
def youtube_connect_start():
    """Start Google OAuth — returns the authorize URL for the popup."""
    if not get_setting("yt_client_id", ""):
        raise HTTPException(400, "Set your YouTube (Google) Client ID first — settings "
                                 "keys yt_client_id and yt_client_secret.")
    state = _secrets.token_urlsafe(16)
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('yt_oauth_state',?)", (state,))
    conn.commit()
    conn.close()
    return {"url": _yt_pub.adapter.auth_url(state)}


@router.get("/api/social/youtube/callback")
def youtube_callback(code: str = None, state: str = None, error: str = None):
    """Google OAuth redirect target — exchanges the code for tokens (stored encrypted)."""
    if error:
        return HTMLResponse(f"<h2 style='font-family:sans-serif'>YouTube auth failed: {error}</h2>"
                            "<p>Close this tab and try again.</p>")
    stored_state = get_setting("yt_oauth_state", "")
    if not code or not stored_state or state != stored_state:
        return HTMLResponse("<h2 style='font-family:sans-serif'>Invalid OAuth state</h2>"
                            "<p>Try connecting again from the Social tab.</p>")
    try:
        tokens = _yt_pub.adapter.exchange_code(code)
        _yt_pub.adapter.save_tokens(tokens)
        conn = get_conn()
        conn.execute("DELETE FROM settings WHERE key='yt_oauth_state'")
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('social_youtube_connected','1')")
        conn.commit()
        conn.close()
        return HTMLResponse("""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#111;color:#eee">
        <h2>✅ YouTube Connected!</h2>
        <p>You can close this tab and return to the Store Command Center.</p>
        <script>if(window.opener){window.opener.postMessage('youtube_connected','*');setTimeout(()=>window.close(),1500);}</script>
        </body></html>""")
    except Exception as e:
        logger.error("YouTube token exchange failed: %s", e)
        return HTMLResponse(f"<h2 style='font-family:sans-serif'>Token exchange failed</h2><pre>{e}</pre>")


@router.delete("/api/social/youtube/disconnect")
def youtube_disconnect():
    _yt_pub.adapter.disconnect()
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('social_youtube_connected','0')")
    conn.commit()
    conn.close()
    return {"ok": True}


# ═════════════════════════════════════════════════════════════════════════════
# TikTok auto-publishing — OAuth v2 mirrors the YouTube flow above, uploads run
# through app/social_publish/tiktok.py (Content Posting API Direct Post).
# SELF_ONLY-gated until the app passes TikTok's audit (tiktok_audited setting).
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/api/social/tiktok/status")
def tiktok_status():
    """Connection state for the Social tab (never leaks token values)."""
    effective, _warn = _tt_pub.adapter.resolve_privacy()
    return {
        "has_client": bool(get_setting("tiktok_client_key", "")),
        "connected":  _tt_pub.adapter.connected(),
        "privacy":    effective,     # the level uploads will ACTUALLY use (audit-gated)
        "audited":    _tt_pub.adapter.audited(),
    }


@router.get("/api/social/tiktok/connect")
def tiktok_connect_start():
    """Start TikTok OAuth — returns the authorize URL for the popup."""
    if not get_setting("tiktok_client_key", ""):
        raise HTTPException(400, "Set your TikTok Client Key first — settings keys "
                                 "tiktok_client_key and tiktok_client_secret.")
    state = _secrets.token_urlsafe(16)
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('tiktok_oauth_state',?)", (state,))
    conn.commit()
    conn.close()
    return {"url": _tt_pub.adapter.auth_url(state)}


@router.get("/api/social/tiktok/callback")
def tiktok_callback(code: str = None, state: str = None, error: str = None,
                    error_description: str = None):
    """TikTok OAuth redirect target — exchanges the code for tokens (stored encrypted)."""
    if error:
        return HTMLResponse(f"<h2 style='font-family:sans-serif'>TikTok auth failed: {error}</h2>"
                            f"<p>{error_description or ''}</p><p>Close this tab and try again.</p>")
    stored_state = get_setting("tiktok_oauth_state", "")
    if not code or not stored_state or state != stored_state:
        return HTMLResponse("<h2 style='font-family:sans-serif'>Invalid OAuth state</h2>"
                            "<p>Try connecting again from the Social tab.</p>")
    try:
        tokens = _tt_pub.adapter.exchange_code(code)
        _tt_pub.adapter.save_tokens(tokens)
        conn = get_conn()
        conn.execute("DELETE FROM settings WHERE key='tiktok_oauth_state'")
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('social_tiktok_connected','1')")
        conn.commit()
        conn.close()
        return HTMLResponse("""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#111;color:#eee">
        <h2>✅ TikTok Connected!</h2>
        <p>You can close this tab and return to the Store Command Center.</p>
        <script>if(window.opener){window.opener.postMessage('tiktok_connected','*');setTimeout(()=>window.close(),1500);}</script>
        </body></html>""")
    except Exception as e:
        logger.error("TikTok token exchange failed: %s", e)
        return HTMLResponse(f"<h2 style='font-family:sans-serif'>Token exchange failed</h2><pre>{e}</pre>")


@router.delete("/api/social/tiktok/disconnect")
def tiktok_disconnect():
    _tt_pub.adapter.disconnect()
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('social_tiktok_connected','0')")
    conn.commit()
    conn.close()
    return {"ok": True}


# ═════════════════════════════════════════════════════════════════════════════
# Instagram + Facebook Page auto-publishing — ONE Meta OAuth connect covers
# both; uploads run through app/social_publish/instagram.py (Graph API: IG
# Reels resumable container flow / Page video upload). Also lights up without
# OAuth when the owner pastes meta_access_token + ig_business_id (and
# fb_page_id) into Settings. Meta publishes are PUBLIC — no privacy knob.
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/api/social/instagram/status")
def instagram_status():
    """Connection state for the Social tab (never leaks token values)."""
    return {
        "has_client":   bool(get_setting("meta_app_id", "")),
        "connected":    _ig_pub.adapter.connected(),
        "fb_connected": _ig_pub.fb_adapter.connected(),
        "ig_username":  (get_setting("social_instagram_handle", "") or "").lstrip("@"),
    }


@router.get("/api/social/instagram/connect")
def instagram_connect_start():
    """Start Meta OAuth — returns the authorize URL for the popup."""
    if not get_setting("meta_app_id", ""):
        raise HTTPException(400, "Set your Meta App ID first — settings keys meta_app_id "
                                 "and meta_app_secret.")
    state = _secrets.token_urlsafe(16)
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('meta_oauth_state',?)", (state,))
    conn.commit()
    conn.close()
    return {"url": _ig_pub.adapter.auth_url(state)}


@router.get("/api/social/instagram/callback")
def instagram_callback(code: str = None, state: str = None, error: str = None,
                       error_description: str = None):
    """Meta OAuth redirect target — exchanges the code for a long-lived token
    (stored encrypted), then auto-discovers the Page + IG Business account."""
    if error:
        return HTMLResponse(f"<h2 style='font-family:sans-serif'>Meta auth failed: {error}</h2>"
                            f"<p>{error_description or ''}</p><p>Close this tab and try again.</p>")
    stored_state = get_setting("meta_oauth_state", "")
    if not code or not stored_state or state != stored_state:
        return HTMLResponse("<h2 style='font-family:sans-serif'>Invalid OAuth state</h2>"
                            "<p>Try connecting again from the Social tab.</p>")
    try:
        tokens = _ig_pub.adapter.exchange_code(code)
        _ig_pub.adapter.save_tokens(tokens)
        note = ""
        try:
            found = _ig_pub.adapter.discover_accounts()
            if found.get("ig_username"):
                note = f"Instagram: @{found['ig_username']} · Page: {found.get('page_name') or found.get('page_id')}"
            elif found.get("page_id"):
                note = (f"Page {found.get('page_name') or found['page_id']} linked, but no "
                        "Instagram Business account found on it — link one in Meta Business "
                        "Suite (or set ig_business_id in Settings).")
        except Exception as de:
            note = f"Token saved, but account discovery failed: {de}"
        conn = get_conn()
        conn.execute("DELETE FROM settings WHERE key='meta_oauth_state'")
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('social_instagram_connected',?)",
                     ("1" if _ig_pub.adapter.connected() else "0",))
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('social_facebook_connected',?)",
                     ("1" if _ig_pub.fb_adapter.connected() else "0",))
        conn.commit()
        conn.close()
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#111;color:#eee">
        <h2>✅ Meta Connected!</h2>
        <p>{note}</p>
        <p>You can close this tab and return to the Store Command Center.</p>
        <script>if(window.opener){{window.opener.postMessage('instagram_connected','*');setTimeout(()=>window.close(),2500);}}</script>
        </body></html>""")
    except Exception as e:
        logger.error("Meta token exchange failed: %s", e)
        return HTMLResponse(f"<h2 style='font-family:sans-serif'>Token exchange failed</h2><pre>{e}</pre>")


@router.delete("/api/social/instagram/disconnect")
def instagram_disconnect():
    _ig_pub.adapter.disconnect()
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('social_instagram_connected','0')")
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('social_facebook_connected','0')")
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/api/social/instagram/stats")
def instagram_stats(media_id: Optional[str] = None):
    """Analytics passthrough — per-media views/likes/comments (media_id given) or
    an account snapshot (followers + recent media)."""
    if not _ig_pub.adapter.connected():
        raise HTTPException(400, "Instagram isn't connected — use Connect in the Social tab "
                                 "(or set meta_access_token + ig_business_id in Settings).")
    try:
        return _ig_pub.adapter.stats(media_id or "")
    except Exception as e:
        raise HTTPException(502, f"Instagram stats failed: {e}")


# ── publish flow ─────────────────────────────────────────────────────────────
def _resolve_post_video(conn, post) -> tuple[Optional[str], dict, int]:
    """(video_path, dims{width,height}, nsfw) for the post's linked video.

    Resolution order: explicit video_id → explicit chain_id → media_path matched
    back to a videos/video_chains row. Only DB-known videos qualify — an
    arbitrary path can't be nsfw-checked, so it is never auto-published."""
    if post["video_id"]:
        v = conn.execute("SELECT * FROM videos WHERE id=?", (post["video_id"],)).fetchone()
        if v:
            path = (v["audio_path"] or "").strip() or v["video_path"]
            return path, {"width": v["width"], "height": v["height"]}, int(v["nsfw"] or 0)
    if post["chain_id"]:
        ch = conn.execute("SELECT * FROM video_chains WHERE id=?", (post["chain_id"],)).fetchone()
        if ch:
            return ch["compiled_path"], {"width": ch["width"], "height": ch["height"]}, int(ch["nsfw"] or 0)
    mp = (post["media_path"] or "").strip()
    if mp:
        v = conn.execute("SELECT * FROM videos WHERE video_path=? OR audio_path=?", (mp, mp)).fetchone()
        if v:
            return mp, {"width": v["width"], "height": v["height"]}, int(v["nsfw"] or 0)
        ch = conn.execute("SELECT * FROM video_chains WHERE compiled_path=?", (mp,)).fetchone()
        if ch:
            return mp, {"width": ch["width"], "height": ch["height"]}, int(ch["nsfw"] or 0)
    return None, {}, 0


def _youtube_meta_for(post, dims: dict) -> dict:
    """Build the upload metadata from the post (+ per-platform overrides)."""
    try:
        pp = (_json.loads(post["per_platform"] or "{}") or {}).get("youtube") or {}
    except Exception:
        pp = {}
    title = (pp.get("title") or post["title"] or "").strip()
    if not title:
        title = (post["caption"] or "").strip().split("\n")[0][:_yt_pub.TITLE_MAX].strip()
    desc = pp.get("description")
    if desc is None:
        desc = (post["caption"] or "")
        if post["hashtags"]:
            desc = (desc + "\n\n" + post["hashtags"]).strip()
    tags = pp.get("tags")
    if not tags:
        tags = [t.lstrip("#") for t in (post["hashtags"] or "").replace(",", " ").split() if t.lstrip("#")]
    return {"title": title, "description": desc, "tags": tags,
            "privacy": pp.get("privacy"), **dims}


def _tiktok_meta_for(post, dims: dict) -> dict:
    """TikTok Direct Post metadata: title IS the caption (hashtags ride inline)."""
    try:
        pp = (_json.loads(post["per_platform"] or "{}") or {}).get("tiktok") or {}
    except Exception:
        pp = {}
    caption = (pp.get("title") or pp.get("caption") or "").strip()
    if not caption:
        caption = (post["caption"] or "").strip()
        if post["hashtags"]:
            caption = (caption + "\n\n" + post["hashtags"]).strip()
    return {"title": caption, "privacy": pp.get("privacy"), **dims}


def _instagram_meta_for(post, dims: dict) -> dict:
    """Instagram Reels metadata: title IS the caption (hashtags ride inline)."""
    try:
        pp = (_json.loads(post["per_platform"] or "{}") or {}).get("instagram") or {}
    except Exception:
        pp = {}
    caption = (pp.get("title") or pp.get("caption") or "").strip()
    if not caption:
        caption = (post["caption"] or "").strip()
        if post["hashtags"]:
            caption = (caption + "\n\n" + post["hashtags"]).strip()
    return {"title": caption, **dims}


def _facebook_meta_for(post, dims: dict) -> dict:
    """Facebook Page video metadata: description = caption + hashtags, optional title."""
    try:
        pp = (_json.loads(post["per_platform"] or "{}") or {}).get("facebook") or {}
    except Exception:
        pp = {}
    desc = (pp.get("description") or pp.get("caption") or "").strip()
    if not desc:
        desc = (post["caption"] or "").strip()
        if post["hashtags"]:
            desc = (desc + "\n\n" + post["hashtags"]).strip()
    return {"title": desc, "description": desc,
            "fb_title": (pp.get("title") or post["title"] or "").strip(), **dims}


# Platform registry for the publish flow — adding a platform is one entry here
# plus its adapter module (see app/social_publish/base.py).
_PUBLISHERS = {
    "youtube":   {"adapter": _yt_pub.adapter,    "meta": _youtube_meta_for,   "label": "YouTube"},
    "tiktok":    {"adapter": _tt_pub.adapter,    "meta": _tiktok_meta_for,    "label": "TikTok"},
    "instagram": {"adapter": _ig_pub.adapter,    "meta": _instagram_meta_for, "label": "Instagram"},
    "facebook":  {"adapter": _ig_pub.fb_adapter, "meta": _facebook_meta_for,  "label": "Facebook"},
}


def _do_publish(platform: str, pub_id: int, video_path: str, meta: dict):
    """Background worker: pending → uploading → live/failed on the publication row."""
    conn = get_conn()
    conn.execute("UPDATE social_publications SET status='uploading', attempts=attempts+1, "
                 "updated_at=datetime('now') WHERE id=?", (pub_id,))
    conn.commit()
    conn.close()
    try:
        res = _PUBLISHERS[platform]["adapter"].upload(video_path, meta)
        state = dict(res.get("state") or {})
        if res.get("warnings"):
            state["warnings"] = res["warnings"]
        conn = get_conn()
        conn.execute(
            "UPDATE social_publications SET status='live', platform_post_id=?, platform_url=?, "
            "upload_state=?, error=NULL, updated_at=datetime('now') WHERE id=?",
            (res.get("platform_post_id"), res.get("url"), _json.dumps(state), pub_id))
        # reflect it on the post: the platform joins posted_on (same shape as mark-posted)
        row = conn.execute("SELECT post_id FROM social_publications WHERE id=?", (pub_id,)).fetchone()
        if row:
            post = conn.execute("SELECT * FROM social_posts WHERE id=?", (row["post_id"],)).fetchone()
            if post:
                on_post = _json.loads(post["platforms"] or "[]")
                done = set(_json.loads(post["posted_on"] or "[]")) | {platform}
                all_done = bool(on_post) and set(on_post).issubset(done)
                conn.execute(
                    "UPDATE social_posts SET posted_on=?, status=?, posted_at=COALESCE(posted_at,?), "
                    "updated_at=datetime('now') WHERE id=?",
                    (_json.dumps(sorted(done)), "posted" if all_done else post["status"],
                     datetime.now().isoformat(timespec="minutes"), post["id"]))
        conn.commit()
        conn.close()
        logger.info("social publication %s live: %s", pub_id, res.get("url"))
    except Exception as e:
        logger.error("%s publish failed (publication %s): %s",
                     _PUBLISHERS[platform]["label"], pub_id, e)
        conn = get_conn()
        conn.execute("UPDATE social_publications SET status='failed', error=?, "
                     "updated_at=datetime('now') WHERE id=?", (str(e)[:500], pub_id))
        conn.commit()
        conn.close()


class PublishIn(BaseModel):
    platforms: Optional[list[str]] = None   # default: ['youtube']


@router.post("/api/social/posts/{pid}/publish")
def publish_post(pid: int, body: PublishIn, background_tasks: BackgroundTasks):
    """Manually publish a post's video to platform(s). Manual publish IS the owner's
    consent — this endpoint is deliberately not gated on anything but NSFW."""
    platforms = body.platforms or ["youtube"]
    unsupported = [x for x in platforms if x not in _PUBLISHERS]
    if unsupported:
        raise HTTPException(400, f"Auto-publish isn't available yet for: {', '.join(unsupported)}. "
                                 f"Supported: {', '.join(sorted(_PUBLISHERS))}.")
    conn = get_conn()
    post = conn.execute("SELECT * FROM social_posts WHERE id=?", (pid,)).fetchone()
    if not post:
        conn.close()
        raise HTTPException(404, "Post not found")
    video_path, dims, nsfw = _resolve_post_video(conn, post)
    if not video_path:
        conn.close()
        raise HTTPException(400, "No publishable video on this post — attach a generated "
                                 "video (or compiled chain) from the media picker.")
    if nsfw:
        conn.close()
        # HARD refusal: Private Studio media never leaves the machine via auto-publish.
        raise HTTPException(403, "This video is marked NSFW (Private Studio) — auto-publishing "
                                 "it is blocked.")
    for plat in platforms:      # all-or-nothing: check connections before queuing anything
        label = _PUBLISHERS[plat]["label"]
        if not _PUBLISHERS[plat]["adapter"].connected():
            conn.close()
            raise HTTPException(400, f"{label} isn't connected — use Connect {label} in the "
                                     "Social tab first.")
    results = []
    for plat in platforms:
        label = _PUBLISHERS[plat]["label"]
        meta = _PUBLISHERS[plat]["meta"](post, dims)
        existing = conn.execute("SELECT * FROM social_publications WHERE post_id=? AND platform=?",
                                (pid, plat)).fetchone()
        if existing and existing["status"] == "live":
            results.append({"platform": plat, "already_live": True, "publication_id": existing["id"],
                            "url": existing["platform_url"],
                            "message": f"Already live on {label} — delete it there first to re-publish."})
            continue
        if existing:
            conn.execute("UPDATE social_publications SET status='pending', error=NULL, "
                         "updated_at=datetime('now') WHERE id=?", (existing["id"],))
            pub_id = existing["id"]
        else:
            cur = conn.execute("INSERT INTO social_publications (post_id, platform, status) "
                               "VALUES (?, ?, 'pending')", (pid, plat))
            pub_id = cur.lastrowid
        background_tasks.add_task(_do_publish, plat, pub_id, video_path, meta)
        results.append({"platform": plat, "publication_id": pub_id,
                        "message": f"Uploading to {label} in background…"})
    conn.commit()
    conn.close()
    out = {"ok": True, "results": results}
    if len(results) == 1:       # single-platform (the UI's shape) keeps the flat keys
        out.update(results[0])
    return out


@router.get("/api/social/posts/{pid}/publications")
def post_publications(pid: int):
    """Per-platform publish status rows for a post (the UI polls this)."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM social_publications WHERE post_id=? ORDER BY platform",
                        (pid,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["upload_state"] = _json.loads(d.get("upload_state") or "{}")
        except Exception:
            d["upload_state"] = {}
        try:
            d["stats"] = _json.loads(d.get("stats") or "null")
        except Exception:
            d["stats"] = None
        out.append(d)
    return {"publications": out}


# ═════════════════════════════════════════════════════════════════════════════
# Auto-scheduler (app/social_scheduler.py) — the "schedule" field made real.
# GATED: master setting social_scheduler_enabled defaults OFF; the loop only
# ever touches explicitly-scheduled posts and claims them (auto_state) before
# uploading, so it can never double-post. Dry-run mode logs without uploading.
# ═════════════════════════════════════════════════════════════════════════════
import social_scheduler as _sched


@router.get("/api/social/scheduler/status")
def scheduler_status():
    """Gates + loop state + due count + recent actions (for the Social tab)."""
    return _sched.status()


class SchedulerConfigIn(BaseModel):
    enabled: Optional[bool] = None
    dry_run: Optional[bool] = None
    interval_sec: Optional[int] = None
    analytics_auto_hours: Optional[float] = None
    taste_feed: Optional[bool] = None


@router.post("/api/social/scheduler/config")
def scheduler_config(cfg: SchedulerConfigIn):
    updates = {}
    if cfg.enabled is not None:
        updates["social_scheduler_enabled"] = "1" if cfg.enabled else "0"
    if cfg.dry_run is not None:
        updates["social_scheduler_dry_run"] = "1" if cfg.dry_run else "0"
    if cfg.interval_sec is not None:
        updates["social_scheduler_interval_sec"] = str(max(15, int(cfg.interval_sec)))
    if cfg.analytics_auto_hours is not None:
        updates["social_analytics_auto_hours"] = str(max(0.0, float(cfg.analytics_auto_hours)))
    if cfg.taste_feed is not None:
        updates["social_taste_feed"] = "1" if cfg.taste_feed else "0"
    if updates:
        _sched.save_config(updates)
    return {"ok": True, "status": _sched.status()}


@router.post("/api/social/scheduler/run-once")
def scheduler_run_once():
    """Manual tick — honors every gate (disabled ⇒ no-op result, dry-run ⇒ log
    only). Useful to verify a due post would publish without waiting a cycle."""
    return _sched.run_once()


@router.post("/api/social/posts/{pid}/schedule-reset")
def schedule_reset(pid: int):
    """Re-arm a post for the auto-scheduler (clears the auto_state claim after
    a failure/dry-run — deliberate, so retries are always a human choice)."""
    conn = get_conn()
    row = conn.execute("SELECT auto_state FROM social_posts WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Post not found")
    conn.execute("UPDATE social_posts SET auto_state=NULL, updated_at=datetime('now') "
                 "WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"ok": True, "was": row["auto_state"]}


# ── post analytics → taste model ─────────────────────────────────────────────
@router.post("/api/social/analytics/refresh")
def analytics_refresh(platform: Optional[str] = None, post_id: Optional[int] = None):
    """Fetch live publications' performance (YouTube videos.list statistics;
    TikTok best-effort + gated), store it on social_publications.stats, and
    feed the world_taste model so future proposals lean toward what performed.
    Per-row failures come back as {available: false, detail} — never a 500."""
    if platform and platform not in _PUBLISHERS:
        raise HTTPException(400, f"No stats support for platform '{platform}'.")
    return _sched.refresh_analytics(platform=platform, post_id=post_id)

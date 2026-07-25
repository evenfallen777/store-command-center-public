"""YouTube publishing adapter — Google OAuth + YouTube Data API v3 resumable upload.

Mirrors the Etsy OAuth shape (app/routers/etsy.py + app/etsy_client.py):
  connect  → Google authorize URL (offline access so we get a refresh token)
  callback → code exchange at the token endpoint, tokens stored ENCRYPTED
             (settings keys yt_access_token / yt_refresh_token, see crypto.SECRET_KEYS)
  upload   → resumable protocol: POST init (metadata) → session URI from the
             Location header → PUT the file bytes → videoId from the response.

Safety defaults: privacyStatus comes from the `social_youtube_privacy` setting
and defaults to "private" — a fresh connect can never blast a video public by
accident. selfDeclaredMadeForKids is always False (this store's content is not
kids-directed; COPPA designation must be a deliberate human choice).
"""
import time
import logging
import urllib.parse
from pathlib import Path

import httpx

from db import get_conn
from crypto import enc as _enc, dec as _dec

try:
    from config import YT_REDIRECT_URI as REDIRECT_URI
except Exception:
    REDIRECT_URI = "http://localhost:8787/store/api/social/youtube/callback"

from social_publish.base import PublishAdapter

log = logging.getLogger("store")

AUTH_URL   = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL  = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
SCOPES     = ("https://www.googleapis.com/auth/youtube.upload "
              "https://www.googleapis.com/auth/youtube.readonly")

TITLE_MAX = 100     # YouTube hard limit
DESC_MAX  = 5000    # YouTube hard limit
PRIVACY_VALUES = ("private", "unlisted", "public")


# ── settings helpers (no deps.py import — keeps the adapter light + cycle-free) ──
def _get(key: str, default: str = "") -> str:
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row and row["value"] not in (None, ""):
        return _dec(row["value"])
    return default


def _set(key: str, value: str, secret: bool = False):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                 (key, _enc(value) if secret else value))
    conn.commit()
    conn.close()


def _delete(*keys: str):
    conn = get_conn()
    conn.execute(f"DELETE FROM settings WHERE key IN ({','.join('?' * len(keys))})", keys)
    conn.commit()
    conn.close()


class YouTubeAdapter(PublishAdapter):
    platform = "youtube"

    # ── OAuth ────────────────────────────────────────────────────────────────
    def connected(self) -> bool:
        return bool(_get("yt_refresh_token") or _get("yt_access_token"))

    def auth_url(self, state: str) -> str:
        client_id = _get("yt_client_id")
        if not client_id:
            raise ValueError("YouTube Client ID not set — save yt_client_id (and "
                             "yt_client_secret) in Settings first.")
        params = urllib.parse.urlencode({
            "response_type": "code",
            "client_id":     client_id,
            "redirect_uri":  REDIRECT_URI,
            "scope":         SCOPES,
            "access_type":   "offline",   # → refresh token
            "prompt":        "consent",   # force refresh token even on re-connect
            "state":         state,
        })
        return f"{AUTH_URL}?{params}"

    def exchange_code(self, code: str) -> dict:
        """Exchange the callback code for tokens (does NOT store — see save_tokens)."""
        r = httpx.post(TOKEN_URL, data={
            "grant_type":    "authorization_code",
            "client_id":     _get("yt_client_id"),
            "client_secret": _get("yt_client_secret"),
            "code":          code,
            "redirect_uri":  REDIRECT_URI,
        }, timeout=30)
        r.raise_for_status()
        return r.json()

    def save_tokens(self, tokens: dict):
        """Persist access/refresh tokens ENCRYPTED + the expiry timestamp.
        Google omits refresh_token on some re-grants — never clobber a stored
        one with nothing."""
        if tokens.get("access_token"):
            _set("yt_access_token", tokens["access_token"], secret=True)
        if tokens.get("refresh_token"):
            _set("yt_refresh_token", tokens["refresh_token"], secret=True)
        _set("yt_token_expires", str(int(time.time()) + int(tokens.get("expires_in", 3600))))

    def refresh_access_token(self) -> str:
        """grant_type=refresh_token → persist + return the fresh access token."""
        refresh = _get("yt_refresh_token")
        if not refresh:
            raise ValueError("YouTube is not connected (no refresh token) — use Connect first.")
        r = httpx.post(TOKEN_URL, data={
            "grant_type":    "refresh_token",
            "client_id":     _get("yt_client_id"),
            "client_secret": _get("yt_client_secret"),
            "refresh_token": refresh,
        }, timeout=30)
        r.raise_for_status()
        tokens = r.json()
        self.save_tokens(tokens)
        return tokens["access_token"]

    def disconnect(self):
        _delete("yt_access_token", "yt_refresh_token", "yt_token_expires")

    def _access_token(self) -> str:
        """A valid access token — refreshed first when missing/expiring (<120s left)."""
        token = _get("yt_access_token")
        try:
            expires_at = int(_get("yt_token_expires", "0") or 0)
        except Exception:
            expires_at = 0
        if not token or time.time() >= expires_at - 120:
            token = self.refresh_access_token()
        return token

    # ── validation ───────────────────────────────────────────────────────────
    def validate(self, meta: dict) -> list[str]:
        """Hard-fail on YouTube's field limits; return soft warnings (aspect)."""
        title = (meta.get("title") or "").strip()
        if not title:
            raise ValueError("A title is required for YouTube.")
        if len(title) > TITLE_MAX:
            raise ValueError(f"YouTube title is {len(title)} chars — the limit is {TITLE_MAX}. "
                             "Shorten the post title (or set a per-platform YouTube title).")
        desc = meta.get("description") or ""
        if len(desc) > DESC_MAX:
            raise ValueError(f"YouTube description is {len(desc)} chars — the limit is {DESC_MAX}.")
        warnings = []
        w, h = meta.get("width"), meta.get("height")
        if w and h and int(w) > int(h):
            warnings.append(f"Video is landscape ({w}x{h}) — YouTube Shorts prefer "
                            "vertical 9:16; it will upload as a regular video.")
        return warnings

    # ── upload (resumable protocol) ──────────────────────────────────────────
    def upload(self, video_path: str, meta: dict) -> dict:
        p = Path(video_path)
        if not p.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        warnings = self.validate(meta)

        privacy = (meta.get("privacy") or _get("social_youtube_privacy", "private")
                   or "private").strip().lower()
        if privacy not in PRIVACY_VALUES:
            privacy = "private"
        body = {
            "snippet": {
                "title":       meta["title"].strip(),
                "description": meta.get("description") or "",
                "tags":        [t for t in (meta.get("tags") or []) if t][:30],
            },
            "status": {
                "privacyStatus":          privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        token = self._access_token()
        size = p.stat().st_size
        # 1) initiate: metadata POST → resumable session URI in the Location header
        init = httpx.post(
            f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
            json=body,
            headers={
                "Authorization":           f"Bearer {token}",
                "X-Upload-Content-Length": str(size),
                "X-Upload-Content-Type":   "video/mp4",
            },
            timeout=30,
        )
        init.raise_for_status()
        session_uri = init.headers.get("location") or init.headers.get("Location")
        if not session_uri:
            raise RuntimeError("YouTube resumable init returned no session URI (Location header).")

        # 2) upload the bytes to the session URI
        with p.open("rb") as fh:
            put = httpx.put(
                session_uri,
                content=fh,
                headers={
                    "Authorization":  f"Bearer {token}",
                    "Content-Type":   "video/mp4",
                    "Content-Length": str(size),
                },
                timeout=httpx.Timeout(600.0, connect=30.0),
            )
        put.raise_for_status()
        data = put.json()
        video_id = data.get("id")
        if not video_id:
            raise RuntimeError(f"YouTube upload finished but returned no videoId: {str(data)[:300]}")
        log.info("YouTube upload done: video %s (privacy=%s, %d bytes)", video_id, privacy, size)
        return {
            "platform_post_id": video_id,
            "url": f"https://youtu.be/{video_id}",
            "state": {
                "session_uri":   session_uri,
                "bytes":         size,
                "privacy":       privacy,
                "upload_status": (data.get("status") or {}).get("uploadStatus"),
            },
            "warnings": warnings,
        }

    # ── post-upload processing status ────────────────────────────────────────
    def status(self, platform_post_id: str) -> dict:
        token = self._access_token()
        r = httpx.get(
            VIDEOS_URL,
            params={"part": "status,processingDetails", "id": platform_post_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        r.raise_for_status()
        items = r.json().get("items") or []
        if not items:
            return {"status": "failed", "detail": "video not found on YouTube"}
        st = items[0].get("status") or {}
        up = st.get("uploadStatus") or ""
        if up == "processed":
            mapped = "live"
        elif up in ("failed", "rejected", "deleted"):
            mapped = "failed"
        else:                       # "uploaded" = still processing
            mapped = "processing"
        detail = st.get("failureReason") or st.get("rejectionReason") or \
            ((items[0].get("processingDetails") or {}).get("processingStatus") or up)
        return {"status": mapped, "detail": str(detail)}

    # ── performance stats (analytics → taste model) ──────────────────────────
    def stats(self, platform_post_id: str) -> dict:
        """videos.list?part=statistics for a published video — rides the stored
        OAuth token (youtube.readonly scope is already requested at connect).
        Returns {available: True, views, likes, comments, favorites} or
        {available: False, detail} — callers treat unavailable as a soft skip."""
        token = self._access_token()
        r = httpx.get(
            VIDEOS_URL,
            params={"part": "statistics", "id": platform_post_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        r.raise_for_status()
        items = r.json().get("items") or []
        if not items:
            return {"available": False, "detail": "video not found on YouTube"}
        s = items[0].get("statistics") or {}

        def _i(key):
            try:
                return int(s.get(key) or 0)
            except Exception:
                return 0
        return {"available": True, "views": _i("viewCount"), "likes": _i("likeCount"),
                "comments": _i("commentCount"), "favorites": _i("favoriteCount")}


adapter = YouTubeAdapter()

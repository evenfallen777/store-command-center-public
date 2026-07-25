"""TikTok publishing adapter — TikTok OAuth v2 + Content Posting API Direct Post.

Mirrors the YouTube adapter shape (app/social_publish/youtube.py):
  connect  → TikTok authorize URL (client_key + video scopes)
  callback → code exchange at the v2 token endpoint, tokens stored ENCRYPTED
             (settings keys tiktok_access_token / tiktok_refresh_token, see
             crypto.SECRET_KEYS)
  upload   → Direct Post protocol: POST video/init (post_info + source_info)
             → publish_id + upload_url → PUT the file bytes (Content-Range)
             → poll status/fetch until PUBLISH_COMPLETE / FAILED.

Safety defaults — privacy_level comes from the `social_tiktok_privacy` setting
and defaults to "SELF_ONLY" (private to the creator). CRITICAL: an UNAUDITED
TikTok app may ONLY post SELF_ONLY; the `tiktok_audited` setting (default "0")
gates the public levels — until it is "1", any requested public level is
downgraded to SELF_ONLY with a warning instead of failing the whole upload.
"""
import time
import logging
import urllib.parse
from pathlib import Path

import httpx

from db import get_conn
from crypto import enc as _enc, dec as _dec

try:
    from config import TIKTOK_REDIRECT_URI as REDIRECT_URI
except Exception:
    REDIRECT_URI = "http://localhost:8787/store/api/social/tiktok/callback"

from social_publish.base import PublishAdapter

log = logging.getLogger("store")

AUTH_URL   = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL  = "https://open.tiktokapis.com/v2/oauth/token/"
CREATOR_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
INIT_URL   = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
VIDEO_QUERY_URL = "https://open.tiktokapis.com/v2/video/query/"   # Display API (stats)
SCOPES     = "user.info.basic,video.upload,video.publish"

CAPTION_MAX = 2200      # TikTok caption (post_info.title) hard limit
PRIVACY_VALUES = ("SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR",
                  "PUBLIC_TO_EVERYONE")


def _aigc_disclose() -> bool:
    """TikTok policy requires AI-generated content to be labeled. The store posts
    AI-made media, so we disclose (post_info.is_aigc) by default. Toggle off with the
    `tiktok_disclose_aigc` setting only if posting genuinely non-AI footage."""
    try:
        from deps import get_setting
        return str(get_setting("tiktok_disclose_aigc", "1")).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return True


def _prep_for_tiktok(src: str) -> str:
    """TikTok requires 23–60 fps and generally an audio track; our clips render at
    16 fps and silent (→ frame_rate_check_failed). Transcode to a compliant 30 fps
    H.264/yuv420p mp4 with a silent AAC track. Returns a temp path (caller uploads it
    and cleans it up), or the original on any ffmpeg error."""
    import subprocess as _sp, tempfile, os as _os, re as _re
    try:
        r = _sp.run(["ffprobe", "-v", "quiet", "-select_streams", "v:0",
                     "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", src],
                    capture_output=True, text=True)
        m = _re.findall(r"\d+", r.stdout)
        fps = (int(m[0]) / int(m[1])) if len(m) >= 2 and int(m[1]) else (int(m[0]) if m else 0)
    except Exception:
        fps = 0
    dst = _os.path.join(tempfile.gettempdir(), "tt_" + _os.path.basename(src))
    try:
        _sp.run([
            "ffmpeg", "-y", "-i", src,
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+faststart", dst,
        ], check=True, capture_output=True)
        return dst
    except Exception as e:
        log.warning("TikTok fps/audio transcode failed, uploading original: %s", e)
        return src

# Status-poll pacing (module-level so tests can shrink them).
POLL_INTERVAL = 3.0
POLL_ATTEMPTS = 40


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


def _api_error(payload: dict) -> str:
    """TikTok's response envelope: {'data': ..., 'error': {code, message, log_id}}.
    Returns '' when the error block says ok / is absent."""
    err = (payload or {}).get("error") or {}
    code = str(err.get("code") or "")
    if code in ("", "ok"):
        return ""
    return f"{code}: {err.get('message') or 'TikTok API error'}"


class TikTokAdapter(PublishAdapter):
    platform = "tiktok"

    # ── OAuth ────────────────────────────────────────────────────────────────
    def connected(self) -> bool:
        return bool(_get("tiktok_refresh_token") or _get("tiktok_access_token"))

    def auth_url(self, state: str) -> str:
        client_key = _get("tiktok_client_key")
        if not client_key:
            raise ValueError("TikTok Client Key not set — save tiktok_client_key (and "
                             "tiktok_client_secret) in Settings first.")
        params = urllib.parse.urlencode({
            "client_key":    client_key,
            "response_type": "code",
            "scope":         SCOPES,
            "redirect_uri":  REDIRECT_URI,
            "state":         state,
        })
        return f"{AUTH_URL}?{params}"

    def exchange_code(self, code: str) -> dict:
        """Exchange the callback code for tokens (does NOT store — see save_tokens).
        TikTok wants client_key + client_secret form-encoded (not Basic auth)."""
        r = httpx.post(TOKEN_URL, data={
            "grant_type":    "authorization_code",
            "client_key":    _get("tiktok_client_key"),
            "client_secret": _get("tiktok_client_secret"),
            "code":          code,
            "redirect_uri":  REDIRECT_URI,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        r.raise_for_status()
        tokens = r.json()
        if not tokens.get("access_token"):
            raise ValueError(f"TikTok token exchange failed: "
                             f"{tokens.get('error_description') or tokens.get('error') or str(tokens)[:200]}")
        return tokens

    def save_tokens(self, tokens: dict):
        """Persist access/refresh tokens ENCRYPTED + the expiry timestamp.
        Never clobber a stored refresh token with nothing."""
        if tokens.get("access_token"):
            _set("tiktok_access_token", tokens["access_token"], secret=True)
        if tokens.get("refresh_token"):
            _set("tiktok_refresh_token", tokens["refresh_token"], secret=True)
        _set("tiktok_token_expires", str(int(time.time()) + int(tokens.get("expires_in", 3600))))

    def refresh_access_token(self) -> str:
        """grant_type=refresh_token → persist + return the fresh access token."""
        refresh = _get("tiktok_refresh_token")
        if not refresh:
            raise ValueError("TikTok is not connected (no refresh token) — use Connect first.")
        r = httpx.post(TOKEN_URL, data={
            "grant_type":    "refresh_token",
            "client_key":    _get("tiktok_client_key"),
            "client_secret": _get("tiktok_client_secret"),
            "refresh_token": refresh,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        r.raise_for_status()
        tokens = r.json()
        if not tokens.get("access_token"):
            raise ValueError(f"TikTok token refresh failed: "
                             f"{tokens.get('error_description') or tokens.get('error') or str(tokens)[:200]}")
        self.save_tokens(tokens)
        return tokens["access_token"]

    def disconnect(self):
        _delete("tiktok_access_token", "tiktok_refresh_token", "tiktok_token_expires")

    def _access_token(self) -> str:
        """A valid access token — refreshed first when missing/expiring (<120s left)."""
        token = _get("tiktok_access_token")
        try:
            expires_at = int(_get("tiktok_token_expires", "0") or 0)
        except Exception:
            expires_at = 0
        if not token or time.time() >= expires_at - 120:
            token = self.refresh_access_token()
        return token

    # ── privacy gating ───────────────────────────────────────────────────────
    def audited(self) -> bool:
        return _get("tiktok_audited", "0") == "1"

    def resolve_privacy(self, requested: str | None = None) -> tuple[str, list[str]]:
        """(effective privacy_level, warnings). An unaudited TikTok app can ONLY
        post SELF_ONLY — a requested public level is downgraded (with a warning),
        never sent, so the upload still succeeds instead of 4xx-ing at TikTok."""
        privacy = (requested or _get("social_tiktok_privacy", "SELF_ONLY")
                   or "SELF_ONLY").strip().upper()
        warnings = []
        if privacy not in PRIVACY_VALUES:
            warnings.append(f"Unknown TikTok privacy level '{privacy}' — using SELF_ONLY.")
            privacy = "SELF_ONLY"
        if privacy != "SELF_ONLY" and not self.audited():
            warnings.append(
                f"TikTok app is UNAUDITED — requested privacy '{privacy}' was downgraded to "
                "SELF_ONLY (private to you). Public posting needs TikTok's app audit; set the "
                "tiktok_audited setting to 1 once approved.")
            privacy = "SELF_ONLY"
        return privacy, warnings

    # ── validation ───────────────────────────────────────────────────────────
    def validate(self, meta: dict) -> list[str]:
        """Hard-fail on TikTok's caption limit; return soft warnings (aspect)."""
        caption = (meta.get("title") or "").strip()
        if len(caption) > CAPTION_MAX:
            raise ValueError(f"TikTok caption is {len(caption)} chars — the limit is {CAPTION_MAX}. "
                             "Shorten the caption (or set a per-platform TikTok caption).")
        warnings = []
        w, h = meta.get("width"), meta.get("height")
        if w and h and int(w) > int(h):
            warnings.append(f"Video is landscape ({w}x{h}) — TikTok wants vertical 9:16; "
                            "it will post letterboxed.")
        return warnings

    # ── upload (Direct Post: init → PUT bytes → poll status) ─────────────────
    def upload(self, video_path: str, meta: dict) -> dict:
        p = Path(video_path)
        if not p.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        warnings = self.validate(meta)
        privacy, pw = self.resolve_privacy(meta.get("privacy"))
        warnings += pw

        caption = (meta.get("title") or "").strip()[:CAPTION_MAX]
        token = self._access_token()
        _hdr = {"Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8"}
        # TikTok requires creator_info to be queried before a Direct Post — it validates
        # the token + video.publish scope and returns the creator's allowed privacy
        # levels. Skipping it is a common cause of 403 on video/init.
        ci = httpx.post(CREATOR_URL, headers=_hdr, timeout=30)
        if ci.status_code >= 400:
            raise RuntimeError(f"TikTok creator_info HTTP {ci.status_code}: {ci.text[:500]}")
        _allowed = ((ci.json().get("data") or {}).get("privacy_level_options")) or []
        if _allowed and privacy not in _allowed:
            privacy = "SELF_ONLY" if "SELF_ONLY" in _allowed else _allowed[0]
        # TikTok needs 23–60 fps + audio; our clips are 16 fps + silent. Transcode to a
        # compliant temp file for the upload, then clean it up.
        _prepared = _prep_for_tiktok(str(p))
        _prepared_tmp = _prepared != str(p)
        p = Path(_prepared)
        size = p.stat().st_size

        # 1) init: post_info + source_info → publish_id + upload_url.
        #    Single chunk — our generated clips are small (well under TikTok's 64MB
        #    single-chunk ceiling).
        init = httpx.post(
            INIT_URL,
            json={
                "post_info": {
                    "title":           caption,
                    "privacy_level":   privacy,
                    "disable_comment": False,
                    "disable_duet":    False,
                    "disable_stitch":  False,
                    # Label AI-generated content per TikTok policy (store media is AI-made).
                    "is_aigc":         _aigc_disclose(),
                },
                "source_info": {
                    "source":            "FILE_UPLOAD",
                    "video_size":        size,
                    "chunk_size":        size,
                    "total_chunk_count": 1,
                },
            },
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json; charset=UTF-8"},
            timeout=30,
        )
        if init.status_code >= 400:
            # Surface TikTok's actual error body (code + message) instead of a bare
            # "403 Forbidden" — that's what tells us WHY (scope, audit, creator_info…).
            raise RuntimeError(f"TikTok video init HTTP {init.status_code}: {init.text[:600]}")
        payload = init.json()
        err = _api_error(payload)
        if err:
            raise RuntimeError(f"TikTok video init failed — {err}")
        data = payload.get("data") or {}
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        if not publish_id or not upload_url:
            raise RuntimeError(f"TikTok init returned no publish_id/upload_url: {str(payload)[:300]}")

        # 2) PUT the file bytes to the upload_url (single chunk → one Content-Range)
        with p.open("rb") as fh:
            put = httpx.put(
                upload_url,
                content=fh,
                headers={
                    "Content-Type":   "video/mp4",
                    "Content-Length": str(size),
                    "Content-Range":  f"bytes 0-{size - 1}/{size}",
                },
                timeout=httpx.Timeout(600.0, connect=30.0),
            )
        put.raise_for_status()
        if _prepared_tmp:
            try: p.unlink(missing_ok=True)
            except Exception: pass

        # 3) poll status/fetch until PUBLISH_COMPLETE / FAILED (bounded)
        last_status, post_ids = "", []
        for attempt in range(POLL_ATTEMPTS):
            st = httpx.post(
                STATUS_URL,
                json={"publish_id": publish_id},
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json; charset=UTF-8"},
                timeout=30,
            )
            st.raise_for_status()
            sp = st.json()
            sdata = sp.get("data") or {}
            last_status = str(sdata.get("status") or "").upper()
            if last_status == "PUBLISH_COMPLETE":
                # TikTok's actual (misspelled) field name for the public post ids
                post_ids = sdata.get("publicaly_available_post_id") or \
                    sdata.get("publicly_available_post_id") or []
                break
            if last_status == "FAILED":
                raise RuntimeError(f"TikTok publish failed: "
                                   f"{sdata.get('fail_reason') or _api_error(sp) or 'unknown reason'}")
            time.sleep(POLL_INTERVAL)
        else:
            warnings.append(f"TikTok is still processing the post (last status: "
                            f"{last_status or 'unknown'}) — check the app in a minute.")

        url = ""
        if post_ids:
            handle = (_get("social_tiktok_handle", "") or "").strip().lstrip("@")
            if handle:
                url = f"https://www.tiktok.com/@{handle}/video/{post_ids[0]}"
        log.info("TikTok direct post done: publish_id %s (privacy=%s, %d bytes, status=%s)",
                 publish_id, privacy, size, last_status)
        return {
            "platform_post_id": publish_id,
            "url": url,
            "state": {
                "publish_id":     publish_id,
                "bytes":          size,
                "privacy":        privacy,
                "publish_status": last_status,
                "post_ids":       post_ids,
            },
            "warnings": warnings,
        }

    # ── post-upload processing status ────────────────────────────────────────
    def status(self, platform_post_id: str) -> dict:
        token = self._access_token()
        r = httpx.post(
            STATUS_URL,
            json={"publish_id": platform_post_id},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json; charset=UTF-8"},
            timeout=30,
        )
        r.raise_for_status()
        payload = r.json()
        err = _api_error(payload)
        if err:
            return {"status": "failed", "detail": err}
        data = payload.get("data") or {}
        st = str(data.get("status") or "").upper()
        if st == "PUBLISH_COMPLETE":
            mapped = "live"
        elif st == "FAILED":
            mapped = "failed"
        else:                       # PROCESSING_UPLOAD / PROCESSING_DOWNLOAD / …
            mapped = "processing"
        return {"status": mapped, "detail": str(data.get("fail_reason") or st)}

    # ── performance stats (analytics → taste model) — BEST-EFFORT + GATED ────
    def stats(self, platform_post_id: str, post_ids: list | None = None) -> dict:
        """Video stats via the Display API (video/query). HEAVILY caveated:
          • needs the video.list scope, which our connect flow does NOT request
            (an unaudited app generally can't get it approved), and
          • SELF_ONLY posts may expose no public video id at all.
        So this is gated OFF by default (tiktok_stats_enabled setting) and NEVER
        raises — any failure returns {available: False, detail} so the analytics
        pipeline degrades gracefully instead of erroring."""
        if _get("tiktok_stats_enabled", "0") != "1":
            return {"available": False,
                    "detail": "TikTok stats disabled (set tiktok_stats_enabled=1 to try). "
                              "Needs the video.list scope — usually unavailable to an "
                              "unaudited app."}
        ids = [str(x) for x in (post_ids or []) if x]
        if not ids:
            return {"available": False,
                    "detail": "no public video id recorded (SELF_ONLY posts often have none)"}
        try:
            token = self._access_token()
            r = httpx.post(
                VIDEO_QUERY_URL,
                params={"fields": "id,view_count,like_count,comment_count,share_count"},
                json={"filters": {"video_ids": ids[:20]}},
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json; charset=UTF-8"},
                timeout=30,
            )
            payload = r.json() if r.content else {}
            err = _api_error(payload)
            if r.status_code >= 400 or err:
                return {"available": False,
                        "detail": f"TikTok video/query HTTP {r.status_code}: "
                                  f"{err or r.text[:200]} (video.list scope / audit likely required)"}
            vids = ((payload.get("data") or {}).get("videos")) or []
            if not vids:
                return {"available": False, "detail": "TikTok returned no videos for those ids"}
            agg = {"views": 0, "likes": 0, "comments": 0, "shares": 0}
            for v in vids:
                agg["views"] += int(v.get("view_count") or 0)
                agg["likes"] += int(v.get("like_count") or 0)
                agg["comments"] += int(v.get("comment_count") or 0)
                agg["shares"] += int(v.get("share_count") or 0)
            return {"available": True, **agg}
        except Exception as e:
            return {"available": False, "detail": f"TikTok stats fetch failed: {e}"}


adapter = TikTokAdapter()

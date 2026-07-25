"""Instagram (+ Facebook Page) publishing adapter — Meta Graph API.

Mirrors the YouTube/TikTok adapter shape (app/social_publish/{youtube,tiktok}.py):
  connect  → Facebook Login dialog (meta_app_id + instagram/pages scopes)
  callback → code exchange at the Graph token endpoint, upgraded to a LONG-LIVED
             user token (~60 days), stored ENCRYPTED (settings key
             meta_access_token, see crypto.SECRET_KEYS). After the exchange we
             auto-discover the linked Facebook Page + IG Business account
             (fb_page_id / fb_page_token / ig_business_id) via /me/accounts.
  upload   → Instagram: Reels resumable protocol — POST /{ig}/media
             (media_type=REELS, upload_type=resumable) → container id + rupload
             uri → POST the file bytes → poll status_code until FINISHED →
             POST /{ig}/media_publish. Facebook Page: multipart POST to
             graph-video.facebook.com /{page}/videos with the page token.
  stats    → per-media likes/comments (+ views via the insights edge) or an
             account snapshot (followers, recent media) when no id is given.

Manual-setup path (no OAuth): the owner can paste meta_access_token (a
long-lived user token or a system-user token) plus ig_business_id / fb_page_id
straight into Settings — connected() lights up from the stored values alone, so
the adapters activate the moment creds exist and report not-configured until
then (no crashes).

Safety notes — the IG content-publish API has NO privacy knob: a published Reel
is PUBLIC immediately (the router's manual-publish endpoint + NSFW hard-block
are the gate; nothing here fires autonomously). Meta requires the app to have
the instagram_content_publish permission approved (App Review) before non-test
users can publish.
"""
import time
import logging
import urllib.parse
from pathlib import Path

import httpx

from db import get_conn
from crypto import enc as _enc, dec as _dec

try:
    from config import META_REDIRECT_URI as REDIRECT_URI
except Exception:
    REDIRECT_URI = "http://localhost:8787/store/api/social/instagram/callback"

from social_publish.base import PublishAdapter

log = logging.getLogger("store")

GRAPH_VERSION = "v21.0"
GRAPH       = f"https://graph.facebook.com/{GRAPH_VERSION}"
GRAPH_VIDEO = f"https://graph-video.facebook.com/{GRAPH_VERSION}"
AUTH_URL    = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
TOKEN_URL   = f"{GRAPH}/oauth/access_token"
SCOPES = ("instagram_basic,instagram_content_publish,instagram_manage_insights,"
          "pages_show_list,pages_read_engagement,pages_manage_posts,business_management")

CAPTION_MAX = 2200          # Instagram caption hard limit
FB_DESC_MAX = 63206         # Facebook post body limit

# Status-poll pacing for IG container processing (module-level so tests can shrink).
POLL_INTERVAL = 5.0
POLL_ATTEMPTS = 60


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


def _graph_error(payload: dict) -> str:
    """Meta's error envelope: {'error': {message, type, code, error_user_msg…}}.
    Returns '' when there is no error block."""
    err = (payload or {}).get("error") or {}
    if not err:
        return ""
    msg = err.get("error_user_msg") or err.get("message") or "Meta Graph API error"
    return f"{err.get('code', '?')}: {msg}"


def _check(r: httpx.Response, what: str) -> dict:
    """Parse a Graph response; raise a readable RuntimeError on HTTP/API errors."""
    try:
        payload = r.json()
    except Exception:
        payload = {}
    err = _graph_error(payload)
    if r.status_code >= 400 or err:
        raise RuntimeError(f"Meta {what} HTTP {r.status_code}: {err or r.text[:400]}")
    return payload


class InstagramAdapter(PublishAdapter):
    """Instagram Reels via an IG Business/Creator account linked to a FB Page."""
    platform = "instagram"

    # ── OAuth / connection state ─────────────────────────────────────────────
    def connected(self) -> bool:
        # Token + IG business id may come from OAuth OR be pasted into Settings —
        # either path lights this up; until then the platform reports needs-login.
        return bool(_get("meta_access_token") and _get("ig_business_id"))

    def auth_url(self, state: str) -> str:
        app_id = _get("meta_app_id")
        if not app_id:
            raise ValueError("Meta App ID not set — save meta_app_id (and meta_app_secret) "
                             "in Settings first.")
        params = urllib.parse.urlencode({
            "client_id":     app_id,
            "redirect_uri":  REDIRECT_URI,
            "response_type": "code",
            "scope":         SCOPES,
            "state":         state,
        })
        return f"{AUTH_URL}?{params}"

    def exchange_code(self, code: str) -> dict:
        """Callback code → short-lived user token → LONG-LIVED user token (~60d).
        Does NOT store — see save_tokens."""
        r = httpx.get(TOKEN_URL, params={
            "client_id":     _get("meta_app_id"),
            "client_secret": _get("meta_app_secret"),
            "redirect_uri":  REDIRECT_URI,
            "code":          code,
        }, timeout=30)
        tokens = _check(r, "token exchange")
        short = tokens.get("access_token")
        if not short:
            raise ValueError(f"Meta token exchange returned no access_token: {str(tokens)[:200]}")
        # Upgrade to a long-lived token right away (short ones die in ~1-2 hours).
        r2 = httpx.get(TOKEN_URL, params={
            "grant_type":        "fb_exchange_token",
            "client_id":         _get("meta_app_id"),
            "client_secret":     _get("meta_app_secret"),
            "fb_exchange_token": short,
        }, timeout=30)
        try:
            long_tokens = _check(r2, "long-lived token exchange")
            if long_tokens.get("access_token"):
                return long_tokens
        except Exception as e:
            log.warning("Meta long-lived token exchange failed, keeping short token: %s", e)
        return tokens

    def save_tokens(self, tokens: dict):
        """Persist the user token ENCRYPTED + its expiry timestamp."""
        if tokens.get("access_token"):
            _set("meta_access_token", tokens["access_token"], secret=True)
        # Long-lived tokens report ~5.2M seconds; default 60 days when omitted.
        _set("meta_token_expires",
             str(int(time.time()) + int(tokens.get("expires_in", 60 * 86400))))

    def discover_accounts(self) -> dict:
        """/me/accounts → first Page with a linked IG Business account (falls back
        to the first Page). Stores fb_page_id / fb_page_token / ig_business_id and
        the IG @username. Returns what was found (for the callback page)."""
        token = _get("meta_access_token")
        r = httpx.get(f"{GRAPH}/me/accounts", params={
            "fields":       "id,name,access_token,instagram_business_account{id,username}",
            "access_token": token,
        }, timeout=30)
        payload = _check(r, "account discovery")
        pages = payload.get("data") or []
        if not pages:
            raise ValueError("No Facebook Pages on this account — Instagram publishing needs "
                             "an IG Business/Creator account linked to a Facebook Page.")
        page = next((p for p in pages if p.get("instagram_business_account")), pages[0])
        found = {"page_id": page.get("id"), "page_name": page.get("name"),
                 "ig_id": "", "ig_username": ""}
        if page.get("id"):
            _set("fb_page_id", page["id"], secret=True)
        if page.get("access_token"):
            _set("fb_page_token", page["access_token"], secret=True)
        ig = page.get("instagram_business_account") or {}
        if ig.get("id"):
            _set("ig_business_id", ig["id"], secret=True)
            found["ig_id"] = ig["id"]
            if ig.get("username"):
                found["ig_username"] = ig["username"]
                _set("social_instagram_handle", "@" + str(ig["username"]).lstrip("@"))
        return found

    def refresh_access_token(self) -> str:
        """Meta has no refresh_token grant — a still-valid long-lived token can be
        re-exchanged (fb_exchange_token) for a fresh ~60-day one. Once truly
        expired the owner must reconnect."""
        token = _get("meta_access_token")
        if not token:
            raise ValueError("Instagram is not connected — use Connect (or set "
                             "meta_access_token in Settings) first.")
        app_id, app_secret = _get("meta_app_id"), _get("meta_app_secret")
        if not (app_id and app_secret):
            return token        # manual token without app creds: use as-is
        r = httpx.get(TOKEN_URL, params={
            "grant_type":        "fb_exchange_token",
            "client_id":         app_id,
            "client_secret":     app_secret,
            "fb_exchange_token": token,
        }, timeout=30)
        tokens = _check(r, "token refresh")
        if not tokens.get("access_token"):
            raise ValueError("Meta token refresh returned no access_token — reconnect "
                             "Instagram from the Social tab.")
        self.save_tokens(tokens)
        return tokens["access_token"]

    def disconnect(self):
        # Tokens only — meta_app_id/meta_app_secret and any hand-set ids stay.
        _delete("meta_access_token", "meta_token_expires", "fb_page_token")

    def _access_token(self) -> str:
        """The stored user token; re-exchanged when <7 days from expiry (best-effort
        — a manually-pasted token with no recorded expiry is returned as-is)."""
        token = _get("meta_access_token")
        if not token:
            raise ValueError("Instagram is not connected — use Connect (or set "
                             "meta_access_token in Settings) first.")
        try:
            expires_at = int(_get("meta_token_expires", "0") or 0)
        except Exception:
            expires_at = 0
        if expires_at and time.time() >= expires_at - 7 * 86400:
            try:
                token = self.refresh_access_token()
            except Exception as e:
                log.warning("Meta token refresh failed (using stored token): %s", e)
        return token

    def _ig_id(self) -> str:
        ig = _get("ig_business_id")
        if not ig:
            raise ValueError("No Instagram Business account id — reconnect Instagram, or set "
                             "ig_business_id in Settings.")
        return ig

    # ── validation ───────────────────────────────────────────────────────────
    def validate(self, meta: dict) -> list[str]:
        caption = (meta.get("title") or "").strip()
        if len(caption) > CAPTION_MAX:
            raise ValueError(f"Instagram caption is {len(caption)} chars — the limit is "
                             f"{CAPTION_MAX}. Shorten it (or set a per-platform IG caption).")
        warnings = []
        w, h = meta.get("width"), meta.get("height")
        if w and h and int(w) > int(h):
            warnings.append(f"Video is landscape ({w}x{h}) — Instagram Reels want vertical "
                            "9:16; it will post cropped/letterboxed.")
        warnings.append("Instagram API posts are PUBLIC immediately — there is no privacy "
                        "level on the Graph publish endpoint.")
        return warnings

    # ── upload (Reels resumable: container → bytes → poll → publish) ─────────
    def upload(self, video_path: str, meta: dict) -> dict:
        p = Path(video_path)
        if not p.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        warnings = self.validate(meta)
        caption = (meta.get("title") or "").strip()[:CAPTION_MAX]
        token = self._access_token()
        ig_id = self._ig_id()
        size = p.stat().st_size

        # 1) create a resumable REELS media container → container id + rupload uri
        r = httpx.post(f"{GRAPH}/{ig_id}/media", data={
            "media_type":    "REELS",
            "upload_type":   "resumable",
            "caption":       caption,
            "share_to_feed": "true",
            "access_token":  token,
        }, timeout=30)
        payload = _check(r, "media container create")
        container_id = payload.get("id")
        upload_uri = payload.get("uri") or \
            f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}/{container_id}"
        if not container_id:
            raise RuntimeError(f"Meta container create returned no id: {str(payload)[:300]}")

        # 2) POST the file bytes to the rupload endpoint (single shot — our clips
        #    are small; `offset: 0` means "the whole file from the start")
        with p.open("rb") as fh:
            up = httpx.post(
                upload_uri,
                content=fh,
                headers={
                    "Authorization": f"OAuth {token}",
                    "offset":        "0",
                    "file_size":     str(size),
                    "Content-Type":  "application/octet-stream",
                },
                timeout=httpx.Timeout(600.0, connect=30.0),
            )
        up_payload = _check(up, "video upload")
        if not (up_payload.get("success") or up_payload.get("id")):
            raise RuntimeError(f"Meta video upload not confirmed: {str(up_payload)[:300]}")

        # 3) poll the container until FINISHED (Reels transcode takes a while)
        last_status = ""
        for _ in range(POLL_ATTEMPTS):
            st = httpx.get(f"{GRAPH}/{container_id}", params={
                "fields": "status_code,status", "access_token": token}, timeout=30)
            sp = _check(st, "container status")
            last_status = str(sp.get("status_code") or "").upper()
            if last_status == "FINISHED":
                break
            if last_status in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram processing failed ({last_status}): "
                                   f"{str(sp.get('status') or '')[:300]}")
            time.sleep(POLL_INTERVAL)
        else:
            raise RuntimeError(f"Instagram container not ready after "
                               f"{int(POLL_ATTEMPTS * POLL_INTERVAL)}s (last status: "
                               f"{last_status or 'unknown'}) — try Publish again shortly; "
                               "the container may still finish.")

        # 4) publish the container → the real media id
        pub = httpx.post(f"{GRAPH}/{ig_id}/media_publish", data={
            "creation_id": container_id, "access_token": token}, timeout=30)
        pub_payload = _check(pub, "media publish")
        media_id = pub_payload.get("id")
        if not media_id:
            raise RuntimeError(f"Instagram publish returned no media id: {str(pub_payload)[:300]}")

        url = ""
        try:
            pl = httpx.get(f"{GRAPH}/{media_id}", params={
                "fields": "permalink", "access_token": token}, timeout=30)
            url = (pl.json() or {}).get("permalink") or ""
        except Exception:
            pass
        log.info("Instagram publish done: media %s (container %s, %d bytes)",
                 media_id, container_id, size)
        return {
            "platform_post_id": media_id,
            "url": url,
            "state": {
                "container_id":     container_id,
                "bytes":            size,
                "container_status": last_status,
            },
            "warnings": warnings,
        }

    # ── post-publish status ──────────────────────────────────────────────────
    def status(self, platform_post_id: str) -> dict:
        """media_publish is synchronous — if the media node resolves, it's live."""
        token = self._access_token()
        r = httpx.get(f"{GRAPH}/{platform_post_id}", params={
            "fields": "id,permalink,media_type", "access_token": token}, timeout=30)
        try:
            payload = r.json()
        except Exception:
            payload = {}
        err = _graph_error(payload)
        if r.status_code >= 400 or err:
            return {"status": "failed", "detail": err or f"HTTP {r.status_code}"}
        return {"status": "live", "detail": payload.get("permalink") or "published"}

    # ── analytics ────────────────────────────────────────────────────────────
    def stats(self, platform_post_id: str = "") -> dict:
        """With a media id: {views, likes, comments, permalink} for that post.
        Without: an account snapshot (followers/media count + recent media)."""
        token = self._access_token()
        if platform_post_id:
            r = httpx.get(f"{GRAPH}/{platform_post_id}", params={
                "fields":       "like_count,comments_count,permalink,media_type,timestamp",
                "access_token": token}, timeout=30)
            payload = _check(r, "media stats")
            out = {
                "media_id":  platform_post_id,
                "likes":     payload.get("like_count", 0),
                "comments":  payload.get("comments_count", 0),
                "permalink": payload.get("permalink") or "",
                "timestamp": payload.get("timestamp") or "",
                "views":     None,
            }
            # views ride the insights edge; metric name varies by API version —
            # best-effort, never fatal ("views" replaced "plays"/"video_views").
            for metric in ("views", "plays", "video_views"):
                try:
                    ins = httpx.get(f"{GRAPH}/{platform_post_id}/insights", params={
                        "metric": metric, "access_token": token}, timeout=30)
                    data = (ins.json() or {}).get("data") or []
                    if ins.status_code < 400 and data:
                        vals = (data[0].get("values") or [{}])
                        out["views"] = vals[-1].get("value", 0)
                        break
                except Exception:
                    continue
            return out
        ig_id = self._ig_id()
        acct = _check(httpx.get(f"{GRAPH}/{ig_id}", params={
            "fields":       "username,followers_count,media_count",
            "access_token": token}, timeout=30), "account stats")
        recent = []
        try:
            med = httpx.get(f"{GRAPH}/{ig_id}/media", params={
                "fields": "id,caption,like_count,comments_count,permalink,timestamp",
                "limit":  10, "access_token": token}, timeout=30)
            for m in (med.json() or {}).get("data") or []:
                recent.append({
                    "media_id":  m.get("id"),
                    "caption":   (m.get("caption") or "")[:80],
                    "likes":     m.get("like_count", 0),
                    "comments":  m.get("comments_count", 0),
                    "permalink": m.get("permalink") or "",
                    "timestamp": m.get("timestamp") or "",
                })
        except Exception as e:
            log.warning("Instagram recent-media stats failed: %s", e)
        return {
            "username":  acct.get("username") or "",
            "followers": acct.get("followers_count", 0),
            "media":     acct.get("media_count", 0),
            "recent":    recent,
        }


class FacebookPageAdapter(PublishAdapter):
    """Facebook Page video posts — same Meta app/token, published as the Page."""
    platform = "facebook"

    def connected(self) -> bool:
        return bool(_get("fb_page_id") and (_get("fb_page_token") or _get("meta_access_token")))

    # OAuth is shared with Instagram — the Social tab exposes ONE Meta connect.
    def auth_url(self, state: str) -> str:
        return adapter.auth_url(state)

    def _page_token(self) -> str:
        """The Page access token — cached from discovery, else derived from the
        user token (GET /{page}?fields=access_token needs pages perms)."""
        tok = _get("fb_page_token")
        if tok:
            return tok
        page_id = _get("fb_page_id")
        user_tok = _get("meta_access_token")
        if not (page_id and user_tok):
            raise ValueError("Facebook Page is not connected — connect Instagram/Meta first "
                             "(or set fb_page_id + meta_access_token in Settings).")
        r = httpx.get(f"{GRAPH}/{page_id}", params={
            "fields": "access_token", "access_token": user_tok}, timeout=30)
        payload = _check(r, "page token fetch")
        tok = payload.get("access_token")
        if not tok:
            raise ValueError("Could not derive a Page access token — reconnect Instagram/Meta "
                             "from the Social tab.")
        _set("fb_page_token", tok, secret=True)
        return tok

    def validate(self, meta: dict) -> list[str]:
        desc = (meta.get("description") or meta.get("title") or "").strip()
        if len(desc) > FB_DESC_MAX:
            raise ValueError(f"Facebook post text is {len(desc)} chars — the limit is {FB_DESC_MAX}.")
        return ["Facebook Page API posts are PUBLIC immediately — there is no privacy "
                "level on the video publish endpoint."]

    def upload(self, video_path: str, meta: dict) -> dict:
        p = Path(video_path)
        if not p.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        warnings = self.validate(meta)
        page_id = _get("fb_page_id")
        if not page_id:
            raise ValueError("No Facebook Page id — reconnect Instagram/Meta, or set "
                             "fb_page_id in Settings.")
        token = self._page_token()
        size = p.stat().st_size
        data = {
            "description":  (meta.get("description") or meta.get("title") or "").strip(),
            "access_token": token,
        }
        if (meta.get("fb_title") or "").strip():
            data["title"] = meta["fb_title"].strip()
        # Simple multipart upload — our generated clips are small (well under the
        # ~1GB threshold where the chunked/resumable protocol becomes necessary).
        with p.open("rb") as fh:
            r = httpx.post(
                f"{GRAPH_VIDEO}/{page_id}/videos",
                data=data,
                files={"source": (p.name, fh, "video/mp4")},
                timeout=httpx.Timeout(600.0, connect=30.0),
            )
        payload = _check(r, "page video upload")
        video_id = payload.get("id")
        if not video_id:
            raise RuntimeError(f"Facebook upload returned no video id: {str(payload)[:300]}")
        log.info("Facebook Page video upload done: %s (%d bytes)", video_id, size)
        return {
            "platform_post_id": video_id,
            "url": f"https://www.facebook.com/{page_id}/videos/{video_id}",
            "state": {"bytes": size, "page_id": page_id},
            "warnings": warnings,
        }

    def status(self, platform_post_id: str) -> dict:
        token = self._page_token()
        r = httpx.get(f"{GRAPH}/{platform_post_id}", params={
            "fields": "status,permalink_url", "access_token": token}, timeout=30)
        try:
            payload = r.json()
        except Exception:
            payload = {}
        err = _graph_error(payload)
        if r.status_code >= 400 or err:
            return {"status": "failed", "detail": err or f"HTTP {r.status_code}"}
        st = str(((payload.get("status") or {}).get("video_status")) or "").lower()
        if st == "ready":
            mapped = "live"
        elif st in ("error", "blocked"):
            mapped = "failed"
        else:                       # processing / upload_complete / …
            mapped = "processing"
        return {"status": mapped, "detail": payload.get("permalink_url") or st or "unknown"}

    def stats(self, platform_post_id: str = "") -> dict:
        """Best-effort Page-video metrics (views via video_insights, never fatal)."""
        if not platform_post_id:
            return {"detail": "pass a video id for Facebook stats"}
        token = self._page_token()
        r = httpx.get(f"{GRAPH}/{platform_post_id}", params={
            "fields": "title,description,permalink_url,created_time", "access_token": token},
            timeout=30)
        payload = _check(r, "video stats")
        out = {"video_id": platform_post_id,
               "permalink": payload.get("permalink_url") or "",
               "created": payload.get("created_time") or "", "views": None}
        try:
            ins = httpx.get(f"{GRAPH}/{platform_post_id}/video_insights", params={
                "metric": "total_video_views", "access_token": token}, timeout=30)
            data = (ins.json() or {}).get("data") or []
            if ins.status_code < 400 and data:
                out["views"] = (data[0].get("values") or [{}])[0].get("value", 0)
        except Exception:
            pass
        return out


adapter = InstagramAdapter()
fb_adapter = FacebookPageAdapter()

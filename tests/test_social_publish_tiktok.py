"""Social auto-publishing — TikTok (Content Posting API Direct Post) tests.

Locks down:
  • OAuth connect: refuses without a client key; the authorize URL carries the
    right client_key / scope / redirect_uri / state (stored single-use).
  • Callback: rejects a bad state; on success stores tokens ENCRYPTED at rest
    (form-encoded client_key+client_secret exchange at the v2 token endpoint).
  • Publish: HARD-refuses NSFW videos, refuses when not connected, drives the
    social_publications row pending → live/failed via the (mocked) adapter.
  • Direct Post wire format: init POST (post_info + source_info FILE_UPLOAD) →
    publish_id + upload_url → PUT bytes with Content-Range → status poll.
  • PRIVACY GATING: an unaudited app ALWAYS sends SELF_ONLY, even when a public
    level is requested; tiktok_audited=1 unlocks the public levels.
  • Caption limit (2200) enforced before any network call.

ALL TikTok/httpx calls are mocked — no test touches the network.
"""
import time

import pytest

import db
import crypto
from social_publish import tiktok as tt


# ── helpers ──────────────────────────────────────────────────────────────────
TT_KEYS = ("tiktok_client_key", "tiktok_client_secret", "tiktok_access_token",
           "tiktok_refresh_token", "tiktok_token_expires", "tiktok_oauth_state",
           "social_tiktok_privacy", "tiktok_audited", "social_tiktok_handle")


def _clear_tt_settings():
    conn = db.get_conn()
    conn.execute(f"DELETE FROM settings WHERE key IN ({','.join('?' * len(TT_KEYS))})", TT_KEYS)
    conn.commit()
    conn.close()


def _set_setting(key, value):
    conn = db.get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
    conn.commit()
    conn.close()


def _get_setting_raw(key):
    conn = db.get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def _connect_fake():
    """Pretend TikTok is connected with a non-expiring fake token."""
    _set_setting("tiktok_client_key", "fake-client-key")
    _set_setting("tiktok_client_secret", "fake-client-secret")
    _set_setting("tiktok_access_token", "fake-access")
    _set_setting("tiktok_refresh_token", "fake-refresh")
    _set_setting("tiktok_token_expires", str(int(time.time()) + 100000))


def _mk_video(video_path="/tmp/tt/clip1.mp4", nsfw=0, audio_path=None, w=480, h=832):
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO videos (prompt, status, video_path, audio_path, width, height, nsfw) "
        "VALUES ('tt clip', 'done', ?, ?, ?, ?, ?)", (video_path, audio_path, w, h, nsfw))
    conn.commit()
    vid = cur.lastrowid
    conn.close()
    return vid


def _mk_post(client, **overrides):
    body = {"caption": "A cool tiktok", "hashtags": "#jelly #test",
            "platforms": ["tiktok"], "media_type": "video",
            "media_path": "/tmp/tt/clip1.mp4"}
    body.update(overrides)
    r = client.post("/api/social/posts", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


class FakeResp:
    def __init__(self, json_data=None, headers=None, status_code=200):
        self._json = json_data or {}
        self.headers = headers or {}
        self.status_code = status_code
        self.text = str(json_data)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def _no_network(*a, **k):
    raise AssertionError("network call attempted — TikTok/httpx must be mocked in tests")


def _happy_httpx(monkeypatch, calls, status_json=None):
    """Mock the whole direct-post protocol: init → upload PUT → status fetch."""
    status_json = status_json or {
        "data": {"status": "PUBLISH_COMPLETE",
                 "publicaly_available_post_id": [7345678901234567890]},
        "error": {"code": "ok"},
    }

    def fake_post(url, json=None, data=None, headers=None, **kw):
        calls.setdefault("post", []).append({"url": url, "json": json, "data": data,
                                             "headers": headers})
        if url == tt.INIT_URL:
            return FakeResp({"data": {"publish_id": "v_pub.abc123",
                                      "upload_url": "https://upload.tiktokapis.example/u1"},
                             "error": {"code": "ok"}})
        if url == tt.STATUS_URL:
            return FakeResp(status_json)
        raise AssertionError(f"unexpected POST to {url}")

    def fake_put(url, content=None, headers=None, **kw):
        calls.setdefault("put", []).append({"url": url, "bytes": content.read(),
                                            "headers": headers})
        return FakeResp({})

    monkeypatch.setattr(tt.httpx, "post", fake_post)
    monkeypatch.setattr(tt.httpx, "put", fake_put)


# ── OAuth connect / callback ─────────────────────────────────────────────────
def test_connect_refuses_without_client_key(client, monkeypatch):
    _clear_tt_settings()
    monkeypatch.setattr(tt.httpx, "post", _no_network)
    r = client.get("/api/social/tiktok/connect")
    assert r.status_code == 400
    assert "tiktok_client_key" in r.json()["detail"]


def test_connect_url_has_client_key_scope_redirect_state(client, monkeypatch):
    _clear_tt_settings()
    _set_setting("tiktok_client_key", "fake-client-key")
    monkeypatch.setattr(tt.httpx, "post", _no_network)   # connect must not hit the network
    r = client.get("/api/social/tiktok/connect")
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.startswith("https://www.tiktok.com/v2/auth/authorize/?")
    assert "client_key=fake-client-key" in url
    assert "response_type=code" in url
    # scope is url-encoded: user.info.basic,video.upload,video.publish
    assert "user.info.basic%2Cvideo.upload%2Cvideo.publish" in url
    assert "%2Fapi%2Fsocial%2Ftiktok%2Fcallback" in url   # url-encoded redirect_uri
    state = _get_setting_raw("tiktok_oauth_state")
    assert state and f"state={state}" in url


def test_callback_rejects_bad_state(client, monkeypatch):
    _clear_tt_settings()
    _set_setting("tiktok_oauth_state", "expected-state")
    monkeypatch.setattr(tt.httpx, "post", _no_network)
    r = client.get("/api/social/tiktok/callback", params={"code": "abc", "state": "WRONG"})
    assert r.status_code == 200
    assert "Invalid OAuth state" in r.text
    assert _get_setting_raw("tiktok_access_token") is None


def test_callback_exchanges_and_stores_encrypted(client, monkeypatch):
    _clear_tt_settings()
    _set_setting("tiktok_client_key", "fake-client-key")
    _set_setting("tiktok_client_secret", "fake-client-secret")
    _set_setting("tiktok_oauth_state", "state-tt-1")
    calls = {}

    def fake_post(url, data=None, headers=None, **kw):
        calls["url"] = url
        calls["data"] = data
        calls["headers"] = headers
        return FakeResp({"access_token": "tt-access", "refresh_token": "tt-refresh",
                         "expires_in": 86400, "refresh_expires_in": 31536000,
                         "open_id": "openid-1", "scope": tt.SCOPES})

    monkeypatch.setattr(tt.httpx, "post", fake_post)
    r = client.get("/api/social/tiktok/callback", params={"code": "the-code", "state": "state-tt-1"})
    assert r.status_code == 200
    assert "tiktok_connected" in r.text            # postMessage for the opener popup
    assert calls["url"] == "https://open.tiktokapis.com/v2/oauth/token/"
    # TikTok wants client_key + client_secret FORM-ENCODED
    assert calls["data"]["grant_type"] == "authorization_code"
    assert calls["data"]["code"] == "the-code"
    assert calls["data"]["client_key"] == "fake-client-key"
    assert calls["data"]["client_secret"] == "fake-client-secret"
    assert "x-www-form-urlencoded" in calls["headers"]["Content-Type"]
    # tokens are stored ENCRYPTED at rest, decrypt back to the real values
    raw_access = _get_setting_raw("tiktok_access_token")
    raw_refresh = _get_setting_raw("tiktok_refresh_token")
    assert crypto.is_encrypted(raw_access) and crypto.dec(raw_access) == "tt-access"
    assert crypto.is_encrypted(raw_refresh) and crypto.dec(raw_refresh) == "tt-refresh"
    assert int(_get_setting_raw("tiktok_token_expires")) > time.time()
    # state is single-use
    assert _get_setting_raw("tiktok_oauth_state") is None
    # status endpoint reflects the connection (and the unaudited default privacy)
    st = client.get("/api/social/tiktok/status").json()
    assert st["connected"] is True and st["has_client"] is True
    assert st["privacy"] == "SELF_ONLY" and st["audited"] is False
    # disconnect clears the tokens
    r = client.delete("/api/social/tiktok/disconnect")
    assert r.status_code == 200
    assert _get_setting_raw("tiktok_access_token") is None
    assert _get_setting_raw("tiktok_refresh_token") is None


# ── publish flow ─────────────────────────────────────────────────────────────
def test_publish_hard_refuses_nsfw_video(client, monkeypatch):
    _connect_fake()
    monkeypatch.setattr(tt.httpx, "post", _no_network)
    monkeypatch.setattr(tt.httpx, "put", _no_network)
    vid = _mk_video(video_path="/tmp/tt/nsfw.mp4", nsfw=1)
    pid = _mk_post(client, video_id=vid, media_path="/tmp/tt/nsfw.mp4")
    r = client.post(f"/api/social/posts/{pid}/publish", json={"platforms": ["tiktok"]})
    assert r.status_code == 403
    assert "NSFW" in r.json()["detail"]
    # no publication row was created
    r = client.get(f"/api/social/posts/{pid}/publications")
    assert r.json()["publications"] == []


def test_publish_refuses_when_not_connected(client, monkeypatch):
    _clear_tt_settings()
    monkeypatch.setattr(tt.httpx, "post", _no_network)
    vid = _mk_video(video_path="/tmp/tt/ok1.mp4")
    pid = _mk_post(client, video_id=vid, media_path="/tmp/tt/ok1.mp4")
    r = client.post(f"/api/social/posts/{pid}/publish", json={"platforms": ["tiktok"]})
    assert r.status_code == 400
    assert "connected" in r.json()["detail"].lower()
    assert "TikTok" in r.json()["detail"]


def test_publish_creates_row_and_goes_live(client, monkeypatch):
    _connect_fake()
    seen = {}

    def fake_upload(video_path, meta):
        seen["video_path"] = video_path
        seen["meta"] = meta
        return {"platform_post_id": "v_pub.live1", "url": "",
                "state": {"publish_status": "PUBLISH_COMPLETE"}, "warnings": []}

    monkeypatch.setattr(tt.adapter, "upload", fake_upload)
    vid = _mk_video(video_path="/tmp/tt/live1.mp4", w=480, h=832)
    pid = _mk_post(client, video_id=vid, media_path="/tmp/tt/live1.mp4",
                   caption="Great tiktok", hashtags="#jelly #wow")
    r = client.post(f"/api/social/posts/{pid}/publish", json={"platforms": ["tiktok"]})
    assert r.status_code == 200, r.text
    pub_id = r.json()["publication_id"]
    # TestClient runs BackgroundTasks synchronously after the response
    pubs = client.get(f"/api/social/posts/{pid}/publications").json()["publications"]
    assert len(pubs) == 1
    p = pubs[0]
    assert p["id"] == pub_id and p["platform"] == "tiktok"
    assert p["status"] == "live"
    assert p["platform_post_id"] == "v_pub.live1"
    # tiktok meta: title IS the caption (+hashtags inline)
    assert seen["video_path"] == "/tmp/tt/live1.mp4"
    assert seen["meta"]["title"] == "Great tiktok\n\n#jelly #wow"
    # the post now shows tiktok in posted_on
    posts = client.get("/api/social/posts").json()["posts"]
    me = next(x for x in posts if x["id"] == pid)
    assert "tiktok" in me["posted_on"]
    # re-publish while live is a friendly no-op
    r = client.post(f"/api/social/posts/{pid}/publish", json={"platforms": ["tiktok"]})
    assert r.status_code == 200 and r.json().get("already_live") is True


def test_publish_failure_records_error(client, monkeypatch):
    _connect_fake()

    def boom(video_path, meta):
        raise RuntimeError("spam_risk_too_many_posts (mock)")

    monkeypatch.setattr(tt.adapter, "upload", boom)
    vid = _mk_video(video_path="/tmp/tt/fail1.mp4")
    pid = _mk_post(client, video_id=vid, media_path="/tmp/tt/fail1.mp4")
    r = client.post(f"/api/social/posts/{pid}/publish", json={"platforms": ["tiktok"]})
    assert r.status_code == 200
    pubs = client.get(f"/api/social/posts/{pid}/publications").json()["publications"]
    assert pubs[0]["status"] == "failed"
    assert "spam_risk" in pubs[0]["error"]


# ── adapter: direct post protocol + privacy gating ───────────────────────────
def test_direct_post_wire_format(client, monkeypatch, tmp_path):
    _connect_fake()
    _set_setting("social_tiktok_handle", "@acme")
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x01" * 2048)
    calls = {}
    _happy_httpx(monkeypatch, calls)

    res = tt.adapter.upload(str(f), {"title": "My caption #jelly", "width": 480, "height": 832})
    # init: post_info + source_info, bearer auth
    init = calls["post"][0]
    assert init["url"] == "https://open.tiktokapis.com/v2/post/publish/video/init/"
    assert init["headers"]["Authorization"] == "Bearer fake-access"
    assert init["json"]["post_info"]["title"] == "My caption #jelly"
    assert init["json"]["post_info"]["privacy_level"] == "SELF_ONLY"   # safe default
    assert init["json"]["post_info"]["disable_comment"] is False
    si = init["json"]["source_info"]
    assert si["source"] == "FILE_UPLOAD"
    assert si["video_size"] == 2048 and si["chunk_size"] == 2048 and si["total_chunk_count"] == 1
    # bytes went to the upload_url with a single-chunk Content-Range
    put = calls["put"][0]
    assert put["url"] == "https://upload.tiktokapis.example/u1"
    assert put["bytes"] == b"\x01" * 2048
    assert put["headers"]["Content-Range"] == "bytes 0-2047/2048"
    assert put["headers"]["Content-Type"] == "video/mp4"
    # status poll asked about our publish_id
    status = calls["post"][1]
    assert status["url"] == "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
    assert status["json"] == {"publish_id": "v_pub.abc123"}
    # result: publish_id is the platform_post_id; url built from handle + post id
    assert res["platform_post_id"] == "v_pub.abc123"
    assert res["url"] == "https://www.tiktok.com/@acme/video/7345678901234567890"
    assert res["state"]["publish_status"] == "PUBLISH_COMPLETE"
    assert not any("landscape" in w.lower() for w in res["warnings"])   # 480x832 is vertical


def test_unaudited_forces_self_only_even_when_public_requested(client, monkeypatch, tmp_path):
    _connect_fake()
    _set_setting("social_tiktok_privacy", "PUBLIC_TO_EVERYONE")
    _set_setting("tiktok_audited", "0")                       # explicit: NOT audited
    f = tmp_path / "clip2.mp4"
    f.write_bytes(b"x")
    calls = {}
    _happy_httpx(monkeypatch, calls)

    res = tt.adapter.upload(str(f), {"title": "t"})
    assert calls["post"][0]["json"]["post_info"]["privacy_level"] == "SELF_ONLY"
    assert any("unaudited" in w.lower() for w in res["warnings"])
    # same downgrade when the public level comes via per-post meta
    calls.clear()
    res = tt.adapter.upload(str(f), {"title": "t", "privacy": "MUTUAL_FOLLOW_FRIENDS"})
    assert calls["post"][0]["json"]["post_info"]["privacy_level"] == "SELF_ONLY"
    assert any("unaudited" in w.lower() for w in res["warnings"])


def test_audited_app_may_post_public(client, monkeypatch, tmp_path):
    _connect_fake()
    _set_setting("social_tiktok_privacy", "PUBLIC_TO_EVERYONE")
    _set_setting("tiktok_audited", "1")
    f = tmp_path / "clip3.mp4"
    f.write_bytes(b"x")
    calls = {}
    _happy_httpx(monkeypatch, calls)

    res = tt.adapter.upload(str(f), {"title": "t"})
    assert calls["post"][0]["json"]["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert not any("unaudited" in w.lower() for w in res["warnings"])
    _set_setting("tiktok_audited", "0")   # don't leak audited state into other tests


def test_landscape_warns_but_uploads(client, monkeypatch, tmp_path):
    _connect_fake()
    f = tmp_path / "wide.mp4"
    f.write_bytes(b"x")
    calls = {}
    _happy_httpx(monkeypatch, calls)
    res = tt.adapter.upload(str(f), {"title": "t", "width": 832, "height": 480})
    assert any("landscape" in w.lower() for w in res["warnings"])
    assert calls["put"], "upload must proceed despite the aspect warning"


def test_caption_limit_enforced_before_network(client, monkeypatch, tmp_path):
    _connect_fake()
    monkeypatch.setattr(tt.httpx, "post", _no_network)
    monkeypatch.setattr(tt.httpx, "put", _no_network)
    f = tmp_path / "clip4.mp4"
    f.write_bytes(b"x")
    with pytest.raises(ValueError, match="2200"):
        tt.adapter.upload(str(f), {"title": "T" * 2201})
    with pytest.raises(FileNotFoundError):
        tt.adapter.upload(str(tmp_path / "missing.mp4"), {"title": "ok"})


def test_upload_failed_status_raises(client, monkeypatch, tmp_path):
    _connect_fake()
    f = tmp_path / "clip5.mp4"
    f.write_bytes(b"x")
    calls = {}
    _happy_httpx(monkeypatch, calls,
                 status_json={"data": {"status": "FAILED", "fail_reason": "video_too_long"},
                              "error": {"code": "ok"}})
    with pytest.raises(RuntimeError, match="video_too_long"):
        tt.adapter.upload(str(f), {"title": "t"})


def test_status_mapping(client, monkeypatch):
    _connect_fake()

    def status_for(st):
        monkeypatch.setattr(
            tt.httpx, "post",
            lambda *a, **k: FakeResp({"data": {"status": st}, "error": {"code": "ok"}}))
        return tt.adapter.status("v_pub.x")["status"]

    assert status_for("PUBLISH_COMPLETE") == "live"
    assert status_for("FAILED") == "failed"
    assert status_for("PROCESSING_UPLOAD") == "processing"
    assert status_for("PROCESSING_DOWNLOAD") == "processing"
    monkeypatch.setattr(
        tt.httpx, "post",
        lambda *a, **k: FakeResp({"error": {"code": "invalid_publish_id", "message": "nope"}}))
    assert tt.adapter.status("gone")["status"] == "failed"


def test_token_refresh_persists_new_access_token(client, monkeypatch):
    _connect_fake()
    _set_setting("tiktok_token_expires", "1")   # long expired → refresh required

    def fake_post(url, data=None, headers=None, **kw):
        assert url == tt.TOKEN_URL
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "fake-refresh"
        assert data["client_key"] == "fake-client-key"
        return FakeResp({"access_token": "fresh-tt", "refresh_token": "fresh-rt",
                         "expires_in": 86400})

    monkeypatch.setattr(tt.httpx, "post", fake_post)
    tok = tt.adapter._access_token()
    assert tok == "fresh-tt"
    raw = _get_setting_raw("tiktok_access_token")
    assert crypto.is_encrypted(raw) and crypto.dec(raw) == "fresh-tt"
    assert int(_get_setting_raw("tiktok_token_expires")) > time.time()

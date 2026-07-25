"""Social auto-publishing Phase 1 (YouTube) — endpoint + adapter tests.

Locks down:
  • OAuth connect: refuses without a client id; the authorize URL carries the
    right client_id / scope / redirect_uri / offline params and a stored state.
  • Callback: rejects a bad state; on success stores tokens ENCRYPTED at rest.
  • Publish: HARD-refuses NSFW videos, refuses posts without a DB-known video,
    refuses when not connected, creates a social_publications row and drives it
    pending → uploading → live/failed via the (mocked) adapter.
  • Resumable upload protocol: init POST → Location session URI → PUT bytes →
    videoId; privacy defaults to "private"; selfDeclaredMadeForKids is False;
    title/description limits enforced; landscape only warns.
  • Media picker: offers compiled chains, prefers the audio-muxed copy, and
    never lists NSFW rows.

ALL Google/httpx calls are mocked — no test touches the network.
"""
import time

import pytest

import db
import crypto
from social_publish import youtube as yt


# ── helpers ──────────────────────────────────────────────────────────────────
YT_KEYS = ("yt_client_id", "yt_client_secret", "yt_access_token",
           "yt_refresh_token", "yt_token_expires", "yt_oauth_state",
           "social_youtube_privacy")


def _clear_yt_settings():
    conn = db.get_conn()
    conn.execute(f"DELETE FROM settings WHERE key IN ({','.join('?' * len(YT_KEYS))})", YT_KEYS)
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
    """Pretend YouTube is connected with a non-expiring fake token."""
    _set_setting("yt_client_id", "fake-client-id.apps.googleusercontent.com")
    _set_setting("yt_client_secret", "fake-secret")
    _set_setting("yt_access_token", "fake-access")
    _set_setting("yt_refresh_token", "fake-refresh")
    _set_setting("yt_token_expires", str(int(time.time()) + 100000))


def _mk_video(video_path="/tmp/x/clip1.mp4", nsfw=0, audio_path=None, w=480, h=832):
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO videos (prompt, status, video_path, audio_path, width, height, nsfw) "
        "VALUES ('test clip', 'done', ?, ?, ?, ?, ?)", (video_path, audio_path, w, h, nsfw))
    conn.commit()
    vid = cur.lastrowid
    conn.close()
    return vid


def _mk_chain(compiled_path="/tmp/x/chain_compiled.mp4", nsfw=0, w=480, h=832):
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO video_chains (title, concept, status, compiled_path, width, height, nsfw) "
        "VALUES ('test chain', 'concept', 'done', ?, ?, ?, ?)", (compiled_path, w, h, nsfw))
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def _mk_post(client, **overrides):
    body = {"caption": "A cool video", "hashtags": "#jelly #test",
            "platforms": ["youtube"], "media_type": "video",
            "media_path": "/tmp/x/clip1.mp4"}
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
    raise AssertionError("network call attempted — Google/httpx must be mocked in tests")


# ── OAuth connect / callback ─────────────────────────────────────────────────
def test_connect_refuses_without_client_id(client, monkeypatch):
    _clear_yt_settings()
    monkeypatch.setattr(yt.httpx, "post", _no_network)
    r = client.get("/api/social/youtube/connect")
    assert r.status_code == 400
    assert "yt_client_id" in r.json()["detail"]


def test_connect_url_has_client_scope_redirect_state(client, monkeypatch):
    _clear_yt_settings()
    _set_setting("yt_client_id", "fake-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(yt.httpx, "post", _no_network)   # connect must not hit the network
    r = client.get("/api/social/youtube/connect")
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=fake-client-id.apps.googleusercontent.com" in url
    assert "youtube.upload" in url and "youtube.readonly" in url
    assert "response_type=code" in url
    assert "access_type=offline" in url and "prompt=consent" in url
    assert "%2Fapi%2Fsocial%2Fyoutube%2Fcallback" in url   # url-encoded redirect_uri
    state = _get_setting_raw("yt_oauth_state")
    assert state and f"state={state}" in url


def test_callback_rejects_bad_state(client, monkeypatch):
    _clear_yt_settings()
    _set_setting("yt_oauth_state", "expected-state")
    monkeypatch.setattr(yt.httpx, "post", _no_network)
    r = client.get("/api/social/youtube/callback", params={"code": "abc", "state": "WRONG"})
    assert r.status_code == 200
    assert "Invalid OAuth state" in r.text
    assert _get_setting_raw("yt_access_token") is None


def test_callback_exchanges_and_stores_encrypted(client, monkeypatch):
    _clear_yt_settings()
    _set_setting("yt_client_id", "fake-client-id")
    _set_setting("yt_client_secret", "fake-secret")
    _set_setting("yt_oauth_state", "state-123")
    calls = {}

    def fake_post(url, data=None, **kw):
        calls["url"] = url
        calls["data"] = data
        return FakeResp({"access_token": "tok-access", "refresh_token": "tok-refresh",
                         "expires_in": 3600})

    monkeypatch.setattr(yt.httpx, "post", fake_post)
    r = client.get("/api/social/youtube/callback", params={"code": "the-code", "state": "state-123"})
    assert r.status_code == 200
    assert "youtube_connected" in r.text          # postMessage for the opener popup
    assert calls["url"] == "https://oauth2.googleapis.com/token"
    assert calls["data"]["grant_type"] == "authorization_code"
    assert calls["data"]["code"] == "the-code"
    assert calls["data"]["client_secret"] == "fake-secret"
    # tokens are stored ENCRYPTED at rest, decrypt back to the real values
    raw_access = _get_setting_raw("yt_access_token")
    raw_refresh = _get_setting_raw("yt_refresh_token")
    assert crypto.is_encrypted(raw_access) and crypto.dec(raw_access) == "tok-access"
    assert crypto.is_encrypted(raw_refresh) and crypto.dec(raw_refresh) == "tok-refresh"
    assert int(_get_setting_raw("yt_token_expires")) > time.time()
    # state is single-use
    assert _get_setting_raw("yt_oauth_state") is None
    # disconnect clears the tokens
    r = client.delete("/api/social/youtube/disconnect")
    assert r.status_code == 200
    assert _get_setting_raw("yt_access_token") is None
    assert _get_setting_raw("yt_refresh_token") is None


# ── publish flow ─────────────────────────────────────────────────────────────
def test_publish_hard_refuses_nsfw_video(client, monkeypatch):
    _connect_fake()
    monkeypatch.setattr(yt.httpx, "post", _no_network)
    monkeypatch.setattr(yt.httpx, "put", _no_network)
    vid = _mk_video(video_path="/tmp/x/nsfw.mp4", nsfw=1)
    pid = _mk_post(client, video_id=vid, media_path="/tmp/x/nsfw.mp4")
    r = client.post(f"/api/social/posts/{pid}/publish", json={"platforms": ["youtube"]})
    assert r.status_code == 403
    assert "NSFW" in r.json()["detail"]
    # no publication row was created
    r = client.get(f"/api/social/posts/{pid}/publications")
    assert r.json()["publications"] == []


def test_publish_refuses_nsfw_chain(client, monkeypatch):
    _connect_fake()
    monkeypatch.setattr(yt.httpx, "post", _no_network)
    cid = _mk_chain(compiled_path="/tmp/x/nsfw_chain.mp4", nsfw=1)
    pid = _mk_post(client, chain_id=cid, media_path="/tmp/x/nsfw_chain.mp4")
    r = client.post(f"/api/social/posts/{pid}/publish", json={})
    assert r.status_code == 403


def test_publish_refuses_without_known_video(client, monkeypatch):
    _connect_fake()
    monkeypatch.setattr(yt.httpx, "post", _no_network)
    pid = _mk_post(client, media_path="/nowhere/not-in-db.mp4")
    r = client.post(f"/api/social/posts/{pid}/publish", json={})
    assert r.status_code == 400
    assert "video" in r.json()["detail"].lower()


def test_publish_refuses_when_not_connected(client, monkeypatch):
    _clear_yt_settings()
    monkeypatch.setattr(yt.httpx, "post", _no_network)
    vid = _mk_video(video_path="/tmp/x/ok1.mp4")
    pid = _mk_post(client, video_id=vid, media_path="/tmp/x/ok1.mp4")
    r = client.post(f"/api/social/posts/{pid}/publish", json={})
    assert r.status_code == 400
    assert "connected" in r.json()["detail"].lower()


def test_publish_refuses_unsupported_platform(client):
    # tiktok graduated to a real adapter — instagram is still unsupported
    r = client.post("/api/social/posts/999999/publish", json={"platforms": ["instagram"]})
    assert r.status_code == 400
    assert "instagram" in r.json()["detail"]


def test_publish_creates_row_and_goes_live(client, monkeypatch):
    _connect_fake()
    seen = {}

    def fake_upload(video_path, meta):
        seen["video_path"] = video_path
        seen["meta"] = meta
        return {"platform_post_id": "vid-abc123", "url": "https://youtu.be/vid-abc123",
                "state": {"session_uri": "https://upload.example/sess"}, "warnings": []}

    monkeypatch.setattr(yt.adapter, "upload", fake_upload)
    vid = _mk_video(video_path="/tmp/x/live1.mp4", w=480, h=832)
    pid = _mk_post(client, video_id=vid, media_path="/tmp/x/live1.mp4",
                   caption="Great video", hashtags="#jelly #wow")
    r = client.post(f"/api/social/posts/{pid}/publish", json={"platforms": ["youtube"]})
    assert r.status_code == 200, r.text
    pub_id = r.json()["publication_id"]
    # TestClient runs BackgroundTasks synchronously after the response
    r = client.get(f"/api/social/posts/{pid}/publications")
    pubs = r.json()["publications"]
    assert len(pubs) == 1
    p = pubs[0]
    assert p["id"] == pub_id and p["platform"] == "youtube"
    assert p["status"] == "live"
    assert p["platform_post_id"] == "vid-abc123"
    assert p["platform_url"] == "https://youtu.be/vid-abc123"
    assert p["attempts"] == 1
    # meta was built from the post; tags lose their '#'
    assert seen["video_path"] == "/tmp/x/live1.mp4"
    assert seen["meta"]["title"] == "Great video"
    assert "jelly" in seen["meta"]["tags"] and "wow" in seen["meta"]["tags"]
    # the post now shows youtube in posted_on
    posts = client.get("/api/social/posts").json()["posts"]
    me = next(x for x in posts if x["id"] == pid)
    assert "youtube" in me["posted_on"]
    # re-publish while live is a friendly no-op
    r = client.post(f"/api/social/posts/{pid}/publish", json={})
    assert r.status_code == 200 and r.json().get("already_live") is True


def test_publish_failure_records_error(client, monkeypatch):
    _connect_fake()

    def boom(video_path, meta):
        raise RuntimeError("quota exceeded (mock)")

    monkeypatch.setattr(yt.adapter, "upload", boom)
    vid = _mk_video(video_path="/tmp/x/fail1.mp4")
    pid = _mk_post(client, video_id=vid, media_path="/tmp/x/fail1.mp4")
    r = client.post(f"/api/social/posts/{pid}/publish", json={})
    assert r.status_code == 200
    pubs = client.get(f"/api/social/posts/{pid}/publications").json()["publications"]
    assert pubs[0]["status"] == "failed"
    assert "quota exceeded" in pubs[0]["error"]
    # a retry re-uses the same row (UNIQUE post_id+platform) and bumps attempts
    monkeypatch.setattr(yt.adapter, "upload",
                        lambda vp, m: {"platform_post_id": "v2", "url": "https://youtu.be/v2",
                                       "state": {}, "warnings": []})
    r = client.post(f"/api/social/posts/{pid}/publish", json={})
    assert r.status_code == 200
    pubs = client.get(f"/api/social/posts/{pid}/publications").json()["publications"]
    assert len(pubs) == 1
    assert pubs[0]["status"] == "live" and pubs[0]["attempts"] == 2


# ── adapter: resumable upload protocol ───────────────────────────────────────
def test_resumable_upload_flow(client, monkeypatch, tmp_path):
    _connect_fake()
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 1024)
    calls = {"post": [], "put": []}

    def fake_post(url, json=None, headers=None, **kw):
        calls["post"].append({"url": url, "json": json, "headers": headers})
        return FakeResp({}, headers={"location": "https://upload.example/session-1"})

    def fake_put(url, content=None, headers=None, **kw):
        calls["put"].append({"url": url, "bytes": content.read(), "headers": headers})
        return FakeResp({"id": "res-video-9", "status": {"uploadStatus": "uploaded"}})

    monkeypatch.setattr(yt.httpx, "post", fake_post)
    monkeypatch.setattr(yt.httpx, "put", fake_put)

    res = yt.adapter.upload(str(f), {"title": "My title", "description": "desc",
                                     "tags": ["a", "b"], "width": 832, "height": 480})
    # init request: resumable, snippet+status, metadata body, safe status defaults
    init = calls["post"][0]
    assert init["url"].startswith("https://www.googleapis.com/upload/youtube/v3/videos")
    assert "uploadType=resumable" in init["url"] and "part=snippet,status" in init["url"]
    assert init["json"]["snippet"]["title"] == "My title"
    assert init["json"]["snippet"]["tags"] == ["a", "b"]
    assert init["json"]["status"]["privacyStatus"] == "private"      # safe default
    assert init["json"]["status"]["selfDeclaredMadeForKids"] is False
    assert init["headers"]["X-Upload-Content-Length"] == "1024"
    # bytes went to the session URI from the Location header
    put = calls["put"][0]
    assert put["url"] == "https://upload.example/session-1"
    assert put["bytes"] == b"\x00" * 1024
    assert put["headers"]["Content-Type"] == "video/mp4"
    # result
    assert res["platform_post_id"] == "res-video-9"
    assert res["url"] == "https://youtu.be/res-video-9"
    assert res["state"]["session_uri"] == "https://upload.example/session-1"
    # 832x480 is landscape → warned, not blocked
    assert any("landscape" in w.lower() for w in res["warnings"])


def test_upload_respects_privacy_setting(client, monkeypatch, tmp_path):
    _connect_fake()
    _set_setting("social_youtube_privacy", "unlisted")
    f = tmp_path / "clip2.mp4"
    f.write_bytes(b"x")
    captured = {}

    def fake_post(url, json=None, headers=None, **kw):
        captured["json"] = json
        return FakeResp({}, headers={"location": "https://upload.example/s2"})

    monkeypatch.setattr(yt.httpx, "post", fake_post)
    monkeypatch.setattr(yt.httpx, "put",
                        lambda *a, **k: FakeResp({"id": "v", "status": {}}))
    yt.adapter.upload(str(f), {"title": "t"})
    assert captured["json"]["status"]["privacyStatus"] == "unlisted"


def test_upload_validates_limits_before_network(client, monkeypatch, tmp_path):
    _connect_fake()
    monkeypatch.setattr(yt.httpx, "post", _no_network)
    monkeypatch.setattr(yt.httpx, "put", _no_network)
    f = tmp_path / "clip3.mp4"
    f.write_bytes(b"x")
    with pytest.raises(ValueError, match="100"):
        yt.adapter.upload(str(f), {"title": "T" * 101})
    with pytest.raises(ValueError, match="5000"):
        yt.adapter.upload(str(f), {"title": "ok", "description": "D" * 5001})
    with pytest.raises(ValueError):
        yt.adapter.upload(str(f), {"title": "   "})
    with pytest.raises(FileNotFoundError):
        yt.adapter.upload(str(tmp_path / "missing.mp4"), {"title": "ok"})


def test_status_mapping(client, monkeypatch):
    _connect_fake()

    def status_for(upload_status):
        monkeypatch.setattr(
            yt.httpx, "get",
            lambda *a, **k: FakeResp({"items": [{"status": {"uploadStatus": upload_status}}]}))
        return yt.adapter.status("some-id")["status"]

    assert status_for("processed") == "live"
    assert status_for("uploaded") == "processing"
    assert status_for("failed") == "failed"
    assert status_for("rejected") == "failed"
    monkeypatch.setattr(yt.httpx, "get", lambda *a, **k: FakeResp({"items": []}))
    assert yt.adapter.status("gone")["status"] == "failed"


def test_token_refresh_persists_new_access_token(client, monkeypatch):
    _connect_fake()
    _set_setting("yt_token_expires", "1")   # long expired → refresh required

    def fake_post(url, data=None, **kw):
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "fake-refresh"
        return FakeResp({"access_token": "fresh-tok", "expires_in": 3600})

    monkeypatch.setattr(yt.httpx, "post", fake_post)
    tok = yt.adapter._access_token()
    assert tok == "fresh-tok"
    raw = _get_setting_raw("yt_access_token")
    assert crypto.is_encrypted(raw) and crypto.dec(raw) == "fresh-tok"
    assert int(_get_setting_raw("yt_token_expires")) > time.time()


# ── media picker fix ─────────────────────────────────────────────────────────
def test_media_picker_offers_chains_and_prefers_audio_mux(client):
    _mk_video(video_path="/tmp/x/plain.mp4")
    _mk_video(video_path="/tmp/x/silent.mp4", audio_path="/tmp/x/withsound.mp4")
    _mk_chain(compiled_path="/tmp/x/chain_ok.mp4")
    _mk_chain(compiled_path="/tmp/x/chain_bad.mp4", nsfw=1)
    _mk_video(video_path="/tmp/x/nsfw_v.mp4", nsfw=1)

    media = client.get("/api/social/media", params={"kind": "video"}).json()["media"]
    paths = [m["local_path"] for m in media]
    assert "/tmp/x/withsound.mp4" in paths      # audio-muxed copy preferred
    assert "/tmp/x/silent.mp4" not in paths
    assert "/tmp/x/plain.mp4" in paths
    assert "/tmp/x/chain_ok.mp4" in paths       # compiled chains now offered
    assert "/tmp/x/chain_bad.mp4" not in paths  # nsfw chain filtered
    assert "/tmp/x/nsfw_v.mp4" not in paths
    chain_item = next(m for m in media if m["local_path"] == "/tmp/x/chain_ok.mp4")
    assert chain_item.get("chain_id")
    vid_item = next(m for m in media if m["local_path"] == "/tmp/x/withsound.mp4")
    assert vid_item.get("video_id")

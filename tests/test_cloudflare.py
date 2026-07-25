"""Cloudflare cache-purge integration (routers/cloudflare.py) — endpoint-level tests.

Locks down:
  • GET /api/cloudflare/status never leaks the token value, and reports
    configured=false when no cf_api_token is set.
  • POST /api/cloudflare/purge fails with a clear "not configured" message when no
    token is set, and — critically — never touches the network in that case (httpx
    is monkeypatched to explode if called, proving the early-return happens before
    any outbound request).
"""
import httpx
import pytest

import db
from routers import cloudflare as cf_router


def _reset(conn):
    for k in ("cf_api_token", "cf_account_id", "cf_zone_id", "cf_zone_name"):
        conn.execute("DELETE FROM settings WHERE key=?", (k,))
    conn.commit()


def _boom(*a, **k):
    raise AssertionError("httpx should not be called when Cloudflare isn't configured")


def test_status_not_configured_when_no_token(client, monkeypatch):
    conn = db.get_conn()
    try:
        _reset(conn)
    finally:
        conn.close()

    r = client.get("/api/cloudflare/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["zone"] == "example.com"   # default zone name
    assert body["zone_id_known"] is False


def test_status_configured_when_token_set_and_never_leaks_value(client, monkeypatch):
    secret_marker = "SUPER-SECRET-CF-TOKEN-DO-NOT-LEAK"
    conn = db.get_conn()
    try:
        _reset(conn)
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_api_token', ?)",
                     (secret_marker,))
        conn.commit()
    finally:
        conn.close()

    r = client.get("/api/cloudflare/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert secret_marker not in r.text


def test_purge_without_token_is_a_clear_error_and_never_calls_cloudflare(client, monkeypatch):
    monkeypatch.setattr(cf_router.httpx, "get", _boom)
    monkeypatch.setattr(cf_router.httpx, "post", _boom)
    conn = db.get_conn()
    try:
        _reset(conn)
    finally:
        conn.close()

    r = client.post("/api/cloudflare/purge")
    assert r.status_code == 400
    detail = r.json().get("detail", "")
    assert "not set" in detail.lower()
    assert "settings" in detail.lower()


def test_purge_resolves_and_caches_zone_id_then_purges(client, monkeypatch):
    conn = db.get_conn()
    try:
        _reset(conn)
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_api_token', 'test-token')")
        conn.commit()
    finally:
        conn.close()

    calls = {"zones": 0, "purge": 0}

    class _Resp:
        def __init__(self, payload):
            self._payload = payload
        def json(self):
            return self._payload

    def _fake_get(url, params=None, headers=None, timeout=None):
        calls["zones"] += 1
        assert params == {"name": "example.com"}
        assert headers.get("Authorization") == "Bearer test-token"
        return _Resp({"success": True, "result": [{"id": "zone-abc-123"}]})

    def _fake_post(url, headers=None, json=None, timeout=None):
        calls["purge"] += 1
        assert url.endswith("/zones/zone-abc-123/purge_cache")
        assert json == {"purge_everything": True}
        return _Resp({"success": True, "result": {"id": "zone-abc-123"}})

    monkeypatch.setattr(cf_router.httpx, "get", _fake_get)
    monkeypatch.setattr(cf_router.httpx, "post", _fake_post)

    r = client.post("/api/cloudflare/purge")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert calls["zones"] == 1
    assert calls["purge"] == 1

    # zone id should now be cached — a second purge must NOT re-resolve it.
    r2 = client.post("/api/cloudflare/purge")
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    assert calls["zones"] == 1   # unchanged — cached
    assert calls["purge"] == 2

    st = client.get("/api/cloudflare/status").json()
    assert st["zone_id_known"] is True

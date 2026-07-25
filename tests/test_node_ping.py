"""GET /api/node/ping — the cheap reachability probe used for global polling.

Unlike node_status() (full SSH session), this is a bare TCP connect to
gpu_host:22 with a short timeout, cached for a while. We monkeypatch the
socket connect so the test never touches the network, and we reset the
module-level cache between assertions since it's shared process-wide.
"""
from routers import node as node_router


def _reset_cache():
    node_router._ping_cache.update({"reachable": False, "checked_at": 0, "host": None})


def test_ping_reachable(client, monkeypatch):
    _reset_cache()
    monkeypatch.setattr(node_router, "GPU_HOST", "127.0.0.1")

    class _FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_connect(addr, timeout=None):
        assert addr == ("127.0.0.1", 22)
        return _FakeSock()

    monkeypatch.setattr(node_router._socket, "create_connection", _fake_connect)

    r = client.get("/api/node/ping")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is True
    assert body["host"] == "127.0.0.1"
    assert "checked_at" in body


def test_ping_unreachable_on_error(client, monkeypatch):
    _reset_cache()
    monkeypatch.setattr(node_router, "GPU_HOST", "127.0.0.1")

    def _fake_connect(addr, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(node_router._socket, "create_connection", _fake_connect)

    r = client.get("/api/node/ping")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False
    assert body["host"] == "127.0.0.1"


def test_ping_never_raises_on_unexpected_exception(client, monkeypatch):
    _reset_cache()
    monkeypatch.setattr(node_router, "GPU_HOST", "127.0.0.1")

    def _boom(addr, timeout=None):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(node_router._socket, "create_connection", _boom)

    r = client.get("/api/node/ping")
    assert r.status_code == 200
    assert r.json()["reachable"] is False


def test_ping_is_cached_within_ttl(client, monkeypatch):
    """Second call within the TTL window shouldn't re-probe — the cached value
    (and its checked_at) is returned as-is even if reachability flips."""
    _reset_cache()
    monkeypatch.setattr(node_router, "GPU_HOST", "127.0.0.1")

    calls = {"n": 0}

    def _fake_connect(addr, timeout=None):
        calls["n"] += 1
        class _S:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return _S()

    monkeypatch.setattr(node_router._socket, "create_connection", _fake_connect)

    r1 = client.get("/api/node/ping").json()
    r2 = client.get("/api/node/ping").json()
    assert calls["n"] == 1, "second poll within TTL must be served from cache"
    assert r1["checked_at"] == r2["checked_at"]

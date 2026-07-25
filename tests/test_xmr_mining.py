"""XMR (Monero) CPU mining gate — mirrors tests/test_pearl.py's hard mining gate:
start refused while the xmr_mining_enabled toggle is off, and agent/automation
callers are refused unless xmr_agent_access is on, even with mining enabled.
Closes TODO.md 'Left open' item #2 (XMR had no toggle / no agent gate, unlike
Pearl and JellyCoin, which each have both)."""
import pytest


def test_mining_status_reports_gate_defaults_off(client):
    r = client.get("/api/crypto/mining")
    assert r.status_code == 200
    d = r.json()
    assert d["mining_enabled"] is False
    assert d["agent_access"] is False


def test_start_is_gated_on_toggle(client):
    # toggle off (default) → start is REFUSED before xmrig is ever touched
    client.post("/api/crypto/mining/config", json={"mining_enabled": "0"})
    r = client.post("/api/crypto/mining/start")
    assert r.status_code == 403
    assert "toggle" in r.json()["detail"].lower()
    # bogus action → 400
    assert client.post("/api/crypto/mining/reboot").status_code == 400


def test_stop_is_not_gated_on_mining_toggle(client):
    # stop should always be reachable for a human, regardless of the master toggle,
    # mirroring pearl (only start is gated on the master toggle).
    client.post("/api/crypto/mining/config", json={"mining_enabled": "0"})
    r = client.post("/api/crypto/mining/stop")
    assert r.status_code == 200
    assert r.json()["running"] is False


def test_mining_toggle_roundtrips_via_config(client):
    r = client.post("/api/crypto/mining/config", json={"mining_enabled": "1"})
    assert r.status_code == 200 and r.json()["mining_enabled"] is True
    assert client.get("/api/crypto/mining").json()["mining_enabled"] is True
    client.post("/api/crypto/mining/config", json={"mining_enabled": "0"})
    assert client.get("/api/crypto/mining").json()["mining_enabled"] is False


def test_agent_access_defaults_off_and_toggles(client):
    from routers.crypto import mining as xmr_mining
    assert xmr_mining.agent_access_enabled() is False
    assert client.get("/api/crypto/mining").json()["agent_access"] is False
    r = client.post("/api/crypto/mining/config", json={"agent_access": "1"})
    assert r.status_code == 200 and r.json()["agent_access"] is True
    assert client.get("/api/crypto/mining").json()["agent_access"] is True
    client.post("/api/crypto/mining/config", json={"agent_access": "0"})
    assert xmr_mining.agent_access_enabled() is False


def test_agent_caller_is_gated_even_with_mining_on(client):
    """A non-human (agent) caller is refused unless xmr_agent_access is on —
    exercised at the module level since the human TestClient session is always
    'human'. With mining ON but agent-access OFF, an agent start is a 403,
    and it never reaches the xmrig-not-installed / no-wallet checks."""
    from routers.crypto import mining as xmr_mining
    client.post("/api/crypto/mining/config", json={"mining_enabled": "1", "agent_access": "0"})
    try:
        with pytest.raises(PermissionError):
            xmr_mining.mining_action("start", by_agent=True)
        # stop is also gated by agent_access (mirrors pearl: applies to both actions)
        with pytest.raises(PermissionError):
            xmr_mining.mining_action("stop", by_agent=True)
    finally:
        client.post("/api/crypto/mining/config", json={"mining_enabled": "0"})


def test_agent_allowed_once_agent_access_on(client):
    """Once xmr_agent_access is on, an agent stop call proceeds past the gate
    (stop is a no-op pkill so this is safe to actually run)."""
    from routers.crypto import mining as xmr_mining
    client.post("/api/crypto/mining/config", json={"agent_access": "1"})
    try:
        d = xmr_mining.mining_action("stop", by_agent=True)
        assert d == {"ok": True, "running": False}
    finally:
        client.post("/api/crypto/mining/config", json={"agent_access": "0"})


def test_xmr_settings_ride_the_key_backup():
    from routers import crypto as crypto_router
    assert "xmr_" in crypto_router._BACKUP_PREFIXES

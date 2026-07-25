"""GET /api/integrations/status (routers/integrations.py) — the Integrations Status
Board on Settings → Integrations.

Covers the core status logic (all keys set -> active, none -> not_setup, some ->
needs_login) plus the "never leak a secret value" contract: the response must
never contain the actual value of a key we set, only derived status/detail text.
"""
import db
from routers import integrations as integ_router
from routers import node as node_router


# Every settings key any descriptor might read, so each test starts from a clean
# slate regardless of test order (client fixture/DB are shared across the module).
_ALL_KEYS = [
    "etsy_key", "etsy_access_token", "etsy_shop_id", "etsy_token_expires",
    "printify_key", "printify_shop_id", "cults3d_api_key",
    "world_paypal_client_id", "world_paypal_secret", "square_access_token",
    "wp_url", "wp_consumer_key", "wp_consumer_secret",
    "kraken_api_key", "kraken_api_secret", "rh_username", "rh_password",
    "wallet_mnemonic", "pearl_rpc_pass",
    "yt_client_id", "yt_access_token", "tiktok_client_key",
    "meta_app_id", "meta_page_token",
    "hf_token", "lmstudio_api_key", "mail_user", "mail_pass",
    "cf_api_token", "cloudflare_api_token", "github_token",
]


def _reset(conn):
    conn.execute(f"DELETE FROM settings WHERE key IN ({','.join('?'*len(_ALL_KEYS))})", _ALL_KEYS)
    conn.commit()


def _set(conn, **kv):
    for k, v in kv.items():
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, v))
    conn.commit()


def _by_id(body, key_id):
    return next(it for it in body["integrations"] if it["key_id"] == key_id)


def _fake_no_github_no_node(monkeypatch):
    """Keep the two live-ish checks (gh CLI, node ping) deterministic and off the
    network for every test in this file."""
    class _FakeProc:
        returncode = 1
        stdout = ""
        stderr = "not authenticated"
    monkeypatch.setattr(integ_router.subprocess, "run", lambda *a, **k: _FakeProc())

    # Deterministic, network-free node ping — same monkeypatch approach as
    # tests/test_node_ping.py (reset the module-level cache, fake the TCP connect).
    node_router._ping_cache.update({"reachable": False, "checked_at": 0, "host": None})
    monkeypatch.setattr(node_router, "GPU_HOST", "127.0.0.1")

    def _fake_connect(addr, timeout=None):
        raise OSError("connection refused")
    monkeypatch.setattr(node_router._socket, "create_connection", _fake_connect)


def test_all_required_keys_set_is_active(client, monkeypatch):
    _fake_no_github_no_node(monkeypatch)
    conn = db.get_conn()
    try:
        _reset(conn)
        _set(conn, printify_key="pk_live_abc123", printify_shop_id="12345")
    finally:
        conn.close()

    r = client.get("/api/integrations/status")
    assert r.status_code == 200
    body = r.json()
    printify = _by_id(body, "printify")
    assert printify["status"] == "active"
    assert printify["in_app"] is True
    assert printify["setup_url"] == "https://printify.com/app/account/api"


def test_no_keys_set_is_not_setup(client, monkeypatch):
    _fake_no_github_no_node(monkeypatch)
    conn = db.get_conn()
    try:
        _reset(conn)
    finally:
        conn.close()

    r = client.get("/api/integrations/status")
    assert r.status_code == 200
    body = r.json()
    printify = _by_id(body, "printify")
    assert printify["status"] == "not_setup"
    kraken = _by_id(body, "kraken")
    assert kraken["status"] == "not_setup"
    assert kraken["setup_url"] == "https://www.kraken.com/u/security/api"


def test_partial_keys_set_is_needs_login(client, monkeypatch):
    _fake_no_github_no_node(monkeypatch)
    conn = db.get_conn()
    try:
        _reset(conn)
        _set(conn, kraken_api_key="ka_abc123")   # secret left unset -> partial
    finally:
        conn.close()

    r = client.get("/api/integrations/status")
    assert r.status_code == 200
    kraken = _by_id(r.json(), "kraken")
    assert kraken["status"] == "needs_login"


def test_etsy_expired_token_is_needs_login_even_with_all_keys_set(client, monkeypatch):
    _fake_no_github_no_node(monkeypatch)
    conn = db.get_conn()
    try:
        _reset(conn)
        _set(conn, etsy_key="k", etsy_access_token="t", etsy_shop_id="s",
             etsy_token_expires="1")   # far in the past -> expired
    finally:
        conn.close()

    r = client.get("/api/integrations/status")
    etsy = _by_id(r.json(), "etsy")
    assert etsy["status"] == "needs_login"
    assert "expired" in etsy["detail"].lower() or "reconnect" in etsy["detail"].lower()


def test_response_never_contains_secret_values(client, monkeypatch):
    _fake_no_github_no_node(monkeypatch)
    secret_marker = "SUPER-SECRET-VALUE-DO-NOT-LEAK-9f8a7"
    conn = db.get_conn()
    try:
        _reset(conn)
        _set(conn, printify_key=secret_marker, kraken_api_secret=secret_marker,
             wallet_mnemonic=secret_marker)
    finally:
        conn.close()

    r = client.get("/api/integrations/status")
    assert r.status_code == 200
    assert secret_marker not in r.text


def test_shape_has_categories_and_summary(client, monkeypatch):
    _fake_no_github_no_node(monkeypatch)
    conn = db.get_conn()
    try:
        _reset(conn)
    finally:
        conn.close()

    r = client.get("/api/integrations/status")
    body = r.json()
    assert "integrations" in body and "summary" in body and "checked_at" in body
    categories = {it["category"] for it in body["integrations"]}
    assert {"Commerce", "Crypto & Markets", "Social", "Infra & AI"} <= categories
    key_ids = {it["key_id"] for it in body["integrations"]}
    for expected in ("etsy", "printify", "cults3d", "paypal", "square", "wp",
                      "kraken", "robinhood", "wallets", "pearl",
                      "youtube", "tiktok", "meta",
                      "huggingface", "lmstudio", "mail", "cloudflare", "github", "node_gpu"):
        assert expected in key_ids, f"missing integration: {expected}"
    total = sum(body["summary"].values())
    assert total == len(body["integrations"])


def test_never_raises_even_if_a_check_blows_up(client, monkeypatch):
    """subprocess.run raising should degrade the GitHub row, not 500 the endpoint."""
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(integ_router.subprocess, "run", _boom)

    r = client.get("/api/integrations/status")
    assert r.status_code == 200
    github = _by_id(r.json(), "github")
    assert github["status"] in ("active", "needs_login", "not_setup")

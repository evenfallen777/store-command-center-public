"""Donate rail (routers/donate.py) — GET /api/donate/config.

A donate LINK moves no money and needs no world_ops prayer/gate (unlike Cash App),
so this is just settings-backed config: toggle-controlled and default OFF, so a
fresh public clone never ships someone else's Buy Me a Coffee handle.
"""
import db


def _reset(conn):
    conn.execute("DELETE FROM settings WHERE key IN ('donate_enabled','donate_bmc_user')")
    conn.commit()


def test_defaults_off_and_blank(client):
    conn = db.get_conn()
    try:
        _reset(conn)
    finally:
        conn.close()
    r = client.get("/api/donate/config")
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "url": ""}


def test_enabled_without_handle_stays_hidden(client):
    conn = db.get_conn()
    try:
        _reset(conn)
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('donate_enabled','1')")
        conn.commit()
    finally:
        conn.close()
    r = client.get("/api/donate/config")
    assert r.json() == {"enabled": True, "url": ""}


def test_handle_without_enabled_stays_hidden(client):
    conn = db.get_conn()
    try:
        _reset(conn)
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('donate_bmc_user','acme')")
        conn.commit()
    finally:
        conn.close()
    r = client.get("/api/donate/config")
    assert r.json() == {"enabled": False, "url": ""}


def test_enabled_and_handle_builds_the_bmc_url(client):
    conn = db.get_conn()
    try:
        _reset(conn)
    finally:
        conn.close()
    r = client.patch("/api/settings", json={"donate_bmc_user": "acme", "donate_enabled": "1"})
    assert r.status_code == 200
    r = client.get("/api/donate/config")
    assert r.json() == {"enabled": True, "url": "https://buymeacoffee.com/acme"}


def test_config_is_readable_pre_auth():
    """The login page (pre-auth) decides whether to show the ☕ Support button
    from this endpoint (main.py auth-guard carve-out), so it must answer with
    NO session — and expose nothing but the public {enabled, url} pair."""
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app, base_url="https://testserver") as anon:
        r = anon.get("/api/donate/config")
        assert r.status_code == 200
        assert set(r.json().keys()) == {"enabled", "url"}


def test_login_page_shows_button_only_when_enabled(client):
    """The sign-in page renders the ☕ link only when donate_enabled + a handle
    are set (routers/auth.py _login_html), and hides it otherwise."""
    from fastapi.testclient import TestClient
    import main
    conn = db.get_conn()
    try:
        _reset(conn)
    finally:
        conn.close()
    with TestClient(main.app, base_url="https://testserver") as anon:
        assert "Support this project" not in anon.get("/login").text
    r = client.patch("/api/settings", json={"donate_bmc_user": "acme", "donate_enabled": "1"})
    assert r.status_code == 200
    with TestClient(main.app, base_url="https://testserver") as anon:
        html = anon.get("/login").text
        assert 'https://buymeacoffee.com/acme' in html
        assert 'rel="noopener"' in html
    # leave the throwaway DB back at defaults for whatever runs next
    conn = db.get_conn()
    try:
        _reset(conn)
    finally:
        conn.close()


def test_settings_get_exposes_defaults(client):
    """A fresh clone's GET /api/settings must show the toggle OFF and the handle
    blank — never omit the keys (the frontend checkbox would render unchecked
    either way, but the field must exist so the UI has something to bind to)."""
    conn = db.get_conn()
    try:
        _reset(conn)
    finally:
        conn.close()
    r = client.get("/api/settings")
    body = r.json()
    assert body["donate_enabled"] == "0"
    assert body["donate_bmc_user"] == ""

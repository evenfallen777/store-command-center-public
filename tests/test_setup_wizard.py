"""First-run setup wizard — gating (needs_setup) and the topology write path.

The wizard itself (static/js/setup-wizard.js) is pure client-side glue over existing
endpoints; what this task actually adds server-side is:
  1. GET "/" routes to /setup while the install is on the default password and
     setup_complete isn't "1" (auth_core.needs_setup(), wired into routers/auth.py's
     dashboard() route) — and stops once setup_complete=1 is written.
  2. GET /setup itself, always reachable regardless of setup state.
  3. The 1-PC topology write path the wizard's step 2 calls: POST /api/settings/nodes
     with gpu_host=127.0.0.1 (the same mechanism app/retail_scrub.py's public
     no-GPU default uses) persists to .env.

Each test resets exactly the state it needs at the top (mirroring
tests/test_first_login.py's _clear_auth), since `client` is a session-scoped fixture
shared across the whole suite.
"""
from deps import get_conn


def _reset_auth_and_setup(client):
    """Put auth/setup settings back to a genuine fresh-install state: default
    password, no setup_complete flag."""
    conn = get_conn()
    conn.execute("DELETE FROM settings WHERE key IN "
                 "('_auth_password_hash','_auth_default_pw','setup_complete')")
    conn.commit()
    conn.close()


def test_wizard_shown_on_fresh_default_password_install(client):
    _reset_auth_and_setup(client)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/setup")


def test_setup_page_reachable_and_serves_the_wizard_script(client):
    _reset_auth_and_setup(client)
    r = client.get("/setup")
    assert r.status_code == 200
    assert "setup-wizard.js" in r.text


def test_wizard_not_shown_once_setup_complete_is_set(client):
    _reset_auth_and_setup(client)
    # Still on the default password, but the wizard has been finished (or skipped) —
    # setup_complete=1 must be sticky even though is_default_password() is still true.
    r = client.patch("/api/settings", json={"setup_complete": "1"})
    assert r.status_code == 200
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200  # dashboard served directly, no redirect to /setup


def test_setup_route_never_hidden_even_after_completion(client):
    # /setup itself must always stay reachable, even once setup_complete=1 — a
    # completed/skipped install can still revisit it on purpose.
    client.patch("/api/settings", json={"setup_complete": "1"})
    r = client.get("/setup")
    assert r.status_code == 200


def test_topology_1pc_sets_node_host_to_loopback(client, monkeypatch, tmp_path):
    """What the wizard's topology step calls for '1-PC': POST /api/settings/nodes
    with gpu_host=127.0.0.1. Redirect ENV_PATH to a throwaway file so the test never
    writes to the real repo's app/.env."""
    from routers import settings as settings_router
    fake_env = tmp_path / ".env"
    monkeypatch.setattr(settings_router, "ENV_PATH", fake_env)

    r = client.post("/api/settings/nodes", json={"gpu_host": "127.0.0.1"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "STORE_GPU_HOST" in body["written"]

    assert fake_env.exists()
    assert "STORE_GPU_HOST=127.0.0.1" in fake_env.read_text()

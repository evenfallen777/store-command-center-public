"""🎮 Games tab — engine detection, project discovery, assets, and graceful degradation.

Every test MOCKS the ssh helper (games._ssh). Nothing here requires a live GPU node:
the point of this tab is that it stays useful when the node is unreachable, so the
"node is down" path is tested as hard as the happy path.
"""
import pytest

import cache


@pytest.fixture(autouse=True)
def _clean_cache():
    """Engine/project reads are TTL-cached — drop the cache so each test's mocked ssh
    is actually consulted."""
    cache.invalidate("games:engines")
    cache.invalidate("games:projects")
    yield
    cache.invalidate("games:engines")
    cache.invalidate("games:projects")


@pytest.fixture
def games():
    import routers.games as g
    return g


def _ssh_returning(script_out, rc=0):
    return lambda cmd, timeout=30: (rc, script_out)


# ─── engine detection ────────────────────────────────────────────────────────

GODOT_ONLY = (
    "godot|/home/u/engines/godot/Godot_v4.7.1-stable_linux.x86_64|4.7.1.stable.official.a13da4feb\n"
    "unity||\n"
    "unreal||\n"
    "disk|121|\n"
)


def test_engines_parses_installed_godot_and_missing_others(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning(GODOT_ONLY))
    r = client.get("/api/games/engines")
    assert r.status_code == 200
    d = r.json()
    by = {e["key"]: e for e in d["engines"]}
    assert set(by) == {"godot", "unity", "unreal"}

    assert by["godot"]["installed"] is True
    assert by["godot"]["version"].startswith("4.7.1")
    assert by["godot"]["path"].endswith("Godot_v4.7.1-stable_linux.x86_64")
    assert by["godot"]["can_build"] is True
    assert by["godot"]["install_hint"] == ""      # installed => no hint

    for k in ("unity", "unreal"):
        assert by[k]["installed"] is False
        assert by[k]["path"] == ""
        assert by[k]["can_build"] is False
        # "not installed" is a first-class state: it MUST carry a usable hint.
        assert len(by[k]["install_hint"]) > 40
    assert "hub.unity3d.com" in by["unity"]["install_hint"]
    assert "Epic" in by["unreal"]["install_hint"]

    assert d["disk_free_gb"] == 121
    assert d["reachable"] is True


def test_engines_detects_all_three_when_present(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning(
        "godot|/x/godot|4.7.1.stable\n"
        "unity|/home/u/Unity/Hub/Editor/2022.3.10f1/Editor/Unity|2022.3.10f1\n"
        "unreal|/home/u/UnrealEngine/Engine/Binaries/Linux/UnrealEditor|5.4\n"
        "disk|900|\n"))
    by = {e["key"]: e for e in client.get("/api/games/engines").json()["engines"]}
    assert all(by[k]["installed"] for k in ("godot", "unity", "unreal"))
    assert by["unity"]["version"] == "2022.3.10f1"
    assert by["unreal"]["version"] == "5.4"


def test_engines_node_unreachable_is_graceful(client, games, monkeypatch):
    """Node down: 200 with reachable=False and install hints intact — never a 5xx."""
    monkeypatch.setattr(games, "_ssh", _ssh_returning("ssh: connect to host port 22: No route", rc=255))
    r = client.get("/api/games/engines")
    assert r.status_code == 200
    d = r.json()
    assert d["reachable"] is False
    assert d["error"]
    by = {e["key"]: e for e in d["engines"]}
    assert not any(e["installed"] for e in d["engines"])
    assert by["godot"]["install_hint"]           # still tells the user what to do


def test_engines_result_is_cached(client, games, monkeypatch):
    calls = []

    def _spy(cmd, timeout=30):
        calls.append(cmd)
        return 0, GODOT_ONLY

    monkeypatch.setattr(games, "_ssh", _spy)
    client.get("/api/games/engines")
    client.get("/api/games/engines")
    assert len(calls) == 1, "engine detection must be TTL-cached (one ssh round-trip)"
    client.get("/api/games/engines?refresh=1")
    assert len(calls) == 2, "refresh=1 must bust the cache"


# ─── project discovery ───────────────────────────────────────────────────────

FIND_OUT = (
    "/home/u/games/spacegame/project.godot|1750000000.0\n"
    "/home/u/games/shooter/Shooter.uproject|1740000000.0\n"
    "/home/u/games/rpg/ProjectSettings/ProjectVersion.txt|1760000000.0\n"
    "/home/u/games/junk/ProjectVersion.txt|1730000000.0\n"     # not under ProjectSettings/
)


def test_projects_discovery_maps_markers_to_engines(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning(FIND_OUT))
    d = client.get("/api/games/projects").json()
    assert d["reachable"] is True and d["root_exists"] is True
    by = {p["name"]: p for p in d["projects"]}
    assert by["spacegame"]["engine"] == "godot"
    assert by["spacegame"]["path"] == "/home/u/games/spacegame"
    assert by["shooter"]["engine"] == "unreal"
    assert by["rpg"]["engine"] == "unity"
    assert by["rpg"]["path"] == "/home/u/games/rpg"   # up two from ProjectSettings/
    assert "junk" not in by, "a stray ProjectVersion.txt is not a Unity project"
    # newest first
    assert [p["name"] for p in d["projects"]] == ["rpg", "spacegame", "shooter"]


def test_projects_missing_root_is_empty_state_not_error(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning("NOROOT\n"))
    d = client.get("/api/games/projects").json()
    assert d["projects"] == []
    assert d["root_exists"] is False
    assert d["reachable"] is True
    assert d["error"] is None


def test_projects_node_unreachable_is_graceful(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning("no route to host", rc=255))
    r = client.get("/api/games/projects")
    assert r.status_code == 200
    d = r.json()
    assert d["projects"] == [] and d["reachable"] is False and d["error"]


# ─── project creation ────────────────────────────────────────────────────────

def test_create_project_writes_godot_files(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning(GODOT_ONLY))   # godot present
    sent = {}

    def _fake(cmd, timeout=30):
        if "project.godot" in cmd and "base64 -d" in cmd:
            sent["cmd"] = cmd
            return 0, "OK\n/home/u/games/My_Game\n"
        return 0, GODOT_ONLY

    monkeypatch.setattr(games, "_ssh", _fake)
    r = client.post("/api/games/projects", json={"engine": "godot", "name": "My Game"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] and d["engine"] == "godot"
    assert d["path"].endswith("/My_Game")
    assert "mkdir -p" in sent["cmd"] and "main.tscn" in sent["cmd"]


def test_create_project_rejects_non_godot_with_install_hint(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning(GODOT_ONLY))
    r = client.post("/api/games/projects", json={"engine": "unity", "name": "Thing"})
    assert r.status_code == 400
    d = r.json()
    assert "not installed" in d["error"].lower()
    assert d["install_hint"]


def test_create_project_rejects_bad_name(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning(GODOT_ONLY))
    r = client.post("/api/games/projects", json={"engine": "godot", "name": "../../etc; rm -rf"})
    assert r.status_code == 400


def test_create_project_blocked_when_godot_missing(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning("godot||\nunity||\nunreal||\ndisk|10|\n"))
    r = client.post("/api/games/projects", json={"engine": "godot", "name": "Nope"})
    assert r.status_code == 400
    assert r.json()["install_hint"]


# ─── builds ──────────────────────────────────────────────────────────────────

def test_build_rejects_when_godot_missing(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning("godot||\nunity||\nunreal||\ndisk|10|\n"))
    r = client.post("/api/games/build", json={"path": "~/games/x"})
    assert r.status_code == 400
    assert "not installed" in r.json()["error"].lower()


def test_build_queues_a_task_and_returns_immediately(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning(GODOT_ONLY))
    submitted = {}

    def _fake_submit(func, desc, **kw):
        submitted["desc"] = desc
        submitted["kw"] = kw
        return 4242

    monkeypatch.setattr(games.orch, "submit_llm", _fake_submit)
    r = client.post("/api/games/build", json={"path": "~/games/spacegame"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["task_id"] == 4242
    assert "Godot build" in submitted["desc"]
    # No model / no prompt-registry task key => the orchestrator never loads an LLM.
    assert submitted["kw"].get("source") == "games"
    assert "model" not in submitted["kw"] and "task" not in submitted["kw"]
    # And the status endpoint knows about it right away.
    s = client.get("/api/games/build/4242").json()
    assert s["task_id"] == 4242 and s["status"] == "queued"


def test_build_rejects_bad_preset(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning(GODOT_ONLY))
    r = client.post("/api/games/build", json={"path": "~/games/x", "preset": 'a"; rm -rf /'})
    assert r.status_code == 400


def test_build_status_unknown_task_is_not_an_error(client):
    r = client.get("/api/games/build/999999")
    assert r.status_code == 200
    assert "status" in r.json()


# ─── assets ──────────────────────────────────────────────────────────────────

def test_assets_lists_sprites_models_and_packs(client, games, monkeypatch):
    monkeypatch.setattr(games, "_entity_assets", lambda: [
        {"kind": "sprite", "id": "hero:idle", "name": "hero — idle",
         "entity": "hero", "action": "idle",
         "url": "/store/static/world_assets/entities/hero/idle.png",
         "frames": 4, "fw": 32, "fh": 32, "source": "generated"}])
    monkeypatch.setattr(games, "_model_assets", lambda: [
        {"kind": "model3d", "id": "generated/thing.stl", "name": "thing.stl",
         "folder": "generated", "size_kb": 12.5}])
    monkeypatch.setattr(games, "_pack_assets", lambda: ([{"kind": "pack", "id": "p", "name": "p"}], 16))
    d = client.get("/api/games/assets").json()
    assert d["counts"] == {"sprites": 1, "models": 1, "packs": 1}
    assert d["sprites"][0]["frames"] == 4
    assert d["tile_size"] == 16


def test_assets_works_with_no_node(client):
    """Assets are read from local disk — they must list even with the node down."""
    r = client.get("/api/games/assets")
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d["packs"], list) and isinstance(d["sprites"], list)


def test_asset_export_requires_project_and_selection(client):
    assert client.post("/api/games/assets/export", json={}).status_code == 400
    assert client.post("/api/games/assets/export",
                       json={"project": "~/games/x", "assets": []}).status_code == 400


def test_asset_export_reports_unreachable_node(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning("no route", rc=255))
    r = client.post("/api/games/assets/export",
                    json={"project": "~/games/x", "assets": [{"kind": "sprite", "id": "a"}]})
    assert r.status_code == 502
    assert "unreachable" in r.json()["error"].lower()


def test_asset_export_copies_sprite_with_sidecar(client, games, monkeypatch, tmp_path):
    png = games.BASE / "static" / "world_assets" / "entities" / "_test_ent" / "idle.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    puts, shells = [], []
    monkeypatch.setattr(games, "_ssh", lambda cmd, timeout=30: (shells.append(cmd), (0, "/home/u"))[1])
    monkeypatch.setattr(games, "_node_put", lambda local, remote, timeout=180: (puts.append(remote), (True, ""))[1])
    try:
        r = client.post("/api/games/assets/export", json={
            "project": "~/games/x",
            "assets": [{"kind": "sprite", "id": "_test_ent:idle", "entity": "_test_ent",
                        "action": "idle", "frames": 4, "fw": 32, "fh": 32,
                        "url": "/store/static/world_assets/entities/_test_ent/idle.png"}]})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] and d["exported"] == ["_test_ent_idle.png"]
        assert puts and puts[0].endswith("/assets/_test_ent_idle.png")
        # sidecar json describing the frames went over too
        assert any("_test_ent_idle.json" in c for c in shells)
    finally:
        png.unlink(missing_ok=True)
        try:
            png.parent.rmdir()
        except OSError:
            pass


def test_asset_export_skips_unknown_assets_without_failing(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", lambda cmd, timeout=30: (0, "/home/u"))
    r = client.post("/api/games/assets/export", json={
        "project": "~/games/x", "assets": [{"kind": "sprite", "id": "ghost", "url": "/nope.png"}]})
    assert r.status_code == 200
    d = r.json()
    assert d["exported"] == [] and d["skipped"]


def test_asset_export_rejects_path_traversal(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", lambda cmd, timeout=30: (0, "/home/u"))
    r = client.post("/api/games/assets/export", json={
        "project": "~/games/x",
        "assets": [{"kind": "model3d", "id": "../../../../etc/passwd"}]})
    assert r.json()["exported"] == []


# ─── MCP + notes ─────────────────────────────────────────────────────────────

def test_mcp_pane_is_informational_only(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning(GODOT_ONLY))
    d = client.get("/api/games/mcp").json()
    keys = {o["key"] for o in d["options"]}
    assert {"godot-mcp", "unity-mcp", "unreal-mcp"} <= keys
    for o in d["options"]:
        assert o["docs"].startswith("http")
        assert o["status"]
    # It reflects engine state but installs nothing.
    by = {o["key"]: o for o in d["options"]}
    assert by["godot-mcp"]["engine_installed"] is True
    assert by["unity-mcp"]["engine_installed"] is False


def test_notes_roundtrip_and_project_root(client):
    assert client.post("/api/games/notes",
                       json={"notes": "remember the export preset"}).status_code == 200
    d = client.get("/api/games/notes").json()
    assert d["notes"] == "remember the export preset"
    assert d["docs"] and all(x["url"].startswith("http") for x in d["docs"])

    assert client.post("/api/games/notes", json={"project_root": "~/gamedev"}).status_code == 200
    assert client.get("/api/games/notes").json()["project_root"] == "~/gamedev"
    # restore the default so project-discovery tests aren't order-dependent
    client.post("/api/games/notes", json={"project_root": "~/games"})


def test_notes_rejects_a_shell_y_project_root(client):
    r = client.post("/api/games/notes", json={"project_root": "~/games; rm -rf /"})
    assert r.status_code == 400


# ─── editor MCP: model setting + queue-routed bridge calls ───────────────────

def test_mcp_settings_roundtrip_and_rejects_bad_input(client):
    r = client.post("/api/games/mcp/settings",
                    json={"model": "google/gemma-3-4b-qat",
                          "project": "/mnt/projects/unreal/Sandbox"})
    assert r.status_code == 200
    d = r.json()
    assert d["model"] == "google/gemma-3-4b-qat"
    assert d["project"] == "/mnt/projects/unreal/Sandbox"

    seen = client.get("/api/games/mcp").json()
    assert seen["mcp_model"] == "google/gemma-3-4b-qat"

    assert client.post("/api/games/mcp/settings",
                       json={"model": "bad; rm -rf /"}).status_code == 400
    assert client.post("/api/games/mcp/settings",
                       json={"project": "/tmp/x; reboot"}).status_code == 400


def test_mcp_bridge_reports_missing_port_file_as_state_not_error(client, monkeypatch):
    """No port.json = the editor simply has not started the bridge. That is a
    first-class state, never an exception."""
    from routers import games
    monkeypatch.setattr(games, "_ssh", _ssh_returning("NOPORTFILE\n"))
    client.post("/api/games/mcp/settings", json={"project": "/mnt/projects/unreal/Sandbox"})
    d = client.get("/api/games/mcp/bridge").json()
    assert d["running"] is False and d["port"] == 0
    assert "bridge" in (d["error"] or "").lower()


def test_mcp_bridge_reads_the_published_port(client, monkeypatch):
    """The bridge port is derived per-worktree — we must read port.json, not
    assume the 9877 the Node client defaults to."""
    from routers import games
    monkeypatch.setattr(games, "_ssh", _ssh_returning(
        '{"port": 62775, "pid": 3073501, "apiVersion": 1}\n---\n1\n'))
    client.post("/api/games/mcp/settings", json={"project": "/mnt/projects/unreal/Sandbox"})
    d = client.get("/api/games/mcp/bridge").json()
    assert d["port"] == 62775 and d["running"] is True


def test_mcp_call_queues_and_loads_no_model_without_use_llm(client, monkeypatch):
    """A bare editor command must ride the queue but load NO model — that is what
    keeps co-op viable while the editor holds the VRAM."""
    from routers import games
    from orchestrator import orch

    submitted = {}
    real_submit = orch.submit_llm

    def _spy(func, desc, **kw):
        submitted.update(desc=desc, **kw)
        return real_submit(func, desc, **kw)

    monkeypatch.setattr(orch, "submit_llm", _spy)
    monkeypatch.setattr(games, "_ssh", _ssh_returning(
        '{"port": 62775}\n---\n1\n'))
    client.post("/api/games/mcp/settings",
                json={"project": "/mnt/projects/unreal/Sandbox",
                      "model": "google/gemma-3-4b-qat"})

    r = client.post("/api/games/mcp/call", json={"method": "get_current_level"})
    assert r.status_code == 200
    assert r.json()["queued"] is True
    assert submitted["model"] is None, "no LLM for a plain editor command"
    assert submitted["source"] == "games_mcp"

    r = client.post("/api/games/mcp/call",
                    json={"method": "get_current_level", "use_llm": True})
    assert r.status_code == 200
    assert submitted["model"] == "google/gemma-3-4b-qat"

    # These submit REAL tasks to the shared orchestrator. Left queued, they make
    # _store_busy() read True and break gpu_guard's tests downstream.
    orch.clear_pending()


def test_mcp_call_rejects_bad_method_and_missing_project(client):
    client.post("/api/games/mcp/settings", json={"project": ""})
    assert client.post("/api/games/mcp/call",
                       json={"method": "rm -rf /"}).status_code == 400
    assert client.post("/api/games/mcp/call",
                       json={"method": "get_current_level"}).status_code == 400


def test_unity_mcp_install_validates_and_is_reversible(client, monkeypatch):
    """Installing Unity MCP rewrites a real project's manifest, so the endpoint
    must validate hard and never fire implicitly."""
    from routers import games

    assert client.post("/api/games/mcp/unity",
                       json={"project": "/tmp/x; reboot"}).status_code == 400
    assert client.post("/api/games/mcp/unity",
                       json={"project": "/mnt/projects/Unity/Projects/X",
                             "action": "sudo"}).status_code == 400
    assert client.post("/api/games/mcp/unity", json={}).status_code == 400

    monkeypatch.setattr(games, "_ssh", _ssh_returning(
        '{"ok": true, "changed": true, "backup": "/p/Packages/manifest.json.bak-1", '
        '"manifest": "/p/Packages/manifest.json", "action": "install"}'))
    r = client.post("/api/games/mcp/unity",
                    json={"project": "/mnt/projects/Unity/Projects/Chao World"})
    assert r.status_code == 200
    d = r.json()
    assert d["changed"] is True and d["package"] == games.UNITY_MCP_PKG
    assert "backup" in d and "Unity" in d["next"]

    monkeypatch.setattr(games, "_ssh", _ssh_returning(
        '{"ok": false, "error": "no Packages/manifest.json - not a Unity project"}'))
    r = client.post("/api/games/mcp/unity",
                    json={"project": "/mnt/projects/unreal/Sandbox"})
    assert r.status_code == 400
    assert "manifest" in r.json()["error"]


def test_engines_report_every_installed_version(client, games, monkeypatch):
    """Unity is routinely kept multi-version on purpose (a 2022.3 project
    force-upgrades if opened in Unity 6), so reporting only the newest is wrong."""
    monkeypatch.setattr(games, "_ssh", _ssh_returning(
        "godot|/x/godot|4.7.1.stable\n"
        "unity|/mnt/p/Unity/Hub/Editor/6000.5.5f1/Editor/Unity|6000.5.5f1"
        "|2022.3.62f3,6000.0.80f1,6000.5.5f1\n"
        "unreal|/home/u/.local/bin/UnrealEditor|5.8.0\n"
        "disk|56|\n"))
    by = {e["key"]: e for e in client.get("/api/games/engines").json()["engines"]}
    assert by["unity"]["version"] == "6000.5.5f1", "newest stays the headline version"
    assert by["unity"]["versions"] == ["2022.3.62f3", "6000.0.80f1", "6000.5.5f1"]
    # engines with a single install still get a usable list, not an empty one
    assert by["unreal"]["versions"] == ["5.8.0"]
    assert by["godot"]["versions"] == ["4.7.1.stable"]


def test_engines_versions_absent_when_not_installed(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning(GODOT_ONLY))
    by = {e["key"]: e for e in client.get("/api/games/engines").json()["engines"]}
    assert by["unity"]["versions"] == []
    assert by["godot"]["versions"] == ["4.7.1.stable.official.a13da4feb"]


def test_projects_scan_supports_multiple_roots(client, games, monkeypatch):
    """Projects legitimately span drives: Unreal must sit on ext4 (ntfs-3g deadlocks
    on its small-file I/O) while a 21-project Unity library is too big to move."""
    seen = {}

    def _spy(cmd, timeout=30):
        seen["cmd"] = cmd
        return 0, ("/mnt/space/unreal/Sandbox/Sandbox.uproject|1770000000.0\n"
                   "/mnt/projects/Unity/Projects/Chao World/ProjectSettings/ProjectVersion.txt|1760000000.0\n")

    monkeypatch.setattr(games, "_ssh", _spy)
    client.post("/api/games/notes", json={"project_root": "/mnt/projects,/mnt/space"})
    d = client.get("/api/games/projects?refresh=1").json()
    by = {p["name"]: p for p in d["projects"]}
    assert by["Sandbox"]["engine"] == "unreal"
    assert by["Sandbox"]["path"].startswith("/mnt/space")
    assert by["Chao World"]["engine"] == "unity"
    assert "/mnt/space" in seen["cmd"] and "/mnt/projects" in seen["cmd"]
    assert games._project_roots() == ["/mnt/projects", "/mnt/space"]


def test_projects_all_roots_missing_is_empty_state(client, games, monkeypatch):
    monkeypatch.setattr(games, "_ssh", _ssh_returning("NOROOT\n"))
    client.post("/api/games/notes", json={"project_root": "/nope/one,/nope/two"})
    d = client.get("/api/games/projects?refresh=1").json()
    assert d["projects"] == [] and d["root_exists"] is False and d["error"] is None


def test_project_root_rejects_shell_metacharacters(client):
    assert client.post("/api/games/notes",
                       json={"project_root": "/mnt/a; rm -rf /"}).status_code == 400

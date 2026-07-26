"""gpu_guard router — the node's busy/free heartbeat pauses and resumes the queue.

TestClient requests arrive with host "testclient", which _check_miner treats like
localhost, so no X-Jelly-Token is needed here.
"""


def test_busy_pauses_and_free_resumes(client):
    from orchestrator import orch
    from routers import gpu_guard

    orch.resume()
    r = client.post("/api/gpu/guard/state", json={"busy": True, "apps": ["steam_app_620"]})
    assert r.status_code == 200
    assert r.json()["paused"] is True
    assert orch.is_paused()

    st = client.get("/api/gpu/guard/state").json()
    assert st["busy"] is True and st["apps"] == ["steam_app_620"]

    r = client.post("/api/gpu/guard/state", json={"busy": False})
    assert r.status_code == 200
    assert not orch.is_paused()
    assert gpu_guard.guard_info()["busy"] is False


def test_manual_pause_not_clobbered_by_free_heartbeat(client):
    from orchestrator import orch

    orch.pause()   # manual Dashboard pause — guard did NOT set this
    r = client.post("/api/gpu/guard/state", json={"busy": False})
    assert r.status_code == 200
    assert orch.is_paused(), "a free heartbeat must not undo a manual pause"
    orch.resume()


def test_interrupted_generation_requeued_on_resume(client, monkeypatch):
    """A generation mid-flight at pause time that lands 'failed' (killed by the
    node's ComfyUI /interrupt) is flipped back to 'queued' and re-launched when
    the guard resumes; one that finished 'done' is left alone."""
    import services
    from db import get_conn
    from orchestrator import orch

    redone = []
    monkeypatch.setattr(services, "run_generation", lambda gid: redone.append(gid))

    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO generations (prompt, status) VALUES ('interrupted one', 'generating')")
    gid_killed = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO generations (prompt, status) VALUES ('finished one', 'generating')")
    gid_done = cur.lastrowid
    conn.commit()

    orch.resume()
    client.post("/api/gpu/guard/state", json={"busy": True, "apps": ["steam_app_620"]})

    # what the interrupt does to one, and a natural finish for the other
    conn.execute("UPDATE generations SET status='failed' WHERE id=?", (gid_killed,))
    conn.execute("UPDATE generations SET status='done' WHERE id=?", (gid_done,))
    conn.commit()

    client.post("/api/gpu/guard/state", json={"busy": False})
    import time as _t
    _t.sleep(0.3)   # rerun threads are daemons; give them a beat

    st = conn.execute("SELECT status FROM generations WHERE id=?", (gid_killed,)).fetchone()["status"]
    assert st == "queued"
    assert redone == [gid_killed], "only the killed job is re-launched"
    assert conn.execute("SELECT status FROM generations WHERE id=?",
                        (gid_done,)).fetchone()["status"] == "done"
    conn.close()


def test_chain_resume_point_and_resume(client, monkeypatch):
    """A chain with segments 0,1 done resumes at segment 2, continuing V2V from
    segment 1's video; the killed segment's failed row is removed."""
    import services_media
    from db import get_conn

    conn = get_conn()
    cid = conn.execute(
        "INSERT INTO video_chains (prompts, status, completed_segments) "
        "VALUES ('[\"a\",\"b\",\"c\"]', 'failed', 2)").lastrowid
    for idx, (st, path) in enumerate([("done", "/v/seg0.mp4"), ("done", "/v/seg1.mp4"),
                                      ("failed", None)]):
        conn.execute(
            "INSERT INTO videos (prompt, status, chain_id, chain_index, video_path) "
            "VALUES (?,?,?,?,?)", (f"seg{idx}", st, cid, idx, path))
    conn.commit()

    assert services_media.chain_resume_point(cid) == (2, "/v/seg1.mp4")

    calls = []
    import services_media_chain   # run_chain_generation lives here now; patch where it's looked up
    monkeypatch.setattr(services_media_chain, "run_chain_generation",
                        lambda chain_id, _start_idx=0, _prev_video_path=None:
                        calls.append((chain_id, _start_idx, _prev_video_path)))
    services_media.resume_chain_generation(cid)
    assert calls == [(cid, 2, "/v/seg1.mp4")]
    left = conn.execute("SELECT status FROM videos WHERE chain_id=?", (cid,)).fetchall()
    assert [r["status"] for r in left] == ["done", "done"], "failed segment row removed"
    conn.close()


def test_interrupted_video_chain_audio_resumed(client, monkeypatch):
    """Guard pause snapshots mid-flight video/chain/audio jobs; on resume the
    failed ones are re-launched (single video re-queued, chain resumed, clip rerun)."""
    import services_media
    from db import get_conn
    from orchestrator import orch

    redone = {"video": [], "chain": [], "audio": []}
    monkeypatch.setattr(services_media, "run_video_generation",
                        lambda vid: redone["video"].append(vid))
    monkeypatch.setattr(services_media, "resume_chain_generation",
                        lambda cid: redone["chain"].append(cid))
    monkeypatch.setattr(services_media, "run_audio_clip",
                        lambda aid: redone["audio"].append(aid))

    conn = get_conn()
    vid = conn.execute("INSERT INTO videos (prompt, status) VALUES ('solo', 'generating')").lastrowid
    cid = conn.execute("INSERT INTO video_chains (prompts, status) "
                       "VALUES ('[\"a\"]', 'generating')").lastrowid
    aid = conn.execute("INSERT INTO audio_clips (prompt, status) "
                       "VALUES ('song', 'generating')").lastrowid
    conn.commit()

    orch.resume()
    client.post("/api/gpu/guard/state", json={"busy": True, "apps": ["steam_app_620"]})

    # the node's pkill lands: all three die as 'failed'
    conn.execute("UPDATE videos SET status='failed' WHERE id=?", (vid,))
    conn.execute("UPDATE video_chains SET status='failed' WHERE id=?", (cid,))
    conn.execute("UPDATE audio_clips SET status='failed' WHERE id=?", (aid,))
    conn.commit()

    client.post("/api/gpu/guard/state", json={"busy": False})
    import time as _t
    _t.sleep(0.3)

    assert redone == {"video": [vid], "chain": [cid], "audio": [aid]}
    assert conn.execute("SELECT status FROM videos WHERE id=?", (vid,)).fetchone()["status"] == "queued"
    conn.close()


def test_store_busy_reflects_gpu_work(client):
    """store_busy (the miner gate) is False when idle and True while any media
    row is generating — the node only mines when this stays False."""
    from db import get_conn

    assert client.get("/api/gpu/guard/state").json()["store_busy"] is False

    conn = get_conn()
    gid = conn.execute(
        "INSERT INTO generations (prompt, status) VALUES ('busy probe', 'generating')").lastrowid
    conn.commit()
    assert client.get("/api/gpu/guard/state").json()["store_busy"] is True

    conn.execute("UPDATE generations SET status='done' WHERE id=?", (gid,))
    conn.commit()
    conn.close()
    assert client.get("/api/gpu/guard/state").json()["store_busy"] is False


def _set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    conn.commit()


def test_guard_enabled_setting(client):
    """gpu_guard_enabled=0 → busy heartbeats never pause, and a pause taken
    before the setting was flipped mid-game is released."""
    from db import get_conn
    from orchestrator import orch

    conn = get_conn()
    orch.resume()
    client.post("/api/gpu/guard/state", json={"busy": True, "apps": ["steam_app_1"]})
    assert orch.is_paused()

    _set(conn, "gpu_guard_enabled", "0")
    r = client.post("/api/gpu/guard/state", json={"busy": True, "apps": ["steam_app_1"]})
    assert not orch.is_paused(), "disabling mid-game must release the guard pause"
    assert r.json()["guard_paused"] is False
    assert client.get("/api/gpu/guard/state").json()["guard_enabled"] is False

    _set(conn, "gpu_guard_enabled", "1")
    client.post("/api/gpu/guard/state", json={"busy": False})
    conn.close()


def test_auto_resume_setting(client, monkeypatch):
    """gpu_guard_auto_resume=0 → interrupted jobs stay failed, no rerun."""
    import services
    from db import get_conn
    from orchestrator import orch

    redone = []
    monkeypatch.setattr(services, "run_generation", lambda gid: redone.append(gid))
    conn = get_conn()
    _set(conn, "gpu_guard_auto_resume", "0")
    gid = conn.execute(
        "INSERT INTO generations (prompt, status) VALUES ('no redo', 'generating')").lastrowid
    conn.commit()

    orch.resume()
    client.post("/api/gpu/guard/state", json={"busy": True, "apps": ["blender"]})
    conn.execute("UPDATE generations SET status='failed' WHERE id=?", (gid,))
    conn.commit()
    client.post("/api/gpu/guard/state", json={"busy": False})
    import time as _t
    _t.sleep(0.2)

    assert redone == []
    assert conn.execute("SELECT status FROM generations WHERE id=?",
                        (gid,)).fetchone()["status"] == "failed"
    _set(conn, "gpu_guard_auto_resume", "1")
    conn.close()


def test_stale_guard_auto_resumes(client, monkeypatch):
    from orchestrator import orch
    from routers import gpu_guard

    orch.resume()
    client.post("/api/gpu/guard/state", json={"busy": True, "apps": ["blender"]})
    assert orch.is_paused()
    # Beat goes stale → next status read unsticks the queue.
    monkeypatch.setitem(gpu_guard._state, "last_beat",
                        gpu_guard._state["last_beat"] - gpu_guard.STALE_SEC - 1)
    gpu_guard.maybe_unstick()
    assert not orch.is_paused()
    assert gpu_guard.guard_info()["busy"] is False


# ── editor co-op ──────────────────────────────────────────────────────────────

def test_editor_mode_does_not_pause_the_queue(client):
    """mode="editor" is the whole point of co-op: an engine editor must NOT stop
    the queue, because the AI may be driving that editor over MCP — pausing would
    deadlock the work the user just started."""
    from db import get_conn
    from orchestrator import orch
    from routers import gpu_guard

    conn = get_conn()
    orch.resume()
    r = client.post("/api/gpu/guard/state",
                    json={"busy": True, "apps": ["UnrealEditor"], "mode": "editor"})
    assert r.status_code == 200
    assert not orch.is_paused(), "editor co-op must never pause the queue"
    body = r.json()
    assert body["mode"] == "editor" and body["editor_active"] is True
    assert body["guard_paused"] is False
    assert gpu_guard.editor_active() is True
    assert gpu_guard.coop_blocks_media() is True, "media jobs are held under co-op"

    client.post("/api/gpu/guard/state", json={"busy": False})
    assert gpu_guard.editor_active() is False
    conn.close()


def test_editor_mode_off_falls_back_to_full_pause(client):
    """With gpu_guard_editor_mode=0 an editor is an ordinary heavy app again."""
    from db import get_conn
    from orchestrator import orch

    conn = get_conn()
    orch.resume()
    _set(conn, "gpu_guard_editor_mode", "0")
    r = client.post("/api/gpu/guard/state",
                    json={"busy": True, "apps": ["UnrealEditor"], "mode": "editor"})
    assert orch.is_paused(), "co-op disabled → editor pauses like any heavy app"
    assert r.json()["editor_active"] is False

    _set(conn, "gpu_guard_editor_mode", "1")
    client.post("/api/gpu/guard/state", json={"busy": False})
    conn.close()


def test_editor_coop_releases_a_pause_taken_by_the_heavy_path(client):
    """Launching an editor from a shell that first tripped the heavy path must
    end in co-op, not stay paused."""
    from db import get_conn
    from orchestrator import orch

    conn = get_conn()
    orch.resume()
    client.post("/api/gpu/guard/state", json={"busy": True, "apps": ["blender"]})
    assert orch.is_paused()

    client.post("/api/gpu/guard/state",
                json={"busy": True, "apps": ["UnrealEditor"], "mode": "editor"})
    assert not orch.is_paused(), "co-op supersedes an earlier heavy pause"

    client.post("/api/gpu/guard/state", json={"busy": False})
    conn.close()


def test_mcp_model_setting_is_exposed_to_the_node(client):
    """The node reads editor_mode + mcp_model off the GET to decide co-op vs the
    heavy handoff, and which model fits beside the editor."""
    from db import get_conn
    from routers import gpu_guard

    conn = get_conn()
    _set(conn, "games_mcp_model", "google/gemma-3-4b-qat")
    st = client.get("/api/gpu/guard/state").json()
    assert st["editor_mode"] is True
    assert st["mcp_model"] == "google/gemma-3-4b-qat"
    assert gpu_guard.mcp_model() == "google/gemma-3-4b-qat"

    _set(conn, "games_mcp_model", "")
    assert gpu_guard.mcp_model() == "", "blank = orchestrator default"
    conn.close()

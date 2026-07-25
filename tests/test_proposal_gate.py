"""Proposal review gate — LLM judge + dedup + mode actions (proposal_gate.py)."""
import db
import proposal_gate as pg


def _prop(title, description="", tags="T-Shirt"):
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO proposals (title,description,source,source_label,tags) VALUES (?,?,?,?,?)",
        (title, description, "manual", "Manual", tags),
    )
    pid = cur.lastrowid
    conn.commit(); conn.close()
    return pid


def _row(pid):
    conn = db.get_conn()
    r = conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
    conn.close()
    return r


def _set(key, val):
    conn = db.get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(val)))
    conn.commit(); conn.close()


def _defaults():
    _set("proposal_gate_mode", "auto_weed")
    _set("proposal_gate_reject_below", "40")
    _set("proposal_gate_approve_above", "80")


def test_migration_added_score_columns():
    conn = db.get_conn()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(proposals)").fetchall()}
    conn.close()
    assert {"score", "score_reason", "verdict", "reviewed_at"} <= cols


def test_parse_judge_tolerates_chatty_replies():
    raw = 'Thinking about it...\n```json\n{"score": 87, "reason": "strong niche"}\n```'
    assert pg._parse_judge(raw) == (87, "strong niche")
    assert pg._parse_judge("no json here")[0] is None
    assert pg._parse_judge('{"score": 250, "reason": "clamped"}')[0] == 100


def test_low_score_auto_weeds(monkeypatch):
    _defaults()
    monkeypatch.setattr(pg, "_judge_llm", lambda s, u: '{"score": 12, "reason": "generic cliche"}')
    pid = _prop("Totally generic mug idea nobody searches")
    r = pg.review_proposal_sync(pid)
    row = _row(pid)
    assert r["verdict"] == "reject" and r["action"] == "auto_rejected"
    assert row["status"] == "rejected" and row["score"] == 12
    assert row["reviewed_at"] and row["score_reason"] == "generic cliche"


def test_hold_stays_pending(monkeypatch):
    _defaults()
    monkeypatch.setattr(pg, "_judge_llm", lambda s, u: '{"score": 60, "reason": "maybe"}')
    pid = _prop("Mid tier vaporwave possum concept")
    r = pg.review_proposal_sync(pid)
    assert r["verdict"] == "hold" and _row(pid)["status"] == "pending"


def test_auto_weed_never_auto_approves(monkeypatch):
    _defaults()
    monkeypatch.setattr(pg, "_judge_llm", lambda s, u: '{"score": 95, "reason": "banger"}')
    monkeypatch.setattr(pg, "_start_generation_async",
                        lambda gid: (_ for _ in ()).throw(AssertionError("no gen in auto_weed")))
    pid = _prop("Retro synthwave sloth drinking espresso")
    r = pg.review_proposal_sync(pid)
    row = _row(pid)
    assert r["verdict"] == "approve" and r["action"] == "held" and row["status"] == "pending"


def test_full_auto_queues_generations(monkeypatch):
    _defaults()
    _set("proposal_gate_mode", "full_auto")
    monkeypatch.setattr(pg, "_judge_llm", lambda s, u: '{"score": 92, "reason": "strong"}')
    started = []
    monkeypatch.setattr(pg, "_start_generation_async", started.append)
    pid = _prop("Cottagecore frog knight embroidery style", tags="Hoodie,Mug")
    r = pg.review_proposal_sync(pid)
    row = _row(pid)
    assert r["action"] == "auto_approved:2" and row["status"] == "approved"
    conn = db.get_conn()
    gens = conn.execute("SELECT * FROM generations WHERE proposal_id=?", (pid,)).fetchall()
    conn.close()
    assert len(gens) == 2 and len(started) == 2
    assert gens[0]["product_type"] == "Hoodie"          # first tag wins
    _set("proposal_gate_mode", "auto_weed")             # don't leak full_auto to later tests


def test_score_only_records_but_never_rejects(monkeypatch):
    _defaults()
    _set("proposal_gate_mode", "score_only")
    monkeypatch.setattr(pg, "_judge_llm", lambda s, u: '{"score": 3, "reason": "trademarked"}')
    pid = _prop("Blatant brand ripoff tee idea here")
    r = pg.review_proposal_sync(pid)
    row = _row(pid)
    assert r["verdict"] == "reject" and r["action"] == "scored"
    assert row["status"] == "pending" and row["verdict"] == "reject"
    _set("proposal_gate_mode", "auto_weed")


def test_near_duplicate_skips_llm_and_older_survives(monkeypatch):
    _defaults()
    _set("proposal_gate_mode", "score_only")
    older = _prop("Funny cat astronaut floating in space")
    newer = _prop("Funny cat astronaut floating around deep space")
    monkeypatch.setattr(pg, "_judge_llm",
                        lambda s, u: (_ for _ in ()).throw(AssertionError("dup must not hit the LLM")))
    r = pg.review_proposal_sync(newer)
    assert r["verdict"] == "reject" and "duplicate" in r["reason"].lower()
    assert _row(older)["score"] is None                 # older twin untouched
    _set("proposal_gate_mode", "auto_weed")


def test_thresholds_clamp_bad_config():
    _set("proposal_gate_reject_below", "90")
    _set("proposal_gate_approve_above", "10")           # nonsense: approve below reject
    lo, hi = pg.thresholds()
    assert lo == 90 and hi >= lo
    _defaults()


def test_gate_tick_disabled():
    _set("proposal_gate_enabled", "0")
    assert pg.gate_tick() == {"skipped": "disabled"}


def test_gate_tick_submits_when_enabled(monkeypatch):
    _set("proposal_gate_enabled", "1")
    monkeypatch.setattr(pg, "submit_review_all", lambda priority=2: {"task_id": 9, "queued": 2})
    assert pg.gate_tick()["task_id"] == 9
    _set("proposal_gate_enabled", "0")


def test_review_endpoints(client, monkeypatch):
    pid = _prop("Endpoint smoke proposal for the judge")
    monkeypatch.setattr(pg, "submit_review", lambda p: 4242)
    r = client.post(f"/api/proposals/{pid}/review")
    assert r.status_code == 200 and r.json()["task_id"] == 4242
    r = client.post("/api/proposals/99999/review")
    assert r.status_code == 404
    monkeypatch.setattr(pg, "submit_review_all", lambda priority=1: {"task_id": 7, "queued": 3})
    r = client.post("/api/proposals/review-all")
    assert r.status_code == 200 and r.json()["queued"] == 3


def test_settings_defaults_exposed(client):
    s = client.get("/api/settings").json()
    assert s.get("proposal_gate_mode") in ("score_only", "auto_weed", "full_auto")
    assert "proposal_gate_reject_below" in s and "proposal_gate_approve_above" in s


def test_list_proposals_sorts_scored_first(client):
    _defaults()
    hi = _prop("Sorted high scorer wolf howling moon graphic")
    lo = _prop("Sorted lower scorer turtle skateboard graphic")
    conn = db.get_conn()
    conn.execute("UPDATE proposals SET score=90, verdict='approve' WHERE id=?", (hi,))
    conn.execute("UPDATE proposals SET score=55, verdict='hold' WHERE id=?", (lo,))
    conn.commit(); conn.close()
    ids = [p["id"] for p in client.get("/api/proposals?status=pending").json()]
    assert ids.index(hi) < ids.index(lo)
    unscored = [p["id"] for p in client.get("/api/proposals?status=pending").json() if p["score"] is None]
    if unscored:                                        # scored rows come before unscored rows
        assert max(ids.index(hi), ids.index(lo)) < min(ids.index(u) for u in unscored)

"""Oracle ladder: per-rung generation rows, the rebalanced short-horizon scoring
math (vs the preserved legacy curve), the settings gates, and the accuracy-weighted
consensus signal the Company consumes."""
import math
from datetime import datetime, timedelta

import db


def _conn():
    return db.get_conn()


def _clear_predictions():
    c = _conn()
    c.execute("DELETE FROM oracle_predictions")
    c.commit(); c.close()


# ── ladder generation: one row per rung, shared batch, correct resolve dates ──
def test_round_inserts_one_row_per_rung(client, monkeypatch):
    from routers.oracle import forecast as fc
    _clear_predictions()
    monkeypatch.setattr(fc, "_price", lambda a: 100.0)
    monkeypatch.setattr(fc, "_searx", lambda q, n=5: [])
    monkeypatch.setattr(fc, "_forecast", lambda ag, a, price, research: {
        "thesis": "test thesis",
        "rungs": [{"days": d, "direction": "up", "target_price": 100.0 + d, "confidence": 0.6}
                  for d in (1, 3, 5, 7, 14)],
    })
    fc._run_round(1)                      # synchronous — no thread
    c = _conn()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM oracle_predictions ORDER BY id").fetchall()]
    n_agents = c.execute("SELECT COUNT(*) AS n FROM oracle_agents WHERE active=1").fetchone()["n"]
    c.close()
    assert len(rows) == n_agents * 5, "one row per rung per active analyst"
    # per-agent: shared batch_id, one row per horizon, resolve_at = created + days
    by_agent = {}
    for r in rows:
        by_agent.setdefault(r["agent_id"], []).append(r)
    for rs in by_agent.values():
        assert len({r["batch_id"] for r in rs}) == 1 and rs[0]["batch_id"]
        assert sorted(r["horizon_days"] for r in rs) == [1, 3, 5, 7, 14]
        now = datetime.now()
        for r in rs:
            resolve = datetime.fromisoformat(r["resolve_at"])
            delta_days = (resolve - now).total_seconds() / 86400
            assert abs(delta_days - r["horizon_days"]) < 0.05, "resolve_at ≈ now + rung days"
            assert r["status"] == "open" and r["thesis"] == "test thesis"


def test_clean_rungs_validation_and_legacy_shape():
    from routers.oracle.forecast import _clean_rungs
    want = [1, 3, 5, 7, 14]
    # junk + duplicate + off-ladder rungs are dropped; direction derived when missing
    rungs = _clean_rungs({"rungs": [
        {"days": 1, "target_price": 101},                        # no direction → derived up
        {"days": 1, "direction": "down", "target_price": 99},    # duplicate day → dropped
        {"days": 4, "direction": "up", "target_price": 102},     # not a rung → dropped
        {"days": 14, "direction": "down", "target_price": 90, "confidence": 2.5},
        {"days": 7, "direction": "up", "target_price": -5},      # bad price → dropped
    ]}, 100.0, want)
    assert [r["days"] for r in rungs] == [1, 14]
    assert rungs[0]["direction"] == "up"
    assert rungs[1]["confidence"] == 1.0                          # clamped
    # old-style single-object reply becomes one rung snapped to the nearest horizon
    one = _clean_rungs({"direction": "down", "target_price": 95, "horizon_days": 30}, 100.0, want)
    assert len(one) == 1 and one[0]["days"] == 14


# ── scoring: ladder curve vs preserved legacy curve ───────────────────────────
def test_scoring_ladder_curve_math():
    from routers.oracle.scoring import _score

    def pred(h, direction="up", tgt=105.0, batch="b1"):
        return {"current_value": 100.0, "target_value": tgt, "direction": direction,
                "horizon_days": h, "batch_id": batch}

    # perfect calls: correct 2-week beats correct 1-day MODESTLY (not the old 4× blowout)
    s1, ok1, _ = _score(pred(1, tgt=102.0), 102.0)
    s14, ok14, _ = _score(pred(14, tgt=102.0), 102.0)
    assert ok1 and ok14 and s1 > 0
    assert s14 > s1, "a correct 2-week call still beats a correct 1-day call"
    assert s14 / s1 < 2.0, "…but only modestly"
    # exact expected values: base 30 × (1+√h/6)
    assert s1 == round(30 * (1 + math.sqrt(1) / 6), 2)
    assert s14 == round(30 * (1 + math.sqrt(14) / 6), 2)
    # a wrong-direction 1-day call can't ride a flat market into a big positive score
    sw, okw, _ = _score(pred(1, direction="down", tgt=99.9), 100.1)
    assert not okw and sw < 1.0
    # closeness is horizon-scaled: 2% off is decent at 1d tolerance-wise vs 14d
    far1, _, _ = _score(pred(1, tgt=110.0), 102.0)     # ~7.8% off at 1d: outside tol → direction only
    assert far1 == round(10 * (1 + math.sqrt(1) / 6), 2)


def test_scoring_legacy_rows_keep_old_curve():
    from routers.oracle.scoring import _score
    legacy = {"current_value": 100.0, "target_value": 105.0, "direction": "up",
              "horizon_days": 60, "batch_id": None}
    s, ok, rel = _score(legacy, 105.0)
    assert ok and rel == 0.0
    assert s == round((10 + 20) * (1 + 60 / 30.0), 2)   # exactly the old formula


# ── settings gates (every gate ships with a toggle) ───────────────────────────
def test_oracle_settings_roundtrip(client):
    r = client.get("/api/oracle/settings")
    assert r.status_code == 200
    d = r.json()
    assert d["settings"]["oracle_auto"] == "on"
    assert d["ladder_days"] == [1, 3, 5, 7, 14]
    r = client.post("/api/oracle/settings", json={"settings": {
        "oracle_auto": "off", "oracle_auto_rounds": "0", "oracle_long_tier": "1",
        "oracle_company_hookup": "0", "oracle_ladder": "1,7,14", "bogus_key": "x"}})
    assert r.status_code == 200
    d = r.json()
    assert d["settings"]["oracle_auto"] == "off"
    assert d["settings"]["oracle_company_hookup"] == "0"
    assert d["ladder_days"] == [1, 7, 14, 30]           # ladder + long tier
    assert "bogus_key" not in d["settings"]
    # invalid ladders rejected
    assert client.post("/api/oracle/settings",
                       json={"settings": {"oracle_ladder": "0,7"}}).status_code == 400
    assert client.post("/api/oracle/settings",
                       json={"settings": {"oracle_ladder": "nope"}}).status_code == 400
    # restore defaults for the rest of the suite
    r = client.post("/api/oracle/settings", json={"settings": {
        "oracle_auto": "on", "oracle_auto_rounds": "1", "oracle_long_tier": "0",
        "oracle_company_hookup": "1", "oracle_ladder": "1,3,5,7,14"}})
    assert r.json()["ladder_days"] == [1, 3, 5, 7, 14]


# ── predicted-asset config: crypto set is owner-editable, defaults unchanged ──
def _clear_crypto_asset_setting():
    conn = _conn()
    conn.execute("DELETE FROM settings WHERE key='oracle_crypto_assets'")
    conn.commit(); conn.close()


def test_crypto_assets_default_to_the_five_majors(client):
    from routers.oracle._base import crypto_ids, _assets
    _clear_crypto_asset_setting()
    assert crypto_ids() == {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
                            "XRP": "ripple", "DOGE": "dogecoin"}
    assets = _assets()
    assert assets[:5] == ["BTC", "ETH", "SOL", "XRP", "DOGE"]


def test_oracle_assets_endpoint_add_remove_crypto(client):
    from routers.oracle._base import _assets
    _clear_crypto_asset_setting()
    r = client.get("/api/oracle/assets")
    assert r.status_code == 200
    d = r.json()
    assert d["crypto"] == {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
                           "XRP": "ripple", "DOGE": "dogecoin"}
    assert "BTC" in d["assets"]

    # add a new coin — requires a CoinGecko id, degrades cleanly (400) without one
    assert client.post("/api/oracle/assets/crypto", json={"symbol": "KAS"}).status_code in (400, 422)
    r = client.post("/api/oracle/assets/crypto", json={"symbol": "kas", "coingecko_id": "kaspa"})
    assert r.status_code == 200
    d = r.json()
    assert d["crypto"]["KAS"] == "kaspa"
    assert "KAS" in _assets(), "adding a crypto changes _assets()"

    # remove a default — the tracked set shrinks accordingly
    r = client.delete("/api/oracle/assets/crypto/DOGE")
    assert r.status_code == 200
    assert "DOGE" not in r.json()["crypto"]
    assert "DOGE" not in _assets()
    assert "KAS" in _assets()             # the earlier add persists

    # can't remove a symbol that isn't tracked
    assert client.delete("/api/oracle/assets/crypto/NOPE").status_code == 404

    _clear_crypto_asset_setting()          # restore defaults for the rest of the suite


def test_oracle_assets_stock_watchlist_still_works(client):
    from routers.oracle._base import _assets
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('stocks_watchlist','AAPL,MSFT')")
    conn.commit(); conn.close()
    assets = _assets()
    assert "AAPL" in assets and "MSFT" in assets
    r = client.get("/api/oracle/assets")
    assert set(r.json()["stocks"]) == {"AAPL", "MSFT"}
    conn = _conn()
    conn.execute("DELETE FROM settings WHERE key='stocks_watchlist'")
    conn.commit(); conn.close()


# ── consensus signal (advisory, accuracy-weighted, toggle-gated) ──────────────
def test_consensus_endpoint_and_hookup_gate(client):
    _clear_predictions()
    c = _conn()
    a1 = c.execute("SELECT id FROM oracle_agents ORDER BY id LIMIT 1").fetchone()["id"]
    a2 = c.execute("SELECT id FROM oracle_agents ORDER BY id LIMIT 1 OFFSET 1").fetchone()["id"]
    # track records: a1 sharp (4/4), a2 poor (0/4) → a1's calls dominate the weighting
    for aid, correct in ((a1, 1), (a2, 0)):
        for i in range(4):
            c.execute("INSERT INTO oracle_predictions (agent_id,agent_name,market,asset,"
                      "current_value,direction,target_value,horizon_days,resolve_at,confidence,"
                      "status,correct,score,batch_id) VALUES (?,?,?,?,?,?,?,?,?,?, 'resolved',?,?,?)",
                      (aid, f"A{aid}", "crypto", "ETH", 100, "up", 105, 7,
                       datetime.now().isoformat(timespec="seconds"), 0.6, correct, 10, f"hist{aid}{i}"))
    # open ladder: sharp agent says down, poor agent says up — consensus must lean down
    for aid, direction in ((a1, "down"), (a2, "up")):
        for h in (1, 7):
            c.execute("INSERT INTO oracle_predictions (agent_id,agent_name,market,asset,"
                      "current_value,direction,target_value,horizon_days,resolve_at,confidence,status,batch_id) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?, 'open',?)",
                      (aid, f"A{aid}", "crypto", "BTC", 50000, direction, 49000 if direction == "down" else 51000,
                       h, (datetime.now() + timedelta(days=h)).isoformat(timespec="seconds"), 0.6, f"open{aid}"))
    c.commit(); c.close()

    r = client.get("/api/oracle/consensus")
    assert r.status_code == 200
    d = r.json()
    assert d["enabled"] is True
    btc = next(a for a in d["assets"] if a["asset"] == "BTC")
    assert btc["bias"] < 0, "accuracy-weighted consensus follows the sharper analyst"
    assert btc["n_agents"] == 2 and btc["n_calls"] == 4
    assert [r_["h"] for r_ in btc["rungs"]] == [1, 7]
    assert "BTC" in d["brief"] and "advisory" in d["brief"].lower()

    # the hookup toggle silences the brief (world/crypto/money consumers go quiet)
    client.post("/api/oracle/settings", json={"settings": {"oracle_company_hookup": "0"}})
    d = client.get("/api/oracle/consensus").json()
    assert d["enabled"] is False and d["brief"] == ""
    from routers.oracle import consensus as oc
    assert oc.brief() == ""
    client.post("/api/oracle/settings", json={"settings": {"oracle_company_hookup": "1"}})
    _clear_predictions()

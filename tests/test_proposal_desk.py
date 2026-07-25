"""Proposal desk + lanes + Etsy signal + gate throttle + loop-map entries.

Covers the multi-lane origination layer built on top of the review gate:
feed tagging (trends.py feed groups), lane routing/selection, agent-suggestion
harvest (proposal_desk.py), etsy_signal parsing, the proposal_gate batch
throttle, and the new 🕸️ Agent Loops graph entries (world_loops.py).
"""
import json

import db
import etsy_signal
import proposal_desk as pd
import proposal_gate as pg
import trends


def _set(key, val):
    conn = db.get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(val)))
    conn.commit(); conn.close()


def _clear(key):
    conn = db.get_conn()
    conn.execute("DELETE FROM settings WHERE key=?", (key,))
    conn.commit(); conn.close()


# ── migration ─────────────────────────────────────────────────────────────────
def test_migration_added_lane_column():
    conn = db.get_conn()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(proposals)").fetchall()}
    conn.close()
    assert "lane" in cols


# ── feed tagging ──────────────────────────────────────────────────────────────
def test_feed_group_config_defaults_and_overrides():
    cfg = trends.feed_group_config({})
    assert set(cfg) == set(trends.FEED_GROUPS)
    assert cfg["tech_news"]["enabled"] and cfg["tech_news"]["urls"]
    assert cfg["local_news"]["urls"] == []          # no global default for local
    over = trends.feed_group_config({
        "trend_feed_tech_news_enabled": "false",
        "trend_feed_local_news_urls": "https://my.town/rss\n\nhttps://county.example/feed\n",
    })
    assert over["tech_news"]["enabled"] is False
    assert over["local_news"]["urls"] == ["https://my.town/rss", "https://county.example/feed"]


def test_fetch_feed_groups_tags_items_with_category(monkeypatch):
    monkeypatch.setattr(trends, "fetch_rss_feeds",
                        lambda urls, limit_per_feed=6: [f"headline via {u}" for u in urls])
    cfg = {
        "world_news": {"enabled": True, "urls": ["http://w1", "http://w2"]},
        "game_news": {"enabled": True, "urls": ["http://g1"]},
        "tech_news": {"enabled": False, "urls": ["http://t1"]},   # disabled → skipped
        "local_news": {"enabled": True, "urls": []},              # empty → skipped
    }
    items = trends.fetch_feed_groups(cfg)
    assert ("headline via http://w1", "world_news") in items
    assert ("headline via http://g1", "game_news") in items
    assert all(cat != "tech_news" for _, cat in items)
    assert len(items) == 3


def test_reddit_sub_categorization():
    assert trends.categorize_reddit_sub("gaming") == "reddit_gaming"
    assert trends.categorize_reddit_sub("Technology") == "reddit_tech"
    assert trends.categorize_reddit_sub("memes") == "reddit"


def test_source_labels_for_new_categories():
    assert trends._source_display("world_news") == "World news"
    assert trends._source_label("tech_news") == "news"
    assert trends._source_label("etsy") == "etsy"
    assert trends._source_display("google_trends") == "Google Trends"   # legacy unchanged


# ── lane selection ────────────────────────────────────────────────────────────
def test_lane_items_routes_by_source_category():
    items = [("cat meme", "reddit"), ("election chaos", "world_news"),
             ("new GPU", "tech_news"), ("indie hit", "game_news"),
             ("cottagecore", "etsy")]
    by_id = {l["id"]: l for l in trends.LANES}
    assert trends.lane_items(by_id["tech"], items) == [("new GPU", "tech_news")]
    assert trends.lane_items(by_id["gaming"], items) == [("indie hit", "game_news")]
    assert trends.lane_items(by_id["market"], items) == [("cottagecore", "etsy")]
    assert trends.lane_items(by_id["evergreen"], items) == []          # source-less lane
    humor = trends.lane_items(by_id["humor"], items)
    assert ("cat meme", "reddit") in humor and ("election chaos", "world_news") in humor


def test_parse_lanes_enabled_validates_and_defaults():
    assert trends.parse_lanes_enabled("humor, market, bogus") == ["humor", "market"]
    assert trends.parse_lanes_enabled("") == trends.lane_ids()
    assert trends.parse_lanes_enabled(None) == trends.lane_ids()


def test_generate_proposals_stamps_lane_and_empty_lane_runs():
    fake = lambda sys, user, max_tokens=0, json_mode=False: json.dumps([
        {"trend": "t", "title": "Sourdough Rebellion Tee", "description": "d",
         "tags": "bread,baking", "source": "etsy"}])
    out = trends.generate_proposals_from_trends(
        [("sourdough", "etsy")], fake, system="s", lane="market")
    assert out and out[0]["lane"] == "market" and out[0]["source"] == "etsy"
    # evergreen: no trend items, still generates when allow_empty
    out2 = trends.generate_proposals_from_trends([], fake, system="s",
                                                 lane="evergreen", allow_empty=True)
    assert out2 and out2[0]["lane"] == "evergreen"
    assert trends.generate_proposals_from_trends([], fake) == []       # legacy path unchanged


def test_every_lane_prompt_is_registered():
    from prompts import get_prompt
    for lane in trends.LANES:
        text = get_prompt(lane["prompt_key"])
        assert "Return ONLY valid JSON" in text
    assert "{etsy_signal}" in get_prompt("lane_market")


# ── etsy signal ───────────────────────────────────────────────────────────────
def test_parse_trending_html_pulls_market_slugs():
    html = ('<a href="https://www.etsy.com/market/trending_now">now</a>'
            '<a href="/market/cottagecore_decor?ref=x">a</a>'
            '<a class="x" href="https://www.etsy.com/market/funny-cat-mug">b</a>'
            '<a href="/market/cottagecore_decor">dup</a>'
            '<a href="/listing/123/thing">not a market link</a>')
    terms = etsy_signal.parse_trending_html(html)
    assert terms == ["cottagecore decor", "funny cat mug"]
    assert etsy_signal.parse_trending_html("") == []


def test_signal_text_briefs_hot_and_not():
    snap = {"fetched_at": 1, "hot_terms": ["cottagecore", "frog hat"],
            "own": {"top": [{"title": "Possum Tee", "views": 40, "favorites": 3}],
                    "cold": ["Sad Rock Poster"], "sold": 7}}
    txt = etsy_signal.signal_text(snap)
    assert "cottagecore" in txt and "Possum Tee" in txt
    assert "Sad Rock Poster" in txt and "Lifetime sales: 7" in txt
    assert etsy_signal.signal_text({}) == ""


def test_snapshot_uses_fresh_cache_without_refetch(monkeypatch):
    import time as _t
    _set("etsy_signal_cache", json.dumps({"fetched_at": int(_t.time()),
                                          "hot_terms": ["cached term"], "own": {}}))
    monkeypatch.setattr(etsy_signal, "refresh",
                        lambda: (_ for _ in ()).throw(AssertionError("fresh cache must not refetch")))
    assert etsy_signal.snapshot()["hot_terms"] == ["cached term"]
    _clear("etsy_signal_cache")


# ── agent/oracle/researcher suggestions → proposals ───────────────────────────
def _suggest(text, category="products", agent_key="zz_desk_test"):
    conn = db.get_conn()
    cur = conn.execute("INSERT INTO world_suggestions (agent_key,text,category) VALUES (?,?,?)",
                       (agent_key, text, category))
    sid = cur.lastrowid
    conn.commit(); conn.close()
    return sid


def test_qualifies_filter():
    assert pd.qualifies("Launch a retro camping mug collection now", "products")
    assert pd.qualifies("A new shirt design series about houseplants", "ops")   # product-ish ops
    assert not pd.qualifies("Batch the nightly backups differently", "ops")     # process tweak
    assert not pd.qualifies("too short", "products")


def test_harvest_turns_suggestions_into_lane_tagged_proposals():
    _set("proposal_desk_last_suggestion_id", "999999")   # ignore anything pre-existing
    conn = db.get_conn()
    row = conn.execute("SELECT MAX(id) m FROM world_suggestions").fetchone()
    conn.close()
    _set("proposal_desk_last_suggestion_id", str(row["m"] or 0))
    s1 = _suggest("Sell a birdwatcher life-list checklist poster series")
    _suggest("Rebalance the worker shift schedule for morale", "ops")           # not a product
    r = pd.harvest_suggestions()
    assert r["inserted"] == 1
    conn = db.get_conn()
    p = conn.execute("SELECT * FROM proposals WHERE source='agent' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert "birdwatcher" in p["title"] and p["lane"] == "evergreen"   # unknown dept → evergreen
    # idempotent: the high-water mark means a re-run imports nothing
    assert pd.harvest_suggestions()["inserted"] == 0
    assert pd._int_setting("proposal_desk_last_suggestion_id", 0) >= s1


def test_dept_lane_mapping():
    assert pd.dept_lane("trends") == "news"
    assert pd.dept_lane("research") == "evergreen"
    assert pd.dept_lane("storefront") == "market"
    assert pd.dept_lane("devlab") == "tech"          # oracles live in devlab
    assert pd.dept_lane(None) == "evergreen"


def test_desk_tick_disabled_and_enabled(monkeypatch):
    _set("proposal_desk_enabled", "0")
    assert pd.desk_tick() == {"skipped": "disabled"}
    _set("proposal_desk_enabled", "1")
    _set("proposal_desk_scan", "0")                  # don't kick a real scan in tests
    monkeypatch.setattr(pd, "harvest_suggestions", lambda limit=12: {"scanned": 0, "inserted": 0})
    import etsy_signal as es
    monkeypatch.setattr(es, "snapshot", lambda max_age_min=None: {})
    r = pd.desk_tick()
    assert "suggestions" in r and "scan" not in r
    assert float(pd._setting("proposal_desk_last_run", "0")) > 0
    _set("proposal_desk_enabled", "0")
    _clear("proposal_desk_scan")


# ── gate batch throttle ───────────────────────────────────────────────────────
def test_batch_size_setting():
    _clear("proposal_gate_batch_size")
    assert pg.batch_size() == 10
    _set("proposal_gate_batch_size", "3")
    assert pg.batch_size() == 3
    _set("proposal_gate_batch_size", "-5")
    assert pg.batch_size() == 0                      # clamped: 0 = unlimited
    _clear("proposal_gate_batch_size")


def test_review_all_respects_limit(monkeypatch):
    _set("proposal_gate_mode", "score_only")
    conn = db.get_conn()
    for i in range(3):
        conn.execute("INSERT INTO proposals (title,source,tags) VALUES (?,?,?)",
                     (f"Throttle probe idea number {i} entirely unique words {i}", "manual", "T-Shirt"))
    conn.commit(); conn.close()
    monkeypatch.setattr(pg, "_judge_llm", lambda s, u: '{"score": 50, "reason": "mid"}')
    pending_before = len(pg._pending_unscored_ids())
    assert pending_before >= 3
    out = pg.review_all_sync(limit=2)
    assert out["reviewed"] + out["errors"] == 2
    assert len(pg._pending_unscored_ids()) == pending_before - 2
    _set("proposal_gate_mode", "auto_weed")


def test_submit_review_all_caps_queue_at_batch_size(monkeypatch):
    _set("proposal_gate_batch_size", "2")
    submitted = {}

    class _FakeOrch:
        def submit_llm(self, fn, desc="", priority=1, task=None):
            submitted["desc"] = desc
            return 777
        def poll(self, tid):
            return {"status": "done"}

    import deps
    monkeypatch.setattr(deps, "orch", _FakeOrch())
    monkeypatch.setattr(pg, "_batch_task_id", None)
    monkeypatch.setattr(pg, "_pending_unscored_ids", lambda: list(range(9)))
    r = pg.submit_review_all(priority=2)
    assert r["queued"] == 2 and r["pending"] == 9 and r["task_id"] == 777
    assert "judge 2 of 9" in submitted["desc"]
    _clear("proposal_gate_batch_size")


# ── company control plane ─────────────────────────────────────────────────────
def test_company_control_has_proposal_systems():
    import world_control as wc
    ids = {s["id"] for s in wc.SYSTEMS}
    assert {"proposal_desk", "proposal_gate"} <= ids
    keys = {s["id"]: s["key"] for s in wc.SYSTEMS}
    assert keys["proposal_desk"] == "proposal_desk_enabled"
    assert keys["proposal_gate"] == "proposal_gate_enabled"


# ── 🕸️ Agent Loops map ────────────────────────────────────────────────────────
def test_loop_graph_has_proposal_funnel():
    import world_ops
    world_ops.ensure()          # world tables (graph reads them live)
    import world_loops
    _set("proposal_desk_enabled", "1")
    _set("proposal_gate_enabled", "0")
    g = world_loops.graph()
    loops = {l["id"]: l for l in g["loops"]}
    assert "loop_desk" in loops and "loop_propgate" in loops
    assert loops["loop_desk"]["cadence"]["enabled"] is True     # live from settings
    assert loops["loop_propgate"]["cadence"]["enabled"] is False
    assert loops["loop_desk"]["drives"] == ["sys_desk"]
    nodes = {n["id"] for n in g["nodes"]}
    assert {"sys_desk", "sys_propgate"} <= nodes
    edges = {(e["from"], e["to"]) for e in g["edges"]}
    assert ("sys_desk", "sys_propgate") in edges
    assert ("sys_propgate", "p_listing") in edges
    _set("proposal_desk_enabled", "0")


def test_trend_config_exposes_feeds_and_lanes(client):
    cfg = client.get("/api/trends/config").json()
    assert set(cfg["feeds"]) == set(trends.FEED_GROUPS)
    assert {l["id"] for l in cfg["lanes"]} == set(trends.lane_ids())
    r = client.patch("/api/trends/config", json={
        "feed_game_news_enabled": False, "lanes_enabled": "humor,market"})
    assert r.status_code == 200
    cfg2 = client.get("/api/trends/config").json()
    assert cfg2["feeds"]["game_news"]["enabled"] is False
    assert cfg2["lanes_enabled"] == "humor,market"
    client.patch("/api/trends/config", json={"feed_game_news_enabled": True,
                                             "lanes_enabled": ""})

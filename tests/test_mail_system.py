"""Mail & Quotes rework — profiles, accounts, FAQ matching, order linking, the
auto-reply gate's modes + guardrails (mail_engine.py / mail_gate.py / routers/mail.py)."""
import json

import db
import db_schema
import mail_engine as me
import mail_gate as mg


def _set(key, val):
    conn = db.get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(val)))
    conn.commit(); conn.close()


def _del_setting(key):
    conn = db.get_conn()
    conn.execute("DELETE FROM settings WHERE key=?", (key,))
    conn.commit(); conn.close()


def _wipe_mail():
    conn = db.get_conn()
    for t in ("mail_accounts", "mail_profiles", "mail_faq", "mail_log"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()


def _gate_defaults():
    _set("mail_gate_mode", "full_auto")
    _set("mail_gate_confidence", "80")
    _set("mail_gate_allow", "")
    _set("mail_gate_deny", "")


def _mk_profile(**over):
    d = {"name": "Test Biz", "business_type": "test", "description": "a test business",
         "terms": "- be nice", "pricing": '{"hourly_rate": 40, "minimum_hours": 4}',
         "tone": "friendly", "signature": "Tester"}
    d.update(over)
    return me.save_profile(d)


def _mk_account(**over):
    d = {"label": "Box", "provider": "imap", "email": "box@test.local",
         "password": "hunter2", "imap_host": "127.0.0.1", "smtp_host": "127.0.0.1",
         "verify_cert": "0"}
    d.update(over)
    return me.save_account(d)


# ── schema + seed ─────────────────────────────────────────────────────────────
def test_mail_tables_exist():
    conn = db.get_conn()
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert {"mail_profiles", "mail_accounts", "mail_faq", "mail_log"} <= names


def test_seed_migrates_legacy_settings_once():
    _wipe_mail()
    _set("mail_user", "support@legacy.test")
    _set("mail_pass", "enc:v1:LEGACYBLOB")           # already-encrypted value copies verbatim
    _set("mail_imap_host", "10.9.8.7")
    _del_setting("mail_seeded_v1")
    conn = db.get_conn()
    db_schema.seed_mail_defaults(conn)
    conn.close()
    accts = me.list_accounts()
    profs = me.list_profiles()
    assert len(accts) == 1 and accts[0]["email"] == "support@legacy.test"
    assert accts[0]["imap_host"] == "10.9.8.7" and accts[0]["verify_cert"] == 0
    assert len(profs) == 2                            # carpentry default + store CS
    assert profs[0]["is_default"] == 1
    assert "hourly_rate" in profs[0]["pricing"]
    # the encrypted password row was copied byte-for-byte, never re-encrypted
    conn = db.get_conn()
    raw = conn.execute("SELECT password_enc FROM mail_accounts").fetchone()["password_enc"]
    conn.close()
    assert raw == "enc:v1:LEGACYBLOB"
    # idempotent: a second run adds nothing
    conn = db.get_conn()
    db_schema.seed_mail_defaults(conn)
    conn.close()
    assert len(me.list_accounts()) == 1 and len(me.list_profiles()) == 2
    _wipe_mail()
    _del_setting("mail_user"); _del_setting("mail_pass"); _del_setting("mail_imap_host")


def test_seed_noop_without_legacy_settings():
    _wipe_mail()
    _del_setting("mail_user")
    _del_setting("mail_seeded_v1")
    conn = db.get_conn()
    db_schema.seed_mail_defaults(conn)
    conn.close()
    assert me.list_accounts() == [] and me.list_profiles() == []


# ── accounts ──────────────────────────────────────────────────────────────────
def test_account_password_encrypted_at_rest_and_never_leaked(client):
    _wipe_mail()
    r = client.post("/api/mail/accounts", json={
        "label": "API box", "provider": "imap", "email": "api@test.local",
        "password": "sekrit", "imap_host": "h", "smtp_host": "h"})
    assert r.status_code == 200
    aid = r.json()["id"]
    conn = db.get_conn()
    raw = conn.execute("SELECT password_enc FROM mail_accounts WHERE id=?", (aid,)).fetchone()[0]
    conn.close()
    assert raw.startswith("enc:v1:") and "sekrit" not in raw
    listed = client.get("/api/mail/accounts").json()["accounts"][0]
    assert "password" not in listed and "password_enc" not in listed
    assert listed["password_set"] is True
    # blank password on update keeps the stored one
    r = client.post("/api/mail/accounts", json={
        "id": aid, "label": "renamed", "provider": "imap", "email": "api@test.local",
        "password": "", "imap_host": "h2", "smtp_host": "h"})
    assert r.status_code == 200
    a = me.get_account(aid)
    assert a["label"] == "renamed" and a["imap_host"] == "h2" and a["password"] == "sekrit"


def test_account_bad_provider_rejected(client):
    r = client.post("/api/mail/accounts", json={"label": "x", "provider": "pop3", "email": "x@y.z"})
    assert r.status_code == 400


# ── profiles + prompt rendering ───────────────────────────────────────────────
def test_profile_crud_and_default_exclusive(client):
    _wipe_mail()
    p1 = _mk_profile(name="Alpha", is_default="1")
    p2 = _mk_profile(name="Beta", is_default="1")
    profs = {p["name"]: p for p in me.list_profiles()}
    assert profs["Beta"]["is_default"] == 1 and profs["Alpha"]["is_default"] == 0
    r = client.post("/api/mail/profiles", json={"name": "Bad", "pricing": "not json"})
    assert r.status_code == 400
    client.delete(f"/api/mail/profiles/{p1}")
    assert [p["id"] for p in me.list_profiles()] == [p2]


def test_render_reply_system_fills_profile_faq_and_context():
    _wipe_mail()
    pid = _mk_profile(name="Acme Fixit", description="handyman services",
                      terms="- labor only", signature="Max — Acme Fixit")
    prof = me.get_profile(pid)
    sys = me.render_reply_system(prof)
    assert "Acme Fixit" in sys and "handyman services" in sys
    assert "- labor only" in sys and "Max — Acme Fixit" in sys
    assert "$40/hour, 4-hour minimum" in sys or "40/hour" in sys       # pricing block rendered
    assert "{business_name}" not in sys                                # no unfilled placeholders
    faq = {"question": "Do you ship?", "answer": "We ship everywhere."}
    ctx = {"designs": [], "orders": [{"id": "77", "status": "fulfilled", "created": "2026-07-01",
                                      "buyer_name": "Pat", "items": ["Frog Tee"]}], "proposals": []}
    sys2 = me.render_reply_system(prof, faq=faq, order_ctx=ctx)
    assert "We ship everywhere." in sys2 and "Order 77" in sys2


# ── FAQ matching ──────────────────────────────────────────────────────────────
def test_faq_matching_local_scoring():
    _wipe_mail()
    pid = _mk_profile(name="Shop")
    conn = db.get_conn()
    conn.execute("INSERT INTO mail_faq (profile_id,question,answer) VALUES (?,?,?)",
                 (pid, "Do you ship internationally?", "Yes, worldwide via tracked mail."))
    conn.execute("INSERT INTO mail_faq (profile_id,question,answer) VALUES (?,?,?)",
                 (None, "What is your return policy?", "Returns within 30 days."))
    conn.execute("INSERT INTO mail_faq (profile_id,question,answer,enabled) VALUES (?,?,?,0)",
                 (None, "Do you offer gift wrapping?", "No."))
    conn.commit(); conn.close()
    faq, score = me.match_faq("Hi! Quick question — do you ship internationally to France?", pid)
    assert faq and "worldwide" in faq["answer"] and score >= 0.6
    faq2, _ = me.match_faq("what's the return policy on a hoodie", pid)
    assert faq2 and "30 days" in faq2["answer"]
    # disabled FAQs never match; unrelated text matches nothing
    faq3, _ = me.match_faq("do you offer gift wrapping", pid)
    assert faq3 is None
    faq4, _ = me.match_faq("completely unrelated ramble about weather", pid)
    assert faq4 is None


def test_faq_endpoint_crud(client):
    _wipe_mail()
    r = client.post("/api/mail/faq", json={"question": "Q1?", "answer": "A1"})
    fid = r.json()["id"]
    assert client.get("/api/mail/faq").json()["faq"][0]["question"] == "Q1?"
    r = client.post("/api/mail/faq", json={"id": fid, "question": "Q1?", "answer": "A1+", "enabled": False})
    row = client.get("/api/mail/faq").json()["faq"][0]
    assert row["answer"] == "A1+" and row["enabled"] == 0
    client.delete(f"/api/mail/faq/{fid}")
    assert client.get("/api/mail/faq").json()["faq"] == []
    assert client.post("/api/mail/faq", json={"question": " ", "answer": ""}).status_code == 400


# ── order linking ─────────────────────────────────────────────────────────────
def test_link_order_context_matches_designs_by_listing_id(monkeypatch):
    monkeypatch.setattr(me, "_printify_orders", lambda: [])
    conn = db.get_conn()
    conn.execute("INSERT INTO generations (prompt) VALUES ('x')")
    conn.execute(
        "INSERT INTO designs (image_path, prompt, product_type, status, etsy_listing_id) "
        "VALUES ('a.png', 'Cottagecore frog knight tee', 'T-Shirt', 'published', '123456789')")
    conn.commit(); conn.close()
    ctx = me.link_order_context("buyer@x.com", "Question about listing 123456789",
                                "Hi, is this still available?")
    assert ctx["designs"] and ctx["designs"][0]["etsy_listing_id"] == "123456789"
    txt = me.context_text(ctx)
    assert "123456789" in txt and "Cottagecore" in txt
    # quoted-title fallback
    ctx2 = me.link_order_context("b@x.com", 'About the "cottagecore frog knight" shirt', "sizing?")
    assert ctx2["designs"]


def test_link_order_context_matches_live_orders_by_sender(monkeypatch):
    monkeypatch.setattr(me, "_printify_orders", lambda: [
        {"id": "687654321", "status": "in-production", "created": "2026-07-20",
         "buyer_name": "Pat", "buyer_email": "pat@buyer.com", "total_cents": 2499,
         "items": ["Frog Tee"]}])
    ctx = me.link_order_context("Pat@Buyer.com", "where is my stuff", "no ids here")
    assert ctx["orders"] and ctx["orders"][0]["id"] == "687654321"
    ctx2 = me.link_order_context("other@x.com", "order 687654321 status?", "")
    assert ctx2["orders"] and ctx2["orders"][0]["id"] == "687654321"
    ctx3 = me.link_order_context("other@x.com", "nothing relevant", "")
    assert ctx3["orders"] == []


# ── gate: parsing + guardrails (pure) ─────────────────────────────────────────
def test_parse_classify_tolerates_chatty_replies():
    raw = 'thinking…\n```json\n{"intent":"faq","confidence":91,"routine":true,"faq_id":3,"summary":"ok"}\n```'
    c = mg._parse_classify(raw)
    assert c == {"intent": "faq", "confidence": 91, "routine": True, "faq_id": 3, "summary": "ok"}
    assert mg._parse_classify("no json")is None
    assert mg._parse_classify('{"intent":"weird","confidence":900,"routine":false}')["intent"] == "other"
    assert mg._parse_classify('{"intent":"faq","confidence":900}')["confidence"] == 100


def test_money_guard():
    assert mg.money_guard_ok("Thanks, we ship worldwide!")
    assert not mg.money_guard_ok("That will be $120 total.")
    assert mg.money_guard_ok("Shipping is $5.99 flat.", faq_answer="Flat rate: $5.99 anywhere.")
    assert not mg.money_guard_ok("Shipping is $5.99 plus a $20 fee.", faq_answer="Flat $5.99.")


def test_decide_action_guardrails():
    _gate_defaults()
    routine_faq = {"intent": "faq", "confidence": 95, "routine": True}
    # green path
    act, why = mg.decide_action(routine_faq, "We ship worldwide.", "", "a@b.c", "full_auto")
    assert act == "send"
    # hard floor: quotes are ALWAYS held, whatever the confidence
    act, why = mg.decide_action({"intent": "quote_request", "confidence": 99, "routine": True},
                                "sure!", "", "a@b.c", "full_auto")
    assert act == "hold" and "human" in why
    # hard floor: non-routine held
    act, _ = mg.decide_action({"intent": "faq", "confidence": 95, "routine": False},
                              "ok", "", "a@b.c", "full_auto")
    assert act == "hold"
    # confidence threshold
    act, why = mg.decide_action({"intent": "faq", "confidence": 60, "routine": True},
                                "ok", "", "a@b.c", "full_auto")
    assert act == "hold" and "threshold" in why
    # money in the draft (not from the FAQ) holds
    act, why = mg.decide_action(routine_faq, "It costs $50.", "", "a@b.c", "full_auto")
    assert act == "hold" and "pricing" in why
    # spam skipped, deny list skipped
    act, _ = mg.decide_action({"intent": "spam", "confidence": 99, "routine": True},
                              "x", "", "a@b.c", "full_auto")
    assert act == "skip"
    _set("mail_gate_deny", "@bad.com")
    act, _ = mg.decide_action(routine_faq, "ok", "", "eve@bad.com", "full_auto")
    assert act == "skip"
    _set("mail_gate_deny", "")
    # allow list: only listed senders may auto-send
    _set("mail_gate_allow", "@trusted.com")
    act, _ = mg.decide_action(routine_faq, "ok", "", "a@elsewhere.com", "full_auto")
    assert act == "hold"
    act, _ = mg.decide_action(routine_faq, "ok", "", "a@trusted.com", "full_auto")
    assert act == "send"
    _set("mail_gate_allow", "")
    # auto_draft NEVER sends, even a perfect message
    act, _ = mg.decide_action(routine_faq, "ok", "", "a@b.c", "auto_draft")
    assert act == "hold"


# ── gate: end-to-end message pipeline ─────────────────────────────────────────
def _msg(frm="cust@x.com", subject="Do you ship internationally?", body="Hi — do you ship internationally?"):
    return {"uid": "42", "from_email": frm, "from_name": "Cust", "subject": subject,
            "body": body, "message_id": "<m1@x>", "images": []}


def _log_row(aid, uid="42"):
    conn = db.get_conn()
    r = conn.execute("SELECT * FROM mail_log WHERE account_id=? AND uid=?", (aid, uid)).fetchone()
    conn.close()
    return r


def test_full_auto_sends_routine_faq_and_logs(monkeypatch):
    _wipe_mail(); _gate_defaults()
    pid = _mk_profile(name="Shop")
    aid = _mk_account(profile_id=pid)
    conn = db.get_conn()
    conn.execute("INSERT INTO mail_faq (profile_id,question,answer) VALUES (?,?,?)",
                 (pid, "Do you ship internationally?", "Yes — worldwide, tracked."))
    conn.commit(); conn.close()
    fid = db.get_conn().execute("SELECT id FROM mail_faq").fetchone()["id"]
    monkeypatch.setattr(mg, "_classify_llm", lambda s, u:
        json.dumps({"intent": "faq", "confidence": 92, "routine": True, "faq_id": fid,
                    "summary": "shipping q"}))
    monkeypatch.setattr(mg, "_draft_llm", lambda s, u: "Yes — we ship worldwide, tracked!")
    monkeypatch.setattr(me, "_printify_orders", lambda: [])
    sent = []
    monkeypatch.setattr(mg, "_send", lambda a, to, sub, body, irt="": sent.append((to, sub, body)) or {"ok": True})
    r = mg.process_message_sync(aid, "42", msg=_msg(), gate_mode="full_auto")
    assert r["action"] == "send" and r.get("sent") and sent[0][0] == "cust@x.com"
    row = _log_row(aid)
    assert row["status"] == "sent" and row["intent"] == "faq" and row["faq_id"] == fid
    assert row["draft"] and row["sent_at"]
    # FAQ usage counter bumped
    conn = db.get_conn()
    assert conn.execute("SELECT hits FROM mail_faq WHERE id=?", (fid,)).fetchone()["hits"] == 1
    conn.close()


def test_full_auto_holds_quote_requests(monkeypatch):
    _wipe_mail(); _gate_defaults()
    aid = _mk_account(profile_id=_mk_profile())
    monkeypatch.setattr(mg, "_classify_llm", lambda s, u:
        '{"intent":"quote_request","confidence":99,"routine":false,"faq_id":null,"summary":"deck"}')
    monkeypatch.setattr(mg, "_draft_llm", lambda s, u: "Happy to help — roughly 6-10 hours.")
    monkeypatch.setattr(me, "_printify_orders", lambda: [])
    monkeypatch.setattr(mg, "_send", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("quote must NEVER auto-send")))
    r = mg.process_message_sync(aid, "42", msg=_msg(subject="Need a quote for a deck"),
                                gate_mode="full_auto")
    assert r["action"] == "hold"
    assert _log_row(aid)["status"] == "held"


def test_auto_draft_drafts_but_never_sends(monkeypatch):
    _wipe_mail(); _gate_defaults()
    _set("mail_gate_mode", "auto_draft")
    aid = _mk_account(profile_id=_mk_profile())
    monkeypatch.setattr(mg, "_classify_llm", lambda s, u:
        '{"intent":"faq","confidence":99,"routine":true,"faq_id":null,"summary":"hi"}')
    monkeypatch.setattr(mg, "_draft_llm", lambda s, u: "Draft reply.")
    monkeypatch.setattr(me, "_printify_orders", lambda: [])
    monkeypatch.setattr(mg, "_send", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("auto_draft must never send")))
    r = mg.process_message_sync(aid, "42", msg=_msg(), gate_mode="auto_draft")
    assert r["action"] == "hold"
    row = _log_row(aid)
    assert row["status"] == "held" and row["draft"] == "Draft reply."
    _set("mail_gate_mode", "manual")


def test_denied_and_spam_senders_skip_without_drafting(monkeypatch):
    _wipe_mail(); _gate_defaults()
    _set("mail_gate_deny", "@blocked.com")
    aid = _mk_account(profile_id=_mk_profile())
    monkeypatch.setattr(mg, "_classify_llm", lambda s, u:
        '{"intent":"faq","confidence":95,"routine":true,"faq_id":null,"summary":"x"}')
    monkeypatch.setattr(mg, "_draft_llm", lambda s, u: (_ for _ in ()).throw(
        AssertionError("denied sender must not burn a draft call")))
    monkeypatch.setattr(me, "_printify_orders", lambda: [])
    r = mg.process_message_sync(aid, "42", msg=_msg(frm="eve@blocked.com"), gate_mode="full_auto")
    assert r["action"] == "skipped"
    assert _log_row(aid)["status"] == "skipped"
    _set("mail_gate_deny", "")


def test_gate_tick_gating():
    _set("mail_gate_enabled", "0")
    assert mg.gate_tick() == {"skipped": "disabled"}
    _set("mail_gate_enabled", "1")
    _set("mail_gate_mode", "manual")
    assert mg.gate_tick() == {"skipped": "manual mode"}
    _set("mail_gate_mode", "auto_draft")
    _wipe_mail()                                   # no gate-enabled accounts
    assert mg.gate_tick() == {"skipped": "no gate-enabled accounts"}
    _set("mail_gate_enabled", "0")
    _set("mail_gate_mode", "manual")


def test_run_batch_respects_cap_and_dedupes(monkeypatch):
    _wipe_mail(); _gate_defaults()
    _set("mail_gate_mode", "auto_draft")
    _set("mail_gate_batch_size", "2")
    aid = _mk_account(profile_id=_mk_profile())
    headers = [{"uid": str(i), "from_email": f"c{i}@x.com", "from_name": "c",
                "subject": f"s{i}", "date": "", "seen": False, "message_id": f"<{i}>"}
               for i in range(5)]
    monkeypatch.setattr(me, "fetch_inbox", lambda a, limit=30, unseen_only=False: headers)
    processed = []

    def fake_process(account_id, uid, msg=None, gate_mode=None):
        processed.append(uid)
        conn = db.get_conn()
        conn.execute("INSERT OR IGNORE INTO mail_log (account_id, uid) VALUES (?,?)", (account_id, uid))
        conn.execute("UPDATE mail_log SET status='held' WHERE account_id=? AND uid=?", (account_id, uid))
        conn.commit(); conn.close()
        return {"uid": uid, "action": "hold"}
    monkeypatch.setattr(mg, "process_message_sync", fake_process)
    r = mg.run_batch_sync()
    assert r["processed"] == 2 and processed == ["0", "1"]     # capped at batch size
    r2 = mg.run_batch_sync()
    assert processed == ["0", "1", "2", "3"]                   # already-logged uids skipped
    _set("mail_gate_mode", "manual")
    _set("mail_gate_batch_size", "5")


# ── endpoints ─────────────────────────────────────────────────────────────────
def test_overview_and_settings_defaults(client):
    ov = client.get("/api/mail/overview").json()
    assert "accounts" in ov and "profiles" in ov and "gate" in ov and "counts" in ov
    assert ov["gate"]["mode"] in ("manual", "auto_draft", "full_auto")
    s = client.get("/api/settings").json()
    assert s.get("mail_gate_enabled") in ("0", "1")
    assert s.get("mail_gate_mode") in ("manual", "auto_draft", "full_auto")
    assert "mail_gate_confidence" in s and "mail_gate_batch_size" in s


def test_log_review_endpoints(client, monkeypatch):
    _wipe_mail()
    aid = _mk_account()
    conn = db.get_conn()
    conn.execute("INSERT INTO mail_log (account_id, uid, from_email, subject, draft, status) "
                 "VALUES (?, '7', 'c@x.com', 'hello', 'the draft', 'held')", (aid,))
    conn.commit()
    lid = conn.execute("SELECT id FROM mail_log").fetchone()["id"]
    conn.close()
    rows = client.get("/api/mail/log").json()["log"]
    assert rows and rows[0]["status"] == "held"
    sent = []
    monkeypatch.setattr(me, "send_mail", lambda a, to, sub, body, irt="": sent.append((to, body)) or {"ok": True})
    r = client.post(f"/api/mail/log/{lid}/send", json={"body": "edited draft"})
    assert r.status_code == 200 and sent == [("c@x.com", "edited draft")]
    conn = db.get_conn()
    row = conn.execute("SELECT status, draft FROM mail_log WHERE id=?", (lid,)).fetchone()
    conn.close()
    assert row["status"] == "sent" and row["draft"] == "edited draft"
    # already sent → 400; dismiss works on a fresh held row
    assert client.post(f"/api/mail/log/{lid}/send", json={}).status_code == 400
    conn = db.get_conn()
    conn.execute("INSERT INTO mail_log (account_id, uid, status, draft) VALUES (?, '8', 'drafted', 'd')", (aid,))
    conn.commit()
    lid2 = conn.execute("SELECT id FROM mail_log WHERE uid='8'").fetchone()["id"]
    conn.close()
    assert client.post(f"/api/mail/log/{lid2}/dismiss").json()["ok"]


def test_prompt_registry_has_mail_prompts():
    from prompts import get_prompt
    q = get_prompt("mail_quote")
    for ph in ("{business_name}", "{terms}", "{tone}", "{signature}", "{faq_block}", "{order_block}"):
        assert ph in q
    c = get_prompt("mail_classify")
    assert "intent" in c and "routine" in c


def test_company_layer_wiring():
    import world_control
    ids = {s["id"] for s in world_control.SYSTEMS}
    assert "mail_gate" in ids
    sys = next(s for s in world_control.SYSTEMS if s["id"] == "mail_gate")
    assert sys["key"] == "mail_gate_enabled"
    import systems_registry
    keys = {e["key"] for e in systems_registry.CATALOG}
    assert "mail_gate" in keys
    import world_loops
    conn = db.get_conn()
    loops = {l["id"] for l in world_loops._loops(conn.cursor())}
    conn.close()
    assert "loop_mail" in loops

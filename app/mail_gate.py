"""Mail auto-reply gate — the approval gate between incoming email and outgoing replies.

Same shape as proposal_gate.py: settings-driven modes, an LLM step, a scheduler tick
that submits ONE bounded orchestrator batch, and a persisted last-run stamp. But this
gate can SEND REAL EMAIL, so the guardrails are deliberately harder than the proposal
gate's:

  mode (mail_gate_mode)   what a new message gets
  manual                  nothing — the tick no-ops; you click Draft in the tab (today's flow)
  auto_draft              classified + a profile-driven AI draft, logged for your review
  full_auto               auto_draft, PLUS the reply is auto-SENT — but only when EVERY
                          guardrail passes; anything else is HELD for a human

Guardrails (full_auto; decide_action() is the single pure decision function):
  • hard floor: quote requests / anything committing money or pricing are ALWAYS held —
    a quote is a business commitment and stays human, whatever the confidence
  • hard floor: the classifier must call the message ROUTINE (a reply that commits no
    money, price, or promise); non-routine → held
  • confidence: below mail_gate_confidence (default 80) → held
  • money scan: a draft containing a dollar amount that is NOT verbatim in the matched
    FAQ answer → held ("pricing content")
  • sender lists: mail_gate_deny → skipped (no reply); a non-empty mail_gate_allow means
    only matching senders may auto-send (others are held)
  • spam → skipped, never replied to
  • review trail: EVERY processed message writes a mail_log row (classification, matched
    FAQ, order context, the draft, the action and the reason); UNIQUE(account_id, uid)
    is the reprocess guard
  • batch cap (mail_gate_batch_size, default 5) bounds every tick

Settings (rows in `settings`; UI in the Mail tab's Configuration section):
  mail_gate_enabled        "1"/"0"  scheduler master switch (default OFF)
  mail_gate_mode           manual | auto_draft | full_auto   (default manual)
  mail_gate_confidence     int 0-100, min classifier confidence to auto-send (default 80)
  mail_gate_interval_min   minutes between ticks (default 15)
  mail_gate_batch_size     max messages processed per tick (default 5)
  mail_gate_allow          sender allowlist (comma/newline, substring match; empty = off)
  mail_gate_deny           sender denylist  (comma/newline, substring match)
  mail_gate_last_run       epoch stamp (read by the 🕸️ Loops map)

Per-account applicability: mail_accounts.gate_enabled (+ enabled). Wiring:
scheduler.py calls gate_tick() on its 30s loop (same place the proposal gate ticks).
Every LLM call goes through orch.submit_llm → _call_lmstudio so the unified GPU queue
stays the single authority. Import-safe: module level touches only stdlib + db.
"""
import json
import logging
import re
import time

from db import get_conn

log = logging.getLogger("store")

MODES = ("manual", "auto_draft", "full_auto")

# intents the classifier may return; only these two are ever auto-send eligible
AUTO_SEND_INTENTS = ("faq", "order_support")

CLASSIFY_SYSTEM = """You are the mail-triage clerk for a small business. Classify the incoming email.
Intents:
- quote_request: asking for a price, estimate, bid, or to book paid work
- order_support: about an existing order/purchase (status, shipping, sizing, problem)
- faq: a general question answerable from the FAQ list
- spam: unsolicited marketing, scams, cold outreach
- other: anything else
routine is true ONLY if a reply commits NO money, NO price, NO refund and NO legal promise — a purely informational answer.
If a FAQ CANDIDATES list is provided and one clearly answers the email, set faq_id to that id, else null.
Return ONLY compact JSON, no markdown, no explanation:
{"intent": "<one of the five>", "confidence": <0-100>, "routine": true|false, "faq_id": <id or null>, "summary": "<one short sentence>"}"""


# ── settings ──────────────────────────────────────────────────────────────────
def _setting(key, default):
    try:
        conn = get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        if row and row["value"] not in (None, ""):
            return row["value"]
    except Exception:
        pass
    return default


def _int_setting(key, default):
    try:
        return int(float(_setting(key, str(default))))
    except Exception:
        return default


def enabled() -> bool:
    return str(_setting("mail_gate_enabled", "0")).strip().lower() in ("1", "true", "on", "yes")


def mode() -> str:
    m = str(_setting("mail_gate_mode", "manual")).strip()
    return m if m in MODES else "manual"


def confidence_threshold() -> int:
    return max(0, min(100, _int_setting("mail_gate_confidence", 80)))


def batch_size() -> int:
    return max(1, _int_setting("mail_gate_batch_size", 5))


def _list_setting(key) -> list:
    raw = str(_setting(key, "") or "")
    return [p.strip().lower() for p in re.split(r"[,\n;]+", raw) if p.strip()]


def sender_lists() -> tuple:
    """(allow, deny) — lowercase substring patterns."""
    return _list_setting("mail_gate_allow"), _list_setting("mail_gate_deny")


def _matches(addr: str, patterns: list) -> bool:
    a = (addr or "").lower()
    return any(p in a for p in patterns)


# ── LLM seams (monkeypatched in tests) ────────────────────────────────────────
def _classify_llm(system: str, user: str) -> str:
    from deps import _call_lmstudio
    return _call_lmstudio(system, user, max_tokens=400)


def _draft_llm(system: str, user: str) -> str:
    from deps import _call_lmstudio
    return _call_lmstudio(system, user, max_tokens=800)


def _parse_classify(raw):
    """Pull the LAST parseable classification object out of a chatty reply."""
    out = None
    for cand in re.findall(r"\{[^{}]*\}", raw or "", re.S):
        try:
            d = json.loads(cand)
        except Exception:
            continue
        if "intent" not in d:
            continue
        intent = str(d.get("intent", "other")).strip().lower()
        if intent not in ("quote_request", "order_support", "faq", "spam", "other"):
            intent = "other"
        try:
            conf = max(0, min(100, int(float(d.get("confidence", 0)))))
        except Exception:
            conf = 0
        fid = d.get("faq_id")
        try:
            fid = int(fid) if fid not in (None, "", "null") else None
        except Exception:
            fid = None
        out = {"intent": intent, "confidence": conf,
               "routine": bool(d.get("routine")), "faq_id": fid,
               "summary": str(d.get("summary", "") or "")[:200]}
    return out


# ── guardrails ────────────────────────────────────────────────────────────────
_MONEY_RE = re.compile(r"\$\s*\d[\d,]*(?:\.\d+)?|\b\d+\s*(?:USD|dollars)\b", re.I)


def money_guard_ok(draft: str, faq_answer: str = "") -> bool:
    """True when the draft carries no money amounts beyond what the matched FAQ
    answer already says verbatim. Conservative: any novel $-amount fails."""
    amounts = set(m.group(0).replace(" ", "") for m in _MONEY_RE.finditer(draft or ""))
    if not amounts:
        return True
    allowed = set(m.group(0).replace(" ", "") for m in _MONEY_RE.finditer(faq_answer or ""))
    return amounts <= allowed


def decide_action(cls: dict, draft: str, faq_answer: str, sender: str,
                  gate_mode: str = None) -> tuple:
    """The single pure gate decision: ('send'|'hold'|'skip', reason).
    'send' can ONLY come out of full_auto with every guardrail green."""
    m = gate_mode or mode()
    allow, deny = sender_lists()
    if _matches(sender, deny):
        return "skip", "sender is on the deny list"
    intent = (cls or {}).get("intent", "other")
    if intent == "spam":
        return "skip", "classified as spam"
    if m != "full_auto":
        return "hold", "auto_draft mode — awaiting your review"
    # ── full_auto guardrails (all must pass) ──
    if intent == "quote_request":
        return "hold", "quotes commit real pricing — always held for a human"
    if intent not in AUTO_SEND_INTENTS:
        return "hold", f"intent '{intent}' is not auto-send eligible"
    if not (cls or {}).get("routine"):
        return "hold", "classified as non-routine"
    conf = int((cls or {}).get("confidence") or 0)
    if conf < confidence_threshold():
        return "hold", f"confidence {conf} below threshold {confidence_threshold()}"
    if allow and not _matches(sender, allow):
        return "hold", "sender not on the allow list"
    if not (draft or "").strip():
        return "hold", "empty draft"
    if not money_guard_ok(draft, faq_answer):
        return "hold", "draft contains pricing/money content — held for a human"
    return "send", f"routine {intent}, confidence {conf}"


# ── sending seam (monkeypatched in tests) ─────────────────────────────────────
def _send(acct: dict, to: str, subject: str, body: str, in_reply_to: str = "") -> dict:
    import mail_engine
    return mail_engine.send_mail(acct, to, subject, body, in_reply_to)


# ── per-message pipeline ──────────────────────────────────────────────────────
def _log_upsert(conn, account_id, uid, **fields):
    conn.execute(
        "INSERT OR IGNORE INTO mail_log (account_id, uid) VALUES (?,?)", (account_id, uid))
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE mail_log SET {sets}, updated_at=datetime('now') "
                 "WHERE account_id=? AND uid=?", (*fields.values(), account_id, uid))
    conn.commit()


def process_message_sync(account_id: int, uid: str, msg: dict = None,
                         gate_mode: str = None) -> dict:
    """Classify one message, match FAQ + order context, draft a reply, and apply the
    gate action for the current mode. Runs inside an orchestrator LLM worker.
    `msg` may be pre-fetched (the tick passes it); else it is fetched here."""
    import mail_engine
    acct = mail_engine.get_account(account_id)
    if not acct:
        return {"error": "account not found"}
    if msg is None:
        msg = mail_engine.fetch_message(acct, uid, mark_seen=True)
    sender = msg.get("from_email", "")
    subject = msg.get("subject", "") or ""
    body = msg.get("body", "") or ""
    profile = mail_engine.get_profile(acct.get("profile_id"))

    conn = get_conn()
    _log_upsert(conn, account_id, uid,
                message_id=msg.get("message_id", ""), from_email=sender,
                from_name=msg.get("from_name", ""), subject=subject[:300], status="new")

    # local FAQ shortlist feeds the classifier; a strong local match stands alone
    local_faq, local_score = mail_engine.match_faq(f"{subject}\n{body}",
                                                   profile.get("id") if profile else None)
    candidates = mail_engine.faq_candidates(f"{subject}\n{body}",
                                            profile.get("id") if profile else None)
    cand_txt = "\n".join(f"[{c['id']}] Q: {c['question']}" for c in candidates) or "(none)"

    try:
        from prompts import get_prompt
        classify_sys = get_prompt("mail_classify")
    except Exception:
        classify_sys = CLASSIFY_SYSTEM
    user = (f"FROM: {sender}\nSUBJECT: {subject}\n\nBODY:\n{body[:2500]}\n\n"
            f"FAQ CANDIDATES:\n{cand_txt}")
    try:
        cls = _parse_classify(_classify_llm(classify_sys, user))
    except Exception as ex:
        _log_upsert(conn, account_id, uid, status="error", reason=f"classify failed: {ex}"[:300])
        conn.close()
        raise
    if not cls:
        _log_upsert(conn, account_id, uid, status="error", reason="unparseable classification")
        conn.close()
        return {"uid": uid, "error": "unparseable classification"}

    # resolve the FAQ: classifier pick wins, strong local match backs it up
    faq = None
    if cls.get("faq_id"):
        faq = next((c for c in candidates if c["id"] == cls["faq_id"]), None)
    if faq is None and local_faq and local_score >= 0.6:
        faq = local_faq
        cls["faq_id"] = local_faq["id"]

    order_ctx = mail_engine.link_order_context(sender, subject, body)
    _log_upsert(conn, account_id, uid,
                intent=cls["intent"], confidence=cls["confidence"],
                routine=1 if cls["routine"] else 0, faq_id=cls.get("faq_id"),
                order_ref=json.dumps(order_ctx) if any(order_ctx.values()) else "")

    # spam / denied senders never get a draft (no GPU spent on them either)
    m = gate_mode or mode()
    _, deny = sender_lists()
    if cls["intent"] == "spam" or _matches(sender, deny):
        action, reason = decide_action(cls, "", "", sender, m)
        _log_upsert(conn, account_id, uid, status="skipped", reason=reason)
        conn.close()
        return {"uid": uid, "intent": cls["intent"], "action": "skipped", "reason": reason}

    # draft (profile-driven; FAQ + order context injected)
    system = mail_engine.render_reply_system(profile, faq=faq, order_ctx=order_ctx)
    try:
        draft = _draft_llm(system, f"Customer email:\nFROM: {sender}\nSUBJECT: {subject}\n\n{body[:2500]}")
        draft = re.sub(r"<think>.*?</think>", "", draft or "", flags=re.DOTALL).strip()
    except Exception as ex:
        _log_upsert(conn, account_id, uid, status="error", reason=f"draft failed: {ex}"[:300])
        conn.close()
        raise
    if faq:
        mail_engine.bump_faq_hit(faq["id"])

    action, reason = decide_action(cls, draft, (faq or {}).get("answer", ""), sender, m)
    result = {"uid": uid, "intent": cls["intent"], "confidence": cls["confidence"],
              "action": action, "reason": reason}
    if action == "send":
        try:
            _send(acct, sender, f"Re: {subject}" if subject else "Re: your message",
                  draft, msg.get("message_id", ""))
            _log_upsert(conn, account_id, uid, draft=draft, status="sent", reason=reason,
                        sent_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
            result["sent"] = True
        except Exception as ex:
            _log_upsert(conn, account_id, uid, draft=draft, status="held",
                        reason=f"auto-send failed: {ex}"[:300])
            result.update(action="held", reason=f"auto-send failed: {ex}"[:200])
    else:
        _log_upsert(conn, account_id, uid, draft=draft,
                    status=("held" if action == "hold" else "skipped"), reason=reason)
    conn.close()
    return result


# ── batch + automation ────────────────────────────────────────────────────────
def _gate_accounts() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM mail_accounts WHERE enabled=1 AND gate_enabled=1 ORDER BY id").fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _already_logged(conn, account_id, uid) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM mail_log WHERE account_id=? AND uid=? AND status<>'new'",
        (account_id, uid)).fetchone())


def run_batch_sync(limit: int = None) -> dict:
    """Process up to `limit` NEW unseen messages across gate-enabled accounts inside
    ONE orchestrator task. Each message commits its log row as it finishes."""
    import mail_engine
    cap = batch_size() if limit is None else max(1, int(limit))
    m = mode()
    out = {"processed": 0, "sent": 0, "held": 0, "skipped": 0, "errors": 0, "items": []}
    if m == "manual":
        return {"skipped": "manual mode"}
    budget = cap
    for aid in _gate_accounts():
        if budget <= 0:
            break
        acct = mail_engine.get_account(aid)
        if not acct:
            continue
        try:
            headers = mail_engine.fetch_inbox(acct, limit=budget * 2, unseen_only=True)
        except Exception as ex:
            log.warning("mail gate: inbox fetch for account %s failed: %s", aid, ex)
            out["errors"] += 1
            continue
        conn = get_conn()
        fresh = [h for h in headers if not _already_logged(conn, aid, h["uid"])]
        conn.close()
        for h in fresh[:budget]:
            budget -= 1
            try:
                r = process_message_sync(aid, h["uid"], gate_mode=m)
            except Exception as ex:
                log.warning("mail gate: message %s/%s failed: %s", aid, h["uid"], ex)
                out["errors"] += 1
                continue
            if r.get("error"):
                out["errors"] += 1
                continue
            out["processed"] += 1
            a = r.get("action", "")
            if r.get("sent"):
                out["sent"] += 1
            elif a == "skipped":
                out["skipped"] += 1
            else:
                out["held"] += 1
            out["items"].append({k: r.get(k) for k in ("uid", "intent", "action", "reason")})
    log.info("mail gate: batch done — %s", {k: out[k] for k in
             ("processed", "sent", "held", "skipped", "errors")})
    return out


_batch_task_id = None      # last submitted batch — never stack a second one


def submit_batch(priority: int = 2, limit: int = None) -> dict:
    """Submit ONE orchestrator task that runs run_batch_sync. Refuses to stack."""
    global _batch_task_id
    from deps import orch
    if _batch_task_id is not None:
        try:
            st = orch.poll(_batch_task_id)
        except Exception:
            st = {}
        if st.get("status") in ("pending", "running"):
            return {"task_id": _batch_task_id, "already_running": True}
    cap = batch_size() if limit is None else max(1, int(limit))
    _batch_task_id = orch.submit_llm(
        lambda: run_batch_sync(limit=cap),
        desc=f"Mail gate: triage up to {cap} new messages",
        priority=priority,
        task="mail_classify",
    )
    return {"task_id": _batch_task_id, "cap": cap}


def gate_tick() -> dict:
    """Scheduler entry (scheduler.py, every mail_gate_interval_min). Non-blocking:
    submits ONE bounded batch at background priority and stamps mail_gate_last_run."""
    if not enabled():
        return {"skipped": "disabled"}
    if mode() == "manual":
        return {"skipped": "manual mode"}
    if not _gate_accounts():
        return {"skipped": "no gate-enabled accounts"}
    r = submit_batch(priority=2)
    try:
        conn = get_conn()
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                     ("mail_gate_last_run", str(time.time())))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return r

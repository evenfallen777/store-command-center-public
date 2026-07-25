"""Mail & Quotes — the config-driven mail desk.

Multiple mail ACCOUNTS (generic IMAP/SMTP or Gmail OAuth — mail_engine.py), editable
BUSINESS PROFILES that drive the reply/quote drafter (prompt-registry template
`mail_quote`), a per-profile FAQ knowledge base, store-order context linking, and the
auto-reply GATE (mail_gate.py — manual | auto_draft | full_auto with hard guardrails).

Back-compat: the original endpoints (/api/mail/config, /inbox, /message/{uid},
/draft-quote, /send) still work — they resolve to the default (first enabled) account,
which the db_schema seed migrated from the legacy mail_* settings, so the owner's
original Mailcow flow is unchanged. No business identity is hardcoded here: profiles
and accounts are rows, seeded once from the legacy settings (public forks start clean).
"""
import io
import base64
import re as _re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from deps import *          # get_conn, get_setting, orch, _call_lmstudio, BASE, _enc, _is_secret
from services import *

import mail_engine
import mail_gate

router = APIRouter()

ATTACH_DIR = BASE / "mail_attachments"
ATTACH_DIR.mkdir(exist_ok=True)

# Legacy single-mailbox settings (kept for back-compat + the Integrations status card).
_DEFAULTS = {
    "mail_imap_host": "127.0.0.1", "mail_imap_port": "993",
    "mail_smtp_host": "127.0.0.1", "mail_smtp_port": "587",
    "mail_user": "",
}


def _cfg(k):
    return get_setting(k, "") or _DEFAULTS.get(k, "")


def _acct(account_id=None) -> dict:
    """Resolve the working account: explicit id → that row; else the first enabled
    account; else (legacy fallback) a synthetic account from the old mail_* settings."""
    a = mail_engine.get_account(account_id)
    if a:
        return a
    if account_id:
        raise HTTPException(404, "Mail account not found")
    user = _cfg("mail_user")
    pw = _cfg("mail_pass")
    if not user or not pw:
        raise HTTPException(400, "No mail account configured — add one in the Mail tab's Configuration.")
    return {"id": None, "label": "Legacy mailbox", "provider": "imap", "email": user,
            "display_name": "", "username": user, "password": pw,
            "imap_host": _cfg("mail_imap_host"), "imap_port": int(_cfg("mail_imap_port") or 993),
            "imap_security": "ssl", "smtp_host": _cfg("mail_smtp_host"),
            "smtp_port": int(_cfg("mail_smtp_port") or 587), "smtp_security": "starttls",
            "verify_cert": 0, "signature": "", "profile_id": None, "enabled": 1}


# ── legacy config (kept working; the new UI uses /accounts + /profiles) ───────
class MailCfg(BaseModel):
    mail_imap_host: Optional[str] = None
    mail_imap_port: Optional[str] = None
    mail_smtp_host: Optional[str] = None
    mail_smtp_port: Optional[str] = None
    mail_user: Optional[str] = None
    mail_pass: Optional[str] = None


@router.get("/api/mail/config")
def mail_config_get():
    return {k: _cfg(k) for k in _DEFAULTS} | {"mail_pass_set": bool(_cfg("mail_pass"))}


@router.post("/api/mail/config")
def mail_config_set(c: MailCfg):
    conn = get_conn()
    for k, v in c.dict().items():
        if v is not None:
            val = v.strip()
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                         (k, _enc(val) if _is_secret(k) else val))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── overview (the tab's first paint) ──────────────────────────────────────────
@router.get("/api/mail/overview")
def mail_overview():
    conn = get_conn()
    counts = dict(conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN status IN ('drafted','held') THEN 1 ELSE 0 END),0) awaiting, "
        "COALESCE(SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END),0) sent, "
        "COALESCE(SUM(CASE WHEN status='sent' AND date(sent_at)=date('now') THEN 1 ELSE 0 END),0) sent_today, "
        "COALESCE(SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END),0) skipped "
        "FROM mail_log").fetchone())
    faq_count = conn.execute("SELECT COUNT(*) c FROM mail_faq WHERE enabled=1").fetchone()["c"]
    conn.close()
    return {
        "accounts": mail_engine.list_accounts(),
        "profiles": mail_engine.list_profiles(),
        "gate": {
            "enabled": mail_gate.enabled(),
            "mode": mail_gate.mode(),
            "confidence": mail_gate.confidence_threshold(),
            "interval_min": int(float(get_setting("mail_gate_interval_min", "15") or 15)),
            "batch_size": mail_gate.batch_size(),
            "allow": get_setting("mail_gate_allow", ""),
            "deny": get_setting("mail_gate_deny", ""),
            "last_run": float(get_setting("mail_gate_last_run", "0") or 0),
        },
        "counts": counts | {"faq": faq_count},
        "gmail_client_set": bool(get_setting("gmail_client_id", "")),
        "legacy_configured": bool(_cfg("mail_user") and _cfg("mail_pass")),
    }


# ── accounts ──────────────────────────────────────────────────────────────────
class AccountIn(BaseModel):
    id: Optional[int] = None
    label: Optional[str] = None
    provider: Optional[str] = "imap"
    email: Optional[str] = ""
    display_name: Optional[str] = ""
    username: Optional[str] = ""
    password: Optional[str] = ""          # blank on update = keep stored
    imap_host: Optional[str] = ""
    imap_port: Optional[int] = 993
    imap_security: Optional[str] = "ssl"
    smtp_host: Optional[str] = ""
    smtp_port: Optional[int] = 587
    smtp_security: Optional[str] = "starttls"
    verify_cert: Optional[bool] = True
    signature: Optional[str] = ""
    profile_id: Optional[int] = None
    enabled: Optional[bool] = True
    gate_enabled: Optional[bool] = True


@router.get("/api/mail/accounts")
def accounts_list():
    return {"accounts": mail_engine.list_accounts()}


@router.post("/api/mail/accounts")
def accounts_save(a: AccountIn):
    try:
        aid = mail_engine.save_account({k: v for k, v in a.dict().items() if v is not None})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": aid}


@router.delete("/api/mail/accounts/{account_id}")
def accounts_delete(account_id: int):
    mail_engine.delete_account(account_id)
    return {"ok": True}


@router.post("/api/mail/accounts/{account_id}/test")
def accounts_test(account_id: int):
    a = mail_engine.get_account(account_id)
    if not a:
        raise HTTPException(404, "Account not found")
    return mail_engine.test_account(a)


# ── Gmail OAuth (PKCE — same pattern as routers/etsy.py) ─────────────────────
class GmailAppIn(BaseModel):
    gmail_client_id: Optional[str] = None
    gmail_client_secret: Optional[str] = None


@router.post("/api/mail/gmail/app")
def gmail_app_save(g: GmailAppIn):
    """Save the Google OAuth client (one per install, from Google Cloud Console)."""
    conn = get_conn()
    for k, v in g.dict().items():
        if v is not None:
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                         (k, _enc(v.strip()) if _is_secret(k) else v.strip()))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/api/mail/gmail/connect")
def gmail_connect(account_id: int):
    try:
        return {"url": mail_engine.gmail_auth_url(account_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/mail/gmail/callback")
def gmail_callback(code: str = None, state: str = None, error: str = None):
    from fastapi.responses import HTMLResponse
    if error:
        return HTMLResponse(f"<h2 style='font-family:sans-serif'>Gmail auth failed: {error}</h2>"
                            "<p>Close this tab and try again.</p>")
    try:
        mail_engine.gmail_exchange_code(code, state)
        return HTMLResponse("""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#111;color:#eee">
        <h2>✅ Gmail Connected!</h2>
        <p>You can close this tab and return to the Store Command Center.</p>
        <script>if(window.opener){window.opener.postMessage('gmail_connected','*');setTimeout(()=>window.close(),1500);}</script>
        </body></html>""")
    except Exception as e:
        logger.error("Gmail token exchange failed: %s", e)
        return HTMLResponse(f"<h2 style='font-family:sans-serif'>Token exchange failed</h2><pre>{e}</pre>")


@router.delete("/api/mail/gmail/disconnect/{account_id}")
def gmail_disconnect(account_id: int):
    mail_engine.gmail_disconnect(account_id)
    return {"ok": True}


# ── business profiles ─────────────────────────────────────────────────────────
class ProfileIn(BaseModel):
    id: Optional[int] = None
    name: str
    business_type: Optional[str] = ""
    description: Optional[str] = ""
    terms: Optional[str] = ""
    pricing: Optional[str] = "{}"
    tone: Optional[str] = "warm, concise, professional"
    signature: Optional[str] = ""
    is_default: Optional[bool] = False


@router.get("/api/mail/profiles")
def profiles_list():
    return {"profiles": mail_engine.list_profiles()}


@router.post("/api/mail/profiles")
def profiles_save(p: ProfileIn):
    try:
        pid = mail_engine.save_profile(p.dict())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": pid}


@router.delete("/api/mail/profiles/{profile_id}")
def profiles_delete(profile_id: int):
    mail_engine.delete_profile(profile_id)
    return {"ok": True}


# ── FAQ knowledge base ────────────────────────────────────────────────────────
class FaqIn(BaseModel):
    id: Optional[int] = None
    profile_id: Optional[int] = None      # NULL = all profiles
    question: str
    answer: str
    enabled: Optional[bool] = True


@router.get("/api/mail/faq")
def faq_list(profile_id: Optional[int] = None):
    conn = get_conn()
    if profile_id:
        rows = conn.execute(
            "SELECT * FROM mail_faq WHERE profile_id IS NULL OR profile_id=? ORDER BY id",
            (profile_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM mail_faq ORDER BY id").fetchall()
    conn.close()
    return {"faq": [dict(r) for r in rows]}


@router.post("/api/mail/faq")
def faq_save(f: FaqIn):
    if not f.question.strip() or not f.answer.strip():
        raise HTTPException(400, "Question and answer are both required.")
    conn = get_conn()
    if f.id:
        conn.execute(
            "UPDATE mail_faq SET profile_id=?, question=?, answer=?, enabled=?, "
            "updated_at=datetime('now') WHERE id=?",
            (f.profile_id, f.question.strip(), f.answer.strip(), 1 if f.enabled else 0, f.id))
        fid = f.id
    else:
        cur = conn.execute(
            "INSERT INTO mail_faq (profile_id, question, answer, enabled) VALUES (?,?,?,?)",
            (f.profile_id, f.question.strip(), f.answer.strip(), 1 if f.enabled else 0))
        fid = cur.lastrowid
    conn.commit()
    conn.close()
    return {"ok": True, "id": fid}


@router.delete("/api/mail/faq/{faq_id}")
def faq_delete(faq_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM mail_faq WHERE id=?", (faq_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── inbox / message ───────────────────────────────────────────────────────────
@router.get("/api/mail/inbox")
def inbox(limit: int = 30, account_id: Optional[int] = None):
    a = _acct(account_id)
    try:
        messages = mail_engine.fetch_inbox(a, limit=limit)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"IMAP connect/login failed: {e}")
    # decorate with what the gate already knows (intent labels, status chips)
    if a.get("id"):
        conn = get_conn()
        known = {r["uid"]: dict(r) for r in conn.execute(
            "SELECT uid, intent, confidence, status, faq_id, order_ref FROM mail_log "
            "WHERE account_id=?", (a["id"],)).fetchall()}
        conn.close()
        for m in messages:
            k = known.get(m["uid"])
            if k:
                m["intent"] = k["intent"]
                m["gate_status"] = k["status"]
                m["has_context"] = bool(k["order_ref"])
    return {"count": len(messages), "account_id": a.get("id"), "messages": messages}


@router.get("/api/mail/message/{uid}")
def message(uid: str, account_id: Optional[int] = None):
    a = _acct(account_id)
    try:
        m = mail_engine.fetch_message(a, uid, mark_seen=True)
    except LookupError:
        raise HTTPException(404, "Message not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"IMAP connect/login failed: {e}")
    # store/order context + any existing gate log for this message
    m["context"] = mail_engine.link_order_context(m["from_email"], m["subject"], m["body"])
    if a.get("id"):
        conn = get_conn()
        r = conn.execute("SELECT * FROM mail_log WHERE account_id=? AND uid=?",
                         (a["id"], uid)).fetchone()
        conn.close()
        if r:
            m["log"] = {k: r[k] for k in ("intent", "confidence", "routine", "faq_id",
                                          "draft", "status", "reason")}
    m["profile_id"] = a.get("profile_id")
    return m


@router.delete("/api/mail/message/{uid}")
def message_delete(uid: str, account_id: Optional[int] = None):
    """Permanently delete a message from the mailbox (IMAP \\Deleted + expunge)."""
    a = _acct(account_id)
    try:
        mail_engine.delete_message(a, uid)
    except Exception as e:
        raise HTTPException(502, f"Delete failed: {e}")
    return {"ok": True, "deleted": uid}


# ── AI reply / quote draft ────────────────────────────────────────────────────
class DraftIn(BaseModel):
    uid: Optional[str] = None
    text: Optional[str] = None
    account_id: Optional[int] = None
    profile_id: Optional[int] = None      # override the account's bound profile
    faq_id: Optional[int] = None          # force a specific FAQ answer into the draft


def _img_data_url(path, max_px=1024):
    """Resize an image for the vision model and return a data: URL (keeps payload small)."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


@router.post("/api/mail/draft-quote")
def draft_quote(req: DraftIn):
    a = _acct(req.account_id)
    text = (req.text or "").strip()
    from_email, subject = "", ""
    image_paths = []
    if req.uid:
        m = message(req.uid, account_id=req.account_id)   # body + photo attachments to disk
        from_email, subject = m.get("from_email", ""), m.get("subject", "")
        if not text:
            text = m.get("body", "")
        for url in m.get("images", []):      # /mail-attachments/{key}/{fn} → disk path
            p = ATTACH_DIR / url.split("/mail-attachments/", 1)[-1]
            if p.exists():
                image_paths.append(p)
    if not text and not image_paths:
        raise HTTPException(400, "No message text to quote from.")

    profile = mail_engine.get_profile(req.profile_id or a.get("profile_id"))
    faq = None
    if req.faq_id:
        conn = get_conn()
        r = conn.execute("SELECT * FROM mail_faq WHERE id=?", (req.faq_id,)).fetchone()
        conn.close()
        faq = dict(r) if r else None
    else:
        faq, _score = mail_engine.match_faq(f"{subject}\n{text}",
                                            (profile or {}).get("id"))
    order_ctx = mail_engine.link_order_context(from_email, subject, text)

    def _work():
        quote_sys = mail_engine.render_reply_system(profile, faq=faq, order_ctx=order_ctx)
        content = [{"type": "text", "text": f"Customer message:\n{text[:2500]}"}]
        for p in image_paths[:4]:            # cap at 4 photos
            try:
                content.append({"type": "image_url", "image_url": {"url": _img_data_url(p)}})
            except Exception:
                pass
        model = getattr(orch, "_current_llm_model", None) or ENHANCE_MODEL
        if len(content) > 1:
            # Vision: the model 400s on a separate system role next to images → fold the
            # instructions into the user turn instead.
            content[0]["text"] = (quote_sys + "\n\n----\n" + content[0]["text"] +
                "\n\nThe customer attached the photo(s) above. Look at them to judge the actual "
                "scope and condition, and factor that into your reply/estimate. "
                "Mention what you can see in the photos.")
            messages = [{"role": "user", "content": content}]
        else:
            messages = [{"role": "system", "content": quote_sys},
                        {"role": "user", "content": content[0]["text"]}]
        body = {"model": model, "messages": messages,
                "max_tokens": 750, "temperature": 0.7, "reasoning_effort": "none"}
        r = httpx.post(f"{LMSTUDIO_URL}/chat/completions", json=body, headers=_llm_headers(), timeout=400)
        r.raise_for_status()
        out = (r.json()["choices"][0]["message"].get("content") or "").strip()
        out = _re.sub(r"<think>.*?</think>", "", out, flags=_re.DOTALL).strip()
        if faq:
            mail_engine.bump_faq_hit(faq["id"])
        return {"quote": out, "photos_analyzed": len(content) - 1,
                "profile": (profile or {}).get("name"),
                "faq_used": (faq or {}).get("id"),
                "context_used": bool(any(order_ctx.values()))}
    tid = orch.submit_llm(_work, desc="Draft mail reply (profile-driven)", task="mail_quote")
    return {"task_id": tid}


# ── send reply ────────────────────────────────────────────────────────────────
class SendIn(BaseModel):
    to: str
    subject: str
    body: str
    in_reply_to: Optional[str] = ""
    account_id: Optional[int] = None
    uid: Optional[str] = None             # source message uid → stamps the review trail


@router.post("/api/mail/send")
def send(req: SendIn):
    a = _acct(req.account_id)
    try:
        r = mail_engine.send_mail(a, req.to, req.subject, req.body, req.in_reply_to or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Send failed: {e}")
    if a.get("id") and req.uid:            # manual sends leave a trail too
        conn = get_conn()
        conn.execute("INSERT OR IGNORE INTO mail_log (account_id, uid) VALUES (?,?)",
                     (a["id"], req.uid))
        conn.execute("UPDATE mail_log SET status='sent', draft=?, reason='sent manually', "
                     "sent_at=datetime('now'), updated_at=datetime('now') "
                     "WHERE account_id=? AND uid=?", (req.body, a["id"], req.uid))
        conn.commit()
        conn.close()
    return r


# ── auto-reply gate: review queue + controls ──────────────────────────────────
@router.get("/api/mail/log")
def gate_log(status: Optional[str] = None, limit: int = 50):
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT l.*, a.label account_label, a.email account_email FROM mail_log l "
            "LEFT JOIN mail_accounts a ON a.id=l.account_id "
            "WHERE l.status=? ORDER BY l.id DESC LIMIT ?", (status, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT l.*, a.label account_label, a.email account_email FROM mail_log l "
            "LEFT JOIN mail_accounts a ON a.id=l.account_id "
            "ORDER BY l.id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return {"log": [dict(r) for r in rows]}


class LogSendIn(BaseModel):
    body: Optional[str] = None            # edited draft; None = send as drafted


@router.post("/api/mail/log/{log_id}/send")
def gate_log_send(log_id: int, req: Optional[LogSendIn] = None):
    """Approve a held/drafted auto-reply: send it (optionally edited) now."""
    conn = get_conn()
    r = conn.execute("SELECT * FROM mail_log WHERE id=?", (log_id,)).fetchone()
    conn.close()
    if not r:
        raise HTTPException(404, "Log entry not found")
    if r["status"] not in ("drafted", "held"):
        raise HTTPException(400, f"Entry is '{r['status']}' — only drafted/held replies can be sent.")
    body = (req.body if req and req.body else r["draft"]) or ""
    if not body.strip():
        raise HTTPException(400, "No draft body to send.")
    a = mail_engine.get_account(r["account_id"])
    if not a:
        raise HTTPException(404, "The account for this entry no longer exists.")
    try:
        mail_engine.send_mail(a, r["from_email"],
                              f"Re: {r['subject']}" if r["subject"] else "Re: your message",
                              body, r["message_id"] or "")
    except Exception as e:
        raise HTTPException(502, f"Send failed: {e}")
    conn = get_conn()
    conn.execute("UPDATE mail_log SET status='sent', draft=?, reason='approved by owner', "
                 "sent_at=datetime('now'), updated_at=datetime('now') WHERE id=?", (body, log_id))
    conn.commit()
    conn.close()
    return {"ok": True, "to": r["from_email"]}


@router.post("/api/mail/log/{log_id}/dismiss")
def gate_log_dismiss(log_id: int):
    conn = get_conn()
    r = conn.execute("SELECT id FROM mail_log WHERE id=?", (log_id,)).fetchone()
    if not r:
        conn.close()
        raise HTTPException(404, "Log entry not found")
    conn.execute("UPDATE mail_log SET status='dismissed', updated_at=datetime('now') WHERE id=?",
                 (log_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/mail/gate/run")
def gate_run_now():
    """Manual 'run the gate now' — one bounded batch regardless of the schedule
    (still respects the mode; manual mode has nothing to run)."""
    if mail_gate.mode() == "manual":
        raise HTTPException(400, "Gate mode is 'manual' — switch to auto_draft or full_auto first.")
    if not mail_gate._gate_accounts():
        raise HTTPException(400, "No gate-enabled accounts.")
    return mail_gate.submit_batch(priority=1)

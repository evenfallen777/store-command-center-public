"""Mail engine — accounts, providers, business profiles, FAQ matching, order context.

The de-hardcoded core of the Mail & Quotes tab. Everything that used to be a fixed
single-business constant (one Mailcow mailbox, one set of carpentry terms baked into
the prompt) is now DATA:

  • mail_accounts  — any number of mailboxes; provider 'imap' (generic IMAP/SMTP with
    ssl/starttls/plain + verify-cert options) or 'gmail' (Google OAuth, spoken over
    the same imaplib/smtplib code path via SASL XOAUTH2). Credentials are
    Fernet-encrypted in the row (crypto.enc — same at-rest scheme as secret settings).
  • mail_profiles  — editable business profiles (name/description/terms/pricing/tone/
    signature). The reply drafter is the prompt-registry template `mail_quote`
    (REPLY_SYSTEM below); render_reply_system() fills it from a profile, so changing
    the business is a settings edit, not a code change.
  • mail_faq       — per-profile Q&A knowledge base, matched locally by word overlap
    (match_faq — no LLM cost) and confirmed by the gate's classifier.
  • order context  — link_order_context() connects an incoming mail to store data:
    designs (etsy_listing_id / printify_id / title mentions) plus a cached best-effort
    pull of live Printify orders matched by order id or buyer name.

Import-safe: module level touches only stdlib + db + crypto. deps/services are
imported lazily inside functions (same rule as proposal_gate.py) so the module can be
imported by the scheduler, the router and the tests without a cycle.
"""
import base64
import email
import imaplib
import json
import logging
import re
import smtplib
import ssl
import time
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid, parseaddr

import httpx

from db import get_conn
from crypto import enc as _enc, dec as _dec

log = logging.getLogger("store")

PROVIDERS = ("imap", "gmail")
SECURITY = ("ssl", "starttls", "plain")

GMAIL_IMAP_HOST, GMAIL_SMTP_HOST = "imap.gmail.com", "smtp.gmail.com"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SCOPE = "https://mail.google.com/"        # full IMAP/SMTP access via XOAUTH2


# ── the profile-driven reply prompt (registry key: mail_quote) ────────────────
# {placeholders} are filled by render_reply_system() from the bound business
# profile + any FAQ / order context. Editable in Settings → Prompts; keep the
# placeholders when editing.
REPLY_SYSTEM = (
    "You are the email assistant for {business_name} — {business_description}\n"
    "Draft a friendly, professional email REPLY to the customer's message. Follow these "
    "NON-NEGOTIABLE business rules exactly:\n{terms}\n"
    "{pricing_block}"
    "{faq_block}"
    "{order_block}"
    "Tone: {tone}. Keep it concise and confident; answer what was actually asked. "
    "Never invent prices, discounts, refunds or dates that are not in the rules or "
    "context above. Sign off as '{signature}'. "
    "Output ONLY the email reply body text, no subject line."
)


# ── small helpers ─────────────────────────────────────────────────────────────
def _setting(key, default=""):
    try:
        conn = get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        if row and row["value"] not in (None, ""):
            return _dec(row["value"])
    except Exception:
        pass
    return default


def _set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
    conn.commit()
    conn.close()


def _dec_hdr(s):
    if not s:
        return ""
    out = []
    for part, enc_ in decode_header(s):
        out.append(part.decode(enc_ or "utf-8", "replace") if isinstance(part, bytes) else part)
    return "".join(out)


# ── accounts ──────────────────────────────────────────────────────────────────
_SECRET_COLS = ("password_enc", "gmail_access_token_enc", "gmail_refresh_token_enc")


def _acct_dict(row, secrets=False):
    a = dict(row)
    a["password_set"] = bool(a.get("password_enc"))
    a["gmail_connected"] = bool(a.get("gmail_refresh_token_enc"))
    if secrets:
        a["password"] = _dec(a.get("password_enc") or "")
        a["gmail_access_token"] = _dec(a.get("gmail_access_token_enc") or "")
        a["gmail_refresh_token"] = _dec(a.get("gmail_refresh_token_enc") or "")
    for c in _SECRET_COLS:
        a.pop(c, None)
    return a


def list_accounts(secrets=False):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM mail_accounts ORDER BY id").fetchall()
    conn.close()
    return [_acct_dict(r, secrets) for r in rows]


def get_account(account_id=None, secrets=True):
    """One account (default: the first ENABLED one) with decrypted credentials.
    Returns None when nothing is configured."""
    conn = get_conn()
    if account_id:
        row = conn.execute("SELECT * FROM mail_accounts WHERE id=?", (account_id,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM mail_accounts WHERE enabled=1 ORDER BY id LIMIT 1").fetchone()
    conn.close()
    return _acct_dict(row, secrets) if row else None


def save_account(data: dict) -> int:
    """Create/update an account. Blank password on update = keep the stored one."""
    provider = (data.get("provider") or "imap").strip()
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}")
    fields = {
        "label": (data.get("label") or data.get("email") or "Mail account").strip(),
        "provider": provider,
        "email": (data.get("email") or "").strip(),
        "display_name": (data.get("display_name") or "").strip(),
        "username": (data.get("username") or "").strip(),
        "imap_host": (data.get("imap_host") or "").strip(),
        "imap_port": int(data.get("imap_port") or 993),
        "imap_security": (data.get("imap_security") or "ssl") if (data.get("imap_security") or "ssl") in SECURITY else "ssl",
        "smtp_host": (data.get("smtp_host") or "").strip(),
        "smtp_port": int(data.get("smtp_port") or 587),
        "smtp_security": (data.get("smtp_security") or "starttls") if (data.get("smtp_security") or "starttls") in SECURITY else "starttls",
        "verify_cert": 1 if str(data.get("verify_cert", "1")) in ("1", "true", "True") else 0,
        "signature": data.get("signature") or "",
        "profile_id": data.get("profile_id") or None,
        "enabled": 1 if str(data.get("enabled", "1")) in ("1", "true", "True") else 0,
        "gate_enabled": 1 if str(data.get("gate_enabled", "1")) in ("1", "true", "True") else 0,
    }
    conn = get_conn()
    aid = data.get("id")
    if aid:
        sets = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values())
        if data.get("password"):
            sets += ", password_enc=?"
            vals.append(_enc(data["password"]))
        conn.execute(f"UPDATE mail_accounts SET {sets}, updated_at=datetime('now') WHERE id=?",
                     (*vals, aid))
    else:
        cols = list(fields) + ["password_enc"]
        vals = list(fields.values()) + [_enc(data.get("password") or "") or ""]
        cur = conn.execute(
            f"INSERT INTO mail_accounts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            vals)
        aid = cur.lastrowid
    conn.commit()
    conn.close()
    return int(aid)


def delete_account(account_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM mail_accounts WHERE id=?", (account_id,))
    conn.commit()
    conn.close()


def _store_tokens(account_id: int, access_token: str, refresh_token: str = None,
                  expires_in: int = 3600):
    conn = get_conn()
    if refresh_token:
        conn.execute(
            "UPDATE mail_accounts SET gmail_access_token_enc=?, gmail_refresh_token_enc=?, "
            "gmail_token_expires=?, updated_at=datetime('now') WHERE id=?",
            (_enc(access_token), _enc(refresh_token), int(time.time()) + int(expires_in or 3600),
             account_id))
    else:
        conn.execute(
            "UPDATE mail_accounts SET gmail_access_token_enc=?, gmail_token_expires=?, "
            "updated_at=datetime('now') WHERE id=?",
            (_enc(access_token), int(time.time()) + int(expires_in or 3600), account_id))
    conn.commit()
    conn.close()


# ── Gmail OAuth (PKCE — same pattern as routers/etsy.py) ─────────────────────
def gmail_redirect_uri() -> str:
    try:
        from config import GMAIL_REDIRECT_URI
        return GMAIL_REDIRECT_URI
    except Exception:
        return "http://localhost:8787/api/mail/gmail/callback"


def gmail_auth_url(account_id: int) -> str:
    """Start the OAuth2 PKCE flow for a gmail-provider account. The Google OAuth
    client id/secret are the global settings gmail_client_id / gmail_client_secret
    (the secret is encrypted at rest via SECRET_KEYS)."""
    import secrets as _secrets
    import urllib.parse
    client_id = _setting("gmail_client_id")
    if not client_id:
        raise ValueError("Set gmail_client_id (and gmail_client_secret) in the mail "
                         "configuration first — create an OAuth client in Google Cloud Console.")
    from etsy_client import generate_pkce
    verifier, challenge = generate_pkce()
    state = _secrets.token_urlsafe(16)
    _set_setting("gmail_pkce_verifier", verifier)
    _set_setting("gmail_pkce_state", state)
    _set_setting("gmail_pkce_account", str(account_id))
    params = {
        "client_id": client_id,
        "redirect_uri": gmail_redirect_uri(),
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def gmail_exchange_code(code: str, state: str) -> int:
    """OAuth callback half: verify state, swap the code for tokens, store them on
    the account that started the flow. Returns the account id."""
    if not code or state != _setting("gmail_pkce_state"):
        raise ValueError("Invalid OAuth state — start the connect flow again.")
    account_id = int(_setting("gmail_pkce_account", "0") or 0)
    if not account_id:
        raise ValueError("No account attached to this OAuth flow.")
    body = {
        "client_id": _setting("gmail_client_id"),
        "client_secret": _setting("gmail_client_secret"),
        "code": code,
        "code_verifier": _setting("gmail_pkce_verifier"),
        "grant_type": "authorization_code",
        "redirect_uri": gmail_redirect_uri(),
    }
    r = httpx.post(GOOGLE_TOKEN_URL, data=body, timeout=25)
    r.raise_for_status()
    tok = r.json()
    _store_tokens(account_id, tok["access_token"], tok.get("refresh_token"),
                  tok.get("expires_in", 3600))
    conn = get_conn()
    conn.execute("DELETE FROM settings WHERE key IN "
                 "('gmail_pkce_verifier','gmail_pkce_state','gmail_pkce_account')")
    conn.commit()
    conn.close()
    return account_id


def gmail_disconnect(account_id: int):
    conn = get_conn()
    conn.execute("UPDATE mail_accounts SET gmail_access_token_enc='', "
                 "gmail_refresh_token_enc='', gmail_token_expires=0, "
                 "updated_at=datetime('now') WHERE id=?", (account_id,))
    conn.commit()
    conn.close()


def gmail_access_token(acct: dict) -> str:
    """A valid access token for a gmail account, refreshing (and persisting) when
    the stored one is expired. `acct` must carry decrypted secrets."""
    if acct.get("gmail_access_token") and int(acct.get("gmail_token_expires") or 0) > time.time() + 60:
        return acct["gmail_access_token"]
    refresh = acct.get("gmail_refresh_token")
    if not refresh:
        raise RuntimeError("Gmail account is not connected — run the OAuth connect flow.")
    r = httpx.post(GOOGLE_TOKEN_URL, data={
        "client_id": _setting("gmail_client_id"),
        "client_secret": _setting("gmail_client_secret"),
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }, timeout=25)
    r.raise_for_status()
    tok = r.json()
    _store_tokens(acct["id"], tok["access_token"], None, tok.get("expires_in", 3600))
    acct["gmail_access_token"] = tok["access_token"]
    acct["gmail_token_expires"] = int(time.time()) + int(tok.get("expires_in", 3600))
    return tok["access_token"]


def _xoauth2(user: str, token: str) -> str:
    return f"user={user}\x01auth=Bearer {token}\x01\x01"


# ── connections ───────────────────────────────────────────────────────────────
def _ssl_ctx(verify: bool):
    c = ssl.create_default_context()
    if not verify:
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
    return c


def imap_connect(acct: dict):
    """Open + authenticate an IMAP connection for an account (any provider)."""
    if acct["provider"] == "gmail":
        tok = gmail_access_token(acct)
        M = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, 993, ssl_context=_ssl_ctx(True))
        M.authenticate("XOAUTH2", lambda x: _xoauth2(acct["email"], tok).encode())
        return M
    ctx = _ssl_ctx(bool(acct.get("verify_cert")))
    host, port = acct["imap_host"], int(acct.get("imap_port") or 993)
    sec = acct.get("imap_security") or "ssl"
    if sec == "ssl":
        M = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
    else:
        M = imaplib.IMAP4(host, port)
        if sec == "starttls":
            M.starttls(ssl_context=ctx)
    M.login(acct.get("username") or acct["email"], acct.get("password") or "")
    return M


def smtp_connect(acct: dict):
    """Open + authenticate an SMTP connection for an account (any provider)."""
    if acct["provider"] == "gmail":
        tok = gmail_access_token(acct)
        s = smtplib.SMTP(GMAIL_SMTP_HOST, 587, timeout=25)
        s.ehlo()
        s.starttls(context=_ssl_ctx(True))
        s.ehlo()
        auth = base64.b64encode(_xoauth2(acct["email"], tok).encode()).decode()
        s.docmd("AUTH", "XOAUTH2 " + auth)
        return s
    ctx = _ssl_ctx(bool(acct.get("verify_cert")))
    host, port = acct["smtp_host"], int(acct.get("smtp_port") or 587)
    sec = acct.get("smtp_security") or "starttls"
    if sec == "ssl":
        s = smtplib.SMTP_SSL(host, port, timeout=25, context=ctx)
    else:
        s = smtplib.SMTP(host, port, timeout=25)
        if sec == "starttls":
            s.starttls(context=ctx)
    s.login(acct.get("username") or acct["email"], acct.get("password") or "")
    return s


def test_account(acct: dict) -> dict:
    """Test Connection: try IMAP login + SMTP login (nothing is read or sent)."""
    out = {"imap": {"ok": False, "error": ""}, "smtp": {"ok": False, "error": ""}}
    try:
        M = imap_connect(acct)
        try:
            M.select("INBOX", readonly=True)
            out["imap"]["ok"] = True
        finally:
            try:
                M.logout()
            except Exception:
                pass
    except Exception as e:
        out["imap"]["error"] = str(e)[:300]
    try:
        s = smtp_connect(acct)
        try:
            s.noop()
            out["smtp"]["ok"] = True
        finally:
            try:
                s.quit()
            except Exception:
                pass
    except Exception as e:
        out["smtp"]["error"] = str(e)[:300]
    out["ok"] = out["imap"]["ok"] and out["smtp"]["ok"]
    conn = get_conn()
    conn.execute("UPDATE mail_accounts SET last_error=?, updated_at=datetime('now') WHERE id=?",
                 ("" if out["ok"] else (out["imap"]["error"] or out["smtp"]["error"]),
                  acct.get("id") or 0))
    conn.commit()
    conn.close()
    return out


# ── fetch ─────────────────────────────────────────────────────────────────────
def fetch_inbox(acct: dict, limit: int = 30, unseen_only: bool = False) -> list:
    """Message headers, newest first. Read-only (BODY.PEEK)."""
    M = imap_connect(acct)
    try:
        M.select("INBOX")
        typ, data = M.search(None, "UNSEEN" if unseen_only else "ALL")
        ids = data[0].split()[-limit:][::-1]
        out = []
        for i in ids:
            t, d = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)] FLAGS UID)")
            raw = b""
            uid = i.decode()
            seen = False
            for item in d:
                if isinstance(item, tuple):
                    raw += item[1]
                    m = re.search(rb"UID (\d+)", item[0])
                    if m:
                        uid = m.group(1).decode()
                    if b"\\Seen" in item[0]:
                        seen = True
            h = email.message_from_bytes(raw)
            frm = parseaddr(_dec_hdr(h.get("From")))
            out.append({"uid": uid, "from_name": frm[0] or frm[1], "from_email": frm[1],
                        "subject": _dec_hdr(h.get("Subject")) or "(no subject)",
                        "date": _dec_hdr(h.get("Date")), "seen": seen,
                        "message_id": h.get("Message-ID", "")})
        return out
    finally:
        try:
            M.logout()
        except Exception:
            pass


def delete_message(acct: dict, uid: str) -> bool:
    """Permanently delete a message by IMAP UID: mark \\Deleted then expunge.
    Irreversible — removes it from the mailbox on the server."""
    M = imap_connect(acct)
    try:
        M.select("INBOX")
        typ, _ = M.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
        if typ != "OK":
            raise RuntimeError(f"IMAP STORE \\Deleted failed for uid {uid}")
        M.expunge()
        return True
    finally:
        try:
            M.logout()
        except Exception:
            pass


def _body_and_images(msg, attach_dir, key):
    """(text_body, [image_urls]) — saves image attachments under attach_dir/key/."""
    text, html, imgs = "", "", []
    idx = 0
    for part in msg.walk():
        ctype = part.get_content_type()
        disp = str(part.get("Content-Disposition") or "")
        if ctype == "text/plain" and "attachment" not in disp and not text:
            text = (part.get_payload(decode=True) or b"").decode(
                part.get_content_charset() or "utf-8", "replace")
        elif ctype == "text/html" and "attachment" not in disp and not html:
            html = (part.get_payload(decode=True) or b"").decode(
                part.get_content_charset() or "utf-8", "replace")
        elif ctype.startswith("image/"):
            data = part.get_payload(decode=True)
            if data:
                ext = ctype.split("/")[-1].split("+")[0][:4] or "jpg"
                d = attach_dir / str(key)
                d.mkdir(parents=True, exist_ok=True)
                fn = f"{idx}.{ext}"
                (d / fn).write_bytes(data)
                imgs.append(f"/mail-attachments/{key}/{fn}")
                idx += 1
    if not text and html:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
    return text.strip(), imgs


def fetch_message(acct: dict, uid: str, mark_seen: bool = True) -> dict:
    """Full message body + saved image attachments for one UID."""
    from config import BASE
    attach_dir = BASE / "mail_attachments"
    M = imap_connect(acct)
    try:
        M.select("INBOX")
        t, d = M.uid("fetch", uid, "(RFC822)")
        if not d or not d[0]:
            raise LookupError("Message not found")
        msg = email.message_from_bytes(d[0][1])
        if mark_seen:
            M.uid("store", uid, "+FLAGS", "\\Seen")
        frm = parseaddr(_dec_hdr(msg.get("From")))
        key = f"{acct.get('id') or 0}-{uid}" if acct.get("id") else uid
        body, imgs = _body_and_images(msg, attach_dir, key)
        return {"uid": uid, "account_id": acct.get("id"),
                "from_name": frm[0] or frm[1], "from_email": frm[1],
                "subject": _dec_hdr(msg.get("Subject")), "date": _dec_hdr(msg.get("Date")),
                "message_id": msg.get("Message-ID", ""), "body": body, "images": imgs}
    finally:
        try:
            M.logout()
        except Exception:
            pass


# ── send ──────────────────────────────────────────────────────────────────────
def send_mail(acct: dict, to: str, subject: str, body: str, in_reply_to: str = "") -> dict:
    to_addr = parseaddr(to)[1]
    if not to_addr or "@" not in to_addr:
        raise ValueError("Invalid recipient.")
    frm = acct["email"]
    msg = MIMEMultipart()
    msg["From"] = formataddr((acct.get("display_name") or acct.get("label") or frm, frm))
    msg["To"] = to_addr
    msg["Subject"] = subject or "Re: your message"
    domain = frm.split("@")[-1] if "@" in frm else None
    msg["Message-ID"] = make_msgid(domain=domain)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.attach(MIMEText(body, "plain", "utf-8"))
    s = smtp_connect(acct)
    try:
        s.sendmail(frm, [to_addr], msg.as_string())
    finally:
        try:
            s.quit()
        except Exception:
            pass
    return {"ok": True, "to": to_addr, "from": frm}


# ── business profiles ─────────────────────────────────────────────────────────
def list_profiles():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM mail_profiles ORDER BY is_default DESC, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_profile(profile_id=None):
    """One profile (by id, else the default, else the first). None when empty."""
    conn = get_conn()
    row = None
    if profile_id:
        row = conn.execute("SELECT * FROM mail_profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM mail_profiles ORDER BY is_default DESC, id LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def save_profile(data: dict) -> int:
    fields = {
        "name": (data.get("name") or "Business profile").strip(),
        "business_type": (data.get("business_type") or "").strip(),
        "description": data.get("description") or "",
        "terms": data.get("terms") or "",
        "pricing": data.get("pricing") or "{}",
        "tone": data.get("tone") or "warm, concise, professional",
        "signature": data.get("signature") or "",
        "is_default": 1 if str(data.get("is_default", "0")) in ("1", "true", "True") else 0,
    }
    try:
        json.loads(fields["pricing"])
    except Exception:
        raise ValueError("pricing must be valid JSON")
    conn = get_conn()
    pid = data.get("id")
    if fields["is_default"]:
        conn.execute("UPDATE mail_profiles SET is_default=0")
    if pid:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE mail_profiles SET {sets}, updated_at=datetime('now') WHERE id=?",
                     (*fields.values(), pid))
    else:
        cur = conn.execute(
            f"INSERT INTO mail_profiles ({','.join(fields)}) VALUES ({','.join('?' * len(fields))})",
            list(fields.values()))
        pid = cur.lastrowid
    conn.commit()
    conn.close()
    return int(pid)


def delete_profile(profile_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM mail_profiles WHERE id=?", (profile_id,))
    conn.execute("UPDATE mail_accounts SET profile_id=NULL WHERE profile_id=?", (profile_id,))
    conn.execute("DELETE FROM mail_faq WHERE profile_id=?", (profile_id,))
    conn.commit()
    conn.close()


def pricing_text(profile: dict) -> str:
    """Human-readable pricing block from the profile's pricing JSON ('' when empty)."""
    try:
        p = json.loads(profile.get("pricing") or "{}")
    except Exception:
        p = {}
    lines = []
    cur = p.get("currency", "USD")
    sym = "$" if cur in ("USD", "") else f"{cur} "
    if p.get("hourly_rate"):
        lines.append(f"- Labor rate: {sym}{p['hourly_rate']}/hour"
                     + (f", {p['minimum_hours']}-hour minimum" if p.get("minimum_hours") else "")
                     + ".")
    if p.get("materials_policy"):
        lines.append(f"- Materials: {p['materials_policy']}.")
    if p.get("tax_note"):
        lines.append(f"- Tax: {p['tax_note']}.")
    return "\n".join(lines)


def render_reply_system(profile: dict, faq: dict = None, order_ctx: dict = None) -> str:
    """Fill the mail_quote template from the profile + optional FAQ / order context.
    Placeholder fill is replace-based (never KeyErrors on a user-edited template)."""
    try:
        from prompts import get_prompt
        tpl = get_prompt("mail_quote")
    except Exception:
        tpl = REPLY_SYSTEM
    pb = pricing_text(profile or {})
    fb = ""
    if faq:
        fb = ("This question matches a saved FAQ — base the reply on this exact answer "
              f"(reworded naturally):\nQ: {faq['question']}\nA: {faq['answer']}\n")
    ob = ""
    if order_ctx and any(order_ctx.get(k) for k in ("designs", "orders", "proposals")):
        ob = "Store context matched to this customer (use it to answer specifically):\n" + \
             context_text(order_ctx) + "\n"
    vals = {
        "business_name": (profile or {}).get("name", "the business"),
        "business_description": (profile or {}).get("description", ""),
        "terms": (profile or {}).get("terms", "") or "(no special terms)",
        "pricing_block": (f"Pricing model:\n{pb}\n" if pb else ""),
        "faq_block": fb,
        "order_block": ob,
        "tone": (profile or {}).get("tone", "warm, concise, professional"),
        "signature": (profile or {}).get("signature") or (profile or {}).get("name", ""),
    }
    out = tpl
    for k, v in vals.items():
        out = out.replace("{" + k + "}", str(v))
    return out


# ── FAQ matching (local, no LLM) ─────────────────────────────────────────────
_WORD_RE = re.compile(r"[a-z0-9']+")
_STOP = {"the", "a", "an", "is", "are", "do", "does", "you", "your", "i", "my", "we",
         "of", "to", "in", "on", "for", "and", "or", "it", "with", "what", "how",
         "when", "can", "will", "be", "have", "has"}


def _words(text):
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOP and len(w) > 1}


def match_faq(text: str, profile_id=None, min_overlap: float = 0.6):
    """Best FAQ for a message by meaningful-word overlap with the stored question.
    Returns (faq_row_dict, score) or (None, 0). Purely local — never costs a GPU call."""
    twords = _words(text)
    if not twords:
        return None, 0.0
    conn = get_conn()
    if profile_id:
        rows = conn.execute(
            "SELECT * FROM mail_faq WHERE enabled=1 AND (profile_id IS NULL OR profile_id=?)",
            (profile_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM mail_faq WHERE enabled=1").fetchall()
    conn.close()
    best, best_score = None, 0.0
    for r in rows:
        qwords = _words(r["question"])
        if not qwords:
            continue
        shared = qwords & twords
        score = len(shared) / len(qwords)
        if score > best_score and len(shared) >= 2:
            best, best_score = dict(r), score
    if best and best_score >= min_overlap:
        return best, best_score
    return None, 0.0


def faq_candidates(text: str, profile_id=None, limit: int = 5) -> list:
    """Loose shortlist (any overlap) for the classifier to confirm from."""
    twords = _words(text)
    conn = get_conn()
    if profile_id:
        rows = conn.execute(
            "SELECT * FROM mail_faq WHERE enabled=1 AND (profile_id IS NULL OR profile_id=?)",
            (profile_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM mail_faq WHERE enabled=1").fetchall()
    conn.close()
    scored = []
    for r in rows:
        qwords = _words(r["question"])
        if qwords and (qwords & twords):
            scored.append((len(qwords & twords) / len(qwords), dict(r)))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:limit]]


def bump_faq_hit(faq_id: int):
    try:
        conn = get_conn()
        conn.execute("UPDATE mail_faq SET hits=hits+1, updated_at=datetime('now') WHERE id=?",
                     (faq_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── order / store-data linking ────────────────────────────────────────────────
_orders_cache = {"ts": 0.0, "orders": []}


def _printify_orders() -> list:
    """Best-effort cached pull of recent live Printify orders (10-min TTL).
    Empty when Printify isn't configured or the API is unreachable — never raises."""
    if time.time() - _orders_cache["ts"] < 600:
        return _orders_cache["orders"]
    orders = []
    try:
        from deps import _get_printify
        client = _get_printify()
        r = client._get(f"/shops/{client.shop_id}/orders.json?limit=50")
        raw = r if isinstance(r, list) else (r or {}).get("data", [])
        for o in raw:
            addr = o.get("address_to") or {}
            orders.append({
                "id": str(o.get("id", "")),
                "status": o.get("status", ""),
                "created": o.get("created_at", ""),
                "buyer_name": f"{addr.get('first_name', '')} {addr.get('last_name', '')}".strip(),
                "buyer_email": (addr.get("email") or "").lower(),
                "total_cents": o.get("total_price", 0),
                "items": [li.get("metadata", {}).get("title", "")
                          for li in (o.get("line_items") or [])][:4],
            })
    except Exception:
        pass
    _orders_cache.update({"ts": time.time(), "orders": orders})
    return orders


def link_order_context(from_email: str, subject: str, body: str) -> dict:
    """Connect an incoming mail to store data: designs (listing/product ids or quoted
    title mentions), live Printify orders (order id or buyer email), and explicitly
    referenced proposals. Everything is defended; empty dict parts on no match."""
    out = {"designs": [], "orders": [], "proposals": []}
    text = f"{subject or ''}\n{body or ''}"
    ids = set(re.findall(r"\b(\d{5,})\b", text))
    sender = (from_email or "").strip().lower()
    try:
        conn = get_conn()
        rows = conn.execute(
            "SELECT id, prompt, product_type, status, etsy_listing_id, printify_id "
            "FROM designs WHERE etsy_listing_id IS NOT NULL OR printify_id IS NOT NULL").fetchall()
        # id match: any listing/product id mentioned in the mail
        for r in rows:
            if (r["etsy_listing_id"] and str(r["etsy_listing_id"]) in ids) or \
               (r["printify_id"] and str(r["printify_id"]) in ids):
                out["designs"].append({
                    "id": r["id"], "title": (r["prompt"] or "")[:80],
                    "product_type": r["product_type"], "status": r["status"],
                    "etsy_listing_id": r["etsy_listing_id"], "printify_id": r["printify_id"]})
        # quoted-title match: “the "cottagecore frog" shirt”
        if not out["designs"]:
            for m in re.findall(r'["“]([^"”]{6,70})["”]', text)[:3]:
                mw = _words(m)
                if len(mw) < 2:
                    continue
                for r in rows:
                    if len(mw & _words(r["prompt"])) >= max(2, len(mw) - 1):
                        out["designs"].append({
                            "id": r["id"], "title": (r["prompt"] or "")[:80],
                            "product_type": r["product_type"], "status": r["status"],
                            "etsy_listing_id": r["etsy_listing_id"],
                            "printify_id": r["printify_id"]})
                        break
        # explicit proposal references ("proposal #123")
        for m in re.findall(r"proposal\s*#?(\d{1,6})", text, re.I)[:3]:
            r = conn.execute("SELECT id, title, status FROM proposals WHERE id=?", (m,)).fetchone()
            if r:
                out["proposals"].append({"id": r["id"], "title": r["title"], "status": r["status"]})
        conn.close()
    except Exception:
        pass
    # live Printify orders — by order id in the text or the sender's email
    try:
        for o in _printify_orders():
            if (o["id"] and o["id"] in ids) or (sender and o["buyer_email"] == sender):
                out["orders"].append(o)
        out["orders"] = out["orders"][:3]
    except Exception:
        pass
    return out


def context_text(ctx: dict) -> str:
    """Short prompt-ready text block for a link_order_context() result."""
    lines = []
    for d in (ctx.get("designs") or [])[:3]:
        ref = d.get("etsy_listing_id") or d.get("printify_id") or d.get("id")
        lines.append(f"- Listing {ref}: \"{d.get('title')}\" ({d.get('product_type')}, "
                     f"status {d.get('status')})")
    for o in (ctx.get("orders") or [])[:3]:
        items = ", ".join(i for i in o.get("items", []) if i) or "items n/a"
        lines.append(f"- Order {o.get('id')}: status {o.get('status')}, placed "
                     f"{o.get('created', '?')[:10]}, buyer {o.get('buyer_name') or '?'} — {items}")
    for p in (ctx.get("proposals") or [])[:2]:
        lines.append(f"- Proposal #{p.get('id')}: \"{p.get('title')}\" ({p.get('status')})")
    return "\n".join(lines)

"""Auth core: password hashing/verification, first-run secret, and the login page.

Extracted verbatim from deps.py for modularity. Re-exported through
`from deps import *` (deps re-imports these single-underscore names explicitly, and
its bottom __all__ picks them up), so the public surface of deps is unchanged.

Dependency direction: auth_core -> db (get_conn) + stdlib only. It does NOT import
deps, so there is no import cycle.
"""

import hashlib as _hashlib
import secrets as _secrets
import hmac as _hmac

from db import get_conn


def _ensure_db():
    from db import init_db as _init
    _init()


def _get_or_create_secret() -> str:
    _ensure_db()
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key='_auth_secret'").fetchone()
    if row:
        conn.close()
        return row["value"]
    key = _secrets.token_hex(32)
    conn.execute("INSERT INTO settings (key,value) VALUES ('_auth_secret',?)", (key,))
    conn.commit()
    conn.close()
    return key


def _pw_hash(pw: str, salt: str = None, iterations: int = 200_000) -> str:
    """Salted PBKDF2-HMAC-SHA256. Format: pbkdf2$<iters>$<salt_hex>$<derived_hex>.
    (Was plain sha256 — legacy hashes still verify + auto-upgrade on next login.)"""
    if salt is None:
        salt = _secrets.token_hex(16)
    dk = _hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"pbkdf2${iterations}${salt}${dk.hex()}"


def _verify_pw(pw: str, stored: str) -> bool:
    """Constant-time verify against a pbkdf2 hash, or a legacy plain-sha256 one."""
    if stored.startswith("pbkdf2$"):
        try:
            _, iters, salt, _h = stored.split("$", 3)
            return _hmac.compare_digest(_pw_hash(pw, salt=salt, iterations=int(iters)), stored)
        except Exception:
            return False
    return _hmac.compare_digest(_hashlib.sha256(pw.encode("utf-8")).hexdigest(), stored)


def _get_stored_hash() -> str | None:
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key='_auth_password_hash'").fetchone()
    conn.close()
    return row["value"] if row else None


def _set_stored_hash(pw: str):
    h = _pw_hash(pw)   # always stores salted pbkdf2
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('_auth_password_hash',?)", (h,))
    conn.commit()
    conn.close()


def is_default_password() -> bool:
    """True while the install still runs on the first-run default password —
    drives the login-page hint and the post-login 'change it now' banner."""
    if _get_stored_hash() is None:
        return True
    try:
        conn = get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key='_auth_default_pw'").fetchone()
        conn.close()
        return bool(row and row["value"] == "1")
    except Exception:
        return False


def needs_setup() -> bool:
    """True while a fresh install should be routed to /setup instead of the dashboard:
    still on the default password AND the first-run wizard hasn't been finished or
    skipped. Mirrors the first-run banner condition (routers/auth.py's login_page),
    plus the sticky `setup_complete` setting so the wizard never resurfaces once
    the owner has been through it (or explicitly skipped it)."""
    if not is_default_password():
        return False
    try:
        conn = get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key='setup_complete'").fetchone()
        conn.close()
        return not (row and row["value"] == "1")
    except Exception:
        return False


def _flag_default_pw(on: bool):
    try:
        conn = get_conn()
        if on:
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('_auth_default_pw','1')")
        else:
            conn.execute("DELETE FROM settings WHERE key='_auth_default_pw'")
        conn.commit()
        conn.close()
    except Exception:
        pass


def _check_password(pw: str) -> bool:
    stored = _get_stored_hash()
    if stored is None:
        # First-run default password — accept it, but FLAG it so the UI walks the
        # user straight to changing it (it used to lock in silently, and anyone
        # typing the password they *wanted* just got "Incorrect password").
        if _hmac.compare_digest(_hashlib.sha256(pw.encode("utf-8")).hexdigest(),
                                _hashlib.sha256(b"store").hexdigest()):
            _set_stored_hash(pw)
            _flag_default_pw(True)
            return True
        return False
    if _verify_pw(pw, stored):
        if not stored.startswith("pbkdf2$"):   # transparent upgrade of legacy hashes
            _set_stored_hash(pw)
        return True
    return False


_LOGIN_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Store — Sign In</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:#0d0d12;color:#e2e2f0;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}
.card{background:#16161f;border:1px solid #2a2a3a;border-radius:16px;
  padding:40px;width:100%;max-width:360px;box-shadow:0 20px 60px rgba(0,0,0,.5);}
.logo{font-size:2.2rem;text-align:center;margin-bottom:8px;}
h1{text-align:center;font-size:1rem;color:#9090b0;margin-bottom:28px;font-weight:400;}
label{display:block;font-size:.78rem;font-weight:600;color:#9090b0;margin-bottom:6px;letter-spacing:.03em;text-transform:uppercase;}
input[type=password]{width:100%;padding:10px 14px;background:#0d0d12;
  border:1px solid #2a2a3a;border-radius:8px;color:#e2e2f0;font-size:.95rem;
  outline:none;transition:border-color .15s;}
input[type=password]:focus{border-color:#6c63ff;}
button{width:100%;margin-top:16px;padding:11px;background:#6c63ff;
  border:none;border-radius:8px;color:#fff;font-size:.95rem;font-weight:600;
  cursor:pointer;transition:background .15s;}
button:hover{background:#7c73ff;}
.err{background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.3);
  border-radius:8px;padding:10px 14px;font-size:.82rem;color:#f87171;
  margin-bottom:18px;}
.donate{display:block;text-align:center;margin-top:18px;font-size:.78rem;
  color:#7a7a95;text-decoration:none;transition:color .15s;}
.donate:hover{color:#e8a13c;}
</style>
</head>
<body>
<div class="card">
  <div class="logo">🛍️</div>
  <h1>Store Command Center</h1>
  {error_block}
  <form method="post">
    <label for="p">Password</label>
    <input type="password" id="p" name="password" autofocus placeholder="Enter password">
    <button type="submit">Sign In →</button>
  </form>
  {donate_block}
</div>
</body></html>"""

_SETUP_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Store — First-Run Setup</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:#0d0d12;color:#e2e2f0;padding:32px 16px;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}
.card{background:#16161f;border:1px solid #2a2a3a;border-radius:16px;
  padding:36px;width:100%;max-width:560px;box-shadow:0 20px 60px rgba(0,0,0,.5);}
.logo{font-size:2.2rem;text-align:center;margin-bottom:4px;}
h1{text-align:center;font-size:1.05rem;color:#e2e2f0;margin-bottom:2px;font-weight:600;}
.sub{text-align:center;font-size:.8rem;color:#9090b0;margin-bottom:22px;line-height:1.4;}
.steps{display:flex;gap:6px;justify-content:center;margin-bottom:24px;}
.step-dot{width:26px;height:4px;border-radius:2px;background:#2a2a3a;transition:background .2s;}
.step-dot.active{background:#6c63ff;}
.step-dot.done{background:#3ecf8e;}
label{display:block;font-size:.78rem;font-weight:600;color:#9090b0;margin:14px 0 6px;letter-spacing:.03em;text-transform:uppercase;}
input[type=password],input[type=text]{width:100%;padding:10px 14px;background:#0d0d12;
  border:1px solid #2a2a3a;border-radius:8px;color:#e2e2f0;font-size:.95rem;outline:none;transition:border-color .15s;}
input:focus{border-color:#6c63ff;}
.hint{font-size:.76rem;color:#7a7a95;margin-top:6px;line-height:1.45;}
.opt{display:flex;gap:12px;align-items:flex-start;background:#0d0d12;border:1px solid #2a2a3a;
  border-radius:10px;padding:14px;margin-top:10px;cursor:pointer;transition:border-color .15s;}
.opt:hover{border-color:#4a4a6a;}
.opt.sel{border-color:#6c63ff;background:rgba(108,99,255,.08);}
.opt input{margin-top:3px;}
.opt .t{font-weight:600;font-size:.9rem;}
.opt .d{font-size:.78rem;color:#9090b0;margin-top:4px;line-height:1.4;}
.toggle-row{display:flex;justify-content:space-between;align-items:center;gap:12px;
  background:#0d0d12;border:1px solid #2a2a3a;border-radius:10px;padding:12px 14px;margin-top:10px;}
.toggle-row .t{font-weight:600;font-size:.85rem;}
.toggle-row .d{font-size:.74rem;color:#9090b0;margin-top:2px;}
.switch{position:relative;width:42px;height:24px;flex-shrink:0;}
.switch input{opacity:0;width:0;height:0;}
.slider{position:absolute;inset:0;background:#2a2a3a;border-radius:24px;cursor:pointer;transition:background .15s;}
.slider::before{content:'';position:absolute;width:18px;height:18px;left:3px;top:3px;background:#e2e2f0;border-radius:50%;transition:transform .15s;}
input:checked + .slider{background:#6c63ff;}
input:checked + .slider::before{transform:translateX(18px);}
table.health{width:100%;border-collapse:collapse;margin-top:10px;font-size:.8rem;}
table.health td{padding:6px 8px;border-bottom:1px solid #2a2a3a;}
.pill{display:inline-block;padding:2px 8px;border-radius:20px;font-size:.68rem;font-weight:700;text-transform:uppercase;}
.pill.up{background:rgba(62,207,142,.15);color:#3ecf8e;}
.pill.down{background:rgba(239,68,68,.15);color:#f87171;}
.pill.degraded{background:rgba(232,161,60,.15);color:#e8a13c;}
.pill.unknown{background:rgba(144,144,176,.15);color:#9090b0;}
.actions{display:flex;justify-content:space-between;align-items:center;margin-top:26px;gap:10px;}
button{padding:11px 18px;background:#6c63ff;border:none;border-radius:8px;color:#fff;
  font-size:.9rem;font-weight:600;cursor:pointer;transition:background .15s;}
button:hover{background:#7c73ff;}
button.ghost{background:transparent;border:1px solid #2a2a3a;color:#9090b0;}
button.ghost:hover{background:#1c1c28;color:#e2e2f0;}
button:disabled{opacity:.5;cursor:not-allowed;}
.skip{position:fixed;top:18px;right:22px;font-size:.78rem;color:#7a7a95;text-decoration:none;
  border:1px solid #2a2a3a;padding:6px 12px;border-radius:20px;cursor:pointer;background:#16161f;}
.skip:hover{color:#e2e2f0;border-color:#4a4a6a;}
.err{background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.3);border-radius:8px;
  padding:10px 14px;font-size:.82rem;color:#f87171;margin-top:14px;}
.ok{background:rgba(62,207,142,.12);border:1px solid rgba(62,207,142,.3);border-radius:8px;
  padding:10px 14px;font-size:.82rem;color:#3ecf8e;margin-top:14px;}
</style>
</head>
<body>
<a class="skip" id="skip-link" title="Set defaults and go to the dashboard">Skip for now &rarr;</a>
<div class="card">
  <div class="logo">🛍️</div>
  <h1>Welcome to Store Command Center</h1>
  <div class="sub">A few quick steps to get set up — everything here is optional and can be changed later in Settings.</div>
  <div class="steps" id="wizard-steps"></div>
  <div id="wizard-root"></div>
</div>
<script>window.__STORE_BASE__ = "__STORE_BASE__";</script>
<script src="__STORE_BASE__/static/js/setup-wizard.js"></script>
</body></html>"""

_AUTH_BYPASS = {"/login", "/logout"}

"""auth routes."""
import re as _re, glob as _glob, os as _os
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Form, UploadFile, File
from deps import *
from services import *

router = APIRouter()


def _asset_ver():
    """Newest mtime across static JS/CSS — bumps whenever any asset changes, so the
    browser refetches only what actually changed (no more hard-refresh needed)."""
    try:
        files = _glob.glob(str(BASE / "static/js/*.js")) + _glob.glob(str(BASE / "static/css/*.css"))
        return int(max(_os.path.getmtime(f) for f in files)) if files else 0
    except Exception:
        return 0


@router.get("/", include_in_schema=False)
async def dashboard():
    # FIRST-RUN WIZARD GATE: a fresh install (still on the default password, and the
    # wizard hasn't been finished or explicitly skipped) lands here straight out of
    # login — send it to /setup instead of the dashboard. needs_setup() is sticky-off
    # once setup_complete=1 is written (Finish or Skip), so this never nags again.
    from auth_core import needs_setup
    if needs_setup():
        return RedirectResponse(url=f"{STORE_BASE}/setup", status_code=303)
    # Serve index.html, injecting the configured base path so the app works under
    # any reverse-proxy prefix (STORE_BASE) or at root ("") — not just "/store".
    html = (BASE / "static/index.html").read_text()
    if STORE_BASE != "/store":
        html = (html
                .replace("'/store'", f"'{STORE_BASE}'")       # const API
                .replace("/store/logout", f"{STORE_BASE}/logout")
                .replace("/store/static/", f"{STORE_BASE}/static/"))
    if APP_NAME != "Store Command Center":
        html = html.replace("Store Command Center", APP_NAME)
    # cache-bust: stamp ?v=<newest-asset-mtime> on every static js/css src so browsers
    # always pull the current file after an update (index.html itself is no-cache below)
    ver = _asset_ver()
    html = _re.sub(r'(/static/(?:js|css)/[\w./\-]+\.(?:js|css))(["\'])', rf'\1?v={ver}\2', html)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})

@router.get("/setup", include_in_schema=False)
async def setup_page():
    """First-run setup wizard shell. static/js/setup-wizard.js drives the actual steps
    (password, topology, GPU node, subsystem opt-ins, health check) over existing
    endpoints; this route just serves the styled page.

    Deliberately ALWAYS reachable — unlike the dashboard, this route does not gate on
    needs_setup(), so a completed/skipped install can still revisit /setup on purpose,
    and the wizard itself is never the thing standing between the owner and Settings."""
    from auth_core import _SETUP_HTML
    html = (_SETUP_HTML.replace("__STORE_BASE__", STORE_BASE)
                       .replace("Store Command Center", APP_NAME))
    return HTMLResponse(html)


def _login_html(error_block: str) -> str:
    """Render the sign-in page. Includes the optional pre-auth "☕ Support this
    project" link — the same public {enabled, url} pair the Money tab reads from
    GET /api/donate/config (routers/donate.py). Shown only when donate_enabled +
    a Buy Me a Coffee handle are set (Settings → Integrations → Donations), and
    it exposes nothing but that public BMC URL."""
    donate = ""
    try:
        import html as _html
        from routers.donate import donate_config
        d = donate_config()
        if d.get("enabled") and d.get("url"):
            donate = (f'<a class="donate" href="{_html.escape(d["url"], quote=True)}" '
                      'target="_blank" rel="noopener">&#9749; Support this project</a>')
    except Exception:
        pass   # a settings hiccup must never break the login page
    return (_LOGIN_HTML.replace("{error_block}", error_block)
                       .replace("{donate_block}", donate)
                       .replace("Store Command Center", APP_NAME))


@router.get("/login", include_in_schema=False)
async def login_page(request: Request, error: str = ""):
    block = f'<div class="err">{error}</div>' if error else ""
    # SECURE-COOKIE TRAP: the session cookie is `Secure`, so over plain HTTP from
    # another machine the browser silently drops it and login "does nothing".
    # Warn up front instead of letting the user fail mysteriously.
    try:
        # Detect real HTTPS through the whole proxy chain, not just X-Forwarded-Proto
        # (nginx-proxy-manager / Cloudflare don't always forward it). Cloudflare sends
        # `CF-Visitor: {"scheme":"https"}`; also honour Forwarded and the direct scheme.
        fwd_proto = (request.headers.get("x-forwarded-proto") or "").lower()
        cf_visitor = (request.headers.get("cf-visitor") or "").replace(" ", "").lower()
        forwarded = (request.headers.get("forwarded") or "").lower()
        is_https = (
            fwd_proto == "https"
            or request.url.scheme == "https"
            or '"scheme":"https"' in cf_visitor
            or "proto=https" in forwarded
        )
        host = (request.url.hostname or "").lower()
        if (not is_https) and host not in ("localhost", "127.0.0.1", "::1"):
            block += ('<div class="err" style="background:#3a2f14;border-color:#6a5a2a;color:#e8d49a">'
                      '⚠️ <b>Plain HTTP from another machine</b> — the secure session cookie can\'t be set, '
                      'so signing in will silently fail.<br>Use HTTPS (see <code>deploy/caddy/</code> for a '
                      '2-line reverse proxy), an SSH tunnel, or open via <code>http://localhost:8787</code> '
                      'on the server itself.</div>')
    except Exception:
        pass
    # FIRST-RUN HELPER: a fresh install has no way to know the default password —
    # tell them, and promise the change prompt. Only shown while the default is
    # in effect (no info leak once a real password is set).
    try:
        from auth_core import is_default_password
        if is_default_password():
            block += ('<div class="err" style="background:#173a2a;border-color:#2a6a4a;color:#9fe0b8">'
                      '🔑 <b>First run</b> — sign in with the default password <code>store</code>.<br>'
                      "You'll be prompted to change it right after you're in.</div>")
    except Exception:
        pass
    return HTMLResponse(_login_html(block))


@router.get("/api/auth/status")
async def auth_status():
    """Post-login helper: is this install still on the default password?"""
    from auth_core import is_default_password
    return {"default_password": is_default_password()}

# ── Brute-force limiter: per-IP failed-login throttle ────────────────────────
import time as _time
import asyncio as _asyncio
_login_fails: dict = {}          # ip -> [failure timestamps]
_LOGIN_WINDOW = 300              # sliding window (5 min)
_LOGIN_MAX    = 8                # failures in the window before lockout


def _client_ip(request: Request) -> str:
    # Rate-limit key. Cloudflare sets CF-Connecting-IP to the TRUE client IP and overwrites
    # any client-supplied value, and the app is only reachable THROUGH Cloudflare — so it
    # can't be spoofed. NEVER key the limiter on the client-controllable leftmost
    # X-Forwarded-For (that let an attacker rotate fake IPs to dodge the lockout entirely).
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf
    return request.client.host if request.client else "unknown"


@router.post("/login", include_in_schema=False)
async def login_post(request: Request, password: str = Form(...)):
    ip = _client_ip(request)
    now = _time.time()
    fails = [t for t in _login_fails.get(ip, []) if now - t < _LOGIN_WINDOW]
    if len(fails) >= _LOGIN_MAX:
        wait_min = int((_LOGIN_WINDOW - (now - fails[0])) // 60) + 1
        logger.warning("Login lockout for %s (%d failures)", ip, len(fails))
        block = f'<div class="err">🔒 Too many failed attempts. Try again in ~{wait_min} min.</div>'
        return HTMLResponse(_login_html(block), status_code=429)
    if _check_password(password):
        _login_fails.pop(ip, None)
        request.session["authenticated"] = True
        return RedirectResponse(url=f"{STORE_BASE}/", status_code=303)
    fails.append(now)
    _login_fails[ip] = fails
    await _asyncio.sleep(0.6)   # slow down guessing (async — doesn't block the server)
    left = _LOGIN_MAX - len(fails)
    tail = f' ({left} attempt{"s" if left != 1 else ""} left)' if left <= 3 else ''
    block = f'<div class="err">❌ Incorrect password. Try again.{tail}</div>'
    return HTMLResponse(_login_html(block), status_code=401)

@router.get("/logout", include_in_schema=False)
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url=f"{STORE_BASE}/login", status_code=303)

class PasswordChange(BaseModel):
    current: str
    new_password: str

@router.post("/api/auth/change-password")
async def change_password(body: PasswordChange):
    if not _check_password(body.current):
        raise HTTPException(400, "Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    _set_stored_hash(body.new_password)
    from auth_core import _flag_default_pw
    _flag_default_pw(False)                 # a chosen password ends the first-run state
    return {"ok": True}

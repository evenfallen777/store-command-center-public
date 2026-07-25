"""Public world snapshot — an OUTBOUND-ONLY push of a rendered picture of The Company.

────────────────────────────────────────────────────────────────────────────────
SECURITY MODEL — read this before changing anything in this file
────────────────────────────────────────────────────────────────────────────────
The Store and the simulated world run on a private machine. example.com is a
public site. The ONLY thing that crosses that boundary is this module, and it
crosses it in one direction: we render a PNG locally, build a small allow-listed
stats dict locally, and POST both outward to WordPress. Consequences:

  • No inbound path is opened. Nothing here listens, proxies, tunnels or forwards.
  • The public page never talks to this box. It reads the image + JSON from
    WordPress's own public REST API (same origin as the page).
  • No controls exist. The payload is display data only — there is no command
    channel, and the public page has nothing it could call even if it wanted to.

Three independent guards decide whether a push may happen at all:

  1. TOGGLE   `world_public_snapshot` in `settings`. Defaults OFF. Nothing is ever
              pushed until the owner turns it on.
  2. GATE     `gate_reason()` — if the Private Studio (NSFW) feature is enabled in
              ANY way, we skip the push and log why. The world can hang generated
              art on its walls, so "master toggle on at all" is treated as
              disqualifying, not just "world mode on".
  3. LEAK     `scan_payload()` — a final allow-list + regex sweep over the exact
              bytes about to leave. Anything that smells like an address, a path,
              a port, a version or a host aborts the push.

The stats blob is built by ALLOW-LIST (`build_stats`): every field is named,
coerced to a known type and length-capped. Nothing is passed through from the
world state wholesale. Free text (in-world event lines) is scrubbed and then
dropped entirely if it still trips the leak detector.

Rendering is pure CPU headless Chromium — it does NOT touch the GPU box, so it
does not ride the unified GPU queue. It runs off the request path in a daemon
thread (see `push_async`).
"""
import base64
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger("worldsnap")

# ── settings keys ────────────────────────────────────────────────────────────
TOGGLE_KEY = "world_public_snapshot"            # master, default OFF
INTERVAL_KEY = "world_public_snapshot_interval"  # minutes
LAST_TS_KEY = "world_public_snapshot_last_ts"
LAST_RESULT_KEY = "world_public_snapshot_last"

DEFAULT_INTERVAL_MIN = 20
MIN_INTERVAL_MIN = 10
KEEP_SNAPSHOTS = 6                # how many we keep for the "then vs now" strip

DATA_SLUG = "world-live-data"     # public WP page that carries the base64 JSON
DATA_TITLE = "World Live Data"
DATA_OPEN, DATA_CLOSE = "<!--WORLDDATA-->", "<!--/WORLDDATA-->"

_LOCK = threading.Lock()
_RUNNING = False


# ─────────────────────────────────────────────────────────────────────────────
# settings helpers (kept local so this module imports cleanly in tests)
# ─────────────────────────────────────────────────────────────────────────────
def _setting(key: str, default: str = "") -> str:
    try:
        from deps import get_setting
        v = get_setting(key, None)
        return default if v in (None, "") else str(v)
    except Exception:
        return default


def _set_setting(key: str, value: str):
    try:
        from db import get_conn
        c = get_conn()
        c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(value)))
        c.commit()
        c.close()
    except Exception as e:
        log.warning("could not persist %s: %s", key, e)


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "on", "yes")


def enabled() -> bool:
    """Master toggle. Defaults OFF — nothing leaves the box until it's turned on."""
    return _truthy(_setting(TOGGLE_KEY, ""))


def interval_sec() -> int:
    try:
        m = int(float(_setting(INTERVAL_KEY, str(DEFAULT_INTERVAL_MIN))))
    except Exception:
        m = DEFAULT_INTERVAL_MIN
    return max(MIN_INTERVAL_MIN, m) * 60


# ─────────────────────────────────────────────────────────────────────────────
# GUARD 2 — gated / non-public content
# ─────────────────────────────────────────────────────────────────────────────
def gate_reason():
    """Return a human reason to SKIP the push, or None if the world is safe to show.

    Deliberately strict: the world renders generated art onto in-world walls, so if
    the Private Studio exists in any form we do not publish a picture of it. Any of
    the three NSFW toggles being on is disqualifying — not just the world one.
    """
    try:
        import nsfw
    except Exception:
        return None   # feature not installed → nothing gated to worry about
    try:
        if nsfw.world_active():
            return "private studio world mode is active"
        if nsfw.visible():
            return "private studio display is on"
        if nsfw.enabled():
            return "private studio master toggle is on"
    except Exception as e:
        # Fail CLOSED: if we cannot prove the world is clean, we do not publish it.
        return f"could not verify private-studio state ({e})"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# GUARD 3 — leak detection / scrubbing
# ─────────────────────────────────────────────────────────────────────────────
_LEAK_RULES = [
    ("ipv4", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")),
    ("unix_path", re.compile(r"/(?:home|usr|var|etc|opt|srv|root|mnt|media|tmp|proc|sys)(?:/[\w.\-]+)+")),
    ("win_path", re.compile(r"[A-Za-z]:\\[\w.\\\-]+")),
    ("venv_path", re.compile(r"\b(?:env|venv|site-packages|node_modules)/[\w./\-]+")),
    ("internal_host", re.compile(r"\b[\w\-]+\.(?:local|lan|internal|home|arpa|localdomain)\b", re.I)),
    ("localhost", re.compile(r"\blocalhost\b", re.I)),
    ("url", re.compile(r"\bhttps?://\S+", re.I)),
    ("port", re.compile(r"(?:\bport\s*|[\w\]\)]:)(?:80|443|\d{4,5})\b", re.I)),
    ("version", re.compile(r"\b(?:python|node|nginx|uvicorn|cuda|torch|sqlite|php)[ /]?v?\d+(?:\.\d+)+", re.I)),
    ("semver", re.compile(r"\bv?\d+\.\d+\.\d+\b")),
    ("gpu", re.compile(r"\b(?:nvidia|geforce|rtx|gtx|radeon|cuda)\b", re.I)),
    ("email", re.compile(r"\b[\w.\-]+@[\w.\-]+\.\w+\b")),
    ("traceback", re.compile(r"\b(?:Traceback|File \"|line \d+, in |\.py\b)")),
    ("user", re.compile(r"\buser\b", re.I)),
    # Real-operations vocabulary. The event allow-list below is the primary control;
    # this is a second net in case a new event kind starts emitting owner ops text.
    ("ops_talk", re.compile(
        r"\b(?:pending your approval|awaiting approval|company fund|store upgrade|"
        r"dev crew|swarm job|api key|password|token|webhook|checkout|payout|"
        r"conversion rate|abandoned cart|profit margin)\b", re.I)),
]

# ── event kinds that are safe to publish ─────────────────────────────────────
# ALLOW-LIST, not a blocklist: every kind here is template-generated, purely
# in-world flavor. Everything else is excluded, notably:
#   security  → real security-AI findings about the private network
#   thought / opinion / meeting / town / system → free LLM text that regularly
#             contains the owner's actual shop strategy and spend approvals
#   phase / raid → templated, but they narrate REAL subsystem health and defense
#             posture, which is exactly what must not be public
SAFE_EVENT_KINDS = {"job_done", "incident", "season", "want", "achievement", "build", "level_up"}


def find_leaks(text: str):
    """Return the names of every leak rule that matches `text`."""
    if not text:
        return []
    return [name for name, rx in _LEAK_RULES if rx.search(text)]


def scrub(text, limit: int = 240) -> str:
    """Redact anything infra-shaped out of a free-text string and cap its length."""
    if not text:
        return ""
    s = str(text)
    for _name, rx in _LEAK_RULES:
        s = rx.sub("…", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def scan_payload(obj) -> list:
    """Final gate: walk the whole outbound payload and report any leak hits.

    Returns a list of "path: rule" strings — non-empty means DO NOT PUSH.
    """
    hits = []

    def walk(o, path="$"):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
        elif isinstance(o, str):
            for rule in find_leaks(o):
                hits.append(f"{path}: {rule}")

    walk(obj)
    return hits


# ─────────────────────────────────────────────────────────────────────────────
# stats — built by ALLOW-LIST from the world state
# ─────────────────────────────────────────────────────────────────────────────
def _int(v, default=0):
    try:
        return int(float(v))
    except Exception:
        return default


def _word(v, limit=32):
    """A short, scrubbed, single-token-ish label."""
    return scrub(v, limit)


def build_stats(state: dict, image_url: str = "", captured_at: str = "") -> dict:
    """Turn the raw world state into the sanitized public blob.

    ALLOW-LIST ONLY. Every field below is named explicitly and coerced. Nothing is
    copied through from `state` wholesale, so a new field appearing in the sim can
    never silently become public. Notably EXCLUDED on purpose:

      • state["security"]   — carries real health output and stack traces
      • state["activity"]   — internal subsystem counters
      • agent `key`/`kind`/`dept`/`sprite_path` — tooling + filesystem identifiers
      • agent `mood`/`goal` and governance directives — free LLM text, highest risk
      • treasury / fund figures — owner operations, not in-world flavor
    """
    state = state or {}
    orch = state.get("orchestra") or {}
    comp = state.get("company") or {}
    tech = comp.get("tech") or {}
    agents = state.get("agents") or []

    total_levels = 0
    for a in agents:
        for sk in (a.get("skills") or {}).values():
            total_levels += _int((sk or {}).get("level"))

    # in-world specialist flavor: display name + skill + level ONLY
    specialists = []
    for skill, s in sorted((comp.get("specialists") or {}).items()):
        if not isinstance(s, dict):
            continue
        specialists.append({
            "skill": _word(skill, 24),
            "name": _word(s.get("name"), 24),
            "level": _int(s.get("level")),
        })
    specialists = sorted(specialists, key=lambda x: -x["level"])[:6]

    # in-world event lines: allow-list the KIND first, then scrub, then DROP any
    # line that still trips the detector. Three layers, deliberately.
    events = []
    for e in (state.get("events") or [])[:40]:
        if not isinstance(e, dict):
            continue
        if str(e.get("kind") or "") not in SAFE_EVENT_KINDS:
            continue
        raw = str(e.get("text") or "")
        # Check the RAW line first: a suspicious event is DROPPED, never redacted.
        # (Redacting would publish a half-line that still betrays its shape.)
        if not raw or find_leaks(raw):
            continue
        txt = scrub(raw, 160)
        if not txt or find_leaks(txt):
            continue
        if txt.lower() in {e.lower() for e in events}:
            continue          # the sim repeats identical lines; show each once
        events.append(txt)
        if len(events) >= 6:
            break

    achievements = []
    for a in (state.get("achievements") or [])[:4]:
        if isinstance(a, dict):
            lbl = scrub(a.get("label"), 48)
            if lbl and not find_leaks(lbl):
                achievements.append(lbl)

    return {
        "captured_at": captured_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "image_url": image_url,
        "population": _int(comp.get("pop"), len(agents)),
        "day": _int(orch.get("day")),
        "season": _word(orch.get("season"), 24),
        "season_emoji": _word(orch.get("emoji"), 8),
        "festival": scrub(orch.get("festival"), 80),
        "tech_age": _word(tech.get("tier_name") or tech.get("tier"), 32),
        "tech_emoji": _word(tech.get("emoji"), 8),
        "total_levels": total_levels,
        "top_level": _int(comp.get("max_level")),
        "jobs_done": _int(comp.get("total_jobs")),
        "buildings": _int(comp.get("props_done")),
        "town_meetings": _int(comp.get("meetings")),
        "specialists": specialists,
        "achievements": achievements,
        "events": events,
    }


def fetch_state(base: str = "http://127.0.0.1:8787") -> dict:
    """Read the world state over the loopback API (localhost bypasses auth)."""
    with urllib.request.urlopen(base + "/api/world/state", timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# render — pure CPU headless Chromium, no GPU, off the request path
# ─────────────────────────────────────────────────────────────────────────────
def _login_cookie(base: str, password: str):
    """POST the store password and return the (name, value) session cookie."""
    data = urllib.parse.urlencode({"password": password}).encode()
    req = urllib.request.Request(base + "/login", data=data)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(req, timeout=30)
    except urllib.error.HTTPError as e:
        resp = e
    raw = resp.headers.get("Set-Cookie") or ""
    if "=" not in raw:
        raise RuntimeError("store login did not return a session cookie")
    name, value = raw.split(";")[0].split("=", 1)
    return name, value


# The world canvas is overlaid by DOM HUD panels (feed, agent inspector, town hall,
# toolbar…). Those panels render the operator's REAL text — boss directives, spend
# approvals, security findings — and an element screenshot clips them in, which would
# publish as PIXELS exactly what `build_stats` refuses to publish as text.
# So: hide every overlay before the shot, then verify none survived.
_HIDE_OVERLAYS_CSS = """
.whud-bar, .whud-panel, .whud-btn, .whud-pill,
#world-hudbar, #world-editbar, #world-modal, #world-loading,
#world-feed, #world-detail, #world-townhall, #world-activity,
#world-god-badge, #world-clock, #world-season { display: none !important; }
"""

# Runs in the page: report any VISIBLE element that overlaps the canvas and carries
# its own text. Anything here would be baked into the published image.
_OVERLAP_PROBE = """
() => {
  const c = document.getElementById('world-canvas');
  if (!c) return ['no canvas'];
  const r = c.getBoundingClientRect();
  const bad = [];
  document.querySelectorAll('body *').forEach(el => {
    if (el === c || el.contains(c)) return;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return;
    const b = el.getBoundingClientRect();
    if (!b.width || !b.height) return;
    if (b.right <= r.left || b.left >= r.right || b.bottom <= r.top || b.top >= r.bottom) return;
    let own = '';
    el.childNodes.forEach(n => { if (n.nodeType === 3) own += n.textContent; });
    own = own.trim();
    if (own) bad.push((el.id || el.className || el.tagName) + ': ' + own.slice(0, 60));
  });
  return bad;
}
"""


def render_world_png(base: str = "http://127.0.0.1:8787", password: str = "",
                     settle_sec: float = 9.0) -> bytes:
    """Render the live world canvas to PNG bytes with headless Chromium.

    Pure CPU: this drives a local browser, it never queues work on the GPU box, so
    it deliberately does not use the unified GPU queue.
    """
    from playwright.sync_api import sync_playwright

    password = password or _setting("store_render_password", "")
    name, value = _login_cookie(base, password)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
            ctx.add_cookies([{
                "name": name, "value": value, "domain": "127.0.0.1",
                "path": "/", "httpOnly": True, "secure": False,
            }])
            page = ctx.new_page()

            # The SPA hard-codes a "/store" prefix that only the reverse proxy strips.
            # Rewrite it back to root so the app serves itself over plain loopback.
            def _rewrite(route):
                url = route.request.url
                fixed = url.replace("127.0.0.1:8787/store/", "127.0.0.1:8787/", 1)
                if fixed != url:
                    route.continue_(url=fixed)
                else:
                    route.continue_()

            page.route("**/*", _rewrite)
            page.goto(base + "/", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2500)
            page.evaluate("window.switchView && window.switchView('world')")
            page.wait_for_selector("#world-canvas", timeout=60000)
            page.wait_for_timeout(int(settle_sec * 1000))

            # Strip the HUD, then let a frame settle so nothing re-opens a panel.
            page.add_style_tag(content=_HIDE_OVERLAYS_CSS)
            page.wait_for_timeout(1200)

            # FAIL CLOSED: if any text overlay still covers the canvas, abort rather
            # than publish an image with the operator's real text burned into it.
            overlaps = page.evaluate(_OVERLAP_PROBE)
            if overlaps:
                raise RuntimeError(
                    f"refusing to render: {len(overlaps)} text overlay(s) still cover "
                    f"the world canvas: {overlaps[:3]}")

            # Frame the WHOLE map, centered. The world view's default camera is zoomed
            # in on HQ (_worldZoomHome zooms ~2.1x) or a restored pan, so a raw shot
            # publishes only a corner (~1/4). WM.fit() recomputes the camera to fit the
            # full map to the canvas — same as the app's "recenter" — then we wait a
            # frame for the RAF loop to repaint before capturing.
            page.evaluate("""
                () => {
                  const cv = document.getElementById('world-canvas');
                  if (cv && window.WM && WM.fit) WM.fit(cv._cssW || cv.clientWidth, cv._cssH || cv.clientHeight);
                }
            """)
            page.wait_for_timeout(1200)

            el = page.query_selector("#world-canvas")
            if el is None:
                raise RuntimeError("world canvas never appeared")
            return el.screenshot(type="png")
        finally:
            browser.close()


# ─────────────────────────────────────────────────────────────────────────────
# outbound push to WordPress
# ─────────────────────────────────────────────────────────────────────────────
def _mcp_client():
    from deps import get_setting
    import config
    from wc_client import WPMcpClient
    ep = get_setting("wp_mcp_url", "") or getattr(config, "WP_MCP_URL", "")
    tok = get_setting("wp_mcp_token", "") or getattr(config, "WP_MCP_TOKEN", "")
    if not (ep and tok):
        raise RuntimeError("WordPress MCP is not configured (wp_mcp_url / wp_mcp_token)")
    return WPMcpClient(ep, tok)


def _encode_data(payload: dict) -> str:
    """base64 the JSON so WordPress's text filters can't mangle quotes/entities."""
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return DATA_OPEN + base64.b64encode(raw).decode("ascii") + DATA_CLOSE


def decode_data(content: str) -> dict:
    """Inverse of `_encode_data` — used by tests and by the public page's JS."""
    m = re.search(re.escape(DATA_OPEN) + r"([A-Za-z0-9+/=\s]+)" + re.escape(DATA_CLOSE), content or "")
    if not m:
        return {}
    return json.loads(base64.b64decode(re.sub(r"\s+", "", m.group(1))).decode("utf-8"))


def public_site_base() -> str:
    """The configured PUBLIC WordPress origin (e.g. https://example.com)."""
    try:
        from deps import get_setting
        import config
        return (get_setting("wp_url", "") or getattr(config, "WP_URL", "") or "").rstrip("/")
    except Exception:
        return ""


def check_image_url(url: str) -> bool:
    """The one URL allowed in the payload: an https media link on the PUBLIC site.

    The leak scanner rejects URLs wholesale (rightly — a URL is the classic way an
    internal host escapes). The media link is the deliberate exception, so it is
    validated explicitly instead of being waved through the generic rule.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return False
    if u.scheme != "https" or not u.hostname:
        return False
    if find_leaks(u.hostname):        # no IPs, .local, localhost, ports…
        return False
    if u.port:
        return False
    base = public_site_base()
    if base:
        allowed = urllib.parse.urlparse(base).hostname or ""
        if u.hostname != allowed and not u.hostname.endswith("." + allowed):
            return False
    return True


def _publish(mcp, png: bytes, stats: dict, history: list) -> dict:
    """Upload the image, then rewrite the public data page. Outbound HTTP only."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    att = mcp.upload_media_base64(
        filename=f"world-{stamp}.png",
        file_bytes=png,
        title=f"The Company — {stats.get('captured_at', '')}",
        alt_text="A rendered snapshot of the simulated company world",
    )
    url = att.get("source_url") or att.get("url") or ""
    media_id = att.get("id")
    stats["image_url"] = url

    entry = {"url": url, "id": media_id, "captured_at": stats["captured_at"]}
    history = ([entry] + [h for h in (history or []) if h.get("url") != url])[:KEEP_SNAPSHOTS]

    payload = {"current": stats, "history": history,
               "note": "Display-only snapshot. No controls, no live connection."}

    # Media links are the one legitimate URL in the payload: validate them against
    # the public site explicitly, then exclude them from the generic leak sweep.
    for u in [stats.get("image_url")] + [h.get("url") for h in history]:
        if not check_image_url(u):
            raise RuntimeError(f"refusing to publish a non-public media URL: {str(u)[:80]}")

    scanned = {**stats}
    scanned.pop("image_url", None)
    leaks = scan_payload({"current": scanned, "note": payload["note"]})
    if leaks:
        raise RuntimeError(f"leak gate tripped after render: {leaks[:5]}")

    content = _encode_data(payload)
    page = mcp.find_page_by_slug(DATA_SLUG)
    if page:
        mcp.update_page(int(page["id"]), content)
        page_id = int(page["id"])
    else:
        created = mcp.create_page(DATA_TITLE, content, slug=DATA_SLUG, status="publish")
        page_id = int(created.get("id") or 0)

    return {"media_id": media_id, "image_url": url, "page_id": page_id, "history": history}


def _load_history(mcp) -> list:
    try:
        page = mcp.find_page_by_slug(DATA_SLUG)
        if not page:
            return []
        pid = int(page["id"])
        full = mcp._tool("wp_get_page", {"page_id": pid})
        return (decode_data(full.get("content") or "") or {}).get("history") or []
    except Exception:
        return []


def _prune(mcp, history: list):
    """Delete media beyond the retention window so the library doesn't grow forever."""
    for old in (history or [])[KEEP_SNAPSHOTS:]:
        try:
            mcp._tool("wp_delete_media", {"media_id": int(old["id"]), "force": True})
        except Exception as e:
            log.warning("could not prune old snapshot: %s", e)


def push_now(force: bool = False, password: str = "", state: dict = None,
             png: bytes = None) -> dict:
    """Run one full snapshot → push cycle. Returns a result dict; never raises.

    `state`/`png` exist so tests can drive the pipeline without a browser.
    """
    started = time.time()

    # GUARD 1 — the toggle
    if not force and not enabled():
        return _record({"pushed": False, "reason": "toggle off", "skipped": True})

    # GUARD 2 — gated content (checked before we spend time rendering)
    gated = gate_reason()
    if gated:
        log.info("world snapshot skipped — %s", gated)
        return _record({"pushed": False, "reason": f"gated: {gated}", "skipped": True})

    try:
        if png is None:
            png = render_world_png(password=password)
        if state is None:
            state = fetch_state()
    except Exception as e:
        log.warning("world snapshot render failed: %s", e)
        return _record({"pushed": False, "reason": f"render failed: {e}", "error": True})

    # GUARD 2 again — rendering takes ~20s; the toggle could have flipped mid-flight.
    gated = gate_reason()
    if gated:
        log.info("world snapshot discarded after render — %s", gated)
        return _record({"pushed": False, "reason": f"gated: {gated}", "skipped": True})

    stats = build_stats(state)

    # GUARD 3 — leak sweep over the exact payload about to leave the box
    leaks = scan_payload(stats)
    if leaks:
        log.error("world snapshot BLOCKED by leak gate: %s", leaks[:5])
        return _record({"pushed": False, "reason": "leak gate tripped",
                        "leaks": leaks[:10], "error": True})

    try:
        mcp = _mcp_client()
        history = _load_history(mcp)
        out = _publish(mcp, png, stats, history)
        _prune(mcp, history)
    except Exception as e:
        log.warning("world snapshot push failed: %s", e)
        return _record({"pushed": False, "reason": f"push failed: {e}", "error": True})

    _set_setting(LAST_TS_KEY, str(int(time.time())))
    return _record({
        "pushed": True, "reason": "ok",
        "image_url": out["image_url"], "page_id": out["page_id"],
        "captured_at": stats["captured_at"],
        "kept": len(out["history"]),
        "took_sec": round(time.time() - started, 1),
    })


def _record(result: dict) -> dict:
    try:
        _set_setting(LAST_RESULT_KEY, json.dumps({**result, "at": int(time.time())})[:2000])
    except Exception:
        pass
    return result


def push_async(force: bool = False, password: str = "") -> bool:
    """Kick a push onto a daemon thread. Returns False if one is already running.

    Headless Chromium is CPU-only, so this intentionally does NOT enter the GPU
    queue — it just stays off the request path.
    """
    global _RUNNING
    with _LOCK:
        if _RUNNING:
            return False
        _RUNNING = True

    def _run():
        global _RUNNING
        try:
            push_now(force=force, password=password)
        finally:
            with _LOCK:
                _RUNNING = False

    threading.Thread(target=_run, daemon=True, name="world-snapshot").start()
    return True


def running() -> bool:
    return _RUNNING


def status() -> dict:
    try:
        last = json.loads(_setting(LAST_RESULT_KEY, "{}") or "{}")
    except Exception:
        last = {}
    return {
        "enabled": enabled(),
        "interval_min": interval_sec() // 60,
        "gated": gate_reason(),
        "running": running(),
        "last_push_ts": _int(_setting(LAST_TS_KEY, "0")),
        "last": last,
        "keep": KEEP_SNAPSHOTS,
        "data_slug": DATA_SLUG,
    }


def tick() -> dict:
    """Scheduler entry point — respects the toggle and the interval."""
    if not enabled():
        return {"pushed": False, "reason": "toggle off", "skipped": True}
    last = _int(_setting(LAST_TS_KEY, "0"))
    if time.time() - last < interval_sec():
        return {"pushed": False, "reason": "not due", "skipped": True}
    if not push_async():
        return {"pushed": False, "reason": "already running", "skipped": True}
    return {"pushed": None, "reason": "started"}

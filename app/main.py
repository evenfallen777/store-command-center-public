"""
Store Command Center — application assembly.

This file is intentionally thin. All behaviour lives in:
  - config.py    → every value you'd change to move machines (hosts, paths, keys, models)
  - deps.py      → shared kernel (settings, auth, clients, LLM helper, prompts)
  - services.py  → background jobs (image/video/chain generation, publishing)
  - routers/*.py → one module per feature area (the API endpoints)

To add or change an endpoint, edit the matching routers/<area>.py — not this file.
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from deps import *          # config, settings, auth helpers, clients, prompts
from services import *      # background job functions (used by the routers)

from routers import (
    auth, dashboard, proposals, designs, generate, tasks, models,
    trends, settings, printify, etsy, agent, videos, resell, library, security,
    system, cults3d, models3d, node, audio, resell_browser, portal, github,
    world, world_ops, llm, homelab, social, mail, graph, money, crypto, wallets,
    cashapp, peers, oracle, jellycoin, pearl, gpu_guard, watcher as watcher_router,
    research, nsfw as nsfw_router, systems as systems_router,
    health as health_router, pnl as pnl_router, games as games_router, donate,
    integrations as integrations_router, cloudflare as cloudflare_router,
    studio as studio_router, tv as tv_router,
    knowledge, livedocs, brands, voice,
)
from routers import prompts as prompts_router   # /api/prompts — the prompt editor
import docextract as docextract_router   # /api/extract — reusable photo->fields primitive

app = FastAPI(title=f"{APP_NAME} API")

# ─── AUTH GUARD ──────────────────────────────────────────────────────────────
@app.middleware("http")
async def _auth_guard(request: Request, call_next):
    path = request.url.path
    if path in _AUTH_BYPASS:
        return await call_next(request)
    # Donate config: pre-auth surfaces (the login page's "☕ Support this project"
    # button) need to know whether to render. Read-only and leaks nothing but
    # {enabled, url} — the public Buy Me a Coffee link the owner chose to publish
    # (routers/donate.py). GET only; no secrets ever flow through this route.
    if path == "/api/donate/config" and request.method == "GET":
        return await call_next(request)
    # Public, token-guarded 3D asset route — Cults3D fetches these from the
    # internet with no session; the token in the URL is the access control.
    if path.startswith("/api/public/"):
        return await call_next(request)
    # Public, token-guarded demand-signal intake — the WordPress container posts
    # shop searches here from the docker bridge (not 127.0.0.1); the endpoint
    # itself enforces the X-Money-Token header against settings.money_signal_token.
    if path == "/api/money/signals" and request.method == "POST":
        return await call_next(request)
    # JellyCoin GPU rigs: LAN boxes (old GPUs given a second life) poll getwork /
    # submit blocks with no session. Every endpoint under this prefix self-guards
    # with the X-Jelly-Token header (routers/jellycoin.py _check_miner).
    if path.startswith("/api/jelly/mining/"):
        return await call_next(request)
    # GPU node guard: the gaming/desktop box heartbeats busy|free with no session
    # (pauses the AI queue while a Steam game / heavy GPU app runs). Endpoints
    # self-guard with the same X-Jelly-Token the mining rigs use.
    if path.startswith("/api/gpu/guard/"):
        return await call_next(request)
    # Peer RPC: a friend's Store install calls these from a REMOTE host with no
    # session. Every endpoint under this prefix self-guards with the X-Peer-Key
    # header (hash-matched against an APPROVED peer in routers/peers.py) except
    # /pair, which requires a one-time invite key.
    if path.startswith("/api/peers/rpc/"):
        return await call_next(request)
    # Etsy OAuth callback: Etsy redirects the user's browser here (through the
    # reverse proxy, not 127.0.0.1) after they approve, with no store session
    # guaranteed. The endpoint self-guards by matching the OAuth `state` param
    # against the stored `etsy_pkce_state` (routers/etsy.py), so it's CSRF-safe
    # without auth — it must be reachable session-less or the connect flow 401s.
    if path == "/api/etsy/callback":
        return await call_next(request)
    # Allow internal localhost calls to API endpoints (cron jobs, agents)
    client_host = request.client.host if request.client else ""
    if path.startswith("/api/") and client_host in ("127.0.0.1", "::1"):
        return await call_next(request)
    if not request.session.get("authenticated"):
        if path.startswith("/api/"):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return RedirectResponse(url=f"{STORE_BASE}/login", status_code=303)
    return await call_next(request)

# SessionMiddleware is added AFTER the guard so it wraps outermost (session ready first).
# Cookie name + path derive from STORE_BASE so multiple instances on the same host
# (main /store :8787 and dev /store-dev :8788) don't clobber each other's session.
_SESS_COOKIE = "store_sess_" + (STORE_BASE.strip("/").replace("/", "_") or "root")
app.add_middleware(
    SessionMiddleware,
    secret_key=_get_or_create_secret(),
    session_cookie=_SESS_COOKIE,
    path=STORE_BASE or "/",
    https_only=True,
    same_site="lax",
    max_age=86400 * 30,   # 30 days
)


@app.exception_handler(Exception)
async def _log_unhandled(request: Request, exc: Exception):
    """Catch-all so every unhandled endpoint failure is logged with its path + traceback
    (visible in Settings → Logs), instead of vanishing. HTTPExceptions are handled
    separately by FastAPI and don't reach here."""
    import logging, traceback
    logging.getLogger("store").error(
        "Unhandled error on %s %s: %s\n%s",
        request.method, request.url.path, exc, traceback.format_exc())
    return JSONResponse({"error": "Internal error — see Settings → Logs for details."},
                        status_code=500)


def _setup_file_logging():
    """Write all logs (store, orch, uvicorn, tracebacks) to a rotating file so the
    Settings → Logs view can show what happened. stdout still works as before."""
    import logging
    from logging.handlers import RotatingFileHandler
    logdir = DATA_DIR / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(getattr(h, "_store_file", False) for h in root.handlers):
        return  # already installed (avoid dupes on reload)
    fh = RotatingFileHandler(logdir / "store.log", maxBytes=2_000_000, backupCount=3)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s",
                                      datefmt="%Y-%m-%d %H:%M:%S"))
    fh.setLevel(logging.INFO)
    fh._store_file = True
    root.addHandler(fh)
    if root.level > logging.INFO or root.level == 0:
        root.setLevel(logging.INFO)
    # These propagate to root (→ our file handler), so just ensure their level.
    for name in ("store", "orch", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.INFO)
    logging.getLogger("store").info("File logging started → %s", logdir / "store.log")


# ─── STARTUP ─────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    _setup_file_logging()
    init_db()
    migrate_encrypt_secrets(get_conn)   # encrypt any plaintext credentials at rest (idempotent)
    for d in [DESIGNS_PENDING, DESIGNS_APPROVED, DESIGNS_REJECTED]:
        d.mkdir(parents=True, exist_ok=True)
    for d in [MODELS3D_BACKLOG, MODELS3D_RENDERS, MODELS3D_HERO, MODELS3D_ASSETS]:
        d.mkdir(parents=True, exist_ok=True)
    VIDEOS_DIR.mkdir(exist_ok=True)
    RESELL_UPLOADS.mkdir(parents=True, exist_ok=True)
    DOCEXTRACT_UPLOADS.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    reconcile_stuck_media()   # fail any video/chain orphaned by a previous restart
    try:
        from swarm import reconcile_on_start as _swarm_reconcile
        _swarm_reconcile()    # park swarm jobs a restart left mid-run (else "running" forever)
    except Exception as ex:
        import logging
        logging.getLogger("store").warning("swarm reconcile_on_start failed: %s", ex)
    import scheduler
    scheduler.start()   # background security monitor (no-op until enabled in Settings)
    import world_ticker
    world_ticker.start()   # The Company sim: advances the world independent of viewers
    import world_auto
    world_auto.start()     # The Company: autonomous creation → prayers (off until enabled)
    import social_scheduler
    social_scheduler.start()   # Social: auto-post due scheduled posts + analytics (off until enabled)
    money.start_auto()     # Money: 6h signal review + daily carpentry lead hunt (setting money_auto=off disables)
    oracle.start_auto()    # Oracle: resolve due forecasts + a daily tournament round (setting oracle_auto=off disables)
    import engineers
    engineers.start_auto()  # 🐙 The Engineers: autonomous dev-swarm proposer/auto-runner (setting engineers_auto=off disables)
    import redteam, blueteam
    redteam.start_auto()    # 🔴 Red-team: read-only static security auditor (setting redteam_enabled=off disables — inert while off)
    blueteam.start_auto()   # 🔵 Blue-team: files fixes via the gated dev swarm (setting blueteam_enabled=off disables — inert while off)
    import income_import
    income_import.start()  # Income Phase 2: gated READ-ONLY income auto-import (income_autoimport_enabled, default OFF)

# ─── STATIC MOUNTS ───────────────────────────────────────────────────────────
class CachedStaticFiles(StaticFiles):
    """StaticFiles that sends a Cache-Control header so the browser stops
    re-validating every asset on each request (a round-trip per image/script that
    made tab switches slow). Generated media (/designs, /videos) use unique
    filenames and never change once written → cache them long + 'immutable'."""
    def __init__(self, *args, cache_control="public, max-age=31536000, immutable", **kwargs):
        self._cache_control = cache_control
        super().__init__(*args, **kwargs)

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers.setdefault("Cache-Control", self._cache_control)
        return resp


RESELL_UPLOADS.mkdir(parents=True, exist_ok=True)
# These mount at IMPORT time, but the runtime dirs are only created in startup()
# (and by setup.sh) — and none of them are tracked in git, so a fresh clone has
# neither. StaticFiles raises "Directory does not exist" and the app dies before
# it ever serves a request. Create them here, next to the mount that needs them.
for _d in ("designs", "videos"):
    (BASE / _d).mkdir(parents=True, exist_ok=True)
app.mount("/designs", CachedStaticFiles(directory=str(BASE / "designs")), name="designs")
app.mount("/videos",  CachedStaticFiles(directory=str(BASE / "videos")),  name="videos")
# App JS/CSS can change on deploy and aren't content-hashed → shorter cache so an
# update is picked up within the hour (still kills the per-tab-switch revalidation).
app.mount("/static",  CachedStaticFiles(directory=str(BASE / "static"),
                                        cache_control="public, max-age=3600"), name="static")
(BASE / "mail_attachments").mkdir(exist_ok=True)
app.mount("/mail-attachments", StaticFiles(directory=str(BASE / "mail_attachments")), name="mail-attachments")

# ─── ROUTERS ─────────────────────────────────────────────────────────────────
for _mod in (auth, dashboard, proposals, designs, generate, tasks, models,
             trends, settings, printify, etsy, agent, videos, resell, library, security,
             system, cults3d, models3d, node, audio, resell_browser, portal, github,
             world, world_ops, llm, homelab, social, mail, graph, money, crypto, wallets,
             cashapp, peers, oracle, jellycoin, pearl, gpu_guard, watcher_router, prompts_router,
             research, nsfw_router, systems_router, health_router, pnl_router,
             games_router, donate, integrations_router, cloudflare_router,
             studio_router, tv_router, docextract_router, knowledge, livedocs, brands, voice):
    app.include_router(_mod.router)

# ─── PLUGINS (drop-in, auto-discovered) ──────────────────────────────────────
# Third-party add-ons live in gitignored plugins/<name>/ folders. ALL discovery
# and hardening lives in app/plugin_host.py: per-plugin enable/disable (settings
# key plugin_disabled_<id>), manifest `requires` dep checks, a route-collision
# guard, import isolation, and per-plugin status at GET /api/plugins. Mounted
# BEFORE the MCP server below so plugin routes are MCP-exposed like core ones.
# Guarded so even a plugin_host bug can never break boot.
try:
    import plugin_host
    plugin_host.mount_plugins(app)
    PLUGIN_MANIFESTS = plugin_host.PLUGIN_MANIFESTS   # back-compat alias (tests/tools)
except Exception as _e:
    import logging as _plg
    _plg.getLogger("store").warning("plugin host failed to mount: %s", _e)
    PLUGIN_MANIFESTS = []

# ─── MCP SERVER ──────────────────────────────────────────────────────────────
# Expose every /api endpoint to OpenClaw (and any MCP client) as a callable tool,
# so the agent can drive the Store directly — no per-task code edits, no browser
# automation. Mounted UNDER /api/ on purpose: the localhost bypass in _auth_guard
# (above) lets same-box MCP clients through with no session. fastapi-mcp executes
# each tool by calling the endpoint in-process via httpx ASGITransport, whose
# client host is 127.0.0.1 — so that same bypass also covers the internal calls.
# Register with OpenClaw once:
#   openclaw mcp add store --url http://127.0.0.1:8787/api/mcp --transport streamable-http
# (Must be mounted AFTER all routers are included so it sees every route.)
from fastapi_mcp import FastApiMCP

_mcp = FastApiMCP(
    app,
    name=f"{APP_NAME} MCP",
    description="Control the Store Command Center: browse and manage designs, kick off "
                "image/video/audio/3D generation, publish products (Portal/WooCommerce, "
                "Printify, Etsy, Cults3D), drive the dev-swarm and the Company world, and "
                "read dashboard/orders/status — all via the Store's own /api endpoints.",
)
_mcp.mount_http(mount_path="/api/mcp")

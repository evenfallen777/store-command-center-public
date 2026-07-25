"""AI Assistant tool layer — turns the Store's own API surface into callable tools.

The assistant (routers/agent.py) runs an agentic loop on the local model. Instead of
acting as an MCP client against /api/mcp (an extra protocol hop the local gemma-class
models don't need), this module generates tools DIRECTLY from the FastAPI route table
— the same ~248 endpoints fastapi-mcp exposes — and executes them in-process via
httpx ASGITransport (client host 127.0.0.1, so the same localhost auth bypass the MCP
mount rides covers these calls too).

Three layers:
  * CURATED named tools — a small, model-friendly set for the common jobs (queue
    studio generations, dev-swarm jobs, world/God-Console reads, library, settings,
    knowledge graph, queue/stats).
  * api_search(query) — search the FULL endpoint catalog (name, method, path, params).
  * api_call(method, path, ...) — call ANY endpoint found via api_search.

Safety: every non-GET call is classified into a category (money / delete / security /
world / publish / settings / swarm / studio / other). Categories that aren't
auto-approved (per-category user toggles, setting `assistant_auto_<cat>`) require an
explicit user approval in the chat UI before the call executes (routers/agent.py).

Also here: the ReAct/JSON tool-call parser — local models emit tool calls as JSON in
text (fenced or bare); `parse_tool_call` finds and normalizes them robustly.
"""
import json
import re
from typing import Optional

from db import get_conn


# ─── Danger categories + per-category auto-approve toggles ───────────────────
# Every gate ships with a user toggle (Settings inside the Assistant tab).
# `auto_default` = whether calls in this category run WITHOUT asking, by default.
CATEGORIES = [
    {"key": "read",     "label": "Reads (GET)",              "auto_default": True,  "locked": True,
     "desc": "Read-only lookups. Always allowed."},
    {"key": "studio",   "label": "Studio generation jobs",   "auto_default": True,
     "desc": "Queue image / video / audio / 3D generations (uses GPU time)."},
    {"key": "swarm",    "label": "Dev-swarm jobs",           "auto_default": True,
     "desc": "Create/run dev-swarm coding jobs (their own approval pipeline still applies)."},
    {"key": "other",    "label": "Other writes",             "auto_default": True,
     "desc": "Misc harmless writes (tasks, proposals, notes...)."},
    {"key": "world",    "label": "World / God Console acts", "auto_default": False,
     "desc": "God-Console actions on The Company world (blessings, gates, control)."},
    {"key": "publish",  "label": "Publishing / listings",    "auto_default": False,
     "desc": "Publish products or posts to Printify, Etsy, Cults3D, WooCommerce, social, mail."},
    {"key": "settings", "label": "Settings / config writes", "auto_default": False,
     "desc": "Change store settings, prompts, or model slots."},
    {"key": "security", "label": "Security changes",         "auto_default": False,
     "desc": "Network-security / defense / DNS actions."},
    {"key": "money",    "label": "Money movement",           "auto_default": False,
     "desc": "Anything that moves money or coins (money, crypto, wallets, JellyCoin, PayPal)."},
    {"key": "delete",   "label": "Deletions",                "auto_default": False,
     "desc": "DELETE endpoints and destructive clears/resets."},
]
_CAT_BY_KEY = {c["key"]: c for c in CATEGORIES}


def _get_setting(key: str, default=None):
    try:
        c = get_conn()
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        c.close()
        if row and row["value"] not in (None, ""):
            return row["value"]
    except Exception:
        pass
    return default


def auto_approved(category: str) -> bool:
    """Is this category currently auto-approved? Honors the per-category user toggle
    (`assistant_auto_<cat>` in settings), else the category default."""
    cat = _CAT_BY_KEY.get(category) or _CAT_BY_KEY["other"]
    if cat.get("locked"):
        return True
    raw = _get_setting(f"assistant_auto_{cat['key']}", None)
    if raw is None:
        return bool(cat["auto_default"])
    return str(raw).lower() in ("1", "true", "on", "yes")


def category_states() -> list:
    """CATEGORIES with their live toggle state, for the assistant settings UI."""
    return [{**c, "auto": auto_approved(c["key"])} for c in CATEGORIES]


def classify_call(method: str, path: str) -> str:
    """Map an API call to a danger category. GETs are always 'read'."""
    m = (method or "GET").upper()
    p = (path or "").lower().split("?")[0]
    if m in ("GET", "HEAD", "OPTIONS"):
        return "read"
    if m == "DELETE" or re.search(r"/(delete|remove|clear|reset|wipe|purge)(/|$)", p):
        return "delete"
    if (re.match(r"^/api/(money|crypto|wallets|paypal|robinhood)", p)
            or re.match(r"^/api/jelly", p)
            or re.search(r"/(buy|sell|pay|payout|withdraw|transfer|tip|order)(/|$)", p)):
        return "money"
    if re.match(r"^/api/(security|defense|pihole|netguard|guardian|aishield|net)(/|$)", p):
        return "security"
    if re.match(r"^/api/world/(ops|control|god)", p) or "/bless" in p or "/god" in p:
        return "world"
    if (re.search(r"/(publish|unpublish|list-on|send|post)(/|$)", p)
            or re.match(r"^/api/(printify|etsy|cults3d|portal|social|mail|resell)", p)):
        return "publish"
    if re.match(r"^/api/(settings|prompts|models($|/)|model-registry)", p):
        return "settings"
    if re.match(r"^/api/github", p):
        return "swarm"
    if re.match(r"^/api/(generate|videos|video-chains|audio|models3d|enhance-prompt|collection|ai)", p):
        return "studio"
    if re.search(r"^/api/knowledge/notes/\d+/(promote|reject)", p):
        return "settings"   # shared-memory gate — owner review, not agent auto-promote
    return "other"


# ─── Endpoint catalog (generated from the FastAPI route table) ───────────────
_CATALOG_CACHE: Optional[list] = None


def _field_type(f) -> str:
    try:
        t = getattr(f, "type_", None) or getattr(getattr(f, "field_info", None), "annotation", None)
        return getattr(t, "__name__", str(t)) if t is not None else "str"
    except Exception:
        return "str"


def _route_entry(route) -> Optional[dict]:
    path = getattr(route, "path", "")
    if not path.startswith("/api/") or path.startswith("/api/mcp"):
        return None
    methods = [m for m in (getattr(route, "methods", None) or []) if m not in ("HEAD", "OPTIONS")]
    if not methods:
        return None
    ep = getattr(route, "endpoint", None)
    doc = (getattr(ep, "__doc__", None) or "").strip().split("\n")[0][:180]
    entry = {"name": getattr(route, "name", "") or path, "method": methods[0],
             "path": path, "desc": doc, "params": [], "body": {}}
    try:
        dep = getattr(route, "dependant", None)
        if dep:
            for f in list(getattr(dep, "path_params", [])):
                entry["params"].append({"name": f.name, "in": "path", "type": _field_type(f), "required": True})
            for f in list(getattr(dep, "query_params", [])):
                entry["params"].append({"name": f.name, "in": "query", "type": _field_type(f),
                                        "required": bool(getattr(f, "required", False))})
        bf = getattr(route, "body_field", None)
        if bf is not None:
            model = (getattr(getattr(bf, "field_info", None), "annotation", None)
                     or getattr(bf, "type_", None))
            fields = getattr(model, "model_fields", None) or getattr(model, "__fields__", None)
            if fields:
                for fname, finfo in fields.items():
                    ann = getattr(finfo, "annotation", None)
                    req = bool(getattr(finfo, "is_required", lambda: False)()) \
                        if callable(getattr(finfo, "is_required", None)) else False
                    entry["body"][fname] = {"type": getattr(ann, "__name__", str(ann)), "required": req}
            else:
                entry["body"] = {"...": {"type": "object", "required": False}}
    except Exception:
        pass
    return entry


def _walk_routes(routes):
    """Flatten the route table. Newer FastAPI keeps include_router()-ed routers as
    lazy _IncludedRouter wrappers (with .original_router) instead of flat APIRoutes."""
    for r in routes:
        orig = getattr(r, "original_router", None)
        if orig is not None:
            yield from _walk_routes(orig.routes)
        else:
            yield r


def build_catalog(app=None) -> list:
    """All /api endpoints as compact tool entries. Cached after first build."""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None and app is None:
        return _CATALOG_CACHE
    if app is None:
        import main
        app = main.app
    out = []
    for route in _walk_routes(app.routes):
        e = _route_entry(route)
        if e:
            out.append(e)
    if app is not None:
        _CATALOG_CACHE = out
    return out


def search_catalog(query: str, limit: int = 12) -> list:
    """Keyword search over the endpoint catalog (name, path, description)."""
    words = [w for w in re.split(r"[^a-z0-9]+", (query or "").lower()) if w]
    scored = []
    for e in build_catalog():
        hay = f"{e['name']} {e['path']} {e['desc']}".lower()
        score = sum(2 if w in e["path"].lower() else 1 for w in words if w in hay)
        if score:
            scored.append((score, e))
    scored.sort(key=lambda s: -s[0])
    return [e for _, e in scored[:limit]]


# ─── Curated named tools (model-friendly core set) ───────────────────────────
# Each maps a simple tool name → one endpoint. Args: body fields for POST,
# query/path params by name for GET (path params substituted into the path).
CURATED = [
    {"name": "queue_status",    "method": "GET",  "path": "/api/queue",
     "desc": "Current unified job queue (all pending/running/done generation+LLM jobs)."},
    {"name": "store_stats",     "method": "GET",  "path": "/api/stats",
     "desc": "Dashboard stats for the whole store."},
    {"name": "generate_image",  "method": "POST", "path": "/api/generate",
     "desc": "Queue an image generation. Args: prompt (required), product_type, width, height, steps, variations."},
    {"name": "generate_video",  "method": "POST", "path": "/api/videos/generate",
     "desc": "Queue a video generation. Args: prompt (required) plus optional model/settings."},
    {"name": "generate_audio",  "method": "POST", "path": "/api/audio/generate",
     "desc": "Queue music/voice audio generation. Args: prompt (required), engine, duration."},
    {"name": "generate_3d",     "method": "POST", "path": "/api/models3d/generate",
     "desc": "Queue a 3D model generation. Args: prompt (required)."},
    {"name": "swarm_jobs",      "method": "GET",  "path": "/api/github/jobs",
     "desc": "List dev-swarm coding jobs and their status."},
    {"name": "swarm_submit_job", "method": "POST", "path": "/api/github/jobs",
     "desc": "Create a dev-swarm coding job. Args: title, description (what to build/fix)."},
    {"name": "world_summary",   "method": "GET",  "path": "/api/world/ops/summary",
     "desc": "The Company world God-Console summary (prayers, budget, board)."},
    {"name": "world_state",     "method": "GET",  "path": "/api/world/state",
     "desc": "Live world state: agents, buildings, economy of The Company."},
    {"name": "library_search",  "method": "GET",  "path": "/api/library/search",
     "desc": "Search the knowledge library. Args: q (query string)."},
    {"name": "list_designs",    "method": "GET",  "path": "/api/generations",
     "desc": "Recent generated designs/images. Args: limit."},
    {"name": "settings_list",   "method": "GET",  "path": "/api/settings",
     "desc": "Read current store settings (read-only)."},
    {"name": "graph_query",     "method": "POST", "path": "/api/graph/query",
     "desc": "Ask the codebase knowledge graph a question. Args: q (the question)."},
    {"name": "knowledge_search", "method": "GET", "path": "/api/knowledge/search",
     "desc": "Federated search across the Library, Research reports AND the codebase "
             "Knowledge Graph in ONE call. Args: q (question/keywords), scope (all|code|research). "
             "The default way to pull context/memory before answering or coding."},
    {"name": "livedocs_lookup", "method": "GET", "path": "/api/livedocs/lookup",
     "desc": "Fetch CURRENT docs for a named software library (context7). "
             "Args: library (e.g. fastapi), topic (optional). Use before coding against a library."},
    {"name": "knowledge_remember", "method": "POST", "path": "/api/knowledge/remember",
     "desc": "Record a fact you learned into SHARED memory (staged for owner review before it "
             "becomes searchable). Args: text (the fact), agent (who), tags, confidence. Use for "
             "durable findings worth keeping across sessions."},
]
_CURATED_BY_NAME = {t["name"]: t for t in CURATED}

META_TOOLS = [
    {"name": "api_search",
     "desc": "Search the store's FULL API catalog (~248 endpoints) when no named tool fits. "
             "Args: query (keywords). Returns matching endpoints with method/path/params."},
    {"name": "api_call",
     "desc": "Call ANY store API endpoint (found via api_search). "
             "Args: method (GET/POST/DELETE), path (e.g. /api/queue), params (query dict), body (JSON dict)."},
]


def prompt_tool_docs() -> str:
    """Tool list injected into the assistant system prompt."""
    lines = []
    for t in CURATED:
        lines.append(f"- {t['name']}: {t['desc']}")
    for t in META_TOOLS:
        lines.append(f"- {t['name']}: {t['desc']}")
    return "\n".join(lines)


# ─── Tool-call parsing (robust for local models: JSON-in-text / ReAct) ───────
def _json_objects(text: str):
    dec = json.JSONDecoder()
    i = 0
    n = len(text)
    while i < n:
        i = text.find("{", i)
        if i < 0:
            return
        try:
            obj, end = dec.raw_decode(text, i)
            yield obj
            i = end
        except ValueError:
            i += 1


def parse_tool_call(text: str) -> Optional[dict]:
    """Find a tool call in model output. Accepts fenced or bare JSON, and several
    shapes: {"tool": name, "args": {...}}, {"name": ..., "arguments": {...}},
    {"tool_call": {...}}. Returns {"tool": str, "args": dict} or None (= final answer)."""
    if not text:
        return None
    for obj in _json_objects(text):
        if not isinstance(obj, dict):
            continue
        if isinstance(obj.get("tool_call"), dict):
            obj = obj["tool_call"]
        name = obj.get("tool") or obj.get("tool_name") or obj.get("function")
        if name is None and obj.get("name") and any(k in obj for k in ("args", "arguments", "parameters", "input")):
            name = obj.get("name")
        if not name or not isinstance(name, str):
            continue
        args = None
        for k in ("args", "arguments", "parameters", "input"):
            if isinstance(obj.get(k), dict):
                args = obj[k]
                break
            if isinstance(obj.get(k), str):
                try:
                    parsed = json.loads(obj[k])
                    if isinstance(parsed, dict):
                        args = parsed
                        break
                except ValueError:
                    pass
        return {"tool": name.strip(), "args": args or {}}
    return None


# ─── Resolution + execution ──────────────────────────────────────────────────
def resolve_call(tool: str, args: dict) -> dict:
    """Turn a parsed tool call into a concrete {method, path, params, body, category}.
    Raises ValueError on unknown tools / bad args."""
    args = args or {}
    if tool == "api_search":
        return {"kind": "search", "query": str(args.get("query") or args.get("q") or ""),
                "category": "read"}
    if tool == "api_call":
        method = str(args.get("method") or "GET").upper()
        path = str(args.get("path") or "").strip()
        if not path.startswith("/api/"):
            raise ValueError("api_call needs a path starting with /api/ (use api_search to find one)")
        if path.startswith("/api/mcp"):
            raise ValueError("the MCP mount is not callable from here")
        body = args.get("body") if isinstance(args.get("body"), dict) else None
        params = args.get("params") if isinstance(args.get("params"), dict) else None
        return {"kind": "http", "method": method, "path": path, "params": params,
                "body": body, "category": classify_call(method, path)}
    t = _CURATED_BY_NAME.get(tool)
    if not t:
        raise ValueError(f"unknown tool '{tool}' — use one of the listed tools, or api_search + api_call")
    method, path = t["method"], t["path"]
    if method == "GET":
        # path-param substitution + leftovers become query params
        params = {}
        for k, v in args.items():
            if "{" + k + "}" in path:
                path = path.replace("{" + k + "}", str(v))
            elif v is not None:
                params[k] = v
        return {"kind": "http", "method": method, "path": path, "params": params or None,
                "body": None, "category": classify_call(method, path)}
    return {"kind": "http", "method": method, "path": path, "params": None,
            "body": args, "category": classify_call(method, path)}


def _redact_secrets(obj):
    """Never hand the ASSISTANT decrypted secrets. GET /api/settings decrypts credentials
    for the human UI, but the assistant's auto-approved read tool would otherwise let a
    prompt-injected read (e.g. text in a summarized email/page) exfil PayPal/Etsy/Kraken
    keys and spend money OUTSIDE the world_ops gates. Redact any secret-named field in
    whatever the tool returns — defense-in-depth across every endpoint, not just settings.
    The human UI calls /api/settings directly and is unaffected."""
    try:
        from crypto import SECRET_KEYS as _SK
    except Exception:
        return obj
    if isinstance(obj, dict):
        return {k: ("***redacted***" if (isinstance(k, str) and k in _SK and v not in (None, "", 0))
                    else _redact_secrets(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_secrets(x) for x in obj]
    return obj


def execute_resolved(res: dict, timeout: float = 300.0) -> dict:
    """Execute a resolved call in-process against the app (ASGI transport →
    127.0.0.1 client → rides the localhost auth bypass, like the MCP mount)."""
    if res.get("kind") == "search":
        hits = search_catalog(res.get("query", ""))
        return {"status": 200, "result": {"matches": hits, "hint": "call these with api_call"}}
    import asyncio

    import httpx
    import main

    async def _go():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1",
                                     timeout=timeout) as c:
            r = await c.request(res["method"], res["path"], params=res.get("params"),
                                json=res.get("body"))
            try:
                data = r.json()
            except ValueError:
                data = (r.text or "")[:2000]
            return {"status": r.status_code, "result": _redact_secrets(data)}

    return asyncio.run(_go())


def truncate_for_llm(obj, limit: int = 3500) -> str:
    """Compact a tool result for the model's context."""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(obj)
    if len(s) > limit:
        s = s[:limit] + f"... [truncated, {len(s)} chars total]"
    return s

"""Live Docs — up-to-date library documentation for Store agents (and OpenClaw).

The one capability the local-model kit lacked versus Claude Code's context7: fetch
*current*, version-aware API docs for a library on demand, instead of relying on
whatever the model memorized at training time. Backed by context7.com's public docs
API (keyless; set CONTEXT7_API_KEY in the store's env for higher rate limits).

Why a plugin: drop-in, gitignored, survives store updates, and — because every
/api/* route is auto-discoverable by the AI-Assistant (api_search / api_call) and is
reachable through the store MCP bridge OpenClaw already speaks — one implementation
gives BOTH agent layers live docs with zero core-file edits.

Agent-facing routes (all GET, all behind the store's normal auth guard; localhost
callers — the in-process assistant and OpenClaw's MCP calls — bypass as everywhere):
  GET /api/livedocs/search?query=            -> resolve a name to candidate library ids
  GET /api/livedocs/docs?id=&topic=&tokens=  -> fetch docs for a known id
  GET /api/livedocs/lookup?library=&topic=   -> one-shot: resolve best match + fetch
  GET /api/livedocs/health                   -> upstream reachability check

The HTTP fetch logic lives in the module-level _search/_docs/_lookup helpers (plain
params) so it is unit-testable without a running app; the routes are thin wrappers.
"""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Query

router = APIRouter()

_BASE = os.environ.get("CONTEXT7_BASE", "https://context7.com/api/v1").rstrip("/")
_KEY = os.environ.get("CONTEXT7_API_KEY", "").strip()
_TIMEOUT = 20.0


def _headers(accept: str = "application/json") -> dict:
    h = {"Accept": accept}
    if _KEY:
        h["Authorization"] = f"Bearer {_KEY}"
    return h


# ---- core logic (plain params, no FastAPI objects -> directly testable) ----

def _search(query: str) -> dict:
    """Resolve a free-text library name to candidate context7 library ids."""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "empty query", "query": query, "results": []}
    try:
        r = httpx.get(f"{_BASE}/search", params={"query": query},
                      headers=_headers(), timeout=_TIMEOUT)
        r.raise_for_status()
        results = (r.json() or {}).get("results", []) or []
    except Exception as e:  # never raise into the agent loop — return a shaped error
        return {"ok": False, "error": f"context7 search failed: {e}",
                "query": query, "results": []}
    slim = [{
        "id": x.get("id"),
        "title": x.get("title"),
        "description": (x.get("description") or "")[:300],
        "score": x.get("score"),          # context7 relevance — best primary sort key
        "trustScore": x.get("trustScore"),
        "snippets": x.get("totalSnippets"),
        "lastUpdate": x.get("lastUpdateDate"),
    } for x in results[:10]]
    return {"ok": True, "query": query, "count": len(slim), "results": slim}


def _docs(id: str, topic: str = "", tokens: int = 2500) -> dict:
    """Fetch current docs text for a known context7 library id."""
    if not id:
        return {"ok": False, "error": "empty id", "id": id}
    lib = id if id.startswith("/") else f"/{id}"
    try:
        r = httpx.get(f"{_BASE}{lib}",
                      params={"type": "txt", "topic": topic or "", "tokens": tokens},
                      headers=_headers("text/plain"), timeout=_TIMEOUT)
        r.raise_for_status()
        text = r.text or ""
    except Exception as e:
        return {"ok": False, "error": f"context7 docs fetch failed: {e}", "id": id}
    return {"ok": True, "id": id, "topic": topic, "tokens": tokens,
            "chars": len(text), "text": text}


def _lookup(library: str, topic: str = "", tokens: int = 2500) -> dict:
    """One-shot: resolve the best library match, then return its docs."""
    s = _search(library)
    if not s.get("ok") or not s.get("results"):
        return {"ok": False, "library": library,
                "error": s.get("error") or f"no library found for '{library}'"}
    # context7 relevance score is the best match signal; trust then snippets break ties
    best = sorted(s["results"],
                  key=lambda x: ((x.get("score") or 0), (x.get("trustScore") or 0),
                                 (x.get("snippets") or 0)),
                  reverse=True)[0]
    d = _docs(best["id"], topic=topic, tokens=tokens)
    d["library"] = library
    d["resolved_id"] = best["id"]
    d["resolved_title"] = best.get("title")
    return d


# ---- routes (thin wrappers) ----

@router.get("/api/livedocs/search")
def livedocs_search(query: str = Query(..., description="Library name to resolve, e.g. 'fastapi'")):
    """Resolve a library name to candidate ids (pick the highest-trust match, then /docs)."""
    return _search(query)


@router.get("/api/livedocs/docs")
def livedocs_docs(
    id: str = Query(..., description="context7 id from /search, e.g. '/websites/fastapi_tiangolo'"),
    topic: str = Query("", description="Optional focus topic, e.g. 'middleware'"),
    tokens: int = Query(2500, ge=200, le=12000, description="Approx docs size to return"),
):
    """Fetch current docs text for a known context7 library id."""
    return _docs(id, topic, tokens)


@router.get("/api/livedocs/lookup")
def livedocs_lookup(
    library: str = Query(..., description="Library name, e.g. 'fastapi', 'react', 'httpx'"),
    topic: str = Query("", description="Optional focus topic to narrow the docs"),
    tokens: int = Query(2500, ge=200, le=12000),
):
    """One-shot resolve-and-fetch: the tool agents should reach for by default."""
    return _lookup(library, topic, tokens)


@router.get("/api/livedocs/health")
def livedocs_health():
    """Cheap upstream reachability check."""
    try:
        r = httpx.get(f"{_BASE}/search", params={"query": "python"},
                      headers=_headers(), timeout=8.0)
        return {"ok": r.status_code == 200, "upstream": _BASE,
                "status": r.status_code, "keyed": bool(_KEY)}
    except Exception as e:
        return {"ok": False, "upstream": _BASE, "error": str(e), "keyed": bool(_KEY)}

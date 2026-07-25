"""Knowledge Hub — one place (and one tool) that federates the store's knowledge.

Fans a question out to the sources that already exist and returns merged, cited hits:
  - Library      -> app/library/**/*.md   (semantic index; substring fallback)
  - Research Lab -> research_projects.report_md (semantic index)
  - Graph        -> the graphify codebase knowledge graph (via /api/graph's CLI)

Live Docs (context7) is offered alongside as its own tool/tab, NOT folded in — it fetches
docs for a *named* library, a different shape from a knowledge search.

Phase 2 (this file): a real SEMANTIC index. Library + research are chunked and embedded
(via the store's /api/llm/v1/embeddings proxy → LM Studio nomic model, the same rail
world_taste uses) into the `knowledge_index` table; queries embed + cosine-rank. Indexing
is incremental (per-chunk content hash) and reindex runs in the background. If the index
is empty (or embeddings are unavailable) the search falls back to the old substring path,
so it always returns something.

Why a plugin: drop-in, gitignored, survives updates, auto-visible to the AI Assistant and
reachable by OpenClaw via the store MCP bridge — one endpoint, every agent.

Routes (GET read-only unless noted; localhost callers bypass auth):
  GET  /api/knowledge/search?q=&scope=all|code|research&limit=  -> merged federated hits
  GET  /api/knowledge/sources                                    -> live sources + index size
  POST /api/knowledge/reindex                                    -> (re)build the semantic index
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import httpx
import numpy as np
from fastapi import APIRouter, BackgroundTasks, Body, Query

router = APIRouter()

_SNIPPET = 320
_LOCAL = "http://127.0.0.1:8787"
_EMBED_MODEL_DEFAULT = "text-embedding-nomic-embed-text-v1.5"
_CHUNK = 1400          # chars per chunk
_MAX_CHUNKS_PER_FILE = 6
_EMBED_BATCH = 32

# in-process cache of the embedding matrix (rebuilt lazily / after reindex)
_MATRIX = {"mat": None, "rows": None, "dirty": True}
_REINDEX = {"running": False, "done": 0, "new": 0, "error": None}


# ── embeddings ────────────────────────────────────────────────────────────────

def _embed_model():
    try:
        from deps import get_setting
        return get_setting("taste_embed_model", _EMBED_MODEL_DEFAULT) or _EMBED_MODEL_DEFAULT
    except Exception:
        return _EMBED_MODEL_DEFAULT


def _embed_batch(texts):
    """Embed a list of texts in one proxy call. Returns list of vectors (or [] on failure)."""
    if not texts:
        return []
    try:
        r = httpx.post(f"{_LOCAL}/api/llm/v1/embeddings",
                       json={"model": _embed_model(), "input": [t[:1500] for t in texts]},
                       timeout=60)
        r.raise_for_status()
        data = r.json().get("data", [])
        return [d["embedding"] for d in data]
    except Exception:
        return []


def _embed_one(text):
    v = _embed_batch([text])
    return v[0] if v else None


# ── chunking ──────────────────────────────────────────────────────────────────

def _chunks(text):
    """Paragraph-aware chunks ~_CHUNK chars, capped per doc."""
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 2 <= _CHUNK:
            cur = (cur + "\n\n" + p) if cur else p
        else:
            if cur:
                chunks.append(cur)
            cur = p[:_CHUNK]
        if len(chunks) >= _MAX_CHUNKS_PER_FILE:
            break
    if cur and len(chunks) < _MAX_CHUNKS_PER_FILE:
        chunks.append(cur)
    return chunks


def _hash(s):
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()


# ── index storage ─────────────────────────────────────────────────────────────

def _ensure(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT, ref TEXT, title TEXT, chunk_idx INTEGER,
            hash TEXT, text TEXT, vec TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source, ref, chunk_idx)
        )""")


def _docs():
    """Yield (source, ref, title, full_text) for everything to index."""
    # library files
    try:
        from library import LIBRARY_ROOT
        for f in LIBRARY_ROOT.rglob("*.md"):
            try:
                yield ("library", str(f.relative_to(LIBRARY_ROOT)), f.stem,
                       f.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
    except Exception:
        pass
    # research reports
    try:
        from db import get_conn
        for row in get_conn().execute(
                "SELECT id, title, report_md FROM research_projects "
                "WHERE report_md IS NOT NULL AND report_md != ''").fetchall():
            yield ("research", f"/api/research/projects/{row[0]}/report", row[1], row[2])
    except Exception:
        pass


def reindex(max_new=6000):
    """Incremental (re)build: embed only new/changed chunks. Runs in a bg thread."""
    from db import get_conn
    con = get_conn()
    _ensure(con)
    _REINDEX.update(running=True, done=0, new=0, error=None)
    try:
        pending = []   # (source, ref, title, idx, hash, text)
        for source, ref, title, full in _docs():
            chunks = _chunks(full)
            # drop stale chunks beyond current count
            con.execute("DELETE FROM knowledge_index WHERE source=? AND ref=? AND chunk_idx>=?",
                        (source, ref, len(chunks)))
            for i, ch in enumerate(chunks):
                h = _hash(ch)
                row = con.execute("SELECT hash FROM knowledge_index WHERE source=? AND ref=? AND chunk_idx=?",
                                  (source, ref, i)).fetchone()
                if row and row[0] == h:
                    continue                      # unchanged
                pending.append((source, ref, title, i, h, ch))
            if len(pending) >= max_new:
                break
        con.commit()
        # embed in batches and upsert
        for b in range(0, min(len(pending), max_new), _EMBED_BATCH):
            batch = pending[b:b + _EMBED_BATCH]
            vecs = _embed_batch([c[5] for c in batch])
            if len(vecs) != len(batch):
                continue                          # embedding hiccup — skip this batch
            for (source, ref, title, i, h, ch), v in zip(batch, vecs):
                con.execute(
                    "INSERT INTO knowledge_index (source,ref,title,chunk_idx,hash,text,vec,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,datetime('now')) "
                    "ON CONFLICT(source,ref,chunk_idx) DO UPDATE SET "
                    "hash=excluded.hash, text=excluded.text, vec=excluded.vec, "
                    "title=excluded.title, updated_at=datetime('now')",
                    (source, ref, title, i, h, ch, json.dumps(v)))
            con.commit()
            _REINDEX["new"] += len(batch)
            _REINDEX["done"] = b + len(batch)
        _MATRIX["dirty"] = True
    except Exception as e:
        _REINDEX["error"] = str(e)
    finally:
        _REINDEX["running"] = False


# ── semantic search ───────────────────────────────────────────────────────────

def _load_matrix():
    """Load (and cache) the normalized embedding matrix + row metadata."""
    if not _MATRIX["dirty"] and _MATRIX["mat"] is not None:
        return _MATRIX["mat"], _MATRIX["rows"]
    try:
        from db import get_conn
        con = get_conn()
        _ensure(con)
        rows = con.execute("SELECT source, ref, title, text, vec FROM knowledge_index "
                           "WHERE vec IS NOT NULL").fetchall()
    except Exception:
        return None, None
    if not rows:
        _MATRIX.update(mat=None, rows=[], dirty=False)
        return None, []
    vecs, meta = [], []
    for source, ref, title, text, vec in rows:
        try:
            vecs.append(json.loads(vec))
            meta.append({"source": source, "ref": ref, "title": title, "text": text})
        except Exception:
            continue
    mat = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms
    _MATRIX.update(mat=mat, rows=meta, dirty=False)
    return mat, meta


def _semantic_hits(q, sources, limit):
    mat, meta = _load_matrix()
    if mat is None or not meta:
        return None                    # signal "index unavailable" -> caller falls back
    qv = _embed_one(q)
    if not qv:
        return None
    qv = np.asarray(qv, dtype=np.float32)
    n = np.linalg.norm(qv) or 1.0
    scores = mat @ (qv / n)
    order = np.argsort(-scores)
    out = []
    for idx in order:
        m = meta[idx]
        if m["source"] not in sources:
            continue
        out.append({
            "source": m["source"],
            "title": m["title"],
            "snippet": (m["text"] or "")[:_SNIPPET].strip(),
            "ref": m["ref"],
            "score": round(float(scores[idx]), 4),
        })
        if len(out) >= limit:
            break
    return out


# ── substring fallback (Phase-1 behaviour, used until the index is built) ─────

def _library_hits_kw(q, limit):
    try:
        from library import search_library
    except Exception:
        return []
    out = []
    for r in (search_library(q) or [])[:limit]:
        ctx = (r.get("matches") or [{}])[0].get("context", "") or ""
        out.append({"source": "library", "title": r.get("name") or r.get("path"),
                    "snippet": ctx.strip()[:_SNIPPET], "ref": r.get("path"),
                    "score": float(r.get("match_count") or 1)})
    return out


def _research_hits_kw(q, limit):
    try:
        from db import get_conn
        like = f"%{q}%"
        rows = get_conn().execute(
            "SELECT id, title, status, report_md FROM research_projects "
            "WHERE title LIKE ? OR description LIKE ? OR report_md LIKE ? "
            "ORDER BY updated_at DESC LIMIT ?", (like, like, like, limit)).fetchall()
    except Exception:
        return []
    out = []
    for rid, title, status, report_md in rows:
        t = report_md or ""
        i = t.lower().find(q.lower())
        snip = (("…" + t[max(0, i - 100):i + 220] + "…") if i >= 0 else t[:_SNIPPET]).strip()
        out.append({"source": "research", "title": title, "snippet": snip,
                    "ref": f"/api/research/projects/{rid}/report", "score": 2.0, "status": status})
    return out


def _graph_hits(q):
    try:
        from routers.graph import GRAPHIFY, BASE, _have_graph
        if not _have_graph():
            return []
        r = subprocess.run([GRAPHIFY, "query", q, "--budget", "900"],
                           cwd=str(BASE), capture_output=True, text=True, timeout=45)
        out = (r.stdout or "").strip() or (r.stderr or "").strip()
        if r.returncode != 0 or not out:
            return []
        # score sits in the semantic cosine range (0-1) so a strongly-relevant doc can
        # outrank the graph, while the graph still surfaces prominently for code questions.
        return [{"source": "graph", "title": "Codebase knowledge graph",
                 "snippet": out[:1200], "ref": "/api/graph/query", "score": 0.62}]
    except Exception:
        return []


def _index_count():
    try:
        from db import get_conn
        con = get_conn(); _ensure(con)
        return int(con.execute("SELECT COUNT(*) FROM knowledge_index WHERE vec IS NOT NULL").fetchone()[0])
    except Exception:
        return 0


# ── auto-refresh (keeps the index + code graph current) ──────────────────────
#
# A single background daemon that, on an interval:
#   • re-indexes the Library + Research — incremental, so an added/edited doc or a
#     finished research report is embedded automatically (nothing changed => ~free);
#   • runs `graphify update .` when any code file changed — so a NEW SCRIPT or an edit
#     to an existing one lands in the codebase graph (AST-only, no LLM, cheap).
# Self-contained in this plugin (no core edits). Tune/disable via env
# KNOWLEDGE_REFRESH_SECONDS (default 900; 0 disables).

_CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".html", ".css", ".sh", ".sql", ".json", ".md"}
_GRAPH_LOCK = threading.Lock()
_REFRESH = {
    "enabled": os.environ.get("KNOWLEDGE_REFRESH_SECONDS", "900") != "0",
    "interval": max(120, int(os.environ.get("KNOWLEDGE_REFRESH_SECONDS", "900") or "900")),
    "started": False,
    "last_run": None, "last_docs_reindex": None, "last_graph_update": None,
    "last_code_mtime": 0.0,
}


def _newest_mtime(root, exts):
    newest = 0.0
    try:
        for p in Path(root).rglob("*"):
            if p.suffix in exts and p.is_file():
                try:
                    m = p.stat().st_mtime
                    if m > newest:
                        newest = m
                except Exception:
                    continue
    except Exception:
        pass
    return newest


def _graph_update():
    """Incremental AST re-extract of changed code into the graph. Never overlaps."""
    if not _GRAPH_LOCK.acquire(blocking=False):
        return
    try:
        from routers.graph import GRAPHIFY, BASE
        subprocess.run([GRAPHIFY, "update", "."], cwd=str(BASE),
                       capture_output=True, text=True, timeout=600)
        _REFRESH["last_graph_update"] = time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    finally:
        _GRAPH_LOCK.release()


def _refresh_tick():
    # Docs/research: reindex is incremental (hash-based) — cheap when nothing changed,
    # and it's the one place research reports + new library files get picked up.
    try:
        if not _REINDEX["running"]:
            reindex()
            _REFRESH["last_docs_reindex"] = time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    # Code: only rebuild the graph when a watched code file actually changed.
    try:
        from routers.graph import BASE
        code_m = _newest_mtime(BASE, _CODE_EXTS)
        if code_m > _REFRESH["last_code_mtime"]:
            _graph_update()
            _REFRESH["last_code_mtime"] = code_m
    except Exception:
        pass
    _REFRESH["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")


def _refresher():
    time.sleep(90)                      # let boot settle before the first sweep
    while _REFRESH["enabled"]:
        _refresh_tick()
        time.sleep(_REFRESH["interval"])


def _start_refresher():
    if _REFRESH["started"] or not _REFRESH["enabled"]:
        return
    _REFRESH["started"] = True
    threading.Thread(target=_refresher, name="knowledge-refresher", daemon=True).start()


# ── routes ────────────────────────────────────────────────────────────────────

# ── shared memory: agent write-back with a promotion gate ───────────────
#
# Agents record what they learn via POST /api/knowledge/remember → stored 'staged'.
# Staged notes are NOT searchable; the owner promotes trustworthy ones (.../promote)
# to 'trusted', at which point they join federated results as a 'memory' source. This
# keeps shared memory from filling with unverified agent claims. Notes are embedded
# (nomic) so recall is semantic like everything else.

def _ensure_notes(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT, agent TEXT DEFAULT '', tags TEXT DEFAULT '',
            status TEXT DEFAULT 'staged',        -- staged | trusted
            confidence REAL DEFAULT 0.5, vec TEXT,
            created_at TEXT DEFAULT (datetime('now')), reviewed_at TEXT
        )""")


def _notes_hits(q, limit):
    """Trusted shared-memory notes, cosine-ranked (source='memory'). Small volume, so
    cosine on the fly rather than caching a matrix."""
    try:
        from db import get_conn
        con = get_conn(); _ensure_notes(con)
        rows = con.execute("SELECT id, text, agent, tags, vec FROM knowledge_notes "
                           "WHERE status='trusted' AND vec IS NOT NULL").fetchall()
    except Exception:
        return []
    if not rows:
        return []
    qv = _embed_one(q)
    if not qv:
        return []
    qa = np.asarray(qv, dtype=np.float32); qn = np.linalg.norm(qa) or 1.0
    scored = []
    for rid, text, agent, tags, vec in rows:
        try:
            v = np.asarray(json.loads(vec), dtype=np.float32)
            sc = float(v @ qa / ((np.linalg.norm(v) or 1.0) * qn))
        except Exception:
            continue
        scored.append((sc, rid, text, agent, tags))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"source": "memory", "title": (tags or agent or "note"),
             "snippet": (text or "")[:_SNIPPET], "ref": f"/api/knowledge/notes/{rid}",
             "score": round(sc, 4)} for sc, rid, text, agent, tags in scored[:limit]]


@router.get("/api/knowledge/search")
def knowledge_search(
    q: str = Query(..., description="Natural-language question or keywords"),
    scope: str = Query("all", description="all | code | research — which sources to hit"),
    limit: int = Query(8, ge=1, le=30, description="Max hits per prose source"),
):
    """Federated search across Library + Research + Graph → merged, cited hits.

    Semantic (embedding) ranking when the index is built; substring fallback otherwise.
    THE 'ask our knowledge' tool for agents. Scope: 'code' = library+graph,
    'research' = research+library, 'all' = everything."""
    q = (q or "").strip()
    if not q:
        return {"ok": False, "error": "empty q", "results": []}
    scope = (scope or "all").lower()
    prose_sources = set()
    if scope in ("all", "code", "research"):
        prose_sources.add("library")
    if scope in ("all", "research"):
        prose_sources.add("research")

    hits, mode = [], "substring"
    sem = _semantic_hits(q, prose_sources, limit * len(prose_sources) or limit) if prose_sources else None
    if sem is not None:
        hits += sem
        mode = "semantic"
    else:
        if "library" in prose_sources:
            hits += _library_hits_kw(q, limit)
        if "research" in prose_sources:
            hits += _research_hits_kw(q, limit)
    if scope in ("all", "code"):
        hits += _graph_hits(q)
    hits += _notes_hits(q, limit)          # trusted shared-memory notes (always)

    hits.sort(key=lambda h: h.get("score", 0), reverse=True)
    hits = hits[:12]                    # bound the merged context handed to agents/UI
    return {"ok": True, "query": q, "scope": scope, "mode": mode, "count": len(hits), "results": hits}


@router.get("/api/knowledge/sources")
def knowledge_sources():
    """Which knowledge sources are live + how big the semantic index is."""
    out = {}
    try:
        from library import search_library  # noqa: F401
        out["library"] = True
    except Exception:
        out["library"] = False
    try:
        from db import get_conn
        n = get_conn().execute("SELECT COUNT(*) FROM research_projects").fetchone()[0]
        out["research"] = {"ok": True, "projects": int(n)}
    except Exception:
        out["research"] = {"ok": False}
    try:
        from routers.graph import _have_graph
        out["graph"] = bool(_have_graph())
    except Exception:
        out["graph"] = False
    try:
        from db import get_conn
        con = get_conn(); _ensure_notes(con)
        st = dict(con.execute("SELECT status, COUNT(*) FROM knowledge_notes GROUP BY status").fetchall())
        out["memory"] = {"staged": int(st.get("staged", 0)), "trusted": int(st.get("trusted", 0))}
    except Exception:
        out["memory"] = {"staged": 0, "trusted": 0}
    out["livedocs"] = True
    return {"ok": True, "sources": out,
            "index": {"chunks": _index_count(), "reindex": _REINDEX},
            "auto_refresh": _REFRESH}


@router.post("/api/knowledge/reindex")
def knowledge_reindex(bg: BackgroundTasks, max_new: int = Query(6000, ge=1, le=20000)):
    """(Re)build the semantic index in the background. Incremental — only new/changed chunks."""
    if _REINDEX["running"]:
        return {"ok": True, "status": "already running", "reindex": _REINDEX}
    bg.add_task(reindex, max_new)
    return {"ok": True, "status": "started", "note": "indexing library + research in background"}


@router.post("/api/knowledge/refresh")
def knowledge_refresh(bg: BackgroundTasks):
    """Force a refresh NOW: reindex docs/research + update the code graph in the background.
    (This also runs automatically every KNOWLEDGE_REFRESH_SECONDS.)"""
    bg.add_task(_refresh_tick)
    return {"ok": True, "status": "refreshing", "auto_refresh": _REFRESH}


@router.post("/api/knowledge/remember")
def knowledge_remember(body: dict = Body(...)):
    """Record a learned fact into SHARED memory. Stored 'staged' (NOT searchable) until the
    owner promotes it. Args: text (required), agent, tags, confidence."""
    text = (body.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "text required"}
    from db import get_conn
    con = get_conn(); _ensure_notes(con)
    vec = _embed_one(text[:1500])
    cur = con.execute(
        "INSERT INTO knowledge_notes (text, agent, tags, confidence, vec, status) "
        "VALUES (?,?,?,?,?, 'staged')",
        (text[:4000], (body.get("agent") or "")[:80], (body.get("tags") or "")[:200],
         float(body.get("confidence") or 0.5), json.dumps(vec) if vec else None))
    con.commit()
    return {"ok": True, "id": cur.lastrowid, "status": "staged",
            "note": "saved to shared memory pending owner review"}


@router.get("/api/knowledge/notes")
def knowledge_notes(status: str = Query("all", description="staged | trusted | all"),
                    limit: int = Query(100, ge=1, le=500)):
    """List shared-memory notes (for the review gate)."""
    from db import get_conn
    con = get_conn(); _ensure_notes(con)
    keys = ["id", "text", "agent", "tags", "status", "confidence", "created_at", "reviewed_at"]
    cols = ",".join(keys)
    if status in ("staged", "trusted"):
        rows = con.execute(f"SELECT {cols} FROM knowledge_notes WHERE status=? ORDER BY id DESC LIMIT ?",
                           (status, limit)).fetchall()
    else:
        rows = con.execute(f"SELECT {cols} FROM knowledge_notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"ok": True, "notes": [dict(zip(keys, r)) for r in rows]}


@router.post("/api/knowledge/notes/{note_id}/promote")
def knowledge_note_promote(note_id: int):
    """Owner gate: promote a staged note to 'trusted' so it joins search results."""
    from db import get_conn
    con = get_conn(); _ensure_notes(con)
    con.execute("UPDATE knowledge_notes SET status='trusted', reviewed_at=datetime('now') WHERE id=?", (note_id,))
    con.commit()
    return {"ok": True, "id": note_id, "status": "trusted"}


@router.post("/api/knowledge/notes/{note_id}/reject")
def knowledge_note_reject(note_id: int):
    """Owner gate: delete a note."""
    from db import get_conn
    con = get_conn(); _ensure_notes(con)
    con.execute("DELETE FROM knowledge_notes WHERE id=?", (note_id,))
    con.commit()
    return {"ok": True, "id": note_id, "status": "deleted"}


# start the background auto-refresher when the plugin loads
_start_refresher()

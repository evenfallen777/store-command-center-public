"""RESEARCH LAB — resident "Research Geniuses" + the research-project pipeline.

The user proposes a project ("build a chicken coop", "start a laser-engraving
side business", "design a 3D-printed part"). A Genius takes it and produces
everything needed to actually DO it — WITHOUT writing code: an articulated plan,
step-by-step instructions, materials/tools lists with estimated costs, stats and
comparisons, illustrative images (searxng image search and, optionally, the
Studio image pipeline), links/references and safety notes. For coding projects
it articulates architecture/requirements/specs but writes NO code.

Pipeline (background thread, oracle-style; every LLM call rides the orchestrator
queue at priority=2 so it yields the GPU to user-facing work):
    plan → web search (searxng) → read pages → images → write report → file

Finished reports auto-file into the Library (app/library/research/) and pay the
Genius world-agents via the normal WORK_METRICS delta in world_sim
("research_done" counts rows here with status='done').

Toggles (settings keys, all surfaced in the Research tab):
    research_autostart    on  — start the pipeline the moment a project is proposed
    research_images       on  — fetch illustrative images via searxng image search
    research_gen_images   off — additionally GENERATE one hero image on the GPU
    research_auto_library on  — auto-file finished reports into the Library
    research_peer_review  on  — a SECOND Genius peer-reviews the draft; the author
                                revises before the report is filed (research_lab_deep)

The "after the report" features — peer review, Ask-the-Genius Q&A, Dig-deeper
versioned passes, and the 💡 ideas board — live in research_lab_deep.py.
"""
import json as _json
import re
import shutil
import threading
import time
from datetime import datetime
from html import escape as _hesc

import httpx
import requests

from deps import *          # get_conn, get_setting, orch, logger, _call_lmstudio
from config import DATA_DIR, STORE_BASE
import model_registry
from prompts import get_prompt

SEARX_URL = "http://127.0.0.1:8899"

# The resident Geniuses. Mirrored as world agents (world_defs.WORKER_POOL,
# job_class "research") so they live in The Company town; this list is the
# fallback roster if the world tables aren't around.
GENIUSES = [
    {"key": "w_res_1", "name": "Newton", "specialty": "Engineering & builds"},
    {"key": "w_res_2", "name": "Curie",  "specialty": "Science, safety & materials"},
    {"key": "w_res_3", "name": "Vinci",  "specialty": "Design, business & craft"},
]


# ── prompts (registered in app/prompts.py via ref=("research_lab", ...)) ──────
PLAN_SYS = (
    "You are a research director planning how to fully research a practical project "
    "for a DIY owner-operator. You NEVER write code. Given a project title and "
    "description, reply with STRICT JSON and nothing else:\n"
    '{"kind":"build"|"business"|"design"|"coding"|"other",'
    '"overview":"2-3 sentence framing of the project and what a great report must cover",'
    '"sections":["5-8 report section titles"],'
    '"search_queries":["4-6 focused web search queries that would surface guides, costs, specs and comparisons"],'
    '"image_queries":["2-4 image search queries for genuinely helpful illustrations (diagrams, examples, plans)"],'
    '"hero_image_prompt":"one image-generation prompt for a single illustrative hero image",'
    '"safety":["the 2-4 most important safety or risk topics to research"]}'
)

DIGEST_SYS = (
    "You are a research assistant. You are given the text of ONE web page fetched while "
    "researching a project. Extract only what is USEFUL for that project: concrete steps, "
    "materials/parts/tools with prices or cost figures, dimensions, specs, stats, "
    "comparisons, pitfalls and safety warnings. Reply with 5-12 terse bullet points, "
    "each a plain '- ' line with hard facts (keep numbers and units). If the page is "
    "irrelevant, reply exactly: IRRELEVANT"
)

REPORT_SYS = (
    "You are a Research Genius writing the definitive illustrated report that lets the "
    "owner actually BUILD/DO the project — without you writing any code. Using ONLY the "
    "research notes provided (plus common knowledge), write a complete markdown report:\n"
    "- Start with '## Overview' — what it is, why, key decisions.\n"
    "- '## Step-by-step guide' — numbered, concrete, tool-in-hand instructions.\n"
    "- '## Materials, parts & tools' — a markdown table with columns Item | Qty | Est. cost, "
    "and a rough total. Use real cost figures from the notes where available.\n"
    "- '## Stats & comparisons' — options weighed against each other (sizes, methods, "
    "products, prices) using the researched numbers.\n"
    "- '## Safety notes' — honest, specific warnings.\n"
    "- '## Tips & pitfalls'.\n"
    "For CODING projects: articulate architecture, requirements, data model and specs in "
    "prose/tables ONLY — absolutely no code, no pseudo-code, no shell commands.\n"
    "Where one of the available images would genuinely help, insert a line containing "
    "exactly [IMAGE:n] (n = the image number from the list). Do not invent images or "
    "URLs. No top-level '# ' title (it is added for you). Markdown only."
)


# ── schema ────────────────────────────────────────────────────────────────────
def _ensure_schema():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS research_projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, description TEXT DEFAULT '',
        kind TEXT DEFAULT '', status TEXT DEFAULT 'proposed',
        phase TEXT DEFAULT '', progress INTEGER DEFAULT 0, phase_note TEXT DEFAULT '',
        genius_key TEXT DEFAULT '', genius_name TEXT DEFAULT '',
        plan TEXT DEFAULT '', notes TEXT DEFAULT '', sources TEXT DEFAULT '',
        images TEXT DEFAULT '', report_md TEXT DEFAULT '',
        library_path TEXT DEFAULT '', error TEXT DEFAULT '',
        review TEXT DEFAULT '', version INTEGER DEFAULT 1,
        recur_days INTEGER DEFAULT 0, next_run_at TEXT,
        created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')),
        completed_at TEXT);
    CREATE TABLE IF NOT EXISTS research_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER,
        phase TEXT DEFAULT '', message TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')));
    CREATE TABLE IF NOT EXISTS research_qa (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER,
        question TEXT NOT NULL, answer TEXT DEFAULT '', status TEXT DEFAULT 'pending',
        genius_name TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')), answered_at TEXT);
    CREATE TABLE IF NOT EXISTS research_price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER,
        item TEXT NOT NULL, price REAL, kind TEXT DEFAULT 'check',
        captured_at TEXT DEFAULT (datetime('now')));
    CREATE TABLE IF NOT EXISTS research_price_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER,
        item TEXT NOT NULL, baseline REAL, price REAL, pct REAL,
        created_at TEXT DEFAULT (datetime('now')));
    """)
    # migrations for installs whose table predates these columns
    for stmt in ("ALTER TABLE research_projects ADD COLUMN review TEXT DEFAULT ''",
                 "ALTER TABLE research_projects ADD COLUMN version INTEGER DEFAULT 1",
                 "ALTER TABLE research_projects ADD COLUMN recur_days INTEGER DEFAULT 0",
                 "ALTER TABLE research_projects ADD COLUMN next_run_at TEXT"):
        try:
            conn.execute(stmt)
        except Exception:
            pass
    # restart-safety: pipeline/answer threads do not survive a restart
    conn.execute("UPDATE research_projects SET status='failed', "
                 "error='interrupted by a server restart — press Restart to run it again' "
                 "WHERE status='running'")
    conn.execute("UPDATE research_qa SET status='failed', "
                 "answer='interrupted by a server restart — ask again' "
                 "WHERE status='pending'")
    conn.commit()
    conn.close()

_ensure_schema()

_running: set = set()
_lock = threading.Lock()


# ── small helpers ─────────────────────────────────────────────────────────────
def _set(pid: int, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    conn = get_conn()
    conn.execute(f"UPDATE research_projects SET {cols}, updated_at=datetime('now') WHERE id=?",
                 (*fields.values(), pid))
    conn.commit()
    conn.close()


def _ev(pid: int, phase: str, message: str):
    conn = get_conn()
    conn.execute("INSERT INTO research_events (project_id,phase,message) VALUES (?,?,?)",
                 (pid, phase, message[:400]))
    conn.commit()
    conn.close()
    logger.info("research #%d [%s] %s", pid, phase, message[:160])


def _get(pid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM research_projects WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _cancelled(pid: int) -> bool:
    p = _get(pid)
    return (not p) or p["status"] == "cancelled"


def _toggle(key: str, default: str = "on") -> bool:
    return (get_setting(key, default) or default).lower() != "off"


def _searx(query: str, n: int = 5, categories: str = "") -> list:
    """searxng JSON search. For categories='images' rows carry img_src."""
    try:
        params = {"q": query, "format": "json", "language": "en"}
        if categories:
            params["categories"] = categories
        r = requests.get(f"{SEARX_URL}/search", params=params, timeout=25)
        r.raise_for_status()
        out = []
        for x in (r.json().get("results") or [])[:n]:
            out.append({"title": (x.get("title") or "")[:160], "url": x.get("url") or "",
                        "snippet": (x.get("content") or "")[:300],
                        "img_src": x.get("img_src") or ""})
        return out
    except Exception as e:
        logger.warning("research searx('%s') failed: %s", query[:60], e)
        return []


def _parse_json(raw: str):
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.DOTALL)
    if m:
        raw = m.group(1)
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not m:
        return None
    frag = m.group(0)
    for candidate in (frag, re.sub(r",\s*([}\]])", r"\1", frag)):
        try:
            return _json.loads(candidate)
        except Exception:
            continue
    return None


def _wait_task(tid: int, timeout: float = 900):
    end = time.time() + timeout
    while time.time() < end:
        p = orch.poll(tid)
        if p["status"] == "done":
            return p.get("result")
        if p["status"] in ("failed", "error", "cancelled", "not_found"):
            return None
        time.sleep(2)
    return None


def _llm(prompt_key: str, user: str, max_tokens: int = 1800, desc: str = ""):
    """One LLM turn through the shared queue, honouring the research model slot."""
    sysmsg = get_prompt(prompt_key)
    model = model_registry.for_task(prompt_key) or model_registry.resolve("research_model") or None
    tid = orch.submit_llm(lambda: _call_lmstudio(sysmsg, user, max_tokens=max_tokens),
                          desc=desc or f"research: {prompt_key}", model=model, priority=2)
    return _wait_task(tid)


# ── geniuses ──────────────────────────────────────────────────────────────────
def geniuses() -> list:
    """The Genius roster, enriched from the world (xp/level/state) when available."""
    out = []
    conn = get_conn()
    for g in GENIUSES:
        row = dict(g)
        try:
            w = conn.execute("SELECT name,xp,level,state,location,jobs_done FROM world_agents "
                             "WHERE key=?", (g["key"],)).fetchone()
            if w:
                row.update({"name": w["name"], "xp": w["xp"] or 0, "level": w["level"] or 1,
                            "state": w["state"] or "idle", "location": w["location"] or "home",
                            "jobs_done": w["jobs_done"] or 0})
        except Exception:
            pass
        n = conn.execute("SELECT COUNT(*) FROM research_projects WHERE genius_key=? AND status='done'",
                         (g["key"],)).fetchone()[0]
        a = conn.execute("SELECT COUNT(*) FROM research_projects WHERE genius_key=? AND "
                         "status IN ('proposed','running')", (g["key"],)).fetchone()[0]
        row.update({"projects_done": n, "projects_active": a})
        out.append(row)
    conn.close()
    return out


# keyword hints that route a project to the Genius whose specialty fits it
_SPECIALTY_HINTS = {
    "w_res_1": ("build", "diy", "construct", "wood", "metal", "weld", "engine", "repair",
                "install", "3d print", "machine", "fix", "garage", "workshop", "coop",
                "shed", "frame", "deck", "fence", "plumb", "wiring", "tool"),
    "w_res_2": ("science", "chemical", "safety", "material", "garden", "soil", "compost",
                "health", "food", "battery", "solar", "energy", "water", "test",
                "experiment", "grow", "plant", "weather", "insulat"),
    "w_res_3": ("design", "business", "brand", "logo", "sell", "market", "shop", "craft",
                "art", "decor", "website", "product", "etsy", "price", "customer",
                "side hustle", "studio", "layout"),
}
_KIND_TO_GENIUS = {"build": "w_res_1", "coding": "w_res_1",
                   "business": "w_res_3", "design": "w_res_3"}


def _assign_genius(title: str = "", desc: str = "", kind: str = "") -> dict:
    """Specialty match takes the project; ties go to the least-busy Genius."""
    gs = geniuses()
    text = f"{kind} {title} {desc}".lower()
    scores = {g["key"]: sum(1 for kw in _SPECIALTY_HINTS.get(g["key"], ()) if kw in text)
              for g in gs}
    if _KIND_TO_GENIUS.get(kind) in scores:
        scores[_KIND_TO_GENIUS[kind]] += 2
    return sorted(gs, key=lambda g: (-scores.get(g["key"], 0),
                                     g.get("projects_active", 0),
                                     g.get("projects_done", 0)))[0]


def _world_note(genius_key: str, genius_name: str, text: str, thought: str = "", mood: int = 0):
    """Additive world tie-in: journal line + town event + optional mood thought.
    Guarded — the research lab must never be able to break the world sim."""
    try:
        import world_defs
        import world_mood
        world_defs.log_agent(genius_key, genius_name, text)
        conn = get_conn()
        conn.execute("INSERT INTO world_events (agent_key,kind,text) VALUES (?,?,?)",
                     (genius_key, "research", f"🔬 {genius_name}: {text}"[:300]))
        if thought:
            world_mood.add_thought(conn.cursor(), genius_key, thought, mood, hours=8, unique=True)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("research world_note skipped: %s", e)


# ── images & report rendering (extracted to research_lab_media.py) ───────
# Re-export keeps this module's surface identical; the two image helpers
# lazy-import research_lab, so there is no import cycle.
from research_lab_media import (  # noqa: E402,F401
    RESEARCH_MEDIA, _download_image, _fetch_images, _generate_hero,
    render_report_html, _final_markdown, _file_to_library,
)


# ── the pipeline ──────────────────────────────────────────────────────────────
def _remember_research(pid, project, md):
    """Best-effort: stage a research finding into shared memory (Knowledge -> Memory for the
    owner to promote). Never raises into the pipeline."""
    try:
        import httpx
        try:
            title = project["title"]
        except Exception:
            title = ""
        summary = ""
        for line in (md or "").splitlines():
            t = line.strip().lstrip("#").strip()
            if t and not t.startswith("!") and len(t) > 30:
                summary = t
                break
        text = f"Research finding: {title}." + (f" {summary[:400]}" if summary else "") + f" (research #{pid})"
        httpx.post("http://127.0.0.1:8787/api/knowledge/remember",
                   json={"text": text[:1500], "agent": "research-lab", "tags": "research", "confidence": 0.6},
                   timeout=15)
    except Exception:
        pass


def _run_pipeline(pid: int):
    try:
        p = _get(pid)
        if not p:
            return
        title, desc = p["title"], p["description"]
        gname = p["genius_name"]
        _world_note(p["genius_key"], gname, f"Took on research project: “{title}”.",
                    thought="a fascinating new research project", mood=5)

        # 1) PLAN
        _set(pid, phase="plan", progress=5, phase_note="drafting the research plan")
        _ev(pid, "plan", f"{gname} is drafting the research plan…")
        raw = _llm("research_plan", f"PROJECT: {title}\n\nDESCRIPTION:\n{desc}",
                   max_tokens=1200, desc=f"research plan · {title[:40]}")
        plan = _parse_json(raw or "") or {}
        if not plan.get("search_queries"):
            plan.setdefault("search_queries", [title, f"{title} guide", f"{title} cost"])
        plan.setdefault("image_queries", [title])
        plan.setdefault("sections", [])
        plan.setdefault("safety", [])
        _set(pid, plan=_json.dumps(plan), kind=(plan.get("kind") or p["kind"] or "other"),
             progress=15)
        _ev(pid, "plan", f"plan ready — {len(plan['search_queries'])} searches, "
                         f"{len(plan.get('sections') or [])} sections")
        if _cancelled(pid):
            return

        # 2) WEB SEARCH
        _set(pid, phase="search", progress=20, phase_note="searching the web")
        hits, seen = [], set()
        queries = list(plan["search_queries"])[:6] + [f"{title} safety {s}" for s in plan["safety"][:1]]
        for q in queries:
            if _cancelled(pid):
                return
            found = _searx(q, 5)
            for h in found:
                if h["url"] and h["url"] not in seen:
                    seen.add(h["url"])
                    hits.append(h)
            _ev(pid, "search", f"“{q}” → {len(found)} results")
        hits = hits[:20]
        _set(pid, sources=_json.dumps(hits), progress=35,
             phase_note=f"{len(hits)} sources found")

        # 3) READ PAGES
        _set(pid, phase="read", progress=40, phase_note="reading the best sources")
        notes = []
        import library
        for h in hits:
            if len(notes) >= 5 or _cancelled(pid):
                break
            try:
                pg_title, text = library.fetch_readable_text(h["url"])
            except Exception:
                continue
            if len(text) < 400:
                continue
            digest = _llm("research_digest",
                          f"PROJECT: {title}\nPAGE: {pg_title} ({h['url']})\n\nCONTENT:\n{text[:6000]}",
                          max_tokens=700, desc=f"research read · {pg_title[:36]}")
            if digest and "IRRELEVANT" not in digest[:40]:
                notes.append({"url": h["url"], "title": pg_title, "digest": digest.strip()[:2500]})
                _ev(pid, "read", f"digested “{pg_title[:70]}”")
        _set(pid, notes=_json.dumps(notes), progress=58,
             phase_note=f"{len(notes)} sources digested")
        if _cancelled(pid):
            return

        # 4) IMAGES
        images = []
        if _toggle("research_images", "on"):
            _set(pid, phase="images", progress=60, phase_note="collecting illustrations")
            images = _fetch_images(pid, plan.get("image_queries") or [title])
        if _toggle("research_gen_images", "off") and plan.get("hero_image_prompt"):
            _generate_hero(pid, plan["hero_image_prompt"], images)
        _set(pid, images=_json.dumps(images), progress=68,
             phase_note=f"{len(images)} images ready")
        if _cancelled(pid):
            return

        # 5) WRITE THE REPORT
        _set(pid, phase="write", progress=72, phase_note=f"{gname} is writing the report")
        _ev(pid, "write", f"{gname} is synthesizing the report…")
        img_list = "\n".join(f"[IMAGE:{i+1}] {im['caption']}" for i, im in enumerate(images)) \
                   or "(no images available — do not insert [IMAGE] markers)"
        note_txt = "\n\n".join(f"SOURCE: {n['title']} ({n['url']})\n{n['digest']}" for n in notes)
        snip_txt = "\n".join(f"- {h['title']}: {h['snippet']}" for h in hits[:12] if h["snippet"])
        user = (f"PROJECT: {title}\nKIND: {plan.get('kind','other')}\n\nDESCRIPTION:\n{desc}\n\n"
                f"PLAN OVERVIEW: {plan.get('overview','')}\n"
                f"SECTIONS WANTED: {', '.join(plan.get('sections') or []) or '(your judgement)'}\n"
                f"SAFETY TOPICS: {', '.join(plan.get('safety') or []) or '(your judgement)'}\n\n"
                f"AVAILABLE IMAGES:\n{img_list}\n\n"
                f"RESEARCH NOTES (from fetched pages):\n{note_txt or '(none)'}\n\n"
                f"EXTRA SEARCH SNIPPETS:\n{snip_txt or '(none)'}")
        body = _llm("research_report", user, max_tokens=3600, desc=f"research report · {title[:40]}")
        if not body or len(body.strip()) < 200:
            raise RuntimeError("the model returned an empty/too-short report")
        body = re.sub(r"<think>.*?</think>", "", body, flags=re.DOTALL).strip()
        # resolve [IMAGE:n] markers → local media markdown; unresolved markers are dropped
        def _img_md(m):
            i = int(m.group(1)) - 1
            if 0 <= i < len(images):
                im = images[i]
                return f"![{im['caption']}](/api/research/media/{pid}/{im['file']})"
            return ""
        body = re.sub(r"\[IMAGE:(\d+)\]", _img_md, body)
        used = set(re.findall(r"/api/research/media/\d+/(\S+?)\)", body))
        gallery = [im for im in images if im["file"] not in used]
        if gallery:
            body += "\n\n## Illustrations\n\n" + "\n\n".join(
                f"![{im['caption']}](/api/research/media/{pid}/{im['file']})" for im in gallery)

        # 5b) PEER REVIEW — a second Genius critiques; the author revises
        if _toggle("research_peer_review", "on"):
            try:
                import research_lab_deep
                body = research_lab_deep.peer_review(pid, body)
            except Exception as e:
                _ev(pid, "review", f"peer review skipped: {str(e)[:120]}")
        _set(pid, progress=90, phase_note="compiling the final report")

        # 6) COMPILE + FILE
        p = _get(pid)
        md = _final_markdown(p, body, hits, images)
        lib_path = ""
        if _toggle("research_auto_library", "on"):
            try:
                lib_path = _file_to_library(p, md)
                _ev(pid, "file", f"filed into the Library at {lib_path}")
            except Exception as e:
                _ev(pid, "file", f"library filing failed: {str(e)[:120]}")
        _set(pid, status="done", phase="done", progress=100, report_md=md,
             library_path=lib_path, phase_note="report ready",
             completed_at=datetime.now().isoformat(timespec="seconds"))
        _ev(pid, "done", f"research complete — {len(notes)} sources read, {len(images)} images")
        _remember_research(pid, p, md)   # stage a finding for owner review
        # 7) MARKET — price baseline + materials → Money tab (research_lab_market)
        try:
            import research_lab_market
            research_lab_market.after_report(pid)
        except Exception as e:
            _ev(pid, "market", f"market step skipped: {str(e)[:120]}")
        _world_note(p["genius_key"], gname,
                    f"Published the research report “{p['title']}” "
                    f"({len(notes)} sources, {len(images)} illustrations).",
                    thought="published a research report", mood=6)
    except Exception as e:
        logger.error("research #%d failed: %s", pid, e)
        _set(pid, status="failed", error=str(e)[:300], phase_note="failed")
        _ev(pid, "error", str(e)[:200])
    finally:
        with _lock:
            _running.discard(pid)


def start_project(pid: int) -> bool:
    """Kick the pipeline for a project (daemon thread). False if already running."""
    with _lock:
        if pid in _running:
            return False
        _running.add(pid)
    _set(pid, status="running", phase="plan", progress=2, error="", phase_note="starting")
    threading.Thread(target=_run_pipeline, args=(pid,), daemon=True,
                     name=f"research-{pid}").start()
    return True


def is_running(pid: int) -> bool:
    with _lock:
        return pid in _running

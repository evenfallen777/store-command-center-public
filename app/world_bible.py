"""
The Company — The Bible / Field Manual.

Two layers share the world_bible table:

  • LEGACY TEACHINGS (kind study/law/lesson/revelation) — the themed lore layer:
    when the Republic's research/scout strategies are blessed, a scholar agent
    studies the topic and records a short teaching. BOOK.md remains a plain
    system-reference document that word() serves to the UI — agents are never
    told it is scripture.

  • OPERATIONAL TEACHINGS (kind 'ops') — a VERIFIED knowledge base about THIS
    system: where subsystems live, the directory layout, the knowledge graph,
    the capability catalog, recent changes. Requirement #1: they must help and
    never mislead. So every ops teaching is (a) harvested from GROUND TRUTH
    (the live tree / caps catalog / git history — deterministic text, no LLM
    invention), (b) gated by verify_refs() at store time — a fact whose
    referenced path/symbol/cap doesn't exist is REJECTED — and (c) re-checked
    on read + by revalidate(): a teaching whose reference disappears is flagged
    stale and silently dropped from what agents see. ops_context() closes the
    loop: world_strategy grounds research/scout commissions with it, and
    _study_llm feeds it to the scholar so new studies build on verified facts.

Teachings live in the world_bible table (schema self-managed in ensure()).
Decoupled from the world sim; writes only its own table + world_ops notes.
"""
import json, logging, re, subprocess, threading, time
from pathlib import Path
from deps import get_conn

logger = logging.getLogger("store")

BOOK_PATH = Path(__file__).parent.parent / "BOOK.md"
_ROOT = Path(__file__).resolve().parent.parent      # repo root (holds app/, static/, …)

OPS_KIND = "ops"                                     # operational, verified teachings
OPS_SWEEP_KEY = "world_bible_ops_sweep"              # timer sweep gate — default OFF
OPS_SWEEP_INTERVAL_KEY = "world_bible_ops_sweep_min"
OPS_SWEEP_INTERVAL_DEFAULT = 360                     # at most one sweep / 6h when enabled


# ── schema ────────────────────────────────────────────────────────────────────
def ensure(conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS world_bible (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            book       TEXT,                    -- related BOOK.md chapter, if any
            title      TEXT NOT NULL,
            verse      TEXT NOT NULL,           -- the teaching body
            author     TEXT,                    -- the agent who recorded it
            kind       TEXT DEFAULT 'study',    -- study | law | lesson | revelation | ops
            created_at TEXT DEFAULT (datetime('now'))
        );""")
        # ops-layer columns (schema lives HERE, never in db_schema.py). Additive
        # only — existing rows keep verified=0/stale=0 and are untouched.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(world_bible)").fetchall()}
        for col, ddl in (("source",      "TEXT"),                # repo-scan | git-log | mtime-scan | …
                         ("refs",        "TEXT"),                # JSON list of grounded references
                         ("verified",    "INTEGER DEFAULT 0"),   # every ref checked at store time
                         ("verified_at", "TEXT"),                # last successful/attempted check
                         ("stale",       "INTEGER DEFAULT 0")):  # a ref stopped verifying
            if col not in cols:
                conn.execute(f"ALTER TABLE world_bible ADD COLUMN {col} {ddl}")
        conn.commit()
    finally:
        if own:
            conn.close()


# ── the Word (BOOK.md → chapters) ────────────────────────────────────────────
def word():
    """Parse BOOK.md into an intro + level-2 chapters (### stay inside their chapter)."""
    try:
        text = BOOK_PATH.read_text(encoding="utf-8")
    except Exception as e:
        return {"title": "The Book", "intro": f"_The Word could not be read ({e})._", "chapters": []}

    lines = text.splitlines()
    title = "The Book"
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        lines = lines[1:]

    intro, chapters, cur = [], [], None
    for ln in lines:
        m = re.match(r"^##\s+(.*)$", ln)      # level-2 = a new chapter (not ### )
        if m:
            if cur:
                chapters.append(cur)
            heading = m.group(1).strip()
            cur = {"title": heading, "anchor": re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-"), "md": ""}
        elif cur is None:
            intro.append(ln)
        else:
            cur["md"] += ln + "\n"
    if cur:
        chapters.append(cur)
    return {"title": title, "intro": "\n".join(intro).strip(), "chapters": chapters}


# ── teachings ─────────────────────────────────────────────────────────────────
def add_teaching(title, verse, author=None, book=None, kind="study", conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        conn.execute("INSERT INTO world_bible (book,title,verse,author,kind) VALUES (?,?,?,?,?)",
                     (book, title, verse, author, kind))
        conn.commit()
    finally:
        if own:
            conn.close()


def teachings(limit=100, conn=None, kind=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        if kind:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM world_bible WHERE kind=? ORDER BY id DESC LIMIT ?",
                (kind, limit)).fetchall()]
        return [dict(r) for r in conn.execute(
            "SELECT * FROM world_bible ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    finally:
        if own:
            conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# OPERATIONAL TEACHINGS — the verified field manual
#
# Anti-mislead mechanism, in order:
#   1. SOURCING is ground truth only: facts are harvested by reading the live
#      tree / caps catalog / git history, and the verse text is DETERMINISTIC —
#      no LLM writes ops prose, so nothing can be invented.
#   2. STORE-TIME GATE: add_ops_teaching() refuses any teaching whose grounded
#      references don't ALL verify against reality right now.
#   3. FRESHNESS: every ops row carries source + verified_at; revalidate() (and
#      an on-read check inside ops_context()) flags a row stale the moment a
#      referenced path/symbol/cap disappears, and stale rows are never surfaced
#      to agents. The timer sweep is OFF by default (world_bible_ops_sweep).
# ═════════════════════════════════════════════════════════════════════════════

def _verify_ref(ref):
    """Does ONE grounded reference exist right now?
      path:<relpath>              — file/dir exists under the repo root
      symbol:<relpath>:<name>     — that file defines/assigns <name>
      cap:<cap_id>                — the world_caps catalog has that capability
    Anything else (or any error) is unverifiable → False."""
    try:
        kind, _, val = str(ref).partition(":")
        if kind == "path":
            if not val or val.startswith(("/", "~")) or ".." in val:
                return False
            return (_ROOT / val).exists()
        if kind == "symbol":
            rel, _, name = val.rpartition(":")
            if not rel or not name or rel.startswith(("/", "~")) or ".." in rel:
                return False
            f = _ROOT / rel
            if not f.is_file():
                return False
            text = f.read_text(encoding="utf-8", errors="replace")
            return bool(re.search(
                rf"^\s*(?:def|class)\s+{re.escape(name)}\b|^{re.escape(name)}\s*=",
                text, re.M))
        if kind == "cap":
            import world_caps
            return val in world_caps._CAP
    except Exception:
        return False
    return False


def verify_refs(refs):
    """(all_ok, missing) for a list of grounded references."""
    missing = [r for r in (refs or []) if not _verify_ref(r)]
    return (not missing, missing)


def add_ops_teaching(title, verse, refs, source="repo-scan", author=None, conn=None):
    """Store ONE operational teaching — only if EVERY grounded reference
    verifies against the live tree. A fact with a dead path/symbol/cap is
    REJECTED outright (never stored, never shown). Re-runs upsert by title so
    harvests refresh in place instead of piling up duplicates.
    Returns {"ok": True, "id", "updated"} or {"ok": False, "missing", "error"}."""
    refs = [str(r).strip() for r in (refs or []) if str(r).strip()]
    if not refs:
        return {"ok": False, "missing": [],
                "error": "an operational teaching needs at least one verifiable reference"}
    ok, missing = verify_refs(refs)
    if not ok:
        logger.info("ops teaching rejected (%s): unverified refs %s", title, missing)
        return {"ok": False, "missing": missing,
                "error": "unverified references — teaching rejected"}
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        row = conn.execute("SELECT id FROM world_bible WHERE kind=? AND title=?",
                           (OPS_KIND, title)).fetchone()
        if row:
            conn.execute(
                "UPDATE world_bible SET verse=?, author=?, source=?, refs=?, verified=1, "
                "stale=0, verified_at=datetime('now') WHERE id=?",
                (verse, author, source, json.dumps(refs), row["id"]))
            conn.commit()
            return {"ok": True, "id": row["id"], "updated": True}
        cur = conn.execute(
            "INSERT INTO world_bible (book,title,verse,author,kind,source,refs,verified,stale,verified_at) "
            "VALUES (NULL,?,?,?,?,?,?,1,0,datetime('now'))",
            (title, verse, author, OPS_KIND, source, json.dumps(refs)))
        conn.commit()
        return {"ok": True, "id": cur.lastrowid, "updated": False}
    finally:
        if own:
            conn.close()


# ── ground-truth harvesters (deterministic text; every claim carries a ref) ──
def _fact_layout():
    app_dir, routers, static = _ROOT / "app", _ROOT / "app" / "routers", _ROOT / "static"
    if not (app_dir.is_dir() and routers.is_dir()):
        return None
    n_mod = len(list(app_dir.glob("*.py")))
    n_routers = (len(list(routers.glob("*.py")))
                 + len([d for d in routers.iterdir()
                        if d.is_dir() and (d / "__init__.py").is_file()]))
    n_world = len(list(app_dir.glob("world_*.py")))
    verse = (f"Repo layout: backend Python lives in app/ ({n_mod} modules); HTTP endpoints in "
             f"app/routers/ ({n_routers} routers); the company/world-sim modules are the "
             f"app/world_*.py family ({n_world} files); the frontend is static/ "
             "(static/index.html + static/js/*.js); reference docs live in docs/ and the "
             "repo-root *.md files (BOOK.md, INDEX.md, WORLD.md).")
    refs = ["path:app", "path:app/routers", "path:static/js", "path:docs", "path:BOOK.md"]
    return ("Where the code lives", verse, refs, "repo-scan")


def _fact_media():
    verse = ("Media generation: video+audio pipelines live in app/services_media.py "
             "(diffusers video + the video→audio bridge), app/services_media_chain.py "
             "(multi-segment video chains) and app/services_media_audio.py "
             "(MusicGen/MMS-TTS/Stable Audio/ACE-Step) — all split out of app/services.py and "
             "re-exported by it. HTTP surface: app/routers/generate.py (images), "
             "app/routers/videos.py (video), app/routers/audio.py (music/voice).")
    refs = ["path:app/services_media.py", "path:app/services_media_chain.py",
            "path:app/services_media_audio.py", "path:app/services.py",
            "path:app/routers/generate.py", "path:app/routers/videos.py",
            "path:app/routers/audio.py"]
    return ("Where media generation lives", verse, refs, "repo-scan")


def _fact_3d_studio():
    verse = ("3D + Studio: the 3D pipeline (turntable render, SDXL hero image, image→mesh, "
             "Cults3D publish) is app/services_3d.py with routes in app/routers/models3d/; "
             "the AI Video Studio (storyboard → render → layered audio → assemble → export) "
             "is app/services_studio.py with routes in app/routers/studio.py.")
    refs = ["path:app/services_3d.py", "path:app/routers/models3d",
            "path:app/services_studio.py", "path:app/routers/studio.py"]
    return ("Where 3D and the Studio live", verse, refs, "repo-scan")


def _fact_gpu():
    verse = ("All GPU/LLM work rides the unified queue in app/orchestrator.py (`orch`): "
             "generations go through orch.submit*, and background LLM chatter is BORROW-ONLY "
             "via orch.llm_borrow — it returns None when the GPU is busy and callers degrade "
             "to canned text. Never call the LLM outside the queue; see app/GPU_QUEUE.md.")
    refs = ["path:app/orchestrator.py", "symbol:app/orchestrator.py:Orchestrator",
            "path:app/GPU_QUEUE.md"]
    return ("The GPU queue rule", verse, refs, "repo-scan")


def _fact_graph():
    if not (_ROOT / "graphify-out" / "graph.json").is_file():
        return None
    verse = ("The repo knowledge graph lives in graphify-out/ (graph.json + GRAPH_TREE.html + "
             "GRAPH_REPORT.md) and is served by app/routers/graph.py under /api/graph/* "
             "(stats, query, viz, rebuild). Use it to find how files/subsystems relate.")
    refs = ["path:graphify-out/graph.json", "path:app/routers/graph.py"]
    return ("Where the knowledge graph is", verse, refs, "repo-scan")


def _fact_knowledge():
    verse = ("The Knowledge Hub (app/routers/knowledge.py) federates the store's knowledge "
             "sources — the library markdown under app/library/ and the other existing "
             "indexes — behind one query endpoint with merged, cited hits.")
    refs = ["path:app/routers/knowledge.py", "path:app/library"]
    return ("Where to search the store's knowledge", verse, refs, "repo-scan")


def _fact_ops_gate():
    verse = ("Anything real-world (publishing, listing, spending) exits ONLY through the "
             "world_ops prayer gate: file with world_ops.pray(kind, …), executors register "
             "via world_ops.register_executor. The God Console endpoints live under "
             "app/routers/world_ops/.")
    refs = ["symbol:app/world_ops.py:pray", "symbol:app/world_ops.py:register_executor",
            "path:app/routers/world_ops"]
    return ("How actions are gated", verse, refs, "repo-scan")


def _fact_caps():
    try:
        import world_caps
    except Exception:
        return None
    groups = {}
    for c in world_caps.CATALOG:
        groups[c["group"]] = groups.get(c["group"], 0) + 1
    sample = [c["id"] for c in world_caps.CATALOG[:4]]
    verse = (f"The agent capability catalog (CATALOG in app/world_caps.py) exposes "
             f"{len(world_caps.CATALOG)} gated capabilities: "
             + ", ".join(f"{g} ({n})" for g, n in groups.items())
             + ". Each cap is toggle-gated (settings key world_cap_<id>) behind the "
               "world_caps_master switch; trigger via world_caps.invoke(cap_id, args, actor=…).")
    refs = (["symbol:app/world_caps.py:CATALOG", "symbol:app/world_caps.py:invoke"]
            + [f"cap:{cid}" for cid in sample])
    return ("The capability catalog", verse, refs, "repo-scan")


def _fact_recent():
    """Recent system changes — from git history when available (read-only
    subprocess, fail-soft), else from file mtimes. Only files that still exist
    are cited, so the refs always verify."""
    files, label, source = [], None, "git-log"
    try:
        out = subprocess.run(
            ["git", "-C", str(_ROOT), "log", "-n", "12", "--name-only",
             "--pretty=format:%ad %s", "--date=short"],
            capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            heads = []
            for ln in out.stdout.splitlines():
                if re.match(r"^\d{4}-\d{2}-\d{2} ", ln):
                    heads.append(ln.strip())
                elif ln.strip():
                    files.append(ln.strip())
            label = "latest commits: " + "; ".join(h[:80] for h in heads[:4])
    except Exception:
        pass
    if not files:
        source = "mtime-scan"
        cutoff = time.time() - 7 * 86400
        for base in ("app", "static/js"):
            d = _ROOT / base
            if d.is_dir():
                files += [str(p.relative_to(_ROOT)) for p in sorted(
                    d.rglob("*"), key=lambda p: -(p.stat().st_mtime if p.is_file() else 0))
                    if p.is_file() and p.suffix in (".py", ".js")
                    and p.stat().st_mtime >= cutoff]
        label = "modified in the last 7 days (file mtimes; git history unavailable)"
    seen = []
    for f in files:
        if f not in seen and (_ROOT / f).is_file():
            seen.append(f)
        if len(seen) >= 8:
            break
    if not seen:
        return None
    verse = (f"Recently changed files ({label}): " + ", ".join(seen)
             + ". Check these first when behavior differs from older notes.")
    return ("Recent system changes", verse, [f"path:{f}" for f in seen], source)


def refresh_ops(include_recent=True, conn=None):
    """Harvest ground-truth operational facts from the live tree and (up)store
    them through the verification gate. Deterministic — no LLM writes ops prose.
    include_recent=False skips the git/mtime recent-changes source."""
    harvesters = [_fact_layout, _fact_media, _fact_3d_studio, _fact_gpu,
                  _fact_graph, _fact_knowledge, _fact_ops_gate, _fact_caps]
    if include_recent:
        harvesters.append(_fact_recent)
    stored = updated = rejected = 0
    for fn in harvesters:
        try:
            cand = fn()
        except Exception:
            logger.exception("ops harvester %s failed", getattr(fn, "__name__", fn))
            continue
        if not cand:
            continue
        title, verse, refs, source = cand
        res = add_ops_teaching(title, verse, refs, source=source,
                               author="The Surveyor", conn=conn)
        if not res.get("ok"):
            rejected += 1
        elif res.get("updated"):
            updated += 1
        else:
            stored += 1
    return {"stored": stored, "updated": updated, "rejected": rejected}


def revalidate(conn=None):
    """Re-check EVERY operational teaching against the live tree. A teaching
    whose reference disappeared is flagged stale (dropped from ops_context and
    badged in the UI) — a moved file can never mislead an agent. A stale row
    whose reference comes back is restored. Runs on demand from the God Console;
    the timer sweep (maybe_sweep) is OFF unless world_bible_ops_sweep is on."""
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT id, refs, verified, stale FROM world_bible WHERE kind=?",
            (OPS_KIND,)).fetchall()]
        checked = went_stale = restored = fresh = 0
        for r in rows:
            checked += 1
            try:
                refs = json.loads(r.get("refs") or "[]")
            except Exception:
                refs = []
            ok, _missing = verify_refs(refs)
            if ok and refs:
                if r.get("stale") or not r.get("verified"):
                    conn.execute("UPDATE world_bible SET verified=1, stale=0, "
                                 "verified_at=datetime('now') WHERE id=?", (r["id"],))
                    restored += 1
                else:
                    conn.execute("UPDATE world_bible SET verified_at=datetime('now') "
                                 "WHERE id=?", (r["id"],))
                    fresh += 1
            else:
                if not r.get("stale"):
                    went_stale += 1
                conn.execute("UPDATE world_bible SET stale=1, verified_at=datetime('now') "
                             "WHERE id=?", (r["id"],))
        conn.commit()
        return {"checked": checked, "fresh": fresh, "stale": went_stale, "restored": restored}
    finally:
        if own:
            conn.close()


_SWEEP = {"last": 0.0}


def maybe_sweep(now=None):
    """Timer hook for the world_auto tick — a periodic revalidate(), gated OFF
    by default (world_bible_ops_sweep, like every other world daemon). Pure
    disk stats + one SQLite pass; no GPU, no LLM, no network."""
    try:
        from deps import get_setting
        if str(get_setting(OPS_SWEEP_KEY, "0")).lower() not in ("1", "true", "yes", "on"):
            return None
        now = now or time.time()
        try:
            interval = max(30, int(get_setting(OPS_SWEEP_INTERVAL_KEY,
                                               str(OPS_SWEEP_INTERVAL_DEFAULT)))) * 60
        except Exception:
            interval = OPS_SWEEP_INTERVAL_DEFAULT * 60
        if now - _SWEEP["last"] < interval:
            return None
        _SWEEP["last"] = now
        return revalidate()
    except Exception:
        logger.exception("world_bible ops sweep failed")
        return None


def ops_context(topic=None, limit=6):
    """THE LOOP-CLOSER: compact, VERIFIED operational notes for injection into
    agent planning/prompts. Only verified, non-stale ops teachings are eligible,
    ranked by topic keyword overlap (recency otherwise) — and each row's refs
    are re-checked ON READ (cheap stat/grep): a teaching that went stale since
    its last sweep is flagged and silently skipped, so an agent is never handed
    a dead path. Returns '' when nothing verified exists (callers no-op)."""
    conn = get_conn()
    try:
        ensure(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM world_bible WHERE kind=? AND verified=1 AND COALESCE(stale,0)=0 "
            "ORDER BY id DESC LIMIT 40", (OPS_KIND,)).fetchall()]
        if topic:
            toks = set(re.findall(r"[a-z0-9]{3,}", str(topic).lower()))
            def _score(r):
                text = f"{r['title']} {r['verse']}".lower()
                return sum(1 for t in toks if t in text)
            rows.sort(key=lambda r: (-_score(r), -r["id"]))
        out = []
        for r in rows:
            if len(out) >= max(1, int(limit)):
                break
            try:
                refs = json.loads(r.get("refs") or "[]")
            except Exception:
                refs = []
            ok, _missing = verify_refs(refs)
            if not (ok and refs):
                try:      # flag it so the sweep/UI see it too; skip regardless
                    conn.execute("UPDATE world_bible SET stale=1, "
                                 "verified_at=datetime('now') WHERE id=?", (r["id"],))
                    conn.commit()
                except Exception:
                    pass
                continue
            out.append(f"• {r['title']}: {r['verse']}")
        return "\n".join(out)
    finally:
        conn.close()


# ── research executor: a blessed research/scout prayer → a new teaching ──────
_STUDY_SYS = (
    "You are a scholar in The Company — a small pixel-art civilization that survives on "
    "the web by making and selling digital goods (art, music, 3D, products). You study a "
    "topic and record a SHORT practical note for the company's shared knowledge base.\n"
    "Respond with ONLY the teaching: 2-4 plain sentences of concrete, practical wisdom the "
    "workers can act on. Do NOT show any reasoning. Do NOT write 'Thinking Process', "
    "analysis, headings, bullet points, numbered lists, or any preamble — output the "
    "finished teaching text and nothing else."
)


def _clean_teaching(text):
    """Strip reasoning-model scratchpad so only the finished teaching remains."""
    if not text:
        return ""
    t = text.strip()
    # <think> is handled upstream; this catches models that emit visible reasoning.
    if "thinking process" in t.lower() or "**" in t or t.count("\n") > 5:
        paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
        bad = ("thinking", "analyze", "analysis", "step", "role", "task", "topic",
               "output", "draft", "okay", "let me", "first", "here's", "here is",
               "the request", "constraint")
        prose = [p for p in paras
                 if re.match(r'^["\'“A-Za-z]', p) and not re.match(r"^\d+[.)]", p)
                 and "**" not in p and len(p) > 45
                 and not p.lower().startswith(bad)]
        if prose:
            t = prose[-1]                     # the finished answer usually comes last
    t = re.sub(r"\s+", " ", t).strip().strip('"“”').strip()
    # strip a leading label like "Revised Draft:", "Final answer:", "Teaching:"
    t = re.sub(r"^(revised\s+draft|final(\s+\w+)?|draft|teaching|answer|response|summary|"
               r"output|result|note|lesson|the teaching|my teaching)\s*:\s*", "", t, flags=re.I).strip().strip('"“”').strip()
    # drop leading reasoning lead-in sentences ("Let's…", "Okay, so…", etc.)
    cue = re.compile(r"^(let'?s|let me|let us|okay|ok|so|first|i'?ll|i will|we could|"
                     r"we can|we should|here'?s|now|alright|to answer|combining|thinking)\b", re.I)
    sents = re.split(r"(?<=[.!?])\s+", t)
    while len(sents) > 1 and cue.match(sents[0]):
        sents.pop(0)
    t = " ".join(sents).strip()
    # single-sentence cue clause ending in a colon: "Okay, so the point is: X" → "X"
    if cue.match(t):
        m = re.match(r"^[^:]{0,64}:\s+(.+)$", t)
        if m:
            t = m.group(1)
    return t.strip().strip('"“”').strip()


def _nearest_chapter(topic):
    try:
        chs = word()["chapters"]
        t = (topic or "").lower()
        for ch in chs:
            for w in re.findall(r"[a-z]{4,}", ch["title"].lower()):
                if w in t:
                    return ch["title"]
    except Exception:
        pass
    return None


def _study_llm(topic, detail, timeout=150):
    """Run the LLM study in a watched thread. Returns cleaned text, or None if the
    GPU is jammed and it doesn't finish in `timeout`s (so we never hang forever)."""
    box = {}

    def work():
        try:
            from swarm import _turn
            from deps import ENHANCE_MODEL
            user = f"Topic to study: {topic}\nContext: {detail or '(none)'}\n"
            # ground the scholar in VERIFIED facts about our own system — the LLM
            # may summarize/build on these but is told never to invent paths.
            try:
                ops = ops_context(topic=topic, limit=3)
            except Exception:
                ops = ""
            if ops:
                user += ("Verified facts about our own system (ground truth — use them, "
                         "never contradict them, never invent file paths):\n" + ops + "\n")
            user += "Write the teaching."
            box["v"] = _clean_teaching(_turn(ENHANCE_MODEL, _STUDY_SYS, user, max_tokens=500))
        except Exception as e:
            box["e"] = str(e)

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout=timeout)
    if "e" in box:
        logger.info("bible study LLM error (%s); using fallback", box["e"])
    v = box.get("v")
    if v and len(v) > 900:
        v = v[:900].rsplit(".", 1)[0] + "."
    return v


def _do_study(topic, detail, author):
    try:
        verse = _study_llm(topic, detail)
        if not verse:
            verse = (f"{author or 'A scholar'} studied “{topic}”. {detail or ''} "
                     "The lesson is recorded for the company to act upon.").strip()
        # AI Shield: don't let a poisoned study inject instructions into scripture
        try:
            import aishield
            scan = aishield.scan_injection(f"{topic}\n{detail or ''}\n{verse}")
            if scan["risk"] == "high":
                import world_ops as wo
                wo.note(f"🤖 AI Shield quarantined a study on “{topic[:40]}” — prompt-injection detected "
                        f"({', '.join(scan['tags'])}).", kind="warning", from_agent="AI Shield")
                logger.warning("bible study quarantined (injection): %s", scan["tags"])
                return
        except Exception:
            pass
        add_teaching(topic, verse, author=author, book=_nearest_chapter(topic), kind="study")
        try:
            import world_ops as wo
            wo.note(f"📖 {author or 'A scholar'} recorded a new teaching: “{topic[:48]}”.",
                    kind="praise", from_agent=author)
        except Exception:
            pass
    except Exception:
        logger.exception("bible study failed for topic %r", topic)


def _research_executor(conn, prayer):
    """Blessed research/scout → study in the background and grow the Bible."""
    topic = (prayer["title"] or "Study").replace("Research ", "").strip()
    threading.Thread(target=_do_study,
                     args=(topic, prayer.get("detail"), prayer.get("agent_name")),
                     daemon=True).start()
    return "study underway — the teaching will appear in the Bible"


def register():
    import world_ops as wo
    wo.register_executor("library_research", _research_executor)


register()

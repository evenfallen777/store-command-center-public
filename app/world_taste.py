"""
The Company — the GOD-TASTE model (real online machine learning).

The town learns what YOU like. Three signal sources feed a labelled example set:

  • your prayer verdicts    — every bless/deny you make by hand (+1 / -1)
  • your design reviews     — the store's approve/reject pipeline (+1 / -1)
  • your own creations      — what god makes with god's own hands is what god
                              likes (+0.7 exemplars)
  • the audience's verdict  — social post performance (views/likes vs the
                              platform's own median) fed by app/social_scheduler
                              .refresh_analytics via add_or_update_example

Each example's text is EMBEDDED (LM Studio embeddings via the store's own LLM
proxy — semantic vectors, so "cozy cabin poster" generalises to "warm cottage
print" without keyword overlap). `score(text)` is a cosine k-NN over those
vectors → predicted approval 0..1. It's honest, incremental ML: every decision
you make retrains the town's judgement, no heavyweight stack required.

Used by:
  • world_ops     — Boss Kane endorses prayers using this score; the sweep only
                    auto-runs work the model thinks you'd bless
  • world_auto    — the studio picks creative prompts you're likely to love
  • the agents    — a blessed piece gives its creator a mood boost + journal
                    entry; a denial stings (they feel the verdict)
"""
import json
import logging
import math

import httpx
from deps import get_setting

logger = logging.getLogger("store")

_LOCAL = "http://127.0.0.1:8787"
EMBED_MODEL_DEFAULT = "text-embedding-nomic-embed-text-v1.5"
_ensured = False


def ensure(c):
    global _ensured
    if _ensured:
        return
    c.execute("""
    CREATE TABLE IF NOT EXISTS world_taste (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skey TEXT UNIQUE,              -- source key, e.g. prayer:12 / design:5 / gen:88
        kind TEXT,
        text TEXT,
        label REAL,                    -- -1 rejected … +1 approved
        source TEXT,                   -- god_verdict | design_review | god_own_work
        vec TEXT,                      -- embedding json (null if embed unavailable)
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    _ensured = True


def _embed(text):
    try:
        model = get_setting("taste_embed_model", EMBED_MODEL_DEFAULT)
        r = httpx.post(f"{_LOCAL}/api/llm/v1/embeddings",
                       json={"model": model, "input": text[:600]}, timeout=20)
        return r.json()["data"][0]["embedding"]
    except Exception:
        return None


def add_example(c, skey, kind, text, label, source):
    """Record one labelled example (idempotent by skey). Returns 1 if added."""
    ensure(c)
    text = (text or "").strip()
    if not text:
        return 0
    if c.execute("SELECT 1 FROM world_taste WHERE skey=?", (skey,)).fetchone():
        return 0
    vec = _embed(text)
    c.execute("INSERT OR IGNORE INTO world_taste (skey,kind,text,label,source,vec) VALUES (?,?,?,?,?,?)",
              (skey, kind, text[:600], float(label), source, json.dumps(vec) if vec else None))
    return 1


def add_or_update_example(c, skey, kind, text, label, source):
    """Like add_example, but REFRESHES the label when the skey already exists.
    Built for social analytics (app/social_scheduler.py): a post's performance
    label drifts as views/likes accumulate, so the same skey gets re-scored on
    every refresh. The stored embedding is kept — the text is stable, only the
    verdict moves. Returns 1 if anything was added/changed."""
    ensure(c)
    row = c.execute("SELECT id, label FROM world_taste WHERE skey=?", (skey,)).fetchone()
    if not row:
        return add_example(c, skey, kind, text, label, source)
    if abs(float(row["label"]) - float(label)) < 1e-9:
        return 0
    c.execute("UPDATE world_taste SET label=? WHERE skey=?", (float(label), skey))
    return 1


def sync(c):
    """Harvest new labelled examples from everywhere god's judgement shows up."""
    ensure(c)
    n = 0
    # 1) hand-made prayer verdicts (auto-sweep and duplicate rejections are NOT god's taste)
    for r in c.execute("SELECT id,kind,title,detail,status,god_comment FROM world_prayers "
                       "WHERE status IN ('done','approved','rejected','failed')").fetchall():
        gc = (r["god_comment"] or "")
        if gc.startswith("auto") or gc.startswith("duplicate"):
            continue
        label = 1.0 if r["status"] in ("done", "approved", "failed") else -1.0
        n += add_example(c, f"prayer:{r['id']}", r["kind"],
                         f"{r['title']}. {r['detail'] or ''}", label, "god_verdict")
    # 2) the design review pipeline (nsfw designs are excluded — Private-Studio
    #    rejections are wired in explicitly by app/nsfw.py as their own deny
    #    examples, and nsfw work must not skew the SFW studio's taste)
    try:
        for r in c.execute("SELECT id,status,prompt,product_type FROM designs "
                           "WHERE status IN ('approved','published','rejected') "
                           "AND COALESCE(nsfw,0)=0").fetchall():
            label = 1.0 if r["status"] in ("approved", "published") else -1.0
            n += add_example(c, f"design:{r['id']}", "design",
                             f"{r['prompt'] or ''} ({r['product_type'] or 'design'})", label, "design_review")
    except Exception:
        pass
    # 3) god's own creations — strong positive exemplars
    try:
        for r in c.execute("SELECT id,prompt FROM generations WHERE status='done' "
                           "AND (source IS NULL OR source!='world_auto') "
                           "AND COALESCE(nsfw,0)=0 "
                           "ORDER BY id DESC LIMIT 400").fetchall():
            n += add_example(c, f"gen:{r['id']}", "image", r["prompt"] or "", 0.7, "god_own_work")
    except Exception:
        pass
    if n:
        c.commit()
        logger.info("world_taste: learned %d new examples", n)
    return n


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (na * nb)


def score(c, text, kind=None, k=7):
    """Predicted approval 0..1 for a candidate. Embedding k-NN when trained,
    keyword-overlap fallback when the embedder is down, 0.5 when cold."""
    ensure(c)
    text = (text or "").strip()
    if not text:
        return 0.5
    rows = c.execute("SELECT label, vec, text FROM world_taste").fetchall()
    if not rows:
        return 0.5
    vecs = [(float(r["label"]), json.loads(r["vec"])) for r in rows if r["vec"]]
    if len(vecs) >= 4:
        v = _embed(text)
        if v:
            sims = sorted(((_cos(v, ev), lab) for lab, ev in vecs), reverse=True)[:k]
            wsum = sum(max(0.0, s) for s, _ in sims) or 1e-9
            est = sum(max(0.0, s) * lab for s, lab in sims) / wsum
            raw = max(0.0, min(1.0, (est + 1) / 2))
            # CONFIDENCE ramp: nomic cosines run ~0.65+ for genuinely-similar art
            # prompts and ~0.5 for unrelated text — without this, anything
            # English scored "liked". Unfamiliar → pulled toward neutral 0.5,
            # so only content actually near god's judged work gets a verdict.
            conf = max(0.0, min(1.0, (sims[0][0] - 0.52) / 0.12))
            return 0.5 + (raw - 0.5) * conf
    # fallback: crude token overlap vs positive/negative examples
    toks = set(text.lower().split())
    pos = neg = 0.0
    for r in rows:
        ov = len(toks & set((r["text"] or "").lower().split()))
        if not ov:
            continue
        if float(r["label"]) >= 0:
            pos += ov
        else:
            neg += ov
    if pos + neg == 0:
        return 0.5
    return max(0.0, min(1.0, pos / (pos + neg)))


def score_band(c, text, kind=None, k=7):
    """score() wrapped in the Jesus/Satan hi-lo band (world_duality):
    {best, worst, expected, base}. best = the optimistic (Jesus) anchor,
    worst = the adversarial (Satan) anchor, expected = the calibrated middle.
    Falls back to a degenerate band (all values == the plain score) when the
    lieutenants are off or the duality helper is unavailable — never errors."""
    s = score(c, text, kind, k)
    try:
        import world_duality
        return world_duality.band(s)
    except Exception:
        return {"best": s, "worst": s, "expected": s, "base": s,
                "sides": {"jesus": False, "satan": False}}


def stats(c):
    ensure(c)
    row = c.execute("SELECT COUNT(*) n, SUM(CASE WHEN label>0 THEN 1 ELSE 0 END) pos, "
                    "SUM(CASE WHEN vec IS NOT NULL THEN 1 ELSE 0 END) emb FROM world_taste").fetchone()
    return {"examples": row["n"] or 0, "positive": row["pos"] or 0,
            "embedded": row["emb"] or 0, "trained": (row["emb"] or 0) >= 4}

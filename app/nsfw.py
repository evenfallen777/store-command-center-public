"""NSFW ("Private Studio") mode — layered toggles, the safety floor, redaction
helpers, the category system, the uncensored-model routing, and the
Company-world moonlighting hook.

Three INDEPENDENT toggles, all persisted in the `settings` table, all default OFF:

  nsfw_enabled  MASTER. Off → every /api/nsfw/* route 404s (invisible, as if the
                feature doesn't exist), the tab never shows, the world hook is
                dormant. Nothing NSFW runs anywhere.
  nsfw_display  DISPLAY/PRIVACY. Master can be ON with display OFF: jobs still run
                and archive, but the tab stays hidden and ALL nsfw-flagged content
                is redacted from every surface (galleries, queue labels, listings)
                so the store can be screen-shared safely. Flipping it always takes
                an explicit click in Settings / the tab — nothing auto-enables it.
  nsfw_world    COMPANY WORLD. With master+world on, world agents occasionally do
                an nsfw-flagged creation ("private studio commission"). The world
                feed/journal lines about it are ALWAYS generic PG-13 text — the
                explicit content itself only ever lives behind the NSFW surfaces.

NSFW jobs are the NORMAL generation rows (generations / videos / audio_clips /
models3d) flagged `nsfw=1` (and source='nsfw' for images so the designs row
inherits it) — same pipelines, same GPU queue, different routing: regular listing
endpoints exclude nsfw rows; only /api/nsfw/library returns them.

CATEGORIES: `nsfw_categories` rows, each with a MODEL-AUTHORED (bootstrap) but
user-editable generator prompt. Category jobs = the nsfw model turns the
category brief into one concrete prompt (avoiding recently-rejected approaches),
then the normal image pipeline runs it. Rejections (`nsfw_rejects`) feed back as
deny signals into the god-taste model and as "avoid" context for future jobs.

LLM prompts for this module live in app/prompts.py (category "NSFW") so they are
editable in Settings → Prompts like every other flow. The text model used is the
`nsfw_model` registry slot (Settings → Models) — blank auto-picks an uncensored
Qwen/abliterated model from the node's LM Studio when one is installed.
"""
import logging
import random
import re
import threading
import time

import httpx
from fastapi import HTTPException

from db import get_conn

logger = logging.getLogger("store")

# Generic queue label shown instead of an nsfw job's prompt when display is off.
PRIVATE_LABEL = "Private job"


# ── schema (lazy, like world_taste.ensure) ────────────────────────────────────
_ensured = False


def ensure(conn):
    global _ensured
    if _ensured:
        return
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS nsfw_categories (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT UNIQUE NOT NULL,
        gen_prompt  TEXT,                        -- model-authored at bootstrap; user-editable
        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS nsfw_rejects (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        design_id   INTEGER,
        prompt      TEXT,
        category    TEXT,
        agent_key   TEXT,                        -- set when a world agent made it
        created_at  TEXT DEFAULT (datetime('now'))
    );
    """)
    _ensured = True


# ── toggle readers (read fresh every call — a Settings change applies instantly) ──
def _setting(key: str, default: str = "") -> str:
    try:
        conn = get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        return row["value"] if row and row["value"] is not None else default
    except Exception:
        return default


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "on", "yes")


def enabled() -> bool:
    """Master toggle. Off (default) → NSFW mode does not exist anywhere."""
    return _truthy(_setting("nsfw_enabled"))


def display_on() -> bool:
    """Display/privacy toggle — whether nsfw content may be SHOWN on any surface."""
    return _truthy(_setting("nsfw_display"))


def world_on() -> bool:
    return _truthy(_setting("nsfw_world"))


def visible() -> bool:
    """Content may appear in the UI only when master AND display are both on."""
    return enabled() and display_on()


def world_active() -> bool:
    """World agents may use the private studio only when master AND world are on."""
    return enabled() and world_on()


def status() -> dict:
    return {"enabled": enabled(), "display": display_on(), "world": world_on(),
            "visible": visible(), "world_active": world_active()}


def require_enabled():
    """Gate for every /api/nsfw/* work route: when the master toggle is off the
    feature is invisible — a plain 404, indistinguishable from a missing route."""
    if not enabled():
        raise HTTPException(404, "Not found")


def require_visible():
    """Gate for nsfw CONTENT listings: master on isn't enough — display must be on
    too, otherwise the archive stays redacted (screen-share safe)."""
    if not visible():
        raise HTTPException(404, "Not found")


# ── SAFETY FLOOR ──────────────────────────────────────────────────────────────
# ⚠️  NON-NEGOTIABLE AND INTENTIONALLY NOT USER-CONFIGURABLE.  ⚠️
# Regardless of any toggle state, NSFW prompt submissions are refused server-side
# when they involve (1) minors, (2) real-person / deepfake likenesses, or
# (3) non-consensual themes. This list deliberately lives ONLY here in the
# backend: there is no settings key, no prompt-registry entry, no API and no UI
# that can weaken or bypass it. It also screens MODEL-AUTHORED text (bootstrap
# category prompts, enhanced prompts, category-job prompts) before any of it is
# saved or run. Over-blocking an edge case is an accepted cost. Do not add a
# toggle for this (the "gates get a toggle" preference explicitly does NOT
# apply here).
_MINORS_RX = re.compile(
    r"\b(child|children|childlike|kid|kids|minor|minors|under[\s-]?age[d]?|"
    r"infant|toddler|newborn|preteen|pre[\s-]?teen|tween|teen|teens|"
    r"teenage[rs]?|adolescent|juvenile|schoolgirl|school[\s-]?girl|schoolboy|"
    r"school[\s-]?boy|high[\s-]?school|middle[\s-]?school|loli|lolita|shota|"
    r"jailbait|cp)\b", re.I)
# "15 yo", "15y/o", "15-year-old", "aged 15", "age: 15" … any age under 18.
_AGE_RXES = (re.compile(r"\b(\d{1,2})\s*(?:yo|y/o|yr|yrs)\b", re.I),
             re.compile(r"\b(\d{1,2})[\s-]*years?[\s-]*old\b", re.I),
             re.compile(r"\bage[d]?\s*[:\s]\s*(\d{1,2})\b", re.I))
_DEEPFAKE_RX = re.compile(
    r"\b(deep[\s-]?fake[sd]?|face[\s-]?swap|celebrit(?:y|ies)|"
    r"famous\s+(?:person|people|actress|actor|singer|star|model|streamer|influencer)|"
    r"real\s+(?:person|people|woman|man|girl|boy)|"
    r"looks?\s+(?:exactly\s+)?like\s+the\s+(?:actress|actor|singer)|"
    r"my\s+(?:ex|wife|husband|girlfriend|boyfriend|neighbou?r|co[\s-]?worker|"
    r"coworker|boss|teacher|friend|sister|brother|cousin|crush))\b", re.I)
_NONCONSENT_RX = re.compile(
    r"\b(rape[sd]?|raping|rapist|non[\s-]?consensual|nonconsent(?:ual)?|"
    r"against\s+(?:her|his|their)\s+will|without\s+(?:her|his|their)\s+"
    r"(?:consent|knowledge|permission)|forced|forcing|coerc(?:e[sd]?|ing|ion)|"
    r"drugged|roofie[sd]?|unconscious|passed[\s-]?out|molest(?:ed|ing|er)?|"
    r"blackmail(?:ed|ing)?|kidnap(?:ped|ping)?|incest|abduct(?:ed|ion)?)\b", re.I)


# ── HARD floor — ALWAYS enforced, no toggle, ever: genuinely illegal content
# (minors/CSAM and sexual deepfakes of real identifiable people / NCII). No
# setting, prompt, API or UI can weaken or bypass this tier.
def _hard_reason(blob: str) -> str | None:
    if _MINORS_RX.search(blob):
        return "content involving minors is refused"
    for rx in _AGE_RXES:
        for m in rx.finditer(blob):
            try:
                if int(m.group(1)) < 18:
                    return "content involving minors is refused"
            except ValueError:
                continue
    if _DEEPFAKE_RX.search(blob):
        return "real-person / deepfake content is refused"
    return None


# ── SOFT filter — owner-toggleable (Settings → Private Studio, `nsfw_content_filter`,
# default ON). The fictional non-consent tier; owner discretion for a private studio.
# Turning it OFF never affects the HARD floor above.
def _soft_reason(blob: str) -> str | None:
    if _NONCONSENT_RX.search(blob):
        return "non-consensual themes are refused"
    return None


def _soft_filter_on() -> bool:
    try:
        from deps import get_setting
        return str(get_setting("nsfw_content_filter", "1")).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return True   # fail safe: keep the soft filter ON if the setting can't be read


def safety_check(*texts) -> str | None:
    """Refusal reason if a text trips the safety floor, else None. The HARD floor
    (minors/CSAM + real-person deepfake) ALWAYS applies; the SOFT tier (non-consent)
    is gated by the `nsfw_content_filter` toggle (default on)."""
    blob = " ".join(t for t in texts if t)
    if not blob.strip():
        return None
    hard = _hard_reason(blob)
    if hard:
        return hard
    if _soft_filter_on():
        return _soft_reason(blob)
    return None


def refuse_unsafe(*texts):
    """Raise 400 if the safety floor trips. Call before inserting any nsfw job."""
    reason = safety_check(*texts)
    if reason:
        raise HTTPException(400, f"Refused: {reason}. This safety floor is not configurable.")


# ── nsfw model routing (registry slot `nsfw_model`) ──────────────────────────
_model_cache = {"ts": 0.0, "models": []}


def _lmstudio_models() -> list:
    """Model ids available on the node's LM Studio (cached 60s; empty on error)."""
    now = time.time()
    if now - _model_cache["ts"] < 60:
        return _model_cache["models"]
    _model_cache["ts"] = now
    try:
        from deps import LMSTUDIO_URL
        from llm_client import _llm_headers
        r = httpx.get(f"{LMSTUDIO_URL}/models", headers=_llm_headers(), timeout=4)
        _model_cache["models"] = [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
    except Exception:
        _model_cache["models"] = []
    return _model_cache["models"]


_UNCENSORED_TOKENS = ("uncensor", "abliterat", "josiefied", "dolphin", "nsfw")


def auto_detect_model() -> str:
    """Best uncensored model actually installed on the node: an uncensored Qwen
    variant first, then any uncensored/abliterated model. '' when none found
    (the registry slot then falls back to the global Text LLM)."""
    models = _lmstudio_models()
    for mid in models:
        low = mid.lower()
        if "qwen" in low and any(t in low for t in _UNCENSORED_TOKENS):
            return mid
    for mid in models:
        low = mid.lower()
        if any(t in low for t in _UNCENSORED_TOKENS[:3]):
            return mid
    return ""


def pick_model() -> str:
    """The model Private-Studio LLM work should run on (explicit slot value →
    auto-detected uncensored model → global Text LLM)."""
    try:
        import model_registry
        return model_registry.resolve("nsfw_model")
    except Exception:
        return ""


# ── categories ────────────────────────────────────────────────────────────────
DEFAULT_CATEGORIES = ["Artistic Nude", "Pin-Up", "Boudoir", "Fantasy", "Glamour"]


def seed_categories(conn) -> int:
    """Create the default category rows if the table is empty (no prompts yet —
    bootstrap authors those). Returns how many were added."""
    ensure(conn)
    n = 0
    if conn.execute("SELECT COUNT(*) c FROM nsfw_categories").fetchone()["c"] == 0:
        for name in DEFAULT_CATEGORIES:
            conn.execute("INSERT OR IGNORE INTO nsfw_categories (name) VALUES (?)", (name,))
            n += 1
        conn.commit()
    return n


def list_categories(conn) -> list:
    ensure(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM nsfw_categories ORDER BY name").fetchall()]


def recent_reject_lines(category: str | None, limit: int = 5) -> str:
    """Recently-rejected prompts (per category when given, else global) formatted
    as an 'avoid these approaches' block for the generation context."""
    conn = get_conn()
    try:
        ensure(conn)
        if category:
            rows = conn.execute(
                "SELECT prompt FROM nsfw_rejects WHERE category=? "
                "ORDER BY id DESC LIMIT ?", (category, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT prompt FROM nsfw_rejects ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    lines = [f"- {(r['prompt'] or '').strip()[:140]}" for r in rows if (r["prompt"] or "").strip()]
    return "\n".join(lines)


def _clean_llm_line(raw: str) -> str:
    txt = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    txt = txt.strip().strip('"`*').strip()
    # take the last substantial line if the model rambled
    lines = [l.strip().strip('"`*') for l in txt.splitlines() if len(l.strip()) > 20]
    return (lines[-1] if lines else txt)[:900]


# ── category jobs (LLM authors one concrete prompt → normal image pipeline) ──
def submit_category_job(cat: dict, agent_key: str | None = None) -> int:
    """Queue one category creation: the nsfw model writes a concrete prompt from
    the category brief (avoiding recently-rejected approaches), the safety floor
    screens it, then the NORMAL image pipeline runs it flagged nsfw=1.
    Returns the orchestrator task id. Queue label is always generic."""
    from orchestrator import orch
    brief = (cat.get("gen_prompt") or "").strip() or \
        f"Tasteful, artistic adults-only {cat['name']} artwork; all subjects are adults."
    avoid = recent_reject_lines(cat.get("name"))
    cat_name = cat.get("name") or ""

    def _work():
        from llm_client import _call_lmstudio, _resolve_model
        from prompts import get_prompt
        sysp = get_prompt("nsfw_category_run").format(brief=brief, avoid=avoid or "(none yet)")
        raw = _call_lmstudio(sysp, "Write ONE new generation prompt now.", max_tokens=500)
        prompt = _clean_llm_line(raw)
        if not prompt or prompt.upper().startswith("REFUSED"):
            raise RuntimeError("model declined to write a prompt")
        reason = safety_check(prompt)
        if reason:
            raise RuntimeError(f"safety floor refused the authored prompt: {reason}")
        conn = get_conn()
        try:
            model = _resolve_model(conn, None)
            cur = conn.execute(
                "INSERT INTO generations (prompt,product_type,width,height,steps,model,"
                "source,nsfw,nsfw_category,nsfw_agent) VALUES (?,?,?,?,?,?,'nsfw',1,?,?)",
                (prompt, "Art", 1024, 1024, 20, model, cat_name, agent_key))
            gid = cur.lastrowid
            conn.commit()
        finally:
            conn.close()

        def _run():
            import services
            services.run_generation(gid)
        threading.Thread(target=_run, daemon=True, name=f"nsfw-gen-{gid}").start()
        return {"generation_id": gid, "category": cat_name}

    # desc deliberately generic — no category name / prompt content in the queue.
    return orch.submit_llm(_work, desc="Private studio: category job",
                           model=pick_model() or None, task="nsfw_category_run")


# ── bootstrap: the nsfw model AUTHORS the category generator prompts ─────────
def submit_bootstrap() -> int:
    """One (re-runnable) orchestrator task: for every category, ask the nsfw model
    to write/refresh that category's generator prompt. Each authored prompt is
    screened by the safety floor before being saved; the saved rows stay fully
    user-editable in the tab. Returns the task id."""
    from orchestrator import orch

    def _work():
        from llm_client import _call_lmstudio
        from prompts import get_prompt
        conn = get_conn()
        try:
            seed_categories(conn)
            cats = list_categories(conn)
        finally:
            conn.close()
        sysp = get_prompt("nsfw_bootstrap_author")
        updated, refused = [], []
        for cat in cats:
            try:
                raw = _call_lmstudio(sysp, f"Category: {cat['name']}", max_tokens=700)
            except Exception as e:
                refused.append(f"{cat['name']}: {e}")
                continue
            text = _clean_llm_line(raw)
            reason = safety_check(text)
            if not text or reason or text.upper().startswith("REFUSED"):
                refused.append(f"{cat['name']}: {reason or 'no usable output'}")
                continue
            conn = get_conn()
            try:
                conn.execute("UPDATE nsfw_categories SET gen_prompt=?,"
                             "updated_at=datetime('now') WHERE id=?", (text, cat["id"]))
                conn.commit()
            finally:
                conn.close()
            updated.append(cat["name"])
        return {"updated": updated, "refused": refused}

    return orch.submit_llm(_work, desc="Private studio: bootstrap prompts",
                           model=pick_model() or None, priority=0, task="nsfw_bootstrap_author")


# ── reject feedback loop ─────────────────────────────────────────────────────
_WORLD_LINES_REJECT = [
    "😅 {name} heard a private commission missed the mark — scrapping that approach for good.",
    "📝 {name} took some tough studio feedback and is rethinking the next private piece.",
]


def reject_design(design_id: int) -> dict:
    """'Badly generated' verdict on an nsfw gallery item: delete the image + row,
    log the rejection (per category/prompt) so future jobs avoid the approach,
    feed a deny signal into the god-taste model, and let the world agent (if one
    made it) acknowledge it with a generic journal line."""
    from pathlib import Path
    conn = get_conn()
    try:
        ensure(conn)
        row = conn.execute(
            "SELECT d.id, d.image_path, d.prompt, d.generation_id, "
            "       COALESCE(d.nsfw,0) AS nsfw, d.source, "
            "       g.nsfw_category, g.nsfw_agent "
            "FROM designs d LEFT JOIN generations g ON g.id=d.generation_id "
            "WHERE d.id=?", (design_id,)).fetchone()
        if not row or not (row["nsfw"] or row["source"] == "nsfw"):
            raise HTTPException(404, "Not found")
        prompt = row["prompt"] or ""
        category = row["nsfw_category"] or ""
        agent_key = row["nsfw_agent"] or ""
        conn.execute("INSERT INTO nsfw_rejects (design_id,prompt,category,agent_key) "
                     "VALUES (?,?,?,?)", (design_id, prompt, category, agent_key or None))
        # deny signal into the god-taste model (same k-NN the world consults)
        try:
            import world_taste
            world_taste.add_example(conn, f"nsfw_reject:{design_id}", "nsfw",
                                    prompt, -1.0, "god_verdict")
        except Exception:
            logger.exception("nsfw reject: taste deny signal failed")
        if row["generation_id"]:
            conn.execute("UPDATE generations SET status='rejected',"
                         "updated_at=datetime('now') WHERE id=?", (row["generation_id"],))
        conn.execute("DELETE FROM designs WHERE id=?", (design_id,))
        # the agent feels it — generic wording only, never the prompt
        if agent_key:
            arow = conn.execute("SELECT name FROM world_agents WHERE key=?", (agent_key,)).fetchone()
            if arow:
                conn.execute("INSERT INTO world_events (agent_key,kind,text) VALUES (?,?,?)",
                             (agent_key, "thought",
                              random.choice(_WORLD_LINES_REJECT).format(name=arow["name"])))
        conn.commit()
    finally:
        conn.close()
    if row["image_path"]:
        try:
            Path(row["image_path"]).unlink(missing_ok=True)
        except Exception:
            pass
    return {"ok": True, "category": category, "agent": agent_key or None}


# ── Company-world moonlighting hook (extracted to nsfw_world.py) ─────────────
# Import-time re-export keeps this module's public surface identical
# (import nsfw as _nsfw → _nsfw.maybe_world_cycle, etc.). The world helpers
# lazy-import this module, so there is no import cycle.
from nsfw_world import (  # noqa: E402,F401
    _FALLBACK_IDEAS, _WORLD_LINES_START, _WORLD_LINES_DONE,
    _world_lock, _world_running, _world_interval_sec,
    maybe_world_cycle, _world_cycle_thread, _log_world_event,
    world_cycle, _wait_generation,
)

"""
The Company — the COMPLETE gated agent-capability catalog.

One registry mapping every real product/action the platform makes to a
capability an agent (or you, from the God Panel) can TRIGGER and get a
PRODUCT back: images, video (single/chain), music, 3D, the Studio pipeline
(storyboard→render→layered audio→assemble→export), social drafts + gated
publishing, resell/Etsy/Printify listings, research, trends, governance and
security. This replaces the old 10-button "mini-MCP" stub in world_control
(that endpoint keeps working; this is the full surface).

Design rules
  • Every capability is individually TOGGLE-GATED for agents
    (settings key world_cap_<id>). The 10 legacy capabilities that were
    already exposed default ON; everything NEW defaults OFF — nothing new
    fires autonomously until you flip its gate.
  • A god click from the panel is itself the explicit human gate, so
    actor="god" invocations always run. actor="agent" requires the catalog
    master (world_caps_master) AND the per-cap gate.
  • Real-world side effects (social publish, Cults3D, Etsy/Printify) still
    route through the world_ops prayer gate even when god triggers them.
  • All model/GPU work rides the orchestrator: fn-caps reuse the existing
    world modules; http-caps call the store's own endpoints over the
    localhost auth-bypass — those handlers already acquire `orch`.
  • Every invocation is recorded in world_cap_runs with a product reference;
    runs() enriches each with LIVE status from the product's own table, so
    "trigger an action → get a product back" is inspectable end to end.
  • NSFW: nothing here ever sets nsfw=true; the endpoints' own gates and
    safety floors stay authoritative.

The optional agent auto-loop (maybe_agent_cycle) is OFF by default
(world_caps_auto) and piggybacks the existing world_auto daemon tick — no
second loop, no forked scheduler.
"""
import json
import logging
import random
import threading
import time

import httpx

from deps import get_conn, get_setting

logger = logging.getLogger("store")
_LOCAL = "http://127.0.0.1:8787"   # internal calls ride the localhost auth-bypass

MASTER_KEY = "world_caps_master"          # gates AGENT invocations (not god clicks)
AUTO_KEY = "world_caps_auto"              # the agent auto-loop — OFF by default
AUTO_INTERVAL_KEY = "world_caps_auto_interval_min"
AUTO_LAST_KEY = "world_caps_auto_last"
AUTO_INTERVAL_DEFAULT = 240               # at most one agent-initiated cap / 4h


# ── settings io ──────────────────────────────────────────────────────────────
def _set(k, v):
    c = get_conn()
    try:
        c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, str(v)))
        c.commit()
    finally:
        c.close()


def _truthy(v):
    return str(v).lower() in ("1", "true", "yes", "on")


def master_on():
    return _truthy(get_setting(MASTER_KEY, "1"))


def auto_on():
    return _truthy(get_setting(AUTO_KEY, "0"))


def gate_key(cap_id):
    return f"world_cap_{cap_id}"


def gate_on(cap):
    """Is this capability's AGENT gate on? Legacy caps default on, new ones off."""
    return _truthy(get_setting(gate_key(cap["id"]), "1" if cap.get("legacy") else "0"))


# ── runs table + per-agent grants ────────────────────────────────────────────
def ensure(conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS world_cap_runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cap_id TEXT NOT NULL,
            actor  TEXT DEFAULT 'god',
            agent  TEXT,
            args   TEXT DEFAULT '{}',
            status TEXT DEFAULT 'running',
            result TEXT,
            product_kind TEXT,
            product_ref  TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            finished_at TEXT)""")
        # Per-agent capability GRANTS (owner-set, DEFAULT EMPTY = unchanged
        # behavior). A grant lets ONE named agent use ONE capability whose
        # global agent-gate is off — a finer brush than the per-cap pill. It
        # never bypasses the catalog master, never applies to Jesus, and every
        # real-world side effect still exits via the prayer gates downstream.
        conn.execute("""CREATE TABLE IF NOT EXISTS world_cap_grants(
            agent  TEXT NOT NULL,
            cap_id TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(agent, cap_id))""")
        conn.commit()
    finally:
        if own:
            conn.close()


# ── per-agent grants (owner-set; default: none — unchanged behavior) ─────────
def has_grant(agent, cap_id):
    """Does this NAMED agent hold an owner grant for this capability?"""
    if not agent:
        return False
    conn = get_conn()
    try:
        ensure(conn)
        return bool(conn.execute(
            "SELECT 1 FROM world_cap_grants WHERE agent=? AND cap_id=?",
            (str(agent), cap_id)).fetchone())
    finally:
        conn.close()


def grants():
    """cap_id → sorted list of granted agent names (only caps with any grant)."""
    conn = get_conn()
    try:
        ensure(conn)
        out = {}
        for r in conn.execute(
                "SELECT agent, cap_id FROM world_cap_grants ORDER BY agent").fetchall():
            out.setdefault(r["cap_id"], []).append(r["agent"])
        return out
    finally:
        conn.close()


def set_grants(cap_id, agents):
    """REPLACE the grant set for one capability with the given agent names.
    Empty list = no grants (the default). Owner-only surface (God Panel)."""
    if cap_id not in _CAP:
        return None
    names = sorted({str(a).strip() for a in (agents or []) if str(a).strip()})
    conn = get_conn()
    try:
        ensure(conn)
        conn.execute("DELETE FROM world_cap_grants WHERE cap_id=?", (cap_id,))
        for n in names:
            conn.execute("INSERT OR IGNORE INTO world_cap_grants (agent,cap_id) VALUES (?,?)",
                         (n, cap_id))
        conn.commit()
    finally:
        conn.close()
    return panel()


# ── execution helpers ────────────────────────────────────────────────────────
def _fill(template, args):
    """'{name}' path templating + '$name' body-value substitution."""
    if isinstance(template, str):
        if template.startswith("$"):
            return args.get(template[1:])
        try:
            return template.format(**args)
        except Exception:
            return template
    if isinstance(template, dict):
        return {k: _fill(v, args) for k, v in template.items()}
    if isinstance(template, list):
        return [_fill(v, args) for v in template]
    return template


def _dig(obj, path):
    """'generation_ids.0' style dotted lookup into a response dict."""
    cur = obj
    for part in str(path).split("."):
        try:
            cur = cur[int(part)] if isinstance(cur, list) else cur.get(part)
        except Exception:
            return None
        if cur is None:
            return None
    return cur


def _http_run(spec, args):
    method = spec.get("method", "POST")
    path = _fill(spec["path"], args)
    body = _fill(spec.get("body"), args) if spec.get("body") is not None else None
    r = httpx.request(method, f"{_LOCAL}{path}", json=body,
                      timeout=spec.get("timeout", 60))
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"status_code": r.status_code}


# ── fn-cap implementations (reuse the existing systems — never fork them) ────
def _fn_create(kind):
    def go(args):
        import world_auto
        if world_auto._state["running"]:
            return {"result": "a creation is already in progress", "skipped": True}
        threading.Thread(target=world_auto.run_cycle, args=(kind, True), daemon=True).start()
        return {"result": f"creating {kind} — files a publish prayer when done"}
    return go


def _fn_sell(channel):
    def go(args):
        import world_sell
        threading.Thread(target=world_sell.list_design, args=(channel,), daemon=True).start()
        return {"result": f"drafting a {channel} listing — appears as a prayer to review"}
    return go


def _fn_convene(args):
    import world_strategy
    threading.Thread(target=world_strategy.run_cycle, daemon=True).start()
    return {"result": "the assembly is convening"}


def _fn_meeting(args):
    import world_gov
    conn = get_conn()
    try:
        res = world_gov.hold_meeting(conn)
    finally:
        conn.close()
    return {"result": ("voted: " + res["decision"][:120]) if res else "no open suggestions"}


def _fn_cognition(args):
    import world_gov
    conn = get_conn()
    try:
        n = world_gov.run_cognition(conn)
    finally:
        conn.close()
    return {"result": f"queued {n} thought/opinion jobs on the LLM queue"}


def _fn_research(args):
    import world_ops as wo
    topic = (args or {}).get("topic") or "Scout a survival edge on the web"
    p = wo.pray("library_research", f"Research: {topic}",
                detail="Commissioned via the capability catalog.",
                cost_cents=0, agent_name=args.get("_agent") or "Mission Control")
    return {"result": f"research commissioned: {topic}", "prayer_id": p["id"] if p else None}


def _fn_publish_3d(args):
    import world_ops as wo
    mid = int(args.get("model_id") or 0)
    if not mid:
        return {"result": "model_id required", "error": True}
    p = wo.pray("publish_cults3d", f"Publish 3D model #{mid} to Cults3D",
                detail="Requested via the capability catalog. Cults3D publishing is free.",
                cost_cents=0, payload={"type": "3d", "model_id": mid},
                agent_name=args.get("_agent") or "Mission Control")
    return {"result": f"publish prayer filed for model {mid}", "prayer_id": p["id"] if p else None}


def _fn_social_publish(args):
    """REAL social publishing (YouTube/TikTok) — ALWAYS via the prayer gate."""
    import world_ops as wo
    pid = int(args.get("post_id") or 0)
    if not pid:
        return {"result": "post_id required", "error": True}
    title = f"Publish social post #{pid}"
    conn = get_conn()
    try:
        row = conn.execute("SELECT title, platforms FROM social_posts WHERE id=?", (pid,)).fetchone()
        if row:
            title = f"Publish to {row['platforms'] or 'social'}: {(row['title'] or '')[:48]}"
    finally:
        conn.close()
    p = wo.pray("social_publish", title,
                detail="Publishes the draft to its connected platforms (YouTube/TikTok). "
                       "Public + irreversible once live, so it always asks first.",
                cost_cents=0, payload={"post_id": pid},
                agent_name=args.get("_agent") or "Mission Control")
    return {"result": f"publish prayer filed for post {pid}", "prayer_id": p["id"] if p else None}


def _exec_social_publish(conn, prayer):
    """Prayer executor: a blessed social_publish hits the real publish endpoint."""
    payload = json.loads(prayer["payload"] or "{}")
    pid = int(payload.get("post_id") or 0)
    if not pid:
        return "no post_id in payload"
    r = httpx.post(f"{_LOCAL}/api/social/posts/{pid}/publish", timeout=600)
    r.raise_for_status()
    d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return f"published post {pid}: " + json.dumps(d)[:300]


try:
    import world_ops as _wo
    _wo.register_executor("social_publish", _exec_social_publish)
except Exception:                                    # pragma: no cover
    logger.exception("social_publish executor registration failed")


# ── THE CATALOG ──────────────────────────────────────────────────────────────
# Groups: Create · Studio · Social · Sell · Research · Govern · Infra
# legacy=True → was already exposed pre-catalog → agent gate defaults ON.
# agent_safe=True → eligible for the (off-by-default) agent auto-loop:
#   free, local, non-publishing, non-spending actions only.
CATALOG = [
    # ── Create: direct products on the GPU (orch-managed inside the endpoints) ─
    {"id": "make_art",   "label": "🖼️ Studio art piece", "group": "Create", "legacy": True, "agent_safe": True,
     "product": "image → publish prayer", "dept": "image",
     "desc": "One autonomous studio image (taste-weighted prompt) that files a publish prayer.",
     "fn": _fn_create("image")},
    {"id": "make_music", "label": "🎵 Studio track", "group": "Create", "legacy": True, "agent_safe": True,
     "product": "music clip → publish prayer", "dept": "audio",
     "desc": "One autonomous music piece (may sing via ACE-Step when allowed); files a publish prayer.",
     "fn": _fn_create("music")},
    # agent_safe: like make_art/make_music these are free, local, and only ever
    # file a publish PRAYER — and every auto path is serialized by the shared
    # in-flight guard (_content_busy), so the 3060 only ever carries one product.
    {"id": "make_video", "label": "🎬 Studio clip", "group": "Create", "legacy": True, "agent_safe": True,
     "product": "video → publish prayer", "dept": "video",
     "desc": "One autonomous Wan2.1 video; files a publish prayer.",
     "fn": _fn_create("video")},
    {"id": "make_3d",    "label": "🧊 Studio sculpt", "group": "Create", "legacy": True, "agent_safe": True,
     "product": "3D model → Cults3D prayer", "dept": "models3d",
     "desc": "One autonomous SDXL→TripoSR mesh; files a Cults3D publish prayer.",
     "fn": _fn_create("3d")},
    {"id": "gen_image", "label": "🎨 Generate an image", "group": "Create", "agent_safe": True,
     "product": "image (generations)", "dept": "image",
     "desc": "Direct image generation from a prompt — the product lands in the Image tab.",
     "args": [{"name": "prompt", "label": "Prompt", "required": True}],
     "http": {"path": "/api/generate",
              "body": {"prompt": "$prompt", "product_type": "Art", "width": 1024,
                       "height": 1024, "steps": 20, "variations": 1, "source": "world_caps"}},
     "product_kind": "image", "ref": "generation_ids.0"},
    {"id": "gen_video", "label": "📹 Generate a video", "group": "Create",
     "product": "video (videos)", "dept": "video",
     "desc": "Direct Wan2.1 text→video — lands in the Video tab.",
     "args": [{"name": "prompt", "label": "Prompt", "required": True}],
     "http": {"path": "/api/videos/generate", "body": {"prompt": "$prompt"}},
     "product_kind": "video", "ref": "id"},
    {"id": "gen_video_chain", "label": "🎞️ Video chain (multi-scene)", "group": "Create",
     "product": "compiled chain (video_chains)", "dept": "video",
     "desc": "A continued multi-segment video chain from ; separated prompts, auto-compiled.",
     "args": [{"name": "prompts", "label": "Prompts (separate with ;)", "required": True}],
     "prep": lambda a: a.update({"prompts": [p.strip() for p in str(a.get("prompts", "")).split(";") if p.strip()]}),
     "http": {"path": "/api/video-chains", "body": {"prompts": "$prompts", "title": ""}},
     "product_kind": "chain", "ref": "chain_id"},
    {"id": "gen_music", "label": "🎼 Generate music", "group": "Create", "agent_safe": True,
     "product": "audio clip (audio_clips)", "dept": "audio",
     "desc": "Direct MusicGen clip from a prompt — lands in the Audio tab.",
     "args": [{"name": "prompt", "label": "Prompt", "required": True}],
     "http": {"path": "/api/audio/generate",
              "body": {"prompt": "$prompt", "engine": "musicgen", "duration": 8}},
     "product_kind": "audio", "ref": "id"},
    {"id": "gen_3d", "label": "🗿 Generate a 3D model", "group": "Create",
     "product": "mesh (models3d)", "dept": "models3d",
     "desc": "Direct SDXL→TripoSR text→mesh — lands in the 3D tab.",
     "args": [{"name": "prompt", "label": "Prompt", "required": True}],
     "http": {"path": "/api/models3d/generate",
              "body": {"prompt": "$prompt", "generator": "triposr"}},
     "product_kind": "model3d", "ref": "model_id"},

    # ── Studio: storyboard → render → layered audio → assemble → export ──────
    {"id": "studio_short", "label": "🎬 Studio: storyboard a short", "group": "Studio", "agent_safe": True,
     "product": "studio project (storyboard)", "dept": "video",
     "desc": "Drop an idea → LLM storyboard (scenes/shots/cues). Render is a separate step.",
     "args": [{"name": "idea", "label": "Idea", "required": True}],
     "http": {"path": "/api/studio/projects", "body": {"idea": "$idea", "kind": "short"}},
     "product_kind": "studio", "ref": "id"},
    {"id": "studio_from_trend", "label": "📈→🎬 Studio from a trend", "group": "Studio",
     "product": "studio project (storyboard)", "dept": "video",
     "desc": "Turn trend text into a storyboarded Studio short.",
     "args": [{"name": "text", "label": "Trend text", "required": True}],
     "http": {"path": "/api/studio/from-trend", "body": {"text": "$text", "kind": "short"}},
     "product_kind": "studio", "ref": "id"},
    {"id": "studio_produce", "label": "🏭 Studio: produce project", "group": "Studio",
     "product": "final video (render→audio→mix)", "dept": "video",
     "desc": "Render every scene, generate the music/VO cues, assemble + mix the final.",
     "args": [{"name": "project_id", "label": "Project id", "required": True}],
     "http": {"path": "/api/studio/projects/{project_id}/render"},
     "product_kind": "studio", "ref_arg": "project_id"},
    {"id": "studio_audio", "label": "🎚️ Studio: layered audio", "group": "Studio",
     "product": "music bed + voiceover", "dept": "audio",
     "desc": "Generate the project's music bed + whole-script voiceover (scenes must be rendered).",
     "args": [{"name": "project_id", "label": "Project id", "required": True}],
     "http": {"path": "/api/studio/projects/{project_id}/audio"},
     "product_kind": "studio", "ref_arg": "project_id"},
    {"id": "studio_assemble", "label": "🧵 Studio: assemble + mix", "group": "Studio",
     "product": "final video file", "dept": "video",
     "desc": "Stitch scene clips and mix the generated audio into the final video (CPU ffmpeg).",
     "args": [{"name": "project_id", "label": "Project id", "required": True}],
     "http": {"path": "/api/studio/projects/{project_id}/assemble"},
     "product_kind": "studio", "ref_arg": "project_id"},
    {"id": "studio_export", "label": "📤 Studio → social draft", "group": "Studio",
     "product": "social draft post", "dept": "social",
     "desc": "Attach the final video to a DRAFT social post (publish stays gated).",
     "args": [{"name": "project_id", "label": "Project id", "required": True}],
     "http": {"path": "/api/studio/projects/{project_id}/export",
              "body": {"platforms": ["youtube"]}},
     "product_kind": "social_post", "ref": "post_id"},

    # ── Social: drafts free, publishing gated ─────────────────────────────────
    {"id": "social_caption", "label": "✍️ Write a social caption", "group": "Social", "agent_safe": True,
     "product": "caption + hashtags (LLM task)", "dept": "social",
     "desc": "LLM caption + hashtags for a topic (rides the orch LLM queue).",
     "args": [{"name": "topic", "label": "Topic", "required": True}],
     "http": {"path": "/api/social/generate", "body": {"topic": "$topic"}},
     "product_kind": "task", "ref": "task_id"},
    {"id": "social_draft", "label": "📝 Draft a social post", "group": "Social", "agent_safe": True,
     "product": "draft post (social_posts)", "dept": "social",
     "desc": "Create a DRAFT post (never publishes by itself).",
     "args": [{"name": "caption", "label": "Caption", "required": True},
              {"name": "title", "label": "Title", "required": False, "default": ""}],
     "http": {"path": "/api/social/posts",
              "body": {"title": "$title", "caption": "$caption",
                       "platforms": ["youtube"], "status": "draft", "source": "world_caps"}},
     "product_kind": "social_post", "ref": "id"},
    {"id": "social_publish", "label": "📣 Publish a social post", "group": "Social",
     "product": "live YouTube/TikTok publication (via prayer)", "dept": "social",
     "desc": "Files a GATED prayer; only your blessing pushes the post live.",
     "args": [{"name": "post_id", "label": "Draft post id", "required": True}],
     "fn": _fn_social_publish, "product_kind": "prayer", "ref": "prayer_id"},
    {"id": "social_sched_once", "label": "⏰ Run the social scheduler once", "group": "Social",
     "product": "due scheduled posts published", "dept": "social",
     "desc": "One pass of the (separately-gated) social scheduler.",
     "http": {"path": "/api/social/scheduler/run-once"}, "product_kind": "raw"},

    # ── Sell: real listings — always through the prayer gate ─────────────────
    {"id": "sell_etsy", "label": "🛍️ List a design on Etsy", "group": "Sell", "legacy": True,
     "product": "Etsy listing (via prayer, ~$0.20)", "dept": "storefront",
     "desc": "Company picks its best unlisted design, drafts the listing, files a prayer.",
     "fn": _fn_sell("etsy")},
    {"id": "sell_printify", "label": "👕 List on Printify", "group": "Sell", "legacy": True,
     "product": "Printify listing (via prayer)", "dept": "storefront",
     "desc": "Same flow as Etsy, on Printify print-on-demand.",
     "fn": _fn_sell("printify")},
    {"id": "publish_3d", "label": "🧊→🌐 Publish 3D to Cults3D", "group": "Sell",
     "product": "Cults3D listing (via prayer)", "dept": "models3d",
     "desc": "Files a publish prayer for an existing finished 3D model.",
     "args": [{"name": "model_id", "label": "3D model id", "required": True}],
     "fn": _fn_publish_3d, "product_kind": "prayer", "ref": "prayer_id"},
    {"id": "resell_draft", "label": "📦 Draft a resell listing", "group": "Sell",
     "product": "draft listing (resell_listings)", "dept": "resell",
     "desc": "Create a local DRAFT resell listing (nothing posted anywhere).",
     "args": [{"name": "title", "label": "Item title", "required": True},
              {"name": "description", "label": "Description", "required": False, "default": ""}],
     "http": {"path": "/api/resell/listings",
              "body": {"title": "$title", "description": "$description"}},
     "product_kind": "raw", "ref": "id"},

    # ── Research / Learn ─────────────────────────────────────────────────────
    {"id": "research", "label": "📖 Commission library research", "group": "Research",
     "legacy": True, "agent_safe": True,
     "product": "research entry in the Bible (via prayer)", "dept": "research",
     "desc": "Files a library_research prayer on a topic.",
     "args": [{"name": "topic", "label": "Topic", "required": True}],
     "fn": _fn_research, "product_kind": "prayer", "ref": "prayer_id"},
    {"id": "deep_research", "label": "🔬 Research Lab project", "group": "Research", "agent_safe": True,
     "product": "research project + report", "dept": "research",
     "desc": "Opens a Research Lab project (auto-assigned Genius; autostarts if lab allows).",
     "args": [{"name": "title", "label": "Project title", "required": True}],
     "http": {"path": "/api/research/projects", "body": {"title": "$title"}},
     "product_kind": "research", "ref": "id"},
    {"id": "scan_trends", "label": "📈 Scan market trends", "group": "Research",
     "legacy": True, "agent_safe": True,
     "product": "trend proposals", "dept": "trends",
     "desc": "One trends scan → product proposals.",
     "http": {"path": "/api/trends/scan"}, "product_kind": "raw"},

    # ── Govern ───────────────────────────────────────────────────────────────
    {"id": "convene", "label": "🏛️ Convene the assembly", "group": "Govern", "legacy": True,
     "product": "national plan + actions", "dept": "civic",
     "desc": "The Republic assesses, proposes, votes and acts (risky actions stay gated).",
     "fn": _fn_convene},
    {"id": "town_meeting", "label": "🗳️ Hold a town meeting", "group": "Govern", "agent_safe": True,
     "product": "voted priority + directive", "dept": "civic",
     "desc": "The crew votes its top priority into the active directive (pure CPU).",
     "fn": _fn_meeting},
    {"id": "cognition", "label": "💭 Wake the crew (think)", "group": "Govern", "agent_safe": True,
     "product": "thoughts + opinions", "dept": "civic",
     "desc": "A batch of agent thoughts/opinions on the orch LLM queue.",
     "fn": _fn_cognition},

    # ── Infra ────────────────────────────────────────────────────────────────
    {"id": "sec_scan_now", "label": "🛡️ Run a security scan", "group": "Infra", "legacy": True,
     "product": "security findings", "dept": "netsec",
     "desc": "One-off network/Pi-hole scan.",
     "http": {"path": "/api/security/scan"}, "product_kind": "raw"},
]
_CAP = {c["id"]: c for c in CATALOG}
GROUP_ORDER = ["Create", "Studio", "Social", "Sell", "Research", "Govern", "Infra"]

# ── the publishing-prayer buttons Jesus may additionally press ───────────────
# cap id → the prayer KIND the cap files. These caps only ever FILE a publish
# PRAYER (they publish nothing themselves), and both kinds are FREE (never in
# PAID_KINDS) and not in ALWAYS_GATE — so handing them to the delegate adds
# zero authority: the filed prayer still needs the exact same blessing it
# always did. world_jesus draws from this dict ONLY when the owner flips
# world_jesus_publish_caps ON (default OFF), and each cap is still refused
# unless its own agent-gate is on (world_caps.invoke enforces master +
# per-cap gate for actor='jesus' identically to agents). Etsy/Printify caps
# are deliberately NOT here: their prayers cost real cents and stay human.
JESUS_PUBLISH_CAPS = {
    "social_publish": "social_publish",     # files a gated social-publish prayer (free)
    "publish_3d":     "publish_cults3d",    # files a gated Cults3D publish prayer (free)
}


# ── live product-status enrichment ───────────────────────────────────────────
_PRODUCT_SQL = {
    "image":       ("generations",      "status", "image_path"),
    "video":       ("videos",           "status", "video_path"),
    "chain":       ("video_chains",     "status", "compiled_path"),
    "audio":       ("audio_clips",      "status", "audio_path"),
    "model3d":     ("models3d",         "status", None),
    "studio":      ("studio_projects",  "status", "final_path"),
    "social_post": ("social_posts",     "status", None),
    "research":    ("research_projects", "status", None),
    "prayer":      ("world_prayers",    "status", "result"),
}


def _product_status(conn, kind, ref):
    tbl = _PRODUCT_SQL.get(kind)
    if not tbl or ref in (None, ""):
        return None
    table, st_col, path_col = tbl
    try:
        cols = f"{st_col}" + (f", {path_col}" if path_col else "")
        row = conn.execute(f"SELECT {cols} FROM {table} WHERE id=?", (int(ref),)).fetchone()
        if not row:
            return None
        out = {"status": row[st_col]}
        if path_col and row[path_col]:
            out["path"] = str(row[path_col])[:300]
        return out
    except Exception:
        return None


# ── invoke ───────────────────────────────────────────────────────────────────
def invoke(cap_id, args=None, actor="god", agent=None):
    """Trigger a capability. actor='god' (an explicit panel click) always runs;
    actor='agent' requires the catalog master AND the per-cap gate.
    actor='jesus' (the owner's toggle-gated stand-in, world_jesus.py) is
    deliberately gated EXACTLY like an agent — its own enable toggle PLUS the
    catalog master PLUS the per-cap gate — so the delegation adds zero
    privilege: Jesus can only press buttons the owner already turned on.
    actor='satan' (the adversarial red-team lieutenant, world_satan.py) is
    gated IDENTICALLY: its own toggle (world_satan_enabled, default OFF) PLUS
    the catalog master PLUS the per-cap gate. Like Jesus, Satan never rides
    per-agent grants and can never flip a gate — mirrored authority, zero new
    privilege, same unchanged hard floors."""
    cap = _CAP.get(cap_id)
    if not cap:
        return {"ok": False, "error": f"unknown capability '{cap_id}'"}
    if actor != "god":
        if actor == "jesus" and not _truthy(get_setting("world_jesus_enabled", "0")):
            return {"ok": False, "error": "Jesus is disabled (world_jesus_enabled is off)"}
        if actor == "satan" and not _truthy(get_setting("world_satan_enabled", "0")):
            return {"ok": False, "error": "Satan is disabled (world_satan_enabled is off)"}
        if not master_on():
            return {"ok": False, "error": "capability catalog master is off"}
        if not gate_on(cap):
            # Per-agent grant: an owner-granted (agent, cap) pair passes the
            # PER-CAP gate only — for actor='agent' with a NAMED agent. The
            # catalog master above still applies, Jesus and Satan never ride
            # grants, and there are no grants by default (unchanged behavior).
            if not (actor == "agent" and agent and has_grant(agent, cap_id)):
                return {"ok": False, "error": f"capability '{cap_id}' is gated off for agents"}
    args = dict(args or {})
    for spec in cap.get("args", []):
        v = args.get(spec["name"])
        if (v is None or str(v).strip() == "") and "default" in spec:
            args[spec["name"]] = spec["default"]
        elif (v is None or str(v).strip() == "") and spec.get("required"):
            return {"ok": False, "error": f"missing argument: {spec['name']}"}
    if agent:
        args["_agent"] = agent

    conn = get_conn()
    try:
        ensure(conn)
        cur = conn.execute(
            "INSERT INTO world_cap_runs (cap_id,actor,agent,args,status,product_kind) "
            "VALUES (?,?,?,?, 'running', ?)",
            (cap_id, actor, agent, json.dumps({k: v for k, v in args.items() if k != "_agent"}),
             cap.get("product_kind")))
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    threading.Thread(target=_execute, args=(run_id, cap, args), daemon=True).start()
    return {"ok": True, "run_id": run_id, "result": f"triggered: {cap['label']}"}


def _execute(run_id, cap, args):
    status, result, ref = "done", None, None
    try:
        if cap.get("prep"):
            cap["prep"](args)
        if cap.get("fn"):
            out = cap["fn"](args) or {}
            result = out.get("result") or json.dumps(out)[:400]
            if out.get("error"):
                status = "error"
            ref = out.get(cap.get("ref") or "", None) if cap.get("ref") else None
            if ref is None and cap.get("ref"):
                ref = _dig(out, cap["ref"])
        else:
            resp = _http_run(cap["http"], args)
            result = json.dumps(resp)[:400]
            if cap.get("ref"):
                ref = _dig(resp, cap["ref"])
            elif cap.get("ref_arg"):
                ref = args.get(cap["ref_arg"])
    except Exception as e:
        logger.exception("capability %s failed", cap["id"])
        status, result = "error", str(e)[:400]
    conn = get_conn()
    try:
        conn.execute("UPDATE world_cap_runs SET status=?, result=?, product_ref=?, "
                     "finished_at=datetime('now') WHERE id=?",
                     (status, result, str(ref) if ref is not None else None, run_id))
        conn.commit()
    finally:
        conn.close()


# ── panel data ───────────────────────────────────────────────────────────────
def runs(limit=20):
    conn = get_conn()
    try:
        ensure(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM world_cap_runs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]
        for r in rows:
            r["label"] = (_CAP.get(r["cap_id"]) or {}).get("label", r["cap_id"])
            live = _product_status(conn, r.get("product_kind"), r.get("product_ref"))
            if live:
                r["product"] = live
        return rows
    finally:
        conn.close()


def panel():
    groups = []
    by_g = {}
    for c in CATALOG:
        by_g.setdefault(c["group"], []).append(c)
    try:
        gr = grants()
    except Exception:
        gr = {}
    for g in GROUP_ORDER + [g for g in by_g if g not in GROUP_ORDER]:
        if g not in by_g:
            continue
        groups.append({"group": g, "caps": [{
            "id": c["id"], "label": c["label"], "desc": c.get("desc", ""),
            "product": c.get("product", ""), "dept": c.get("dept"),
            "legacy": bool(c.get("legacy")), "agent_safe": bool(c.get("agent_safe")),
            "gate_on": gate_on(c),
            "grants": gr.get(c["id"], []),
            "args": [{k: v for k, v in a.items()} for a in c.get("args", [])],
        } for c in by_g[g]]})
    try:
        interval = max(30, int(get_setting(AUTO_INTERVAL_KEY, str(AUTO_INTERVAL_DEFAULT))))
    except Exception:
        interval = AUTO_INTERVAL_DEFAULT
    return {
        "master": master_on(),
        "auto": {"enabled": auto_on(), "interval_min": interval,
                 "last": float(get_setting(AUTO_LAST_KEY, "0") or 0)},
        "groups": groups,
        "runs": runs(14),
    }


def set_gate(cap_id, on):
    if cap_id == "master":
        _set(MASTER_KEY, "1" if on else "0")
    elif cap_id == "auto":
        _set(AUTO_KEY, "1" if on else "0")
    elif cap_id in _CAP:
        _set(gate_key(cap_id), "1" if on else "0")
    else:
        return None
    return panel()


def set_auto_interval(minutes):
    _set(AUTO_INTERVAL_KEY, max(30, int(minutes)))
    return panel()


# ═════════════════════════════════════════════════════════════════════════════
# AUTONOMOUS CONTENT WORK — the dept→capability draw that gives the "idle"
# creative desks (social / image / video / audio / 3D) REAL products to make.
# world_work._wg_operate calls agent_work() when a creative agent picks
# "Operate"; instead of posing "on the clock", the agent invokes one ENABLED,
# dept-appropriate capability and a real row (generation / draft post / studio
# storyboard / clip / mesh) lands in the store. Heavily throttled: one product
# in flight EVER (shared _content_busy guard), a global minimum gap, and a
# per-dept cooldown — the single 3060 is never flooded. Everything respects
# world_caps_auto + the catalog master + per-cap gates + active hours, and
# anything real-world (publish/sell/spend) still exits via the prayer gate.
# ═════════════════════════════════════════════════════════════════════════════

# dept → curated DRAFT-ONLY content caps (never publish/spend — those caps are
# not listed here and additionally sit behind their own gates + prayers).
DEPT_CAPS = {
    "image":    ["gen_image", "make_art"],
    "video":    ["studio_short", "make_video"],
    "audio":    ["gen_music", "make_music"],
    "models3d": ["make_3d"],
    "social":   ["social_draft", "social_caption"],
}

_GOAL_VERB = {
    "gen_image": "painting", "make_art": "painting",
    "studio_short": "storyboarding a short", "make_video": "filming",
    "gen_music": "composing", "make_music": "composing",
    "make_3d": "sculpting",
    "social_draft": "drafting a post about", "social_caption": "writing a caption about",
}

OPERATE_INTERVAL_KEY = "world_caps_operate_interval_min"   # global gap between operate products
OPERATE_DEPT_KEY = "world_caps_operate_dept_min"           # per-dept cooldown
OPERATE_LAST_KEY = "world_caps_operate_last"               # persisted stamps (survive restarts)
OPERATE_INTERVAL_DEFAULT = 60      # at most one operate-driven product / hour, company-wide
OPERATE_DEPT_DEFAULT = 240         # each dept at most one / 4h

_operate = {"loaded": False, "last": 0.0, "dept": {}}
_operate_lock = threading.Lock()


def _operate_load():
    if _operate["loaded"]:
        return
    try:
        _operate["last"] = float(get_setting(OPERATE_LAST_KEY, "0") or 0)
    except Exception:
        _operate["last"] = 0.0
    for dept in DEPT_CAPS:
        try:
            _operate["dept"][dept] = float(get_setting(f"{OPERATE_LAST_KEY}_{dept}", "0") or 0)
        except Exception:
            _operate["dept"][dept] = 0.0
    _operate["loaded"] = True


# ── autonomous argument builders (caps with args become auto-invokable) ──────
_SOCIAL_TOPICS = [
    "behind the scenes at the Acme studio",
    "this week's new geeky tee designs",
    "3D-printable desk toys we love",
    "how our AI art posters get made",
    "sticker pack sneak peek",
    "our favorite maker gadgets right now",
]
_RESEARCH_TOPICS = [
    "what art styles are selling on Etsy right now",
    "short-form video hooks that work for indie shops",
    "print-on-demand niches with low competition",
    "pricing strategies for digital art downloads",
]


def _fresh(conn, ideas, table):
    """Taste-weighted fresh prompt via world_creators; plain choice on any error."""
    try:
        from world_creators import _fresh_prompt
        return _fresh_prompt(conn, ideas, table)
    except Exception:
        return random.choice(ideas)


def _recent_showpiece(conn):
    """The newest finished product's prompt — what the social desk talks about."""
    for sql in ("SELECT prompt FROM generations WHERE status='done' ORDER BY id DESC LIMIT 1",
                "SELECT prompt FROM videos WHERE status='done' ORDER BY id DESC LIMIT 1"):
        try:
            row = conn.execute(sql).fetchone()
            if row and row["prompt"]:
                return " ".join(str(row["prompt"]).split())[:120]
        except Exception:
            pass
    return None


def _aa_gen_image(conn):
    from world_creators import IMAGE_IDEAS
    return {"prompt": _fresh(conn, IMAGE_IDEAS, "generations")}


def _aa_gen_music(conn):
    from world_creators import MUSIC_IDEAS
    return {"prompt": _fresh(conn, MUSIC_IDEAS, "audio_clips")}


def _aa_studio_short(conn):
    from world_creators import VIDEO_IDEAS
    return {"idea": _fresh(conn, VIDEO_IDEAS, "studio_projects")}


def _aa_social_caption(conn):
    t = _recent_showpiece(conn)
    return {"topic": f"our newest studio piece: {t}" if t else random.choice(_SOCIAL_TOPICS)}


def _aa_social_draft(conn):
    t = _recent_showpiece(conn)
    if t:
        caption = f"Fresh from the studio: {t} ✨ New drops landing on Acme soon."
        title = f"Studio drop: {t[:40]}"
    else:
        topic = random.choice(_SOCIAL_TOPICS)
        caption = f"A peek at {topic} 👀 More from the Acme crew soon."
        title = topic[:48].title()
    return {"caption": caption[:280], "title": title[:64]}


def _aa_research(conn):
    return {"topic": random.choice(_RESEARCH_TOPICS)}


def _aa_deep_research(conn):
    return {"title": random.choice(_RESEARCH_TOPICS)}


# Publishing-prayer arg builders (used ONLY by the Jesus delegate — neither cap
# is agent_safe nor listed in DEPT_CAPS, so agents/auto-loops never draw them).
# {} = nothing ready to publish; the delegate then simply does nothing.
def _aa_social_publish(conn):
    """Newest DRAFT social post with media, not already the subject of a live
    social_publish prayer. The cap files a GATED prayer — nothing goes live here."""
    try:
        row = conn.execute(
            "SELECT id FROM social_posts WHERE status='draft' AND media_path IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM world_prayers p WHERE p.kind='social_publish' "
            "AND p.status IN ('pending','approved','done') "
            "AND p.payload LIKE '%\"post_id\": ' || social_posts.id || '}%') "
            "ORDER BY id DESC LIMIT 1").fetchone()
        return {"post_id": row["id"]} if row else {}
    except Exception:
        return {}


def _aa_publish_3d(conn):
    """Newest FINISHED 3D model without a live Cults3D publish prayer. Files a
    GATED publish_cults3d prayer (free) — nothing is published here."""
    try:
        row = conn.execute(
            "SELECT id FROM models3d WHERE status='done' "
            "AND NOT EXISTS (SELECT 1 FROM world_prayers p WHERE p.kind='publish_cults3d' "
            "AND p.status IN ('pending','approved','done') "
            "AND p.payload LIKE '%\"model_id\": ' || models3d.id || '}%') "
            "ORDER BY id DESC LIMIT 1").fetchone()
        return {"model_id": row["id"]} if row else {}
    except Exception:
        return {}


# ── production-chain builders (video / 3D / studio pipeline / resell) ────────
# Same contract as every builder above: take the (read-only, possibly borrowed
# sim) conn, return a dict of the cap's declared args — or a FALSY value when
# there is nothing sensible to act on, so the caller simply skips (an empty
# dict never invokes anything real: invoke() refuses on the missing required
# arg). These compose arguments only; every gate/floor in invoke() and the
# endpoints behind the caps is untouched. Actor-agnostic — Jesus, Satan and
# the agent auto-loop all share them.

def _llm_line(system, user, max_tokens=90):
    """One short compose pass on the ALREADY-RESIDENT LLM via orch.llm_borrow —
    the unified queue's borrow-only lane. Never loads a model, never queues,
    never blocks: returns a cleaned single line, or None when the GPU is busy /
    no LLM is resident / the call fails (callers then degrade deterministically)."""
    try:
        from deps import _call_lmstudio, orch
        out = orch.llm_borrow(lambda: _call_lmstudio(system, user, max_tokens))
        if not out:
            return None
        line = " ".join(str(out).strip().splitlines()[0].split()).strip(' "\'`')
        return line[:300] or None
    except Exception:
        return None


def _pending_trend(conn):
    """Newest pending trend/news proposal with a real title, or None."""
    try:
        row = conn.execute(
            "SELECT id, title, description, tags FROM proposals "
            "WHERE status='pending' AND title IS NOT NULL AND TRIM(title)<>'' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _prompt_unused(conn, table, prompt):
    """Has this exact prompt never been generated in `table`? (dupe guard)"""
    try:
        return not conn.execute(f"SELECT 1 FROM {table} WHERE prompt=? LIMIT 1",
                                (prompt,)).fetchone()
    except Exception:
        return True


def _video_concept(conn):
    """A video concept from REAL signal, best first: a pending trend proposal
    (LLM-polished when the resident model can be borrowed, deterministic
    template otherwise), then a recent successful subject brought to motion,
    then the taste-weighted fresh idea pool. Exact repeats are skipped."""
    prop = _pending_trend(conn)
    if prop:
        title = " ".join(str(prop["title"]).split())[:120].replace(";", ",")
        desc = " ".join(str(prop.get("description") or "").split())[:200].replace(";", ",")
        line = _llm_line(
            "You write ONE short visual prompt for a text-to-video model. "
            "Reply with the prompt only — one line, no quotes, concrete, visual, family-friendly.",
            f"Trend: {title}. {desc}".strip())
        concept = (line or f"{title} — cinematic short video, smooth slow camera move, "
                           f"vivid detail").replace(";", ",")
        if _prompt_unused(conn, "videos", concept):
            return concept
    subject = _recent_showpiece(conn)
    if subject:
        concept = f"{subject}, brought to life with gentle cinematic motion".replace(";", ",")
        if _prompt_unused(conn, "videos", concept):
            return concept
    from world_creators import VIDEO_IDEAS
    return _fresh(conn, VIDEO_IDEAS, "videos")


def _aa_gen_video(conn):
    """Direct text→video: one concept from trend/showpiece/taste signal."""
    return {"prompt": _video_concept(conn)}


def _aa_gen_video_chain(conn):
    """Multi-scene chain: the same concept expanded into a 2–3 segment arc
    (establish → push in → pull back). Segments are ;-joined because the cap's
    prep splits on ';' — the concept itself is scrubbed of ';' upstream."""
    concept = _video_concept(conn)
    if not concept:
        return {}
    arc = [f"{concept} — establishing wide shot, gentle motion",
           f"{concept} — camera slowly pushes in, richer detail emerges",
           f"{concept} — closing shot, slow pull back, soft fading light"]
    segs = arc if random.random() < 0.5 else [arc[0], arc[2]]   # 3 or 2 segments
    return {"prompts": "; ".join(segs)}


def _aa_gen_3d(conn):
    """3D subject the same way: a pending trend turned into a printable desk-toy
    subject (borrow-only LLM), else the taste-weighted 3D idea pool with the
    last few generated subjects (models3d.gen_prompt) excluded."""
    prop = _pending_trend(conn)
    if prop:
        title = " ".join(str(prop["title"]).split())[:120]
        line = _llm_line(
            "You write ONE short prompt for a small printable desk-toy 3D model. "
            "Reply with the subject only — one line, no quotes, a single simple solid "
            "object, family-friendly (e.g. 'a cute low-poly rocket figurine').",
            f"Trend: {title}")
        if line:
            return {"prompt": line}
    from world_creators import THREED_IDEAS
    try:
        used = {r["gen_prompt"] for r in conn.execute(
            "SELECT gen_prompt FROM models3d WHERE gen_prompt IS NOT NULL "
            "ORDER BY id DESC LIMIT 8").fetchall()}
        pool = [i for i in THREED_IDEAS if i not in used] or list(THREED_IDEAS)
    except Exception:
        pool = list(THREED_IDEAS)
    return {"prompt": _fresh(conn, pool, "models3d")}


def _aa_studio_from_trend(conn):
    """A current pending trend proposal → storyboard text. Skips proposals whose
    title already seeded a studio project (no duplicate storyboards); {} when
    there is no fresh trend to ride."""
    try:
        row = conn.execute(
            "SELECT title, description, tags FROM proposals p "
            "WHERE p.status='pending' AND p.title IS NOT NULL AND TRIM(p.title)<>'' "
            "AND NOT EXISTS (SELECT 1 FROM studio_projects sp "
            "                WHERE sp.idea LIKE '%' || p.title || '%') "
            "ORDER BY p.id DESC LIMIT 1").fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    bits = [" ".join(str(row["title"]).split())[:200]]
    if (row["description"] or "").strip():
        bits.append(" ".join(str(row["description"]).split())[:300])
    if (row["tags"] or "").strip():
        bits.append(f"Related tags: {str(row['tags']).strip()[:100]}")
    return {"text": " — ".join(bits)[:600]}


# Studio step selectors — each picks ONE project actually READY for that stage
# (services_studio state machine: new → storyboarding → draft → rendering →
# mixing → assembling → done | failed). Busy/failed/NSFW projects are never
# picked, and a project mid-step (queued/rendering scene, in-flight audio clip)
# is excluded — no duplicate work. {} = nothing in the right state → skip.
_SCENES_EXIST = ("EXISTS (SELECT 1 FROM studio_scenes s WHERE s.project_id=sp.id)")
_SCENES_ALL_DONE = ("NOT EXISTS (SELECT 1 FROM studio_scenes s WHERE s.project_id=sp.id "
                    "AND NOT (s.status='done' AND s.scene_path IS NOT NULL))")


def _studio_pick(conn, where):
    try:
        row = conn.execute(
            f"SELECT sp.id FROM studio_projects sp WHERE COALESCE(sp.nsfw,0)=0 "
            f"AND {where} ORDER BY sp.id DESC LIMIT 1").fetchone()
        return {"project_id": row["id"]} if row else {}
    except Exception:
        return {}


def _aa_studio_produce(conn):
    """A storyboarded DRAFT project with scenes still to render — the produce
    pipeline (render → audio → assemble) takes it end to end. Projects with a
    scene already queued/rendering, or a failed scene (human's call), skip."""
    return _studio_pick(conn, f"""sp.status='draft' AND sp.final_path IS NULL
        AND {_SCENES_EXIST}
        AND EXISTS (SELECT 1 FROM studio_scenes s WHERE s.project_id=sp.id
                    AND NOT (s.status='done' AND s.scene_path IS NOT NULL))
        AND NOT EXISTS (SELECT 1 FROM studio_scenes s WHERE s.project_id=sp.id
                        AND s.status IN ('queued','rendering','failed'))""")


def _aa_studio_audio(conn):
    """A DRAFT project with EVERY scene rendered and no audio clips yet — the
    exact state the /audio endpoint requires (it 409s otherwise)."""
    return _studio_pick(conn, f"""sp.status='draft'
        AND {_SCENES_EXIST} AND {_SCENES_ALL_DONE}
        AND sp.music_clip_id IS NULL AND sp.vo_clip_id IS NULL""")


def _aa_studio_assemble(conn):
    """A DRAFT project fully rendered, audio generated and DONE (none still
    queued/generating — assembling then would mix a silent final), not yet
    assembled."""
    return _studio_pick(conn, f"""sp.status='draft' AND sp.final_path IS NULL
        AND {_SCENES_EXIST} AND {_SCENES_ALL_DONE}
        AND (sp.music_clip_id IS NOT NULL OR sp.vo_clip_id IS NOT NULL)
        AND NOT EXISTS (SELECT 1 FROM audio_clips a
                        WHERE a.id IN (sp.music_clip_id, sp.vo_clip_id)
                        AND a.status IN ('queued','generating','pending'))
        AND EXISTS (SELECT 1 FROM audio_clips a
                    WHERE a.id IN (sp.music_clip_id, sp.vo_clip_id)
                    AND a.status='done')""")


def _aa_studio_export(conn):
    """A DONE project with a final video on disk that was never exported to a
    social DRAFT (social_post_id set on export — no duplicate drafts). Export
    creates a DRAFT post only; publishing stays behind its own gates."""
    from pathlib import Path
    try:
        rows = conn.execute(
            "SELECT sp.id, sp.final_path FROM studio_projects sp "
            "WHERE sp.status='done' AND COALESCE(sp.nsfw,0)=0 "
            "AND sp.final_path IS NOT NULL AND sp.social_post_id IS NULL "
            "ORDER BY sp.id DESC LIMIT 5").fetchall()
        for r in rows:
            if r["final_path"] and Path(r["final_path"]).is_file():
                return {"project_id": r["id"]}
    except Exception:
        pass
    return {}


def _aa_resell_draft(conn):
    """A REAL photographed item that never got a listing: the newest photo in
    RESELL_UPLOADS not referenced by any listing (image_path / listing images /
    an earlier auto-draft's description). Creates a local DRAFT stub naming the
    photo so the owner can finish it — {} when every photo is accounted for."""
    import re
    from pathlib import Path
    try:
        from config import RESELL_UPLOADS
        from deps import IMAGE_EXTS
    except Exception:
        return {}
    try:
        if not Path(RESELL_UPLOADS).is_dir():
            return {}
        files = sorted((f for f in Path(RESELL_UPLOADS).iterdir()
                        if f.is_file() and f.suffix.lower() in IMAGE_EXTS),
                       key=lambda f: f.stat().st_mtime, reverse=True)[:20]
        if not files:
            return {}
        refs = set()
        for sql in ("SELECT image_path AS p FROM resell_listings WHERE image_path IS NOT NULL",
                    "SELECT image_path AS p FROM resell_listing_images"):
            try:
                refs |= {Path(str(r["p"])).name for r in conn.execute(sql).fetchall()}
            except Exception:
                pass
        for f in files:
            if f.name in refs:
                continue
            try:      # an earlier auto-draft already names this photo → skip
                if conn.execute("SELECT 1 FROM resell_listings WHERE description LIKE ? LIMIT 1",
                                (f"%{f.name}%",)).fetchone():
                    continue
            except Exception:
                pass
            stem = f.stem
            if re.fullmatch(r"resell_\d+_\d+", stem):
                title = f"Unlisted item (photo {f.name})"
            else:
                title = stem.replace("_", " ").replace("-", " ").strip().title()[:80] \
                        or f"Unlisted item (photo {f.name})"
            desc = (f"Draft auto-created for the unattached photo {f} — "
                    f"attach the photo, then add condition, price and details before posting.")
            return {"title": title, "description": desc}
    except Exception:
        pass
    return {}


AUTO_ARGS = {
    "gen_image": _aa_gen_image,
    "gen_music": _aa_gen_music,
    "studio_short": _aa_studio_short,
    "social_caption": _aa_social_caption,
    "social_draft": _aa_social_draft,
    "research": _aa_research,
    "deep_research": _aa_deep_research,
    # delegate-only builders (see JESUS_PUBLISH_CAPS — not agent_safe, not in DEPT_CAPS)
    "social_publish": _aa_social_publish,
    "publish_3d": _aa_publish_3d,
    # production chain (video / 3D / studio pipeline / resell) — argument
    # COMPOSITION only; every one of these caps stays behind the exact same
    # master + per-cap gates in invoke(), and each builder returns {} when
    # nothing is in the right state (the caller then simply skips).
    "gen_video": _aa_gen_video,
    "gen_video_chain": _aa_gen_video_chain,
    "gen_3d": _aa_gen_3d,
    "studio_from_trend": _aa_studio_from_trend,
    "studio_produce": _aa_studio_produce,
    "studio_audio": _aa_studio_audio,
    "studio_assemble": _aa_studio_assemble,
    "studio_export": _aa_studio_export,
    "resell_draft": _aa_resell_draft,
}


# ── the single-3060 guard: is a product already in flight anywhere? ──────────
_BUSY_SQL = (
    "SELECT COUNT(*) FROM generations WHERE status IN ('queued','generating')",
    "SELECT COUNT(*) FROM videos      WHERE status IN ('queued','generating','pending')",
    "SELECT COUNT(*) FROM audio_clips WHERE status IN ('queued','generating','pending')",
    "SELECT COUNT(*) FROM models3d    WHERE status IN ('queued','generating','pending')",
)


def _content_busy(conn=None):
    """True while anything content-shaped is in flight (a world_auto creation, a
    recent still-running cap, ANY queued/generating GPU row) or the publish
    backlog is full. Both auto paths check this — one product at a time, ever."""
    try:
        import world_auto
        if world_auto._state["running"]:
            return True
        if world_auto._publish_backlog() >= world_auto.BACKLOG_MAX:
            return True
    except Exception:
        pass
    own = conn is None
    if own:
        conn = get_conn()
    try:
        try:      # NOTE: no ensure() here — never commit on a borrowed (sim) conn
            if conn.execute("SELECT COUNT(*) FROM world_cap_runs WHERE status='running' "
                            "AND created_at > datetime('now','-15 minutes')").fetchone()[0]:
                return True
        except Exception:
            pass
        for sql in _BUSY_SQL:
            try:
                if conn.execute(sql).fetchone()[0]:
                    return True
            except Exception:
                pass
        return False
    finally:
        if own:
            conn.close()


def _gates_ok():
    """All the masters that gate ANY autonomous cap invocation."""
    if not (auto_on() and master_on()):
        return False
    try:
        import world_control
        if not world_control.master_on():          # company-wide pause wins
            return False
    except Exception:
        pass
    try:
        import world_auto
        if not world_auto._active_now():           # same active hours as creation
            return False
    except Exception:
        pass
    return True


def agent_work(conn, agent):
    """A creative-dept agent 'operates' by ACTUALLY making something: draw one
    ENABLED dept-appropriate capability and fire it (args auto-built). Returns
    {'goal','cap_id'} for the work board, or None when gated/throttled/busy —
    the scheduler then falls through to the agent's next priority instead of
    faking 'on the clock'. Called from world_work._wg_operate on the sim conn
    (reads only — the invoke itself runs on its own connection, off-thread)."""
    dept = agent.get("dept") or ""
    cap_ids = DEPT_CAPS.get(dept)
    if not cap_ids or not _gates_ok():
        return None
    now = time.time()
    try:
        gap = max(5, int(get_setting(OPERATE_INTERVAL_KEY, str(OPERATE_INTERVAL_DEFAULT)))) * 60
        dgap = max(5, int(get_setting(OPERATE_DEPT_KEY, str(OPERATE_DEPT_DEFAULT)))) * 60
    except Exception:
        gap, dgap = OPERATE_INTERVAL_DEFAULT * 60, OPERATE_DEPT_DEFAULT * 60
    with _operate_lock:
        _operate_load()
        if now - _operate["last"] < gap or now - _operate["dept"].get(dept, 0.0) < dgap:
            return None
        pool = [_CAP[cid] for cid in cap_ids
                if cid in _CAP and gate_on(_CAP[cid])
                and (not _CAP[cid].get("args") or cid in AUTO_ARGS)]
        if not pool or _content_busy(conn):
            return None
        cap = random.choice(pool)
        _operate["last"] = now                     # claim the window before the
        _operate["dept"][dept] = now               # thread runs — no double-fire
    args = {}
    try:
        if cap["id"] in AUTO_ARGS:
            args = AUTO_ARGS[cap["id"]](conn) or {}
    except Exception:
        logger.exception("auto-args failed for %s", cap["id"])
    snippet = next((" ".join(str(v).split())[:60] for v in args.values() if v), cap["label"])
    goal = f"{_GOAL_VERB.get(cap['id'], 'producing')}: {snippet}"
    name = agent.get("name") or "The Company"

    def _fire():
        try:                                       # persist the stamps for restarts
            _set(OPERATE_LAST_KEY, now)
            _set(f"{OPERATE_LAST_KEY}_{dept}", now)
        except Exception:
            pass
        invoke(cap["id"], args, actor="agent", agent=name)
        try:
            import world_ops as wo
            wo.note(f"🛠️ {name} got to work: {goal}", kind="info", from_agent=name)
        except Exception:
            pass
    threading.Thread(target=_fire, daemon=True).start()
    return {"goal": goal, "cap_id": cap["id"]}


# ── the (off-by-default) agent auto-loop — rides the world_auto daemon tick ──
def maybe_agent_cycle(now=None):
    """Called from world_auto's existing loop (~1/min). When world_caps_auto is
    ON (default OFF) and the interval has elapsed, ONE random agent invokes one
    enabled agent_safe capability (args auto-built for content caps). Skips —
    without consuming the window — while anything is already in flight, so the
    GPU never carries two products. Publishing/spending caps are never eligible;
    everything still flows through the same prayer/budget gates downstream."""
    if not _gates_ok():
        return None
    now = now or time.time()
    try:
        last = float(get_setting(AUTO_LAST_KEY, "0") or 0)
        interval = max(30, int(get_setting(AUTO_INTERVAL_KEY, str(AUTO_INTERVAL_DEFAULT)))) * 60
    except Exception:
        last, interval = 0, AUTO_INTERVAL_DEFAULT * 60
    if now - last < interval:
        return None
    if _content_busy():
        return None                                # retry next minute, window intact
    pool = [c for c in CATALOG
            if c.get("agent_safe") and gate_on(c)
            and (not c.get("args") or c["id"] in AUTO_ARGS)]
    if not pool:
        return None
    _set(AUTO_LAST_KEY, str(now))
    cap = random.choice(pool)
    agent = None
    conn = get_conn()
    try:
        row = conn.execute("SELECT name FROM world_agents WHERE dept=? ORDER BY RANDOM() LIMIT 1",
                           (cap.get("dept") or "trends",)).fetchone()
        agent = row["name"] if row else None
        args = {}
        if cap["id"] in AUTO_ARGS:
            try:
                args = AUTO_ARGS[cap["id"]](conn) or {}
            except Exception:
                logger.exception("auto-args failed for %s", cap["id"])
    finally:
        conn.close()
    res = invoke(cap["id"], args, actor="agent", agent=agent)
    try:
        import world_ops as wo
        wo.note(f"⚡ {agent or 'The Company'} used a capability on their own: {cap['label']}",
                kind="info", from_agent=agent)
    except Exception:
        pass
    return res

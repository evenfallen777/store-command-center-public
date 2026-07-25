"""
The Company — shared kernel.

Constants, the agent roster, seeding, live activity, small persistence/logging
helpers, and — importantly — the SINGLE gateway through which every LLM call in the
world must pass (`run_llm_job`). Routing all model work through the orchestrator's
queue is what stops us from "dumping models on the GPU": the job only runs once the
orchestrator has arranged VRAM (freed ComfyUI, borrowed/loaded the LLM), so world
thoughts/opinions can never collide with an in-flight image/video render.
"""
import time, json, math
from pathlib import Path

from deps import get_conn, orch          # shared kernel: DB + GPU orchestrator
try:
    from config import BASE
except Exception:
    BASE = Path(__file__).parent.parent

OPENCLAW_DB  = Path.home() / ".openclaw" / "state" / "openclaw.sqlite"
WORLD_ASSETS = BASE / "static" / "world_assets"      # served at /static/world_assets/
AGENT_LOG_DIR = BASE / "world_agents"                # per-agent + town markdown journals

NEEDS = ["energy", "fun", "social", "fulfillment", "hunger"]
LEISURE = ["bar", "arcade", "tv", "park", "cafe"]

# ── Departments (office desks): key → (label, accent colour) ───────────────────
DEPARTMENTS = {
    "storefront": ("Storefront",   "#f59e0b"),
    "image":      ("Image Studio", "#a78bfa"),
    "video":      ("Video Studio", "#f472b6"),
    "audio":      ("Audio Studio", "#34d399"),
    "models3d":   ("3D Studio",    "#60a5fa"),
    "publishing": ("Publishing",   "#22d3ee"),
    "devlab":     ("Dev Lab",      "#f87171"),
    "resell":     ("Resell Desk",  "#fbbf24"),
    "trends":     ("Trends",       "#c084fc"),
    # the store grew — the Company grows with it (portal/social/finance/netsec)
    "portal":     ("Portal / WP",  "#2dd4bf"),
    "social":     ("Social Desk",  "#38bdf8"),
    "finance":    ("Finance Desk", "#eab308"),
    "netsec":     ("Network Sec",  "#94a3b8"),
    "research":   ("Research Lab", "#818cf8"),
    # four standalone-building depts (NOT HQ rooms — self-contained places like the
    # Research Lab). They give the store's previously "homeless" systems a body:
    # Mail (IMAP/SMTP + quotes), Homelab (Docker + *arr), Pearl (PRL miner),
    # AI Assistant (agentic loop). Only Assistant has a real activity signal today.
    "mail":       ("Mail Room",    "#f9a8d4"),
    "homelab":    ("Homelab",      "#7dd3fc"),
    "pearl":      ("Pearl Mine",   "#a7f3d0"),
    "assistant":  ("AI Assistant", "#d8b4fe"),
}

# How long a *completed* unit of discrete work (a WooCommerce push, a money
# mission, a security finding, a social post…) keeps its department's production
# line running so the ship is actually visible. The GPU studios (image/video/…)
# have long-lived 'queued/generating' rows and don't need this; the store's other
# arms finish work in an instant, so we light their desk for a short window after.
DEPT_WORK_WINDOW_MIN = 15

# The 8 real OpenClaw agents → named, persistent characters.
OPENCLAW_AGENTS = [
    ("openclaw_engineer", "Ozzy",   "devlab",     "#f87171"),
    ("wordpress_engineer","Wendy",  "publishing", "#22d3ee"),
    ("docker_engineer",   "Dex",    "devlab",     "#fb923c"),
    ("nextcloud_engineer","Nova",   "publishing", "#38bdf8"),
    ("agent_search",      "Sable",  "trends",     "#c084fc"),
    ("agent_claude",      "Cleo",   "devlab",     "#f472b6"),
    ("coding_agent",      "Cody",   "devlab",     "#a3e635"),
    ("agent_store",       "Stella", "storefront", "#f59e0b"),
]

# Special leaders — NOT assigned work. Their wellbeing mirrors those they serve:
# the Mayor's mood tracks the whole town's happiness; the Boss's tracks the workers'.
# They spend the company fund on upgrades (routed through the GitHub/dev-swarm system).
# (key, name, dept, color, kind, home-location)
SPECIAL_AGENTS = [
    ("mayor", "Mayor Vex", "civic", "#fde047", "mayor", "townhall"),
    ("boss",  "Boss Kane", "exec",  "#fb7185", "boss",  "exec"),
]

# Reusable job-class workers (persistent, reused when a job of the class fires).
WORKER_POOL = [
    ("w_image_1",  "Pip",   "image",    "image",     "#a78bfa"),
    ("w_image_2",  "Indi",  "image",    "image",     "#c4b5fd"),
    ("w_video_1",  "Vic",   "video",    "video",     "#f472b6"),
    ("w_audio_1",  "Ada",   "audio",    "audio",     "#34d399"),
    ("w_3d_1",     "Trip",  "models3d", "models3d",  "#60a5fa"),
    ("w_etsy_1",   "Etta",  "etsy",     "storefront","#f59e0b"),
    ("w_resell_1", "Reese", "resell",   "resell",    "#fbbf24"),
    ("w_portal_1", "Polly", "portal",   "portal",    "#2dd4bf"),
    ("w_trends_1", "Trent", "trends",   "trends",    "#c084fc"),
    ("w_social_1", "Sunny", "social",   "social",    "#38bdf8"),
    ("w_fin_1",    "Penny", "finance",  "finance",   "#eab308"),
    ("w_sec_1",    "Gale",  "netsec",   "netsec",    "#94a3b8"),
    # Research Geniuses — the Research Lab's resident researchers (Research tab)
    ("w_res_1",    "Newton", "research", "research",  "#818cf8"),
    ("w_res_2",    "Curie",  "research", "research",  "#a5b4fc"),
    ("w_res_3",    "Vinci",  "research", "research",  "#6366f1"),
    # the four formerly-homeless systems — each gets a resident worker bound to its
    # own standalone building (Mail Room / Homelab / Pearl Mine / AI Assistant).
    ("w_mail_1",   "Miles",  "mail",      "mail",      "#f9a8d4"),
    ("w_lab_1",    "Docky",  "homelab",   "homelab",   "#7dd3fc"),
    ("w_pearl_1",  "Clara",  "pearl",     "pearl",     "#a7f3d0"),
    ("w_asst_1",   "Jarvis", "assistant", "assistant", "#d8b4fe"),
]

# The 5 Oracle analysts are named after their LLMs (see DEFAULT_ANALYSTS in
# routers/oracle/_base.py) — great for the tournament board, awful as a citizen's
# name. Give each a persona for the world body; the raw model id is kept in
# world_agents.model_id (detail-panel only) so nothing forecasting-side changes.
ORACLE_PERSONAS = {
    "GLM-4.7":         "Delphi",
    "GLM-4.6v":        "Pythia",
    "Qwen3.5-9B":      "Sibyl",
    "Qwen-Coder-32B":  "Cassandra",
    "Qwen3-Coder-30B": "Merlin",
}

def _oracle_persona(model_name):
    """Persona for an oracle analyst's world body; unknown analysts (custom /
    renamed rows) fall back to "Oracle <short id>" instead of the raw model id."""
    if model_name in ORACLE_PERSONAS:
        return ORACLE_PERSONAS[model_name]
    return "Oracle " + model_name[:12]


def _migrate_oracle_personas(conn, c):
    """One-time: the 5 oracle world_agents rows seeded before ORACLE_PERSONAS
    existed still show the raw model id as their name (seed() never overwrites
    an existing row). Rename them to their persona. Idempotent — guarded by a
    world_meta flag, and each UPDATE only fires if the name is STILL the exact
    old model id (a user rename, or a previous run of this migration, is left
    alone)."""
    if mget(c, "world_oracle_personas_migrated", "") == "1":
        return
    try:
        import re as _re
        for model_name, persona in ORACLE_PERSONAS.items():
            key = "oracle_" + _re.sub(r"[^a-z0-9]+", "_", model_name.lower()).strip("_")
            c.execute("UPDATE world_agents SET name=?, model_id=COALESCE(model_id,?) "
                      "WHERE key=? AND name=?", (persona, model_name, key, model_name))
    except Exception:
        pass
    mset(c, "world_oracle_personas_migrated", "1")


# ── Economy ───────────────────────────────────────────────────────────────────
ITEM_COST = 30          # coins to conjure a new prop/item
UPGRADES = [
    {"id": "chair",    "label": "Ergonomic Chair",    "cost": 40,  "mult": 0.25, "desc": "+25% earnings"},
    {"id": "keyboard", "label": "Mechanical Keyboard", "cost": 60,  "mult": 0.20, "desc": "+20% earnings"},
    {"id": "monitors", "label": "Dual Monitors",       "cost": 90,  "mult": 0.35, "desc": "+35% earnings"},
    {"id": "espresso", "label": "Espresso Machine",    "cost": 140, "mult": 0.50, "desc": "+50% earnings"},
]
UPGRADES_BY_ID = {u["id"]: u for u in UPGRADES}

# Themed desk tool each department's worker will "want" (the world-builder hands
# these out so studios fill with relevant pixel props over time).
DEPT_TOOL = {
    "storefront": "cash register",   "image":   "drawing tablet",
    "video":      "film camera",     "audio":   "synthesizer keyboard",
    "models3d":   "3d printer",      "publishing": "printing press",
    "devlab":     "server rack",     "resell":  "cardboard shipping box",
    "trends":     "crystal ball",    "research": "microscope",
    "mail":       "mailbox",         "homelab": "server rack",
    "pearl":      "mining pickaxe",  "assistant": "robot assistant",
}


# ── pixel-art prompt (crisp sprites, not soft SDXL renders) ───────────────────
def pixel_prompt(label, theme="futuristic"):
    """Prompt tuned for true pixel-art output: flat colors + hard outline + no
    gradients render far crisper after the nearest-neighbour downscale."""
    style = "sleek futuristic sci-fi" if theme == "futuristic" else "retro"
    return (f"{label}, {style} pixel art sprite, 16-bit game asset, flat solid colors, "
            f"bold dark outline, no gradients, no anti-aliasing, single centered object, "
            f"plain flat background, crisp clean pixels")


# ── the single LLM-queue gateway ──────────────────────────────────────────────
def run_llm_job(job, desc="world:llm", wait=0, model=None):
    """Submit an LLM closure to the orchestrator queue. The job runs in the orch
    worker (which frees ComfyUI + loads/verifies the model first), so it never
    collides with image/video gen on the shared GPU.

    model  → if given, the orch guarantees that specific model is resident (verified)
             before the job runs; else the job borrows whatever is loaded.
    wait=0  → fire-and-forget (the job must persist its own result); returns None.
    wait>0  → block up to `wait`s and return the job's return value (or None).
    """
    tid = orch.submit_llm(job, desc, model=model, priority=2)   # background world cognition
    if not wait:
        return None
    end = time.time() + wait
    while time.time() < end:
        p = orch.poll(tid)
        st = p["status"]
        if st == "done":
            return p["result"]
        if st in ("error", "cancelled", "not_found"):
            return None
        time.sleep(0.4)
    orch.cancel(tid)
    return None


# ── meta / math helpers ───────────────────────────────────────────────────────
def mget(c, key, default=None):
    row = c.execute("SELECT value FROM world_meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default

def mset(c, key, value):
    c.execute("INSERT INTO world_meta (key,value) VALUES (?,?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))

def level_for(xp):
    return 1 + int(math.sqrt(max(0, xp) / 120.0) * 3)


# ── logging (per-agent + town markdown journals) ──────────────────────────────
def log_agent(agent_key, name, line):
    try:
        AGENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        p = AGENT_LOG_DIR / f"{agent_key}.md"
        if not p.exists():
            p.write_text(f"# {name} — journal\n\n")
        with p.open("a") as f:
            f.write(f"- {time.strftime('%Y-%m-%d %H:%M')} — {line}\n")
    except Exception:
        pass

def log_town(line):
    try:
        AGENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        p = AGENT_LOG_DIR / "_TOWN_HALL.md"
        if not p.exists():
            p.write_text("# The Company — Town Hall log\n\n")
        with p.open("a") as f:
            f.write(f"- {time.strftime('%Y-%m-%d %H:%M')} — {line}\n")
    except Exception:
        pass


# ── seeding + live activity ───────────────────────────────────────────────────
def seed(conn):
    """Idempotently ensure every roster character exists (never overwrites a row,
    so user renames / XP / levels survive)."""
    c = conn.cursor()
    existing = {r["key"] for r in c.execute("SELECT key FROM world_agents").fetchall()}
    for key, name, dept, color in OPENCLAW_AGENTS:
        if key not in existing:
            c.execute("INSERT INTO world_agents (key,name,kind,job_class,dept,color,location,state) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (key, name, "openclaw", "agent", dept, color, "home", "idle"))
    for key, name, job_class, dept, color in WORKER_POOL:
        if key not in existing:
            c.execute("INSERT INTO world_agents (key,name,kind,job_class,dept,color,location,state) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (key, name, "worker", job_class, dept, color, "home", "idle"))
    # DEV-SWARM crew — the real coding pipeline's roles, as citizens
    for role, name, color in (("architect", "Archie", "#e879a0"), ("planner", "Blue", "#7dd3fc"),
                              ("coder", "Cassie", "#a3e635"), ("tester", "Tess", "#fb923c")):
        key = f"swarm_{role}"
        if key not in existing:
            c.execute("INSERT INTO world_agents (key,name,kind,job_class,dept,color,location,state) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (key, name, "worker", "swarm", "devlab", color, "home", "idle"))
    # ORACLE agents — every active model in the oracle roster gets a body.
    # Display name is a persona (ORACLE_PERSONAS) — the raw model id is kept in
    # model_id for the detail panel only, never shown as the citizen's name.
    try:
        import re as _re
        for r in c.execute("SELECT name FROM oracle_agents WHERE active=1").fetchall():
            model_name = r["name"]
            key = "oracle_" + _re.sub(r"[^a-z0-9]+", "_", model_name.lower()).strip("_")
            if key not in existing and not c.execute(
                    "SELECT 1 FROM world_agents WHERE key=?", (key,)).fetchone():
                c.execute("INSERT INTO world_agents (key,name,kind,job_class,dept,color,location,state,model_id) "
                          "VALUES (?,?,?,?,?,?,?,?,?)",
                          (key, _oracle_persona(model_name), "worker", "oracle", "devlab", "#8b5cf6",
                           "home", "idle", model_name))
    except Exception:
        pass
    _migrate_oracle_personas(conn, c)
    for key, name, dept, color, kind, home in SPECIAL_AGENTS:
        if key not in existing:
            c.execute("INSERT INTO world_agents (key,name,kind,job_class,dept,color,location,state,coins) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (key, name, kind, kind, dept, color, home, "overseeing", 100))
    conn.commit()


def live_activity():
    """(activity, oc_active): in-progress store work per job_class, and the set of
    OpenClaw agent ids currently running a task. Defensive — a missing source just
    contributes nothing. Used for the desk 'busy' animation + the header readout."""
    import sqlite3
    activity = {}
    conn = get_conn(); c = conn.cursor()

    def _count(sql):
        try: return c.execute(sql).fetchone()[0]
        except Exception: return 0

    activity["image"]    = _count("SELECT COUNT(*) FROM generations WHERE status IN ('queued','generating')")
    activity["video"]    = _count("SELECT COUNT(*) FROM videos WHERE status IN ('queued','generating','pending')") \
                         + _count("SELECT COUNT(*) FROM video_chains WHERE status IN ('queued','generating','pending')")
    activity["audio"]    = _count("SELECT COUNT(*) FROM audio_clips WHERE status IN ('queued','generating','pending')")
    activity["models3d"] = _count("SELECT COUNT(*) FROM models3d WHERE status IN ('queued','generating','pending')")
    activity["resell"]   = _count("SELECT COUNT(*) FROM resell_auto_tasks WHERE status='pending'") \
                         + _count("SELECT COUNT(*) FROM automation_log WHERE status='running' AND created_at > datetime('now','-10 minutes')")

    # ── the store's OTHER arms: desks that used to sit dark now light up on the
    # SAME real backend signals world_sim already PAYS them for (WORK_METRICS).
    # These finish work in an instant, so a recent-completion window keeps the
    # dept's line running (and shipping) long enough to see. Every count is
    # guarded by _count → a missing table contributes 0, never raises. ADDITIVE:
    # this never touches the five GPU-studio signals above. Keys are DEPT names
    # (world-factory.js animates a room while activity[room.dept] > 0).
    _w = f"datetime('now','-{DEPT_WORK_WINDOW_MIN} minutes')"
    activity["portal"]     = _count(f"SELECT COUNT(*) FROM portal_pushes WHERE kind='product' AND pushed_at > {_w}")          # WooCommerce/affiliate/Etsy/Cults3D product pushes
    activity["publishing"] = _count(f"SELECT COUNT(*) FROM portal_pushes WHERE kind IN ('portfolio','media') AND pushed_at > {_w}")  # media/portfolio published to WP/Nextcloud
    activity["social"]     = _count(f"SELECT COUNT(*) FROM social_posts WHERE COALESCE(posted_at, created_at) > {_w}")        # composed/posted social content
    activity["trends"]     = _count(f"SELECT COUNT(*) FROM proposals WHERE COALESCE(updated_at, created_at) > {_w}")          # Etsy/Printify/trend proposals
    activity["storefront"] = _count(f"SELECT COUNT(*) FROM designs WHERE status='published' AND updated_at > {_w}")           # listings going live (Etsy/Printify)
    activity["devlab"]     = _count(f"SELECT COUNT(*) FROM swarm_events WHERE created_at > {_w}")                             # dev-swarm pipeline steps
    activity["netsec"]     = _count(f"SELECT COUNT(*) FROM security_findings WHERE COALESCE(updated_at, first_seen) > {_w}")  # security scans/findings
    activity["finance"]    = _count(f"SELECT COUNT(*) FROM money_missions   WHERE COALESCE(updated_at, created_at) > {_w}") \
                           + _count(f"SELECT COUNT(*) FROM crypto_backtests WHERE created_at > {_w}") \
                           + _count(f"SELECT COUNT(*) FROM wallet_sends     WHERE COALESCE(updated_at, created_at) > {_w}")   # money missions + backtests + sends
    # AI Assistant — the ONLY one of the four formerly-homeless systems with a real,
    # persisted activity signal: any recent assistant/tool message lights the room.
    # assistant_messages.ts is a Unix EPOCH (time.time()), so it compares to an epoch
    # offset, NOT the datetime('now',...) window the desks above use. Guarded → 0 if absent.
    activity["assistant"]  = _count(f"SELECT COUNT(*) FROM assistant_messages WHERE ts > strftime('%s','now') - {DEPT_WORK_WINDOW_MIN * 60}")
    # Mail / Homelab / Pearl have their building + worker, but NO signal table exists to
    # animate them yet (see world_sim.WORK_METRICS note) — deliberately left un-fabricated.
    conn.close()

    try:
        st = orch.status()
        if st.get("active_images", 0) > 0 or st.get("image") == "busy":
            activity["image"] = max(activity.get("image", 0), 1)
    except Exception:
        pass

    oc_active = set()
    if OPENCLAW_DB.exists():
        try:
            oc = sqlite3.connect(f"file:{OPENCLAW_DB}?mode=ro&immutable=1", uri=True, timeout=2)
            for r in oc.execute("SELECT DISTINCT agent_id FROM task_runs WHERE status='running'"):
                if r[0]:
                    oc_active.add(r[0])
            oc.close()
        except Exception:
            pass
    return {k: v for k, v in activity.items() if v}, oc_active

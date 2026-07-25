"""
The Company — systems registry (the master status board's single source of truth).

A DECLARATIVE catalog of every system, subsystem and control surface the app runs,
plus a live `snapshot()` that stamps each with its REAL current status by reading the
actual settings / gates (world_settings, world_ops, defense, world_control). This is
the read-only, machine-checkable version of the hand audit — and the intended seed of
a future single-source settings registry, so keep CATALOG clean and declarative.

Status vocabulary (per system):
  enabled    — its controlling setting resolves ON (green)
  disabled   — its controlling setting resolves OFF (grey)
  gated      — an always-approval gate is active for it (amber)
  orphan     — a setting exists but there is NO UI to change it (red)
  invisible  — the system RUNS but has no leg in the Company world (red)
  infra      — plumbing / a control surface itself (not on/off) (neutral)

Design rules:
  • Import-safe: importing this module NEVER touches the DB.
  • snapshot() NEVER raises — every live read is defended; on any failure a system
    falls back to its declared `default_status` so the board still renders.
"""
from pathlib import Path

try:
    from config import BASE
except Exception:  # pragma: no cover - config always present in-app
    BASE = Path(__file__).resolve().parent.parent


# ── categories ────────────────────────────────────────────────────────────────
CATEGORIES = [
    {"key": "world",   "label": "🌍 World Simulation",
     "desc": "The Company town's living systems — the sim and everything that gives it a visible leg."},
    {"key": "studio",  "label": "🎨 Studio & Commerce",
     "desc": "Creation engines and the sales/publishing channels they feed."},
    {"key": "infra",   "label": "🛠️ Infrastructure, Dev, Security & Crypto",
     "desc": "The operator-facing machinery: dev-swarm, security, homelab, assistants, coins."},
    {"key": "control", "label": "🎛️ Control Plane",
     "desc": "The setting surfaces that govern everything above — and the settings that have no UI yet."},
]


# ── catalog ───────────────────────────────────────────────────────────────────
# Each entry is declarative. `classify` tells snapshot() how to derive live status:
#   toggle  → read `setting_key` as a boolean            → enabled | disabled
#   always  → runs unconditionally, no toggle            → enabled
#   invisible → runs but has no world leg                → invisible
#   orphan  → `setting_key` exists but has no UI          → orphan
#   gate    → `gate` kind is in world_ops.gated_kinds()   → gated | disabled
#   mode    → world_ops automation_mode                  → enabled(budget) | gated(review)
#   infra   → a control surface / plumbing               → infra
def _S(key, label, category, classify, *, subsystems=None, setting_key=None,
       gate=None, tab=None, world_visible=False, notes=""):
    return {
        "key": key, "label": label, "category": category, "classify": classify,
        "subsystems": subsystems or [], "setting_key": setting_key, "gate": gate,
        "tab": tab, "world_visible": bool(world_visible), "notes": notes,
    }


CATALOG = [
    # ── World Simulation ─────────────────────────────────────────────────────
    _S("world_sim", "World Sim", "world", "always", tab="world", world_visible=True,
       subsystems=["ticker cadence", "real-elapsed ticks", "pay-for-real-work"],
       notes="Core town engine — always ticking (cognition cadence is world_llm_enabled)."),
    _S("world_skills", "Skills", "world", "always", tab="world", world_visible=True,
       subsystems=["woodcutting", "mining", "fishing", "stock targets"],
       notes="RuneScape-style gathering. Always on — no god toggle."),
    _S("world_mood", "Mood", "world", "always", tab="world", world_visible=True,
       subsystems=["thought ledger", "mental breaks"],
       notes="Always on — no god toggle."),
    _S("world_items", "Items & Economy", "world", "always", tab="world", world_visible=True,
       subsystems=["catalog", "inventory", "food", "shop trips", "placements"]),
    _S("world_construct", "Construction", "world", "always", tab="world", world_visible=True,
       subsystems=["build orders", "world_structures lifecycle"],
       notes="Always on — no god toggle."),
    _S("world_tech", "Tech Ladder", "world", "always", tab="world", world_visible=True,
       subsystems=["wood→stone→bronze→iron→steel"],
       notes="Always on — no god toggle."),
    _S("world_research", "Research (Research Lab)", "world", "always", tab="research", world_visible=True,
       subsystems=["research tree", "prerequisites", "geniuses"],
       notes="Fully wired to the Research Lab tab."),
    _S("world_raid", "Raids (monsters)", "world", "always", tab="network-security", world_visible=True,
       subsystems=["waves", "walls", "turrets", "duels"],
       notes="Security threats surface as monsters. Fully visible."),
    _S("world_security", "Security beats (→ NetSec)", "world", "toggle",
       setting_key="security_monitor_enabled", tab="network-security", world_visible=True,
       subsystems=["log scanning", "security beats"],
       notes="Store-wide log scan feeds the world + Network Security tab."),
    _S("world_leader", "Leaders (Mayor/Boss)", "world", "toggle",
       setting_key="world_leader_upgrades", tab="world", world_visible=True,
       subsystems=["Boss Kane", "Mayor Vex", "real swarm spend on approval"]),
    _S("world_strategy", "The Republic", "world", "toggle",
       setting_key="world_auto_govern_min", tab="world", world_visible=True,
       subsystems=["assess", "propose", "convene", "strategy engine"],
       notes="Self-governance cadence (minutes); 0 = off. Cascaded by Company Control."),
    _S("world_schedule", "Town Schedule", "world", "always", tab="world", world_visible=True,
       subsystems=["24h timetable", "Sleep/Work/Free/Anything"]),
    _S("world_space", "Space Program (JASA)", "world", "toggle",
       setting_key="world_space_enabled", tab="world", world_visible=True,
       subsystems=["rocket launches", "moon trips", "return flights"],
       notes="Its ONLY control is the orphan setting world_space_enabled (no UI) — see Control Plane."),
    _S("world_moon", "Moon & sky", "world", "toggle",
       setting_key="world_moon_enabled", tab="world", world_visible=True,
       subsystems=["drifting moon", "ground shadow", "generated texture"]),
    _S("world_bills", "Production Bills", "world", "toggle",
       setting_key="world_bills_drive", tab="world", world_visible=True,
       subsystems=["bill scheduler", "Produce cadence"],
       notes="world_bills_drive has no UI (orphan setting) — see Control Plane."),
    _S("world_auto", "Autonomous Creation (→ Studio)", "world", "toggle",
       setting_key="world_auto_enabled", tab="studio", world_visible=True,
       subsystems=["image", "music", "video", "3d"],
       notes="Studio desks animate when the crew creates. Cascaded by Company Control."),
    _S("world_creators", "Creators / lyrics", "world", "toggle",
       setting_key="world_music_lyrics", tab="world", world_visible=True,
       subsystems=["own-lyrics ACE-Step songs"]),
    _S("world_orchestra", "Orchestra (macro clock)", "world", "always", tab="world", world_visible=True,
       subsystems=["macro-clock conductor"],
       notes="Always on — no god toggle."),
    _S("world_rank", "Ranking", "world", "always", tab="world", world_visible=True,
       notes="Runs, rendered in the world UI. No god toggle."),
    _S("world_learn", "Adaptive Learning", "world", "always", tab="world", world_visible=True,
       subsystems=["online policy learning", "world_policy"],
       notes="Runs, rendered in the world UI. No god toggle."),
    _S("world_balance", "Balance tuning", "world", "infra", tab="world", world_visible=False,
       notes="Every tuning number in one place. A pure constants module — no on/off state."),
    _S("world_ops", "God Console (ops backbone)", "world", "always", tab="world", world_visible=True,
       subsystems=["prayers queue", "postpaid budget", "PayPal", "community board"],
       notes="The safety backbone — approval queue + real-money ledger (world_ops_ledger)."),
    _S("world_tileset", "Progressive tilesets", "world", "toggle",
       setting_key="world_tileset_auto", tab="world", world_visible=True,
       subsystems=["per-tile agent painting", "QA + style gates"],
       notes="Auto painting default OFF."),
    _S("world_terrain", "Terrain image", "world", "toggle",
       setting_key="world_terrain_image_enabled", tab="world", world_visible=True,
       subsystems=["whole-world ground image"], notes="Default OFF until an image is generated."),
    _S("world_floors", "Interior floors", "world", "toggle",
       setting_key="world_floor_image_enabled", tab="world", world_visible=True,
       subsystems=["shared interior-floor texture"], notes="Default OFF until a floor is generated."),
    _S("world_vision", "Vision QA (sprite judge)", "world", "toggle",
       setting_key="world_vision_enabled", tab="world", world_visible=True,
       subsystems=["best-of-N sprite pick", "feedback retries"]),
    _S("world_sprites", "Per-entity sprite sheets", "world", "toggle",
       setting_key="world_sprites_enabled", tab="world", world_visible=True,
       subsystems=["on-demand action sheets", "auto backfill (off by default)"]),
    _S("world_era", "Civilization eras", "world", "toggle",
       setting_key="world_era_enabled", tab="world", world_visible=True,
       subsystems=["wood→brick→…→moon ladder", "advance/decay cadence"],
       notes="Cosmetic building restyle by real activity/neglect. Default ON; had no board handle."),
    _S("world_era_sprites", "Era building art", "world", "toggle",
       setting_key="world_era_sprites_enabled", tab="world", world_visible=True,
       subsystems=["generated per-(building,era) pixel art"],
       notes="Optional generated art over the procedural era restyle. Default OFF. "
             "Also has a control in Company Control (🏛️ Era building art)."),
    _S("world_run", "Run mode", "world", "always", tab="world", world_visible=True,
       subsystems=["normal/fast/test speed", "world_run_mode"],
       notes="Always ticking; world_run_mode (normal/fast/test) is a 3-way mode, not on/off — "
             "set via Company Control's ⏱️ Run mode picker, not this board."),
    _S("world_taste", "Learned taste", "world", "always", tab="world", world_visible=True,
       subsystems=["thumbs-up/down training", "predicted-approval scoring"],
       notes="Always scoring; surfaced in the God Console (taste %, examples studied). "
             "Its gate threshold is world_taste_min — see Control Plane."),
    _S("world_renew", "Renewal (self-refresh)", "world", "always", tab="world", world_visible=True,
       subsystems=["oldest-prop re-render", "improvement requests → Town Hall"],
       notes="Self-cadenced (world_ticker); no god toggle. Suggestions surface in Town Hall."),

    # ── Studio & Commerce ────────────────────────────────────────────────────
    _S("studio", "Studio (Image/Video/Audio/3D)", "studio", "always", tab="studio", world_visible=True,
       subsystems=["Image", "Video", "Audio", "3D", "Queue"],
       notes="Wired — the Studio desks animate as the crew creates."),
    _S("models", "Models (config hub)", "studio", "always", tab="studio", world_visible=False,
       subsystems=["model registry", "downloads", "LoRAs"],
       notes="Configuration hub — no world leg."),
    _S("etsy_printify", "Etsy / Printify", "studio", "always", tab="etsy-printify", world_visible=False,
       subsystems=["trend scan", "proposals", "publish"],
       notes="INVISIBLE — the storefront/trends desks are dead."),
    _S("proposal_gate", "Proposal Review Gate", "studio", "toggle",
       setting_key="proposal_gate_enabled", tab="etsy-printify",
       subsystems=["LLM judge", "dedup", "auto-weed"],
       notes="LLM judge scores pending proposals 0-100 and weeds/queues them per "
             "proposal_gate_mode (score_only | auto_weed | full_auto). Scheduler runs it "
             "every proposal_gate_interval_min in proposal_gate_batch_size bites; "
             "toggles in Settings → Store & Content."),
    _S("proposal_desk", "Proposal Desk (lanes + feeds)", "studio", "toggle",
       setting_key="proposal_desk_enabled", tab="etsy-printify",
       subsystems=["proposal lanes", "news feed groups", "agent suggestions", "etsy demand signal"],
       notes="Multi-lane proposal origination (proposal_desk.py): categorized news feeds + "
             "the crew's 'Suggested:' ideas + the Etsy hot/not signal become lane-tagged "
             "proposals every proposal_desk_interval_min. Company control-plane system."),
    _S("portal", "Portal → WooCommerce", "studio", "always", tab="portal", world_visible=False,
       subsystems=["affiliate items", "portfolio push"],
       notes="INVISIBLE, and no god surface."),
    _S("resell", "Resell", "studio", "always", tab="resell", world_visible=True,
       subsystems=["analyze", "list", "offers", "browser-post"],
       notes="Visible in the world; no god toggle."),
    _S("finance", "Finance (Money + Crypto)", "studio", "mode", tab="money", world_visible=False,
       subsystems=["demand signals", "missions", "crypto desk"],
       notes="Desk is dead; gated through world_ops (automation_mode)."),
    _S("social", "Social", "studio", "always", tab="social", world_visible=False,
       notes="INVISIBLE — no world leg."),
    _S("mail", "Mail & Quotes", "studio", "always", tab="mail", world_visible=True,
       subsystems=["accounts (IMAP/SMTP + Gmail OAuth)", "business profiles", "FAQ", "order linking"],
       notes="Config-driven mail desk (mail_engine.py) — multiple accounts, editable "
             "business profiles drive the reply drafter. The Mail Room dept (Miles) is its world leg."),
    _S("mail_gate", "Mail auto-reply gate", "studio", "toggle",
       setting_key="mail_gate_enabled", tab="mail", world_visible=True,
       subsystems=["triage classifier", "auto drafts", "guardrailed auto-send", "review trail"],
       notes="The approval gate between incoming email and outgoing replies (mail_gate.py): "
             "manual | auto_draft | full_auto per mail_gate_mode. Quotes/pricing and anything "
             "non-routine are ALWAYS held for a human; every action logs to mail_log. "
             "Scheduler runs it every mail_gate_interval_min in mail_gate_batch_size bites."),
    _S("cults3d", "Cults3D", "studio", "always", tab="cults3d", world_visible=True,
       notes="Rides the 3D desk."),
    _S("library", "Library", "studio", "always", tab="library", world_visible=True,
       notes="A civic building in the world — but no worker."),
    _S("nsfw", "Private Studio (NSFW)", "studio", "toggle",
       setting_key="nsfw_enabled", tab="settings", world_visible=False,
       subsystems=["nsfw_enabled", "nsfw_display", "nsfw_world"],
       notes="Three toggles in Settings → Content (all default off). Not a god surface."),
    _S("oracle", "Oracle", "studio", "always", tab="oracle", world_visible=False,
       notes="INVISIBLE — no world leg."),
    _S("world_sell", "Autonomous listing", "studio", "toggle",
       setting_key="world_sell_auto", tab="etsy-printify", world_visible=False,
       subsystems=["queue Etsy/Printify listing", "revenue sync"],
       notes="The engine behind Company Control's 'Autonomous listing' toggle; queues drafts "
             "as prayers for review. Desks it feeds are dead — see etsy_printify."),

    # ── Infrastructure, Dev, Security & Crypto ───────────────────────────────
    _S("network_security", "Network Security", "infra", "toggle",
       setting_key="security_monitor_enabled", tab="network-security", world_visible=True,
       subsystems=["Command view", "14 defenses", "Guardian", "AI Shield", "Pi-hole"],
       notes="Wired — the Command view calls all defenses (see app/defense.py)."),
    _S("world_public_snapshot", "Public world snapshot (→ example.com)", "infra", "toggle",
       setting_key="world_public_snapshot", tab=None, world_visible=False,
       subsystems=["render + push to public WordPress", "gate_reason() NSFW gate", "leak sweep"],
       notes="SECURITY-RELEVANT: the only outbound-to-public path (app/world_snapshot.py, "
             "polled by scheduler.py). Default OFF. Had ZERO UI until this row — now a real "
             "toggle. See world_snapshot.py's SECURITY MODEL docstring before flipping it on."),
    _S("dev_swarm", "GitHub / Dev Swarm", "infra", "toggle",
       setting_key="swarm_cron_enabled", tab="github", world_visible=True,
       subsystems=["repo mgmt", "dev→master→retail", "local-model swarm"],
       notes="Wired to the world."),
    _S("homelab", "Services (Docker + *arr)", "infra", "always", tab="homelab", world_visible=False,
       notes="INVISIBLE — a major gap; no world leg."),
    _S("agent", "AI Assistant", "infra", "always", tab="agent", world_visible=False,
       subsystems=["agentic tool loop over all endpoints"],
       notes="INVISIBLE — no world leg."),
    _S("graph", "Knowledge Graph", "infra", "always", tab="graph", world_visible=False,
       notes="Cosmetic in the world only."),
    _S("jellycoin", "JellyCoin (JLY)", "infra", "toggle",
       setting_key="world_crypto_mining_enabled", tab="crypto", world_visible=False,
       subsystems=["OpenCL miner", "boost tickets", "NFTs"],
       notes="Partially surfaced in the world; mining default OFF."),
    _S("pearl", "Pearl (PRL)", "infra", "toggle",
       setting_key="pearl_mining_enabled", tab="crypto", world_visible=False,
       subsystems=["proof-of-useful-work L1", "NVIDIA-only miner"],
       notes="INVISIBLE; miner default OFF."),
    _S("xmr", "XMR (Monero) mining", "infra", "toggle",
       setting_key="xmr_mining_enabled", tab="crypto", world_visible=False,
       subsystems=["xmrig (RandomX, CPU)"],
       notes="INVISIBLE; miner default OFF. Agent access gated separately (xmr_agent_access)."),
    _S("peers", "Peers / Federation", "infra", "always", tab="github", world_visible=False,
       subsystems=["invite pairing", "advisory reviews", "lent compute"],
       notes="INVISIBLE — no world leg."),
    _S("mcp", "MCP server", "infra", "infra", tab=None, world_visible=False,
       subsystems=["fastapi-mcp mount at /api/mcp", "every endpoint as a tool"],
       notes="Infrastructure — how OpenClaw drives the Store."),

    # ── Control Plane ────────────────────────────────────────────────────────
    _S("surface_company_settings", "⚙️ Company Settings modal", "control", "infra", tab="world",
       notes="One of 5 setting surfaces."),
    _S("surface_god_console", "🏛️ God Console", "control", "infra", tab="world",
       notes="One of 5 setting surfaces (prayers, gates, budget)."),
    _S("surface_company_control", "🎛️ Company Control", "control", "infra", tab="world",
       subsystems=["master switch", "per-system cascade"],
       notes="One of 5 setting surfaces (world_control SYSTEMS map)."),
    _S("surface_security_command", "🛡️ Security Command", "control", "infra", tab="network-security",
       notes="One of 5 setting surfaces."),
    _S("surface_settings_tab", "🖥️ Settings tab", "control", "infra", tab="settings",
       subsystems=["System", "Models", "Integrations", "Store", "Account", "Prompts", "Systems"],
       notes="One of 5 setting surfaces (this board lives here)."),
    _S("dup_automation_mode", "Automation mode (duplicate surface)", "control", "mode", tab="world",
       notes="world_ops_automation_mode — the real posting gate. Formerly also mirrored by Company "
             "Control's auto-publish toggle; that mirror was dropped as a pure duplicate (2026-07-21)."),
    # 8 (formerly ORPHAN) settings — now given real handles on THIS board.
    # Booleans classify "toggle" (→ enabled/disabled + inline switch); numeric/text
    # settings classify "value" (→ neutral pill + inline editor). No longer orphan.
    _S("orphan_world_space_enabled", "world_space_enabled", "control", "toggle", tab="world",
       setting_key="world_space_enabled", notes="Space Program on/off — edit inline here."),
    _S("orphan_world_bills_drive", "world_bills_drive", "control", "toggle", tab="world",
       setting_key="world_bills_drive", notes="Bills may drive REAL auto-creation — edit inline here."),
    _S("orphan_world_theme", "world_theme", "control", "value", tab="world",
       setting_key="world_theme", notes="World theme string — edit inline here."),
    _S("orphan_world_night_brightness", "world_night_brightness", "control", "value", tab="world",
       setting_key="world_night_brightness", notes="Night brightness (1=default, 0=brightest) — edit inline here."),
    _S("orphan_world_prop_matte", "world_prop_matte", "control", "value", tab="world",
       setting_key="world_prop_matte", notes="Bg-removal (matte) model; blank=auto, 'off'=disable — edit inline here."),
    _S("orphan_world_taste_min", "world_taste_min", "control", "value", tab="world",
       setting_key="world_taste_min", notes="Min predicted-approval (0..1) before automation runs — edit inline here."),
]


# ── setting editability / type (drives the board's inline controls) ────────────
# Explicit type map for the non-boolean settable keys; everything else that is a
# `toggle` is a boolean switch. Values are written via PATCH /api/settings.
_SETTING_TYPES = {
    "world_taste_min":        "float",   # 0..1
    "world_night_brightness": "float",
    "world_theme":            "text",
    "world_vision_model":     "text",
    "world_prop_matte":       "text",
    "world_auto_govern_min":  "int",     # The Republic cadence (minutes)
}
# Fallback current-values for settable keys NOT stored under world_settings.DEFAULTS
# (those live in world_ops / world_gov), so the inline editor pre-fills sensibly.
_VALUE_DEFAULTS = {
    "world_taste_min":       "0.35",
    "world_auto_govern_min": "0",
}


def _setting_type(entry):
    """bool | int | float | text for a settable row, else None (not editable here).
    A row is editable iff it has a setting_key and classifies as a live control."""
    sk = entry.get("setting_key")
    if not sk:
        return None
    if sk in _SETTING_TYPES:
        return _SETTING_TYPES[sk]
    if entry.get("classify") == "toggle":
        return "bool"
    if entry.get("classify") == "value":
        return "text"
    return None


# ── live status resolution ────────────────────────────────────────────────────
def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _live_context(conn):
    """Read every live source ONCE. Each read is defended; a failure just leaves that
    source empty so systems fall back to their declared default status."""
    ctx = {"world": {}, "gated": set(), "mode": "review", "get": (lambda k, d=None: d)}
    try:
        import world_settings
        ctx["world"] = world_settings.get_all(conn)
    except Exception:
        pass
    try:
        import world_ops
        try:
            ctx["gated"] = set(world_ops.gated_kinds())
        except Exception:
            ctx["gated"] = set()
        try:
            ctx["mode"] = world_ops.automation_mode()
        except Exception:
            ctx["mode"] = "review"
    except Exception:
        pass
    try:
        from deps import get_setting
        ctx["get"] = get_setting
    except Exception:
        pass
    return ctx


def _setting_value(key, ctx):
    """Resolve a setting from the world_* snapshot first, else the generic settings table."""
    if key in ctx["world"]:
        return ctx["world"][key]
    return ctx["get"](key, None)


def _status_for(entry, ctx):
    c = entry["classify"]
    try:
        if c == "infra":
            return "infra"
        if c == "invisible":
            return "invisible"
        if c == "orphan":
            return "orphan"
        if c == "value":
            # a numeric/text control surface — neutral (not on/off), always live
            return "infra"
        if c == "mode":
            return "enabled" if ctx["mode"] == "budget" else "gated"
        if c == "gate":
            return "gated" if entry["gate"] in ctx["gated"] else "disabled"
        if c == "always":
            return "enabled"
        if c == "toggle":
            sk = entry["setting_key"]
            if not sk:
                return "enabled"
            val = _setting_value(sk, ctx)
            if entry["key"] == "world_strategy":     # numeric cadence: >0 minutes = on
                try:
                    return "enabled" if int(float(val or 0)) > 0 else "disabled"
                except Exception:
                    return "disabled"
            return "enabled" if _truthy(val) else "disabled"
    except Exception:
        pass
    return "infra"


# ── plugins ───────────────────────────────────────────────────────────────────
def plugins():
    """Scan plugins/<name>/ manifests the same way main.py's loader does, without
    importing any backend. Returns a list of {id, name, version, description, icon,
    view, nav_group, enabled, loaded, frontend_url}. Never raises; empty dir → []."""
    import json
    out = []
    pdir = Path(BASE) / "plugins"
    try:
        if not pdir.is_dir():
            return out
        for sub in sorted(p for p in pdir.iterdir() if p.is_dir()):
            mf = sub / "plugin.json"
            if not mf.is_file():
                continue
            try:
                man = json.loads(mf.read_text())
            except Exception:
                out.append({"id": sub.name, "name": sub.name, "version": "?",
                            "description": "manifest failed to parse", "icon": "⚠️",
                            "view": None, "nav_group": "Plugins",
                            "enabled": False, "loaded": False, "frontend_url": None})
                continue
            backend = sub / man.get("backend", "backend.py")
            sdir = sub / "static"
            fjs = man.get("frontend", "frontend.js")
            frontend_url = (f"/plugins/{sub.name}/{fjs}"
                            if (sdir / fjs).is_file() else None)
            enabled = man.get("enabled", True) is not False
            loaded = enabled and (backend.is_file() or frontend_url is not None)
            out.append({
                "id": sub.name,
                "name": man.get("name", sub.name),
                "version": str(man.get("version", "?")),
                "description": man.get("description", ""),
                "icon": man.get("icon", "🧩"),
                "view": man.get("view"),
                "nav_group": man.get("nav_group", "Plugins"),
                "enabled": bool(enabled),
                "loaded": bool(loaded),
                "frontend_url": frontend_url,
            })
    except Exception:
        pass
    return out


# ── snapshot ──────────────────────────────────────────────────────────────────
def snapshot(conn=None):
    """The catalog enriched with LIVE status. Never raises — any live-read failure
    degrades to declared defaults so the board always renders."""
    own = conn is None
    if own:
        try:
            from deps import get_conn
            conn = get_conn()
        except Exception:
            conn = None
    try:
        ctx = _live_context(conn)
        systems = []
        for e in CATALOG:
            row = dict(e)
            row["status"] = _status_for(e, ctx)
            st = _setting_type(e)
            row["setting_type"] = st
            row["editable"] = st is not None
            if st and e.get("setting_key"):
                val = _setting_value(e["setting_key"], ctx)
                if val is None:
                    val = _VALUE_DEFAULTS.get(e["setting_key"], "")
                row["setting_value"] = str(val)
            else:
                row["setting_value"] = None
            systems.append(row)
    except Exception:
        systems = [dict(e, status="infra", setting_type=None, editable=False,
                        setting_value=None) for e in CATALOG]
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    plist = plugins()
    counts = {
        "total": len(systems),
        "enabled": sum(1 for s in systems if s["status"] == "enabled"),
        "invisible": sum(1 for s in systems if s["status"] == "invisible"),
        "orphan": sum(1 for s in systems if s["status"] == "orphan"),
        "plugins": len(plist),
    }
    return {"categories": CATEGORIES, "systems": systems, "plugins": plist, "counts": counts}

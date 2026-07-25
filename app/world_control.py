"""
The Company — unified automation control plane.

One place the Company owns ALL automation. Two kinds of thing:

  • Autonomy systems — background loops that run on their own (creation, the
    Republic's self-governance, the crew thinking, town meetings, dev-swarm cron,
    security scans). Each has a toggle; a single MASTER switch gates them all.

  • Capabilities ("mini-MCP") — discrete actions the Company can TRIGGER on
    demand and receive a product back (make art/music/video/3D, convene the
    assembly, commission research, scan trends). No autonomy to manage — just
    fire and receive.

Design is deliberately NON-INVASIVE: every autonomy system already reads its own
native setting each tick. We don't rewrite those loops — the master + per-system
toggles simply CASCADE into the native settings (effective = master AND desired).
So flipping the master off writes every native switch off, and each existing
loop stops on its next tick. One tiny exception (dev-swarm cron) gets a global
gate added in scheduler.py.
"""
import json, logging, threading
import httpx
from deps import get_conn, get_setting

logger = logging.getLogger("store")
_LOCAL = "http://127.0.0.1:8787"


# ── settings io ──────────────────────────────────────────────────────────────
def _get(k, d=None):
    return get_setting(k, d)


def _set(k, v):
    c = get_conn()
    try:
        c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, str(v)))
        c.commit()
    finally:
        c.close()


def _truthy(v):
    return str(v).lower() in ("1", "true", "yes", "on")


# ── autonomy systems: id → native setting it cascades into ───────────────────
SYSTEMS = [
    {"id": "create",      "label": "Autonomous creation",   "group": "Company", "key": "world_auto_enabled",         "on": "1",   "off": "0", "desc": "The studio makes art/music/video/3D on a cadence."},
    {"id": "govern",      "label": "Republic self-governs",  "group": "Company", "key": "world_auto_govern_min",      "on": "360", "off": "0", "numeric": True, "desc": "The assembly convenes itself to strategize + act."},
    {"id": "cognition",   "label": "Crew thinking (LLM)",    "group": "World",   "key": "world_llm_enabled",          "on": "1",   "off": "0", "desc": "Agents form thoughts, opinions, ideas."},
    {"id": "meetings",    "label": "Town meetings",          "group": "World",   "key": "world_meetings_enabled",     "on": "1",   "off": "0", "desc": "Periodic votes on company direction."},
    {"id": "incidents",   "label": "Incidents & events",     "group": "World",   "key": "world_incidents_enabled",    "on": "1",   "off": "0", "desc": "Random world events keep it lively."},
    # NOTE: "auto_publish" (world_ops_automation_mode) was removed 2026-07-21 — it was a
    # pure mirror of the God Console's mode control (/api/world/ops/config), writing the
    # exact same native key with the exact same "budget"/"review" values. No unique
    # functionality was lost; set the mode from the God Console instead.
    {"id": "sell",        "label": "Autonomous listing",     "group": "Company", "key": "world_sell_auto",            "on": "1",   "off": "0", "desc": "Queue paid Etsy/Printify listings for your review (costs $0.20/ea)."},
    {"id": "proposal_desk", "label": "Proposal desk (lanes + feeds)", "group": "Company", "key": "proposal_desk_enabled", "on": "1", "off": "0", "desc": "News/Reddit/Google feeds, the crew's suggestions and the Etsy demand signal become lane-tagged product proposals."},
    {"id": "proposal_gate", "label": "Proposal review gate", "group": "Company", "key": "proposal_gate_enabled",      "on": "1",   "off": "0", "desc": "LLM judge scores + weeds pending proposals (vs live Etsy demand) before any GPU generation."},
    {"id": "mail_gate",   "label": "Mail auto-reply gate",   "group": "Company", "key": "mail_gate_enabled",          "on": "1",   "off": "0", "desc": "The mail desk triages new email, drafts profile-driven replies, and (full_auto, hard-guardrailed) sends routine ones. Quotes/pricing always wait for you."},
    {"id": "swarm_cron",  "label": "Dev-swarm cron",         "group": "Dev",     "key": "swarm_cron_enabled",         "on": "1",   "off": "0", "desc": "Advance cron-enabled coding jobs on schedule."},
    {"id": "sec_monitor", "label": "Security monitor",       "group": "Infra",   "key": "security_monitor_enabled",   "on": "1",   "off": "0", "desc": "Watch the network for device changes."},
    {"id": "sec_scan",    "label": "Security auto-scan",     "group": "Infra",   "key": "security_autoscan_enabled",  "on": "1",   "off": "0", "desc": "Periodic Pi-hole / config scans."},
    {"id": "sec_analyze", "label": "AI threat-hunt",         "group": "Infra",   "key": "security_autoanalyze_enabled", "on": "1", "off": "0", "desc": "LLM threat analysis (uses the GPU)."},
    {"id": "sec_audit",   "label": "Nightly security audit", "group": "Infra",   "key": "security_audit_enabled",     "on": "1",   "off": "0", "desc": "Hardening snapshot + regression alerts to the God Console."},
    {"id": "guardian",    "label": "Network Guardian (auto-block)", "group": "Infra", "key": "netguard_auto_enabled",  "on": "1",   "off": "0", "desc": "Auto-block ad/tracker/ACR domains network-wide — never functional/local. Reversible."},
    {"id": "ai_watch",    "label": "AI Shield (agent watch)",   "group": "Infra",   "key": "ai_watch_enabled",           "on": "1",   "off": "0", "desc": "Watch AI agents for rogue behaviour (payout/code bursts, unknown actors) → God Console."},
]
_SYS = {s["id"]: s for s in SYSTEMS}


def _val_on(s, raw):
    """Interpret a raw setting value as on/off for this system's type."""
    if s.get("literal"):                 # non-boolean setting: on means raw == the "on" value
        return raw == s["on"]
    if s.get("numeric"):
        try:
            return int(raw) > 0
        except Exception:
            return False
    return _truthy(raw)


def _native_on(s):
    return _val_on(s, _get(s["key"], s["off"]))


def master_on():
    return _truthy(_get("company_master_on", "1"))


def desired(sid):
    """Per-system INTENT = the native setting itself — the SINGLE SOURCE OF TRUTH. Control
    no longer keeps a `company_desired_*` shadow that got re-cascaded over the native keys
    (on every import + every panel action), silently reverting edits you made directly in
    the ⚙️ Settings modal / 🛡️ Security Command. While the master is PAUSED all natives are
    forced off, so we surface the saved pre-pause value so the panel shows what will resume."""
    s = _SYS[sid]
    if not master_on():
        saved = _get(f"company_paused_{s['key']}", None)
        if saved is not None:
            return _val_on(s, saved)
    return _native_on(s)


def set_master(on):
    """Master = a non-destructive PAUSE. Pausing snapshots each system's current native
    value then forces it off; resuming restores from the snapshot. This never clobbers a
    value you set directly elsewhere (unlike the old cascade)."""
    was = master_on()
    if on and not was:                                    # RESUME → restore pre-pause states
        for s in SYSTEMS:
            saved = _get(f"company_paused_{s['key']}", None)
            if saved is not None:
                _set(s["key"], saved)
        _set("company_master_on", "1")
    elif (not on) and was:                                # PAUSE → snapshot, then force off
        for s in SYSTEMS:
            _set(f"company_paused_{s['key']}", _get(s["key"], s["off"]))
            _set(s["key"], s["off"])
        _set("company_master_on", "0")
    else:
        _set("company_master_on", "1" if on else "0")
    try:
        import world_ops as wo
        wo.note("🟢 The Company is awake — automation resumed." if on
                else "⏸️ The Company is dormant — all automation paused.",
                kind="info", from_agent="Mission Control")
    except Exception:
        pass
    return panel()


def set_system(sid, on):
    """Write the NATIVE key directly (the source of truth). While paused, remember the
    intent in the snapshot so it takes effect when the master resumes."""
    if sid not in _SYS:
        return None
    s = _SYS[sid]
    val = s["on"] if on else s["off"]
    if master_on():
        _set(s["key"], val)
    else:
        _set(f"company_paused_{s['key']}", val)
    return panel()


def init():
    """First-run ONLY: set the full-auto baseline once, natively. NEVER cascades on later
    imports — that silent re-apply was the bug that reverted native edits every restart."""
    if _get("company_control_init") == "1":
        return
    full_auto = {"create", "govern", "cognition", "meetings", "incidents"}
    for s in SYSTEMS:
        if s["id"] in full_auto:
            _set(s["key"], s["on"])
    _set("company_master_on", "1")
    _set("company_control_init", "1")
    logger.info("world_control: first-run full-auto baseline set (native keys)")


# ── capabilities ("mini-MCP"): trigger an action, receive a product ──────────
def _cap_create(kind):
    import world_auto
    if world_auto._state["running"]:
        return "a creation is already in progress"
    threading.Thread(target=world_auto.run_cycle, args=(kind, True), daemon=True).start()
    return f"creating {kind}"


def _cap_convene():
    import world_strategy
    threading.Thread(target=world_strategy.run_cycle, daemon=True).start()
    return "the assembly is convening"


def _cap_research(args):
    import world_ops as wo
    topic = (args or {}).get("topic") or "Scout a survival edge on the web"
    wo.pray("library_research", f"Research: {topic}", detail="Commissioned from Mission Control.",
            cost_cents=0, agent_name="Mission Control")
    return f"research commissioned: {topic}"


def _cap_sell(channel):
    import world_sell
    threading.Thread(target=world_sell.list_design, args=(channel,), daemon=True).start()
    return f"drafting a {channel} listing — it'll appear as a prayer to review"


def _cap_http(method, path):
    def go():
        try:
            httpx.request(method, f"{_LOCAL}{path}", timeout=20)
        except Exception:
            logger.exception("capability http %s %s", method, path)
    threading.Thread(target=go, daemon=True).start()
    return "triggered"


CAPABILITIES = [
    {"id": "make_art",    "label": "🖼️ Create art",           "group": "Create", "fn": lambda a: _cap_create("image")},
    {"id": "make_music",  "label": "🎵 Create music",          "group": "Create", "fn": lambda a: _cap_create("music")},
    {"id": "make_video",  "label": "🎬 Create video",          "group": "Create", "fn": lambda a: _cap_create("video")},
    {"id": "make_3d",     "label": "🧊 Sculpt a 3D model",     "group": "Create", "fn": lambda a: _cap_create("3d")},
    {"id": "sell_etsy",   "label": "🛍️ List a design on Etsy", "group": "Sell",   "fn": lambda a: _cap_sell("etsy")},
    {"id": "sell_printify", "label": "👕 List on Printify",     "group": "Sell",   "fn": lambda a: _cap_sell("printify")},
    {"id": "convene",     "label": "🏛️ Convene the assembly",  "group": "Govern", "fn": lambda a: _cap_convene()},
    {"id": "research",    "label": "📖 Commission research",   "group": "Learn",  "fn": lambda a: _cap_research(a)},
    {"id": "scan_trends", "label": "📈 Scan market trends",    "group": "Research", "fn": lambda a: _cap_http("POST", "/api/trends/scan")},
    {"id": "sec_scan_now", "label": "🛡️ Run a security scan",  "group": "Infra",  "fn": lambda a: _cap_http("POST", "/api/security/scan")},
]
_CAP = {c["id"]: c for c in CAPABILITIES}


def invoke(cap_id, args=None):
    c = _CAP.get(cap_id)
    if not c:
        return {"ok": False, "error": f"unknown capability '{cap_id}'"}
    try:
        return {"ok": True, "result": c["fn"](args or {})}
    except Exception as e:
        logger.exception("capability %s failed", cap_id)
        return {"ok": False, "error": str(e)}


def set_sell_config(price_cents=None, product_type=None):
    if price_cents is not None:
        _set("world_sell_price_cents", max(100, int(price_cents)))
    if product_type:
        _set("world_sell_product_type", str(product_type)[:40])
    return panel()


def panel():
    m = master_on()
    try:
        price = int(_get("world_sell_price_cents", "2499"))
    except Exception:
        price = 2499
    return {
        "master": m,
        "run_mode": _get("world_run_mode", "normal"),
        "systems": [{
            "id": s["id"], "label": s["label"], "group": s["group"], "desc": s.get("desc", ""),
            "desired": desired(s["id"]), "effective": (m and _native_on(s)),
        } for s in SYSTEMS],
        "capabilities": [{"id": c["id"], "label": c["label"], "group": c["group"]} for c in CAPABILITIES],
        "sell": {
            "price_cents": price,
            "product_type": _get("world_sell_product_type", "Poster"),
            "etsy_ready": bool(_get("etsy_access_token") and _get("etsy_shop_id")),
            "printify_ready": bool(_get("printify_key") and _get("printify_shop_id")),
        },
    }


init()

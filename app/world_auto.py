"""
The Company — autonomous creation loop (Chunk 4).

The heart of "the game runs the store": creative agents periodically make media
on the local GPU (free) and file a PRAYER to publish it. Nothing goes live
without the God Console gate (world_ops.pray) — so you come back to real work
that's queued for your blessing (Review mode) or already published if free and
you flipped on Auto ≤budget.

OFF by default. Interval- and active-hour-gated so it never surprise-hammers the
GPU. One creation per cycle; one model in VRAM at a time is enforced by the
orchestrator (services.run_generation acquires the GPU).

Decoupled from the world sim (another agent's world_ticker/world_sim). Reads
world_agents read-only to attribute creations; writes only via world_ops.
"""
import base64, json, logging, os, random, threading, time
import httpx
from deps import get_conn, get_setting, _resolve_model
import services
import world_ops as wo

_LOCAL = "http://127.0.0.1:8787"   # internal calls ride the localhost auth-bypass

logger = logging.getLogger("store")

# creators + publish adapters live in sibling modules; re-export their full
# public surface so world_auto keeps its historical API (and world_publish's
# register_executor side effects still run at import time).
from world_creators import (  # noqa: E402
    IMAGE_IDEAS, MUSIC_IDEAS, VIDEO_IDEAS, THREED_IDEAS, _WORLD_PRODUCTS,
    _fresh_prompt, _pick_agent, _pick_product_type,
    _create_image, rework_image, _acestep, _acestep_ok, _write_lyrics,
    _create_audio, _create_video, _create_3d,
)
from world_publish import (  # noqa: E402
    _publish_cults3d, _wp_mcp, _web_bytes, _publish_wordpress,
)

DEFAULTS = {
    "world_auto_enabled":      "0",     # master switch — off until you turn it on
    "world_auto_interval_min": "120",   # make something at most this often
    "world_auto_active_start": "8",     # only create between these hours (24h)
    "world_auto_active_end":   "22",
    "world_auto_kinds":        "image", # comma list; v1 ships image (GPU-cheap, core)
    "world_auto_govern_min":   "360",   # convene the Republic this often (0 = never)
}

# creative departments → who gets the credit
CREATIVE_DEPT = {"image": "image", "video": "video", "audio": "audio", "models3d": "models3d"}

_state = {"last_run": 0.0, "running": False, "thread": None, "last_result": None,
          "last_govern": 0.0, "last_sell": 0.0}

# don't create MORE media while this many publish prayers already wait — without
# backpressure the studio outruns the gallery and the queue grows forever.
BACKLOG_MAX = 6

# cadence timers survive restarts via these settings keys; otherwise every
# service restart reset last_run to 0 and fired an immediate creation burst.
_PERSIST = {"last_run": "world_auto_t_last_run", "last_govern": "world_auto_t_last_govern",
            "last_sell": "world_auto_t_last_sell"}


def _load_timers():
    for f, key in _PERSIST.items():
        try:
            _state[f] = float(get_setting(key, "0") or 0)
        except Exception:
            _state[f] = 0.0


def _stamp(field):
    _state[field] = time.time()
    try:
        conn = get_conn()
        try:
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                         (_PERSIST[field], str(_state[field])))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


# ── config ───────────────────────────────────────────────────────────────────
def cfg(k):
    return get_setting(k, DEFAULTS.get(k))


def enabled():
    return str(cfg("world_auto_enabled")).lower() in ("1", "true", "yes", "on")


def _interval_sec():
    try:
        return max(5, int(cfg("world_auto_interval_min"))) * 60
    except Exception:
        return 7200


def _active_now():
    try:
        h = int(time.strftime("%H"))
        s, e = int(cfg("world_auto_active_start")), int(cfg("world_auto_active_end"))
        return s <= h < e if s < e else True
    except Exception:
        return True


SUPPORTED_KINDS = ("image", "music", "video", "3d")


def kinds():
    ks = [k.strip() for k in str(cfg("world_auto_kinds") or "image").split(",")
          if k.strip() in SUPPORTED_KINDS]
    return ks or ["image"]


def pick_kind():
    return random.choice(kinds())


def save_config(updates, conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        for k, v in updates.items():
            if k in DEFAULTS:
                conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, str(v)))
        conn.commit()
    finally:
        if own:
            conn.close()


def _govern_sec():
    try:
        return max(0, int(cfg("world_auto_govern_min"))) * 60
    except Exception:
        return 21600


def status():
    gsec = _govern_sec()
    return {
        "enabled": enabled(),
        "interval_min": int(cfg("world_auto_interval_min")),
        "active_start": int(cfg("world_auto_active_start")),
        "active_end": int(cfg("world_auto_active_end")),
        "kinds": kinds(),
        "govern_min": int(cfg("world_auto_govern_min")),
        "running": _state["running"],
        "last_run": _state["last_run"],
        "next_due_sec": max(0, int(_state["last_run"] + _interval_sec() - time.time())) if _state["last_run"] else 0,
        "next_govern_sec": (max(0, int(_state["last_govern"] + gsec - time.time())) if (gsec and _state["last_govern"]) else 0),
        "last_result": _state["last_result"],
    }


# ── helpers ──────────────────────────────────────────────────────────────────
def _publish_backlog():
    """How many publish prayers already await — the creation backpressure gate."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM world_prayers "
                           "WHERE status='pending' AND kind LIKE 'publish_%'").fetchone()
        return int(row["n"] or 0)
    except Exception:
        return 0
    finally:
        conn.close()


# ── the creation cycle ───────────────────────────────────────────────────────
def run_cycle(kind="image", manual=False):
    """Run one creation. Returns a result dict. Serialized by _state['running']."""
    if _state["running"]:
        return {"ok": False, "skipped": "a creation is already in progress"}
    _state["running"] = True
    try:
        if kind == "image":
            res = _create_image()
        elif kind in ("music", "audio"):
            res = _create_audio()
        elif kind == "video":
            res = _create_video()
        elif kind == "3d":
            res = _create_3d()
        else:
            res = {"ok": False, "error": f"kind '{kind}' not supported"}
        _state["last_result"] = res
        return res
    except Exception as e:
        logger.exception("world_auto.run_cycle failed")
        _state["last_result"] = {"ok": False, "error": str(e)}
        return _state["last_result"]
    finally:
        _stamp("last_run")
        _state["running"] = False


# ── background loop ──────────────────────────────────────────────────────────
def _govern_tick():
    """Let the Republic self-convene: assess → vote → act. Lazy import avoids the
    world_strategy ↔ world_auto import cycle. Only ever yields free actions or
    gated prayers, so it's safe to run unattended."""
    try:
        import world_strategy
        world_strategy.run_cycle()
        wo.note("🏛️ The assembly convened on its own and set the nation's course.",
                kind="info", from_agent="The Republic")
    except Exception:
        logger.exception("world_auto govern tick failed")


def _loop():
    time.sleep(30)   # let the app settle
    while True:
        try:
            now = time.time()
            # drain the prayer queue (no-op unless budget mode; a couple per
            # minute). Deliberately NOT gated on enabled()/active hours —
            # publishing is a cheap upload decoupled from GPU creation, and the
            # auto-publish toggle is the mode itself.
            try:
                wo.sweep_pending(limit=2)
            except Exception:
                logger.exception("world_auto prayer sweep failed")
            # harvest god's latest verdicts into the taste model (~5 min cadence)
            if now - _state.get("last_taste_sync", 0) >= 300:
                _state["last_taste_sync"] = now
                try:
                    import world_taste
                    conn = get_conn()
                    try:
                        world_taste.sync(conn)
                    finally:
                        conn.close()
                except Exception:
                    logger.exception("taste sync failed")
            # pull REAL sales into the treasury (~15 min) — the missing half of the loop.
            # Not gated on enabled(): money should always be tracked, even paused.
            if now - _state.get("last_revenue_sync", 0) >= 900:
                _state["last_revenue_sync"] = now
                try:
                    import world_sell
                    r = world_sell.sync_revenue()
                    if r.get("added"):
                        logger.info("revenue sync: +%d Etsy sale(s), $%.2f to treasury",
                                    r["added"], r["revenue_cents"] / 100)
                except Exception:
                    logger.exception("revenue sync failed")
            # field-manual re-validation (world_bible ops layer): flags any
            # operational teaching whose referenced file/symbol moved, so a
            # stale note can never mislead an agent. OWN toggle
            # (world_bible_ops_sweep, default OFF); pure disk stats, no GPU.
            try:
                import world_bible
                world_bible.maybe_sweep(now)
            except Exception:
                logger.exception("world_bible ops sweep failed")
            # full-catalog capability auto-loop (world_caps): its OWN toggle
            # (world_caps_auto, default OFF) + the company master, both checked
            # inside; independent of the creation loop's enabled() switch but
            # still confined to active hours. At most one agent-safe capability
            # per its interval — never publishing/spending kinds.
            if _active_now():
                try:
                    import world_caps
                    world_caps.maybe_agent_cycle(now)
                except Exception:
                    logger.exception("world_caps auto cycle failed")
                # "Jesus" — the owner's toggle-gated stand-in operator
                # (world_jesus_enabled, default OFF). One delegated action per
                # its interval: routine prayer verdicts / already-enabled caps.
                # The hard floor (real money, code, illegal content) stays
                # human — see world_jesus.py.
                try:
                    import world_jesus
                    world_jesus.maybe_tick(now)
                except Exception:
                    logger.exception("world_jesus tick failed")
                # "Satan" — the adversarial red-team lieutenant, Jesus's mirror
                # (world_satan_enabled, default OFF). One adversarial action per
                # its interval: routine rejects / risk flags / already-enabled
                # caps. NO approve path, NO money path; the illegal-content
                # floors are always-on at the endpoints — see world_satan.py.
                try:
                    import world_satan
                    world_satan.maybe_tick(now)
                except Exception:
                    logger.exception("world_satan tick failed")
            if enabled() and _active_now():
                if (not _state["running"] and (now - _state["last_run"]) >= _interval_sec()
                        and _publish_backlog() < BACKLOG_MAX):
                    run_cycle(random.choice(kinds()))
                gsec = _govern_sec()
                if gsec and (now - _state["last_govern"]) >= gsec:
                    _stamp("last_govern")
                    _govern_tick()
                # Private-Studio moonlighting: layered nsfw_enabled + nsfw_world
                # toggles on top of the world automation master (see app/nsfw.py).
                # No-op unless both are on; feed lines stay generic PG-13 always.
                try:
                    import nsfw as _nsfw
                    _nsfw.maybe_world_cycle()
                except Exception:
                    logger.exception("nsfw world cycle tick failed")
                # autonomous listing — cascaded on only when master + sell toggle are on
                if str(get_setting("world_sell_auto", "0")).lower() in ("1", "true", "on"):
                    ssec = max(1, int(get_setting("world_sell_interval_min", "180") or 180)) * 60
                    if (now - _state["last_sell"]) >= ssec:
                        _stamp("last_sell")
                        try:
                            import world_sell
                            threading.Thread(target=world_sell.list_design,
                                             args=(get_setting("world_sell_channel", "etsy"),), daemon=True).start()
                        except Exception:
                            logger.exception("world_auto sell tick failed")
        except Exception:
            logger.exception("world_auto loop tick failed")
        time.sleep(60)


def start():
    if _state["thread"]:
        return
    _load_timers()
    t = threading.Thread(target=_loop, daemon=True, name="world-auto")
    _state["thread"] = t
    t.start()
    logger.info("world_auto started (enabled=%s)", enabled())

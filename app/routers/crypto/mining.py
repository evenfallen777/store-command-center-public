"""Monero CPU mining (official xmrig, installed but OFF by default).

Gating mirrors Pearl (app/pearl.py + app/routers/pearl.py) exactly: a master
`xmr_mining_enabled` toggle (default OFF) hard-gates the start action, and a
separate `xmr_agent_access` toggle (default OFF) gates non-human callers —
even with mining enabled, only an authenticated human session may start/stop
xmrig until agent access is explicitly turned on."""
import io
import json as _json
import os
import re as _re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Body, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from deps import *          # get_conn, get_setting, orch, _call_lmstudio, _dec, logger
from services import *      # (kept consistent with sibling routers)

from ._base import router, _set_setting


# ── ⛏️ Monero CPU mining (official xmrig, installed but OFF by default) ────────
XMRIG_DIR    = Path.home() / "crypto"
XMRIG_BIN    = XMRIG_DIR / "xmrig-dist" / "xmrig"
XMRIG_CFG    = XMRIG_DIR / "xmrig-config.json"
XMRIG_LOG    = XMRIG_DIR / "xmrig.log"
DEFAULT_POOL = "gulf.moneroocean.stream:10128"   # MoneroOcean auto-switches algos

# ── settings contract (mirrors pearl.py's MINING_TOGGLE_KEY / AGENT_ACCESS_KEY) ─
MINING_TOGGLE_KEY = "xmr_mining_enabled"   # master toggle — nothing starts while this is off
AGENT_ACCESS_KEY  = "xmr_agent_access"     # agent/automation gate — OFF = only the human can start/stop


def mining_enabled() -> bool:
    return str(get_setting(MINING_TOGGLE_KEY, "0") or "0") in ("1", "true", "on")


def agent_access_enabled() -> bool:
    """Whether agents / automation (non-human callers) may drive xmrig. OFF by
    default: even with mining enabled, only the human UI can start/stop until
    this is turned on. Mirrors pearl.agent_access_enabled()."""
    return str(get_setting(AGENT_ACCESS_KEY, "0") or "0") in ("1", "true", "on")


def _is_human(request: Request) -> bool:
    """A caller is the HUMAN only when they present an authenticated browser
    session. MCP tools / automation / cron reach the API through the localhost
    auth-bypass with no session — those are agent callers and need
    xmr_agent_access before they can touch the miner. Mirrors
    routers/pearl.py's _is_human()."""
    try:
        return bool(request.session.get("authenticated"))
    except Exception:
        return False


def _xmr_payout() -> str:
    """The Monero address xmrig mines to: explicit xmr_wallet setting, else the
    real XMR receive address derived from the wallet seed."""
    w = (get_setting("xmr_wallet", "") or "").strip()
    if w:
        return w
    try:
        import wallet_lib
        m = get_setting("wallet_mnemonic", "") or ""
        if m:
            return wallet_lib.derive_xmr_primary(m)
    except Exception as e:
        logger.warning("xmr payout derive failed: %s", e)
    return ""


def _xmrig_running() -> bool:
    try:
        r = subprocess.run(["pgrep", "-f", "xmrig-dist/xmrig"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


@router.get("/api/crypto/mining")
def crypto_mining():
    return {
        "installed": XMRIG_BIN.exists(),
        "running":   _xmrig_running(),
        "wallet":    _xmr_payout(),
        "pool":      get_setting("xmr_pool", DEFAULT_POOL),
        "threads":   int(get_setting("xmr_threads", "0") or 0),  # 0 = auto
        "mining_enabled": mining_enabled(),
        "agent_access":   agent_access_enabled(),
        "note":      "CPU mining on this shared box earns pennies/day and adds heat/load. "
                     "Off by default — this is real mining to your real XMR address.",
    }


class MiningCfg(BaseModel):
    pool: Optional[str] = None
    wallet: Optional[str] = None
    threads: Optional[int] = None
    mining_enabled: Optional[str] = None
    agent_access: Optional[str] = None


@router.post("/api/crypto/mining/config")
def crypto_mining_config(cfg: MiningCfg):
    if cfg.pool is not None:
        _set_setting("xmr_pool", cfg.pool.strip() or DEFAULT_POOL)
    if cfg.wallet is not None:
        _set_setting("xmr_wallet", cfg.wallet.strip())
    if cfg.threads is not None:
        _set_setting("xmr_threads", str(max(0, min(int(cfg.threads), 8))))
    if cfg.mining_enabled is not None:
        _set_setting(MINING_TOGGLE_KEY, "1" if str(cfg.mining_enabled) in ("1", "true", "on", "True") else "0")
    if cfg.agent_access is not None:
        _set_setting(AGENT_ACCESS_KEY, "1" if str(cfg.agent_access) in ("1", "true", "on", "True") else "0")
    return crypto_mining()


def mining_action(action: str, by_agent: bool = False) -> dict:
    """start|stop xmrig. HARD-GATED on the xmr_mining_enabled toggle for start;
    never called automatically by anything — only the UI button / an explicit
    API call reaches this.

    ``by_agent`` marks a non-human caller (MCP tool / automation / localhost
    bypass). Such callers ALSO need xmr_agent_access on — otherwise only the
    human, from an authenticated session, may start/stop xmrig. Mirrors
    pearl.miner_action() exactly."""
    if action not in ("start", "stop"):
        raise ValueError("action must be start or stop")
    if by_agent and not agent_access_enabled():
        raise PermissionError("Agent access to XMR mining is OFF — turn on the "
                              "'Allow agents to control mining' toggle for automation "
                              "to start/stop xmrig. (A human can still use the buttons.)")
    if action == "stop":
        subprocess.run(["pkill", "-f", "xmrig-dist/xmrig"], capture_output=True)
        return {"ok": True, "running": False}
    # start
    if not mining_enabled():
        raise PermissionError("XMR mining is toggled OFF — enable the 'Allow XMR "
                              "mining' toggle first. Nothing auto-starts.")
    if not XMRIG_BIN.exists():
        raise ValueError("xmrig binary not installed")
    payout = _xmr_payout()
    if not payout:
        raise ValueError("no XMR address — set one in Wallets or the xmr_wallet setting")
    if _xmrig_running():
        return {"ok": True, "running": True, "note": "already running"}
    threads = int(get_setting("xmr_threads", "0") or 0)
    cpu = {"enabled": True} if threads == 0 else {"enabled": True, "max-threads-hint": min(threads * 25, 100)}
    conf = {
        "autosave": False, "cpu": cpu,
        "pools": [{"url": get_setting("xmr_pool", DEFAULT_POOL), "user": payout,
                   "pass": get_setting("xmr_pool_pass", "x"),   # pool worker label, not a secret
                   "keepalive": True, "tls": False}],
    }
    XMRIG_CFG.write_text(_json.dumps(conf, indent=2))
    with open(XMRIG_LOG, "ab") as log:
        subprocess.Popen([str(XMRIG_BIN), "--config", str(XMRIG_CFG)],
                         stdout=log, stderr=log, cwd=str(XMRIG_DIR),
                         start_new_session=True)
    logger.info("[xmr] miner %s", action)
    return {"ok": True, "running": True, "wallet": payout}


@router.post("/api/crypto/mining/{action}")
def crypto_mining_action(action: str, request: Request):
    """start|stop xmrig. start is REFUSED while xmr_mining_enabled is off (403).
    Non-human callers (MCP/automation) are ALSO refused unless xmr_agent_access
    is on (403) — mirrors routers/pearl.py's pearl_miner() gate exactly."""
    try:
        return mining_action(action, by_agent=not _is_human(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))

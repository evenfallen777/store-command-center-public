"""Shared foundation for the oracle package: the single APIRouter, the SQLite
schema + default-analyst seed (both run once at import), the tournament constants,
and the low-level helpers (meta key/value store, tracked-asset list, price lookup,
searx research) used across the forecast / scoring / agents submodules."""
import json as _json
from typing import Optional

import requests
from fastapi import APIRouter

from deps import *          # get_conn, get_setting, orch, logger, _call_lmstudio
from services import *      # (kept consistent with sibling routers)

router = APIRouter()

SEARX_URL = "http://127.0.0.1:8899"

# The competitors. name → LM Studio model id (seeded on first use; toggle in the UI).
# Chosen for SPEED + reliable JSON on this single-GPU box. Gemma and Ministral were
# dropped — they took up to ~17 min/call and never returned clean JSON. Coder models
# are the most JSON-compliant; GLM-flash adds non-Qwen diversity.
DEFAULT_ANALYSTS = [
    ("GLM-4.7",        "zai-org/glm-4.7-flash"),
    ("GLM-4.6v",       "zai-org/glm-4.6v-flash"),
    ("Qwen3.5-9B",     "qwen/qwen3.5-9b"),
    ("Qwen-Coder-32B", "qwen2.5-coder-32b-instruct"),
    ("Qwen3-Coder-30B", "qwen3-coder-30b-a3b-instruct"),
]

# Auto-resolvable assets. Crypto → CoinGecko id; stocks come from stocks_watchlist.
# CRYPTO_IDS is the built-in DEFAULT map. The owner-editable set lives in the
# `oracle_crypto_assets` setting (JSON symbol→CoinGecko-id) — see crypto_ids()
# below. BTC/ETH/SOL/XRP/DOGE are the tracked defaults; ADA/LTC are extra
# resolvable ids kept around for legacy rows but not in the default tracked set.
CRYPTO_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
              "DOGE": "dogecoin", "ADA": "cardano", "LTC": "litecoin"}
DEFAULT_CRYPTO_ASSETS = ["BTC", "ETH", "SOL", "XRP", "DOGE"]   # today's tracked defaults

# ── the short-horizon LADDER ──────────────────────────────────────────────────
# Each forecast produces one prediction PER RUNG (day-trade-friendly horizons), so
# every rung resolves and scores independently. The long tier (30d) is optional.
LADDER_DEFAULT = [1, 3, 5, 7, 14]
LONG_TIER_DAYS = 30

# Oracle settings (plain rows in the `settings` table; surfaced in the Oracle tab
# AND the God panel — every gate ships with a toggle, per the house rule).
ORACLE_SETTINGS_DEFAULTS = {
    "oracle_auto":           "on",              # master auto loop (resolve + rounds)
    "oracle_auto_rounds":    "1",               # one autonomous tournament round per day
    "oracle_ladder":         "1,3,5,7,14",      # per-rung enable: the horizons forecast
    "oracle_long_tier":      "0",               # add the optional 30d long-tier rung
    "oracle_company_hookup": "1",               # Company/world may cite the consensus (advisory only)
    "oracle_crypto_assets":  "",                # "" = built-in defaults (BTC/ETH/SOL/XRP/DOGE);
                                                 # else a JSON {SYMBOL: coingecko_id} FULL override map
}


def oracle_setting(key: str) -> str:
    return str(get_setting(key, ORACLE_SETTINGS_DEFAULTS.get(key, "")) or ORACLE_SETTINGS_DEFAULTS.get(key, ""))


def ladder_days() -> list:
    """The enabled rung horizons (days), sorted + deduped, each clamped 1–90."""
    days = []
    for tok in oracle_setting("oracle_ladder").split(","):
        tok = tok.strip()
        if tok.isdigit() and 1 <= int(tok) <= 90:
            days.append(int(tok))
    if oracle_setting("oracle_long_tier") in ("1", "true", "on"):
        days.append(LONG_TIER_DAYS)
    days = sorted(set(days)) or list(LADDER_DEFAULT)
    return days


# ── schema ────────────────────────────────────────────────────────────────────
def _ensure_schema():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS oracle_agents (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, model TEXT,
        active INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now')));
    CREATE TABLE IF NOT EXISTS oracle_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, agent_name TEXT,
        market TEXT, asset TEXT, current_value REAL, direction TEXT,
        target_value REAL, horizon_days INTEGER, resolve_at TEXT, confidence REAL,
        thesis TEXT, sources TEXT, status TEXT DEFAULT 'open',
        actual_value REAL, rel_error REAL, correct INTEGER, score REAL,
        created_at TEXT DEFAULT (datetime('now')), resolved_at TEXT);
    CREATE TABLE IF NOT EXISTS oracle_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, text TEXT,
        created_at TEXT DEFAULT (datetime('now')));
    CREATE TABLE IF NOT EXISTS oracle_meta (k TEXT PRIMARY KEY, v TEXT);
    """)
    # additive migration: ladder rows carry a batch_id grouping the rungs of one
    # forecast call. Legacy rows keep batch_id NULL — that's also the scoring
    # discriminator (NULL → old curve, set → the ladder curve).
    try:
        conn.execute("ALTER TABLE oracle_predictions ADD COLUMN batch_id TEXT")
    except Exception:
        pass                                   # column already exists
    # additive migration: each rung may carry a DUALITY band — {"high","low",
    # "expected"} json from world_duality.forecast_band (✝️ best case / 😈 worst
    # case / calibrated middle). NULL on legacy rows and while both lieutenants
    # are off — purely informational, scoring is untouched.
    try:
        conn.execute("ALTER TABLE oracle_predictions ADD COLUMN band TEXT")
    except Exception:
        pass                                   # column already exists
    conn.commit()
    conn.close()

_ensure_schema()


def _seed_agents():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM oracle_agents").fetchone()["n"]
    if n == 0:
        for name, model in DEFAULT_ANALYSTS:
            conn.execute("INSERT INTO oracle_agents (name,model,active) VALUES (?,?,1)", (name, model))
        conn.commit()
    conn.close()

_seed_agents()


# ── small helpers ─────────────────────────────────────────────────────────────
def _meta_get(k, d=""):
    conn = get_conn(); r = conn.execute("SELECT v FROM oracle_meta WHERE k=?", (k,)).fetchone(); conn.close()
    return r["v"] if r else d


def _meta_set(k, v):
    conn = get_conn(); conn.execute("INSERT OR REPLACE INTO oracle_meta (k,v) VALUES (?,?)", (k, str(v)))
    conn.commit(); conn.close()


def crypto_ids() -> dict:
    """Effective symbol→CoinGecko-id map the Oracle tracks. Owner-editable via the
    `oracle_crypto_assets` setting (JSON), which — once saved — holds the FULL
    map (defaults merged with the owner's adds/removes at save time, see
    routers/oracle/auto.py). Empty/unset/unparseable → today's built-in default
    tracked set (BTC/ETH/SOL/XRP/DOGE), i.e. zero behavior change out of the box."""
    raw = get_setting("oracle_crypto_assets", "") or ""
    if raw:
        try:
            d = _json.loads(raw)
            if isinstance(d, dict) and d:
                return {str(k).strip().upper(): str(v).strip() for k, v in d.items()
                        if str(k).strip() and str(v).strip()}
        except Exception:
            pass
    return {sym: CRYPTO_IDS[sym] for sym in DEFAULT_CRYPTO_ASSETS}


def _assets() -> list:
    """Tracked assets: the configured crypto set + any non-crypto stock tickers on
    the watchlist. Crypto set is owner-editable — see crypto_ids()."""
    out = list(crypto_ids().keys())
    wl = (get_setting("stocks_watchlist", "") or "").upper()
    for s in [x.strip() for x in wl.split(",") if x.strip()]:
        if "-" in s or s in out:            # skip BTC-USD style + crypto dupes
            continue
        out.append(s)
    return out


def _price(asset: str) -> Optional[float]:
    """Current USD price. Crypto via CoinGecko, stocks via yfinance. None on failure."""
    try:
        cids = crypto_ids()
        if asset in cids:
            r = requests.get("https://api.coingecko.com/api/v3/simple/price",
                             params={"ids": cids[asset], "vs_currencies": "usd"}, timeout=15)
            return float(r.json()[cids[asset]]["usd"])
        import yfinance as yf
        fi = yf.Ticker(asset).fast_info
        p = None
        for k in ("last_price", "lastPrice"):
            p = getattr(fi, k, None) if hasattr(fi, k) else (fi.get(k) if hasattr(fi, "get") else None)
            if p:
                break
        return float(p) if p else None
    except Exception as e:
        logger.warning("oracle price(%s) failed: %s", asset, e)
        return None


def _searx(query: str, n: int = 5) -> list:
    try:
        r = requests.get(f"{SEARX_URL}/search",
                         params={"q": query, "format": "json", "language": "en"}, timeout=20)
        r.raise_for_status()
        return [{"title": (x.get("title") or "")[:160], "url": x.get("url") or "",
                 "snippet": (x.get("content") or "")[:280]}
                for x in (r.json().get("results") or [])[:n]]
    except Exception as e:
        logger.warning("oracle searx failed: %s", e)
        return []

"""Income Phase 2 — READ-ONLY auto-importers: PayPal / Printify / on-chain.

Pulls money-IN visibility from external sources and records it as income rows in
the existing `paychecks` table (Income Phase 1's superset — see
routers/money/ledger.py). This module is STRICTLY read-only on the money side:
it fetches transaction/order/blockchain data and INSERTs income rows. There is
no spending, no withdrawal, no transfer, and no code path that could move money.

Sources (one fetcher per source, each returning normalized records):
  • paypal   — completed incoming transactions via the PayPal Reporting API
               (/v1/reporting/transactions). Credentials: settings
               paypal_client_id / paypal_client_secret (encrypted at rest via
               crypto.SECRET_KEYS; falls back to the world payout rail's
               world_paypal_* keys so an already-connected account just works).
  • printify — recent orders via the Printify API; records the merchant NET
               (retail total − production cost) per order. Credential:
               printify_api_key (falls back to the shop's existing printify_key).
  • onchain  — incoming transactions to a configured PUBLIC wallet address via
               public explorer APIs (mempool.space for BTC, Blockscout for ETH —
               no API key; a public address is not a secret). Settings:
               income_wallet_address + income_wallet_chain (btc|eth).

Dedupe: every record carries an external_txn_id; rows INSERT OR IGNORE against
the existing UNIQUE index idx_income_ext (external_source, external_txn_id), so
re-running an import can never double-count.

Background daemon: modeled on world_auto.py / routers/money/auto.py — a single
daemon thread, gated on the `income_autoimport_enabled` setting which DEFAULTS
OFF. The manual "Import now" endpoint (routers/money/income_sources.py) always
works regardless of the toggle. Anti-double-run: an in-process per-source lock
plus the `running` column in income_import_state (db_schema.py), which start()
resets on boot so a crash mid-import can never wedge a source. Per-source
last_run_at persists in the DB, so a restart never triggers an import burst.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from deps import get_conn, get_setting, logger
from db_schema import create_ledger_tables as _create_ledger_tables

SOURCES = ("paypal", "printify", "onchain")

DEFAULTS = {
    "income_autoimport_enabled":      "0",    # master switch — OFF until you turn it on
    "income_autoimport_interval_min": "360",  # per-source cadence when enabled
    "income_paypal_lookback_days":    "30",   # reporting window per run (PayPal caps a page at 31)
    "income_wallet_chain":            "btc",  # btc | eth
}

_UA = {"User-Agent": "StoreCommandCenter/1.0"}


# ── config ───────────────────────────────────────────────────────────────────
def cfg(k):
    return get_setting(k, DEFAULTS.get(k))


def enabled() -> bool:
    return str(cfg("income_autoimport_enabled")).lower() in ("1", "true", "yes", "on")


def _interval_sec() -> int:
    try:
        return max(15, int(cfg("income_autoimport_interval_min"))) * 60
    except Exception:
        return 21600


def _paypal_cfg() -> dict:
    """Importer creds first; fall back to the world payout rail's keys so an
    already-connected PayPal account works without re-entering anything."""
    cid = get_setting("paypal_client_id") or get_setting("world_paypal_client_id")
    sec = get_setting("paypal_client_secret") or get_setting("world_paypal_secret")
    mode = get_setting("paypal_mode") or get_setting("world_paypal_mode") or "live"
    return {"client_id": cid, "secret": sec, "mode": mode}


def _printify_key() -> str:
    return get_setting("printify_api_key") or get_setting("printify_key") or ""


def _wallet() -> tuple[str, str]:
    addr = (get_setting("income_wallet_address") or "").strip()
    chain = (str(cfg("income_wallet_chain")) or "btc").strip().lower()
    return addr, chain


def configured(source: str) -> bool:
    if source == "paypal":
        c = _paypal_cfg()
        return bool(c["client_id"] and c["secret"])
    if source == "printify":
        return bool(_printify_key())
    if source == "onchain":
        return bool(_wallet()[0])
    return False


# ── state (income_import_state, created by create_ledger_tables) ─────────────
def _ensure_schema(conn):
    _create_ledger_tables(conn)   # idempotent; commits internally
    for s in SOURCES:
        conn.execute("INSERT OR IGNORE INTO income_import_state (source) VALUES (?)", (s,))
    conn.commit()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _cents(v) -> int:
    try:
        return int(round(float(v) * 100))
    except Exception:
        return 0


# ── PayPal — completed incoming transactions (Reporting API, read-only) ──────
def _fetch_paypal() -> tuple[list, str]:
    import paypal_client
    c = _paypal_cfg()
    if not (c["client_id"] and c["secret"]):
        return [], "PayPal not connected — save paypal_client_id / paypal_client_secret"
    tok, err = paypal_client.get_token(c)
    if not tok:
        return [], err or "PayPal auth failed"
    base = ("https://api-m.sandbox.paypal.com" if str(c["mode"]).lower() == "sandbox"
            else "https://api-m.paypal.com")
    try:
        days = max(1, min(31, int(cfg("income_paypal_lookback_days"))))
    except Exception:
        days = 30
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    fmt = "%Y-%m-%dT%H:%M:%S-0000"
    out, page = [], 1
    try:
        with httpx.Client(timeout=40) as client:
            while page <= 10:   # hard cap; 100 txns/page is plenty for a personal ledger
                r = client.get(
                    f"{base}/v1/reporting/transactions",
                    params={"start_date": start.strftime(fmt), "end_date": end.strftime(fmt),
                            "fields": "transaction_info,payer_info",
                            "page_size": 100, "page": page},
                    headers={"Authorization": f"Bearer {tok}", **_UA})
                if r.status_code != 200:
                    return out, f"PayPal transactions failed ({r.status_code}): {r.text[:180]}"
                data = r.json()
                for td in data.get("transaction_details") or []:
                    ti = td.get("transaction_info") or {}
                    if ti.get("transaction_status") != "S":     # completed only
                        continue
                    amt = ti.get("transaction_amount") or {}
                    try:
                        gross = float(amt.get("value"))
                    except (TypeError, ValueError):
                        continue
                    if gross <= 0:                              # money IN only — never outflows
                        continue
                    if (amt.get("currency_code") or "USD") != "USD":
                        continue    # amount_cents is USD-at-receipt; skip non-USD (no guessing FX)
                    try:
                        fee = float((ti.get("fee_amount") or {}).get("value") or 0)
                    except (TypeError, ValueError):
                        fee = 0.0
                    net = gross + fee            # PayPal fee_amount is negative
                    if net <= 0:
                        continue
                    payer = ((td.get("payer_info") or {}).get("payer_name") or {})
                    who = (payer.get("alternate_full_name")
                           or (td.get("payer_info") or {}).get("email_address") or "PayPal")
                    out.append({
                        "external_id": str(ti.get("transaction_id") or ""),
                        "source": str(who)[:120],
                        "amount_cents": _cents(net),
                        "gross_cents": _cents(gross),
                        "received_at": str(ti.get("transaction_initiation_date") or "")[:10]
                                       or datetime.now().date().isoformat(),
                        "income_type": "sale",
                        "currency": "USD",
                        "amount_native": None,
                        "notes": (ti.get("transaction_subject") or ti.get("transaction_note")
                                  or "PayPal import")[:200],
                    })
                if page >= int(data.get("total_pages") or 1):
                    break
                page += 1
    except Exception as e:
        return out, f"PayPal fetch error: {e}"
    return [r for r in out if r["external_id"]], ""


# ── Printify — order revenue (read-only; net = retail − production cost) ─────
def _fetch_printify() -> tuple[list, str]:
    key = _printify_key()
    if not key:
        return [], "Printify not connected — save printify_api_key"
    headers = {"Authorization": f"Bearer {key}", **_UA}
    out = []
    try:
        with httpx.Client(timeout=30) as client:
            shop = get_setting("printify_shop_id")
            if not shop:
                r = client.get("https://api.printify.com/v1/shops.json", headers=headers)
                if r.status_code != 200:
                    return [], f"Printify shops failed ({r.status_code}): {r.text[:180]}"
                shops = r.json() or []
                if not shops:
                    return [], "Printify: no shops on this account"
                shop = shops[0].get("id")
            r = client.get(f"https://api.printify.com/v1/shops/{shop}/orders.json?limit=50",
                           headers=headers)
            if r.status_code != 200:
                return [], f"Printify orders failed ({r.status_code}): {r.text[:180]}"
            data = r.json()
            orders = data.get("data") if isinstance(data, dict) else data
            for o in orders or []:
                if str(o.get("status") or "").lower() in ("canceled", "cancelled"):
                    continue
                oid = str(o.get("id") or "")
                if not oid:
                    continue
                revenue = int(o.get("total_price") or 0)      # Printify amounts are already cents
                cost = 0
                for li in o.get("line_items") or []:
                    q = int(li.get("quantity") or 1)
                    unit_cost = li.get("cost")
                    if unit_cost is None:
                        unit_cost = (li.get("metadata") or {}).get("cost") or 0
                    try:
                        cost += int(unit_cost) * q
                    except (TypeError, ValueError):
                        pass
                net = revenue - cost
                if net <= 0:
                    continue      # a sample/own order (cost only) is spending, not income — skip
                out.append({
                    "external_id": oid,
                    "source": "Printify",
                    "amount_cents": net,
                    "gross_cents": revenue,
                    "received_at": str(o.get("created_at") or "")[:10]
                                   or datetime.now().date().isoformat(),
                    "income_type": "sale",
                    "currency": "USD",
                    "amount_native": None,
                    "notes": f"Printify order {oid} — retail {revenue / 100:.2f} − cost {cost / 100:.2f}",
                })
    except Exception as e:
        return out, f"Printify fetch error: {e}"
    return out, ""


# ── On-chain — incoming txs to a PUBLIC address via public explorers ─────────
def _btc_usd(client) -> float:
    d = client.get("https://mempool.space/api/v1/prices", headers=_UA).json()
    return float(d.get("USD") or 0)


def _eth_usd(client) -> float:
    d = client.get("https://api.coingecko.com/api/v3/simple/price",
                   params={"ids": "ethereum", "vs_currencies": "usd"}, headers=_UA).json()
    return float((d.get("ethereum") or {}).get("usd") or 0)


def _fetch_onchain() -> tuple[list, str]:
    addr, chain = _wallet()
    if not addr:
        return [], "No wallet configured — save income_wallet_address (public address, read-only)"
    out = []
    try:
        with httpx.Client(timeout=30) as client:
            if chain == "btc":
                price = _btc_usd(client)
                txs = client.get(f"https://mempool.space/api/address/{addr}/txs",
                                 headers=_UA).json()
                for tx in txs or []:
                    st = tx.get("status") or {}
                    if not st.get("confirmed"):
                        continue
                    # pure receives only: if the address funded any input this is a
                    # spend/change tx, not income
                    if any(((vin.get("prevout") or {}).get("scriptpubkey_address") == addr)
                           for vin in tx.get("vin") or []):
                        continue
                    sats = sum(int(vo.get("value") or 0) for vo in tx.get("vout") or []
                               if vo.get("scriptpubkey_address") == addr)
                    if sats <= 0:
                        continue
                    btc = sats / 1e8
                    when = datetime.fromtimestamp(int(st.get("block_time") or time.time()))
                    out.append({
                        "external_id": f"btc:{tx.get('txid')}",
                        "source": "On-chain (BTC)",
                        "amount_cents": _cents(btc * price),
                        "gross_cents": None,
                        "received_at": when.date().isoformat(),
                        "income_type": "crypto_receive",
                        "currency": "BTC",
                        "amount_native": btc,
                        "notes": f"{btc:.8f} BTC to {addr[:12]}… (USD at import-time spot)",
                    })
            elif chain == "eth":
                price = _eth_usd(client)
                r = client.get("https://eth.blockscout.com/api",
                               params={"module": "account", "action": "txlist",
                                       "address": addr, "sort": "desc",
                                       "page": 1, "offset": 25}, headers=_UA)
                res = (r.json() or {}).get("result")
                if not isinstance(res, list):
                    return [], f"Explorer error: {str(res)[:160]}"
                for tx in res:
                    if str(tx.get("isError") or "0") != "0":
                        continue
                    if str(tx.get("to") or "").lower() != addr.lower():
                        continue    # incoming only
                    try:
                        eth = int(tx.get("value") or 0) / 1e18
                    except (TypeError, ValueError):
                        continue
                    if eth <= 0:
                        continue
                    when = datetime.fromtimestamp(int(tx.get("timeStamp") or time.time()))
                    out.append({
                        "external_id": f"eth:{tx.get('hash')}",
                        "source": "On-chain (ETH)",
                        "amount_cents": _cents(eth * price),
                        "gross_cents": None,
                        "received_at": when.date().isoformat(),
                        "income_type": "crypto_receive",
                        "currency": "ETH",
                        "amount_native": eth,
                        "notes": f"{eth:.6f} ETH to {addr[:12]}… (USD at import-time spot)",
                    })
            else:
                return [], f"unsupported chain {chain!r} — use btc or eth"
    except Exception as e:
        return out, f"On-chain fetch error: {e}"
    return out, ""


_FETCHERS = {"paypal": _fetch_paypal, "printify": _fetch_printify, "onchain": _fetch_onchain}


# ── the import run (dedupe + state bookkeeping) ──────────────────────────────
_locks = {s: threading.Lock() for s in SOURCES}


def run_import(source: str) -> dict:
    """Fetch one source and record new income rows. Read-only against the outside
    world; the only write is INSERT OR IGNORE into `paychecks` (dedupe by the
    UNIQUE (external_source, external_txn_id) index). Safe to call repeatedly."""
    if source not in SOURCES:
        return {"ok": False, "source": source, "error": f"unknown source {source!r}"}
    lock = _locks[source]
    if not lock.acquire(blocking=False):
        return {"ok": False, "source": source, "error": "import already running"}
    try:
        conn = get_conn()
        try:
            _ensure_schema(conn)
            row = conn.execute("SELECT running FROM income_import_state WHERE source=?",
                               (source,)).fetchone()
            if row and int(row["running"] or 0):
                return {"ok": False, "source": source, "error": "import already running"}
            conn.execute("UPDATE income_import_state SET running=1 WHERE source=?", (source,))
            conn.commit()
        finally:
            conn.close()

        records, err = _FETCHERS[source]()

        added = 0
        conn = get_conn()
        try:
            for rec in records:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO paychecks (source,amount_cents,gross_cents,"
                    "received_at,hours,hourly_rate_cents,cycle,notes,extra,income_type,"
                    "currency,amount_native,external_source,external_txn_id,voided) "
                    "VALUES (?,?,?,?,NULL,NULL,'irregular',?,'{}',?,?,?,?,?,0)",
                    (rec["source"], int(rec["amount_cents"] or 0), rec.get("gross_cents"),
                     rec["received_at"], rec.get("notes") or "", rec["income_type"],
                     rec.get("currency") or "USD", rec.get("amount_native"),
                     source, rec["external_id"]))
                if cur.rowcount and cur.rowcount > 0:
                    added += 1
            now = _now_iso()
            conn.execute(
                "UPDATE income_import_state SET running=0, last_run_at=?, last_added=?, "
                "last_seen=?, last_error=?, last_ok_at=CASE WHEN ?='' THEN ? ELSE last_ok_at END "
                "WHERE source=?",
                (now, added, len(records), err or "", err or "", now, source))
            conn.commit()
        finally:
            conn.close()
        if added:
            logger.info("income import [%s]: +%d new income row(s) (%d seen)",
                        source, added, len(records))
        return {"ok": not err, "source": source, "seen": len(records),
                "added": added, "error": err or None}
    except Exception as e:
        logger.exception("income import [%s] failed", source)
        try:
            conn = get_conn()
            conn.execute("UPDATE income_import_state SET running=0, last_run_at=?, last_error=? "
                         "WHERE source=?", (_now_iso(), str(e)[:300], source))
            conn.commit()
            conn.close()
        except Exception:
            pass
        return {"ok": False, "source": source, "error": str(e)}
    finally:
        lock.release()


# ── status (for the Income tab UI; never returns secret values) ──────────────
def status() -> dict:
    conn = get_conn()
    try:
        _ensure_schema(conn)
        rows = {r["source"]: dict(r) for r in
                conn.execute("SELECT * FROM income_import_state").fetchall()}
    finally:
        conn.close()
    addr, chain = _wallet()
    src = {}
    for s in SOURCES:
        st = rows.get(s, {})
        src[s] = {"configured": configured(s),
                  "running": bool(st.get("running")),
                  "last_run_at": st.get("last_run_at"),
                  "last_ok_at": st.get("last_ok_at"),
                  "last_added": st.get("last_added") or 0,
                  "last_seen": st.get("last_seen") or 0,
                  "last_error": st.get("last_error") or ""}
    src["paypal"]["mode"] = _paypal_cfg()["mode"]
    src["printify"]["shop_id"] = get_setting("printify_shop_id") or ""
    src["onchain"]["address"] = addr          # public address — not a secret
    src["onchain"]["chain"] = chain
    return {"enabled": enabled(),
            "interval_min": _interval_sec() // 60,
            "sources": src}


# ── background daemon (gated, default OFF; manual Import-now always works) ───
_daemon = {"thread": None}


def _seconds_since(iso: str) -> float:
    try:
        return time.time() - datetime.fromisoformat(iso).timestamp()
    except Exception:
        return float("inf")


def _loop():
    time.sleep(45)   # let the app settle
    while True:
        try:
            if enabled():
                iv = _interval_sec()
                conn = get_conn()
                try:
                    _ensure_schema(conn)
                    rows = {r["source"]: dict(r) for r in
                            conn.execute("SELECT * FROM income_import_state").fetchall()}
                finally:
                    conn.close()
                for s in SOURCES:
                    if not configured(s):
                        continue
                    st = rows.get(s, {})
                    if st.get("running"):
                        continue
                    if _seconds_since(st.get("last_run_at") or "") >= iv:
                        run_import(s)   # read-only pull; result recorded in state
        except Exception:
            logger.exception("income autoimport loop tick failed")
        time.sleep(60)


def start():
    """Start the gated auto-import daemon (call once from main.py startup).
    Clears any `running` flag a crash left behind — the boot reconcile."""
    if _daemon["thread"]:
        return
    try:
        conn = get_conn()
        _ensure_schema(conn)
        conn.execute("UPDATE income_import_state SET running=0")
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("income_import boot reconcile failed")
    t = threading.Thread(target=_loop, daemon=True, name="income-import")
    _daemon["thread"] = t
    t.start()
    logger.info("income_import daemon started (autoimport enabled=%s)", enabled())

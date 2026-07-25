"""ledger — 💵 personal money ledger: paychecks in, purchases out (Finance → Bills pane).

The sibling of ``bills.py``. Where bills tracks *recurring obligations* and their
payment history, this tracks the two other halves of a real personal ledger:

  • paychecks  — money IN. Employer or client, net amount, optional gross, and an
                 optional hours × hourly-rate pair for hourly/contract work (the
                 rate is per-entry, never a constant — everyone bills differently).
                 An expected `cycle` lets the UI reason about recurring income.
  • purchases  — money OUT that is NOT a bill. Bill payments already live in
                 `bill_payments` (written by bills.py's mark-paid), so purchases
                 deliberately has no bill_id: double-entering a bill here would
                 double-count it in every total. The UI says so out loud.

Income Phase 1 generalized `paychecks` into any-kind income, additively: the
table gained income_type/currency/amount_native/external_source/external_txn_id/
voided columns (db_schema.py), /api/ledger/paychecks is untouched byte-for-byte,
and /api/ledger/income is the new superset (GET all types + per-type totals,
POST/PATCH/DELETE a manual entry). Phase 1 writes external_source='manual' only.
Phase 2 (app/income_import.py + income_sources.py in this package) adds the
READ-ONLY PayPal/Printify/on-chain importers, which write rows here with
external_source=paypal|printify|onchain and dedupe on external_txn_id.

Summary/series therefore treat outgoings as `purchases + bill_payments`, two
disjoint sets, and net as `income − outgoings`; income sums exclude voided rows.

Same conventions as bills.py throughout: integer cents everywhere, free-text
categories (shared vocabulary with bills), an `extra` JSON object of arbitrary
user-defined fields, and CSV export/import with dollar-formatted amounts.

NOT the game world's economy (world_ledger / world_ops_ledger) — unrelated.
"""
import csv as _csv
import io as _io
import json as _json
from datetime import date
from typing import Optional

from fastapi import HTTPException, Body
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from deps import *          # get_conn, logger, …
from ._base import router

# ── schema (defined in db_schema.py; ensured once here at import, exactly like
#    bills.py and the money package's own _base._ensure_schema) ───────────────
from db_schema import create_ledger_tables as _create_ledger_tables

# Line-item handling lives in budget.py (it owns `purchase_items` and the name
# normalizer that makes items comparable across trips). Imported one-way — budget
# never imports ledger — so there is no cycle.
from .budget import (clean_items as _clean_items,
                     replace_purchase_items as _replace_purchase_items,
                     load_purchase_items as _load_purchase_items,
                     items_total_cents as _items_total_cents)


def _ensure_ledger_schema():
    conn = get_conn()
    try:
        _create_ledger_tables(conn)   # commits internally
    finally:
        conn.close()


_ensure_ledger_schema()


# ── pay cycles (expected recurring income; purely descriptive — nothing here
#    auto-advances a date the way bills' next_due does) ────────────────────────
PAY_CYCLES = ("weekly", "biweekly", "semimonthly", "monthly", "irregular")


# ── shared helpers (mirrors bills.py) ─────────────────────────────────────────
def _norm_date(v) -> Optional[str]:
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except (ValueError, TypeError):
        raise HTTPException(400, f"bad date {v!r} — use YYYY-MM-DD")


def _norm_extra(v) -> str:
    """`extra` is a JSON object of user-defined fields; accept dict or JSON text."""
    if v in (None, ""):
        return "{}"
    if isinstance(v, str):
        try:
            v = _json.loads(v)
        except ValueError:
            raise HTTPException(400, "extra must be a JSON object")
    if not isinstance(v, dict):
        raise HTTPException(400, "extra must be a JSON object")
    return _json.dumps({str(k)[:80]: str(val)[:500] for k, val in v.items()})


def _row(r) -> dict:
    d = dict(r)
    try:
        d["extra"] = _json.loads(d.get("extra") or "{}")
    except ValueError:
        d["extra"] = {}
    return d


def _cents(v, field="amount_cents") -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field} must be a whole number of cents")


def _hours(v) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        h = float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, "hours must be a number")
    if h < 0:
        raise HTTPException(400, "hours must be positive")
    return h


def derive_amount_cents(amount_cents, hours, hourly_rate_cents) -> Optional[int]:
    """hours × rate fills in the amount when it wasn't given explicitly.

    An explicit amount always wins (a paycheck can differ from hours × rate —
    overtime, a bonus, or the net after withholding). The rate lives on the
    entry, so different clients/jobs can carry different rates.
    """
    if amount_cents is not None:
        return amount_cents
    if hours is not None and hourly_rate_cents is not None:
        return max(0, int(round(hours * hourly_rate_cents)))
    return None


# ══ PAYCHECKS ════════════════════════════════════════════════════════════════
class PaycheckIn(BaseModel):
    source: str                                  # employer or client
    amount_cents: Optional[int] = None           # net; auto-filled from hours × rate
    gross_cents: Optional[int] = None            # before withholding, optional
    received_at: Optional[str] = None            # YYYY-MM-DD, default today
    hours: Optional[float] = None
    hourly_rate_cents: Optional[int] = None
    cycle: str = "irregular"                     # expected recurrence
    notes: str = ""
    extra: Optional[dict] = None


def _get_paycheck(conn, pid: int):
    row = conn.execute("SELECT * FROM paychecks WHERE id=?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "paycheck not found")
    return row


def _valid_cycle(c: str) -> bool:
    return c in PAY_CYCLES


@router.get("/api/ledger/paychecks")
def list_paychecks(limit: int = 500, source: Optional[str] = None):
    """Newest first. Totals for this month and year-to-date ride along."""
    limit = max(1, min(2000, int(limit)))
    today = date.today().isoformat()
    conn = get_conn()
    try:
        where, args = "", []
        if source:
            where, args = "WHERE source=?", [source]
        rows = conn.execute(
            f"SELECT * FROM paychecks {where} ORDER BY received_at DESC, id DESC LIMIT ?",
            (*args, limit)).fetchall()
        month = conn.execute("SELECT COALESCE(SUM(amount_cents),0), COUNT(*) FROM paychecks "
                             "WHERE substr(received_at,1,7)=?", (today[:7],)).fetchone()
        ytd = conn.execute("SELECT COALESCE(SUM(amount_cents),0), COUNT(*) FROM paychecks "
                           "WHERE substr(received_at,1,4)=?", (today[:4],)).fetchone()
        sources = [r[0] for r in conn.execute(
            "SELECT DISTINCT source FROM paychecks WHERE source<>'' ORDER BY source").fetchall()]
        return {"paychecks": [_row(r) for r in rows], "sources": sources,
                "month_cents": month[0], "month_count": month[1],
                "ytd_cents": ytd[0], "ytd_count": ytd[1], "today": today}
    finally:
        conn.close()


@router.post("/api/ledger/paychecks")
def create_paycheck(p: PaycheckIn):
    source = (p.source or "").strip()
    if not source:
        raise HTTPException(400, "source required")
    if not _valid_cycle(p.cycle or "irregular"):
        raise HTTPException(400, f"bad cycle {p.cycle!r} — {'|'.join(PAY_CYCLES)}")
    hours = _hours(p.hours)
    rate = _cents(p.hourly_rate_cents, "hourly_rate_cents")
    amount = derive_amount_cents(_cents(p.amount_cents), hours, rate)
    if amount is None:
        raise HTTPException(400, "amount_cents required (or give both hours and hourly_rate_cents)")
    received = _norm_date(p.received_at) or date.today().isoformat()
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO paychecks (source,amount_cents,gross_cents,received_at,hours,"
            "hourly_rate_cents,cycle,notes,extra) VALUES (?,?,?,?,?,?,?,?,?)",
            (source, amount, _cents(p.gross_cents, "gross_cents"), received, hours, rate,
             p.cycle or "irregular", (p.notes or "").strip(), _norm_extra(p.extra)))
        conn.commit()
        return _row(_get_paycheck(conn, cur.lastrowid))
    finally:
        conn.close()


_PAY_PATCHABLE = {"source", "amount_cents", "gross_cents", "received_at", "hours",
                  "hourly_rate_cents", "cycle", "notes", "extra"}


@router.patch("/api/ledger/paychecks/{pid}")
def update_paycheck(pid: int, data: dict = Body(...)):
    fields = {k: v for k, v in (data or {}).items() if k in _PAY_PATCHABLE}
    if not fields:
        raise HTTPException(400, "nothing to update")
    conn = get_conn()
    try:
        row = _get_paycheck(conn, pid)
        if "cycle" in fields and not _valid_cycle(fields["cycle"]):
            raise HTTPException(400, f"bad cycle {fields['cycle']!r} — {'|'.join(PAY_CYCLES)}")
        if "source" in fields:
            fields["source"] = str(fields["source"]).strip()
            if not fields["source"]:
                raise HTTPException(400, "source required")
        if "received_at" in fields:
            fields["received_at"] = _norm_date(fields["received_at"]) or row["received_at"]
        if "extra" in fields:
            fields["extra"] = _norm_extra(fields["extra"])
        if "hours" in fields:
            fields["hours"] = _hours(fields["hours"])
        if "hourly_rate_cents" in fields:
            fields["hourly_rate_cents"] = _cents(fields["hourly_rate_cents"], "hourly_rate_cents")
        if "gross_cents" in fields:
            fields["gross_cents"] = _cents(fields["gross_cents"], "gross_cents")
        if "amount_cents" in fields:
            fields["amount_cents"] = _cents(fields["amount_cents"])
        # Re-derive the amount when hours/rate moved and no explicit amount came with them.
        if "amount_cents" not in fields and ("hours" in fields or "hourly_rate_cents" in fields):
            h = fields.get("hours", row["hours"])
            r = fields.get("hourly_rate_cents", row["hourly_rate_cents"])
            derived = derive_amount_cents(None, h, r)
            if derived is not None:
                fields["amount_cents"] = derived
        if fields.get("amount_cents", row["amount_cents"]) is None:
            raise HTTPException(400, "amount_cents required (or give both hours and hourly_rate_cents)")
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE paychecks SET {sets} WHERE id=?", (*fields.values(), pid))
        conn.commit()
        return _row(_get_paycheck(conn, pid))
    finally:
        conn.close()


@router.delete("/api/ledger/paychecks/{pid}")
def delete_paycheck(pid: int):
    conn = get_conn()
    try:
        _get_paycheck(conn, pid)
        conn.execute("DELETE FROM paychecks WHERE id=?", (pid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ══ INCOME (Income Phase 1) ══════════════════════════════════════════════════
# `paychecks` generalized ADDITIVELY into any-kind income — same table, same row
# shape, plus income_type/currency/amount_native/external_source/external_txn_id/
# voided (see db_schema.py create_ledger_tables migrations). /api/ledger/paychecks
# above is untouched, byte-for-byte back-compat; this is the superset view + a
# manual-entry endpoint for non-paycheck income (sale, refund, gift, dividend, …).
#
# The endpoints below are manual entry ONLY — no external API calls. Every row
# created here writes external_source='manual', external_txn_id=NULL. The Phase 2
# READ-ONLY importers (app/income_import.py, routes in income_sources.py) are the
# only writers of external_source=paypal|printify|onchain rows, deduped by the
# UNIQUE (external_source, external_txn_id) index.
INCOME_TYPES = ("paycheck", "sale", "refund", "gift", "dividend", "interest",
                "crypto_receive", "payout", "other")


def _valid_income_type(t: str) -> bool:
    return t in INCOME_TYPES


class IncomeIn(BaseModel):
    source: str                                  # payer / employer / marketplace / client
    income_type: str = "paycheck"
    amount_cents: Optional[int] = None           # USD-at-receipt; auto-filled from hours × rate
    gross_cents: Optional[int] = None
    received_at: Optional[str] = None            # YYYY-MM-DD, default today
    hours: Optional[float] = None
    hourly_rate_cents: Optional[int] = None
    cycle: str = "irregular"
    currency: str = "USD"                        # display currency; amount_cents is always USD
    amount_native: Optional[float] = None         # coin/share amount when currency != USD
    notes: str = ""
    extra: Optional[dict] = None


def _get_income(conn, iid: int):
    row = conn.execute("SELECT * FROM paychecks WHERE id=?", (iid,)).fetchone()
    if not row:
        raise HTTPException(404, "income entry not found")
    return row


def _norm_currency(v) -> str:
    return (str(v or "USD").strip().upper()[:10]) or "USD"


def _native(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, "amount_native must be a number")


@router.get("/api/ledger/income")
def list_income(limit: int = 500, type: Optional[str] = None):
    """Every income row (all income_types), newest first, with a per-type
    breakdown alongside the same month/YTD totals /api/ledger/paychecks shows.
    Voided rows still appear in the list (so they can be reviewed/un-voided) but
    are excluded from every total, exactly like the shared summary/series."""
    limit = max(1, min(2000, int(limit)))
    if type is not None and not _valid_income_type(type):
        raise HTTPException(400, f"bad type {type!r} — {'|'.join(INCOME_TYPES)}")
    today = date.today().isoformat()
    conn = get_conn()
    try:
        where, args = "", []
        if type:
            where, args = "WHERE income_type=?", [type]
        rows = conn.execute(
            f"SELECT * FROM paychecks {where} ORDER BY received_at DESC, id DESC LIMIT ?",
            (*args, limit)).fetchall()
        by_type = conn.execute(
            "SELECT COALESCE(NULLIF(income_type,''),'paycheck') AS income_type, "
            "COALESCE(SUM(amount_cents),0) AS total_cents, COUNT(*) AS count "
            "FROM paychecks WHERE voided=0 GROUP BY income_type ORDER BY total_cents DESC"
        ).fetchall()
        month = conn.execute("SELECT COALESCE(SUM(amount_cents),0), COUNT(*) FROM paychecks "
                             "WHERE substr(received_at,1,7)=? AND voided=0", (today[:7],)).fetchone()
        ytd = conn.execute("SELECT COALESCE(SUM(amount_cents),0), COUNT(*) FROM paychecks "
                           "WHERE substr(received_at,1,4)=? AND voided=0", (today[:4],)).fetchone()
        sources = [r[0] for r in conn.execute(
            "SELECT DISTINCT source FROM paychecks WHERE source<>'' ORDER BY source").fetchall()]
        return {"income": [_row(r) for r in rows], "types": list(INCOME_TYPES),
                "by_type": [dict(r) for r in by_type], "sources": sources,
                "month_cents": month[0], "month_count": month[1],
                "ytd_cents": ytd[0], "ytd_count": ytd[1], "today": today}
    finally:
        conn.close()


@router.post("/api/ledger/income")
def create_income(p: IncomeIn):
    source = (p.source or "").strip()
    if not source:
        raise HTTPException(400, "source required")
    itype = p.income_type or "paycheck"
    if not _valid_income_type(itype):
        raise HTTPException(400, f"bad type {itype!r} — {'|'.join(INCOME_TYPES)}")
    if not _valid_cycle(p.cycle or "irregular"):
        raise HTTPException(400, f"bad cycle {p.cycle!r} — {'|'.join(PAY_CYCLES)}")
    hours = _hours(p.hours)
    rate = _cents(p.hourly_rate_cents, "hourly_rate_cents")
    amount = derive_amount_cents(_cents(p.amount_cents), hours, rate)
    if amount is None:
        raise HTTPException(400, "amount_cents required (or give both hours and hourly_rate_cents)")
    received = _norm_date(p.received_at) or date.today().isoformat()
    currency = _norm_currency(p.currency)
    native = _native(p.amount_native)
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO paychecks (source,amount_cents,gross_cents,received_at,hours,"
            "hourly_rate_cents,cycle,notes,extra,income_type,currency,amount_native,"
            "external_source,external_txn_id,voided) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (source, amount, _cents(p.gross_cents, "gross_cents"), received, hours, rate,
             p.cycle or "irregular", (p.notes or "").strip(), _norm_extra(p.extra),
             itype, currency, native, "manual", None))
        conn.commit()
        return _row(_get_income(conn, cur.lastrowid))
    finally:
        conn.close()


_INCOME_PATCHABLE = _PAY_PATCHABLE | {"income_type", "currency", "amount_native", "voided"}


@router.patch("/api/ledger/income/{iid}")
def update_income(iid: int, data: dict = Body(...)):
    fields = {k: v for k, v in (data or {}).items() if k in _INCOME_PATCHABLE}
    if not fields:
        raise HTTPException(400, "nothing to update")
    conn = get_conn()
    try:
        row = _get_income(conn, iid)
        if "cycle" in fields and not _valid_cycle(fields["cycle"]):
            raise HTTPException(400, f"bad cycle {fields['cycle']!r} — {'|'.join(PAY_CYCLES)}")
        if "income_type" in fields and not _valid_income_type(fields["income_type"]):
            raise HTTPException(400, f"bad type {fields['income_type']!r} — {'|'.join(INCOME_TYPES)}")
        if "source" in fields:
            fields["source"] = str(fields["source"]).strip()
            if not fields["source"]:
                raise HTTPException(400, "source required")
        if "received_at" in fields:
            fields["received_at"] = _norm_date(fields["received_at"]) or row["received_at"]
        if "extra" in fields:
            fields["extra"] = _norm_extra(fields["extra"])
        if "hours" in fields:
            fields["hours"] = _hours(fields["hours"])
        if "hourly_rate_cents" in fields:
            fields["hourly_rate_cents"] = _cents(fields["hourly_rate_cents"], "hourly_rate_cents")
        if "gross_cents" in fields:
            fields["gross_cents"] = _cents(fields["gross_cents"], "gross_cents")
        if "amount_cents" in fields:
            fields["amount_cents"] = _cents(fields["amount_cents"])
        if "amount_native" in fields:
            fields["amount_native"] = _native(fields["amount_native"])
        if "currency" in fields:
            fields["currency"] = _norm_currency(fields["currency"])
        if "voided" in fields:
            fields["voided"] = 1 if fields["voided"] in (1, True, "1", "true", "True") else 0
        # Re-derive the amount when hours/rate moved and no explicit amount came with them.
        if "amount_cents" not in fields and ("hours" in fields or "hourly_rate_cents" in fields):
            h = fields.get("hours", row["hours"])
            r = fields.get("hourly_rate_cents", row["hourly_rate_cents"])
            derived = derive_amount_cents(None, h, r)
            if derived is not None:
                fields["amount_cents"] = derived
        if fields.get("amount_cents", row["amount_cents"]) is None:
            raise HTTPException(400, "amount_cents required (or give both hours and hourly_rate_cents)")
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE paychecks SET {sets} WHERE id=?", (*fields.values(), iid))
        conn.commit()
        return _row(_get_income(conn, iid))
    finally:
        conn.close()


@router.delete("/api/ledger/income/{iid}")
def delete_income(iid: int):
    conn = get_conn()
    try:
        _get_income(conn, iid)
        conn.execute("DELETE FROM paychecks WHERE id=?", (iid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ══ PURCHASES ════════════════════════════════════════════════════════════════
class PurchaseIn(BaseModel):
    merchant: str
    amount_cents: Optional[int] = None
    purchased_at: Optional[str] = None           # YYYY-MM-DD, default today
    category: str = ""                           # free text, shared with bills
    method: str = ""                             # card / cash / transfer / …
    notes: str = ""
    extra: Optional[dict] = None
    # OPTIONAL line items. A trip can still be logged as one total exactly as
    # before — this is purely additive. When items are given and amount_cents is
    # not, the amount is summed from the lines; when BOTH are given the explicit
    # amount wins, because a receipt total legitimately exceeds the lines (tax,
    # deposits, fees). The parent amount stays the authoritative money either way;
    # `purchase_items` is detail, never a second copy of the total.
    items: Optional[list] = None


def _get_purchase(conn, pid: int):
    row = conn.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "purchase not found")
    return row


def _with_items(conn, row) -> dict:
    """A purchase dict plus its line items and their total (for the UI)."""
    d = _row(row)
    items = _load_purchase_items(conn, d["id"])
    d["items"] = items
    d["item_count"] = len(items)
    d["items_total_cents"] = sum(int(i.get("line_total_cents") or 0) for i in items)
    return d


@router.get("/api/ledger/purchases")
def list_purchases(limit: int = 500, category: Optional[str] = None,
                   month: Optional[str] = None):
    """Newest first, with this-month / YTD totals and a per-category breakdown."""
    limit = max(1, min(2000, int(limit)))
    today = date.today().isoformat()
    conn = get_conn()
    try:
        clauses, args = [], []
        if category:
            clauses.append("category=?")
            args.append(category)
        if month:
            clauses.append("substr(purchased_at,1,7)=?")
            args.append(str(month)[:7])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM purchases {where} ORDER BY purchased_at DESC, id DESC LIMIT ?",
            (*args, limit)).fetchall()
        m = conn.execute("SELECT COALESCE(SUM(amount_cents),0), COUNT(*) FROM purchases "
                         "WHERE substr(purchased_at,1,7)=?", (today[:7],)).fetchone()
        y = conn.execute("SELECT COALESCE(SUM(amount_cents),0), COUNT(*) FROM purchases "
                         "WHERE substr(purchased_at,1,4)=?", (today[:4],)).fetchone()
        cats = conn.execute(
            "SELECT COALESCE(NULLIF(category,''),'uncategorized') AS cat, SUM(amount_cents) AS total, "
            "COUNT(*) AS n FROM purchases WHERE substr(purchased_at,1,7)=? GROUP BY cat "
            "ORDER BY total DESC", (today[:7],)).fetchall()
        # How many lines each listed purchase has, in ONE query — the list must not
        # fan out into a per-row lookup.
        line_counts: dict = {}
        try:
            for r in conn.execute(
                    "SELECT purchase_id, COUNT(*) n FROM purchase_items GROUP BY purchase_id"):
                line_counts[r["purchase_id"]] = r["n"]
        except Exception:
            line_counts = {}
        out_rows = []
        for r in rows:
            d = _row(r)
            d["item_count"] = line_counts.get(d["id"], 0)
            out_rows.append(d)
        return {"purchases": out_rows,
                "month_cents": m[0], "month_count": m[1],
                "ytd_cents": y[0], "ytd_count": y[1],
                "month_categories": [dict(r) for r in cats], "today": today}
    finally:
        conn.close()


@router.post("/api/ledger/purchases")
def create_purchase(p: PurchaseIn):
    merchant = (p.merchant or "").strip()
    if not merchant:
        raise HTTPException(400, "merchant required")
    category = (p.category or "").strip()
    items = _clean_items(p.items, category)
    amount = _cents(p.amount_cents)
    if amount is None:
        # Itemized trip with no stated total: the lines ARE the total. This is the
        # only place a purchase amount is derived, and only from numbers the owner
        # typed himself.
        amount = _items_total_cents(items) if items else None
    if amount is None:
        raise HTTPException(400, "amount_cents required (or give line items to sum)")
    when = _norm_date(p.purchased_at) or date.today().isoformat()
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO purchases (purchased_at,merchant,amount_cents,category,method,notes,extra) "
            "VALUES (?,?,?,?,?,?,?)",
            (when, merchant, amount, category, (p.method or "").strip(),
             (p.notes or "").strip(), _norm_extra(p.extra)))
        if items:
            _replace_purchase_items(conn, cur.lastrowid, items)
        conn.commit()
        return _with_items(conn, _get_purchase(conn, cur.lastrowid))
    finally:
        conn.close()


@router.get("/api/ledger/purchases/{pid}/items")
def purchase_items(pid: int):
    """The line items on one purchase (empty list for a single-total trip)."""
    conn = get_conn()
    try:
        row = _get_purchase(conn, pid)
        items = _load_purchase_items(conn, pid)
        return {"purchase_id": pid, "items": items,
                "items_total_cents": sum(int(i.get("line_total_cents") or 0) for i in items),
                "amount_cents": row["amount_cents"],
                "note": ("The purchase amount stays authoritative — a receipt total can "
                         "exceed the lines (tax, deposits, fees).")}
    finally:
        conn.close()


_PUR_PATCHABLE = {"purchased_at", "merchant", "amount_cents", "category", "method",
                  "notes", "extra"}


@router.patch("/api/ledger/purchases/{pid}")
def update_purchase(pid: int, data: dict = Body(...)):
    fields = {k: v for k, v in (data or {}).items() if k in _PUR_PATCHABLE}
    if not fields and "items" not in (data or {}):
        raise HTTPException(400, "nothing to update")
    conn = get_conn()
    try:
        row = _get_purchase(conn, pid)
        if "merchant" in fields:
            fields["merchant"] = str(fields["merchant"]).strip()
            if not fields["merchant"]:
                raise HTTPException(400, "merchant required")
        if "purchased_at" in fields:
            fields["purchased_at"] = _norm_date(fields["purchased_at"]) or row["purchased_at"]
        if "extra" in fields:
            fields["extra"] = _norm_extra(fields["extra"])
        if "amount_cents" in fields:
            fields["amount_cents"] = _cents(fields["amount_cents"])
            if fields["amount_cents"] is None:
                raise HTTPException(400, "amount_cents required")
        # Line items: an edit REPLACES the whole set (merging half-edited lines by
        # name would be guesswork). Passing items=[] clears them and turns the
        # purchase back into a plain single-total row.
        new_items = None
        if "items" in (data or {}):
            cat = fields.get("category", row["category"]) or ""
            new_items = _clean_items((data or {}).get("items"), str(cat).strip())
            if "amount_cents" not in fields and new_items:
                fields["amount_cents"] = _items_total_cents(new_items)
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE purchases SET {sets} WHERE id=?", (*fields.values(), pid))
        if new_items is not None:
            _replace_purchase_items(conn, pid, new_items)
        conn.commit()
        return _with_items(conn, _get_purchase(conn, pid))
    finally:
        conn.close()


@router.delete("/api/ledger/purchases/{pid}")
def delete_purchase(pid: int):
    conn = get_conn()
    try:
        _get_purchase(conn, pid)
        # Lines belong to the purchase; they die with it (no orphan detail rows).
        try:
            conn.execute("DELETE FROM purchase_items WHERE purchase_id=?", (pid,))
        except Exception:
            pass
        conn.execute("DELETE FROM purchases WHERE id=?", (pid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ══ SUMMARY / SERIES ═════════════════════════════════════════════════════════
def _bucket(conn, table, date_col, key_len, key, extra_where=""):
    return conn.execute(
        f"SELECT COALESCE(SUM(amount_cents),0), COUNT(*) FROM {table} "
        f"WHERE substr({date_col},1,{key_len})=?{extra_where}", (key,)).fetchone()


@router.get("/api/ledger/summary")
def ledger_summary():
    """This month + year-to-date: income, purchases, bill payments, net.

    Bill payments come from the EXISTING `bill_payments` table (bills.py owns it)
    and purchases hold only non-bill spending, so the two never overlap:
        outgoings = purchases + bill_payments
        net       = income − outgoings
    """
    today = date.today().isoformat()
    conn = get_conn()
    try:
        # NOTE: "month" and "ytd" hold the scope dicts, so the plain keys are
        # month_key/year — do not reuse "month" for the YYYY-MM string.
        out = {"today": today, "month_key": today[:7], "year": today[:4]}
        for scope, key, klen in (("month", today[:7], 7), ("ytd", today[:4], 4)):
            # voided=0: future importer-written rows can be voided (dup/refund-undo)
            # without skewing the totals every other scope depends on.
            inc = _bucket(conn, "paychecks", "received_at", klen, key, " AND voided=0")
            pur = _bucket(conn, "purchases", "purchased_at", klen, key)
            bil = _bucket(conn, "bill_payments", "paid_at", klen, key)
            outgo = pur[0] + bil[0]
            out[scope] = {"income_cents": inc[0], "income_count": inc[1],
                          "purchases_cents": pur[0], "purchases_count": pur[1],
                          "bill_payments_cents": bil[0], "bill_payments_count": bil[1],
                          "outgoings_cents": outgo,
                          "net_cents": inc[0] - outgo}
        return out
    finally:
        conn.close()


@router.get("/api/ledger/series")
def ledger_series(months: int = 12):
    """Per-month income vs outgoings for the chart (outgoings = purchases + bills)."""
    months = max(1, min(60, int(months)))
    today = date.today()
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    idx = {k: i for i, k in enumerate(keys)}
    n = len(keys)
    income = [0] * n
    purchases = [0] * n
    bills = [0] * n
    conn = get_conn()
    try:
        for table, col, dest in (("paychecks", "received_at", income),
                                 ("purchases", "purchased_at", purchases),
                                 ("bill_payments", "paid_at", bills)):
            extra = " AND voided=0" if table == "paychecks" else ""
            for r in conn.execute(
                    f"SELECT substr({col},1,7) AS ym, SUM(amount_cents) AS total FROM {table} "
                    f"WHERE substr({col},1,7) >= ?{extra} GROUP BY ym", (keys[0],)).fetchall():
                i = idx.get(r["ym"])
                if i is not None:
                    dest[i] += r["total"] or 0
    finally:
        conn.close()
    outgoings = [purchases[i] + bills[i] for i in range(n)]
    return {"months": keys, "income_cents": income, "purchases_cents": purchases,
            "bill_payments_cents": bills, "outgoings_cents": outgoings,
            "net_cents": [income[i] - outgoings[i] for i in range(n)]}


# ══ CSV export / import ══════════════════════════════════════════════════════
_PAY_CSV_COLS = ["source", "amount", "gross", "received_at", "hours", "hourly_rate",
                 "cycle", "notes", "extra"]
_PUR_CSV_COLS = ["purchased_at", "merchant", "amount", "category", "method", "notes", "extra"]


def _usd(c) -> str:
    return "" if c is None else f"{c / 100:.2f}"


def _parse_usd(s):
    """'$1,234.50' → 123450 cents. Blank → None."""
    s = (s or "").strip()
    if s == "":
        return None
    return max(0, int(round(float(s.replace("$", "").replace(",", "")) * 100)))


# NOTE: the export routes are declared before nothing conflicting, but keep them
# distinct from /{pid} paths — "export.csv" would 422 against the int path param.
@router.get("/api/ledger/paychecks/export.csv")
def export_paychecks_csv():
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM paychecks ORDER BY received_at, id").fetchall()
    finally:
        conn.close()
    out = _io.StringIO()
    w = _csv.writer(out)
    w.writerow(_PAY_CSV_COLS)
    for r in rows:
        w.writerow([r["source"], _usd(r["amount_cents"]), _usd(r["gross_cents"]),
                    r["received_at"] or "", "" if r["hours"] is None else r["hours"],
                    _usd(r["hourly_rate_cents"]), r["cycle"] or "irregular",
                    r["notes"] or "", r["extra"] or "{}"])
    return PlainTextResponse(out.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=paychecks.csv"})


@router.post("/api/ledger/paychecks/import")
def import_paychecks_csv(data: dict = Body(...)):
    """Import paychecks from CSV text {csv: "..."}. Header aliases accepted:
    amount|net|amount_usd, received_at|date|received, hourly_rate|rate.
    Rows with hours + hourly_rate but no amount get the amount computed.
    Bad rows are skipped and reported."""
    text = (data or {}).get("csv") or ""
    if not text.strip():
        raise HTTPException(400, "csv text required")
    reader = _csv.DictReader(_io.StringIO(text))
    imported, errors = 0, []
    conn = get_conn()
    try:
        for i, row in enumerate(reader, start=2):    # start=2: header is line 1
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in (row or {}).items()}
            try:
                source = row.get("source") or row.get("employer") or row.get("client") or ""
                if not source:
                    raise ValueError("missing source")
                cycle = row.get("cycle") or "irregular"
                if not _valid_cycle(cycle):
                    raise ValueError(f"bad cycle {cycle!r}")
                hours = _hours(row.get("hours") or None)
                rate = _parse_usd(row.get("hourly_rate") or row.get("rate"))
                amount = derive_amount_cents(
                    _parse_usd(row.get("amount") or row.get("net") or row.get("amount_usd")),
                    hours, rate)
                if amount is None:
                    raise ValueError("missing amount (and no hours × hourly_rate to compute it)")
                received = _norm_date(row.get("received_at") or row.get("date")
                                      or row.get("received")) or date.today().isoformat()
                conn.execute(
                    "INSERT INTO paychecks (source,amount_cents,gross_cents,received_at,hours,"
                    "hourly_rate_cents,cycle,notes,extra) VALUES (?,?,?,?,?,?,?,?,?)",
                    (source, amount, _parse_usd(row.get("gross")), received, hours, rate,
                     cycle, row.get("notes", ""), _norm_extra(row.get("extra") or "{}")))
                imported += 1
            except (ValueError, HTTPException) as e:
                detail = getattr(e, "detail", None) or str(e)
                errors.append(f"line {i}: {detail}")
        conn.commit()
    finally:
        conn.close()
    return {"imported": imported, "errors": errors}


@router.get("/api/ledger/purchases/export.csv")
def export_purchases_csv():
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM purchases ORDER BY purchased_at, id").fetchall()
    finally:
        conn.close()
    out = _io.StringIO()
    w = _csv.writer(out)
    w.writerow(_PUR_CSV_COLS)
    for r in rows:
        w.writerow([r["purchased_at"] or "", r["merchant"], _usd(r["amount_cents"]),
                    r["category"] or "", r["method"] or "", r["notes"] or "", r["extra"] or "{}"])
    return PlainTextResponse(out.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=purchases.csv"})


@router.post("/api/ledger/purchases/import")
def import_purchases_csv(data: dict = Body(...)):
    """Import purchases from CSV text {csv: "..."}. Header aliases accepted:
    amount|amount_usd|total, purchased_at|date, merchant|store|vendor,
    method|payment_method. Bad rows are skipped and reported."""
    text = (data or {}).get("csv") or ""
    if not text.strip():
        raise HTTPException(400, "csv text required")
    reader = _csv.DictReader(_io.StringIO(text))
    imported, errors = 0, []
    conn = get_conn()
    try:
        for i, row in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in (row or {}).items()}
            try:
                merchant = row.get("merchant") or row.get("store") or row.get("vendor") or ""
                if not merchant:
                    raise ValueError("missing merchant")
                amount = _parse_usd(row.get("amount") or row.get("amount_usd") or row.get("total"))
                if amount is None:
                    raise ValueError("missing amount")
                when = _norm_date(row.get("purchased_at") or row.get("date")) \
                    or date.today().isoformat()
                conn.execute(
                    "INSERT INTO purchases (purchased_at,merchant,amount_cents,category,method,"
                    "notes,extra) VALUES (?,?,?,?,?,?,?)",
                    (when, merchant, amount, row.get("category", ""),
                     row.get("method") or row.get("payment_method", ""), row.get("notes", ""),
                     _norm_extra(row.get("extra") or "{}")))
                imported += 1
            except (ValueError, HTTPException) as e:
                detail = getattr(e, "detail", None) or str(e)
                errors.append(f"line {i}: {detail}")
        conn.commit()
    finally:
        conn.close()
    return {"imported": imported, "errors": errors}

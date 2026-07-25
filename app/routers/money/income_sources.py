"""income_sources — Income Phase 2 importer routes (READ-ONLY money-IN).

The thin API over app/income_import.py:

    GET  /api/income/import/status      per-source connected/last-run state +
                                        the auto-import toggle (secrets never
                                        returned — booleans only)
    POST /api/income/import/{source}    manual "Import now" for paypal |
                                        printify | onchain. Always available,
                                        independent of the auto-import gate.

Credentials are saved via the existing PATCH /api/settings (crypto.SECRET_KEYS
auto-encrypts paypal_client_id / paypal_client_secret / printify_api_key at
rest); the wallet address (income_wallet_address) is a PUBLIC address and not a
secret. The gated background daemon (setting income_autoimport_enabled, default
OFF) is started from main.py via income_import.start().

Nothing in this module — or anywhere in the import path — can move money:
importers only pull external records and INSERT income rows, deduped by the
UNIQUE (external_source, external_txn_id) index on `paychecks`.
"""
from fastapi import HTTPException

from deps import *          # get_conn, logger, …
from ._base import router

import income_import as _imp


@router.get("/api/income/import/status")
def income_import_status():
    """Per-source importer state for the Income tab (configured/running/last run)."""
    return _imp.status()


@router.post("/api/income/import/{source}")
def income_import_run(source: str):
    """Manual 'Import now' for one source. Read-only pull; new rows land in the
    income ledger (external_source=<source>), duplicates are ignored."""
    source = (source or "").strip().lower()
    if source not in _imp.SOURCES:
        raise HTTPException(400, f"unknown source {source!r} — {'|'.join(_imp.SOURCES)}")
    r = _imp.run_import(source)
    if not r.get("ok") and r.get("error") == "import already running":
        raise HTTPException(409, "an import for this source is already running")
    return r

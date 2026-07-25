"""Money — real-dollar mission control.

The agent swarm's purpose here is to make the owner REAL dollars (not in-game gold).
Shop search queries (demand signals) flow in from the storefront; "the company"
reviews them against the current catalog with the LLM and proposes money missions:
product gaps we should carry, affiliate/deal opportunities, online income ideas,
and local carpentry work leads (your local area) for the Acme Carpentry front.
Missions follow the same approve/reject queue pattern as world_ops prayers, and an
approved mission is announced into The Company world (world_events + a named agent).

Signals ingest (/api/money/signals POST) is designed to be allowlisted through the
auth middleware — it self-guards with the X-Money-Token header, which must equal
the `money_signal_token` setting.

This module is a package: the shared ``router`` + schema/enums/JSON-parser live in
``_base``; the routes are split across ``signals`` (demand-signal ingest/list),
``intel`` (LLM review + carpentry lead hunt), ``missions`` (the missions queue +
stats), ``auto`` (the autonomous cadence thread), ``bills`` (the REAL personal
bills tracker behind the Finance tab's 📆 Bills pane — /api/bills/*, unrelated to
the game world's world_bills) and ``ledger`` (its sibling — paychecks IN and
non-bill purchases OUT, /api/ledger/*, sharing the same pane) and ``calendar``
(the 🗓️ month view over all three — /api/calendar/* — plus the iCalendar export
and the token-guarded /api/public/calendar.ics subscription feed for Nextcloud)
and ``budget`` (the 🧮 AI budget + grocery planner on top of them — /api/budget/*:
item-level purchase lines, the consumption model, pay-period envelopes and
safe-to-spend, the calendar's budget/restock markers, and the approval-gated LLM
grocery plan). Importing the submodules runs their ``@router.*``
decorators, registering every route on the single shared router.

Import order matters in one place: ``ledger`` imports the line-item helpers from
``budget``, and ``budget`` imports the cycle math from ``bills``, so ``bills``
must be imported before ``ledger``. ``budget`` reaches ``calendar`` lazily (inside
a function) and ``calendar`` reaches ``budget`` the same way, which keeps that
pair free of an import cycle in both directions.
"""
from ._base import router                              # shared router + one-time _ensure_schema()
from . import signals, intel, missions, auto, bills, ledger, calendar, budget, income_sources   # noqa: F401  (import registers their @router routes)

# Re-exports so external callers keep resolving names against this module exactly
# as they did against the old single-file module:
#   main.py    -> money.start_auto()
#   prompts.py -> MONEY_GAP_REVIEW_PROMPT, LEAD_HUNT_PROMPT
#                 (lazy refs: ("routers.money", "MONEY_GAP_REVIEW_PROMPT"), ("routers.money", "LEAD_HUNT_PROMPT"))
from .auto import start_auto  # noqa: F401
from .intel import MONEY_GAP_REVIEW_PROMPT, LEAD_HUNT_PROMPT  # noqa: F401

__all__ = ["router", "start_auto", "MONEY_GAP_REVIEW_PROMPT", "LEAD_HUNT_PROMPT"]

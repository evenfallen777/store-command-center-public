"""
The Company — God Console HTTP surface.

Thin endpoints over world_ops (budget ledger, prayers approval queue, community
messages, PayPal config). All under /api/world/ops/*.

This module is a package: the shared ``router``, the media→URL helpers and the
import-time executor registration live in ``_base``; the routes are split across
``budget`` (treasury/config), ``prayers`` (approval queue + gate toggles),
``community`` (messages + PayPal + board), ``control`` (automation + control plane
+ workboard) and ``republic`` (strategy engine + bible). Importing the submodules
runs their ``@router.*`` decorators, registering every route on the shared router.
"""
from ._base import router, _designs_url, _exec_paypal_payout   # shared router + executor registration side effect
# ``_designs_url`` and ``_exec_paypal_payout`` re-exported so external callers keep
# resolving ``routers.world_ops._designs_url`` / ``._exec_paypal_payout`` exactly as
# they did against the old single-file module (tests/test_board_url.py and
# tests/test_money_gates.py depend on them).
from . import budget, prayers, community, control, republic, caps, jesus, satan  # noqa: F401  (import registers @router routes)

__all__ = ["router", "_designs_url", "_exec_paypal_payout"]

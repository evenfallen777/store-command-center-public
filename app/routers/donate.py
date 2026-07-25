"""
Donations — real-money rail #3 (receive-only, link-only). Surfaced as a small
"☕ Support this project" card in Finance → Missions & Earn.

Unlike Cash App / PayPal, a donate LINK moves no money and needs no world_ops
prayer/gate: it's just a URL the visitor clicks, same shape as the GitHub repo's
FUNDING.yml Sponsor button. Only a payout of what's collected would ever need a
gate, and that's out of scope here (Buy Me a Coffee pays out to the creator's own
BMC/Stripe account, not through this app).

Toggle-controlled and DEFAULT OFF (`donate_enabled` = "0"), per the "every gate
gets a toggle" rule — a fresh public clone must never ship someone else's donate
handle. The handle (`donate_bmc_user`) is a public Buy Me a Coffee username, not a
credential, so it's stored as a plain setting (not in crypto.SECRET_KEYS) like
other public config (e.g. cashapp_cashtag).
"""
from fastapi import APIRouter

from deps import get_setting

router = APIRouter()


@router.get("/api/donate/config")
def donate_config():
    """Everything the Money tab's donate card (and any future surface) needs in
    one call. No network, no secrets — safe to call unauthenticated-shaped."""
    enabled = (get_setting("donate_enabled", "0") or "0").strip().lower() in ("1", "true", "on", "yes")
    handle = (get_setting("donate_bmc_user", "") or "").strip()
    url = f"https://buymeacoffee.com/{handle}" if (enabled and handle) else ""
    return {"enabled": enabled, "url": url}

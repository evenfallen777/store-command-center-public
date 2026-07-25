"""Cloudflare — zone cache purge for example.com (Settings → Integrations).

Minimal on purpose: the one thing the owner actually wants day-to-day is "flush the
edge cache after a WordPress/product change." Credentials (`cf_api_token`,
`cf_account_id`) are plain settings-table rows — the token is encrypted at rest via
crypto.SECRET_KEYS (see app/crypto.py); the account id doesn't gate anything here
today but is stored for future account-scoped calls.

Zone id resolution: `cf_zone_id` wins if already set (cached from a prior purge or
set by hand); otherwise it's looked up by zone name (`cf_zone_name`, default
'example.com') via GET /zones and cached back into the setting so the lookup only
happens once. Nothing here ever returns a secret value — status only reports
booleans.
"""
import logging

import httpx
from fastapi import APIRouter, Body, HTTPException

from deps import get_conn, get_setting, _enc, _is_secret

router = APIRouter()
logger = logging.getLogger("store")

CF_API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_ZONE_NAME = "example.com"


def _zone_name() -> str:
    return (get_setting("cf_zone_name", "") or "").strip() or DEFAULT_ZONE_NAME


def _save_setting(key: str, value: str):
    conn = get_conn()
    try:
        val = _enc(str(value)) if _is_secret(key) else str(value)
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, val))
        conn.commit()
    finally:
        conn.close()


@router.get("/api/cloudflare/status")
def cloudflare_status():
    """{configured, zone, zone_id, zone_id_known} — NEVER the token itself, only
    whether it is set, which zone purges target, and the (non-secret) zone id so
    the Settings UI can prefill its field. Zone ids identify a zone but grant no
    access without a token, so returning the value is safe."""
    token = (get_setting("cf_api_token", "") or "").strip()
    zone_id = (get_setting("cf_zone_id", "") or "").strip()
    return {
        "configured": bool(token),
        "zone": _zone_name(),
        "zone_id": zone_id,
        "zone_id_known": bool(zone_id),
    }


def _resolve_zone_id(token: str) -> str:
    """cf_zone_id setting wins if already set; else resolve by zone name via the
    Cloudflare API and cache the result so future purges skip the lookup."""
    zone_id = (get_setting("cf_zone_id", "") or "").strip()
    if zone_id:
        return zone_id
    zone_name = _zone_name()
    try:
        r = httpx.get(f"{CF_API_BASE}/zones", params={"name": zone_name},
                       headers={"Authorization": f"Bearer {token}"}, timeout=15)
        data = r.json()
    except Exception as e:
        raise HTTPException(502, f"Couldn't reach Cloudflare to resolve the zone id: {e}")
    if not data.get("success", False):
        raise HTTPException(502, f"Cloudflare zone lookup failed: {data.get('errors')}")
    results = data.get("result") or []
    if not results:
        raise HTTPException(404, f"No Cloudflare zone named '{zone_name}' found for this token.")
    zone_id = (results[0] or {}).get("id") or ""
    if not zone_id:
        raise HTTPException(502, "Cloudflare zone lookup returned no zone id.")
    _save_setting("cf_zone_id", zone_id)
    return zone_id


@router.post("/api/cloudflare/purge")
def cloudflare_purge(data: dict = Body(None)):
    """Purge the edge cache for the configured zone (default example.com).

    Owner-triggered ONLY — nothing in the app calls this automatically; it fires
    on an explicit click in Settings → Integrations (or a deliberate API call).

    Body is optional. Empty/no body → purge EVERYTHING. To purge specific URLs
    instead, POST {"files": ["https://example.com/page", ...]} (full URLs,
    Cloudflare caps a single call at 30). Resolves + caches the zone id on first
    use. Returns {ok, result, mode} on success or {ok:false, errors} for a
    Cloudflare-reported failure."""
    token = (get_setting("cf_api_token", "") or "").strip()
    if not token:
        raise HTTPException(400, "Cloudflare token not set — add it in Settings → Integrations → Cloudflare → Configure")
    files = []
    if isinstance(data, dict) and data.get("files"):
        raw = data["files"]
        if not isinstance(raw, list):
            raise HTTPException(400, "'files' must be a list of full URLs")
        files = [str(u).strip() for u in raw if str(u).strip()]
        if any(not u.startswith(("http://", "https://")) for u in files):
            raise HTTPException(400, "Each purge URL must be a full http(s):// URL")
        if len(files) > 30:
            raise HTTPException(400, "Cloudflare allows at most 30 URLs per purge call")
    zone_id = _resolve_zone_id(token)
    payload = {"files": files} if files else {"purge_everything": True}
    try:
        r = httpx.post(f"{CF_API_BASE}/zones/{zone_id}/purge_cache",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        json=payload, timeout=20)
        data = r.json()
    except Exception as e:
        raise HTTPException(502, f"Cloudflare purge request failed: {e}")
    if not data.get("success", False):
        return {"ok": False, "errors": data.get("errors") or ["unknown Cloudflare error"]}
    logger.info("Cloudflare cache purged (%s) for zone %s",
                f"{len(files)} URLs" if files else "everything", zone_id)
    return {"ok": True, "result": data.get("result") or {}, "mode": "files" if files else "everything"}

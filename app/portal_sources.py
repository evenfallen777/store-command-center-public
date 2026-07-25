"""Portal source adapters — fetch curate-ready items from every external source.

These `_*_items` functions each return a list of unified item dicts (shape below)
for one source; portal.py imports them and wires them into the `/api/portal/items`
aggregator. Kept separate from the router so the (large) fetch/normalize logic lives
apart from the endpoint/push code.

item shape: {uid, source, kind, title, description, price, external_url,
             image_url, local_path, category, tags}
"""
import httpx
from fastapi import HTTPException

from deps import *   # get_conn, _get_etsy_settings, _get_printify, _enc, config (etc.)


def _strip(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html or "").strip()[:500]


def _apply_program_tag(url: str, program_id) -> str:
    """Append the saved affiliate tag for `program_id` to `url` if the program uses a
    simple query-param tag (e.g. Amazon ?tag=) and the URL doesn't already carry it.
    Networks with deep-links (tag_param="") are returned unchanged — the user pastes
    the finished deep-link themselves."""
    if not program_id or not url:
        return url
    conn = get_conn()
    row = conn.execute("SELECT tag_param, tag_value FROM portal_programs WHERE id=?", (program_id,)).fetchone()
    conn.close()
    if not row:
        return url
    param = (row["tag_param"] or "").strip()
    val = (row["tag_value"] or "").strip()
    if not param or not val:
        return url
    if f"{param}=" in url:           # already tagged — don't double-append
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{param}={val}"


def _etsy_valid_token(s: dict) -> str:
    """Return a fresh Etsy access token, refreshing + persisting it if expired/expiring
    (same pattern as dashboard.py). Etsy tokens live ~1h, so this is routine."""
    import time
    from etsy_client import refresh_access_token, EtsyAuthError, auth_needs_reconnect
    key = s.get("etsy_key", ""); token = s.get("etsy_access_token", "")
    ref = s.get("etsy_refresh_token", ""); secret = s.get("etsy_shared_secret", "")
    exp = int(s.get("etsy_token_expires", "0") or 0)
    if token and ref and time.time() >= exp - 120:
        # Stale OAuth → one clean 409 the UI understands, not a raw httpx 400.
        # (Logged once by etsy_client.mark_auth_stale; known-dead tokens skip the call.)
        if auth_needs_reconnect():
            raise HTTPException(409, "Etsy authorization expired — reconnect Etsy in Settings.")
        try:
            tokens = refresh_access_token(key, ref, client_secret=secret or None)
        except EtsyAuthError:
            raise HTTPException(409, "Etsy authorization expired — reconnect Etsy in Settings.")
        token = tokens["access_token"]
        new_exp = int(time.time()) + tokens.get("expires_in", 3600)
        c = get_conn()
        c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_access_token", _enc(token)))
        c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_token_expires", str(new_exp)))
        if tokens.get("refresh_token"):
            c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_refresh_token", _enc(tokens["refresh_token"])))
        c.commit(); c.close()
    return token


def _etsy_items() -> list:
    s = _get_etsy_settings()
    key = s.get("etsy_key"); shop = s.get("etsy_shop_id"); secret = s.get("etsy_shared_secret", "")
    if not (key and s.get("etsy_access_token") and shop):
        raise HTTPException(400, "Etsy not connected — add credentials in Settings.")
    token = _etsy_valid_token(s)
    # Use EtsyClient's headers (x-api-key must be 'keystring:shared_secret', else 403),
    # with includes=Images so listing thumbnails come back inline.
    from etsy_client import EtsyClient, ETSY_BASE
    client = EtsyClient(key, token, shop, shared_secret=secret)
    r = httpx.get(
        f"{ETSY_BASE}/application/shops/{shop}/listings",
        params={"state": "active", "limit": 100, "includes": "Images"},
        headers=client._headers(), timeout=30)
    r.raise_for_status()
    out = []
    for L in r.json().get("results", []):
        price = L.get("price") or {}
        amt = price.get("amount"); div = price.get("divisor") or 100
        dollars = f"{amt/div:.2f}" if isinstance(amt, (int, float)) else ""
        imgs = L.get("images") or []
        img = imgs[0].get("url_570xN") or imgs[0].get("url_fullxfull") if imgs else ""
        out.append({
            "uid": str(L.get("listing_id")), "source": "etsy", "kind": "product",
            "title": L.get("title", ""), "description": _strip(L.get("description", "")),
            "price": dollars, "external_url": L.get("url", ""),
            "image_url": img, "local_path": None,
            "category": "Etsy", "tags": ",".join(L.get("tags", []) or []),
        })
    return out


def _printify_items() -> list:
    client = _get_printify()
    data = client.get_products(limit=50)
    out = []
    for p in data.get("data", []):
        variants = p.get("variants") or []
        prices = [v.get("price") for v in variants if v.get("price")]
        dollars = f"{min(prices)/100:.2f}" if prices else ""
        imgs = p.get("images") or []
        img = imgs[0].get("src") if imgs else ""
        ext = (p.get("external") or {}).get("handle", "")   # set once published to a sales channel
        out.append({
            "uid": str(p.get("id")), "source": "printify", "kind": "product",
            "title": p.get("title", ""), "description": _strip(p.get("description", "")),
            "price": dollars, "external_url": ext, "image_url": img, "local_path": None,
            "category": "Printify", "tags": ",".join(p.get("tags", []) or []),
        })
    return out


def _cults3d_items() -> list:
    from cults import cults_query, CultsError
    try:
        data = cults_query("""{ me { creations(limit: 100) {
            name url price { cents currency } illustrationImageUrl } } }""")
    except CultsError as e:
        raise HTTPException(502, f"Cults3D: {e}")
    me = data.get("me") or {}
    out = []
    for c in me.get("creations") or []:
        price = c.get("price") or {}
        cents = price.get("cents")
        dollars = f"{cents/100:.2f}" if isinstance(cents, (int, float)) else ""
        out.append({
            "uid": c.get("url", ""), "source": "cults3d", "kind": "product",
            "title": c.get("name", ""), "description": "",
            "price": dollars, "external_url": c.get("url", ""),
            "image_url": c.get("illustrationImageUrl", ""), "local_path": None,
            "category": "3D Models", "tags": "",
        })
    return out


def _affiliate_items(kind: str) -> list:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM portal_affiliate WHERE kind=? ORDER BY updated_at DESC", (kind,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        raw_url = d.get("external_url") or ""
        tagged = _apply_program_tag(raw_url, d.get("program_id"))
        out.append({
            "uid": str(d["id"]), "source": kind, "kind": "product",
            "title": d["title"], "description": d.get("description") or "",
            "price": d.get("price") or "", "external_url": tagged,
            "image_url": d.get("image_url") or "", "local_path": None,
            "category": d.get("category") or ("Software" if kind == "software" else "Affiliate"),
            "tags": d.get("tags") or "", "button_text": d.get("button_text") or "Buy now",
            "program_id": d.get("program_id"),
        })
    return out


def _image_items() -> list:
    """Generated images (designs that have a produced image) → portfolio previews."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, image_path, prompt, status FROM designs "
        "WHERE image_path IS NOT NULL AND image_path != '' "
        "ORDER BY created_at DESC LIMIT 200").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        parts = str(d["image_path"]).split("/")
        fn = parts[-1]
        sub = parts[-2] if len(parts) > 1 else "approved"
        out.append({
            "uid": str(d["id"]), "source": "image", "kind": "portfolio",
            "title": (d.get("prompt") or fn)[:80], "description": d.get("prompt") or "",
            "price": "", "external_url": "",
            "image_url": f"/designs/{sub}/{fn}",   # frontend prefixes API base
            "local_path": d["image_path"], "category": "Gallery", "tags": "",
        })
    return out


def _video_items() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, video_path, prompt, status FROM videos "
        "WHERE status='done' AND video_path IS NOT NULL AND video_path != '' "
        "ORDER BY created_at DESC LIMIT 200").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        fn = str(d["video_path"]).split("/")[-1]
        out.append({
            "uid": str(d["id"]), "source": "video", "kind": "portfolio",
            "title": (d.get("prompt") or fn)[:80], "description": d.get("prompt") or "",
            "price": "", "external_url": "",
            "image_url": f"/videos/{fn}", "local_path": d["video_path"],
            "category": "Gallery", "tags": "",
        })
    return out

"""etsy routes."""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Form, UploadFile, File
from deps import *
from services import *

router = APIRouter()


class UpdateListingRequest(BaseModel):
    title:       Optional[str]   = None
    description: Optional[str]   = None
    tags:        Optional[str]   = None  # comma-separated
    price:       Optional[float] = None

@router.patch("/api/etsy/listings/{listing_id}")
def update_etsy_listing(listing_id: int, req: UpdateListingRequest):
    """Update an Etsy listing's title, description, tags, or price."""
    s = _get_etsy_settings()
    ek      = s.get("etsy_key", "")
    etok    = s.get("etsy_access_token", "")
    eid     = s.get("etsy_shop_id", "")
    esecret = s.get("etsy_shared_secret", "")
    if not ek or not etok or not eid:
        raise HTTPException(400, "Etsy not configured")
    tag_list = [t.strip() for t in (req.tags or "").split(",") if t.strip()] if req.tags else None
    from etsy_client import EtsyClient
    result = EtsyClient(ek, etok, eid, shared_secret=esecret).update_listing(
        listing_id,
        title=req.title,
        description=req.description,
        tags=tag_list,
        price=req.price,
    )
    return {"ok": True, "listing": result}

@router.get("/api/etsy/status")
def etsy_status():
    import etsy_client as _ec
    s = _get_etsy_settings()
    auth = _ec.auth_state()
    return {
        "has_key":   bool(s.get("etsy_key")),
        "connected": bool(s.get("etsy_access_token")),
        "has_shop":  bool(s.get("etsy_shop_id")),
        "shop_id":   s.get("etsy_shop_id", ""),
        # Stale OAuth (refresh rejected with 400/401): the UI shows a
        # "Reconnect Etsy" CTA that re-runs GET /api/etsy/connect.
        "needs_reconnect": bool(s.get("etsy_access_token")) and auth["needs_reconnect"],
        "reconnect_reason": auth["reason"],
    }

@router.get("/api/etsy/connect")
def etsy_connect_start():
    """Start the OAuth2 PKCE flow — returns the Etsy authorization URL."""
    s   = _get_etsy_settings()
    key = s.get("etsy_key", "")
    if not key:
        raise HTTPException(400, "Set your Etsy API key in Settings first")
    verifier, challenge = generate_pkce()
    state = _secrets.token_urlsafe(16)
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_pkce_verifier", verifier))
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_pkce_state", state))
    conn.commit()
    conn.close()
    url = build_auth_url(key, state, challenge)
    return {"url": url}

@router.get("/api/etsy/callback")
def etsy_callback(code: str = None, state: str = None, error: str = None):
    """Etsy OAuth2 redirect target — exchanges code for tokens."""
    from fastapi.responses import HTMLResponse
    if error:
        return HTMLResponse(f"<h2 style='font-family:sans-serif'>Etsy auth failed: {error}</h2><p>Close this tab and try again.</p>")
    s            = _get_etsy_settings()
    stored_state = s.get("etsy_pkce_state", "")
    verifier     = s.get("etsy_pkce_verifier", "")
    key          = s.get("etsy_key", "")
    if not code or state != stored_state:
        return HTMLResponse("<h2 style='font-family:sans-serif'>Invalid OAuth state</h2><p>Try connecting again from Settings.</p>")
    secret = s.get("etsy_shared_secret", "")
    try:
        tokens     = exchange_code(key, code, verifier, client_secret=secret or None)
        expires_at = int(time.time()) + tokens.get("expires_in", 3600)
        conn = get_conn()
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_access_token",  _enc(tokens["access_token"])))
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_refresh_token", _enc(tokens["refresh_token"])))
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_token_expires", str(expires_at)))
        conn.execute("DELETE FROM settings WHERE key IN ('etsy_pkce_verifier','etsy_pkce_state')")
        conn.commit()
        conn.close()
        import etsy_client as _ec
        _ec.mark_auth_ok()   # fresh OAuth — clear any reconnect-needed state
        return HTMLResponse("""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#111;color:#eee">
        <h2>✅ Etsy Connected!</h2>
        <p>You can close this tab and return to the Store Command Center.</p>
        <script>if(window.opener){window.opener.postMessage('etsy_connected','*');setTimeout(()=>window.close(),1500);}</script>
        </body></html>""")
    except Exception as e:
        logger.error("Etsy token exchange failed: %s", e)
        return HTMLResponse(f"<h2 style='font-family:sans-serif'>Token exchange failed</h2><pre>{e}</pre>")

@router.delete("/api/etsy/disconnect")
def etsy_disconnect():
    conn = get_conn()
    conn.execute("DELETE FROM settings WHERE key IN ('etsy_access_token','etsy_refresh_token','etsy_token_expires')")
    conn.commit()
    conn.close()
    import etsy_client as _ec
    _ec.mark_auth_ok()   # disconnected shows "Not connected", not "reconnect needed"
    return {"ok": True}

class EtsyPublishRequest(BaseModel):
    design_id:    int
    title:        str
    description:  Optional[str] = ""
    tags:         Optional[str]  = ""  # comma-separated string; converted to list internally
    price:        Optional[float] = 25.0
    product_type: Optional[str]  = "T-Shirt"

@router.post("/api/etsy/publish")
def publish_to_etsy(req: EtsyPublishRequest, background_tasks: BackgroundTasks):
    import etsy_client as _ec
    if _ec.auth_needs_reconnect():
        # Fail fast instead of queueing a background publish doomed to a raw 400.
        raise HTTPException(409, "Etsy authorization expired — reconnect Etsy in Settings.")
    conn   = get_conn()
    design = conn.execute("SELECT * FROM designs WHERE id=?", (req.design_id,)).fetchone()
    if not design:
        conn.close()
        raise HTTPException(404, "Design not found")
    if design["status"] not in ("approved", "published"):
        conn.close()
        raise HTTPException(400, "Design must be approved before publishing to Etsy")
    image_path   = design["image_path"]
    product_type = req.product_type or design["product_type"] or "T-Shirt"
    conn.close()
    background_tasks.add_task(
        _do_etsy_publish,
        req.design_id, req.title, req.description or req.title,
        req.tags, req.price or 25.0, product_type, image_path
    )
    return {"ok": True, "message": "Publishing to Etsy in background…"}

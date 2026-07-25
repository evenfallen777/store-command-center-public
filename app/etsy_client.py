"""
Etsy API v3 client — OAuth2 PKCE + listing management
"""
import hashlib, base64, os, secrets, time, logging
import urllib.parse
import httpx

_log = logging.getLogger("store.etsy")


class EtsyAuthError(Exception):
    """Etsy OAuth is stale — the token refresh was rejected (HTTP 400/401).
    Retrying without the user re-connecting can NEVER succeed, so callers must
    stop calling Etsy and surface a 'Reconnect Etsy' state instead of raw 400s."""


# ── Stale-auth state (shared, in-process) ─────────────────────────────────────
# One flag the whole app reads instead of each caller re-attempting the doomed
# refresh and spamming the log with raw 400s. Set on the first rejected refresh
# (logged ONCE), cleared when a refresh succeeds or the user re-runs OAuth.
_AUTH_STATE = {"needs_reconnect": False, "reason": "", "since": 0.0}


def mark_auth_stale(reason: str) -> None:
    """Flip to the quiet reconnect-needed state. Logs once per stale episode."""
    if not _AUTH_STATE["needs_reconnect"]:
        _log.warning(
            "Etsy auth is stale (%s) — pausing Etsy calls until reconnected "
            "(Settings → Reconnect Etsy). This is logged once.", str(reason)[:200])
        _AUTH_STATE["since"] = time.time()
    _AUTH_STATE["needs_reconnect"] = True
    _AUTH_STATE["reason"] = str(reason)[:200]


def mark_auth_ok() -> None:
    """Clear the reconnect-needed state (successful refresh or fresh OAuth)."""
    _AUTH_STATE.update({"needs_reconnect": False, "reason": "", "since": 0.0})


def auth_state() -> dict:
    """{'needs_reconnect': bool, 'reason': str, 'since': float} — for /api/etsy/status."""
    return dict(_AUTH_STATE)


def auth_needs_reconnect() -> bool:
    return bool(_AUTH_STATE["needs_reconnect"])

ETSY_BASE      = "https://openapi.etsy.com/v3"
ETSY_AUTH_URL  = "https://www.etsy.com/oauth/connect"
ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
try:
    from config import ETSY_REDIRECT_URI as REDIRECT_URI
except Exception:  # standalone / import-order fallback
    REDIRECT_URI = "http://localhost:8787/store/api/etsy/callback"
SCOPES         = "listings_w listings_r transactions_r email_r"

# Etsy taxonomy IDs for common product types
TAXONOMY_MAP = {
    "T-Shirt":           1022,
    "Hoodie":            1023,
    "Sweatshirt":        1023,
    "Tank Top":          1022,
    "Mug":               5245,
    "Tumbler":           5245,
    "Poster":            66,
    "Sticker":           66,
    "Tote Bag":          3254,
    "Phone Case":        3267,
    "Mouse Pad":         3200,
    "Pillow":            4171,
    "Hat":               2581,
    "Beanie":            2581,
    "Socks":             2578,
    "Men's Underwear":   2578,
    "Women's Underwear": 2578,
    "Bumper Sticker":    66,
}


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) pair."""
    verifier  = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b'=').decode()
    return verifier, challenge


def build_auth_url(keystring: str, state: str, code_challenge: str) -> str:
    params = urllib.parse.urlencode({
        "response_type":         "code",
        "client_id":             keystring,
        "redirect_uri":          REDIRECT_URI,
        "scope":                 SCOPES,
        "state":                 state,
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
    })
    return f"{ETSY_AUTH_URL}?{params}"


def exchange_code(keystring: str, code: str, code_verifier: str, client_secret: str = None) -> dict:
    """Exchange auth code for access + refresh tokens."""
    payload = {
        "grant_type":    "authorization_code",
        "client_id":     keystring,
        "redirect_uri":  REDIRECT_URI,
        "code":          code,
        "code_verifier": code_verifier,
    }
    if client_secret:
        payload["client_secret"] = client_secret
    r = httpx.post(ETSY_TOKEN_URL, data=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def refresh_access_token(keystring: str, refresh_token: str, client_secret: str = None) -> dict:
    """Refresh an expired access token. A 400/401 from the token endpoint means the
    refresh token itself is dead (revoked/expired) — that raises EtsyAuthError and
    flips the shared reconnect-needed flag; retrying is pointless until the user
    re-runs OAuth. Success clears the flag."""
    payload = {
        "grant_type":    "refresh_token",
        "client_id":     keystring,
        "refresh_token": refresh_token,
    }
    if client_secret:
        payload["client_secret"] = client_secret
    r = httpx.post(ETSY_TOKEN_URL, data=payload, timeout=20)
    if r.status_code in (400, 401):
        reason = f"token refresh rejected (HTTP {r.status_code})"
        mark_auth_stale(reason)
        raise EtsyAuthError(f"Etsy {reason} — reconnect Etsy in Settings.")
    r.raise_for_status()
    mark_auth_ok()
    return r.json()


# ── Client ────────────────────────────────────────────────────────────────────

class EtsyClient:
    def __init__(self, keystring: str, access_token: str, shop_id: str, shared_secret: str = ""):
        self.keystring     = keystring
        self.shared_secret = shared_secret
        self.access_token  = access_token
        self.shop_id       = str(shop_id)

    def _api_key(self) -> str:
        """Return x-api-key value: 'keystring:shared_secret' when secret is available."""
        if self.shared_secret:
            return f"{self.keystring}:{self.shared_secret}"
        return self.keystring

    def _headers(self, json_content=True) -> dict:
        h = {
            "x-api-key":     self._api_key(),
            "Authorization": f"Bearer {self.access_token}",
        }
        if json_content:
            h["Content-Type"] = "application/json"
        return h

    def get_shop(self) -> dict:
        r = httpx.get(
            f"{ETSY_BASE}/application/shops/{self.shop_id}",
            headers=self._headers(), timeout=15
        )
        r.raise_for_status()
        return r.json()

    def get_me(self) -> dict:
        r = httpx.get(f"{ETSY_BASE}/application/users/me",
                      headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def get_listings(self, state: str = "active", limit: int = 100) -> list:
        """Fetch listings with a specific state (e.g., 'active', 'draft', 'sold')."""
        r = httpx.get(
            f"{ETSY_BASE}/application/shops/{self.shop_id}/listings?state={state}&limit={limit}",
            headers=self._headers(),
            timeout=30
        )
        r.raise_for_status()
        data = r.json()
        return data.get("results", [])

    def create_draft_listing(
        self,
        title: str,
        description: str,
        price_usd: float,
        tags: list,
        product_type: str = "T-Shirt",
        quantity: int = 999,
    ) -> dict:
        taxonomy_id = TAXONOMY_MAP.get(product_type, 1022)
        # Etsy: max 13 tags, each max 20 chars
        safe_tags = [t.strip()[:20] for t in tags if t.strip()][:13]
        body = {
            "quantity":    quantity,
            "title":       title[:140],
            "description": description or title,
            "price":       round(price_usd, 2),
            "who_made":    "i_did",
            "when_made":   "made_to_order",
            "taxonomy_id": taxonomy_id,
            "tags":        safe_tags,
            "is_supply":   False,
            "type":        "physical",
            "state":       "draft",   # draft first so image can be attached
        }
        r = httpx.post(
            f"{ETSY_BASE}/application/shops/{self.shop_id}/listings",
            headers=self._headers(),
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def upload_listing_image(self, listing_id: int, image_path: str) -> dict:
        import mimetypes
        mime = mimetypes.guess_type(image_path)[0] or "image/png"
        with open(image_path, "rb") as f:
            r = httpx.post(
                f"{ETSY_BASE}/application/shops/{self.shop_id}/listings/{listing_id}/images",
                headers=self._headers(json_content=False),
                files={"image": (os.path.basename(image_path), f, mime)},
                timeout=60,
            )
        r.raise_for_status()
        return r.json()

    def get_listing(self, listing_id: int) -> dict:
        r = httpx.get(
            f"{ETSY_BASE}/application/listings/{listing_id}",
            headers=self._headers(), timeout=15
        )
        r.raise_for_status()
        return r.json()

    def get_receipts(self, min_created: int = 0, limit: int = 100) -> list:
        """Real orders (customer payments) as Etsy receipts — the revenue source.
        Each receipt carries grandtotal {amount, divisor, currency_code}. `min_created`
        is a unix timestamp; only receipts created after it are returned."""
        params = {"limit": limit, "sort_on": "created", "sort_order": "asc"}
        if min_created:
            params["min_created"] = int(min_created)
        r = httpx.get(
            f"{ETSY_BASE}/application/shops/{self.shop_id}/receipts",
            headers=self._headers(), params=params, timeout=25,
        )
        r.raise_for_status()
        out = []
        for rc in r.json().get("results", []):
            gt = rc.get("grandtotal") or {}
            amt, div = gt.get("amount", 0), (gt.get("divisor") or 100)
            out.append({
                "receipt_id": rc.get("receipt_id"),
                "created": rc.get("created_timestamp") or rc.get("create_timestamp") or 0,
                "total_cents": int(round((amt / div) * 100)) if div else 0,
                "currency": gt.get("currency_code", "USD"),
                "buyer": rc.get("name") or "",
            })
        return out

    def update_listing(self, listing_id: int, title: str = None, description: str = None,
                       tags: list = None, price: float = None, state: str = None) -> dict:
        """Update an existing listing's title, description, tags, price, or state
        (e.g. state='active' to take a draft live)."""
        body = {}
        if title is not None:       body["title"] = title[:140]
        if description is not None: body["description"] = description
        if tags is not None:        body["tags"] = [t.strip()[:20] for t in tags][:13]
        if state is not None:       body["state"] = state
        if price is not None:
            if isinstance(price, float):
                body["price"] = round(price, 2)
            else:
                body["price"] = price
        r = httpx.patch(
            f"{ETSY_BASE}/application/shops/{self.shop_id}/listings/{listing_id}",
            headers=self._headers(),
            json=body,
            timeout=30
        )
        r.raise_for_status()
        return r.json()

    def get_shop_stats(self) -> dict:
        """Return shop-level stats: sales, reviews, listing views/favorites."""
        shop = self.get_shop()
        listings_r = httpx.get(
            f"{ETSY_BASE}/application/shops/{self.shop_id}/listings",
            headers=self._headers(),
            params={"state": "active", "limit": 100},
            timeout=20,
        )
        listings_r.raise_for_status()
        data      = listings_r.json()
        items     = data.get("results", [])
        top       = sorted(items, key=lambda x: x.get("views", 0), reverse=True)[:10]
        return {
            "shop_name":             shop.get("shop_name", ""),
            "transaction_sold_count": shop.get("transaction_sold_count", 0),
            "review_average":        shop.get("review_average", 0),
            "review_count":          shop.get("review_count", 0),
            "listing_count":         data.get("count", len(items)),
            "total_views":           sum(l.get("views", 0) for l in items),
            "total_favorites":       sum(l.get("num_favorers", 0) for l in items),
            "top_listings":          top,
        }

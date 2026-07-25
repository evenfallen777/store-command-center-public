"""Shared Cults3D GraphQL client — credentials, queries, and the publish mutation.

Auth is HTTP Basic (username + generated API key). The write path uses the
`createCreation` mutation, which pulls assets from PUBLIC https URLs you pass in
`imageUrls` / `fileUrls` (max 10 each) — the app serves those from a token-guarded
route (see routers/models3d.py). Rate limits ~60/30s, ~500/day.
"""
import base64
import httpx
from db import get_conn
from crypto import dec_secrets

CULTS_GRAPHQL = "https://cults3d.com/graphql"


class CultsError(Exception):
    """Readable Cults3D API/credential failure. `status` is the HTTP status the API
    router should return — 4xx for config/auth (your fault), 502 for upstream."""
    def __init__(self, msg: str, status: int = 502):
        super().__init__(msg)
        self.status = status


def cults_creds() -> tuple[str, str]:
    conn = get_conn()
    rows = conn.execute("SELECT key,value FROM settings WHERE key LIKE 'cults3d%'").fetchall()
    conn.close()
    s = dec_secrets({r["key"]: r["value"] for r in rows})
    user = (s.get("cults3d_username") or "").strip()
    key = (s.get("cults3d_api_key") or "").strip()
    if not user or not key:
        raise CultsError("Set your Cults3D username and API key first (Cults3D tab)", status=400)
    return user, key


def cults_query(query: str, variables: dict = None) -> dict:
    user, key = cults_creds()
    auth = base64.b64encode(f"{user}:{key}".encode()).decode()
    try:
        r = httpx.post(CULTS_GRAPHQL,
                       json={"query": query, "variables": variables or {}},
                       headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
                       timeout=45)
    except Exception as e:
        raise CultsError(f"Could not reach Cults3D: {e}")
    if r.status_code == 401:
        hint = ""
        if len(key) < 20:
            hint = (f" Your key is only {len(key)} characters — that looks like a password, not "
                    "an API key. API keys are long tokens.")
        raise CultsError("Cults3D rejected your credentials. Generate an API key at "
                         "cults3d.com/en/api (log in with Google → API → create key)." + hint, status=401)
    if r.status_code == 429:
        raise CultsError("Cults3D rate limit hit (max ~60 req / 30s). Wait a moment and retry.", status=429)
    if r.status_code >= 400:
        raise CultsError(f"Cults3D API error {r.status_code}: {r.text[:200]}")
    data = r.json()
    if data.get("errors"):
        raise CultsError("Cults3D GraphQL error: " +
                         "; ".join(e.get("message", "?") for e in data["errors"])[:300])
    return data.get("data", {})


# The publish mutation. Argument names verified against the Cults3D API docs
# (createCreation). Optional args are omitted when their variable is null.
_CREATE_CREATION = """
mutation CreateCreation(
  $name: String!, $description: String!,
  $imageUrls: [String!]!, $fileUrls: [String!]!,
  $locale: LocaleEnum!, $price: Float, $currency: Currency,
  $tagNames: [String!], $license: CreationLicense, $madeWithAi: Boolean
) {
  createCreation(
    name: $name, description: $description,
    imageUrls: $imageUrls, fileUrls: $fileUrls,
    locale: $locale, downloadPrice: $price, currency: $currency,
    tagNames: $tagNames, licenseCode: $license, madeWithAi: $madeWithAi
  ) {
    creation { id url(locale: $locale) }
    errors
  }
}
"""


def create_creation(*, name: str, description: str, image_urls: list[str],
                    file_urls: list[str], locale: str = "en",
                    price: float = 0.0, currency: str = "USD",
                    tag_names: list[str] = None, license_code: str = "standard",
                    made_with_ai: bool = False) -> dict:
    """Publish a creation. Returns {'id', 'url'} on success; raises CultsError."""
    if not file_urls:
        raise CultsError("At least one public file URL is required to publish")
    if not image_urls:
        raise CultsError("At least one public image URL is required to publish")
    variables = {
        "name": name[:150],
        "description": description or name,
        "imageUrls": image_urls[:10],
        "fileUrls": file_urls[:10],
        "locale": locale,
        "price": float(price or 0),
        "currency": currency,
        "tagNames": [t for t in (tag_names or []) if t][:20],
        "license": license_code,
        "madeWithAi": bool(made_with_ai),
    }
    data = cults_query(_CREATE_CREATION, variables)
    result = data.get("createCreation") or {}
    errs = result.get("errors")
    if errs:
        raise CultsError("Cults3D rejected the listing: " + "; ".join(str(e) for e in errs)[:300])
    creation = result.get("creation") or {}
    if not creation.get("id"):
        raise CultsError("Cults3D did not return a creation id — publish may have failed")
    return {"id": creation["id"], "url": creation.get("url")}

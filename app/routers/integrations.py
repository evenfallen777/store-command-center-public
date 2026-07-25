"""Integrations status board — GET /api/integrations/status

One place to see every external service/login the Store can use and whether it's
set up, so the owner isn't hunting through six tabs to remember what's connected.
For each service we report ONLY whether its settings-table keys are populated
(plus a couple of live-ish reachability flags) — NEVER the key values themselves.

Status is derived purely from `get_setting()` (+ the odd cheap live check like
`gh auth status` or the cached node ping) so this endpoint is safe to poll and
never touches a paid/external API. Defensive throughout: a single integration's
check blowing up degrades that ONE row to "not_setup" rather than 500ing the
whole board.
"""
import subprocess
import time as _time

from fastapi import APIRouter

from deps import *   # get_setting, GPU_HOST, GH_BIN, LMSTUDIO_URL

router = APIRouter()


def _has(key: str) -> bool:
    """True if a settings-table key is set to a non-empty value. Never returns
    or logs the value itself."""
    try:
        v = get_setting(key, "")
        return bool(v and str(v).strip())
    except Exception:
        return False


def _val(key: str) -> str:
    """Raw value for keys that are NOT secrets (e.g. a store's own public wp_url) —
    only ever call this for non-secret keys, and only to build a setup link."""
    try:
        return str(get_setting(key, "") or "")
    except Exception:
        return ""


def _svc(key_id: str, name: str, category: str, required: list, setup_url,
         in_app: bool, expired: bool = False,
         note_active: str = None, note_partial: str = None, note_none: str = None) -> dict:
    """Build one descriptor from a list of required settings keys.
    all required set (and not expired)  -> active
    some required set, or expired       -> needs_login
    none required set                   -> not_setup
    """
    try:
        have = [k for k in required if _has(k)]
        n, total = len(have), max(len(required), 1)
        if have and n == total and not expired:
            status = "active"
            detail = note_active or "Connected — all required keys are set."
        elif expired and n == total:
            status = "needs_login"
            detail = note_partial or "Token expired — reconnect."
        elif have:
            status = "needs_login"
            detail = note_partial or f"Partially set up ({n}/{total} keys) — finish setup."
        else:
            status = "not_setup"
            detail = note_none or "Not set up yet."
        return {"key_id": key_id, "name": name, "category": category, "status": status,
                "detail": detail, "setup_url": setup_url, "in_app": in_app}
    except Exception:
        return {"key_id": key_id, "name": name, "category": category, "status": "not_setup",
                "detail": "Status check failed.", "setup_url": setup_url, "in_app": in_app}


def _etsy() -> dict:
    try:
        required = ["etsy_key", "etsy_access_token", "etsy_shop_id"]
        exp = 0
        try:
            exp = int(get_setting("etsy_token_expires", "0") or 0)
        except Exception:
            exp = 0
        expired = bool(exp) and exp < _time.time()
        d = _svc("etsy", "Etsy", "Commerce", required,
                 "https://www.etsy.com/developers/your-apps", in_app=True, expired=expired,
                 note_active="Connected — draft listings can be created via the Etsy API.",
                 note_partial=("Access token expired — reconnect in Settings → Integrations."
                                if expired else None),
                 note_none="No Etsy API key/shop connected yet.")
        return d
    except Exception:
        return {"key_id": "etsy", "name": "Etsy", "category": "Commerce", "status": "not_setup",
                "detail": "Status check failed.", "setup_url": "https://www.etsy.com/developers/your-apps",
                "in_app": True}


def _github() -> dict:
    key_id, name, category = "github", "GitHub", "Infra & AI"
    setup_url = "https://github.com/settings/tokens"
    try:
        authed = False
        try:
            r = subprocess.run([GH_BIN, "auth", "status"], capture_output=True, text=True, timeout=3)
            authed = (r.returncode == 0)
        except Exception:
            authed = False
        token_set = _has("github_token")
        if authed:
            status, detail = "active", "gh CLI is authenticated on this box."
        elif token_set:
            status = "needs_login"
            detail = "A token is saved but the gh CLI isn't signed in — run gh auth login."
        else:
            status = "not_setup"
            detail = "Not signed in. Use gh auth login (Settings → System → GitHub), or a personal access token."
        return {"key_id": key_id, "name": name, "category": category, "status": status,
                "detail": detail, "setup_url": setup_url, "in_app": True}
    except Exception:
        return {"key_id": key_id, "name": name, "category": category, "status": "not_setup",
                "detail": "Status check failed.", "setup_url": setup_url, "in_app": True}


def _node_gpu() -> dict:
    key_id, name, category = "node_gpu", "Node / GPU", "Infra & AI"
    try:
        from routers import node as _node_router
        r = _node_router.node_ping()
        reachable = bool(r.get("reachable"))
        host = r.get("host") or GPU_HOST
    except Exception:
        reachable, host = False, GPU_HOST
    if reachable:
        status, detail = "active", f"GPU node reachable at {host}."
    else:
        status = "needs_login"
        detail = f"GPU node ({host}) not reachable — check it's powered on and SSH is up."
    return {"key_id": key_id, "name": name, "category": category, "status": status,
            "detail": detail, "setup_url": None, "in_app": True}


def _wordpress() -> dict:
    setup_url = "https://woocommerce.com/document/woocommerce-rest-api/"
    try:
        wp_url = _val("wp_url").rstrip("/")   # not a secret — the owner's own store URL
        if wp_url:
            setup_url = f"{wp_url}/wp-admin/admin.php?page=wc-settings&tab=advanced&section=keys"
    except Exception:
        pass
    return _svc("wp", "WordPress / WooCommerce", "Commerce",
                ["wp_url", "wp_consumer_key", "wp_consumer_secret"], setup_url, in_app=True,
                note_active="Connected — Portal can publish to WooCommerce.",
                note_none="No WordPress/Woo store connected yet.")


def _lmstudio() -> dict:
    """LM Studio's URL ships with a working default (points at the configured GPU
    node) even before the owner touches Settings, so we treat the LIVE effective
    URL as \"set\" rather than requiring a DB row — the API key is optional for a
    local LM Studio server."""
    try:
        url_set = bool((globals().get("LMSTUDIO_URL") or "").strip())
    except Exception:
        url_set = False
    key_set = _has("lmstudio_api_key")
    if url_set:
        status = "active"
        detail = "LM Studio URL configured" + (" (API key set)." if key_set else " (no API key — fine for a local server).")
    else:
        status = "not_setup"
        detail = "No LM Studio / LLM URL configured."
    return {"key_id": "lmstudio", "name": "LM Studio", "category": "Infra & AI", "status": status,
            "detail": detail, "setup_url": "https://lmstudio.ai/docs/local-server", "in_app": True}


def _cloudflare() -> dict:
    has = _has("cf_api_token") or _has("cloudflare_api_token")
    status = "active" if has else "not_setup"
    detail = "Cloudflare API token set." if has else "No Cloudflare API token set."
    return {"key_id": "cloudflare", "name": "Cloudflare", "category": "Infra & AI", "status": status,
            "detail": detail, "setup_url": "https://dash.cloudflare.com/profile/api-tokens", "in_app": False}


def _all_descriptors() -> list:
    out = []

    def add(fn):
        try:
            out.append(fn())
        except Exception:
            pass

    # ── Commerce ──
    add(_etsy)
    add(lambda: _svc("printify", "Printify", "Commerce", ["printify_key", "printify_shop_id"],
                      "https://printify.com/app/account/api", in_app=True,
                      note_active="Connected — designs can be pushed to Printify.",
                      note_none="No Printify API key/shop connected yet."))
    add(lambda: _svc("cults3d", "Cults3D", "Commerce", ["cults3d_api_key"],
                      "https://cults3d.com/en/api", in_app=True,
                      note_active="Connected — listings can be read/published on Cults3D.",
                      note_none="No Cults3D API key set."))
    add(lambda: _svc("paypal", "PayPal", "Commerce", ["world_paypal_client_id", "world_paypal_secret"],
                      "https://developer.paypal.com/dashboard/applications", in_app=False,
                      note_active="Connected — The Company can pay out via PayPal.",
                      note_none="No PayPal app credentials set (The Company → God Console)."))
    add(lambda: _svc("square", "Cash App / Square", "Commerce", ["square_access_token"],
                      "https://developer.squareup.com/apps", in_app=False,
                      note_active="Connected — Square-hosted Cash App Pay checkouts can be created.",
                      note_none="No Square access token set — $cashtag links still work without it."))
    add(_wordpress)

    # ── Crypto & Markets ──
    add(lambda: _svc("kraken", "Kraken", "Crypto & Markets", ["kraken_api_key", "kraken_api_secret"],
                      "https://www.kraken.com/u/security/api", in_app=True,
                      note_active="Connected — Trading tab can place/read orders.",
                      note_none="No Kraken API key/secret set."))
    add(lambda: _svc("robinhood", "Robinhood", "Crypto & Markets", ["rh_username", "rh_password"],
                      "https://robinhood.com/login", in_app=True,
                      note_active="Credentials saved for the Stocks tab.",
                      note_none="No Robinhood login saved."))
    add(lambda: _svc("wallets", "Wallets", "Crypto & Markets", ["wallet_mnemonic"], None, in_app=True,
                      note_active="A wallet seed is set (Finance → Wallets).",
                      note_none="No wallet seed generated/imported yet (Finance → Wallets)."))
    add(lambda: _svc("pearl", "Pearl", "Crypto & Markets", ["pearl_rpc_pass"], None, in_app=True,
                      note_active="Pearl node RPC password set.",
                      note_none="No Pearl node RPC password set (Finance → Markets → Pearl)."))

    # ── Social ── (all likely empty out of the box — no in-app config UI yet)
    add(lambda: _svc("youtube", "YouTube", "Social", ["yt_client_id", "yt_access_token"],
                      "https://console.cloud.google.com/apis/library/youtube.googleapis.com", in_app=False,
                      note_none="No YouTube OAuth client connected."))
    add(lambda: _svc("tiktok", "TikTok", "Social", ["tiktok_client_key"],
                      "https://developers.tiktok.com/apps", in_app=False,
                      note_none="No TikTok app connected."))
    add(lambda: _svc("meta", "Meta / Facebook / Instagram", "Social", ["meta_app_id", "meta_page_token"],
                      "https://developers.facebook.com/apps", in_app=False,
                      note_none="No Meta app connected."))

    # ── Infra & AI ──
    add(lambda: _svc("huggingface", "HuggingFace", "Infra & AI", ["hf_token"],
                      "https://huggingface.co/settings/tokens", in_app=True,
                      note_active="Token set — gated model downloads will work.",
                      note_none="No HuggingFace token set (needed for gated models)."))
    add(_lmstudio)
    add(lambda: _svc("mail", "Mail", "Infra & AI", ["mail_user", "mail_pass"], None, in_app=False,
                      note_active="Mailbox credentials set.",
                      note_none="No mailbox credentials set."))
    add(_cloudflare)
    add(_github)
    add(_node_gpu)

    return out


@router.get("/api/integrations/status")
def integrations_status():
    """{integrations:[{key_id,name,category,status,detail,setup_url,in_app}],
    summary:{active,needs_login,not_setup}, checked_at}. Never raises; never
    returns a secret value — only whether a key is populated."""
    try:
        items = _all_descriptors()
    except Exception:
        items = []
    summary = {"active": 0, "needs_login": 0, "not_setup": 0}
    for it in items:
        summary[it.get("status", "not_setup")] = summary.get(it.get("status", "not_setup"), 0) + 1
    return {"integrations": items, "summary": summary, "checked_at": int(_time.time())}

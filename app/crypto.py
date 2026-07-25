"""Encryption for credential settings at rest.

Secret settings (API keys, tokens, passwords) are stored in the `settings` table
encrypted with Fernet (AES-128-CBC + HMAC). The key comes from the environment
(`STORE_SECRET_KEY`) or a local key file (`DATA_DIR/.secret_key`, chmod 600) — NEVER
from the DB, so a stolen `store.db` alone can't reveal the credentials.

`dec()` is passthrough-safe: any value without the marker (legacy plaintext, or a
non-secret) is returned unchanged. This makes the rollout backward-compatible, and a
key mishap degrades to "an integration can't authenticate", not "the app crashes".
"""
import os
import logging
from pathlib import Path

from cryptography.fernet import Fernet

try:
    from config import DATA_DIR
except Exception:
    DATA_DIR = Path(__file__).resolve().parent.parent

_log = logging.getLogger("store")
_MARKER = "enc:v1:"

# Setting keys whose VALUE is a credential to encrypt at rest.
SECRET_KEYS = {
    "printify_key", "etsy_key", "etsy_shared_secret", "etsy_access_token",
    "etsy_refresh_token", "cults3d_api_key", "hf_token", "lmstudio_api_key",
    "models3d_asset_token", "wp_consumer_key", "wp_consumer_secret", "wp_mcp_token",
    "world_paypal_client_id", "world_paypal_secret", "mail_pass",
    # Gmail OAuth client for the Mail tab's gmail-provider accounts (mail_engine.py).
    # Per-account tokens are encrypted in mail_accounts rows, not here.
    "gmail_client_secret",
    # money + crypto/markets tabs (2026-07-17)
    "money_signal_token", "btc_rpc_pass", "ft_api_pass",
    "rh_password", "rh_mfa_secret",
    # real light-wallet master seed — controls REAL funds
    "wallet_mnemonic",
    # Kraken exchange API credentials (real account)
    "kraken_api_key", "kraken_api_secret",
    # JellyCoin GPU-rig shared token (LAN mining auth)
    "jelly_miner_token",
    # Pearl (PRL) node/wallet RPC password (Crypto → Pearl)
    "pearl_rpc_pass",
    # Square (Block) access token — Cash App Pay checkout links (Money tab)
    "square_access_token",
    # Cloudflare API token — zone cache purge (Settings → Integrations). The
    # account id isn't sensitive on its own, but it's harmless to encrypt too.
    "cf_api_token", "cf_account_id",
    # YouTube (Google OAuth) — social publishing (Social tab, app/social_publish/youtube.py)
    "yt_client_id", "yt_client_secret", "yt_access_token", "yt_refresh_token",
    # TikTok (Content Posting API OAuth) — social publishing (app/social_publish/tiktok.py)
    "tiktok_client_key", "tiktok_client_secret", "tiktok_access_token", "tiktok_refresh_token",
    # Meta Graph API (Instagram Reels + Facebook Page video) — social publishing
    # (app/social_publish/instagram.py). The ids aren't strictly credentials but
    # they identify the owner's accounts, so they're encrypted too.
    "meta_app_id", "meta_app_secret", "meta_access_token",
    "ig_business_id", "fb_page_id", "fb_page_token",
    # Income Phase 2 READ-ONLY importers (app/income_import.py) — pull/record
    # income only, never spend. PayPal here is the OWNER's account (distinct from
    # world_paypal_* which the game-world payout rail uses); printify_api_key is
    # the importer's own key (falls back to the shop's printify_key when unset).
    "paypal_client_id", "paypal_client_secret", "printify_api_key",
}


def is_secret(key: str) -> bool:
    return key in SECRET_KEYS


def _key_path() -> Path:
    return Path(DATA_DIR) / ".secret_key"


def _load_key() -> bytes:
    env = os.environ.get("STORE_SECRET_KEY", "").strip()
    if env:
        return env.encode()
    p = _key_path()
    if p.exists():
        return p.read_bytes().strip()
    key = Fernet.generate_key()
    try:
        p.write_bytes(key)
        os.chmod(p, 0o600)
        _log.info("generated new secret key at %s (back this up — losing it loses the secrets)", p)
    except Exception as e:
        _log.error("could not persist secret key to %s: %s", p, e)
    return key


_fernet = None


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(_MARKER)


def enc(value):
    """Encrypt a plaintext string. No-op on empty/None or already-encrypted values."""
    if value is None or value == "":
        return value
    if is_encrypted(value):
        return value
    try:
        return _MARKER + _f().encrypt(str(value).encode()).decode()
    except Exception as e:
        _log.error("secret encrypt failed (storing plaintext): %s", e)
        return value


def dec(value):
    """Decrypt a value. Passthrough for anything not marked-encrypted (plaintext-safe)."""
    if not is_encrypted(value):
        return value
    try:
        return _f().decrypt(value[len(_MARKER):].encode()).decode()
    except Exception as e:
        _log.error("secret decrypt failed (returning raw): %s", e)
        return value


def dec_secrets(d: dict) -> dict:
    """Return a copy of a settings dict with its secret values decrypted."""
    if not d:
        return d
    out = dict(d)
    for k in SECRET_KEYS:
        if out.get(k):
            out[k] = dec(out[k])
    return out


def migrate_encrypt_secrets(get_conn) -> int:
    """Idempotent: encrypt any plaintext secret rows in the settings table. Safe to run on
    every startup — already-encrypted rows are skipped. Returns how many were encrypted."""
    try:
        conn = get_conn()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        changed = 0
        for r in rows:
            k, v = r["key"], r["value"]
            if k in SECRET_KEYS and v and not is_encrypted(v):
                conn.execute("UPDATE settings SET value=? WHERE key=?", (enc(v), k))
                changed += 1
        if changed:
            conn.commit()
            _log.info("encrypted %d plaintext secret(s) at rest", changed)
        conn.close()
        return changed
    except Exception as e:
        _log.error("secret migration failed: %s", e)
        return 0

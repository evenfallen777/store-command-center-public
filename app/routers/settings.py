"""settings routes."""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Form, UploadFile, File
from deps import *
from services import *

router = APIRouter()


# Secrets that must never be returned by the settings API (auth material).
_SETTINGS_SECRET_KEYS = {"_auth_secret", "_auth_password_hash", "auth_secret", "auth_password_hash"}


@router.get("/api/settings")
def get_settings():
    conn = get_conn()
    rows = conn.execute("SELECT key,value FROM settings").fetchall()
    conn.close()
    # Never expose auth material or any private "_"-prefixed keys.
    result = {r["key"]: r["value"] for r in rows
              if r["key"] not in _SETTINGS_SECRET_KEYS and not r["key"].startswith("_auth")}
    # Provide defaults for keys that may not be set yet
    if "enhance_system_prompt" not in result:
        result["enhance_system_prompt"] = ENHANCE_SYSTEM
    if "default_model" not in result:
        result["default_model"] = DEFAULT_IMAGE_MODEL
    # Pricing defaults
    if "pricing_margin_pct" not in result:
        result["pricing_margin_pct"] = "40"
    for pt, base_c in BASE_COSTS.items():
        key = f"pricing_base_{pt.replace(' ', '_')}"
        if key not in result:
            result[key] = str(base_c)
    result.setdefault("cults3d_username", "")
    result.setdefault("cults3d_api_key", "")
    result.setdefault("enhance_model", ENHANCE_MODEL_DEFAULT)
    result.setdefault("nsfw_enabled", "")
    result.setdefault("nsfw_display", "")
    result.setdefault("nsfw_world", "")
    # Donations (Buy Me a Coffee) — toggle default OFF, handle default blank, so a
    # fresh public clone never ships someone else's donate link (see routers/donate.py).
    result.setdefault("donate_enabled", "0")
    result.setdefault("donate_bmc_user", "")
    # Proposal review gate (LLM judge) — see proposal_gate.py for the semantics.
    result.setdefault("proposal_gate_enabled", "0")
    result.setdefault("proposal_gate_mode", "auto_weed")
    result.setdefault("proposal_gate_reject_below", "40")
    result.setdefault("proposal_gate_approve_above", "80")
    result.setdefault("proposal_gate_interval_min", "360")
    result.setdefault("proposal_gate_batch_size", "10")
    # Mail auto-reply gate — see mail_gate.py for the semantics. Ships OFF, mode
    # 'manual' (today's click-to-draft flow); full_auto is an explicit opt-in.
    result.setdefault("mail_gate_enabled", "0")
    result.setdefault("mail_gate_mode", "manual")
    result.setdefault("mail_gate_confidence", "80")
    result.setdefault("mail_gate_interval_min", "15")
    result.setdefault("mail_gate_batch_size", "5")
    result.setdefault("mail_gate_allow", "")
    result.setdefault("mail_gate_deny", "")
    # Proposal desk (multi-lane origination) + Etsy demand signal — see
    # proposal_desk.py / etsy_signal.py for the semantics.
    result.setdefault("proposal_desk_enabled", "0")
    result.setdefault("proposal_desk_interval_min", "240")
    result.setdefault("proposal_lanes_enabled", DEFAULT_LANES)
    result.setdefault("etsy_signal_ttl_min", "360")
    result.setdefault("etsy_signal_scrape_enabled", "0")
    # P&L (Etsy) fee-model defaults — tunable so the margin view isn't magic numbers.
    try:
        from pnl import PNL_FEE_DEFAULTS
        for k, v in PNL_FEE_DEFAULTS.items():
            result.setdefault(k, str(v))
    except Exception:
        pass
    # The Cloudflare token (and account id) are WRITE-ONLY: the Integrations panel
    # never re-displays them ("leave blank to keep the current one"), so drop them
    # before the decrypt step — the token must never appear in any API response.
    result.pop("cf_api_token", None)
    result.pop("cf_account_id", None)
    return _dec_secrets(result)   # decrypt credentials so the UI shows the saved values


def _current_enhance_model() -> str:
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key='enhance_model'").fetchone()
    conn.close()
    return (row["value"] if row and row["value"] else ENHANCE_MODEL_DEFAULT)


@router.get("/api/settings/llm-models")
def list_llm_models():
    """LLM models available on the node's LM Studio, for the Settings model picker.
    Returns {models, current, error?}. Embedding models are separated out."""
    import httpx
    base = (globals().get("LLM_URL_DEFAULT") or globals().get("LMSTUDIO_URL")
            or f"http://{GPU_HOST}:1234/v1").rstrip("/")
    current = _current_enhance_model()
    try:
        r = httpx.get(f"{base}/models", headers=_llm_headers(), timeout=8)
        data = r.json()
    except Exception as e:
        return {"models": [], "current": current, "error": f"Couldn't reach LM Studio at {base}: {e}"}
    llms, embeds = [], []
    for m in data.get("data", []):
        mid = m.get("id", "")
        if not mid:
            continue
        (embeds if any(x in mid.lower() for x in ("embed", "nomic", "bge", "gte")) else llms).append(mid)
    # ensure the current pick is selectable even if LM Studio didn't list it
    if current and current not in llms:
        llms.insert(0, current)
    return {"models": llms, "embeddings": embeds, "current": current}

@router.patch("/api/settings")
def update_settings(data: dict):
    conn = get_conn()
    for k, v in data.items():
        # Never let the settings API write auth material (password hash / secret) —
        # the password is changed only via /api/auth/change-password (needs the old one).
        if k in _SETTINGS_SECRET_KEYS or str(k).startswith("_auth"):
            continue
        val = _enc(str(v)) if _is_secret(k) else str(v)   # encrypt credentials at rest
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, val))
    conn.commit()
    conn.close()
    return {"ok": True}

# ── Server configuration (name / port / URL path / data dir) → written to .env ──
ENV_PATH = BASE / ".env"

def _env_quote(v: str) -> str:
    """Quote values with spaces/# so `.env` sources cleanly in run.sh."""
    v = str(v)
    return f'"{v}"' if (" " in v or "#" in v or "\t" in v) else v

def _write_env_file(updates: dict):
    updates = {k: _env_quote(v) for k, v in updates.items()}
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    seen, out = set(), []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}"); seen.add(k); continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(out).rstrip("\n") + "\n")

_SERVER_FIELD_ENV = {
    "app_name": "STORE_APP_NAME", "port": "STORE_PORT",
    "base_path": "STORE_BASE_PATH", "data_dir": "STORE_DATA_DIR",
}

@router.get("/api/settings/server")
def get_server_settings():
    """Current server identity/location settings (live values)."""
    return {
        "app_name": APP_NAME,
        "port": PORT,
        "base_path": STORE_BASE,
        "data_dir": str(DATA_DIR),
        "env_path": str(ENV_PATH),
        "note": "Saved to .env — takes effect after a restart.",
    }

@router.post("/api/settings/server")
def set_server_settings(data: dict):
    """Persist server settings to .env. Requires a restart to apply."""
    updates = {}
    for field, envkey in _SERVER_FIELD_ENV.items():
        if field in data and str(data[field]).strip() != "":
            val = str(data[field]).strip()
            if field == "port" and not val.isdigit():
                raise HTTPException(400, "Port must be a number")
            updates[envkey] = val
    if not updates:
        raise HTTPException(400, "No server settings provided")
    _write_env_file(updates)
    return {"ok": True, "written": list(updates.keys()), "restart_required": True}

# ── Compute nodes / model hosts (per model type) → written to .env ────────────
# Config reads all of these from env at startup, so writing .env + restart moves
# where each model type runs. Image and Video both use ComfyUI (one URL).
_NODE_FIELD_ENV = {
    "gpu_host":     "STORE_GPU_HOST",       # box IP/host for SSH (3D gen, model installs, renders)
    "ssh_user":     "STORE_GPU_SSH_USER",   # ssh user on that box
    "llm_url":      "STORE_LLM_URL",        # LM Studio (LLM) OpenAI-compatible URL
    "comfyui_url":  "STORE_COMFYUI_URL",    # ComfyUI (image + video)
    "audio_url":    "STORE_AUDIO_URL",      # audio/music node (future)
}


@router.get("/api/settings/nodes")
def get_node_settings():
    """Live compute-node/host config used per model type."""
    return {
        "gpu_host": GPU_HOST,
        "ssh_user": GPU_SSH_USER,
        "llm_url": LMSTUDIO_URL,
        "comfyui_url": COMFYUI_URL,
        "audio_url": AUDIO_URL,
        "env_path": str(ENV_PATH),
        "note": "Saved to .env — takes effect after a restart. Image & Video share the ComfyUI URL.",
    }


@router.post("/api/settings/nodes")
def set_node_settings(data: dict):
    """Persist node/host config to .env. Requires a restart to apply everywhere."""
    updates = {}
    for field, envkey in _NODE_FIELD_ENV.items():
        if field in data and str(data[field]).strip() != "":
            updates[envkey] = str(data[field]).strip()
    if not updates:
        raise HTTPException(400, "No node settings provided")
    _write_env_file(updates)
    return {"ok": True, "written": list(updates.keys()), "restart_required": True}


@router.get("/api/product-types")
def get_product_types():
    """Return all product types (defaults + custom ones added by user)."""
    conn = get_conn()
    row  = conn.execute("SELECT value FROM settings WHERE key='custom_product_types'").fetchone()
    conn.close()
    custom = json.loads(row["value"]) if row else []
    merged = DEFAULT_PRODUCT_TYPES + [t for t in custom if t not in DEFAULT_PRODUCT_TYPES]
    return {"types": merged, "custom": custom}

@router.post("/api/product-types")
def add_product_type(data: dict):
    """Add a custom product type."""
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    conn = get_conn()
    row  = conn.execute("SELECT value FROM settings WHERE key='custom_product_types'").fetchone()
    custom = json.loads(row["value"]) if row else []
    if name not in DEFAULT_PRODUCT_TYPES and name not in custom:
        custom.append(name)
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('custom_product_types',?)",
                     (json.dumps(custom),))
        conn.commit()
    conn.close()
    return {"ok": True}

@router.delete("/api/product-types")
def remove_product_type(name: str):
    """Remove a custom product type (query param: ?name=...) ."""
    conn = get_conn()
    row  = conn.execute("SELECT value FROM settings WHERE key='custom_product_types'").fetchone()
    custom = json.loads(row["value"]) if row else []
    if name in custom:
        custom.remove(name)
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('custom_product_types',?)",
                     (json.dumps(custom),))
        conn.commit()
    conn.close()
    return {"ok": True}

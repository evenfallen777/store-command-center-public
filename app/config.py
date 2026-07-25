"""
Central configuration — EDIT HERE to move the Store to another machine.
Every value can be overridden with the environment variable shown in [brackets];
otherwise the default below is used.  Nothing else in the app hard-codes a host,
path, key, or model name — it all funnels through this file.
"""
import os
from pathlib import Path

def _env(name, default):
    return os.getenv(name, default)

# ── Filesystem paths (relative to the store/ project root) ───────────────────
# App display name (shown in the browser title, login page, and sidebar).
APP_NAME = _env("STORE_APP_NAME", "Store Command Center")

# BASE = code root (this repo). DATA_DIR = where user data lives (db, designs,
# videos, uploads, backups). Keep them equal unless you want data on another disk.
BASE     = Path(__file__).resolve().parent.parent
DATA_DIR = Path(_env("STORE_DATA_DIR", str(BASE)))
DESIGNS_PENDING  = DATA_DIR / "designs/pending"
DESIGNS_APPROVED = DATA_DIR / "designs/approved"
DESIGNS_REJECTED = DATA_DIR / "designs/rejected"
VIDEOS_DIR       = DATA_DIR / "videos"
RESELL_UPLOADS   = BASE / "static" / "resell_uploads"   # served under /static, stays with code
DOCEXTRACT_UPLOADS = BASE / "static" / "docextract_uploads"  # photo->fields captures (receipts, bills, …)
DB_PATH          = DATA_DIR / "store.db"
BACKUP_DIR       = DATA_DIR / "backups"
ARCHIVE_DIR      = DATA_DIR / "archive"   # saved webpage snapshots (time machine)

# ── 3D models (Cults3D pipeline) ─────────────────────────────────────────────
# MODELS3D_DIR holds the working copies + generated assets. MODELS3D_BACKLOG is
# the folder you drop raw 3D files into (STL/OBJ/3MF/ZIP/GLB) for the pipeline to
# scan → review → propose → publish. Point it anywhere with STORE_MODELS_BACKLOG.
MODELS3D_DIR      = DATA_DIR / "models3d"
MODELS3D_BACKLOG  = Path(_env("STORE_MODELS_BACKLOG", str(MODELS3D_DIR / "backlog")))
MODELS3D_RENDERS  = MODELS3D_DIR / "renders"    # turntable PNGs rendered from the mesh
MODELS3D_HERO     = MODELS3D_DIR / "hero"       # SDXL marketing/hero images
MODELS3D_ASSETS   = MODELS3D_DIR / "assets"     # zipped/staged files served to Cults3D
MODELS3D_GENERATED = MODELS3D_DIR / "generated" # locally-generated meshes (kept out of your backlog)
# 3D file extensions the backlog scanner picks up.
MODELS3D_EXTS     = tuple(e.strip().lower() for e in _env(
    "STORE_MODELS_EXTS", ".stl,.obj,.3mf,.glb,.gltf,.ply,.zip").split(",") if e.strip())
# Box-side helper scripts (mirror imagegen/videogen). Live on the GPU box, run via SSH.
RENDER_STL_SCRIPT = _env("STORE_RENDER_STL_SCRIPT", "~/.openclaw/tools/model3d/render_stl.sh")
GEN_3D_SCRIPT     = _env("STORE_GEN_3D_SCRIPT",     "~/.openclaw/tools/model3d/generate_3d.sh")

# ── Cults3D publishing defaults ──────────────────────────────────────────────
# createCreation requires PUBLIC https asset URLs. We serve them from a token-
# guarded route under PUBLIC_BASE_URL + STORE_BASE (see routers/models3d.py).
CULTS_DEFAULT_LOCALE   = _env("STORE_CULTS_LOCALE",   "en")
CULTS_DEFAULT_CURRENCY = _env("STORE_CULTS_CURRENCY", "USD")
CULTS_DEFAULT_LICENSE  = _env("STORE_CULTS_LICENSE",  "standard")  # cults license code
# Random token guarding the public asset route; persisted in settings on first use.
MODELS3D_ASSET_TOKEN   = _env("STORE_MODELS_ASSET_TOKEN", "")

# Headless browser used to capture JS-heavy / lightly-protected pages for the web archive
# (falls back to a plain HTTP fetch if not found). chromium works too.
CHROME_BIN = _env("STORE_CHROME_BIN", "google-chrome")

# Folders scanned by "Auto-populate → Import" to pull loose markdown into the library.
LIBRARY_IMPORT_DIRS = [str(BASE)]         # override with STORE_LIBRARY_IMPORT_DIRS (os.pathsep-separated)
if _env("STORE_LIBRARY_IMPORT_DIRS", ""):
    LIBRARY_IMPORT_DIRS = [p for p in _env("STORE_LIBRARY_IMPORT_DIRS", "").split(os.pathsep) if p]

# External helper scripts for image / video generation on the GPU box.
GENERATE_SCRIPT   = Path(_env("STORE_GENERATE_SCRIPT",   str(Path.home() / ".openclaw/tools/imagegen/generate.sh")))
VIDEO_GEN_SCRIPT  = Path(_env("STORE_VIDEO_GEN_SCRIPT",  str(Path.home() / ".openclaw/tools/videogen/generate_video.sh")))
VIDEO_CONT_SCRIPT = Path(_env("STORE_VIDEO_CONT_SCRIPT", str(Path.home() / ".openclaw/tools/videogen/generate_video_continuation.sh")))

# ── Web server / reverse-proxy ───────────────────────────────────────────────
STORE_BASE = _env("STORE_BASE_PATH", "/store")   # URL path prefix behind your reverse proxy ("" for root)
HOST       = _env("STORE_HOST", "0.0.0.0")       # [STORE_HOST]
PORT       = int(_env("STORE_PORT", "8787"))     # [STORE_PORT]

# ── GPU box: the machine running LM Studio (LLM) + ComfyUI (image/video) ──────
GPU_HOST       = _env("STORE_GPU_HOST", "127.0.0.1")   # <- change to your GPU machine's IP/host
GPU_SSH_USER   = _env("STORE_GPU_SSH_USER", "user")     # <- ssh user on the GPU box
# Total GPU VRAM (GB). With ~20+ GB the LLM and the image/video model can coexist,
# so set STORE_GPU_EXCLUSIVE=0 to stop unloading the LLM for image gen (2x 3060 = 24 GB).
GPU_VRAM_GB    = int(_env("STORE_GPU_VRAM_GB", "12"))
GPU_EXCLUSIVE  = _env("STORE_GPU_EXCLUSIVE", "1" if GPU_VRAM_GB < 20 else "0") == "1"
LLM_URL_DEFAULT = _env("STORE_LLM_URL", f"http://{GPU_HOST}:1234/v1")
COMFYUI_URL     = _env("STORE_COMFYUI_URL", f"http://{GPU_HOST}:8188")
# Audio / music generation node (future model type) — no default endpoint yet.
AUDIO_URL       = _env("STORE_AUDIO_URL", "")
BOX_SSH = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=10", f"{GPU_SSH_USER}@{GPU_HOST}"]
COMFY_CKPT      = _env("STORE_COMFY_CKPT", "~/ComfyUI/models/checkpoints")
BOX_VENV_PYTHON = _env("STORE_BOX_VENV_PYTHON", "~/ComfyUI/venv/bin/python3")
BOX_HF_CACHE    = _env("STORE_BOX_HF_CACHE", "~/.cache/huggingface/hub")
# Per-type HuggingFace homes on the node's model SSD (models separated by type).
# Each pipeline sets HF_HOME to its own folder so video/audio/3D weights never mix.
# These env values are FALLBACKS — the live `models_dir_<kind>` settings (Settings →
# 🧠 Models → 📁 Storage, resolved via app/model_paths.py) win when set.
NODE_HF_VIDEO   = _env("STORE_HF_VIDEO", "/media/user/SSD/models_video")
NODE_HF_AUDIO   = _env("STORE_HF_AUDIO", "/media/user/SSD/models_audio")
NODE_HF_3D      = _env("STORE_HF_3D",    "/media/user/SSD/models_3d")
NODE_LLM_DIR    = _env("STORE_NODE_LLM_DIR", "/media/user/SSD/models_llm")   # LM Studio's folder (informational)

# ── Default models (also overridable live in the Settings tab, which wins) ────
ENHANCE_MODEL_DEFAULT       = _env("STORE_ENHANCE_MODEL", "google/gemma-4-12b-qat")
DEFAULT_IMAGE_MODEL_DEFAULT = _env("STORE_DEFAULT_IMAGE_MODEL", "dreamshaperXL_lightningDPMSDE.safetensors")

# ── OpenClaw integration ─────────────────────────────────────────────────────
# The store shells out to the OpenClaw CLI (marketplace posting agent) and uses
# OpenClaw tool scripts for image/video generation (paths above).
OPENCLAW_BIN   = _env("STORE_OPENCLAW_BIN", "openclaw")          # CLI binary/path

# ── GitHub / Dev Swarm ───────────────────────────────────────────────────────
GH_BIN  = _env("STORE_GH_BIN", "gh")     # GitHub CLI (must be `gh auth login`-ed)
GIT_BIN = _env("STORE_GIT_BIN", "git")
# The 3-branch worktrees (see BOOK → git/GitHub workflow). Default to siblings of BASE.
REPO_MASTER = _env("STORE_REPO_MASTER", str(BASE))
REPO_DEV    = _env("STORE_REPO_DEV",    str(Path(BASE).parent.parent / "store-dev"))
REPO_RETAIL = _env("STORE_REPO_RETAIL", str(Path(BASE).parent.parent / "store-retail"))
OPENCLAW_AGENT = _env("STORE_OPENCLAW_AGENT", "agent_store")     # agent used for posting

# Public URL where this app is reachable from the internet (for OAuth callbacks).
PUBLIC_BASE_URL   = _env("STORE_PUBLIC_URL", "http://localhost:8787")
ETSY_REDIRECT_URI = _env("STORE_ETSY_REDIRECT_URI", f"{PUBLIC_BASE_URL}{STORE_BASE}/api/etsy/callback")
YT_REDIRECT_URI   = _env("STORE_YT_REDIRECT_URI",   f"{PUBLIC_BASE_URL}{STORE_BASE}/api/social/youtube/callback")
TIKTOK_REDIRECT_URI = _env("STORE_TIKTOK_REDIRECT_URI", f"{PUBLIC_BASE_URL}{STORE_BASE}/api/social/tiktok/callback")
META_REDIRECT_URI = _env("STORE_META_REDIRECT_URI", f"{PUBLIC_BASE_URL}{STORE_BASE}/api/social/instagram/callback")
GMAIL_REDIRECT_URI = _env("STORE_GMAIL_REDIRECT_URI", f"{PUBLIC_BASE_URL}{STORE_BASE}/api/mail/gmail/callback")

# ── Network Security tab (Pi-hole scanner + live monitor) ────────────────────
PIHOLE_API_HOST  = _env("STORE_PIHOLE_API_HOST", "localhost")   # where the Pi-hole API lives
PIHOLE_API_PORT  = _env("STORE_PIHOLE_API_PORT", "8889")
PIHOLE_API_PASS  = _env("STORE_PIHOLE_API_PASS", "")            # Pi-hole admin/API password
PIHOLE_CONTAINER = _env("STORE_PIHOLE_CONTAINER", "pihole")     # docker container name

# ── Restart behaviour ────────────────────────────────────────────────────────
# The "Restart" button in Settings runs this. Leave blank to re-exec the process
# in place (works under any supervisor with restart-on-exit, e.g. systemd
# Restart=always or Docker --restart, and also standalone). Or set a command,
# e.g. "systemctl --user restart store.service".
RESTART_CMD = _env("STORE_RESTART_CMD", "")

# ── Third-party API keys ─────────────────────────────────────────────────────
# These are normally stored in the DB (set them in the Settings tab). Setting the
# env var here provides a default / lets you ship keys with the deployment.
PRINTIFY_API_KEY = _env("PRINTIFY_API_KEY", "")
PRINTIFY_SHOP_ID = _env("PRINTIFY_SHOP_ID", "")
ETSY_API_KEY     = _env("ETSY_API_KEY", "")
ETSY_API_SECRET  = _env("ETSY_API_SECRET", "")

# WordPress / WooCommerce Portal bridge. Normally entered in the Portal tab UI
# (stored in the DB, takes precedence). Set here to ship/pre-seed a deployment
# via a config file before first launch. See app/routers/portal.py.
WP_URL             = _env("STORE_WP_URL", "")
WP_CONSUMER_KEY    = _env("STORE_WP_CONSUMER_KEY", "")
WP_CONSUMER_SECRET = _env("STORE_WP_CONSUMER_SECRET", "")
WP_MCP_URL         = _env("STORE_WP_MCP_URL", "")
WP_MCP_TOKEN       = _env("STORE_WP_MCP_TOKEN", "")

# ── Printify base production costs (cents) — used for retail price math ───────
BASE_COSTS: dict[str, int] = {
    "T-Shirt":    950,   # $9.50  — Monster Digital
    "Hoodie":     1750,  # $17.50 — Prima Printing
    "Sweatshirt": 1450,  # $14.50 — Prima Printing
    "Tank Top":   900,   # $9.00  — Monster Digital
    "Mug":        550,   # $5.50  — SPOKE Custom Products
    "Tumbler":    900,   # $9.00  — SPOKE Custom Products
    "Poster":     700,   # $7.00  — T Shirt and Sons
    "Sticker":    250,   # $2.50  — Printify Choice
    "Tote Bag":   800,   # $8.00  — Fulfill Engine
    "Phone Case": 650,   # $6.50  — SPOKE Custom Products
    "Mouse Pad":  500,   # $5.00  — Printed Mint
    "Pillow":     1200,  # $12.00 — MWW On Demand
}

# ── ComfyUI LoRA directory + recommended LoRAs ───────────────────────────────
COMFY_LORA = _env("STORE_COMFY_LORA", "~/ComfyUI/models/loras")

# ── Specialty ComfyUI models grouped by type (upscalers / controlnet / matting) ──
# Each group has its own ComfyUI dir. Downloads reuse the same flow (dest_dir per entry).
COMFY_UPSCALE    = _env("STORE_COMFY_UPSCALE",    "~/ComfyUI/models/upscale_models")
COMFY_CONTROLNET = _env("STORE_COMFY_CONTROLNET", "~/ComfyUI/models/controlnet")
COMFY_RMBG       = _env("STORE_COMFY_RMBG",       "~/ComfyUI/models/background_removal")


# ── Model catalogs (image / LoRA / video / 3D) live in model_catalog.py ──
# Re-exported here so `from config import *` keeps its full public surface.
# This import sits at the very end so every COMFY_* dir it needs is already
# defined above (no import cycle: config is complete when model_catalog loads).
from model_catalog import *  # noqa: E402,F401,F403

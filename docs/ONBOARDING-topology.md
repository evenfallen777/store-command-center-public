# Onboarding — Topology: one machine or two

New-operator guide to the two supported deployments, what runs where, and how to
choose + configure each. Companion docs: [`ONBOARDING-openclaw.md`](ONBOARDING-openclaw.md)
(the generation tool scripts referenced below) and `deploy/node/README.md` (the node
provisioner in detail).

## The two shapes

| | **1-PC** | **2-PC (server + GPU node)** |
|---|---|---|
| Store app (FastAPI + SQLite) | this box | **server** |
| LM Studio (LLM, :1234) | this box (optional) | **node** |
| ComfyUI (image, :8188) | this box (optional) | **node** |
| Video / audio / 3D model stacks | this box (optional) | **node** |
| gpu-guard + JellyMiner | this box (optional) | **node** |
| Reverse proxy (nginx/Caddy) | this box | **server** |

The store itself is lightweight (FastAPI + SQLite) and runs anywhere with Python 3.10+.
The heavy AI work needs an NVIDIA GPU box running LM Studio + ComfyUI + the Python
model stacks — **the same box or a separate one on your LAN**. Without any GPU box the
dashboard, storefront, finance, library etc. all still run; generation is just disabled.

**The GPU node must be Ubuntu** (24.04 recommended, or another Debian derivative) —
`deploy/node/node-setup.sh` hard-gates on this because Windows/macOS can't headlessly
autostart the CUDA services the way the node needs.

## The first-run setup wizard (`/setup`)

A fresh install redirects the first login to **`/setup`** (`app/routers/auth.py`,
`needs_setup()` in `app/auth_core.py`; driven by `static/js/setup-wizard.js`). Steps:

1. **Password** — replace the default login password.
2. **Topology** — pick **One PC** or **Two PCs**. Saved as the `topology` setting;
   choosing One PC also points the GPU host at `127.0.0.1`.
3. **GPU node** *(Two-PC only)* — enter the node's host/IP + SSH user, with a live
   *Test connection* button (`GET /api/node/ping`). Saved via
   `POST /api/settings/nodes` → written to `.env` → **takes effect after a restart**.
4. **Opt-in subsystems** — a short list of default-off toggles (the full ~40-row board
   lives in Settings → Systems).
5. **Health check** — `GET /api/health/pulse` rendered as a pass/fail table.

The wizard is sticky-off once finished or skipped, but stays reachable at `/setup`
forever; everything it does can also be done later in **Settings**.

## How the two machines are wired (2-PC)

Two transports, both configured in `app/config.py` / `.env`:

**HTTP** — for the always-on services on the node:

| Service | URL | Env var |
|---|---|---|
| LM Studio (OpenAI-compatible LLM) | `http://<node>:1234/v1` | `STORE_LLM_URL` |
| ComfyUI (image + video workflows) | `http://<node>:8188` | `STORE_COMFYUI_URL` |
| Audio node (optional/future) | — | `STORE_AUDIO_URL` |

**SSH** — for everything on-demand: video/audio/3D generation runs, model installs,
LLM unloads (`lms unload`), and the node deploy itself. `app/config.py` builds the
shared command prefix:

```python
BOX_SSH = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=10", f"{GPU_SSH_USER}@{GPU_HOST}"]
```

`BatchMode=yes` means **key-based auth is mandatory**: the server user must be able to
`ssh <STORE_GPU_SSH_USER>@<STORE_GPU_HOST>` with no password prompt (`ssh-copy-id`
once, then verify). System-package installs during node deploy additionally want
passwordless `sudo` on the node — without it the deploy prints the exact `apt-get`
line to run manually instead of hanging.

**Node → server (return direction):** `gpu-guard.sh` on the node heartbeats
`POST /api/gpu/guard/state` on the store ("a human is using the GPU") so the unified
queue auto-pauses during games/Blender/OBS and auto-resumes when idle — and the queue
can never wedge shut: a stale heartbeat (>5 min) auto-resumes (`app/routers/gpu_guard.py`).
The guard + miner read `~/.config/store-node.env` (`STORE_URL=`, `JELLY_TOKEN=`),
which the UI-driven deploy fills in automatically.

## Ports at a glance

| Port | What | Machine |
|---|---|---|
| 8787 | Store (FastAPI, `STORE_PORT`) | server |
| 80/443 | Reverse proxy → 8787 under `/store` (`STORE_BASE_PATH`) | server |
| 1234 | LM Studio headless server | node |
| 8188 | ComfyUI | node |
| 22 | SSH (all on-demand generation + deploy) | node |

## Setting up: 2-PC checklist

1. On the **server**: `./setup.sh`, edit `.env` —

   ```bash
   STORE_GPU_HOST=127.0.0.1      # your node's LAN IP/host
   STORE_GPU_SSH_USER=youruser       # ssh user on the node
   # STORE_LLM_URL / STORE_COMFYUI_URL default to http://$STORE_GPU_HOST:1234|8188
   STORE_GPU_VRAM_GB=12              # node VRAM; ≥20 GB flips GPU_EXCLUSIVE off
   ```

   (Or enter the same values later in the wizard / Settings → System →
   **Compute Nodes / Model Hosts** — both write `.env`; restart to apply.)
2. Key-auth SSH from the server user to the node user (`ssh-copy-id`).
3. Start the store (`./run.sh`, or as a service — `deploy/store.service`, README
   "Running as a service"), log in, run the wizard.
4. **Deploy the node**: Settings → **GPU Node** → *Deploy / Update Node* (pushes
   `deploy/node/` over SSH, runs `node-setup.sh deploy` in the background, streams the
   log live — `GET /api/node/deploy-log`). Or run it on the node directly; see
   `deploy/node/README.md`. This installs/repairs ComfyUI, the video/3D(/audio)
   stacks, the LM Studio headless autostart, gpu-guard, the miner, and the
   systemd `--user` units + linger.
5. Install LM Studio itself once by hand on the node (GUI app, can't be automated —
   <https://lmstudio.ai>), enable the `lms` CLI; the deploy wires the headless service.
6. Health: Settings → GPU Node panel, `GET /api/node/status`, or the wizard's pulse
   page. When the node is down, a banner flags it; generation jobs fail with clear
   errors and the rest of the store keeps working.

## Setting up: 1-PC

Two flavors, be honest about which you want:

- **1-PC, no GPU** (what the wizard's *One PC* choice configures): GPU host is set to
  `127.0.0.1` and image/video/music/3D/LLM stay **off** — dashboard, storefront, and
  everything non-generative runs fine. This is also the public repo's scrubbed default.
- **1-PC with a GPU** (server *is* the node): keep `STORE_GPU_HOST=127.0.0.1` (or
  `localhost`) and run LM Studio + ComfyUI on the same box. Note the SSH-based
  pipelines (video/audio/3D, model installs, LLM unload) still go through
  `ssh user@127.0.0.1`, so the box must run sshd and the store user needs key auth to
  itself. The HTTP pipelines (LLM, image) just work. `node-setup.sh` can be run on the
  same box to install the stacks. Mind VRAM: with <20 GB, `GPU_EXCLUSIVE` stays on and
  the orchestrator unloads the LLM around image/video work.

## Moving machines later

Everything machine-specific funnels through `app/config.py`, and every value is
env-overridable — nothing else in the app hard-codes a host, path, key, or model name.
Change `.env` (or Settings → System, which writes `.env` for you) and restart. The
single-GPU arbitration lives in `app/orchestrator.py` and follows whatever
host/URLs config gives it.

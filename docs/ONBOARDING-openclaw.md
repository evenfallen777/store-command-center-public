# Onboarding — OpenClaw: the tool suite behind generation

New-operator guide to what OpenClaw is, exactly where the store calls into it, and how
to set it up or re-point the store somewhere else. Companion docs:
[`ONBOARDING-topology.md`](ONBOARDING-topology.md) (which machine each piece runs on) and
[`ONBOARDING-plugins.md`](ONBOARDING-plugins.md).

## What OpenClaw is

**OpenClaw is not part of this repo.** It is a separately-installed local agent + tools
suite that lives in `~/.openclaw/` on the store machine, with an `openclaw` CLI on the
PATH. The store treats it as an optional neighbor and integrates at four seams — every
one env-overridable through `app/config.py`, and every one degrades cleanly if OpenClaw
is absent (the affected feature fails with a clear error; nothing else breaks).

The four integration points:

| # | Seam | What the store uses it for |
|---|------|----------------------------|
| 1 | **Tool scripts** under `~/.openclaw/tools/` | All image / video / 3D generation shells out to these |
| 2 | **The `openclaw` CLI agent** | Resell browser-posting; Library "research a guide" |
| 3 | **MCP / LLM proxy** (OpenClaw → store) | OpenClaw drives the store's whole API as tools |
| 4 | **State DB** `~/.openclaw/state/openclaw.sqlite` | The Company mirrors the 8 OpenClaw agents as town characters (read-only) |

Only #1 matters for the core generation pipelines. #2–#4 are quality-of-life.

---

## 1. The generation tool scripts

`app/config.py` defines one env-overridable path per script. Note the **"runs on"**
column — it is the key to understanding the wiring (see the topology doc):

| Script | Env var | Default path | Runs on |
|--------|---------|--------------|---------|
| Image gen | `STORE_GENERATE_SCRIPT` | `~/.openclaw/tools/imagegen/generate.sh` | **store box** (talks HTTP to ComfyUI) |
| Video gen | `STORE_VIDEO_GEN_SCRIPT` | `~/.openclaw/tools/videogen/generate_video.sh` | **store box** (SSHes into the node) |
| Video continuation | `STORE_VIDEO_CONT_SCRIPT` | `~/.openclaw/tools/videogen/generate_video_continuation.sh` | **store box** (SSHes into the node) |
| STL turntable render | `STORE_RENDER_STL_SCRIPT` | `~/.openclaw/tools/model3d/render_stl.sh` | **GPU node** (invoked via `BOX_SSH`) |
| Image → 3D mesh | `STORE_GEN_3D_SCRIPT` | `~/.openclaw/tools/model3d/generate_3d.sh` | **GPU node** (invoked via `BOX_SSH`) |

Per-model 3D variants (TripoSG / SF3D / TRELLIS / Hunyuan) are listed with their own
node-side `script:`/`install:` paths in `app/model_catalog.py`.

### How each one works

- **`imagegen/generate.sh`** runs as a local subprocess on the store machine
  (`subprocess.run([GENERATE_SCRIPT, prompt, out, w, h, steps, seed, model, lora,
  upscaler, matte])`). Internally it builds a minimal SDXL ComfyUI workflow JSON and
  submits it over HTTP to `STORE_COMFYUI_URL` — so the *script* runs locally but the
  *GPU work* happens wherever ComfyUI is. It first calls a helper,
  `~/.openclaw/stt/comfyui-ensure.sh`, to make sure ComfyUI is up.
- **`videogen/generate_video*.sh`** run locally, then SSH into the GPU node and execute
  `~/store_videogen.py` under the node's ComfyUI venv (diffusers: Wan / LTX / CogVideoX).
  `store_videogen.py` is shipped in `deploy/node/` and pushed by the node deploy.
- **`model3d/*.sh`** live *on the node* (installed there by `deploy/node/node-setup.sh`
  from `deploy/node/model3d/`); the store scp's the input image over, runs the script
  through `BOX_SSH`, and pulls the mesh back (`app/services_3d.py`).
- **Audio** does *not* go through an `~/.openclaw/tools` script — the store SSHes
  straight to `~/store_audiogen.py` on the node (`app/services_media_audio.py`).

### Call sites (where the store shells out)

| Pipeline | Files |
|----------|-------|
| Image (Studio, proposals) | `app/services.py` (`GENERATE_SCRIPT` subprocess) |
| Video + chains | `app/services_media.py`, `app/services_media_chain.py` |
| 3D (mesh gen, hero images) | `app/services_3d.py`, `app/routers/models3d/generate.py` |
| The Company's self-made art (sprites, tiles, terrain, buildings) | `app/world_sprites.py`, `app/world_tileset.py`, `app/world_terrain.py`, `app/world_build.py`, `app/world_floors.py`, `app/world_moon.py` |

Every call site rides the unified GPU queue (`orch.image_acquire()` /
`orch.video_acquire()` in `app/orchestrator.py`) before the script runs — never call
these scripts from new code without going through the queue (see `docs/DEV_PROCESS.md`).

### Setting the scripts up on a fresh install

The store repo ships **reference copies** of the store-side scripts:

```
app/_generate.sh.reference                    → ~/.openclaw/tools/imagegen/generate.sh
app/_generate_video.sh.reference              → ~/.openclaw/tools/videogen/generate_video.sh
app/_generate_video_continuation.sh.reference → ~/.openclaw/tools/videogen/generate_video_continuation.sh
```

1. Copy each reference file to its default path above (`mkdir -p` the dirs,
   `chmod +x` the scripts) — **or** put them anywhere and point the `STORE_*_SCRIPT`
   env vars there in `.env`. You do not need the OpenClaw suite itself for this;
   the directory layout is just the conventional home.
2. The scripts read `STORE_GPU_HOST` / `STORE_COMFYUI_URL` / `STORE_GPU_SSH_USER` from
   the environment, so the same `.env` values that configure the store configure them.
3. `generate.sh` expects the `comfyui-ensure.sh` helper at `~/.openclaw/stt/` (it
   starts ComfyUI if it's down). If you don't have the OpenClaw suite, either keep
   ComfyUI always running via its systemd unit (the node deploy installs
   `comfyui.service`) and trim that line from your copy, or supply your own ensure
   script at that path.
4. The **node-side** pieces (`model3d/*.sh`, `store_videogen.py`, `store_audiogen.py`)
   are installed automatically by the GPU-node deploy — Settings → **GPU Node** →
   *Deploy / Update Node*, or run `deploy/node/node-setup.sh` on the node by hand
   (see `deploy/node/README.md`).
5. Missing-script failures are explicit: e.g. video gen checks
   `Path(VIDEO_GEN_SCRIPT).exists()` and fails the job with *"Video generator script
   not found at … Set STORE_VIDEO_GEN_SCRIPT"*.

---

## 2. The `openclaw` CLI agent

Two features drive the OpenClaw agent CLI directly (config: `STORE_OPENCLAW_BIN`,
default `openclaw`; `STORE_OPENCLAW_AGENT`, default `agent_store`):

- **Resell auto-posting** — `app/services.py::_do_post_via_agent()` pipes a per-platform
  posting prompt into `openclaw agent --agent <agent> --json`; the agent browser-posts
  the listing and the store parses the JSON reply (detecting `NEEDS_LOGIN` / `CAPTCHA`
  outcomes).
- **Library guide research** — `app/routers/library.py` runs the same CLI with
  `--session-key store-library --message <prompt> --json` to research and write a
  Markdown guide into the Library.

Without the CLI installed, these two actions error out cleanly; everything else in the
store is unaffected.

## 3. The reverse direction: OpenClaw driving the store

- The store mounts its entire API as an **MCP server** at `/api/mcp` (fastapi-mcp, in
  `app/main.py`). Register it once on the store box:

  ```bash
  openclaw mcp add store --url http://127.0.0.1:8787/api/mcp --transport streamable-http
  ```

  Same-box MCP clients ride the localhost auth bypass — no session needed.
- The OpenAI-compatible **LLM proxy** at `/api/llm/v1/*` (`app/routers/llm.py`) lets
  OpenClaw (or any outside caller) use the store's LLM *through the unified GPU queue*
  instead of hitting LM Studio directly and colliding with generation jobs.

## 4. The Company mirror

`app/world_defs.py` names 8 OpenClaw agents (Ozzy, Wendy, Dex, Nova, Sable, Cleo, Cody,
Stella) as persistent town characters, and the world sim reads their real activity
**read-only** from `~/.openclaw/state/openclaw.sqlite` (opened with
`?mode=ro&immutable=1`). If the DB doesn't exist the characters simply have no external
activity signal — nothing fails.

---

## Quick answers

- **"Do I need OpenClaw?"** For image/video/3D generation you need the *tool scripts*
  (copy the reference files, step-by-step above) — not the suite. For resell
  auto-posting and Library research you need the actual `openclaw` CLI + a configured
  agent. For everything else: no.
- **"How do I point the store at scripts somewhere else?"** Set the `STORE_*_SCRIPT`
  env vars in `.env` and restart. Nothing outside `app/config.py` hard-codes a path.
- **"Which machine do the scripts belong on?"** `imagegen`/`videogen`: the machine
  running the store. `model3d` + `store_videogen.py`/`store_audiogen.py`: the GPU node.
  On a single-machine install, that's the same box.

# Store Command Center

A self-hosted **AI company-in-a-box**: a FastAPI + SQLite dashboard that runs a real
print-on-demand / resale / services operation end-to-end on your own hardware — and a
pixel-art company town, **The Company**, whose agents *are* the operation. Every image,
video, song, 3D model, listing, quote, trade proposal and code change is produced by
local models on your own GPU, queued through one scheduler, and **gated behind your
approval** before anything real (money, publishing, code) happens.

No cloud services required. The only external dependency is a GPU machine running
**LM Studio** (LLM) and **ComfyUI** + Python model stacks (image / video / audio / 3D) —
same box or another machine on your LAN.

![The Company — the pixel-art town that runs the store](docs/screenshots/01_world.jpg)

📖 **[Full documentation is in the wiki](../../wiki)** — per-system deep dives, the
complete [gates and toggles reference](../../wiki/Gates-and-toggles), install
walkthroughs and troubleshooting.

- [Feature Tour](#feature-tour)
- [Systems](#systems)
- [The Company — the game that runs the store](#the-company--the-game-that-runs-the-store)
- [The buddy system](#the-buddy-system)
- [JellyCoin (JLY) — the project's own coin](#jellycoin-jly--the-projects-own-coin)
- [**What's gated**](#whats-gated)
- [Requirements](#requirements)
- [Quick start](#quick-start-run-it-anywhere)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Running as a service](#running-as-a-service)
- [Reverse proxy](#reverse-proxy)
- [License](#license)

---

## Feature Tour

A curated walk through the tabs — there are twenty of them, so this is the highlight
reel. Every tab and sub-tab has its own shot in the
[wiki's Systems page](../../wiki/Systems).

| | |
|---|---|
| ![Dashboard](docs/screenshots/00_dashboard.jpg) *Dashboard — pipeline, queue, swarm and services at a glance* | ![GitHub & Dev Swarm](docs/screenshots/13_github.jpg) *GitHub & Dev Swarm — "The Engineers": repos, workboard and the dev → master → retail pipeline* |
| ![Dev Swarm — a job running](docs/screenshots/13_github__03_swarm.jpg) *The swarm at work — a local model plans and edits in a workspace; you review the diff* | ![God Console — Prayers](docs/screenshots/02_command__06_god.jpg) *God Console — anything that costs money or touches the world files a prayer you bless or deny* |
| ![Master Breaker](docs/screenshots/02_command__00_breakers.jpg) *Master Breaker — every gate, switch and automation on one page, locked rows included* | ![Studio](docs/screenshots/05_studio.jpg) *Studio — image, video, audio and 3D generation on your own GPU* |
| ![Finance](docs/screenshots/03_finance.jpg) *Finance — net worth, P&L, bills, treasury, wallets and markets under one roof* | ![Oracle](docs/screenshots/04_oracle.jpg) *Oracle — a forecasting tournament between local models; no money moves* |
| ![Etsy / Printify](docs/screenshots/07_etsy-printify.jpg) *Etsy / Printify — trend scan → design → review → approve → publish* | ![Resell](docs/screenshots/12_resell.jpg) *Resell — photograph an item → AI listing → post through a real logged-in browser* |
| ![Portal → WordPress](docs/screenshots/08_portal.jpg) *Portal — curate affiliate and generated products, push to your WooCommerce site* | ![Mail & Quotes](docs/screenshots/10_mail.jpg) *Mail & Quotes — LLM-drafted service quotes from your own mailbox, sent after review* |
| ![Network Security — Red/Blue](docs/screenshots/16_network-security__01_redblue.jpg) *Red team / Blue team — a look-only auditor files findings; fixes route through the gated dev swarm* | ![Knowledge](docs/screenshots/18_knowledge.jpg) *Knowledge — the library, web archive, research lab and queryable repo graph in one hub* |
| ![AI Assistant](docs/screenshots/17_agent.jpg) *AI Assistant — a tooled agent over the whole API, with per-category approval* | ![Social](docs/screenshots/09_social.jpg) *Social — drafts, YouTube/TikTok publishing, and post analytics feeding the taste model* |

---

## Systems

Everything is a tab in one single-page app, one router module per feature
(`app/routers/`, one JS module per tab in `static/js/`). The sections below mirror the
sidebar groups you actually see. See `INDEX.md` for the full per-system map (endpoints,
DB tables, files), and the [wiki](../../wiki) for per-system deep dives.

### 🏠 Home

| System | What it does |
|---|---|
| **📊 Dashboard** | Landing overview: pipeline stats, running generations, dev-swarm rollup, services up/down, plus the universal GPU queue controls (also pinned bottom-left everywhere). |
| **🌅 The Company** | The pixel-art town that *is* the operations layer — see [its section below](#the-company--the-game-that-runs-the-store). |
| **⚡ Command** | The God Console as a standalone tab, no game canvas required. The **Master Breaker** puts every gate, switch and automation on one page (locked rows shown honestly); alongside it sit the prayer queue, the per-agent **caps catalog**, a live **agent-loops graph**, the Republic, workboard, roster, finances, research and schedule. Two god-tier lieutenants live here too: ✝️ **Jesus** (constructive autopilot, file-only publish caps, paid actions excluded) and 😈 **Satan** (the adversarial worst-case mirror, whose verdicts widen every review into a best/worst/expected band) — both **default off**, both fenced by the same money and content floors as everyone else. |
| **💰 Finance** | One roof for all money, with seven panes: **Overview** (net worth across every rail), **P&L**, **Bills** (real recurring household bills and due dates), **Treasury** (the company books, operations ledger and PayPal), **Missions & Earn**, **Wallets**, and **Markets**. **Income** tracking lives here too: any income type with manual entry, plus read-only PayPal / Printify / on-chain importers (importer daemon default **off**; there is no spend path). |
| **🔮 Oracle** | A forecasting **tournament** between local LLM models: each "analyst" researches real-world catalysts, predicts direction/target/horizon, gets auto-scored when the horizon arrives, and learns from its hits and misses. Leaderboard included; **no money moves.** |

**Finance → Markets** is itself the markets desk: 📊 Stats · 🪼 **JellyCoin** ([below](#jellycoin-jly--the-projects-own-coin)) · 🦪 **Pearl (PRL)**, a supported *external* proof-of-useful-work L1 · ⛓ **Nodes** (local regtest `bitcoind`) · ⛏ **Mining** · 🤖 **Trading** (freqtrade: LLM proposes → backtest → you approve; dry-run by default) · 📈 **Stocks** · 🔑 encrypted **key backups**.

**Wallets** are real mainnet **light wallets**: deterministic BIP39 derivation, balances via public block explorers (no full node), a guarded two-step send flow, and Monero via `monerod` + wallet-RPC.

### 🎭 Studio

| System | What it does |
|---|---|
| **🎨 Image** | ComfyUI / SDXL generation with per-product-type LoRA + upscaler selection, prompt enhancement, sticker background knockout, and the design review pipeline. |
| **🎬 Video** | Local diffusers generation (Wan / LTX / CogVideoX …) with live progress, cancel/retry, multi-clip **chains** (segments conformed to a uniform size before compile), a **video→audio bridge** that scores a clip with music and optional narration, and the **Video Studio**: storyboard → scenes and shots → layered audio (voiceover + music) → export. Per-model **VRAM gates** stop oversized video models with a clear message instead of an OOM. |
| **🎵 Audio** | Music and voice: MusicGen, MMS-TTS, Stable Audio, and **ACE-Step** full songs with vocals + lyrics. |
| **🧩 3D** | Text/image → mesh (TripoSR / TripoSG / Hunyuan / SF3D / TRELLIS), rendered and reviewed before it enters the Cults3D pipeline. |
| **⚡ Queue** | The unified GPU queue in full-page form. Each engine pane also carries its own model catalog with one-click downloads, install/test buttons and idle-unload TTLs. |
| **🔒 Private Studio** | An opt-in, hidden workspace reusing the exact same pipelines for content you keep off every public surface. Three layered toggles (the master switch makes it **404-invisible**) and a **non-toggleable safety floor** that screens every prompt — see [What's gated](#whats-gated). |

### 🛍 Storefront

| System | What it does |
|---|---|
| **🛍 Etsy / Printify** | The print-on-demand pipeline: trend scan (Google Trends / Reddit / RSS → LLM-filtered proposals) → generate design → review → approve → publish. Live product management and store stats included. **Nothing publishes without a click.** |
| **🖨️ Cults3D** | The 3D-printables pipeline: scan a backlog of STL files → CPU turntable renders + SDXL hero images → AI-proposed listings → approve → publish via Cults3D's API. |
| **📷 Resell** | Physical-item flipping: photograph an item → vision-model analysis → AI listing content → post to marketplaces through a **real Chrome browser** (CDP automation with your logged-in profile), plus an offers inbox with AI-drafted haggling replies. |
| **🌐 Portal → WordPress** | Curates affiliate/external products, software and generated media from nine sources and pushes the picks to your own WooCommerce/WordPress site. Curate-then-push — you pick what goes live. |
| **📱 Social** | Drafts posts and captions from store media with the local LLM — and now **publishes**: YouTube and TikTok are wired end-to-end (connect → private test post → publish), a Meta/Instagram adapter (IG Reels + FB Page) is included, post **analytics flow back into the taste model**, and a **gated auto-scheduler** (default off) posts scheduled drafts at their set time. NSFW content is hard-blocked from every social surface. |
| **✉️ Mail & Quotes** | Reads a self-hosted mailbox (IMAP), drafts labor/service quotes with the local LLM under your business terms, and sends replies — each one reviewed first. |

### 🐙 Dev

| System | What it does |
|---|---|
| **🐙 GitHub / Dev Swarm** | Repo management via the `gh` CLI plus **The Engineers** — the local-model coding crew: file a job on the per-project **workboard**, a local LLM plans and edits code in a workspace, you review the diff, and the **pipeline** promotes dev → master → **retail** (the public branch, produced by an automated identifier scrub whose leak gate blocks the push if anything private survives). Human collaborators get a **collab** branch that merges through the same review lane. Sub-views cover the board, repos, the workflow, the live swarm, cron, the Agent Watcher and per-agent roles. |
| **🩺 Agent Watcher** | A background doctor for the automation: detects failed / paused / stalled swarm and media jobs, diagnoses them (optionally with the LLM), and feeds the diagnosis back into the coder's context on re-run. |
| **🎮 Games** | A game-engine workbench on the GPU node: Godot / Unity / Unreal install state, project management, asset wiring, headless builds, and editor control over MCP. |

### 🔌 Infrastructure

| System | What it does |
|---|---|
| **⚡ Unified GPU queue** | One orchestrator (`app/orchestrator.py` + a pure scheduling core with priority, model affinity and anti-starvation aging) serializes **everything** — LLM, image, video, audio, 3D, swarm, world — through the single GPU. Pause/resume/clear from any tab. |
| **🛡 gpu-guard** | The GPU box heartbeats "a human is using me" (game, Blender, OBS, VM…) and the queue auto-pauses; auto-resumes when idle, and can never wedge shut if the guard dies. |
| **🔌 Services** | A unified homelab hub: every Docker container and \*arr service grouped and controllable, with manual entries and overrides. |
| **🛡️ Network Security** | A security command center across nine views (Command, Connections, Threats, Audit, Web Traffic, Guardian, AI Shield, Pi-hole DNS, System & LLM). The Command view drives **seven scheduled background defenses**, each with its own toggle and interval — all default **off** except backups. On top sits the **Red team / Blue team** loop: a look-only adversarial auditor (no exploitation, ever) audits the store, site and system and files findings with CVE/OWASP references; the blue side routes fixes through the gated Engineers dev swarm; a findings lifecycle (open → triaged → fixing → fixed → verified) and a structured red↔blue Q&A channel keep the whole thing auditable. Default **off**, no auto-deploy, human approval before anything ships. |
| **🔗 MCP server** | The whole API is mounted as an MCP server at `/api/mcp` (fastapi-mcp) — every endpoint becomes a tool, so an external agent framework on the same box can drive the entire store. An OpenAI-compatible LLM proxy (`/api/llm/v1/*`) routes outside callers through the same GPU queue. |
| **🤝 Peers** | Federation between Store installs — see [The buddy system](#the-buddy-system). |

### 🤖 Assist & Config

| System | What it does |
|---|---|
| **🤖 AI Assistant** | A genuinely **tooled agent** in the UI: its tools are generated straight from the app's own API route table, so it can plan → call endpoints → observe → continue. Non-read calls are categorized (money / delete / publish / …) and pause for your approval in-chat; each category has an auto-approve toggle. Persistent conversations and reusable "skills" included. |
| **🧠 Knowledge** | One hub for three systems. The **Research Lab**: "Research Geniuses" — agents that run multi-step research projects (with optional image gathering/generation) and file the results into the Library. The **Library**: an offline knowledge base plus a full **web archive** — saving a page auto-escalates HTTP fetch → `wget` → your logged-in Store browser, re-saving builds a version history, AI guides/summaries on top. And the **Knowledge Graph**: the whole repo as a queryable graph (built with graphify) — natural-language queries, path/affected analysis, and a native force-directed explorer (`setup.sh --with-graphify`). |
| **⚙️ Settings** | Eight panes: **System** (host/GPU-node admin, logs, restart), **Backups**, **Models**, **Integrations** (with a status board showing every connected service as active / needs-login / not-set-up), **Prompts**, **Systems**, **Plugins**, and **Interface** (hide any tab; turn the game layer on or off). |

Three registries live under Settings and are worth calling out:

- **Model registry** — pick which local model powers *each feature* (listings, haggling,
  security, swarm coder, …); the queue shows which model each job wants.
- **Prompt registry** — every LLM system prompt in the app (50 of them) in one place,
  editable in a workbench with search and a live ▶ Test button.
- **Systems registry** — `app/systems_registry.py` catalogues every background system and
  the exact setting that controls it, including which ones have **no** toggle. It is the
  source of truth behind [What's gated](#whats-gated).

**Plugins** are drop-in: put a folder in `plugins/`, and its backend routes and frontend
views register themselves. A failed plugin is isolated (it flags itself in the nav rather
than breaking it). See `plugins/README.md` for the contract.

**Hardening** — secrets encrypted at rest (Fernet), consistent online DB snapshots (local
and off-box), money-math tests, a rotating log with an in-app viewer, and a global
exception handler. See `HARDENING.md`.

---

## The Company — the game that runs the store

The centerpiece tab is a living pixel-art company town. It is **not a minigame bolted
onto a dashboard — it is the store's operations layer, gamified.** Two coupled layers,
one truth:

1. **The Real Layer** — actual platform work: generations, listings, resale, code jobs,
   security scans. This is truth, and it drives the simulation.
2. **The Game Layer** — the town. Every visible action is a *dramatization* of a real
   event. Agents never do fake work; they act out real work.

**Named, persistent agents are bound to real jobs.** When the swarm compiles code, an
agent walks to a desk and codes. When a design generates, an artist paints. Agents earn
XP in RuneScape-style skills (woodcutting, mining, farming, fishing, construction,
combat, knowledge), and a RimWorld-style work-priority scheduler assigns real work to
whoever's best at it — the town measurably gets better at running the store over time.

What's in the world:

- **A real economy.** A sized item catalog, per-agent inventories, food that must be
  bought and eaten, shop trips, houses with placement slots for furniture — and every
  purchase flows into the company fund. Agents draw salaries for **real** work done.
- **The God Console** — the safety backbone. Twelve tabs: **Prayers** (an approval
  queue — anything an agent wants that costs money or touches the outside world is
  filed as a prayer you bless or deny), a postpaid **budget + ledger** with PayPal,
  a community **Board**, the **Republic** (an assess → propose → convene strategy
  engine), the town **Bible** (canonical scripture generated from the project's own
  book), finances, roster, research, and schedule. Every gate ships with a toggle —
  nothing is hard-coded except the irreversible-money guards.
- **God-taste learning.** An embedding k-NN model learns *your* taste from every
  bless/deny and design review, so agent proposals drift toward what you'd approve —
  and Boss/Mayor endorsement checks gate the automation on top. The leaders are real
  officers: they can spend actual (budgeted, approval-gated) money on swarm jobs.
- **Raids.** Waves of enemies with traits, city-bbox walls, watchtower turrets,
  fighter duels with kill credit, and drill readiness grades. The church restores
  morale; mood has a real thought-ledger and mental breaks.
- **Progression.** A research tree with prerequisites, a material tech ladder
  (wood → stone → bronze → iron → steel), a construction lifecycle for new buildings,
  achievements, seasons, and a 24-hour town timetable. The HQ itself evolves through
  saved **progression stages** — the current era is an iron/steel-age rework of the
  town, with ore and fishing venues to feed the material ladder.
- **The town makes its own art.** Sprites are generated through a pixel-art pipeline
  (ComfyUI + a pixel-art LoRA) and judged by a vision model before they're used; the
  terrain itself is **progressively agent-painted** — individual tiles are generated
  one at a time (by you, or slowly by an agent when the toggle is on) and must pass a
  QA gate *and* a palette/style-harmony gate before joining the shared atlas.
- **The town has a voice.** Agents keep journals and thoughts, hold opinions and town
  meetings, and even write **their own song lyrics**, which are rendered into real
  audio via ACE-Step. Actions have positional sound effects; nights get real lighting.
- **The town does real work on its own.** An autonomous creation loop lets agents make
  real media, and a selling module can place real paid listings — both strictly behind
  the prayer/approval system above.

The philosophy: automation you can *watch* is automation you can trust. Instead of a
cron log, you get a town where every worker, every purchase and every request for your
blessing is visible — and the fun of the game is exactly the act of supervising a real
business.

Docs: `app/WORLD.md` (module map), `app/WORLD_GAME.md` (game-systems design),
`app/WORLD_ROLES.md`, `app/RIMWORLD_RESEARCH.md`.

---

## The buddy system

You don't run this alone — the store is built around helpers, at two ranges:

**Remote buddies — the peer federation.** Two Store installs can pair with a one-time
**invite key** and become peers over HTTPS RPC:

- **Peer review:** before you approve a swarm coding job, ask a buddy's install to
  review it — their local model reads the diff and files an **advisory vote** next to
  yours. Your click still decides; a peer can never merge anything.
- **Lent compute:** opt-in, a peer can queue jobs on your GPU when it's idle (and you
  on theirs) — a two-person compute co-op with the same approval gates.
- Pairing is explicit and revocable; the remote surface is a small self-guarded RPC
  (`X-Peer-Key`) that opens *only* the peer endpoints, nothing else
  (`tests/test_peers.py` pins this).
- **"Make this install yours":** one button re-homes a cloned install onto *your*
  GitHub (your repo becomes `origin`, the source stays `upstream` for updates), so a
  friend you onboard becomes a real peer with their own fork, not a tenant.

**In-house buddies.** The same idea, inside one install: The Company's agents are
coworkers doing your real jobs, the **Agent Watcher** is the colleague who notices a
stuck job and writes up what went wrong before the retry, and the **AI Assistant** is
the operator sitting at the same console as you — with tools over the whole API but
your approval gates between it and anything consequential.

---

## JellyCoin (JLY) — the project's own coin

The store ships its own token, and it is deliberately honest about what it is:
**a community token, not an investment** — no price, no exchange listing, no promise
of value (see `docs/jellycoin/WHITEPAPER.md`).

- **All supply is real GPU proof-of-work.** There is no faucet and no admin mint. The
  store acts as the single **authority node**: it keeps the ledger and *verifies*
  hashes, while any OpenCL GPU — from a decade-old Radeon to a current RTX — mines
  blocks via a getwork protocol. Every coin traces to a block anyone can re-verify
  with a few lines of SHA-256.
- **No CPU mining, by protocol.** The server only verifies; the shipped miner
  (`miner/`, installed as a service by the GPU-node deploy) refuses to start without
  an OpenCL GPU. This keeps issuance tied to hardware people actually own — and gives
  old cards a second life.
- **The Company mines with you.** Agent skilling earns **boost tickets** (toggleable
  in the God Console) that cash out *only inside a real mined block* — in-world labor
  sweetens real proof-of-work, but skilling never mints coins by itself.
- **Art NFTs.** Real generated art files can be minted as NFTs anchored to their
  content hash on the ledger.
- **Human-approved commerce.** The Company's LLM can draft "push JLY" missions —
  every one sits in *proposed* until you approve it. Software proposes; only a human
  sells.
- Wallets, transfers, tips (including the AI Assistant's own tipping wallet) and a
  block explorer live in the JellyCoin tab.

**Pearl (PRL)** is separate: an *external* proof-of-useful-work L1 the store can
monitor and (behind a hard gate) mine — supported, but not ours. JLY is the native
coin of this project's economy.

---

## What's gated

The whole point of this project is that agents do real work while **you** stay the one
who says yes. This section documents exactly where that line is drawn — including the
places it *isn't*, because a safety claim you can't verify is worse than none.

The full per-setting reference lives in the [wiki](../../wiki/Gates-and-toggles).

**The posture.** `world_ops_automation_mode` defaults to **`review`** — everything real
is queued to the God Console's prayer queue and nothing runs until you bless it. Switching
it to `budget` lets free and within-budget actions run on their own; gated kinds still
always wait. The design rule is that **every gate ships with a toggle**, with the
deliberate exceptions listed below.

### Hard floors — these cannot be turned off

| Floor | What it protects |
|---|---|
| `paypal_payout`, `wallet_send`, `secret_export` | The three irreversible money-out paths. The API **refuses** to disable these gates, and refuses to let them be filed through the generic prayer API — they only come from their own validating endpoints. A wallet send is signed and broadcast *only* after a human blessing, so even a localhost caller cannot move real crypto on its own. |
| NSFW safety floor | Screens **every** prompt — yours, the model's own, and the world's — for minors (keyword *and* numeric-age parsing), real-person likenesses/deepfakes, and non-consensual themes. Applies to every modality, before anything is stored or run. There is no setting, no prompt-registry entry and no API that can weaken it, and the "every gate gets a toggle" rule explicitly does **not** apply here. |
| Retail scrub leak gate | Blocks the public push outright if any real identifier survives the scrub. |
| Public snapshot gates | The outbound world snapshot is off by default, refuses to run if *any* NSFW toggle is on (failing closed if it can't tell), and runs a regex leak sweep over the exact outbound bytes. |
| Swarm final approval | `user_final_approval` is forced on — a safety invariant, not a user setting. Promotion also refuses until you approve. |
| Peer boundary | Peers must be invited, approved, and can be revoked. Their RPC surface opens *only* the peer endpoints — never settings, money, git or anything else — and their code-review votes are **advisory**; your click still decides. |
| Live trading | Syncing exchange keys force-sets `dry_run = True`. Going live is a manual config change, never an API action. |

### Toggleable gates

All default **on** (`world_ops_gate_*`), and all flip from the God Console:

| Gate | Protects |
|---|---|
| `post_etsy`, `post_printify` | Paid listings (~$0.20 each) |
| `cashapp_request`, `cashapp_checkout` | Cash App payment links (money *in*) |
| `add_software` | Code changes reaching your repo |

`world_ops_gate_creations` (default **off**) additionally forces a 👍/👎 on every creative
publish even in budget mode.

### Spend limits

`world_ops_cap_cents` is a **$20/month** postpaid ceiling and `world_taste_min` (0.35)
sets how confident the taste model must be before anything auto-runs. Note that approving
with `force=true` deliberately overrides the monthly cap — it is a ceiling on *automation*,
not a hard spend limit.

### Defaults that matter

Everything genuinely autonomous ships **off**: autonomous creation (`world_auto_enabled`),
autonomous listing (`world_sell_auto`), the dev-swarm cron, all three miners
(JellyCoin, Pearl, world mining) *and* their separate agent-control gates, every security
defense except backups, the public snapshot, and all three Private Studio toggles.

### Known gaps — not currently gated

Honest disclosure, since this is a self-hosted app you are trusting with real credentials:

- **XMR mining** starts the miner directly, with no toggle and no agent gate — unlike
  JellyCoin and Pearl, which each have both. This is an inconsistency, not a decision.
- **Social posting**, the **WooCommerce/Portal push** and **outbound mail** publish without
  a prayer gate (the AI Assistant still asks, since it classifies them as publish/money —
  but a direct API call does not).
- **Docker control** (start/stop containers) and **SSH command execution on the GPU node**
  are ungated.
- **Free publishes** (WordPress, Cults3D) auto-run in budget mode unless you turn on the
  creations gate.
- **Plugins** run in-process with no sandbox, and an unknown plugin defaults to enabled.
  Only install plugins you trust.
- **Any request from `127.0.0.1` skips session auth**, by design — it is how the MCP mount
  and the in-app assistant work. There is no toggle. It is the single largest blast-radius
  decision in the app, and the irreversible-money floors above exist specifically to
  compensate for it. **Do not expose this app directly to the internet.** Put it behind a
  reverse proxy with real auth, or keep it on your LAN/VPN.

## Requirements

- **Python 3.10+** on **Linux or macOS**. The app itself is lightweight (FastAPI + SQLite).
- For the **AI generation** features: a machine with an **NVIDIA GPU** running **LM Studio**
  (LLM) and **ComfyUI** (+ the Python model stacks for video/audio/3D). It can be the same
  box or a separate one on your LAN — point `STORE_GPU_HOST` at it in `.env`. Without a GPU
  box the dashboard still runs; generation is just disabled.
- Publishing accounts are optional and only needed for the features you use: Printify / Etsy
  (print-on-demand), Cults3D (3D). Enter keys in the **Settings** tab.

> **This is a public self-host snapshot.** Every default is a generic `localhost` value —
> nothing points at anyone's real infrastructure. Set your own hosts/keys in `.env`
> (see [Configuration](#configuration)).

## Quick start (run it anywhere)

```bash
git clone <repo-url> store && cd store   # the green "Code" button above has the URL

# One-shot bootstrap: venv, deps, .env, database
./setup.sh                       # add --all to also fetch ComfyUI + LM Studio
$EDITOR .env                     # set STORE_GPU_HOST, STORE_PUBLIC_URL, keys
./run.sh                         # serves on http://0.0.0.0:8787
```

<details><summary>…or do it manually</summary>

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env && $EDITOR .env
( cd app && ../venv/bin/python -c "from db import init_db; init_db()" )
./run.sh
```
</details>

**`setup.sh` flags:** `--with-comfyui` (clone + prep ComfyUI), `--with-lmstudio`
(guide LM Studio download), `--with-graphify` (install Graphify and build the
Knowledge Graph tab's index from your checkout), `--with-dev` (pytest + playwright
so `./run_tests.sh` and the SPA browser-verify work), `--service` (install +
enable the systemd `--user` unit), `--all` (everything). The GPU tools belong on
your `STORE_GPU_HOST` machine; graphify runs fine on this one. At the end setup
prints a **post-install checklist** of everything it can't automate (reverse
proxy, GPU-node deploy — which also installs the gpu-guard queue-pauser and the
JellyMiner service on the node — model downloads, searxng for the Research tab,
mining rigs, external accounts).

First login password is **`store`** (you'll set your own on first sign-in / in
Settings). A first-run **setup wizard** (`/setup`) then walks you through password →
topology → node test → subsystems → health check. If you're behind a reverse proxy,
see **Reverse proxy** below.

---

## Configuration

Everything you'd change to move machines lives in **`app/config.py`**, and every
value there can be overridden with an environment variable (put them in `.env`).
See **`.env.example`** for the full list. The important ones:

| What | Env var | Default |
|------|---------|---------|
| GPU box host (LM Studio + ComfyUI) | `STORE_GPU_HOST` | `127.0.0.1` |
| SSH user on the GPU box | `STORE_GPU_SSH_USER` | `user` |
| LLM endpoint | `STORE_LLM_URL` | `http://<GPU_HOST>:1234/v1` |
| ComfyUI endpoint | `STORE_COMFYUI_URL` | `http://<GPU_HOST>:8188` |
| Listen host / port | `STORE_HOST` / `STORE_PORT` | `0.0.0.0` / `8787` |
| Reverse-proxy path prefix | `STORE_BASE_PATH` | `/store` |
| App display name | `STORE_APP_NAME` | `Store Command Center` |
| Data directory (db/designs/videos/backups) | `STORE_DATA_DIR` | repo root |
| Public URL (Etsy OAuth callback) | `STORE_PUBLIC_URL` | `http://localhost:8787` |
| OpenClaw CLI / agent | `STORE_OPENCLAW_BIN` / `STORE_OPENCLAW_AGENT` | `openclaw` / `agent_store` |
| Printify key / shop | `PRINTIFY_API_KEY` / `PRINTIFY_SHOP_ID` | (Settings tab) |

API keys can also be entered live in the **Settings** tab (stored in the DB,
which takes precedence over env vars).

### In-app admin (Settings → System)

- **Server**: change app name, port, URL base path, and data directory (written
  to `.env`; applied on restart).
- **Compute Nodes / Model Hosts**: point LLM / ComfyUI / 3D / audio at any machine,
  and **pick the LM Studio model** the LLM uses (prompts, listings, haggling, enhance).
- **GPU Node**: one-click **Deploy / health-check** the GPU box — image, video, 3D,
  audio, LM Studio, and the autostart services — with a live log. Requires Ubuntu.
- **Backups**: create / download / restore / delete backups (stored in
  `<data-dir>/backups`). Restore takes a safety backup first, then restarts.
- **Store Logs**: a live, filterable view of the rotating log at
  `<data-dir>/logs/store.log` (errors/warnings filter + text search + tally). Every
  unhandled endpoint failure is logged with its path + traceback by a global handler.
- **Restart Server** — guarded: a live "GPU busy — N job(s)" banner warns when a
  generation is in flight (a restart would kill it); restart requires confirmation
  while busy. Plus **Sign Out** and **Fix Browser Lock**.

---

## Architecture

The backend is split into small, single-purpose modules — edit the one that
matches your change, not one giant file:

```
app/
├── main.py            # thin assembler: app, middleware, static mounts, router wiring,
│                      #   file logging + a global exception handler, /api/mcp mount
├── config.py          # ALL machine-specific settings (hosts, paths, keys, models)
├── deps.py            # shared kernel: settings, auth, clients, LLM helper, prompts
├── services.py        # background jobs (image gen, publishing, geo, resale-agent, 3D)
├── services_media.py  # video + audio generation + the video→audio bridge (re-exported)
├── orchestrator.py    # the single-GPU queue: LLM vs image/video/3D/audio/swarm/world
└── routers/           # one module per feature area — the API endpoints
    ├── auth.py        proposals.py   models.py    printify.py   videos.py
    ├── dashboard.py   designs.py     trends.py    etsy.py       resell.py
    ├── generate.py    tasks.py       settings.py  agent.py      library.py
    ├── security.py    system.py      audio.py     node.py       models3d.py
    ├── cults3d.py     world.py       world_ops.py crypto.py     wallets.py
    ├── oracle.py      jellycoin.py   pearl.py     cashapp.py    money.py
    ├── mail.py        portal.py      social.py    homelab.py    research.py
    ├── watcher.py     gpu_guard.py   graph.py     llm.py        nsfw.py
    ├── github/        peers/         # router packages (repos/jobs, api/rpc)
    └── resell_browser.py             # headless-Chrome resale automation
```

Big files are split so each stays editable (and small enough for a local model to
open): `services.py` re-exports `services_media.py`; the world simulation is a
family of small `app/world_*.py` modules; the frontend's `app-core.js` kernel
loads per-tab modules (`tab-world.js`, `tab-crypto.js`, `world-render-*.js`, …).

Frontend is `static/index.html` (markup) + classic-script JS modules under
`static/js/` — no build step. The node provisioner is `deploy/node/node-setup.sh`
(run from Settings → GPU Node or directly on the Ubuntu GPU box).

**To add or change an endpoint:** edit `routers/<area>.py`. Shared helpers live
in `deps.py`; long-running jobs in `services.py`. The full subsystem map —
every tab's files, endpoints and DB tables — is **`INDEX.md`**; the architecture
patterns behind it are **`docs/ARCHITECTURE.md`**.

---

## Running as a service

A per-user systemd template is in `deploy/store.service` (no root required):

```bash
mkdir -p ~/.config/systemd/user
cp deploy/store.service ~/.config/systemd/user/store.service
$EDITOR ~/.config/systemd/user/store.service   # fill in the two <ABSOLUTE_PATH_TO> lines
systemctl --user daemon-reload
systemctl --user enable --now store.service
loginctl enable-linger "$USER"                 # survive logout
```

**Restart** and **Sign Out** are available in the app under **Settings → System**.
The Restart button re-execs the process in place by default (works with or
without a supervisor). To route restarts through systemd instead, set
`STORE_RESTART_CMD=systemctl --user restart store.service` in `.env`.

---

## Reverse proxy

The app is designed to sit under a path prefix (default `/store`). Example nginx:

```nginx
location ^~ /store/ {
    proxy_pass http://127.0.0.1:8787/;   # trailing slash strips the /store prefix
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Set `STORE_BASE_PATH` to match (`/store`), or to `""` to serve at the domain
root. The frontend base path is injected server-side, so static assets and the
API resolve correctly under whatever prefix you choose.

> Note: the session cookie is `Secure`, so sign-in requires **HTTPS** (or
> `http://localhost`, which browsers treat as secure).

---

## License

[MIT](LICENSE) — use it, change it, sell it, fork it. No warranty of any kind.

Please read [What's gated](#whats-gated) before pointing this at real money, real
credentials or a real storefront. It ships with the cautious defaults on, and it is
built to be run on your own hardware behind your own network — not exposed to the
open internet.

Bundled third-party reference documentation is **not** redistributed in this public
tree; the Library ships only with content written for this project.

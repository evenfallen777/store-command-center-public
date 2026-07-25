# Store — INDEX SUITE

**How to use this index.** One section per SPA tab/subsystem. Each section tells you: what the tab does, its sub-views, the exact frontend `static/js/*.js` files, the backend `app/routers/*.py` + supporting `app/*.py` modules, every API endpoint, the DB tables it owns, external services, and related docs. An agent working on one subsystem should read ONLY the files in that section plus §Shared Infrastructure. Endpoint notation: `G`=GET `P`=POST `PA`=PATCH `D`=DELETE `PU`=PUT.

Verified 2026-07-18 against the live code: **20 nav tabs**, **36 routers** (+ empty `__init__.py`), **450 endpoints**.

> **See also [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — the *patterns* behind this map (router-package layout, the classic-script shared-global SPA model, the money-gate system, how to add a tab/endpoint) plus the two verification harnesses.
>
> **2026-07-18 refactor note** — ten oversized files were split for small-context agents. Paths in older sections may name the pre-split file; the current structure is:
> - `routers/github.py` → package `routers/github/` (`_base`, `repos`, `models`, `jobs`)
> - `routers/peers.py` → package `routers/peers/` (`_base`, `api`, `rpc`, `client`)
> - `swarm.py` → package `swarm/` (`_base`, `llm`, `workspace`, `systasks`, `engine`)
> - `deps.py` → extracted `auth_core.py` + `llm_client.py` (deps still re-exports them)
> - `static/js/app-main.js` → `app-core` (kernel, loads first) + `app-nav/queue/studio` + `tab-dashboard/designs/products/trends/agent`
> - `network-security.js` → `sec-core` + `sec-command/connections/engines/dns/system`
> - `tab-settings.js` → `settings-core` + `settings-models/prompts/updates-github/peers`
> - `tab-models.js` → `tab-models` (slim) + `models-image/video-audio/3d`
> - `tab-resell.js` → `tab-resell` (slim) + `resell-new/listings/offers`
> - `world-render.js` → `world-render` (core) + `world-render-buildings/combat/overlays/agents`

## All tabs (authoritative: `static/index.html` nav + `renderView()` in `static/js/app-nav.js`)

| view key | Nav label | Section | Router(s) |
|---|---|---|---|
| `dashboard` | Dashboard | §1 | dashboard.py |
| `world` | The Company | §2 | world.py, world_ops.py |
| `finance` | Finance | §3 | pnl.py (+ world_ops, money, crypto, wallets routers as sub-panes) |
| `oracle` | Oracle | §4 | oracle.py |
| `studio` | Studio | §5 | generate.py, designs.py, videos.py, audio.py, models3d.py, models.py |
| `nsfw` | Private Studio | §6 | nsfw.py |
| `etsy-printify` | Etsy / Printify | §7 | proposals.py, etsy.py, printify.py, trends.py |
| `portal` | Portal → WordPress | §8 | portal.py |
| `social` | Social | §9 | social.py |
| `mail` | Mail & Quotes | §10 | mail.py |
| `cults3d` | Cults3D | §11 | cults3d.py (+ models3d.py publish path) |
| `resell` | Resell | §12 | resell.py, resell_browser.py |
| `github` | GitHub / Dev Swarm | §13 | github.py, peers.py |
| `games` | Games | §14 | games.py |
| `homelab` | Services | §15 | homelab.py |
| `network-security` | Network Security | §16 | security.py |
| `agent` | AI Assistant | §17 | agent.py |
| `research` | Research Lab | §18 | research.py |
| `library` | Library | §19 | library.py |
| `graph` | Knowledge Graph | §20 | graph.py |
| `settings` | Settings | §21 | settings.py, system.py, prompts.py, node.py |

Deep-link aliases in `renderView()`: 
- Finance sub-panes: `treasury`/`money`/`crypto`/`wallets` → `finance` (dispatch to matching pane)
- Studio sub-tabs: `image-gen`/`videos`/`audio`/`models3d`/`models` → Studio sub-tabs
- Etsy/Printify sub-tabs: `proposals`/`review`/`approved`/`published`/`store-stats`/`products` → Etsy/Printify sub-tabs

Shared (no nav tab): auth.py, llm.py, tasks.py → §Shared Infrastructure.

---

## 1. Dashboard

**Purpose:** Landing overview — pipeline stats, running generations, dev-swarm rollup, portal status, services up/down, plus the **universal queue** controls (also shown bottom-left + header everywhere).

- **Sub-views:** none (single page).
- **Frontend:** `renderDashboard()` in `static/js/tab-dashboard.js` (200+ ln).
- **Backend:** `app/routers/dashboard.py` (214 ln). Queue data comes from `app/orchestrator.py`.
- **Endpoints:** `G /api/stats` · `G /api/status` · `G /api/queue` · `P /api/queue/pause` · `P /api/queue/resume` · `P /api/queue/clear` · `G /api/store-stats`
- **DB tables owned:** none (aggregates others').
- **External services:** none directly (fans out to other tabs' APIs client-side).
- **Docs:** BOOK.md "One GPU, many jobs — queue + restart coordination", app/GPU_QUEUE.md.

## 2. The Company (world)

**Purpose:** Gamified pixel-art company town whose agents are bound to REAL store jobs — "the game runs the store." Includes the God Console safety backbone (prayers approval queue, budget, community board), skills, mood, raids, research, construction, item economy, taste-learning.

- **Sub-views:** main canvas map + **God Console** (12 tabs, `WORLD_CONSOLE_TABS` in `world-economy.js`): Prayers (`god`), Workboard, Priorities (`work`), Control, Republic, Board, Bible, Finances, Roster, Research, Schedule, Settings. Plus Play-God edit mode (`worldToggleEdit`) and sound mixer (`worldSndPanel`).
- **Frontend (23 files):** `tab-world.js` 508 · `world-render.js` 1142 · `world-map.js` 872 · `world-ui.js` 318 · `world-god.js` 258 · `world-audio.js` 213 · `world-wildlife.js` 172 · `world-npcs.js` 149 · `world-control.js` 142 · `world-board.js` 139 · `world-worktab.js` 136 · `world-actions.js` 133 · `world-roster.js` 126 · `world-republic.js` 121 · `world-economy.js` 117 · `world-factory.js` 115 · `world-assets.js` 111 · `world-workboard.js` 101 · `world-bible.js` 86 · `world-edit.js` 80 · `world-buildings.js` 60 · `world-mobs.js` 51 · `world-character.js` 50
- **Backend routers:** `app/routers/world.py` (517 ln), `app/routers/world_ops.py` (558 ln, Treasury is now a pane of §3 Finance).
- **Supporting modules (the whole `app/world_*.py` family):**
  - `world_defs.py` 234 — shared kernel: constants, agent roster, seeding, logging
  - `world_sim.py` 441 — simulation engine (real-elapsed-time ticks, pay for real work)
  - `world_ticker.py` 122 — daemon-thread cadence, independent of UI
  - `world_orchestra.py` 135 — macro-clock conductor above the per-agent sim
  - `world_ops.py` 526 — God Console / safety backbone (prayers, budget, ledger, PayPal)
  - `world_auto.py` 693 — autonomous creation loop (agents make real media)
  - `world_control.py` 231 — unified automation control plane (master/system toggles)
  - `world_strategy.py` 354 — The Republic: assess → propose → convene strategy engine
  - `world_gov.py` 212 — voice & governance: thoughts, opinions, town meetings
  - `world_bible.py` 233 — nation scripture (canonical Word = BOOK.md)
  - `world_taste.py` 168 — god-taste online ML (learns your bless/deny)
  - `world_learn.py` 114 — adaptive layer (online policy learning)
  - `world_skills.py` 192 — RuneScape-style gathering skills + stock targets
  - `world_work.py` 151 — RimWorld-style work-priority scheduler
  - `world_schedule.py` 60 — 24-hour town timetable (Sleep/Work/Free/Anything)
  - `world_mood.py` 142 — mood thought-ledger + mental breaks
  - `world_items.py` 280 — item catalog, inventory, food, shop trips, placements
  - `world_construct.py` 395 — construction lifecycle (`world_structures` rows)
  - `world_build.py` 274 — pixel-art asset generation via ComfyUI
  - `world_vision.py` 141 — vision model judges generated sprites
  - `world_raid.py` 684 — raid combat v3 (waves, walls, turrets, duels)
  - `world_security.py` 291 — store-wide log scanning → security beats
  - `world_research.py` 142 — research tree with prerequisites
  - `world_tech.py` 97 — material ladder (wood→stone→bronze→iron→steel)
  - `world_sell.py` 174 — real paid listings on Etsy/Printify (spends real money)
  - `world_systems.py` 95 — achievements, incidents, housekeeping
  - `world_balance.py` 103 — every tuning number in one place
  - `world_settings.py` 103 — typed layer over `settings` (world_* keys)
- **Endpoints — world.py:** `G /api/world/settings` · `P /api/world/settings` · `P /api/world/cognition` · `G /api/world/state` · `P /api/world/agent/{agent_id}/rename` · `P /api/world/agent/{agent_id}/assign` · `P /api/world/placement/move` · `P /api/world/stock/target` · `P /api/world/schedule` · `P /api/world/research` · `P /api/world/build/order` · `P /api/world/work/priority` · `P /api/world/raid` · `P /api/world/raid/standdown` · `P /api/world/think` · `P /api/world/opinion` · `P /api/world/meeting` · `P /api/world/directive/{directive_id}/resolve` · `P /api/world/agent/{agent_id}/want` · `P /api/world/prop/{prop_id}/generate` · `P /api/world/agent/{agent_id}/buy` · `G /api/world/layout` · `P /api/world/wear` · `P /api/world/layout` · `G /api/world/agent/{agent_id}/log` · `G /api/world/snapshot`
- **Endpoints — world_ops.py:** `G /api/world/ops/summary` · `G /api/world/ops/ledger` · `G /api/world/taste` · `P /api/world/taste/test` · `P /api/world/ops/config` · `P /api/world/ops/budget/entry` · `G /api/world/ops/prayers` · `P /api/world/ops/prayers` · `P /api/world/ops/prayers/{pid}/approve` · `P /api/world/ops/prayers/{pid}/reject` · `G /api/world/ops/gates` · `P /api/world/ops/gates` · `G /api/world/ops/messages` · `P /api/world/ops/messages` · `P /api/world/ops/messages/seen` · `P /api/world/ops/paypal/config` · `P /api/world/ops/paypal/verify` · `P /api/world/ops/paypal/withdraw` · `G /api/world/ops/board` · `G /api/world/ops/auto-config` · `P /api/world/ops/auto-config` · `P /api/world/ops/auto-run-now` · `G /api/world/control/panel` · `P /api/world/control/master` · `P /api/world/control/system` · `P /api/world/control/trigger` · `P /api/world/control/sell-config` · `G /api/world/ops/workboard` · `G /api/world/republic/state` · `P /api/world/republic/convene` · `G /api/world/bible/word` · `G /api/world/bible/teachings` · `P /api/world/republic/strategy/{sid}/override`
- **DB tables:** from db.py: `world_agents`, `world_props`, `world_events`, `world_meta`, `world_ledger` (sim-coins — NOT real money), `world_suggestions`, `world_meetings`, `world_directives`, `world_achievements`. Per-module: `world_structures`, `world_build_orders` (world_construct) · `world_inventory`, `world_placements` (world_items) · `world_threats`, `world_walls` (world_raid) · `world_policy` (world_learn) · `world_taste` · `world_thoughts` (world_mood) · `world_bible` · `world_skills`, `world_stock_targets` (world_skills) · `world_work_priority` (world_work) · `world_prayers`, `world_messages`, `world_ops_ledger` (world_ops — REAL money) · `world_beats` (world_security) · `world_strategy_state`, `world_strategies` (world_strategy). **Gotcha:** real budget lives in `world_ops_ledger`, not the sim's `world_ledger`.
- **External services:** LM Studio (thoughts/strategy/vision LLMs), ComfyUI on GPU box (sprite generation), PayPal REST (`app/paypal_client.py`, withdrawals), Etsy/Printify (via world_sell → §7 clients). Sprite/asset files under `world_agents/`.
- **Docs:** app/WORLD.md, app/WORLD_GAME.md, app/WORLD_ROLES.md, app/RIMWORLD_RESEARCH.md; BOOK.md "The Company runs the store — autonomous system".

## 3. Finance (unified real-money dashboard)

**Purpose:** One roof for all money management — treasury reserves, real-dollar missions, mainnet wallets, and crypto/stock markets. Sub-tab panes (same mechanism as Settings/Crypto) that lazy-load and stay in the DOM.

- **Sub-tabs (Finance panes):**
  - **Overview** — net-worth strip aggregating Treasury/Missions/Wallets/Markets data; status health checks
  - **Profit & Loss** — Printify→Etsy margin analysis (P&L per period: all/30d/mtd)
  - **Bills** — real household bills, due dates, payment tracking
  - **Treasury** — the national reserve, ops ledger, budget entries, PayPal withdraw
  - **Missions & Earn** — money missions pipeline (proposed/approved/done), Cash App lead hunting
  - **Wallets** — real mainnet light wallets (BIP39 derivation, balances from public explorers, Monero)
  - **Markets** — crypto desk (regtest mining, freqtrade strategies, Kraken, stocks watchlist) with its own nested sub-tabs (Stats/Nodes/Mining/Trading/Stocks/Backups)
  
- **Frontend:** `static/js/tab-finance.js` (main shell) · `tab-bills.js` (renderBills) · `tab-treasury.js` (renderTreasury) · `tab-money.js` (renderMoney) · `tab-wallets.js` (renderWallets) · `tab-crypto.js` (renderCrypto — Markets pane).

- **Backend routers:** 
  - `pnl.py` 17 (Profit & Loss) · 
  - `world_ops.py` 558 (Treasury) · 
  - `money.py` 550 (Missions) · 
  - `wallets.py` 478 (Wallets) · 
  - `crypto.py` 1059 (Markets/Crypto)

- **Supporting modules:** `app/paypal_client.py` (PayPal REST) · `app/pnl.py` (P&L engine) · `app/wallet_lib.py` 237 (BIP39, UTXO logic).

- **Endpoints — pnl.py:** `G /api/pnl`
- **Endpoints — treasury (world_ops.py):** `G /api/world/ops/summary` · `G /api/world/ops/ledger` · `G /api/world/ops/budget/entry` · `G /api/world/ops/config` · `P /api/world/ops/paypal/config` · `P /api/world/ops/paypal/verify` · `P /api/world/ops/paypal/withdraw`
- **Endpoints — money.py:** `P /api/money/signals` (X-Money-Token guarded) · `G /api/money/signals` · `P /api/money/review` · `P /api/money/leads/hunt` · `G /api/money/missions` · `P /api/money/missions` · `P /api/money/missions/{mid}/approve/reject/done` · `G /api/money/stats`
- **Endpoints — wallets.py:** `G /api/wallets` · `G /api/wallets/seed` · `P /api/wallets/seed/ack` · `P /api/wallets/seed/import` · `G /api/wallets/sends` · `P /api/wallets/send` · `P /api/wallets/sends/{send_id}/prepare` · `P /api/wallets/sends/{send_id}/broadcast` · `P /api/wallets/sends/{send_id}/cancel` · `P /api/wallets/xmr/setup` · `P /api/wallets/xmr/daemon/start` · `G /api/wallets/xmr`
- **Endpoints — crypto.py:** `G /api/crypto/stats` · `G /api/crypto/nodes` · `G /api/crypto/mining` · `P /api/crypto/mining/config` · `P /api/crypto/mining/{action}` · `G /api/crypto/backup` · `G /api/crypto/trading` · `P /api/crypto/trading/strategy/propose/backtest/approve/reject` · `G /api/crypto/stocks` · `G /api/crypto/kraken` · `P /api/crypto/kraken/sync-freqtrade` · `G /api/crypto/settings` · `P /api/crypto/settings`

- **DB tables:** `world_ops_ledger` (Treasury) · `money_signals`, `money_missions` (Missions) · `wallet_sends`, `wallet_meta` (Wallets) · `crypto_strategy_drafts`, `crypto_backtests` (Markets).

- **External services:** PayPal REST, Bitcoin Core regtest Docker (RPC 127.0.0.1:8332), freqtrade API (127.0.0.1:8898), Kraken API, Robinhood/yfinance (read-only), public block explorers (wallet balances), Monero daemon+wallet-rpc (local), LM Studio (mission drafting, strategy ideas).

- **Docs:** HARDENING.md §3 "Money correctness"; memory note store-money-crypto; tests/test_ledger.py, test_pricing.py; BOOK.md "Portal → WordPress".

## 4. Oracle (LLM Forecasting Tournament)

**Purpose:** Competing LLM "analysts" predict crypto & stock prices across multiple horizons (1d–30d), earn accuracy scores, remember lessons, and climb a leaderboard. No real money moves; consensus is an advisory signal for the Company's strategy, money reviews, and crypto drafts.

- **Sub-tabs (Oracle panes):** Leaderboard (ranked analysts, run-round controls) · Open calls (predictions awaiting their horizon) · Results (resolved predictions, scored ✓/✗).
- **Frontend:** `static/js/tab-oracle.js` 800+ ln · `tab-oracle-history.js` 200+ ln (detailed call history charts).
- **Backend package:** `app/routers/oracle/` (split from former monolithic file)
  - `_base.py` — router, schema, ladder, helpers
  - `forecast.py` — round tournament + `/api/oracle/round`, `/status`
  - `scoring.py` — resolution/scoring + `/api/oracle/resolve`
  - `agents.py` — roster, leaderboard, predictions, memory views/mutations
  - `consensus.py` — accuracy-weighted consensus signal advisory + `/api/oracle/consensus`
  - `auto.py` — cadence (15-min resolve tick, daily round) + `/api/oracle/settings`
- **Endpoints:** `G /api/oracle/leaderboard` · `G /api/oracle/agents` · `G /api/oracle/round` · `G /api/oracle/round/status` · `P /api/oracle/round/start` · `G /api/oracle/predictions` · `G /api/oracle/predictions/{pid}` · `P /api/oracle/predictions` · `P /api/oracle/resolve` · `G /api/oracle/consensus` · `G /api/oracle/settings` · `P /api/oracle/settings`
- **DB tables:** none explicitly (schema in oracle_base.py; data likely in a `oracle_*` tables family).
- **External:** Multiple local LLM models (loaded via model_registry), searxng for research (127.0.0.1:8899), real-world crypto/stock APIs for resolution (yfinance, Kraken, etc.).
- **Docs:** memory note store-oracle.

## 5. Studio (Image / Video / Audio / 3D / Queue)

**Purpose:** All media creation + the models that power it. Each engine sub-tab renders its generator with its model catalog appended underneath (`appendStudioModels`); Queue shows the unified GPU queue.

- **Sub-tabs (`STUDIO_SUBS` in app-nav.js):** `image` (🎨 Image) · `video` (🎬 Video) · `audio` (🎵 Audio) · `3d` (🧩 3D) · `gpu` (⚡ Queue). Legacy `models` view normalizes to `image`.
- **Frontend:** `tab-image-gen.js` 157 · `tab-videos.js` 669 · `tab-audio.js` 168 · `tab-models3d.js` 422 · `tab-models.js` 820 (model catalogs/downloads) · `renderStudio()` dispatcher in `app-nav.js` · parts of `card-actions.js` 636 (design approve/regen modals).
- **Backend routers:** `generate.py` 259, `designs.py` 220, `videos.py` 324, `audio.py` 239, `models3d.py` 554, `models.py` 313.
- **Supporting modules:** `services.py` 655 (background image/chain generation jobs), `services_media.py` 698 (video diffusers Wan/LTX/CogVideoX + MusicGen/MMS-TTS/Stable Audio/ACE-Step + video→audio bridge), `gen_models.py` (per-product-type LoRA/upscaler selection), `img_cutout.py` (sticker background knockout), `render3d.py` (headless turntable renders), `orchestrator.py` + `gpu_scheduler.py` (§Shared).
- **Endpoints — generate.py:** `P /api/generate` · `G /api/generations` · `P /api/enhance-prompt` · `P /api/research-image` · `P /api/research-prompt` · `P /api/designs/{design_id}/generate-listing` · `P /api/ai/suggest-price`
- **Endpoints — designs.py:** `G /thumb/{sub}/{filename}` (WebP thumbnails) · `G /api/designs` · `PA /api/designs/{design_id}/approve` · `D /api/designs/{design_id}` · `PA /api/designs/{design_id}/reject` · `PA /api/designs/{design_id}/send-to-review` · `P /api/designs/{design_id}/regen` · `D /api/designs/{design_id}/unpublish`
- **Endpoints — videos.py:** `G /api/video-chains` · `G /api/video-chains/{chain_id}` · `P /api/video-chains` · `D /api/video-chains/{chain_id}` · `P /api/video-chains/{chain_id}/compile` · `P /api/videos/chain-prompts` · `G /api/videos` · `G /api/videos/{vid_id}` · `P /api/videos/generate` · `D /api/videos/{vid_id}` · `G /api/video-health` · `P /api/videos/{vid_id}/cancel` · `P /api/videos/{vid_id}/add-audio` · `P /api/videos/{vid_id}/retry` · `P /api/video-chains/{chain_id}/cancel`
- **Endpoints — audio.py:** `G /api/audio-models` · `P /api/audio-models/{key}/download` · `D /api/audio-models/{key}/download` · `G /api/audio-models/{key}/download-status` · `P /api/audio/enhance-prompt` · `G /api/audio/engines` · `G /api/audio` · `P /api/audio/generate` · `D /api/audio/{clip_id}`
- **Endpoints — models3d.py:** `G /api/models3d/config` · `P /api/models3d/config` · `P /api/models3d/scan` · `G /api/models3d` · `G /api/models3d/counts` · `G /api/models3d/gen-models` · `P /api/models3d/gen-models/{key}/test` · `G /api/models3d/gen-models/{key}/test-status` · `P /api/models3d/gen-models/{key}/install` · `G /api/models3d/gen-models/{key}/install-status` · `G /api/models3d/{model_id}` · `PA /api/models3d/{model_id}` · `D /api/models3d/{model_id}` · `P /api/models3d/{model_id}/render` · `P /api/models3d/{model_id}/hero` · `P /api/models3d/{model_id}/propose` · `PA /api/models3d/{model_id}/approve` · `PA /api/models3d/{model_id}/reject` · `P /api/models3d/{model_id}/publish` · `P /api/models3d/generate` · `P /api/models3d/enhance` · `G /api/public/m3d/{token}/{model_id}/{kind}/{filename}` (token-guarded public asset route for Cults3D)
- **Endpoints — models.py:** `G /api/models/registry` · `P /api/models/idle-ttl` · `P /api/models/registry` · `P /api/models/{filename}/download` · `D /api/models/{filename}/download` · `G /api/models/{filename}/download-status` · `G /api/models` · `G /api/loras` · `G /api/extra-models` · `G /api/video-models` · `P /api/video-models/{key}/download` · `D /api/video-models/{key}/download` · `G /api/video-models/{key}/download-status`
- **DB tables:** `generations`, `designs`, `videos`, `video_chains`, `audio_clips`, `models3d` (all in db.py).
- **External:** ComfyUI (`http://GPU_HOST:8188`) for images, GPU node python envs for video/audio engines (SSH to 127.0.0.1), LM Studio for prompt enhancement, TripoSR image→3D on the GPU box, HuggingFace model downloads. Output dirs: `designs/`, `videos/`, `models3d/`.
- **Docs:** BOOK.md "3D Studio + Cults3D Pipeline", "3D Generation Models — READ THIS", "Audio / Music + Video Sound", "Model Storage on the SSD", "GPU & Model Management"; app/GPU_QUEUE.md.

## 6. Private Studio (Adults-only generation)

**Purpose:** Separate gallery for all adults-only generation work (image/video/audio/3D), reusing the same pipelines as the regular Studio but with a private archive. Content here is redactable from public surfaces via the `nsfw_display` toggle.

- **Sub-tabs:** Image · Video · Audio · 3D (mirror of regular Studio sub-tabs, but via `/api/nsfw/*` endpoints).
- **Frontend:** `static/js/tab-nsfw.js` 500+ ln.
- **Backend:** `app/routers/nsfw.py` 400+ ln.
- **Visibility control (server-enforced):**
  - `nsfw_enabled` OFF → routes 404, nav item hidden, tab unreachable.
  - `nsfw_display` OFF → nav item hidden from sidebar, content redacted everywhere (but jobs keep running silently — "screen-share safe").
- **Endpoints:** `G /api/nsfw/status` · `P /api/nsfw/{sub}/generate` (image/video/audio/3d) · `G /api/nsfw/library` · `P /api/nsfw/settings`
- **DB tables:** same design/video/audio/models3d tables as §5, tagged with NSFW flags.
- **External:** same as Studio (ComfyUI, GPU node, LM Studio, HuggingFace).
- **Docs:** Settings → Content toggles (`nsfw_enabled`, `nsfw_display`, `nsfw_world_ok`).

## 7. Etsy / Printify

**Purpose:** The POD pipeline: trend scan → proposals → generate design → review → approve → publish to Printify/Etsy; store stats and live product management.

- **Sub-tabs (`tabs` in tab-etsy-printify.js):** Proposals · Review · Approved · Published · Stats (`store-stats`) · Products.
- **Frontend:** `tab-etsy-printify.js` 63 (shell) · `card-actions.js` 636 (the actual pipeline cards + approve/publish/regen/etsy/edit modals + Printify images manager).
- **Backend routers:** `proposals.py` 97, `etsy.py` 130, `printify.py` 143, `trends.py` 56.
- **Supporting modules:** `app/etsy_client.py` 241 (Etsy API v3 OAuth2 PKCE client), `app/printify.py` 235 (Printify API), `app/trends.py` 172 (Google Trends + Reddit RSS + custom RSS → LLM-filtered proposals), `app/fix_etsy_prices.py` (one-off price-fix script; contains hardcoded creds — candidate for cleanup), `app/gen_models.py` (shared with §5 Studio).
- **Endpoints — proposals.py:** `G /api/proposals` · `P /api/proposals` · `PA /api/proposals/{proposal_id}/approve` · `P /api/proposals/{proposal_id}/enhance-prompt` · `PA /api/proposals/{proposal_id}/reject`
- **Endpoints — etsy.py:** `PA /api/etsy/listings/{listing_id}` · `G /api/etsy/status` · `G /api/etsy/connect` · `G /api/etsy/callback` (OAuth) · `D /api/etsy/disconnect` · `P /api/etsy/publish`
- **Endpoints — printify.py:** `G /api/printify/shops` · `G /api/printify/products` · `PA /api/printify/products/{product_id}` · `P /api/printify/publish` · `G /api/printify/images` · `D /api/printify/images/{image_id}` · `D /api/printify/products/{product_id}`
- **Endpoints — trends.py:** `G /api/trends/status` · `G /api/trends/config` · `PA /api/trends/config` · `P /api/trends/scan`
- **DB tables:** `proposals` (db.py); shares `designs` with §5 Studio.
- **External:** Etsy API v3 (OAuth2 PKCE), Printify REST API, Google Trends (pytrends), Reddit RSS, custom RSS feeds, LM Studio (proposal filtering).
- **Docs:** app/etsy_client.md, app/printify.md, app/trends.md; tests/test_pricing.py (calc_retail_price).

## 8. Portal → WordPress

**Purpose:** Pushes curated external/affiliate products and a media portfolio to the example.com WooCommerce/WordPress site. Curate-then-push model with 7 curated sources + affiliate program tracking.

- **Sub-views:** none (sections within the tab: status, affiliate items, programs, curated items, WP products, portfolio).
- **Frontend:** `static/js/tab-portal.js` (509 ln).
- **Backend:** `app/routers/portal.py` (751 ln), `app/wc_client.py` (217 ln — WooCommerce REST over HTTPS Basic Auth + WordPress MCP transport).
- **Endpoints:** `G /api/portal/status` · `P /api/portal/config` · `G /api/portal/affiliate` · `P /api/portal/affiliate` · `PA /api/portal/affiliate/{item_id}` · `D /api/portal/affiliate/{item_id}` · `G /api/portal/programs` · `P /api/portal/programs` · `PA /api/portal/programs/{pid}` · `D /api/portal/programs/{pid}` · `G /api/portal/items` · `P /api/portal/push` · `P /api/portal/portfolio` · `G /api/portal/wp-products` · `D /api/portal/wp-products/{pid}`
- **DB tables:** `portal_affiliate`, `portal_pushes`, `portal_programs` (db.py).
- **External:** WooCommerce REST API (`/wp-json/wc/v3`, https://example.com), WordPress MCP (easy-mcp-ai plugin, `localhost:8090` — NOT example.com, OAuth-discovery trap). Cloudflare caches uploads (gotcha).
- **Docs:** BOOK.md "Portal → WordPress"; memory notes store-portal-wordpress, wordpress-mcp.

## 9. Social

**Purpose:** Social post drafts generated from store media; manual posting workflow (draft → copy → mark-posted). No platform APIs post automatically.

- **Sub-views:** status filter pills (All / Drafts / …) in `tab-social.js`.
- **Frontend:** `static/js/tab-social.js` (338 ln).
- **Backend:** `app/routers/social.py` (258 ln).
- **Endpoints:** `G /api/social/platforms` · `P /api/social/config` · `G /api/social/posts` · `P /api/social/posts` · `PA /api/social/posts/{pid}` · `P /api/social/posts/{pid}/mark-posted` · `D /api/social/posts/{pid}` · `G /api/social/media` · `P /api/social/generate`
- **DB tables:** `social_posts` (db.py).
- **External:** LM Studio (caption generation) only.

## 10. Mail & Quotes

**Purpose:** Reads customer email from the self-hosted Mailcow mailbox (support@example.com), drafts labor quotes with the local LLM per Acme Carpentry terms, sends replies.

- **Sub-views:** none (inbox list + message view + draft panel).
- **Frontend:** `static/js/tab-mail.js` (117 ln).
- **Backend:** `app/routers/mail.py` (264 ln).
- **Endpoints:** `G /api/mail/config` · `P /api/mail/config` · `G /api/mail/inbox` · `G /api/mail/message/{uid}` · `P /api/mail/draft-quote` · `P /api/mail/send`
- **DB tables:** none (settings `mail_*` keys; attachments to `mail_attachments/`, mounted at `/mail-attachments`).
- **External:** Mailcow IMAP (127.0.0.1:993) + SMTP submission (127.0.0.1:587), LM Studio.

## 11. Cults3D

**Purpose:** Cults3D marketplace integration — connection test + listing of published creations. The publish path itself lives in §5 Studio's `models3d.py` (`/api/models3d/{id}/publish` → `createCreation` mutation, assets fetched by Cults3D from the token-guarded `/api/public/m3d/...` route).

- **Sub-views:** none.
- **Frontend:** `static/js/tab-cults3d.js` (142 ln).
- **Backend:** `app/routers/cults3d.py` (57 ln), `app/cults.py` (shared GraphQL client, HTTP Basic auth: username + API key).
- **Endpoints:** `P /api/cults3d/test` · `G /api/cults3d/creations`
- **DB tables:** none (uses `models3d` from §5 Studio; settings for creds).
- **External:** Cults3D GraphQL API.
- **Docs:** BOOK.md "3D Studio + Cults3D Pipeline"; memory note store-cults3d-3d-pipeline.

## 12. Resell

**Purpose:** Photograph → AI-analyze → list physical items on marketplaces. Listing content generation, photos, offers inbox with AI replies, and real-browser posting automation via Chrome DevTools Protocol.

- **Sub-tabs (tab-resell.js):** 🆕 New Listing · 📋 Listings · 💬 Offers (unread badge) · ⚙️ Preferences.
- **Frontend:** `tab-resell.js` 823 · `tab-resell-browser.js` 207 (live browser panel: screenshot/inspect/fill).
- **Backend routers:** `resell.py` 527, `resell_browser.py` 560.
- **Supporting modules:** `app/browser.py` (CDP driver of a persistent-profile headed Chrome; profile in `browser-profile/`).
- **Endpoints — resell.py:** `P /api/resell/analyze` · `P /api/resell/scan-directory` · `G /api/resell/listings` · `P /api/resell/listings` · `G /api/resell/listings/{lid}` · `PA /api/resell/listings/{lid}` · `D /api/resell/listings/{lid}` · `P /api/resell/listings/{lid}/generate-content` · `P /api/resell/listings/{lid}/post-ebay` · `P /api/resell/listings/{lid}/photos` · `G /api/resell/listings/{lid}/photos` · `D /api/resell/listings/{lid}/photos/{photo_id}` · `PA /api/resell/listings/{lid}/photos/{photo_id}/primary` · `P /api/resell/research` · `P /api/resell/listings/{lid}/post` · `G /api/resell/tasks/{task_id}` · `G /api/resell/offers` · `P /api/resell/offers` · `PA /api/resell/offers/{offer_id}` · `D /api/resell/offers/{offer_id}` · `G /api/resell/monitor/status`
- **Endpoints — resell_browser.py:** `G /api/resell/browser/status` · `G /api/resell/browser/activity` · `P /api/resell/browser/launch` · `P /api/resell/browser/quit` · `G /api/resell/browser/screenshot` · `P /api/resell/listings/{lid}/browser-post` · `G /api/resell/browser/inspect` · `P /api/resell/listings/{lid}/browser-fill` · `P /api/resell/offers/{offer_id}/ai-reply` · `P /api/resell/offers/{offer_id}/send-reply` · `P /api/resell/inbox/read`
- **DB tables:** `resell_listings`, `resell_listing_images`, `resell_offers`, `resell_auto_tasks` (db.py).
- **External:** marketplaces via the real Chrome browser (eBay, Facebook Marketplace, etc.), LM Studio (vision analysis, content, offer replies).
- **Docs:** RESELL_PLAN.md; tests/test_browser_session.py (stale-tab-restore regression).

## 13. GitHub / Dev Swarm (+ Peers)

**Purpose:** Repo management via gh CLI, the dev→master→retail promotion workflow (with the retail scrub), and the local-model agent swarm that runs coding jobs; plus the peer network (invite-key pairing, advisory peer reviews, lent compute).

- **Sub-views:** section pills (`_ghSection` in tab-github.js): swarm / repos / etc.; peers UI is inside this tab.
- **Frontend:** `static/js/tab-github.js` (824 ln).
- **Backend routers:** `github.py` 1004, `peers.py` 798.
- **Supporting modules:** `app/swarm.py` 937 (Phase-2 state-machine engine, local models one-at-a-time through the orchestrator), `app/retail_scrub.py` 156 (public-branch scrub: identifier map + leak-gate; imported lazily by the Promote handler; self-drops from retail).
- **Endpoints — github.py:** `G /api/github/status` · `P /api/github/auth/login` · `P /api/github/auth/logout` · `P /api/github/repo/collaborator` · `P /api/github/repo/setup-own` ("Make this install yours") · `G /api/github/repos` · `G /api/github/repo` · `G /api/github/repo/contents` · `G /api/github/repo/readme` · `G /api/github/repo/file` · `P /api/github/repo/create` · `G /api/github/workflow` · `G /api/github/llm-models` · `G /api/github/loaded-model` · `P /api/github/load-model` · `G /api/github/swarm-config` · `P /api/github/swarm-config` · `G /api/github/jobs` · `P /api/github/jobs` · `G /api/github/jobs/{jid}` · `PA /api/github/jobs/{jid}` · `D /api/github/jobs/{jid}` · `P /api/github/questions/{qid}/answer` · `P /api/github/jobs/{jid}/approve` · `P /api/github/jobs/{jid}/reject` · `G /api/github/jobs/{jid}/system-tasks` · `P /api/github/system-tasks` · `P /api/github/system-tasks/{tid}/approve` · `P /api/github/system-tasks/{tid}/reject` · `P /api/github/jobs/{jid}/ask-system` · `P /api/github/jobs/{jid}/run` · `P /api/github/jobs/{jid}/promote` (dev→master→retail incl. scrub) · `P /api/github/restart-live`
- **Endpoints — peers.py:** `G /api/peers` · `P /api/peers/invite` · `P /api/peers/connect` · `P /api/peers/{pid}/approve` · `P /api/peers/{pid}/config` · `D /api/peers/{pid}` · `G /api/peers/connection-info` · `G /api/peers/{pid}/model-check` · `G /api/peers/{pid}/status` · `P /api/peers/reviews/{rid}/vote` · `P /api/peers/{pid}/review-job` · `G /api/peers/review-requests` · `P /api/peers/review-requests/{rid}/refresh` · `P /api/peers/{pid}/test-job` · plus the remote-facing RPC surface (X-Peer-Key self-guarded, session-exempt): `P /api/peers/rpc/pair` · `G /api/peers/rpc/models` · `G /api/peers/rpc/ping` · `P /api/peers/rpc/review` · `G /api/peers/rpc/review/{rid}` · `P /api/peers/rpc/job` · `G /api/peers/rpc/job/{jid}`
- **DB tables:** `swarm_jobs`, `swarm_events`, `swarm_questions` (db.py); `swarm_system_tasks` (github.py); `peers`, `peer_invites`, `peer_jobs`, `peer_reviews`, `peer_review_requests`, `peer_rpc_log` (peers.py).
- **External:** GitHub (gh CLI + API), LM Studio (swarm coding models, load/unload), remote peer Store installs (HTTPS RPC).
- **Docs:** docs/DEV_SWARM.md; BOOK.md "Git / GitHub workflow — READ THIS"; tests/test_retail_scrub.py, tests/test_peers.py; memory notes store-github-swarm, store-peers-federation, store-retail-scrub.

## 14. Games

**Purpose:** Game engine workbench (Godot/Unity/Unreal project pipeline on the GPU node). Build, test, and publish games; manage engine versions and asset libraries.

- **Sub-views:** projects list, build/test/publish controls, engine/SDK management.
- **Frontend:** `static/js/tab-games.js` 500+ ln.
- **Backend routers:** `games.py` 1000+ ln, `games_publish.py` 700+ ln (asset publishing).
- **Endpoints:** `G /api/games` · `P /api/games` · `G /api/games/{gid}` · `PA /api/games/{gid}` · `D /api/games/{gid}` · `P /api/games/{gid}/build` · `G /api/games/{gid}/build-status` · `P /api/games/{gid}/test` · `P /api/games/{gid}/publish` · `G /api/games/engines` · `P /api/games/engines/{eid}/install`
- **DB tables:** `game_projects`, `game_builds`, `game_publishes` (in-router).
- **External:** GPU node (SSH to 127.0.0.1), game engine SDKs (Godot/Unity/Unreal), asset stores.
- **Docs:** BOOK.md "Game Development Pipeline"; memory note store-games-engine.

## 15. Services (homelab)

**Purpose:** Unified Docker + *arr homelab hub — every container/service grouped and controllable, with manual entries and overrides.

- **Sub-views:** none (grouped service cards).
- **Frontend:** `static/js/tab-homelab.js` (161 ln).
- **Backend:** `app/routers/homelab.py` (370 ln). (Note: `app/services.py` is §5 Studio background jobs, NOT this tab.)
- **Endpoints:** `G /api/homelab/services` · `P /api/homelab/override` · `P /api/homelab/manual` · `D /api/homelab/manual/{mid}` · `G /api/homelab/arr/{name}` · `G /api/homelab/config` · `P /api/homelab/config`
- **DB tables:** `homelab_overrides`, `homelab_manual` (in-router schema).
- **External:** Docker (gotcha: force `/var/run/docker.sock` — docker-context trap), Sonarr/Radarr/etc. *arr APIs.
- **Docs:** memory note store-services-homelab.

## 16. Network Security

**Purpose:** Security Command Center — one Command view calling all 14 defenses (`app/defense.py`), plus per-domain engines: connections intel, threats, audit, web traffic, Guardian device control, AI Shield, Pi-hole DNS, system/LLM access.

- **Sub-tabs (`secTab` in network-security.js):** 🛡️ Command (default) · 🌐 Connections · 🚨 Threats · 🔍 Audit · 🌍 Web Traffic · 🐺 Guardian · 🤖 AI Shield · 🧿 DNS (Pi-hole) — with pills Overview/Logs/Devices/Findings/Blocklist · 🔐 System & LLM.
- **Frontend:** `static/js/network-security.js` (872 ln).
- **Backend router:** `app/routers/security.py` (693 ln).
- **Supporting modules:** `defense.py` (unified defense status/toggles — §Shared too) · `secaudit.py` 427 (native hardening audit) · `netguard.py` (Network Guardian: device fingerprint/name/block) · `netwatch.py` (live connection intel in/out) · `pihole.py` 114 (Pi-hole v6 API client, session auth) · `aishield.py` (AI-stack defenses, 4 fronts) · `scheduler.py` 207 (background security monitor — §Shared).
- **Endpoints:** `G /api/security/posture` · `G /api/security/defenses` · `P /api/security/defenses/toggle` · `G /api/security/ai/surface` · `G /api/security/ai/bots` · `P /api/security/ai/scan` · `G /api/security/ai/anomalies` · `G /api/security/guardian` · `P /api/security/guardian/block` · `P /api/security/guardian/unblock` · `P /api/security/guardian/name` · `G /api/security/guardian/actions` · `G /api/security/connections` · `G /api/security/audit` · `G /api/security/web-traffic` · `G /api/security/threats` · `G /api/security/events` · `P /api/security/audit/run` · `G /api/security/status` · `P /api/security/scan` · `G /api/security/findings` · `P /api/security/findings/{fid}/review` · `G /api/security/report` · `G /api/security/monitor/config` · `P /api/security/monitor/config` · `G /api/security/overview` · `G /api/security/logs` · `P /api/security/monitor/tick` · `G /api/security/profile` · `P /api/security/clients/{ip}/flag` · `P /api/security/ban` · `P /api/security/allow` · `P /api/security/unban` · `G /api/security/blocklist` · `G /api/security/actions` · `P /api/security/analyze` · `G /api/security/llm-access`
- **DB tables:** `security_scans`, `network_clients`, `automation_log`, `pihole_actions`, `security_findings` (db.py); `security_snapshots`, `security_events` (secaudit.py); `net_devices`, `net_actions` (netguard.py).
- **External:** Pi-hole v6 API (docker container `pihole`), Docker, UFW/system tools, LM Studio (AI threat hunt, per the `security_model` registry slot). This box = server @ 127.0.0.1.
- **Docs:** BOOK.md "Security"; network-security/ dir; tests/test_defense.py; memory note store-network-security.

## 17. AI Assistant

**Purpose:** Free-form chat with the local LLM inside the Store UI.

- **Frontend:** `renderAgent()` in `static/js/app-nav.js`.
- **Backend:** `app/routers/agent.py` (54 ln).
- **Endpoints:** `P /api/agent/chat`
- **DB tables:** none. **External:** LM Studio.

## 18. Research Lab

**Purpose:** Multi-agent research orchestration — run concurrent deep-research jobs on any topic, aggregate findings, fact-check across sources, and synthesize reports.

- **Sub-views:** active jobs list, research reports, source management.
- **Frontend:** `static/js/tab-research.js` 800+ ln.
- **Backend:** `app/routers/research.py` 300+ ln.
- **Supporting modules:** `research_lab.py`, `research_synthesis.py` (multi-source aggregation + synthesis).
- **Endpoints:** `G /api/research` · `P /api/research` · `G /api/research/{rid}` · `PA /api/research/{rid}` · `D /api/research/{rid}` · `P /api/research/{rid}/run` · `G /api/research/{rid}/status` · `G /api/research/{rid}/sources` · `P /api/research/{rid}/synthesize`
- **DB tables:** `research_jobs`, `research_sources`, `research_findings` (in-router).
- **External:** Searxng (127.0.0.1:8899), multiple LLM models (research + synthesis), web fetching for source validation.
- **Docs:** memory note store-research-lab.

## 19. Library

**Purpose:** Curated link library + full web-page archive (snapshots), page ripping, AI guides/summaries/audits, file browsing of `app/library/` content.

- **Sub-views:** browse / manage / archive panels (one JS file each).
- **Frontend:** `lib-core.js` 22 (shell) · `lib-browse.js` 166 · `lib-manage.js` 158 · `lib-management.js` 98 · `lib-archive.js` 240.
- **Backend:** `app/routers/library.py` (336 ln), `app/library.py` (content root `app/library/` — dir excluded from this index per scope).
- **Endpoints:** `G /api/library/sections` · `G /api/library/search` · `P /api/library/links` · `G /api/library/links` · `G /api/library/links/{link_id}` · `PA /api/library/links/{link_id}` · `P /api/library/links/{link_id}/review` · `D /api/library/links/{link_id}` · `G /api/library/render` · `G /api/library/read` · `P /api/library/archive` · `P /api/library/archive/upload` · `G /api/library/archive` · `G /api/library/archive/versions` · `G /api/library/archive/{snapshot_id}/view` · `D /api/library/archive/{snapshot_id}` · `P /api/library/rip` · `P /api/library/guide` · `P /api/library/import` · `G /api/library/audit` · `P /api/library/audit/ai` · `G /api/library/meta` · `P /api/library/enrich` · `P /api/library/summarize` · `G /api/library/{category}` · `G /api/library/{category}/{sub}` · `G /api/library/{category}/{sub}/{path:path}`
- **DB tables:** `library_links`, `archive_snapshots` (db.py).
- **External:** arbitrary web fetches (archive/rip), LM Studio (guides/summaries).
- **Docs:** app/library.md; BOOK.md "Web Archive — save any page".

## 20. Knowledge Graph

**Purpose:** Queryable knowledge graph of the whole repo (graphify) — graph version of the book/bible. Also exposed to OpenClaw via MCP.

- **Sub-views:** stats/report/query panels + the native force-directed explorer (`window.GE`).
- **Frontend:** `tab-graph.js` 290 · `graph-explorer.js` 167.
- **Backend:** `app/routers/graph.py` (427 ln).
- **Endpoints:** `G /api/graph/stats` · `G /api/graph/viz` · `G /api/graph/report` · `P /api/graph/query` · `P /api/graph/explain` · `P /api/graph/path` · `P /api/graph/affected` · `G /api/graph/scopes` · `G /api/graph/subgraph` · `G /api/graph/export` · `G /api/graph/highlights` · `P /api/graph/rebuild` · `G /api/graph/rebuild/status`
- **DB tables:** none (data lives in `graphify-out/` — ignored dir; built by graphify-venv; `setup.sh --with-graphify`).
- **External:** graphify toolchain (local venv), LM Studio (graph builds/queries).
- **Docs:** memory note store-graphify; ~/.claude/skills/graphify.

## 21. Settings

**Purpose:** All configuration: server/system admin, model registry, integrations (API keys), store & content defaults, account/password, and the LLM prompt workbench.

- **Sub-tabs (`settingsSub` in tab-settings.js):** 🖥️ System · 🧠 Models · 🔗 Integrations · 🏪 Store & Content · 🔒 Account · 📝 Prompts.
- **Frontend:** `tab-settings.js` 851 · `admin.js` 449 (server config, backups, restart, sign-out panel mounted into `#admin-panel-slot`).
- **Backend routers:** `settings.py` 219, `system.py` 339, `prompts.py` (router) 60, `node.py` 144.
- **Supporting modules:** `app/prompts.py` 219 (registry of all 29 LLM prompts), `app/model_registry.py` (per-feature model slots incl. `security_model`; default gemma-4-12b-qat), `app/backups.py` (consistent online sqlite snapshots, gzipped, local + off-box), `app/crypto.py` (Fernet secrets-at-rest), `app/config.py` (§Shared).
- **Endpoints — settings.py:** `G /api/settings` · `G /api/settings/llm-models` · `PA /api/settings` · `G /api/settings/server` · `P /api/settings/server` · `G /api/settings/nodes` · `P /api/settings/nodes` · `G /api/product-types` · `P /api/product-types` · `D /api/product-types`
- **Endpoints — system.py:** `G /api/system/backups` · `P /api/system/backup` · `P /api/system/db-backup` · `G /api/system/db-backup/status` · `G /api/system/backups/{name}/download` · `D /api/system/backups/{name}` · `P /api/system/restore` · `G /api/system/info` · `G /api/system/gpu-status` · `G /api/system/logs` · `P /api/system/restart` · `P /api/system/browser-reset` · `G /api/system/update-status` · `P /api/system/update-config` · `P /api/system/update-apply`
- **Endpoints — prompts.py:** `G /api/prompts` · `PA /api/prompts/{key}` · `P /api/prompts/{key}/reset` · `P /api/prompts/{key}/test`
- **Endpoints — node.py:** `G /api/node/status` · `P /api/node/deploy` · `G /api/node/deploy-log` (deploys the GPU-node services; see deploy/node/)
- **DB tables:** `settings` (db.py — the app-wide key/value store; secret values Fernet-encrypted).
- **External:** SSH to GPU box (node deploy, gpu-status), off-box backup target, git (self-update).
- **Docs:** BOOK.md "Node Hosts + Deploy", "Store Logs + error handling", "LLM model picker"; HARDENING.md §2/§4; tests/test_backups.py, test_prompts.py, test_crypto.py; memory notes store-prompt-registry, store-model-registry.

---

## Shared / cross-cutting infrastructure

| File | What it does | Who depends on it |
|---|---|---|
| `app/main.py` (≈190 ln) | App assembly: includes all 37 router modules, auth-guard middleware (session + bypasses: `/api/public/*`, `POST /api/money/signals`, `/api/peers/rpc/*`, localhost `/api/*`), SessionMiddleware (cookie derives from STORE_BASE so /store & /store-dev don't clash), catch-all exception logger, static mounts (`/designs`, `/videos`, `/static`, `/mail-attachments` via `CachedStaticFiles`), **MCP mount at `/api/mcp`** (fastapi-mcp, mounted after all routers → exposes every endpoint as a tool; rides the localhost auth bypass — how OpenClaw drives the Store) | everything |
| `app/deps.py` | Shared kernel — config, DB/setting helpers, clients, LLM helper, prompts; re-exported via `from deps import *` | every router |
| `app/config.py` | Central config, all env-overridable (`STORE_*`): GPU_HOST=127.0.0.1, LLM_URL (LM Studio :1234/v1), COMFYUI_URL (:8188), PUBLIC_BASE_URL=http://localhost:8787, PIHOLE_CONTAINER, Cults license, paths | everything |
| `app/db.py` | sqlite connection + the master schema (37 CREATE TABLEs listed per-section above) | everything |
| `app/cache.py` | In-process TTL cache for slow external calls (WooCommerce, docker ps, SSH); bust with `invalidate_prefix` on writes | portal, homelab, security |
| `app/crypto.py` | Fernet encryption for secret settings at rest | settings, all credentialed integrations |
| `app/prompts.py` | Registry of all 29 LLM system prompts (`get_prompt(key)`), editable via Settings→Prompts | every LLM-calling module |
| `app/orchestrator.py` | GPU orchestrator — single worker sharing the GPU between LM Studio and ComfyUI; the unified queue's engine | studio, swarm, world, dashboard queue |
| `app/gpu_scheduler.py` | Pure scheduling core (priority + model affinity + anti-starvation aging), no side effects | orchestrator |
| `app/scheduler.py` | Background security-monitor scheduler (Pi-hole snapshots, config scan, AI threat hunt) — DB-toggleable | security |
| `app/defense.py` | Unified status/toggles for all 14 background defenses | security Command view, tests |
| `app/retail_scrub.py` | Public-branch scrub map + leak-gate for the Promote button; self-drops from retail | github promote |
| `app/backups.py` | Automated consistent DB snapshots (local + off-box) | system.py, defense.py |
| `app/model_registry.py` | Per-feature model slots powering Settings→Models + queue model chip | all model-using features |
| `app/world_ticker.py` | World daemon-thread ticker (started at app startup) | world |
| `app/routers/auth.py` | Login/logout pages + session, `P /api/auth/change-password`; endpoints: `G /` · `G /login` · `P /login` · `G /logout` | SPA shell |
| `app/routers/llm.py` | OpenAI-compatible LLM proxy: `G /api/llm/v1/models` · `P /api/llm/v1/embeddings` · `P /api/llm/v1/chat/completions` — routes external callers (OpenClaw) through the store queue | OpenClaw, peers |
| `app/routers/tasks.py` | Generic background-task polling: `G /api/task/{task_id}` · `D /api/task/{task_id}` · `P /api/task/{task_id}/retry` | studio, resell, anything using `pollTask()` |
| `static/js/app-nav.js` (125 ln) | SPA shell: `switchView()`/`renderView()` dispatcher, nav sync, deep-link aliases (finance/studio sub-tabs, etsy/printify sub-tabs) | all tabs |
| `static/js/tab-shared.js` (137 ln) | Shared helpers (`pollTask`, etc.) | multiple tabs |
| `static/js/card-actions.js` (636 ln) | Pipeline card actions + modals (approve/publish/regen/etsy/edit) + Printify images manager | etsy-printify, studio |
| `static/index.html` | Nav, layout, script tags (gotcha: scripts load from `/store/static/js` behind nginx) | — |
| `app/init_db.py` | Legacy standalone DB initializer | (superseded by db.py) |

**Serving:** nginx `/store` prefix → uvicorn :8787 (systemd unit `deploy/store.service`); dev instance /store-dev :8788. GPU box 127.0.0.1 (LM Studio :1234, ComfyUI :8188); `deploy/node/` = GPU-node deploy payload.

## Tests (`tests/`, run via `./run_tests.sh`; temp-DB isolated via conftest.py STORE_DATA_DIR)

| Test file | Covers |
|---|---|
| `conftest.py` | fixtures — throwaway data dir, never live store.db |
| `test_api_smoke.py` | all safe GET endpoints return non-5xx (cross-cutting) |
| `test_backups.py` | §21 Settings app/backups.py |
| `test_board_url.py` | §2 world board design-image URL folder resolution |
| `test_browser_session.py` | §12 Resell app/browser.py stale-tab-restore regression |
| `test_crypto.py` | §Shared app/crypto.py secrets-at-rest + settings PATCH/GET |
| `test_defense.py` | §16 Network Security app/defense.py toggles/posture |
| `test_gpu_scheduler.py` | §Shared gpu_scheduler.py pure logic |
| `test_ledger.py` | §2/§3 Finance world_ops budget/ledger real-money math |
| `test_orchestrator_pick.py` | §Shared orchestrator `_pick_pending()` |
| `test_peers.py` | §13 GitHub peer pairing + key-auth scope (`X-Peer-Key` only opens /api/peers/rpc/*) |
| `test_perf_cache.py` | §Shared Cache-Control, thumbnails, TTL cache |
| `test_pricing.py` | §7 Etsy/Printify calc_retail_price money math |
| `test_prompts.py` | §21 Settings prompt registry |
| `test_queue_controls.py` | §1 Dashboard queue pause/start/clear + orchestrator gate |
| `test_retail_scrub.py` | §13 GitHub retail scrub leak-gate |
| `test_world_placements.py` | §2 /api/world/placement/move |
| `ui_regression.py` | end-to-end headless-browser SPA check (not part of pytest run) |

## Docs map

- **BOOK.md** — the living operations book (sections: App Components, Design & Assets, Security, Goals, GPU & Model Management, Model Storage, Audio/Video, Queue coordination, Logs, LLM picker, Web Archive, Modular layout, Portal→WordPress, Git/GitHub workflow, Known Issues, The Company autonomous system).
- **HARDENING.md** — 5-item roadmap, all ✅ (tests, secrets-at-rest, money correctness, off-site backups, GPU scheduler).
- **RESELL_PLAN.md** — §13 plan. **TODO.md / GOAL.md / problems.md** — planning scratch. **README.md** — public-facing overview.
- **docs/DEV_SWARM.md** — §14 swarm design.
- **app/*.md side-docs:** db.md, main.md, orchestrator.md, GPU_QUEUE.md, library.md, trends.md, printify.md, etsy_client.md, fix_etsy_prices.md, WORLD.md, WORLD_GAME.md, WORLD_ROLES.md, RIMWORLD_RESEARCH.md.

## Orphans / strays (files belonging to no live subsystem)

- `app/fix_etsy_prices.py` — one-off script with **hardcoded Etsy creds** (cleanup candidate).
- `app/init_db.py` — legacy initializer, superseded by db.py.
- `app/test_library.py` — stray test living in app/ instead of tests/.
- `app/store.db` — stray DB copy inside app/ (live DB is repo-root `store.db`).
- `app/_generate.sh.reference` — reference script, unused.
- All 36 real routers ARE assigned to sections above; `routers/__init__.py` is empty.

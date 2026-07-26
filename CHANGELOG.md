# Changelog

All notable changes to **Store Command Center**.

Everything autonomous is **gated and defaults off**. Three floors never move, in any version:
real money movement (payouts / withdrawals / transfers) always requires an explicit human
action; the minors/CSAM and non-consensual-intimate-imagery protections are always-on and
**not** toggleable; autonomy only ever operates *inside* the existing approval gates.

---

## [2.1.0] — 2026-07-25

### Added — features
- **Prompt-to-Television** — the Director pipeline now produces shows/episodes with canon and a
  Phase-3 timeline audio track; the TV view shows box art, banner & blurb with gallery fixes.
- **Live game-editor integration** — editor-MCP wiring for **Unreal, Unity, and Godot**
  (multi-root scanning), plus a GPU-guard **editor co-op mode** so an open engine editor no
  longer pauses the media queue.
- **Jarvis voice assistant** for the AI Assistant tab.

### Fixed — bugs
- `llm_borrow` now respects the chain-long video hold — stops mid-chain out-of-memory errors.
- Dev-swarm: defined a missing logger and added a last-resort pinned-model eviction.
- Director poll flicker (keyed cards + skeleton/dynamic editor split); video gallery flicker
  during generation and single-video audio parity with the chain.

## [2.0.0] — 2026-07-24

### Added — features
- **Video Studio** — storyboard prompt → scenes / shots → **matched, layered audio** (a TTS
  voiceover over background music + sound effects) → export. Short and long videos; clips stitch
  into scenes, scenes into a film.
- **Multi-platform social publishing** — **YouTube**, **TikTok**, and **Instagram / Facebook**
  adapters (gated, opt-in). Post-analytics feed back into the taste model.
- **Content loop** — trends → auto-storyboard, a meme quick-mode (idea → short + caption + audio),
  a real auto-scheduler, and analytics → taste.
- **GitHub & Dev Swarm — "The Engineers"** — an autonomous local-model dev crew: propose → build
  on the dev branch → review (you *or* reviewer-swarm consensus) → `dev → main` pipeline → apply
  to live. Per-project workboards, a shared collaborator branch, model loading through the unified
  GPU queue, and a per-email delete in the mail view.
- **Reworked God Panel** — a 30-capability catalog in tidy groups, a live agent-loops graph, and
  optional god-tier **lieutenants**: **✝️ Jesus** (a constructive delegating operator) and
  **😈 Satan** (his adversarial red-team mirror) — together they give every prediction/review/
  forecast a calibrated best- **and** worst-case band. Both default **off**; both held to the
  exact same gates and floors as every agent.
- **Company HQ rework** — an Iron/Steel-age multi-section complex with saved progression stages.
- **Image sizing / export** — download any design at Etsy-spec or web sizes.
- **Income tracking** — manual entry plus **read-only** PayPal / Printify / on-chain importers
  (money-*in* visibility only; no autonomous spend path anywhere).
- **Per-model VRAM gating for video** — models that won't fit the GPU fail fast with a clear message.

### Fixed — bugs
- **Unified GPU queue** — fixed VRAM starvation from out-of-queue model loads (including the dev
  swarm); scheduler anti-starvation for image/video work.
- **Long-format video** now reliably compiles multiple clips into one file.
- **Public "show company" world snapshot** now frames the whole map, centered (was a corner).
- NSFW prompt-enhancer false-refusal; image-gen prompt quoting; TikTok transcode + creator-info
  gating; social media picker filtering; Etsy clean "reconnect needed" state; rogue-agent watch
  false positives; design path-integrity repair.

### Safety — always-on
- Real money movement always requires explicit human approval.
- Minors/CSAM and non-consensual-intimate-imagery protections are always-on and not toggleable.
- New autonomous features default **off** and act only inside the existing gates.

## [0.1.0] — 2026-07-20

Initial public release — the dashboard, **The Company** pixel-art town, the buddy system, local
image/video/audio/3D generation on a unified GPU queue, the storefront + services pipeline,
JellyCoin, and the full gates-and-toggles system. See the [README](README.md) and the
[wiki](../../wiki) for the complete reference.

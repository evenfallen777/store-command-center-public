# Work Tracker — big rework arc (started 2026-07-21)

## ▶ RESUME HERE — post-v2 (2026-07-22)

**STATE:** RDP crash RESOLVED. Everything from the big arc is **committed** — local only, NOT pushed to public.
- Last commit: `f50ca2a` **v2** (32 files, +4734/-115) on top of `619a3b1` checkpoint.
- Also local-only/unpushed: `b1edf20` (Knowledge Hub).
- `origin` = the GitHub public-mirror repo → **never `git push`**; public goes only via the retail-scrub → promote flow.
- Store healthy (`systemctl --user restart store`; `/store/` → 303). Working tree clean.
- Secrets safe: all creds in gitignored encrypted `store.db`; `.secret_key` ignored; no media/secrets in tree.

**SHIPPED in v2 (all gated default-OFF where autonomous; money/minors/NCII floors intact):**
Video Studio audio-mix fix · TikTok+YouTube publish + analytics · gated social auto-scheduler ·
agents make real content (idle-dept fix) · analytics→taste · HQ Iron/Steel rework · God Panel 30-cap
catalog + live agent-loops graph · ✝️ Jesus god-tier autopilot (default OFF).

**OUTSTANDING TODO — owner picks next:**
_owner setup status (updated 2026-07-22):_
- [x] **YouTube** — ✅ CONNECTED + test post WORKED (owner confirmed).
- [~] **TikTok** — review done, **application submitted**; awaiting TikTok's approval (publish path built + demo delivered).
- [⏸] **Etsy — PARKED** — owner can't remove the old Etsy app on Etsy's side; **skip until fixable**. (Agent-3's reconnect-CTA makes this a graceful "reconnect needed" state instead of 400 spam.)
- [ ] **Meta/Instagram** — create a Meta dev app to activate the adapter (Agent-1 building the adapter now).

_🟢 BUILT + integrated on master, smoke-tested, awaiting **v2.1** commit (2026-07-22, 4-agent parallel build):_
- [x] **Meta/Instagram adapter** — `social_publish/instagram.py` (IG Reels + FB Page), OAuth + registry, NSFW hard-blocked, no auto-publish. `/api/social/instagram/status` → 200. **Owner activation:** create a Meta app → save `meta_app_id`/`meta_app_secret` → Connect.
- [x] **Income Phase 2** — READ-ONLY PayPal/Printify/on-chain importers into the income table, idempotent, daemon default-OFF, **no spend path**. `/api/income/import/status` → 200 (PayPal already configured via fallback creds).
- [x] **Video VRAM gate** — mirrors the 3D gate; blocks Wan-14B/CogX-5b/Hunyuan on the 12 GB node with a clear message instead of OOM.
- [x] **Etsy reconnect-CTA** — clean "Reconnect Etsy" state + logs-once instead of 400 spam (arms when token goes stale; Etsy currently still valid).
- [x] **Jesus + God Panel follow-ups** — Jesus publish-caps (file-only, paid excluded), shadow "would-do" stream (`world_jesus_shadow`), WorkGiver, per-agent cap grants, swarm-role graph nodes. All default-OFF; every gate/floor verified intact. Endpoints → 200.

_✝️😈 GOVERNANCE / CHECK-AND-BALANCE LOOP (owner vision 2026-07-22 — the "snake eating its tail"):_
- [~] **Satan** — adversarial/worst-case god-tier lieutenant, mirror of Jesus; dual best/worst/expected band wired into taste + reviews + Oracle + prayer verdicts; yin/yang graph (owner centered, Jesus + Satan flanking layer 0); two-lieutenant owner digest; NSFW-domain gate (default OFF; CSAM/minors + NCII floors immovable & enforced against Satan); no spend path. **BUILDING now → will land as v2.2.**
- [ ] **Red-team / Blue-team engineers** (QUEUED, next after v2.2) — extends the duality to the eng/security tier. RED = adversarial static auditor, LOOK-ONLY (no attacking/exploitation): audits store code + WordPress + web front + system, emits findings w/ references (CVE/OWASP), referrals, reviews, Q&A for blue. BLUE = defender/fixer, routes fixes through the existing gated Engineers dev-swarm (gate-the-merge, human approval before live). AUDITOR = findings lifecycle open→triaged→fixing→fixed→verified + audit trail. Q&A = structured red↔blue channel + escalation to owner. Red feeds Satan's worst-case layer; blue feeds Jesus's constructive layer. Builds on netsec dept + swarm consensus-review. Default-OFF; no auto-deploy; no offensive action.

_✅ ALL buildables DONE (committed surgically, tree clean, local-only — NOT pushed since v2.1):_
- **v2.2 `5992db9`** — 😈 Satan + dual-verdict band + security-registry fix.
- **v2.3 `75ee7b1`** — 🔴🔵 Red/Blue-team loop + onboarding docs. ⚠️ also swept in a Knowledge-Hub consolidation (tab-knowledge.js +4 nav files) that was ANOTHER SESSION's WIP — parses+runs; owner aware; leave unless they want it split out.
- **v2.4 `aab163d`** — 🏛️ Iron/Steel building set + town rework + ⛏️ ore / 🎣 fishing (fixed 4 unreachable venues; NSFW store gated).
- **v2.5 `f556760`** — ☁️ Cloudflare purge endpoint + closed a `/api/settings` cf_api_token leak. (NOTE: a smoke-test accidentally fired one real cache purge on example.com — low-impact, disclosed.)
- **v2.6 `0d70f22`** — 🎨 naming-theme switch (themed ↔ neutral), display-only, default themed.

_LESSON (owner flagged): NEVER `git add -A` — multiple Claude sessions share this tree. Stage each build's reported files explicitly, verify nothing foreign is staged._

_checkpoint (2026-07-23, post-CLAUDE.md): session work pushed to private backup (store-command-center, master → 0ea774f) + offline backups refreshed. Then:_
- `572f2e5` **feat(video)** — uniform chain segments (_conform_segment; node WanVideoToVideoPipeline height/width fix needs node redeploy) + per-model gen tuning (defaults == today). Live openclaw videogen scripts synced separately (backward-compat).
- **Income Phase 2** — dry-run verified READ-ONLY; PayPal + Printify configured & ready; on-chain idle until `income_wallet_address` set; auto-import OFF. (Owner chose dry-run, no live calls.)
- **WordPress-for-Meta** — dropped (owner: not needed).
- `572f2e5` is local-only (post-push); not pushed per CLAUDE.md (needs explicit OK).

_CONVENTIONS NOW (per /CLAUDE.md): Claude→master; NO vX.Y labels; NEVER git add -A (multi-session tree — stage explicit paths); don't touch /VERSION; no push without explicit OK. Surgical staging already caught a foreign db_schema.py edit mid-commit (2026-07-23)._

_📌 checkpoint 2026-07-23:_ v2.2 committed (`5992db9`) + pushed to private online repo (store-command-center) + offline backups refreshed (tarball+bundle in ~/backups/store/). Public repo (store-command-center-public) got a **docs-only** v2 notice + new CHANGELOG.md (`a541919`) — public CODE deliberately NOT updated. **Red/Blue-team build still queued next.**

---

## 📜 ARCHIVED — earlier resume notes (superseded by v2)
### paused 2026-07-22 to fix a server/VS Code RDP crash — unrelated to the store
**🏢 HQ REWORK — owner direction given 2026-07-22 (Fable building):**
- **Progression STAGES**: the HQ saves its CURRENT look + systems as a "progression stage" (era snapshot), then a whole NEW stage is built on top. HQ evolves through ages; old one preserved as an earlier stage. Tie to the existing `world_construct.py` tech-tier system.
- **New stage = entering the IRON/STEEL AGE** with some modern-times elements mixed in — whole new style, look, shape, layout.
- **Multi-section complex, NOT one big square/rectangle**: distinct connected sections — **warehouse & shipping dept**, **office area**, **utilities**, + other/ext. Extend the WB wall-ring renderer to a compound/multi-wing footprint; keep interiors (desks + agents) visible per section.

**🔁 CONTENT-LOOP FEATURES — owner greenlit all 5 (2026-07-22), building via subagents:**
1. **🔥 Trends → auto-Storyboard** — pipe Google/Reddit/RSS trends into the Director inbox → AI drafts video ideas. (Fable: Studio cluster)
2. **🏭 Company agents produce REAL content** — social/media/visuals agents actually generate a real video/post via the Studio when they "work" → also fixes the idle-dept bugs. (Queued — needs working Studio render.)
3. **📊 Post analytics → taste model** — pull YouTube/TikTok views/likes post-publish, feed the taste model. (Fable: Social cluster)
4. **⏰ Real auto-scheduler** — auto-publish scheduled posts at their set time (currently just a reminder). (Fable: Social cluster)
5. **😂 Meme quick-mode** — drop a meme idea → instant short w/ caption + audio, skip full storyboard. (Queued — needs Studio.)
- **Dept bugs** (fixed by #2): social agents idle "on the clock" with no completing task; **visuals dept shows work going with nothing in queue**.
- **⚠️ Studio Phase 2 BUG**: `studio_2_final.mp4` rendered ~0 bytes, video-only, NO audio — the scene-assemble/audio-mix step is broken. Fable-fix first before #2/#5 build on it.

**🎬 VIDEO STUDIO — designed (Fable), scope growing, NOT built yet:**
- Blueprint: `docs/VIDEO-STUDIO-DESIGN.md` (storyboard → scenes/shots → layered audio → export; reuses video_chains + audio engines + queue; new 🎞️ Director sub-tab; 4 phases; smallest slice = idea→1 scene×3 shots→music+VO→draft post). Caught latent bug: chain segments don't inherit `video_chains.nsfw`.
- Requirements captured across the arc: matched+layered audio (voiceover reads script/captions via TTS + background music + SFX); AI writes the audio script from the same video prompt; short+long videos; **long-video compile — FIXED** (concat fallback); storyboard inbox (drop prompt→storyboard→tweak→render); scenes stitched from clips, multiple scenes stitched. **NEW asks (2026-07-22):** manual **local-media upload** (bring-your-own footage into the library/composer), **caption edit/add** on videos, **audio overlay** onto a video. Captions + audio-overlay already live in the Fable blueprint; manual upload is a clean addition.

**🧰 UNCOMMITTED this session (owner will commit at v2 launch — do NOT auto-commit):**
- **Image sizing system** — `app/image_sizes.py` (new), auto-derive in `services.py`, `GET /api/designs/{id}/export?spec=etsy|web`, Etsy publish uploads the 1024² variant. Etsy=1024²/≤5MB JPEG, Web=1600px. Verified (4096²→1024²/1600²).
- **Size/quality download menu** — `tab-image-gen.js` `_igCard`: ⬇ Download ▾ → Etsy / Web / Full master.
- **NSFW image-model selector** — `routers/nsfw.py` honors `nsfw_image_model` setting; `tab-nsfw.js` dropdown persists via PATCH /api/settings + sends `model` in generate POST.
- **NSFW enhancer CoT-strip** — `routers/nsfw.py` `_extract_prompt()` (fixes "same thing back" false-refusal).
- **Delete/Regenerate failed images** — `generate.py`: `DELETE /api/generations/{id}` + `POST /api/generations/{id}/retry` (clones params). `tab-nsfw.js`: Regenerate + Delete buttons on failed image cards (were dead cards); `nsfwDelete` learned the `generations` kind.
- **Generation quoting fix** — `~/.openclaw/tools/imagegen/generate.sh`: `${PROMPT@Q}` (shell-quote, broke rich prompts) → JSON-encoded. NOT in repo.
- **Queue fix (#2)** — `orchestrator.py` `llm_borrow()` + `world_gov.py`/`world_security.py` now borrow-only through the queue, so world-sim can't JIT-load an LLM outside it (was starving image/video VRAM via LM Studio's 30m TTL). Freed a stuck `gemma-4-12b` (was holding 8.4GB).
- Model rec: download **DarkIdol-Llama-3.1-8B-Instruct-Uncensored** (or Cydonia 24B) in LM Studio on the node → set in `nsfw_model` slot.

Store build queue is DONE + committed (last commit `820bdb4`). Nothing store-side is mid-flight. When we pick back up:

**🙋 Waiting on owner (setup, not code):**
- YouTube + TikTok: hard-refresh → Social tab → **Connect** each → run a **private/SELF_ONLY test post**.
- TikTok app form: icon sent; verify domain `example.com` via DNS; record demo of the private post.
- Etsy: click the re-auth link already in-thread.

**🔨 Buildable on my side (owner picks next):**
- Meta/Instagram publish adapter (one entry in the `_PUBLISHERS` registry now).
- Income Phase 2 importers (PayPal / Printify / on-chain).
- Agent autonomy Phase 1 (agents propose → owner approves).
- Building HQ art direction (needs owner's vibe call).
- Remaining onboarding docs (openclaw / 1-vs-2-PC / plugin guide).

**✅ Side-quest RESOLVED (non-store):** VS Code-over-RDP crash was gnome-remote-desktop running in SYSTEM/headless mode (broken surfaceless GPU path). Fix = switched to USER desktop-share mode (autologin + user RDP). Kernel + VS Code snap were red herrings. Details in [[vscode-rdp-nvidia]] memory.

---


Living status of every request in this arc. **Legend:** ✅ committed+verified · 🟢 done, pending consolidation commit · 🔧 in flight (code) · 🔎 designed/reviewed, NOT built · ⛔ blocked on owner · 📋 queued.

## ✅ Committed & verified
- **Video long-format** — 4 stacked bugs fixed (HF_HOME env, ftfy dep, VAE fp16/fp32 dtype, GPU-exclusivity OOM) + **auto-compile** chains into one video + **compile robust to mismatched segments** + wrappers vendored. Verified: 2-seg chain completes & merges.
- **NSFW → uncensored model routing** for image/video/music/3D prompt-gen (resolves to qwen uncensored). Verified live.
- **Lyrics writer** (Audio tab) — verified generating end-to-end.
- **XMR mining gate** (toggle + agent-access, default OFF) — verified 403 when disabled.
- **Node-down indicator** (topbar dot + banner, /api/node/ping) — verified.
- **3D**: TRELLIS VRAM-gated (clear message) + installer hardened + `generator` column.
- **Donate** — Buy Me a Coffee `acme`: store card (default OFF) + FUNDING.yml (scrub-exempt). Live.
- **Oracle clarity rework** — Forecaster-Leaderboard rename, pointer to town board, tooltips, table-first, Company HUD opens by default.
- **Cults3D** enum fix (Locale→LocaleEnum; Currency/License flagged for live check).
- **Bills** custom-field collectors scoped per form (no cross-bleed).
- **Systems board cleanup** — dead `world_require_review` removed, dups consolidated, invisibles reclassified, `world_public_snapshot` toggle added (default OFF).
- **NSFW attach-picker leak** — /api/social/media now filters nsfw.
- **DEV_PROCESS.md** + **INDEX.md** refresh.

## ✅ Consolidated & committed — full suite 726 passed
- **Scheduler affinity batching** — media jobs join the unified queue; drain resident-model work before evict-reload; anti-starvation + video-hold safe; 36 tests. Your exact lineup → llm×5→image×3→video×2.
- **Oracle configurable assets** — verified live (added KAS/kaspa).
- **NetSec queue-starvation fix** — drain function verified live (50→47 on direct call); Gale dept=netsec; drains steadily as he operates.
- **Moon + HQ-door render** — return button clear of HUD, craters de-blobbed, door flush + orientation-aware; SPA PASS (visual confirm = owner).
- **Agent personas + stacking** — verified live: Delphi/Pythia/Sibyl/Cassandra/Merlin (model id in detail panel); 39/39 distinct sprite offsets.
- **Buddy/peer + queue** — already correct by design (peer jobs borrow-resident, never churn host GPU); peer path verified intact.
- **God Panel rework (Phases 1–3)** — Settings→Interface (hide any tab + game on/off), standalone **Command** tab (god controls without the game canvas), consolidated **Master Breaker** (all gates/switches/automation on one page + honest locked rows). SPA PASS; hard-refresh to see it.
- **Integrations Status board** — Settings→Integrations shows 19 services active/needs-login/not-setup + setup links (no secrets exposed). Verified live.
- **Income Phase 1** — Paychecks→Income (any type + manual entry). Verified live.
- **Receipt / document-extract primitive** — reusable photo→fields via vision model on the unified queue; receipt capture in Purchases (Gas/Fuel + line items). Live-verified end-to-end.
- **Setup wizard** — first-run /setup (password→topology→node test→subsystems→health→setup_complete). Verified.
- *(building)* Cloudflare token wiring + purge · install-script skip-gate hardening.

## 🟢 Live config (not code)
- **Autonomy ON** — flipped `world_ops_gate_creations` OFF (gate ON = waits; OFF = auto-runs). Confirmed producing (design count rose within 90s). Money + posting stay gated.

## Infra / one-offs done
- **Wiki** pushed to public repo (6 pages). **Cloudflare** purged by owner; API token provided → wire into encrypted store + purge endpoint (pending).

## 🔎 Designed / reviewed — NOT built (need owner decision)
- **Social video-publishing** (YouTube/TikTok/etc.) — phased plan, YouTube-first, reuses world_sell/world_taste, gated+default-OFF. Needs owner decision + platform dev-apps. Phase-0 nsfw-picker fix already shipped.
- **Agent-system vision** — Phases 2–4 (gated dept ownership → collaborative review → bounded autonomy). Needs owner decision on autonomy scope.
- **Income auto-import** — phased (PayPal/on-chain/Printify feasible; CashApp manual). Phase 1 (paycheck→income generalize) not built.
- **Setup wizard + install-script sibling-bug fixes + onboarding docs** (openclaw/1-vs-2-PC/plugin guide) — audited, not built.

## 📋 Queued / follow-ups
- Receipt/document-extract reusable primitive (photo/doc → structured fields).
- Etsy reconnect-CTA (stop raw 400 spam) — + owner must re-authorize Etsy.
- WordPress donate block; wire Cloudflare token.
- Building HQ **look** rework (procedural, multi-file) — needs owner direction.
- Wan V2V continuation returns model-native size (compile letterboxes around it) — uniform-segment follow-up.
- Per-model VRAM gate for **video** (like the 3D one).
- Video big models (Wan-14B/CogVideoX-5b) don't fit 12GB node — now fail with a clear message.

## ⛔ Blocked on owner
- Re-authorize **Etsy** (keystring changed → refresh 400). YouTube/TikTok/Meta **dev-apps** for social. Building-look **direction**. Decisions on **social / agent-autonomy / income** plans.

# Store Fix List — started 2026-07-19

## 1. 🖥️ Dashboard overhaul — DONE 2026-07-19 (browser-verified)
`static/js/tab-dashboard.js` (191 lines) only knows the original platform: POD stats,
dev swarm, "generating now", portal/WordPress, homelab services. Missing everything
built since:

- **The Company / world** — population, treasury health, prayers awaiting approval
- **Treasury** — real-$ balance, owed/bill status, spend cap health (`/api/world/ops/summary`)
- **Unified queue** — running/queued jobs + current model chip (`/api/queue`)
- **Money** — proposed missions awaiting approval, new demand signals (`/api/money/stats`)
- **Crypto/Wallets** — wallet total balances, JellyCoin height/supply, Pearl, trading bot status
- **Mail / Quotes** — unread count + new quote requests (`/api/mail/inbox`)
- **Research Lab** — running pipelines / new reports
- **Security Command** — defense summary / threats (`app/defense.py`)
- **Oracle, Assistant, Agent Watcher** pills

Plan: keep the "at a glance" stat-card row but generate it from ALL subsystems, each
card click-through to its tab (pattern already in `stat()` helper). Add an
"awaiting you" strip that merges everything gated on approval (prayers, missions,
swarm needs-you, proposals, quote requests) — that's the real daily driver.

## 2. 📧 Mailbox "test@gmail.com" emails — ANSWERED (not real clients)
Inbox = Mailcow `support@example.com`. 4 messages total:
- uid 1/2: "test email" sent by Wes himself (user@live.com / @gmail.com), Jul 15
- uid 3/4: website quote-form tests from launch day Jul 17 — "Mike in Greenville"
  (`mike.test@gmail.com`, 903-555-0142) and "Dana in Rockwall" (`dana.test@gmail.com`,
  972-555-0177). 555 numbers + .test emails = launch-day pipeline tests, **not customers**.

Optional cleanup: delete the 4 test mails so the first real client is obvious.

## 3. ⚡ LM Studio model bypassing the unified queue — FIXED + LIVE (restarted 2026-07-19)
Audit of every LM Studio call site in `app/` (~38 sites): all run inside
`orch.submit_llm` / `run_llm_job` jobs **except one**:

- `app/routers/jellycoin.py` `POST /api/jelly/missions/draft` called
  `_call_lmstudio()` directly in the request handler → bare JIT model load with
  **no idle-TTL and no queue entry**. That's how a model ends up sitting resident
  in LM Studio with the queue showing empty.
- **Fixed 2026-07-19**: wrapped in `run_llm_job(..., wait=240)` — queued, model
  loaded by the orch with TTL, visible in the queue UI. Import verified.
  ⚠️ Takes effect on next store restart (uvicorn runs without --reload).

Notes (by design, not bugs):
- `/api/llm/v1/chat/completions` proxy has a fast path that streams straight to a
  *resident* model without a queue entry (that's what allows parallel prompts);
  it still calls `orch.mark_activity()` so TTL is honored.
- `sweep_idle_llms()` (scheduler tick) is the backstop that unloads stray no-TTL
  models after `model_idle_ttl` (currently 1800s) of full idle.

## 4. 💰 Unified into one Finance tab — DONE 2026-07-19 (browser-verified; old view ids alias in)
Today four nav items cover one concept:
- **Treasury** (`tab-treasury.js`) — Company real-$ ledger, PayPal, spend cap, health
- **Crypto & Markets** (`tab-crypto.js`) — 8 sub-tabs: stats, JellyCoin, Pearl, nodes, mining, trading, stocks, backups
- **Wallets** (`tab-wallets.js`) — real mainnet light-wallets (BTC/LTC/DOGE/ETH/KAS/XMR), review-gated send
- **Money** (`tab-money.js`) — demand signals → missions, Cash App, real-$ goal

Proposal: one **💰 Finance** tab using the existing sub-tab pane pattern
(`tab-crypto.js` already does exactly this — pane-toggle, lazy-load per pane):

1. **Overview** (new, small) — net worth strip: treasury $ + wallet coin values +
   JLY/PRL + pipeline value, plus "awaiting approval" counts
2. **Treasury** — renderTreasury() as-is
3. **Missions & Earn** — the current Money tab (signals, missions, Cash App)
4. **Wallets** — renderWallets() as-is
5. **Markets** — current Crypto sub-tabs fold in (stats/jelly/pearl/nodes/mining/trading/stocks/backups)

Mechanics: mostly moving render functions into panes + nav consolidation; render
fns are already self-contained. Keep old `switchView('treasury'|'money'|...)`
ids as aliases → open Finance at the right pane so dashboards/deep-links don't break.

## 5. 🔌 Plugin system — DONE 2026-07-19 (spec: ../PLUGIN-SYSTEM.md; /api/plugins live, hello-world pings, suite 280 green)
Drop-in `plugins/<name>/` folders (manifest + optional FastAPI router + optional
frontend view) auto-discovered at boot, so third parties can extend the store and
still pull updates with zero re-wiring. Core touch-points: main.py discovery block
+ `GET /api/plugins`, new `static/js/plugin-loader.js` (registerView/initPlugins),
2-line index.html hook, `default:` plugin dispatch in app-nav.js renderView,
gitignore `plugins/` (keep README). Ships `plugins/hello-world/` example + pytest
coverage. External reference install (BlixLive) will become pure drop-in once merged.

## 6. ⛏️ World agent/player action system — DONE 2026-07-19 (see note below on melee adjacency)
Reported 2026-07-19: agents walk around swinging pickaxes randomly, combat doesn't
visibly interact, and pickaxe is the only tool — every activity needs its own
tool/action visual (combat weapon, gather, sleep pose, work, build…). Plan: fix the
walk-vs-strike gating in every render path (agents/players/NPCs), replace the
pickaxe-as-default with an explicit state→action map covering all backend states
(unknown states degrade to idle, not pickaxe), make duels face/strike/feedback on
real hit events, wire new actions into positional SFX. Key files:
world-render-agents.js (tool map ~L94, strike gating ~L234), world-render-combat.js,
world_sim.py / world_raid.py / world_work.py.

Done: walking-swing root cause was the held-tool overlay animating while pathing
(body was gated, the overlay wasn't); pickaxe-everywhere = only the Crush sheet
(pickaxe baked in) was loaded — Slice/Collect/Fishing/Watering/Carry/Death sheets
now wired via a single `_actionOf` state→action map (pickaxe = mine only; unknown
states → idle). Combat: fighters face/lunge at nearest threat, hit sparks + damage
numbers on real HP deltas, medics no longer lunge; sim bug fixed — boss shockwave
dealt ~0.008 HP via a stray 1/60 through `breach_dps`, now flat BOSS_SMASH_CHIP.

**Deferred design issue — melee adjacency**: fighter posts are an inner ring while
enemies halt at the wall ring, and threat positions are client-interpolated
(server keeps none), so duels still happen "at a distance" bridged by facing/lunge
visuals. Real fix = defense-post geometry assigning fighters to the threatened
wall segment, or server-side threat coordinates.

## 7. 🧍 Per-entity sprite-sheet system + player overhaul — PARTIALLY DONE; ANIMATION BROKEN
⚠️ 2026-07-19 owner report + root cause CONFIRMED: the registry/gates/pack-first
resolution work, but the ANIMATION is fake. `world_sprites.make_action_sheet()`
builds all 4 "frames" from ONE image via rotate/dx,dy/brightness transforms
(_STRIKE/_BOB/_SWAY/_TILT), so a walk is the same sprite swaying — the legs never
move. Owner is right: a walk needs ~3 genuinely different poses like the premade
pack sheets. IN PROGRESS: real per-frame pose generation conditioned on the
entity's base sprite (identity consistency is the hard part), a SHEET SPLITTER so
generated/dropped-in sheets become usable frames, and frame-QA that REJECTS
near-identical frames (pins this exact bug) + identity drift. Pack sheets remain
the quality bar and the fallback.
Reported 2026-07-19: player character has 2 competing looks (generated picture vs
downloaded pack model); the "make your own look" system outputs a static picture —
entities walk around as picture-boxes with backgrounds; generated objects have the
same background problem; anything animated should be a real sprite sheet. Goal: an
on-demand per-entity sprite system — every agent/player/building/object owns a
cached SET of sheets; gaining a new action/object generates that sheet ONCE (queue
+ QA-gated, transparent, never per-use), always animated; downloaded packs library
(static/world_assets/packs — kenney/anokolisa, index.json) finally linked as
browsable/usable assets + fallbacks.

## 8. 🗺️ World tab layout overhaul — game-first HUD — DONE 2026-07-19 (293 tests green; /api/world/hud/skills pending restart; gaps: no real equipment backend, quests = presentation over mandate/prayers/research)
Reported 2026-07-19: current layout is bad design — header + boxed canvas + panels
eat the screen. Goal: the GAME gets the full tab real estate (canvas fills the
view, like the existing fullscreen mode but as the default), and everything else
becomes toggleable overlays (each with a hide toggle, states persisted):
- RuneScape-style skills panel (world_skills.py: XP/levels per skill)
- chat/event feed overlay (world ticker/speech)
- player card / agent card: portrait, armor/equipment (if data exists), inventory
  (world_items.py), current job/state
- quests: active + completed (map to whatever exists — missions/workboard/board;
  stub cleanly + report gaps rather than invent backend)
- skill trees w/ tiers on skill select (world_tech.py tech tree as base)
- company/work progress: treasury slice, workboard, rank/leaders
Existing anchors: tab-world.js (~L185 header, L253 canvas-wrap, fullscreen fns
L149-166), world-ui.js/world-roster.js/world-god.js panels. Overlay HUD should be
a new world-hud.js; keep god console/edit-mode entry points reachable.

## 9. 🔮 Oracle overhaul — DONE 2026-07-19 (ladder 1/3/5/7/14d live, scoring rebalanced, 15-min resolution tick, consensus wired to world_strategy/leaders/crypto-drafts/money-review — all advisory + approval-gated; settings in Oracle tab AND god panel)
Reported 2026-07-19: predictions are far out — horizon_days clamped 7–90
(forecast.py L110) and the prompt/scoring reward long calls. Goal: day-trade
horizon ladder {1, 3, 5, 7, 14} days for a proper picture and more data points
(scoring multiplier rebalanced so short calls are worth making; resolution ticks
must handle 1-day turnaround). Audit + fix the Company hookup — oracle output
should actually be consumed (leaders/strategy/trading signals), gated. Settings
+ gates exposed in BOTH the Oracle tab (store area) and the God panel; god-panel
UI insertion deferred until the item-8 HUD agent releases world-god.js.

## 10. 📜 Unified queue — completed-job history/log — DONE 2026-07-19 (browser-verified; endpoint pending restart)
Reported 2026-07-19: the queue display only shows live jobs (orchestrator tasks
are in-memory — completed work vanishes). Need a persistent completion log +
display: what ran (kind/desc/model), when (queued/started/finished, duration),
outcome (done/error/cancelled), and from whom/what system (source attribution —
submit_llm desc/task prefixes like proxy:/world:/jelly:). DB table written by the
orchestrator on completion (incl. image/ComfyUI jobs where trackable), history
API with filters, and a History view in the queue UI (app-queue.js strip/popover
+ Studio GPU view).

Built: `queue_history` table (db_schema.py) + `app/queue_history.py` (attribution
map + capped writer, keep last 2000) written by the orchestrator at every terminal
transition (done/error/cancelled — `Orchestrator._record_history`, never breaks the
job). Media jobs are NOT double-written — GET /api/queue/history (dashboard.py)
unions generations/videos/video_chains/audio_clips in at read time (source=studio)
with the same NSFW redaction as /api/queue; filters kind/source/status/limit + a
24h by-source/by-status summary. UI: "Recent" section in the strip popover +
filterable History block in Studio → Queue (app-queue.js); both hide quietly while
the live backend predates the endpoint (404 probe). Suite 306 green incl. 7 new
tests. ⚠️ Endpoint + orch writes go live on next store restart (JS is live now).

## 11. 🔌 Plugin system hardening — DONE 2026-07-19 (plugin_host.py, collision/deps/disable guards live, Settings→Plugins pane, 309 tests green)
Reported 2026-07-19: plugins must never break the store, while staying free to do
(within reason) whatever they want. v1 already isolates boot failures + request
exceptions. Overhaul adds: per-plugin enable/disable toggles (persisted, UI);
route-collision guard (plugin may not shadow core or other-plugin routes — refuse
+ mark failed, core keeps working); view-collision guard in registerView; plugin
status surfaced (/api/plugins → loaded/failed/disabled + error + route count);
Settings → Plugins management pane (status, toggles, errors, docs); frontend
isolation (script onerror → ⚠ nav item; PLUGIN_VIEWS dispatch try/catch renders
an error panel instead of breaking nav); manifest `requires` (python deps checked
+ surfaced as warnings, never auto-installed); README contract updated; tests.

## 12. 🌐 WordPress (example.com) overhaul — LIVE 2026-07-19 (user-approved go-live executed; V1 preserved as switchable theme; REMAINING: manual Cloudflare purge, Contact/Our Work/Résumé restyle batch, LICENSE for store-command-center-public)
Reported 2026-07-19: (a) design has AI red flags — 2-color gradients, em-dashes,
generic spacing — user loves it but wants it distinctive; ALL changes as drafts/
preview links for review before anything goes live. (b) Shop needs a full rework
showcasing everything new: fuse carpentry × technology × brand/creativity ×
engineering × software (carpentry services + POD apparel + Cults3D models + free
software/plugins + curated gear). (c) 3D website example: standalone demo link
(Artifact) + same page added as a DRAFT WP page. Access: easy-mcp-ai on
localhost:8090 (Bearer, NOT example.com — OAuth trap), Woo via store portal
creds. Rule: no published page/product/menu is modified — drafts + report only.
USER RULE added 2026-07-19: the old gradient design is NEVER deleted — it is preserved
as a switchable theme: [V1 Gradient] draft copies 889-895 in WP + raw sources in
../wordpress-themes/v1-gradient/ (README has the switch procedure). Go-live = swap,
not overwrite.

## 13. 📆 Real bills tracker — DONE 2026-07-19 (327 tests green, live)
Finance → 📆 Bills pane: bills w/ portal links, fixed or variable amounts, cycles
(monthly/weekly/yearly/quarterly/once/custom-N-days), autopay flag, mark-paid w/
payment history + auto-advance of next_due (month-end clamped, catch-up when long
overdue), custom fields via `extra` JSON, CSV import/export, monthly-total chart.
Backend app/routers/money/bills.py + tests/test_bills.py; UI static/js/tab-bills.js.
Dashboard tie-ins: due-soon stat card + overdue/due-≤3d rows in the awaiting strip.
NOTE: two agents were killed mid-build by a usage limit; a wrong weekly-cadence test
expectation (Thu-start bill expected on a Wed) was fixed, not the code.
Requested 2026-07-19: track actual personal bills — each with its website/portal
link, amount (fixed or variable), due dates/cycles, autopay flag, what's paid,
payment history, spending totals + over-time graphs. Flexible by design (custom
fields, user categories) since the user has "just the basics but others will
need more" (retail installs). No passwords stored (portal links + notes only;
any secret via existing Fernet settings pattern). Surfaces: Finance tab pane +
due-soon on the Dashboard "awaiting you" strip. NOT the world's in-game bills.

## 18. 🗓️ Money calendar + ICS feed (Nextcloud) — DONE 2026-07-19 (437 tests; needs restart)
🗓️ Calendar segment (bill_due/bill_paid/paycheck/purchase chips, month nav, day
detail w/ Mark-paid, month totals). Recurrence reuses advance_due by calling it
with today=<the date itself> so it advances exactly ONE step and inherits the real
month-end clamp; walked dates flagged projected:true, capped 400/bill + 800 days.
ICS: /api/calendar/export.ics (session) + /api/public/calendar.ics?token= (feed,
NO localhost bypass — the URL IS the credential; Fernet-stored, rotatable).
Month-end bills emit BYMONTHDAY=28,29,30,31;BYSETPOS=-1 (last-day-of-month) rather
than a BYMONTHDAY=31 that silently skips February — verified with dateutil.rrule
against advance_due. icalendar was installed to a SCRATCHPAD dir only for
validation; venv + requirements untouched.
OPEN: paycheck cycles are descriptive only, so recurring INCOME is not projected
(no payday forecasting yet) — worth adding if wanted.
Owner 2026-07-19 ("there is a calendar icon but no calendar lol"): add a real
🗓️ Calendar segment to the money pane showing bills due / paid / paychecks /
purchases, with recurring bills PROJECTED forward via the existing advance_due
cycle logic (never reimplemented). Plus export: one-off /api/calendar/export.ics
AND a subscribable feed at /api/public/calendar.ics?token=... — reuses the
EXISTING /api/public/ session-bypass prefix in main.py (no auth change needed);
endpoint self-guards with hmac token like jellycoin _check_miner, token stored
Fernet-encrypted in settings, rotatable. Real RRULEs (not exploded rows), 75-octet
folding, stable UIDs. UI warns the feed URL exposes financial data -> keep it LAN.

## 17. 🎮 Games tab → push a title to the shop — DONE 2026-07-19 (437 tests; needs restart)
app/routers/games_publish.py mounted onto the games router (main.py untouched);
local `game_listings` table + images under DATA_DIR/game_listings/<id>/. Draft is
edited locally, then an explicit push creates/updates a Woo product hard-wired to
status=draft + catalog_visibility=hidden (module constant, asserted pre-request,
test-pinned that "publish" never appears in the payload). Images: node screenshots
(engine noise filtered), owner uploads, or Studio-generated cover art via the GPU
queue (auto-created designs row deleted so covers don't pollute merch review).
Descriptions via orch.submit_llm (never direct LM Studio), suggestion-only, never
auto-saved. _scrub() strips project paths/root/node label/IPv4 before anything
leaves the box. Confirm gate has a toggle per [[gates-get-a-toggle]].
CAVEAT: Woo is NOT connected on this box, so pushes were only ever mocked — eyeball
the FIRST real push in WP admin.
Owner 2026-07-19: Unity projects must NOT be listed publicly anywhere (WP Games
page is generic only). Instead the Games tab gets a "Publish to shop" flow: pick a
project, set title/price/description/photos (screenshots or generated art), then
create the WooCommerce product. Follow the existing curate-then-push portal
pattern (app/routers/portal.py + wc_client) and the approval-gate convention —
create as a Woo DRAFT for review, never auto-publish. Reuse the Studio image
pipeline for cover art.

## 16. 🌐 WP: NEW v3 brand palette LIVE + public showcase pages — IN PROGRESS
2026-07-19: started as "revert to V1 gradient" but the owner redirected mid-run to
a NEW palette and CONFIRMED it after the fact: v3-brand-2026 = blue #0EA5E9 /
green #10B981 / orange #F97316 / navy #0B2545. Purple-pink (#7C3AED/#DB2777) is
retired. Water-drop logo replaced by a "J" tile mark (live via mu-plugin
acme-logo-swap.php because the old logo URLs were Cloudflare-cached ~17h).
LIVE: Home 13, About 16, Shop 818, Services 885, Lab 886. Products 876/877/878 now
have images; 0 of 45 products missing art.
GOTCHA: the image model forced purple backgrounds through 4 rounds of negative
prompts, so the 3 brand graphics are DETERMINISTIC SVG (v3-brand-2026/logo/cards.py
regenerates them) rather than generated.
Purple also lived in 3 site-wide mu-plugins + the Blocksy theme palette — override
is acme-palette.php (delete to revert); backups in
wordpress-themes/mu-plugins-backup-2026-07-19/.
Archives: v1-gradient/ (untouched), v2-blueprint/, v3-brand-2026/ (live sources).
DONE 2026-07-19: all 10 published pages are v3 — the last 4 (Contact 20, Free
Software 800, Our Work 849, Résumé 820) recoloured, archived first as drafts
962-965 + wordpress-themes/v1-gradient/pre-v3-live-2026-07-19/.
WORKAROUND TO RETIRE: the homepage's last purple came from Blocksy/Gutenberg
theme output, not page markup. acme-palette.php now needs 3 layers incl. an
OUTPUT-BUFFER HTML rewrite on every render. PROPER FIX = set the palette in
Appearance → Customize → Colors, then DELETE the whole plugin.
PENDING OWNER (Cloudflare, no CF creds on this box):
- 4 of 5 logo URLs aged out; only /uploads/2022/04/logo-light.svg still stale.
  Purge it, then delete mu-plugins/acme-logo-swap.php.
- ⚠️ USE "PURGE EVERYTHING": the first 3 world snapshots pushed on 2026-07-19 had
  the in-world Feed panel BAKED INTO THE PIXELS (real directives incl. an approval
  line + strategy). Deleted from WP, but they were briefly public and may sit in
  the CF edge cache. Pixel text bypasses all text sanitisation — the render now
  aborts if any text overlays the canvas.
All pages report cf-cache-status: DYNAMIC, so no page purge is needed.
Requested 2026-07-19 (owner prefers the ORIGINAL gradient look over the V2
blueprint): revert Home/About/Shop to V1 gradient from the archive, RE-THEME the
new Services + Lab pages into V1 (keep content, keep #jn-qf form + $40 terms),
shop lanes as V1 cards, audit + generate missing images, and archive V2 as
[V2 Blueprint] drafts + wordpress-themes/v2-blueprint/ (same swap-both-ways rule
as [[gates-get-a-toggle]] spirit — never delete a design, make it a theme).
PLUS three new public pages in V1 style: "The Company — Live World" (READ-ONLY,
no controls), "JellyCoin" (+ links to the public jellycoin-core repo), "Games".
SECURITY DESIGN: the world portal is fed by OUTBOUND snapshot pushes from the
store (headless render -> WP media + sanitized stats). NO public endpoint, no
proxy, no iframe into the private box; toggle world_public_snapshot defaults OFF;
push is skipped whenever gated/private-studio content could be on screen.

## 15. 💵 Bills pane → full money ledger (paychecks + purchases) — DONE 2026-07-19 (369 tests, live)
Segmented control in the Bills pane: 📆 Bills | 💰 Paychecks | 🛒 Purchases | 📊 Overview.
Paychecks: source, net/gross, hours x per-entry rate (🔢 Fill button; explicit amount
always wins), cycle, month/YTD. Purchases: quick-add (today-dated, Enter saves,
category datalist shared with bills), month grouping + per-category totals.
Overview: month + YTD income/outgoings/net + paired-bar chart. CSV both ways.
NO DOUBLE COUNTING by construction: purchases has no bill_id; outgoings =
purchases + the existing bill_payments rows; test-pinned disjoint.
GOTCHA: tests/test_ledger.py was ALREADY the world_ops God-Console ledger — the
new tests live in tests/test_money_ledger.py.
Requested 2026-07-19: track paychecks and purchases alongside bills. New
app/routers/money/ledger.py + create_ledger_tables (appended to db_schema.py);
tab-bills.js gains a Bills | Paychecks | Purchases | Overview segmented control.
Paychecks: source, net/gross, date, hours x rate helper (hourly carpentry work),
recurring cycle, YTD. Purchases: fast entry (date/merchant/amount/category),
month grouping, per-category totals. Overview: income vs outgoings + net + chart.
NO double counting: outgoings = purchases + existing bill_payments rows.

## 14. 🎮 Games tab (Unity / Unreal / Godot) — DONE 2026-07-19 (352 tests green; needs restart)
Built app/routers/games.py + static/js/tab-games.js, 5 panes (Engines/Projects/
Assets/MCP/Docs). Node ssh reuses routers.llm._box_ssh (prepends ~/.local/bin to
PATH — non-interactive ssh doesn't source the profile). Builds ride the unified
queue via orch.submit_llm with NO model/task kwargs so no LLM is ever loaded for a
shell job (test-pinned). Asset export adds _node_put (base64 over stdin) since
_box_ssh has no stdin channel. Verified live: Godot 4.7.1 detected, a real
Smoke_Test project created + booted headless, sprites+GLB exported with sidecar
JSON. LEFT ON NODE: ~/games/Smoke_Test demo project (44KB) — delete if unwanted.
KNOWN LIMIT: headless Godot can't invent export_presets.cfg, so a first export
must be configured once in the editor; the build returns that instruction.
ENGINE INSTALL STATE on the node (127.0.0.1, 121GB free, 16 cores):
- Godot 4.7.1 INSTALLED 2026-07-19 → ~/engines/godot/, symlink ~/.local/bin/godot
- Unity: BLOCKED on user. Old AppImage CDN path is 404; official route is the apt
  repo which needs sudo (node sudo requires a password) PLUS a Unity account for
  the editor license. Commands for the user are in the report.
- Unreal: BLOCKED on user + DISK RISK. Download is behind an Epic account; UE5
  Linux needs ~100-110GB extracted vs 121GB free on the node, which would leave
  almost nothing for LM Studio/ComfyUI models. Recommend an external drive or
  skipping UE until space exists.
Tab spec (user picked ALL panes): one Games tab, sub-tabs per engine + panes for
Asset bridge (reuse sprite registry/models3d/packs → engine-ready), Projects +
builds, Editor MCP, Docs/learning.
Requested 2026-07-19: a dedicated tab for game-engine work. User is unsure of scope
("i really dont know what options, or I would say them all") — so START WITH A
DISCOVERY PASS: inventory what is actually installed/reachable (engine installs on
this box + GPU node, existing projects/repos, Godot/Unity/Unreal MCP servers, asset
libs already in-tree e.g. static/world_assets/packs), then propose the tab layout
before building. Candidate panes: Projects (detect + open/build/run), Engine MCP
(connect the store's assistant to a running editor), Assets (reuse the sprite/3D
pipelines to generate engine-ready assets), Plugins/tooling, Docs/learning, Builds.
Ties into existing systems: the sprite registry (item 7), models3d/Cults3D, the
plugin system (item 11), dev swarm.

---

## 19. 🛣️ Roads/paths vanished from the world — ROOT-CAUSED 2026-07-19
Owner: "haven't seen paths or roads in days." NOT missing art — the terrain image
(static/world_assets/terrain/world_terrain.png, 2640x2080) has a full road network
and /api/world/terrain reports enabled+has_image. The culprit is the INSTALLED
GENERATED TILESET, which overrides terrain per-tile and whose manifest maps only
grass/water/plaza — `path`, `floor`, `wall`, `tree` are all **None**. Unmapped
tiles render as nothing, so the roads disappeared. Atlas dated Jul 18 22:46 (=
"days"); the last regen failed with "no tiles passed — is the GPU box reachable?"
(transient — ComfyUI answers 200 now).
FIXED 2026-07-19: backed up tilesets/ to scratchpad, re-ran the fill with the GPU
up — `path` regenerated in ~20s and passed the gates. Verified the atlas cell is
real grey cobblestone (avg RGB 109,107,103, 1942 distinct colours — NOT another
black tile) and the manifest now maps path -> gen(64,0). Roads render again on
refresh.
NOT A BUG: `floor` and `wall` are deliberately unmapped — the API rejects
generating them ("structural — the renderer keeps its crafted procedural art").
So only TERRAIN tile keys can suffer this failure mode.
DURABLE FIX STILL NEEDED: a PARTIAL tileset must never erase features — an
unmapped tile key should fall back to the terrain image / procedural paint for
that tile instead of drawing nothing. Same failure class as the earlier
black-atlas saga; the QA gate protects individual tiles but nothing guards the
"installed but incomplete" state.

## 20. 🧮 AI budget + grocery planner — DONE 2026-07-19 (543 tests, live)
Built: purchase_items (line items + name normalizer), budget_envelopes,
budget_plans; consumption stats (distinct purchase DAYS so a 2-gallon trip isn't
a fake 0-day interval; MIN_OBSERVATIONS=3; predictive fields explicitly None when
insufficient so no caller reads a missing key as 0; confidence from interval CV);
envelopes/safe-to-spend; calendar events (budget_period/savings_target/
safe_to_spend/restock/grocery_day, flagged projected, EXCLUDED from totals);
AI planner via orch.submit_llm(task="budget_grocery_plan") with a validator that
drops unknown items, matches aliases, clamps qty and REPLACES model prices with
real receipt prices; sample seed/purge keyed on a parsed tag (never LIKE/date).

⚠️ THREE BUGS THAT ONLY APPEARED WITH DATA IN IT — the lesson of the day:
1. INFINITE RECURSION collect_events -> budget events -> compute_period -> bill
   projection -> collect_events. Every level swallowed by try/except, so it
   PASSED ALL TESTS while doing exponential work: the .ics export never returned
   and the suite went 78s -> 716s. This was the mystery slowdown all evening and
   it also caused the phantom test_gpu_guard failures. Fixed via include_budget=False.
2. Safe-to-spend went UP when a bill was paid (paying advances next_due out of
   the period). Committed now = payments + unsettled due dates, counted once.
3. ICS drowned in 30 identical far-future period rows; capped to 4 ahead.

KNOWN LIMITS (owner-facing): useless until ~3 purchases per item; needs a
confirmed pay cycle + 2 paychecks to project income; CANNOT see anything not
itemised (a lump-sum trip feeds envelopes but teaches consumption nothing);
variable-amount bills are excluded from committed so safe-to-spend runs
optimistic while any exist; normalizer keeps "whole milk" and "milk" separate.
Owner 2026-07-19: an AI to manage money/budget for food + gas — post budget and
savings to the calendar, recommend grocery lists, and track WHAT is bought (item
count, price, burn rate) so scheduling gets smarter.
FOUNDATION GAP being fixed: purchases record merchant+total only, NOT line items —
you cannot predict restock without them. Adding purchase_items (+ a name
normalizer so "Milk 1 gal"/"milk gallon" -> milk).
Consumption: avg interval / qty / unit-price trend + predicted next-need, but
REQUIRES >=3 observations — below that it must report insufficient_data with the
count, never a confident guess. Confidence from interval variance, always shown.
Budget: envelopes (food/gas/savings/other, fixed or %) over the owner's real pay
cycle derived from paychecks; expected income - committed bills (reusing the
existing projection, never reimplemented) = disposable -> envelopes -> safe to
spend. No double counting (bills from bill_payments, rest from purchases).
Calendar: new event types through the EXISTING /api/calendar/events so they also
ride the ICS feed — budget period, savings target, safe-to-spend, predicted
restock, suggested grocery day; all flagged `projected`.
AI planner: grounded on the computed figures via orch.submit_llm (prompt in the
registry), validates the returned list against known items and drops/flags
hallucinations, saved as an editable DRAFT — advisory only, never auto-applied.
RULE FOR THIS FEATURE: it is the owner's real money — never fabricate a number.

## 21. ⚡ LM Studio load failures + "agents sending bad prompts" — DIAGNOSED + FIXED
Owner 2026-07-19 21:17: LM Studio "Unexpected endpoint or method (POST
/v1/chat/completions). Returning 200 anyway" + agents behaving oddly.
NOT bad prompts. Two config faults found via the new queue history:
1. Per-task overrides (Settings→Prompts, settings key `task_model_<key>`) pointed
   PROSE tasks at a 30B CODER model: money_lead_hunt, money_gap_review,
   threed_listing -> qwen3-coder-30b-a3b-instruct. A code model doing lead
   research / shop copy = the "acting up" output. 14 such jobs, several errored.
2. FIVE models in rotation (gemma-4-12b-qat, qwen3.5-9b, glm-4.7-flash,
   glm-4.6v-flash, coder-30b) -> constant multi-GB swaps. Errors were literally
   "could not load required model X (loaded=[Y])" / "GPU may be busy".
   Compounding it: qwen/qwen3.5-9b was PINNED resident (unused) while
   gemma-4-12b-qat — the model everything actually uses — was unpinned with
   ttl=900, so it kept being evicted.
FIX APPLIED: cleared ALL 11 task_model_* overrides (backup:
scratchpad/task_model_backup.txt) so every text task uses the global
gemma-4-12b-qat; unpinned qwen3.5-9b; pinned gemma and dropped its ttl.
IN PROGRESS: miner yields to the queue (owner picked "pause mining when busy") —
mirrors the existing gpu_guard pattern in reverse, with hysteresis + a toggle.
NOTE: the running miner on the node must be REDEPLOYED to pick up client changes.

## 22. 🚀 Public release v0.1.0 — DONE 2026-07-20 (session close; 667 tests green)
Full flow executed: `master → retail (scrubbed) → public repo main`. Everything below
is already live — this entry is the handoff, not a task list.

- **Pushed** the pending arc (93 files, +25k lines: bills/budget/ledger, games, plugin
  host, world HUD+sprites, JLY policy/supply/yield, peer billing, oracle consensus).
- **MIT LICENSE** added. Chosen because there's no plan to sell the software (RESELL_PLAN
  is about physical items) and the warranty disclaimer matters for an app that moves real
  money. Copyright must stay a real name — `RETAIL_LEAK_EXEMPT_LINES` in `retail_scrub.py`
  is an exact whole-line allowlist for that one line, pinned by 3 tests. Don't widen it.
- **Dropped `app/library/openclaw-docs`** from the public tree (~684 files / 9.2 MB of
  third-party docs, no licence or attribution — not ours to redistribute). Public clone
  64 MB → 54.7 MB. Library categories come from `iterdir()`, so nothing breaks.
- **README**: Systems section rewritten to match the real nav, plus a new "What's gated"
  section. Two stale counts corrected — 50 prompts (not 29), 7 scheduled defenses (not 14).
- **v0.1.0** tagged + GitHub Release published; repo description + 9 topics set.

### ⚠️ Left open — pick these up next session

1. **WIKI IS BLOCKED ON ONE MANUAL CLICK.** GitHub won't create `<repo>.wiki.git` until a
   human makes the first page in the web UI (no API for it). Six finished pages are
   committed and waiting at `/home/user/projects/store-wiki` with `origin` already set.
   After creating any page, publish with:
   `git -C /home/user/projects/store-wiki push origin HEAD:master`
   **The README already links to `../../wiki`, so those links 404 until this happens.**
2. **XMR mining has no toggle and no agent gate** — unlike Pearl and JellyCoin, which each
   have both (`pearl_mining_enabled` + `pearl_agent_access`). Reads as an oversight, not a
   decision, and it's a small fix. It is currently documented publicly as a known gap.
3. **The gates docs are now a public promise.** README "What's gated" + the wiki gates page
   openly list what is NOT gated (XMR mining, social posting, Portal→Woo push, outbound
   mail, Docker control, GPU-node SSH, unsandboxed default-enabled plugins, the localhost
   auth bypass). Close any of those gaps → update BOTH docs.
4. **`INDEX.md` is stale** in the same ways the README was: it references an `app-main.js`
   that doesn't exist (dispatch is `app-nav.js`), still lists Treasury/Crypto/Wallets/Money
   as top-level instead of Finance panes, and omits Finance, Oracle, Games, Research Lab,
   Private Studio, Bills and P&L.

## 23. 💝 Donation / support rails — TODO (store + WordPress + public repo)
Add a way for people to give money. Three surfaces, and there's already more plumbing
built for this than it looks.

**What already exists — reuse, don't rebuild:**
- **Cash App rail** (`app/routers/cashapp.py`) — `$cashtag` payment link + QR is live and
  free; the Square Cash App Pay checkout rail is built but needs a Square account to go
  live. Both are already prayer-gated (`cashapp_request`, `cashapp_checkout`).
- **PayPal** — configured in Treasury (config/verify/withdraw), and payouts are one of the
  three permanently-gated money-out kinds.
- Donations are money **IN**, so they belong in the toggleable-gate tier, not behind the
  irreversible money-out floors. A donate *link* costs nothing and needs no approval; only
  a payout of what's collected should hit a gate.

**Surfaces to add:**
1. **Store app** — a small "Support this project" affordance. Natural home is Finance
   (its own pane, or a card on Overview) with the shared `hlp()` help tooltip. Should be
   **toggle-controlled and default OFF** so a fresh public clone doesn't ship someone
   else's donate links — per the "every gate gets a toggle" rule. The cashtag/links belong
   in Settings → Integrations, encrypted at rest like every other credential.
2. **WordPress (example.com)** — a donate/support block on the site. Options: a WooCommerce
   "name your price" product (keeps money in the existing Woo reporting and the Portal
   push already knows how to talk to Woo), or a plain embedded button. Woo product is
   probably less work given what's already wired.
3. **Public repo** — add `.github/FUNDING.yml`. This is the cheapest win of the three:
   GitHub renders a **Sponsor button** on the repo with zero code. It accepts GitHub
   Sponsors plus `ko_fi`, `buy_me_a_coffee`, `liberapay`, `patreon`, and arbitrary
   `custom:` URLs. ⚠️ It lands in the PUBLIC tree, so it must survive the retail scrub —
   any real handle in it needs a `RETAIL_LEAK_EXEMPT_LINES` entry, exactly like the LICENSE
   copyright line. Decide deliberately whether a fresh clone should carry those handles at
   all (probably not — see the toggle-OFF point above).

**Platform: ✅ DECIDED 2026-07-20 — Buy Me a Coffee.** (GoFundMe rejected: it's built for
a specific finite need with a story and a target, and works badly as an open-ended tip
jar. BMC is the right shape for ongoing support and takes a smaller cut.)

**⛔ BLOCKED ON ONE INPUT: the Buy Me a Coffee username.** Nothing can be wired until
there's an account — the handle is the only missing piece. Once it exists, the whole
first pass is ~15 minutes:
- `.github/FUNDING.yml` → `buy_me_a_coffee: <username>` (renders the repo Sponsor button)
- Store: donate pane/card linking `https://buymeacoffee.com/<username>`, toggle default OFF
- WordPress: support block / "name your price" Woo product pointing at the same link
- Add the handle line to `RETAIL_LEAK_EXEMPT_LINES` if it must survive the scrub, and
  decide whether a fresh public clone should carry it at all (default: no)

**Note:** none of this makes money on its own. Per the income-ops review, the store's
money loop still isn't closed (nothing sold yet) — donation rails are a supplement to
that, not a substitute for finishing it.

## Older list (pre-2026-07-19 — believed done, verify then delete)
- Tab navigation mapping fixes (image-gen, videos, cults3d, resell, library) — done per tabs-fix arc
- Settings Pi-hole/security context — reworked in Security Command Center overhaul
- AI Assistant "message corrupt" — assistant rebuilt as agentic tool loop

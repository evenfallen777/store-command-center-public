# 🐙 GitHub & Dev Swarm — The Engineers + per-project workboards

The Engineers are the autonomous half of the dev swarm: Engineer personas (Ada,
Grace, Linus, Turing — modeled on `app/world_leader.py`'s Mayor/Boss) study each
enabled project on a cadence, file a real `swarm_jobs` proposal, and — unlike
the company leaders — **auto-run it immediately**, so the swarm codes and tests
with no human step. On top of that sits a per-project registry so the same
pipeline works the store AND any side project.

## The pipeline

```
agent/swarm (idea · mod · fix)
   → GitHub DEV branch            (the swarm commits AND pushes origin/<work_branch> after coding)
   → review & Q&A                 (USER or AUTONOMOUS SWARM, per-project review_mode)
        approve / request-change / bug / deny
   → MAIN                         (merge dev→master + push origin/master — NOT live)
   → "⬆️ Apply main → live store" (user click — or the project's opt-in auto_go_live)
```

**Main ≠ live.** Promote (approve → main) merges and pushes but NEVER restarts
the running app (`restart_after_promote` is ignored by design). The only path
to live is `POST /api/github/update-live` — surfaced everywhere as
**"⬆️ Apply main → live store"**: it pulls the approved code on `main` into
THIS running store (fetch `origin/master`, `--ff-only` merge in the live
worktree), stamps promoted jobs `deployed_at`, and restarts. This is
**distinct from Settings → Updates** (`/api/system/update-apply`), which pulls
a *published release/public channel* — that consumer updater is untouched.

**Auto go-live (opt-in, default OFF).** `dev_projects.auto_go_live=1` makes the
apply-step automatic: right after a job of that project promotes to main, the
store applies main → live and restarts by itself (all DB writes + a God-Console
note land first; the restart is deferred via `threading.Timer`, like
`system.update_apply`). Store/primary only — a side project has no separate
running "live" this app controls, so reaching main IS done there and the toggle
is a no-op. `review_mode=swarm` + `auto_go_live=1` is the fully-autonomous
chain: swarm approves → merges to main → applies to live → restart. Both
default to off/human, so nothing autonomous happens unless the owner opts in
twice.

## The policy (identical for every project)

1. **Auto-build, gate the merge.** Proposal → code → push to the GitHub dev
   branch → review → test happens autonomously on the project's *working*
   branch. An approval is required before code lands on the project's **main**
   branch (`/approve` → `/promote`) — by **you**, or in `swarm`/`either` review
   mode by the reviewer-panel consensus. Going **live** always needs your
   Update-to-live click.
2. **`Work on: dev / main` toggle** (every board, store included) selects
   *where the swarm edits* — never whether the gate applies. `main` = edit the
   live worktree directly; commits still sit locally until approved/promoted.
   The UI states this everywhere so "main" never reads as "no approval".
3. The **store** is the permanent primary project (`kind='store'`, cannot be
   deleted, `work_branch='dev'` by default — the board opens on it). The
   Engineers may also create brand-new projects (`kind='engineer'`) and work
   them under the same gates.

## Review modes (`dev_projects.review_mode`)

| mode | who satisfies the review gate (merge to main) |
|---|---|
| `human` (default) | the job waits at `awaiting_review`; only your `/approve` advances it; `/reject` (with comment) sends it back to coding |
| `swarm` | the reviewer panel's **consensus approve** (majority vote + syntax tests passed) auto-approves and auto-runs the promote-to-main step — still no restart; consensus reject/bug loops back to coding with the feedback; a hard error pauses the job |
| `either` | whichever comes first — swarm consensus or your approve |

Even in `swarm` mode a God-Console note is posted for every auto-merge, so you
always SEE what the swarm approved before (or as) it goes further.

## Two-way human Q&A around a job

Three channels (tables reused: `swarm_questions`, `swarm_events`, plus the small
`swarm_directives` table):

1. **The swarm asks & waits (blocking, any stage).** The architect, coder, and
   reviewer/auditor turns may emit an optional `{"question_for_user": "..."}`
   (opt-in in their prompts — rare by instruction, so normal jobs don't stall).
   The engine's `_ask_user()` files an open `swarm_questions` row, logs it on
   the timeline, parks the job at `awaiting_input`, and the drive loop exits.
   **Resume wiring:** `POST /api/github/questions/{qid}/answer` saves the
   answer and, when it was the LAST open question of an `awaiting_input` job,
   calls `swarm.start_job(jid)` — the run resumes with all answers injected
   into the coder's context (`_answered_context`).
2. **You direct anytime.** `POST /jobs/{jid}/direct` records a directive;
   pickup latency is *the next stage boundary* (directives are read at the
   start of the architect/coder/reviewer turns, never mid-LLM-turn). Directing
   a paused/awaiting job also resumes it — you can direct instead of answering.
3. **The swarm answers you.** `POST /jobs/{jid}/ask` = one LM Studio turn with
   the job's context; informational only, no course change.

UI: the job detail (Dev Swarm tab) has a **💬 Q&A / Direct** panel showing the
interleaved conversation with Ask + Direct inputs; open blocking questions keep
their answer boxes. The God-panel Engineers block lists **jobs waiting on you**
(`awaiting_input` with open questions) with quick answer boxes, next to the
merge-gate queue.

## Tables

### `dev_projects` (new — `db_schema.create_dev_projects_tables`, called from `init_db()`)

| column | meaning |
|---|---|
| `kind` | `store` \| `external` \| `engineer` |
| `name` | display name |
| `repo` | GitHub `owner/name`, NULL for pure-local |
| `local_path` | live-branch checkout/worktree on disk |
| `dev_path` | optional separate dev worktree (store = `config.REPO_DEV`) |
| `live_branch` | the branch that is "live" (`master` for the store) |
| `work_branch` | the toggle: `dev` (staged) \| `main` (edit live worktree directly) |
| `is_primary` | 1 for the store — its board also owns legacy jobs with `project_id IS NULL` |
| `engineers_enabled` | Engineers actively work this project |
| `merge_gate` | human approval before the live branch (the policy backbone — leave on) |
| `autonomy` | per-project swarm autonomy (`auto`/`gate`/`step`) |
| `review_mode` | who merges to main: `human` \| `swarm` \| `either` (default `human`) |
| `auto_go_live` | 1 = auto-apply main→live store right after promote (store/primary only, default 0) |

Seeded once: exactly one `kind='store'` row (`Store Command Center`,
`is_primary=1`, `live_branch='master'`, `work_branch='dev'`,
`local_path=REPO_MASTER`, `dev_path=REPO_DEV`, `merge_gate=1`,
`engineers_enabled=0`; `repo` derived from the master worktree's origin remote).

### `swarm_jobs.project_id`, `swarm_jobs.deployed_at` (new columns)

Idempotent `ALTER`s in `app/routers/github/_base.py::_ensure_schema`, next to
the existing ones. `project_id` NULL = legacy job, treated as belonging to the
primary store project everywhere (job listing, board counts, Engineer
busy-checks). `deployed_at` is stamped by Apply-main→live-store and drives the
board's 🚀 Live column: when the ff-merge succeeds, **every `done` job without
a `deployed_at` is marked deployed** — everything on main is by definition
contained in the new live HEAD (the simple mark-all-done approach; jobs don't
record their own commit SHAs, so per-commit containment was skipped on purpose).

### `swarm_directives` (new — created in `create_swarm_tables` + `_ensure_schema`)

`(id, job_id, text, consumed, created_at)` — owner directives from
`POST /jobs/{jid}/direct`, consumed by the engine at the next stage boundary.

## Endpoints (all on the shared github router)

| method + path | purpose |
|---|---|
| `GET /api/github/projects` | list projects, each with `job_counts` (proposed/working/needs_you/approved/done/paused) |
| `POST /api/github/projects` | register an existing repo or create a brand-new one (`create_repo` reuses the `/api/github/repo/create` logic, clones under `~/projects/engineer/<name>`) |
| `PATCH /api/github/projects/{pid}` | whitelisted toggles: `work_branch, engineers_enabled, merge_gate, autonomy, is_primary, name` (only one primary at a time) |
| `DELETE /api/github/projects/{pid}` | remove a project (the `kind='store'` row is refused) |
| `GET /api/github/jobs?project_id=` | board scoped to a project (primary store also matches `project_id IS NULL`) |
| `GET\|POST /api/github/engineers-settings` | the Engineers settings surface (same settings-row pattern as `/api/oracle/settings`) |
| `POST /api/github/update-live` | ⬆️ Apply main → live store (the ONLY path to live): fetch + `--ff-only` merge `origin/master` into the live worktree, stamp `deployed_at`, restart. Shared body `_apply_main_to_live()` also powers the auto_go_live path |
| `GET /api/github/live-status` | "update available" indicator: live HEAD vs `origin/master` ahead/behind (origin fetched at most once/min) + count of done-but-not-deployed jobs |
| `POST /api/github/jobs/{jid}/direct` | inject an OWNER DIRECTIVE `{text}`; the engine reads unconsumed directives at the next stage boundary (architect/coder/reviewer turn start — not mid-LLM-turn), appends them as "OWNER DIRECTIVE (incorporate this)", marks them consumed, and logs the pickup. Also resumes a paused/awaiting job |
| `POST /api/github/jobs/{jid}/ask` | ask the swarm ABOUT a job `{question}`: one LM Studio turn over the job context (spec, plan, latest commit, recent timeline). Pure Q&A — never changes status/plan/code. Q and A land on the timeline as `user_q`/`agent_a` |

`POST /api/github/jobs` also accepts `project_id` and defaults `branch` from the
project's `work_branch` when no branch is supplied. `POST /jobs/{id}/promote`
handles non-store projects too (merge their dev branch into their live branch in
the project checkout + push) and never restarts anything.

## Settings keys

| key | default | meaning |
|---|---|---|
| `engineers_auto` | `off` | master switch for the autonomous loop (God Console → 🛠️ The Engineers) |
| `engineers_interval_hours` | `12` | cadence between Engineer proposals, per project |
| `engineers_last_t_<pid>` | — | internal: last proposal time per project |
| `engineers_idea_n` | — | internal: fallback-idea rotation counter |
| `engineers_noted_<jid>` | — | internal: merge-gate God-Console note sent for job |

## How a tick works (`app/engineers.py`, started from `main.py` startup)

Background thread (15-min tick, same shape as `oracle.start_auto`); a no-op
unless `engineers_auto=on`. Per engineers-enabled project, once per cadence
window and only if no Engineer job is already in flight for it
(`awaiting_review` counts as in-flight, so work never piles up at the gate):

1. **Analyze** — in-repo signals (`problems.md`, `TODO.md`), recent swarm
   errors/tests, and already-filed titles go to ONE LM Studio turn via the
   swarm's own serialized path (`swarm.llm._turn`). **No Claude/Anthropic
   calls** — there is no provider abstraction and none is wanted. If the LLM is
   unavailable, a rotating fallback list (like `world_leader.UPGRADE_IDEAS`)
   picks the idea.
2. **File** — `INSERT INTO swarm_jobs (…, branch=<work_branch>, project_id,
   autonomy, status='proposed')`, attributed `[Filed by Engineer <name> …]` in
   the spec.
3. **Auto-run** — `swarm.start_job(jid)` immediately (the no-human-step half of
   the policy).
4. **Surface** — `world_ops.note()` on filing, and again when the job reaches
   `awaiting_review` (the merge gate). The God Console / Command tab's
   Engineers block lists gate-pending jobs with Approve/Reject buttons that hit
   the existing `/approve` and `/reject` endpoints.
5. **New projects** — the LLM may answer with a `new_project` idea; that calls
   the same project-create path (repo + clone + registry row) and files the
   bootstrap job on it, under the same gates.

The Engineers never draw the company fund — proposals are free.
(TODO, deliberately not built: a coin budget hook à la
`world_leader.charge_on_approval`.)

## Working-branch resolution (`app/swarm/workspace.py`)

The engine no longer hardcodes `config.REPO_DEV`. Per job:

- resolve the project (`swarm_jobs.project_id`, fallback primary store);
- `work_branch='dev'` → the project's **dev worktree** (`dev_path`; store =
  `REPO_DEV` — the historical default path, byte-for-byte unchanged behavior);
- `work_branch='main'` → the project's **live worktree** (`local_path`). For
  non-store projects with a single checkout, the workdir is `git checkout`ed to
  the selected branch before editing (a `dev` branch is created on first use).
  Commits made on the live worktree still only reach the live REMOTE through
  the approve/promote gate.

The strict `<<<FILE path>>> … <<<END>>>` writer, `_path_allowed` scoping, and
the syntax-check test stage are unchanged — only the base directory became
project-aware. After each successful coding commit the engine **pushes the
working branch to origin** (`_push_ws`, best-effort: a failed push is logged to
the job timeline and never fails the job), so "GitHub dev branch" is a real
pipeline stage.

## UI

- **GitHub tab → Workboard**: project switcher pills; per-project header with
  the `Work on: dev/main` toggle (labeled "main = edit the live worktree
  directly; a human still approves before code goes live"), Engineers on/off,
  Merge gate on/off, autonomy, and the review-mode select (you / swarm
  consensus / either); ⚙️ Projects management view; ➕ New project. The kanban
  columns mirror the pipeline — 💡 Ideas → 🔧 Building on dev → 🔎 Review & Q&A
  (shows the project's review mode) → ✅ On main (not live) → 🚀 Live
  (`deployed_at` set) → ⏸️ Paused / Bug / Error — with the same 5s live
  refresh. The store board header carries the prominent **⬆️ Apply main → live
  store** button plus the "N approved change(s) on main — not applied to the
  live store yet" indicator from `/api/github/live-status`, the review-mode
  select, and the 🚀 Auto go-live toggle.
- **God Console / Command tab** (`world-god.js`, hosted by both): 🛠️ The
  Engineers block — `engineers_auto` master switch, cadence, per-project enable
  list with review-mode selects + 🚀 auto-go-live toggles, the
  **waiting-on-you** queue (`awaiting_input` blocking questions with quick
  answer boxes), the merge-gate queue (all `awaiting_review` jobs;
  Engineer-filed ones marked 🛠️) with Approve/Reject, and the same ⬆️ Apply
  main → live store button + indicator.

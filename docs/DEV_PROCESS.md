# Store Dev Process — Claude Code operating rules

Extends `DEV_SWARM.md` (which governs the in-app local-model dev swarm). **This** doc
governs how Claude Code + its subagents build and fix the store. Adopted 2026-07-21.

## Definition of Done — nothing ships until ALL pass
1. **Smoke-tested for real** — not "code parses." In layers:
   - *Static (JS/SPA):* `bash tools/verify_spa.sh` — syntax, duplicate top-level
     globals, dispatch coverage.
   - *Backend:* route imports + registers; endpoint returns 200 with real data via
     the authed session.
   - *Live:* exercise the actual user action end-to-end under the `/store/` prefix.
     Never root-absolute `/js/…` or `/api/…` — it 404s silently behind nginx-proxy.
2. **Edge cases from a Q&A pass** are added. Adversarially ask "what breaks this?":
   empty input, huge input, model returns junk / code fences / no expected tags,
   reasoning `<think>` blocks, timeout, unauthenticated, GPU busy. Handle or note.
3. **Unified queue is mandatory.** Any model call — LLM / image / video / music / 3D —
   rides the queue (`orch.submit_*` / `run_*_job`). No bare model calls in request
   handlers. (Ref: the `jellycoin.py` missions/draft queue-bypass bug, fixed 2026-07-19.)
4. **Logged:** root cause + fix in `problems.md`; status in `TODO.md`.
5. **Tests:** `./run_tests.sh` green, or the new test added.
6. **Human approval before commit to master.** Never self-approve (per `DEV_SWARM.md`).
   Commit only the files belonging to that one change.

## Bug intake — smoke-testing the live site
- Each reported bug: read `logs/store.log` + any error report left in the repo FIRST.
- Triage against `problems.md` / `TODO.md` before editing. Do not reopen resolved
  root causes — especially the `/store/` path-404 class and the queue-bypass class.
- Reproduce → root-cause → fix → run the Definition of Done.

## Subagent orchestration & model tiers
Claude Code (Opus 4.8) is the orchestrator. Fan out subagents for parallel/independent
bugs; keep only conclusions in the main thread.
- **Fable 5** — advanced: architecture, gnarly multi-file root-cause, security-sensitive
  logic, ambiguous failures, tricky refactors.
- **Opus** — heavy synthesis / multi-step tasks when Fable isn't required.
- **Sonnet** — standard implementation, scoped edits, smoke tests, test writing.
- **Haiku** — mechanical: grep sweeps, file mapping, log scraping, lookups.
Match the model to the task. Don't burn Fable on greps or Haiku on architecture.

## Context discipline
- Delegate heavy reads / log tailing / live iteration to subagents; they report concise
  results, not file dumps.
- Targeted reads over whole-file dumps. Summarize and discard; don't accumulate.

## Secrets
- The master password is **session-only**. NEVER write it to any file, commit, log, or
  memory. The retail-scrub → public-mirror pipeline makes a leaked secret catastrophic.

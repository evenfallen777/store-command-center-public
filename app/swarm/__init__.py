"""Dev Swarm engine — Phase 2.

Runs a proposed job through a state machine using LOCAL models only, one model in
VRAM at a time (every LLM turn goes through the orchestrator's single worker).

Hard safety boundaries:
  • All file edits happen in the **dev worktree** (config.REPO_DEV) — never master.
  • Scoped jobs (scope=file|folder) may only touch their listed paths.
  • Every applied change is committed to the `dev` branch → fully revertible.
  • Coders do FULL-FILE rewrites in a strict fenced format (reliable to parse from
    local models); unified diffs are not trusted.
  • Promotion dev→master→retail is a SEPARATE, user-approved step (routers/github.py).

State machine (swarm_jobs.status):
  proposed → planning → [awaiting_input] → coding → reviewing → testing
           → awaiting_review → (user approve) approved → (user promote) done
  reject/fail routes back to coding with feedback, or paused.

The driver advances stage-by-stage and stops at a gate determined by autonomy:
  gate  → stop at awaiting_input (questions) and awaiting_review (before promote)
  auto  → run straight through to awaiting_review (still user-approved)
  step  → stop after every stage (click Run to continue)

This package was split out of the former single-file `app/swarm.py`. It preserves the
exact public surface every external caller (and the prompt registry) relies on, so
`import swarm; swarm.<name>()` keeps resolving. Submodules are layered to avoid import
cycles:
  _base      ← (no intra-package deps)     state/config helpers
  llm        ← (no intra-package deps)     model roster/load/turn/parse
  workspace  ← (no intra-package deps)     git sandbox + file scoping
  systasks   ← _base, llm                  gated system-agent installs
  engine     ← _base, llm, workspace, systasks   stage machine + driver
The engine⇄systasks cycle (systasks.run_system_task resumes a job via start_job) is
broken by a lazy `from .engine import start_job` inside run_system_task.
"""

# ── Shared state / config helpers ────────────────────────────────────────────
from ._base import _ev, _set, _job, _config

# ── LLM plumbing (model roster/load/turn + JSON/vote parsing) ─────────────────
from .llm import (
    _model_pool, _roster, _model_context, load_and_pin, _loaded_context,
    _resolve_model, _THINK_RE, _is_thinking, _strip_think, _turn,
    _extract_json, _parse_vote, _loaded_llms,
)

# ── git sandbox + file scoping (project-aware working dirs) ─────────────────
from .workspace import (
    _git_dev, _git_ws, _scoped_paths, _path_allowed, _FILE_RE, _parse_files,
    _read_scoped_context, _fallback_single_file, _repo_tree, _read_files,
    _job_project, _job_workdir, _prepare_workdir,
)

# ── gated system-agent installs (+ the SYSTEM_SYS prompt) ─────────────────────
from .systasks import (
    SYSTEM_SYS, _system_task, _set_system_task, propose_system_task, run_system_task,
)

# ── the stage machine, driver, and public entrypoints (+ stage prompts) ───────
from .engine import (
    PLANNER_SYS, SCOUT_SYS, ARCHITECT_SYS, CODER_SYS, _CODER_RETRY,
    REVIEWER_SYS, AUDITOR_SYS,
    _spawn_subtasks, _stage_architect, _stage_planning, _answered_context,
    _stage_coding, _stage_reviewing, _stage_testing, _drive,
    start_job, is_running, reconcile_on_start,
    MAX_CODE_ROUNDS, _running, _running_lock,
)

__all__ = [
    # public entrypoints
    "start_job", "is_running", "reconcile_on_start",
    "propose_system_task", "run_system_task", "load_and_pin",
    # helpers relied on by other modules (peers, github, scheduler)
    "_ev", "_extract_json", "_parse_vote",
    "_loaded_llms", "_loaded_context", "_is_thinking",
    # prompt-registry ref targets (prompts.py resolves swarm.<NAME>)
    "PLANNER_SYS", "SCOUT_SYS", "ARCHITECT_SYS", "CODER_SYS",
    "REVIEWER_SYS", "AUDITOR_SYS", "SYSTEM_SYS",
]

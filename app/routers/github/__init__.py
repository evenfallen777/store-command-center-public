"""GitHub / Dev Swarm tab.

Phase 1 (this file): GitHub management (repos, branches, PRs, issues, start new
projects) via the `gh` CLI, the dev→master→retail worktree status, the agent/model
configuration for the swarm (assign your LOCAL models to roles + autonomy setting),
and the job/proposal data model (propose a fix/project, agents raise questions).

Phase 2 (next): the swarm ENGINE — sequential local-model agent turns through the
orchestrator (one model in VRAM at a time), comment/audit/vote, apply to the dev
branch, test, human-approve, promote dev→master→retail; cron to keep working WIP.

This module is a package: the router + shared helpers live in ``_base``; the routes
are split across ``repos`` (Domain A), ``models`` (Domain B), ``jobs`` (Domain C)
and ``projects`` (Domain D — the per-project registry + Engineers settings).
Importing the submodules runs their ``@router.*`` decorators, registering every route
on the single shared ``router`` exposed here.
"""
from ._base import router          # shared router + one-time schema/reconcile side effects
from . import repos, models, jobs, projects, devstore, collab  # noqa: F401  (import registers their @router routes)

__all__ = ["router"]

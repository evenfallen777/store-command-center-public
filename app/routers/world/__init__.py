"""
The Company — HTTP surface (thin).

Endpoints only. All behaviour lives in the world modules:
  world_defs   → constants, roster, seeding, live activity, the LLM-queue gateway
  world_sim    → simulation tick (needs, economy, bills, behaviour, mood)
  world_gov    → thoughts, opinions, town meetings & voting (LLM via the queue)
  world_build  → pixel-art asset generation + autobuild (image gen via the queue)

This module is a package: the shared ``router`` lives in ``_base``; the routes are
split across ``state`` (settings + the read snapshots), ``agents`` (agent actions,
props, governance), ``economy`` (production/config controls + bills), ``build``
(tileset, raids, map layout, soundscape) and ``hud`` (read-only aggregations for
the overlay HUD). Importing the submodules runs their
``@router.*`` decorators, registering every route on the single shared ``router``.
"""
from ._base import router                       # shared router
from . import state, agents, economy, build, sprites, hud, public, hq   # noqa: F401  (import registers their @router routes)

__all__ = ["router"]

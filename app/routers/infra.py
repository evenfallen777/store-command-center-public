"""Infrastructure — live hardware telemetry for the Store's machines.

routers/health.py answers "is it up?"; this answers "how hard is it working and
how much room is left?" — CPU, RAM, disks and GPUs for both the server the Store
runs on and the GPU node it drives. Collection lives in app/telemetry.py (one
stdlib-only collector, run locally and over SSH so both hosts report the same
shape).

Cached for a few seconds: each poll costs an SSH round-trip plus a 0.25s CPU
sample per host, and the UI refreshes on a timer. The TTL is short enough that
numbers still read as live and long enough that several open tabs cost one probe.
"""
from fastapi import APIRouter, HTTPException

from deps import *          # noqa: F401,F403 — get_setting, … (store convention)
import config               # explicit: `deps` does not re-export it
import telemetry

router = APIRouter()

_TTL = 5                    # seconds; see module docstring


@router.get("/api/infra/telemetry")
def infra_telemetry():
    """CPU / RAM / disk / GPU for every host, newest sample (<=5s old).

    A host that cannot be reached comes back with ok=false and an `error`
    string rather than failing the request — one dead node must not blank the
    whole page.
    """
    from cache import cached
    return cached("infra:telemetry", _TTL, telemetry.collect_all)


@router.get("/api/infra/telemetry/{host_key}")
def infra_telemetry_one(host_key: str):
    """A single host, for a focused panel that should not pay for the other's probe."""
    from cache import cached
    if host_key == "server":
        data = cached("infra:telemetry:server", _TTL, telemetry.collect_local)
        return {"key": "server", "label": "Server", "role": "store",
                "address": "localhost", **data}
    if host_key == "node":
        data = cached("infra:telemetry:node", _TTL, telemetry.collect_remote)
        return {"key": "node", "label": "GPU Node", "role": "gpu",
                "address": config.GPU_HOST, **data}
    raise HTTPException(404, f"Unknown host '{host_key}' (expected 'server' or 'node').")


@router.post("/api/infra/telemetry/refresh")
def infra_telemetry_refresh():
    """Drop the cache so the next read is a fresh probe (the UI's Refresh button)."""
    from cache import invalidate_prefix
    invalidate_prefix("infra:telemetry")
    return {"ok": True}

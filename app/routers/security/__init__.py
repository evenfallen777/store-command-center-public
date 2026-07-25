"""Network Security routes — live connection intelligence + Pi-hole scan/findings."""
from ._base import router
# Importing the engine modules registers their @router decorators on `router`.
from . import posture, engines, scans, dns, redblue   # noqa: F401

# Re-exports so external callers keep resolving `security.<name>` exactly as they
# did against the old single-file module:
#   scheduler.py -> monitor_tick, trigger_security_scan, analyze_logs
#   prompts.py   -> _ANALYZE_SYSTEM  (lazy ref=("routers.security", "_ANALYZE_SYSTEM"))
from .dns import monitor_tick, analyze_logs, _ANALYZE_SYSTEM  # noqa: F401
from .scans import trigger_security_scan  # noqa: F401

__all__ = ["router", "monitor_tick", "analyze_logs", "trigger_security_scan", "_ANALYZE_SYSTEM"]

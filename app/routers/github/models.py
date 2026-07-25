"""Domain B — local LLM models (list/load/pin) and the swarm configuration
(agent roster + per-role model + autonomy). Routes register on the shared router.
"""
import json
from typing import Optional

from deps import *   # get_setting, get_conn, httpx, LMSTUDIO_URL, _llm_headers, config
import swarm
from ._base import router


# ─────────────────────────────────────────────────────────────────────────────
# Local LLM models (for assigning models to agent roles)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/github/llm-models")
def github_llm_models():
    """List local LLMs from LM Studio (borrowed from the settings endpoint's approach)."""
    try:
        r = httpx.get(f"{LMSTUDIO_URL}/models", headers=_llm_headers(), timeout=10)
        r.raise_for_status()
        ids = [m["id"] for m in r.json().get("data", [])]
    except Exception as e:
        return {"models": [], "error": str(e)}
    def is_embed(i): return any(k in i.lower() for k in ("embed", "nomic", "bge", "gte"))
    return {"models": [i for i in ids if not is_embed(i)],
            "loaded": get_setting("enhance_model", "")}


@router.get("/api/github/loaded-model")
def loaded_model():
    """What's resident in VRAM right now + a context/prompt sanity check for the pool."""
    loaded = swarm._loaded_llms() or []
    cur = loaded[0] if loaded else None
    ctx = swarm._loaded_context(cur) if cur else None
    cfg = get_swarm_config()
    want = int(cfg.get("context", 16384))
    checks = []
    for m in (cfg.get("models") or []):
        if swarm._is_thinking(m):
            checks.append({"model": m, "level": "info",
                           "msg": "reasoning/thinking model — its <think> scratchpad is auto-stripped and "
                                  "its token budget doubled; expect slower turns."})
    if cur and ctx and ctx < 8192:
        checks.append({"model": cur, "level": "warn",
                       "msg": f"loaded context is only {ctx} tokens — low for rewriting whole files. "
                              f"Raise the context and reload ({want} is set)."})
    if not (cfg.get("models") or []):
        checks.append({"model": None, "level": "warn",
                       "msg": "No models in the swarm pool — add at least one below so the swarm has a model to run."})
    elif not loaded:
        checks.append({"model": None, "level": "info",
                       "msg": "No model resident right now — that's fine: the swarm loads its model on demand "
                              "through the unified GPU queue when a turn runs. Pinning is optional (keeps one warm)."})
    return {"loaded": cur, "context": ctx, "want_context": want, "all_loaded": loaded, "checks": checks}


@router.post("/api/github/load-model")
def load_model(body: dict):
    """Load & PIN a specific local model (unloads others to fit; sets context; no TTL)."""
    model = (body or {}).get("model", "").strip()
    if not model:
        raise HTTPException(400, "model required")
    ctx = (body or {}).get("context")
    r = swarm.load_and_pin(model, context=int(ctx) if ctx else None)
    if not r.get("ok"):
        raise HTTPException(502, f"Load failed: {r.get('note')}")
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Swarm configuration (agent roster + per-role model + autonomy)
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_SWARM_CONFIG = {
    "autonomy": "gate",         # gate | auto | step
    "mode": "dynamic",          # dynamic (spin up N agents) | static (fixed named roster)
    "agent_count": 3,           # dynamic default N of agents (per-job overridable)
    "context": 16384,           # context length to load models with (coders feed whole files)
    "auto_pin": True,           # pin the coder model into VRAM before a run (no manual pin needed)
    "restart_after_promote": False,  # after a promote, restart the live app so it runs the new code
    "models": [],               # pool of local models the swarm draws from (assign your coding models)
    "voters": 2,                # approvals needed to pass a change (author excluded)
    # ── Review rules (your directives) ──────────────────────────────────────
    "no_self_approval": True,   # a model may NOT review/decide its OWN code — another model must
    "self_review_when_solo": True,  # if effective agent_count == 1, the sole model reviews+tests its own code
    "user_final_approval": True,    # only YOU approve/reject (reject requires a comment). Always on.
    # ── System agent: installs/configures system deps the swarm needs ────────
    "system_agent": {
        "enabled": True, "model": "",
        "task": "When the swarm needs a tool/library/service installed or configured on the system, "
                "do it, VERIFY it works, report back, and signal the swarm to resume/test. "
                "Never run destructive commands; propose anything risky for user approval first."},
    # ── Named roster (used only when mode == 'static') ───────────────────────
    "agents": [
        {"role": "planner", "enabled": True, "model": "",
         "task": "Break the job into concrete steps. Raise clarifying questions when the "
                 "ask is ambiguous, or before a big change, a file split, or a direction shift."},
        {"role": "coder1", "enabled": True, "model": "",
         "task": "Implement the planned change on the working branch."},
        {"role": "coder2", "enabled": False, "model": "",
         "task": "Second coder — an alternative implementation to compare against coder1."},
        {"role": "reviewer", "enabled": True, "model": "",
         "task": "Review another agent's diff for correctness, clarity, simplicity. Never your own."},
        {"role": "auditor", "enabled": True, "model": "",
         "task": "Security and quality audit of the diff (not your own). Flag risks; vote approve/reject."},
    ],
}


@router.get("/api/github/swarm-config")
def get_swarm_config():
    raw = get_setting("swarm_config", "")
    if raw:
        try:
            cfg = json.loads(raw)
            # merge in any new default keys
            for k, v in _DEFAULT_SWARM_CONFIG.items():
                cfg.setdefault(k, v)
            cfg["context"] = int(get_setting("swarm_context", str(cfg.get("context", 16384))))
            return cfg
        except Exception:
            pass
    return _DEFAULT_SWARM_CONFIG


class SwarmConfigIn(BaseModel):
    autonomy: Optional[str] = None
    mode: Optional[str] = None
    agent_count: Optional[int] = None
    context: Optional[int] = None
    auto_pin: Optional[bool] = None
    restart_after_promote: Optional[bool] = None
    models: Optional[list] = None
    voters: Optional[int] = None
    no_self_approval: Optional[bool] = None
    self_review_when_solo: Optional[bool] = None
    system_agent: Optional[dict] = None
    agents: Optional[list] = None


@router.post("/api/github/swarm-config")
def set_swarm_config(body: SwarmConfigIn):
    cur = get_swarm_config()
    if body.autonomy in ("gate", "auto", "step"):
        cur["autonomy"] = body.autonomy
    if body.mode in ("dynamic", "static"):
        cur["mode"] = body.mode
    if body.agent_count is not None:
        cur["agent_count"] = max(1, min(12, body.agent_count))
    if body.context is not None:
        cur["context"] = max(2048, min(131072, body.context))
        conn0 = get_conn()
        conn0.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('swarm_context',?)", (str(cur["context"]),))
        conn0.commit(); conn0.close()
    if body.auto_pin is not None:
        cur["auto_pin"] = bool(body.auto_pin)
    if body.restart_after_promote is not None:
        cur["restart_after_promote"] = bool(body.restart_after_promote)
    if body.models is not None:
        cur["models"] = body.models
    if body.voters is not None:
        cur["voters"] = max(1, min(9, body.voters))
    if body.no_self_approval is not None:
        cur["no_self_approval"] = bool(body.no_self_approval)
    if body.self_review_when_solo is not None:
        cur["self_review_when_solo"] = bool(body.self_review_when_solo)
    if body.system_agent is not None:
        cur["system_agent"] = body.system_agent
    if body.agents is not None:
        cur["agents"] = body.agents
    # user_final_approval is always on — it's a safety invariant, not user-togglable.
    cur["user_final_approval"] = True
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('swarm_config',?)",
                 (json.dumps(cur),))
    conn.commit()
    conn.close()
    return cur

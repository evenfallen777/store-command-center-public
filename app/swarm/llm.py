"""Dev Swarm — LLM plumbing.

Model pool/roster/load/pin/context/resolve, reasoning-model handling, the single
serialized LLM turn, and tolerant JSON / vote parsing. Depends on the orchestrator's
single worker so only one model sits in VRAM at a time.

No intra-package dependencies.
"""
import json
import re
import time

from orchestrator import orch, _loaded_llms, _ssh, LMS
from deps import _call_lmstudio, get_setting


# ─────────────────────────────────────────────────────────────────────────────
# Model roster
# ─────────────────────────────────────────────────────────────────────────────
def _model_pool(cfg: dict) -> list[str]:
    pool = [m for m in (cfg.get("models") or []) if m]
    if not pool:
        # fall back to whatever the app's default LLM is
        d = get_setting("enhance_model", "")
        if d:
            pool = [d]
    return pool


def _roster(cfg: dict, n: int) -> dict:
    """Assign models to roles for a run of n agents. The reviewer PANEL has up to
    `voters` members, drawn from the pool EXCLUDING the coder's model (no self-approval)
    unless the job is solo. Honors self_review_when_solo."""
    pool = _model_pool(cfg)
    if not pool:
        return {}
    coder = pool[0]
    n_voters = max(1, int(cfg.get("voters", 2)))
    if n <= 1:
        # solo job: one reviewer — itself (if allowed) else a different model
        if cfg.get("self_review_when_solo", True):
            reviewers = [coder]
        else:
            reviewers = [next((m for m in pool if m != coder), coder)]
    else:
        cands = [m for m in pool if m != coder] if cfg.get("no_self_approval", True) else pool
        if not cands:
            cands = pool
        reviewers = [cands[i % len(cands)] for i in range(n_voters)]
    planner = pool[1 % len(pool)] if len(pool) > 1 else coder
    return {"planner": planner, "coder": coder, "reviewer": reviewers[0], "reviewers": reviewers,
            "self_review": (len(reviewers) == 1 and reviewers[0] == coder)}


def _model_context() -> int:
    try:
        return max(2048, int(get_setting("swarm_context", "16384")))
    except Exception:
        return 16384


def load_and_pin(model: str, context: int = None, unload_first: bool = True) -> dict:
    """Make `model` the resident LLM by requesting it THROUGH the unified queue —
    the orchestrator worker's _ensure_loaded is the SOLE authority that ever calls
    `lms load`/`unload` (single-VRAM invariant, GPU_QUEUE.md). We submit a no-op
    task with model=... so the worker loads it (serialized against image/video),
    then poll. Never touches lms/SSH directly. Returns {ok, loaded, context, note}.
    (context/unload_first kept for API compat; residency + context are the worker's job.)"""
    if not model:
        return {"ok": False, "loaded": None, "note": "no model given"}
    try:
        if model in (_loaded_llms() or []):
            return {"ok": True, "loaded": model, "context": _loaded_context(model), "note": "already resident"}
        tid = orch.submit_llm(lambda: True, desc=f"swarm: load {model}", model=model, priority=2)
        while True:
            p = orch.poll(tid)
            if p["status"] in ("done", "error", "cancelled", "not_found"):
                break
            time.sleep(1)
        if p["status"] == "done":
            return {"ok": True, "loaded": model, "context": _loaded_context(model), "note": "loaded via queue"}
        return {"ok": False, "loaded": None, "note": (p.get("error") or f"load {p['status']}")[:200]}
    except Exception as e:
        return {"ok": False, "loaded": None, "note": str(e)[:200]}


def _loaded_context(model: str = None) -> int | None:
    """Context length LM Studio reports for a loaded model (via lms ps --json)."""
    import json as _j
    rc, out = _ssh(LMS, "ps", "--json", timeout=10)
    if rc != 0 or not out:
        return None
    try:
        for m in _j.loads(out):
            if m.get("type") != "llm":
                continue
            if model and (m.get("modelKey") or m.get("identifier")) != model:
                continue
            for k in ("contextLength", "maxContextLength", "loadedContextLength"):
                if isinstance(m.get(k), int):
                    return m[k]
    except Exception:
        pass
    return None


def _resolve_model(preferred: str) -> str:
    """The model a turn actually targets: `preferred` if resident/loadable (robust
    load/pin), else BORROW the resident model (single-model constraint still holds)."""
    try:
        loaded = _loaded_llms() or []
        if preferred and preferred in loaded:
            return preferred
        if preferred:
            r = load_and_pin(preferred)
            if r["ok"]:
                return r["loaded"]
        loaded = _loaded_llms() or []
        if loaded:
            return loaded[0]
    except Exception:
        pass
    return preferred


# ── Thinking / reasoning model handling ──────────────────────────────────────
_THINK_RE = re.compile(r"<(think|thinking|reason|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)


def _is_thinking(model: str) -> bool:
    return any(k in (model or "").lower() for k in ("reason", "think", "-r1", "qwq", "o1", "cot"))


def _strip_think(text: str) -> str:
    """Remove reasoning-model scratchpads so JSON / FILE-block parsing sees the answer.
    Handles paired <think>…</think> and a dangling …</think> before the real output."""
    t = _THINK_RE.sub("", text or "")
    m = None
    for mm in re.finditer(r"</(think|thinking|reason|reasoning)>", t, re.IGNORECASE):
        m = mm
    if m:
        t = t[m.end():]
    return t.strip()


def _turn(model: str, system: str, user: str, max_tokens: int = 2000) -> str:
    """One LLM turn, serialized through the orchestrator. Targets `model` if loadable,
    else borrows the resident one. Reasoning models get a bigger budget (they spend
    tokens thinking) and their scratchpad is stripped from the returned text."""
    def work():
        # The orchestrator worker has ALREADY made `model` the sole resident LLM
        # (submit_llm(model=...) → _ensure_loaded) and set orch._current_llm_model.
        # We only run inference here — the swarm never loads/swaps models itself.
        budget = max_tokens * 2 if _is_thinking(model) else max_tokens
        return _call_lmstudio(system, user, budget)
    # Pass model= so the SINGLE worker loads it via the unified queue (never raw lms).
    tid = orch.submit_llm(work, desc="swarm turn", model=model, priority=2)   # background dev-swarm
    while True:
        p = orch.poll(tid)
        if p["status"] in ("done", "error", "cancelled", "not_found"):
            break
        time.sleep(1)
    if p["status"] != "done":
        raise RuntimeError(p.get("error") or f"turn {p['status']}")
    return _strip_think(p.get("result") or "")


def _extract_json(text: str) -> dict:
    """Tolerant JSON extraction from local-model output: strips ``` fences, then tries
    the whole brace span, then the first balanced object."""
    if not text:
        return {}
    t = re.sub(r"```[\w-]*\n?", "", text).replace("```", "")
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return {}
    frag = m.group(0)
    try:
        return json.loads(frag)
    except Exception:
        pass
    # trim to the first balanced {...}
    depth = 0
    for i, ch in enumerate(frag):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(frag[:i + 1])
                except Exception:
                    break
    return {}


def _parse_vote(data: dict, raw: str) -> str:
    """Robustly decide approve/reject: explicit JSON vote wins; else infer from text.
    Never silently default to reject when the model clearly approved."""
    v = str(data.get("vote", "")).strip().lower()
    if v.startswith("approv"):
        return "approve"
    if v.startswith(("reject", "deny", "fail", "no")):
        return "reject"
    low = (raw or "").lower()
    if "approve" in low and "reject" not in low:
        return "approve"
    if "reject" in low and "approve" not in low:
        return "reject"
    # ambiguous → lean approve only if an explicit approve token exists
    return "approve" if re.search(r'"?vote"?\s*[:=]\s*"?approv', low) else "reject"

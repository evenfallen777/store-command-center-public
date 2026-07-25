"""Sender-side client library: helpers that reach OUT to a peer's Store over
HTTP with OUR key. These are NOT routes — other modules import them
(`from routers.peers import delegate_llm`)."""
from fastapi import HTTPException

from deps import httpx
from crypto import dec

from ._base import _get_peer


def _call_peer(peer, method: str, path: str, body: dict = None, timeout: int = 60):
    """HTTP to a peer's Store: base_url (incl. any /store prefix) + path, with OUR key."""
    url = (peer["base_url"] or "").rstrip("/") + path
    try:
        r = httpx.request(method, url, json=body, timeout=timeout,
                          headers={"X-Peer-Key": dec(peer["token_out"] or "")})
    except Exception as e:
        raise HTTPException(502, f"Could not reach peer '{peer['name']}': {e}")
    if r.status_code >= 400:
        detail = ""
        try:
            detail = (r.json().get("detail") or r.json().get("error") or "")[:200]
        except Exception:
            detail = r.text[:200]
        raise HTTPException(502, f"Peer '{peer['name']}' said HTTP {r.status_code}: {detail}")
    try:
        return r.json()
    except Exception:
        raise HTTPException(502, f"Peer '{peer['name']}' returned a non-JSON response.")


# ── Sender-side helpers: run one of OUR llm jobs on a friend's node ──────────
def peer_has_model(peer, model: str) -> bool:
    """Can this peer's node serve `model`? Sender-side gate: different installs have
    different models downloaded, so a job that NEEDS a specific model must stay in
    the local queue when no peer model matches (never sent then errored remotely)."""
    if not model:
        return True     # no specific model needed → any peer can help
    try:
        remote = _call_peer(peer, "GET", "/api/peers/rpc/models", timeout=20)
    except HTTPException:
        return False    # unreachable peer can't help either
    return model in (remote.get("models") or [])


def peer_quote(peer) -> dict:
    """Fetch the peer's ADVERTISED price BEFORE we send it work. A peer running an
    older build has no rpc/price route — we then fall back to per-job mode, which is
    exactly what that build will charge, so pairing across versions keeps working."""
    try:
        q = _call_peer(peer, "GET", "/api/peers/rpc/price", timeout=20)
        if q.get("mode") in ("job", "token"):
            return q
    except HTTPException:
        pass
    return {"mode": "job", "billing": True, "price_per_1k_completion_jly": 0.0,
            "price_per_1k_prompt_jly": 0.0, "legacy": True}


def delegate_llm(peer_id: int, system: str, user: str, model: str = None,
                 max_tokens: int = 1500, wait: int = 120):
    """Submit an llm job to an approved peer's queue and block for the result.
    Returns the output text, or raises. Raises 409 when the peer lacks the required
    model — callers treat that as 'keep the job in the local queue'. (Used by future
    'borrow a friend's GPU' features; also the Crypto → JellyCoin → Peers test button.)

    Billing (consumer side). We quote FIRST, then send. When the peer advertises
    token mode we pay for the ANSWER only, at the quoted rate, and never more than
    our OWN count of the answer times the tolerance — a peer that inflates its
    completion_tokens gets billed down and logged. Before the job is even sent we
    price the WORST case (max_tokens at the quoted rate) against the owner's caps
    and refuse the whole job if it would breach one — no partial spend, ever.
    """
    import time
    import jellycoin_extra as jx
    peer = _get_peer(peer_id)
    if not peer_has_model(peer, model):
        raise HTTPException(409, f"Peer '{peer['name']}' doesn't have model '{model}' — "
                                 "keeping the job in the local queue.")
    quote = peer_quote(peer)
    quoted_rates = {"completion": int(float(quote.get("price_per_1k_completion_jly") or 0) * 1_000_000),
                    "prompt": int(float(quote.get("price_per_1k_prompt_jly") or 0) * 1_000_000)}
    if jx.peer_billing_enabled():
        worst = (jx.token_cost(0, int(max_tokens or 0), quoted_rates, "spent")
                 if quote.get("mode") == "token" else jx.peer_job_price("llm"))
        cap = jx.peer_cap_check(peer["name"], worst)
        if not cap["ok"]:
            raise HTTPException(402, f"Refusing to send this job to '{peer['name']}': {cap['reason']}. "
                                     "Raise the cap in Crypto → JellyCoin → Peers → Compute pricing, "
                                     "or run the job locally.")
    resp = _call_peer(peer, "POST", "/api/peers/rpc/job",
                      {"kind": "llm", "system": system, "user": user,
                       "model": model, "max_tokens": max_tokens})
    jid = resp.get("job_id")
    deadline = time.time() + wait
    while time.time() < deadline:
        st = _call_peer(peer, "GET", f"/api/peers/rpc/job/{jid}")
        if st.get("status") == "done":
            result = st.get("result") or {}
            out = result.get("output", "")
            usage = result.get("usage") or {}
            try:
                if quote.get("mode") == "token" and usage:
                    # count the answer OURSELVES — never trust their number alone
                    jx.peer_settle_tokens(
                        peer["name"], "spent", kind="llm", model=usage.get("model") or model,
                        prompt_tokens=0 if not quoted_rates["prompt"] else int(usage.get("prompt_tokens") or 0),
                        completion_tokens=int(usage.get("completion_tokens") or 0),
                        reported=bool(usage.get("reported")),
                        own_estimate=jx.estimate_tokens(out),
                        quoted_rates=quoted_rates)      # the price they quoted BEFORE the job
                else:
                    import jellycoin
                    jellycoin.peer_job_credit(peer["name"], "llm")
            except Exception:
                pass
            return out
        if st.get("status") == "error":
            raise HTTPException(502, f"Peer job failed on {peer['name']}: {st.get('error')}")
        time.sleep(2)
    raise HTTPException(504, f"Peer job timed out on {peer['name']}.")

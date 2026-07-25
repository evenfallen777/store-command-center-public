"""RPC endpoints — called BY peers, X-Peer-Key auth, out of MCP/docs schema.
These are the ONLY paths a peer key can reach, and none of them read or write
host settings/prompts/git."""
import hmac
import json

from fastapi import HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from deps import get_conn, httpx, logger, _call_lmstudio, LMSTUDIO_URL
from crypto import enc
from prompts import get_prompt
from orchestrator import orch

from ._base import (
    router, _hash, _new_key, _peer_from_key, _my_name, _git_info,
    _set_job, _set_review, _MAX_DIFF, _MAX_PROMPT,
)
from . import metering


# ─────────────────────────────────────────────────────────────────────────────
# RPC endpoints — called BY peers, X-Peer-Key auth, out of MCP/docs schema.
# These are the ONLY paths a peer key can reach, and none of them read or write
# host settings/prompts/git.
# ─────────────────────────────────────────────────────────────────────────────
class PairIn(BaseModel):
    invite_key: str
    name: str
    base_url: Optional[str] = None
    key: str                       # the key THEY will accept from US
    branch: Optional[str] = None


@router.post("/api/peers/rpc/pair", include_in_schema=False)
def rpc_pair(body: PairIn):
    """Redeem a one-time invite key. Creates the peer as PENDING (host must approve)
    and hands back the key we'll accept from them once approved."""
    h = _hash((body.invite_key or "").strip())
    conn = get_conn()
    inv = None
    for row in conn.execute("SELECT * FROM peer_invites WHERE used_by IS NULL").fetchall():
        if hmac.compare_digest(row["key_hash"], h):
            inv = row
            break
    if not inv:
        conn.close()
        raise HTTPException(401, "Invalid or already-used invite key.")
    key_we_accept = _new_key("peer")
    cur = conn.execute(
        "INSERT INTO peers (name,base_url,token_in_hash,token_out,status) VALUES (?,?,?,?,?)",
        ((body.name or "friend").strip()[:60], (body.base_url or "").strip()[:300],
         _hash(key_we_accept), enc((body.key or "").strip()), "pending"))
    pid = cur.lastrowid
    conn.execute("UPDATE peer_invites SET used_by=? WHERE id=?", (pid, inv["id"]))
    conn.commit()
    conn.close()
    logger.info(f"peer pair request from '{body.name}' — pending approval (peer #{pid})")
    return {"ok": True, "key": key_we_accept, "name": _my_name(),
            "note": "Pending approval on this side — ask your friend to approve you "
                    "in their Crypto → JellyCoin → Peers."}


def _my_llm_models():
    """Models this node's LM Studio can serve + the one currently loaded. Best-effort:
    empty list (never an error) when LM Studio is unreachable."""
    models = []
    try:
        r = httpx.get(f"{LMSTUDIO_URL}/models", timeout=8)
        models = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
    except Exception:
        pass
    return {"models": models, "loaded": getattr(orch, "_current_llm_model", None)}


@router.get("/api/peers/rpc/models", include_in_schema=False)
def rpc_models(request: Request):
    """What models this node could run for a peer — used by the SENDER to decide
    whether a job can be delegated here at all (no matching model → keep it local)."""
    _peer_from_key(request)
    return _my_llm_models()


@router.get("/api/peers/rpc/ping", include_in_schema=False)
def rpc_ping(request: Request):
    """Coarse liveness + progress info for an approved peer (the 'share progress' feed)."""
    _peer_from_key(request)
    conn = get_conn()
    promoted = [{"title": r["title"], "when": r["updated_at"]} for r in conn.execute(
        "SELECT title, updated_at FROM swarm_jobs WHERE status='done' "
        "ORDER BY updated_at DESC LIMIT 5").fetchall()]
    conn.close()
    return {"ok": True, "name": _my_name(), **_git_info(), "recently_promoted": promoted}


@router.get("/api/peers/rpc/price", include_in_schema=False)
def rpc_price(request: Request):
    """The price this node is CHARGING RIGHT NOW, for a consumer to fetch BEFORE it
    submits work. The consumer settles against this copy, so a provider that raises
    its rate after the fact cannot bill the new rate for an already-quoted job.
    Older builds have no such route — the consumer then falls back to per-job mode."""
    _peer_from_key(request)
    import jellycoin_extra as jx
    return {"ok": True, "name": _my_name(), **jx.peer_price_quote()}


@router.get("/api/peers/rpc/wallet", include_in_schema=False)
def rpc_wallet(request: Request):
    """A buddy checks their JellyCoin wallet ON OUR CHAIN: balance earned lending us
    compute, spent using our AI helper, and their recent txs. Read-only by design —
    spending happens implicitly via jobs (and the host's Crypto→JellyCoin tab)."""
    peer = _peer_from_key(request)
    import jellycoin
    w = jellycoin.wallet(f"peer:{peer['name']}", kind="peer")
    conn = get_conn()
    txs = [dict(r) for r in conn.execute(
        "SELECT time,frm,dst,amount,kind,memo FROM jelly_txs WHERE frm=? OR dst=? "
        "ORDER BY id DESC LIMIT 20", (w["name"], w["name"])).fetchall()]
    conn.close()
    return {"ok": True, "symbol": jellycoin.SYMBOL, "wallet": w["name"],
            "address": w["address"], "balance_jly": w["balance"] / jellycoin.UNIT,
            "billing": jellycoin.peer_billing_enabled(),
            "price_per_llm_job_jly": jellycoin.peer_job_price("llm") / jellycoin.UNIT,
            "price_per_review_jly": jellycoin.peer_job_price("review") / jellycoin.UNIT,
            "quote": __import__("jellycoin_extra").peer_price_quote(),
            "recent_txs": txs}


class RpcReviewIn(BaseModel):
    title: Optional[str] = None
    diff: str


@router.post("/api/peers/rpc/review", include_in_schema=False)
def rpc_review(body: RpcReviewIn, request: Request):
    """A peer asks THIS node to review a diff: our local LLM reviews it through the
    unified queue, and our human can add a vote from Crypto → JellyCoin → Peers."""
    peer = _peer_from_key(request)
    if not peer["accept_reviews"]:
        raise HTTPException(403, "This node is not accepting review requests from you.")
    diff = (body.diff or "").strip()
    if not diff:
        raise HTTPException(400, "Empty diff.")
    title = (body.title or "untitled change").strip()[:200]
    conn = get_conn()
    cur = conn.execute("INSERT INTO peer_reviews (peer_id,title,diff) VALUES (?,?,?)",
                       (peer["id"], title, diff[:_MAX_DIFF]))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    sys_p = get_prompt("swarm_reviewer")
    user_p = f"JOB: {title}\n(from peer '{peer['name']}')\n\nDIFF:\n{diff[:9000]}"

    def _work():
        try:
            import swarm
            out = _call_lmstudio(sys_p, user_p, max_tokens=1500)
            data = swarm._extract_json(out)
            vote = swarm._parse_vote(data, out)
            comments = (data.get("comments") if isinstance(data, dict) else None) or out
            _set_review(rid, status="done", llm_vote=vote, llm_comments=str(comments)[:4000],
                        llm_model=getattr(orch, "_current_llm_model", None))
            # buddy economy: our LLM read their diff → charge their wallet like any
            # AI job (comped if broke). Billed only on a delivered verdict — an
            # errored review costs them nothing.
            try:
                import jellycoin
                jellycoin.peer_job_charge(peer["name"], "review")
            except Exception:
                pass
            return {"vote": vote}
        except Exception as e:  # a review must never wedge in 'reviewing'
            _set_review(rid, status="error", llm_comments=f"review failed: {e}"[:500])
            raise

    try:
        tid = orch.submit_llm(_work, desc=f"peer review for {peer['name']}: {title[:40]}", priority=2)
        _set_review(rid, orch_tid=tid)
    except Exception as e:
        _set_review(rid, status="error", llm_comments=f"queue error: {e}")
    return {"ok": True, "review_id": rid}


@router.get("/api/peers/rpc/review/{rid}", include_in_schema=False)
def rpc_review_status(rid: int, request: Request):
    peer = _peer_from_key(request)
    conn = get_conn()
    row = conn.execute("SELECT * FROM peer_reviews WHERE id=? AND peer_id=?",
                       (rid, peer["id"])).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Review not found")
    # error state still returns 200 so the requester sees the failure, not a retry loop.
    # not_found = the orch task was pruned/lost before finishing — also terminal.
    if row["status"] == "reviewing" and row["orch_tid"] is not None:
        t = orch.poll(row["orch_tid"]) or {}
        if t.get("status") in ("error", "cancelled", "not_found"):
            _set_review(rid, status="error",
                        llm_comments=f"review failed: {t.get('error') or t.get('status')}")
            row = dict(row) | {"status": "error"}
    return {"status": row["status"], "llm_vote": row["llm_vote"],
            "llm_comments": row["llm_comments"], "llm_model": row["llm_model"],
            "human_vote": row["human_vote"], "human_comments": row["human_comments"]}


class RpcJobIn(BaseModel):
    kind: str                      # llm | embedding
    system: Optional[str] = None   # llm: system prompt (THE REQUESTER'S, never ours)
    user: Optional[str] = None     # llm: user prompt
    model: Optional[str] = None    # accepted but IGNORED for llm — peers can't swap host models
    max_tokens: int = 1500
    input: Optional[str] = None    # embedding: text to embed
    embed_model: Optional[str] = None


@router.post("/api/peers/rpc/job", include_in_schema=False)
def rpc_job(body: RpcJobIn, request: Request):
    """A peer submits COMPUTE into this node's unified queue (host opt-in per peer).
    llm → queued behind local work at background priority. embedding → LM Studio's
    embeddings passthrough (coexists with the resident chat model; no model swap)."""
    peer = _peer_from_key(request)
    if not peer["accept_work"]:
        raise HTTPException(403, "This node is not accepting work from you. "
                                 "Ask your friend to enable 'accept work' for you.")
    if body.kind not in ("llm", "embedding"):
        raise HTTPException(400, "kind must be llm or embedding")
    allowed = {k.strip() for k in (peer["work_kinds"] or "llm,embedding").split(",") if k.strip()}
    if body.kind not in allowed:
        raise HTTPException(403, f"This node only accepts {', '.join(sorted(allowed)) or 'no'} "
                                 f"work from you (not {body.kind}).")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO peer_jobs (peer_id,kind,payload) VALUES (?,?,?)",
        (peer["id"], body.kind,
         json.dumps({"model": body.model or body.embed_model, "max_tokens": body.max_tokens})))
    conn.commit()
    jid = cur.lastrowid
    conn.close()
    # JellyCoin buddy economy. TWO modes, and the mode we bill on is the one this
    # node ADVERTISED (rpc/price) before the job ran:
    #   job   (legacy default) — flat fee, charged at submit, exactly as before.
    #   token — "you only pay for the answers": nothing is charged now; the meter
    #           settles inside _work() once we know how many completion tokens the
    #           answer actually cost. An errored job therefore costs them nothing.
    import jellycoin_extra as jx
    quote = jx.peer_price_quote()
    if quote["mode"] != "token" or body.kind != "llm":
        try:
            import jellycoin
            jellycoin.peer_job_charge(peer["name"], body.kind)
        except Exception:
            pass

    if body.kind == "embedding":
        text = (body.input or "")[:8000]
        if not text.strip():
            _set_job(jid, status="error", error="empty input")
            raise HTTPException(400, "Empty embedding input.")
        try:
            # Go through the store's own embeddings proxy (localhost /api bypasses auth)
            # rather than hitting LM Studio directly: it adds the LM Studio auth header
            # and is deliberately coexist-safe (no orch model-swap). Same path world_taste uses.
            r = httpx.post("http://127.0.0.1:8787/api/llm/v1/embeddings", timeout=60,
                           json={"model": body.embed_model or "", "input": text})
            r.raise_for_status()
            _set_job(jid, status="done", result=json.dumps(r.json())[:500_000])
        except Exception as e:
            _set_job(jid, status="error", error=str(e)[:300])
        return {"ok": True, "job_id": jid, "quote": quote}

    sysp = (body.system or "")[:_MAX_PROMPT]
    userp = (body.user or "")[:_MAX_PROMPT]
    if not userp.strip():
        _set_job(jid, status="error", error="empty prompt")
        raise HTTPException(400, "Empty llm prompt.")

    def _work():
        try:
            m = metering.call_metered(_call_lmstudio, sysp, userp,
                                      min(int(body.max_tokens or 1500), 4000))
            billed = None
            if quote["mode"] == "token":
                # settle on the ANSWER, at the rate we advertised before the job ran
                billed = jx.peer_settle_tokens(
                    peer["name"], "earned", kind="llm", model=m.get("model"),
                    prompt_tokens=m["prompt_tokens"], completion_tokens=m["completion_tokens"],
                    reported=m["reported"],
                    quoted_rates={"completion": int(quote["price_per_1k_completion_jly"] * 1_000_000),
                                  "prompt": int(quote["price_per_1k_prompt_jly"] * 1_000_000)})
            _set_job(jid, status="done", result=json.dumps({
                "output": m["output"],
                # the consumer needs these to check the bill against its own count
                "usage": {"prompt_tokens": m["prompt_tokens"],
                          "completion_tokens": m["completion_tokens"],
                          "reported": m["reported"], "model": m.get("model")},
                "quote": quote,
                "billed": ({k: billed.get(k) for k in ("billed", "amount", "reason")} if billed else None),
            })[:500_000])
        except Exception as e:
            _set_job(jid, status="error", error=str(e)[:300])
            raise
        return {"job": jid}

    # model is deliberately NOT passed through: a requested model would make the
    # orchestrator evict the host's resident chat model (lms unload/load) — a remote
    # peer must never churn the host's GPU. Peer jobs borrow whatever is loaded.
    try:
        tid = orch.submit_llm(_work, desc=f"peer job from {peer['name']}", priority=2)
        _set_job(jid, orch_tid=tid)
    except Exception as e:
        _set_job(jid, status="error", error=str(e)[:300])
    return {"ok": True, "job_id": jid, "quote": quote}


@router.get("/api/peers/rpc/job/{jid}", include_in_schema=False)
def rpc_job_status(jid: int, request: Request):
    peer = _peer_from_key(request)
    conn = get_conn()
    row = conn.execute("SELECT * FROM peer_jobs WHERE id=? AND peer_id=?",
                       (jid, peer["id"])).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Job not found")
    # reconcile a job the queue dropped (cancel/clear/prune) before the closure ran,
    # so the peer never polls a permanently-'queued' ghost
    if row["status"] == "queued" and row["orch_tid"] is not None:
        t = orch.poll(row["orch_tid"]) or {}
        if t.get("status") in ("error", "cancelled", "not_found"):
            _set_job(jid, status="error", error=t.get("error") or t.get("status"))
            row = dict(row) | {"status": "error", "error": t.get("error") or t.get("status")}
    out = {"status": row["status"], "error": row["error"]}
    if row["status"] == "done" and row["result"]:
        try:
            out["result"] = json.loads(row["result"])
        except Exception:
            out["result"] = row["result"]
    return out

"""Local management endpoints (session-authenticated — the node's OWNER only):
list/invite/connect/approve/config/revoke/connection-info/model-check/status,
plus incoming-review voting and outgoing peer-review requests."""
import subprocess

from fastapi import HTTPException
from pydantic import BaseModel
from typing import Optional

from deps import get_conn, httpx
from config import GIT_BIN, PUBLIC_BASE_URL
from crypto import enc

from ._base import (
    router, _hash, _new_key, _git_info, _my_name, _get_peer,
    _set_review, _set_row, _MAX_DIFF,
)
from .client import _call_peer, delegate_llm
from .rpc import _my_llm_models


# ─────────────────────────────────────────────────────────────────────────────
# Local management endpoints (session-authenticated — the node's OWNER only)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/peers")
def list_peers():
    conn = get_conn()
    peers = [dict(r) for r in conn.execute(
        "SELECT id,name,base_url,status,accept_work,accept_reviews,work_kinds,last_seen,created_at "
        "FROM peers WHERE status != 'revoked' ORDER BY created_at").fetchall()]
    invites = conn.execute("SELECT COUNT(*) c FROM peer_invites WHERE used_by IS NULL").fetchone()["c"]
    reviews = [dict(r) for r in conn.execute(
        "SELECT id,peer_id,title,status,llm_vote,llm_model,human_vote,created_at "
        "FROM peer_reviews ORDER BY id DESC LIMIT 20").fetchall()]
    conn.close()
    return {"peers": peers, "open_invites": invites, "incoming_reviews": reviews}


class InviteIn(BaseModel):
    note: Optional[str] = None


@router.post("/api/peers/invite")
def create_invite(body: InviteIn = None):
    """One-time invite key. Shown ONCE — share it with your friend out-of-band."""
    body = body or InviteIn()
    key = _new_key("pinv")
    conn = get_conn()
    conn.execute("INSERT INTO peer_invites (key_hash,note) VALUES (?,?)",
                 (_hash(key), (body.note or "").strip()[:120]))
    conn.commit()
    conn.close()
    return {"ok": True, "invite_key": key,
            "note": "Give this to your friend (once). They paste it in THEIR "
                    "Crypto → JellyCoin → Peers → Connect, along with this install's URL."}


class ConnectIn(BaseModel):
    name: str
    url: str
    invite_key: str


@router.post("/api/peers/connect")
def connect_peer(body: ConnectIn):
    """Run on the JOINING side: redeem a friend's invite key against their URL.
    We generate the key we'll accept from them, send it in the pair call, and store
    the key they hand back. They still have to approve us before rpc works."""
    name = (body.name or "").strip()[:60] or "friend"
    url = (body.url or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Peer URL must start with http(s):// — e.g. https://friend.example.com/store")
    key_we_accept = _new_key("peer")
    me = _git_info()
    my_url = PUBLIC_BASE_URL or ""
    try:
        r = httpx.post(url + "/api/peers/rpc/pair", timeout=30, json={
            "invite_key": body.invite_key.strip(),
            "name": _my_name(), "base_url": my_url, "key": key_we_accept,
            "branch": me.get("branch")})
    except Exception as e:
        raise HTTPException(502, f"Could not reach {url}: {e}")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail") or r.text[:200]
        except Exception:
            detail = r.text[:200]
        raise HTTPException(502, f"Pairing refused by peer: {detail}")
    data = r.json()
    their_key = data.get("key") or ""
    if not their_key:
        raise HTTPException(502, "Peer did not return a key — is their Store up to date?")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO peers (name,base_url,token_in_hash,token_out,status) VALUES (?,?,?,?,?)",
        (name, url, _hash(key_we_accept), enc(their_key), "approved"))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return {"ok": True, "peer_id": pid, "their_name": data.get("name"),
            "message": f"Paired with {data.get('name') or name}. They still need to "
                       "APPROVE you on their side before requests go through."}


@router.post("/api/peers/{pid}/approve")
def approve_peer(pid: int):
    _get_peer(pid, statuses=("pending", "approved"))
    conn = get_conn()
    conn.execute("UPDATE peers SET status='approved' WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"ok": True}


class PeerConfigIn(BaseModel):
    accept_work: Optional[bool] = None
    accept_reviews: Optional[bool] = None
    work_kinds: Optional[str] = None  # csv of allowed kinds, e.g. "llm,embedding"
    name: Optional[str] = None
    base_url: Optional[str] = None   # fix a wrong advertised URL (must include any /store prefix)


@router.post("/api/peers/{pid}/config")
def config_peer(pid: int, body: PeerConfigIn):
    _get_peer(pid, statuses=None)
    conn = get_conn()
    if body.accept_work is not None:
        conn.execute("UPDATE peers SET accept_work=? WHERE id=?", (1 if body.accept_work else 0, pid))
    if body.accept_reviews is not None:
        conn.execute("UPDATE peers SET accept_reviews=? WHERE id=?", (1 if body.accept_reviews else 0, pid))
    if body.work_kinds is not None:
        kinds = ",".join(k for k in ("llm", "embedding")
                         if k in {t.strip() for t in body.work_kinds.split(",")})
        conn.execute("UPDATE peers SET work_kinds=? WHERE id=?", (kinds, pid))
    if body.name:
        conn.execute("UPDATE peers SET name=? WHERE id=?", (body.name.strip()[:60], pid))
    if body.base_url is not None:
        conn.execute("UPDATE peers SET base_url=? WHERE id=?", (body.base_url.strip().rstrip("/")[:300], pid))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.delete("/api/peers/{pid}")
def revoke_peer(pid: int):
    """Revoke: their key stops working immediately. The row is kept for the log."""
    _get_peer(pid, statuses=None)
    conn = get_conn()
    conn.execute("UPDATE peers SET status='revoked' WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/api/peers/connection-info")
def connection_info():
    """What YOUR friend needs to reach this node — shown in Crypto → JellyCoin → Peers."""
    import socket
    from config import PORT
    lan_ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    return {"public_url": PUBLIC_BASE_URL or "", "port": PORT, "lan_ip": lan_ip}


@router.get("/api/peers/my-wallets")          # BEFORE /api/peers/{pid}/... — a literal
def my_wallets_on_buddy_chains():             # segment must not be eaten as a {pid}
    """What WE have earned on each buddy's chain.

    JellyCoin is per-node: our mining shares, review pay and compute credits on a
    buddy's network live in OUR peer:<name> wallet on THEIR ledger, not in our own
    wallet list. Each buddy exposes it at rpc/wallet (authenticated with the key
    they issued us), so fan out and collect. One unreachable buddy must never sink
    the panel — failures come back as a per-peer note, not a 502."""
    conn = get_conn()
    peers = [dict(r) for r in conn.execute(
        "SELECT id,name,base_url FROM peers WHERE status='approved' ORDER BY name").fetchall()]
    conn.close()
    out, total = [], 0.0
    for p in peers:
        row = {"peer_id": p["id"], "peer": p["name"], "base_url": p["base_url"]}
        try:
            w = _call_peer(_get_peer(p["id"]), "GET", "/api/peers/rpc/wallet", timeout=15)
            bal = float(w.get("balance_jly") or 0)
            row.update({"ok": True, "wallet": w.get("wallet"), "address": w.get("address"),
                        "balance_jly": bal, "symbol": w.get("symbol") or "JLY",
                        "billing": w.get("billing"),
                        "price_per_llm_job_jly": w.get("price_per_llm_job_jly"),
                        "price_per_review_jly": w.get("price_per_review_jly"),
                        "recent_txs": (w.get("recent_txs") or [])[:8]})
            total += bal
        except HTTPException as e:            # offline / revoked / older build without the route
            row.update({"ok": False, "error": str(e.detail)[:160]})
        except Exception as e:
            row.update({"ok": False, "error": str(e)[:160]})
        out.append(row)
    return {"wallets": out, "total_jly": round(total, 6),
            "reachable": sum(1 for r in out if r.get("ok")), "peers": len(out)}


@router.get("/api/peers/{pid}/model-check")   # NOT .../models — that path segment would
def peer_models(pid: int):                    # shadow /api/peers/rpc/models ({pid}="rpc")
    """Ask a peer which models their node can serve (sender-side capability check)."""
    peer = _get_peer(pid)
    return {"ok": True, "peer": peer["name"],
            "remote": _call_peer(peer, "GET", "/api/peers/rpc/models", timeout=20),
            "local": _my_llm_models()}


@router.get("/api/peers/{pid}/status")
def peer_status(pid: int):
    """Ping the peer and show their coarse progress (name, version, recent promotes)."""
    peer = _get_peer(pid)
    return {"ok": True, "peer": peer["name"], "remote": _call_peer(peer, "GET", "/api/peers/rpc/ping")}


class HumanVoteIn(BaseModel):
    vote: str                      # approve | reject
    comments: Optional[str] = None


@router.post("/api/peers/reviews/{rid}/vote")
def human_vote(rid: int, body: HumanVoteIn):
    """The HOST human's own vote on a review a friend sent to this node."""
    if body.vote not in ("approve", "reject"):
        raise HTTPException(400, "vote must be approve or reject")
    conn = get_conn()
    row = conn.execute("SELECT id FROM peer_reviews WHERE id=?", (rid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Review not found")
    _set_review(rid, human_vote=body.vote, human_comments=(body.comments or "").strip()[:2000])
    return {"ok": True}


# ── Outgoing review requests (us → a peer, about one of OUR swarm jobs) ──────
class ReviewJobIn(BaseModel):
    job_id: int


@router.post("/api/peers/{pid}/review-job")
def request_peer_review(pid: int, body: ReviewJobIn):
    """Send one of our swarm jobs' dev-branch diff to a peer for review."""
    peer = _get_peer(pid)
    conn = get_conn()
    job = conn.execute("SELECT * FROM swarm_jobs WHERE id=?", (body.job_id,)).fetchone()
    conn.close()
    if not job:
        raise HTTPException(404, "Swarm job not found")
    from config import REPO_DEV
    try:
        r = subprocess.run([GIT_BIN, "-C", REPO_DEV, "diff", "master...dev"],
                           capture_output=True, text=True, timeout=30)
        diff = r.stdout if r.returncode == 0 else ""
        if not diff.strip():
            r = subprocess.run([GIT_BIN, "-C", REPO_DEV, "show", "--stat", "--patch", "HEAD"],
                               capture_output=True, text=True, timeout=30)
            diff = r.stdout
    except Exception as e:
        raise HTTPException(500, f"Could not read the job diff: {e}")
    if not diff.strip():
        raise HTTPException(400, "No diff to review on the dev branch.")
    resp = _call_peer(peer, "POST", "/api/peers/rpc/review",
                      {"title": job["title"], "diff": diff[:_MAX_DIFF]}, timeout=60)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO peer_review_requests (peer_id,job_id,remote_review_id) VALUES (?,?,?)",
        (pid, body.job_id, resp.get("review_id")))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    import swarm
    swarm._ev(body.job_id, f"peer:{peer['name']}", "system",
              f"Peer review requested from {peer['name']} — sent the CURRENT dev-branch "
              "diff (master...dev), which may include other in-flight jobs' work.")
    return {"ok": True, "request_id": rid}


@router.get("/api/peers/review-requests")
def review_requests(job_id: int):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT rr.*, p.name AS peer_name FROM peer_review_requests rr "
        "JOIN peers p ON p.id = rr.peer_id WHERE rr.job_id=? ORDER BY rr.id", (job_id,)).fetchall()]
    conn.close()
    return {"requests": rows}


@router.post("/api/peers/review-requests/{rid}/refresh")
def refresh_review_request(rid: int):
    """Poll the peer for the verdict; on first arrival, record it as an advisory
    vote event on the swarm job (shows up in the job timeline next to Approve)."""
    conn = get_conn()
    req = conn.execute("SELECT * FROM peer_review_requests WHERE id=?", (rid,)).fetchone()
    conn.close()
    if not req:
        raise HTTPException(404, "Review request not found")
    peer = _get_peer(req["peer_id"])
    remote = _call_peer(peer, "GET", f"/api/peers/rpc/review/{req['remote_review_id']}")
    fields = {"status": remote.get("status") or "sent",
              "llm_vote": remote.get("llm_vote"), "llm_comments": remote.get("llm_comments"),
              "llm_model": remote.get("llm_model"), "human_vote": remote.get("human_vote"),
              "human_comments": remote.get("human_comments")}
    first_verdict = (remote.get("status") == "done" and req["status"] != "done")
    # the friend's HUMAN vote can land after their LLM verdict — record it when it appears
    new_human_vote = bool(remote.get("human_vote")) and remote.get("human_vote") != req["human_vote"]
    _set_row("peer_review_requests", rid, **fields)
    if first_verdict:
        # buddy economy: their box reviewed our diff → treasury pays their wallet,
        # same as a delegated llm job. Once, when the verdict first lands; a later
        # human vote on the same review is not billed again.
        try:
            import jellycoin
            jellycoin.peer_job_credit(peer["name"], "review")
        except Exception:
            pass
    if first_verdict or new_human_vote:
        import swarm
        vote = remote.get("human_vote") or remote.get("llm_vote")
        who = f"peer:{peer['name']}" + ("" if remote.get("human_vote") else " (their LLM)")
        swarm._ev(req["job_id"], who, "vote",
                  (remote.get("human_comments") or remote.get("llm_comments") or "")[:2000],
                  vote=vote, model=remote.get("llm_model"))
    return {"ok": True, "request": {**dict(req), **fields, "peer_name": peer["name"]}}


class TestJobIn(BaseModel):
    prompt: Optional[str] = None


@router.post("/api/peers/{pid}/test-job")
def send_test_job(pid: int, body: TestJobIn = None):
    """End-to-end check of the work-sharing path against a peer."""
    body = body or TestJobIn()
    out = delegate_llm(pid, "You are a helpful assistant. Answer in one short sentence.",
                       body.prompt or "Say hello and confirm you are a friend's Store node.",
                       max_tokens=100, wait=90)
    return {"ok": True, "output": out}

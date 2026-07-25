"""Peer network — connect two (or more) Store installs so friends can help each other.

What peers CAN do on your node (and you on theirs), always host-controlled:
  • Review your swarm jobs: you send a diff, THEIR node runs its local LLM reviewer
    and their human can vote too; the verdict lands on your job as an advisory vote.
  • Lend compute: submit llm/embedding jobs into your unified queue (only if you
    flip "accept work" on for that peer) and poll the result.
  • Share coarse progress: name, branch/commit, recently promoted job titles.

What peers can NEVER do: reach any other API path (the auth middleware only exempts
/api/peers/rpc/*, and every rpc endpoint re-checks the X-Peer-Key header itself), so
they cannot read or change settings, prompts, money, git, or anything else. Peer job
payloads carry the REQUESTER's full prompt text — the host's prompts are never used,
read, or written. Embedding jobs go through LM Studio's embeddings passthrough, which
coexists with the resident chat model (no model swapping on the host).

Pairing = invite key + human approval:
  1. Host: Crypto → JellyCoin → Peers → "New invite key" → give it to the friend out-of-band.
  2. Friend: Crypto → JellyCoin → Peers → "Connect" with the host's URL + invite key. Their node
     calls our /api/peers/rpc/pair; keys are exchanged (each side stores the OTHER
     side's key encrypted, and a HASH of the key it accepts — a DB leak exposes no
     usable inbound credential).
  3. Host sees the peer as PENDING and must approve it before any rpc call works.
"""
from ._base import router
# billing FIRST: its literal /api/peers/billing/... paths must be registered before
# api.py's /api/peers/{pid}/... or "billing" gets eaten as a {pid} and 422s.
from . import billing, api, rpc  # noqa: F401 — importing registers their @router routes
from .client import _call_peer, peer_has_model, delegate_llm

__all__ = ["router", "_call_peer", "peer_has_model", "delegate_llm"]

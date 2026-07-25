"""Unified GPU job scheduler — the PURE decision core from GPU_QUEUE.md.

This module contains ONLY the scheduling algorithm (priority + model/type affinity +
anti-starvation) as pure functions over a list of Jobs. It has NO side effects. The
orchestrator feeds it the REAL unified queue: LLM tasks AND image/video media tickets
(every image/video job registers a ticket via Orchestrator._media_gate before it is
allowed to evict whatever is resident, so the scheduler decides the order for ALL
GPU work, not just LLM tasks).

The resident model is a (family, model) pair — family 'llm' | 'image' | 'video' —
because the GPU holds exactly ONE thing at a time (single-VRAM invariant): an LM
Studio LLM, a ComfyUI image checkpoint, or a video pipeline. A bare string is
accepted everywhere for backward compatibility and means ('llm', <string>).

The rules:
  1. Run the highest-priority pending job (priority 0 = most urgent).
  2. Affinity: among equally-urgent jobs, prefer one that BORROWS what is already
     resident — same family AND compatible model — so same-model work batches and the
     VRAM swap is paid once. A job of a DIFFERENT family (e.g. an image job while an
     LLM is resident) never counts as borrowing: running it forces a cross-type
     eviction, which is exactly the reload-thrash this module exists to prevent. So a
     lineup like llm-image-llm-llm-image-video runs as llm,llm,llm → image,image →
     video when the LLM jobs use the resident model.
  3. Aging (anti-starvation, cross-tier): a job's *effective* priority rises one tier
     per `age_step_sec` waited, so a low-priority job can never be deferred forever
     behind a stream of higher-priority work.
  4. Starvation cap (anti-starvation, within-tier): affinity is overridden when the
     oldest NON-borrowing job in the tier has waited `starve_cap_sec` LONGER than the
     oldest borrowing job. A batch enqueued together still drains in one run (its
     members aren't meaningfully older than one another), but a continuous stream of
     fresh same-model jobs cannot starve a waiting image/video/other-model job: as the
     batch drains, its oldest pending member keeps getting younger relative to the
     waiter, and past the cap the waiter wins. This is what bounds a video job's wait
     behind an endless LLM batch — the wait is bounded by the backlog that existed
     within `starve_cap_sec` of the waiter's own enqueue, never by future arrivals.
"""
from dataclasses import dataclass, field
from typing import Optional, Tuple, Union

Resident = Union[None, str, Tuple[str, Optional[str]]]


@dataclass
class Job:
    id: int
    kind: str                      # 'llm' | 'vision' | 'image' | 'video'
    model: Optional[str] = None    # required model; '*' = borrow whatever's loaded; None = n/a
    priority: int = 1              # 0 user-facing > 1 autobuild/vision > 2 background
    origin: str = "store"          # 'store' | 'openclaw' | 'world'
    enqueued_at: float = 0.0       # epoch seconds (pass in — module stays time-source free)
    status: str = "pending"        # pending | running | done | error | cancelled
    _meta: dict = field(default_factory=dict)


def _family(kind: str) -> str:
    """VRAM family of a job kind: what kind of thing must be resident to run it.
    Vision runs on an LM Studio VLM, so it shares the 'llm' family."""
    return "llm" if kind in ("llm", "vision") else kind


def _norm_resident(resident: Resident) -> Optional[Tuple[str, Optional[str]]]:
    """Normalize the resident descriptor: None, a legacy bare LLM-model string, or a
    (family, model) pair → (family, model) | None. (family, None) means 'something of
    this family is resident but its exact model is unknown' — still worth batching."""
    if resident is None:
        return None
    if isinstance(resident, str):
        return ("llm", resident)
    fam, model = resident
    return (fam, model)


def effective_priority(job: Job, now: float, age_step_sec: float = 60.0) -> int:
    """Priority after aging. Lower = more urgent. Each `age_step_sec` waited promotes the
    job one tier (toward 0), so a long-waiting low-priority job eventually wins."""
    waited = max(0.0, now - (job.enqueued_at or now))
    bumps = int(waited // age_step_sec) if age_step_sec > 0 else 0
    return max(0, int(job.priority) - bumps)


def borrows_resident(job: Job, resident_model: Resident) -> bool:
    """True if running this job forces NO model swap and NO cross-type eviction:
    the job's family matches what is resident, and its model is the resident one,
    borrow-any ('*'), or unspecified (None = use whatever of that family is loaded)."""
    resident = _norm_resident(resident_model)
    if resident is None:
        return job.model in ("*", None)
    r_fam, r_model = resident
    if _family(job.kind) != r_fam:
        return False               # cross-type ⇒ running it evicts the resident model
    return job.model in (r_model, "*", None)


def pick_next(jobs, resident_model: Resident, now: float,
              age_step_sec: float = 60.0, starve_cap_sec: Optional[float] = None):
    """Choose the next job to run, or None if nothing is pending.

    jobs: iterable of Job. resident_model: what is currently loaded in VRAM — a
    (family, model) pair, a bare LLM-model string (legacy), or None.
    starve_cap_sec: within-tier affinity override threshold (default 2×age_step_sec).
    """
    pending = [j for j in jobs if j.status == "pending"]
    if not pending:
        return None
    if starve_cap_sec is None:
        starve_cap_sec = 2.0 * age_step_sec
    # 1. best (lowest) effective priority tier
    best = min(effective_priority(j, now, age_step_sec) for j in pending)
    tier = [j for j in pending if effective_priority(j, now, age_step_sec) == best]
    resident = _norm_resident(resident_model)
    # 2. affinity within the tier — prefer a job needing no swap/eviction…
    if resident is not None:
        affine = [j for j in tier if borrows_resident(j, resident)]
        others = [j for j in tier if not borrows_resident(j, resident)]
        if affine:
            # …UNLESS a non-borrowing job has already waited starve_cap_sec longer
            # than the oldest borrowing one — then it runs now (hard starvation bound).
            if others and starve_cap_sec > 0:
                oldest_affine_enq = min(j.enqueued_at for j in affine)
                starved = [j for j in others
                           if oldest_affine_enq - j.enqueued_at > starve_cap_sec]
                if starved:
                    return min(starved, key=lambda j: (j.enqueued_at, j.id))
            return min(affine, key=lambda j: (j.enqueued_at, j.id))
    # 3. otherwise oldest in the tier
    return min(tier, key=lambda j: (j.enqueued_at, j.id))


def order(jobs, resident_model: Resident, now: float,
          age_step_sec: float = 60.0, starve_cap_sec: Optional[float] = None):
    """Full run order (repeatedly pick_next, updating the notional resident to the
    picked job's family+model). Useful for previews/tests; the live scheduler picks
    one at a time."""
    remaining = [j for j in jobs if j.status == "pending"]
    resident = _norm_resident(resident_model)
    out = []
    # snapshot so we don't mutate caller state
    remaining = list(remaining)
    while remaining:
        nxt = pick_next(remaining, resident, now, age_step_sec, starve_cap_sec)
        if nxt is None:
            break
        out.append(nxt)
        remaining = [j for j in remaining if j.id != nxt.id]
        fam = _family(nxt.kind)
        if nxt.model not in (None, "*"):
            resident = (fam, nxt.model)
        elif resident is None or resident[0] != fam:
            resident = (fam, None)   # family switched; exact model unknown
        # else: borrowed the same-family resident — unchanged
    return out

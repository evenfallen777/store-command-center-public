"""Pure scheduling logic (app/gpu_scheduler.py) — priority, model/type affinity,
aging, and the within-tier starvation cap (media jobs vs LLM batches)."""
from gpu_scheduler import Job, pick_next, order, effective_priority, borrows_resident


def J(id, priority=1, model=None, enq=0.0, kind="llm", status="pending"):
    return Job(id=id, kind=kind, model=model, priority=priority, enqueued_at=enq, status=status)


def test_empty_and_no_pending():
    assert pick_next([], None, now=100) is None
    assert pick_next([J(1, status="running")], None, now=100) is None


def test_highest_priority_wins():
    jobs = [J(1, priority=2), J(2, priority=0), J(3, priority=1)]
    assert pick_next(jobs, resident_model=None, now=0).id == 2


def test_affinity_batches_resident_model_within_tier():
    # same priority + same enqueue time; the one matching the resident model is chosen
    jobs = [J(1, priority=1, model="A", enq=0), J(2, priority=1, model="B", enq=0)]
    assert pick_next(jobs, resident_model="B", now=0).id == 2
    assert pick_next(jobs, resident_model="A", now=0).id == 1


def test_affinity_prefers_borrow_any_and_none():
    jobs = [J(1, model="A", enq=0), J(2, model="*", enq=1)]
    # both are "no swap" vs resident C? model A needs swap, '*' borrows — '*' preferred
    assert pick_next(jobs, resident_model="C", now=0).id == 2


def test_affinity_never_overrides_priority():
    # a fresh high-priority job for a NON-resident model still beats resident-model lower prio
    jobs = [J(1, priority=1, model="A", enq=0),   # resident, but lower priority
            J(2, priority=0, model="B", enq=0)]   # needs swap, but urgent
    assert pick_next(jobs, resident_model="A", now=0).id == 2


def test_aging_promotes_a_starved_job():
    # job 1 is low priority (2) but has waited 3 age-steps -> effective 0
    # job 2 is fresh priority 1 -> effective 1. The aged job should win.
    now = 300.0
    aged = J(1, priority=2, model="A", enq=now - 180)   # waited 180s, step 60 -> -3 -> eff 0
    fresh = J(2, priority=1, model="B", enq=now)         # eff 1
    assert effective_priority(aged, now) == 0
    assert effective_priority(fresh, now) == 1
    assert pick_next([fresh, aged], resident_model="B", now=now).id == 1


def test_order_batches_same_model_then_switches():
    # priority all equal; affinity should group model A together before switching to B
    jobs = [J(1, model="A", enq=0), J(2, model="B", enq=1),
            J(3, model="A", enq=2), J(4, model="B", enq=3)]
    ids = [j.id for j in order(jobs, resident_model="A", now=0)]
    # starts resident A -> both A jobs first (1,3), then B jobs (2,4)
    assert ids[:2] == [1, 3]
    assert set(ids[2:]) == {2, 4}


def test_order_is_stable_and_complete():
    jobs = [J(1, model="A", enq=0), J(2, model="A", enq=1), J(3, model="B", enq=2)]
    ids = sorted(j.id for j in order(jobs, resident_model=None, now=0))
    assert ids == [1, 2, 3]   # every pending job scheduled exactly once


# ── Unified (family, model) residency: cross-type eviction awareness ──────────

def test_cross_type_job_never_borrows_llm_resident():
    # an image/video job while an LLM is resident forces an eviction → NOT a borrow,
    # even with model=None (which for a same-family job would mean "use what's loaded")
    assert not borrows_resident(J(1, kind="image"), "A")
    assert not borrows_resident(J(1, kind="image", model="ckpt"), ("llm", "A"))
    assert not borrows_resident(J(1, kind="video"), ("llm", "A"))
    assert not borrows_resident(J(1, kind="llm", model="A"), ("image", None))


def test_same_family_borrows_resident():
    assert borrows_resident(J(1, kind="image"), ("image", None))          # type-level batch
    assert borrows_resident(J(1, kind="image", model="ck"), ("image", "ck"))
    assert borrows_resident(J(1, kind="video", model="wan"), ("video", "wan"))
    assert not borrows_resident(J(1, kind="video", model="wan"), ("video", "hunyuan"))
    # legacy bare-string resident still means ('llm', model)
    assert borrows_resident(J(1, kind="llm", model="A"), "A")
    assert borrows_resident(J(1, kind="vision", model="A"), "A")   # vision runs on the LLM


def test_resident_llm_batches_before_image_ticket():
    # equal tier: the resident model's LLM jobs run before the image job evicts it
    jobs = [J(1, kind="llm", model="A", enq=0),
            J(2, kind="image", enq=1),
            J(3, kind="llm", model="A", enq=2)]
    assert pick_next(jobs, resident_model=("llm", "A"), now=3).id == 1


def test_mixed_queue_drains_resident_model_first():
    # Owner scenario: llm-image-llm-llm-image-video-image-video-llm-llm, all the LLM
    # jobs on the resident model → all 5 LLMs run on ONE load, then images batch,
    # then videos. Zero LLM reloads instead of 4.
    lineup = ["llm", "image", "llm", "llm", "image", "video", "image", "video", "llm", "llm"]
    jobs = [J(i, kind=k, model=("A" if k == "llm" else None), enq=float(i))
            for i, k in enumerate(lineup)]
    got = order(jobs, resident_model=("llm", "A"), now=10.0)
    kinds = [j.kind for j in got]
    assert kinds == ["llm"] * 5 + ["image"] * 3 + ["video"] * 2
    assert [j.id for j in got[:5]] == [0, 2, 3, 8, 9]        # oldest-first within the batch
    assert [j.id for j in got[5:8]] == [1, 4, 6]             # images batch together
    assert [j.id for j in got[8:]] == [5, 7]                 # then videos


def test_batch_enqueued_together_is_not_broken_by_aging():
    # 10 same-model jobs + 1 other-model job all enqueued together: even long after
    # every job has fully aged, affinity still drains the batch first (the outsider is
    # not OLDER than the batch, so the starvation cap does not trigger).
    jobs = [J(i, model="A", enq=0.0) for i in range(10)] + [J(99, model="B", enq=0.0)]
    got = order(jobs, resident_model="A", now=600.0)
    assert [j.id for j in got] == list(range(10)) + [99]


def test_starvation_cap_rescues_old_job_from_fresh_affine_stream():
    # a continuous stream of FRESH resident-model jobs cannot starve a waiting job:
    # once it is starve_cap_sec older than the oldest affine job, it wins the tier.
    now = 1000.0
    fresh = [J(i, model="A", enq=now - 5) for i in range(3)]
    waiting = J(50, model="B", enq=now - 200)   # 195s older > default cap 120s
    assert pick_next(fresh + [waiting], resident_model="A", now=now).id == 50
    # …but inside the cap window (and the same tier), affinity still batches
    waiting_young = J(51, model="B", enq=now - 50)   # < one age step → same tier
    assert pick_next(fresh + [waiting_young], resident_model="A", now=now).id == 0


def test_video_job_not_starved_by_llm_batch():
    # A video ticket behind an endless same-model LLM batch is rescued two ways:
    # 1) tier aging — fresh priority-1 LLMs vs an aged video ticket
    now = 500.0
    llms = [J(i, kind="llm", model="A", priority=1, enq=now - 1) for i in range(4)]
    video = J(9, kind="video", model="wan", priority=1, enq=now - 70)   # aged → tier 0
    assert pick_next(llms + [video], resident_model=("llm", "A"), now=now).id == 9
    # 2) starvation cap — even fresh priority-0 LLM floods can't hold it past the cap
    llms0 = [J(i, kind="llm", model="A", priority=0, enq=now - 1) for i in range(4)]
    video0 = J(9, kind="video", model="wan", priority=0, enq=now - 130)  # >120s older
    assert pick_next(llms0 + [video0], resident_model=("llm", "A"), now=now).id == 9


def test_priority_zero_still_beats_background_media():
    # a fresh user-facing LLM job outranks an older image ticket of default priority
    jobs = [J(1, kind="image", priority=1, enq=0),
            J(2, kind="llm", model="B", priority=0, enq=10)]
    assert pick_next(jobs, resident_model=("llm", "A"), now=10).id == 2


def test_order_updates_family_residency():
    # after an image job runs, image-family jobs batch; the later LLM job comes last
    jobs = [J(1, kind="image", enq=0), J(2, kind="llm", model="A", enq=1),
            J(3, kind="image", enq=2)]
    got = order(jobs, resident_model=None, now=3.0)
    assert [j.id for j in got] == [1, 3, 2]

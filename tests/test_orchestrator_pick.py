"""The orchestrator's scheduler integration — _pick_pending() maps queued LLM tasks to
gpu_scheduler and returns the next one to run. Built with __new__ so no worker thread /
LM Studio calls are involved — pure ordering glue."""
import threading
import orchestrator


def _bare():
    o = orchestrator.Orchestrator.__new__(orchestrator.Orchestrator)
    o._lock = threading.RLock()
    o._current_llm_model = None
    o._tasks = {}
    o._order = []
    return o


def _add(o, tid, priority=1, model=None, enq=0.0, status="pending"):
    o._tasks[tid] = {"id": tid, "status": status, "priority": priority,
                     "model": model, "enqueued_at": enq}
    o._order.append(tid)


def test_pick_prefers_higher_priority_over_submit_order():
    o = _bare()
    _add(o, 1, priority=2, enq=0)   # background, submitted first
    _add(o, 2, priority=0, enq=1)   # user-facing, submitted later
    _add(o, 3, priority=1, enq=2)
    with o._lock:
        assert o._pick_pending()["id"] == 2   # priority 0 wins despite later enqueue


def test_pick_is_fifo_within_equal_priority():
    o = _bare()
    _add(o, 5, priority=1, enq=10)
    _add(o, 6, priority=1, enq=11)
    with o._lock:
        assert o._pick_pending()["id"] == 5   # oldest of the tier


def test_pick_none_when_no_pending():
    o = _bare()
    _add(o, 7, priority=0, enq=0, status="running")
    with o._lock:
        assert o._pick_pending() is None


def test_pick_affinity_batches_resident_model_within_tier():
    o = _bare()
    o._current_llm_model = "B"
    _add(o, 1, priority=1, model="A", enq=0)
    _add(o, 2, priority=1, model="B", enq=1)   # matches resident → run to avoid a swap
    with o._lock:
        assert o._pick_pending()["id"] == 2


# ── Unified queue: media tickets ride the same scheduler ─────────────────────

def _add_ticket(o, tid, kind="image", priority=1, model=None, enq=0.0, status="pending"):
    o._tasks[tid] = {"id": tid, "type": kind, "media_ticket": True, "status": status,
                     "priority": priority, "model": model, "enqueued_at": enq,
                     "desc": f"{kind} job", "event": threading.Event()}
    o._order.append(tid)


def _add_llm(o, tid, priority=1, model=None, enq=0.0, status="pending"):
    o._tasks[tid] = {"id": tid, "type": "llm", "status": status, "priority": priority,
                     "model": model, "enqueued_at": enq}
    o._order.append(tid)


def test_pick_drains_resident_llm_before_image_ticket():
    import time
    o = _bare()
    now = time.time()
    o._resident = ("llm", "A")
    _add_llm(o, 1, model="A", enq=now - 3)
    _add_ticket(o, 2, kind="image", enq=now - 2)
    _add_llm(o, 3, model="A", enq=now - 1)
    with o._lock:
        assert o._pick_pending()["id"] == 1   # resident-model LLM first, ticket waits


def test_pick_returns_media_ticket_when_no_affine_work():
    import time
    o = _bare()
    now = time.time()
    o._resident = ("llm", "A")
    _add_ticket(o, 1, kind="video", model="wan", enq=now - 2)
    _add_llm(o, 2, model="B", enq=now - 1)        # needs a swap anyway
    with o._lock:
        picked = o._pick_pending()
    assert picked["id"] == 1                      # oldest wins — nothing borrows


def test_pick_media_ticket_rescued_from_llm_stream_by_starve_cap():
    import time
    o = _bare()
    now = time.time()
    o._resident = ("llm", "A")
    for i, tid in enumerate((1, 2, 3)):
        _add_llm(o, tid, model="A", enq=now - 1 - 0.001 * i)   # fresh resident-model work
    _add_ticket(o, 9, kind="video", model="wan", enq=now - 200)  # > cap older
    with o._lock:
        assert o._pick_pending()["id"] == 9


def test_pick_orphaned_ticket_is_retired_not_returned():
    import time, orchestrator as om
    o = _bare()
    now = time.time()
    o._resident = ("llm", "A")
    _add_ticket(o, 1, kind="image", enq=now - (om._QUEUE_WAIT + 400))   # long-dead waiter
    _add_llm(o, 2, model="A", enq=now - 1)
    with o._lock:
        picked = o._pick_pending()
    assert picked["id"] == 2
    assert o._tasks[1]["status"] == "cancelled"   # orphan retired, can't wedge the head


def test_video_hold_thread_tracking():
    o = _bare()
    o._video_hold = 0
    o._video_hold_ts = 0.0
    o._video_hold_threads = {}
    o._work_event = threading.Event()
    assert not o._thread_holds_video()
    o.video_hold_begin()
    assert o._thread_holds_video()        # this thread owns it → nested gate is skipped
    o.video_hold_begin()                  # nested (chain segment)
    o.video_hold_end()
    assert o._thread_holds_video()        # still nested once
    o.video_hold_end()
    assert not o._thread_holds_video()
    assert o._video_hold == 0

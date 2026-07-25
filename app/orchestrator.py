"""
GPU Orchestrator — shared GPU between LM Studio (LLM) and ComfyUI (image/video).
Host / URLs / model come from config.py (STORE_GPU_HOST, STORE_COMFYUI_URL, …).

Unload tools:
  LLM   → SSH to box: lms unload <model>
  Image → POST <comfyui>/free

Flow:
  LLM task  → wait for active images → free ComfyUI VRAM → run → unload LLM immediately after
  Image task → wait for active LLM  → unload LLM → run → release when done

Unified queue (anti reload-thrash): every image/video job first registers a TICKET in
the same queue the LLM worker drains (_media_gate) and waits until gpu_scheduler picks
it. Affinity keeps picking queued tasks that borrow whatever is ACTUALLY resident
(tracked as self._resident = (family, model) for llm/image/video alike), so e.g. all
queued same-model LLM work runs on one load before an image job evicts the model —
instead of reloading it between every interleaved job. Aging + a starvation cap in
gpu_scheduler bound every wait; _QUEUE_WAIT is the hard force-proceed backstop.
"""
import subprocess, threading, time, httpx, logging
from contextlib import contextmanager
from typing import Callable, Optional

log = logging.getLogger("orch")

try:
    from config import GPU_SSH_USER, GPU_HOST, COMFYUI_URL, ENHANCE_MODEL_DEFAULT, GPU_EXCLUSIVE
    BOX = f"{GPU_SSH_USER}@{GPU_HOST}"
    COMFYUI = COMFYUI_URL
    DEFAULT_MODEL = ENHANCE_MODEL_DEFAULT
except Exception:
    BOX = "user@127.0.0.1"
    COMFYUI = "http://127.0.0.1:8188"
    DEFAULT_MODEL = "google/gemma-4-12b-qat"
    GPU_EXCLUSIVE = True
LMS = "~/.lmstudio/bin/lms"

# Max seconds a queued media job waits for the GPU to free before proceeding anyway.
# Must exceed the longest single job (a 4-min ACE-Step song or a multi-segment video
# chain can run ~10 min) so concurrent video/3D/audio jobs genuinely queue instead of
# timing out and colliding on the single GPU. Override via STORE_GPU_QUEUE_TIMEOUT.
import os as _os
try:
    _QUEUE_WAIT = int(_os.getenv("STORE_GPU_QUEUE_TIMEOUT", "1800"))
except Exception:
    _QUEUE_WAIT = 1800

# ── Video VRAM verification ───────────────────────────────────────────────────
# After freeing ComfyUI + LM Studio for a video job, poll the node's nvidia-smi
# until at least _VIDEO_MIN_FREE_MB MiB are free before launching, so a squatting
# model fails the job with a CLEAR message (listing the offender) instead of an
# OOM at the end of a 10-minute denoise. 0 disables the check.
try:
    _VIDEO_MIN_FREE_MB = int(_os.getenv("STORE_VIDEO_MIN_FREE_MB", "8000"))
except Exception:
    _VIDEO_MIN_FREE_MB = 8000
try:
    _VRAM_FREE_WAIT = int(_os.getenv("STORE_VIDEO_VRAM_WAIT", "120"))   # seconds
except Exception:
    _VRAM_FREE_WAIT = 120

# A video-exclusive hold with no begin/end activity for this long is treated as
# stale (its thread died without releasing — normally impossible: every hold is
# finally-guarded) and stops blocking the LLM worker. Must exceed the longest
# single segment (HunyuanVideo timeout is 5400 s); the timestamp refreshes at
# every segment boundary, so per-chain length doesn't matter.
try:
    _HOLD_STALE = int(_os.getenv("STORE_GPU_HOLD_STALE", "7200"))
except Exception:
    _HOLD_STALE = 7200

# SSH transport + LM Studio model-load/VRAM helpers live in orchestrator_node
# (split out verbatim). Re-imported here so orchestrator's surface is unchanged;
# the Orchestrator class below uses them exactly as before.
from orchestrator_node import (
    _ssh, _loaded_llms, _active_model, _idle_ttl, _model_cfg_of, _load_args,
)


class Orchestrator:
    """Mutual-exclusion GPU scheduler for LLM and image generation tasks."""

    def __init__(self, llm_model: str = DEFAULT_MODEL):
        self.llm_model = llm_model
        self._current_llm_model = llm_model   # model _call_lmstudio should target right now
        # What is ACTUALLY resident in VRAM right now, as a (family, model) pair —
        # family 'llm' | 'image' | 'video', model may be None when unknown — or None
        # when nothing is resident. This is the unified-resident notion the scheduler
        # uses for affinity: it lets image/video work batch by type (and checkpoint)
        # exactly like same-model LLM work, instead of only ever knowing the LLM.
        self._resident = ("llm", llm_model)
        self._restore_model = None            # model we unloaded for image/video, to reload after
        self._lock = threading.RLock()
        self._img_prepare_lock = threading.Lock()  # serialise image_acquire calls
        self._last_llm_activity = time.time()  # for idle-TTL sweep (updated on any LLM use)

        # ── Video-exclusive GPU hold ─────────────────────────────────────────
        # > 0 ⇒ a video/3D/audio job (or a whole multi-segment chain) owns the
        # GPU: the LLM worker must not START any task — and therefore can never
        # trigger an LM Studio model load or JIT reload — until the hold drops
        # to 0. A counter (not a bool) so each segment's video_acquire() nests
        # inside a chain-long video_exclusive(). _video_hold_ts guards against
        # a stale hold ever wedging the LLM queue (see _video_hold_active).
        self._video_hold = 0
        self._video_hold_ts = 0.0
        # thread ident → nested hold count. Lets _media_gate() detect "this thread
        # ALREADY owns the GPU via video_exclusive()" (a chain segment's nested
        # video_acquire) and skip the queue gate — gating there would deadlock:
        # the gate would wait for pending LLM tasks that the hold itself blocks.
        self._video_hold_threads: dict[int, int] = {}

        # ── GPU states ────────────────────────────────────────────────────────
        self._llm_state = "idle"   # idle | loading | busy | unloading
        self._img_state = "idle"   # idle | loading | busy | unloading
        self._active_images = 0    # number of ComfyUI jobs in flight

        # ── LLM task queue ────────────────────────────────────────────────────
        self._tasks: dict[int, dict] = {}
        self._order: list[int] = []   # insertion order
        self._counter = 0

        # ── Pause gate ────────────────────────────────────────────────────────
        # SET = running, CLEARED = paused. Blocks the LLM worker and every media
        # *_acquire() so NO new GPU job starts while paused; in-flight jobs finish.
        self._pause_gate = threading.Event()
        self._pause_gate.set()

        # ── Worker thread ─────────────────────────────────────────────────────
        self._work_event = threading.Event()
        threading.Thread(
            target=self._worker_loop, daemon=True, name="orch-worker"
        ).start()

    # ─── Public status ────────────────────────────────────────────────────────

    def status(self) -> dict:
        with self._lock:
            llm   = self._llm_state
            img   = self._img_state
            active = self._active_images
            queue = [
                {"id": tid, "type": d["type"], "desc": d["desc"], "status": d["status"],
                 # model this job runs on: its explicit pick, else the currently-resident LLM
                 # it will borrow (so the unified queue shows what's actually loading/running)
                 "model": d.get("model") or (self._current_llm_model if d["type"] == "llm" else None)}
                for tid in self._order
                if (d := self._tasks.get(tid)) and d["status"] in ("pending", "running")
            ]
        return {
            "llm":          llm,
            "image":        img,
            "active_images": active,
            "queue":        queue,
            "message":      self._build_message(llm, img, active, queue),
        }

    # ─── Pause / resume / clear ───────────────────────────────────────────────

    def pause(self):
        """Stop dispatching NEW GPU jobs (LLM + image/video). In-flight jobs finish."""
        self._pause_gate.clear()

    def resume(self):
        """Resume dispatching queued jobs."""
        self._pause_gate.set()
        self._work_event.set()

    def is_paused(self) -> bool:
        return not self._pause_gate.is_set()

    def clear_pending(self) -> int:
        """Cancel every PENDING (not-yet-running) LLM task. Returns how many were cleared."""
        cleared = []
        with self._lock:
            for tid in self._order:
                t = self._tasks.get(tid)
                if t and t["status"] == "pending":
                    t["status"] = "cancelled"
                    t["event"].set()
                    cleared.append(t)
        for t in cleared:                      # DB write outside the lock
            self._record_history(t, "cancelled")
        return len(cleared)

    def _build_message(self, llm, img, active, queue) -> str:
        if self.is_paused():
            return "⏸ Queue paused"
        if llm == "loading":    return "⏳ Loading LLM model…"
        if llm == "busy":       return "🤖 LLM running…"
        if llm == "unloading":  return "🔄 Unloading LLM…"
        if img == "loading":    return "⏳ Loading image model…"
        if img == "busy":       return f"🎨 Generating {active} image(s)…"
        if img == "unloading":  return "🔄 Freeing image VRAM…"
        if self._video_hold_active():
            return "🎬 Video job holds the GPU (chain in progress)"
        pending = sum(1 for t in queue if t["status"] == "pending")
        if pending and active:
            return f"⏸ LLM queued — waiting for {active} image job(s) to finish"
        if queue:
            return f"⏳ {len(queue)} task(s) in queue"
        return ""

    # ─── LLM task API ─────────────────────────────────────────────────────────

    def submit_llm(
        self,
        func: Callable,
        desc: str,
        retry_meta: Optional[dict] = None,
        model: Optional[str] = None,
        priority: int = 1,
        task: Optional[str] = None,
        source: Optional[str] = None,
    ) -> int:
        """Submit an LLM task. Returns task_id immediately (non-blocking).

        `source` — optional explicit system/feature tag for the persistent queue
        history; when omitted it is derived from `task`/`desc` prefixes at
        completion time (queue_history.derive_source).

        `model` — if given, the worker guarantees THAT specific model is the sole
        resident LLM (verified via `lms ps`) before running `func`; the orchestrator
        is the single authority for model loading, so nothing races `lms` in parallel.
        If omitted, the worker borrows whatever model is already loaded (legacy path)."""

        if model is None and task:
            try:                       # per-task override (Settings → Prompts picker)
                import model_registry
                model = model_registry.for_task(task) or None
            except Exception:
                model = None
            # NSFW mode → route Studio media prompt-gen (image/video/music/3D) to the
            # uncensored nsfw_model (blank = auto-detected uncensored build, else the
            # global LLM). Explicit per-task overrides above still win; unrelated LLM
            # features (listings/pricing/security/assistant/research) are excluded.
            if model is None and task in ("image_enhance", "audio_music", "audio_voice",
                                          "audio_lyrics", "video_chain", "threed_enhance",
                                          "studio_storyboard", "studio_scene_regen"):
                try:
                    import nsfw, model_registry
                    if nsfw.enabled():
                        model = model_registry.resolve("nsfw_model") or None
                except Exception:
                    pass
        with self._lock:
            tid = self._counter
            self._counter += 1
            self._tasks[tid] = {
                "id":         tid,
                "type":       "llm",
                "func":       func,
                "desc":       desc,
                "status":     "pending",
                "result":     None,
                "error":      None,
                "retry_meta": retry_meta,
                "model":      model,
                "task":       task,              # prompt-registry key (history attribution)
                "source":     source,            # explicit system tag (history attribution)
                "priority":   priority,          # 0 user-facing > 1 default > 2 background
                "enqueued_at": time.time(),
                "event":      threading.Event(),
            }
            self._order.append(tid)
            self._prune()
        self._work_event.set()
        return tid

    def mark_activity(self):
        """Record that the LLM was just used (queued task OR proxy fast-path), so the
        idle-TTL sweep never unloads a model that is actively serving requests."""
        self._last_llm_activity = time.time()

    def poll(self, task_id: int) -> dict:
        with self._lock:
            t = self._tasks.get(task_id)
        if not t:
            return {"id": task_id, "status": "not_found"}
        return {
            "id":         task_id,
            "status":     t["status"],
            "result":     t["result"],
            "error":      t["error"],
            "retry_meta": t.get("retry_meta"),
        }

    def cancel(self, task_id: int) -> bool:
        hit = None
        with self._lock:
            t = self._tasks.get(task_id)
            if t and t["status"] == "pending":
                t["status"] = "cancelled"
                t["event"].set()
                hit = t
        if hit:
            self._record_history(hit, "cancelled")   # DB write outside the lock
            return True
        return False

    def _prune(self):
        """Keep only the last 50 completed tasks (called under lock)."""
        done_ids = [
            tid for tid in self._order
            if self._tasks.get(tid, {}).get("status") not in ("pending", "running")
        ]
        for old in done_ids[:-50]:
            self._tasks.pop(old, None)
            self._order.remove(old)

    # ─── Low-value background LLM chatter (borrow-only) ───────────────────────

    def llm_borrow(self, work_fn):
        """Run a low-value background LLM call (world-sim agent thoughts / security
        reviews) ONLY by borrowing an already-resident LLM while the GPU is otherwise
        idle. Returns work_fn()'s result, or None if it can't borrow — the caller then
        falls back to canned text.

        Why this exists: world_gov / world_security used to call _call_lmstudio()
        directly, which made LM Studio JIT-load a model *outside* the unified queue.
        That model then lingered on LM Studio's keep-alive TTL and starved image/video
        generation of VRAM (the "someone's not following the queue" bug). Gating these
        calls here means world chatter never loads or evicts anything — it only
        piggybacks on a model the queue already has resident, and steps aside the moment
        real GPU work is running or owns the VRAM."""
        with self._lock:
            gpu_busy = (self._img_state != "idle"
                        or self._active_images > 0
                        or bool(self._video_hold_threads)
                        or self._llm_state in ("loading", "unloading"))
            can_borrow = bool(self._resident) and self._resident[0] == "llm"
        if gpu_busy or not can_borrow:
            return None
        try:
            return work_fn()
        except Exception:
            return None

    # ─── Image task hooks ─────────────────────────────────────────────────────

    def image_acquire(self, model: Optional[str] = None, priority: int = 1,
                      desc: str = "Image generation"):
        """
        Call BEFORE submitting work to ComfyUI.
        Blocks until any active LLM task finishes, then unloads LLM from VRAM.
        Serialised so multiple concurrent generations don't race.

        `model`/`priority`/`desc` (all optional, backward compatible) describe this
        job to the unified queue: the job first waits its TURN via _media_gate — so
        a resident LLM drains its queued same-model work before being evicted for
        this image job, instead of reload-thrashing — and only then evicts.
        """
        self._pause_gate.wait()   # hold new image jobs while the queue is paused
        if GPU_EXCLUSIVE:
            # Only on an exclusive GPU does an image job EVICT the resident LLM —
            # that's the only case where queue order matters for reload-thrash.
            # On a big GPU (both fit) images proceed immediately, as before.
            self._media_gate("image", model=model, priority=priority, desc=desc)
        with self._img_prepare_lock:
            # Wait for LLM to go idle
            waited = 0
            while True:
                with self._lock:
                    state = self._llm_state
                if state == "idle":
                    break
                if waited == 0:
                    log.info("[orch] image_acquire: waiting for LLM (%s)…", state)
                time.sleep(1)
                waited += 1
                if waited > 120:
                    log.warning("[orch] image_acquire: LLM wait timed out")
                    break

            # Wait for any currently-running image jobs to finish first.
            # image_acquire releases _img_prepare_lock before ComfyUI finishes,
            # so without this check two variations would run concurrently and
            # produce near-identical output.
            waited_img = 0
            while True:
                with self._lock:
                    active = self._active_images
                if active == 0:
                    break
                if waited_img == 0:
                    log.info("[orch] image_acquire: waiting for %d active image job(s) to finish…", active)
                time.sleep(1)
                waited_img += 1
                if waited_img > _QUEUE_WAIT:
                    log.warning("[orch] image_acquire: active-image wait timed out")
                    break

            # Unload LLM to free VRAM for the image model (exclusive GPU only)
            self._free_llm_for_media()

            with self._lock:
                self._active_images += 1
                self._img_state = "busy"
                if GPU_EXCLUSIVE:
                    self._resident = ("image", model)   # image family now owns the VRAM
                # non-exclusive GPU: the LLM was NOT evicted — residency unchanged

    def image_release(self):
        """Call when a ComfyUI job finishes (success or failure)."""
        with self._lock:
            self._active_images = max(0, self._active_images - 1)
            done = self._active_images == 0
            if done:
                self._img_state = "idle"
        # Wake worker — a queued LLM task may now proceed
        self._work_event.set()
        if done:
            self._restore_if_needed()

    def _free_llm_for_media(self, force: bool = False):
        """Unload the LLM to free VRAM for image/video gen. Skipped when the GPU has
        room for both (STORE_GPU_EXCLUSIVE=0) unless forced (video is very heavy).
        Remembers what was loaded so it can be restored afterwards."""
        if not force and not GPU_EXCLUSIVE:
            return
        try:
            loaded = _loaded_llms()
            self._restore_model = loaded[0] if loaded else None
        except Exception:
            self._restore_model = None
        self._set(llm="unloading")
        self._unload_llm()
        self._set(llm="idle")
        time.sleep(3)   # let VRAM settle

    def _restore_if_needed(self):
        """After media gen, reload the model we evicted — but only if no LLM task is
        queued (a queued task would load its own model / borrow anyway)."""
        with self._lock:
            model = self._restore_model
            self._restore_model = None
            pending = any(self._tasks.get(t, {}).get("status") == "pending" for t in self._order)
        if not model or pending:
            return

        def _do_restore():
            # Hold the SAME prepare-lock the media *_acquire() paths use, so a restore
            # and a media job can never both be arranging the GPU at once. Re-check under
            # it: a new media job (image/video/3D/audio all bump _active_images) may have
            # grabbed the GPU while this thread was starting — reloading now would OOM it.
            with self._img_prepare_lock:
                with self._lock:
                    busy = self._active_images > 0 or self._img_state != "idle"
                if busy or self._video_hold_active():
                    log.info("[orch] skip LLM restore — a media job/video chain is using the GPU")
                    return
                log.info("[orch] restoring previously-loaded model: %s", model)
                rc, _ = _ssh(LMS, *_load_args(model), timeout=180)
                if rc == 0:
                    with self._lock:
                        self._resident = ("llm", model)   # keep unified residency accurate
        threading.Thread(target=_do_restore, daemon=True, name="orch-restore").start()

    # ─── Video-exclusive GPU hold ─────────────────────────────────────────────

    def video_hold_begin(self):
        """Mark the GPU as owned by video work. While the hold count is > 0 the
        LLM worker will not start any task (so no `lms load` and no LM Studio
        JIT reload can land mid-generation). Callers MUST pair with
        video_hold_end() in a finally — or use video_exclusive()."""
        ident = threading.get_ident()
        with self._lock:
            self._video_hold += 1
            self._video_hold_ts = time.time()
            self._video_hold_threads[ident] = self._video_hold_threads.get(ident, 0) + 1

    def video_hold_end(self):
        ident = threading.get_ident()
        with self._lock:
            self._video_hold = max(0, self._video_hold - 1)
            self._video_hold_ts = time.time()
            n = self._video_hold_threads.get(ident, 0) - 1
            if n > 0:
                self._video_hold_threads[ident] = n
            else:
                self._video_hold_threads.pop(ident, None)
        self._work_event.set()   # wake the worker — queued LLM tasks may proceed

    def _thread_holds_video(self) -> bool:
        """True when the CALLING thread already owns a video-exclusive hold (it is
        inside video_exclusive(), e.g. a chain between segments)."""
        with self._lock:
            return self._video_hold_threads.get(threading.get_ident(), 0) > 0

    def _video_hold_active(self) -> bool:
        """True while video work owns the GPU. A hold whose thread died without
        releasing (should be impossible — every hold is finally-guarded) goes
        stale after _HOLD_STALE seconds of no begin/end activity and stops
        blocking, so the LLM queue can never be wedged forever."""
        with self._lock:
            n, ts = self._video_hold, self._video_hold_ts
        if n <= 0:
            return False
        if time.time() - ts > _HOLD_STALE:
            log.warning("[orch] video-exclusive hold looks stale (%d holder(s), "
                        "untouched %ds > %ds) — ignoring it", n, int(time.time() - ts), _HOLD_STALE)
            return False
        return True

    @contextmanager
    def video_exclusive(self, model: Optional[str] = None, priority: int = 1,
                        desc: str = "Video chain"):
        """Hold GPU exclusivity across a WHOLE multi-segment video chain, not just
        one segment. Between segments _active_images drops to 0, which used to let
        the LLM worker load an 8 GB model that then OOMed the next segment; this
        span-hold closes that gap. Nested video_acquire/release still work (the
        hold is a counter). `model`/`priority`/`desc` describe the chain to the
        unified queue: the gate runs BEFORE the hold is taken (gating after would
        deadlock — the hold blocks the very LLM tasks the gate waits on), so a
        resident LLM drains its queued same-model work before the chain evicts it."""
        self._media_gate("video", model=model, priority=priority, desc=desc)
        self.video_hold_begin()
        try:
            yield
        finally:
            self.video_hold_end()

    def video_acquire(self, model: Optional[str] = None, priority: int = 1,
                      desc: str = "Video generation"):
        """
        Call BEFORE submitting work to Wan2.1 / any video gen pipeline.
        Like image_acquire(), but ALSO frees ComfyUI VRAM first, and then
        VERIFIES via nvidia-smi that the VRAM is actually free.

        Why needed: Wan2.1's T5-XXL text encoder needs ~9.5 GB VRAM even with
        CPU offloading.  If ComfyUI has SDXL cached (~6.7 GB) + T5 = 16+ GB > 12 GB.
        We must free ComfyUI *and* LM Studio before any video generation starts.

        Raises RuntimeError when the node can't free enough VRAM (message lists
        the offending process) — fail the job clearly instead of launching into
        a guaranteed OOM. On raise, no state is leaked: do NOT call
        video_release() for a failed acquire.

        `model`/`priority`/`desc` (optional, backward compatible) describe this job
        to the unified queue. The _media_gate turn-wait runs BEFORE video_hold_begin:
        while waiting, the LLM worker must stay free to drain the resident model's
        queued work (gating inside the hold would deadlock). A nested acquire from a
        thread already inside video_exclusive() skips the gate — it owns the GPU.
        """
        self._pause_gate.wait()   # hold new video jobs while the queue is paused
        self._media_gate("video", model=model, priority=priority, desc=desc)
        self.video_hold_begin()   # from here on, the LLM worker starts nothing
        try:
            self._video_acquire_locked(model)
        except BaseException:
            self.video_hold_end()   # failed acquire must not leak the hold
            raise

    def _video_acquire_locked(self, model: Optional[str] = None):
        with self._img_prepare_lock:
            # Wait for LLM to go idle
            waited = 0
            while True:
                with self._lock:
                    state = self._llm_state
                if state == "idle":
                    break
                if waited == 0:
                    log.info("[orch] video_acquire: waiting for LLM (%s)\u2026", state)
                time.sleep(1)
                waited += 1
                if waited > 120:
                    log.warning("[orch] video_acquire: LLM wait timed out")
                    break

            # Wait for active image/video jobs to finish
            waited_img = 0
            while True:
                with self._lock:
                    active = self._active_images
                if active == 0:
                    break
                if waited_img == 0:
                    log.info("[orch] video_acquire: waiting for %d active job(s)\u2026", active)
                time.sleep(1)
                waited_img += 1
                if waited_img > _QUEUE_WAIT:
                    log.warning("[orch] video_acquire: active-job wait timed out")
                    break

            # Free ComfyUI VRAM (SDXL ~6.7 GB) + unload LM Studio before video gen.
            # Video is heavy (T5-XXL ~9.5 GB), so free the LLM even on a big GPU.
            log.info("[orch] video_acquire: freeing ComfyUI + LLM VRAM for video gen\u2026")
            self._set(img="unloading")
            self._free_comfyui()
            self._set(img="idle")
            self._free_llm_for_media(force=True)

            # VERIFY the VRAM is actually free before claiming the GPU. Unloads
            # are asynchronous on the node and LM Studio JIT-reloads a model for
            # any still-pending /chat/completions request \u2014 "we asked" is not
            # "it's free". Raises RuntimeError (offender listed) on failure.
            self._verify_vram_free()

            with self._lock:
                self._active_images += 1
                self._img_state = "busy"
                self._resident = ("video", model)   # video family now owns the VRAM

    def video_release(self):
        """Call when a video generation job finishes (success or failure)."""
        self.image_release()   # same release mechanism
        self.video_hold_end()  # pairs with video_acquire's video_hold_begin

    # ─── Unified-queue gate for media jobs ────────────────────────────────────

    def _media_gate(self, kind: str, model: Optional[str], priority: int, desc: str):
        """Register this image/video job as a TICKET in the unified queue and wait
        until the scheduler picks it. This is what stops reload-thrash: while a
        resident LLM still has queued same-model work, the scheduler keeps picking
        those LLM tasks (affinity), and this media job waits its turn instead of
        evicting the model between them. Anti-starvation (aging + starve-cap in
        gpu_scheduler) guarantees the wait is bounded even under a continuous
        stream of LLM work; _QUEUE_WAIT is the hard force-proceed backstop.

        The ticket is ONLY a queue position: cancelling it (queue clear) does not
        cancel the media job — the job just stops waiting and proceeds, exactly as
        media jobs behaved before the gate existed. Never called with the video
        hold owned by this thread (that would wait on LLM tasks the hold blocks)."""
        if self._thread_holds_video():
            return   # nested acquire inside video_exclusive() — GPU already ours
        with self._lock:
            tid = self._counter
            self._counter += 1
            self._tasks[tid] = {
                "id":          tid,
                "type":        kind,            # 'image' | 'video'
                "media_ticket": True,           # queue position only — worker never runs it
                "func":        None,
                "desc":        desc,
                "status":      "pending",
                "result":      None,
                "error":       None,
                "retry_meta":  None,
                "model":       model,           # checkpoint / model_id when known
                "task":        None,
                "source":      None,
                "priority":    int(priority),
                "enqueued_at": time.time(),
                "event":       threading.Event(),
            }
            self._order.append(tid)
            self._prune()
        try:
            self._await_turn(tid)
        finally:
            # retire the ticket no matter how the wait ended — it must never linger
            with self._lock:
                t = self._tasks.get(tid)
                if t and t["status"] in ("pending", "running"):
                    t["status"] = "done"
                    t["event"].set()
            self._work_event.set()   # let the worker re-evaluate the queue

    def _await_turn(self, tid: int):
        """Poll until the unified scheduler says this ticket is next (or it was
        cancelled, or the _QUEUE_WAIT backstop expires — then proceed anyway).
        Checks BEFORE sleeping, so an empty queue adds no latency. Each losing
        iteration wakes the worker so the actual winner (an LLM task) runs."""
        deadline = time.time() + _QUEUE_WAIT
        logged = False
        while True:
            if not self._pause_gate.is_set():
                t0 = time.time()
                self._pause_gate.wait()
                deadline += time.time() - t0   # pause time doesn't consume the budget
            with self._lock:
                t = self._tasks.get(tid)
                if not t or t["status"] != "pending":
                    return   # cancelled/cleared — proceed; queue controls never block media
                winner = self._pick_pending()
            if winner is not None and winner["id"] == tid:
                return       # our turn — caller proceeds to evict + claim the GPU
            if time.time() >= deadline:
                log.warning("[orch] media job '%s' waited %ds for its queue turn — proceeding anyway",
                            t.get("desc"), _QUEUE_WAIT)
                return
            if not logged:
                log.info("[orch] %s job '%s' queued — letting resident-model work drain first…",
                         t.get("type"), t.get("desc"))
                logged = True
            self._work_event.set()   # the winner is an LLM task — make sure the worker runs it
            time.sleep(1)

    # ─── Worker loop ──────────────────────────────────────────────────────────

    def _worker_loop(self):
        while True:
            self._work_event.wait()
            self._work_event.clear()
            self._drain()

    def _drain(self):
        """Process pending LLM tasks, one at a time, in scheduler order."""
        while True:
            self._pause_gate.wait()   # hold here while the queue is paused
            with self._lock:
                task = self._pick_pending()
            if not task:
                break
            if task.get("type") != "llm":
                # The unified scheduler says a MEDIA job is next. Its own thread is
                # waiting in _await_turn and will see the same verdict within a
                # second, evict the LLM and claim the GPU; image_release /
                # video_hold_end will wake this worker afterwards. (If the verdict
                # flips to an LLM task by aging first, that waiting thread pokes
                # _work_event every poll, so we re-run and pick it up.)
                break
            self._run_llm_task(task)

    def _pick_pending(self):
        """Choose the next pending job — LLM task OR media ticket — from the unified
        queue (called under self._lock). Uses the unified scheduler (priority →
        model/type-affinity batching → anti-starvation aging + starve cap); falls back
        to FIFO across the whole queue on ANY issue so a scheduler bug can never wedge
        the worker OR a gated media job. Callers: _drain runs the pick only when it is
        an LLM task; _await_turn compares the pick against its own ticket."""
        pending = [tid for tid in self._order
                   if (t := self._tasks.get(tid)) and t["status"] == "pending"]
        if not pending:
            return None
        try:
            import gpu_scheduler as _sched
            now = time.time()
            jobs = []
            for tid in pending:
                t = self._tasks[tid]
                kind = t.get("type") or "llm"
                enq = float(t.get("enqueued_at", 0.0))
                if t.get("media_ticket") and now - enq > _QUEUE_WAIT + 300:
                    # Orphaned ticket: its thread force-proceeds at _QUEUE_WAIT, so a
                    # pending ticket this old has no living waiter. Retire it so it
                    # can never sit at the head of the queue and wedge the pick.
                    log.warning("[orch] retiring orphaned %s ticket %d ('%s')",
                                kind, tid, t.get("desc"))
                    t["status"] = "cancelled"
                    t["event"].set()
                    continue
                jobs.append(_sched.Job(
                    id=tid, kind=kind, model=t.get("model"),
                    priority=int(t.get("priority", 1)), enqueued_at=enq))
            # unified resident (family, model); legacy fallback: the tracked LLM model
            resident = getattr(self, "_resident", None)
            if resident is None and self._current_llm_model:
                resident = ("llm", self._current_llm_model)
            nxt = _sched.pick_next(jobs, resident_model=resident, now=now)
            if nxt is not None:
                return self._tasks[nxt.id]
            if not jobs:
                return None   # every pending entry was an orphaned ticket
        except Exception as e:
            log.warning("[orch] scheduler pick failed — FIFO fallback: %s", e)
        return self._tasks[pending[0]]   # FIFO fallback (insertion order, all types)

    def _run_llm_task(self, task: dict):
        gate_held = False
        try:
            # ── Step 1: wait for media jobs AND any video-exclusive hold to end,
            # then CLAIM the GPU under the same prepare-lock every media
            # *_acquire() uses. The old gate only checked _active_images at one
            # instant: between two segments of a video chain it reads 0, so the
            # worker would start a task whose LM Studio call JIT-loaded an 8 GB
            # model straight into the running chain (the seg-1 OOM). Now the
            # chain's video_exclusive() hold spans the gap, and the claim is
            # re-checked under the lock so acquire/load can never interleave. ──
            waited = 0
            while True:
                with self._lock:
                    active = self._active_images
                if active == 0 and not self._video_hold_active():
                    self._img_prepare_lock.acquire()
                    with self._lock:
                        active = self._active_images
                    if active == 0 and not self._video_hold_active():
                        gate_held = True
                        break
                    # A media job started while we were claiming — back off.
                    self._img_prepare_lock.release()
                if waited == 0:
                    log.info("[orch] LLM task queued — waiting for media/video job(s) to release the GPU…")
                time.sleep(2)
                waited += 2

            # Bail if cancelled while waiting
            with self._lock:
                if task["status"] == "cancelled":
                    return

            # ── Step 2: free ComfyUI VRAM (only when the GPU can't hold both) ──
            if GPU_EXCLUSIVE:
                self._set(img="unloading")
                self._free_comfyui()
                self._set(img="idle")
            self._set(llm="loading")

            # Final cancelled check before running
            with self._lock:
                if task["status"] == "cancelled":
                    self._set(llm="idle")
                    return
                task["status"] = "running"
                task["started_at"] = time.time()
            self.mark_activity()

            # ── Step 3: get the model ready. If the task REQUIRES a specific model,
            # load+verify it (single authority, no race); else borrow the loaded one. ──
            required = task.get("model")
            if required:
                if not self._ensure_loaded(required):
                    raise RuntimeError(
                        f"LM Studio could not load required model '{required}' "
                        f"(loaded={_loaded_llms()})")
            else:
                borrowed = self._pick_llm_model()
                with self._lock:
                    self._current_llm_model = borrowed
                    self._resident = ("llm", borrowed)
            self._set(llm="busy")
            # Model is resident — release the claim before the (possibly long)
            # inference so media acquires can start their waits; they still wait
            # for llm busy→idle exactly as before.
            self._img_prepare_lock.release()
            gate_held = False
            result = task["func"]()

            with self._lock:
                task["result"] = result
                task["status"] = "done"
            self._record_history(task, "done")

        except Exception as e:
            log.error("[orch] task %d error: %s", task["id"], e)
            with self._lock:
                task["error"] = str(e)
                task["status"] = "error"
            self._record_history(task, "error")
        finally:
            if gate_held:
                self._img_prepare_lock.release()
            task["event"].set()
            self.mark_activity()
            # ── Step 4: keep the LLM loaded for reuse (no thrash). It is only
            # unloaded when image/video gen actually needs the VRAM. ──
            self._set(llm="idle")

    def _record_history(self, task: dict, status: str):
        """Persist a finished LLM task (done|error|cancelled) to the queue_history
        table — the SINGLE write path for the persistent queue history. A logging
        failure must NEVER break the job itself, so everything is swallowed here
        (and record() swallows its own errors too — belt and suspenders)."""
        if task.get("media_ticket"):
            return   # queue-position tickets aren't jobs — media persists its own lifecycle
        try:
            import queue_history
            queue_history.record(
                kind="llm",
                label=task.get("desc") or "LLM task",
                status=status,
                task=task.get("task"),
                source=task.get("source"),
                # the model it actually ran on: its explicit pick, else the
                # resident model it borrowed (cancelled tasks never ran → pick only)
                model=task.get("model") or (self._current_llm_model
                                            if status != "cancelled" else None),
                error=task.get("error"),
                enqueued_at=task.get("enqueued_at"),
                started_at=task.get("started_at"),
                finished_at=time.time(),
            )
        except Exception as e:                                # noqa: BLE001
            log.debug("[orch] history log skipped: %s", e)

    def sweep_idle_llms(self) -> dict:
        """Belt-and-suspenders idle-TTL enforcement (called from the scheduler tick).

        LM Studio already auto-unloads models the STORE loaded, because `_load_args`
        appends `lms load --ttl <model_idle_ttl>`. But a model loaded OUTSIDE the store's
        control — by the dev-swarm/OpenClaw, or a bare JIT load with no TTL — can sit
        resident forever holding VRAM (this is exactly the untracked coder model we
        observed). When the orchestrator has been fully idle longer than a model's
        effective idle TTL, unload GPU-resident, non-pinned models. Pinned models
        (OpenClaw's primary) and CPU-placed side models are never touched.
        `model_idle_ttl`=0 disables the sweep entirely (owner toggle)."""
        ttl = _idle_ttl()
        if ttl <= 0:
            return {"swept": [], "reason": "ttl-disabled"}
        with self._lock:
            busy = (self._active_images > 0
                    or self._img_state != "idle"
                    or self._llm_state != "idle"
                    or any(self._tasks.get(t, {}).get("status") in ("pending", "running")
                           for t in self._order))
            idle_for = time.time() - self._last_llm_activity
        if busy or idle_for < ttl:
            return {"swept": [], "reason": "busy-or-fresh"}
        try:
            loaded = _loaded_llms()
        except Exception:
            loaded = []
        swept = []
        for m in loaded:
            if not m or self._is_cpu_placed(m):
                continue
            cfg = _model_cfg_of(m)
            if cfg.get("pin"):
                continue                       # pinned model stays resident by design
            m_ttl = int(cfg.get("ttl") or ttl)  # honour a longer per-model TTL
            if idle_for < m_ttl:
                continue
            rc, _out = _ssh(LMS, "unload", m, timeout=20)
            if rc == 0:
                swept.append(m)
                log.info("[orch] idle-sweep unloaded %s (idle %ds ≥ ttl %ds)",
                         m, int(idle_for), m_ttl)
        if swept:
            with self._lock:
                if self._resident and self._resident[0] == "llm":
                    self._resident = None
        return {"swept": swept}

    def _ensure_loaded(self, model: str) -> bool:
        """Make `model` the sole resident LLM and VERIFY it via `lms ps` before we
        dispatch. This is the fix for the proxy's 'Model is unloaded' race: model
        loading happens ONLY here, in the single worker, and is confirmed resident
        before the request runs. Returns True on success, False if the load never
        takes (caller turns that into a clean task error)."""
        model = (model or "").split("lmstudio/", 1)[-1].strip()
        if not model:
            return False
        def _match():
            try:
                loaded = _loaded_llms()
            except Exception:
                loaded = []
            return any(m == model or m.endswith("/" + model) or model.endswith("/" + m)
                       for m in loaded), loaded
        ok, loaded = _match()
        if ok:
            with self._lock:
                self._current_llm_model = model
                self._resident = ("llm", model)
            return True
        # `lms load` is SYNCHRONOUS (blocks until the model is ready). MULTI-MODEL
        # eviction policy: CPU-placed instances (`@cpu` / gpu:"off") and PINNED
        # models coexist and are never evicted; only unpinned GPU residents are
        # cleared to make room for a new GPU model. A `@cpu` load evicts nothing.
        new_is_cpu = model.endswith("@cpu") or str(_model_cfg_of(model).get("gpu")) == "off"
        for attempt in range(2):
            _, loaded = _match()
            for m in loaded:
                if new_is_cpu:
                    continue                    # CPU loads never displace anyone
                mc = _model_cfg_of(m)
                if m.endswith("@cpu") or str(mc.get("gpu")) == "off" or mc.get("pin"):
                    continue                    # CPU side-models + pinned stay resident
                _ssh(LMS, "unload", m, timeout=20)
            log.info("[orch] ensure_loaded: loading '%s' (attempt %d)", model, attempt + 1)
            _ssh(LMS, *_load_args(model), timeout=240)
            time.sleep(3)                      # let it settle past any 'loading' state
            ok, now = _match()
            if ok:
                with self._lock:
                    self._current_llm_model = model
                    self._resident = ("llm", model)
                log.info("[orch] ensure_loaded: '%s' resident", model)
                return True
            log.warning("[orch] ensure_loaded: '%s' not resident after load (saw %s)", model, now)
        return False

    def _pick_llm_model(self) -> str:
        """Borrow an already-loaded LLM if present (avoids evicting OpenClaw's model
        and avoids failed loads); otherwise fall back to the configured/Settings model."""
        try:
            loaded = _loaded_llms()
            if loaded:
                log.info("[orch] borrowing loaded model: %s", loaded[0])
                return loaded[0]
        except Exception:
            pass
        return _active_model(self.llm_model)

    # ─── GPU operations ───────────────────────────────────────────────────────

    def _set(self, llm: str = None, img: str = None):
        with self._lock:
            if llm is not None:
                self._llm_state = llm
            if img is not None:
                self._img_state = img

    def _gpu_free_mb(self):
        """Free VRAM on the node's GPU in MiB, or None if the query failed."""
        rc, out = _ssh("nvidia-smi", "--query-gpu=memory.free",
                       "--format=csv,noheader,nounits", timeout=15)
        if rc != 0 or not out:
            return None
        try:
            return int(out.strip().splitlines()[0].strip())
        except Exception:
            return None

    def _gpu_vram_holders(self) -> str:
        """'pid name used_memory' lines for every compute process on the GPU —
        the OOM post-mortem, gathered BEFORE the OOM."""
        rc, out = _ssh("nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                       "--format=csv,noheader", timeout=15)
        return out.strip() if rc == 0 and out.strip() else "unknown (nvidia-smi query failed)"

    def _verify_vram_free(self):
        """Poll the node's free VRAM until ≥ _VIDEO_MIN_FREE_MB MiB or timeout.
        Halfway through, retry the unloads once (LM Studio JIT-reloads a model
        for an in-flight request; a straggler ComfyUI free can lag). On timeout,
        raise RuntimeError naming the process(es) holding VRAM so the job fails
        with an actionable message instead of OOMing at the end of a denoise.
        A failed nvidia-smi QUERY (transient SSH hiccup) does NOT fail the job —
        preflight already proved the node reachable; we just proceed as before."""
        if _VIDEO_MIN_FREE_MB <= 0:
            return
        deadline = time.time() + max(10, _VRAM_FREE_WAIT)
        refreed = False
        last = None
        while True:
            free = self._gpu_free_mb()
            if free is None:
                log.warning("[orch] video_acquire: could not query free VRAM — proceeding unverified")
                return
            last = free
            if free >= _VIDEO_MIN_FREE_MB:
                log.info("[orch] video_acquire: VRAM verified free (%d MiB ≥ %d MiB)",
                         free, _VIDEO_MIN_FREE_MB)
                return
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            if not refreed and remaining < max(10, _VRAM_FREE_WAIT) / 2:
                log.warning("[orch] video_acquire: only %d MiB free — retrying ComfyUI/LLM unload", free)
                self._free_comfyui()
                self._unload_llm()
                refreed = True
            time.sleep(3)
        holders = self._gpu_vram_holders()
        raise RuntimeError(
            f"GPU not free for video generation: only {last} MiB VRAM free after "
            f"{_VRAM_FREE_WAIT}s (need ≥ {_VIDEO_MIN_FREE_MB} MiB; override via "
            f"STORE_VIDEO_MIN_FREE_MB). Processes holding VRAM (pid, name, used): "
            f"{holders}. Free the GPU and retry.")

    def _free_comfyui(self):
        """POST /free to ComfyUI — unloads model weights, keeps process alive."""
        try:
            r = httpx.post(
                f"{COMFYUI}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=10,
            )
            time.sleep(2)   # give VRAM time to settle
            log.info("[orch] ComfyUI /free → %s", r.status_code)
        except Exception as e:
            log.info("[orch] ComfyUI /free skipped: %s", e)
        with self._lock:
            if self._resident and self._resident[0] in ("image", "video"):
                self._resident = None   # image/video family no longer owns the VRAM

    @staticmethod
    def _is_cpu_placed(model: str) -> bool:
        """A model instance that holds NO GPU VRAM: an `@cpu` alias or one configured
        gpu:"off". These coexist with image/video gen and must NOT be unloaded to free
        the GPU (they aren't on it)."""
        try:
            return bool(model) and (model.endswith("@cpu")
                                    or str(_model_cfg_of(model).get("gpu")) == "off")
        except Exception:
            return False

    def _unload_llm(self):
        """Free GPU VRAM by unloading EVERY GPU-resident LLM `lms ps` reports — not just
        the store's tracked enhance_model.

        The old code unloaded only `_active_model()` (the DB `enhance_model`). When the
        actually-resident model differed — a coder model loaded by the dev-swarm/OpenClaw,
        or a model JIT-loaded by LM Studio itself — that `unload <enhance_model>` was a
        no-op and the real model kept holding ~8-9 GB through the whole image/video job,
        starving ComfyUI. We now enumerate the real residents and unload each, then verify
        VRAM was actually freed (one retry for stragglers). CPU-placed side models
        (`@cpu` / gpu:off) hold no GPU VRAM and are left resident."""
        try:
            loaded = _loaded_llms()
        except Exception:
            loaded = []
        # If `lms ps` returned nothing (e.g. transient SSH failure), fall back to the
        # tracked model so we at least attempt the historical unload.
        targets = [m for m in loaded if m and not self._is_cpu_placed(m)]
        if not targets and not loaded:
            fallback = _active_model(self.llm_model)
            if fallback and not self._is_cpu_placed(fallback):
                targets = [fallback]
        if not targets:
            log.info("[orch] _unload_llm: no GPU-resident LLM to free (loaded=%s)", loaded)
            return
        for model in targets:
            rc, out = _ssh(LMS, "unload", model, timeout=20)
            if rc == 0:
                log.info("[orch] lms unload OK: %s", model)
            else:
                # Might fail if already unloaded — not an error
                log.info("[orch] lms unload rc=%d for %s: %s", rc, model, out[:80])
        # Verify VRAM actually freed; retry once for anything still resident.
        try:
            still = [m for m in _loaded_llms() if m and not self._is_cpu_placed(m)]
        except Exception:
            still = []
        if still:
            log.warning("[orch] LLM(s) still resident after unload: %s — retrying", still)
            for model in still:
                _ssh(LMS, "unload", model, timeout=20)
        with self._lock:
            if self._resident and self._resident[0] == "llm":
                self._resident = None   # the LLM family no longer owns the VRAM


# ── Singleton ─────────────────────────────────────────────────────────────────
orch = Orchestrator()

"""Warm speech-to-text for the Jarvis voice loop.

Reuses OpenClaw's existing faster-whisper install (`~/.openclaw/stt/`) but keeps the
model RESIDENT in a long-lived worker subprocess so we don't pay the model-load cost on
every utterance (the cold `transcribe.sh` reloads the model each call — fine for a
one-off meeting transcript, far too slow for a back-and-forth voice chat).

Runs on the GTX 1060 / CPU that OpenClaw's STT already uses — deliberately SEPARATE from
the .210 LLM GPU slot, so transcription never contends with or evicts the brain model.
See app/VOICE.md.

Public API:
    transcribe(audio_path, model="small") -> (text, ms)
    health() -> {"ready": bool, "warm": bool, "model": str, "engine": str}
"""
import os
import subprocess
import threading
import time

STT_DIR = os.path.expanduser("~/.openclaw/stt")
VENV_PY = os.path.join(STT_DIR, "venv", "bin", "python")
SCRIPT = os.path.join(STT_DIR, "transcribe.sh")
SITE = os.path.join(STT_DIR, "venv", "lib", "python3.12", "site-packages")

# The warm worker: load WhisperModel once, then transcribe one file path per stdin line
# and emit `<text>\x1e` (record separator) per result. int8 on the 1060, CPU fallback —
# same recipe as transcribe.sh so behaviour matches the existing OpenClaw pipeline.
_WORKER_SRC = r"""
import sys
from faster_whisper import WhisperModel
name = sys.argv[1] if len(sys.argv) > 1 else "small"
try:
    model = WhisperModel(name, device="cuda", compute_type="int8")
except Exception:
    model = WhisperModel(name, device="cpu", compute_type="int8")
sys.stderr.write("READY\n"); sys.stderr.flush()
for line in sys.stdin:
    path = line.strip()
    if not path:
        continue
    try:
        # beam_size=1 (greedy): short voice commands don't need beam search, and it
        # roughly halves latency vs the meeting-transcription default of 5.
        segs, _ = model.transcribe(path, vad_filter=True,
                                   vad_parameters=dict(min_silence_duration_ms=500),
                                   beam_size=1)
        out = "".join(s.text for s in segs).strip()
    except Exception as e:
        out = ""
        sys.stderr.write("ERR %s\n" % e); sys.stderr.flush()
    sys.stdout.write(out + "\x1e"); sys.stdout.flush()
"""


def _worker_env():
    env = dict(os.environ)
    ld = ":".join([os.path.join(SITE, "nvidia", "cublas", "lib"),
                   os.path.join(SITE, "nvidia", "cudnn", "lib"),
                   os.path.join(SITE, "nvidia", "cuda_nvrtc", "lib")])
    env["LD_LIBRARY_PATH"] = ld + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    return env


class _Warm:
    """One resident faster-whisper worker. Lazily started, auto-respawned if it dies."""
    def __init__(self):
        self._proc = None
        self._model = None
        self._lock = threading.Lock()

    def _alive(self):
        return self._proc is not None and self._proc.poll() is None

    def _start(self, model):
        if not os.path.exists(VENV_PY):
            raise FileNotFoundError(f"OpenClaw STT venv not found at {VENV_PY}")
        p = subprocess.Popen([VENV_PY, "-u", "-c", _WORKER_SRC, model],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=_worker_env(), text=True)
        # Wait for the READY line so the first real request isn't racing model load.
        deadline = time.time() + 120
        while time.time() < deadline:
            line = p.stderr.readline()
            if not line and p.poll() is not None:
                raise RuntimeError("STT worker died during startup")
            if line.strip() == "READY":
                break
        else:
            p.kill(); raise TimeoutError("STT worker did not become ready in 120s")
        self._proc, self._model = p, model

    def transcribe(self, path, model="small"):
        with self._lock:
            if not self._alive() or self._model != model:
                if self._alive():
                    try: self._proc.kill()
                    except Exception: pass
                self._start(model)
            t0 = time.time()
            self._proc.stdin.write(path + "\n"); self._proc.stdin.flush()
            buf = []
            while True:
                ch = self._proc.stdout.read(1)
                if ch == "" and self._proc.poll() is not None:
                    raise RuntimeError("STT worker died mid-transcribe")
                if ch == "\x1e":
                    break
                buf.append(ch)
            return "".join(buf).strip(), int((time.time() - t0) * 1000)

    def warm(self):
        return self._alive()


_warm = _Warm()


def transcribe(audio_path: str, model: str = "small") -> tuple[str, int]:
    """Transcribe an audio file → (text, elapsed_ms). Warm worker first; on any failure
    fall back to the cold `transcribe.sh` so a single utterance never hard-fails."""
    try:
        return _warm.transcribe(audio_path, model)
    except Exception:
        t0 = time.time()
        env = _worker_env(); env["WHISPER_MODEL"] = model
        r = subprocess.run(["bash", SCRIPT, audio_path], capture_output=True,
                           text=True, env=env, timeout=300)
        return r.stdout.strip(), int((time.time() - t0) * 1000)


def health() -> dict:
    return {"ready": os.path.exists(VENV_PY) or os.path.exists(SCRIPT),
            "warm": _warm.warm(), "model": _warm._model or "", "engine": "faster-whisper"}

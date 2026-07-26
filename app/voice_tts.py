"""Text-to-speech for the Jarvis voice loop — dual engine.

- kokoro (DEFAULT): a modern neural TTS. Natural, small, many voices incl. British.
  Runs on CPU here (the store host). Higher quality than Piper; on this 4-core box it's
  ~2x realtime, so it's the "sounds best" option. Moving it to the node GPU (queue-managed)
  is the follow-up that makes it fast too.
- piper: fast, robotic-ish, CPU. The "fastest" fallback.

Both are fully CPU here and never touch the .210 LLM GPU slot. See app/VOICE.md.

Public API:
    synth(text, voice=None, engine=None) -> bytes              # a complete WAV
    synth_stream(text, voice=None, engine=None) -> iter[bytes] # same WAV, chunked
    voices(engine=None) -> list[str]
    health() -> {...}
"""
import io
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import wave

_HERE = os.path.dirname(os.path.abspath(__file__))
VOICE_DIR = os.path.join(_HERE, "voice_models")

DEFAULT_ENGINE = "kokoro"

# ── Kokoro (neural, default) ─────────────────────────────────────────────────
KOKORO_MODEL = os.path.join(VOICE_DIR, "kokoro-v1.0.onnx")
KOKORO_VOICES = os.path.join(VOICE_DIR, "voices-v1.0.bin")
KOKORO_DEFAULT_VOICE = "bm_george"      # British male — the Jarvis default
_kokoro = None
_kokoro_lock = threading.Lock()


def _kokoro_get():
    global _kokoro
    if _kokoro is None:
        with _kokoro_lock:
            if _kokoro is None:
                from kokoro_onnx import Kokoro
                _kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
    return _kokoro


def _kokoro_lang(voice: str) -> str:
    # b* voices are British English; everything else American English.
    return "en-gb" if (voice or "").startswith(("bm_", "bf_")) else "en-us"


def _kokoro_synth(text: str, voice: str) -> bytes:
    import numpy as np
    voice = voice or KOKORO_DEFAULT_VOICE
    k = _kokoro_get()
    samples, sr = k.create(text, voice=voice, speed=1.0, lang=_kokoro_lang(voice))
    pcm = (np.clip(np.asarray(samples), -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(int(sr)); w.writeframes(pcm)
    return buf.getvalue()


def kokoro_ready() -> bool:
    return os.path.exists(KOKORO_MODEL) and os.path.exists(KOKORO_VOICES)


def _kokoro_voices() -> list:
    try:
        return sorted(_kokoro_get().get_voices())
    except Exception:
        return ["bm_george", "bm_lewis", "bm_daniel", "bf_emma", "bf_isabella", "af_heart", "am_michael"]


# ── Piper (fast fallback) ────────────────────────────────────────────────────
PIPER_DEFAULT_VOICE = "en_GB-alan-medium"
_HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
_PIPER_PATHS = {
    "en_GB-alan-medium":  "en/en_GB/alan/medium/en_GB-alan-medium",
    "en_GB-northern_english_male-medium": "en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium",
    "en_US-ryan-high":    "en/en_US/ryan/high/en_US-ryan-high",
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium",
}
_dl_lock = threading.Lock()


def _piper_bin():
    return shutil.which("piper") or (
        os.path.join(_HERE, "..", "venv", "bin", "piper")
        if os.path.exists(os.path.join(_HERE, "..", "venv", "bin", "piper")) else None)


def _piper_ensure(voice: str) -> str:
    onnx = os.path.join(VOICE_DIR, voice + ".onnx")
    cfg = onnx + ".json"
    if os.path.exists(onnx) and os.path.exists(cfg):
        return onnx
    rel = _PIPER_PATHS.get(voice)
    if not rel:
        raise ValueError(f"unknown piper voice '{voice}'")
    with _dl_lock:
        if not (os.path.exists(onnx) and os.path.exists(cfg)):
            os.makedirs(VOICE_DIR, exist_ok=True)
            import urllib.request
            for url, dest in ((f"{_HF}/{rel}.onnx", onnx), (f"{_HF}/{rel}.onnx.json", cfg)):
                tmp = dest + ".part"; urllib.request.urlretrieve(url, tmp); os.replace(tmp, dest)
    return onnx


def _piper_synth(text: str, voice: str) -> bytes:
    binp = _piper_bin()
    if not binp:
        raise RuntimeError("piper not installed")
    onnx = _piper_ensure(voice or PIPER_DEFAULT_VOICE)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        out = tf.name
    try:
        subprocess.run([binp, "-m", onnx, "-f", out], input=text, text=True,
                       capture_output=True, timeout=120, check=True)
        with open(out, "rb") as f:
            return f.read()
    finally:
        try: os.unlink(out)
        except OSError: pass


# ── Dispatch ─────────────────────────────────────────────────────────────────
def _default_voice(engine: str) -> str:
    return KOKORO_DEFAULT_VOICE if engine == "kokoro" else PIPER_DEFAULT_VOICE


def synth(text: str, voice: str = None, engine: str = None) -> bytes:
    text = (text or "").strip()
    if not text:
        return b""
    engine = engine or DEFAULT_ENGINE
    if engine == "kokoro" and kokoro_ready():
        try:
            return _kokoro_synth(text, voice or KOKORO_DEFAULT_VOICE)
        except Exception:
            engine = "piper"                       # fall back to piper on any kokoro failure
            voice = None
    return _piper_synth(text, voice or PIPER_DEFAULT_VOICE)


def synth_stream(text: str, voice: str = None, engine: str = None):
    data = synth(text, voice, engine)
    for i in range(0, len(data), 16384):
        yield data[i:i + 16384]


def voices(engine: str = None) -> list:
    engine = engine or DEFAULT_ENGINE
    return _kokoro_voices() if engine == "kokoro" else list(_PIPER_PATHS.keys())


def health() -> dict:
    binp = _piper_bin()
    return {"ready": kokoro_ready() or bool(binp), "engine": DEFAULT_ENGINE,
            "kokoro": {"ready": kokoro_ready(), "voice": KOKORO_DEFAULT_VOICE},
            "piper": {"ready": bool(binp), "voice": PIPER_DEFAULT_VOICE},
            "voice": _default_voice(DEFAULT_ENGINE)}

"""Jarvis voice endpoints — ears (STT) + mouth (TTS) + settings around the EXISTING
assistant loop (/api/agent/*). Deliberately NOT on the orch LLM queue: Whisper (1060)
and Piper (CPU) are small and independent and must never contend for the .210 LLM slot.
See app/VOICE.md.
"""
import os
import tempfile

from fastapi import APIRouter, UploadFile, File
from fastapi.responses import StreamingResponse, JSONResponse, Response

from deps import *          # get_setting, get_conn, BaseModel, Optional, etc.
import voice_stt
import voice_tts
import voice_node

router = APIRouter()

_MODES = {"ptt", "wake", "open"}
_DEFAULTS = {
    "voice_enabled": "1",
    "voice_trigger_mode": "wake",
    "voice_wake_phrase": "hey jarvis",
    "voice_tts_engine": voice_tts.DEFAULT_ENGINE,   # kokoro (natural) | piper (fast)
    "voice_tts_voice": voice_tts.KOKORO_DEFAULT_VOICE,
    "voice_stt_model": "base.en",                   # faster than 'small' on the 1060
}


def _settings() -> dict:
    return {k: get_setting(k, d) for k, d in _DEFAULTS.items()}


# ─── Ears: speech → text ─────────────────────────────────────────────────────
@router.post("/api/voice/stt")
async def voice_stt_endpoint(audio: UploadFile = File(...)):
    data = await audio.read()
    if not data:
        return JSONResponse({"error": "empty audio"}, status_code=400)
    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        tf.write(data); path = tf.name
    try:
        model = get_setting("voice_stt_model", "base.en") or "base.en"
        text, ms = voice_stt.transcribe(path, model)
        return {"text": text, "ms": ms}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    finally:
        try: os.unlink(path)
        except OSError: pass


# ─── Mouth: text → speech ────────────────────────────────────────────────────
class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None


@router.post("/api/voice/tts")
def voice_tts_endpoint(req: TTSRequest):
    engine = get_setting("voice_tts_engine", voice_tts.DEFAULT_ENGINE)
    voice = req.voice or get_setting("voice_tts_voice", "")
    # If a node TTS session is active, synth there (warm, ~0.8s, zero VRAM) — fall back
    # to local CPU on any node hiccup so a voice turn never hard-fails.
    if voice_node.is_active():
        try:
            return Response(content=voice_node.synth(req.text, voice or None), media_type="audio/wav")
        except Exception:
            pass
    try:
        return StreamingResponse(voice_tts.synth_stream(req.text, voice or None, engine),
                                 media_type="audio/wav")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# ─── On-demand node TTS lifecycle (activate → ready → unload) ─────────────────
@router.post("/api/voice/activate")
def voice_activate():
    """Load Kokoro on the node for this session (fast, warm, zero VRAM)."""
    return voice_node.activate()


@router.post("/api/voice/deactivate")
def voice_deactivate():
    """Unload the node TTS server (frees its RAM). Called on Stop and on page leave."""
    return voice_node.deactivate()


@router.get("/api/voice/node-status")
def voice_node_status():
    return voice_node.status()


# ─── Settings ────────────────────────────────────────────────────────────────
@router.get("/api/voice/settings")
def voice_settings():
    s = _settings()
    return {"enabled": s["voice_enabled"] == "1",
            "trigger_mode": s["voice_trigger_mode"],
            "wake_phrase": s["voice_wake_phrase"],
            "engine": s["voice_tts_engine"],
            "voice": s["voice_tts_voice"],
            "stt_model": s["voice_stt_model"],
            "voices": voice_tts.voices(s["voice_tts_engine"]),
            "engines": ["kokoro", "piper"]}


class VoiceSettingsRequest(BaseModel):
    enabled: Optional[bool] = None
    trigger_mode: Optional[str] = None
    wake_phrase: Optional[str] = None
    engine: Optional[str] = None
    voice: Optional[str] = None
    stt_model: Optional[str] = None


@router.post("/api/voice/settings")
def voice_settings_save(req: VoiceSettingsRequest):
    conn = get_conn()
    def _put(k, v):
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, str(v)))
    if req.enabled is not None:
        _put("voice_enabled", "1" if req.enabled else "0")
    if req.trigger_mode is not None:
        if req.trigger_mode not in _MODES:
            return JSONResponse({"error": f"trigger_mode must be one of {sorted(_MODES)}"}, status_code=400)
        _put("voice_trigger_mode", req.trigger_mode)
    if req.wake_phrase is not None:
        _put("voice_wake_phrase", req.wake_phrase.strip().lower() or "hey jarvis")
    if req.engine is not None:
        if req.engine not in ("kokoro", "piper"):
            return JSONResponse({"error": "engine must be kokoro or piper"}, status_code=400)
        _put("voice_tts_engine", req.engine)
    if req.voice is not None:
        eng = req.engine or get_setting("voice_tts_engine", voice_tts.DEFAULT_ENGINE)
        if req.voice not in voice_tts.voices(eng):
            return JSONResponse({"error": "unknown voice for engine " + eng}, status_code=400)
        _put("voice_tts_voice", req.voice)
    if req.stt_model is not None:
        _put("voice_stt_model", req.stt_model.strip() or "small")
    conn.commit(); conn.close()
    return {"ok": True, **_settings()}


# ─── Health ──────────────────────────────────────────────────────────────────
@router.get("/api/voice/health")
def voice_health():
    s = _settings()
    return {"stt": voice_stt.health(), "tts": voice_tts.health(),
            "enabled": s["voice_enabled"] == "1", "trigger_mode": s["voice_trigger_mode"],
            "voice": s["voice_tts_voice"]}

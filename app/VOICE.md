# Jarvis — realtime voice for the store AI Assistant

Talk to the EXISTING store assistant (`/api/agent/*`) by voice. We are not building a
new brain — only ears + mouth + a browser voice UI around the assistant that already
exists. Web-first; every capability is an HTTP endpoint so a future always-on desktop
client on .210 reuses the same plumbing.

## Pieces (what already exists vs. new)
- 👂 Ears — faster-whisper. Existing script `~/.openclaw/stt/transcribe.sh <file>` → prints text
  (runs on the GTX 1060, SEPARATE from the .210 LLM card). New warm wrapper avoids per-call model reload.
- 🧠 Brain — the existing assistant loop: `POST /api/agent/chat`, poll `GET /api/agent/events`.
  Reuse verbatim (it already runs through the fixed GPU queue, has tools, memory, approvals).
- 👄 Mouth — Piper TTS (fast, CPU-only, natural). New. MMS-TTS (`routers/audio.py`) is a fallback.

## Golden rule — do NOT put STT/TTS on the LLM GPU queue
Whisper (1060) and Piper (CPU) are small and independent. Running them through `orch.submit_llm`
would serialize them behind the brain and add latency. They must never grab the .210 LLM slot.

## Data flow (Phase 1)
1. Browser captures ONE utterance (how it starts depends on trigger mode).
2. `POST /api/voice/stt` (multipart audio) → `{ "text": "...", "ms": 123 }`.
3. Feed text into the assistant EXACTLY like the text UI does (reuse tab-agent.js's existing
   `/api/agent/chat` + `/api/agent/events` calls). Get the assistant's final reply text.
4. `POST /api/voice/tts` `{ "text": "..." }` → streamed `audio/ogg` (or wav). Play it.
5. Barge-in: if the user starts speaking while audio is playing, stop playback and capture anew.

## Trigger modes (user-selectable in the UI; DEFAULT = "wake")
- `ptt`  — push-to-talk: hold a button / spacebar to talk. Most reliable.
- `wake` — wake word "Hey Jarvis" (DEFAULT). Always listening for the phrase, then captures the
  following utterance (stop on silence via VAD).
- `open` — open mic: VAD auto-detects speech start/stop, no button.

## Backend — files I (Claude) own
- `app/voice_stt.py` — warm faster-whisper: `transcribe(path_or_bytes, model="small") -> (text, ms)`.
  Reuse OpenClaw's venv/model dir if present; load model once, keep resident. CPU/1060, NOT the LLM slot.
- `app/voice_tts.py` — Piper: `synth(text, voice=None) -> bytes` and `synth_stream(text) -> iter[bytes]`.
  Auto-download the voice model on first use. Default voice: a British male (Jarvis). CPU only.
- `app/routers/voice.py` — `router = APIRouter()`, mounted by adding `voice` to the main.py include list.
  - `POST /api/voice/stt`     (multipart `audio`) → `{text, ms}`
  - `POST /api/voice/tts`      `{text, voice?}` → StreamingResponse audio/ogg
  - `GET  /api/voice/settings` → the keys below
  - `POST /api/voice/settings` `{trigger_mode?, wake_phrase?, voice?, enabled?, stt_model?}`
  - `GET  /api/voice/health`   → `{stt_ready, tts_ready, voice, trigger_mode}`
  - Settings table keys (via get_setting/INSERT OR REPLACE, like routers/agent/settings.py):
    `voice_enabled`(default "1"), `voice_trigger_mode`(default "wake"),
    `voice_wake_phrase`(default "hey jarvis"), `voice_tts_voice`, `voice_stt_model`(default "small").

## Frontend — files the Fable 5 agents own (NEW files only; I wire the <script> tags + tab hook)
- `static/js/voice-jarvis.js` — the controller. Vanilla JS matching the style of `static/js/tab-agent.js`
  (read it). Exposes `window.VoiceJarvis = { init(container), start(), stop(), setMode(m) }`.
  - Mic capture via getUserMedia + MediaRecorder (opus/webm) or AudioWorklet.
  - State machine: idle → listening → thinking → speaking; visible status + live transcript.
  - Sends captured audio to `/api/voice/stt`, then drives the assistant by REUSING the same
    `/api/agent/chat` + `/api/agent/events` calls tab-agent.js already makes (don't reinvent), then
    `/api/voice/tts` and plays the returned audio. Implements barge-in (stop playback on speech).
  - A floating mic button + a small mode selector (ptt / wake / open), reading defaults from
    `/api/voice/settings`. Push-to-talk = hold button or Space.
- `static/js/voice-wake.js` — wake word + VAD, isolated. Exposes
  `window.VoiceWake = { startWake(phrase, onWake), stopWake(), vadListen(onSpeechEnd) }`.
  - Wake word "Hey Jarvis": prefer a small in-browser detector (openWakeWord ONNX via onnxruntime-web
    WASM, or Porcupine if a key exists). Acceptable Phase-1 fallback: the browser Web Speech API
    (`webkitSpeechRecognition`) matching the phrase — note the fallback clearly in code comments.
  - VAD for `open` mode + end-of-utterance detection (energy/webrtcvad-wasm). Keep self-contained.
  - All assets must be self-hosted under `static/` (no external CDNs — the store may be offline/LAN).

## Latency target
First audio back < ~1.5 s after you stop speaking. Phase 2 (later): streaming partial STT, stream the
assistant's first sentence into TTS before it finishes, tighter barge-in.

## Desktop-ready (Phase 3, later)
Because ears/brain/mouth are all HTTP, an Electron/Python client on .210 can do mic+wake locally and
call the same `/api/voice/*` + `/api/agent/*` endpoints. No server rework needed.

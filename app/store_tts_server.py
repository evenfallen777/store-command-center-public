#!/usr/bin/env python3
"""Persistent Kokoro TTS server for the store's Jarvis voice — runs on the GPU NODE
(.210), NOT the store host. Loads Kokoro once (warm) and serves synth over HTTP so a
voice session pays the model-load cost ONCE. The store starts it on 'activate' and
kills it on 'deactivate'/leave.

Runs on the node CPU (≈4x the store host's CPU, RTF ~0.5) → zero VRAM, so it never
touches the LLM GPU slot or the queue. Stdlib-only (needs just kokoro-onnx in
~/kokoro-venv). See app/VOICE.md.

Usage: ~/kokoro-venv/bin/python ~/store_tts_server.py --port 8790
"""
import argparse
import io
import json
import os
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = os.path.expanduser("~/kokoro_models")
DEFAULT_VOICE = "bm_george"          # British male — the Jarvis default
_kokoro = None
_lock = threading.Lock()


def _load():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(os.path.join(MODELS, "kokoro-v1.0.onnx"),
                         os.path.join(MODELS, "voices-v1.0.bin"))
    return _kokoro


def _lang(v):
    return "en-gb" if (v or "").startswith(("bm_", "bf_")) else "en-us"


def _synth(text, voice):
    import numpy as np
    voice = voice or DEFAULT_VOICE
    k = _load()
    with _lock:                       # kokoro create() serialized (one CPU synth at a time)
        samples, sr = k.create(text, voice=voice, speed=1.0, lang=_lang(voice))
    pcm = (np.clip(np.asarray(samples), -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(int(sr)); w.writeframes(pcm)
    return buf.getvalue()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json(200, {"ready": _kokoro is not None, "voice": DEFAULT_VOICE})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/synth"):
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            wav = _synth((body.get("text") or "").strip(), body.get("voice"))
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)
        except Exception as e:
            self._json(502, {"error": str(e)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    _load()                           # warm the model first
    srv = ThreadingHTTPServer((a.host, a.port), H)   # BIND before announcing (so "listening" is true)
    print("kokoro-tts listening on %s:%d" % (a.host, a.port), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

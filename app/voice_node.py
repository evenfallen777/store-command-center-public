"""On-demand node TTS lifecycle for Jarvis voice.

"Activate → load Kokoro on the node → ready → unload on stop/leave." Runs
store_tts_server.py on the GPU node (.210) CPU — fast (~0.8s/sentence over the LAN),
warm for the whole session (no per-call reload), and ZERO VRAM, so it never touches the
LLM GPU slot or the queue. The store starts it on activate and kills it on
deactivate/leave so nothing lingers. See app/VOICE.md.
"""
import os
import subprocess
import threading
import time

import httpx

from config import GPU_HOST, GPU_SSH_USER, BOX_SSH

NODE_TTS_PORT = 8790
NODE_URL = f"http://{GPU_HOST}:{NODE_TTS_PORT}"
_LOCAL_SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "store_tts_server.py")
_REMOTE_SERVER = "~/store_tts_server.py"
_SCP = ["scp", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]

_active = False
_lock = threading.Lock()


def _health(timeout=2) -> bool:
    try:
        r = httpx.get(NODE_URL + "/health", timeout=timeout)
        return r.status_code == 200 and bool(r.json().get("ready"))
    except Exception:
        return False


def is_active() -> bool:
    return _active


def status() -> dict:
    return {"active": _active, "ready": _health() if _active else False, "url": NODE_URL}


def activate(wait: int = 45) -> dict:
    """Deploy + start the node TTS server and wait until it's warm. Idempotent."""
    global _active
    with _lock:
        if _health():                       # already up (e.g. a prior session)
            _active = True
            return {"ok": True, "ready": True, "note": "already running"}
        # push the latest server script, then start it detached (setsid+nohup so it
        # outlives the ssh session) and POLL until the port is actually listening — keeping
        # the ssh session alive through model load avoids the start/detach race.
        subprocess.run(_SCP + [_LOCAL_SERVER, f"{GPU_SSH_USER}@{GPU_HOST}:{_REMOTE_SERVER}"],
                       capture_output=True, timeout=30)
        start = (
            f"pkill -f store_tts_server.py 2>/dev/null; sleep 1; "
            f"setsid nohup ~/kokoro-venv/bin/python {_REMOTE_SERVER} --port {NODE_TTS_PORT} "
            f"</dev/null >/tmp/kokoro_tts.log 2>&1 & "
            f"for i in $(seq 1 {max(5, wait)}); do ss -ltn 2>/dev/null | grep -q ':{NODE_TTS_PORT} ' "
            f"&& {{ echo READY; break; }}; sleep 1; done"
        )
        try:
            r = subprocess.run(BOX_SSH + [start], capture_output=True, text=True, timeout=wait + 15)
        except Exception as e:
            return {"ok": False, "ready": False, "error": f"could not start node TTS: {e}"}
        if "READY" not in (r.stdout or ""):
            log = subprocess.run(BOX_SSH + ["tail -8 /tmp/kokoro_tts.log 2>&1"],
                                 capture_output=True, text=True, timeout=15)
            return {"ok": False, "ready": False,
                    "error": "node TTS didn't come up: " + ((log.stdout or "").strip()[-300:] or "unknown")}
        # it's listening on the node; confirm it's reachable from the store side too
        for _ in range(10):
            if _health():
                break
            time.sleep(1)
        _active = True
        return {"ok": True, "ready": True}


def deactivate() -> dict:
    """Kill the node TTS server → frees its RAM. Idempotent; safe to call on page leave."""
    global _active
    with _lock:
        try:
            subprocess.run(BOX_SSH + ["pkill -f store_tts_server.py 2>/dev/null; echo ok"],
                           capture_output=True, text=True, timeout=15)
        except Exception:
            pass
        _active = False
        return {"ok": True}


def synth(text: str, voice: str = None) -> bytes:
    """Synthesize one utterance on the node. Returns WAV bytes; raises if unreachable."""
    r = httpx.post(NODE_URL + "/synth", json={"text": text, "voice": voice}, timeout=30)
    r.raise_for_status()
    return r.content

#!/usr/bin/env bash
# Store Command Center — imagegen bridge tool (reference implementation).
#
# The store (app/services.py:run_generation) calls GENERATE_SCRIPT as:
#   generate.sh <prompt> <out_path> <width> <height> <steps> <seed> <model_name>
# and expects a PNG written to <out_path> on the machine that runs the store.
#
# This drives ComfyUI (SDXL) on the GPU node over its HTTP API and downloads the
# result. ComfyUI URL: STORE_COMFYUI_URL, else http://${STORE_GPU_HOST}:8188.
#
# INSTALL: this belongs at ~/.openclaw/tools/imagegen/generate.sh (path from
# STORE_GENERATE_SCRIPT / config.py). setup.sh or node-setup.sh should install it.
# It was recreated during a deployment because the public repo shipped without it.
set -euo pipefail

PROMPT="${1:?prompt required}"
OUT="${2:?out_path required}"
W="${3:-1024}"; H="${4:-1024}"; STEPS="${5:-8}"; SEED="${6:-0}"
MODEL="${7:-dreamshaperXL_lightningDPMSDE.safetensors}"
COMFY="${STORE_COMFYUI_URL:-http://${STORE_GPU_HOST:-127.0.0.1}:8188}"

exec python3 - "$PROMPT" "$OUT" "$W" "$H" "$STEPS" "$SEED" "$MODEL" "$COMFY" <<'PY'
import sys, os, json, time, urllib.request, urllib.parse
prompt, out, w, h, steps, seed, model, comfy = sys.argv[1:9]
w, h, steps, seed = int(w), int(h), int(steps), int(seed)

# Standard SDXL text-to-image workflow (ComfyUI API format). Lightning-friendly
# sampler defaults suit the store's default dreamshaperXL_lightning checkpoint and
# are fine for regular SDXL too.
wf = {
  "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}},
  "5": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
  "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
  "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "lowres, blurry, jpeg artifacts, watermark, text, extra limbs, deformed", "clip": ["4", 1]}},
  "3": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": 2.0,
        "sampler_name": "dpmpp_sde", "scheduler": "karras", "denoise": 1.0,
        "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
  "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
  "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "storegen", "images": ["8", 0]}},
}

def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

pid = post(comfy + "/prompt", {"prompt": wf})["prompt_id"]

img = None
for _ in range(150):  # up to ~5 min (checkpoint load + sampling)
    time.sleep(2)
    try:
        hist = json.loads(urllib.request.urlopen(comfy + "/history/" + pid, timeout=15).read())
    except Exception:
        continue
    if pid in hist:
        status = hist[pid].get("status", {})
        for node in hist[pid].get("outputs", {}).values():
            if node.get("images"):
                img = node["images"][0]; break
        if img:
            break
        if status.get("status_str") == "error":
            sys.stderr.write("ComfyUI reported an error: %s\n" % json.dumps(status)[:400]); sys.exit(1)

if not img:
    sys.stderr.write("timed out waiting for ComfyUI image\n"); sys.exit(1)

q = urllib.parse.urlencode({"filename": img["filename"], "subfolder": img.get("subfolder", ""),
                            "type": img.get("type", "output")})
raw = urllib.request.urlopen(comfy + "/view?" + q, timeout=120).read()
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
with open(out, "wb") as f:
    f.write(raw)
sys.stderr.write("saved %s (%d bytes)\n" % (out, len(raw)))
PY

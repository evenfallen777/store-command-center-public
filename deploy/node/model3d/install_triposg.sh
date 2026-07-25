#!/usr/bin/env bash
# Install TripoSG on the box WITHOUT a CUDA toolkit: patch out the diso (flash)
# extractor and use the built-in CPU skimage marching-cubes path instead.
set -euo pipefail
cd ~
[ -d TripoSG ] || git clone --depth 1 https://github.com/VAST-AI-Research/TripoSG.git
cd ~/TripoSG
# TripoSG downloads weights to a repo-local pretrained_weights/ (not the HF cache).
# If a models SSD is present, symlink that dir onto it so weights never touch the system drive.
SSD="${STORE_MODELS_SSD:-}"; [ -z "$SSD" ] && [ -d /media/user/SSD/models ] && SSD=/media/user/SSD/models
if [ -n "$SSD" ] && [ ! -L ~/TripoSG/pretrained_weights ]; then
  mkdir -p "$SSD/triposg-weights"
  if [ -d ~/TripoSG/pretrained_weights ]; then
    rsync -a ~/TripoSG/pretrained_weights/ "$SSD/triposg-weights/" && rm -rf ~/TripoSG/pretrained_weights
  fi
  ln -s "$SSD/triposg-weights" ~/TripoSG/pretrained_weights
fi
# Patch 1: diso is CUDA-compiled (needs nvcc, which this box lacks) → make it optional.
python3 - <<'PY'
p="triposg/inference_utils.py"; s=open(p).read()
if "DiffDMC = None" not in s:
    s=s.replace("from diso import DiffDMC",
                "try:\n    from diso import DiffDMC\nexcept Exception:\n    DiffDMC = None")
    open(p,"w").write(s); print("patched diso import")
PY
# Patch 2: default to the CPU (skimage) extractor instead of the diso flash decoder.
sed -i 's/use_flash_decoder: bool = True/use_flash_decoder: bool = False/' triposg/pipelines/pipeline_triposg.py
grep -n "use_flash_decoder: bool" triposg/pipelines/pipeline_triposg.py | head -1
# venv reuses ComfyUI's torch + numpy (via .pth) — NEVER installs torch (would break ComfyUI).
python3 -m venv --system-site-packages ~/TripoSG/venv
CSITE="$(ls -d "$HOME"/ComfyUI/venv/lib/python*/site-packages 2>/dev/null | head -1)"
VSITE="$(ls -d "$HOME"/TripoSG/venv/lib/python*/site-packages 2>/dev/null | head -1)"
[ -n "$CSITE" ] && echo "$CSITE" > "$VSITE/zzz_comfyui.pth" || { echo "ERROR: ComfyUI venv not found" >&2; exit 3; }
grep -viE '^(torch|torchvision|numpy|diso)([=<>! ]|$)' requirements.txt > /tmp/triposg_reqs.txt
~/TripoSG/venv/bin/pip install -q -r /tmp/triposg_reqs.txt
python3 -c "print('deps installed')"
# Completion marker for node-setup.sh's gate: written only after the payload
# install above succeeds (set -euo pipefail means a failed pip install exits
# before this line runs). node-setup.sh must NOT gate on venv/bin/python3
# existing alone — that binary appears before the payload installs.
touch ~/TripoSG/.install_complete
echo TRIPOSG_INSTALL_DONE

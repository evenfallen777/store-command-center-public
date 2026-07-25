#!/usr/bin/env bash
# TRELLIS (Microsoft, MIT) — heavy/experimental. Needs the CUDA toolkit. Reuses ComfyUI
# torch (never installs torch). Custom CUDA deps are best-effort; check the log on failure.
#
# SHADOW-TORCH GUARD: xformers/flash-attn/spconv/nvdiffrast/diffoctreerast/kaolin all
# declare torch as a dependency. Unlike install_triposg.sh:34 and install_sf3d.sh:18,22
# (which install from a requirements.txt that's grep-filtered for torch|torchvision|numpy
# before `pip install -r`), this script installs packages one at a time by name, so a
# stray torch/torchvision/numpy pulled in transitively by ANY of them lands directly in
# ~/TRELLIS/venv's own site-packages — which sits AHEAD of the zzz_comfyui.pth path in
# sys.path and SHADOWS ComfyUI's torch 2.5.1+cu121. Symptom: `OSError: libcudart.so.13:
# cannot open shared object file` (a stray cu13 torch got pulled in). Every install below
# is routed through pip_install_filtered(), which grep-filters torch|torchvision|numpy
# (matching the siblings' pattern) out of the package list before installing; a final
# guard also strips out anything that lands directly in the TRELLIS venv anyway.
set -uo pipefail
command -v nvcc >/dev/null || { echo "ERROR: nvcc (CUDA toolkit) not found — install it first" >&2; exit 3; }
export CUDA_HOME="${CUDA_HOME:-/usr}"
cd ~
[ -d TRELLIS ] || git clone --recurse-submodules https://github.com/microsoft/TRELLIS.git
cd ~/TRELLIS
python3 -m venv --system-site-packages ~/TRELLIS/venv
CSITE="$(ls -d "$HOME"/ComfyUI/venv/lib/python*/site-packages | head -1)"
VSITE="$(ls -d ~/TRELLIS/venv/lib/python*/site-packages | head -1)"
echo "$CSITE" > "$VSITE/zzz_comfyui.pth"
PIP=~/TRELLIS/venv/bin/pip

# Same forbidden pattern the siblings grep out of requirements.txt (install_triposg.sh:34,
# install_sf3d.sh:18) — applied here to every explicit package name/arg instead.
FORBIDDEN_RE='^(torch|torchvision|numpy|diso|gpytoolbox)([=<>! ]|$)'
pip_install_filtered() {
  local args=() a
  for a in "$@"; do
    if printf '%s\n' "$a" | grep -qiE "$FORBIDDEN_RE"; then
      echo "WARN: skipping forbidden package (would shadow ComfyUI's torch): $a" >&2
      continue
    fi
    args+=("$a")
  done
  [ ${#args[@]} -eq 0 ] && return 0
  "$PIP" install -q "${args[@]}"
}

pip_install_filtered pillow imageio imageio-ffmpeg tqdm easydict opencv-python scipy ninja rembg onnxruntime trimesh xatlas pyvista pymeshfix igraph transformers safetensors einops || true
pip_install_filtered xformers || echo "xformers failed (will try flash-attn)"
pip_install_filtered flash-attn --no-build-isolation || echo "flash-attn build failed — TRELLIS can use xformers instead"
pip_install_filtered spconv-cu120 || pip_install_filtered spconv-cu121 || echo "spconv failed"
pip_install_filtered git+https://github.com/NVlabs/nvdiffrast.git || echo "nvdiffrast build failed"
pip_install_filtered git+https://github.com/JeffreyXiang/diffoctreerast.git || echo "diffoctreerast build failed"
pip_install_filtered kaolin -f https://nvidia-kaolin.s3.us-east-2.amazonaws.com/torch-2.5.1_cu121.html || echo "kaolin failed"

# Final guard: even with the filter above, if anything above still pulled a stray
# torch/torchvision/numpy in as a TRANSITIVE dependency (pip resolves deps regardless of
# what we asked for by name), it lands directly inside $VSITE and shadows ComfyUI's torch
# via sys.path ordering. Detect that by filesystem (never touch ComfyUI's own venv) and
# strip it back out so the zzz_comfyui.pth-provided torch is what TRELLIS actually sees.
for pkg in torch torchvision numpy; do
  hit="$(find "$VSITE" -maxdepth 1 -iname "${pkg}-*.dist-info" 2>/dev/null | head -1)"
  if [ -n "$hit" ]; then
    echo "WARN: ${pkg} landed directly in TRELLIS's own venv (shadows ComfyUI's torch, causes libcudart drift) — removing: $hit" >&2
    "$PIP" uninstall -y "$pkg" >/dev/null 2>&1 || true
    rm -rf "$hit"
  fi
done
echo TRELLIS_INSTALL_DONE

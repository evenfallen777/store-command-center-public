#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Store Command Center — GPU NODE deploy / health-check
#
# Sets up (or verifies) everything the store's GPU node needs:
#   • image   → ComfyUI (SDXL) + service
#   • video   → diffusers/torch media stack + store_videogen.py
#   • 3d      → TripoSR (image → mesh)
#   • audio   → AudioCraft / MusicGen (opt-in: --with-audio)
#   • llm     → LM Studio + headless autostart service
#   • guard   → gpu-guard.sh + gpu-guard.service (pauses the Store's unified AI
#               queue while a Steam game / heavy GPU app runs, manages the miner)
#   • miner   → JellyMiner (JellyCoin OpenCL miner) venv + jellyminer.service
#   • services→ systemd --user units that autostart the above, + linger
#
# Runs ON THE NODE (Ubuntu). Everything is idempotent — re-run any time.
#
#   ./node-setup.sh check                 # report status only, change nothing
#   ./node-setup.sh deploy                # install/repair everything (no audio)
#   ./node-setup.sh deploy --with-audio   # + set up the audio/music stack
#
# Requires Ubuntu (24.04 recommended) + an NVIDIA GPU. On any other OS it stops
# with a clear message — the store node must be Ubuntu.
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

MODE="${1:-check}"
WITH_AUDIO=0
for a in "$@"; do [ "$a" = "--with-audio" ] && WITH_AUDIO=1; done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${STORE_NODE_LOG:-$HOME/store-node-deploy.log}"
COMFY_DIR="$HOME/ComfyUI"
TRIPO_DIR="$HOME/TripoSR"
AUDIO_DIR="$HOME/audiogen"
UNITS="$HOME/.config/systemd/user"

c_g="\033[32m"; c_y="\033[33m"; c_b="\033[36m"; c_r="\033[31m"; c_0="\033[0m"
_ts(){ date '+%Y-%m-%d %H:%M:%S'; }
log(){  echo -e "$(_ts) $*" | tee -a "$LOG" >&2; }
info(){ log "${c_b}==>${c_0} $*"; }
ok(){   log "${c_g} ✓${c_0} $*"; }
warn(){ log "${c_y} !${c_0} $*"; }
err(){  log "${c_r} ✗${c_0} $*"; }

# component results, printed as a JSON summary the store parses
declare -A RESULT
set_result(){ RESULT["$1"]="$2"; }

# ── OS GATE ──────────────────────────────────────────────────────────────────
OS_ID=""; OS_PRETTY="unknown"
if [ -r /etc/os-release ]; then . /etc/os-release; OS_ID="${ID:-}"; OS_PRETTY="${PRETTY_NAME:-$NAME}"; fi
case "$(uname -s 2>/dev/null)" in
  Linux) ;;
  *) OS_PRETTY="$(uname -s 2>/dev/null || echo non-Linux)"; OS_ID="non-linux" ;;
esac
OS_OK=0
case "$OS_ID" in
  ubuntu|debian|pop|linuxmint|neon) OS_OK=1 ;;
esac

emit_status(){
  # single parseable line for the store UI
  local svc_line=""
  echo "[NODE-STATUS] {\"os\":\"${OS_PRETTY//\"/}\",\"os_id\":\"$OS_ID\",\"os_ok\":$OS_OK,\"gpu\":\"${RESULT[gpu]:-unknown}\",\"comfyui\":\"${RESULT[comfyui]:-unknown}\",\"video\":\"${RESULT[video]:-unknown}\",\"model3d\":\"${RESULT[model3d]:-unknown}\",\"audio\":\"${RESULT[audio]:-unknown}\",\"lmstudio\":\"${RESULT[lmstudio]:-unknown}\",\"services\":\"${RESULT[services]:-unknown}\",\"guard\":\"${RESULT[guard]:-unknown}\",\"miner\":\"${RESULT[miner]:-unknown}\"}"
}

if [ "$OS_OK" != "1" ]; then
  err "Unsupported OS: $OS_PRETTY"
  err "The Store GPU node MUST run Ubuntu (24.04 recommended)."
  err "Windows/macOS can't autostart the CUDA services (ComfyUI, diffusers, LM Studio headless)"
  err "the way the node needs. Install Ubuntu on the GPU machine, then re-run this."
  set_result gpu na; set_result comfyui na; set_result video na
  set_result model3d na; set_result audio na; set_result lmstudio na; set_result services na
  emit_status
  echo "[DEPLOY-RESULT] {\"ok\":false,\"reason\":\"needs_ubuntu\",\"os\":\"${OS_PRETTY//\"/}\"}"
  exit 2
fi
info "OS: $OS_PRETTY — supported"

# Only use passwordless sudo — never hang waiting for a password over SSH.
APT() {
  if sudo -n true 2>/dev/null; then sudo -n apt-get "$@" 2>>"$LOG"
  else warn "skipping 'apt-get $*' — needs sudo. Enable passwordless sudo for apt, or run node-setup.sh directly on the node."; return 1; fi
}
have(){ command -v "$1" >/dev/null 2>&1; }

# ── GPU / driver ─────────────────────────────────────────────────────────────
check_gpu(){
  if have nvidia-smi; then
    local g; g="$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null | head -1)"
    ok "GPU: $g"; set_result gpu ok
  else
    warn "nvidia-smi not found — install the NVIDIA driver (sudo ubuntu-drivers autoinstall) then reboot."
    set_result gpu missing
  fi
}

# ── system dependencies ──────────────────────────────────────────────────────
setup_system(){
  # python3-dev added: needed to compile PyMCubes/pyopencl from source (3D + miner).
  local pkgs=(python3 python3-venv python3-pip python3-dev git ffmpeg build-essential wget curl jq libgl1 libglib2.0-0)
  local missing=()
  # skip-if-partially-present class: `have()` only sees commands on $PATH, but
  # several pkgs above (python3-venv, python3-pip, python3-dev, build-essential,
  # libgl1, libglib2.0-0) install NO binary — a box can have python3/git/ffmpeg/
  # wget/curl/jq present yet be missing python3-venv, which used to make this
  # skip apt entirely, then every downstream `python3 -m venv` failed silently
  # (script runs under `set -uo pipefail`, not `-e`). Check every pkg the way it
  # can actually be detected: `have` for ones with a same-named binary, `dpkg -s`
  # for the library/dev packages that don't.
  for p in "${pkgs[@]}"; do
    case "$p" in
      python3-venv|python3-pip|python3-dev|build-essential|libgl1|libglib2.0-0)
        dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p") ;;
      *)
        have "$p" || missing+=("$p") ;;
    esac
  done
  if [ "$MODE" != "deploy" ]; then
    [ ${#missing[@]} -eq 0 ] && ok "core system tools present" || warn "missing: ${missing[*]} (run: deploy)"
    return
  fi
  if [ ${#missing[@]} -eq 0 ]; then
    ok "core system tools already present — skipping apt"
    return
  fi
  info "Installing system dependencies (apt): ${missing[*]}…"
  if ! sudo -n true 2>/dev/null; then
    warn "Missing packages (${missing[*]}) need sudo to install. Enable passwordless sudo for apt, or run this on the node: sudo apt-get install -y ${pkgs[*]}"
    return
  fi
  APT update -y >/dev/null 2>&1 || warn "apt update had warnings"
  APT install -y "${pkgs[@]}" >/dev/null 2>&1 && ok "system deps installed" || warn "some apt packages failed — see $LOG"
}

# ── Model storage on a dedicated SSD, SEPARATED BY TYPE ──────────────────────
# Models live in type-separated sibling folders on the SSD:
#   models_llm (LM Studio) | models_image (ComfyUI) | models_video | models_audio | models_3d
# ComfyUI checkpoints are symlinked to models_image. Video/audio/3D use per-type HF_HOME
# (set in the video/audio/3D generation scripts) so their HF weights never mix. LM Studio
# manages models_llm itself. Idempotent. SSD base via STORE_MODELS_SSD or the known mount.
_relink_to_ssd(){
  local src="$1" dst="$2"
  [ -L "$src" ] && { ok "$(basename "$src") already on SSD"; return; }
  mkdir -p "$dst"
  if [ -d "$src" ] && [ -n "$(ls -A "$src" 2>/dev/null)" ]; then
    info "Moving $(basename "$src") → SSD ($dst) …"
    rsync -a "$src"/ "$dst"/ >>"$LOG" 2>&1 && rm -rf "$src" || { warn "relocate $(basename "$src") failed"; return; }
  else
    rm -rf "$src" 2>/dev/null || true
  fi
  ln -s "$dst" "$src" && ok "$(basename "$src") → $dst"
}
relocate_models_to_ssd(){
  local SSD="${STORE_MODELS_SSD:-}"
  [ -z "$SSD" ] && [ -d /media/user/SSD ] && SSD=/media/user/SSD
  if [ -z "$SSD" ]; then
    [ "$MODE" = deploy ] && info "No models SSD (set STORE_MODELS_SSD) — models stay on the system drive"
    return
  fi
  if [ ! -d "$SSD" ]; then warn "models SSD $SSD not mounted — skipping relocation"; return; fi
  [ "$MODE" = check ] && { [ -L "$HOME/ComfyUI/models" ] && ok "models on SSD ($SSD, type-separated)" || warn "models on system drive (SSD at $SSD)"; return; }
  info "Model storage → SSD ($SSD), separated by type …"
  mkdir -p "$SSD"/models_image "$SSD"/models_video/hub "$SSD"/models_audio/hub "$SSD"/models_3d/hub
  [ -d "$HOME/ComfyUI" ] && _relink_to_ssd "$HOME/ComfyUI/models" "$SSD/models_image"
  # HF weights for video/audio/3D are directed by per-type HF_HOME in the generation
  # scripts (STORE_HF_VIDEO/AUDIO/3D) — nothing to symlink here.
}

# ── ComfyUI (image) ──────────────────────────────────────────────────────────
setup_comfyui(){
  if [ "$MODE" = "check" ]; then
    if [ -f "$COMFY_DIR/main.py" ] && [ -x "$COMFY_DIR/venv/bin/python3" ]; then
      local n; n=$(ls "$COMFY_DIR"/models/checkpoints/*.safetensors 2>/dev/null | wc -l)
      ok "ComfyUI installed ($n checkpoint model(s))"; set_result comfyui ok
    else warn "ComfyUI not installed"; set_result comfyui missing; fi
    return
  fi
  info "ComfyUI (image generation)…"
  # skip-if-partially-present class (see ftfy/video-gen fix): torch alone can
  # import even when `pip install -r requirements.txt` died partway on a prior
  # run, which would make a broken install read as "healthy" forever. Check a
  # spread of core ComfyUI requirements, not just torch.
  local healthy=0
  [ -f "$COMFY_DIR/main.py" ] && [ -x "$COMFY_DIR/venv/bin/python3" ] && \
    "$COMFY_DIR/venv/bin/python3" -c "import torch, torchsde, einops, transformers, safetensors, aiohttp" >/dev/null 2>&1 && healthy=1
  if [ "$healthy" = "1" ]; then
    ok "ComfyUI already installed — skipping reinstall (won't touch working packages)"
  else
    if [ ! -f "$COMFY_DIR/main.py" ]; then
      git clone --depth 1 https://github.com/comfyanonymous/ComfyUI "$COMFY_DIR" >>"$LOG" 2>&1 || { err "ComfyUI clone failed"; set_result comfyui failed; return; }
    fi
    [ -x "$COMFY_DIR/venv/bin/python3" ] || python3 -m venv "$COMFY_DIR/venv" >>"$LOG" 2>&1
    "$COMFY_DIR/venv/bin/pip" install -q --upgrade pip >>"$LOG" 2>&1
    "$COMFY_DIR/venv/bin/pip" install -q torch torchvision --index-url https://download.pytorch.org/whl/cu121 >>"$LOG" 2>&1 || warn "torch install had issues"
    "$COMFY_DIR/venv/bin/pip" install -q -r "$COMFY_DIR/requirements.txt" >>"$LOG" 2>&1 || warn "ComfyUI requirements had issues"
  fi
  mkdir -p "$COMFY_DIR/models/checkpoints"
  # start script + ensure helper
  cat > "$HOME/comfyui-start.sh" <<SH
#!/usr/bin/env bash
cd "$COMFY_DIR"
exec "$COMFY_DIR/venv/bin/python3" main.py --listen 0.0.0.0 --port 8188
SH
  chmod +x "$HOME/comfyui-start.sh"
  local n; n=$(ls "$COMFY_DIR"/models/checkpoints/*.safetensors 2>/dev/null | wc -l)
  [ "$n" -eq 0 ] && warn "No SDXL checkpoints in $COMFY_DIR/models/checkpoints — download one (e.g. sdxl_base_1.0.safetensors) to generate images."
  ok "ComfyUI ready ($n checkpoint(s))"; set_result comfyui ok
}

# ── Video (diffusers media stack + store_videogen.py) ────────────────────────
setup_video(){
  local PY="$COMFY_DIR/venv/bin/python3"
  if [ "$MODE" = "check" ]; then
    if [ -x "$PY" ] && "$PY" -c "import torch,diffusers" >/dev/null 2>&1 && [ -f "$HOME/store_videogen.py" ]; then
      ok "Video stack ready ($("$PY" -c 'import diffusers;print("diffusers",diffusers.__version__)' 2>/dev/null))"; set_result video ok
    else warn "Video stack incomplete"; set_result video missing; fi
    return
  fi
  info "Video generation (diffusers) …"
  [ -x "$PY" ] || { err "ComfyUI venv missing — run ComfyUI setup first"; set_result video failed; return; }
  if "$PY" -c "import diffusers,imageio_ffmpeg,ftfy" >/dev/null 2>&1; then
    ok "diffusers media stack already present — skipping reinstall"
  else
    "$PY" -m pip install -q diffusers transformers accelerate imageio imageio-ffmpeg ftfy safetensors >>"$LOG" 2>&1 || warn "diffusers deps had issues"
  fi
  # store_videogen.py ships alongside this script
  if [ -f "$HERE/store_videogen.py" ]; then
    cp "$HERE/store_videogen.py" "$HOME/store_videogen.py" && ok "installed store_videogen.py"
  elif [ -f "$HOME/store_videogen.py" ]; then ok "store_videogen.py present"
  else warn "store_videogen.py not found in bundle"; fi
  set_result video ok
}

# ── 3D (TripoSR + TripoSG) ───────────────────────────────────────────────────
# CRITICAL: never install/upgrade torch here. The 3D venvs REUSE ComfyUI's exact
# torch via a .pth link (--system-site-packages + a path file). Installing a second
# torch would waste GBs and, if versions drift, break ComfyUI. We also use CPU mesh
# extractors (PyMCubes / skimage) so no CUDA toolkit (nvcc) is required on the node.
M3D_TOOLS="$HOME/.openclaw/tools/model3d"
TRIPOSG_DIR="$HOME/TripoSG"

_comfy_site(){ ls -d "$COMFY_DIR"/venv/lib/python*/site-packages 2>/dev/null | head -1; }
_venv_site(){ ls -d "$1"/lib/python*/site-packages 2>/dev/null | head -1; }

install_m3d_scripts(){
  mkdir -p "$M3D_TOOLS"
  if [ -d "$HERE/model3d" ]; then
    cp -f "$HERE/model3d/"*.sh "$M3D_TOOLS/" 2>/dev/null || true
    chmod +x "$M3D_TOOLS/"*.sh 2>/dev/null || true
  fi
}

setup_3d(){
  if [ "$MODE" = "check" ]; then
    if [ -x "$TRIPO_DIR/venv/bin/python3" ]; then ok "TripoSR (3D) installed"; set_result model3d ok
    else warn "TripoSR (3D) not installed"; set_result model3d missing; fi
    return
  fi
  info "3D generation (TripoSR + TripoSG, CPU mesh path — reuses ComfyUI torch) …"
  install_m3d_scripts
  local SITE; SITE="$(_comfy_site)"
  if [ -z "$SITE" ]; then warn "ComfyUI venv not found — set up ComfyUI first"; set_result model3d failed; return; fi

  # ── TripoSR (MIT, fast) ──
  [ -f "$TRIPO_DIR/run.py" ] || git clone --depth 1 https://github.com/VAST-AI-Research/TripoSR "$TRIPO_DIR" >>"$LOG" 2>&1 \
    || { warn "TripoSR clone failed"; set_result model3d failed; return; }
  if [ ! -x "$TRIPO_DIR/venv/bin/python3" ]; then
    python3 -m venv --system-site-packages "$TRIPO_DIR/venv" >>"$LOG" 2>&1   # reuse ComfyUI torch
    echo "$SITE" > "$(_venv_site "$TRIPO_DIR/venv")/zzz_comfyui.pth"
  fi
  # PyMCubes replaces torchmcubes (which needs a CUDA toolkit this node lacks).
  "$TRIPO_DIR/venv/bin/pip" install -q PyMCubes omegaconf einops "transformers==4.35.0" \
      "tokenizers>=0.14,<0.15" "huggingface-hub<0.18" trimesh rembg imageio pillow xatlas moderngl >>"$LOG" 2>&1 \
      || warn "TripoSR deps had issues"
  # CPU marching-cubes patch (idempotent) so it never needs the diso/torchmcubes CUDA build.
  [ -f "$HERE/model3d/triposr_isosurface_pymcubes.py" ] && \
    cp -f "$HERE/model3d/triposr_isosurface_pymcubes.py" "$TRIPO_DIR/tsr/models/isosurface.py"
  ok "TripoSR ready (CPU mesh path; weights download on first use)"; set_result model3d ok

  # ── TripoSG (MIT, higher quality) — diso patched to the CPU extractor ──
  # skip-if-partially-present class: the venv's python3 binary exists the instant
  # `python3 -m venv` finishes inside install_triposg.sh, well before the trailing
  # `pip install -r` of TripoSG's actual requirements — so a failed payload install
  # used to read as "already installed" forever. Gate on the completion marker the
  # install script only writes after that pip install succeeds (set -euo pipefail
  # there means it never reaches the marker line on failure).
  if [ -f "$M3D_TOOLS/install_triposg.sh" ]; then
    if [ -f "$TRIPOSG_DIR/.install_complete" ]; then ok "TripoSG already installed"
    else info "TripoSG (MIT, higher quality) …"
      bash "$M3D_TOOLS/install_triposg.sh" >>"$LOG" 2>&1 && ok "TripoSG ready" || warn "TripoSG install had issues (see log)"
    fi
  fi
}

# ── Audio / music + voice (transformers: MusicGen + MMS-TTS) ─────────────────
# Reuses the ComfyUI venv (transformers/torch already there) — no fragile extra stack.
setup_audio(){
  local PY="$COMFY_DIR/venv/bin/python3"
  if [ "$MODE" = "check" ]; then
    if [ -x "$PY" ] && "$PY" -c "from transformers import MusicgenForConditionalGeneration, VitsModel; import scipy" >/dev/null 2>&1 && [ -f "$HOME/store_audiogen.py" ]; then
      ok "Audio/music + voice ready"; set_result audio ok
    else warn "Audio/music not set up"; set_result audio missing; fi
    return
  fi
  info "Audio / music (MusicGen) + voice (MMS-TTS) …"
  [ -x "$PY" ] || { err "ComfyUI venv missing — run ComfyUI setup first"; set_result audio failed; return; }
  "$PY" -c "import scipy" >/dev/null 2>&1 || "$PY" -m pip install -q scipy >>"$LOG" 2>&1
  # SELF-HEAL: transitive installs can pull a torchaudio built for a DIFFERENT
  # CUDA than torch (e.g. a cu13 wheel against torch+cu121) — transformers'
  # audio path then dies with "libcudart.so: cannot open shared object file".
  # Pin torchaudio to torch's exact version from torch's own CUDA index.
  if ! "$PY" -c "import torch,torchaudio; assert torchaudio.__version__.split('+')[0]==torch.__version__.split('+')[0]" >/dev/null 2>&1; then
    TVER="$("$PY" -c 'import torch;print(torch.__version__.split("+")[0])' 2>/dev/null)"
    CUTAG="$("$PY" -c 'import torch;v=torch.version.cuda;print("cu"+v.replace(".","") if v else "cpu")' 2>/dev/null)"
    if [ -n "$TVER" ] && "$PY" -m pip install -q "torchaudio==$TVER" --index-url "https://download.pytorch.org/whl/${CUTAG:-cu121}" >>"$LOG" 2>&1; then
      ok "torchaudio re-pinned to $TVER (${CUTAG:-cu121}) — CUDA mismatch healed"
    else warn "torchaudio/torch CUDA mismatch could not be auto-healed — see $LOG"; fi
  fi
  if [ -f "$HERE/store_audiogen.py" ]; then cp "$HERE/store_audiogen.py" "$HOME/store_audiogen.py" && ok "installed store_audiogen.py"; fi
  if "$PY" -c "from transformers import MusicgenForConditionalGeneration, VitsModel; import scipy" >/dev/null 2>&1; then
    ok "Audio ready (MusicGen + MMS-TTS models download on first use)"; set_result audio ok
  else warn "audio imports failed — check $LOG"; set_result audio failed; fi
}

# ── LM Studio (LLM) ──────────────────────────────────────────────────────────
setup_lmstudio(){
  local LMS="$HOME/.lmstudio/bin/lms"
  if [ -x "$LMS" ]; then
    ok "LM Studio present ($("$LMS" version 2>/dev/null | head -1 | tr -d '\r'))"; set_result lmstudio ok; return
  fi
  if [ "$MODE" = "check" ]; then warn "LM Studio not installed"; set_result lmstudio missing; return; fi
  # LM Studio ships an OFFICIAL headless CLI installer now — no GUI/xvfb needed.
  info "Installing LM Studio (headless CLI installer) …"
  if curl -fsSL https://lmstudio.ai/install.sh | sh >>"$LOG" 2>&1 && [ -x "$LMS" ]; then
    ok "LM Studio CLI installed ($("$LMS" version 2>/dev/null | head -1 | tr -d '\r'))"
    ok "download models with: $LMS get <publisher/model> --gguf -y"
    set_result lmstudio ok
  else
    warn "headless install failed — fallback: install the AppImage from https://lmstudio.ai, run it once, then enable the CLI. See $LOG"
    set_result lmstudio missing
  fi
}

# ── systemd --user services (autostart) ──────────────────────────────────────
setup_services(){
  if [ "$MODE" = "check" ]; then
    local up=0
    for s in comfyui lmstudio; do systemctl --user is-enabled "$s.service" >/dev/null 2>&1 && up=$((up+1)); done
    [ "$up" -ge 1 ] && ok "$up autostart service(s) enabled" || warn "no autostart services enabled"
    set_result services "$([ "$up" -ge 1 ] && echo ok || echo missing)"
    return
  fi
  info "Autostart services (systemd --user) …"
  mkdir -p "$UNITS"
  # copy any bundled unit templates, substituting the current user's HOME/UID
  if [ -d "$HERE/services" ]; then
    for f in "$HERE/services"/*.service; do
      [ -e "$f" ] || continue
      local name; name="$(basename "$f")"
      # audiogen.service only if audio was set up
      [ "$name" = "audiogen.service" ] && [ "$WITH_AUDIO" != "1" ] && continue
      # jellyminer.service is handled by setup_guard_miner (an existing unit may
      # carry a literal token — never blindly overwritten here)
      [ "$name" = "jellyminer.service" ] && continue
      sed -e "s#__HOME__#$HOME#g" -e "s#__UID__#$(id -u)#g" "$f" > "$UNITS/$name"
      ok "installed $name"
    done
  fi
  systemctl --user daemon-reload 2>>"$LOG"
  for s in comfyui lmstudio; do
    [ -f "$UNITS/$s.service" ] && systemctl --user enable "$s.service" >>"$LOG" 2>&1 && ok "enabled $s.service"
  done
  [ "$WITH_AUDIO" = "1" ] && [ -f "$UNITS/audiogen.service" ] && systemctl --user enable audiogen.service >>"$LOG" 2>&1 && ok "enabled audiogen.service"
  loginctl enable-linger "$USER" >>"$LOG" 2>&1 && ok "linger enabled (services survive logout)" || warn "could not enable linger"
  set_result services ok
}

# ── GPU guard + JellyMiner (queue pause for games; JellyCoin OpenCL miner) ───
# The Store passes STORE_URL + STORE_JELLY_TOKEN in the deploy env; they land in
# ~/.config/store-node.env, which gpu-guard.sh and jellyminer.service both read.
ENVF="$HOME/.config/store-node.env"

_envf_set(){  # _envf_set KEY VALUE — upsert one line, keep the rest
  touch "$ENVF"; chmod 600 "$ENVF"
  if grep -q "^$1=" "$ENVF" 2>/dev/null; then
    sed -i "s#^$1=.*#$1=$2#" "$ENVF"
  else
    echo "$1=$2" >> "$ENVF"
  fi
}

setup_guard_miner(){
  if [ "$MODE" = "check" ]; then
    if [ -x "$HOME/gpu-guard.sh" ] && [ -f "$UNITS/gpu-guard.service" ]; then
      ok "gpu-guard installed"; set_result guard ok
    else warn "gpu-guard not installed"; set_result guard missing; fi
    if [ -f "$UNITS/jellyminer.service" ] && [ -x "$HOME/jellyminer-venv/bin/python" ]; then
      ok "JellyMiner installed"; set_result miner ok
    else warn "JellyMiner not installed"; set_result miner missing; fi
    return
  fi
  info "GPU guard (queue pause for games) + JellyMiner …"
  mkdir -p "$HOME/.config" "$UNITS"
  [ -n "${STORE_URL:-}" ]         && _envf_set STORE_URL "$STORE_URL"
  [ -n "${STORE_JELLY_TOKEN:-}" ] && _envf_set JELLY_TOKEN "$STORE_JELLY_TOKEN"
  # guard script + unit — always refreshed from the bundle (updates flow via deploy)
  if [ -f "$HERE/gpu-guard.sh" ]; then
    install -m 755 "$HERE/gpu-guard.sh" "$HOME/gpu-guard.sh" && ok "installed gpu-guard.sh"
  fi
  # OpenCL runtime + BUILD HEADERS — must be ensured BEFORE the pyopencl pip
  # install below, not after: pyopencl is a source build and needs CL/cl.h at
  # build time. The old order (pip install pyopencl, THEN apt the runtime lib)
  # meant a from-scratch node's first pyopencl build always failed for lack of
  # headers. ocl-icd-libopencl1 alone is only the runtime dispatcher — it never
  # provided the dev headers, so also apt ocl-icd-opencl-dev + opencl-headers.
  local ocl_runtime_ok=0 ocl_headers_ok=0
  ldconfig -p 2>/dev/null | grep -q libOpenCL && ocl_runtime_ok=1
  [ -f /usr/include/CL/cl.h ] && ocl_headers_ok=1
  if [ "$ocl_runtime_ok" != "1" ] || [ "$ocl_headers_ok" != "1" ]; then
    APT install -y ocl-icd-libopencl1 ocl-icd-opencl-dev opencl-headers clinfo >/dev/null 2>&1 || \
      warn "OpenCL runtime/headers missing — install ocl-icd-libopencl1 ocl-icd-opencl-dev opencl-headers for the miner"
  fi
  # miner script + its own venv (OpenCL — deliberately separate from the AI venvs)
  [ -f "$HERE/jellyminer.py" ] && install -m 644 "$HERE/jellyminer.py" "$HOME/jellyminer.py"
  # skip-if-partially-present class: venv/bin/python existing only proves
  # `python3 -m venv` finished, not that the trailing `pip install` of the
  # actual payload (pyopencl/numpy/requests) succeeded. Gate on those imports.
  if [ ! -x "$HOME/jellyminer-venv/bin/python" ] || \
     ! "$HOME/jellyminer-venv/bin/python" -c "import pyopencl, numpy, requests" >/dev/null 2>&1; then
    python3 -m venv "$HOME/jellyminer-venv" >>"$LOG" 2>&1 && \
      "$HOME/jellyminer-venv/bin/pip" install -q pyopencl numpy requests >>"$LOG" 2>&1 && \
      ok "JellyMiner venv ready" || warn "JellyMiner venv install had issues (see $LOG)"
  else
    ok "JellyMiner venv already present"
  fi
  # miner unit: never overwrite an existing one (it may carry a literal token)
  if [ ! -f "$UNITS/jellyminer.service" ]; then
    if grep -q '^JELLY_TOKEN=' "$ENVF" 2>/dev/null && [ -f "$HERE/services/jellyminer.service" ]; then
      sed -e "s#__HOME__#$HOME#g" "$HERE/services/jellyminer.service" > "$UNITS/jellyminer.service"
      ok "installed jellyminer.service (env-file driven)"
    else
      warn "no JELLY_TOKEN yet — grab it from the Store UI (Crypto → JellyCoin → Mining),"
      warn "add 'JELLY_TOKEN=…' to $ENVF, then re-run deploy to install jellyminer.service"
    fi
  else
    ok "jellyminer.service already present (left untouched)"
  fi
  systemctl --user daemon-reload 2>>"$LOG"
  # gpu-guard.service was copied by setup_services; enable + start (idempotent)
  if [ -f "$UNITS/gpu-guard.service" ]; then
    systemctl --user enable gpu-guard.service >>"$LOG" 2>&1
    systemctl --user start  gpu-guard.service >>"$LOG" 2>&1 && ok "gpu-guard running"
    set_result guard ok
  else
    warn "gpu-guard.service missing from bundle"; set_result guard missing
  fi
  if [ -f "$UNITS/jellyminer.service" ]; then
    # enable only — gpu-guard starts/stops the miner around AI work
    systemctl --user enable jellyminer.service >>"$LOG" 2>&1
    set_result miner ok
  else
    set_result miner missing
  fi
}

# ── run ──────────────────────────────────────────────────────────────────────
log "──────────────────────────────────────────────────────────"
info "Store node $MODE starting (audio=$WITH_AUDIO) — log: $LOG"
check_gpu
setup_system
setup_comfyui
relocate_models_to_ssd   # keep HF cache + ComfyUI models on the SSD (frees system drive)
setup_video
setup_3d
setup_audio
setup_lmstudio
setup_services
setup_guard_miner        # gpu-guard + JellyMiner (needs the units dir from setup_services)

log ""
info "Summary:"
for k in gpu comfyui video model3d audio lmstudio services guard miner; do
  v="${RESULT[$k]:-unknown}"
  case "$v" in
    ok) log "  ${c_g}✓${c_0} $k";;
    missing|skipped) log "  ${c_y}!${c_0} $k ($v)";;
    *) log "  ${c_r}✗${c_0} $k ($v)";;
  esac
done
emit_status
FAIL=0; for k in comfyui video lmstudio services; do [ "${RESULT[$k]}" = "failed" ] && FAIL=1; done
echo "[DEPLOY-RESULT] {\"ok\":$([ $FAIL -eq 0 ] && echo true || echo false),\"mode\":\"$MODE\"}"
[ "$MODE" = "deploy" ] && ok "Node $MODE complete."
exit 0

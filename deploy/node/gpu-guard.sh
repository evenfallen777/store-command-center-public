#!/usr/bin/env bash
# gpu-guard — pause the Store's unified AI/GPU queue while this box is used
# interactively (Steam game, Wine/Bottles app, emulator, Blender, OBS, a VM…).
#
# Every POLL seconds it heartbeats the Store (busy=true/false + app names):
#   busy  → Store pauses the orchestrator (in-flight jobs finish, nothing new
#           starts), JellyMiner is stopped, and once the queue drains the
#           loaded models are unloaded so the game gets the full VRAM.
#   quiet RESUME_AFTER seconds → Store resumes, JellyMiner restarts. Models
#           reload on demand (JIT) with the next AI job — nothing else to do.
#
# Runs as systemd user unit gpu-guard.service (installed by node-setup.sh).
# Config comes from ~/.config/store-node.env (written by node-setup.sh):
#   STORE_URL=http://<store-box>:8787     JELLY_TOKEN=<jelly miner token>

[ -f "$HOME/.config/store-node.env" ] && . "$HOME/.config/store-node.env"
STORE_URL="${STORE_URL:-http://127.0.0.1:8787}"
POLL=5                 # seconds between checks
RESUME_AFTER=60        # seconds of quiet before resuming the queue
MINER_START_AFTER=90   # seconds the AI queue must be idle before mining starts
VRAM_MIN_MB=400        # unknown app holding more VRAM than this counts as heavy

# Tdarr coexistence (AI yields to Tdarr): when the local tdarr-node container is
# actively transcoding, hold the Store's AI/GPU queue so no NEW model/job loads
# onto the shared GPU mid-transcode. Gentle by default — in-flight AI finishes and
# models are NOT unloaded (a transcode needs little VRAM). Set TDARR_HARD=1 for the
# full game-style kill+unload handoff instead.
TDARR_CONTAINER="${TDARR_CONTAINER:-tdarr-node}"
TDARR_HARD="${TDARR_HARD:-0}"
DOCKER="$(command -v docker || echo /usr/bin/docker)"

# Steam games (native AND Proton) run under "reaper SteamLaunch AppId=…".
# wineserver covers Bottles/Lutris/plain Wine. The rest are the known heavies.
# OBS is deliberately NOT name-matched — merely being open shouldn't pause the
# queue; when it actually encodes it trips the VRAM threshold below instead.
HEAVY_RE='SteamLaunch AppId=|pressure-vessel|wineserver|blender|retroarch|qemu-system|ryujinx|xemu|dolphin-emu|UltiMaker-Cura|OrcaSlicer'
# GPU processes that are ALLOWED to hold VRAM (the AI stack itself + desktop +
# the idle Steam client UI, whose steamwebhelper holds a few hundred MB):
ALLOW_RE='gnome-shell|Xorg|Xwayland|gnome-remote-desktop|mutter|lm-studio|\.lmstudio|ComfyUI|jellyminer|firefox|steamwebhelper'

# Token: env file first; else parse a literal --token out of jellyminer.service
# (never a ${VAR} placeholder) so the two services can't drift apart.
TOKEN="${JELLY_TOKEN:-}"
[ -n "$TOKEN" ] || TOKEN=$(grep -oP -- '--token \K\S+' \
  ~/.config/systemd/user/jellyminer.service 2>/dev/null | grep -v '^\$' | head -1)
[ -n "$TOKEN" ] || { echo "FATAL: no JELLY_TOKEN in ~/.config/store-node.env and no literal --token in jellyminer.service"; exit 1; }

busy_apps() {
  { # 1) name-based: known interactive heavies by cmdline
    pgrep -fa "$HEAVY_RE" 2>/dev/null | awk '{n=split($2,p,"/"); print p[n]}'
    # 2) generic: any not-allowed process holding real VRAM (type C or G)
    nvidia-smi 2>/dev/null | awk -v min="$VRAM_MIN_MB" -v allow="$ALLOW_RE" \
      '$2 ~ /^[0-9]+$/ && $6 ~ /^(C|G|C\+G)$/ {
         mem=$8; sub(/MiB/,"",mem);
         if (mem+0 >= min && $7 !~ allow) {n=split($7,p,"/"); print p[n]} }'
  } | sort -u | head -6
}

# True while the local Tdarr node is running a transcode (ffmpeg/HandBrake inside
# the container). Container-scoped via `docker top`, so the Store's own ffmpeg or
# any other host process never trips it.
tdarr_transcoding() {
  "$DOCKER" top "$TDARR_CONTAINER" 2>/dev/null | grep -qiE 'ffmpeg|handbrake'
}

post() {  # $1 = true|false, $2 = newline-separated app names
  local apps_json
  apps_json=$(printf '%s\n' "$2" | python3 -c \
    'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))' \
    2>/dev/null) || apps_json='[]'
  curl -sf -m 10 -X POST "$STORE_URL/api/gpu/guard/state" \
    -H "X-Jelly-Token: $TOKEN" -H "Content-Type: application/json" \
    -d "{\"busy\": $1, \"apps\": $apps_json}" >/dev/null
}

echo "gpu-guard up (store: $STORE_URL, poll ${POLL}s, resume after ${RESUME_AFTER}s quiet)"
paused=false; freed=false; idle=0; miner_idle=0; pause_kind=""
while true; do
  apps=$(busy_apps)
  # Tdarr transcoding → AI yields. HARD mode treats it exactly like a game (full
  # kill+unload handoff via the heavy path); gentle (default) just holds the queue
  # through the tdapp branch below so no new model loads mid-transcode.
  tdapp=""
  if [ -z "$apps" ] && tdarr_transcoding; then
    if [ "$TDARR_HARD" = "1" ]; then apps="tdarr-transcode"; else tdapp="tdarr-transcode"; fi
  fi
  if [ -n "$apps" ]; then
    idle=0
    if ! $paused || [ "$pause_kind" != "heavy" ]; then
      echo "heavy app(s): $(echo "$apps" | tr '\n' ' ')— pausing AI queue + miner"
      systemctl --user stop jellyminer 2>/dev/null
      # Snapshot FIRST: this heartbeat makes the Store record what's mid-flight,
      # so the kills below land on jobs the Store knows to auto-resume.
      post true "$apps"
      # Kill switch: abort running GPU jobs for an instant VRAM handoff.
      #  - ComfyUI /interrupt → image jobs (Store re-runs them on resume)
      #  - pkill diffusers    → video segments + audio clips (Store resumes the
      #    chain from its last DONE segment / re-runs the clip on resume)
      # 3D jobs are deliberately NOT killed (no resumable entry point) — they
      # finish naturally and the queue holds afterwards.
      curl -sf -m 5 -X POST http://127.0.0.1:8188/interrupt >/dev/null 2>&1
      pkill -f 'store_videogen\.py|store_audiogen\.py' 2>/dev/null
      paused=true; freed=false; pause_kind="heavy"
    else
      post true "$apps"
    fi
    # Once in-flight AI work drains, unload models so the game gets the VRAM.
    if ! $freed; then
      drained=$(curl -sf -m 10 -H "X-Jelly-Token: $TOKEN" "$STORE_URL/api/gpu/guard/state" |
        python3 -c 'import json,sys; d=json.load(sys.stdin); print("yes" if d.get("llm")=="idle" and not d.get("active_images") else "no")' 2>/dev/null)
      if [ "$drained" = "yes" ]; then
        echo "queue drained — unloading models to free VRAM"
        ~/.lmstudio/bin/lms unload --all >/dev/null 2>&1
        curl -sf -m 10 -X POST http://127.0.0.1:8188/free \
          -H 'Content-Type: application/json' \
          -d '{"unload_models": true, "free_memory": true}' >/dev/null 2>&1
        freed=true
      fi
    fi
  elif [ -n "$tdapp" ]; then
    # Tdarr is transcoding — AI yields (gentle): hold the queue so nothing NEW
    # loads onto the GPU; in-flight AI finishes on its own and models are NOT
    # unloaded (NVENC needs little VRAM). Stop the miner so Tdarr owns the spare
    # GPU. The queue resumes RESUME_AFTER seconds after the transcode ends.
    idle=0
    if ! $paused; then
      echo "tdarr transcoding — holding AI queue (AI yields to Tdarr); stopping miner"
      systemctl --user stop jellyminer 2>/dev/null
      post true "$tdapp"
      paused=true; freed=false; pause_kind="tdarr"
    else
      post true "$tdapp"
    fi
  elif $paused; then
    idle=$((idle + POLL))
    if [ "$idle" -ge "$RESUME_AFTER" ]; then
      echo "quiet ${RESUME_AFTER}s — resuming AI queue (miner returns once the queue is idle)"
      post false ""
      paused=false; freed=false; miner_idle=0; pause_kind=""
    else
      post true ""   # still counting down — keep the queue held
    fi
  else
    post false ""    # steady-state heartbeat so the Store knows the guard is alive
    # ── miner vs AI queue: never mine while the Store is running GPU work ──
    sb=$(curl -sf -m 10 -H "X-Jelly-Token: $TOKEN" "$STORE_URL/api/gpu/guard/state" |
      python3 -c 'import json,sys; print(json.load(sys.stdin).get("store_busy"))' 2>/dev/null)
    if [ "$sb" = "True" ]; then
      miner_idle=0
      if systemctl --user is-active jellyminer >/dev/null 2>&1; then
        echo "AI queue busy — stopping miner"
        systemctl --user stop jellyminer 2>/dev/null
      fi
    elif [ "$sb" = "False" ]; then
      miner_idle=$((miner_idle + POLL))
      if [ "$miner_idle" -ge "$MINER_START_AFTER" ] && \
         ! systemctl --user is-active jellyminer >/dev/null 2>&1; then
        echo "AI queue idle ${MINER_START_AFTER}s — starting miner"
        systemctl --user start jellyminer 2>/dev/null
      fi
    fi   # store unreachable → leave the miner as-is
  fi
  sleep "$POLL"
done

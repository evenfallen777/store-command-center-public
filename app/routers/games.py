"""🎮 Games — game-engine workbench (Godot / Unity / Unreal) on the GPU node.

Everything here runs over the SAME ssh path the rest of the store already uses:
`routers.llm._box_ssh`, which reads the node host/user from config (STORE_GPU_HOST /
STORE_GPU_SSH_USER) — no new ssh plumbing, no hardcoded addresses. The only addition
is `_node_put`, which pipes a file through that same ssh invocation on stdin (base64)
because `_box_ssh` has no stdin channel and asset export moves binaries.

Design rules for this tab:
  * "engine not installed" is a FIRST-CLASS STATE, never an error. Unity and Unreal
    cannot be installed unattended (vendor accounts + sudo), so every missing engine
    comes back with an `install_hint` the UI renders as a copy-paste panel.
  * Nothing here ever raises. Node down / ssh timeout / missing root all degrade to
    an empty payload with `error` set, so a pane can explain itself instead of dying.
  * Reads are TTL-cached (app/cache.py) — each probe is an ssh round-trip.
  * Builds ride the unified queue via orch.submit_llm (a plain callable; no `model`
    and no `task` is passed, so the orchestrator never loads an LLM for them) — the
    request returns a task_id immediately and never blocks.

Endpoints
    GET  /api/games/engines         engine detection (installed/version/path/hint)
    GET  /api/games/projects        discover projects under the configurable root(s)
    POST /api/games/projects        create a minimal Godot project on the node
    POST /api/games/build           queue a headless Godot export
    GET  /api/games/build/{id}      build job status + collected output
    GET  /api/games/assets          sprite sheets / 3D models / asset packs
    POST /api/games/assets/export   copy selected assets into a project
    GET  /api/games/mcp             editor-MCP catalog + what is really deployed
    GET  /api/games/mcp/bridge      live Unreal bridge state (port read from port.json)
    POST /api/games/mcp/settings    model + project used for editor/MCP work
    POST /api/games/mcp/call        one bridge command, THROUGH the unified queue
    POST /api/games/mcp/unity       add/remove the Unity MCP package in one project
    GET  /api/games/notes           free-text scratchpad (settings-backed)
    POST /api/games/notes

The "publish a title to the shop" flow (listing drafts → a WooCommerce DRAFT product)
lives in the sibling module routers/games_publish.py and is mounted onto this router
at the bottom of this file, so main.py keeps including exactly one games router.
"""
import base64
import json
import re
import shlex
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import cache
from config import BASE, DATA_DIR, GPU_HOST, MODELS3D_DIR, MODELS3D_EXTS, BOX_SSH
from db import get_conn
from orchestrator import orch

router = APIRouter()

# Where projects live on the node. Overridable in settings (games_project_root).
DEFAULT_PROJECT_ROOT = "~/games"
# Godot lives here after the 2026-07-19 install; `command -v godot` is tried first,
# this glob is the fallback because a non-interactive ssh may not have ~/.local/bin.
GODOT_GLOB = "$HOME/engines/godot/Godot_v*linux.x86_64"

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,48}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9 _./~-]{1,300}$")
# The project root may be a COMMA-SEPARATED list. Projects legitimately live on more
# than one drive — an Unreal project has to sit on ext4 (its small-file I/O deadlocks
# ntfs-3g), while a 21-project Unity library is far too large to move off one.
_ROOTS_RE = re.compile(r"^[A-Za-z0-9 _./~,-]{1,600}$")


# ─── settings helpers ────────────────────────────────────────────────────────

def _setting(key, default=""):
    try:
        c = get_conn()
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        c.close()
        if row and row["value"] not in (None, ""):
            return row["value"]
    except Exception:
        pass
    return default


def _set_setting(key, value):
    try:
        c = get_conn()
        c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(value)))
        c.commit()
        c.close()
        return True
    except Exception:
        return False


def _project_root():
    """The configured root(s), verbatim — may be a comma-separated list."""
    r = (_setting("games_project_root", "") or DEFAULT_PROJECT_ROOT).strip()
    return r if _ROOTS_RE.match(r) else DEFAULT_PROJECT_ROOT


def _project_roots():
    """Each configured root as its own path. Order preserved, blanks dropped."""
    return [p.strip() for p in _project_root().split(",") if p.strip()] or [DEFAULT_PROJECT_ROOT]


def _node_label():
    """What to call the node in user-visible copy — config-derived, never baked in."""
    return GPU_HOST or "the GPU node"


# ─── ssh (reused from routers.llm) ───────────────────────────────────────────

def _ssh(cmd, timeout=30):
    """Run `cmd` on the node. Delegates to routers.llm._box_ssh (same host/user/flags
    the LLM + node tabs already use); we only prepend ~/.local/bin to PATH because a
    non-interactive ssh does not source the profile that puts `godot` there.

    Returns (rc, output). Never raises — a dead node comes back as (-1, message).
    """
    try:
        from routers.llm import _box_ssh
        return _box_ssh(f'export PATH="$HOME/.local/bin:$PATH"; {cmd}', timeout=timeout)
    except Exception as e:                       # ssh missing, timeout, node down
        return -1, f"ssh failed: {e}"


def _node_put(local: Path, remote: str, timeout=180):
    """Copy one local file to the node through the SAME ssh target as _box_ssh.

    _box_ssh cannot stream stdin, and asset export moves binaries (PNG sheets, STL/GLB),
    so this pipes base64 in on stdin rather than stuffing it into argv (ARG_MAX).
    """
    try:
        data = base64.b64encode(local.read_bytes())
        r = subprocess.run(
            BOX_SSH + [f"mkdir -p {shlex.quote(str(Path(remote).parent))} && "
                       f"base64 -d > {shlex.quote(remote)}"],
            input=data, capture_output=True, timeout=timeout)
        return r.returncode == 0, (r.stderr or b"").decode(errors="replace")[:300]
    except Exception as e:
        return False, str(e)[:300]


# ─── engines ─────────────────────────────────────────────────────────────────

INSTALL_HINTS = {
    "godot": (
        "Godot is a single self-contained binary — no account, no sudo:\n"
        "  mkdir -p ~/engines/godot && cd ~/engines/godot\n"
        "  wget https://github.com/godotengine/godot/releases/download/"
        "4.7.1-stable/Godot_v4.7.1-stable_linux.x86_64.zip\n"
        "  unzip Godot_v4.7.1-stable_linux.x86_64.zip\n"
        "  chmod +x Godot_v4.7.1-stable_linux.x86_64\n"
        "  mkdir -p ~/.local/bin && ln -sf ~/engines/godot/Godot_v4.7.1-stable_linux.x86_64 "
        "~/.local/bin/godot"
    ),
    "unity": (
        "Unity needs a Unity account (personal licence is free) AND sudo on the node — "
        "it cannot be installed unattended from here. Run these ON the node:\n"
        "  sudo sh -c 'echo \"deb https://hub.unity3d.com/linux/repos/deb stable main\" "
        "> /etc/apt/sources.list.d/unityhub.list'\n"
        "  wget -qO - https://hub.unity3d.com/linux/keys/public | gpg --dearmor | "
        "sudo tee /usr/share/keyrings/Unity_Technologies_ApS.gpg > /dev/null\n"
        "  sudo apt update && sudo apt install unityhub\n"
        "Then open Unity Hub once, sign in, and install an Editor version. The old "
        "standalone AppImage download path is dead (404) — the apt repo is the "
        "supported route."
    ),
    "unreal": (
        "Unreal needs an Epic Games account (link it to GitHub for source access) and "
        "roughly 100-110 GB extracted. Check free space before starting — the node is "
        "tight, and filling it would starve the LLM/image model storage. Recommended: "
        "put it on an external drive, or wait until space exists.\n"
        "  1. Sign in at unrealengine.com and link your GitHub account\n"
        "  2. Download the Linux binary release (or clone EpicGames/UnrealEngine)\n"
        "  3. ./Setup.sh && ./GenerateProjectFiles.sh && make"
    ),
}

DOC_LINKS = {
    "godot": "https://docs.godotengine.org/en/stable/",
    "unity": "https://docs.unity3d.com/Manual/index.html",
    "unreal": "https://dev.epicgames.com/documentation/en-us/unreal-engine",
}

_DETECT_SH = f"""
gb=$(command -v godot 2>/dev/null)
[ -z "$gb" ] && gb=$(ls -1 {GODOT_GLOB} 2>/dev/null | head -1)
if [ -n "$gb" ]; then echo "godot|$gb|$("$gb" --version 2>/dev/null | tail -1)"; else echo "godot||"; fi

# Prefer a real EDITOR path over the Hub launcher: the Hub tells us nothing about
# which engine version is installed, and `command -v unityhub` used to win the race
# and report a blank version.
# ASK THE HUB where its editors live. It can relocate installs (Settings -> Installs,
# e.g. onto a roomier drive) and records the chosen root in secondaryInstallPath.json.
# Assuming ~/Unity/Hub/Editor makes Unity read as "not installed" the moment someone
# moves them.
uv=""
uroot=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])))" "$HOME/.config/unityhub/secondaryInstallPath.json" 2>/dev/null)
[ -z "$uroot" ] && uroot="$HOME/Unity/Hub/Editor"
ue_dir=$(ls -1d "$uroot"/*/Editor/Unity 2>/dev/null | sort -V | tail -1)
[ -z "$ue_dir" ] && ue_dir=$(ls -1d $HOME/Unity/Hub/Editor/*/Editor/Unity 2>/dev/null | sort -V | tail -1)
if [ -n "$ue_dir" ]; then
  ub="$ue_dir"; uv=$(basename "$(dirname "$(dirname "$ue_dir")")")
else
  ub=$(command -v unity-editor 2>/dev/null)
  [ -z "$ub" ] && ub=$(command -v unityhub 2>/dev/null)
fi
# Report EVERY installed editor, not just the newest. Keeping several side by side
# is deliberate — a 2022.3 project force-upgrades if opened in Unity 6 — so showing
# only the highest version actively misleads.
uall=$(ls -1d "$uroot"/*/Editor/Unity $HOME/Unity/Hub/Editor/*/Editor/Unity 2>/dev/null \
  | sed -n 's#.*/\\([^/]*\\)/Editor/Unity$#\\1#p' | sort -V -u | paste -sd, -)
if [ -n "$ub" ]; then echo "unity|$ub|$uv|$uall"; else echo "unity||"; fi

# Unreal: ~/engines/UnrealEngine is where a plain Linux binary release lands, and
# it carries NO UE_<ver> path segment — read Build.version instead of parsing the
# path, which is the only reliable source for an unpacked zip install.
eb=$(command -v UnrealEditor 2>/dev/null)
[ -z "$eb" ] && eb=$(ls -1 $HOME/engines/UnrealEngine/Engine/Binaries/Linux/UnrealEditor 2>/dev/null | head -1)
[ -z "$eb" ] && eb=$(ls -1 $HOME/UnrealEngine/Engine/Binaries/Linux/UnrealEditor 2>/dev/null | head -1)
[ -z "$eb" ] && eb=$(ls -1 $HOME/Epic*/UE_*/Engine/Binaries/Linux/UnrealEditor 2>/dev/null | head -1)
if [ -n "$eb" ]; then
  ev=$(echo "$eb" | sed -n 's#.*/UE_\\([^/]*\\)/.*#\\1#p')
  if [ -z "$ev" ]; then
    real=$(readlink -f "$eb")
    ueroot=$(dirname "$(dirname "$(dirname "$(dirname "$real")")")")
    ev=$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print('%d.%d.%d' % (d['MajorVersion'],d['MinorVersion'],d['PatchVersion']))" "$ueroot/Engine/Build/Build.version" 2>/dev/null)
  fi
  echo "unreal|$eb|$ev"
else
  echo "unreal||"
fi

echo "disk|$(df -BG --output=avail $HOME 2>/dev/null | tail -1 | tr -d ' G')|"
""".strip()

ENGINE_META = {
    "godot": {"label": "Godot", "icon": "\U0001f7e6",
              "note": "Open source, no account, ~100 MB. The store can create and build projects."},
    "unity": {"label": "Unity", "icon": "⬛",
              "note": "Needs a Unity account and sudo on the node — install is a manual step."},
    "unreal": {"label": "Unreal", "icon": "\U0001f7ea",
               "note": "Needs an Epic account and ~100 GB. Check free space first."},
}


def _detect_engines():
    """Parse the one-shot detection script into per-engine records. Never raises."""
    rc, out = _ssh(_DETECT_SH, timeout=45)
    engines, disk_free_gb, err = [], None, None
    found = {}
    for line in (out or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) < 2:
            continue
        key, path = parts[0], parts[1].strip()
        version = parts[2].strip() if len(parts) > 2 else ""
        # 4th field (optional): every installed version, comma-separated. Unity is
        # routinely kept multi-version on purpose, so "the newest" is not the answer.
        versions = [v.strip() for v in (parts[3] if len(parts) > 3 else "").split(",") if v.strip()]
        if key == "disk":
            try:
                disk_free_gb = int(path)
            except (TypeError, ValueError):
                disk_free_gb = None
            continue
        if key in ENGINE_META:
            found[key] = (path, version, versions)
    if rc != 0 and not found:
        err = (out or "node unreachable").strip()[:300]
    for key, meta in ENGINE_META.items():
        path, version, versions = found.get(key, ("", "", []))
        installed = bool(path)
        engines.append({
            "key": key,
            "label": meta["label"],
            "icon": meta["icon"],
            "note": meta["note"],
            "installed": installed,
            "version": version or ("" if installed else ""),
            # every version present; `version` stays the newest for compatibility
            "versions": versions or ([version] if version else []),
            "path": path,
            "docs": DOC_LINKS[key],
            "install_hint": "" if installed else INSTALL_HINTS[key],
            "can_build": key == "godot" and installed,
        })
    return {"engines": engines, "node": _node_label(), "disk_free_gb": disk_free_gb,
            "reachable": err is None, "error": err,
            "checked_at": int(time.time())}


@router.get("/api/games/engines")
def games_engines(refresh: int = 0):
    """Per-engine installed/version/path, plus an install_hint when missing.
    Cached 120s — each check is an ssh round-trip."""
    try:
        if refresh:
            cache.invalidate("games:engines")
        return cache.cached("games:engines", 120, _detect_engines)
    except Exception as e:
        return {"engines": [], "node": _node_label(), "reachable": False,
                "error": str(e)[:200], "disk_free_gb": None}


def _godot_bin(engines=None):
    """Path to the godot binary on the node, or "" when it isn't installed."""
    data = engines or games_engines()
    for e in data.get("engines") or []:
        if e["key"] == "godot" and e.get("installed"):
            return e.get("path") or ""
    return ""


# ─── projects ────────────────────────────────────────────────────────────────

def _discover_projects():
    root = _project_root()
    roots = _project_roots()
    # One ssh round-trip for every root: `for r in a b c` reuses the same scan body,
    # so adding a second drive costs no extra connection.
    quoted = " ".join(shlex.quote(r) for r in roots)
    rc, out = _ssh(
        f'seen=0; for r in {quoted}; do r="${{r/#\\~/$HOME}}"; '
        f'if [ ! -d "$r" ]; then continue; fi; seen=1; '
        # depth 5, not 3: a real library nests (e.g. <root>/Unity/Projects/<name>/
        # ProjectSettings/ProjectVersion.txt is 5 deep). PRUNE first — a root like
        # /mnt/projects sits beside SteamLibrary/Software/$RECYCLE.BIN on a 1.4 TB
        # ntfs-3g volume, and walking those through FUSE is what makes this slow.
        f'find "$r" \\( -name node_modules -o -name .git -o -name Library '
        f'-o -name Intermediate -o -name Saved -o -name DerivedDataCache '
        f'-o -name SteamLibrary -o -name Software -o -name \'$RECYCLE.BIN\' '
        f"-o -name 'System Volume Information' -o -name 'VM OS' \\) -prune -o "
        f"-maxdepth 5 \\( -name project.godot -o -name '*.uproject' "
        f"-o -name ProjectVersion.txt \\) -printf '%p|%T@\\n' 2>/dev/null; "
        f'done; [ "$seen" = "0" ] && echo "NOROOT"; exit 0',
        timeout=60)
    if rc != 0:
        return {"projects": [], "root": root, "node": _node_label(), "reachable": False,
                "root_exists": False, "error": (out or "node unreachable").strip()[:300]}
    text = out or ""
    if "NOROOT" in text:
        return {"projects": [], "root": root, "node": _node_label(), "reachable": True,
                "root_exists": False, "error": None}
    projects = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        marker, _, mtime = line.rpartition("|")
        marker = marker.strip()
        p = Path(marker)
        if p.name == "project.godot":
            engine, proj = "godot", p.parent
        elif p.suffix == ".uproject":
            engine, proj = "unreal", p.parent
        elif p.name == "ProjectVersion.txt":
            if p.parent.name != "ProjectSettings":       # only the real Unity marker
                continue
            engine, proj = "unity", p.parent.parent
        else:
            continue
        try:
            mod = int(float(mtime))
        except (TypeError, ValueError):
            mod = 0
        projects.append({"name": proj.name, "engine": engine,
                         "path": str(proj), "modified": mod})
    projects.sort(key=lambda x: -x["modified"])
    return {"projects": projects, "root": root, "node": _node_label(),
            "reachable": True, "root_exists": True, "error": None}


@router.get("/api/games/projects")
def games_projects(refresh: int = 0):
    """Projects found under the configurable root (settings key games_project_root).
    project.godot -> godot, *.uproject -> unreal, ProjectSettings/ProjectVersion.txt
    -> unity. Cached 30s."""
    try:
        if refresh:
            cache.invalidate("games:projects")
        return cache.cached("games:projects", 30, _discover_projects)
    except Exception as e:
        return {"projects": [], "root": _project_root(), "node": _node_label(),
                "reachable": False, "root_exists": False, "error": str(e)[:200]}


PROJECT_GODOT_TMPL = """; Godot project generated by the Store's Games tab.
config_version=5

[application]

config/name="{name}"
run/main_scene="res://main.tscn"
config/features=PackedStringArray("4.3", "GL Compatibility")

[display]

window/size/viewport_width=640
window/size/viewport_height=360
window/stretch/mode="viewport"

[rendering]

renderer/rendering_method="gl_compatibility"
textures/canvas_textures/default_texture_filter=0
"""

MAIN_TSCN_TMPL = """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://main.gd" id="1"]

[node name="Main" type="Node2D"]
script = ExtResource("1")

[node name="Camera2D" type="Camera2D" parent="."]
position = Vector2(320, 180)
"""

MAIN_GD_TMPL = """extends Node2D

# Starter scene for "{name}" — created by the Store's Games tab.
# Assets exported from the store land in res://assets/ (sprite sheets ship with a
# matching .json describing frame size and frame count).

func _ready() -> void:
\tprint("{name} ready")
"""


@router.post("/api/games/projects")
async def games_create_project(request: Request):
    """Create a minimal Godot project on the node (project.godot + starter scene).
    Godot only — Unity/Unreal return a clear not-installed message instead."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    engine = (body.get("engine") or "godot").strip().lower()
    name = (body.get("name") or "").strip()

    if engine != "godot":
        meta = ENGINE_META.get(engine)
        return JSONResponse({
            "error": f"{meta['label'] if meta else engine} is not installed on {_node_label()}, "
                     f"so the store can't scaffold a project for it. Install it first "
                     f"(see the Engines pane) — only Godot projects can be created from here.",
            "engine": engine,
            "install_hint": INSTALL_HINTS.get(engine, ""),
        }, status_code=400)
    if not _NAME_RE.match(name):
        return JSONResponse({"error": "Project name must be 1-49 chars: letters, numbers, "
                                      "spaces, dashes or underscores."}, status_code=400)
    if not _godot_bin():
        return JSONResponse({"error": f"Godot is not installed on {_node_label()} — "
                                      f"see the Engines pane for the install commands.",
                             "install_hint": INSTALL_HINTS["godot"]}, status_code=400)

    folder = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "project"
    root = _project_root()
    proj = f"{root}/{folder}"
    files = {
        "project.godot": PROJECT_GODOT_TMPL.format(name=name),
        "main.tscn": MAIN_TSCN_TMPL,
        "main.gd": MAIN_GD_TMPL.format(name=name),
        "assets/.gdignore_placeholder": "# store asset exports land here\n",
    }
    # base64 so nothing in the templates can break out of the shell command.
    parts = [f'r={shlex.quote(proj)}; r="${{r/#\\~/$HOME}}"',
             'if [ -e "$r/project.godot" ]; then echo EXISTS; exit 0; fi',
             'mkdir -p "$r/assets"']
    for rel, content in files.items():
        b64 = base64.b64encode(content.encode()).decode()
        parts.append(f'echo {b64} | base64 -d > "$r/{rel}"')
    parts.append('echo OK; echo "$r"')
    rc, out = _ssh(" && ".join(parts), timeout=40)
    if "EXISTS" in (out or ""):
        return JSONResponse({"error": f"A project already exists at {proj}."}, status_code=400)
    if rc != 0 or "OK" not in (out or ""):
        return JSONResponse({"error": f"Could not create the project on {_node_label()}: "
                                      f"{(out or 'node unreachable').strip()[:200]}"},
                            status_code=502)
    cache.invalidate("games:projects")
    return {"ok": True, "name": name, "engine": "godot", "path": proj,
            "note": "Created with a starter scene. Export assets into res://assets/ "
                    "from the Assets pane."}


# ─── builds (unified queue) ──────────────────────────────────────────────────

_BUILDS: dict = {}          # task_id -> {status, output, project, started, finished}
_BUILDS_MAX = 40


def _record(tid, **kw):
    b = _BUILDS.setdefault(tid, {})
    b.update(kw)
    if len(_BUILDS) > _BUILDS_MAX:                     # keep the dict bounded
        for old in sorted(_BUILDS, key=lambda k: _BUILDS[k].get("started", 0))[:-_BUILDS_MAX]:
            _BUILDS.pop(old, None)


@router.post("/api/games/build")
async def games_build(request: Request):
    """Queue a headless Godot export. Returns a task_id immediately — the export runs
    on the unified queue (orch.submit_llm with a plain callable; no model and no task
    key is passed, so the orchestrator never loads an LLM for it) and output is
    collected into a build record polled via /api/games/build/{task_id}."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    path = (body.get("path") or "").strip()
    preset = (body.get("preset") or "Linux/X11").strip()
    if not path or not _PATH_RE.match(path):
        return JSONResponse({"error": "A valid project path is required."}, status_code=400)
    if len(preset) > 60 or any(c in preset for c in ';|&$`"\\'):
        return JSONResponse({"error": "Invalid export preset name."}, status_code=400)
    godot = _godot_bin()
    if not godot:
        return JSONResponse({"error": f"Godot is not installed on {_node_label()} — "
                                      f"builds are unavailable until it is.",
                             "install_hint": INSTALL_HINTS["godot"]}, status_code=400)

    name = Path(path).name
    out_dir = f"{path}/build"
    out_file = f"{out_dir}/{re.sub(r'[^A-Za-z0-9_-]+', '_', name) or 'game'}.x86_64"

    def _work():
        cmd = (f'p={shlex.quote(path)}; p="${{p/#\\~/$HOME}}"; '
               f'if [ ! -f "$p/project.godot" ]; then echo "NOT_A_GODOT_PROJECT"; exit 2; fi; '
               f'if [ ! -f "$p/export_presets.cfg" ]; then echo "NO_EXPORT_PRESETS"; exit 3; fi; '
               f'mkdir -p "$p/build" && '
               f'{shlex.quote(godot)} --headless --path "$p" '
               f'--export-release {shlex.quote(preset)} "$p/build/{Path(out_file).name}" 2>&1 | tail -80')
        rc, out = _ssh(cmd, timeout=900)
        text = (out or "").strip()
        if "NOT_A_GODOT_PROJECT" in text:
            text = (f"{path} has no project.godot — it isn't a Godot project.")
        elif "NO_EXPORT_PRESETS" in text:
            text = ("This project has no export_presets.cfg yet. Open it once in the Godot "
                    "editor and add an export preset (Project → Export → Add…), "
                    "then build from here. Headless Godot cannot invent a preset.")
        ok = rc == 0 and "NOT_A_GODOT_PROJECT" not in (out or "") and "NO_EXPORT_PRESETS" not in (out or "")
        return {"ok": ok, "rc": rc, "output": text[-6000:], "artifact": out_file if ok else ""}

    def _wrapped():
        _record(tid_holder["id"], status="running")
        try:
            res = _work()
        except Exception as e:                          # a build must never poison the queue
            res = {"ok": False, "rc": -1, "output": str(e)[:2000], "artifact": ""}
        _record(tid_holder["id"], status=("done" if res["ok"] else "failed"),
                output=res["output"], artifact=res["artifact"], finished=time.time())
        return res

    tid_holder = {"id": 0}
    tid = orch.submit_llm(_wrapped, desc=f"Godot build: {name[:40]}", priority=1,
                          source="games")
    tid_holder["id"] = tid
    _record(tid, status="queued", project=path, preset=preset, output="",
            artifact="", started=time.time(), finished=0)
    return {"ok": True, "task_id": tid, "project": path, "preset": preset,
            "note": "Queued on the unified queue — watch progress here or in the queue panel."}


@router.get("/api/games/build/{task_id}")
def games_build_status(task_id: int):
    """Status + collected output for a queued build. Falls back to the orchestrator's
    own view while the job is still pending."""
    rec = dict(_BUILDS.get(task_id) or {})
    try:
        q = orch.poll(task_id)
    except Exception:
        q = {"status": "unknown"}
    if not rec:
        rec = {"status": q.get("status", "unknown"), "output": "", "artifact": ""}
    rec["queue_status"] = q.get("status")
    rec["task_id"] = task_id
    return rec


# ─── assets ──────────────────────────────────────────────────────────────────

def _entity_assets():
    """Generated entity sprite sheets, straight from the sprite registry."""
    out = []
    try:
        import world_sprites
        for eid, rec in (world_sprites.index() or {}).items():
            for action, meta in (rec.get("sheets") or {}).items():
                out.append({
                    "kind": "sprite",
                    "id": f"{eid}:{action}",
                    "name": f"{eid} — {action}",
                    "entity": eid, "action": action,
                    "url": meta.get("url", ""),
                    "frames": meta.get("frames"), "fw": meta.get("fw"), "fh": meta.get("fh"),
                    "source": meta.get("source", "generated"),
                })
    except Exception:
        pass
    return out


def _model_assets():
    """3D models the store has generated/collected (models3d/)."""
    out = []
    try:
        exts = tuple(MODELS3D_EXTS) if MODELS3D_EXTS else (".stl", ".obj", ".glb")
        for sub in ("generated", "assets", "hero", "backlog"):
            d = Path(MODELS3D_DIR) / sub
            if not d.is_dir():
                continue
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix.lower() in exts:
                    out.append({"kind": "model3d", "id": f"{sub}/{f.name}", "name": f.name,
                                "folder": sub, "size_kb": round(f.stat().st_size / 1024, 1)})
        out = out[:400]
    except Exception:
        pass
    return out


def _pack_assets():
    """The downloaded pixel-art packs (static/world_assets/packs/index.json)."""
    try:
        idx = json.loads((BASE / "static" / "world_assets" / "packs" / "index.json").read_text())
        packs = idx.get("packs") or []
        return [{"kind": "pack", "id": p.get("path") or p.get("name"), "name": p.get("name"),
                 "png_count": p.get("png_count"), "license": p.get("license"),
                 "commercial": p.get("commercial"), "source": p.get("source"),
                 "theme": p.get("theme") or []} for p in packs], idx.get("tile_size")
    except Exception:
        return [], None


@router.get("/api/games/assets")
def games_assets():
    """Everything the store can hand to an engine: entity sprite sheets, 3D models,
    and the asset packs. Purely local — works even with the node down."""
    try:
        packs, tile_size = _pack_assets()
        sprites = _entity_assets()
        models = _model_assets()
        return {"sprites": sprites, "models": models, "packs": packs,
                "tile_size": tile_size,
                "counts": {"sprites": len(sprites), "models": len(models), "packs": len(packs)}}
    except Exception as e:
        return {"sprites": [], "models": [], "packs": [], "tile_size": None,
                "counts": {"sprites": 0, "models": 0, "packs": 0}, "error": str(e)[:200]}


def _local_of(asset):
    """Map an asset record back to a file on this box. Returns (Path|None, remote_name)."""
    kind = asset.get("kind")
    if kind == "sprite":
        url = asset.get("url") or ""
        m = re.search(r"world_assets/entities/([^/]+)/([^/?]+)$", url)
        if not m:
            return None, ""
        p = BASE / "static" / "world_assets" / "entities" / m.group(1) / m.group(2)
        return (p if p.is_file() else None), f"{m.group(1)}_{m.group(2)}"
    if kind == "model3d":
        rel = (asset.get("id") or "").strip("/")
        if ".." in rel or not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_. -]+$", rel):
            return None, ""
        p = Path(MODELS3D_DIR) / rel
        return (p if p.is_file() else None), Path(rel).name
    return None, ""


@router.post("/api/games/assets/export")
async def games_assets_export(request: Request):
    """Copy selected assets into a project's assets/ folder on the node.

    Sprite sheets go over as PNG plus a sidecar .json describing frame size/count so
    the engine can slice them; 3D models go as-is. Purely additive — nothing on the
    node is ever deleted or overwritten outside the target assets/ folder.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    project = (body.get("project") or "").strip()
    assets = body.get("assets") or []
    if not project or not _PATH_RE.match(project):
        return JSONResponse({"error": "A valid project path is required."}, status_code=400)
    if not isinstance(assets, list) or not assets:
        return JSONResponse({"error": "Select at least one asset to export."}, status_code=400)
    if len(assets) > 100:
        return JSONResponse({"error": "Export at most 100 assets at a time."}, status_code=400)

    # Resolve ~ once on the node so _node_put gets an absolute destination.
    rc, home = _ssh('echo "$HOME"', timeout=15)
    if rc != 0:
        return JSONResponse({"error": f"{_node_label()} is unreachable — "
                                      f"can't export right now."}, status_code=502)
    base_dir = project.replace("~", (home or "").strip(), 1) if project.startswith("~") else project
    dest_dir = f"{base_dir}/assets"
    _ssh(f"mkdir -p {shlex.quote(dest_dir)}", timeout=15)

    exported, skipped = [], []
    for a in assets:
        if not isinstance(a, dict):
            skipped.append({"asset": str(a)[:60], "why": "malformed"})
            continue
        local, remote_name = _local_of(a)
        if not local:
            skipped.append({"asset": (a.get("id") or "?")[:60], "why": "not a local file"})
            continue
        ok, err = _node_put(local, f"{dest_dir}/{remote_name}")
        if not ok:
            skipped.append({"asset": remote_name, "why": err or "copy failed"})
            continue
        exported.append(remote_name)
        if a.get("kind") == "sprite":
            side = json.dumps({"sheet": remote_name, "frames": a.get("frames"),
                               "frame_width": a.get("fw"), "frame_height": a.get("fh"),
                               "entity": a.get("entity"), "action": a.get("action"),
                               "source": "store"}, indent=2)
            b64 = base64.b64encode(side.encode()).decode()
            _ssh(f"echo {b64} | base64 -d > {shlex.quote(dest_dir + '/' + Path(remote_name).stem + '.json')}",
                 timeout=20)
    return {"ok": bool(exported), "exported": exported, "skipped": skipped,
            "dest": dest_dir,
            "note": f"{len(exported)} asset(s) copied into {dest_dir}. Nothing was deleted."}


# ─── editor MCP (informational only) ─────────────────────────────────────────

MCP_OPTIONS = [
    {"key": "godot-mcp", "engine": "godot", "label": "Godot MCP",
     "what": "Launches the editor, runs projects and captures debug output. Needs NO "
             "editor plugin and no bridge port — it drives the Godot CLI directly, so "
             "GODOT_PATH is the entire configuration.",
     "docs": "https://github.com/Coding-Solo/godot-mcp",
     "install": "npm install --prefix ~/engines/godot-mcp @coding-solo/godot-mcp   "
                "(then ~/.local/bin/godot-mcp wraps it with GODOT_PATH set)"},
    {"key": "unity-mcp", "engine": "unity", "label": "Unity MCP",
     "what": "Bridge package + MCP server for the Unity editor (scene/asset/console access). "
             "MIT, free — Unity's OWN MCP requires a paid Unity AI subscription.",
     "docs": "https://github.com/CoplayDev/unity-mcp",
     "install": "Package Manager → add from git URL "
                "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main, then "
                "Window → MCP for Unity → Configure All Detected Clients. "
                "(Repo moved from justinpbarnett/unity-mcp.)"},
    {"key": "unreal-mcp", "engine": "unreal", "label": "Unreal MCP",
     "what": "C++ WebSocket bridge plugin + Node MCP server driving the Unreal editor. "
             "The only option with stated Linux support (UE 5.6+).",
     "docs": "https://github.com/db-lyon/ue-mcp",
     "install": "npx ue-mcp init  (or ue-mcp-deploy <project> for the plugin only), then "
                "build with Engine/Build/BatchFiles/Linux/Build.sh — NOT `ue-mcp build`, "
                "which passes -Project=\"...\" with literal quotes and fails on Linux."},
]

# ── live bridge (Unreal / ue-mcp) ────────────────────────────────────────────
# The bridge port is NOT fixed. ue-mcp derives it per-worktree from the project
# path and publishes the bound port to <Project>/Saved/UE_MCP_Bridge/port.json;
# the Node client still defaults to 9877 and does NOT read that file. So we read
# it ourselves — otherwise everything looks dead while the editor is fine.
BRIDGE_PORT_REL = "Saved/UE_MCP_Bridge/port.json"

_BRIDGE_JS = r"""
import fs from "fs";
const [,, port, method, paramsJson] = process.argv;
const ws = new WebSocket(`ws://127.0.0.1:${port}`);
const t = setTimeout(() => { console.log(JSON.stringify({error:"timeout"})); process.exit(1); }, 30000);
ws.onopen = () => ws.send(JSON.stringify({jsonrpc:"2.0", id:1, method, params: JSON.parse(paramsJson || "{}")}));
ws.onmessage = (e) => { clearTimeout(t); console.log(String(e.data)); process.exit(0); };
ws.onerror = (e) => { clearTimeout(t); console.log(JSON.stringify({error: String(e.message || "socket error")})); process.exit(1); };
"""


def _mcp_model() -> str:
    """Model for editor/MCP work. Blank = orchestrator default. Kept separate
    because the keep-warm default does not fit beside an Unreal editor."""
    return (_setting("games_mcp_model", "") or "").strip()


def _bridge_status(project: str) -> dict:
    """Read the bridge's published port.json and confirm something is listening.
    Never raises — a dead bridge is a state, not an error."""
    if not project or not _PATH_RE.match(project):
        return {"running": False, "port": 0, "error": "no project path"}
    rc, out = _ssh(
        f'p={shlex.quote(project)}; p="${{p/#\\~/$HOME}}"; '
        f'f="$p/{BRIDGE_PORT_REL}"; '
        f'if [ ! -f "$f" ]; then echo "NOPORTFILE"; exit 0; fi; cat "$f"; '
        f'echo "---"; ss -ltn 2>/dev/null | grep -c ":$(python3 -c '
        f'"import json;print(json.load(open(\'$f\'))[\'port\'])" 2>/dev/null) "',
        timeout=20)
    text = (out or "").strip()
    if rc != 0:
        return {"running": False, "port": 0, "error": (text or "node unreachable")[:200]}
    if "NOPORTFILE" in text:
        return {"running": False, "port": 0,
                "error": "no port.json — the editor has not started the bridge yet"}
    head, _, tail = text.partition("---")
    try:
        info = json.loads(head.strip())
        port = int(info.get("port") or 0)
    except Exception:
        return {"running": False, "port": 0, "error": "unreadable port.json"}
    listening = tail.strip().endswith("1") or tail.strip() == "1"
    return {"running": bool(port and listening), "port": port,
            "pid": info.get("pid"), "started_at": info.get("startedAt"),
            "error": None if listening else "port.json exists but nothing is listening "
                                            "(stale file from a closed editor)"}


def _mcp_deployed() -> dict:
    """Which editor-MCP integrations are actually deployed ON THE NODE.

    The original check only looked at Store-box paths (BASE/plugins, ~/…), which
    can never be true — every engine and every project lives on the node, so a
    working bridge still reported "not present". One ssh probe, 20s TTL."""
    def _probe():
        proj = (_setting("games_mcp_project", "") or "").strip()
        pq = shlex.quote(proj) if proj else "''"
        rc, out = _ssh(
            f'p={pq}; p="${{p/#\\~/$HOME}}"; '
            # Unreal: the compiled bridge module, not just the source drop
            f'if [ -n "$p" ] && ls "$p"/Plugins/UE_MCP_Bridge/Binaries/Linux/*UE_MCP_Bridge*.so '
            f'>/dev/null 2>&1; then echo "unreal-mcp|built"; '
            f'elif [ -n "$p" ] && [ -d "$p/Plugins/UE_MCP_Bridge" ]; then echo "unreal-mcp|source-only"; '
            f'else echo "unreal-mcp|"; fi; '
            # Unity: the CoplayDev package referenced from any project manifest
            f'u=$(grep -rl "com.coplaydev.unity-mcp\\|CoplayDev/unity-mcp" '
            f'/mnt/projects/Unity/Projects/*/Packages/manifest.json 2>/dev/null | head -1); '
            f'if [ -n "$u" ]; then echo "unity-mcp|$u"; else echo "unity-mcp|"; fi; '
            # Godot has no plugin and no bridge port — it drives the CLI, so "installed"
            # simply means the server package plus a resolvable godot binary.
            f'if [ -x "$HOME/engines/godot-mcp/node_modules/.bin/godot-mcp" ]; then '
            f'echo "godot-mcp|$(command -v godot 2>/dev/null || echo no-godot)"; '
            f'else echo "godot-mcp|"; fi',
            timeout=25)
        found = {}
        if rc == 0:
            for line in (out or "").splitlines():
                k, _, v = line.strip().partition("|")
                if k:
                    found[k] = v
        return found
    try:
        return cache.cached("games:mcp:deployed", 20, _probe)
    except Exception:
        return {}


def _bridge_call_now(project: str, method: str, params: dict) -> dict:
    """One JSON-RPC round-trip to the editor bridge. Runs ON the node (the bridge
    binds 127.0.0.1 only, so this is never reachable across the LAN)."""
    st = _bridge_status(project)
    if not st.get("running"):
        return {"ok": False, "error": st.get("error") or "bridge not running",
                "bridge": st}
    cmd = (f"cat > /tmp/.store_mcp_call.mjs <<'JSEOF'\n{_BRIDGE_JS}\nJSEOF\n"
           f"node /tmp/.store_mcp_call.mjs {st['port']} {shlex.quote(method)} "
           f"{shlex.quote(json.dumps(params or {}))}")
    rc, out = _ssh(cmd, timeout=60)
    text = (out or "").strip()
    try:
        reply = json.loads(text.splitlines()[-1]) if text else {}
    except Exception:
        return {"ok": False, "error": f"unparseable bridge reply: {text[:300]}",
                "bridge": st}
    if isinstance(reply, dict) and reply.get("error"):
        return {"ok": False, "error": reply["error"], "bridge": st}
    return {"ok": rc == 0, "result": reply.get("result", reply), "bridge": st}


@router.get("/api/games/mcp")
def games_mcp():
    """Informational: what editor-MCP options exist and whether anything is already
    present/configured here. This endpoint NEVER installs or connects anything —
    it only looks."""
    try:
        eng = {e["key"]: e for e in (games_engines().get("engines") or [])}
        deployed = _mcp_deployed()
        opts = []
        for o in MCP_OPTIONS:
            configured = (_setting(f"games_mcp_{o['key']}", "") or "").strip()
            # Look (read-only) for a local checkout / node_modules copy.
            present = False
            for cand in (BASE / "plugins" / o["key"], Path.home() / o["key"],
                         BASE / "tools" / o["key"]):
                try:
                    if cand.exists():
                        present = True
                        break
                except Exception:
                    pass
            e = eng.get(o["engine"]) or {}
            node_state = (deployed.get(o["key"]) or "").strip()
            if node_state:
                present = True
            status = ("built — bridge plugin compiled" if node_state == "built" else
                      "deployed, NOT built (run Build.sh)" if node_state == "source-only" else
                      "installed, but no godot binary found" if node_state == "no-godot" else
                      ("installed — server ready" if o["key"] == "godot-mcp" else
                       "deployed on the node") if node_state else
                      "configured" if configured else
                      "found locally, not configured" if present else
                      "not present")
            opts.append({**o,
                         "engine_installed": bool(e.get("installed")),
                         "detected_locally": present,
                         "node_state": node_state,
                         "configured": bool(configured),
                         "status": status})
        guard = {}
        try:
            from routers.gpu_guard import guard_info, _flag as _gflag
            guard = {**guard_info(), "editor_mode": _gflag("gpu_guard_editor_mode"),
                     "guard_enabled": _gflag("gpu_guard_enabled")}
        except Exception:
            pass
        return {"options": opts, "node": _node_label(),
                "mcp_model": _mcp_model(),
                "mcp_project": _setting("games_mcp_project", ""),
                "guard": guard,
                "store_mcp": "The Store already exposes its own API over MCP at /api/mcp — "
                             "that is separate from an editor bridge.",
                "note": "Nothing here is installed or connected automatically. Pick an option, "
                        "follow its docs, then set the matching setting to record it."}
    except Exception as e:
        return {"options": [], "node": _node_label(), "error": str(e)[:200],
                "store_mcp": "", "note": "", "mcp_model": "", "guard": {}}


@router.get("/api/games/mcp/bridge")
def games_mcp_bridge(project: str = ""):
    """Live state of the Unreal editor bridge for `project` (or the saved default).
    Reads the port from port.json — the bridge port is derived per-worktree and is
    NOT the 9877 the Node client assumes."""
    try:
        proj = (project or _setting("games_mcp_project", "")).strip()
        if not proj:
            return {"running": False, "port": 0, "project": "",
                    "error": "no project set — POST /api/games/mcp/settings first"}
        return {**_bridge_status(proj), "project": proj, "model": _mcp_model()}
    except Exception as e:
        return {"running": False, "port": 0, "project": "", "error": str(e)[:200]}


@router.post("/api/games/mcp/settings")
async def games_mcp_settings(request: Request):
    """Set the model used for editor/MCP work and the default MCP project.

    The model matters: while an engine editor holds the GPU, co-op mode keeps the
    queue running instead of pausing it — so whatever is named here has to FIT
    beside the editor. The orchestrator default (12B) does not, next to Unreal,
    on a 12 GB card. Blank = orchestrator default."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    out = {}
    if "model" in body:
        model = str(body.get("model") or "").strip()[:120]
        if model and not re.match(r"^[A-Za-z0-9 ._:/@-]{1,120}$", model):
            return JSONResponse({"error": "Invalid model name."}, status_code=400)
        _set_setting("games_mcp_model", model)
        out["model"] = model
    if "project" in body:
        proj = str(body.get("project") or "").strip()
        if proj and not _PATH_RE.match(proj):
            return JSONResponse({"error": "Project must be a plain path."}, status_code=400)
        _set_setting("games_mcp_project", proj)
        out["project"] = proj
    return {"ok": True, **out, "model_effective": _mcp_model() or "(orchestrator default)"}


# ── Unity MCP: install into a project, on demand, from the Projects pane ─────
# Unity's MCP is a git package dependency — installing it EDITS the project's
# Packages/manifest.json. That is a real modification to a real project, so it
# never happens implicitly: the UI offers it per-project and nothing moves until
# someone clicks. Every write takes a timestamped backup first, and it is fully
# reversible via action="remove".
UNITY_MCP_PKG = "com.coplaydev.unity-mcp"
UNITY_MCP_URL = "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main"

# Plain (non-f) string — it is full of JSON braces, so it must never be an f-string.
_UNITY_MCP_PY = r'''
import json, os, shutil, sys, time
proj, action, pkg, url = sys.argv[1:5]
mf = os.path.join(proj, "Packages", "manifest.json")
if not os.path.isfile(mf):
    print(json.dumps({"ok": False, "error": "no Packages/manifest.json - not a Unity project"}))
    sys.exit(0)
try:
    with open(mf, encoding="utf-8") as fh:
        data = json.load(fh)
except Exception as ex:
    print(json.dumps({"ok": False, "error": "manifest.json is not valid JSON: %s" % ex}))
    sys.exit(0)
deps = data.setdefault("dependencies", {})
present = pkg in deps
if action == "install" and present and deps.get(pkg) == url:
    print(json.dumps({"ok": True, "changed": False, "note": "already installed"}))
    sys.exit(0)
if action == "remove" and not present:
    print(json.dumps({"ok": True, "changed": False, "note": "not installed"}))
    sys.exit(0)
bak = mf + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(mf, bak)
if action == "install":
    deps[pkg] = url
elif action == "remove":
    deps.pop(pkg, None)
else:
    print(json.dumps({"ok": False, "error": "unknown action"}))
    sys.exit(0)
with open(mf, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
print(json.dumps({"ok": True, "changed": True, "backup": bak, "manifest": mf, "action": action}))
'''


@router.post("/api/games/mcp/unity")
async def games_mcp_unity(request: Request):
    """Add (or remove) the Unity MCP package in ONE named project.

    Deliberately explicit and per-project: this rewrites that project's
    Packages/manifest.json. A timestamped .bak- copy is taken before every write,
    and action="remove" undoes it. Unity resolves the git dependency the next time
    the project is opened."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    project = (body.get("project") or "").strip()
    action = (body.get("action") or "install").strip().lower()
    if not project or not _PATH_RE.match(project):
        return JSONResponse({"error": "A valid project path is required."}, status_code=400)
    if action not in ("install", "remove"):
        return JSONResponse({"error": "action must be 'install' or 'remove'."}, status_code=400)

    cmd = ("cat > /tmp/.store_unity_mcp.py <<'STOREPYEOF'\n" + _UNITY_MCP_PY + "\nSTOREPYEOF\n"
           + "python3 /tmp/.store_unity_mcp.py "
           + shlex.quote(project) + " " + shlex.quote(action) + " "
           + shlex.quote(UNITY_MCP_PKG) + " " + shlex.quote(UNITY_MCP_URL))
    rc, out = _ssh(cmd, timeout=45)
    text = (out or "").strip()
    if rc != 0:
        return JSONResponse({"error": f"node unreachable or write failed: {text[:300]}"},
                            status_code=502)
    try:
        res = json.loads(text.splitlines()[-1])
    except Exception:
        return JSONResponse({"error": f"unreadable node reply: {text[:300]}"}, status_code=502)
    if not res.get("ok"):
        return JSONResponse({"error": res.get("error") or "install failed"}, status_code=400)
    cache.invalidate("games:mcp:deployed")
    res["package"] = UNITY_MCP_PKG
    res["next"] = ("Open this project in Unity once — the Editor resolves the git package on "
                   "load, then use Window → MCP for Unity → Configure All Detected Clients."
                   if action == "install" else
                   "Package reference removed. Unity drops it on next open.")
    return res


@router.post("/api/games/mcp/call")
async def games_mcp_call(request: Request):
    """Run ONE editor-bridge command through the unified queue.

    This is the point of the whole thing: MCP editor work is GPU-adjacent work on
    the same node, so it serializes with everything else instead of racing it —
    the exact bypass that has been causing CUDA OOM when the swarm/OpenClaw drive
    LM Studio directly. Returns a task_id immediately; poll /api/games/build/{id}.

    `model` is passed to the orchestrator ONLY when this call needs an LLM; a bare
    editor command (get_current_level, spawn_actor…) passes none, so no model is
    loaded and the editor keeps its VRAM."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    method = str(body.get("method") or "").strip()
    params = body.get("params") or {}
    project = (body.get("project") or _setting("games_mcp_project", "")).strip()
    use_llm = bool(body.get("use_llm"))
    if not method or not re.match(r"^[A-Za-z0-9_]{1,64}$", method):
        return JSONResponse({"error": "A valid bridge method name is required."},
                            status_code=400)
    if not isinstance(params, dict):
        return JSONResponse({"error": "params must be an object."}, status_code=400)
    if not project:
        return JSONResponse({"error": "No MCP project set. POST /api/games/mcp/settings "
                                      "with {\"project\": \"/path/to/Project\"} first."},
                            status_code=400)

    def _wrapped():
        _record(tid_holder["id"], status="running")
        try:
            res = _bridge_call_now(project, method, params)
        except Exception as e:              # never poison the queue
            res = {"ok": False, "error": str(e)[:2000]}
        _record(tid_holder["id"], status=("done" if res.get("ok") else "failed"),
                output=json.dumps(res)[:6000], artifact="", finished=time.time())
        return res

    tid_holder = {"id": 0}
    tid = orch.submit_llm(
        _wrapped, desc=f"MCP {method}: {Path(project).name[:30]}", priority=1,
        # Only name a model when the caller actually wants inference. A plain
        # editor command must NOT load one — that is what keeps co-op viable.
        model=(_mcp_model() or None) if use_llm else None,
        source="games_mcp")
    tid_holder["id"] = tid
    _record(tid, status="queued", started=time.time(), output="", artifact="")
    return {"task_id": tid, "method": method, "project": project,
            "model": (_mcp_model() or "(orchestrator default)") if use_llm else None,
            "queued": True}


# ─── docs notes ──────────────────────────────────────────────────────────────

@router.get("/api/games/notes")
def games_notes():
    """Free-text scratchpad for the Docs pane (settings-backed, no new table)."""
    return {"notes": _setting("games_notes", ""),
            "project_root": _project_root(),
            "docs": [
                {"engine": "godot", "label": "Godot docs", "url": DOC_LINKS["godot"]},
                {"engine": "godot", "label": "Godot: your first 2D game",
                 "url": "https://docs.godotengine.org/en/stable/getting_started/first_2d_game/index.html"},
                {"engine": "godot", "label": "Godot: exporting projects",
                 "url": "https://docs.godotengine.org/en/stable/tutorials/export/index.html"},
                {"engine": "godot", "label": "Godot: command line / headless",
                 "url": "https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html"},
                {"engine": "unity", "label": "Unity manual", "url": DOC_LINKS["unity"]},
                {"engine": "unity", "label": "Unity on Linux (Hub)",
                 "url": "https://docs.unity3d.com/hub/manual/InstallHub.html"},
                {"engine": "unreal", "label": "Unreal docs", "url": DOC_LINKS["unreal"]},
                {"engine": "unreal", "label": "Unreal on Linux",
                 "url": "https://dev.epicgames.com/documentation/en-us/unreal-engine/linux-development-requirements-for-unreal-engine"},
            ]}


@router.post("/api/games/notes")
async def games_notes_save(request: Request):
    """Save the Docs-pane notes and (optionally) the project root."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if "notes" in body:
        _set_setting("games_notes", str(body.get("notes") or "")[:20000])
    root = (body.get("project_root") or "").strip()
    if root:
        if not _ROOTS_RE.match(root):
            return JSONResponse({"error": "Project root must be a plain path, or a "
                                          "comma-separated list of them."}, status_code=400)
        _set_setting("games_project_root", root)
        cache.invalidate("games:projects")
    return {"ok": True, "project_root": _project_root()}


# ─── shop publishing (sibling module) ────────────────────────────────────────
# Imported LAST so games_publish can import helpers from here (_ssh, _project_root,
# _node_label) without a circular-import problem at module load.
from routers.games_publish import router as _publish_router   # noqa: E402

router.include_router(_publish_router)

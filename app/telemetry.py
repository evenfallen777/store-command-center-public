"""Host telemetry — live CPU / RAM / disk / GPU for the Store's machines.

The Store knew whether things were *up* (routers/health.py) but never how hard
they were working or how much room was left. This fills that in for both the
server it runs on and the GPU node it drives.

ONE collector, TWO transports: `_COLLECTOR` is a self-contained stdlib-only
Python program. Locally it runs through `python3 -`; on the node it is piped to
`ssh … python3 -` (the same trick routers/node.py uses for its status snippet).
Same code both sides, so the two hosts can never drift into reporting different
shapes — and nothing has to be installed on the node.

Deliberately no psutil: it would be a new dependency on every install, and a
node-side one we cannot guarantee. Everything here comes from /proc, statvfs
and nvidia-smi, all of which are already present on the Ubuntu boxes the Store
targets.
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Optional

import config

# ─────────────────────────────────────────────────────────────────────────────
# The collector. Runs on the target host; prints one JSON object to stdout.
# Keep it stdlib-only and Python 3.8-compatible — it has to run on whatever the
# node happens to have, not on this app's venv.
# ─────────────────────────────────────────────────────────────────────────────
_COLLECTOR = r'''
import json, os, socket, subprocess, time

def _read(p, default=""):
    try:
        with open(p) as f:
            return f.read()
    except Exception:
        return default

def cpu_times():
    line = _read("/proc/stat").split("\n")[0].split()
    v = [int(x) for x in line[1:]] if len(line) > 1 else []
    idle = (v[3] + v[4]) if len(v) > 4 else 0
    return sum(v), idle

def cpu():
    model, cores = "", 0
    for line in _read("/proc/cpuinfo").split("\n"):
        if line.startswith("model name") and not model:
            model = line.split(":", 1)[1].strip()
        if line.startswith("processor"):
            cores += 1
    # two samples so we report real utilisation, not a since-boot average
    t1, i1 = cpu_times()
    time.sleep(0.25)
    t2, i2 = cpu_times()
    dt, di = t2 - t1, i2 - i1
    pct = round(100.0 * (dt - di) / dt, 1) if dt > 0 else 0.0
    try:
        load = [float(x) for x in _read("/proc/loadavg").split()[:3]]
    except Exception:
        load = [0.0, 0.0, 0.0]
    temp = None
    for zone in range(6):
        t = _read("/sys/class/thermal/thermal_zone%d/temp" % zone).strip()
        typ = _read("/sys/class/thermal/thermal_zone%d/type" % zone).strip()
        if t.isdigit() and typ in ("x86_pkg_temp", "coretemp", "k10temp", "acpitz"):
            temp = round(int(t) / 1000.0, 1)
            break
    return {"model": model, "cores": cores, "usage_pct": pct,
            "load": load, "load_per_core": round(load[0] / cores, 2) if cores else None,
            "temp_c": temp}

def memory():
    info = {}
    for line in _read("/proc/meminfo").split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = int(v.strip().split()[0]) * 1024  # kB -> bytes
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    used = total - avail
    stot = info.get("SwapTotal", 0)
    sfree = info.get("SwapFree", 0)
    return {
        "total": total, "available": avail, "used": used,
        "used_pct": round(100.0 * used / total, 1) if total else 0.0,
        "cached": info.get("Cached", 0), "buffers": info.get("Buffers", 0),
        "swap_total": stot, "swap_used": stot - sfree,
        "swap_used_pct": round(100.0 * (stot - sfree) / stot, 1) if stot else 0.0,
    }

# pseudo/virtual filesystems carry no capacity worth showing
_SKIP_FS = {"proc","sysfs","devtmpfs","devpts","tmpfs","cgroup","cgroup2","pstore",
            "securityfs","debugfs","tracefs","configfs","fusectl","hugetlbfs","mqueue",
            "bpf","autofs","binfmt_misc","efivarfs","ramfs","squashfs","overlay","nsfs"}

def disks():
    out, seen = [], set()
    for line in _read("/proc/mounts").split("\n"):
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, mount, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
        if fstype in _SKIP_FS or not dev.startswith("/"):
            continue
        try:
            st = os.statvfs(mount)
        except Exception:
            continue
        total = st.f_blocks * st.f_frsize
        if total == 0:
            continue
        key = (dev, total)
        if key in seen:          # same device mounted twice — report it once
            continue
        seen.add(key)
        free = st.f_bavail * st.f_frsize
        used = total - (st.f_bfree * st.f_frsize)
        out.append({"device": dev, "mount": mount, "fstype": fstype,
                    "total": total, "used": used, "free": free,
                    "used_pct": round(100.0 * used / total, 1) if total else 0.0})
    out.sort(key=lambda d: -d["total"])
    return out

def gpus():
    q = ("index,name,memory.total,memory.used,utilization.gpu,"
         "temperature.gpu,power.draw,fan.speed")
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=" + q,
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return []
    except Exception:
        return []
    out = []
    for line in (r.stdout or "").strip().split("\n"):
        if not line.strip():
            continue
        f = [x.strip() for x in line.split(",")]
        if len(f) < 6:
            continue
        def num(x):
            try:
                return float(x)
            except Exception:
                return None
        mt, mu = num(f[2]), num(f[3])
        out.append({
            "index": f[0], "name": f[1],
            "mem_total": int(mt * 1048576) if mt is not None else None,
            "mem_used": int(mu * 1048576) if mu is not None else None,
            "mem_used_pct": round(100.0 * mu / mt, 1) if (mt and mu is not None) else None,
            "util_pct": num(f[4]), "temp_c": num(f[5]),
            "power_w": num(f[6]) if len(f) > 6 else None,
            "fan_pct": num(f[7]) if len(f) > 7 else None,
        })
    return out

def osinfo():
    pretty = ""
    for line in _read("/etc/os-release").split("\n"):
        if line.startswith("PRETTY_NAME="):
            pretty = line.split("=", 1)[1].strip().strip('"')
    try:
        up = float(_read("/proc/uptime").split()[0])
    except Exception:
        up = 0.0
    return {"hostname": socket.gethostname(), "os": pretty,
            "kernel": os.uname().release, "uptime_sec": int(up)}

print(json.dumps({"ok": True, "collected_at": time.time(),
                  "system": osinfo(), "cpu": cpu(), "memory": memory(),
                  "disks": disks(), "gpus": gpus()}))
'''


def _run(argv: list, timeout: int) -> dict:
    """Execute the collector through `argv` (which must end in a python3 reading
    stdin) and parse its JSON. Any failure becomes a structured error rather than
    an exception, so one unreachable host never blanks the whole page."""
    try:
        r = subprocess.run(argv, input=_COLLECTOR, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out after %ss" % timeout}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    if r.returncode != 0:
        err = (r.stderr or "").strip().split("\n")[-1][:200] or "exit %d" % r.returncode
        return {"ok": False, "error": err}
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"ok": False, "error": "unparseable output: " + (r.stdout or "")[:160]}


def collect_local(timeout: int = 20) -> dict:
    """Telemetry for the machine running the Store."""
    return _run(["python3", "-"], timeout)


def collect_remote(timeout: int = 30) -> dict:
    """Telemetry for the GPU node, over the same SSH the deploy flow uses."""
    return _run(list(config.BOX_SSH) + ["python3 -"], timeout)


def collect_all() -> dict:
    """Both hosts, tagged for the UI. The node is only labelled 'node' — its
    address comes from config so a single-box install (GPU_HOST=127.0.0.1) still
    renders two cards that happen to describe the same machine."""
    started = time.time()
    server = collect_local()
    node = collect_remote()
    return {
        "hosts": [
            {"key": "server", "label": "Server", "role": "store",
             "address": "localhost", **server},
            {"key": "node", "label": "GPU Node", "role": "gpu",
             "address": config.GPU_HOST, **node},
        ],
        "took_sec": round(time.time() - started, 2),
    }

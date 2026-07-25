# Onboarding — Plugins: extend the store without touching core

New-developer guide to the drop-in plugin system and a first-plugin walkthrough.
The **authoritative author contract is [`plugins/README.md`](../plugins/README.md)**
(also served in-app at `GET /api/plugins/readme`); this doc is the guided tour.
Companion docs: [`ONBOARDING-openclaw.md`](ONBOARDING-openclaw.md),
[`ONBOARDING-topology.md`](ONBOARDING-topology.md).

## The idea

Drop a folder in `plugins/`, restart the store, done. `plugins/` is gitignored (only
its README and the `hello-world` example are tracked), so your plugins survive every
store update with zero re-wiring. A broken plugin can never break boot, routing, or
the UI — it's listed as **failed** with its error and skipped.

```
plugins/<name>/
  plugin.json          # manifest (required; never web-served)
  backend.py           # exposes a FastAPI `router` (optional; never web-served)
  static/
    frontend.js        # defines a render fn + calls registerView() (optional)
    <assets…>          # everything in static/ is served at /plugins/<name>/…
```

## Who does what (the real code)

| Piece | File | Role |
|---|---|---|
| Backend host | `app/plugin_host.py` | At boot, walks `plugins/*/plugin.json`, checks `requires` deps, imports each `backend.py`, includes its `router` (with a route-collision guard), mounts each `static/`, serves `GET /api/plugins` + `POST /api/plugins/{id}/toggle` |
| Frontend loader | `static/js/plugin-loader.js` | Fetches `/api/plugins`, injects one sidebar nav item per **loaded** plugin (grouped by `nav_group`), script-loads each frontend; exposes `registerView()` |
| Reference plugin | `plugins/hello-world/` | Complete working example — one backend route + one view. Also the test fixture (`tests/test_plugins.py`) — **leave it in place** |
| Management UI | Settings → 🔌 Plugins | Enable/disable, statuses, errors (`plugin_disabled_<id>` setting; backend toggle applies on next restart) |

Guard rails (all in `app/plugin_host.py` — the store protects itself, not you):
import isolation (a backend that raises at import → plugin `failed`, boot continues),
route-collision guard (a path+method that already exists → router not included,
`failed: route collision <path>`), manifest `requires` checked *before* import
(`failed: missing deps [...]` — never auto-installed), and per-plugin enable/disable.

## Write your first plugin

A minimal "uptime" plugin: one API route, one sidebar view. Modeled directly on
`plugins/hello-world/` — copying that folder and renaming is an equally good start.

### 1. Create the folder + manifest

```bash
mkdir -p plugins/uptime/static
```

`plugins/uptime/plugin.json`:

```json
{
  "name": "Uptime",
  "version": "1.0.0",
  "icon": "⏱",
  "view": "uptime",
  "nav_group": "Plugins",
  "requires": [],
  "description": "Shows how long the store process has been up."
}
```

`view` must be unique — `registerView()` refuses to shadow a core view or another
plugin's. `backend`/`frontend` filenames default to `backend.py`/`frontend.js`.

### 2. Backend — `plugins/uptime/backend.py`

Expose a module-level `router = APIRouter()` and **namespace routes under
`/api/<plugin>/…`** (the collision guard will reject anything already taken):

```python
import time
from fastapi import APIRouter

router = APIRouter()
_STARTED = time.time()

@router.get("/api/uptime/now")
def uptime_now():
    return {"uptime_sec": int(time.time() - _STARTED)}
```

The store's `app/` dir is on `sys.path`, so a plugin backend can import the same
shared kernel core routers use (`from deps import *`, `from config import _env`, …) —
`plugins/hello-world/backend.py` documents this. Your routes sit behind the store's
normal auth guard automatically (session required; same-box localhost bypass applies,
as everywhere). Third-party imports go in the manifest's `"requires"` and get
pip-installed into the store's venv by you.

### 3. Frontend — `plugins/uptime/static/frontend.js`

Define an async render fn that draws into `#main-content`, then register it:

```javascript
'use strict';

async function renderUptime() {
  const data = await api('/api/uptime/now');
  document.getElementById('main-content').innerHTML = `
    <div class="card" style="padding:16px;">
      <h3 style="margin:0 0 10px;">⏱ Uptime</h3>
      ${statCard('Store has been up', esc(String(data.uptime_sec)) + ' s')}
    </div>`;
}
registerView('uptime', renderUptime);
```

All the store's frontend globals are available (`api()`, `esc()`, `toast()`,
`statCard()`, `hlp()`, plus the existing CSS classes/variables). `registerView` is the
only wiring — the injected nav item dispatches through the normal `renderView()` flow.
Extra assets in `static/` are served at `/plugins/uptime/<file>` (prefix URLs with the
`API` global in JS).

### 4. Restart + verify

1. Restart the store (Settings → System → **Restart Server**, or
   `systemctl --user restart store.service` if installed as a service).
2. `GET /api/plugins` — your entry should show `"status": "loaded"`, a `routes` count,
   and `"frontend_ok": true`. If it says `failed`, the (truncated) `error` field tells
   you why — also visible in **Settings → 🔌 Plugins**.
3. The **⏱ Uptime** nav item appears in the sidebar under **Plugins**; click it.
4. `GET /api/uptime/now` answers directly too (from the same box, no session needed).

### 5. Iterating

- Backend changes need a restart (uvicorn can't hot-reload plugin routers).
  Frontend/static changes need only a browser refresh (plugin assets are served with
  a 60 s cache).
- Disable/enable in Settings → 🔌 Plugins; a disabled plugin is listed but never
  imported or mounted (backend toggle takes effect on the next restart).

## Rules of the road

- **Only `static/` is web-served.** `plugin.json`, `backend.py`, and anything else at
  the plugin root are never exposed over HTTP. Don't put secrets in `static/` — it's
  served to any logged-in browser.
- A plugin backend runs **with the store's full privileges** — only install plugins
  you trust.
- Namespace routes under `/api/<plugin>/…` and pick a `view` id that can't collide.
- If your plugin calls any model — LLM, image, video, audio, 3D — it must ride the
  unified GPU queue like core code does (`orch.*` in `app/orchestrator.py`); no bare
  model calls in request handlers (see `docs/DEV_PROCESS.md` rule 3).
- The behavior contract is pinned by `tests/test_plugins.py` (discovery, serving,
  containment of broken plugins) — worth a read to see exactly what the host promises.

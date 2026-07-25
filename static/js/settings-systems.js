'use strict';

/* ── Settings → 🧩 Systems ──────────────────────────────────────────────────
   Live, read-only master status board of EVERY system, subsystem and plugin the
   app runs. Backed by GET /api/systems (app/systems_registry.py). No writes yet —
   this is the read-only seed of a future single-source settings registry.

   All symbols are IIFE-scoped except the one entry point window.renderSystemsPane,
   so this classic-script file never collides with another file's globals. */
(function () {
  const STATUS = {
    enabled:   { label: 'enabled',   color: 'var(--green)', bg: 'rgba(34,197,94,.14)' },
    disabled:  { label: 'disabled',  color: 'var(--muted)', bg: 'rgba(100,116,139,.16)' },
    gated:     { label: 'gated',     color: 'var(--warn)',  bg: 'rgba(245,158,11,.15)' },
    orphan:    { label: 'orphan',    color: 'var(--red)',   bg: 'rgba(239,68,68,.14)' },
    invisible: { label: 'invisible', color: 'var(--red)',   bg: 'rgba(239,68,68,.14)' },
    infra:     { label: 'infra',     color: '#9fb4d8',      bg: 'rgba(120,150,205,.15)' },
  };

  function pill(status) {
    const s = STATUS[status] || STATUS.infra;
    return `<span style="font-size:.62rem;font-weight:700;padding:2px 8px;border-radius:9px;` +
           `color:${s.color};background:${s.bg};white-space:nowrap;text-transform:uppercase;letter-spacing:.03em">${s.label}</span>`;
  }

  function chip(text, muted) {
    return `<code style="font-size:.66rem;padding:1px 5px;border-radius:5px;background:var(--surface);` +
           `border:1px solid var(--border);color:${muted ? 'var(--muted)' : 'var(--text)'}">${esc(text)}</code>`;
  }

  /* ── Inline setting controls ──────────────────────────────────────────────
     Every board row that carries a settable key becomes a live handle:
       • boolean  → a toggle switch  → PATCH /api/settings {key:'1'|'0'}
       • num/text → an inline editor → PATCH /api/settings {key:'<value>'}
     All writes go through the ONE existing generic write path (PATCH /api/settings);
     world_* keys land in the same settings table world_settings/world_ops read from.
     Optimistic UI: the pill/knob flips first, and a failed save reverts + toasts. */
  function toggleCtl(sys) {
    const on = sys.status === 'enabled';
    return `<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <button type="button" class="sys-toggle" role="switch" aria-checked="${on}"
        data-key="${esc(sys.setting_key)}" data-syskey="${esc(sys.key)}" data-on="${on ? '1' : '0'}"
        title="${esc(sys.setting_key)} — click to ${on ? 'disable' : 'enable'}"
        style="position:relative;width:38px;height:21px;flex:none;border:none;cursor:pointer;border-radius:11px;
        padding:0;transition:background .15s;background:${on ? 'var(--green)' : 'var(--border)'}">
        <span style="position:absolute;top:2px;left:2px;width:17px;height:17px;border-radius:50%;background:#fff;
          box-shadow:0 1px 2px rgba(0,0,0,.35);transition:transform .15s;
          transform:translateX(${on ? '17px' : '0'})"></span></button>
      <code style="font-size:.66rem;padding:1px 5px;border-radius:5px;background:var(--surface);
        border:1px solid var(--border);color:var(--muted)">${esc(sys.setting_key)}</code></div>`;
  }

  function editorCtl(sys) {
    const v = (sys.setting_value == null) ? '' : String(sys.setting_value);
    const num = sys.setting_type === 'float' || sys.setting_type === 'int';
    const step = sys.setting_type === 'float' ? '0.05' : '1';
    const bounds = sys.setting_key === 'world_taste_min' ? 'min="0" max="1"' : (num ? 'min="0"' : '');
    return `<div style="display:flex;flex-direction:column;gap:4px">
      <code style="font-size:.66rem;padding:1px 5px;border-radius:5px;background:var(--surface);
        border:1px solid var(--border);color:var(--muted);align-self:flex-start">${esc(sys.setting_key)}</code>
      <div style="display:flex;gap:5px;align-items:center">
        <input class="sys-val" type="${num ? 'number' : 'text'}" ${num ? `step="${step}" ${bounds}` : ''}
          data-key="${esc(sys.setting_key)}" data-syskey="${esc(sys.key)}" data-orig="${esc(v)}" value="${esc(v)}"
          style="width:${num ? '78px' : '128px'};padding:4px 7px;background:var(--surface);border:1px solid var(--border);
          border-radius:6px;color:var(--text);font-size:.72rem">
        <button type="button" class="sys-save" data-syskey="${esc(sys.key)}"
          style="padding:4px 9px;border:1px solid var(--border);border-radius:6px;background:var(--surface);
          color:var(--text);font-size:.68rem;font-weight:700;cursor:pointer">Save</button>
      </div></div>`;
  }

  function control(sys) {
    if (sys.editable && sys.setting_key) {
      return sys.setting_type === 'bool' ? toggleCtl(sys) : editorCtl(sys);
    }
    if (sys.gate)        return chip('gate: ' + sys.gate);
    if (sys.setting_key) return chip(sys.setting_key);
    return `<span style="color:var(--muted);font-size:.68rem">—</span>`;
  }

  /* Optimistically repaint a toggle button to `on`. */
  function paintToggle(btn, on) {
    btn.dataset.on = on ? '1' : '0';
    btn.setAttribute('aria-checked', on ? 'true' : 'false');
    btn.title = btn.dataset.key + ' — click to ' + (on ? 'disable' : 'enable');
    btn.style.background = on ? 'var(--green)' : 'var(--border)';
    const knob = btn.firstElementChild;
    if (knob) knob.style.transform = 'translateX(' + (on ? '17px' : '0') + ')';
  }

  /* Repaint a row's status pill (rows carry data-syskey; pill lives in .sys-pill). */
  function setRowPill(syskey, status) {
    const row = document.querySelector('#sys-board .sysrow[data-syskey="' + syskey + '"]');
    const cell = row && row.querySelector('.sys-pill');
    if (cell) cell.innerHTML = pill(status);
  }

  async function saveSetting(key, value) {
    return api('/api/settings', { method: 'PATCH', body: JSON.stringify({ [key]: value }) });
  }

  async function doToggle(btn) {
    if (btn.dataset.busy) return;
    const key = btn.dataset.key, syskey = btn.dataset.syskey;
    const cur = btn.dataset.on === '1', next = !cur;
    btn.dataset.busy = '1';
    paintToggle(btn, next);                              // optimistic
    setRowPill(syskey, next ? 'enabled' : 'disabled');
    try {
      await saveSetting(key, next ? '1' : '0');
      toast(key + ' → ' + (next ? 'on' : 'off'));
    } catch (e) {
      paintToggle(btn, cur);                             // revert both control + pill
      setRowPill(syskey, cur ? 'enabled' : 'disabled');
      toast("Couldn't save " + key + ': ' + (e.message || e), 'error');
    } finally {
      delete btn.dataset.busy;
    }
  }

  async function doSave(btn) {
    if (btn.dataset.busy) return;
    const input = document.querySelector('#sys-board .sys-val[data-syskey="' + btn.dataset.syskey + '"]');
    if (!input) return;
    const key = input.dataset.key, val = input.value.trim();
    btn.dataset.busy = '1'; btn.disabled = true;
    try {
      await saveSetting(key, val);
      input.dataset.orig = val;
      toast(key + ' saved');
    } catch (e) {
      input.value = input.dataset.orig;                  // revert on failure
      toast("Couldn't save " + key + ': ' + (e.message || e), 'error');
    } finally {
      delete btn.dataset.busy; btn.disabled = false;
    }
  }

  function onBoardClick(e) {
    const t = e.target.closest && e.target.closest('.sys-toggle');
    if (t) { doToggle(t); return; }
    const s = e.target.closest && e.target.closest('.sys-save');
    if (s) { doSave(s); }
  }

  function onBoardKey(e) {
    if (e.key !== 'Enter') return;
    const inp = e.target.closest && e.target.closest('.sys-val');
    if (!inp) return;
    e.preventDefault();
    const btn = inp.parentElement.querySelector('.sys-save');
    if (btn) doSave(btn);
  }

  function visible(v) {
    return v
      ? `<span style="color:var(--green);font-weight:700" title="Has a leg in the Company world">&#10003;</span>`
      : `<span style="color:var(--red);font-weight:700" title="No leg in the Company world">&#10007;</span>`;
  }

  function sysRow(sys) {
    const subs = (sys.subsystems || []).length
      ? `<div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap">` +
        sys.subsystems.map(s => `<span style="font-size:.6rem;color:var(--muted);background:var(--surface);` +
          `border:1px solid var(--border);border-radius:6px;padding:1px 5px">${esc(s)}</span>`).join('') + `</div>`
      : '';
    const note = sys.notes
      ? `<div style="font-size:.68rem;color:var(--muted);margin-top:4px;line-height:1.4">${esc(sys.notes)}</div>`
      : '';
    const tab = sys.tab
      ? `<span style="font-size:.66rem;color:#9fb4d8">${esc(sys.tab)}</span>`
      : `<span style="font-size:.66rem;color:var(--muted)">—</span>`;
    const hay = [sys.key, sys.label, sys.notes, sys.setting_key, sys.gate, sys.tab, sys.status,
                 (sys.subsystems || []).join(' ')].filter(Boolean).join(' ').toLowerCase();
    return `<div class="sysrow" data-hay="${esc(hay)}" data-syskey="${esc(sys.key)}"
        style="display:grid;grid-template-columns:minmax(200px,2fr) 88px 64px minmax(120px,1.2fr) 90px;
        gap:10px;align-items:start;padding:11px 12px;border-bottom:1px solid var(--border)">
      <div>
        <div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap">
          <b style="font-size:.82rem">${esc(sys.label)}</b>
        </div>
        ${subs}${note}
      </div>
      <div class="sys-pill">${pill(sys.status)}</div>
      <div style="text-align:center;font-size:.9rem">${visible(sys.world_visible)}</div>
      <div style="align-self:center">${control(sys)}</div>
      <div style="align-self:center">${tab}</div>
    </div>`;
  }

  function header() {
    return `<div style="display:grid;grid-template-columns:minmax(200px,2fr) 88px 64px minmax(120px,1.2fr) 90px;
        gap:10px;padding:7px 12px;border-bottom:2px solid var(--border);
        font-size:.64rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)">
      <div>System / subsystems</div><div>Status</div><div style="text-align:center">World</div>
      <div>Setting / gate</div><div>Tab</div></div>`;
  }

  function categoryBlock(cat, systems) {
    const rows = systems.map(sysRow).join('');
    return `<div class="sys-cat" style="margin-bottom:22px">
      <div style="font-size:.9rem;font-weight:700;color:var(--accent);margin-bottom:2px">${esc(cat.label)}
        <span style="font-size:.66rem;font-weight:500;color:var(--muted)">· ${systems.length}</span></div>
      <div style="font-size:.72rem;color:var(--muted);margin-bottom:9px;line-height:1.4">${esc(cat.desc || '')}</div>
      <div style="border:1px solid var(--border);border-radius:11px;overflow:hidden;background:var(--surface2)">
        ${header()}${rows}
      </div></div>`;
  }

  function pluginsBlock(plugins) {
    const note = `<div style="font-size:.72rem;color:var(--muted);margin-bottom:10px;line-height:1.45">
      Drop-in add-ons discovered from <code>plugins/&lt;name&gt;/</code> at boot. New plugins auto-appear here
      (and in the sidebar) on the next restart — no core edits.</div>`;
    if (!plugins || !plugins.length) {
      return `<div class="sys-cat"><div style="font-size:.9rem;font-weight:700;color:var(--accent);margin-bottom:2px">🔌 Plugins <span style="font-size:.66rem;font-weight:500;color:var(--muted)">· 0</span></div>${note}
        <div style="font-size:.78rem;color:var(--muted);padding:14px;border:1px dashed var(--border);border-radius:11px">No plugins installed.</div></div>`;
    }
    const rows = plugins.map(p => {
      const st = p.loaded ? 'enabled' : (p.enabled ? 'disabled' : 'disabled');
      const stLabel = p.loaded ? pill('enabled') : pill('disabled');
      const link = p.frontend_url
        ? chip(p.frontend_url, true)
        : `<span style="font-size:.66rem;color:var(--muted)">backend-only</span>`;
      return `<div style="display:grid;grid-template-columns:minmax(200px,2fr) 88px 90px minmax(120px,1.4fr);
          gap:10px;align-items:center;padding:11px 12px;border-bottom:1px solid var(--border)">
        <div><b style="font-size:.82rem">${esc(p.icon || '🧩')} ${esc(p.name || p.id)}</b>
          <span style="font-size:.66rem;color:var(--muted);margin-left:6px">v${esc(p.version || '?')}</span>
          ${p.description ? `<div style="font-size:.68rem;color:var(--muted);margin-top:3px;line-height:1.4">${esc(p.description)}</div>` : ''}</div>
        <div>${stLabel}</div>
        <div><span style="font-size:.66rem;color:#9fb4d8">${esc(p.view || '—')}</span></div>
        <div>${link}</div>
      </div>`;
    }).join('');
    return `<div class="sys-cat"><div style="font-size:.9rem;font-weight:700;color:var(--accent);margin-bottom:2px">🔌 Plugins
        <span style="font-size:.66rem;font-weight:500;color:var(--muted)">· ${plugins.length}</span></div>${note}
      <div style="border:1px solid var(--border);border-radius:11px;overflow:hidden;background:var(--surface2)">
        <div style="display:grid;grid-template-columns:minmax(200px,2fr) 88px 90px minmax(120px,1.4fr);gap:10px;
          padding:7px 12px;border-bottom:2px solid var(--border);font-size:.64rem;font-weight:700;
          text-transform:uppercase;letter-spacing:.04em;color:var(--muted)">
          <div>Plugin</div><div>Status</div><div>View</div><div>Frontend</div></div>
        ${rows}</div></div>`;
  }

  function countBar(counts) {
    const item = (n, label, color) =>
      `<div style="display:flex;flex-direction:column;align-items:center;min-width:70px">
        <span style="font-size:1.15rem;font-weight:800;color:${color}">${n}</span>
        <span style="font-size:.62rem;color:var(--muted);text-transform:uppercase;letter-spacing:.04em">${label}</span></div>`;
    return `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px">
      ${item(counts.total, 'systems', 'var(--text)')}
      ${item(counts.enabled, 'enabled', 'var(--green)')}
      ${item(counts.invisible, 'invisible', 'var(--red)')}
      ${item(counts.orphan, 'orphan', 'var(--red)')}
      ${item(counts.plugins, 'plugins', '#9fb4d8')}</div>`;
  }

  function applyFilter(q) {
    q = (q || '').trim().toLowerCase();
    document.querySelectorAll('#sys-board .sysrow').forEach(row => {
      row.style.display = (!q || row.dataset.hay.indexOf(q) !== -1) ? '' : 'none';
    });
    document.querySelectorAll('#sys-board .sys-cat').forEach(cat => {
      const rows = cat.querySelectorAll('.sysrow');
      if (!rows.length) return;                       // plugins block has no .sysrow
      const anyVisible = [...rows].some(r => r.style.display !== 'none');
      cat.style.display = anyVisible ? '' : 'none';
    });
  }

  /* ── 🩺 Health pulse ──────────────────────────────────────────────────────
     Compact red/amber/green dots for every dependency the Store leans on (GPU
     box, LM Studio, ComfyUI, Docker + key containers, DNS). Polled while the
     Systems pane is open; the poll self-cancels once the pane is hidden. */
  const HEALTH_DOT = {
    up:       { color: 'var(--green)', ring: 'rgba(34,197,94,.35)' },
    down:     { color: 'var(--red)',   ring: 'rgba(239,68,68,.4)' },
    degraded: { color: 'var(--warn)',  ring: 'rgba(245,158,11,.4)' },
    unknown:  { color: 'var(--muted)', ring: 'rgba(120,140,170,.3)' },
  };
  let _healthTimer = null;

  function healthDot(c) {
    const d = HEALTH_DOT[c.status] || HEALTH_DOT.unknown;
    const title = `${c.label} — ${c.status.toUpperCase()}${c.detail ? '\n' + c.detail : ''}`;
    return `<div title="${esc(title)}" style="display:flex;align-items:center;gap:6px;
        padding:5px 10px;border:1px solid var(--border);border-radius:9px;background:var(--surface);
        font-size:.72rem;white-space:nowrap;cursor:default">
      <span style="width:9px;height:9px;border-radius:50%;flex:none;background:${d.color};
        box-shadow:0 0 0 3px ${d.ring}"></span>
      <span style="color:var(--text)">${esc(c.label)}</span></div>`;
  }

  function renderHealthInto(el, data) {
    const comps = (data && data.components) || [];
    const sum = (data && data.summary) || {};
    const worst = (data && data.worst) || 'unknown';
    const wd = HEALTH_DOT[worst] || HEALTH_DOT.unknown;
    const allUp = worst === 'up' && comps.length;
    const parts = [];
    if (sum.down)     parts.push(`${sum.down} down`);
    if (sum.degraded) parts.push(`${sum.degraded} degraded`);
    if (sum.unknown)  parts.push(`${sum.unknown} unknown`);
    if (sum.up)       parts.push(`${sum.up} up`);
    const line = allUp ? 'All systems up' : (parts.join(' · ') || 'No components reporting');
    // group by component.group, preserving first-seen order
    const groups = [];
    const gmap = {};
    comps.forEach(c => {
      if (!gmap[c.group]) { gmap[c.group] = []; groups.push(c.group); }
      gmap[c.group].push(c);
    });
    const blocks = groups.map(g =>
      `<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:7px">
        <span style="font-size:.62rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
          color:var(--muted);min-width:66px">${esc(g)}</span>
        ${gmap[g].map(healthDot).join('')}</div>`).join('');
    el.innerHTML =
      `<div style="border:1px solid var(--border);border-radius:12px;background:var(--surface2);
        padding:13px 15px;margin-bottom:20px">
        <div style="display:flex;align-items:center;gap:9px;flex-wrap:wrap">
          <span style="font-size:.95rem;font-weight:800">🩺 Health</span>
          <span style="width:10px;height:10px;border-radius:50%;background:${wd.color};
            box-shadow:0 0 0 3px ${wd.ring}"></span>
          <span style="font-size:.8rem;font-weight:600;color:${wd.color}">${esc(line)}</span>
          <span style="font-size:.66rem;color:var(--muted);margin-left:auto">hover a dot for detail</span>
        </div>
        ${blocks}
      </div>`;
  }

  async function refreshHealth() {
    const el = document.getElementById('health-pulse');
    // pane hidden / gone → stop polling (offsetParent is null when display:none)
    if (!el || el.offsetParent === null) {
      if (_healthTimer) { clearInterval(_healthTimer); _healthTimer = null; }
      return;
    }
    try {
      renderHealthInto(el, await api('/api/health/pulse'));
    } catch (e) {
      el.innerHTML = `<div style="border:1px solid var(--border);border-radius:12px;background:var(--surface2);
        padding:13px 15px;margin-bottom:20px;font-size:.78rem;color:#e07a7a">🩺 Health — couldn't load pulse: ${esc(e.message || e)}</div>`;
    }
  }

  function renderHealthPulse() {
    if (_healthTimer) { clearInterval(_healthTimer); _healthTimer = null; }
    refreshHealth();
    _healthTimer = setInterval(refreshHealth, 20000);
  }

  window.renderSystemsPane = async function () {
    const pane = document.getElementById('pane-systems');
    if (!pane) return;
    pane.innerHTML = `<div style="color:var(--muted);font-size:.8rem">Loading systems&hellip;</div>`;
    let data;
    try {
      data = await api('/api/systems');
    } catch (e) {
      pane.innerHTML = `<div style="color:#e07a7a;font-size:.8rem">Couldn't load the systems board: ${esc(e.message || e)}</div>`;
      return;
    }
    const byCat = {};
    (data.systems || []).forEach(s => { (byCat[s.category] = byCat[s.category] || []).push(s); });

    let h = `<div id="health-pulse"></div>
      <div class="settings-section-head">🧩 Systems</div>
      <div style="font-size:.8rem;color:var(--muted);margin-bottom:14px;max-width:820px;line-height:1.55">
        Live, read-only status of every system, subsystem &amp; plugin the app runs — what exists, whether it's
        on / off / gated, whether it shows in the Company world, and what controls it. The seed of a future
        single-source settings registry.</div>
      ${countBar(data.counts || {})}
      <div style="margin-bottom:16px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <input id="sys-filter" type="text" placeholder="🔍 Filter systems, settings, tabs…"
          style="flex:1;min-width:220px;max-width:420px;padding:8px 11px;background:var(--surface);
          border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:.8rem">
        <span style="font-size:.66rem;color:var(--muted)">
          ${pill('enabled')} ${pill('gated')} ${pill('disabled')} ${pill('orphan')} ${pill('invisible')} ${pill('infra')}</span>
      </div>
      <div id="sys-board">`;
    (data.categories || []).forEach(cat => {
      const systems = byCat[cat.key] || [];
      if (systems.length) h += categoryBlock(cat, systems);
    });
    h += pluginsBlock(data.plugins || []);
    h += `</div>`;

    /* Store Logs — moved here from the System pane (admin.js still owns the
       loadStoreLogs/toggleLogAuto loaders; they're window-globals). */
    h += `
      <div class="settings-group" style="margin-top:20px;">
        <div class="settings-group-title">&#128220; Store Logs</div>
        <div style="font-size:.75rem;color:var(--muted);margin-bottom:8px;">Everything the server logs — errors, warnings, background jobs. Rotating file in the data folder.</div>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:8px;">
          <select id="log-level" onchange="loadStoreLogs()" style="padding:5px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;font-size:.78rem;">
            <option value="">All</option><option value="ERROR">Errors</option><option value="WARNING">Warnings+</option></select>
          <input id="log-search" placeholder="filter text…" onkeydown="if(event.key==='Enter')loadStoreLogs()" style="flex:1;min-width:120px;padding:5px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;font-size:.78rem;">
          <button class="btn-sm" onclick="loadStoreLogs()">&#128260; Refresh</button>
          <label style="font-size:.72rem;color:var(--muted);display:flex;align-items:center;gap:3px;cursor:pointer;"><input type="checkbox" id="log-auto" onchange="toggleLogAuto()"> auto</label>
          <span id="log-tally" style="font-size:.72rem;color:var(--muted);"></span>
        </div>
        <pre id="store-logs" style="max-height:360px;overflow:auto;background:#0b0b0f;border:1px solid var(--border);border-radius:8px;padding:10px;font-size:.7rem;line-height:1.35;white-space:pre-wrap;color:#cbd5e1;margin:0;">Loading&hellip;</pre>
      </div>`;
    pane.innerHTML = h;

    const f = document.getElementById('sys-filter');
    if (f) f.addEventListener('input', () => applyFilter(f.value));

    const board = document.getElementById('sys-board');
    if (board) {                              // delegated inline-control handlers
      board.addEventListener('click', onBoardClick);
      board.addEventListener('keydown', onBoardKey);
    }

    renderHealthPulse();   // fill 🩺 Health at the top + start polling while open
    if (typeof loadStoreLogs === 'function') loadStoreLogs();   // fill 📜 Store Logs (admin.js loader)
  };
})();

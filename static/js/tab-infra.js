/* ══ INFRASTRUCTURE ══
   Live hardware for every machine the Store uses: CPU, RAM, disks and GPUs for
   the server it runs on and the GPU node it drives. The Services/Docker hub says
   WHAT is running; this says how hard the metal is working and how much room is
   left.

   Accessibility note — the meters are status-coloured (green/amber/red from the
   app's own vars), but that pair fails colourblind separation (green↔amber is
   ΔE 5.7 for protanopia). So colour is never the only signal: every meter also
   prints its percentage, and anything at or past the warning threshold gets a
   word ("high" / "critical"). Read with the colour stripped out, it still works. */

const INFRA_WARN = 70;    // %  — amber
const INFRA_CRIT = 90;    // %  — red
let _infraTimer = null;

function _infraState(pct) {
  if (pct >= INFRA_CRIT) return { c: 'var(--red)',   word: 'critical' };
  if (pct >= INFRA_WARN) return { c: 'var(--warn)',  word: 'high' };
  return { c: 'var(--green)', word: '' };
}

function _gb(bytes)  { return (bytes || 0) / 1e9; }
function _fmtGB(b)   { const g = _gb(b); return g >= 100 ? g.toFixed(0) : g.toFixed(1); }
function _fmtUptime(s) {
  s = s || 0;
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : (h ? `${h}h ${m}m` : `${m}m`);
}

/* One meter row. `pct` drives the fill; `right` is the raw magnitude (e.g.
   "4.5 / 8.1 GB") so the number is always readable independent of the bar. */
function _meter(label, pct, right, hint) {
  pct = Math.max(0, Math.min(100, pct || 0));
  const st = _infraState(pct);
  return `
    <div style="margin-bottom:9px;" ${hint ? `title="${esc(hint)}"` : ''}>
      <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:.76rem;margin-bottom:3px;">
        <span style="color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(label)}</span>
        <span style="color:var(--text);font-variant-numeric:tabular-nums;white-space:nowrap;">
          ${right ? `<span style="color:var(--muted);">${esc(right)}</span>&nbsp;&nbsp;` : ''}${pct.toFixed(0)}%${
            st.word ? ` <span style="color:${st.c};font-weight:600;">${st.word}</span>` : ''}
        </span>
      </div>
      <div style="height:7px;background:var(--bg);border-radius:4px;overflow:hidden;">
        <div style="height:100%;width:${pct}%;background:${st.c};border-radius:4px;"></div>
      </div>
    </div>`;
}

function _hostCard(h) {
  if (!h.ok) {
    return `<div class="card" style="padding:16px;">
      <div style="font-weight:700;margin-bottom:4px;">${esc(h.label)}</div>
      <div style="font-size:.78rem;color:var(--muted);margin-bottom:10px;">${esc(h.address)}</div>
      <div style="font-size:.8rem;color:var(--red);">&#9888;&#65039; Unreachable — ${esc(h.error || 'no response')}</div>
      <div style="font-size:.74rem;color:var(--muted);margin-top:8px;line-height:1.5;">
        ${h.key === 'node' ? 'Checked over SSH. Verify the node is powered on and STORE_GPU_HOST / STORE_GPU_SSH_USER are correct.' : 'Local probe failed.'}
      </div>
    </div>`;
  }
  const s = h.system || {}, c = h.cpu || {}, m = h.memory || {};
  const disks = h.disks || [], gpus = h.gpus || [];

  const gpuHtml = gpus.length ? gpus.map(g => {
    const vram = (g.mem_used_pct != null) ? g.mem_used_pct : 0;
    const bits = [];
    if (g.util_pct != null) bits.push(`${g.util_pct.toFixed(0)}% util`);
    if (g.temp_c != null)   bits.push(`${g.temp_c.toFixed(0)}&deg;C`);
    if (g.power_w != null)  bits.push(`${g.power_w.toFixed(0)}W`);
    return `<div style="margin-top:10px;">
        <div style="font-size:.78rem;color:var(--text);margin-bottom:5px;">
          ${esc(g.name)} <span style="color:var(--muted);">&middot; ${bits.join(' &middot; ')}</span>
        </div>
        ${_meter('VRAM', vram, `${_fmtGB(g.mem_used)} / ${_fmtGB(g.mem_total)} GB`,
                 'GPU memory in use. Models stay resident here — high VRAM with low utilisation just means something is loaded and idle.')}
      </div>`;
  }).join('') : `<div style="font-size:.78rem;color:var(--muted);margin-top:8px;">No NVIDIA GPU detected.</div>`;

  return `<div class="card" style="padding:16px;">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:12px;">
      <div>
        <div style="font-weight:700;font-size:.98rem;">${esc(h.label)}</div>
        <div style="font-size:.74rem;color:var(--muted);margin-top:2px;">
          ${esc(s.hostname || '')} &middot; ${esc(h.address)}
        </div>
      </div>
      <div style="text-align:right;font-size:.72rem;color:var(--muted);line-height:1.5;">
        ${esc(s.os || '')}<br>up ${_fmtUptime(s.uptime_sec)}
      </div>
    </div>

    <div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin:14px 0 7px;">Compute</div>
    ${_meter('CPU', c.usage_pct, `${c.cores || '?'} cores${c.temp_c != null ? ` &middot; ${c.temp_c.toFixed(0)}°C` : ''}`,
             'Live utilisation, sampled over 0.25s — not a since-boot average.')}
    <div style="font-size:.72rem;color:var(--muted);margin:-3px 0 10px;">
      ${esc((c.model || 'unknown CPU').slice(0, 46))}
      ${c.load ? ` &middot; load ${c.load.map(x => x.toFixed(2)).join(' / ')}` : ''}
      ${c.load_per_core != null ? ` (${c.load_per_core.toFixed(2)}/core)` : ''}
    </div>

    <div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin:14px 0 7px;">Memory</div>
    ${_meter('RAM', m.used_pct, `${_fmtGB(m.used)} / ${_fmtGB(m.total)} GB`,
             'Excludes cache/buffers — this is memory genuinely unavailable to new work.')}
    ${m.swap_total ? _meter('Swap', m.swap_used_pct, `${_fmtGB(m.swap_used)} / ${_fmtGB(m.swap_total)} GB`,
             'Sustained swap use with high RAM means this box is short on memory and is paging to disk.') : ''}

    <div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin:14px 0 7px;">Storage</div>
    ${disks.length ? disks.map(d => _meter(
        `${d.mount}`, d.used_pct,
        `${_fmtGB(d.used)} / ${_fmtGB(d.total)} GB`,
        `${d.device} (${d.fstype}) — ${_fmtGB(d.free)} GB free`)).join('')
      : '<div style="font-size:.78rem;color:var(--muted);">No mounted filesystems reported.</div>'}

    <div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin:14px 0 7px;">GPU</div>
    ${gpuHtml}
  </div>`;
}

async function loadInfra() {
  const body = document.getElementById('infra-body');
  if (!body) return;
  try {
    const d = await api('/api/infra/telemetry');
    body.innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px;">
        ${(d.hosts || []).map(_hostCard).join('')}
      </div>
      <div style="font-size:.72rem;color:var(--muted);margin-top:12px;">
        Sampled in ${d.took_sec}s &middot; cached ~5s &middot; the node is probed over SSH.
      </div>`;
  } catch (e) {
    body.innerHTML = `<div class="card" style="padding:16px;color:var(--red);">
      Could not load telemetry: ${esc(e.message || String(e))}</div>`;
  }
}
window.loadInfra = loadInfra;

async function renderInfra() {
  document.getElementById('main-content').innerHTML = `
    <div class="view-header">
      <div class="view-title">&#128421;&#65039; Infrastructure</div>
      <div class="view-sub">Live CPU, memory, storage and GPU for the machines behind the Store. Amber past ${INFRA_WARN}%, red past ${INFRA_CRIT}%.</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:14px;">
      <button class="btn-sm" onclick="infraRefresh()" title="Force a fresh probe now, bypassing the ~5s cache.">&#128260; Refresh</button>
      <label style="font-size:.78rem;display:flex;gap:6px;align-items:center;cursor:pointer;color:var(--muted);">
        <input type="checkbox" id="infra-auto" onchange="infraAuto(this.checked)"> auto-refresh (10s)
      </label>
    </div>
    <div id="infra-body"><div class="loading-state">Probing hosts…</div></div>`;
  await loadInfra();
}
window.renderInfra = renderInfra;

async function infraRefresh() {
  try { await api('/api/infra/telemetry/refresh', { method: 'POST' }); } catch {}
  await loadInfra();
}
window.infraRefresh = infraRefresh;

/* Auto-refresh is opt-in and self-cancelling: each poll costs an SSH round-trip,
   and the interval is cleared as soon as the view is gone so it cannot keep
   probing the node from a tab the user has navigated away from. */
function infraAuto(on) {
  if (_infraTimer) { clearInterval(_infraTimer); _infraTimer = null; }
  if (!on) return;
  _infraTimer = setInterval(() => {
    if (!document.getElementById('infra-body')) { clearInterval(_infraTimer); _infraTimer = null; return; }
    loadInfra();
  }, 10000);
}
window.infraAuto = infraAuto;

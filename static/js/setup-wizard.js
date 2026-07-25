'use strict';
/* First-run setup wizard — pure glue over existing endpoints (see app/routers/auth.py
 * GET /setup for the page shell, and app/auth_core.py needs_setup() for the gate that
 * lands a fresh install here). Every step writes through an endpoint that already
 * existed before this wizard — see the comment on each render*() function. */

const API = window.__STORE_BASE__ || '';

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  let body = null;
  try { body = await res.json(); } catch (e) { /* no/invalid JSON body */ }
  if (!res.ok) {
    const d = body && body.detail;
    const msg = (typeof d === 'string' && d)
      || (Array.isArray(d) && d.map(x => x.msg || JSON.stringify(x)).join('; '))
      || (body && (body.error || body.message))
      || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return body;
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* ── wizard state / step machine ── */
const STEP_IDS = ['password', 'topology', 'node', 'toggles', 'health', 'finish'];
const state = {
  stepIdx: 0,
  topology: null,   // '1pc' | '2pc'
  toggles: {},      // setting_key -> bool (checked in step 4)
};

function visibleSteps() {
  // The GPU-node step only applies to the 2-PC topology.
  return STEP_IDS.filter(id => !(id === 'node' && state.topology === '1pc'));
}

function renderDots() {
  const vis = visibleSteps();
  const cur = vis.indexOf(STEP_IDS[state.stepIdx]);
  document.getElementById('wizard-steps').innerHTML = vis.map((id, i) =>
    `<div class="step-dot ${i < cur ? 'done' : ''} ${i === cur ? 'active' : ''}"></div>`
  ).join('');
}

function next() {
  let idx = state.stepIdx + 1;
  while (idx < STEP_IDS.length && STEP_IDS[idx] === 'node' && state.topology === '1pc') idx++;
  state.stepIdx = Math.min(idx, STEP_IDS.length - 1);
  render();
}

function back() {
  let idx = state.stepIdx - 1;
  while (idx >= 0 && STEP_IDS[idx] === 'node' && state.topology === '1pc') idx--;
  state.stepIdx = Math.max(0, idx);
  render();
}

async function finishSetup() {
  // Sticky flag — PATCH /api/settings (generic settings write). Once this is "1",
  // auth_core.needs_setup() never routes here again (see app/routers/auth.py "/").
  try {
    await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ setup_complete: '1' }) });
  } catch (e) { /* even if this fails, don't trap the user on the wizard */ }
  window.location.href = `${API}/`;
}

function render() {
  renderDots();
  const id = STEP_IDS[state.stepIdx];
  if (id === 'password') renderPassword();
  else if (id === 'topology') renderTopology();
  else if (id === 'node') renderNode();
  else if (id === 'toggles') renderToggles();
  else if (id === 'health') renderHealth();
  else renderFinish();
}

/* ── Step 1: password — reuses POST /api/auth/change-password (needs current + new,
   8-char min; already flips the default-password flag on success). The endpoint
   PBKDF2-hashes the password server-side; this page never stores it anywhere. ── */
function renderPassword() {
  document.getElementById('wizard-root').innerHTML = `
    <label for="w-cur">Current password</label>
    <input type="password" id="w-cur" autocomplete="current-password" placeholder="The install's current password">
    <label for="w-new">New password</label>
    <input type="password" id="w-new" autocomplete="new-password" placeholder="At least 8 characters">
    <label for="w-new2">Confirm new password</label>
    <input type="password" id="w-new2" autocomplete="new-password" placeholder="Repeat the new password">
    <div class="hint">Hashed (PBKDF2-HMAC-SHA256, salted) before it's ever written to disk — the plain password is never stored.</div>
    <div id="w-msg"></div>
    <div class="actions">
      <span></span>
      <div style="display:flex;gap:10px;">
        <button class="ghost" type="button" id="w-pw-skip">Skip this step</button>
        <button type="button" id="w-pw-next">Save &amp; Continue &rarr;</button>
      </div>
    </div>`;
  document.getElementById('w-pw-skip').onclick = () => next();
  document.getElementById('w-pw-next').onclick = async () => {
    const cur = document.getElementById('w-cur').value;
    const n1 = document.getElementById('w-new').value;
    const n2 = document.getElementById('w-new2').value;
    const msg = document.getElementById('w-msg');
    msg.innerHTML = '';
    if (!cur || !n1) { msg.innerHTML = '<div class="err">Enter your current and new password.</div>'; return; }
    if (n1.length < 8) { msg.innerHTML = '<div class="err">New password must be at least 8 characters.</div>'; return; }
    if (n1 !== n2) { msg.innerHTML = '<div class="err">Passwords do not match.</div>'; return; }
    try {
      await api('/api/auth/change-password', {
        method: 'POST', body: JSON.stringify({ current: cur, new_password: n1 }),
      });
      next();
    } catch (e) {
      msg.innerHTML = `<div class="err">${esc(e.message)}</div>`;
    }
  };
}

/* ── Step 2: topology — persisted via the generic PATCH /api/settings. Choosing
   1-PC also points the GPU host at 127.0.0.1 via POST /api/settings/nodes — the
   same mechanism app/retail_scrub.py's public no-GPU default uses. ── */
function renderTopology() {
  document.getElementById('wizard-root').innerHTML = `
    <div class="opt ${state.topology === '1pc' ? 'sel' : ''}" id="opt-1pc">
      <input type="radio" name="topo" ${state.topology === '1pc' ? 'checked' : ''} readonly>
      <div><div class="t">One PC</div>
        <div class="d">Everything runs on this box. Image, video, music, 3D generation and the local
          LLM stay OFF — the dashboard, storefront and everything else still runs fine.</div></div>
    </div>
    <div class="opt ${state.topology === '2pc' ? 'sel' : ''}" id="opt-2pc">
      <input type="radio" name="topo" ${state.topology === '2pc' ? 'checked' : ''} readonly>
      <div><div class="t">Two PCs</div>
        <div class="d">A separate GPU box handles the heavy AI work (image/video/music/3D/LLM) over the
          network, so this box stays light. You'll point it at that box next.</div></div>
    </div>
    <div id="w-msg"></div>
    <div class="actions">
      <button class="ghost" type="button" id="w-topo-back">&larr; Back</button>
      <button type="button" id="w-topo-next" ${state.topology ? '' : 'disabled'}>Continue &rarr;</button>
    </div>`;
  const pick = (v) => { state.topology = v; renderTopology(); };
  document.getElementById('opt-1pc').onclick = () => pick('1pc');
  document.getElementById('opt-2pc').onclick = () => pick('2pc');
  document.getElementById('w-topo-back').onclick = back;
  document.getElementById('w-topo-next').onclick = async () => {
    const msg = document.getElementById('w-msg');
    msg.innerHTML = '';
    try {
      await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ topology: state.topology }) });
      if (state.topology === '1pc') {
        await api('/api/settings/nodes', { method: 'POST', body: JSON.stringify({ gpu_host: '127.0.0.1' }) });
      }
      next();
    } catch (e) {
      msg.innerHTML = `<div class="err">${esc(e.message)}</div>`;
    }
  };
}

/* ── Step 3 (2-PC only): gpu_host + ssh_user → POST /api/settings/nodes. "Test" hits
   the existing cheap GET /api/node/ping for instant feedback. It checks the box's
   CURRENTLY ACTIVE GPU host (node/env changes need a restart to take effect), so an
   unreachable result here never blocks continuing — the node-down banner covers the
   ongoing state once the new host is live. ── */
async function renderNode() {
  document.getElementById('wizard-root').innerHTML = `
    <label for="w-host">GPU node host / IP</label>
    <input type="text" id="w-host" placeholder="e.g. 127.0.0.1">
    <label for="w-user">SSH user on that box</label>
    <input type="text" id="w-user" placeholder="e.g. myuser">
    <div class="actions" style="margin-top:14px;">
      <button class="ghost" type="button" id="w-test">Test connection</button>
      <span id="w-test-result" style="font-size:.8rem;color:#9090b0;"></span>
    </div>
    <div class="hint">Unreachable right now? That's fine — finish setup and fix it later; a banner
      flags the node as long as it's down. (The test checks the currently active host — saving a
      new one here takes effect after a restart.)</div>
    <div id="w-msg"></div>
    <div class="actions">
      <button class="ghost" type="button" id="w-node-back">&larr; Back</button>
      <button type="button" id="w-node-next">Save &amp; Continue &rarr;</button>
    </div>`;
  document.getElementById('w-node-back').onclick = back;
  document.getElementById('w-test').onclick = async () => {
    const r = document.getElementById('w-test-result');
    r.textContent = 'Testing…'; r.style.color = '#9090b0';
    try {
      const p = await api('/api/node/ping');
      r.textContent = p.reachable ? '✓ reachable' : '✗ not reachable';
      r.style.color = p.reachable ? '#3ecf8e' : '#f87171';
    } catch (e) {
      r.textContent = 'check failed'; r.style.color = '#f87171';
    }
  };
  document.getElementById('w-node-next').onclick = async () => {
    const host = document.getElementById('w-host').value.trim();
    const user = document.getElementById('w-user').value.trim();
    const msg = document.getElementById('w-msg');
    msg.innerHTML = '';
    if (!host) { msg.innerHTML = '<div class="err">Enter the GPU node’s host or IP.</div>'; return; }
    try {
      const body = { gpu_host: host };
      if (user) body.ssh_user = user;
      await api('/api/settings/nodes', { method: 'POST', body: JSON.stringify(body) });
      next();
    } catch (e) {
      msg.innerHTML = `<div class="err">${esc(e.message)}</div>`;
    }
  };
  // Best-effort prefill from the live config (GET /api/settings/nodes already exists).
  try {
    const cur = await api('/api/settings/nodes');
    if (cur.gpu_host && cur.gpu_host !== '127.0.0.1') document.getElementById('w-host').value = cur.gpu_host;
    if (cur.ssh_user) document.getElementById('w-user').value = cur.ssh_user;
  } catch (e) { /* prefill is a convenience only */ }
}

/* ── Step 4: opt-in subsystems — pulls systems_registry.snapshot() via the existing
   GET /api/systems, filtered to classify=='toggle' rows that default OFF (i.e. are
   currently 'disabled' — true on a fresh install), narrowed to a handful of common
   ones. Everything else stays off; the full ~40-row board still lives in
   Settings → Systems for anyone who wants it. Writes go through PATCH /api/settings. ── */
const COMMON_TOGGLES = [
  'nsfw_enabled',
  'security_monitor_enabled',
  'world_crypto_mining_enabled',
  'pearl_mining_enabled',
  'xmr_mining_enabled',
];

async function renderToggles() {
  document.getElementById('wizard-root').innerHTML = `
    <div class="hint">These default OFF and stay off unless you flip them here — everything else
      (about 40 more optional systems) is available any time in Settings → Systems.</div>
    <div id="w-toggle-list">Loading…</div>
    <div class="actions">
      <button class="ghost" type="button" id="w-tog-back">&larr; Back</button>
      <button type="button" id="w-tog-next">Continue &rarr;</button>
    </div>`;
  document.getElementById('w-tog-back').onclick = back;
  document.getElementById('w-tog-next').onclick = async () => {
    const on = Object.entries(state.toggles).filter(([, v]) => v).map(([k]) => k);
    if (on.length) {
      const patch = {};
      on.forEach(k => { patch[k] = '1'; });
      try { await api('/api/settings', { method: 'PATCH', body: JSON.stringify(patch) }); } catch (e) { /* non-fatal */ }
    }
    next();
  };
  try {
    const data = await api('/api/systems');
    const rows = (data.systems || []).filter(s =>
      s.classify === 'toggle' && s.status === 'disabled' &&
      s.setting_key && COMMON_TOGGLES.includes(s.setting_key));
    const list = document.getElementById('w-toggle-list');
    if (!rows.length) {
      list.innerHTML = '<div class="hint">Nothing to opt into right now — see Settings → Systems any time.</div>';
      return;
    }
    list.innerHTML = rows.map(s => `
      <div class="toggle-row">
        <div><div class="t">${esc(s.label)}</div><div class="d">${esc(s.notes || '')}</div></div>
        <label class="switch"><input type="checkbox" data-key="${esc(s.setting_key)}"><span class="slider"></span></label>
      </div>`).join('');
    list.querySelectorAll('input[type=checkbox]').forEach(cb => {
      cb.onchange = () => { state.toggles[cb.dataset.key] = cb.checked; };
    });
  } catch (e) {
    document.getElementById('w-toggle-list').innerHTML = `<div class="err">Couldn't load systems: ${esc(e.message)}</div>`;
  }
}

/* ── Step 5: health check — the existing app.health.pulse() (cached, fully defended)
   via GET /api/health/pulse, rendered as a pass/fail table before finishing. ── */
async function renderHealth() {
  document.getElementById('wizard-root').innerHTML = `
    <div class="hint">A quick pulse check of everything the Store leans on.</div>
    <div id="w-health">Checking…</div>
    <div class="actions">
      <button class="ghost" type="button" id="w-health-back">&larr; Back</button>
      <button type="button" id="w-health-next">Continue &rarr;</button>
    </div>`;
  document.getElementById('w-health-back').onclick = back;
  document.getElementById('w-health-next').onclick = next;
  try {
    const pulse = await api('/api/health/pulse');
    const rows = (pulse.components || []).map(c => `
      <tr><td>${esc(c.label)}</td><td><span class="pill ${esc(c.status)}">${esc(c.status)}</span></td>
      <td style="color:#7a7a95;">${esc(c.detail || '')}</td></tr>`).join('');
    document.getElementById('w-health').innerHTML = rows
      ? `<table class="health"><tbody>${rows}</tbody></table>`
      : '<div class="hint">No components reported.</div>';
  } catch (e) {
    document.getElementById('w-health').innerHTML = `<div class="err">Health check failed: ${esc(e.message)}</div>`;
  }
}

/* ── Step 6: finish — writes setup_complete=1 (sticky) via PATCH /api/settings so
   the wizard never resurfaces (see auth_core.needs_setup()). ── */
function renderFinish() {
  document.getElementById('wizard-root').innerHTML = `
    <div class="ok">You're all set. Everything above can be changed any time in Settings.</div>
    <div class="actions">
      <button class="ghost" type="button" id="w-fin-back">&larr; Back</button>
      <button type="button" id="w-fin-btn">Finish &amp; Go to Dashboard &rarr;</button>
    </div>`;
  document.getElementById('w-fin-back').onclick = back;
  document.getElementById('w-fin-btn').onclick = finishSetup;
}

document.getElementById('skip-link').onclick = finishSetup;
render();

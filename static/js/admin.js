/* Admin / System settings panel: server config, compute nodes, content flags,
   GPU node — rendered as one full-width collapsible <details class="settings-group">
   block per concern, stacked inside #admin-panel-slot by renderSettings().
   Backups moved to Settings → Backups (pane-backups, loader loadBackups below);
   Store Logs moved to Settings → Systems (settings-systems.js calls loadStoreLogs). */

function _fmtBytes(n) {
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(0) + ' KB';
  if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MB';
  return (n / 1073741824).toFixed(2) + ' GB';
}
function _fmtTime(sec) {
  try { return new Date(sec * 1000).toLocaleString(); } catch { return ''; }
}

async function mountAdminPanel() {
  const slot = document.getElementById('admin-panel-slot');
  if (!slot) return;
  let sv = {}, nodes = {}, settings = {};
  try { sv = await api('/api/settings/server'); } catch {}
  try { nodes = await api('/api/settings/nodes'); } catch {}
  try { settings = await api('/api/settings'); } catch {}

  slot.innerHTML = `
    <details class="settings-group" open>
    <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#128421;&#65039; Server / Host</summary>
    <div style="font-size:.75rem;color:var(--muted);margin:10px 0;">Identity &amp; location. Saved to <code>.env</code>; a restart applies them.</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:0 18px;">
      <div class="field"><label>App Name ${hlp('The name shown in the app’s title/header. Cosmetic branding for this instance. Written to .env; takes effect after a restart.')}</label><input type="text" id="sv-name" value="${esc(sv.app_name||'')}"></div>
      <div class="field"><label>Port ${hlp('The TCP port uvicorn serves on (default 8787). Must match your nginx/reverse-proxy config or the site won’t load. Change only if the port conflicts. Needs a restart.')}</label><input type="text" id="sv-port" value="${esc(String(sv.port||''))}"></div>
      <div class="field"><label>URL Base Path <span style="color:var(--muted)">(reverse-proxy prefix, "" = root)</span> ${hlp('The path prefix the app is served under (here: /store). It must match the reverse-proxy route. Getting it wrong breaks all JS/CSS/API links. Leave “/store” unless you re-map the proxy. Needs a restart.')}</label><input type="text" id="sv-base" value="${esc(sv.base_path||'')}" placeholder="/store"></div>
      <div class="field"><label>Data Directory <span style="color:var(--muted)">(db, designs, videos, backups)</span> ${hlp('Absolute folder where the SQLite DB, generated designs/videos, and backups live. Move it to a bigger disk if you’re low on space. Point it at an EXISTING copy to migrate. Needs a restart.')}</label><input type="text" id="sv-data" value="${esc(sv.data_dir||'')}"></div>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px;">
      <button class="btn-sm primary" onclick="saveServerSettings()">&#128190; Save (needs restart)</button>
      <button class="btn-sm" onclick="systemRestart()">&#128260; Restart Server</button>
      <button class="btn-sm" onclick="browserReset()" title="Fix a stuck automation browser (stale Chrome lock)">&#128295; Fix Browser Lock</button>
      <a class="btn-sm" href="${API}/logout" style="text-decoration:none;color:#f87171;border-color:rgba(239,68,68,.4);">&#128275; Sign Out</a>
    </div>
    <div id="sv-msg" style="font-size:.75rem;margin-top:8px;"></div>
    <div id="gpu-busy-banner" style="margin-top:8px;"></div>
    </details>

    <details class="settings-group">
    <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#129513; Compute Nodes / Model Hosts</summary>
    <div style="font-size:.75rem;color:var(--muted);margin:10px 0;">Where each kind of model runs. Point these at any machine on your network. Saved to <code>.env</code>; a restart applies them. Image &amp; Video share the ComfyUI host.</div>
    <div class="field"><label>&#129504; LLM &mdash; LM Studio URL <span style="color:var(--muted)">(text / prompts / listings)</span> ${hlp('The OpenAI-compatible endpoint of LM Studio on your GPU box. Every text task — prompt enhance, listing copy, research, haggling, the assistant — calls this. If it’s wrong/unreachable, all those features fail. Include the /v1 suffix. Needs a restart.')}</label>
      <input type="text" id="nd-llm" value="${esc(nodes.llm_url||'')}" placeholder="http://127.0.0.1:1234/v1"></div>
    <div class="field"><label>&#129504; LLM model <span style="color:var(--muted)">(used for prompts, listings, haggling, enhance)</span> ${hlp('Which model (already loaded in LM Studio on the GPU box) every text task uses — prompt enhance, listing copy, haggling, the assistant. The list is read live from that node; pick one and click Use. Applies on the next task, no restart. For adult content pick an uncensored model.')}</label>
      <div style="display:flex;gap:6px;align-items:center;">
        <select id="llm-model-select" style="flex:1;padding:7px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;"><option>Loading&hellip;</option></select>
        <button class="btn-sm primary" onclick="saveLlmModel()" title="Set the selected model as the app's LLM. Takes effect on the next text task; no restart.">&#128190; Use</button>
        <button class="btn-sm" onclick="loadLlmModels()" title="Refresh model list from LM Studio">&#128260;</button>
      </div>
      <div id="llm-model-msg" style="font-size:.72rem;color:var(--muted);margin-top:3px;"></div></div>
    <div class="field"><label>&#128273; LM Studio API key <span style="color:var(--muted)">(if the node requires one — locks the LLM to authorized callers)</span> ${hlp('Only needed if LM Studio on the GPU box is set to require a key. Sent as a Bearer token on every LLM call. Leave blank for an open LAN node. Stored locally; applies on the next call, no restart.')}</label>
      <div style="display:flex;gap:6px;">
        <input type="password" id="llm-api-key" value="${esc(settings.lmstudio_api_key||'')}" placeholder="sk-lm-xxxx:yyyy" style="flex:1;padding:7px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;">
        <button class="btn-sm primary" onclick="saveLlmKey()">&#128190; Save</button>
      </div>
      <div id="llm-key-msg" style="font-size:.72rem;color:var(--muted);margin-top:3px;"></div></div>
    <div class="field"><label>&#127912; Image &amp; &#127916; Video &mdash; ComfyUI URL ${hlp('The ComfyUI server on your GPU box (default port 8188). Drives ALL image and video generation. If unreachable, generating and the GPU queue break. Image and video share this one host.')}</label>
      <input type="text" id="nd-comfy" value="${esc(nodes.comfyui_url||'')}" placeholder="http://127.0.0.1:8188"></div>
    <div style="display:flex;gap:8px;">
      <div class="field" style="flex:2;"><label>&#129513; 3D node &mdash; host / IP <span style="color:var(--muted)">(SSH: 3D gen, installs)</span> ${hlp('IP/hostname the app SSHes into to run image→3D generation and to install models on the box. Used for 3D Studio → Generate and the model Install/Test buttons. Requires key-based SSH as the user below.')}</label>
        <input type="text" id="nd-host" value="${esc(nodes.gpu_host||'')}" placeholder="127.0.0.1"></div>
      <div class="field" style="flex:1;"><label>SSH user ${hlp('The Linux username the app logs in as over SSH on the 3D node. Must have passwordless (key) SSH set up and permission to run the model tooling.')}</label>
        <input type="text" id="nd-user" value="${esc(nodes.ssh_user||'')}" placeholder="user"></div>
    </div>
    <div class="field"><label>&#127925; Audio / Music URL <span style="color:var(--muted)">(optional / future)</span> ${hlp('Optional endpoint for a dedicated audio/music service. The audio engines currently run via the GPU node, so this is usually left blank / for future use.')}</label>
      <input type="text" id="nd-audio" value="${esc(nodes.audio_url||'')}" placeholder="http://127.0.0.1:XXXX"></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px;">
      <button class="btn-sm primary" onclick="saveNodeSettings()">&#128190; Save (needs restart)</button>
      <button class="btn-sm" onclick="systemRestart()">&#128260; Restart Server</button>
    </div>
    <div id="nd-msg" style="font-size:.75rem;margin-top:8px;"></div>

    <div class="field" style="margin-top:14px;"><label>&#129303; HuggingFace token <span style="color:var(--muted)">(for gated models e.g. Stable Fast 3D)</span> ${hlp('A HuggingFace read token so the GPU box can download license-gated models (accept the model license on huggingface.co first). Used during node Deploy and 3D installs. Applies immediately, no restart.')}</label>
      <div style="display:flex;gap:6px;">
        <input type="password" id="hf-token" value="${esc(settings.hf_token||'')}" placeholder="hf_xxx (accept the model license on huggingface.co first)" style="flex:1;">
        <button class="btn-sm primary" onclick="saveHfToken()">&#128190; Save</button>
      </div></div>
    <div id="hf-msg" style="font-size:.75rem;margin-top:4px;"></div>
    </details>

    <details class="settings-group">
    <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#127859; Content</summary>
    <label style="display:flex;gap:10px;align-items:flex-start;cursor:pointer;padding:10px;border:1px solid var(--border);border-radius:8px;margin-top:10px;">
      <input type="checkbox" id="nsfw-toggle" ${(settings.nsfw_enabled||'').toString().toLowerCase().match(/^(1|true|on|yes)$/)?'checked':''} onchange="saveNsfw()" style="margin-top:3px;">
      <span><b>&#128286; NSFW mode (master)</b><br><span style="color:var(--muted);font-size:.78rem;">Enables the Private Studio (adult content across image, video, audio &amp; 3D) and un-censors the local LLM. Off by default: everything NSFW is disabled and invisible — no tab, no routes, world integration dormant. A non-configurable safety floor always refuses content involving minors and real-person deepfakes; the toggleable content filter below covers non-consent themes.</span></span>
    </label>
    <label style="display:flex;gap:10px;align-items:flex-start;cursor:pointer;padding:10px;border:1px solid var(--border);border-radius:8px;margin-top:8px;">
      <input type="checkbox" id="nsfw-display-toggle" ${(settings.nsfw_display||'').toString().toLowerCase().match(/^(1|true|on|yes)$/)?'checked':''} onchange="saveNsfwFlag('nsfw_display','nsfw-display-toggle')" style="margin-top:3px;">
      <span><b>&#128065;&#65039; Show private content</b><br><span style="color:var(--muted);font-size:.78rem;">Display toggle — separate from the master. Off: the Private Studio tab is hidden and ALL private content is redacted from every surface (galleries, queue labels, listings) so you can screen-share safely; submitted jobs still run and archive. Never turns on by itself.</span></span>
    </label>
    <label style="display:flex;gap:10px;align-items:flex-start;cursor:pointer;padding:10px;border:1px solid var(--border);border-radius:8px;margin-top:8px;">
      <input type="checkbox" id="nsfw-world-toggle" ${(settings.nsfw_world||'').toString().toLowerCase().match(/^(1|true|on|yes)$/)?'checked':''} onchange="saveNsfwFlag('nsfw_world','nsfw-world-toggle')" style="margin-top:3px;">
      <span><b>&#127749; Company world may use the studio</b><br><span style="color:var(--muted);font-size:.78rem;">With the master on, world agents occasionally take on a "private studio commission" (an NSFW-flagged job in the normal pipeline). Town-feed/journal lines about it are ALWAYS generic PG-13 — the content itself only ever lives in the Private Studio.</span></span>
    </label>
    <label style="display:flex;gap:10px;align-items:flex-start;cursor:pointer;padding:10px;border:1px solid var(--border);border-radius:8px;margin-top:8px;">
      <input type="checkbox" id="nsfw-filter-toggle" ${String(settings.nsfw_content_filter ?? '1').toLowerCase().match(/^(1|true|on|yes)$/)?'checked':''} onchange="saveNsfwFlag('nsfw_content_filter','nsfw-filter-toggle')" style="margin-top:3px;">
      <span><b>&#129529; Content filter (soft)</b><br><span style="color:var(--muted);font-size:.78rem;">On by default. Blocks the fuzzy extra stuff — non-consent themes + over-broad keyword matches that can wrongly refuse legitimate adult prompts. Turn OFF for full discretion on your own private content. The hard floor — <b>minors</b> and <b>real-person deepfakes</b> — is ALWAYS enforced regardless of this toggle.</span></span>
    </label>
    <div id="nsfw-msg" style="font-size:.72rem;color:var(--muted);margin-top:4px;"></div>
    </details>

    <details class="settings-group">
    <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#128421;&#65039; GPU Node</summary>
    <div style="font-size:.75rem;color:var(--muted);margin:10px 0;">Provision &amp; health-check the GPU box — image (ComfyUI), video, 3D, audio/music, LM Studio, and the autostart services. Deploy downloads any missing dependencies.</div>
    <div id="node-panel" style="font-size:.8rem;color:var(--muted);">Checking node&hellip;</div>
    </details>
  `;
  loadNodePanel();
  refreshGpuBusy();
  loadLlmModels();
}

let _logAutoTimer = null;
function _colorLogLine(l) {
  const e = document.createElement('div');
  e.textContent = l;
  if (/ ERROR | CRITICAL /.test(l)) e.style.color = '#f87171';
  else if (/ WARNING /.test(l)) e.style.color = '#fbbf24';
  else if (/ DEBUG /.test(l)) e.style.color = '#6b7280';
  return e.outerHTML;
}
async function loadStoreLogs() {
  const pre = document.getElementById('store-logs');
  const tally = document.getElementById('log-tally');
  if (!pre) return;
  const level = document.getElementById('log-level')?.value || '';
  const q = document.getElementById('log-search')?.value.trim() || '';
  try {
    const r = await api(`/api/system/logs?lines=400&level=${encodeURIComponent(level)}&q=${encodeURIComponent(q)}`);
    if (r.note) { pre.textContent = r.note; if (tally) tally.textContent = ''; return; }
    const lines = r.lines || [];
    pre.innerHTML = lines.length ? lines.map(_colorLogLine).join('') : '<span style="color:var(--muted)">(no matching lines)</span>';
    pre.scrollTop = pre.scrollHeight;
    if (tally) tally.innerHTML = `<span style="color:#f87171">${r.errors||0} err</span> · <span style="color:#fbbf24">${r.warnings||0} warn</span>`;
  } catch (e) { pre.textContent = 'Error loading logs: ' + e.message; }
}
window.loadStoreLogs = loadStoreLogs;

function toggleLogAuto() {
  const on = document.getElementById('log-auto')?.checked;
  if (_logAutoTimer) { clearInterval(_logAutoTimer); _logAutoTimer = null; }
  if (on) _logAutoTimer = setInterval(loadStoreLogs, 3000);
}
window.toggleLogAuto = toggleLogAuto;

async function loadLlmModels() {
  const sel = document.getElementById('llm-model-select');
  const msg = document.getElementById('llm-model-msg');
  if (!sel) return;
  sel.innerHTML = '<option>Loading…</option>';
  try {
    const d = await api('/api/settings/llm-models');
    if (!d.models || !d.models.length) {
      sel.innerHTML = `<option value="${esc(d.current || '')}">${esc(d.current || '(none)')}</option>`;
      if (msg) { msg.style.color = 'var(--warn)'; msg.textContent = d.error || 'No models found on LM Studio.'; }
      return;
    }
    sel.innerHTML = d.models.map(m => `<option value="${esc(m)}"${m === d.current ? ' selected' : ''}>${esc(m)}</option>`).join('');
    if (msg) { msg.style.color = 'var(--muted)'; msg.textContent = `In use: ${d.current}`; }
  } catch (e) { if (msg) { msg.style.color = 'var(--warn)'; msg.textContent = 'Error: ' + e.message; } }
}
window.loadLlmModels = loadLlmModels;

async function saveNsfw() {
  const on = document.getElementById('nsfw-toggle')?.checked;
  const msg = document.getElementById('nsfw-msg');
  try {
    await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ nsfw_enabled: on ? 'true' : '' }) });
    if (msg) { msg.style.color = on ? '#f59e0b' : 'var(--green)'; msg.textContent = on ? '🔞 NSFW mode ON — Private Studio active (show it with the display toggle below). Safety floor stays on.' : '✓ NSFW off — everything private is disabled and invisible.'; }
    toast(on ? 'NSFW mode enabled' : 'NSFW mode disabled');
    if (typeof updateNsfwNav === 'function') updateNsfwNav();
  } catch (e) { if (msg) { msg.style.color = 'var(--warn)'; msg.textContent = 'Error: ' + e.message; } }
}
window.saveNsfw = saveNsfw;

// Generic saver for the display/world sub-toggles (each is an explicit click).
async function saveNsfwFlag(key, elId) {
  const on = document.getElementById(elId)?.checked;
  const msg = document.getElementById('nsfw-msg');
  try {
    await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ [key]: on ? 'true' : '' }) });
    const what = key === 'nsfw_display' ? 'Private content display' : 'Company-world studio access';
    if (msg) { msg.style.color = on ? '#f59e0b' : 'var(--green)'; msg.textContent = `${on ? '🔞' : '✓'} ${what} ${on ? 'ON' : 'OFF'}.`; }
    toast(`${what} ${on ? 'on' : 'off'}`);
    if (typeof updateNsfwNav === 'function') updateNsfwNav();
  } catch (e) { if (msg) { msg.style.color = 'var(--warn)'; msg.textContent = 'Error: ' + e.message; } }
}
window.saveNsfwFlag = saveNsfwFlag;

async function saveLlmKey() {
  const k = document.getElementById('llm-api-key')?.value.trim() || '';
  const msg = document.getElementById('llm-key-msg');
  try {
    await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ lmstudio_api_key: k }) });
    if (msg) { msg.style.color = 'var(--green)'; msg.textContent = k ? '✓ Saved — sent as Bearer token on the next LLM call.' : '✓ Cleared (no auth header).'; }
    toast('LM Studio API key saved');
  } catch (e) { if (msg) { msg.style.color = 'var(--warn)'; msg.textContent = 'Error: ' + e.message; } }
}
window.saveLlmKey = saveLlmKey;

async function saveLlmModel() {
  const sel = document.getElementById('llm-model-select');
  const msg = document.getElementById('llm-model-msg');
  const m = sel && sel.value;
  if (!m) return;
  try {
    await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ enhance_model: m }) });
    if (msg) { msg.style.color = 'var(--green)'; msg.textContent = `✓ LLM model set to ${m} — applies to the next task.`; }
    toast('LLM model set: ' + m);
  } catch (e) { if (msg) { msg.style.color = 'var(--warn)'; msg.textContent = 'Error: ' + e.message; } }
}
window.saveLlmModel = saveLlmModel;

// Live "GPU busy" indicator — polls while the Settings panel is open so both people/
// agents can see when it's safe to restart (a restart kills in-flight generations).
let _gpuBusyTimer = null;
async function refreshGpuBusy() {
  const el = document.getElementById('gpu-busy-banner');
  if (!el) { if (_gpuBusyTimer) { clearTimeout(_gpuBusyTimer); _gpuBusyTimer = null; } return; }
  let s = { busy: false, total: 0, jobs: [] };
  try { s = await api('/api/system/gpu-status'); } catch {}
  const restartBtns = document.querySelectorAll('button[onclick="systemRestart()"]');
  if (s.busy) {
    const detail = (s.jobs || []).map(j => `${j.count} ${j.kind}`).join(', ');
    el.innerHTML = `<div style="background:#2a1005;border:1px solid #f59e0b80;border-radius:8px;padding:8px 10px;font-size:.78rem;color:#fcd34d">
      &#9203; <b>GPU busy — ${s.total} job(s) running/queued</b>${detail ? ' ('+esc(detail)+')' : ''}.
      Restarting now will kill them — wait for them to finish.</div>`;
    restartBtns.forEach(b => { b.style.opacity = '.55'; b.title = `${s.total} GPU job(s) in flight — restart will kill them`; });
  } else {
    el.innerHTML = `<div style="font-size:.74rem;color:var(--green)">&#10003; GPU idle — safe to restart.</div>`;
    restartBtns.forEach(b => { b.style.opacity = ''; b.title = ''; });
  }
  if (_gpuBusyTimer) clearTimeout(_gpuBusyTimer);
  _gpuBusyTimer = setTimeout(refreshGpuBusy, 4000);
}
window.refreshGpuBusy = refreshGpuBusy;

window.mountAdminPanel = mountAdminPanel;

async function saveServerSettings() {
  const msg = document.getElementById('sv-msg');
  const body = {
    app_name:  document.getElementById('sv-name').value.trim(),
    port:      document.getElementById('sv-port').value.trim(),
    base_path: document.getElementById('sv-base').value.trim(),
    data_dir:  document.getElementById('sv-data').value.trim(),
  };
  try {
    await api('/api/settings/server', { method: 'POST', body: JSON.stringify(body) });
    msg.style.color = 'var(--green)';
    msg.innerHTML = '&#10003; Saved to .env. Click <b>Restart Server</b> to apply.';
    toast('Server settings saved — restart to apply');
  } catch (e) {
    msg.style.color = 'var(--warn)';
    msg.textContent = 'Error: ' + e.message;
  }
}
window.saveServerSettings = saveServerSettings;

async function saveNodeSettings() {
  const msg = document.getElementById('nd-msg');
  const body = {
    llm_url:     document.getElementById('nd-llm').value.trim(),
    comfyui_url: document.getElementById('nd-comfy').value.trim(),
    gpu_host:    document.getElementById('nd-host').value.trim(),
    ssh_user:    document.getElementById('nd-user').value.trim(),
    audio_url:   document.getElementById('nd-audio').value.trim(),
  };
  try {
    await api('/api/settings/nodes', { method: 'POST', body: JSON.stringify(body) });
    msg.style.color = 'var(--green)';
    msg.innerHTML = '&#10003; Saved to .env. Click <b>Restart Server</b> to apply.';
    toast('Node settings saved — restart to apply');
  } catch (e) {
    msg.style.color = 'var(--warn)';
    msg.textContent = 'Error: ' + e.message;
  }
}
window.saveNodeSettings = saveNodeSettings;

async function saveHfToken() {
  const msg = document.getElementById('hf-msg');
  try {
    await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ hf_token: document.getElementById('hf-token').value.trim() }) });
    msg.style.color = 'var(--green)'; msg.innerHTML = '&#10003; Saved — applies immediately (no restart).';
    toast('HuggingFace token saved');
  } catch (e) { msg.style.color = 'var(--warn)'; msg.textContent = 'Error: ' + e.message; }
}
window.saveHfToken = saveHfToken;

async function loadBackups() {
  const el = document.getElementById('backups-list');
  if (!el) return;
  try {
    const data = await api('/api/system/backups');
    const list = data.backups || [];
    if (!list.length) { el.innerHTML = '<div class="empty" style="padding:12px;">No backups yet.</div>'; return; }
    el.innerHTML = list.map(b => `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:8px;margin-bottom:6px;">
        <div>
          <div style="color:var(--text);font-weight:600;">${esc(b.name)}${b.kind === 'db'
            ? ' <span style="font-size:.62rem;padding:1px 6px;border-radius:8px;background:rgba(120,150,205,.15);color:#9fb4d8;" title="Nightly database snapshot (automatic). Download it, or restore by unpacking over store.db while the server is stopped.">DB snapshot</span>'
            : ''}</div>
          <div style="font-size:.7rem;">${_fmtBytes(b.size)} &middot; ${_fmtTime(b.mtime)}</div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0;">
          <a class="btn-sm" href="${API}/api/system/backups/${encodeURIComponent(b.name)}/download" title="Download">&#11015;</a>
          ${b.kind === 'db' ? '' : `<button class="btn-sm" onclick="restoreBackup('${esc(b.name)}')" title="Restore">&#8635;</button>`}
          <button class="btn-sm" onclick="deleteBackup('${esc(b.name)}')" title="Delete" style="color:#f87171;">&#128465;</button>
        </div>
      </div>`).join('');
  } catch (e) {
    el.innerHTML = `<div style="color:var(--warn);">Error loading backups: ${esc(e.message)}</div>`;
  }
}
window.loadBackups = loadBackups;

async function createBackup() {
  const btn = document.getElementById('bk-create');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Creating…'; }
  try {
    const r = await api('/api/system/backup', { method: 'POST' });
    toast('Backup created: ' + r.name);
    await loadBackups();
  } catch (e) {
    toast('Backup failed: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '&#10133; Create Backup'; }
  }
}
window.createBackup = createBackup;

async function deleteBackup(name) {
  if (!confirm('Delete backup ' + name + '?')) return;
  try {
    await api('/api/system/backups/' + encodeURIComponent(name), { method: 'DELETE' });
    toast('Deleted');
    await loadBackups();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.deleteBackup = deleteBackup;

async function restoreBackup(name) {
  if (!confirm('Restore from ' + name + '?\n\nThis overwrites the current app and data, then restarts. A safety backup of the current state is taken first.')) return;
  try {
    await api('/api/system/restore', { method: 'POST', body: JSON.stringify({ name }) });
  } catch (e) { /* connection may drop during restart */ }
  toast('Restoring & restarting…');
  let tries = 0;
  const wait = setInterval(async () => {
    tries++;
    try { await api('/api/status'); clearInterval(wait); toast('Restored — reloading'); setTimeout(() => location.reload(), 800); }
    catch { if (tries > 40) { clearInterval(wait); toast('Server did not come back — check logs', 'error'); } }
  }, 1000);
}
window.restoreBackup = restoreBackup;

async function browserReset() {
  const msg = document.getElementById('sv-msg');
  if (msg) { msg.style.color = 'var(--muted)'; msg.textContent = 'Cleaning up the automation browser…'; }
  try {
    const r = await api('/api/system/browser-reset', { method: 'POST' });
    if (msg) { msg.style.color = 'var(--green)'; msg.textContent = `✓ Browser reset — cleared ${(r.removed_locks||[]).length} lock file(s).`; }
    toast('Browser lock cleared');
  } catch (e) { if (msg) { msg.style.color = 'var(--warn)'; msg.textContent = '❌ ' + e.message; } }
}
window.browserReset = browserReset;

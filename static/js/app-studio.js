'use strict';

/* ── STUDIO HUB ──
   Image / Video / Audio / 3D / Models / Queue consolidated into ONE tab with a
   sub-tab bar. Each sub reuses its existing render function, which now mounts into
   #studio-content via viewRoot() (falls back to #main-content when standalone).
   _currentView is set to the sub's LEGACY view name so existing pollers/guards
   (e.g. `_currentView === 'models3d'`) keep working unchanged. */
// Each engine sub-tab renders its generator, then its own model catalog is appended
// underneath (appendStudioModels). There is no standalone "Models" sub-tab anymore.
const STUDIO_SUBS = [
  { k:'image',  view:'image-gen', label:'\u{1F3A8} Image',  fn: () => renderImageGenerator() },
  { k:'video',  view:'videos',    label:'\u{1F3AC} Video',  fn: () => renderVideos() },
  { k:'audio',  view:'audio',     label:'\u{1F3B5} Audio',  fn: () => renderAudio() },
  { k:'3d',     view:'models3d',  label:'\u{1F9E9} 3D',     fn: () => renderModels3D() },
  { k:'director', view:'director', label:'\u{1F39E}️ Director', fn: () => renderDirector() },
  { k:'brand',  view:'brandkit',  label:'\u{1F3AF} Brand',  fn: () => renderBrandKit() },
  { k:'gpu',    view:'studio',    label:'⚡ Queue',    fn: () => renderStudioQueue() },
];
const _STUDIO_MODEL_SUBS = ['image', 'video', 'audio', '3d'];
let _studioSub = 'image';
async function renderStudio(sub) {
  if (sub) _studioSub = sub;
  const cur = STUDIO_SUBS.find(s => s.k === _studioSub) || STUDIO_SUBS[0];
  _studioSub = cur.k;   // normalize an unknown/legacy sub (e.g. 'models') to a real one
  const bar = STUDIO_SUBS.map(s =>
    `<div class="subtab${s.k === cur.k ? ' active' : ''}" onclick="studioSub('${s.k}')">${s.label}</div>`
  ).join('');
  // Stop any playing player before the sub-tab swap — a detached media element
  // can keep its audio playing after its view is gone (tab-videos.js helper).
  if (typeof stopMediaIn === 'function') stopMediaIn(document.getElementById('main-content'));
  document.getElementById('main-content').innerHTML = `
    <div class="view-header">
      <div class="view-title">&#127917; Studio</div>
      <div class="view-sub">Create images, video, audio &amp; 3D &mdash; and manage the models that power them.</div>
    </div>
    <div class="subtab-bar">${bar}</div>
    <div id="studio-content"><div class="empty"><div class="empty-icon">&#9203;</div>Loading&#8230;</div></div>`;
  // header + active nav reflect Studio even on a deep-link; _currentView tracks the sub.
  const tt = document.getElementById('topbar-title'); if (tt) tt.textContent = 'Studio';
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === 'studio'));
  _currentView = cur.view;
  await cur.fn();
  // Append this engine's model catalog (checkpoints / video / audio / 3D) underneath.
  if (_STUDIO_MODEL_SUBS.includes(cur.k) && typeof appendStudioModels === 'function') {
    await appendStudioModels(cur.k);
  }
}
async function studioSub(k) {
  await renderStudio(k);
  bindCards();
}

/* ── MODELS ── */
async function loadModels() {
  try {
    const data = await api('/api/models');
    const installed = data.installed || [];
    const rec = data.recommended || [];
    const labelMap = {};
    for (const m of rec) labelMap[m.filename] = m.label || m.filename;
    const opts = installed.length
      ? installed.map(f => `<option value="${esc(f)}">${esc(labelMap[f] || f)}</option>`).join('')
      : '<option value="">Default Model</option>';
    // Populate any model selectors currently in DOM (image-gen tab)
    ['qg-model','ig-model'].forEach(id => {
      const sel = document.getElementById(id);
      if (sel) sel.innerHTML = opts;
    });
  } catch {
    ['qg-model','ig-model'].forEach(id => {
      const sel = document.getElementById(id);
      if (sel) sel.innerHTML = '<option value="">Default Model</option>';
    });
  }
}

/* ══ GPU QUEUE ══ */
async function renderGpuQueue() {
  const st = await api('/api/status');
  const qLen = (st.queue || []).length;
  const busy = st.running || qLen > 0;
  let h = `
    <div class="view-header"><div class="view-title">&#9889; GPU Queue</div><div class="view-sub">Orchestrator and generation status</div></div>
    <div class="queue-card">
      <div class="queue-status-row">
        <span style="font-weight:700;font-size:.95rem;">Orchestrator</span>
        <span class="queue-status-badge ${busy?'running':'idle'}">${busy?'Running':'Idle'}</span>
      </div>`;

  if (st.current_task) {
    h += `<div style="font-size:.8rem;color:var(--muted);margin-bottom:8px;">Current: ${esc((st.current_task.prompt||st.current_task.type||'Unknown').slice(0,100))}</div>`;
  }

  if (qLen > 0) {
    h += `<div style="font-size:.82rem;font-weight:600;margin-bottom:8px;">Queue (${qLen})</div>`;
    for (const item of st.queue) {
      h += `<div class="gen-bar"><div class="gen-bar-pulse"></div><div class="gen-bar-label">${esc((item.prompt||item.type||'Task').slice(0,80))}</div><div class="gen-bar-model">${esc(item.model||item.status||'')}</div></div>`;
    }
  } else {
    h += `<div style="font-size:.8rem;color:var(--muted);">Queue is empty</div>`;
  }
  h += `</div>`;

  try {
    const gens = await api('/api/generations?status=generating');
    if (gens && gens.length) {
      h += `<div class="section-header" style="margin-top:20px;"><div class="section-title">&#127912; Active Generations (${gens.length})</div></div><div class="gen-bars">`;
      for (const g of gens)
        h += `<div class="gen-bar"><div class="gen-bar-pulse"></div><div class="gen-bar-label">${esc((g.prompt||'').slice(0,80))}</div><div class="gen-bar-model">${esc(g.model||'')} &middot; ${esc(g.product_type||'')}</div></div>`;
      h += `</div>`;
    }
  } catch {}

  document.getElementById('main-content').innerHTML = h;
}

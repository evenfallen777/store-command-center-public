'use strict';

/* ── PRIVATE STUDIO (NSFW) TAB ──
   Home for ALL nsfw-flagged generation work — image / video / audio / 3D sub-tabs
   mirroring the Studio hub, reusing the same pipelines via /api/nsfw/*.
   Visibility is layered (server-enforced; this file only mirrors it):
     - nsfw_enabled (master) off → routes 404, nav item hidden, tab dead.
     - nsfw_display off → nav item hidden + library redacted server-side, even
       though submitted jobs keep running (screen-share safe).
   updateNsfwNav() keeps the sidebar entry in sync; Settings toggles call it. */

let _nsfwSub = 'image';
let _nsfwStatus = null;
let _nsfwPollTimer = null;

async function updateNsfwNav() {
  try { _nsfwStatus = await api('/api/nsfw/status'); }
  catch { _nsfwStatus = { enabled: false, display: false, world: false, visible: false }; }
  const item = document.getElementById('nav-nsfw');
  if (item) item.style.display = _nsfwStatus.visible ? '' : 'none';
  return _nsfwStatus;
}
window.updateNsfwNav = updateNsfwNav;
// initial sidebar sync on load (scripts sit at the end of <body>, DOM is ready)
setTimeout(() => { updateNsfwNav().catch(() => {}); }, 300);

const NSFW_SUBS = [
  { k: 'image', label: '\u{1F3A8} Image' },
  { k: 'video', label: '\u{1F3AC} Video' },
  { k: 'audio', label: '\u{1F3B5} Audio' },
  { k: '3d',    label: '\u{1F9E9} 3D' },
];

async function renderNsfw() {
  if (_nsfwPollTimer) { clearTimeout(_nsfwPollTimer); _nsfwPollTimer = null; }
  const st = await updateNsfwNav();
  const main = document.getElementById('main-content');
  if (!st.visible) {
    main.innerHTML = `<div class="empty"><div class="empty-icon">&#128274;</div>
      Private Studio is ${st.enabled ? 'hidden (display toggle is off)' : 'disabled'}.<br>
      <span style="font-size:.8rem;color:var(--muted);">Enable it in Settings &rarr; Content. ${st.enabled ? 'Jobs keep running while hidden.' : ''}</span></div>`;
    return;
  }
  const bar = NSFW_SUBS.map(s =>
    `<div class="subtab${s.k === _nsfwSub ? ' active' : ''}" onclick="nsfwSub('${s.k}')">${s.label}</div>`).join('');
  main.innerHTML = `
    <div class="view-header">
      <div class="view-title">&#128274; Private Studio</div>
      <div class="view-sub">Adults-only creation across image, video, audio &amp; 3D &mdash; same pipelines, private archive. Content here never appears in the regular galleries or queue labels.</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap;">
      <button class="btn-sm" onclick="nsfwQuickHide()" title="Turn the display toggle off: hides this tab and redacts all private content everywhere (jobs keep running). Re-enable in Settings &rarr; Content.">&#128584; Hide everything (screen-share)</button>
      <span style="font-size:.72rem;color:var(--muted);">World agents: <b>${st.world ? 'may use the studio' : 'off'}</b> &middot; toggles live in Settings &rarr; Content</span>
    </div>
    <div class="subtab-bar">${bar}</div>
    <div id="nsfw-content"><div class="empty"><div class="empty-icon">&#9203;</div>Loading&#8230;</div></div>`;
  await _renderNsfwSub();
}
window.renderNsfw = renderNsfw;

async function nsfwSub(k) { _nsfwSub = k; await renderNsfw(); }
window.nsfwSub = nsfwSub;

async function nsfwQuickHide() {
  if (!confirm('Hide the Private Studio and redact all private content from every surface? Jobs keep running. Re-enable in Settings → Content.')) return;
  await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ nsfw_display: '' }) });
  toast('Private content hidden everywhere');
  await updateNsfwNav();
  switchView('dashboard');
}
window.nsfwQuickHide = nsfwQuickHide;

function _nsfwImgUrl(p) {
  if (!p) return '';
  const i = p.indexOf('/designs/');
  return i >= 0 ? `${API}/designs/${p.slice(i + 9)}` : '';
}
function _nsfwFileUrl(p) {
  if (!p) return '';
  return `${API}/videos/${encodeURIComponent(p.split('/').pop())}`;
}

let _nsfwCats = [];
let _nsfwCatFilter = '';
let _nsfwCatsOpen = false;
let _nsfwModelOptions = '<option value="">Default Model</option>';
let _nsfwImageModel = '';
let _nsfwVideoModels = [];
let _nsfwAudioEngines = [];
let _nsfwGenModels = [];

function _nsfwVideoModelOptionsHtml() {
  return _nsfwVideoModels.length
    ? _nsfwVideoModels.map(m => `<option value="${esc(m.model_id)}"${m.model_id === 'Wan-AI/Wan2.1-T2V-1.3B-Diffusers' ? ' selected' : ''}>${esc(m.label)}</option>`).join('')
    : '<option value="Wan-AI/Wan2.1-T2V-1.3B-Diffusers">Wan2.1 T2V 1.3B (Default)</option>';
}

function _nsfwAudioEngineOptionsHtml() {
  return _nsfwAudioEngines.length
    ? _nsfwAudioEngines.map(e => `<option value="${esc(e.key)}"${e.key === 'musicgen' ? ' selected' : ''}>${esc(e.label)}</option>`).join('')
    : '<option value="musicgen">MusicGen (instrumental, fast)</option>';
}

// Per-engine length options (seconds) — mirrors tab-audio.js's _AUD_DURATIONS.
const _NSFW_AUD_DURATIONS = {
  musicgen:     { opts: [5, 8, 15, 30, 45, 60], rec: 8 },
  musicgen_med: { opts: [5, 8, 15, 30, 45, 60], rec: 8 },
  stable_audio: { opts: [8, 15, 30, 47],         rec: 30 },
  acestep:      { opts: [30, 60, 90, 120, 150, 180, 210, 240], rec: 120 },
};
function _nsfwFmtSecs(s) { return s >= 60 ? `${Math.floor(s / 60)}m${s % 60 ? (' ' + (s % 60) + 's') : ''}` : `${s}s`; }

function _nsfwGenModelOptionsHtml() {
  return _nsfwGenModels.length
    ? _nsfwGenModels.map(m => `<option value="${esc(m.key)}"${m.key === 'triposr' ? ' selected' : ''}>${esc(m.label)}${m.commercial === false ? ' ⚠️' : ''}</option>`).join('')
    : '<option value="triposr">TripoSR (fast, default)</option>';
}

async function _renderNsfwSub() {
  const root = document.getElementById('nsfw-content');
  if (!root) return;
  let lib;
  try { lib = await api('/api/nsfw/library'); }
  catch (e) { root.innerHTML = `<div class="empty">&#10060; ${esc(e.message)}</div>`; return; }
  if (_nsfwSub === 'image') {
    try { _nsfwCats = (await api('/api/nsfw/categories')).categories || []; } catch { _nsfwCats = []; }
    try { _nsfwImageModel = (await api('/api/settings')).nsfw_image_model || ''; } catch { _nsfwImageModel = ''; }
    try {
      const data = await api('/api/models');
      const installed = data.installed || [];
      const rec = data.recommended || [];
      const labelMap = {};
      for (const m of rec) labelMap[m.filename] = m.label || m.filename;
      _nsfwModelOptions = `<option value="" ${_nsfwImageModel ? '' : 'selected'}>Default Model</option>` +
        installed.map(f => `<option value="${esc(f)}" ${f === _nsfwImageModel ? 'selected' : ''}>${esc(labelMap[f] || f)}</option>`).join('');
    } catch {}
  }
  if (_nsfwSub === 'video') {
    try {
      const vms = await api('/api/video-models');
      _nsfwVideoModels = (vms || []).filter(m => m.installed);
    } catch { _nsfwVideoModels = []; }
  }
  if (_nsfwSub === 'audio') {
    try { _nsfwAudioEngines = await api('/api/audio/engines'); } catch { _nsfwAudioEngines = []; }
  }
  if (_nsfwSub === '3d') {
    try {
      const gm = await api('/api/models3d/gen-models');
      _nsfwGenModels = (gm || []).filter(m => m.installed);
    } catch { _nsfwGenModels = []; }
  }
  const forms = {
    image: `
      <div class="card" style="margin-bottom:14px;">
        <label style="font-size:.78rem;">Prompt <span style="color:var(--muted);">(type a rough idea, Enhance expands it — edit before generating)</span></label>
        <textarea id="nsfw-prompt" rows="3" placeholder="Describe the image (adults only)&hellip;" style="width:100%;"></textarea>
        <div style="margin-top:8px;">
          <label style="font-size:.78rem;">&#127912; Image model <span style="color:var(--muted);">(blank = default)</span></label><br>
          <select id="nsfw-model" onchange="nsfwModelChange(this.value)" style="padding:5px 8px;margin-top:2px;">${_nsfwModelOptions}</select>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:8px;">
          <div>
            <label style="font-size:.78rem;">Variations</label><br>
            <select id="nsfw-variations" style="width:100%;padding:5px 8px;margin-top:2px;">
              <option value="1">1 variation</option>
              <option value="2">2 variations</option>
              <option value="3" selected>3 variations</option>
              <option value="4">4 variations</option>
              <option value="6">6 variations</option>
            </select>
          </div>
          <div>
            <label style="font-size:.78rem;">Quality (steps)</label><br>
            <select id="nsfw-steps" style="width:100%;padding:5px 8px;margin-top:2px;">
              <option value="15">15 &mdash; fast</option>
              <option value="20" selected>20 &mdash; balanced</option>
              <option value="30">30 &mdash; best quality</option>
            </select>
          </div>
        </div>
        <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;">
          <button class="btn-sm" id="nsfw-enhance-btn" onclick="nsfwEnhance()">&#10024; Enhance</button>
          <button class="btn-sm primary" onclick="nsfwGenerate()">&#127912; Generate</button>
          <button class="btn-sm" onclick="nsfwGenerateAll()" title="Queue one model-authored creation per category (uses each category's generator prompt).">&#9889; Generate every category</button>
          <button class="btn-sm" id="nsfw-bootstrap-btn" onclick="nsfwBootstrap()" title="Have the NSFW model write/refresh every category's generator prompt. Re-runnable; results stay editable below.">&#129504; Bootstrap prompts</button>
        </div>
      </div>
      ${_nsfwCategoriesHtml()}`,
    video: `
      <div class="card" style="margin-bottom:14px;">
        <label style="font-size:.78rem;">Prompt</label>
        <textarea id="nsfw-vprompt" rows="3" placeholder="Describe the video clip (adults only)&hellip;" style="width:100%;"></textarea>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:8px;">
          <div>
            <label style="font-size:.78rem;">&#127916; Model</label><br>
            <select id="nsfw-vmodel" style="width:100%;padding:5px 8px;margin-top:2px;">${_nsfwVideoModelOptionsHtml()}</select>
          </div>
          <div>
            <label style="font-size:.78rem;">Resolution</label><br>
            <select id="nsfw-vres" style="width:100%;padding:5px 8px;margin-top:2px;">
              <option value="832x480">832&times;480 (16:9 landscape)</option>
              <option value="480x832">480&times;832 (9:16 portrait)</option>
              <option value="512x512">512&times;512 (square)</option>
            </select>
          </div>
          <div>
            <label style="font-size:.78rem;">Duration</label><br>
            <select id="nsfw-vframes" style="width:100%;padding:5px 8px;margin-top:2px;">
              <option value="25">&sim;1.5s (25 frames)</option>
              <option value="49" selected>&sim;3s (49 frames)</option>
              <option value="81">&sim;5s (81 frames)</option>
              <option value="121">&sim;7.5s (121 frames, slow)</option>
            </select>
          </div>
          <div>
            <label style="font-size:.78rem;">Quality (steps)</label><br>
            <select id="nsfw-vsteps" style="width:100%;padding:5px 8px;margin-top:2px;">
              <option value="15">15 &mdash; fast</option>
              <option value="20" selected>20 &mdash; balanced</option>
              <option value="30">30 &mdash; best quality</option>
            </select>
          </div>
        </div>
        <div style="margin-top:8px;font-size:.72rem;color:var(--muted);">
          &#9432; Download additional video models in the <a href="#" onclick="switchView('models');return false;" style="color:var(--accent);">Models tab</a>. First gen with a new model downloads it automatically (~5&ndash;10&nbsp;min).
        </div>
        <div style="display:flex;gap:8px;margin-top:8px;">
          <button class="btn-sm primary" onclick="nsfwVideo()">&#127916; Generate video</button>
        </div>
      </div>`,
    audio: `
      <div class="card" style="margin-bottom:14px;">
        <label style="font-size:.78rem;">Prompt</label>
        <textarea id="nsfw-aprompt" rows="2" placeholder="Describe the music / audio&hellip;" style="width:100%;"></textarea>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:8px;">
          <div>
            <label style="font-size:.78rem;">&#127925; Engine</label><br>
            <select id="nsfw-aengine" onchange="_nsfwAudEngineChange()" style="width:100%;padding:5px 8px;margin-top:2px;">${_nsfwAudioEngineOptionsHtml()}</select>
          </div>
          <div id="nsfw-adur-wrap">
            <label style="font-size:.78rem;">Duration</label><br>
            <select id="nsfw-adur" style="width:100%;padding:5px 8px;margin-top:2px;"></select>
          </div>
        </div>
        <div id="nsfw-alyrics-wrap" style="display:none;margin-top:8px;">
          <label style="font-size:.78rem;">&#127908; Lyrics <span style="color:var(--muted);">(ACE-Step only, optional &mdash; leave empty for instrumental)</span></label>
          <textarea id="nsfw-alyrics" rows="3" placeholder="[verse]&hellip;" style="width:100%;"></textarea>
        </div>
        <div style="display:flex;gap:8px;margin-top:8px;">
          <button class="btn-sm primary" onclick="nsfwAudio()">&#127925; Generate audio</button>
        </div>
      </div>`,
    '3d': `
      <div class="card" style="margin-bottom:14px;">
        <label style="font-size:.78rem;">Prompt (single object &rarr; image &rarr; mesh)</label>
        <textarea id="nsfw-3prompt" rows="2" placeholder="Describe the 3D figure (adults only)&hellip;" style="width:100%;"></textarea>
        <div style="margin-top:8px;">
          <label style="font-size:.78rem;">&#129513; Generator model</label><br>
          <select id="nsfw-3dgen" style="padding:5px 8px;margin-top:2px;">${_nsfwGenModelOptionsHtml()}</select>
        </div>
        <div style="margin-top:8px;font-size:.72rem;color:var(--muted);">
          &#9432; Install more image&rarr;3D generator models in the <a href="#" onclick="switchView('models3d');return false;" style="color:var(--accent);">3D Studio tab</a>.
        </div>
        <div style="display:flex;gap:8px;margin-top:8px;">
          <button class="btn-sm primary" onclick="nsfw3d()">&#129513; Generate 3D</button>
        </div>
      </div>`,
  };
  root.innerHTML = (forms[_nsfwSub] || '') + `<div id="nsfw-gallery">${_nsfwGalleryHtml(lib)}</div>`;
  if (_nsfwSub === 'audio') _nsfwAudEngineChange();
  // keep the gallery live while anything is still cooking
  const active = (lib.generating || []).some(g => ['queued', 'generating'].includes(g.status))
    || (lib.videos || []).some(v => ['queued', 'generating'].includes(v.status))
    || (lib.audio || []).some(a => ['queued', 'generating'].includes(a.status))
    || (lib.models3d || []).some(m => m.status === 'generating');
  if (active && _currentView === 'nsfw') {
    _nsfwPollTimer = setTimeout(() => { if (_currentView === 'nsfw') _renderNsfwSub(); }, 4000);
  }
}

function _nsfwCategoriesHtml() {
  const rows = _nsfwCats.map(c => `
    <div style="border:1px solid var(--border);border-radius:8px;padding:10px;margin-top:8px;">
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
        <input id="nsfw-cat-name-${c.id}" value="${esc(c.name)}" style="width:160px;padding:5px 8px;font-weight:600;">
        <button class="btn-sm primary" onclick="nsfwCatGenerate(${c.id})" title="Queue one model-authored creation in this category.">&#127912; Generate</button>
        <button class="btn-sm" onclick="nsfwCatSave(${c.id})">&#128190; Save</button>
        <button class="btn-sm danger" onclick="nsfwCatDelete(${c.id})">&#128465;</button>
        <span style="font-size:.7rem;color:var(--muted);">${c.rejects ? c.rejects + ' reject(s) on file — next prompts steer away' : ''}</span>
      </div>
      <textarea id="nsfw-cat-prompt-${c.id}" rows="3" placeholder="Generator prompt (empty — run Bootstrap or write your own)&hellip;" style="width:100%;margin-top:6px;font-size:.78rem;">${esc(c.gen_prompt || '')}</textarea>
    </div>`).join('');
  return `<div class="card" style="margin-bottom:14px;">
    <div style="display:flex;justify-content:space-between;align-items:center;cursor:pointer;" onclick="_nsfwCatsOpen=!_nsfwCatsOpen;_renderNsfwSub();">
      <b style="font-size:.85rem;">&#128193; Categories (${_nsfwCats.length})</b>
      <span style="color:var(--muted);font-size:.8rem;">${_nsfwCatsOpen ? '▴ hide' : '▾ manage'}</span>
    </div>
    ${_nsfwCatsOpen ? `
      <div style="font-size:.72rem;color:var(--muted);margin-top:6px;">Each category has its own generator prompt — authored by the NSFW model at Bootstrap, editable here. Rejections feed back so future prompts avoid rejected approaches.</div>
      ${rows}
      <div style="display:flex;gap:6px;margin-top:10px;">
        <input id="nsfw-cat-new" placeholder="New category name&hellip;" style="flex:1;padding:5px 8px;">
        <button class="btn-sm primary" onclick="nsfwCatAdd()">&#10133; Add</button>
      </div>` : ''}
  </div>`;
}

function _nsfwGalleryHtml(lib) {
  const grid = (inner) => `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;">${inner}</div>`;
  const emptyBox = '<div style="text-align:center;color:var(--muted);padding:40px 20px;">Nothing here yet &mdash; generate something above.</div>';
  if (_nsfwSub === 'image') {
    const catNames = [...new Set(_nsfwCats.map(c => c.name))];
    const filterBar = `<div style="display:flex;gap:6px;align-items:center;margin-bottom:10px;">
      <span style="font-size:.75rem;color:var(--muted);">Filter:</span>
      <select onchange="_nsfwCatFilter=this.value;_renderNsfwSub();" style="padding:4px 8px;font-size:.78rem;">
        <option value="">All categories</option>
        ${catNames.map(n => `<option value="${esc(n)}" ${_nsfwCatFilter === n ? 'selected' : ''}>${esc(n)}</option>`).join('')}
        <option value="__none" ${_nsfwCatFilter === '__none' ? 'selected' : ''}>Uncategorized</option>
      </select></div>`;
    const match = (cat) => !_nsfwCatFilter || (_nsfwCatFilter === '__none' ? !cat : cat === _nsfwCatFilter);
    const cooking = (lib.generating || []).filter(g => match(g.nsfw_category)).map(g => {
      const failed = g.status === 'failed';
      return `<div class="card">
        <div style="font-size:.72rem;color:${failed ? '#fca5a5' : '#a855f7'};">${failed ? '&#10060; failed' : '&#9203; ' + esc(g.status)}</div>
        <div style="font-size:.8rem;margin-top:4px;">${esc((g.prompt || '').slice(0, 120))}</div>
        ${failed ? `<div style="display:flex;gap:6px;margin-top:6px;">
          <button class="btn-sm" onclick="nsfwRetry(${g.id})" title="Re-run this generation with the same prompt, model & settings">&#128260; Regenerate</button>
          <button class="btn-sm danger" onclick="nsfwDelete('generations', ${g.id})">&#128465; Delete</button>
        </div>` : ''}</div>`;
    }).join('');
    const done = (lib.images || []).filter(d => match(d.nsfw_category)).map(d => {
      const url = _nsfwImgUrl(d.image_path);
      const chip = d.nsfw_category ? `<span style="font-size:.62rem;padding:1px 6px;border-radius:6px;background:rgba(168,85,247,.18);color:#c4a5f7;">${esc(d.nsfw_category)}</span>` : '';
      return `<div class="card">
        ${url ? `<a href="${url}" target="_blank" rel="noopener"><img src="${thumbUrl(d.image_path)}" loading="lazy" decoding="async" style="width:100%;border-radius:8px;" onerror="this.onerror=null;this.src='${url}'"></a>` : ''}
        <div style="display:flex;gap:6px;align-items:center;margin-top:6px;">${chip}</div>
        <div style="font-size:.78rem;margin-top:4px;color:var(--muted);" title="${esc(d.prompt || '')}">${esc((d.prompt || '').slice(0, 90))}</div>
        <div style="display:flex;gap:6px;margin-top:6px;">
          <button class="btn-sm" onclick="nsfwReject(${d.id})" title="Badly generated: removes the image AND feeds the rejection back — the taste model learns, and future prompts in this category avoid this approach.">&#128078; Reject</button>
          <button class="btn-sm danger" onclick="nsfwDelete('designs', ${d.id})">&#128465; Delete</button>
        </div>
      </div>`;
    }).join('');
    return filterBar + ((cooking + done) ? grid(cooking + done) : emptyBox);
  }
  if (_nsfwSub === 'video') {
    const cards = (lib.videos || []).map(v => {
      const src = v.status === 'done' && v.video_path ? _nsfwFileUrl(v.video_path) : null;
      const body = src ? `<video controls preload="none" src="${src}" style="width:100%;border-radius:8px;margin-top:6px;"></video>`
        : v.status === 'failed' ? `<div style="font-size:.72rem;color:#fca5a5;margin-top:6px;">${esc(v.error || 'failed')}</div>`
        : `<div style="font-size:.78rem;color:#a855f7;margin-top:6px;">&#9203; ${esc(v.progress_msg || v.status)}${v.progress ? ' ' + v.progress + '%' : ''}</div>`;
      return `<div class="card"><div style="font-size:.78rem;" title="${esc(v.prompt)}">${esc(v.prompt.slice(0, 90))}</div>${body}
        <button class="btn-sm danger" style="margin-top:6px;" onclick="nsfwDelete('videos', ${v.id})">&#128465; Delete</button></div>`;
    }).join('');
    return cards ? grid(cards) : emptyBox;
  }
  if (_nsfwSub === 'audio') {
    const cards = (lib.audio || []).map(a => {
      const src = a.status === 'done' && a.audio_path ? _nsfwFileUrl(a.audio_path) : null;
      const body = src ? `<audio controls preload="none" style="width:100%;margin-top:6px;"><source src="${src}"></audio>`
        : a.status === 'failed' ? `<div style="font-size:.72rem;color:#fca5a5;margin-top:6px;">${esc(a.error || 'failed')}</div>`
        : `<div style="font-size:.78rem;color:#a855f7;margin-top:6px;">&#9203; ${esc(a.progress_msg || a.status)}</div>`;
      return `<div class="card"><div style="font-size:.72rem;color:var(--muted);">${esc(a.engine)} &middot; ${a.duration}s</div>
        <div style="font-size:.78rem;margin-top:4px;" title="${esc(a.prompt)}">${esc(a.prompt.slice(0, 90))}</div>${body}
        <button class="btn-sm danger" style="margin-top:6px;" onclick="nsfwDelete('audio', ${a.id})">&#128465; Delete</button></div>`;
    }).join('');
    return cards ? grid(cards) : emptyBox;
  }
  if (_nsfwSub === '3d') {
    const cards = (lib.models3d || []).map(m => `<div class="card">
        <div style="font-size:.82rem;font-weight:600;">${esc(m.title || 'Model #' + m.id)}</div>
        <div style="font-size:.72rem;color:var(--muted);margin-top:2px;">${esc(m.gen_prompt || '')}</div>
        <div style="font-size:.75rem;margin-top:4px;color:${m.status === 'error' ? '#fca5a5' : 'var(--muted)'};">${esc(m.status)}${m.progress_msg ? ' &middot; ' + esc(m.progress_msg) : ''}</div>
        <button class="btn-sm danger" style="margin-top:6px;" onclick="nsfwDelete('models3d', ${m.id})">&#128465; Delete</button>
      </div>`).join('');
    return cards ? grid(cards) : emptyBox;
  }
  return emptyBox;
}

/* ── actions ── */
async function nsfwGenerate() {
  const prompt = document.getElementById('nsfw-prompt')?.value.trim();
  if (!prompt) { toast('Type a prompt first', 'warn'); return; }
  const model = document.getElementById('nsfw-model')?.value || '';
  const variations = parseInt(document.getElementById('nsfw-variations')?.value || '3', 10);
  const steps = parseInt(document.getElementById('nsfw-steps')?.value || '20', 10);
  try {
    await api('/api/nsfw/generate', { method: 'POST', body: JSON.stringify({ prompt, model: model || null, variations, steps }) });
    toast(`Queued ${variations} variation${variations > 1 ? 's' : ''} (private)`);
    _renderNsfwSub();
  } catch (e) { toast(e.message, 'error'); }
}
window.nsfwGenerate = nsfwGenerate;

// mirrors tab-audio.js's _audEngineChange: rebuilds the duration options for the
// selected engine and shows/hides the lyrics box (ACE-Step only).
function _nsfwAudEngineChange() {
  const key = document.getElementById('nsfw-aengine')?.value;
  const e = _nsfwAudioEngines.find(x => x.key === key);
  const isVoice = e && e.kind === 'voice';
  const durWrap = document.getElementById('nsfw-adur-wrap');
  const durSel = document.getElementById('nsfw-adur');
  const lyrWrap = document.getElementById('nsfw-alyrics-wrap');
  if (lyrWrap) lyrWrap.style.display = (key === 'acestep') ? 'block' : 'none';
  if (durWrap) durWrap.style.display = isVoice ? 'none' : '';
  if (durSel && !isVoice) {
    const d = _NSFW_AUD_DURATIONS[key] || _NSFW_AUD_DURATIONS.musicgen;
    durSel.innerHTML = d.opts.map(s => `<option value="${s}"${s === d.rec ? ' selected' : ''}>${_nsfwFmtSecs(s)}</option>`).join('');
  }
}
window._nsfwAudEngineChange = _nsfwAudEngineChange;

async function nsfwModelChange(v) {
  _nsfwImageModel = v || '';
  try {
    await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ nsfw_image_model: _nsfwImageModel }) });
    toast('Image model saved');
  } catch (e) { toast(e.message, 'error'); }
}
window.nsfwModelChange = nsfwModelChange;

const _nsfwEnhBusy = (on) => { const b = document.getElementById('nsfw-enhance-btn'); if (b) { b.disabled = on; b.innerHTML = on ? '⏳ Enhancing…' : '✨ Enhance'; } };
async function nsfwEnhance() {
  const raw = document.getElementById('nsfw-prompt')?.value.trim();
  if (!raw) { toast('Type a rough idea first', 'warn'); return; }
  enhanceStart('nsfw-prompt',
    async () => (await api('/api/nsfw/enhance', { method: 'POST', body: JSON.stringify({ prompt: raw }) })).task_id,
    _nsfwEnhBusy);
}
window.nsfwEnhance = nsfwEnhance;

async function nsfwVideo() {
  const prompt = document.getElementById('nsfw-vprompt')?.value.trim();
  if (!prompt) { toast('Type a prompt first', 'warn'); return; }
  const model_id = document.getElementById('nsfw-vmodel')?.value || 'Wan-AI/Wan2.1-T2V-1.3B-Diffusers';
  const res = document.getElementById('nsfw-vres')?.value || '832x480';
  const [width, height] = res.split('x').map(Number);
  const num_frames = parseInt(document.getElementById('nsfw-vframes')?.value || '49', 10);
  const steps = parseInt(document.getElementById('nsfw-vsteps')?.value || '20', 10);
  try {
    await api('/api/nsfw/video', { method: 'POST', body: JSON.stringify({ prompt, model_id, width, height, num_frames, steps }) });
    toast('Video queued (private)');
    _renderNsfwSub();
  } catch (e) { toast(e.message, 'error'); }
}
window.nsfwVideo = nsfwVideo;

async function nsfwAudio() {
  const prompt = document.getElementById('nsfw-aprompt')?.value.trim();
  if (!prompt) { toast('Type a prompt first', 'warn'); return; }
  const engine = document.getElementById('nsfw-aengine')?.value || 'musicgen';
  const body = {
    prompt,
    engine,
    duration: parseInt(document.getElementById('nsfw-adur')?.value || '12', 10),
    model_id: '',
    lyrics: (engine === 'acestep') ? (document.getElementById('nsfw-alyrics')?.value || '') : '',
  };
  try {
    await api('/api/nsfw/audio', { method: 'POST', body: JSON.stringify(body) });
    toast('Audio queued (private)');
    _renderNsfwSub();
  } catch (e) { toast(e.message, 'error'); }
}
window.nsfwAudio = nsfwAudio;

async function nsfw3d() {
  const prompt = document.getElementById('nsfw-3prompt')?.value.trim();
  if (!prompt) { toast('Type a prompt first', 'warn'); return; }
  const generator = document.getElementById('nsfw-3dgen')?.value || 'triposr';
  try {
    await api('/api/nsfw/3d', { method: 'POST', body: JSON.stringify({ prompt, generator }) });
    toast('3D generation queued (private)');
    _renderNsfwSub();
  } catch (e) { toast(e.message, 'error'); }
}
window.nsfw3d = nsfw3d;

async function nsfwBootstrap() {
  if (!confirm('Have the NSFW model write/refresh the generator prompt for EVERY category? Existing prompts (including your edits) are overwritten. Re-runnable any time.')) return;
  const btn = document.getElementById('nsfw-bootstrap-btn');
  if (btn) { btn.disabled = true; btn.innerHTML = '⏳ Bootstrapping…'; }
  try {
    const r = await api('/api/nsfw/bootstrap', { method: 'POST' });
    toast('Bootstrap queued — the model is authoring category prompts…');
    const res = await pollTask(r.task_id, 150);
    const up = (res.updated || []).length, ref = (res.refused || []).length;
    toast(`Bootstrap done: ${up} categor${up === 1 ? 'y' : 'ies'} authored${ref ? `, ${ref} refused/failed` : ''}`);
    _renderNsfwSub();
  } catch (e) { toast('Bootstrap: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.innerHTML = '🧠 Bootstrap prompts'; } }
}
window.nsfwBootstrap = nsfwBootstrap;

async function nsfwGenerateAll() {
  try {
    const r = await api('/api/nsfw/generate-all', { method: 'POST' });
    toast(`Queued ${r.queued} category job(s) (private)`);
    _renderNsfwSub();
  } catch (e) { toast(e.message, 'error'); }
}
window.nsfwGenerateAll = nsfwGenerateAll;

async function nsfwCatGenerate(id) {
  try {
    await api(`/api/nsfw/categories/${id}/generate`, { method: 'POST' });
    toast('Category job queued (private)');
    _renderNsfwSub();
  } catch (e) { toast(e.message, 'error'); }
}
window.nsfwCatGenerate = nsfwCatGenerate;

async function nsfwCatSave(id) {
  const name = document.getElementById(`nsfw-cat-name-${id}`)?.value.trim();
  const gen_prompt = document.getElementById(`nsfw-cat-prompt-${id}`)?.value ?? '';
  try {
    await api(`/api/nsfw/categories/${id}`, { method: 'PATCH', body: JSON.stringify({ name, gen_prompt }) });
    toast('Category saved');
    _renderNsfwSub();
  } catch (e) { toast(e.message, 'error'); }
}
window.nsfwCatSave = nsfwCatSave;

async function nsfwCatDelete(id) {
  if (!confirm('Delete this category? (Its generated images stay in the archive.)')) return;
  try {
    await api(`/api/nsfw/categories/${id}`, { method: 'DELETE' });
    toast('Category deleted');
    _renderNsfwSub();
  } catch (e) { toast(e.message, 'error'); }
}
window.nsfwCatDelete = nsfwCatDelete;

async function nsfwCatAdd() {
  const name = document.getElementById('nsfw-cat-new')?.value.trim();
  if (!name) { toast('Type a category name', 'warn'); return; }
  try {
    await api('/api/nsfw/categories', { method: 'POST', body: JSON.stringify({ name }) });
    toast('Category added — run Bootstrap (or write its prompt) to arm it');
    _renderNsfwSub();
  } catch (e) { toast(e.message, 'error'); }
}
window.nsfwCatAdd = nsfwCatAdd;

async function nsfwReject(id) {
  if (!confirm('Reject as badly generated? The image is removed and the rejection is fed back (taste model + avoid-list) so future prompts steer away.')) return;
  try {
    await api(`/api/nsfw/item/${id}/reject`, { method: 'POST' });
    toast('Rejected — feedback recorded');
    _renderNsfwSub();
  } catch (e) { toast(e.message, 'error'); }
}
window.nsfwReject = nsfwReject;

async function nsfwDelete(kind, id) {
  if (!confirm('Delete this item permanently?')) return;
  const route = { designs: `/api/designs/${id}`, videos: `/api/videos/${id}`,
                  audio: `/api/audio/${id}`, models3d: `/api/models3d/${id}`,
                  generations: `/api/generations/${id}` }[kind];
  try {
    await api(route, { method: 'DELETE' });
    toast('Deleted');
    _renderNsfwSub();
  } catch (e) { toast(e.message, 'error'); }
}
window.nsfwDelete = nsfwDelete;

async function nsfwRetry(id) {
  try {
    await api(`/api/generations/${id}/retry`, { method: 'POST' });
    toast('Regenerating…');
    _renderNsfwSub();
  } catch (e) { toast(e.message, 'error'); }
}
window.nsfwRetry = nsfwRetry;

'use strict';

/* 🎞️ Director — AI Video Studio, Phases 1+2.
   Phase 1: drop an idea → the storyboard LLM (unified queue) writes scenes/
   shots/script/captions/audio plan → everything is editable here.
   Phase 2: render buttons (per-scene + whole project), live chain progress,
   inline previews, music+voiceover generation, assemble & mix → final video,
   export to Social (draft post), and own-footage upload (📎 on a shot).
   Full layered timeline (per-scene VO, SFX, ducking, captions) = Phase 3
   (docs/VIDEO-STUDIO-DESIGN.md §8). */

let _dirOpenId = null;        // project open in the editor; null = inbox
let _dirPollTimer = null;
let _dirModels = [];          // /api/video-models cache (installed only)
let _dirNsfwOk = false;       // show the 🔒 Private toggle?

const _DIR_RES = [
  { v: '480x832', label: '480×832 (9:16 portrait)' },
  { v: '832x480', label: '832×480 (16:9 landscape)' },
  { v: '512x512', label: '512×512 (square)' },
];
const _DIR_SECS = [1.5, 3, 5, 7.5];
const _DIR_STATUS_COLORS = {
  new: '#94a3b8', storyboarding: '#a855f7', draft: '#38bdf8', rendering: '#f59e0b',
  assembling: '#f59e0b', mixing: '#f59e0b', done: '#22c55e', failed: '#ef4444',
};

function _dirClearPoll() {
  if (_dirPollTimer) { clearTimeout(_dirPollTimer); _dirPollTimer = null; }
}

function _dirBadge(status) {
  const c = _DIR_STATUS_COLORS[status] || '#94a3b8';
  const label = status === 'storyboarding' ? '🪄 storyboarding…' : status;
  return `<span style="font-size:.7rem;font-weight:700;padding:2px 8px;border-radius:10px;background:${c}22;color:${c};border:1px solid ${c}55">${esc(label)}</span>`;
}

const _dirInp = 'padding:7px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;font-family:inherit;font-size:.85rem;box-sizing:border-box';
const _dirTa = `width:100%;${_dirInp};resize:vertical`;
const _DIR_BUSY = ['storyboarding', 'rendering', 'assembling', 'mixing'];

function _dirVidUrl(path) {
  return `${API}/videos/${encodeURIComponent(String(path).split('/').pop())}`;
}

function _dirVideoTag(path, extra) {
  // NOT muted: finals/scene clips carry the mixed soundtrack, and the hardcoded
  // muted attribute (re-applied by every 2.5 s poll re-render) was why studio
  // videos played silent and unmute/volume never stuck. Playback is user-
  // initiated (controls, no autoplay), so muted isn't needed for any policy.
  return `<video src="${_dirVidUrl(path)}" controls loop preload="metadata" playsinline
    style="width:100%;max-height:360px;border-radius:8px;background:#000;${extra || ''}"></video>`;
}

function _dirProgressBar(pct, msg) {
  const p = Math.max(2, Math.min(100, Math.round(pct)));
  return `<div style="margin-top:8px">
    <div style="height:8px;border-radius:4px;background:var(--border);overflow:hidden">
      <div style="width:${p}%;height:100%;background:linear-gradient(90deg,#a855f7,#f59e0b);transition:width .6s"></div>
    </div>
    <div style="margin-top:4px;font-size:.72rem;color:var(--muted)">${esc(msg || '')} · ${p}%</div>
  </div>`;
}

async function renderDirector() {
  _dirClearPoll();
  try { _dirNsfwOk = !!(await api('/api/nsfw/status')).visible; } catch { _dirNsfwOk = false; }
  if (!_dirModels.length) {
    try { _dirModels = (await api('/api/video-models')).filter(m => m.installed); } catch { _dirModels = []; }
  }
  if (_dirOpenId) await _dirRenderEditor(_dirOpenId);
  else await _dirRenderInbox();
}
window.renderDirector = renderDirector;

/* ── Inbox ─────────────────────────────────────────────────────────────── */

async function _dirRenderInbox() {
  const el = viewRoot();
  const modelOpts = _dirModels.length
    ? _dirModels.map(m => `<option value="${esc(m.model_id)}">${esc(m.label || m.model_id)}</option>`).join('')
    : '<option value="Wan-AI/Wan2.1-T2V-1.3B-Diffusers">Wan 2.1 T2V 1.3B</option>';
  el.innerHTML = `
    <div class="section-header">
      <div>
        <div class="section-title">🎞️ Director</div>
        <div class="section-sub">Drop an idea — get a storyboard, a video, and a soundtrack</div>
      </div>
    </div>
    <div class="card" style="margin-bottom:16px">
      <textarea id="dir-idea" placeholder="Paste an idea, a meme, a shower thought…" style="${_dirTa};min-height:80px"></textarea>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:10px">
        <label style="font-size:.78rem;color:var(--muted)">Kind
          <select id="dir-kind" style="width:100%;margin-top:4px;${_dirInp}">
            <option value="short" selected>Short (~15s, 1 scene)</option>
            <option value="long">Long (1–2 min, scenes)</option>
          </select></label>
        <label style="font-size:.78rem;color:var(--muted)">Length
          <select id="dir-len" style="width:100%;margin-top:4px;${_dirInp}">
            <option value="10">~10s</option><option value="20" selected>~20s</option>
            <option value="30">~30s</option><option value="60">~60s</option><option value="90">~90s</option>
          </select></label>
        <label style="font-size:.78rem;color:var(--muted)">Style (optional)
          <input id="dir-style" placeholder="e.g. cozy pixel art, neon noir" style="width:100%;margin-top:4px;${_dirInp}"></label>
        <label style="font-size:.78rem;color:var(--muted)">Resolution
          <select id="dir-res" style="width:100%;margin-top:4px;${_dirInp}">
            ${_DIR_RES.map(r => `<option value="${r.v}">${r.label}</option>`).join('')}
          </select></label>
        <label style="font-size:.78rem;color:var(--muted)">Model
          <select id="dir-model" style="width:100%;margin-top:4px;${_dirInp}">${modelOpts}</select></label>
      </div>
      <div style="display:flex;align-items:center;gap:14px;margin-top:12px;flex-wrap:wrap">
        <button id="dir-create-btn" style="width:auto;padding:10px 26px" onclick="dirCreateProject()">🎬 Storyboard it</button>
        <button id="dir-meme-btn" title="Fast lane: one punchy captioned short (~3–6s) with audio — no storyboard editing, everything runs automatically" style="width:auto;padding:10px 20px" onclick="dirQuickMeme()">😂 Quick Meme</button>
        ${_dirNsfwOk ? `<label style="font-size:.78rem;color:var(--muted);display:flex;align-items:center;gap:6px"><input type="checkbox" id="dir-nsfw" style="width:auto">🔒 Private</label>` : ''}
      </div>
    </div>
    <div id="dir-trends"></div>
    <div id="dir-projects"></div>`;
  await _dirRefreshInbox();
  _dirRenderTrends();   // fire-and-forget — the inbox must not wait on the backlog
}

/* ── Trending → storyboard (trend scanner backlog, one click to Studio) ── */

let _dirTrendsCache = null, _dirTrendsAt = 0;

async function _dirRenderTrends() {
  const el = document.getElementById('dir-trends');
  if (!el) return;
  try {
    if (!_dirTrendsCache || Date.now() - _dirTrendsAt > 60000) {
      _dirTrendsCache = await api('/api/proposals?status=pending');
      _dirTrendsAt = Date.now();
    }
  } catch { el.innerHTML = ''; return; }
  const rows = (_dirTrendsCache || []).slice(0, 8);
  if (!rows.length) { el.innerHTML = ''; return; }
  el.innerHTML = `<div class="card" style="margin-bottom:16px">
    <div style="font-weight:600;font-size:.88rem">🔥 Trending now <span style="font-weight:400;color:var(--muted)">— straight from the trend scanner; one click to a storyboard</span></div>
    ${rows.map(t => `<div style="display:flex;gap:10px;align-items:center;margin-top:8px;flex-wrap:wrap">
      <div style="flex:1;min-width:220px">
        <div style="font-size:.82rem;font-weight:600">${esc(t.title)} <span style="font-weight:400;font-size:.7rem;color:var(--muted)">· ${esc(t.source_label || t.source || 'trend')}</span></div>
        ${t.description ? `<div style="font-size:.74rem;color:var(--muted);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(t.description)}</div>` : ''}
      </div>
      <button class="btn-sm" style="align-self:center" onclick="dirStoryboardTrend(${t.id},this)">🎬 Storyboard this trend</button>
    </div>`).join('')}
  </div>`;
}

async function dirStoryboardTrend(proposalId, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Queuing…'; }
  try {
    await api('/api/studio/from-trend', { method: 'POST', body: JSON.stringify({ proposal_id: proposalId }) });
    toast('🎬 Storyboarding this trend — the Director is on it');
    await _dirRefreshInbox();
    if (btn) btn.textContent = '✓ Storyboarding';
  } catch (e) {
    toast('Error: ' + e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = '🎬 Storyboard this trend'; }
  }
}
window.dirStoryboardTrend = dirStoryboardTrend;

async function _dirRefreshInbox() {
  const el = document.getElementById('dir-projects');
  if (!el || _dirOpenId) return;
  _dirClearPoll();
  let projects = [];
  try { projects = await api('/api/studio/projects'); } catch { return; }
  // syncCards (tab-videos.js): the poll only re-renders cards whose data
  // changed — unchanged previews are never rebuilt (no flicker).
  if (!projects.length) {
    stopMediaIn(el);
    el.innerHTML = `<div style="text-align:center;color:var(--muted);padding:50px 20px">🎞️ No storyboards yet — drop an idea above.</div>`;
  } else {
    let grid = el.querySelector('[data-cards="dir-projects"]');
    if (!grid) {
      stopMediaIn(el);
      el.innerHTML = '<div data-cards="dir-projects" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px"></div>';
      grid = el.firstElementChild;
    }
    syncCards(grid, projects, _dirProjectCard);
  }
  if (projects.some(p => _DIR_BUSY.includes(p.status)) && _currentView === 'director') {
    _dirPollTimer = setTimeout(_dirRefreshInbox, 2500);
  }
}

function _dirProjectCard(p) {
  let body = '';
  if (p.status === 'storyboarding') {
    body = `<div style="margin-top:8px;font-size:.8rem;color:#a855f7">⏳ The Director is writing the storyboard…</div>`;
  } else if (p.status === 'failed') {
    body = `<div style="margin-top:8px;font-size:.72rem;color:#fca5a5;white-space:pre-wrap;max-height:80px;overflow:auto;font-family:monospace">${esc(p.error || 'failed')}</div>
      <button class="btn-sm" style="margin-top:6px" onclick="event.stopPropagation();dirRetry(${p.id})">🔁 Try again</button>`;
  } else if (p.status === 'draft') {
    body = `<div style="margin-top:8px;font-size:.8rem;color:var(--muted)">📋 Storyboard ready — ${p.scene_count} scene${p.scene_count === 1 ? '' : 's'}. Open to edit.</div>`;
  } else if (['rendering', 'assembling', 'mixing'].includes(p.status)) {
    body = `<div style="margin-top:8px;font-size:.8rem;color:#f59e0b">⏳ ${esc(p.progress_msg || p.status + '…')}</div>`;
  } else if (p.status === 'done' && p.final_path) {
    body = `<div style="margin-top:8px" onclick="event.stopPropagation()">${_dirVideoTag(p.final_path, 'max-height:220px')}</div>
      <div style="margin-top:6px;font-size:.76rem;color:#22c55e">🎉 Final video ready — open to preview & export.</div>`;
  }
  return `<div class="card" style="cursor:pointer" onclick="dirOpen(${p.id})">
    <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
      ${_dirBadge(p.status)}
      <button onclick="event.stopPropagation();dirDeleteProject(${p.id})" style="width:auto;padding:2px 8px;font-size:.72rem;background:#ef444420;color:#ef4444;border:1px solid #ef444450">🗑</button>
    </div>
    <div style="margin-top:8px;font-weight:700;font-size:.92rem">${esc(p.title || p.idea.slice(0, 60))}${p.nsfw ? ' 🔒' : ''}</div>
    <div style="margin-top:4px;font-size:.78rem;color:var(--muted);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(p.logline || p.idea)}</div>
    ${body}
  </div>`;
}

async function dirCreateProject() {
  const idea = document.getElementById('dir-idea').value.trim();
  if (!idea) { toast('Drop an idea first', 'warn'); return; }
  const [w, h] = document.getElementById('dir-res').value.split('x').map(Number);
  const body = {
    idea,
    kind: document.getElementById('dir-kind').value,
    style: document.getElementById('dir-style').value.trim(),
    target_seconds: parseInt(document.getElementById('dir-len').value),
    model_id: document.getElementById('dir-model').value,
    width: w, height: h,
    nsfw: !!document.getElementById('dir-nsfw')?.checked,
  };
  const btn = document.getElementById('dir-create-btn');
  btn.disabled = true; btn.textContent = '⏳ Queuing…';
  try {
    await api('/api/studio/projects', { method: 'POST', body: JSON.stringify(body) });
    toast('Storyboarding — the Director is on it');
    document.getElementById('dir-idea').value = '';
    await _dirRefreshInbox();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { btn.disabled = false; btn.innerHTML = '🎬 Storyboard it'; }
}
window.dirCreateProject = dirCreateProject;

/* 😂 Quick Meme — the fast lane: POST /api/studio/meme with the idea box's
   text; the server seeds a preset 1-scene board (no LLM) and one background
   task renders, generates audio, assembles & mixes and burns the caption.
   The inbox card's normal busy/done states track it from there. */
async function dirQuickMeme() {
  const idea = document.getElementById('dir-idea').value.trim();
  if (!idea) { toast('Drop a meme idea first', 'warn'); return; }
  const [w, h] = document.getElementById('dir-res').value.split('x').map(Number);
  const body = {
    text: idea,
    style: document.getElementById('dir-style').value.trim(),
    model_id: document.getElementById('dir-model').value,
    width: w, height: h,
    nsfw: !!document.getElementById('dir-nsfw')?.checked,
  };
  const btn = document.getElementById('dir-meme-btn');
  btn.disabled = true; btn.textContent = '⏳ Cooking…';
  try {
    await api('/api/studio/meme', { method: 'POST', body: JSON.stringify(body) });
    toast('😂 Meme cooking — render, audio and caption run automatically');
    document.getElementById('dir-idea').value = '';
    await _dirRefreshInbox();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { btn.disabled = false; btn.textContent = '😂 Quick Meme'; }
}
window.dirQuickMeme = dirQuickMeme;

async function dirOpen(id) { _dirOpenId = id; await renderDirector(); }
window.dirOpen = dirOpen;

async function dirBack() { _dirOpenId = null; await renderDirector(); }
window.dirBack = dirBack;

async function dirDeleteProject(id) {
  if (!confirm('Delete this storyboard project?')) return;
  try {
    await api(`/api/studio/projects/${id}`, { method: 'DELETE' });
    toast('Deleted');
    if (_dirOpenId === id) _dirOpenId = null;
    await renderDirector();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.dirDeleteProject = dirDeleteProject;

async function dirRetry(id, notes) {
  try {
    await api(`/api/studio/projects/${id}/storyboard`, { method: 'POST', body: JSON.stringify({ notes: notes || '' }) });
    toast('Storyboarding again…');
    await renderDirector();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.dirRetry = dirRetry;

async function dirRestoryboard(id) {
  const notes = prompt('Notes for the rewrite (optional) — the current storyboard will be replaced:');
  if (notes === null) return;
  await dirRetry(id, notes);
}
window.dirRestoryboard = dirRestoryboard;

/* ── Editor ────────────────────────────────────────────────────────────── */

async function _dirRenderEditor(id) {
  const el = viewRoot();
  let p;
  try { p = await api(`/api/studio/projects/${id}`); }
  catch (e) { _dirOpenId = null; toast('Error: ' + e.message, 'error'); return _dirRenderInbox(); }

  if (p.status === 'storyboarding') {
    stopMediaIn(el);   // never leave a detached player's audio running
    el.innerHTML = `
      ${_dirEditorHeader(p, false)}
      <div class="card" style="text-align:center;padding:50px 20px">
        <div style="font-size:2rem">🪄</div>
        <div style="margin-top:8px;color:#a855f7;font-size:.9rem">The Director is writing the storyboard…</div>
        <div style="margin-top:4px;font-size:.78rem;color:var(--muted)">${esc(p.idea)}</div>
      </div>`;
    _dirPollTimer = setTimeout(() => { if (_currentView === 'director' && _dirOpenId === id) _dirRenderEditor(id); }, 2500);
    return;
  }

  const modelOpts = (_dirModels.length ? _dirModels.map(m => m.model_id) : [p.model_id])
    .map(mid => `<option value="${esc(mid)}"${mid === p.model_id ? ' selected' : ''}>${esc(mid.split('/').pop())}</option>`).join('');
  const resVal = `${p.width}x${p.height}`;
  const musicCue = (p.cues || []).find(c => c.kind === 'music');
  const sfxCues = (p.cues || []).filter(c => c.kind === 'sfx');
  const voCues = (p.cues || []).filter(c => c.kind === 'voiceover');
  const plan = (() => { try { return JSON.parse(p.audio_plan || '{}'); } catch { return {}; } })();

  const scenes = p.scenes || [];
  const allDone = scenes.length > 0 && scenes.every(s => s.status === 'done');
  const busy = _DIR_BUSY.includes(p.status) || scenes.some(s => s.status === 'queued' || s.status === 'rendering');

  // Skeleton/dynamic split (the flicker fix): the full page renders ONLY when
  // its structural inputs change; the 2.5 s busy poll touches just #dir-busy,
  // the keyed scene cards and #dir-final — so the script/caption textareas
  // the owner may be typing in and any playing preview are never rebuilt.
  const busyHtml = busy ? `<div class="card" style="margin-bottom:14px;border-color:#f59e0b70">
      <div style="font-size:.82rem;color:#f59e0b">⏳ ${esc(p.progress_msg || p.status + '…')}</div>
      <div style="font-size:.72rem;color:var(--muted);margin-top:4px">The 3060 renders one segment at a time — a shot takes ~2–4 min. This page live-updates.</div>
    </div>` : '';
  const finalHtml = _dirFinalCard(p, allDone, busy);
  const skelKey = JSON.stringify([p.status, busy, allDone,
    scenes.map(s => s.id).join(','), (p.cues || []).map(c => c.id).join(','),
    p.model_id, resVal, p.fps, p.steps, p.music_engine, p.voice_engine,
    p.style, p.logline, p.script, p.captions, p.error]);
  if (el._dirSkel === skelKey && document.getElementById('dir-scenes')) {
    const bEl = document.getElementById('dir-busy');
    if (bEl && bEl._html !== busyHtml) { bEl.innerHTML = busyHtml; bEl._html = busyHtml; }
    const fEl = document.getElementById('dir-final');
    if (fEl && fEl._html !== finalHtml) { setHTMLKeepMedia(fEl, finalHtml); fEl._html = finalHtml; }
    syncCards(document.getElementById('dir-scenes'), scenes, sc => _dirSceneCard(sc, p));
    if (busy && _currentView === 'director' && _dirOpenId === id) {
      _dirPollTimer = setTimeout(() => { if (_currentView === 'director' && _dirOpenId === id) _dirRenderEditor(id); }, 2500);
    }
    return;
  }
  setHTMLKeepMedia(el, `
    ${_dirEditorHeader(p, true)}
    <input type="file" id="dir-upload-file" accept=".mp4,.mov,.m4v,.webm,.mkv,video/*" style="display:none">
    <div id="dir-busy">${busyHtml}</div>
    ${p.status === 'failed' ? `<div class="card" style="margin-bottom:14px;border-color:#ef444470">
      <div style="font-size:.8rem;color:#fca5a5;white-space:pre-wrap;font-family:monospace">${esc(p.error || 'failed')}</div>
      ${scenes.length ? '' : `<button class="btn-sm" style="margin-top:8px" onclick="dirRetry(${p.id})">🔁 Try again</button>`}</div>` : ''}

    <details class="card" style="margin-bottom:14px">
      <summary style="cursor:pointer;font-weight:600;font-size:.88rem">⚙️ Render settings <span style="font-weight:400;color:var(--muted)">(${esc(resVal)} · ${p.fps} fps · ${p.steps} steps)</span></summary>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-top:12px">
        <label style="font-size:.78rem;color:var(--muted)">Model
          <select style="width:100%;margin-top:4px;${_dirInp}" onchange="dirPatchProject(${p.id},{model_id:this.value})">${modelOpts}</select></label>
        <label style="font-size:.78rem;color:var(--muted)">Resolution
          <select style="width:100%;margin-top:4px;${_dirInp}" onchange="dirPatchRes(${p.id},this.value)">
            ${_DIR_RES.map(r => `<option value="${r.v}"${r.v === resVal ? ' selected' : ''}>${r.label}</option>`).join('')}
          </select></label>
        <label style="font-size:.78rem;color:var(--muted)">Steps
          <select style="width:100%;margin-top:4px;${_dirInp}" onchange="dirPatchProject(${p.id},{steps:parseInt(this.value)})">
            ${[15, 20, 30].map(s => `<option value="${s}"${s === p.steps ? ' selected' : ''}>${s}</option>`).join('')}
          </select></label>
        <label style="font-size:.78rem;color:var(--muted)">Music engine
          <select style="width:100%;margin-top:4px;${_dirInp}" onchange="dirPatchProject(${p.id},{music_engine:this.value})">
            ${['musicgen', 'musicgen_med', 'stable_audio', 'acestep'].map(e => `<option value="${e}"${e === p.music_engine ? ' selected' : ''}>${e}</option>`).join('')}
          </select></label>
        <label style="font-size:.78rem;color:var(--muted)">Voice engine
          <select style="width:100%;margin-top:4px;${_dirInp}" onchange="dirPatchProject(${p.id},{voice_engine:this.value})">
            <option value="mms_tts"${p.voice_engine === 'mms_tts' ? ' selected' : ''}>mms_tts</option>
          </select></label>
        <label style="font-size:.78rem;color:var(--muted)">Style thread
          <input value="${esc(p.style || '')}" style="width:100%;margin-top:4px;${_dirInp}" onchange="dirPatchProject(${p.id},{style:this.value})"></label>
      </div>
    </details>

    <div class="card" style="margin-bottom:14px">
      <div style="font-weight:600;font-size:.88rem;margin-bottom:6px">📜 Script &amp; caption</div>
      ${p.logline ? `<div style="font-size:.78rem;color:var(--muted);margin-bottom:8px;font-style:italic">${esc(p.logline)}</div>` : ''}
      <label style="font-size:.75rem;color:var(--muted)">Full voiceover script (edit the words per scene below — this is the read-through)</label>
      <textarea id="dir-script" style="${_dirTa};min-height:70px;margin-top:4px">${esc(p.script || '')}</textarea>
      <label style="font-size:.75rem;color:var(--muted);display:block;margin-top:8px">Social caption + hashtags</label>
      <textarea id="dir-captions" style="${_dirTa};min-height:50px;margin-top:4px">${esc(p.captions || '')}</textarea>
      <button class="btn-sm" style="margin-top:8px" onclick="dirSaveScript(${p.id})">💾 Save</button>
    </div>

    <div class="card" style="margin-bottom:14px">
      <div style="font-weight:600;font-size:.88rem;margin-bottom:8px">🎚️ Audio plan</div>
      ${musicCue ? `
        <label style="font-size:.75rem;color:var(--muted)">🎵 Music bed (${esc(p.music_engine)}${plan.music && plan.music.mood ? ' · ' + esc(plan.music.mood) : ''})</label>
        <textarea style="${_dirTa};min-height:44px;margin-top:4px" onchange="dirPatchCue(${musicCue.id},{text:this.value})">${esc(musicCue.text)}</textarea>`
      : `<div style="font-size:.78rem;color:var(--muted)">No music cue.</div>`}
      <div style="font-size:.75rem;color:var(--muted);margin-top:10px">🗣️ Voiceover: ${voCues.length} scene cue${voCues.length === 1 ? '' : 's'} (${esc(p.voice_engine)}) — edit the words on each scene card.</div>
      <div style="font-size:.75rem;color:var(--muted);margin-top:10px;margin-bottom:4px">💥 Sound effects</div>
      ${sfxCues.map(c => _dirSfxRow(c, p)).join('') || '<div style="font-size:.75rem;color:var(--muted)">No SFX cues.</div>'}
      <button class="btn-sm" style="margin-top:8px" onclick="dirAddSfx(${p.id})">＋ SFX cue</button>
    </div>

    <div id="dir-scenes"></div>

    <div id="dir-final">${finalHtml}</div>`);
  el._dirSkel = skelKey;
  const scEl = document.getElementById('dir-scenes');
  if (scEl) { syncCards(scEl, scenes, sc => _dirSceneCard(sc, p)); restoreMediaState(scEl); }
  const fEl = document.getElementById('dir-final');
  if (fEl) fEl._html = finalHtml;
  const bEl = document.getElementById('dir-busy');
  if (bEl) bEl._html = busyHtml;

  if (busy && _currentView === 'director' && _dirOpenId === id) {
    _dirPollTimer = setTimeout(() => { if (_currentView === 'director' && _dirOpenId === id) _dirRenderEditor(id); }, 2500);
  }
}

function _dirAudioChip(label, clip) {
  if (!clip) return `<span style="font-size:.72rem;color:var(--muted)">${label}: —</span>`;
  const c = clip.status === 'done' ? '#22c55e' : clip.status === 'failed' ? '#ef4444' : '#f59e0b';
  return `<span title="${esc(clip.error || '')}" style="font-size:.72rem;font-weight:600;padding:2px 8px;border-radius:10px;background:${c}22;color:${c};border:1px solid ${c}55">${label}: ${esc(clip.status)}</span>`;
}

function _dirFinalCard(p, allDone, busy) {
  const audioReady = (p.music_clip && p.music_clip.status === 'done') || (p.vo_clip && p.vo_clip.status === 'done');
  const chips = (p.music_clip || p.vo_clip)
    ? `<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">${_dirAudioChip('🎵 music', p.music_clip)} ${_dirAudioChip('🗣️ voiceover', p.vo_clip)}</div>` : '';
  if (p.final_path) {
    return `<div class="card" style="margin-top:4px;border-color:#22c55e55">
      <div style="font-weight:700;font-size:.92rem">🎉 Final video</div>
      ${chips}
      <div style="margin-top:10px">${_dirVideoTag(p.final_path)}</div>
      <div style="display:flex;gap:10px;align-items:center;margin-top:10px;flex-wrap:wrap">
        <a class="btn-sm" style="text-decoration:none" href="${_dirVidUrl(p.final_path)}" download>⬇ Download</a>
        ${!p.nsfw ? `
          <label style="font-size:.78rem;color:var(--muted)"><input type="checkbox" id="dir-exp-youtube" checked style="width:auto"> 🎬 YouTube</label>
          <label style="font-size:.78rem;color:var(--muted)"><input type="checkbox" id="dir-exp-tiktok" style="width:auto"> 🎵 TikTok</label>
          <button class="btn-sm" ${busy ? 'disabled' : ''} onclick="dirExport(${p.id})">📤 Create draft Social post</button>
          ${p.social_post_id ? `<button class="btn-sm" onclick="switchView('social')">↗ Post #${p.social_post_id} on Social</button>` : ''}`
        : `<span style="font-size:.75rem;color:var(--muted)">🔒 Private project — no Social export; download only.</span>`}
      </div>
    </div>`;
  }
  return `<div class="card" style="margin-top:4px;text-align:center;color:var(--muted);font-size:.78rem">
    ${allDone
      ? (audioReady ? '🎞 Scenes + audio ready — hit <b>Assemble &amp; mix</b> (or <b>Produce</b>) to make the final video.'
                    : '🎬 All scenes rendered — hit <b>Produce</b> for audio + final mix, or run <b>Generate audio</b> then <b>Assemble &amp; mix</b> yourself.')
      : '🎬 Hit <b>Produce</b> — scenes render one by one, then music + voiceover generate, then the final mixes. Per-scene render buttons stay for redoing one scene.'}
  </div>`;
}

function _dirEditorHeader(p, editable) {
  const scenes = p.scenes || [];
  const allDone = scenes.length > 0 && scenes.every(s => s.status === 'done');
  const busy = _DIR_BUSY.includes(p.status) || scenes.some(s => s.status === 'queued' || s.status === 'rendering');
  const rail = editable ? `
    <button class="btn-sm" ${busy ? 'disabled' : ''} onclick="dirRestoryboard(${p.id})">🪄 Re-storyboard</button>
    <button class="btn-sm" ${busy || !scenes.length ? 'disabled' : ''} title="The whole pipeline: render every scene, generate music + voiceover, assemble & mix the final" onclick="dirRenderAll(${p.id})">🎬 Produce</button>
    <button class="btn-sm" ${busy || !allDone ? 'disabled' : ''} title="${allDone ? 'Music bed + narration via the audio engines' : 'Render all scenes first'}" onclick="dirAudio(${p.id})">🎙 Generate audio</button>
    <button class="btn-sm" ${busy || !allDone ? 'disabled' : ''} title="${allDone ? 'Stitch scenes + mix the audio on (CPU)' : 'Render all scenes first'}" onclick="dirAssemble(${p.id})">🎞 Assemble &amp; mix</button>
    <button class="btn-sm" ${busy ? 'disabled' : ''} title="Upload your own footage into the studio" onclick="dirUploadForShot(0)">📁 Upload footage</button>` : '';
  return `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px">
    <button class="btn-sm" onclick="dirBack()">← Back</button>
    ${editable
      ? `<input value="${esc(p.title || '')}" placeholder="Untitled project" style="flex:1;min-width:180px;font-weight:700;font-size:1rem;${_dirInp}" onchange="dirPatchProject(${p.id},{title:this.value})">`
      : `<div style="flex:1;font-weight:700;font-size:1rem">${esc(p.title || p.idea.slice(0, 60))}</div>`}
    ${_dirBadge(p.status)}${p.nsfw ? ' <span style="font-size:.8rem">🔒</span>' : ''}
    ${rail}
    <button class="btn-sm" style="background:#ef444420;color:#ef4444;border:1px solid #ef444450" onclick="dirDeleteProject(${p.id})">🗑</button>
  </div>`;
}

function _dirSfxRow(c, p) {
  const sceneOpts = (p.scenes || []).map(sc =>
    `<option value="${sc.id}"${sc.id === c.scene_id ? ' selected' : ''}>Scene ${sc.idx + 1}</option>`).join('');
  return `<div style="display:flex;gap:6px;align-items:center;margin-top:6px;flex-wrap:wrap">
    <input value="${esc(c.text)}" style="flex:2;min-width:160px;${_dirInp}" onchange="dirPatchCue(${c.id},{text:this.value})">
    <select style="${_dirInp}" onchange="dirSfxScene(${c.id},this.value)">${sceneOpts}</select>
    <label style="font-size:.72rem;color:var(--muted)">at&nbsp;s
      <input type="number" min="0" step="0.1" value="${c.offset_s || 0}" style="width:64px;${_dirInp}" onchange="dirPatchCue(${c.id},{offset_s:parseFloat(this.value)||0})"></label>
    <label style="font-size:.72rem;color:var(--muted)">gain
      <input type="number" min="0" max="2" step="0.05" value="${c.gain ?? 0.9}" style="width:64px;${_dirInp}" onchange="dirPatchCue(${c.id},{gain:parseFloat(this.value)||0})"></label>
    <button onclick="dirDeleteCue(${c.id})" style="width:auto;padding:2px 8px;font-size:.72rem;background:#ef444420;color:#ef4444;border:1px solid #ef444450">🗑</button>
  </div>`;
}

function _dirSceneRenderBlock(sc, p) {
  const shots = sc.shots || [];
  const busy = _DIR_BUSY.includes(p.status);
  if (sc.status === 'queued' || sc.status === 'rendering') {
    const ch = sc.chain || {};
    const total = ch.total_segments || shots.length || 1;
    const done = ch.completed_segments || 0;
    const curp = ch.current ? (ch.current.progress || 0) / 100 : 0;
    const msg = (ch.current && ch.current.progress_msg) ||
      (sc.status === 'queued' ? 'Waiting for the GPU queue…' : `Shot ${Math.min(done + 1, total)}/${total}`);
    return _dirProgressBar((done + curp) / total * 100, `🎬 ${msg}`);
  }
  let out = '';
  if (sc.status === 'failed') {
    out += `<div style="margin-top:8px;font-size:.72rem;color:#fca5a5;white-space:pre-wrap;font-family:monospace;max-height:70px;overflow:auto">${esc(sc.error || 'render failed')}</div>`;
  }
  const btns = [];
  if (shots.length && !busy && sc.status !== 'done') {
    if (sc.status === 'failed' && sc.chain_id) btns.push(`<button class="btn-sm" onclick="dirResumeScene(${sc.id})">▶ Resume render</button>`);
    btns.push(`<button class="btn-sm" onclick="dirRenderScene(${sc.id})">🎬 Render scene</button>`);
  }
  if (sc.status === 'done' && !busy) btns.push(`<button class="btn-sm" title="Render this scene again from scratch (new chain)" onclick="dirRenderScene(${sc.id})">🔁 Re-render</button>`);
  if (btns.length) out += `<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">${btns.join('')}</div>`;
  if (sc.status === 'done' && sc.scene_path) {
    out += `<div style="margin-top:8px">${_dirVideoTag(sc.scene_path, 'max-height:280px')}</div>`;
  }
  return out;
}

function _dirSceneCard(sc, p) {
  const shots = sc.shots || [];
  const dur = sc.duration_s ? `${sc.duration_s.toFixed(1)}s` : `~${sc.est_seconds || 0}s`;
  return `<div class="card" style="margin-bottom:14px">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
      <div style="font-weight:700;font-size:.9rem">🎬 Scene ${sc.idx + 1}${sc.title ? ' · ' + esc(sc.title) : ''} <span style="font-weight:400;color:var(--muted)">· ${dur}</span></div>
      ${_dirBadge(sc.status)}
    </div>
    ${_dirSceneRenderBlock(sc, p)}
    <label style="font-size:.75rem;color:var(--muted);display:block;margin-top:8px">Summary</label>
    <textarea style="${_dirTa};min-height:40px;margin-top:4px" onchange="dirPatchScene(${sc.id},{summary:this.value})">${esc(sc.summary || '')}</textarea>
    <label style="font-size:.75rem;color:var(--muted);display:block;margin-top:8px">🗣️ Voiceover (words spoken over this scene)</label>
    <textarea style="${_dirTa};min-height:40px;margin-top:4px" onchange="dirPatchScene(${sc.id},{voiceover:this.value})">${esc(sc.voiceover || '')}</textarea>
    <label style="font-size:.75rem;color:var(--muted);display:block;margin-top:8px">💬 On-screen caption</label>
    <input value="${esc(sc.caption || '')}" style="width:100%;margin-top:4px;${_dirInp}" onchange="dirPatchScene(${sc.id},{caption:this.value})">
    <div style="font-size:.75rem;color:var(--muted);margin-top:12px;margin-bottom:2px">🎥 Shots (each is one generated clip; consecutive shots continue the motion)</div>
    ${shots.map(sh => _dirShotRow(sh)).join('')}
    ${shots.length < 5 ? `<button class="btn-sm" style="margin-top:8px" onclick="dirAddShot(${sc.id})">＋ Shot</button>` : ''}
  </div>`;
}

function _dirShotRow(sh) {
  const st = sh.status && sh.status !== 'draft'
    ? `<span style="margin-left:4px">${_dirBadge(sh.status)}</span>` : '';
  const src = sh.source_path
    ? `<span style="font-size:.72rem;color:#22c55e;white-space:nowrap">🎞 own footage
        <button title="Back to AI-generated" onclick="dirClearSource(${sh.id})" style="width:auto;padding:0 6px;font-size:.72rem;background:transparent;color:#ef4444;border:1px solid #ef444450">✖</button></span>`
    : `<button class="btn-sm" title="Use your own footage for this shot (Phase 2: leading shots of the scene)" onclick="dirUploadForShot(${sh.id})" style="align-self:flex-start">📎 Footage</button>`;
  return `<div style="display:flex;gap:6px;align-items:flex-start;margin-top:8px;flex-wrap:wrap">
    <div style="font-size:.75rem;color:var(--muted);padding-top:8px;min-width:18px">#${sh.idx + 1}${st}</div>
    <textarea style="flex:3;min-width:220px;${_dirInp};resize:vertical;min-height:52px" onchange="dirPatchShot(${sh.id},{video_prompt:this.value})">${esc(sh.video_prompt)}</textarea>
    <div style="display:flex;flex-direction:column;gap:6px;flex:1;min-width:130px">
      <select style="${_dirInp}" onchange="dirPatchShot(${sh.id},{seconds:parseFloat(this.value)})">
        ${_DIR_SECS.map(s => `<option value="${s}"${Math.abs(s - sh.seconds) < 0.01 ? ' selected' : ''}>~${s}s (${s * 16 + 1}f)</option>`).join('')}
      </select>
      <input value="${esc(sh.caption || '')}" placeholder="caption (optional)" style="${_dirInp}" onchange="dirPatchShot(${sh.id},{caption:this.value})">
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        ${src}
        <button onclick="dirDeleteShot(${sh.id})" style="width:auto;padding:2px 8px;font-size:.72rem;background:#ef444420;color:#ef4444;border:1px solid #ef444450">🗑</button>
      </div>
    </div>
  </div>`;
}

/* ── editor actions (all thin PATCH/POST wrappers) ─────────────────────── */

async function _dirCall(path, method, body, refresh) {
  try {
    await api(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
    if (refresh && _dirOpenId) await _dirRenderEditor(_dirOpenId);
    else toast('Saved');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function dirPatchProject(id, fields) { await _dirCall(`/api/studio/projects/${id}`, 'PATCH', fields); }
window.dirPatchProject = dirPatchProject;

async function dirPatchRes(id, res) {
  const [w, h] = res.split('x').map(Number);
  await dirPatchProject(id, { width: w, height: h });
}
window.dirPatchRes = dirPatchRes;

async function dirSaveScript(id) {
  await dirPatchProject(id, {
    script: document.getElementById('dir-script').value,
    captions: document.getElementById('dir-captions').value,
  });
}
window.dirSaveScript = dirSaveScript;

async function dirPatchScene(id, fields) { await _dirCall(`/api/studio/scenes/${id}`, 'PATCH', fields); }
window.dirPatchScene = dirPatchScene;

async function dirPatchShot(id, fields) { await _dirCall(`/api/studio/shots/${id}`, 'PATCH', fields); }
window.dirPatchShot = dirPatchShot;

async function dirDeleteShot(id) {
  if (!confirm('Delete this shot?')) return;
  await _dirCall(`/api/studio/shots/${id}`, 'DELETE', undefined, true);
}
window.dirDeleteShot = dirDeleteShot;

async function dirAddShot(sceneId) {
  const prompt_ = prompt('Video prompt for the new shot (subject, action, camera, lighting, style):');
  if (!prompt_ || !prompt_.trim()) return;
  await _dirCall(`/api/studio/scenes/${sceneId}/shots`, 'POST', { video_prompt: prompt_.trim(), seconds: 3 }, true);
}
window.dirAddShot = dirAddShot;

async function dirPatchCue(id, fields) { await _dirCall(`/api/studio/cues/${id}`, 'PATCH', fields); }
window.dirPatchCue = dirPatchCue;

async function dirSfxScene(cueId, sceneId) {
  await dirPatchCue(cueId, { scene_id: parseInt(sceneId) });
}
window.dirSfxScene = dirSfxScene;

async function dirDeleteCue(id) {
  if (!confirm('Delete this cue?')) return;
  await _dirCall(`/api/studio/cues/${id}`, 'DELETE', undefined, true);
}
window.dirDeleteCue = dirDeleteCue;

async function dirAddSfx(projectId) {
  if (!_dirOpenId) return;
  const p = await api(`/api/studio/projects/${projectId}`);
  if (!(p.scenes || []).length) { toast('No scenes yet', 'warn'); return; }
  const text = prompt("Describe the sound (e.g. 'single deep whoosh, cinematic transition sound, no music'):");
  if (!text || !text.trim()) return;
  let sceneIdx = 0;
  if (p.scenes.length > 1) {
    const v = prompt(`Which scene? (1-${p.scenes.length})`, '1');
    if (v === null) return;
    sceneIdx = Math.max(0, Math.min(p.scenes.length - 1, (parseInt(v) || 1) - 1));
  }
  await _dirCall('/api/studio/cues', 'POST', {
    project_id: projectId, scene_id: p.scenes[sceneIdx].id, kind: 'sfx', text: text.trim(),
  }, true);
}
window.dirAddSfx = dirAddSfx;

/* ── Phase 2: render / audio / assemble / export / own footage ─────────── */

async function dirRenderScene(sceneId) {
  await _dirCall(`/api/studio/scenes/${sceneId}/render`, 'POST', {}, true);
}
window.dirRenderScene = dirRenderScene;

async function dirResumeScene(sceneId) {
  await _dirCall(`/api/studio/scenes/${sceneId}/resume`, 'POST', {}, true);
}
window.dirResumeScene = dirResumeScene;

async function dirRenderAll(id) {
  await _dirCall(`/api/studio/projects/${id}/render`, 'POST', {}, true);
}
window.dirRenderAll = dirRenderAll;

async function dirAudio(id) {
  await _dirCall(`/api/studio/projects/${id}/audio`, 'POST', {}, true);
}
window.dirAudio = dirAudio;

async function dirAssemble(id) {
  await _dirCall(`/api/studio/projects/${id}/assemble`, 'POST', {}, true);
}
window.dirAssemble = dirAssemble;

async function dirExport(id) {
  const platforms = [];
  if (document.getElementById('dir-exp-youtube')?.checked) platforms.push('youtube');
  if (document.getElementById('dir-exp-tiktok')?.checked) platforms.push('tiktok');
  if (!platforms.length) { toast('Pick at least one platform', 'warn'); return; }
  try {
    const r = await api(`/api/studio/projects/${id}/export`, { method: 'POST', body: JSON.stringify({ platforms }) });
    toast(`📤 Draft post #${r.post_id} created — finish it on the Social tab`);
    if (_dirOpenId) await _dirRenderEditor(_dirOpenId);
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.dirExport = dirExport;

/* own footage: one hidden <input type=file>, reused; _dirUploadShot = target
   shot (0 = just add to the studio's footage library under /videos). */
let _dirUploadShot = 0;

function dirUploadForShot(shotId) {
  const inp = document.getElementById('dir-upload-file');
  if (!inp) return;
  _dirUploadShot = shotId || 0;
  inp.value = '';
  inp.onchange = _dirDoUpload;
  inp.click();
}
window.dirUploadForShot = dirUploadForShot;

async function _dirDoUpload() {
  const inp = document.getElementById('dir-upload-file');
  const f = inp && inp.files && inp.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  if (_dirUploadShot) fd.append('shot_id', String(_dirUploadShot));
  toast('⏫ Uploading footage…');
  try {
    // raw fetch: api() forces a JSON content-type, multipart needs its own boundary
    const r = await fetch(API + '/api/studio/upload', { method: 'POST', body: fd });
    if (!r.ok) {
      let m = `HTTP ${r.status}`;
      try { const j = await r.json(); m = (typeof j.detail === 'string' && j.detail) || m; } catch {}
      throw new Error(m);
    }
    const j = await r.json();
    toast(_dirUploadShot ? '🎞 Footage attached as the shot\'s source clip' : `🎞 Uploaded: ${j.filename}`);
    if (_dirOpenId) await _dirRenderEditor(_dirOpenId);
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function dirClearSource(shotId) {
  await _dirCall(`/api/studio/shots/${shotId}`, 'PATCH', { source_path: '' }, true);
}
window.dirClearSource = dirClearSource;

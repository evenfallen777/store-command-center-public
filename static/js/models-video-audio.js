/* Models — video & audio families (catalog HTML + video download polling). Split from tab-models.js. */
async function pollVideoDownload(key, safeVid) {
  const totalBytes = VIDEO_MODEL_SIZES[key] || 0;
  const statusEl = document.getElementById(`vdl-status-${safeVid}`);
  const barEl    = document.getElementById(`vdl-bar-${safeVid}`);
  for (let i = 0; i < 900; i++) {  // up to 60 min
    await new Promise(r => setTimeout(r, 4000));
    try {
      const s = await api(`/api/video-models/${encodeURIComponent(key)}/download-status`);
      if (s.status === 'done') {
        if (statusEl) statusEl.innerHTML = '&#10003; Download complete!';
        if (barEl) { barEl.style.width = '100%'; barEl.style.background = 'var(--green)'; }
        toast(key.split('--')[1] + ' installed!');
        setTimeout(() => refreshStudioModels(), 2000);
        return;
      }
      if (s.status === 'error') {
        if (statusEl) statusEl.innerHTML = '&#10060; ' + esc(s.error || 'Download failed');
        if (barEl) barEl.style.background = 'var(--red)';
        toast('Video model download failed: ' + (s.error || 'unknown'), 'error');
        return;
      }
      if (s.status === 'cancelled') return;
      if (s.bytes_downloaded) {
        const mb = (s.bytes_downloaded / 1048576).toFixed(0);
        if (totalBytes) {
          const pct = Math.min(99, (s.bytes_downloaded / totalBytes) * 100);
          if (barEl) barEl.style.width = pct.toFixed(1) + '%';
          const gbTotal = (totalBytes / 1073741824).toFixed(1);
          if (statusEl) statusEl.textContent = `Downloading\u2026 ${mb} MB / ${gbTotal} GB`;
        } else {
          if (statusEl) statusEl.textContent = `Downloading\u2026 ${mb} MB`;
        }
      }
    } catch {}
  }
}

function _videoModelsHTML(videoModels) {
  videoModels = videoModels || [];
  let h = `<div class="section-header"><div><div class="section-title">&#127916; Video Models</div>
      <div class="section-sub">HuggingFace diffusers models for text-to-video &middot; cached on the GPU box</div></div></div>
    <div style="display:grid;gap:12px;max-width:820px;">`;
  for (const vm of videoModels) {
    const safeVid = vm.key.replace(/[^a-zA-Z0-9_-]/g, '_');
    const isInst  = vm.installed;
    const dlSt    = vm.dl_status || null;
    h += `<div class="queue-card" id="vmc-${safeVid}">
      <div style="display:flex;align-items:flex-start;gap:12px;">
        <div style="flex:1;">
          <div style="font-size:.95rem;font-weight:700;">${esc(vm.label)}</div>
          <div style="font-size:.75rem;color:var(--muted);margin-top:3px;">${esc(vm.style)} &middot; VRAM ${esc(vm.vram)} &middot; ${esc(vm.size)} &middot; ${esc(vm.source)}</div>
          ${vm.note ? `<div style="font-size:.72rem;color:var(--warn);margin-top:3px;">&#9432; ${esc(vm.note)}</div>` : ''}
          <div style="font-size:.7rem;color:var(--border);margin-top:3px;">${esc(vm.model_id)}</div>
        </div>
        <div style="flex-shrink:0;text-align:right;">
          ${isInst
            ? `<span style="color:var(--green);font-weight:700;font-size:.82rem;">&#10003; Installed</span>`
            : dlSt === 'downloading'
              ? `<button class="btn-sm" disabled>&#11015; Downloading&hellip;</button>`
              : `<button class="btn-sm primary" data-action="dl-video-model" data-key="${esc(vm.key)}" id="vdl-btn-${safeVid}" title="Download this text-to-video model to the GPU box (several GB, one time). Needed before it can be selected in Video generation.">&#11015; Download</button>`}
        </div>
      </div>
      ${_videoTuneHTML(vm, safeVid)}
      <div class="dl-progress" id="vdl-prog-${safeVid}" style="display:${dlSt==='downloading'?'block':'none'};margin-top:10px;">
        <div style="font-size:.75rem;color:var(--muted);margin-bottom:5px;" id="vdl-status-${safeVid}">Downloading&hellip;</div>
        <div style="background:var(--border);border-radius:4px;height:6px;overflow:hidden;">
          <div style="background:var(--accent);height:100%;width:0%;transition:width .5s;" id="vdl-bar-${safeVid}"></div>
        </div>
        <div style="margin-top:8px;">
          <button class="btn-sm danger" data-action="cancel-vdl" data-key="${esc(vm.key)}" data-safeid="${safeVid}" style="font-size:.7rem;">&#10005; Cancel</button>
        </div>
      </div>
    </div>`;
  }
  if (!videoModels.length) h += `<div class="empty"><div class="empty-icon">&#127916;</div>Could not fetch video models. Check box connection.</div>`;
  h += `</div>`;
  return h;
}

// ── Per-model video-gen tuning (⚙ Tune defaults) ──────────────────────────────
// Collapsible editor for a video model's generation defaults (width/height/
// frames/steps/fps/guidance/strength). Placeholders show the stock catalog
// value; a filled field becomes the owner's override (stored server-side in
// the video_model_settings setting). Blank everything = stock behavior.
const _VIDEO_TUNE_FIELDS = [
  ['width', 'Width'], ['height', 'Height'], ['num_frames', 'Frames'],
  ['steps', 'Steps'], ['fps', 'FPS'], ['guidance', 'Guidance (CFG)'],
  ['strength', 'Chain strength'],
];

function _videoTuneHTML(vm, safe) {
  const d = vm.gen_defaults || {};
  const o = vm.gen_overrides || {};
  if (!Object.keys(d).length) return '';
  const tuned = Object.keys(o).length;
  const inputs = _VIDEO_TUNE_FIELDS.filter(([k]) => d[k] !== undefined).map(([k, label]) => `
    <label style="font-size:.7rem;color:var(--muted);display:block;">${label}
      <input type="number" step="any" data-vk="${k}" value="${o[k] !== undefined ? o[k] : ''}"
        placeholder="${d[k]}" title="Stock default: ${d[k]} — leave blank to use it"
        style="width:100%;margin-top:2px;padding:4px 6px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:5px;box-sizing:border-box;font-size:.78rem;">
    </label>`).join('');
  return `
    <div style="margin-top:8px;">
      <button class="btn-sm" onclick="toggleVideoTune('${safe}')" style="font-size:.72rem;"
        title="Per-model generation defaults: what this model uses when you don't pick values explicitly (and the guidance/CFG it renders with). Blank fields keep the stock value shown in grey.">
        &#9881; Tune defaults${tuned ? ` <span style="color:var(--accent);">· ${tuned} set</span>` : ''}</button>
      <div id="vmtune-${safe}" style="display:none;margin-top:8px;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px;">
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px;">${inputs}</div>
        <div style="font-size:.68rem;color:var(--muted);margin-top:6px;">Used when the Videos/Chain forms don't override them; guidance always renders with this value. Blank = stock default.</div>
        <div style="display:flex;gap:8px;margin-top:8px;">
          <button class="btn-sm primary" onclick="saveVideoTune('${esc(vm.key)}','${safe}')" style="font-size:.72rem;">&#128190; Save</button>
          <button class="btn-sm" onclick="resetVideoTune('${esc(vm.key)}','${safe}')" style="font-size:.72rem;">&#8634; Reset to stock</button>
        </div>
      </div>
    </div>`;
}

function toggleVideoTune(safe) {
  const el = document.getElementById('vmtune-' + safe);
  if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}
window.toggleVideoTune = toggleVideoTune;

async function saveVideoTune(key, safe) {
  const body = {};
  document.querySelectorAll(`#vmtune-${safe} input[data-vk]`).forEach(inp => {
    if (inp.value.trim() !== '') body[inp.dataset.vk] = parseFloat(inp.value);
  });
  try {
    const r = await api(`/api/video-models/${encodeURIComponent(key)}/gen-settings`,
                        { method: 'PUT', body: JSON.stringify(body) });
    const n = Object.keys(r.overrides || {}).length;
    toast(n ? `Saved — ${n} setting${n > 1 ? 's' : ''} tuned for this model`
            : 'Saved — model back on stock defaults');
  } catch (e) { toast('Save failed: ' + e.message, 'error'); }
}
window.saveVideoTune = saveVideoTune;

async function resetVideoTune(key, safe) {
  try {
    await api(`/api/video-models/${encodeURIComponent(key)}/gen-settings`, { method: 'DELETE' });
    document.querySelectorAll(`#vmtune-${safe} input[data-vk]`).forEach(inp => { inp.value = ''; });
    toast('Reset to stock defaults');
  } catch (e) { toast('Reset failed: ' + e.message, 'error'); }
}
window.resetVideoTune = resetVideoTune;


function _audioModelsHTML(audioModels) {
  audioModels = audioModels || [];
  let h = `<div class="section-header"><div><div class="section-title">&#127925; Audio Models</div>
      <div class="section-sub">Music (MusicGen, ACE-Step, Stable Audio) &amp; voice (MMS-TTS) &middot; used by the Audio tab &amp; video sound</div></div></div>
    <div style="display:grid;gap:12px;max-width:820px;">`;
  for (const am of audioModels) {
    const safe = am.key.replace(/[^a-zA-Z0-9_-]/g, '_');
    const dlSt = am.dl_status || null;
    h += `<div class="queue-card" id="amc-${safe}">
      <div style="display:flex;align-items:flex-start;gap:12px;">
        <div style="flex:1;">
          <div style="font-size:.95rem;font-weight:700;">${esc(am.label)}</div>
          <div style="font-size:.75rem;color:var(--muted);margin-top:3px;">${esc(am.kind)} &middot; VRAM ${esc(am.vram)} &middot; ${esc(am.size)}</div>
          ${am.note ? `<div style="font-size:.72rem;color:var(--warn);margin-top:3px;">&#9432; ${esc(am.note)}</div>` : ''}
          ${am.dl_error ? `<div style="font-size:.68rem;color:var(--warn);margin-top:3px;white-space:pre-wrap;">${esc(am.dl_error)}</div>` : ''}
          <div style="font-size:.7rem;color:var(--border);margin-top:3px;">${esc(am.repo)}</div>
        </div>
        <div style="flex-shrink:0;text-align:right;" id="am-btn-${safe}">
          ${am.installed
            ? `<span style="color:var(--green);font-weight:700;font-size:.82rem;">&#10003; Installed</span>`
            : dlSt === 'downloading'
              ? `<button class="btn-sm" disabled>&#11015; ${am.install ? 'Installing' : 'Downloading'}&hellip;</button>`
              : `<button class="btn-sm primary" data-action="dl-audio-model" data-key="${esc(am.key)}" data-install="${am.install?1:0}" id="adl-btn-${safe}" title="Download/install this audio model on the GPU box (one time). Used by the Audio tab and for adding music/voice to videos.">&#11015; ${am.install ? 'Install' : 'Download'}</button>`}
        </div>
      </div>
      <div class="dl-progress" id="adl-prog-${safe}" style="display:${dlSt==='downloading'?'block':'none'};margin-top:10px;">
        <div style="font-size:.75rem;color:var(--muted);margin-bottom:5px;" id="adl-status-${safe}">Working&hellip;</div>
        <div style="background:var(--border);border-radius:4px;height:6px;overflow:hidden;">
          <div style="background:var(--accent);height:100%;width:40%;transition:width .5s;" id="adl-bar-${safe}"></div>
        </div>
        <div style="margin-top:8px;">
          <button class="btn-sm danger" data-action="cancel-adl" data-key="${esc(am.key)}" data-safeid="${safe}" style="font-size:.7rem;">&#10005; Cancel</button>
        </div>
      </div>
    </div>`;
  }
  if (!audioModels.length) h += `<div class="empty"><div class="empty-icon">&#127925;</div>Could not fetch audio models. Check node connection.</div>`;
  h += `</div>`;
  return h;
}

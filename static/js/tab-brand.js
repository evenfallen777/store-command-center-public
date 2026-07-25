'use strict';

/* 🎯 Brand Kit — a Studio sub-tab. Create and manage the branding for each
   business/site as a persistent, reusable package (multiple brands supported):
   name/tagline/descriptions (LLM ✨ on the unified queue), colors, links, and
   logo + banner assets you can Generate (GPU queue) / Upload / Download —
   the single source of truth so branding stops drifting. */

let _brandOpenId = null;      // brand open in the editor; null = list
let _brandPollTimer = null;
let _brandPresets = [];       // /api/brands/presets cache

const _bkInp = 'padding:7px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;font-family:inherit;font-size:.85rem;box-sizing:border-box;width:100%';
const _bkTa = `${_bkInp};resize:vertical`;
const _BK_LINKS = [
  ['website', '🌐 Website'], ['twitter', '𝕏 / Twitter'], ['instagram', '📸 Instagram'],
  ['youtube', '▶️ YouTube'], ['facebook', '📘 Facebook'], ['tiktok', '🎵 TikTok'],
];

function _bkClearPoll() {
  if (_brandPollTimer) { clearTimeout(_brandPollTimer); _brandPollTimer = null; }
}

function _bkAssetUrl(id, kind) {
  return `${API}/api/brands/${id}/download/${kind}?inline=1&t=${Date.now()}`;
}

async function renderBrandKit() {
  _bkClearPoll();
  if (!_brandPresets.length) {
    try { _brandPresets = await api('/api/brands/presets'); } catch { _brandPresets = []; }
  }
  if (_brandOpenId) await _bkRenderEditor(_brandOpenId);
  else await _bkRenderList();
}
window.renderBrandKit = renderBrandKit;

/* ── Brand list ──────────────────────────────────────────────────────────── */

async function _bkRenderList() {
  const el = viewRoot();
  let brands = [];
  try { brands = await api('/api/brands'); }
  catch (e) { el.innerHTML = `<div class="empty">Error loading brands: ${esc(e.message)}</div>`; return; }
  el.innerHTML = `
    <div class="section-header">
      <div>
        <div class="section-title">🎯 Brand Kit</div>
        <div class="section-sub">One source of truth per brand — reuse the saved logo, banner &amp; copy instead of regenerating them</div>
      </div>
      <button style="width:auto;padding:8px 18px" onclick="bkNewBrand()">＋ New brand</button>
    </div>
    ${brands.length ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px">
      ${brands.map(b => `<div class="card" style="cursor:pointer" onclick="bkOpenBrand(${b.id})">
        <div style="display:flex;gap:10px;align-items:center">
          ${b.logo_path ? `<img src="${_bkAssetUrl(b.id, 'logo')}" alt="" style="width:44px;height:44px;border-radius:8px;object-fit:cover;background:var(--bg)">`
                        : `<div style="width:44px;height:44px;border-radius:8px;background:var(--bg);display:flex;align-items:center;justify-content:center;font-size:1.2rem">🎯</div>`}
          <div style="flex:1;min-width:0">
            <div style="font-weight:700;font-size:.9rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(b.name)}
              ${b.is_default ? '<span style="font-size:.65rem;font-weight:700;padding:1px 6px;border-radius:8px;background:var(--accent);color:#fff;margin-left:6px">DEFAULT</span>' : ''}</div>
            <div style="font-size:.74rem;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(b.tagline || b.slug)}</div>
          </div>
        </div>
      </div>`).join('')}
    </div>` : `<div class="empty"><div class="empty-icon">🎯</div>No brands yet — create the first one and stop the branding drift.</div>`}`;
}

async function bkNewBrand() {
  const name = prompt('Brand name?');
  if (!name || !name.trim()) return;
  try {
    const b = await api('/api/brands', { method: 'POST', body: JSON.stringify({ name: name.trim() }) });
    toast('🎯 Brand created');
    _brandOpenId = b.id;
    await renderBrandKit();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function bkOpenBrand(id) {
  _brandOpenId = id;
  await renderBrandKit();
}

async function bkBackToList() {
  _brandOpenId = null;
  await renderBrandKit();
}

/* ── Editor ──────────────────────────────────────────────────────────────── */

async function _bkRenderEditor(id) {
  const el = viewRoot();
  let b;
  try { b = await api(`/api/brands/${id}`); }
  catch (e) { _brandOpenId = null; el.innerHTML = `<div class="empty">Brand not found (${esc(e.message)})</div>`; return; }
  const colors = b.colors || {}, links = b.links || {};
  const genField = (field, label) => `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-top:10px">
      <label style="font-size:.78rem;color:var(--muted)">${label}</label>
      <button class="btn-sm" title="Generate with the brand LLM writer (unified queue)" onclick="bkGenIdentity(this)">✨ Generate</button>
    </div>`;
  el.innerHTML = `
    <div class="section-header">
      <div>
        <div class="section-title">🎯 ${esc(b.name)}</div>
        <div class="section-sub">${esc(b.slug)} · created ${esc((b.created_at || '').slice(0, 10))}</div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn-sm" onclick="bkBackToList()">← All brands</button>
        <button class="btn-sm" ${b.is_default ? 'disabled' : ''} onclick="bkSetDefault(${b.id})">${b.is_default ? '⭐ Default brand' : '☆ Set default'}</button>
        <button class="btn-sm" onclick="bkExport(${b.id})">📦 Export package</button>
        <button class="btn-sm" style="color:var(--danger,#ef4444)" onclick="bkDelete(${b.id})">🗑 Delete</button>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px">
      <div class="card">
        <div style="font-weight:700;font-size:.88rem">Identity</div>
        <label style="font-size:.78rem;color:var(--muted);display:block;margin-top:10px">Name</label>
        <input id="bk-name" value="${esc(b.name)}" style="${_bkInp};margin-top:4px">
        ${genField('tagline', 'Tagline')}
        <input id="bk-tagline" value="${esc(b.tagline || '')}" placeholder="A punchy one-liner" style="${_bkInp};margin-top:4px">
        ${genField('description_short', 'Short description')}
        <textarea id="bk-short" rows="2" placeholder="1–2 sentences" style="${_bkTa};margin-top:4px">${esc(b.description_short || '')}</textarea>
        ${genField('description_long', 'Long description')}
        <textarea id="bk-long" rows="6" placeholder="The full brand story" style="${_bkTa};margin-top:4px">${esc(b.description_long || '')}</textarea>
        <label style="font-size:.78rem;color:var(--muted);display:block;margin-top:10px">Style notes (voice, imagery, dos &amp; don'ts)</label>
        <textarea id="bk-style" rows="2" style="${_bkTa};margin-top:4px">${esc(b.style_notes || '')}</textarea>
        <div id="bk-identity-out" style="margin-top:8px"></div>
      </div>
      <div class="card">
        <div style="font-weight:700;font-size:.88rem">Colors &amp; links</div>
        <div style="display:flex;gap:14px;margin-top:10px;flex-wrap:wrap">
          ${['primary', 'secondary', 'accent'].map(k => `
            <label style="font-size:.78rem;color:var(--muted);display:flex;flex-direction:column;gap:4px">${k[0].toUpperCase() + k.slice(1)}
              <input type="color" id="bk-color-${k}" value="${esc(colors[k] || '#888888')}" style="width:56px;height:34px;padding:2px;border:1px solid var(--border);border-radius:6px;background:var(--bg)">
            </label>`).join('')}
        </div>
        ${_BK_LINKS.map(([k, label]) => `
          <label style="font-size:.78rem;color:var(--muted);display:block;margin-top:8px">${label}
            <input id="bk-link-${k}" value="${esc(links[k] || '')}" placeholder="${k === 'website' ? 'https://…' : '@handle or URL'}" style="${_bkInp};margin-top:4px">
          </label>`).join('')}
        <button style="margin-top:14px;width:auto;padding:9px 22px" onclick="bkSave(${b.id})">💾 Save brand</button>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px;margin-top:14px">
      ${_bkAssetPanel(b, 'logo')}
      ${_bkAssetPanel(b, 'banner')}
    </div>`;
  _bkMaybePoll(b);
}

function _bkAssetPanel(b, kind) {
  const status = b[`${kind}_status`] || '';
  const err = b[`${kind}_error`] || '';
  const has = !!b[`${kind}_path`];
  const busy = status === 'queued' || status === 'generating';
  const presetSel = kind === 'banner' ? `
    <select id="bk-banner-preset" style="${_bkInp};width:auto;flex:1;min-width:150px">
      ${(_brandPresets.length ? _brandPresets : [{ key: 'x_header', label: 'X / Twitter header', w: 1500, h: 500 }])
        .map(p => `<option value="${esc(p.key)}" ${(b.meta || {}).banner_preset === p.key ? 'selected' : ''}>${esc(p.label)} (${p.w}×${p.h})</option>`).join('')}
    </select>` : '';
  return `<div class="card">
    <div style="font-weight:700;font-size:.88rem">${kind === 'logo' ? '🎨 Logo' : '🖼️ Banner'}
      ${busy ? '<span style="font-size:.7rem;color:var(--warn,#f59e0b);margin-left:8px">⏳ ' + esc(status) + '…</span>' : ''}</div>
    <div style="margin-top:10px;min-height:80px;display:flex;align-items:center;justify-content:center;background:var(--bg);border-radius:8px;overflow:hidden">
      ${has ? `<img src="${_bkAssetUrl(b.id, kind)}" alt="${kind}" style="max-width:100%;${kind === 'logo' ? 'max-height:200px' : 'max-height:160px'};object-fit:contain">`
            : `<div style="font-size:.78rem;color:var(--muted);padding:24px">No ${kind} yet — generate one or upload your own</div>`}
    </div>
    ${err && status === 'failed' ? `<div style="font-size:.72rem;color:var(--danger,#ef4444);margin-top:6px">${esc(err)}</div>` : ''}
    <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;align-items:center">
      ${presetSel}
      <input id="bk-${kind}-prompt" placeholder="Optional art direction…" style="${_bkInp};flex:2;min-width:160px;width:auto">
    </div>
    <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
      <button class="btn-sm" ${busy ? 'disabled' : ''} onclick="bkGenAsset(${b.id},'${kind}',this)">✨ Generate</button>
      <button class="btn-sm" onclick="document.getElementById('bk-${kind}-file').click()">⬆️ Upload</button>
      <button class="btn-sm" ${has ? '' : 'disabled'} onclick="bkDownload(${b.id},'${kind}')">⬇️ Download</button>
      <input type="file" id="bk-${kind}-file" accept=".png,.jpg,.jpeg,.webp,.svg" style="display:none" onchange="bkUpload(${b.id},'${kind}',this)">
    </div>
  </div>`;
}

/* ── actions ─────────────────────────────────────────────────────────────── */

function _bkCollect() {
  const colors = {}, links = {};
  ['primary', 'secondary', 'accent'].forEach(k => {
    const el = document.getElementById(`bk-color-${k}`);
    if (el) colors[k] = el.value;
  });
  _BK_LINKS.forEach(([k]) => {
    const el = document.getElementById(`bk-link-${k}`);
    if (el && el.value.trim()) links[k] = el.value.trim();
  });
  return {
    name: (document.getElementById('bk-name') || {}).value || undefined,
    tagline: (document.getElementById('bk-tagline') || {}).value,
    description_short: (document.getElementById('bk-short') || {}).value,
    description_long: (document.getElementById('bk-long') || {}).value,
    style_notes: (document.getElementById('bk-style') || {}).value,
    colors, links,
  };
}

async function bkSave(id) {
  try {
    await api(`/api/brands/${id}`, { method: 'PATCH', body: JSON.stringify(_bkCollect()) });
    toast('💾 Brand saved');
    await renderBrandKit();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function bkSetDefault(id) {
  try {
    await api(`/api/brands/${id}/set-default`, { method: 'POST' });
    toast('⭐ Default brand set');
    await renderBrandKit();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function bkDelete(id) {
  if (!confirm('Delete this brand and its saved assets? This cannot be undone.')) return;
  try {
    await api(`/api/brands/${id}`, { method: 'DELETE' });
    toast('🗑 Brand deleted');
    _brandOpenId = null;
    await renderBrandKit();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

function bkExport(id) {
  window.location = `${API}/api/brands/${id}/export`;
}

function bkDownload(id, kind) {
  window.location = `${API}/api/brands/${id}/download/${kind}`;
}

/* identity text — one LLM turn fills tagline + short + long (user reviews, then Save) */
async function bkGenIdentity(btn) {
  const id = _brandOpenId;
  if (!id) return;
  if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
  try {
    // save current fields first so the writer grounds on what's on screen
    await api(`/api/brands/${id}`, { method: 'PATCH', body: JSON.stringify(_bkCollect()) });
    const extra = prompt('Tell the writer about this brand (what it is, who it serves) — optional:') || '';
    const { task_id } = await api(`/api/brands/${id}/generate/description`,
      { method: 'POST', body: JSON.stringify({ prompt: extra }) });
    toast('✨ Writing brand copy on the queue…');
    _bkPollIdentity(task_id, btn);
  } catch (e) {
    toast('Error: ' + e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = '✨ Generate'; }
  }
}

function _bkPollIdentity(taskId, btn) {
  _bkClearPoll();
  _brandPollTimer = setTimeout(async () => {
    try {
      const t = await api(`/api/task/${taskId}`);
      if (t.status === 'done' && t.result) {
        const r = t.result;
        if (r.tagline) document.getElementById('bk-tagline').value = r.tagline;
        if (r.description_short) document.getElementById('bk-short').value = r.description_short;
        if (r.description_long) document.getElementById('bk-long').value = r.description_long;
        const out = document.getElementById('bk-identity-out');
        if (out && (r.names || []).length) {
          out.innerHTML = `<div style="font-size:.74rem;color:var(--muted)">💡 Name ideas: ${r.names.map(esc).join(' · ')}</div>`;
        }
        toast('✨ Copy drafted — review the fields, then 💾 Save');
        if (btn) { btn.disabled = false; btn.textContent = '✨ Generate'; }
        return;
      }
      if (t.status === 'failed' || t.status === 'error' || t.status === 'not_found' || t.status === 'cancelled') {
        toast('Identity generation failed: ' + (t.error || t.status), 'error');
        if (btn) { btn.disabled = false; btn.textContent = '✨ Generate'; }
        return;
      }
      _bkPollIdentity(taskId, btn);   // still pending/running
    } catch {
      _bkPollIdentity(taskId, btn);
    }
  }, 2000);
}

/* image assets — generation rides the GPU queue; poll the brand row for status */
async function bkGenAsset(id, kind, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Queuing…'; }
  const extra = (document.getElementById(`bk-${kind}-prompt`) || {}).value || '';
  const body = { prompt: extra };
  if (kind === 'banner') body.preset = (document.getElementById('bk-banner-preset') || {}).value || 'x_header';
  try {
    // persist any unsaved edits first — the generator grounds on the saved brand
    await api(`/api/brands/${id}`, { method: 'PATCH', body: JSON.stringify(_bkCollect()) });
    await api(`/api/brands/${id}/generate/${kind}`, { method: 'POST', body: JSON.stringify(body) });
    toast(`✨ ${kind === 'logo' ? 'Logo' : 'Banner'} queued on the GPU`);
    await renderBrandKit();   // re-render shows the ⏳ status + starts polling
  } catch (e) {
    toast('Error: ' + e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = '✨ Generate'; }
  }
}

async function bkUpload(id, kind, input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(`${API}/api/brands/${id}/upload/${kind}`, { method: 'POST', body: fd });
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { const j = await r.json(); msg = j.detail || j.error || msg; } catch {}
      throw new Error(msg);
    }
    toast(`⬆️ ${kind === 'logo' ? 'Logo' : 'Banner'} uploaded`);
    await renderBrandKit();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { input.value = ''; }
}

function _bkMaybePoll(b) {
  const busy = ['queued', 'generating'];
  if (busy.includes(b.logo_status) || busy.includes(b.banner_status)) {
    _bkClearPoll();
    _brandPollTimer = setTimeout(async () => {
      if (_currentView !== 'brandkit' || _brandOpenId !== b.id) return;
      await renderBrandKit();
    }, 3000);
  }
}

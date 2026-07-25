/* Restored from pre_unification_backup (Jul 9) — real tab implementation.
   Part of the modular frontend: one file per tab. */
/* ══ IMAGE GENERATOR TAB ══ */
async function renderImageGenerator() {
  const main = viewRoot();

  // Load installed models
  let modelOptions = '<option value="">Default Model</option>';
  try {
    const data = await api('/api/models');
    const installed = data.installed || [];
    const rec = data.recommended || [];
    const labelMap = {};
    for (const m of rec) labelMap[m.filename] = m.label || m.filename;
    if (installed.length) {
      modelOptions = installed.map(f => `<option value="${esc(f)}">${esc(labelMap[f] || f)}</option>`).join('');
    }
  } catch {}

  // Load generator designs (source='generator', status='review')
  let designs = [];
  try { designs = await api('/api/designs?status=review&source=generator'); } catch {}

  main.innerHTML = `
    <div class="view-header">
      <div class="view-title">&#127912; Image Generator</div>
      <div class="view-sub">Generate images here &mdash; send to Review Queue when ready, or download directly</div>
    </div>

    <div class="ig-form-card">
      <div style="font-size:.85rem;font-weight:700;margin-bottom:12px;">&#9889; New Generation ${hlp('Type a design idea, choose a model + how many variations, then Generate. Buttons: ✨ Enhance rewrites your idea into a richer image prompt (via the LLM); 🔬 Research digs deeper and also suggests a title + tags; 📎 Image ref conditions the output on an uploaded image. Results appear below — Review sends one into the main pipeline, Save downloads it.')}</div>
      <textarea id="ig-prompt" placeholder="Describe your design idea&hellip;"></textarea>
      <div class="ig-form-row">
        <select id="ig-model" title="Which ComfyUI checkpoint renders these images">${modelOptions}</select>
        <select id="ig-variations" title="How many images to generate from this prompt">
          <option value="1">1 variation</option>
          <option value="2">2 variations</option>
          <option value="3" selected>3 variations</option>
          <option value="4">4 variations</option>
          <option value="6">6 variations</option>
        </select>
      </div>
      <div class="ig-form-row" style="margin-top:6px;">
        <button class="btn-sm" id="ig-enhance">&#10024; Enhance</button>
        <button class="btn-sm" id="ig-research">&#128300; Research</button>
        <label class="btn-sm" style="cursor:pointer;display:inline-flex;align-items:center;gap:4px;">
          &#128206; Image ref
          <input type="file" id="ig-image" accept="image/*" style="display:none;">
        </label>
        <span id="ig-image-name" style="font-size:.68rem;color:var(--accent2);"></span>
      </div>
      <button class="btn-gen-main" id="ig-generate" style="width:100%;margin-top:12px;">&#127912; Generate</button>
    </div>

    <div class="section-header" style="margin-top:4px;">
      <div><div class="section-title">&#128247; Generated Images</div><div class="section-sub">Send to Review Queue to enter the main pipeline, or download directly</div></div>
      <button class="btn-sm" onclick="renderImageGenerator()">&#8635; Refresh</button>
    </div>
    ${designs.length === 0
      ? '<div class="empty"><div class="empty-icon">&#127912;</div>No images yet &mdash; use the form above to generate your first design</div>'
      : `<div class="review-grid">${designs.map(d => _igCard(d)).join('')}</div>`
    }`;

  _bindIgForm();
}

function _igCard(d) {
  const url = imgUrl(d.image_path);
  const prompt = (d.prompt || '').slice(0, 100);
  return `<div class="image-card" id="ig-card-${d.id}">
    <img src="${thumbUrl(d.image_path)}" alt="${esc(prompt)}" data-lb="${esc(url)}" data-lb-caption="${encodeURIComponent(prompt)}" loading="lazy" decoding="async" style="cursor:zoom-in;" onerror="this.onerror=null;this.src='${url}'">
    <div class="image-card-info">
      <div class="image-card-title" title="${esc(prompt)}">${esc(prompt) || '&mdash;'}</div>
      <div class="image-card-actions">
        <button class="btn-sm primary" data-action="ig-send-review" data-id="${d.id}">&#10132; Review</button>
        <a class="btn-sm" href="${API}/api/designs/${d.id}/export?spec=etsy" download style="text-decoration:none;" title="Download 1024×1024 JPEG, ≤5MB — Etsy spec">&#127991; Etsy</a>
        <a class="btn-sm" href="${API}/api/designs/${d.id}/export?spec=web" download style="text-decoration:none;" title="Download 1600px JPEG — web/store">&#127760; Web</a>
        <a class="btn-sm" href="${esc(url)}" download style="text-decoration:none;" title="Download full-res master (print quality)">&#11015; Full</a>
        <button class="btn-sm" data-action="ig-discard" data-id="${d.id}" style="color:var(--red);border-color:var(--red);">&#128465; Discard</button>
      </div>
    </div>
  </div>`;
}

function _bindIgForm() {
  // Enhance \u2014 runs server-side; persists across tab switches / reload (see enhanceStart).
  const _igBusy = (on) => { const b = document.getElementById('ig-enhance'); if (b) { b.disabled = on; b.innerHTML = on ? '\u23F3 Enhancing\u2026' : '\u2728 Enhance'; } };
  document.getElementById('ig-enhance')?.addEventListener('click', () => {
    const prompt = document.getElementById('ig-prompt')?.value.trim();
    if (!prompt) { toast('Enter a prompt first', 'error'); return; }
    enhanceStart('ig-prompt',
      async () => (await api('/api/enhance-prompt', { method: 'POST', body: JSON.stringify({ prompt }) })).task_id,
      _igBusy);
  });
  // If an enhance was started here and we wandered off, re-attach on return.
  enhanceResume('ig-prompt', _igBusy);

  // Research
  document.getElementById('ig-research')?.addEventListener('click', async () => {
    const prompt = document.getElementById('ig-prompt')?.value.trim();
    if (!prompt) { toast('Enter a prompt first', 'error'); return; }
    const btn = document.getElementById('ig-research');
    btn.disabled = true; btn.textContent = '\u23F3';
    try {
      const r = await api('/api/research-prompt', { method: 'POST', body: JSON.stringify({ prompt }) });
      const result = await pollTask(r.task_id);
      const el = document.getElementById('ig-prompt');
      if (el) el.value = (typeof result === 'string') ? result : (result.result?.enhanced_prompt || result.enhanced || result.prompt || prompt);
      toast('\u{1F52C} Research done!');
    } catch(e) { toast('Research failed: ' + e.message, 'error'); }
    finally { btn.disabled = false; btn.textContent = '\u{1F52C} Research'; }
  });

  // Image file ref
  document.getElementById('ig-image')?.addEventListener('change', e => {
    const f = e.target.files[0];
    const nameEl = document.getElementById('ig-image-name');
    if (nameEl) nameEl.textContent = f ? f.name : '';
  });

  // Generate
  document.getElementById('ig-generate')?.addEventListener('click', async () => {
    const prompt = document.getElementById('ig-prompt')?.value.trim();
    if (!prompt) { toast('Enter a prompt to generate', 'error'); return; }
    const model = document.getElementById('ig-model')?.value;
    const variations = parseInt(document.getElementById('ig-variations')?.value) || 3;
    const btn = document.getElementById('ig-generate');
    btn.disabled = true; btn.textContent = '\u23F3 Generating\u2026';
    try {
      // Optional image research
      let finalPrompt = prompt;
      const imgFile = document.getElementById('ig-image')?.files[0];
      if (imgFile) {
        try {
          const b64 = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = ev => resolve(ev.target.result);
            reader.onerror = reject;
            reader.readAsDataURL(imgFile);
          });
          const imgRes = await api('/api/research-image', { method:'POST', body: JSON.stringify({ image: b64, prompt }) });
          const taskResult = await pollTask(imgRes.task_id, 45);
          const rr = (taskResult && taskResult.result) ? taskResult.result : (taskResult || {});
          const ep = rr.enhanced_prompt || rr.description || '';
          if (ep) finalPrompt = ep;
        } catch {}
      }
      await api('/api/generate', { method:'POST', body: JSON.stringify({ prompt: finalPrompt, model, variations, source: 'generator' }) });
      toast(`\u{1F3A8} Generating ${variations} variation${variations>1?'s':''}!`);
      const pEl = document.getElementById('ig-prompt'); if (pEl) pEl.value = '';
      const nEl = document.getElementById('ig-image-name'); if (nEl) nEl.textContent = '';
      const iEl = document.getElementById('ig-image'); if (iEl) iEl.value = '';
      loadStats(); loadGpuStrip();
      // Auto-refresh after delay to show new images
      setTimeout(() => { if (_currentView === 'image-gen') renderImageGenerator(); }, 8000);
    } catch(e) { toast('Generation failed: ' + e.message, 'error'); }
    finally { btn.disabled = false; btn.textContent = '\u{1F3A8} Generate'; }
  });
}

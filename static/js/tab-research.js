/* ── RESEARCH LAB tab ─────────────────────────────────────────────────────────
   Research Geniuses (resident researcher agents, also citizens of The Company)
   take user-proposed projects through a background pipeline:
   plan → web search → read pages → images → illustrated report → Library.
   Backend: app/routers/research.py + app/research_lab.py. */

let _rlOverview = null;
let _rlProjects = [];
let _rlOpenId = null;
let _rlTimer = null;
let _rlIdeas = [];
let _rlFilter = '';
let _rlQuery = '';

const _RL_STATUS = {
  proposed:  ['#8899bb', '📋 proposed'],
  running:   ['#f0b45a', '⏳ researching'],
  done:      ['#4ade80', '✅ done'],
  failed:    ['#f87171', '❌ failed'],
  cancelled: ['#8899bb', '🚫 cancelled'],
};

async function renderResearch(mount = 'main-content') {
  clearTimeout(_rlTimer);
  const main = document.getElementById(mount);
  main.innerHTML = `
    <div class="view-header">
      <div>
        <div class="view-title">🔬 Research Lab</div>
        <div class="view-sub">Propose any project — build it, start it, design it — and a Research
        Genius produces the full illustrated how-to: plan, steps, materials &amp; costs, stats,
        images, sources and safety notes. Coding projects get specs, never code.</div>
      </div>
    </div>
    <div id="rl-geniuses" class="stats-row" style="margin-bottom:14px;"></div>
    <div class="card" style="margin-bottom:14px;">
      <div style="display:flex;gap:18px;flex-wrap:wrap;align-items:center;font-size:.85rem;" id="rl-toggles"></div>
    </div>
    <div class="card" style="margin-bottom:14px;">
      <div style="font-weight:600;margin-bottom:8px;">Propose a research project
        ${hlp('Describe what you want to build or do. A Genius researches the web and compiles an illustrated report with steps, materials, costs and safety notes.')}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <input id="rl-title" placeholder="e.g. Build a chicken coop for 6 hens" style="flex:2;min-width:220px;">
        <select id="rl-kind" style="min-width:120px;">
          <option value="">auto-detect kind</option>
          <option value="build">build / DIY</option>
          <option value="business">business</option>
          <option value="design">design</option>
          <option value="coding">coding (specs only)</option>
          <option value="other">other</option>
        </select>
      </div>
      <textarea id="rl-desc" rows="2" placeholder="Details: goals, constraints, budget, materials on hand…" style="width:100%;margin-top:8px;"></textarea>
      <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">
        <button class="btn-sm primary" id="rl-propose-btn" onclick="rlPropose()">🔬 Propose project</button>
        <button class="btn-sm" id="rl-ideas-btn" onclick="rlSuggest()">💡 Suggest ideas</button>
        ${hlp('The Geniuses pitch 4-6 fresh project ideas (they never repeat a past project). Type a theme in the title box first to steer them, or leave it blank.')}
      </div>
      <div id="rl-ideas"></div>
    </div>
    <div class="card" style="margin-bottom:14px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
      <input id="rl-search" placeholder="🔎 filter projects…" style="flex:1;min-width:160px;"
             oninput="_rlQuery=this.value;_rlRenderProjects()">
      <div id="rl-chips" style="display:flex;gap:6px;flex-wrap:wrap;"></div>
    </div>
    <div id="rl-detail"></div>
    <div id="rl-projects"></div>`;
  await rlRefresh();
  if (_rlOpenId) rlOpen(_rlOpenId, true);
}
window.renderResearch = renderResearch;

async function rlRefresh() {
  try {
    const [ov, pj] = await Promise.all([api('/api/research/overview'), api('/api/research/projects')]);
    _rlOverview = ov;
    _rlProjects = pj.projects || [];
  } catch (e) {
    const el = document.getElementById('rl-projects');
    if (el) el.innerHTML = `<div class="empty"><div class="empty-icon">❌</div>${esc(e.message)}</div>`;
    return;
  }
  _rlRenderGeniuses();
  _rlRenderToggles();
  _rlRenderChips();
  _rlRenderProjects();
  // live progress: keep refreshing while anything is running and we're on this tab
  if (_rlProjects.some(p => p.status === 'running') && _currentView === 'research') {
    clearTimeout(_rlTimer);
    _rlTimer = setTimeout(() => { if (_currentView === 'research') rlRefresh(); }, 5000);
  }
}

function _rlRenderGeniuses() {
  const el = document.getElementById('rl-geniuses');
  if (!el || !_rlOverview) return;
  el.innerHTML = (_rlOverview.geniuses || []).map(g => `
    <div class="stat-card" style="text-align:left;">
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="font-size:1.4rem;">🧑‍🔬</span>
        <div>
          <div style="font-weight:700;">${esc(g.name)} <span style="color:var(--muted);font-weight:400;">Lv ${g.level || 1}</span></div>
          <div style="font-size:.75rem;color:var(--muted);">${esc(g.specialty || '')}</div>
        </div>
      </div>
      <div style="font-size:.75rem;color:var(--muted);margin-top:6px;">
        ${g.projects_done || 0} report${(g.projects_done || 0) === 1 ? '' : 's'} ·
        ${g.projects_active ? `<span style="color:var(--warn);">⏳ ${g.projects_active} active</span>` : 'free'}
        · ${esc(g.state || 'idle')} @ ${esc(g.location || 'town')}
      </div>
    </div>`).join('');
}

const _RL_TOGGLES = [
  ['research_autostart',    'Auto-start on propose', 'Start researching the moment you propose a project. Off = projects wait for you to press Start.'],
  ['research_images',       'Web images',            'Fetch illustrative images for the report via searxng image search.'],
  ['research_gen_images',   'Generate hero image',   'Also GENERATE one concept illustration on the GPU via the Studio image pipeline (slower, uses the GPU).'],
  ['research_auto_library', 'Auto-file to Library',  'File every finished report into the Library (research section) automatically.'],
  ['research_peer_review',  'Peer review',           'A second Genius peer-reviews every draft report and the author revises before it is filed (a couple of extra LLM calls per report).'],
  ['research_shop_push',    'Materials → Money tab', 'When a report finishes, the Genius files its materials list into the Money tab as shop-search demand signals (deduped; the normal review→missions→approval flow stays in charge — nothing is bought).'],
  ['research_recur_enabled', 'Recurring rechecks',   'Master switch for scheduled price rechecks. Give a project a cadence (⏰ in its detail view) and its Genius re-verifies material prices on schedule, building the 📈 price-watch graph.'],
  ['research_price_alerts', 'Price-drop alerts',     'When a recheck finds a material below its report baseline by the alert threshold, the Genius posts a 💸 buy-window to the God Console community board and files a demand signal for the money review. Advisory only — nothing is bought.'],
];

function _rlRenderToggles() {
  const el = document.getElementById('rl-toggles');
  if (!el || !_rlOverview) return;
  const cfg = _rlOverview.config || {};
  el.innerHTML = _RL_TOGGLES.map(([key, label, help]) => {
    const on = (cfg[key] || 'on') !== 'off';
    return `<label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
      <span class="toggle ${on ? 'on' : ''}" data-rlkey="${key}"></span>
      ${esc(label)} ${hlp(help)}</label>`;
  }).join('') + `
    <label style="display:flex;align-items:center;gap:5px;">💸 alert at −
      <input id="rl-alert-pct" type="number" min="1" max="90" style="width:56px;"
             value="${esc(cfg.research_price_alert_pct || '10')}">%
      ${hlp('Price-drop alert threshold: how far below the report baseline a rechecked price must fall before the Genius calls it a buy window.')}</label>`;
  const pctInput = el.querySelector('#rl-alert-pct');
  if (pctInput) pctInput.addEventListener('change', async () => {
    const v = Math.max(1, Math.min(90, parseInt(pctInput.value, 10) || 10));
    pctInput.value = v;
    try {
      await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ research_price_alert_pct: String(v) }) });
      toast(`💸 Alerting at −${v}%`);
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  });
  el.querySelectorAll('.toggle[data-rlkey]').forEach(t => {
    t.addEventListener('click', async () => {
      t.classList.toggle('on');
      const on = t.classList.contains('on');
      const patch = {}; patch[t.dataset.rlkey] = on ? 'on' : 'off';
      try {
        await api('/api/settings', { method: 'PATCH', body: JSON.stringify(patch) });
        if (_rlOverview) _rlOverview.config[t.dataset.rlkey] = on ? 'on' : 'off';
        toast(`${on ? 'Enabled' : 'Disabled'}`);
      } catch (e) { toast('Error: ' + e.message, 'error'); t.classList.toggle('on'); }
    });
  });
}

function _rlRenderChips() {
  const el = document.getElementById('rl-chips');
  if (!el || !_rlOverview) return;
  const c = _rlOverview.counts || {};
  const chips = [['', `All ${_rlProjects.length}`], ['running', `⏳ ${c.running || 0}`],
                 ['done', `✅ ${c.done || 0}`], ['proposed', `📋 ${c.proposed || 0}`],
                 ['failed', `❌ ${c.failed || 0}`]];
  el.innerHTML = chips.map(([key, label]) => `
    <button class="btn-sm ${_rlFilter === key ? 'primary' : ''}"
            onclick="_rlFilter='${key}';_rlRenderChips();_rlRenderProjects()">${label}</button>`).join('');
}
window._rlRenderChips = _rlRenderChips;

function _rlRenderProjects() {
  const el = document.getElementById('rl-projects');
  if (!el) return;
  let list = _rlProjects;
  if (_rlFilter) list = list.filter(p => p.status === _rlFilter);
  const q = _rlQuery.trim().toLowerCase();
  if (q) list = list.filter(p =>
    `${p.title} ${p.genius_name || ''} ${p.kind || ''}`.toLowerCase().includes(q));
  if (!list.length) {
    el.innerHTML = `<div class="empty"><div class="empty-icon">🔬</div>${
      _rlProjects.length ? 'No projects match the filter.' : 'No research projects yet — propose one above.'}</div>`;
    return;
  }
  el.innerHTML = list.map(p => {
    const [color, chip] = _RL_STATUS[p.status] || ['#8899bb', p.status];
    const startable = ['proposed', 'failed', 'cancelled'].includes(p.status);
    const review = (p.review && p.review.reviewer)
      ? ` · ${p.review.verdict === 'revise' ? '✍️ peer-revised' : '🧑‍⚖️ peer-approved'}` : '';
    return `
    <div class="card" style="margin-bottom:10px;">
      <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center;">
        <div style="min-width:220px;flex:1;">
          <div style="font-weight:700;">${esc(p.title)}
            ${(p.version || 1) > 1 ? `<span style="color:var(--accent);font-size:.75rem;">v${p.version}</span>` : ''}</div>
          <div style="font-size:.75rem;color:var(--muted);">🧑‍🔬 ${esc(p.genius_name)} ·
            <span style="color:${color};">${chip}</span>
            ${p.kind ? ' · ' + esc(p.kind) : ''}${review}
            ${p.phase_note ? ' · ' + esc(p.phase_note) : ''}</div>
          ${p.status === 'running' ? `
            <div style="background:var(--surface2);border-radius:6px;height:8px;margin-top:6px;overflow:hidden;">
              <div style="height:100%;width:${p.progress || 0}%;background:var(--accent);transition:width .5s;"></div>
            </div>` : ''}
          ${p.status === 'failed' && p.error ? `<div style="font-size:.75rem;color:var(--warn);margin-top:4px;">${esc(p.error)}</div>` : ''}
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <button class="btn-sm" onclick="rlOpen(${p.id})">📖 Open</button>
          ${p.status === 'done' ? `<button class="btn-sm" onclick="rlDeeper(${p.id})" title="Follow-up research pass — rewrites the report as the next version">🔍 Deeper</button>` : ''}
          ${startable ? `<button class="btn-sm success" onclick="rlStart(${p.id})">▶ ${p.status === 'proposed' ? 'Start' : 'Restart'}</button>` : ''}
          ${p.status === 'running' ? `<button class="btn-sm" onclick="rlCancel(${p.id})">🚫 Cancel</button>` : ''}
          ${p.status !== 'running' ? `<button class="btn-sm danger" onclick="rlDelete(${p.id})">🗑</button>` : ''}
        </div>
      </div>
    </div>`;
  }).join('');
}

async function rlPropose() {
  const title = document.getElementById('rl-title').value.trim();
  const desc = document.getElementById('rl-desc').value.trim();
  const kind = document.getElementById('rl-kind').value;
  if (!title) { toast('Give the project a title first', 'warn'); return; }
  const btn = document.getElementById('rl-propose-btn');
  btn.disabled = true;
  try {
    const r = await api('/api/research/projects', {
      method: 'POST', body: JSON.stringify({ title, description: desc, kind })
    });
    toast(`🧑‍🔬 ${r.genius} took the project${r.started ? ' and started researching' : ''}`);
    document.getElementById('rl-title').value = '';
    document.getElementById('rl-desc').value = '';
    await rlRefresh();
    if (r.started) rlOpen(r.id);
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { btn.disabled = false; }
}
window.rlPropose = rlPropose;

async function rlStart(pid) {
  try { await api(`/api/research/projects/${pid}/start`, { method: 'POST', body: '{}' }); toast('Research started'); }
  catch (e) { toast('Error: ' + e.message, 'error'); }
  rlRefresh();
  rlOpen(pid);
}
window.rlStart = rlStart;

async function rlCancel(pid) {
  try { await api(`/api/research/projects/${pid}/cancel`, { method: 'POST', body: '{}' }); toast('Cancelled'); }
  catch (e) { toast('Error: ' + e.message, 'error'); }
  rlRefresh();
}
window.rlCancel = rlCancel;

async function rlDelete(pid) {
  if (!confirm('Delete this research project (and its report/media)?')) return;
  try { await api(`/api/research/projects/${pid}`, { method: 'DELETE' }); toast('Deleted'); }
  catch (e) { toast('Error: ' + e.message, 'error'); }
  if (_rlOpenId === pid) { _rlOpenId = null; const d = document.getElementById('rl-detail'); if (d) d.innerHTML = ''; }
  rlRefresh();
}
window.rlDelete = rlDelete;

async function rlOpen(pid, keep) {
  _rlOpenId = pid;
  const el = document.getElementById('rl-detail');
  if (!el) return;
  let p;
  try { p = await api(`/api/research/projects/${pid}`); }
  catch (e) { el.innerHTML = `<div class="card" style="margin-bottom:14px;">❌ ${esc(e.message)}</div>`; return; }
  const prevQa = document.getElementById('rl-qa-input');
  const qaDraft = prevQa ? prevQa.value : '';
  const qaHadFocus = prevQa && document.activeElement === prevQa;
  const [color, chip] = _RL_STATUS[p.status] || ['#8899bb', p.status];
  let reportHtml = '';
  let marketHtml = '';
  let mkChecking = false;
  if (p.status === 'done') {
    try {
      const mk = await api(`/api/research/projects/${pid}/market`);
      mkChecking = !!mk.checking;
      marketHtml = _rlMarket(pid, mk);
    } catch (e) { /* market layer is optional — never block the report */ }
    try {
      const rep = await api(`/api/research/projects/${pid}/report`);
      reportHtml = `<div style="border-top:1px solid var(--border);margin-top:10px;padding-top:6px;">${rep.html}</div>
        ${rep.library_path ? `<div style="font-size:.75rem;color:var(--muted);margin-top:8px;">📚 Filed in the Library: ${esc(rep.library_path)}</div>` : ''}`;
    } catch (e) { reportHtml = `<div style="color:var(--warn);">Report error: ${esc(e.message)}</div>`; }
  }
  const events = (p.events || []).map(ev =>
    `<div style="font-size:.72rem;color:var(--muted);">
       <span style="color:var(--accent);">${esc(ev.phase)}</span> ${esc(ev.message)}
       <span style="opacity:.6;">· ${esc(ev.created_at || '')}</span></div>`).join('');
  const rv = (p.review && p.review.reviewer) ? p.review : null;
  const reviewHtml = rv ? `
    <div style="border:1px solid var(--border);border-radius:8px;padding:8px 10px;margin-top:10px;font-size:.8rem;">
      <b>🧑‍⚖️ Peer review</b> — ${esc(rv.reviewer)}
      ${rv.verdict === 'revise' ? 'requested changes (the report was revised)' : 'approved the report'}
      ${rv.summary ? `<div style="color:var(--muted);margin-top:4px;">${esc(rv.summary)}</div>` : ''}
      ${(rv.issues || []).length ? `<div style="margin-top:4px;">${rv.issues.map(i => `<div>• ${esc(i)}</div>`).join('')}</div>` : ''}
    </div>` : '';
  const srcHtml = (p.sources || []).length ? `
    <details style="margin-top:8px;">
      <summary style="cursor:pointer;font-size:.8rem;color:var(--muted);">🔗 ${p.sources.length} sources · 🖼 ${(p.images || []).length} images</summary>
      <div style="max-height:180px;overflow:auto;margin-top:6px;">
        ${p.sources.map(s => `<div style="font-size:.75rem;"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a></div>`).join('')}
      </div>
    </details>` : '';
  let qaHtml = '';
  if (p.status === 'done') {
    const items = (p.qa || []).map(q => `
      <div style="margin-top:8px;">
        <div style="font-weight:600;font-size:.82rem;">❓ ${esc(q.question)}</div>
        ${q.status === 'pending'
          ? `<div style="font-size:.78rem;color:var(--warn);">⏳ ${esc(q.genius_name || 'the Genius')} is thinking…</div>`
          : q.status === 'failed'
            ? `<div style="font-size:.78rem;color:var(--warn);">${esc(q.answer)}</div>`
            : `<div style="font-size:.82rem;margin-top:2px;">${q.answer_html || esc(q.answer)}</div>`}
      </div>`).join('');
    qaHtml = `
      <details style="margin-top:8px;" ${(p.qa || []).length ? 'open' : ''}>
        <summary style="cursor:pointer;font-size:.8rem;color:var(--muted);">💬 Ask ${esc(p.genius_name)} (${(p.qa || []).length})</summary>
        ${items || '<div style="font-size:.75rem;color:var(--muted);margin-top:6px;">No questions yet.</div>'}
        <div style="display:flex;gap:6px;margin-top:8px;">
          <input id="rl-qa-input" placeholder="Follow-up question about this project…" style="flex:1;"
                 onkeydown="if(event.key==='Enter')rlAsk(${p.id})">
          <button class="btn-sm" onclick="rlAsk(${p.id})">💬 Ask</button>
        </div>
      </details>`;
  }
  el.innerHTML = `
    <div class="card" style="margin-bottom:14px;">
      <div style="display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap;">
        <div>
          <div style="font-weight:700;font-size:1.05rem;">${esc(p.title)}
            ${(p.version || 1) > 1 ? `<span style="color:var(--accent);font-size:.78rem;">v${p.version}</span>` : ''}</div>
          <div style="font-size:.78rem;color:var(--muted);">🧑‍🔬 ${esc(p.genius_name)} ·
            <span style="color:${color};">${chip}</span> · ${p.progress || 0}%
            ${p.phase_note ? ' · ' + esc(p.phase_note) : ''}</div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          ${p.status === 'done' ? `
            <button class="btn-sm" onclick="rlDeeper(${p.id})" title="Follow-up research pass — rewrites the report as the next version">🔍 Dig deeper</button>
            <button class="btn-sm" onclick="rlDownload(${p.id})" title="Download the report as a markdown file">📥 .md</button>` : ''}
          <button class="btn-sm" onclick="_rlCloseDetail()">✕ Close</button>
        </div>
      </div>
      ${p.status === 'running' ? `
        <div style="background:var(--surface2);border-radius:6px;height:10px;margin-top:8px;overflow:hidden;">
          <div style="height:100%;width:${p.progress || 0}%;background:var(--accent);transition:width .5s;"></div>
        </div>` : ''}
      ${reviewHtml}
      ${srcHtml}
      <details style="margin-top:8px;" ${p.status === 'running' ? 'open' : ''}>
        <summary style="cursor:pointer;font-size:.8rem;color:var(--muted);">Research log</summary>
        <div style="max-height:220px;overflow:auto;margin-top:6px;">${events || '<div style="font-size:.75rem;color:var(--muted);">no activity yet</div>'}</div>
      </details>
      ${marketHtml}
      ${qaHtml}
      ${reportHtml}
    </div>`;
  const qaInput = document.getElementById('rl-qa-input');
  if (qaInput && qaDraft) { qaInput.value = qaDraft; if (qaHadFocus) qaInput.focus(); }
  if (!keep) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  const qaPending = (p.qa || []).some(q => q.status === 'pending');
  if ((p.status === 'running' || qaPending || mkChecking) && _currentView === 'research') {
    setTimeout(() => { if (_rlOpenId === pid && _currentView === 'research') rlOpen(pid, true); }, 4000);
  }
}
window.rlOpen = rlOpen;

function _rlCloseDetail() {
  _rlOpenId = null;
  const el = document.getElementById('rl-detail');
  if (el) el.innerHTML = '';
}
window._rlCloseDetail = _rlCloseDetail;

/* ── Ask the Genius ─────────────────────────────────────────────────────────*/
async function rlAsk(pid) {
  const input = document.getElementById('rl-qa-input');
  const q = (input ? input.value : '').trim();
  if (!q) { toast('Type a question first', 'warn'); return; }
  try {
    await api(`/api/research/projects/${pid}/ask`, { method: 'POST', body: JSON.stringify({ question: q }) });
    if (input) input.value = '';
    toast('💬 Question sent to the Genius');
    rlOpen(pid, true);
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.rlAsk = rlAsk;

/* ── Dig deeper (versioned follow-up pass) ──────────────────────────────────*/
async function rlDeeper(pid) {
  const focus = prompt('Focus for the deeper pass (leave blank to just fill the gaps):');
  if (focus === null) return;
  try {
    await api(`/api/research/projects/${pid}/deeper`, { method: 'POST', body: JSON.stringify({ focus: focus.trim() }) });
    toast('🔍 Deeper pass started');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  rlRefresh();
  rlOpen(pid, true);
}
window.rlDeeper = rlDeeper;

/* ── download the report as .md ─────────────────────────────────────────────*/
async function rlDownload(pid) {
  try {
    const rep = await api(`/api/research/projects/${pid}/report`);
    const blob = new Blob([rep.md], { type: 'text/markdown' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (rep.title || 'research-report').replace(/[^\w\- ]+/g, '').trim().replace(/\s+/g, '-').toLowerCase() + '.md';
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.rlDownload = rlDownload;

/* ── 🧾 Materials & price watch (research_lab_market) ───────────────────────*/
function _rlPriceChart(runs) {
  const W = 320, H = 110, PL = 44, PR = 8, PT = 8, PB = 16;
  const pts = runs.filter(r => isFinite(r.total));
  if (pts.length < 2) return '';
  let lo = Math.min(...pts.map(r => r.total)), hi = Math.max(...pts.map(r => r.total));
  if (hi === lo) hi = lo + 1;
  const X = i => PL + (i / (pts.length - 1)) * (W - PL - PR);
  const Y = v => PT + (1 - (v - lo) / (hi - lo)) * (H - PT - PB);
  const path = pts.map((r, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(r.total).toFixed(1)}`).join('');
  const grid = [lo, (lo + hi) / 2, hi].map(v =>
    `<line x1="${PL}" y1="${Y(v)}" x2="${W - PR}" y2="${Y(v)}" stroke="var(--border)" stroke-width="1"/>
     <text x="${PL - 5}" y="${Y(v) + 3}" text-anchor="end" font-size="8.5" fill="var(--muted)">$${Math.round(v)}</text>`).join('');
  const dots = pts.map((r, i) =>
    `<circle cx="${X(i).toFixed(1)}" cy="${Y(r.total).toFixed(1)}" r="3" fill="var(--accent)">
       <title>${esc(r.ts)} · $${r.total.toFixed(2)} est. total · ${r.kind === 'report' ? 'from the report' : 'price check'} (${r.n} items)</title>
     </circle>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:460px;height:auto;display:block;margin-top:6px;">
    ${grid}
    <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>
    ${dots}
    <text x="${PL}" y="${H - 3}" font-size="8.5" fill="var(--muted)">${esc(pts[0].ts.slice(0, 10))}</text>
    <text x="${W - PR}" y="${H - 3}" text-anchor="end" font-size="8.5" fill="var(--muted)">${esc(pts[pts.length - 1].ts.slice(0, 10))}</text>
  </svg>`;
}

function _rlSpark(points) {
  const ys = (points || []).map(s => s.price).filter(v => v != null && isFinite(v));
  if (ys.length < 2) return '';
  const W = 70, H = 18;
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi === lo) hi = lo + 1;
  const pts = ys.map((v, i) =>
    `${((i / (ys.length - 1)) * W).toFixed(1)},${(H - 2 - ((v - lo) / (hi - lo)) * (H - 4)).toFixed(1)}`).join(' ');
  return `<svg viewBox="0 0 ${W} ${H}" style="width:70px;height:18px;vertical-align:middle;">
    <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.5"/></svg>`;
}

function _rlMarket(pid, mk) {
  if (!(mk.materials || []).length && !(mk.runs || []).length) return '';
  const recurOpts = [[0, 'no recheck'], [3, 'every 3 days'], [7, 'weekly'], [14, 'every 2 weeks'], [30, 'monthly']];
  const rows = (mk.materials || []).map(m => {
    const ser = (mk.series || {})[m.item] || [];
    const first = ser.length ? ser[0].price : m.cost;
    const last = ser.length ? ser[ser.length - 1].price : m.cost;
    let delta = '';
    if (first != null && last != null && first > 0 && ser.length > 1) {
      const pct = (last - first) / first * 100;
      const col = pct > 1 ? 'var(--warn)' : pct < -1 ? '#4ade80' : 'var(--muted)';
      delta = `<span style="color:${col};">${pct > 0 ? '+' : ''}${pct.toFixed(0)}%</span>`;
    }
    return `<tr>
      <td>${esc(m.item)}</td><td style="color:var(--muted);">${esc(m.qty || '')}</td>
      <td>${first != null ? '$' + first.toFixed(2) : '—'}</td>
      <td>${last != null ? '$' + last.toFixed(2) : '—'} ${delta}</td>
      <td>${_rlSpark(ser)}</td></tr>`;
  }).join('');
  return `
    <details style="margin-top:8px;" open>
      <summary style="cursor:pointer;font-size:.8rem;color:var(--muted);">🧾 Materials & price watch
        (${(mk.materials || []).length} items${mk.filed ? ` · 🛒 ${mk.filed} in Money tab` : ''}${mk.checking ? ' · ⏳ checking prices…' : ''})</summary>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px;font-size:.8rem;">
        <button class="btn-sm" onclick="rlShop(${pid})" title="File the materials into the Money tab as shop searches (deduped)">🛒 Send to Money</button>
        <button class="btn-sm" onclick="rlPriceCheck(${pid})" ${mk.checking ? 'disabled' : ''}
                title="The Genius re-searches every material's current price and records a snapshot">💲 Recheck prices now</button>
        <label style="display:flex;align-items:center;gap:5px;">⏰
          <select onchange="rlRecur(${pid}, this.value)" style="font-size:.78rem;">
            ${recurOpts.map(([d, l]) => `<option value="${d}" ${d === (mk.recur_days || 0) ? 'selected' : ''}>${l}</option>`).join('')}
          </select>
          ${hlp('Scheduled recurring research: the Genius re-checks material prices on this cadence and the graph below builds up over time. Master switch: the "Recurring rechecks" toggle above.')}</label>
        ${mk.next_run_at ? `<span style="color:var(--muted);font-size:.72rem;">next: ${esc(mk.next_run_at)}</span>` : ''}
      </div>
      ${(mk.alerts || []).length ? `
        <div style="border:1px solid var(--border);border-left:3px solid #4ade80;border-radius:8px;padding:6px 10px;margin-top:8px;font-size:.78rem;">
          ${mk.alerts.slice(0, 5).map(a => `<div>💸 <b>${esc(a.item)}</b> at $${(a.price || 0).toFixed(2)}
            <span style="color:#4ade80;">−${(a.pct || 0).toFixed(0)}%</span>
            <span style="color:var(--muted);">vs $${(a.baseline || 0).toFixed(2)} baseline · ${esc((a.created_at || '').slice(0, 16))}</span></div>`).join('')}
        </div>` : ''}
      ${_rlPriceChart(mk.runs || []) ||
        `<div style="font-size:.74rem;color:var(--muted);margin-top:6px;">One price snapshot so far — rechecks (manual or scheduled) build the estimated-total graph here.</div>`}
      ${rows ? `
        <div style="overflow-x:auto;margin-top:8px;">
          <table style="font-size:.76rem;border-collapse:collapse;min-width:420px;">
            <tr style="color:var(--muted);text-align:left;"><th>Item</th><th>Qty</th><th>Report est.</th><th>Latest</th><th></th></tr>
            ${rows}
          </table>
        </div>` : ''}
    </details>`;
}

async function rlShop(pid) {
  try {
    const r = await api(`/api/research/projects/${pid}/shop`, { method: 'POST', body: '{}' });
    toast(r.filed ? `🛒 Filed ${r.filed} materials into the Money tab` : 'Everything is already filed');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  rlOpen(pid, true);
}
window.rlShop = rlShop;

async function rlPriceCheck(pid) {
  try {
    await api(`/api/research/projects/${pid}/pricecheck`, { method: 'POST', body: '{}' });
    toast('💲 Price recheck started');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  rlOpen(pid, true);
}
window.rlPriceCheck = rlPriceCheck;

async function rlRecur(pid, days) {
  try {
    await api(`/api/research/projects/${pid}/recur`, { method: 'POST', body: JSON.stringify({ days: parseInt(days, 10) || 0 }) });
    toast(parseInt(days, 10) ? '⏰ Recurring recheck scheduled' : 'Recurring recheck turned off');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  rlOpen(pid, true);
}
window.rlRecur = rlRecur;

/* ── the 💡 ideas board ─────────────────────────────────────────────────────*/
async function rlSuggest() {
  const btn = document.getElementById('rl-ideas-btn');
  btn.disabled = true; btn.textContent = '💡 Thinking…';
  try {
    const theme = document.getElementById('rl-title').value.trim();
    const r = await api('/api/research/suggest', { method: 'POST', body: JSON.stringify({ theme }) });
    _rlIdeas = r.ideas || [];
    _rlRenderIdeas();
    if (!_rlIdeas.length) toast('No ideas came back — try again', 'warn');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { btn.disabled = false; btn.textContent = '💡 Suggest ideas'; }
}
window.rlSuggest = rlSuggest;

function _rlRenderIdeas() {
  const el = document.getElementById('rl-ideas');
  if (!el) return;
  if (!_rlIdeas.length) { el.innerHTML = ''; return; }
  el.innerHTML = `<div style="margin-top:10px;display:grid;gap:8px;">` + _rlIdeas.map((idea, i) => `
    <div style="border:1px solid var(--border);border-radius:8px;padding:8px 10px;display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap;">
      <div style="flex:1;min-width:200px;">
        <div style="font-weight:600;font-size:.85rem;">${esc(idea.title)}
          ${idea.kind ? `<span style="color:var(--muted);font-weight:400;font-size:.72rem;"> · ${esc(idea.kind)}</span>` : ''}</div>
        <div style="font-size:.75rem;color:var(--muted);">${esc(idea.description || '')}</div>
      </div>
      <button class="btn-sm success" onclick="rlProposeIdea(${i})">🔬 Propose</button>
    </div>`).join('') + `</div>`;
}

async function rlProposeIdea(i) {
  const idea = _rlIdeas[i];
  if (!idea) return;
  try {
    const r = await api('/api/research/projects', {
      method: 'POST',
      body: JSON.stringify({ title: idea.title, description: idea.description || '', kind: idea.kind || '' })
    });
    toast(`🧑‍🔬 ${r.genius} took “${idea.title}”${r.started ? ' and started researching' : ''}`);
    _rlIdeas.splice(i, 1);
    _rlRenderIdeas();
    await rlRefresh();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.rlProposeIdea = rlProposeIdea;

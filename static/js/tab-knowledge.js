'use strict';

/* Knowledge — the consolidated hub. Federated search is pinned at the top; the sub-tabs
   host the FULL Library / Research / Knowledge-Graph / Live-Docs experiences by calling
   their real renderers into #k-content (each takes a mount arg). renderKnowledge is a
   core view dispatched from app-nav.js renderView(); it also backs the old library/
   research/graph view names (they deep-link to the matching sub-tab). */

const _K_SUBTABS = [
  ['library',  '📖 Library'],
  ['research', '🔬 Research'],
  ['graph',    '🕸️ Graph'],
  ['livedocs', '📚 Live Docs'],
  ['memory',   '🧠 Memory'],
];

function _kSrcBadge(s) {
  const c = { library: '#22a06b', research: '#a855f7', graph: '#3b82f6', livedocs: '#f59e0b', memory: '#e11d48' }[s] || '#888';
  return `<span style="background:${c};color:#fff;border-radius:4px;padding:1px 7px;font-size:.7rem;">${esc(s)}</span>`;
}

function _kSetActive(name) {
  document.querySelectorAll('[data-ksub]').forEach(b => {
    const on = b.dataset.ksub === name;
    b.style.background = on ? 'var(--accent, #3b82f6)' : '';
    b.style.color = on ? '#fff' : '';
  });
}

function _kHideHeader(host) {                 // the source renderers draw their own view-header;
  const vh = host.querySelector('.view-header'); // hide it inside the hub so we don't double up
  if (vh) vh.style.display = 'none';
}

async function _kShow(name) {
  const host = document.getElementById('k-content');
  if (!host) return;
  _kSetActive(name);
  try {
    if (name === 'library')  { await renderLibrary('k-content');  _kHideHeader(host); return; }
    if (name === 'research') { await renderResearch('k-content'); _kHideHeader(host); return; }
    if (name === 'graph')    { await renderGraph('k-content');    _kHideHeader(host); return; }
    if (name === 'livedocs') { _kLivedocs(host); return; }
    if (name === 'memory')   { await _kMemory(host); return; }
  } catch (e) {
    host.innerHTML = `<div class="card" style="padding:16px;color:var(--danger,#e05555);">${esc(e.message)}</div>`;
  }
}

async function _kSearch() {
  const q = (document.getElementById('k-q').value || '').trim();
  const scope = document.getElementById('k-scope').value;
  if (!q) return;
  _kSetActive(null);                        // searching isn't one of the source sub-tabs
  const host = document.getElementById('k-content');
  host.innerHTML = `<div class="card" style="padding:16px;color:var(--muted);">Searching across everything…</div>`;
  try {
    const d = await api(`/api/knowledge/search?q=${encodeURIComponent(q)}&scope=${scope}`);
    if (!d.ok || !d.results.length) {
      host.innerHTML = `<div class="card" style="padding:16px;color:var(--muted);">No results for “${esc(q)}”.</div>`;
      return;
    }
    host.innerHTML = `<div class="card" style="padding:16px;">
      <div style="color:var(--muted);font-size:.8rem;margin-bottom:10px;">${d.count} results · ${esc(d.mode)} ranking</div>
      ${d.results.map(r => `
        <div style="border-left:3px solid var(--border,#333);padding:8px 12px;margin-bottom:10px;">
          <div style="display:flex;gap:8px;align-items:center;margin-bottom:4px;flex-wrap:wrap;">
            ${_kSrcBadge(r.source)} <b>${esc(r.title || '')}</b>
            ${r.ref ? `<span style="color:var(--muted);font-size:.72rem;">${esc(r.ref)}</span>` : ''}
          </div>
          <div style="white-space:pre-wrap;font-size:.82rem;color:var(--muted);">${esc(r.snippet || '')}</div>
        </div>`).join('')}
    </div>`;
  } catch (e) {
    host.innerHTML = `<div class="card" style="padding:16px;color:var(--danger,#e05555);">${esc(e.message)}</div>`;
  }
}

function _kLivedocs(host) {
  host.innerHTML = `<div class="card" style="padding:16px;">
    <div style="color:var(--muted);font-size:.83rem;margin-bottom:10px;">Current docs for a named library (context7) — try <code>fastapi</code>, <code>httpx</code>, <code>react</code>.</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <input id="k-ld" placeholder="library, e.g. fastapi" style="flex:1;min-width:150px;padding:9px;border-radius:6px;border:1px solid var(--border,#333);background:var(--surface,#111);color:inherit;">
      <input id="k-ld-topic" placeholder="topic (optional)" style="flex:1;min-width:150px;padding:9px;border-radius:6px;border:1px solid var(--border,#333);background:var(--surface,#111);color:inherit;">
      <button class="btn" id="k-ld-go">Fetch docs</button>
    </div>
    <div id="k-ld-out" style="margin-top:14px;"></div></div>`;
  const out = document.getElementById('k-ld-out');
  async function go() {
    const lib = document.getElementById('k-ld').value.trim();
    const topic = document.getElementById('k-ld-topic').value.trim();
    if (!lib) return;
    out.innerHTML = `<p style="color:var(--muted);">Fetching current docs…</p>`;
    try {
      const d = await api(`/api/livedocs/lookup?library=${encodeURIComponent(lib)}&topic=${encodeURIComponent(topic)}`);
      out.innerHTML = d.ok
        ? `<div style="color:var(--muted);font-size:.75rem;margin-bottom:6px;">${esc(d.resolved_title || d.resolved_id || lib)}</div>
           <pre style="white-space:pre-wrap;font-size:.8rem;max-height:60vh;overflow:auto;">${esc(d.text || '')}</pre>`
        : `<p style="color:var(--danger,#e05555);">${esc(d.error || 'not found')}</p>`;
    } catch (e) {
      out.innerHTML = `<p style="color:var(--danger,#e05555);">${esc(e.message)}</p>`;
    }
  }
  document.getElementById('k-ld-go').addEventListener('click', go);
  for (const id of ['k-ld', 'k-ld-topic'])
    document.getElementById(id).addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
}

async function _kMemory(host) {
  host.innerHTML = `<div class="card" style="padding:16px;">
    <div style="color:var(--muted);font-size:.83rem;margin-bottom:10px;">
      Shared memory — facts your agents recorded. New ones are <b>staged</b> until you promote them;
      trusted notes join search results. (Agents write via the <code>knowledge_remember</code> tool.)</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;">
      <input id="k-mem-add" placeholder="Add a trusted fact to shared memory…"
             style="flex:1;min-width:240px;padding:9px;border-radius:6px;border:1px solid var(--border,#333);background:var(--surface,#111);color:inherit;">
      <button class="btn" id="k-mem-addgo">+ Add (trusted)</button>
    </div>
    <div id="k-mem-list">Loading…</div></div>`;

  async function load() {
    const box = document.getElementById('k-mem-list');
    try {
      const d = await api('/api/knowledge/notes?status=all');
      const notes = d.notes || [];
      const staged = notes.filter(n => n.status === 'staged');
      const trusted = notes.filter(n => n.status === 'trusted');
      const row = (n, actions) => `<div style="border-left:3px solid ${n.status === 'trusted' ? '#22a06b' : '#f59e0b'};padding:6px 12px;margin-bottom:8px;">
        <div style="font-size:.85rem;white-space:pre-wrap;">${esc(n.text || '')}</div>
        <div style="color:var(--muted);font-size:.72rem;margin-top:3px;">${esc(n.agent || '—')}${n.tags ? ' · ' + esc(n.tags) : ''} · ${esc(n.created_at || '')} ${actions}</div></div>`;
      box.innerHTML =
        `<div style="font-weight:600;margin:4px 0 8px;">⏳ Pending review (${staged.length})</div>` +
        (staged.length ? staged.map(n => row(n,
          `· <a href="#" data-promote="${n.id}" style="color:#22a06b;">promote</a> · <a href="#" data-reject="${n.id}" style="color:var(--danger,#e05555);">reject</a>`)).join('')
          : `<div style="color:var(--muted);font-size:.8rem;margin-bottom:10px;">Nothing pending.</div>`) +
        `<div style="font-weight:600;margin:14px 0 8px;">✅ Trusted (${trusted.length})</div>` +
        (trusted.length ? trusted.map(n => row(n,
          `· <a href="#" data-reject="${n.id}" style="color:var(--danger,#e05555);">remove</a>`)).join('')
          : `<div style="color:var(--muted);font-size:.8rem;">No trusted notes yet.</div>`);
      box.querySelectorAll('[data-promote]').forEach(a => a.addEventListener('click', async e => {
        e.preventDefault(); await api(`/api/knowledge/notes/${a.dataset.promote}/promote`, { method: 'POST' }); load();
      }));
      box.querySelectorAll('[data-reject]').forEach(a => a.addEventListener('click', async e => {
        e.preventDefault(); await api(`/api/knowledge/notes/${a.dataset.reject}/reject`, { method: 'POST' }); load();
      }));
    } catch (e) {
      box.innerHTML = `<div style="color:var(--danger,#e05555);">${esc(e.message)}</div>`;
    }
  }
  async function add() {
    const t = document.getElementById('k-mem-add').value.trim();
    if (!t) return;
    const r = await api('/api/knowledge/remember', { method: 'POST', body: JSON.stringify({ text: t, agent: 'owner', tags: 'manual' }) });
    if (r.ok && r.id) await api(`/api/knowledge/notes/${r.id}/promote`, { method: 'POST' });  // owner adds go straight to trusted
    document.getElementById('k-mem-add').value = '';
    load();
  }
  document.getElementById('k-mem-addgo').addEventListener('click', add);
  document.getElementById('k-mem-add').addEventListener('keydown', e => { if (e.key === 'Enter') add(); });
  load();
}

async function renderKnowledge(subtab) {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="view-header">
      <div class="view-title">🧠 Knowledge</div>
      <div class="view-sub">Search across everything, or dive into a source — Library, Research, Graph, Live Docs.</div>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 12px;">
      <input id="k-q" placeholder="Ask across all your knowledge…  e.g. how does the LLM proxy route to lmstudio?"
             style="flex:1;min-width:240px;padding:10px;border-radius:8px;border:1px solid var(--border,#333);background:var(--surface,#111);color:inherit;">
      <select id="k-scope" title="which sources to search"
              style="background:var(--surface,#111);color:inherit;border:1px solid var(--border,#333);border-radius:8px;padding:0 8px;">
        <option value="all">all</option><option value="code">code</option><option value="research">research</option>
      </select>
      <button class="btn" id="k-go" style="padding:10px 18px;">🔎 Search</button>
    </div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px;border-bottom:1px solid var(--border,#222);padding-bottom:8px;">
      ${_K_SUBTABS.map(([id, label]) => `<button class="btn" data-ksub="${id}" style="padding:6px 13px;">${label}</button>`).join('')}
    </div>
    <div id="k-content"></div>`;

  document.getElementById('k-go').addEventListener('click', _kSearch);
  document.getElementById('k-q').addEventListener('keydown', e => { if (e.key === 'Enter') _kSearch(); });
  main.querySelectorAll('[data-ksub]').forEach(b => b.addEventListener('click', () => _kShow(b.dataset.ksub)));

  await _kShow(_K_SUBTABS.some(([id]) => id === subtab) ? subtab : 'library');
}
// Core view: dispatched from app-nav.js renderView() switch (case 'knowledge').

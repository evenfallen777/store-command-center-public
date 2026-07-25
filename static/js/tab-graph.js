/* ══ Knowledge Graph tab (Graphify) ══
   A queryable knowledge graph of the whole repo (code + docs). Four query modes
   (Ask / Explain / Path / Impact), a "god nodes" + communities panel (the map's
   landmarks), the collapsible-tree viz, and rebuild. The query endpoints are MCP
   tools too, so OpenClaw can ask the graph the same way. */
let _graphMode = 'ask';
const _GMODES = {
  ask:     { icon: '🔎', label: 'Ask',     ep: '/api/graph/query',    field: 'q',    ph: 'Ask the graph… e.g. how does the raid combat work?' },
  explain: { icon: '💡', label: 'Explain', ep: '/api/graph/explain',  field: 'node', ph: 'A node/symbol to explain… e.g. choose_work()' },
  impact:  { icon: '⚡', label: 'Impact',  ep: '/api/graph/affected', field: 'node', ph: "What depends on…?  e.g. world_defs.py" },
  path:    { icon: '🔗', label: 'Path',    ep: '/api/graph/path',     field: null,   ph: '' },
};

async function renderGraph(mount = 'main-content') {
  document.getElementById(mount).innerHTML = `
    <div class="view-header">
      <h1>🕸️ Knowledge Graph</h1>
      <div class="view-sub">A queryable map of the whole codebase + docs (Graphify) — ask it, trace connections, see impact. OpenClaw can query it too.</div>
    </div>
    <div id="graph-stats" style="color:var(--muted);font-size:.8rem;margin:6px 0 8px"></div>
    <div style="display:flex;gap:6px;margin-bottom:6px;flex-wrap:wrap">
      ${Object.entries(_GMODES).map(([k, m]) => `<button class="btn" id="gmode-${k}" onclick="graphSetMode('${k}')" style="padding:5px 11px">${m.icon} ${m.label}</button>`).join('')}
      <span style="flex:1"></span>
      <button class="btn" onclick="graphRebuild()">↻ Rebuild</button>
      <a class="btn" href="${API}/api/graph/report" target="_blank" rel="noopener" style="text-decoration:none">📄 Report</a>
    </div>
    <div id="graph-controls"></div>
    <div id="graph-answer" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px;margin:10px 0;font-size:.78rem;white-space:pre-wrap;max-height:300px;overflow:auto;font-family:ui-monospace,monospace"></div>
    <div id="graph-history" style="margin:0 0 8px"></div>
    <div id="graph-highlights" style="margin:10px 0"></div>
    <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px;flex-wrap:wrap">
      <span style="font-size:.72rem;color:var(--muted)">View:</span>
      <button class="btn" id="gviz-explore" onclick="graphSetViz('explore')" style="padding:4px 10px;font-size:.72rem">🧭 Explore</button>
      <button class="btn" id="gviz-graph" onclick="graphSetViz('graph')" style="padding:4px 10px;font-size:.72rem">🕸️ Force graph</button>
      <button class="btn" id="gviz-tree" onclick="graphSetViz('tree')" style="padding:4px 10px;font-size:.72rem">🌳 Tree</button>
      <span id="gviz-note" style="font-size:.66rem;color:var(--muted)"></span>
    </div>
    <div id="graph-explore" style="display:none">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px;flex-wrap:wrap">
        <span style="font-size:.72rem;color:var(--muted)">Slice:</span>
        <select id="graph-scope" onchange="graphExploreScope()" style="background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:7px;padding:5px 8px;font-size:.78rem;max-width:320px"></select>
        <span id="graph-scope-count" style="font-size:.66rem;color:var(--muted)"></span>
        <span style="flex:1"></span>
        <span style="font-size:.62rem;color:var(--muted)">— extracted · <span style="color:#c8aa6e">- - inferred</span> · <span style="color:#e16e6e">- - ambiguous</span></span>
        <button class="btn" style="padding:3px 8px;font-size:.66rem" onclick="graphExportPNG()">📷 PNG</button>
        <button class="btn" style="padding:3px 8px;font-size:.66rem" onclick="graphExportGraphML()">⬇ GraphML</button>
      </div>
      <div id="graph-comm-filter" style="margin-bottom:6px;line-height:1.7"></div>
      <div style="display:flex;gap:8px;height:62vh">
        <canvas id="graph-canvas" style="flex:1;border:1px solid var(--border);border-radius:10px;background:#0b0f16;cursor:grab"></canvas>
        <div id="graph-inspector" style="width:250px;flex-shrink:0;border:1px solid var(--border);border-radius:10px;background:var(--card);padding:12px;font-size:.76rem;overflow:auto">
          <div style="color:var(--muted)">Click a node to inspect it.</div>
        </div>
      </div>
    </div>
    <iframe id="graph-viz" src="about:blank" title="knowledge graph"
      style="display:none;width:100%;height:62vh;border:1px solid var(--border);border-radius:10px;background:#0b0f16"></iframe>`;
  graphSetMode('ask');
  graphSetViz('explore');
  graphLoadStats();
  graphLoadHighlights();
  graphRenderHistory();
}

function graphSetMode(m) {
  _graphMode = m;
  Object.keys(_GMODES).forEach(k => { const b = document.getElementById('gmode-' + k); if (b) b.style.background = k === m ? 'var(--accent,#6c63ff)' : ''; });
  const c = document.getElementById('graph-controls'); if (!c) return;
  if (m === 'path') {
    c.innerHTML = `<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <input id="graph-a" placeholder="from node… e.g. world_sim.py" style="flex:1;min-width:180px;padding:9px 12px;background:var(--card);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:.85rem" onkeydown="if(event.key==='Enter')graphRun()">
      <span style="color:var(--muted)">→</span>
      <input id="graph-b" placeholder="to node… e.g. api/mcp" style="flex:1;min-width:180px;padding:9px 12px;background:var(--card);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:.85rem" onkeydown="if(event.key==='Enter')graphRun()">
      <button class="btn" onclick="graphRun()">🔗 Trace</button></div>`;
  } else {
    const spec = _GMODES[m];
    c.innerHTML = `<div style="display:flex;gap:8px;flex-wrap:wrap">
      <input id="graph-q" placeholder="${spec.ph}" style="flex:1;min-width:260px;padding:9px 12px;background:var(--card);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:.85rem" onkeydown="if(event.key==='Enter')graphRun()">
      <button class="btn" onclick="graphRun()">${spec.icon} ${spec.label}</button></div>`;
  }
}

async function graphRun() {
  const spec = _GMODES[_graphMode];
  const out = document.getElementById('graph-answer');
  let body;
  if (_graphMode === 'path') {
    const a = (document.getElementById('graph-a').value || '').trim(), b = (document.getElementById('graph-b').value || '').trim();
    if (!a || !b) return; body = { a, b };
  } else {
    const v = (document.getElementById('graph-q').value || '').trim(); if (!v) return;
    body = { [spec.field]: v };
  }
  const label = _graphMode === 'path' ? `${body.a} → ${body.b}` : body[spec.field];
  out.style.display = 'block'; out.textContent = '🕸️ traversing the graph…';
  try {
    const r = await api(spec.ep, { method: 'POST', body: JSON.stringify(body) });
    out.textContent = r.answer || r.error || '(no result)';
    _qhistPush(_graphMode, label);
  } catch (e) { out.textContent = e.message; }
}
function _qhist() { try { return JSON.parse(localStorage.getItem('graph_qhist') || '[]'); } catch (e) { return []; } }
function _qhistPush(mode, label) {
  if (!label) return;
  let h = _qhist().filter(x => !(x.mode === mode && x.label === label));
  h.unshift({ mode, label }); h = h.slice(0, 12);
  try { localStorage.setItem('graph_qhist', JSON.stringify(h)); } catch (e) {}
  graphRenderHistory();
}
function graphRenderHistory() {
  const el = document.getElementById('graph-history'); if (!el) return;
  const h = _qhist();
  el.innerHTML = h.length ? `<span style="font-size:.64rem;color:var(--muted);margin-right:3px">📜 recent:</span>` +
    h.map(x => `<span onclick="graphRerun('${x.mode}',this.dataset.q)" data-q="${esc(x.label).replace(/"/g, '&quot;')}" title="${_GMODES[x.mode] ? _GMODES[x.mode].label : x.mode}" style="cursor:pointer;display:inline-block;font-size:.64rem;padding:1px 8px;margin:1px;border-radius:10px;background:var(--card);border:1px solid var(--border);color:var(--muted)">${_GMODES[x.mode] ? _GMODES[x.mode].icon : ''} ${esc((x.label || '').slice(0, 40))}</span>`).join('') : '';
}
function graphRerun(mode, label) {
  if (mode === 'path') {
    const parts = (label || '').split(' → '); graphSetMode('path');
    setTimeout(() => { const a = document.getElementById('graph-a'), b = document.getElementById('graph-b'); if (a) a.value = parts[0] || ''; if (b) b.value = parts[1] || ''; graphRun(); }, 30);
  } else {
    graphSetMode(mode);
    setTimeout(() => { const inp = document.getElementById('graph-q'); if (inp) inp.value = label; graphRun(); }, 30);
  }
}

async function graphLoadStats() {
  const el = document.getElementById('graph-stats'); if (!el) return;
  try {
    const s = await api('/api/graph/stats');
    el.innerHTML = s.built
      ? `<b style="color:var(--text)">${(s.nodes || 0).toLocaleString()}</b> nodes · <b style="color:var(--text)">${(s.edges || 0).toLocaleString()}</b> edges · ${s.communities || 0} communities${s.updated ? ` · updated ${new Date(s.updated * 1000).toLocaleString()}` : ''}`
      : '⚠ Graph not built yet — hit <b>Rebuild</b>.';
    const note = document.getElementById('gviz-note');
    if (note && s.built) note.textContent = s.force_viz ? '' : '(force graph builds after the next full extract — showing tree for now)';
  } catch (e) { el.textContent = e.message; }
}

async function graphLoadHighlights() {
  const el = document.getElementById('graph-highlights'); if (!el) return;
  try {
    const h = await api('/api/graph/highlights');
    if (!h.built || h.error) { el.innerHTML = ''; return; }
    const chip = (label, onclick, title, col) => `<span onclick="${onclick}" title="${esc(title || '')}" style="cursor:pointer;display:inline-block;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:3px 10px;margin:2px;font-size:.72rem;color:${col || 'var(--text)'}">${esc(label)}</span>`;
    const gods = (h.gods || []).map(g => chip(`${g.label} · ${g.degree}`, `graphJumpToNode('${(g.id || '').replace(/'/g, "\\'")}','${(g.label || '').replace(/'/g, "\\'")}')`, `${g.file || ''} — click to open in the Explorer`, g.kind === 'document' ? '#8fd0a0' : 'var(--text)')).join('');
    const comms = (h.communities || []).map(c => chip(`${c.name} (${c.size})`, `graphExplainNode('${(c.name || '').replace(/'/g, "\\'")}')`, 'community — click to explore')).join('');
    el.innerHTML = `
      <div style="font-size:.72rem;color:var(--muted);margin-bottom:3px">🌟 <b>God nodes</b> — the most-connected concepts (click to explain):</div>
      <div style="margin-bottom:8px">${gods || '<span style="color:var(--muted)">—</span>'}</div>
      <div style="font-size:.72rem;color:var(--muted);margin-bottom:3px">🗂️ <b>Biggest communities</b>:</div>
      <div>${comms || '<span style="color:var(--muted)">—</span>'}</div>`;
  } catch (e) { el.innerHTML = ''; }
}

let _graphViz = 'explore', _geReady = false, _scopesLoaded = false;
function graphSetViz(kind) {
  _graphViz = kind;
  ['explore', 'graph', 'tree'].forEach(k => { const b = document.getElementById('gviz-' + k); if (b) b.style.background = k === kind ? 'var(--accent,#6c63ff)' : ''; });
  const exp = document.getElementById('graph-explore'), ifr = document.getElementById('graph-viz');
  if (kind === 'explore') {
    if (ifr) ifr.style.display = 'none';
    if (exp) exp.style.display = 'block';
    _mountExplorer();
  } else {
    if (exp) exp.style.display = 'none';
    if (ifr) { ifr.style.display = 'block'; ifr.src = `${API}/api/graph/viz?kind=${kind}&t=${Date.now()}`; }
  }
}
async function _mountExplorer() {
  const cv = document.getElementById('graph-canvas'); if (!cv) return;
  if (!window.GE) {
    // graph-explorer.js didn't load — almost always a stale browser cache after an
    // update. Make it VISIBLE (it used to fail silently → "Explore does nothing").
    const host = document.getElementById('graph-explore');
    if (host && !document.getElementById('ge-missing')) {
      const d = document.createElement('div');
      d.id = 'ge-missing';
      d.style.cssText = 'padding:16px;margin-top:8px;border:1px solid #e0a05a55;border-radius:9px;background:#2a1c05;color:#f0c070;font-size:.82rem;line-height:1.5';
      d.innerHTML = "⚠️ The Explore engine (graph-explorer.js) didn't load — this is a cached old page. " +
        "<b>Hard-refresh</b> to fix it: <b>Ctrl+Shift+R</b> (Windows/Linux) or <b>Cmd+Shift+R</b> (Mac). " +
        "🕸️ Force graph and 🌳 Tree still work in the meantime.";
      host.appendChild(d);
    }
    return;
  }
  const gone = document.getElementById('ge-missing'); if (gone) gone.remove();
  // renderView() rebuilds #main-content on every view switch, so `cv` is a NEW canvas
  // each time Explore is (re)opened. destroy() stops any prior sim + unbinds its window
  // listeners, then mount() rebinds idempotently to the current canvas.
  GE.destroy(); GE.mount(cv, { onNodeClick: graphInspect }); _geReady = true;
  // resize twice — once now, once after the pane's layout settles — so the canvas is
  // never stuck at 0×0 when Explore is opened from another view (was a blank-canvas cause)
  setTimeout(() => window.__geResize && window.__geResize(), 40);
  setTimeout(() => window.__geResize && window.__geResize(), 260);
  if (!_scopesLoaded) await graphLoadScopes();
}
async function graphLoadScopes() {
  const sel = document.getElementById('graph-scope'); if (!sel) return;
  try {
    const s = await api('/api/graph/scopes'); if (!s.built) return;
    const grp = (arr, label, by) => arr.length ? `<optgroup label="${label}">` + arr.map(x => `<option value="${esc(x.scope)}::${by}">${esc(x.scope)} (${x.count})</option>`).join('') + '</optgroup>' : '';
    // Repos first — the natural top-level slice of a merged cross-repo (homelab) graph.
    sel.innerHTML = grp(s.repos || [], '📦 Repos', 'repo') + grp(s.dirs || [], '📁 Folders', 'path') + grp(s.communities || [], '🗂️ Communities', 'community');
    _scopesLoaded = true; graphExploreScope();
  } catch (e) { /* ignore */ }
}
let _hiddenComms = new Set(), _curSlice = { scope: '', by: 'path' };
async function graphExploreScope() {
  const sel = document.getElementById('graph-scope'); if (!sel || !sel.value) return;
  const [scope, by] = sel.value.split('::');
  _curSlice = { scope, by };
  const cnt = document.getElementById('graph-scope-count'); if (cnt) cnt.textContent = 'loading…';
  try {
    const d = await api(`/api/graph/subgraph?by=${by}&scope=${encodeURIComponent(scope)}&limit=180`);
    GE.setData(d.nodes || [], d.edges || []); _hiddenComms = new Set(); GE.setHidden(_hiddenComms); graphRenderCommFilter();
    if (cnt) cnt.textContent = `${(d.nodes || []).length} nodes · ${(d.edges || []).length} links${d.capped ? ' (capped)' : ''}`;
  } catch (e) { if (cnt) cnt.textContent = e.message; }
}
function graphRenderCommFilter() {
  const el = document.getElementById('graph-comm-filter'); if (!el || !window.GE) return;
  const comms = GE.communities().slice(0, 22);
  if (!comms.length) { el.innerHTML = ''; return; }
  el.innerHTML = `<span style="font-size:.66rem;color:var(--muted);margin-right:4px">Clusters (click to hide):</span>` +
    comms.map(c => {
      const off = _hiddenComms.has(c.community);
      return `<span onclick="graphToggleComm('${c.community}')" title="${esc(c.name)} · ${c.count} nodes" style="cursor:pointer;display:inline-block;font-size:.66rem;padding:1px 8px;margin:1px;border-radius:11px;border:1px solid ${c.color};background:${off ? 'transparent' : c.color};color:${off ? 'var(--muted)' : '#0b0f16'};opacity:${off ? .5 : 1}">${esc(c.name)} ${c.count}</span>`;
    }).join('');
}
function graphToggleComm(c) {
  const cn = isNaN(+c) ? c : +c;
  if (_hiddenComms.has(cn)) _hiddenComms.delete(cn); else _hiddenComms.add(cn);
  GE.setHidden(_hiddenComms); graphRenderCommFilter();
}
function graphExportPNG() {
  const url = window.GE && GE.exportPNG(); if (!url) return;
  const a = document.createElement('a'); a.href = url; a.download = `graph-${(_curSlice.scope || 'view').replace(/\W+/g, '_')}.png`; a.click();
}
function graphExportGraphML() {
  const u = `${API}/api/graph/export?by=${_curSlice.by}&scope=${encodeURIComponent(_curSlice.scope)}`;
  window.open(u, '_blank');
}
function graphJumpToNode(id, label) {
  graphSetViz('explore');
  setTimeout(() => graphExpandEgo(id), 60);
}
function graphInspect(n) {
  const el = document.getElementById('graph-inspector'); if (!el) return;
  const nbrs = GE.neighborsOf(n.id).length;
  el.innerHTML = `
    <div style="font-weight:700;color:var(--text);word-break:break-word">${esc(n.label || n.id)}</div>
    <div style="font-size:.68rem;color:var(--muted);margin:3px 0">${esc(n.kind || '')} · ${n.degree} links total · ${nbrs} in view</div>
    ${n.file ? `<div style="font-size:.66rem;color:#8fb4e0;word-break:break-all">${esc(n.file)}</div>` : ''}
    ${n.community_name ? `<div style="font-size:.66rem;color:#8fd0a0;margin-top:3px">🗂️ ${esc(n.community_name)}</div>` : ''}
    <div style="display:flex;gap:6px;margin-top:12px;flex-wrap:wrap">
      <button class="btn" style="padding:3px 9px;font-size:.68rem" onclick="graphExplainNode('${(n.label || n.id).replace(/'/g, "\\'")}')">💡 Explain</button>
      <button class="btn" style="padding:3px 9px;font-size:.68rem" onclick="graphExpandEgo('${(n.id).replace(/'/g, "\\'")}')">⚡ Focus here</button>
    </div>`;
}
async function graphExpandEgo(id) {
  const cnt = document.getElementById('graph-scope-count');
  _curSlice = { scope: id, by: 'ego' };
  try {
    const d = await api(`/api/graph/subgraph?by=ego&scope=${encodeURIComponent(id)}&depth=2&limit=160`);
    GE.setData(d.nodes || [], d.edges || []); _hiddenComms = new Set(); GE.setHidden(_hiddenComms); graphRenderCommFilter();
    if (cnt) cnt.textContent = `ego network · ${(d.nodes || []).length} nodes`;
  } catch (e) { /* ignore */ }
}
function graphExplainNode(node) {
  graphSetMode('explain');
  const inp = document.getElementById('graph-q'); if (inp) { inp.value = node; }
  graphRun();
}

async function graphRebuild() {
  try {
    const r = await api('/api/graph/rebuild', { method: 'POST', body: '{}' });
    toast?.(r.already ? 'Already rebuilding…' : 'Rebuilding the code graph… (~1 min, no LLM)');
    setTimeout(graphPollRebuild, 4000);
  } catch (e) { toast?.(e.message); }
}
async function graphPollRebuild() {
  try {
    const s = await api('/api/graph/rebuild/status');
    if (s.running) { setTimeout(graphPollRebuild, 4000); return; }
    toast?.('✅ Graph rebuilt'); graphLoadStats(); graphLoadHighlights();
    const f = document.getElementById('graph-viz'); if (f) f.src = `${API}/api/graph/viz?t=${Date.now()}`;
  } catch (e) { /* keep last */ }
}
window.renderGraph = renderGraph; window.graphRun = graphRun; window.graphSetMode = graphSetMode;
window.graphExplainNode = graphExplainNode; window.graphRebuild = graphRebuild; window.graphSetViz = graphSetViz;
window.graphExploreScope = graphExploreScope; window.graphExpandEgo = graphExpandEgo; window.graphInspect = graphInspect; window.graphLoadScopes = graphLoadScopes;
window.graphToggleComm = graphToggleComm; window.graphExportPNG = graphExportPNG; window.graphExportGraphML = graphExportGraphML;
window.graphJumpToNode = graphJumpToNode; window.graphRenderHistory = graphRenderHistory; window.graphRerun = graphRerun; window.graphRenderCommFilter = graphRenderCommFilter;
window.graphPollRebuild = graphPollRebuild; window.graphLoadStats = graphLoadStats; window.graphLoadHighlights = graphLoadHighlights;

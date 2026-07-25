/* ══ THE COMPANY — 🕸️ Loops (the agent-orchestration graph, LIVE) ══
   God Console tab over /api/world/loops/graph (app/world_loops.py): a visual
   node view of who's in charge, what each agent/department can do (its
   capabilities), who prays to whom for approval, who endorses/reviews whom,
   who votes where — plus a card per autonomous LOOP with its live cadence /
   schedule / on-off state AND when it last fired.

   Operator tier: You (God) sit in the CENTER with the two toggle-gated
   lieutenants mirrored on either side — ✝️ Jesus (the light/constructive/
   best-case side) above and 😈 Satan (the dark/adversarial/worst-case side)
   below — same tier, identical edges, opposite polarities; their only other
   differences are the toggles and the human-only hard floor.

   LIVE FLOW: every node shows recency (last action + how long ago, count in
   the last hour, active ● vs idle ○), and a time-anchored "Live flow" stream
   shows approvals/prayers/invocations/votes actually MOVING — who did what,
   when. Auto-refreshes every 30s while the tab is on screen. Read-only —
   the map, not the territory. Click any node for the drill-down.

   Uses _worldModal (world-economy.js) + api()/esc()/toast() (app-core.js).
   Global-scope classic script. */

let _loopsData = null;
let _loopsKinds = null;    // Set of visible edge kinds
let _loopsSel = null;      // selected node id (survives refreshes)
let _loopsTimer = null;    // 30s live-refresh while the tab is visible

const _LOOP_EDGE_COLORS = {
  commands: '#fde047', delegates: '#fb923c', prays: '#a78bfa', endorses: '#38bdf8',
  approves: '#6ee7a8', reviews: '#f87171', votes: '#e879f9', produces: '#94a3b8',
  drives: '#22d3ee',
};

function _loopsAgo(sec) {
  if (sec == null) return '—';
  if (sec < 60) return `${Math.max(1, Math.round(sec))}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

async function worldLoops() {
  let d;
  try { d = await api('/api/world/loops/graph'); }
  catch (e) { toast?.('Loops graph failed to load'); return; }
  _loopsData = d;
  if (!_loopsKinds) {
    _loopsKinds = new Set(Object.keys(_LOOP_EDGE_COLORS));
    _loopsKinds.delete('votes');            // the densest kind starts hidden
  }
  _renderLoops();
  _loopsLive();
}

/* live refresh: silently re-fetch + re-render while the graph is on screen */
function _loopsLive() {
  if (_loopsTimer) return;
  _loopsTimer = setInterval(async () => {
    if (!document.getElementById('loops-svg')) {        // tab closed → stop
      clearInterval(_loopsTimer); _loopsTimer = null; return;
    }
    try { _loopsData = await api('/api/world/loops/graph'); _renderLoops(); }
    catch (e) { /* transient — keep the last picture */ }
  }, 30000);
}

function _loopCard(l) {
  const on = !!(l.cadence && l.cadence.enabled);
  const last = l.ago_sec != null
    ? `<span style="color:${l.ago_sec < 3600 ? '#6ee7a8' : '#7a86a0'}">⏱ last ran ${_loopsAgo(l.ago_sec)}</span>` : '';
  return `<div style="background:${on ? '#12251b' : '#0e1626'};border:1px solid ${on ? '#2a5a3a' : '#26324a'};border-radius:10px;padding:9px 11px">
    <div style="display:flex;justify-content:space-between;gap:6px;align-items:baseline">
      <div style="font-weight:700;color:#e8eefc;font-size:.8rem">${l.icon} ${esc(l.label)}</div>
      <span style="font-size:.62rem;color:${on ? '#6ee7a8' : '#7a86a0'}">${on ? '● ON' : '○ off'}</span>
    </div>
    <div style="font-size:.66rem;color:#8ecaff;margin-top:2px">🕐 ${esc(l.cadence?.label || '')} ${last}</div>
    <div style="font-size:.62rem;color:#7a86a0;margin-top:2px">${esc(l.cadence?.detail || '')}</div>
    <div style="font-size:.62rem;color:#c7d2e5;margin-top:4px">👑 ${esc(l.in_charge || '')}</div>
  </div>`;
}

function _loopsSvg(d) {
  const kinds = _loopsKinds;
  const layers = {};
  for (const n of d.nodes) (layers[n.layer] = layers[n.layer] || []).push(n);
  const NL = Object.keys(layers).map(Number).sort((a, b) => a - b);
  const COLW = 236, BOXW = 190, BOXH = 40, GAPY = 14, PAD = 16;
  const maxRows = Math.max(...NL.map(l => layers[l].length));
  const H = PAD * 2 + maxRows * (BOXH + GAPY);
  const W = PAD * 2 + NL.length * COLW;
  const pos = {};
  for (const l of NL) {
    const col = layers[l];
    const total = col.length * (BOXH + GAPY) - GAPY;
    let y = Math.max(PAD, (H - total) / 2);
    for (const n of col) {
      pos[n.id] = { x: PAD + l * COLW, y, n };
      y += BOXH + GAPY;
    }
  }
  const edges = d.edges.filter(e => kinds.has(e.kind) && pos[e.from] && pos[e.to]);
  const paths = edges.map(e => {
    const a = pos[e.from], b = pos[e.to];
    const col = _LOOP_EDGE_COLORS[e.kind] || '#54607a';
    let x1, y1, x2, y2;
    if (a.x === b.x) {                       // same layer → arc out the right side
      x1 = a.x + BOXW; y1 = a.y + BOXH / 2; x2 = b.x + BOXW; y2 = b.y + BOXH / 2;
      const bump = 46 + Math.abs(y2 - y1) / 8;
      return `<path d="M${x1},${y1} C${x1 + bump},${y1} ${x2 + bump},${y2} ${x2},${y2}" fill="none" stroke="${col}" stroke-width="1.3" opacity=".55"><title>${esc(e.kind)}${e.label ? ': ' + esc(e.label) : ''}</title></path>`;
    }
    if (a.x < b.x) { x1 = a.x + BOXW; y1 = a.y + BOXH / 2; x2 = b.x; y2 = b.y + BOXH / 2; }
    else { x1 = a.x; y1 = a.y + BOXH / 2; x2 = b.x + BOXW; y2 = b.y + BOXH / 2; }
    const mx = (x1 + x2) / 2;
    return `<path d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" fill="none" stroke="${col}" stroke-width="1.3" opacity=".62" marker-end="url(#loop-arr)"><title>${esc(e.kind)}${e.label ? ': ' + esc(e.label) : ''}</title></path>`;
  }).join('');

  const typeFill = { human: '#2a1f4a', leader: '#243447', system: '#14222e', dept: '#0e1f18', role: '#0f1a2e', product: '#241a10' };
  const typeEdge = { human: '#6d5aff', leader: '#3a6a9a', system: '#2a5a6a', dept: '#2a5a3a', role: '#3a4a8a', product: '#6a4a2a' };
  // the mirrored lieutenants read at a glance: ✝️ light vs 😈 dark/red
  const idFill = { jesus: '#3b3560', satan: '#2a1214' };
  const idEdge = { jesus: '#a78bfa', satan: '#7f1d1d' };
  const boxes = d.nodes.map(n => {
    const p = pos[n.id];
    const act = n.activity || {};
    let sub = n.type === 'dept'
      ? `${(n.agents || []).length} agent${(n.agents || []).length === 1 ? '' : 's'} · ${(n.caps || []).filter(c => c.gate_on).length}/${(n.caps || []).length || 0}⚡`
      : (n.stats ? Object.entries(n.stats).slice(0, 2).map(([k, v]) => `${k} ${v}`).join(' · ') : '');
    // live recency on the box itself: count in the last hour + how long ago
    const rec = act.ago_sec != null ? `${act.count_1h || 0}/h · ${_loopsAgo(act.ago_sec)}` : 'no activity yet';
    const dotCol = (n.id === 'jesus' || n.id === 'satan') && n.enabled === false ? '#54607a'
      : act.active ? '#6ee7a8' : (act.ago_sec != null && act.ago_sec < 86400 ? '#fbbf24' : '#54607a');
    const off = n.enabled === false ? ' (off)' : '';
    return `<g style="cursor:pointer" onclick="loopsSelect('${n.id}')">
      <rect x="${p.x}" y="${p.y}" width="${BOXW}" height="${BOXH}" rx="9"
        fill="${idFill[n.id] || typeFill[n.type] || '#0e1626'}" stroke="${_loopsSel === n.id ? '#c4b5fd' : (idEdge[n.id] || typeEdge[n.type] || '#26324a')}" stroke-width="${_loopsSel === n.id ? 2 : 1.2}"${n.enabled === false ? ' stroke-dasharray="4,3" opacity=".75"' : ''}/>
      <circle cx="${p.x + BOXW - 10}" cy="${p.y + 10}" r="3.5" fill="${dotCol}">
        <title>${act.active ? 'active' : 'idle'} — ${esc(rec)}${act.last ? ' · last: ' + esc(act.last) : ''}</title></circle>
      <text x="${p.x + 9}" y="${p.y + 17}" font-size="11.5" fill="#e8eefc" font-weight="600">${n.icon || ''} ${esc(n.label).slice(0, 24)}${off}</text>
      <text x="${p.x + 9}" y="${p.y + 31}" font-size="9" fill="#7a86a0">${esc(sub).slice(0, 26)} <tspan fill="${act.active ? '#6ee7a8' : '#54607a'}">· ${esc(rec)}</tspan></text>
    </g>`;
  }).join('');

  // display labels only (world-theme.js): themed default = today's exact string
  const _opTier = window.WTheme
    ? WTheme.pick('✝️ Jesus · 🌟 God · 😈 Satan', '🔺 Constructive · 🌟 Owner · 🔻 Adversarial')
    : '✝️ Jesus · 🌟 God · 😈 Satan';
  const colTitles = [_opTier, '⚖️ Reviewers & gates', '🏢 Departments', '🤖 Swarm roles', '📦 Products'];
  const titles = NL.map(l => `<text x="${PAD + l * COLW}" y="12" font-size="10" fill="#54607a" font-weight="700">${esc(colTitles[l] || '')}</text>`).join('');

  return `<div style="overflow:auto;background:#0b1120;border:1px solid #1b2740;border-radius:10px">
    <svg width="${W}" height="${H + 16}" style="display:block">
      <defs><marker id="loop-arr" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto">
        <path d="M0,0 L6,3 L0,6" fill="none" stroke="#8a97ad" stroke-width="1"/></marker></defs>
      ${titles}<g transform="translate(0,16)">${paths}${boxes}</g>
    </svg></div>`;
}

function _loopsLegend(d) {
  return `<div style="display:flex;gap:5px;flex-wrap:wrap;margin:8px 0">${Object.entries(_LOOP_EDGE_COLORS).map(([k, col]) => `
    <span onclick="loopsToggleKind('${k}')" title="${esc((d.legend || {})[k] || k)}"
      style="cursor:pointer;font-size:.62rem;padding:2px 8px;border-radius:10px;border:1px solid ${col}55;
             color:${_loopsKinds.has(k) ? col : '#54607a'};background:${_loopsKinds.has(k) ? col + '22' : 'transparent'}">
      ─ ${k}</span>`).join('')}</div>`;
}

/* the moving stream: who approved/prayed/invoked/voted what, time-anchored */
function _loopsFlowHtml(d) {
  const rows = (d.flow || []).slice(0, 16).map(f => {
    const vcol = { approved: '#6ee7a8', rejected: '#f87171', prayed: '#a78bfa', invoked: '#22d3ee', voted: '#e879f9' }[f.verb] || '#c7d2e5';
    const scol = { done: '#6ee7a8', failed: '#f87171', rejected: '#f87171', pending: '#fbbf24', running: '#8ecaff' }[f.status] || '#54607a';
    return `<div style="display:flex;gap:6px;align-items:baseline;font-size:.68rem;padding:2px 0;border-top:1px solid #131c30"
                 ${f.node ? `onclick="loopsSelect('${f.node}')" role="button"` : ''} ${f.node ? 'class="loops-flow-row"' : ''}>
      <span style="color:#54607a;white-space:nowrap;width:56px">${_loopsAgo(f.ago_sec)}</span>
      <span style="color:#e8eefc;white-space:nowrap">${esc(f.who)}</span>
      <span style="color:${vcol}">${esc(f.verb)}</span>
      <span style="color:#8a97ad;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">${esc(f.what)}</span>
      ${f.status ? `<span style="color:${scol};margin-left:auto;white-space:nowrap">[${esc(f.status)}]</span>` : ''}
    </div>`;
  }).join('');
  return `<div style="background:#0b1120;border:1px solid #1b2740;border-radius:10px;padding:8px 10px;margin-bottom:10px">
    <div style="font-size:.7rem;font-weight:700;color:#e8eefc">⚡ Live flow <span style="font-size:.62rem;color:#54607a;font-weight:400">— approvals, prayers, invocations & votes moving through the org (auto-refreshes)</span></div>
    ${rows || '<div style="font-size:.68rem;color:#54607a;padding:4px 0">Nothing has moved yet.</div>'}
  </div>`;
}

function _loopsDetailHtml(n) {
  if (!n) return '<div style="font-size:.7rem;color:#54607a">Click a node for the drill-down.</div>';
  const act = n.activity || {};
  const stats = n.stats ? `<div style="font-size:.68rem;color:#8ecaff;margin:3px 0">${Object.entries(n.stats).map(([k, v]) => `${esc(k)}: <b>${v}</b>`).join(' · ')}</div>` : '';
  const live = act.ago_sec != null
    ? `<div style="font-size:.68rem;margin:3px 0;color:${act.active ? '#6ee7a8' : '#7a86a0'}">${act.active ? '● active' : '○ idle'} · ${act.count_1h || 0} action${act.count_1h === 1 ? '' : 's'} in the last hour · last ${_loopsAgo(act.ago_sec)}${act.last ? `<div style="color:#8a97ad">↳ ${esc(act.last)}</div>` : ''}</div>`
    : '<div style="font-size:.68rem;color:#54607a;margin:3px 0">○ no recorded activity yet</div>';
  const toggle = n.enabled === false ? '<div style="font-size:.66rem;color:#fbbf24;margin:3px 0">⏻ toggle is OFF — inert until the owner enables it</div>'
    : (n.enabled === true ? '<div style="font-size:.66rem;color:#6ee7a8;margin:3px 0">⏻ toggle is ON</div>' : '');
  const agents = (n.agents || []).map(a => `
    <div style="border-top:1px solid #131c30;padding:4px 0;font-size:.7rem">
      <b style="color:#e8eefc">${esc(a.name)}</b>
      <span style="color:#7a86a0"> L${a.level || 1} · ${a.jobs || 0} jobs · built ${a.built || 0} · ${esc(a.state || '')}</span>
      ${(a.recent || []).map(r => `<div style="color:#54607a;padding-left:10px">↳ ${esc(r.title).slice(0, 56)} <span style="color:${r.status === 'done' ? '#6ee7a8' : r.status === 'rejected' ? '#f87171' : '#8a97ad'}">[${esc(r.status)}]</span></div>`).join('')}
    </div>`).join('');
  const caps = (n.caps || []).map(c => `
    <span style="font-size:.64rem;padding:2px 7px;border-radius:9px;margin:2px;display:inline-block;
      border:1px solid ${c.gate_on ? '#2a5a3a' : '#26324a'};color:${c.gate_on ? '#6ee7a8' : '#54607a'}">${esc(c.label)}</span>`).join('');
  // this node's recent slice of the live flow — WHAT moved, WHEN
  const myFlow = (_loopsData.flow || []).filter(f => f.node === n.id).slice(0, 8).map(f => `
    <div style="font-size:.66rem;color:#8a97ad;padding:1px 0"><span style="color:#54607a">${_loopsAgo(f.ago_sec)}</span>
      ${esc(f.who)} <span style="color:#c4b5fd">${esc(f.verb)}</span> ${esc(f.what)}${f.status ? ` <span style="color:#54607a">[${esc(f.status)}]</span>` : ''}</div>`).join('');
  const rel = (_loopsData.edges || []).filter(e => e.from === n.id || e.to === n.id).map(e => {
    const other = _loopsData.nodes.find(x => x.id === (e.from === n.id ? e.to : e.from));
    const col = _LOOP_EDGE_COLORS[e.kind] || '#54607a';
    return `<div style="font-size:.66rem;color:#c7d2e5;padding:1px 0">
      <span style="color:${col}">${e.from === n.id ? '→' : '←'} ${esc(e.kind)}</span>
      ${esc(other?.label || '')}${e.label ? ` <span style="color:#54607a">(${esc(e.label)})</span>` : ''}</div>`;
  }).join('');
  return `
    <div style="font-weight:700;color:#e8eefc">${n.icon || ''} ${esc(n.label)}</div>
    ${toggle}${live}${stats}
    ${n.detail ? `<div style="font-size:.68rem;color:#8a97ad;margin:3px 0">${esc(n.detail)}</div>` : ''}
    ${myFlow ? `<div style="font-size:.64rem;color:#7a86a0;margin-top:6px">⚡ Recent flow through this node:</div>${myFlow}` : ''}
    ${caps ? `<div style="font-size:.64rem;color:#7a86a0;margin-top:6px">⚡ Capabilities (green = agent-enabled):</div><div>${caps}</div>` : ''}
    ${agents ? `<div style="font-size:.64rem;color:#7a86a0;margin-top:6px">🧑‍🤝‍🧑 Members — what they built/did:</div>${agents}` : ''}
    ${rel ? `<div style="font-size:.64rem;color:#7a86a0;margin-top:6px">🕸️ Relations:</div>${rel}` : ''}`;
}

function _renderLoops() {
  const d = _loopsData;
  const html = `
    <div style="font-size:.7rem;color:#8a97ad;margin-bottom:8px">${window.WTheme ? WTheme.pick(
      "Who's in charge, who can do what, who approves/reviews whom — live. You (God) sit centered on the operator tier with your two toggle-gated lieutenants mirrored either side, wired to the same systems with identical edges: ✝️ Jesus (light — best case) and 😈 Satan (dark — worst case).",
      "Who's in charge, who can do what, who approves/reviews whom — live. You (the Owner) sit centered on the operator tier with your two toggle-gated lieutenants mirrored either side, wired to the same systems with identical edges: 🔺 the Constructive Lieutenant (best case) and 🔻 the Adversarial Lieutenant (worst case).")
      : "Who's in charge, who can do what, who approves/reviews whom — live. You (God) sit centered on the operator tier with your two toggle-gated lieutenants mirrored either side, wired to the same systems with identical edges: ✝️ Jesus (light — best case) and 😈 Satan (dark — worst case)."} Green dots = active in the last hour. Click nodes to drill down; legend chips toggle edge kinds. <button class="btn" style="padding:1px 8px;font-size:.64rem" onclick="worldLoops()">↻ refresh</button></div>
    <div style="font-weight:700;color:#e8eefc;margin:2px 0 5px">🔁 The loops <span style="font-size:.66rem;color:#7a86a0;font-weight:400">— cadence + who runs each + last fired</span></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:8px;margin-bottom:12px">
      ${(d.loops || []).map(_loopCard).join('')}
    </div>
    ${_loopsFlowHtml(d)}
    <div style="font-weight:700;color:#e8eefc;margin:8px 0 2px">🕸️ The org graph</div>
    ${_loopsLegend(d)}
    <div style="display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:10px;align-items:start">
      <div id="loops-svg">${_loopsSvg(d)}</div>
      <div id="loops-detail" style="background:#0b1120;border:1px solid #1b2740;border-radius:10px;padding:10px;max-height:560px;overflow:auto">
        ${_loopsDetailHtml((d.nodes || []).find(x => x.id === _loopsSel) || null)}
      </div>
    </div>`;
  _worldModal('🕸️ Agent Loops — live orchestration map', html);
}

function loopsSelect(id) {
  _loopsSel = id;
  const n = (_loopsData?.nodes || []).find(x => x.id === id);
  const el = document.getElementById('loops-detail');
  if (el) el.innerHTML = _loopsDetailHtml(n);
  const svg = document.getElementById('loops-svg');
  if (svg && _loopsData) svg.innerHTML = _loopsSvg(_loopsData);   // repaint selection ring
}

function loopsToggleKind(k) {
  if (_loopsKinds.has(k)) _loopsKinds.delete(k); else _loopsKinds.add(k);
  _renderLoops();
}

window.worldLoops = worldLoops;
window.loopsSelect = loopsSelect;
window.loopsToggleKind = loopsToggleKind;

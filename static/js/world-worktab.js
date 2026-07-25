/* ══ THE COMPANY — Work-Priority tab (RimWorld-style) ══
   A matrix of agents × work types. Each cell is a priority 1 (highest) → 4 (lowest),
   or · (never). Agents pick the highest-priority job that's available right now.
   Reads _worldState; writes via /api/world/work/priority. Shared global script. */

const _WP_COLOR = ['#222c3e', '#3fae6a', '#4a90d9', '#e0b050', '#9a86b0'];   // 0..4
function _wpCell(agentId, wt, p) {
  return `<td onclick="worldCycleWork(${agentId},'${wt}',${p})" title="click to cycle · / 1 / 2 / 3 / 4"
    style="cursor:pointer;text-align:center;background:${_WP_COLOR[p] || _WP_COLOR[0]};color:${p ? '#0b1018' : '#4a5568'};font-weight:800;border:1px solid #0b1018;width:32px;height:24px">${p || '·'}</td>`;
}

async function worldWorkTab() {
  const st = await _ensureWorldState();
  if (!st) { toast?.('World not loaded yet'); return; }
  const wts = st.work_types || [];
  const ags = (st.agents || []).filter(a => a.kind === 'worker' || a.kind === 'openclaw')
    .sort((a, b) => (a.dept || '').localeCompare(b.dept || '') || a.id - b.id);
  const head = wts.map(w =>
    `<th onclick="worldBumpColumn('${w.key}')" title="${esc(w.label)} — click to make it top priority for everyone"
       style="cursor:pointer;font-size:.62rem;color:#9fb4d6;padding:3px 2px;line-height:1.15">${w.icon}<br>${esc(w.label)}</th>`).join('');
  const rows = ags.map(a =>
    `<tr><td style="padding:2px 8px;color:#e8eefc;white-space:nowrap;font-size:.74rem;border-bottom:1px solid #1a2436">
       ${esc(a.name)} <span style="color:#66738c;font-size:.66rem">${esc(a.dept || a.job_class || '')}</span>
       <span style="color:#5a6478;font-size:.62rem">· ${esc(a.goal || a.state || '')}</span></td>
     ${wts.map(w => _wpCell(a.id, w.key, (a.work_priority || {})[w.key] || 0)).join('')}</tr>`).join('');
  _worldModal('🗂️ Work Priorities', `
    <div style="font-size:.72rem;color:#8a97ad;margin-bottom:8px">
      <b>1</b>=highest … <b>4</b>=lowest · <b>·</b>=never. Click a cell to cycle; click a column header to make it top-priority for everyone.
      Idle agents scan work types in priority order and take the first available job (real dept work, construction, research, or a gathering skill), else they relax.</div>
    <div style="overflow-x:auto"><table style="border-collapse:collapse;font-size:.74rem;min-width:100%">
      <thead><tr><th style="text-align:left;font-size:.66rem;color:#7a86a0;padding:2px 8px">Agent</th>${head}</tr></thead>
      <tbody>${rows}</tbody></table></div>
    <div style="margin-top:10px;font-size:.66rem;color:#66738c">Legend:
      <span style="background:${_WP_COLOR[1]};color:#0b1018;padding:1px 5px;border-radius:3px">1</span>
      <span style="background:${_WP_COLOR[2]};color:#0b1018;padding:1px 5px;border-radius:3px">2</span>
      <span style="background:${_WP_COLOR[3]};color:#0b1018;padding:1px 5px;border-radius:3px">3</span>
      <span style="background:${_WP_COLOR[4]};color:#0b1018;padding:1px 5px;border-radius:3px">4</span>
      <span style="color:#4a5568">·  off</span></div>
    ${_stockManager(st)}
    ${_buildOrders(st)}
    <div id="wb-bills"></div>`);
  _loadBills();
}

/* ── Media BILLS (RimWorld "do until X") — keep N finished outputs ready ── */
async function _loadBills() {
  const box = document.getElementById('wb-bills');
  if (!box) return;
  try {
    const [r, sr] = await Promise.all([api('/api/world/bills'), api('/api/world/settings')]);
    const rows = (r.bills || []).length ? r.bills.map(b => {
      const pct = Math.min(100, b.count / b.target * 100);
      const [col, lbl] = b.suspended ? ['#7a86a0', '⏸ suspended']
        : b.active ? ['#3fae6a', 'filling'] : ['#4a90d9', '✓ met'];
      return `<div style="display:flex;align-items:center;gap:7px;font-size:.72rem;margin:3px 0">
        <span style="width:150px;color:#e8eefc">${b.icon} ${esc(b.label)}</span>
        <div style="flex:1;height:7px;background:#0b1120;border-radius:4px;overflow:hidden;min-width:60px"><div style="height:100%;width:${pct}%;background:${col}"></div></div>
        <span style="width:64px;text-align:right;color:#c7d2e5;font-size:.66rem">${b.count}/${b.target}</span>
        <span style="width:56px;color:#8fb4e0;font-size:.62rem" title="Paused at target; resumes filling when stock falls to this.">↻ at ${b.unpause_at}</span>
        <span style="width:44px;color:#9a86b0;font-size:.62rem" title="Minimum agent level to work this bill (skill routing).">lv ${b.min_level}+</span>
        <span style="width:72px;color:${col};font-size:.62rem;font-weight:700">${lbl}</span>
        <button class="btn" style="padding:1px 6px;font-size:.62rem" onclick="worldBillUpd(${b.id},{suspended:${b.suspended ? 0 : 1}})" title="Suspend or resume this bill.">${b.suspended ? '▶' : '⏸'}</button>
        <button class="btn" style="padding:1px 6px;font-size:.62rem" onclick="worldBillDel(${b.id})" title="Delete this bill.">✕</button></div>`;
    }).join('') : '<div style="font-size:.66rem;color:#66738c;margin:4px 0">No bills — add one to give the town a standing production target.</div>';
    const opts = (r.kinds || []).map(k => `<option value="${k.key}">${k.icon} ${esc(k.label)}</option>`).join('');
    const drive = String((sr.settings || {}).world_bills_drive ?? '0') === '1';
    box.innerHTML = `<div style="margin-top:14px;border-top:1px solid #26324a;padding-top:10px">
      <div style="font-size:.78rem;font-weight:700;color:#e8eefc;margin-bottom:4px">🏭 Media Bills</div>
      <div style="font-size:.66rem;color:#8a97ad;margin-bottom:6px">RimWorld-style production targets on the REAL pipelines: while finished stock is below <b>target</b>, the 🏭 Produce column offers agents the job; at target the bill pauses until stock falls to its unpause point.</div>
      ${rows}
      <div style="display:flex;align-items:center;gap:6px;margin-top:8px;font-size:.72rem;flex-wrap:wrap">
        <select id="bill-kind" style="background:#0b1120;border:1px solid #33456b;color:#e8eefc;border-radius:5px;padding:3px 4px">${opts}</select>
        <span style="color:#66738c;font-size:.66rem">keep</span>
        <input id="bill-target" type="number" value="10" min="1" title="How many finished outputs to keep ready." style="width:52px;background:#0b1120;border:1px solid #33456b;color:#e8eefc;border-radius:5px;padding:3px 4px">
        <span style="color:#66738c;font-size:.66rem">resume at</span>
        <input id="bill-unpause" type="number" placeholder="auto" min="0" title="Optional: refill starts again when stock falls to this (blank = ~75% of target)." style="width:52px;background:#0b1120;border:1px solid #33456b;color:#e8eefc;border-radius:5px;padding:3px 4px">
        <span style="color:#66738c;font-size:.66rem">min lv</span>
        <input id="bill-minlv" type="number" value="1" min="1" title="Only agents at or above this level take the bill." style="width:44px;background:#0b1120;border:1px solid #33456b;color:#e8eefc;border-radius:5px;padding:3px 4px">
        <button class="btn" style="padding:3px 10px;font-size:.68rem" onclick="worldBillAdd()">+ add bill</button>
        <label style="margin-left:auto;display:flex;align-items:center;gap:5px;font-size:.66rem;color:#c7d2e5" title="When ON, an active bill may start ONE real autopilot creation per interval — budget caps, review queue, taste and endorsements all still apply. Default off.">
          <input id="bill-drive" type="checkbox" ${drive ? 'checked' : ''} onchange="worldBillDrive(this.checked)" style="width:15px;height:15px">⚡ bills drive real production</label>
      </div></div>`;
  } catch (e) { box.innerHTML = `<div style="font-size:.66rem;color:#b06a6a;margin-top:10px">bills: ${esc(e.message)}</div>`; }
}
async function worldBillAdd() {
  const kind = document.getElementById('bill-kind').value;
  const target = document.getElementById('bill-target').value || 10;
  const unpause = document.getElementById('bill-unpause').value;
  const min_level = document.getElementById('bill-minlv').value || 1;
  try {
    await api('/api/world/bills', { method: 'POST', body: JSON.stringify(
      { kind, target, unpause_at: unpause === '' ? null : unpause, min_level }) });
    _loadBills();
  } catch (e) { toast?.(e.message); }
}
async function worldBillUpd(id, fields) {
  try { await api(`/api/world/bills/${id}`, { method: 'POST', body: JSON.stringify(fields) }); _loadBills(); }
  catch (e) { toast?.(e.message); }
}
async function worldBillDel(id) {
  try { await api(`/api/world/bills/${id}`, { method: 'DELETE' }); _loadBills(); }
  catch (e) { toast?.(e.message); }
}
async function worldBillDrive(on) {
  try {
    await api('/api/world/settings', { method: 'POST', body: JSON.stringify({ settings: { world_bills_drive: on ? 1 : 0 } }) });
    toast?.(on ? '⚡ Bills may now start real (gated) creations' : 'Bills back to display-only');
    await _pollWorld();
  } catch (e) { toast?.(e.message); }
}
window.worldBillAdd = worldBillAdd; window.worldBillUpd = worldBillUpd;
window.worldBillDel = worldBillDel; window.worldBillDrive = worldBillDrive;

/* ── Production Orders (RimWorld "bills") for the construction queue ── */
const _ORD_BADGE = {
  building: ['#3fae6a', 'building'], met: ['#4a90d9', '✓ met'],
  paused: ['#7a86a0', '⏸ paused'], locked: ['#b06a6a', '⛔ locked'],
};
function _buildOrders(st) {
  const con = (st.company || {}).construction || {};
  const ords = con.orders || [], cat = con.catalog || [];
  const rows = ords.length ? ords.map(o => {
    const [col, lbl] = _ORD_BADGE[o.status] || ['#7a86a0', o.status];
    const prog = o.mode === 'make' ? `${o.produced}/${o.target}` : `${o.built}/${o.target} standing`;
    const pct = o.mode === 'make' ? Math.min(100, o.produced / o.target * 100) : Math.min(100, o.built / o.target * 100);
    return `<div style="display:flex;align-items:center;gap:7px;font-size:.72rem;margin:3px 0">
      <span style="width:120px;color:#e8eefc">${esc(o.name)}</span>
      <span style="width:58px;color:#8fb4e0;font-size:.66rem">${o.mode === 'keep' ? 'keep' : 'make'}</span>
      <div style="flex:1;height:7px;background:#0b1120;border-radius:4px;overflow:hidden;min-width:60px"><div style="height:100%;width:${pct}%;background:${col}"></div></div>
      <span style="width:80px;text-align:right;color:#c7d2e5;font-size:.66rem">${prog}</span>
      <span style="width:64px;color:${col};font-size:.62rem;font-weight:700">${lbl}</span>
      <button class="btn" style="padding:1px 6px;font-size:.62rem" onclick="worldOrderPause(${o.id},${o.paused ? 0 : 1})" title="Pause or resume this build order. Paused orders stop consuming builder slots.">${o.paused ? '▶' : '⏸'}</button>
      <button class="btn" style="padding:1px 6px;font-size:.62rem" onclick="worldOrderDel(${o.id})" title="Delete this build order.">✕</button></div>`;
  }).join('') : '<div style="font-size:.66rem;color:#66738c;margin:4px 0">No orders — the town auto-builds up the tier ladder. Add an order to take control.</div>';
  const opts = cat.map(s => `<option value="${s.kind}"${s.available ? '' : ' disabled'}>${esc(s.name)}${s.available ? '' : ` (tier ${s.tier} 🔒)`}</option>`).join('');
  return `<div style="margin-top:14px;border-top:1px solid #26324a;padding-top:10px">
    <div style="font-size:.78rem;font-weight:700;color:#e8eefc;margin-bottom:4px">🏗️ Production Orders</div>
    <div style="font-size:.66rem;color:#8a97ad;margin-bottom:6px">Queue what the town builds. <b>make N</b> = build N then stop · <b>keep N</b> = maintain N standing (rebuilds losses). Up to ${con.concurrent || 3} build at once. Orders override auto-grow.</div>
    ${rows}
    <div style="display:flex;align-items:center;gap:6px;margin-top:8px;font-size:.72rem">
      <select id="ord-kind" title="Which structure the town builds. Locked items (🔒) need the town to reach a higher tier first." style="background:#0b1120;border:1px solid #33456b;color:#e8eefc;border-radius:5px;padding:3px 4px">${opts}</select>
      <select id="ord-mode" title="make = build this many then stop. keep = maintain this many standing, rebuilding any that are lost." style="background:#0b1120;border:1px solid #33456b;color:#e8eefc;border-radius:5px;padding:3px 4px"><option value="make">make</option><option value="keep">keep</option></select>
      <input id="ord-target" type="number" value="1" min="1" title="How many to build (make) or to keep standing (keep)." style="width:50px;background:#0b1120;border:1px solid #33456b;color:#e8eefc;border-radius:5px;padding:3px 4px">
      <button class="btn" style="padding:3px 10px;font-size:.68rem" onclick="worldOrderAdd()" title="Queue this production order. Orders take priority over the town auto-build tier ladder.">+ add</button></div></div>`;
}
async function _ordPost(body) {
  try { await api('/api/world/build/order', { method: 'POST', body: JSON.stringify(body) }); await _pollWorld(); worldWorkTab(); }
  catch (e) { toast?.(e.message); }
}
function worldOrderAdd() {
  const kind = document.getElementById('ord-kind').value;
  const mode = document.getElementById('ord-mode').value;
  const target = document.getElementById('ord-target').value || 1;
  if (kind) _ordPost({ action: 'add', kind, mode, target });
}
function worldOrderPause(id, paused) { _ordPost({ action: 'update', id, paused }); }
function worldOrderDel(id) { _ordPost({ action: 'remove', id }); }
window.worldOrderAdd = worldOrderAdd; window.worldOrderPause = worldOrderPause; window.worldOrderDel = worldOrderDel;

const _STK_EMOJI = { logs: '🪵', ore: '⛏️', crops: '🌾', fish: '🎣', planks: '🔨' };
function _stockManager(st) {
  const co = st.company || {}, sp = co.stockpile || {}, tg = co.stock_targets || {};
  const res = ['logs', 'ore', 'crops', 'fish', 'planks'];
  const rows = res.map(r => {
    const have = sp[r] || 0, t = tg[r] || { floor: '', ceil: '' };
    const low = t.floor !== '' && t.floor != null && have < t.floor;
    return `<div style="display:flex;align-items:center;gap:6px;font-size:.72rem;margin:3px 0">
      <span style="width:74px;color:#c7d2e5">${_STK_EMOJI[r] || ''} ${r}</span>
      <span style="width:44px;text-align:right;color:${low ? '#f0a860' : '#8fd0ff'};font-weight:700">${have}</span>
      <span style="color:#66738c;font-size:.66rem">keep</span>
      <input id="stk-f-${r}" type="number" value="${t.floor}" placeholder="floor" title="Floor: if the stockpile falls below this, agents urgently gather this resource. Blank = no minimum." style="width:54px;background:#0b1120;border:1px solid #33456b;color:#e8eefc;border-radius:5px;padding:2px 4px">
      <span style="color:#66738c">…</span>
      <input id="stk-c-${r}" type="number" value="${t.ceil}" placeholder="ceil" title="Ceiling: once the stockpile reaches this, agents stop gathering this resource. Blank = no cap." style="width:54px;background:#0b1120;border:1px solid #33456b;color:#e8eefc;border-radius:5px;padding:2px 4px">
      <button class="btn" style="padding:2px 8px;font-size:.66rem" onclick="worldSetStock('${r}')" title="Save this keep-range (floor…ceiling) for this resource.">set</button>
      ${low ? '<span style="color:#f0a860;font-size:.62rem">⚠ low — agents gathering</span>' : ''}</div>`;
  }).join('');
  return `<div style="margin-top:14px;border-top:1px solid #26324a;padding-top:10px">
    <div style="font-size:.78rem;font-weight:700;color:#e8eefc;margin-bottom:4px">📦 Stockpile Manager</div>
    <div style="font-size:.66rem;color:#8a97ad;margin-bottom:6px">Set a keep-range per resource. Below <b>floor</b> → agents rush to gather it (urgent); at <b>ceil</b> → they stop. Blank = no target.</div>
    ${rows}</div>`;
}
async function worldSetStock(r) {
  const f = document.getElementById('stk-f-' + r).value, cc = document.getElementById('stk-c-' + r).value;
  if (f === '' && cc === '') return;
  try {
    await api('/api/world/stock/target', { method: 'POST', body: JSON.stringify({ resource: r, floor: f || 0, ceil: cc || 9999 }) });
    await _pollWorld(); worldWorkTab();
    toast?.(`keep ${r}: ${f || 0}…${cc || '∞'}`);
  } catch (e) { toast?.(e.message); }
}
window.worldSetStock = worldSetStock;

async function worldCycleWork(agentId, wt, cur) {
  const next = ((cur | 0) + 1) % 5;                            // · → 1 → 2 → 3 → 4 → ·
  try {
    await api('/api/world/work/priority', { method: 'POST', body: JSON.stringify({ agent_id: agentId, work_type: wt, priority: next }) });
    await _pollWorld(); worldWorkTab();
  } catch (e) { toast?.(e.message); }
}
async function worldBumpColumn(wt) {
  try {
    await api('/api/world/work/priority', { method: 'POST', body: JSON.stringify({ work_type: wt, priority: 1 }) });
    await _pollWorld(); worldWorkTab();
    toast?.(`${wt} → top priority for everyone`);
  } catch (e) { toast?.(e.message); }
}
window.worldWorkTab = worldWorkTab; window.worldCycleWork = worldCycleWork; window.worldBumpColumn = worldBumpColumn;

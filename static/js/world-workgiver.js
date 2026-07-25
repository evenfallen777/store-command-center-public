/* ══ THE COMPANY — 🧭 WorkGiver (owner work-priority directives) ══
   God Console tab over /api/world/work/givers (app/world_work.py). The owner
   pins a Work-Priority (0 = never … 4 = lowest, 1 = highest) for a WORK TYPE
   onto a whole DEPARTMENT or a single AGENT. Directives are stored always but
   only BITE while the master toggle (world_workgiver_enabled) is ON — the
   default is OFF, i.e. exactly the pre-WorkGiver scheduler behavior. Agent
   directives override dept directives; everything else about the scheduler
   (WorkGivers, gates, GPU throttles) is unchanged — this only reorders which
   job an agent reaches for first.

   Uses _worldModal (world-economy.js), _pill (world-control.js),
   api()/esc()/toast()/hlp() (app-core.js). Global-scope classic script. */

let _wgData = null;

async function worldWorkGiver() {
  try { _wgData = await api('/api/world/work/givers'); }
  catch (e) { toast?.('WorkGiver failed to load'); return; }
  _renderWorkGiver();
}

function _wgDirRow(x, d) {
  const wt = (d.work_types || []).find(w => w.key === x.work_type) || { label: x.work_type, icon: '' };
  const who = x.scope === 'dept' ? `🏢 ${esc(x.target)}`
    : `🧑 ${esc(((d.agents || []).find(a => a.key === x.target) || {}).name || x.target)}`;
  const pcol = x.priority === 0 ? '#f87171' : x.priority === 1 ? '#6ee7a8' : '#8ecaff';
  return `<div style="display:flex;gap:8px;align-items:center;font-size:.74rem;padding:4px 0;border-top:1px solid #131c30">
    <span style="min-width:120px;color:#e8eefc">${who}</span>
    <span style="color:#c7d2e5">${wt.icon} ${esc(wt.label)}</span>
    <span style="color:${pcol};font-weight:700">→ ${x.priority === 0 ? 'never' : 'P' + x.priority}</span>
    ${x.note ? `<span style="color:#54607a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">· ${esc(x.note)}</span>` : ''}
    <button class="btn" style="padding:1px 8px;font-size:.64rem;margin-left:auto"
      onclick='wgClear(${JSON.stringify(x.scope)}, ${JSON.stringify(x.target)}, ${JSON.stringify(x.work_type)})'>✕</button>
  </div>`;
}

function _renderWorkGiver() {
  const d = _wgData;
  const on = !!d.enabled;
  const agentOpts = (d.agents || []).map(a =>
    `<option value="agent:${esc(a.key)}">🧑 ${esc(a.name)} (${esc(a.dept || '')})</option>`).join('');
  const deptOpts = (d.depts || []).map(x => `<option value="dept:${esc(x)}">🏢 ${esc(x)}</option>`).join('');
  const wtOpts = (d.work_types || []).map(w => `<option value="${esc(w.key)}">${w.icon} ${esc(w.label)}</option>`).join('');
  const html = `
    <div style="font-size:.7rem;color:#8a97ad;margin-bottom:10px">Point the crew's Work-Priority scheduler where YOU want it:
      pin a priority for a work type on a department or a named agent. Directives only apply while the master is on —
      off (the default) is exactly today's behavior. Agent pins beat dept pins; per-agent numbers stay editable in 📌 Priorities.</div>

    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;margin-bottom:12px;
      background:${on ? '#12251b' : '#1a1414'};border:1px solid ${on ? '#2a5a3a' : '#5a2a2a'};cursor:pointer" onclick="wgMaster(${on ? 'false' : 'true'})">
      <div>
        <div style="font-weight:700;color:#e8eefc;font-size:.85rem">🧭 WorkGiver directives <span style="font-size:.66rem;color:${on ? '#6ee7a8' : '#7a86a0'}">· ${on ? 'ON' : 'OFF (default)'}</span></div>
        <div style="font-size:.66rem;color:#8a97ad">Master toggle (world_workgiver_enabled). Off ⇒ directives are stored but inert.
        ${hlp('Directives overlay the normal Work-Priority numbers (0 = never, 1 = highest … 4 = lowest) inside world_work.get_priorities. They change nothing about WHAT jobs exist or any gate — only the order an agent reaches for available work. All capability gates, prayer gates and GPU throttles apply unchanged downstream.')}</div>
      </div>${_pill(on, false)}
    </div>

    <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;padding:9px 11px;background:#0e1626;border:1px solid #26324a;border-radius:10px;margin-bottom:12px">
      <span style="font-size:.7rem;color:#c7d2e5">Assign</span>
      <select id="wg-target" style="padding:3px 6px;background:#0b1120;border:1px solid #26324a;border-radius:6px;color:#e8eefc;font-size:.72rem">
        ${deptOpts}${agentOpts}</select>
      <select id="wg-wt" style="padding:3px 6px;background:#0b1120;border:1px solid #26324a;border-radius:6px;color:#e8eefc;font-size:.72rem">${wtOpts}</select>
      <select id="wg-prio" style="padding:3px 6px;background:#0b1120;border:1px solid #26324a;border-radius:6px;color:#e8eefc;font-size:.72rem">
        <option value="1">P1 — highest</option><option value="2">P2</option><option value="3">P3</option>
        <option value="4">P4 — lowest</option><option value="0">0 — never</option></select>
      <input id="wg-note" placeholder="note (optional)" style="flex:1;min-width:120px;padding:3px 6px;background:#0b1120;border:1px solid #26324a;border-radius:6px;color:#e8eefc;font-size:.72rem">
      <button class="btn" style="padding:3px 12px;font-size:.72rem" onclick="wgAdd()">＋ Pin it</button>
    </div>

    <div style="font-weight:700;color:#e8eefc;margin:4px 0">📍 Active directives
      <span style="font-size:.66rem;color:#7a86a0;font-weight:400">— ${on ? 'applied live to the scheduler' : 'stored, waiting for the master toggle'}</span>
      <button class="btn" style="padding:1px 8px;font-size:.64rem;margin-left:6px" onclick="worldWorkGiver()">↻</button></div>
    <div style="background:#0b1120;border:1px solid #1b2740;border-radius:8px;padding:4px 10px">
      ${(d.directives || []).map(x => _wgDirRow(x, d)).join('') || '<div style="font-size:.72rem;color:#54607a;padding:6px 0">No directives — the scheduler runs exactly as before.</div>'}
    </div>`;
  _worldModal('🧭 WorkGiver — owner work-priority directives', html);
}

async function wgMaster(on) {
  try { _wgData = await api('/api/world/work/giver-master', { method: 'POST', body: JSON.stringify({ on }) }); _renderWorkGiver(); }
  catch (e) { toast?.(e?.message || 'Failed'); }
}

async function wgAdd() {
  const tv = document.getElementById('wg-target')?.value || '';
  const [scope, ...rest] = tv.split(':');
  const target = rest.join(':');
  const body = {
    scope, target,
    work_type: document.getElementById('wg-wt')?.value,
    priority: parseInt(document.getElementById('wg-prio')?.value || '1', 10),
    note: document.getElementById('wg-note')?.value || null,
  };
  try { _wgData = await api('/api/world/work/giver', { method: 'POST', body: JSON.stringify(body) }); _renderWorkGiver(); toast?.('📍 Pinned'); }
  catch (e) { toast?.(e?.message || 'Failed'); }
}

async function wgClear(scope, target, work_type) {
  try {
    _wgData = await api('/api/world/work/giver', { method: 'POST', body: JSON.stringify({ scope, target, work_type, priority: null }) });
    _renderWorkGiver();
  } catch (e) { toast?.(e?.message || 'Failed'); }
}

window.worldWorkGiver = worldWorkGiver;
window.wgMaster = wgMaster;
window.wgAdd = wgAdd;
window.wgClear = wgClear;

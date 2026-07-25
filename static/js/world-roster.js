/* ══ THE COMPANY — Company Roster (#9 QoL) ══
   Every agent at a glance in one sortable table: dept, what they're doing, level,
   mood, top skill, coins, goal. Click a row → full character sheet. Click a header
   → sort. Reads _worldState; uses _worldModal + worldSheet. Global classic script. */
let _rosterSort = { key: 'dept', dir: 1 };

function _rosterVal(a, key) {
  switch (key) {
    case 'name':  return (a.name || '').toLowerCase();
    case 'dept':  return (a.dept || a.job_class || '').toLowerCase();
    case 'state': return (a.state || '').toLowerCase();
    case 'level': return a.level || 0;
    case 'mood':  return a.mood_value == null ? 50 : a.mood_value;
    case 'skill': return (a.primary_skill || '').toLowerCase();
    case 'coins': return a.coins || 0;
    default:      return '';
  }
}
function worldRosterSort(key) {
  if (_rosterSort.key === key) _rosterSort.dir *= -1; else _rosterSort = { key, dir: 1 };
  worldRoster();
}
const _rMood = m => m < 35 ? '#ef4444' : m < 55 ? '#f0b45a' : '#6ee7a8';

async function worldRoster() {
  const st = await _ensureWorldState();
  if (!st) { toast?.('World not loaded yet'); return; }
  const ags = (st.agents || []).filter(a => a.kind === 'worker' || a.kind === 'openclaw');
  const { key, dir } = _rosterSort;
  ags.sort((a, b) => { const x = _rosterVal(a, key), y = _rosterVal(b, key); return (x < y ? -1 : x > y ? 1 : 0) * dir; });
  const th = (k, lbl, extra) => `<th onclick="worldRosterSort('${k}')" title="sort" style="cursor:pointer;padding:4px 6px;text-align:${extra || 'left'};color:#9fb4d6;border-bottom:1px solid #26324a;user-select:none;white-space:nowrap">${lbl}${key === k ? (dir > 0 ? ' ▲' : ' ▼') : ''}</th>`;
  const rows = ags.map(a => {
    const m = a.mood_value == null ? 50 : a.mood_value;
    const flags = (a.broken ? ' 😤' : '') + (a.downed ? ' 🩸' : '') + (a.posted_to ? ' 📌' : '') + ((a.debt || 0) > 0 ? ' 💸' : '');
    return `<tr onclick="worldCloseModal();worldSheet(${a.id})" style="cursor:pointer;border-bottom:1px solid #141c2b"
        onmouseover="this.style.background='#18202e'" onmouseout="this.style.background=''">
      <td style="padding:3px 6px;color:#e8eefc;white-space:nowrap">${esc(a.name)}<span style="font-size:.7rem">${flags}</span></td>
      <td style="padding:3px 6px;color:#9fb4d6">${esc(a.dept || a.job_class || '')}</td>
      <td style="padding:3px 6px;color:#8fb4e0;white-space:nowrap">${esc(a.state || '')}</td>
      <td style="padding:3px 6px;text-align:center;color:#c7d2e5">${a.level || 0}</td>
      <td style="padding:3px 6px;white-space:nowrap"><span style="display:inline-block;width:32px;height:6px;background:#0b1120;border-radius:3px;overflow:hidden;vertical-align:middle"><span style="display:block;height:100%;width:${m}%;background:${_rMood(m)}"></span></span> <span style="color:${_rMood(m)};font-size:.66rem">${Math.round(m)}</span></td>
      <td style="padding:3px 6px;color:#c7d2e5">${esc(a.primary_skill || '—')}</td>
      <td style="padding:3px 6px;text-align:right;color:#f0c674">${a.coins || 0}</td>
      <td style="padding:3px 6px;color:#66738c;font-size:.66rem;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.goal || '')}</td>
    </tr>`;
  }).join('');
  const avg = Math.round(ags.reduce((s, a) => s + (a.mood_value == null ? 50 : a.mood_value), 0) / Math.max(1, ags.length));
  const debt = ags.filter(a => (a.debt || 0) > 0).length, down = ags.filter(a => a.downed).length, brk = ags.filter(a => a.broken).length;
  _worldModal('🧑‍🤝‍🧑 Company Roster', `
    <div style="font-size:.72rem;color:#8a97ad;margin-bottom:8px">${ags.length} on staff · avg mood <b style="color:${_rMood(avg)}">${avg}</b>${debt ? ` · <span style="color:#f0a860">${debt} in debt 💸</span>` : ''}${down ? ` · <span style="color:#f0908a">${down} down 🩸</span>` : ''}${brk ? ` · <span style="color:#f0a860">${brk} breaking 😤</span>` : ''} · <span style="color:#66738c">click a row → full sheet · click a header → sort · 📌 posted</span></div>
    <div style="overflow-x:auto"><table style="border-collapse:collapse;font-size:.74rem;min-width:100%">
      <thead><tr>${th('name', 'Name')}${th('dept', 'Dept')}${th('state', 'Doing')}${th('level', 'Lv', 'center')}${th('mood', 'Mood')}${th('skill', 'Top skill')}${th('coins', '🪙', 'right')}<th style="padding:4px 6px;text-align:left;color:#9fb4d6;border-bottom:1px solid #26324a">Goal</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`);
}
window.worldRoster = worldRoster; window.worldRosterSort = worldRosterSort;

/* ══ Research Tree (#7) — pick projects; prereqs gate the deeper ones ══ */
async function worldSetResearch(key) {
  try {
    const r = await api('/api/world/research', { method: 'POST', body: JSON.stringify({ key }) });
    if (!r.ok) { toast?.('Prerequisites not met'); return; }
    await _pollWorld(); worldResearch(); toast?.('▶ researching ' + key);
  } catch (e) { toast?.(e.message); }
}
const _RES_BADGE = { done: ['#3fae6a', '✓ done'], active: ['#e0b050', 'researching'],
                     available: ['#4a90d9', 'ready'], locked: ['#7a86a0', '🔒 locked'] };
async function worldResearch() {
  const st = await _ensureWorldState();
  if (!st) { toast?.('World not loaded yet'); return; }
  const r = (st.company || {}).research || { projects: [] };
  const cards = (r.projects || []).map(p => {
    const [col, lbl] = _RES_BADGE[p.status] || ['#7a86a0', p.status];
    const req = (p.req || []).length ? `needs: ${p.req.join(', ')}` : 'no prerequisites';
    const bar = p.status === 'active'
      ? `<div style="height:5px;background:#0b1120;border-radius:3px;margin-top:5px;overflow:hidden"><div style="height:100%;width:${p.pct}%;background:${col}"></div></div>` : '';
    const click = p.status === 'available' ? `onclick="worldSetResearch('${p.key}')" style="cursor:pointer" onmouseover="this.style.borderColor='#4a90d9'" onmouseout="this.style.borderColor='#26324a'"` : '';
    return `<div ${click} style="border:1px solid ${p.status === 'active' ? '#e0b050' : '#26324a'};border-radius:8px;padding:8px;background:#131a28">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-weight:700;color:#e8eefc">${p.icon} ${esc(p.name)}</span>
        <span style="font-size:.6rem;color:${col};font-weight:700">${lbl}</span></div>
      <div style="font-size:.68rem;color:#8fd0a0;margin-top:2px">${esc(p.effect)}</div>
      <div style="font-size:.62rem;color:#66738c;margin-top:2px">${p.rp} RP · ${esc(req)}${p.status === 'active' ? ` · ${p.pct}%` : ''}</div>
      ${bar}${p.status === 'available' ? '<div style="font-size:.6rem;color:#4a90d9;margin-top:4px">▶ click to research</div>' : ''}</div>`;
  }).join('');
  _worldModal('🔬 Research Tree', `
    <div style="font-size:.72rem;color:#8a97ad;margin-bottom:8px">Agents studying at the Library earn research points that flow into your <b>active</b> project. Pick what to pursue — prerequisites unlock the deeper projects.${r.speed > 1 ? ` <span style="color:#8fd0a0">· research speed ×${r.speed}</span>` : ''}</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px">${cards}</div>`);
}
window.worldResearch = worldResearch; window.worldSetResearch = worldSetResearch;

/* ══ Town Schedule (#6) — a 24-hour timetable the sim obeys ══ */
const _SCH_ORDER = ['sleep', 'work', 'rec', 'any'];
async function worldSetSchedHour(hour, cur) {
  const next = _SCH_ORDER[(_SCH_ORDER.indexOf(cur) + 1) % 4];
  try {
    await api('/api/world/schedule', { method: 'POST', body: JSON.stringify({ hour, band: next }) });
    await _pollWorld(); worldSchedule();
  } catch (e) { toast?.(e.message); }
}
async function worldSchedAll(band) {
  try {
    await api('/api/world/schedule', { method: 'POST', body: JSON.stringify({ schedule: Array(24).fill(band) }) });
    await _pollWorld(); worldSchedule();
  } catch (e) { toast?.(e.message); }
}
async function worldSchedule() {
  const st = await _ensureWorldState();
  if (!st) { toast?.('World not loaded yet'); return; }
  const s = (st.company || {}).schedule || {};
  const sched = s.schedule || [], meta = s.meta || {}, nowH = s.hour;
  const cells = sched.map((b, h) => {
    const m = meta[b] || { icon: '?', color: '#556', label: b }, isNow = h === nowH;
    return `<div onclick="worldSetSchedHour(${h},'${b}')" title="${h}:00 — ${m.label} · click to change"
      style="cursor:pointer;flex:1 0 30px;text-align:center;padding:3px 0;background:${m.color};opacity:${isNow ? 1 : .78};box-shadow:${isNow ? '0 0 0 2px #fff inset' : 'none'};border-radius:4px">
      <div style="font-size:.54rem;color:#0b1018;font-weight:700">${String(h).padStart(2, '0')}</div>
      <div style="font-size:.82rem;line-height:1">${m.icon}</div></div>`;
  }).join('');
  const legend = _SCH_ORDER.map(b => { const m = meta[b] || {}; return `<span style="background:${m.color};color:#0b1018;padding:1px 8px;border-radius:4px;font-size:.66rem;font-weight:700">${m.icon} ${m.label}</span>`; }).join(' ');
  const presets = _SCH_ORDER.map(b => { const m = meta[b] || {}; return `<button class="btn" style="padding:2px 8px;font-size:.64rem" onclick="worldSchedAll('${b}')">all ${m.label}</button>`; }).join(' ');
  _worldModal('🕐 Town Schedule', `
    <div style="font-size:.72rem;color:#8a97ad;margin-bottom:8px">What the crew does each hour. Click an hour to cycle <b>Sleep → Work → Free → Anything</b>. Critical needs (exhaustion, starving) and raids always override. The current hour is outlined.</div>
    <div style="display:flex;flex-wrap:wrap;gap:3px;margin-bottom:10px">${cells}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px">${legend}</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap"><span style="font-size:.66rem;color:#66738c;align-self:center">quick set:</span> ${presets}</div>`);
}
window.worldSchedule = worldSchedule; window.worldSetSchedHour = worldSetSchedHour; window.worldSchedAll = worldSchedAll;

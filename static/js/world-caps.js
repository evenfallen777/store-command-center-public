/* ══ THE COMPANY — ⚡ Capabilities (the FULL catalog) ══
   God Console tab over /api/world/caps (app/world_caps.py): every real
   product/action the platform makes — image, video, chains, the Studio
   pipeline, music, 3D, social drafts + gated publishing, Etsy/Printify/
   resell listings, research, trends, governance, security — each one
   "trigger an action → get a product back", each individually toggle-gated
   for agents. The pill gates AGENT access; the ▶ button is YOUR explicit
   trigger and always works. Recent runs show the product coming back live.

   Uses _worldModal/_consoleHost (world-economy.js), _pill (world-control.js),
   api()/esc()/toast()/hlp() (app-core.js). Global-scope classic script. */

async function worldCaps() {
  let d;
  try { d = await api('/api/world/caps'); }
  catch (e) { toast?.('Capability catalog failed to load'); return; }
  _renderCaps(d);
}

function _capsHeader(d) {
  const m = d.master, a = d.auto || {};
  return `
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;margin-bottom:14px">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;
        background:${m ? '#12251b' : '#1a1414'};border:1px solid ${m ? '#2a5a3a' : '#5a2a2a'};cursor:pointer" onclick="capsGate('master',${m ? 'false' : 'true'})">
        <div>
          <div style="font-weight:700;color:#e8eefc;font-size:.85rem">🔑 Agent access master</div>
          <div style="font-size:.66rem;color:#8a97ad">Gates every AGENT invocation below. Your own ▶ triggers always work — the click is the gate. ${hlp('Off = no agent/loop may invoke any capability, whatever the per-cap pills say. God-Panel triggers are explicit human actions and stay available either way; real-world side effects (publishing, listings) still go through the prayer gates.')}</div>
        </div>${_pill(m, false)}
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;
        background:${a.enabled ? '#12251b' : '#0e1626'};border:1px solid ${a.enabled ? '#2a5a3a' : '#26324a'}">
        <div>
          <div style="font-weight:700;color:#e8eefc;font-size:.85rem;cursor:pointer" onclick="capsGate('auto',${a.enabled ? 'false' : 'true'})">🔁 Agent auto-loop <span style="font-size:.66rem;color:${a.enabled ? '#6ee7a8' : '#7a86a0'}">· ${a.enabled ? 'ON' : 'off'}</span></div>
          <div style="font-size:.66rem;color:#8a97ad">One random agent uses one enabled 🤝-safe capability at most every
            <input id="caps-auto-int" type="number" min="30" value="${a.interval_min || 240}" onclick="event.stopPropagation()"
              style="width:56px;padding:1px 4px;background:#0b1120;border:1px solid #26324a;border-radius:5px;color:#e8eefc"> min
            <button class="btn" style="padding:1px 7px;font-size:.64rem" onclick="capsAutoSave(event)">💾</button>
            ${hlp('OFF by default. Rides the existing world_auto daemon within active hours; only free, local, non-publishing capabilities are eligible, and only those whose gate you turned on. The company master pause stops it too.')}</div>
        </div>${_pill(a.enabled, false)}
      </div>
    </div>`;
}

function _capsRun(r) {
  const col = { done: '#6ee7a8', running: '#8ecaff', error: '#f87171' }[r.status] || '#c7d2e5';
  const prod = r.product
    ? ` · product: <b style="color:#c4b5fd">${esc(r.product.status || '')}</b>${r.product.path ? ` <span style="color:#54607a">${esc(String(r.product.path).split('/').pop())}</span>` : ''}`
    : (r.product_ref ? ` · ref #${esc(String(r.product_ref))}` : '');
  return `<div style="display:flex;gap:8px;align-items:baseline;font-size:.72rem;padding:3px 0;border-top:1px solid #131c30">
    <span style="color:${col};width:52px">${esc(r.status)}</span>
    <span style="color:#e8eefc">${esc(r.label)}</span>
    <span style="color:#7a86a0">${r.actor === 'agent' ? `🤖 ${esc(r.agent || 'agent')}` : '🌟 you'}${prod}</span>
    <span style="color:#54607a;margin-left:auto;white-space:nowrap">${esc((r.created_at || '').slice(5, 16))}</span>
  </div>`;
}

function _renderCaps(d) {
  const groups = (d.groups || []).map(g => `
    <div style="font-size:.66rem;color:#7a86a0;text-transform:uppercase;letter-spacing:.06em;margin:12px 0 4px">${esc(g.group)}</div>
    ${g.caps.map(c => `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:7px 10px;margin-bottom:4px;
                  background:#0e1626;border:1px solid #26324a;border-radius:8px">
        <div style="min-width:0">
          <div style="font-size:.8rem;color:#e8eefc">${esc(c.label)}
            ${c.agent_safe ? '<span title="eligible for the agent auto-loop" style="font-size:.62rem">🤝</span>' : ''}
            ${hlp((c.desc || '') + ' Product: ' + (c.product || '—') + '.')}</div>
          <div style="font-size:.64rem;color:#7a86a0">→ ${esc(c.product || '')}</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex-shrink:0">
          <button class="btn" style="padding:3px 10px;font-size:.72rem" title="Trigger now (your click is the gate)"
            onclick='capsInvoke(${JSON.stringify(c.id)}, ${JSON.stringify(c.args || [])})'>▶</button>
          <span style="cursor:pointer;font-size:.64rem;padding:1px 7px;border-radius:9px;white-space:nowrap;
                       border:1px solid ${(c.grants || []).length ? '#6d5aff' : '#26324a'};
                       color:${(c.grants || []).length ? '#c4b5fd' : '#54607a'}"
            title="Per-agent grants${(c.grants || []).length ? ': ' + esc(c.grants.join(', ')) : ' (none)'} — named agents allowed past the pill (catalog master + prayer gates still apply)"
            onclick='capsGrantEdit(${JSON.stringify(c.id)}, ${JSON.stringify(c.grants || [])})'>👤${(c.grants || []).length || ''}</span>
          <span title="Agent access: ${c.gate_on ? 'allowed' : 'gated off'}" style="cursor:pointer"
            onclick="capsGate('${c.id}',${c.gate_on ? 'false' : 'true'})">${_pill(c.gate_on, false)}</span>
        </div>
      </div>`).join('')}`).join('');

  const runs = `
    <div style="font-weight:700;color:#e8eefc;margin:16px 0 4px">📦 Recent runs <span style="font-size:.66rem;color:#7a86a0;font-weight:400">— the product coming back</span>
      <button class="btn" style="padding:1px 8px;font-size:.64rem;margin-left:6px" onclick="worldCaps()">↻</button></div>
    <div style="background:#0b1120;border:1px solid #1b2740;border-radius:8px;padding:4px 10px">
      ${(d.runs || []).map(_capsRun).join('') || '<div style="font-size:.72rem;color:#54607a;padding:6px 0">Nothing triggered yet.</div>'}
    </div>`;

  _worldModal('⚡ Capabilities — the full catalog',
    `<div style="font-size:.7rem;color:#8a97ad;margin-bottom:10px">Every capability maps to a REAL platform action and hands back a product.
      Pills gate <b>agent</b> access (new ones default off); ▶ triggers it now as you. Money/publishing actions still land in 🙏 Prayers.</div>`
    + _capsHeader(d) + groups + runs);
}

async function capsGate(id, on) {
  try { _renderCaps(await api('/api/world/caps/gate', { method: 'POST', body: JSON.stringify({ id, on }) })); }
  catch (e) { toast?.(e?.message || 'Failed'); }
}

async function capsAutoSave(ev) {
  ev?.stopPropagation?.();
  const v = parseInt(document.getElementById('caps-auto-int')?.value || '240', 10);
  try { _renderCaps(await api('/api/world/caps/auto-interval', { method: 'POST', body: JSON.stringify({ interval_min: v }) })); toast?.('Saved'); }
  catch (e) { toast?.('Failed'); }
}

async function capsGrantEdit(id, current) {
  const v = prompt(
    'Per-agent grants — agent names, comma-separated.\n' +
    'A grant lets ONLY these named agents use this capability while its global pill is off. ' +
    'The catalog master still applies, and real-world actions still land in 🙏 Prayers. ' +
    'Empty = no grants (the default).', (current || []).join(', '));
  if (v == null) return;                                 // cancelled
  const agents = v.split(',').map(s => s.trim()).filter(Boolean);
  try {
    _renderCaps(await api('/api/world/caps/grant', { method: 'POST', body: JSON.stringify({ id, agents }) }));
    toast?.(agents.length ? `👤 Granted to ${agents.length} agent(s)` : '👤 Grants cleared');
  } catch (e) { toast?.(e?.message || 'Failed'); }
}

async function capsInvoke(id, argSpecs) {
  const args = {};
  for (const s of (argSpecs || [])) {
    const v = prompt(s.label || s.name, s.default || '');
    if (v == null) return;                       // cancelled
    args[s.name] = v;
  }
  try {
    const r = await api('/api/world/caps/invoke', { method: 'POST', body: JSON.stringify({ id, args }) });
    toast?.(r.ok ? `⚡ ${r.result || 'triggered'}` : (r.error || 'Failed'));
  } catch (e) { toast?.(e?.message || 'Failed'); }
  setTimeout(() => { if (document.getElementById('caps-auto-int')) worldCaps(); }, 900);
}

window.worldCaps = worldCaps;
window.capsGate = capsGate;
window.capsAutoSave = capsAutoSave;
window.capsInvoke = capsInvoke;
window.capsGrantEdit = capsGrantEdit;

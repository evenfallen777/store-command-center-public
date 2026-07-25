/* ══ THE COMPANY — 🎛️ Breakers (Phase 3 of the God Panel rework) ══
   The single consolidated MASTER BREAKER page. Before this, the "everything
   that gates or switches the Company" surface was scattered across five
   places: God Console Prayers (gates), Company Control (master + 13 system
   toggles + run mode), the Info Board (world_auto autonomy-loop config), the
   Settings modal (misc knobs) and Settings→Systems. This page reads/writes
   the SAME endpoints those surfaces already use — nothing new on the backend —
   it just paints them all in one place.

   Every handler here (brk*) intentionally does NOT reuse the origin tab's own
   handler function (controlMaster/controlSystem/worldSetMode/worldToggleGate/
   worldAutoToggle/…) even though it could reach it (classic scripts, shared
   global scope): those handlers refresh by repainting THEIR OWN tab
   (_renderControl(d) / worldGod() / worldBoard()), which would yank the user
   off the Breakers page on the very first click. The brk* wrappers hit the
   identical endpoint + payload, then repaint via worldBreakers() instead —
   so behavior matches the old scattered surfaces exactly, just staying put.

   Uses _worldModal/_consoleHost (world-economy.js), _pill (world-control.js,
   shared global scope) + api()/esc()/toast()/hlp() (app-core.js).
   Global-scope classic script — not a module. */

async function worldBreakers() {
  let panel, ops, gates, auto, settings;
  try {
    [panel, ops, gates, auto, settings] = await Promise.all([
      api('/api/world/control/panel'),
      api('/api/world/ops/summary'),
      api('/api/world/ops/gates'),
      api('/api/world/ops/auto-config'),
      api('/api/settings'),
    ]);
  } catch (e) { toast?.('Breakers failed to load'); return; }
  _renderBreakers(panel, ops, gates, auto, settings);
}

/* the 3 irreversible kinds (app/world_ops.py ALWAYS_GATE) — never toggleable,
   shown here so the breaker page is honest about what NEVER auto-runs even
   though they have no on/off control anywhere in the UI. */
const _BRK_ALWAYS_GATE = [
  { icon: '💸', label: 'PayPal payouts', note: 'Real money leaving the treasury to your PayPal.' },
  { icon: '👛', label: 'Wallet sends', note: 'Any JellyCoin/wallet send.' },
  { icon: '🔑', label: 'Secret export', note: 'Exporting API keys or other secrets.' },
];

function _renderBreakers(panel, ops, gates, auto, settings) {
  const m = panel.master;
  const rm = panel.run_mode || 'normal';
  const modeReview = ops.mode === 'review';

  // ── 1) Master + run mode + automation mode + cap ──────────────────────────
  const masterCard = `
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:14px 16px;margin-bottom:12px;border-radius:12px;
      background:${m ? 'linear-gradient(135deg,#12251b,#0f1626)' : '#1a1414'};border:1px solid ${m ? '#2a5a3a' : '#5a2a2a'}">
      <div>
        <div style="font-weight:800;font-size:1.05rem;color:#e8eefc">${m ? '🟢 The Company is running' : '⏸️ The Company is dormant'}</div>
        <div style="font-size:.74rem;color:#8a97ad;margin-top:2px">Master switch — gates every autonomous system on this page. ${hlp('When off, every system below is forced off on its next tick regardless of its own switch. A system is effective only when this master AND its own toggle are on.')}</div>
      </div>
      <button class="btn" style="padding:8px 16px;font-weight:700;${m ? 'background:#5a2a2a;border-color:#7c3a3a' : 'background:#1f4a32;border-color:#2a5a3a;color:#6ee7a8'}" onclick="brkMaster(${m ? 'false' : 'true'})">${m ? '⏸ Pause all' : '▶ Wake the Company'}</button>
    </div>`;

  const rmBtn = (id, label, desc) => `<button class="btn" title="${esc(desc)}" onclick="brkRunMode('${id}')"
    style="flex:1;padding:7px 6px;font-weight:600;${rm === id ? 'background:#1f3a5a;border-color:#3a6a9a;color:#8ecaff' : ''}">${label}</button>`;
  const runmode = `
    <div style="padding:10px 14px;margin-bottom:12px;border-radius:12px;background:#0e1626;border:1px solid #26324a">
      <div style="font-size:.74rem;color:#9fc0ff;font-weight:600;margin-bottom:6px">⏱️ Run mode <span style="color:#8a97ad;font-weight:400">· how fast + how autonomous the sim runs</span></div>
      <div style="display:flex;gap:6px">
        ${rmBtn('normal', '🐢 Normal', 'Real pace, your automation settings as-is.')}
        ${rmBtn('fast', '⚡ Fast', '~5x sim speed. LLM/GPU/money keep their own real cadences + gates.')}
        ${rmBtn('test', '🧪 Test', 'Fast + auto-run the FREE loops so the world visibly progresses. Money & code stay gated.')}
      </div>
    </div>`;

  const automation = `
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:16px">
      <div style="background:#0e1626;border:1px solid #26324a;border-radius:8px;padding:8px 10px">
        <div style="font-size:.64rem;color:#7a86a0">🤖 SPENDING AUTOMATION ${hlp('Whether agents can spend real money on their own. "Review all" = every paid action waits for your approval. "Auto ≤budget" = agents may spend automatically under the monthly cap; over-cap still asks first.')}</div>
        <div style="display:flex;gap:4px;margin-top:4px">
          <button class="btn" style="padding:3px 9px;font-size:.72rem;${modeReview ? 'background:#3a2a5a;border-color:#6d5aff;color:#c4b5fd' : ''}" onclick="brkSetMode('review')">🙏 Review all</button>
          <button class="btn" style="padding:3px 9px;font-size:.72rem;${!modeReview ? 'background:#1f4a32;border-color:#2a5a3a;color:#6ee7a8' : ''}" onclick="brkSetMode('budget')">💸 Auto ≤budget</button>
        </div>
      </div>
      <div style="background:#0e1626;border:1px solid #26324a;border-radius:8px;padding:8px 10px">
        <div style="font-size:.64rem;color:#7a86a0">💰 MONTHLY CAP ${hlp('The hard ceiling on real money agents can spend per month on Etsy/Printify listings before even Auto ≤budget mode has to ask you.')}</div>
        <button class="btn" style="padding:3px 9px;font-size:.78rem;margin-top:4px" onclick="brkSetCap()">⚙️ $${((ops.cap_cents || 0) / 100).toFixed(2)}/mo</button>
        <span style="font-size:.66rem;color:#54607a;margin-left:6px">spent this cycle: $${((ops.cycle_spend_cents || 0) / 100).toFixed(2)}</span>
      </div>
    </div>`;

  // ── 2) Gates ────────────────────────────────────────────────────────────
  const gateRow = (checked, label, key, always) => `
    <label style="display:flex;align-items:center;gap:7px;font-size:.76rem;color:#c7d2e5;background:#0b1120;border:1px solid #26324a;border-radius:6px;padding:5px 9px;cursor:${always ? 'default' : 'pointer'}${always ? ';opacity:.65' : ''}">
      <input type="checkbox" ${checked ? 'checked' : ''} ${always ? 'disabled' : ''} onchange="brkToggleGate('${key}',this.checked)">
      <span>${esc(label)}${always ? ' 🔒' : ''}</span>
    </label>`;
  const gatesHtml = `
    <div style="font-weight:700;color:#e8eefc;margin:4px 0 3px">🔒 Gates <span style="font-size:.66rem;color:#7a86a0;font-weight:400">— what always waits for your blessing before it runs</span></div>
    <div style="background:#0e1626;border:1px solid #26324a;border-radius:8px;padding:10px;margin-bottom:8px">
      <div style="font-size:.68rem;color:#7a86a0;margin-bottom:6px">Toggleable — on = always asks you first (never auto-runs); off = may auto-run in Auto ≤budget mode.</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px">
        ${gateRow(gates.creations, '🎨 Always judge creations before publishing', 'creations', false)}
        ${(gates.kinds || []).map(k => gateRow(k.gated, k.label, k.kind, false)).join('')}
      </div>
    </div>
    <div style="background:#150f0f;border:1px solid #4a2a2a;border-radius:8px;padding:10px;margin-bottom:16px">
      <div style="font-size:.68rem;color:#e8a0a0;margin-bottom:6px">🔒 Always require your blessing — irreversible, can never be turned off ${hlp('paypal_payout, wallet_send, secret_export (app/world_ops.py ALWAYS_GATE). Shown locked so this breaker is honest about the full picture.')}</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px">
        ${_BRK_ALWAYS_GATE.map(g => `<label title="${esc(g.note)}" style="display:flex;align-items:center;gap:7px;font-size:.76rem;color:#c7d2e5;background:#0b1120;border:1px solid #3a2a2a;border-radius:6px;padding:5px 9px;opacity:.7">
          <input type="checkbox" checked disabled><span>${g.icon} ${esc(g.label)} 🔒</span></label>`).join('')}
      </div>
    </div>`;

  // ── 3) Autonomy loop (world_auto) ──────────────────────────────────────
  const aOn = !!auto.enabled;
  const autoHtml = `
    <div style="font-weight:700;color:#e8eefc;margin:4px 0 3px">🔁 Autonomy loop <span style="font-size:.66rem;color:#7a86a0;font-weight:400">— the creation/govern/sell cadence (same config as the Board tab)</span></div>
    <div style="background:${aOn ? '#12251b' : '#0e1626'};border:1px solid ${aOn ? '#2a5a3a' : '#26324a'};border-radius:8px;padding:10px;margin-bottom:16px">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
        <div><b style="color:#e8eefc">🤖 Autonomous creation</b> <span style="font-size:.72rem;color:${aOn ? '#6ee7a8' : '#7a86a0'}"> · ${aOn ? 'ON' : 'OFF'}</span></div>
        <button class="btn" style="padding:4px 10px;font-size:.74rem;${aOn ? 'background:#5a2a2a;border-color:#7c3a3a' : 'background:#1f4a32;border-color:#2a5a3a;color:#6ee7a8'}" onclick="brkAutoToggle(${aOn ? 'false' : 'true'})">${aOn ? '⏸ Pause' : '▶ Turn on'}</button>
      </div>
      <div style="display:flex;gap:10px;align-items:center;margin-top:8px;font-size:.72rem;color:#8a97ad;flex-wrap:wrap">
        <span>Every <input id="brk-auto-int" type="number" min="5" value="${auto.interval_min || 120}" style="width:56px;padding:2px 5px;background:#0b1120;border:1px solid #26324a;border-radius:5px;color:#e8eefc"> min</span>
        <span>Active <input id="brk-auto-s" type="number" min="0" max="23" value="${auto.active_start ?? 8}" style="width:44px;padding:2px 5px;background:#0b1120;border:1px solid #26324a;border-radius:5px;color:#e8eefc">–<input id="brk-auto-e" type="number" min="0" max="24" value="${auto.active_end ?? 22}" style="width:44px;padding:2px 5px;background:#0b1120;border:1px solid #26324a;border-radius:5px;color:#e8eefc">h</span>
        <span>🏛️ Govern every <input id="brk-auto-gov" type="number" min="0" value="${auto.govern_min ?? 360}" style="width:56px;padding:2px 5px;background:#0b1120;border:1px solid #26324a;border-radius:5px;color:#e8eefc"> min</span>
        <button class="btn" style="padding:2px 9px;font-size:.7rem" onclick="brkAutoSave()">💾 Save</button>
      </div>
      <div style="display:flex;gap:6px;align-items:center;margin-top:8px;font-size:.72rem;color:#8a97ad;flex-wrap:wrap">
        <span>Make:</span>
        ${[['image', '🖼️ Art'], ['music', '🎵 Music'], ['video', '🎬 Video'], ['3d', '🧊 3D']].map(([k, lbl]) => {
          const on = (auto.kinds || ['image']).includes(k);
          return `<button class="btn" style="padding:2px 9px;font-size:.7rem;${on ? 'background:#1f4a32;border-color:#2a5a3a;color:#6ee7a8' : ''}" onclick="brkAutoKind('${k}')">${on ? '✓ ' : ''}${lbl}</button>`;
        }).join('')}
      </div>
    </div>`;

  // ── 4) Systems (13 toggles, grouped) ───────────────────────────────────
  const groups = {};
  (panel.systems || []).forEach(s => (groups[s.group] = groups[s.group] || []).push(s));
  const sysHtml = `
    <div style="font-weight:700;color:#e8eefc;margin:4px 0 3px">⚙️ Systems <span style="font-size:.66rem;color:#7a86a0;font-weight:400">— every autonomy switch, gated by the master above</span></div>
    ${Object.entries(groups).map(([g, arr]) => `
      <div style="font-size:.64rem;color:#7a86a0;text-transform:uppercase;letter-spacing:.06em;margin:8px 0 4px">${esc(g)}</div>
      ${arr.map(s => {
        const gated = s.desired && !m;
        return `<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:7px 10px;margin-bottom:4px;background:#0e1626;border:1px solid #26324a;border-radius:8px;cursor:pointer" onclick="brkSystemToggle('${s.id}',${s.desired ? 'false' : 'true'})">
          <div style="min-width:0">
            <div style="font-size:.8rem;color:#e8eefc">${esc(s.label)} ${gated ? '<span style="font-size:.64rem;color:#f59e0b">· paused by master</span>' : ''}</div>
            <div style="font-size:.66rem;color:#7a86a0">${esc(s.desc || '')}</div>
          </div>
          ${_pill(s.desired, false)}
        </div>`;
      }).join('')}`).join('')}`;

  // ── 5) Missing handles: world_sell_interval_min / world_sell_channel ──
  const sellInt = settings.world_sell_interval_min ?? '180';
  const sellChan = settings.world_sell_channel || 'etsy';
  const sellHtml = `
    <div style="font-weight:700;color:#e8eefc;margin:16px 0 3px">🕳️ Missing handles <span style="font-size:.66rem;color:#7a86a0;font-weight:400">— read by the autonomy loop, previously surfaced in no panel</span></div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;background:#0e1626;border:1px solid #26324a;border-radius:8px;padding:10px;margin-bottom:6px">
      <span style="font-size:.72rem;color:#8a97ad">Autonomous listing every ${hlp('How often (minutes) the "Autonomous listing" system (world_sell_auto, in Systems above) may draft a new paid listing.')} <input id="brk-sell-int" type="number" min="1" value="${esc(sellInt)}" style="width:64px;padding:4px 7px;background:#0b1120;border:1px solid #26324a;border-radius:6px;color:#e8eefc"> min</span>
      <span style="font-size:.72rem;color:#8a97ad">Channel ${hlp('Which storefront autonomous listing drafts to.')}
        <select id="brk-sell-chan" style="padding:4px 7px;background:#0b1120;border:1px solid #26324a;border-radius:6px;color:#e8eefc">
          <option value="etsy" ${sellChan === 'etsy' ? 'selected' : ''}>Etsy</option>
          <option value="printify" ${sellChan === 'printify' ? 'selected' : ''}>Printify</option>
        </select></span>
      <button class="btn" style="padding:4px 10px;font-size:.72rem" onclick="brkSellSave()">💾 Save</button>
    </div>`;

  _worldModal('🎛️ Master Breaker', masterCard + runmode + automation + gatesHtml + autoHtml + sysHtml + sellHtml);
}

// ── handlers — same endpoints as the origin surfaces, refresh THIS page ────
async function brkMaster(on) {
  try { await api('/api/world/control/master', { method: 'POST', body: JSON.stringify({ on }) }); toast?.(on ? '🟢 Company running' : '⏸️ Company paused'); }
  catch (e) { toast?.('Failed'); }
  worldBreakers();
}
async function brkRunMode(mode) {
  try {
    await api('/api/world/control/runmode', { method: 'POST', body: JSON.stringify({ mode }) });
    toast?.(mode === 'test' ? '🧪 Test — dry run, evolving fast (money/code still gated)'
          : mode === 'fast' ? '⚡ Fast — ~5x sim speed'
          : '🐢 Normal pace');
  } catch (e) { toast?.(e.message); }
  worldBreakers();
}
async function brkSetMode(mode) {
  try { await api('/api/world/ops/config', { method: 'POST', body: JSON.stringify({ mode }) }); }
  catch (e) { toast?.('Failed'); }
  worldBreakers();
}
async function brkSetCap() {
  const v = prompt('Monthly bill cap in dollars:');
  if (v == null) return;
  try { await api('/api/world/ops/config', { method: 'POST', body: JSON.stringify({ cap_dollars: parseFloat(v) || 0 }) }); }
  catch (e) { toast?.('Failed'); }
  worldBreakers();
}
async function brkToggleGate(key, on) {
  try {
    await api('/api/world/ops/gates', { method: 'POST', body: JSON.stringify({ key, on }) });
    toast?.(on ? '🔒 Gate on — always asks you first' : '🔓 Gate off — may auto-run');
  } catch (e) { toast?.(e?.message || 'Failed'); }
  worldBreakers();
}
async function brkSystemToggle(id, on) {
  try { await api('/api/world/control/system', { method: 'POST', body: JSON.stringify({ id, on }) }); }
  catch (e) { toast?.(e?.message || 'Failed'); }
  worldBreakers();
}
async function brkAutoToggle(on) {
  try { await api('/api/world/ops/auto-config', { method: 'POST', body: JSON.stringify({ enabled: on }) }); toast?.(on ? '🤖 Automation on' : 'Automation paused'); }
  catch (e) { toast?.('Failed'); }
  worldBreakers();
}
async function brkAutoSave() {
  const body = {
    interval_min: parseInt(document.getElementById('brk-auto-int')?.value || '120', 10),
    active_start: parseInt(document.getElementById('brk-auto-s')?.value || '8', 10),
    active_end: parseInt(document.getElementById('brk-auto-e')?.value || '22', 10),
    govern_min: parseInt(document.getElementById('brk-auto-gov')?.value || '360', 10),
  };
  try { await api('/api/world/ops/auto-config', { method: 'POST', body: JSON.stringify(body) }); toast?.('Saved'); }
  catch (e) { toast?.('Failed'); }
  worldBreakers();
}
async function brkAutoKind(k) {
  try {
    const st = await api('/api/world/ops/auto-config');
    let ks = st.kinds || ['image'];
    ks = ks.includes(k) ? ks.filter(x => x !== k) : ks.concat(k);
    if (!ks.length) ks = ['image'];
    await api('/api/world/ops/auto-config', { method: 'POST', body: JSON.stringify({ kinds: ks }) });
  } catch (e) { toast?.('Failed'); }
  worldBreakers();
}
async function brkSellSave() {
  const interval = parseInt(document.getElementById('brk-sell-int')?.value || '180', 10);
  const channel = document.getElementById('brk-sell-chan')?.value || 'etsy';
  try {
    await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ world_sell_interval_min: String(interval), world_sell_channel: channel }) });
    toast?.('Saved');
  } catch (e) { toast?.('Failed'); }
  worldBreakers();
}

window.worldBreakers = worldBreakers;
window.brkMaster = brkMaster;
window.brkRunMode = brkRunMode;
window.brkSetMode = brkSetMode;
window.brkSetCap = brkSetCap;
window.brkToggleGate = brkToggleGate;
window.brkSystemToggle = brkSystemToggle;
window.brkAutoToggle = brkAutoToggle;
window.brkAutoSave = brkAutoSave;
window.brkAutoKind = brkAutoKind;
window.brkSellSave = brkSellSave;

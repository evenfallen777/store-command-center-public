/* ══ THE COMPANY — ✝️ Jesus (the owner's delegated operator seat) ══
   God Console tab over /api/world/jesus (app/world_jesus.py). Jesus is a
   toggle-gated stand-in for YOUR day-to-day operator role: when (and only
   when) you flip world_jesus_enabled ON, it performs the same routine actions
   you already perform — approve/reject pending prayers by your own bar
   (both leaders endorse + taste ≥ your minimum), and invoke capabilities
   whose gates you already enabled. It never enables anything itself, and the
   hard floor is unchanged: real money, code changes and the illegal-content
   floor stay human/refused, for Jesus exactly as for everyone.

   Uses _worldModal (world-economy.js), _pill (world-control.js),
   api()/esc()/toast()/hlp() (app-core.js). Global-scope classic script. */

async function worldJesus() {
  let d;
  try { d = await api('/api/world/jesus'); }
  catch (e) { toast?.('Jesus panel failed to load'); return; }
  _renderJesus(d);
}

function _jesusAgo(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function _jesusLedgerRow(r) {
  const dcol = { approve: '#6ee7a8', invoke: '#22d3ee', reject: '#f87171', refused: '#fbbf24' }[r.decision] || '#c7d2e5';
  const icon = { prayer: '🙏', capability: '⚡', refused: '🚫' }[r.action] || '·';
  return `<div style="display:flex;gap:8px;align-items:baseline;font-size:.72rem;padding:3px 0;border-top:1px solid #131c30">
    <span style="width:18px">${icon}</span>
    <span style="color:${dcol};width:58px">${esc(r.decision || '')}</span>
    <span style="color:#e8eefc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">${esc(r.target || r.kind || '')}</span>
    <span style="color:#7a86a0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.detail || '')}</span>
    <span style="color:#54607a;margin-left:auto;white-space:nowrap" title="${esc(r.created_at || '')}">${_jesusAgo(r.ts)}</span>
  </div>`;
}

function _jesusShadowRow(r) {
  const dcol = { 'would-approve': '#6ee7a8', 'would-invoke': '#22d3ee', 'would-reject': '#f87171' }[r.decision] || '#c7d2e5';
  const icon = { prayer: '🙏', capability: '⚡' }[r.action] || '·';
  return `<div style="display:flex;gap:8px;align-items:baseline;font-size:.72rem;padding:3px 0;border-top:1px solid #131c30">
    <span style="width:18px">${icon}</span>
    <span style="color:${dcol};width:92px">${esc(r.decision || '')}</span>
    <span style="color:#e8eefc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">${esc(r.target || r.kind || '')}</span>
    <span style="color:#7a86a0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.detail || '')}</span>
    <span style="color:#54607a;margin-left:auto;white-space:nowrap" title="${esc(r.created_at || '')}">${_jesusAgo(r.ts)}</span>
  </div>`;
}

function _renderJesus(d) {
  // display labels only (world-theme.js): themed default = today's exact strings
  const _t = (a, b) => (window.WTheme ? WTheme.pick(a, b) : a);
  const on = !!d.enabled;
  const next = on && d.next_due_sec ? `next action in ~${Math.ceil(d.next_due_sec / 60)} min` : (on ? 'due now (rides the world_auto tick)' : 'inert while off');
  const pc = d.publish_caps || {};
  const sh = d.shadow || {};
  const st = sh.stats || {};
  const html = `
    <div style="font-size:.7rem;color:#8a97ad;margin-bottom:10px">
      Hand your own operator seat to an AI teammate. Pure <b>delegation</b> of what you already do — Jesus sits at your tier in the org
      (see 🕸️ Loops), presses only buttons you already turned on, and makes only the routine prayer calls you would make.
      It introduces no new privilege and cannot change any gate or floor.</div>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;
        background:${on ? '#12251b' : '#1a1414'};border:1px solid ${on ? '#2a5a3a' : '#5a2a2a'};cursor:pointer" onclick="jesusToggle(${on ? 'false' : 'true'})">
        <div>
          <div style="font-weight:700;color:#e8eefc;font-size:.85rem">${_t('✝️ Jesus takes the wheel', '🔺 The Constructive Lieutenant takes the wheel')} <span style="font-size:.66rem;color:${on ? '#6ee7a8' : '#7a86a0'}">· ${on ? 'ON' : 'OFF (default)'}</span></div>
          <div style="font-size:.66rem;color:#8a97ad">One toggle (world_jesus_enabled). Off ⇒ completely inert — even the "act now" button refuses.
          ${hlp('When ON: one delegated action per interval, riding the existing world_auto daemon within the same active hours. (a) Invokes ONLY capabilities whose agent-gates you already enabled — via the exact same gating as agents (catalog master + per-cap gate), so it can never turn anything on. (b) Approves prayers when Boss Kane AND Mayor Vex both endorse and taste clears your minimum; rejects when both blocked; split calls wait for you. Verdicts are stamped "auto (Jesus)" so only YOUR verdicts train the taste model.')}</div>
        </div>${_pill(on, false)}
      </div>
      <div style="padding:10px 12px;border-radius:10px;background:#0e1626;border:1px solid #26324a">
        <div style="font-weight:700;color:#e8eefc;font-size:.85rem">🕐 Cadence</div>
        <div style="font-size:.66rem;color:#8a97ad;margin-top:3px">At most one delegated action every
          <input id="jesus-int" type="number" min="5" value="${d.interval_min || 30}"
            style="width:56px;padding:1px 4px;background:#0b1120;border:1px solid #26324a;border-radius:5px;color:#e8eefc"> min
          <button class="btn" style="padding:1px 7px;font-size:.64rem" onclick="jesusSaveInterval()">💾</button></div>
        <div style="font-size:.66rem;color:#8ecaff;margin-top:4px">last action: ${_jesusAgo(d.last)} · ${esc(next)}</div>
        <button class="btn" style="padding:2px 10px;font-size:.68rem;margin-top:6px" ${on ? '' : 'disabled title="enable the toggle first"'}
          onclick="jesusTickNow()">⚡ Act now (one action)</button>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;
        background:${pc.enabled ? '#12251b' : '#0e1626'};border:1px solid ${pc.enabled ? '#2a5a3a' : '#26324a'};cursor:pointer"
        onclick="jesusPublishCaps(${pc.enabled ? 'false' : 'true'})">
        <div>
          <div style="font-weight:700;color:#e8eefc;font-size:.85rem">📣 Publishing-prayer buttons <span style="font-size:.66rem;color:${pc.enabled ? '#6ee7a8' : '#7a86a0'}">· ${pc.enabled ? 'ON' : 'off (default)'}</span></div>
          <div style="font-size:.66rem;color:#8a97ad">Also let Jesus press ${esc((pc.caps || []).join(' · ') || 'the free publish caps')} — each only FILES a still-gated prayer.
          ${hlp('Extends the delegated pool with the FREE publishing caps (social publish + Cults3D). Each is still refused unless its own agent-gate pill is on (same gating as agents: catalog master + per-cap gate), the filed prayer still needs its normal blessing, and paid (Etsy/Printify) or human-only kinds can never enter this pool. Currently eligible: ' + ((pc.eligible_now || []).join(', ') || 'none — turn a cap gate on in ⚡ Capabilities first') + '.')}</div>
        </div>${_pill(!!pc.enabled, false)}
      </div>
    </div>

    <div style="padding:9px 12px;border-radius:10px;background:#171126;border:1px solid #3a2a5a;margin-bottom:12px">
      <div style="font-size:.72rem;font-weight:700;color:#c4b5fd">🛡️ The hard floor is unchanged — and applies to Jesus too</div>
      <div style="font-size:.66rem;color:#8a97ad;margin-top:3px">
        Real-money transfers/withdrawals/spend always need <b>you</b> (Jesus refuses every ${esc((d.floor?.human_only_kinds || []).join(', '))} prayer
        and anything that costs cents). Code-change prayers stay yours — approved code could alter the floor itself.
        The illegal-content floor (minors/CSAM + real-person deepfakes) is refused at the endpoints for every actor, Jesus included.
        Jesus cannot flip a gate, raise a cap, or exempt itself.</div>
    </div>

    <div style="padding:9px 12px;border-radius:10px;background:#0e1626;border:1px solid ${sh.enabled ? '#2a5a3a' : '#26324a'};margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;cursor:pointer" onclick="jesusShadow(${sh.enabled ? 'false' : 'true'})">
        <div>
          <div style="font-size:.78rem;font-weight:700;color:#e8eefc">👁️ Shadow — what Jesus <i>would</i> do <span style="font-size:.66rem;color:${sh.enabled ? '#6ee7a8' : '#7a86a0'}">· ${sh.enabled ? 'ON' : 'off (default)'}</span></div>
          <div style="font-size:.66rem;color:#8a97ad">While the main toggle is OFF, record its would-be verdicts/invocations — observe-only, it never acts.
          ${hlp('Runs only while Jesus is DISABLED. Uses the exact same routine bar and cap pools as the real tick, but takes no action: every would-approve / would-reject / would-invoke lands in this read-only stream. The agreement score compares its would-verdicts with YOUR eventual real verdicts on the same prayers — a dry-run of the delegation before you ever hand over the seat.')}</div>
        </div>${_pill(!!sh.enabled, false)}
      </div>
      ${st.evaluated ? `<div style="font-size:.68rem;color:#8ecaff;margin-top:5px">🎯 Agreement with your real verdicts:
        <b style="color:${st.agreement_pct == null ? '#7a86a0' : st.agreement_pct >= 80 ? '#6ee7a8' : st.agreement_pct >= 50 ? '#fbbf24' : '#f87171'}">${st.agreement_pct == null ? 'no overlap yet' : st.agreement_pct + '%'}</b>
        <span style="color:#54607a">· ${st.evaluated} shadow verdict(s), ${st.human_resolved} since resolved by you, ${st.agreed} matched</span></div>` : ''}
      ${(sh.stream || []).length ? `<div style="background:#0b1120;border:1px solid #1b2740;border-radius:8px;padding:2px 10px;margin-top:6px;max-height:180px;overflow:auto">
        ${(sh.stream || []).map(_jesusShadowRow).join('')}</div>`
        : `<div style="font-size:.66rem;color:#54607a;margin-top:5px">${sh.enabled ? 'Nothing shadow-judged yet — it records on the same cadence as the real tick.' : 'Turn it on to preview the delegation without delegating anything.'}</div>`}
    </div>

    <div style="font-weight:700;color:#e8eefc;margin:10px 0 4px">📜 Ledger <span style="font-size:.66rem;color:#7a86a0;font-weight:400">— every action/decision it took</span>
      <button class="btn" style="padding:1px 8px;font-size:.64rem;margin-left:6px" onclick="worldJesus()">↻</button></div>
    <div style="background:#0b1120;border:1px solid #1b2740;border-radius:8px;padding:4px 10px;max-height:320px;overflow:auto">
      ${(d.ledger || []).map(_jesusLedgerRow).join('') || '<div style="font-size:.72rem;color:#54607a;padding:6px 0">No delegated actions yet.</div>'}
    </div>`;
  _worldModal(_t('✝️ Jesus — delegated operator (your seat, gated)',
                 '🔺 Constructive Lieutenant — delegated operator (your seat, gated)'), html);
}

async function jesusToggle(on) {
  try { _renderJesus(await api('/api/world/jesus/config', { method: 'POST', body: JSON.stringify({ enabled: on }) })); }
  catch (e) { toast?.(e?.message || 'Failed'); }
}

async function jesusPublishCaps(on) {
  try { _renderJesus(await api('/api/world/jesus/config', { method: 'POST', body: JSON.stringify({ publish_caps: on }) })); }
  catch (e) { toast?.(e?.message || 'Failed'); }
}

async function jesusShadow(on) {
  try { _renderJesus(await api('/api/world/jesus/config', { method: 'POST', body: JSON.stringify({ shadow: on }) })); }
  catch (e) { toast?.(e?.message || 'Failed'); }
}

async function jesusSaveInterval() {
  const v = parseInt(document.getElementById('jesus-int')?.value || '30', 10);
  try { _renderJesus(await api('/api/world/jesus/config', { method: 'POST', body: JSON.stringify({ interval_min: v }) })); toast?.('Saved'); }
  catch (e) { toast?.('Failed'); }
}

async function jesusTickNow() {
  try {
    const r = await api('/api/world/jesus/tick-now', { method: 'POST', body: '{}' });
    const ji = window.WTheme ? WTheme.icon('jesus') : '✝️';
    toast?.(r.acted ? `${ji} ${r.did?.action === 'prayer' ? `${r.did.decision}d: ${r.did.title || ''}` : `invoked ${r.did?.label || 'a capability'}`}` : `${ji} Nothing routine to act on right now`);
  } catch (e) { toast?.(e?.message || 'Refused'); }
  setTimeout(worldJesus, 700);
}

window.worldJesus = worldJesus;
window.jesusToggle = jesusToggle;
window.jesusPublishCaps = jesusPublishCaps;
window.jesusShadow = jesusShadow;
window.jesusSaveInterval = jesusSaveInterval;
window.jesusTickNow = jesusTickNow;

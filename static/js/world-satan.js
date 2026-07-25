/* ══ THE COMPANY — 😈 Satan (the owner's adversarial red-team lieutenant) ══
   God Console tab over /api/world/satan (app/world_satan.py) — the mirror of
   the ✝️ Jesus tab. Satan is a toggle-gated WORST-CASE lieutenant: when (and
   only when) you flip world_satan_enabled ON, he performs the adversarial
   half of your routine — reject the prayers both leaders blocked, flag the
   risks on everything else, invoke only capabilities whose gates you already
   enabled — and supplies the LOW side of every prediction band (world_duality)
   opposite Jesus's high side. He never approves anything, never moves money
   (ALWAYS_GATE + any real cents refused), and the minors/CSAM + real-person
   deepfake/NCII floors are ALWAYS-ON at the endpoints — not toggleable, not
   under Satan's (or anyone's) control.

   Uses _worldModal (world-economy.js), _pill (world-control.js),
   api()/esc()/toast()/hlp() (app-core.js). Global-scope classic script. */

async function worldSatan() {
  let d, lts = null;
  try { d = await api('/api/world/satan'); }
  catch (e) { toast?.('Satan panel failed to load'); return; }
  try { lts = await api('/api/world/lieutenants'); } catch (e) { /* optional */ }
  _renderSatan(d, lts);
}

function _satanAgo(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function _satanLedgerRow(r) {
  const dcol = { reject: '#f87171', flag: '#fbbf24', invoke: '#22d3ee', review: '#c4b5fd', refused: '#fbbf24' }[r.decision] || '#c7d2e5';
  const icon = { prayer: '🙏', capability: '⚡', risk: '⚠️', nsfw_review: '🔞', refused: '🚫' }[r.action] || '·';
  return `<div style="display:flex;gap:8px;align-items:baseline;font-size:.72rem;padding:3px 0;border-top:1px solid #2a1420">
    <span style="width:18px">${icon}</span>
    <span style="color:${dcol};width:58px">${esc(r.decision || '')}</span>
    <span style="color:#e8eefc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">${esc(r.target || r.kind || '')}</span>
    <span style="color:#a08a94;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.detail || '')}</span>
    <span style="color:#6a5460;margin-left:auto;white-space:nowrap" title="${esc(r.created_at || '')}">${_satanAgo(r.ts)}</span>
  </div>`;
}

function _satanShadowRow(r) {
  const dcol = { 'would-reject': '#f87171', 'would-flag': '#fbbf24', 'would-invoke': '#22d3ee' }[r.decision] || '#c7d2e5';
  const icon = { prayer: '🙏', risk: '⚠️', capability: '⚡' }[r.action] || '·';
  return `<div style="display:flex;gap:8px;align-items:baseline;font-size:.72rem;padding:3px 0;border-top:1px solid #2a1420">
    <span style="width:18px">${icon}</span>
    <span style="color:${dcol};width:92px">${esc(r.decision || '')}</span>
    <span style="color:#e8eefc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">${esc(r.target || r.kind || '')}</span>
    <span style="color:#a08a94;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.detail || '')}</span>
    <span style="color:#6a5460;margin-left:auto;white-space:nowrap" title="${esc(r.created_at || '')}">${_satanAgo(r.ts)}</span>
  </div>`;
}

/* the two-lieutenant owner digest: ✝️ what's happening · 😈 what could go wrong */
function _satanLieutenantsHtml(lts) {
  if (!lts) return '';
  // display labels only (world-theme.js): themed default = today's exact strings
  const _t = (a, b) => (window.WTheme ? WTheme.pick(a, b) : a);
  const j = lts.jesus || {}, s = lts.satan || {};
  const seat = on => on ? '<span style="color:#6ee7a8;font-size:.62rem">● ON</span>'
                        : '<span style="color:#7a86a0;font-size:.62rem">○ off — reporting only, inert</span>';
  const li = (arr, col) => (arr || []).map(t => `<div style="font-size:.68rem;color:${col};padding:2px 0">• ${esc(t)}</div>`).join('')
    || '<div style="font-size:.66rem;color:#54607a">nothing to report</div>';
  return `
    <div style="font-weight:700;color:#e8eefc;margin:10px 0 4px">👑 Your two lieutenants
      <span style="font-size:.66rem;color:#7a86a0;font-weight:400">— every call now has a best case AND a worst case (the hi-lo band); you sit in the middle and decide</span></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-bottom:12px">
      <div style="padding:10px 12px;border-radius:10px;background:#101a2e;border:1px solid #2a3a5a">
        <div style="font-weight:700;color:#e8eefc;font-size:.8rem">${_t("✝️ Jesus — what's happening / what I'd do", "🔺 Constructive Lieutenant — what's happening / what I'd do")} ${seat(j.enabled)}</div>
        ${li(j.summary, '#c7d2e5')}
        ${(j.ledger || []).length ? `<div style="font-size:.62rem;color:#54607a;margin-top:4px">recent: ${(j.ledger || []).slice(0, 3).map(r => esc(`${r.decision} ${r.target || r.kind || ''}`)).join(' · ')}</div>` : ''}
      </div>
      <div style="padding:10px 12px;border-radius:10px;background:#1c0f16;border:1px solid #5a2a3a">
        <div style="font-weight:700;color:#e8eefc;font-size:.8rem">${_t('😈 Satan — what could go wrong', '🔻 Adversarial Lieutenant — what could go wrong')} ${seat(s.enabled)}</div>
        ${li(s.risks, '#f0c0c8')}
        ${(s.ledger || []).length ? `<div style="font-size:.62rem;color:#6a5460;margin-top:4px">recent: ${(s.ledger || []).slice(0, 3).map(r => esc(`${r.decision} ${r.target || r.kind || ''}`)).join(' · ')}</div>` : ''}
      </div>
    </div>`;
}

function _renderSatan(d, lts) {
  // display labels only (world-theme.js): themed default = today's exact strings
  const _t = (a, b) => (window.WTheme ? WTheme.pick(a, b) : a);
  const on = !!d.enabled;
  const next = on && d.next_due_sec ? `next action in ~${Math.ceil(d.next_due_sec / 60)} min` : (on ? 'due now (rides the world_auto tick)' : 'inert while off');
  const nd = d.nsfw_domain || {};
  const sh = d.shadow || {};
  const st = sh.stats || {};
  const html = `
    <div style="font-size:.7rem;color:#a08a94;margin-bottom:10px">
      The devil on your other shoulder. Satan is ✝️ Jesus's <b>mirror</b> — same tier, same gates, the OPPOSITE polarity:
      where Jesus argues the best case, Satan argues the <b>worst</b> case, so every prediction/review/forecast carries a
      hi-lo band with you in the middle. He is the <b>risk predictor</b>: routine rejects, risk flags and red-team reviews only.
      He introduces no new privilege, cannot change any gate or floor, never approves anything, and never touches money.</div>

    ${_satanLieutenantsHtml(lts)}

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;
        background:${on ? '#2a1214' : '#1a1414'};border:1px solid ${on ? '#7f1d1d' : '#5a2a2a'};cursor:pointer" onclick="satanToggle(${on ? 'false' : 'true'})">
        <div>
          <div style="font-weight:700;color:#e8eefc;font-size:.85rem">${_t('😈 Satan takes the low road', '🔻 The Adversarial Lieutenant takes the low road')} <span style="font-size:.66rem;color:${on ? '#f87171' : '#7a86a0'}">· ${on ? 'ON' : 'OFF (default)'}</span></div>
          <div style="font-size:.66rem;color:#a08a94">One toggle (world_satan_enabled). Off ⇒ completely inert — even the "act now" button refuses.
          ${hlp('When ON: one adversarial action per interval, riding the existing world_auto daemon within the same active hours. (a) REJECTS prayers both Boss Kane AND Mayor Vex blocked (the same reject half of your routine bar Jesus uses — Satan NEVER approves anything). (b) FLAGS risks (read-only ledger notes) on prayers with real downside: cost, irreversibility, blocked endorsements, low taste. (c) Invokes ONLY capabilities whose agent-gates you already enabled — via the exact same gating as agents (catalog master + per-cap gate), so he can never turn anything on. Verdicts are stamped "auto (Satan)" so only YOUR verdicts train the taste model.')}</div>
        </div>${_pill(on, false)}
      </div>
      <div style="padding:10px 12px;border-radius:10px;background:#170e12;border:1px solid #3a2430">
        <div style="font-weight:700;color:#e8eefc;font-size:.85rem">🕐 Cadence</div>
        <div style="font-size:.66rem;color:#a08a94;margin-top:3px">At most one adversarial action every
          <input id="satan-int" type="number" min="5" value="${d.interval_min || 30}"
            style="width:56px;padding:1px 4px;background:#120a0e;border:1px solid #3a2430;border-radius:5px;color:#e8eefc"> min
          <button class="btn" style="padding:1px 7px;font-size:.64rem" onclick="satanSaveInterval()">💾</button></div>
        <div style="font-size:.66rem;color:#f0a8b8;margin-top:4px">last action: ${_satanAgo(d.last)} · ${esc(next)}</div>
        <button class="btn" style="padding:2px 10px;font-size:.68rem;margin-top:6px" ${on ? '' : 'disabled title="enable the toggle first"'}
          onclick="satanTickNow()">⚡ Act now (one action)</button>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;
        background:${nd.enabled ? '#2a1214' : '#170e12'};border:1px solid ${nd.enabled ? '#7f1d1d' : '#3a2430'};cursor:pointer"
        onclick="satanNsfwDomain(${nd.enabled ? 'false' : 'true'})">
        <div>
          <div style="font-weight:700;color:#e8eefc;font-size:.85rem">🔞 NSFW oversight domain <span style="font-size:.66rem;color:${nd.enabled ? '#f87171' : '#7a86a0'}">· ${nd.enabled ? 'ON' : 'off (default)'}</span></div>
          <div style="font-size:.66rem;color:#a08a94">When the (separate) NSFW mode is on AND this gate is on, private-studio <b>review/oversight</b> becomes Satan's domain — review only.
          ${hlp('Review-only oversight: Satan audits the recent private-studio output (counts, failures, rejections) into his ledger. He never generates, never approves, never touches files. It takes effect ONLY while the layered NSFW mode itself is active' + (nd.nsfw_world_active ? ' (currently active)' : ' (currently NOT active)') + '. Default OFF: Satan is the risk predictor, not an NSFW operator.')}</div>
          <div style="font-size:.64rem;color:#fbbf24;margin-top:4px">🛡️ NOT part of this gate: the minors/CSAM and real-person deepfake/NCII detection floors are <b>ALWAYS-ON</b> at the generation endpoints, are <b>not toggleable</b> by anyone — Satan included — and stay enforced no matter how this switch is set.</div>
        </div>${_pill(!!nd.enabled, false)}
      </div>
    </div>

    <div style="padding:9px 12px;border-radius:10px;background:#171126;border:1px solid #3a2a5a;margin-bottom:12px">
      <div style="font-size:.72rem;font-weight:700;color:#c4b5fd">🛡️ The hard floors are unchanged — and apply to Satan too</div>
      <div style="font-size:.66rem;color:#a08a94;margin-top:3px">
        Satan may <b>predict/red-team</b> a money action but can never execute one: every ${esc((d.floor?.human_only_kinds || []).join(', '))} prayer
        and anything that costs cents is refused, and he has <b>no approve path at all</b> — only you (or, when delegated, Jesus) can bless anything.
        Code-change prayers stay yours. The illegal-content floors (minors/CSAM + real-person deepfake/NCII) are always-on at the endpoints
        for every actor, are not toggleable, and are not under Satan's control. Satan cannot flip a gate, raise a cap, or exempt himself.</div>
    </div>

    <div style="padding:9px 12px;border-radius:10px;background:#170e12;border:1px solid ${sh.enabled ? '#7f1d1d' : '#3a2430'};margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;cursor:pointer" onclick="satanShadow(${sh.enabled ? 'false' : 'true'})">
        <div>
          <div style="font-size:.78rem;font-weight:700;color:#e8eefc">👁️ Shadow — what Satan <i>would</i> do <span style="font-size:.66rem;color:${sh.enabled ? '#f87171' : '#7a86a0'}">· ${sh.enabled ? 'ON' : 'off (default)'}</span></div>
          <div style="font-size:.66rem;color:#a08a94">While the main toggle is OFF, record his would-be rejects/flags/invocations — observe-only, he never acts.
          ${hlp('Runs only while Satan is DISABLED. Uses the exact same adversarial bar and cap pool as the real tick, but takes no action: every would-reject / would-flag / would-invoke lands in this read-only stream. The agreement score compares his would-rejects with YOUR eventual real verdicts on the same prayers — a dry-run of the red team before you ever switch it on.')}</div>
        </div>${_pill(!!sh.enabled, false)}
      </div>
      ${st.evaluated ? `<div style="font-size:.68rem;color:#f0a8b8;margin-top:5px">🎯 Agreement with your real verdicts:
        <b style="color:${st.agreement_pct == null ? '#7a86a0' : st.agreement_pct >= 80 ? '#6ee7a8' : st.agreement_pct >= 50 ? '#fbbf24' : '#f87171'}">${st.agreement_pct == null ? 'no overlap yet' : st.agreement_pct + '%'}</b>
        <span style="color:#6a5460">· ${st.evaluated} shadow verdict(s), ${st.human_resolved} since resolved by you, ${st.agreed} matched</span></div>` : ''}
      ${(sh.stream || []).length ? `<div style="background:#120a0e;border:1px solid #2a1420;border-radius:8px;padding:2px 10px;margin-top:6px;max-height:180px;overflow:auto">
        ${(sh.stream || []).map(_satanShadowRow).join('')}</div>`
        : `<div style="font-size:.66rem;color:#6a5460;margin-top:5px">${sh.enabled ? 'Nothing shadow-judged yet — it records on the same cadence as the real tick.' : 'Turn it on to preview the red team without arming it.'}</div>`}
    </div>

    <div style="font-weight:700;color:#e8eefc;margin:10px 0 4px">📜 Ledger <span style="font-size:.66rem;color:#7a86a0;font-weight:400">— every reject/flag/review/refusal, auditable</span>
      <button class="btn" style="padding:1px 8px;font-size:.64rem;margin-left:6px" onclick="worldSatan()">↻</button></div>
    <div style="background:#120a0e;border:1px solid #2a1420;border-radius:8px;padding:4px 10px;max-height:320px;overflow:auto">
      ${(d.ledger || []).map(_satanLedgerRow).join('') || '<div style="font-size:.72rem;color:#6a5460;padding:6px 0">No adversarial actions yet.</div>'}
    </div>`;
  _worldModal(_t('😈 Satan — adversarial red team (worst case, gated)',
                 '🔻 Adversarial Lieutenant — red team (worst case, gated)'), html);
}

async function _satanCfg(body) {
  try {
    const d = await api('/api/world/satan/config', { method: 'POST', body: JSON.stringify(body) });
    let lts = null;
    try { lts = await api('/api/world/lieutenants'); } catch (e) { /* optional */ }
    _renderSatan(d, lts);
  } catch (e) { toast?.(e?.message || 'Failed'); }
}

async function satanToggle(on) { await _satanCfg({ enabled: on }); }
async function satanNsfwDomain(on) { await _satanCfg({ nsfw_domain: on }); }
async function satanShadow(on) { await _satanCfg({ shadow: on }); }

async function satanSaveInterval() {
  const v = parseInt(document.getElementById('satan-int')?.value || '30', 10);
  await _satanCfg({ interval_min: v });
  toast?.('Saved');
}

async function satanTickNow() {
  try {
    const r = await api('/api/world/satan/tick-now', { method: 'POST', body: '{}' });
    const si = window.WTheme ? WTheme.icon('satan') : '😈';
    toast?.(r.acted
      ? `${si} ${r.did?.action === 'prayer' ? `rejected: ${r.did.title || ''}`
          : r.did?.action === 'risk' ? `flagged: ${r.did.title || ''}`
          : r.did?.action === 'nsfw_review' ? 'oversight review recorded'
          : `invoked ${r.did?.label || 'a capability'}`}`
      : `${si} Nothing to red-team right now`);
  } catch (e) { toast?.(e?.message || 'Refused'); }
  setTimeout(worldSatan, 700);
}

window.worldSatan = worldSatan;
window.satanToggle = satanToggle;
window.satanNsfwDomain = satanNsfwDomain;
window.satanShadow = satanShadow;
window.satanSaveInterval = satanSaveInterval;
window.satanTickNow = satanTickNow;

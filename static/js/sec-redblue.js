/* Security → 🔴🔵 Red/Blue: the security/engineering check-and-balance tier.
   🔴 Red-team = adversarial STATIC auditor (look-only) — finds + hands off.
   🔵 Blue-team = defender — files fixes through the gated dev swarm (human
      approval before live). 🔍 Auditor = findings lifecycle + verify.
   Reads /api/security/redblue/*. Uses api()/esc()/hlp()/toast() (app-core.js).
   Both roles default OFF and are inert while off. */

const _SEV_COL = { critical: '#f43f5e', high: '#fb7185', medium: '#fbbf24', low: '#a3e635', info: '#7a86a0' };
const _RB_COLS = ['open', 'triaged', 'fixing', 'fixed', 'verified'];
const _RB_COL_LABEL = { open: '🔴 Open', triaged: '🟠 Triaged', fixing: '🔵 Fixing', fixed: '🟢 Fixed', verified: '🔍 Verified' };

async function secRedBlue() {
  const el = document.getElementById('sec-body');
  el.innerHTML = `<div class="empty">Loading red/blue board…</div>`;
  let d;
  try { d = await api('/api/security/redblue/status'); }
  catch (e) { el.innerHTML = `<div class="empty">Red/Blue panel failed to load.</div>`; return; }
  _renderRedBlue(el, d);
}
window.secRedBlue = secRedBlue;

function _rbAgo(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function _rbToggleCard(role, on, extra) {
  const red = role === 'red';
  const bg = on ? (red ? '#2a1214' : '#0f1e2e') : '#161a22';
  const bd = on ? (red ? '#7f1d1d' : '#1d4e7f') : (red ? '#4a2430' : '#24384a');
  const dot = on ? (red ? '#f87171' : '#38bdf8') : '#7a86a0';
  return `<div style="padding:10px 12px;border-radius:10px;background:${bg};border:1px solid ${bd};min-width:250px;flex:1">
    <div style="display:flex;justify-content:space-between;align-items:center;cursor:pointer" onclick="rbToggle('${role}',${on ? 'false' : 'true'})">
      <div style="font-weight:700;color:#e8eefc;font-size:.85rem">${red
        ? (window.WTheme ? WTheme.pick('🔴 Red-team auditor', '🔴 Red Team (audit)') : '🔴 Red-team auditor')
        : (window.WTheme ? WTheme.pick('🔵 Blue-team fixer', '🔵 Blue Team (fix)') : '🔵 Blue-team fixer')}
        <span style="font-size:.66rem;color:${dot}">· ${on ? 'ON' : 'OFF (default)'}</span></div>
      <div style="width:34px;height:18px;border-radius:10px;background:${on ? dot : '#3a4252'};position:relative">
        <div style="position:absolute;top:2px;${on ? 'right:2px' : 'left:2px'};width:14px;height:14px;border-radius:50%;background:#0b0e14"></div></div>
    </div>
    <div style="font-size:.66rem;color:#8a97ad;margin-top:4px">${extra}</div>
  </div>`;
}

function _rbFindingCard(f) {
  const col = _SEV_COL[f.severity] || '#7a86a0';
  const btns = [];
  if (f.status === 'open') btns.push(`<button class="btn-sm" onclick="rbTriage(${f.id})" title="Mark ready for blue-team">🟠 Triage</button>`);
  if (f.status === 'triaged') btns.push(`<button class="btn-sm" onclick="rbFix(${f.id})" title="Blue-team files a fix through the gated dev swarm (human approval before live)">🔵 File fix</button>`);
  if (f.status === 'fixed') btns.push(`<button class="btn-sm" onclick="rbVerify(${f.id})" title="Auditor re-reads the location (read-only) and confirms the fix closed it">🔍 Verify</button>`);
  if (f.status === 'fixing' && f.swarm_job_id) btns.push(`<span style="font-size:.64rem;color:#38bdf8">swarm job #${f.swarm_job_id} building…</span>`);
  if (!['verified', 'dismissed'].includes(f.status)) btns.push(`<button class="btn-sm" onclick="rbDismiss(${f.id})" title="Dismiss (false positive / accepted risk)">✕</button>`);
  btns.push(`<button class="btn-sm" onclick="rbTrail(${f.id})" title="Audit trail + Q&amp;A">🧾</button>`);
  btns.push(`<button class="btn-sm" onclick="rbAsk(${f.id})" title="Ask blue-team a question">💬</button>`);
  return `<div style="border:1px solid #24303f;border-left:3px solid ${col};border-radius:8px;padding:7px 9px;margin-bottom:6px;background:#12161d">
    <div style="display:flex;justify-content:space-between;gap:8px;align-items:baseline">
      <span style="font-size:.74rem;font-weight:700;color:#e8eefc">${esc(f.title)}</span>
      <span style="font-size:.6rem;font-weight:800;color:${col};text-transform:uppercase">${esc(f.severity)}</span>
    </div>
    <div style="font-size:.66rem;color:#8a97ad;margin-top:2px">📁 ${esc(f.location || '')}${f.line ? ':' + f.line : ''} · <span style="color:#6a7688">${esc(f.category || '')}</span></div>
    ${f.snippet ? `<div style="font-size:.62rem;color:#7a8698;font-family:monospace;background:#0b0e14;border-radius:4px;padding:2px 6px;margin-top:3px;overflow-x:auto;white-space:nowrap">${esc(f.snippet)}</div>` : ''}
    <div style="font-size:.62rem;color:#6a7688;margin-top:3px">🔖 ${esc(f.reference || '')}</div>
    <div style="font-size:.64rem;color:#a3b1c6;margin-top:2px">🛠️ ${esc(f.recommendation || '')}</div>
    <div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:5px">${btns.join('')}</div>
  </div>`;
}

function _renderRedBlue(el, d) {
  const r = d.red || {}, b = d.blue || {};
  const sev = r.severity_open || {};
  const findings = r.findings || [];
  const byCol = {}; _RB_COLS.forEach(c => byCol[c] = []);
  findings.forEach(f => { if (byCol[f.status]) byCol[f.status].push(f); });
  const sevTiles = ['critical', 'high', 'medium', 'low', 'info'].map(s =>
    `<div style="text-align:center;padding:5px 10px;border-radius:8px;background:#12161d;border:1px solid #24303f">
      <div style="font-size:1.2rem;font-weight:900;color:${_SEV_COL[s]}">${sev[s] || 0}</div>
      <div style="font-size:.58rem;color:#7a86a0;text-transform:uppercase">${s}</div></div>`).join('');
  const targets = (r.targets || []).map(t =>
    `<div style="display:flex;align-items:center;gap:6px;font-size:.66rem;padding:2px 0">
      <span onclick="rbTgToggle(${t.id},${t.enabled ? 'false' : 'true'})" style="cursor:pointer;color:${t.enabled ? '#6ee7a8' : '#7a86a0'}">${t.enabled ? '☑' : '☐'}</span>
      <span style="color:#e8eefc">${esc(t.label || t.path)}</span>
      <span style="color:#6a7688;font-size:.6rem">${esc(t.kind)}</span>
      <span style="color:#54607a;font-size:.58rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">${esc(t.path)}</span>
      <span onclick="rbTgRemove(${t.id})" style="cursor:pointer;color:#f87171">✕</span></div>`).join('') || '<div style="font-size:.64rem;color:#6a7688">No targets — add one below.</div>';

  el.innerHTML = `
    <div style="font-size:.7rem;color:#8a97ad;margin-bottom:10px">
      The check-and-balance pattern one layer down. <b style="color:#f87171">🔴 Red-team</b> is an adversarial
      <b>static</b> auditor — <b>look-only</b>: it reads your own source + config and files findings with a
      CVE/OWASP/CWE reference; it never attacks. <b style="color:#38bdf8">🔵 Blue-team</b> fixes them, but ONLY by
      filing a job through the <b>gated dev swarm</b> (human approval before anything reaches live). 🔍 The auditor
      confirms a fix actually closed a finding. Both roles default OFF and are inert while off.</div>

    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">
      ${_rbToggleCard('red', !!r.enabled,
        `Read-only static audit every
         <input id="rb-red-int" type="number" min="1" value="${r.interval_hours || 12}" style="width:44px;padding:0 3px;background:#0b0e14;border:1px solid #3a2430;border-radius:4px;color:#e8eefc">h ·
         <label style="cursor:pointer">GPU-queue summary <input type="checkbox" ${r.llm_assist ? 'checked' : ''} onclick="rbLlm(this.checked)"></label>
         <button class="btn-sm" onclick="rbSaveInt('red')">💾</button>
         <button class="btn-sm" onclick="rbRunAudit()" title="Run a read-only static audit now (refused while red-team is off)">▶ Run audit</button>
         <div style="margin-top:3px">last: ${_rbAgo(r.last_audit)}${r.last_summary ? ' · <span style="color:#fbbf24">' + esc(r.last_summary) + '</span>' : ''}</div>`)}
      ${_rbToggleCard('blue', !!b.enabled,
        `Files fixes via the gated dev swarm every
         <input id="rb-blue-int" type="number" min="1" value="${b.interval_hours || 6}" style="width:44px;padding:0 3px;background:#0b0e14;border:1px solid #1d4e7f;border-radius:4px;color:#e8eefc">h
         <button class="btn-sm" onclick="rbSaveInt('blue')">💾</button>
         <button class="btn-sm" onclick="rbBlueRun()" title="File swarm fixes for triaged findings now (refused while blue-team is off)">▶ Run blue pass</button>
         <div style="margin-top:3px">${b.triaged_waiting || 0} triaged waiting · ${(b.in_fix || []).length} in fix · ${r.verified_total || 0} verified</div>`)}
    </div>

    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">${sevTiles}</div>

    <div style="padding:9px 12px;border-radius:10px;background:#0f1520;border:1px solid #24303f;margin-bottom:12px">
      <div style="font-size:.72rem;font-weight:700;color:#a3b1c6">🎯 Audit targets ${window.hlp ? hlp('Read-only paths the static auditor walks. Defaults to the store app/ and static/. Add the WordPress path (e.g. ~/Docker/Openclaw_Wordpress) or any other local path you own. The auditor only reads files — it never modifies or executes them.') : ''}</div>
      <div style="margin-top:5px">${targets}</div>
      <div style="display:flex;gap:5px;margin-top:6px">
        <input id="rb-tg-path" placeholder="/absolute/path (e.g. ~/Docker/Openclaw_Wordpress)" style="flex:1;padding:3px 6px;background:#0b0e14;border:1px solid #24303f;border-radius:5px;color:#e8eefc;font-size:.66rem">
        <select id="rb-tg-kind" style="padding:3px;background:#0b0e14;border:1px solid #24303f;border-radius:5px;color:#e8eefc;font-size:.64rem">
          <option value="wordpress">wordpress</option><option value="frontend">frontend</option>
          <option value="system">system</option><option value="store">store</option><option value="other">other</option></select>
        <button class="btn-sm" onclick="rbTgAdd()">+ Add</button>
      </div>
    </div>

    <div style="padding:9px 12px;border-radius:10px;background:#0f1a14;border:1px solid #1d5a3a;margin-bottom:12px;font-size:.64rem;color:#8fbfa0">
      🛡️ <b>Safety:</b> ${esc((r.safety || {}).note || 'Red-team is static read-only; blue-team fixes route only through the gated dev swarm.')}</div>

    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px">
      ${_RB_COLS.map(c => `<div>
        <div style="font-size:.72rem;font-weight:700;color:#c7d2e5;margin-bottom:5px;border-bottom:1px solid #24303f;padding-bottom:3px">${_RB_COL_LABEL[c]} <span style="color:#6a7688">${byCol[c].length}</span></div>
        ${byCol[c].map(_rbFindingCard).join('') || '<div style="font-size:.62rem;color:#54607a;padding:4px 0">—</div>'}
      </div>`).join('')}
    </div>`;
}

/* ── actions ─────────────────────────────────────────────────────────────── */
async function _rbCfg(role, body) {
  try { await api(`/api/security/redblue/${role}/config`, { method: 'POST', body: JSON.stringify(body) }); }
  catch (e) { toast?.(e?.message || 'Failed'); }
  secRedBlue();
}
async function rbToggle(role, on) { await _rbCfg(role, { enabled: on }); }
async function rbLlm(on) { await _rbCfg('red', { llm_assist: on }); }
async function rbSaveInt(role) {
  const v = parseInt(document.getElementById(`rb-${role}-int`)?.value || '12', 10);
  await _rbCfg(role, { interval_hours: v }); toast?.('Saved');
}

async function _rbPost(url, body) {
  try { return await api(url, { method: 'POST', body: JSON.stringify(body || {}) }); }
  catch (e) { toast?.(e?.message || 'Refused'); throw e; }
}

async function rbRunAudit() {
  try { const r = await _rbPost('/api/security/redblue/audit', {}); toast?.(`🔴 Audit: ${r.new_findings} new finding(s) across ${r.scanned_files} files`); }
  catch (e) { }
  secRedBlue();
}
async function rbBlueRun() {
  try { const r = await _rbPost('/api/security/redblue/blue/run', {}); toast?.(`🔵 Filed ${r.count} fix job(s)`); }
  catch (e) { }
  secRedBlue();
}
async function rbTriage(id) { try { await _rbPost(`/api/security/redblue/findings/${id}/triage`, {}); } catch (e) { } secRedBlue(); }
async function rbFix(id) { try { const r = await _rbPost(`/api/security/redblue/findings/${id}/fix`, {}); toast?.(`🔵 Fix job #${r.job_id} filed on '${r.branch}' (approve before live)`); } catch (e) { } secRedBlue(); }
async function rbVerify(id) {
  try { const r = await _rbPost(`/api/security/redblue/findings/${id}/verify`, {}); toast?.(r.verified === false ? `🔍 Not verified: ${r.reason}` : '🔍 Verified — fix confirmed'); }
  catch (e) { } secRedBlue();
}
async function rbDismiss(id) { try { await _rbPost(`/api/security/redblue/findings/${id}/dismiss`, {}); } catch (e) { } secRedBlue(); }

async function rbTgToggle(id, on) { try { await _rbPost(`/api/security/redblue/targets/${id}/toggle`, { enabled: on }); } catch (e) { } secRedBlue(); }
async function rbTgRemove(id) {
  try { await api(`/api/security/redblue/targets/${id}`, { method: 'DELETE' }); } catch (e) { toast?.('Failed'); }
  secRedBlue();
}
async function rbTgAdd() {
  const path = document.getElementById('rb-tg-path')?.value?.trim();
  const kind = document.getElementById('rb-tg-kind')?.value || 'other';
  if (!path) { toast?.('Enter a path'); return; }
  try { await _rbPost('/api/security/redblue/targets', { path, kind }); toast?.('Target added'); } catch (e) { }
  secRedBlue();
}

async function rbAsk(id) {
  const q = prompt('Ask blue-team about this finding:');
  if (!q) return;
  try { await _rbPost('/api/security/redblue/qa', { finding_id: id, question: q, asker: 'red' }); toast?.('Question posted'); } catch (e) { }
}

async function rbTrail(id) {
  let d; try { d = await api(`/api/security/redblue/findings/${id}/trail`); } catch (e) { toast?.('Failed'); return; }
  const trail = (d.trail || []).map(t =>
    `<div style="font-size:.64rem;color:#8a97ad;padding:2px 0;border-top:1px solid #1a2028"><span style="color:#c7d2e5">${esc(t.actor || '')}</span> ${esc(t.event)} <span style="color:#6a7688">${esc(t.detail || '')}</span> <span style="color:#54607a;float:right">${esc((t.created_at || '').replace('T', ' '))}</span></div>`).join('') || '<div style="font-size:.62rem;color:#6a7688">No trail.</div>';
  const qa = (d.qa || []).map(q =>
    `<div style="font-size:.66rem;padding:4px 0;border-top:1px solid #1a2028">
       <div style="color:#f0a8b8">🔴 ${esc(q.asker)}: ${esc(q.question)}</div>
       ${q.answer ? `<div style="color:#8fc0f0;margin-top:2px">🔵 ${esc(q.answered_by || 'blue')}: ${esc(q.answer)}</div>`
         : `<div style="margin-top:2px"><input id="rb-ans-${q.id}" placeholder="answer as blue…" style="width:70%;padding:2px 5px;background:#0b0e14;border:1px solid #24303f;border-radius:4px;color:#e8eefc;font-size:.64rem"><button class="btn-sm" onclick="rbAnswer(${q.id})">Answer</button><button class="btn-sm" onclick="rbEscalate(${q.id})" title="Escalate to owner">⬆</button></div>`}
     </div>`).join('') || '<div style="font-size:.62rem;color:#6a7688">No questions.</div>';
  const html = `<div style="font-weight:700;color:#e8eefc;margin-bottom:6px">🧾 Finding #${id} — audit trail</div>
    <div style="max-height:200px;overflow:auto;margin-bottom:10px">${trail}</div>
    <div style="font-weight:700;color:#e8eefc;margin-bottom:4px">💬 Red ↔ Blue Q&amp;A</div>
    <div style="max-height:200px;overflow:auto">${qa}</div>`;
  if (window._worldModal) _worldModal(`Finding #${id}`, html);
  else alert('Trail loaded (modal unavailable)');
}
async function rbAnswer(qid) {
  const a = document.getElementById(`rb-ans-${qid}`)?.value?.trim();
  if (!a) return;
  try { await _rbPost(`/api/security/redblue/qa/${qid}/answer`, { answer: a, answered_by: 'blue' }); toast?.('Answered'); } catch (e) { }
}
async function rbEscalate(qid) {
  try { await _rbPost(`/api/security/redblue/qa/${qid}/escalate`, {}); toast?.('Escalated to owner'); } catch (e) { }
}

window.rbToggle = rbToggle; window.rbLlm = rbLlm; window.rbSaveInt = rbSaveInt;
window.rbRunAudit = rbRunAudit; window.rbBlueRun = rbBlueRun;
window.rbTriage = rbTriage; window.rbFix = rbFix; window.rbVerify = rbVerify; window.rbDismiss = rbDismiss;
window.rbTgToggle = rbTgToggle; window.rbTgRemove = rbTgRemove; window.rbTgAdd = rbTgAdd;
window.rbAsk = rbAsk; window.rbTrail = rbTrail; window.rbAnswer = rbAnswer; window.rbEscalate = rbEscalate;

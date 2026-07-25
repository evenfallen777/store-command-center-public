/* ══ MONEY TAB ══
   Real-dollar mission control. Demand signals (shop searches) flow in via
   /api/money/signals; "Run Company Review" has the LLM compare them to the
   current catalog and propose money missions (product gaps, online income,
   carpentry leads). Missions follow an approve/reject/done queue, and an
   approved mission is announced into The Company world. */

let _moSignals = [];
let _moMissions = [];
let _moFilter = 'all';

const MO_KINDS = {
  product_gap:    { icon: '&#128161;', label: 'Product gap',    color: 'var(--accent)' },
  online_income:  { icon: '&#127760;', label: 'Online income',  color: 'var(--accent2)' },
  carpentry_lead: { icon: '&#128296;', label: 'Carpentry lead', color: 'var(--warn)' },
  other:          { icon: '&#10024;',  label: 'Other',          color: 'var(--muted)' },
};
function _moKind(k) { return MO_KINDS[k] || MO_KINDS.other; }
function _moUsd(cents) { return '$' + ((cents || 0) / 100).toLocaleString(undefined, { maximumFractionDigits: 0 }); }

async function renderMoney() {
  // Lives inside the 💰 Finance tab (fin-pane-money). If the pane is gone the
  // user switched views — bail instead of clobbering whatever is on screen now.
  const _moRoot = document.getElementById('fin-pane-money');
  if (!_moRoot) return;
  _moRoot.innerHTML = `
    <div class="view-header">
      <div class="view-title">&#128176; Money</div>
      <div class="view-sub">Real-dollar mission control &mdash; shop demand signals reviewed by the company
        into product gaps, online income ideas &amp; carpentry leads. Approve the ones worth chasing.</div>
    </div>
    <div class="stats-row" id="money-stats"></div>
    <div id="money-signals" style="margin-bottom:18px;"></div>
    <div id="money-cashapp" style="margin-bottom:18px;"></div>
    <div id="money-donate" style="margin-bottom:18px;"></div>
    <div id="money-add" style="margin-bottom:18px;"></div>
    <div id="money-missions"></div>`;
  await loadMoStats();
  await loadMoSignals();
  await loadMoCashApp();
  await loadMoDonate();
  renderMoAdd();
  await loadMoMissions();
}
window.renderMoney = renderMoney;

/* ── stats strip ──────────────────────────────────────────────────────────── */
async function loadMoStats() {
  const el = document.getElementById('money-stats');
  if (!el) return;
  let s = {};
  try { s = await api('/api/money/stats'); } catch { el.innerHTML = ''; return; }
  const m = s.missions || {};
  el.innerHTML = `
    <div class="stat-card"><div class="stat-label">Proposed missions</div>
      <div class="stat-val c-warn">${m.proposed || 0}</div></div>
    <div class="stat-card"><div class="stat-label">Approved</div>
      <div class="stat-val c-green">${m.approved || 0}</div></div>
    <div class="stat-card"><div class="stat-label">Est. pipeline</div>
      <div class="stat-val c-accent">${_moUsd(s.pipeline_value_cents)}</div></div>
    <div class="stat-card"><div class="stat-label">New signals</div>
      <div class="stat-val c-accent2">${(s.signals || {}).new || 0}</div></div>`;
}

/* ── demand signals ───────────────────────────────────────────────────────── */
async function loadMoSignals() {
  const el = document.getElementById('money-signals');
  if (!el) return;
  let data;
  try { data = await api('/api/money/signals?limit=50'); }
  catch (e) { el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  _moSignals = data.signals || [];
  const newCount = (data.counts || {}).new || 0;
  el.innerHTML = `
    <div class="card" style="padding:16px 18px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px;">
        <div style="font-weight:700;font-size:1rem;">&#128269; Demand Signals
          <span style="font-size:.72rem;color:var(--muted);font-weight:400;">(${newCount} new)</span>
          ${hlp('Shop search queries reported by the storefront (POST /api/money/signals with the X-Money-Token header). The company review compares NEW signals against the current catalog to find products we should carry but do not.')}</div>
        <button class="btn-sm primary" id="mo-review-btn" onclick="moRunReview()" ${newCount ? '' : 'disabled'}
          title="Send all NEW signals + the current catalog to the LLM once; it proposes money missions (demand gaps and other leads) and marks the signals reviewed.">&#9654; Run Company Review</button>
        <button class="btn-sm" id="mo-hunt-btn" onclick="moHuntLeads()"
          title="Sweep the local searxng meta-search for local carpentry work leads, screen them with the LLM, and propose carpentry_lead missions. Queries customizable via setting money_lead_queries.">&#128296; Hunt Carpentry Leads</button>
      </div>
      ${_moSignals.length ? `
      <div style="overflow-x:auto"><table style="width:100%;font-size:.78rem;border-collapse:collapse;">
        <thead><tr style="text-align:left;color:var(--muted);font-size:.68rem;text-transform:uppercase;letter-spacing:.04em;">
          <th style="padding:6px 8px;">Query</th><th style="padding:6px 8px;">Source</th>
          <th style="padding:6px 8px;">Results</th><th style="padding:6px 8px;">Status</th>
          <th style="padding:6px 8px;">When</th></tr></thead>
        <tbody>${_moSignals.map(s => `
          <tr style="border-top:1px solid var(--border,#3333);">
            <td style="padding:6px 8px;">${esc(s.query || '')}</td>
            <td style="padding:6px 8px;color:var(--muted);">${esc(s.source || '')}</td>
            <td style="padding:6px 8px;${(s.results_count || 0) === 0 ? 'color:var(--warn);font-weight:600;' : ''}">${s.results_count || 0}</td>
            <td style="padding:6px 8px;">${s.status === 'new'
              ? '<span style="color:var(--accent2);">new</span>'
              : `<span style="color:var(--muted);">${esc(s.status)}</span>`}</td>
            <td style="padding:6px 8px;color:var(--muted);white-space:nowrap;">${esc((s.created_at || '').slice(0, 16))}</td>
          </tr>`).join('')}
        </tbody></table></div>`
      : `<div style="color:var(--muted);font-size:.8rem;">No signals yet &mdash; the storefront reports searches here as shoppers look for things.</div>`}
    </div>`;
}

async function moRunReview() {
  const btn = document.getElementById('mo-review-btn');
  const orig = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '⏳ Reviewing…';
  try {
    const { task_id } = await api('/api/money/review', { method: 'POST', body: JSON.stringify({}) });
    const r = await pollTask(task_id, 120);
    toast(`💰 Review done — ${r && r.proposed != null ? r.proposed : '?'} mission(s) proposed`);
  } catch (e) { toast('Review failed: ' + e.message, 'error'); }
  btn.disabled = false; btn.innerHTML = orig;
  await loadMoStats(); await loadMoSignals(); await loadMoMissions();
}

async function moHuntLeads() {
  const btn = document.getElementById('mo-hunt-btn');
  const orig = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '⏳ Hunting…';
  try {
    const r0 = await api('/api/money/leads/hunt', { method: 'POST', body: JSON.stringify({}) });
    if (r0.task_id == null) {
      toast(`🔨 ${r0.note || 'No fresh leads found'} (${r0.results || 0} results)`);
    } else {
      const r = await pollTask(r0.task_id, 180);
      toast(`🔨 Lead hunt done — ${r && r.proposed != null ? r.proposed : '?'} lead(s) proposed from ${r0.fresh} fresh results`);
    }
  } catch (e) { toast('Lead hunt failed: ' + e.message, 'error'); }
  btn.disabled = false; btn.innerHTML = orig;
  await loadMoStats(); await loadMoMissions();
}

/* ── add mission (manual, e.g. a carpentry lead idea) ─────────────────────── */
function renderMoAdd() {
  const el = document.getElementById('money-add');
  if (!el) return;
  el.innerHTML = `
    <details class="settings-group">
      <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#10133; Add mission</summary>
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-top:12px;">
        <div class="field" style="margin:0;">
          <label style="font-size:.72rem;">Kind ${hlp('What kind of money-maker this is. carpentry_lead is for local Acme Carpentry work (your local area).')}</label>
          <select id="mo-add-kind">
            <option value="product_gap">&#128161; Product gap</option>
            <option value="online_income">&#127760; Online income</option>
            <option value="carpentry_lead">&#128296; Carpentry lead</option>
            <option value="other" selected>&#10024; Other</option>
          </select>
        </div>
        <div class="field" style="margin:0;flex:1;min-width:200px;">
          <label style="font-size:.72rem;">Title</label>
          <input type="text" id="mo-add-title" placeholder="e.g. Deck repair leads — post in your town FB groups">
        </div>
        <div class="field" style="margin:0;flex:2;min-width:240px;">
          <label style="font-size:.72rem;">Detail</label>
          <input type="text" id="mo-add-detail" placeholder="What to do &amp; why it will earn">
        </div>
        <div class="field" style="margin:0;width:110px;">
          <label style="font-size:.72rem;">Est. $ ${hlp('Rough dollar value of this mission if it lands. Feeds the pipeline total in the stats strip.')}</label>
          <input type="number" id="mo-add-value" min="0" step="1" placeholder="0">
        </div>
        <button class="btn-sm primary" onclick="moAddMission()">&#128190; Add</button>
      </div>
    </details>`;
}

async function moAddMission() {
  const title = document.getElementById('mo-add-title').value.trim();
  if (!title) { toast('Give the mission a title', 'error'); return; }
  const payload = {
    kind: document.getElementById('mo-add-kind').value,
    title,
    detail: document.getElementById('mo-add-detail').value.trim(),
    est_value_cents: Math.round((parseFloat(document.getElementById('mo-add-value').value) || 0) * 100),
  };
  try {
    await api('/api/money/missions', { method: 'POST', body: JSON.stringify(payload) });
    toast('✅ Mission added');
    ['mo-add-title', 'mo-add-detail', 'mo-add-value'].forEach(id => { const e = document.getElementById(id); if (e) e.value = ''; });
    await loadMoStats(); await loadMoMissions();
  } catch (e) { toast('Add failed: ' + e.message, 'error'); }
}

/* ── missions queue ───────────────────────────────────────────────────────── */
async function loadMoMissions() {
  const el = document.getElementById('money-missions');
  if (!el) return;
  let data;
  try { data = await api('/api/money/missions'); }
  catch (e) { el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  _moMissions = data.missions || [];
  const c = data.counts || {};
  const tabs = [['all', 'All', _moMissions.length], ['proposed', 'Proposed', c.proposed || 0],
                ['approved', 'Approved', c.approved || 0], ['done', 'Done', c.done || 0],
                ['rejected', 'Rejected', c.rejected || 0]];
  const shown = _moFilter === 'all' ? _moMissions : _moMissions.filter(m => m.status === _moFilter);
  el.innerHTML = `
    <div style="font-weight:700;font-size:1rem;margin-bottom:10px;">&#128176; Money Missions
      ${hlp('The queue of real-dollar leads. Approve a mission to greenlight it (a Company agent is assigned and it is announced in the town), mark it Done when it earned (record the result), or Reject it.')}</div>
    <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;">
      ${tabs.map(([k, l, n]) => `<button class="btn-sm ${k === _moFilter ? 'primary' : ''}" onclick="moSetFilter('${k}')">${l} (${n})</button>`).join('')}
    </div>
    ${shown.length ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;">${shown.map(moCardHtml).join('')}</div>`
      : `<div class="empty"><div class="empty-icon">&#128176;</div>No ${_moFilter === 'all' ? '' : _moFilter} missions yet &mdash; run a company review or add one above.</div>`}`;
}
function moSetFilter(f) { _moFilter = f; loadMoMissions(); }

function moCardHtml(m) {
  const k = _moKind(m.kind);
  const statusChip = {
    proposed: '<span style="color:var(--warn);">&#9203; proposed</span>',
    approved: '<span style="color:var(--green,#22c55e);">&#9989; approved</span>',
    rejected: '<span style="color:var(--red,#ef4444);">&#10060; rejected</span>',
    done:     '<span style="color:var(--accent);">&#127942; done</span>',
  }[m.status] || `<span style="color:var(--muted);">${esc(m.status)}</span>`;
  const btns =
    m.status === 'proposed' ? `
      <button class="btn-sm success" onclick="moApprove(${m.id})" title="Greenlight this mission — a Company agent is assigned and the town is told.">&#10003; Approve</button>
      <button class="btn-sm danger" onclick="moReject(${m.id})">&#10005; Reject</button>`
    : m.status === 'approved' ? `
      <button class="btn-sm success" onclick="moDone(${m.id})" title="Mark this mission complete and record what it earned / what happened.">&#127942; Done</button>
      <button class="btn-sm danger" onclick="moReject(${m.id})">&#10005; Reject</button>`
    : '';
  return `
    <div class="card" style="padding:12px 14px;display:flex;flex-direction:column;gap:8px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
        <span style="font-size:.68rem;font-weight:700;color:${k.color};border:1px solid ${k.color};
          border-radius:12px;padding:2px 9px;white-space:nowrap;">${k.icon} ${k.label}</span>
        <span style="font-size:.72rem;">${statusChip}</span>
      </div>
      <div style="font-weight:600;font-size:.9rem;line-height:1.35;">${esc(m.title || '')}</div>
      ${m.detail ? `<div style="font-size:.78rem;color:var(--muted);line-height:1.45;">${esc(m.detail)}</div>` : ''}
      <div style="display:flex;justify-content:space-between;align-items:center;font-size:.72rem;color:var(--muted);">
        <span>${m.est_value_cents ? `est. <b style="color:var(--accent);">${_moUsd(m.est_value_cents)}</b>` : ''}</span>
        <span>${m.agent ? `&#128100; ${esc(m.agent)}` : ''}</span>
      </div>
      ${m.result ? `<div style="font-size:.74rem;color:var(--accent2);">&#128221; ${esc(m.result)}</div>` : ''}
      ${btns ? `<div style="display:flex;gap:6px;flex-wrap:wrap;border-top:1px solid var(--border,#3333);padding-top:8px;">${btns}</div>` : ''}
    </div>`;
}

async function moApprove(id) {
  try { await api(`/api/money/missions/${id}/approve`, { method: 'POST', body: JSON.stringify({}) });
    toast('✅ Mission approved'); await loadMoStats(); await loadMoMissions(); }
  catch (e) { toast('Approve failed: ' + e.message, 'error'); }
}
async function moReject(id) {
  if (!confirm('Reject this mission?')) return;
  try { await api(`/api/money/missions/${id}/reject`, { method: 'POST', body: JSON.stringify({}) });
    toast('Rejected'); await loadMoStats(); await loadMoMissions(); }
  catch (e) { toast('Reject failed: ' + e.message, 'error'); }
}
async function moDone(id) {
  const result = prompt('What happened / what did it earn?') || '';
  try { await api(`/api/money/missions/${id}/done`, { method: 'POST', body: JSON.stringify({ result }) });
    toast('🏆 Marked done'); await loadMoStats(); await loadMoMissions(); }
  catch (e) { toast('Failed: ' + e.message, 'error'); }
}

/* ── Cash App (real-money rail #2 — receive only) ─────────────────────────── */
let _moCa = null;   // last /api/cashapp/status payload

async function loadMoCashApp() {
  const el = document.getElementById('money-cashapp');
  if (!el) return;
  let st, reqs = [];
  try { st = await api('/api/cashapp/status'); } catch (e) { el.innerHTML = ''; return; }
  _moCa = st;
  try { reqs = (await api('/api/cashapp/requests?limit=8')).requests || []; } catch {}
  const sq = st.square || {};
  const tagChip = st.cashtag
    ? `<span style="color:var(--green,#22c55e);font-weight:600;">$${esc(st.cashtag)}</span>`
    : `<span style="color:var(--warn);">no $cashtag set</span>`;
  const sqChip = sq.configured
    ? `<span style="color:var(--green,#22c55e);font-weight:600;">Square connected (${esc(sq.mode || '')})</span>`
    : `<span style="color:var(--muted);">Square not configured</span>`;
  el.innerHTML = `
    <div class="card" style="padding:16px 18px;">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
        <div style="font-weight:700;font-size:1rem;">&#128181; Cash App
          ${hlp('Receive real money via Cash App. Two rails: free $cashtag payment-request links (cash.app/$tag/amount, no credentials), and real Square-hosted checkout pages that accept Cash App Pay (needs a Square access token; ~3.3% + 30¢ online on the free plan, 2026 pricing). Both actions are approval-gated: they file a prayer you bless in the God Console — each gate has a toggle below. There is NO official API for personal-account balance/send; this is receive-only by design.')}
          <span style="font-size:.74rem;font-weight:400;margin-left:8px;">${tagChip} &middot; ${sqChip}</span>
        </div>
        <div style="font-size:.72rem;color:var(--muted);">
          ${st.pending_prayers ? `&#9203; ${st.pending_prayers} awaiting blessing in the God Console` : ''}
        </div>
      </div>

      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-bottom:12px;">
        <div class="field" style="margin:0;width:110px;">
          <label style="font-size:.72rem;">Amount $ ${hlp('Dollar amount to request. Leave 0/empty on the $cashtag rail for an open-amount profile link.')}</label>
          <input type="number" id="mo-ca-amount" min="0" step="0.01" placeholder="25.00">
        </div>
        <div class="field" style="margin:0;flex:1;min-width:180px;">
          <label style="font-size:.72rem;">Note</label>
          <input type="text" id="mo-ca-note" placeholder="e.g. Deck repair deposit">
        </div>
        <button class="btn-sm primary" onclick="moCaRequest()" ${st.cashtag ? '' : 'disabled'}
          title="Files a gated cashapp_request prayer. Once blessed (God Console), the cash.app/$tag/amount link + QR appear below. Free; the payer confirms in their Cash App.">&#128181; Request via $cashtag</button>
        <button class="btn-sm" onclick="moCaCheckout()" ${sq.configured ? '' : 'disabled'}
          title="Files a gated cashapp_checkout prayer. Once blessed, a REAL Square-hosted checkout link (Cash App Pay + cards) is created${sq.configured ? ' (' + sq.mode + ')' : ''}. Needs a Square access token.">&#129001; Cash App Pay checkout</button>
      </div>

      ${reqs.length ? `
      <div style="overflow-x:auto;margin-bottom:10px;"><table style="width:100%;font-size:.78rem;border-collapse:collapse;">
        <thead><tr style="text-align:left;color:var(--muted);font-size:.68rem;text-transform:uppercase;letter-spacing:.04em;">
          <th style="padding:6px 8px;">QR</th><th style="padding:6px 8px;">Rail</th><th style="padding:6px 8px;">Amount</th>
          <th style="padding:6px 8px;">Note</th><th style="padding:6px 8px;">Link</th><th style="padding:6px 8px;">When</th></tr></thead>
        <tbody>${reqs.map(r => `
          <tr style="border-top:1px solid var(--border,#3333);">
            <td style="padding:6px 8px;"><img src="${API}/api/cashapp/requests/${r.id}/qr" alt="QR" width="46" height="46"
              style="border-radius:4px;background:#fff;" onerror="this.style.display='none'"></td>
            <td style="padding:6px 8px;">${r.kind === 'checkout' ? '&#129001; Square' : '&#128181; $cashtag'}</td>
            <td style="padding:6px 8px;">${r.amount_cents ? '$' + (r.amount_cents / 100).toFixed(2) : 'any'}</td>
            <td style="padding:6px 8px;color:var(--muted);">${esc(r.note || '')}</td>
            <td style="padding:6px 8px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
              <a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.url)}</a>
              <button class="btn-sm" style="padding:1px 7px;margin-left:4px;" title="Copy link"
                onclick="navigator.clipboard.writeText('${esc(r.url)}').then(()=>toast('Link copied'))">&#128203;</button></td>
            <td style="padding:6px 8px;color:var(--muted);white-space:nowrap;">${esc((r.created_at || '').slice(0, 16))}</td>
          </tr>`).join('')}
        </tbody></table></div>` : ''}

      <div style="display:flex;gap:18px;flex-wrap:wrap;align-items:center;font-size:.76rem;margin-bottom:8px;">
        <label style="display:flex;gap:6px;align-items:center;cursor:pointer;" title="ON = every $cashtag payment-request link waits for your blessing in the God Console. OFF (in budget mode) = links generate immediately.">
          <input type="checkbox" ${st.gates && st.gates.cashapp_request ? 'checked' : ''}
            onchange="moCaGate('cashapp_request', this.checked)"> Gate $cashtag requests</label>
        <label style="display:flex;gap:6px;align-items:center;cursor:pointer;" title="ON = every Square Cash App Pay checkout link waits for your blessing. OFF (in budget mode) = live checkout links are created immediately.">
          <input type="checkbox" ${st.gates && st.gates.cashapp_checkout ? 'checked' : ''}
            onchange="moCaGate('cashapp_checkout', this.checked)"> Gate Cash App Pay checkouts</label>
      </div>

      <details class="settings-group">
        <summary style="cursor:pointer;font-weight:600;font-size:.85rem;">&#9881;&#65039; Cash App setup</summary>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-top:12px;">
          <div class="field" style="margin:0;width:160px;">
            <label style="font-size:.72rem;">$Cashtag ${hlp('Your Cash App $cashtag (personal or business account). Used to build cash.app/$tag/amount payment-request links — free, no API keys needed. Business accounts pay ~2.75% per received payment; personal is free with receive limits until verified.')}</label>
            <input type="text" id="mo-ca-tag" value="${esc(st.cashtag || '')}" placeholder="$YourTag">
          </div>
          <div class="field" style="margin:0;flex:1;min-width:220px;">
            <label style="font-size:.72rem;">Square access token ${hlp('From developer.squareup.com → your application → Credentials. Use the SANDBOX token first (free, fake money), the production token after Square identity verification. Stored encrypted at rest; never shown after saving.')}</label>
            <input type="password" id="mo-ca-token" placeholder="${sq.configured ? '(saved — enter to replace)' : 'EAAA…'}">
          </div>
          <div class="field" style="margin:0;width:150px;">
            <label style="font-size:.72rem;">Location ID ${hlp('Optional. Square location for checkout links (Developer Console → Locations). Left blank = the first location on the account is used automatically.')}</label>
            <input type="text" id="mo-ca-loc" value="${esc(sq.location_id || '')}" placeholder="auto">
          </div>
          <div class="field" style="margin:0;width:130px;">
            <label style="font-size:.72rem;">Mode</label>
            <select id="mo-ca-mode">
              <option value="sandbox" ${sq.mode !== 'production' ? 'selected' : ''}>sandbox</option>
              <option value="production" ${sq.mode === 'production' ? 'selected' : ''}>production</option>
            </select>
          </div>
          <button class="btn-sm primary" onclick="moCaSave()">&#128190; Save</button>
          <button class="btn-sm" onclick="moCaVerify()" ${sq.configured ? '' : 'disabled'}
            title="Read-only check: lists your Square locations to prove the token works. Moves no money.">&#128270; Verify</button>
        </div>
        <div style="font-size:.74rem;color:var(--muted);line-height:1.5;margin-top:10px;">
          <b>To go live, you need to:</b><br>
          &bull; <b>$cashtag rail (free, 2 min):</b> open Cash App &rarr; profile &rarr; note your $cashtag and enter it above. Done — request links + QR work immediately. (No official API exists for personal balance/send; receive-only.)<br>
          &bull; <b>Cash App Pay rail (Square):</b> 1) create a Square account at squareup.com; 2) create an application at developer.squareup.com and paste the <i>sandbox</i> access token above to test; 3) for real money, complete Square identity verification (name, DOB, SSN/ITIN — sole proprietor is fine) and switch to the <i>production</i> token + mode. Online fee ≈ 3.3% + 30¢ (free plan, 2026).<br>
          &bull; Blessed links appear in the table above with a scannable QR.
        </div>
      </details>
    </div>`;
}

async function moCaSave() {
  const body = {
    cashtag: document.getElementById('mo-ca-tag').value.trim(),
    location_id: document.getElementById('mo-ca-loc').value.trim(),
    mode: document.getElementById('mo-ca-mode').value,
  };
  const tok = document.getElementById('mo-ca-token').value.trim();
  if (tok) body.access_token = tok;          // never blank out a saved token
  if (!body.cashtag) delete body.cashtag;
  try {
    await api('/api/cashapp/config', { method: 'POST', body: JSON.stringify(body) });
    toast('💾 Cash App settings saved');
    await loadMoCashApp();
  } catch (e) { toast('Save failed: ' + e.message, 'error'); }
}

async function moCaVerify() {
  try {
    const r = await api('/api/cashapp/verify', { method: 'POST', body: JSON.stringify({}) });
    if (r.connected) {
      const locs = (r.locations || []).map(l => l.name || l.id).join(', ');
      toast(`✅ Square connected (${r.mode})${locs ? ' — ' + locs : ''}`);
    } else toast('Square: ' + (r.error || 'not connected'), 'error');
  } catch (e) { toast('Verify failed: ' + e.message, 'error'); }
}

function _moCaAmount() {
  return Math.round((parseFloat(document.getElementById('mo-ca-amount').value) || 0) * 100);
}

async function moCaRequest() {
  const body = { amount_cents: _moCaAmount(), note: document.getElementById('mo-ca-note').value.trim() };
  try {
    const { prayer } = await api('/api/cashapp/request', { method: 'POST', body: JSON.stringify(body) });
    toast(prayer.status === 'done' ? '💵 Link ready below' :
      '⏳ Request filed — bless it in the God Console to generate the link');
    await loadMoCashApp();
  } catch (e) { toast('Request failed: ' + e.message, 'error'); }
}

async function moCaCheckout() {
  const amt = _moCaAmount();
  if (!amt) { toast('Enter an amount for a checkout link', 'error'); return; }
  const body = { amount_cents: amt, note: document.getElementById('mo-ca-note').value.trim(), name: 'Payment' };
  try {
    const { prayer } = await api('/api/cashapp/checkout', { method: 'POST', body: JSON.stringify(body) });
    toast(prayer.status === 'done' ? '🟩 Checkout link created below' :
      '⏳ Checkout filed — bless it in the God Console to create the live link');
    await loadMoCashApp();
  } catch (e) { toast('Checkout failed: ' + e.message, 'error'); }
}

async function moCaGate(key, on) {
  try {
    await api('/api/world/ops/gates', { method: 'POST', body: JSON.stringify({ key, on }) });
    toast(`Gate ${on ? 'ON — needs your blessing' : 'OFF — can auto-run in budget mode'}`);
  } catch (e) { toast('Gate change failed: ' + e.message, 'error'); await loadMoCashApp(); }
}

window.loadMoCashApp = loadMoCashApp;

/* ── Donate (real-money rail #3 — Buy Me a Coffee link, receive only) ─────────
   Toggle-controlled, default OFF (GET /api/donate/config → {enabled, url}). No
   handle is ever hardcoded here — a fresh public clone gets an empty/hidden
   card until the owner sets donate_bmc_user + donate_enabled in Settings →
   Integrations. A donate link moves no money itself, so no approval gate. */
async function loadMoDonate() {
  const el = document.getElementById('money-donate');
  if (!el) return;
  let d;
  try { d = await api('/api/donate/config'); } catch { el.innerHTML = ''; return; }
  if (!d || !d.enabled || !d.url) { el.innerHTML = ''; return; }
  el.innerHTML = `
    <div class="card" style="padding:16px 18px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
      <div style="font-weight:700;font-size:1rem;">&#9749; Support this project
        ${hlp('A Buy Me a Coffee link, set in Settings → Integrations. Purely receive-only — no approval gate, since a donate link moves no money itself. Off by default on a fresh clone until the owner sets a handle and flips this on.')}
      </div>
      <a class="btn-sm primary" href="${esc(d.url)}" target="_blank" rel="noopener">&#9749; Support this project</a>
    </div>`;
}
window.loadMoDonate = loadMoDonate;
window.moCaSave = moCaSave;
window.moCaVerify = moCaVerify;
window.moCaRequest = moCaRequest;
window.moCaCheckout = moCaCheckout;
window.moCaGate = moCaGate;

window.moRunReview = moRunReview;
window.moAddMission = moAddMission;
window.moSetFilter = moSetFilter;
window.moApprove = moApprove;
window.moReject = moReject;
window.moDone = moDone;

'use strict';
/* ══ 📆 BILLS — real personal bills tracker (Finance → Bills pane) ══
   Backend: app/routers/money/bills.py
     GET    /api/bills[?active=1]        list + counts
     POST   /api/bills                   create
     PATCH  /api/bills/{id}              update (any of the patchable fields)
     DELETE /api/bills/{id}              hard delete (bill + its payments)
     POST   /api/bills/{id}/pay          {amount_cents?, note?, paid_at?}
     GET    /api/bills/{id}/payments     history
     DELETE /api/bills/{id}/payments/{pid}
     GET    /api/bills/summary           overdue / due_soon / monthly / paid-this-month
     GET    /api/bills/series?months=12  per-month paid totals (the canvas chart)
     GET    /api/bills/export.csv        download
     POST   /api/bills/import            {csv: "..."}
   Amounts are integer cents everywhere; amount_cents null means the bill varies.
   No credentials live here by design: portal links and notes only.
   Every fetch is failure tolerant — the pane still renders when the API is down
   (e.g. before a restart picks the router up), it just shows a degraded state. */

const BILL_CYCLES = ['monthly', 'weekly', 'quarterly', 'yearly', 'once'];

let _bills = [];            // full list from GET /api/bills
let _billsSummary = null;   // GET /api/bills/summary
let _billsSeries = null;    // GET /api/bills/series
let _billsCounts = {};
let _billsShowInactive = false;
let _billEditId = null;     // null = the form is adding, number = editing that bill
let _billFormOpen = false;
let _billExtraSeq = 0;      // unique ids for the "+ custom field" rows
let _billsDegraded = false; // true when the API did not answer

/* Which segment of the pane is showing. The 📆 Bills pane hosts the whole
   personal money ledger: bills (obligations), paychecks (money in), purchases
   (non-bill money out) and an overview that nets them against each other. */
let _billSection = 'bills';   // bills|calendar|paychecks|purchases|budget|insights|plan|overview
const _LEDGER_SECTIONS = [
  ['bills', '&#128198; Bills'],
  ['calendar', '&#128467;&#65039; Calendar'],
  ['paychecks', '&#128176; Income'],
  ['purchases', '&#128722; Purchases'],
  ['budget', '&#129518; Budget'],
  ['insights', '&#128200; Insights'],
  ['plan', '&#128722; Plan'],
  ['overview', '&#128202; Overview'],
];

const billUSD = c => '$' + (Math.round(c || 0) / 100).toFixed(2);

/* ── helpers ─────────────────────────────────────────────────────────────── */

function _billTodayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// Whole days from today to a YYYY-MM-DD date (negative = overdue). null if unset.
function _billDays(iso) {
  if (!iso) return null;
  const p = String(iso).slice(0, 10).split('-').map(Number);
  if (p.length !== 3 || p.some(isNaN)) return null;
  const t = _billTodayISO().split('-').map(Number);
  const a = Date.UTC(p[0], p[1] - 1, p[2]), b = Date.UTC(t[0], t[1] - 1, t[2]);
  return Math.round((a - b) / 86400000);
}

function _billWhen(iso) {
  const d = _billDays(iso);
  if (d === null) return { text: 'no date set', color: 'var(--muted)' };
  if (d < 0) return { text: `${-d} day${d === -1 ? '' : 's'} overdue`, color: 'var(--red)' };
  if (d === 0) return { text: 'due today', color: 'var(--red)' };
  if (d === 1) return { text: 'due tomorrow', color: 'var(--warn)' };
  if (d <= 7) return { text: `in ${d} days`, color: 'var(--warn)' };
  return { text: `in ${d} days`, color: 'var(--muted)' };
}

function _billCycleLabel(cycle) {
  const m = /^custom-(\d+)-days$/.exec(cycle || '');
  if (m) return `every ${m[1]} day${m[1] === '1' ? '' : 's'}`;
  return cycle || 'monthly';
}

function _billChip(text, color) {
  return `<span style="font-size:.65rem;padding:2px 8px;border-radius:9px;background:var(--surface);
    border:1px solid var(--border);color:${color || 'var(--muted)'};white-space:nowrap;">${esc(text)}</span>`;
}

/* ── load + render ───────────────────────────────────────────────────────── */

async function renderBills(rootId) {
  const el = document.getElementById(rootId || 'fin-pane-bills');
  if (!el) return;
  el.innerHTML = `${_ledgerSegHtml()}
    <div id="bill-section-body">
      <div class="empty"><div class="empty-icon">&#9203;</div>Loading&#8230;</div>
    </div>`;
  await _ledgerRenderSection();
}
window.renderBills = renderBills;

/* Renders the active segment into #bill-section-body. Each section owns its own
   fetches and its own degraded/empty states, so one dead endpoint never takes
   the whole pane down. */
async function _ledgerRenderSection() {
  if (_billSection === 'calendar') return _calRender();
  if (_billSection === 'paychecks') return _payRender();
  if (_billSection === 'purchases') return _purRender();
  if (_billSection === 'budget') return _budRender();
  if (_billSection === 'insights') return _insRender();
  if (_billSection === 'plan') return _planRender();
  if (_billSection === 'overview') return _ovRender();
  return _billsSectionRender();
}

async function _billsSectionRender() {
  const el = document.getElementById('bill-section-body');
  if (!el) return;
  el.innerHTML = `<div class="empty"><div class="empty-icon">&#9203;</div>Loading bills&#8230;</div>`;

  const [list, sum, series] = await Promise.allSettled([
    api('/api/bills'),
    api('/api/bills/summary'),
    api('/api/bills/series?months=12'),
  ]);
  if (!el.isConnected) return;   // user moved on mid-fetch

  _billsDegraded = list.status !== 'fulfilled';
  _bills = (list.status === 'fulfilled' && Array.isArray(list.value.bills)) ? list.value.bills : [];
  _billsCounts = (list.status === 'fulfilled' && list.value.counts) || {};
  _billsSummary = sum.status === 'fulfilled' ? sum.value : null;
  _billsSeries = series.status === 'fulfilled' ? series.value : null;

  el.innerHTML = _billsHtml();
  _billsDrawChart();
}

function _billsHtml() {
  const head = `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
      <div>
        <div style="font-weight:700;font-size:1.02rem;">&#128198; Bills</div>
        <div style="color:var(--muted);font-size:.78rem;margin-top:2px;">
          Real household bills: portal links, cycles, what is due and what is paid.
          Links and notes only ${hlp('No passwords or account numbers are stored here. Keep any secret in Settings, which is encrypted at rest.')}
        </div>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">
        <button class="btn-sm primary" onclick="billOpenForm()">&#10133; Add bill</button>
        <button class="btn-sm" onclick="billExportCsv()">&#8595; Export CSV</button>
        <button class="btn-sm" onclick="billImportCsv()">&#8593; Import CSV</button>
        <button class="btn-sm" onclick="renderBills()">&#8635; Refresh</button>
      </div>
    </div>
    <input type="file" id="bill-csv-file" accept=".csv,text/csv" style="display:none;"
           onchange="billImportPicked(this)">`;

  if (_billsDegraded) {
    return `${head}
      <div class="empty" style="padding:40px 16px;">
        <div class="empty-icon">&#9888;&#65039;</div>
        <div style="font-weight:600;margin-bottom:4px;">Bills are not answering yet</div>
        <div style="color:var(--muted);font-size:.82rem;max-width:440px;margin:0 auto;line-height:1.6;">
          The bills service did not respond. If the app was just updated it needs a restart to pick it up.
        </div>
        <div style="margin-top:10px;"><button class="btn-sm" onclick="renderBills()">Retry</button></div>
      </div>
      <div id="bill-form-wrap"></div>`;
  }

  if (!_bills.length) {
    return `${head}
      <div class="empty" style="padding:46px 16px;">
        <div class="empty-icon">&#128198;</div>
        <div style="color:var(--muted);font-size:.86rem;">Track real bills: portal links, due dates, what is paid.</div>
        <div style="margin-top:12px;"><button class="btn-sm primary" onclick="billOpenForm()">&#10133; Add bill</button></div>
      </div>
      <div id="bill-form-wrap"></div>`;
  }

  return `${head}
    ${_billsStrip()}
    <div id="bill-form-wrap"></div>
    ${_billsTable()}
    ${_billsChartHtml()}`;
}

/* ── top strip ───────────────────────────────────────────────────────────── */

function _billsStrip() {
  const s = _billsSummary;
  const card = (icon, label, val, color, sub) => `
    <div class="stat-card">
      <div class="stat-label">${icon} ${label}</div>
      <div class="stat-val" style="font-size:1.6rem;color:${color};">${val}</div>
      <div style="font-size:.66rem;color:var(--muted);margin-top:5px;">${sub || '&nbsp;'}</div>
    </div>`;
  if (!s) {
    return `<div class="stats-row" style="margin:16px 0;">
      ${card('&#128308;', 'Overdue', '—', 'var(--muted)', 'summary unavailable')}
      ${card('&#9203;', 'Due in 7 days', '—', 'var(--muted)', '')}
      ${card('&#128197;', 'Est. monthly', '—', 'var(--muted)', '')}
      ${card('&#9989;', 'Paid this month', '—', 'var(--muted)', '')}
    </div>`;
  }
  const od = s.overdue_count || 0, soon = s.due_soon_count || 0;
  const odSum = (s.overdue || []).reduce((a, b) => a + (b.amount_cents || 0), 0);
  const soonSum = (s.due_soon || []).reduce((a, b) => a + (b.amount_cents || 0), 0);
  return `<div class="stats-row" style="margin:16px 0;">
    ${card('&#128308;', 'Overdue', od, od ? 'var(--red)' : 'var(--green)',
      od ? `${billUSD(odSum)} of known amounts` : 'nothing past due')}
    ${card('&#9203;', 'Due in 7 days', soon, soon ? 'var(--warn)' : 'var(--muted)',
      soon ? `${billUSD(soonSum)} of known amounts` : 'clear week')}
    ${card('&#128197;', 'Est. monthly', billUSD(s.monthly_total_cents), 'var(--text)',
      `${s.active_count || 0} active${s.variable_unknown ? ` &middot; ${s.variable_unknown} varies, no history` : ''}`)}
    ${card('&#9989;', 'Paid this month', billUSD(s.paid_this_month_cents), 'var(--accent2)',
      `${s.paid_this_month_count || 0} payment${(s.paid_this_month_count || 0) === 1 ? '' : 's'}`)}
  </div>`;
}

/* ── table ───────────────────────────────────────────────────────────────── */

function _billsTable() {
  const shown = _bills.filter(b => _billsShowInactive || b.active);
  const inactive = _billsCounts.inactive || _bills.filter(b => !b.active).length;
  const th = (t, extra) => `<th style="padding:7px 8px;${extra || ''}">${t}</th>`;

  const rows = shown.map(b => {
    const when = _billWhen(b.next_due);
    const dim = b.active ? '' : 'opacity:.55;';
    const link = b.portal_url
      ? ` <a href="${esc(b.portal_url)}" target="_blank" rel="noopener" title="Open the biller portal"
           style="text-decoration:none;">&#128279;</a>` : '';
    const extras = Object.keys(b.extra || {}).length;
    return `
      <tr style="border-bottom:1px solid var(--border);${dim}">
        <td style="padding:7px 8px;">
          <span style="cursor:pointer;" onclick="billToggleDrawer(${b.id})" title="History and details">&#9662;</span>
          <b>${esc(b.name)}</b>${link}
          ${b.portal_note ? `<div style="font-size:.66rem;color:var(--muted);">${esc(b.portal_note)}</div>` : ''}
          ${extras ? `<div style="font-size:.62rem;color:var(--muted);">${extras} custom field${extras === 1 ? '' : 's'}</div>` : ''}
        </td>
        <td style="padding:7px 8px;">${b.category ? _billChip(b.category, 'var(--accent2)') : ''}</td>
        <td style="padding:7px 8px;text-align:right;">${b.amount_cents === null || b.amount_cents === undefined
          ? '<span style="color:var(--muted);font-style:italic;">varies</span>' : billUSD(b.amount_cents)}</td>
        <td style="padding:7px 8px;color:var(--muted);">${esc(_billCycleLabel(b.cycle))}</td>
        <td style="padding:7px 8px;">
          <span style="color:${when.color};font-weight:600;">${when.text}</span>
          <div style="font-size:.66rem;color:var(--muted);">${esc(b.next_due || '')}</div>
        </td>
        <td style="padding:7px 8px;">${b.autopay ? _billChip('autopay', 'var(--green)') : _billChip('manual')}</td>
        <td style="padding:7px 8px;text-align:right;white-space:nowrap;">
          ${b.active ? `<button class="btn-sm success" onclick="billMarkPaid(${b.id})">&#10003; Paid</button>` : ''}
          <button class="btn-sm" onclick="billOpenForm(${b.id})">&#9998;</button>
          <button class="btn-sm" onclick="billSetActive(${b.id},${b.active ? 0 : 1})"
            title="${b.active ? 'Deactivate (keeps history)' : 'Reactivate'}">${b.active ? '&#9209;' : '&#9654;'}</button>
        </td>
      </tr>
      <tr id="bill-drawer-${b.id}" style="display:none;">
        <td colspan="7" style="padding:0 8px 12px;"><div id="bill-drawer-body-${b.id}"></div></td>
      </tr>`;
  }).join('');

  return `
    <div style="display:flex;align-items:center;justify-content:space-between;margin:6px 0;">
      <div style="font-weight:600;font-size:.86rem;">${shown.length} bill${shown.length === 1 ? '' : 's'}</div>
      ${inactive ? `<label style="font-size:.72rem;color:var(--muted);cursor:pointer;">
        <input type="checkbox" ${_billsShowInactive ? 'checked' : ''} onchange="billToggleInactive(this.checked)">
        show ${inactive} inactive</label>` : ''}
    </div>
    <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:.8rem;">
        <thead><tr style="color:var(--muted);text-align:left;border-bottom:1px solid var(--border);">
          ${th('Bill')}${th('Category')}${th('Amount', 'text-align:right;')}${th('Cycle')}
          ${th('Next due')}${th('Pay')}${th('', 'text-align:right;')}
        </tr></thead>
        <tbody>${rows || `<tr><td colspan="7" style="padding:14px 8px;color:var(--muted);">Nothing to show.</td></tr>`}</tbody>
      </table>
    </div>`;
}

function billToggleInactive(v) {
  _billsShowInactive = !!v;
  renderBills();
}
window.billToggleInactive = billToggleInactive;

/* ── per-bill drawer: custom fields + payment history ────────────────────── */

async function billToggleDrawer(id) {
  const row = document.getElementById('bill-drawer-' + id);
  const body = document.getElementById('bill-drawer-body-' + id);
  if (!row || !body) return;
  if (row.style.display !== 'none') { row.style.display = 'none'; return; }
  row.style.display = '';
  body.innerHTML = `<div style="color:var(--muted);font-size:.76rem;padding:8px;">Loading history&#8230;</div>`;

  const b = _bills.find(x => x.id === id) || {};
  let pays = null;
  try { pays = await api(`/api/bills/${id}/payments?limit=50`); } catch { pays = null; }
  if (!body.isConnected) return;

  const extras = Object.entries(b.extra || {});
  const extraHtml = extras.length
    ? `<div style="margin-bottom:10px;">
         <div style="font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Custom fields</div>
         <div style="display:flex;gap:8px;flex-wrap:wrap;">
           ${extras.map(([k, v]) => `<span style="font-size:.72rem;background:var(--surface);border:1px solid var(--border);
             border-radius:8px;padding:4px 9px;"><b>${esc(k)}</b>: ${esc(v)}</span>`).join('')}
         </div></div>`
    : '';

  const list = (pays && pays.payments) || [];
  const total = list.reduce((a, p) => a + (p.amount_cents || 0), 0);
  const payHtml = !pays
    ? `<div style="color:var(--muted);font-size:.76rem;">History unavailable.</div>`
    : (!list.length
      ? `<div style="color:var(--muted);font-size:.76rem;">No payments logged yet.</div>`
      : `<table style="width:100%;border-collapse:collapse;font-size:.76rem;">
           <thead><tr style="color:var(--muted);text-align:left;">
             <th style="padding:4px 8px;">Paid</th><th style="padding:4px 8px;text-align:right;">Amount</th>
             <th style="padding:4px 8px;">Note</th><th style="padding:4px 8px;"></th></tr></thead>
           <tbody>${list.map(p => `<tr style="border-top:1px solid var(--border);">
             <td style="padding:4px 8px;">${esc(String(p.paid_at || '').slice(0, 10))}</td>
             <td style="padding:4px 8px;text-align:right;">${billUSD(p.amount_cents)}</td>
             <td style="padding:4px 8px;color:var(--muted);">${esc(p.note || '')}</td>
             <td style="padding:4px 8px;text-align:right;">
               <button class="btn-sm" title="Remove this payment"
                 onclick="billDeletePayment(${id},${p.id})">&#10005;</button></td>
           </tr>`).join('')}</tbody>
         </table>
         <div style="font-size:.7rem;color:var(--muted);margin-top:6px;">
           ${list.length} payment${list.length === 1 ? '' : 's'} &middot; ${billUSD(total)} total logged.
           Removing a payment does not roll the next due date back.
         </div>`);

  body.innerHTML = `
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px;">
      ${extraHtml}
      ${b.portal_url ? `<div style="font-size:.72rem;margin-bottom:8px;">Portal:
        <a href="${esc(b.portal_url)}" target="_blank" rel="noopener" style="color:var(--accent);">${esc(b.portal_url)}</a></div>` : ''}
      ${payHtml}
      <div style="margin-top:10px;display:flex;gap:6px;">
        <button class="btn-sm" onclick="billOpenForm(${id})">&#9998; Edit bill</button>
        <button class="btn-sm danger" onclick="billDelete(${id})">&#128465; Delete permanently</button>
      </div>
    </div>`;
}
window.billToggleDrawer = billToggleDrawer;

async function billDeletePayment(bid, pid) {
  if (!confirm('Remove this payment from the history?')) return;
  try {
    await api(`/api/bills/${bid}/payments/${pid}`, { method: 'DELETE' });
    toast('Payment removed');
    await renderBills();
  } catch (e) { toast(e.message || 'Could not remove that payment', 'error'); }
}
window.billDeletePayment = billDeletePayment;

/* ── actions ─────────────────────────────────────────────────────────────── */

async function billMarkPaid(id) {
  const b = _bills.find(x => x.id === id);
  if (!b) return;
  let amount_cents = b.amount_cents;
  if (amount_cents === null || amount_cents === undefined) {
    const raw = prompt(`How much was the ${b.name} bill this time? (dollars)`, '');
    if (raw === null) return;
    const v = parseFloat(String(raw).replace(/[$,\s]/g, ''));
    if (!isFinite(v) || v < 0) { toast('Enter a dollar amount like 84.20', 'error'); return; }
    amount_cents = Math.round(v * 100);
  }
  try {
    const r = await api(`/api/bills/${id}/pay`, {
      method: 'POST',
      body: JSON.stringify({ amount_cents, paid_at: _billTodayISO() }),
    });
    const nxt = r && r.bill && r.bill.next_due;
    toast(`${b.name} marked paid${nxt ? ` — next due ${nxt}` : ''}`);
    await renderBills();
  } catch (e) { toast(e.message || 'Could not mark that paid', 'error'); }
}
window.billMarkPaid = billMarkPaid;

async function billSetActive(id, active) {
  try {
    await api(`/api/bills/${id}`, { method: 'PATCH', body: JSON.stringify({ active: !!active }) });
    toast(active ? 'Bill reactivated' : 'Bill deactivated (history kept)');
    await renderBills();
  } catch (e) { toast(e.message || 'Could not update that bill', 'error'); }
}
window.billSetActive = billSetActive;

async function billDelete(id) {
  const b = _bills.find(x => x.id === id) || {};
  if (!confirm(`Delete "${b.name || 'this bill'}" and its whole payment history? Deactivating keeps the history instead.`)) return;
  try {
    await api(`/api/bills/${id}`, { method: 'DELETE' });
    toast('Bill deleted');
    await renderBills();
  } catch (e) { toast(e.message || 'Could not delete that bill', 'error'); }
}
window.billDelete = billDelete;

/* ── add / edit form (incl. the custom-field row editor) ─────────────────── */

function billOpenForm(id) {
  const wrap = document.getElementById('bill-form-wrap');
  if (!wrap) return;
  _billEditId = (id === undefined || id === null) ? null : id;
  _billFormOpen = true;
  const b = _billEditId ? (_bills.find(x => x.id === _billEditId) || {}) : {};
  const cycle = b.cycle || 'monthly';
  const customDays = (/^custom-(\d+)-days$/.exec(cycle) || [])[1] || '';
  const isCustom = !!customDays;

  wrap.innerHTML = `
    <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:16px;margin:12px 0;">
      <div style="font-weight:700;margin-bottom:12px;">${_billEditId ? '&#9998; Edit bill' : '&#10133; New bill'}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;">
        <div class="field" style="margin:0;">
          <label>Name</label>
          <input type="text" id="bill-f-name" value="${esc(b.name || '')}" placeholder="Power company">
        </div>
        <div class="field" style="margin:0;">
          <label>Category ${hlp('Free text. Groups the chart and the totals, so use whatever words fit your bills.')}</label>
          <input type="text" id="bill-f-category" value="${esc(b.category || '')}" placeholder="utilities">
        </div>
        <div class="field" style="margin:0;">
          <label>Amount ${hlp('Leave blank when the amount changes every cycle. You will be asked for it each time you mark it paid.')}</label>
          <input type="number" id="bill-f-amount" min="0" step="0.01" placeholder="blank = varies"
                 value="${(b.amount_cents === null || b.amount_cents === undefined) ? '' : (b.amount_cents / 100).toFixed(2)}">
        </div>
        <div class="field" style="margin:0;">
          <label>Cycle</label>
          <select id="bill-f-cycle" onchange="billCycleChanged()">
            ${BILL_CYCLES.map(c => `<option value="${c}"${(!isCustom && cycle === c) ? ' selected' : ''}>${c}</option>`).join('')}
            <option value="custom"${isCustom ? ' selected' : ''}>custom (every N days)</option>
          </select>
        </div>
        <div class="field" style="margin:0;${isCustom ? '' : 'display:none;'}" id="bill-f-customwrap">
          <label>Every N days</label>
          <input type="number" id="bill-f-customdays" min="1" max="9999" value="${esc(customDays)}" placeholder="45">
        </div>
        <div class="field" style="margin:0;">
          <label>Next due</label>
          <input type="date" id="bill-f-nextdue" value="${esc(String(b.next_due || '').slice(0, 10))}">
        </div>
        <div class="field" style="margin:0;">
          <label>Due day ${hlp('Day-of-month anchor, 1 to 31. A bill due the 31st stays month-end instead of drifting to the 28th. Blank fills in from the due date.')}</label>
          <input type="number" id="bill-f-dueday" min="1" max="31" value="${b.due_day || ''}" placeholder="auto">
        </div>
        <div class="field" style="margin:0;">
          <label>Portal URL ${hlp('The biller login or pay page. Link only. Never put a password or account number here.')}</label>
          <input type="url" id="bill-f-portal" value="${esc(b.portal_url || '')}" placeholder="https://">
        </div>
        <div class="field" style="margin:0;">
          <label>Note</label>
          <input type="text" id="bill-f-note" value="${esc(b.portal_note || '')}" placeholder="how you pay it">
        </div>
        <div class="field" style="margin:0;display:flex;align-items:flex-end;gap:14px;">
          <label style="display:flex;align-items:center;gap:6px;text-transform:none;letter-spacing:0;font-size:.78rem;color:var(--text);cursor:pointer;">
            <input type="checkbox" id="bill-f-autopay" style="width:auto;" ${b.autopay ? 'checked' : ''}> Autopay
          </label>
        </div>
      </div>

      <div style="margin-top:12px;">
        <div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;">
          Custom fields ${hlp('Anything your bills need that the form does not have: account nickname, meter number, service address. Stored per bill.')}
        </div>
        <div id="bill-extra-rows"></div>
        <button class="btn-sm" onclick="billAddExtraRow('bill-extra-rows')">&#10133; custom field</button>
      </div>

      <div style="display:flex;gap:8px;margin-top:14px;">
        <button class="btn-sm primary" onclick="billSave()">&#128190; ${_billEditId ? 'Save' : 'Add bill'}</button>
        <button class="btn-sm" onclick="billCloseForm()">Cancel</button>
      </div>
    </div>`;

  const rows = document.getElementById('bill-extra-rows');
  const entries = Object.entries(b.extra || {});
  if (rows) rows.innerHTML = '';
  entries.forEach(([k, v]) => billAddExtraRow('bill-extra-rows', k, v));
  wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
window.billOpenForm = billOpenForm;

function billCloseForm() {
  _billFormOpen = false;
  _billEditId = null;
  const wrap = document.getElementById('bill-form-wrap');
  if (wrap) wrap.innerHTML = '';
}
window.billCloseForm = billCloseForm;

function billCycleChanged() {
  const sel = document.getElementById('bill-f-cycle');
  const wrap = document.getElementById('bill-f-customwrap');
  if (sel && wrap) wrap.style.display = sel.value === 'custom' ? '' : 'none';
}
window.billCycleChanged = billCycleChanged;

function billAddExtraRow(containerId, k, v) {
  const rows = document.getElementById(containerId);
  if (!rows) return;
  const i = ++_billExtraSeq;
  const div = document.createElement('div');
  div.className = 'bill-extra-row';
  div.style.cssText = 'display:flex;gap:6px;margin-bottom:6px;align-items:center;';
  div.innerHTML = `
    <input type="text" class="bill-extra-k" placeholder="field name"
      style="flex:1;background:var(--surface);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:.78rem;padding:7px 9px;font-family:inherit;">
    <input type="text" class="bill-extra-v" placeholder="value"
      style="flex:2;background:var(--surface);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:.78rem;padding:7px 9px;font-family:inherit;">
    <button class="btn-sm" title="Remove this field" onclick="this.parentNode.remove()">&#10005;</button>`;
  rows.appendChild(div);
  const ks = div.querySelector('.bill-extra-k'), vs = div.querySelector('.bill-extra-v');
  if (typeof k === 'string') ks.value = k;
  if (typeof v === 'string') vs.value = v;
  if (!k) ks.focus();
  return i;
}
window.billAddExtraRow = billAddExtraRow;

function _billCollectExtra(containerId) {
  const out = {};
  document.querySelectorAll(`#${containerId} .bill-extra-row`).forEach(r => {
    const k = (r.querySelector('.bill-extra-k') || {}).value || '';
    const v = (r.querySelector('.bill-extra-v') || {}).value || '';
    if (k.trim()) out[k.trim()] = v.trim();
  });
  return out;
}

async function billSave() {
  const val = id => ((document.getElementById(id) || {}).value || '').trim();
  const name = val('bill-f-name');
  if (!name) { toast('Give the bill a name', 'error'); return; }

  let cycle = val('bill-f-cycle') || 'monthly';
  if (cycle === 'custom') {
    const n = parseInt(val('bill-f-customdays'), 10);
    if (!n || n < 1) { toast('Enter how many days the custom cycle runs', 'error'); return; }
    cycle = `custom-${n}-days`;
  }
  const amountRaw = val('bill-f-amount');
  const amt = amountRaw === '' ? null : Math.round(parseFloat(amountRaw) * 100);
  if (amt !== null && (!isFinite(amt) || amt < 0)) { toast('Amount must be a positive number, or blank for varies', 'error'); return; }
  const dueDayRaw = val('bill-f-dueday');
  const dueDay = dueDayRaw === '' ? null : parseInt(dueDayRaw, 10);
  if (dueDay !== null && (!(dueDay >= 1 && dueDay <= 31))) { toast('Due day must be 1 to 31', 'error'); return; }

  const payload = {
    name,
    category: val('bill-f-category'),
    portal_url: val('bill-f-portal'),
    portal_note: val('bill-f-note'),
    amount_cents: amt,
    cycle,
    due_day: dueDay,
    next_due: val('bill-f-nextdue') || null,
    autopay: !!(document.getElementById('bill-f-autopay') || {}).checked,
    extra: _billCollectExtra('bill-extra-rows'),
  };
  try {
    if (_billEditId) {
      await api(`/api/bills/${_billEditId}`, { method: 'PATCH', body: JSON.stringify(payload) });
      toast('Bill saved');
    } else {
      await api('/api/bills', { method: 'POST', body: JSON.stringify(payload) });
      toast('Bill added');
    }
    billCloseForm();
    await renderBills();
  } catch (e) { toast(e.message || 'Could not save that bill', 'error'); }
}
window.billSave = billSave;

/* ── CSV in / out ────────────────────────────────────────────────────────── */

function billExportCsv() {
  window.open(API + '/api/bills/export.csv', '_blank');
}
window.billExportCsv = billExportCsv;

function billImportCsv() {
  const f = document.getElementById('bill-csv-file');
  if (f) { f.value = ''; f.click(); }
}
window.billImportCsv = billImportCsv;

function billImportPicked(input) {
  const file = input && input.files && input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const r = await api('/api/bills/import', {
        method: 'POST',
        body: JSON.stringify({ csv: String(reader.result || '') }),
      });
      const errs = (r && r.errors) || [];
      toast(`Imported ${r.imported || 0} bill${(r.imported || 0) === 1 ? '' : 's'}` +
        (errs.length ? ` — ${errs.length} row${errs.length === 1 ? '' : 's'} skipped` : ''),
        errs.length ? 'warn' : 'success');
      if (errs.length) errs.slice(0, 3).forEach(m => toast(m, 'warn'));
      await renderBills();
    } catch (e) { toast(e.message || 'Import failed', 'error'); }
  };
  reader.onerror = () => toast('Could not read that file', 'error');
  reader.readAsText(file);
}
window.billImportPicked = billImportPicked;

/* ── over-time chart (plain canvas, no libraries) ────────────────────────── */

function _billsChartHtml() {
  return `
    <div style="margin-top:22px;">
      <div style="font-weight:600;font-size:.86rem;margin-bottom:2px;">&#128200; Paid per month</div>
      <div style="font-size:.72rem;color:var(--muted);margin-bottom:8px;">
        Logged payments across the last 12 months. Mark bills paid to fill it in.</div>
      <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:12px;">
        <canvas id="bills-chart" height="180" style="width:100%;height:180px;display:block;"></canvas>
        <div id="bills-chart-note" style="font-size:.7rem;color:var(--muted);margin-top:6px;"></div>
      </div>
    </div>`;
}

function _billsDrawChart() {
  const cv = document.getElementById('bills-chart');
  if (!cv) return;
  const note = document.getElementById('bills-chart-note');
  const s = _billsSeries;
  if (!s || !Array.isArray(s.months) || !s.months.length) {
    if (note) note.textContent = 'Chart data unavailable.';
    return;
  }
  const vals = (s.total_cents || []).map(v => v || 0);
  const maxV = Math.max(...vals, 0);
  if (!maxV) {
    if (note) note.textContent = 'No payments logged yet — the chart fills in as you mark bills paid.';
  } else {
    const paid = vals.filter(v => v > 0).length;
    if (note) note.textContent = `Peak ${billUSD(maxV)} · ${paid} month${paid === 1 ? '' : 's'} with payments · ` +
      `${billUSD(Math.round(vals.reduce((a, b) => a + b, 0) / vals.length))} average.`;
  }

  const css = getComputedStyle(document.documentElement);
  const cvar = (n, fb) => (css.getPropertyValue(n) || '').trim() || fb;
  const COL = cvar('--accent', '#6c63ff'), MUTED = cvar('--muted', '#64748b'), BORDER = cvar('--border', '#2a2f3d');

  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(240, cv.clientWidth || 600), h = 180;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  const x = cv.getContext('2d');
  if (!x) return;
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  x.clearRect(0, 0, w, h);

  const padL = 46, padR = 8, padT = 10, padB = 22;
  const plotW = w - padL - padR, plotH = h - padT - padB;
  const scale = maxV > 0 ? plotH / maxV : 0;

  // gridlines + y labels
  x.strokeStyle = BORDER; x.lineWidth = 1;
  x.fillStyle = MUTED; x.font = '10px system-ui, sans-serif'; x.textBaseline = 'middle';
  for (let i = 0; i <= 3; i++) {
    const y = Math.round(padT + plotH - (plotH * i / 3)) + 0.5;
    x.beginPath(); x.moveTo(padL, y); x.lineTo(w - padR, y); x.stroke();
    x.textAlign = 'right';
    x.fillText(maxV ? billUSD(maxV * i / 3) : (i ? '' : '$0'), padL - 6, y);
  }

  // bars
  const n = s.months.length;
  const slot = plotW / n, bw = Math.max(3, Math.min(28, slot * 0.6));
  x.textAlign = 'center'; x.textBaseline = 'top';
  for (let i = 0; i < n; i++) {
    const cx = padL + slot * i + slot / 2;
    const bh = Math.max(vals[i] > 0 ? 2 : 0, Math.round(vals[i] * scale));
    if (bh) {
      x.fillStyle = COL;
      x.fillRect(Math.round(cx - bw / 2), Math.round(padT + plotH - bh), Math.round(bw), bh);
    }
    // label every other month when they would collide
    if (n <= 6 || i % 2 === (n - 1) % 2) {
      x.fillStyle = MUTED;
      x.fillText(String(s.months[i]).slice(2), cx, padT + plotH + 6);
    }
  }
}

// Redraw on resize so the canvas never ends up stretched or blurry.
window.addEventListener('resize', () => {
  if (document.getElementById('bills-chart')) _billsDrawChart();
});

/* ══════════════════════════════════════════════════════════════════════════
   💵 LEDGER — income in, purchases out, and the overview that nets them.
   Backend: app/routers/money/ledger.py

   Income Phase 1 generalized "paychecks" into any-kind income, additively —
   same `paychecks` table, plus income_type/currency/amount_native (manual entry
   ONLY this phase; no external APIs). This pane now drives on the superset:
     GET/POST      /api/ledger/income            every income row (+ ?type=,
                                                   per-type totals) / create
     PATCH/DELETE  /api/ledger/income/{id}
   /api/ledger/paychecks (below) still exists, byte-for-byte back-compat, and
   still backs the Export/Import CSV buttons (it has no WHERE clause, so it
   already round-trips every income row, just without the new columns):
     GET/POST      /api/ledger/paychecks         list (+ month/YTD totals) / create
     PATCH/DELETE  /api/ledger/paychecks/{id}
     GET/POST      /api/ledger/purchases         list (+ totals + category split) / create
     PATCH/DELETE  /api/ledger/purchases/{id}
     GET           /api/ledger/summary           month + YTD income/outgoings/net
     GET           /api/ledger/series?months=12  monthly income vs outgoings (chart)
     GET           /api/ledger/{paychecks|purchases}/export.csv
     POST          /api/ledger/{paychecks|purchases}/import   {csv: "..."}

   Money is integer cents everywhere, same as bills. Purchases are NON-BILL
   spending only: bill payments already live in the bills history, so entering a
   bill here too would double-count it in every total. Outgoings = purchases +
   bill payments, two sets that never overlap.

   Every fetch is failure tolerant — before a restart picks the router up these
   endpoints 404, and each section shows a calm empty/degraded state instead of
   throwing or blanking the pane.
   ══════════════════════════════════════════════════════════════════════════ */

const PAY_CYCLES = ['weekly', 'biweekly', 'semimonthly', 'monthly', 'irregular'];
// Keep in sync with INCOME_TYPES in app/routers/money/ledger.py.
const INCOME_TYPES = [
  ['paycheck', '&#128188; Paycheck'],
  ['sale', '&#128722; Sale'],
  ['refund', '&#8617;&#65039; Refund'],
  ['gift', '&#127873; Gift'],
  ['dividend', '&#128202; Dividend'],
  ['interest', '&#127974;&#65039; Interest'],
  ['crypto_receive', '&#8383; Crypto received'],
  ['payout', '&#128184; Payout'],
  ['other', '&#10024; Other'],
];
const _incomeTypeLabel = t => (INCOME_TYPES.find(([k]) => k === t) || [t, t || 'paycheck'])[1];

let _paychecks = null;      // GET /api/ledger/income   (null = did not answer)
let _payEditId = null;
let _purchases = null;      // GET /api/ledger/purchases   (null = did not answer)
let _purEditId = null;
let _ledgerSummary = null;  // GET /api/ledger/summary
let _ledgerSeries = null;   // GET /api/ledger/series

/* ── segmented control ───────────────────────────────────────────────────── */

function _ledgerSegHtml() {
  return `<div class="subtab-bar" id="ledger-segbar" style="margin-bottom:14px;">
    ${_LEDGER_SECTIONS.map(([k, label]) =>
      `<div class="subtab${_billSection === k ? ' active' : ''}"
            onclick="billSection('${k}')">${label}</div>`).join('')}
  </div>`;
}

function billSection(k) {
  if (!_LEDGER_SECTIONS.some(s => s[0] === k)) return;
  _billSection = k;
  billCloseForm();
  renderBills();
}
window.billSection = billSection;

/* ── shared bits ─────────────────────────────────────────────────────────── */

// A dollars string -> cents. Returns null when blank, NaN when unparseable.
function _ledgerCents(raw) {
  const s = String(raw === undefined || raw === null ? '' : raw).replace(/[$,\s]/g, '');
  if (s === '') return null;
  const v = parseFloat(s);
  return isFinite(v) ? Math.round(v * 100) : NaN;
}

function _ledgerVal(id) {
  return ((document.getElementById(id) || {}).value || '').trim();
}

function _ledgerHead(title, blurb, buttons) {
  return `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
      <div>
        <div style="font-weight:700;font-size:1.02rem;">${title}</div>
        <div style="color:var(--muted);font-size:.78rem;margin-top:2px;max-width:560px;line-height:1.5;">${blurb}</div>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">${buttons}</div>
    </div>`;
}

function _ledgerDegraded(what, retry) {
  return `
    <div class="empty" style="padding:40px 16px;">
      <div class="empty-icon">&#9888;&#65039;</div>
      <div style="font-weight:600;margin-bottom:4px;">${what} is not answering yet</div>
      <div style="color:var(--muted);font-size:.82rem;max-width:440px;margin:0 auto;line-height:1.6;">
        This part of the ledger is new. If the app was just updated it needs a restart to pick it up.
      </div>
      <div style="margin-top:10px;"><button class="btn-sm" onclick="${retry}">Retry</button></div>
    </div>`;
}

// Month key (YYYY-MM) -> "July 2026"
const _LEDGER_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                        'August', 'September', 'October', 'November', 'December'];
function _ledgerMonthLabel(ym) {
  const p = String(ym || '').split('-');
  const m = parseInt(p[1], 10);
  return (m >= 1 && m <= 12) ? `${_LEDGER_MONTHS[m - 1]} ${p[0]}` : String(ym || '');
}

/* ══ PAYCHECKS ═══════════════════════════════════════════════════════════ */

async function _payRender() {
  const el = document.getElementById('bill-section-body');
  if (!el) return;
  el.innerHTML = `<div class="empty"><div class="empty-icon">&#9203;</div>Loading income&#8230;</div>`;
  const r = await Promise.allSettled([api('/api/ledger/income?limit=500')]);
  if (!el.isConnected) return;
  _paychecks = r[0].status === 'fulfilled' ? r[0].value : null;
  el.innerHTML = _payHtml();
  payLoadImports();   // fills #pay-imports; failure-tolerant (section stays hidden pre-restart)
}

function _payHtml() {
  const head = _ledgerHead('&#128176; Income',
    `Money in, any kind: paychecks, sales, refunds, gifts, dividends, crypto received &mdash;
     whatever actually landed, and when. Hourly work can be entered as hours &times; your rate
     and the amount fills itself in
     ${hlp('The rate lives on each entry, so different jobs and clients can carry different rates. Besides manual entry, read-only importers (PayPal / Printify / on-chain) can pull income in — see the Income sources section below. They only ever record money IN; nothing here can spend.')}`,
    `<button class="btn-sm primary" onclick="payOpenForm()">&#10133; Add income</button>
     <button class="btn-sm" onclick="payExportCsv()">&#8595; Export CSV</button>
     <button class="btn-sm" onclick="payImportCsv()">&#8593; Import CSV</button>
     <button class="btn-sm" onclick="renderBills()">&#8635; Refresh</button>` ) +
    `<input type="file" id="pay-csv-file" accept=".csv,text/csv" style="display:none;"
            onchange="payImportPicked(this)">`;

  if (!_paychecks) return `${head}${_ledgerDegraded('Income', 'renderBills()')}<div id="bill-form-wrap"></div>`;

  const rows = _paychecks.income || [];
  if (!rows.length) {
    return `${head}
      <div class="empty" style="padding:46px 16px;">
        <div class="empty-icon">&#128176;</div>
        <div style="color:var(--muted);font-size:.86rem;max-width:460px;margin:0 auto;line-height:1.6;">
          Log every paycheck, sale, refund or other income here so the Overview can tell you what you
          actually earned this month and this year.</div>
        <div style="margin-top:12px;"><button class="btn-sm primary" onclick="payOpenForm()">&#10133; Add income</button></div>
      </div>
      <div id="pay-imports"></div>
      <div id="bill-form-wrap"></div>`;
  }

  const card = (icon, label, val, color, sub) => `
    <div class="stat-card">
      <div class="stat-label">${icon} ${label}</div>
      <div class="stat-val" style="font-size:1.6rem;color:${color};">${val}</div>
      <div style="font-size:.66rem;color:var(--muted);margin-top:5px;">${sub || '&nbsp;'}</div>
    </div>`;
  const n = c => `${c} entr${c === 1 ? 'y' : 'ies'}`;
  const strip = `<div class="stats-row" style="margin:16px 0;">
    ${card('&#128197;', 'This month', billUSD(_paychecks.month_cents), 'var(--green)', n(_paychecks.month_count || 0))}
    ${card('&#128200;', 'Year to date', billUSD(_paychecks.ytd_cents), 'var(--green)', n(_paychecks.ytd_count || 0))}
    ${card('&#127970;', 'Sources', (_paychecks.sources || []).length,
      'var(--text)', (_paychecks.sources || []).slice(0, 3).map(esc).join(', ') || 'none yet')}
  </div>`;

  // Small per-type breakdown (voided rows already excluded by the backend).
  const byType = (_paychecks.by_type || []).filter(t => t.total_cents || t.count);
  const breakdown = byType.length ? `
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin:0 0 16px;">
      ${byType.map(t => `
        <div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;
                     padding:6px 12px;font-size:.74rem;display:flex;gap:8px;align-items:baseline;">
          <span>${_incomeTypeLabel(t.income_type)}</span>
          <b style="color:var(--green);">${billUSD(t.total_cents)}</b>
          <span style="color:var(--muted);">${t.count}</span>
        </div>`).join('')}
    </div>` : '';

  const th = (t, extra) => `<th style="padding:7px 8px;${extra || ''}">${t}</th>`;
  const body = rows.map(p => {
    const hrs = (p.hours !== null && p.hours !== undefined)
      ? `${p.hours} h${p.hourly_rate_cents ? ` &times; ${billUSD(p.hourly_rate_cents)}` : ''}` : '';
    const extras = Object.keys(p.extra || {}).length;
    const voided = !!p.voided;
    return `<tr style="border-bottom:1px solid var(--border);${voided ? 'opacity:.5;' : ''}">
      <td style="padding:7px 8px;white-space:nowrap;">${esc(String(p.received_at || '').slice(0, 10))}</td>
      <td style="padding:7px 8px;">${_billChip(_incomeTypeLabel(p.income_type || 'paycheck'), 'var(--accent2)')}${voided ? ' <span style="font-size:.64rem;color:var(--muted);">voided</span>' : ''}</td>
      <td style="padding:7px 8px;">
        <b>${esc(p.source || '')}</b>
        ${p.notes ? `<div style="font-size:.66rem;color:var(--muted);">${esc(p.notes)}</div>` : ''}
        ${extras ? `<div style="font-size:.62rem;color:var(--muted);">${extras} custom field${extras === 1 ? '' : 's'}</div>` : ''}
      </td>
      <td style="padding:7px 8px;text-align:right;font-weight:600;color:var(--green);">${billUSD(p.amount_cents)}</td>
      <td style="padding:7px 8px;text-align:right;color:var(--muted);">${p.gross_cents ? billUSD(p.gross_cents) : ''}</td>
      <td style="padding:7px 8px;color:var(--muted);">${hrs}</td>
      <td style="padding:7px 8px;">${_billChip(p.cycle || 'irregular', 'var(--accent2)')}</td>
      <td style="padding:7px 8px;text-align:right;white-space:nowrap;">
        <button class="btn-sm" onclick="payOpenForm(${p.id})">&#9998;</button>
        <button class="btn-sm danger" onclick="payDelete(${p.id})">&#128465;</button>
      </td></tr>`;
  }).join('');

  return `${head}${strip}${breakdown}
    <div id="pay-imports"></div>
    <div id="bill-form-wrap"></div>
    <div style="font-weight:600;font-size:.86rem;margin:6px 0;">${rows.length} income entr${rows.length === 1 ? 'y' : 'ies'}, newest first</div>
    <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:.8rem;">
        <thead><tr style="color:var(--muted);text-align:left;border-bottom:1px solid var(--border);">
          ${th('Received')}${th('Type')}${th('Source')}${th('Net', 'text-align:right;')}${th('Gross', 'text-align:right;')}
          ${th('Hours')}${th('Cycle')}${th('', 'text-align:right;')}
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

function payOpenForm(id) {
  const wrap = document.getElementById('bill-form-wrap');
  if (!wrap) return;
  _payEditId = (id === undefined || id === null) ? null : id;
  const p = _payEditId ? (((_paychecks || {}).income || []).find(x => x.id === _payEditId) || {}) : {};
  const cycle = p.cycle || 'irregular';
  const itype = p.income_type || 'paycheck';

  wrap.innerHTML = `
    <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:16px;margin:12px 0;">
      <div style="font-weight:700;margin-bottom:12px;">${_payEditId ? '&#9998; Edit income' : '&#10133; New income'}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;">
        <div class="field" style="margin:0;">
          <label>Type ${hlp('What kind of income this is. Drives the per-type breakdown above.')}</label>
          <select id="pay-f-type">
            ${INCOME_TYPES.map(([k, label]) => `<option value="${k}"${itype === k ? ' selected' : ''}>${label}</option>`).join('')}
          </select>
        </div>
        <div class="field" style="margin:0;">
          <label>Source ${hlp('Who paid you — employer, client, marketplace, or person. Reused sources group your income in the totals.')}</label>
          <input type="text" id="pay-f-source" value="${esc(p.source || '')}" placeholder="employer, client, Etsy, PayPal…">
        </div>
        <div class="field" style="margin:0;">
          <label>Received</label>
          <input type="date" id="pay-f-date" value="${esc(String(p.received_at || '').slice(0, 10) || _billTodayISO())}">
        </div>
        <div class="field" style="margin:0;">
          <label>Hours ${hlp('Optional. Fill in hours and a rate, then press Fill to compute the amount.')}</label>
          <input type="number" id="pay-f-hours" min="0" step="0.25" placeholder="e.g. 12.5"
                 value="${(p.hours === null || p.hours === undefined) ? '' : p.hours}">
        </div>
        <div class="field" style="margin:0;">
          <label>Hourly rate ${hlp('Your rate for THIS job. Stored per entry, so every client can be different.')}</label>
          <input type="number" id="pay-f-rate" min="0" step="0.01" placeholder="per hour"
                 value="${(p.hourly_rate_cents === null || p.hourly_rate_cents === undefined) ? '' : (p.hourly_rate_cents / 100).toFixed(2)}">
        </div>
        <div class="field" style="margin:0;">
          <label>Amount (net) ${hlp('What actually landed, in USD. Leave blank and press Fill to take hours times rate.')}</label>
          <div style="display:flex;gap:6px;">
            <input type="number" id="pay-f-amount" min="0" step="0.01" placeholder="take-home"
                   value="${(p.amount_cents === null || p.amount_cents === undefined) ? '' : (p.amount_cents / 100).toFixed(2)}">
            <button class="btn-sm" onclick="payFillFromHours()" title="Set the amount to hours times rate">&#128290; Fill</button>
          </div>
        </div>
        <div class="field" style="margin:0;">
          <label>Gross ${hlp('Optional, before withholding. Only the net amount counts toward your totals.')}</label>
          <input type="number" id="pay-f-gross" min="0" step="0.01" placeholder="optional"
                 value="${(p.gross_cents === null || p.gross_cents === undefined) ? '' : (p.gross_cents / 100).toFixed(2)}">
        </div>
        <div class="field" style="margin:0;">
          <label>Expected cycle ${hlp('How often this income is expected. Descriptive only — nothing is scheduled for you.')}</label>
          <select id="pay-f-cycle">
            ${PAY_CYCLES.map(c => `<option value="${c}"${cycle === c ? ' selected' : ''}>${c}</option>`).join('')}
          </select>
        </div>
        <div class="field" style="margin:0;">
          <label>Notes</label>
          <input type="text" id="pay-f-notes" value="${esc(p.notes || '')}" placeholder="what the work was">
        </div>
      </div>

      <div style="margin-top:12px;">
        <div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;">
          Custom fields ${hlp('Anything else this entry needs: invoice number, job site, check number, order id.')}
        </div>
        <div id="pay-extra-rows"></div>
        <button class="btn-sm" onclick="billAddExtraRow('pay-extra-rows')">&#10133; custom field</button>
      </div>

      <div style="display:flex;gap:8px;margin-top:14px;">
        <button class="btn-sm primary" onclick="paySave()">&#128190; ${_payEditId ? 'Save' : 'Add income'}</button>
        <button class="btn-sm" onclick="billCloseForm()">Cancel</button>
      </div>
    </div>`;

  const rows = document.getElementById('pay-extra-rows');
  if (rows) rows.innerHTML = '';
  Object.entries(p.extra || {}).forEach(([k, v]) => billAddExtraRow('pay-extra-rows', k, v));
  wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
window.payOpenForm = payOpenForm;

function payFillFromHours() {
  const h = parseFloat(_ledgerVal('pay-f-hours'));
  const rate = _ledgerCents(_ledgerVal('pay-f-rate'));
  if (!isFinite(h) || h < 0 || rate === null || !isFinite(rate)) {
    toast('Enter both hours and an hourly rate first', 'error');
    return;
  }
  const box = document.getElementById('pay-f-amount');
  if (box) box.value = (Math.round(h * rate) / 100).toFixed(2);
  toast(`${h} h at ${billUSD(rate)} = ${billUSD(Math.round(h * rate))}`);
}
window.payFillFromHours = payFillFromHours;

async function paySave() {
  const source = _ledgerVal('pay-f-source');
  if (!source) { toast('Who paid you?', 'error'); return; }

  const hoursRaw = _ledgerVal('pay-f-hours');
  const hours = hoursRaw === '' ? null : parseFloat(hoursRaw);
  if (hours !== null && (!isFinite(hours) || hours < 0)) { toast('Hours must be a positive number', 'error'); return; }
  const rate = _ledgerCents(_ledgerVal('pay-f-rate'));
  if (rate !== null && (!isFinite(rate) || rate < 0)) { toast('Hourly rate must be a positive amount', 'error'); return; }
  let amount = _ledgerCents(_ledgerVal('pay-f-amount'));
  if (amount !== null && (!isFinite(amount) || amount < 0)) { toast('Amount must be a positive number', 'error'); return; }
  if (amount === null && hours !== null && rate !== null) amount = Math.round(hours * rate);
  if (amount === null) { toast('Enter an amount, or hours and a rate to compute one', 'error'); return; }
  const gross = _ledgerCents(_ledgerVal('pay-f-gross'));
  if (gross !== null && (!isFinite(gross) || gross < 0)) { toast('Gross must be a positive number', 'error'); return; }

  const payload = {
    source,
    income_type: _ledgerVal('pay-f-type') || 'paycheck',
    amount_cents: amount,
    gross_cents: gross,
    received_at: _ledgerVal('pay-f-date') || null,
    hours,
    hourly_rate_cents: rate,
    cycle: _ledgerVal('pay-f-cycle') || 'irregular',
    notes: _ledgerVal('pay-f-notes'),
    extra: _billCollectExtra('pay-extra-rows'),
  };
  try {
    if (_payEditId) {
      await api(`/api/ledger/income/${_payEditId}`, { method: 'PATCH', body: JSON.stringify(payload) });
      toast('Income saved');
    } else {
      await api('/api/ledger/income', { method: 'POST', body: JSON.stringify(payload) });
      toast('Income added');
    }
    billCloseForm();
    await renderBills();
  } catch (e) { toast(e.message || 'Could not save that income entry', 'error'); }
}
window.paySave = paySave;

async function payDelete(id) {
  if (!confirm('Delete this income entry from the ledger?')) return;
  try {
    await api(`/api/ledger/income/${id}`, { method: 'DELETE' });
    toast('Income entry deleted');
    await renderBills();
  } catch (e) { toast(e.message || 'Could not delete that income entry', 'error'); }
}
window.payDelete = payDelete;

function payExportCsv() { window.open(API + '/api/ledger/paychecks/export.csv', '_blank'); }
window.payExportCsv = payExportCsv;

function payImportCsv() {
  const f = document.getElementById('pay-csv-file');
  if (f) { f.value = ''; f.click(); }
}
window.payImportCsv = payImportCsv;

function payImportPicked(input) {
  _ledgerImportPicked(input, '/api/ledger/paychecks/import', 'paycheck');
}
window.payImportPicked = payImportPicked;

/* ── Income Phase 2: READ-ONLY importers (PayPal / Printify / on-chain) ─────
   Backend: app/income_import.py + routers/money/income_sources.py.
     GET  /api/income/import/status      per-source connected/last-run state
     POST /api/income/import/{source}    manual "Import now"
   Credentials save through the existing PATCH /api/settings (SECRET_KEYS
   auto-encrypts them at rest; saved secrets are never echoed back here — the
   inputs just show "(saved)"). The wallet address is PUBLIC — read-only
   visibility, never a key. The auto-import daemon is gated by the
   income_autoimport_enabled setting and defaults OFF; "Import now" always
   works. Nothing in this section can move money — money IN only. */

let _payImports = null;   // last GET /api/income/import/status payload

async function payLoadImports() {
  const el = document.getElementById('pay-imports');
  if (!el) return;
  try { _payImports = await api('/api/income/import/status'); }
  catch { el.innerHTML = ''; return; }   // pre-restart 404 → section simply absent
  el.innerHTML = _payImportsHtml(_payImports);
}
window.payLoadImports = payLoadImports;

function _payImpStatusLine(st) {
  if (!st) return '<span style="color:var(--muted);">&mdash;</span>';
  if (st.running) return '<span style="color:var(--accent2);">&#9203; importing&#8230;</span>';
  if (!st.last_run_at) return '<span style="color:var(--muted);">never imported</span>';
  const when = esc(String(st.last_run_at).slice(0, 16).replace('T', ' '));
  if (st.last_error) {
    return `<span style="color:var(--warn);" title="${esc(st.last_error)}">&#9888;&#65039; ${when} &mdash; ${esc(String(st.last_error).slice(0, 70))}</span>`;
  }
  return `<span style="color:var(--muted);">last ${when} &mdash; <b style="color:var(--green);">+${st.last_added || 0}</b> new of ${st.last_seen || 0} seen</span>`;
}

function _payImpChip(connected) {
  return connected
    ? '<span style="color:var(--green,#22c55e);font-weight:600;font-size:.72rem;">connected</span>'
    : '<span style="color:var(--muted);font-size:.72rem;">not connected</span>';
}

function _payImpCard(title, hint, chip, statusLine, fieldsHtml, source, canImport) {
  return `
    <div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:12px 14px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
        <div style="font-weight:600;font-size:.86rem;">${title} ${hlp(hint)} ${chip}</div>
        <div style="display:flex;gap:6px;align-items:center;">
          <button class="btn-sm" onclick="payImpSave('${source}')">&#128190; Save</button>
          <button class="btn-sm primary" id="pay-imp-run-${source}" onclick="payImpRun('${source}')" ${canImport ? '' : 'disabled'}
            title="Pull new income records from this source now (read-only; duplicates are skipped).">&#8595; Import now</button>
        </div>
      </div>
      <div style="font-size:.74rem;margin:6px 0 8px;">${statusLine}</div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;">${fieldsHtml}</div>
    </div>`;
}

function _payImportsHtml(d) {
  const s = (d || {}).sources || {};
  const pp = s.paypal || {}, pf = s.printify || {}, oc = s.onchain || {};
  const field = (label, inner) => `
    <div class="field" style="margin:0;flex:1;min-width:170px;">
      <label style="font-size:.7rem;">${label}</label>${inner}
    </div>`;
  const paypalFields =
    field('Client ID', `<input type="password" id="pay-imp-pp-id" placeholder="${pp.configured ? '(saved — enter to replace)' : 'PayPal REST client id'}">`) +
    field('Client secret', `<input type="password" id="pay-imp-pp-secret" placeholder="${pp.configured ? '(saved — enter to replace)' : 'PayPal REST secret'}">`) +
    `<div class="field" style="margin:0;width:110px;">
      <label style="font-size:.7rem;">Mode</label>
      <select id="pay-imp-pp-mode">
        <option value="live" ${pp.mode !== 'sandbox' ? 'selected' : ''}>live</option>
        <option value="sandbox" ${pp.mode === 'sandbox' ? 'selected' : ''}>sandbox</option>
      </select></div>`;
  const printifyFields =
    field('API key', `<input type="password" id="pay-imp-pf-key" placeholder="${pf.configured ? '(saved — enter to replace)' : 'Printify personal access token'}">`) +
    field('Shop ID', `<input type="text" id="pay-imp-pf-shop" value="${esc(pf.shop_id || '')}" placeholder="blank = first shop">`);
  const onchainFields =
    field('Public wallet address', `<input type="text" id="pay-imp-oc-addr" value="${esc(oc.address || '')}" placeholder="bc1q… / 0x…">`) +
    `<div class="field" style="margin:0;width:110px;">
      <label style="font-size:.7rem;">Chain</label>
      <select id="pay-imp-oc-chain">
        <option value="btc" ${oc.chain !== 'eth' ? 'selected' : ''}>BTC</option>
        <option value="eth" ${oc.chain === 'eth' ? 'selected' : ''}>ETH</option>
      </select></div>`;
  return `
    <details class="settings-group" style="margin:0 0 16px;">
      <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#8595; Income sources
        <span style="font-size:.72rem;color:var(--muted);font-weight:400;">(read-only importers &mdash; money in only)</span>
      </summary>
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0 10px;">
        <label style="display:flex;gap:6px;align-items:center;cursor:pointer;font-size:.78rem;"
          title="OFF (default): sources are only pulled when you press Import now. ON: a background daemon re-imports each connected source on the interval. Importing only READS from each source and records income rows — it can never spend, withdraw or transfer.">
          <input type="checkbox" ${d && d.enabled ? 'checked' : ''} onchange="payImpToggle(this.checked)">
          Auto-import every ${d ? d.interval_min : 360} min
        </label>
        <span style="font-size:.7rem;color:var(--muted);">Duplicates are skipped automatically (each record is remembered by its transaction id).</span>
      </div>
      <div style="display:flex;flex-direction:column;gap:10px;">
        ${_payImpCard('&#128179; PayPal',
          'Pulls COMPLETED incoming USD transactions from the PayPal Reporting API (last ~30 days) and records the net (after PayPal fees) as sale income. Needs a REST app client id + secret from developer.paypal.com. Read-only: the reporting API cannot move money.',
          _payImpChip(pp.configured), _payImpStatusLine(pp), paypalFields, 'paypal', !!pp.configured)}
        ${_payImpCard('&#128085; Printify',
          'Pulls recent orders from the Printify API and records the per-order net (retail total minus production cost) as sale income. Uses the importer key if set, else the existing Printify key from Settings. Read-only.',
          _payImpChip(pf.configured), _payImpStatusLine(pf), printifyFields, 'printify', !!pf.configured)}
        ${_payImpCard('&#9939; On-chain',
          'Watches ONE public wallet address via a public block explorer (mempool.space for BTC, Blockscout for ETH — no API key) and records confirmed incoming transactions as crypto income, valued at the USD spot price at import time. The address is public read-only data — no private key is ever stored here.',
          _payImpChip(oc.configured), _payImpStatusLine(oc), onchainFields, 'onchain', !!oc.configured)}
      </div>
    </details>`;
}

async function payImpToggle(on) {
  try {
    await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ income_autoimport_enabled: on ? '1' : '0' }) });
    toast(on ? '⏱️ Auto-import ON — connected sources pull on the interval'
             : 'Auto-import OFF — Import now still works anytime');
    await payLoadImports();
  } catch (e) { toast('Could not change auto-import: ' + e.message, 'error'); await payLoadImports(); }
}
window.payImpToggle = payImpToggle;

async function payImpSave(source) {
  const v = id => ((document.getElementById(id) || {}).value || '').trim();
  const body = {};
  if (source === 'paypal') {
    const id = v('pay-imp-pp-id'), sec = v('pay-imp-pp-secret');
    if (id) body.paypal_client_id = id;          // never blank out a saved secret
    if (sec) body.paypal_client_secret = sec;
    body.paypal_mode = v('pay-imp-pp-mode') || 'live';
  } else if (source === 'printify') {
    const key = v('pay-imp-pf-key');
    if (key) body.printify_api_key = key;
    body.printify_shop_id = v('pay-imp-pf-shop');
  } else if (source === 'onchain') {
    body.income_wallet_address = v('pay-imp-oc-addr');
    body.income_wallet_chain = v('pay-imp-oc-chain') || 'btc';
  } else return;
  try {
    await api('/api/settings', { method: 'PATCH', body: JSON.stringify(body) });
    toast('💾 Saved — credentials are encrypted at rest');
    await payLoadImports();
  } catch (e) { toast('Save failed: ' + e.message, 'error'); }
}
window.payImpSave = payImpSave;

async function payImpRun(source) {
  const btn = document.getElementById(`pay-imp-run-${source}`);
  if (btn) { btn.disabled = true; btn.innerHTML = '⏳ Importing…'; }
  try {
    const r = await api(`/api/income/import/${source}`, { method: 'POST', body: JSON.stringify({}) });
    if (r.error) toast(`${source}: ${r.error}`, 'error');
    else toast(`⬇️ ${source}: ${r.added || 0} new income row${(r.added || 0) === 1 ? '' : 's'} (${r.seen || 0} seen)`);
    if (r.added) { await renderBills(); return; }   // re-render shows the new rows + totals
  } catch (e) { toast(`${source} import failed: ` + e.message, 'error'); }
  if (btn) { btn.disabled = false; btn.innerHTML = '&#8595; Import now'; }
  await payLoadImports();
}
window.payImpRun = payImpRun;

/* ══ PURCHASES ═══════════════════════════════════════════════════════════ */

async function _purRender() {
  const el = document.getElementById('bill-section-body');
  if (!el) return;
  el.innerHTML = `<div class="empty"><div class="empty-icon">&#9203;</div>Loading purchases&#8230;</div>`;
  const r = await Promise.allSettled([api('/api/ledger/purchases?limit=500')]);
  if (!el.isConnected) return;
  _purchases = r[0].status === 'fulfilled' ? r[0].value : null;
  el.innerHTML = _purHtml();
  const first = document.getElementById('pur-q-merchant');
  if (first) first.focus();
}

function _purHtml() {
  const head = _ledgerHead('&#128722; Purchases',
    `Everyday spending that is <b>not</b> a bill &mdash; groceries, materials, gas, one-off buys.
     Bill payments are already tracked under Bills, so leave them out of here
     ${hlp('Entering a bill as a purchase too would count it twice in the Overview. Outgoings are purchases plus bill payments.')}`,
    `<button class="btn-sm" onclick="purExportCsv()">&#8595; Export CSV</button>
     <button class="btn-sm" onclick="purImportCsv()">&#8593; Import CSV</button>
     <button class="btn-sm" onclick="renderBills()">&#8635; Refresh</button>`) +
    `<input type="file" id="pur-csv-file" accept=".csv,text/csv" style="display:none;"
            onchange="purImportPicked(this)">`;

  if (!_purchases) return `${head}${_ledgerDegraded('Purchases', 'renderBills()')}<div id="bill-form-wrap"></div>`;

  // Quick-add: one row, date already set to today, Enter saves from any field.
  const cats = Array.from(new Set([
    'Gas/Fuel',   // always offered — what 📷 Snap a receipt sets for fuel purchases
    ...((_purchases.month_categories || []).map(c => c.cat).filter(c => c && c !== 'uncategorized')),
    ...(_bills || []).map(b => b.category).filter(Boolean),
  ])).sort();
  const quick = `
    <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:12px;margin:14px 0;">
      <div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;">
        Quick add ${hlp('Built for speed: the date is already today, and Enter in any box saves the row.')}
      </div>
      <div style="display:grid;grid-template-columns:130px 1.4fr 110px 1fr auto;gap:8px;align-items:end;">
        <div class="field" style="margin:0;"><label>Date</label>
          <input type="date" id="pur-q-date" value="${_billTodayISO()}" onkeydown="purQuickKey(event)"></div>
        <div class="field" style="margin:0;"><label>Merchant</label>
          <input type="text" id="pur-q-merchant" placeholder="where you spent it" onkeydown="purQuickKey(event)"></div>
        <div class="field" style="margin:0;"><label>Amount</label>
          <input type="number" id="pur-q-amount" min="0" step="0.01" placeholder="0.00" onkeydown="purQuickKey(event)"></div>
        <div class="field" style="margin:0;"><label>Category</label>
          <input type="text" id="pur-q-category" list="pur-cat-list" placeholder="optional" onkeydown="purQuickKey(event)">
          <datalist id="pur-cat-list">${cats.map(c => `<option value="${esc(c)}"></option>`).join('')}</datalist></div>
        <button class="btn-sm primary" onclick="purQuickAdd()" style="margin-bottom:2px;">&#10133; Add</button>
      </div>
      <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">
        <button class="btn-sm" onclick="purOpenForm()">More fields&#8230;</button>
        <button class="btn-sm" onclick="purSnapReceipt()">&#128247; Snap a receipt</button>
      </div>
    </div>`;

  const rows = _purchases.purchases || [];
  if (!rows.length) {
    return `${head}${quick}
      <div id="bill-form-wrap"></div>
      <div class="empty" style="padding:36px 16px;">
        <div class="empty-icon">&#128722;</div>
        <div style="color:var(--muted);font-size:.86rem;max-width:460px;margin:0 auto;line-height:1.6;">
          Nothing logged yet. Add non-bill spending above and it groups itself by month and category.</div>
      </div>`;
  }

  const card = (icon, label, val, color, sub) => `
    <div class="stat-card">
      <div class="stat-label">${icon} ${label}</div>
      <div class="stat-val" style="font-size:1.6rem;color:${color};">${val}</div>
      <div style="font-size:.66rem;color:var(--muted);margin-top:5px;">${sub || '&nbsp;'}</div>
    </div>`;
  const topCat = (_purchases.month_categories || [])[0];
  const strip = `<div class="stats-row" style="margin:16px 0;">
    ${card('&#128197;', 'This month', billUSD(_purchases.month_cents), 'var(--warn)',
      `${_purchases.month_count || 0} purchase${(_purchases.month_count || 0) === 1 ? '' : 's'}`)}
    ${card('&#128200;', 'Year to date', billUSD(_purchases.ytd_cents), 'var(--warn)',
      `${_purchases.ytd_count || 0} purchase${(_purchases.ytd_count || 0) === 1 ? '' : 's'}`)}
    ${card('&#127991;&#65039;', 'Top category', topCat ? esc(topCat.cat) : '—', 'var(--text)',
      topCat ? `${billUSD(topCat.total)} this month` : 'nothing this month')}
  </div>`;

  const catBars = (_purchases.month_categories || []).length ? `
    <div style="margin:6px 0 16px;">
      <div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;">
        This month by category</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        ${_purchases.month_categories.map(c => `<span style="font-size:.72rem;background:var(--surface);
          border:1px solid var(--border);border-radius:8px;padding:4px 9px;">
          <b>${esc(c.cat)}</b> ${billUSD(c.total)} <span style="color:var(--muted);">&times;${c.n}</span></span>`).join('')}
      </div>
    </div>` : '';

  // Group the table by month, with a per-month subtotal header.
  const groups = [];
  rows.forEach(p => {
    const ym = String(p.purchased_at || '').slice(0, 7);
    let g = groups[groups.length - 1];
    if (!g || g.ym !== ym) { g = { ym, items: [], total: 0 }; groups.push(g); }
    g.items.push(p);
    g.total += p.amount_cents || 0;
  });

  const body = groups.map(g => `
    <tr style="background:var(--surface);">
      <td colspan="4" style="padding:7px 8px;font-weight:600;font-size:.78rem;">${esc(_ledgerMonthLabel(g.ym))}</td>
      <td colspan="2" style="padding:7px 8px;text-align:right;font-weight:600;font-size:.78rem;color:var(--warn);">
        ${billUSD(g.total)} &middot; ${g.items.length} item${g.items.length === 1 ? '' : 's'}</td>
    </tr>
    ${g.items.map(p => `<tr style="border-bottom:1px solid var(--border);">
      <td style="padding:7px 8px;white-space:nowrap;">${esc(String(p.purchased_at || '').slice(0, 10))}</td>
      <td style="padding:7px 8px;"><b>${esc(p.merchant || '')}</b>
        ${p.item_count ? `<button class="btn-sm" title="see the line items" onclick="event.stopPropagation();purShowItems(${p.id})"
             style="margin-left:6px;padding:1px 6px;font-size:.62rem;">&#129534; ${p.item_count}</button>` : ''}
        ${p.notes ? `<div style="font-size:.66rem;color:var(--muted);">${esc(p.notes)}</div>` : ''}</td>
      <td style="padding:7px 8px;">${p.category ? _billChip(p.category, 'var(--accent2)') : ''}</td>
      <td style="padding:7px 8px;color:var(--muted);">${esc(p.method || '')}</td>
      <td style="padding:7px 8px;text-align:right;font-weight:600;">${billUSD(p.amount_cents)}</td>
      <td style="padding:7px 8px;text-align:right;white-space:nowrap;">
        <button class="btn-sm" onclick="purOpenForm(${p.id})">&#9998;</button>
        <button class="btn-sm danger" onclick="purDelete(${p.id})">&#128465;</button>
      </td></tr>`).join('')}`).join('');

  const th = (t, extra) => `<th style="padding:7px 8px;${extra || ''}">${t}</th>`;
  return `${head}${strip}${quick}
    <div id="bill-form-wrap"></div>
    ${catBars}
    <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:.8rem;">
        <thead><tr style="color:var(--muted);text-align:left;border-bottom:1px solid var(--border);">
          ${th('Date')}${th('Merchant')}${th('Category')}${th('Method')}
          ${th('Amount', 'text-align:right;')}${th('', 'text-align:right;')}
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

function purQuickKey(ev) {
  if (ev && ev.key === 'Enter') { ev.preventDefault(); purQuickAdd(); }
}
window.purQuickKey = purQuickKey;

async function purQuickAdd() {
  const merchant = _ledgerVal('pur-q-merchant');
  if (!merchant) { toast('Where did you spend it?', 'error'); return; }
  const amount = _ledgerCents(_ledgerVal('pur-q-amount'));
  if (amount === null || !isFinite(amount) || amount < 0) { toast('Enter an amount like 24.99', 'error'); return; }
  try {
    await api('/api/ledger/purchases', {
      method: 'POST',
      body: JSON.stringify({
        merchant,
        amount_cents: amount,
        purchased_at: _ledgerVal('pur-q-date') || _billTodayISO(),
        category: _ledgerVal('pur-q-category'),
      }),
    });
    toast(`${merchant} ${billUSD(amount)} logged`);
    await renderBills();
  } catch (e) { toast(e.message || 'Could not log that purchase', 'error'); }
}
window.purQuickAdd = purQuickAdd;

function purOpenForm(id) {
  const wrap = document.getElementById('bill-form-wrap');
  if (!wrap) return;
  _purEditId = (id === undefined || id === null) ? null : id;
  const p = _purEditId ? (((_purchases || {}).purchases || []).find(x => x.id === _purEditId) || {}) : {};

  wrap.innerHTML = `
    <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:16px;margin:12px 0;">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
        <div style="font-weight:700;">${_purEditId ? '&#9998; Edit purchase' : '&#10133; New purchase'}</div>
        ${_purEditId ? '' : '<button class="btn-sm" onclick="purSnapReceipt()">&#128247; Snap a receipt</button>'}
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;">
        <div class="field" style="margin:0;"><label>Date</label>
          <input type="date" id="pur-f-date" value="${esc(String(p.purchased_at || '').slice(0, 10) || _billTodayISO())}"></div>
        <div class="field" style="margin:0;"><label>Merchant</label>
          <input type="text" id="pur-f-merchant" value="${esc(p.merchant || '')}" placeholder="where you spent it"></div>
        <div class="field" style="margin:0;"><label>Amount</label>
          <input type="number" id="pur-f-amount" min="0" step="0.01" placeholder="0.00"
                 value="${(p.amount_cents === null || p.amount_cents === undefined) ? '' : (p.amount_cents / 100).toFixed(2)}"></div>
        <div class="field" style="margin:0;">
          <label>Category ${hlp('Free text, and it shares the same vocabulary as your bill categories so totals line up.')}</label>
          <input type="text" id="pur-f-category" value="${esc(p.category || '')}" list="pur-cat-list" placeholder="groceries"></div>
        <div class="field" style="margin:0;">
          <label>Payment method ${hlp('However you paid: card, cash, transfer. Free text.')}</label>
          <input type="text" id="pur-f-method" value="${esc(p.method || '')}" placeholder="card"></div>
        <div class="field" style="margin:0;"><label>Notes</label>
          <input type="text" id="pur-f-notes" value="${esc(p.notes || '')}" placeholder="what it was for"></div>
      </div>

      ${_purItemsEditorHtml()}

      <div style="margin-top:12px;">
        <div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;">
          Custom fields ${hlp('Anything else worth keeping: receipt number, job it belongs to, warranty date.')}
        </div>
        <div id="pur-extra-rows"></div>
        <button class="btn-sm" onclick="billAddExtraRow('pur-extra-rows')">&#10133; custom field</button>
      </div>

      <div style="display:flex;gap:8px;margin-top:14px;">
        <button class="btn-sm primary" onclick="purSave()">&#128190; ${_purEditId ? 'Save' : 'Add purchase'}</button>
        <button class="btn-sm" onclick="billCloseForm()">Cancel</button>
      </div>
    </div>`;

  const rows = document.getElementById('pur-extra-rows');
  if (rows) rows.innerHTML = '';
  Object.entries(p.extra || {}).forEach(([k, v]) => billAddExtraRow('pur-extra-rows', k, v));

  // Remembered items + their measured cadences load in the background: the form
  // is usable immediately and the autocomplete/hints fill in a moment later, so a
  // slow (or pre-restart, 404-ing) budget API never blocks logging a purchase.
  _purLoadSuggest().then(() => {
    const dl = document.getElementById('pur-item-list');
    if (dl) dl.innerHTML = _purSuggest.map(s => `<option value="${esc(s.name)}"></option>`).join('');
    if (_purEditId) {
      api(`/api/ledger/purchases/${_purEditId}/items`)
        .then(r => (r.items || []).forEach(it => purAddItemRow(it)))
        .catch(() => {});
    }
  });
  wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
window.purOpenForm = purOpenForm;

/* ── 📷 Snap a receipt — first consumer of the reusable docExtract() widget ────
   (static/js/doc-extract.js, backend app/docextract.py schema 'receipt'). Opens
   the purchase form (so the pur-f-* fields exist), then a camera/file picker;
   on success pre-fills merchant/date/amount/method/category and any line items.
   The manual quick-add / form flow is untouched — this only ever fills fields in,
   never submits on its own, so the user still reviews before saving. */
function purSnapReceipt() {
  if (!document.getElementById('pur-f-merchant')) purOpenForm();
  docExtract({
    schema: 'receipt',
    onResult: purApplyExtract,
    onError: (e) => toast('Could not read that receipt: ' + (e.message || e), 'error'),
  });
}
window.purSnapReceipt = purSnapReceipt;

function purApplyExtract(data) {
  data = data || {};
  const set = (id, v) => {
    const el = document.getElementById(id);
    if (el && v !== undefined && v !== null && v !== '') el.value = v;
  };
  set('pur-f-merchant', data.merchant);
  if (data.date) set('pur-f-date', data.date);
  if (typeof data.total === 'number' && isFinite(data.total)) set('pur-f-amount', data.total.toFixed(2));
  set('pur-f-method', data.payment_method);
  const catEl = document.getElementById('pur-f-category');
  if (catEl) catEl.value = data.is_fuel ? 'Gas/Fuel' : (catEl.value || (data.category || ''));

  const noteBits = [];
  if (typeof data.tax === 'number' && isFinite(data.tax) && data.tax > 0) noteBits.push(`tax ${billUSD(Math.round(data.tax * 100))}`);
  if (data.is_fuel && typeof data.gallons === 'number' && isFinite(data.gallons)) noteBits.push(`${data.gallons} gal`);
  if (noteBits.length) {
    const notesEl = document.getElementById('pur-f-notes');
    if (notesEl) notesEl.value = notesEl.value ? `${notesEl.value} — ${noteBits.join(', ')}` : noteBits.join(', ');
  }

  (data.line_items || []).forEach(li => {
    const isFuel = /gas|fuel/i.test(li.category || '');
    purAddItemRow({
      name: (isFuel ? '⛽ ' : '') + (li.description || 'item'),
      qty: li.qty || 1,
      unit: '',
      unit_price_cents: typeof li.unit_price === 'number' ? Math.round(li.unit_price * 100)
        : (typeof li.amount === 'number' ? Math.round(li.amount * 100) : undefined),
    });
  });
  purItemsRecalc();
  toast('Receipt fields filled in — check them, then save');
}
window.purApplyExtract = purApplyExtract;

async function purSave() {
  const merchant = _ledgerVal('pur-f-merchant');
  if (!merchant) { toast('Where did you spend it?', 'error'); return; }
  const amount = _ledgerCents(_ledgerVal('pur-f-amount'));
  const items = purCollectItems();
  // An itemised trip may leave Amount blank — the backend sums the lines. Only
  // when there are NO lines is an explicit amount still required.
  if (amount !== null && (!isFinite(amount) || amount < 0)) { toast('Enter an amount like 24.99', 'error'); return; }
  if (amount === null && !items.length) { toast('Enter an amount like 24.99, or add line items', 'error'); return; }
  const payload = {
    merchant,
    amount_cents: amount,
    purchased_at: _ledgerVal('pur-f-date') || _billTodayISO(),
    category: _ledgerVal('pur-f-category'),
    method: _ledgerVal('pur-f-method'),
    notes: _ledgerVal('pur-f-notes'),
    extra: _billCollectExtra('pur-extra-rows'),
    items,
  };
  if (amount === null) delete payload.amount_cents;
  try {
    if (_purEditId) {
      await api(`/api/ledger/purchases/${_purEditId}`, { method: 'PATCH', body: JSON.stringify(payload) });
      toast('Purchase saved');
    } else {
      await api('/api/ledger/purchases', { method: 'POST', body: JSON.stringify(payload) });
      toast('Purchase added');
    }
    billCloseForm();
    await renderBills();
  } catch (e) { toast(e.message || 'Could not save that purchase', 'error'); }
}
window.purSave = purSave;

async function purDelete(id) {
  if (!confirm('Delete this purchase from the ledger?')) return;
  try {
    await api(`/api/ledger/purchases/${id}`, { method: 'DELETE' });
    toast('Purchase deleted');
    await renderBills();
  } catch (e) { toast(e.message || 'Could not delete that purchase', 'error'); }
}
window.purDelete = purDelete;

function purExportCsv() { window.open(API + '/api/ledger/purchases/export.csv', '_blank'); }
window.purExportCsv = purExportCsv;

function purImportCsv() {
  const f = document.getElementById('pur-csv-file');
  if (f) { f.value = ''; f.click(); }
}
window.purImportCsv = purImportCsv;

function purImportPicked(input) {
  _ledgerImportPicked(input, '/api/ledger/purchases/import', 'purchase');
}
window.purImportPicked = purImportPicked;

/* Shared CSV import handler for both ledger sections (same shape as bills'). */
function _ledgerImportPicked(input, url, noun) {
  const file = input && input.files && input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const r = await api(url, { method: 'POST', body: JSON.stringify({ csv: String(reader.result || '') }) });
      const errs = (r && r.errors) || [];
      toast(`Imported ${r.imported || 0} ${noun}${(r.imported || 0) === 1 ? '' : 's'}` +
        (errs.length ? ` — ${errs.length} row${errs.length === 1 ? '' : 's'} skipped` : ''),
        errs.length ? 'warn' : 'success');
      if (errs.length) errs.slice(0, 3).forEach(m => toast(m, 'warn'));
      await renderBills();
    } catch (e) { toast(e.message || 'Import failed', 'error'); }
  };
  reader.onerror = () => toast('Could not read that file', 'error');
  reader.readAsText(file);
}

/* ══ OVERVIEW — income vs outgoings ══════════════════════════════════════ */

async function _ovRender() {
  const el = document.getElementById('bill-section-body');
  if (!el) return;
  el.innerHTML = `<div class="empty"><div class="empty-icon">&#9203;</div>Loading overview&#8230;</div>`;
  const [sum, series] = await Promise.allSettled([
    api('/api/ledger/summary'),
    api('/api/ledger/series?months=12'),
  ]);
  if (!el.isConnected) return;
  _ledgerSummary = sum.status === 'fulfilled' ? sum.value : null;
  _ledgerSeries = series.status === 'fulfilled' ? series.value : null;
  el.innerHTML = _ovHtml();
  _ovDrawChart();
}

function _ovScopeHtml(title, s) {
  if (!s) return '';
  const net = s.net_cents || 0;
  const netColor = net >= 0 ? 'var(--green)' : 'var(--red)';
  const line = (label, val, color, note) => `
    <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:6px 0;
                border-bottom:1px solid var(--border);">
      <div style="font-size:.8rem;">${label}${note ? `<div style="font-size:.66rem;color:var(--muted);">${note}</div>` : ''}</div>
      <div style="font-weight:600;color:${color};white-space:nowrap;">${val}</div>
    </div>`;
  return `
    <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:14px;">
      <div style="font-weight:700;font-size:.9rem;margin-bottom:8px;">${title}</div>
      ${line('Income', billUSD(s.income_cents), 'var(--green)',
        `${s.income_count || 0} paycheck${(s.income_count || 0) === 1 ? '' : 's'}`)}
      ${line('Purchases', '&minus;' + billUSD(s.purchases_cents), 'var(--warn)',
        `${s.purchases_count || 0} non-bill purchase${(s.purchases_count || 0) === 1 ? '' : 's'}`)}
      ${line('Bill payments', '&minus;' + billUSD(s.bill_payments_cents), 'var(--warn)',
        `${s.bill_payments_count || 0} payment${(s.bill_payments_count || 0) === 1 ? '' : 's'} logged under Bills`)}
      ${line('<b>Outgoings</b>', '&minus;' + billUSD(s.outgoings_cents), 'var(--text)', 'purchases plus bill payments')}
      <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding-top:10px;">
        <div style="font-size:.86rem;font-weight:700;">Net</div>
        <div style="font-size:1.5rem;font-weight:700;color:${netColor};white-space:nowrap;">
          ${net < 0 ? '&minus;' : ''}${billUSD(Math.abs(net))}</div>
      </div>
      <div style="font-size:.66rem;color:var(--muted);">${net >= 0 ? 'kept' : 'short'} after everything went out</div>
    </div>`;
}

function _ovHtml() {
  const head = _ledgerHead('&#128202; Overview',
    `What came in against what went out. Outgoings are your purchases plus the bill payments
     logged under Bills &mdash; each one counted exactly once
     ${hlp('Bill payments live in the bills history and purchases hold only non-bill spending, so the two never overlap.')}`,
    `<button class="btn-sm" onclick="renderBills()">&#8635; Refresh</button>`);

  if (!_ledgerSummary) return `${head}${_ledgerDegraded('The overview', 'renderBills()')}`;

  const m = _ledgerSummary.month || {};
  const y = _ledgerSummary.ytd || {};
  const anything = (m.income_cents || m.outgoings_cents || y.income_cents || y.outgoings_cents);
  if (!anything) {
    return `${head}
      <div class="empty" style="padding:46px 16px;">
        <div class="empty-icon">&#128202;</div>
        <div style="color:var(--muted);font-size:.86rem;max-width:470px;margin:0 auto;line-height:1.6;">
          Nothing to net out yet. Add income and purchases, or mark a bill paid, and this
          becomes your month-by-month picture of income against everything going out.</div>
        <div style="margin-top:12px;display:flex;gap:6px;justify-content:center;">
          <button class="btn-sm primary" onclick="billSection('paychecks')">&#128176; Add income</button>
          <button class="btn-sm" onclick="billSection('purchases')">&#128722; Log a purchase</button>
        </div>
      </div>`;
  }

  return `${head}
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin:16px 0;">
      ${_ovScopeHtml(`&#128197; ${esc(_ledgerMonthLabel(_ledgerSummary.month_key || _ledgerSummary.month))}`, m)}
      ${_ovScopeHtml(`&#128200; ${esc(_ledgerSummary.year || '')} year to date`, y)}
    </div>
    <div style="margin-top:22px;">
      <div style="font-weight:600;font-size:.86rem;margin-bottom:2px;">&#128200; Income vs outgoings by month</div>
      <div style="font-size:.72rem;color:var(--muted);margin-bottom:8px;">
        Green is money in, amber is money out (purchases plus bill payments), over the last 12 months.</div>
      <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:12px;">
        <canvas id="ledger-chart" height="200" style="width:100%;height:200px;display:block;"></canvas>
        <div id="ledger-chart-note" style="font-size:.7rem;color:var(--muted);margin-top:6px;"></div>
      </div>
    </div>`;
}

/* Paired bars per month, same plain-canvas approach as the bills chart. */
function _ovDrawChart() {
  const cv = document.getElementById('ledger-chart');
  if (!cv) return;
  const note = document.getElementById('ledger-chart-note');
  const s = _ledgerSeries;
  if (!s || !Array.isArray(s.months) || !s.months.length) {
    if (note) note.textContent = 'Chart data unavailable.';
    return;
  }
  const inc = (s.income_cents || []).map(v => v || 0);
  const out = (s.outgoings_cents || []).map(v => v || 0);
  const maxV = Math.max(...inc, ...out, 0);
  if (!maxV) {
    if (note) note.textContent = 'Nothing logged yet — the chart fills in as you add paychecks, purchases and bill payments.';
  } else {
    const net = inc.reduce((a, b) => a + b, 0) - out.reduce((a, b) => a + b, 0);
    const up = inc.map((v, i) => v > out[i]).filter(Boolean).length;
    if (note) note.textContent = `${billUSD(Math.abs(net))} ${net >= 0 ? 'ahead' : 'behind'} over these ` +
      `${s.months.length} months · ${up} month${up === 1 ? '' : 's'} in the black · peak ${billUSD(maxV)}.`;
  }

  const css = getComputedStyle(document.documentElement);
  const cvar = (n, fb) => (css.getPropertyValue(n) || '').trim() || fb;
  const GREEN = cvar('--green', '#22c55e'), WARN = cvar('--warn', '#f59e0b');
  const MUTED = cvar('--muted', '#64748b'), BORDER = cvar('--border', '#2a2f3d');

  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(240, cv.clientWidth || 600), h = 200;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  const x = cv.getContext('2d');
  if (!x) return;
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  x.clearRect(0, 0, w, h);

  const padL = 52, padR = 8, padT = 10, padB = 22;
  const plotW = w - padL - padR, plotH = h - padT - padB;
  const scale = maxV > 0 ? plotH / maxV : 0;

  x.strokeStyle = BORDER; x.lineWidth = 1;
  x.fillStyle = MUTED; x.font = '10px system-ui, sans-serif'; x.textBaseline = 'middle';
  for (let i = 0; i <= 3; i++) {
    const y = Math.round(padT + plotH - (plotH * i / 3)) + 0.5;
    x.beginPath(); x.moveTo(padL, y); x.lineTo(w - padR, y); x.stroke();
    x.textAlign = 'right';
    x.fillText(maxV ? billUSD(maxV * i / 3) : (i ? '' : '$0'), padL - 6, y);
  }

  const n = s.months.length;
  const slot = plotW / n, bw = Math.max(2, Math.min(12, slot * 0.32));
  x.textAlign = 'center'; x.textBaseline = 'top';
  for (let i = 0; i < n; i++) {
    const cx = padL + slot * i + slot / 2;
    const pair = [[inc[i], GREEN, -bw - 1], [out[i], WARN, 1]];
    pair.forEach(([v, col, dx]) => {
      const bh = Math.max(v > 0 ? 2 : 0, Math.round(v * scale));
      if (!bh) return;
      x.fillStyle = col;
      x.fillRect(Math.round(cx + dx), Math.round(padT + plotH - bh), Math.round(bw), bh);
    });
    if (n <= 6 || i % 2 === (n - 1) % 2) {
      x.fillStyle = MUTED;
      x.fillText(String(s.months[i]).slice(2), cx, padT + plotH + 6);
    }
  }
}

// Keep the overview chart crisp on resize, same as the bills chart above.
window.addEventListener('resize', () => {
  if (document.getElementById('ledger-chart')) _ovDrawChart();
});

/* ══════════════════════════════════════════════════════════════════════════
   🗓️ CALENDAR — the month grid over everything the ledger tracks.
   Backend: app/routers/money/calendar.py
     GET  /api/calendar/events?from=&to=   bills due (real + projected),
                                           bill payments, paychecks, purchases
     GET  /api/calendar/export.ics         one-off download
     GET  /api/calendar/feed               the subscription URL + its token
     POST /api/calendar/feed/rotate        new token; every old URL dies
     GET  /api/public/calendar.ics?token=  the feed Nextcloud subscribes to

   There was a calendar icon on this pane for months and no calendar. This is it.
   Recurring bills are PROJECTED forward by the backend (same cycle logic that
   mark-paid uses) and drawn dimmer than real rows, so an extrapolated due date
   never looks like a fact. Pure DOM + inline styles, no calendar library, same
   house style as the rest of the pane — and like every other section it degrades
   to a calm "not answering yet" card when the endpoint 404s before a restart.
   ══════════════════════════════════════════════════════════════════════════ */

let _calYM = null;        // {y, m} — the visible month (m is 1-12). null = this month
let _calData = null;      // GET /api/calendar/events   (null = did not answer)
let _calSelDay = null;    // YYYY-MM-DD of the open day detail, or null
let _calFeed = null;      // GET /api/calendar/feed     (lazy — only when opened)
let _calFeedOpen = false;

/* type → colour + label. Bill due dates shift colour with their state so an
   overdue row reads red on the grid without opening anything. */
const _CAL_TYPES = {
  bill_due:  { label: 'Bill due',    color: 'var(--warn)' },
  bill_paid: { label: 'Bill paid',   color: 'var(--accent)' },
  paycheck:  { label: 'Paycheck',    color: 'var(--green)' },
  purchase:  { label: 'Purchase',    color: 'var(--muted)' },
  // Budget layer (routers/money/budget.py). These are DERIVED, not money that
  // moved — the strip below excludes them from every total so a restock guess can
  // never land in a spending figure.
  budget_period:  { label: 'Budget period', color: 'var(--accent2)' },
  savings_target: { label: 'Savings target', color: 'var(--green)' },
  safe_to_spend:  { label: 'Safe to spend', color: 'var(--accent)' },
  restock:        { label: 'Predicted restock', color: 'var(--accent2)' },
  grocery_day:    { label: 'Suggested shopping day', color: 'var(--accent2)' },
};
// Kept in step with calendar.BUDGET_EVENT_TYPES on the backend.
const _CAL_BUDGET_TYPES = ['budget_period', 'savings_target', 'safe_to_spend',
                           'restock', 'grocery_day'];
const _CAL_DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function _calEventColor(e) {
  if (e.type === 'bill_due') {
    if (e.state === 'overdue' || e.state === 'due_today') return 'var(--red)';
    if (e.state === 'paid') return 'var(--muted)';
    return 'var(--warn)';
  }
  return (_CAL_TYPES[e.type] || {}).color || 'var(--muted)';
}

/* ── date maths (plain YYYY-MM-DD strings; no Date-parsing timezone traps) ── */

function _calISO(y, m, d) {
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

function _calCurYM() {
  if (_calYM) return _calYM;
  const p = _billTodayISO().split('-');
  return { y: +p[0], m: +p[1] };
}

function _calDaysInMonth(y, m) { return new Date(y, m, 0).getDate(); }

/* The 6x7 grid always starts on the Sunday on/before the 1st, so the month
   sits in a stable rectangle and neighbouring days stay visible. */
function _calGrid(y, m) {
  const first = new Date(y, m - 1, 1);
  const start = new Date(y, m - 1, 1 - first.getDay());
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    cells.push({
      iso: _calISO(d.getFullYear(), d.getMonth() + 1, d.getDate()),
      day: d.getDate(),
      inMonth: (d.getMonth() + 1) === m && d.getFullYear() === y,
    });
  }
  return cells;
}

function _calShiftMonth(delta) {
  const c = _calCurYM();
  let y = c.y, m = c.m + delta;
  while (m < 1) { m += 12; y -= 1; }
  while (m > 12) { m -= 12; y += 1; }
  _calYM = { y, m };
  _calSelDay = null;
  _calRender();
}
window.calPrevMonth = () => _calShiftMonth(-1);
window.calNextMonth = () => _calShiftMonth(1);
window.calToday = () => { _calYM = null; _calSelDay = null; _calRender(); };

/* ── load + render ───────────────────────────────────────────────────────── */

async function _calRender() {
  const el = document.getElementById('bill-section-body');
  if (!el) return;
  el.innerHTML = `<div class="empty"><div class="empty-icon">&#9203;</div>Loading calendar&#8230;</div>`;

  const { y, m } = _calCurYM();
  const cells = _calGrid(y, m);
  const r = await Promise.allSettled([
    api(`/api/calendar/events?from=${cells[0].iso}&to=${cells[cells.length - 1].iso}`),
  ]);
  if (!el.isConnected) return;   // user moved on mid-fetch
  _calData = r[0].status === 'fulfilled' ? r[0].value : null;

  el.innerHTML = _calHtml();
}

/* All events for the visible grid, bucketed by day. */
function _calByDay() {
  const out = {};
  ((_calData && _calData.events) || []).forEach(e => {
    (out[e.date] = out[e.date] || []).push(e);
  });
  return out;
}

function _calHtml() {
  const { y, m } = _calCurYM();
  const head = _ledgerHead('&#128467;&#65039; Calendar',
    `Everything with a date on it in one month view &mdash; bills due, payments made,
     paychecks in and purchases out. Future bill dates are worked out from each
     bill's cycle and drawn faded, because they have not happened yet
     ${hlp('A recurring bill stores only its next due date. The projected occurrences after that use the same cycle and month-end rules as marking it paid, so a bill due the 31st shows Feb 28 rather than skipping February.')}`,
    `<button class="btn-sm" onclick="calSubscribe()">&#128225; Subscribe / export</button>
     <button class="btn-sm" onclick="renderBills()">&#8635; Refresh</button>`);

  if (!_calData) return `${head}${_ledgerDegraded('The calendar', 'renderBills()')}`;

  const byDay = _calByDay();
  const today = _billTodayISO();
  const cells = _calGrid(y, m);

  const nav = `
    <div style="display:flex;align-items:center;gap:8px;margin:16px 0 10px;flex-wrap:wrap;">
      <button class="btn-sm" onclick="calPrevMonth()" title="Previous month">&#8592;</button>
      <div style="font-weight:700;font-size:1.05rem;min-width:190px;text-align:center;">
        ${esc(_ledgerMonthLabel(_calISO(y, m, 1).slice(0, 7)))}</div>
      <button class="btn-sm" onclick="calNextMonth()" title="Next month">&#8594;</button>
      <button class="btn-sm" onclick="calToday()">Today</button>
      <div style="flex:1;"></div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;font-size:.68rem;color:var(--muted);">
        ${Object.keys(_CAL_TYPES).map(k => `<span style="display:inline-flex;align-items:center;gap:4px;">
          <span style="width:9px;height:9px;border-radius:50%;background:${_CAL_TYPES[k].color};
                       display:inline-block;"></span>${_CAL_TYPES[k].label}</span>`).join('')}
      </div>
    </div>`;

  const dowRow = `
    <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:4px;margin-bottom:4px;">
      ${_CAL_DOW.map(d => `<div style="font-size:.66rem;color:var(--muted);text-transform:uppercase;
        letter-spacing:.05em;text-align:center;padding:2px 0;">${d}</div>`).join('')}
    </div>`;

  const cellHtml = c => {
    const evs = byDay[c.iso] || [];
    const isToday = c.iso === today;
    const isSel = c.iso === _calSelDay;
    const shown = evs.slice(0, 3);
    const more = evs.length - shown.length;
    const border = isSel ? 'var(--accent)' : (isToday ? 'var(--accent2)' : 'var(--border)');
    return `
      <div onclick="calSelectDay('${c.iso}')" title="${evs.length} item${evs.length === 1 ? '' : 's'}"
           style="min-height:88px;background:${c.inMonth ? 'var(--surface2)' : 'var(--surface)'};
                  border:1px solid ${border};border-radius:9px;padding:5px 5px 4px;cursor:pointer;
                  opacity:${c.inMonth ? 1 : .45};overflow:hidden;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;">
          <span style="font-size:.72rem;font-weight:${isToday ? 800 : 600};
                       color:${isToday ? 'var(--accent2)' : 'var(--text)'};">${c.day}</span>
          ${evs.length ? `<span style="font-size:.58rem;color:var(--muted);">${evs.length}</span>` : ''}
        </div>
        ${shown.map(e => _calChip(e)).join('')}
        ${more > 0 ? `<div style="font-size:.58rem;color:var(--muted);padding-left:2px;">+${more} more</div>` : ''}
      </div>`;
  };

  const grid = `
    <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:4px;">
      ${cells.map(cellHtml).join('')}
    </div>`;

  return `${head}${_calStrip()}${nav}${dowRow}${grid}${_calDayDetail()}${_calFeedHtml()}`;
}

/* One compact chip inside a day cell: colour bar, name, amount. */
function _calChip(e) {
  const col = _calEventColor(e);
  const amt = (e.amount_cents === null || e.amount_cents === undefined) ? '~' : billUSD(e.amount_cents);
  return `<div style="display:flex;align-items:center;gap:3px;font-size:.6rem;line-height:1.5;
      border-left:3px solid ${col};padding-left:4px;margin-bottom:2px;white-space:nowrap;
      overflow:hidden;text-overflow:ellipsis;${e.projected ? 'opacity:.62;font-style:italic;' : ''}">
    <span style="overflow:hidden;text-overflow:ellipsis;flex:1;">${esc(e.title)}</span>
    <span style="color:${col};font-weight:600;">${amt}</span>
  </div>`;
}

/* Month total strip: what came in against what went out, plus what is still due. */
function _calStrip() {
  const t = (_calData && _calData.totals) || {};
  const { y, m } = _calCurYM();
  // Totals from the API cover the whole 6-week grid; re-total the month itself so
  // the strip matches the month in the heading, not the neighbouring spill days.
  const key = _calISO(y, m, 1).slice(0, 7);
  let inc = 0, out = 0, due = 0, n = 0;
  ((_calData && _calData.events) || []).forEach(e => {
    if (String(e.date).slice(0, 7) !== key) return;
    // Budget markers restate figures the real rows already carry (the period's
    // income, a savings target). Summing them here would show double.
    if (_CAL_BUDGET_TYPES.indexOf(e.type) !== -1) return;
    n++;
    const a = e.amount_cents || 0;
    if (e.type === 'bill_due') due += a;
    else if (e.direction === 'in') inc += a;
    else out += a;
  });
  const net = inc - out;
  const card = (icon, label, val, color, sub) => `
    <div class="stat-card">
      <div class="stat-label">${icon} ${label}</div>
      <div class="stat-val" style="font-size:1.5rem;color:${color};">${val}</div>
      <div style="font-size:.66rem;color:var(--muted);margin-top:5px;">${sub || '&nbsp;'}</div>
    </div>`;
  return `<div class="stats-row" style="margin:16px 0 0;">
    ${card('&#128176;', 'In this month', billUSD(inc), 'var(--green)', 'paychecks received')}
    ${card('&#128722;', 'Out this month', '&minus;' + billUSD(out), 'var(--warn)', 'purchases plus bill payments')}
    ${card(net >= 0 ? '&#9989;' : '&#9888;&#65039;', 'Net', (net < 0 ? '&minus;' : '') + billUSD(Math.abs(net)),
      net >= 0 ? 'var(--green)' : 'var(--red)', `${n} dated item${n === 1 ? '' : 's'}`)}
    ${card('&#128198;', 'Bills due', billUSD(due), 'var(--text)', 'scheduled, incl. projected')}
  </div>`;
}

function calSelectDay(iso) {
  _calSelDay = (_calSelDay === iso) ? null : iso;
  const el = document.getElementById('bill-section-body');
  if (el) el.innerHTML = _calHtml();
  const d = document.getElementById('cal-day-detail');
  if (d && _calSelDay) d.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
window.calSelectDay = calSelectDay;

/* The open day's full list — this is where actions live (the grid stays clean). */
function _calDayDetail() {
  if (!_calSelDay) return '';
  const evs = (_calByDay()[_calSelDay] || []);
  const pretty = (() => {
    const p = _calSelDay.split('-').map(Number);
    return `${_CAL_DOW[new Date(p[0], p[1] - 1, p[2]).getDay()]}, ${_ledgerMonthLabel(_calSelDay.slice(0, 7)).replace(/ \d{4}$/, '')} ${p[2]}, ${p[0]}`;
  })();

  const body = evs.length ? evs.map(e => {
    const col = _calEventColor(e);
    const isBudget = _CAL_BUDGET_TYPES.indexOf(e.type) !== -1;
    const meta = [_CAL_TYPES[e.type] ? _CAL_TYPES[e.type].label : e.type,
                  e.category || null,
                  e.projected ? (isBudget ? 'predicted from your history — not a fact'
                                          : 'projected from its cycle') : null,
                  e.type === 'bill_due' && e.autopay ? 'autopay' : null,
                  e.notes || null].filter(Boolean).join(' · ');
    const action = (e.type === 'bill_due' && !e.projected && e.state !== 'paid')
      ? `<button class="btn-sm primary" onclick="calMarkPaid(${e.bill_id}, ${JSON.stringify(e.title).replace(/"/g, '&quot;')}, ${e.amount_cents === null || e.amount_cents === undefined ? 'null' : e.amount_cents})">&#10004; Mark paid</button>`
      : '';
    return `
      <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);">
        <span style="width:4px;align-self:stretch;background:${col};border-radius:2px;"></span>
        <div style="flex:1;min-width:0;">
          <div style="font-weight:600;font-size:.86rem;${e.projected ? 'opacity:.75;font-style:italic;' : ''}">${esc(e.title)}</div>
          <div style="font-size:.68rem;color:var(--muted);">${esc(meta)}</div>
        </div>
        <div style="font-weight:700;color:${col};white-space:nowrap;">
          ${isBudget
            ? (e.amount_cents === null || e.amount_cents === undefined ? '&mdash;' : billUSD(e.amount_cents))
            : `${e.direction === 'in' ? '+' : (e.type === 'bill_due' ? '' : '&minus;')}${e.amount_cents === null || e.amount_cents === undefined ? 'varies' : billUSD(e.amount_cents)}`}</div>
        ${action}
      </div>`;
  }).join('') : `<div style="color:var(--muted);font-size:.82rem;padding:10px 0;">Nothing on this day.</div>`;

  return `
    <div id="cal-day-detail" style="margin-top:16px;background:var(--surface2);border:1px solid var(--border);
                border-radius:12px;padding:14px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:4px;">
        <div style="font-weight:700;font-size:.95rem;">${esc(pretty)}</div>
        <button class="btn-sm" onclick="calSelectDay('${_calSelDay}')">&times; Close</button>
      </div>
      ${body}
    </div>`;
}

/* Mark-paid from the calendar. Self-contained (the bills list is not loaded in
   this section), but it posts to exactly the same endpoint as the Bills table. */
async function calMarkPaid(billId, name, amountCents) {
  let amount_cents = amountCents;
  if (amount_cents === null || amount_cents === undefined) {
    const raw = prompt(`How much was the ${name} bill this time? (dollars)`, '');
    if (raw === null) return;
    const v = parseFloat(String(raw).replace(/[$,\s]/g, ''));
    if (!isFinite(v) || v < 0) { toast('Enter a dollar amount like 84.20', 'error'); return; }
    amount_cents = Math.round(v * 100);
  }
  try {
    const r = await api(`/api/bills/${billId}/pay`, {
      method: 'POST',
      body: JSON.stringify({ amount_cents, paid_at: _billTodayISO() }),
    });
    const nxt = r && r.bill && r.bill.next_due;
    toast(`${name} marked paid${nxt ? ` — next due ${nxt}` : ''}`);
    await _calRender();
  } catch (e) { toast(e.message || 'Could not mark that paid', 'error'); }
}
window.calMarkPaid = calMarkPaid;

/* ── subscribe / export panel ────────────────────────────────────────────── */

async function calSubscribe() {
  _calFeedOpen = !_calFeedOpen;
  if (_calFeedOpen && !_calFeed) {
    try { _calFeed = await api('/api/calendar/feed'); }
    catch (e) { _calFeed = { error: e.message || 'not available yet' }; }
  }
  const el = document.getElementById('bill-section-body');
  if (el) el.innerHTML = _calHtml();
  const p = document.getElementById('cal-feed-panel');
  if (p) p.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
window.calSubscribe = calSubscribe;

/* Same-origin URL, because the feed should be reachable on the LAN and nowhere
   else. The backend also returns a public-hostname form; we deliberately show
   the one the browser is already talking to. */
function _calFeedUrl() {
  if (!_calFeed || !_calFeed.path) return '';
  return window.location.origin + _calFeed.path;
}

function _calFeedHtml() {
  if (!_calFeedOpen) return '';
  if (!_calFeed || _calFeed.error) {
    return `
      <div id="cal-feed-panel" style="margin-top:16px;background:var(--surface2);border:1px solid var(--border);
                  border-radius:12px;padding:14px;">
        <div style="font-weight:700;margin-bottom:4px;">&#128225; Subscribe / export</div>
        <div style="color:var(--muted);font-size:.8rem;">
          The feed endpoint is not answering yet${_calFeed && _calFeed.error ? ` (${esc(_calFeed.error)})` : ''}.
          If the app was just updated it needs a restart to pick it up.</div>
      </div>`;
  }
  const url = _calFeedUrl();
  return `
    <div id="cal-feed-panel" style="margin-top:16px;background:var(--surface2);border:1px solid var(--border);
                border-radius:12px;padding:14px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
        <div style="font-weight:700;">&#128225; Subscribe / export</div>
        <button class="btn-sm" onclick="calSubscribe()">&times; Close</button>
      </div>
      <div style="color:var(--muted);font-size:.78rem;margin:4px 0 12px;line-height:1.6;">
        A subscription keeps updating by itself; the download is a snapshot you import once.
      </div>

      <div style="font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:5px;">
        Subscription link</div>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
        <input type="text" id="cal-feed-url" readonly value="${esc(url)}"
               onclick="this.select()" style="flex:1;min-width:260px;font-family:monospace;font-size:.72rem;">
        <button class="btn-sm primary" onclick="calCopyFeed()">&#128203; Copy</button>
        <button class="btn-sm" onclick="calDownloadIcs()">&#8595; Download .ics</button>
        <button class="btn-sm" onclick="calRotateToken()">&#8635; Rotate token</button>
      </div>

      <div style="font-size:.76rem;color:var(--muted);margin-top:10px;line-height:1.7;">
        <b style="color:var(--text);">Nextcloud:</b> Calendar &rarr; <i>+ New calendar</i> &rarr;
        <i>Add calendar from link (subscription)</i>, paste the link, done &mdash; it refreshes itself.
        Thunderbird, Apple Calendar, GNOME Calendar and DAVx&#8309; take the same link
        ${hlp('Any app that supports an iCalendar / webcal subscription URL will work. The feed carries recurring bills as real recurrence rules, so your calendar app fills in future months on its own.')}
      </div>

      <div style="margin-top:12px;border:1px solid var(--warn);border-radius:9px;padding:10px 12px;
                  background:rgba(245,158,11,.08);font-size:.76rem;line-height:1.65;">
        <b style="color:var(--warn);">&#9888;&#65039; This link is the password.</b>
        Anyone who opens it can read every bill, paycheck and purchase amount in here &mdash;
        no login is asked for. Keep it on your local network, do not paste it into anything
        public, and hit <b>Rotate token</b> if it ever gets out (that instantly kills every
        copy of the old link, including ones already added to a calendar app).
      </div>
    </div>`;
}

function calCopyFeed() {
  const url = _calFeedUrl();
  if (!url) return;
  const done = () => toast('Feed link copied — keep it on your local network');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(done).catch(() => {
      const el = document.getElementById('cal-feed-url');
      if (el) { el.select(); document.execCommand('copy'); done(); }
    });
  } else {
    const el = document.getElementById('cal-feed-url');
    if (el) { el.select(); document.execCommand('copy'); done(); }
  }
}
window.calCopyFeed = calCopyFeed;

function calDownloadIcs() { window.open(API + '/api/calendar/export.ics', '_blank'); }
window.calDownloadIcs = calDownloadIcs;

async function calRotateToken() {
  if (!confirm('Rotate the calendar token?\n\nEvery copy of the current link stops working '
             + 'immediately — including calendars already subscribed to it, which you will '
             + 'need to re-add with the new link.')) return;
  try {
    _calFeed = await api('/api/calendar/feed/rotate', { method: 'POST' });
    toast('Token rotated — the old link is dead');
    const el = document.getElementById('bill-section-body');
    if (el) el.innerHTML = _calHtml();
  } catch (e) { toast(e.message || 'Could not rotate the token', 'error'); }
}
window.calRotateToken = calRotateToken;

/* ══════════════════════════════════════════════════════════════════════════
   🧮 BUDGET · 📈 INSIGHTS · 🛒 PLAN — the AI budget + grocery planner.
   Backend: app/routers/money/budget.py
     GET  /api/budget/period               the pay period: income basis, committed
                                           bills, envelopes, safe-to-spend
     GET/POST /api/budget/config           pay cycle + anchor (candidates derived
                                           from recorded paychecks; you confirm)
     GET/POST/PATCH/DELETE /api/budget/envelopes
     GET  /api/budget/consumption?sort=    every item + its cadence (discovery)
     GET  /api/budget/consumption/item?name=   one item's full series (the chart)
     GET  /api/budget/categories           per-category spend per month
     GET  /api/budget/items/suggest?q=     autocomplete from your own history
     POST /api/budget/plan                 queue an AI grocery list (advisory)
     GET/PATCH /api/budget/plans[/{id}]    · POST .../accept · .../reject
     POST /api/budget/plans/{id}/purchase  pre-fill a real purchase (after accept)
     POST /api/budget/toggles              feature switches
     GET/POST /api/budget/sample[/seed|/purge]   clearly-tagged demo data

   THE RULE THIS UI FOLLOWS: never render a number the backend did not compute.
   When the API says `insufficient_data`, this shows what is missing and how many
   more purchases are needed — it does not fill the gap with a dash that looks
   like a measurement or a trend line through two points. Predicted values are
   always visually marked as predictions.

   Charts are hand-drawn on canvas in the same style as the bills and overview
   charts above — no chart library, and the same var(--…) theme colours.
   ══════════════════════════════════════════════════════════════════════════ */

let _budPeriod = null;    // GET /api/budget/period   (null = did not answer)
let _budEnvs = null;      // GET /api/budget/envelopes
let _budSetupOpen = false;
let _budEnvFormId = null; // null = closed, 0 = adding, >0 = editing that envelope

let _insData = null;      // GET /api/budget/consumption
let _insCats = null;      // GET /api/budget/categories
let _insSort = 'spend';
let _insItem = null;      // the item whose chart is open (full stats object)

let _plans = null;        // GET /api/budget/plans
let _planBusy = false;

/* Small shared bits ------------------------------------------------------- */

// "$12.34" or an explicit "not enough data" marker — never a bare 0 standing in
// for a figure the backend refused to compute.
function _budAmt(c, dash) {
  return (c === null || c === undefined) ? `<span style="color:var(--muted);">${dash || '—'}</span>` : billUSD(c);
}

function _budConfChip(conf) {
  const map = { high: ['var(--green)', 'high confidence'], medium: ['var(--warn)', 'medium confidence'],
                low: ['var(--red)', 'low confidence'], none: ['var(--muted)', 'no prediction'] };
  const [col, label] = map[conf] || map.none;
  return _billChip(label, col);
}

function _budBar(pct, color) {
  const w = Math.max(0, Math.min(100, pct || 0));
  return `<div style="height:8px;background:var(--surface);border-radius:5px;overflow:hidden;border:1px solid var(--border);">
    <div style="height:100%;width:${w}%;background:${color};transition:width .25s;"></div></div>`;
}

// The one place "we do not have enough data" is rendered, so it always reads the
// same way and always says what would fix it.
function _budNeedCard(title, message, needs) {
  const list = (needs || []).length
    ? `<ul style="text-align:left;max-width:420px;margin:10px auto 0;padding-left:18px;
         color:var(--muted);font-size:.78rem;line-height:1.7;">
        ${needs.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : '';
  return `<div class="empty" style="padding:34px 16px;">
      <div class="empty-icon">&#128209;</div>
      <div style="font-weight:600;margin-bottom:6px;">${title}</div>
      <div style="color:var(--muted);font-size:.82rem;max-width:460px;margin:0 auto;line-height:1.6;">
        ${message}</div>
      ${list}
    </div>`;
}

/* ══ 🧮 BUDGET ═══════════════════════════════════════════════════════════ */

async function _budRender() {
  const el = document.getElementById('bill-section-body');
  if (!el) return;
  el.innerHTML = `<div class="empty"><div class="empty-icon">&#9203;</div>Working out this period&#8230;</div>`;
  const [per, envs] = await Promise.allSettled([
    api('/api/budget/period'),
    api('/api/budget/envelopes'),
  ]);
  if (!el.isConnected) return;
  _budPeriod = per.status === 'fulfilled' ? per.value : null;
  _budEnvs = envs.status === 'fulfilled' ? (envs.value.envelopes || []) : null;
  el.innerHTML = _budHtml();
}

function _budHtml() {
  const head = _ledgerHead('&#129518; Budget',
    `Envelopes for food, gas and savings over your real pay period, with what is
     already committed to bills and what is genuinely safe to spend
     ${hlp('Every figure here is computed from paychecks, bills and purchases you actually recorded. Where there is not enough data, it says so instead of guessing.')}`,
    `<button class="btn-sm" onclick="budToggleSetup()">&#9881;&#65039; Setup</button>
     <button class="btn-sm" onclick="budEnvForm(0)">&#10133; Envelope</button>
     <button class="btn-sm" onclick="renderBills()">&#8635; Refresh</button>`);

  if (!_budPeriod) return `${head}${_ledgerDegraded('The budget', 'renderBills()')}`;

  const p = _budPeriod.period || {};
  const inc = _budPeriod.income || {};
  const setup = _budSetupOpen ? _budSetupHtml() : '';
  const envForm = _budEnvFormId === null ? '' : _budEnvHtml();

  if (_budPeriod.status !== 'ok') {
    return `${head}${setup}${envForm}
      ${_budNeedCard('Not enough recorded yet to budget this period',
        'The budget will not guess at your income. Once these are in place every figure below fills itself in.',
        _budPeriod.needs)}
      ${_budSampleHtml()}`;
  }

  const card = (icon, label, val, color, sub) => `
    <div class="stat-card">
      <div class="stat-label">${icon} ${label}</div>
      <div class="stat-val" style="font-size:1.5rem;color:${color};">${val}</div>
      <div style="font-size:.66rem;color:var(--muted);margin-top:5px;">${sub || '&nbsp;'}</div>
    </div>`;

  const parts = _budPeriod.safe_to_spend_parts || {};
  const safeCol = (_budPeriod.safe_to_spend_cents || 0) < 0 ? 'var(--red)' : 'var(--green)';
  const perDay = _budPeriod.safe_to_spend_per_day_cents;

  const strip = `<div class="stats-row" style="margin:16px 0;">
    ${card('&#128176;', 'Income this period', _budAmt(inc.basis_cents),
      inc.basis === 'projected' ? 'var(--warn)' : 'var(--green)',
      inc.basis === 'projected' ? 'projected — no paycheck recorded yet'
                                : `${inc.recorded_count || 0} paycheck(s) recorded`)}
    ${card('&#128198;', 'Committed to bills', _budAmt(_budPeriod.committed.cents), 'var(--warn)',
      `${_budPeriod.committed.count} bill(s)` +
      (_budPeriod.committed.unknown_count ? ` · ${_budPeriod.committed.unknown_count} varies` : ''))}
    ${card('&#128179;', 'Spent so far', billUSD(_budPeriod.spend.total_cents), 'var(--warn)',
      'purchases only — bills counted above')}
    ${card('&#9989;', 'Safe to spend', _budAmt(_budPeriod.safe_to_spend_cents), safeCol,
      perDay !== null && perDay !== undefined
        ? `${billUSD(perDay)}/day for ${p.days_left} more day(s)` : 'rest of this period')}
  </div>`;

  // The safe-to-spend arithmetic, written out. The owner should be able to check
  // this figure against his own head without trusting the app.
  const maths = `
    <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;
                padding:12px 14px;margin-bottom:16px;font-size:.76rem;color:var(--muted);line-height:1.9;">
      <b style="color:var(--text);">How safe-to-spend is worked out</b>
      ${hlp('No hidden maths. Each number below comes straight from rows you recorded.')}
      <div style="font-family:ui-monospace,monospace;margin-top:6px;">
        ${billUSD(parts.income_basis_cents)} income (${esc(inc.basis)})
        &minus; ${billUSD(parts.less_committed_cents)} bills due
        &minus; ${billUSD(parts.less_savings_target_cents)} savings target
        &minus; ${billUSD(parts.less_spent_cents)} already spent
        = <b style="color:${safeCol};">${billUSD(_budPeriod.safe_to_spend_cents)}</b>
      </div>
      ${inc.projection_note ? `<div style="margin-top:4px;">${esc(inc.projection_note)}</div>` : ''}
    </div>`;

  const pctElapsed = p.days_total ? Math.round(p.days_elapsed / p.days_total * 100) : 0;
  const periodBar = `
    <div style="margin-bottom:16px;">
      <div style="display:flex;justify-content:space-between;font-size:.75rem;margin-bottom:5px;">
        <span><b>${esc(p.start)}</b> &rarr; <b>${esc(p.end)}</b>
          <span style="color:var(--muted);">· ${esc(p.cycle)}${p.configured ? '' : ' (not confirmed)'}</span></span>
        <span style="color:var(--muted);">day ${p.days_elapsed} of ${p.days_total}</span>
      </div>
      ${_budBar(pctElapsed, 'var(--accent2)')}
    </div>`;

  const envs = _budPeriod.envelopes || [];
  const envHtml = envs.length ? envs.map(e => {
    const alloc = e.allocation_cents;
    const over = e.over;
    const pct = e.pct_used === null || e.pct_used === undefined ? 0 : e.pct_used;
    const col = over ? 'var(--red)' : (pct > 85 ? 'var(--warn)' : 'var(--accent)');
    const kindLabel = e.kind === 'percent' ? `${e.percent}% of income` : 'fixed';
    return `
      <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;
                  padding:12px 14px;margin-bottom:9px;">
        <div style="display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap;">
          <div style="font-weight:700;">${esc(e.category)}
            <span style="font-size:.68rem;color:var(--muted);font-weight:400;">${esc(kindLabel)}</span></div>
          <div style="font-size:.82rem;">
            <b style="color:${col};">${billUSD(e.spent_cents)}</b>
            <span style="color:var(--muted);"> of ${_budAmt(alloc, 'not set')}</span>
          </div>
        </div>
        <div style="margin:7px 0 5px;">${_budBar(pct, col)}</div>
        <div style="display:flex;justify-content:space-between;font-size:.7rem;color:var(--muted);gap:8px;flex-wrap:wrap;">
          <span>${alloc === null ? 'needs an income figure before this can allocate'
                                 : (over ? `over by ${billUSD(-e.remaining_cents)}`
                                         : `${billUSD(e.remaining_cents)} left`)}</span>
          <span>
            <button class="btn-sm" onclick="budEnvForm(${e.id})">&#9998;</button>
            <button class="btn-sm danger" onclick="budEnvDelete(${e.id})">&#128465;</button>
          </span>
        </div>
      </div>`;
  }).join('') : `<div class="empty" style="padding:26px 16px;">
      <div class="empty-icon">&#129518;</div>
      <div style="color:var(--muted);font-size:.84rem;">No envelopes yet. Add one for food, gas and savings
        and this period will start tracking against them.</div>
      <div style="margin-top:10px;"><button class="btn-sm primary" onclick="budEnvForm(0)">&#10133; Add envelope</button></div>
    </div>`;

  const bills = (_budPeriod.committed.items || []).slice(0, 12);
  const billList = bills.length ? `
    <div style="margin-top:18px;">
      <div style="font-weight:600;font-size:.86rem;margin-bottom:6px;">&#128198; Bills in this period</div>
      <div style="font-size:.72rem;color:var(--muted);margin-bottom:8px;">${esc(_budPeriod.committed.note)}</div>
      ${bills.map(b => `<div style="display:flex;justify-content:space-between;gap:10px;padding:5px 0;
            border-bottom:1px solid var(--border);font-size:.79rem;${b.projected ? 'opacity:.7;font-style:italic;' : ''}">
          <span>${esc(b.date)} · ${esc(b.title)}${b.projected ? ' <span style="font-size:.66rem;">(projected)</span>' : ''}</span>
          <span style="font-weight:600;">${b.amount_cents === null ? 'varies' : billUSD(b.amount_cents)}</span>
        </div>`).join('')}
    </div>` : '';

  return `${head}${setup}${envForm}${strip}${maths}${periodBar}
    <div style="font-weight:600;font-size:.86rem;margin-bottom:8px;">Envelopes</div>
    ${envHtml}${billList}${_budSampleHtml()}`;
}

/* ── setup: pay cycle + toggles + sample data ───────────────────────────── */

function budToggleSetup() {
  _budSetupOpen = !_budSetupOpen;
  const el = document.getElementById('bill-section-body');
  if (el) el.innerHTML = _budHtml();
}
window.budToggleSetup = budToggleSetup;

function _budSetupHtml() {
  const cand = (_budPeriod && _budPeriod.cycle_candidates) || {};
  const p = (_budPeriod && _budPeriod.period) || {};
  const tog = (_budPeriod && _budPeriod.toggles) || {};
  const cycles = ['weekly', 'biweekly', 'semimonthly', 'monthly', 'irregular'];
  const cur = p.cycle && p.cycle !== 'calendar-month' ? p.cycle : '';

  // Suggestions, clearly labelled as suggestions — the owner confirms, nothing
  // is auto-applied from a guess about his pay.
  const suggest = cand.status === 'ok'
    ? `Your paychecks land about every <b>${cand.median_gap_days}</b> days, which looks
       <b>${esc(cand.observed || '')}</b>. Confirm it below if that is right.`
    : `<span style="color:var(--muted);">${esc(cand.message || 'Record a few paychecks and a cycle can be suggested from them.')}</span>`;

  const toggleRow = (key, label, help) => `
    <label style="display:flex;align-items:center;gap:8px;font-size:.79rem;margin-top:7px;cursor:pointer;">
      <input type="checkbox" ${tog[key] ? 'checked' : ''} onchange="budSetToggle('${key}', this.checked)">
      <span>${label} ${hlp(help)}</span>
    </label>`;

  return `
    <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:16px;margin:12px 0;">
      <div style="font-weight:700;margin-bottom:10px;">&#9881;&#65039; Budget setup</div>
      <div style="font-size:.78rem;color:var(--muted);line-height:1.6;margin-bottom:10px;">${suggest}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;">
        <div class="field" style="margin:0;">
          <label>Pay cycle ${hlp('How often you get paid. This sets the budget period. Leave it unset and the budget falls back to the calendar month and will NOT project income.')}</label>
          <select id="bud-cycle">
            ${cycles.map(c => `<option value="${c}"${c === cur ? ' selected' : ''}>${c}</option>`).join('')}
          </select>
        </div>
        <div class="field" style="margin:0;">
          <label>A period start date ${hlp('Any one real payday. Every period is counted forward and back from this date.')}</label>
          <input type="date" id="bud-anchor" value="${esc(cand.suggested_anchor || p.start || _billTodayISO())}">
        </div>
      </div>
      ${toggleRow('budget_planner_enabled', 'AI grocery planner', 'Lets the 🛒 Plan tab ask the model for a list. It only ever writes a draft you accept or reject — it can never change a budget or buy anything.')}
      ${toggleRow('budget_calendar_predictions', 'Predicted restock dates on the calendar', 'Shows "milk likely out ~" markers and a suggested shopping day. They are always drawn as predictions, never as facts.')}
      <div style="display:flex;gap:8px;margin-top:14px;">
        <button class="btn-sm primary" onclick="budSaveConfig()">&#128190; Save</button>
        <button class="btn-sm" onclick="budToggleSetup()">Close</button>
      </div>
    </div>`;
}

async function budSaveConfig() {
  try {
    await api('/api/budget/config', {
      method: 'POST',
      body: JSON.stringify({
        pay_cycle: (document.getElementById('bud-cycle') || {}).value || 'irregular',
        anchor: _ledgerVal('bud-anchor'),
      }),
    });
    toast('Pay cycle saved');
    _budSetupOpen = false;
    await renderBills();
  } catch (e) { toast(e.message || 'Could not save that', 'error'); }
}
window.budSaveConfig = budSaveConfig;

async function budSetToggle(key, on) {
  try {
    await api('/api/budget/toggles', { method: 'POST', body: JSON.stringify({ key, on: !!on }) });
    toast(on ? 'Turned on' : 'Turned off');
    await renderBills();
  } catch (e) { toast(e.message || 'Could not change that', 'error'); }
}
window.budSetToggle = budSetToggle;

/* Sample data. Deliberately understated and clearly labelled — this writes rows
   into the same database as his real money, so the copy has to be unambiguous
   about what it is and that it can be removed exactly. */
function _budSampleHtml() {
  return `
    <div style="margin-top:22px;border:1px dashed var(--border);border-radius:12px;padding:12px 14px;">
      <div style="font-size:.78rem;font-weight:600;margin-bottom:3px;">&#129514; Sample data (not your records)</div>
      <div style="font-size:.72rem;color:var(--muted);line-height:1.6;margin-bottom:8px;">
        Loads a few months of clearly-tagged made-up history so you can see the budget,
        charts and planner working before you have your own in here. Every sample row is
        tagged and the remove button deletes exactly those rows &mdash; anything you
        entered yourself is never touched. ${hlp('The purge matches the sample tag on each row, not a date range, so it is safe to run at any time.')}
      </div>
      <button class="btn-sm" onclick="budSample('seed')">&#10133; Load sample data</button>
      <button class="btn-sm danger" onclick="budSample('purge')">&#128465; Remove sample data</button>
    </div>`;
}

async function budSample(which) {
  if (which === 'purge' && !confirm('Remove the tagged sample rows? Your own records are not affected.')) return;
  if (which === 'seed' && !confirm('Load a few months of clearly-tagged SAMPLE money data? You can remove it again with one click.')) return;
  try {
    const r = await api(`/api/budget/sample/${which}`, { method: 'POST', body: JSON.stringify({}) });
    if (which === 'seed') {
      const s = r.seeded || {};
      toast(`Sample loaded: ${s.purchases || 0} purchases, ${s.paychecks || 0} paychecks, ${s.bills || 0} bills`);
    } else {
      toast(`Removed ${r.total || 0} sample row(s)`);
    }
    await renderBills();
  } catch (e) { toast(e.message || 'Could not do that', 'error'); }
}
window.budSample = budSample;

/* ── envelopes ──────────────────────────────────────────────────────────── */

function budEnvForm(id) {
  _budEnvFormId = id;
  const el = document.getElementById('bill-section-body');
  if (el) el.innerHTML = _budHtml();
}
window.budEnvForm = budEnvForm;

function _budEnvHtml() {
  const e = (_budEnvFormId ? (_budEnvs || []).find(x => x.id === _budEnvFormId) : null) || {};
  const kind = e.kind || 'fixed';
  return `
    <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:16px;margin:12px 0;">
      <div style="font-weight:700;margin-bottom:12px;">${_budEnvFormId ? '&#9998; Edit envelope' : '&#10133; New envelope'}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;">
        <div class="field" style="margin:0;">
          <label>Category ${hlp('Must match the category you put on purchases (food, gas…) so spending lands in the right envelope. "other" catches everything with no envelope of its own.')}</label>
          <input type="text" id="bud-env-cat" value="${esc(e.category || '')}" list="pur-cat-list" placeholder="food">
        </div>
        <div class="field" style="margin:0;">
          <label>Type</label>
          <select id="bud-env-kind" onchange="budEnvKindChanged()">
            <option value="fixed"${kind === 'fixed' ? ' selected' : ''}>fixed amount</option>
            <option value="percent"${kind === 'percent' ? ' selected' : ''}>percent of income</option>
          </select>
        </div>
        <div class="field" style="margin:0;" id="bud-env-amt-wrap">
          <label>Amount per period</label>
          <input type="number" id="bud-env-amt" min="0" step="0.01" placeholder="0.00"
                 value="${e.amount_cents ? (e.amount_cents / 100).toFixed(2) : ''}">
        </div>
        <div class="field" style="margin:0;" id="bud-env-pct-wrap">
          <label>Percent of income ${hlp('Recomputed each period from the income actually recorded, so it moves with your real pay.')}</label>
          <input type="number" id="bud-env-pct" min="0" max="100" step="0.1" placeholder="10"
                 value="${e.percent ? e.percent : ''}">
        </div>
      </div>
      <div style="display:flex;gap:8px;margin-top:14px;">
        <button class="btn-sm primary" onclick="budEnvSave()">&#128190; Save</button>
        <button class="btn-sm" onclick="budEnvForm(null)">Cancel</button>
      </div>
    </div>`;
}

function budEnvKindChanged() {
  const kind = (document.getElementById('bud-env-kind') || {}).value;
  const a = document.getElementById('bud-env-amt-wrap');
  const p = document.getElementById('bud-env-pct-wrap');
  if (a) a.style.display = kind === 'percent' ? 'none' : '';
  if (p) p.style.display = kind === 'percent' ? '' : 'none';
}
window.budEnvKindChanged = budEnvKindChanged;

async function budEnvSave() {
  const category = _ledgerVal('bud-env-cat');
  if (!category) { toast('Which category is this envelope for?', 'error'); return; }
  const kind = (document.getElementById('bud-env-kind') || {}).value || 'fixed';
  const payload = { category, kind };
  if (kind === 'percent') {
    const pct = parseFloat(_ledgerVal('bud-env-pct'));
    if (!isFinite(pct) || pct <= 0 || pct > 100) { toast('Enter a percent between 0 and 100', 'error'); return; }
    payload.percent = pct;
  } else {
    const amt = _ledgerCents(_ledgerVal('bud-env-amt'));
    if (amt === null || !isFinite(amt) || amt < 0) { toast('Enter an amount like 400.00', 'error'); return; }
    payload.amount_cents = amt;
  }
  try {
    if (_budEnvFormId) await api(`/api/budget/envelopes/${_budEnvFormId}`, { method: 'PATCH', body: JSON.stringify(payload) });
    else await api('/api/budget/envelopes', { method: 'POST', body: JSON.stringify(payload) });
    toast('Envelope saved');
    _budEnvFormId = null;
    await renderBills();
  } catch (e) { toast(e.message || 'Could not save that envelope', 'error'); }
}
window.budEnvSave = budEnvSave;

async function budEnvDelete(id) {
  if (!confirm('Delete this envelope? Your purchases are not affected.')) return;
  try {
    await api(`/api/budget/envelopes/${id}`, { method: 'DELETE' });
    toast('Envelope deleted');
    await renderBills();
  } catch (e) { toast(e.message || 'Could not delete that', 'error'); }
}
window.budEnvDelete = budEnvDelete;

/* ══ 📈 INSIGHTS — what you actually buy, how fast, and what it costs ═════
   The discovery view exists because the owner is guessing at his own habits
   ("a gallon of milk every 3 days I think, and don't know how many Dr Peppers").
   So it is sorted by TOTAL SPEND by default: the ranking is what turns a list
   into a realisation. Nothing here is predicted below the backend's minimum —
   a thin item shows the points it has and says how many more it needs. */

async function _insRender() {
  const el = document.getElementById('bill-section-body');
  if (!el) return;
  el.innerHTML = `<div class="empty"><div class="empty-icon">&#9203;</div>Measuring what you buy&#8230;</div>`;
  const [cons, cats] = await Promise.allSettled([
    api(`/api/budget/consumption?sort=${encodeURIComponent(_insSort)}`),
    api('/api/budget/categories?months=12'),
  ]);
  if (!el.isConnected) return;
  _insData = cons.status === 'fulfilled' ? cons.value : null;
  _insCats = cats.status === 'fulfilled' ? cats.value : null;
  el.innerHTML = _insHtml();
  _insDrawCatChart();
  _insDrawItemChart();
}

function _insHtml() {
  const head = _ledgerHead('&#128200; Insights',
    `What you buy, how often, and what it really costs you per month &mdash; measured
     from your own line items, not from memory
     ${hlp('Add line items to a purchase (Purchases → More fields) and every item here fills in on its own.')}`,
    `<button class="btn-sm" onclick="renderBills()">&#8635; Refresh</button>`);

  if (!_insData) return `${head}${_ledgerDegraded('Insights', 'renderBills()')}`;

  const items = _insData.items || [];
  if (!items.length) {
    return `${head}
      <div class="empty" style="padding:44px 16px;">
        <div class="empty-icon">&#128200;</div>
        <div style="color:var(--muted);font-size:.86rem;max-width:480px;margin:0 auto;line-height:1.7;">
          Nothing itemised yet. Log a shopping trip with its individual items and this page
          starts measuring how fast you go through each one, what you pay, and whether the
          price is creeping up.
        </div>
        <div style="margin-top:12px;"><button class="btn-sm primary" onclick="billSection('purchases')">
          &#128722; Go to Purchases</button></div>
      </div>`;
  }

  const c = _insData.counts || {};
  const honesty = c.insufficient
    ? `<div style="font-size:.73rem;color:var(--muted);margin:2px 0 12px;">
         ${c.predictable} item(s) have enough history to predict.
         ${c.insufficient} do not yet &mdash; they are listed with the purchases they have,
         and no cadence is guessed for them.</div>`
    : `<div style="font-size:.73rem;color:var(--muted);margin:2px 0 12px;">
         All ${c.predictable} item(s) have at least ${_insData.min_observations} purchases,
         so every cadence below is measured.</div>`;

  const sorts = [['spend', 'total spend'], ['count', 'times bought'],
                 ['frequency', 'how often'], ['soon', 'needed soonest'], ['name', 'name']];
  const sortBar = `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:10px;">
      <span style="font-size:.72rem;color:var(--muted);">Sort by</span>
      ${sorts.map(([k, label]) => `<button class="btn-sm${_insSort === k ? ' primary' : ''}"
          onclick="insSort('${k}')">${label}</button>`).join('')}
    </div>`;

  const th = (t, extra) => `<th style="padding:7px 8px;${extra || ''}">${t}</th>`;
  const rows = items.map(s => {
    const thin = s.status !== 'ok';
    const every = thin
      ? `<span style="color:var(--muted);font-style:italic;">not enough data</span>`
      : `every <b>${s.avg_interval_days}</b> days`;
    const next = thin ? '—'
      : (s.days_until_next <= 0 ? `<span style="color:var(--red);font-weight:600;">due now</span>`
                                : `in ${s.days_until_next}d`);
    const trend = (s.price_trend || {});
    const trendCell = trend.status === 'ok'
      ? (trend.direction === 'rising'
          ? `<span style="color:var(--red);">&#9650; ${trend.change_pct}%</span>`
          : trend.direction === 'falling'
            ? `<span style="color:var(--green);">&#9660; ${Math.abs(trend.change_pct)}%</span>`
            : `<span style="color:var(--muted);">flat</span>`)
      : `<span style="color:var(--muted);" title="${esc(trend.message || '')}">—</span>`;
    return `<tr style="border-bottom:1px solid var(--border);cursor:pointer;"
                onclick="insOpenItem(${JSON.stringify(s.normalized_name).replace(/"/g, '&quot;')})">
      <td style="padding:7px 8px;"><b>${esc(s.display_name)}</b>
        <div style="font-size:.64rem;color:var(--muted);">${esc(s.category || 'uncategorized')}</div></td>
      <td style="padding:7px 8px;text-align:right;">${s.observations}</td>
      <td style="padding:7px 8px;">${every}</td>
      <td style="padding:7px 8px;text-align:right;">${_budAmt(s.last_unit_price_cents)}</td>
      <td style="padding:7px 8px;text-align:right;">${trendCell}</td>
      <td style="padding:7px 8px;text-align:right;">${_budAmt(s.monthly_spend_cents)}</td>
      <td style="padding:7px 8px;text-align:right;font-weight:600;">${billUSD(s.total_spent_cents)}</td>
      <td style="padding:7px 8px;text-align:right;white-space:nowrap;">${next}</td>
    </tr>`;
  }).join('');

  const table = `
    <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:.8rem;">
        <thead><tr style="color:var(--muted);text-align:left;border-bottom:1px solid var(--border);">
          ${th('Item')}${th('Buys', 'text-align:right;')}${th('How often')}
          ${th('Last price', 'text-align:right;')}${th('Price trend', 'text-align:right;')}
          ${th('Per month', 'text-align:right;')}${th('Total spent', 'text-align:right;')}
          ${th('Next', 'text-align:right;')}
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div style="font-size:.7rem;color:var(--muted);margin-top:6px;">Click any row for its chart.</div>
    </div>`;

  return `${head}${honesty}${_insItemPanel()}${sortBar}${table}${_insCatChartHtml()}`;
}

function insSort(k) {
  _insSort = k;
  _insRender();
}
window.insSort = insSort;

async function insOpenItem(norm) {
  if (_insItem && _insItem.normalized_name === norm) { _insItem = null; return _insRender(); }
  try {
    _insItem = await api(`/api/budget/consumption/item?name=${encodeURIComponent(norm)}`);
  } catch (e) {
    toast(e.message || 'Could not load that item', 'error');
    return;
  }
  const el = document.getElementById('bill-section-body');
  if (el) { el.innerHTML = _insHtml(); _insDrawCatChart(); _insDrawItemChart(); }
  const p = document.getElementById('ins-item-panel');
  if (p) p.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
window.insOpenItem = insOpenItem;

/* The per-item answer to "am I really going through a gallon every 3 days?" —
   the measured interval is the headline, stated before any chart. */
function _insItemPanel() {
  const s = _insItem;
  if (!s) return '';
  const thin = s.status !== 'ok';
  const headline = thin
    ? `<span style="color:var(--warn);">Not enough to predict yet &mdash; ${s.observations}
        purchase${s.observations === 1 ? '' : 's'} so far, ${s.needed} more needed.</span>`
    : `You buy this <b style="font-size:1.15rem;">every ${s.avg_interval_days} days</b> on average,
       across your last ${s.observations} purchases
       <span style="color:var(--muted);">(gaps of ${Math.min.apply(null, s.intervals)}&ndash;${Math.max.apply(null, s.intervals)} days)</span>.`;
  const price = s.last_unit_price_cents !== null && s.last_unit_price_cents !== undefined
    ? `Last paid <b>${billUSD(s.last_unit_price_cents)}</b>${s.unit ? ' / ' + esc(s.unit) : ''}` : 'No unit price recorded';
  const trend = s.price_trend || {};
  const trendLine = trend.status === 'ok'
    ? ` · price ${esc(trend.direction)}${trend.direction === 'flat' ? '' : ` ${trend.change_pct}%`}
        (${billUSD(trend.earlier_avg_cents)} &rarr; ${billUSD(trend.recent_avg_cents)})`
    : ` · <span style="color:var(--muted);">${esc(trend.message || 'no price trend yet')}</span>`;
  const nextLine = thin ? '' :
    `<div style="font-size:.78rem;margin-top:5px;">Predicted next needed
       <b>${esc(s.predicted_next_date)}</b>
       <span style="color:var(--muted);">(window ${esc(s.predicted_earliest)} &rarr; ${esc(s.predicted_latest)})</span>
       ${_budConfChip(s.confidence)}</div>`;

  return `
    <div id="ins-item-panel" style="background:var(--surface2);border:1px solid var(--border);
                border-radius:12px;padding:14px;margin-bottom:16px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:6px;">
        <div style="font-weight:700;font-size:1rem;">${esc(s.display_name)}</div>
        <button class="btn-sm" onclick="insOpenItem(${JSON.stringify(s.normalized_name).replace(/"/g, '&quot;')})">&times; Close</button>
      </div>
      <div style="font-size:.86rem;line-height:1.7;">${headline}</div>
      <div style="font-size:.78rem;color:var(--muted);margin-top:3px;">${price}${trendLine}</div>
      ${nextLine}
      <div style="margin-top:12px;">
        <canvas id="ins-item-chart" height="200" style="width:100%;height:200px;display:block;"></canvas>
        <div id="ins-item-note" style="font-size:.7rem;color:var(--muted);margin-top:6px;"></div>
      </div>
      ${(s.variants || []).length > 1 ? `<div style="font-size:.68rem;color:var(--muted);margin-top:8px;">
          Counted together across the ways you typed it: ${s.variants.map(v => esc(v)).join(' · ')}</div>` : ''}
    </div>`;
}

/* Per-item chart: quantity bars per shopping trip on the left axis, unit price as
   a line on the right axis, both on one time axis. Below the plot, the gap in
   days between consecutive trips is printed — that strip IS the answer to "how
   fast do I go through this". With fewer than the minimum observations the points
   are still drawn but NO line is fitted through them and the note says so. */
function _insDrawItemChart() {
  const cv = document.getElementById('ins-item-chart');
  if (!cv || !_insItem) return;
  const s = _insItem;
  const pts = s.points || [];
  const note = document.getElementById('ins-item-note');
  if (!pts.length) { if (note) note.textContent = 'No purchases recorded for this item.'; return; }

  const css = getComputedStyle(document.documentElement);
  const cvar = (n, fb) => (css.getPropertyValue(n) || '').trim() || fb;
  const ACC = cvar('--accent', '#6c63ff'), WARN = cvar('--warn', '#f59e0b');
  const MUTED = cvar('--muted', '#64748b'), BORDER = cvar('--border', '#2a2f3d');
  const RED = cvar('--red', '#ef4444');

  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(260, cv.clientWidth || 600), h = 200;
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  const x = cv.getContext('2d');
  if (!x) return;
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  x.clearRect(0, 0, w, h);

  const padL = 34, padR = 46, padT = 12, padB = 34;
  const plotW = w - padL - padR, plotH = h - padT - padB;

  const days = pts.map(p => Date.parse(p.date + 'T00:00:00Z'));
  const t0 = days[0], t1 = days[days.length - 1];
  const span = Math.max(1, t1 - t0);
  const px = t => padL + (pts.length === 1 ? plotW / 2 : (t - t0) / span * plotW);

  const maxQty = Math.max.apply(null, pts.map(p => p.qty || 0).concat([1]));
  const prices = pts.map(p => p.unit_price_cents).filter(v => v !== null && v !== undefined);
  const maxP = prices.length ? Math.max.apply(null, prices) : 0;
  const minP = prices.length ? Math.min.apply(null, prices) : 0;
  const pSpan = Math.max(1, maxP - minP);

  // gridlines + left (qty) axis
  x.strokeStyle = BORDER; x.lineWidth = 1;
  x.fillStyle = MUTED; x.font = '10px system-ui, sans-serif'; x.textBaseline = 'middle';
  for (let i = 0; i <= 3; i++) {
    const y = Math.round(padT + plotH - (plotH * i / 3)) + 0.5;
    x.beginPath(); x.moveTo(padL, y); x.lineTo(w - padR, y); x.stroke();
    x.textAlign = 'right';
    x.fillText((maxQty * i / 3).toFixed(maxQty >= 10 ? 0 : 1), padL - 5, y);
    if (prices.length) {
      x.textAlign = 'left';
      x.fillStyle = WARN;
      x.fillText('$' + ((minP + pSpan * i / 3) / 100).toFixed(2), w - padR + 5, y);
      x.fillStyle = MUTED;
    }
  }

  // quantity bars
  const bw = Math.max(3, Math.min(20, plotW / Math.max(1, pts.length) * 0.45));
  pts.forEach((p, i) => {
    const cx = px(days[i]);
    const bh = Math.max(2, Math.round((p.qty || 0) / maxQty * plotH));
    x.fillStyle = ACC;
    x.fillRect(Math.round(cx - bw / 2), Math.round(padT + plotH - bh), Math.round(bw), bh);
  });

  // unit-price line (only where a price was actually recorded)
  if (prices.length >= 2) {
    x.strokeStyle = WARN; x.lineWidth = 2;
    x.beginPath();
    let started = false;
    pts.forEach((p, i) => {
      if (p.unit_price_cents === null || p.unit_price_cents === undefined) return;
      const cx = px(days[i]);
      const cy = padT + plotH - ((p.unit_price_cents - minP) / pSpan) * plotH;
      if (!started) { x.moveTo(cx, cy); started = true; } else x.lineTo(cx, cy);
    });
    x.stroke();
    x.fillStyle = WARN;
    pts.forEach((p, i) => {
      if (p.unit_price_cents === null || p.unit_price_cents === undefined) return;
      const cx = px(days[i]);
      const cy = padT + plotH - ((p.unit_price_cents - minP) / pSpan) * plotH;
      x.beginPath(); x.arc(cx, cy, 2.6, 0, Math.PI * 2); x.fill();
    });
  }

  // the gap-in-days strip under the plot — the measured cadence, drawn
  x.textAlign = 'center'; x.textBaseline = 'top';
  x.font = '9px system-ui, sans-serif';
  for (let i = 1; i < pts.length; i++) {
    const gap = Math.round((days[i] - days[i - 1]) / 86400000);
    const mid = (px(days[i]) + px(days[i - 1])) / 2;
    x.fillStyle = MUTED;
    x.fillText(`${gap}d`, mid, padT + plotH + 5);
  }
  // first / last dates
  x.font = '10px system-ui, sans-serif';
  x.fillStyle = MUTED;
  x.textAlign = 'left';  x.fillText(pts[0].date.slice(5), padL, padT + plotH + 18);
  if (pts.length > 1) { x.textAlign = 'right'; x.fillText(pts[pts.length - 1].date.slice(5), w - padR, padT + plotH + 18); }

  // predicted-next marker, only when the backend actually predicted one
  if (s.status === 'ok' && s.predicted_next_date) {
    const tp = Date.parse(s.predicted_next_date + 'T00:00:00Z');
    if (tp > t0 && (tp - t0) / span <= 1.35) {
      const cx = Math.min(w - padR, px(tp));
      x.strokeStyle = RED; x.lineWidth = 1; x.setLineDash([3, 3]);
      x.beginPath(); x.moveTo(cx, padT); x.lineTo(cx, padT + plotH); x.stroke();
      x.setLineDash([]);
    }
  }

  if (note) {
    note.innerHTML = s.status === 'ok'
      ? `Bars = how many you bought each trip · line = unit price · numbers between = days between trips`
        + (s.predicted_next_date ? ` · dashed red = predicted next need` : '')
      : `<b>Not enough to predict yet</b> — ${pts.length} purchase${pts.length === 1 ? '' : 's'} plotted, `
        + `${s.needed} more needed before a cadence is worked out. No trend is drawn through this few points.`;
  }
}

/* Category rollup: where the money actually goes, month by month. Grouped bars
   (one colour per category) in the same style as the overview chart. */
function _insCatChartHtml() {
  const cats = _insCats && _insCats.totals ? Object.keys(_insCats.totals) : [];
  if (!cats.length) return '';
  const legend = cats.slice(0, 6).map((c, i) => `
    <span style="font-size:.7rem;display:inline-flex;align-items:center;gap:4px;margin-right:10px;">
      <span style="width:9px;height:9px;border-radius:2px;background:${_INS_CAT_COLORS[i % _INS_CAT_COLORS.length]};
        display:inline-block;"></span>${esc(c)}
      <span style="color:var(--muted);">${billUSD(_insCats.totals[c])}</span></span>`).join('');
  return `
    <div style="margin-top:22px;">
      <div style="font-weight:600;font-size:.86rem;margin-bottom:2px;">&#127991;&#65039; Spending by category</div>
      <div style="font-size:.72rem;color:var(--muted);margin-bottom:8px;">
        Non-bill spending per month. Bills are tracked separately and are not folded in here,
        so nothing is counted twice.</div>
      <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:12px;">
        <div style="margin-bottom:8px;">${legend}</div>
        <canvas id="ins-cat-chart" height="190" style="width:100%;height:190px;display:block;"></canvas>
        <div id="ins-cat-note" style="font-size:.7rem;color:var(--muted);margin-top:6px;"></div>
      </div>
    </div>`;
}

const _INS_CAT_COLORS = ['var(--accent)', 'var(--warn)', 'var(--green)',
                         'var(--accent2)', 'var(--red)', 'var(--muted)'];

function _insDrawCatChart() {
  const cv = document.getElementById('ins-cat-chart');
  if (!cv || !_insCats) return;
  const months = _insCats.months || [];
  const totals = _insCats.totals || {};
  const cats = Object.keys(totals).sort((a, b) => totals[b] - totals[a]).slice(0, 6);
  const note = document.getElementById('ins-cat-note');
  if (!months.length || !cats.length) { if (note) note.textContent = 'No purchases recorded yet.'; return; }

  const css = getComputedStyle(document.documentElement);
  const cvar = (n, fb) => (css.getPropertyValue(n) || '').trim() || fb;
  const resolve = v => {
    const m = /^var\((--[a-z0-9-]+)\)$/i.exec(v);
    return m ? cvar(m[1], '#6c63ff') : v;
  };
  const MUTED = cvar('--muted', '#64748b'), BORDER = cvar('--border', '#2a2f3d');
  const colors = _INS_CAT_COLORS.map(resolve);

  const series = cats.map(c => (_insCats.series[c] || []).map(v => v || 0));
  const maxV = Math.max(1, ...series.map(s => Math.max.apply(null, s.concat([0]))));

  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(260, cv.clientWidth || 600), h = 190;
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  const x = cv.getContext('2d');
  if (!x) return;
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  x.clearRect(0, 0, w, h);

  const padL = 52, padR = 8, padT = 10, padB = 22;
  const plotW = w - padL - padR, plotH = h - padT - padB;

  x.strokeStyle = BORDER; x.lineWidth = 1;
  x.fillStyle = MUTED; x.font = '10px system-ui, sans-serif'; x.textBaseline = 'middle';
  for (let i = 0; i <= 3; i++) {
    const y = Math.round(padT + plotH - (plotH * i / 3)) + 0.5;
    x.beginPath(); x.moveTo(padL, y); x.lineTo(w - padR, y); x.stroke();
    x.textAlign = 'right';
    x.fillText(billUSD(maxV * i / 3), padL - 6, y);
  }

  const n = months.length;
  const slot = plotW / n;
  const bw = Math.max(2, Math.min(9, slot / (cats.length + 1)));
  x.textAlign = 'center'; x.textBaseline = 'top';
  for (let i = 0; i < n; i++) {
    const cx = padL + slot * i + slot / 2;
    const groupW = bw * cats.length;
    cats.forEach((c, k) => {
      const v = series[k][i] || 0;
      const bh = Math.max(v > 0 ? 2 : 0, Math.round(v / maxV * plotH));
      if (!bh) return;
      x.fillStyle = colors[k % colors.length];
      x.fillRect(Math.round(cx - groupW / 2 + k * bw), Math.round(padT + plotH - bh),
                 Math.round(Math.max(2, bw - 1)), bh);
    });
    if (n <= 6 || i % 2 === (n - 1) % 2) {
      x.fillStyle = MUTED;
      x.fillText(String(months[i]).slice(2), cx, padT + plotH + 6);
    }
  }
  if (note) {
    const top = cats[0];
    note.textContent = `${cats.length} categor${cats.length === 1 ? 'y' : 'ies'} · `
      + `biggest is ${top} at ${billUSD(totals[top])} over these ${n} months · peak month ${billUSD(maxV)}.`;
  }
}

// Keep both insight charts crisp on resize, same as the charts above.
window.addEventListener('resize', () => {
  if (document.getElementById('ins-cat-chart')) _insDrawCatChart();
  if (document.getElementById('ins-item-chart')) _insDrawItemChart();
});

/* ══ 🛒 PLAN — the AI grocery list ═══════════════════════════════════════
   ADVISORY, and the UI says so in as many words. The model gets the real
   remaining food envelope and the owner's own item history; anything it names
   that is not in that history is dropped by the backend validator before it ever
   reaches this screen, and every price shown is recomputed from his receipts —
   never a number the model produced. A plan changes no budget and buys nothing;
   it becomes a real purchase only after he accepts it and presses the button. */

async function _planRender() {
  const el = document.getElementById('bill-section-body');
  if (!el) return;
  el.innerHTML = `<div class="empty"><div class="empty-icon">&#9203;</div>Loading plans&#8230;</div>`;
  const r = await Promise.allSettled([api('/api/budget/plans?limit=10')]);
  if (!el.isConnected) return;
  _plans = r[0].status === 'fulfilled' ? r[0].value : null;
  el.innerHTML = _planHtml();
}

function _planHtml() {
  const head = _ledgerHead('&#128722; Plan',
    `An AI grocery list built from what you actually buy, sized to what is left in your
     food envelope. Advice only &mdash; nothing is bought and no budget changes
     ${hlp('The model may only choose from items in your own purchase history, and every price comes from your receipts. Anything it invents is dropped before you see it.')}`,
    `<button class="btn-sm primary" id="plan-gen-btn" onclick="planGenerate()">&#10024; Generate list</button>
     <button class="btn-sm" onclick="renderBills()">&#8635; Refresh</button>`);

  if (!_plans) return `${head}${_ledgerDegraded('The planner', 'renderBills()')}`;

  const tog = _plans.toggles || {};
  const off = tog.budget_planner_enabled === false
    ? `<div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;
              padding:12px 14px;margin:12px 0;font-size:.79rem;">
        The AI grocery planner is turned <b>off</b>.
        <button class="btn-sm" onclick="budSetToggle('budget_planner_enabled', true)">Turn it on</button>
        <div style="color:var(--muted);font-size:.72rem;margin-top:4px;">
          It only ever writes a draft you accept or reject.</div>
      </div>` : '';

  const plans = _plans.plans || [];
  if (!plans.length) {
    return `${head}${off}
      <div class="empty" style="padding:44px 16px;">
        <div class="empty-icon">&#128722;</div>
        <div style="color:var(--muted);font-size:.86rem;max-width:470px;margin:0 auto;line-height:1.7;">
          No lists yet. Once a few items have enough purchase history, Generate builds a list of
          what is running out, priced from what you actually paid last time.
        </div>
      </div>`;
  }
  return `${head}${off}${plans.map(_planCard).join('')}`;
}

function _planCard(p) {
  const statusChip = {
    generating: _billChip('working…', 'var(--warn)'),
    draft: _billChip('draft — your call', 'var(--accent)'),
    accepted: _billChip('accepted', 'var(--green)'),
    rejected: _billChip('rejected', 'var(--muted)'),
    failed: _billChip('failed', 'var(--red)'),
  }[p.status] || _billChip(p.status, 'var(--muted)');

  const editable = p.status === 'draft';
  const lines = (p.items || []).map((i, idx) => `
    <tr style="border-bottom:1px solid var(--border);">
      <td style="padding:6px 8px;"><b>${esc(i.name)}</b>
        ${i.why ? `<div style="font-size:.65rem;color:var(--muted);">${esc(i.why)}</div>` : ''}</td>
      <td style="padding:6px 8px;text-align:right;">
        ${editable
          ? `<input type="number" min="0" step="0.1" value="${i.qty}" id="plan-${p.id}-qty-${idx}"
                    style="width:66px;text-align:right;">`
          : i.qty}${i.unit ? ' ' + esc(i.unit) : ''}</td>
      <td style="padding:6px 8px;text-align:right;color:var(--muted);">
        ${_budAmt(i.unit_price_cents)}</td>
      <td style="padding:6px 8px;text-align:right;font-weight:600;">
        ${i.est_cents === null || i.est_cents === undefined
          ? `<span style="color:var(--warn);" title="no unit price recorded for this item">no price yet</span>`
          : billUSD(i.est_cents)}</td>
      <td style="padding:6px 8px;text-align:right;">
        ${editable ? `<button class="btn-sm danger" onclick="planDropLine(${p.id}, ${idx})">&times;</button>` : ''}</td>
    </tr>`).join('');

  const facts = (p.observations || []).map(o =>
    `<li style="margin-bottom:3px;">${esc(o.text)}</li>`).join('');
  const notes = (p.llm_notes || []).map(o =>
    `<li style="margin-bottom:3px;">${esc(o.text)}</li>`).join('');
  const dropped = (p.rejected_items || []).length
    ? `<div style="font-size:.72rem;color:var(--muted);margin-top:8px;">
        Dropped as not in your history: ${p.rejected_items.map(r => esc(r.name)).join(', ')}
        ${hlp('The model suggested these; they were removed because you have never bought them. They are shown so you can see what it tried to add.')}</div>` : '';

  const budgetLine = (p.envelope_cents === null || p.envelope_cents === undefined)
    ? `<span style="color:var(--muted);">no food envelope set</span>`
    : `${billUSD(p.est_total_cents)} of ${billUSD(p.envelope_cents)} food budget left
       ${p.est_total_cents > p.envelope_cents ? '<span style="color:var(--red);font-weight:600;">— over</span>' : ''}`;

  const actions = p.status === 'draft'
    ? `<button class="btn-sm primary" onclick="planSaveEdits(${p.id})">&#128190; Save edits</button>
       <button class="btn-sm primary" onclick="planAccept(${p.id})">&#10004; Accept</button>
       <button class="btn-sm danger" onclick="planReject(${p.id})">&times; Reject</button>`
    : p.status === 'accepted' && !p.purchase_id
      ? `<button class="btn-sm primary" onclick="planToPurchase(${p.id})">&#128722; Log as a purchase</button>
         <span style="font-size:.7rem;color:var(--muted);">pre-filled with your own prices — correct it against the receipt</span>`
      : p.purchase_id
        ? `<span style="font-size:.72rem;color:var(--muted);">Logged as purchase #${p.purchase_id}.</span>`
        : '';

  return `
    <div style="background:var(--surface2);border:1px solid var(--border);border-radius:12px;
                padding:14px;margin:12px 0;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
        <div style="font-weight:700;">Grocery list #${p.id} ${statusChip}</div>
        <div style="font-size:.78rem;">${budgetLine}</div>
      </div>
      <div style="font-size:.7rem;color:var(--muted);margin-top:2px;">
        For ${esc(p.period_start || '')} &rarr; ${esc(p.period_end || '')}</div>
      ${p.status === 'failed' ? `<div style="color:var(--red);font-size:.78rem;margin-top:8px;">
          ${esc(p.error || 'The model did not answer.')}</div>` : ''}
      ${lines ? `<div style="overflow-x:auto;margin-top:10px;">
        <table style="width:100%;border-collapse:collapse;font-size:.79rem;">
          <thead><tr style="color:var(--muted);text-align:left;border-bottom:1px solid var(--border);">
            <th style="padding:6px 8px;">Item</th>
            <th style="padding:6px 8px;text-align:right;">Qty</th>
            <th style="padding:6px 8px;text-align:right;">Your last price</th>
            <th style="padding:6px 8px;text-align:right;">Estimate</th>
            <th></th></tr></thead>
          <tbody>${lines}</tbody>
        </table></div>` : (p.status === 'generating'
          ? `<div style="color:var(--muted);font-size:.8rem;margin-top:10px;">Waiting on the queue&#8230;</div>`
          : `<div style="color:var(--muted);font-size:.8rem;margin-top:10px;">No items on this list.</div>`)}
      ${facts ? `<div style="margin-top:10px;">
          <div style="font-size:.74rem;font-weight:600;">What your numbers say</div>
          <ul style="font-size:.74rem;color:var(--muted);margin:4px 0 0;padding-left:18px;">${facts}</ul>
        </div>` : ''}
      ${notes ? `<div style="margin-top:8px;">
          <div style="font-size:.74rem;font-weight:600;">Model notes <span style="font-weight:400;color:var(--muted);">(advisory)</span></div>
          <ul style="font-size:.74rem;color:var(--muted);margin:4px 0 0;padding-left:18px;">${notes}</ul>
        </div>` : ''}
      ${dropped}
      <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center;">${actions}</div>
    </div>`;
}

async function planGenerate() {
  if (_planBusy) return;
  _planBusy = true;
  const btn = document.getElementById('plan-gen-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Queued…'; }
  try {
    const r = await api('/api/budget/plan', { method: 'POST', body: JSON.stringify({}) });
    toast('Queued — the list appears when the model finishes');
    await _planRender();                      // show the 'working…' card straight away
    try { await pollTask(r.task_id, 120); } catch (e) { /* the card shows the failure */ }
    await _planRender();
  } catch (e) {
    toast(e.message || 'Could not start a plan', 'error');
  } finally {
    _planBusy = false;
    const b = document.getElementById('plan-gen-btn');
    if (b) { b.disabled = false; b.innerHTML = '&#10024; Generate list'; }
  }
}
window.planGenerate = planGenerate;

// Collect the edited quantities off the form. Prices are never collected — they
// are the backend's to compute from recorded receipts.
function _planCollect(pid) {
  const plan = ((_plans || {}).plans || []).find(p => p.id === pid);
  if (!plan) return [];
  return (plan.items || []).map((i, idx) => {
    const el = document.getElementById(`plan-${pid}-qty-${idx}`);
    const q = el ? parseFloat(el.value) : i.qty;
    return { name: i.name, qty: (isFinite(q) && q > 0) ? q : i.qty, why: i.why };
  });
}

async function planSaveEdits(pid, items) {
  try {
    await api(`/api/budget/plans/${pid}`, {
      method: 'PATCH',
      body: JSON.stringify({ items: items || _planCollect(pid) }),
    });
    toast('List updated');
    await _planRender();
  } catch (e) { toast(e.message || 'Could not save that list', 'error'); }
}
window.planSaveEdits = planSaveEdits;

async function planDropLine(pid, idx) {
  const items = _planCollect(pid).filter((_, i) => i !== idx);
  await planSaveEdits(pid, items);
}
window.planDropLine = planDropLine;

async function planAccept(pid) {
  try {
    await api(`/api/budget/plans/${pid}`, {
      method: 'PATCH', body: JSON.stringify({ items: _planCollect(pid) }),
    });
  } catch (e) { /* accept anyway with what is stored */ }
  try {
    await api(`/api/budget/plans/${pid}/accept`, { method: 'POST', body: JSON.stringify({}) });
    toast('Accepted — nothing was bought or budgeted, it is just your list now');
    await _planRender();
  } catch (e) { toast(e.message || 'Could not accept that list', 'error'); }
}
window.planAccept = planAccept;

async function planReject(pid) {
  try {
    await api(`/api/budget/plans/${pid}/reject`, { method: 'POST', body: JSON.stringify({}) });
    toast('Rejected');
    await _planRender();
  } catch (e) { toast(e.message || 'Could not reject that list', 'error'); }
}
window.planReject = planReject;

async function planToPurchase(pid) {
  const merchant = prompt('Where did you shop? (this logs a purchase you can then correct)', '');
  if (merchant === null || !merchant.trim()) return;
  try {
    const r = await api(`/api/budget/plans/${pid}/purchase`, {
      method: 'POST',
      body: JSON.stringify({ merchant: merchant.trim(), purchased_at: _billTodayISO(), category: 'food' }),
    });
    toast(`Logged ${billUSD(r.amount_cents)} — edit it to match your receipt`);
    await _planRender();
  } catch (e) { toast(e.message || 'Could not log that', 'error'); }
}
window.planToPurchase = planToPurchase;

/* ══ PURCHASE LINE ITEMS — the foundation everything above measures ═══════
   Optional by design: a trip logged as one total behaves exactly as it always
   has. Itemising it is what lets the Insights page measure how fast things go
   and what they cost, so the form nudges toward it without ever requiring it.

   Autocomplete comes from /api/budget/items/suggest — the owner's OWN history is
   the only item vocabulary this app has. Picking a remembered item fills in its
   unit and the price he last paid, and shows the measured cadence inline
   ("you buy this every ~3 days, last paid $3.89"). */

let _purItemSeq = 0;
let _purSuggest = [];      // GET /api/budget/items/suggest — remembered items
let _purStats = {};        // normalized_name -> consumption stats, for the hints

async function _purLoadSuggest() {
  try {
    const r = await api('/api/budget/items/suggest?limit=40');
    _purSuggest = r.items || [];
  } catch (e) { _purSuggest = []; }
  try {
    const c = await api('/api/budget/consumption?sort=count');
    _purStats = {};
    (c.items || []).forEach(s => { _purStats[s.normalized_name] = s; });
  } catch (e) { _purStats = {}; }
}

function _purSuggestDatalist() {
  return `<datalist id="pur-item-list">
    ${_purSuggest.map(s => `<option value="${esc(s.name)}"></option>`).join('')}
  </datalist>`;
}

function _purItemsEditorHtml(items) {
  return `
    <div style="margin-top:14px;">
      <div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;">
        Line items &mdash; optional ${hlp('Leave this empty to log the trip as a single total, exactly as before. Fill it in and the Insights page can measure how fast you go through each item and whether its price is climbing.')}
      </div>
      ${_purSuggestDatalist()}
      <div id="pur-item-rows"></div>
      <button class="btn-sm" onclick="purAddItemRow()">&#10133; item</button>
      <span id="pur-items-total" style="font-size:.74rem;color:var(--muted);margin-left:10px;"></span>
    </div>`;
}

function purAddItemRow(it) {
  const wrap = document.getElementById('pur-item-rows');
  if (!wrap) return;
  const i = _purItemSeq++;
  const row = document.createElement('div');
  row.id = `pur-item-${i}`;
  row.style.cssText = 'display:grid;grid-template-columns:1.6fr 70px 70px 90px auto;gap:6px;align-items:center;margin-bottom:6px;';
  row.innerHTML = `
    <div>
      <input type="text" id="pur-item-name-${i}" list="pur-item-list" placeholder="item name"
             value="${esc((it && it.name) || '')}" oninput="purItemPicked(${i})"
             style="width:100%;">
      <div id="pur-item-hint-${i}" style="font-size:.64rem;color:var(--muted);margin-top:2px;"></div>
    </div>
    <input type="number" id="pur-item-qty-${i}" min="0" step="0.01" placeholder="qty"
           value="${(it && it.qty) || 1}" oninput="purItemsRecalc()">
    <input type="text" id="pur-item-unit-${i}" placeholder="unit" value="${esc((it && it.unit) || '')}">
    <input type="number" id="pur-item-price-${i}" min="0" step="0.01" placeholder="unit $"
           value="${it && it.unit_price_cents ? (it.unit_price_cents / 100).toFixed(2) : ''}"
           oninput="purItemsRecalc()">
    <button class="btn-sm danger" onclick="purRemoveItemRow(${i})">&times;</button>`;
  wrap.appendChild(row);
  if (it && it.name) purItemPicked(i);
  purItemsRecalc();
}
window.purAddItemRow = purAddItemRow;

function purRemoveItemRow(i) {
  const r = document.getElementById(`pur-item-${i}`);
  if (r) r.remove();
  purItemsRecalc();
}
window.purRemoveItemRow = purRemoveItemRow;

/* When a remembered item is typed/picked: fill unit + last price, and show what
   the measurement actually says about it. Under the minimum it says so instead
   of showing a cadence. */
function purItemPicked(i) {
  const nameEl = document.getElementById(`pur-item-name-${i}`);
  const hint = document.getElementById(`pur-item-hint-${i}`);
  if (!nameEl || !hint) return;
  const typed = (nameEl.value || '').trim().toLowerCase();
  const match = _purSuggest.find(s => (s.name || '').toLowerCase() === typed);
  if (!match) { hint.textContent = ''; return; }
  const unitEl = document.getElementById(`pur-item-unit-${i}`);
  const priceEl = document.getElementById(`pur-item-price-${i}`);
  if (unitEl && !unitEl.value) unitEl.value = match.unit || '';
  if (priceEl && !priceEl.value && match.last_unit_price_cents) {
    priceEl.value = (match.last_unit_price_cents / 100).toFixed(2);
  }
  const s = _purStats[match.normalized_name];
  if (s && s.status === 'ok') {
    hint.innerHTML = `you buy this every ~<b>${s.avg_interval_days}</b> days` +
      (s.last_unit_price_cents ? `, last paid ${billUSD(s.last_unit_price_cents)}` : '');
  } else if (s) {
    hint.textContent = `${s.observations} purchase(s) so far — ${s.needed} more before a cadence can be measured`;
  } else {
    hint.textContent = '';
  }
  purItemsRecalc();
}
window.purItemPicked = purItemPicked;

function purCollectItems() {
  const wrap = document.getElementById('pur-item-rows');
  if (!wrap) return [];
  const out = [];
  Array.from(wrap.children).forEach(row => {
    const i = String(row.id).replace('pur-item-', '');
    const name = ((document.getElementById(`pur-item-name-${i}`) || {}).value || '').trim();
    if (!name) return;
    const qty = parseFloat((document.getElementById(`pur-item-qty-${i}`) || {}).value) || 1;
    const price = _ledgerCents((document.getElementById(`pur-item-price-${i}`) || {}).value);
    const item = { name, qty, unit: ((document.getElementById(`pur-item-unit-${i}`) || {}).value || '').trim() };
    if (price !== null && isFinite(price)) {
      item.unit_price_cents = price;
    } else {
      // No price typed: the row is still worth keeping (it records THAT the item
      // was bought and when), so give it a zero line total rather than dropping
      // it or inventing a price.
      item.line_total_cents = 0;
    }
    out.push(item);
  });
  return out;
}
window.purCollectItems = purCollectItems;

function purItemsRecalc() {
  const el = document.getElementById('pur-items-total');
  if (!el) return;
  const items = purCollectItems();
  if (!items.length) { el.textContent = ''; return; }
  const total = items.reduce((a, i) => a + (i.unit_price_cents ? Math.round(i.qty * i.unit_price_cents) : 0), 0);
  el.innerHTML = `${items.length} line(s) &middot; <b>${billUSD(total)}</b>
    <span style="opacity:.8;">&mdash; leave the Amount box empty to use this as the total</span>`;
}
window.purItemsRecalc = purItemsRecalc;

/* Read-only line-item view from the Purchases table (the 🧾 badge). */
async function purShowItems(pid) {
  try {
    const r = await api(`/api/ledger/purchases/${pid}/items`);
    const lines = (r.items || []).map(i =>
      `${i.qty}${i.unit ? ' ' + i.unit : ''} × ${i.name}` +
      (i.unit_price_cents ? ` @ ${billUSD(i.unit_price_cents)}` : '') +
      ` = ${billUSD(i.line_total_cents)}`).join('\n');
    alert(`Line items\n\n${lines || '(none)'}\n\nLines total ${billUSD(r.items_total_cents)}\n` +
          `Purchase total ${billUSD(r.amount_cents)}\n\n${r.note}`);
  } catch (e) { toast(e.message || 'Could not load those items', 'error'); }
}
window.purShowItems = purShowItems;

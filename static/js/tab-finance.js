'use strict';
/* ══ FINANCE TAB ══
   One roof for all the money: sub-tab panes (same pane-toggle mechanism as
   Crypto/Settings — every pane stays in the DOM, we only switch which one is
   visible, and each pane lazy-loads once):
     🏦 Overview — net-worth strip built from the other panes' APIs
     📆 Bills    — real household bills, due dates & payments (tab-bills.js renderBills)
     🏛️ Treasury — the national reserve (tab-treasury.js renderTreasury)
     💵 Missions & Earn — money missions + Cash App + Donate card (tab-money.js renderMoney)
     👛 Wallets  — real mainnet light-wallets (tab-wallets.js renderWallets)
     📈 Markets  — crypto & markets w/ its own nested sub-tabs (tab-crypto.js renderCrypto)
   Old view ids treasury/money/crypto/wallets still deep-link here (app-nav.js). */

const _FIN_PANES = ['overview', 'pnl', 'bills', 'treasury', 'money', 'wallets', 'crypto'];
let _finLoaded = {};   // pane -> true once its renderer has run

async function renderFinance(pane) {
  _finLoaded = {};
  document.getElementById('main-content').innerHTML = `
    <div class="view-header">
      <div class="view-title">&#128176; Finance</div>
      <div class="view-sub">Everything money under one roof &mdash; the Treasury reserve, real-dollar missions,
        mainnet wallets and the crypto &amp; stock markets.</div>
    </div>
    <div class="subtab-bar" id="finance-subtabs">
      <div class="subtab" onclick="financeSub('overview')">&#127974; Overview</div>
      <div class="subtab" onclick="financeSub('pnl')">&#128181; Profit &amp; Loss</div>
      <div class="subtab" onclick="financeSub('bills')">&#128198; Bills</div>
      <div class="subtab" onclick="financeSub('treasury')">&#127963;&#65039; Treasury</div>
      <div class="subtab" onclick="financeSub('money')">&#128181; Missions &amp; Earn</div>
      <div class="subtab" onclick="financeSub('wallets')">&#128092; Wallets</div>
      <div class="subtab" onclick="financeSub('crypto')">&#128200; Markets</div>
    </div>
    ${_FIN_PANES.map(k => `<div class="settings-tabpane" id="fin-pane-${k}" style="display:none;">
      <div class="empty"><div class="empty-icon">&#9203;</div>Loading&#8230;</div>
    </div>`).join('')}`;
  financeSub(_FIN_PANES.includes(pane) ? pane : 'overview');
}
window.renderFinance = renderFinance;

function financeSub(k) {
  _FIN_PANES.forEach(name => {
    const pane = document.getElementById('fin-pane-' + name);
    if (pane) pane.style.display = (name === k) ? '' : 'none';
  });
  document.querySelectorAll('#finance-subtabs .subtab').forEach((el, i) => {
    el.classList.toggle('active', _FIN_PANES[i] === k);
  });
  if (!_finLoaded[k]) {
    _finLoaded[k] = true;
    ({ overview: finLoadOverview, pnl: finLoadPnl, bills: renderBills, treasury: renderTreasury,
       money: renderMoney, wallets: renderWallets, crypto: renderCrypto }[k])();
  }
}
window.financeSub = financeSub;

/* ── Overview — net-worth/status strip ─────────────────────────────────────
   Everything failure-tolerant (Promise.allSettled); a dead API just blanks
   its card. Wallet balances hit public block explorers and can take many
   seconds, so that card fills in lazily AFTER first paint. */

const _finCard = (onclick, icon, label, val, color, sub) => `
  <div class="stat-card" style="cursor:pointer;" onclick="${onclick}">
    <div class="stat-label">${icon} ${label}</div>
    <div class="stat-val" style="color:${color};">${val}</div>
    <div style="font-size:.68rem;color:var(--muted);margin-top:2px;">${sub || '&nbsp;'}</div>
  </div>`;

async function finLoadOverview() {
  const el = document.getElementById('fin-pane-overview');
  if (!el) return;
  const [ops, money, jelly] = await Promise.allSettled([
    api('/api/world/ops/summary'),
    api('/api/money/stats'),
    api('/api/jelly/status'),
  ]);

  let treasuryCard, moneyCard, jellyCard;
  if (ops.status === 'fulfilled') {
    const s = ops.value, h = _health(s);
    treasuryCard = _finCard(`financeSub('treasury')`, '&#127963;&#65039;', 'Treasury reserve',
      fmtUSD(s.balance_cents), (s.balance_cents || 0) >= 0 ? '#fcd34d' : 'var(--red)',
      `${h.t}${s.owed_cents ? ` &middot; ${fmtUSD(s.owed_cents)} owed` : ''}${s.pending_prayers ? ` &middot; ${s.pending_prayers} prayers pending` : ''}`);
  } else {
    treasuryCard = _finCard(`financeSub('treasury')`, '&#127963;&#65039;', 'Treasury reserve', '—', 'var(--muted)', 'unavailable');
  }
  if (money.status === 'fulfilled') {
    const m = money.value.missions || {};
    moneyCard = _finCard(`financeSub('money')`, '&#128181;', 'Money missions',
      `${m.proposed || 0} proposed`, 'var(--warn)',
      `pipeline $${(((money.value.pipeline_value_cents || 0)) / 100).toLocaleString(undefined, { maximumFractionDigits: 0 })}`);
  } else {
    moneyCard = _finCard(`financeSub('money')`, '&#128181;', 'Money missions', '—', 'var(--muted)', 'unavailable');
  }
  if (jelly.status === 'fulfilled') {
    const j = jelly.value;
    jellyCard = _finCard(`financeSub('crypto')`, '&#129724;', 'JellyCoin supply',
      `${Number(j.supply || 0).toLocaleString(undefined, { maximumFractionDigits: 1 })} JLY`, 'var(--accent2)',
      `height ${j.height ?? '—'} &middot; ${j.miners_online || 0} miner(s) online`);
  } else {
    jellyCard = _finCard(`financeSub('crypto')`, '&#129724;', 'JellyCoin', '—', 'var(--muted)', 'chain unavailable');
  }
  const walletCard = _finCard(`financeSub('wallets')`, '&#128092;', 'Wallet balances',
    '<span id="fin-ov-wallets">&#9203;</span>', 'var(--text)',
    '<span id="fin-ov-wallets-sub">checking public explorers&hellip;</span>');

  el.innerHTML = `
    <div class="stats-row" style="margin-bottom:16px;">
      ${treasuryCard}${moneyCard}${walletCard}${jellyCard}
    </div>
    <div style="font-size:.74rem;color:var(--muted);line-height:1.7;">
      Click a card to open its pane. &#127963;&#65039; Treasury holds the real-dollar reserve &amp; ledger,
      &#128181; Missions &amp; Earn is the real-money lead queue + Cash App,
      &#128092; Wallets are your mainnet coins, and &#128200; Markets covers
      crypto stats, JellyCoin/Pearl, mining, trading &amp; stocks.
    </div>`;

  finLoadOverviewWallets();   // slow (public explorers) — fills in after paint, no await
}
window.finLoadOverview = finLoadOverview;

async function finLoadOverviewWallets() {
  let d;
  try { d = await api('/api/wallets'); } catch { d = null; }
  const v = document.getElementById('fin-ov-wallets');
  const sub = document.getElementById('fin-ov-wallets-sub');
  if (!v) return;   // user re-rendered or left the view — never clobber anything
  if (!d || !Array.isArray(d.coins)) {
    v.textContent = '—';
    if (sub) sub.textContent = 'explorers unavailable';
    return;
  }
  const funded = d.coins.filter(c => (c.balance || 0) > 0);
  if (!funded.length) {
    v.textContent = '0 funded';
    if (sub) sub.textContent = `${d.coins.length} coins watched — no balance detected`;
    return;
  }
  v.textContent = `${funded.length} funded`;
  if (sub) sub.innerHTML = funded.map(c =>
    `<b>${Number(c.balance).toLocaleString(undefined, { maximumFractionDigits: 6 })}</b> ${esc(c.sym)}`).join(' &middot; ');
}

/* ── 💵 Profit & Loss — Printify→Etsy margin view ──────────────────────────
   The only live sales channel is Printify-fulfilled Etsy orders. Data comes
   from GET /api/pnl (Printify orders API + estimated Etsy fees). Nothing has
   sold yet, so this shows a ready/zero state — it populates automatically on
   the first sale. Period toggle: All / 30d / MTD. */

let _pnlPeriod = 'all';

async function finLoadPnl() {
  const el = document.getElementById('fin-pane-pnl');
  if (!el) return;
  el.innerHTML = `<div class="empty"><div class="empty-icon">&#9203;</div>Loading&#8230;</div>`;
  let d;
  try { d = await api('/api/pnl?period=' + encodeURIComponent(_pnlPeriod)); }
  catch { d = null; }
  if (!el.isConnected) return;
  if (!d) {
    el.innerHTML = `<div class="empty"><div class="empty-icon">&#9888;&#65039;</div>
      Couldn&rsquo;t load the P&amp;L just now.
      <div style="margin-top:8px;"><button class="btn" onclick="finLoadPnl()">Retry</button></div></div>`;
    return;
  }
  el.innerHTML = _pnlHtml(d);
}
window.finLoadPnl = finLoadPnl;

function finPnlPeriod(p) {
  _pnlPeriod = p;
  finLoadPnl();
}
window.finPnlPeriod = finPnlPeriod;

function _pnlToggle() {
  const btn = (p, label) =>
    `<div class="subtab${_pnlPeriod === p ? ' active' : ''}" onclick="finPnlPeriod('${p}')">${label}</div>`;
  return `<div class="subtab-bar" style="margin-bottom:14px;">
    ${btn('all', 'All time')}${btn('30d', 'Last 30 days')}${btn('mtd', 'This month')}
  </div>`;
}

function _pnlHtml(d) {
  const net = d.net_cents || 0;
  const hasSales = (d.orders || 0) > 0;
  const netColor = net > 0 ? 'var(--green,#34d399)' : (net < 0 ? 'var(--red,#f87171)' : 'var(--muted)');
  const marginColor = (d.margin_pct || 0) > 0 ? 'var(--green,#34d399)'
    : ((d.margin_pct || 0) < 0 ? 'var(--red,#f87171)' : 'var(--muted)');

  const cards = `
    <div class="stats-row" style="margin-bottom:16px;">
      ${_finCard('', '&#128176;', 'Revenue', fmtUSD(d.revenue_cents), 'var(--text)',
        `${d.orders || 0} order${(d.orders || 0) === 1 ? '' : 's'}`)}
      ${_finCard('', '&#127981;&#65039;', 'Printify cost', fmtUSD(d.cost_cents), 'var(--warn,#fbbf24)', 'fulfilment')}
      ${_finCard('', '&#127974;', 'Etsy fees', fmtUSD(d.fees_cents), 'var(--warn,#fbbf24)', 'estimated')}
      ${_finCard('', (net >= 0 ? '&#128200;' : '&#128201;'), 'Net margin', fmtUSD(net), netColor,
        `<b style="color:${marginColor};">${(d.margin_pct || 0).toFixed(1)}%</b> margin`)}
    </div>`;

  // Empty / ready state.
  const notice = !hasSales ? `
    <div class="empty" style="padding:26px 16px;">
      <div class="empty-icon">&#128722;</div>
      <div style="font-weight:600;margin-bottom:4px;">No sales yet</div>
      <div style="color:var(--muted);font-size:.82rem;line-height:1.6;max-width:440px;margin:0 auto;">
        ${esc(d.note || 'This lights up on your first Etsy order. 5 products are live.')}
      </div>
    </div>` : '';

  // Per-product breakdown.
  const byProd = (d.by_product || []);
  const prodRows = byProd.map(p => {
    const pnet = p.net_cents || 0;
    const c = pnet > 0 ? 'var(--green,#34d399)' : (pnet < 0 ? 'var(--red,#f87171)' : 'var(--muted)');
    return `<tr>
      <td style="padding:6px 8px;">${esc(p.title || 'Product')}</td>
      <td style="padding:6px 8px;text-align:right;">${p.units || p.orders || 0}</td>
      <td style="padding:6px 8px;text-align:right;">${fmtUSD(p.revenue_cents)}</td>
      <td style="padding:6px 8px;text-align:right;">${fmtUSD(p.cost_cents)}</td>
      <td style="padding:6px 8px;text-align:right;">${fmtUSD(p.fees_cents)}</td>
      <td style="padding:6px 8px;text-align:right;color:${c};font-weight:600;">${fmtUSD(pnet)}</td>
    </tr>`;
  }).join('');
  const prodTable = byProd.length ? `
    <div style="font-weight:600;margin:18px 0 8px;">By product</div>
    <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:.8rem;">
        <thead><tr style="color:var(--muted);text-align:left;border-bottom:1px solid var(--border,#333);">
          <th style="padding:6px 8px;">Product</th>
          <th style="padding:6px 8px;text-align:right;">Units</th>
          <th style="padding:6px 8px;text-align:right;">Revenue</th>
          <th style="padding:6px 8px;text-align:right;">Cost</th>
          <th style="padding:6px 8px;text-align:right;">Fees</th>
          <th style="padding:6px 8px;text-align:right;">Net</th>
        </tr></thead>
        <tbody>${prodRows}</tbody>
      </table>
    </div>` : '';

  const fm = d.fees_model || {};
  const feeNote = `
    <div style="font-size:.7rem;color:var(--muted);margin-top:16px;line-height:1.6;">
      Net = Etsy sale price &minus; Printify fulfilment cost &minus; estimated Etsy fees.
      Fee model: ${(+fm.pnl_etsy_txn_pct || 0)}% transaction + ${(+fm.pnl_etsy_processing_pct || 0)}% +
      ${fmtUSD(+fm.pnl_etsy_processing_flat_cents || 0)} processing + ${fmtUSD(+fm.pnl_etsy_listing_flat_cents || 0)} listing.
      Tunable in Settings (pnl_etsy_* keys).
    </div>`;

  return `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
      <div style="font-weight:700;font-size:1.02rem;">&#128181; Profit &amp; Loss</div>
    </div>
    <div style="color:var(--muted);font-size:.78rem;margin:4px 0 12px;">
      Live channel: Printify &rarr; Etsy. Real margin per sale, straight from Printify&rsquo;s order data.
    </div>
    ${_pnlToggle()}
    ${cards}
    ${notice}
    ${prodTable}
    ${feeNote}`;
}

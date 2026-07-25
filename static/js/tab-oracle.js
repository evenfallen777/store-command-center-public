/* ══ ORACLE TAB ══
   A forecasting TOURNAMENT: multiple LLM "analysts" compete to predict crypto/stock
   prices. Each call is scored on getting the direction right, how close the target
   was, and how far out it was called (longer correct calls score much higher). They
   keep a memory of past lessons and climb a leaderboard.

   Sub-tabs (same pane-toggle mechanism as Crypto/Settings — everything stays in the
   DOM, we only switch which pane is visible, and each pane lazy-loads once):
     🏆 Forecaster Leaderboard — ranked analysts + run-a-round controls + live progress
     🔮 Open calls  — predictions still awaiting their horizon
     📊 Results     — resolved predictions, scored ✓/✗ */

const _ORACLE_PANES = ['leaderboard', 'open', 'results'];
let _oracleLoaded = {};        // pane -> true once its data has been fetched
let _oracleRoundPoll = null;   // setTimeout handle for the live round view
let _oracleHistoryFilter = ''; // agent_id filter carried into the Open/Results panes

async function renderOracle() {
  _oracleLoaded = {};
  document.getElementById('main-content').innerHTML = `
    <div class="view-header">
      <div class="view-title">&#128302; Oracle</div>
      <div class="view-sub">A forecasting tournament &mdash; LLM analysts compete to predict crypto &amp;
        stock prices, get scored on accuracy and how far out they called it, remember their lessons,
        and climb a leaderboard. Nothing here trades real money.<br>
        Looking for your town's leaderboard? That's on The Company tab &rarr; &#127970; Company panel.</div>
    </div>
    <div class="subtab-bar" id="oracle-subtabs">
      <div class="subtab active" onclick="oracleSub('leaderboard')">&#127942; Forecaster Leaderboard</div>
      <div class="subtab" onclick="oracleSub('open')">&#128302; Open calls</div>
      <div class="subtab" onclick="oracleSub('results')">&#128202; Results</div>
    </div>
    ${_ORACLE_PANES.map(k => `<div class="settings-tabpane" id="pane-oracle-${k}"${k === 'leaderboard' ? '' : ' style="display:none;"'}>
      <div class="empty"><div class="empty-icon">&#9203;</div>Loading&#8230;</div>
    </div>`).join('')}`;
  oracleSub('leaderboard');
}
window.renderOracle = renderOracle;

function oracleSub(k) {
  _ORACLE_PANES.forEach(name => {
    const pane = document.getElementById('pane-oracle-' + name);
    if (pane) pane.style.display = (name === k) ? '' : 'none';
  });
  document.querySelectorAll('#oracle-subtabs .subtab').forEach((el, i) => {
    el.classList.toggle('active', _ORACLE_PANES[i] === k);
  });
  if (!_oracleLoaded[k]) {
    _oracleLoaded[k] = true;
    ({ leaderboard: oracleLoadLeaderboard, open: oracleLoadOpen, results: oracleLoadResults }[k])();
  }
}
window.oracleSub = oracleSub;

/* ── formatting helpers ───────────────────────────────────────────────────── */
// Crypto can be sub-cent (DOGE/XRP) — don't force 2 decimals that hide the value.
// Use up to ~5 significant figures, more decimals for small numbers.
function _orUsd(v) {
  if (v == null || isNaN(v)) return '—';
  v = Number(v);
  const a = Math.abs(v);
  let dec;
  if (a === 0)      dec = 2;
  else if (a < 1)   dec = 6;
  else if (a < 100) dec = 4;
  else              dec = 2;
  return '$' + v.toLocaleString(undefined, { maximumFractionDigits: dec });
}
function _orPct(v, dp) {
  if (v == null || isNaN(v)) return '—';
  return Number(v).toFixed(dp != null ? dp : 1) + '%';
}
function _orDate(s) {
  if (!s) return '—';
  return esc(String(s).slice(0, 10));
}
function _orAssetBadge(asset, market) {
  const isStock = market === 'stock';
  const col = isStock ? 'var(--accent2)' : 'var(--warn)';
  return `<span style="font-size:.62rem;font-weight:700;color:${col};border:1px solid ${col};
    border-radius:10px;padding:2px 8px;text-transform:uppercase;letter-spacing:.03em;">${esc(asset || '?')}</span>`;
}
// ladder rung label + chip (7→1w, 14→2w) and a resolve-countdown, shared with the history views
function _orRungLabel(h) {
  if (h == null) return '—';
  h = Number(h);
  return h === 7 ? '1w' : (h === 14 ? '2w' : h + 'd');
}
function _orRungChip(h) {
  return `<span style="font-size:.62rem;font-weight:700;color:var(--accent);border:1px solid var(--accent);
    border-radius:10px;padding:1px 7px;letter-spacing:.03em;">${_orRungLabel(h)}</span>`;
}
function _orCountdown(iso) {
  if (!iso) return '—';
  const ms = new Date(String(iso).replace(' ', 'T')) - Date.now();
  if (isNaN(ms)) return '—';
  if (ms <= 0) return '<span style="color:var(--warn);font-weight:700;">due</span>';
  const h = ms / 3600000;
  const txt = h < 1 ? Math.max(1, Math.round(ms / 60000)) + 'm'
    : (h < 48 ? Math.round(h) + 'h' : Math.round(h / 24) + 'd');
  return `<span style="color:var(--muted);">in ${txt}</span>`;
}
function _orDir(dir) {
  const up = dir === 'up';
  const col = up ? 'var(--green)' : 'var(--red)';
  return `<span style="color:${col};font-weight:700;">${up ? '&#9650;' : '&#9660;'} ${up ? 'up' : 'down'}</span>`;
}
function _orConfBar(c) {
  const pct = Math.round((Number(c) || 0) * 100);
  return `<div title="confidence ${pct}%" style="display:flex;align-items:center;gap:6px;">
    <div style="flex:1;min-width:44px;height:6px;background:var(--surface);border-radius:4px;overflow:hidden;">
      <div style="height:100%;width:${pct}%;background:linear-gradient(90deg,var(--accent),var(--accent2));"></div></div>
    <span style="font-size:.7rem;color:var(--muted);">${pct}%</span></div>`;
}

/* ── 🏆 LEADERBOARD ───────────────────────────────────────────────────────── */
async function oracleLoadLeaderboard() {
  const pane = document.getElementById('pane-oracle-leaderboard');
  let d, llmOpts = [], orSt = null, orAssets = null;
  try {
    const [dd, lm, st, as] = await Promise.all([
      api('/api/oracle/leaderboard'),
      api('/api/settings/llm-models').catch(() => ({ models: [] })),
      api('/api/oracle/settings').catch(() => null),   // graceful: null until the server restart
      api('/api/oracle/assets').catch(() => null),     // graceful: null until the server restart
    ]);
    d = dd; llmOpts = lm.models || []; orSt = st; orAssets = as;
  }
  catch (e) { pane.innerHTML = `<div class="empty"><div class="empty-icon">&#10060;</div>${esc(e.message)}</div>`; return; }
  const lb = d.leaderboard || [];
  const medal = (i) => ['🥇', '🥈', '🥉'][i] || `#${i + 1}`;

  const rows = lb.map((a, i) => {
    const s = a.stats || {};
    const score = Number(s.score || 0);
    const scoreCol = score > 0 ? 'var(--green)' : (score < 0 ? 'var(--red)' : 'var(--muted)');
    const rungChips = (s.rungs || []).map(r =>
      `<span title="${r.resolved} resolved at ${_orRungLabel(r.h)}">${_orRungLabel(r.h)}&nbsp;${r.accuracy != null ? Math.round(r.accuracy) + '%' : '—'}</span>`
    ).join(' · ');
    return `
      <tr style="border-top:1px solid var(--border);${a.active ? '' : 'opacity:.5;'}">
        <td style="padding:7px 10px;font-size:.95rem;white-space:nowrap;">${medal(i)}</td>
        <td style="padding:7px 10px;">
          <div style="font-weight:600;">${esc(a.name)}</div>
          <div style="font-size:.68rem;color:var(--muted);">${esc(a.model || '')}</div>
        </td>
        <td style="padding:7px 10px;text-align:right;font-weight:700;color:${scoreCol};">${score > 0 ? '+' : ''}${score.toFixed(1)}</td>
        <td style="padding:7px 10px;text-align:right;color:var(--muted);">${s.accuracy != null ? _orPct(s.accuracy, 0) : '—'}
          ${rungChips ? `<div style="font-size:.62rem;color:var(--muted);white-space:nowrap;" title="Per-rung accuracy on resolved calls">${rungChips}</div>` : ''}</td>
        <td style="padding:7px 10px;text-align:right;color:var(--muted);">${s.resolved ?? 0}</td>
        <td style="padding:7px 10px;text-align:right;color:var(--muted);">${s.open ?? 0}</td>
        <td style="padding:7px 10px;text-align:right;color:var(--muted);">${s.avg_horizon != null ? Number(s.avg_horizon).toFixed(1) + 'd' : '—'}</td>
        <td style="padding:7px 10px;text-align:right;white-space:nowrap;">
          <button class="btn-sm" onclick="oracleMemory(${a.id})" title="See the lessons this analyst has learned from past calls.">&#129504; Memory</button>
          <button class="btn-sm" onclick="oracleHistory(${a.id})" title="Filter Open calls & Results to just this analyst.">History</button>
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:.7rem;color:var(--muted);margin-left:4px;cursor:pointer;"
            title="Bench a model to keep it out of new tournament rounds.">
            <input type="checkbox" ${a.active ? 'checked' : ''} onchange="oracleToggle(${a.id},this.checked)"> active</label>
        </td>
      </tr>
      <tr id="or-mem-${a.id}" style="display:none;"><td colspan="8" style="padding:0 10px 10px 10px;"></td></tr>`;
  }).join('');

  pane.innerHTML = `
    <div class="section-header">
      <div><div class="section-title">&#127942; Forecaster Leaderboard</div>
        <div class="section-sub">Models compete to forecast prices with a LADDER of calls per asset
          (1d / 3d / 5d / 1w / 2w). Every rung scores independently on direction + horizon-scaled closeness
          &mdash; a correct 2-week call beats a correct 1-day call modestly.</div></div>
      <button class="btn-sm" onclick="_oracleLoaded.leaderboard=false;oracleSub('leaderboard')">&#8635; Refresh</button>
    </div>

    ${lb.length ? `
    <div class="settings-group" style="max-width:900px;overflow-x:auto;margin-bottom:16px;">
      <table style="width:100%;border-collapse:collapse;font-size:.8rem;">
        <thead><tr style="text-align:left;color:var(--muted);font-size:.66rem;text-transform:uppercase;letter-spacing:.04em;">
          <th style="padding:6px 10px;">#</th><th style="padding:6px 10px;">Analyst</th>
          <th style="padding:6px 10px;text-align:right;">Score ${hlp('Points for calling direction right + how close the price was; not a dollar amount — nothing here trades real money.')}</th>
          <th style="padding:6px 10px;text-align:right;">Accuracy ${hlp('Share of this analyst\'s resolved calls that landed on the right side of the price.')}</th>
          <th style="padding:6px 10px;text-align:right;">Resolved</th>
          <th style="padding:6px 10px;text-align:right;">Open</th>
          <th style="padding:6px 10px;text-align:right;">Avg horizon ${hlp('How many days out this analyst typically calls — longer correct calls score higher.')}</th>
          <th style="padding:6px 10px;text-align:right;"></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`
    : `<div class="empty"><div class="empty-icon">&#128302;</div>Run your first tournament round to see analysts compete.</div>`}

    <details class="settings-group" style="max-width:900px;margin-bottom:16px;">
      <summary style="cursor:pointer;font-weight:700;">&#9881;&#65039; Round settings</summary>
      <div class="settings-group" style="max-width:900px;margin:12px 0 0 0;border-color:var(--accent);">
        <div class="settings-group-title">&#128302; Run a tournament round</div>
        <div style="font-size:.76rem;color:var(--muted);margin-bottom:10px;">
          Each active analyst studies the market and makes a fresh forecast for the chosen number of assets.
          Calls resolve automatically once their horizon arrives &mdash; or hit <b>Resolve due now</b> to score any that are ready.
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <label style="font-size:.76rem;color:var(--muted);">How many assets:
            <input type="number" id="or-round-n" min="1" max="12" value="3" style="width:64px;margin-left:4px;"
              title="How many different assets each analyst forecasts this round (1–12).">
            ${hlp('Each active analyst makes one forecast per asset. More assets = a bigger, slower round.')}</label>
          <button class="btn-sm primary" id="or-round-btn" onclick="oracleStartRound()">&#128302; Run tournament round</button>
          <button class="btn-sm" id="or-resolve-btn" onclick="oracleResolve()"
            title="Score any open predictions whose horizon date has already passed.">&#9878;&#65039; Resolve due now</button>
        </div>
        <div id="or-round-status" style="margin-top:10px;"></div>
      </div>

      ${_orSettingsGroup(orSt)}
      ${_orAssetsGroup(orAssets)}
    </details>
    ${_orManage(lb, llmOpts)}`;

  if (_oracleRoundPoll) oraclePollRound();   // resume the live progress view if a round is running
}

/* ── ⚙️ oracle settings: cadence / ladder rungs / company hookup ──────────── */
const _OR_RUNGS = [1, 3, 5, 7, 14];   // the standard ladder; 30d is the optional long tier
function _orSettingsGroup(st) {
  if (!st || !st.settings) {
    return `<div class="settings-group" style="max-width:900px;margin-bottom:16px;">
      <div class="settings-group-title">&#9881;&#65039; Oracle settings</div>
      <div style="font-size:.74rem;color:var(--muted);">Settings API not reachable yet
        (pending a server restart) &mdash; the ladder runs on its defaults: 1d / 3d / 5d / 1w / 2w.</div></div>`;
  }
  const s = st.settings;
  const ladder = String(s.oracle_ladder || '1,3,5,7,14').split(',').map(x => parseInt(x, 10));
  const chk = (id, label, on, hint) => `
    <label style="display:inline-flex;align-items:center;gap:6px;font-size:.76rem;color:var(--text);cursor:pointer;margin-right:14px;"
      ${hint ? `title="${esc(hint)}"` : ''}>
      <input type="checkbox" id="${id}" ${on ? 'checked' : ''}> ${label}</label>`;
  return `
    <div class="settings-group" style="max-width:900px;margin-bottom:16px;">
      <div class="settings-group-title">&#9881;&#65039; Oracle settings</div>
      <div style="margin-bottom:8px;">
        ${chk('or-set-auto', '&#128302; Auto-pilot (resolve due calls every 15 min)', String(s.oracle_auto).toLowerCase() !== 'off',
              'Master switch for the background loop: it scores due rungs every 15 minutes so 1-day calls resolve on time.')}
        ${chk('or-set-rounds', '&#128197; One autonomous round per day', s.oracle_auto_rounds === '1',
              'When auto-pilot is on, kick off one fresh tournament round per day.')}
        ${chk('or-set-hookup', '&#127970; Company may cite the consensus', s.oracle_company_hookup === '1',
              'The world strategy/leaders, crypto strategy drafts and money reviews may cite the accuracy-weighted consensus. Advisory only — never an automatic action.')}
      </div>
      <div style="font-size:.72rem;color:var(--muted);margin-bottom:4px;">Ladder rungs &mdash; each forecast makes one call per enabled horizon:</div>
      <div>
        ${_OR_RUNGS.map(h => chk('or-rung-' + h, _orRungLabel(h), ladder.includes(h),
          `Include the ${_orRungLabel(h)} horizon in every forecast ladder.`)).join('')}
        ${chk('or-set-long', '30d long tier', s.oracle_long_tier === '1',
              'Optionally add a 30-day long-tier rung to every ladder.')}
      </div>
      <div style="margin-top:10px;">
        <button class="btn-sm primary" onclick="oracleSaveSettings()">&#128190; Save settings</button>
        <span style="font-size:.7rem;color:var(--muted);margin-left:8px;">Also editable from the God panel.</span>
      </div>
    </div>`;
}
async function oracleSaveSettings() {
  const ladder = _OR_RUNGS.filter(h => document.getElementById('or-rung-' + h)?.checked);
  if (!ladder.length) { toast('Enable at least one ladder rung', 'error'); return; }
  const body = { settings: {
    oracle_auto: document.getElementById('or-set-auto')?.checked ? 'on' : 'off',
    oracle_auto_rounds: document.getElementById('or-set-rounds')?.checked ? '1' : '0',
    oracle_company_hookup: document.getElementById('or-set-hookup')?.checked ? '1' : '0',
    oracle_long_tier: document.getElementById('or-set-long')?.checked ? '1' : '0',
    oracle_ladder: ladder.join(','),
  } };
  try {
    await api('/api/oracle/settings', { method: 'POST', body: JSON.stringify(body) });
    toast('Oracle settings saved');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.oracleSaveSettings = oracleSaveSettings;

/* ── 🎯 what we predict: owner-editable crypto + stock asset set ─────────────
   Crypto needs a CoinGecko id per symbol to resolve a real price for scoring
   (see routers/oracle/_base.py crypto_ids), so adding one takes symbol + id.
   Stocks reuse the same stocks_watchlist setting the Crypto tab edits. */
function _orAssetChip(label, onRemove, title) {
  return `<span style="display:inline-flex;align-items:center;gap:5px;font-size:.72rem;font-weight:600;
    color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:12px;
    padding:3px 6px 3px 10px;margin:3px 6px 3px 0;" ${title ? `title="${esc(title)}"` : ''}>
    ${esc(label)}
    <button onclick="${onRemove}" title="Remove"
      style="border:none;background:none;color:var(--muted);cursor:pointer;font-size:.85rem;line-height:1;padding:2px 4px;">&times;</button>
  </span>`;
}
function _orAssetsGroup(as) {
  if (!as) {
    return `<div class="settings-group" style="max-width:900px;margin-bottom:16px;">
      <div class="settings-group-title">&#127919; What we predict</div>
      <div style="font-size:.74rem;color:var(--muted);">Asset config API not reachable yet
        (pending a server restart) &mdash; tracking today's defaults: BTC / ETH / SOL / XRP / DOGE
        + your stock watchlist.</div></div>`;
  }
  const cryptoChips = Object.entries(as.crypto || {}).map(([sym, id]) =>
    _orAssetChip(sym, `oracleRemoveCrypto('${esc(sym)}')`, `CoinGecko id: ${id}`)).join('');
  const stockChips = (as.stocks || []).map(sym =>
    _orAssetChip(sym, `oracleRemoveStock('${esc(sym)}')`)).join('');
  return `
    <div class="settings-group" style="max-width:900px;margin-bottom:16px;">
      <div class="settings-group-title">&#127919; What we predict</div>
      <div style="font-size:.74rem;color:var(--muted);margin-bottom:10px;">
        The tournament forecasts these assets each round. Defaults are BTC / ETH / SOL / XRP / DOGE
        &mdash; add or remove crypto and stocks here.</div>

      <div style="font-size:.72rem;font-weight:700;color:var(--text);margin-bottom:2px;">Crypto</div>
      <div style="margin-bottom:8px;">${cryptoChips || '<span style="color:var(--muted);font-size:.74rem;">none tracked</span>'}</div>
      <div style="display:flex;gap:6px;align-items:flex-end;flex-wrap:wrap;margin-bottom:14px;">
        <div class="field" style="margin:0;">
          <label style="font-size:.7rem;">Symbol
            <input type="text" id="or-add-crypto-sym" placeholder="KAS" maxlength="12" style="width:80px;"></label>
        </div>
        <div class="field" style="margin:0;">
          <label style="font-size:.7rem;">CoinGecko id ${hlp('The id CoinGecko uses for this coin — open the coin\'s page on coingecko.com and take the last part of the URL, e.g. coingecko.com/en/coins/kaspa → id is "kaspa". Needed so the Oracle can fetch a real price to score the calls against.')}
            <input type="text" id="or-add-crypto-id" placeholder="kaspa" style="width:120px;"></label>
        </div>
        <button class="btn-sm primary" onclick="oracleAddCrypto()">&#10133; Add crypto</button>
        <a href="https://www.coingecko.com/" target="_blank" rel="noopener" style="font-size:.7rem;color:var(--accent);text-decoration:none;">Find an id on CoinGecko &#8599;</a>
      </div>

      <div style="font-size:.72rem;font-weight:700;color:var(--text);margin-bottom:2px;">Stocks ${hlp('Shared with the Crypto tab → Stocks → Watchlist — editing here updates the same list.')}</div>
      <div style="margin-bottom:8px;">${stockChips || '<span style="color:var(--muted);font-size:.74rem;">none on the watchlist</span>'}</div>
      <div style="display:flex;gap:6px;align-items:flex-end;flex-wrap:wrap;">
        <div class="field" style="margin:0;">
          <label style="font-size:.7rem;">Ticker
            <input type="text" id="or-add-stock-sym" placeholder="AAPL" maxlength="10" style="width:90px;"></label>
        </div>
        <button class="btn-sm primary" onclick="oracleAddStock()">&#10133; Add stock</button>
      </div>
    </div>`;
}

function _orAssetsReload() {
  _oracleLoaded.leaderboard = false;
  oracleSub('leaderboard');
}

async function oracleAddCrypto() {
  const symbol = document.getElementById('or-add-crypto-sym')?.value.trim();
  const coingecko_id = document.getElementById('or-add-crypto-id')?.value.trim();
  if (!symbol || !coingecko_id) { toast('Symbol and CoinGecko id are both required', 'error'); return; }
  try {
    await api('/api/oracle/assets/crypto', { method: 'POST', body: JSON.stringify({ symbol, coingecko_id }) });
    toast(`${symbol.toUpperCase()} added ✓`);
    _orAssetsReload();
  } catch (e) { toast('Add failed: ' + e.message, 'error'); }
}
window.oracleAddCrypto = oracleAddCrypto;

async function oracleRemoveCrypto(symbol) {
  try {
    await api('/api/oracle/assets/crypto/' + encodeURIComponent(symbol), { method: 'DELETE' });
    toast(`${symbol} removed`);
    _orAssetsReload();
  } catch (e) { toast('Remove failed: ' + e.message, 'error'); }
}
window.oracleRemoveCrypto = oracleRemoveCrypto;

async function oracleAddStock() {
  const sym = document.getElementById('or-add-stock-sym')?.value.trim().toUpperCase();
  if (!sym) { toast('Enter a ticker', 'error'); return; }
  try {
    const cur = await api('/api/oracle/assets');
    const syms = new Set(cur.stocks || []);
    syms.add(sym);
    await api('/api/crypto/settings', { method: 'POST', body: JSON.stringify({ stocks_watchlist: [...syms].join(',') }) });
    toast(`${sym} added ✓`);
    _orAssetsReload();
  } catch (e) { toast('Add failed: ' + e.message, 'error'); }
}
window.oracleAddStock = oracleAddStock;

async function oracleRemoveStock(symbol) {
  try {
    const cur = await api('/api/oracle/assets');
    const syms = (cur.stocks || []).filter(s => s !== symbol);
    await api('/api/crypto/settings', { method: 'POST', body: JSON.stringify({ stocks_watchlist: syms.join(',') }) });
    toast(`${symbol} removed`);
    _orAssetsReload();
  } catch (e) { toast('Remove failed: ' + e.message, 'error'); }
}
window.oracleRemoveStock = oracleRemoveStock;

/* ── 🧑‍🔬 manage analysts: add / retire / change model ── */
function _orModelSel(id, current, llmOpts) {
  const opts = [];
  if (current && !llmOpts.includes(current)) opts.push(`<option value="${esc(current)}" selected>${esc(current)} (saved)</option>`);
  opts.push(...llmOpts.map(m => `<option value="${esc(m)}" ${m === current ? 'selected' : ''}>${esc(m)}</option>`));
  return `<select id="${id}" style="min-width:230px;max-width:340px;padding:4px 7px;background:var(--card);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:.72rem;">${opts.join('')}</select>`;
}
function _orManage(lb, llmOpts) {
  const rows = lb.map(a => `
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:5px 0;">
      <input type="text" id="or-nm-${a.id}" value="${esc(a.name)}" style="width:140px;padding:4px 7px;background:var(--card);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:.74rem;">
      ${_orModelSel('or-md-' + a.id, a.model || '', llmOpts)}
      <button class="btn-sm" onclick="oracleSaveAgent(${a.id})" title="Save this analyst's name/model — future rounds use the new model; past scores stay theirs.">Save</button>
      <button class="btn-sm" onclick="oracleDeleteAgent(${a.id}, '${esc(a.name)}')" title="Retire this analyst from the tournament (its history stays in the DB).">&#128465;&#65039;</button>
    </div>`).join('');
  return `
    <div class="settings-group" style="max-width:900px;margin-top:16px;">
      <div class="settings-group-title">&#129489;&#8205;&#128300; Manage analysts</div>
      <div style="font-size:.72rem;color:var(--muted);margin-bottom:8px;">Any LM Studio model on the node can compete. Change a model to re-arm an analyst; retire ones that waste GPU time. Use the <i>active</i> checkbox above to bench without retiring.</div>
      ${rows}
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px;padding-top:8px;border-top:1px solid var(--border);">
        <input type="text" id="or-add-name" placeholder="Analyst name" style="width:140px;padding:4px 7px;background:var(--card);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:.74rem;">
        ${_orModelSel('or-add-model', '', llmOpts)}
        <button class="btn-sm primary" onclick="oracleAddAgent()">+ Add analyst</button>
      </div>
    </div>`;
}
async function oracleAddAgent() {
  const name = document.getElementById('or-add-name')?.value?.trim();
  const model = document.getElementById('or-add-model')?.value;
  if (!name || !model) { toast('Name and model are both required', 'error'); return; }
  try {
    await api('/api/oracle/agents', { method: 'POST', body: JSON.stringify({ name, model }) });
    toast(`🔮 ${name} joins the tournament`);
    _oracleLoaded.leaderboard = false; oracleSub('leaderboard');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
async function oracleSaveAgent(id) {
  const name = document.getElementById('or-nm-' + id)?.value?.trim();
  const model = document.getElementById('or-md-' + id)?.value;
  try {
    await api(`/api/oracle/agents/${id}`, { method: 'POST', body: JSON.stringify({ name, model }) });
    toast('Analyst updated');
    _oracleLoaded.leaderboard = false; oracleSub('leaderboard');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
async function oracleDeleteAgent(id, name) {
  if (!confirm(`Retire ${name} from the tournament?`)) return;
  try {
    await api(`/api/oracle/agents/${id}`, { method: 'DELETE' });
    toast(`${name} retired`);
    _oracleLoaded.leaderboard = false; oracleSub('leaderboard');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.oracleAddAgent = oracleAddAgent; window.oracleSaveAgent = oracleSaveAgent; window.oracleDeleteAgent = oracleDeleteAgent;
window.oracleLoadLeaderboard = oracleLoadLeaderboard;

async function oracleToggle(id, active) {
  try {
    await api(`/api/oracle/agents/${id}/toggle`, { method: 'POST', body: JSON.stringify({ active }) });
    toast(active ? 'Analyst activated' : 'Analyst benched');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  _oracleLoaded.leaderboard = false; oracleSub('leaderboard');
}
window.oracleToggle = oracleToggle;

async function oracleMemory(id) {
  const tr = document.getElementById('or-mem-' + id);
  if (!tr) return;
  if (tr.style.display !== 'none') { tr.style.display = 'none'; return; }
  tr.style.display = '';
  const cell = tr.firstElementChild;
  cell.innerHTML = '<div style="color:var(--muted);font-size:.76rem;">Loading lessons…</div>';
  let d;
  try { d = await api(`/api/oracle/memory/${id}?limit=25`); }
  catch (e) { cell.innerHTML = `<div style="color:var(--red);font-size:.76rem;">${esc(e.message)}</div>`; return; }
  const lessons = d.lessons || [];
  cell.innerHTML = `
    <div class="settings-group" style="margin:4px 0 0 0;border-color:var(--accent2);">
      <div class="settings-group-title">&#129504; Lessons learned</div>
      ${lessons.length ? lessons.map(l => `
        <div style="font-size:.76rem;color:var(--text);line-height:1.6;padding:5px 0;border-top:1px solid var(--border);">
          ${esc(l.text)}
          <span style="color:var(--muted);font-size:.66rem;margin-left:6px;">${_orDate(l.created_at)}</span>
        </div>`).join('')
      : '<div style="font-size:.76rem;color:var(--muted);">No lessons yet — this analyst learns as its calls resolve.</div>'}
    </div>`;
}
window.oracleMemory = oracleMemory;

// "History" jumps to the Open-calls pane filtered to a single analyst.
function oracleHistory(id) {
  _oracleHistoryFilter = String(id);
  _oracleLoaded.open = false;
  _oracleLoaded.results = false;
  oracleSub('open');
}
window.oracleHistory = oracleHistory;

let _oracleRoundStarting = false;
async function oracleStartRound() {
  const n = parseInt(document.getElementById('or-round-n').value, 10) || 3;
  const btn = document.getElementById('or-round-btn');
  try {
    await api('/api/oracle/round', { method: 'POST', body: JSON.stringify({ assets: n }) });
    toast(`Tournament round started — ${n} asset(s) per analyst`);
  } catch (e) {
    toast(e.message && /409|running/i.test(e.message) ? 'A round is already running' : ('Error: ' + e.message), 'error');
    return;
  }
  if (btn) btn.disabled = true;
  oraclePollRound();
}
window.oracleStartRound = oracleStartRound;

async function oraclePollRound() {
  let st;
  try { st = await api('/api/oracle/round/status'); } catch { return; }
  const box = document.getElementById('or-round-status');
  const btn = document.getElementById('or-round-btn');
  if (box) {
    const pct = st.target ? Math.round((st.done / st.target) * 100) : 0;
    box.innerHTML = `
      <div style="font-size:.78rem;margin-bottom:6px;">
        ${st.running ? '⏳' : '✅'} ${st.done || 0}/${st.target || 0} done${st.made != null ? ` · <b style="color:var(--green);">${st.made} call(s) made</b>` : ''}
        <div style="height:6px;background:var(--surface);border-radius:4px;margin-top:5px;overflow:hidden;">
          <div style="height:100%;width:${pct}%;background:linear-gradient(90deg,var(--accent),var(--accent2));"></div></div>
      </div>
      <pre style="font-size:.68rem;color:var(--muted);background:var(--surface);border-radius:6px;padding:8px 10px;max-height:180px;overflow:auto;white-space:pre-wrap;margin:0;">${esc((st.log || []).slice(-12).join('\n'))}</pre>`;
  }
  if (st.running) {
    if (btn) btn.disabled = true;
    _oracleRoundPoll = setTimeout(oraclePollRound, 3000);
  } else {
    _oracleRoundPoll = null;
    if (btn) btn.disabled = false;
    // fresh calls landed — leaderboard & open pane are stale
    _oracleLoaded.open = false;
    oracleLoadLeaderboard();
  }
}
window.oraclePollRound = oraclePollRound;

async function oracleResolve() {
  const btn = document.getElementById('or-resolve-btn');
  const orig = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = '⏳ Resolving…'; }
  try {
    const r = await api('/api/oracle/resolve', { method: 'POST', body: JSON.stringify({}) });
    toast(`Resolved ${r && r.resolved != null ? r.resolved : 0} prediction(s)`);
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  if (btn) { btn.disabled = false; btn.innerHTML = orig; }
  _oracleLoaded.open = false;
  _oracleLoaded.results = false;
  oracleLoadLeaderboard();
}
window.oracleResolve = oracleResolve;


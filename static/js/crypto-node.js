/* ══ CRYPTO TAB — Stats / Nodes / Mining panes (split from tab-crypto.js; shares cryptoSub + _cy* helpers) ══ */

/* ── 📊 STATS ─────────────────────────────────────────────────────────────── */
async function cryptoLoadStats() {
  const pane = document.getElementById('pane-crypto-stats');
  let d;
  try { d = await api('/api/crypto/stats'); }
  catch (e) { pane.innerHTML = `<div class="empty"><div class="empty-icon">&#10060;</div>${esc(e.message)}</div>`; return; }
  if (d.error && !(d.coins || []).length) {
    pane.innerHTML = `<div class="empty"><div class="empty-icon">&#128268;</div>${esc(d.error)}</div>`;
    return;
  }
  const trending = (d.trending || []).length ? `
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;align-items:center;">
      <span style="font-size:.72rem;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.05em;">&#128293; Trending</span>
      ${d.trending.map(t => `<span style="display:inline-flex;align-items:center;gap:5px;background:var(--surface2);border:1px solid var(--border);border-radius:20px;padding:4px 10px;font-size:.75rem;">
        ${t.thumb ? `<img src="${esc(t.thumb)}" style="width:16px;height:16px;border-radius:50%;" loading="lazy">` : ''}${esc(t.symbol || t.name)}${t.rank ? ` <span style="color:var(--muted);">#${t.rank}</span>` : ''}</span>`).join('')}
    </div>` : '';
  const rows = (d.coins || []).map(c => `
    <tr style="border-bottom:1px solid var(--border);">
      <td style="padding:7px 10px;color:var(--muted);">${c.rank ?? ''}</td>
      <td style="padding:7px 10px;font-weight:600;white-space:nowrap;">
        ${c.image ? `<img src="${esc(c.image)}" style="width:18px;height:18px;border-radius:50%;vertical-align:-4px;margin-right:7px;" loading="lazy">` : ''}${esc(c.name)}
        <span style="color:var(--muted);font-weight:400;">${esc(c.symbol)}</span></td>
      <td style="padding:7px 10px;text-align:right;">${_cyUsd(c.price)}</td>
      <td style="padding:7px 10px;text-align:right;">${_cyPct(c.chg24h)}</td>
      <td style="padding:7px 10px;text-align:right;">${_cyPct(c.chg7d)}</td>
      <td style="padding:7px 10px;text-align:right;color:var(--muted);">${_cyMcap(c.mcap)}</td>
    </tr>`).join('');
  pane.innerHTML = `
    <div class="section-header">
      <div><div class="section-title">&#128202; Top 20 by market cap</div>
        <div class="section-sub">CoinGecko free API · cached 2 min${d.fetched_at ? ' · as of ' + esc(d.fetched_at) : ''}</div></div>
      <button class="btn-sm" onclick="_cryptoLoaded.stats=false;cryptoSub('stats')">&#8635; Refresh</button>
    </div>
    ${trending}
    <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:.8rem;">
        <thead><tr style="color:var(--muted);text-align:left;border-bottom:1px solid var(--border);">
          <th style="padding:6px 10px;">#</th><th style="padding:6px 10px;">Coin</th>
          <th style="padding:6px 10px;text-align:right;">Price</th>
          <th style="padding:6px 10px;text-align:right;">24h</th>
          <th style="padding:6px 10px;text-align:right;">7d</th>
          <th style="padding:6px 10px;text-align:right;">Market cap</th>
        </tr></thead>
        <tbody>${rows || '<tr><td colspan="6" style="padding:14px;color:var(--muted);">No data.</td></tr>'}</tbody>
      </table>
    </div>`;
}
window.cryptoLoadStats = cryptoLoadStats;
/* ── ⛓️ NODES ─────────────────────────────────────────────────────────────── */

async function cryptoLoadNodes() {
  const pane = document.getElementById('pane-crypto-nodes');
  let d;
  try { d = await api('/api/crypto/nodes'); }
  catch (e) { pane.innerHTML = `<div class="empty"><div class="empty-icon">&#10060;</div>${esc(e.message)}</div>`; return; }
  pane.innerHTML = `
    <div class="section-header">
      <div><div class="section-title">&#9939;&#65039; Nodes</div>
        <div class="section-sub">Your spendable coins are real mainnet wallets in the <b>&#128092; Wallets</b> tab — no node, no download. Running your own full node is optional and only about self-sovereignty.</div></div>
    </div>
    <div class="settings-group" style="max-width:640px;margin-bottom:16px;border-color:var(--accent);">
      <div style="font-size:.8rem;color:var(--text);line-height:1.7;">&#128161; ${esc(d.note || '')}
        <br><a onclick="document.querySelector('.nav-item[data-view=wallets]').click()" style="color:var(--accent);cursor:pointer;font-weight:600;">Open Wallets &#8594;</a></div>
    </div>
    <div class="section-header"><div><div class="section-title">Full nodes you could run</div>
      <div class="section-sub">Honest notes on what each one really gets you at home (none are needed for the wallets to work).</div></div></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;">
      ${(d.catalog || []).map(n => `
        <div class="settings-group" style="padding:14px 16px;">
          <div style="font-weight:700;font-size:.88rem;margin-bottom:6px;">${esc(n.name)}
            <span style="font-size:.6rem;font-weight:700;background:var(--surface);border:1px solid var(--border);color:var(--muted);border-radius:10px;padding:2px 8px;margin-left:6px;text-transform:uppercase;">full node not installed</span></div>
          <div style="font-size:.74rem;color:var(--muted);line-height:1.6;">${esc(n.note)}</div>
        </div>`).join('')}
    </div>`;
}
window.cryptoLoadNodes = cryptoLoadNodes;

/* ── ⛏️ MINING (real Monero CPU mining via official xmrig) ─────────────────── */
async function cryptoLoadMining() {
  const pane = document.getElementById('pane-crypto-mining');
  let m = {};
  try { m = await api('/api/crypto/mining'); }
  catch (e) { pane.innerHTML = `<div class="empty"><div class="empty-icon">&#10060;</div>${esc(e.message)}</div>`; return; }
  const running = !!m.running;
  const enabled = !!m.mining_enabled;
  const agentAccess = !!m.agent_access;
  pane.innerHTML = `
    <div class="section-header"><div><div class="section-title">&#9935;&#65039; Mining</div>
      <div class="section-sub">Real Monero CPU mining via official xmrig ${esc(m.installed ? '(installed)' : '(NOT installed)')}. Off by default — this mines to your real XMR address.</div></div>
      <button class="btn-sm" onclick="_cryptoLoaded.mining=false;cryptoSub('mining')">&#8635; Refresh</button>
    </div>
    <div class="settings-group" style="max-width:600px;margin-bottom:16px;">
      <div class="settings-group-title">${_cyDot(running)}xmrig — Monero (RandomX, CPU)
        <span style="font-size:.62rem;font-weight:700;background:${running ? 'rgba(34,197,94,.16)' : 'rgba(148,163,184,.16)'};color:${running ? 'var(--green)' : 'var(--muted)'};border-radius:10px;padding:2px 8px;margin-left:8px;text-transform:uppercase;">${running ? 'mining' : 'stopped'}</span></div>
      <div style="font-size:.76rem;color:var(--muted);line-height:1.7;margin-bottom:12px;">
        Mines to <code style="word-break:break-all;">${esc(m.wallet || '(no XMR address yet — open Wallets)')}</code><br>
        Pool: <code>${esc(m.pool || '')}</code>
      </div>
      <label style="display:flex;align-items:center;gap:10px;font-size:.82rem;font-weight:600;margin-bottom:8px;cursor:pointer;">
        <input type="checkbox" id="cy-xmr-mining-enabled" ${enabled ? 'checked' : ''} onchange="cryptoToggleMining(this.checked)">
        Allow XMR mining ${hlp('Master gate (mirrors Pearl). While OFF, the Start button is refused by the server (403). Turning it ON does not start anything — it only unlocks the Start button.')}
      </label>
      <label style="display:flex;align-items:center;gap:10px;font-size:.82rem;font-weight:600;margin-bottom:12px;cursor:pointer;">
        <input type="checkbox" id="cy-xmr-agent-access" ${agentAccess ? 'checked' : ''} onchange="cryptoToggleAgentAccess(this.checked)">
        Allow agents to control mining ${hlp('Agent-access gate (default OFF). While OFF, only YOU (an authenticated browser session) can start/stop xmrig — the MCP tool / OpenClaw / any automation is refused with 403. Turn ON to let agents drive it (they still need the master toggle above too).')}
        <span style="font-size:.62rem;font-weight:700;background:${agentAccess ? 'rgba(245,158,11,.16)' : 'rgba(148,163,184,.16)'};color:${agentAccess ? 'var(--warn)' : 'var(--muted)'};border-radius:10px;padding:2px 8px;text-transform:uppercase;">${agentAccess ? 'agents allowed' : 'human-only'}</span>
      </label>
      <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-bottom:12px;">
        <div class="field" style="margin:0;flex:1;min-width:220px;">
          <label>Pool ${hlp('Monero mining pool host:port. Default MoneroOcean auto-switches to the most profitable algorithm.')}</label>
          <input id="cy-xmr-pool" value="${esc(m.pool || '')}" style="width:100%;">
        </div>
        <div class="field" style="margin:0;">
          <label>Threads ${hlp('0 = auto (xmrig picks). Lower it to leave CPU for the live sites — this box also serves WordPress + the Store.')}</label>
          <input type="number" id="cy-xmr-threads" min="0" max="8" value="${m.threads ?? 0}" style="width:90px;">
        </div>
        <button class="btn-sm" onclick="cryptoMiningConfig()">&#128190; Save</button>
      </div>
      <div style="display:flex;gap:8px;">
        <button class="btn-sm success" onclick="cryptoMiningAction('start')" ${(!enabled || running || !m.installed) ? 'disabled' : ''} title="${enabled ? '' : 'Enable the toggle first'}">&#9654; Start mining</button>
        <button class="btn-sm danger" onclick="cryptoMiningAction('stop')" ${running ? '' : 'disabled'}>&#9209; Stop</button>
      </div>
    </div>
    <div class="settings-group" style="max-width:600px;border-color:var(--warn);">
      <div class="settings-group-title" style="color:var(--warn);">&#9888;&#65039; Profitability reality check</div>
      <div style="font-size:.76rem;color:var(--muted);line-height:1.7;">
        Home CPU mining earns <b>pennies per day</b> — usually less than the electricity it
        burns, and it heats/loads this box while it serves your live sites. BTC / LTC / DOGE
        are ASIC-only; Ethereum has no mining at all. The realistic money paths here are the
        <b>&#129302; Trading</b> flow (paper-tested first) and the store's missions/products.
        Mining pays to your real XMR wallet, so anything earned is genuinely yours.
      </div>
    </div>`;
}
window.cryptoLoadMining = cryptoLoadMining;

async function cryptoMiningConfig() {
  const pool = document.getElementById('cy-xmr-pool').value;
  const threads = parseInt(document.getElementById('cy-xmr-threads').value, 10) || 0;
  try {
    await api('/api/crypto/mining/config', { method: 'POST', body: JSON.stringify({ pool, threads }) });
    toast('Mining config saved');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  _cryptoLoaded.mining = false; cryptoSub('mining');
}
window.cryptoMiningConfig = cryptoMiningConfig;

async function cryptoToggleMining(on) {
  try {
    await api('/api/crypto/mining/config', { method: 'POST',
      body: JSON.stringify({ mining_enabled: on ? '1' : '0' }) });
    toast(on ? 'XMR mining UNLOCKED — nothing started; use the Start button.'
             : 'XMR mining locked off.');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  _cryptoLoaded.mining = false; cryptoSub('mining');
}
window.cryptoToggleMining = cryptoToggleMining;

async function cryptoToggleAgentAccess(on) {
  try {
    await api('/api/crypto/mining/config', { method: 'POST',
      body: JSON.stringify({ agent_access: on ? '1' : '0' }) });
    toast(on ? 'Agents may now start/stop XMR mining (master toggle still required).'
             : 'Agent access locked off — only you can control XMR mining.');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  _cryptoLoaded.mining = false; cryptoSub('mining');
}
window.cryptoToggleAgentAccess = cryptoToggleAgentAccess;

async function cryptoMiningAction(action) {
  if (action === 'start' &&
      !confirm('Start xmrig CPU mining on this box?\n\nThis competes for CPU with WordPress/the Store and earns pennies/day.')) return;
  try {
    await api(`/api/crypto/mining/${action}`, { method: 'POST' });
    toast(`xmrig ${action}`);
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  _cryptoLoaded.mining = false; cryptoSub('mining');
}
window.cryptoMiningAction = cryptoMiningAction;

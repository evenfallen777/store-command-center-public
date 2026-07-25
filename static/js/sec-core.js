/**
 * Security Command Center — core.
 * Shared state, timers, dispatch (secTab/secDns), the shell renderer, and the
 * helpers used across 2+ security sub-views. Loads FIRST among the sec-*.js files.
 */

let _secTab = 'command';
let _secDnsTab = 'overview';
let _secLogTimer = null;
let _secConnTimer = null;

/* Legacy DNS views render into the pill container when it exists. */
const _secBody = () => document.getElementById('sec-dns-body') || document.getElementById('sec-body');

function _secStopTimers() {
  if (_secLogTimer) { clearInterval(_secLogTimer); _secLogTimer = null; }
  if (_secConnTimer) { clearInterval(_secConnTimer); _secConnTimer = null; }
}

async function renderNetworkSecurity() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="view-header">
      <div class="view-title">&#128737;&#65039; Network Security</div>
      <div class="view-sub">Who&rsquo;s connecting in &amp; out, who&rsquo;s knocking, your exposed surface &mdash; plus Pi-hole DNS guardian</div>
    </div>
    <div class="subtab-bar" id="sec-tabs">
      ${['command','redblue','connections','threats','audit','web','guardian','ai','dns','system'].map(t =>
        `<div class="subtab${t===_secTab?' active':''}" data-tab="${t}" onclick="secTab('${t}')">${{command:'🛡️ Command',redblue:'🔴🔵 Red/Blue',connections:'🌐 Connections',threats:'🚨 Threats',audit:'🔍 Audit',web:'🌍 Web Traffic',guardian:'🐺 Guardian',ai:'🤖 AI Shield',dns:'🧿 DNS (Pi-hole)',system:'🔐 System &amp; LLM'}[t]}</div>`).join('')}
    </div>
    <div id="sec-body"><div class="empty">Loading…</div></div>`;
  secTab(_secTab);
}
window.renderNetworkSecurity = renderNetworkSecurity;

const _SEC_DNS_TABS = ['overview','logs','devices','findings','blocklist'];

function secTab(t) {
  _secStopTimers();
  if (_SEC_DNS_TABS.includes(t)) { _secDnsTab = t; t = 'dns'; }   // old deep-links land in the DNS group
  _secTab = t;
  document.querySelectorAll('#sec-tabs .subtab').forEach(el =>
    el.classList.toggle('active', el.dataset.tab === t));
  ({ command: secCommand, redblue: secRedBlue, connections: secConnections, threats: secThreats, audit: secAudit, web: secWebTraffic, guardian: secGuardian, ai: secAIShield, dns: secDns, system: secSystem }[t] || secCommand)();
}
window.secTab = secTab;

/* ── DNS (Pi-hole) group: the legacy views under one roof ──────────────────── */
function secDns(pill) {
  _secStopTimers();
  if (pill) _secDnsTab = pill;
  const el = document.getElementById('sec-body');
  el.innerHTML = `
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
      ${_SEC_DNS_TABS.map(p => `<button class="btn-sm${p===_secDnsTab?' primary':''}" onclick="secDns('${p}')">
        ${{overview:'📊 Overview',logs:'📜 Live Logs',devices:'🖥️ Devices',findings:'🚩 Findings',blocklist:'⛔ Blocklist'}[p]}</button>`).join('')}
    </div>
    <div id="sec-dns-body"><div class="empty">Loading…</div></div>`;
  ({ overview: secOverview, logs: secLogs, devices: secDevices, findings: secFindings, blocklist: secBlocklist }[_secDnsTab] || secOverview)();
}
window.secDns = secDns;

function _ago(s) {
  if (s == null) return 'never';
  if (s < 90) return 'just now';
  if (s < 5400) return Math.round(s / 60) + 'm ago';
  if (s < 129600) return Math.round(s / 3600) + 'h ago';
  return Math.round(s / 86400) + 'd ago';
}

/* Severity palette — shared by Command (posture) and Threats. */
const _SEV = { critical: '#f87171', high: '#fb923c', medium: '#fbbf24', low: '#7a86a0' };

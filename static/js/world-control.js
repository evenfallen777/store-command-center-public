/* ══ THE COMPANY — Control Plane ══
   Mission control: one master switch for the whole Company, a toggle for every
   autonomy system, and trigger buttons for the Company's capabilities (its
   "mini-MCP"). Uses _worldModal + api()/esc()/toast(). Global-scope classic script. */

async function worldControl() {
  let d;
  try { d = await api('/api/world/control/panel'); }
  catch (e) { toast?.('Control panel failed to load'); return; }
  _renderControl(d);
}

/* Plain-language help for each autonomy toggle: what it turns on downstream. */
const _SYS_HELP = {
  create: 'Turns on the Studio making art/music/video/3D by itself on the GPU box; finished pieces land in the queue and in Review. Runs only while the master switch is on.',
  govern: 'Lets the Republic (the agent assembly) convene itself every few hours to set strategy and take action. Gated by the master switch.',
  cognition: 'Agents form thoughts, opinions and ideas via the local LLM on the GPU box. Free - no real money involved.',
  meetings: 'Periodic town-hall votes where the agents decide company direction.',
  incidents: 'Injects random in-world events to keep the simulation lively. Cosmetic - no real-world effect.',
  sell: 'Lets agents draft paid Etsy/Printify listings by themselves. Each one queues as a prayer in the God Console for you to approve (about 0.20 dollars per Etsy listing from the treasury).',
  swarm_cron: 'Advances cron-scheduled coding jobs in the Dev-Swarm on schedule.',
  sec_monitor: 'Watches the home network for new or changed devices.',
  sec_scan: 'Runs periodic Pi-hole and network config scans.',
  sec_analyze: 'Runs LLM threat analysis on the GPU box and flags suspicious activity.',
  sec_audit: 'Nightly hardening snapshot; any regression alerts you in the God Console.',
  guardian: 'Auto-blocks ad, tracker and ACR domains network-wide - never local or functional ones. Fully reversible.',
  ai_watch: 'Watches the AI agents themselves for rogue behaviour (payout or code bursts, unknown actors) and reports to the God Console.',
};

/* What each Capability trigger actually does when clicked. */
const _CAP_HELP = {
  make_art: 'Runs one Studio image generation now on the GPU box; the result lands in the queue and Review.',
  make_music: 'Runs one Studio music generation now on the GPU box; the result lands in the queue and Review.',
  make_video: 'Runs one Studio video generation now on the GPU box; the result lands in the queue and Review.',
  make_3d: 'Sculpts one 3D model now on the GPU box; the result lands in the queue and Review.',
  sell_etsy: 'Drafts an Etsy listing now; it appears as a prayer in the God Console for you to approve before it goes live (about 0.20 dollars).',
  sell_printify: 'Drafts a Printify listing now; it appears as a prayer in the God Console for you to approve before it goes live.',
  convene: 'Convenes the Republic assembly right now to strategize and act.',
  research: 'Asks you for a topic, then files a research request as a prayer in the God Console.',
  scan_trends: 'Scans market trends right now to surface product ideas.',
  sec_scan_now: 'Runs a one-off security scan of the network right now.',
};

function _pill(on, disabled) {
  const bg = on ? '#1f4a32' : '#26324a', dot = on ? 'right:2px' : 'left:2px', col = on ? '#6ee7a8' : '#54607a';
  return `<span style="display:inline-block;position:relative;width:38px;height:20px;border-radius:11px;background:${bg};border:1px solid ${col}44;vertical-align:middle;${disabled ? 'opacity:.5' : ''}">
    <span style="position:absolute;top:2px;${dot};width:14px;height:14px;border-radius:50%;background:${col};transition:all .12s"></span></span>`;
}

function _renderControl(d) {
  const m = d.master;

  const master = `
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:14px 16px;margin-bottom:16px;border-radius:12px;
      background:${m ? 'linear-gradient(135deg,#12251b,#0f1626)' : '#1a1414'};border:1px solid ${m ? '#2a5a3a' : '#5a2a2a'}">
      <div>
        <div style="font-weight:800;font-size:1.05rem;color:#e8eefc">${m ? '🟢 The Company is running' : '⏸️ The Company is dormant'}</div>
        <div style="font-size:.74rem;color:#8a97ad;margin-top:2px">Master switch — gates every autonomous system below. ${hlp('When off, every system below is forced off on its next tick regardless of its own switch. A system is effective only when this master AND its own toggle are on.')}</div>
      </div>
      <button class="btn" style="padding:8px 16px;font-weight:700;${m ? 'background:#5a2a2a;border-color:#7c3a3a' : 'background:#1f4a32;border-color:#2a5a3a;color:#6ee7a8'}" onclick="controlMaster(${m ? 'false' : 'true'})">${m ? '⏸ Pause all' : '▶ Wake the Company'}</button>
    </div>`;

  // run mode: how fast + how autonomous the sim runs
  const rm = d.run_mode || 'normal';
  const rmBtn = (id, label, desc) => `<button class="btn" title="${esc(desc)}" onclick="controlRunMode('${id}')"
    style="flex:1;padding:8px 6px;font-weight:600;${rm === id ? 'background:#1f3a5a;border-color:#3a6a9a;color:#8ecaff' : ''}">${label}</button>`;
  const runmode = `
    <div style="padding:10px 14px;margin-bottom:14px;border-radius:12px;background:#0e1626;border:1px solid #26324a">
      <div style="font-size:.74rem;color:#9fc0ff;font-weight:600;margin-bottom:6px">⏱️ Run mode <span style="color:#8a97ad;font-weight:400">· how fast + how autonomous the sim runs</span></div>
      <div style="display:flex;gap:6px">
        ${rmBtn('normal', '🐢 Normal', 'Real pace, your automation settings as-is.')}
        ${rmBtn('fast', '⚡ Fast', '~5x sim speed — watch the town work, gather and advance. LLM/GPU/money keep their own real cadences + gates.')}
        ${rmBtn('test', '🧪 Test', 'Fast + auto-run the FREE loops (art/work/era) so the world visibly progresses. Money & code stay gated — nothing real is spent or posted.')}
      </div>
    </div>`;

  // era building art: optional generated pixel-art per building per age
  const eraArt = `
    <div style="padding:10px 14px;margin-bottom:14px;border-radius:12px;background:#0e1626;border:1px solid #26324a">
      <div style="font-size:.74rem;color:#9fc0ff;font-weight:600;margin-bottom:4px">🏛️ Era building art
        <span style="color:#8a97ad;font-weight:400">· real pixel-art per building per age (🪵→🌙) ${hlp('Optional. Generates one pixel-art sprite per building-type per era, swapping in over the procedural restyle. Shares the sprite hourly GPU budget and generates a few at a time — safe to leave on. Procedural styling shows until each sprite is ready. Off by default.')}</span></div>
      <div id="era-art-status" style="font-size:.68rem;color:#7a86a0;margin-bottom:6px">…</div>
      <div style="display:flex;gap:6px">
        <button class="btn" id="era-art-toggle" style="padding:6px 12px;font-size:.74rem" onclick="controlEraSprites('toggle')">Enable</button>
        <button class="btn" style="padding:6px 12px;font-size:.74rem" title="Queue generation for every building × era, paced by the sprite hourly budget. Safe to click." onclick="controlEraSprites('preseed')">▶ Generate all era art</button>
      </div>
    </div>`;

  // systems grouped
  const groups = {};
  (d.systems || []).forEach(s => (groups[s.group] = groups[s.group] || []).push(s));
  const sysHtml = Object.entries(groups).map(([g, arr]) => `
    <div style="font-size:.66rem;color:#7a86a0;text-transform:uppercase;letter-spacing:.06em;margin:10px 0 4px">${esc(g)}</div>
    ${arr.map(s => {
      const paused = m && s.desired && !s.effective;   // shouldn't happen, but safe
      const gated = s.desired && !m;
      return `<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:8px 10px;margin-bottom:5px;background:#0e1626;border:1px solid #26324a;border-radius:8px;cursor:pointer" onclick="controlSystem('${s.id}',${s.desired ? 'false' : 'true'})">
        <div style="min-width:0">
          <div style="font-size:.82rem;color:#e8eefc">${esc(s.label)} ${_SYS_HELP[s.id] ? hlp(_SYS_HELP[s.id]) : ''} ${gated ? '<span style="font-size:.64rem;color:#f59e0b">· paused by master</span>' : ''}</div>
          <div style="font-size:.68rem;color:#7a86a0">${esc(s.desc || '')}</div>
        </div>
        ${_pill(s.desired, false)}
      </div>`;
    }).join('')}`).join('');

  // capabilities moved to their own full-catalog console tab (world-caps.js) —
  // the old 10-button stub here was the "light" surface; every one of those
  // triggers (plus the rest of the platform's toolkit) lives in the catalog,
  // individually gated. controlTrigger()/the /api/world/control/trigger
  // endpoint stay for compatibility.
  const capHtml = `
    <div style="font-weight:700;color:#e8eefc;margin:16px 0 6px">⚡ Capabilities <span style="font-size:.66rem;color:#7a86a0;font-weight:400">— trigger an action, get a product back</span></div>
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 12px;background:#0e1626;border:1px solid #26324a;border-radius:8px">
      <div style="font-size:.72rem;color:#8a97ad">The full gated catalog — image/video/Studio/music/3D/social/listings/research and more, each returning a product — now lives in its own tab.</div>
      <button class="btn" style="padding:5px 12px;font-size:.76rem;background:#2a1f4a;border-color:#6d5aff;color:#c4b5fd;white-space:nowrap" onclick="worldConsole('caps')">⚡ Open the catalog</button>
    </div>`;

  // selling config
  const sl = d.sell || {};
  const sellHtml = `
    <div style="font-weight:700;color:#e8eefc;margin:16px 0 6px">💰 Selling <span style="font-size:.66rem;color:#7a86a0;font-weight:400">— pricing for paid listings (always reviewed before going live)</span></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:10px;background:#0e1626;border:1px solid #26324a;border-radius:8px">
      <span style="font-size:.72rem;color:#8a97ad">Price ${hlp('The list price agents put on new paid Etsy/Printify listings. Every listing is still reviewed as a prayer before it goes live.')} $<input id="sell-price" type="number" step="0.01" value="${((sl.price_cents || 2499) / 100).toFixed(2)}" style="width:74px;padding:4px 7px;background:#0b1120;border:1px solid #26324a;border-radius:6px;color:#e8eefc"></span>
      <span style="font-size:.72rem;color:#8a97ad">Sold as ${hlp('What the design is sold as (e.g. Poster, T-Shirt). Sets the product type on new Etsy/Printify listings.')} <input id="sell-ptype" value="${esc(sl.product_type || 'Poster')}" style="width:100px;padding:4px 7px;background:#0b1120;border:1px solid #26324a;border-radius:6px;color:#e8eefc"></span>
      <button class="btn" style="padding:4px 10px;font-size:.72rem" onclick="controlSellSave()">💾 Save</button>
      <span style="font-size:.68rem;color:#54607a;width:100%">
        Etsy: <b style="color:${sl.etsy_ready ? '#6ee7a8' : '#f59e0b'}">${sl.etsy_ready ? 'connected ✓' : 'not connected'}</b> ·
        Printify: <b style="color:${sl.printify_ready ? '#6ee7a8' : '#f59e0b'}">${sl.printify_ready ? 'connected ✓' : 'add API key'}</b> ·
        each Etsy listing costs $0.20 from the treasury.
      </span>
    </div>`;

  _worldModal('🎛️ Company Control', master + runmode + eraArt + sysHtml + capHtml + sellHtml);
  _loadEraArtStatus();
}

async function _loadEraArtStatus() {
  const el = document.getElementById('era-art-status'); if (!el) return;
  try {
    const s = await api('/api/world/era-sprites');
    const b = s.budget || {}, ps = s.preseed || {};
    el.innerHTML = `<b style="color:${s.enabled ? '#6ee7a8' : '#f59e0b'}">${s.enabled ? 'ON' : 'off'}</b> · `
      + `${s.have_count || 0}/${s.total || 0} sprites generated · budget ${b.remaining ?? '?'}/${b.max_hour ?? '?'} left this hour`
      + (ps.running ? ` · <span style="color:#8ecaff">generating ${ps.made || 0}/${ps.total || 0}…</span>` : '');
    const tb = document.getElementById('era-art-toggle');
    if (tb) tb.textContent = s.enabled ? 'Disable' : 'Enable';
    el.dataset.enabled = s.enabled ? '1' : '0';
  } catch (e) { el.textContent = 'status unavailable'; }
}

async function controlEraSprites(action) {
  try {
    if (action === 'toggle') {
      const el = document.getElementById('era-art-status');
      const on = el && el.dataset.enabled === '1';
      await api('/api/world/settings', { method: 'POST', body: JSON.stringify({ settings: { world_era_sprites_enabled: on ? '0' : '1' } }) });
      toast?.(on ? 'Era art disabled' : '🏛️ Era art enabled');
    } else if (action === 'preseed') {
      const r = await api('/api/world/era-sprites/preseed', { method: 'POST', body: JSON.stringify({}) });
      toast?.(r && r.reason === 'disabled' ? 'Enable era art first' : '🏛️ Generating era art — paced by the hourly budget');
    }
  } catch (e) { toast?.(e?.message || 'Failed'); }
  _loadEraArtStatus();
}
window.controlEraSprites = controlEraSprites;

async function controlSellSave() {
  const price = parseFloat(document.getElementById('sell-price')?.value || '24.99');
  const product_type = document.getElementById('sell-ptype')?.value || 'Poster';
  try { const d = await api('/api/world/control/sell-config', { method: 'POST', body: JSON.stringify({ price_dollars: price, product_type }) }); toast?.('Saved'); _renderControl(d); }
  catch (e) { toast?.('Failed'); }
}

async function controlMaster(on) {
  try { const d = await api('/api/world/control/master', { method: 'POST', body: JSON.stringify({ on }) }); toast?.(on ? '🟢 Company running' : '⏸️ Company paused'); _renderControl(d); }
  catch (e) { toast?.('Failed'); }
}
async function controlSystem(id, on) {
  try { const d = await api('/api/world/control/system', { method: 'POST', body: JSON.stringify({ id, on }) }); _renderControl(d); }
  catch (e) { toast?.(e?.message || 'Failed'); }
}
async function controlTrigger(id) {
  let args = {};
  if (id === 'research') {
    const topic = prompt('What should the scholars research?');
    if (topic == null) return;
    args = { topic };
  }
  try {
    const r = await api('/api/world/control/trigger', { method: 'POST', body: JSON.stringify({ id, args }) });
    toast?.(r.ok ? `⚡ ${r.result || 'triggered'}` : (r.error || 'Failed'));
  } catch (e) { toast?.('Failed'); }
}

window.worldControl = worldControl;
async function controlRunMode(mode) {
  try {
    await api('/api/world/control/runmode', { method: 'POST', body: JSON.stringify({ mode }) });
    toast?.(mode === 'test' ? '🧪 Test — dry run, evolving fast (money/code still gated)'
          : mode === 'fast' ? '⚡ Fast — ~5x sim speed'
          : '🐢 Normal pace');
    const d = await api('/api/world/control/panel'); _renderControl(d);
  } catch (e) { toast?.(e.message); }
}
window.controlRunMode = controlRunMode;
window.controlMaster = controlMaster;
window.controlSystem = controlSystem;
window.controlTrigger = controlTrigger;
window.controlSellSave = controlSellSave;

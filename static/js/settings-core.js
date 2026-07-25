/* Settings tab renderer. Split from app-main.js; the admin panel lives in admin.js. */
/* ══ SETTINGS ══ */
/* Helper: load trend sources into the #trend-body div. The trend-sources UI
   moved from Settings into Etsy/Printify → Dashboard → Store Configuration
   (tab-etsy-printify.js renders the #trend-body placeholder there); this
   loader is target-agnostic — it fills whichever #trend-body is on screen. */
async function _loadTrendIntoSettings() {
  let cfg = {}, scanStatus = {};
  try { cfg = await api('/api/trends/config'); } catch {}
  try { scanStatus = await api('/api/trends/status'); } catch {}
  const body = document.getElementById('trend-body');
  if (!body) return;

  const googleOn  = cfg.google_enabled !== false;
  const redditOn  = cfg.reddit_enabled !== false;
  const rssOn     = cfg.rss_enabled    !== false;
  const rssFeeds  = cfg.rss_urls ? cfg.rss_urls.split('\n').filter(Boolean) : [];
  const redditSubs = cfg.reddit_subs || '';
  const scanMsg = scanStatus.status === 'running'
    ? '&#9881; Scanning now…'
    : esc(scanStatus.message || (cfg.last_run ? `Last scan: ${new Date(cfg.last_run).toLocaleString()} — ${cfg.last_count||0} proposals added` : 'No scans run yet'));

  const laneSet = new Set((cfg.lanes_enabled || '').split(',').filter(Boolean));
  const lanes = cfg.lanes || [];

  let th = `
    <div style="font-size:.78rem;color:var(--muted);margin-bottom:10px;">${scanMsg}</div>
    <div style="margin-bottom:12px;"><button class="btn-sm primary" id="scan-now-btn" title="Scan every enabled source now and turn trending topics into product proposals in the Backlog. Runs on this server; takes a minute or two.">&#128269; Scan Now</button></div>`;
  if (lanes.length) {
    th += `<div class="field" style="margin-bottom:12px;">
      <label>Proposal lanes ${hlp('Which themed generators run on a scan. Each lane has its own editable prompt (Settings → Prompts → Storefront) and its own model pick; every proposal is tagged with its lane. Humor is one lane among many — not the default frame. Saved instantly.')}</label>
      <div style="display:flex;flex-wrap:wrap;gap:6px;">`;
    for (const l of lanes)
      th += `<button class="btn-sm ${laneSet.has(l.id) ? 'primary' : ''}" data-lane="${esc(l.id)}" title="Toggle the ${esc(l.label)} lane">${esc(l.label)}</button>`;
    th += `</div></div>`;
  }
  th += `
    <div class="trend-grid">
      <div class="trend-card">
        <div class="trend-card-header">
          <div class="trend-card-title">&#127758; Google Trends</div>
          <div class="toggle ${googleOn?'on':''}" id="toggle-google" data-source="google" title="Include Google Trends when scanning. On = a scan pulls trending searches for the region below into the proposal Backlog. Saved instantly."></div>
        </div>
        <div class="field">
          <label>Region ${hlp('Which country Google Trends pulls trending searches from during a scan. Affects the Google source only. Saved the moment you change it.')}</label>
          <select id="google-region">
            ${['US','GB','CA','AU','DE','FR','JP','BR','IN','MX'].map(r => `<option value="${r}"${cfg.google_region===r?' selected':''}>${r}</option>`).join('')}
          </select>
        </div>
      </div>
      <div class="trend-card">
        <div class="trend-card-header">
          <div class="trend-card-title">&#128992; Reddit</div>
          <div class="toggle ${redditOn?'on':''}" id="toggle-reddit" data-source="reddit" title="Include Reddit when scanning. On = a scan reads hot posts from the subreddits below into the proposal Backlog. Saved instantly."></div>
        </div>
        <div class="field">
          <label>Subreddits (comma-separated) ${hlp('Which subreddits a scan reads hot posts from when Reddit is on. Comma-separated (e.g. gifts, cats, woodworking). Click Save Subreddits to apply.')}</label>
          <textarea id="reddit-subs" rows="4" style="font-size:.73rem;resize:vertical;">${esc(redditSubs)}</textarea>
        </div>
        <button class="btn-sm" id="save-reddit-btn" style="margin-top:6px;">&#128190; Save Subreddits</button>
      </div>
      <div class="trend-card">
        <div class="trend-card-header">
          <div class="trend-card-title">&#128225; RSS Feeds</div>
          <div class="toggle ${rssOn?'on':''}" id="toggle-rss" data-source="rss" title="Include your custom RSS feeds when scanning. On = a scan reads new items from the feed URLs below into the proposal Backlog. Saved instantly."></div>
        </div>`;
  if (rssFeeds.length) {
    th += `<ul class="rss-list">`;
    for (const feed of rssFeeds)
      th += `<li class="rss-item"><span class="rss-item-url" title="${esc(feed)}">${esc(feed)}</span><button class="btn-sm" style="padding:2px 7px;font-size:.68rem;" data-action="remove-rss" data-url="${esc(feed)}">&#10005;</button></li>`;
    th += `</ul>`;
  } else {
    th += `<div style="font-size:.75rem;color:var(--muted);margin-bottom:8px;">No custom feeds added.</div>`;
  }
  th += `<div class="add-rss-row">
    <input type="text" id="rss-add-input" placeholder="https://feed.url/rss.xml">
    <button class="btn-sm primary" id="rss-add-btn">Add</button>
  </div></div>`;

  // categorized feed groups (world/local/USA/game/tech news)
  for (const [cat, f] of Object.entries(cfg.feeds || {})) {
    th += `
      <div class="trend-card">
        <div class="trend-card-header">
          <div class="trend-card-title">${f.icon || '&#128240;'} ${esc(f.label || cat)}</div>
          <div class="toggle ${f.enabled ? 'on' : ''}" data-feedcat="${esc(cat)}" title="Include the ${esc(f.label || cat)} feeds when scanning. Items are tagged '${esc(cat)}' and routed to the matching proposal lanes. Saved instantly."></div>
        </div>
        <div class="field">
          <label>Feed URLs (one per line) ${hlp('RSS/Atom feed URLs for this category. Click Save to apply.')}</label>
          <textarea data-feedurls="${esc(cat)}" rows="3" style="font-size:.7rem;resize:vertical;">${esc(f.urls || '')}</textarea>
        </div>
        <button class="btn-sm" data-savefeed="${esc(cat)}" style="margin-top:6px;">&#128190; Save</button>
      </div>`;
  }
  th += `</div>`;

  body.innerHTML = th;

  // lane chips → PATCH lanes_enabled (recomputed from the chip states)
  body.querySelectorAll('[data-lane]').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.classList.toggle('primary');
      const on = [...body.querySelectorAll('[data-lane].primary')].map(b => b.dataset.lane);
      try {
        await api('/api/trends/config', { method: 'PATCH', body: JSON.stringify({ lanes_enabled: on.join(',') }) });
        toast(`Lanes: ${on.join(', ') || 'none'}`);
      } catch (e) { toast('Error: ' + e.message, 'error'); btn.classList.toggle('primary'); }
    });
  });

  // feed-group toggles + save buttons
  body.querySelectorAll('.toggle[data-feedcat]').forEach(el => {
    el.addEventListener('click', async () => {
      el.classList.toggle('on');
      const on = el.classList.contains('on');
      const patch = {}; patch['feed_' + el.dataset.feedcat + '_enabled'] = on;
      try {
        await api('/api/trends/config', { method: 'PATCH', body: JSON.stringify(patch) });
        toast(`${el.dataset.feedcat} feeds ${on ? 'enabled' : 'disabled'}`);
      } catch (e) { toast('Error: ' + e.message, 'error'); el.classList.toggle('on'); }
    });
  });
  body.querySelectorAll('[data-savefeed]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const cat = btn.dataset.savefeed;
      const ta = body.querySelector(`textarea[data-feedurls="${cat}"]`);
      const patch = {}; patch['feed_' + cat + '_urls'] = (ta ? ta.value : '').trim();
      try {
        await api('/api/trends/config', { method: 'PATCH', body: JSON.stringify(patch) });
        toast('Feeds saved ✓');
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    });
  });

  document.getElementById('scan-now-btn')?.addEventListener('click', async () => {
    const btn = document.getElementById('scan-now-btn');
    btn.disabled = true; btn.textContent = '\u231B Scanning…';
    try {
      const r = await api('/api/trends/scan', { method: 'POST' });
      if (r.ok === false) { toast(r.message || 'Already scanning', 'warn'); return; }
      toast('Trend scan started!');
      for (let i = 0; i < 120; i++) {
        await new Promise(res => setTimeout(res, 3000));
        const st = await api('/api/trends/status');
        if (st.status !== 'running') { toast(st.message || 'Scan complete'); _loadTrendIntoSettings(); return; }
      }
    } catch(e) { toast('Scan error: ' + e.message, 'error'); }
    finally { btn.disabled = false; btn.textContent = '\u{1F50D} Scan Now'; }
  });

  body.querySelectorAll('.toggle[data-source]').forEach(el => {
    el.addEventListener('click', async () => {
      el.classList.toggle('on');
      const on = el.classList.contains('on');
      const patch = {}; patch[el.dataset.source + '_enabled'] = on;
      try {
        await api('/api/trends/config', { method: 'PATCH', body: JSON.stringify(patch) });
        toast(`${el.dataset.source} trends ${on ? 'enabled' : 'disabled'}`);
      } catch(e) { toast('Error: ' + e.message, 'error'); el.classList.toggle('on'); }
    });
  });

  document.getElementById('google-region')?.addEventListener('change', async (e) => {
    try { await api('/api/trends/config', { method: 'PATCH', body: JSON.stringify({ google_region: e.target.value }) }); toast('Region saved'); }
    catch(e2) { toast('Error: ' + e2.message, 'error'); }
  });

  document.getElementById('save-reddit-btn')?.addEventListener('click', async () => {
    const subs = document.getElementById('reddit-subs').value.trim();
    try { await api('/api/trends/config', { method: 'PATCH', body: JSON.stringify({ reddit_subs: subs }) }); toast('Subreddits saved \u2713'); }
    catch(e) { toast('Error: ' + e.message, 'error'); }
  });

  document.getElementById('rss-add-btn')?.addEventListener('click', async () => {
    const input = document.getElementById('rss-add-input');
    const url = input.value.trim();
    if (!url) return;
    try {
      const cfg2 = await api('/api/trends/config');
      const feeds = [...(cfg2.rss_urls ? cfg2.rss_urls.split('\n').filter(Boolean) : []), url];
      await api('/api/trends/config', { method: 'PATCH', body: JSON.stringify({ rss_urls: feeds.join('\n') }) });
      toast('Feed added'); input.value = ''; _loadTrendIntoSettings();
    } catch(e) { toast('Error: ' + e.message, 'error'); }
  });

  // Bind to the freshly rendered buttons — NOT a delegated listener on #trend-body,
  // which persists across re-renders and would stack a duplicate handler per reload.
  body.querySelectorAll('[data-action="remove-rss"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      try {
        const cfg2 = await api('/api/trends/config');
        const feeds = (cfg2.rss_urls ? cfg2.rss_urls.split('\n').filter(Boolean) : []).filter(f => f !== btn.dataset.url);
        await api('/api/trends/config', { method: 'PATCH', body: JSON.stringify({ rss_urls: feeds.join('\n') }) });
        toast('Feed removed'); _loadTrendIntoSettings();
      } catch(e) { toast('Error: ' + e.message, 'error'); }
    });
  });
}

// Settings sub-tabs: every field stays in the DOM (so all save/wire logic is
// untouched) — we only toggle which pane is visible.
const _SETTINGS_PANES = ['system', 'backups', 'models', 'integrations', 'prompts', 'systems', 'plugins', 'interface'];
function settingsSub(k) {
  _SETTINGS_PANES.forEach(name => {
    const pane = document.getElementById('pane-' + name);
    if (pane) pane.style.display = (name === k) ? '' : 'none';
  });
  document.querySelectorAll('#settings-subtabs .subtab').forEach((el, i) => {
    el.classList.toggle('active', _SETTINGS_PANES[i] === k);
  });
  if (k === 'prompts') loadPromptsEditor();
  if (k === 'models') loadModelRegistry();
  if (k === 'backups' && typeof loadBackups === 'function') loadBackups();
  if (k === 'systems' && typeof renderSystemsPane === 'function') renderSystemsPane();
  if (k === 'plugins' && typeof renderPluginsPane === 'function') renderPluginsPane();
  if (k === 'interface' && typeof renderInterfacePane === 'function') renderInterfacePane();
  if (k === 'integrations') loadIntegrationsBoard();
}

/* ══ INTEGRATIONS STATUS BOARD (Settings → Integrations) ══
   One dashboard for every external service/login: GET /api/integrations/status
   returns which settings keys are populated (never the values) + a couple of
   live-ish reachability checks. Each row gets a colored pill, a short note, an
   "Open setup ↗" link to the service's own dashboard, and — for services with
   an existing in-app config field somewhere — a "Configure" button that jumps
   there (not always the same tab the row lives in a category-wise sense; it's
   wherever that service is actually editable today). */
const _INTEGRATIONS_STATUS_PILL = {
  active:      { color: 'var(--green)', bg: 'rgba(34,197,94,.14)',  label: '✅ Active' },
  needs_login: { color: 'var(--warn)',  bg: 'rgba(245,158,11,.14)', label: '⚠️ Needs login' },
  not_setup:   { color: 'var(--red)',   bg: 'rgba(239,68,68,.14)',  label: '❌ Not set up' },
};
const _INTEGRATIONS_CATEGORY_ORDER = ['Commerce', 'Crypto & Markets', 'Social', 'Infra & AI'];

// Where each in-app-configurable service's field actually lives, so "Configure"
// can jump straight there instead of just re-showing this same board.
const _INTEGRATIONS_GOTO = {
  // Etsy/Printify config moved to the Etsy/Printify tab's Dashboard subtab —
  // epGotoConfig (tab-etsy-printify.js) opens the Store Configuration section
  // there and handles the anchor/highlight itself.
  etsy:       { run: () => window.epGotoConfig?.('s-etsy-key') },
  printify:   { run: () => window.epGotoConfig?.('s-printify-key') },
  cults3d:    { run: () => document.querySelector('[data-view=cults3d]')?.click() },
  wp:         { run: () => document.querySelector('[data-view=portal]')?.click() },
  kraken:     { run: () => { document.querySelector('[data-view=finance]')?.click();
                              setTimeout(() => { if (typeof financeSub === 'function') financeSub('crypto');
                                setTimeout(() => { if (typeof cryptoSub === 'function') cryptoSub('trading'); }, 80); }, 80); } },
  robinhood:  { run: () => { document.querySelector('[data-view=finance]')?.click();
                              setTimeout(() => { if (typeof financeSub === 'function') financeSub('crypto');
                                setTimeout(() => { if (typeof cryptoSub === 'function') cryptoSub('stocks'); }, 80); }, 80); } },
  wallets:    { run: () => { document.querySelector('[data-view=finance]')?.click();
                              setTimeout(() => { if (typeof financeSub === 'function') financeSub('wallets'); }, 80); } },
  pearl:      { run: () => { document.querySelector('[data-view=finance]')?.click();
                              setTimeout(() => { if (typeof financeSub === 'function') financeSub('crypto');
                                setTimeout(() => { if (typeof cryptoSub === 'function') cryptoSub('pearl'); }, 80); }, 80); } },
  huggingface:{ run: () => settingsSub('system'), anchor: 'admin-panel-slot' },
  lmstudio:   { run: () => settingsSub('system'), anchor: 'admin-panel-slot' },
  github:     { run: () => settingsSub('system'), anchor: 'github-slot' },
  node_gpu:   { run: () => settingsSub('system'), anchor: 'admin-panel-slot' },
};

function _integrationsGoto(keyId) {
  const g = _INTEGRATIONS_GOTO[keyId];
  if (!g) return;
  g.run();
  if (g.anchor) {
    setTimeout(() => {
      const el = document.getElementById(g.anchor);
      if (!el) return;
      // the target may sit inside a collapsed <details> block — open it first
      const det = el.closest('details');
      if (det) det.open = true;
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      const prev = el.style.outline;
      el.style.outline = '2px solid var(--accent)';
      setTimeout(() => { el.style.outline = prev; }, 1500);
    }, 250);
  }
}

async function loadIntegrationsBoard() {
  const slot = document.getElementById('integrations-board-slot');
  if (!slot) return;
  let data;
  try { data = await api('/api/integrations/status'); }
  catch (e) { slot.innerHTML = `<div style="color:var(--red);font-size:.8rem;">Couldn't load integrations status: ${esc(e.message)}</div>`; return; }

  const items = data.integrations || [];
  const byCat = {};
  items.forEach(it => { (byCat[it.category] = byCat[it.category] || []).push(it); });
  const cats = [..._INTEGRATIONS_CATEGORY_ORDER, ...Object.keys(byCat).filter(c => !_INTEGRATIONS_CATEGORY_ORDER.includes(c))]
    .filter(c => byCat[c] && byCat[c].length);

  const s = data.summary || {};
  let h = `<div style="font-size:.78rem;color:var(--muted);margin-bottom:12px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
    <span><span style="color:var(--green);">&#9679;</span> ${s.active||0} active</span>
    <span><span style="color:var(--warn);">&#9679;</span> ${s.needs_login||0} needs login</span>
    <span><span style="color:var(--red);">&#9679;</span> ${s.not_setup||0} not set up</span>
    <button class="btn-sm" id="ib-refresh" style="padding:2px 8px;font-size:.7rem;">&#8635; Refresh</button>
  </div>`;

  for (const cat of cats) {
    h += `<div style="margin-bottom:14px;">
      <div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:6px;">${esc(cat)}</div>
      <div style="display:flex;flex-direction:column;gap:6px;">`;
    for (const it of byCat[cat]) {
      const p = _INTEGRATIONS_STATUS_PILL[it.status] || _INTEGRATIONS_STATUS_PILL.not_setup;
      const isCf = it.key_id === 'cloudflare';
      h += `<div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden;">
        <div style="display:flex;align-items:center;gap:10px;padding:8px 10px;flex-wrap:wrap;">
          <span style="font-weight:600;font-size:.82rem;min-width:170px;">${esc(it.name)}</span>
          <span style="font-size:.68rem;font-weight:700;padding:2px 8px;border-radius:999px;color:${p.color};background:${p.bg};white-space:nowrap;">${p.label}</span>
          <span style="font-size:.73rem;color:var(--muted);flex:1;min-width:180px;">${esc(it.detail||'')}</span>
          <span style="display:flex;gap:6px;flex-shrink:0;">
            ${isCf ? `<button class="btn-sm" id="cf-purge-btn" style="padding:2px 8px;font-size:.68rem;" title="Purge the entire Cloudflare edge cache for the configured zone">&#128465;&#65039; Purge cache</button>
            <button class="btn-sm" id="cf-config-toggle" style="padding:2px 8px;font-size:.68rem;">&#9881;&#65039; Configure</button>` : ''}
            ${(it.in_app && !isCf) ? `<button class="btn-sm" data-goto="${esc(it.key_id)}" style="padding:2px 8px;font-size:.68rem;">Configure</button>` : ''}
            ${it.setup_url ? `<a class="btn-sm" href="${esc(it.setup_url)}" target="_blank" rel="noopener" style="padding:2px 8px;font-size:.68rem;text-decoration:none;">Open setup &#8599;</a>` : ''}
          </span>
        </div>
        ${isCf ? `<div id="cf-config-panel" style="display:none;padding:10px;border-top:1px solid var(--border);background:var(--bg);">
          <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;">
            <div class="field" style="margin:0;min-width:220px;">
              <label style="font-size:.7rem;">API Token ${hlp('Cloudflare API token with Zone → Cache Purge permission for the target zone (dash.cloudflare.com/profile/api-tokens). Stored encrypted; never shown again after saving — leave blank to keep the current one.')}</label>
              <input type="password" id="cf-api-token-input" placeholder="Leave blank to keep current" style="font-size:.75rem;" autocomplete="off">
            </div>
            <div class="field" style="margin:0;min-width:220px;">
              <label style="font-size:.7rem;">Account ID ${hlp('Cloudflare account ID (dashboard right sidebar). Not currently required for a purge, saved for future account-scoped calls — leave blank to keep the current one.')}</label>
              <input type="password" id="cf-account-id-input" placeholder="Leave blank to keep current" style="font-size:.75rem;" autocomplete="off">
            </div>
            <div class="field" style="margin:0;min-width:220px;">
              <label style="font-size:.7rem;">Zone ID ${hlp('Cloudflare zone ID for the site (zone Overview page, right sidebar). Optional — if left empty it is looked up by zone name on the first purge and cached here. Not a secret.')}</label>
              <input type="text" id="cf-zone-id-input" placeholder="Auto-resolved on first purge" style="font-size:.75rem;" autocomplete="off">
            </div>
            <button class="btn-sm primary" id="cf-config-save" style="font-size:.7rem;">&#128190; Save</button>
          </div>
          <div id="cf-status-line" style="font-size:.72rem;color:var(--muted);margin-top:6px;"></div>
          <div id="cf-config-msg" style="font-size:.72rem;color:var(--muted);margin-top:6px;"></div>
        </div>` : ''}
      </div>`;
    }
    h += `</div></div>`;
  }

  slot.innerHTML = h || `<div style="color:var(--muted);font-size:.8rem;">No integrations found.</div>`;
  document.getElementById('ib-refresh')?.addEventListener('click', loadIntegrationsBoard);
  slot.querySelectorAll('[data-goto]').forEach(btn => btn.addEventListener('click', () => _integrationsGoto(btn.dataset.goto)));

  // ── Cloudflare row: purge-cache action + inline credential config ──
  _loadCfStatus();

  document.getElementById('cf-purge-btn')?.addEventListener('click', async () => {
    const btn = document.getElementById('cf-purge-btn');
    const orig = btn.innerHTML;
    btn.disabled = true; btn.innerHTML = '⌛ Purging…';
    try {
      const r = await api('/api/cloudflare/purge', { method: 'POST' });
      if (r && r.ok) toast('Cloudflare cache purged ✓');
      else toast('Cloudflare purge failed: ' + (r && r.errors ? JSON.stringify(r.errors) : 'unknown error'), 'error');
    } catch (e) {
      toast('Purge failed: ' + e.message, 'error');
    } finally {
      btn.disabled = false; btn.innerHTML = orig;
    }
  });

  document.getElementById('cf-config-toggle')?.addEventListener('click', () => {
    const panel = document.getElementById('cf-config-panel');
    if (panel) panel.style.display = (panel.style.display === 'none') ? 'block' : 'none';
  });

  document.getElementById('cf-config-save')?.addEventListener('click', async () => {
    const tokenEl = document.getElementById('cf-api-token-input');
    const acctEl = document.getElementById('cf-account-id-input');
    const zoneEl = document.getElementById('cf-zone-id-input');
    const msg = document.getElementById('cf-config-msg');
    const patch = {};
    if (tokenEl && tokenEl.value.trim()) patch.cf_api_token = tokenEl.value.trim();
    if (acctEl && acctEl.value.trim()) patch.cf_account_id = acctEl.value.trim();
    // Zone id is non-secret and prefilled — save when it CHANGED (clearing it to
    // empty is a valid change: forces a fresh lookup by zone name on next purge).
    if (zoneEl && zoneEl.value.trim() !== (zoneEl.dataset.loaded || '')) patch.cf_zone_id = zoneEl.value.trim();
    if (!Object.keys(patch).length) { if (msg) { msg.style.color = 'var(--warn)'; msg.textContent = 'Nothing to save — fill in a field first.'; } return; }
    try {
      await api('/api/settings', { method: 'PATCH', body: JSON.stringify(patch) });
      if (msg) { msg.style.color = 'var(--green)'; msg.textContent = '✓ Saved.'; }
      if (tokenEl) tokenEl.value = '';
      if (acctEl) acctEl.value = '';
      loadIntegrationsBoard();
    } catch (e) {
      if (msg) { msg.style.color = 'var(--red)'; msg.textContent = 'Error: ' + e.message; }
    }
  });
}

// Cloudflare configured/not-configured line + zone-id prefill for the Integrations
// panel. Reads GET /api/cloudflare/status — booleans + the (non-secret) zone id
// only; the token itself is never returned by any endpoint.
async function _loadCfStatus() {
  const line = document.getElementById('cf-status-line');
  const zoneEl = document.getElementById('cf-zone-id-input');
  if (!line) return;   // no Cloudflare row rendered
  try {
    const s = await api('/api/cloudflare/status');
    if (zoneEl) { zoneEl.value = s.zone_id || ''; zoneEl.dataset.loaded = s.zone_id || ''; }
    line.innerHTML = s.configured
      ? `<span style="color:var(--green);">&#9679;</span> Configured — purges target zone <b>${esc(s.zone)}</b>${s.zone_id_known ? ' (zone id set)' : ' (zone id will be looked up on first purge)'}`
      : `<span style="color:var(--red);">&#9679;</span> Not configured — save an API token below to enable cache purge.`;
  } catch (e) {
    line.textContent = "Couldn't load Cloudflare status: " + e.message;
  }
}

async function renderSettings() {
  let settings = {}, settingsLoaded = true;
  try { settings = await api('/api/settings'); } catch { settingsLoaded = false; }
  // Etsy/Printify configuration (keys, connection, store identity, trend sources,
  // proposal pipeline) lives in Etsy/Printify → Dashboard → Store Configuration.

  let h = `
    <div class="view-header"><div class="view-title">&#9881;&#65039; Settings</div><div class="view-sub">Configure the system, models, integrations, prompts, and interface.</div></div>
    <div class="subtab-bar" id="settings-subtabs" style="margin-bottom:16px;">
      <div class="subtab active" onclick="settingsSub('system')">&#128421;&#65039; System</div>
      <div class="subtab" onclick="settingsSub('backups')">&#128190; Backups</div>
      <div class="subtab" onclick="settingsSub('models')">&#129504; Models</div>
      <div class="subtab" onclick="settingsSub('integrations')">&#128279; Integrations</div>
      <div class="subtab" onclick="settingsSub('prompts')">&#128221; Prompts</div>
      <div class="subtab" onclick="settingsSub('systems')">&#129513; Systems</div>
      <div class="subtab" onclick="settingsSub('plugins')">&#128268; Plugins</div>
      <div class="subtab" onclick="settingsSub('interface')">&#128065;&#65039; Interface</div>
    </div>

    <div class="settings-tabpane" id="pane-system">
      <div class="settings-grid">
      <!-- admin.js (mountAdminPanel) renders one collapsible <details> block per
           concern (Server/Host open by default; Compute Nodes, Content, GPU Node
           collapsed) into this slot — it's a stacking container, not a group.
           Backups & Restore lives in its own subtab (pane-backups); Store Logs
           lives in the Systems subtab (settings-systems.js). -->
      <div id="admin-panel-slot" style="display:flex;flex-direction:column;gap:20px;">
        <div class="settings-group">
          <div class="settings-group-title">&#128421;&#65039; System</div>
          <div style="font-size:.78rem;color:var(--muted);">Loading&hellip;</div>
        </div>
      </div>
      <details class="settings-group">
        <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#128260; Updates</summary>
        <div id="updates-slot" style="margin-top:10px;">
          <div style="font-size:.78rem;color:var(--muted);">Loading&hellip;</div>
        </div>
      </details>
      <details class="settings-group">
        <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#128025; GitHub</summary>
        <div id="github-slot" style="margin-top:10px;">
          <div style="font-size:.78rem;color:var(--muted);">Loading&hellip;</div>
        </div>
      </details>
      <details class="settings-group" id="password-group">
        <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#128274; Login Password</summary>
        <div class="field" style="margin-top:10px;"><label>Current Password ${hlp('This changes the LOGIN password for this dashboard (the one you type on the login screen). It is NOT any Etsy/Printify/Google password. You must enter the current one to set a new one; you’ll be signed out after changing it.')}</label><input type="password" id="s-pw-cur" placeholder="Current password"></div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:0 18px;">
          <div class="field"><label>New Password</label><input type="password" id="s-pw-new" placeholder="New password (min 4 chars)"></div>
          <div class="field"><label>Confirm New Password</label><input type="password" id="s-pw-confirm" placeholder="Confirm new password"></div>
        </div>
        <div style="display:flex;gap:8px;margin-top:10px;">
          <button class="btn-sm primary" id="s-pw-save">&#128274; Change Password</button>
          <a href="/store/logout" class="btn-sm danger" style="text-decoration:none;display:inline-flex;align-items:center;">&#128275; Sign Out</a>
        </div>
        <div id="s-pw-msg" style="margin-top:8px;font-size:.78rem;"></div>
      </details>
      </div>
    </div>

    <div class="settings-tabpane" id="pane-backups" style="display:none;">
      <div class="settings-group">
        <div class="settings-group-title">&#128190; Backups &amp; Restore</div>
        <div style="font-size:.75rem;color:var(--muted);margin-bottom:10px;">Stored in the store's data folder. Restore is destructive (a safety backup is taken first).</div>
        <div style="margin-bottom:10px;"><button class="btn-sm primary" onclick="createBackup()" id="bk-create">&#10133; Create Backup</button></div>
        <div id="backups-list" style="font-size:.78rem;color:var(--muted);">Loading&hellip;</div>
      </div>
    </div>

    <div class="settings-tabpane" id="pane-models" style="display:none;">
      <div class="settings-group">
        <div class="settings-group-title">&#129504; Models — one place to pick what runs where</div>
        <div style="font-size:.78rem;color:var(--muted);margin-bottom:12px;line-height:1.5">
          Every feature that uses an AI model, with the model it's set to. LLM/text and vision jobs
          all funnel through the <b>unified GPU queue</b> (the single authority that loads &amp; unloads
          models on the node) — including OpenClaw's local agents — so the model you pick here is what
          the queue loads for that job. Changes apply on the next job (no restart).
        </div>
        <div id="model-registry-slot" style="font-size:.8rem;color:var(--muted)">Loading&hellip;</div>
      </div>
    </div>

    <div class="settings-tabpane" id="pane-integrations" style="display:none;">
      <div class="settings-grid">
      <div class="settings-group" style="grid-column:1/-1;" id="integrations-board-wrap">
        <div class="settings-group-title">&#128225; Integrations Status Board</div>
        <div style="font-size:.78rem;color:var(--muted);margin-bottom:10px;">
          Every external service/login the Store knows about, at a glance &mdash; never shows the actual key/password, only whether it's set and whether it looks reachable.
        </div>
        <div id="integrations-board-slot"><div style="color:var(--muted);font-size:.8rem;">Loading&hellip;</div></div>
      </div>
      <div class="settings-group">
        <div class="settings-group-title">&#9749; Donations</div>
        <div class="field">
          <label>Buy Me a Coffee handle ${hlp('Your Buy Me a Coffee username (from buymeacoffee.com/&lt;handle&gt;). Public — not a secret — but a fresh clone of this app ships with it BLANK on purpose, so nobody accidentally hands out someone else’s donate link.')}</label>
          <input type="text" id="s-donate-user" value="${esc(settings.donate_bmc_user||'')}" placeholder="yourname">
        </div>
        <div class="field" style="display:flex;align-items:flex-start;gap:8px;">
          <input type="checkbox" id="s-donate-enabled" ${(settings.donate_enabled||'').toString().toLowerCase().match(/^(1|true|on|yes)$/)?'checked':''} style="margin-top:3px;">
          <label style="margin:0;" for="s-donate-enabled">Show the &#9749; Support this project card ${hlp('OFF by default. Turn on to show a donate button (login page, sidebar footer, and Finance → Missions &amp; Earn) linking to your Buy Me a Coffee page. Needs a handle set above to actually appear; off hides every placement.')}</label>
        </div>
        <button class="btn-sm primary" id="s-save-donate" style="margin-top:10px;">&#128190; Save</button>
      </div>
      </div>
    </div>

    <div class="settings-tabpane" id="pane-prompts" style="display:none;">
      <div class="settings-section-head">&#128221; Prompts</div>
      <div style="font-size:.8rem;color:var(--muted);margin-bottom:14px;max-width:760px;">
        Every LLM system prompt the app uses. Edit and Save to override; Reset restores the built-in default.
        Prompts marked <b style="color:var(--warn)">templated</b> contain <code>{placeholders}</code> &mdash; keep them intact.
      </div>
      <div id="prompts-list"><div style="color:var(--muted);font-size:.8rem;">Loading&#8230;</div></div>
    </div>

    <div class="settings-tabpane" id="pane-systems" style="display:none;">
      <div style="color:var(--muted);font-size:.8rem;">Loading&#8230;</div>
    </div>

    <div class="settings-tabpane" id="pane-plugins" style="display:none;">
      <div style="color:var(--muted);font-size:.8rem;">Loading&#8230;</div>
    </div>

    <div class="settings-tabpane" id="pane-interface" style="display:none;">
      <div style="color:var(--muted);font-size:.8rem;">Loading&#8230;</div>
    </div>`;

  document.getElementById('main-content').innerHTML = h;
  if (window.mountAdminPanel) mountAdminPanel();
  loadUpdates();
  loadGithubSettings();
  // Peers (JLY peer network) moved to the Jelly tab — tab-jellycoin.js renders
  // #peers-slot there and calls loadPeers() (settings-peers.js) itself.

  // Etsy/Printify keys, store identity, trend sources and the proposal pipeline
  // moved to Etsy/Printify -> Dashboard -> Store Configuration; tab-etsy-printify.js
  // owns those fields AND their save logic now. This saver covers what's left here.
  async function saveAll() {
    if (!settingsLoaded) {
      // The initial GET failed, so the inputs rendered EMPTY — saving now would
      // overwrite real values with ''.
      toast('Settings failed to load — refresh the page before saving.', 'error');
      return;
    }
    try {
      const patch = {
        donate_bmc_user:      document.getElementById('s-donate-user').value.trim(),
        donate_enabled:       document.getElementById('s-donate-enabled').checked ? '1' : '0',
      };
      await api('/api/settings', { method: 'PATCH', body: JSON.stringify(patch) });
      try { _settings = await api('/api/settings'); } catch {}
      if (window.refreshDonateLink) refreshDonateLink();   // sidebar \u2615 link follows the toggle live
      toast('Settings saved \u2713');
    } catch(e) { toast('Save failed: ' + e.message, 'error'); }
  }

  const pwSaveBtn = document.getElementById('s-pw-save');
  if (pwSaveBtn) pwSaveBtn.addEventListener('click', async () => {
    const cur = document.getElementById('s-pw-cur').value;
    const nw  = document.getElementById('s-pw-new').value;
    const cfm = document.getElementById('s-pw-confirm').value;
    const msg = document.getElementById('s-pw-msg');
    msg.style.color = 'var(--warn)'; msg.textContent = '';
    if (!cur || !nw)     { msg.textContent = 'Fill in current and new password.'; return; }
    if (nw !== cfm)      { msg.textContent = 'Passwords do not match.'; return; }
    if (nw.length < 4)   { msg.textContent = 'New password must be at least 4 characters.'; return; }
    try {
      await api('/api/auth/change-password', { method: 'POST', body: JSON.stringify({ current: cur, new_password: nw }) });
      msg.style.color = 'var(--green)'; msg.textContent = '\u2713 Password changed. You will be signed out.';
      document.getElementById('s-pw-cur').value = '';
      document.getElementById('s-pw-new').value = '';
      document.getElementById('s-pw-confirm').value = '';
      setTimeout(() => { window.location.href = '/store/logout'; }, 1500);
    } catch(e) { msg.textContent = 'Error: ' + e.message; }
  });

  ['s-save-donate'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('click', saveAll);
  });

  // Trend sources, proposal gate/desk controls, Printify shops and the Etsy
  // connect/disconnect flow all moved with their fields to the Etsy/Printify
  // Dashboard (tab-etsy-printify.js: _bindEpConfig + _loadEtsyStatus).
  loadIntegrationsBoard();   // async — fills the Integrations Status Board (cheap, local-only checks)
}


/* Restored from pre_unification_backup (Jul 9) — real tab implementation.
   Part of the modular frontend: one file per tab. */
/* ══ ETSY / PRINTIFY (tabbed view) ══
   Lands on the 🏠 Dashboard subtab: the store command center — connections,
   funnel at a glance, automation status, live sales stats, and the FULL store
   configuration (Printify/Etsy keys, store identity, trend sources, proposal
   pipeline) which moved here from the global Settings tab. */
async function renderEtsyPrintify(subtab) {
  if (subtab) _etsySubTab = subtab;
  // 'store-stats' was folded into the Dashboard; old deep-links land there.
  if (!_etsySubTab || _etsySubTab === 'store-stats') _etsySubTab = 'dashboard';

  // Fetch stats for subtab badges
  let s = {};
  try { s = await api('/api/stats'); } catch {}

  const tabs = [
    { id:'dashboard',  icon:'&#127968;', label:'Dashboard'                                  },
    { id:'proposals',  icon:'&#128161;', label:'Proposals',   badge: s.proposals_pending||0, bc:'warn'  },
    { id:'review',     icon:'&#128269;', label:'Review',      badge: s.review_count||0                   },
    { id:'approved',   icon:'&#9989;',   label:'Approved',    badge: s.approved_count||0,    bc:'green' },
    { id:'published',  icon:'&#128717;', label:'Published',   badge: s.published_count||0,   bc:'teal'  },
    { id:'products',   icon:'&#128176;', label:'Products'                                                },
  ];

  const tabBarHtml = `<div class="subtab-bar">
    ${tabs.map(t => {
      const active = _etsySubTab === t.id ? ' active' : '';
      const badge  = t.badge ? `<span class="subtab-badge ${t.bc||'blue'}">${t.badge}</span>` : '';
      return `<div class="subtab${active}" data-ep-tab="${t.id}">${t.icon} ${t.label}${badge}</div>`;
    }).join('')}
  </div>`;

  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="view-header">
      <div class="view-title">&#128717; Etsy / Printify</div>
      <div class="view-sub">Your print-on-demand pipeline &mdash; proposals through to published</div>
    </div>
    ${tabBarHtml}
    <div id="ep-subtab-content"><div class="empty"><div class="empty-icon">&#9203;</div>Loading&hellip;</div></div>`;

  // Bind subtab clicks
  main.querySelectorAll('[data-ep-tab]').forEach(el => {
    el.addEventListener('click', async () => {
      _etsySubTab = el.dataset.epTab;
      main.querySelectorAll('[data-ep-tab]').forEach(x => x.classList.toggle('active', x.dataset.epTab === _etsySubTab));
      const sub = document.getElementById('ep-subtab-content');
      if (sub) sub.innerHTML = '<div class="empty"><div class="empty-icon">&#9203;</div>Loading&hellip;</div>';
      await _renderEpSubtab(_etsySubTab);
      bindCards();
    });
  });

  await _renderEpSubtab(_etsySubTab);
  bindCards();
}

async function _renderEpSubtab(id) {
  switch (id) {
    case 'dashboard':
    case 'store-stats': await renderStoreDashboard(); break;   // stats folded into the dashboard
    case 'proposals':   await renderProposals();  break;
    case 'review':      await renderReview();     break;
    case 'approved':    await renderApproved();   break;
    case 'published':   await renderPublished();  break;
    case 'products':    await renderProducts();   break;
  }
}

/* Jump helper for dashboard tiles → funnel subtabs. */
function epJump(id) { renderEtsyPrintify(id); }
window.epJump = epJump;

/* Open the Dashboard's Store Configuration section (optionally scrolled to a
   field). Used by Settings → Integrations "Configure" buttons and the Settings
   pointer card, so old "configure Etsy/Printify in Settings" paths land here. */
async function epGotoConfig(anchor) {
  if (typeof _currentView !== 'undefined' && _currentView !== 'etsy-printify') {
    await switchView('etsy-printify');
  }
  if (_etsySubTab !== 'dashboard') await renderEtsyPrintify('dashboard');
  let tries = 0;
  const t = setInterval(() => {
    const det = document.getElementById('ep-config-details');
    if (det) {
      clearInterval(t);
      det.open = true;
      if (anchor) {
        const el = document.getElementById(anchor);
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          const prev = el.style.outline;
          el.style.outline = '2px solid var(--accent)';
          setTimeout(() => { el.style.outline = prev; }, 1500);
        }
      }
    } else if (++tries > 40) clearInterval(t);
  }, 50);
}
window.epGotoConfig = epGotoConfig;

/* ══ DASHBOARD SUBTAB — the store command center ══ */
async function renderStoreDashboard() {
  // Fast paint from local endpoints; the slow external ones (Etsy status,
  // Printify shops, live store stats) patch themselves in after first paint.
  let s = {}, settings = {}, settingsLoaded = true;
  try { s = await api('/api/stats'); } catch {}
  try { settings = await api('/api/settings'); } catch { settingsLoaded = false; }

  const gateOn = settings.proposal_gate_enabled === '1';
  const deskOn = settings.proposal_desk_enabled === '1';

  const funnelTile = (icon, label, val, sub, tab, cls) =>
    `<div class="stat-card" style="cursor:pointer" onclick="epJump('${tab}')">
       <div class="stat-label">${icon} ${label}</div>
       <div class="stat-val ${cls||''}">${val}</div>
       ${sub?`<div style="font-size:.64rem;color:var(--muted);margin-top:5px;">${sub}</div>`:''}</div>`;

  let h = `
    <!-- connections -->
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;margin-bottom:16px;">
      <div class="settings-group" style="margin:0;">
        <div class="settings-group-title">&#128717; Etsy</div>
        <div id="ep-conn-etsy" style="font-size:.8rem;color:var(--muted);">Checking&hellip;</div>
        <div style="margin-top:8px;"><button class="btn-sm" onclick="epGotoConfig('s-etsy-key')">&#9881;&#65039; Configure</button></div>
      </div>
      <div class="settings-group" style="margin:0;">
        <div class="settings-group-title">&#128424;&#65039; Printify</div>
        <div id="ep-conn-printify" style="font-size:.8rem;color:var(--muted);">Checking&hellip;</div>
        <div style="margin-top:8px;"><button class="btn-sm" onclick="epGotoConfig('s-printify-key')">&#9881;&#65039; Configure</button></div>
      </div>
      <div class="settings-group" style="margin:0;">
        <div class="settings-group-title">&#129302; Automation</div>
        <div style="display:flex;flex-direction:column;gap:8px;font-size:.8rem;">
          <div style="display:flex;align-items:center;gap:8px;">
            <div class="toggle ${gateOn?'on':''}" id="ep-auto-gate" title="Scheduled proposal review gate — the LLM judge batch-scores pending proposals. Saved instantly."></div>
            <span>Review gate <span style="color:var(--muted);">(${esc(settings.proposal_gate_mode||'auto_weed')} &middot; batch ${esc(settings.proposal_gate_batch_size||'10')})</span></span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <div class="toggle ${deskOn?'on':''}" id="ep-auto-desk" title="Scheduled proposal desk — feeds, crew suggestions and the Etsy demand signal become lane-tagged proposals. Saved instantly."></div>
            <span>Proposal desk <span style="color:var(--muted);">(lanes: ${esc(settings.proposal_lanes_enabled||'all')})</span></span>
          </div>
        </div>
        <div style="margin-top:8px;"><button class="btn-sm" onclick="epGotoConfig('proposal-gate-section')">&#9881;&#65039; Pipeline settings</button></div>
      </div>
    </div>

    <!-- funnel at a glance -->
    <div class="section-header"><div class="section-title">&#128378; Funnel at a glance</div></div>
    <div class="stats-row" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:16px;">
      ${funnelTile('&#128161;','Proposals', s.proposals_pending||0, 'pending review', 'proposals', (s.proposals_pending||0)?'c-warn':'c-accent')}
      ${funnelTile('&#128269;','In review', s.review_count||0, 'generated designs', 'review')}
      ${funnelTile('&#9989;','Approved', s.approved_count||0, 'ready to publish', 'approved', 'c-green')}
      ${funnelTile('&#128717;','Published', s.published_count||0, 'live listings', 'published', 'c-accent2')}
    </div>

    <!-- sales & stats (folded-in Store Stats) -->
    <div class="settings-group" style="margin-bottom:16px;">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div class="settings-group-title" style="margin-bottom:0;">&#128200; Sales &amp; listings</div>
        <button class="btn-sm" id="ep-stats-refresh">&#8635; Refresh</button>
      </div>
      <div id="ep-stats-body" style="margin-top:10px;"><div style="color:var(--muted);font-size:.8rem;">&#8987; Loading live Printify &amp; Etsy data&hellip;</div></div>
    </div>

    <!-- store configuration (moved here from the global Settings tab) -->
    <details id="ep-config-details" class="settings-group" style="margin-bottom:16px;">
      <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#9881;&#65039; Store Configuration
        <span style="font-weight:400;font-size:.75rem;color:var(--muted);">&mdash; Printify &amp; Etsy keys, store identity, trend sources, proposal pipeline</span>
      </summary>
      <div class="settings-grid" style="margin-top:14px;">
        ${_epConfigHtml(settings)}
      </div>
    </details>`;

  _setContent(h);
  _bindEpConfig(settings, settingsLoaded);

  /* deferred fills — slow external calls patch in after first paint */
  const patch = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };

  _loadEtsyStatus();                    // fills the config section's status line + connect button
  api('/api/etsy/status').then(es => {
    patch('ep-conn-etsy', es.needs_reconnect
      ? `<span style="color:var(--warning,#e6a23c)">&#9888; Auth expired</span> — <a href="javascript:void(0)" onclick="reconnectEtsy()" style="color:var(--accent);">reconnect</a>`
      : es.connected
      ? `<span style="color:var(--green)">&#10003; Connected</span>${es.shop_id ? ` &middot; shop <b>${esc(es.shop_id)}</b>` : ''}`
      : `<span style="color:var(--muted)">Not connected</span> — add keys &amp; connect below`);
  }).catch(() => patch('ep-conn-etsy', '<span style="color:var(--muted)">Status unavailable</span>'));

  if (settings.printify_key) {
    api('/api/printify/shops').then(shops => {
      patch('ep-conn-printify', (shops && shops.length)
        ? `<span style="color:var(--green)">&#10003; Connected</span> &middot; ${shops.map(x => esc(x.title||x.id)).join(', ')}`
        : `<span style="color:var(--warning,#e6a23c)">&#9888; Key set, but no shops returned</span>`);
    }).catch(e => patch('ep-conn-printify',
      `<span style="color:var(--warning,#e6a23c)">&#9888; Key set, API unreachable</span> <span style="color:var(--muted)">(${esc(e.message||'error')})</span>`));
  } else {
    patch('ep-conn-printify', '<span style="color:var(--muted)">Not configured</span> — add your API key below');
  }

  loadStoreStatsInto('ep-stats-body');  // live Printify + Etsy numbers (tab-dashboard.js)
  document.getElementById('ep-stats-refresh')?.addEventListener('click', () => {
    patch('ep-stats-body', '<div style="color:var(--muted);font-size:.8rem;">&#8987; Refreshing&hellip;</div>');
    loadStoreStatsInto('ep-stats-body');
  });
}

/* The Store Configuration markup — one clean full-width block per concern
   (Printify, Etsy, Store defaults, Trend Sources, Review Gate, Proposal Desk),
   stacked by the single-column .settings-grid. Field ids unchanged so
   deep-links/anchors (s-etsy-key, s-printify-key, …) keep working.
   Store Name moved to Settings → Interface (App branding). */
function _epConfigHtml(settings) {
  return `
      <div class="settings-group">
        <div class="settings-group-title">&#128424;&#65039; Printify</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:0 18px;">
          <div class="field"><label>API Key ${hlp('Your Printify personal access token (Printify → Settings → Connections). Lets the app create products and push them to your print-on-demand shop. Stored locally; never shown after saving.')}</label><input type="password" id="s-printify-key" value="${esc(settings.printify_key||'')}" placeholder="pk_…"></div>
          <div class="field"><label>Shop ID ${hlp('Which Printify shop to publish into (a Printify account can have several — Etsy, eBay, etc.). Click “List Shops” below to find the numeric ID. Determines where “→ Printify” sends a design.')}</label><input type="text" id="s-printify-shop" value="${esc(settings.printify_shop_id||'')}" placeholder="12345678"></div>
        </div>
        <div style="display:flex;gap:8px;margin-top:10px;">
          <button class="btn-sm primary" id="s-save">&#128190; Save</button>
          <button class="btn-sm" id="s-shops">&#127978; List Shops</button>
        </div>
        <div id="shops-list" style="margin-top:12px;font-size:.78rem;color:var(--muted);"></div>
      </div>
      <div class="settings-group">
        <div class="settings-group-title">&#128717; Etsy</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:0 18px;">
          <div class="field"><label>API Key ${hlp('Your Etsy app “keystring” from the Etsy Developer portal. Used for the DIRECT Etsy listing path (creates draft listings via the Etsy API). Not needed if you sell through Printify→Etsy.')}</label><input type="password" id="s-etsy-key" value="${esc(settings.etsy_key||'')}" placeholder="your-etsy-api-key"></div>
          <div class="field"><label>Shop ID ${hlp('Your Etsy shop name/ID that new draft listings are created under. Only used by the direct Etsy API path.')}</label><input type="text" id="s-etsy-shop" value="${esc(settings.etsy_shop_id||'')}" placeholder="MyEtsyShop"></div>
          <div class="field"><label>OAuth Secret ${hlp('The Etsy app’s shared secret, paired with the API Key to authorize (OAuth) so the app can act on your shop. Kept local; only used by the direct Etsy path.')}</label><input type="password" id="s-etsy-secret" value="${esc(settings.etsy_shared_secret||'')}" placeholder="secret…"></div>
        </div>
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:4px;">
          <button class="btn-sm primary" id="s-save-2">&#128190; Save</button>
          <span id="etsy-conn-btn"></span>
          <span style="font-size:.78rem;color:var(--muted);">Status: <span id="etsy-conn-status" style="color:var(--muted)">Checking&hellip;</span></span>
        </div>
        <details style="margin-top:12px;">
          <summary style="cursor:pointer;font-size:.78rem;color:var(--text);">&#9432; How Etsy works here &mdash; choose ONE path</summary>
          <div style="margin-top:8px;padding:10px;background:var(--surface);border-radius:8px;border:1px solid var(--border);font-size:.75rem;color:var(--muted);line-height:1.5;">
            <b style="color:var(--text);">Path A &mdash; Printify &rarr; Etsy (recommended if Etsy is your Printify sales channel):</b><br>
            Click <b>&rarr; Printify</b> on an approved design. Printify creates the product AND automatically pushes a live listing to your connected Etsy store. Printify handles production &amp; fulfillment. <em>This is the right path if Etsy is set as your main store inside Printify.</em><br><br>
            <b style="color:var(--text);">Path B &mdash; Direct Etsy API:</b><br>
            Click <b>&#128717; Etsy</b> to create a draft listing directly on Etsy. Use this <em>only</em> if a design is NOT going through Printify (e.g. digital downloads, self-fulfilled items).<br><br>
            <b style="color:#e74c3c;">&#9888; DUPLICATE RISK:</b> If Etsy is already your Printify sales channel, using <em>both</em> buttons for the same design will create two Etsy listings. The app will warn you and block the Etsy button if a design is already published to Printify.
          </div>
        </details>
      </div>
      <div class="settings-group">
        <div class="settings-group-title">&#127978; Store Defaults</div>
        <div class="field" style="max-width:420px;">
          <label>Default Product Type ${hlp('The product (T-Shirt, Mug, Poster…) pre-selected when you open the “Publish to Printify” modal. Just a default to save clicks; you can change it per design.')}</label>
          <select id="s-default-product">${_productTypes.map(pt => `<option value="${esc(pt)}"${settings.default_product_type===pt?' selected':''}>${esc(pt)}</option>`).join('')}</select>
          <div style="font-size:.7rem;color:var(--muted);margin-top:3px;">Pre-selected when opening the Publish to Printify modal. The Store / Brand Name now lives in Settings &rarr; Interface.</div>
        </div>
        <button class="btn-sm primary" id="s-save-4" style="margin-top:10px;">&#128190; Save</button>
      </div>
      <div class="settings-group" id="trend-settings-section">
        <div class="settings-group-title">&#128200; Trend Sources</div>
        <div id="trend-body"><div style="color:var(--muted);font-size:.8rem;">Loading&#8230;</div></div>
      </div>
      <div class="settings-group" id="proposal-gate-section">
        <div class="settings-group-title">&#9878;&#65039; Proposal Review Gate</div>
        <div style="font-size:.78rem;color:var(--muted);margin-bottom:12px;line-height:1.5;">
          An LLM judge scores every pending proposal 0&ndash;100 (marketability, originality, print-on-demand fit,
          policy safety) and checks for near-duplicates &mdash; so the good ideas surface and the weeds get gated out
          before they reach image generation. Run it manually from the Proposals subtab (&#9878; Review all),
          or turn on the toggle to run it automatically on a schedule.
        </div>
        <div style="display:flex;gap:18px;flex-wrap:wrap;align-items:flex-end;">
          <div class="field" style="margin:0;">
            <label>Auto-review ${hlp('Master switch for the scheduled gate. On = the background scheduler batch-reviews all unscored pending proposals every “Interval” minutes. Off = review only runs when you click Review all / Rate. Saved instantly.')}</label>
            <div class="toggle ${settings.proposal_gate_enabled==='1'?'on':''}" id="s-gate-enabled"></div>
          </div>
          <div class="field" style="margin:0;min-width:190px;">
            <label>Mode ${hlp('What the gate DOES with a verdict. score_only: just records scores, touches nothing. auto_weed (default): also auto-rejects proposals scoring below the weed threshold. full_auto: additionally auto-approves high scorers — queues image generations exactly like clicking Approve. Mid-range scores always stay pending for you.')}</label>
            <select id="s-gate-mode">
              ${['score_only','auto_weed','full_auto'].map(m => `<option value="${m}"${(settings.proposal_gate_mode||'auto_weed')===m?' selected':''}>${m}</option>`).join('')}
            </select>
          </div>
          <div class="field" style="margin:0;min-width:190px;">
            <label>Weed below <b id="s-gate-reject-val">${esc(settings.proposal_gate_reject_below||'40')}</b> ${hlp('Proposals scoring UNDER this are verdict “reject” — auto-rejected in auto_weed/full_auto modes, only recorded in score_only.')}</label>
            <input type="range" id="s-gate-reject" min="0" max="100" step="5" value="${esc(settings.proposal_gate_reject_below||'40')}">
          </div>
          <div class="field" style="margin:0;min-width:190px;">
            <label>Auto-approve at <b id="s-gate-approve-val">${esc(settings.proposal_gate_approve_above||'80')}</b> ${hlp('Proposals scoring AT or ABOVE this are verdict “approve” — auto-approved (generations queued) ONLY in full_auto mode; otherwise they just sort to the top for you.')}</label>
            <input type="range" id="s-gate-approve" min="0" max="100" step="5" value="${esc(settings.proposal_gate_approve_above||'80')}">
          </div>
          <div class="field" style="margin:0;width:110px;">
            <label>Batch size ${hlp('Max proposals judged per run — scheduled OR manual Review all. The backlog drains in bounded bites instead of one endless LLM task. 0 = unlimited; default 10.')}</label>
            <input type="number" id="s-gate-batch" min="0" step="5" value="${esc(settings.proposal_gate_batch_size||'10')}">
          </div>
          <div class="field" style="margin:0;width:110px;">
            <label>Interval (min) ${hlp('How often the scheduled auto-review runs when the toggle is on. Minimum 15 minutes; default 360 (6 hours).')}</label>
            <input type="number" id="s-gate-interval" min="15" step="15" value="${esc(settings.proposal_gate_interval_min||'360')}">
          </div>
        </div>
      </div>
      <div class="settings-group" id="proposal-desk-section">
        <div class="settings-group-title">&#128480;&#65039; Proposal Desk</div>
        <div style="font-size:.78rem;color:var(--muted);margin-bottom:12px;line-height:1.5;">
          The intake side of the funnel: on a schedule it harvests the Company crew&rsquo;s &ldquo;Suggested:&rdquo; ideas
          into proposals, keeps the Etsy demand snapshot fresh, and runs the multi-lane trend scan (lanes + feeds
          configured in Trend Sources above). Also governable from Mission Control (Company control plane).
        </div>
        <div style="display:flex;gap:18px;flex-wrap:wrap;align-items:flex-end;">
          <div class="field" style="margin:0;">
            <label>Auto-run ${hlp('Master switch for the scheduled desk. On = every “Interval” minutes the desk imports qualifying agent/oracle/researcher suggestions as proposals and kicks a multi-lane scan. Saved instantly.')}</label>
            <div class="toggle ${settings.proposal_desk_enabled==='1'?'on':''}" id="s-desk-enabled"></div>
          </div>
          <div class="field" style="margin:0;width:110px;">
            <label>Interval (min) ${hlp('How often the desk runs when the toggle is on. Minimum 15 minutes; default 240 (4 hours).')}</label>
            <input type="number" id="s-desk-interval" min="15" step="15" value="${esc(settings.proposal_desk_interval_min||'240')}">
          </div>
          <div class="field" style="margin:0;">
            <label>Etsy trending scrape ${hlp('Opt-in: also scrape Etsy’s public trending page for market-wide hot search terms (the connected Etsy API is always used for your own listing performance). Failure-tolerant — a Etsy layout change just yields no terms. Saved instantly.')}</label>
            <div class="toggle ${settings.etsy_signal_scrape_enabled==='1'?'on':''}" id="s-desk-scrape"></div>
          </div>
        </div>
      </div>`;
}

/* Wire every control in the dashboard (config section + automation chips).
   Same save semantics the fields had in Settings: the three 💾 buttons PATCH
   /api/settings with the full key set; gate/desk controls save instantly. */
function _bindEpConfig(settings, settingsLoaded) {
  async function saveStoreConfig() {
    if (!settingsLoaded) {
      // The initial GET failed, so the inputs rendered EMPTY — saving now would
      // overwrite real keys (Printify/Etsy credentials) with ''.
      toast('Settings failed to load — refresh the page before saving.', 'error');
      return;
    }
    try {
      const patch = {
        printify_key:         document.getElementById('s-printify-key').value,
        printify_shop_id:     document.getElementById('s-printify-shop').value,
        etsy_key:             document.getElementById('s-etsy-key').value,
        etsy_shop_id:         document.getElementById('s-etsy-shop').value,
        etsy_shared_secret:   document.getElementById('s-etsy-secret').value,
        default_product_type: document.getElementById('s-default-product').value,
      };
      await api('/api/settings', { method: 'PATCH', body: JSON.stringify(patch) });
      try { _settings = await api('/api/settings'); } catch {}
      toast('Store settings saved ✓');
    } catch(e) { toast('Save failed: ' + e.message, 'error'); }
  }
  ['s-save','s-save-2','s-save-4'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('click', saveStoreConfig);
  });

  const shopsBtn = document.getElementById('s-shops');
  if (shopsBtn) shopsBtn.addEventListener('click', async () => {
    shopsBtn.disabled = true; shopsBtn.textContent = '⧗…';
    try {
      const shops = await api('/api/printify/shops');
      const el = document.getElementById('shops-list');
      el.innerHTML = (shops && shops.length)
        ? shops.map(s => `<div>• ${esc(s.title||s.id||JSON.stringify(s))}</div>`).join('')
        : 'No shops found';
    } catch(e) { toast('Error: ' + e.message, 'error'); }
    finally { shopsBtn.disabled = false; shopsBtn.textContent = '\u{1F3EA} List Shops'; }
  });

  // Load trend sources into the config section's placeholder
  _loadTrendIntoSettings();

  // ── Proposal Review Gate — every control saves instantly (like trend toggles) ──
  const _gatePatch = async (patch, okMsg) => {
    try { await api('/api/settings', { method: 'PATCH', body: JSON.stringify(patch) }); toast(okMsg || 'Gate setting saved ✓'); return true; }
    catch(e) { toast('Error: ' + e.message, 'error'); return false; }
  };
  // One master-toggle can live in two places (automation card + config section):
  // flip BOTH UI copies together so they never disagree.
  const _twinToggle = (ids, key, label) => {
    const els = ids.map(i => document.getElementById(i)).filter(Boolean);
    els.forEach(el => el.addEventListener('click', async () => {
      const on = !el.classList.contains('on');
      els.forEach(x => x.classList.toggle('on', on));
      if (!await _gatePatch({ [key]: on ? '1' : '0' }, `${label} ${on ? 'enabled' : 'disabled'}`))
        els.forEach(x => x.classList.toggle('on', !on));
    }));
  };
  _twinToggle(['s-gate-enabled', 'ep-auto-gate'], 'proposal_gate_enabled', 'Proposal gate auto-review');
  _twinToggle(['s-desk-enabled', 'ep-auto-desk'], 'proposal_desk_enabled', 'Proposal desk auto-run');
  _twinToggle(['s-desk-scrape'], 'etsy_signal_scrape_enabled', 'Etsy trending scrape');

  document.getElementById('s-gate-mode')?.addEventListener('change', (e) =>
    _gatePatch({ proposal_gate_mode: e.target.value }, `Gate mode: ${e.target.value}`));
  const _gateSlider = (id, valId, key) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('input',  () => { const v = document.getElementById(valId); if (v) v.textContent = el.value; });
    el.addEventListener('change', () => _gatePatch({ [key]: el.value }));
  };
  _gateSlider('s-gate-reject',  's-gate-reject-val',  'proposal_gate_reject_below');
  _gateSlider('s-gate-approve', 's-gate-approve-val', 'proposal_gate_approve_above');
  document.getElementById('s-gate-interval')?.addEventListener('change', (e) =>
    _gatePatch({ proposal_gate_interval_min: String(Math.max(15, parseInt(e.target.value || '360', 10) || 360)) }));
  document.getElementById('s-gate-batch')?.addEventListener('change', (e) =>
    _gatePatch({ proposal_gate_batch_size: String(Math.max(0, parseInt(e.target.value || '10', 10) || 0)) },
      'Gate batch size saved ✓'));
  document.getElementById('s-desk-interval')?.addEventListener('change', (e) =>
    _gatePatch({ proposal_desk_interval_min: String(Math.max(15, parseInt(e.target.value || '240', 10) || 240)) }));
}

/* Etsy connection status (slow external API) — fills the config section's
   status line + Connect/Disconnect button after the dashboard has painted.
   Moved here from settings-core.js along with the Etsy config UI. */
async function _loadEtsyStatus() {
  const st = document.getElementById('etsy-conn-status');
  const btn = document.getElementById('etsy-conn-btn');
  let es = {};
  try { es = await api('/api/etsy/status'); } catch {}
  if (st) st.innerHTML = es.needs_reconnect
    ? `<span style="color:var(--warning,#e6a23c)">&#9888; Auth expired — reconnect needed</span>`
    : es.connected
    ? `<span style="color:var(--green)">&#10003; Connected${es.shop_id ? ' &middot; Shop: ' + esc(es.shop_id) : ''}</span>`
    : `<span style="color:var(--muted)">Not connected</span>`;
  if (!btn) return;
  btn.innerHTML = es.needs_reconnect
    ? `<button class="btn-sm" id="s-etsy-connect">&#128260; Reconnect Etsy</button> <button class="btn-sm danger" id="s-etsy-disconnect">Disconnect</button>`
    : es.connected
    ? `<button class="btn-sm danger" id="s-etsy-disconnect">Disconnect</button>`
    : `<button class="btn-sm" id="s-etsy-connect">&#128279; Connect Etsy</button>`;
  const c = document.getElementById('s-etsy-connect');
  if (c) c.addEventListener('click', async () => {
    try { const r = await api('/api/etsy/connect'); if (r.url) window.open(r.url, '_blank'); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
  });
  const d = document.getElementById('s-etsy-disconnect');
  if (d) d.addEventListener('click', async () => {
    if (!confirm('Disconnect Etsy?')) return;
    try {
      await api('/api/etsy/disconnect', { method: 'DELETE' });
      toast('Etsy disconnected');
      if (typeof _currentView !== 'undefined' && _currentView === 'etsy-printify') renderEtsyPrintify('dashboard');
      else _loadEtsyStatus();
    }
    catch (e) { toast('Error: ' + e.message, 'error'); }
  });
}

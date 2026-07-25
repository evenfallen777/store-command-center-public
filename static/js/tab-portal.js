/* ══ PORTAL → WORDPRESS TAB ══
   Curate items from every source in the Store and push the ones you pick to
   example.com as WooCommerce external/affiliate products (Buy button links out),
   or push generated media to a Portfolio gallery page. Nothing goes live without a click.

   Sources:
     affiliate | software  → local catalog you enter here (electronics→soap, tools you promote)
     etsy | printify | cults3d → your live listings (linked out with images)
     image | video         → generated media → Portfolio gallery page  */

let _portalSource = 'affiliate';
let _portalItems = [];
let _portalPrograms = [];   // affiliate programs (signup portals + your saved tags)

const PORTAL_SOURCES = [
  { key: 'programs',  icon: '&#128279;',         label: 'Programs' },
  { key: 'affiliate', icon: '&#128717;&#65039;', label: 'Affiliate' },
  { key: 'software',  icon: '&#128190;',         label: 'Software' },
  { key: 'etsy',      icon: '&#129527;',         label: 'Etsy' },
  { key: 'printify',  icon: '&#128085;',         label: 'Printify' },
  { key: 'cults3d',   icon: '&#128424;&#65039;', label: 'Cults3D' },
  { key: 'image',     icon: '&#127912;',         label: 'Images' },
  { key: 'video',     icon: '&#127916;',         label: 'Videos' },
  { key: 'wp',        icon: '&#127760;',         label: 'On WordPress' },
];

async function loadPortalPrograms() {
  try { _portalPrograms = await api('/api/portal/programs'); }
  catch { _portalPrograms = []; }
  return _portalPrograms;
}

async function renderPortal() {
  document.getElementById('main-content').innerHTML = `
    <div class="view-header">
      <div class="view-title">&#127760; Portal &rarr; WordPress</div>
      <div class="view-sub">Curate anything you promote &mdash; affiliate, Etsy, Printify, Cults3D, software &mdash;
        and push it to your <b>example.com</b> store. Generated media go to a Portfolio gallery.</div>
    </div>
    <div id="portal-status" style="margin-bottom:10px;"></div>
    <div id="portal-config" style="margin-bottom:14px;"></div>
    <div id="portal-pills" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;"></div>
    <div id="portal-body"></div>`;

  renderPortalPills();
  await loadPortalStatus();
  await renderPortalConfig();
  await switchPortalSource(_portalSource);
}
window.renderPortal = renderPortal;

/* ── WordPress connection settings (enter creds post-deploy) ─────────────── */
async function renderPortalConfig() {
  let cfg = {};
  try { cfg = await api('/api/settings'); } catch {}
  const el = document.getElementById('portal-config');
  // Open by default when products aren't configured yet (fresh/retail deploy).
  let st = {}; try { st = await api('/api/portal/status'); } catch {}
  const open = !st.products_configured ? 'open' : '';
  el.innerHTML = `
    <details class="settings-group" ${open}>
      <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#9881;&#65039; WordPress connection</summary>
      <div style="font-size:.72rem;color:var(--muted);margin:8px 0 12px;line-height:1.6;">
        Point this at your WooCommerce store. Products use the WooCommerce REST API over HTTPS
        (Store URL + consumer key/secret from <b>WooCommerce &rarr; Settings &rarr; Advanced &rarr; REST API</b>).
        Media/gallery uploads use the WordPress MCP endpoint + Bearer token (optional).
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <div class="field" style="grid-column:1/3;"><label>Store URL (HTTPS) ${hlp('The base URL of your WooCommerce/WordPress store (must be https). The app pushes products here via the WooCommerce REST API — it has to be reachable from this server.')}</label>
          <input type="text" id="wp-url" value="${esc(cfg.wp_url || '')}" placeholder="https://yourstore.com"></div>
        <div class="field"><label>Consumer Key ${hlp('WooCommerce REST API consumer key (WooCommerce → Settings → Advanced → REST API). Paired with the secret so the app can create products on your store. Needs Read/Write.')}</label>
          <input type="password" id="wp-ck" value="${esc(cfg.wp_consumer_key || '')}" placeholder="ck_…"></div>
        <div class="field"><label>Consumer Secret ${hlp('The secret half of the WooCommerce REST API credentials. Kept local; used with the Consumer Key to authenticate product pushes.')}</label>
          <input type="password" id="wp-cs" value="${esc(cfg.wp_consumer_secret || '')}" placeholder="cs_…"></div>
        <div class="field"><label>MCP Endpoint <span style="color:var(--muted);font-weight:400;">(optional, for media)</span> ${hlp('Optional. The WordPress MCP endpoint (easy-mcp-ai plugin) used to upload media/portfolio items to the site. Leave blank if you only push products.')}</label>
          <input type="text" id="wp-mcp-url" value="${esc(cfg.wp_mcp_url || '')}" placeholder="http://localhost:8090/wp-json/easy-mcp-ai/v1/mcp"></div>
        <div class="field"><label>MCP Token <span style="color:var(--muted);font-weight:400;">(optional)</span> ${hlp('Optional Bearer token for the WordPress MCP endpoint above. Only needed for the media/portfolio upload features.')}</label>
          <input type="password" id="wp-mcp-token" value="${esc(cfg.wp_mcp_token || '')}" placeholder="wpmcp_…"></div>
      </div>
      <div style="display:flex;gap:8px;margin-top:6px;align-items:center;">
        <button class="btn-sm primary" onclick="portalSaveConfig()">&#128190; Save &amp; test</button>
        <span id="wp-cfg-msg" style="font-size:.78rem;color:var(--muted);"></span>
      </div>
    </details>`;
}

async function portalSaveConfig() {
  const payload = {
    wp_url: document.getElementById('wp-url').value.trim(),
    wp_consumer_key: document.getElementById('wp-ck').value.trim(),
    wp_consumer_secret: document.getElementById('wp-cs').value.trim(),
    wp_mcp_url: document.getElementById('wp-mcp-url').value.trim(),
    wp_mcp_token: document.getElementById('wp-mcp-token').value.trim(),
  };
  const msg = document.getElementById('wp-cfg-msg');
  msg.textContent = 'Saving…';
  try {
    await api('/api/portal/config', { method: 'POST', body: JSON.stringify(payload) });
    await loadPortalStatus();
    const st = await api('/api/portal/status');
    msg.textContent = st.connected ? '✅ Connected' : ('⚠️ ' + (st.error || 'not connected'));
    toast(st.connected ? 'WordPress connected' : 'Saved, but not connected', st.connected ? 'success' : 'error');
  } catch (e) { msg.textContent = '❌ ' + e.message; toast('Save failed: ' + e.message, 'error'); }
}
window.renderPortalConfig = renderPortalConfig;
window.portalSaveConfig = portalSaveConfig;

function renderPortalPills() {
  document.getElementById('portal-pills').innerHTML = PORTAL_SOURCES.map(s => `
    <button class="btn-sm ${s.key === _portalSource ? 'primary' : ''}" data-psrc="${s.key}"
      onclick="switchPortalSource('${s.key}')">${s.icon} ${s.label}</button>`).join('');
}

async function loadPortalStatus() {
  const el = document.getElementById('portal-status');
  try {
    const s = await api('/api/portal/status');
    if (s.connected) {
      el.innerHTML = `<div style="background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.35);
        border-radius:10px;padding:10px 14px;font-size:.82rem;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <span style="color:#22c55e;font-weight:600;">&#9679; Connected</span>
        <span style="color:var(--muted);">${esc(s.wp_url)}</span>
        <span style="color:var(--muted);">&middot; ${s.total_products ?? '?'} products live</span>
        ${s.media_configured ? '<span style="color:var(--muted);">&middot; media bridge ready</span>'
          : '<span style="color:var(--warn);">&middot; media bridge off</span>'}
      </div>`;
    } else {
      el.innerHTML = `<div style="background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.35);
        border-radius:10px;padding:10px 14px;font-size:.82rem;">
        <span style="color:#f87171;font-weight:600;">&#9679; Not connected</span>
        <span style="color:var(--muted);"> &mdash; ${esc(s.error || 'WooCommerce API not configured')}</span>
      </div>`;
    }
  } catch (e) {
    el.innerHTML = `<div style="color:var(--warn);font-size:.82rem;">Status check failed: ${esc(e.message)}</div>`;
  }
}

async function switchPortalSource(src) {
  _portalSource = src;
  renderPortalPills();
  const body = document.getElementById('portal-body');
  body.innerHTML = '<div class="loading-state">Loading…</div>';
  if (src === 'programs') return renderPrograms();
  if (src === 'wp')       return loadWpProducts();
  await loadPortalItems(src);
}

/* ── source item grids (curate + push) ───────────────────────────────────── */
async function loadPortalItems(src) {
  const body = document.getElementById('portal-body');
  const isMedia = (src === 'image' || src === 'video');
  const isLocal = (src === 'affiliate' || src === 'software');
  if (isLocal && !_portalPrograms.length) await loadPortalPrograms();

  let data;
  try {
    data = await api(`/api/portal/items?source=${encodeURIComponent(src)}`);
  } catch (e) {
    body.innerHTML = `<div class="empty"><div class="empty-icon">&#9888;&#65039;</div>${esc(e.message)}</div>`;
    return;
  }
  _portalItems = data.items || [];

  const addForm = isLocal ? portalAddFormHtml(src) : '';
  const pushLabel = isMedia ? '&#128444;&#65039; Push selected to Portfolio' : '&#11014;&#65039; Push selected to WordPress';
  const pushTitle = isMedia
    ? 'Uploads the selected media to the Portfolio gallery page on example.com via the WordPress MCP endpoint.'
    : 'Publishes the selected items to example.com as WooCommerce external/affiliate products — the Buy button links out to each item external URL. Items already on the store are updated, not duplicated.';

  if (!_portalItems.length) {
    body.innerHTML = addForm + `<div class="empty"><div class="empty-icon">&#128230;</div>
      Nothing here yet${isLocal ? ' — add your first item above.' : '.'}</div>`;
    return;
  }

  const cards = _portalItems.map((it, i) => portalCardHtml(it, i, isMedia, isLocal)).join('');
  body.innerHTML = addForm + `
    <div style="display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap;">
      <label style="font-size:.82rem;display:flex;align-items:center;gap:6px;cursor:pointer;">
        <input type="checkbox" id="portal-selall" onchange="portalToggleAll(this.checked)"> Select all
      </label>
      <span style="color:var(--muted);font-size:.8rem;" id="portal-selcount">0 selected</span>
      <button class="btn-sm primary" id="portal-push" onclick="portalPush(${isMedia})" title="${pushTitle}" disabled>${pushLabel}</button>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;">${cards}</div>`;
}

function portalCardHtml(it, i, isMedia, isLocal) {
  // 'image' source items are local design originals (/designs/<sub>/<file>, often
  // multi-MB) — thumbnail those; external CDN urls (etsy/printify/cults3d/affiliate)
  // and video posters can't be thumbnailed server-side, so they load as-is.
  const fullImg = it.image_url ? (it.image_url.startsWith('/') ? API + it.image_url : esc(it.image_url)) : '';
  const thumbImg = it.source === 'image' ? thumbUrl(it.image_url) : fullImg;
  const img = it.image_url
    ? (it.source === 'video'
        ? `<video src="${fullImg}" muted style="width:100%;height:150px;object-fit:cover;border-radius:8px 8px 0 0;"></video>`
        : `<img src="${thumbImg}" loading="lazy" decoding="async" style="width:100%;height:150px;object-fit:cover;border-radius:8px 8px 0 0;" onerror="this.onerror=null;this.src='${fullImg}'">`)
    : `<div style="width:100%;height:150px;display:flex;align-items:center;justify-content:center;background:var(--bg2);border-radius:8px 8px 0 0;color:var(--muted);font-size:2rem;">&#128444;&#65039;</div>`;
  const pushedBadge = it.pushed
    ? `<a href="${esc(it.wp_link || '#')}" target="_blank" rel="noopener"
         style="position:absolute;top:8px;left:8px;background:rgba(34,197,94,.9);color:#fff;font-size:.62rem;
         padding:2px 7px;border-radius:20px;text-decoration:none;">&#10003; on store</a>` : '';
  const missingUrl = (!isMedia && !it.external_url)
    ? `<div style="color:var(--warn);font-size:.66rem;margin-top:3px;">&#9888;&#65039; no Buy link ${isLocal ? '' : '— add one before push'}</div>` : '';
  return `
    <div class="card" style="position:relative;overflow:hidden;padding:0;">
      <input type="checkbox" class="portal-chk" data-i="${i}" onchange="portalUpdateCount()"
        style="position:absolute;top:8px;right:8px;width:20px;height:20px;z-index:2;cursor:pointer;">
      ${pushedBadge}${img}
      <div style="padding:10px 12px;">
        <div style="font-weight:600;font-size:.82rem;line-height:1.3;margin-bottom:4px;">${esc(it.title || '(untitled)')}</div>
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
          <span style="color:var(--accent2);font-size:.8rem;">${it.price ? '$' + esc(it.price) : '&mdash;'}</span>
          ${it.external_url ? `<a href="${esc(it.external_url)}" target="_blank" rel="noopener" style="font-size:.68rem;color:var(--muted);">link &#8599;</a>` : ''}
        </div>
        ${missingUrl}
        ${isLocal ? `<div style="margin-top:8px;display:flex;gap:6px;">
          <button class="btn-sm" onclick="portalEditAff(${it.uid})">&#9998; Edit</button>
          <button class="btn-sm" onclick="portalDelAff(${it.uid})" title="Removes this item from your local Portal catalog. Does not remove it from example.com if already pushed.">&#128465;&#65039;</button></div>` : ''}
      </div>
    </div>`;
}

function portalToggleAll(on) {
  document.querySelectorAll('.portal-chk').forEach(c => { c.checked = on; });
  portalUpdateCount();
}
function portalUpdateCount() {
  const n = document.querySelectorAll('.portal-chk:checked').length;
  const c = document.getElementById('portal-selcount'); if (c) c.textContent = `${n} selected`;
  const b = document.getElementById('portal-push'); if (b) b.disabled = n === 0;
}

async function portalPush(isMedia) {
  const idxs = [...document.querySelectorAll('.portal-chk:checked')].map(c => +c.dataset.i);
  if (!idxs.length) return;
  const items = idxs.map(i => _portalItems[i]);
  const btn = document.getElementById('portal-push');
  btn.disabled = true; const orig = btn.innerHTML; btn.innerHTML = '⏳ Pushing…';
  try {
    const endpoint = isMedia ? '/api/portal/portfolio' : '/api/portal/push';
    const res = await api(endpoint, { method: 'POST', body: JSON.stringify({ items, status: 'publish' }) });
    const ok = res.pushed ?? res.uploaded ?? 0;
    const failed = res.failed ?? 0;
    if (failed) {
      const firstErr = (res.results || []).find(r => !r.ok);
      toast(`Pushed ${ok}, ${failed} failed${firstErr ? ': ' + firstErr.error : ''}`, failed > ok ? 'error' : 'success');
    } else {
      toast(`✅ Pushed ${ok} to WordPress${isMedia && res.page ? ' — Portfolio updated' : ''}`);
    }
    await loadPortalStatus();
    await loadPortalItems(_portalSource);
  } catch (e) {
    toast('Push failed: ' + e.message, 'error');
    btn.disabled = false; btn.innerHTML = orig;
  }
}

/* ── affiliate / software add + edit form ────────────────────────────────── */
function portalAddFormHtml(kind) {
  const label = kind === 'software' ? 'software you offer/promote' : 'an affiliate product you use & promote';
  return `
    <details class="settings-group" style="margin-bottom:16px;" id="portal-addwrap">
      <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#10133; Add ${kind === 'software' ? 'software' : 'affiliate product'}</summary>
      <div style="font-size:.72rem;color:var(--muted);margin:8px 0 12px;">Add ${label}. The Buy button links out to your affiliate/download URL.</div>
      <input type="hidden" id="paf-id" value="">
      <input type="hidden" id="paf-kind" value="${kind}">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <div class="field" style="grid-column:1/3;"><label>Title *</label><input type="text" id="paf-title" placeholder="e.g. Anker USB-C Charger"></div>
        <div class="field" style="grid-column:1/3;"><label>Buy / affiliate link * ${hlp('Where the Buy button sends shoppers on example.com — your affiliate or download URL. Required. Pick a program below to auto-append your saved tracking tag.')}</label><input type="text" id="paf-url" placeholder="paste the product link — e.g. https://amazon.com/dp/B0..."></div>
        ${kind === 'software' ? '' : `<div class="field" style="grid-column:1/3;"><label>Affiliate program
          <span style="color:var(--muted);font-weight:400;">(auto-applies your saved tag)</span> ${hlp('Optional. Applies the saved tracking tag/ID for that program to the Buy link so you earn commission. Set tags up in the Programs tab first.')}</label>
          <select id="paf-program">
            <option value="">— none / link already tagged —</option>
            ${_portalPrograms.map(pr => `<option value="${pr.id}">${esc(pr.name)}${pr.tag_value ? ' ✓' : ''}</option>`).join('')}
          </select></div>`}
        <div class="field"><label>Price</label><input type="text" id="paf-price" placeholder="19.99"></div>
        <div class="field"><label>Category</label><input type="text" id="paf-cat" placeholder="${kind === 'software' ? 'Software' : 'Tech Picks'}"></div>
        <div class="field" style="grid-column:1/3;"><label>Image URL</label><input type="text" id="paf-img" placeholder="https://…/photo.jpg (optional)"></div>
        <div class="field" style="grid-column:1/3;"><label>Description</label><textarea id="paf-desc" rows="2" placeholder="Short description"></textarea></div>
        <div class="field" style="grid-column:1/3;"><label>Tags (comma-separated)</label><input type="text" id="paf-tags" placeholder="usb-c, charging, tech"></div>
      </div>
      <div style="display:flex;gap:8px;margin-top:6px;">
        <button class="btn-sm primary" onclick="portalSaveAff()">&#128190; Save</button>
        <button class="btn-sm" onclick="portalResetAff()">Clear</button>
      </div>
    </details>`;
}

function portalResetAff() {
  ['paf-id','paf-title','paf-url','paf-price','paf-cat','paf-img','paf-desc','paf-tags','paf-program']
    .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
}

async function portalSaveAff() {
  const id = document.getElementById('paf-id').value;
  const payload = {
    kind: document.getElementById('paf-kind').value,
    title: document.getElementById('paf-title').value.trim(),
    external_url: document.getElementById('paf-url').value.trim(),
    price: document.getElementById('paf-price').value.trim(),
    category: document.getElementById('paf-cat').value.trim(),
    image_url: document.getElementById('paf-img').value.trim(),
    description: document.getElementById('paf-desc').value.trim(),
    tags: document.getElementById('paf-tags').value.trim(),
    program_id: (document.getElementById('paf-program')?.value || '') ? +document.getElementById('paf-program').value : null,
  };
  if (!payload.title || !payload.external_url) { toast('Title and Buy link are required', 'error'); return; }
  try {
    if (id) await api(`/api/portal/affiliate/${id}`, { method: 'PATCH', body: JSON.stringify(payload) });
    else    await api('/api/portal/affiliate', { method: 'POST', body: JSON.stringify(payload) });
    toast('✅ Saved');
    portalResetAff();
    await loadPortalItems(_portalSource);
  } catch (e) { toast('Save failed: ' + e.message, 'error'); }
}

async function portalEditAff(id) {
  const it = _portalItems.find(x => String(x.uid) === String(id));
  if (!it) return;
  const wrap = document.getElementById('portal-addwrap'); if (wrap) wrap.open = true;
  document.getElementById('paf-id').value = id;
  document.getElementById('paf-title').value = it.title || '';
  document.getElementById('paf-url').value = it.external_url || '';
  document.getElementById('paf-price').value = it.price || '';
  document.getElementById('paf-cat').value = it.category || '';
  document.getElementById('paf-img').value = it.image_url || '';
  document.getElementById('paf-desc').value = it.description || '';
  document.getElementById('paf-tags').value = it.tags || '';
  const psel = document.getElementById('paf-program'); if (psel) psel.value = it.program_id ? String(it.program_id) : '';
  wrap?.scrollIntoView({ behavior: 'smooth' });
}

async function portalDelAff(id) {
  if (!confirm('Delete this item?')) return;
  try { await api(`/api/portal/affiliate/${id}`, { method: 'DELETE' }); toast('Deleted'); await loadPortalItems(_portalSource); }
  catch (e) { toast('Delete failed: ' + e.message, 'error'); }
}

/* ── affiliate PROGRAMS: signup portals + your saved tags ─────────────────── */
async function renderPrograms() {
  const body = document.getElementById('portal-body');
  await loadPortalPrograms();
  const p = _portalPrograms;
  const done = p.filter(x => x.tag_value || x.signed_up).length;

  const card = (x) => {
    const hasTag = !!(x.tag_value && x.tag_value.trim());
    const ready = hasTag || x.signed_up;
    const isNet = x.ptype === 'network';
    const isDirect = (x.via === 'Direct');
    const badge = isNet ? 'Network' : (isDirect ? 'Direct' : 'via ' + esc(x.via || x.network));
    const tagHint = x.tag_param
      ? `Auto-added to your links as <code>?${esc(x.tag_param)}=…</code>`
      : isNet
        ? `Sign up here, then apply to individual brands inside ${esc(x.name)}. You get deep-links to paste per product.`
        : isDirect
          ? `Runs its own program &mdash; sign up direct, then paste your affiliate link per product.`
          : `Hosted on <b>${esc(x.via)}</b>. Join that network first, then apply to ${esc(x.name)} inside it.`;
    return `
      <div class="card" style="padding:12px 14px;position:relative;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
          <span style="width:9px;height:9px;border-radius:50%;background:${ready ? '#22c55e' : 'var(--muted)'};flex:0 0 auto;"></span>
          <span style="font-weight:600;font-size:.9rem;">${esc(x.name)}</span>
          <span style="font-size:.62rem;color:var(--muted);border:1px solid var(--border,#3334);border-radius:20px;padding:1px 7px;white-space:nowrap;">${badge}</span>
          ${x.is_custom ? `<button class="btn-sm" style="margin-left:auto;padding:1px 6px;" title="Delete" onclick="portalDelProgram(${x.id})">&#128465;&#65039;</button>` : ''}
        </div>
        <div style="font-size:.68rem;color:var(--muted);line-height:1.5;margin-bottom:8px;">${tagHint}</div>
        <div style="display:flex;gap:6px;margin-bottom:8px;">
          <a class="btn-sm primary" href="${esc(x.signup_url || '#')}" target="_blank" rel="noopener"
             onclick="portalMarkSignup(${x.id})" style="flex:0 0 auto;">&#128279; ${isNet ? 'Join network' : 'Apply'} &#8599;</a>
        </div>
        <div class="field" style="margin-bottom:6px;">
          <label style="font-size:.66rem;">Your tag / publisher ID ${hlp('Your affiliate tracking ID for this program (e.g. an Amazon Associates tag). It’s appended to the buy link so you earn commission on sales.')}</label>
          <input type="text" id="prog-tag-${x.id}" value="${esc(x.tag_value || '')}"
            placeholder="${x.tag_param === 'tag' ? 'e.g. yourtag-20' : 'publisher id / tracking code'}">
        </div>
        <div class="field" style="margin-bottom:8px;">
          <label style="font-size:.66rem;">Notes (login, account #, etc.)</label>
          <input type="text" id="prog-note-${x.id}" value="${esc(x.account_id || '')}" placeholder="optional">
        </div>
        <button class="btn-sm primary" onclick="portalSaveProgram(${x.id})">&#128190; Save</button>
      </div>`;
  };

  const nets    = p.filter(x => x.ptype === 'network');
  const viaNet  = p.filter(x => x.ptype === 'merchant' && x.via && x.via !== 'Direct');
  const direct  = p.filter(x => x.ptype === 'merchant' && (!x.via || x.via === 'Direct'));
  const grid = (arr) => `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px;margin-bottom:20px;">${arr.map(card).join('')}</div>`;
  body.innerHTML = `
    <div style="background:rgba(124,58,237,.08);border:1px solid rgba(124,58,237,.3);border-radius:12px;
      padding:12px 16px;margin-bottom:16px;font-size:.8rem;line-height:1.6;">
      <b>&#128279; How affiliate programs actually work</b>
      <div style="color:var(--muted);margin-top:4px;">
        Most retailers <b>don't run their own program</b> &mdash; they live on an affiliate
        <b>network</b>. So you: <b>1.</b> join the network(s) below &nbsp;<b>2.</b> apply to each
        brand <i>inside</i> that network &nbsp;<b>3.</b> paste the tag/ID it gives you here &nbsp;
        <b>4.</b> add products in the <b>Affiliate</b> tab and pick the program.
        <b>${done}/${p.length}</b> set up.
        <br><span style="opacity:.85;">&#9432; Biggest win: <b>Impact.com</b> hosts Walmart, Target, Home Depot &amp; Best Buy.
        <b>Rakuten</b> now hosts Newegg &amp; Etsy. <b>CJ/FlexOffers/Sovrn</b> host Lowe's. Only <b>Amazon</b> is fully direct.</span>
      </div>
    </div>
    <div style="font-weight:600;font-size:.82rem;margin:4px 0 8px;">&#127760; Networks &amp; platforms <span style="color:var(--muted);font-weight:400;">&mdash; sign up to these first</span></div>
    ${grid(nets)}
    <div style="font-weight:600;font-size:.82rem;margin:4px 0 8px;">&#127978; Retailers <span style="color:var(--muted);font-weight:400;">&mdash; apply inside the network shown</span></div>
    ${grid(viaNet)}
    <div style="font-weight:600;font-size:.82rem;margin:4px 0 8px;">&#127981; Direct brand programs <span style="color:var(--muted);font-weight:400;">&mdash; sign up straight with the brand</span></div>
    ${grid(direct)}

    <details class="settings-group" style="margin-top:18px;">
      <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#10133; Add a custom program</summary>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px;">
        <div class="field"><label>Name *</label><input type="text" id="npg-name" placeholder="e.g. B&amp;H Photo"></div>
        <div class="field"><label>Network</label><input type="text" id="npg-net" placeholder="Direct / Impact / …"></div>
        <div class="field" style="grid-column:1/3;"><label>Signup URL</label><input type="text" id="npg-url" placeholder="https://…"></div>
        <div class="field"><label>Tag URL param <span style="color:var(--muted);font-weight:400;">(optional)</span> ${hlp('Only for Amazon-style links where the tag rides in a URL query param (e.g. tag). Leave blank for network/deep-link programs that give you a full pre-tagged link.')}</label><input type="text" id="npg-param" placeholder="tag (Amazon-style only)"></div>
        <div class="field"><label>Your tag <span style="color:var(--muted);font-weight:400;">(optional)</span></label><input type="text" id="npg-tag" placeholder="if you have it"></div>
      </div>
      <button class="btn-sm primary" style="margin-top:6px;" onclick="portalAddProgram()">&#128190; Add program</button>
    </details>`;
}

async function portalMarkSignup(id) {
  // opening the signup link flags the program as "applied" (visual only; safe to undo by clearing tag)
  try { await api(`/api/portal/programs/${id}`, { method: 'PATCH', body: JSON.stringify({ signed_up: 1 }) }); } catch {}
}

async function portalSaveProgram(id) {
  const tag = document.getElementById(`prog-tag-${id}`)?.value.trim() || '';
  const note = document.getElementById(`prog-note-${id}`)?.value.trim() || '';
  try {
    await api(`/api/portal/programs/${id}`, { method: 'PATCH',
      body: JSON.stringify({ tag_value: tag, account_id: note, signed_up: tag ? 1 : undefined }) });
    toast('✅ Saved' + (tag ? ' — tag will apply to linked products' : ''));
    await renderPrograms();
  } catch (e) { toast('Save failed: ' + e.message, 'error'); }
}

async function portalAddProgram() {
  const name = document.getElementById('npg-name').value.trim();
  if (!name) { toast('Program name is required', 'error'); return; }
  const payload = {
    name,
    network: document.getElementById('npg-net').value.trim(),
    signup_url: document.getElementById('npg-url').value.trim(),
    tag_param: document.getElementById('npg-param').value.trim(),
    tag_value: document.getElementById('npg-tag').value.trim(),
  };
  try { await api('/api/portal/programs', { method: 'POST', body: JSON.stringify(payload) }); toast('✅ Added'); await renderPrograms(); }
  catch (e) { toast('Add failed: ' + e.message, 'error'); }
}

async function portalDelProgram(id) {
  if (!confirm('Delete this custom program?')) return;
  try { await api(`/api/portal/programs/${id}`, { method: 'DELETE' }); toast('Deleted'); await renderPrograms(); }
  catch (e) { toast('Delete failed: ' + e.message, 'error'); }
}

/* ── what's live on WordPress ────────────────────────────────────────────── */
async function loadWpProducts() {
  const body = document.getElementById('portal-body');
  try {
    const data = await api('/api/portal/wp-products');
    if (!data.products.length) {
      body.innerHTML = '<div class="empty"><div class="empty-icon">&#128722;</div>No products on the store yet.</div>';
      return;
    }
    body.innerHTML = `<div style="color:var(--muted);font-size:.8rem;margin-bottom:10px;">${data.count} products live on example.com</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;">
      ${data.products.map(p => `
        <div class="card" style="padding:0;overflow:hidden;">
          ${p.image ? `<img src="${esc(p.image)}" loading="lazy" style="width:100%;height:150px;object-fit:cover;">`
            : `<div style="height:150px;background:var(--bg2);"></div>`}
          <div style="padding:10px 12px;">
            <div style="font-weight:600;font-size:.82rem;">${esc(p.name)}</div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px;">
              <span style="color:var(--accent2);font-size:.8rem;">${p.price ? '$' + esc(p.price) : esc(p.type)}</span>
              <span style="font-size:.66rem;color:var(--muted);">${esc(p.status)}</span>
            </div>
            <div style="margin-top:8px;display:flex;gap:6px;">
              <a class="btn-sm" href="${esc(p.permalink || '#')}" target="_blank" rel="noopener">View &#8599;</a>
              <button class="btn-sm" onclick="portalDelWp(${p.id})" title="Permanently deletes this product from example.com (WooCommerce). Cannot be undone.">&#128465;&#65039;</button>
            </div>
          </div>
        </div>`).join('')}</div>`;
  } catch (e) {
    body.innerHTML = `<div class="empty"><div class="empty-icon">&#9888;&#65039;</div>${esc(e.message)}</div>`;
  }
}

async function portalDelWp(pid) {
  if (!confirm('Delete this product from the store? This cannot be undone.')) return;
  try { await api(`/api/portal/wp-products/${pid}`, { method: 'DELETE' }); toast('Deleted from store'); await loadWpProducts(); await loadPortalStatus(); }
  catch (e) { toast('Delete failed: ' + e.message, 'error'); }
}

window.switchPortalSource = switchPortalSource;
window.portalToggleAll = portalToggleAll;
window.portalUpdateCount = portalUpdateCount;
window.portalPush = portalPush;
window.portalSaveAff = portalSaveAff;
window.portalResetAff = portalResetAff;
window.portalEditAff = portalEditAff;
window.portalDelAff = portalDelAff;
window.loadWpProducts = loadWpProducts;
window.portalDelWp = portalDelWp;
window.renderPrograms = renderPrograms;
window.portalMarkSignup = portalMarkSignup;
window.portalSaveProgram = portalSaveProgram;
window.portalAddProgram = portalAddProgram;
window.portalDelProgram = portalDelProgram;
window.loadPortalPrograms = loadPortalPrograms;

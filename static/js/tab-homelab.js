/* ══ SERVICES / HOMELAB HUB ══
   One place for all your Docker services + *arr apps: auto-discovered, grouped by
   category, with a running/stopped dot and clickable host:port links. Helper/DB/
   sidecar containers are hidden by default. *arr apps show version/queue/health when
   you add their API key. Manual entries cover anything non-Docker. */

let _hlIncludeHidden = false;

async function renderHomelab() {
  document.getElementById('main-content').innerHTML = `
    <div class="view-header">
      <div class="view-title">&#128051; Docker</div>
      <div class="view-sub">Your containers in one place — Docker services, *arr apps, and anything you add. Click to open. For host CPU/RAM/disk/GPU, see Infrastructure.</div>
    </div>
    <div id="hl-bar" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:14px;"></div>
    <div id="hl-body"><div class="loading-state">Discovering services…</div></div>`;
  await renderHomelabBar();
  await loadHomelab();
}
window.renderHomelab = renderHomelab;

async function renderHomelabBar() {
  let cfg = {}; try { cfg = await api('/api/homelab/config'); } catch {}
  document.getElementById('hl-bar').innerHTML = `
    <span style="font-size:.78rem;color:var(--muted);">Host <input type="text" id="hl-host" value="${esc(cfg.host || '')}" style="width:130px;" title="LAN host for service links"> </span>
    <button class="btn-sm" onclick="hlSaveHost()" title="Save this LAN host/IP. All auto-built service links become http://host:port using it - set it to the machine actually running your containers.">&#128190;</button>
    <button class="btn-sm" onclick="loadHomelab()" title="Re-run docker ps and rebuild the service tiles. Results are cached ~8s, so a rapid re-click may show the same list.">&#128260; Refresh</button>
    <label style="font-size:.78rem;display:flex;gap:6px;align-items:center;cursor:pointer;">
      <input type="checkbox" ${_hlIncludeHidden ? 'checked' : ''} onchange="hlToggleHidden(this.checked)"> show hidden/helpers ${hlp('Reveal the containers auto-hidden by default - databases, redis, VPN sidecars, and anything you manually hid. Turn on to un-hide or manage them; off keeps the hub to real apps only.')}</label>
    <button class="btn-sm primary" style="margin-left:auto;" onclick="hlAddModal()" title="Add a manual (non-Docker) service tile - anything not running as a container here, e.g. a device UI or an app on another host. Stored locally in the Store DB.">&#10133; Add service</button>`;
}

async function loadHomelab() {
  const body = document.getElementById('hl-body');
  let data;
  try { data = await api('/api/homelab/services?include_hidden=' + (_hlIncludeHidden ? 1 : 0)); }
  catch (e) { body.innerHTML = `<div class="empty"><div class="empty-icon">&#128268;</div>${esc(e.message)}</div>`; return; }
  if (!data.groups.length) { body.innerHTML = '<div class="empty"><div class="empty-icon">&#128268;</div>No services.</div>'; return; }
  body.innerHTML = data.groups.map(g => `
    <div style="margin-bottom:20px;">
      <div style="font-size:.8rem;font-weight:700;color:var(--accent2);text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px;">${esc(g.category)} <span style="color:var(--muted);opacity:.6;">${g.services.length}</span></div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px;">
        ${g.services.map(hlCardHtml).join('')}
      </div>
    </div>`).join('') +
    (data.hidden_count && !_hlIncludeHidden ? `<div style="font-size:.72rem;color:var(--muted);">${data.hidden_count} helper/DB container(s) hidden. Toggle “show hidden” to manage.</div>` : '');
  // lazy-load *arr status
  data.groups.forEach(g => g.services.forEach(s => {
    if (s.arr_type && s.has_key) hlLoadArr(s.name);
  }));
}

function hlCardHtml(s) {
  const dot = s.source === 'manual' ? 'var(--muted)' : (s.running ? '#22c55e' : '#f87171');
  const opacity = s.hidden ? 'opacity:.55;' : '';
  return `<div class="card" style="padding:12px;${opacity}">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
      <span style="display:flex;align-items:center;gap:7px;min-width:0;">
        <span style="width:8px;height:8px;border-radius:50%;background:${dot};flex-shrink:0;"></span>
        <span style="font-weight:600;font-size:.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(s.display)}</span>
      </span>
      <span style="display:flex;gap:4px;flex-shrink:0;">
        ${s.source === 'docker' ? `<span title="edit" style="cursor:pointer;font-size:.7rem;color:var(--muted);" onclick='hlEditModal(${JSON.stringify(s).replace(/'/g,"&#39;")})'>&#9998;</span>` : ''}
        ${s.source === 'manual' ? `<span title="remove" style="cursor:pointer;font-size:.7rem;color:var(--muted);" onclick="hlDelManual(${s.id})">&#10005;</span>` : ''}
      </span>
    </div>
    <div style="font-size:.68rem;color:var(--muted);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
      ${s.source === 'docker' ? esc(s.image || '') : 'manual'}${s.source === 'docker' && !s.running ? ' · <span style="color:#f87171;">stopped</span>' : ''}
    </div>
    <div id="hl-arr-${cssId(s.name)}" style="font-size:.68rem;color:var(--muted);margin-top:3px;"></div>
    <div style="margin-top:8px;display:flex;gap:6px;align-items:center;">
      ${s.url ? `<a class="btn-sm primary" style="padding:3px 10px;" href="${esc(s.url)}" target="_blank" rel="noopener">Open &#8599;</a>`
        : `<span style="font-size:.66rem;color:var(--warn);">no URL — edit to add</span>`}
      ${s.hidden ? `<button class="btn-sm" style="padding:3px 8px;" onclick="hlSetHidden('${esc(s.name)}',false)">unhide</button>` : ''}
    </div>
  </div>`;
}
function cssId(s){ return String(s).replace(/[^a-z0-9]/gi,'_'); }

async function hlLoadArr(name) {
  const el = document.getElementById('hl-arr-' + cssId(name));
  if (!el) return;
  try {
    const a = await api('/api/homelab/arr/' + encodeURIComponent(name));
    if (!a.ok) { el.innerHTML = '<span style="color:#f87171;">API unreachable</span>'; return; }
    const warn = (a.warnings || []).length;
    el.innerHTML = `v${esc(a.version || '?')} · queue ${a.queue ?? '?'}` +
      (warn ? ` · <span style="color:var(--warn);" title="${esc((a.warnings||[]).join(' | '))}">&#9888;&#65039;${warn}</span>` : ' · <span style="color:#22c55e;">healthy</span>');
  } catch (e) { el.innerHTML = ''; }
}

async function hlSaveHost() {
  try { await api('/api/homelab/config', { method:'POST', body: JSON.stringify({ host: document.getElementById('hl-host').value.trim() }) });
    toast('Host saved'); loadHomelab();
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}
function hlToggleHidden(on) { _hlIncludeHidden = on; loadHomelab(); }
async function hlSetHidden(name, hidden) {
  try { await api('/api/homelab/override', { method:'POST', body: JSON.stringify({ container: name, hidden }) });
    toast(hidden ? 'Hidden' : 'Shown'); loadHomelab();
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}
async function hlDelManual(id) {
  if (!confirm('Remove this service?')) return;
  try { await api('/api/homelab/manual/' + id, { method:'DELETE' }); toast('Removed'); loadHomelab(); }
  catch (e) { toast('Failed: ' + e.message, 'error'); }
}

function hlEditModal(s) {
  const cats = ['Media','Media Management','Downloads','Files','Store','AI','Network','Tools','Other'];
  const arrTypes = ['','sonarr','radarr','lidarr','readarr','prowlarr'];
  const body = `
    <div class="field"><label>Display name</label><input type="text" id="hle-name" value="${esc(s.display)}"></div>
    <div class="field"><label>Category</label><select id="hle-cat">${cats.map(c=>`<option ${c===s.category?'selected':''}>${c}</option>`).join('')}</select></div>
    <div class="field"><label>URL override (blank = auto host:port) ${hlp('Force a specific link/status URL for this service instead of auto-deriving it from the Docker container’s host:port. Use when the service sits behind a proxy or on another host.')}</label><input type="text" id="hle-url" value="${esc(s.url||'')}" placeholder="http://host:port"></div>
    <div class="field"><label>*arr type (for status) ${hlp('Marks this as a Servarr app (Sonarr, Radarr, Prowlarr, etc.) so the dashboard can query its status API and show health/queue. Choose (none) for a plain link tile.')}</label><select id="hle-arr">${arrTypes.map(t=>`<option ${t===(s.arr_type||'')?'selected':''}>${t||'(none)'}</option>`).join('')}</select></div>
    <div class="field"><label>*arr API key ${hlp('The Servarr app’s API key (its Settings → General). Lets this dashboard read that app’s status. Stored locally; leave blank to keep the existing one.')}</label><input type="password" id="hle-key" placeholder="${s.has_key?'•••••• (set — leave blank to keep)':'paste API key'}"></div>
    <label style="font-size:.8rem;display:flex;gap:6px;align-items:center;cursor:pointer;"><input type="checkbox" id="hle-hide" ${s.hidden?'checked':''}> hide from the hub ${hlp('Keep this container off the main Services grid (it still runs - this only affects display). Find it again with the show hidden/helpers toggle up top.')}</label>`;
  hlModal('Edit ' + s.display, body, async () => {
    const arr = document.getElementById('hle-arr').value; const key = document.getElementById('hle-key').value;
    const payload = { container: s.name,
      display_name: document.getElementById('hle-name').value.trim(),
      category: document.getElementById('hle-cat').value,
      url_override: document.getElementById('hle-url').value.trim(),
      arr_type: arr === '(none)' ? '' : arr,
      hidden: document.getElementById('hle-hide').checked };
    if (key) payload.api_key = key;
    await api('/api/homelab/override', { method:'POST', body: JSON.stringify(payload) });
    toast('Saved'); loadHomelab();
  });
}
function hlAddModal() {
  const cats = ['Media','Media Management','Downloads','Files','Store','AI','Network','Tools','Other'];
  const body = `
    <div class="field"><label>Name * ${hlp('Label for this manual (non-Docker) service tile. Use it for things not discovered by docker ps - a NAS UI, a router, or an app on another box.')}</label><input type="text" id="hlm-name" placeholder="e.g. FTP (sftpgo)"></div>
    <div class="field"><label>URL ${hlp('The full link this tile opens - include http/https, host, and port. There is no auto host:port for manual entries, so set it here or the tile has no Open button.')}</label><input type="text" id="hlm-url" placeholder="http://127.0.0.1:8080"></div>
    <div class="field"><label>Category</label><select id="hlm-cat">${cats.map(c=>`<option>${c}</option>`).join('')}</select></div>`;
  hlModal('Add a service', body, async () => {
    await api('/api/homelab/manual', { method:'POST', body: JSON.stringify({
      name: document.getElementById('hlm-name').value.trim(),
      url: document.getElementById('hlm-url').value.trim(),
      category: document.getElementById('hlm-cat').value }) });
    toast('Added'); loadHomelab();
  });
}
function hlModal(title, bodyHtml, onSave) {
  let m = document.getElementById('hl-modal');
  if (!m) { m = document.createElement('div'); m.id='hl-modal'; m.className='modal'; document.body.appendChild(m); }
  m.innerHTML = `<div class="modal-content" style="max-width:420px;">
    <div style="font-weight:700;margin-bottom:12px;">${esc(title)}</div>${bodyHtml}
    <div style="display:flex;gap:8px;margin-top:14px;justify-content:flex-end;">
      <button class="btn-sm" onclick="document.getElementById('hl-modal').style.display='none'">Cancel</button>
      <button class="btn-sm primary" id="hl-modal-save">Save</button></div></div>`;
  m.style.display='flex';
  document.getElementById('hl-modal-save').onclick = async () => {
    try { await onSave(); m.style.display='none'; } catch(e){ toast('Failed: '+e.message,'error'); }
  };
}
window.loadHomelab=loadHomelab; window.hlSaveHost=hlSaveHost; window.hlToggleHidden=hlToggleHidden;
window.hlSetHidden=hlSetHidden; window.hlDelManual=hlDelManual; window.hlEditModal=hlEditModal;
window.hlAddModal=hlAddModal;

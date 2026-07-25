/* ══ SOCIAL TAB ══
   Draft & schedule posts for Instagram / TikTok / YouTube / Facebook.
   Phase 1: compose (LLM-assisted) → attach your generated media → queue/schedule →
   "copy caption, open the app, mark posted".
   Phase 2 (YouTube + TikTok + Instagram/Facebook live): Connect via OAuth popup
   then "Publish" on any video post — uploads in the background, status chip
   polls /api/social/posts/{id}/publications until live/failed. TikTok posts are
   SELF_ONLY (private to you) until the app passes TikTok's audit. One Meta
   connect covers Instagram Reels AND the linked Facebook Page — those posts go
   PUBLIC immediately (Meta's API has no privacy knob). */

let _soPlatforms = [];
let _soPosts = [];
let _soMedia = [];
let _soSel = null;          // {media_type, media_path, media_url, title, video_id?, chain_id?}
let _soMediaFilter = 'all'; // media picker filter: all | video | image
let _soEditId = null;
let _soFilter = 'all';
let _soYt = { has_client: false, connected: false, privacy: 'private' };
let _soTk = { has_client: false, connected: false, privacy: 'SELF_ONLY', audited: false };
let _soIg = { has_client: false, connected: false, fb_connected: false, ig_username: '' };
let _soPubs = {};           // post_id -> [publication rows]
let _soPubTimer = null;     // active publish-poll timer

async function renderSocial() {
  document.getElementById('main-content').innerHTML = `
    <div class="view-header">
      <div class="view-title">&#128241; Social</div>
      <div class="view-sub">Draft, caption &amp; schedule posts for Instagram, TikTok, YouTube &amp; Facebook.
        Attach your generated media, write a caption, then <b>Publish directly</b> to your connected platforms (YouTube &amp; TikTok) &mdash; or copy &amp; post to the rest.</div>
    </div>
    <div id="social-conn" style="margin-bottom:14px;"></div>
    <div id="social-compose" style="margin-bottom:18px;"></div>
    <div id="social-queue"></div>`;
  await loadSoPlatforms();
  renderSoConnections();
  renderSoCompose();
  await loadSoQueue();
}
window.renderSocial = renderSocial;

async function loadSoPlatforms() {
  try { const d = await api('/api/social/platforms'); _soPlatforms = d.platforms || []; }
  catch { _soPlatforms = []; }
  try { _soYt = await api('/api/social/youtube/status'); }
  catch { _soYt = { has_client: false, connected: false, privacy: 'private' }; }
  try { _soTk = await api('/api/social/tiktok/status'); }
  catch { _soTk = { has_client: false, connected: false, privacy: 'SELF_ONLY', audited: false }; }
  try { _soIg = await api('/api/social/instagram/status'); }
  catch { _soIg = { has_client: false, connected: false, fb_connected: false, ig_username: '' }; }
}

/* ── YouTube / TikTok / Meta OAuth (popup) ────────────────────────────────── */
window.addEventListener('message', async (e) => {
  if (e.data === 'youtube_connected' || e.data === 'tiktok_connected' || e.data === 'instagram_connected') {
    toast(e.data === 'youtube_connected' ? '✅ YouTube connected'
        : e.data === 'tiktok_connected' ? '✅ TikTok connected' : '✅ Meta (Instagram/Facebook) connected');
    await loadSoPlatforms();
    if (document.getElementById('social-conn')) { renderSoConnections(); await loadSoQueue(); }
  }
});
async function soConnectYouTube() {
  try {
    const { url } = await api('/api/social/youtube/connect');
    window.open(url, 'yt_oauth', 'width=620,height=760,noopener=no');
  } catch (e) { toast('Connect failed: ' + e.message, 'error'); }
}
async function soDisconnectYouTube() {
  if (!confirm('Disconnect YouTube? Stored tokens are deleted (uploads already live stay live).')) return;
  try {
    await api('/api/social/youtube/disconnect', { method: 'DELETE' });
    toast('YouTube disconnected');
    await loadSoPlatforms(); renderSoConnections(); await loadSoQueue();
  } catch (e) { toast('Disconnect failed: ' + e.message, 'error'); }
}
async function soConnectTikTok() {
  try {
    const { url } = await api('/api/social/tiktok/connect');
    window.open(url, 'tk_oauth', 'width=620,height=760,noopener=no');
  } catch (e) { toast('Connect failed: ' + e.message, 'error'); }
}
async function soDisconnectTikTok() {
  if (!confirm('Disconnect TikTok? Stored tokens are deleted (posts already live stay live).')) return;
  try {
    await api('/api/social/tiktok/disconnect', { method: 'DELETE' });
    toast('TikTok disconnected');
    await loadSoPlatforms(); renderSoConnections(); await loadSoQueue();
  } catch (e) { toast('Disconnect failed: ' + e.message, 'error'); }
}
async function soConnectInstagram() {
  try {
    const { url } = await api('/api/social/instagram/connect');
    window.open(url, 'meta_oauth', 'width=620,height=760,noopener=no');
  } catch (e) { toast('Connect failed: ' + e.message, 'error'); }
}
async function soDisconnectInstagram() {
  if (!confirm('Disconnect Meta (Instagram + Facebook Page)? Stored tokens are deleted (posts already live stay live).')) return;
  try {
    await api('/api/social/instagram/disconnect', { method: 'DELETE' });
    toast('Meta disconnected');
    await loadSoPlatforms(); renderSoConnections(); await loadSoQueue();
  } catch (e) { toast('Disconnect failed: ' + e.message, 'error'); }
}
function _plat(key) { return _soPlatforms.find(p => p.key === key) || { key, name: key, icon: '', upload_url: '#' }; }

/* ── platform connections (handles + Phase-2 auto-post status) ────────────── */
function renderSoConnections() {
  const el = document.getElementById('social-conn');
  el.innerHTML = `
    <details class="settings-group">
      <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#128279; Platform connections &amp; auto-post status</summary>
      <div style="font-size:.72rem;color:var(--muted);margin:8px 0 12px;">
        Save your @handle for each platform. <b>Auto-posting</b> needs each platform's API app
        &mdash; status shown below; for now the queue is copy-and-post.
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;">
        ${_soPlatforms.map(p => `
          <div class="card" style="padding:12px 14px;">
            <div style="font-weight:600;font-size:.9rem;margin-bottom:6px;">${p.icon} ${esc(p.name)}</div>
            <div class="field" style="margin-bottom:6px;">
              <label style="font-size:.66rem;">Your handle ${hlp('Your @username on this platform. Saved to app settings (/api/social/config) and used to label your posts. Auto-posting is not live yet, so this is informational for now.')}</label>
              <input type="text" id="so-handle-${p.key}" value="${esc(p.handle || '')}" placeholder="@yourshop">
            </div>
            <div style="font-size:.64rem;color:var(--muted);margin-bottom:8px;line-height:1.5;">
              <span style="color:var(--warn);">&#9881;&#65039; auto-post: ${esc(p.auto)}</span><br>${esc(p.api)}
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
              <button class="btn-sm" onclick="soSaveHandle('${p.key}')">&#128190; Save</button>
              ${p.key === 'youtube' ? (
                _soYt.connected
                  ? `<span style="font-size:.7rem;color:#22c55e;align-self:center;" title="OAuth tokens stored — Publish to YouTube is available on video posts. New uploads default to '${esc(_soYt.privacy)}' privacy (setting social_youtube_privacy).">&#9679; Connected</span>
                     <button class="btn-sm" onclick="soDisconnectYouTube()" title="Delete the stored YouTube OAuth tokens.">Disconnect</button>`
                  : (_soYt.has_client
                      ? `<button class="btn-sm primary" onclick="soConnectYouTube()" title="Open Google's consent screen in a popup and authorize video uploads to your channel.">&#128279; Connect YouTube</button>`
                      : `<span style="font-size:.66rem;color:var(--muted);align-self:center;">Set <b>yt_client_id</b> + <b>yt_client_secret</b> in Settings to enable Connect</span>`)
              ) : ''}
              ${p.key === 'tiktok' ? (
                _soTk.connected
                  ? `<span style="font-size:.7rem;color:#22c55e;align-self:center;" title="OAuth tokens stored — Publish to TikTok is available on video posts. Posts upload as '${esc(_soTk.privacy)}'${_soTk.audited ? " (setting social_tiktok_privacy)" : " — private to you until the app passes TikTok's audit (then set tiktok_audited=1)"}.">&#9679; Connected</span>
                     <button class="btn-sm" onclick="soDisconnectTikTok()" title="Delete the stored TikTok OAuth tokens.">Disconnect</button>`
                  : (_soTk.has_client
                      ? `<button class="btn-sm primary" onclick="soConnectTikTok()" title="Open TikTok's consent screen in a popup and authorize video posting to your account.">&#128279; Connect TikTok</button>`
                      : `<span style="font-size:.66rem;color:var(--muted);align-self:center;">Set <b>tiktok_client_key</b> + <b>tiktok_client_secret</b> in Settings to enable Connect</span>`)
              ) : ''}
              ${p.key === 'instagram' ? (
                _soIg.connected
                  ? `<span style="font-size:.7rem;color:#22c55e;align-self:center;" title="Meta token stored${_soIg.ig_username ? ' for @' + esc(_soIg.ig_username) : ''} — Publish to Instagram is available on video posts. Reels published via the API are PUBLIC immediately.">&#9679; Connected${_soIg.ig_username ? ' @' + esc(_soIg.ig_username) : ''}</span>
                     <button class="btn-sm" onclick="soDisconnectInstagram()" title="Delete the stored Meta OAuth tokens (also disconnects the Facebook Page).">Disconnect</button>`
                  : (_soIg.has_client
                      ? `<button class="btn-sm primary" onclick="soConnectInstagram()" title="Open Meta's consent screen in a popup and authorize publishing to your IG Business account + linked Facebook Page (one connect covers both).">&#128279; Connect Instagram</button>`
                      : `<span style="font-size:.66rem;color:var(--muted);align-self:center;">Set <b>meta_app_id</b> + <b>meta_app_secret</b> in Settings to enable Connect (or paste <b>meta_access_token</b> + <b>ig_business_id</b> directly)</span>`)
              ) : ''}
              ${p.key === 'facebook' ? (
                _soIg.fb_connected
                  ? `<span style="font-size:.7rem;color:#22c55e;align-self:center;" title="Page token stored — Publish to your Facebook Page is available on video posts. Page videos published via the API are PUBLIC immediately.">&#9679; Connected (via Meta)</span>`
                  : `<span style="font-size:.66rem;color:var(--muted);align-self:center;">Connects together with Instagram (one Meta login covers both)</span>`
              ) : ''}
            </div>
          </div>`).join('')}
      </div>
    </details>`;
}
async function soSaveHandle(key) {
  const handle = document.getElementById(`so-handle-${key}`).value.trim();
  try { await api('/api/social/config', { method: 'POST', body: JSON.stringify({ platform: key, handle }) });
    toast('✅ Saved'); } catch (e) { toast('Save failed: ' + e.message, 'error'); }
}

/* ── compose ──────────────────────────────────────────────────────────────── */
function renderSoCompose() {
  const el = document.getElementById('social-compose');
  const platBtns = _soPlatforms.map(p => `
    <label class="so-plat" title="Include ${esc(p.name)} in this post. Auto-post status: ${esc(p.auto)} — for now the queue is copy-and-post, not automatic. Caption limit ${p.caption_limit} chars." style="display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border:1px solid var(--border,#3334);
      border-radius:20px;cursor:pointer;font-size:.8rem;">
      <input type="checkbox" class="so-plat-chk" value="${p.key}" style="cursor:pointer;"> ${p.icon} ${esc(p.name)}</label>`).join('');
  el.innerHTML = `
    <div class="card" style="padding:16px 18px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
        <div style="font-weight:700;font-size:1rem;" id="so-compose-title">&#9998; New post</div>
        <button class="btn-sm" onclick="soResetCompose()" id="so-clear-btn" style="display:none;">Clear / new</button>
      </div>
      <input type="hidden" id="so-id" value="">
      <div style="margin-bottom:10px;display:flex;gap:8px;flex-wrap:wrap;" id="so-plats">${platBtns}</div>
      <div class="field" style="margin-bottom:10px;">
        <label style="display:flex;justify-content:space-between;">Caption ${hlp('The post text. The counter warns if it passes the tightest character limit among the platforms you selected. Saved with the post and copied to your clipboard when you post.')}
          <span style="font-weight:400;color:var(--muted);" id="so-charcount"></span></label>
        <textarea id="so-caption" rows="4" placeholder="Write your caption… or generate one ✨"
          oninput="soUpdateCharcount()"></textarea>
      </div>
      <div style="display:flex;gap:8px;margin:-4px 0 12px;flex-wrap:wrap;align-items:center;">
        <input type="text" id="so-gen-topic" placeholder="Topic for AI caption, e.g. new Galactic Couch Potato tee"
          title="What the post is about. Fed to the local LM Studio model to draft a caption plus hashtags tailored to the selected platforms. Does not post anything."
          style="flex:1;min-width:220px;">
        <button class="btn-sm" onclick="soGenerate()" id="so-gen-btn" title="Ask the local LLM to write a caption and hashtags for the topic above, tuned to the platforms you picked. Fills the Caption and Hashtags fields for you to edit — nothing is posted.">&#10024; Generate caption</button>
      </div>
      <div class="field" style="margin-bottom:12px;">
        <label>Hashtags ${hlp('Space-separated tags appended after the caption when you copy or post. Generated with the caption, or type your own.')}</label>
        <input type="text" id="so-hashtags" placeholder="#yourshop #geekstyle #3dprinting">
      </div>
      <div style="margin-bottom:12px;">
        <label style="font-size:.72rem;color:var(--muted);">Media ${hlp('The image or video that goes with this post. Pick one of your generated files (from the Image/Video tabs) or paste a URL. It rides along in the queue so you have it ready when you copy and post.')}</label>
        <div id="so-media-preview" style="margin:6px 0;"></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button class="btn-sm" onclick="soToggleMediaPicker()" title="Browse images and videos you already generated in the Image/Video tabs and attach one to this post.">&#128247; Attach generated media</button>
          <input type="text" id="so-media-url" placeholder="…or paste a media URL" style="flex:1;min-width:200px;"
            title="Paste a direct link to an image or video (.mp4/.webm/.mov become video) to attach instead of a generated file."
            oninput="soMediaFromUrl(this.value)">
        </div>
        <div id="so-media-picker" style="display:none;margin-top:10px;"></div>
      </div>
      <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;">
        <div class="field" style="margin:0;">
          <label style="font-size:.72rem;">Schedule (optional) ${hlp('Pick a date and time to mark the post as scheduled instead of a draft. This is a reminder only — it does NOT auto-post yet; you still copy the caption and post manually when the time comes.')}</label>
          <input type="datetime-local" id="so-sched">
        </div>
        <button class="btn-sm primary" onclick="soSave()">&#128190; Save to queue</button>
      </div>
    </div>`;
  soUpdateCharcount();
}

function soUpdateCharcount() {
  const cap = (document.getElementById('so-caption')?.value || '');
  const tags = (document.getElementById('so-hashtags')?.value || '');
  const total = cap.length + (tags ? tags.length + 2 : 0);
  const sel = [...document.querySelectorAll('.so-plat-chk:checked')].map(c => c.value);
  const limits = sel.map(k => _plat(k)).filter(p => p.caption_limit);
  const min = limits.length ? Math.min(...limits.map(p => p.caption_limit)) : null;
  const el = document.getElementById('so-charcount');
  if (!el) return;
  if (min && total > min) el.innerHTML = `<span style="color:var(--warn);">${total} / ${min} — too long for ${limits.find(p=>p.caption_limit===min).name}</span>`;
  else el.textContent = `${total}${min ? ' / ' + min : ''} chars`;
}
document.addEventListener('change', e => { if (e.target.classList?.contains('so-plat-chk')) soUpdateCharcount(); });

/* media picker */
async function soToggleMediaPicker() {
  const box = document.getElementById('so-media-picker');
  if (box.style.display === 'block') { box.style.display = 'none'; return; }
  box.style.display = 'block';
  box.innerHTML = '<div class="loading-state">Loading media…</div>';
  if (!_soMedia.length) { try { const d = await api('/api/social/media'); _soMedia = d.media || []; } catch {} }
  if (!_soMedia.length) { box.innerHTML = '<div style="color:var(--muted);font-size:.8rem;">No generated media yet — make some in Image/Video tabs.</div>'; return; }
  _soRenderMediaPicker();
}
function _soRenderMediaPicker() {
  const box = document.getElementById('so-media-picker');
  if (!box) return;
  const nVid = _soMedia.filter(m => m.type === 'video').length;
  const nImg = _soMedia.length - nVid;
  const items = _soMedia.filter(m => _soMediaFilter === 'all' || m.type === _soMediaFilter);
  const chip = (k, label) => `<button class="btn-sm${_soMediaFilter === k ? ' primary' : ''}" onclick="_soMediaFilter='${k}';_soRenderMediaPicker()">${label}</button>`;
  box.innerHTML = `
    <div style="display:flex;gap:6px;margin-bottom:8px;align-items:center;flex-wrap:wrap;">
      <span style="font-size:.72rem;color:var(--muted);">Show:</span>
      ${chip('all', 'All (' + _soMedia.length + ')')}
      ${chip('video', '&#127916; Videos (' + nVid + ')')}
      ${chip('image', '&#128444; Images (' + nImg + ')')}
      <span style="font-size:.66rem;color:var(--warn);margin-left:4px;">TikTok &amp; YouTube need a &#127916; video.</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:8px;max-height:280px;overflow:auto;padding:4px;border:1px solid var(--border,#3334);border-radius:10px;">
      ${items.length ? items.map(m => {
        const i = _soMedia.indexOf(m);
        const isVid = m.type === 'video';
        const badge = isVid
          ? '<span style="position:absolute;top:3px;left:3px;background:rgba(34,197,94,.92);color:#fff;font-size:.58rem;font-weight:700;padding:1px 5px;border-radius:4px;">&#127916; VIDEO</span>'
          : '<span style="position:absolute;top:3px;left:3px;background:rgba(100,116,139,.92);color:#fff;font-size:.58rem;font-weight:700;padding:1px 5px;border-radius:4px;">&#128444; IMAGE</span>';
        const media = isVid
          ? `<video src="${API + m.url}" muted style="width:100%;height:90px;object-fit:cover;"></video>`
          : `<img src="${thumbUrl(m.local_path)}" loading="lazy" decoding="async" style="width:100%;height:90px;object-fit:cover;" onerror="this.onerror=null;this.src='${API + m.url}'">`;
        return `<div onclick="soPickMedia(${i})" title="${esc(m.title)}" style="cursor:pointer;position:relative;border-radius:8px;overflow:hidden;border:2px solid transparent;">${media}${badge}</div>`;
      }).join('') : `<div style="color:var(--muted);font-size:.8rem;padding:12px;grid-column:1/-1;">No ${_soMediaFilter} files yet — generate some in the Video/Image tabs.</div>`}
    </div>`;
}
window._soRenderMediaPicker = _soRenderMediaPicker;
function soPickMedia(i) {
  const m = _soMedia[i]; if (!m) return;
  _soSel = { media_type: m.type, media_path: m.local_path, media_url: m.url, title: m.title,
             video_id: m.video_id || null, chain_id: m.chain_id || null };
  document.getElementById('so-media-url').value = '';
  document.getElementById('so-media-picker').style.display = 'none';
  soRenderMediaPreview();
}
function soMediaFromUrl(url) {
  url = (url || '').trim();
  if (!url) { if (_soSel && !_soSel.media_path) { _soSel = null; soRenderMediaPreview(); } return; }
  const isVid = /\.(mp4|webm|mov)(\?|$)/i.test(url);
  _soSel = { media_type: isVid ? 'video' : 'image', media_path: '', media_url: url, title: '' };
  soRenderMediaPreview();
}
function soRenderMediaPreview() {
  const el = document.getElementById('so-media-preview');
  if (!_soSel) { el.innerHTML = ''; return; }
  const src = _soSel.media_url && _soSel.media_url.startsWith('/') ? API + _soSel.media_url : esc(_soSel.media_url);
  el.innerHTML = `<div style="display:inline-flex;align-items:center;gap:10px;background:var(--bg2);padding:8px;border-radius:10px;">
    ${_soSel.media_type === 'video'
      ? `<video src="${src}" muted style="width:80px;height:80px;object-fit:cover;border-radius:6px;"></video>`
      : `<img src="${src}" loading="lazy" decoding="async" style="width:80px;height:80px;object-fit:cover;border-radius:6px;">`}
    <span style="font-size:.74rem;color:var(--muted);max-width:220px;">${esc(_soSel.title || _soSel.media_type)}</span>
    <button class="btn-sm" onclick="soClearMedia()">&#10005;</button></div>`;
}
function soClearMedia() { _soSel = null; document.getElementById('so-media-url').value = ''; soRenderMediaPreview(); }

async function soGenerate() {
  const topic = document.getElementById('so-gen-topic').value.trim();
  if (!topic) { toast('Enter a topic for the AI caption', 'error'); return; }
  const platforms = [...document.querySelectorAll('.so-plat-chk:checked')].map(c => c.value);
  const btn = document.getElementById('so-gen-btn'); const orig = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '⏳ Writing…';
  try {
    const { task_id } = await api('/api/social/generate', { method: 'POST', body: JSON.stringify({ topic, platforms }) });
    const r = await pollTask(task_id, 60);
    if (r && r.caption) document.getElementById('so-caption').value = r.caption;
    if (r && r.hashtags) document.getElementById('so-hashtags').value = r.hashtags;
    soUpdateCharcount();
    toast('✨ Caption drafted — tweak & save');
  } catch (e) { toast('Generate failed: ' + e.message, 'error'); }
  btn.disabled = false; btn.innerHTML = orig;
}

async function soSave() {
  const platforms = [...document.querySelectorAll('.so-plat-chk:checked')].map(c => c.value);
  const caption = document.getElementById('so-caption').value.trim();
  const hashtags = document.getElementById('so-hashtags').value.trim();
  const sched = document.getElementById('so-sched').value;
  if (!caption && !_soSel) { toast('Add a caption or attach media', 'error'); return; }
  if (!platforms.length) { toast('Pick at least one platform', 'error'); return; }
  const payload = {
    caption, hashtags, platforms,
    media_type: _soSel ? _soSel.media_type : 'none',
    media_path: _soSel ? (_soSel.media_path || '') : '',
    media_url: _soSel ? (_soSel.media_url || '') : '',
    video_id: _soSel ? (_soSel.video_id || null) : null,
    chain_id: _soSel ? (_soSel.chain_id || null) : null,
    scheduled_at: sched || null,
    status: sched ? 'scheduled' : 'draft',
  };
  try {
    if (_soEditId) await api(`/api/social/posts/${_soEditId}`, { method: 'PATCH', body: JSON.stringify(payload) });
    else await api('/api/social/posts', { method: 'POST', body: JSON.stringify(payload) });
    toast(_soEditId ? '✅ Updated' : (sched ? '✅ Scheduled' : '✅ Saved to drafts'));
    soResetCompose();
    await loadSoQueue();
  } catch (e) { toast('Save failed: ' + e.message, 'error'); }
}

function soResetCompose() {
  _soEditId = null; _soSel = null;
  document.getElementById('so-compose-title').innerHTML = '&#9998; New post';
  document.getElementById('so-clear-btn').style.display = 'none';
  ['so-caption', 'so-hashtags', 'so-sched', 'so-gen-topic', 'so-media-url'].forEach(id => { const e = document.getElementById(id); if (e) e.value = ''; });
  document.querySelectorAll('.so-plat-chk').forEach(c => c.checked = false);
  soRenderMediaPreview(); soUpdateCharcount();
}

/* ── queue ────────────────────────────────────────────────────────────────── */
async function loadSoQueue() {
  const el = document.getElementById('social-queue');
  let data;
  try { data = await api('/api/social/posts'); } catch (e) { el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  _soPosts = data.posts || [];
  const c = data.counts || {};
  const badge = document.getElementById('badge-social'); if (badge) badge.textContent = (c.draft || 0) + (c.scheduled || 0);
  const tabs = [['all', 'All', _soPosts.length], ['draft', 'Drafts', c.draft || 0],
                ['scheduled', 'Scheduled', c.scheduled || 0], ['posted', 'Posted', c.posted || 0]];
  const shown = _soFilter === 'all' ? _soPosts : _soPosts.filter(p => p.status === _soFilter);
  el.innerHTML = `
    <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;">
      ${tabs.map(([k, l, n]) => `<button class="btn-sm ${k === _soFilter ? 'primary' : ''}" onclick="soSetFilter('${k}')">${l} (${n})</button>`).join('')}
    </div>
    ${shown.length ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;">${shown.map(soCardHtml).join('')}</div>`
      : `<div class="empty"><div class="empty-icon">&#128241;</div>No ${_soFilter === 'all' ? '' : _soFilter} posts yet — compose one above.</div>`}`;
  // fetch publish status for the video posts on screen (small N — the queue is short)
  shown.filter(p => p.media_type === 'video').forEach(p => soLoadPubs(p.id));
}

/* ── auto-publish (YouTube + TikTok) ──────────────────────────────────────── */
async function soLoadPubs(pid) {
  try {
    const d = await api(`/api/social/posts/${pid}/publications`);
    _soPubs[pid] = d.publications || [];
  } catch { _soPubs[pid] = _soPubs[pid] || []; }
  soRenderPubChips(pid);
}
function soRenderPubChips(pid) {
  const el = document.getElementById(`so-pubs-${pid}`);
  if (!el) return;
  const pubs = _soPubs[pid] || [];
  if (!pubs.length) { el.innerHTML = ''; return; }
  el.innerHTML = pubs.map(pub => {
    const s = pub.status;
    if (s === 'live') return `<a href="${esc(pub.platform_url || '#')}" target="_blank" rel="noopener"
      style="font-size:.68rem;color:#22c55e;text-decoration:none;" title="Uploaded — open on ${esc(pub.platform)}">&#9654; ${esc(pub.platform)}: live &#8599;</a>`;
    if (s === 'failed') return `<span style="font-size:.68rem;color:var(--warn,#f87171);" title="${esc(pub.error || 'upload failed')}">&#9888; ${esc(pub.platform)}: failed</span>`;
    return `<span style="font-size:.68rem;color:var(--accent2);">&#8987; ${esc(pub.platform)}: ${esc(s)}…</span>`;
  }).join(' ');
}
function _soPubInfo(plat) {
  if (plat === 'youtube') return { label: 'YouTube', connected: _soYt.connected,
    msg: `Upload this post's video to YouTube now? It uploads as "${_soYt.privacy}" (change via the social_youtube_privacy setting).` };
  if (plat === 'tiktok') return { label: 'TikTok', connected: _soTk.connected,
    msg: `Upload this post's video to TikTok now? It posts as "${_soTk.privacy}"${_soTk.audited
      ? ' (change via the social_tiktok_privacy setting)'
      : " — private to you, because the app hasn't passed TikTok's audit yet (public posting unlocks after approval; then set tiktok_audited=1)"}.` };
  if (plat === 'instagram') return { label: 'Instagram', connected: _soIg.connected,
    msg: `Publish this post's video as an Instagram Reel now${_soIg.ig_username ? ' on @' + _soIg.ig_username : ''}? It goes PUBLIC immediately — Meta's API has no privacy setting.` };
  if (plat === 'facebook') return { label: 'Facebook', connected: _soIg.fb_connected,
    msg: `Post this video to your Facebook Page now? It goes PUBLIC immediately — Meta's API has no privacy setting.` };
  return { label: plat, connected: false, msg: `Publish to ${plat}?` };
}
async function soPublish(pid, plat) {
  const { label, connected, msg } = _soPubInfo(plat);
  if (!connected) { toast(`Connect ${label} first (Platform connections above)`, 'error'); return; }
  if (!confirm(msg)) return;
  try {
    const r = await api(`/api/social/posts/${pid}/publish`, { method: 'POST', body: JSON.stringify({ platforms: [plat] }) });
    if (r.already_live) { toast(`Already live on ${label}`); await soLoadPubs(pid); return; }
    toast(`⬆️ Uploading to ${label}…`);
    soPollPubs(pid);
  } catch (e) { toast('Publish failed: ' + e.message, 'error'); }
}

function soPublishYt(pid) { return soPublish(pid, 'youtube'); }
function soPollPubs(pid, tries = 0) {
  if (_soPubTimer) clearTimeout(_soPubTimer);
  _soPubTimer = setTimeout(async () => {
    await soLoadPubs(pid);
    const pubs = _soPubs[pid] || [];
    const busy = pubs.some(p => p.status === 'pending' || p.status === 'uploading' || p.status === 'processing');
    if (busy && tries < 60) { soPollPubs(pid, tries + 1); return; }
    const live = pubs.find(p => p.status === 'live');
    if (live) { toast(`✅ Live on ${live.platform}` + (live.platform_url ? ': ' + live.platform_url : '')); await loadSoQueue(); }
    else if (pubs.some(p => p.status === 'failed')) toast('Upload failed — see the status chip', 'error');
  }, tries === 0 ? 1200 : 3000);
}
function soSetFilter(f) { _soFilter = f; loadSoQueue(); }

function soCardHtml(p) {
  const media = p.media_url ? (p.media_url.startsWith('/') ? API + p.media_url : esc(p.media_url)) : '';
  const thumb = media
    ? (p.media_type === 'video'
        ? `<video src="${media}" muted style="width:100%;height:150px;object-fit:cover;border-radius:8px 8px 0 0;"></video>`
        : p.media_path
          ? `<img src="${thumbUrl(p.media_path)}" loading="lazy" decoding="async" style="width:100%;height:150px;object-fit:cover;border-radius:8px 8px 0 0;" onerror="this.onerror=null;this.src='${media}'">`
          : `<img src="${media}" loading="lazy" style="width:100%;height:150px;object-fit:cover;border-radius:8px 8px 0 0;">`)
    : `<div style="height:60px;"></div>`;
  const chips = (p.platforms || []).map(k => {
    const pl = _plat(k); const done = (p.posted_on || []).includes(k);
    return `<span title="${esc(pl.name)}${done ? ' — posted' : ''}" style="font-size:.9rem;opacity:${done ? '0.4' : '1'};">${pl.icon}</span>`;
  }).join(' ');
  const openBtns = (p.platforms || []).map(k => {
    const pl = _plat(k);
    return `<button class="btn-sm" onclick="soOpenPost(${p.id},'${k}')" title="Copy caption &amp; open ${esc(pl.name)}">${pl.icon} ${esc(pl.name)} &#8599;</button>`;
  }).join('');
  const when = p.status === 'scheduled' && p.scheduled_at
    ? `<span style="color:var(--accent2);">&#128197; ${esc(p.scheduled_at.replace('T', ' '))}</span>`
    : p.status === 'posted' ? `<span style="color:#22c55e;">&#10003; posted${p.posted_at ? ' ' + esc(p.posted_at.replace('T', ' ')) : ''}</span>`
    : `<span style="color:var(--muted);">draft</span>`;
  return `
    <div class="card" style="padding:0;overflow:hidden;display:flex;flex-direction:column;">
      ${thumb}
      <div style="padding:12px 14px;flex:1;display:flex;flex-direction:column;gap:8px;">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <div style="display:flex;gap:5px;">${chips}</div>${when}
        </div>
        <div style="font-size:.82rem;line-height:1.4;white-space:pre-wrap;max-height:96px;overflow:auto;">${esc(p.caption || '')}</div>
        ${p.hashtags ? `<div style="font-size:.74rem;color:var(--accent2);">${esc(p.hashtags)}</div>` : ''}
        <div id="so-pubs-${p.id}" style="display:flex;gap:8px;flex-wrap:wrap;"></div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:auto;">${openBtns}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;border-top:1px solid var(--border,#3333);padding-top:8px;">
          ${p.media_type === 'video' && _soYt.connected ? `<button class="btn-sm primary" onclick="soPublish(${p.id},'youtube')" title="Actually upload this post's video to your YouTube channel via the API (default privacy: ${esc(_soYt.privacy)}).">&#9654; Publish to YouTube</button>` : ''}
          ${p.media_type === 'video' && _soTk.connected ? `<button class="btn-sm primary" onclick="soPublish(${p.id},'tiktok')" title="Actually post this video to your TikTok account via the API (privacy: ${esc(_soTk.privacy)}${_soTk.audited ? '' : ' — private until TikTok audits the app'}).">&#127925; Publish to TikTok</button>` : ''}
          ${p.media_type === 'video' && _soIg.connected ? `<button class="btn-sm primary" onclick="soPublish(${p.id},'instagram')" title="Actually publish this video as a Reel on your Instagram Business account${_soIg.ig_username ? ' (@' + esc(_soIg.ig_username) + ')' : ''} via the Meta API. Reels go PUBLIC immediately.">&#128248; Publish to Instagram</button>` : ''}
          ${p.media_type === 'video' && _soIg.fb_connected ? `<button class="btn-sm primary" onclick="soPublish(${p.id},'facebook')" title="Actually post this video to your Facebook Page via the Meta API. Page videos go PUBLIC immediately.">&#127760; Publish to Facebook</button>` : ''}
          <button class="btn-sm" onclick="soCopy(${p.id})" title="Copy the caption and hashtags to your clipboard, ready to paste into the platform app.">&#128203; Copy</button>
          <button class="btn-sm" onclick="soEdit(${p.id})">&#9998; Edit</button>
          ${p.status !== 'posted' ? `<button class="btn-sm" onclick="soMarkPosted(${p.id})" title="Mark this post as posted (moves it to the Posted tab). It does NOT post for you — use it after you have posted it yourself.">&#10003; Mark posted</button>` : ''}
          <button class="btn-sm" onclick="soDelete(${p.id})" style="margin-left:auto;" title="Delete this post from the queue.">&#128465;&#65039;</button>
        </div>
      </div>
    </div>`;
}

function _soText(p) { return (p.caption || '') + (p.hashtags ? '\n\n' + p.hashtags : ''); }
async function soCopy(id) {
  const p = _soPosts.find(x => x.id === id); if (!p) return;
  try { await navigator.clipboard.writeText(_soText(p)); toast('📋 Caption copied'); }
  catch { toast('Copy failed — select the text manually', 'error'); }
}
async function soOpenPost(id, key) {
  const p = _soPosts.find(x => x.id === id); if (!p) return;
  try { await navigator.clipboard.writeText(_soText(p)); toast('📋 Caption copied — paste in ' + _plat(key).name); } catch {}
  window.open(_plat(key).upload_url, '_blank', 'noopener');
}
async function soMarkPosted(id) {
  try { await api(`/api/social/posts/${id}/mark-posted`, { method: 'POST', body: JSON.stringify({}) });
    toast('✅ Marked posted'); await loadSoQueue(); } catch (e) { toast('Failed: ' + e.message, 'error'); }
}
async function soDelete(id) {
  if (!confirm('Delete this post?')) return;
  try { await api(`/api/social/posts/${id}`, { method: 'DELETE' }); toast('Deleted'); await loadSoQueue(); }
  catch (e) { toast('Delete failed: ' + e.message, 'error'); }
}
function soEdit(id) {
  const p = _soPosts.find(x => x.id === id); if (!p) return;
  _soEditId = id;
  document.getElementById('so-id').value = id;
  document.getElementById('so-compose-title').innerHTML = '&#9998; Editing post';
  document.getElementById('so-clear-btn').style.display = '';
  document.getElementById('so-caption').value = p.caption || '';
  document.getElementById('so-hashtags').value = p.hashtags || '';
  document.getElementById('so-sched').value = p.scheduled_at || '';
  document.querySelectorAll('.so-plat-chk').forEach(c => c.checked = (p.platforms || []).includes(c.value));
  _soSel = (p.media_url || p.media_path) ? { media_type: p.media_type, media_path: p.media_path, media_url: p.media_url, title: '', video_id: p.video_id || null, chain_id: p.chain_id || null } : null;
  soRenderMediaPreview(); soUpdateCharcount();
  document.getElementById('social-compose').scrollIntoView({ behavior: 'smooth' });
}

window.soSaveHandle = soSaveHandle;
window.soToggleMediaPicker = soToggleMediaPicker;
window.soPickMedia = soPickMedia;
window.soMediaFromUrl = soMediaFromUrl;
window.soClearMedia = soClearMedia;
window.soGenerate = soGenerate;
window.soSave = soSave;
window.soResetCompose = soResetCompose;
window.soUpdateCharcount = soUpdateCharcount;
window.soSetFilter = soSetFilter;
window.soCopy = soCopy;
window.soOpenPost = soOpenPost;
window.soMarkPosted = soMarkPosted;
window.soDelete = soDelete;
window.soEdit = soEdit;
window.soConnectYouTube = soConnectYouTube;
window.soDisconnectYouTube = soDisconnectYouTube;
window.soConnectTikTok = soConnectTikTok;
window.soDisconnectTikTok = soDisconnectTikTok;
window.soConnectInstagram = soConnectInstagram;
window.soDisconnectInstagram = soDisconnectInstagram;
window.soPublish = soPublish;
window.soPublishYt = soPublishYt;
window.soLoadPubs = soLoadPubs;

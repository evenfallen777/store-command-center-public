'use strict';

const API = '/store';

let _currentView = 'dashboard';
let _etsySubTab  = 'dashboard'; // active subtab inside Etsy/Printify view (lands on the Dashboard)
let _settings = {};
let _publishDesignId = null;
let _publishPrefillTitle = '', _publishPrefillDesc = '', _publishPrefillTags = '';
let _regenDesignId   = null;
let _approveProposalId   = null;
let _approveProposalCard = null;
let _etsyDesignId = null;

const PRODUCT_TYPES = [
  'T-Shirt','Hoodie','Sweatshirt','Tank Top','Mug','Tumbler',
  'Poster','Sticker','Tote Bag','Phone Case','Mouse Pad','Pillow',
  "Men's Underwear","Women's Underwear","Men's Swim Trunks","Women's Swimsuit",
  'Bumper Sticker','Hat','Beanie','Socks'
];
let _productTypes = [...PRODUCT_TYPES]; // live list (includes custom types from server)
let _customProductTypes = [];           // custom-only list

/* ── API HELPER ── */
async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      // FastAPI errors arrive as {detail: "..."} (or a list for 422 validation)
      const d = j.detail;
      msg = (typeof d === 'string' && d)
         || (Array.isArray(d) && d.map(x => x.msg || JSON.stringify(x)).join('; '))
         || j.error || j.message || msg;
    } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
}

/* ── HELP TOOLTIP ── returns a circled "?" that shows `text` on hover or click.
   Use in any template: `<label>Foo ${hlp('what foo does & affects')}</label>` */
function hlp(text) {
  return `<span class="help" tabindex="0" onclick="toggleHelp(this,event)">?<span class="tip">${esc(text)}</span></span>`;
}
function toggleHelp(el, ev) {
  if (ev) ev.stopPropagation();
  const wasOpen = el.classList.contains('show');
  document.querySelectorAll('.help.show').forEach(h => h.classList.remove('show'));
  if (!wasOpen) el.classList.add('show');
}
// Any outside click (or Escape) closes an open tooltip.
document.addEventListener('click', () => document.querySelectorAll('.help.show').forEach(h => h.classList.remove('show')));
document.addEventListener('keydown', e => { if (e.key === 'Escape') document.querySelectorAll('.help.show').forEach(h => h.classList.remove('show')); });

/* ── TOAST ── */
function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

/* ── MODALS ── */
function openModal(id)  { document.getElementById(id).style.display = 'flex'; }
function closeModal(id) { document.getElementById(id).style.display = 'none'; }
document.querySelectorAll('.modal').forEach(m => {
  m.addEventListener('click', e => { if (e.target === m) m.style.display = 'none'; });
});

/* ── LIGHTBOX ── */
function openLightbox(src, caption) {
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox-caption').textContent = caption || '';
  document.getElementById('lightbox').classList.add('open');
}
function closeLightbox() { document.getElementById('lightbox').classList.remove('open'); }
document.getElementById('lightbox').addEventListener('click', e => {
  if (e.target === document.getElementById('lightbox')) closeLightbox();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeLightbox();
    document.querySelectorAll('.modal').forEach(m => m.style.display = 'none');
  }
});

/* ── IMAGE PATH → URL ── */
function imgUrl(path) {
  if (!path) return null;
  const parts = path.split('/');
  const filename = parts.pop();
  const subfolder = parts.pop(); // 'pending', 'approved', 'rejected'
  return `${API}/designs/${subfolder}/${filename}`;
}
// Small (<=400px) WebP thumbnail for gallery grids — generated on demand + cached.
// Use for the grid <img>; keep imgUrl() (full-res) for the lightbox.
function thumbUrl(path) {
  if (!path) return null;
  const parts = path.split('/');
  const filename = parts.pop();
  const subfolder = parts.pop();
  return `${API}/thumb/${subfolder}/${filename}`;
}

/* ── ESCAPE HTML ── */
function esc(s) {
  return String(s || '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

/* ── SET CONTENT (subtab-aware) ── */
function _setContent(h) {
  const sub = document.getElementById('ep-subtab-content');
  if (sub) { sub.innerHTML = h; }
  else     { document.getElementById('main-content').innerHTML = h; }
}

/* ── STAT CARD (shared little tile) ── */
function statCard(label, value) {
  return `<div style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:10px 12px;">
    <div style="font-size:1.1rem;font-weight:700;">${value ?? '—'}</div>
    <div style="font-size:.68rem;color:var(--muted);margin-top:2px;">${label}</div>
  </div>`;
}

/* ── FIRST-RUN HELPER ── still on the default password? Walk the user straight
   to changing it (retail installs had no way to know the default, and nothing
   prompted them afterwards). Shows until a real password is set. */
(async () => {
  try {
    const s = await api('/api/auth/status');
    if (!s.default_password) return;
    const b = document.createElement('div');
    b.id = 'default-pw-banner';
    b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:1200;background:#7a4a12;color:#ffe9c2;' +
      'padding:8px 14px;font-size:.85rem;display:flex;gap:12px;align-items:center;justify-content:center;box-shadow:0 2px 12px rgba(0,0,0,.4)';
    b.innerHTML = `🔑 <b>You're signed in with the default password (<code>store</code>)</b> — set your own now.
      <button style="background:#e8a13c;color:#231303;border:none;border-radius:6px;padding:4px 12px;font-weight:700;cursor:pointer"
        onclick="switchView('settings'); setTimeout(() => { if (window.settingsSub) settingsSub('system');
          const pg = document.getElementById('password-group');
          if (pg && 'open' in pg) pg.open = true;
          const el = document.getElementById('s-pw-cur');
          if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); el.focus(); } }, 300);">Change password</button>
      <span style="cursor:pointer;opacity:.7" title="dismiss until next reload" onclick="this.parentElement.remove()">✕</span>`;
    document.body.appendChild(b);
  } catch {}
})();

/* ── DONATE LINK (sidebar footer) ── shows the ☕ Support this project link only
   when the owner set a Buy Me a Coffee handle AND flipped donate_enabled on
   (Settings → Integrations → Donations). Same GET /api/donate/config the Money
   tab card and the login page read — toggle off / no handle hides all of them.
   settings-core.js calls refreshDonateLink() after a Donations save so the
   sidebar updates without a reload. */
async function refreshDonateLink() {
  const el = document.getElementById('sidebar-donate');
  if (!el) return;
  try {
    const d = await api('/api/donate/config');
    if (d && d.enabled && d.url) { el.href = d.url; el.style.display = 'flex'; }
    else { el.href = '#'; el.style.display = 'none'; }
  } catch { el.style.display = 'none'; }
}
window.refreshDonateLink = refreshDonateLink;
refreshDonateLink();

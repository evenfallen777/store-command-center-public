'use strict';

/* ── STUDIO HUB view root ── (falls back to #main-content when standalone) */
function viewRoot() {
  return document.getElementById('studio-content') || document.getElementById('main-content');
}

/* ── NAV ── */
document.getElementById('main-nav').addEventListener('click', e => {
  // collapse/expand a group when its title is clicked
  const title = e.target.closest('.nav-group-title');
  if (title) {
    const g = title.parentElement;
    g.classList.toggle('collapsed');
    saveNavGroups();
    return;
  }
  const item = e.target.closest('[data-view]');
  if (!item) return;
  switchView(item.dataset.view);
});

function saveNavGroups() {
  try {
    const collapsed = [...document.querySelectorAll('.nav-group.collapsed')].map(g => g.dataset.group);
    localStorage.setItem('navCollapsed', JSON.stringify(collapsed));
  } catch {}
}
function restoreNavGroups() {
  try {
    const collapsed = JSON.parse(localStorage.getItem('navCollapsed') || '[]');
    collapsed.forEach(name => {
      const g = document.querySelector(`.nav-group[data-group="${name}"]`);
      if (g) g.classList.add('collapsed');
    });
  } catch {}
}
restoreNavGroups();

/* ── Per-tab visibility (Settings → 👁️ Interface, Phase 1 of the God Panel
   rework) ── ui_hidden_tabs (JSON array of hidden view keys) hides any nav
   tab a user doesn't want; settings-interface.js is the editor, this is the
   enforcement. NEVER-HIDEABLE: settings, dashboard, command (Phase 2 — the
   standalone Command tab is the god/console controls' door in even if the
   game view/World tab is off or hidden) — always a way in/out. */
const NAV_NEVER_HIDE = new Set(['settings', 'dashboard', 'command']);
let _navHiddenSet = new Set();   // populated by applyNavVisibility(), read synchronously by switchView/renderView
applyNavVisibility();

// Legacy money views now live as panes inside the 💰 Finance tab. The old ids
// stay valid switchView targets (dashboard cards etc. deep-link to them) — they
// highlight the Finance nav item and open Finance on the right pane.
const _FIN_VIEW_ALIASES = { treasury: 'finance', money: 'finance', crypto: 'finance', wallets: 'finance', research: 'knowledge', library: 'knowledge', graph: 'knowledge' };

/* Reads ui_hidden_tabs, hides matching .nav-item[data-view], collapses a
   .nav-group whose children are all hidden, and bounces off the current view
   if it just got hidden out from under the user. Called at boot (above) and
   again after any save in settings-interface.js — always synchronizes
   _navHiddenSet so switchView/renderView's guards stay accurate without an
   API round-trip per navigation. */
async function applyNavVisibility() {
  let hidden = [];
  try {
    const s = await api('/api/settings');
    _settings = s;
    hidden = JSON.parse(s.ui_hidden_tabs || '[]');
  } catch { hidden = []; }
  if (!Array.isArray(hidden)) hidden = [];
  _navHiddenSet = new Set(hidden.filter(v => !NAV_NEVER_HIDE.has(v)));

  document.querySelectorAll('.nav-item[data-view]').forEach(item => {
    const v = item.dataset.view;
    if (NAV_NEVER_HIDE.has(v)) return;   // never touched
    // #nav-nsfw's visibility is independently owned by tab-nsfw.js's
    // updateNsfwNav() (nsfw_enabled/nsfw_display gate it off by default) —
    // only ever HIDE it here; showing it is that file's call, not ours.
    if (item.id === 'nav-nsfw') {
      if (_navHiddenSet.has(v)) item.style.display = 'none';
      return;
    }
    item.style.display = _navHiddenSet.has(v) ? 'none' : '';
  });
  document.querySelectorAll('.nav-group').forEach(g => {
    const items = [...g.querySelectorAll('.nav-item[data-view]')];
    const allHidden = items.length > 0 && items.every(it => it.style.display === 'none');
    g.style.display = allHidden ? 'none' : '';
  });

  const navView = _FIN_VIEW_ALIASES[_currentView] || _currentView;
  if (_navHiddenSet.has(navView)) switchView('dashboard');
}
window.applyNavVisibility = applyNavVisibility;

function switchView(view) {
  const navView0 = _FIN_VIEW_ALIASES[view] || view;
  if (_navHiddenSet.has(navView0)) view = 'dashboard';   // deep-link/nav-click into a hidden tab → Dashboard
  _currentView = view;
  const navView = _FIN_VIEW_ALIASES[view] || view;
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === navView));
  // update the header title from the matching nav item's label
  const active = document.querySelector(`.nav-item[data-view="${navView}"] span:not(.nav-icon):not(.nav-badge)`);
  const tt = document.getElementById('topbar-title');
  if (tt && active) tt.textContent = active.textContent;
  renderView(view);
}

/* Lightweight card shown instead of the game canvas when ui_world_game_enabled
   is "0" — the sim keeps running server-side; only the pixel-art view is
   hidden. Turning it back on re-renders the world view in place. */
function _renderWorldOffCard() {
  const main = document.getElementById('main-content');
  main.innerHTML = `<div class="card" style="max-width:560px;margin:60px auto;padding:26px;text-align:center;">
    <div style="font-size:2rem;margin-bottom:10px;">&#127918;&#65039;</div>
    <div style="font-size:1rem;font-weight:700;margin-bottom:8px;">Company game view is off</div>
    <div style="font-size:.85rem;color:var(--muted);line-height:1.6;margin-bottom:16px;">
      The Company keeps running on the server &mdash; agents, economy, and events continue exactly as before.
      Only the pixel-art visualization is hidden.
    </div>
    <button class="btn-sm primary" id="world-game-on-btn">&#9654;&#65039; Turn game view on</button>
  </div>`;
  document.getElementById('world-game-on-btn')?.addEventListener('click', async () => {
    try {
      await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ ui_world_game_enabled: '1' }) });
      _settings.ui_world_game_enabled = '1';
      toast('Game view on');
      switchView('world');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  });
}

async function renderView(view) {
  const navView0 = _FIN_VIEW_ALIASES[view] || view;
  if (_navHiddenSet.has(navView0)) view = 'dashboard';   // guard direct renderView() calls too
  const main = document.getElementById('main-content');
  // Stop any playing <video>/<audio> BEFORE the swap — a detached media element
  // can keep its audio playing after its view is gone (tab-videos.js helper).
  if (typeof stopMediaIn === 'function') stopMediaIn(main);
  main.innerHTML = '<div class="empty"><div class="empty-icon">&#9203;</div>Loading&#8230;</div>';
  // NOTE: do NOT reset _cardsBound here — bindCards attaches a single permanent
  // delegated listener that handles all views; resetting causes listener accumulation.
  try {
    switch (view) {
      case 'dashboard':     await renderDashboard();       break;
      case 'world':
        if ((_settings.ui_world_game_enabled || '1') === '0') { _renderWorldOffCard(); }
        else { await renderWorld(); }
        break;
      // Command = Phase 2 of the God Panel rework: the same god/console controls
      // (prayers, control, board, …) hosted standalone, no game canvas/RAF loop —
      // works even with the World tab/game view off. tab-command.js.
      case 'command':       await renderCommand();         break;
      // Finance hub — Overview / Treasury / Missions & Earn / Wallets / Markets
      // as sub-tab panes. Legacy view names deep-link straight to their pane.
      case 'finance':       await renderFinance();             break;
      case 'treasury':      await renderFinance('treasury');   break;
      case 'etsy-printify': await renderEtsyPrintify();    break;
      case 'cults3d':       await renderCults3D();         break;
      case 'portal':        await renderPortal();          break;
      case 'social':        await renderSocial();          break;
      case 'money':         await renderFinance('money');      break;
      case 'mail':          await renderMail();            break;
      case 'github':        await renderGithub();          break;
      case 'resell':        await renderResell();          break;
      case 'settings':      await renderSettings();        break;
      case 'agent':         await renderAgent();           break;
      case 'library':       await renderKnowledge('library'); break;
      case 'graph':          await renderKnowledge('graph');   break;
      case 'knowledge':     await renderKnowledge();       break;
      case 'network-security': await renderNetworkSecurity(); break;
      case 'homelab':       await renderHomelab();         break;
      case 'crypto':        await renderFinance('crypto');     break;
      case 'oracle':        await renderOracle();          break;
      case 'research':       await renderKnowledge('research'); break;
      case 'games':         await renderGames();           break;
      case 'wallets':       await renderFinance('wallets');    break;
      case 'nsfw':          await renderNsfw();            break;
      // Studio hub — Image / Video / Audio / 3D / Models / Queue as sub-tabs.
      // Legacy view names deep-link straight to the matching sub-tab.
      case 'studio':        await renderStudio();          break;
      case 'image-gen':     await renderStudio('image');   break;
      case 'videos':        await renderStudio('video');   break;
      case 'audio':         await renderStudio('audio');   break;
      case 'models3d':      await renderStudio('3d');       break;
      case 'director':      await renderStudio('director'); break;
      case 'models':        await renderStudio('models');  break;
      // Legacy direct views (still accessible, redirect into E/P subtab)
      case 'proposals':     _etsySubTab='proposals';  await switchView('etsy-printify'); break;
      case 'review':        _etsySubTab='review';     await switchView('etsy-printify'); break;
      case 'approved':      _etsySubTab='approved';   await switchView('etsy-printify'); break;
      case 'published':     _etsySubTab='published';  await switchView('etsy-printify'); break;
      case 'store-stats':   _etsySubTab='dashboard';  await switchView('etsy-printify'); break; // stats folded into the Dashboard
      case 'products':      _etsySubTab='products';   await switchView('etsy-printify'); break;
      // Drop-in plugins: any view registered via registerView() (plugin-loader.js).
      // A crashing plugin render paints an in-view error card — never breaks nav.
      default:
        if (window.PLUGIN_VIEWS && PLUGIN_VIEWS[view]) {
          try { await PLUGIN_VIEWS[view](); }
          catch (pe) {
            main.innerHTML = `<div class="card" style="padding:18px;max-width:620px;">
              <h3 style="margin:0 0 8px;">&#9888;&#65039; Plugin view failed</h3>
              <div style="font-size:.85rem;">The <b>${esc(view)}</b> plugin threw while rendering:</div>
              <pre style="margin:10px 0;padding:10px;background:var(--surface);border:1px solid var(--border);border-radius:8px;font-size:.75rem;white-space:pre-wrap;overflow-x:auto;">${esc(pe && pe.message ? pe.message : String(pe))}</pre>
              <div style="font-size:.78rem;color:var(--muted);">The rest of the store is unaffected. You can disable this plugin in Settings &rarr; &#128268; Plugins.</div>
            </div>`;
          }
        }
        break;
    }
  } catch(e) {
    main.innerHTML = `<div class="empty"><div class="empty-icon">&#10060;</div>${esc(e.message)}</div>`;
  }
  bindCards();
}

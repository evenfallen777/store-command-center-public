'use strict';

/* ── Settings → 👁️ Interface ─────────────────────────────────────────────
   Phase 1 of the God Panel rework: per-tab visibility + a Company-game on/off
   toggle. Pure frontend + generic settings — nothing new on the backend, the
   world sim keeps running server-side regardless of what's hidden here.

   Source of truth for "every nav tab" is the LIVE DOM (#main-nav
   .nav-item[data-view]) rather than a hand-maintained list — that already
   merges the static nav (index.html) with plugin views injected by
   plugin-loader.js, so a new plugin or a nav edit never needs a matching edit
   here.

   Persisted via the generic PATCH /api/settings (see app/routers/settings.py
   — accepts any key):
     ui_hidden_tabs        — JSON array of hidden view keys (default [])
     ui_world_game_enabled — "1" default; "0" hides the game canvas

   Enforcement (hiding nav items, redirecting deep links, swapping the world
   canvas for a status card) lives in app-nav.js: applyNavVisibility(),
   NAV_NEVER_HIDE, and the switchView/renderView guards. This file only reads
   + writes the two settings keys and calls applyNavVisibility() to make a
   change take effect immediately (no restart). */
(function () {

  function navTabs() {
    return [...document.querySelectorAll('#main-nav .nav-item[data-view]')]
      .filter(item => !(typeof NAV_NEVER_HIDE !== 'undefined' && NAV_NEVER_HIDE.has(item.dataset.view)))
      .map(item => ({
        view:  item.dataset.view,
        icon:  item.querySelector('.nav-icon')?.textContent || '\u{1F9E9}',
        label: item.querySelector('span:not(.nav-icon):not(.nav-badge)')?.textContent?.trim() || item.dataset.view,
      }));
  }

  function tabRow(t, hidden) {
    const on = !hidden; // switch reads "visible"
    return `<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;
        padding:9px 12px;border-bottom:1px solid var(--border);">
      <div style="display:flex;align-items:center;gap:8px;min-width:0;">
        <span>${t.icon}</span>
        <span style="font-size:.82rem;">${esc(t.label)}</span>
        <code style="font-size:.6rem;color:var(--muted);">${esc(t.view)}</code>
      </div>
      <div class="toggle ${on ? 'on' : ''}" data-hide-view="${esc(t.view)}"
        title="${on ? 'Visible — click to hide this tab' : 'Hidden — click to show this tab'}"></div>
    </div>`;
  }

  window.renderInterfacePane = async function () {
    const pane = document.getElementById('pane-interface');
    if (!pane) return;
    pane.innerHTML = `<div style="color:var(--muted);font-size:.8rem;">Loading&#8230;</div>`;

    let settings = {};
    try { settings = await api('/api/settings'); _settings = settings; }
    catch (e) {
      pane.innerHTML = `<div style="color:var(--muted);font-size:.8rem;">Couldn't load settings: ${esc(e.message)}</div>`;
      return;
    }

    let hidden = [];
    try { hidden = JSON.parse(settings.ui_hidden_tabs || '[]'); } catch {}
    if (!Array.isArray(hidden)) hidden = [];
    const hiddenSet = new Set(hidden);
    const gameOn = (settings.ui_world_game_enabled || '1') !== '0';
    const namingTheme = settings.naming_theme === 'neutral' ? 'neutral' : 'themed';

    const tabs = navTabs();
    const rows = tabs.length
      ? tabs.map(t => tabRow(t, hiddenSet.has(t.view))).join('')
      : `<div style="padding:14px 12px;color:var(--muted);font-size:.8rem;">No nav tabs found.</div>`;

    pane.innerHTML = `
      <div class="settings-grid">
      <div class="settings-group">
        <div class="settings-group-title">&#127991;&#65039; App branding ${hlp('Your brand name. Shown in the header and used as context when the LLM auto-writes listing titles/descriptions. Cosmetic + prompt context; does not rename anything on Etsy/Printify.')}</div>
        <div class="field" style="max-width:420px;">
          <label>Store / Brand Name &mdash; shown in the header</label>
          <input type="text" id="s-store-name" value="${esc(settings.store_name || '')}" placeholder="My Awesome Store">
          <div style="font-size:.7rem;color:var(--muted);margin-top:3px;">Also used as a prefix when auto-generating listing titles.</div>
        </div>
        <button class="btn-sm primary" id="s-brand-save" style="margin-top:6px;">&#128190; Save</button>
      </div>
      <div class="settings-group">
        <div class="settings-group-title">&#128065;&#65039; Nav tabs ${hlp('Hide any tab you never use — it disappears from the sidebar immediately. A hidden tab’s deep links (dashboard cards, saved links, etc.) fall back to Dashboard instead of erroring. Settings and Dashboard can’t be hidden.')}</div>
        <div style="border:1px solid var(--border);border-radius:10px;overflow:hidden;">${rows}</div>
      </div>
      <div class="settings-group">
        <div class="settings-group-title">&#127918; Company game visualization ${hlp('Turns off the pixel-art canvas on The Company tab. The sim keeps running on the server exactly as before — agents, economy, events — this only hides the picture (useful on low-power devices or if you just don’t want the animation).')}</div>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 12px;">
          <span style="font-size:.82rem;">Show the game canvas on The Company tab</span>
          <div class="toggle ${gameOn ? 'on' : ''}" id="toggle-world-game"
            title="${gameOn ? 'On — click to turn the game view off' : 'Off — click to turn the game view on'}"></div>
        </div>
      </div>
      <div class="settings-group">
        <div class="settings-group-title">&#127991;&#65039; Naming theme ${hlp('How the god-tier & security layer is PRESENTED. Themed (default) keeps the current look — 🌟 God, ✝️ Jesus, 😈 Satan, ⛪ Church, 🍺 Bar, 🔞 store. Neutral shows the SAME system with professional labels — Owner, Constructive/Adversarial Lieutenant, Advisory Hall, Social Lounge, Private Studio. Display labels only: no internal id, actor, gate, setting key or endpoint changes, and every approval gate and safety floor is identical in both. Re-labels on the next render of each panel.')}</div>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 12px;">
          <span style="font-size:.82rem;">God-tier &amp; security display labels</span>
          <select id="naming-theme-sel" style="font-size:.8rem;background:var(--bg,#0e1626);color:var(--text,#e2e8f0);border:1px solid var(--border,#26324a);border-radius:6px;padding:4px 8px;">
            <option value="themed" ${namingTheme === 'themed' ? 'selected' : ''}>&#127775; Themed &mdash; God &middot; Jesus &middot; Satan (default)</option>
            <option value="neutral" ${namingTheme === 'neutral' ? 'selected' : ''}>&#128188; Neutral &mdash; Owner &middot; Lieutenants</option>
          </select>
        </div>
      </div>
      </div>`;

    // Store / Brand Name — moved here from the Etsy Store Configuration; saves
    // through the same generic PATCH /api/settings the rest of this pane uses.
    // Safe from the empty-wipe case: this pane aborts above if the GET failed.
    const brandBtn = document.getElementById('s-brand-save');
    if (brandBtn) brandBtn.addEventListener('click', async () => {
      const name = document.getElementById('s-store-name').value.trim();
      try {
        await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ store_name: name }) });
        _settings.store_name = name;
        toast('Store name saved ✓');
      } catch (e) { toast(`Couldn't save: ${e.message}`, 'error'); }
    });

    pane.querySelectorAll('.toggle[data-hide-view]').forEach(el => {
      el.addEventListener('click', async () => {
        const view = el.dataset.hideView;
        const willShow = !el.classList.contains('on');   // currently hidden → click shows it
        el.classList.toggle('on');
        el.title = el.classList.contains('on') ? 'Visible — click to hide this tab' : 'Hidden — click to show this tab';
        let cur = [];
        try { cur = JSON.parse(_settings.ui_hidden_tabs || '[]'); } catch {}
        if (!Array.isArray(cur)) cur = [];
        cur = willShow ? cur.filter(v => v !== view) : [...new Set([...cur, view])];
        try {
          await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ ui_hidden_tabs: JSON.stringify(cur) }) });
          _settings.ui_hidden_tabs = JSON.stringify(cur);
          toast(`${view} ${willShow ? 'shown' : 'hidden'}`);
          if (typeof applyNavVisibility === 'function') applyNavVisibility();
        } catch (e) {
          el.classList.toggle('on');   // revert
          toast(`Couldn't save: ${e.message}`, 'error');
        }
      });
    });

    const gameToggle = document.getElementById('toggle-world-game');
    if (gameToggle) gameToggle.addEventListener('click', async () => {
      const nowOn = !gameToggle.classList.contains('on');
      gameToggle.classList.toggle('on');
      gameToggle.title = nowOn ? 'On — click to turn the game view off' : 'Off — click to turn the game view on';
      try {
        await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ ui_world_game_enabled: nowOn ? '1' : '0' }) });
        _settings.ui_world_game_enabled = nowOn ? '1' : '0';
        toast(`Company game visualization ${nowOn ? 'on' : 'off'}`);
        if (_currentView === 'world') switchView('world');   // refresh in place if already open
      } catch (e) {
        gameToggle.classList.toggle('on');   // revert
        toast(`Couldn't save: ${e.message}`, 'error');
      }
    });

    // Naming theme (display labels only — world-theme.js / app/naming_theme.py).
    // Saved via the same generic PATCH /api/settings flow as everything above.
    const themeSel = document.getElementById('naming-theme-sel');
    if (themeSel) themeSel.addEventListener('change', async () => {
      const v = themeSel.value === 'neutral' ? 'neutral' : 'themed';
      const prev = _settings.naming_theme === 'neutral' ? 'neutral' : 'themed';
      try {
        await api('/api/settings', { method: 'PATCH', body: JSON.stringify({ naming_theme: v }) });
        _settings.naming_theme = v;
        if (window.WTheme) WTheme.set(v);   // client relabels on each panel's next render
        toast(v === 'neutral'
          ? 'Neutral naming — Owner · Lieutenants (labels update as panels re-render)'
          : 'Themed naming — God · Jesus · Satan (labels update as panels re-render)');
      } catch (e) {
        themeSel.value = prev;              // revert
        toast(`Couldn't save: ${e.message}`, 'error');
      }
    });
  };
})();

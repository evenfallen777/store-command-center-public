/* ══ COMMAND TAB — Phase 2 of the God Panel rework ══
   The exact same god/console controls as The Company's 🏛️ God Console — the
   12 WORLD_CONSOLE_TABS (Prayers, Workboard, Priorities, Control, Republic,
   Board, Bible, Finances, Roster, Research, Schedule, Settings) declared in
   world-economy.js — hosted standalone: no game canvas, no sprite/tileset
   init, no RAF loop. The DATA side was already pure API calls plus a
   server-side autonomy loop; only the render target was tied to the game
   modal. _worldModal/_consoleStrip (world-economy.js) resolve their host
   dynamically via _consoleHost() and paint into #command-title/#command-strip
   /#command-body below whenever this container is in the DOM — the exact
   same world*() tab functions the game's HUD 🏛️ button calls, completely
   unmodified. Global-scope classic script. */
async function renderCommand() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="view-header">
      <div class="view-title">&#128377;&#65039; Command</div>
      <div class="view-sub">The Company's managed control surface &mdash; breakers &amp; gates, the full capability
        catalog, the agent-loops map, approvals, governance and company books &mdash; grouped into sections instead of a
        wall of tabs. The sim keeps running on the server either way; this is a second door into the exact same console
        the World tab's &#127963;&#65039; God button opens.</div>
    </div>
    <div style="background:#0f1626;border:1px solid #2a3752;border-radius:12px;padding:18px;max-width:960px;">
      <div id="command-title" style="font-weight:700;color:#e8eefc;font-size:1rem;margin-bottom:10px;"></div>
      <div id="command-strip" style="display:none;gap:4px;flex-wrap:wrap;margin:-2px 0 10px;padding-bottom:8px;border-bottom:1px solid #1b2740"></div>
      <div id="command-body" style="white-space:pre-wrap;font-size:.78rem;color:#c7d2e5;font-family:ui-monospace,monospace;margin:0"></div>
    </div>`;
  // Default tab = Breakers ('breakers', Phase 3) — the consolidated master
  // switch/gates/automation page, and the first entry in WORLD_CONSOLE_TABS.
  // (The game HUD's 🏛️ button still opens straight to Prayers via its own
  // worldConsole('god') call — unrelated to this default.) worldConsole() is
  // defined in world-economy.js and is host-agnostic; it finds #command-title
  // above (this container) and paints there instead of the game's #world-modal-*.
  worldConsole('breakers');
  if (window.worldGodRefreshBadge) worldGodRefreshBadge();   // pending-prayer badge (world-god.js)
}
window.renderCommand = renderCommand;

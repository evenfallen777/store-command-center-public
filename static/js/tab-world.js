'use strict';
/* ══════════════════════════════════════════════════════════════════════════
   THE COMPANY — a living pixel-art world.
   Every character is a real job/agent on the platform. Backend (/api/world/*)
   owns truth + state machine; this file owns pixel layout + smooth animation.
   ══════════════════════════════════════════════════════════════════════════ */

/* The tile world (terrain, buildings, pathfinding, camera) lives in WM
   (world-map.js). This file maps agents onto it and animates them. */

let _worldState = null;          // last /api/world/state payload
let _sprites = {};               // id -> {col,row, path, targetKey, px,py, agent, bob}
let _propImgs = {};              // prop id -> HTMLImageElement (loaded sprites)
let _propSheets = {};            // prop id -> 4-frame idle-animation sheet (if generated)
let _houseByKey = {};            // agent key -> house slot index (stable homes)
let _selectedId = null;
let _worldTimers = { raf: null, poll: null, think: null };

/* ── play-god edit mode ── */
let _edit = { on: false, sel: null, add: null, drag: null, ghost: null, addGhost: null };

/* Resolve an agent's current symbolic location → a map tile. */
function _agentTile(a) {
  const loc = a.location || 'park';
  if (loc === 'posted') {                            // RCT-style: dropped on a free spot by the player
    return { col: a.posted_col || (WM.COLS / 2 | 0), row: a.posted_row || (WM.ROWS / 2 | 0) };
  }
  if (loc === 'home') {                              // per-agent house
    const slots = WM.houseSlots;
    let idx = _houseByKey[a.key];
    if ((idx == null || !slots[idx]) && slots.length) idx = a.id % slots.length;  // survive edits
    if (idx != null && slots[idx]) return slots[idx];
  }
  if (loc === 'defense') {                           // raid posts: builders man the CITY walls, fighters hold inside
    const g = (typeof _raidGeom === 'function') ? _raidGeom() : null;
    const cx = g ? g.cx : WM.COLS / 2, cy = g ? g.cy : WM.ROWS / 2;
    const RX = g ? g.RX : 34, RY = g ? g.RY : 28;
    // hash the id to an angle instead of `id % 12` (only 12 slots → up to 4 of the
    // 39 agents shared an angle and stacked). 3600 slots is effectively collision-free.
    const _rh = (a.id * 2654435761) >>> 0;
    const ang = (_rh % 3600) / 3600 * Math.PI * 2;
    if (a.role === 'build') {                         // spread all around the wall ring
      return { col: Math.round(cx + Math.cos(ang) * RX), row: Math.round(cy + Math.sin(ang) * RY) };
    }
    return { col: Math.round(cx + Math.cos(ang) * RX * 0.7), row: Math.round(cy + Math.sin(ang) * RY * 0.7) };  // fighters: inner ring
  }
  if (loc === 'shop') {                              // grocery run → "their" store (stable per agent)
    const shops = (WM.buildings || []).filter(b => b.kind === 'shop');
    if (shops.length) {
      const b = shops[a.id % shops.length];
      return { col: b.c + (b.w / 2 | 0), row: b.r + (b.h / 2 | 0) };
    }
  }
  // WM.locations[kind] collapses to ONE tile even when several matching nodes
  // exist (play-god can add extra resource nodes of the same kind) — every agent
  // headed there piled onto that single tile. Spread a cohort across all matching
  // nodes; falls back to the single collapsed location when there's just one (or
  // none), so the common case is unchanged.
  const nodeCands = (WM.nodes || []).filter(n => n.kind === loc);
  if (nodeCands.length > 1) return nodeCands[a.id % nodeCands.length];
  return WM.locations[loc] || WM.locations['park'] || { col: 28, row: 22 };
}

/* Assign each non-leader agent a stable personal house. */
function _assignHouses(agents) {
  const residents = agents.filter(a => a.kind !== 'mayor' && a.kind !== 'boss')
    .sort((x, y) => x.id - y.id);
  residents.forEach((a, i) => { if (_houseByKey[a.key] == null) _houseByKey[a.key] = i % WM.houseSlots.length; });
}

function _stopWorld() {
  _saveWorldVisuals();
  document.getElementById('world-snd-pop')?.remove();   // mixer popover dies with the view
  if (_worldView.ro) { try { _worldView.ro.disconnect(); } catch {} _worldView.ro = null; }  // canvas ResizeObserver
  if (window.WM && WM.detachControls) WM.detachControls();   // release window drag listeners (canvas-memory leak)
  if (_worldTimers.raf)   cancelAnimationFrame(_worldTimers.raf);
  if (_worldTimers.poll)  clearInterval(_worldTimers.poll);
  if (_worldTimers.think) clearInterval(_worldTimers.think);
  if (_worldTimers.heal)  clearInterval(_worldTimers.heal);
  _worldTimers = { raf: null, poll: null, think: null, heal: null };
}

/* ── visual-state persistence ─────────────────────────────────────────────────
   The BACKEND ticker owns world progression (it advances whether or not anyone
   is watching). What we persist here is only the VIEW: camera framing and the
   cosmetic tallies, so re-entering the tab resumes where you left off instead
   of resetting. */
function _saveWorldVisuals() {
  try {
    const c = WM.camera;
    if (c && isFinite(c.x) && isFinite(c.scale))
      localStorage.setItem('world_cam', JSON.stringify({ x: c.x, y: c.y, scale: c.scale }));
    if (window.WF && WF.save) WF.save();
  } catch {}
}
function _restoreCamera(canvas) {
  try {
    const c = JSON.parse(localStorage.getItem('world_cam') || 'null');
    if (c && isFinite(c.x) && isFinite(c.y) && isFinite(c.scale) && c.scale > 0.05 && c.scale <= 6) {
      WM.camera.x = c.x; WM.camera.y = c.y; WM.camera.scale = c.scale;
      return true;
    }
  } catch {}
  return false;
}

/* ── run-when-shown lifecycle ─────────────────────────────────────────────────
   The render + poll loops run ONLY while the world view exists AND the browser
   tab is visible; the sim keeps going server-side. (Previously a hidden browser
   tab paused RAF but left the 3s state poll running forever.) */
let _worldView = { canvas: null, ctx: null };

/* ── TEST/PREVIEW cycle ───────────────────────────────────────────────────────
   In Test run-mode the client CYCLES every visual state fast so you can eyeball the
   whole range in ~30s (the slow real progression is for Normal/Fast). It sweeps every
   building through all 7 civilization eras (~4s each → 28s) and runs a full day→night
   loop (so lighting + the moon show). Overrides _worldState each frame; leaving Test
   mode removes the banner and the next poll restores the real values. */
let _previewT = 0;
const _PREV_LADDER = ['wood', 'brick', 'metal', 'western', 'modern', 'futuristic', 'moon'];
const _PREV_EMOJI = { wood: '🪵', brick: '🧱', metal: '⚙️', western: '🤠', modern: '🏙️', futuristic: '🚀', moon: '🌙' };
function _previewTick(dt) {
  const on = !!(typeof _worldState !== 'undefined' && _worldState && _worldState.run_mode === 'test');
  let banner = document.getElementById('world-preview-banner');
  if (!on) { if (banner) banner.remove(); _previewT = 0; return; }
  _previewT += dt;
  const era = Math.min(6, Math.floor((_previewT / 4) % 7));        // 4s per era → 28s full sweep
  const e = _worldState.eras || (_worldState.eras = {});
  if (!e.ladder) e.ladder = _PREV_LADDER;
  if (!e.emoji) e.emoji = _PREV_EMOJI;
  e.town = era; e.byLoc = {};                                      // every building follows the sweep
  _worldState.clock_hour = Math.floor(_previewT % 24);            // ~1 hr/sec → a full day in 24s (night shows the moon)
  if (!banner) {
    const stage = document.getElementById('world-stage'); if (!stage) return;
    banner = document.createElement('div'); banner.id = 'world-preview-banner';
    banner.style.cssText = 'position:absolute;top:8px;left:50%;transform:translateX(-50%);z-index:40;padding:5px 14px;border-radius:20px;background:rgba(30,20,50,.92);border:1px solid #7c5aff;color:#e0d0ff;font-size:.76rem;font-family:ui-monospace,monospace;pointer-events:none;white-space:nowrap';
    stage.appendChild(banner);
  }
  banner.textContent = `🧪 TEST — cycling every visual state · ${_PREV_EMOJI[_PREV_LADDER[era]]} ${_PREV_LADDER[era]} · ${String(_worldState.clock_hour).padStart(2, '0')}:00`;
}

function _startWorldLoops() {
  const { canvas, ctx } = _worldView;
  if (!canvas || !document.getElementById('world-canvas')) return;
  if (!_worldTimers.poll) {
    _pollWorld();                                  // immediate catch-up frame
    _worldTimers.poll = setInterval(_pollWorld, 3000);
  }
  // Terrain self-heal: browsers reclaim the backing store of the large off-DOM terrain
  // canvas (and decoded ground/floor images) under memory pressure — the textures
  // silently blank after a while and used to need a full browser restart. Every 5s,
  // if the baked terrain reads as evicted, re-decode from URL + re-bake automatically.
  if (!_worldTimers.heal) {
    _worldTimers.heal = setInterval(() => {
      try { if (WM.terrainAlive && !WM.terrainAlive() && WM.reheal) WM.reheal(); } catch {}
    }, 5000);
  }
  if (_worldTimers.raf) return;
  let last = performance.now();
  const loop = (now) => {
    const cv = document.getElementById('world-canvas');
    if (!cv) { _stopWorld(); return; }          // navigated away → self-clean
    try {
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      _stepAgents(dt);
      _previewTick(dt);                         // Test mode: sweep every visual state fast
      if (window.WN) WN.tick(dt);
      if (window.WW) WW.tick(dt);
      if (window.WF) WF.tick(dt, (_worldState && _worldState.activity) || {});
      _drawWorld(ctx, canvas);
      if (!loop._shown) { loop._shown = true; _hideWorldLoading(); }   // fade the loader out on the first real frame
    } catch (e) {                                 // a single bad frame must not freeze the world
      if (!loop._warned) { console.error('[world] frame draw error (continuing):', e); loop._warned = true; }
    }
    _worldTimers.raf = requestAnimationFrame(loop);   // ALWAYS re-queue, even after a throw
  };
  _worldTimers.raf = requestAnimationFrame(loop);
}

document.addEventListener('visibilitychange', () => {
  if (!document.getElementById('world-canvas')) return;   // not on the world view
  if (document.hidden) _stopWorld();
  else {
    _startWorldLoops();
    // Coming back from a hidden tab is the #1 moment the browser has discarded the
    // terrain canvas — re-check + heal right away instead of waiting for the 5s tick.
    try { if (WM.terrainAlive && !WM.terrainAlive() && WM.reheal) WM.reheal(); } catch {}
  }
});

// ── Browser-fullscreen: the game already owns the whole tab (#8 game-first
// layout); this takes it the last step to the whole SCREEN. FS on the STAGE so
// the HUD overlays ride along; the canvas is width/height:100% either way. ───
function worldFullscreen() {
  const stage = document.getElementById('world-stage');
  if (!stage) return;
  if (document.fullscreenElement) { try { document.exitFullscreen(); } catch {} }
  else { try { const p = stage.requestFullscreen(); if (p && p.catch) p.catch(() => {}); } catch (e) { toast?.('Fullscreen not available'); } }
}
window.worldFullscreen = worldFullscreen;

// The canvas backing store is sized from clientWidth/clientHeight in _resize()
// (not per-frame), so on enter/exit we re-run _resize, then WM.fit() to
// recompute the camera to the new dimensions. Esc (native FS exit) fires
// fullscreenchange too, so the same handler covers both directions.
document.addEventListener('fullscreenchange', () => {
  const stage = document.getElementById('world-stage');
  const cv = document.getElementById('world-canvas');
  if (!stage || !cv) return;
  const fs = document.fullscreenElement === stage;
  const btn = document.getElementById('world-fs-btn');
  if (btn) btn.classList.toggle('on', fs);
  requestAnimationFrame(() => {                          // let layout settle first
    if (window._worldResize) window._worldResize();
    if (window.WM && WM.fit) WM.fit(cv._cssW || cv.clientWidth, cv._cssH || cv.clientHeight);
  });
});

/* world-hud.js is loaded on demand as a classic script (index.html untouched).
   Reuse the ?v= cache-bust stamp THIS script was served with, so the HUD file
   updates in lock-step with the rest of the app (never hand-write a ?v=). */
let _hudLoadP = null;
function _loadWorldHud() {
  if (window.WHUD) return Promise.resolve();
  if (_hudLoadP) return _hudLoadP;
  _hudLoadP = new Promise((resolve, reject) => {
    const own = document.querySelector('script[src*="js/tab-world.js"]');
    const q = own && own.src.indexOf('?') > 0 ? own.src.slice(own.src.indexOf('?')) : '';
    const s = document.createElement('script');
    s.src = (typeof API !== 'undefined' ? API : '/store') + '/static/js/world-hud.js' + q;
    s.onload = resolve;
    s.onerror = () => { _hudLoadP = null; reject(new Error('world-hud.js unreachable')); };
    document.head.appendChild(s);
  });
  return _hudLoadP;
}

/* ── loading screen ───────────────────────────────────────────────────────────
   The world does real async work on open (sprite packs, tileset/building atlases,
   layout + generated terrain/floor/moon fetch & decode, the bake) — a bare black
   canvas for a second reads as "broken". A themed overlay (an orbiting-moon loader,
   nodding to the space layers) covers it, updates through the stages, and fades out
   on the FIRST painted frame so there's never a black flash. */
function _ensureWorldLoadingCSS() {
  if (document.getElementById('world-loading-css')) return;
  const st = document.createElement('style'); st.id = 'world-loading-css';
  st.textContent =
    '@keyframes wl-spin{to{transform:rotate(360deg)}}' +
    '@keyframes wl-bob{0%,100%{opacity:.5}50%{opacity:1}}' +
    '#world-loading .wl-orbit{position:relative;width:72px;height:72px;animation:wl-spin 2.8s linear infinite}' +
    '#world-loading .wl-planet{position:absolute;top:50%;left:50%;width:38px;height:38px;margin:-19px 0 0 -19px;border-radius:50%;background:radial-gradient(circle at 32% 28%,#4a90e0,#2f6bb0 60%,#173a66);box-shadow:0 0 22px rgba(74,144,224,.45)}' +
    '#world-loading .wl-moon{position:absolute;top:-2px;left:50%;width:13px;height:13px;margin-left:-6px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#f2f0e6,#b9b6a4);box-shadow:0 0 8px rgba(240,238,225,.4)}' +
    '#world-loading .wl-dots{animation:wl-bob 1.5s ease-in-out infinite}';
  document.head.appendChild(st);
}
function _worldLoad(msg) {
  const el = document.getElementById('world-loading-msg');
  if (el) el.textContent = msg;
}
function _hideWorldLoading() {
  const ov = document.getElementById('world-loading');
  if (!ov) return;
  ov.style.opacity = '0';
  setTimeout(() => { ov.remove(); }, 600);
}

async function renderWorld() {
  _stopWorld();                  // clean any prior instance
  _ensureWorldLoadingCSS();
  // keep _sprites/_selectedId — agents resume from where they stood and the
  // inspector stays on the same character across tab switches

  /* GAME-FIRST LAYOUT (#8): the canvas fills the ENTIRE tab (the old
     view-header/boxed-wrap/side-panels are gone); everything else is a floating
     HUD overlay built by world-hud.js (toolbar of toggles + draggable panels).
     The -24px margins cancel .main-scroll's padding so the stage is edge-to-edge;
     height:calc(100% + 48px) resolves against the scroller's content box, i.e.
     exactly the visible tab height. */
  const h = `
  <div id="world-stage" style="position:relative;margin:-24px;height:calc(100% + 48px);min-height:420px;overflow:hidden;background:#0a0f1a">
    <div id="world-canvas-wrap" style="position:absolute;inset:0;background:#0a0f1a">
      <canvas id="world-canvas" style="width:100%;height:100%;display:block;image-rendering:pixelated;cursor:grab"></canvas>
    </div>
    <div id="world-loading" style="position:absolute;inset:0;z-index:60;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:15px;background:radial-gradient(ellipse at center,#101a2e,#070b12);transition:opacity .55s ease">
      <div class="wl-orbit"><div class="wl-planet"></div><div class="wl-moon"></div></div>
      <div style="font-weight:750;font-size:1.05rem;color:#e8eefc;letter-spacing:.02em">🏙️ The Company</div>
      <div id="world-loading-msg" class="wl-dots" style="font-size:.8rem;color:#8aa2c4;font-family:ui-monospace,monospace">Waking the town…</div>
    </div>
  <div id="world-editbar" style="display:none;position:absolute;top:42px;left:0;right:0;z-index:25;gap:6px;align-items:center;flex-wrap:wrap;padding:8px 16px;background:rgba(19,26,40,.94);border-bottom:1px solid #26324a;font-size:.76rem;color:#c7d2e5">
    <b style="color:#a78bfa">🛠️ God Mode</b>
    <span id="world-editsel" style="color:#7a86a0">— click a building to select</span>
    <span style="margin-left:auto"></span>
    <button class="btn" style="padding:4px 8px" onclick="worldEditResize(-1,0)">W−</button>
    <button class="btn" style="padding:4px 8px" onclick="worldEditResize(1,0)">W+</button>
    <button class="btn" style="padding:4px 8px" onclick="worldEditResize(0,-1)">H−</button>
    <button class="btn" style="padding:4px 8px" onclick="worldEditResize(0,1)">H+</button>
    <button class="btn" style="padding:4px 8px" onclick="worldEditAdd('house')">➕ House</button>
    <button class="btn" style="padding:4px 8px" onclick="worldEditAdd('shop')">➕ Shop</button>
    <button class="btn" style="padding:4px 8px" onclick="worldEditAdd('tree')">🌲 Tree</button>
    <span style="width:1px;height:16px;background:#33456b;margin:0 2px"></span>
    <span style="color:#7a86a0;font-size:.7rem">Decor (place many):</span>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('decor:plant')">🪴</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('decor:lamp')">💡</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('decor:bench')">🪑</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('decor:statue')">🗿</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('decor:fountain')">⛲</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('decor:picnic_table')" title="Picnic spot">🧺</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('decor:rock')">🪨</button>
    <span style="width:1px;height:16px;background:#33456b;margin:0 2px"></span>
    <span style="color:#7a86a0;font-size:.7rem">Work nodes:</span>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('node:mine')" title="Mining node">⛏️</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('node:woodcut')" title="Woodcutting node">🪓</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('node:farm')" title="Farming node">🌾</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('node:fish')" title="Fishing spot">🎣</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('node:build')" title="Build site">🔨</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('node:hunt')" title="Hunting grounds">🏹</button>
    <span style="width:1px;height:16px;background:#33456b;margin:0 2px"></span>
    <span style="color:#7a86a0;font-size:.7rem">Nature:</span>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('landmark:tree_green')" title="Green tree">🌲</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('landmark:tree_autumn')" title="Autumn tree">🍂</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('landmark:tree_yellow')" title="Yellow tree">🌳</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('landmark:well')" title="Well">💧</button>
    <span style="width:1px;height:16px;background:#33456b;margin:0 2px"></span>
    <span style="color:#7a86a0;font-size:.7rem" title="Place on a building's interior tiles (zoom in to see). Doors may sit on the wall as openings.">Interior:</span>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('interior:door')" title="Interior door / wall opening (walkable)">🚪</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('interior:window')" title="Window">🪟</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('interior:object')" title="Furniture piece">🪑</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('interior:plant')" title="Potted plant">🪴</button>
    <button class="btn" style="padding:4px 6px" onclick="worldEditAdd('interior:crate')" title="Crate">📦</button>
    <span style="width:1px;height:16px;background:#33456b;margin:0 2px"></span>
    <button class="btn" style="padding:4px 8px;border-color:#7c3a3a" onclick="worldEditAdd('erase')" title="Click any object to remove it">🧹 Erase anything</button>
    <span style="width:1px;height:16px;background:#33456b;margin:0 2px"></span>
    <button class="btn" style="padding:4px 8px;border-color:#7c3a3a" onclick="worldEditDelete()">🗑️ Delete</button>
    <button class="btn" style="padding:4px 8px;background:#2a5a3a" onclick="worldEditSave()">💾 Save</button>
    <label style="display:inline-flex;align-items:center;gap:4px;cursor:pointer;color:#9fb0cc;font-size:.72rem" title="Automatically persist map edits a moment after each change (debounced)">
      <input type="checkbox" id="world-autosave-cb" onchange="worldToggleAutosave(this.checked)" checked> Auto-save</label>
    <span id="world-autosave-note" style="color:#5db07a;font-size:.68rem;min-width:44px"></span>
    <button class="btn" style="padding:4px 8px" onclick="worldEditReset()">↺ Reset map</button>
  </div>
  <div id="world-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:900;align-items:center;justify-content:center" onclick="if(event.target===this)worldCloseModal()">
    <div style="background:#0f1626;border:1px solid #2a3752;border-radius:12px;max-width:780px;width:92%;max-height:82vh;overflow:auto;padding:18px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <div id="world-modal-title" style="font-weight:700;color:#e8eefc"></div>
        <button class="btn" style="padding:4px 10px" onclick="worldCloseModal()">✕</button>
      </div>
      <div id="world-modal-tabs" style="display:none;gap:4px;flex-wrap:wrap;margin:-2px 0 10px;padding-bottom:8px;border-bottom:1px solid #1b2740"></div>
      <div id="world-modal-body" style="white-space:pre-wrap;font-size:.78rem;color:#c7d2e5;font-family:ui-monospace,monospace;margin:0"></div>
    </div>
  </div>
  </div>`;
  document.getElementById('main-content').innerHTML = h;
  // The HUD overlay (toolbar + panels) comes from world-hud.js, loaded on demand
  // (index.html is untouched). It hosts every control the old view-header had —
  // same element ids — plus the panel toggles; failure degrades to a bare canvas.
  try { await _loadWorldHud(); } catch (e) { console.error('[world] HUD failed to load:', e); }
  if (window.WHUD) { try { WHUD.init(document.getElementById('world-stage')); } catch (e) { console.error('[world] HUD init:', e); } }
  if (window.worldGodRefreshBadge) worldGodRefreshBadge();   // God Console pending-prayer badge
  { const sb = document.getElementById('world-snd-btn');
    if (sb && window.WAU && !WAU.on) sb.textContent = '🔇'; }

  _worldLoad('Loading sprites & tiles…');
  if (window.WA) { try { await WA.init(); } catch {} }   // downloaded tilesets (fallback if absent)
  if (window.WB) { try { await WB.init(); } catch {} }   // Kenney building wall tiles (autotiled ring)
  if (window.WN) { try { await WN.init(); } catch {} }   // ambient townsfolk sprites
  if (window.WMob) { try { await WMob.init(); } catch {} } // raid monster sprites (system D)
  let _lay = null, _wearSaved = null;
  // Flicker fix: kick BOTH the layout + generated-terrain-image fetches together, then
  // (if a terrain image is enabled + present) PRELOAD + decode it and hand it to WM
  // BEFORE build(). That way the single build()→_bake() paints the image directly — no
  // visible procedural→image second bake on load. (Post-generation live swaps still use
  // the async setTerrainImage(url) path, where a swap is expected.)
  const layP = api('/api/world/layout');
  const hqP = api('/api/world/hq/stages').catch(() => null);   // HQ progression stages (era snapshots)
  const terrP = api('/api/world/terrain').catch(() => null);
  const floorP = api('/api/world/floor').catch(() => null);   // Layer-2b: shared interior-floor texture
  const moonP = api('/api/world/moon').catch(() => null);     // sky: moon texture + enable/daytime flags
  try {
    const mn = await moonP;
    if (mn) {
      window._wskyMoonOn = mn.enabled !== false;
      window._wskyMoonDay = !!mn.daytime;
      const murl = mn.has_image && mn.url ? '/store/static/' + mn.url : null;
      if (window.WSKY && WSKY.setMoonImage) WSKY.setMoonImage(murl);       // the moon disc in the sky
      if (window.WMOON && WMOON.setMoonTexture) WMOON.setMoonTexture(murl); // and the lunar-map ground
    }
  } catch {}
  try { const lr = await layP; _lay = lr?.layout; _wearSaved = lr?.wear; } catch {}
  // HQ stage: hand the ACTIVE era to WM before build() so the single bake already
  // renders the right footprint (compound Iron/Steel Age vs the founding block).
  try {
    const hs = await hqP;
    if (hs && Array.isArray(hs.stages) && WM.setHqStage) {
      window._hqStages = hs;
      WM.setHqStage(hs.stages.find(s => s.id === hs.active) || null);
    }
  } catch {}
  try {
    const tr = await terrP;
    if (tr && tr.enabled && tr.has_image && tr.url) {
      const url = '/store/static/' + tr.url;
      const img = new Image(); img.src = url;
      await img.decode();
      if (WM.setTerrainImageEl) WM.setTerrainImageEl(img, url);   // remember url → self-heal can re-decode after eviction
    } else if (WM.setTerrainImageEl) { WM.setTerrainImageEl(null, null); }
  } catch {}
  try {
    const fr = await floorP;
    if (fr && fr.enabled && fr.has_image && fr.url) {
      const url = '/store/static/' + fr.url;
      const img = new Image(); img.src = url;
      await img.decode();
      if (WM.setFloorImageEl) WM.setFloorImageEl(img, url);     // preloaded → single bake paints it directly
    } else if (WM.setFloorImageEl) { WM.setFloorImageEl(null, null); }
  } catch {}
  _worldLoad('Building the town…');
  WM.build(_lay);                                         // ONE bake — already sees _terrainImg/_floorImg when preloaded
  if (_wearSaved && WM.loadWear) WM.loadWear(_wearSaved);  // resume the town's worn trails
  // play-god auto-save toggle: default ON; reflect the saved world_layout_autosave
  try { const cfg = await api('/api/world/settings'); const s = cfg?.settings || {};
        const on = (s.world_layout_autosave ?? '1') !== '0';
        window._wmLayoutAutosave = on; const cb = document.getElementById('world-autosave-cb'); if (cb) cb.checked = on;
        const rf = parseFloat(s.world_roof_fade_zoom); window._wmRoofFade = (isFinite(rf) && rf > 0) ? rf : 1.15;
        const nb = parseFloat(s.world_night_brightness); window._wmNightBright = (isFinite(nb) && nb >= 0) ? nb : 1; }
  catch { window._wmLayoutAutosave = true; window._wmRoofFade = 1.15; }
  if (window.WN && WN.ready) WN.spawn(12);               // populate the town with wanderers
  if (window.WW) WW.spawn(() => {                        // wildlife + who scares it
    const w = Object.values(_sprites).map(s => ({ x: s.px, y: s.py }));
    if (window.WN && WN.positions) w.push(...WN.positions());
    return w;
  });
  if (window.WF) (WF.restore || WF.reset)();             // resume the production tallies
  const canvas = document.getElementById('world-canvas');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const ctx = canvas.getContext('2d');
  function _resize() {
    const cssW = canvas.clientWidth, cssH = canvas.clientHeight;
    canvas.width = cssW * dpr; canvas.height = cssH * dpr;
    canvas._cssW = cssW; canvas._cssH = cssH;
  }
  _resize();
  // The canvas is now 100%×100% of the tab (no fixed 600px), so window/layout
  // resizes must re-sync the backing store. Debounced; the RAF loop repaints
  // the very next frame. Camera framing is intentionally left alone.
  { let t = null;
    const ro = new ResizeObserver(() => {
      clearTimeout(t);
      t = setTimeout(() => { if (document.getElementById('world-canvas')) _resize(); }, 120);
    });
    ro.observe(canvas);
    _worldView.ro = ro; }   // parked on the OLD view object; carried into the new one below
  if (!_restoreCamera(canvas)) _worldZoomHome(canvas);   // resume framing, else zoom on HQ
  ctx.imageSmoothingEnabled = false;
  if (window.WMOON) WMOON.init(canvas);                  // moon-map: click-the-moon travel + Return-to-Earth button
  const _wpt = ev => { const r = canvas.getBoundingClientRect(); return WM.screenToWorld(ev.clientX - r.left, ev.clientY - r.top); };
  WM.attachControls(canvas, {
    isEditing: () => _edit.on,
    onEditDown: ev => {                       // returns true to consume (skip camera pan)
      const w = _wpt(ev), tile = WM.worldToTile(w.x, w.y);
      if (_edit.add) { _placeAdd(tile, w); return true; }
      // grab priority: a PERSON first (RCT-style pick-up), then building, then point-entity
      if (!_edit.add) {
        let gid = null, gd = 20;
        for (const id in _sprites) { const s = _sprites[id]; const d = Math.hypot((s.px || 0) - w.x, (s.py || 0) - w.y - 8); if (d < gd) { gd = d; gid = id; } }
        if (gid != null) { const s = _sprites[gid]; _edit.agentDrag = { id: +gid, name: s.agent?.name || 'someone' }; _edit.agentGhost = { x: w.x, y: w.y, id: +gid }; return true; }
      }
      // SMALL THINGS FIRST — decor, nodes, landmarks under the cursor grab before
      // any building does, so items inside rooms are editable without yanking
      // the whole structure around.
      let i;
      // agents' bought furniture (world_placements) grabs like any small item
      const plc = (typeof _placementNear === 'function') && _placementNear(w.x, w.y);
      if (plc) { _edit.pdrag = { type: 'placement', p: plc.p }; _edit.pghost = { type: 'placement', p: plc.p, x: w.x, y: w.y }; return true; }
      // agent-built structures (world_structures) grab like any small item
      const str = (typeof _structureNear === 'function') && _structureNear(w.x, w.y);
      if (str) { _edit.pdrag = { type: 'structure', s: str.s }; _edit.pghost = { type: 'structure', s: str.s, x: w.x, y: w.y }; return true; }
      if ((i = WM.decorIndexNear(w.x, w.y)) >= 0) { const h = WM.pickDecor(i); _edit.pdrag = { type: 'decor', kind: h.kind }; _edit.pghost = { type: 'decor', kind: h.kind, x: w.x, y: w.y }; return true; }
      if ((i = WM.nodeIndexNear(w.x, w.y)) >= 0) { const h = WM.pickNode(i); _edit.pdrag = { type: 'node', kind: h.kind }; _edit.pghost = { type: 'node', kind: h.kind, x: w.x, y: w.y }; return true; }
      if ((i = WM.landmarkIndexNear(w.x, w.y)) >= 0) { const h = WM.pickLandmark(i); _edit.pdrag = { type: 'landmark', kind: h.kind, scale: h.scale }; _edit.pghost = { type: 'landmark', kind: h.kind, x: w.x, y: w.y }; return true; }
      // BUILDINGS move only when grabbed by their WALL (edge tiles) — clicking
      // furniture mid-room no longer drags the whole building away.
      const b = WM.buildingAtTile(tile.col, tile.row);
      if (b) {
        const onEdge = tile.col === b.c || tile.col === b.c + b.w - 1 ||
                       tile.row === b.r || tile.row === b.r + b.h - 1;
        if (onEdge) { _edit.sel = b.id; _edit.drag = { off: { c: tile.col - b.c, r: tile.row - b.r } }; _updateEditSel(); return true; }
        if (_edit.sel !== b.id) { _edit.sel = b.id; _updateEditSel(); }   // interior click = select only
        return true;
      }
      _edit.sel = null; _updateEditSel(); return false;   // empty space → let camera pan
    },
    onEditMove: ev => {
      if (_edit.agentDrag) { const w = _wpt(ev); _edit.agentGhost = { ..._edit.agentGhost, x: w.x, y: w.y }; return true; }
      if (_edit.pdrag) { const w = _wpt(ev); _edit.pghost = { ..._edit.pghost, x: w.x, y: w.y }; return true; }
      if (_edit.add && _edit.add.indexOf('interior:') === 0) {           // Layer-3: tile-snap ghost so you see where the item lands
        const w = _wpt(ev), t = WM.worldToTile(w.x, w.y);
        _edit.addGhost = { col: t.col, row: t.row, add: _edit.add };     // (no return — add mode never pans)
      }
      if (!_edit.drag || _edit.sel == null) return false;
      const w = _wpt(ev), tile = WM.worldToTile(w.x, w.y);
      const b = WM.buildings.find(x => x.id === _edit.sel); if (!b) return false;
      _edit.ghost = { c: tile.col - _edit.drag.off.c, r: tile.row - _edit.drag.off.r, w: b.w, h: b.h };
      return true;
    },
    onEditUp: () => {
      if (_edit.agentDrag && _edit.agentGhost) {            // drop the PERSON on a task or spot
        const g = _edit.agentGhost, t = WM.worldToTile(g.x, g.y);
        const ni = WM.nodeIndexNear(g.x, g.y);
        let assign;
        if (ni >= 0) { const n = WM.nodes[ni]; assign = { location: n.kind, kind: 'skill', col: n.col, row: n.row }; }
        else { assign = { location: 'spot', kind: 'spot', col: t.col, row: t.row }; }
        worldDropAgent(_edit.agentDrag.id, _edit.agentDrag.name, assign);
        _edit.agentDrag = null; _edit.agentGhost = null;
      }
      if (_edit.pdrag && _edit.pghost) {                    // drop the held point-entity at the ghost
        const g = _edit.pghost, t = WM.worldToTile(g.x, g.y);
        if (g.type === 'placement') worldMovePlacement(g.p, g.x, g.y);   // placements auto-save via their own endpoint
        else if (g.type === 'structure') worldMoveStructure(g.s, g.x, g.y);  // structures auto-save via /structure/move
        else if (g.type === 'decor') { WM.addDecor(g.x, g.y, g.kind); WM.scheduleSave(); }
        else if (g.type === 'node') { WM.addNode(g.kind, t.col, t.row); WM.scheduleSave(); }
        else if (g.type === 'landmark') { WM.addLandmark(g.kind, t.col, t.row, _edit.pdrag.scale); WM.scheduleSave(); }
      }
      if (_edit.drag && _edit.ghost && _edit.sel != null) { WM.moveBuilding(_edit.sel, _edit.ghost.c, _edit.ghost.r); WM.scheduleSave(); }
      _edit.drag = null; _edit.ghost = null; _edit.pdrag = null; _edit.pghost = null; _edit.agentDrag = null; _edit.agentGhost = null;
    },
  });
  window._worldResize = _resize;

  canvas.addEventListener('click', ev => {
    if ((canvas._dragMoved || 0) > 5) { canvas._dragMoved = 0; return; }  // was a pan/drag
    if (_edit.on) return;                       // edit mode handles its own selection
    const w = _wpt(ev);
    let best = null, bestD = 28;                  // generous hit radius — agents are small + moving
    for (const id in _sprites) {
      const s = _sprites[id];
      const d = Math.hypot((s.px || 0) - w.x, (s.py || 0) - w.y - 8);
      if (d < bestD) { bestD = d; best = id; }
    }
    _selectedId = best ? +best : null;
    if (best != null && window.WHUD) WHUD.open('agent');   // clicking a citizen surfaces their card
    // no agent under the cursor → maybe a VENUE: each wired building opens its
    // matching system (⛪→Jesus · 🍺→Satan · 🎓→knowledge hub · 🏛️→Republic · 👔→Board).
    if (best == null) {
      const t = WM.worldToTile(w.x, w.y);
      const b = WM.buildingAtTile(t.col, t.row);
      if (b && _openVenue(b)) return;
    }
    _renderDetail();
    if (window.WHUD) WHUD.onState(_worldState);            // refresh portrait/skills for the new selection
  });

  const thinkBtn = document.getElementById('world-think-btn');
  if (thinkBtn) thinkBtn.onclick = async () => {
    thinkBtn.disabled = true; thinkBtn.textContent = '💭…';
    try { await api('/api/world/think', { method: 'POST', body: JSON.stringify(_selectedId ? { agent_id: _selectedId } : {}) }); await _pollWorld(); }
    catch (e) { toast?.(e.message); }
    thinkBtn.disabled = false; thinkBtn.textContent = '💭';
  };

  // No automatic LLM polling — thoughts/opinions come from the backend's scheduled
  // cognition batch (settable, hourly). The 💭/💡 buttons remain for on-demand use.
  _worldView = { canvas, ctx, ro: _worldView.ro };
  _startWorldLoops();            // poll + RAF; visibilitychange pauses/resumes them
}
window.renderWorld = renderWorld;

/* ── venue → system wiring: clicking a building opens the surface it embodies ──
   ⛪ Church → ✝️ Jesus console · 🍺 Bar → 😈 Satan console · 🎓 University →
   the knowledge hub (Library/Research/Graph/Live-Docs) · 📚 Library → the hub's
   library pane · 🏛️ Town Hall → the Republic · 👔 Boss's Office → the Board.
   The 🔞 store is INERT unless the layered NSFW gate is on (then → Satan's
   console, where its oversight ledger lives). Returns true when consumed. */
function _openVenue(b) {
  try {
    switch (b.loc) {
      case 'church':   if (window.worldConsole) { worldConsole('jesus'); return true; } break;
      case 'bar':      if (window.worldConsole) { worldConsole('satan'); return true; } break;
      case 'school':   if (typeof switchView === 'function') { switchView('knowledge'); return true; } break;
      case 'library':  if (typeof switchView === 'function') { switchView('library'); return true; } break;
      case 'townhall': if (window.worldConsole) { worldConsole('republic'); return true; } break;
      case 'exec':     if (window.worldConsole) { worldConsole('board'); return true; } break;
      case 'research': if (typeof switchView === 'function') { switchView('research'); return true; } break;
      case 'nsfw':                             // GATED: boarded-up store does nothing at all
        if (window._wmNsfwOn === true && window.worldConsole) { worldConsole('satan'); return true; }
        break;
    }
  } catch (e) { /* venue wiring must never break map clicks */ }
  return false;
}

function worldRecenter() {   // fit the whole city
  const cv = document.getElementById('world-canvas');
  if (cv) WM.fit(cv._cssW || cv.clientWidth, cv._cssH || cv.clientHeight);
}
function _worldZoomHome(cv) {  // zoomed-in view centred on HQ (shows pixel detail)
  const vpW = cv._cssW || cv.clientWidth, vpH = cv._cssH || cv.clientHeight;
  WM.fit(vpW, vpH);
  WM.camera.scale = Math.min(2.4, WM.camera.scale * 2.1);
  WM.camera.x = vpW / 2 - (WM.W / 2) * WM.camera.scale;
  WM.camera.y = vpH / 2 - (WM.H / 2) * WM.camera.scale;
}
window.worldRecenter = worldRecenter;

let _wearPushN = 0;
let _sndPrev = { bless: null, phase: null };
async function _pollWorld() {
  try {
    const st = await api('/api/world/state');
    _worldState = st;
    // 😈 NSFW-store gate flag (server ANDs world_satan_nsfw_domain + nsfw.world_active();
    // absent/old backend → undefined → treated as OFF everywhere). Read-only mirror.
    window._wmNsfwOn = (st.nsfw_store_open === true);
    // naming-theme sync (display labels only — world-theme.js). Absent/old
    // backend → undefined → WTheme keeps its current (default themed) value.
    if (st.naming_theme && window.WTheme) WTheme.set(st.naming_theme);
    _assignHouses(st.agents);
    // push accumulated foot-traffic to the server (~every 60s) so trails persist
    if (++_wearPushN % 20 === 0 && WM.takeWearDirty) {
      const d = WM.takeWearDirty();
      if (Object.keys(d).length)
        api('/api/world/wear', { method: 'POST', body: JSON.stringify({ updates: d }) }).catch(() => {});
    }
    // sound: feed the dynamic soundscape + cue transitions (chime, alarm)
    if (window.WAU) {
      WAU.update(st);
      const cv = document.getElementById('world-canvas');
      if (cv && WM.waterTiles) {                       // water plips when the camera is near a pond
        const cam = WM.camera, vw = cv._cssW || cv.clientWidth, vh = cv._cssH || cv.clientHeight;
        const cx = (vw / 2 - cam.x) / cam.scale, cy = (vh / 2 - cam.y) / cam.scale, R = 230;
        WAU.updateCam(WM.waterTiles.some(t =>
          Math.abs((t.col + 0.5) * WM.TILE - cx) < R && Math.abs((t.row + 0.5) * WM.TILE - cy) < R));
      }
      const blessedNow = new Set(st.agents.filter(a => a.blessed).map(a => a.key));
      if (_sndPrev.bless) {
        for (const a of st.agents) {                    // blessing chimes AT the blessed one
          if (!a.blessed || _sndPrev.bless.has(a.key)) continue;
          const s = _sprites[a.id];
          if (s && WAU.sfxAt) WAU.sfxAt('bless', s.px, s.py, 3000); else WAU.sfx('bless', 3000);
        }
      }
      const ph = st.orchestra?.phase;
      if (ph === 'raid' && _sndPrev.phase && _sndPrev.phase !== 'raid') WAU.sfx('raid', 5000);  // alarm is town-wide by design
      // per-agent transitions: shop ka-ching at their store, level-up fanfare at them
      const ag = _sndPrev.ag || (_sndPrev.ag = {});
      for (const a of st.agents) {
        const pv = ag[a.id], s = _sprites[a.id];
        if (pv && WAU.sfxAt) {
          if (a.state === 'shopping' && pv.state !== 'shopping') {
            const g = _agentTile(a), p = WM.tileToPx(g.col, g.row);
            WAU.sfxAt('shop', p.x, p.y, 800);
          }
          if ((a.level || 0) > pv.level && s) WAU.sfxAt('levelup', s.px, s.py, 900);
        }
        ag[a.id] = { state: a.state, level: a.level || 0 };
      }
      _sndPrev.bless = blessedNow; _sndPrev.phase = ph;
    }
    // sync sprites — each agent paths across the tile grid to its current place
    const seen = {};
    for (const a of st.agents) {
      seen[a.id] = true;
      const goal = _agentTile(a);
      const key = `${a.location}@${goal.col},${goal.row}`;
      let s = _sprites[a.id];
      if (!s) {
        const p = WM.tileToPx(goal.col, goal.row);
        _sprites[a.id] = { col: goal.col, row: goal.row, px: p.x, py: p.y, path: [], targetKey: key,
                           agent: a, bob: 0, off: _spriteOffset(a) };
      } else {
        s.agent = a;
        if (s.targetKey !== key) {
          s.targetKey = key;
          const path = WM.findPath({ col: s.col, row: s.row }, goal);
          // COLLISION: no path → stay put and retry next poll. (The old
          // fallback beelined [goal], walking straight through walls.)
          s.path = path ? path.slice(1) : [];
          if (!path) s.targetKey = null;               // force a re-plan next poll
        }
      }
    }
    for (const id in _sprites) if (!seen[id]) delete _sprites[id];

    // preload generated prop sprites
    for (const pr of (st.props || [])) {
      if (pr.status === 'done' && pr.image_path && !_propImgs[pr.id]) {
        const img = new Image();
        img.src = pr.image_path;
        _propImgs[pr.id] = img;
        const sh = new Image();                          // optional animation sheet
        sh.onerror = () => { sh._missing = true; };
        sh.src = pr.image_path.replace(/\.png$/, '_sheet.png');
        _propSheets[pr.id] = sh;
      }
    }

    // clock + activity
    const clk = document.getElementById('world-clock');
    if (clk) clk.textContent = `🕒 ${st.now.slice(11,16)} · pop ${st.agents.length} · 🪙 ${st.economy?.treasury ?? 0} · 🏦 ${st.economy?.company_fund ?? 0}`;
    const seasonEl = document.getElementById('world-season');
    if (seasonEl && st.orchestra) {
      const o = st.orchestra, PH = { watch: '👁️ watch', raid: '⚔️ RAID', recovery: '🩹 recovery' };
      seasonEl.textContent = `${o.emoji} ${o.season} · day ${o.day}${o.phase && o.phase !== 'peace' ? ' · ' + (PH[o.phase] || o.phase) : ''}`;
      seasonEl.title = o.festival || '';
      seasonEl.style.background = o.phase === 'raid' ? '#3a1620' : '';
    }
    const act = document.getElementById('world-activity');
    if (act) {
      const parts = Object.entries(st.activity || {}).map(([k,v]) => `${k}:${v}`);
      act.textContent = parts.length ? `⚡ working: ${parts.join('  ')}` : '💤 all quiet';
    }
    _renderFeed(st.events || []);
    _renderTownHall(st);
    _renderDetail();
    if (window.WHUD) WHUD.onState(st);   // HUD panels (skills/quests/company/portrait)
  } catch (e) { /* keep last frame */ }
}


/* ── HQ progression stages: view the eras, advance up the ladder ──────────────
   Each stage is an era snapshot of the HQ (world_hq backend). Viewing an
   earlier stage re-renders the HQ with that era's footprint locally + persists
   the choice; advancing is gated on the world_tech tier and archives the look
   being left. All pure data + canvas — no LLM/GPU work. */
function _hqApplyFromList(hs) {
  window._hqStages = hs;
  if (WM.applyHqStage) WM.applyHqStage(hs.stages.find(s => s.id === hs.active) || null);
}
async function worldHqPanel() {
  let hs = null;
  try { hs = await api('/api/world/hq/stages'); } catch (e) { toast?.(e.message); return; }
  window._hqStages = hs;
  const rows = (hs.stages || []).map(s => {
    const state = s.active ? '<span style="color:#5db07a;font-weight:700">● active</span>'
      : s.unlocked ? `<button class="btn" style="padding:3px 10px" onclick="worldHqSetStage(${s.id})">👁 View this era</button>`
      : `<span style="color:#7a86a0">🔒 needs ${esc(s.tier_required_name || '')} tier</span>`;
    const snap = s.snapshot_at ? `<div style="color:#7a86a0;font-size:.68rem">look archived ${esc(s.snapshot_at)}</div>` : '';
    return `<div style="display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px solid #1b2740">
      <div style="font-size:1.3rem">${s.emoji || '🏢'}</div>
      <div style="flex:1;min-width:0">
        <div style="font-weight:700;color:#e8eefc">${esc(s.name)} <span style="color:#7a86a0;font-weight:400;font-size:.72rem">(${esc(s.key)})</span></div>
        <div style="color:#9fb0cc;font-size:.74rem;margin:2px 0">${esc(s.style?.desc || '')}</div>${snap}
      </div>
      <div style="white-space:nowrap">${state}</div>
    </div>`;
  }).join('');
  const adv = hs.next
    ? (hs.next.ready
       ? `<button class="btn" style="background:#2a5a3a;padding:6px 14px" onclick="worldHqAdvance()">🏗️ Advance to the ${esc(hs.next.name)}</button>`
       : `<span style="color:#7a86a0;font-size:.76rem">Next: ${esc(hs.next.name)} — unlocks with the tech-tier progression (currently ${esc(hs.tier_name)}).</span>`)
    : `<span style="color:#7a86a0;font-size:.76rem">The HQ is in its final stage.</span>`;
  const html = `<div style="font-size:.78rem;color:#c7d2e5">
    <div style="color:#9fb0cc;margin-bottom:6px">The HQ evolves through eras. Advancing archives the current look as an earlier
    stage you can always look back on; the tech tier (${esc(hs.tier_name)}) gates the ladder.</div>
    ${rows}
    <div style="margin-top:12px;display:flex;gap:10px;align-items:center">${adv}</div>
  </div>`;
  if (typeof _worldModal === 'function') _worldModal('🏭 HQ Progression Stages', html);
}
async function worldHqSetStage(id) {
  try {
    await api('/api/world/hq/stage', { method: 'POST', body: JSON.stringify({ id }) });
    const hs = await api('/api/world/hq/stages');
    _hqApplyFromList(hs);
    toast?.('HQ stage switched ✓');
    worldHqPanel();
  } catch (e) { toast?.(e.message); }
}
async function worldHqAdvance() {
  try {
    const r = await api('/api/world/hq/advance', { method: 'POST', body: '{}' });
    const hs = await api('/api/world/hq/stages');
    _hqApplyFromList(hs);
    toast?.(`🏗️ HQ advanced to the ${r.stage?.name || 'next stage'} — previous look archived ✓`);
    worldHqPanel();
  } catch (e) { toast?.(e.message); }
}
window.worldHqPanel = worldHqPanel; window.worldHqSetStage = worldHqSetStage;
window.worldHqAdvance = worldHqAdvance;

window.worldToggleEdit = worldToggleEdit; window.worldEditResize = worldEditResize;
window.worldEditAdd = worldEditAdd; window.worldEditDelete = worldEditDelete;
window.worldEditSave = worldEditSave; window.worldEditReset = worldEditReset;
window.worldRename = worldRename; window.worldThink = worldThink;
window.worldWant = worldWant; window.worldBuy = worldBuy;
window.worldMeeting = worldMeeting; window.worldOpinion = worldOpinion;
window.worldLog = worldLog; window.worldCloseModal = worldCloseModal;
window.worldResolveDirective = worldResolveDirective;
window.worldSettings = worldSettings; window.worldSaveSettings = worldSaveSettings;

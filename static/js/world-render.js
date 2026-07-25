/* ══ THE COMPANY — render & agent motion (draw loop, sprites, day/night, props) ══
   Split out of tab-world.js for modularity. Runs in shared global scope
   (classic script, not a module) — same as world-map.js / world-assets.js. */

/* Small deterministic per-agent offset so co-located agents don't fully overlap.
   Decorrelated hash fan-out (not id%5, which only gave 5 distinct offsets for
   39 agents and stacked every 5th agent pixel-perfect on top of each other):
   spread ~6-20px out at a per-agent angle so a crowd reads as a crowd. */
function _spriteOffset(a) {
  const h = (a.id * 2654435761) >>> 0;                 // Knuth multiplicative hash
  const ang = (h % 360) * Math.PI / 180;
  const rad = 6 + ((h >>> 9) % 14);
  return { x: Math.cos(ang) * rad, y: Math.sin(ang) * rad };
}

/* How fast an agent ambles — matched to what they're doing so the motion reads
   as purposeful, and slow enough that you can actually click them. */
function _agentSpeed(s) {
  const st = s.agent && s.agent.state;
  if (st === 'defending' || (s.agent && s.agent.role && s.agent.role !== 'downed')) return 1.9;  // raid urgency
  if (st === 'leisure' || st === 'idle' || st === 'breakdown' || st === 'downed'
      || st === 'sitting' || st === 'picnicking' || st === 'admiring') return 0.85;                // slow amble/mosey (downed = carried off, not sprinting)
  return 1.25;                                                                                     // normal walk to work
}

/* Advance every agent along its A* path at its state-appropriate speed. */
function _stepAgents(dt) {
  for (const id in _sprites) {
    const s = _sprites[id];
    if (s.path && s.path.length) {
      const next = s.path[0], tp = WM.tileToPx(next.col, next.row);
      const last = s.path.length === 1;
      // apply a fraction of the offset en route too, not just on arrival — otherwise
      // a cohort walking the same route overlaps pixel-for-pixel until the last tile
      const tx = tp.x + (last ? s.off.x : s.off.x * 0.35), ty = tp.y + (last ? s.off.y : s.off.y * 0.35);
      const dx = tx - s.px, dy = ty - s.py, dist = Math.hypot(dx, dy);
      if (Math.abs(dx) > Math.abs(dy) + 0.5) s.dir = dx < 0 ? 'left' : 'right';
      else if (Math.abs(dy) > 0.5) s.dir = dy < 0 ? 'up' : 'down';
      const step = _agentSpeed(s) * WM.TILE * dt;
      if (dist <= step) { s.px = tx; s.py = ty; s.col = next.col; s.row = next.row; s.path.shift();
                          if (WM.bumpWear) WM.bumpWear(next.col, next.row); }   // feet carve desire lines
      else { s.px += dx / dist * step; s.py += dy / dist * step; }
      s.bob += dt * 6; s.moving = true;
    } else { s.moving = false; }
  }
}

/* ── DRAW (camera-transformed tile world) ── */
function _drawWorld(ctx, canvas) {
  if (window.WMOON && WMOON.active()) { WMOON.draw(ctx, canvas); return; }   // on the Moon → the lunar map replaces the town
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cam = WM.camera;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#070b12'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (window.WSKY) WSKY.drawSpace(ctx, canvas, cam.scale);   // starfield behind the world when zoomed out to orbit
  // PLANET: zoomed out to orbit → clip the whole world (+ season/lights) to a circle so the
  // town reads as a round planet; the atmosphere rim + curved clouds go over it afterward.
  const _planetOn = !!(window.WSKY && WSKY.planetAmount && WSKY.planetAmount(cam.scale) > 0);
  if (_planetOn) { const pc = WSKY.planetCircle(canvas); ctx.save(); ctx.beginPath(); ctx.arc(pc.cx, pc.cy, pc.r, 0, 6.283); ctx.clip(); }
  ctx.setTransform(dpr * cam.scale, 0, 0, dpr * cam.scale, dpr * cam.x, dpr * cam.y);  // camera
  ctx.imageSmoothingEnabled = false;

  // positional audio: the camera centre is the listener, every frame
  if (window.WAU && WAU.setListener) {
    const vw = canvas._cssW || canvas.clientWidth, vh = canvas._cssH || canvas.clientHeight;
    WAU.setListener((vw / 2 - cam.x) / cam.scale, (vh / 2 - cam.y) / cam.scale, cam.scale);
  }
  _actionSfx();                          // per-action foley at each agent's spot

  WM.drawTerrain(ctx, canvas);
  _drawWear(ctx);                        // desire lines — trampled grass → dirt → cobbled road
  if (window.WSKY) WSKY.drawGroundShadow(ctx);   // moon's shadow sweeps the ground (world space → scales with zoom)
  _drawWater(ctx);                       // live shimmer on ponds + fountains
  _drawBuildingDepth(ctx);               // drop shadows + eaves trim so lots read 3-D
  _drawBuildingSprites(ctx);             // real Kenney building sprites over the procedural lots
  _drawHQInterior(ctx);                  // department nameplates + themed gear inside the HQ rooms (replaces the old desk markers)
  _drawWallArt(ctx);                     // real store-generated images hung as wall art
  _drawPlacements(ctx);                  // furniture/yard pieces the agents bought (item economy)
  _drawDoors(ctx);                       // doors swing open when someone approaches (trigger)
  _drawProps(ctx);
  _drawLandmarks(ctx);
  _drawNodes(ctx);                        // resource nodes (woodcut/mine/farm/fish/build)
  _drawConstruction(ctx);                 // ghost-build structures rising at the build site
  _drawWalls(ctx);                        // defensive walls during a raid (under the agents)
  if (window.WW) WW.draw(ctx);            // wildlife (birds/butterflies/rabbits, under everyone)
  if (window.WN) WN.draw(ctx);            // ambient townsfolk (behind the job-agents)

  // agents, depth-sorted by y so nearer ones draw on top
  const list = Object.values(_sprites).sort((a, b) => a.py - b.py);
  for (const s of list) _character(ctx, s, s.moving);
  if (window.WF) WF.draw(ctx);           // products flowing through the department pipeline
  if (window.WROCKET) WROCKET.draw(ctx); // JASA launch pad + rocket ferrying agents to the Moon ("to the moon")
  _drawThreats(ctx);                     // raid monsters (on top of the melee)
  _drawDefeatFx(ctx);                    // ⚔️ poof + label when a threat is slain
  _drawRoofs(ctx);                       // pseudo-3D roofs — solid when zoomed out, fade away close-up
  if (WM.drawWallBands) WM.drawWallBands(ctx, 1 - _roofAlpha());   // crisp per-building walls fade IN as the roofs fade OUT
  if (WM.drawInterior) WM.drawInterior(ctx, 1 - _roofAlpha());     // Layer-3 per-tile interior edits (doors/windows/objects) reveal with the walls
  WM.drawBuildingLabels(ctx);            // name pills stay readable on top of the roofs
  _drawShieldDome(ctx);                  // HQ energy shield — strength = the REAL defenses online

  if (_selectedId && _sprites[_selectedId]) {
    const s = _sprites[_selectedId];
    if (s.agent.mood) _bubble(ctx, Math.round(s.px), Math.round(s.py) - 26, s.agent.mood);
  }
  _drawSeason(ctx, canvas);
  _drawLights(ctx, canvas);
  if (_planetOn) ctx.restore();                              // end planet clip → sky layers draw AROUND the globe
  if (window.WSKY && WSKY.drawAtmosphere) WSKY.drawAtmosphere(ctx, canvas, cam.scale);   // blue rim + limb shadow
  if (window.WSKY) WSKY.drawMoon(ctx, canvas, cam.scale);   // the moon drifts across the sky at night (screen space, zoomed-out)
  if (window.WSKY) WSKY.drawClouds(ctx, canvas, cam.scale); // clouds you pass through between orbit and town (over the moon)
  if (_edit.on) _drawEditOverlay(ctx);
  // subtle vignette (screen space) to frame the scene — cached, rebuilt only when the
  // canvas or its backing-store size changes (was reallocated every frame).
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  if (_vgCanvas !== canvas || _vgW !== canvas.width || _vgH !== canvas.height) {
    _vgGrad = ctx.createRadialGradient(canvas.width / 2, canvas.height / 2, canvas.height * 0.35, canvas.width / 2, canvas.height / 2, canvas.width * 0.72);
    _vgGrad.addColorStop(0, 'rgba(0,0,0,0)'); _vgGrad.addColorStop(1, 'rgba(0,0,0,.26)');
    _vgCanvas = canvas; _vgW = canvas.width; _vgH = canvas.height;
  }
  ctx.fillStyle = _vgGrad; ctx.fillRect(0, 0, canvas.width, canvas.height);
}
let _vgGrad = null, _vgCanvas = null, _vgW = 0, _vgH = 0;

/* Subtle seasonal colour wash (screen space) from the orchestrator's tint, plus a
   red pulse during a RAID so the whole town reads as under threat. */
function _drawSeason(ctx, canvas) {
  const o = _worldState?.orchestra; if (!o) return;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  if (o.tint) {
    ctx.globalCompositeOperation = 'soft-light';
    ctx.fillStyle = `rgba(${o.tint[0]},${o.tint[1]},${o.tint[2]},0.18)`;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.globalCompositeOperation = 'source-over';
  }
  if (o.phase === 'raid') {
    const pulse = 0.10 + 0.06 * Math.sin(performance.now() / 320);
    ctx.fillStyle = `rgba(190,30,40,${pulse})`;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  } else if (o.phase === 'watch') {
    // amber unease while the security desk is watching a failing system
    const pulse = 0.05 + 0.03 * Math.sin(performance.now() / 700);
    ctx.fillStyle = `rgba(220,150,30,${pulse})`;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }
}

/* Time-of-day lighting driven by the sim clock: the town darkens toward night with
   a colour shift, and lamps / lit windows glow. */
function _daylight(h) {
  // Night darkening lightened so the ground/terrain, walls and agents stay clearly
  // readable at night (was dark 0.55 → near-black; dusk 0.55). Tunable via the
  // `world_night_brightness` setting (0..1, default 1 = these values; lower = darker).
  const nb = (window._wmNightBright != null ? window._wmNightBright : 1);
  if (h >= 9 && h < 18) return { dark: 0, tint: [18, 26, 64] };                    // day
  if (h >= 6 && h < 9) return { dark: (9 - h) / 3 * 0.22 * nb, tint: [95, 60, 38] };   // dawn (warm)
  if (h >= 18 && h < 21) return { dark: (h - 18 + 1) / 3 * 0.34 * nb, tint: [70, 38, 58] }; // dusk (purple)
  return { dark: 0.34 * nb, tint: [22, 30, 66] };                                 // night (readable)
}

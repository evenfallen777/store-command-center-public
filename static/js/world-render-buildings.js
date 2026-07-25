/* ══ THE COMPANY — building sprites, HQ interior, doors, depth, roofs, lights, wall art ══
   Split out of world-render.js (core keeps the loop + shared state). Runs in
   shared global scope (classic script, not a module). Loads right after
   world-render.js, before world-ui.js. Code moved verbatim. */


/* Real building sprites (Kenney) over the procedural lots — so shops/homes/venues
   read as actual buildings. Live-drawn (async sprites); box stays as the fallback. */
const _BLD_SPRITE = { bar: 'bld_tavern', arcade: 'bld_arcade', cafe: 'bld_cafe', tv: 'bld_shop_1',
                      lounge: 'bld_shop_1', church: 'bld_church', library: 'bld_library',
                      townhall: 'bld_townhall', exec: 'bld_shop_2', gas: 'bld_shop_2',
                      research: 'bld_library',
                      // the four standalone dept buildings reuse existing pack sprites as a fallback
                      mail: 'bld_shop_2', homelab: 'bld_shop_1', pearl: 'bld_shop_1', assistant: 'bld_shop_2' };
/* Iron/Steel-age restyle: these buildings now have their OWN industrial exteriors
   (riveted flat roofs, smokestacks, brick faces — _ironRoof below), so the generic
   Kenney cottage sprite must NOT paper over them (it was exactly the "everything
   looks like a house" problem). Houses + the HQ keep their sprite chain; explicitly
   generated per-building sprites (WSP building_<id> / era art) still take precedence. */
function _ironStyled(b) { return b.kind !== 'house' && b.kind !== 'hq' && b.kind !== 'church'; }
function _bldSprite(b) {
  if (_ironStyled(b)) return null;                       // the new industrial exterior IS the look
  if (b.loc && _BLD_SPRITE[b.loc]) return _BLD_SPRITE[b.loc];
  if (b.kind === 'house') return (b.id % 2) ? 'bld_house_2' : 'bld_house_1';
  if (b.kind === 'shop') return (b.id % 2) ? 'bld_shop_2' : 'bld_shop_1';
  return null;
}
function _drawBuildingSprites(ctx) {
  if (!(window.WA && WA.hasSprite)) return;
  // exterior-shell layer — fades with the roofs so zooming in reveals the baked
  // interiors (bed/kitchen/office) instead of a flat building image on top.
  const alpha = _roofAlpha();
  if (alpha <= 0.02) return;
  const TL = WM.TILE;
  ctx.save();
  ctx.globalAlpha = alpha;
  for (const b of (WM.buildings || [])) {
    if (b.kind === 'hq') continue;                         // HQ keeps its furnished interior
    const cx = (b.c + b.w / 2) * TL, baseY = (b.r + b.h) * TL - 2;
    const targetH = Math.min(b.h * TL * 1.02, b.w * TL * 1.1);   // fill the lot, keep aspect
    // chain: the CURRENT-ERA generated sprite (wood/brick/…/moon — most specific,
    // reflects the live civilization era) → the building's OWN generated sheet
    // (frame 0 — buildings don't bob) → its shared-kind own sheet → the downloaded
    // pack sprite → procedural lot. drawStatic returns false when absent, so a
    // missing era sprite falls straight through to today's look.
    const _est = WM.eraStyle && WM.eraStyle(b);
    if (window.WSP && WSP.ready && _est && _est.key &&
        WSP.drawStatic(ctx, 'era_' + (b.loc || b.kind) + '_' + _est.key, 'idle', cx, baseY, targetH)) continue;
    if (window.WSP && WSP.ready &&
        (WSP.drawStatic(ctx, 'building_' + b.id, 'idle', cx, baseY, targetH) ||
         WSP.drawStatic(ctx, 'building_' + (b.loc || b.kind), 'idle', cx, baseY, targetH))) continue;
    const name = _bldSprite(b); if (!name || !WA.hasSprite(name)) continue;
    WA.drawSprite(ctx, name, cx, baseY, targetH);
  }
  ctx.restore();
}

/* Department nameplate + themed gear inside each HQ room (drawn live so the async
   Kenney sprites appear once loaded; falls back to a per-dept emoji). */
const _DEPT_EMOJI = { storefront: '🛒', image: '🖼️', video: '🎥', audio: '🔊', models3d: '🖨️',
                      publishing: '📚', devlab: '💻', resell: '📦', trends: '📈',
                      portal: '🌐', social: '📣', finance: '💰', netsec: '🛡️' };
const _DEPT_LABEL = { storefront: 'Store', image: 'Image', video: 'Video', audio: 'Audio', models3d: '3D',
                      publishing: 'Publish', devlab: 'Dev', resell: 'Resell', trends: 'Trends',
                      portal: 'Portal', social: 'Social', finance: 'Finance', netsec: 'NetSec' };
function _drawHQInterior(ctx) {
  const rooms = WM.hqRooms || []; if (!rooms.length) return;
  for (const room of rooms) {
    // small nameplate at the top of the room
    const t = _DEPT_LABEL[room.dept] || room.dept;
    ctx.font = 'bold 6px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
    const w = ctx.measureText(t).width, nx = room.x, ny = room.y0 + 8;
    ctx.fillStyle = 'rgba(10,14,22,.72)'; ctx.fillRect(nx - w / 2 - 2, ny - 6, w + 4, 8);
    ctx.fillStyle = room.tint || '#cfe0ff'; ctx.fillRect(nx - w / 2 - 2, ny - 6, 1.5, 8);
    ctx.fillStyle = '#e6eefc'; ctx.fillText(t, nx, ny);
    // themed gear (dept identity) — real extracted sprite if present, else a per-dept emoji.
    // Sits in the upper-middle, above the machine row (which draws at ~0.60 of the room).
    const gx = room.x, gy = room.y0 + room.h * 0.40, gh = Math.min(room.h * 0.42, 18);
    if (window.WA && WA.hasSprite && WA.hasSprite('gear_' + room.dept)) {
      WA.drawSprite(ctx, 'gear_' + room.dept, gx, gy + gh / 2, gh);
    } else {
      ctx.font = Math.round(gh) + 'px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(_DEPT_EMOJI[room.dept] || '⚙️', gx, gy); ctx.textBaseline = 'alphabetic';
    }
  }
}

/* ── door triggers: doors swing open when anyone comes near ─────────────────── */
const _doorOpen = {};                                        // building id → open?
function _doorTile(b) {
  if (b.door === 'N') return { c: b.c + (b.w / 2 | 0), r: b.r };
  if (b.door === 'W') return { c: b.c, r: b.r + (b.h / 2 | 0) };
  if (b.door === 'E') return { c: b.c + b.w - 1, r: b.r + (b.h / 2 | 0) };
  return { c: b.c + (b.w / 2 | 0), r: b.r + b.h - 1 };
}
function _drawDoors(ctx) {
  const T = WM.TILE, R = T * 1.5;
  const walkers = Object.values(_sprites).map(s => ({ x: s.px, y: s.py }));
  if (window.WN && WN.positions) walkers.push(...WN.positions());
  for (const b of (WM.buildings || [])) {
    const d = _doorTile(b), dx = d.c * T, dy = d.r * T, cx = dx + T / 2, cy = dy + T / 2;
    let open = false;
    for (const w of walkers) {
      if (Math.abs(w.x - cx) < R && Math.abs(w.y - cy) < R) { open = true; break; }
    }
    if (open && !_doorOpen[b.id] && window.WAU) {
      (WAU.sfxAt || WAU.sfx)('door', cx, cy, 900);   // listener is set each frame in _drawWorld
    }
    _doorOpen[b.id] = open;
    if (!open) continue;                                     // baked closed door shows
    // swing open: clear the doorway to floor + door panel against the jamb
    ctx.fillStyle = '#7a5230'; ctx.fillRect(dx + 3, dy + 2, T - 6, T - 3);
    ctx.fillStyle = 'rgba(0,0,0,.25)'; ctx.fillRect(dx + 3, dy + 2, T - 6, 2);
    ctx.fillStyle = '#5b3a22';                                // the swung panel
    if (b.door === 'W' || b.door === 'E') ctx.fillRect(dx + (b.door === 'W' ? 2 : T - 5), dy + 1, 3, T * 0.55);
    else ctx.fillRect(dx + 1, dy + (b.door === 'N' ? 2 : T - 5), T * 0.55, 3);
  }
}

/* Drop shadow + colour-varied eaves trim on every lot, so buildings read as
   standing objects instead of flat outlines (the "everything looks the same
   beige box" fix). Pure overlay — the baked terrain stays untouched. */
const _TRIM_HUES = [14, 205, 355, 95, 265, 30, 180, 320];
function _depthRect(ctx, x, y, w, h, hue) {
  // soft cast shadow bottom + right
  ctx.fillStyle = 'rgba(0,0,0,.22)';
  ctx.fillRect(x + 3, y + h, w, 4);
  ctx.fillRect(x + w, y + 4, 4, h - 1);
  // eaves band along the top wall, hue varied per building
  ctx.fillStyle = `hsla(${hue},45%,42%,.85)`;
  ctx.fillRect(x, y - 2, w, 4);
  ctx.fillStyle = `hsla(${hue},50%,58%,.9)`;
  ctx.fillRect(x, y - 2, w, 1.5);
}
function _drawBuildingDepth(ctx) {
  const TL = WM.TILE;
  for (const b of (WM.buildings || [])) {
    const hue = _TRIM_HUES[(b.id || 0) % _TRIM_HUES.length];
    if (b.sections && b.sections.length) {           // staged compound HQ: depth per WING, not the bbox
      for (const s of b.sections) if (!s.open)
        _depthRect(ctx, (b.c + s.lc) * TL, (b.r + s.lr) * TL, s.w * TL, s.h * TL, 210);   // steel-blue eaves
      continue;
    }
    _depthRect(ctx, b.c * TL, b.r * TL, b.w * TL, b.h * TL, hue);
  }
}

/* ── pseudo-3D roofs ──────────────────────────────────────────────────────────
   The "3D pixel" pass: every building gets a gabled roof + a front wall face
   with windows and a door, as if the camera looks down at a standing building.
   Zoomed OUT the town reads as a real pixel village; zooming IN fades the roofs
   away so the interiors (desks, furniture, agents) the sim depends on stay
   visible. Drawn after agents — anyone indoors is naturally "under the roof". */
const WALL_H = 8, ROOF_OV = 3;   // thinner pseudo-3D front-wall face, consistent with the new thin per-building shell (was 14)
const _ROOF_PAL = {
  house:    [['#a84f35', '#7f3c28', '#c06a4a'], ['#5d6a7c', '#485460', '#75859a'],
             ['#5f7d49', '#4a6338', '#77985c'], ['#8a6a42', '#6d5334', '#a5854f']],
  // Iron/Steel-age slabs: steel/iron greys with a coloured PARAPET TRIM per venue
  // (the trim keeps each building identifiable at a glance from above).
  shop:     [['#5f6570', '#484e58', '#7a828e'], ['#665f58', '#4c4640', '#837a70'], ['#5a626e', '#434a55', '#77808c']],
  leisure:  [['#635c55', '#48423c', '#7f766c']],
  church:   [['#4a4f5c', '#383d48', '#666d7c']],                       // slate + iron for the steeple age
  library:  [['#57544a', '#403e36', '#726e60']],
  townhall: [['#6a6d78', '#4f525c', '#888c99']],
  exec:     [['#5c626e', '#454a54', '#7a808c']],
  research: [['#565b6e', '#404452', '#727890']],
  school:   [['#5f6874', '#474e58', '#7d8894']],
  nsfw:     [['#4a444e', '#35313a', '#5f5864']],
  mail:     [['#665a62', '#4b4248', '#847680']],
  homelab:  [['#4f5a68', '#3a444f', '#697786']],
  pearl:    [['#4f6a62', '#3b504a', '#68877d']],
  assistant:[['#5b5568', '#43404f', '#767085']],
};
const _FACE_PAL = ['#d9c9a8', '#cbb491', '#c2c8ce', '#d1bfae'];
/* brick / steel front-wall faces for the industrial set */
const _FACE_IRON = ['#9a7a5e', '#8f98a4', '#a08066', '#7e8894'];
function _roofAlpha() {
  // Roofs cut away (fade to interiors) MUCH earlier now — you no longer have to
  // zoom almost all the way in to see inside. The fade START is tunable via the
  // `world_roof_fade_zoom` setting (God Console → world), surfaced as
  // window._wmRoofFade; default 1.15x. The reveal spans a 0.4x band above it, so
  // by ~start+0.4 the roof is fully off and desks/agents read clearly.
  const s = WM.camera.scale;
  const start = (window._wmRoofFade != null && window._wmRoofFade > 0) ? window._wmRoofFade : 1.15;
  const end = start + 0.4;
  return s <= start ? 1 : s >= end ? 0 : (end - s) / (end - start);
}
function _drawRoofs(ctx) {
  const alpha = _roofAlpha();
  if (alpha <= 0.02) return;
  const winter = _worldState?.orchestra?.season === 'winter';
  const nglow = Math.min(1, _daylight(_worldState?.clock_hour ?? 12).dark * 1.6);
  const now = performance.now();
  ctx.save();
  ctx.globalAlpha = alpha;
  for (const b of (WM.buildings || [])) {
    const T = WM.TILE, bx = b.c * T, by = b.r * T, bw = b.w * T, bh = b.h * T;
    if (b.kind === 'hq') {
      const est = (WM.eraStyle && WM.eraStyle(b)) || null;
      if (b.sections && b.sections.length) {         // Iron/Steel-Age compound: one flat roof per WING (yard stays open sky)
        for (const s of b.sections) {
          if (s.open) continue;
          const sx = (b.c + s.lc) * T, sy = (b.r + s.lr) * T, sw = s.w * T, sh = s.h * T;
          _flatRoof(ctx, sx, sy, sw, sh, winter, nglow, est);
          if (s.key === 'utilities') {               // industrial smokestack over the boiler house
            const cx2 = sx + sw * 0.5;
            ctx.fillStyle = '#4d4943'; ctx.fillRect(cx2 - 3.5, sy - WALL_H - 16, 7, 16);
            ctx.fillStyle = '#6f6a63'; ctx.fillRect(cx2 - 4.5, sy - WALL_H - 18, 9, 3);
            for (let k = 0; k < 3; k++) {            // working smoke
              const t = ((now / 1600) + k / 3) % 1;
              ctx.fillStyle = `rgba(200,204,212,${(1 - t) * 0.5})`;
              ctx.beginPath();
              ctx.arc(cx2 + Math.sin(t * 5 + k) * 4, sy - WALL_H - 20 - t * 18, 2 + t * 3.2, 0, 6.283);
              ctx.fill();
            }
          }
        }
        continue;
      }
      _flatRoof(ctx, bx, by, bw, bh, winter, nglow, est); continue;
    }
    // CIVILIZATION ERA: age the roof + face palette (null → today's per-kind palette)
    const est = (WM.eraStyle && WM.eraStyle(b)) || null;
    // Iron/Steel-age exteriors: every venue/shop/dept building gets an INDUSTRIAL
    // silhouette (flat riveted roof, sawtooth skylights, stacks, brick face) that
    // matches the HQ compound. Houses keep their gables; the church keeps its
    // steeple (restyled to slate + iron below).
    if (_ironStyled(b)) { _ironRoof(ctx, b, est, winter, now); continue; }
    const pal = _ROOF_PAL[b.kind] || _ROOF_PAL.house;
    const [main, dark, light] = est ? est.roof : pal[(b.id || 0) % pal.length];
    const faceTop = by + bh - WALL_H;
    // FRONT WALL FACE (the vertical surface you'd see standing before the building)
    const face = est ? est.face : _FACE_PAL[(b.id || 0) % _FACE_PAL.length];
    ctx.fillStyle = face; ctx.fillRect(bx, faceTop, bw, WALL_H);
    ctx.fillStyle = 'rgba(0,0,0,.25)'; ctx.fillRect(bx, by + bh - 2, bw, 2);          // base shadow
    const nwin = Math.max(1, Math.min(4, b.w - 3));
    for (let i = 0; i < nwin; i++) {                                                  // windows on the face
      const wx = bx + (i + 0.5) * bw / nwin - 3;
      ctx.fillStyle = '#2c3a52'; ctx.fillRect(wx, faceTop + 4, 6, 6);
      ctx.fillStyle = 'rgba(190,220,255,.5)'; ctx.fillRect(wx + 1, faceTop + 5, 2, 2);
      ctx.fillStyle = '#efe6d2'; ctx.fillRect(wx - 1, faceTop + 10, 8, 1);            // sill
    }
    const dx = bx + bw / 2 - 4;                                                       // door, centred
    ctx.fillStyle = '#5b3a22'; ctx.fillRect(dx, faceTop + 4, 8, WALL_H - 5);
    ctx.fillStyle = '#734a2c'; ctx.fillRect(dx, faceTop + 4, 8, 2);
    ctx.fillStyle = '#e8c14a'; ctx.fillRect(dx + 6, faceTop + 9, 1.5, 1.5);
    // GABLED ROOF above the face (overhangs the footprint slightly)
    const rTop = by - WALL_H - ROOF_OV, rH = (faceTop + 2) - rTop, ridge = rTop + rH * 0.30;
    ctx.fillStyle = dark; ctx.fillRect(bx - ROOF_OV, rTop, bw + 2 * ROOF_OV, ridge - rTop);       // back slope (shaded)
    ctx.fillStyle = main; ctx.fillRect(bx - ROOF_OV, ridge, bw + 2 * ROOF_OV, faceTop + 2 - ridge); // front slope
    ctx.fillStyle = 'rgba(0,0,0,.14)';                                                 // shingle bands
    for (let y = ridge + 4; y < faceTop; y += 5) ctx.fillRect(bx - ROOF_OV, y, bw + 2 * ROOF_OV, 1);
    ctx.fillStyle = light; ctx.fillRect(bx - ROOF_OV, ridge - 1, bw + 2 * ROOF_OV, 2);  // lit ridge cap
    if (est && est.accent === 'neon') { ctx.fillStyle = est.neon || '#3af0d8'; ctx.fillRect(bx - ROOF_OV, ridge, bw + 2 * ROOF_OV, 1); }  // futuristic neon ridge trim
    ctx.fillStyle = 'rgba(0,0,0,.30)'; ctx.fillRect(bx - ROOF_OV, faceTop, bw + 2 * ROOF_OV, 2);  // eaves shadow
    ctx.fillStyle = 'rgba(0,0,0,.18)';                                                 // side trim
    ctx.fillRect(bx - ROOF_OV, rTop, 2, rH + 2); ctx.fillRect(bx + bw + ROOF_OV - 2, rTop, 2, rH + 2);
    if (winter) { ctx.fillStyle = 'rgba(235,240,248,.55)'; ctx.fillRect(bx - ROOF_OV, rTop, bw + 2 * ROOF_OV, faceTop - rTop); }
    // chimney + smoke: hearth homes + food shops feel alive
    const hearth = (b.kind === 'house' && (b.id || 0) % 3 === 2) ||
                   (b.kind === 'shop' && ['Diner', 'Bakery', 'Deli'].includes(b.label));
    if (hearth) {
      const cx = bx + bw * 0.72;
      ctx.fillStyle = '#6f6a63'; ctx.fillRect(cx - 2.5, rTop - 5, 6, 9);
      ctx.fillStyle = '#4d4943'; ctx.fillRect(cx - 3.5, rTop - 6, 8, 2);
      for (let k = 0; k < 3; k++) {                                                   // smoke puffs
        const t = ((now / 1400) + k / 3 + ((b.id || 0) * 0.13)) % 1;
        ctx.fillStyle = `rgba(210,214,222,${(1 - t) * 0.45})`;
        ctx.beginPath();
        ctx.arc(cx + Math.sin(t * 5 + b.id) * 3, rTop - 8 - t * 14, 1.5 + t * 2.6, 0, 6.283);
        ctx.fill();
      }
    }
    if (b.kind === 'church') {                                                        // steeple + cross
      const sx = bx + bw / 2;
      ctx.fillStyle = dark; ctx.fillRect(sx - 4, rTop - 12, 8, 12);
      ctx.fillStyle = light; ctx.beginPath(); ctx.moveTo(sx - 5, rTop - 12); ctx.lineTo(sx, rTop - 19); ctx.lineTo(sx + 5, rTop - 12); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = '#e8dfc8'; ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(sx, rTop - 26); ctx.lineTo(sx, rTop - 20); ctx.moveTo(sx - 2.5, rTop - 24); ctx.lineTo(sx + 2.5, rTop - 24); ctx.stroke();
    }
  }
  ctx.restore();
}

/* ── Iron/Steel-age exterior for the non-house town set ──────────────────────
   One shared industrial silhouette family, differentiated per venue: a flat
   riveted slab (matching the HQ compound's wings), SAWTOOTH north-light glass
   on the works/shops vs a skylight grid on the civic set, a coloured parapet
   trim in the building's identity colour, steel door with hazard jambs, brick/
   steel front face, working smokestacks on the industry + kitchens, and per-
   venue toppers (town-hall clock + flag, university bell tower, arcade neon
   marquee, the bar's hanging mug sign). The gated 🔞 store reads as a boarded
   derelict until the NSFW gate opens (then a pink neon sign — nothing else). */
function _ironRoof(ctx, b, est, winter, now) {
  const T = WM.TILE, bx = b.c * T, by = b.r * T, bw = b.w * T, bh = b.h * T;
  const loc = b.loc || b.kind;
  const pal = _ROOF_PAL[loc] || _ROOF_PAL[b.kind] || _ROOF_PAL.shop;
  const [main, dark, light] = est ? est.roof : pal[(b.id || 0) % pal.length];
  const faceTop = by + bh - WALL_H, ry0 = by - WALL_H;
  const nsOpen = (loc === 'nsfw') ? (window._wmNsfwOn === true) : null;
  // ── the slab ──
  ctx.fillStyle = dark; ctx.fillRect(bx - 2, ry0 - 2, bw + 4, bh + 4);
  ctx.fillStyle = main; ctx.fillRect(bx + 2, ry0 + 2, bw - 4, bh - 4);
  const civic = loc === 'townhall' || loc === 'school' || loc === 'exec' || loc === 'library';
  if (civic) {                                                     // skylight grid (like the HQ office wing)
    for (let gx = 0; gx < 3; gx++) for (let gy = 0; gy < 2; gy++) {
      const sx = bx + bw * 0.14 + gx * bw * 0.28, sy = ry0 + bh * 0.18 + gy * bh * 0.38;
      ctx.fillStyle = 'rgba(165,210,250,.4)'; ctx.fillRect(sx, sy, bw * 0.17, bh * 0.22);
      ctx.strokeStyle = 'rgba(20,26,38,.5)'; ctx.lineWidth = 1; ctx.strokeRect(sx, sy, bw * 0.17, bh * 0.22);
    }
  } else {                                                         // sawtooth monitor rows (the works)
    const rows = Math.max(2, (bh / 22) | 0), rh = (bh - 8) / rows;
    for (let i = 0; i < rows; i++) {
      const sy = ry0 + 4 + i * rh;
      ctx.fillStyle = light; ctx.fillRect(bx + 3, sy, bw - 6, rh * 0.55);
      ctx.fillStyle = 'rgba(140,190,240,.42)'; ctx.fillRect(bx + 3, sy + rh * 0.55, bw - 6, rh * 0.3);   // north-light glass
      ctx.fillStyle = 'rgba(0,0,0,.22)'; ctx.fillRect(bx + 3, sy + rh * 0.85, bw - 6, 1);
    }
  }
  // parapet: steel edge + rivet dots + identity-colour trim band
  ctx.strokeStyle = '#828c9d'; ctx.lineWidth = 2; ctx.strokeRect(bx - 1, ry0 - 1, bw + 2, bh + 2);
  ctx.fillStyle = b.color || '#8fb3ff'; ctx.fillRect(bx - 1, ry0 - 1, bw + 2, 2.5);
  ctx.fillStyle = 'rgba(232,238,244,.55)';
  for (let px = bx + 4; px < bx + bw - 2; px += 8) { ctx.fillRect(px, ry0 + 2.5, 1.4, 1.4); ctx.fillRect(px, ry0 + bh - 4.5, 1.4, 1.4); }
  ctx.fillStyle = '#8a919f'; ctx.fillRect(bx + bw - 15, ry0 + 4, 8, 6);          // roof vent unit
  ctx.fillStyle = 'rgba(0,0,0,.3)'; ctx.fillRect(bx + bw - 15, ry0 + 6.5, 8, 1);
  // ── front wall face: brick/steel with tall industrial windows + steel door ──
  const face = est ? est.face : _FACE_IRON[(b.id || 0) % _FACE_IRON.length];
  ctx.fillStyle = face; ctx.fillRect(bx, faceTop, bw, WALL_H);
  ctx.fillStyle = 'rgba(0,0,0,.25)'; ctx.fillRect(bx, by + bh - 2, bw, 2);
  const nwin = Math.max(1, Math.min(4, b.w - 3));
  for (let i = 0; i < nwin; i++) {
    const wx = bx + (i + 0.5) * bw / nwin - 3;
    ctx.fillStyle = '#2c3a52'; ctx.fillRect(wx, faceTop + 3, 6, 7);
    ctx.fillStyle = 'rgba(190,220,255,.5)'; ctx.fillRect(wx + 1, faceTop + 4, 2, 2);
    if (nsOpen === false) {                                        // boarded windows on the gated store
      ctx.strokeStyle = '#4a3b2a'; ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.moveTo(wx - 1, faceTop + 3); ctx.lineTo(wx + 7, faceTop + 10);
      ctx.moveTo(wx + 7, faceTop + 3); ctx.lineTo(wx - 1, faceTop + 10); ctx.stroke();
    }
  }
  const dx = bx + bw / 2 - 4;
  ctx.fillStyle = '#39414f'; ctx.fillRect(dx, faceTop + 3, 8, WALL_H - 4);       // steel door
  ctx.fillStyle = '#4a505c'; ctx.fillRect(dx, faceTop + 3, 8, 2);
  ctx.fillStyle = '#d8b13c'; ctx.fillRect(dx - 1.5, faceTop + 3, 1.5, WALL_H - 4); ctx.fillRect(dx + 8, faceTop + 3, 1.5, WALL_H - 4);
  // ── working smokestack (industry + kitchens) ──
  const stacky = loc === 'research' || loc === 'bar' || loc === 'cafe' || loc === 'pearl'
              || (b.kind === 'shop' && ['Diner', 'Bakery', 'Deli'].includes(b.label));
  if (stacky) {
    const cx2 = bx + bw * 0.78;
    ctx.fillStyle = '#4d4943'; ctx.fillRect(cx2 - 3, ry0 - 14, 6, 14);
    ctx.fillStyle = '#6f6a63'; ctx.fillRect(cx2 - 4, ry0 - 16, 8, 3);
    ctx.fillStyle = '#8a3f3f'; ctx.fillRect(cx2 - 3, ry0 - 11, 6, 2);            // banded stack
    for (let k = 0; k < 3; k++) {
      const t = ((now / 1500) + k / 3 + ((b.id || 0) * 0.17)) % 1;
      ctx.fillStyle = `rgba(200,204,212,${(1 - t) * 0.45})`;
      ctx.beginPath(); ctx.arc(cx2 + Math.sin(t * 5 + k + (b.id || 0)) * 3.5, ry0 - 18 - t * 15, 1.8 + t * 2.8, 0, 6.283); ctx.fill();
    }
  }
  // ── per-venue toppers ──
  if (loc === 'townhall') {
    const cx2 = bx + bw / 2;
    ctx.fillStyle = '#6a6d78'; ctx.fillRect(cx2 - 7, ry0 - 10, 14, 10);          // clock housing
    ctx.fillStyle = '#efe6d2'; ctx.beginPath(); ctx.arc(cx2, ry0 - 5, 4, 0, 6.283); ctx.fill();
    ctx.strokeStyle = '#2a2f3a'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(cx2, ry0 - 5); ctx.lineTo(cx2, ry0 - 8); ctx.moveTo(cx2, ry0 - 5); ctx.lineTo(cx2 + 2.5, ry0 - 4); ctx.stroke();
    ctx.strokeStyle = '#8a919f'; ctx.beginPath(); ctx.moveTo(bx + 6, ry0 - 1); ctx.lineTo(bx + 6, ry0 - 16); ctx.stroke();
    ctx.fillStyle = '#fde047'; ctx.fillRect(bx + 6, ry0 - 16, 8, 4);             // company flag
  } else if (loc === 'school') {
    const cx2 = bx + bw * 0.5;
    ctx.fillStyle = '#57544a'; ctx.fillRect(cx2 - 5, ry0 - 12, 10, 12);          // bell tower
    ctx.fillStyle = '#6e6a5e'; ctx.beginPath(); ctx.moveTo(cx2 - 6, ry0 - 12); ctx.lineTo(cx2, ry0 - 18); ctx.lineTo(cx2 + 6, ry0 - 12); ctx.closePath(); ctx.fill();
    ctx.fillStyle = '#e8c14a'; ctx.beginPath(); ctx.arc(cx2, ry0 - 8, 2.2, 0, 6.283); ctx.fill();   // the bell
  } else if (loc === 'arcade') {
    ctx.fillStyle = '#a26cf0'; ctx.fillRect(bx + 2, ry0 - 4, bw - 4, 3);         // neon marquee
    ctx.fillStyle = 'rgba(162,108,240,.4)'; ctx.fillRect(bx + 2, ry0 - 6, bw - 4, 2);
  } else if (loc === 'bar') {
    ctx.fillStyle = '#5a3d24'; ctx.fillRect(bx + bw - 8, faceTop - 5, 1.5, 6);   // hanging sign
    ctx.fillStyle = '#c0303a'; ctx.fillRect(bx + bw - 12, faceTop - 8, 9, 6);
    ctx.fillStyle = '#e8c14a'; ctx.fillRect(bx + bw - 10, faceTop - 6.5, 3, 3);  // the mug
  } else if (loc === 'nsfw' && nsOpen) {
    ctx.fillStyle = '#f06aa8'; ctx.fillRect(bx + 3, ry0 - 4, bw - 6, 3);         // pink neon — ONLY when the gate is open
    ctx.fillStyle = 'rgba(240,106,168,.35)'; ctx.fillRect(bx + 3, ry0 - 7, bw - 6, 2);
  }
  if (winter) { ctx.fillStyle = 'rgba(235,240,248,.45)'; ctx.fillRect(bx - 2, ry0 - 2, bw + 4, bh + 4); }
}

/* THE COMPANY HQ gets a modern flat roof: parapet, skylight grid, AC units and a
   logo stripe — the corporate island in a gabled village. Redesigned from the old
   near-black navy slab (it read as a broken empty building at mid zoom): warm
   concrete panels, a service ridge, and skylights that GLOW at night from the
   lit offices below. `nglow` = 0 (day) → 1 (full night). */
function _hqSkylights(bx, by, bw, bh) {
  const out = [];
  for (let gy = 0; gy < 2; gy++) for (let gx = 0; gx < 4; gx++)
    out.push({ x: bx + bw * 0.14 + gx * bw * 0.19, y: by - WALL_H + bh * 0.18 + gy * bh * 0.34,
               w: bw * 0.13, h: bh * 0.2 });
  return out;
}
function _flatRoof(ctx, bx, by, bw, bh, winter, nglow, est) {
  nglow = nglow || 0;
  const faceTop = by + bh - WALL_H, ry0 = by - WALL_H;
  // slab: readable mid-gray concrete, not a dark void — aged to the HQ's civilization era when present
  ctx.fillStyle = est ? est.roof[1] : '#555d6c'; ctx.fillRect(bx - 2, ry0 - 2, bw + 4, bh + 4);   // footprint shifted up by wall height
  ctx.fillStyle = est ? est.roof[0] : '#68717f'; ctx.fillRect(bx + 2, ry0 + 2, bw - 4, bh - 4);
  // concrete panel seams + a central service walkway so the roof reads as a textured surface
  ctx.strokeStyle = 'rgba(20,26,38,.26)'; ctx.lineWidth = 1;
  for (let px = bx + bw / 6; px < bx + bw - 4; px += bw / 6) { ctx.beginPath(); ctx.moveTo(px, ry0 + 3); ctx.lineTo(px, ry0 + bh - 3); ctx.stroke(); }
  for (let py = ry0 + bh / 4; py < ry0 + bh - 4; py += bh / 4) { ctx.beginPath(); ctx.moveTo(bx + 3, py); ctx.lineTo(bx + bw - 3, py); ctx.stroke(); }
  ctx.fillStyle = '#7b8595'; ctx.fillRect(bx + 4, ry0 + bh / 2 - 2, bw - 8, 4);            // ridge walkway
  ctx.fillStyle = 'rgba(255,255,255,.12)'; ctx.fillRect(bx + 4, ry0 + bh / 2 - 2, bw - 8, 1);
  // parapet: lit top edge + inner shadow
  ctx.strokeStyle = '#828c9d'; ctx.lineWidth = 2; ctx.strokeRect(bx - 1, ry0 - 1, bw + 2, bh + 2);
  ctx.strokeStyle = 'rgba(0,0,0,.28)'; ctx.lineWidth = 1; ctx.strokeRect(bx + 1.5, ry0 + 1.5, bw - 3, bh - 3);
  // face: glass-and-steel front — windows go warm-lit after dark (era-toned when present)
  ctx.fillStyle = est ? est.roof[1] : '#39424f'; ctx.fillRect(bx, faceTop, bw, WALL_H);
  ctx.fillStyle = nglow > 0.15 ? `rgba(255,205,130,${0.28 + 0.34 * nglow})` : 'rgba(140,190,240,.4)';
  for (let i = 0; i < Math.floor(bw / 14); i++) ctx.fillRect(bx + 3 + i * 14, faceTop + 3, 9, 8);
  // skylights over the atrium — sky-mirrors by day, warm office light by night
  for (const s of _hqSkylights(bx, by, bw, bh)) {
    ctx.fillStyle = nglow > 0.15 ? `rgba(255,200,120,${0.30 + 0.40 * nglow})` : 'rgba(165,210,250,.45)';
    ctx.fillRect(s.x, s.y, s.w, s.h);
    ctx.strokeStyle = 'rgba(20,26,38,.5)'; ctx.lineWidth = 1; ctx.strokeRect(s.x, s.y, s.w, s.h);
    ctx.strokeStyle = 'rgba(255,255,255,.20)';                                              // mullions
    ctx.beginPath(); ctx.moveTo(s.x + s.w / 2, s.y); ctx.lineTo(s.x + s.w / 2, s.y + s.h); ctx.stroke();
  }
  // AC units + logo stripe
  ctx.fillStyle = '#8a919f'; ctx.fillRect(bx + bw - 26, ry0 + 6, 9, 7); ctx.fillRect(bx + bw - 14, ry0 + 6, 9, 7);
  ctx.fillStyle = 'rgba(0,0,0,.3)'; ctx.fillRect(bx + bw - 26, ry0 + 9, 9, 1); ctx.fillRect(bx + bw - 14, ry0 + 9, 9, 1);
  ctx.fillStyle = '#4f93c8'; ctx.fillRect(bx + 6, ry0 + 5, bw * 0.22, 4);
  if (winter) { ctx.fillStyle = 'rgba(235,240,248,.45)'; ctx.fillRect(bx - 2, ry0 - 2, bw + 4, bh + 4); }
}
/* Per-frame radial-gradient churn was the night-time perf sink: _drawLights built
   ~160 createRadialGradient objects EVERY frame (one per lit window / skylight /
   room / lamp). Their coordinates are WORLD-space and get mapped by the camera CTM
   at paint time, so the SAME gradient object repaints correctly across pans/zooms —
   only the slowly-changing night `glow` (hourly) and the zoom-driven roof cutaway
   actually vary the colour. Memoize by a key of position+radii+colour-stops so a
   steady camera allocates ZERO gradients/frame (a zoom sweep varies only the ~20
   roof-cutaway room/skylight gradients); a size cap keeps the cache bounded. Output
   is byte-identical to the old per-frame gradients. */
const _gradCache = new Map();
function _lightGrad(ctx, cx, cy, r0, r1, c0, c1) {
  const key = cx + '|' + cy + '|' + r0 + '|' + r1 + '|' + c0 + '|' + c1;
  let g = _gradCache.get(key);
  if (g === undefined) {
    if (_gradCache.size > 800) _gradCache.clear();     // zoom sweeps vary the key — stay bounded
    g = ctx.createRadialGradient(cx, cy, r0, cx, cy, r1);
    g.addColorStop(0, c0); g.addColorStop(1, c1);
    _gradCache.set(key, g);
  }
  return g;
}
function _drawLights(ctx, canvas) {
  const L = _daylight(_worldState?.clock_hour ?? 12);
  if (L.dark <= 0.01) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2), cam = WM.camera;
  // 1) darkening tint over the whole viewport (screen space)
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalCompositeOperation = 'source-over';
  ctx.fillStyle = `rgba(${L.tint[0]},${L.tint[1]},${L.tint[2]},${L.dark})`;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  // 2) light sources punch through (world space, additive)
  ctx.setTransform(dpr * cam.scale, 0, 0, dpr * cam.scale, dpr * cam.x, dpr * cam.y);
  ctx.globalCompositeOperation = 'lighter';
  const glow = Math.min(1, L.dark * 1.6);
  // Warm interior light. When the camera is zoomed in the roofs fade away and the
  // interiors are on show — they are LIT rooms, so counter the night tint hard
  // (they used to sit under the full darkening and read as black boxes). Zoomed
  // out the roofs cover them again, so the wash eases back to a faint glow.
  const inWash = 0.07 + 0.34 * (1 - _roofAlpha());
  ctx.fillStyle = `rgba(255,205,130,${inWash * glow})`;
  for (const b of WM.buildings) ctx.fillRect((b.c + 1) * WM.TILE, (b.r + 1) * WM.TILE, (b.w - 2) * WM.TILE, (b.h - 2) * WM.TILE);
  // lit WINDOWS along each building's bottom wall — the cozy village-at-night look
  const TL = WM.TILE, flick = performance.now() / 900;
  for (const b of WM.buildings) {
    const nwin = Math.max(1, Math.min(4, (b.w - 2) | 0));
    for (let i = 0; i < nwin; i++) {
      if (((b.id || 0) * 31 + i * 17) % 5 === 0) continue;          // some windows stay dark
      const wx = (b.c + 1 + (i + 0.5) * (b.w - 2) / nwin) * TL;
      const wy = (b.r + b.h - 0.5) * TL;
      const a = (0.5 + 0.08 * Math.sin(flick + i + (b.id || 0))) * glow;
      ctx.fillStyle = `rgba(255,214,140,${a})`;
      ctx.fillRect(wx - 2.5, wy - 4, 5, 5);
      ctx.fillStyle = _lightGrad(ctx, wx, wy, 1, 13, `rgba(255,214,140,${0.28 * glow})`, 'rgba(255,214,140,0)');
      ctx.beginPath(); ctx.arc(wx, wy, 13, 0, 6.283); ctx.fill();
    }
  }
  // Layer-3: hand-placed interior WINDOWS glow at night too (only meaningful once the
  // roof cuts away, so scale by 1-roofAlpha like the interior wash above).
  const iw = (1 - _roofAlpha());
  if (iw > 0.05) for (const b of WM.buildings) {
    if (!b.interior) continue;
    for (const it of b.interior) {
      if (it.kind !== 'window') continue;
      const wx = (b.c + it.lc + 0.5) * TL, wy = (b.r + it.lr + 0.5) * TL;
      ctx.fillStyle = `rgba(255,214,140,${0.5 * glow * iw})`; ctx.fillRect(wx - 2.5, wy - 2.5, 5, 5);
      ctx.fillStyle = _lightGrad(ctx, wx, wy, 1, 12, `rgba(255,214,140,${0.26 * glow * iw})`, 'rgba(255,214,140,0)');
      ctx.beginPath(); ctx.arc(wx, wy, 12, 0, 6.283); ctx.fill();
    }
  }
  // HQ at night: the flat roof's skylights + face windows glow through the dark
  // wash while the roof is on, and once the cutaway kicks in the department
  // rooms read as warm LIT offices (occupied ones brightest) instead of the old
  // uniformly-dim gray squares.
  const ra = _roofAlpha();
  const hq = (WM.buildings || []).find(b => b.kind === 'hq');
  if (hq && ra > 0.05) {
    // staged compound HQ → skylights glow per WING; classic HQ → the whole slab
    const hqRects = (hq.sections && hq.sections.length)
      ? hq.sections.filter(s => !s.open).map(s => [(hq.c + s.lc) * TL, (hq.r + s.lr) * TL, s.w * TL, s.h * TL])
      : [[hq.c * TL, hq.r * TL, hq.w * TL, hq.h * TL]];
    for (const [bx, by, bw, bh] of hqRects) for (const s of _hqSkylights(bx, by, bw, bh)) {
      const cx = s.x + s.w / 2, cy = s.y + s.h / 2;
      ctx.fillStyle = _lightGrad(ctx, cx, cy, 1, s.w, `rgba(255,205,130,${0.40 * glow * ra})`, 'rgba(255,205,130,0)');
      ctx.beginPath(); ctx.arc(cx, cy, s.w, 0, 6.283); ctx.fill();
    }
  }
  if (ra < 0.95) {
    for (const rm of (WM.hqRooms || [])) {
      let occupied = false;
      for (const id in _sprites) {
        const s = _sprites[id];
        if (s.px >= rm.x0 && s.px <= rm.x0 + rm.w && s.py >= rm.y0 && s.py <= rm.y0 + rm.h) { occupied = true; break; }
      }
      const amt = (occupied ? 0.52 : 0.28) * glow * (1 - ra);
      ctx.fillStyle = _lightGrad(ctx, rm.x, rm.y, 2, Math.max(rm.w, rm.h) * 0.62, `rgba(255,205,135,${amt})`, 'rgba(255,205,135,0)');
      ctx.fillRect(rm.x0, rm.y0, rm.w, rm.h);
    }
  }
  for (const d of WM.decor) {                                       // lamps + fountain glow
    if (d.kind !== 'lamp' && d.kind !== 'fountain') continue;
    const cx = d.x, cy = d.kind === 'lamp' ? d.y - 9 : d.y, R = d.kind === 'lamp' ? 26 : 16;
    ctx.fillStyle = _lightGrad(ctx, cx, cy, 1, R, `rgba(255,225,150,${(d.kind === 'lamp' ? 0.6 : 0.3) * glow})`, 'rgba(255,225,150,0)');
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, 6.283); ctx.fill();
  }
  ctx.globalCompositeOperation = 'source-over';
}

/* Real store-generated images, framed and hung on house + HQ-department walls.
   Loaded lazily (async); only drawn once the image is available. */
const _artCache = {};
function _artImg(url) {
  let im = _artCache[url];
  if (im === undefined) { im = new Image(); im.onerror = () => { _artCache[url] = null; }; im.src = url; _artCache[url] = im; }
  return (im && im.complete && im.naturalWidth) ? im : null;
}
function _drawWallArt(ctx) {
  const art = _worldState && _worldState.art;
  if (!art || !art.length) return;
  const TL = WM.TILE;
  const frame = (im, fx, fy, fw, fh) => {
    ctx.fillStyle = '#3a2a1a'; ctx.fillRect(fx - 1.5, fy - 1.5, fw + 3, fh + 3);
    ctx.imageSmoothingEnabled = false; ctx.drawImage(im, fx, fy, fw, fh);
    ctx.strokeStyle = '#caa06a'; ctx.lineWidth = 1; ctx.strokeRect(fx, fy, fw, fh);
  };
  for (const b of (WM.buildings || [])) {
    if (b.kind !== 'house') continue;
    // small wall pictures — they were TILE-plus posters that dwarfed the bed
    const n = b.w >= 6 ? 1 + (b.id % 2) : 1;                    // 1–2 per home, 1 in small homes
    for (let k = 0; k < n; k++) {
      const im = _artImg(art[(b.id + k) % art.length]); if (!im) continue;
      frame(im, (b.c + 1.4 + k * 1.6) * TL, (b.r + 1.05) * TL, TL * 0.85, TL * 0.65);
    }
  }
  for (let i = 0; i < (WM.hqRooms || []).length; i++) {          // one per HQ department room
    const rm = WM.hqRooms[i], im = _artImg(art[i % art.length]); if (!im) continue;
    const fw = TL * 0.85, fh = TL * 0.65;
    frame(im, rm.x - fw / 2, rm.y - TL * 1.5, fw, fh);
  }
}

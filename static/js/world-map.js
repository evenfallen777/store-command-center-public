'use strict';
/* ══════════════════════════════════════════════════════════════════════════
   THE COMPANY — tile world engine (v3: editable city).
   A large grid world with THE COMPANY HQ dead-centre. Terrain is split into a
   BASE layer (grass/roads/trees/parks/plaza) generated once, and a BUILDINGS
   layer stamped on top by rasterize(). That split is what makes "play god" edit
   mode possible: move/resize/add/delete a building and just re-rasterize. A*
   pathfinding + pan/zoom camera. Layout edits persist via /api/world/layout.
   ══════════════════════════════════════════════════════════════════════════ */
window.WM = (function () {
  const TILE = 20, COLS = 132, ROWS = 104;   // expanded: wild outskirts ring the city (wildlife, hunting, room to grow)
  const W = COLS * TILE, H = ROWS * TILE;
  const T = { GRASS: 0, PATH: 1, FLOOR: 2, WALL: 3, TREE: 4, WATER: 5, PLAZA: 6, MOUNTAIN: 7 };
  const WALK_COST = { 0: 3, 1: 1, 2: 1, 6: 1.2 };   // MOUNTAIN/WATER/TREE/WALL absent → impassable
  const WALL_PX = Math.round(TILE * 0.5);            // 10 — themed per-building wall shell band (HALF the old 20px stone ring; collision T.WALL is unchanged)

  // ── DESIRE LINES: foot traffic wears grass → dirt → packed → cobbled road ──
  // Every walker bumps the tile it steps on; worn tiles get CHEAPER for A*, so
  // popular shortcuts reinforce themselves into real roads organically.
  let wear = {};                                  // "c,r" -> step count
  let wearDirty = {};                             // un-pushed increments (synced to backend)
  const WEAR_STAGES = [110, 320, 700];            // steps → dirt / packed / cobbled (slow — a trail is EARNED)
  const WEAR_COST = [3, 2.2, 1.6, 1.05];          // grass walk-cost by wear stage
  function bumpWear(c, r) {
    if (!inb(c, r) || grid[r][c] !== T.GRASS) return;
    if (Math.random() > 0.4) return;              // not every footstep scuffs — keeps the town green
    const k = c + ',' + r;
    wear[k] = Math.min(1200, (wear[k] || 0) + 1);
    wearDirty[k] = (wearDirty[k] || 0) + 1;
  }
  function wearStage(c, r) {
    const n = wear[c + ',' + r] || 0;
    return n >= WEAR_STAGES[2] ? 3 : n >= WEAR_STAGES[1] ? 2 : n >= WEAR_STAGES[0] ? 1 : 0;
  }
  function loadWear(obj) { if (obj && typeof obj === 'object') wear = { ...obj }; }
  function takeWearDirty() { const d = wearDirty; wearDirty = {}; return d; }

  let baseGrid = [];         // terrain WITHOUT buildings (grass/road/tree/park/plaza)
  let grid = [];             // baseGrid + stamped buildings (what pathfinding/render use)
  const locations = {};      // name → {col,row}
  let houseSlots = [];       // interior tiles for agent homes
  let buildings = [];        // editable descriptors {id,c,r,w,h,kind,loc,dept,label,color,door}
  let decor = [];            // sub-grid objects {x,y,kind}
  let landmarks = [];        // big pack sprites (park trees) {col,row,kind}
  let nodes = [];            // resource nodes {col,row,kind} for idle-skilling (woodcut/mine/farm/fish/build)
  let waterTiles = [];       // {col,row} of pond/water cells — animated live by the renderer
  let _terrainImg = null;    // Layer-2: one generated whole-world ground image (null = procedural per-tile)
  let _floorImg = null;      // Layer-2b: ONE shared interior-floor texture blitted under every building (null = procedural per-kind tint only)
  // Self-heal: browsers reclaim the backing store of large off-DOM canvases / decoded
  // images under memory pressure. The element refs stay valid (.complete === true) but
  // draw nothing, so terrain silently blanks after a while. We keep the SOURCE URLs so
  // reheal() can re-decode from scratch and re-bake without a page/browser restart.
  let _terrainUrl = null, _floorUrl = null;
  let _rehealing = false;
  // ── CHUNKED TERRAIN + LOD PYRAMID STREAMER ───────────────────────────────────
  // Instead of one giant 2640×2080 resident canvas (which browsers evict under memory
  // pressure and which can't scale to 4k/8k/moon maps), the ground is rendered as:
  //   • _overview + _MIPS — a small MIP PYRAMID of whole-map canvases (W/2, W/4, W/8).
  //     Zoomed out we blit the COARSEST level that's still crisp at the current scale
  //     (cheaper drawImage + no blurry upscale). The finest level (_overview, W/2) is
  //     ALSO the fallback UNDER full-res chunks that are still baking (so never blank).
  //     Only the finest is baked with the full structPass; the coarser levels are just
  //     downscales of it (built lazily), so map side-effects run exactly once.
  //   • _chunks   — full-res tiles baked ON DEMAND in a CENTERED DISC WINDOW around the
  //     camera-centre chunk (a 5×5 block minus its corners at radius 2.5), which PREFETCHES
  //     neighbours so panning never pops in, then EVICTED once they age out of the window.
  //     Memory stays flat regardless of map size — only the disc is ever resident. The
  //     moon map reuses this.
  let _overview = null;                            // finest LOD (W/_OV = W/2) — structPass source + fallback under chunks
  const _OV = 2;                                   // finest overview downscale (W/2×H/2 ≈ crisp to ~0.5×)
  const _MIP_D = [4, 8];                            // coarser pyramid downscales (W/4, W/8); index → _MIPS slot
  const _MIPS = [null, null];                       // lazily-built coarser levels (downscaled from _overview)
  let _structPass = false;                         // true only during the full pass that fills waterTiles/hqRooms/locations
  const _chunks = new Map();                       // "cx,cy" -> {cv, wx, wy, seen}
  const CHUNK_CW = 22, CHUNK_CH = 26;              // chunk size in TILES (440×520 px; 132/22=6 × 104/26=4 = 24, exact)
  const CHUNK_LOD = 0.5;                           // below this camera.scale → pyramid only; at/above → stream full-res chunks
  const CHUNK_EVICT_MS = 8000;                     // free a chunk unseen for this long
  const CHUNK_BUDGET = 2;                          // max chunk bakes per frame (no hitch)
  const WINDOW_R = 2.5;                             // disc window radius in chunks (→ a 5×5 block minus its corners = prefetch ring)
  // Precompute the disc offsets ONCE (no per-frame allocation): every (dx,dy) whose
  // chunk-index distance from the centre chunk is within WINDOW_R (rounds the corners off).
  const _discOffsets = (() => {
    const r = Math.ceil(WINDOW_R), out = [];
    for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++)
      if (Math.hypot(dx, dy) <= WINDOW_R + 1e-6) out.push([dx, dy]);
    return out;
  })();
  const DISC_MAX = _discOffsets.length;            // max resident chunks in the disc window (21 at R=2.5)
  const camera = { x: 0, y: 0, scale: 1 };
  let _fitScale = 0.25;      // scale at which the whole map fits the viewport (set in fit()); the space/orbit bands key off this so they're viewport-independent
  let _nextId = 1;

  const inb = (c, r) => c >= 0 && r >= 0 && c < COLS && r < ROWS;
  const bset = (c, r, t) => { if (inb(c, r)) baseGrid[r][c] = t; };
  const bfill = (c0, r0, w, h, t) => { for (let r = r0; r < r0 + h; r++) for (let c = c0; c < c0 + w; c++) bset(c, r, t); };
  const region = (x0, y0, x1, y1, q) => (x0 < q.x1 && x1 > q.x0 && y0 < q.y1 && y1 > q.y0);

  function mulberry32(a) {
    return function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  }

  const KIND_COLOR = { hq: '#8fb3ff', house: '#6b7ba0', shop: '#6aa6d6', leisure: '#f0b45a',
                       townhall: '#fde047', exec: '#fb7185', church: '#cdbff0', library: '#8fc7a9',
                       research: '#818cf8', school: '#6ec6e6', nsfw: '#e0567a',
                       // standalone dept buildings (like Research Lab — NOT HQ rooms)
                       mail: '#f9a8d4', homelab: '#7dd3fc', pearl: '#a7f3d0', assistant: '#d8b4fe' };
  // distinct roof colour per named venue/loc so no two building types look alike
  const LOC_COLOR = { bar: '#e0714a', arcade: '#a26cf0', tv: '#4aa0e0', cafe: '#d1a05a',
                      church: '#cdbff0', library: '#8fc7a9', townhall: '#fde047', exec: '#fb7185',
                      park: '#5bb46a', gas: '#e05a6a', lounge: '#c07ad0', research: '#818cf8',
                      school: '#6ec6e6', nsfw: '#e0567a',
                      mail: '#f9a8d4', homelab: '#7dd3fc', pearl: '#a7f3d0', assistant: '#d8b4fe' };
  // ── 😈 NSFW store gate: the building exists but is BOARDED UP + inert unless the
  // EXISTING layered gate is on (world_satan_nsfw_domain AND nsfw.world_active(),
  // ANDed server-side into /api/world/state's nsfw_store_open → window._wmNsfwOn).
  // Nothing here can turn the gate on — this only READS it. Default off = boarded.
  function _nsfwOn() { return window._wmNsfwOn === true; }

  // ── organic-map helpers ──
  const TAU = Math.PI * 2;
  function _brush(c, r, t) { if (inb(c, r)) bset(c, r, t); }
  function _areaGrass(x, y, w, h) {                    // footprint (+1 margin) is all clear grass?
    if (x < 2 || y < 2 || x + w > COLS - 2 || y + h > ROWS - 2) return false;
    for (let r = y; r < y + h; r++) for (let c = x; c < x + w; c++) if (baseGrid[r][c] !== T.GRASS) return false;
    return true;
  }
  function _nearRoad(x, y, w, h) {                     // a road within 2 tiles so buildings face a street
    for (let c = x - 2; c < x + w + 2; c++) for (let r = y - 2; r < y + h + 2; r++)
      if (inb(c, r) && baseGrid[r][c] === T.PATH) return true;
    return false;
  }
  // scatter a building of w×h in a distance band from centre, on grass. Phase 0 prefers a
  // roadside spot; phase 1 accepts any clear grass (then carves a short access lane to a road).
  // `arc` = [startAngle, span] biases phase 0 into a DISTRICT sector (civic quarter, leisure
  // strip, industrial row…) so the town reads as planned rather than a uniform scatter; the
  // phase-1 fallback still searches the whole circle so nothing ever fails to place.
  function _tryPlace(w, h, minD, maxD, extra, rnd, pick, CX, CY, arc) {
    for (let phase = 0; phase < 2; phase++) {
      const dHi = phase === 0 ? maxD : Math.max(maxD, 46);   // fallback pass searches the whole map
      for (let t = 0; t < 300; t++) {
        const a = (arc && phase === 0) ? arc[0] + rnd() * arc[1] : rnd() * TAU;
        const d = minD + rnd() * (dHi - minD);
        const ox = Math.round(CX + Math.cos(a) * d - w / 2), oy = Math.round(CY + Math.sin(a) * d * 0.8 - h / 2);
        if (!_areaGrass(ox - 1, oy - 1, w + 2, h + 2)) continue;
        if (phase === 0 && !_nearRoad(ox, oy, w, h)) continue;
        const b = { id: _nextId++, c: ox, r: oy, w, h, door: pick(['N', 'S', 'E', 'W']), ...extra };
        buildings.push(b);
        bfill(ox, oy, w, h, T.FLOOR);                 // reserve footprint so nothing else lands on it
        if (phase === 1) _laneToRoad(ox + (w >> 1), oy + h);   // connect off-road placements
        return b;
      }
    }
    return null;
  }
  // carve a short straight lane from (c,r) toward the map centre until it hits a
  // road/plaza — through grass AND woods (a hewn lane), so an outskirts building
  // can never end up sealed behind the tree scatter.
  function _laneToRoad(c, r) {
    const CX = COLS / 2 | 0, CY = ROWS / 2 | 0;
    let x = c, y = r;
    for (let i = 0; i < 14; i++) {
      if (inb(x, y) && (baseGrid[y][x] === T.PATH || baseGrid[y][x] === T.PLAZA)) return;
      if (inb(x, y) && (baseGrid[y][x] === T.GRASS || baseGrid[y][x] === T.TREE)) bset(x, y, T.PATH);
      x += Math.sign(CX - x) || 0; y += Math.sign(CY - y) || 0;
    }
  }

  // ── generate the BASE terrain + building descriptors (no walls stamped yet) ──
  function _genBase() {
    const rng = mulberry32(20260713);
    const rnd = () => rng(), ri = (a, b) => a + Math.floor(rnd() * (b - a + 1));
    const chance = p => rnd() < p, pick = arr => arr[Math.floor(rnd() * arr.length)];
    baseGrid = Array.from({ length: ROWS }, () => Array(COLS).fill(T.GRASS));
    buildings = []; decor = []; landmarks = []; _nextId = 1;
    const CX = COLS / 2 | 0, CY = ROWS / 2 | 0;

    // ── HQ + central plaza + fountain ──
    const hw = 26, hh = 14, hc = CX - (hw / 2 | 0), hr = CY - (hh / 2 | 0);   // widened: 13 departments now
    const hq = { x0: hc - 3, y0: hr - 3, x1: hc + hw + 3, y1: hr + hh + 3 };
    bfill(hq.x0, hq.y0, hq.x1 - hq.x0, hq.y1 - hq.y0, T.PLAZA);
    for (let x = hq.x0; x <= hq.x1; x++) { bset(x, hq.y0, T.PATH); bset(x, hq.y1, T.PATH); }
    for (let y = hq.y0; y <= hq.y1; y++) { bset(hq.x0, y, T.PATH); bset(hq.x1, y, T.PATH); }
    const fx = hc + (hw / 2 | 0), fy = hr - 2;
    for (let dy = 0; dy < 2; dy++) for (let dx = 0; dx < 2; dx++) bset(fx - 1 + dx, fy - 1 + dy, T.WATER);
    decor.push({ x: (fx + 0.5) * TILE, y: (fy + 0.5) * TILE, kind: 'fountain' });
    buildings.push({ id: _nextId++, c: hc, r: hr, w: hw, h: hh, kind: 'hq', loc: null,
                     label: '⬢ THE COMPANY HQ', color: KIND_COLOR.hq, door: 'S' });

    // ── organic road network: a winding ring + meandering avenues + plaza spokes ──
    const ringR = 21;
    for (let a = 0; a < TAU; a += 0.018) {
      const rr = ringR + 3.4 * Math.sin(a * 3 + 1) + 1.8 * Math.sin(a * 7 + 2);
      const rc = Math.round(CX + Math.cos(a) * rr), rw = Math.round(CY + Math.sin(a) * rr * 0.82);
      _brush(rc, rw, T.PATH); _brush(rc, rw + 1, T.PATH);
    }
    for (let k = 0; k < 6; k++) {                        // avenues meandering out to the countryside
      let a = k / 6 * TAU + rnd() * 0.6;
      let x = CX + Math.cos(a) * ringR, y = CY + Math.sin(a) * ringR * 0.82;
      for (let step = 0; step < 62; step++) {
        a += (rnd() - 0.5) * 0.34;
        x += Math.cos(a) * 1.5; y += Math.sin(a) * 1.5;
        const rc = Math.round(x), rw = Math.round(y);
        if (!inb(rc, rw)) break;
        _brush(rc, rw, T.PATH); _brush(rc + 1, rw, T.PATH);
      }
    }
    for (let k = 0; k < 5; k++) {                        // short spokes: plaza → ring
      const a = k / 5 * TAU + 0.7;
      for (let d = 8; d <= ringR + 1; d++) { const rc = Math.round(CX + Math.cos(a) * d), rw = Math.round(CY + Math.sin(a) * d * 0.82); _brush(rc, rw, T.PATH); _brush(rc, rw + 1, T.PATH); }
    }

    // ── rugged mountain range along the north edge (before buildings, so they avoid it) ──
    for (let c = 1; c < COLS - 1; c++) {
      const depth = 2 + Math.round(5 * (0.5 + 0.5 * Math.sin(c * 0.17 + 1.3)) * (0.55 + 0.45 * rnd()));
      for (let r = 0; r < depth; r++) if (baseGrid[r][c] !== T.PATH) bset(c, r, T.MOUNTAIN);
    }

    // ── DISTRICTED town plan (Iron/Steel-age rework): instead of a uniform scatter the
    // groups cluster into quarters keyed off the plaza — CIVIC CROWN just north of the
    // plaza (town hall, church, university, library, boss's office), a LEISURE STRIP
    // east along the ring road (bar, arcade, lounge, café), an INDUSTRIAL ROW west
    // (research + the four standalone dept works), shops filling the mid ring and homes
    // pushed to the outskirts. The 😈 NSFW video store sits alone on the far south edge
    // of town (boarded up unless the layered NSFW gate is on). Angles: 0=E, π/2=S(+y),
    // π=W, -π/2=N. All placements still road-seek + fall back, so nothing fails.
    const NORTH = [-Math.PI / 2 - 0.85, 1.7], EAST = [-0.55, 1.1], WEST = [Math.PI - 0.6, 1.2], SOUTH = [Math.PI / 2 - 0.4, 0.8];
    const leisure = [['bar', 'Bar 🍺', 7, 6], ['arcade', 'Arcade 🕹️', 7, 6], ['tv', 'Lounge 📺', 6, 5], ['cafe', 'Café ☕', 6, 5]];
    const civic = [['townhall', 'Grand Town Hall 🏛️', 9, 7], ['church', 'Church ⛪', 7, 8], ['school', 'University 🎓', 10, 8],
                   ['library', 'Library 📚', 7, 6], ['exec', "Boss's Office 👔", 7, 6]];
    const industry = [['research', 'Research Lab 🔬', 8, 6],
                      // the store's four "homeless" systems get real standalone places (self-contained, like the Research Lab — the HQ layout is untouched)
                      ['mail', 'Mail Room 📬', 6, 5], ['homelab', 'Homelab 🖥️', 6, 6], ['pearl', 'Pearl Mine 🦪', 6, 5], ['assistant', 'AI Assistant 🤖', 6, 5]];
    const shopNames = ['Diner', 'Market', 'Bank', 'Gym', 'Salon', 'Bakery', 'Books', 'Garage', 'Clinic', 'Deli', 'Toys', 'Pharmacy'];
    for (const [k, lbl, w, h] of civic) _tryPlace(w, h, 12, 20, { kind: k, loc: k, label: lbl, color: KIND_COLOR[k] }, rnd, pick, CX, CY, NORTH);
    for (const [k, lbl, w, h] of leisure) _tryPlace(w, h, 12, 22, { kind: 'leisure', loc: k, label: lbl, color: LOC_COLOR[k] || KIND_COLOR.leisure }, rnd, pick, CX, CY, EAST);
    for (const [k, lbl, w, h] of industry) _tryPlace(w, h, 14, 26, { kind: k, loc: k, label: lbl, color: KIND_COLOR[k] }, rnd, pick, CX, CY, WEST);
    _tryPlace(6, 5, 26, 38, { kind: 'nsfw', loc: 'nsfw', label: 'Video Store', color: KIND_COLOR.nsfw }, rnd, pick, CX, CY, SOUTH);
    for (let s = 0; s < 12; s++) _tryPlace(ri(5, 7), ri(5, 6), 13, 32, { kind: 'shop', loc: null, label: pick(shopNames), color: KIND_COLOR.shop, small: true }, rnd, pick, CX, CY);
    for (let h = 0; h < 28; h++) _tryPlace(ri(5, 6), ri(5, 6), 14, 42, { kind: 'house', loc: null, label: '', color: KIND_COLOR.house, house: true }, rnd, pick, CX, CY);

    // ── scattered parks with landmark trees, a well, and a couple of fishing ponds ──
    for (let p = 0; p < 6; p++) {
      for (let t = 0; t < 70; t++) {
        const pw = ri(6, 9), ph = ri(5, 7), a = rnd() * TAU, d = 15 + rnd() * 26;
        const px0 = Math.round(CX + Math.cos(a) * d - pw / 2), py0 = Math.round(CY + Math.sin(a) * d * 0.8 - ph / 2);
        if (!_areaGrass(px0, py0, pw, ph)) continue;
        const kinds = ['tree_green', 'tree_autumn', 'tree_yellow'];
        if (p < 2) _pond(px0, py0, pw, ph);
        _park(px0, py0, pw, ph, rnd, ri);
        landmarks.push({ col: px0 + 1, row: py0 + ph - 2, kind: kinds[p % 3] });
        if (pw >= 8) landmarks.push({ col: px0 + pw - 2, row: py0 + ph - 2, kind: kinds[(p + 1) % 3] });
        if (p % 2 === 0) landmarks.push({ col: px0 + pw - 3, row: py0 + 2, kind: 'well', scale: 1.6 });
        if (!locations['park']) locations['park'] = { col: px0 + (pw / 2 | 0), row: py0 + 1 };
        break;
      }
    }
    if (!locations['park']) locations['park'] = { col: CX + 8, row: CY + 8 };

    // ── natural cover: border trees (grass only, skips the mountains) + scattered woods ──
    for (let r = 0; r < ROWS; r++) for (let d = 0; d < 3; d++) {
      if (baseGrid[r][d] === T.GRASS && chance(0.6)) bset(d, r, T.TREE);
      if (baseGrid[r][COLS - 1 - d] === T.GRASS && chance(0.6)) bset(COLS - 1 - d, r, T.TREE);
    }
    for (let c = 0; c < COLS; c++) for (let d = 0; d < 3; d++) if (baseGrid[ROWS - 1 - d][c] === T.GRASS && chance(0.6)) bset(c, ROWS - 1 - d, T.TREE);
    for (let r = 1; r < ROWS - 1; r++) for (let c = 1; c < COLS - 1; c++) if (baseGrid[r][c] === T.GRASS && chance(0.045)) bset(c, r, T.TREE);
    for (let n = 0; n < 55; n++) { const cc = ri(2, COLS - 3), rr = ri(2, ROWS - 3); if (baseGrid[rr][cc] === T.GRASS) _blob(cc, rr, ri(2, 4), rnd); }

    // ── a couple of big organic lakes out in the open country ──
    for (let n = 0, lakes = 0; n < 12 && lakes < 2; n++) {
      const lx = ri(12, COLS - 22), ly = ri(16, ROWS - 16);
      if (region(lx - 2, ly - 2, lx + 16, ly + 12, hq) || baseGrid[ly][lx] !== T.GRASS) continue;
      _pond(lx, ly, ri(11, 16), ri(8, 12)); lakes++;
    }

    // ── every FRONT DOOR must open onto passable ground: the tree scatter and the
    // country lakes run AFTER building placement, so a door could end up sealed
    // behind woods/water (agents then can't enter — the old "bar nobody could
    // reach" bug). Clear a small apron outside each door and lane it to the road
    // net, which also gives every building a walk-up path (a more built-out town).
    for (const b of buildings) {
      const [oc, orr] = _doorOut(b);
      for (let dr2 = -1; dr2 <= 1; dr2++) for (let dc2 = -1; dc2 <= 1; dc2++) {
        const c2 = oc + dc2, r2 = orr + dr2;
        if (inb(c2, r2) && (baseGrid[r2][c2] === T.TREE || baseGrid[r2][c2] === T.WATER)) bset(c2, r2, T.GRASS);
      }
      _laneToRoad(oc, orr);
    }
    _connectTown(CX, CY);   // hard guarantee: no venue/home/node generates cut off from the plaza
    _decorate(rnd, ri, chance);

    // resource nodes for idle-skilling — placed on grass in distinct regions, cleared of trees
    nodes = [];
    _placeNode('woodcut', 15, 15, CX, CY);
    _placeNode('mine', COLS - 15, 15, CX, CY);
    _placeNode('farm', 15, ROWS - 15, CX, CY);
    _placeNode('build', CX + 15, CY + 11, CX, CY);
    _placeNode('hunt', COLS - 14, ROWS - 14, CX, CY);   // hunting grounds, deep in the wilds with the deer
    _placeOreDeposits();                                 // minable ORE BLOCKS along the mountain fringe (⛏️ → stockpile ore)
    _placeFishSpots(CX, CY);                             // FISHING spots on real shorelines (🎣 → stockpile fish)
  }

  // ── MINING ORE DEPOSITS: real ore-block outcrops at the foot of the northern
  // mountain range — extra 'mine' nodes, so they plug straight into the EXISTING
  // idle-skilling economy (world_skills: mining → ore → stockpile → construction
  // costs). Purely more places to mine; no new autonomous behavior is added.
  function _placeOreDeposits() {
    for (let k = 0; k < 3; k++) {
      const tc = Math.round(COLS * (0.22 + 0.28 * k));
      for (let c = tc; c < tc + 10; c++) {
        let rTop = -1;
        for (let r = 1; r < 16; r++) if (baseGrid[r][c] === T.MOUNTAIN) rTop = r;
        const rr = rTop + 2;                                        // just below the rock face
        if (rTop < 0 || !inb(c, rr) || baseGrid[rr][c] !== T.GRASS) continue;
        for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++)
          if (inb(c + a, rr + b) && baseGrid[rr + b][c + a] === T.TREE) bset(c + a, rr + b, T.GRASS);
        nodes.push({ col: c, row: rr, kind: 'mine' });
        break;
      }
    }
  }

  // ── FISHING SPOTS: rod-and-jetty spots on grass beside REAL water (ponds + the
  // big country lakes), spaced apart so each water body gets its own. Extra 'fish'
  // nodes — same existing skilling economy (fishing → fish → stockpile).
  function _placeFishSpots(CX, CY) {
    const spots = [];
    for (let r = 2; r < ROWS - 2 && spots.length < 3; r++) for (let c = 2; c < COLS - 2 && spots.length < 3; c++) {
      if (baseGrid[r][c] !== T.WATER) continue;
      for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const nc = c + dc, nr = r + dr;
        if (!inb(nc, nr) || baseGrid[nr][nc] !== T.GRASS) continue;
        if (spots.some(s => Math.hypot(s.col - nc, s.row - nr) < 18)) continue;   // one per water body
        nodes.push({ col: nc, row: nr, kind: 'fish' });
        spots.push({ col: nc, row: nr });
        locations['fish'] = { col: nc, row: nr };
        break;
      }
    }
    if (!spots.length) _placeNode('fish', CX - 18, CY + 12, CX, CY);   // fallback if no water at all
  }

  // find a grass tile near (tc,tr), clear a little glade, register it as a skilling node + location
  function _placeNode(kind, tc, tr, CX, CY) {
    for (let rad = 0; rad < 24; rad++) for (let dr = -rad; dr <= rad; dr++) for (let dc = -rad; dc <= rad; dc++) {
      const c = tc + dc, r = tr + dr;
      if (!inb(c, r) || baseGrid[r][c] !== T.GRASS || Math.hypot(c - CX, r - CY) < 9) continue;
      for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) if (inb(c + a, r + b) && baseGrid[r + b][c + a] === T.TREE) bset(c + a, r + b, T.GRASS);
      nodes.push({ col: c, row: r, kind }); locations[kind] = { col: c, row: r };
      return;
    }
  }
  // the tile just OUTSIDE a building's door (where visitors stand before entering)
  function _doorOut(b) {
    if (b.door === 'N') return [b.c + (b.w >> 1), b.r - 1];
    if (b.door === 'W') return [b.c - 1, b.r + (b.h >> 1)];
    if (b.door === 'E') return [b.c + b.w, b.r + (b.h >> 1)];
    return [b.c + (b.w >> 1), b.r + b.h];
  }

  // ── CONNECTIVITY GUARANTEE (gen-time only): the organic scatter + lakes can
  // strand a venue/home/node in a landlocked pocket (school behind a lake, a bar
  // walled in by woods — agents then path-fail forever). BFS the open terrain
  // from the plaza; every unreachable door-front / node gets a corridor found by
  // a tiny Dijkstra that prefers open ground but may carve TREE/WATER into PATH
  // (a hewn lane / plank causeway). Never touches buildings or mountains.
  function _connectTown(CX, CY) {
    const pass = t => t === T.GRASS || t === T.PATH || t === T.PLAZA;
    const reach = () => {
      const seen = new Uint8Array(COLS * ROWS), q = [];
      let sc = CX, sr = CY;                                    // nearest passable tile to the plaza centre
      outer: for (let rad = 0; rad < 24; rad++) for (let dr = -rad; dr <= rad; dr++) for (let dc = -rad; dc <= rad; dc++) {
        if (inb(CX + dc, CY + dr) && pass(baseGrid[CY + dr][CX + dc])) { sc = CX + dc; sr = CY + dr; break outer; }
      }
      seen[sr * COLS + sc] = 1; q.push([sc, sr]);
      for (let i = 0; i < q.length; i++) {
        const [c, r] = q[i];
        for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const nc = c + dc, nr = r + dr;
          if (!inb(nc, nr) || seen[nr * COLS + nc] || !pass(baseGrid[nr][nc])) continue;
          seen[nr * COLS + nc] = 1; q.push([nc, nr]);
        }
      }
      return seen;
    };
    const targets = buildings.map(_doorOut).concat(nodes.map(n => [n.col, n.row]));
    let seen = reach();
    for (const [tc, tr] of targets) {
      if (!inb(tc, tr) || seen[tr * COLS + tc]) continue;
      if (_carveCorridor(tc, tr, seen)) seen = reach();        // recompute after each carve
    }
  }
  // Dijkstra from (c0,r0) to ANY already-reachable cell, then carve the found path
  function _carveCorridor(c0, r0, seen) {
    const dist = new Float64Array(COLS * ROWS).fill(Infinity);
    const prev = new Int32Array(COLS * ROWS).fill(-1);
    const cost = t => (t === T.GRASS || t === T.PATH || t === T.PLAZA) ? 1
                    : (t === T.TREE || t === T.WATER) ? 4 : Infinity;   // buildings/mountains: never
    const pq = [[0, c0 + r0 * COLS]];
    dist[c0 + r0 * COLS] = 0;
    let guard = 0;
    while (pq.length && guard++ < 40000) {
      pq.sort((a, b) => a[0] - b[0]);                          // tiny frontier; gen-time only
      const [d, i] = pq.shift();
      if (d > dist[i]) continue;
      if (seen[i]) {                                           // met the connected world → carve back
        for (let j = i; j >= 0; j = prev[j]) {
          const c = j % COLS, r = (j / COLS) | 0;
          if (baseGrid[r][c] === T.TREE || baseGrid[r][c] === T.WATER) bset(c, r, T.PATH);
        }
        return true;
      }
      const c = i % COLS, r = (i / COLS) | 0;
      for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const nc = c + dc, nr = r + dr;
        if (!inb(nc, nr)) continue;
        const st = cost(baseGrid[nr][nc]);
        if (!isFinite(st)) continue;
        const ni = nc + nr * COLS, nd = d + st;
        if (nd < dist[ni]) { dist[ni] = nd; prev[ni] = i; pq.push([nd, ni]); }
      }
    }
    return false;
  }

  function _park(x0, y0, w, h, rnd, ri) {
    for (let n = 0; n < (w * h) / 6; n++) { const cc = x0 + ri(0, w - 1), rr = y0 + ri(0, h - 1); if (rnd() < 0.6) bset(cc, rr, T.TREE); }
  }
  function _pond(x0, y0, w, h) {
    const cx = x0 + w / 2, cy = y0 + h / 2, rx = w * 0.46, ry = h * 0.44;   // bigger
    for (let r = y0 - 1; r < y0 + h + 1; r++) for (let c = x0 - 1; c < x0 + w + 1; c++) {
      if (!inb(c, r) || baseGrid[r][c] !== T.GRASS) continue;               // never eat paths/trees/buildings
      const dx = (c - cx) / rx, dy = (r - cy) / ry;
      const wob = 0.70 + 0.34 * Math.sin(c * 0.9 + r * 1.3) + 0.12 * Math.sin(c * 2.1);  // organic shoreline
      if (dx * dx + dy * dy <= wob) bset(c, r, T.WATER);
    }
  }
  function _blob(c, r, rad, rnd) {
    for (let dr = -rad; dr <= rad; dr++) for (let dc = -rad; dc <= rad; dc++)
      if (dc * dc + dr * dr <= rad * rad && rnd() < 0.7 && baseGrid[r + dr] && baseGrid[r + dr][c + dc] === T.GRASS) bset(c + dc, r + dr, T.TREE);
  }
  function _decorate(rnd, ri, chance) {
    for (let r = 1; r < ROWS - 1; r++) for (let c = 1; c < COLS - 1; c++) {
      const t = baseGrid[r][c];
      if (t === T.PATH && chance(0.03) && (baseGrid[r][c + 1] === T.GRASS || baseGrid[r][c - 1] === T.GRASS)) decor.push({ x: (c + 0.5) * TILE, y: (r + 0.5) * TILE, kind: 'lamp' });
      else if (t === T.PLAZA && chance(0.05)) decor.push({ x: (c + 0.5) * TILE, y: (r + 0.5) * TILE, kind: 'bench' });
      else if (t === T.GRASS && chance(0.02)) decor.push({ x: (c + ri(2, 8) / 10) * TILE, y: (r + ri(2, 8) / 10) * TILE, kind: rnd() < 0.6 ? 'bush' : 'rock' });
    }
  }

  // ── stamp a building into `grid` (walls + floor + door) ──
  function _stamp(b) {
    if (b.kind === 'hq' && b.sections && b.sections.length) return _stampCompound(b);
    for (let r = b.r; r < b.r + b.h; r++) for (let c = b.c; c < b.c + b.w; c++) if (inb(c, r)) grid[r][c] = T.WALL;
    for (let r = b.r + 1; r < b.r + b.h - 1; r++) for (let c = b.c + 1; c < b.c + b.w - 1; c++) if (inb(c, r)) grid[r][c] = T.FLOOR;
    if (b.kind === 'hq') {                    // organic HQ: chamfered corners (octagon-ish)
      for (const [cc, rr, sx, sy] of [[b.c, b.r, 1, 1], [b.c + b.w - 1, b.r, -1, 1],
                                      [b.c, b.r + b.h - 1, 1, -1], [b.c + b.w - 1, b.r + b.h - 1, -1, -1]]) {
        if (inb(cc, rr)) grid[rr][cc] = T.GRASS;                       // cut the sharp corner
        if (inb(cc + sx, rr)) grid[rr][cc + sx] = T.WALL;              // diagonal wall step
        if (inb(cc, rr + sy)) grid[rr + sy][cc] = T.WALL;
      }
    }
    let dc, dr;
    if (b.door === 'N') { dc = b.c + (b.w / 2 | 0); dr = b.r; }
    else if (b.door === 'W') { dc = b.c; dr = b.r + (b.h / 2 | 0); }
    else if (b.door === 'E') { dc = b.c + b.w - 1; dr = b.r + (b.h / 2 | 0); }
    else { dc = b.c + (b.w / 2 | 0); dr = b.r + b.h - 1; }
    if (inb(dc, dr)) grid[dr][dc] = T.FLOOR;
    // Layer-3 interior doors are openings → walkable FLOOR so pathfinding allows them
    // (a door on the wall ring punches a real opening; on an interior tile it stays floor).
    if (b.interior) for (const it of b.interior) {
      if (it.kind !== 'door') continue;
      const ic = b.c + it.lc, ir = b.r + it.lr;
      if (inb(ic, ir)) grid[ir][ic] = T.FLOOR;
    }
    return { col: dc, row: dr };
  }

  // ── compose base + buildings → grid, recompute locations, bake ──
  function rasterize() {
    grid = baseGrid.map(row => row.slice());
    houseSlots = [];
    const deptKeys = ['storefront', 'image', 'video', 'audio', 'models3d', 'publishing', 'devlab', 'resell', 'trends'];
    for (const b of buildings) {
      _stamp(b);
      const interior = { col: b.c + (b.w / 2 | 0), row: b.r + (b.h / 2 | 0) };
      if (b.kind === 'hq') {
        if (!(b.sections && b.sections.length)) {     // compound-stage desks are registered by _hqCompound's struct pass
          const dcx = [b.c + 4, b.c + (b.w / 2 | 0), b.c + b.w - 4], dcy = [b.r + 3, b.r + (b.h / 2 | 0), b.r + b.h - 3];
          deptKeys.forEach((k, i) => { locations['desk:' + k] = { col: dcx[i % 3], row: dcy[i / 3 | 0] }; });
        }
        locations['defense'] = { col: b.c + (b.w / 2 | 0), row: b.r + b.h + 2 };   // rally point south of HQ (raid)
      } else if (b.loc) {
        locations[b.loc] = interior;
        // standalone dept buildings have no HQ desk — their "desk" IS the building
        // (Research Lab + the four homeless systems: Mail / Homelab / Pearl / Assistant)
        if (['research', 'mail', 'homelab', 'pearl', 'assistant'].includes(b.loc)) locations['desk:' + b.loc] = interior;
      }
      else if (b.kind === 'house') houseSlots.push(interior);
    }
    if (!houseSlots.length) houseSlots.push({ col: COLS / 2 | 0, row: ROWS / 2 | 0 });
    _bake();
  }

  function build(saved) {
    _genBase();
    // Capture the freshly-placed NEW buildings so an OLDER saved layout (which predates
    // them) can gain them without a destructive full regen that wipes hand-edits:
    // the four standalone dept works + the University 🎓 and the (gated) video store.
    const _NEW_DEPTS = new Set(['mail', 'homelab', 'pearl', 'assistant', 'school', 'nsfw']);
    const _newDeptBldgs = buildings.filter(b => b.loc && _NEW_DEPTS.has(b.loc)).map(b => ({ ...b }));
    const _freshNodes = nodes.map(n => ({ ...n }));   // fresh gen's node set (ore deposits + fishing spots)
    if (saved && Array.isArray(saved.buildings) && saved.buildings.length) {
      buildings = saved.buildings.map(b => ({ ...b }));
      _nextId = Math.max(0, ...buildings.map(b => b.id || 0)) + 1;
      // non-destructive merge: add any new building the saved layout is missing, with
      // a fresh id (so University/Video-Store/Mail/… show up; autosave then persists them).
      const haveLocs = new Set(buildings.map(b => b.loc).filter(Boolean));
      for (const nb of _newDeptBldgs) if (!haveLocs.has(nb.loc)) { nb.id = _nextId++; buildings.push(nb); }
      // label upgrades ride along for saved maps — ONLY when the label is still the old
      // default (hand-renamed buildings are never touched).
      const _RELABEL = { exec: ['Exec Office 💼', "Boss's Office 👔"], townhall: ['Town Hall 🏛️', 'Grand Town Hall 🏛️'] };
      for (const b of buildings) { const rl = b.loc && _RELABEL[b.loc]; if (rl && b.label === rl[0]) b.label = rl[1]; }
      if (Array.isArray(saved.decor)) decor = saved.decor.map(d => ({ ...d }));
      if (Array.isArray(saved.nodes) && saved.nodes.length) {
        nodes = saved.nodes.map(n => ({ ...n }));
        // merge in the NEW ore deposits / fishing spots for layouts that predate them:
        // top the saved count of each kind up to the fresh gen's count (never removes).
        for (const kind of ['mine', 'fish']) {
          const have = nodes.filter(n => n.kind === kind).length;
          const fresh = _freshNodes.filter(n => n.kind === kind);
          for (let i = have; i < fresh.length; i++) nodes.push({ ...fresh[i] });
        }
        _rebuildLocations();
      }
      if (Array.isArray(saved.landmarks)) landmarks = saved.landmarks.map(l => ({ ...l }));
    }
    _applyHqStageToBuildings();   // HQ progression stage (era) patches the HQ descriptor before the bake
    _leisureSpots();
    rasterize();
  }

  // ── public-space leisure destinations (Mayor's park & plaza upgrade) ──
  // Anchored to the decor that's ACTUALLY on the map (works with saved/edited
  // layouts) so agents walk to what you see: a bench to sit on, the plaza
  // fountain to admire, and a picnic spot on the green (created on first run
  // if the layout predates it, then saved with the layout like any decor).
  function _leisureSpots() {
    const toTile = d => ({ col: Math.max(1, Math.min(COLS - 2, d.x / TILE | 0)),
                           row: Math.max(1, Math.min(ROWS - 2, d.y / TILE | 0)) });
    const fount = decor.find(d => d.kind === 'fountain');
    if (fount) locations['plaza'] = toTile(fount);
    const bench = decor.find(d => d.kind === 'bench');
    if (bench) locations['bench'] = toTile(bench);
    let pic = decor.find(d => d.kind === 'picnic_table');
    if (!pic) {
      const p = locations['park'] || { col: COLS / 2 | 0, row: ROWS / 2 | 0 };
      pic = { x: (p.col + 2.5) * TILE, y: (p.row + 1.5) * TILE, kind: 'picnic_table' };
      decor.push(pic);
    }
    locations['picnic'] = toTile(pic);
    for (const k of ['plaza', 'bench', 'picnic'])   // never strand an agent
      if (!locations[k]) locations[k] = locations['park'];
  }

  // ── EDIT API (play-god) ──
  const byId = id => buildings.find(b => b.id === id);
  function moveBuilding(id, c, r) {
    const b = byId(id); if (!b) return;
    b.c = Math.max(1, Math.min(COLS - b.w - 1, Math.round(c)));
    b.r = Math.max(1, Math.min(ROWS - b.h - 1, Math.round(r)));
    // staged compound HQ: keep the persisted BASE geometry centred on the new
    // spot (exportLayout saves _baseGeom, so a drag must move it too)
    if (b.sections && b._baseGeom) {
      b._baseGeom.c = Math.round(b.c + b.w / 2 - b._baseGeom.w / 2);
      b._baseGeom.r = Math.round(b.r + b.h / 2 - 1 - b._baseGeom.h / 2);   // -1 mirrors the apply offset
    }
    rasterize();
  }
  function resizeBuilding(id, dw, dh) {
    const b = byId(id); if (!b) return;
    if (b.sections) return;    // staged compound HQ: geometry is owned by the stage layout, not the resize handles
    b.w = Math.max(3, Math.min(30, b.w + dw));
    b.h = Math.max(3, Math.min(24, b.h + dh));
    b.c = Math.min(b.c, COLS - b.w - 1); b.r = Math.min(b.r, ROWS - b.h - 1);
    if (b.interior) b.interior = b.interior.filter(e => e.lc < b.w && e.lr < b.h);   // drop interior items now outside the smaller footprint
    rasterize();
  }
  function addBuilding(kind, c, r) {
    const spec = { house: { w: 6, h: 6, label: '', house: true }, shop: { w: 6, h: 5, label: 'Shop', small: true },
                   tree: null }[kind] || { w: 6, h: 6 };
    if (kind === 'tree') { for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) bset(c + dc, r + dr, T.TREE); rasterize(); return null; }
    const b = { id: _nextId++, c: Math.round(c), r: Math.round(r), door: 'S', kind, loc: null,
                color: KIND_COLOR[kind] || '#6b7ba0', ...spec };
    buildings.push(b); rasterize(); return b.id;
  }
  function deleteBuilding(id) { buildings = buildings.filter(b => b.id !== id); rasterize(); }
  function setBuilding(id, patch) { const b = byId(id); if (b) { Object.assign(b, patch); rasterize(); } }
  function buildingAtTile(col, row) {
    for (let i = buildings.length - 1; i >= 0; i--) { const b = buildings[i]; if (col >= b.c && col < b.c + b.w && row >= b.r && row < b.r + b.h) return b; }
    return null;
  }
  // ── Layer-3 PER-TILE INTERIOR (play-god): doors / windows / objects on a BUILDING-LOCAL
  // grid (lc = col - b.c, lr = row - b.r). Building-local so they move with the building for
  // free; they ride exportLayout/build like any building field and auto-save via scheduleSave. ──
  function addInterior(id, col, row, kind) {
    const b = byId(id); if (!b) return false;
    const lc = col - b.c, lr = row - b.r;
    if (lc < 0 || lr < 0 || lc >= b.w || lr >= b.h) return false;         // must sit within the footprint
    const onRing = lc === 0 || lr === 0 || lc === b.w - 1 || lr === b.h - 1;
    if (onRing && kind !== 'door') return false;                         // only doors may sit on the wall ring (they are openings)
    if (!b.interior) b.interior = [];
    b.interior = b.interior.filter(e => !(e.lc === lc && e.lr === lr));   // one item per tile (replace)
    b.interior.push({ lc, lr, kind });
    rasterize();                                                         // re-stamp (door→FLOOR) + re-bake
    return true;
  }
  function removeInteriorAt(id, col, row) {
    const b = byId(id); if (!b || !b.interior || !b.interior.length) return false;
    const lc = col - b.c, lr = row - b.r, n = b.interior.length;
    b.interior = b.interior.filter(e => !(e.lc === lc && e.lr === lr));
    if (b.interior.length === n) return false;
    rasterize();
    return true;
  }
  // Stage-patched HQ fields (sections/doors/stage*) are VIEW state owned by the
  // world_hq stage system — never persisted into the layout blob. The hq row
  // saves its BASE geometry so a load under any stage starts from the original
  // single-rect descriptor and the active stage re-patches it.
  const exportLayout = () => ({
    buildings: buildings.map(b => {
      const o = b.interior ? { ...b, interior: b.interior.map(e => ({ ...e })) } : { ...b };
      if (o.sections && o._baseGeom) Object.assign(o, o._baseGeom);
      delete o.sections; delete o.doors; delete o.stageKey; delete o.stageName; delete o._baseGeom;
      delete o._tw; delete o._twKey;
      return o;
    }),
    decor,
    nodes: nodes.map(n => ({ ...n })), landmarks: landmarks.map(l => ({ ...l })) });

  // ── AUTO-SAVE (play-god): persist hand edits without a manual 💾 click ──
  // Debounced so a drag/resize burst coalesces into ONE POST after it settles.
  // Called only from user-edit paths (never build()/rasterize()), so loading a
  // saved layout can't trigger a save loop. Toggle: window._wmLayoutAutosave
  // (set from the world_layout_autosave setting on tab load); undefined = ON.
  let _saveTimer = null;
  function scheduleSave() {
    if (window._wmLayoutAutosave === false) return;   // toggle off → only 💾 saves
    clearTimeout(_saveTimer);
    _saveTimer = setTimeout(() => {
      _saveTimer = null;
      try {
        api('/api/world/layout', { method: 'POST', body: JSON.stringify({ layout: exportLayout() }) })
          .then(() => { const el = document.getElementById('world-autosave-note'); if (el) { el.textContent = '✓ saved'; setTimeout(() => { if (el.textContent === '✓ saved') el.textContent = ''; }, 1400); } })
          .catch(() => {});
      } catch (e) { /* never let a save error break editing */ }
    }, 800);
  }
  // fine-grained decor placement (play-god) — pixel-precise, saved in the layout
  function addDecor(px, py, kind) { decor.push({ x: px, y: py, kind }); _bake(); }
  function removeDecorNear(px, py) {
    const bi = decorIndexNear(px, py);
    if (bi >= 0) { decor.splice(bi, 1); _bake(); return true; }
    return false;
  }
  function decorIndexNear(px, py) {
    let bi = -1, bd = 26 * 26;
    for (let i = 0; i < decor.length; i++) { const dx = decor[i].x - px, dy = decor[i].y - py, d = dx * dx + dy * dy; if (d < bd) { bd = d; bi = i; } }
    return bi;
  }
  function pickDecor(index) { if (index < 0 || index >= decor.length) return null; const d = decor[index]; decor.splice(index, 1); _bake(); return d; }
  function previewDecor(ctx, kind, px, py) { ctx.save(); ctx.globalAlpha = 0.7; _decorSprite(ctx, { x: px, y: py, kind }); ctx.restore(); }

  // ── resource NODES (play-god): mine/woodcut/farm/fish/build — positioned by tile ──
  function _rebuildLocations() {                       // keep locations[kind] pointing at a live node
    for (const n of nodes) locations[n.kind] = { col: n.col, row: n.row };
  }
  function nodeIndexNear(px, py) {
    let bi = -1, bd = 26 * 26;
    for (let i = 0; i < nodes.length; i++) { const p = tileToPx(nodes[i].col, nodes[i].row); const dx = p.x - px, dy = p.y - py, d = dx * dx + dy * dy; if (d < bd) { bd = d; bi = i; } }
    return bi;
  }
  function addNode(kind, col, row) {
    const n = { col: Math.max(0, Math.min(COLS - 1, Math.round(col))), row: Math.max(0, Math.min(ROWS - 1, Math.round(row))), kind };
    nodes.push(n); _rebuildLocations(); return n;
  }
  function pickNode(index) { if (index < 0 || index >= nodes.length) return null; const n = nodes[index]; nodes.splice(index, 1); _rebuildLocations(); return n; }
  function removeNodeAt(index) { if (index < 0 || index >= nodes.length) return false; nodes.splice(index, 1); _rebuildLocations(); return true; }

  // ── LANDMARKS (play-god): big park sprites (trees, well, pond) — positioned by tile ──
  function landmarkIndexNear(px, py) {
    let bi = -1, bd = 30 * 30;
    for (let i = 0; i < landmarks.length; i++) { const p = tileToPx(landmarks[i].col, landmarks[i].row); const dx = p.x - px, dy = p.y - py, d = dx * dx + dy * dy; if (d < bd) { bd = d; bi = i; } }
    return bi;
  }
  function addLandmark(kind, col, row, scale) {
    const l = { col: Math.max(0, Math.min(COLS - 1, Math.round(col))), row: Math.max(0, Math.min(ROWS - 1, Math.round(row))), kind };
    if (scale) l.scale = scale;
    landmarks.push(l); return l;
  }
  function pickLandmark(index) { if (index < 0 || index >= landmarks.length) return null; const l = landmarks[index]; landmarks.splice(index, 1); return l; }
  function removeLandmarkAt(index) { if (index < 0 || index >= landmarks.length) return false; landmarks.splice(index, 1); return true; }

  // per-tile hash → stable pseudo-random variation (cozy pixel-art texture)
  const hsh = (c, r, s) => { let x = ((c + 1) * 374761393 + (r + 1) * 668265263 + (s || 0) * 2246822519) >>> 0; x = ((x ^ (x >>> 13)) * 1274126177) >>> 0; return (x >>> 0) / 4294967296; };
  const FLOWERS = ['#e05a6a', '#e8c14a', '#e8e0e0', '#d97ac0'];

  // Layer 2: swap in ONE generated whole-world ground image (loads async, then
  // re-bakes). Terrain LOGIC (pathfinding/water/wear) stays on the grid — this is
  // a pure visual skin. Passing a falsy url reverts to procedural per-tile art.
  function setTerrainImage(url) {
    _terrainUrl = url || null;
    if (!url) { _terrainImg = null; _bake(); return; }
    const img = new Image();
    img.onload = () => { _terrainImg = img; _bake(); };
    img.onerror = () => { _terrainImg = null; };
    img.src = url;
  }

  // Flicker fix: set the generated terrain image WITHOUT re-baking. The caller runs
  // build()→_bake() right after, so the (single) bake sees _terrainImg already present
  // and paints the image directly — no procedural→image swap on load. Pass the source
  // url too so reheal() can re-decode it after a browser memory-eviction.
  function setTerrainImageEl(img, url) { _terrainImg = img || null; if (url !== undefined) _terrainUrl = url || null; }

  // Layer-2b: swap in ONE shared generated interior-floor texture (loads async, then
  // re-bakes). It is blitted under EVERY building interior in _building() and the
  // per-kind FLOOR_TINT is washed over it at low alpha so buildings still read
  // distinct. Passing a falsy url reverts to the procedural per-kind tint floor.
  function setFloorImage(url) {
    _floorUrl = url || null;
    if (!url) { _floorImg = null; _bake(); return; }
    const img = new Image();
    img.onload = () => { _floorImg = img; _bake(); };
    img.onerror = () => { _floorImg = null; };
    img.src = url;
  }
  // Flicker-free variant (mirrors setTerrainImageEl): set WITHOUT re-baking; the
  // caller runs build()→_bake() right after so the single bake already sees it.
  function setFloorImageEl(img, url) { _floorImg = img || null; if (url !== undefined) _floorUrl = url || null; }

  // ── SELF-HEAL: detect + recover a browser-evicted overview ───────────────────
  // terrainAlive(): sample a few points of the small always-resident _overview. If they're
  // ALL fully transparent, the browser reclaimed its backing store and the ground has
  // silently blanked. (Full-res chunks self-recover — they're re-baked on demand — so the
  // overview is the only long-lived surface worth watching.) Returns true when there's
  // nothing to heal (no overview yet) so callers don't thrash before the first bake.
  function terrainAlive() {
    if (!_overview) return true;
    try {
      const x = _overview.getContext('2d'), ow = _overview.width, oh = _overview.height;
      const pts = [[ow*0.5, oh*0.5], [ow*0.15, oh*0.2], [ow*0.85, oh*0.25], [ow*0.2, oh*0.8], [ow*0.8, oh*0.82]];
      for (const [px, py] of pts) {
        const d = x.getImageData(px | 0, py | 0, 2, 2).data;
        for (let i = 3; i < d.length; i += 4) if (d[i] !== 0) return true;  // any opaque pixel → alive
      }
      return false;   // every probe fully transparent → evicted
    } catch (e) { return true; }   // readback blocked → assume alive, don't thrash
  }

  // reheal(): re-decode the terrain + floor images FROM their source URLs (the in-memory
  // elements may themselves be evicted, so a plain _bake() isn't enough) and re-bake. With
  // no URLs it just re-bakes procedural ground, which also restores an evicted canvas.
  async function reheal() {
    if (_rehealing) return;
    _rehealing = true;
    try {
      const jobs = [];
      if (_terrainUrl) jobs.push(_reload(_terrainUrl).then(img => { if (img) _terrainImg = img; }));
      if (_floorUrl)   jobs.push(_reload(_floorUrl).then(img => { if (img) _floorImg = img; }));
      if (jobs.length) await Promise.all(jobs);
      _bake();   // restores procedural ground even if the images failed to reload
    } catch (e) {
      try { _bake(); } catch {}
    } finally { _rehealing = false; }
  }
  function _reload(url) {
    return new Promise(res => {
      const img = new Image();
      img.onload = () => { (img.decode ? img.decode().catch(() => {}) : Promise.resolve()).then(() => res(img)); };
      img.onerror = () => res(null);
      img.src = url + (url.includes('?') ? '&' : '?') + '_rh=' + (W + H);  // stable suffix, avoids a stale evicted cache entry
    });
  }

  // Layout-guided img2img — the "alpha map maker" helper. Exports the PROCEDURAL layout
  // (roads/water/plaza/fields + building footprints) as a base image so the terrain
  // generator's img2img matches your real map. Crucially it must NOT export a generated
  // terrain image (that'd feed the image back into itself), so we paint PROCEDURAL ground
  // (_terrainImg temporarily nulled) DIRECTLY into the w×h target via _paintRegion — no
  // giant intermediate canvas, and it's independent of the chunk cache. Returns a PNG
  // dataURL, or null on failure.
  function exportLayoutBase(w, h) {
    const saved = _terrainImg;
    try {
      _terrainImg = null;
      const off = document.createElement('canvas'); off.width = w; off.height = h;
      const octx = off.getContext('2d'); octx.imageSmoothingEnabled = false;
      octx.setTransform(w / W, 0, 0, h / H, 0, 0);             // world coords → w×h target
      _paintRegion(octx, 0, 0, COLS, ROWS);                    // full procedural map (structPass off: no side effects)
      octx.setTransform(1, 0, 0, 1, 0, 0);
      return off.toDataURL('image/png');
    } catch (e) {
      return null;
    } finally {
      _terrainImg = saved; _bake();                            // restore the live view (overview + chunk cache)
    }
  }

  // ── PAINT one tile-region of the ground into an (already-transformed) context ──
  // Pure drawing: terrain tiles + buildings + decor whose footprint touches the region
  // (+1-tile margin so overhanging walls/roofs aren't clipped at chunk seams). The ONLY
  // side effects (waterTiles / hqRooms / desk-locations) are guarded by _structPass, so
  // they run exactly once (the overview full pass) and never per-chunk.
  function _bIntersects(b, tc0, tr0, tc1, tr1) {
    return b.c < tc1 + 1 && b.c + b.w > tc0 - 1 && b.r < tr1 + 1 && b.r + b.h > tr0 - 1;
  }
  function _paintRegion(x, tc0, tr0, tc1, tr1) {
    const useImg = _terrainImg && _terrainImg.complete && _terrainImg.naturalWidth;
    if (useImg) x.drawImage(_terrainImg, 0, 0, W, H);   // whole ground image; the ctx transform clips it to this region
    for (let r = tr0; r < tr1; r++) for (let c = tc0; c < tc1; c++) {
      const t = grid[r][c];
      if (!useImg || t === T.FLOOR || t === T.WALL) _tile(x, c, r);
      if (t === T.WATER && _structPass) waterTiles.push({ col: c, row: r });   // live-water list: full pass only
    }
    // Building floors/detail first, then the thin themed wall shell on top of the edge.
    for (const b of buildings) if (_bIntersects(b, tc0, tr0, tc1, tr1)) _building(x, b);
    for (const b of buildings) if (_bIntersects(b, tc0, tr0, tc1, tr1)) _drawBuildingShell(x, b);
    for (const d of decor) {
      const dc = d.x / TILE, dr = d.y / TILE;
      if (dc >= tc0 - 1 && dc < tc1 + 1 && dr >= tr0 - 1 && dr < tr1 + 1) _decorSprite(x, d);
    }
  }

  // Whole-map LOW-RES overview: the FINEST pyramid level (W/2) + the fallback under baking
  // chunks. This is the ONE full pass over the map, so it also (re)populates waterTiles/
  // hqRooms/locations via _structPass. Small + always resident; if the browser evicts it,
  // terrainAlive()/reheal() rebuild it. The coarser pyramid levels are pure downscales of
  // THIS canvas (see _mip), so the structPass runs exactly once per bake — never per level.
  function _bakeOverview() {
    if (!_overview) { _overview = document.createElement('canvas'); _overview.width = Math.ceil(W / _OV); _overview.height = Math.ceil(H / _OV); }
    const x = _overview.getContext('2d'); x.imageSmoothingEnabled = false;
    x.setTransform(1 / _OV, 0, 0, 1 / _OV, 0, 0);       // draw in world coords, scaled down
    x.clearRect(0, 0, W, H);
    _structPass = true;
    waterTiles = [];
    _paintRegion(x, 0, 0, COLS, ROWS);
    _structPass = false;
    x.setTransform(1, 0, 0, 1, 0, 0);
  }

  // Lazily build a COARSER pyramid level (index i → downscale _MIP_D[i]) by smoothly
  // downscaling the finest overview — cheaper + crisper than upscaling W/2 when zoomed way
  // out. Nulled on _bake() so it rebuilds from the fresh overview.
  function _mip(i) {
    if (!_overview) return null;
    if (!_MIPS[i]) {
      const d = _MIP_D[i], cv = document.createElement('canvas');
      cv.width = Math.ceil(W / d); cv.height = Math.ceil(H / d);
      const x = cv.getContext('2d');
      x.imageSmoothingEnabled = true; x.imageSmoothingQuality = 'high';
      x.drawImage(_overview, 0, 0, cv.width, cv.height);
      _MIPS[i] = cv;
    }
    return _MIPS[i];
  }
  // Pick the COARSEST level that still renders crisp (≤1 texel per screen pixel) at `scale`:
  // a level of downscale D maps each texel to D*scale screen pixels, so it upscales (blurs)
  // once D*scale > 1. Return the largest such D (coarsest → cheapest blit), else the finest.
  function _baseFor(scale) {
    for (let i = _MIP_D.length - 1; i >= 0; i--) if (_MIP_D[i] * scale <= 1.0001) return _mip(i) || _overview;
    return _overview;
  }

  // One FULL-RES chunk (baked on demand for the visible viewport, evicted when off-screen).
  function _bakeChunk(cx, cy) {
    const tc0 = cx * CHUNK_CW, tr0 = cy * CHUNK_CH;
    const tc1 = Math.min(COLS, tc0 + CHUNK_CW), tr1 = Math.min(ROWS, tr0 + CHUNK_CH);
    const wx = tc0 * TILE, wy = tr0 * TILE, ww = (tc1 - tc0) * TILE, wh = (tr1 - tr0) * TILE;
    const cv = document.createElement('canvas'); cv.width = ww; cv.height = wh;
    const x = cv.getContext('2d'); x.imageSmoothingEnabled = false;
    x.translate(-wx, -wy);                               // world coords → chunk-local
    _paintRegion(x, tc0, tr0, tc1, tr1);                 // _structPass stays false → pure draw
    return { cv, wx, wy, seen: performance.now() };
  }

  // Structure/appearance changed (edit, terrain/floor image swap, decor add): rebuild the
  // overview (the one full pass) and drop the full-res cache so visible chunks re-bake lazily.
  function _bake() {
    _bakeOverview();
    _MIPS[0] = _MIPS[1] = null;   // coarser pyramid levels rebuild lazily from the fresh overview
    _chunks.clear();
  }

  const _hex = (h, a) => { const n = parseInt((h || '#888').slice(1), 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`; };

  // the 9 department rooms inside the HQ — tinted zones with divider walls + props
  const DEPT_ORDER = ['storefront', 'image', 'video', 'audio', 'models3d', 'publishing', 'devlab',
                      'resell', 'trends', 'portal', 'social', 'finance', 'netsec'];
  const DEPT_TINT = { storefront: '#6aa6d6', image: '#e0b050', video: '#e07a5a', audio: '#7ac0a0', models3d: '#b48fe0', publishing: '#e090c0', devlab: '#8fb3ff', resell: '#f0a860', trends: '#7fd4a0',
                      portal: '#2dd4bf', social: '#38bdf8', finance: '#eab308', netsec: '#94a3b8' };
  // expose the 9 room centres (px) so the factory can flow products desk→desk
  let hqRooms = [];
  // walls around a room with a centered DOOR gap on the hallway-facing side
  function _roomWalls(x, zx, zy, zw, zh, doorSide) {
    x.strokeStyle = 'rgba(44,33,22,.75)'; x.lineWidth = 2;
    const gap = Math.min(15, zw * 0.42), gx = zx + zw / 2 - gap / 2;
    x.beginPath();
    if (doorSide === 'top') { x.moveTo(zx, zy); x.lineTo(gx, zy); x.moveTo(gx + gap, zy); x.lineTo(zx + zw, zy); }
    else { x.moveTo(zx, zy); x.lineTo(zx + zw, zy); }
    if (doorSide === 'bottom') { x.moveTo(zx, zy + zh); x.lineTo(gx, zy + zh); x.moveTo(gx + gap, zy + zh); x.lineTo(zx + zw, zy + zh); }
    else { x.moveTo(zx, zy + zh); x.lineTo(zx + zw, zy + zh); }
    x.moveTo(zx, zy); x.lineTo(zx, zy + zh); x.moveTo(zx + zw, zy); x.lineTo(zx + zw, zy + zh);
    x.stroke();
    // door frame posts
    x.strokeStyle = 'rgba(210,170,110,.5)'; x.lineWidth = 1.2;
    const dy = doorSide === 'top' ? zy : zy + zh;
    x.beginPath(); x.moveTo(gx, dy - 2); x.lineTo(gx, dy + 2); x.moveTo(gx + gap, dy - 2); x.lineTo(gx + gap, dy + 2); x.stroke();
  }

  // The HQ interior: two rows of department rooms off a central HALLWAY (a real
  // building, not a plain grid). 5 rooms up top, 4 below, each with a door onto
  // the corridor. Gear + nameplates are drawn LIVE (they need async sprites).
  function _hqRooms(x, b) {
    if (_structPass) hqRooms = [];        // geometry list rebuilt only on the full pass, not per-chunk
    const ix0 = (b.c + 1) * TILE, iy0 = (b.r + 1) * TILE, iw = (b.w - 2) * TILE, ih = (b.h - 2) * TILE;
    const g = _deptRoomBands(x, ix0, iy0, iw, ih, DEPT_ORDER);
    _reception(x, ix0, g.hallY);
  }
  // Two rows of department rooms off a central hallway, constrained to an
  // ARBITRARY interior rect — shared by the classic single-rect HQ (all depts)
  // and the Iron/Steel-Age office wing (its own dept subset). Pushes hqRooms +
  // desk locations during the struct pass exactly like before.
  function _deptRoomBands(x, ix0, iy0, iw, ih, depts) {
    const hallH = Math.max(TILE * 1.2, Math.round(ih * 0.15));
    const topH = Math.floor((ih - hallH) / 2), botH = ih - hallH - topH, hallY = iy0 + topH;
    // ── central hallway floor + carpet runner + planters ──
    x.fillStyle = 'rgba(54,60,74,.55)'; x.fillRect(ix0, hallY, iw, hallH);
    x.fillStyle = 'rgba(150,96,72,.30)'; x.fillRect(ix0 + 3, hallY + hallH / 2 - 2, iw - 6, 4);   // runner
    x.strokeStyle = 'rgba(0,0,0,.25)'; x.lineWidth = 1; x.strokeRect(ix0, hallY, iw, hallH);
    for (let px = ix0 + 10; px < ix0 + iw - 6; px += 34) {                                          // hallway planters
      x.fillStyle = '#6b4c2f'; x.fillRect(px - 2, hallY + hallH - 5, 4, 4);
      x.fillStyle = '#2f8542'; x.beginPath(); x.arc(px, hallY + hallH - 6, 3, 0, 6.283); x.fill();
    }
    const _half = Math.ceil(depts.length / 2);
    const bands = [{ y: iy0, h: topH, depts: depts.slice(0, _half), door: 'bottom' },
                   { y: hallY + hallH, h: botH, depts: depts.slice(_half), door: 'top' }];
    for (const band of bands) {
      const rw = iw / band.depts.length;
      band.depts.forEach((dept, i) => {
        const zx = ix0 + i * rw, zy = band.y, zw = rw, zh = band.h, cc = DEPT_TINT[dept] || '#8ab';
        if (_structPass) {
          hqRooms.push({ dept, x: zx + zw / 2, y: zy + zh / 2, x0: zx, y0: zy, w: zw, h: zh, tint: cc, door: band.door });
          locations['desk:' + dept] = { col: Math.round(zx / TILE + zw / TILE / 2 - 0.5), row: Math.round(zy / TILE + zh / TILE / 2 - 0.5) };  // agents operate here
        }
        x.fillStyle = _hex(cc, 0.15); x.fillRect(zx + 1, zy + 1, zw - 2, zh - 2);                   // floor tint
        // OFFICE + FACTORY split: carpet-tiled office along the back wall, a
        // concrete production strip mid-room where the live WF machine line runs.
        const backTop = band.door === 'bottom';
        const carY = backTop ? zy + 2 : zy + zh * 0.72, carH = backTop ? zh * 0.42 : zh * 0.26;
        for (let ty = carY; ty < carY + carH - 3; ty += 7)                                          // carpet tiles
          for (let tx = zx + 3; tx < zx + zw - 8; tx += 7) {
            x.fillStyle = _hex(cc, ((tx / 7 | 0) + (ty / 7 | 0)) % 2 ? 0.13 : 0.22); x.fillRect(tx, ty, 6, 6);
          }
        x.fillStyle = 'rgba(120,126,138,.30)'; x.fillRect(zx + 2, zy + zh * 0.52, zw - 4, zh * 0.22); // concrete slab
        const beltY = zy + zh * 0.60 + 3, bx0 = zx + zw * 0.12, bx1 = zx + zw * 0.88;
        x.fillStyle = '#20242c'; x.fillRect(bx0, beltY, bx1 - bx0, 7);                              // conveyor belt
        x.fillStyle = 'rgba(160,170,185,.5)'; for (let rx = bx0 + 3; rx < bx1 - 2; rx += 6) x.fillRect(rx, beltY + 1, 1, 5);  // rollers
        for (let rx = bx0; rx < bx1 - 3; rx += 8) {                                                 // hazard stripe
          x.fillStyle = '#d8b13c'; x.fillRect(rx, beltY + 9, 4, 2);
          x.fillStyle = '#23262e'; x.fillRect(rx + 4, beltY + 9, 4, 2);
        }
        // office corner: desk + glowing monitor + chair + filing cabinet
        const deskY = backTop ? zy + zh * 0.24 : zy + zh * 0.80;
        x.fillStyle = '#5d4328'; x.fillRect(zx + 7, deskY, 18, 7);
        x.fillStyle = 'rgba(255,255,255,.12)'; x.fillRect(zx + 7, deskY, 18, 1.5);
        x.fillStyle = '#1c2530'; x.fillRect(zx + 11, deskY - 5, 9, 6);                              // monitor
        x.fillStyle = '#7fd0ff'; x.fillRect(zx + 12, deskY - 4, 7, 4);                              // screen glow
        x.fillStyle = '#39414f'; x.beginPath(); x.arc(zx + 16, deskY + (backTop ? 11 : -4), 3, 0, 6.283); x.fill();  // chair
        x.fillStyle = '#767d8a'; x.fillRect(zx + zw - 15, deskY - 2, 8, 10);                        // filing cabinet
        x.fillStyle = 'rgba(0,0,0,.35)'; x.fillRect(zx + zw - 15, deskY + 1, 8, 1); x.fillRect(zx + zw - 15, deskY + 4, 8, 1);
        // pallet of finished boxes at the belt's end
        x.fillStyle = '#8a6a3f'; x.fillRect(bx1 - 2, beltY - 2, 10, 10);
        x.fillStyle = '#c89b55'; x.fillRect(bx1 - 1, beltY - 1, 8, 4); x.fillRect(bx1 - 1, beltY + 4, 8, 4);
        x.fillStyle = 'rgba(0,0,0,.3)'; x.fillRect(bx1 - 1, beltY + 3, 8, 1);
        _deptSignature(x, dept, zx, zy, zw, zh, deskY, cc);   // each studio's own equipment
        _roomWalls(x, zx, zy, zw, zh, band.door);
        // shelf along the back wall (the wall away from the hallway)
        const backY = band.door === 'bottom' ? zy + 4 : zy + zh - 6, shw = Math.min(zw - 10, 24), shx = zx + (zw - shw) / 2;
        x.fillStyle = '#6b4a2c'; x.fillRect(shx, backY, shw, 3);
        for (let k = 0; k < (shw / 6 | 0); k++) { x.fillStyle = _hex([cc, '#d8c090', '#c07a5a'][(i + k) % 3], 0.9); x.fillRect(shx + 1 + k * 6, backY - 3, 4, 3); }
        // inner AO so the room reads enclosed
        x.strokeStyle = 'rgba(0,0,0,.18)'; x.lineWidth = 3; x.strokeRect(zx + 2.5, zy + 2.5, zw - 5, zh - 5);
      });
    }
    return { hallY, hallH };
  }
  // reception: a welcome desk beside the hallway entrance
  function _reception(x, ix0, hallY) {
    x.fillStyle = '#5d4328'; x.fillRect(ix0 + 6, hallY + 3, 22, 6);
    x.fillStyle = 'rgba(255,255,255,.14)'; x.fillRect(ix0 + 6, hallY + 3, 22, 1.5);
    x.fillStyle = '#e8c14a'; x.fillRect(ix0 + 24, hallY + 4, 2, 2);                                 // bell
    x.fillStyle = '#39414f'; x.beginPath(); x.arc(ix0 + 17, hallY + 12, 3, 0, 6.283); x.fill();
  }
  // each department room's SIGNATURE equipment — no two studios look alike
  function _deptSignature(x, dept, zx, zy, zw, zh, deskY, cc) {
    const px0 = zx + zw - 34, py0 = deskY - 3;              // beside the filing cabinet
    if (dept === 'image') {                                  // easel with a canvas
      x.fillStyle = '#6b4a2c'; x.fillRect(px0 + 2, py0, 2, 12); x.fillRect(px0 + 10, py0, 2, 12);
      x.fillRect(px0 + 1, py0 + 10, 12, 1.5);
      x.fillStyle = '#efe6d2'; x.fillRect(px0 + 1, py0, 12, 9);
      x.fillStyle = _hex(cc, .7); x.fillRect(px0 + 3, py0 + 2, 5, 4); x.fillStyle = '#5f97c4'; x.fillRect(px0 + 8, py0 + 3, 3, 3);
    } else if (dept === 'video') {                           // tripod camera + greenscreen
      x.fillStyle = '#3fae62'; x.fillRect(px0 - 2, py0 - 2, 16, 5);              // greenscreen strip
      x.fillStyle = '#22262e'; x.fillRect(px0 + 4, py0 + 4, 7, 5);               // camera body
      x.fillStyle = '#7fd0ff'; x.fillRect(px0 + 9.5, py0 + 5, 2, 3);             // lens
      x.strokeStyle = '#4a505c'; x.lineWidth = 1.2;                              // tripod
      x.beginPath(); x.moveTo(px0 + 7, py0 + 9); x.lineTo(px0 + 3, py0 + 15); x.moveTo(px0 + 7, py0 + 9); x.lineTo(px0 + 11, py0 + 15); x.stroke();
    } else if (dept === 'audio') {                           // mixing desk + monitors + foam
      x.fillStyle = '#2c313c'; for (let i = 0; i < 5; i++) x.fillRect(px0 - 2 + i * 3.4, py0 - 2, 2.4, 3);  // foam wedges
      x.fillStyle = '#39414f'; x.fillRect(px0, py0 + 4, 14, 6);
      x.fillStyle = '#8fe0a0'; for (let i = 0; i < 4; i++) x.fillRect(px0 + 1.5 + i * 3.4, py0 + 5 + (i % 2), 1.4, 3);  // faders
      x.fillStyle = '#1c2530'; x.fillRect(px0 - 3, py0 + 4, 3, 6); x.fillRect(px0 + 14.5, py0 + 4, 3, 6);   // speakers
    } else if (dept === 'models3d') {                         // printer farm + spools
      for (let i = 0; i < 2; i++) {
        x.fillStyle = '#3a4150'; x.fillRect(px0 + i * 9, py0, 7, 10);
        x.fillStyle = '#f0a860'; x.fillRect(px0 + 2 + i * 9, py0 + 5, 3, 2.5);   // hot print glow
      }
      x.fillStyle = '#c25b4e'; x.beginPath(); x.arc(px0 + 4, py0 + 14, 2.4, 0, 6.283); x.fill();
      x.fillStyle = '#4e7fc2'; x.beginPath(); x.arc(px0 + 11, py0 + 14, 2.4, 0, 6.283); x.fill();
    } else if (dept === 'storefront') {                       // display shelf of products
      x.fillStyle = '#6b4a2c'; x.fillRect(px0 - 2, py0, 18, 12);
      x.fillStyle = 'rgba(0,0,0,.3)'; x.fillRect(px0 - 2, py0 + 5.5, 18, 1);
      for (let i = 0; i < 4; i++) { x.fillStyle = _hex(['#d8c090', '#c07a5a', cc, '#7ac0a0'][i % 4], .95); x.fillRect(px0 + i * 4.2, py0 + 1.5, 3, 3.5); x.fillRect(px0 + i * 4.2, py0 + 7, 3, 3.5); }
    } else if (dept === 'publishing') {                       // press rollers + paper stacks
      x.fillStyle = '#4a505c'; x.fillRect(px0, py0 + 2, 12, 7);
      x.fillStyle = '#8a919f'; x.beginPath(); x.arc(px0 + 3.5, py0 + 2, 2.4, 0, 6.283); x.fill(); x.beginPath(); x.arc(px0 + 8.5, py0 + 2, 2.4, 0, 6.283); x.fill();
      x.fillStyle = '#efe6d2'; x.fillRect(px0 + 13.5, py0 + 4, 6, 1.6); x.fillRect(px0 + 13.5, py0 + 6.2, 6, 1.6); x.fillRect(px0 + 13.5, py0 + 8.4, 6, 1.6);
    } else if (dept === 'devlab') {                           // server rack, blinking lights
      x.fillStyle = '#1a2029'; x.fillRect(px0 + 2, py0 - 2, 11, 15);
      for (let i = 0; i < 5; i++) {
        x.fillStyle = '#2c3542'; x.fillRect(px0 + 3.5, py0 + i * 2.8, 8, 2);
        x.fillStyle = i % 2 ? '#8fe0a0' : '#f08a8a'; x.fillRect(px0 + 10, py0 + 0.5 + i * 2.8, 1.2, 1.2);
      }
    } else if (dept === 'resell') {                           // parcel stack + scale
      x.fillStyle = '#c89b55'; x.fillRect(px0, py0 + 4, 7, 6); x.fillRect(px0 + 1.5, py0 - 1, 6, 5);
      x.fillStyle = 'rgba(0,0,0,.35)'; x.fillRect(px0 + 3, py0 + 4, 1, 6); x.fillRect(px0 + 4, py0 - 1, 1, 5);
      x.fillStyle = '#8a919f'; x.fillRect(px0 + 10, py0 + 8, 8, 2); x.fillRect(px0 + 13, py0 + 5, 2, 3);
      x.fillStyle = '#39414f'; x.fillRect(px0 + 10.5, py0 + 3, 7, 2.5);
    } else if (dept === 'trends') {                           // chart wall
      x.fillStyle = '#0e1626'; x.fillRect(px0 - 1, py0 - 2, 17, 11);
      x.strokeStyle = '#3f7fb0'; x.lineWidth = 1;
      x.beginPath(); x.moveTo(px0 + 1, py0 + 6); x.lineTo(px0 + 5, py0 + 3); x.lineTo(px0 + 9, py0 + 5); x.lineTo(px0 + 14, py0); x.stroke();
      x.strokeStyle = '#6ee7a8'; x.beginPath(); x.moveTo(px0 + 1, py0 + 8); x.lineTo(px0 + 6, py0 + 6.5); x.lineTo(px0 + 14, py0 + 7.5); x.stroke();
    }
  }

  // ══ HQ PROGRESSION STAGES (era snapshots) ══════════════════════════════════
  // The active stage (GET /api/world/hq/stages, applied by tab-world before
  // build()) patches the HQ descriptor: the Iron/Steel Age turns it into a
  // multi-section industrial COMPOUND (office wing + warehouse & shipping +
  // utilities + open loading yard — explicitly NOT one rectangle), while the
  // founding stage restores the original single-rect look. Sections are
  // bbox-local rects; b.doors are the carved wall openings (entrances + the
  // links BETWEEN sections). All of it is view state — exportLayout strips it.
  let _hqStage = null;
  function setHqStage(stage) { _hqStage = stage || null; }
  function _applyHqStageToBuildings() {
    const b = buildings.find(x => x.kind === 'hq'); if (!b) return;
    const st = _hqStage, lay = st && st.layout;
    if (!(lay && lay.kind === 'compound' && Array.isArray(lay.sections) && lay.sections.length)) {
      if (b._baseGeom) Object.assign(b, b._baseGeom);        // earlier-era view → original geometry
      delete b.sections; delete b.doors; delete b.stageKey; delete b.stageName; delete b._baseGeom;
      return;
    }
    if (!b._baseGeom) b._baseGeom = { c: b.c, r: b.r, w: b.w, h: b.h, door: b.door, label: b.label };
    const cx = b._baseGeom.c + b._baseGeom.w / 2, cy = b._baseGeom.r + b._baseGeom.h / 2;
    b.w = lay.w; b.h = lay.h;
    b.c = Math.max(1, Math.min(COLS - lay.w - 1, Math.round(cx - lay.w / 2)));
    b.r = Math.max(1, Math.min(ROWS - lay.h - 1, Math.round(cy - lay.h / 2) + 1)); // +1 clears the plaza fountain
    b.door = lay.door || 'S';
    b.sections = lay.sections.map(s => ({ ...s }));
    b.doors = (lay.doors || []).map(d => ({ ...d }));
    b.stageKey = st.key; b.stageName = st.name;
  }
  function applyHqStage(stage) {          // live stage switch (era viewer / advance)
    if (stage !== undefined) _hqStage = stage || null;
    _applyHqStageToBuildings();
    rasterize();
  }

  // stamp the compound footprint: a wall ring PER SECTION, floors inside, the
  // open yard as walkable paving, then carve every stage door to FLOOR (both the
  // outside entrances and the section-to-section links) so A* paths through.
  function _stampCompound(b) {
    for (const s of b.sections) {
      const c0 = b.c + s.lc, r0 = b.r + s.lr;
      for (let r = r0; r < r0 + s.h; r++) for (let c = c0; c < c0 + s.w; c++) {
        if (!inb(c, r)) continue;
        if (s.open) { grid[r][c] = T.PLAZA; continue; }      // paved open-air yard (walkable)
        const edge = r === r0 || r === r0 + s.h - 1 || c === c0 || c === c0 + s.w - 1;
        grid[r][c] = edge ? T.WALL : T.FLOOR;
      }
    }
    for (const d of (b.doors || [])) { const c = b.c + d.lc, r = b.r + d.lr; if (inb(c, r)) grid[r][c] = T.FLOOR; }
    if (b.interior) for (const it of b.interior) {           // Layer-3 hand-placed doors still open walls
      if (it.kind !== 'door') continue;
      const ic = b.c + it.lc, ir = b.r + it.lr;
      if (inb(ic, ir)) grid[ir][ic] = T.FLOOR;
    }
  }

  // split an edge pixel-span [a0,a1) into segments around door-gap spans
  function _spanSegs(a0, a1, gaps) {
    gaps = gaps.filter(g => g[1] > a0 && g[0] < a1).sort((p, q) => p[0] - q[0]);
    const segs = []; let cur = a0;
    for (const [g0, g1] of gaps) { if (g0 > cur) segs.push([cur, g0]); cur = Math.max(cur, g1); }
    if (cur < a1) segs.push([cur, a1]);
    return segs;
  }
  function _sectionShellRects(b, s) {
    const x0 = (b.c + s.lc) * TILE, y0 = (b.r + s.lr) * TILE, w = s.w * TILE, h = s.h * TILE, wp = WALL_PX;
    const doors = (b.doors || []).map(d => ({ c: b.c + d.lc, r: b.r + d.lr }));
    const rowGaps = rr => doors.filter(d => d.r === rr).map(d => [d.c * TILE, (d.c + 1) * TILE]);
    const colGaps = cc => doors.filter(d => d.c === cc).map(d => [d.r * TILE, (d.r + 1) * TILE]);
    const rects = [];
    for (const [g0, g1] of _spanSegs(x0, x0 + w, rowGaps(b.r + s.lr)))           rects.push({ x: g0, y: y0, w: g1 - g0, h: wp, edge: 'T' });
    for (const [g0, g1] of _spanSegs(x0, x0 + w, rowGaps(b.r + s.lr + s.h - 1))) rects.push({ x: g0, y: y0 + h - wp, w: g1 - g0, h: wp, edge: 'B' });
    for (const [g0, g1] of _spanSegs(y0, y0 + h, colGaps(b.c + s.lc)))           rects.push({ x: x0, y: g0, w: wp, h: g1 - g0, edge: 'L' });
    for (const [g0, g1] of _spanSegs(y0, y0 + h, colGaps(b.c + s.lc + s.w - 1))) rects.push({ x: x0 + w - wp, y: g0, w: wp, h: g1 - g0, edge: 'R' });
    return rects;
  }
  // Iron/Steel-Age wall shells: one themed band PER SECTION — riveted steel for
  // the warehouse/utilities, a modern glass strip on the office wing (the
  // industrial-plus-modern mix), with door gaps matching the carved openings.
  function _paintShellCompound(x, b) {
    for (const s of b.sections) {
      if (s.open) {                                          // yard: painted curb, no wall ring
        const sx = (b.c + s.lc) * TILE, sy = (b.r + s.lr) * TILE;
        x.strokeStyle = 'rgba(216,177,60,.55)'; x.lineWidth = 2;
        x.setLineDash([6, 5]); x.strokeRect(sx + 2, sy + 2, s.w * TILE - 4, s.h * TILE - 4); x.setLineDash([]);
        continue;
      }
      const theme = s.theme || '#8f98a4';
      const wall = _shade(theme, -0.10), lite = _shade(theme, 0.40), dark = _shade(theme, -0.34), key = _shade(theme, -0.62);
      const st = { accent: s.accent || 'rivet', neon: '#3af0d8' };
      for (const sh of _sectionShellRects(b, s)) {
        if (sh.w <= 0 || sh.h <= 0) continue;
        x.fillStyle = wall; x.fillRect(sh.x, sh.y, sh.w, sh.h);
        if (sh.edge === 'T') { x.fillStyle = lite; x.fillRect(sh.x, sh.y + 1, sh.w, 2); x.fillStyle = dark; x.fillRect(sh.x, sh.y + sh.h - 1, sh.w, 1); x.fillStyle = key; x.fillRect(sh.x, sh.y, sh.w, 1); }
        else if (sh.edge === 'B') { x.fillStyle = lite; x.fillRect(sh.x, sh.y, sh.w, 1); x.fillStyle = dark; x.fillRect(sh.x, sh.y + sh.h - 3, sh.w, 2); x.fillStyle = key; x.fillRect(sh.x, sh.y + sh.h - 1, sh.w, 1); }
        else if (sh.edge === 'L') { x.fillStyle = lite; x.fillRect(sh.x + 1, sh.y, 2, sh.h); x.fillStyle = dark; x.fillRect(sh.x + sh.w - 1, sh.y, 1, sh.h); x.fillStyle = key; x.fillRect(sh.x, sh.y, 1, sh.h); }
        else { x.fillStyle = lite; x.fillRect(sh.x, sh.y, 1, sh.h); x.fillStyle = dark; x.fillRect(sh.x + sh.w - 3, sh.y, 2, sh.h); x.fillStyle = key; x.fillRect(sh.x + sh.w - 1, sh.y, 1, sh.h); }
        _eraAccent(x, sh, st);                               // rivets / glass band on the wall face
      }
    }
  }

  // ── the compound's interiors, per section (baked like every building) ──
  const _SECTION_TINT = { office: 'rgba(120,150,190,.14)', warehouse: 'rgba(140,146,158,.20)',
                          utilities: 'rgba(150,110,70,.16)', yard: 'rgba(90,95,105,.22)' };
  function _hqCompound(x, b) {
    if (_structPass) hqRooms = [];
    for (const s of b.sections) {
      const sx = (b.c + s.lc) * TILE, sy = (b.r + s.lr) * TILE, sw = s.w * TILE, sh = s.h * TILE;
      if (s.open) { _yardDetail(x, sx, sy, sw, sh); _sectionLabel(x, s, sx, sy); continue; }
      const ix0 = sx + TILE, iy0 = sy + TILE, iw = sw - 2 * TILE, ih = sh - 2 * TILE;
      const useFloor = _floorImg && _floorImg.complete && _floorImg.naturalWidth && iw > 0 && ih > 0;
      if (useFloor) x.drawImage(_floorImg, ix0, iy0, iw, ih);
      const tint = _SECTION_TINT[s.key]; if (tint) { x.fillStyle = tint; x.fillRect(ix0, iy0, iw, ih); }
      if (s.key === 'office') { const g = _deptRoomBands(x, ix0, iy0, iw, ih, s.depts || []); _reception(x, ix0, g.hallY); }
      else if (s.key === 'warehouse') _warehouseWing(x, s, ix0, iy0, iw, ih);
      else if (s.key === 'utilities') _utilitiesWing(x, ix0, iy0, iw, ih);
      x.strokeStyle = 'rgba(0,0,0,.18)'; x.lineWidth = 3; x.strokeRect(ix0 + 1.5, iy0 + 1.5, iw - 3, ih - 3);  // inner AO
      _sectionLabel(x, s, sx, sy);
    }
  }
  // baked per-section nameplate so each wing reads at a glance
  function _sectionLabel(x, s, sx, sy) {
    const t = s.label || s.key;
    x.font = 'bold 7px sans-serif'; x.textAlign = 'left'; x.textBaseline = 'alphabetic';
    const tw = x.measureText(t).width;
    x.fillStyle = 'rgba(10,14,22,.78)'; x.fillRect(sx + 3, sy + 2, tw + 8, 10);
    x.fillStyle = _hex(s.theme || '#8f98a4', 1); x.fillRect(sx + 3, sy + 2, 2, 10);
    x.fillStyle = '#e6eefc'; x.fillText(t, sx + 8, sy + 10);
  }
  // Warehouse & Shipping: production bays (the five GPU studios) along the top,
  // and a real shipping floor below — spine conveyor toward the south dock,
  // steel racking, staged pallets, painted floor lanes. Bays register in
  // hqRooms so agents, WF machine lines, lights and overlays keep working.
  function _warehouseWing(x, s, ix0, iy0, iw, ih) {
    const depts = s.depts || [];
    const bayH = Math.round(ih * 0.52), bw = iw / Math.max(1, depts.length);
    depts.forEach((dept, i) => {
      const zx = ix0 + i * bw, zy = iy0, zw = bw, zh = bayH, cc = DEPT_TINT[dept] || '#8ab';
      if (_structPass) {
        hqRooms.push({ dept, x: zx + zw / 2, y: zy + zh / 2, x0: zx, y0: zy, w: zw, h: zh, tint: cc, door: 'bottom' });
        locations['desk:' + dept] = { col: Math.round(zx / TILE + zw / TILE / 2 - 0.5), row: Math.round(zy / TILE + zh / TILE / 2 - 0.5) };
      }
      x.fillStyle = _hex(cc, 0.15); x.fillRect(zx + 1, zy + 1, zw - 2, zh - 2);
      x.fillStyle = 'rgba(120,126,138,.30)'; x.fillRect(zx + 2, zy + zh * 0.52, zw - 4, zh * 0.30);  // concrete work slab
      const deskY = zy + zh * 0.24;                                    // bay office corner
      x.fillStyle = '#5d4328'; x.fillRect(zx + 6, deskY, 16, 6);
      x.fillStyle = '#1c2530'; x.fillRect(zx + 9, deskY - 5, 9, 5);
      x.fillStyle = '#7fd0ff'; x.fillRect(zx + 10, deskY - 4, 7, 3);
      _deptSignature(x, dept, zx, zy, zw, zh, deskY, cc);
      _roomWalls(x, zx, zy, zw, zh, 'bottom');                         // dividers + a door onto the shipping floor
    });
    // ── the shipping floor ──
    const fy0 = iy0 + bayH, fh = ih - bayH;
    x.fillStyle = 'rgba(104,110,122,.35)'; x.fillRect(ix0, fy0, iw, fh);        // sealed concrete
    x.strokeStyle = 'rgba(0,0,0,.15)'; x.lineWidth = 1;
    for (let gx = ix0 + 26; gx < ix0 + iw; gx += 26) { x.beginPath(); x.moveTo(gx, fy0 + 2); x.lineTo(gx, fy0 + fh - 2); x.stroke(); }  // slab joints
    const beltY = fy0 + fh * 0.45, bx0 = ix0 + iw * 0.05, bx1 = ix0 + iw * 0.95;
    x.fillStyle = '#20242c'; x.fillRect(bx0, beltY, bx1 - bx0, 8);              // spine conveyor
    x.fillStyle = 'rgba(160,170,185,.5)'; for (let rx = bx0 + 3; rx < bx1 - 2; rx += 6) x.fillRect(rx, beltY + 1, 1, 6);
    for (let rx = bx0; rx < bx1 - 3; rx += 9) {                                  // hazard striping
      x.fillStyle = '#d8b13c'; x.fillRect(rx, beltY + 10, 4.5, 2);
      x.fillStyle = '#23262e'; x.fillRect(rx + 4.5, beltY + 10, 4.5, 2);
    }
    x.strokeStyle = 'rgba(216,177,60,.5)'; x.lineWidth = 1.4; x.setLineDash([5, 4]);  // floor lanes to the docks
    x.beginPath(); x.moveTo(ix0 + iw * 0.55, beltY + 12); x.lineTo(ix0 + iw * 0.55, fy0 + fh); x.stroke();
    x.beginPath(); x.moveTo(bx1, beltY + 4); x.lineTo(ix0 + iw, beltY + 4); x.stroke();
    x.setLineDash([]);
    for (let k = 0; k < 3; k++) {                                                // pallets staged at the dock
      const px = ix0 + iw * (0.62 + k * 0.11), py = fy0 + fh - 14;
      x.fillStyle = '#8a6a3f'; x.fillRect(px, py, 11, 11);
      x.fillStyle = '#c89b55'; x.fillRect(px + 1, py + 1, 9, 4); x.fillRect(px + 1, py + 6, 9, 4);
      x.fillStyle = 'rgba(0,0,0,.3)'; x.fillRect(px + 1, py + 5, 9, 1);
    }
    for (let k = 0; k < 2; k++) {                                                // steel racking, west wall
      const rx = ix0 + 4, ry = fy0 + 6 + k * 16;
      x.fillStyle = '#39414f'; x.fillRect(rx, ry, 22, 4); x.fillRect(rx, ry + 7, 22, 4);
      x.fillStyle = '#4a505c'; x.fillRect(rx, ry, 2, 11); x.fillRect(rx + 20, ry, 2, 11);
      for (let i2 = 0; i2 < 4; i2++) { x.fillStyle = _hex(['#c07a5a', '#7ac0a0', '#d8c090', '#8fb3ff'][i2], .9); x.fillRect(rx + 3 + i2 * 5, ry + 1, 3.5, 2.5); }
    }
  }
  // Utilities: boiler + furnace glow + generator + pipe runs + coal + water tank
  // — the compound's beating iron heart.
  function _utilitiesWing(x, ix0, iy0, iw, ih) {
    x.fillStyle = 'rgba(58,52,46,.35)'; x.fillRect(ix0, iy0, iw, ih);           // soot-dark plate floor
    x.strokeStyle = 'rgba(0,0,0,.2)'; x.lineWidth = 1;
    for (let gy = iy0 + 8; gy < iy0 + ih; gy += 8) { x.beginPath(); x.moveTo(ix0, gy); x.lineTo(ix0 + iw, gy); x.stroke(); }
    const bx = ix0 + iw * 0.28, by = iy0 + ih * 0.45;                            // riveted boiler
    x.fillStyle = '#6d5f52'; x.beginPath(); x.ellipse(bx, by, 13, 9, 0, 0, 6.283); x.fill();
    x.fillStyle = '#8a7a6a'; x.beginPath(); x.ellipse(bx - 3, by - 3, 6, 4, 0, 0, 6.283); x.fill();
    x.fillStyle = 'rgba(232,238,244,.5)'; for (let a = 0; a < 6; a++) x.fillRect(bx - 10 + a * 4, by + 6, 1.2, 1.2);   // rivet row
    x.fillStyle = '#241d16'; x.fillRect(bx - 4, by - 1, 8, 6);                  // firebox
    x.fillStyle = '#f0a03c'; x.fillRect(bx - 3, by + 1, 6, 3);                  // fire glow
    const gx = ix0 + iw * 0.72, gy2 = iy0 + ih * 0.40;                           // generator block
    x.fillStyle = '#39414f'; x.fillRect(gx - 8, gy2 - 6, 16, 12);
    x.fillStyle = '#2c313c'; x.fillRect(gx - 8, gy2 - 6, 16, 3);
    x.fillStyle = '#7fd0ff'; x.fillRect(gx - 5, gy2 - 2, 3, 3); x.fillRect(gx + 2, gy2 - 2, 3, 3);   // dials
    x.strokeStyle = '#6a7280'; x.lineWidth = 2.4;                                // pipe run, top wall
    x.beginPath(); x.moveTo(ix0 + 2, iy0 + 4); x.lineTo(ix0 + iw - 2, iy0 + 4); x.stroke();
    x.strokeStyle = '#4a505c'; x.lineWidth = 1;
    for (let px2 = ix0 + 8; px2 < ix0 + iw - 4; px2 += 12) { x.beginPath(); x.moveTo(px2, iy0 + 2); x.lineTo(px2, iy0 + 7); x.stroke(); }
    x.fillStyle = '#1c1f26'; x.beginPath(); x.ellipse(ix0 + iw * 0.15, iy0 + ih - 7, 9, 4.5, 0, 0, 6.283); x.fill();   // coal pile
    x.fillStyle = '#31353f'; x.beginPath(); x.ellipse(ix0 + iw * 0.13, iy0 + ih - 8.5, 5, 2.6, 0, 0, 6.283); x.fill();
    x.fillStyle = '#5b6c7c'; x.beginPath(); x.arc(ix0 + iw - 12, iy0 + ih - 10, 6.5, 0, 6.283); x.fill();             // water tank
    x.fillStyle = '#7d8f9f'; x.beginPath(); x.arc(ix0 + iw - 13.5, iy0 + ih - 11.5, 2.6, 0, 6.283); x.fill();
  }
  // open-air loading yard: crates, pallets, a fuel drum on the painted apron
  function _yardDetail(x, sx, sy, sw, sh2) {
    x.fillStyle = 'rgba(96,100,110,.30)'; x.fillRect(sx, sy, sw, sh2);          // work-worn paving wash
    for (let k = 0; k < 3; k++) {
      const px = sx + 5, py = sy + 8 + k * 22;
      x.fillStyle = '#8a6238'; x.fillRect(px, py, 12, 11);
      x.fillStyle = '#a9763f'; x.fillRect(px, py, 12, 2);
      x.strokeStyle = '#5a3d24'; x.lineWidth = 1; x.strokeRect(px, py, 12, 11);
    }
    x.fillStyle = '#8a3f3f'; x.beginPath(); x.arc(sx + sw - 12, sy + 14, 5, 0, 6.283); x.fill();   // fuel drum
    x.fillStyle = '#a95555'; x.beginPath(); x.arc(sx + sw - 13, sy + 12.5, 2, 0, 6.283); x.fill();
    x.fillStyle = '#8a6a3f'; x.fillRect(sx + sw - 20, sy + sh2 - 18, 11, 11);                       // pallet
    x.fillStyle = '#c89b55'; x.fillRect(sx + sw - 19, sy + sh2 - 17, 9, 4); x.fillRect(sx + sw - 19, sy + sh2 - 12, 9, 4);
  }

  const FLOOR_TINT = { shop: 'rgba(70,120,180,.16)', townhall: 'rgba(230,200,80,.14)', exec: 'rgba(230,110,120,.14)', leisure: 'rgba(230,160,70,.14)', church: 'rgba(180,150,235,.15)', library: 'rgba(90,180,130,.14)',
                       school: 'rgba(110,170,220,.14)', nsfw: 'rgba(90,70,95,.18)', research: 'rgba(129,140,248,.12)',
                       mail: 'rgba(230,150,190,.10)', homelab: 'rgba(100,170,230,.12)', pearl: 'rgba(120,220,180,.10)', assistant: 'rgba(180,150,230,.10)' };

  function _doorPx(b) {
    let dc, dr;
    if (b.door === 'N') { dc = b.c + (b.w / 2 | 0); dr = b.r; }
    else if (b.door === 'W') { dc = b.c; dr = b.r + (b.h / 2 | 0); }
    else if (b.door === 'E') { dc = b.c + b.w - 1; dr = b.r + (b.h / 2 | 0); }
    else { dc = b.c + (b.w / 2 | 0); dr = b.r + b.h - 1; }
    return { c: dc, r: dr, x: (dc + 0.5) * TILE, y: (dr + 1) * TILE, side: b.door || 'S' };
  }

  // ── themed thin per-building wall shell ──────────────────────────────────────
  // Muted stone/plaster wall tones per kind (fallback = house). A building may carry
  // its own b.theme (a hex colour) which round-trips through exportLayout; when absent
  // the tone derives from b.kind. The picker UI is a later task — support b.theme now.
  // Iron & Steel Age restyle: the non-HQ set drops the pastel-plaster "house" look for
  // the HQ compound's materials — riveted steel plate, kiln brick, soot iron, with a
  // glass band on the civic/office fronts. Tones match IRON_STEEL_LAYOUT's section
  // themes (#9aa3ad office / #77675a utilities / #7e8894 warehouse) so the whole town
  // reads as one industrial company. Keyed by LOC first (bar ≠ arcade ≠ café), then kind.
  const _WALL_PAL = { hq: '#8b909c', house: '#9a8b76', shop: '#9e5a42', leisure: '#8a7462',
                      townhall: '#8f98a4', exec: '#7e8894', church: '#8a7080', library: '#77675a',
                      research: '#7d86cf', school: '#9aa3ad', nsfw: '#5d5560',
                      mail: '#8a7284', homelab: '#5f7284', pearl: '#6a8478', assistant: '#7a7490' };
  const _WALL_LOC = { bar: '#8a5a48', arcade: '#5c5570', tv: '#5f7284', cafe: '#9e6a4a' };
  // per-building material texture (reuses the era-accent painter): rivet = steel plate,
  // brick = kiln brick, glass = office band, neon = signage trim, plank = timber homes
  const _WALL_ACCENT = { hq: 'rivet', house: 'plank', shop: 'brick', leisure: 'brick',
                         townhall: 'glass', exec: 'glass', church: 'brick', library: 'brick',
                         research: 'rivet', school: 'glass', nsfw: 'plank',
                         mail: 'rivet', homelab: 'rivet', pearl: 'rivet', assistant: 'panel',
                         bar: 'brick', arcade: 'neon', tv: 'rivet', cafe: 'brick' };
  const _WALL_NEON = { arcade: '#a26cf0', nsfw: '#f06aa8' };
  function _themeFor(b) {
    if (b.loc === 'nsfw' && _nsfwOn()) return '#7a4460';               // open for business: plum neon-lit front
    return (b.loc && _WALL_LOC[b.loc]) || _WALL_PAL[b.kind] || _WALL_PAL.house;
  }
  function _accentFor(b) {
    const k = (b.loc === 'nsfw') ? (_nsfwOn() ? 'neon' : 'plank') : null;   // boarded-up timber until the gate opens
    const acc = k || _WALL_ACCENT[b.loc] || _WALL_ACCENT[b.kind];
    return acc ? { accent: acc, neon: _WALL_NEON[b.loc] || '#3af0d8' } : null;
  }
  // lighten (f>0) / darken (f<0) a #rrggbb toward white / black
  function _shade(hex, f) {
    const n = parseInt((hex || '#888').slice(1), 16); let R = (n >> 16) & 255, G = (n >> 8) & 255, B = n & 255;
    if (f < 0) { const k = 1 + f; R *= k; G *= k; B *= k; } else { R += (255 - R) * f; G += (255 - G) * f; B += (255 - B) * f; }
    return `rgb(${R | 0},${G | 0},${B | 0})`;
  }
  // Geometry of the themed wall band: the outer WALL_PX of the footprint edge, with a
  // gap at the door tile (aligned with the T.FLOOR opening _stamp punches). Each rect is
  // tagged with its edge (T/B/L/R) so the painter can put the crisp keyline on the side
  // that faces OUT (toward terrain) vs IN (toward the floor).
  function _shellRects(b) {
    const bx = b.c * TILE, by = b.r * TILE, bw = b.w * TILE, bh = b.h * TILE, wp = WALL_PX;
    const d = _doorPx(b);                                   // door tile (same math as _stamp)
    const dx0 = d.c * TILE, dx1 = (d.c + 1) * TILE;         // door opening pixel span (horizontal edges)
    const dy0 = d.r * TILE, dy1 = (d.r + 1) * TILE;         // door opening pixel span (vertical edges)
    const rects = [];
    // TOP edge band (skip door span when door is on the N side)
    if (d.side === 'N') { rects.push({ x: bx, y: by, w: dx0 - bx, h: wp, edge: 'T' }, { x: dx1, y: by, w: (bx + bw) - dx1, h: wp, edge: 'T' }); }
    else rects.push({ x: bx, y: by, w: bw, h: wp, edge: 'T' });
    // BOTTOM edge band (skip door span when door is on the S side)
    if (d.side === 'S') { rects.push({ x: bx, y: by + bh - wp, w: dx0 - bx, h: wp, edge: 'B' }, { x: dx1, y: by + bh - wp, w: (bx + bw) - dx1, h: wp, edge: 'B' }); }
    else rects.push({ x: bx, y: by + bh - wp, w: bw, h: wp, edge: 'B' });
    // LEFT edge band (skip door span when door is on the W side)
    if (d.side === 'W') { rects.push({ x: bx, y: by, w: wp, h: dy0 - by, edge: 'L' }, { x: bx, y: dy1, w: wp, h: (by + bh) - dy1, edge: 'L' }); }
    else rects.push({ x: bx, y: by, w: wp, h: bh, edge: 'L' });
    // RIGHT edge band (skip door span when door is on the E side)
    if (d.side === 'E') { rects.push({ x: bx + bw - wp, y: by, w: wp, h: dy0 - by, edge: 'R' }, { x: bx + bw - wp, y: dy1, w: wp, h: (by + bh) - dy1, edge: 'R' }); }
    else rects.push({ x: bx + bw - wp, y: by, w: wp, h: bh, edge: 'R' });
    return rects;
  }
  // ── CIVILIZATION ERAS: buildings visibly AGE 🪵→🧱→⚙️→🤠→🏙️→🚀→🌙 ──────────────
  // Driven by the backend, polled into the BARE lexical global `_worldState` every 3s:
  //   _worldState.eras = { ladder, emoji, byLoc:{loc:lvl}, town:lvl }
  // Per building the era level = eras.byLoc[b.loc] ?? eras.town ?? 0. A SINGLE ERA_STYLE
  // table gives each era its wall / trim / roof palette + a material-accent flag; it is
  // threaded into the baked wall shell (_paintShell, below) AND the pseudo-3D roofs
  // (world-render-buildings.js, via WM.eraStyle). Fully ADDITIVE: no eras data → level -1
  // → today's exact look. Reference _worldState bare with a typeof guard (it is a `let` in
  // tab-world.js; window._worldState would be undefined — this bit us before).
  const ERA_STYLE = [
    { key: 'wood',       wall: '#8a5a34', trim: '#5e3d22', face: '#a6764a', roof: ['#8a5a34', '#623d22', '#a6764a'], accent: 'plank'   },
    { key: 'brick',      wall: '#9e4a34', trim: '#6f2f22', face: '#b5745a', roof: ['#9e4a34', '#6f2f22', '#c06a4a'], accent: 'brick'   },
    { key: 'metal',      wall: '#8f98a4', trim: '#565d68', face: '#aab2bc', roof: ['#8f98a4', '#565d68', '#b3bcc7'], accent: 'rivet'   },
    { key: 'western',    wall: '#c2a066', trim: '#836230', face: '#d4b483', roof: ['#b5834a', '#7c5730', '#d4aa6a'], accent: 'western' },
    { key: 'modern',     wall: '#cbd0d6', trim: '#8a97a5', face: '#c2c8ce', roof: ['#aeb6c0', '#7c8794', '#d6dde4'], accent: 'glass'   },
    { key: 'futuristic', wall: '#2c313d', trim: '#161a22', face: '#3a4150', roof: ['#2c313d', '#141821', '#3f4656'], accent: 'neon', neon: '#3af0d8' },
    { key: 'moon',       wall: '#e7eaf0', trim: '#b9c0cc', face: '#eef1f6', roof: ['#dfe4ec', '#b7bfcb', '#f4f7fb'], accent: 'panel'   },
  ];
  function _erasObj() { return (typeof _worldState !== 'undefined' && _worldState) ? _worldState.eras : null; }
  function eraLevel(b) {                                    // -1 = no era data → render exactly as today
    const e = _erasObj(); if (!e) return -1;
    const lvl = (b && b.loc != null && e.byLoc && e.byLoc[b.loc] != null) ? e.byLoc[b.loc]
              : (e.town != null ? e.town : 0);
    return Math.max(0, Math.min(ERA_STYLE.length - 1, lvl | 0));
  }
  function eraStyle(b) { const l = eraLevel(b); return l < 0 ? null : ERA_STYLE[l]; }
  function eraEmoji(b) {                                    // tiny age glyph for the name pill
    const e = _erasObj(), st = eraStyle(b);
    return (e && st && e.emoji && e.emoji[st.key]) || '';
  }
  // Per-era material texture painted INSIDE a wall-band rect `s` (edge-tagged T/B/L/R).
  function _eraAccent(x, s, st) {
    const horiz = s.w >= s.h;                               // band runs horizontally (T/B) vs vertically (L/R)
    if (st.accent === 'brick') {                            // mortar grid
      x.fillStyle = 'rgba(238,232,224,.28)';
      if (horiz) { for (let gx = s.x + 5; gx < s.x + s.w; gx += 6) x.fillRect(gx, s.y, 1, s.h); x.fillRect(s.x, s.y + s.h / 2 - 0.5, s.w, 1); }
      else { for (let gy = s.y + 5; gy < s.y + s.h; gy += 6) x.fillRect(s.x, gy, s.w, 1); x.fillRect(s.x + s.w / 2 - 0.5, s.y, 1, s.h); }
    } else if (st.accent === 'plank' || st.accent === 'western') {   // wood plank seams along the band
      x.fillStyle = st.accent === 'western' ? 'rgba(92,62,30,.30)' : 'rgba(60,40,22,.34)';
      if (horiz) for (let gy = s.y + 3; gy < s.y + s.h; gy += 4) x.fillRect(s.x, gy, s.w, 0.8);
      else for (let gx = s.x + 3; gx < s.x + s.w; gx += 4) x.fillRect(gx, s.y, 0.8, s.h);
      if (st.accent === 'western') {                        // false-front vertical board posts
        x.fillStyle = 'rgba(120,86,44,.45)';
        if (horiz) for (let gx = s.x + 8; gx < s.x + s.w; gx += 9) x.fillRect(gx, s.y, 1, s.h);
        else for (let gy = s.y + 8; gy < s.y + s.h; gy += 9) x.fillRect(s.x, gy, s.w, 1);
      }
    } else if (st.accent === 'rivet') {                     // steel rivet dots
      if (horiz) for (let gx = s.x + 4; gx < s.x + s.w - 1; gx += 7) { x.fillStyle = 'rgba(232,238,244,.55)'; x.fillRect(gx, s.y + Math.max(1, s.h * 0.3), 1.4, 1.4); x.fillStyle = 'rgba(18,24,32,.4)'; x.fillRect(gx + 0.5, s.y + Math.max(1, s.h * 0.3) + 0.7, 0.8, 0.8); }
      else for (let gy = s.y + 4; gy < s.y + s.h - 1; gy += 7) { x.fillStyle = 'rgba(232,238,244,.55)'; x.fillRect(s.x + Math.max(1, s.w * 0.3), gy, 1.4, 1.4); x.fillStyle = 'rgba(18,24,32,.4)'; x.fillRect(s.x + Math.max(1, s.w * 0.3) + 0.5, gy + 0.7, 0.8, 0.8); }
    } else if (st.accent === 'glass') {                     // blue-grey glass band
      x.fillStyle = 'rgba(150,190,230,.34)';
      if (horiz) x.fillRect(s.x, s.y + Math.max(1, s.h * 0.35), s.w, Math.max(1, s.h * 0.3));
      else x.fillRect(s.x + Math.max(1, s.w * 0.35), s.y, Math.max(1, s.w * 0.3), s.h);
    } else if (st.accent === 'neon') {                      // neon trim just inside the outer keyline
      x.fillStyle = st.neon || '#3af0d8';
      if (s.edge === 'T') x.fillRect(s.x, s.y + s.h - 2, s.w, 1);
      else if (s.edge === 'B') x.fillRect(s.x, s.y + 1, s.w, 1);
      else if (s.edge === 'L') x.fillRect(s.x + s.w - 2, s.y, 1, s.h);
      else x.fillRect(s.x + 1, s.y, 1, s.h);
    } else if (st.accent === 'panel') {                     // pale moon-panel seams
      x.fillStyle = 'rgba(150,160,180,.35)';
      if (horiz) for (let gx = s.x + 7; gx < s.x + s.w; gx += 8) x.fillRect(gx, s.y, 0.8, s.h);
      else for (let gy = s.y + 7; gy < s.y + s.h; gy += 8) x.fillRect(s.x, gy, s.w, 0.8);
    }
  }
  // Re-bake trigger: building shells are BAKED into the overview/chunks by _paintShell, so an
  // era change is invisible until we re-bake. Detect a changed era signature each frame (cheap
  // string over ~50 buildings) and call the existing _bake() path — mirrors the terrain/floor
  // image swap. Skips the pure startup null→"" (no era data) transition to avoid a wasted bake.
  let _lastEraSig = null;
  function _eraSig() {
    // the 😈 NSFW-store gate is part of the signature too: its boarded↔open look is
    // BAKED (shell + interior), so a gate flip must re-bake exactly like an era change.
    let s = 'nx' + (_nsfwOn() ? 1 : 0);
    const e = _erasObj(); if (!e) return s;
    s += '|t' + (e.town ?? 0);
    if (e.byLoc) for (const b of buildings) if (b.loc != null && e.byLoc[b.loc] != null) s += '|' + b.loc + ':' + (e.byLoc[b.loc] | 0);
    return s;
  }
  function _eraRebakeCheck() {
    const sig = _eraSig();
    if (_lastEraSig === null) { _lastEraSig = sig; return; }   // first frame = baseline (the build() bake already used it)
    if (sig === _lastEraSig) return;
    _lastEraSig = sig;
    if (_overview) _bake();                                    // era / gate appeared, changed or reverted
  }

  // Paint the themed wall band so it reads UNMISTAKABLY as a wall standing proud of the
  // floor/terrain: a solid wall face (deeper than the floor tone) + a crisp dark keyline
  // on the OUTER pixel (separates the building from the ground) + a lit highlight just
  // inside it + a shadow on the INNER pixel (where the wall meets the interior floor).
  function _paintShell(x, b) {
    if (b.sections && b.sections.length) return _paintShellCompound(x, b);   // staged compound HQ: per-section themed shells
    const est = eraStyle(b);                                // civilization era (null → today's per-kind theme)
    const theme = est ? est.wall : (b.theme || _themeFor(b));
    const acc = est || _accentFor(b);                       // era accent wins; else the Iron/Steel per-kind material
    const wall = _shade(theme, -0.10);                      // wall face — a touch deeper than the theme so it's never floor-coloured
    const lite = _shade(theme, 0.40), dark = _shade(theme, -0.34);
    const key = _shade(theme, -0.62);                       // crisp outer keyline against the terrain
    for (const s of _shellRects(b)) {
      if (s.w <= 0 || s.h <= 0) continue;
      x.fillStyle = wall; x.fillRect(s.x, s.y, s.w, s.h);
      if (s.edge === 'T') {                                 // outer=top, inner=bottom
        x.fillStyle = lite; x.fillRect(s.x, s.y + 1, s.w, 2);
        x.fillStyle = dark; x.fillRect(s.x, s.y + s.h - 1, s.w, 1);
        x.fillStyle = key; x.fillRect(s.x, s.y, s.w, 1);
      } else if (s.edge === 'B') {                          // outer=bottom, inner=top
        x.fillStyle = lite; x.fillRect(s.x, s.y, s.w, 1);
        x.fillStyle = dark; x.fillRect(s.x, s.y + s.h - 3, s.w, 2);
        x.fillStyle = key; x.fillRect(s.x, s.y + s.h - 1, s.w, 1);
      } else if (s.edge === 'L') {                          // outer=left, inner=right
        x.fillStyle = lite; x.fillRect(s.x + 1, s.y, 2, s.h);
        x.fillStyle = dark; x.fillRect(s.x + s.w - 1, s.y, 1, s.h);
        x.fillStyle = key; x.fillRect(s.x, s.y, 1, s.h);
      } else {                                              // R: outer=right, inner=left
        x.fillStyle = lite; x.fillRect(s.x, s.y, 1, s.h);
        x.fillStyle = dark; x.fillRect(s.x + s.w - 3, s.y, 2, s.h);
        x.fillStyle = key; x.fillRect(s.x + s.w - 1, s.y, 1, s.h);
      }
      if (acc) _eraAccent(x, s, acc);                        // material texture (era, or the Iron/Steel kind accent) on the wall face
    }
  }
  const _drawBuildingShell = _paintShell;                   // painted per-region into the overview + chunks by _paintRegion()
  // Per-frame legibility pass: as the roofs fade away (zoom in) the walls fade IN on top of
  // everything, so the wall footprint is always crisp regardless of terrain/floor/lighting.
  // alpha is driven by the render layer (1 - roofAlpha); no-op when the roofs are solid.
  function drawWallBands(x, alpha) {
    if (!(alpha > 0.02)) return;
    x.save(); x.globalAlpha = Math.min(1, alpha);
    for (const b of buildings) _paintShell(x, b);
    x.restore();
  }

  // ── Layer-3: per-tile INTERIOR edits (doors / windows / objects) drawn in the zoomed-in
  // reveal layer (called from the render loop with 1-roofAlpha, i.e. as the roofs fade), ON
  // TOP of the per-frame wall bands so a wall-edge door punches through the wall as a real
  // opening. Procedural furniture (_homeFurnish/_hqRooms) stays the default look; these ADD. ─
  function drawInterior(x, alpha) {
    if (!(alpha > 0.02)) return;
    x.save(); x.globalAlpha = Math.min(1, alpha);
    for (const b of buildings) {
      const items = b.interior; if (!items || !items.length) continue;
      for (const it of items) {
        const px = (b.c + it.lc) * TILE, py = (b.r + it.lr) * TILE;
        if (it.kind === 'door') _interiorDoor(x, px, py);
        else if (it.kind === 'window') _interiorWindow(x, px, py);
        else _interiorObject(x, px, py, it.kind, b);
      }
    }
    x.restore();
  }
  function _interiorDoor(x, px, py) {
    const T = TILE;
    x.fillStyle = '#7a5230'; x.fillRect(px + 1, py + 1, T - 2, T - 2);          // floor opening under the leaf
    x.fillStyle = '#5b3a22'; x.fillRect(px + 4, py + 3, T - 8, T - 4);          // door leaf
    x.fillStyle = '#734a2c'; x.fillRect(px + 4, py + 3, T - 8, 2);              // top rail highlight
    x.fillStyle = 'rgba(0,0,0,.3)'; x.fillRect(px + 4, py + T - 2, T - 8, 1);   // threshold shadow
    x.fillStyle = '#e8c14a'; x.fillRect(px + T - 7, py + (T / 2 | 0), 2, 2);    // knob
  }
  function _interiorWindow(x, px, py) {
    const T = TILE;
    x.fillStyle = '#26303f'; x.fillRect(px + 3, py + 5, T - 6, T - 10);                          // frame
    x.fillStyle = '#3a5170'; x.fillRect(px + 4, py + 6, T - 8, T - 12);                          // glass
    x.fillStyle = 'rgba(190,220,255,.55)'; x.fillRect(px + 5, py + 7, (T - 10) / 2 - 1, T - 14); // sky reflection
    x.fillStyle = '#3a2a1a'; x.fillRect(px + (T / 2 | 0) - 0.5, py + 5, 1, T - 10);              // mullion
    x.fillStyle = '#efe6d2'; x.fillRect(px + 2, py + T - 5, T - 4, 1.5);                         // sill
  }
  function _interiorObject(x, px, py, kind, b) {
    const T = TILE, cx = px + T / 2, cy = py + T / 2;
    x.fillStyle = 'rgba(0,0,0,.22)'; x.beginPath(); x.ellipse(cx, py + T - 3, T * 0.32, T * 0.13, 0, 0, 6.283); x.fill();
    if (kind === 'plant') {
      x.fillStyle = '#8a5a2b'; x.fillRect(cx - 3, cy + 2, 6, 5);                // pot
      x.fillStyle = '#a9763f'; x.fillRect(cx - 3, cy + 2, 6, 1.5);
      x.fillStyle = '#2f8542'; x.beginPath(); x.arc(cx, cy - 1, 5, 0, 6.283); x.fill();
      x.fillStyle = '#3ea355'; x.beginPath(); x.arc(cx - 2, cy - 3, 2.6, 0, 6.283); x.fill();
    } else if (kind === 'crate') {
      x.fillStyle = '#8a6238'; x.fillRect(cx - 6, cy - 5, 12, 11);             // crate body
      x.fillStyle = '#a9763f'; x.fillRect(cx - 6, cy - 5, 12, 2);             // lit top edge
      x.strokeStyle = '#5a3d24'; x.lineWidth = 1; x.strokeRect(cx - 6, cy - 5, 12, 11);
      x.beginPath(); x.moveTo(cx - 6, cy - 5); x.lineTo(cx + 6, cy + 6); x.moveTo(cx + 6, cy - 5); x.lineTo(cx - 6, cy + 6); x.stroke();  // banding
    } else {                                                                   // generic furniture — tinted to the building's colour
      x.fillStyle = '#5d4328'; x.fillRect(cx - 6, cy - 3, 12, 8);             // table / chest body
      x.fillStyle = _hex(b.color, 0.9); x.fillRect(cx - 6, cy - 3, 12, 2.5);  // coloured top
      x.fillStyle = 'rgba(255,255,255,.15)'; x.fillRect(cx - 6, cy - 3, 12, 1);
      x.fillStyle = 'rgba(0,0,0,.3)'; x.fillRect(cx - 5, cy + 4, 2, 2); x.fillRect(cx + 3, cy + 4, 2, 2);  // legs
    }
  }

  // per-building detail: floor tint, roof/awning trim, door, sign, interior furniture
  function _building(x, b) {
    if (b.kind === 'hq' && b.sections && b.sections.length) { _hqCompound(x, b); return; }   // staged compound HQ
    const bx = b.c * TILE, by = b.r * TILE, bw = b.w * TILE, bh = b.h * TILE;
    // Layer-2b: ONE shared generated interior-floor texture (warm planks/tiles) as
    // the base under every interior, then the per-kind FLOOR_TINT washed OVER it at
    // low alpha so buildings still read distinct. No floor image → the classic
    // per-kind tint fill only (exact prior behavior).
    const iw = bw - 2 * TILE, ih = bh - 2 * TILE;
    const useFloor = _floorImg && _floorImg.complete && _floorImg.naturalWidth && iw > 0 && ih > 0;
    if (useFloor) x.drawImage(_floorImg, bx + TILE, by + TILE, iw, ih);   // baked in _bake()
    const tint = FLOOR_TINT[b.kind]; if (tint) { x.fillStyle = tint; x.fillRect(bx + TILE, by + TILE, bw - 2 * TILE, bh - 2 * TILE); }
    if (b.kind === 'hq') _hqRooms(x, b);               // divide HQ into furnished department rooms
    // roof / awning trim in the building colour (makes each type distinct) — skip when
    // the Kenney wall ring is drawn (it would paint over the top wall tiles)
    if (!(window.WB && WB.ready)) {
      x.fillStyle = _hex(b.color, b.kind === 'hq' ? .9 : .8); x.fillRect(bx, by, bw, b.kind === 'hq' ? 6 : 4);
      x.fillStyle = 'rgba(255,255,255,.18)'; x.fillRect(bx, by, bw, 1);
      x.fillStyle = 'rgba(0,0,0,.3)'; x.fillRect(bx, by + (b.kind === 'hq' ? 6 : 4), bw, 1);
    }
    // door — panel sized/positioned off WALL_PX so it sits flush in the (thin) wall band on
    // the door's own side, instead of the old full-tile-thick rect that overhangs into the
    // interior floor. N/S bands run horizontal across the tile; W/E bands run vertical.
    const d = _doorPx(b), dx = d.c * TILE, dy = d.r * TILE, side = d.side;
    let px, py, pw, ph;
    if (side === 'N') { px = dx + 4; py = dy - 2; pw = TILE - 8; ph = WALL_PX + 4; }
    else if (side === 'W') { px = dx - 2; py = dy + 4; pw = WALL_PX + 4; ph = TILE - 8; }
    else if (side === 'E') { px = dx + TILE - WALL_PX - 2; py = dy + 4; pw = WALL_PX + 4; ph = TILE - 8; }
    else { px = dx + 4; py = dy + TILE - WALL_PX - 2; pw = TILE - 8; ph = WALL_PX + 4; }   // S (default)
    x.fillStyle = '#5b3a22'; x.fillRect(px, py, pw, ph);
    x.fillStyle = '#734a2c';
    if (side === 'W' || side === 'E') x.fillRect(px, py, 2, ph); else x.fillRect(px, py, pw, 2);
    x.fillStyle = '#e8c14a';                                                          // knob
    if (side === 'W') x.fillRect(px + pw - 4, dy + TILE / 2 - 1, 2, 2);
    else if (side === 'E') x.fillRect(px + 2, dy + TILE / 2 - 1, 2, 2);
    else x.fillRect(dx + TILE - 7, py + ph / 2 - 1, 2, 2);
    // hanging sign near the door for named/shop buildings
    if (b.label && b.kind !== 'hq' && b.kind !== 'house') {
      x.fillStyle = _hex(b.color, .95); x.fillRect(dx + 2, dy - 6, TILE - 4, 5);
      x.fillStyle = 'rgba(0,0,0,.35)'; x.fillRect(dx + 2, dy - 1, TILE - 4, 1);
    }
    if (b.kind === 'house' && !(window.WB && WB.ready)) _fence(x, b);   // wall ring replaces the yard fence
    // department banners hung on the top wall — restores the colour identity the
    // roof-trim used to give, in-theme (needs the extracted pennant sprites)
    const bn = _bannerFor(b);
    if (bn && window.WA && WA.hasSprite && WA.hasSprite(bn)) {
      const bh2 = TILE * 1.3, byb = by + TILE * 1.35;
      if (b.kind === 'hq') { WA.drawSprite(x, bn, bx + bw * 0.30, byb, bh2); WA.drawSprite(x, bn, bx + bw * 0.70, byb, bh2); }
      else if (b.label) WA.drawSprite(x, bn, bx + bw / 2, byb, bh2);
    }
    // interior furnishing: named venues get a REAL bespoke interior (bar counter,
    // pews, arcade cabinets, lecture hall…). Everything else keeps the classic
    // rug + barrel/crate look. Houses keep their full _homeFurnish below.
    if (b.kind !== 'hq' && b.w >= 5 && b.h >= 5 && !_venueFurnish(x, b, bx, by, bw, bh)) {
      const cxp = bx + bw / 2, cyp = by + bh / 2;
      x.fillStyle = _hex(b.color, .20); x.beginPath(); x.roundRect(cxp - TILE * 0.9, cyp - TILE * 0.55, TILE * 1.8, TILE * 1.1, 3); x.fill();
      x.strokeStyle = _hex(b.color, .5); x.lineWidth = 1; x.stroke();
      // real barrel + crate in the corners (fallback: procedural potted plant)
      if (window.WA && WA.hasSprite && WA.hasSprite('barrel')) {
        WA.drawSprite(x, 'barrel', bx + TILE + 4, by + bh - TILE - 2, TILE * 0.95);
        WA.drawSprite(x, (b.c + b.r) % 2 ? 'crate' : 'crate_produce', bx + bw - TILE - 4, by + bh - TILE - 2, TILE * 0.95);
      } else {
        const pxp = bx + TILE + 3, pyp = by + bh - TILE - 3;                             // plant, back corner
        x.fillStyle = '#6b4c2f'; x.fillRect(pxp - 2, pyp, 4, 4);
        x.fillStyle = '#2f8542'; x.beginPath(); x.arc(pxp, pyp - 1, 3.2, 0, 6.283); x.fill();
        x.fillStyle = '#3ea355'; x.beginPath(); x.arc(pxp - 1, pyp - 2, 1.8, 0, 6.283); x.fill();
      }
    }
    if (b.kind === 'house') _homeFurnish(x, b, bx, by, bw, bh);   // beds, table — lived-in homes
  }

  // ══ VENUE INTERIORS — the Iron/Steel-age build-out ═══════════════════════════
  // Every named venue is furnished like an HQ wing instead of the one-size rug:
  // pews + altar in the ✝️ church, a bar counter + kegs in 😈 Satan's bar, arcade
  // cabinet rows, the 🎓 University's lecture hall + knowledge-graph lab, the
  // council chamber, the boss's office, lab benches, shop shelves… All BAKED
  // (zero per-frame cost). Returns false for buildings with no bespoke interior
  // (houses / unknown kinds) so the caller keeps the classic look.
  function _venueFurnish(x, b, bx, by, bw, bh) {
    const fn = _VENUES[b.loc || b.kind];
    if (!fn) return false;
    const ix = bx + TILE, iy = by + TILE, iw = bw - 2 * TILE, ih = bh - 2 * TILE;
    if (iw < TILE * 2 || ih < TILE * 2) return false;
    fn(x, b, ix, iy, iw, ih);
    x.strokeStyle = 'rgba(0,0,0,.16)'; x.lineWidth = 3;                    // inner AO — the room reads enclosed
    x.strokeRect(ix + 1.5, iy + 1.5, iw - 3, ih - 3);
    return true;
  }
  // tiny shared pieces
  function _vChair(x, cx, cy, tone) { x.fillStyle = tone || '#39414f'; x.beginPath(); x.arc(cx, cy, 3, 0, 6.283); x.fill(); }
  function _vTable(x, cx, cy, r) {
    x.fillStyle = '#5a3d24'; x.beginPath(); x.arc(cx, cy, r + 1.5, 0, 6.283); x.fill();
    x.fillStyle = '#7a5230'; x.beginPath(); x.arc(cx, cy, r, 0, 6.283); x.fill();
  }
  function _vShelfRow(x, sx, sy, w, colors) {                              // stocked display shelf
    x.fillStyle = '#4a3b2a'; x.fillRect(sx, sy, w, 8);
    x.fillStyle = 'rgba(0,0,0,.3)'; x.fillRect(sx, sy + 3.5, w, 1);
    for (let i = 0; i * 5 < w - 4; i++) { x.fillStyle = colors[i % colors.length]; x.fillRect(sx + 2 + i * 5, sy + 1, 3.5, 2.5); x.fillRect(sx + 2 + i * 5, sy + 5, 3.5, 2.5); }
  }
  const _VENUES = {
    // ── 🍺 BAR — 😈 Satan's haunt: long counter, taps, kegs, bottle shelf, tables ──
    bar(x, b, ix, iy, iw, ih) {
      x.fillStyle = 'rgba(90,40,30,.16)'; x.fillRect(ix, iy, iw, ih);                       // warm tavern wash
      _vShelfRow(x, ix + 3, iy + 2, iw * 0.62, ['#c25b4e', '#e8c14a', '#8fc7a9', '#d97ac0']);  // bottle wall
      x.fillStyle = '#4a3120'; x.fillRect(ix + 3, iy + 12, iw * 0.62, 7);                   // the bar counter
      x.fillStyle = '#6e4a2b'; x.fillRect(ix + 3, iy + 12, iw * 0.62, 2);
      x.fillStyle = '#c9b488'; for (let i = 0; i < 3; i++) x.fillRect(ix + 8 + i * 12, iy + 11, 2, 3);   // taps
      for (let i = 0; i < Math.min(4, (iw * 0.62 / 12) | 0); i++) _vChair(x, ix + 9 + i * 12, iy + 24);  // stools
      for (let k = 0; k < 2; k++) {                                                          // kegs, bottom-left
        x.fillStyle = '#6e4a2b'; x.beginPath(); x.arc(ix + 8 + k * 12, iy + ih - 8, 5, 0, 6.283); x.fill();
        x.fillStyle = '#8a6238'; x.beginPath(); x.arc(ix + 8 + k * 12, iy + ih - 8, 3, 0, 6.283); x.fill();
        x.fillStyle = '#3a2a1a'; x.fillRect(ix + 4 + k * 12, iy + ih - 9, 8, 1);
      }
      _vTable(x, ix + iw - 14, iy + ih * 0.4, 5); _vChair(x, ix + iw - 22, iy + ih * 0.4); _vChair(x, ix + iw - 6, iy + ih * 0.4);
      _vTable(x, ix + iw - 14, iy + ih - 10, 5); _vChair(x, ix + iw - 22, iy + ih - 10);
      x.fillStyle = '#c0303a'; x.fillRect(ix + iw * 0.66, iy + 2, 10, 8);                    // 😈 house sign
      x.font = '7px sans-serif'; x.textAlign = 'left'; x.fillText('😈', ix + iw * 0.66 + 1, iy + 9);
    },
    // ── 🕹️ ARCADE — cabinet rows with glowing screens + prize counter ──
    arcade(x, b, ix, iy, iw, ih) {
      x.fillStyle = 'rgba(50,40,90,.22)'; x.fillRect(ix, iy, iw, ih);                        // dim neon-den wash
      const scr = ['#7fd0ff', '#8fe0a0', '#f0a860', '#d97ac0'];
      const n = Math.min(3, ((ih - 10) / 15) | 0);
      for (let s2 = 0; s2 < 2; s2++) for (let i = 0; i < n; i++) {                           // cabinets on both side walls
        const cxp = s2 ? ix + iw - 11 : ix + 3, cyp = iy + 4 + i * 15;
        x.fillStyle = '#232838'; x.fillRect(cxp, cyp, 8, 12);
        x.fillStyle = scr[(i + s2) % 4]; x.fillRect(cxp + 1.5, cyp + 2, 5, 4);               // glowing screen
        x.fillStyle = '#e8c14a'; x.fillRect(cxp + 2.5, cyp + 8, 1.5, 1.5); x.fillRect(cxp + 5, cyp + 8, 1.5, 1.5);  // buttons
      }
      x.fillStyle = 'rgba(162,108,240,.30)'; x.fillRect(ix + 13, iy + 4, iw - 26, 2);        // neon ceiling strip
      x.fillStyle = '#5d4328'; x.fillRect(ix + iw / 2 - 11, iy + ih - 9, 22, 6);             // prize counter
      x.fillStyle = '#d97ac0'; x.fillRect(ix + iw / 2 - 8, iy + ih - 8, 3, 3);
      x.fillStyle = '#8fe0a0'; x.fillRect(ix + iw / 2 + 2, iy + ih - 8, 3, 3);
    },
    // ── ☕ CAFÉ — espresso counter + pastry case + round tables ──
    cafe(x, b, ix, iy, iw, ih) {
      x.fillStyle = 'rgba(180,130,80,.14)'; x.fillRect(ix, iy, iw, ih);
      x.fillStyle = '#8b8f98'; x.fillRect(ix + 3, iy + 3, iw * 0.5, 7);                      // counter
      x.fillStyle = '#2a2f3a'; x.fillRect(ix + 5, iy + 1, 7, 5);                             // espresso machine
      x.fillStyle = '#e8c14a'; x.fillRect(ix + 6, iy + 2, 2, 2);
      x.fillStyle = '#bfe3ff'; x.fillRect(ix + 15, iy + 4, 9, 4);                            // pastry case
      x.fillStyle = '#d8a050'; x.fillRect(ix + 16, iy + 5, 2, 2); x.fillRect(ix + 19, iy + 5, 2, 2);
      for (let i = 0; i < 3; i++) {
        const tx = ix + 10 + (i % 2) * (iw - 24), ty = iy + ih * 0.5 + (i > 1 ? 12 : 0) + (i % 2) * 4;
        _vTable(x, tx, ty, 4.5); _vChair(x, tx - 7, ty); _vChair(x, tx + 7, ty);
      }
    },
    // ── 📺 LOUNGE — big screen + sofa rows ──
    tv(x, b, ix, iy, iw, ih) {
      x.fillStyle = 'rgba(40,60,90,.16)'; x.fillRect(ix, iy, iw, ih);
      x.fillStyle = '#10141c'; x.fillRect(ix + iw * 0.2, iy + 2, iw * 0.6, 9);               // the big screen
      x.fillStyle = '#3f7fb0'; x.fillRect(ix + iw * 0.2 + 1, iy + 3, iw * 0.6 - 2, 7);
      x.fillStyle = 'rgba(255,255,255,.25)'; x.fillRect(ix + iw * 0.22, iy + 4, iw * 0.18, 2);
      for (let r2 = 0; r2 < 2; r2++) {                                                       // sofas facing it
        const sy = iy + ih * 0.45 + r2 * 12;
        x.fillStyle = '#7a4a3f'; x.fillRect(ix + iw * 0.18, sy, iw * 0.64, 8);
        x.fillStyle = '#9a5f4f'; x.fillRect(ix + iw * 0.18, sy, iw * 0.64, 2.5);
        x.fillStyle = 'rgba(0,0,0,.25)'; x.fillRect(ix + iw * 0.18, sy + 7, iw * 0.64, 1);
      }
      _vTable(x, ix + iw * 0.5, iy + ih - 7, 4);                                             // popcorn table
      x.fillStyle = '#e8c14a'; x.fillRect(ix + iw * 0.5 - 1.5, iy + ih - 9, 3, 2);
    },
    // ── ⛪ CHURCH — ✝️ Jesus: aisle, pews, altar + gold cross, candles, glass ──
    church(x, b, ix, iy, iw, ih) {
      x.fillStyle = 'rgba(190,170,220,.10)'; x.fillRect(ix, iy, iw, ih);
      x.fillStyle = 'rgba(150,96,72,.35)'; x.fillRect(ix + iw / 2 - 4, iy, 8, ih);           // centre aisle runner
      x.fillStyle = '#a8a2b8'; x.fillRect(ix + iw * 0.24, iy + 1, iw * 0.52, 8);             // altar dais
      x.fillStyle = '#efe6d2'; x.fillRect(ix + iw / 2 - 6, iy + 3, 12, 5);                   // altar table
      x.fillStyle = '#e8c14a';                                                               // gold cross
      x.fillRect(ix + iw / 2 - 1, iy + 1 - 8, 2, 9); x.fillRect(ix + iw / 2 - 4, iy - 4.5, 8, 2);
      x.fillStyle = '#e8c14a'; x.fillRect(ix + iw * 0.26, iy + 3, 2, 3); x.fillRect(ix + iw * 0.72, iy + 3, 2, 3);  // candles
      x.fillStyle = '#f0a03c'; x.fillRect(ix + iw * 0.26, iy + 2, 2, 1.5); x.fillRect(ix + iw * 0.72, iy + 2, 2, 1.5);
      const rows = Math.min(4, ((ih - 14) / 9) | 0);
      for (let r2 = 0; r2 < rows; r2++) {                                                    // pew columns face the altar
        const py = iy + 13 + r2 * 9;
        x.fillStyle = '#6e4a2b'; x.fillRect(ix + 3, py, iw / 2 - 9, 5); x.fillRect(ix + iw / 2 + 6, py, iw / 2 - 9, 5);
        x.fillStyle = '#8a6238'; x.fillRect(ix + 3, py, iw / 2 - 9, 1.5); x.fillRect(ix + iw / 2 + 6, py, iw / 2 - 9, 1.5);
      }
      x.fillStyle = 'rgba(120,160,235,.35)'; x.fillRect(ix + 1, iy + 6, 2, 10);              // stained-glass light
      x.fillStyle = 'rgba(235,120,140,.35)'; x.fillRect(ix + iw - 3, iy + 6, 2, 10);
    },
    // ── 📚 LIBRARY — stacks + reading tables ──
    library(x, b, ix, iy, iw, ih) {
      x.fillStyle = 'rgba(90,150,120,.10)'; x.fillRect(ix, iy, iw, ih);
      const spines = ['#c25b4e', '#4e7fc2', '#57a06a', '#c2a44e', '#8a5fb0'];
      const rows = Math.min(3, ((ih - 16) / 12) | 0);
      for (let r2 = 0; r2 < rows; r2++) _vShelfRow(x, ix + 3, iy + 2 + r2 * 12, iw - 6, spines);
      const ty = iy + ih - 9;
      x.fillStyle = '#5d4328'; x.fillRect(ix + 5, ty, 18, 6); x.fillRect(ix + iw - 23, ty, 18, 6);   // reading tables
      x.fillStyle = '#ffe9a8'; x.fillRect(ix + 12, ty + 1, 3, 2); x.fillRect(ix + iw - 16, ty + 1, 3, 2);  // lamps
      _vChair(x, ix + 14, ty + 9); _vChair(x, ix + iw - 14, ty + 9);
    },
    // ── 🎓 UNIVERSITY — the knowledge campus: lecture hall (Library/Live-Docs) +
    //    research wing with a glowing KNOWLEDGE-GRAPH board + terminal + flasks ──
    school(x, b, ix, iy, iw, ih) {
      x.fillStyle = 'rgba(110,170,220,.10)'; x.fillRect(ix, iy, iw, ih);
      const mid = ix + iw * 0.55;
      x.strokeStyle = 'rgba(44,33,22,.7)'; x.lineWidth = 2;                                  // partition wall + door gap
      x.beginPath(); x.moveTo(mid, iy); x.lineTo(mid, iy + ih * 0.35); x.moveTo(mid, iy + ih * 0.65); x.lineTo(mid, iy + ih); x.stroke();
      // LECTURE HALL (left): blackboard + desk rows
      x.fillStyle = '#1e3a2c'; x.fillRect(ix + 4, iy + 2, mid - ix - 10, 8);                 // blackboard
      x.strokeStyle = 'rgba(240,240,220,.6)'; x.lineWidth = 0.8;
      x.beginPath(); x.moveTo(ix + 7, iy + 5); x.lineTo(mid - 12, iy + 5); x.moveTo(ix + 7, iy + 7.5); x.lineTo(mid - 18, iy + 7.5); x.stroke();
      const drows = Math.min(3, ((ih - 16) / 10) | 0), dcols = Math.max(2, ((mid - ix - 8) / 14) | 0);
      for (let r2 = 0; r2 < drows; r2++) for (let c2 = 0; c2 < dcols; c2++) {
        const dx2 = ix + 5 + c2 * 14, dy2 = iy + 14 + r2 * 10;
        x.fillStyle = '#5d4328'; x.fillRect(dx2, dy2, 10, 5);
        x.fillStyle = '#efe6d2'; x.fillRect(dx2 + 3, dy2 + 1, 4, 2);                          // open book
        _vChair(x, dx2 + 5, dy2 + 8, '#4a505c');
      }
      // KNOWLEDGE LAB (right): graph board (nodes+edges), bookshelf, terminal, flasks
      const lx = mid + 4, lw = ix + iw - lx - 3;
      x.fillStyle = '#0e1626'; x.fillRect(lx, iy + 2, lw, 12);                                // the knowledge-graph board
      x.strokeStyle = 'rgba(110,180,240,.55)'; x.lineWidth = 0.8;
      const gn = [[0.15, 0.3], [0.45, 0.7], [0.5, 0.2], [0.8, 0.5], [0.9, 0.15]].map(([u, v]) => [lx + u * lw, iy + 3 + v * 10]);
      x.beginPath(); for (const [i2, j2] of [[0, 1], [0, 2], [2, 3], [1, 3], [3, 4], [2, 4]]) { x.moveTo(gn[i2][0], gn[i2][1]); x.lineTo(gn[j2][0], gn[j2][1]); } x.stroke();
      for (let i2 = 0; i2 < gn.length; i2++) { x.fillStyle = ['#7fd0ff', '#8fe0a0', '#f0a860', '#d97ac0', '#e8c14a'][i2]; x.beginPath(); x.arc(gn[i2][0], gn[i2][1], 1.6, 0, 6.283); x.fill(); }
      _vShelfRow(x, lx, iy + 17, lw, ['#c25b4e', '#4e7fc2', '#57a06a', '#c2a44e']);           // research stacks
      x.fillStyle = '#5d4328'; x.fillRect(lx + 1, iy + ih - 9, 12, 6);                        // live-docs terminal
      x.fillStyle = '#1c2530'; x.fillRect(lx + 3, iy + ih - 14, 8, 5);
      x.fillStyle = '#8fe0a0'; x.fillRect(lx + 4, iy + ih - 13, 6, 3);
      x.fillStyle = '#39414f'; x.fillRect(lx + lw - 12, iy + ih - 9, 10, 6);                  // flask bench
      x.fillStyle = '#7fd0ff'; x.fillRect(lx + lw - 10, iy + ih - 12, 2, 3);
      x.fillStyle = '#d97ac0'; x.fillRect(lx + lw - 6, iy + ih - 12, 2, 3);
    },
    // ── 🏛️ GRAND TOWN HALL — council chamber: podium, long table, flags, ballot ──
    townhall(x, b, ix, iy, iw, ih) {
      x.fillStyle = 'rgba(230,200,90,.08)'; x.fillRect(ix, iy, iw, ih);
      x.fillStyle = 'rgba(150,96,72,.28)'; x.fillRect(ix + iw / 2 - 5, iy + ih * 0.4, 10, ih * 0.6);   // ceremonial runner
      x.fillStyle = '#8b8f98'; x.fillRect(ix + iw / 2 - 8, iy + 2, 16, 7);                    // podium dais
      x.fillStyle = '#5d4328'; x.fillRect(ix + iw / 2 - 4, iy + 3, 8, 5);                     // lectern
      x.fillStyle = '#e8c14a'; x.beginPath(); x.arc(ix + iw / 2, iy + 5.5, 1.6, 0, 6.283); x.fill();   // gold seal
      x.fillStyle = '#c0303a'; x.fillRect(ix + 4, iy + 2, 3, 9); x.fillStyle = '#3f5fb0'; x.fillRect(ix + iw - 7, iy + 2, 3, 9);  // flags
      x.fillStyle = '#5d4328'; x.fillRect(ix + 6, iy + ih * 0.42, iw - 12, 7);                // council table
      x.fillStyle = 'rgba(255,255,255,.12)'; x.fillRect(ix + 6, iy + ih * 0.42, iw - 12, 1.5);
      const seats = Math.min(5, ((iw - 12) / 12) | 0);
      for (let i = 0; i < seats; i++) { _vChair(x, ix + 11 + i * 12, iy + ih * 0.42 - 4); _vChair(x, ix + 11 + i * 12, iy + ih * 0.42 + 11); }
      x.fillStyle = '#6b4a2c'; x.fillRect(ix + 3, iy + ih - 8, 10, 6);                        // ballot box
      x.fillStyle = 'rgba(0,0,0,.4)'; x.fillRect(ix + 6, iy + ih - 7, 4, 1);
      x.fillStyle = '#efe6d2'; x.fillRect(ix + iw - 14, iy + ih - 9, 11, 7);                  // notice board
      x.fillStyle = '#c2a44e'; x.fillRect(ix + iw - 13, iy + ih - 8, 4, 2); x.fillStyle = '#5f97c4'; x.fillRect(ix + iw - 8, iy + ih - 8, 4, 3);
    },
    // ── 👔 BOSS'S OFFICE — executive suite: big desk, safe, portrait, meeting nook ──
    exec(x, b, ix, iy, iw, ih) {
      x.fillStyle = 'rgba(120,80,70,.14)'; x.fillRect(ix, iy, iw, ih);
      x.fillStyle = _hex(b.color, .16); x.beginPath(); x.roundRect(ix + iw * 0.2, iy + ih * 0.28, iw * 0.6, ih * 0.4, 3); x.fill();  // office rug
      x.fillStyle = '#3a2a1a'; x.fillRect(ix + iw / 2 - 12, iy + 8, 24, 8);                   // mahogany desk
      x.fillStyle = '#5b3a22'; x.fillRect(ix + iw / 2 - 12, iy + 8, 24, 2);
      x.fillStyle = '#1c2530'; x.fillRect(ix + iw / 2 - 7, iy + 4, 8, 5);                     // monitor
      x.fillStyle = '#7fd0ff'; x.fillRect(ix + iw / 2 - 6, iy + 5, 6, 3);
      x.fillStyle = '#e8c14a'; x.fillRect(ix + iw / 2 + 6, iy + 9, 3, 2);                     // brass lamp
      x.fillStyle = '#232838'; x.beginPath(); x.arc(ix + iw / 2, iy + 20, 4, 0, 6.283); x.fill();   // high-back chair
      x.fillStyle = 'rgba(255,255,255,.15)'; x.beginPath(); x.arc(ix + iw / 2 - 1, iy + 19, 1.6, 0, 6.283); x.fill();
      x.fillStyle = '#6f6a63'; x.fillRect(ix + 3, iy + 2, 9, 10);                             // the safe
      x.fillStyle = '#39414f'; x.fillRect(ix + 4, iy + 3, 7, 8); x.fillStyle = '#e8c14a'; x.beginPath(); x.arc(ix + 7.5, iy + 7, 1.6, 0, 6.283); x.fill();
      x.fillStyle = '#3a2a1a'; x.fillRect(ix + iw - 13, iy + 2, 10, 8);                       // founder's portrait
      x.fillStyle = '#d9c9a8'; x.fillRect(ix + iw - 12, iy + 3, 8, 6); x.fillStyle = '#5b3a22'; x.fillRect(ix + iw - 10, iy + 4, 4, 4);
      _vTable(x, ix + iw - 12, iy + ih - 9, 5); _vChair(x, ix + iw - 20, iy + ih - 9); _vChair(x, ix + iw - 5, iy + ih - 9);  // meeting nook
      _vShelfRow(x, ix + 3, iy + ih - 8, iw * 0.4, ['#c2a44e', '#8a5fb0', '#4e7fc2']);        // trophy shelf
    },
    // ── 🔬 RESEARCH LAB — benches, flasks, fume hood, chalk wall ──
    research(x, b, ix, iy, iw, ih) {
      x.fillStyle = 'rgba(129,140,248,.10)'; x.fillRect(ix, iy, iw, ih);
      x.fillStyle = '#39414f'; x.fillRect(ix + 3, iy + 2, 12, 10);                            // fume hood
      x.fillStyle = 'rgba(140,220,190,.35)'; x.fillRect(ix + 4, iy + 3, 10, 6);
      x.fillStyle = '#10141c'; x.fillRect(ix + 18, iy + 2, iw - 22, 8);                       // formula chalk wall
      x.strokeStyle = 'rgba(140,200,255,.5)'; x.lineWidth = 0.8;
      x.beginPath(); x.moveTo(ix + 20, iy + 6); x.lineTo(ix + 26, iy + 4); x.lineTo(ix + 31, iy + 7); x.lineTo(ix + 37, iy + 3); x.stroke();
      for (let r2 = 0; r2 < 2; r2++) {                                                        // lab benches
        const byy = iy + ih * 0.45 + r2 * 12;
        x.fillStyle = '#8b8f98'; x.fillRect(ix + 4, byy, iw - 8, 6);
        x.fillStyle = '#b9bec7'; x.fillRect(ix + 4, byy, iw - 8, 1.5);
        const fl = ['#7fd0ff', '#8fe0a0', '#d97ac0', '#f0a860'];
        for (let i = 0; i * 9 < iw - 16; i++) { x.fillStyle = fl[(i + r2) % 4]; x.fillRect(ix + 7 + i * 9, byy - 3, 2.5, 3.5); }
      }
    },
    // ── 🔞 VIDEO STORE — GATED: boarded-up storeroom until the layered NSFW gate
    //    is on; then a neon-washed shelf shop. Never anything explicit — it's an
    //    ABSTRACT pixel shop either way; the gate only swaps closed/open dressing. ──
    nsfw(x, b, ix, iy, iw, ih) {
      if (!_nsfwOn()) {                                                                      // boarded up + dark
        x.fillStyle = 'rgba(20,18,24,.45)'; x.fillRect(ix, iy, iw, ih);
        x.strokeStyle = '#4a3b2a'; x.lineWidth = 3;                                          // nailed planks
        x.beginPath(); x.moveTo(ix + 2, iy + 4); x.lineTo(ix + iw - 2, iy + ih * 0.4);
        x.moveTo(ix + 2, iy + ih * 0.55); x.lineTo(ix + iw - 2, iy + ih - 3); x.stroke();
        x.fillStyle = '#6b5a48'; x.fillRect(ix + 4, iy + ih - 10, 9, 8);                     // dusty crates
        x.fillRect(ix + 15, iy + ih - 8, 7, 6);
        x.fillStyle = 'rgba(180,180,190,.25)'; x.beginPath();                                // cobweb corner
        x.moveTo(ix + iw - 2, iy + 2); x.lineTo(ix + iw - 10, iy + 2); x.lineTo(ix + iw - 2, iy + 10); x.closePath(); x.fill();
        return;
      }
      x.fillStyle = 'rgba(120,60,110,.20)'; x.fillRect(ix, iy, iw, ih);                      // plum neon wash
      x.fillStyle = 'rgba(240,106,168,.5)'; x.fillRect(ix + 3, iy + 2, iw - 6, 2);           // neon strip
      _vShelfRow(x, ix + 3, iy + 7, iw - 6, ['#d97ac0', '#8a5fb0', '#c0303a']);              // tape/book racks
      _vShelfRow(x, ix + 3, iy + 17, iw * 0.6, ['#8a5fb0', '#d97ac0']);
      x.fillStyle = '#5d4328'; x.fillRect(ix + iw - 16, iy + ih - 9, 13, 6);                 // counter
      x.fillStyle = '#232838'; x.fillRect(ix + iw - 13, iy + ih - 13, 6, 4);                 // register
      x.fillStyle = '#7a2a4a'; x.fillRect(ix + 3, iy + ih - 10, 3, 8);                       // back-room curtain
      x.fillStyle = '#9a3a5f'; x.fillRect(ix + 6, iy + ih - 10, 3, 8);
    },
    // ── 🏪 SHOP — counter + stocked shelves (goods tinted to the shop's colour) ──
    shop(x, b, ix, iy, iw, ih) {
      const cc = b.color || '#6aa6d6';
      _vShelfRow(x, ix + 3, iy + 2, iw - 6, [_hex(cc, .95), '#d8c090', '#c07a5a']);
      if (ih > 34) _vShelfRow(x, ix + 3, iy + 12, iw * 0.55, ['#7ac0a0', _hex(cc, .8)]);
      x.fillStyle = '#5d4328'; x.fillRect(ix + 4, iy + ih - 9, 17, 6);                       // counter
      x.fillStyle = 'rgba(255,255,255,.12)'; x.fillRect(ix + 4, iy + ih - 9, 17, 1.5);
      x.fillStyle = '#232838'; x.fillRect(ix + 7, iy + ih - 13, 6, 4);                       // register
      x.fillStyle = '#8fe0a0'; x.fillRect(ix + 8, iy + ih - 12, 4, 2);
      _vChair(x, ix + 12, iy + ih - 1, '#4a505c');
    },
    // ── 📬 MAIL ROOM — pigeonhole wall + parcels + service counter ──
    mail(x, b, ix, iy, iw, ih) {
      x.fillStyle = '#4a3b2a'; x.fillRect(ix + 3, iy + 2, iw - 6, 12);                       // pigeonhole wall
      for (let r2 = 0; r2 < 2; r2++) for (let i = 0; i * 7 < iw - 12; i++) {
        x.fillStyle = (i + r2) % 3 ? '#2a2318' : '#efe6d2'; x.fillRect(ix + 5 + i * 7, iy + 3.5 + r2 * 5.5, 5, 4);
      }
      x.fillStyle = '#c89b55'; x.fillRect(ix + 4, iy + ih - 10, 8, 8); x.fillRect(ix + 13, iy + ih - 8, 6, 6);  // parcels
      x.fillStyle = 'rgba(0,0,0,.35)'; x.fillRect(ix + 7, iy + ih - 10, 1, 8);
      x.fillStyle = '#5d4328'; x.fillRect(ix + iw - 20, iy + ih - 9, 17, 6);                 // counter + scale
      x.fillStyle = '#8a919f'; x.fillRect(ix + iw - 15, iy + ih - 12, 6, 3);
    },
    // ── 🖥️ HOMELAB — server racks + workbench + cable run ──
    homelab(x, b, ix, iy, iw, ih) {
      x.fillStyle = 'rgba(30,40,60,.25)'; x.fillRect(ix, iy, iw, ih);
      for (let k = 0; k < Math.min(3, (iw / 16) | 0); k++) {
        const rx = ix + 4 + k * 15;
        x.fillStyle = '#1a2029'; x.fillRect(rx, iy + 2, 11, 15);
        for (let i = 0; i < 5; i++) {
          x.fillStyle = '#2c3542'; x.fillRect(rx + 1.5, iy + 3.5 + i * 2.8, 8, 2);
          x.fillStyle = i % 2 ? '#8fe0a0' : '#f08a8a'; x.fillRect(rx + 8, iy + 4 + i * 2.8, 1.2, 1.2);
        }
      }
      x.strokeStyle = '#e8c14a'; x.lineWidth = 1;                                            // cable run
      x.beginPath(); x.moveTo(ix + 4, iy + 18); x.lineTo(ix + iw - 6, iy + 18); x.stroke();
      x.fillStyle = '#5d4328'; x.fillRect(ix + 4, iy + ih - 9, 20, 6);                       // workbench
      x.fillStyle = '#1c2530'; x.fillRect(ix + 8, iy + ih - 14, 9, 5); x.fillStyle = '#7fd0ff'; x.fillRect(ix + 9, iy + ih - 13, 7, 3);
    },
    // ── 🦪 PEARL MINE — dive pool + shell line + pearl chest ──
    pearl(x, b, ix, iy, iw, ih) {
      x.fillStyle = 'rgba(90,180,160,.12)'; x.fillRect(ix, iy, iw, ih);
      x.fillStyle = '#2f6a9e'; x.beginPath(); x.ellipse(ix + iw * 0.45, iy + ih * 0.5, iw * 0.32, ih * 0.3, 0, 0, 6.283); x.fill();
      x.fillStyle = 'rgba(150,210,255,.4)'; x.beginPath(); x.ellipse(ix + iw * 0.4, iy + ih * 0.44, iw * 0.14, ih * 0.1, 0, 0, 6.283); x.fill();
      x.fillStyle = '#8a6238'; x.fillRect(ix + iw * 0.45 - 3, iy + ih * 0.2 - 4, 14, 3);      // dive plank
      x.fillStyle = '#efe6d2';                                                                // shells drying
      for (let i = 0; i < 4; i++) { x.beginPath(); x.arc(ix + 6 + i * 7, iy + ih - 6, 2.2, 3.14, 6.283); x.fill(); }
      x.fillStyle = '#6b4a2c'; x.fillRect(ix + iw - 14, iy + ih - 10, 11, 8);                 // pearl chest
      x.fillStyle = '#e6ebf3'; x.beginPath(); x.arc(ix + iw - 8.5, iy + ih - 6, 2, 0, 6.283); x.fill();
    },
    // ── 🤖 AI ASSISTANT — console ring + glowing core ──
    assistant(x, b, ix, iy, iw, ih) {
      x.fillStyle = 'rgba(60,50,100,.20)'; x.fillRect(ix, iy, iw, ih);
      const cx2 = ix + iw / 2, cy2 = iy + ih / 2;
      x.fillStyle = '#232838'; x.beginPath(); x.arc(cx2, cy2, Math.min(iw, ih) * 0.24, 0, 6.283); x.fill();   // the core plinth
      x.fillStyle = '#8a5fe0'; x.beginPath(); x.arc(cx2, cy2, Math.min(iw, ih) * 0.13, 0, 6.283); x.fill();   // glowing core
      x.fillStyle = '#c4b5fd'; x.beginPath(); x.arc(cx2 - 1, cy2 - 1.5, Math.min(iw, ih) * 0.05, 0, 6.283); x.fill();
      for (let i = 0; i < 4; i++) {                                                           // console desks around it
        const a2 = i * Math.PI / 2 + 0.78, dx2 = cx2 + Math.cos(a2) * iw * 0.34, dy2 = cy2 + Math.sin(a2) * ih * 0.36;
        x.fillStyle = '#39414f'; x.fillRect(dx2 - 5, dy2 - 3, 10, 6);
        x.fillStyle = ['#7fd0ff', '#8fe0a0', '#f0a860', '#d97ac0'][i]; x.fillRect(dx2 - 3, dy2 - 2, 6, 3);
      }
    },
  };

  // procedural home interior — a real two-room home: bedroom behind a partition
  // (bed/wardrobe/nightstand/rug) + living space (archetype piece, dining table),
  // with sunlight falling in from the windows. Varied per building id.
  function _homeFurnish(x, b, bx, by, bw, bh) {
    const vtint = ['rgba(210,170,110,.07)', 'rgba(150,190,230,.07)', 'rgba(220,150,150,.07)'][(b.id || 0) % 3];
    x.fillStyle = vtint; x.fillRect(bx + TILE, by + TILE, bw - 2 * TILE, bh - 2 * TILE);  // per-house floor tone
    // sunlight streaks from the top-wall windows
    x.fillStyle = 'rgba(255,240,200,.09)';
    x.fillRect(bx + bw * 0.18, by + TILE, TILE * 0.8, bh * 0.42);
    x.fillRect(bx + bw * 0.62, by + TILE, TILE * 0.8, bh * 0.42);
    // bedroom PARTITION: wall stub from the top wall, with a door gap
    const partX = bx + bw * 0.46, partH = bh * 0.52;
    x.strokeStyle = 'rgba(44,33,22,.7)'; x.lineWidth = 2;
    x.beginPath(); x.moveTo(partX, by + TILE); x.lineTo(partX, by + TILE + partH * 0.62); x.stroke();
    x.strokeStyle = 'rgba(210,170,110,.45)'; x.lineWidth = 1;
    x.beginPath(); x.moveTo(partX, by + TILE + partH * 0.62); x.lineTo(partX, by + TILE + partH * 0.62 + 4); x.stroke();  // door frame hint
    // bedroom rug
    x.fillStyle = _hex(b.color, .12); x.beginPath(); x.roundRect(bx + TILE * 0.7, by + bh * 0.36, TILE * 2.2, TILE * 1.5, 3); x.fill();
    const bex = bx + TILE * 0.8, bey = by + bh * 0.42;             // bed along the left wall (clear of the wall art)
    x.fillStyle = '#6e4a2b'; x.fillRect(bex, bey, TILE * 1.5, TILE * 0.95);
    x.fillStyle = '#c9b48a'; x.fillRect(bex + 2, bey + 2, TILE * 1.5 - 4, TILE * 0.9 - 3);   // mattress
    x.fillStyle = '#e6ebf3'; x.fillRect(bex + 2, bey + 2, TILE * 0.5, TILE * 0.9 - 3);       // pillow
    x.fillStyle = _hex(b.color, .8); x.fillRect(bex + TILE * 0.55, bey + 2, TILE * 0.95 - 2, TILE * 0.9 - 3); // blanket
    x.fillStyle = 'rgba(0,0,0,.25)'; x.fillRect(bex, bey + TILE * 0.95 - 2, TILE * 1.5, 2);
    // nightstand by the bed + wardrobe in the bedroom's bottom corner
    x.fillStyle = '#7a5230'; x.fillRect(bex + TILE * 1.6, bey + 2, 6, 7);
    x.fillStyle = '#e8c14a'; x.fillRect(bex + TILE * 1.6 + 2, bey + 4, 2, 1);
    const wx = bx + TILE * 0.75, wy = by + bh - TILE * 1.7;
    x.fillStyle = '#664526'; x.fillRect(wx, wy, 12, TILE * 0.9);                             // wardrobe
    x.fillStyle = 'rgba(0,0,0,.35)'; x.fillRect(wx + 5.5, wy + 1, 1, TILE * 0.9 - 2);        // door split
    x.fillStyle = '#e8c14a'; x.fillRect(wx + 3.5, wy + 8, 1.5, 2); x.fillRect(wx + 7, wy + 8, 1.5, 2);
    // dining table + stools, bottom-right (living room)
    const tx = bx + bw - TILE * 1.3, ty = by + bh - TILE * 1.3;
    x.fillStyle = '#5a3d24'; x.beginPath(); x.arc(tx, ty, 6, 0, 6.283); x.fill();
    x.fillStyle = '#7a5230'; x.beginPath(); x.arc(tx, ty, 4.5, 0, 6.283); x.fill();
    x.fillStyle = '#d8c9a5'; x.fillRect(tx - 2.5, ty - 2.5, 5, 5);                           // table setting
    x.fillStyle = '#8a97ad'; x.beginPath(); x.arc(tx - 8, ty + 1, 2, 0, 6.283); x.fill(); x.beginPath(); x.arc(tx + 8, ty - 1, 2, 0, 6.283); x.fill();
    // archetype piece in the LIVING room, along the top wall right of the partition
    const ax0 = partX + 5, ay0 = by + TILE * 1.1, v = (b.id || 0) % 3;
    if (v === 0) {                                     // KITCHEN: counter run + stove + sink
      const cw = Math.min(bw - TILE * 2.6, TILE * 2.6);
      x.fillStyle = '#8b8f98'; x.fillRect(ax0, ay0, cw, 7);                       // counter
      x.fillStyle = '#b9bec7'; x.fillRect(ax0, ay0, cw, 2);                       // top light
      x.fillStyle = '#2a2f3a'; x.fillRect(ax0 + 2, ay0 + 2, 8, 4);                // stove
      x.fillStyle = '#f0a860'; x.fillRect(ax0 + 3, ay0 + 3, 2, 2); x.fillRect(ax0 + 7, ay0 + 3, 2, 2); // burners
      x.fillStyle = '#5f97c4'; x.fillRect(ax0 + cw - 9, ay0 + 2, 6, 3);           // sink
    } else if (v === 1) {                              // READER: bookshelf + reading chair
      x.fillStyle = '#5d3f26'; x.fillRect(ax0, ay0, TILE * 1.6, 8);
      for (let i = 0; i < 6; i++) { x.fillStyle = ['#c25b4e', '#4e7fc2', '#57a06a', '#c2a44e'][i % 4]; x.fillRect(ax0 + 2 + i * 5, ay0 + 2, 3, 5); }
      x.fillStyle = _hex(b.color, .75); x.fillRect(ax0 + TILE * 1.9, ay0, 9, 8);  // armchair
      x.fillStyle = 'rgba(255,255,255,.2)'; x.fillRect(ax0 + TILE * 1.9, ay0, 9, 2);
    } else {                                           // HEARTH: fireplace + dresser
      x.fillStyle = '#6f6a63'; x.fillRect(ax0, ay0 - 2, 14, 10);                  // stone chimney breast
      x.fillStyle = '#241d16'; x.fillRect(ax0 + 3, ay0 + 2, 8, 5);                // firebox
      x.fillStyle = '#f0a03c'; x.fillRect(ax0 + 4, ay0 + 4, 6, 3);                // embers
      x.fillStyle = '#e8c14a'; x.fillRect(ax0 + 6, ay0 + 3, 2, 2);
      x.fillStyle = '#7a5230'; x.fillRect(ax0 + TILE * 1.4, ay0, 12, 8);          // dresser
      x.fillStyle = 'rgba(0,0,0,.3)'; x.fillRect(ax0 + TILE * 1.4, ay0 + 4, 12, 1);
      x.fillStyle = '#e8c14a'; x.fillRect(ax0 + TILE * 1.4 + 5, ay0 + 2, 2, 1); x.fillRect(ax0 + TILE * 1.4 + 5, ay0 + 6, 2, 1);
    }
    // inner AO ring — the room reads enclosed instead of painted-on
    x.strokeStyle = 'rgba(0,0,0,.16)'; x.lineWidth = 3;
    x.strokeRect(bx + TILE + 1.5, by + TILE + 1.5, bw - 2 * TILE - 3, bh - 2 * TILE - 3);
  }

  // pick a heraldry banner colour for a building kind (identity coding)
  const _BANNER = { hq: 'banner_blue', shop: 'banner_green', townhall: 'banner_red', exec: 'banner_red', leisure: 'banner_green', church: 'banner_blue', library: 'banner_green' };
  function _bannerFor(b) { return _BANNER[b.kind] || null; }

  const _TKEY = { 0: 'grass', 1: 'path', 2: 'floor', 3: 'wall', 4: 'tree', 5: 'water', 6: 'plaza' };
  // STRUCTURAL keys keep their crafted procedural art BY DESIGN (wood floors, wall
  // bevels/insets). Mirrors world_tileset.LOCKED server-side and world-assets'
  // _vetTiles() client-side — they are "unmapped on purpose", never "missing art".
  const _TSTRUCT = { floor: 1, wall: 1 };

  // Paint THIS tile's slice of the whole-world terrain image (Layer 2). It is the
  // FIRST fallback when an atlas cell is unmapped/unloadable, so a PARTIAL tileset
  // degrades to a correct-looking map instead of erasing a feature — the world lost
  // every road for days when `path` sat at null in the manifest and rendered nothing.
  // Returns false when no terrain image is resident (caller then paints procedural).
  function _terrainSlice(x, px, py) {
    const im = _terrainImg;
    if (!im || !im.complete || !im.naturalWidth) return false;
    const sx = im.naturalWidth / W, sy = im.naturalHeight / H;   // image px per world px
    x.drawImage(im, px * sx, py * sy, TILE * sx, TILE * sy, px, py, TILE, TILE);
    return true;
  }

  function _tile(x, c, r) {
    const t = grid[r][c], px = c * TILE, py = r * TILE, v = hsh(c, r);
    // Generated/atlas terrain is OFF by default: the auto-painted atlas produced a
    // stamped single-cell grid + noisy water and kept regenerating the manifest on
    // world load, so procedural terrain (varied per-tile) always wins unless someone
    // explicitly opts in via window.WORLD_ATLAS_TERRAIN = true after the tileset is
    // fixed (per-tile variation + water QA). This is the durable kill-switch.
    // NEVER RENDER NOTHING: when the override is on, an unmapped/unloadable cell
    // falls back for THIS TILE ONLY — terrain image → crafted procedural below.
    if (window.WORLD_ATLAS_TERRAIN === true && window.WA && WA.ready && !_TSTRUCT[_TKEY[t]]) {
      if (WA.tile(x, _TKEY[t], px, py, TILE)) return;
      if (_terrainSlice(x, px, py)) return;
    }
    if (t === T.GRASS) {
      x.fillStyle = v < .5 ? '#3a7d44' : '#357640'; x.fillRect(px, py, TILE, TILE);
      x.fillStyle = 'rgba(74,150,86,.55)'; for (let i = 0; i < 3; i++) { const a = hsh(c, r, i + 1), b = hsh(c, r, i + 5); x.fillRect(px + (a * (TILE - 3) | 0), py + (b * (TILE - 3) | 0), 2, 1); }
      x.fillStyle = 'rgba(30,70,40,.4)'; x.fillRect(px + (hsh(c, r, 9) * TILE | 0), py + (hsh(c, r, 10) * TILE | 0), 1, 2);
      if (v > .93) { x.fillStyle = FLOWERS[(hsh(c, r, 3) * FLOWERS.length | 0)]; const fx = px + 6 + (hsh(c, r, 4) * 7 | 0), fy = py + 6 + (hsh(c, r, 6) * 7 | 0); x.fillRect(fx, fy, 2, 2); x.fillStyle = '#e8e0a0'; x.fillRect(fx, fy, 1, 1); }
    } else if (t === T.PATH || t === T.PLAZA) {
      x.fillStyle = t === T.PLAZA ? '#b9b1a0' : '#9c8f77'; x.fillRect(px, py, TILE, TILE);      // cobble/stone
      x.fillStyle = 'rgba(0,0,0,.12)'; x.fillRect(px, py, TILE, 1); x.fillRect(px, py, 1, TILE);  // grout
      x.fillStyle = t === T.PLAZA ? 'rgba(255,255,255,.10)' : 'rgba(255,240,210,.08)';
      for (let i = 0; i < 2; i++) x.fillRect(px + (hsh(c, r, i + 1) * (TILE - 4) | 0) + 1, py + (hsh(c, r, i + 3) * (TILE - 4) | 0) + 1, 3, 2);
      // bevel where the road meets grass — the path reads as slightly sunken (3D cue)
      const gU = r > 0 && grid[r - 1][c] === T.GRASS, gD = r < ROWS - 1 && grid[r + 1][c] === T.GRASS;
      const gL = c > 0 && grid[r][c - 1] === T.GRASS, gR = c < COLS - 1 && grid[r][c + 1] === T.GRASS;
      if (gU) { x.fillStyle = 'rgba(0,0,0,.22)'; x.fillRect(px, py, TILE, 2); }
      if (gD) { x.fillStyle = 'rgba(255,250,235,.18)'; x.fillRect(px, py + TILE - 2, TILE, 2); }
      if (gL) { x.fillStyle = 'rgba(0,0,0,.14)'; x.fillRect(px, py, 2, TILE); }
      if (gR) { x.fillStyle = 'rgba(255,250,235,.10)'; x.fillRect(px + TILE - 2, py, 2, TILE); }
    } else if (t === T.FLOOR) {                         // warm wood-plank interior
      x.fillStyle = v < .5 ? '#7a5230' : '#734c2c'; x.fillRect(px, py, TILE, TILE);
      x.fillStyle = 'rgba(0,0,0,.18)'; x.fillRect(px, py + (r % 2 ? 6 : 13), TILE, 1);
      x.fillStyle = 'rgba(255,220,170,.06)'; x.fillRect(px, py + 1, TILE, 1);
    } else if (t === T.WALL) {                           // WALL cell is still solid for collision, but no longer
      // rendered as a fat 20px stone ring: paint it as the interior FLOOR look so the
      // baked terrain shows no thick stone band. The thin themed wall is drawn on top
      // per-building by _drawBuildingShell (outer WALL_PX of the footprint edge).
      x.fillStyle = v < .5 ? '#7a5230' : '#734c2c'; x.fillRect(px, py, TILE, TILE);   // warm wood-plank (matches T.FLOOR)
      x.fillStyle = 'rgba(0,0,0,.18)'; x.fillRect(px, py + (r % 2 ? 6 : 13), TILE, 1);
      x.fillStyle = 'rgba(255,220,170,.06)'; x.fillRect(px, py + 1, TILE, 1);
      return;
    } else if (t === T.WALL_UNUSED_BRICK) {              // (legacy procedural brick — no longer reached)
      x.fillStyle = '#8a5a44'; x.fillRect(px, py, TILE, TILE);
      x.fillStyle = 'rgba(0,0,0,.22)'; for (let ry = 0; ry < TILE; ry += 6) x.fillRect(px, py + ry, TILE, 1);
      const off = (r % 2) ? 10 : 0; for (let rx = -off; rx < TILE; rx += 20) x.fillRect(px + rx + 9, py, 1, TILE);
      x.fillStyle = 'rgba(255,235,200,.14)'; x.fillRect(px, py, TILE, 2);
      x.fillStyle = 'rgba(0,0,0,.25)'; x.fillRect(px, py + TILE - 2, TILE, 2);
      if (v > .82) { x.fillStyle = '#bfe3ff'; x.fillRect(px + 6, py + 6, 8, 7); x.fillStyle = '#5b3d2e'; x.strokeStyle = '#5b3d2e'; x.strokeRect(px + 5.5, py + 5.5, 9, 8); x.fillRect(px + 9, py + 6, 1, 7); }  // window
    } else if (t === T.WATER) {
      x.fillStyle = '#2f6a9e'; x.fillRect(px, py, TILE, TILE); x.fillStyle = 'rgba(150,210,255,.4)'; x.fillRect(px + 3, py + (hsh(c, r, 1) * TILE | 0), 8, 1);
    } else if (t === T.TREE) {
      // TALL tree — trunk on this tile, layered canopy rising into the tile above
      // (rows bake top→down, so drawing upward paints over already-baked grass).
      _grassBg(x, px, py, c, r);
      const mx = px + TILE / 2, tall = TILE * (0.75 + v * 0.35);
      x.fillStyle = 'rgba(0,0,0,.30)'; x.beginPath(); x.ellipse(mx + 2, py + TILE - 2, TILE * 0.46, TILE * 0.16, 0, 0, 6.283); x.fill();
      x.fillStyle = '#5a3d24'; x.fillRect(mx - 1.5, py + TILE - 11, 3.5, 10);               // trunk
      x.fillStyle = 'rgba(0,0,0,.25)'; x.fillRect(mx + 1, py + TILE - 11, 1, 10);           // trunk shade
      const cy = py + TILE * 0.35 - tall * 0.45;                                            // canopy centre (raised)
      x.fillStyle = '#1f5c2d'; x.beginPath(); x.ellipse(mx + 1.5, cy + 3, TILE * 0.5, tall * 0.52, 0, 0, 6.283); x.fill();   // dark under-canopy
      x.fillStyle = '#2b7a3d'; x.beginPath(); x.ellipse(mx, cy, TILE * 0.44, tall * 0.48, 0, 0, 6.283); x.fill();
      x.fillStyle = '#3a9450'; x.beginPath(); x.ellipse(mx - 2, cy - tall * 0.14, TILE * 0.3, tall * 0.3, 0, 0, 6.283); x.fill();  // lit side
      x.fillStyle = '#55b168'; x.beginPath(); x.ellipse(mx - 3.5, cy - tall * 0.24, TILE * 0.15, tall * 0.15, 0, 0, 6.283); x.fill();
      if (v > .8) { x.fillStyle = 'rgba(255,235,170,.7)'; x.fillRect(mx + 3, cy + 2, 1.5, 1.5); x.fillRect(mx - 5, cy + 5, 1.5, 1.5); }  // fruit glints
    } else if (t === T.MOUNTAIN) {                        // rocky peak (drawn tall so a band reads as a range)
      _grassBg(x, px, py, c, r);
      const pk = 5 + (v * 9 | 0), mid = px + TILE / 2;
      x.fillStyle = v < .5 ? '#5c606b' : '#666a75'; x.beginPath(); x.moveTo(px - 1, py + TILE); x.lineTo(mid, py - pk); x.lineTo(px + TILE + 1, py + TILE); x.closePath(); x.fill();
      x.fillStyle = '#7d828f'; x.beginPath(); x.moveTo(mid, py - pk); x.lineTo(mid, py + TILE); x.lineTo(px + TILE + 1, py + TILE); x.closePath(); x.fill();   // lit face
      x.fillStyle = 'rgba(0,0,0,.25)'; x.beginPath(); x.moveTo(px - 1, py + TILE); x.lineTo(mid, py + TILE); x.lineTo(mid, py + TILE - 3); x.closePath(); x.fill();
      if (v > .32) { x.fillStyle = '#eef2f8'; x.beginPath(); x.moveTo(mid, py - pk); x.lineTo(mid + 3.5, py - pk + 6); x.lineTo(mid - 3.5, py - pk + 6); x.closePath(); x.fill(); }  // snow cap
    }
  }
  function _grassBg(x, px, py, c, r) { x.fillStyle = hsh(c, r) < .5 ? '#3a7d44' : '#357640'; x.fillRect(px, py, TILE, TILE); }

  // a little picket fence / yard around a house — 3 sides, leaving the door open
  function _fence(x, b) {
    const m = TILE * 0.4, x0 = b.c * TILE - m, y0 = b.r * TILE - m, x1 = (b.c + b.w) * TILE + m, y1 = (b.r + b.h) * TILE + m;
    const post = (px, py) => { x.fillStyle = '#a89168'; x.fillRect(px - 1, py - 5, 2, 6); x.fillStyle = '#c9b488'; x.fillRect(px - 1, py - 5, 2, 1); };
    const rail = (ax, ay, bx, by) => { x.strokeStyle = 'rgba(168,145,104,.85)'; x.lineWidth = 1.5; x.beginPath(); x.moveTo(ax, ay - 2); x.lineTo(bx, by - 2); x.stroke(); };
    const s = b.door;
    if (s !== 'N') { rail(x0, y0, x1, y0); for (let p = x0; p <= x1; p += 7) post(p, y0); }
    if (s !== 'S') { rail(x0, y1, x1, y1); for (let p = x0; p <= x1; p += 7) post(p, y1); }
    if (s !== 'W') { rail(x0, y0, x0, y1); for (let p = y0; p <= y1; p += 7) post(x0, p); }
    if (s !== 'E') { rail(x1, y0, x1, y1); for (let p = y0; p <= y1; p += 7) post(x1, p); }
  }

  function _decorSprite(x, d) {
    if (d.kind === 'lamp') { x.fillStyle = '#2a2f3a'; x.fillRect(d.x - 1, d.y - 9, 2, 9); x.fillStyle = '#4a5568'; x.fillRect(d.x - 2, d.y - 11, 4, 3); x.fillStyle = '#ffe9a8'; x.fillRect(d.x - 1, d.y - 10, 2, 2); x.fillStyle = 'rgba(255,233,168,.25)'; x.beginPath(); x.arc(d.x, d.y - 9, 5, 0, 6.283); x.fill(); }
    else if (d.kind === 'bench') { x.fillStyle = '#6b4c2f'; x.fillRect(d.x - 6, d.y - 2, 12, 3); x.fillStyle = '#4a3b2a'; x.fillRect(d.x - 6, d.y + 1, 2, 3); x.fillRect(d.x + 4, d.y + 1, 2, 3); x.fillStyle = '#7a5836'; x.fillRect(d.x - 6, d.y - 5, 12, 2); }
    else if (d.kind === 'bush') { x.fillStyle = '#256b34'; x.beginPath(); x.arc(d.x, d.y, 4, 0, 6.283); x.fill(); x.fillStyle = '#3ea355'; x.beginPath(); x.arc(d.x - 1, d.y - 1, 2.4, 0, 6.283); x.fill(); if (d.x % 3 < 1) { x.fillStyle = '#e05a6a'; x.fillRect(d.x + 1, d.y - 1, 1, 1); } }
    else if (d.kind === 'rock') { x.fillStyle = '#8a8f9c'; x.beginPath(); x.arc(d.x, d.y, 3, 0, 6.283); x.fill(); x.fillStyle = '#aeb4c0'; x.beginPath(); x.arc(d.x - 1, d.y - 1, 1.4, 0, 6.283); x.fill(); }
    else if (d.kind === 'fountain') { x.fillStyle = '#8a8f9c'; x.beginPath(); x.arc(d.x, d.y, 9, 0, 6.283); x.fill(); x.fillStyle = '#3f7fb0'; x.beginPath(); x.arc(d.x, d.y, 7, 0, 6.283); x.fill(); x.fillStyle = '#aeb4c0'; x.fillRect(d.x - 1, d.y - 8, 2, 8); x.fillStyle = '#bfe4ff'; x.fillRect(d.x - 1, d.y - 10, 2, 3); x.fillStyle = 'rgba(190,228,255,.7)'; x.fillRect(d.x - 3, d.y - 1, 1, 1); x.fillRect(d.x + 2, d.y - 2, 1, 1); }
    else if (d.kind === 'statue') { x.fillStyle = '#6b6f7a'; x.fillRect(d.x - 5, d.y - 2, 10, 3); x.fillStyle = '#9aa0ad'; x.fillRect(d.x - 2, d.y - 14, 4, 12); x.beginPath(); x.arc(d.x, d.y - 15, 2.5, 0, 6.283); x.fill(); x.fillStyle = 'rgba(255,255,255,.2)'; x.fillRect(d.x - 2, d.y - 14, 1.5, 12); }
    else if (d.kind === 'plant') { x.fillStyle = '#6b4c2f'; x.fillRect(d.x - 2, d.y - 3, 4, 4); x.fillStyle = '#2f8542'; x.beginPath(); x.arc(d.x, d.y - 5, 4, 0, 6.283); x.fill(); x.fillStyle = '#3ea355'; x.beginPath(); x.arc(d.x - 1, d.y - 6, 2.4, 0, 6.283); x.fill(); }
    else if (d.kind === 'picnic_table') {
      // checkered blanket + wooden table with side benches + a little basket
      x.fillStyle = '#b8433f'; x.fillRect(d.x - 8, d.y - 5, 16, 11);
      x.fillStyle = '#e8e2d4';
      for (let r = 0; r < 3; r++) for (let c = 0; c < 4; c++)
        if ((r + c) % 2) x.fillRect(d.x - 8 + c * 4, d.y - 5 + r * 4, 4, Math.min(4, 11 - r * 4));
      x.fillStyle = '#6b4c2f'; x.fillRect(d.x - 5, d.y - 3, 10, 5);
      x.fillStyle = '#7a5836'; x.fillRect(d.x - 5, d.y - 4, 10, 2);
      x.fillStyle = '#4a3b2a'; x.fillRect(d.x - 7, d.y - 2, 2, 3); x.fillRect(d.x + 5, d.y - 2, 2, 3);
      x.fillStyle = '#8a5a2b'; x.fillRect(d.x + 1, d.y - 6, 4, 3);
      x.fillStyle = '#c9a15a'; x.fillRect(d.x + 2, d.y - 7, 2, 1);
    }
  }

  // Draw the ground under the live camera transform. Zoomed OUT → the coarsest still-crisp
  // pyramid level (cheap blit). Zoomed IN → the finest overview as an instant fallback, then
  // full-res chunks baked in a CENTERED DISC WINDOW around the camera-centre chunk (prefetch
  // ring, baked on a per-frame budget, evicted once they age out of the window) — only the
  // chunks that actually overlap the viewport are blitted (the overview covers the rest), so
  // both baking and drawImage cost stay flat no matter how big the map is. `canvas` gives the
  // viewport size (falls back to pyramid-only if it's missing).
  function drawTerrain(ctx, canvas) {
    _eraRebakeCheck();                                        // re-bake baked shells when a building's civilization era changed
    if (!_overview) return;
    if (camera.scale < CHUNK_LOD || !canvas) {                  // zoomed out → coarsest crisp pyramid level is enough
      ctx.drawImage(_baseFor(camera.scale), 0, 0, W, H);
      return;
    }
    ctx.drawImage(_overview, 0, 0, W, H);                       // finest LOD as the instant fallback under baking chunks

    const vw = canvas._cssW || canvas.clientWidth || 0, vh = canvas._cssH || canvas.clientHeight || 0;
    if (!vw || !vh) return;
    const cwPx = CHUNK_CW * TILE, chPx = CHUNK_CH * TILE;
    const nX = Math.ceil(COLS / CHUNK_CW), nY = Math.ceil(ROWS / CHUNK_CH);
    // camera-centre world point → its chunk index (the disc window is centred here)
    const ccx = (vw * 0.5 - camera.x) / camera.scale, ccy = (vh * 0.5 - camera.y) / camera.scale;
    const centerCX = Math.floor(ccx / cwPx), centerCY = Math.floor(ccy / chPx);
    // visible world rect → chunk span that actually needs BLITTING (overview covers the rest)
    const wx0 = (0 - camera.x) / camera.scale, wy0 = (0 - camera.y) / camera.scale;
    const wx1 = (vw - camera.x) / camera.scale, wy1 = (vh - camera.y) / camera.scale;
    const vcx0 = Math.max(0, Math.floor(wx0 / cwPx)), vcx1 = Math.min(nX - 1, Math.floor(wx1 / cwPx));
    const vcy0 = Math.max(0, Math.floor(wy0 / chPx)), vcy1 = Math.min(nY - 1, Math.floor(wy1 / chPx));
    const now = performance.now();
    let budget = CHUNK_BUDGET;
    // 1) BAKE/keep-alive the centred disc window (prefetch neighbours → no pan pop-in)
    for (let i = 0; i < _discOffsets.length; i++) {
      const cx = centerCX + _discOffsets[i][0], cy = centerCY + _discOffsets[i][1];
      if (cx < 0 || cy < 0 || cx >= nX || cy >= nY) continue;
      const k = cx + ',' + cy;
      let ch = _chunks.get(k);
      if (!ch) {
        if (budget <= 0) continue;                              // over budget this frame → overview shows through until baked
        budget--; ch = _bakeChunk(cx, cy); _chunks.set(k, ch);
      }
      ch.seen = now;
    }
    // 2) BLIT only the resident chunks overlapping the viewport (cheap; overview under fills gaps)
    for (let cy = vcy0; cy <= vcy1; cy++) for (let cx = vcx0; cx <= vcx1; cx++) {
      const ch = _chunks.get(cx + ',' + cy);
      if (ch) ctx.drawImage(ch.cv, ch.wx, ch.wy);
    }
    // 3) evict chunks that aged out of the window (bounded memory ≈ DISC_MAX resident)
    if (_chunks.size > DISC_MAX) for (const [k, ch] of _chunks)
      if (now - ch.seen > CHUNK_EVICT_MS) _chunks.delete(k);
  }
  const BLD_ICON = { hq: '🏢', townhall: '🏛️', exec: '👔', library: '📚', church: '⛪',
                     bar: '🍺', arcade: '🕹️', tv: '📺', cafe: '☕', park: '🌳',
                     gas: '⛽', lounge: '🛋️', shop: '🏪', house: '🏠',
                     school: '🎓', research: '🔬', mail: '📬', homelab: '🖥️', pearl: '🦪', assistant: '🤖' };
  // naming-theme venue override (world-theme.js) — DISPLAY ONLY. Returns null
  // in the default themed mode (and for non-themed venues) ⇒ today's exact
  // labels/icons; neutral mode relabels ⛪/🍺/🔞 (Advisory Hall / Social
  // Lounge / Private Studio). The gated 🔞 state is excluded on purpose: a
  // CLOSED store stays '🚧 Boarded Up' in every theme (the gate is untouched).
  function _venueTheme(b) {
    if ((b.loc === 'nsfw' || b.kind === 'nsfw') && !_nsfwOn()) return null;
    return (window.WTheme && WTheme.venue && WTheme.venue(b.loc || b.kind)) || null;
  }
  function _bldIcon(b) {
    const v = _venueTheme(b);
    if (v && v.icon) return v.icon;
    if (b.loc === 'nsfw' || b.kind === 'nsfw') return _nsfwOn() ? '🔞' : '🚧';   // gated store: boarded glyph until the NSFW gate is on
    return BLD_ICON[b.loc] || BLD_ICON[b.kind] || '🏢';
  }
  function _plainLabel(b) { return (b.label || '').replace(/[\u{1F000}-\u{1FFFF}☀-➿️]/gu, '').trim(); }

  // Readable name-plate over every building so you can tell them apart at a glance:
  // a dark pill + type ICON + name, colour-keyed to the building.
  function drawBuildingLabels(ctx) {
    ctx.textBaseline = 'alphabetic';
    for (const b of buildings) {
      const em = eraEmoji(b);                         // tiny civilization-era glyph so age reads even zoomed out
      const icon = _bldIcon(b);
      const vt = _venueTheme(b);                      // naming-theme override (null in themed default)
      const name = b.kind === 'hq'
        ? ('THE COMPANY HQ' + (b.stageName ? ' · ' + String(b.stageName).toUpperCase() : ''))
        : ((b.loc === 'nsfw' || b.kind === 'nsfw') && !_nsfwOn()) ? 'Boarded Up'   // gated: reads as a derelict shop
        : vt ? vt.label
        : _plainLabel(b);
      if (b.house) {                                  // houses: just a small roof glyph, no clutter
        ctx.font = '9px sans-serif'; ctx.textAlign = 'center';
        ctx.globalAlpha = 0.5; ctx.fillText((em ? em : '') + '🏠', (b.c + b.w / 2) * TILE, b.r * TILE - 2); ctx.globalAlpha = 1;
        continue;
      }
      const big = b.kind === 'hq';
      ctx.font = `${big ? 'bold 11' : b.small ? '8' : 'bold 9'}px sans-serif`;
      const txt = (em ? em + ' ' : '') + (name ? `${icon} ${name}` : icon);
      // measureText was called for every labelled building EVERY frame though the
      // text + font never change — cache the width on the building (invalidate if
      // the label/font ever changes).
      const twKey = ctx.font + '|' + txt;
      if (b._twKey !== twKey) { b._tw = ctx.measureText(txt).width; b._twKey = twKey; }
      const tw = b._tw, padX = 4, h = big ? 16 : 13;
      const cx = (b.c + b.w / 2) * TILE, bx = Math.round(cx - tw / 2 - padX), by = b.r * TILE - h - 2;
      ctx.fillStyle = 'rgba(10,14,22,.82)';           // pill background
      ctx.fillRect(bx, by, tw + padX * 2, h);
      ctx.fillStyle = b.color || '#cfe0ff';           // colour bar keyed to the building type
      ctx.fillRect(bx, by, 2, h);
      ctx.fillStyle = '#eef4ff'; ctx.textAlign = 'left';
      ctx.fillText(txt, bx + padX, by + h - (big ? 5 : 4));
    }
  }

  // ── geometry + A* ──
  const walkable = (c, r) => inb(c, r) && WALK_COST[grid[r][c]] !== undefined;
  function nearestWalkable(t) {
    if (walkable(t.col, t.row)) return t;
    for (let rad = 1; rad < 10; rad++) for (let dr = -rad; dr <= rad; dr++) for (let dc = -rad; dc <= rad; dc++) { const c = t.col + dc, r = t.row + dr; if (walkable(c, r)) return { col: c, row: r }; }
    return t;
  }
  const tileToPx = (col, row) => ({ x: (col + 0.5) * TILE, y: (row + 0.5) * TILE });
  const DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]];
  function findPath(start, goal) {
    start = { col: Math.round(start.col), row: Math.round(start.row) };
    goal = nearestWalkable({ col: Math.round(goal.col), row: Math.round(goal.row) });
    if (!walkable(start.col, start.row)) start = nearestWalkable(start);
    const key = (c, r) => c + ',' + r;
    const g = {}, f = {}, came = {}, open = new Map();
    const h = (c, r) => Math.hypot(c - goal.col, r - goal.row);
    const sk = key(start.col, start.row); g[sk] = 0; f[sk] = h(start.col, start.row); open.set(sk, start);
    let iter = 0;
    while (open.size && iter++ < 14000) {
      let bk = null, bf = Infinity, bn = null;
      for (const [k, n] of open) { const fv = f[k] ?? Infinity; if (fv < bf) { bf = fv; bk = k; bn = n; } }
      if (bn.col === goal.col && bn.row === goal.row) { const path = []; let cur = bn; while (cur) { path.push(cur); cur = came[key(cur.col, cur.row)]; } return path.reverse(); }
      open.delete(bk);
      for (const [dc, dr] of DIRS) {
        const nc = bn.col + dc, nr = bn.row + dr;
        if (!walkable(nc, nr)) continue;
        if (dc && dr && (!walkable(bn.col + dc, bn.row) || !walkable(bn.col, bn.row + dr))) continue;
        const t = grid[nr][nc];
        const base = t === T.GRASS ? WEAR_COST[wearStage(nc, nr)] : WALK_COST[t];  // worn trails are faster
        const step = (dc && dr ? 1.414 : 1) * base;
        const nk = key(nc, nr), ng = (g[bk] ?? Infinity) + step;
        if (ng < (g[nk] ?? Infinity)) { came[nk] = { col: bn.col, row: bn.row }; g[nk] = ng; f[nk] = ng + h(nc, nr); if (!open.has(nk)) open.set(nk, { col: nc, row: nr }); }
      }
    }
    return null;
  }

  // ── camera ──
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  function fit(vpW, vpH) { camera.scale = Math.min(vpW / W, vpH / H) * 0.98; _fitScale = camera.scale; camera.x = (vpW - W * camera.scale) / 2; camera.y = (vpH - H * camera.scale) / 2; }
  const screenToWorld = (sx, sy) => ({ x: (sx - camera.x) / camera.scale, y: (sy - camera.y) / camera.scale });
  const worldToTile = (wx, wy) => ({ col: Math.floor(wx / TILE), row: Math.floor(wy / TILE) });

  // Window-level drag listeners were re-added on EVERY renderWorld() and never removed —
  // each old pair pinned a stale canvas backing store (8-30MB). Keep refs to the current
  // pair so a new attach (or teardown) can removeEventListener the previous ones.
  let _winMove = null, _winUp = null;
  function detachControls() {
    if (_winMove) window.removeEventListener('mousemove', _winMove);
    if (_winUp) window.removeEventListener('mouseup', _winUp);
    _winMove = _winUp = null;
  }
  function attachControls(canvas, opts) {
    opts = opts || {};
    detachControls();                       // drop any prior view's window listeners
    let drag = null;
    canvas.addEventListener('mousedown', e => {
      if (opts.isEditing && opts.isEditing()) { if (opts.onEditDown && opts.onEditDown(e)) return; }
      drag = { x: e.clientX, y: e.clientY, cx: camera.x, cy: camera.y, moved: 0 };
    });
    _winMove = e => {
      if (opts.isEditing && opts.isEditing() && opts.onEditMove && opts.onEditMove(e)) return;
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.moved += Math.abs(dx) + Math.abs(dy);
      camera.x = drag.cx + dx; camera.y = drag.cy + dy;
    };
    _winUp = e => { if (opts.onEditUp) opts.onEditUp(e); if (drag) { canvas._dragMoved = drag.moved; drag = null; } };
    window.addEventListener('mousemove', _winMove);
    window.addEventListener('mouseup', _winUp);
    canvas.addEventListener('wheel', e => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect(), mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const wx = (mx - camera.x) / camera.scale, wy = (my - camera.y) / camera.scale;
      // min extended far below fit so you can pull ALL the way back into orbit (the
      // town shrinks to a lit patch in space — world-sky.js fades stars in down there).
      camera.scale = clamp(camera.scale * (e.deltaY < 0 ? 1.12 : 0.89), 0.06, 4);
      camera.x = mx - wx * camera.scale; camera.y = my - wy * camera.scale;
    }, { passive: false });
  }

  return {
    TILE, COLS, ROWS, W, H, T, camera,
    build, rasterize, locations, get houseSlots() { return houseSlots; }, get buildings() { return buildings; }, get decor() { return decor; }, get landmarks() { return landmarks; }, get nodes() { return nodes; }, get waterTiles() { return waterTiles; }, get hqRooms() { return hqRooms; },
    walkable, nearestWalkable, tileToPx, findPath,
    bumpWear, wearStage, loadWear, takeWearDirty, get wear() { return wear; },
    tileAt: (c, r) => (inb(c, r) ? grid[r][c] : -1),
    drawTerrain, drawBuildingLabels, drawWallBands, drawInterior, fit, get fitScale() { return _fitScale; }, screenToWorld, worldToTile, attachControls, detachControls,
    eraStyle, eraLevel, eraEmoji,          // civilization-era styling (consumed by world-render-buildings.js roofs)
    setHqStage, applyHqStage, get hqStage() { return _hqStage; },   // HQ progression stages (era snapshots)
    setTerrainImage, setTerrainImageEl, exportLayoutBase,
    setFloorImage, setFloorImageEl, terrainAlive, reheal,
    // edit API
    moveBuilding, resizeBuilding, addBuilding, deleteBuilding, setBuilding, buildingAtTile, addInterior, removeInteriorAt, exportLayout, scheduleSave,
    addDecor, removeDecorNear, decorIndexNear, pickDecor, previewDecor,
    nodeIndexNear, addNode, pickNode, removeNodeAt,
    landmarkIndexNear, addLandmark, pickLandmark, removeLandmarkAt,
  };
})();

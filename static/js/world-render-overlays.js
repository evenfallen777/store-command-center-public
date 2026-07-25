/* ══ THE COMPANY — wear, foley, edit overlay, desks, water, construction, nodes, landmarks, placements, props ══
   Split out of world-render.js (core keeps the loop + shared state). Runs in
   shared global scope (classic script, not a module). Loads right after
   world-render.js, before world-ui.js. Code moved verbatim. */


/* ── desire lines: live overlay of trampled tiles (WM.wear) ──────────────────
   stage 1 dirt scuff → 2 packed earth + pebbles → 3 cobbled road. Drawn over
   the baked grass so no rebake is needed as trails evolve. */
function _drawWear(ctx) {
  const wear = WM.wear; if (!wear) return;
  const T = WM.TILE;
  for (const k in wear) {
    const i = k.indexOf(','); const c = +k.slice(0, i), r = +k.slice(i + 1);
    if (WM.tileAt && WM.tileAt(c, r) !== 0) continue;        // trails only form on grass
    const st = WM.wearStage(c, r); if (!st) continue;
    const x = c * T, y = r * T, h = ((c * 73856093) ^ (r * 19349663)) >>> 0;
    if (st === 1) {                                          // scuffed dirt — subtle, the grass survives
      ctx.fillStyle = 'rgba(122,94,60,.26)';
      ctx.beginPath(); ctx.ellipse(x + T / 2, y + T / 2, T * 0.34, T * 0.26, 0, 0, 6.283); ctx.fill();
    } else if (st === 2) {                                   // packed earth + pebbles
      ctx.fillStyle = 'rgba(133,104,68,.85)'; ctx.fillRect(x + 1, y + 1, T - 2, T - 2);
      ctx.fillStyle = 'rgba(90,70,45,.5)'; ctx.fillRect(x + (h % 12) + 2, y + ((h >> 4) % 12) + 2, 3, 2);
      ctx.fillStyle = 'rgba(190,170,140,.5)'; ctx.fillRect(x + ((h >> 8) % 13) + 2, y + ((h >> 12) % 13) + 2, 2, 2);
    } else {                                                 // cobbled road
      ctx.fillStyle = '#9c8f77'; ctx.fillRect(x, y, T, T);
      ctx.fillStyle = 'rgba(0,0,0,.12)'; ctx.fillRect(x, y, T, 1); ctx.fillRect(x, y, 1, T);
      ctx.fillStyle = 'rgba(255,240,210,.08)';
      ctx.fillRect(x + (h % 9) + 2, y + ((h >> 6) % 9) + 2, 3, 2);
      ctx.fillRect(x + ((h >> 10) % 9) + 8, y + ((h >> 14) % 9) + 8, 3, 2);
    }
  }
}

/* ── per-action positional foley ──────────────────────────────────────────────
   Every visible agent's activity sounds AT its spot: pickaxes at the mine,
   axes in the woods, pages in the library, blades on the wall during a raid.
   The camera is the listener (WAU.sfxAt) — zoom onto a spot and it gets loud;
   distance culling + the per-name throttle inside sfxAt keep it sparse. */
const _ACTION_SND = { mine: 'mine', woodcut: 'chop', farm: 'farm', fish: 'fish', build: 'build', hunt: 'hunt' };
let _sfxNext = 0;
function _actionSfx() {
  if (!window.WAU || !WAU.ready || !WAU.sfxAt) return;
  const now = performance.now();
  if (now < _sfxNext) return;
  _sfxNext = now + 700 + Math.random() * 400;
  for (const s of Object.values(_sprites)) {
    const a = s.agent; if (!a || Math.random() > 0.30) continue;
    let snd = null;
    if (a.state === 'skilling') snd = _ACTION_SND[a.location] || 'build';
    else if (a.state === 'working' && Math.random() < 0.5) snd = 'build';    // tinkering at the desk
    else if (a.state === 'studying') snd = 'study';
    else if (a.state === 'praying') snd = 'pray';
    else if (a.state === 'defending') snd = a.role === 'build' ? 'build' : 'swing';
    if (snd) WAU.sfxAt(snd, s.px, s.py, 350);
  }
}

function _drawEditOverlay(ctx) {
  const TL = WM.TILE;
  const b = _edit.sel != null ? WM.buildings.find(x => x.id === _edit.sel) : null;
  if (b) {
    ctx.strokeStyle = '#a78bfa'; ctx.lineWidth = 2; ctx.setLineDash([5, 3]);
    ctx.strokeRect(b.c * TL, b.r * TL, b.w * TL, b.h * TL); ctx.setLineDash([]);
    ctx.fillStyle = '#a78bfa'; ctx.font = '9px monospace'; ctx.textAlign = 'left';
    ctx.fillText(`${b.kind} ${b.w}×${b.h}`, b.c * TL, b.r * TL - 14);
  }
  if (_edit.ghost) {
    const gh = _edit.ghost;
    ctx.fillStyle = 'rgba(167,139,250,.25)'; ctx.fillRect(gh.c * TL, gh.r * TL, gh.w * TL, gh.h * TL);
    ctx.strokeStyle = '#c4b5fd'; ctx.lineWidth = 2; ctx.strokeRect(gh.c * TL, gh.r * TL, gh.w * TL, gh.h * TL);
  }
  if (_edit.addGhost && _edit.add) {                              // Layer-3 interior-placement tile-snap ghost
    const g = _edit.addGhost, b = WM.buildingAtTile(g.col, g.row);
    const ic = ({ 'interior:door': '🚪', 'interior:window': '🪟', 'interior:object': '🪑',
                  'interior:plant': '🪴', 'interior:crate': '📦' })[g.add] || '▪';
    const onRing = b && (g.col === b.c || g.row === b.r || g.col === b.c + b.w - 1 || g.row === b.r + b.h - 1);
    const ok = b && (!onRing || g.add === 'interior:door');       // mirrors WM.addInterior's guard
    ctx.save();
    ctx.strokeStyle = ok ? '#6ee7a8' : '#f87171'; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
    ctx.strokeRect(g.col * TL, g.row * TL, TL, TL); ctx.setLineDash([]);
    ctx.globalAlpha = 0.9; ctx.font = '12px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(ic, g.col * TL + TL / 2, g.row * TL + TL / 2 + 4);
    ctx.restore();
  }
  if (_edit.agentGhost) {                                      // a PERSON picked up (RCT-style) — show them lifted + target
    const g = _edit.agentGhost, t = WM.worldToTile(g.x, g.y), tp = WM.tileToPx(t.col, t.row);
    const onNode = WM.nodeIndexNear(g.x, g.y) >= 0;
    ctx.save();
    ctx.strokeStyle = onNode ? '#6ee7a8' : '#c4b5fd'; ctx.lineWidth = 2; ctx.setLineDash([4, 3]);
    ctx.strokeRect(t.col * TL, t.row * TL, TL, TL); ctx.setLineDash([]);         // drop-target tile
    const lift = 10 + Math.sin(performance.now() / 150) * 2;                      // bob while carried
    ctx.globalAlpha = 0.35; ctx.fillStyle = '#000'; ctx.beginPath(); ctx.ellipse(g.x, g.y + 2, 7, 3, 0, 0, 6.283); ctx.fill();
    ctx.globalAlpha = 1; ctx.font = '18px sans-serif'; ctx.textAlign = 'center'; ctx.fillText('🧍', g.x, g.y - lift + 6);
    ctx.font = '9px sans-serif'; ctx.fillStyle = onNode ? '#6ee7a8' : '#c4b5fd';
    ctx.fillText(onNode ? '▶ put to work here' : '▷ drop on a spot', g.x, g.y - lift - 8);
    ctx.restore();
  }
  if (_edit.pghost) {                                          // a point-entity (decor / node / landmark / placement) being dragged
    const g = _edit.pghost;
    if (g.type === 'placement') {                              // an agent's furniture piece, carried
      const S = _PLACE_PX[g.p.size] || 9;
      ctx.save();
      ctx.globalAlpha = 0.35; ctx.fillStyle = '#000';
      ctx.beginPath(); ctx.ellipse(g.x, g.y + 2, S * 0.5, S * 0.2, 0, 0, 6.283); ctx.fill();
      ctx.globalAlpha = 0.8; ctx.font = `${S + 3}px serif`; ctx.textAlign = 'center';
      ctx.fillText(g.p.emoji, g.x, g.y - 4);
      ctx.font = '9px sans-serif'; ctx.fillStyle = '#c4b5fd';
      ctx.fillText('▷ drop to place', g.x, g.y - S - 10);
      ctx.restore();
    }
    else if (g.type === 'structure') {                        // an agent-built structure, carried
      ctx.save();
      ctx.globalAlpha = 0.35; ctx.fillStyle = '#000';
      ctx.beginPath(); ctx.ellipse(g.x, g.y + 2, 10, 4, 0, 0, 6.283); ctx.fill();
      ctx.globalAlpha = 0.85; _drawStructShape(ctx, g.s.kind, g.x, g.y);
      ctx.font = '9px sans-serif'; ctx.textAlign = 'center'; ctx.fillStyle = '#c4b5fd';
      ctx.fillText('▷ drop to place', g.x, g.y + 8);
      ctx.restore();
    }
    else if (g.type === 'decor' && WM.previewDecor) { WM.previewDecor(ctx, g.kind, g.x, g.y); }
    else {                                                     // node / landmark → snap-to-tile marker + icon
      const t = WM.worldToTile(g.x, g.y), p = WM.tileToPx(t.col, t.row);
      ctx.save();
      ctx.strokeStyle = '#c4b5fd'; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
      ctx.strokeRect(t.col * TL, t.row * TL, TL, TL); ctx.setLineDash([]);
      const ic = ({ mine: '⛏️', woodcut: '🪓', farm: '🌾', fish: '🎣', build: '🔨',
                    tree_green: '🌲', tree_autumn: '🍂', tree_yellow: '🌳', well: '💧' })[g.kind] || '📍';
      ctx.globalAlpha = 0.85; ctx.font = '13px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText(ic, p.x, p.y + 4); ctx.restore();
    }
  }
}

/* Each department has a themed workstation. If the downloaded pack provides the
   structure sprite (WA), draw it; otherwise a procedural desk + station emoji. */
const DEPT_STATION = {
  devlab:     ['workbench', '🔨'], image:      ['alchemy', '🧪'],
  video:      ['furnace', '🔥'],   models3d:   ['sawmill', '🪚'],
  storefront: ['anvil', '⚒️'],     audio:      ['workbench', '🎹'],
  publishing: ['workbench', '🖨️'], resell:     ['anvil', '📦'],
  trends:     ['alchemy', '🔮'],
  portal:     ['workbench', '🌐'], social:     ['alchemy', '📣'],
  finance:    ['anvil', '💰'],     netsec:     ['furnace', '🛡️'],
};
function _drawDeskMarkers(ctx) {
  for (const key in WM.locations) {
    if (!key.startsWith('desk:')) continue;
    const deptKey = key.slice(5);
    const dept = (_worldState?.departments || []).find(d => d.key === deptKey);
    const t = WM.locations[key], p = WM.tileToPx(t.col, t.row), col = dept ? dept.color : '#68a';
    const st = DEPT_STATION[deptKey];
    if (st && window.WA && WA.hasSprite && WA.hasSprite(st[0])) {
      WA.drawSprite(ctx, st[0], p.x, p.y + 6, WM.TILE * 1.7);       // real workstation sprite
    } else {
      ctx.fillStyle = '#0b1424'; ctx.fillRect(p.x - 11, p.y - 6, 22, 12);
      ctx.fillStyle = col; ctx.fillRect(p.x - 9, p.y - 1, 18, 5);        // desk
      ctx.fillStyle = '#0b1120'; ctx.fillRect(p.x - 6, p.y - 7, 12, 6);  // monitor
      if (st) { ctx.font = '9px serif'; ctx.textAlign = 'center'; ctx.fillText(st[1], p.x, p.y - 8); }
    }
    ctx.fillStyle = col; ctx.font = '7px monospace'; ctx.textAlign = 'center';
    ctx.fillText(dept ? dept.label : deptKey, p.x, p.y - 16);
  }
}

/* big park landmark trees (real pack sprites, on top of the tiles) */
/* Live water: a shimmering surface over baked pond tiles + animated fountain spray.
   Drawn every frame (the terrain canvas underneath stays static). */
function _drawWater(ctx) {
  const TL = WM.TILE, now = performance.now();
  const tiles = WM.waterTiles || [];
  for (const t of tiles) {
    const px = t.col * TL, py = t.row * TL;
    const ph = ((t.col * 7 + t.row * 13) % 10) / 10;
    const wave = 0.5 + 0.5 * Math.sin(now / 720 + (t.col + t.row) * 0.6);   // slow surface swell
    ctx.fillStyle = `rgba(78,150,214,${0.10 + 0.12 * wave})`;
    ctx.fillRect(px, py, TL, TL);
    ctx.fillStyle = 'rgba(180,224,255,.45)';
    for (let i = 0; i < 2; i++) {                                          // scrolling ripple glints
      const sy = py + (((now / 1100) + ph + i * 0.5) % 1) * TL;
      const sx = px + 2 + (Math.sin(now / 520 + t.col + i * 2) * 0.5 + 0.5) * (TL - 8);
      ctx.fillRect(sx, sy, 5, 1);
    }
  }
  for (const d of (WM.decor || [])) {                                      // fountain: rippling pool + rising droplets
    if (d.kind !== 'fountain') continue;
    const swell = 0.5 + 0.5 * Math.sin(now / 400);
    ctx.strokeStyle = `rgba(190,228,255,${0.25 + 0.35 * swell})`; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(d.x, d.y, 6 + swell * 1.5, 0, 6.283); ctx.stroke();
    ctx.fillStyle = 'rgba(210,238,255,.85)';
    for (let i = 0; i < 4; i++) {
      const a = i * 1.571, prog = ((now / 700) + i * 0.25) % 1;            // droplets arc out then fall
      const rad = 2 + prog * 7, dy = -6 + prog * prog * 12;
      ctx.fillRect(d.x + Math.cos(a) * rad, d.y + dy, 1, 2);
    }
  }
}

/* Ghost-build: town structures rise at the build site — a transparent shell that
   fills from the ground up as construction agents work, then stands solid. */
function _drawStructShape(ctx, kind, x, y) {
  if (kind === 'signpost') {
    ctx.fillStyle = '#6b4a2c'; ctx.fillRect(x - 1, y - 14, 3, 14);
    ctx.fillStyle = '#8a6238'; ctx.fillRect(x - 9, y - 16, 18, 7); ctx.strokeStyle = '#5a3d24'; ctx.strokeRect(x - 9, y - 16, 18, 7);
  } else if (kind === 'garden') {
    ctx.fillStyle = '#3f6b32'; ctx.beginPath(); ctx.ellipse(x, y - 2, 12, 6, 0, 0, 6.283); ctx.fill();
    const cols = ['#e05a6a', '#e8c14a', '#d97ac0', '#7fd4ff'];
    for (let i = 0; i < 7; i++) { ctx.fillStyle = cols[i % 4]; ctx.fillRect(x - 9 + i * 3, y - 6 - (i % 3), 2, 2); }
  } else if (kind === 'statue') {
    ctx.fillStyle = '#6b6f7a'; ctx.fillRect(x - 7, y - 4, 14, 4);                 // pedestal
    ctx.fillStyle = '#9aa0ad'; ctx.fillRect(x - 3, y - 20, 6, 16);               // body
    ctx.beginPath(); ctx.arc(x, y - 22, 3, 0, 6.283); ctx.fill();                // head
    ctx.fillStyle = 'rgba(255,255,255,.2)'; ctx.fillRect(x - 3, y - 20, 2, 16);
  } else if (kind === 'fountain') {
    ctx.fillStyle = '#8a8f9c'; ctx.beginPath(); ctx.ellipse(x, y - 2, 11, 5, 0, 0, 6.283); ctx.fill();
    ctx.fillStyle = '#3f7fb0'; ctx.beginPath(); ctx.ellipse(x, y - 3, 8, 3.5, 0, 0, 6.283); ctx.fill();
    ctx.fillStyle = '#aeb4c0'; ctx.fillRect(x - 1, y - 14, 2, 10);
    ctx.fillStyle = '#bfe4ff'; ctx.fillRect(x - 1, y - 16, 2, 3);
  } else if (kind === 'watchtower') {
    ctx.fillStyle = '#7a6a52'; ctx.fillRect(x - 6, y - 22, 12, 22);
    ctx.fillStyle = 'rgba(0,0,0,.2)'; for (let ry = -20; ry < 0; ry += 5) ctx.fillRect(x - 6, y + ry, 12, 1);
    ctx.fillStyle = '#5a4a34'; for (let i = -6; i < 6; i += 4) ctx.fillRect(x + i, y - 26, 3, 4);   // crenellations
    ctx.fillStyle = '#2a2f3a'; ctx.fillRect(x - 2, y - 12, 4, 5);                                    // window
  } else if (kind === 'obelisk') {
    ctx.fillStyle = '#555a63'; ctx.fillRect(x - 6, y - 4, 12, 4);
    ctx.fillStyle = '#7d828f'; ctx.beginPath(); ctx.moveTo(x - 4, y - 4); ctx.lineTo(x, y - 28); ctx.lineTo(x + 4, y - 4); ctx.closePath(); ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,.18)'; ctx.beginPath(); ctx.moveTo(x, y - 28); ctx.lineTo(x + 4, y - 4); ctx.lineTo(x, y - 4); ctx.closePath(); ctx.fill();
  } else if (kind === 'monument') {
    ctx.fillStyle = '#8a6a2a'; ctx.fillRect(x - 8, y - 5, 16, 5);
    ctx.fillStyle = '#e8c14a'; ctx.beginPath(); ctx.moveTo(x - 5, y - 5); ctx.lineTo(x, y - 30); ctx.lineTo(x + 5, y - 5); ctx.closePath(); ctx.fill();
    ctx.fillStyle = '#fff2b0'; ctx.beginPath(); ctx.moveTo(x, y - 30); ctx.lineTo(x + 5, y - 5); ctx.lineTo(x, y - 5); ctx.closePath(); ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,.6)'; ctx.fillRect(x - 1, y - 24, 1, 1);
  }
}
function _drawConstruction(ctx) {
  window._structPos = [];                               // rebuilt each frame; edit-mode hit tests read it
  const con = _worldState && _worldState.company && _worldState.company.construction;
  if (!con) return;
  const bn = WM.locations && WM.locations['build']; if (!bn) return;
  const TL = WM.TILE;
  const slotPos = (slot) => WM.tileToPx(bn.col - 3 + (slot % 5) * 1.6, bn.row + 3 + ((slot / 5) | 0) * 2.3);
  // saved override (ox/oy) wins; else the computed slot — so old structures stay put.
  const posOf = (b) => (b.ox != null && b.oy != null) ? { x: b.ox, y: b.oy } : slotPos(b.slot);
  for (const b of (con.built || [])) {
    const p = posOf(b);
    b._px = p.x; b._py = p.y;                            // cache current pixel pos for hit-test/drag
    if (b.id != null) _structPos.push({ x: p.x, y: p.y, s: b });
    if (_edit.pdrag && _edit.pdrag.type === 'structure' && _edit.pdrag.s === b) continue;  // carried → ghost draws it
    _drawStructShape(ctx, b.kind, p.x, p.y);
  }
  // every in-flight project rises in parallel: blueprints haul materials, frames build up.
  for (const pr of (con.projects || [])) {
    const p = posOf(pr);
    if (pr.status === 'blueprint') {
      // faint dashed ghost + a material (haul) bar
      ctx.save(); ctx.globalAlpha = 0.14; _drawStructShape(ctx, pr.kind, p.x, p.y); ctx.restore();
      ctx.save(); ctx.setLineDash([2, 2]); ctx.strokeStyle = 'rgba(120,170,230,.7)'; ctx.lineWidth = 0.7;
      ctx.strokeRect(p.x - 9, p.y - 24, 18, 24); ctx.setLineDash([]); ctx.restore();
      ctx.fillStyle = 'rgba(0,0,0,.55)'; ctx.fillRect(p.x - 11, p.y - 31, 22, 3);
      ctx.fillStyle = '#5aa0e0'; ctx.fillRect(p.x - 11, p.y - 31, 22 * (pr.mat_pct / 100), 3);   // blue = materials hauled
      ctx.font = '7px sans-serif'; ctx.textAlign = 'center'; ctx.fillStyle = '#a8c8f0'; ctx.fillText('📐 ' + pr.name, p.x, p.y - 34);
    } else {
      const prog = pr.pct / 100;
      ctx.save(); ctx.globalAlpha = 0.18; _drawStructShape(ctx, pr.kind, p.x, p.y); ctx.restore();   // faint full ghost
      ctx.save(); ctx.beginPath(); ctx.rect(p.x - 16, p.y - 32 * prog, 32, 32 * prog + 5); ctx.clip();  // reveal from the ground up
      ctx.globalAlpha = 0.9; _drawStructShape(ctx, pr.kind, p.x, p.y); ctx.restore();
      ctx.strokeStyle = 'rgba(210,170,90,.6)'; ctx.lineWidth = 0.7;
      ctx.strokeRect(p.x - 9, p.y - 26, 18, 26); ctx.beginPath(); ctx.moveTo(p.x - 9, p.y - 26); ctx.lineTo(p.x + 9, p.y); ctx.stroke();
      ctx.fillStyle = 'rgba(0,0,0,.55)'; ctx.fillRect(p.x - 11, p.y - 31, 22, 3);
      ctx.fillStyle = '#e0b050'; ctx.fillRect(p.x - 11, p.y - 31, 22 * prog, 3);                  // amber = build work
      ctx.font = '7px sans-serif'; ctx.textAlign = 'center'; ctx.fillStyle = '#e6c894'; ctx.fillText('🏗️ ' + pr.name, p.x, p.y - 34);
    }
  }
}

/* Resource nodes agents skill at while idle — procedural, readable icons with an emoji tag. */
const _NODE_EMOJI = { woodcut: '🪓', mine: '⛏️', farm: '🌾', fish: '🎣', build: '🔨', hunt: '🏹' };
function _drawNodes(ctx) {
  const TL = WM.TILE;
  for (const nd of (WM.nodes || [])) {
    const p = WM.tileToPx(nd.col, nd.row), x = p.x, y = p.y;
    ctx.fillStyle = 'rgba(0,0,0,.22)'; ctx.beginPath(); ctx.ellipse(x, y + TL * 0.45, TL * 0.5, TL * 0.18, 0, 0, 6.283); ctx.fill();
    if (nd.kind === 'woodcut') {
      ctx.fillStyle = '#6b4a2c'; ctx.fillRect(x - 5, y - 4, 10, 8);
      ctx.fillStyle = '#8a6238'; ctx.beginPath(); ctx.ellipse(x, y - 4, 5, 2.5, 0, 0, 6.283); ctx.fill();
      ctx.fillStyle = '#5a3d24'; ctx.beginPath(); ctx.arc(x, y - 4, 2, 0, 6.283); ctx.fill();
      ctx.fillStyle = '#7a5230'; ctx.fillRect(x + 3, y + 2, 10, 4); ctx.fillRect(x + 3, y + 6, 10, 4);
      ctx.fillStyle = '#caa06a'; ctx.fillRect(x + 12, y + 2, 2, 4); ctx.fillRect(x + 12, y + 6, 2, 4);
      ctx.strokeStyle = '#5b3a22'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(x - 2, y - 5); ctx.lineTo(x - 6, y - 12); ctx.stroke();
      ctx.fillStyle = '#c2cad6'; ctx.fillRect(x - 9, y - 14, 6, 4);
    } else if (nd.kind === 'mine') {
      // ORE DEPOSIT: rock face + minable ore BLOCKS, vein colour varied per node
      // (copper/iron/gold) so each outcrop along the mountain fringe reads distinct.
      const vein = ['#d08a4a', '#aeb4c0', '#e8c14a'][((nd.col || 0) + (nd.row || 0)) % 3];
      ctx.fillStyle = '#7c8493'; ctx.beginPath(); ctx.moveTo(x - 8, y + 4); ctx.lineTo(x - 3, y - 8); ctx.lineTo(x + 4, y - 6); ctx.lineTo(x + 9, y + 4); ctx.closePath(); ctx.fill();
      ctx.fillStyle = '#9aa3b2'; ctx.beginPath(); ctx.moveTo(x - 3, y - 8); ctx.lineTo(x + 4, y - 6); ctx.lineTo(x + 1, y - 1); ctx.closePath(); ctx.fill();
      ctx.fillStyle = vein; ctx.fillRect(x - 4, y - 1, 2, 2); ctx.fillRect(x + 3, y + 1, 2, 2); ctx.fillRect(x - 1, y - 5, 2, 2);
      ctx.fillStyle = 'rgba(255,255,255,.5)'; ctx.fillRect(x - 3.5, y - 0.5, 0.8, 0.8); ctx.fillRect(x - 0.5, y - 4.5, 0.8, 0.8);   // vein glints
      for (const [ox, oy] of [[-11, 6], [9, 7]]) {                        // hewn ore blocks beside the face
        ctx.fillStyle = '#666d7c'; ctx.fillRect(x + ox - 3, y + oy - 3, 6, 6);
        ctx.fillStyle = '#7d8592'; ctx.fillRect(x + ox - 3, y + oy - 3, 6, 1.6);
        ctx.fillStyle = vein; ctx.fillRect(x + ox - 1, y + oy - 1, 2, 2);
      }
      ctx.strokeStyle = '#5b3a22'; ctx.lineWidth = 1.4;                    // pick leaning on the rock
      ctx.beginPath(); ctx.moveTo(x + 6, y + 2); ctx.lineTo(x + 11, y - 5); ctx.stroke();
      ctx.fillStyle = '#c2cad6'; ctx.fillRect(x + 9, y - 8, 5, 3);
    } else if (nd.kind === 'farm') {
      ctx.fillStyle = '#6b4a2c'; ctx.fillRect(x - 9, y - 6, 18, 13);
      ctx.fillStyle = '#5a3d24'; for (let i = 0; i < 3; i++) ctx.fillRect(x - 9, y - 5 + i * 4, 18, 1);
      ctx.fillStyle = '#3ea355'; for (let i = 0; i < 4; i++) { const sx = x - 7 + i * 4; ctx.fillRect(sx, y - 3, 1, 3); ctx.fillRect(sx - 1, y - 4, 3, 1); }
    } else if (nd.kind === 'fish') {
      // FISHING SPOT: jetty planks over the shoreline, rod rack + line, bait bucket, ripple
      ctx.fillStyle = '#8a6238'; ctx.fillRect(x - 7, y + 1, 14, 4);                          // jetty planks
      ctx.fillStyle = '#a9763f'; ctx.fillRect(x - 7, y + 1, 14, 1.2);
      ctx.fillStyle = 'rgba(0,0,0,.25)'; ctx.fillRect(x - 3, y + 1, 1, 4); ctx.fillRect(x + 2, y + 1, 1, 4);
      ctx.fillStyle = '#6b4a2c'; ctx.fillRect(x - 1, y - 8, 3, 10);                          // rod rack post
      ctx.strokeStyle = '#caa06a'; ctx.lineWidth = 1;                                        // two racked rods
      ctx.beginPath(); ctx.moveTo(x + 1, y - 8); ctx.lineTo(x + 9, y - 13); ctx.moveTo(x, y - 7); ctx.lineTo(x - 8, y - 12); ctx.stroke();
      ctx.strokeStyle = 'rgba(210,238,255,.6)'; ctx.beginPath(); ctx.moveTo(x + 9, y - 13); ctx.lineTo(x + 10, y - 2); ctx.stroke();
      ctx.fillStyle = '#c0453a'; ctx.beginPath(); ctx.arc(x + 10, y - 1, 1.2, 0, 6.283); ctx.fill();   // bobber
      ctx.strokeStyle = 'rgba(190,228,255,.5)'; ctx.lineWidth = 0.8;                          // ripple ring
      ctx.beginPath(); ctx.ellipse(x + 10, y + 1, 3.5, 1.4, 0, 0, 6.283); ctx.stroke();
      ctx.fillStyle = '#5b6c7c'; ctx.fillRect(x - 9, y - 4, 5, 5);                            // bait bucket
      ctx.fillStyle = '#7d8f9f'; ctx.fillRect(x - 8, y - 3, 3, 1.5);
    } else if (nd.kind === 'build') {
      ctx.strokeStyle = '#8a6238'; ctx.lineWidth = 2; ctx.strokeRect(x - 8, y - 10, 16, 14);
      ctx.beginPath(); ctx.moveTo(x - 8, y - 10); ctx.lineTo(x + 8, y + 4); ctx.moveTo(x + 8, y - 10); ctx.lineTo(x - 8, y + 4); ctx.stroke();
      ctx.fillStyle = '#a9763f'; ctx.fillRect(x - 9, y + 4, 18, 3);
    } else if (nd.kind === 'hunt') {                                     // straw archery butt on a stand (a huntable spot)
      ctx.fillStyle = '#7a5230'; ctx.fillRect(x - 1, y - 2, 2, 8);       // post
      ctx.fillStyle = '#d9c07a'; ctx.beginPath(); ctx.arc(x, y - 6, 6, 0, 6.283); ctx.fill();   // straw target
      ctx.strokeStyle = '#b89a4a'; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(x, y - 6, 4, 0, 6.283); ctx.stroke();
      ctx.fillStyle = '#c0453a'; ctx.beginPath(); ctx.arc(x, y - 6, 1.6, 0, 6.283); ctx.fill();  // bullseye
      ctx.strokeStyle = '#caa06a'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x - 8, y - 9); ctx.lineTo(x + 1, y - 6); ctx.stroke();  // stuck arrow
    }
    ctx.font = '9px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(_NODE_EMOJI[nd.kind] || '', x, y - 15);
  }
}

function _drawLandmarks(ctx) {
  if (!(window.WA && WA.hasSprite)) return;
  for (const lm of (WM.landmarks || [])) {
    if (!WA.hasSprite(lm.kind)) continue;
    const p = WM.tileToPx(lm.col, lm.row);
    WA.drawSprite(ctx, lm.kind, p.x, p.y + 4, WM.TILE * (lm.scale || 3.2));   // tall tree (or smaller well), base at tile
  }
}

/* Furniture / yard pieces agents bought at the store — placed at THEIR house
   (house slots line the right interior wall; yard slots sit out front) and
   persist in world_placements. Sized: small/medium/large draw bigger. */
const _PLACE_PX = { s: 7, m: 10, l: 13 };
const _YARD_FRAC = [0.18, 0.78, 0.5];
/* Default slot position for a placement (used unless the play-god editor has
   pinned it at an exact point — p.ox/p.oy). Null if the owner's house is
   unresolvable this frame. */
function _placementPos(p) {
  if (p.ox != null && p.oy != null) return { x: p.ox, y: p.oy };
  const T = WM.TILE;
  const hi = _houseByKey[p.agent_key]; if (hi == null || !WM.houseSlots[hi]) return null;
  const ht = WM.houseSlots[hi];
  const b = WM.buildingAtTile(ht.col, ht.row); if (!b) return null;
  const bx = b.c * T, by = b.r * T, bw = b.w * T, bh = b.h * T;
  if (p.spot === 'house') {                       // stacked along the right interior wall
    return { x: bx + bw - T * 1.35,               // (start below the archetype piece at the top wall)
             y: by + T * 2.1 + (p.slot % 4) * Math.max(T * 0.8, (bh - T * 3.2) / 3) };
  }
  return { x: bx + bw * _YARD_FRAC[p.slot % 3],   // yard: out front, clear of the door path
           y: by + bh + T * 0.6 };
}
function _drawPlacements(ctx) {
  window._placePos = [];                          // rebuilt each frame; edit-mode hit tests read it
  const rows = _worldState?.placements; if (!rows || !rows.length) return;
  const byKey = {};
  for (const s of Object.values(_sprites)) if (s.agent) byKey[s.agent.key] = s.agent;
  ctx.textAlign = 'center';
  for (const p of rows) {
    if (!byKey[p.agent_key]) continue;
    const pos = _placementPos(p); if (!pos) continue;
    _placePos.push({ x: pos.x, y: pos.y, p });
    if (_edit.pdrag && _edit.pdrag.type === 'placement' && _edit.pdrag.p === p) continue;  // being carried → ghost draws it
    const S = _PLACE_PX[p.size] || 9;
    ctx.fillStyle = 'rgba(0,0,0,.25)';
    ctx.beginPath(); ctx.ellipse(pos.x, pos.y + 1.5, S * 0.45, S * 0.18, 0, 0, 6.283); ctx.fill();
    ctx.font = `${S}px serif`;
    ctx.fillText(p.emoji, pos.x, pos.y);
  }
}

function _drawProps(ctx) {
  const byLoc = {};
  for (const pr of (_worldState?.props || [])) (byLoc[pr.location] = byLoc[pr.location] || []).push(pr);
  const roomBy = {};
  for (const rm of (WM.hqRooms || [])) roomBy[rm.dept] = rm;
  for (const loc in byLoc) {
    // department props sit tidily along their room's back wall (next to the
    // shelf), not dumped mid-floor where they fought the furniture
    if (loc.startsWith('desk:')) {
      const rm = roomBy[loc.slice(5)];
      if (rm) {
        // on the office floor beside the desk — clear of the wall art frame
        const backTop = rm.door === 'bottom';
        const py = (backTop ? rm.y0 + rm.h * 0.24 : rm.y0 + rm.h * 0.80) + 9;
        byLoc[loc].forEach((pr, i) => _prop(ctx, rm.x0 + 34 + i * 16, py, pr, 17));
        continue;
      }
    }
    const t = WM.locations[loc]; if (!t) continue;
    const p = WM.tileToPx(t.col, t.row);
    byLoc[loc].forEach((pr, i) => _prop(ctx, p.x - 16 + (i % 3) * 16, p.y + 20 + ((i / 3) | 0) * 16, pr));
  }
}

function _prop(ctx, x, y, pr, size) {
  const img = _propImgs[pr.id];
  const S = size || 26;
  // NOTE: props draw STATIC — the generated bob-animation sheets made
  // inanimate things (boxes, keyboards, lamps) hover up and down, which read
  // as a glitch. Sheets stay on disk for anything that genuinely animates.
  if (pr.status === 'done' && img && img.complete && img.naturalWidth) {
    ctx.drawImage(img, x - S / 2, y - S, S, S);          // pixelated sprite
  } else if (pr.status === 'queued' || pr.status === 'generating') {
    // shimmering "being conjured" box
    const a = 0.4 + 0.4 * Math.sin(performance.now() / 200);
    ctx.globalAlpha = a; ctx.strokeStyle = '#a78bfa'; ctx.lineWidth = 1.5;
    ctx.setLineDash([3, 3]); ctx.strokeRect(x - 10, y - 20, 20, 20); ctx.setLineDash([]);
    ctx.globalAlpha = 1; ctx.fillStyle = '#c4b5fd'; ctx.font = '10px serif';
    ctx.textAlign = 'center'; ctx.fillText('✨', x, y - 6);
  } else {
    // unfinished prop (generation failed / not yet produced): a subtle dim marker,
    // NOT a harsh outlined box that reads as a broken/missing slot.
    ctx.globalAlpha = 0.3; ctx.fillStyle = '#2a2440';
    ctx.beginPath(); ctx.arc(x, y - 7, 4, 0, Math.PI * 2); ctx.fill();
    ctx.globalAlpha = 1;
  }
  // caption: readable, width-ellipsized, and hidden when zoomed out so adjacent
  // labels stop overlapping into garble. (Was 4px monospace + mid-word char cut.)
  if (WM.camera.scale >= 2.3) {
    ctx.globalAlpha = 0.55; ctx.fillStyle = '#b9a7ff'; ctx.font = '7px monospace'; ctx.textAlign = 'center';
    const full = pr.label || ''; const maxW = Math.max(S * 1.6, 28);
    let lbl = full;
    while (lbl && ctx.measureText(lbl).width > maxW) lbl = lbl.slice(0, -1);
    if (lbl !== full && lbl) lbl = lbl.slice(0, -1) + '…';
    ctx.fillText(lbl, x, y + 4); ctx.globalAlpha = 1;
  }
}

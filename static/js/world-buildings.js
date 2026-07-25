'use strict';
/* ══════════════════════════════════════════════════════════════════════════
   THE COMPANY — building exteriors (autotiled wall perimeters).
   Draws a real pixel-art wall RING around each building using the Kenney
   tiny-town wooden-frame autotile (tilemap_packed.png, 16×16, indices below).
   Our buildings are rectangular rooms with visible interiors (desks + working
   agents), so a solid roof would hide them — instead we frame the open floor
   with corner/edge/straight wall tiles picked from each cell's position in the
   building rect (deterministic; avoids the top/bottom bitmask ambiguity).
   Degrades to the procedural brick walls in world-map.js if the pack is absent.
   ══════════════════════════════════════════════════════════════════════════ */
window.WB = (function () {
  const PACKS = '/store/static/world_assets/packs';
  const SRC = PACKS + '/kenney-tiny-town/Tilemap/tilemap_packed.png';
  const SHEET_COLS = 12, TS = 16;
  // wooden-frame enclosure autotile (see scratchpad ring test): 3×3 border
  const FRAME = { TL: 44, T: 45, TR: 46, L: 56, R: 58, BL: 68, B: 69, BR: 70 };
  let sheet = null, ready = false;

  function init() {
    return new Promise(res => {
      const im = new Image();
      im.onload = () => { sheet = im; ready = true; console.log('[WB] building tiles loaded'); res(true); };
      im.onerror = () => res(false);
      im.src = SRC;
    });
  }

  function _blit(ctx, idx, dx, dy, size) {
    if (!sheet) return;
    const sx = (idx % SHEET_COLS) * TS, sy = ((idx / SHEET_COLS) | 0) * TS;
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(sheet, sx, sy, TS, TS, dx, dy, size, size);
  }

  // pick the frame tile for a cell at (edge flags) within a building rect
  function _pick(eT, eB, eL, eR) {
    if (eT && eL) return FRAME.TL; if (eT && eR) return FRAME.TR;
    if (eB && eL) return FRAME.BL; if (eB && eR) return FRAME.BR;
    if (eT) return FRAME.T; if (eB) return FRAME.B;
    if (eL) return FRAME.L; return FRAME.R;
  }

  // ring one rect, skipping any cell listed in `skip` (a Set of "c,r" keys)
  function _ring(ctx, c, r, w, h, TILE, skip) {
    for (let cc = c; cc < c + w; cc++) {
      for (let rr = r; rr < r + h; rr++) {
        const eT = rr === r, eB = rr === r + h - 1, eL = cc === c, eR = cc === c + w - 1;
        if (!(eT || eB || eL || eR)) continue;                 // interior stays open
        if (skip && skip.has(cc + ',' + rr)) continue;         // doorway opening
        _blit(ctx, _pick(eT, eB, eL, eR), cc * TILE, rr * TILE, TILE);
      }
    }
  }

  // draw the perimeter wall ring of building b (leaving the door cell open).
  // COMPOUND buildings (b.sections: bbox-local rects + b.doors openings — the
  // staged multi-section HQ) get one ring PER SECTION instead of a bbox ring,
  // with every stage door left open (entrances + section-to-section links).
  function drawWalls(ctx, b, TILE, door) {
    if (!ready) return false;
    if (b.sections && b.sections.length) {
      const skip = new Set((b.doors || []).map(d => (b.c + d.lc) + ',' + (b.r + d.lr)));
      if (door) skip.add(door.c + ',' + door.r);
      for (const s of b.sections) if (!s.open) _ring(ctx, b.c + s.lc, b.r + s.lr, s.w, s.h, TILE, skip);
      return true;
    }
    const skip = door ? new Set([door.c + ',' + door.r]) : null;
    _ring(ctx, b.c, b.r, b.w, b.h, TILE, skip);
    return true;
  }

  return { init, drawWalls, _blit, get ready() { return ready; } };
})();

/* ══ THE COMPANY — the MOON, as a real map you travel to ═════════════════════════
   Phase 4. A self-contained second "place": click the drifting moon (world-sky.js) and
   the view swaps from the Earth-town to a chunked LUNAR surface — the generated moon
   texture as ground + procedural craters, streamed with the SAME overview+chunk LOD as
   the town (bounded memory, any map size), plus a moon base, a planted flag, Earth hanging
   in the black sky, and a starfield. "🌍 Return to Earth" brings you home.

   Reuses WM.camera + WM's pan/zoom controls (same coordinate space, so panning/zooming
   just work) and leaves the ENTIRE town render path untouched — the moon only draws when
   _place === 'moon'. Classic script, shared global scope. */
window.WMOON = (function () {
  let _place = 'earth';                 // 'earth' | 'moon'
  let _moonTex = null;                  // generated lunar texture (ground); null → grey procedural
  let _overview = null; const _OV = 2;  // whole-map LOD (same idea as WM)
  const _chunks = new Map();
  const CW = 22, CH = 26, EVICT = 8000, BUDGET = 2, LOD = 0.5;
  let _earthCam = null;                 // town camera stashed while we're on the moon
  let _stars = null, _sw = 0, _sh = 0;
  let _built = false;
  let _trans = 0;                       // arrival fade-from-black (1→0)
  let _retBtn = null, _wired = false;

  const W = () => (window.WM ? WM.W : 2640);
  const H = () => (window.WM ? WM.H : 2080);
  const TILE = () => (window.WM ? WM.TILE : 20);
  const COLS = () => Math.round(W() / TILE());
  const ROWS = () => Math.round(H() / TILE());
  function active() { return _place === 'moon'; }

  function setMoonTexture(url) {
    if (!url) { _moonTex = null; _built = false; return; }
    const i = new Image();
    i.onload = () => { _moonTex = i; _built = false; _chunks.clear(); };
    i.onerror = () => { _moonTex = null; };
    i.src = url;
  }

  // deterministic hash → [0,1): stable craters per tile, so chunks & overview agree.
  function _rand(x, y, s) {
    let h = (Math.imul(x, 374761393) + Math.imul(y, 668265263) + Math.imul(s, 2246822519)) | 0;
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
  }

  // ── lunar ground: base texture (scaled to fill) + procedural craters for crisp detail ──
  function _paintRegion(x, tc0, tr0, tc1, tr1) {
    const w = W(), h = H(), T = TILE();
    if (_moonTex && _moonTex.complete && _moonTex.naturalWidth) {
      x.imageSmoothingEnabled = false; x.drawImage(_moonTex, 0, 0, w, h);   // whole texture; ctx transform clips to region
    } else {
      x.fillStyle = '#8b887e'; x.fillRect(tc0 * T, tr0 * T, (tc1 - tc0) * T, (tr1 - tr0) * T);
    }
    for (let r = tr0; r < tr1; r++) for (let c = tc0; c < tc1; c++) {
      if (_rand(c, r, 1) > 0.010) continue;                   // sparse crater seeds (lower density → no overlap pile-up)
      const cx = (c + 0.5) * T, cy = (r + 0.5) * T, rad = T * (0.5 + _rand(c, r, 2) * 0.6);
      x.fillStyle = 'rgba(52,50,45,0.4)'; x.beginPath(); x.arc(cx, cy, rad, 0, 6.283); x.fill();
      x.strokeStyle = 'rgba(220,217,206,0.28)'; x.lineWidth = Math.max(0.6, rad * 0.18); x.beginPath(); x.arc(cx - rad * 0.24, cy - rad * 0.24, rad * 0.7, 0, 6.283); x.stroke();   // sunlit rim (thin stroke — translucent fills can't stack into blotches)
      x.fillStyle = 'rgba(34,32,28,0.5)'; x.beginPath(); x.arc(cx + rad * 0.16, cy + rad * 0.16, rad * 0.4, 0, 6.283); x.fill();      // shadowed floor
    }
  }
  function _bakeOverview() {
    if (!_overview) { _overview = document.createElement('canvas'); _overview.width = Math.ceil(W() / _OV); _overview.height = Math.ceil(H() / _OV); }
    const x = _overview.getContext('2d'); x.imageSmoothingEnabled = false;
    x.setTransform(1 / _OV, 0, 0, 1 / _OV, 0, 0); x.clearRect(0, 0, W(), H());
    _paintRegion(x, 0, 0, COLS(), ROWS());
    x.setTransform(1, 0, 0, 1, 0, 0);
    _built = true;
  }
  function _bakeChunk(cx, cy) {
    const T = TILE(), tc0 = cx * CW, tr0 = cy * CH, tc1 = Math.min(COLS(), tc0 + CW), tr1 = Math.min(ROWS(), tr0 + CH);
    const wx = tc0 * T, wy = tr0 * T, ww = (tc1 - tc0) * T, wh = (tr1 - tr0) * T;
    const cv = document.createElement('canvas'); cv.width = ww; cv.height = wh;
    const x = cv.getContext('2d'); x.imageSmoothingEnabled = false; x.translate(-wx, -wy);
    _paintRegion(x, tc0, tr0, tc1, tr1);
    return { cv, wx, wy, seen: performance.now() };
  }
  function _drawGround(ctx, canvas) {
    if (!_built) _bakeOverview();
    ctx.drawImage(_overview, 0, 0, W(), H());
    const cam = WM.camera; if (cam.scale < LOD) return;
    const vw = canvas._cssW || canvas.clientWidth, vh = canvas._cssH || canvas.clientHeight;
    const T = TILE(), cwPx = CW * T, chPx = CH * T;
    const wx0 = (0 - cam.x) / cam.scale, wy0 = (0 - cam.y) / cam.scale, wx1 = (vw - cam.x) / cam.scale, wy1 = (vh - cam.y) / cam.scale;
    const nX = Math.ceil(COLS() / CW), nY = Math.ceil(ROWS() / CH);
    const cx0 = Math.max(0, Math.floor(wx0 / cwPx) - 1), cx1 = Math.min(nX - 1, Math.floor(wx1 / cwPx) + 1);
    const cy0 = Math.max(0, Math.floor(wy0 / chPx) - 1), cy1 = Math.min(nY - 1, Math.floor(wy1 / chPx) + 1);
    const now = performance.now(); let budget = BUDGET;
    for (let cy = cy0; cy <= cy1; cy++) for (let cx = cx0; cx <= cx1; cx++) {
      const k = cx + ',' + cy; let ch = _chunks.get(k);
      if (!ch) { if (budget <= 0) continue; budget--; ch = _bakeChunk(cx, cy); _chunks.set(k, ch); }
      ch.seen = now; ctx.drawImage(ch.cv, ch.wx, ch.wy);
    }
    if (_chunks.size > 16) for (const [k, ch] of _chunks) if (now - ch.seen > EVICT) _chunks.delete(k);
  }

  // ── features (world space): a moon base + a planted flag near the map centre ──
  function _base() { const T = TILE(); return { x: COLS() * 0.5 * T, y: ROWS() * 0.52 * T }; }
  function _drawFeatures(ctx) {
    const T = TILE(), b = _base();
    // landing pad
    ctx.fillStyle = 'rgba(30,30,38,0.55)'; ctx.beginPath(); ctx.arc(b.x, b.y + T * 2.5, T * 3.4, 0, 6.283); ctx.fill();
    ctx.strokeStyle = 'rgba(230,200,90,0.5)'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(b.x, b.y + T * 2.5, T * 3.0, 0, 6.283); ctx.stroke();
    // solar panels
    for (const sx of [-1, 1]) {
      ctx.fillStyle = '#1b2b52'; ctx.fillRect(b.x + sx * T * 4 - T, b.y - T, T * 2, T * 2.4);
      ctx.strokeStyle = 'rgba(120,160,240,0.6)'; ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) { const gx = b.x + sx * T * 4 - T + i * (T * 2 / 4); ctx.beginPath(); ctx.moveTo(gx, b.y - T); ctx.lineTo(gx, b.y + T * 1.4); ctx.stroke(); }
    }
    // habitat domes
    for (const [dx, dr] of [[-1.6, 1.5], [0, 2.0], [1.6, 1.5]]) {
      const hx = b.x + dx * T, hr = dr * T;
      const g = ctx.createRadialGradient(hx - hr * 0.3, b.y - hr * 0.3, hr * 0.2, hx, b.y, hr);
      g.addColorStop(0, '#dfe6f2'); g.addColorStop(1, '#9aa4b8');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(hx, b.y, hr, Math.PI, 0); ctx.fill();
      ctx.fillStyle = '#6b7488'; ctx.fillRect(hx - hr, b.y, hr * 2, 2);
      ctx.fillStyle = 'rgba(120,200,255,0.5)'; ctx.beginPath(); ctx.arc(hx, b.y - hr * 0.35, hr * 0.3, 0, 6.283); ctx.fill();  // porthole
    }
    // antenna
    ctx.strokeStyle = '#c9ccd6'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(b.x + T * 2.4, b.y); ctx.lineTo(b.x + T * 2.4, b.y - T * 3); ctx.stroke();
    ctx.strokeStyle = 'rgba(200,205,215,0.8)'; ctx.beginPath(); ctx.arc(b.x + T * 2.4, b.y - T * 3, T * 0.9, Math.PI * 1.15, Math.PI * 1.85); ctx.stroke();
    // planted flag
    const fx = b.x - T * 6, fy = b.y + T * 1.5;
    ctx.strokeStyle = '#e8e8ee'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(fx, fy); ctx.lineTo(fx, fy - T * 2.6); ctx.stroke();
    ctx.fillStyle = '#3ba7ff'; ctx.fillRect(fx, fy - T * 2.6, T * 1.8, T * 1.1);
    ctx.fillStyle = 'rgba(255,255,255,0.9)'; ctx.font = `${T * 0.8}px sans-serif`; ctx.textBaseline = 'top'; ctx.fillText('🪼', fx + T * 0.2, fy - T * 2.55);
  }

  // ── ambient lunar life: astronauts wandering the base + a rover on patrol ──
  let _life = null;
  function _initLife() {
    const T = TILE(), b = _base();
    _life = {
      astronauts: Array.from({ length: 4 }, () => ({
        cx: b.x + (Math.random() * 2 - 1) * T * 7,
        cy: b.y + T * (2 + Math.random() * 3),
        phase: Math.random() * 6.28, spd: 0.25 + Math.random() * 0.4, rad: T * (2 + Math.random() * 4),
      })),
      rover: Math.random() * 6.28,
    };
  }
  function _drawShadow(ctx, x, y, r) { ctx.fillStyle = 'rgba(10,10,16,0.45)'; ctx.beginPath(); ctx.ellipse(x, y, r, r * 0.4, 0, 0, 6.283); ctx.fill(); }
  function _drawAstronaut(ctx, x, y, bob, T) {
    const s = T * 0.5, dy = Math.abs(Math.sin(bob)) * s * 0.15;
    _drawShadow(ctx, x, y + s, s * 0.7);
    ctx.fillStyle = '#eef1f6'; ctx.fillRect(x - s * 0.4, y - s * 0.9 - dy, s * 0.8, s * 1.1);   // suit
    ctx.fillStyle = '#dfe3ea'; ctx.fillRect(x - s * 0.6, y - s * 0.75 - dy, s * 0.25, s * 0.7);  // pack
    ctx.beginPath(); ctx.arc(x, y - s * 1.05 - dy, s * 0.42, 0, 6.283); ctx.fill();               // helmet
    ctx.fillStyle = '#f0b000'; ctx.beginPath(); ctx.arc(x, y - s * 1.02 - dy, s * 0.26, 0, 6.283); ctx.fill(); // gold visor
  }
  function _drawRover(ctx, x, y, dir, T) {
    const s = T * 0.9;
    _drawShadow(ctx, x, y + s * 0.5, s * 1.1);
    ctx.fillStyle = '#3a3f4a'; for (const wx of [-0.7, 0.7]) { ctx.beginPath(); ctx.arc(x + wx * s, y + s * 0.4, s * 0.3, 0, 6.283); ctx.fill(); }
    ctx.fillStyle = '#c9ccd4'; ctx.fillRect(x - s, y - s * 0.3, s * 2, s * 0.6);                  // chassis
    ctx.fillStyle = '#1b2b52'; ctx.fillRect(x - s * 0.5, y - s * 0.75, s, s * 0.4);               // solar top
    ctx.strokeStyle = '#e8e8ee'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(x + dir * s * 0.9, y - s * 0.3); ctx.lineTo(x + dir * s * 1.3, y - s); ctx.stroke();   // antenna
  }
  function _drawLife(ctx) {
    if (!_life) _initLife();
    const T = TILE(), b = _base(), now = performance.now() / 1000;
    const rt = _life.rover + now * 0.15;
    _drawRover(ctx, b.x + Math.cos(rt) * T * 11, b.y + T * 4.5 + Math.sin(rt) * T * 4, Math.cos(rt) >= 0 ? 1 : -1, T);
    for (const a of _life.astronauts) {
      const ax = a.cx + Math.cos(a.phase + now * a.spd) * a.rad;
      const ay = a.cy + Math.sin((a.phase + now * a.spd) * 0.7) * a.rad * 0.5;
      _drawAstronaut(ctx, ax, ay, now * a.spd * 3 + a.phase, T);
    }
  }

  // ── arriving crew: agents flown up by the space program, doing research near the base ──
  // Reads _worldState.space.on_moon (produced by the sim). Deterministic placement per index
  // with a gentle time-driven wander, IN ADDITION to the ambient _drawLife astronauts.
  function _drawArrivals(ctx) {
    if (typeof _worldState === 'undefined' || !_worldState) return;
    const sp = _worldState.space; if (!sp || !sp.on_moon || !sp.on_moon.length) return;
    const T = TILE(), b = _base(), now = performance.now() / 1000, n = sp.on_moon.length;
    for (let i = 0; i < n; i++) {
      const m = sp.on_moon[i];
      const ang = (i / n) * 6.283 + i * 1.27;                 // spread deterministically around the base
      const rad = T * (5.5 + (i % 3) * 1.6);
      const jx = b.x + Math.cos(ang) * rad;
      const jy = b.y + T * 3 + Math.sin(ang) * rad * 0.45;
      const wx = jx + Math.sin(now * 0.5 + i) * T * 0.6;      // small research wander
      const wy = jy + Math.cos(now * 0.4 + i * 1.3) * T * 0.3;
      _drawAstronaut(ctx, wx, wy, now * 1.4 + i, T);
      // research gizmo: a scanner probe planted beside them + a faint sample beam
      ctx.strokeStyle = '#9aa4b8'; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(wx + T * 0.6, wy + T * 0.2); ctx.lineTo(wx + T * 0.6, wy - T * 0.7); ctx.stroke();
      ctx.fillStyle = 'rgba(120,220,160,0.7)'; ctx.beginPath(); ctx.arc(wx + T * 0.6, wy - T * 0.8, T * 0.14, 0, 6.283); ctx.fill();
      ctx.strokeStyle = 'rgba(120,220,160,0.35)'; ctx.beginPath(); ctx.moveTo(wx + T * 0.6, wy - T * 0.8); ctx.lineTo(wx - T * 0.2, wy + T * 0.1); ctx.stroke();
      // name pill above the helmet
      const nm = (m && m.name) ? m.name : 'crew';
      ctx.font = `${Math.round(T * 0.45)}px sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      const wpx = ctx.measureText(nm).width + T * 0.4;
      ctx.fillStyle = 'rgba(10,14,26,0.55)'; ctx.fillRect(wx - wpx / 2, wy - T * 2.0, wpx, T * 0.62);
      ctx.fillStyle = '#eaf1ff'; ctx.fillText(nm, wx, wy - T * 1.69);
      ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
    }
  }

  // ── Earth hanging in the black sky (screen space, upper area) ──
  // Both radial gradients are memoized by canvas size (ex/ey/er derive from w/h), so they're
  // built once per resize instead of every frame (was a per-frame alloc → measurable lag).
  let _earthCache = null;
  function _drawEarth(ctx, canvas) {
    const w = canvas.width, h = canvas.height;
    if (!_earthCache || _earthCache.w !== w || _earthCache.h !== h) {
      const er = 0.09 * Math.min(w, h), ex = w * 0.78, ey = h * 0.2;
      const glow = ctx.createRadialGradient(ex, ey, er * 0.7, ex, ey, er * 2.2);
      glow.addColorStop(0, 'rgba(120,170,255,0.28)'); glow.addColorStop(1, 'rgba(120,170,255,0)');
      const body = ctx.createRadialGradient(ex - er * 0.3, ey - er * 0.3, er * 0.2, ex, ey, er);
      body.addColorStop(0, '#4a90e0'); body.addColorStop(0.7, '#2f6bb0'); body.addColorStop(1, '#173a66');
      _earthCache = { w, h, er, ex, ey, glow, body };
    }
    const { er, ex, ey, glow, body } = _earthCache;
    ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(ex, ey, er * 2.2, 0, 6.283); ctx.fill();
    ctx.fillStyle = body; ctx.beginPath(); ctx.arc(ex, ey, er, 0, 6.283); ctx.fill();
    ctx.save(); ctx.beginPath(); ctx.arc(ex, ey, er, 0, 6.283); ctx.clip();
    ctx.fillStyle = 'rgba(90,190,120,0.7)';                         // continents
    for (const [dx, dy, rr] of [[-.2, -.1, .5], [.3, .2, .4], [-.1, .4, .35], [.15, -.35, .3]]) {
      ctx.beginPath(); ctx.ellipse(ex + dx * er, ey + dy * er, rr * er, rr * er * 0.7, dx, 0, 6.283); ctx.fill();
    }
    ctx.fillStyle = 'rgba(255,255,255,0.5)';                        // clouds
    for (const [dx, dy, rr] of [[-.35, .15, .28], [.25, -.15, .24], [.05, .45, .2]]) { ctx.beginPath(); ctx.arc(ex + dx * er, ey + dy * er, rr * er, 0, 6.283); ctx.fill(); }
    ctx.restore();
    ctx.fillStyle = 'rgba(255,255,255,0.9)'; ctx.font = `${Math.round(er * 0.34)}px sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.fillText('Earth', ex, ey + er + 4); ctx.textAlign = 'left';
  }

  function _buildStars(w, h) {
    const c = document.createElement('canvas'); c.width = w; c.height = h; const x = c.getContext('2d');
    const n = Math.round(w * h / 2200);
    for (let i = 0; i < n; i++) { const px = Math.random() * w, py = Math.random() * h, big = Math.random() < 0.1; const r = big ? Math.random() * 1.3 + 0.9 : Math.random() * 0.8 + 0.3, b = Math.random() * 0.5 + 0.5; x.fillStyle = `rgba(255,255,255,${b})`; x.beginPath(); x.arc(px, py, r, 0, 6.283); x.fill(); }
    return c;
  }
  function _drawStars(ctx, canvas) {
    const w = canvas.width, h = canvas.height;
    if (!_stars || _sw !== w || _sh !== h) { _stars = _buildStars(w, h); _sw = w; _sh = h; }
    ctx.drawImage(_stars, 0, 0);
  }

  // ── the whole moon-map frame (replaces the town draw while _place==='moon') ──
  function draw(ctx, canvas) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2), cam = WM.camera;
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#03040a'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    _drawStars(ctx, canvas);
    ctx.setTransform(dpr * cam.scale, 0, 0, dpr * cam.scale, dpr * cam.x, dpr * cam.y);
    ctx.imageSmoothingEnabled = false;
    _drawGround(ctx, canvas);
    _drawFeatures(ctx);
    _drawLife(ctx);                     // ambient astronauts + rover (animated)
    _drawArrivals(ctx);                 // crew flown up by the space program, doing research
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    _drawEarth(ctx, canvas);
    if (_trans > 0) { ctx.globalAlpha = _trans; ctx.fillStyle = '#000'; ctx.fillRect(0, 0, canvas.width, canvas.height); ctx.globalAlpha = 1; _trans = Math.max(0, _trans - 0.06); }
  }

  // ── travel ────────────────────────────────────────────────────────────────
  function _fit() { const cv = document.getElementById('world-canvas'); if (cv && window.WM) WM.fit(cv._cssW || cv.clientWidth, cv._cssH || cv.clientHeight); }
  function travelToMoon() {
    if (_place === 'moon' || !window.WM) return;
    _earthCam = { x: WM.camera.x, y: WM.camera.y, scale: WM.camera.scale };
    _place = 'moon'; _trans = 1; _fit();
    if (_retBtn) _retBtn.style.display = '';
    if (window.toast) toast('🌙 Welcome to the Moon');
  }
  function travelToEarth() {
    if (_place !== 'moon') return;
    _place = 'earth';
    if (_earthCam) { WM.camera.x = _earthCam.x; WM.camera.y = _earthCam.y; WM.camera.scale = _earthCam.scale; }
    if (_retBtn) _retBtn.style.display = 'none';
    if (window.toast) toast('🌍 Back on Earth');
  }

  // ── init: attach the moon-click (travel in) + the Return button (travel out) ──
  function init(canvas) {
    if (!canvas) return;
    // click the drifting moon → fly to it
    if (!canvas._wmoonClick) {
      canvas._wmoonClick = true;
      canvas.addEventListener('click', (e) => {
        if (_place !== 'earth' || !window.WSKY || !WSKY.moonScreenRect) return;
        const r = WSKY.moonScreenRect(canvas); if (!r.visible) return;
        const b = canvas.getBoundingClientRect(), mxp = e.clientX - b.left, myp = e.clientY - b.top;
        if (Math.hypot(mxp - r.mx, myp - r.my) <= r.mr * 1.15) { e.stopImmediatePropagation(); travelToMoon(); }
      }, true);   // capture: beat the town's select-agent handler
    }
    // Return-to-Earth button (floats over the canvas; only while on the moon)
    if (!_wired) {
      _wired = true;
      const wrap = document.getElementById('world-canvas-wrap') || canvas.parentElement;
      if (wrap) {
        if (getComputedStyle(wrap).position === 'static') wrap.style.position = 'relative';
        _retBtn = document.createElement('button');
        _retBtn.className = 'btn'; _retBtn.textContent = '🌍 Return to Earth';
        _retBtn.style.cssText = 'position:absolute;top:48px;left:12px;z-index:30;padding:6px 12px;display:none';
        _retBtn.onclick = travelToEarth;
        wrap.appendChild(_retBtn);
      }
    }
    if (_retBtn) _retBtn.style.display = _place === 'moon' ? '' : 'none';
  }

  return { active, draw, init, travelToMoon, travelToEarth, setMoonTexture };
})();

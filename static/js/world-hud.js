'use strict';
/* ══ THE COMPANY — game-first HUD overlay framework ══════════════════════════
   The canvas owns the whole tab; everything else floats on top of it as
   toggleable, draggable panels. This file owns ONLY the DOM around the game:
   tab-world.js stays the shell/loop owner, the world-render-*.js files stay
   the renderer. Panels host the EXISTING render targets (#world-detail,
   #world-townhall, #world-feed), so world-ui.js keeps painting them untouched.

   Loaded on demand by tab-world.js (a classic global-scope script, like every
   other world-*.js). Persistence: localStorage world_hud_open / world_hud_pos
   (same pattern as the nav-group collapse state).                            */
window.WHUD = (function () {
  const LS_OPEN = 'world_hud_open', LS_POS = 'world_hud_pos';

  /* ── panel registry ── */
  const PANELS = [
    { key: 'skills',  icon: '🎯', label: 'Skills',
      tip: 'RuneScape-style skills — one tile per skill with level & XP progress; click a tile for its milestones and what it unlocks.',
      dock: { left: 10, top: 46 }, w: 300, mh: '52%' },
    { key: 'chat',    icon: '💬', label: 'Feed',
      tip: 'Live town feed — every event and spoken thought as it happens.',
      dock: { left: 10, bottom: 10 }, w: 330, mh: '34%', open: true },
    { key: 'agent',   icon: '🧍', label: 'Agent',
      tip: 'The selected citizen: portrait, equipment in use, needs, skills, inventory and actions. Click any character on the map to load them here.',
      dock: { right: 10, top: 46 }, w: 330, mh: '52%' },
    { key: 'quests',  icon: '📜', label: 'Quests',
      tip: 'The quest log — active mandate, prayers awaiting your blessing, research & construction in progress, plus everything completed.',
      dock: { left: 320, top: 46 }, w: 350, mh: '62%' },
    { key: 'company', icon: '🏢', label: 'Company',
      tip: 'Company progress — treasury & real-money budget, town hall, tech age, leaderboard and security posture.',
      dock: { right: 10, bottom: 10 }, w: 340, mh: '42%', open: true },
  ];
  let _open = {}, _pos = {}, _root = null, _skillsMeta = null, _skillDetail = null;
  let _questsData = null, _questsAt = 0, _opsData = null, _opsAt = 0;

  function _load(k, d) { try { return JSON.parse(localStorage.getItem(k) || 'null') || d; } catch { return d; } }
  function _save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} }
  /* _worldState/_selectedId are top-level `let` bindings in tab-world.js —
     shared with classic scripts as bare identifiers, NOT window properties. */
  function _WST() { return (typeof _worldState !== 'undefined' && _worldState) || {}; }

  /* ── styles (injected once — index.html stays untouched) ── */
  function _css() {
    if (document.getElementById('world-hud-css')) return;
    const st = document.createElement('style');
    st.id = 'world-hud-css';
    st.textContent = `
      #world-hud { position:absolute; inset:0; pointer-events:none; z-index:20; font-size:.8rem; }
      .whud-bar { position:absolute; top:0; left:0; right:0; display:flex; gap:6px; align-items:center;
        flex-wrap:wrap; padding:5px 10px; pointer-events:auto;
        background:linear-gradient(180deg, rgba(10,15,26,.92), rgba(10,15,26,.72));
        border-bottom:1px solid var(--border,#2a2f3d); backdrop-filter:blur(4px); }
      .whud-btn { background:rgba(30,35,48,.85); border:1px solid var(--border,#2a2f3d); color:var(--text,#e2e8f0);
        border-radius:8px; padding:4px 10px; font-size:.74rem; cursor:pointer; white-space:nowrap; line-height:1.3; }
      .whud-btn:hover { background:var(--surface2,#1e2330); }
      .whud-btn.on { background:#2a1f4a; border-color:#6d5aff; color:#c4b5fd; }
      .whud-sep { width:1px; height:18px; background:var(--border,#2a2f3d); margin:0 2px; }
      .whud-panel { position:absolute; pointer-events:auto; display:flex; flex-direction:column;
        background:rgba(13,17,26,.88); border:1px solid var(--border,#2a2f3d); border-radius:10px;
        backdrop-filter:blur(5px); box-shadow:0 8px 24px rgba(0,0,0,.45);
        max-height:calc(100% - 100px); min-width:220px; overflow:hidden; }
      .whud-head { display:flex; align-items:center; gap:6px; padding:6px 10px; cursor:grab; user-select:none;
        font-weight:700; font-size:.78rem; color:var(--text,#e2e8f0);
        background:rgba(22,26,34,.9); border-bottom:1px solid var(--border,#2a2f3d); flex-shrink:0; }
      .whud-head:active { cursor:grabbing; }
      .whud-x { margin-left:auto; cursor:pointer; color:var(--muted,#64748b); padding:0 4px; font-size:.85rem; }
      .whud-x:hover { color:var(--text,#e2e8f0); }
      .whud-body { padding:9px 11px; overflow-y:auto; color:var(--muted,#64748b); }
      .whud-body::-webkit-scrollbar { width:8px; } .whud-body::-webkit-scrollbar-thumb { background:#2a2f3d; border-radius:4px; }
      .whud-skill { display:flex; align-items:center; gap:6px; background:rgba(14,22,38,.85); min-width:0;
        border:1px solid var(--border,#2a2f3d); border-radius:8px; padding:5px 6px; cursor:pointer; }
      .whud-skill:hover { border-color:#6d5aff; }
      .whud-skill .sk-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; }
      .whud-pill { display:inline-block; background:rgba(30,35,48,.9); border:1px solid var(--border,#2a2f3d);
        border-radius:12px; padding:2px 9px; font-size:.72rem; color:var(--text,#e2e8f0); white-space:nowrap; }
    `;
    document.head.appendChild(st);
  }

  /* ── toolbar (panel toggles + the meta controls tab-world used to put in the
        view-header; ids preserved — world-clock/-season/-activity/-think-btn/
        -snd-btn/-god-btn/-god-badge/-fs-btn are all read elsewhere) ── */
  function _bar() {
    const toggles = PANELS.map(p =>
      `<button class="whud-btn whud-tgl" data-panel="${p.key}" title="${esc(p.tip)}" onclick="WHUD.toggle('${p.key}')">${p.icon} ${p.label}</button>`).join('');
    return `<div class="whud-bar" id="world-hudbar">
      <span id="world-clock" class="whud-pill">—</span>
      <span id="world-season" class="whud-pill" title="Season & town phase">—</span>
      ${toggles}
      <span id="world-activity" style="font-size:.72rem;color:var(--muted,#64748b);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>
      <span style="margin-left:auto"></span>
      <button class="whud-btn" id="world-think-btn" title="Provoke a thought — the selected agent (or a random one) thinks out loud via the LLM">💭</button>
      <button class="whud-btn" id="world-snd-btn" title="Sound mixer — ambient + effects react to the live world" onclick="worldSndPanel()">🔊</button>
      <button class="whud-btn" id="world-god-btn" title="Play God — edit the map: move buildings, place decor, drop agents onto work" onclick="worldToggleEdit()">🛠️</button>
      <button class="whud-btn" id="world-hq-btn" title="HQ progression stages — era snapshots of the company HQ (view a prior age, advance with the tech tier)" onclick="worldHqPanel()">🏭</button>
      <button class="whud-btn" style="position:relative;background:#2a1f4a;border-color:#6d5aff;color:#c4b5fd;font-weight:600"
        title="${window.WTheme ? WTheme.pick('God Console', 'Owner Console') : 'God Console'} — Prayers · Workboard · Control · Republic · Finances · Settings, all in one place" onclick="worldConsole('god')">🏛️ ${window.WTheme ? WTheme.pick('God', 'Owner') : 'God'}
        <span id="world-god-badge" style="display:none;position:absolute;top:-6px;right:-6px;background:#ef4444;color:#fff;border-radius:10px;font-size:.62rem;font-weight:700;padding:1px 6px;min-width:16px;text-align:center"></span></button>
      <button class="whud-btn" title="Recenter — fit the whole city in view" onclick="worldRecenter()">⤢</button>
      <button class="whud-btn" id="world-fs-btn" title="Browser-fullscreen the game (Esc to exit)" onclick="worldFullscreen()">⛶</button>
    </div>`;
  }

  /* ── panel shells: static hosts the render targets world-ui.js already paints ── */
  function _shell(p) {
    const body = {
      skills:  `<div id="whud-skills-body">Loading skills…</div>`,
      chat:    `<div id="world-feed" style="font-size:.74rem;display:flex;flex-direction:column;gap:4px"></div>`,
      agent:   `<div id="whud-agent-top"></div>
                <div id="world-detail" style="font-size:.8rem">Click a character on the map to inspect them.</div>`,
      quests:  `<div id="whud-quests-body">Loading quests…</div>`,
      company: `<div id="whud-ops-strip"></div><div id="world-townhall"></div>`,
    }[p.key];
    return `<div class="whud-panel" id="whud-${p.key}" style="display:none;width:${p.w}px;max-height:${p.mh || '60%'}" onpointerdown="WHUD.front('${p.key}')">
      <div class="whud-head" data-panel="${p.key}"><span>${p.icon} ${p.label}</span>
        <span class="whud-x" title="Hide (toggle from the bar)" onclick="WHUD.close('${p.key}')">✕</span></div>
      <div class="whud-body">${body}</div>
    </div>`;
  }

  function _place(p) {
    const el = document.getElementById('whud-' + p.key);
    if (!el) return;
    const saved = _pos[p.key];
    el.style.left = el.style.right = el.style.top = el.style.bottom = '';
    if (saved && isFinite(saved.left) && isFinite(saved.top)) {
      el.style.left = Math.max(0, saved.left) + 'px'; el.style.top = Math.max(0, saved.top) + 'px';
    } else {
      for (const side of ['left', 'right', 'top', 'bottom'])
        if (p.dock[side] != null) el.style[side] = p.dock[side] + 'px';
    }
  }

  /* ── cheap drag-by-header (position persisted per panel) ── */
  function _drag(head) {
    head.addEventListener('pointerdown', ev => {
      if (ev.target.classList.contains('whud-x')) return;
      const panel = head.parentElement, root = _root;
      if (!panel || !root) return;
      const pr = panel.getBoundingClientRect(), rr = root.getBoundingClientRect();
      const dx = ev.clientX - pr.left, dy = ev.clientY - pr.top;
      const move = e => {
        const left = Math.min(Math.max(0, e.clientX - rr.left - dx), rr.width - 60);
        const top = Math.min(Math.max(0, e.clientY - rr.top - dy), rr.height - 40);
        panel.style.left = left + 'px'; panel.style.top = top + 'px';
        panel.style.right = panel.style.bottom = 'auto';
      };
      const up = () => {
        window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
        _pos[head.dataset.panel] = { left: parseFloat(panel.style.left) || 0, top: parseFloat(panel.style.top) || 0 };
        _save(LS_POS, _pos);
      };
      window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
      ev.preventDefault();
    });
  }

  /* ── lifecycle ── */
  function init(stage) {
    _css();
    _open = _load(LS_OPEN, null) || Object.fromEntries(PANELS.map(p => [p.key, !!p.open]));
    _pos = _load(LS_POS, {});
    _root = document.createElement('div');
    _root.id = 'world-hud';
    _root.innerHTML = _bar() + PANELS.map(_shell).join('');
    stage.appendChild(_root);
    _root.querySelectorAll('.whud-head').forEach(_drag);
    PANELS.forEach(p => { _place(p); _apply(p.key); });
    _fetchSkillsMeta();
  }

  function _apply(key) {
    const el = document.getElementById('whud-' + key);
    const btn = _root && _root.querySelector(`.whud-tgl[data-panel="${key}"]`);
    if (el) el.style.display = _open[key] ? 'flex' : 'none';
    if (btn) btn.classList.toggle('on', !!_open[key]);
    if (_open[key]) _renderPanel(key);
  }
  function toggle(key) { _open[key] = !_open[key]; _save(LS_OPEN, _open); _apply(key); if (_open[key]) front(key); }
  function open(key)   { if (!_open[key]) { _open[key] = true; _save(LS_OPEN, _open); _apply(key); } else _renderPanel(key); front(key); }
  function close(key)  { _open[key] = false; _save(LS_OPEN, _open); _apply(key); }
  let _z = 30;   // overlapping panels: the one you touch rises to the top
  function front(key) { const el = document.getElementById('whud-' + key); if (el) el.style.zIndex = String(++_z); }

  /* called from tab-world's 3s poll — refresh whatever is showing */
  function onState() {
    if (!_root || !document.getElementById('world-hud')) return;
    if (_open.skills) _renderSkills();
    if (_open.agent)  _renderAgentTop();
    if (_open.quests) _renderQuests();
    if (_open.company) _renderOps();
    // #world-feed / #world-detail / #world-townhall are painted by world-ui.js
  }
  function _renderPanel(key) {
    if (key === 'skills') _renderSkills();
    if (key === 'agent')  { _renderAgentTop(); if (typeof _renderDetail === 'function') _renderDetail(); }
    if (key === 'chat' && typeof _renderFeed === 'function' && _WST().events) _renderFeed(_worldState.events || []);
    if (key === 'quests') { _questsAt = 0; _renderQuests(); }
    if (key === 'company') { _opsAt = 0; _renderOps(); if (typeof _renderTownHall === 'function' && _WST().agents) _renderTownHall(_worldState); }
  }

  /* ═══ 🎯 SKILLS — RuneScape-style grid + tier/milestone detail ═══ */
  // Fallback copy of the backend meta (GET /api/world/hud/skills needs a server
  // restart to exist; until then the panel runs entirely on this).
  const _FALLBACK_META = {
    curve: { base: 80 },
    skills: [
      { key: 'woodcutting',  kind: 'gather', emoji: '🪓', action: 'chopping wood',  resource: 'logs',    unlocks: '+4% logs yield per level.' },
      { key: 'mining',       kind: 'gather', emoji: '⛏️', action: 'mining ore',     resource: 'ore',     unlocks: '+4% ore yield per level.' },
      { key: 'farming',      kind: 'gather', emoji: '🌾', action: 'tending crops',  resource: 'crops',   unlocks: '+4% crops yield per level.' },
      { key: 'fishing',      kind: 'gather', emoji: '🎣', action: 'fishing',        resource: 'fish',    unlocks: '+4% fish yield per level.' },
      { key: 'construction', kind: 'gather', emoji: '🔨', action: 'hammering away', resource: 'planks',  unlocks: '+4% planks yield per level.' },
      { key: 'hunting',      kind: 'gather', emoji: '🏹', action: 'stalking game',  resource: 'venison', unlocks: '+4% venison yield per level — the wilds feed the shop.' },
      { key: 'attack',       kind: 'combat', emoji: '⚔️', action: 'raid combat & drills', unlocks: 'Hits harder in raids.' },
      { key: 'defense',      kind: 'combat', emoji: '🛡️', action: 'holding the walls',    unlocks: 'Takes less damage defending.' },
      { key: 'knowledge',    kind: 'combat', emoji: '📖', action: 'studying at the library', unlocks: '+3% wage & XP on real work per level; study feeds research.' },
    ].map(s => ({ ...s, milestones: [1, 5, 10, 15, 20, 30, 40, 50].map(lv => ({ level: lv, xp: (lv - 1) * (lv - 1) * 80 })) })),
  };
  async function _fetchSkillsMeta() {
    try { _skillsMeta = await api('/api/world/hud/skills'); }
    catch { _skillsMeta = null; }   // old backend — fallback carries the panel
    if (_open.skills) _renderSkills();
  }
  function _meta() { return _skillsMeta || _FALLBACK_META; }
  function _skillAgent() {
    const ags = (typeof _worldState !== 'undefined' && _worldState && _worldState.agents) || [];
    return ags.find(a => a.id === _selectedId) || ags.find(a => a.key === 'player_god')
        || ags.find(a => a.kind === 'worker' || a.kind === 'openclaw') || null;
  }
  function _lvlOf(xp, base) { return 1 + Math.floor(Math.sqrt(Math.max(0, xp) / base)); }
  function _xpFor(lvl, base) { return (Math.max(1, lvl) - 1) ** 2 * base; }

  function _renderSkills() {
    const el = document.getElementById('whud-skills-body');
    if (!el) return;
    const a = _skillAgent();
    if (!a) { el.innerHTML = 'World still loading…'; return; }
    const meta = _meta(), base = (meta.curve && meta.curve.base) || 80;
    if (_skillDetail) { el.innerHTML = _skillDetailHtml(a, _skillDetail, meta, base); return; }
    const skills = a.skills || {};
    const total = Object.values(skills).reduce((s, v) => s + (v.level || 1), 0) || 0;
    const tiles = meta.skills.map(m => {
      const s = skills[m.key] || { xp: 0, level: 1 };
      const cur = _xpFor(s.level, base), nxt = _xpFor(s.level + 1, base);
      const pct = Math.max(2, Math.min(100, Math.round(((s.xp - cur) / Math.max(1, nxt - cur)) * 100)));
      const prim = a.primary_skill === m.key;
      return `<div class="whud-skill" onclick="WHUD.skill('${m.key}')" title="${esc(m.key)} — ${s.xp} xp · ${nxt - s.xp} xp to L${s.level + 1}. Click for milestones & unlocks.">
        <span style="font-size:1.15rem">${m.emoji}</span>
        <div style="flex:1;min-width:0">
          <div style="display:flex;justify-content:space-between;gap:3px;font-size:.68rem">
            <span class="sk-name" style="color:${prim ? '#e8eefc' : '#8a97ad'};text-transform:capitalize">${esc(m.key)}${prim ? ' ★' : ''}</span>
            <b style="color:#e8eefc;flex-shrink:0">${s.level}</b></div>
          <div style="height:5px;background:#0b1120;border-radius:3px;overflow:hidden;margin-top:2px">
            <div style="height:100%;width:${pct}%;background:${m.kind === 'combat' ? '#f472b6' : '#7dd3fc'}"></div></div>
        </div></div>`;
    }).join('');
    el.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:7px;font-size:.72rem">
        <span style="color:#e8eefc;font-weight:600">${esc(a.name)}</span>
        <span class="whud-pill" title="Total level — all skills summed">Σ ${total}</span></div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:5px">${tiles}</div>
      <div style="font-size:.64rem;color:#54607a;margin-top:6px">★ specialisation · click a skill for tiers & unlocks · select a citizen on the map to view theirs</div>`;
  }

  function _skillDetailHtml(a, key, meta, base) {
    const m = meta.skills.find(x => x.key === key) || { emoji: '•', key };
    const s = (a.skills || {})[key] || { xp: 0, level: 1 };
    const spec = (((_WST()).company || {}).specialists || {})[key];
    const rows = (m.milestones || []).map(ms => {
      const got = s.level >= ms.level;
      return `<div style="display:flex;gap:8px;font-size:.72rem;padding:2px 0;color:${got ? '#6ee7a8' : '#8a97ad'}">
        <span style="width:34px">${got ? '✓' : '·'} L${ms.level}</span>
        <span style="color:${got ? '#9fe0b0' : '#54607a'}">${ms.xp} xp${m.kind !== 'combat' ? ` · +${(ms.level - 1) * 4}% yield` : ''}</span></div>`;
    }).join('');
    // the tech ladder is the gathering "tool tier" tree — better tools each age
    const tech = (meta.tech || ((_WST()).company || {}).tech);
    const ladder = (m.kind === 'gather' && tech && tech.ladder) ? `
      <div style="font-size:.68rem;color:#7a86a0;margin:8px 0 3px">⚒️ Tool tiers (company tech age)</div>
      <div>${tech.ladder.map(t => `<span title="${esc(t.name)}${t.unlocked ? ' — unlocked' : ' — locked'}" style="font-size:1rem;opacity:${t.unlocked ? 1 : .3};margin-right:3px">${t.emoji}</span>`).join('<span style="color:#3a4a5a">›</span>')}
      ${tech.bonus > 1 ? `<span style="font-size:.66rem;color:#6ee7a8;margin-left:5px">+${Math.round((tech.bonus - 1) * 100)}% yield</span>` : ''}</div>` : '';
    const cur = _xpFor(s.level, base), nxt = _xpFor(s.level + 1, base);
    const pct = Math.max(2, Math.min(100, Math.round(((s.xp - cur) / Math.max(1, nxt - cur)) * 100)));
    return `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
        <button class="whud-btn" style="padding:2px 8px" onclick="WHUD.skill(null)">←</button>
        <span style="font-size:1.3rem">${m.emoji}</span>
        <div><div style="color:#e8eefc;font-weight:700;text-transform:capitalize">${esc(key)}</div>
        <div style="font-size:.66rem;color:#7a86a0">${esc(a.name)} · L${s.level} · ${s.xp} xp</div></div></div>
      <div style="height:6px;background:#0b1120;border-radius:3px;overflow:hidden;margin-bottom:6px">
        <div style="height:100%;width:${pct}%;background:#7dd3fc"></div></div>
      <div style="font-size:.7rem;color:#aeb9cc">${esc(m.action || '')}${m.resource ? ` → <b style="color:#d8c090">${esc(m.resource)}</b> for the stockpile` : ''}</div>
      <div style="font-size:.7rem;color:#9fe0b0;margin:4px 0 8px">${esc(m.unlocks || '')}</div>
      <div style="font-size:.68rem;color:#7a86a0;margin-bottom:2px">Milestones</div>${rows}
      ${ladder}
      ${spec ? `<div style="font-size:.68rem;color:#7a86a0;margin-top:8px">🏅 Company specialist: <b style="color:#e8eefc">${esc(spec.name)}</b> · L${spec.level}</div>` : ''}`;
  }
  function skill(key) { _skillDetail = key || null; _renderSkills(); }

  /* ═══ 🧍 AGENT card top: portrait + equipment-in-use (no gear backend exists —
         this derives the implied tool/weapon from what they're doing NOW) ═══ */
  function _portraitHtml(a) {
    const eid = 'agent_' + (a.key || a.id);
    const m = window.WSP && WSP.index && WSP.index[eid] && WSP.index[eid].sheets && WSP.index[eid].sheets.idle;
    if (m && m.url) {
      const url = m.url + (m.v ? (m.url.includes('?') ? '&' : '?') + 'v=' + m.v : '');
      const fw = m.fw || 64, fh = m.fh || 64, fr = m.frames || 4;
      return `<div style="width:64px;height:64px;flex-shrink:0;border-radius:8px;border:1px solid var(--border,#2a2f3d);background-color:#0b1120;
        background-image:url('${esc(url)}');background-repeat:no-repeat;image-rendering:pixelated;
        background-size:${Math.round(64 * fr * (fw / fh))}px 64px;background-position:0 0"></div>`;
    }
    if (a.sprite_path) return `<img src="${esc(a.sprite_path)}" alt="" style="width:64px;height:64px;object-fit:contain;image-rendering:pixelated;border-radius:8px;border:1px solid var(--border,#2a2f3d);background:#0b1120;flex-shrink:0" onerror="this.style.opacity=.2">`;
    return `<div style="width:64px;height:64px;flex-shrink:0;border-radius:8px;border:1px solid var(--border,#2a2f3d);background:${esc(a.color || '#31405c')};display:flex;align-items:center;justify-content:center;font-size:1.6rem;color:#0b1120;font-weight:800">${esc((a.name || '?')[0])}</div>`;
  }
  const _NODE_SKILL = { woodcut: 'woodcutting', mine: 'mining', farm: 'farming', fish: 'fishing', build: 'construction', hunt: 'hunting' };
  function _equipped(a) {
    const meta = _meta().skills, out = [];
    const sk = _NODE_SKILL[a.location] || (a.state === 'studying' ? 'knowledge' : null);
    if (sk) { const m = meta.find(x => x.key === sk); if (m) out.push([m.emoji, `${sk} tools (in use)`]); }
    if (a.location === 'defense' || ((_WST().raid || {}).phase === 'raid' && a.role === 'fight'))
      out.push(['⚔️', 'weapon drawn'], ['🛡️', 'guarding the walls']);
    if (a.state === 'working') out.push(['💻', 'work terminal']);
    return out;
  }
  function _renderAgentTop() {
    const el = document.getElementById('whud-agent-top');
    if (!el) return;
    const a = ((_WST()).agents || []).find(x => x.id === _selectedId);
    if (!a) { el.innerHTML = ''; return; }
    const eq = _equipped(a);
    el.innerHTML = `<div style="display:flex;gap:10px;margin-bottom:8px">
      ${_portraitHtml(a)}
      <div style="flex:1;min-width:0">
        <div style="font-size:.68rem;color:#7a86a0;text-transform:uppercase;letter-spacing:.04em;margin-bottom:2px">⚔️ Equipped</div>
        ${eq.length ? eq.map(([e, t]) => `<span class="whud-pill" style="margin:0 3px 3px 0" title="${esc(t)}">${e}</span>`).join('')
                    : '<span style="font-size:.7rem;color:#54607a">travelling light</span>'}
        <div style="font-size:.6rem;color:#54607a;margin-top:3px">implied by their current task — no persistent gear yet</div>
      </div></div>`;
  }

  /* ═══ 📜 QUESTS — mandate + prayers + research/construction as the active log,
         achievements + finished prayers as the completed log ═══ */
  async function _renderQuests() {
    const el = document.getElementById('whud-quests-body');
    if (!el) return;
    const now = Date.now();
    if (!_questsData || now - _questsAt > 30000) {
      _questsAt = now;
      try { _questsData = await api('/api/world/ops/workboard'); } catch { _questsData = _questsData || {}; }
      if (!document.getElementById('whud-quests-body')) return;
    }
    const st = _WST(), d = _questsData || {};
    const q = [];
    const plan = (d.republic || {}).current_plan;
    if (plan) q.push(['🏛️', plan.title, plan.why || 'the Republic’s current mandate']);
    const dir = ((st.governance || {}).directive);
    if (dir) q.push(['📌', dir.text, 'active town directive']);
    const res = ((st.company || {}).research || {});
    const act = (res.projects || []).find(p => p.status === 'active');
    if (act) q.push([act.icon || '🔬', `Research: ${act.name}`, `${act.pct || 0}% — ${act.effect || ''}`]);
    for (const c of (((st.company || {}).construction || {}).projects || []).slice(0, 3))
      q.push(['🏗️', `Build the ${c.name || c.kind}`, `materials ${c.mat_pct || 0}% · work ${c.pct || 0}%`]);
    const pending = d.pending || [];
    const qRows = q.map(([i, t, s]) => `<div style="padding:3px 0;font-size:.74rem">
      <div style="color:#e8eefc">${i} ${esc(t)}</div>
      ${s ? `<div style="font-size:.64rem;color:#7a86a0;margin-left:20px">${esc(s)}</div>` : ''}</div>`).join('');
    const pRows = pending.slice(0, 6).map(p => `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;padding:3px 0;font-size:.72rem">
        <span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#fcd34d" title="${esc(p.title)}">🙏 ${esc(p.title)}${p.cost_cents ? ` · $${(p.cost_cents / 100).toFixed(2)}` : ''}</span>
        <span style="display:flex;gap:4px;flex-shrink:0">
          <button class="whud-btn" style="padding:1px 7px;font-size:.66rem;background:#1f4a32;border-color:#2a5a3a" onclick="WHUD.bless(${p.id})">✓</button>
          <button class="whud-btn" style="padding:1px 7px;font-size:.66rem;border-color:#5a2a2a" onclick="WHUD.deny(${p.id})">✕</button></span>
      </div>`).join('');
    const doneP = (d.done || []).slice(0, 5).map(p => `<div style="font-size:.7rem;padding:2px 0;color:#8a97ad">
      ${{ done: '✅', approved: '✅', failed: '❌', rejected: '🚫' }[p.status] || '•'} ${esc(p.title)}</div>`).join('');
    const ach = (st.achievements || []).slice(0, 8).map(x =>
      `<div style="font-size:.7rem;padding:2px 0;color:#9fe0b0" title="${esc(x.label)}">🏆 ${esc(x.label)}</div>`).join('');
    el.innerHTML = `
      <div style="font-size:.68rem;color:#7a86a0;text-transform:uppercase;letter-spacing:.04em">Active quests</div>
      ${qRows || '<div style="font-size:.72rem;color:#54607a;padding:3px 0">No mandate — convene the Republic in the God Console.</div>'}
      ${pending.length ? `<div style="font-size:.68rem;color:#7a86a0;text-transform:uppercase;letter-spacing:.04em;margin-top:8px">Awaiting your blessing (${pending.length})</div>${pRows}` : ''}
      <div style="font-size:.68rem;color:#7a86a0;text-transform:uppercase;letter-spacing:.04em;margin-top:8px">Completed</div>
      ${(doneP + ach) || '<div style="font-size:.72rem;color:#54607a;padding:3px 0">Nothing finished yet.</div>'}
      <div style="margin-top:8px"><button class="whud-btn" style="width:100%" onclick="worldConsole('workboard')">🗂️ Full workboard</button></div>`;
  }
  async function bless(id) {
    try { await api(`/api/world/ops/prayers/${id}/approve`, { method: 'POST', body: JSON.stringify({}) }); toast?.('🙏 Blessed'); }
    catch (e) { toast?.(e?.message || 'Failed'); }
    _questsAt = 0; _renderQuests();
  }
  async function deny(id) {
    const comment = prompt('Reason (optional):') || '';
    try { await api(`/api/world/ops/prayers/${id}/reject`, { method: 'POST', body: JSON.stringify({ comment }) }); toast?.('Denied'); }
    catch (e) { toast?.(e?.message || 'Failed'); }
    _questsAt = 0; _renderQuests();
  }

  /* ═══ 🏢 COMPANY — real-money strip (ops/summary) above the town hall the
         existing _renderTownHall keeps painting into #world-townhall ═══ */
  async function _renderOps() {
    const el = document.getElementById('whud-ops-strip');
    if (!el) return;
    const now = Date.now();
    if (!_opsData || now - _opsAt > 60000) {
      _opsAt = now;
      try { _opsData = await api('/api/world/ops/summary'); } catch { /* keep last */ }
      if (!document.getElementById('whud-ops-strip')) return;
    }
    const s = _opsData;
    if (!s) { el.innerHTML = ''; return; }
    const $ = c => '$' + ((c || 0) / 100).toFixed(2);
    const owed = s.owed_cents || 0;
    el.innerHTML = `<div style="display:flex;gap:5px;flex-wrap:wrap;margin-bottom:8px">
      <span class="whud-pill" title="${owed ? 'Outstanding real-money bill the agents owe' : 'Treasury balance — banked real earnings'}" style="color:${owed ? '#f87171' : '#6ee7a8'}">${owed ? '💸 owe ' + $(owed) : '💰 ' + $(s.balance_cents)}</span>
      <span class="whud-pill" title="Real spend this billing cycle vs. the monthly cap">🧾 ${$(s.cycle_spend_cents)} / ${$(s.cap_cents)}</span>
      <span class="whud-pill" title="Automation mode — review-all or auto under budget">${s.mode === 'review' ? '🙏 review all' : '🤖 auto ≤budget'}</span>
      ${s.pending_prayers ? `<span class="whud-pill" style="color:#fcd34d;cursor:pointer" onclick="worldConsole('god')" title="Prayers waiting in the God Console">🙏 ${s.pending_prayers} waiting</span>` : ''}
    </div>`;
  }

  return { init, toggle, open, close, front, onState, skill, bless, deny };
})();

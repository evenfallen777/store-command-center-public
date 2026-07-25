/* ══ THE COMPANY — UI side panels (agent detail / character sheet, town hall, feed) ══
   Split out of tab-world.js for modularity. Runs in shared global scope
   (classic script, not a module) — same as world-map.js / world-assets.js. */

/* ── SIDE PANELS ── */
function _renderDetail() {
  const el = document.getElementById('world-detail');
  if (!el) return;
  const a = (_worldState?.agents || []).find(x => x.id === _selectedId);
  if (!a) { el.innerHTML = 'Select a character to see their stats.'; return; }
  const dept = (_worldState.departments || []).find(d => d.key === a.dept);
  const nextXp = Math.ceil(Math.pow((a.level) / 3, 2) * 120);
  const pct = Math.max(4, Math.min(100, Math.round((a.xp / Math.max(1, nextXp)) * 100)));
  const econ = _worldState.economy || { upgrades: [], item_cost: 30 };
  const mult = a.earn_mult || 1;
  let owned = []; try { owned = JSON.parse(a.upgrades || '[]'); } catch {}
  const shop = (econ.upgrades || []).map(u => {
    if (owned.includes(u.id))
      return `<div style="display:flex;justify-content:space-between;padding:4px 0;color:#6ee7a8;font-size:.76rem"><span>✓ ${esc(u.label)}</span><span style="color:#54607a">${esc(u.desc)}</span></div>`;
    const afford = (a.coins || 0) >= u.cost;
    return `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;font-size:.76rem">
      <span style="color:#c7d2e5">${esc(u.label)} <span style="color:#7a86a0">· ${esc(u.desc)}</span></span>
      <button class="btn" ${afford ? '' : 'disabled'} style="padding:3px 8px;font-size:.72rem;opacity:${afford ? 1 : .45}" onclick="worldBuy(${a.id},'${u.id}')">${u.cost}🪙</button></div>`;
  }).join('');
  const canItem = (a.coins || 0) >= econ.item_cost;
  el.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
      <span style="width:14px;height:14px;border-radius:3px;background:${esc(a.color||'#8ab')};display:inline-block"></span>
      <span style="font-weight:700;font-size:1rem;color:#e8eefc">${esc(a.name)}</span>
      <span class="pill">L${a.level}</span>
    </div>
    <div style="margin-bottom:4px"><b>${esc(dept?dept.label:a.dept)}</b> · <span style="text-transform:capitalize">${esc(a.state)}</span>
      ${a.kind==='openclaw'?' · <span style="color:#f472b6">real agent</span>':''}
      ${a.blessed?' · <span style="color:#fcd34d" title="God blessed their work — +25% pay & XP for an hour">😇 blessed</span>':''}
      ${a.thriving?' · <span style="color:#6ee7a8" title="Every need in the green — +25% pay & XP">🌟 thriving</span>':''}</div>
    ${a.model_id?`<div style="margin-bottom:6px;font-size:.7rem;color:#7a86a0" title="LM Studio model backing this Oracle analyst">🔮 ${esc(a.model_id)}</div>`:''}
    ${a.output_pct != null && a.output_pct < 100 ? `<div style="font-size:.7rem;color:#f0a860;margin-bottom:4px" title="Doing the same thing too long — output decays until they switch it up">🥱 ${a.streak_min}m of nonstop ${esc(a.state)} · output ${a.output_pct}%</div>` : ''}
    <div style="display:flex;align-items:center;gap:8px;margin:6px 0;padding:6px 8px;background:#0e1626;border-radius:8px">
      <span style="font-size:1.3rem">${esc(a.mood_emoji||'🙂')}</span>
      <div><div style="color:#e8eefc;font-size:.82rem;text-transform:capitalize">${esc(a.mood_label||'—')}</div>
      ${a.goal?`<div style="color:#7a86a0;font-size:.7rem">🚶 ${esc(a.goal)}</div>`:''}</div>
    </div>
    <div style="display:flex;gap:12px;margin:8px 0;align-items:center;flex-wrap:wrap">
      <span style="font-size:1.05rem;color:#fcd34d;font-weight:700">🪙 ${a.coins||0}</span>
      ${(a.debt||0)>0?`<span style="font-size:.76rem;color:#f87171">💸 owes ${a.debt}</span>`:''}
      ${mult>1?`<span style="font-size:.74rem;color:#6ee7a8">▲ ${Math.round((mult-1)*100)}%</span>`:''}
      <span style="font-size:.7rem;color:#54607a">earned ${a.coins_earned||0}</span>
    </div>
    <div style="margin:6px 0 4px">
      ${_needBar('Energy', a.energy)}${_needBar('Food', a.hunger)}${_needBar('Fun', a.fun)}${_needBar('Social', a.social)}${_needBar('Purpose', a.fulfillment)}
    </div>
    ${_moodBlock(a)}
    <div style="margin:6px 0 4px;font-size:.78rem;color:#aeb9cc">L${a.level} · XP ${a.xp} · Jobs done <b>${a.jobs_done||0}</b></div>
    <div style="height:7px;background:#0b1120;border-radius:4px;overflow:hidden"><div style="height:100%;width:${pct}%;background:${esc(a.color||'#7dd3fc')}"></div></div>
    ${a.beat && a.beat.system ? `<div style="margin-top:8px;font-size:.72rem;color:#8a97ad">🛡️ Security beat: <b style="color:#c7d2e5">${esc(a.beat.label)}</b> ${a.beat.tasks > 0 ? `· <span style="color:#f0a860">${a.beat.tasks} debug task(s)</span>` : `· <span style="color:#6ee7a8">all clear</span>`}</div>` : ''}
    ${a.key === 'player_god' ? _playerActions(a) : ''}
    ${_skillBlock(a)}
    ${_inventoryBlock(a)}
    ${a.mood?`<div style="margin-top:10px;padding:8px;background:#0e1626;border-radius:8px;color:#dfe7f5;font-style:italic">💭 ${esc(a.mood)}</div>`:''}
    <div style="display:flex;gap:6px;margin-top:12px;flex-wrap:wrap">
      <button class="btn" style="padding:6px 10px;font-size:.76rem" onclick="worldSheet(${a.id})">📋 Full sheet</button>
      <button class="btn" style="padding:6px 10px;font-size:.76rem" onclick="worldRename(${a.id})">✏️ Rename</button>
      <button class="btn" style="padding:6px 10px;font-size:.76rem" onclick="worldThink(${a.id})">💭 Think</button>
      <button class="btn" style="padding:6px 10px;font-size:.76rem" onclick="worldLog(${a.id})">📔 Journal</button>
      <button class="btn" ${canItem?'':'disabled'} style="padding:6px 10px;font-size:.76rem;opacity:${canItem?1:.45}" onclick="worldWant(${a.id})">🎁 Item (${econ.item_cost}🪙)</button>
      <button class="btn" ${(a.coins||0)>=80?'':'disabled'} style="padding:6px 10px;font-size:.76rem;opacity:${(a.coins||0)>=80?1:.45}" title="Commission a unique generated sprite for this agent (their coins; 1/day)" onclick="worldMakeover(${a.id})">🎨 New look (80🪙)</button>
    </div>
    <div style="margin-top:12px;border-top:1px solid #26324a;padding-top:8px">
      <div style="font-size:.78rem;font-weight:600;color:#e8eefc;margin-bottom:2px">🛒 Upgrade shop</div>
      ${shop || '<div style="font-size:.74rem;color:#54607a">No upgrades available.</div>'}
    </div>`;
}

/* ── YOUR AVATAR: every action an agent can take, as buttons ────────────────
   Sends the same RCT-style assignments the sim honours (posted_to), so your
   character walks there and does the real activity — gathering XP, filling the
   stockpile, restoring needs — exactly like any citizen. ⏹ returns free will. */
function _playerActions(a) {
  const acts = [
    ['⛏️ Mine', 'mine', 'skill'], ['🪓 Chop', 'woodcut', 'skill'], ['🎣 Fish', 'fish', 'skill'],
    ['🌾 Farm', 'farm', 'skill'], ['🔨 Build', 'build', 'skill'], ['🏹 Hunt', 'hunt', 'skill'],
    ['📖 Study', 'library', 'spot'], ['⛪ Pray', 'church', 'spot'],
    ['🍺 Bar', 'bar', 'spot'], ['🕹️ Arcade', 'arcade', 'spot'], ['☕ Café', 'cafe', 'spot'],
    ['🛍️ Shop', 'shop', 'spot'], ['🏠 Home', 'home', 'spot'],
  ];
  const btns = acts.map(([lbl, loc, kind]) =>
    `<button class="btn" style="padding:4px 8px;font-size:.7rem" onclick="playerGo(${a.id},'${loc}','${kind}')">${lbl}</button>`).join('');
  return `<div style="margin-top:10px;border-top:1px solid #ffd70044;padding-top:8px">
    <div style="font-size:.78rem;font-weight:600;color:#ffd700;margin-bottom:4px">🎮 Your actions</div>
    <div style="display:flex;gap:4px;flex-wrap:wrap">${btns}
      <button class="btn" style="padding:4px 8px;font-size:.7rem;border-color:#6d5aff" onclick="playerGo(${a.id},null,'release')">⏹ Free will</button></div>
    <div style="font-size:.62rem;color:#8a97ad;margin-top:4px">You walk there and do it for real — XP, resources, needs, all of it.</div>
  </div>`;
}
async function playerGo(id, loc, kind) {
  try {
    let body;
    if (kind === 'release') body = { location: null };
    else if (kind === 'skill') { const n = (WM.nodes || []).find(n => n.kind === loc); body = { location: loc, kind: 'skill', col: n?.col, row: n?.row, minutes: 45 }; }
    else {
      let t = WM.locations[loc];
      if (loc === 'shop') { const shops = (WM.buildings || []).filter(b => b.kind === 'shop'); const b = shops[0]; if (b) t = { col: b.c + (b.w / 2 | 0), row: b.r + (b.h / 2 | 0) }; }
      if (loc === 'home') { const hi = _houseByKey['player_god']; t = WM.houseSlots[hi] || t; }
      body = { location: 'spot', kind: 'spot', col: t?.col, row: t?.row, minutes: 45 };
    }
    await api(`/api/world/agent/${id}/assign`, { method: 'POST', body: JSON.stringify(body) });
    toast?.(kind === 'release' ? 'Back to free will' : 'On my way'); _pollWorld();
  } catch (e) { toast?.(e.message); }
}
window.playerGo = playerGo;

async function worldMakeover(id) {
  try {
    const r = await api(`/api/world/agent/${id}/makeover`, { method: 'POST', body: '{}' });
    toast?.(r.ok ? '🎨 The studio is painting their new look…' : (r.msg || 'not right now'));
  } catch (e) { toast?.(e.message); }
}
window.worldMakeover = worldMakeover;

/* what the agent is carrying + what they've placed at home (item economy) */
function _inventoryBlock(a) {
  const inv = a.inventory || [];
  const placed = (_worldState?.placements || []).filter(p => p.agent_key === a.key);
  if (!inv.length && !placed.length) return '';
  const items = inv.map(i =>
    `<span class="pill" style="padding:2px 7px;font-size:.72rem" title="${esc(i.name)} (${i.size})">${i.emoji}${i.qty > 1 ? '×' + i.qty : ''}</span>`).join(' ');
  const home = placed.map(p =>
    `<span title="${esc(p.item)} (${p.spot}, ${p.size})" style="font-size:.85rem">${p.emoji}</span>`).join(' ');
  return `<div style="margin-top:10px;border-top:1px solid #26324a;padding-top:8px">
    <div style="font-size:.78rem;font-weight:600;color:#e8eefc;margin-bottom:4px">🎒 Inventory</div>
    ${items || '<span style="font-size:.7rem;color:#54607a">pockets empty</span>'}
    ${home ? `<div style="font-size:.7rem;color:#8a97ad;margin-top:5px">🏠 at home: ${home}</div>` : ''}
  </div>`;
}

const _SKILL_EMOJI = { woodcutting: '🪓', mining: '⛏️', farming: '🌾', fishing: '🎣', construction: '🔨', attack: '⚔️', defense: '🛡️', knowledge: '📖' };
function _skillBlock(a) {
  const skills = a.skills || {}, prim = a.primary_skill;
  const keys = Object.keys(_SKILL_EMOJI).filter(k => skills[k] || k === prim);
  if (!keys.length) return '';
  const rows = keys.map(k => {
    const s = skills[k] || { xp: 0, level: 1 }, isP = k === prim;
    return `<div style="display:flex;align-items:center;gap:6px;font-size:.7rem;margin:2px 0">
      <span style="width:96px;color:${isP ? '#e8eefc' : '#8a97ad'};text-transform:capitalize">${_SKILL_EMOJI[k]} ${esc(k)}${isP ? ' ★' : ''}</span>
      <span class="pill" style="padding:1px 6px">L${s.level}</span>
      <span style="color:#54607a">${s.xp} xp</span></div>`;
  }).join('');
  const meta = `<div style="font-size:.68rem;color:#8a97ad;margin-bottom:4px">🧭 competence <b style="color:#c7d2e5">${(a.competence || 1).toFixed(2)}</b>${a.prefers ? ` · learned to prefer <b style="color:#9fe0b0">${esc(a.prefers)}</b>` : ''}
    ${a.style ? `<br>🧬 <b style="color:#c7d2e5">${esc(a.style.label)}</b> <span title="Their personal strategy — self-tweaked from results every ~6h">· explore ${a.style.epsilon} · focus ${a.style.focus} · spend ${a.style.spend}</span>` : ''}</div>`;
  return `<div style="margin-top:10px;border-top:1px solid #26324a;padding-top:8px">
    <div style="font-size:.78rem;font-weight:600;color:#e8eefc;margin-bottom:4px">🎯 Skills</div>${meta}${rows}</div>`;
}

/* 🏆 the LEADERBOARD — reigning champion per category, podium on hover */
function _rankBlock(company) {
  const ranks = (company && company.rankings) || [];
  if (!ranks.length) return '';
  const rows = ranks.map(cat => {
    const podium = cat.top.map((t, i) => `${['🥇', '🥈', '🥉'][i]} ${t.name} (${t.value})`).join('  ');
    const c = cat.top[0];
    return `<div style="display:flex;justify-content:space-between;font-size:.7rem;margin:2px 0" title="${esc(podium)}">
      <span style="color:#8a97ad">${cat.emoji} ${esc(cat.label)}</span>
      <span style="color:#e8eefc"><b>${esc(c.name)}</b> <span style="color:#54607a">${c.value}</span></span></div>`;
  }).join('');
  return `<div style="background:#0e1626;border-radius:8px;padding:8px;margin-bottom:8px">
    <div style="font-size:.68rem;color:#7a86a0;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">🏆 Leaderboard <span style="text-transform:none;color:#54607a">· hover for podium</span></div>
    ${rows}
    <div style="font-size:.66rem;color:#54607a;margin-top:6px">Forecaster rankings live on the 🔮 Oracle tab.</div></div>`;
}

function _moodBlock(a) {
  if (a.mood_value == null) return '';
  const m = a.mood_value, col = m < 35 ? '#ef4444' : (m < 55 ? '#f0b45a' : '#6ee7a8');
  const ths = (a.thoughts || []).slice(0, 6).map(t =>
    `<div style="display:flex;justify-content:space-between;font-size:.68rem;margin:1px 0"><span style="color:#aeb9cc">${esc(t.label)}</span><span style="color:${t.delta >= 0 ? '#6ee7a8' : '#f08a8a'};font-weight:700">${t.delta >= 0 ? '+' : ''}${t.delta}</span></div>`).join('');
  return `<div style="margin-top:8px">
    <div style="display:flex;align-items:center;gap:6px;font-size:.72rem">
      <span style="width:56px;color:#8a97ad">🧠 Mood</span>
      <div style="flex:1;height:7px;background:#0b1120;border-radius:4px;overflow:hidden"><div style="height:100%;width:${m}%;background:${col}"></div></div>
      <span style="width:26px;text-align:right;color:${col};font-weight:700">${m}</span></div>
    ${a.broken ? `<div style="font-size:.7rem;color:#f0a860;margin-top:3px;font-weight:600">⚠ ${esc(a.mood_label || 'having a breakdown')}</div>` : ''}
    ${ths ? `<div style="margin-top:4px;padding:6px 8px;background:#0e1626;border-radius:6px">${ths}</div>` : ''}</div>`;
}

function _needBar(label, val) {
  const v = Math.round(val || 0);
  const base = { Energy: '#34d399', Fun: '#f472b6', Social: '#60a5fa', Purpose: '#fbbf24', Food: '#fb923c' }[label] || '#7dd3fc';
  const col = v < 30 ? '#ef4444' : base;
  return `<div style="display:flex;align-items:center;gap:6px;margin:2px 0;font-size:.68rem">
    <span style="width:56px;color:#8a97ad">${label}</span>
    <div style="flex:1;height:6px;background:#0b1120;border-radius:3px;overflow:hidden"><div style="height:100%;width:${v}%;background:${col}"></div></div>
    <span style="width:22px;text-align:right;color:#6b7688">${v}</span></div>`;
}

function _renderTownHall(st) {
  const el = document.getElementById('world-townhall');
  if (!el) return;
  const g = st.governance || {};
  const dir = g.directive;
  const sugg = (g.suggestions || []).slice(0, 4).map(s =>
    `<div style="font-size:.74rem;color:#c7d2e5;padding:2px 0">💡 <b style="color:#9fb4d6">${esc((s.category||'idea'))}</b> — ${esc(s.text)}</div>`).join('');
  const achs = (st.achievements || []);
  const achStrip = achs.slice(0, 6).map(a =>
    `<span title="${esc(a.label)}" style="font-size:1rem">${(a.label.match(/\p{Emoji}/u)||['🏆'])[0]}</span>`).join(' ');
  const raid = st.raid || {};
  el.innerHTML = `
    ${(raid.phase === 'raid' || raid.phase === 'recovery') ? _raidHUD(raid) : ''}
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
      <div style="font-weight:600;font-size:.85rem">🏛️ Town Hall</div>
      <div style="display:flex;gap:5px">
        <button class="btn" style="padding:4px 8px;font-size:.7rem" onclick="worldOpinion()">💡 Idea</button>
        <button class="btn" style="padding:4px 8px;font-size:.7rem" onclick="worldMeeting()">🗳️ Meeting</button>
        <button class="btn" style="padding:4px 8px;font-size:.7rem;border-color:#7c3a3a" onclick="worldRaidDrill()" title="Run a defense drill">🛡️ Drill</button>
      </div>
    </div>
    <div style="background:#0e1626;border-radius:8px;padding:8px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div style="font-size:.68rem;color:#7a86a0;text-transform:uppercase;letter-spacing:.04em">📌 Active directive</div>
        ${dir ? `<button class="btn" style="padding:2px 7px;font-size:.66rem" onclick="worldResolveDirective(${dir.id})">✓ done</button>` : ''}
      </div>
      <div style="font-size:.82rem;color:#fcd34d;margin-top:2px">${dir ? esc(dir.text) : '<span style=\"color:#54607a\">No mandate yet — hold a meeting.</span>'}</div>
    </div>
    ${achs.length ? `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
      <span style="font-size:.7rem;color:#7a86a0">🏆 ${achs.length} unlocked</span><span>${achStrip}</span></div>` : ''}
    ${_techBlock(st.company)}
    ${_rankBlock(st.company)}
    ${_intelBlock(st.company)}
    ${_securityBlock(st.security)}
    <div style="font-size:.7rem;color:#7a86a0;margin-bottom:2px">Open suggestions</div>
    ${sugg || '<div style="font-size:.72rem;color:#54607a">The crew is still thinking…</div>'}`;
}

function _techBlock(company) {
  const t = company && company.tech; if (!t) return '';
  const ladder = (t.ladder || []).map(x =>
    `<span title="${esc(x.name)}${x.unlocked ? ' (unlocked)' : ''}" style="font-size:.9rem;opacity:${x.unlocked ? 1 : 0.3}">${x.emoji}</span>`).join('<span style="color:#3a4a5a">›</span>');
  let next = '';
  if (t.next) {
    const n = t.next, mats = Object.keys(n.cost || {}).map(r => {
      const have = (n.have || {})[r] || 0, ok = have >= n.cost[r];
      return `<span style="color:${ok ? '#6ee7a8' : '#e0a060'}">${have}/${n.cost[r]} ${esc(r)}</span>`;
    }).join(' · ');
    next = `<div style="margin-top:5px;font-size:.66rem;color:#8a97ad">Next: ${n.emoji} ${esc(n.name)} ${n.ready ? '<b style="color:#6ee7a8">READY</b>' : ''}</div>
      <div style="height:5px;background:#0b1120;border-radius:3px;overflow:hidden;margin:3px 0"><div style="height:100%;width:${n.rp_pct}%;background:#7aa8e8"></div></div>
      <div style="font-size:.62rem;color:#7a86a0">🔬 research ${t.research_points} / ${n.rp} · 🧱 ${mats || 'no materials'}</div>`;
  } else {
    next = `<div style="margin-top:5px;font-size:.66rem;color:#6ee7a8">Max tier reached — full steel works.</div>`;
  }
  return `<div style="background:#0e1626;border-radius:8px;padding:8px;margin-bottom:8px">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <span style="font-size:.72rem;color:#7a86a0">⚒️ Tech age</span>
      <span style="font-weight:700;color:#d8c090">${t.emoji} ${esc(t.tier_name)} ${t.bonus > 1 ? `<span style="font-size:.62rem;color:#6ee7a8">+${Math.round((t.bonus - 1) * 100)}%</span>` : ''}</span></div>
    <div style="margin-top:4px">${ladder}</div>${next}</div>`;
}

const _SPEC_EMOJI = { woodcutting: '🪓', mining: '⛏️', farming: '🌾', fishing: '🎣', construction: '🔨', attack: '⚔️', defense: '🛡️', knowledge: '📖' };
function _intelBlock(company) {
  if (!company) return '';
  const spec = company.specialists || {};
  const chips = Object.entries(spec).slice(0, 8).map(([sk, v]) =>
    `<span title="${esc(sk)}: ${esc(v.name)} · L${v.level}" style="font-size:.68rem;background:#0b1120;border-radius:6px;padding:1px 6px">${_SPEC_EMOJI[sk] || '•'} ${esc(v.name)}</span>`).join(' ');
  return `<div style="background:#0e1626;border-radius:8px;padding:8px;margin-bottom:8px">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <span style="font-size:.72rem;color:#7a86a0">🧠 Collective intelligence</span>
      <span style="font-weight:700;color:#9fe0b0">${company.intelligence || 0}</span></div>
    ${chips ? `<div style="margin-top:5px;display:flex;flex-wrap:wrap;gap:4px">${chips}</div>` : ''}</div>`;
}

function _securityBlock(security) {
  if (!security || !security.systems) return '';
  const sys = security.systems, dot = { ok: '#6ee7a8', warn: '#f0b45a', critical: '#ef4444' };
  const chips = Object.values(sys).map(v =>
    `<span title="${esc(v.label)}: ${v.issues} issue(s), ${v.recent} recent" style="font-size:.66rem;color:#9fb4d6"><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${dot[v.health] || '#6ee7a8'};vertical-align:middle"></span> ${esc(v.label)}${v.issues ? ` <b style="color:#f0a860">${v.issues}</b>` : ''}</span>`).join('  ');
  const bad = Object.values(sys).filter(v => v.issues > 0).length;
  // the REAL Command Center: audit grade + shield (defenses on) + live attackers
  const p = security.posture || {};
  const gc = { A: '#6ee7a8', B: '#a3e635', C: '#fbbf24', D: '#fb923c' }[p.grade] || '#ef4444';
  const shield = p.shield != null ? Math.round(p.shield * 100) : null;
  const real = p.grade ? `
    <div style="display:flex;gap:8px;align-items:center;margin-top:6px" title="The town's shield IS the real security stack — Network Security tab. Weak defenses = weak raid walls.">
      <span style="font-size:.78rem;font-weight:800;color:${gc};border:1px solid ${gc};border-radius:5px;padding:0 6px" title="hardening-audit grade">${p.grade}</span>
      <div style="flex:1"><div style="display:flex;justify-content:space-between;font-size:.6rem;color:#7a86a0"><span>🛡️ town shield (real defenses ${p.on}/${p.total})</span><span style="color:${shield > 70 ? '#6ee7a8' : '#f0b45a'}">${shield}%</span></div>
        <div style="height:5px;background:#0b1120;border-radius:3px;overflow:hidden;margin-top:2px"><div style="height:100%;width:${shield}%;background:linear-gradient(90deg,#3b82f6,#7dd3fc)"></div></div></div>
      ${p.attackers ? `<span style="font-size:.66rem;color:#f0908a" title="live attackers seen by the Command Center">⚔️ ${p.attackers}</span>` : ''}
    </div>
    ${(p.warn || []).length ? `<div style="font-size:.6rem;color:#f0b45a;margin-top:3px">⚠ weak: ${p.warn.slice(0, 3).map(esc).join(' · ')}</div>` : ''}` : '';
  return `<div style="background:#0e1626;border-radius:8px;padding:8px;margin-bottom:8px">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <span style="font-size:.72rem;color:#7a86a0">🛡️ Security desk</span>
      <span style="font-size:.64rem;color:${bad ? '#f0a860' : '#6ee7a8'}">${bad ? bad + ' need attention' : 'all systems green'}</span></div>
    ${real}
    <div style="margin-top:5px;display:flex;flex-wrap:wrap;gap:8px">${chips}</div></div>`;
}

function _raidHUD(raid) {
  const threats = raid.threats || [], active = threats.filter(t => t.status === 'active');
  const handled = threats.length - active.length;
  const ic = { domain: '🌐', finding: '🐛', drill: '🎯', attacker: '🏴‍☠️' };
  const rows = active.slice(0, 6).map(t => {
    const hp = Math.max(0, Math.round((t.hp / Math.max(1, t.max_hp)) * 100));
    return `<div style="font-size:.72rem;margin:3px 0">
      <div style="display:flex;justify-content:space-between"><span>${ic[t.kind] || '⚔️'} ${esc(t.label)}</span><span style="color:#f8b4b4">${hp}%</span></div>
      <div style="height:4px;background:#3a1015;border-radius:2px;overflow:hidden;margin-top:1px"><div style="height:100%;width:${hp}%;background:#e0483b"></div></div></div>`;
  }).join('');
  const banner = raid.phase === 'raid'
    ? `⚔️ RAID · Wave ${raid.wave || 1} — ${active.length} enemy(s)`
    : `🩹 RECOVERY — field cleared (${handled} handled)`;
  const wl = raid.walls || {};
  const wallRow = Object.keys(wl).length ? `<div style="display:flex;gap:6px;margin:5px 0 2px">${['N', 'E', 'S', 'W'].filter(s => wl[s]).map(s => {
    const f = Math.max(0, Math.round((wl[s].hp / (wl[s].max_hp || 120)) * 100));
    return `<span style="flex:1;font-size:.62rem;color:#9fb4d6">🧱${s} <b style="color:${f > 40 ? '#8fd0ff' : '#e0a040'}">${f}%</b></span>`;
  }).join('')}</div>` : '';
  // combat depth (#8): cover from walls + wounded/medic status
  const cover = raid.cover != null ? raid.cover : null;
  const statusRow = (cover != null || raid.downed) ? `<div style="display:flex;gap:10px;align-items:center;font-size:.64rem;margin:4px 0 2px;color:#c7d2e5">
      ${cover != null ? `<span title="intact walls absorb up to 75% of breach damage">🛡️ Cover <b style="color:${cover > 40 ? '#8fd0ff' : '#e0a040'}">${cover}%</b></span>` : ''}
      ${raid.downed ? `<span style="color:#f0908a" title="downed defenders — medics are reviving them">🩸 ${raid.downed} down <span style="color:#8a97ad">· ⛑️ medics tending</span></span>` : '<span style="color:#7bbf8a">✓ no wounded</span>'}
    </div>` : '';
  // combat v3: drilled readiness, live turrets, and who's racking up the kills
  const v3Row = `<div style="display:flex;gap:10px;align-items:center;font-size:.64rem;margin:2px 0;color:#c7d2e5">
      ${raid.readiness != null ? `<span title="Drill grade — buffs walls & fighters in real raids">🎖️ Readiness <b style="color:${raid.readiness >= 70 ? '#7bbf8a' : raid.readiness >= 45 ? '#e0c060' : '#e0a040'}">${raid.readiness}%</b></span>` : ''}
      ${raid.towers ? `<span title="Built watchtowers fire on the horde">🏹 ${raid.towers} tower${raid.towers > 1 ? 's' : ''}</span>` : ''}
      ${(raid.kills && raid.kills.length) ? `<span title="Top slayers this raid">⚔️ ${raid.kills.map(k => `${esc(String(k[0]))} ${k[1]}`).join(' · ')}</span>` : ''}
    </div>`;
  const note = (!raid.autoblock && active.some(t => t.kind === 'domain'))
    ? `<div style="font-size:.63rem;color:#d0a98f;margin-top:6px">ℹ️ Auto-block is OFF — hostile domains are flagged, not denied. Enable <code>world_raid_autoblock</code> to have wins hit the Pi-hole denylist.</div>` : '';
  return `<div style="background:#2a0e12;border:1px solid #7c2a2a;border-radius:10px;padding:10px;margin-bottom:10px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
      <div style="font-weight:700;color:#ff9a8f;font-size:.8rem">${banner}</div>
      ${raid.phase === 'raid' ? '<button class="btn" style="padding:2px 7px;font-size:.66rem" onclick="worldStandDown()">stand down</button>' : ''}
    </div>
    ${raid.verdict ? `<div style="font-size:.66rem;color:#c9d6ea;background:#12202e;border-left:2px solid #5fb0e8;border-radius:4px;padding:5px 7px;margin:6px 0"><b style="color:#8fd0ff">🧠 AI analysis:</b> ${esc(raid.verdict)}</div>` : ''}
    ${wallRow}${statusRow}${v3Row}${rows || '<div style="font-size:.72rem;color:#c99">No active threats.</div>'}${note}</div>`;
}

function _renderFeed(events) {
  const el = document.getElementById('world-feed');
  if (!el) return;
  const icon = { thought:'💭', want:'🎁', levelup:'⭐', job_start:'🟢', job_done:'✅',
                 system:'ℹ️', bill:'💸', opinion:'💡', meeting:'🏛️', move:'🚶', upgrade:'🛠️',
                 incident:'⚡', achievement:'🏆', vision:'👁️', raid:'⚔️', security:'🛡️',
                 tech:'🔬', build:'🏗️', season:'🍂', phase:'⚙️', break:'😤' };
  el.innerHTML = events.map(e =>
    `<div><span>${icon[e.kind]||'·'}</span> <span style="color:#c7d2e5">${esc(e.text)}</span>
     <span style="color:#54607a">${(e.created_at||'').slice(11,16)}</span></div>`).join('') || 'Quiet so far…';
}


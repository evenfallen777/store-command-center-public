'use strict';

/* ── STATS ── */
async function loadStats() {
  try {
    const s = await api('/api/stats');
    const map = {
      'badge-proposals': s.proposals_pending || 0,
      'badge-review':    s.review_count      || 0,
      'badge-approved':  s.approved_count    || 0,
      'badge-published': s.published_count   || 0,
    };
    for (const [id, val] of Object.entries(map)) {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    }
    // Etsy/Printify nav badge: show proposals pending (most urgent)
    const epBadge = document.getElementById('badge-ep');
    if (epBadge) {
      const tot = (s.proposals_pending || 0);
      epBadge.textContent = tot;
      epBadge.style.display = tot > 0 ? '' : 'none';
    }
    const ids = ['stat-proposals','stat-review','stat-approved','stat-live'];
    const vals = [s.proposals_pending||0, s.review_count||0, s.approved_count||0, s.published_count||0];
    ids.forEach((id,i) => { const el = document.getElementById(id); if (el) el.textContent = vals[i]; });
  } catch {}
}

/* Deep-link from a dashboard card/row into Finance → 📆 Bills. switchView paints
   Finance asynchronously, so poll briefly for the pane instead of guessing. */
function dashOpenBills() {
  switchView('finance');
  let tries = 0;
  const t = setInterval(() => {
    if (document.getElementById('fin-pane-bills')) {
      clearInterval(t);
      if (typeof financeSub === 'function') financeSub('bills');
    } else if (++tries > 40) clearInterval(t);
  }, 25);
}
window.dashOpenBills = dashOpenBills;

/* ══ DASHBOARD ══ */
async function renderDashboard() {
  // guard at the sink: whatever calls this late (stale timer, queued promise,
  // reconnect), it must never clobber another active view's main-content.
  if (typeof _currentView !== 'undefined' && _currentView !== 'dashboard') return;

  // FAST batch — everything here answers in well under half a second locally.
  // Slow sources (homelab docker probe ~1s, mail IMAP up to seconds, portal
  // hitting live WordPress) are deferred below and patched in after first paint.
  const rs = await Promise.allSettled([
    api('/api/stats'),                                    // 0
    api('/api/generations?status=generating'),            // 1
    api('/api/github/jobs'),                              // 2
    api('/api/queue'),                                    // 3
    api('/api/world/ops/summary'),                        // 4
    api('/api/money/stats'),                              // 5
    api('/api/world/ops/prayers?status=pending&limit=6'), // 6
    api('/api/money/missions?status=proposed&limit=4'),   // 7
    api('/api/jelly/status'),                             // 8
    api('/api/security/posture'),                         // 9
    api('/api/world/state'),                              // 10
  ]);
  const v = (i, fb) => (rs[i].status === 'fulfilled' && rs[i].value != null) ? rs[i].value : fb;
  const s       = v(0, {});
  const gens    = v(1, []);
  const jobs    = v(2, []);
  const queue   = v(3, null);
  const ops     = v(4, null);
  const money   = v(5, null);
  const prayers = (v(6, {}).prayers) || [];
  const propMis = (v(7, {}).missions) || [];
  const jelly   = v(8, null);
  const posture = v(9, null);
  const world   = v(10, null);

  // rollups
  const WORKING = ['planning','coding','reviewing','testing'];
  const NEEDS   = ['awaiting_input','awaiting_review','awaiting_system'];
  const jStat = (arr) => jobs.filter(j => arr.includes(j.status)).length;
  const jNeed = jStat(NEEDS), jWork = jStat(WORKING), jDone = jStat(['done']);

  const usd = c => '$' + ((c || 0) / 100).toFixed(2);
  const qCounts  = (queue && queue.counts) || {};
  const qRun     = qCounts.running || 0, qQd = qCounts.queued || 0;
  const qModel   = ((queue && queue.jobs) || []).map(j => j.model).find(m => m) || '';
  const prayPend = ops ? (ops.pending_prayers || 0) : prayers.length;
  const misProp  = money ? ((money.missions || {}).proposed || 0) : 0;
  const sigNew   = money ? ((money.signals  || {}).new      || 0) : 0;
  const company  = (world && world.company) || null;

  // stat card: optional one-line sub under the value; optional id on the value
  // so deferred fetches can patch it in place. `click` overrides the plain
  // switchView jump for cards that deep-link into a sub-pane.
  const stat = (icon,label,val,cls,view,sub,id,click) =>
    `<div class="stat-card" ${(click||view)?`style="cursor:pointer" onclick="${click||`switchView('${view}')`}"`:''}>
       <div class="stat-label">${icon} ${label}</div>
       <div class="stat-val ${cls||''}"${id?` id="${id}"`:''}>${val}</div>
       ${sub?`<div style="font-size:.64rem;color:var(--muted);margin-top:5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${sub}</div>`:''}</div>`;

  let h = `
    <div class="view-header">
      <div class="view-title">Dashboard</div>
      <div class="view-sub">The whole platform at a glance — money, world, queue, swarm, store, and services.</div>
    </div>
    <div class="stats-row" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));">
      ${stat('&#128176;','Treasury', ops ? usd(ops.balance_cents) : '—',
             ops && ops.owed_cents ? 'c-warn' : 'c-green', 'treasury',
             ops ? `owed ${usd(ops.owed_cents)} · cap ${usd(ops.cap_cents)}` : 'offline')}
      ${stat('&#9889;','Queue', queue ? `${qRun}&#9654; ${qQd}&#8987;` : '—',
             qRun ? 'c-warn' : 'c-accent', 'studio',
             qModel ? esc(qModel) : (queue && queue.paused ? 'paused' : 'idle'))}
      ${stat('&#128181;','Money missions', money ? misProp : '—', misProp?'c-warn':'c-accent2', 'money',
             money ? `${sigNew} new signals` : '')}
      ${stat('&#128198;','Bills due', '&#8230;', 'c-accent', '', '<span id="dash-bills-sub"></span>', 'dash-bills', 'dashOpenBills()')}
      ${stat('&#128236;','Mail unread', '&#8230;', 'c-accent', 'mail', '', 'dash-mail')}
      ${stat('&#129302;','Swarm — needs you', jNeed, jNeed?'c-warn':'c-accent', 'github',
             `${jWork} working · ${jDone} done`)}
      ${stat('&#127969;','The Company', company ? company.pop : '—', 'c-accent2', 'world',
             company ? `${(company.company_fund||0).toLocaleString()}&#129689; fund` : '')}
      ${stat('&#129689;','JellyCoin', jelly ? '#'+(jelly.height||0).toLocaleString() : '—', 'c-accent', 'crypto',
             jelly ? `${(jelly.supply||0).toLocaleString()} JLY · ${jelly.miners_online||0} miner${jelly.miners_online===1?'':'s'}` : '')}
      ${stat('&#128737;&#65039;','Security', posture ? `${esc(posture.grade||'?')} · ${posture.score??'—'}` : '—',
             posture && (posture.score||0) >= 80 ? 'c-green' : 'c-warn', 'network-security')}
      ${stat('&#128722;','Store products', '&#8230;', 'c-accent2', 'portal', '', 'dash-portal')}
      ${stat('&#128717;','POD live', s.published_count||0, 'c-accent2', 'published',
             `${s.review_count||0} review · ${s.approved_count||0} approved`)}
      ${stat('&#128268;','Services up', '&#8230;', 'c-green', 'homelab', '', 'dash-svc')}
    </div>`;

  /* ── ⏳ Awaiting you — everything gated on the owner, merged ── */
  const arow = (badge, color, title, sub, view) =>
    `<div class="gen-bar" style="cursor:pointer;margin-bottom:0;" onclick="switchView('${view}')">
       <div class="gen-bar-pulse" style="background:${color}"></div>
       <span style="font-size:.62rem;padding:1px 7px;border-radius:9px;background:var(--surface);color:var(--muted);flex-shrink:0;">${badge}</span>
       <div class="gen-bar-label">${esc(title)}</div><div class="gen-bar-model">${esc(sub||'')}</div></div>`;
  const rows = [];
  for (const p of prayers.slice(0,3))
    rows.push(arow('&#128591; prayer','var(--warn)', p.title||p.kind||'prayer',
      (p.agent_name?p.agent_name+' · ':'') + (p.cost_cents?usd(p.cost_cents):'free'), 'world'));
  for (const j of jobs.filter(x=>NEEDS.includes(x.status)).slice(0,2))
    rows.push(arow('&#129302; swarm','var(--warn)', j.title||'job', j.status, 'github'));
  for (const m of propMis.slice(0,2))
    rows.push(arow('&#128181; mission','var(--accent2)', m.title||'mission',
      m.est_value_cents?('~'+usd(m.est_value_cents)):'', 'money'));
  if (s.proposals_pending)
    rows.push(arow('&#128717; POD','var(--accent)', `${s.proposals_pending} product proposals awaiting review`, '', 'proposals'));
  if (misProp > 2)
    rows.push(arow('&#128181; mission','var(--accent2)', `+${misProp-Math.min(propMis.length,2)} more proposed missions`, '', 'money'));

  const badges = [];
  if (prayPend)           badges.push(`${prayPend} prayer${prayPend===1?'':'s'}`);
  if (jNeed)              badges.push(`${jNeed} swarm`);
  if (misProp)            badges.push(`${misProp} missions`);
  if (s.proposals_pending)badges.push(`${s.proposals_pending} POD`);
  badges.push(`<span id="await-billsct">&#8230; bills</span>`);
  badges.push(`<span id="await-mailct">&#8230; mail</span>`);

  h += `<div class="section-header"><div><div class="section-title">&#8987; Awaiting you</div>
        <div class="section-sub">${badges.join(' · ')}</div></div></div>
        <div id="await-list" style="display:flex;flex-direction:column;gap:6px;margin-bottom:20px;">
          ${rows.slice(0,8).join('')}
          <div id="await-empty" style="color:var(--muted);font-size:.82rem;${rows.length?'display:none;':''}">Nothing awaiting you. &#127881;</div>
        </div>`;

  // Generating now
  if (gens.length) {
    h += `<div class="section-header"><div class="section-title">&#9889; Generating Now</div></div><div class="gen-bars">`;
    for (const g of gens)
      h += `<div class="gen-bar"><div class="gen-bar-pulse"></div><div class="gen-bar-label">${esc((g.prompt||'').slice(0,80))}</div><div class="gen-bar-model">${esc(g.model||'')}</div></div>`;
    h += `</div>`;
  }

  // Dev swarm one-liner (needs-you rows already live in Awaiting you)
  h += `<div class="section-header"><div><div class="section-title">&#129302; Dev Swarm</div>
        <div class="section-sub">${jobs.length} jobs · ${jWork} working · ${jNeed} need you · ${jDone} done</div></div>
        <button class="btn-sm" onclick="switchView('github')">Open board &rarr;</button></div>`;

  // Storefront + POD summary (WordPress half patched in by the deferred fetch)
  h += `<div class="section-header"><div><div class="section-title">&#128722; Storefront</div>
        <div class="section-sub"><span id="dash-sf-wp">checking WordPress&#8230;</span> ·
        POD: ${s.review_count||0} in review, ${s.approved_count||0} approved</div></div></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px;">
          <button class="btn-sm" onclick="switchView('portal')">&#127760; Portal &rarr; WordPress</button>
          <button class="btn-sm" onclick="switchView('etsy-printify')">&#128717; Etsy / Printify</button>
          <button class="btn-sm" onclick="switchView('cults3d')">&#128424;&#65039; Cults3D</button>
          <button class="btn-sm" onclick="switchView('resell')">&#128247; Resell</button>
        </div>`;

  // Quick actions
  h += `<div class="section-header"><div class="section-title">&#9193; Quick actions</div></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button class="btn-sm primary" onclick="switchView('image-gen')">&#127912; Generate image</button>
          <button class="btn-sm" onclick="switchView('videos')">&#127916; Video</button>
          <button class="btn-sm" onclick="switchView('audio')">&#127925; Audio</button>
          <button class="btn-sm" onclick="switchView('models3d')">&#127981; 3D</button>
          <button class="btn-sm" onclick="switchView('github')">&#129302; New swarm job</button>
          <button class="btn-sm" onclick="switchView('research')">&#128300; Research</button>
          <button class="btn-sm" onclick="switchView('oracle')">&#128302; Oracle</button>
          <button class="btn-sm" onclick="switchView('agent')">&#129302; Assistant</button>
          <button class="btn-sm" onclick="switchView('mail')">&#128236; Mail</button>
          <button class="btn-sm" onclick="switchView('homelab')">&#128268; Services</button>
        </div>`;

  // re-check after the awaits — the user may have switched views mid-fetch
  if (typeof _currentView !== 'undefined' && _currentView !== 'dashboard') return;
  document.getElementById('main-content').innerHTML = h;

  /* ── deferred fills: slow endpoints patch in after first paint ── */
  const stillHere = () => !(typeof _currentView !== 'undefined' && _currentView !== 'dashboard');
  const patch = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };

  // portal status (hits live WordPress; 0.3s cached but seconds when cold)
  api('/api/portal/status').then(portal => {
    if (!stillHere()) return;
    patch('dash-portal', portal.connected ? String(portal.total_products ?? '—') : 'off');
    patch('dash-sf-wp', portal.connected
      ? `WordPress connected · ${portal.total_products ?? '?'} products`
      : 'WordPress not connected');
  }).catch(() => { if (stillHere()) { patch('dash-portal', '—'); patch('dash-sf-wp', 'WordPress unreachable'); } });

  // bills (Finance → 📆 Bills). The router is optional: on any failure the card
  // hides itself rather than showing a broken tile.
  api('/api/bills/summary').then(bl => {
    if (!stillHere() || !bl) return;
    const od = bl.overdue_count || 0, soon = bl.due_soon_count || 0;
    patch('dash-bills', String(od + soon));
    const val = document.getElementById('dash-bills');
    if (val) val.className = 'stat-val ' + (od ? 'c-warn' : 'c-accent');
    patch('dash-bills-sub', od
      ? `${od} overdue · ${usd(bl.monthly_total_cents)}/mo est.`
      : `${soon} due in 7d · ${usd(bl.monthly_total_cents)}/mo est.`);

    // ⏳ Awaiting you: overdue first, then anything due within 3 days.
    const urgent = [
      ...(bl.overdue || []).map(b => [b, true]),
      ...(bl.due_soon || []).filter(b => (b.days ?? 99) <= 3).map(b => [b, false]),
    ].slice(0, 4);
    if (urgent.length) {
      const list = document.getElementById('await-list');
      const empty = document.getElementById('await-empty');
      if (empty) empty.style.display = 'none';
      if (list) {
        let bh = '';
        for (const [b, over] of urgent) {
          const d = b.days ?? 0;
          const when = over ? `${-d} day${d === -1 ? '' : 's'} overdue`
            : (d <= 0 ? 'due today' : `due in ${d} day${d === 1 ? '' : 's'}`);
          const amt = (b.amount_cents === null || b.amount_cents === undefined) ? 'varies' : usd(b.amount_cents);
          bh += `<div class="gen-bar" style="cursor:pointer;margin-bottom:0;" onclick="dashOpenBills()">
                   <div class="gen-bar-pulse" style="background:${over ? 'var(--red)' : 'var(--warn)'}"></div>
                   <span style="font-size:.62rem;padding:1px 7px;border-radius:9px;background:var(--surface);color:var(--muted);flex-shrink:0;">&#128198; bill</span>
                   <div class="gen-bar-label">${esc(b.name || 'bill')} — ${when}</div>
                   <div class="gen-bar-model">${amt}${b.autopay ? ' · autopay' : ''}</div></div>`;
        }
        list.insertAdjacentHTML('afterbegin', bh);
      }
    }
    const ct = od + soon;
    patch('await-billsct', `${ct} bill${ct === 1 ? '' : 's'}`);
  }).catch(() => {
    if (!stillHere()) return;
    const val = document.getElementById('dash-bills');
    const card = val && val.closest('.stat-card');
    if (card) card.style.display = 'none';
    patch('await-billsct', 'bills off');
  });

  // homelab docker probe (~1s)
  api('/api/homelab/services').then(hl => {
    if (!stillHere()) return;
    let up = 0, tot = 0;
    ((hl && hl.groups) || []).forEach(g => (g.services || []).forEach(x => {
      if (x.source === 'docker') { tot++; if (x.running) up++; }
    }));
    patch('dash-svc', tot ? `${up}/${tot}` : '—');
  }).catch(() => { if (stillHere()) patch('dash-svc', '—'); });

  // mail (IMAP, can take seconds; 400s when not configured)
  api('/api/mail/inbox?limit=10').then(m => {
    if (!stillHere()) return;
    const msgs = (m && m.messages) || [];
    const unseen = msgs.filter(x => !x.seen);
    patch('dash-mail', String(unseen.length));
    patch('await-mailct', `${unseen.length} mail`);
    if (unseen.length) {
      const list = document.getElementById('await-list');
      const empty = document.getElementById('await-empty');
      if (empty) empty.style.display = 'none';
      if (list) {
        let mh = '';
        for (const u of unseen.slice(0,2))
          mh += arow('&#128236; mail','var(--accent)', u.subject||'(no subject)', u.from_name||u.from_email||'', 'mail');
        list.insertAdjacentHTML('beforeend', mh);
      }
    }
  }).catch(() => { if (stillHere()) { patch('dash-mail', '—'); patch('await-mailct', 'mail off'); } });
}

/* ══ STORE STATS ══
   The standalone Stats subtab folded into Etsy/Printify → Dashboard, which
   embeds the same numbers via loadStoreStatsInto('ep-stats-body'). The old
   full-view renderer is kept as a thin shell for any legacy caller. */
async function renderStoreStats() {
  _setContent(`
    <div class="view-header">
      <div class="view-title">&#128200; Store Stats</div>
      <div class="view-sub">Live data from Printify &amp; Etsy</div>
      <button class="btn-sm" onclick="renderStoreStats()">&#8635; Refresh</button>
    </div>
    <div id="stats-body" style="padding:0 4px;"><div style="color:var(--muted);margin-top:20px;text-align:center;">&#8987; Loading&hellip;</div></div>`);
  await loadStoreStatsInto('stats-body');
}

/* Fetch /api/store-stats and paint the Printify + Etsy blocks into any
   container. Never throws — errors render inline in the target element. */
async function loadStoreStatsInto(targetId) {
  const target = document.getElementById(targetId);
  if (!target) return;
  try {
    const st = await api('/api/store-stats');
    let h = '';

    // ── Printify ────────────────────────────────────────────────
    h += `<div class="settings-group"><div class="settings-group-title">&#128640; Printify</div>`;
    if (st.error?.printify) {
      h += `<div style="color:var(--error);font-size:.8rem;">Error: ${esc(st.error.printify)}</div>`;
    } else if (!st.printify) {
      h += `<div style="color:var(--muted);font-size:.8rem;">Not configured — add your Printify API key in Store Configuration below.</div>`;
    } else {
      const p = st.printify;
      h += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;margin-top:6px;">`;
      h += statCard('&#128230; Products Live', p.live_products);
      h += statCard('&#128196; Drafts',         p.draft_products);
      h += statCard('&#128666; Recent Orders',   p.recent_orders);
      h += statCard('&#9201;&#65039; Pending',    p.pending_orders);
      h += statCard('&#9989; Fulfilled',          p.fulfilled_orders);
      h += `</div>`;
    }
    h += `</div>`;

    // ── Etsy ────────────────────────────────────────────────────
    h += `<div class="settings-group"><div class="settings-group-title">&#128717; Etsy</div>`;
    if (st.etsy_reconnect) {
      // Stale OAuth — clean CTA instead of a raw error string.
      h += `<div style="color:var(--warning,#e6a23c);font-size:.8rem;">&#9888; ${esc(st.error?.etsy || 'Etsy authorization expired.')} `;
      h += `<button class="btn-sm" onclick="reconnectEtsy()">&#128260; Reconnect Etsy</button></div>`;
    } else if (st.error?.etsy) {
      h += `<div style="color:var(--error);font-size:.8rem;">Error: ${esc(st.error.etsy)}</div>`;
    } else if (!st.etsy) {
      h += `<div style="color:var(--muted);font-size:.8rem;">Not connected — authorize Etsy in Store Configuration below.</div>`;
    } else {
      const e = st.etsy;
      h += `<div style="font-size:.8rem;color:var(--muted);margin-bottom:8px;">Shop: <b style="color:var(--text);">${esc(e.shop_name)}</b></div>`;
      h += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;margin-bottom:12px;">`;
      h += statCard('&#128717; Active Listings', e.listing_count);
      h += statCard('&#128065;&#65039; Total Views',   e.total_views);
      h += statCard('&#10084;&#65039; Favorites',      e.total_favorites);
      h += statCard('&#128181; Sales',                e.transaction_sold_count);
      h += statCard('&#11088; Avg Review',             e.review_average ? e.review_average.toFixed(1) : '—');
      h += `</div>`;

      if (e.top_listings && e.top_listings.length) {
        h += `<div style="font-size:.78rem;font-weight:600;margin-bottom:6px;color:var(--text);">&#128293; Top Listings by Views</div>`;
        h += `<div style="display:flex;flex-direction:column;gap:5px;">`;
        for (const l of e.top_listings) {
          h += `<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 8px;background:var(--surface);border-radius:6px;font-size:.75rem;">`;
          h += `<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(l.title||'').slice(0,60)}</span>`;
          h += `<span style="color:var(--muted);white-space:nowrap;margin-left:12px;">&#128065;&#65039; ${l.views||0} &nbsp;&#10084;&#65039; ${l.num_favorers||0}</span>`;
          h += `<a href="https://etsy.com/listing/${l.listing_id}" target="_blank" rel="noopener" style="color:var(--accent);font-size:.7rem;margin-left:10px;">&#8599;</a>`;
          h += `</div>`;
        }
        h += `</div>`;
      }
    }
    h += `</div>`;

    target.innerHTML = h;
  } catch(e) {
    target.innerHTML = `<div style="color:var(--error);margin-top:20px;">${esc(e.message)}</div>`;
  }
}

/* Re-run the Etsy OAuth flow (same endpoint as first-time Connect). Used by the
   "Reconnect Etsy" CTA shown when the refresh token has gone stale. */
async function reconnectEtsy() {
  try { const r = await api('/api/etsy/connect'); if (r.url) window.open(r.url, '_blank'); }
  catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.reconnectEtsy = reconnectEtsy;

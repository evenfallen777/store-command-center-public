/* ══ THE COMPANY — God Console ══
   The 🙏 approval queue + budget + community messages. Nothing real or costly
   happens without you (god) approving, or a budget covering it.
   Uses _worldModal (world-economy.js) + api()/esc()/toast(). Global-scope classic script. */

function _money(c) { return '$' + ((c || 0) / 100).toFixed(2); }

const _PRAYER_ICON = {
  publish_wordpress: '🌐', post_etsy: '🛍️', post_printify: '👕', publish_cults3d: '🧊',
  generate_image: '🖼️', generate_music: '🎵', generate_video: '🎬', generate_3d: '🧊',
  paypal_payout: '💸', add_affiliate: '🔗', add_software: '💾', library_research: '📖', misc: '✨',
};
const _MSG_STYLE = {
  warning: { c: '#f59e0b', i: '⚠️' }, praise: { c: '#6ee7a8', i: '🎉' },
  need: { c: '#60a5fa', i: '🙏' }, info: { c: '#8a97ad', i: 'ℹ️' },
};

async function worldGod() {
  let s, prayers = [], msgs = [], taste = null, gates = null, recentProps = [], eng = null, engReview = [], liveSt = null, engWaiting = [];
  try {
    [s, prayers, msgs, taste, gates, recentProps, eng, engReview, liveSt, engWaiting] = await Promise.all([
      api('/api/world/ops/summary'),
      api('/api/world/ops/prayers?status=pending').then(r => r.prayers || []),
      api('/api/world/ops/messages?limit=12').then(r => r.messages || []),
      api('/api/world/taste').catch(() => null),
      api('/api/world/ops/gates').catch(() => null),
      api('/api/world/props/recent?limit=12').then(r => r.props || []).catch(() => []),
      api('/api/github/engineers-settings').catch(() => null),
      api('/api/github/jobs?status=awaiting_review').catch(() => []),
      api('/api/github/live-status').catch(() => null),
      api('/api/github/jobs?status=awaiting_input').catch(() => []),
    ]);
  } catch (e) { toast?.('God Console failed to load'); return; }

  // jobs where the crew raised a BLOCKING question — pull their open questions
  let engWaitDetails = [];
  if (eng && (engWaiting || []).length) {
    engWaitDetails = (await Promise.all(engWaiting.slice(0, 4).map(j =>
      api('/api/github/jobs/' + j.id).catch(() => null)))).filter(Boolean);
  }

  const _unrated = recentProps.filter(p => p.user_verdict == null).length;
  const worldPropsHtml = recentProps.length ? `
    <div style="font-weight:700;color:#e8eefc;margin:14px 0 3px">🏠 World creations they made ${_unrated ? `<span class="pill">${_unrated} new</span>` : ''}</div>
    <div style="font-size:.7rem;color:#7a86a0;margin:0 0 6px">Pixel props &amp; decor your agents made for their world. 👍/👎 teaches their taste; reject makes the maker rework it.</div>
    <div style="display:flex;flex-wrap:wrap;gap:8px">
      ${recentProps.map(p => {
        const rated = p.user_verdict === 1 ? '👍' : (p.user_verdict === -1 ? '👎' : '');
        return `<div style="width:96px;border:1px solid #26324a;border-radius:8px;padding:5px;background:#0e1626;text-align:center">
          <img src="${esc(p.image_path)}" alt="" style="width:84px;height:84px;object-fit:contain;image-rendering:pixelated;background:#0b1120;border-radius:5px" onerror="this.style.opacity=.25">
          <div style="font-size:.62rem;color:#aeb9cc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(p.label || '')}">${esc(p.label || '')}${p.score != null ? ` · ${p.score}/10` : ''}</div>
          ${rated ? `<div style="font-size:.85rem;margin-top:1px">${rated}</div>` : `<div style="display:flex;gap:3px;justify-content:center;margin-top:2px">
            <button class="btn" style="padding:1px 7px;font-size:.7rem;background:#1f4a32;border-color:#2a5a3a" title="Like — teaches the town this is good." onclick="worldPropVerdict(${p.id},true)">👍</button>
            <button class="btn" style="padding:1px 7px;font-size:.7rem;border-color:#5a2a2a" title="Reject — teaches dislike and the maker reworks it." onclick="worldPropVerdict(${p.id},false)">👎</button>
          </div>`}
        </div>`;
      }).join('')}
    </div>` : '';

  const gatesHtml = gates ? `
    <div style="background:#0e1626;border:1px solid #26324a;border-radius:8px;padding:8px 10px;margin-bottom:10px">
      <div style="font-size:.64rem;color:#7a86a0;margin-bottom:5px">🔒 GATES — what always waits for your blessing (each is a switch)</div>
      <label style="display:flex;align-items:center;gap:7px;font-size:.74rem;color:#c7d2e5;margin-bottom:6px;cursor:pointer">
        <input type="checkbox" ${gates.creations ? 'checked' : ''} onchange="worldToggleGate('creations',this.checked)">
        <span>🎨 Always judge creations before publishing <span style="color:#7a86a0">— art waits for your 👍/👎 even in Auto mode</span></span>
      </label>
      <div style="display:flex;flex-wrap:wrap;gap:5px">
        ${(gates.kinds || []).map(k => `
          <label style="display:flex;align-items:center;gap:5px;font-size:.7rem;color:#c7d2e5;background:#0b1120;border:1px solid #26324a;border-radius:6px;padding:3px 8px;cursor:pointer" title="On = always asks you first. Off = may auto-run in Auto ≤budget mode.">
            <input type="checkbox" ${k.gated ? 'checked' : ''} onchange="worldToggleGate('${k.kind}',this.checked)">
            <span>${esc(k.label)}</span>
          </label>`).join('')}
      </div>
      <div style="font-size:.62rem;color:#54607a;margin-top:5px">On = always asks you first (never auto-runs). Off = may auto-run in Auto ≤budget mode.</div>
    </div>` : '';

  // ── 🛠️ The Engineers — autonomous dev crew controls + the merge gate queue ──
  const engHtml = eng ? (() => {
    const es = eng.settings || {};
    const on = (es.engineers_auto || 'off') === 'on';
    const projRows = (eng.projects || []).map(p => `
      <span style="display:flex;align-items:center;gap:5px;font-size:.7rem;color:#c7d2e5;background:#0b1120;border:1px solid #26324a;border-radius:6px;padding:3px 8px"
             title="Let the Engineers auto-build improvements to this project on its '${esc(p.work_branch || 'dev')}' branch. Review mode picks who merges to main: you, the reviewer swarm's consensus, or either. Applying main to the LIVE store is a separate step (⬆️ button or the 🚀 auto toggle).">
        <label style="display:flex;align-items:center;gap:5px;cursor:pointer">
          <input type="checkbox" ${p.engineers_enabled ? 'checked' : ''} onchange="worldEngineersProject(${p.id},this.checked)">
          <span>${p.kind === 'store' ? '🏪' : '📁'} ${esc(p.name)} <span style="color:#54607a">· ${esc(p.work_branch || 'dev')}</span></span>
        </label>
        <select style="font-size:.66rem;background:#0e1626;color:#c7d2e5;border:1px solid #26324a;border-radius:4px"
                title="Review gate: who approves the merge to main" onchange="worldEngineersReviewMode(${p.id},this.value)">
          <option value="human" ${(p.review_mode || 'human') === 'human' ? 'selected' : ''}>🧑 you</option>
          <option value="swarm" ${p.review_mode === 'swarm' ? 'selected' : ''}>🤖 swarm</option>
          <option value="either" ${p.review_mode === 'either' ? 'selected' : ''}>🤝 either</option>
        </select>
        <label style="display:flex;align-items:center;gap:3px;cursor:pointer"
               title="Auto go-live after approval (swarm applies main→live): once a job promotes to main, the store applies main → live store and restarts by itself. Store/primary only — side projects have no separate running live. OFF by default.">
          <input type="checkbox" ${p.auto_go_live ? 'checked' : ''} onchange="worldEngineersAutoLive(${p.id},this.checked)">🚀
        </label>
      </span>`).join('');
    const waitRows = engWaitDetails.map(j => {
      const qs = (j.questions || []).filter(q => q.status === 'open');
      return qs.map(q => `<div style="font-size:.74rem;padding:4px 0;border-top:1px solid #1b2740">
        <div style="color:#c7d2e5">⏸ <b style="color:#e8eefc">${esc(j.title)}</b>
          <span style="color:#54607a"> · ${esc(q.agent || 'crew')} asks:</span></div>
        <div style="color:#aeb9cc;margin:2px 0">${esc(q.question)}</div>
        <div style="display:flex;gap:5px">
          <input id="god-swq-${q.id}" placeholder="Your answer — the job resumes with it"
                 style="flex:1;padding:3px 7px;background:#0b1120;border:1px solid #26324a;border-radius:5px;color:#e8eefc;font-size:.72rem"
                 onkeydown="if(event.key==='Enter')worldEngineersAnswer(${q.id})">
          <button class="btn" style="padding:2px 9px;font-size:.7rem" onclick="worldEngineersAnswer(${q.id})">Send</button>
        </div></div>`).join('');
    }).join('');
    const liveRow = liveSt ? `
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:7px;padding-top:7px;border-top:1px solid #1b2740">
        <button class="btn" style="padding:3px 10px;font-size:.72rem;${liveSt.update_available ? 'background:#1f3a5a;border-color:#2a4a6a;color:#93c5fd' : ''}"
          title="Pulls the approved code on main into THIS running store and restarts it. (Different from Settings → Updates, which pulls a published release/public version.)" onclick="worldEngineersUpdateLive()">⬆️ Apply main → live store${liveSt.behind ? ` (${liveSt.behind})` : ''}</button>
        <span style="font-size:.68rem;color:${liveSt.update_available ? '#f0a860' : '#54607a'}">
          ${liveSt.update_available
            ? `${liveSt.pending_done || liveSt.behind} approved change${(liveSt.pending_done || liveSt.behind) === 1 ? '' : 's'} on main — not applied to the live store yet`
            : 'live store is up to date with main'}</span>
      </div>` : '';
    const reviewRows = (engReview || []).map(j => {
      const byEng = (j.spec || '').includes('[Filed by Engineer');
      return `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:.74rem;padding:4px 0;border-top:1px solid #1b2740">
        <span style="color:#c7d2e5;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
          ${byEng ? '🛠️' : '🤖'} <b style="color:#e8eefc">${esc(j.title)}</b>
          <span style="color:#54607a"> · ${esc(j.branch || 'dev')} branch · job #${j.id}</span></span>
        <span style="display:flex;gap:5px;flex-shrink:0">
          <button class="btn" style="padding:2px 8px;font-size:.7rem;background:#1f4a32;border-color:#2a5a3a" title="Approve — the work is accepted and can be promoted to the live branch (Dev Swarm tab → Promote)." onclick="worldEngineersApprove(${j.id})">✓ Approve</button>
          <button class="btn" style="padding:2px 8px;font-size:.7rem;border-color:#5a2a2a" title="Reject with a comment — the swarm revises on the working branch; nothing goes live." onclick="worldEngineersReject(${j.id})">✕ Reject</button>
        </span></div>`;
    }).join('');
    return `
    <div style="background:#0e1626;border:1px solid #26324a;border-radius:8px;padding:8px 10px;margin-bottom:10px">
      <div style="font-size:.64rem;color:#7a86a0;margin-bottom:5px">🛠️ THE ENGINEERS — autonomous dev crew (auto-builds on the working branch; YOU approve before live)</div>
      <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:6px">
        <label style="display:flex;align-items:center;gap:6px;font-size:.74rem;color:#c7d2e5;cursor:pointer">
          <input type="checkbox" ${on ? 'checked' : ''} onchange="worldEngineersSet('engineers_auto',this.checked?'on':'off')">
          <span>Engineers autonomous mode</span></label>
        <label style="display:flex;align-items:center;gap:5px;font-size:.72rem;color:#7a86a0">every
          <input type="number" min="1" max="168" value="${parseInt(es.engineers_interval_hours) || 12}"
                 style="width:52px;padding:2px 5px;background:#0b1120;border:1px solid #26324a;border-radius:5px;color:#e8eefc;font-size:.72rem"
                 onchange="worldEngineersSet('engineers_interval_hours',this.value)"> h per project</label>
        <label style="display:flex;align-items:center;gap:6px;font-size:.74rem;color:#c7d2e5;cursor:pointer"
               title="Full autonomy: the swarm auto-answers its OWN blocking questions (from job context) so the loop never stalls waiting for you. Off = questions wait for you below.">
          <input type="checkbox" ${(es.engineers_auto_answer || 'off') === 'on' ? 'checked' : ''} onchange="worldEngineersSet('engineers_auto_answer',this.checked?'on':'off')">
          <span>Auto-answer Q&amp;A</span></label>
        <button onclick="worldEngineersFullAuto()"
          title="One click: turn ON the full autonomous chain for the store — Engineers on, review = swarm consensus, auto-answer on, auto go-live on. You can still watch and override here."
          style="font-size:.7rem;padding:3px 9px;background:#3a2a5a;border:1px solid #6d43a8;border-radius:6px;color:#e8eefc;cursor:pointer">&#9889; Full Autonomy</button>
      </div>
      ${projRows ? `<div style="display:flex;flex-wrap:wrap;gap:5px">${projRows}</div>` : ''}
      ${waitRows ? `<div style="font-size:.68rem;color:#93c5fd;margin-top:7px">⏸ The crew is WAITING on you — answer to resume the job:</div>${waitRows}` : ''}
      ${reviewRows ? `<div style="font-size:.68rem;color:#f0a860;margin-top:7px">🔒 At the review gate — built on the dev branch &amp; tested, waiting for approval before main:</div>${reviewRows}` : ''}
      ${liveRow}
      <div style="font-size:.62rem;color:#54607a;margin-top:5px">Pipeline: idea → GitHub dev branch → review &amp; Q&amp;A (you or the swarm, per project) → main → ⬆️ Apply main → live store (your click, or a project's 🚀 auto go-live). Distinct from Settings → Updates, which pulls a published release.</div>
    </div>`;
  })() : '';

  const owed = s.owed_cents || 0;
  const modeReview = s.mode === 'review';

  const _prayerRow = p => {
    const creation = p.group === 'creation';
    return `
    <div style="border:1px solid #26324a;border-radius:8px;padding:8px 10px;margin-bottom:6px;background:#0e1626">
      <div style="display:flex;gap:9px">
        ${p.thumb ? `<img src="${esc(p.thumb)}" data-thumb="${esc(p.thumb)}" data-title="${esc(p.title)}" alt="" style="width:54px;height:54px;object-fit:cover;border-radius:6px;border:1px solid #2a3550;flex-shrink:0;cursor:zoom-in;background:#0b1120" onclick="openLightbox&&openLightbox(this.dataset.thumb,this.dataset.title)">` : ''}
        <div style="flex:1;min-width:0">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
            <div style="min-width:0">
              <span>${_PRAYER_ICON[p.kind] || '✨'}</span>
              <b style="color:#e8eefc">${esc(p.title)}</b>
              ${p.cost_cents ? `<span style="color:#fcd34d;font-size:.72rem"> · ${_money(p.cost_cents)}</span>` : '<span style="color:#6ee7a8;font-size:.72rem"> · free</span>'}
              ${p.agent_name ? `<span style="color:#7a86a0;font-size:.72rem"> · ${esc(p.agent_name)}</span>` : ''}
            </div>
            <div style="display:flex;gap:5px;flex-shrink:0">
              <button class="btn" style="padding:3px 9px;font-size:.72rem;background:#1f4a32;border-color:#2a5a3a" title="${creation ? 'Like it — publish it and teach the town this is good.' : 'Approve — it runs now; any cost is charged to the treasury.'}" onclick="worldPrayerApprove(${p.id})">${creation ? '👍 Like' : '✓ Bless'}</button>
              <button class="btn" style="padding:3px 9px;font-size:.72rem;border-color:#5a2a2a" title="${creation ? "Reject — teaches the town you don't like it, and the artist reworks it with your note." : 'Reject — nothing happens and no money is spent.'}" onclick="worldPrayerReject(${p.id})">${creation ? '👎 Reject' : '✕ Deny'}</button>
            </div>
          </div>
          ${p.detail ? `<div style="color:#aeb9cc;font-size:.74rem;margin-top:3px">${esc(p.detail)}</div>` : ''}
          ${p.taste != null ? `<div style="font-size:.68rem;margin-top:4px;display:flex;gap:8px;flex-wrap:wrap;color:#7a86a0">
            <span title="Predicted approval — learned from your past verdicts">🎯 ${Math.round(p.taste * 100)}% your taste</span>
            ${p.duality && p.duality.band && p.duality.band.best !== p.duality.band.worst ? `<span style="color:#c4b5fd" title="The Jesus/Satan hi-lo band: best case · calibrated expected · worst case">⚖️ ${Math.round(p.duality.band.worst * 100)}–${Math.round(p.duality.band.best * 100)}% (exp ${Math.round(p.duality.band.expected * 100)}%)</span>` : ''}
            <span style="color:${p.boss_ok ? '#6ee7a8' : '#f0a860'}" title="${esc(p.endorse_note || '')}">💼 Boss ${p.boss_ok ? 'endorses' : 'doubts it'}</span>
            <span style="color:${p.mayor_ok ? '#6ee7a8' : '#f0a860'}" title="${esc(p.endorse_note || '')}">🏛️ Mayor ${p.mayor_ok ? 'endorses' : 'objects'}</span>
          </div>` : ''}
          ${p.duality && (p.duality.for || p.duality.against) ? `<div style="font-size:.66rem;margin-top:3px;display:flex;flex-direction:column;gap:2px">
            ${p.duality.for ? `<span style="color:#8ecaff" title="Jesus argues FOR — the constructive/best case">${esc(p.duality.for)}</span>` : ''}
            ${p.duality.against ? `<span style="color:#f0a8b8" title="Satan argues AGAINST — the adversarial/worst case">${esc(p.duality.against)}</span>` : ''}
          </div>` : ''}
        </div>
      </div>
    </div>`;
  };
  const _creations = prayers.filter(p => p.group === 'creation');
  const _ops = prayers.filter(p => p.group !== 'creation');

  const msgRows = msgs.length ? msgs.map(m => {
    const st = _MSG_STYLE[m.kind] || _MSG_STYLE.info;
    return `<div style="font-size:.76rem;padding:3px 0;display:flex;gap:6px">
      <span>${st.i}</span>
      <span style="color:#c7d2e5">${m.from_agent ? `<b style="color:${st.c}">${esc(m.from_agent)}:</b> ` : ''}${esc(m.text)}</span>
      <span style="color:#54607a;margin-left:auto;flex-shrink:0">${(m.created_at || '').slice(5, 16)}</span></div>`;
  }).join('') : '<div style="color:#54607a;font-size:.8rem">The community is content.</div>';

  const html = `
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:14px">
      <div style="background:#0e1626;border:1px solid #26324a;border-radius:8px;padding:8px 10px">
        <div style="font-size:.64rem;color:#7a86a0">${owed ? '💸 BILL DUE' : '💰 BALANCE'} ${hlp('The treasury total. Positive (green) = earnings banked and available to withdraw. Red BILL DUE = the agents owe this much for Etsy/Printify listings until you pay it off.')}</div>
        <div style="font-size:1.15rem;font-weight:700;color:${owed ? '#f87171' : '#6ee7a8'}">${owed ? _money(owed) : _money(s.balance_cents)}</div>
      </div>
      <div style="background:#0e1626;border:1px solid #26324a;border-radius:8px;padding:8px 10px">
        <div style="font-size:.64rem;color:#7a86a0">🧾 SPENT THIS MONTH ${hlp('Real money the agents have spent this billing cycle vs. your monthly cap. The ONLY thing they ever pay for is Etsy/Printify listing fees — WordPress & Cults3D publishing are free.')}</div>
        <div style="font-size:1.15rem;font-weight:700;color:#e8eefc">${_money(s.cycle_spend_cents)} <span style="font-size:.66rem;color:#54607a">/ ${_money(s.cap_cents)} cap</span></div>
      </div>
      <div style="background:#0e1626;border:1px solid #26324a;border-radius:8px;padding:8px 10px">
        <div style="font-size:.64rem;color:#7a86a0">🤖 AUTOMATION ${hlp('Whether agents can spend on their own. “Review all” = every paid action waits in the Prayers queue for your approval. “Auto ≤budget” = agents may spend automatically while under the monthly cap; anything over-cap still asks you first.')}</div>
        <div style="display:flex;gap:4px;margin-top:3px">
          <button class="btn" style="padding:2px 8px;font-size:.68rem;${modeReview ? 'background:#3a2a5a;border-color:#6d5aff;color:#c4b5fd' : ''}" onclick="worldSetMode('review')">🙏 Review all</button>
          <button class="btn" style="padding:2px 8px;font-size:.68rem;${!modeReview ? 'background:#1f4a32;border-color:#2a5a3a;color:#6ee7a8' : ''}" onclick="worldSetMode('budget')">💸 Auto ≤budget</button>
        </div>
      </div>
    </div>

    ${gatesHtml}

    ${engHtml}

    <div style="font-weight:700;color:#e8eefc;margin:4px 0 3px">🎨 Creations to judge ${_creations.length ? `<span class="pill">${_creations.length}</span>` : ''}</div>
    <div style="font-size:.7rem;color:#7a86a0;margin:0 0 6px">Like or reject what your agents made — this teaches the town your taste. Reject one and the artist reworks it with your note.</div>
    ${_creations.length ? _creations.map(_prayerRow).join('') : '<div style="color:#54607a;font-size:.78rem;padding:4px 0">No new creations to judge yet. 🎨</div>'}

    <div style="font-weight:700;color:#e8eefc;margin:14px 0 3px">⚙️ Operations awaiting approval ${_ops.length ? `<span class="pill">${_ops.length}</span>` : ''}</div>
    <div style="font-size:.7rem;color:#7a86a0;margin:0 0 6px">Permission requests — spending, listings, code, research. Not creative work.</div>
    ${_ops.length ? _ops.map(_prayerRow).join('') : '<div style="color:#54607a;font-size:.78rem;padding:4px 0">No operations pending. 🕊️</div>'}

    ${worldPropsHtml}

    ${taste ? `<div style="margin:14px 0;padding:10px;background:#0e1626;border:1px solid #26324a;border-radius:8px">
      <div style="font-weight:600;font-size:.82rem;color:#e8eefc;margin-bottom:4px">🧠 What the town has learned about your taste</div>
      <div style="font-size:.72rem;color:#8a97ad;margin-bottom:6px">
        ${taste.examples} judgements studied · ${taste.positive} liked · ${taste.examples - taste.positive} disliked
        ${taste.trained ? ' · <span style="color:#6ee7a8">model active</span>' : ' · <span style="color:#f0a860">still learning</span>'}
        <span style="color:#54607a"> — every Bless/Deny above trains it</span>
      </div>
      <div style="display:flex;gap:6px;align-items:center">
        <input id="god-taste-q" placeholder="Would I like… (test an idea)" style="flex:1;padding:5px 8px;background:#0b1120;border:1px solid #26324a;border-radius:6px;color:#e8eefc;font-size:.76rem"
               onkeydown="if(event.key==='Enter')worldTasteTest()">
        <button class="btn" style="padding:4px 10px;font-size:.74rem" onclick="worldTasteTest()">🎯 Ask</button>
        <span id="god-taste-out" style="font-size:.78rem;color:#c7d2e5;min-width:70px"></span>
      </div>
    </div>` : ''}

    <div style="display:flex;gap:8px;flex-wrap:wrap;margin:14px 0;padding:10px;background:#0e1626;border:1px solid #26324a;border-radius:8px;align-items:flex-end">
      <div style="font-size:.72rem;color:#7a86a0;width:100%">💰 Money in — your real earnings fund the budget (WordPress &amp; Cults3D publishing are free; agents only ever spend on Etsy/Printify listings)</div>
      <input id="god-amt" type="number" step="0.01" placeholder="$ amount" style="width:100px;padding:5px 8px;background:#0b1120;border:1px solid #26324a;border-radius:6px;color:#e8eefc;font-size:.78rem">
      <button class="btn" style="padding:5px 10px;font-size:.72rem" title="Record real Cults3D sale income (enter the amount first). Credits the treasury the agents draw from." onclick="worldMoney('revenue','cults3d')">+ Cults3D earnings</button>
      <button class="btn" style="padding:5px 10px;font-size:.72rem" title="Record a real store sale into the treasury (enter the amount first)." onclick="worldMoney('revenue','store')">+ Store sale</button>
      <button class="btn" style="padding:5px 10px;font-size:.72rem" title="Top up the treasury with your own money (enter the amount first)." onclick="worldMoney('fund','manual')">+ Add funds</button>
      ${owed ? `<button class="btn" style="padding:5px 10px;font-size:.72rem;background:#1f3a5a;border-color:#2a4a6a" title="Record paying off the outstanding Etsy/Printify bill, clearing what the agents owe." onclick="worldMoney('payment','paypal')">✓ Pay Etsy bill (${_money(owed)})</button>` : ''}
      <span style="width:100%;font-size:.68rem;color:#54607a">Monthly Etsy/Printify spend cap: ${hlp('The hard ceiling on real money agents can spend per month on listings. In “Auto ≤budget” mode they never exceed this without asking. Click the amount to change it.')}
        <button class="btn" style="padding:1px 6px;font-size:.66rem" onclick="worldSetCap()">⚙️ ${_money(s.cap_cents)}/mo</button></span>
    </div>

    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 14px;padding:10px;background:#0e1626;border:1px solid #26324a;border-radius:8px">
      <span style="font-size:.8rem">🅿️ <b style="color:#e8eefc">PayPal</b> ${hlp('Connects PayPal so you can pay the agents’ Etsy/Printify bill and withdraw banked earnings to your real account. “Verify” checks the connection; earnings come from your recorded Cults3D/store sales.')}
        <span style="font-size:.72rem;color:${s.paypal?.configured ? '#6ee7a8' : '#f59e0b'}">${s.paypal?.configured ? `connected · ${esc(s.paypal.mode)} · ${esc(s.paypal.email || '')}` : 'not configured'}</span></span>
      <span style="margin-left:auto"></span>
      <button class="btn" style="padding:4px 10px;font-size:.72rem" onclick="worldPaypalVerify()">🔌 Verify</button>
      ${(s.balance_cents || 0) > 0
        ? `<button class="btn" style="padding:4px 10px;font-size:.72rem;background:#1f3a5a;border-color:#2a4a6a" title="Send banked earnings to your real PayPal. This queues a payout prayer - you must Bless it below before any money actually leaves." onclick="worldWithdraw(${s.balance_cents})">💸 Withdraw earnings (${_money(s.balance_cents)})</button>`
        : '<span style="font-size:.68rem;color:#54607a">no earnings banked to withdraw yet</span>'}
    </div>

    <div style="font-weight:700;color:#e8eefc;margin:14px 0 4px">📣 Community board</div>
    ${msgRows}`;

  _worldModal(window.WTheme ? WTheme.pick('🏛️ God Console', '🏛️ Owner Console') : '🏛️ God Console', html);
  api('/api/world/ops/messages/seen', { method: 'POST' }).catch(() => {});
  worldGodRefreshBadge();
}

async function worldPrayerApprove(id) {
  try { await api(`/api/world/ops/prayers/${id}/approve`, { method: 'POST', body: JSON.stringify({}) }); toast?.('🙏 Blessed'); }
  catch (e) { toast?.(e?.message || 'Approve failed'); }
  worldGod();
}
async function worldPrayerReject(id) {
  const comment = prompt('Reason (optional):') || '';
  try { await api(`/api/world/ops/prayers/${id}/reject`, { method: 'POST', body: JSON.stringify({ comment }) }); toast?.('Denied'); }
  catch (e) { toast?.(e?.message || 'Reject failed'); }
  worldGod();
}
async function worldPropVerdict(id, like) {
  let reason = '';
  if (!like) reason = window.prompt('What should the maker change? (optional):') || '';
  try {
    await api(`/api/world/prop/${id}/verdict`, { method: 'POST', body: JSON.stringify({ like, reason }) });
    toast?.(like ? '👍 Liked — taught the town your taste' : '👎 Rejected — the maker will rework it');
  } catch (e) { toast?.(e?.message || 'Failed'); }
  worldGod();
}
async function worldToggleGate(key, on) {
  try {
    await api('/api/world/ops/gates', { method: 'POST', body: JSON.stringify({ key, on }) });
    toast?.(on ? '🔒 Gate on — always asks you first' : '🔓 Gate off — may auto-run');
  } catch (e) { toast?.(e?.message || 'Failed'); }
  worldGod();
}
async function worldSetMode(mode) {
  try { await api('/api/world/ops/config', { method: 'POST', body: JSON.stringify({ mode }) }); }
  catch (e) { toast?.('Failed'); }
  worldGod();
}
async function worldSetCap() {
  const v = prompt('Monthly bill cap in dollars:');
  if (v == null) return;
  try { await api('/api/world/ops/config', { method: 'POST', body: JSON.stringify({ cap_dollars: parseFloat(v) || 0 }) }); }
  catch (e) { toast?.('Failed'); }
  worldGod();
}
async function worldMoney(kind, source) {
  const el = document.getElementById('god-amt');
  const amt = parseFloat(el?.value || '0');
  if (!amt || amt <= 0) { toast?.('Enter an amount'); return; }
  const path = kind === 'payment' ? null : null;
  try {
    await api('/api/world/ops/budget/entry', { method: 'POST', body: JSON.stringify({ kind, source, amount_dollars: amt }) });
    toast?.('Recorded');
  } catch (e) { toast?.('Failed'); }
  worldGod();
}

/* ── 🛠️ The Engineers (autonomous dev crew) controls ───────────────────────── */
async function worldEngineersSet(key, val) {
  try {
    await api('/api/github/engineers-settings', { method: 'POST', body: JSON.stringify({ settings: { [key]: String(val) } }) });
    toast?.(key === 'engineers_auto'
      ? (val === 'on' ? '🛠️ Engineers ON — they auto-build; you still approve before live' : 'Engineers off')
      : 'Saved');
  } catch (e) { toast?.(e?.message || 'Failed'); }
  worldGod();
}
async function worldEngineersFullAuto() {
  if (!confirm('Turn ON FULL AUTONOMY for the store?\n\nThe Engineers will propose → build → review (swarm consensus) → merge to main → auto go-live, and auto-answer their own questions — no human step. You can still watch and override in this panel.')) return;
  try {
    await api('/api/github/engineers-settings', { method: 'POST', body: JSON.stringify({ settings: { engineers_auto: 'on', engineers_auto_answer: 'on' } }) });
    const s = await api('/api/github/engineers-settings');
    const targets = (s.projects || []).filter(p => p.is_primary || p.kind === 'store');
    for (const p of targets) {
      await api('/api/github/projects/' + p.id, { method: 'PATCH', body: JSON.stringify({ engineers_enabled: 1, review_mode: 'swarm', auto_go_live: 1, autonomy: 'auto' }) });
    }
    toast?.('⚡ Full autonomy ON — the crew runs end-to-end; watch/override here');
  } catch (e) { toast?.('Full-auto failed: ' + (e?.message || e)); }
  worldGod();
}
async function worldEngineersProject(pid, on) {
  try {
    await api('/api/github/projects/' + pid, { method: 'PATCH', body: JSON.stringify({ engineers_enabled: on ? 1 : 0 }) });
    toast?.(on ? 'Engineers enabled on this project' : 'Engineers disabled on this project');
  } catch (e) { toast?.(e?.message || 'Failed'); }
  worldGod();
}
async function worldEngineersApprove(jid) {
  if (!confirm('Approve this job? You are the final approver — it can then be promoted to the live branch.')) return;
  try { await api('/api/github/jobs/' + jid + '/approve', { method: 'POST' }); toast?.('✅ Approved — promote it from the Dev Swarm tab'); }
  catch (e) { toast?.(e?.message || 'Approve failed'); }
  worldGod();
}
async function worldEngineersReject(jid) {
  const comment = prompt('What\'s wrong? (required — the swarm revises with this feedback):');
  if (!comment || !comment.trim()) { toast?.('A reject comment is required'); return; }
  try { await api('/api/github/jobs/' + jid + '/reject', { method: 'POST', body: JSON.stringify({ comment: comment.trim() }) }); toast?.('Rejected with feedback'); }
  catch (e) { toast?.(e?.message || 'Reject failed'); }
  worldGod();
}
async function worldEngineersReviewMode(pid, mode) {
  try {
    await api('/api/github/projects/' + pid, { method: 'PATCH', body: JSON.stringify({ review_mode: mode }) });
    toast?.(mode === 'human' ? 'You approve merges to main'
      : mode === 'swarm' ? '🤖 Reviewer-swarm consensus merges to main (going live still needs you)'
      : '🤝 You OR the swarm — whichever approves first');
  } catch (e) { toast?.(e?.message || 'Failed'); }
  worldGod();
}
async function worldEngineersUpdateLive() {
  if (!confirm('Apply main → live store?\n\nPulls the approved code on main into THIS running store and restarts it (~10s downtime). Different from Settings → Updates, which pulls a published release/public version.')) return;
  toast?.('Applying main to the live store…');
  try {
    const r = await api('/api/github/update-live', { method: 'POST' });
    toast?.(r.was_current && !r.marked_live ? 'Already up to date — restarting anyway'
      : `✅ Applying ${r.updated_to || 'main'} to the live store — back in ~10s`);
  } catch (e) { toast?.(e?.message || 'Apply failed'); }
}
async function worldEngineersAutoLive(pid, on) {
  try {
    await api('/api/github/projects/' + pid, { method: 'PATCH', body: JSON.stringify({ auto_go_live: on ? 1 : 0 }) });
    toast?.(on ? '🚀 Auto go-live ON — approved work applies main→live and restarts by itself'
               : 'Auto go-live off — applying main→live stays your click');
  } catch (e) { toast?.(e?.message || 'Failed'); }
  worldGod();
}
async function worldEngineersAnswer(qid) {
  const el = document.getElementById('god-swq-' + qid);
  const v = (el?.value || '').trim();
  if (!v) { toast?.('Type an answer first'); return; }
  try {
    const r = await api('/api/github/questions/' + qid + '/answer', { method: 'POST', body: JSON.stringify({ answer: v }) });
    toast?.(r.resumed ? '▶️ Answered — the job resumed with it' : 'Answered');
  } catch (e) { toast?.(e?.message || 'Answer failed'); }
  worldGod();
}

async function worldPaypalVerify() {
  toast?.('Checking PayPal…');
  try {
    const r = await api('/api/world/ops/paypal/verify', { method: 'POST', body: JSON.stringify({}) });
    toast?.(r.connected ? `✓ PayPal connected (${r.mode})` : `✕ ${r.error || 'not connected'}`);
  } catch (e) { toast?.('Verify failed'); }
}
async function worldWithdraw(maxCents) {
  const v = prompt(`Withdraw how much to your PayPal? (max $${(maxCents / 100).toFixed(2)})`,
    (maxCents / 100).toFixed(2));
  if (v == null) return;
  const dollars = parseFloat(v);
  if (!dollars || dollars <= 0) { toast?.('Enter an amount'); return; }
  try {
    await api('/api/world/ops/paypal/withdraw', { method: 'POST', body: JSON.stringify({ amount_dollars: dollars }) });
    toast?.('🙏 Payout queued — bless it below to send the money');
  } catch (e) { toast?.(e?.message || 'Withdraw failed'); }
  worldGod();
}

/* Passive pending-prayer badge — self-cleaning loop, generalized (Phase 2) to
   stamp BOTH possible hosts: the HUD's 🏛️ button (#world-god-badge, only in
   the DOM while the game canvas is up) and the sidebar's Command nav item
   (#badge-command, static markup in index.html — present from page load
   regardless of view). Since #badge-command always exists, the loop now runs
   for the life of the page rather than stopping when the World/Command tab
   is left; a single module-level handle still keeps repeated calls (from
   renderWorld(), the God Console open, and the boot kick below) from
   stacking multiple 8s loops. */
let _worldGodBadgeTimer = null;
function worldGodRefreshBadge() {
  if (_worldGodBadgeTimer) return;                     // a loop is already running → don't stack
  _worldGodBadgeTimer = setTimeout(_worldGodBadgeTick, 0);   // synchronously reserve the slot, kick now
}
async function _worldGodBadgeTick() {
  const elHud = document.getElementById('world-god-badge');
  const elNav = document.getElementById('badge-command');
  if (!elHud && !elNav) { _worldGodBadgeTimer = null; return; }   // neither host in the DOM → stop
  try {
    const s = await api('/api/world/ops/summary');
    const n = (s.pending_prayers || 0);
    const txt = n ? String(n) : '', show = n ? 'inline-block' : 'none';
    if (elHud) { elHud.textContent = txt; elHud.style.display = show; }
    if (elNav) { elNav.textContent = txt; elNav.style.display = show; }
  } catch {}
  if (!document.getElementById('world-god-badge') && !document.getElementById('badge-command')) {
    _worldGodBadgeTimer = null; return;
  }
  clearTimeout(_worldGodBadgeTimer);
  _worldGodBadgeTimer = setTimeout(_worldGodBadgeTick, 8000);
}
worldGodRefreshBadge();   // boot kick — #badge-command needs the poll running from page load,
                          // independent of whether World/Command is ever opened this session

async function worldTasteTest() {
  const q = document.getElementById('god-taste-q'), out = document.getElementById('god-taste-out');
  if (!q || !q.value.trim()) return;
  out.textContent = '…';
  try {
    const r = await api('/api/world/taste/test', { method: 'POST', body: JSON.stringify({ text: q.value.trim() }) });
    const pct = Math.round((r.score || 0.5) * 100);
    out.textContent = `${pct}% ${pct >= 70 ? '💚' : pct >= 45 ? '🤔' : '💔'}`;
  } catch (e) { out.textContent = 'error'; }
}
window.worldTasteTest = worldTasteTest;
window.worldGod = worldGod;
window.worldGodRefreshBadge = worldGodRefreshBadge;
window.worldPrayerApprove = worldPrayerApprove;
window.worldPrayerReject = worldPrayerReject;
window.worldSetMode = worldSetMode;
window.worldToggleGate = worldToggleGate;
window.worldPropVerdict = worldPropVerdict;
window.worldSetCap = worldSetCap;
window.worldMoney = worldMoney;
window.worldPaypalVerify = worldPaypalVerify;
window.worldWithdraw = worldWithdraw;
window.worldEngineersSet = worldEngineersSet;
window.worldEngineersFullAuto = worldEngineersFullAuto;
window.worldEngineersProject = worldEngineersProject;
window.worldEngineersApprove = worldEngineersApprove;
window.worldEngineersReject = worldEngineersReject;
window.worldEngineersReviewMode = worldEngineersReviewMode;
window.worldEngineersUpdateLive = worldEngineersUpdateLive;
window.worldEngineersAutoLive = worldEngineersAutoLive;
window.worldEngineersAnswer = worldEngineersAnswer;

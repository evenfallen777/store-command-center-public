/* ══ JELLYCOIN (JLY) — Crypto sub-tab ══
   The store's OWN GPU-mined token: chain stats, GPU rigs (old cards welcome, no
   CPU mining ever), Company skilling boosts (god-toggled), wallets & transfers,
   art NFTs, and agent push/sell missions (always behind god approval).
   Loaded by tab-crypto.js as pane 'jelly' → cryptoLoadJelly(). */

const _JLY = { st: null, tokenInfo: null };

function _jlyFmt(u) { return (u / 1e6).toLocaleString(undefined, { maximumFractionDigits: 2 }); }
function _jlyStat(label, val, hint) {
  return `<div style="flex:1;min-width:110px;background:var(--panel2,#0b1120);border:1px solid var(--border,#243049);border-radius:10px;padding:10px 12px;">
    <div style="font-size:.66rem;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;">${label}${hint ? ' ' + hlp(hint) : ''}</div>
    <div style="font-size:1.05rem;font-weight:700;margin-top:2px;">${val}</div></div>`;
}

/* ── the hard cap ─────────────────────────────────────────────────────────────
      6,000,000 JLY, enforced at every mint site — block subsidy AND skilling
      boosts draw from the same pool, so this bar is the whole story of how much
      JLY can ever exist. `sup` comes from /api/jelly/supply, which derives the
      number from the block table and reconciles it against the wallet ledger. */
function _jlySupplyPanel(sup) {
  if (!sup || !sup.chain) return '';
  const pct = Math.max(0, Math.min(100, +sup.pct_mined || 0));
  const eta = sup.cap_eta_epoch
    ? new Date(sup.cap_eta_epoch * 1000).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
    : 'never (subsidy decays to zero first)';
  const n = (v, d = 2) => (+v).toLocaleString(undefined, { maximumFractionDigits: d });
  // A non-zero discrepancy means coins moved outside a block. Never hide it.
  const bad = !sup.reconciled;
  return `<div class="settings-group" style="margin-bottom:16px;">
    <div class="settings-group-title">🧾 Supply &amp; hard cap
      ${hlp('JellyCoin has a fixed maximum of 6,000,000 JLY: the 1,000,000 genesis premine plus the 4,999,999.4 JLY the halving schedule pays out over 26 halvings, rounded to a clean 6M. The cap is enforced when a block is accepted, not merely documented — the final block\'s reward is trimmed to whatever headroom is left instead of overshooting, and after that the subsidy is zero. Skilling boosts mint inside the same cap, so they spend headroom that would otherwise have gone to the last coinbases.')}</div>
    <div style="display:flex;justify-content:space-between;font-size:.78rem;margin-bottom:5px;">
      <span><b>${n(sup.circulating)}</b> <span style="color:var(--muted);">JLY in circulation</span></span>
      <span style="color:var(--muted);">${pct.toFixed(2)}% of ${n(sup.max_supply, 0)}</span>
    </div>
    <div style="background:var(--surface,#161a22);border-radius:5px;height:15px;overflow:hidden;border:1px solid var(--border,#243049);">
      <div style="width:${Math.max(0.6, pct)}%;height:100%;background:linear-gradient(90deg,#00d4aa,#6c63ff);"></div>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:12px;">
      ${_jlyStat('Remaining', n(sup.remaining) + ' JLY', 'Headroom left under the cap. Every mint — coinbase and boost alike — is trimmed to fit inside this.')}
      ${_jlyStat('Block reward', n(sup.block_reward) + ' JLY', sup.block_reward < sup.scheduled_reward ? 'TRIMMED: the schedule says ' + n(sup.scheduled_reward) + ' JLY but only this much headroom remains under the cap.' : 'Halves every 50,000 blocks.')}
      ${_jlyStat('Next halving', '#' + n(sup.next_halving_height, 0), n(sup.blocks_to_halving, 0) + ' blocks away, ~' + n(sup.avg_block_sec, 0) + 's per block at the current rate.')}
      ${_jlyStat('Cap reached', eta, 'Projected from the observed block rate and the halving schedule, including the boost emission that mints alongside it. Not a promise — it moves with hashrate.')}
    </div>
    <div style="display:flex;gap:14px;flex-wrap:wrap;font-size:.72rem;color:var(--muted);margin-top:10px;line-height:1.8;">
      <span>Premine <b>${n(sup.premine, 0)}</b></span>
      <span>Mined <b>${n(sup.mined_subsidy)}</b></span>
      <span>Boosts <b>${n(sup.boost_minted)}</b></span>
      ${sup.burned ? `<span>Burned <b>${n(sup.burned)}</b></span>` : ''}
      <span>Tickets pending <b>${n(sup.boosts_pending, 0)}</b>${sup.boosts_expired ? ` · expired <b>${n(sup.boosts_expired, 0)}</b>` : ''}</span>
    </div>
    <div style="font-size:.72rem;margin-top:8px;color:${bad ? '#f87171' : 'var(--muted)'};line-height:1.7;">
      ${bad
      ? `⚠️ <b>Ledger mismatch:</b> the block table says ${n(sup.circulating)} JLY exists but wallet balances sum to ${n(sup.wallet_sum)} JLY — a difference of ${n(sup.discrepancy)} JLY. Coins moved outside a block; this is a bug, not a rounding artefact.`
      : `✅ Reconciled: block-derived supply and the sum of all ${''}wallet balances agree exactly (${n(sup.wallet_sum)} JLY).`}
    </div>
    <div style="font-size:.72rem;color:var(--muted);margin-top:6px;line-height:1.7;">
      Once the cap is reached there is no block subsidy, and this chain has no transaction fees —
      so mining past that point earns nothing. It still orders and secures blocks; it just stops paying.
    </div>
  </div>`;
}

/* ── how hard, and when: the owner's mining envelope ──────────────────────────
      Intensity is per-rig and reaches the card LIVE inside getwork, so editing a
      throttle here retunes a running miner within one refresh — no restart, no
      reinstall. The schedule is enforced on the SERVER (getwork just answers 503),
      so it binds even a rig running an old build that knows nothing about hours. */
function _jlyBatchOpts(sel) {
  return [[1 << 18, '2¹⁸ — tiniest'], [1 << 20, '2²⁰ — small'], [1 << 22, '2²² — normal'],
          [1 << 24, '2²⁴ — large']]
    .map(([v, l]) => `<option value="${v}"${+sel === v ? ' selected' : ''}>${l}</option>`).join('');
}

const _JLY_SRC = {
  owner: ['rgba(148,163,184,.16)', 'var(--muted)', 'yours'],
  agent: ['rgba(124,58,237,.18)', '#a78bfa', 'company'],
  defense: ['rgba(239,68,68,.18)', '#ef4444', 'defending'],
};

function _jlyRigsTable(rigs, mpol) {
  const pr = ((mpol || {}).rigs) || [];
  const byName = {};
  pr.forEach(r => { byName[r.name] = r; });
  _JLY.rigRows = rigs.map(m => m.name);
  const budget = ((mpol || {}).schedule || {}).daily_hours || 0;
  if (!rigs.length) return `<div style="font-size:.78rem;color:var(--muted);">No rigs yet. Dust off an old graphics card:</div>`;
  return `<table class="mini-table" style="width:100%;font-size:.78rem;">
    <tr><th style="text-align:left;">Rig</th><th style="text-align:left;">GPU</th><th>MH/s</th><th>Blocks</th>
      <th style="text-align:left;">Intensity ${hlp('How hard this rig mines. "Idle %" is the share of each second the card spends doing nothing — 0 is full blast, 90 is barely-there. Batch is the work per kernel launch: a smaller batch keeps each launch short so nothing else on that box ever waits long for the GPU. Both are sent to the rig inside its next getwork, so a change lands within a few seconds without restarting anything.')}</th>
      <th>Today ${hlp('Hours this rig has ACTUALLY mined since local midnight — counted from work issued, not wall-clock, so a paused or offline rig does not burn its budget.')}</th><th></th></tr>
    ${rigs.map((m, i) => {
      const p = byName[m.name] || { throttle: 50, batch: 1 << 22, cost: 'ai', source: 'owner', hours_today: 0 };
      const [bg, fg, lbl] = _JLY_SRC[p.source] || _JLY_SRC.owner;
      return `<tr><td>${esc(m.name)}</td><td style="color:var(--muted);">${esc(m.gpu || '?')}</td>
        <td style="text-align:center;">${(m.hashrate / 1e6).toFixed(1)}</td><td style="text-align:center;">${m.blocks}</td>
        <td style="white-space:nowrap;">
          <input id="jly-thr-${i}" type="number" min="0" max="90" value="${p.throttle}" style="width:56px;" title="idle %">
          <span style="color:var(--muted);font-size:.7rem;">% idle</span>
          <select id="jly-bat-${i}" style="width:104px;">${_jlyBatchOpts(p.batch)}</select>
          <select id="jly-cost-${i}" style="width:96px;" title="does this card also do AI work?">
            <option value="ai"${p.cost !== 'free' ? ' selected' : ''}>AI box</option>
            <option value="free"${p.cost === 'free' ? ' selected' : ''}>spare</option></select>
          <span style="font-size:.6rem;font-weight:700;background:${bg};color:${fg};border-radius:8px;padding:2px 6px;">${lbl}</span>
        </td>
        <td style="text-align:center;color:var(--muted);">${(p.hours_today || 0).toFixed(1)}h${budget ? ` / ${budget}` : ''}</td>
        <td style="text-align:center;white-space:nowrap;">
          <span style="color:${p.held ? '#f59e0b' : (m.online ? 'var(--green)' : 'var(--muted)')};">${p.held ? '⏸ held' : (m.online ? '● online' : '○ offline')}</span>
          <button class="btn-sm" style="margin-left:6px;" onclick="jellyRigSave(${i})">💾</button></td></tr>`;
    }).join('')}
  </table>`;
}

function _jlySchedPanel(mpol) {
  const s = (mpol || {}).schedule;
  if (!s) return '';
  const on = !!s.enabled;
  return `<div style="background:var(--panel2,#0b1120);border:1px solid var(--border,#243049);border-radius:10px;padding:10px 12px;margin-bottom:10px;">
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
      <b style="font-size:.8rem;">🕒 Mining hours</b>
      <span style="font-size:.62rem;font-weight:700;background:${on ? 'rgba(34,197,94,.16)' : 'rgba(148,163,184,.16)'};color:${on ? 'var(--green)' : 'var(--muted)'};border-radius:10px;padding:2px 8px;text-transform:uppercase;">${on ? 'on' : 'off'}</span>
      ${hlp('Your hard limits on when the rigs may mine at all. Enforced on the SERVER: outside your hours getwork simply refuses to hand out work, so even a rig running an old miner build obeys — it just sees the same "stand down" it already understands. Leave the toggle off and mining runs whenever the AI queue is free.')}
      ${on ? `<span style="font-size:.72rem;color:${s.open_now ? 'var(--green)' : 'var(--muted)'};">${s.open_now ? '● window open now' : '○ outside the window'}</span>` : ''}
      <span style="font-size:.7rem;color:var(--muted);">times are ${esc(s.tz || 'local')} (this box's clock)</span>
    </div>
    ${s.error ? `<div style="font-size:.72rem;color:#ef4444;margin-top:6px;">${esc(s.error)}</div>` : ''}
    <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:10px;">
      <button class="btn-sm" onclick="jellySchedSave(${on ? 'false' : 'true'})">${on ? '⏸ Turn off' : '▶ Turn on'} schedule</button>
      <div class="field" style="margin:0;"><label>Allowed hours ${hlp('Comma-separated HH:MM-HH:MM windows, e.g. "22:00-06:00" for overnight only, or "22:00-06:00, 12:00-13:00". Windows may cross midnight. Leave EMPTY to allow any hour and limit purely by the daily budget below.')}</label>
        <input id="jly-sched-win" value="${esc(s.windows || '')}" placeholder="22:00-06:00" style="width:190px;"></div>
      <div class="field" style="margin:0;"><label>Hours/day per rig ${hlp('A real budget, not a theory: the server counts the time each rig is actually issued work and stops handing out more once the rig hits this many hours today. Resets at local midnight. 0 = no daily limit.')}</label>
        <input id="jly-sched-hrs" type="number" min="0" max="24" step="0.5" value="${s.daily_hours || 0}" style="width:80px;"></div>
      <button class="btn-sm" onclick="jellySchedSave()">💾 Save</button>
    </div>
  </div>`;
}

function _jlyAgentPanel(mpol) {
  const a = (mpol || {}).agent;
  if (!a) return '';
  const on = !!a.enabled, plan = a.plan || null;
  return `<div style="background:var(--panel2,#0b1120);border:1px solid var(--border,#243049);border-radius:10px;padding:10px 12px;margin-bottom:10px;">
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
      <b style="font-size:.8rem;">🏢 Let the Company decide</b>
      <span style="font-size:.62rem;font-weight:700;background:${on ? 'rgba(124,58,237,.18)' : 'rgba(148,163,184,.16)'};color:${on ? '#a78bfa' : 'var(--muted)'};border-radius:10px;padding:2px 8px;text-transform:uppercase;">${on ? 'on' : 'off'}</span>
      ${hlp('When on, the Company picks when and how hard to mine — but only INSIDE the envelope you set here. Agents can never mine outside your hours, exceed the daily budget, push harder than the floor below, or override the AI queue. Those limits are enforced elsewhere in the server, so a bad decision physically cannot widen them; the worst an agent can do is mine less. Every plan is logged and shows as "company" on the rig it touches. Ships OFF.')}
    </div>
    ${plan ? `<div style="font-size:.74rem;color:var(--muted);margin-top:6px;">
      Active plan — <b>${esc(plan.agent || 'the Company')}</b> on <code>${esc(plan.rig)}</code>:
      ${plan.throttle != null ? `throttle ${plan.throttle}%` : 'no intensity change'}${plan.pause_until ? ', standing down' : ''}.
      ${esc(plan.reason || '')}
      <button class="btn-sm" style="margin-left:6px;" onclick="jellyAgentSave({clear_agent_plan:true})">✖ Clear</button></div>` : ''}
    <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:10px;">
      <button class="btn-sm" onclick="jellyAgentSave({agent_enabled:${on ? 'false' : 'true'}})">${on ? '⏸ Turn off' : '▶ Turn on'}</button>
      <div class="field" style="margin:0;"><label>Hardest they may push ${hlp('The lowest idle % an agent is allowed to ask for — their aggression ceiling. 25 means agents may choose anything from 25% idle (fairly hard) down to 90% idle (barely there), and nothing harder. Your own settings are unaffected.')}</label>
        <input id="jly-agent-thr" type="number" min="0" max="90" value="${a.min_throttle}" style="width:72px;"></div>
      <div class="field" style="margin:0;"><label>Max stand-down (min) ${hlp('The longest an agent may pause a rig in one decision.')}</label>
        <input id="jly-agent-pause" type="number" min="0" max="1440" value="${a.max_pause_min}" style="width:80px;"></div>
      <div class="field" style="margin:0;"><label>Max plan length (min) ${hlp('How long one agent decision stays in force before it expires and control reverts to your settings.')}</label>
        <input id="jly-agent-min" type="number" min="1" max="1440" value="${a.max_minutes}" style="width:80px;"></div>
      <button class="btn-sm" onclick="jellyAgentSave()">💾 Save</button>
    </div>
  </div>`;
}

/* ── 51%-attack defence: measured share of network hashpower + auto response ── */
function _jlyDefenseBanner(d) {
  if (!d || !d.enabled) return '';
  if (d.engaged) {
    const mins = d.engaged_since ? Math.round((Date.now() / 1000 - d.engaged_since) / 60) : 0;
    return `<div style="background:rgba(239,68,68,.14);border:1px solid #ef4444;border-radius:10px;padding:12px 14px;margin-bottom:10px;">
      <div style="font-weight:700;color:#ef4444;font-size:.9rem;">🛡️ CHAIN DEFENCE ENGAGED — every rig at full power</div>
      <div style="font-size:.78rem;color:var(--muted);margin-top:4px;line-height:1.6;">
        Our share of network hashpower measured <b>${_jlyPct(d.share_pct)}</b>, below your ${d.act_pct}% action line.
        All rigs ramped automatically ${mins ? `${mins} min ago` : 'just now'} — no approval needed, because an attack will not wait.
        ${d.preempt_ai ? 'AI work is being preempted for the duration (in-flight jobs were allowed to finish; nothing was cancelled).' : 'AI work still has priority — the preempt toggle is off.'}
        ${d.ai_seconds ? `Cost so far: ~${Math.round(d.ai_seconds / 60)} min of AI GPU time.` : ''}
        Stands down automatically once share holds above ${d.clear_pct}% for ${d.settle_min} min.
      </div>
      <button class="btn-sm" style="margin-top:8px;" onclick="jellyDefenseSave({stand_down:true})">✋ Stand down now</button>
    </div>`;
  }
  if (d.level === 'warn') {
    return `<div style="background:rgba(245,158,11,.14);border:1px solid #f59e0b;border-radius:10px;padding:10px 12px;margin-bottom:10px;">
      <div style="font-weight:700;color:#f59e0b;font-size:.82rem;">⚠️ Hashpower share slipping — ${_jlyPct(d.share_pct)}</div>
      <div style="font-size:.76rem;color:var(--muted);margin-top:3px;">
        Below your ${d.warn_pct}% warning line. Spare rigs are ramping; full defence engages below ${d.act_pct}%.</div></div>`;
  }
  return '';
}

function _jlyDefensePanel(d) {
  if (!d) return '';
  const on = !!d.enabled;
  const hist = (d.history || []).filter(h => h.share_pct != null);
  const series = hist.map(h => ({ t: h.at, h: h.blocks }));
  return `<div class="settings-group" style="margin-bottom:16px;">
    <div class="settings-group-title">🛡️ Chain defence
      <span style="font-size:.62rem;font-weight:700;background:${on ? 'rgba(34,197,94,.16)' : 'rgba(148,163,184,.16)'};color:${on ? 'var(--green)' : 'var(--muted)'};border-radius:10px;padding:2px 8px;margin-left:8px;text-transform:uppercase;">${on ? 'on' : 'off'}</span>
      ${hlp('JellyCoin is a real proof-of-work chain, so whoever holds the majority of hashpower can rewrite recent history. Today that is you. If outsiders ever join and your share erodes, this ramps your own rigs automatically — cheapest capacity first, and with no approval step, because an attack will not wait for you to wake up. Ships ON. It raises the cost of an attack; it cannot make a small chain unattackable.')}</div>
    <div style="font-size:.78rem;color:var(--muted);line-height:1.7;margin-bottom:10px;">
      Share is measured from <b>blocks actually solved</b> in the last ${d.window_blocks} blocks — the only evidence the chain can verify.
      A miner's self-reported hashrate is display only; an attacker can put any number there.
      ${d.confident ? '' : `<b>${esc(d.reason || 'sample too small')}</b> — no action will be taken on this.`}
      ${d.auto_rigs ? `<br><b>Your rigs were auto-detected</b> (${(d.my_rigs || []).map(esc).join(', ') || 'none'}) as "everything not mapped to a peer wallet". Pin the list below if outsiders ever mine here.` : ''}
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px;">
      ${_jlyStat('Our share', _jlyPct(d.share_pct), 'Fraction of recently solved blocks that came from your rigs. Binomial noise is real: at 60 blocks a true 50% attacker can read anywhere from ~37% to ~63%, which is why the thresholds carry margin.')}
      ${_jlyStat('Network hashrate', _jlyHash(d.net_hashrate), 'Estimated from the work the chain absorbed: sum of 2²⁵⁶/target over the window, divided by the time it spanned.')}
      ${_jlyStat('Ours', _jlyHash(d.my_hashrate))}
      ${_jlyStat('Sample', (d.blocks || 0) + ' blocks', 'Confidence figure — the number of blocks the share was measured over.')}
    </div>
    ${(d.per_rig || []).length ? `<div style="font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Who is finding blocks</div>
      ${_jlyBars((d.per_rig || []).map(r => ({ miner: r.rig + (r.mine ? '' : ' ⚠ not yours'), blocks: r.blocks })))}` : ''}
    ${series.length > 2 ? `<div style="margin-top:14px;">
      <div style="font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Our share over time (%)</div>
      ${_jlyChart(series, hist.map(h => h.share_pct), { color: '#ef4444', fmt: _jlyChartInt, refY: d.warn_pct, refLabel: 'warn ' + d.warn_pct + '%' })}
      <div style="font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin:10px 0 4px;">Network hashrate (MH/s)</div>
      ${_jlyChart(series, hist.map(h => (h.net_hashrate || 0) / 1e6), { color: '#6c63ff', fmt: _jlyChartInt })}
    </div>` : `<div style="font-size:.74rem;color:var(--muted);margin-top:10px;">History builds up as blocks are mined — a datapoint every ${d.sample_min || 15} min, plus one on every level change, so a slow erosion over days shows up instead of passing unnoticed.</div>`}
    <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:12px;">
      <button class="btn-sm" onclick="jellyDefenseSave({enabled:${on ? 'false' : 'true'}})">${on ? '⏸ Turn off' : '▶ Turn on'} defence</button>
      <button class="btn-sm" onclick="jellyDefenseSave({preempt_ai:${d.preempt_ai ? 'false' : 'true'}})">${d.preempt_ai ? '🤖 AI keeps priority' : '🛡️ Let defence beat AI'}</button>
      <div class="field" style="margin:0;"><label>Warn below ${hlp('Share that gets you told and starts ramping SPARE rigs only — capacity that costs nothing but watts.')}</label>
        <input id="jly-def-warn" type="number" min="0" max="100" value="${d.warn_pct}" style="width:72px;"></div>
      <div class="field" style="margin:0;"><label>Engage below ${hlp('Share that triggers full automatic defence: every rig to full power, immediately, no approval.')}</label>
        <input id="jly-def-act" type="number" min="0" max="100" value="${d.act_pct}" style="width:72px;"></div>
      <div class="field" style="margin:0;"><label>Stand down above ${hlp('Share that must be regained before defence ends. Kept at or above the engage line so recovery is a genuinely better position, not the same one.')}</label>
        <input id="jly-def-clear" type="number" min="0" max="100" value="${d.clear_pct}" style="width:72px;"></div>
      <div class="field" style="margin:0;"><label>…held for (min) ${hlp('Hysteresis. The share must stay recovered this long before standing down, so one noisy measurement cannot flap every rig on the box in and out of full power.')}</label>
        <input id="jly-def-settle" type="number" min="0" max="1440" value="${d.settle_min}" style="width:72px;"></div>
      <div class="field" style="margin:0;"><label>Window (blocks) ${hlp('How many recent blocks the share is measured over. Bigger = steadier but slower to notice an attack.')}</label>
        <input id="jly-def-window" type="number" min="5" max="500" value="${d.window_blocks}" style="width:72px;"></div>
      <div class="field" style="margin:0;flex:1;min-width:180px;"><label>My rigs ${hlp('Comma-separated rig names that count as YOURS. Leave empty to auto-detect (anything not mapped to a peer wallet) — but pin it once strangers can mine here, or a hostile rig would be counted as one of your own.')}</label>
        <input id="jly-def-rigs" value="${esc((d.my_rigs || []).join(', '))}" placeholder="auto-detect" style="width:100%;"></div>
      <button class="btn-sm" onclick="jellyDefenseSave()">💾 Save</button>
    </div>
    ${(d.history || []).length ? `<details style="margin-top:10px;">
      <summary style="font-size:.74rem;color:var(--muted);cursor:pointer;">Defence log (${d.history.length}) — what happened while you were asleep</summary>
      <table class="mini-table" style="width:100%;font-size:.74rem;margin-top:6px;">
        <tr><th style="text-align:left;">When</th><th>Share</th><th>Blocks</th><th style="text-align:left;">Level</th><th style="text-align:left;">Note</th></tr>
        ${d.history.slice(-40).reverse().map(h => `<tr>
          <td style="color:var(--muted);white-space:nowrap;">${new Date(h.at * 1000).toLocaleString()}</td>
          <td style="text-align:center;">${_jlyPct(h.share_pct)}</td><td style="text-align:center;">${h.blocks}</td>
          <td style="color:${h.level === 'engage' ? '#ef4444' : h.level === 'warn' ? '#f59e0b' : 'var(--muted)'};">${esc(h.level)}</td>
          <td style="color:var(--muted);">${esc(h.note || '')}</td></tr>`).join('')}
      </table></details>` : ''}
  </div>`;
}

function _jlyPct(v) { return v == null ? '—' : (+v).toFixed(1) + '%'; }
function _jlyHash(h) {
  h = +h || 0;
  return h >= 1e9 ? (h / 1e9).toFixed(2) + ' GH/s' : (h / 1e6).toFixed(1) + ' MH/s';
}

/* ── "yield to the AI queue" — mining stands down while the GPU is doing AI work.
      Same card runs LM Studio/ComfyUI and the miner; sharing it made models fail
      to load, so getwork holds until the queue has been idle for settle_sec. ── */
function _jlyYieldPanel(y) {
  if (!y) return '';
  const held = !!y.held, on = !!y.enabled;
  const [bg, fg, txt] = !on ? ['rgba(148,163,184,.16)', 'var(--muted)', 'yield off — mining always']
    : held ? ['rgba(245,158,11,.16)', '#f59e0b', 'mining paused — GPU in use by the queue']
    : ['rgba(34,197,94,.16)', 'var(--green)', 'mining — GPU free'];
  return `<div style="background:var(--panel2,#0b1120);border:1px solid var(--border,#243049);border-radius:10px;padding:10px 12px;margin-bottom:10px;">
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
      <span style="font-size:.66rem;font-weight:700;background:${bg};color:${fg};border-radius:10px;padding:3px 9px;text-transform:uppercase;">${txt}</span>
      ${hlp('The GPU node runs LM Studio + ComfyUI for the store\'s AI work and mines JLY on the same card. When both ran at once, models failed to load ("the GPU may be busy with another model or ComfyUI"), so getwork tells rigs to stand down while the queue is working and to resume once it has been idle for the settle window. Turn this off to go back to always-on mining.')}
      ${on && held && y.resume_in ? `<span style="font-size:.72rem;color:var(--muted);">resuming in ~${y.resume_in}s</span>` : ''}
      ${on && held && y.busy ? `<span style="font-size:.72rem;color:var(--muted);">queue busy${y.held_for ? ' · held ' + y.held_for + 's' : ''}</span>` : ''}
    </div>
    <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:10px;">
      <button class="btn-sm" onclick="jellyYieldSave(${on ? 'false' : 'true'})">${on ? '⏸ Turn off' : '▶ Turn on'} yield-to-queue</button>
      <div class="field" style="margin:0;"><label>Settle (sec) ${hlp('How long the queue must stay idle before mining resumes. Stops a burst of queued jobs from making the rig start/stop every few seconds. A single momentary job pauses it instantly.')}</label>
        <input id="jly-yield-settle" type="number" min="0" max="600" value="${y.settle_sec}" style="width:80px;"></div>
      <div class="field" style="margin:0;"><label>Re-check (sec) ${hlp('What a held rig is told to sleep before asking for work again. The miner backs off further if the hold persists.')}</label>
        <input id="jly-yield-retry" type="number" min="2" max="300" value="${y.retry_sec}" style="width:80px;"></div>
      <button class="btn-sm" onclick="jellyYieldSave()">💾 Save</button>
    </div>
  </div>`;
}

/* ── tiny SVG chart kit (dataviz-skill spec: thin marks, recessive grid, muted
      text ink, hover tooltip on every plot, one series per chart → no legend) ── */
function _jlyChart(series, ys, { color, fmt, refY = null, refLabel = '' }) {
  const W = 320, H = 120, PL = 44, PR = 8, PT = 8, PB = 18;
  const pts = series.map((s, i) => ({ x: i, y: ys[i], t: s.t, h: s.h }))
                    .filter(p => p.y != null && isFinite(p.y));
  if (pts.length < 2) return `<div style="color:var(--muted);font-size:.74rem;padding:20px 0;">not enough blocks yet</div>`;
  let lo = Math.min(...pts.map(p => p.y)), hi = Math.max(...pts.map(p => p.y));
  if (refY != null) { lo = Math.min(lo, refY); hi = Math.max(hi, refY); }
  if (hi === lo) hi = lo + 1;
  const X = i => PL + (i / (pts.length - 1)) * (W - PL - PR);
  const Y = v => PT + (1 - (v - lo) / (hi - lo)) * (H - PT - PB);
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`).join('');
  const grid = [lo, (lo + hi) / 2, hi].map(v =>
    `<line x1="${PL}" y1="${Y(v)}" x2="${W - PR}" y2="${Y(v)}" stroke="var(--border,#2a2f3d)" stroke-width="1"/>
     <text x="${PL - 5}" y="${Y(v) + 3}" text-anchor="end" font-size="8.5" fill="var(--muted)">${fmt(v)}</text>`).join('');
  const tfmt = t => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const ref = refY == null ? '' :
    `<line x1="${PL}" y1="${Y(refY)}" x2="${W - PR}" y2="${Y(refY)}" stroke="var(--muted)" stroke-width="1" stroke-dasharray="4 3"/>
     <text x="${W - PR}" y="${Y(refY) - 3}" text-anchor="end" font-size="8" fill="var(--muted)">${esc(refLabel)}</text>`;
  const data = esc(JSON.stringify(pts.map(p => [p.h, p.y, p.t])));
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block;cursor:crosshair"
       data-pts="${data}" data-lo="${lo}" data-hi="${hi}" data-pl="${PL}" data-pr="${PR}"
       onmousemove="_jlyHover(event,this)" onmouseleave="_jlyHoverOff(this)">
    ${grid}${ref}
    <path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>
    <circle r="4" fill="${color}" stroke="var(--surface,#161a22)" stroke-width="2" style="display:none"/>
    <text x="${PL}" y="${H - 4}" font-size="8.5" fill="var(--muted)">${tfmt(pts[0].t)}</text>
    <text x="${W - PR}" y="${H - 4}" text-anchor="end" font-size="8.5" fill="var(--muted)">${tfmt(pts[pts.length - 1].t)}</text>
  </svg>`;
}
function _jlyChartInt(v) { return Math.round(v).toLocaleString(); }
function _jlyChartNum(v) { return (Math.abs(v) >= 100 ? Math.round(v) : v.toFixed(1)).toLocaleString(); }

function _jlyHover(ev, svg) {
  const pts = JSON.parse(svg.dataset.pts);
  const r = svg.getBoundingClientRect();
  const PL = +svg.dataset.pl, PR = +svg.dataset.pr;
  const fx = (ev.clientX - r.left) / r.width * 320;
  const i = Math.max(0, Math.min(pts.length - 1,
    Math.round((fx - PL) / (320 - PL - PR) * (pts.length - 1))));
  const [h, y, t] = pts[i];
  const cx = PL + i / (pts.length - 1) * (320 - PL - PR);
  const dot = svg.querySelector('circle');
  const lo = +svg.dataset.lo, hi = +svg.dataset.hi;
  dot.style.display = '';
  dot.setAttribute('cx', cx);
  dot.setAttribute('cy', 8 + (1 - (y - lo) / ((hi - lo) || 1)) * (120 - 8 - 18));
  let tip = svg.parentElement.querySelector('.jly-tip');
  if (!tip) {
    tip = document.createElement('div');
    tip.className = 'jly-tip';
    tip.style.cssText = 'position:absolute;pointer-events:none;background:var(--surface2,#1e2330);border:1px solid var(--border,#2a2f3d);border-radius:6px;padding:4px 8px;font-size:.68rem;color:var(--text,#e2e8f0);white-space:nowrap;z-index:5;';
    svg.parentElement.style.position = 'relative';
    svg.parentElement.appendChild(tip);
  }
  tip.style.display = '';
  tip.textContent = `block ${h} · ${(Math.abs(y) >= 100 ? Math.round(y).toLocaleString() : y.toFixed(1))} · ${new Date(t * 1000).toLocaleTimeString()}`;
  tip.style.left = Math.min(r.width - 130, Math.max(0, (cx / 320) * r.width + 8)) + 'px';
  tip.style.top = '4px';
}
function _jlyHoverOff(svg) {
  svg.querySelector('circle').style.display = 'none';
  const tip = svg.parentElement.querySelector('.jly-tip');
  if (tip) tip.style.display = 'none';
}
window._jlyHover = _jlyHover; window._jlyHoverOff = _jlyHoverOff;

function _jlyBars(perRig) {
  if (!perRig.length) return `<div style="color:var(--muted);font-size:.74rem;padding:20px 0;">no blocks mined yet</div>`;
  const max = Math.max(...perRig.map(r => r.blocks));
  return perRig.map(r => `
    <div style="display:flex;align-items:center;gap:8px;margin:6px 0;" title="${esc(r.miner)}: ${r.blocks} blocks">
      <div style="width:90px;font-size:.72rem;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(r.miner)}</div>
      <div style="flex:1;background:var(--surface,#161a22);border-radius:4px;height:14px;">
        <div style="width:${Math.max(2, r.blocks / max * 100)}%;height:100%;background:#6c63ff;border-radius:4px;"></div>
      </div>
      <div style="font-size:.72rem;color:var(--muted);min-width:40px;text-align:right;">${r.blocks.toLocaleString()}</div>
    </div>`).join('');
}

// ── JOINED mode: we founded no chain, we're a participant on a buddy's ───────
// Everything worth showing lives on THEIR ledger, so this view reads our wallet
// there (via the peer RPC) instead of pretending we have a chain.
async function _jlyRenderJoined(pane, st) {
  const home = st.home_peer || '';
  let mine = null, buddies = null;
  try {
    [mine, buddies] = await Promise.all([
      api('/api/peers/my-wallets').catch(() => null),
      api('/api/peers').catch(() => null),
    ]);
  } catch (e) { /* the panel still renders without them */ }
  const row = ((mine || {}).wallets || []).find(w => w.peer === home);
  const peer = (((buddies || {}).peers) || []).find(p => p.name === home) || {};
  _JLY.paired = (((buddies || {}).peers) || []).filter(p => p.status === 'approved');

  pane.innerHTML = `
    <div class="section-header"><div><div class="section-title">🪼 JellyCoin — joined ${esc(home)}'s network</div>
      <div class="section-sub">This store founded <b>no chain of its own</b>: no genesis, no premine, no local mining.
      ${esc(home)}'s node is the ledger, and your JLY lives in your wallet there.</div></div>
      <button class="btn-sm" onclick="_cryptoLoaded.jelly=false;cryptoSub('jelly')">&#8635; Refresh</button>
    </div>

    <div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">👛 My wallet on ${esc(home)}'s chain</div>
      ${row && row.ok ? `
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:8px;">
          ${_jlyStat('Balance', Number(row.balance_jly).toLocaleString(undefined, { maximumFractionDigits: 4 }) + ' ' + esc(row.symbol || 'JLY'))}
          ${_jlyStat('Wallet', esc(row.wallet || '—'))}
        </div>
        ${(row.recent_txs || []).length ? `<table class="mini-table" style="width:100%;font-size:.76rem;">
          <tr><th style="text-align:left;">From</th><th style="text-align:left;">To</th><th style="text-align:right;">JLY</th><th style="text-align:left;">Why</th></tr>
          ${(row.recent_txs || []).slice(0, 8).map(t => `<tr><td>${esc(t.frm || '—')}</td><td>${esc(t.dst || '—')}</td>
            <td style="text-align:right;font-weight:600;">${_jlyFmt(t.amount)}</td>
            <td style="color:var(--muted);">${esc(t.memo || t.kind || '')}</td></tr>`).join('')}
        </table>` : `<div style="font-size:.78rem;color:var(--muted);">No transactions yet — mine for them, review their code, or lend them AI.</div>`}`
      : `<div style="font-size:.78rem;color:var(--muted);">
           Couldn't reach ${esc(home)}'s node${row && row.error ? ` — ${esc(row.error)}` : ''}.
           Your balance is safe on their ledger; this panel just needs them online.</div>`}
    </div>

    <div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">⛏️ Mine for ${esc(home)} ${hlp('Your rigs point at THEIR node, so the blocks you find grow their chain — the one your wallet is on. If they have the buddy-share pool on, you earn a proportional cut of every block your shares helped find.')}</div>
      <div style="font-size:.78rem;color:var(--muted);line-height:1.7;">
        Mining is disabled on this node by design — a rig here would found the island you chose not to make.
        Point it at ${esc(home)}'s node with the rig token they gave you:
      </div>
      <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:10px;">
        <div class="field" style="margin:0;flex:1;min-width:170px;"><label>${esc(home)}'s node URL</label>
          <input id="jly-join-url" oninput="jellyJoinCmd()" value="${esc(peer.base_url || '')}" placeholder="http://their-host:8787"></div>
        <div class="field" style="margin:0;"><label>Their rig token</label>
          <input id="jly-join-token" oninput="jellyJoinCmd()" placeholder="paste their token" style="width:150px;"></div>
        <div class="field" style="margin:0;"><label>My rig name</label>
          <input id="jly-join-rig" oninput="jellyJoinCmd()" value="rig1" style="width:110px;"></div>
      </div>
      <div style="font-size:.76rem;color:var(--muted);line-height:1.8;margin-top:10px;">
        <code id="jly-join-cmd" style="word-break:break-all;"></code>
        <button class="btn-sm" style="margin-left:6px;" onclick="jellyJoinCopy()">📋 Copy</button>
      </div>
    </div>

    <div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">🏠 Leave and found my own chain</div>
      <div style="font-size:.78rem;color:var(--muted);line-height:1.7;">
        Founding your own chain writes a fresh genesis here and makes this store its own network — a
        <b>separate coin</b> from ${esc(home)}'s. What you've earned on their chain stays on their chain.
      </div>
      <button class="btn-sm" style="margin-top:8px;" onclick="jellySetMode('host')">🏠 Found my own chain</button>
    </div>

    <!-- Peers (the JLY peer network) — moved here from Settings → Integrations. -->
    <div class="settings-group" style="margin-bottom:16px;" id="peers-slot">
      <div class="settings-group-title">🤝 Peers</div>
      <div style="font-size:.78rem;color:var(--muted);">Loading&hellip;</div>
    </div>`;

  jellyJoinCmd();
  if (typeof loadPeers === 'function') loadPeers();   // fill the 🤝 Peers slot (settings-peers.js)
}

async function cryptoLoadJelly() {
  const pane = document.getElementById('pane-crypto-jelly');
  let st, wal, tok, nfts, missions, blocks, ws, pb, stats, pool, buddies, mine, yld, mpol, mdef, sup;
  // Mode first: a JOINED node founded no chain, so the chain endpoints below have
  // nothing to answer with. Render the participant view and skip them entirely.
  try {
    st = await api('/api/jelly/status');
  } catch (e) {
    pane.innerHTML = `<div class="empty"><div class="empty-icon">&#10060;</div>${esc(e.message)}</div>`;
    return;
  }
  if (st.mode === 'joined') return _jlyRenderJoined(pane, st);

  try {
    [wal, tok, nfts, missions, blocks, ws, pb, stats, pool, buddies, mine, yld, mpol, mdef, sup] = await Promise.all([
      api('/api/jelly/wallets'), api('/api/jelly/miner-token'),
      api('/api/jelly/nft/list'), api('/api/jelly/missions'), api('/api/jelly/blocks?limit=8'),
      api('/api/world/settings').catch(() => ({ settings: {} })),
      api('/api/jelly/peer-billing').catch(() => null),
      api('/api/jelly/stats').catch(() => null),
      api('/api/jelly/pool').catch(() => null),
      api('/api/peers').catch(() => null),
      api('/api/peers/my-wallets').catch(() => null),
      api('/api/jelly/miner-yield').catch(() => null),
      api('/api/jelly/miner-policy').catch(() => null),
      api('/api/jelly/miner-defense').catch(() => null),
      api('/api/jelly/supply').catch(() => null),
    ]);
  } catch (e) {
    pane.innerHTML = `<div class="empty"><div class="empty-icon">&#10060;</div>${esc(e.message)}</div>`;
    return;
  }
  _JLY.st = st;
  const companyOn = String((ws.settings || {}).world_crypto_mining_enabled) === '1';
  const rigs = st.miners || [];
  const paired = ((buddies || {}).peers || []).filter(p => p.status === 'approved');
  _JLY.paired = paired;

  pane.innerHTML = `
    <div class="section-header"><div><div class="section-title">🪼 JellyCoin (${esc(st.symbol)})</div>
      <div class="section-sub">Acme's own token. New JLY exists <b>only</b> when a real GPU solves a proof-of-work
      block — old cards get a second life, and there is deliberately no CPU mining. Community token, not an investment.</div></div>
      <button class="btn-sm" onclick="_cryptoLoaded.jelly=false;cryptoSub('jelly')">&#8635; Refresh</button>
    </div>

    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;">
      ${_jlyStat('Height', st.height)}
      ${_jlyStat('Supply', _jlyFmt(st.supply * 1e6) + ' JLY')}
      ${_jlyStat('Difficulty', st.difficulty, 'Relative to genesis (1.0). Auto-retargets toward one block per minute.')}
      ${_jlyStat('Block reward', st.block_reward + ' JLY')}
      ${_jlyStat('GPU rigs online', st.miners_online)}
      ${_jlyStat('Boosts pending', st.boosts_pending, 'Skilling tickets waiting to pay out inside the next mined blocks.')}
      ${_jlyStat('NFTs', st.nft_count)}
    </div>

    ${_jlySupplyPanel(sup)}

    ${stats && (stats.series || []).length > 2 ? `<div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">📊 Network graphs ${hlp('Derived live from the block table. Hover any chart for per-block values. Difficulty retargets every 20 blocks toward the dashed 60-second block goal.')}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;">
        <div><div style="font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Difficulty</div>
          ${_jlyChart(stats.series, stats.series.map(s => s.difficulty), { color: '#6c63ff', fmt: _jlyChartInt })}</div>
        <div><div style="font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Block interval (sec)</div>
          ${_jlyChart(stats.series, stats.series.map(s => s.interval), { color: '#f59e0b', fmt: _jlyChartInt, refY: stats.target_block_sec, refLabel: 'target ' + stats.target_block_sec + 's' })}</div>
        <div><div style="font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Supply (JLY)</div>
          ${_jlyChart(stats.series, stats.series.map(s => s.supply), { color: '#00d4aa', fmt: _jlyChartInt })}</div>
        <div><div style="font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Blocks by rig</div>
          ${_jlyBars(stats.per_rig || [])}</div>
      </div>
    </div>` : ''}

    <div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">⛏️ GPU rigs ${hlp('Any LAN box with an OpenCL GPU can mine — even cards far too old for AI. The miner refuses to run on CPU by design. Rigs are just a set: adding a third or fourth card is an install, never a code change.')}</div>
      ${_jlyDefenseBanner(mdef)}
      ${_jlyYieldPanel(yld)}
      ${_jlySchedPanel(mpol)}
      ${_jlyAgentPanel(mpol)}
      ${_jlyRigsTable(rigs, mpol)}
      <div style="font-size:.76rem;color:var(--muted);line-height:1.8;margin-top:10px;">
        <b>One line on any Linux box with a GPU</b> ${hlp('Installs just the miner — its own venv, the OpenCL loader, and a systemd service that survives reboots. Not the full node build: no ComfyUI, no LM Studio. Re-runnable any time; `install-miner.sh check` reports without changing anything.')}<br>
        <code style="word-break:break-all;">${esc(tok.install || '')}</code>
        <button class="btn-sm" style="margin-left:6px;" onclick="navigator.clipboard.writeText(${JSON.stringify(tok.install || '')});toast?.('Copied ✓ — run it on the GPU box')">📋 Copy</button>
        <div style="margin-top:10px;">
          <button class="btn-sm" onclick="jellyDeployMinerToNode()">🖥️ Install on my GPU node</button>
          <span style="font-size:.72rem;">&nbsp;pushes it over SSH to the configured node (throttled 50% so it fills the gaps around AI work)</span>
        </div>
        <div style="margin-top:8px;">
          <b>Second rig on this box's own idle GPU</b> ${hlp('The store box has a graphics card sitting near-idle while it serves the store, nginx and docker. This script turns it into a second rig at the lowest viable intensity — measured here at 26 MH/s for ~10% of the card, +2.5W and +2°C over idle, with store request latency unchanged. It refuses to install if no OpenCL GPU is present rather than leaving you a service that will not start. Intensity is server-side, so retune it in the table above without touching that box.')}<br>
          <code style="word-break:break-all;">./deploy/miner/install-local-rig.sh</code>
          <span style="font-size:.72rem;">&nbsp;· <code>check</code> to report, <code>uninstall</code> to remove</span>
        </div>
      </div>
      <details style="margin-top:10px;">
        <summary style="font-size:.74rem;color:var(--muted);cursor:pointer;">Manual install instead</summary>
        <div style="font-size:.76rem;color:var(--muted);line-height:1.8;margin-top:6px;">
          1&#41; On the GPU box: <code>pip install pyopencl numpy requests</code><br>
          2&#41; Download <a href="/api/jelly/mining/miner.py" style="color:var(--accent,#7aa2ff);">jellyminer.py</a> &nbsp;
          3&#41; Run: <code style="word-break:break-all;">${esc(tok.run)}</code>
          <button class="btn-sm" style="margin-left:6px;" onclick="navigator.clipboard.writeText(${JSON.stringify(tok.run)});toast?.('Copied ✓')">📋 Copy</button>
        </div>
      </details>
    </div>

    ${_jlyDefensePanel(mdef)}

    <div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">🏢 Company boosts
        <span style="font-size:.62rem;font-weight:700;background:${companyOn ? 'rgba(34,197,94,.16)' : 'rgba(148,163,184,.16)'};color:${companyOn ? 'var(--green)' : 'var(--muted)'};border-radius:10px;padding:2px 8px;margin-left:8px;text-transform:uppercase;">${companyOn ? 'on' : 'off'}</span>
      </div>
      <div style="font-size:.78rem;color:var(--muted);line-height:1.7;">
        When on, agents' woodcutting / mining / fishing queues boost tickets (${st.boosts_pending} pending,
        ${_jlyFmt(st.boosts_paid_jly * 1e6)} JLY paid so far). Tickets cash out <b>only inside a real GPU-mined
        block</b> — bonus JLY split between the agent and the company wallet. No rig online → nothing mines.
      </div>
      <button class="btn-sm" style="margin-top:8px;" onclick="jellyToggleCompany(${companyOn ? 0 : 1})">
        ${companyOn ? '⏸ Turn off' : '▶ Turn on'} skilling boosts</button>
      <span style="font-size:.7rem;color:var(--muted);margin-left:8px;">(same toggle lives in God Console → Company Settings)</span>
    </div>

    ${pb ? `<div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">🤝 Buddy compute ${hlp('JLY meters the peer network\'s shared AI helper: a buddy\'s box doing LLM work for us EARNS their peer wallet JLY from the treasury; a buddy running jobs on our node SPENDS theirs. Broke buddies are comped, never blocked. Buddies see their balance via /api/peers/rpc/wallet.')}
        <span style="font-size:.62rem;font-weight:700;background:${pb.enabled ? 'rgba(34,197,94,.16)' : 'rgba(148,163,184,.16)'};color:${pb.enabled ? 'var(--green)' : 'var(--muted)'};border-radius:10px;padding:2px 8px;margin-left:8px;text-transform:uppercase;">${pb.enabled ? 'on' : 'off'}</span>
      </div>
      <div style="font-size:.78rem;color:var(--muted);line-height:1.7;">
        ${(pb.peer_wallets || []).length
          ? 'Buddy balances: ' + pb.peer_wallets.map(p => `<b>${esc(p.name.replace('peer:', ''))}</b> ${_jlyFmt(p.balance)} JLY`).join(' · ')
          : 'No buddy wallets yet — they appear the first time a paired peer runs or lends a job.'}
        ${pb.comped_jobs ? ` · ${pb.comped_jobs} job(s) comped so far` : ''}
      </div>
      <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:10px;">
        <div class="field" style="margin:0;"><label>JLY per LLM job ${hlp('Embedding jobs cost 1/10th of this.')}</label>
          <input id="jly-pb-price" type="number" step="0.1" min="0" value="${pb.price_jly}" style="width:90px;"></div>
        <button class="btn-sm" onclick="jellyPeerBilling(${pb.enabled ? 'false' : 'true'})">${pb.enabled ? '⏸ Turn off' : '▶ Turn on'} billing</button>
        <button class="btn-sm" onclick="jellyPeerBilling(${pb.enabled})">💾 Save price</button>
      </div>
    </div>` : ''}

    ${mine ? `<div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">🌐 My wallets on buddies' chains ${hlp('JellyCoin is per-node: each store runs its OWN chain. What you earn on a buddy\'s network — mining shares, code reviews, lending them AI — sits in your wallet on THEIR ledger, not in your Wallets list below. This panel asks each paired buddy for your balance with the key they issued you.')}
        <span style="font-size:.62rem;font-weight:700;background:rgba(148,163,184,.16);color:var(--muted);border-radius:10px;padding:2px 8px;margin-left:8px;">${mine.reachable}/${mine.peers} reachable</span>
      </div>
      ${(mine.wallets || []).length ? `
      <div style="font-size:.78rem;color:var(--muted);line-height:1.7;margin-bottom:8px;">
        Total earned away from home: <b style="color:var(--text);">${Number(mine.total_jly).toLocaleString(undefined, { maximumFractionDigits: 4 })} JLY</b>
        across ${mine.peers} paired ${mine.peers === 1 ? 'buddy' : 'buddies'}.
      </div>
      <table class="mini-table" style="width:100%;font-size:.78rem;">
        <tr><th style="text-align:left;">Buddy's network</th><th style="text-align:left;">My wallet there</th><th style="text-align:right;">Balance</th></tr>
        ${(mine.wallets || []).map(w => `<tr>
          <td>${esc(w.peer)}<div style="font-size:.66rem;color:var(--muted);">${esc(w.base_url || '')}</div></td>
          <td style="color:var(--muted);">${w.ok ? esc(w.wallet || '—') : `<span style="color:var(--muted);">offline — ${esc(w.error || 'unreachable')}</span>`}</td>
          <td style="text-align:right;font-weight:600;">${w.ok ? Number(w.balance_jly).toLocaleString(undefined, { maximumFractionDigits: 4 }) + ' ' + esc(w.symbol) : '—'}</td></tr>`).join('')}
      </table>
      <div style="font-size:.72rem;color:var(--muted);line-height:1.7;margin-top:8px;">
        These are separate chains, not one shared ledger — a balance here can't be spent on your own network.
        It buys AI compute and reviews <i>on that buddy's node</i>.
      </div>`
      : `<div style="font-size:.78rem;color:var(--muted);">No paired buddies yet. Pair one in the <b>🤝 Peers</b> section below, then your balance on their chain shows up here.</div>`}
    </div>` : ''}

    <div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">🔗 Join a buddy's network ${hlp('Mining on YOUR node mints YOUR coin — a chain of your own. To grow a buddy\'s network instead, point the same miner at THEIR url with a rig token they hand you. Their node then pays your wallet on their chain (see the panel above).')}</div>
      <div style="font-size:.78rem;color:var(--muted);line-height:1.7;">
        Every store runs its own chain, so mining here grows <b>your</b> JLY. Pointing a rig at a buddy's node instead
        makes you a miner on <b>theirs</b> — if they've turned the buddy-share pool on, you earn a proportional cut of
        every block your shares helped find.
      </div>
      <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:10px;">
        <div class="field" style="margin:0;"><label>Buddy's node ${hlp('Paired buddies from the 🤝 Peers section below. Pick "custom" to type any node URL.')}</label>
          <select id="jly-join-peer" onchange="jellyJoinCmd()" style="min-width:150px;">
            ${paired.length ? paired.map(p => `<option value="${esc(p.base_url || '')}">${esc(p.name)}</option>`).join('') : ''}
            <option value="">— custom URL —</option>
          </select></div>
        <div class="field" style="margin:0;flex:1;min-width:170px;"><label>Node URL</label>
          <input id="jly-join-url" oninput="jellyJoinCmd()" placeholder="http://their-host:8787"
                 value="${esc((paired[0] || {}).base_url || '')}"></div>
        <div class="field" style="margin:0;"><label>Their rig token ${hlp('The X-Jelly-Token from THEIR store (Crypto → JellyCoin → Mining). They hand it to you out of band — it only lets you donate hashpower, never read or move their funds.')}</label>
          <input id="jly-join-token" oninput="jellyJoinCmd()" placeholder="paste their token" style="width:150px;"></div>
        <div class="field" style="margin:0;"><label>My rig name ${hlp('Ask them to map this rig to your peer:<name> wallet in their pool panel, so your share of each block lands in your wallet on their chain.')}</label>
          <input id="jly-join-rig" oninput="jellyJoinCmd()" placeholder="rig1" value="rig1" style="width:110px;"></div>
      </div>
      <div style="font-size:.76rem;color:var(--muted);line-height:1.8;margin-top:10px;">
        Run on your GPU box: <code id="jly-join-cmd" style="word-break:break-all;">python3 jellyminer.py --url &lt;their url&gt; --token &lt;their token&gt; --name rig1</code>
        <button class="btn-sm" style="margin-left:6px;" onclick="jellyJoinCopy()">📋 Copy</button>
      </div>
      <div style="font-size:.72rem;color:var(--muted);line-height:1.7;margin-top:8px;">
        Same <a href="/api/jelly/mining/miner.py" style="color:var(--accent,#7aa2ff);">jellyminer.py</a> either way — only
        <code>--url</code> changes. Ask your buddy to map your rig to your <code>peer:&lt;name&gt;</code> wallet on their
        side, or your shares pay their default rig wallet instead of you.
      </div>

      <div style="border-top:1px solid var(--border,rgba(148,163,184,.2));margin-top:14px;padding-top:12px;">
        <div style="font-size:.78rem;color:var(--muted);line-height:1.7;">
          <b>Or make their network your home.</b> Right now this store runs its own chain — your JLY and theirs are
          <b>separate coins</b> that can never add up. Joining retires this chain and makes you a participant on
          theirs, so there's one growing network instead of one per install.
        </div>
        <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:10px;">
          <div class="field" style="margin:0;"><label>Home network ${hlp('The paired buddy whose chain becomes your ledger. Only offered while your own chain is unused — once you have mined or moved coins, joining would strand them.')}</label>
            <select id="jly-mode-home" style="min-width:150px;">
              ${paired.map(p => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join('')}
            </select></div>
          <button class="btn-sm" onclick="jellySetMode('joined')" ${paired.length ? '' : 'disabled'}>🔗 Join this network</button>
        </div>
        ${paired.length ? '' : `<div style="font-size:.72rem;color:var(--muted);margin-top:6px;">Pair a buddy in the <b>🤝 Peers</b> section below first.</div>`}
      </div>
    </div>

    <!-- Peers (the JLY peer network) — moved here from Settings → Integrations.
         settings-peers.js owns loadPeers() + every peer handler; it fills this slot. -->
    <div class="settings-group" style="margin-bottom:16px;" id="peers-slot">
      <div class="settings-group-title">🤝 Peers</div>
      <div style="font-size:.78rem;color:var(--muted);">Loading&hellip;</div>
    </div>

    ${pool ? `<div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">🤝 Buddy-Share Mining Pool ${hlp('When ON, every GPU-mined block is split proportionally by the shares each rig contributed this round, instead of winner-take-all. Map a buddy\'s rig to their peer:<name> wallet and their share of every block flows straight to it — the fair way to mine together with the friends you paired in the Peers/federation tab.')}
        <span style="font-size:.62rem;font-weight:700;background:${pool.enabled ? 'rgba(34,197,94,.16)' : 'rgba(148,163,184,.16)'};color:${pool.enabled ? 'var(--green)' : 'var(--muted)'};border-radius:10px;padding:2px 8px;margin-left:8px;text-transform:uppercase;">${pool.enabled ? 'on' : 'off'}</span>
      </div>
      <div style="font-size:.78rem;color:var(--muted);line-height:1.7;">
        <b>${pool.enabled ? 'ON — proportional share split' : 'OFF — winner-take-all mining'}.</b>
        ${pool.enabled
          ? 'Each block\'s reward is divided by the shares every rig submitted this round, then paid to each rig\'s mapped owner wallet.'
          : 'Whoever solves the block keeps its whole reward. Turn this on to share rewards fairly across your buddies\' rigs.'}
      </div>
      <button class="btn-sm" style="margin-top:8px;" onclick="jellyPoolToggle(${pool.enabled ? 'false' : 'true'})">
        ${pool.enabled ? '⏸ Turn off' : '▶ Turn on'} share splitting</button>

      <div style="display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 4px;">
        ${_jlyStat('Round', '#' + pool.round_id)}
        ${_jlyStat('Block reward', pool.block_reward_jly + ' JLY')}
        ${_jlyStat('Share factor', pool.share_factor, 'How many shares a rig is credited per unit of proof-of-work it submits toward the current round.')}
      </div>

      <div style="font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin:12px 0 4px;">Shares this round</div>
      ${(pool.shares_by_rig || []).length ? `<table class="mini-table" style="width:100%;font-size:.78rem;">
        <tr><th style="text-align:left;">Rig</th><th style="text-align:left;">Owner wallet</th><th>Shares</th><th style="text-align:right;">Projected split</th></tr>
        ${(pool.shares_by_rig || []).map(r => `<tr><td>${esc(r.rig)}</td>
          <td style="color:var(--muted);">${esc(r.owner || '—')}</td>
          <td style="text-align:center;">${(r.shares || 0).toLocaleString()}</td>
          <td style="text-align:right;font-weight:600;">${(pool.projected_split || {})[r.owner] != null ? Number((pool.projected_split || {})[r.owner]).toLocaleString(undefined, { maximumFractionDigits: 4 }) + ' JLY' : '—'}</td></tr>`).join('')}
      </table>` : `<div style="font-size:.78rem;color:var(--muted);">No shares yet this round — buddies' rigs will appear here once they mine.</div>`}

      <div style="font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin:14px 0 4px;">Add / map a buddy rig</div>
      <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;">
        <div class="field" style="margin:0;"><label>Rig ${hlp('Pick a rig that has already mined, or type the --name a buddy will start their miner with.')}</label>
          <input id="jly-pool-rig" list="jly-pool-riglist" placeholder="rig name" style="width:150px;">
          <datalist id="jly-pool-riglist">${(rigs || []).map(m => `<option value="${esc(m.name)}">`).join('')}</datalist></div>
        <div class="field" style="margin:0;flex:1;min-width:160px;"><label>Owner wallet ${hlp('peer:<buddyName> routes rewards to a paired buddy\'s wallet; miner:<rig> keeps them on your own rig wallet.')}</label>
          <input id="jly-pool-owner" placeholder="peer:willie" value="peer:"></div>
        <button class="btn-sm" onclick="jellyPoolMap()">💾 Save mapping</button>
      </div>
      <div style="font-size:.72rem;color:var(--muted);line-height:1.7;margin-top:8px;">
        A buddy joins by running <code style="word-break:break-all;">jellyminer.py --url http://127.0.0.1:8787 --token &lt;X-Jelly-Token from the Mining section above&gt; --name &lt;rigName&gt;</code>.
        Their rig then shows up here — map it to their <code>peer:&lt;name&gt;</code> wallet (a peer you paired in the Peers/federation tab) and every block's share flows to them.
      </div>

      ${(pool.recent_payouts || []).length ? `
      <div style="font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin:14px 0 4px;">Recent pool payouts</div>
      <table class="mini-table" style="width:100%;font-size:.74rem;">
        <tr><th>#</th><th style="text-align:left;">Owner</th><th style="text-align:right;">JLY</th></tr>
        ${(pool.recent_payouts || []).slice(0, 12).map(p => `<tr><td style="text-align:center;">${p.height}</td>
          <td>${esc(p.dst)}</td><td style="text-align:right;font-weight:600;">${_jlyFmt(p.amount)}</td></tr>`).join('')}
      </table>` : ''}
    </div>` : ''}

    <div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">👛 Wallets</div>
      <table class="mini-table" style="width:100%;font-size:.78rem;">
        <tr><th style="text-align:left;">Wallet</th><th style="text-align:left;">Kind</th><th style="text-align:right;">Balance</th></tr>
        ${(wal.wallets || []).slice(0, 14).map(w => `<tr><td>${esc(w.name)} ${w.name === 'assistant' ? '🤖' : ''}</td>
          <td style="color:var(--muted);">${esc(w.kind)}</td>
          <td style="text-align:right;font-weight:600;">${_jlyFmt(w.balance)} JLY</td></tr>`).join('')}
      </table>
      <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:12px;">
        <div class="field" style="margin:0;"><label>From</label><input id="jly-tx-from" placeholder="treasury" style="width:130px;"></div>
        <div class="field" style="margin:0;"><label>To</label><input id="jly-tx-to" placeholder="miner:rig1" style="width:130px;"></div>
        <div class="field" style="margin:0;"><label>JLY</label><input id="jly-tx-amt" type="number" step="0.01" style="width:80px;"></div>
        <div class="field" style="margin:0;flex:1;min-width:120px;"><label>Memo</label><input id="jly-tx-memo"></div>
        <button class="btn-sm" onclick="jellyTransfer()">💸 Send</button>
        <button class="btn-sm" onclick="jellyTip()" title="Send from the AI friend's 'assistant' wallet">🤖 Tip from AI friend</button>
      </div>
    </div>

    <div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">🖼️ Art NFTs ${hlp('Mints a real art file: its sha256 becomes the on-chain content hash. Fee: 5 JLY to the treasury (treasury mints free).')}</div>
      <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-bottom:12px;">
        <div class="field" style="margin:0;flex:2;min-width:220px;"><label>Art file path</label>
          <input id="jly-nft-path" placeholder="designs/…png (from Studio / Library)"></div>
        <div class="field" style="margin:0;flex:1;"><label>Title</label><input id="jly-nft-title"></div>
        <div class="field" style="margin:0;"><label>Owner</label><input id="jly-nft-owner" placeholder="treasury" style="width:110px;"></div>
        <button class="btn-sm" onclick="jellyMintNft()">🪙 Mint</button>
      </div>
      ${(nfts.nfts || []).length ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;">
        ${(nfts.nfts || []).slice(0, 12).map(n => `<div style="background:var(--panel2,#0b1120);border:1px solid var(--border,#243049);border-radius:10px;padding:8px;">
          <img src="${thumbUrl(n.file_path)}" onerror="this.style.display='none'" style="width:100%;border-radius:6px;aspect-ratio:1;object-fit:cover;">
          <div style="font-size:.74rem;font-weight:600;margin-top:6px;">${esc(n.title)}</div>
          <div style="font-size:.66rem;color:var(--muted);">owner: ${esc(n.owner)} · #${n.minted_height}</div>
          <div style="font-size:.6rem;color:var(--muted);word-break:break-all;" title="content sha256: ${esc(n.sha256)}">${esc(n.token_id)}</div>
        </div>`).join('')}</div>` : `<div style="font-size:.76rem;color:var(--muted);">Nothing minted yet — pick a Studio artwork you love.</div>`}
    </div>

    <div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">📣 Push &amp; sell missions ${hlp('The Company drafts JLY promo/perk/sell pitches with the LLM. Every draft waits for YOUR approval — agents never post or sell anything on their own. Approved pitches hit the town feed for agents to talk up.')}</div>
      <div style="display:flex;gap:8px;margin-bottom:10px;">
        <button class="btn-sm" onclick="jellyDraft('promo')">✍️ Draft promo</button>
        <button class="btn-sm" onclick="jellyDraft('perk')">🎁 Draft store perk</button>
        <button class="btn-sm" onclick="jellyDraft('sell')">🤝 Draft sell offer</button>
      </div>
      ${(missions.missions || []).slice(0, 8).map(m => `
        <div style="border:1px solid var(--border,#243049);border-radius:10px;padding:10px 12px;margin-bottom:8px;background:var(--panel2,#0b1120);">
          <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;">
            <div style="font-weight:600;font-size:.82rem;">${esc(m.title)} <span style="color:var(--muted);font-weight:400;">· ${esc(m.kind)} · ${esc(m.agent)}</span></div>
            <div>${m.status === 'proposed'
              ? `<button class="btn-sm" onclick="jellyDecide(${m.id},1)">✅ Approve</button>
                 <button class="btn-sm" onclick="jellyDecide(${m.id},0)">🚫 Reject</button>`
              : `<span style="font-size:.66rem;font-weight:700;text-transform:uppercase;color:${m.status === 'approved' ? 'var(--green)' : 'var(--red)'};">${esc(m.status)}</span>`}</div>
          </div>
          <div style="font-size:.76rem;color:var(--muted);white-space:pre-wrap;margin-top:6px;">${esc(m.pitch)}</div>
        </div>`).join('') || `<div style="font-size:.76rem;color:var(--muted);">No missions yet.</div>`}
    </div>

    <div class="settings-group" style="margin-bottom:16px;">
      <div class="settings-group-title">📜 Docs, security &amp; backups ${hlp('The white paper defines the coin (PoW spec, tokenomics, what it is and is not); the security doc covers the rig-token protocol, work integrity, and the incident playbook. Chain state lives in the store DB, so Settings → Backups snapshots cover it; the rig token ships in the Crypto key-backup zip.')}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button class="btn-sm" onclick="jellyShowDoc('whitepaper','📜 JellyCoin White Paper')">📜 White paper</button>
        <button class="btn-sm" onclick="jellyShowDoc('security','🛡️ Security protocols & backups')">🛡️ Security &amp; backups</button>
        <a class="btn-sm" style="text-decoration:none;" href="https://github.com/youruser/jellycoin-core" target="_blank">🌐 Public core repo</a>
      </div>
      <div id="jly-doc-panel" style="display:none;margin-top:12px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
          <div id="jly-doc-title" style="font-weight:700;font-size:.85rem;"></div>
          <button class="btn-sm" onclick="document.getElementById('jly-doc-panel').style.display='none'">✕ Close</button>
        </div>
        <pre id="jly-doc-body" style="white-space:pre-wrap;font-size:.74rem;line-height:1.6;color:var(--text);background:var(--surface,#161a22);border:1px solid var(--border,#2a2f3d);border-radius:10px;padding:14px;max-height:420px;overflow-y:auto;"></pre>
      </div>
    </div>

    <div class="settings-group">
      <div class="settings-group-title">⛓️ Recent blocks</div>
      <table class="mini-table" style="width:100%;font-size:.74rem;">
        <tr><th>#</th><th style="text-align:left;">Hash</th><th style="text-align:left;">Miner</th><th>Reward</th><th>Boost</th></tr>
        ${(blocks.blocks || []).map(b => `<tr><td style="text-align:center;">${b.height}</td>
          <td style="font-family:monospace;color:var(--muted);">${esc(b.hash.slice(0, 20))}…</td>
          <td>${esc(b.miner)}</td><td style="text-align:center;">${_jlyFmt(b.reward)}</td>
          <td style="text-align:center;">${b.boost ? _jlyFmt(b.boost) : '—'}</td></tr>`).join('')}
      </table>
    </div>`;

  jellyJoinCmd();   // fill the join command from the pre-selected buddy
  if (typeof loadPeers === 'function') loadPeers();   // fill the 🤝 Peers slot (settings-peers.js)
}
window.cryptoLoadJelly = cryptoLoadJelly;

function _jlyReload() { _cryptoLoaded.jelly = false; cryptoSub('jelly'); }

async function jellyToggleCompany(on) {
  try {
    await api('/api/world/settings', { method: 'POST', body: JSON.stringify({ settings: { world_crypto_mining_enabled: String(on) } }) });
    toast?.(on ? 'Company skilling now boosts GPU mining ⛏️' : 'Skilling boosts off');
    _jlyReload();
  } catch (e) { toast?.(e.message); }
}
window.jellyToggleCompany = jellyToggleCompany;

async function jellyYieldSave(enabled) {
  const body = {
    settle_sec: +document.getElementById('jly-yield-settle').value,
    retry_sec: +document.getElementById('jly-yield-retry').value,
  };
  if (enabled !== undefined) body.enabled = enabled;
  try {
    const r = await api('/api/jelly/miner-yield', { method: 'POST', body: JSON.stringify(body) });
    toast?.(r.enabled ? 'Mining yields to the AI queue ⛏️⏸' : 'Mining now runs through AI work');
    _jlyReload();
  } catch (e) { toast?.(e.message); }
}
window.jellyYieldSave = jellyYieldSave;

/* ── the owner's envelope: schedule, per-rig intensity, agent bounds ── */
async function _jlyPolicyPost(body, msg) {
  try {
    await api('/api/jelly/miner-policy', { method: 'POST', body: JSON.stringify(body) });
    toast?.(msg);
    _jlyReload();
  } catch (e) { toast?.(e.message); }
}

async function jellySchedSave(enabled) {
  const body = {
    windows: document.getElementById('jly-sched-win').value.trim(),
    daily_hours: +document.getElementById('jly-sched-hrs').value || 0,
  };
  if (enabled !== undefined) body.sched_enabled = enabled;
  await _jlyPolicyPost(body, enabled === false ? 'Mining hours off — rigs mine whenever the queue is free'
                                               : 'Mining hours saved 🕒');
}
window.jellySchedSave = jellySchedSave;

async function jellyRigSave(i) {
  const name = (_JLY.rigRows || [])[i];
  if (!name) return;
  await _jlyPolicyPost({ rigs: { [name]: {
    throttle: +document.getElementById('jly-thr-' + i).value,
    batch: +document.getElementById('jly-bat-' + i).value,
    cost: document.getElementById('jly-cost-' + i).value,
  } } }, `${name}: intensity sent — it lands on the rig within a few seconds ⛏️`);
}
window.jellyRigSave = jellyRigSave;

async function jellyAgentSave(extra) {
  const body = Object.assign({
    agent_min_throttle: +document.getElementById('jly-agent-thr').value,
    agent_max_pause_min: +document.getElementById('jly-agent-pause').value,
    agent_max_minutes: +document.getElementById('jly-agent-min').value,
  }, extra || {});
  await _jlyPolicyPost(body, 'Company mining bounds saved 🏢');
}
window.jellyAgentSave = jellyAgentSave;

async function jellyDefenseSave(extra) {
  const g = id => document.getElementById(id);
  const body = Object.assign(g('jly-def-warn') ? {
    warn_pct: +g('jly-def-warn').value, act_pct: +g('jly-def-act').value,
    clear_pct: +g('jly-def-clear').value, settle_min: +g('jly-def-settle').value,
    window_blocks: +g('jly-def-window').value, my_rigs: g('jly-def-rigs').value.trim(),
  } : {}, extra || {});
  try {
    const r = await api('/api/jelly/miner-defense', { method: 'POST', body: JSON.stringify(body) });
    toast?.(r.enabled ? (r.engaged ? '🛡️ Defence engaged' : 'Chain defence saved 🛡️')
                      : 'Chain defence OFF — the chain will not defend itself');
    _jlyReload();
  } catch (e) { toast?.(e.message); }
}
window.jellyDefenseSave = jellyDefenseSave;

async function jellyShowDoc(name, title) {
  try {
    const r = await fetch(API + '/api/jelly/doc/' + name);   // same base the api() helper uses
    if (!r.ok) throw new Error('doc fetch failed: ' + r.status);
    document.getElementById('jly-doc-title').textContent = title;
    document.getElementById('jly-doc-body').textContent = await r.text();
    document.getElementById('jly-doc-panel').style.display = '';
    document.getElementById('jly-doc-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (e) { toast?.(e.message); }
}
window.jellyShowDoc = jellyShowDoc;

async function jellyPeerBilling(enabled) {
  const body = { enabled: !!enabled,
    price_jly: parseFloat(document.getElementById('jly-pb-price')?.value || '1') };
  try {
    await api('/api/jelly/peer-billing', { method: 'POST', body: JSON.stringify(body) });
    toast?.('Buddy compute billing saved ✓'); _jlyReload();
  } catch (e) { toast?.(e.message); }
}
window.jellyPeerBilling = jellyPeerBilling;

async function jellyPoolToggle(enabled) {
  try {
    await api('/api/jelly/pool', { method: 'POST', body: JSON.stringify({ enabled: !!enabled }) });
    toast?.(enabled ? 'Buddy-share pool ON — blocks split by shares 🤝' : 'Pool off — back to winner-take-all');
    _jlyReload();
  } catch (e) { toast?.(e.message); }
}
window.jellyPoolToggle = jellyPoolToggle;

async function jellyPoolMap() {
  const rig = document.getElementById('jly-pool-rig')?.value.trim();
  const owner = document.getElementById('jly-pool-owner')?.value.trim();
  if (!rig || !owner) { toast?.('Give a rig name and an owner wallet'); return; }
  try {
    await api('/api/jelly/pool', { method: 'POST', body: JSON.stringify({ owners: { [rig]: owner } }) });
    toast?.(`Mapped ${rig} → ${owner} ✓`); _jlyReload();
  } catch (e) { toast?.(e.message); }
}
window.jellyPoolMap = jellyPoolMap;

// ── Join a buddy's network: build the miner command for THEIR node ───────────
// The rig token is theirs to hand out (it only donates hashpower), so we never
// fetch it — the user pastes what their buddy gave them.
function _jellyJoinCmdText() {
  const url = (document.getElementById('jly-join-url')?.value || '').trim();
  const token = (document.getElementById('jly-join-token')?.value || '').trim();
  const rig = (document.getElementById('jly-join-rig')?.value || '').trim() || 'rig1';
  return `python3 jellyminer.py --url ${url || '<their url>'} `
       + `--token ${token || '<their token>'} --name ${rig}`;
}

function jellyJoinCmd() {
  const sel = document.getElementById('jly-join-peer');
  const urlBox = document.getElementById('jly-join-url');
  // picking a paired buddy fills the URL; "custom" leaves whatever is typed
  if (sel && urlBox && document.activeElement === sel && sel.value) urlBox.value = sel.value;
  const el = document.getElementById('jly-join-cmd');
  if (el) el.textContent = _jellyJoinCmdText();
}
window.jellyJoinCmd = jellyJoinCmd;

function jellyJoinCopy() {
  navigator.clipboard.writeText(_jellyJoinCmdText());
  toast?.('Copied ✓ — run it on the GPU box');
}
window.jellyJoinCopy = jellyJoinCopy;

// Install the miner on the configured GPU node over SSH — the miner alone, not
// the full node build (/api/node/deploy does that).
async function jellyDeployMinerToNode() {
  if (!confirm('Install the JellyCoin miner on your GPU node?\n\n'
             + 'Pushes install-miner.sh over SSH and runs it there: its own venv, the '
             + 'OpenCL loader, and a systemd service throttled to 50% so it fills the gaps '
             + 'around AI work. Existing AI stack is not touched.')) return;
  try {
    const r = await api('/api/node/deploy-miner', { method: 'POST', body: JSON.stringify({ throttle: '50' }) });
    toast?.(`Installing on the node (${r.url}) — watch the deploy log`);
  } catch (e) { toast?.(e.message); }
}
window.jellyDeployMinerToNode = jellyDeployMinerToNode;

// Switch between hosting our own chain and participating on a buddy's. The
// backend refuses to join once our chain has been used, so surface that plainly.
async function jellySetMode(mode) {
  const home = mode === 'joined' ? (document.getElementById('jly-mode-home')?.value || '') : '';
  if (mode === 'joined' && !home) { toast?.('Pick which buddy\'s network to join'); return; }
  const msg = mode === 'joined'
    ? `Join ${home}'s network?\n\nThis store's own chain is retired — its genesis and premine go away, `
      + `and mining here is disabled. Your JLY will live in your wallet on ${home}'s ledger.`
    : 'Found your own chain?\n\nThis store becomes its own network with a fresh genesis — a separate coin '
      + 'from the one you were on. Anything you earned there stays there.';
  if (!confirm(msg)) return;
  try {
    const r = await api('/api/jelly/mode', { method: 'POST', body: JSON.stringify({ mode, home_peer: home }) });
    toast?.(r.mode === 'joined' ? `Joined ${r.home_peer}'s network ✓` : 'Now hosting your own chain ✓');
    _jlyReload();
  } catch (e) { toast?.(e.message); }
}
window.jellySetMode = jellySetMode;

async function jellyTransfer(fromOverride) {
  const from = fromOverride || document.getElementById('jly-tx-from').value.trim();
  const body = { from, to: document.getElementById('jly-tx-to').value.trim(),
    amount_jly: parseFloat(document.getElementById('jly-tx-amt').value || '0'),
    memo: document.getElementById('jly-tx-memo').value.trim() };
  try {
    await api('/api/jelly/transfer', { method: 'POST', body: JSON.stringify(body) });
    toast?.('Sent ✓'); _jlyReload();
  } catch (e) { toast?.(e.message); }
}
window.jellyTransfer = jellyTransfer;

async function jellyTip() {
  const body = { to: document.getElementById('jly-tx-to').value.trim(),
    amount_jly: parseFloat(document.getElementById('jly-tx-amt').value || '0'),
    memo: document.getElementById('jly-tx-memo').value.trim() || 'tip from your AI friend' };
  try {
    await api('/api/jelly/tip', { method: 'POST', body: JSON.stringify(body) });
    toast?.('AI friend tipped ✓ 🤖'); _jlyReload();
  } catch (e) { toast?.(e.message); }
}
window.jellyTip = jellyTip;

async function jellyMintNft() {
  const body = { file_path: document.getElementById('jly-nft-path').value.trim(),
    title: document.getElementById('jly-nft-title').value.trim(),
    owner: document.getElementById('jly-nft-owner').value.trim() || 'treasury' };
  if (!body.file_path) { toast?.('Give it an art file path'); return; }
  try {
    const r = await api('/api/jelly/nft/mint', { method: 'POST', body: JSON.stringify(body) });
    toast?.(`Minted ${r.token_id} ✓`); _jlyReload();
  } catch (e) { toast?.(e.message); }
}
window.jellyMintNft = jellyMintNft;

async function jellyDraft(kind) {
  try {
    toast?.('Drafting with the LLM…');
    await api('/api/jelly/missions/draft', { method: 'POST', body: JSON.stringify({ kind }) });
    _jlyReload();
  } catch (e) { toast?.(e.message); }
}
window.jellyDraft = jellyDraft;

async function jellyDecide(id, approve) {
  try {
    await api(`/api/jelly/missions/${id}/decide`, { method: 'POST', body: JSON.stringify({ approve: !!approve }) });
    toast?.(approve ? 'Mission approved — the town hears about it 📣' : 'Mission rejected');
    _jlyReload();
  } catch (e) { toast?.(e.message); }
}
window.jellyDecide = jellyDecide;

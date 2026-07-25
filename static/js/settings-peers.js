/* ── PEERS (friend Store installs: shared reviews + lent compute) ──
   Renders into #peers-slot, which lives in the Jelly tab (tab-jellycoin.js,
   Crypto → JellyCoin) since the Peers network is the JLY peer network. */
async function loadPeers() {
  const el = document.getElementById('peers-slot');
  if (!el) return;
  let d;
  try { d = await api('/api/peers'); }
  catch (e) { el.innerHTML = `<div class="settings-group-title">&#129309; Peers</div><div style="font-size:.76rem;color:var(--warn);">${esc(e.message)}</div>`; return; }
  const peerRows = (d.peers || []).map(p => `
    <tr>
      <td style="padding:4px 8px;"><b>${esc(p.name)}</b><br><span style="font-size:.66rem;color:var(--muted);word-break:break-all;">${esc(p.base_url || 'no URL')}</span></td>
      <td style="padding:4px 8px;">${p.status === 'approved'
        ? '<span style="color:var(--green)">&#10003; approved</span>'
        : `<span style="color:var(--warn)">pending</span> <button class="btn-sm primary" onclick="peerApprove(${p.id})">Approve</button>`}
        <br><span style="font-size:.64rem;color:var(--muted);">last seen: ${esc(p.last_seen || 'never')}</span></td>
      <td style="padding:4px 8px;font-size:.72rem;">
        <label style="display:flex;gap:4px;align-items:center;cursor:pointer;"><input type="checkbox" ${p.accept_reviews ? 'checked' : ''} onchange="peerCfg(${p.id},'accept_reviews',this.checked)"> code reviews ${hlp('Let this peer send diffs for YOUR node to review: your local LLM reviews them and you can vote. They only ever see their own diff + the verdict.')}</label>
        <label style="display:flex;gap:4px;align-items:center;cursor:pointer;"><input type="checkbox" id="pw-llm-${p.id}" ${(p.accept_work && (p.work_kinds || 'llm,embedding').includes('llm')) ? 'checked' : ''} onchange="peerCfgWork(${p.id})"> LLM work ${hlp('Let this peer run text-generation jobs on YOUR unified queue at background priority — your own jobs always come first, and their jobs borrow whatever model you already have loaded (they can never swap your models). Their jobs carry THEIR prompts; they can never see or change your settings, prompts, or code.')}</label>
        <label style="display:flex;gap:4px;align-items:center;cursor:pointer;"><input type="checkbox" id="pw-emb-${p.id}" ${(p.accept_work && (p.work_kinds || 'llm,embedding').includes('embedding')) ? 'checked' : ''} onchange="peerCfgWork(${p.id})"> embeddings ${hlp('Let this peer run embedding jobs. These ride the LM Studio passthrough alongside your loaded chat model — cheap, and they never swap models.')}</label>
      </td>
      <td style="padding:4px 8px;white-space:nowrap;">
        <button class="btn-sm" onclick="peerPing(${p.id})" title="Ping — shows their branch/commit + recently promoted work">&#128246;</button>
        <button class="btn-sm" onclick="peerTestJob(${p.id})" title="Send a tiny test job to their queue">&#129514;</button>
        <button class="btn-sm danger" onclick="peerRevoke(${p.id})" title="Revoke — their key stops working immediately">&#10060;</button>
      </td>
    </tr>`).join('');
  const reviews = (d.incoming_reviews || []).filter(r => r.status !== 'error');
  const reviewRows = reviews.map(r => `
    <tr>
      <td style="padding:4px 8px;">#${r.id} <b>${esc(r.title || '')}</b><br><span style="font-size:.64rem;color:var(--muted);">${esc(r.created_at || '')}</span></td>
      <td style="padding:4px 8px;font-size:.72rem;">${r.status === 'done'
        ? `LLM: <b style="color:${r.llm_vote === 'approve' ? 'var(--green)' : 'var(--warn)'}">${esc(r.llm_vote || '?')}</b>`
        : esc(r.status)}</td>
      <td style="padding:4px 8px;">${r.human_vote
        ? `you: <b style="color:${r.human_vote === 'approve' ? 'var(--green)' : 'var(--warn)'}">${esc(r.human_vote)}</b>`
        : `<button class="btn-sm" onclick="peerReviewVote(${r.id},'approve')">&#128077;</button>
           <button class="btn-sm" onclick="peerReviewVote(${r.id},'reject')">&#128078;</button>`}</td>
    </tr>`).join('');
  el.innerHTML = `
    <div class="settings-group-title">&#129309; Peers ${hlp('Connect your Store to a friend’s Store: you review each other’s swarm changes (their local LLM + their human vote lands on your job as an advisory vote), share coarse progress, and — only if you allow it per-peer — lend each other queue compute. Peers can NEVER change anything on your node: no settings, prompts, code, or pushes. Pair: one side makes an invite key, the other Connects with it, then the first side Approves.')}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
      <button class="btn-sm primary" onclick="peerInvite()">&#128273; New invite key</button>
      <button class="btn-sm" onclick="peerConnectForm()">&#128279; Connect to a friend</button>
      <button class="btn-sm" onclick="peerConnInfo()">&#127760; My connection info</button>
      <button class="btn-sm" onclick="loadPeers()">&#8635; Refresh</button>
      ${d.open_invites ? `<span style="font-size:.72rem;color:var(--muted);align-self:center;">${d.open_invites} unused invite key(s)</span>` : ''}
    </div>
    <div id="peer-connect-slot"></div>
    ${(d.peers || []).length
      ? `<table style="width:100%;border-collapse:collapse;font-size:.78rem;"><tr style="color:var(--muted);text-align:left;"><th style="padding:4px 8px;">Peer</th><th style="padding:4px 8px;">Status</th><th style="padding:4px 8px;">Allow</th><th style="padding:4px 8px;"></th></tr>${peerRows}</table>`
      : '<div style="font-size:.76rem;color:var(--muted);">No peers yet. Make an invite key and send it to your friend, or Connect with one they sent you.</div>'}
    ${reviews.length ? `<div style="margin-top:12px;font-size:.8rem;"><b>&#128269; Reviews friends sent you</b>
      <table style="width:100%;border-collapse:collapse;font-size:.76rem;">${reviewRows}</table></div>` : ''}
    <div id="peer-out" style="margin-top:8px;font-size:.74rem;color:var(--muted);white-space:pre-wrap;"></div>
    <div id="peer-billing-slot" style="margin-top:14px;"></div>`;
  loadPeerBilling();
}
async function peerInvite() {
  const note = prompt('Note for this invite (who is it for)?', '') ?? '';
  try {
    const r = await api('/api/peers/invite', { method: 'POST', body: JSON.stringify({ note }) });
    prompt('Copy this ONE-TIME invite key and send it to your friend\n(along with this install\'s URL):', r.invite_key);
    loadPeers();
  } catch (e) { toast('Invite failed: ' + e.message, 'error'); }
}
function peerConnectForm() {
  document.getElementById('peer-connect-slot').innerHTML = `
    <div style="border:1px solid var(--border);border-radius:8px;padding:10px;margin-bottom:10px;">
      <div class="field"><label>Friend's name</label><input type="text" id="pc-name" placeholder="Buddy"></div>
      <div class="field"><label>Friend's Store URL ${hlp('The public URL of their install, INCLUDING any path prefix their reverse proxy uses — e.g. https://friend.example.com/store')}</label><input type="text" id="pc-url" placeholder="https://friend.example.com/store"></div>
      <div class="field"><label>Invite key (from your friend)</label><input type="password" id="pc-key" placeholder="pinv_&hellip;"></div>
      <button class="btn-sm primary" onclick="peerConnect()">&#128279; Connect</button>
    </div>`;
}
async function peerConnect() {
  const name = document.getElementById('pc-name').value.trim();
  const url = document.getElementById('pc-url').value.trim();
  const key = document.getElementById('pc-key').value.trim();
  if (!url || !key) { toast('URL and invite key are required', 'error'); return; }
  try {
    const r = await api('/api/peers/connect', { method: 'POST', body: JSON.stringify({ name, url, invite_key: key }) });
    toast(r.message || 'Paired ✓');
    loadPeers();
  } catch (e) { toast('Pairing failed: ' + e.message, 'error'); }
}
async function peerConnInfo() {
  const out = document.getElementById('peer-out');
  try {
    const c = await api('/api/peers/connection-info');
    out.innerHTML = `<div style="border:1px solid var(--border);border-radius:8px;padding:10px;line-height:1.8;">
      <b>&#127760; What your friend needs to reach this node</b><br>
      Public URL: <code>${esc(c.public_url || '(not set — see STORE_PUBLIC_URL in .env)')}</code><br>
      App port: <code>${esc(String(c.port))}</code>${c.lan_ip ? ` · LAN IP: <code>${esc(c.lan_ip)}</code>` : ''}<br>
      <span style="color:var(--muted);font-size:.72rem;">
        &bull; <b>Same network / VPN:</b> they can use <code>http://${esc(c.lan_ip || 'your-lan-ip')}:${esc(String(c.port))}</code> directly — easiest is a free mesh VPN (Tailscale/ZeroTier) so no router changes are needed.<br>
        &bull; <b>Over the internet:</b> either forward TCP port ${esc(String(c.port))} on your router to this machine (then give them http://your-public-ip:${esc(String(c.port))}), or better, put it behind your reverse proxy with HTTPS and give them the public URL above <b>including any /store path prefix</b>.<br>
        &bull; Peer traffic is key-authenticated either way, but prefer VPN or HTTPS so the keys aren't sent in the clear.
      </span></div>`;
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
async function peerCfgWork(id) {
  const llm = document.getElementById('pw-llm-' + id).checked;
  const emb = document.getElementById('pw-emb-' + id).checked;
  const kinds = [llm ? 'llm' : null, emb ? 'embedding' : null].filter(Boolean).join(',');
  try {
    await api(`/api/peers/${id}/config`, { method: 'POST',
      body: JSON.stringify({ accept_work: !!kinds, work_kinds: kinds || 'llm,embedding' }) });
    toast('Saved');
  } catch (e) { toast('Error: ' + e.message, 'error'); loadPeers(); }
}
async function peerApprove(id) {
  try { await api(`/api/peers/${id}/approve`, { method: 'POST' }); toast('Peer approved ✓'); loadPeers(); }
  catch (e) { toast('Error: ' + e.message, 'error'); }
}
async function peerCfg(id, field, val) {
  try { await api(`/api/peers/${id}/config`, { method: 'POST', body: JSON.stringify({ [field]: val }) }); toast('Saved'); }
  catch (e) { toast('Error: ' + e.message, 'error'); loadPeers(); }
}
async function peerRevoke(id) {
  if (!confirm('Revoke this peer? Their key stops working immediately.')) return;
  try { await api(`/api/peers/${id}`, { method: 'DELETE' }); toast('Peer revoked'); loadPeers(); }
  catch (e) { toast('Error: ' + e.message, 'error'); }
}
async function peerPing(id) {
  const out = document.getElementById('peer-out');
  out.textContent = 'Pinging…';
  try {
    const r = await api(`/api/peers/${id}/status`);
    const rem = r.remote || {};
    out.textContent = `${rem.name || r.peer}: ${rem.branch || '?'} @ ${rem.commit || '?'}\n`
      + ((rem.recently_promoted || []).map(p => `  • promoted: ${p.title} (${p.when})`).join('\n') || '  (no promoted work yet)');
  } catch (e) { out.textContent = ''; toast('Ping failed: ' + e.message, 'error'); }
}
async function peerTestJob(id) {
  const out = document.getElementById('peer-out');
  out.textContent = 'Sending test job to their queue… (can take a minute)';
  try {
    const r = await api(`/api/peers/${id}/test-job`, { method: 'POST', body: JSON.stringify({}) });
    out.textContent = 'Their node replied: ' + (r.output || '(empty)');
  } catch (e) { out.textContent = ''; toast('Test job failed: ' + e.message, 'error'); }
}
async function peerReviewVote(rid, vote) {
  const comments = prompt('Optional comment to send back with your ' + vote + ' vote:', '') ?? '';
  try {
    await api(`/api/peers/reviews/${rid}/vote`, { method: 'POST', body: JSON.stringify({ vote, comments }) });
    toast('Vote recorded — your friend sees it next time they refresh the review.');
    loadPeers();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.loadPeers = loadPeers; window.peerInvite = peerInvite; window.peerConnectForm = peerConnectForm;
window.peerConnect = peerConnect; window.peerApprove = peerApprove; window.peerCfg = peerCfg;
window.peerRevoke = peerRevoke; window.peerPing = peerPing; window.peerTestJob = peerTestJob;
window.peerReviewVote = peerReviewVote; window.peerConnInfo = peerConnInfo;
window.peerCfgWork = peerCfgWork;

/* ── Compute pricing: what a buddy pays to borrow this node's LLM ──────────────
   Per the owner's rule they pay for the ANSWERS — completion tokens — and the
   prompt they send is free (rate 0, but a real field so it can change later).
   Everything here moves REAL JellyCoin on the chain, so billing ships with a
   toggle and hard caps, and the per-token meter is OFF until switched on.      */
let _peerBilling = null, _peerMarket = null;

function _pbNum(id, v) { const e = document.getElementById(id); return e ? parseFloat(e.value) : v; }
function _jly(v) { return (Math.round((v || 0) * 1e6) / 1e6).toLocaleString(undefined, { maximumFractionDigits: 6 }); }

async function loadPeerBilling() {
  const el = document.getElementById('peer-billing-slot');
  if (!el) return;
  let c, led, mk, mon, cb;
  try {
    [c, led, mk, mon, cb] = await Promise.all([
      api('/api/peers/billing/config'), api('/api/peers/billing/ledger?limit=40'),
      api('/api/peers/billing/market'), api('/api/peers/billing/monetary'),
      api('/api/peers/billing/cost-basis')]);
  } catch (e) {
    el.innerHTML = `<div style="font-size:.76rem;color:var(--warn);">Compute pricing unavailable: ${esc(e.message)}</div>`;
    return;
  }
  _peerBilling = c; _peerMarket = mk;
  const tok = c.mode === 'token';
  el.innerHTML = `
    <div class="settings-group-title">&#128176; Compute pricing ${hlp('What a paired buddy pays to run LLM work on YOUR node, and what you pay to run work on theirs. These are REAL JellyCoin transfers on your chain — not play-money previews. Billing is toggleable and capped; nothing auto-spends with the toggle off.')}</div>
    <div style="border:1px solid var(--border);border-radius:8px;padding:10px;font-size:.78rem;">
      <label style="display:flex;gap:6px;align-items:center;cursor:pointer;margin-bottom:6px;">
        <input type="checkbox" id="pb-billing" ${c.billing_toggle ? 'checked' : ''}>
        <b>Bill peers for compute</b> ${hlp('Master switch. Off = every peer job is free in both directions and no JLY moves at all.')}</label>
      <label style="display:flex;gap:6px;align-items:center;cursor:pointer;margin-bottom:8px;">
        <input type="checkbox" id="pb-token" ${c.token_billing ? 'checked' : ''}>
        <b>Meter per token</b> (otherwise flat per job) ${hlp('ON = charge by the ANSWER: price × completion tokens. OFF = the older flat fee per job. Default OFF so an upgrade never starts metering anyone by surprise. The mode you advertise is the mode you bill on, and a peer on an older build automatically stays on the flat fee.')}</label>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:8px;">
        <div class="field"><label>JLY per 1,000 <b>completion</b> tokens ${hlp('The answer price — this is the number the owner asked for. 1.0 means a 500-token answer costs 0.5 JLY. Quoted per 1k so the number stays human.')}</label>
          <input type="number" step="0.000001" min="0" id="pb-comp" value="${c.price_per_1k_completion_jly}"></div>
        <div class="field"><label>JLY per 1,000 <b>prompt</b> tokens ${hlp('Input tokens. 0 by default — you only pay for the answers. Kept as a real, settable field so the price can change later without a schema change.')}</label>
          <input type="number" step="0.000001" min="0" id="pb-prompt" value="${c.price_per_1k_prompt_jly}"></div>
        <div class="field"><label>Flat JLY per job (fallback mode) ${hlp('Used when the meter is off, and by peers on older builds that have no per-token support.')}</label>
          <input type="number" step="0.01" min="0" id="pb-job" value="${c.price_per_llm_job_jly}"></div>
        <div class="field"><label>Over-report tolerance ${hlp('When THEIR node bills you, you count the answer you actually received yourself (about 4 characters per token) and pay min(their number, your count × this). 1.25 = allow 25% over your own count; 1.0 = pay only what you counted. Over-reports are billed down AND logged.')}</label>
          <input type="number" step="0.01" min="1" id="pb-tol" value="${c.tolerance}"></div>
        <div class="field"><label>Cap: max JLY per job ${hlp('A single job may never cost more than this. Breaching it fails the job cleanly — no partial charge.')}</label>
          <input type="number" step="0.01" min="0" id="pb-cap-job" value="${c.cap_job_jly}"></div>
        <div class="field"><label>Cap: max JLY per peer per day ${hlp('Daily ceiling per buddy. Once hit, further jobs with that peer are refused rather than charged.')}</label>
          <input type="number" step="0.01" min="0" id="pb-cap-peer" value="${c.cap_peer_day_jly}"></div>
        <div class="field"><label>Cap: global JLY per day ${hlp('Whole-wallet daily ceiling across ALL peers — the backstop against a drain.')}</label>
          <input type="number" step="0.01" min="0" id="pb-cap-day" value="${c.cap_day_jly}"></div>
      </div>
      <div style="margin-top:6px;">
        <button class="btn-sm primary" onclick="peerBillingSave()">&#128190; Save pricing</button>
        <span style="font-size:.72rem;color:var(--muted);margin-left:8px;">
          In effect now: <b>${tok ? `${_jly(c.price_per_1k_completion_jly)} JLY / 1k answer tokens` : `${_jly(c.price_per_llm_job_jly)} JLY per job (flat)`}</b>
          ${c.billing ? '' : ' — billing is OFF, nothing is charged'}
        </span>
      </div>
      <div style="font-size:.7rem;color:var(--muted);margin-top:6px;">
        Token counts come from the model's own usage report when it gives one; otherwise
        they are <b>estimated</b> at ~${esc(String(c.chars_per_token))} characters per token and the row says so.
        A peer that over-reports ${esc(String(c.flag_threshold))}+ times is flagged below — you decide what to do about it, nothing auto-bans.
      </div>
    </div>
    ${_pbBasisHtml(cb)}
    ${_pbMarketHtml(mk, mon)}
    ${_pbLedgerHtml(led)}`;
  _pbDrawChart();
}

/* WHY the price is the price. Every input is labelled measured / placeholder /
   owner, because a cost model whose inputs you can't audit is just a nicer-looking
   guess. The electricity rate in particular is NOT researched — it is his to set. */
function _pbBasisHtml(cb) {
  const i = cb.inputs || {}, m = cb.mining || {};
  const prov = p => `<span style="font-size:.6rem;border:1px solid var(--border);border-radius:99px;padding:0 5px;color:${p === 'measured' ? 'var(--green)' : p === 'placeholder' ? 'var(--warn)' : 'var(--muted)'};">${esc(p)}</span>`;
  const derived = cb.derived_default_jly_per_1k;
  const cur = cb.current_price_jly_per_1k;
  const marginPct = (derived && cur) ? Math.round(((cur - derived) / derived) * 100) : null;
  return `
    <div class="settings-group-title" style="margin-top:12px;">&#129518; Where this price comes from ${hlp('The default is not a made-up number: it is derived from throughput and power measured on your own RTX 3060, plus the JLY your miner would have earned in the time the answer took. Every input is editable — correct anything you know better than the measurement.')}</div>
    <div style="border:1px solid var(--border);border-radius:8px;padding:10px;font-size:.78rem;">
      <div style="font-size:.72rem;color:var(--muted);margin-bottom:8px;">
        Measured ${esc(cb.measured_on || '')} on ${esc(cb.gpu || '')} running ${esc(cb.model || '')}.
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px;">
        <div class="field"><label>Throughput (completion tok/s) ${prov((i.tok_per_s || {}).provenance)} ${hlp((i.tok_per_s || {}).note || '')}</label>
          <input type="number" step="0.1" min="0.1" id="cb-toks" value="${esc(String((i.tok_per_s || {}).value))}"></div>
        <div class="field"><label>GPU draw under load (W) ${prov((i.gpu_watts || {}).provenance)} ${hlp((i.gpu_watts || {}).note || '')}</label>
          <input type="number" step="1" min="0" id="cb-watts" value="${esc(String((i.gpu_watts || {}).value))}"></div>
        <div class="field"><label>Electricity ($/kWh) ${prov((i.kwh_cost_usd || {}).provenance)} ${hlp((i.kwh_cost_usd || {}).note || '')}</label>
          <input type="number" step="0.001" min="0" id="cb-kwh" value="${esc(String((i.kwh_cost_usd || {}).value))}"></div>
        <div class="field"><label>Margin over break-even ${prov((i.margin || {}).provenance)} ${hlp((i.margin || {}).note || '')}</label>
          <input type="number" step="0.05" min="0" id="cb-margin" value="${esc(String((i.margin || {}).value))}"></div>
      </div>
      <button class="btn-sm" onclick="peerCostBasisSave()">&#128190; Save inputs &amp; recompute</button>
      <div style="margin-top:8px;display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;">
        <div><span style="color:var(--muted);font-size:.7rem;">ENERGY FLOOR</span><br>
          <b>$${esc((cb.energy_floor_usd_per_1k || 0).toFixed(6))}</b> per 1k answer tokens<br>
          <span style="font-size:.7rem;color:var(--muted);">${esc(String(cb.seconds_per_1k_tokens))}s &times; ${esc(String((i.gpu_watts || {}).value))}W = ${esc((cb.kwh_per_1k_tokens || 0).toFixed(7))} kWh, at your $/kWh</span></div>
        <div><span style="color:var(--muted);font-size:.7rem;">MINING OPPORTUNITY COST</span><br>
          ${m.enough_data
            ? `<b>${_jly(cb.opportunity_cost_jly_per_1k)} JLY</b> per 1k answer tokens<br>
               <span style="font-size:.7rem;color:var(--muted);">the same card would have mined this much in ${esc(String(cb.seconds_per_1k_tokens))}s (${esc(String(m.block_reward_jly))} JLY/block, ${esc(String(m.mean_block_seconds))}s mean spacing over ${esc(String(m.blocks))} blocks)</span>`
            : `<b>&mdash;</b><br><span style="font-size:.7rem;color:var(--muted);">${esc(m.why || 'no chain data')} &mdash; falling back to ${_jly(cb.fallback_jly_per_1k)} JLY</span>`}</div>
        <div><span style="color:var(--muted);font-size:.7rem;">PRICE IN EFFECT</span><br>
          <b>${_jly(cur)} JLY</b> per 1k answer tokens
          ${marginPct !== null ? `<br><span style="font-size:.7rem;color:${marginPct < 0 ? 'var(--warn)' : 'var(--muted)'};">${marginPct >= 0 ? '+' : ''}${marginPct}% vs break-even${marginPct < 0 ? ' &mdash; you would earn more mining' : ''}</span>` : ''}
          ${derived ? `<br><button class="btn-sm" style="margin-top:4px;" onclick="peerUseDerived()">Use derived ${_jly(derived)}</button>` : ''}</div>
      </div>
      <div style="font-size:.7rem;color:var(--muted);margin-top:8px;">
        <b>Excluded:</b> ${(cb.excluded || []).map(esc).join('; ')}.
        The energy floor is therefore a genuine floor &mdash; real cost is higher, never lower.<br>
        ${esc(cb.note || '')}
      </div>
    </div>`;
}

async function peerCostBasisSave() {
  try {
    await api('/api/peers/billing/cost-basis', { method: 'POST', body: JSON.stringify({
      tok_per_s: _pbNum('cb-toks', 31.2), gpu_watts: _pbNum('cb-watts', 158),
      kwh_cost_usd: _pbNum('cb-kwh', 0.15), margin: _pbNum('cb-margin', 1) }) });
    toast('Recomputed — this changes the DERIVED default, not what you currently charge');
    loadPeerBilling();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function peerUseDerived() {
  try {
    const r = await api('/api/peers/billing/use-derived-price', { method: 'POST' });
    toast(`Price set to ${r.price_per_1k_completion_jly} JLY per 1k answer tokens`);
    loadPeerBilling();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

function _pbMarketHtml(mk, mon) {
  const t = mk.last_trade, tot = mk.totals || {};
  const fiat = (mk.monetary_mode && mk.fiat_rate) ? mk.fiat_rate : null;
  const chip = fiat ? `<span style="font-size:.62rem;border:1px solid var(--border);border-radius:99px;padding:1px 6px;color:${fiat.chip === 'evidenced' ? 'var(--green)' : 'var(--warn)'};">${esc(fiat.chip)}</span>` : '';
  const banner = (mk.monetary_mode && !mk.warning_hidden)
    ? `<div style="font-size:.7rem;color:var(--muted);border-left:2px solid var(--warn);padding-left:8px;margin:6px 0;">${esc(mk.note)}${fiat && fiat.chip === 'assumed' ? ' The dollar figures below use a rate YOU typed in — it is your own assumption, not a quote.' : ''}</div>`
    : '';
  const rows = (mk.trades || []).slice(0, 12).map(x => `
    <tr>
      <td style="padding:3px 8px;">${esc(x.when || '')}</td>
      <td style="padding:3px 8px;">${esc(x.peer)}</td>
      <td style="padding:3px 8px;color:${x.direction === 'earned' ? 'var(--green)' : 'var(--warn)'};">${x.direction === 'earned' ? '&#8599; earned' : '&#8600; spent'}</td>
      <td style="padding:3px 8px;text-align:right;">${x.completion_tokens.toLocaleString()}${x.reported ? '' : ' <span style="color:var(--muted);font-size:.62rem;">est</span>'}</td>
      <td style="padding:3px 8px;text-align:right;">${_jly(x.rate_jly_per_1k)}</td>
      <td style="padding:3px 8px;text-align:right;"><b>${_jly(x.amount_jly)}</b></td>
    </tr>`).join('');
  return `
    <div class="settings-group-title" style="margin-top:12px;">&#128200; What JLY has traded for ${hlp('JellyCoin is not listed on any exchange, so it has no market price. What it DOES have is a record of what peers actually paid for compute — this panel is that record, in JLY per 1,000 answer tokens. Nothing here is a quote.')}</div>
    <div style="border:1px solid var(--border);border-radius:8px;padding:10px;font-size:.78rem;">
      ${banner}
      ${!(mk.trades || []).length ? `
        <div style="color:var(--muted);font-size:.76rem;padding:6px 0;">
          <b>No trades yet.</b> This fills in once you and a peer actually settle work —
          nothing has been bought or sold, so there is no observed rate to show (and a
          row of zeros here would read like a real market sitting at zero, which it isn't).
        </div>` : `
        <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px;">
          <div><span style="color:var(--muted);font-size:.7rem;">LAST TRADE</span><br>
            <b>${_jly(t.rate_jly_per_1k)} JLY</b> / 1k tokens<br>
            <span style="font-size:.7rem;color:var(--muted);">${esc(t.peer)} · ${esc(t.direction)} · ${t.completion_tokens.toLocaleString()} tokens · ${_jly(t.amount_jly)} JLY · ${esc(t.when || '')}${t.reported ? '' : ' · estimated count'}</span></div>
          <div><span style="color:var(--muted);font-size:.7rem;">OBSERVED RATE</span><br>
            <b>${_jly((mk.observed || {}).avg)}</b> avg ${chip}<br>
            <span style="font-size:.7rem;color:var(--muted);">${_jly((mk.observed || {}).min)}–${_jly((mk.observed || {}).max)} over ${(mk.observed || {}).n} settlement(s)</span></div>
          <div><span style="color:var(--muted);font-size:.7rem;">RUNNING TOTALS</span><br>
            <b style="color:var(--green);">+${_jly(tot.earned_jly)}</b> / <b style="color:var(--warn);">-${_jly(tot.spent_jly)}</b> = ${_jly(tot.net_jly)} JLY<br>
            <span style="font-size:.7rem;color:var(--muted);">${(tot.tokens_served || 0).toLocaleString()} tokens served · ${(tot.tokens_consumed || 0).toLocaleString()} consumed</span></div>
          ${fiat ? `<div><span style="color:var(--muted);font-size:.7rem;">FIAT REFERENCE ${chip}</span><br>
            <b>$${esc(String(fiat.usd_per_jly))}</b> per JLY<br>
            <span style="font-size:.7rem;color:var(--muted);">basis: ${esc(fiat.basis)}${fiat.ref ? ' · ref ' + esc(fiat.ref) : ''}</span></div>` : ''}
        </div>
        <canvas id="pb-chart" style="width:100%;height:150px;"></canvas>
        <div id="pb-chart-note" style="font-size:.7rem;color:var(--muted);margin-top:2px;"></div>
        <table style="width:100%;border-collapse:collapse;font-size:.74rem;margin-top:8px;">
          <tr style="color:var(--muted);text-align:left;"><th style="padding:3px 8px;">When</th><th style="padding:3px 8px;">Peer</th><th style="padding:3px 8px;">Direction</th><th style="padding:3px 8px;text-align:right;">Answer tokens</th><th style="padding:3px 8px;text-align:right;">JLY/1k</th><th style="padding:3px 8px;text-align:right;">JLY</th></tr>
          ${rows}
        </table>`}
      ${_pbMonetaryHtml(mon)}
    </div>`;
}

function _pbMonetaryHtml(mon) {
  const v = mon.valuation || {}, r = mon.rate;
  const chip = r ? `<span style="font-size:.62rem;border:1px solid var(--border);border-radius:99px;padding:1px 6px;color:${r.chip === 'evidenced' ? 'var(--green)' : 'var(--warn)'};">${esc(r.chip)}</span>` : '';
  return `
    <details style="margin-top:10px;">
      <summary style="cursor:pointer;font-size:.76rem;">&#128181; Real-money mode ${hlp('If JellyCoin ever becomes genuinely tradeable, flip this on to allow dollar figures. It is OFF by default and, even on, an ASSUMED rate can never move your treasury, safe-to-spend or budget income — only a settlement where real currency actually changed hands posts to the money ledger.')}</summary>
      <div style="padding:8px 0;font-size:.76rem;">
        <label style="display:flex;gap:6px;align-items:center;cursor:pointer;"><input type="checkbox" id="pb-mon" ${mon.monetary_mode ? 'checked' : ''}> Monetary mode ${hlp('OFF = JLY is shown in JLY only, nowhere in dollars.')}</label>
        <label style="display:flex;gap:6px;align-items:center;cursor:pointer;"><input type="checkbox" id="pb-warn" ${mon.warning_hidden ? 'checked' : ''}> Hide the "no market price" banner ${hlp('Hides the long warning. The small provenance chip ("assumed" / "evidenced") stays next to every dollar figure — that one is not hideable, so a number can never lose track of where it came from.')}</label>
        <label style="display:flex;gap:6px;align-items:center;cursor:pointer;"><input type="checkbox" id="pb-nw" ${mon.count_in_net_worth ? 'checked' : ''}> Count JLY in net worth ${hlp('Your call. OFF by default: an assumed valuation stays its own asset line and is never folded into a real-dollar total.')}</label>
        <div style="margin:6px 0;">
          <b>Holdings:</b> ${_jly(v.holdings_jly)} JLY
          ${v.usd_value !== undefined ? ` &middot; <b>$${esc(String(v.usd_value))}</b> ${chip}` : ' <span style="color:var(--muted);">(no dollar value — monetary mode is off)</span>'}
          <div style="font-size:.7rem;color:var(--muted);">${esc(v.fiat_note || '')}</div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:flex-end;">
          <div class="field" style="margin:0;"><label>USD per JLY (your assumption)</label><input type="number" step="0.0001" min="0" id="pb-fiat" value="${r ? esc(String(r.usd_per_jly)) : ''}" style="width:130px;"></div>
          <button class="btn-sm" onclick="peerFiatRate()">Record assumed rate</button>
        </div>
        <div style="font-size:.7rem;color:var(--muted);margin-top:6px;">${esc(mon.boundary || '')}</div>
      </div>
    </details>`;
}

function _pbLedgerHtml(led) {
  const b = (led.balances || []).map(x => `
    <tr>
      <td style="padding:3px 8px;">${esc((x.peer || '').replace('peer:', ''))}
        ${x.flagged ? `<span style="color:var(--warn);font-size:.66rem;" title="Reported more answer tokens than we counted, ${x.discrepancies} time(s)">&#9873; over-reporting</span>` : ''}</td>
      <td style="padding:3px 8px;text-align:right;color:var(--green);">+${_jly(x.earned_jly)}</td>
      <td style="padding:3px 8px;text-align:right;color:var(--warn);">-${_jly(x.spent_jly)}</td>
      <td style="padding:3px 8px;text-align:right;"><b>${_jly(x.net_jly)}</b></td>
      <td style="padding:3px 8px;text-align:right;font-size:.7rem;color:var(--muted);">${(x.tokens_served || 0).toLocaleString()} / ${(x.tokens_consumed || 0).toLocaleString()}</td>
      <td style="padding:3px 8px;text-align:right;">${x.discrepancies ? `<span style="color:var(--warn);">${x.discrepancies}${x.worst_ratio ? ' (worst ×' + esc(String(Math.round(x.worst_ratio * 100) / 100)) + ')' : ''}</span>` : '&mdash;'}</td>
    </tr>`).join('');
  const rows = (led.rows || []).slice(0, 20).map(r => `
    <tr style="${r.status === 'blocked' ? 'opacity:.7;' : ''}">
      <td style="padding:3px 8px;font-size:.7rem;">${esc(r.created_at || '')}</td>
      <td style="padding:3px 8px;">${esc((r.peer || '').replace('peer:', ''))}</td>
      <td style="padding:3px 8px;">${r.direction === 'earned' ? '&#8599;' : '&#8600;'} ${esc(r.kind || '')}</td>
      <td style="padding:3px 8px;text-align:right;">${(r.prompt_tokens || 0).toLocaleString()} / ${(r.completion_tokens || 0).toLocaleString()}${r.reported ? '' : ' <span style="color:var(--muted);font-size:.62rem;">est</span>'}</td>
      <td style="padding:3px 8px;text-align:right;">${_jly(r.amount_jly)}</td>
      <td style="padding:3px 8px;font-size:.7rem;color:${r.status === 'settled' ? 'var(--green)' : 'var(--warn)'};">${esc(r.status)}${r.reason ? ' — ' + esc(String(r.reason).slice(0, 60)) : ''}
        ${r.discrepancy_ratio ? `<br><span style="color:var(--warn);">claimed ×${esc(String(r.discrepancy_ratio))} our count — billed down</span>` : ''}</td>
    </tr>`).join('');
  return `
    <div class="settings-group-title" style="margin-top:12px;">&#129534; Compute ledger ${hlp('Every metered job: who, which way the coin went, how many tokens, what it cost, and whether the count was reported by the model or estimated locally. Both sides keep their own copy — a divergence shows up here instead of staying silent.')}</div>
    <div style="border:1px solid var(--border);border-radius:8px;padding:10px;font-size:.78rem;">
      ${led.pending ? `<div style="color:var(--warn);font-size:.72rem;margin-bottom:6px;">&#9888; ${led.pending} unconfirmed row(s) — recorded before the transfer and never confirmed (a crash mid-settlement). They are NOT counted as paid.</div>` : ''}
      ${(led.balances || []).length ? `<table style="width:100%;border-collapse:collapse;font-size:.74rem;">
        <tr style="color:var(--muted);text-align:left;"><th style="padding:3px 8px;">Peer</th><th style="padding:3px 8px;text-align:right;">Earned</th><th style="padding:3px 8px;text-align:right;">Spent</th><th style="padding:3px 8px;text-align:right;">Net</th><th style="padding:3px 8px;text-align:right;">Tokens served/used</th><th style="padding:3px 8px;text-align:right;">Discrepancies</th></tr>
        ${b}</table>` : '<div style="color:var(--muted);font-size:.76rem;">No metered jobs yet — this fills in the first time a peer borrows your LLM (or you borrow theirs).</div>'}
      ${rows ? `<table style="width:100%;border-collapse:collapse;font-size:.74rem;margin-top:8px;">
        <tr style="color:var(--muted);text-align:left;"><th style="padding:3px 8px;">When</th><th style="padding:3px 8px;">Peer</th><th style="padding:3px 8px;">Job</th><th style="padding:3px 8px;text-align:right;">Prompt / answer</th><th style="padding:3px 8px;text-align:right;">JLY</th><th style="padding:3px 8px;">Status</th></tr>
        ${rows}</table>` : ''}
    </div>`;
}

/* Observed JLY-per-1k across settlements. Same hand-drawn canvas style as the
   bills charts — no chart library anywhere in this app. */
function _pbDrawChart() {
  const cv = document.getElementById('pb-chart');
  if (!cv || !_peerMarket) return;
  const note = document.getElementById('pb-chart-note');
  const trades = (_peerMarket.trades || []).slice().reverse();   // oldest → newest
  if (note) {
    note.textContent = _peerMarket.enough_data
      ? `${trades.length} settlements · observed ${_jly(_peerMarket.observed.min)}–${_jly(_peerMarket.observed.max)} JLY per 1k answer tokens.`
      : `Only ${trades.length} settlement(s) — too few to read a trend from. The points are shown, `
        + `but no line is drawn through them until there are at least ${_peerMarket.min_trades}.`;
  }
  const vals = trades.map(t => t.rate_jly_per_1k);
  const maxV = Math.max(...vals, 0), minV = Math.min(...vals, 0);
  const css = getComputedStyle(document.documentElement);
  const cvar = (n, fb) => (css.getPropertyValue(n) || '').trim() || fb;
  const COL = cvar('--accent', '#6c63ff'), MUTED = cvar('--muted', '#64748b'),
    BORDER = cvar('--border', '#2a2f3d'), GREEN = cvar('--green', '#3fb950'), WARN = cvar('--warn', '#e3b341');
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(240, cv.clientWidth || 600), h = 150;
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  const x = cv.getContext('2d');
  if (!x) return;
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  x.clearRect(0, 0, w, h);
  const padL = 56, padR = 8, padT = 10, padB = 20;
  const plotW = w - padL - padR, plotH = h - padT - padB;
  const span = (maxV - minV) || maxV || 1;
  const yOf = v => padT + plotH - ((v - minV) / span) * plotH;

  x.strokeStyle = BORDER; x.lineWidth = 1;
  x.fillStyle = MUTED; x.font = '10px system-ui, sans-serif'; x.textBaseline = 'middle'; x.textAlign = 'right';
  for (let i = 0; i <= 3; i++) {
    const v = minV + (span * i / 3), y = Math.round(yOf(v)) + 0.5;
    x.beginPath(); x.moveTo(padL, y); x.lineTo(w - padR, y); x.stroke();
    x.fillText(_jly(v), padL - 6, y);
  }
  const n = trades.length, step = n > 1 ? plotW / (n - 1) : 0;
  const px = i => (n > 1 ? padL + step * i : padL + plotW / 2);
  if (_peerMarket.enough_data && n > 1) {
    x.strokeStyle = COL; x.lineWidth = 2; x.beginPath();
    trades.forEach((t, i) => { const X = px(i), Y = yOf(vals[i]); i ? x.lineTo(X, Y) : x.moveTo(X, Y); });
    x.stroke();
  }
  trades.forEach((t, i) => {
    x.fillStyle = t.direction === 'earned' ? GREEN : WARN;
    x.beginPath(); x.arc(px(i), yOf(vals[i]), 3, 0, Math.PI * 2); x.fill();
  });
  x.fillStyle = MUTED; x.textAlign = 'left'; x.textBaseline = 'top';
  x.fillText('JLY per 1k answer tokens · green = earned, amber = spent', padL, padT + plotH + 5);
}

async function peerBillingSave() {
  const body = {
    billing: document.getElementById('pb-billing').checked,
    token_billing: document.getElementById('pb-token').checked,
    mode: document.getElementById('pb-token').checked ? 'token' : 'job',
    price_per_1k_completion_jly: _pbNum('pb-comp', 1),
    price_per_1k_prompt_jly: _pbNum('pb-prompt', 0),
    price_per_llm_job_jly: _pbNum('pb-job', 1),
    tolerance: _pbNum('pb-tol', 1.25),
    cap_job_jly: _pbNum('pb-cap-job', 25),
    cap_peer_day_jly: _pbNum('pb-cap-peer', 250),
    cap_day_jly: _pbNum('pb-cap-day', 1000),
  };
  try {
    await api('/api/peers/billing/config', { method: 'POST', body: JSON.stringify(body) });
    toast(body.billing ? 'Pricing saved — these are real JLY transfers' : 'Pricing saved (billing off — nothing is charged)');
    loadPeerBilling();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function peerMonetarySave() {
  const body = {
    monetary_mode: document.getElementById('pb-mon').checked,
    warning_hidden: document.getElementById('pb-warn').checked,
    count_in_net_worth: document.getElementById('pb-nw').checked,
  };
  try {
    await api('/api/peers/billing/monetary', { method: 'POST', body: JSON.stringify(body) });
    toast('Saved'); loadPeerBilling();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function peerFiatRate() {
  const v = _pbNum('pb-fiat', 0);
  if (!(v >= 0)) { toast('Enter a USD-per-JLY number', 'error'); return; }
  try {
    await api('/api/peers/billing/fiat-rate', { method: 'POST',
      body: JSON.stringify({ usd_per_jly: v, basis: 'owner_assumed', note: 'set in Crypto → JellyCoin → Peers' }) });
    toast('Recorded as YOUR assumption — it cannot post to the money ledger');
    loadPeerBilling();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

// the three monetary checkboxes save on change (no separate button to forget)
document.addEventListener('change', e => {
  if (e.target && ['pb-mon', 'pb-warn', 'pb-nw'].includes(e.target.id)) peerMonetarySave();
});
window.addEventListener('resize', () => { if (document.getElementById('pb-chart')) _pbDrawChart(); });

window.loadPeerBilling = loadPeerBilling; window.peerBillingSave = peerBillingSave;
window.peerMonetarySave = peerMonetarySave; window.peerFiatRate = peerFiatRate;
window.peerCostBasisSave = peerCostBasisSave; window.peerUseDerived = peerUseDerived;

/* ══ MAIL & QUOTES TAB ══
   Config-driven mail desk: multiple accounts (IMAP/SMTP or Gmail OAuth), business
   profiles that drive the AI reply/quote drafter, FAQ auto-answers, store-order
   context on buyer mail, and the auto-reply gate (manual | auto_draft | full_auto).
   Layout follows the Etsy/Printify pattern: a useful landing (inbox + small
   dashboard) with a collapsible ⚙️ Configuration section underneath. */

let _mailOv = null;          // /api/mail/overview payload
let _mailAcct = null;        // selected account id (null = default)
let _mailMsgs = [];
let _mailCur = null;         // open message
let _mailQuoteRows = [];     // quote-builder line items

async function renderMail() {
  document.getElementById('main-content').innerHTML = `
    <div class="view-header">
      <div class="view-title">&#9993;&#65039; Mail &amp; Quotes</div>
      <div class="view-sub">Customer email, AI-drafted replies &amp; quotes driven by your business profiles,
        FAQ auto-answers, and the guardrailed auto-reply gate.</div>
    </div>
    <div id="mail-stats" class="stats-row" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:12px;"></div>
    <div style="display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap;">
      <select id="mail-acct-sel" style="max-width:260px;" title="Which mail account to read. Accounts are managed in Configuration below."></select>
      <button class="btn-sm primary" onclick="loadMailInbox()" title="Re-read this account's inbox over IMAP. Read-only: nothing is sent. Opening a message marks it seen on the server.">&#128260; Refresh inbox</button>
      <button class="btn-sm" id="mail-gate-run" style="display:none;" onclick="mailGateRun()" title="Run one gate batch now: classify new unseen mail, draft replies, and (full_auto only) send the routine ones. Bounded by the batch size.">&#128678; Run gate now</button>
      <span id="mail-status" style="font-size:.8rem;color:var(--muted);"></span>
    </div>
    <div style="display:grid;grid-template-columns:360px 1fr;gap:16px;align-items:start;margin-bottom:16px;">
      <div id="mail-list"></div>
      <div id="mail-detail"><div class="empty"><div class="empty-icon">&#128231;</div>Select a message to read &amp; reply.</div></div>
    </div>
    <details class="settings-group" id="mail-review-details" style="margin-bottom:16px;">
      <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#128269; Review queue
        <span id="mail-review-badge" style="font-weight:400;font-size:.75rem;color:var(--muted);">&mdash; AI drafts &amp; held replies awaiting you</span>
      </summary>
      <div id="mail-review-body" style="margin-top:12px;"></div>
    </details>
    <details class="settings-group" id="mail-config-details" style="margin-bottom:16px;">
      <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#9881;&#65039; Configuration
        <span style="font-weight:400;font-size:.75rem;color:var(--muted);">&mdash; accounts (IMAP/SMTP &amp; Gmail), business profiles, auto-reply gate, FAQ</span>
      </summary>
      <div id="mail-config-body" style="margin-top:14px;"></div>
    </details>`;
  await loadMailOverview();
  await loadMailInbox();
  loadMailReview();
}
window.renderMail = renderMail;

/* ── overview: stats, account selector, config section ─────────────────────── */
async function loadMailOverview() {
  try { _mailOv = await api('/api/mail/overview'); } catch (e) { _mailOv = null; }
  const ov = _mailOv || { accounts: [], profiles: [], gate: {}, counts: {} };
  const sel = document.getElementById('mail-acct-sel');
  if (sel) {
    sel.innerHTML = ov.accounts.filter(a => a.enabled).map(a =>
      `<option value="${a.id}"${_mailAcct === a.id ? ' selected' : ''}>${esc(a.label)} &lt;${esc(a.email)}&gt;</option>`).join('')
      || (ov.legacy_configured ? '<option value="">Legacy mailbox (settings)</option>'
                               : '<option value="">No account configured</option>');
    if (!_mailAcct && ov.accounts.filter(a => a.enabled).length) _mailAcct = ov.accounts.filter(a => a.enabled)[0].id;
    sel.onchange = () => { _mailAcct = sel.value ? parseInt(sel.value, 10) : null; loadMailInbox(); };
  }
  const g = ov.gate || {};
  const st = document.getElementById('mail-stats');
  if (st) st.innerHTML = `
    <div class="stat-card"><div class="stat-label">&#128269; Awaiting review</div>
      <div class="stat-val ${ov.counts.awaiting ? 'c-warn' : ''}">${ov.counts.awaiting || 0}</div>
      <div style="font-size:.64rem;color:var(--muted);margin-top:5px;">AI drafts &amp; held replies</div></div>
    <div class="stat-card"><div class="stat-label">&#128233; Auto-sent today</div>
      <div class="stat-val">${ov.counts.sent_today || 0}</div>
      <div style="font-size:.64rem;color:var(--muted);margin-top:5px;">${ov.counts.sent || 0} total</div></div>
    <div class="stat-card"><div class="stat-label">&#128218; FAQ entries</div>
      <div class="stat-val">${ov.counts.faq || 0}</div>
      <div style="font-size:.64rem;color:var(--muted);margin-top:5px;">auto-answer knowledge base</div></div>
    <div class="stat-card"><div class="stat-label">&#128678; Gate</div>
      <div class="stat-val" style="font-size:1rem;padding-top:6px;">${g.enabled ? '<span class="c-green">on</span>' : '<span style="color:var(--muted)">off</span>'} &middot; ${esc(g.mode || 'manual')}</div>
      <div style="font-size:.64rem;color:var(--muted);margin-top:5px;">confidence &ge; ${g.confidence ?? 80} &middot; every ${g.interval_min || 15} min</div></div>`;
  const runBtn = document.getElementById('mail-gate-run');
  if (runBtn) runBtn.style.display = (g.mode && g.mode !== 'manual') ? '' : 'none';
  renderMailConfig();
}

/* ── inbox ──────────────────────────────────────────────────────────────────── */
const _MAIL_INTENT_CHIP = {
  quote_request: ['&#128176;', 'quote'], order_support: ['&#128230;', 'order'],
  faq: ['&#128218;', 'faq'], spam: ['&#128465;&#65039;', 'spam'], other: ['&#128172;', 'other'],
};

async function loadMailInbox() {
  const list = document.getElementById('mail-list');
  const st = document.getElementById('mail-status');
  if (!list) return;
  list.innerHTML = '<div class="loading-state">Loading inbox…</div>';
  try {
    const d = await api('/api/mail/inbox' + (_mailAcct ? `?account_id=${_mailAcct}` : ''));
    _mailMsgs = d.messages || [];
    if (st) st.textContent = `${d.count} message${d.count === 1 ? '' : 's'}`;
    if (!_mailMsgs.length) { list.innerHTML = '<div class="empty"><div class="empty-icon">&#128231;</div>Inbox empty.</div>'; return; }
    list.innerHTML = _mailMsgs.map(m => {
      const chip = m.intent && _MAIL_INTENT_CHIP[m.intent]
        ? `<span style="font-size:.62rem;background:var(--bg2);border-radius:8px;padding:1px 7px;" title="Gate classification: ${esc(m.intent)}${m.gate_status ? ' · ' + esc(m.gate_status) : ''}">${_MAIL_INTENT_CHIP[m.intent][0]} ${_MAIL_INTENT_CHIP[m.intent][1]}</span>` : '';
      const ctx = m.has_context ? '<span style="font-size:.62rem;" title="Linked to store data (order/listing match)">&#128717;</span>' : '';
      return `
      <div class="card" style="padding:11px 13px;cursor:pointer;margin-bottom:8px;${m.seen ? '' : 'border-left:3px solid var(--accent);'}"
        onclick="openMailMsg('${esc(m.uid)}')">
        <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;">
          <b style="font-size:.84rem;">${esc(m.from_name || m.from_email)}</b>
          <span style="display:flex;gap:8px;align-items:center;white-space:nowrap;">
            <span style="font-size:.66rem;color:var(--muted);">${esc((m.date || '').replace(/\s*\(.*\)/, '').slice(0, 22))}</span>
            <button title="Delete this email from the mailbox (no undo)" onclick="event.stopPropagation(); mailDeleteMsg('${esc(m.uid)}')"
              style="background:none;border:none;color:var(--muted);cursor:pointer;font-size:.9rem;padding:0 2px;line-height:1;">&#128465;&#65039;</button>
          </span>
        </div>
        <div style="font-size:.8rem;margin-top:3px;${m.seen ? 'color:var(--muted);' : 'font-weight:600;'}">${esc(m.subject)}</div>
        ${(chip || ctx) ? `<div style="display:flex;gap:6px;margin-top:4px;">${chip}${ctx}</div>` : ''}
      </div>`;
    }).join('');
  } catch (e) {
    list.innerHTML = `<div class="empty"><div class="empty-icon">&#9888;&#65039;</div>${esc(e.message)}</div>`;
  }
}

async function mailDeleteMsg(uid) {
  if (!confirm('Delete this email from the mailbox? This removes it on the server — there is no undo.')) return;
  try {
    await api(`/api/mail/message/${encodeURIComponent(uid)}` + (_mailAcct ? `?account_id=${_mailAcct}` : ''), { method: 'DELETE' });
    toast('Deleted');
    if (_mailCur && String(_mailCur.uid) === String(uid)) _mailCur = null;
    loadMailInbox();
  } catch (e) { toast('Delete failed: ' + e.message, 'error'); }
}
window.mailDeleteMsg = mailDeleteMsg;

/* ── message detail + reply ─────────────────────────────────────────────────── */
async function openMailMsg(uid) {
  const det = document.getElementById('mail-detail');
  det.innerHTML = '<div class="loading-state">Opening…</div>';
  try {
    const m = await api(`/api/mail/message/${encodeURIComponent(uid)}` + (_mailAcct ? `?account_id=${_mailAcct}` : ''));
    _mailCur = m;
    _mailQuoteRows = [];
    const ov = _mailOv || { profiles: [] };
    const imgs = (m.images || []).map(u => `<img src="${API + u}" loading="lazy" onclick="openLightbox('${API + u}','')"
        style="width:130px;height:130px;object-fit:cover;border-radius:8px;cursor:pointer;">`).join('');

    /* linked store context (orders / listings / proposals) */
    const ctx = m.context || {};
    let ctxHtml = '';
    const ctxBits = [];
    (ctx.orders || []).forEach(o => ctxBits.push(`&#128230; Order <b>${esc(o.id)}</b> &middot; ${esc(o.status)} &middot; ${esc((o.created || '').slice(0, 10))}${o.items && o.items.filter(Boolean).length ? ' &middot; ' + esc(o.items.filter(Boolean).join(', ')) : ''}`));
    (ctx.designs || []).forEach(d => ctxBits.push(`&#128717; Listing <b>${esc(String(d.etsy_listing_id || d.printify_id || d.id))}</b> &middot; &ldquo;${esc(d.title)}&rdquo; (${esc(d.product_type || '')}, ${esc(d.status || '')})`));
    (ctx.proposals || []).forEach(p => ctxBits.push(`&#128161; Proposal <b>#${p.id}</b> &middot; &ldquo;${esc(p.title)}&rdquo; (${esc(p.status)})`));
    if (ctxBits.length) ctxHtml = `
      <div class="card" style="padding:12px 16px;margin-top:12px;border-left:3px solid var(--accent2,#4cc9f0);">
        <b style="font-size:.8rem;">&#128279; Linked store data</b>
        <div style="font-size:.76rem;margin-top:6px;line-height:1.7;">${ctxBits.join('<br>')}</div>
        <div style="font-size:.66rem;color:var(--muted);margin-top:4px;">Matched by sender/ids in this email — fed into the AI draft so the reply is order-aware.</div>
      </div>`;

    /* prior gate verdict on this message */
    let logHtml = '';
    if (m.log && m.log.intent) {
      logHtml = `<div style="font-size:.72rem;color:var(--muted);margin-top:8px;">
        &#128678; Gate: <b>${esc(m.log.intent)}</b> (${m.log.confidence ?? '?'}% confident, ${m.log.routine ? 'routine' : 'non-routine'})
        &middot; status <b>${esc(m.log.status)}</b>${m.log.reason ? ' &middot; ' + esc(m.log.reason) : ''}</div>`;
    }

    const profOpts = (ov.profiles || []).map(p =>
      `<option value="${p.id}"${(m.profile_id || (ov.profiles.find(x => x.is_default) || {}).id) === p.id ? ' selected' : ''}>${esc(p.name)}</option>`).join('');

    det.innerHTML = `
      <div class="card" style="padding:16px 18px;">
        <div style="font-weight:700;font-size:1rem;">${esc(m.subject || '(no subject)')}</div>
        <div style="font-size:.78rem;color:var(--muted);margin:2px 0 12px;">
          From <b>${esc(m.from_name || '')}</b> &lt;${esc(m.from_email)}&gt; &middot; ${esc(m.date || '')}</div>
        <div style="font-size:.86rem;line-height:1.6;white-space:pre-wrap;max-height:300px;overflow:auto;
          background:var(--bg2);border-radius:8px;padding:12px;">${esc(m.body || '(no text)')}</div>
        ${imgs ? `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;">${imgs}</div>
          <div style="font-size:.7rem;color:var(--muted);margin-top:4px;">${(m.images || []).length} photo(s) attached</div>` : ''}
        ${logHtml}
      </div>
      ${ctxHtml}
      <div class="card" style="padding:16px 18px;margin-top:14px;">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px;">
          <b style="font-size:.92rem;">&#9997;&#65039; Reply</b>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            <select id="mail-profile-sel" title="Which business profile drives the AI draft (terms, pricing, tone, signature). Bind a default per account in Configuration.">${profOpts || '<option value="">(no profile)</option>'}</select>
            <button class="btn-sm primary" id="mail-draft-btn" onclick="mailDraftQuote()" title="Send this email (plus any photos) to the local LLM. It drafts a reply using the selected business profile's terms/pricing/tone, any matching FAQ answer, and the linked order context. Draft only — nothing sends until you press Send reply.">&#10024; Draft AI reply</button>
          </div>
        </div>
        <div class="field" style="margin-bottom:8px;"><label>To ${hlp('Where the reply goes — prefilled to the sender. Sent from the selected account over its SMTP (or Gmail).')}</label>
          <input type="text" id="mail-to" value="${esc(m.from_email)}"></div>
        <div class="field" style="margin-bottom:8px;"><label>Subject</label>
          <input type="text" id="mail-subject" value="Re: ${esc(m.subject || 'your message')}"></div>
        <div class="field" style="margin-bottom:10px;"><label>Message ${hlp('Your reply body. Draft AI reply writes a first draft here; edit freely. Nothing leaves your machine until you press Send reply.')}</label>
          <textarea id="mail-reply-body" rows="12" placeholder="Write a reply, or hit ✨ Draft AI reply…"></textarea></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button class="btn-sm primary" onclick="mailSend()" title="Send this reply now from the selected account. It threads onto the original email. This really emails the customer — there is no undo.">&#128233; Send reply</button>
          <button class="btn-sm" onclick="mailToggleQuoteBuilder()" title="Build an itemized estimate from the active profile's pricing model (rate, minimums) and insert it into the reply.">&#129518; Quote builder</button>
        </div>
        <div id="mail-quote-builder" style="display:none;margin-top:12px;"></div>
      </div>`;
    loadMailInbox();  // refresh seen state
  } catch (e) {
    det.innerHTML = `<div class="empty"><div class="empty-icon">&#9888;&#65039;</div>${esc(e.message)}</div>`;
  }
}

async function mailDraftQuote() {
  if (!_mailCur) return;
  const btn = document.getElementById('mail-draft-btn'); const orig = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '⏳ Drafting…';
  try {
    const profSel = document.getElementById('mail-profile-sel');
    const { task_id } = await api('/api/mail/draft-quote', { method: 'POST', body: JSON.stringify({
      uid: _mailCur.uid, account_id: _mailAcct || null,
      profile_id: profSel && profSel.value ? parseInt(profSel.value, 10) : null }) });
    const r = await pollTask(task_id, 90);
    if (r && r.quote) {
      document.getElementById('mail-reply-body').value = r.quote;
      const extras = [];
      if (r.faq_used) extras.push('FAQ answer used');
      if (r.context_used) extras.push('order context used');
      toast('✨ Draft ready' + (extras.length ? ` (${extras.join(', ')})` : '') + ' — review & tweak before sending');
    } else toast('No draft returned', 'error');
  } catch (e) { toast('Draft failed: ' + e.message, 'error'); }
  btn.disabled = false; btn.innerHTML = orig;
}

async function mailSend() {
  const to = document.getElementById('mail-to').value.trim();
  const subject = document.getElementById('mail-subject').value.trim();
  const body = document.getElementById('mail-reply-body').value.trim();
  if (!to || !body) { toast('Need a recipient and a message', 'error'); return; }
  try {
    await api('/api/mail/send', { method: 'POST', body: JSON.stringify({
      to, subject, body, in_reply_to: _mailCur ? _mailCur.message_id : '',
      account_id: _mailAcct || null, uid: _mailCur ? _mailCur.uid : null }) });
    toast('📨 Reply sent to ' + to);
    loadMailOverview();
  } catch (e) { toast('Send failed: ' + e.message, 'error'); }
}

/* ── quote builder (line items priced from the active profile) ─────────────── */
function _mailActiveProfile() {
  const ov = _mailOv || { profiles: [] };
  const sel = document.getElementById('mail-profile-sel');
  const pid = sel && sel.value ? parseInt(sel.value, 10) : null;
  return (ov.profiles || []).find(p => p.id === pid) || (ov.profiles || [])[0] || null;
}

function mailToggleQuoteBuilder() {
  const el = document.getElementById('mail-quote-builder');
  if (!el) return;
  if (el.style.display === 'none') {
    if (!_mailQuoteRows.length) {
      const p = _mailActiveProfile();
      let rate = 0, minh = 0;
      try { const pr = JSON.parse((p || {}).pricing || '{}'); rate = pr.hourly_rate || 0; minh = pr.minimum_hours || 0; } catch {}
      _mailQuoteRows = [{ label: 'Labor', qty: minh || 1, rate: rate }];
    }
    el.style.display = ''; renderMailQuoteBuilder();
  } else el.style.display = 'none';
}

function renderMailQuoteBuilder() {
  const el = document.getElementById('mail-quote-builder');
  if (!el) return;
  const p = _mailActiveProfile();
  let pr = {}; try { pr = JSON.parse((p || {}).pricing || '{}'); } catch {}
  const total = _mailQuoteRows.reduce((s, r) => s + (parseFloat(r.qty) || 0) * (parseFloat(r.rate) || 0), 0);
  el.innerHTML = `
    <div style="background:var(--bg2);border-radius:8px;padding:12px;">
      <div style="font-size:.78rem;color:var(--muted);margin-bottom:8px;">
        Line items priced from <b>${esc((p || {}).name || 'profile')}</b>${pr.hourly_rate ? ` &middot; $${pr.hourly_rate}/hr` : ''}${pr.minimum_hours ? ` &middot; ${pr.minimum_hours}-hr minimum` : ''}${pr.materials_policy ? ` &middot; materials: ${esc(pr.materials_policy)}` : ''}
      </div>
      <div style="overflow-x:auto;">
      <table style="width:100%;font-size:.8rem;border-collapse:collapse;">
        <tr style="color:var(--muted);text-align:left;"><th style="padding:3px;">Item</th><th style="width:90px;">Hours/Qty</th><th style="width:90px;">Rate $</th><th style="width:90px;">Line $</th><th style="width:30px;"></th></tr>
        ${_mailQuoteRows.map((r, i) => `
        <tr>
          <td style="padding:3px;"><input type="text" value="${esc(r.label)}" onchange="_mailQuoteRows[${i}].label=this.value"></td>
          <td><input type="number" step="0.5" value="${r.qty}" onchange="_mailQuoteRows[${i}].qty=this.value;renderMailQuoteBuilder()"></td>
          <td><input type="number" step="1" value="${r.rate}" onchange="_mailQuoteRows[${i}].rate=this.value;renderMailQuoteBuilder()"></td>
          <td style="padding:3px;">${(((parseFloat(r.qty) || 0) * (parseFloat(r.rate) || 0)) || 0).toFixed(2)}</td>
          <td><button class="btn-sm danger" onclick="_mailQuoteRows.splice(${i},1);renderMailQuoteBuilder()" title="Remove line">&times;</button></td>
        </tr>`).join('')}
      </table>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;flex-wrap:wrap;gap:8px;">
        <button class="btn-sm" onclick="_mailQuoteRows.push({label:'',qty:1,rate:${parseFloat(pr.hourly_rate) || 0}});renderMailQuoteBuilder()">+ Add line</button>
        <div style="font-size:.84rem;"><b>Estimated total: $${total.toFixed(2)}</b>${pr.minimum_hours ? ` <span style="color:var(--muted);font-size:.7rem;">(${pr.minimum_hours}-hr minimum applies)</span>` : ''}</div>
        <button class="btn-sm primary" onclick="mailInsertQuote()" title="Append this itemized estimate to the reply body — still just a draft until you press Send reply.">&#8595; Insert into reply</button>
      </div>
      <div style="font-size:.66rem;color:var(--muted);margin-top:6px;">Estimates only — your profile terms (hour ranges, no fixed bids, materials policy) still apply to the wording.</div>
    </div>`;
}

function mailInsertQuote() {
  const body = document.getElementById('mail-reply-body');
  if (!body) return;
  const p = _mailActiveProfile();
  let pr = {}; try { pr = JSON.parse((p || {}).pricing || '{}'); } catch {}
  const lines = _mailQuoteRows.filter(r => r.label || r.qty).map(r =>
    `  - ${r.label || 'Item'}: ${r.qty} x $${parseFloat(r.rate || 0).toFixed(2)} = $${((parseFloat(r.qty) || 0) * (parseFloat(r.rate) || 0)).toFixed(2)}`);
  const total = _mailQuoteRows.reduce((s, r) => s + (parseFloat(r.qty) || 0) * (parseFloat(r.rate) || 0), 0);
  const block = `\n\nEstimate:\n${lines.join('\n')}\n  Estimated total: $${total.toFixed(2)}`
    + (pr.minimum_hours ? `\n  (${pr.minimum_hours}-hour minimum applies)` : '')
    + (pr.materials_policy ? `\n  Materials: ${pr.materials_policy}` : '');
  body.value = (body.value || '') + block;
  toast('Estimate inserted into the reply');
}

/* ── review queue (gate drafts & held replies) ─────────────────────────────── */
async function loadMailReview() {
  const el = document.getElementById('mail-review-body');
  const badge = document.getElementById('mail-review-badge');
  if (!el) return;
  try {
    const d = await api('/api/mail/log?limit=100');
    const rows = (d.log || []).filter(r => ['drafted', 'held', 'sent', 'skipped'].includes(r.status));
    const awaiting = rows.filter(r => r.status === 'drafted' || r.status === 'held');
    if (badge) badge.innerHTML = awaiting.length
      ? `&mdash; <b class="c-warn">${awaiting.length}</b> awaiting your review`
      : '&mdash; AI drafts &amp; held replies awaiting you';
    if (!rows.length) { el.innerHTML = '<div style="font-size:.8rem;color:var(--muted);">Nothing processed by the gate yet.</div>'; return; }
    el.innerHTML = rows.slice(0, 30).map(r => {
      const chip = _MAIL_INTENT_CHIP[r.intent] ? `${_MAIL_INTENT_CHIP[r.intent][0]} ${_MAIL_INTENT_CHIP[r.intent][1]}` : esc(r.intent || '');
      const stColor = r.status === 'sent' ? 'var(--green)' : (r.status === 'held' || r.status === 'drafted') ? 'var(--warning,#e6a23c)' : 'var(--muted)';
      const actionable = r.status === 'drafted' || r.status === 'held';
      return `
      <div class="card" style="padding:12px 14px;margin-bottom:10px;">
        <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;">
          <div style="font-size:.82rem;"><b>${esc(r.from_email)}</b> &middot; ${esc(r.subject || '(no subject)')}</div>
          <div style="font-size:.7rem;color:${stColor};white-space:nowrap;"><b>${esc(r.status)}</b> &middot; ${chip} ${r.confidence != null ? r.confidence + '%' : ''}</div>
        </div>
        <div style="font-size:.7rem;color:var(--muted);margin-top:2px;">${esc(r.account_label || '')} &middot; ${esc(r.reason || '')}${r.sent_at ? ' &middot; sent ' + esc(r.sent_at) : ''}</div>
        ${r.draft ? `<textarea id="mail-log-draft-${r.id}" rows="${actionable ? 6 : 3}" style="width:100%;margin-top:8px;font-size:.78rem;" ${actionable ? '' : 'readonly'}>${esc(r.draft)}</textarea>` : ''}
        ${actionable ? `
        <div style="display:flex;gap:8px;margin-top:8px;">
          <button class="btn-sm primary" onclick="mailLogSend(${r.id})" title="Approve: send this reply (with your edits) from the account it arrived on. This really emails the customer.">&#128233; Approve &amp; send</button>
          <button class="btn-sm" onclick="mailLogDismiss(${r.id})" title="Dismiss without replying. Stays in the trail as 'dismissed'.">Dismiss</button>
        </div>` : ''}
      </div>`;
    }).join('');
  } catch (e) {
    el.innerHTML = `<div style="font-size:.8rem;color:var(--muted);">${esc(e.message)}</div>`;
  }
}

async function mailLogSend(id) {
  const ta = document.getElementById(`mail-log-draft-${id}`);
  try {
    await api(`/api/mail/log/${id}/send`, { method: 'POST', body: JSON.stringify({ body: ta ? ta.value : null }) });
    toast('📨 Reply approved & sent');
    loadMailReview(); loadMailOverview();
  } catch (e) { toast('Send failed: ' + e.message, 'error'); }
}

async function mailLogDismiss(id) {
  try { await api(`/api/mail/log/${id}/dismiss`, { method: 'POST' }); loadMailReview(); loadMailOverview(); }
  catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function mailGateRun() {
  try {
    const r = await api('/api/mail/gate/run', { method: 'POST' });
    toast(r.already_running ? 'Gate batch already running' : '🚦 Gate batch queued — check the Review queue shortly');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

/* ── configuration section ─────────────────────────────────────────────────── */
function renderMailConfig() {
  const el = document.getElementById('mail-config-body');
  if (!el || !_mailOv) return;
  const ov = _mailOv;
  const g = ov.gate || {};
  const profOpts = pid => '<option value="">(none)</option>' + (ov.profiles || []).map(p =>
    `<option value="${p.id}"${p.id === pid ? ' selected' : ''}>${esc(p.name)}</option>`).join('');

  el.innerHTML = `
  <div class="settings-grid">
    <!-- ── accounts ── -->
    <div class="settings-group" style="grid-column:1/-1;">
      <div class="settings-group-title">&#128231; Mail accounts</div>
      <div style="font-size:.76rem;color:var(--muted);margin-bottom:10px;">Each account is a mailbox the desk reads &amp; replies from — generic IMAP/SMTP (any provider, incl. self-hosted Mailcow) or Gmail via OAuth. Bind a business profile so drafts use the right terms.</div>
      <div id="mail-acct-list">${(ov.accounts || []).map(a => `
        <div class="card" style="padding:10px 14px;margin-bottom:8px;display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center;">
          <div style="font-size:.82rem;">
            <b>${esc(a.label)}</b> &lt;${esc(a.email)}&gt;
            <span style="color:var(--muted);font-size:.7rem;">&middot; ${a.provider === 'gmail' ? (a.gmail_connected ? 'Gmail &#10003; connected' : 'Gmail &#9888; not connected') : `IMAP ${esc(a.imap_host)}:${a.imap_port}`}
            ${a.enabled ? '' : ' &middot; <span class="c-warn">disabled</span>'}${a.gate_enabled ? ' &middot; gate on' : ' &middot; gate off'}</span>
            ${a.last_error ? `<div style="color:var(--warning,#e6a23c);font-size:.68rem;">&#9888; ${esc(a.last_error)}</div>` : ''}
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            <button class="btn-sm" onclick="mailTestAccount(${a.id})" title="Try IMAP login + SMTP login with the saved credentials. Nothing is read or sent.">&#128268; Test</button>
            ${a.provider === 'gmail' ? (a.gmail_connected
              ? `<button class="btn-sm" onclick="mailGmailDisconnect(${a.id})">Disconnect Gmail</button>`
              : `<button class="btn-sm" onclick="mailGmailConnect(${a.id})" title="Open Google's consent screen (OAuth + PKCE). Needs the OAuth client below saved first.">&#128279; Connect Gmail</button>`) : ''}
            <button class="btn-sm" onclick="mailEditAccount(${a.id})">&#9998; Edit</button>
            <button class="btn-sm danger" onclick="mailDeleteAccount(${a.id})">&times;</button>
          </div>
        </div>`).join('') || '<div style="font-size:.78rem;color:var(--muted);">No accounts yet — add one below.</div>'}
      </div>
      <div id="mail-acct-form" style="margin-top:10px;"></div>
      <button class="btn-sm primary" id="mail-acct-add" onclick="mailEditAccount(null)" style="margin-top:6px;">+ Add account</button>
      <div style="margin-top:14px;padding:10px;background:var(--surface);border-radius:8px;border:1px solid var(--border);">
        <b style="font-size:.78rem;">&#128272; Gmail OAuth app</b> ${hlp('One Google OAuth client for this install (Google Cloud Console → Credentials → OAuth client ID, type Web, redirect URI shown below). Needed only for gmail-provider accounts. The secret is encrypted at rest.')}
        <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;">
          <input type="text" id="mail-gmail-cid" placeholder="Client ID${ov.gmail_client_set ? ' (saved)' : ''}" style="flex:1;min-width:220px;">
          <input type="password" id="mail-gmail-csec" placeholder="Client secret${ov.gmail_client_set ? ' (saved)' : ''}" style="flex:1;min-width:180px;">
          <button class="btn-sm" onclick="mailSaveGmailApp()">&#128190; Save</button>
        </div>
      </div>
    </div>

    <!-- ── business profiles ── -->
    <div class="settings-group" style="grid-column:1/-1;">
      <div class="settings-group-title">&#127970; Business profiles</div>
      <div style="font-size:.76rem;color:var(--muted);margin-bottom:10px;">A profile is WHO the AI writes as: business name &amp; description, the non-negotiable terms, the pricing model, tone and signature. The drafter prompt is a template these fields fill (Settings &rarr; Prompts &rarr; &ldquo;Mail: reply / quote draft&rdquo;).</div>
      <div id="mail-prof-list">${(ov.profiles || []).map(p => `
        <div class="card" style="padding:10px 14px;margin-bottom:8px;display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center;">
          <div style="font-size:.82rem;"><b>${esc(p.name)}</b>${p.is_default ? ' <span style="font-size:.64rem;color:var(--accent);">default</span>' : ''}
            <span style="color:var(--muted);font-size:.7rem;">&middot; ${esc(p.business_type || 'general')} &middot; ${esc((p.description || '').slice(0, 70))}</span></div>
          <div style="display:flex;gap:6px;">
            <button class="btn-sm" onclick="mailEditProfile(${p.id})">&#9998; Edit</button>
            <button class="btn-sm danger" onclick="mailDeleteProfile(${p.id})">&times;</button>
          </div>
        </div>`).join('') || '<div style="font-size:.78rem;color:var(--muted);">No profiles yet — add one so AI drafts know your terms.</div>'}
      </div>
      <div id="mail-prof-form" style="margin-top:10px;"></div>
      <button class="btn-sm primary" onclick="mailEditProfile(null)" style="margin-top:6px;">+ Add profile</button>
    </div>

    <!-- ── auto-reply gate ── -->
    <div class="settings-group" style="grid-column:1/-1;" id="mail-gate-section">
      <div class="settings-group-title">&#128678; Auto-reply gate</div>
      <div style="font-size:.76rem;color:var(--muted);margin-bottom:12px;line-height:1.5;">
        New unseen mail is classified and (per mode) auto-drafted or auto-answered. <b>Guardrails:</b> quotes and anything
        committing money/pricing are ALWAYS held for you, even in full_auto; below-threshold confidence holds; every action
        is logged in the Review queue. Applies per account (&ldquo;gate on/off&rdquo; on each account above).
      </div>
      <div style="display:flex;gap:18px;flex-wrap:wrap;align-items:flex-end;">
        <div class="field" style="margin:0;">
          <label>Enabled ${hlp('Master switch for the scheduled gate. Off = nothing runs automatically (today’s flow). Saved instantly.')}</label>
          <div class="toggle ${g.enabled ? 'on' : ''}" id="mail-gate-enabled"></div>
        </div>
        <div class="field" style="margin:0;min-width:180px;">
          <label>Mode ${hlp('manual: nothing automatic — you click Draft. auto_draft: new mail gets an AI draft for your review. full_auto: routine, confident, FAQ/order answers are auto-SENT; everything else is held. Quotes always wait for you.')}</label>
          <select id="mail-gate-mode">
            ${['manual', 'auto_draft', 'full_auto'].map(m => `<option value="${m}"${(g.mode || 'manual') === m ? ' selected' : ''}>${m}</option>`).join('')}
          </select>
        </div>
        <div class="field" style="margin:0;min-width:190px;">
          <label>Auto-send at <b id="mail-gate-conf-val">${g.confidence ?? 80}</b>% ${hlp('Minimum classifier confidence before full_auto may send. Below it, the draft is held for you.')}</label>
          <input type="range" id="mail-gate-conf" min="50" max="100" step="5" value="${g.confidence ?? 80}">
        </div>
        <div class="field" style="margin:0;width:110px;">
          <label>Interval (min) ${hlp('How often the gate checks for new mail. Minimum 5; default 15.')}</label>
          <input type="number" id="mail-gate-interval" min="5" step="5" value="${g.interval_min || 15}">
        </div>
        <div class="field" style="margin:0;width:110px;">
          <label>Batch size ${hlp('Max messages processed per run — bounded bites, never an endless task. Default 5.')}</label>
          <input type="number" id="mail-gate-batch" min="1" step="1" value="${g.batch_size || 5}">
        </div>
      </div>
      <div style="display:flex;gap:18px;flex-wrap:wrap;margin-top:12px;">
        <div class="field" style="flex:1;min-width:240px;margin:0;">
          <label>Sender allow list ${hlp('Optional. When set, ONLY senders matching these patterns (substring, comma/newline separated) may be auto-answered — everyone else is held. Leave empty to allow any sender to pass the other guardrails.')}</label>
          <textarea id="mail-gate-allow" rows="2" placeholder="e.g. @etsy.com, longtimecustomer@">${esc(g.allow || '')}</textarea>
        </div>
        <div class="field" style="flex:1;min-width:240px;margin:0;">
          <label>Sender deny list ${hlp('Senders matching these patterns are skipped entirely — never replied to, never drafted.')}</label>
          <textarea id="mail-gate-deny" rows="2" placeholder="e.g. noreply@, @spamdomain.com">${esc(g.deny || '')}</textarea>
        </div>
      </div>
    </div>

    <!-- ── FAQ manager ── -->
    <div class="settings-group" style="grid-column:1/-1;">
      <div class="settings-group-title">&#128218; FAQ / Q&amp;A knowledge base</div>
      <div style="font-size:.76rem;color:var(--muted);margin-bottom:10px;">Question/answer pairs the desk answers from — matched locally on every incoming mail and confirmed by the classifier. Scope one to a profile, or leave it global.</div>
      <div id="mail-faq-list"></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;align-items:flex-start;">
        <input type="text" id="mail-faq-q" placeholder="Question (e.g. Do you ship internationally?)" style="flex:2;min-width:220px;">
        <textarea id="mail-faq-a" rows="2" placeholder="Your answer, in your words" style="flex:3;min-width:260px;"></textarea>
        <select id="mail-faq-prof" style="min-width:140px;">${profOpts(null).replace('(none)', 'All profiles')}</select>
        <button class="btn-sm primary" onclick="mailSaveFaq()">+ Add FAQ</button>
      </div>
    </div>
  </div>`;

  /* gate controls — instant save via PATCH /api/settings (same as the etsy tab) */
  const patch = async (obj, msg) => {
    try { await api('/api/settings', { method: 'PATCH', body: JSON.stringify(obj) }); toast(msg || 'Saved ✓'); return true; }
    catch (e) { toast('Error: ' + e.message, 'error'); return false; }
  };
  const tgl = document.getElementById('mail-gate-enabled');
  if (tgl) tgl.addEventListener('click', async () => {
    const on = !tgl.classList.contains('on');
    tgl.classList.toggle('on', on);
    if (!await patch({ mail_gate_enabled: on ? '1' : '0' }, `Mail gate ${on ? 'enabled' : 'disabled'}`))
      tgl.classList.toggle('on', !on);
    else loadMailOverview();
  });
  document.getElementById('mail-gate-mode')?.addEventListener('change', async e => {
    await patch({ mail_gate_mode: e.target.value }, `Gate mode: ${e.target.value}`); loadMailOverview();
  });
  const conf = document.getElementById('mail-gate-conf');
  if (conf) {
    conf.addEventListener('input', () => { const v = document.getElementById('mail-gate-conf-val'); if (v) v.textContent = conf.value; });
    conf.addEventListener('change', () => patch({ mail_gate_confidence: conf.value }));
  }
  document.getElementById('mail-gate-interval')?.addEventListener('change', e =>
    patch({ mail_gate_interval_min: String(Math.max(5, parseInt(e.target.value || '15', 10) || 15)) }));
  document.getElementById('mail-gate-batch')?.addEventListener('change', e =>
    patch({ mail_gate_batch_size: String(Math.max(1, parseInt(e.target.value || '5', 10) || 5)) }));
  document.getElementById('mail-gate-allow')?.addEventListener('change', e =>
    patch({ mail_gate_allow: e.target.value }, 'Allow list saved ✓'));
  document.getElementById('mail-gate-deny')?.addEventListener('change', e =>
    patch({ mail_gate_deny: e.target.value }, 'Deny list saved ✓'));

  loadMailFaqList();
}

/* ── accounts CRUD ─────────────────────────────────────────────────────────── */
function mailEditAccount(id) {
  const a = (id && ((_mailOv || {}).accounts || []).find(x => x.id === id)) || {
    provider: 'imap', imap_port: 993, smtp_port: 587, imap_security: 'ssl',
    smtp_security: 'starttls', verify_cert: true, enabled: true, gate_enabled: true };
  const profOpts = '<option value="">(no profile)</option>' + ((_mailOv || {}).profiles || []).map(p =>
    `<option value="${p.id}"${p.id === a.profile_id ? ' selected' : ''}>${esc(p.name)}</option>`).join('');
  const f = document.getElementById('mail-acct-form');
  f.innerHTML = `
  <div class="card" style="padding:14px 16px;">
    <b style="font-size:.84rem;">${id ? 'Edit account' : 'New account'}</b>
    <div class="settings-grid" style="margin-top:10px;">
      <div class="field"><label>Label</label><input type="text" id="ma-label" value="${esc(a.label || '')}" placeholder="Support mailbox"></div>
      <div class="field"><label>Provider ${hlp('imap = any generic IMAP/SMTP mailbox (Mailcow, Fastmail, your host’s mail…). gmail = Google account via OAuth — no password stored, uses the OAuth app below.')}</label>
        <select id="ma-provider" onchange="document.getElementById('ma-imap-cfg').style.display=this.value==='imap'?'':'none'">
          <option value="imap"${a.provider !== 'gmail' ? ' selected' : ''}>Generic IMAP/SMTP</option>
          <option value="gmail"${a.provider === 'gmail' ? ' selected' : ''}>Gmail (OAuth)</option>
        </select></div>
      <div class="field"><label>Email address</label><input type="text" id="ma-email" value="${esc(a.email || '')}" placeholder="you@yourdomain.com"></div>
      <div class="field"><label>From display name ${hlp('The human name on outgoing mail, e.g. your business name.')}</label><input type="text" id="ma-display" value="${esc(a.display_name || '')}"></div>
      <div class="field"><label>Business profile ${hlp('Drafts from this account use this profile’s terms/pricing/tone/signature.')}</label><select id="ma-profile">${profOpts}</select></div>
      <div class="field"><label>Signature override ${hlp('Optional per-account signature; empty = the profile’s.')}</label><input type="text" id="ma-sig" value="${esc(a.signature || '')}"></div>
    </div>
    <div id="ma-imap-cfg" style="${a.provider === 'gmail' ? 'display:none;' : ''}">
      <div class="settings-grid" style="margin-top:4px;">
        <div class="field"><label>Username ${hlp('IMAP/SMTP login; empty = the email address.')}</label><input type="text" id="ma-user" value="${esc(a.username || '')}"></div>
        <div class="field"><label>Password ${hlp('Stored encrypted at rest (same scheme as every other credential). Leave blank on edit to keep the saved one.')}</label><input type="password" id="ma-pass" placeholder="${a.password_set ? '(saved — blank keeps it)' : ''}"></div>
        <div class="field"><label>IMAP host</label><input type="text" id="ma-ihost" value="${esc(a.imap_host || '')}" placeholder="127.0.0.1"></div>
        <div class="field"><label>IMAP port / security</label>
          <div style="display:flex;gap:6px;"><input type="number" id="ma-iport" value="${a.imap_port || 993}" style="width:90px;">
          <select id="ma-isec">${['ssl', 'starttls', 'plain'].map(s => `<option${(a.imap_security || 'ssl') === s ? ' selected' : ''}>${s}</option>`).join('')}</select></div></div>
        <div class="field"><label>SMTP host</label><input type="text" id="ma-shost" value="${esc(a.smtp_host || '')}" placeholder="127.0.0.1"></div>
        <div class="field"><label>SMTP port / security</label>
          <div style="display:flex;gap:6px;"><input type="number" id="ma-sport" value="${a.smtp_port || 587}" style="width:90px;">
          <select id="ma-ssec">${['starttls', 'ssl', 'plain'].map(s => `<option${(a.smtp_security || 'starttls') === s ? ' selected' : ''}>${s}</option>`).join('')}</select></div></div>
        <div class="field"><label>Verify TLS certificate ${hlp('Turn OFF only for self-signed certs on a box you trust (e.g. Mailcow on localhost).')}</label>
          <div class="toggle ${a.verify_cert ? 'on' : ''}" id="ma-verify" onclick="this.classList.toggle('on')"></div></div>
      </div>
    </div>
    <div style="display:flex;gap:16px;align-items:center;margin-top:8px;flex-wrap:wrap;">
      <label style="font-size:.76rem;display:flex;gap:6px;align-items:center;">Enabled
        <div class="toggle ${a.enabled ? 'on' : ''}" id="ma-enabled" onclick="this.classList.toggle('on')"></div></label>
      <label style="font-size:.76rem;display:flex;gap:6px;align-items:center;">Auto-reply gate applies ${hlp('Off = the gate never touches this account; you handle it fully manually.')}
        <div class="toggle ${a.gate_enabled ? 'on' : ''}" id="ma-gate" onclick="this.classList.toggle('on')"></div></label>
    </div>
    <div style="display:flex;gap:8px;margin-top:12px;">
      <button class="btn-sm primary" onclick="mailSaveAccount(${id || 'null'})">&#128190; Save account</button>
      <button class="btn-sm" onclick="document.getElementById('mail-acct-form').innerHTML=''">Cancel</button>
    </div>
  </div>`;
  f.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function mailSaveAccount(id) {
  const v = i => (document.getElementById(i) || {}).value || '';
  const on = i => document.getElementById(i)?.classList.contains('on');
  const body = {
    id: id || null, label: v('ma-label'), provider: v('ma-provider'), email: v('ma-email'),
    display_name: v('ma-display'), username: v('ma-user'), password: v('ma-pass'),
    imap_host: v('ma-ihost'), imap_port: parseInt(v('ma-iport') || '993', 10),
    imap_security: v('ma-isec') || 'ssl', smtp_host: v('ma-shost'),
    smtp_port: parseInt(v('ma-sport') || '587', 10), smtp_security: v('ma-ssec') || 'starttls',
    verify_cert: !!on('ma-verify'), signature: v('ma-sig'),
    profile_id: v('ma-profile') ? parseInt(v('ma-profile'), 10) : null,
    enabled: !!on('ma-enabled'), gate_enabled: !!on('ma-gate'),
  };
  if (!body.email) { toast('Email address is required', 'error'); return; }
  try {
    await api('/api/mail/accounts', { method: 'POST', body: JSON.stringify(body) });
    toast('Account saved ✓');
    document.getElementById('mail-acct-form').innerHTML = '';
    await loadMailOverview();
  } catch (e) { toast('Save failed: ' + e.message, 'error'); }
}

async function mailDeleteAccount(id) {
  if (!confirm('Delete this mail account? Its credentials are removed; the review trail stays.')) return;
  try { await api(`/api/mail/accounts/${id}`, { method: 'DELETE' }); toast('Account deleted'); if (_mailAcct === id) _mailAcct = null; await loadMailOverview(); await loadMailInbox(); }
  catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function mailTestAccount(id) {
  toast('Testing connection…');
  try {
    const r = await api(`/api/mail/accounts/${id}/test`, { method: 'POST' });
    if (r.ok) toast('✓ IMAP and SMTP both connect & authenticate');
    else toast(`IMAP: ${r.imap.ok ? 'OK' : r.imap.error} · SMTP: ${r.smtp.ok ? 'OK' : r.smtp.error}`, 'error');
    loadMailOverview();
  } catch (e) { toast('Test failed: ' + e.message, 'error'); }
}

async function mailSaveGmailApp() {
  const cid = document.getElementById('mail-gmail-cid').value.trim();
  const sec = document.getElementById('mail-gmail-csec').value.trim();
  if (!cid && !sec) { toast('Nothing to save', 'error'); return; }
  try {
    await api('/api/mail/gmail/app', { method: 'POST', body: JSON.stringify({
      ...(cid ? { gmail_client_id: cid } : {}), ...(sec ? { gmail_client_secret: sec } : {}) }) });
    toast('Gmail OAuth app saved ✓'); loadMailOverview();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function mailGmailConnect(id) {
  try {
    const r = await api(`/api/mail/gmail/connect?account_id=${id}`);
    if (r.url) window.open(r.url, '_blank');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.addEventListener('message', ev => {
  if (ev.data === 'gmail_connected') { toast('✅ Gmail connected'); loadMailOverview(); }
});

async function mailGmailDisconnect(id) {
  if (!confirm('Disconnect Gmail for this account?')) return;
  try { await api(`/api/mail/gmail/disconnect/${id}`, { method: 'DELETE' }); toast('Gmail disconnected'); loadMailOverview(); }
  catch (e) { toast('Error: ' + e.message, 'error'); }
}

/* ── profiles CRUD ─────────────────────────────────────────────────────────── */
function mailEditProfile(id) {
  const p = (id && ((_mailOv || {}).profiles || []).find(x => x.id === id)) || { pricing: '{}' };
  let pr = {}; try { pr = JSON.parse(p.pricing || '{}'); } catch {}
  const f = document.getElementById('mail-prof-form');
  f.innerHTML = `
  <div class="card" style="padding:14px 16px;">
    <b style="font-size:.84rem;">${id ? 'Edit profile' : 'New business profile'}</b>
    <div class="settings-grid" style="margin-top:10px;">
      <div class="field"><label>Business name</label><input type="text" id="mp-name" value="${esc(p.name || '')}" placeholder="Acme Carpentry"></div>
      <div class="field"><label>Type ${hlp('Short label: carpentry, store, consulting… (helps you tell profiles apart).')}</label><input type="text" id="mp-type" value="${esc(p.business_type || '')}"></div>
      <div class="field" style="grid-column:1/-1;"><label>Description ${hlp('What the business is/does — the AI’s one-line context about who it writes as.')}</label>
        <input type="text" id="mp-desc" value="${esc(p.description || '')}"></div>
      <div class="field" style="grid-column:1/-1;"><label>Terms &amp; rules ${hlp('The NON-NEGOTIABLE reply/quote rules, one per line. These go into every draft verbatim — e.g. hourly-only pricing, what you don’t do, materials policy.')}</label>
        <textarea id="mp-terms" rows="6">${esc(p.terms || '')}</textarea></div>
      <div class="field"><label>Hourly rate $ ${hlp('Pricing model — used by the quote builder and stated in drafts. Leave 0/blank if not hourly.')}</label>
        <input type="number" id="mp-rate" step="1" value="${pr.hourly_rate || ''}"></div>
      <div class="field"><label>Minimum hours</label><input type="number" id="mp-min" step="0.5" value="${pr.minimum_hours || ''}"></div>
      <div class="field"><label>Materials policy</label><input type="text" id="mp-mat" value="${esc(pr.materials_policy || '')}" placeholder="customer buys/provides all materials"></div>
      <div class="field"><label>Tax note</label><input type="text" id="mp-tax" value="${esc(pr.tax_note || '')}" placeholder="e.g. plus applicable sales tax"></div>
      <div class="field"><label>Tone / voice</label><input type="text" id="mp-tone" value="${esc(p.tone || 'warm, concise, professional')}"></div>
      <div class="field"><label>Signature ${hlp('How replies sign off, e.g. “Sam — Acme Carpentry”.')}</label><input type="text" id="mp-sig" value="${esc(p.signature || '')}"></div>
      <div class="field"><label>Default profile ${hlp('Used when an account has no profile bound.')}</label>
        <div class="toggle ${p.is_default ? 'on' : ''}" id="mp-default" onclick="this.classList.toggle('on')"></div></div>
    </div>
    <div style="display:flex;gap:8px;margin-top:12px;">
      <button class="btn-sm primary" onclick="mailSaveProfile(${id || 'null'})">&#128190; Save profile</button>
      <button class="btn-sm" onclick="document.getElementById('mail-prof-form').innerHTML=''">Cancel</button>
    </div>
  </div>`;
  f.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function mailSaveProfile(id) {
  const v = i => (document.getElementById(i) || {}).value || '';
  const pricing = {};
  if (v('mp-rate')) pricing.hourly_rate = parseFloat(v('mp-rate'));
  if (v('mp-min')) pricing.minimum_hours = parseFloat(v('mp-min'));
  if (v('mp-mat')) pricing.materials_policy = v('mp-mat');
  if (v('mp-tax')) pricing.tax_note = v('mp-tax');
  const body = {
    id: id || null, name: v('mp-name'), business_type: v('mp-type'), description: v('mp-desc'),
    terms: v('mp-terms'), pricing: JSON.stringify(pricing), tone: v('mp-tone'),
    signature: v('mp-sig'), is_default: document.getElementById('mp-default')?.classList.contains('on'),
  };
  if (!body.name) { toast('Business name is required', 'error'); return; }
  try {
    await api('/api/mail/profiles', { method: 'POST', body: JSON.stringify(body) });
    toast('Profile saved ✓');
    document.getElementById('mail-prof-form').innerHTML = '';
    await loadMailOverview();
  } catch (e) { toast('Save failed: ' + e.message, 'error'); }
}

async function mailDeleteProfile(id) {
  if (!confirm('Delete this profile? Its FAQs are removed and accounts bound to it fall back to the default.')) return;
  try { await api(`/api/mail/profiles/${id}`, { method: 'DELETE' }); toast('Profile deleted'); await loadMailOverview(); }
  catch (e) { toast('Error: ' + e.message, 'error'); }
}

/* ── FAQ manager ───────────────────────────────────────────────────────────── */
async function loadMailFaqList() {
  const el = document.getElementById('mail-faq-list');
  if (!el) return;
  try {
    const d = await api('/api/mail/faq');
    const profs = {};
    (((_mailOv || {}).profiles) || []).forEach(p => profs[p.id] = p.name);
    el.innerHTML = (d.faq || []).map(f => `
      <div class="card" style="padding:9px 13px;margin-bottom:6px;display:flex;justify-content:space-between;gap:10px;align-items:center;${f.enabled ? '' : 'opacity:.5;'}">
        <div style="font-size:.78rem;flex:1;">
          <b>Q:</b> ${esc(f.question)}<br><b>A:</b> <span style="color:var(--muted);">${esc(f.answer.slice(0, 140))}${f.answer.length > 140 ? '…' : ''}</span>
          <span style="font-size:.64rem;color:var(--muted);">&middot; ${f.profile_id ? esc(profs[f.profile_id] || ('profile ' + f.profile_id)) : 'all profiles'}${f.hits ? ` &middot; used ${f.hits}&times;` : ''}</span>
        </div>
        <div style="display:flex;gap:6px;">
          <button class="btn-sm" onclick="mailToggleFaq(${f.id}, ${f.enabled ? 0 : 1}, this)" title="${f.enabled ? 'Disable' : 'Enable'} this FAQ">${f.enabled ? '&#9208;' : '&#9654;'}</button>
          <button class="btn-sm danger" onclick="mailDeleteFaq(${f.id})">&times;</button>
        </div>
      </div>`).join('') || '<div style="font-size:.78rem;color:var(--muted);">No FAQs yet.</div>';
    el.dataset.raw = JSON.stringify(d.faq || []);
  } catch (e) { el.innerHTML = `<div style="font-size:.78rem;color:var(--muted);">${esc(e.message)}</div>`; }
}

async function mailSaveFaq() {
  const q = document.getElementById('mail-faq-q').value.trim();
  const a = document.getElementById('mail-faq-a').value.trim();
  const p = document.getElementById('mail-faq-prof').value;
  if (!q || !a) { toast('Need both a question and an answer', 'error'); return; }
  try {
    await api('/api/mail/faq', { method: 'POST', body: JSON.stringify({
      question: q, answer: a, profile_id: p ? parseInt(p, 10) : null }) });
    toast('FAQ added ✓');
    document.getElementById('mail-faq-q').value = '';
    document.getElementById('mail-faq-a').value = '';
    loadMailFaqList(); loadMailOverview();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function mailToggleFaq(id, enable) {
  try {
    const raw = JSON.parse(document.getElementById('mail-faq-list').dataset.raw || '[]');
    const f = raw.find(x => x.id === id);
    if (!f) return;
    await api('/api/mail/faq', { method: 'POST', body: JSON.stringify({
      id, question: f.question, answer: f.answer, profile_id: f.profile_id, enabled: !!enable }) });
    loadMailFaqList();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function mailDeleteFaq(id) {
  try { await api(`/api/mail/faq/${id}`, { method: 'DELETE' }); loadMailFaqList(); loadMailOverview(); }
  catch (e) { toast('Error: ' + e.message, 'error'); }
}

/* exports */
window.loadMailInbox = loadMailInbox;
window.openMailMsg = openMailMsg;
window.mailDraftQuote = mailDraftQuote;
window.mailSend = mailSend;
window.mailToggleQuoteBuilder = mailToggleQuoteBuilder;
window.renderMailQuoteBuilder = renderMailQuoteBuilder;
window.mailInsertQuote = mailInsertQuote;
window.loadMailReview = loadMailReview;
window.mailLogSend = mailLogSend;
window.mailLogDismiss = mailLogDismiss;
window.mailGateRun = mailGateRun;
window.mailEditAccount = mailEditAccount;
window.mailSaveAccount = mailSaveAccount;
window.mailDeleteAccount = mailDeleteAccount;
window.mailTestAccount = mailTestAccount;
window.mailSaveGmailApp = mailSaveGmailApp;
window.mailGmailConnect = mailGmailConnect;
window.mailGmailDisconnect = mailGmailDisconnect;
window.mailEditProfile = mailEditProfile;
window.mailSaveProfile = mailSaveProfile;
window.mailDeleteProfile = mailDeleteProfile;
window.mailSaveFaq = mailSaveFaq;
window.mailToggleFaq = mailToggleFaq;
window.mailDeleteFaq = mailDeleteFaq;

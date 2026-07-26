'use strict';

/* ══ AI ASSISTANT — agentic chat with live tool calls, approvals, skills ══
   Backend: /api/agent/* (routers/agent.py). The assistant runs a tool loop over
   the store's own API; this UI polls /api/agent/events and renders user msgs,
   collapsible tool call/result cards, approval prompts (with per-category
   "always allow" toggles), plus saved skills and assistant settings. */

let _agConv = null;          // active conversation id
let _agAfter = 0;            // last seen message id
let _agPoll = null;          // poll timer
let _agStatus = 'idle';

function _agCss() {
  if (document.getElementById('agent-css2')) return;
  const st = document.createElement('style');
  st.id = 'agent-css2';
  st.textContent = `
    .agent-toolbar { display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin:8px 0; }
    .agent-skill-chip { background:var(--surface2); border:1px solid var(--border); border-radius:14px;
      padding:3px 10px; font-size:.72rem; cursor:pointer; }
    .agent-skill-chip:hover { border-color:var(--accent); }
    .agent-msg.tool-card { align-self:flex-start; max-width:94%; width:94%; background:var(--surface2);
      border:1px solid var(--border); border-left:3px solid var(--accent); font-size:.74rem; padding:6px 10px; }
    .agent-msg.tool-card details summary { cursor:pointer; color:var(--muted); }
    .agent-msg.tool-card pre { margin:6px 0 0; max-height:240px; overflow:auto; white-space:pre-wrap;
      word-break:break-word; font-size:.7rem; background:var(--surface); padding:6px; border-radius:6px; }
    .agent-msg.approval { align-self:flex-start; max-width:94%; border:1px solid #d97706;
      border-left:4px solid #d97706; background:var(--surface2); font-size:.78rem; }
    .agent-msg.status { align-self:center; color:var(--muted); font-size:.72rem; font-style:italic;
      background:transparent; border:none; }
    .agent-msg.errormsg { align-self:flex-start; border:1px solid #b91c1c; background:var(--surface2); }
    .agent-settings-pane { background:var(--surface2); border:1px solid var(--border); border-radius:8px;
      padding:10px 12px; margin-bottom:8px; font-size:.76rem; }
    .agent-settings-pane label { display:flex; gap:8px; align-items:center; margin:3px 0; cursor:pointer; }
    .agent-settings-pane .cat-desc { color:var(--muted); font-size:.68rem; }`;
  document.head.appendChild(st);
}

async function renderAgent() {
  _agCss();
  document.getElementById('main-content').innerHTML = `
    <div class="view-header">
      <div class="view-title">&#129302; AI Assistant</div>
      <div class="view-sub">Agentic assistant with tools over the whole store — it can queue Studio jobs,
        run dev-swarm tasks, query the knowledge graph, read anything, and asks approval for risky actions.</div>
      <div class="agent-toolbar">
        <button class="btn-sm" onclick="agentNewChat()">&#10133; New Chat</button>
        <select id="agent-conv-picker" class="btn-sm" onchange="agentLoadConv(parseInt(this.value)||null)" style="max-width:220px;"></select>
        <button class="btn-sm" onclick="agentDeleteConv()" title="Delete this conversation">&#128465;</button>
        <button class="btn-sm" id="agent-settings-btn" onclick="agentToggleSettings()">&#9881; Approvals</button>
        <button class="btn-sm" onclick="agentManageSkills()">&#128218; Skills</button>
        <span id="agent-skill-chips" style="display:contents;"></span>
      </div>
    </div>
    <div id="agent-settings-pane" class="agent-settings-pane" style="display:none;"></div>
    <div id="agent-voice" class="agent-voice-bar"></div>
    <div class="agent-chat" id="agent-chat">
      <div class="agent-msgs" id="agent-msgs"></div>
      <div class="agent-input-row">
        <textarea id="agent-input" placeholder="Ask for anything — it can actually do it… (Enter to send)" rows="3"></textarea>
        <div style="display:flex;flex-direction:column;gap:4px;">
          <button class="btn-sm primary" id="agent-send" style="height:38px;min-width:70px;">&#9658; Send</button>
          <button class="btn-sm" id="agent-stop" style="height:18px;display:none;" onclick="agentStop()">&#9632; Stop</button>
        </div>
      </div>
    </div>`;

  const input = document.getElementById('agent-input');
  document.getElementById('agent-send').addEventListener('click', agentSend);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); agentSend(); }
  });

  // Voice ("Jarvis") lives inline in this tab. Re-mounts each render (main-content
  // is rewritten); VoiceJarvis.init is re-mountable and reuses this conversation.
  if (window.VoiceJarvis) VoiceJarvis.init(document.getElementById('agent-voice'));

  await agentRefreshConvs();
  await agentRefreshSkills();
  if (_agConv) await agentLoadConv(_agConv);
  else {
    const convs = window._agConvList || [];
    if (convs.length) await agentLoadConv(convs[0].id);
    else agentWelcome();
  }
}

function agentWelcome() {
  const msgs = document.getElementById('agent-msgs');
  if (msgs) msgs.innerHTML = `<div class="agent-msg assistant">👋 I'm your store agent — I have tools now. Try:
• "Status report across all tabs"
• "Generate a retro sunset t-shirt design"
• "What dev-swarm jobs are running?"
• "How does the JellyCoin miner work?" (knowledge graph)
Risky actions (money, deletes, publishing…) pause and ask your approval — toggles in ⚙ Approvals.</div>`;
}

/* ── conversations ── */
async function agentRefreshConvs() {
  try {
    const d = await (await fetch(API + '/api/agent/conversations')).json();
    window._agConvList = d.conversations || [];
    const sel = document.getElementById('agent-conv-picker');
    if (!sel) return;
    sel.innerHTML = '<option value="">— conversations —</option>' + window._agConvList.map(c =>
      `<option value="${c.id}" ${c.id === _agConv ? 'selected' : ''}>${esc((c.title || 'chat').slice(0, 40))}</option>`).join('');
  } catch (e) { /* ignore */ }
}

async function agentLoadConv(id) {
  agentStopPolling();
  _agConv = id; _agAfter = 0;
  const msgs = document.getElementById('agent-msgs');
  if (!id) { agentWelcome(); return; }
  msgs.innerHTML = '';
  try {
    const d = await (await fetch(API + `/api/agent/conversations/${id}`)).json();
    for (const m of d.messages || []) { agentRenderMsg(m); _agAfter = m.id; }
    _agStatus = d.status || 'idle';
    agentSetBusy(_agStatus !== 'idle');
    if (_agStatus !== 'idle') agentStartPolling();
    const sel = document.getElementById('agent-conv-picker');
    if (sel) sel.value = String(id);
  } catch (e) { msgs.innerHTML = '<div class="agent-msg errormsg">Failed to load conversation: ' + esc(e.message) + '</div>'; }
}

function agentNewChat() { _agConv = null; _agAfter = 0; agentStopPolling(); agentSetBusy(false); agentWelcome(); agentRefreshConvs(); }

async function agentDeleteConv() {
  if (!_agConv || !confirm('Delete this conversation?')) return;
  await fetch(API + `/api/agent/conversations/${_agConv}`, { method: 'DELETE' });
  agentNewChat();
}

/* ── sending + polling ── */
async function agentSend() {
  const input = document.getElementById('agent-input');
  const msg = input.value.trim();
  if (!msg || _agStatus === 'running') return;
  input.value = '';
  try {
    const resp = await fetch(API + '/api/agent/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, conversation_id: _agConv })
    });
    const d = await resp.json();
    if (!resp.ok) throw new Error(d.detail || 'request failed');
    _agConv = d.conversation_id;
    agentSetBusy(true);
    agentStartPolling();
    agentRefreshConvs();
  } catch (e) {
    agentRenderMsg({ kind: 'error', content: 'Error: ' + e.message });
  }
}

function agentStartPolling() {
  agentStopPolling();
  _agPoll = setInterval(agentPollOnce, 1300);
  agentPollOnce();
}
function agentStopPolling() { if (_agPoll) { clearInterval(_agPoll); _agPoll = null; } }

async function agentPollOnce() {
  if (!_agConv) return;
  try {
    const d = await (await fetch(API + `/api/agent/events?conversation_id=${_agConv}&after=${_agAfter}`)).json();
    for (const m of d.messages || []) { agentRenderMsg(m); _agAfter = m.id; }
    _agStatus = d.status;
    if (d.status === 'idle') { agentSetBusy(false); agentStopPolling(); }
    else agentSetBusy(true);
  } catch (e) { /* transient */ }
}

async function agentStop() {
  if (!_agConv) return;
  try { await fetch(API + '/api/agent/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ conversation_id: _agConv }) }); } catch (e) {}
  agentPollOnce();
}

function agentSetBusy(busy) {
  const send = document.getElementById('agent-send'), stop = document.getElementById('agent-stop');
  if (send) send.disabled = !!busy;
  if (stop) stop.style.display = busy ? '' : 'none';
}

/* ── message rendering ── */
async function agentRememberMsg(text, btn) {
  // Owner-captured answer → shared memory. Owner's explicit click IS the review,
  // so it goes straight to trusted (remember + promote).
  if (!text || !text.trim()) return;
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const r = await api('/api/knowledge/remember', { method: 'POST', body: JSON.stringify({ text: text.slice(0, 4000), agent: 'assistant', tags: 'answer' }) });
    if (r.ok && r.id) await api(`/api/knowledge/notes/${r.id}/promote`, { method: 'POST' });
    if (btn) btn.textContent = '🧠 Saved ✓';
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = '🧠 Remember'; }
    if (typeof toast === 'function') toast('remember failed: ' + e.message, 'error');
  }
}

function agentRenderMsg(m) {
  const msgs = document.getElementById('agent-msgs');
  if (!msgs) return;
  const kind = m.kind || m.role || 'assistant';
  const div = document.createElement('div');
  if (kind === 'user') { div.className = 'agent-msg user'; div.textContent = m.content; }
  else if (kind === 'assistant') {
    div.className = 'agent-msg assistant';
    const body = document.createElement('div'); body.textContent = m.content; div.appendChild(body);
    const rb = document.createElement('button');
    rb.className = 'btn-sm'; rb.textContent = '🧠 Remember';
    rb.title = 'Save this answer to shared memory (trusted)';
    rb.style.cssText = 'margin-top:6px;font-size:.7rem;opacity:.7;';
    rb.addEventListener('click', () => agentRememberMsg(m.content, rb));
    div.appendChild(rb);
  }
  else if (kind === 'status') { div.className = 'agent-msg status'; div.textContent = m.content; }
  else if (kind === 'error') { div.className = 'agent-msg errormsg'; div.textContent = '❌ ' + m.content; }
  else if (kind === 'tool_call') {
    div.className = 'agent-msg tool-card';
    const meta = m.meta || {};
    div.innerHTML = `<details><summary>🔧 <b>${esc(meta.tool || 'tool')}</b> ${esc(meta.method || '')} ${esc(meta.path || '')}</summary><pre>${esc(m.content)}</pre></details>`;
  } else if (kind === 'tool_result') {
    div.className = 'agent-msg tool-card';
    const ok = (m.meta && m.meta.status >= 200 && m.meta.status < 300);
    let pretty = m.content;
    try { pretty = JSON.stringify(JSON.parse(m.content), null, 1).slice(0, 8000); } catch (e) {}
    div.innerHTML = `<details><summary>${ok ? '✅' : '⚠️'} result of <b>${esc((m.meta || {}).tool || 'tool')}</b> (HTTP ${esc(String((m.meta || {}).status ?? '?'))})</summary><pre>${esc(pretty)}</pre></details>`;
  } else if (kind === 'tool_error') {
    div.className = 'agent-msg tool-card';
    div.innerHTML = `<details open><summary>⚠️ tool error</summary><pre>${esc(m.content)}</pre></details>`;
  } else if (kind === 'approval_request') {
    div.className = 'agent-msg approval';
    const meta = m.meta || {};
    const pending = !m._resolved;
    div.innerHTML = `<div>🔐 <b>Approval needed</b> — <i>${esc(meta.category || '?')}</i><br>
      ${esc(m.content)}<pre style="max-height:140px;overflow:auto;font-size:.68rem;">${esc(JSON.stringify(meta.args || {}, null, 1))}</pre>
      <div class="ap-actions" data-ap="${meta.approval_id}">
        <button class="btn-sm primary" onclick="agentApprove(${meta.approval_id}, true, this)">✔ Approve</button>
        <button class="btn-sm" onclick="agentApprove(${meta.approval_id}, false, this)">✖ Deny</button>
        <label style="font-size:.68rem;margin-left:6px;"><input type="checkbox" id="ap-rem-${meta.approval_id}"> always allow ${esc(meta.category || '')}</label>
      </div></div>`;
  } else if (kind === 'approval_result') {
    div.className = 'agent-msg status'; div.textContent = m.content;
    // hide the buttons of the request it answered
    const apId = (m.meta || {}).approval_id;
    const act = msgs.querySelector(`.ap-actions[data-ap="${apId}"]`);
    if (act) act.innerHTML = `<i>${(m.meta || {}).approved ? '✔ approved' : '✖ denied'}</i>`;
  } else { div.className = 'agent-msg status'; div.textContent = m.content; }
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

async function agentApprove(apId, ok, btn) {
  const rem = document.getElementById('ap-rem-' + apId);
  try {
    btn.disabled = true;
    const r = await fetch(API + '/api/agent/approve', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_id: apId, approve: ok, remember: !!(rem && rem.checked && ok) })
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'failed');
    agentSetBusy(true); agentStartPolling();
  } catch (e) { alert('Approval failed: ' + e.message); btn.disabled = false; }
}

/* ── assistant settings (per-category auto-approve toggles) ── */
async function agentToggleSettings() {
  const pane = document.getElementById('agent-settings-pane');
  if (pane.style.display !== 'none') { pane.style.display = 'none'; return; }
  pane.style.display = '';
  pane.innerHTML = 'Loading…';
  const d = await (await fetch(API + '/api/agent/settings')).json();
  pane.innerHTML = `<b>Auto-approve by category</b> — unchecked = the assistant pauses and asks you first.<br>` +
    d.categories.map(c => `
      <label><input type="checkbox" ${c.auto ? 'checked' : ''} ${c.locked ? 'disabled' : ''}
        onchange="agentSaveToggle('${c.key}', this.checked)">
        <b>${esc(c.label)}</b> <span class="cat-desc">${esc(c.desc)}</span></label>`).join('') +
    `<label style="margin-top:6px;">Max tool steps per run:
      <input type="number" min="1" max="25" value="${d.max_iters}" style="width:60px;"
        onchange="agentSaveMaxIters(this.value)"></label>`;
}
async function agentSaveToggle(cat, on) {
  await fetch(API + '/api/agent/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ toggles: { [cat]: on } }) });
}
async function agentSaveMaxIters(v) {
  await fetch(API + '/api/agent/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ max_iters: parseInt(v) || 8 }) });
}

/* ── skills ── */
async function agentRefreshSkills() {
  try {
    const d = await (await fetch(API + '/api/agent/skills')).json();
    window._agSkills = d.skills || [];
    const box = document.getElementById('agent-skill-chips');
    if (!box) return;
    box.innerHTML = window._agSkills.slice(0, 6).map(s =>
      `<span class="agent-skill-chip" title="${esc(s.description || '')}" onclick="agentRunSkill(${s.id})">⚡ ${esc(s.name)}</span>`).join('');
  } catch (e) {}
}

async function agentRunSkill(sid) {
  const s = (window._agSkills || []).find(x => x.id === sid);
  if (!s) return;
  let extra = '';
  if (/:\s*$/.test(s.prompt.trim())) {   // prompt ends with ":" → needs a topic
    extra = prompt(s.name + ' — topic/question:') || '';
    if (!extra) return;
  }
  try {
    const r = await fetch(API + `/api/agent/skills/${sid}/run`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_id: _agConv, extra })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'failed');
    _agConv = d.conversation_id;
    await agentLoadConv(_agConv);
    agentSetBusy(true); agentStartPolling(); agentRefreshConvs();
  } catch (e) { alert('Skill failed: ' + e.message); }
}

async function agentManageSkills() {
  const d = await (await fetch(API + '/api/agent/skills')).json();
  const list = (d.skills || []).map(s => `#${s.id} ${s.name}${s.builtin ? ' (built-in)' : ''}`).join('\n');
  const action = prompt('Skills:\n' + list + '\n\nType: new  |  edit <id>  |  del <id>', 'new');
  if (!action) return;
  const mDel = action.match(/^del\s+(\d+)/), mEdit = action.match(/^edit\s+(\d+)/);
  if (mDel) {
    if (confirm('Delete skill #' + mDel[1] + '?')) await fetch(API + '/api/agent/skills/' + mDel[1], { method: 'DELETE' });
  } else if (mEdit) {
    const s = (d.skills || []).find(x => x.id === parseInt(mEdit[1]));
    if (!s) return alert('No such skill');
    const name = prompt('Name:', s.name); if (!name) return;
    const desc = prompt('Description:', s.description || '') || '';
    const body = prompt('Prompt (end with ":" to be asked for a topic on run):', s.prompt); if (!body) return;
    await fetch(API + '/api/agent/skills', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: s.id, name, description: desc, prompt: body }) });
  } else {
    const name = prompt('New skill name:'); if (!name) return;
    const desc = prompt('Description:') || '';
    const body = prompt('Prompt (end with ":" to be asked for a topic on run):'); if (!body) return;
    await fetch(API + '/api/agent/skills', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, description: desc, prompt: body }) });
  }
  agentRefreshSkills();
}

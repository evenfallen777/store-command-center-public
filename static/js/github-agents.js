/* ══ DEV SWARM TAB — Agents & Models config sub-tab (split from tab-github.js) ══ */

/* ── Agents & Models config ───────────────────────────────────────────────── */
async function ghRenderAgents() {
  const [cfg, models, loaded] = await Promise.all([
    api('/api/github/swarm-config'),
    api('/api/github/llm-models').catch(() => ({ models: [] })),
    api('/api/github/loaded-model').catch(() => ({ loaded: null, checks: [] })),
  ]);
  const body = document.getElementById('gh-body');
  const allModels = models.models || [];
  const sys = cfg.system_agent || {};
  const modelOpts = (sel) => `<option value="">— pick a local model —</option>` +
    allModels.map(m => `<option value="${esc(m)}" ${m === sel ? 'selected' : ''}>${esc(m)}</option>`).join('');
  window._ghAllModels = allModels;
  window._ghCfgAgents = cfg.agents || [];

  body.innerHTML = `
    <div style="background:rgba(108,99,255,.08);border:1px solid var(--border);border-radius:8px;padding:10px 12px;font-size:.74rem;color:var(--muted);margin-bottom:14px;line-height:1.6;">
      &#8505;&#65039; <b>Only one model loads in VRAM at a time</b>, so agent turns run <b>sequentially</b>. The swarm requests its model through the <b>unified GPU queue</b> — the orchestrator loads it <b>on demand</b> when a turn runs (serialized with image/video), so you do <b>not</b> need a model pre-loaded.
      ${allModels.length ? allModels.length + ' local models detected.' : '<br>No local models detected — is LM Studio running on the GPU box?'}
    </div>

    <div class="settings-group">
      <div class="settings-group-title">&#128190; GPU model — pin (optional)</div>
      <div id="gh-loaded" style="font-size:.8rem;margin-bottom:10px;">
        ${loaded.loaded
          ? `<span style="color:#22c55e;">&#9679; resident: <b>${esc(loaded.loaded)}</b></span> <span style="color:var(--muted);">· context ${loaded.context || '?'} tokens</span>`
          : '<span style="color:var(--muted);">&#9679; nothing resident — fine: the swarm loads its model on demand via the queue. Pinning below is optional (keeps one warm to skip the first-turn load).</span>'}
      </div>
      ${(loaded.checks || []).map(c => `<div style="font-size:.72rem;color:${c.level === 'warn' ? 'var(--warn)' : 'var(--muted)'};margin-bottom:4px;">${c.level === 'warn' ? '&#9888;&#65039;' : '&#8505;&#65039;'} ${esc(c.msg)}</div>`).join('')}
      <div style="display:grid;grid-template-columns:2fr 1fr auto;gap:8px;align-items:end;margin-top:8px;">
        <div class="field" style="margin:0;"><label>Model to pin ${hlp('Force this job to use one specific local model instead of picking from the pool. Use it to keep behaviour consistent or to run your best coder.')}</label>
          <select id="gh-pin-model">${allModels.map(m => `<option value="${esc(m)}" ${m === loaded.loaded ? 'selected' : ''}>${esc(m)}</option>`).join('')}</select></div>
        <div class="field" style="margin:0;"><label>Context length ${hlp('The token context window for the pinned model. Bigger fits more code/history per turn but uses more VRAM and runs slower.')}</label>
          <input type="number" id="sw-context" value="${cfg.context || 16384}" min="2048" max="131072" step="2048"></div>
        <button class="btn-sm primary" onclick="ghLoadPin()" title="OPTIONAL: pre-load the chosen model through the unified queue to keep it warm, so the swarm's first turn doesn't pay the load cost. Not required — the swarm loads its model on demand either way.">Load &amp; pin</button>
      </div>
      <div style="font-size:.7rem;color:var(--muted);margin-top:6px;">Optional warm-up. The swarm always requests its model through the unified queue (loaded on demand); pinning just pre-loads one so the first turn is instant. Loads go through the orchestrator — never raw LM Studio.</div>
      <label style="font-size:.78rem;display:flex;gap:6px;align-items:center;cursor:pointer;margin-top:8px;">
        <input type="checkbox" id="sw-autopin" ${cfg.auto_pin !== false ? 'checked' : ''}>
        Auto-pin the job's coder model before each run (optional warm-up) ${hlp('Before each run, pre-load the job coder model (through the unified queue) so the first turn is instant. Off is fine too — the swarm still loads its model on demand via the queue. Purely a warm-up convenience now.')}</label>
      <label style="font-size:.78rem;display:flex;gap:6px;align-items:center;cursor:pointer;margin-top:6px;">
        <input type="checkbox" id="sw-restart" ${cfg.restart_after_promote ? 'checked' : ''}>
        Auto-restart the live app after a promote (so approved code goes live immediately) ${hlp('After a successful promote, restart this Store app automatically so the new master code runs at once - instead of you clicking Restart live app in Workflow. Causes a few seconds of downtime.')}</label>
    </div>

    <div class="settings-group">
      <div class="settings-group-title">Autonomy (global default — overridable per job)</div>
      <div style="display:flex;flex-direction:column;gap:6px;font-size:.82rem;">
        ${[['gate', 'Gate the big moments — ask before fuzzy work, stop before test + push'],
           ['auto', 'Autonomous until tests pass, then one review gate before push'],
           ['step', 'Step-by-step — confirm each stage']].map(([v, label]) => `
          <label style="display:flex;gap:8px;align-items:flex-start;cursor:pointer;">
            <input type="radio" name="autonomy" value="${v}" ${cfg.autonomy === v ? 'checked' : ''}>
            <span>${label}</span></label>`).join('')}
      </div>
    </div>

    <div class="settings-group" style="margin-top:14px;">
      <div class="settings-group-title">Swarm size</div>
      <div style="display:flex;gap:16px;font-size:.82rem;margin-bottom:10px;flex-wrap:wrap;">
        <label style="display:flex;gap:6px;align-items:center;cursor:pointer;">
          <input type="radio" name="sw-mode" value="dynamic" ${cfg.mode !== 'static' ? 'checked' : ''} onchange="ghModeChanged()"> Dynamic (spin up N agents) ${hlp('The swarm spins up N coder agents per job (the count below) and assigns models from your pool automatically. Simplest - good default.')}</label>
        <label style="display:flex;gap:6px;align-items:center;cursor:pointer;">
          <input type="radio" name="sw-mode" value="static" ${cfg.mode === 'static' ? 'checked' : ''} onchange="ghModeChanged()"> Static (fixed named roster) ${hlp('You define a fixed roster of named agents (planner/coder/reviewer) and hand-pick each one model and instructions in the roster below. More control, less automatic.')}</label>
      </div>
      <div id="sw-dynamic" style="display:${cfg.mode === 'static' ? 'none' : 'block'};">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
          <div class="field"><label>Default # of agents (per job/task overridable) ${hlp('The swarm’s default parallel coder count for new jobs. Any individual job can override it in its own “# Agents” field.')}</label>
            <input type="number" id="sw-count" value="${cfg.agent_count || 3}" min="1" max="12"></div>
          <div class="field"><label>Reviewer/auditor voters (majority approves; author excluded) ${hlp('How many reviewer/auditor agents vote before a change is accepted. Majority wins and the agent that wrote the code can’t vote on it. More voters = stricter review, slower.')}</label>
            <input type="number" id="sw-voters" value="${cfg.voters || 2}" min="1" max="9"></div>
        </div>
        <div class="field"><label>Model pool the swarm draws from (your coding models etc.) ${hlp('The set of local models the swarm can assign to its roles (planner / coder / reviewer). Point it at your best coding models; pinning a model on a job overrides this.')}</label>
          <div style="display:flex;flex-wrap:wrap;gap:8px;max-height:160px;overflow:auto;padding:6px;border:1px solid var(--border);border-radius:8px;">
            ${allModels.length ? allModels.map(m => `
              <label style="font-size:.74rem;display:flex;gap:5px;align-items:center;cursor:pointer;background:var(--bg2);padding:3px 8px;border-radius:6px;">
                <input type="checkbox" class="sw-pool" value="${esc(m)}" ${(cfg.models || []).includes(m) ? 'checked' : ''}> ${esc(m)}</label>`).join('')
              : '<span style="color:var(--muted);font-size:.74rem;">no models detected</span>'}
          </div></div>
      </div>
    </div>

    <div class="settings-group" style="margin-top:14px;">
      <div class="settings-group-title">Review rules</div>
      <div style="display:flex;flex-direction:column;gap:8px;font-size:.8rem;">
        <label style="display:flex;gap:8px;align-items:flex-start;cursor:pointer;">
          <input type="checkbox" id="sw-noself" ${cfg.no_self_approval !== false ? 'checked' : ''}>
          <span>A model may <b>not</b> review or approve its <b>own</b> code — another model must review it (the author still gets a vote).</span></label>
        <label style="display:flex;gap:8px;align-items:flex-start;cursor:pointer;">
          <input type="checkbox" id="sw-solo" ${cfg.self_review_when_solo !== false ? 'checked' : ''}>
          <span>If a job runs with <b>only 1 agent</b>, that model reviews + tests its own code (no peer available).</span></label>
        <div style="font-size:.74rem;color:var(--muted);padding-left:26px;">Either way, <b>only you</b> give the final approve/reject — a reject requires a comment describing the problem.</div>
      </div>
    </div>

    <div class="settings-group" style="margin-top:14px;">
      <div class="settings-group-title">&#128295; System agent</div>
      <div style="font-size:.74rem;color:var(--muted);margin-bottom:8px;">Installs/configures tools the swarm needs on the system, verifies them, then signals the swarm to resume/test. Risky commands are proposed for your approval.</div>
      <label style="font-size:.78rem;display:flex;gap:6px;align-items:center;cursor:pointer;margin-bottom:8px;">
        <input type="checkbox" id="sys-en" ${sys.enabled !== false ? 'checked' : ''}> enabled ${hlp('Let the swarm request tool/dependency installs on this machine when a job needs them. Every command still waits for your approval before it runs. Off = the swarm cannot touch the system.')}</label>
      <div class="field"><label>Model ${hlp('Which local model plays the system agent - the one that figures out and proposes the install/fix commands.')}</label><select id="sys-model">${modelOpts(sys.model)}</select></div>
      <div class="field"><label>Task ${hlp('The system agent standing instructions (its system prompt) - what it may set up and how careful to be. Sent to the model on every system request.')}</label><textarea id="sys-task" rows="2">${esc(sys.task || '')}</textarea></div>
    </div>

    <div class="settings-group" style="margin-top:14px;display:${cfg.mode === 'static' ? 'block' : 'none'};" id="sw-roster">
      <div class="settings-group-title">Named roster (static mode)</div>
      <div id="sw-agents">
        ${(cfg.agents || []).map((a, i) => `
          <div class="card" style="padding:12px;margin-bottom:10px;">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
              <span style="font-weight:600;text-transform:capitalize;">${esc(a.role)}</span>
              <label style="font-size:.74rem;display:flex;gap:6px;align-items:center;cursor:pointer;">
                <input type="checkbox" class="sw-en" data-i="${i}" ${a.enabled ? 'checked' : ''}> enabled</label>
            </div>
            <div class="field" style="margin-top:8px;"><label>Model ${hlp('The local model that plays this named role (planner / coder / reviewer). Give your strongest coding model to the coder role.')}</label>
              <select class="sw-model" data-i="${i}">${modelOpts(a.model)}</select></div>
            <div class="field"><label>Task ${hlp('This agent standing instructions (its system prompt) for the role - how it should plan, code, or review. Sent to its model on every turn.')}</label>
              <textarea class="sw-task" data-i="${i}" rows="2">${esc(a.task || '')}</textarea></div>
          </div>`).join('')}
      </div>
    </div>

    <div style="margin-top:14px;"><button class="btn-sm primary" onclick="ghSaveAgents()">&#128190; Save swarm config</button></div>`;
}

function ghModeChanged() {
  const mode = document.querySelector('input[name="sw-mode"]:checked')?.value;
  document.getElementById('sw-dynamic').style.display = mode === 'static' ? 'none' : 'block';
  document.getElementById('sw-roster').style.display = mode === 'static' ? 'block' : 'none';
}
window.ghModeChanged = ghModeChanged;

async function ghSaveAgents() {
  const agents = (window._ghCfgAgents || []).map((a, i) => ({
    role: a.role,
    enabled: document.querySelector(`.sw-en[data-i="${i}"]`)?.checked ?? a.enabled,
    model: document.querySelector(`.sw-model[data-i="${i}"]`)?.value ?? a.model,
    task: document.querySelector(`.sw-task[data-i="${i}"]`)?.value ?? a.task,
  }));
  const payload = {
    autonomy: document.querySelector('input[name="autonomy"]:checked')?.value || 'gate',
    mode: document.querySelector('input[name="sw-mode"]:checked')?.value || 'dynamic',
    agent_count: parseInt(document.getElementById('sw-count').value) || 3,
    context: parseInt(document.getElementById('sw-context').value) || 16384,
    auto_pin: document.getElementById('sw-autopin')?.checked ?? true,
    restart_after_promote: document.getElementById('sw-restart')?.checked ?? false,
    voters: parseInt(document.getElementById('sw-voters').value) || 2,
    models: [...document.querySelectorAll('.sw-pool:checked')].map(c => c.value),
    no_self_approval: document.getElementById('sw-noself').checked,
    self_review_when_solo: document.getElementById('sw-solo').checked,
    system_agent: {
      enabled: document.getElementById('sys-en').checked,
      model: document.getElementById('sys-model').value,
      task: document.getElementById('sys-task').value,
    },
    agents,
  };
  try {
    await api('/api/github/swarm-config', { method: 'POST', body: JSON.stringify(payload) });
    toast('✅ Swarm config saved');
  } catch (e) { toast('Save failed: ' + e.message, 'error'); }
}
window.ghSaveAgents = ghSaveAgents;

async function ghLoadPin() {
  const model = document.getElementById('gh-pin-model').value;
  const context = parseInt(document.getElementById('sw-context').value) || 16384;
  if (!model) { toast('Pick a model', 'error'); return; }
  const el = document.getElementById('gh-loaded');
  el.innerHTML = `<span style="color:var(--muted);">⏳ Loading ${esc(model)} @ ${context} ctx (unloading others to fit)…</span>`;
  try {
    const r = await api('/api/github/load-model', { method: 'POST', body: JSON.stringify({ model, context }) });
    toast('✅ Pinned ' + r.loaded + (r.note ? ' — ' + r.note : ''));
    ghRenderAgents();
  } catch (e) { toast('Load failed: ' + e.message, 'error'); el.innerHTML = `<span style="color:var(--warn);">${esc(e.message)}</span>`; }
}
window.ghLoadPin = ghLoadPin;

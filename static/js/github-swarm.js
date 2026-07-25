/* ══ DEV SWARM TAB — swarm jobs: create / open / run / review / promote (split from tab-github.js) ══ */

/* ── Dev Swarm: jobs ──────────────────────────────────────────────────────── */
async function ghRenderSwarm(preRepo) {
  _ghJobs = await api('/api/github/jobs');
  if (!_ghProjects.length) {
    try { _ghProjects = (await api('/api/github/projects')).projects || []; } catch { }
  }
  const _projSel = _ghProjects.length ? _ghProjects.map(p => {
    const sel = (_ghProject != null ? p.id === _ghProject : p.is_primary) ? 'selected' : '';
    return `<option value="${p.id}" ${sel}>${esc(p.name)} (work: ${esc(p.work_branch || 'dev')})</option>`;
  }).join('') : '<option value="">(default: store)</option>';
  const ownRepo = '';   // filled in async below — never block the paint on `gh` subprocesses
  const body = document.getElementById('gh-body');
  body.innerHTML = `
    <details class="settings-group" style="margin-bottom:16px;" id="gh-job-form" ${preRepo ? 'open' : ''}>
      <summary style="cursor:pointer;font-weight:600;font-size:.9rem;">&#10133; Propose a job / fix / project</summary>
      <div style="font-size:.72rem;color:var(--muted);margin:8px 0 12px;">Describe what you want. The swarm plans it,
        asks questions if it's fuzzy or a big change, then (Phase 2) codes → reviews → votes → tests on the working branch.</div>
      <div class="field"><label>Title *</label><input type="text" id="job-title" placeholder="e.g. Add CSV export to the resell tab"></div>
      <div class="field"><label>Details / spec</label><textarea id="job-spec" rows="4" placeholder="What should change and why. Constraints, files, acceptance criteria."></textarea></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <div class="field"><label>Project ${hlp('Which registered dev project this job belongs to. The job edits that project\'s working branch (its Work-on toggle); human approval is still required before anything lands on its live branch.')}</label>
          <select id="job-project">${_projSel}</select></div>
        <div class="field"><label>Repo (owner/name) ${hlp('Which GitHub repo the swarm works in. Defaults to this app so it can improve itself; the swarm codes on the working branch here, never straight to the live branch.')}</label><input type="text" id="job-repo" value="${esc(preRepo || ownRepo)}" placeholder="owner/name (defaults to this install's origin)"></div>
        <div class="field"><label>Autonomy for this job ${hlp('How far the swarm runs on its own. gate = stop for your input/review at key points; auto = run straight through to the final review; step = pause after every stage. Less autonomy = more control, more clicks.')}</label>
          <select id="job-autonomy">
            <option value="">Use global default</option>
            <option value="gate">Gate the big moments</option>
            <option value="auto">Autonomous until tests pass</option>
            <option value="step">Step-by-step confirm</option>
          </select></div>
        <div class="field"><label>Direction / scope ${hlp('How wide the change may reach — the whole repo, one folder, or specific files. Narrower scope is safer: the coder agents may only edit the paths you list below.')}</label>
          <select id="job-scope" onchange="ghScopeChanged()">
            <option value="project">Whole project</option>
            <option value="folder">Specific folder</option>
            <option value="file">Specific file(s)</option>
          </select></div>
        <div class="field"><label># Agents (blank = default) ${hlp('How many coder agents work this job in parallel. Blank uses the global default (set below). More = faster on big jobs but more GPU turns and VRAM churn.')}</label>
          <input type="number" id="job-agents" min="1" max="12" placeholder="e.g. 3"></div>
        <div class="field" id="job-paths-wrap" style="grid-column:1/3;display:none;"><label>Path(s) — comma-separated ${hlp('When scope is file/folder, the exact paths the swarm is allowed to touch. Everything else is off-limits — this is the hard safety boundary for the job.')}</label>
          <input type="text" id="job-paths" placeholder="app/routers/resell.py, static/js/tab-resell.js"></div>
      </div>
      <button class="btn-sm primary" onclick="ghCreateJob()">Create job</button>
    </details>
    <div id="gh-jobs-list">${ghJobsListHtml(_ghJobs)}</div>`;
  // Default repo = the install's own origin — filled AFTER paint, and only when the
  // origin actually belongs to the signed-in user (never prefill the vendor repo).
  if (!preRepo) api('/api/github/status').then(st => {
    if (!st.owned) return;
    const m = (st.origin || '').match(/github\.com[:/]([^/]+)\/([^/.]+)/);
    const inp = document.getElementById('job-repo');
    if (m && inp && !inp.value) inp.value = m[1] + '/' + m[2];
  }).catch(() => {});
}
function ghJobsListHtml(jobs) {
  if (!jobs.length) return '<div class="empty"><div class="empty-icon">&#129302;</div>No jobs yet — propose one above.</div>';
  return `<div style="display:flex;flex-direction:column;gap:10px;">` + jobs.map(j => `
    <div class="card" style="padding:12px;cursor:pointer;" onclick="ghOpenJob(${j.id})">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
        <span style="font-weight:600;font-size:.86rem;">${esc(j.title)}</span>
        <span style="font-size:.64rem;padding:2px 8px;border-radius:20px;background:var(--bg2);color:var(--muted);">${esc(j.status)}</span>
      </div>
      <div style="color:var(--muted);font-size:.72rem;margin-top:5px;">
        ${esc(j.repo || '')} · ${esc(j.branch || 'dev')}
        ${j.open_questions ? `· <span style="color:var(--warn);">${j.open_questions} question(s) awaiting you</span>` : ''}
      </div>
    </div>`).join('') + `</div>`;
}

function ghScopeChanged() {
  const scope = document.getElementById('job-scope').value;
  const wrap = document.getElementById('job-paths-wrap');
  if (wrap) wrap.style.display = scope === 'project' ? 'none' : 'block';
}
window.ghScopeChanged = ghScopeChanged;

async function ghCreateJob() {
  const title = document.getElementById('job-title').value.trim();
  if (!title) { toast('Title required', 'error'); return; }
  const scope = document.getElementById('job-scope').value;
  const pathsRaw = document.getElementById('job-paths').value.trim();
  const agents = parseInt(document.getElementById('job-agents').value);
  const projVal = parseInt(document.getElementById('job-project')?.value);
  const payload = {
    title, spec: document.getElementById('job-spec').value.trim(),
    repo: document.getElementById('job-repo').value.trim(),
    autonomy: document.getElementById('job-autonomy').value || null,
    scope,
    paths: scope !== 'project' && pathsRaw ? pathsRaw.split(',').map(s => s.trim()).filter(Boolean) : [],
    agent_count: Number.isInteger(agents) ? agents : null,
    project_id: Number.isInteger(projVal) ? projVal : null,
  };
  try { await api('/api/github/jobs', { method: 'POST', body: JSON.stringify(payload) });
    toast('✅ Job proposed'); await ghRenderSwarm();
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}
window.ghCreateJob = ghCreateJob;
function ghProposeForRepo(full) { _ghSection = 'swarm'; renderGhPills();
  document.getElementById('gh-body').innerHTML = '<div class="loading-state">…</div>'; ghRenderSwarm(full); }
window.ghProposeForRepo = ghProposeForRepo;

async function ghOpenJob(jid) {
  if (_ghJobTimer) { clearTimeout(_ghJobTimer); _ghJobTimer = null; }
  const j = await api('/api/github/jobs/' + jid);
  const systasks = await api('/api/github/jobs/' + jid + '/system-tasks').catch(() => []);
  const body = document.getElementById('gh-body');
  const openQs = (j.questions || []).filter(q => q.status === 'open');
  body.innerHTML = `
    <button class="btn-sm" onclick="switchGhSection('swarm')">&#8592; All jobs</button>
    <div class="card" style="padding:16px;margin-top:12px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
        <div style="font-weight:700;font-size:1rem;">${esc(j.title)}</div>
        <span style="font-size:.66rem;padding:2px 9px;border-radius:20px;background:var(--bg2);color:var(--muted);">${esc(j.status)}</span>
      </div>
      <div style="color:var(--muted);font-size:.78rem;margin-top:8px;white-space:pre-wrap;">${esc(j.spec || '')}</div>
      ${j.enhanced_spec ? `<details style="margin-top:8px;"><summary style="cursor:pointer;font-size:.74rem;color:var(--accent2);">&#10024; Architect's enhanced spec</summary>
        <div style="font-size:.76rem;color:var(--muted);white-space:pre-wrap;margin-top:6px;padding:8px;background:var(--bg2);border-radius:8px;">${esc(j.enhanced_spec)}</div></details>` : ''}
      ${(j.children && j.children.length) ? `<div style="margin-top:10px;">
        <div style="font-size:.76rem;font-weight:600;margin-bottom:6px;">&#129513; Subtasks (${j.children.length})</div>
        ${j.children.map(c => `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:.74rem;padding:5px 8px;background:var(--bg2);border-radius:6px;margin-bottom:5px;">
          <span style="cursor:pointer;" onclick="ghOpenJob(${c.id})">${esc(c.title)} <span style="color:var(--muted);">${esc(c.scope || '')}</span></span>
          <span style="display:flex;gap:6px;align-items:center;"><span style="font-size:.62rem;color:var(--muted);">${esc(c.status)}</span>
            ${c.status === 'proposed' ? `<button class="btn-sm" style="padding:2px 8px;" onclick="event.stopPropagation();ghRunJob(${c.id})">&#9654;</button>` : ''}</span>
        </div>`).join('')}
        ${j.children.some(c => c.status === 'proposed') ? `<button class="btn-sm primary" style="margin-top:4px;" onclick="ghRunAllSubtasks(${j.id})">&#9654; Run all subtasks</button>` : ''}
      </div>` : ''}
      <div style="color:var(--muted);font-size:.72rem;margin-top:8px;">
        ${esc(j.repo || '')} · ${esc(j.branch || 'dev')} · autonomy: ${esc(j.autonomy || 'global default')}
        · scope: ${esc(j.scope || 'project')}${ghPaths(j.paths)}
        · agents: ${j.agent_count || 'default'}
      </div>
      ${j.decision ? `<div style="margin-top:8px;font-size:.78rem;color:${j.decision === 'approved' ? '#22c55e' : '#f87171'};">
        ${j.decision === 'approved' ? '✓ Approved by you' : '✗ Rejected: ' + esc(j.decision_comment || '')}
        ${j.decision === 'approved' && j.status !== 'done' ? `<button class="btn-sm primary" style="margin-left:10px;" onclick="ghPromoteJob(${j.id})" title="Ship this approved work: merge dev into master and push, then resync retail from master, scrub it of real IPs/domains/identity/private docs, and push retail as a clean public branch. The live app keeps old code until you Restart it in Workflow.">&#128640; Promote dev &rarr; master &rarr; retail</button>` : ''}
        ${j.status === 'done' ? ' · <span style="color:var(--muted);">promoted ✓ (restart to run new code)</span>' : ''}</div>` : ''}
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
        <button class="btn-sm primary" onclick="ghRunJob(${j.id})" title="Start (or advance) the local-model swarm on this job now: the planner/coder/reviewer agents plan, code on the dev branch, and vote. It may pause to ask you questions or wait for approval depending on the job's autonomy.">&#9654; Run swarm</button>
        <label style="font-size:.74rem;display:flex;align-items:center;gap:6px;color:var(--muted);">
          <input type="checkbox" ${j.cron_enabled ? 'checked' : ''} onchange="ghToggleCron(${j.id}, this.checked)">
          keep working on a schedule (cron) ${hlp('Let the scheduler keep advancing this job automatically on its interval, instead of you pressing Run swarm each time. Same setting as the Cron tab.')}</label>
        <button class="btn-sm" onclick="ghDeleteJob(${j.id})" title="Delete this job and its timeline from the queue. Does not touch any code already committed on the dev branch.">&#128465;&#65039; Delete</button>
      </div>
      <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border);">
        <div style="font-size:.72rem;color:var(--muted);margin-bottom:6px;">Only you can approve or reject — models review each other's code, never their own.</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <button class="btn-sm" style="background:rgba(34,197,94,.15);color:#22c55e;" onclick="ghApproveJob(${j.id})" title="You are the final approver. Marks the swarm's work accepted and unlocks the Promote button - it does not push code by itself.">&#10003; Approve &rarr; promote</button>
          <input type="text" id="reject-comment" placeholder="What's wrong? (required to reject)" style="flex:1;min-width:180px;">
          <button class="btn-sm" style="background:rgba(239,68,68,.12);color:#f87171;" onclick="ghRejectJob(${j.id})" title="Send the work back to the swarm with your comment (the box to the left is required). The agents use it to revise on the dev branch; nothing is promoted.">&#10007; Reject</button>
          <button class="btn-sm" onclick="ghPeerReview(${j.id})" title="Send this job's dev-branch diff to a connected friend's Store: their local LLM reviews it and your friend can vote too. The verdict comes back as an ADVISORY vote on this job — you still make the final call.">&#129309; Peer review</button>
        </div>
        <div id="peer-votes-${j.id}" style="margin-top:6px;"></div>
      </div>
    </div>
    ${openQs.length ? `<div class="card" style="padding:14px;margin-top:12px;border-color:var(--warn);">
      <div style="font-weight:600;font-size:.84rem;color:var(--warn);margin-bottom:8px;">&#10067; The swarm needs your input</div>
      ${openQs.map(q => `<div style="margin-bottom:10px;">
        <div style="font-size:.8rem;">${esc(q.question)}</div>
        <div style="display:flex;gap:6px;margin-top:5px;">
          <input type="text" id="q-${q.id}" placeholder="Your answer…" style="flex:1;">
          <button class="btn-sm primary" onclick="ghAnswer(${q.id}, ${j.id})">Send</button>
        </div></div>`).join('')}
    </div>` : ''}
    ${ghQaPanelHtml(j)}
    <div class="card" style="padding:14px;margin-top:12px;">
      <div style="font-weight:600;font-size:.84rem;margin-bottom:8px;">&#128295; System agent</div>
      <div style="font-size:.72rem;color:var(--muted);margin-bottom:8px;">Installs/configures tools the swarm needs. Every command needs your approval before it runs.</div>
      ${(systasks || []).map(t => ghSysTaskHtml(t, jid)).join('') || '<div style="font-size:.74rem;color:var(--muted);margin-bottom:8px;">No system tasks.</div>'}
      <div style="display:flex;gap:6px;margin-top:8px;">
        <input type="text" id="ask-sys" placeholder="Ask the system agent to install/fix something…" style="flex:1;">
        <button class="btn-sm" onclick="ghAskSystem(${jid})" title="Ask the system agent to prepare a tool/dependency the swarm needs (e.g. install a package). It proposes the exact shell command, which you must approve before it runs on your machine.">Ask</button>
      </div>
    </div>
    <div class="card" style="padding:14px;margin-top:12px;">
      <div style="font-weight:600;font-size:.84rem;margin-bottom:8px;">Timeline</div>
      <div style="font-size:.76rem;line-height:1.7;max-height:400px;overflow:auto;">
        ${(j.events || []).map(e => `<div style="padding:4px 0;border-bottom:1px solid var(--border);">
          <span style="color:var(--accent2);font-weight:600;">${esc(e.agent || 'system')}</span>
          <span style="color:var(--muted);font-size:.68rem;"> ${esc(e.kind || '')}${e.vote ? ' · ' + esc(e.vote) : ''}${e.model ? ' · ' + esc(e.model) : ''}</span>
          <div style="white-space:pre-wrap;">${esc(e.content || '')}</div>
        </div>`).join('') || '<span style="color:var(--muted);">No activity yet.</span>'}
      </div>
    </div>`;
  ghLoadPeerVotes(jid);   // async — fills the peer-votes strip after paint
  // Live-refresh while the swarm is actively working this job (never while you're typing).
  if (GH_WORKING.includes(j.status) && _ghSection === 'swarm') {
    _ghJobTimer = setTimeout(() => ghOpenJob(jid), 4000);
  }
}
window.ghOpenJob = ghOpenJob;

/* ── Peer review: send this job's diff to a friend's Store for an advisory vote ── */
async function ghPeerReview(jid) {
  let peers = [];
  try { peers = ((await api('/api/peers')).peers || []).filter(p => p.status === 'approved'); }
  catch (e) { toast('Could not load peers: ' + e.message, 'error'); return; }
  if (!peers.length) { toast('No approved peers yet — pair with a friend in Settings → Integrations → Peers.', 'error'); return; }
  let peer = peers[0];
  if (peers.length > 1) {
    const pick = prompt('Send to which peer?\n' + peers.map((p, i) => `${i + 1}. ${p.name}`).join('\n'), '1');
    peer = peers[(parseInt(pick, 10) || 1) - 1] || peers[0];
  }
  try {
    await api(`/api/peers/${peer.id}/review-job`, { method: 'POST', body: JSON.stringify({ job_id: jid }) });
    toast(`Diff sent to ${peer.name} — their node is reviewing. Refresh the votes strip for the verdict.`);
    ghLoadPeerVotes(jid);
  } catch (e) { toast('Peer review failed: ' + e.message, 'error'); }
}
async function ghLoadPeerVotes(jid) {
  const el = document.getElementById(`peer-votes-${jid}`);
  if (!el) return;
  let reqs = [];
  try { reqs = (await api('/api/peers/review-requests?job_id=' + jid)).requests || []; }
  catch { return; }
  if (!reqs.length) { el.innerHTML = ''; return; }
  el.innerHTML = `<div style="font-size:.72rem;color:var(--muted);">
    ${reqs.map(r => {
      const vote = r.human_vote || r.llm_vote;
      const who = esc(r.peer_name) + (r.human_vote ? '' : r.llm_vote ? ' (their LLM)' : '');
      return `<span style="margin-right:10px;">&#129309; ${who}: ${r.status === 'done'
        ? `<b style="color:${vote === 'approve' ? '#22c55e' : '#f87171'}">${esc(vote || '?')}</b>`
        : `${esc(r.status)}&hellip; <a href="#" onclick="ghRefreshPeerVote(${r.id},${jid});return false;">check</a>`}</span>`;
    }).join('')}</div>`;
}
async function ghRefreshPeerVote(rid, jid) {
  try {
    await api(`/api/peers/review-requests/${rid}/refresh`, { method: 'POST' });
    ghLoadPeerVotes(jid);
  } catch (e) { toast('Refresh failed: ' + e.message, 'error'); }
}
window.ghPeerReview = ghPeerReview; window.ghLoadPeerVotes = ghLoadPeerVotes;
window.ghRefreshPeerVote = ghRefreshPeerVote;

async function ghRunJob(jid) {
  try { const r = await api('/api/github/jobs/' + jid + '/run', { method: 'POST' });
    toast(r.message || 'Run requested'); ghOpenJob(jid);
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}
async function ghToggleCron(jid, on) {
  try { await api('/api/github/jobs/' + jid, { method: 'PATCH', body: JSON.stringify({ cron_enabled: on ? 1 : 0 }) });
    toast(on ? 'Cron enabled for this job' : 'Cron disabled');
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}
async function ghAnswer(qid, jid) {
  const v = document.getElementById('q-' + qid).value.trim();
  if (!v) return;
  try { await api('/api/github/questions/' + qid + '/answer', { method: 'POST', body: JSON.stringify({ answer: v }) });
    toast('Answer sent'); ghOpenJob(jid);
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}
async function ghDeleteJob(jid) {
  if (!confirm('Delete this job?')) return;
  try { await api('/api/github/jobs/' + jid, { method: 'DELETE' }); toast('Deleted'); switchGhSection('swarm'); }
  catch (e) { toast('Failed: ' + e.message, 'error'); }
}
function ghPaths(paths) {
  try { const p = typeof paths === 'string' ? JSON.parse(paths || '[]') : (paths || []);
    return p.length ? ' (' + p.map(esc).join(', ') + ')' : ''; } catch { return ''; }
}
async function ghApproveJob(jid) {
  if (!confirm('Approve this job? You are the final approver.')) return;
  try { await api('/api/github/jobs/' + jid + '/approve', { method: 'POST' });
    toast('✅ Approved — ready to promote'); ghOpenJob(jid);
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}
async function ghRejectJob(jid) {
  const c = document.getElementById('reject-comment').value.trim();
  if (!c) { toast('Add a comment explaining the problem to reject', 'error'); return; }
  try { await api('/api/github/jobs/' + jid + '/reject', { method: 'POST', body: JSON.stringify({ comment: c }) });
    toast('Rejected with feedback'); ghOpenJob(jid);
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}
function ghSysTaskHtml(t, jid) {
  const color = { requested: 'var(--warn)', approved: 'var(--muted)', installing: 'var(--muted)',
                  done: '#22c55e', verified: '#22c55e', failed: '#f87171' }[t.status] || 'var(--muted)';
  let reason = '';
  try { reason = JSON.parse(t.report || '{}').reason || ''; } catch { reason = ''; }
  const showReport = ['done', 'verified', 'failed'].includes(t.status);
  return `<div class="card" style="padding:10px;margin-bottom:8px;">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
      <code style="font-size:.74rem;word-break:break-all;">${esc(t.command)}</code>
      <span style="font-size:.62rem;color:${color};white-space:nowrap;">${esc(t.status)}</span>
    </div>
    ${reason && !showReport ? `<div style="font-size:.7rem;color:var(--muted);margin-top:4px;">${esc(reason)}</div>` : ''}
    ${showReport ? `<pre style="font-size:.68rem;background:var(--bg2);padding:6px;border-radius:6px;margin-top:6px;max-height:160px;overflow:auto;white-space:pre-wrap;">${esc(t.report || '')}</pre>` : ''}
    ${t.status === 'requested' ? `<div style="display:flex;gap:6px;margin-top:6px;">
      <button class="btn-sm" style="background:rgba(34,197,94,.15);color:#22c55e;" onclick="ghApproveSys(${t.id}, ${jid})">&#10003; Approve &amp; run</button>
      <button class="btn-sm" style="background:rgba(239,68,68,.12);color:#f87171;" onclick="ghRejectSys(${t.id}, ${jid})">&#10007; Reject</button>
    </div>` : ''}
  </div>`;
}
async function ghApproveSys(tid, jid) {
  if (!confirm('Approve and run this command on your machine?')) return;
  try { const r = await api('/api/github/system-tasks/' + tid + '/approve', { method: 'POST' });
    toast(r.message || 'Running…'); setTimeout(() => ghOpenJob(jid), 1500);
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}
async function ghRejectSys(tid, jid) {
  try { await api('/api/github/system-tasks/' + tid + '/reject', { method: 'POST' }); toast('Rejected'); ghOpenJob(jid); }
  catch (e) { toast('Failed: ' + e.message, 'error'); }
}
async function ghAskSystem(jid) {
  const need = document.getElementById('ask-sys').value.trim();
  if (!need) return;
  try { const r = await api('/api/github/jobs/' + jid + '/ask-system', { method: 'POST', body: JSON.stringify({ need }) });
    toast(r.message || 'Asking…'); setTimeout(() => ghOpenJob(jid), 2500);
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}
window.ghApproveSys = ghApproveSys; window.ghRejectSys = ghRejectSys; window.ghAskSystem = ghAskSystem;

async function ghPromoteJob(jid) {
  if (!confirm('Promote this job dev → master → retail?\n\nThis merges to master, pushes both branches, and resyncs retail. The running app keeps old code until you restart.')) return;
  toast('Promoting… (this runs git across all three worktrees)');
  try { const r = await api('/api/github/jobs/' + jid + '/promote', { method: 'POST' });
    const failed = (r.steps || []).filter(s => !s.ok);
    toast(failed.length ? ('Promoted with issues: ' + failed.map(s => s.step).join(', ')) : '✅ Promoted — restart to run new code', failed.length ? 'error' : 'success');
    ghOpenJob(jid);
  } catch (e) { toast('Promote failed: ' + e.message, 'error'); }
}
async function ghRunAllSubtasks(jid) {
  try {
    const j = await api('/api/github/jobs/' + jid);
    const todo = (j.children || []).filter(c => c.status === 'proposed');
    for (const c of todo) { await api('/api/github/jobs/' + c.id + '/run', { method: 'POST' }); }
    toast(`Started ${todo.length} subtask(s)`); ghOpenJob(jid);
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}
/* ── Q&A / Direct — the two-way conversation around a job ─────────────────────
   Interleaves the conversation kinds from the timeline: the crew's blocking
   questions + your answers, your Ask questions (user_q) + the crew's replies
   (agent_a), and your directives. Ask = pure Q&A, no course change; Direct =
   the crew incorporates it at its NEXT stage boundary (not mid-turn). */
const _GH_QA_STYLE = {
  question: { i: '&#10067;', who: 'crew asks' },   answer: { i: '&#128100;', who: 'you answered' },
  user_q: { i: '&#128100;', who: 'you asked' },    agent_a: { i: '&#129302;', who: 'crew replied' },
  directive: { i: '&#129517;', who: 'you directed' },
};
function ghQaPanelHtml(j) {
  const convo = (j.events || []).filter(e => _GH_QA_STYLE[e.kind]);
  return `<div class="card" style="padding:14px;margin-top:12px;">
    <div style="font-weight:600;font-size:.84rem;margin-bottom:6px;">&#128172; Q&amp;A / Direct</div>
    <div style="font-size:.7rem;color:var(--muted);margin-bottom:8px;">
      <b>Ask</b> = the crew answers a question about this job (informational — no course change).
      <b>Direct</b> = inject an owner directive; the crew incorporates it at its <i>next stage</i>
      (architect/coder/reviewer turn), not mid-turn. The crew's own blocking questions appear
      above with answer boxes and pause the job until answered.</div>
    <div style="font-size:.76rem;line-height:1.6;max-height:260px;overflow:auto;margin-bottom:8px;">
      ${convo.map(e => `<div style="padding:3px 0;border-bottom:1px solid var(--border);">
        <span>${_GH_QA_STYLE[e.kind].i}</span>
        <span style="color:var(--accent2);font-weight:600;font-size:.68rem;">${_GH_QA_STYLE[e.kind].who}${e.agent && e.kind === 'question' ? ' (' + esc(e.agent) + ')' : ''}</span>
        <div style="white-space:pre-wrap;">${esc(e.content || '')}</div>
      </div>`).join('') || '<span style="color:var(--muted);">No conversation yet — ask or direct below.</span>'}
    </div>
    <div style="display:flex;gap:6px;margin-bottom:6px;">
      <input type="text" id="job-ask" placeholder="Ask the crew about this job (no course change)…" style="flex:1;"
             onkeydown="if(event.key==='Enter')ghAskJob(${j.id})">
      <button class="btn-sm" onclick="ghAskJob(${j.id})" title="One local-LLM turn answering from the job's context. Informational only.">&#10067; Ask</button>
    </div>
    <div style="display:flex;gap:6px;">
      <input type="text" id="job-direct" placeholder="Direct the crew (course correction, picked up at the next stage)…" style="flex:1;"
             onkeydown="if(event.key==='Enter')ghDirectJob(${j.id})">
      <button class="btn-sm primary" onclick="ghDirectJob(${j.id})" title="Injected into the next agent turn as 'OWNER DIRECTIVE (incorporate this)'. Also resumes a paused/awaiting job.">&#129517; Direct</button>
    </div>
    <div id="job-qa-status" style="font-size:.72rem;margin-top:6px;color:var(--muted);"></div>
  </div>`;
}
async function ghAskJob(jid) {
  const el = document.getElementById('job-ask');
  const q = (el?.value || '').trim();
  if (!q) return;
  const st = document.getElementById('job-qa-status');
  if (st) st.textContent = '🤖 The crew is thinking… (one local-model turn)';
  try {
    await api('/api/github/jobs/' + jid + '/ask', { method: 'POST', body: JSON.stringify({ question: q }) });
    ghOpenJob(jid);   // reply is on the timeline/conversation
  } catch (e) { if (st) st.textContent = ''; toast('Ask failed: ' + e.message, 'error'); }
}
async function ghDirectJob(jid) {
  const el = document.getElementById('job-direct');
  const t = (el?.value || '').trim();
  if (!t) return;
  try {
    const r = await api('/api/github/jobs/' + jid + '/direct', { method: 'POST', body: JSON.stringify({ text: t }) });
    toast(r.resumed ? '🧭 Directive filed — job resumed with it' : '🧭 Directive filed — picked up at the next stage');
    ghOpenJob(jid);
  } catch (e) { toast('Direct failed: ' + e.message, 'error'); }
}
window.ghQaPanelHtml = ghQaPanelHtml; window.ghAskJob = ghAskJob; window.ghDirectJob = ghDirectJob;

window.ghRunJob = ghRunJob; window.ghToggleCron = ghToggleCron; window.ghAnswer = ghAnswer;
window.ghDeleteJob = ghDeleteJob; window.ghApproveJob = ghApproveJob; window.ghRejectJob = ghRejectJob;
window.ghPromoteJob = ghPromoteJob; window.ghRunAllSubtasks = ghRunAllSubtasks;

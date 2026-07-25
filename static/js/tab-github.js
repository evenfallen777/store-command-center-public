/* ══ GITHUB / DEV SWARM TAB ══
   Manage your GitHub (repos, branches, PRs, issues, start new projects), see the
   dev→master→retail workflow, configure the local-model agent swarm, and propose
   jobs the swarm will work on. Phase 1 = management + config + job proposals.
   Phase 2 = the live autonomous engine (agents code/review/vote/test/promote). */

let _ghSection = 'board';
let _ghJobs = [];
let _ghJobTimer = null;   // live-refresh timer for an open, working job
let _ghProjects = [];     // dev_projects registry (per-project workboards)
let _ghProject = null;    // selected project id for the workboard
let _ghProjView = false;  // true while the Projects management view is on screen
const GH_WORKING = ['planning', 'coding', 'reviewing', 'testing'];

const GH_SECTIONS = [
  { key: 'board',    icon: '&#128203;', label: 'Workboard' },
  { key: 'repos',    icon: '&#128193;', label: 'Repositories' },
  { key: 'workflow', icon: '&#127807;', label: 'Workflow' },
  { key: 'swarm',    icon: '&#129302;', label: 'Dev Swarm' },
  { key: 'cron',     icon: '&#9200;',   label: 'Cron' },
  { key: 'watcher',  icon: '&#129658;', label: 'Watcher' },
  { key: 'agents',   icon: '&#9881;&#65039;', label: 'Agents & Models' },
  { key: 'account',  icon: '&#128279;', label: 'Account & Sharing' },
];

/* The kanban mirrors the pipeline:
   idea → GitHub dev branch → review & Q&A (user OR swarm) → main (NOT live) → ⬆️ Apply main → live store.
   A column with a `filter` fn uses it instead of the statuses list (main vs 🚀 Live
   split `done` jobs on `deployed_at`, stamped by POST /api/github/update-live). */
const BOARD_COLS = [
  { key: 'ideas',    label: '💡 Ideas',                statuses: ['proposed', 'planning', 'decomposed', 'awaiting_input', 'awaiting_system'], color: 'var(--muted)' },
  { key: 'building', label: '🔧 Building on dev',      statuses: ['coding', 'reviewing', 'testing'],  color: 'var(--accent)' },
  { key: 'review',   label: '🔎 Review & Q&A',         statuses: ['awaiting_review'],                 color: 'var(--warn)' },
  { key: 'main',     label: '✅ On main (not live)',   statuses: ['approved', 'done'],
    filter: j => j.status === 'approved' || (j.status === 'done' && !j.deployed_at),  color: '#22c55e' },
  { key: 'live',     label: '🚀 Live',                 statuses: [],
    filter: j => !!j.deployed_at,                                                     color: '#a78bfa' },
  { key: 'held',     label: '⏸️ Paused / Bug / Error', statuses: ['paused', 'failed'],                color: '#f87171' },
];

async function renderGithub() {
  document.getElementById('main-content').innerHTML = `
    <div class="view-header">
      <div class="view-title">&#128025; GitHub &amp; Dev Swarm</div>
      <div class="view-sub">Manage your repos and let a swarm of your <b>local models</b> propose,
        review, and vote on changes — you approve, then it promotes dev&nbsp;&rarr;&nbsp;main&nbsp;&rarr;&nbsp;retail.</div>
    </div>
    <div id="gh-status" style="margin-bottom:12px;"></div>
    <div id="gh-pills" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;"></div>
    <div id="gh-body"></div>`;
  renderGhPills();
  await loadGhStatus();
  await switchGhSection(_ghSection);
}
window.renderGithub = renderGithub;

function renderGhPills() {
  document.getElementById('gh-pills').innerHTML = GH_SECTIONS.map(s => `
    <button class="btn-sm ${s.key === _ghSection ? 'primary' : ''}" onclick="switchGhSection('${s.key}')">
      ${s.icon} ${s.label}</button>`).join('');
}

async function loadGhStatus() {
  const el = document.getElementById('gh-status');
  try {
    const s = await api('/api/github/status');
    if (s.authenticated) {
      el.innerHTML = `<div style="background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.35);
        border-radius:10px;padding:10px 14px;font-size:.82rem;">
        <span style="color:#22c55e;font-weight:600;">&#9679; GitHub connected</span>
        <span style="color:var(--muted);"> &mdash; ${esc(s.login || '')}</span></div>`;
    } else {
      el.innerHTML = `<div style="background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.35);
        border-radius:10px;padding:10px 14px;font-size:.82rem;">
        <span style="color:#f87171;font-weight:600;">&#9679; GitHub not authenticated</span>
        <span style="color:var(--muted);"> &mdash; run <code>gh auth login</code> in a terminal.</span></div>`;
    }
  } catch (e) { el.innerHTML = `<div style="color:var(--warn);font-size:.82rem;">${esc(e.message)}</div>`; }
}

/* ── Account & Sharing: GitHub sign-in, fork-this-install, invite collaborators.
   Moved here from Settings. (The channel-based version Updater stays in Settings.) */
async function ghRenderAccount() {
  const body = document.getElementById('gh-body');
  let s;
  try { s = await api('/api/github/status'); }
  catch (e) { body.innerHTML = `<div style="color:var(--warn);font-size:.85rem;">${esc(e.message)}</div>`; return; }
  const status = s.authenticated
    ? `<span style="color:var(--green)">&#10003; signed in as <b>${esc(s.login || '?')}</b></span>`
    : `<span style="color:var(--warn)">not signed in</span>`;
  let repoBlock = '';
  if (s.authenticated) {
    if (s.owned) {
      repoBlock = `<div style="font-size:.78rem;color:var(--green);margin:8px 0;">&#10003; This install pushes to <b>your</b> repo:
          <code style="word-break:break-all;">${esc(s.origin || '')}</code>${s.has_upstream ? ' · updates flow from <b>upstream</b>' : ''}</div>
        <button class="btn-sm" onclick="ghAddCollab()">&#129309; Add collaborator</button>
        <div style="font-size:.7rem;color:var(--muted);margin-top:4px;">Invite a GitHub user (e.g. a buddy) to push to your repo — the simplest way to work the same <code>dev</code> branch together.</div>`;
    } else if (s.origin) {
      repoBlock = `<div style="font-size:.78rem;color:var(--muted);margin:8px 0;line-height:1.6;">
          This install still points at the repo it was cloned from:<br><code style="word-break:break-all;">${esc(s.origin)}</code></div>
        <button class="btn-sm primary" onclick="ghSetupOwn()">&#127860; Fork this install to your account</button>
        <div style="font-size:.7rem;color:var(--muted);margin-top:4px;">Forks the upstream repo under your account, sets it as <code>origin</code> (updates still flow from upstream), and gives you your own <code>dev</code> branch. No retail branch.</div>
        <div style="font-size:.7rem;color:var(--warn);margin-top:6px;">&#9888;&#65039; Editing store core files will conflict on update — keep changes on your fork's <code>dev</code> branch, and prefer building add-ons as plugins.</div>`;
    } else {
      repoBlock = `<div style="font-size:.78rem;color:var(--muted);margin:8px 0;">No git remote configured yet.</div>`;
    }
  }
  body.innerHTML = `
    <div style="max-width:640px;">
      <div style="font-size:.85rem;color:var(--muted);line-height:1.7;margin-bottom:8px;">
        The GitHub account this app acts as — repos, PRs, and the Dev-Swarm promote flow all use whoever is signed in here.
        Status: ${status}
      </div>
      ${repoBlock}
      ${s.authenticated ? `
        <div style="margin-top:12px;"><button class="btn-sm danger" onclick="ghAccountLogout()">&#128275; Sign out</button></div>
      ` : `
        <div class="field" style="margin-top:8px;"><label>Personal Access Token</label>
          <input type="password" id="gh-acct-token" placeholder="ghp_&hellip; or github_pat_&hellip;" style="width:100%;">
          <div style="font-size:.68rem;color:var(--muted);margin-top:3px;">Create at github.com &rarr; Settings &rarr; Developer settings &rarr; Tokens (repo + workflow + read:org). Stored in the system keyring by gh — never saved by this app.</div></div>
        <button class="btn-sm primary" onclick="ghAccountLogin()">&#128273; Sign in to GitHub</button>
        ${(s.detail && s.detail.length) ? `<div style="font-size:.68rem;color:var(--muted);margin-top:6px;">${s.detail.map(esc).join('<br>')}</div>` : ''}
      `}
    </div>`;
}
async function ghAccountLogin() {
  const tok = (document.getElementById('gh-acct-token').value || '').trim();
  if (!tok) { toast('Paste a GitHub token first', 'error'); return; }
  try {
    const r = await api('/api/github/auth/login', { method: 'POST', body: JSON.stringify({ token: tok }) });
    toast('GitHub: signed in as ' + (r.login || 'ok'));
    await loadGhStatus(); await ghRenderAccount();
  } catch (e) { toast('Sign-in failed: ' + e.message, 'error'); }
}
async function ghAccountLogout() {
  if (!confirm('Sign the GitHub CLI out? The GitHub tab and Updates lose access until you sign in again.')) return;
  try { await api('/api/github/auth/logout', { method: 'POST' }); toast('Signed out'); await loadGhStatus(); await ghRenderAccount(); }
  catch (e) { toast('Error: ' + e.message, 'error'); }
}
async function ghAddCollab() {
  const username = prompt('GitHub username to invite as a collaborator on your repo:');
  if (!username) return;
  try {
    const r = await api('/api/github/repo/collaborator', { method: 'POST', body: JSON.stringify({ username }) });
    toast(r.message || 'Invited &#10003;');
  } catch (e) { toast('Invite failed: ' + e.message, 'error'); }
}
async function ghSetupOwn() {
  const name = prompt('Fork name under your GitHub account (leave blank to keep the same name).\n\nForks the upstream repo, sets your fork as origin (updates still flow from upstream), and creates your own dev branch. No retail branch.', '');
  if (name === null) return;
  toast('Forking & setting up your dev branch — this can take a minute…');
  try {
    const r = await api('/api/github/repo/setup-own', { method: 'POST', body: JSON.stringify(name ? { name } : {}) });
    toast(r.message || 'Fork ready &#10003;');
    await loadGhStatus(); await ghRenderAccount();
  } catch (e) { toast('Fork setup failed: ' + e.message, 'error'); }
}
window.ghRenderAccount = ghRenderAccount; window.ghAccountLogin = ghAccountLogin;
window.ghAccountLogout = ghAccountLogout; window.ghAddCollab = ghAddCollab; window.ghSetupOwn = ghSetupOwn;

async function switchGhSection(sec) {
  if (_ghJobTimer) { clearTimeout(_ghJobTimer); _ghJobTimer = null; }
  _ghSection = sec; renderGhPills();
  const body = document.getElementById('gh-body');
  body.innerHTML = '<div class="loading-state">Loading…</div>';
  try {
    if (sec === 'board')    return await ghRenderBoard();
    if (sec === 'account')  return await ghRenderAccount();
    if (sec === 'repos')    return await ghRenderRepos();
    if (sec === 'workflow') return await ghRenderWorkflow();
    if (sec === 'swarm')    return await ghRenderSwarm();
    if (sec === 'cron')     return await ghRenderCron();
    if (sec === 'watcher')  return await ghRenderWatcher();
    if (sec === 'agents')   return await ghRenderAgents();
  } catch (e) {
    body.innerHTML = `<div class="empty"><div class="empty-icon">&#9888;&#65039;</div>${esc(e.message)}</div>`;
  }
}
window.switchGhSection = switchGhSection;

/* ── Workboard (per-project kanban of swarm jobs by status) ───────────────── */
async function ghLoadProjects() {
  _ghProjects = (await api('/api/github/projects')).projects || [];
  if (_ghProject == null || !_ghProjects.some(p => p.id === _ghProject)) {
    const prim = _ghProjects.find(p => p.is_primary) || _ghProjects[0];
    _ghProject = prim ? prim.id : null;
  }
  return _ghProjects;
}

function ghProjectHeaderHtml(p, live) {
  if (!p) return '';
  const auto = p.autonomy || 'auto';
  const rm = p.review_mode || 'human';
  const liveHtml = live ? `
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:8px;padding-top:8px;border-top:1px solid var(--border);">
      <button class="btn-sm ${live.update_available ? 'primary' : ''}" onclick="ghUpdateLive()"
        title="Pulls the approved code on main into THIS running store and restarts it. (Different from Settings → Updates, which pulls a published release/public version.)">
        &#11014;&#65039; Apply main &rarr; live store${live.behind ? ` (${live.behind})` : ''}</button>
      <span style="font-size:.7rem;color:${live.update_available ? 'var(--warn)' : 'var(--muted)'};">
        ${live.update_available
          ? `${live.pending_done || live.behind} approved change${(live.pending_done || live.behind) === 1 ? '' : 's'} on main — not applied to the live store yet`
          : 'live store is up to date with main'}
        <span style="opacity:.6;"> · live ${esc(live.head || '?')} / main ${esc(live.remote_head || '?')}</span></span>
    </div>` : '';
  return `<div class="card" style="padding:10px 12px;margin-bottom:12px;">
    <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;">
      <span style="font-weight:700;font-size:.88rem;">${p.kind === 'store' ? '&#127970;' : '&#128193;'} ${esc(p.name)}</span>
      <span style="font-size:.68rem;color:var(--muted);">${esc(p.repo || 'no remote')} · live: <b>${esc(p.live_branch || 'main')}</b></span>
      ${(p.kind === 'store' || p.is_primary)
        ? `<span style="font-size:.74rem;display:flex;gap:4px;align-items:center;">Local agents work on:
             <span class="btn-sm primary" style="padding:2px 9px;cursor:default;" title="Locked for the store">dev</span>
             ${hlp('The store local dev swarm ALWAYS works on the dev branch — main is Claude lane (local models → dev, Claude → master). Their work reaches main only through the review → promote pipeline. Locked for the store; side projects get a dev/main toggle.')}</span>`
        : `<span style="font-size:.74rem;display:flex;gap:4px;align-items:center;">Work on:
             <button class="btn-sm ${p.work_branch !== 'main' ? 'primary' : ''}" style="padding:2px 9px;" onclick="ghProjSet(${p.id},'work_branch','dev')" title="The swarm edits the project's dev worktree/branch (staged).">dev</button>
             <button class="btn-sm ${p.work_branch === 'main' ? 'primary' : ''}" style="padding:2px 9px;" onclick="ghProjSet(${p.id},'work_branch','main')" title="The swarm edits the live worktree directly — a human still approves before code goes live.">main</button>
             ${hlp('WHERE the swarm edits, not whether you approve. dev = staged on the dev worktree/branch; main = edit the live worktree directly. Either way, a human approval (Approve → Promote) is ALWAYS required before code lands on the live branch.')}</span>`}
      <label style="font-size:.74rem;display:flex;gap:5px;align-items:center;cursor:pointer;">
        <input type="checkbox" ${p.engineers_enabled ? 'checked' : ''} onchange="ghProjSet(${p.id},'engineers_enabled',this.checked?1:0)">
        &#128736;&#65039; Engineers ${hlp('Let The Engineers (the autonomous dev crew) propose AND auto-build improvements to this project on its working branch. Their finished work still waits at the merge gate for your approval.')}</label>
      <label style="font-size:.74rem;display:flex;gap:5px;align-items:center;cursor:pointer;">
        <input type="checkbox" ${p.merge_gate ? 'checked' : ''} onchange="ghProjSet(${p.id},'merge_gate',this.checked?1:0)">
        &#128274; Merge gate ${hlp('Human approval before code lands on the live branch. This is the policy backbone — leave it on.')}</label>
      <label style="font-size:.74rem;color:var(--muted);display:flex;gap:5px;align-items:center;">autonomy
        <select style="font-size:.72rem;" onchange="ghProjSet(${p.id},'autonomy',this.value)">
          <option value="auto" ${auto === 'auto' ? 'selected' : ''}>auto</option>
          <option value="gate" ${auto === 'gate' ? 'selected' : ''}>gate</option>
          <option value="step" ${auto === 'step' ? 'selected' : ''}>step</option>
        </select></label>
      <label style="font-size:.74rem;color:var(--muted);display:flex;gap:5px;align-items:center;">review
        <select style="font-size:.72rem;" onchange="ghProjSet(${p.id},'review_mode',this.value)">
          <option value="human" ${rm === 'human' ? 'selected' : ''}>&#129489; you approve</option>
          <option value="swarm" ${rm === 'swarm' ? 'selected' : ''}>&#129302; swarm consensus</option>
          <option value="either" ${rm === 'either' ? 'selected' : ''}>&#129309; either</option>
        </select>
        ${hlp('Who satisfies the review gate before a change merges to MAIN. you = the job waits for your Approve; swarm = the reviewer-panel consensus auto-merges to main; either = whichever comes first. Reaching main is NOT going live — only ⬆️ Apply main → live store pulls main into the running store.')}</label>
      <label style="font-size:.74rem;display:flex;gap:5px;align-items:center;cursor:pointer;">
        <input type="checkbox" ${p.auto_go_live ? 'checked' : ''} onchange="ghProjSet(${p.id},'auto_go_live',this.checked?1:0)">
        &#128640; Auto go-live ${hlp('Auto go-live after approval (swarm applies main→live): as soon as a job of this project is promoted to main, the store automatically applies main → live store and restarts — no click. Store/primary only (side projects have no separate running live; reaching main IS done there). Combined with review mode = swarm this is the fully-autonomous chain; both are OFF by default.')}</label>
    </div>
    <div style="font-size:.66rem;color:var(--muted);margin-top:6px;">
      Pipeline: idea &rarr; GitHub <b>${esc(p.work_branch || 'dev')}</b> branch &rarr; review &amp; Q&amp;A
      (${rm === 'human' ? 'you' : rm === 'swarm' ? 'the reviewer swarm' : 'you or the swarm'})
      &rarr; <b>${esc(p.live_branch || 'main')}</b> (main &ne; live) &rarr; &#11014;&#65039; Apply main &rarr; live store.
      Nothing reaches the LIVE store until it's applied${p.auto_go_live ? ' (auto go-live is ON for this project)' : ''}.</div>
    ${liveHtml}
  </div>`;
}

async function ghUpdateLive() {
  if (!confirm('Apply main → live store?\n\nPulls the approved code on main into THIS running store (ff-merge) and restarts it (~10s downtime). Different from Settings → Updates, which pulls a published release/public version.')) return;
  toast('Applying main to the live store…');
  try {
    const r = await api('/api/github/update-live', { method: 'POST' });
    toast(r.was_current && !r.marked_live ? 'Already up to date — restarting anyway'
          : `✅ Applying ${r.updated_to || 'main'} to the live store — back in ~10s`);
  } catch (e) { toast('Apply failed: ' + e.message, 'error'); }
}
window.ghUpdateLive = ghUpdateLive;

async function ghRenderBoard() {
  _ghProjView = false;
  await ghLoadProjects();
  const proj = _ghProjects.find(p => p.id === _ghProject) || null;
  const jobs = await api('/api/github/jobs' + (proj ? '?project_id=' + proj.id : ''));
  // "update available" indicator — the store's live worktree vs origin/master
  let live = null;
  if (proj && proj.kind === 'store') {
    try { live = await api('/api/github/live-status'); } catch { }
  }
  // Fork-first warning: if this install still points at upstream (not forked/owned),
  // editing store files here gets overwritten on update — nudge them to fork first.
  let acct = null;
  try { acct = await api('/api/github/status'); } catch { }
  const forkWarn = (acct && acct.authenticated && !acct.owned && acct.origin) ? `
    <div style="background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.4);border-radius:10px;padding:9px 13px;margin-bottom:12px;font-size:.76rem;color:var(--warn);display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
      <span>&#9888;&#65039; This install isn't forked yet — it still points at <code>${esc((acct.origin || '').replace(/^https?:\/\/github.com\//, '').replace(/\.git$/, ''))}</code>.
      Editing store files here will be <b>overwritten on the next update</b>. Fork it first so your changes live in your own repo &amp; dev branch.</span>
      <button class="btn-sm primary" style="margin-left:auto;" onclick="switchGhSection('account')">&#127860; Fork this install</button>
    </div>` : '';
  const body = document.getElementById('gh-body');
  if (!body || _ghSection !== 'board') return;
  const col = (c) => {
    const items = jobs.filter(j => c.filter ? c.filter(j) : c.statuses.includes(j.status));
    const label = c.key === 'review' && proj
      ? `${c.label} <span style="opacity:.6;text-transform:none;">· ${esc(proj.review_mode || 'human')}</span>` : c.label;
    return `<div style="flex:1;min-width:200px;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px;">
      <div style="font-size:.74rem;font-weight:700;color:${c.color};text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px;display:flex;justify-content:space-between;">
        <span>${label}</span><span style="opacity:.6;">${items.length}</span></div>
      <div style="display:flex;flex-direction:column;gap:8px;">
        ${items.map(j => `<div class="card" style="padding:9px 10px;cursor:pointer;" onclick="ghOpenJob(${j.id})">
          <div style="font-weight:600;font-size:.78rem;line-height:1.3;">${esc(j.title)}</div>
          <div style="font-size:.66rem;color:var(--muted);margin-top:4px;">${esc((j.repo || '').split('/').pop() || '')} · ${esc(j.branch || 'dev')}${j.open_questions ? ` · <span style="color:var(--warn);">${j.open_questions}?</span>` : ''}</div>
          ${GH_WORKING.includes(j.status) ? `<div style="font-size:.64rem;color:var(--accent);margin-top:3px;">⏳ ${esc(j.progress_msg || j.status)}</div>` : ''}
        </div>`).join('') || '<div style="font-size:.7rem;color:var(--muted);opacity:.5;">—</div>'}
      </div></div>`;
  };
  const switcher = _ghProjects.map(p => {
    const n = p.job_counts ? Object.values(p.job_counts).reduce((a, b) => a + b, 0) : 0;
    return `<button class="btn-sm ${p.id === _ghProject ? 'primary' : ''}" onclick="ghSwitchProject(${p.id})">
      ${p.kind === 'store' ? '&#127970;' : '&#128193;'} ${esc(p.name)}${n ? ` <span style="opacity:.6;">${n}</span>` : ''}</button>`;
  }).join('');
  body.innerHTML = `
    ${forkWarn}
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px;">
      ${switcher}
      <button class="btn-sm" onclick="ghRenderProjects()">&#9881;&#65039; Projects</button>
      <button class="btn-sm" onclick="ghNewDevProject()">&#10133; New project</button>
      <span style="margin-left:auto;"></span>
      <span style="color:var(--muted);font-size:.78rem;">${jobs.length} jobs</span>
      <button class="btn-sm primary" onclick="switchGhSection('swarm')">&#10133; New job</button>
    </div>
    ${ghProjectHeaderHtml(proj, live)}
    <div style="display:flex;gap:12px;overflow-x:auto;align-items:flex-start;padding-bottom:8px;">
      ${BOARD_COLS.map(col).join('')}
    </div>`;
  // live-refresh the board while anything is actively working
  if (jobs.some(j => GH_WORKING.includes(j.status)) && _ghSection === 'board') {
    _ghJobTimer = setTimeout(() => { if (_ghSection === 'board') ghRenderBoard(); }, 5000);
  }
}
window.ghRenderBoard = ghRenderBoard;

function ghSwitchProject(pid) { _ghProject = pid; ghRenderBoard(); }
window.ghSwitchProject = ghSwitchProject;

async function ghProjSet(pid, key, val) {
  try {
    await api('/api/github/projects/' + pid, { method: 'PATCH', body: JSON.stringify({ [key]: val }) });
    if (key === 'work_branch') {
      toast(val === 'main'
        ? 'Swarm now edits the LIVE worktree — a human still approves before code goes live'
        : 'Swarm now edits the dev worktree (staged)');
    } else { toast('Saved'); }
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
  if (_ghSection === 'board') (_ghProjView ? ghRenderProjects() : ghRenderBoard());
}
window.ghProjSet = ghProjSet;

/* ── Projects management view (register / create / delete dev projects) ───── */
async function ghRenderProjects() {
  if (_ghJobTimer) { clearTimeout(_ghJobTimer); _ghJobTimer = null; }
  _ghProjView = true;
  await ghLoadProjects();
  const body = document.getElementById('gh-body');
  body.innerHTML = `
    <button class="btn-sm" onclick="ghRenderBoard()">&#8592; Workboard</button>
    <div style="color:var(--muted);font-size:.78rem;margin:10px 0;">
      Every project the dev swarm / The Engineers can work. The policy is identical for all of them:
      <b>auto-build on the working branch, human approval before the live branch</b>. The Work-on toggle
      only picks where the swarm edits.</div>
    <div style="display:flex;flex-direction:column;gap:10px;">
      ${_ghProjects.map(p => `<div class="card" style="padding:12px;">
        ${ghProjectHeaderHtml(p)}
        <div style="display:flex;gap:10px;align-items:center;font-size:.7rem;color:var(--muted);flex-wrap:wrap;">
          <span>kind: ${esc(p.kind)}</span>
          ${p.local_path ? `<span>path: <code>${esc(p.local_path)}</code></span>` : ''}
          ${p.job_counts ? `<span>jobs: ${Object.entries(p.job_counts).filter(([, v]) => v).map(([k, v]) => `${k} ${v}`).join(' · ') || 'none'}</span>` : ''}
          <span style="margin-left:auto;"></span>
          <button class="btn-sm" onclick="ghSwitchProject(${p.id});">&#128203; Open board</button>
          ${p.kind !== 'store' ? `<button class="btn-sm" onclick="ghDeleteProject(${p.id})">&#128465;&#65039; Remove</button>` : ''}
        </div>
      </div>`).join('')}
    </div>
    <div style="margin-top:12px;"><button class="btn-sm primary" onclick="ghNewDevProject()">&#10133; New project</button></div>`;
}
window.ghRenderProjects = ghRenderProjects;

async function ghNewDevProject() {
  const name = prompt('Project name:');
  if (!name) return;
  const existing = prompt('Existing GitHub repo to register (owner/name) — leave EMPTY to create a brand-new repo:') || '';
  let payload;
  if (existing.trim()) {
    payload = { name, repo: existing.trim(), kind: 'external', create_repo: false };
  } else {
    const priv = confirm('Create a NEW GitHub repo for it?\n\nOK = private repo, Cancel = public repo');
    payload = { name, kind: 'engineer', create_repo: true, private: priv };
  }
  payload.engineers_enabled = confirm('Enable The Engineers on this project?\n\n(They auto-build improvements; you still approve before anything goes live.)');
  toast('Creating project…');
  try {
    const p = await api('/api/github/projects', { method: 'POST', body: JSON.stringify(payload) });
    toast('✅ Project “' + p.name + '” registered');
    _ghProject = p.id;
    if (_ghSection === 'board') ghRenderBoard();
  } catch (e) { toast('Create failed: ' + e.message, 'error'); }
}
window.ghNewDevProject = ghNewDevProject;

async function ghDeleteProject(pid) {
  if (!confirm('Remove this project from the registry? (Its repo and local files are NOT deleted.)')) return;
  try { await api('/api/github/projects/' + pid, { method: 'DELETE' }); toast('Removed'); ghRenderProjects(); }
  catch (e) { toast('Failed: ' + e.message, 'error'); }
}
window.ghDeleteProject = ghDeleteProject;

/* ── Repositories ─────────────────────────────────────────────────────────── */
async function ghRenderRepos() {
  const data = await api('/api/github/repos');
  const body = document.getElementById('gh-body');
  body.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
      <span style="color:var(--muted);font-size:.82rem;">${data.count} repositories</span>
      <button class="btn-sm primary" onclick="ghNewProjectModal()">&#10133; New project</button>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;">
      ${data.repos.map(r => `
        <div class="card" style="padding:14px;cursor:pointer;" onclick="ghOpenRepo('${esc(r.full)}')">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
            <span style="font-weight:600;font-size:.9rem;">${esc(r.name)}</span>
            <span style="font-size:.62rem;padding:2px 7px;border-radius:20px;
              background:${r.private ? 'rgba(234,179,8,.15)' : 'rgba(34,197,94,.15)'};
              color:${r.private ? '#eab308' : '#22c55e'};">${r.private ? 'private' : 'public'}</span>
          </div>
          <div style="color:var(--muted);font-size:.75rem;margin-top:6px;min-height:2.2em;">${esc(r.description || '—')}</div>
          <div style="color:var(--muted);font-size:.68rem;margin-top:8px;">
            ${esc(r.default_branch || '')} · ★ ${r.stars} · ${r.pushed_at ? new Date(r.pushed_at).toLocaleDateString() : ''}
          </div>
        </div>`).join('')}
    </div>`;
}

let _ghRepo = null;   // current repo full name for the browser

async function ghOpenRepo(full) {
  _ghRepo = full;
  const body = document.getElementById('gh-body');
  body.innerHTML = '<div class="loading-state">Loading ' + esc(full) + '…</div>';
  const r = await api('/api/github/repo?full=' + encodeURIComponent(full));
  body.innerHTML = `
    <button class="btn-sm" onclick="switchGhSection('repos')">&#8592; All repos</button>
    <div class="card" style="padding:16px;margin-top:12px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
        <div style="font-weight:700;font-size:1.05rem;">${esc(r.full)}</div>
        <a class="btn-sm" href="${esc(r.url)}" target="_blank" rel="noopener">Open on GitHub &#8599;</a>
      </div>
      <div style="color:var(--muted);font-size:.82rem;margin-top:6px;">${esc(r.description || '')}</div>
      <div style="color:var(--muted);font-size:.72rem;margin-top:6px;">default: ${esc(r.default_branch || '')} · ${esc(r.visibility || '')}</div>
      <div style="margin-top:12px;">
        <button class="btn-sm primary" onclick="ghProposeForRepo('${esc(r.full)}')">&#129302; Propose a swarm job on this repo</button>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px;">
      <div id="gh-files" class="card" style="padding:12px;"><div class="loading-state">Loading files…</div></div>
      <div style="display:flex;flex-direction:column;gap:12px;">
        ${ghListCard('Branches (' + r.branches.length + ')', r.branches.map(b => `<div>${esc(b)}</div>`).join('') || '—')}
        ${ghListCard('Open PRs (' + r.pulls.length + ')', r.pulls.map(p => `<div><a href="${esc(p.url)}" target="_blank" rel="noopener">#${p.number} ${esc(p.title)}</a></div>`).join('') || 'none')}
        ${ghListCard('Open Issues (' + r.issues.length + ')', r.issues.map(i => `<div><a href="${esc(i.url)}" target="_blank" rel="noopener">#${i.number} ${esc(i.title)}</a></div>`).join('') || 'none')}
      </div>
    </div>
    <div id="gh-readme" class="card" style="padding:16px;margin-top:12px;"><div class="loading-state">Checking for README…</div></div>`;
  ghBrowse(full, '');
  ghLoadReadme(full);
}

async function ghBrowse(full, path) {
  const el = document.getElementById('gh-files');
  if (!el) return;
  el.innerHTML = '<div class="loading-state">Loading…</div>';
  try {
    const d = await api(`/api/github/repo/contents?full=${encodeURIComponent(full)}&path=${encodeURIComponent(path)}`);
    const up = path ? path.split('/').slice(0, -1).join('/') : null;
    el.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <div style="font-weight:600;font-size:.8rem;">&#128193; /${esc(path)}</div>
        <button class="btn-sm" onclick="ghScopeJob('${esc(full)}','${esc(path || '')}','folder')">Scope job here</button>
      </div>
      <div style="font-size:.78rem;line-height:1.9;max-height:340px;overflow:auto;">
        ${path !== '' ? `<div style="cursor:pointer;color:var(--muted);" onclick="ghBrowse('${esc(full)}','${esc(up)}')">&#8617; ..</div>` : ''}
        ${d.items.map(it => it.type === 'dir'
          ? `<div style="cursor:pointer;" onclick="ghBrowse('${esc(full)}','${esc(it.path)}')">&#128193; ${esc(it.name)}</div>`
          : `<div style="display:flex;justify-content:space-between;gap:8px;">
               <span style="cursor:pointer;" onclick="ghViewFile('${esc(full)}','${esc(it.path)}')">&#128196; ${esc(it.name)}</span>
               <span style="cursor:pointer;color:var(--accent2);font-size:.66rem;" title="Scope a job to this file"
                 onclick="ghScopeJob('${esc(full)}','${esc(it.path)}','file')">scope&nbsp;&#8594;</span>
             </div>`).join('') || '<span style="color:var(--muted);">empty</span>'}
      </div>`;
  } catch (e) { el.innerHTML = `<div style="color:var(--warn);font-size:.78rem;">${esc(e.message)}</div>`; }
}
window.ghBrowse = ghBrowse;

async function ghViewFile(full, path) {
  try {
    const d = await api(`/api/github/repo/file?full=${encodeURIComponent(full)}&path=${encodeURIComponent(path)}`);
    openLightbox && document.getElementById('lightbox') ? null : null;
    const w = window.open('', '_blank');
    if (w) { w.document.write('<pre style="white-space:pre-wrap;font-family:monospace;padding:16px;">' +
             esc(path) + '\n\n' + esc(d.content) + '</pre>'); w.document.title = path; }
  } catch (e) { toast('Open failed: ' + e.message, 'error'); }
}
window.ghViewFile = ghViewFile;

async function ghLoadReadme(full) {
  const el = document.getElementById('gh-readme');
  if (!el) return;
  try {
    const d = await api('/api/github/repo/readme?full=' + encodeURIComponent(full));
    if (!d.has_readme) { el.innerHTML = '<div style="color:var(--muted);font-size:.78rem;">No README in this repo.</div>'; return; }
    el.innerHTML = `<div style="font-weight:600;font-size:.82rem;margin-bottom:10px;">&#128196; ${esc(d.name)}</div>
      <div class="readme-body" style="font-size:.82rem;line-height:1.6;max-height:520px;overflow:auto;">${d.html}</div>`;
  } catch (e) { el.innerHTML = `<div style="color:var(--muted);font-size:.78rem;">README unavailable: ${esc(e.message)}</div>`; }
}

function ghScopeJob(full, path, scope) {
  _ghSection = 'swarm'; renderGhPills();
  document.getElementById('gh-body').innerHTML = '<div class="loading-state">…</div>';
  ghRenderSwarm(full).then(() => {
    const f = document.getElementById('gh-job-form'); if (f) f.open = true;
    const sc = document.getElementById('job-scope'); if (sc) { sc.value = scope; ghScopeChanged(); }
    const p = document.getElementById('job-paths'); if (p) p.value = path;
    const t = document.getElementById('job-title'); if (t) t.focus();
  });
}
window.ghScopeJob = ghScopeJob;
function ghListCard(title, html) {
  return `<div class="card" style="padding:12px;">
    <div style="font-weight:600;font-size:.8rem;margin-bottom:8px;">${title}</div>
    <div style="font-size:.74rem;line-height:1.7;color:var(--muted);max-height:220px;overflow:auto;">${html}</div>
  </div>`;
}
window.ghOpenRepo = ghOpenRepo;

function ghNewProjectModal() {
  const name = prompt('New repository name:');
  if (!name) return;
  const desc = prompt('Description (optional):') || '';
  const priv = confirm('Private repo?  OK = private, Cancel = public');
  api('/api/github/repo/create', { method: 'POST', body: JSON.stringify({
    name, description: desc, private: priv, add_readme: true, gitignore: 'Python'
  })}).then(res => { toast('✅ ' + res.message); switchGhSection('repos'); })
     .catch(e => toast('Create failed: ' + e.message, 'error'));
}
window.ghNewProjectModal = ghNewProjectModal;

/* ── Workflow (dev → master → retail) ─────────────────────────────────────── */
async function ghRenderWorkflow() {
  const data = await api('/api/github/workflow');
  const body = document.getElementById('gh-body');
  body.innerHTML = `
    <div id="gh-devstore" style="margin-bottom:14px;"></div>
    <div id="gh-collab" style="margin-bottom:14px;"></div>
    <div style="color:var(--muted);font-size:.82rem;margin-bottom:12px;">
      Your three worktrees. Develop on <b>dev</b>, promote working changes to <b>master</b> (live), then <b>retail</b> (clean).
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;">
      ${data.worktrees.map(w => `
        <div class="card" style="padding:14px;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-weight:700;">${esc(w.branch)}</span>
            <span style="font-size:.66rem;color:var(--muted);">${w.port ? ':' + w.port : ''} ${w.base ? esc(w.base) : ''}</span>
          </div>
          <div style="font-family:monospace;font-size:.72rem;color:var(--muted);margin-top:6px;">${esc(w.head || 'missing')} @ ${esc(w.checked_out || '')}</div>
          <div style="font-size:.74rem;margin-top:8px;">
            ${w.dirty ? `<span style="color:var(--warn);">● ${w.changed_files} uncommitted</span>` : '<span style="color:#22c55e;">● clean</span>'}
            ${w.branch !== 'master' && w.ahead != null ? `<span style="color:var(--muted);"> · ${w.ahead} ahead / ${w.behind} behind master</span>` : ''}
          </div>
        </div>`).join('')}
    </div>
    <div style="margin-top:14px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
      <button class="btn-sm" onclick="ghRestartLive()" title="Restart this live Store app so it loads the code already promoted to the master worktree on disk. Briefly unavailable (~10s). A promote writes the files but the running process keeps old code until this restart.">&#128260; Restart live app (load promoted code)</button>
      <span style="font-size:.72rem;color:var(--muted);">A promote writes new code to the master worktree on disk — the live app loads it on restart (no GitHub pull needed here).</span>
    </div>`;
  await ghDevStoreRefresh();
  await ghCollabRefresh();
}

/* ── 🧪 Dev test store (store-dev on :8788) ──────────────────────────────── */
async function ghDevStoreRefresh() {
  const el = document.getElementById('gh-devstore');
  if (!el) return;   // user navigated away from the Workflow section
  let s;
  try { s = await api('/api/github/dev-store/status'); }
  catch (e) { el.innerHTML = `<div class="card" style="padding:14px;color:var(--warn);font-size:.8rem;">🧪 Dev test store — status failed: ${esc(e.message)}</div>`; return; }
  const status = s.running
    ? `<span style="color:#22c55e;font-weight:600;">&#9679; running${s.pid ? ' (pid ' + s.pid + ')' : ''}</span>${s.healthy ? ' <span style="color:#22c55e;" title="answers HTTP on :8788">&#10003; healthy</span>' : ' <span style="color:var(--muted);">starting&hellip;</span>'}`
    : `<span style="color:var(--muted);font-weight:600;">&#9675; stopped</span>`;
  el.innerHTML = `
    <div class="card" style="padding:14px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
        <span style="font-weight:700;">&#129514; Dev test store</span>
        <span style="font-size:.66rem;color:var(--muted);">:${s.port}</span>
      </div>
      <div style="font-size:.78rem;margin-top:8px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
        ${status}
        <span style="font-family:monospace;font-size:.72rem;color:var(--muted);">store-dev @ ${esc(s.head || '?')}${s.branch ? ' (' + esc(s.branch) + ')' : ''}</span>
        ${s.behind > 0 ? `<span style="color:var(--warn);">&#9888;&#65039; ${s.behind} behind origin/dev</span>` : `<span style="color:var(--muted);">in sync with origin/dev</span>`}
        ${s.dirty ? `<span style="color:var(--warn);">&#9679; ${s.dirty} uncommitted</span>` : ''}
      </div>
      <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;">
        <button class="btn-sm" onclick="ghDevStoreStart()" ${s.running ? 'disabled' : ''}>&#9654; Start</button>
        <button class="btn-sm" onclick="ghDevStorePull()">&#11015; Pull github-dev</button>
        <button class="btn-sm" onclick="window.open('http://' + location.hostname + ':${s.port}', '_blank')" ${s.running ? '' : 'disabled'}>&#8599; Open :${s.port}</button>
        <button class="btn-sm danger" onclick="ghDevStoreStop()" ${s.running ? '' : 'disabled'}>&#9209; Stop</button>
      </div>
      <div style="font-size:.7rem;color:var(--muted);margin-top:6px;">Start the dev store to smoke-test dev code on :${s.port} before promoting. Pull syncs it to origin/dev. Stop it when done — don't run two stores all the time.</div>
    </div>`;
}
async function ghDevStoreStart() {
  try {
    const r = await api('/api/github/dev-store/start', { method: 'POST' });
    toast(r.already ? 'Already running (pid ' + r.pid + ')' : (r.note || 'Starting…'));
    await ghDevStoreRefresh();
    // poll a few times so the card flips to running/healthy once uvicorn is up
    for (let i = 0; i < 4; i++) {
      await new Promise(res => setTimeout(res, 3000));
      await ghDevStoreRefresh();
      if (!document.getElementById('gh-devstore')) break;
    }
  } catch (e) { toast('Start failed: ' + e.message, 'error'); }
}
async function ghDevStorePull() {
  try {
    const r = await api('/api/github/dev-store/pull', { method: 'POST' });
    toast('Pulled — store-dev @ ' + (r.updated_to || '?') + (r.restarted ? ' (restarted)' : ''));
  } catch (e) { toast('Pull failed: ' + e.message, 'error'); }
  await ghDevStoreRefresh();
}
async function ghDevStoreStop() {
  try {
    const r = await api('/api/github/dev-store/stop', { method: 'POST' });
    if (r.ok === false) toast(r.note || 'Could not stop', 'error');
    else toast(r.already_stopped ? 'Already stopped' : 'Dev store stopped');
  } catch (e) { toast('Stop failed: ' + e.message, 'error'); }
  await ghDevStoreRefresh();
}
window.ghDevStoreRefresh = ghDevStoreRefresh; window.ghDevStoreStart = ghDevStoreStart;
window.ghDevStorePull = ghDevStorePull; window.ghDevStoreStop = ghDevStoreStop;

/* ── 🤝 Collaborators (shared collab branch → dev → main) ─────────────────── */
async function ghCollabRefresh() {
  const el = document.getElementById('gh-collab');
  if (!el) return;   // user navigated away from the Workflow section
  let s;
  try { s = await api('/api/github/collab/status'); }
  catch (e) { el.innerHTML = `<div class="card" style="padding:14px;color:var(--warn);font-size:.8rem;">&#129309; Collaborators — status failed: ${esc(e.message)}</div>`; return; }
  const helper = `<div style="font-size:.7rem;color:var(--muted);margin-top:6px;">Collaborators push to <b>collab</b>; review and merge into <b>dev</b> here. From <b>dev</b> it reaches <b>main</b> through the normal promote pipeline (nothing extra).</div>`;
  if (!s.exists) {
    el.innerHTML = `
      <div class="card" style="padding:14px;">
        <div style="font-weight:700;">&#129309; Collaborators</div>
        <div style="font-size:.78rem;color:var(--muted);margin-top:8px;">
          No <b>collab</b> branch yet. Create one (cut from <b>dev</b>) as the shared branch
          human collaborators push to — you review their work and merge it into <b>dev</b> here.
        </div>
        <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;">
          <button class="btn-sm" onclick="ghCollabEnsure()">&#10133; Create collab branch</button>
          <button class="btn-sm" onclick="ghAddCollab()">&#129309; Add collaborator</button>
        </div>
        ${helper}
      </div>`;
    return;
  }
  const ahead = s.ahead || 0;
  const aheadHtml = ahead > 0
    ? `<span style="color:var(--warn);font-weight:600;">&#9679; collab: ${ahead} commit(s) ahead of dev</span>`
    : `<span style="color:#22c55e;font-weight:600;">&#9679; collab: 0 commits ahead of dev</span>`;
  const who = (s.contributors || []).map(c => `${esc(c.name)} (${c.count})`).join(', ');
  const statLine = (s.diff_stat || '').split('\n').pop() || '';
  el.innerHTML = `
    <div class="card" style="padding:14px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
        <span style="font-weight:700;">&#129309; Collaborators</span>
        <span style="font-size:.66rem;color:var(--muted);">collab &rarr; dev &rarr; main</span>
      </div>
      <div style="font-size:.78rem;margin-top:8px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
        ${aheadHtml}
        ${who ? `<span style="color:var(--muted);">by ${who}</span>` : ''}
        ${statLine ? `<span style="font-family:monospace;font-size:.7rem;color:var(--muted);">${esc(statLine.trim())}</span>` : ''}
        ${s.dirty ? `<span style="color:var(--warn);">&#9679; dev worktree: ${s.dirty} uncommitted</span>` : ''}
      </div>
      <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;">
        <button class="btn-sm" onclick="ghCollabToggleDiff()" ${ahead ? '' : 'disabled'}>&#128065; Review diff</button>
        <button class="btn-sm" onclick="ghCollabMerge()" ${ahead ? '' : 'disabled'}>&#11015; Merge collab &rarr; dev</button>
        <button class="btn-sm" onclick="ghAddCollab()">&#129309; Add collaborator</button>
      </div>
      <div id="gh-collab-diff" style="display:none;margin-top:10px;">
        ${(s.commits || []).length ? `<div style="font-size:.74rem;font-weight:600;margin-bottom:4px;">Waiting to merge:</div>
        <div style="font-family:monospace;font-size:.72rem;line-height:1.5;">
          ${(s.commits || []).map(c => `<div><span style="color:var(--muted);">${esc(c.sha)}</span> ${esc(c.subject)}</div>`).join('')}
        </div>` : ''}
        ${s.diff_stat ? `<pre style="font-size:.68rem;color:var(--muted);margin-top:6px;overflow-x:auto;">${esc(s.diff_stat)}</pre>` : ''}
      </div>
      ${helper}
    </div>`;
}
async function ghCollabEnsure() {
  try {
    const r = await api('/api/github/collab/ensure', { method: 'POST' });
    toast(r.already ? 'collab branch already exists' : '✅ ' + (r.note || 'collab branch created'));
  } catch (e) { toast('Create failed: ' + e.message, 'error'); }
  await ghCollabRefresh();
}
async function ghCollabMerge() {
  if (!confirm('Merge collab into dev? (dev → main still goes through the normal promote pipeline.)')) return;
  try {
    const r = await api('/api/github/collab/merge-to-dev', { method: 'POST' });
    toast('✅ Merged ' + (r.merged_commits ? r.merged_commits.length : 0) + ' commit(s). ' + (r.note || ''));
  } catch (e) { toast('Merge failed: ' + e.message, 'error'); }
  await ghCollabRefresh();
}
function ghCollabToggleDiff() {
  const d = document.getElementById('gh-collab-diff');
  if (d) d.style.display = d.style.display === 'none' ? 'block' : 'none';
}
window.ghCollabRefresh = ghCollabRefresh; window.ghCollabEnsure = ghCollabEnsure;
window.ghCollabMerge = ghCollabMerge; window.ghCollabToggleDiff = ghCollabToggleDiff;

/* ── Cron management ──────────────────────────────────────────────────────── */
async function ghRenderCron() {
  const jobs = await api('/api/github/jobs');
  const body = document.getElementById('gh-body');
  const active = jobs.filter(j => j.cron_enabled);
  body.innerHTML = `
    <div style="color:var(--muted);font-size:.82rem;margin-bottom:12px;">
      Cron keeps working-in-progress jobs moving on a schedule — it advances any non-gated job
      on its interval (gated jobs still wait for you). ${active.length} scheduled.
    </div>
    ${jobs.length ? `<div style="display:flex;flex-direction:column;gap:10px;">${jobs.map(j => `
      <div class="card" style="padding:12px;">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
          <span style="font-weight:600;font-size:.84rem;cursor:pointer;" onclick="ghOpenJob(${j.id})">${esc(j.title)}</span>
          <span style="font-size:.64rem;color:var(--muted);">${esc(j.status)} · updated ${j.updated_at ? new Date(j.updated_at.replace(' ','T')+'Z').toLocaleString() : ''}</span>
        </div>
        <div style="display:flex;gap:10px;align-items:center;margin-top:8px;flex-wrap:wrap;">
          <label style="font-size:.76rem;display:flex;gap:6px;align-items:center;cursor:pointer;">
            <input type="checkbox" ${j.cron_enabled ? 'checked' : ''} onchange="ghCronToggle(${j.id}, this.checked)"> scheduled ${hlp('When on, the swarm advances this job automatically on the interval below - no clicks needed. Gated jobs still pause for your input/review; only non-gated stages auto-run.')}</label>
          <label style="font-size:.76rem;color:var(--muted);display:flex;gap:6px;align-items:center;">every
            <input type="number" value="${j.cron_interval || 30}" min="1" max="1440" style="width:64px;" onchange="ghCronInterval(${j.id}, this.value)"> min ${hlp('How often the scheduler pokes this job to take its next step (1-1440 minutes). Shorter = faster progress but more GPU turns.')}</label>
          <button class="btn-sm" onclick="ghRunJob(${j.id})" title="Kick the swarm to take this job's next step right now, without waiting for the schedule.">&#9654; Run now</button>
        </div>
      </div>`).join('')}</div>`
      : '<div class="empty"><div class="empty-icon">&#9200;</div>No jobs yet — propose one in Dev Swarm, then schedule it here.</div>'}`;
}
async function ghCronToggle(jid, on) {
  try { await api('/api/github/jobs/' + jid, { method: 'PATCH', body: JSON.stringify({ cron_enabled: on ? 1 : 0 }) }); toast(on ? 'Scheduled' : 'Unscheduled'); }
  catch (e) { toast('Failed: ' + e.message, 'error'); }
}
async function ghCronInterval(jid, v) {
  const n = Math.max(1, parseInt(v) || 30);
  try { await api('/api/github/jobs/' + jid, { method: 'PATCH', body: JSON.stringify({ cron_interval: n }) }); toast('Interval set to ' + n + ' min'); }
  catch (e) { toast('Failed: ' + e.message, 'error'); }
}
window.ghRenderCron = ghRenderCron; window.ghCronToggle = ghCronToggle; window.ghCronInterval = ghCronInterval;

/* ── Agent Watcher (background health monitor + diagnoses) ────────────────── */
const _WSEV = { high: '#f87171', warn: 'var(--warn)', info: 'var(--muted)' };

async function ghRenderWatcher() {
  const w = await api('/api/watcher');
  const s = w.settings;
  const on = (k) => s[k] === '1';
  const open = w.incidents.filter(i => i.status === 'open');
  const resolved = w.incidents.filter(i => i.status !== 'open').slice(0, 10);
  const tog = (key, label, help) => `
    <label style="font-size:.76rem;display:flex;gap:6px;align-items:center;cursor:pointer;">
      <input type="checkbox" ${on(key) ? 'checked' : ''} onchange="ghWatcherSet('${key}', this.checked ? '1' : '0')">
      ${label} ${hlp(help)}</label>`;
  const card = (i, dim) => `
    <div class="card" style="padding:12px;${dim ? 'opacity:.55;' : ''}">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
        <span style="font-weight:600;font-size:.84rem;${i.source === 'swarm' ? 'cursor:pointer;' : ''}"
              ${i.source === 'swarm' ? `onclick="ghOpenJob(${i.ref_id})"` : ''}>
          <span style="color:${_WSEV[i.severity] || 'var(--muted)'};">&#9679;</span>
          ${i.source === 'swarm' ? `job #${i.ref_id}` : esc(i.source === 'media' ? (i.ref_kind || 'media') + ' #' + i.ref_id : 'system task #' + i.ref_id)}
          — ${esc(i.title || '')}</span>
        <span style="font-size:.64rem;color:var(--muted);">${esc(i.status_seen)} · ${i.created_at ? new Date(i.created_at.replace(' ','T')+'Z').toLocaleString() : ''}</span>
      </div>
      <div style="font-size:.78rem;margin-top:6px;">${esc(i.summary || '')}</div>
      ${i.cause ? `<div style="font-size:.74rem;color:var(--muted);margin-top:4px;"><b>Likely cause:</b> ${esc(i.cause)}</div>` : ''}
      ${i.fix ? `<div style="font-size:.74rem;margin-top:4px;"><b>How to fix:</b> ${esc(i.fix)}</div>` : ''}
      ${i.llm_notes ? `<div style="font-size:.72rem;color:var(--muted);margin-top:4px;">&#129658; doctor: ${esc(i.llm_notes)}</div>` : ''}
      ${dim ? `<div style="font-size:.64rem;color:var(--muted);margin-top:4px;">closed: ${esc(i.action || 'resolved')}</div>` : `
      <div style="display:flex;gap:8px;margin-top:8px;">
        ${i.source === 'swarm' ? `<button class="btn-sm" onclick="ghWatcherRerun(${i.id})" title="Re-run the job now — the diagnosis above is fed to the agents as context.">&#9654; Re-run informed</button>` : ''}
        <button class="btn-sm" onclick="ghWatcherResolve(${i.id})">&#10003; Resolve</button>
      </div>`}
    </div>`;
  document.getElementById('gh-body').innerHTML = `
    <div style="color:var(--muted);font-size:.82rem;margin-bottom:12px;">
      The watcher runs in the background and checks every agent-run task — swarm jobs, gated
      system installs, and media/builder jobs — for failures, pauses, and silent stalls. Each
      problem gets a diagnosis (what happened, likely cause, how to fix) that is posted on the
      job's timeline, so agents <b>re-run informed instead of blind</b>. ${open.length} open.
    </div>
    <div class="card" style="padding:12px;margin-bottom:12px;">
      <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;">
        ${tog('agent_watcher_enabled', '&#129658; watcher on', 'Master switch for the background watcher. Off = no scanning, no diagnoses.')}
        ${tog('agent_watcher_llm', 'LLM doctor', 'After the fast rule-based diagnosis, also ask a local model to read the job timeline and write a deeper diagnosis. Uses a GPU turn per incident.')}
        ${tog('agent_watcher_notify', 'God Console notes', 'Post high-severity incidents to the God Console board.')}
        ${tog('agent_watcher_autoresume', 'auto re-run', 'When a pause is clearly resumable (e.g. interrupted by a restart), re-run the job automatically with the diagnosis as context. Off = you click Re-run yourself.')}
        <label style="font-size:.76rem;color:var(--muted);display:flex;gap:6px;align-items:center;">every
          <input type="number" value="${parseInt(s.agent_watcher_interval) || 5}" min="2" max="120" style="width:56px;"
                 onchange="ghWatcherSet('agent_watcher_interval', this.value)"> min ${hlp('How often the background check runs (2-120 minutes).')}</label>
        <label style="font-size:.76rem;color:var(--muted);display:flex;gap:6px;align-items:center;">stall after
          <input type="number" value="${parseInt(s.agent_watcher_stall_min) || 20}" min="5" max="240" style="width:56px;"
                 onchange="ghWatcherSet('agent_watcher_stall_min', this.value)"> min ${hlp('A running swarm job with no progress update for this long counts as stalled.')}</label>
        <button class="btn-sm" onclick="ghWatcherTick()" title="Run one watcher pass right now.">&#8635; Check now</button>
      </div>
    </div>
    ${open.length
      ? `<div style="display:flex;flex-direction:column;gap:10px;">${open.map(i => card(i, false)).join('')}</div>`
      : '<div class="empty"><div class="empty-icon">&#129658;</div>No open incidents — every agent task looks healthy.</div>'}
    ${resolved.length ? `<div style="color:var(--muted);font-size:.74rem;margin:14px 0 8px;">Recently resolved</div>
      <div style="display:flex;flex-direction:column;gap:10px;">${resolved.map(i => card(i, true)).join('')}</div>` : ''}`;
}
async function ghWatcherSet(key, val) {
  try { await api('/api/watcher/settings', { method: 'POST', body: JSON.stringify({ [key]: String(val) }) }); toast('Saved'); }
  catch (e) { toast('Failed: ' + e.message, 'error'); }
}
async function ghWatcherTick() {
  try { const r = await api('/api/watcher/tick', { method: 'POST' });
    toast(`Checked — ${r.new} new, ${r.open} open`); await ghRenderWatcher();
  } catch (e) { toast('Check failed: ' + e.message, 'error'); }
}
async function ghWatcherResolve(iid) {
  try { await api('/api/watcher/incidents/' + iid + '/resolve', { method: 'POST' }); toast('Resolved'); await ghRenderWatcher(); }
  catch (e) { toast('Failed: ' + e.message, 'error'); }
}
async function ghWatcherRerun(iid) {
  try { const r = await api('/api/watcher/incidents/' + iid + '/rerun', { method: 'POST' });
    toast('▶ Job #' + r.job_id + ' re-running with the diagnosis as context'); await ghRenderWatcher();
  } catch (e) { toast('Re-run failed: ' + e.message, 'error'); }
}
window.ghRenderWatcher = ghRenderWatcher; window.ghWatcherSet = ghWatcherSet;
window.ghWatcherTick = ghWatcherTick; window.ghWatcherResolve = ghWatcherResolve;
window.ghWatcherRerun = ghWatcherRerun;

async function ghRestartLive() {
  if (!confirm('Restart the live app now? It will be unavailable for a few seconds while it reloads.')) return;
  try { await api('/api/github/restart-live', { method: 'POST' });
    toast('Restarting… reload the page in ~10s');
  } catch (e) { toast('Restart failed: ' + e.message, 'error'); }
}
window.ghRestartLive = ghRestartLive;


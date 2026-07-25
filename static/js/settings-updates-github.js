/* ── UPDATES (GitHub) ── */
async function loadUpdates(fetch) {
  const el = document.getElementById('updates-slot');
  if (!el) return;
  let s;
  try { s = await api('/api/system/update-status' + (fetch ? '?fetch=true' : '')); }
  catch (e) { el.innerHTML = `<div style="font-size:.76rem;color:var(--warn);">${esc(e.message)}</div>`; return; }
  const chanOpts = (s.channels || []).map(c => `<option value="${esc(c)}" ${c === s.channel ? 'selected' : ''}>${esc(c)}</option>`).join('')
    + (s.channels.includes(s.channel) ? '' : `<option value="${esc(s.channel)}" selected>${esc(s.channel)} (current)</option>`);
  const behindTxt = !s.has_remote ? '<span style="color:var(--muted)">no git remote</span>'
    : (s.behind == null ? '<span style="color:var(--muted)">unknown — Check</span>'
      : (s.behind > 0 ? `<span style="color:var(--warn)">${s.behind} update(s) available</span>`
        : '<span style="color:var(--green)">&#10003; up to date</span>'));
  el.innerHTML = `
    <div style="font-size:.76rem;color:var(--muted);line-height:1.7;margin-bottom:8px;">
      Version: <b>${esc(s.branch)}</b> @ <code>${esc(s.commit)}</code><br>${esc(s.subject || '')}
      <br>Status: ${behindTxt}${s.dirty ? ' · <span style="color:var(--warn)">local changes present</span>' : ''}
      ${s.has_remote ? ` · updates from <b>${esc(s.remote || 'origin')}</b>` : ''}
    </div>
    <div class="field"><label>Update channel (branch) ${hlp('Which git branch this install pulls updates from. retail = stable/tested, master = latest features, dev = experimental (may break). Changing it + Update & restart switches your running code to that branch. Updates come from YOUR install’s own git remote (origin) using the GitHub account signed in below — nothing is hard-wired to a vendor repo.')}</label>
      <select id="upd-channel">${chanOpts}</select>
      <div style="font-size:.68rem;color:var(--muted);margin-top:3px;">retail = stable · master = latest · dev = experimental</div></div>
    <label style="font-size:.78rem;display:flex;gap:6px;align-items:center;cursor:pointer;margin:6px 0;">
      <input type="checkbox" id="upd-enabled" ${s.enabled ? 'checked' : ''}> Updates enabled (uncheck to pin this version) ${hlp('When unchecked, the “Update & restart” button is disabled so this install stays pinned to its current version — nothing auto-changes your code. Check it to allow pulling updates.')}</label>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px;">
      <button class="btn-sm" onclick="updSaveConfig()">&#128190; Save</button>
      <button class="btn-sm" onclick="loadUpdates(true)">&#128269; Check for updates</button>
      <button class="btn-sm primary" onclick="updApply()" ${(!s.enabled || !s.has_remote || s.behind === 0) ? 'disabled' : ''}>&#11015;&#65039; Update &amp; restart</button>
    </div>`;
}
async function updSaveConfig() {
  try {
    await api('/api/system/update-config', { method: 'POST', body: JSON.stringify({
      channel: document.getElementById('upd-channel').value,
      enabled: document.getElementById('upd-enabled').checked,
    })});
    toast('Update settings saved'); loadUpdates(true);
  } catch (e) { toast('Save failed: ' + e.message, 'error'); }
}
async function updApply() {
  if (!confirm('Update this install to the latest ' + document.getElementById('upd-channel').value + ' and restart?')) return;
  try {
    await api('/api/system/update-config', { method: 'POST', body: JSON.stringify({ channel: document.getElementById('upd-channel').value }) });
    const r = await api('/api/system/update-apply', { method: 'POST' });
    toast(r.message || 'Updating… reload in ~10s');
  } catch (e) { toast('Update failed: ' + e.message, 'error'); }
}
window.loadUpdates = loadUpdates; window.updSaveConfig = updSaveConfig; window.updApply = updApply;

/* ── GITHUB account controls moved to the 🐙 GitHub tab → Account & Sharing.
   (Sign-in, fork-this-install, and Add-collaborator now live there; the channel-
   based version Updater above stays in Settings.) This slot is now a pointer. ── */
async function loadGithubSettings() {
  const el = document.getElementById('github-slot');
  if (!el) return;
  let s = {};
  try { s = await api('/api/github/status'); } catch (e) { /* status best-effort */ }
  const status = s.authenticated
    ? `<span style="color:var(--green)">&#10003; signed in as <b>${esc(s.login || '?')}</b></span>`
    : `<span style="color:var(--warn)">not signed in</span>`;
  el.innerHTML = `
    <div style="font-size:.76rem;color:var(--muted);line-height:1.7;">
      GitHub sign-in, forking this install to your account, and inviting collaborators
      have moved to the <b>&#128025; GitHub tab &rarr; Account &amp; Sharing</b>. Status: ${status}
    </div>
    <button class="btn-sm" style="margin-top:8px;" onclick="switchView('github')">&#128025; Open the GitHub tab</button>`;
}
window.loadGithubSettings = loadGithubSettings;

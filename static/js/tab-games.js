'use strict';
/* ══ GAMES TAB ══
   Game-engine workbench (Godot / Unity / Unreal) driven by /api/games/*.
   Sub-tab panes use the same mechanism as Finance/Crypto/Settings: every pane
   stays in the DOM, we only toggle which one is visible, and each lazy-loads once.
     🕹️ Engines  — install state per engine; "not installed" is a normal state with
                    copy-paste install commands, NEVER an error
     📁 Projects — discover / create (Godot) / build, with live build output, plus
                    "Publish to shop": a two-step curate-then-push listing editor
                    (Save draft = local only → Push = a WooCommerce DRAFT product)
     🎨 Assets   — the store's sprite sheets, 3D models and packs → export to a project
     🔌 MCP      — informational only; nothing is installed or connected from here
     📚 Docs     — curated links + a persisted notes scratchpad

   EVERY pane degrades: node unreachable, engine missing, or a 404 (endpoint not
   live until the service restarts) all render an explanatory empty state. */

const _GAME_PANES = ['engines', 'projects', 'assets', 'mcp', 'docs'];
let _gamesLoaded = {};        // pane -> true once its loader has run
let _gamesData = {};          // cached payloads shared between panes
let _gameSel = new Set();     // selected asset ids (Assets pane)
let _gameBuildTimer = null;
let _gameDrafts = {};         // project path -> shop listing draft (Publish flow)
let _gamePub = null;          // the listing currently open in the publish editor
let _gamePubShots = null;     // cached node-screenshot probe for that project
let _gamePubTimer = null;     // poll timer for a queued cover render / description

async function renderGames(pane) {
  _gamesLoaded = {};
  _gamesData = {};
  _gameSel = new Set();
  _gameDrafts = {};
  gamesPublishClose();
  if (_gameBuildTimer) { clearInterval(_gameBuildTimer); _gameBuildTimer = null; }
  document.getElementById('main-content').innerHTML = `
    <div class="view-header">
      <div class="view-title">&#127918; Games</div>
      <div class="view-sub">Game-engine workbench &mdash; engine install state, projects and headless
        builds on the GPU node, and a bridge that hands the store's generated sprites and 3D models
        straight to an engine.</div>
    </div>
    <div class="subtab-bar" id="games-subtabs">
      <div class="subtab" onclick="gamesSub('engines')">&#128377;&#65039; Engines</div>
      <div class="subtab" onclick="gamesSub('projects')">&#128193; Projects</div>
      <div class="subtab" onclick="gamesSub('assets')">&#127912; Assets</div>
      <div class="subtab" onclick="gamesSub('mcp')">&#128279; Editor MCP</div>
      <div class="subtab" onclick="gamesSub('docs')">&#128218; Docs</div>
    </div>
    ${_GAME_PANES.map(k => `<div class="settings-tabpane" id="games-pane-${k}" style="display:none;">
      <div class="empty"><div class="empty-icon">&#9203;</div>Loading&#8230;</div>
    </div>`).join('')}`;
  gamesSub(_GAME_PANES.includes(pane) ? pane : 'engines');
}
window.renderGames = renderGames;

function gamesSub(k) {
  _GAME_PANES.forEach(name => {
    const p = document.getElementById('games-pane-' + name);
    if (p) p.style.display = (name === k) ? '' : 'none';
  });
  document.querySelectorAll('#games-subtabs .subtab').forEach((el, i) => {
    el.classList.toggle('active', _GAME_PANES[i] === k);
  });
  if (!_gamesLoaded[k]) {
    _gamesLoaded[k] = true;
    ({ engines: gamesLoadEngines, projects: gamesLoadProjects, assets: gamesLoadAssets,
       mcp: gamesLoadMcp, docs: gamesLoadDocs }[k])();
  }
}
window.gamesSub = gamesSub;

/* ── shared bits ─────────────────────────────────────────────────────────── */

const _gPanel = (inner, pad = '16px') =>
  `<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;
    padding:${pad};margin-bottom:14px;">${inner}</div>`;

const _gPre = txt => `<pre style="white-space:pre-wrap;word-break:break-word;font-size:.7rem;
  line-height:1.5;background:var(--bg);border:1px solid var(--border);border-radius:8px;
  padding:10px 12px;margin:8px 0 0;color:var(--text);max-height:320px;overflow:auto;">${esc(txt)}</pre>`;

/* Every loader funnels failures through here, so a 404 (endpoint not live until the
   service restarts) or a dead node explains itself instead of breaking the tab. */
function _gError(err, what) {
  const msg = String((err && err.message) || err || '');
  const pending = /HTTP 404|Not Found/i.test(msg);
  return `<div class="empty">
    <div class="empty-icon">${pending ? '&#128260;' : '&#9888;&#65039;'}</div>
    <div style="font-weight:600;margin-bottom:4px;">${pending
      ? 'This pane needs a service restart'
      : `Could not load ${esc(what)}`}</div>
    <div style="font-size:.75rem;color:var(--muted);max-width:460px;margin:0 auto;">${pending
      ? 'The Games API is new — it becomes available the next time the store service restarts. Nothing is broken.'
      : esc(msg)}</div>
  </div>`;
}

function _gUnreachable(node) {
  return `<div class="empty">
    <div class="empty-icon">&#128268;</div>
    <div style="font-weight:600;margin-bottom:4px;">Can't reach the GPU node</div>
    <div style="font-size:.75rem;color:var(--muted);max-width:460px;margin:0 auto;">
      ${esc(node || 'The node')} isn't answering over SSH. Engine and project info come from
      there &mdash; the Assets and Docs panes still work offline.</div>
  </div>`;
}

function gamesCopy(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const txt = el.textContent || '';
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(() => toast('Copied'), () => toast('Copy failed', 'error'));
  } else {
    toast('Select the text to copy it', 'error');
  }
}
window.gamesCopy = gamesCopy;

/* ── ENGINES ─────────────────────────────────────────────────────────────── */

async function gamesLoadEngines(refresh) {
  const el = document.getElementById('games-pane-engines');
  if (!el) return;
  if (refresh) el.innerHTML = '<div class="empty"><div class="empty-icon">&#9203;</div>Re-checking&#8230;</div>';
  let d;
  try {
    d = await api('/api/games/engines' + (refresh ? '?refresh=1' : ''));
  } catch (e) {
    el.innerHTML = _gError(e, 'engine status');
    return;
  }
  _gamesData.engines = d;
  const engines = d.engines || [];
  if (!engines.length) { el.innerHTML = _gUnreachable(d.node); return; }

  const head = `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px;">
    <div style="font-size:.75rem;color:var(--muted);">
      Node <b style="color:var(--text);">${esc(d.node || '—')}</b>
      ${d.disk_free_gb != null ? ` &middot; ${d.disk_free_gb} GB free` : ''}
      ${d.reachable === false ? ' &middot; <span style="color:var(--red);">unreachable</span>' : ''}
    </div>
    <button class="btn-sm" onclick="gamesLoadEngines(1)">&#8635; Re-check</button>
  </div>`;

  const cards = engines.map(e => {
    const ok = !!e.installed;
    return _gPanel(`
      <div style="display:flex;align-items:flex-start;gap:12px;flex-wrap:wrap;">
        <div style="font-size:1.6rem;line-height:1;">${e.icon || '&#127918;'}</div>
        <div style="flex:1;min-width:220px;">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <span style="font-weight:600;font-size:.95rem;">${esc(e.label || e.key)}</span>
            <span style="font-size:.66rem;padding:2px 8px;border-radius:20px;
              background:${ok ? 'var(--green)' : 'var(--surface2)'};
              color:${ok ? '#fff' : 'var(--muted)'};border:1px solid ${ok ? 'var(--green)' : 'var(--border)'};">
              ${ok ? 'installed' : 'not installed'}</span>
            ${e.version ? `<span style="font-size:.7rem;color:var(--accent2);">v${esc(e.version)}</span>` : ''}
            ${(e.versions || []).length > 1
              ? `<span title="${esc((e.versions || []).join(', '))}"
                   style="font-size:.62rem;padding:1px 7px;border-radius:20px;border:1px solid var(--border);
                   color:var(--muted);">+${(e.versions || []).length - 1} more</span>` : ''}
          </div>
          <div style="font-size:.72rem;color:var(--muted);margin-top:4px;">${esc(e.note || '')}</div>
          ${ok ? `<div style="font-size:.68rem;color:var(--muted);margin-top:6px;">
                    <code>${esc(e.path || '')}</code></div>` : ''}
        </div>
        <a class="btn-sm" href="${esc(e.docs || '#')}" target="_blank" rel="noopener">Docs &#8599;</a>
      </div>
      ${ok ? '' : `
        <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:2px;">
            <span style="font-size:.75rem;font-weight:600;">How to install it</span>
            ${hlp('Run these on the GPU node. The store deliberately does not run them for you — '
                + 'Unity and Unreal need vendor accounts and root, so they must be a human decision.')}
            <button class="btn-sm" style="margin-left:auto;"
              onclick="gamesCopy('ghint-${esc(e.key)}')">Copy</button>
          </div>
          <pre id="ghint-${esc(e.key)}" style="white-space:pre-wrap;word-break:break-word;
            font-size:.68rem;line-height:1.55;background:var(--bg);border:1px solid var(--border);
            border-radius:8px;padding:10px 12px;margin:6px 0 0;color:var(--text);">${esc(e.install_hint || '')}</pre>
          ${e.key === 'unreal' && d.disk_free_gb != null ? `
            <div style="font-size:.7rem;color:var(--warn);margin-top:8px;">
              &#9888;&#65039; Unreal wants roughly 100&ndash;110 GB extracted and the node reports
              ${d.disk_free_gb} GB free in total &mdash; installing it there would leave almost
              nothing for the LLM and image models. Use an external drive or wait for space.</div>` : ''}
        </div>`}
    `);
  }).join('');
  el.innerHTML = head + cards;
}
window.gamesLoadEngines = gamesLoadEngines;

/* ── PROJECTS ────────────────────────────────────────────────────────────── */

async function gamesLoadProjects(refresh) {
  const el = document.getElementById('games-pane-projects');
  if (!el) return;
  let d;
  try {
    d = await api('/api/games/projects' + (refresh ? '?refresh=1' : ''));
  } catch (e) {
    el.innerHTML = _gError(e, 'projects');
    return;
  }
  _gamesData.projects = d;
  // Engine state drives whether "create" is offered at all; failure is not fatal.
  if (!_gamesData.engines) {
    try { _gamesData.engines = await api('/api/games/engines'); } catch (e) { _gamesData.engines = null; }
  }
  const godotOk = ((_gamesData.engines && _gamesData.engines.engines) || [])
    .some(e => e.key === 'godot' && e.installed);
  // Shop listing drafts, so each project can show its push state. Tolerant: the
  // publish API is newer than the tab, and a 404 must not break the project list.
  await gamesLoadDrafts();

  if (d.reachable === false) {
    el.innerHTML = _gUnreachable(d.node);
    return;
  }

  const createCard = _gPanel(`
    <div style="font-weight:600;font-size:.85rem;margin-bottom:8px;">&#10133; New Godot project
      ${hlp('Writes a minimal project.godot plus a starter scene into the project root on the node. '
          + 'Only Godot can be scaffolded from here — Unity and Unreal are not installed.')}</div>
    ${godotOk ? `
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
        <input id="games-new-name" placeholder="Project name" maxlength="48"
          style="flex:1;min-width:200px;padding:7px 10px;background:var(--bg);border:1px solid var(--border);
          border-radius:8px;color:var(--text);font-size:.78rem;">
        <button class="btn-sm primary" onclick="gamesCreateProject()">Create</button>
      </div>
      <div style="font-size:.68rem;color:var(--muted);margin-top:6px;">
        Lands in <code>${esc(d.root || '~/games')}</code> on ${esc(d.node || 'the node')}.</div>`
    : `<div style="font-size:.74rem;color:var(--muted);">
        Godot isn't installed on ${esc(d.node || 'the node')}, so projects can't be created yet.
        The <a href="#" onclick="gamesSub('engines');return false;"
        style="color:var(--accent);">Engines pane</a> has the install commands.</div>`}
  `);

  const list = (d.projects || []).length
    ? (d.projects || []).map(p => {
        const icon = { godot: '&#128998;', unity: '&#11035;', unreal: '&#128999;' }[p.engine] || '&#127918;';
        const when = p.modified ? new Date(p.modified * 1000).toLocaleString() : '—';
        const buildable = p.engine === 'godot' && godotOk;
        const dr = _gameDrafts[p.path];
        return _gPanel(`
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
            <span style="font-size:1.2rem;">${icon}</span>
            <div style="flex:1;min-width:200px;">
              <div style="font-weight:600;font-size:.85rem;">${esc(p.name)}
                ${_gPushChip(dr)}</div>
              <div style="font-size:.68rem;color:var(--muted);">
                ${esc(p.engine)} &middot; <code>${esc(p.path)}</code> &middot; modified ${esc(when)}</div>
            </div>
            ${buildable
              ? `<button class="btn-sm primary" onclick="gamesBuild('${esc(p.path)}')">&#128296; Build</button>`
              : `<span style="font-size:.68rem;color:var(--muted);">${p.engine === 'godot'
                  ? 'engine missing' : 'builds are Godot-only for now'}</span>`}
            <button class="btn-sm" onclick="gamesSub('assets')">&#127912; Assets</button>
            ${p.engine === 'unity' ? `<button class="btn-sm"
              title="Adds the MCP for Unity git package to this project's Packages/manifest.json. Takes a timestamped backup first and is reversible."
              onclick="gamesUnityMcp('${esc(p.path)}','install')">&#128279; Add MCP</button>` : ''}
            <button class="btn-sm" onclick="gamesPublishOpen('${esc(p.path)}','${esc(p.name)}','${esc(p.engine)}')"
              >&#128717;&#65039; ${dr ? 'Edit listing' : 'Publish to shop'}</button>
          </div>
          ${dr && dr.wp_id ? `<div style="font-size:.68rem;color:var(--muted);margin-top:8px;
            padding-top:8px;border-top:1px solid var(--border);">
            Draft product #${dr.wp_id} exists in the shop &mdash; still unpublished.
            ${dr.wp_admin_url ? `<a href="${esc(dr.wp_admin_url)}" target="_blank" rel="noopener"
              style="color:var(--accent);">Open in shop admin &#8599;</a>` : ''}
            ${dr.needs_update ? ' &middot; <span style="color:var(--warn);">local edits not pushed yet</span>' : ''}
          </div>` : ''}`);
      }).join('')
    : `<div class="empty"><div class="empty-icon">&#128193;</div>
        <div style="font-weight:600;margin-bottom:4px;">No projects yet</div>
        <div style="font-size:.75rem;color:var(--muted);">
          ${d.root_exists === false
            ? `<code>${esc(d.root || '~/games')}</code> doesn't exist on ${esc(d.node || 'the node')} yet —
               creating a project makes it.`
            : `Nothing found under <code>${esc(d.root || '~/games')}</code>.`}</div></div>`;

  el.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap;">
      <div style="font-size:.75rem;color:var(--muted);">Scanning
        <code>${esc(d.root || '~/games')}</code> on <b style="color:var(--text);">${esc(d.node || '—')}</b></div>
      <button class="btn-sm" onclick="gamesLoadProjects(1)">&#8635; Rescan</button>
    </div>
    ${createCard}
    <div id="games-build-out"></div>
    ${list}`;
}
window.gamesLoadProjects = gamesLoadProjects;

async function gamesCreateProject() {
  const inp = document.getElementById('games-new-name');
  const name = (inp && inp.value || '').trim();
  if (!name) { toast('Give the project a name', 'error'); return; }
  try {
    const r = await api('/api/games/projects', {
      method: 'POST', body: JSON.stringify({ engine: 'godot', name })
    });
    toast(`Created ${r.name}`);
    if (inp) inp.value = '';
    gamesLoadProjects(1);
  } catch (e) {
    toast(String(e.message || e), 'error');
  }
}
window.gamesCreateProject = gamesCreateProject;

async function gamesBuild(path) {
  const box = document.getElementById('games-build-out');
  try {
    const r = await api('/api/games/build', {
      method: 'POST', body: JSON.stringify({ path, preset: 'Linux/X11' })
    });
    toast('Build queued');
    if (box) box.innerHTML = _gPanel(
      `<div style="font-weight:600;font-size:.85rem;">&#128296; Build &mdash; ${esc(path)}</div>
       <div id="games-build-status" style="font-size:.72rem;color:var(--muted);margin-top:4px;">
         queued (task #${r.task_id}) &mdash; running on the unified queue&#8230;</div>
       <div id="games-build-log"></div>`);
    gamesPollBuild(r.task_id);
  } catch (e) {
    if (box) box.innerHTML = _gPanel(
      `<div style="font-size:.78rem;color:var(--red);">&#9888;&#65039; ${esc(String(e.message || e))}</div>`);
    else toast(String(e.message || e), 'error');
  }
}
window.gamesBuild = gamesBuild;

function gamesPollBuild(tid) {
  if (_gameBuildTimer) clearInterval(_gameBuildTimer);
  let ticks = 0;
  _gameBuildTimer = setInterval(async () => {
    ticks++;
    let s;
    try {
      s = await api('/api/games/build/' + tid);
    } catch (e) {
      clearInterval(_gameBuildTimer); _gameBuildTimer = null;
      return;
    }
    const st = document.getElementById('games-build-status');
    const lg = document.getElementById('games-build-log');
    if (!st) { clearInterval(_gameBuildTimer); _gameBuildTimer = null; return; }
    const status = s.status || s.queue_status || 'unknown';
    const colour = status === 'done' ? 'var(--green)' : status === 'failed' ? 'var(--red)' : 'var(--muted)';
    st.innerHTML = `<span style="color:${colour};">${esc(status)}</span> &mdash; task #${tid}
      ${s.artifact ? ` &middot; <code>${esc(s.artifact)}</code>` : ''}`;
    if (lg && s.output) lg.innerHTML = _gPre(s.output);
    if (status === 'done' || status === 'failed' || ticks > 300) {
      clearInterval(_gameBuildTimer); _gameBuildTimer = null;
    }
  }, 2000);
}

/* ── ASSETS ──────────────────────────────────────────────────────────────── */

async function gamesLoadAssets() {
  const el = document.getElementById('games-pane-assets');
  if (!el) return;
  let d;
  try {
    d = await api('/api/games/assets');
  } catch (e) {
    el.innerHTML = _gError(e, 'assets');
    return;
  }
  _gamesData.assets = d;
  if (!_gamesData.projects) {
    try { _gamesData.projects = await api('/api/games/projects'); } catch (e) { _gamesData.projects = null; }
  }
  const projects = (_gamesData.projects && _gamesData.projects.projects) || [];
  const c = d.counts || {};

  const sprites = (d.sprites || []).map((s, i) => `
    <label style="display:flex;align-items:center;gap:8px;padding:8px;border:1px solid var(--border);
      border-radius:10px;background:var(--bg);cursor:pointer;">
      <input type="checkbox" data-gkind="sprite" data-gidx="${i}" onchange="gamesToggleSel(this)">
      <img src="${esc(s.url)}" alt="" loading="lazy" style="width:44px;height:44px;object-fit:contain;
        image-rendering:pixelated;background:var(--surface2);border-radius:6px;">
      <div style="min-width:0;">
        <div style="font-size:.74rem;font-weight:600;overflow:hidden;text-overflow:ellipsis;
          white-space:nowrap;">${esc(s.name)}</div>
        <div style="font-size:.64rem;color:var(--muted);">${s.frames || '?'} frames &middot;
          ${s.fw || '?'}&times;${s.fh || '?'} &middot; ${esc(s.source || '')}</div>
      </div>
    </label>`).join('');

  const models = (d.models || []).map((m, i) => `
    <label style="display:flex;align-items:center;gap:8px;padding:8px;border:1px solid var(--border);
      border-radius:10px;background:var(--bg);cursor:pointer;">
      <input type="checkbox" data-gkind="model3d" data-gidx="${i}" onchange="gamesToggleSel(this)">
      <span style="font-size:1.1rem;">&#128737;&#65039;</span>
      <div style="min-width:0;">
        <div style="font-size:.74rem;font-weight:600;overflow:hidden;text-overflow:ellipsis;
          white-space:nowrap;">${esc(m.name)}</div>
        <div style="font-size:.64rem;color:var(--muted);">${esc(m.folder || '')} &middot; ${m.size_kb} KB</div>
      </div>
    </label>`).join('');

  const packs = (d.packs || []).map(p => `
    <div style="padding:10px;border:1px solid var(--border);border-radius:10px;background:var(--bg);">
      <div style="font-size:.76rem;font-weight:600;">${esc(p.name || '')}</div>
      <div style="font-size:.64rem;color:var(--muted);margin-top:3px;">
        ${p.png_count || 0} PNGs &middot; ${esc(p.license || 'unknown licence')}
        ${p.commercial ? ' &middot; <span style="color:var(--green);">commercial ok</span>' : ''}</div>
      <div style="font-size:.64rem;color:var(--muted);margin-top:3px;">${esc((p.theme || []).join(', '))}</div>
      ${p.source ? `<a href="${esc(p.source)}" target="_blank" rel="noopener"
        style="font-size:.64rem;color:var(--accent);">source &#8599;</a>` : ''}
    </div>`).join('');

  const grid = inner => `<div style="display:grid;gap:8px;
    grid-template-columns:repeat(auto-fill,minmax(230px,1fr));margin-top:8px;">${inner}</div>`;
  const sect = (title, help, body, empty) => _gPanel(`
    <div style="font-weight:600;font-size:.85rem;">${title} ${hlp(help)}</div>
    ${body ? grid(body) : `<div style="font-size:.72rem;color:var(--muted);margin-top:6px;">${empty}</div>`}`);

  el.innerHTML = `
    ${_gPanel(`
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;">
        <div style="font-size:.78rem;font-weight:600;">Export to a project ${hlp(
          'Copies the selected files into <project>/assets/ on the node. Sprite sheets get a sidecar '
          + '.json with frame size and frame count so the engine can slice them. Purely additive — '
          + 'nothing is ever deleted or moved.')}</div>
        <select id="games-export-target" style="padding:6px 9px;background:var(--bg);
          border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:.75rem;">
          ${projects.length
            ? projects.map(p => `<option value="${esc(p.path)}">${esc(p.name)} (${esc(p.engine)})</option>`).join('')
            : '<option value="">no projects found</option>'}
        </select>
        <span id="games-sel-count" style="font-size:.72rem;color:var(--muted);">0 selected</span>
        <button class="btn-sm primary" onclick="gamesExportAssets()">&#8681; Export</button>
      </div>
      ${projects.length ? '' : `<div style="font-size:.7rem;color:var(--muted);margin-top:6px;">
        Create a project in the <a href="#" onclick="gamesSub('projects');return false;"
        style="color:var(--accent);">Projects pane</a> first &mdash; there's nowhere to export to yet.</div>`}`)}
    ${sect(`&#129497; Sprite sheets <span style="color:var(--muted);font-weight:400;">(${c.sprites || 0})</span>`,
           'Generated per-entity animation sheets from the world sprite registry.',
           sprites, 'No generated sprite sheets yet — the world sprite pipeline fills this in.')}
    ${sect(`&#128736;&#65039; 3D models <span style="color:var(--muted);font-weight:400;">(${c.models || 0})</span>`,
           'STL/OBJ/GLB from the store\'s 3D pipeline (models3d/). Exported as-is.',
           models, 'No 3D models found in models3d/.')}
    ${sect(`&#128230; Asset packs <span style="color:var(--muted);font-weight:400;">(${c.packs || 0})</span>`,
           'Downloaded pixel-art packs. Listed for reference and licence — copy what you need '
           + 'straight from static/world_assets/packs/.',
           packs, 'No packs indexed.')}`;
  gamesUpdateSelCount();
}
window.gamesLoadAssets = gamesLoadAssets;

function gamesToggleSel(cb) {
  const key = cb.dataset.gkind + ':' + cb.dataset.gidx;
  if (cb.checked) _gameSel.add(key); else _gameSel.delete(key);
  gamesUpdateSelCount();
}
window.gamesToggleSel = gamesToggleSel;

function gamesUpdateSelCount() {
  const el = document.getElementById('games-sel-count');
  if (el) el.textContent = `${_gameSel.size} selected`;
}

async function gamesExportAssets() {
  const sel = document.getElementById('games-export-target');
  const project = sel && sel.value;
  if (!project) { toast('No project to export into', 'error'); return; }
  if (!_gameSel.size) { toast('Select some assets first', 'error'); return; }
  const d = _gamesData.assets || {};
  const assets = [];
  _gameSel.forEach(k => {
    const [kind, idx] = k.split(':');
    const src = kind === 'sprite' ? (d.sprites || []) : (d.models || []);
    const a = src[Number(idx)];
    if (a) assets.push(a);
  });
  try {
    const r = await api('/api/games/assets/export', {
      method: 'POST', body: JSON.stringify({ project, assets })
    });
    toast(`Exported ${(r.exported || []).length} asset(s)`);
    if ((r.skipped || []).length) {
      toast(`${r.skipped.length} skipped`, 'error');
    }
  } catch (e) {
    toast(String(e.message || e), 'error');
  }
}
window.gamesExportAssets = gamesExportAssets;

/* ── EDITOR MCP (informational) ──────────────────────────────────────────── */

async function gamesLoadMcp() {
  const el = document.getElementById('games-pane-mcp');
  if (!el) return;
  let d;
  try {
    d = await api('/api/games/mcp');
  } catch (e) {
    el.innerHTML = _gError(e, 'MCP options');
    return;
  }
  const opts = (d.options || []).map(o => {
    const colour = o.configured ? 'var(--green)' : o.detected_locally ? 'var(--warn)' : 'var(--muted)';
    return _gPanel(`
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
        <span style="font-weight:600;font-size:.86rem;">${esc(o.label)}</span>
        <span style="font-size:.64rem;padding:2px 8px;border-radius:20px;border:1px solid ${colour};
          color:${colour};">${esc(o.status || '')}</span>
        ${o.engine_installed ? '' : `<span style="font-size:.64rem;color:var(--muted);">
          (${esc(o.engine)} not installed)</span>`}
        <a class="btn-sm" style="margin-left:auto;" href="${esc(o.docs || '#')}"
          target="_blank" rel="noopener">Docs &#8599;</a>
      </div>
      <div style="font-size:.72rem;color:var(--muted);margin-top:6px;">${esc(o.what || '')}</div>
      ${o.install ? `<div style="font-size:.66rem;color:var(--muted);margin-top:6px;">
        <code>${esc(o.install)}</code></div>` : ''}`);
  }).join('');
  // Live bridge state for the configured Unreal project (never fatal — a bridge
  // that isn't up is a normal state, not an error).
  let br = null;
  try { br = await api('/api/games/mcp/bridge'); } catch (e) { br = { error: String(e.message || e) }; }
  _gamesData.mcp = d;

  const g = d.guard || {};
  const coop = g.editor_mode ? (g.editor_active
    ? { c: 'var(--green)', t: 'co-op ACTIVE — editor up, queue still running' }
    : { c: 'var(--muted)', t: 'co-op armed — no editor detected' })
    : { c: 'var(--warn)', t: 'co-op OFF — an editor will PAUSE the queue' };

  const bs = br && br.running
    ? { c: 'var(--green)', t: `connected on port ${br.port}` }
    : { c: br && br.port ? 'var(--warn)' : 'var(--muted)',
        t: (br && br.error) ? br.error : 'not running' };

  el.innerHTML = `
    ${_gPanel(`
      <div style="font-weight:600;font-size:.85rem;margin-bottom:6px;">&#128279; Live editor bridge</div>
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px;">
        <span style="font-size:.64rem;padding:2px 8px;border-radius:20px;border:1px solid ${bs.c};
          color:${bs.c};">${esc(bs.t)}</span>
        <span style="font-size:.64rem;padding:2px 8px;border-radius:20px;border:1px solid ${coop.c};
          color:${coop.c};">${esc(coop.t)}</span>
        <button class="btn-sm" style="margin-left:auto;" onclick="gamesMcpTest()">Test connection</button>
      </div>
      <div style="font-size:.72rem;color:var(--muted);line-height:1.6;">
        The bridge port is <b>derived per project</b> and published to
        <code>Saved/UE_MCP_Bridge/port.json</code> &mdash; it is not the 9877 the ue-mcp
        client assumes, so the Store reads that file instead. Calls placed here ride the
        <b>unified queue</b>, so editor work serialises with everything else rather than
        racing it for the GPU.
      </div>
      <div style="margin-top:10px;display:grid;grid-template-columns:110px 1fr;gap:6px 10px;align-items:center;">
        <label style="font-size:.72rem;color:var(--muted);">Project</label>
        <input id="mcp-project" value="${esc(d.mcp_project || '')}"
          placeholder="/mnt/projects/unreal/Sandbox"
          style="font-size:.72rem;padding:4px 7px;width:100%;box-sizing:border-box;">
        <label style="font-size:.72rem;color:var(--muted);">Model</label>
        <input id="mcp-model" value="${esc(d.mcp_model || '')}"
          placeholder="(blank = orchestrator default)"
          style="font-size:.72rem;padding:4px 7px;width:100%;box-sizing:border-box;">
        <span></span>
        <div style="font-size:.66rem;color:var(--muted);line-height:1.5;">
          Used only when a call needs inference; a plain editor command loads no model at all.
          Pick something that fits <b>beside</b> the editor &mdash; the 12B keep-warm default
          does not, next to Unreal, on a 12&nbsp;GB card. A <code>-cpu</code> build uses no VRAM.
          <button class="btn-sm" style="margin-left:6px;" onclick="gamesMcpSave()">Save</button>
        </div>
      </div>
      <div id="mcp-test-out" style="font-size:.7rem;color:var(--muted);margin-top:8px;"></div>`)}
    ${_gPanel(`
      <div style="font-weight:600;font-size:.85rem;margin-bottom:6px;">&#128161; What this pane is</div>
      <div style="font-size:.74rem;color:var(--muted);line-height:1.6;">
        An <b>editor MCP</b> lets an AI agent drive a running game editor directly &mdash; open scenes,
        move nodes, run the project, read the error console. The catalog below never installs,
        launches or connects anything &mdash; it reports what exists so you can opt in.
        ${d.store_mcp ? `<br><br>${esc(d.store_mcp)}` : ''}</div>`)}
    ${opts || `<div class="empty"><div class="empty-icon">&#128268;</div>
      <div style="font-size:.75rem;color:var(--muted);">No MCP options to report.</div></div>`}`;
}
window.gamesLoadMcp = gamesLoadMcp;

/* Add / remove the Unity MCP package in ONE project. This rewrites that project's
   Packages/manifest.json, so it is confirm-gated and never implicit — a backup is
   taken on the node before every write and 'remove' undoes it. */
async function gamesUnityMcp(path, action) {
  const verb = action === 'remove' ? 'Remove' : 'Add';
  if (!confirm(`${verb} the MCP for Unity package ${action === 'remove' ? 'from' : 'to'}:\n\n${path}\n\n`
      + `This edits the project's Packages/manifest.json (a timestamped backup is taken first).`)) return;
  try {
    const r = await api('/api/games/mcp/unity', {
      method: 'POST', body: JSON.stringify({ project: path, action }) });
    toast?.(r.changed ? `${verb}d — ${r.next || ''}` : (r.note || 'no change needed'));
    gamesLoadProjects(true);
  } catch (e) {
    toast?.(`Failed: ${String(e.message || e)}`, true);
  }
}
window.gamesUnityMcp = gamesUnityMcp;

async function gamesMcpSave() {
  const model = (document.getElementById('mcp-model') || {}).value || '';
  const project = (document.getElementById('mcp-project') || {}).value || '';
  try {
    const r = await api('/api/games/mcp/settings', {
      method: 'POST', body: JSON.stringify({ model: model.trim(), project: project.trim() }) });
    toast?.(`Saved — model: ${r.model_effective || 'default'}`);
    gamesLoadMcp();
  } catch (e) { toast?.(`Save failed: ${String(e.message || e)}`, true); }
}
window.gamesMcpSave = gamesMcpSave;

/* Queue-routed smoke test: a read-only editor command with NO model, so it proves
   the bridge answers without loading anything onto the GPU. */
async function gamesMcpTest() {
  const out = document.getElementById('mcp-test-out');
  if (out) out.textContent = 'Queued…';
  try {
    const r = await api('/api/games/mcp/call', {
      method: 'POST', body: JSON.stringify({ method: 'get_current_level' }) });
    if (!r.task_id) { if (out) out.textContent = 'No task id returned.'; return; }
    for (let i = 0; i < 20; i++) {
      await new Promise(res => setTimeout(res, 1000));
      let s;
      try { s = await api(`/api/games/build/${r.task_id}`); } catch (e) { continue; }
      if (s.status === 'done' || s.status === 'failed') {
        if (out) out.textContent = (s.status === 'done' ? '✅ ' : '⚠️ ') + String(s.output || '').slice(0, 400);
        gamesLoadMcp();
        return;
      }
      if (out) out.textContent = `Running… (${s.status || 'queued'})`;
    }
    if (out) out.textContent = 'Timed out waiting for the queue.';
  } catch (e) {
    if (out) out.textContent = `Failed: ${String(e.message || e)}`;
  }
}
window.gamesMcpTest = gamesMcpTest;

/* ── DOCS + NOTES ────────────────────────────────────────────────────────── */

async function gamesLoadDocs() {
  const el = document.getElementById('games-pane-docs');
  if (!el) return;
  let d;
  try {
    d = await api('/api/games/notes');
  } catch (e) {
    el.innerHTML = _gError(e, 'docs');
    return;
  }
  const byEngine = {};
  (d.docs || []).forEach(x => { (byEngine[x.engine] = byEngine[x.engine] || []).push(x); });
  const label = { godot: '&#128998; Godot', unity: '&#11035; Unity', unreal: '&#128999; Unreal' };
  const links = Object.keys(byEngine).map(k => _gPanel(`
    <div style="font-weight:600;font-size:.84rem;margin-bottom:6px;">${label[k] || esc(k)}</div>
    <div style="display:flex;flex-direction:column;gap:5px;">
      ${byEngine[k].map(x => `<a href="${esc(x.url)}" target="_blank" rel="noopener"
        style="font-size:.75rem;color:var(--accent);text-decoration:none;">&#8599; ${esc(x.label)}</a>`).join('')}
    </div>`)).join('');

  el.innerHTML = `
    ${links}
    ${_gPanel(`
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px;">
        <span style="font-weight:600;font-size:.84rem;">&#128221; Notes ${hlp(
          'A scratchpad for engine notes — export presets, gotchas, what you were mid-way through. '
          + 'Saved in settings, so it survives restarts.')}</span>
        <button class="btn-sm primary" style="margin-left:auto;" onclick="gamesSaveNotes()">Save</button>
      </div>
      <textarea id="games-notes" rows="10" placeholder="Engine notes, build gotchas, TODOs&#8230;"
        style="width:100%;padding:10px;background:var(--bg);border:1px solid var(--border);
        border-radius:8px;color:var(--text);font-size:.76rem;line-height:1.6;resize:vertical;"
        >${esc(d.notes || '')}</textarea>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px;">
        <span style="font-size:.74rem;color:var(--muted);">Project root ${hlp(
          'Where the Projects pane scans for game projects on the node.')}</span>
        <input id="games-root" value="${esc(d.project_root || '~/games')}"
          style="flex:1;min-width:180px;padding:6px 9px;background:var(--bg);border:1px solid var(--border);
          border-radius:8px;color:var(--text);font-size:.75rem;">
      </div>`)}`;
}
window.gamesLoadDocs = gamesLoadDocs;

async function gamesSaveNotes() {
  const ta = document.getElementById('games-notes');
  const rt = document.getElementById('games-root');
  try {
    await api('/api/games/notes', {
      method: 'POST',
      body: JSON.stringify({ notes: (ta && ta.value) || '', project_root: (rt && rt.value || '').trim() })
    });
    toast('Saved');
    _gamesLoaded.projects = false;   // rescan with the new root next time
  } catch (e) {
    toast(String(e.message || e), 'error');
  }
}
window.gamesSaveNotes = gamesSaveNotes;

/* ══ PUBLISH A TITLE TO THE SHOP ══════════════════════════════════════════════
   Curate-then-push, exactly like the Portal tab: everything is edited LOCALLY as a
   listing draft, and the only outbound step creates a WooCommerce **draft** product
   that the owner still has to publish by hand in the shop admin.

   Nothing in this flow lists projects anywhere. A project appears in the shop only
   because the owner opened this editor on it and pressed both buttons.

   Two-step, always in this order:
     1. Save draft  → local only, safe, reversible
     2. Push to shop → creates/updates the Woo DRAFT product
   ─────────────────────────────────────────────────────────────────────────── */

const _gPubNote = 'Pushing creates a DRAFT product in WooCommerce. It is not visible to '
  + 'anyone until you open the shop admin and publish it yourself. Your projects are '
  + 'never listed publicly by the store.';

async function gamesLoadDrafts() {
  _gameDrafts = {};
  try {
    const d = await api('/api/games/publish/drafts');
    (d.drafts || []).forEach(x => { _gameDrafts[x.project_path] = x; });
    _gamesData.publish = d;
  } catch (e) {
    _gamesData.publish = null;      // publish API not live yet — projects still render
  }
}

/* Per-project push state: never pushed / draft in shop / needs update. */
function _gPushChip(dr) {
  if (!dr) return '';
  const s = dr.wp_id
    ? (dr.needs_update
        ? { t: 'shop draft &middot; edits pending', c: 'var(--warn)' }
        : { t: 'draft in shop', c: 'var(--green)' })
    : { t: 'listing drafted &middot; not pushed', c: 'var(--muted)' };
  return `<span style="font-size:.62rem;padding:2px 8px;border-radius:20px;margin-left:6px;
    border:1px solid ${s.c};color:${s.c};font-weight:500;">${s.t}</span>`;
}

function gamesPublishClose() {
  if (_gamePubTimer) { clearInterval(_gamePubTimer); _gamePubTimer = null; }
  const m = document.getElementById('games-publish-modal');
  if (m) m.remove();
  _gamePub = null;
  _gamePubShots = null;
}
window.gamesPublishClose = gamesPublishClose;

/* Open (or create) the listing draft for one project, then render the editor. */
async function gamesPublishOpen(path, name, engine) {
  let draft = _gameDrafts[path];
  try {
    if (!draft) {
      const r = await api('/api/games/publish/draft', {
        method: 'POST',
        body: JSON.stringify({ project_path: path, project_name: name, engine,
                               title: name, category: 'Games' })
      });
      draft = r.draft;
      _gameDrafts[path] = draft;
    } else {
      draft = (await api('/api/games/publish/draft/' + draft.id)).draft;
    }
  } catch (e) {
    toast(String(e.message || e), 'error');
    return;
  }
  _gamePub = draft;
  _gamePubShots = null;
  gamesPublishRender();
}
window.gamesPublishOpen = gamesPublishOpen;

function gamesPublishRender() {
  const d = _gamePub;
  if (!d) return;
  const cfg = _gamesData.publish || {};
  let m = document.getElementById('games-publish-modal');
  if (!m) {
    m = document.createElement('div');
    m.id = 'games-publish-modal';
    m.className = 'modal';
    m.style.display = 'flex';
    // Dynamically created, so app-core's backdrop handler doesn't cover it.
    m.addEventListener('click', e => { if (e.target === m) gamesPublishClose(); });
    document.body.appendChild(m);
  }

  const wooOff = cfg.products_configured === false;
  const field = (label, help, inner) => `<div class="modal-field">
    <label>${label} ${hlp(help)}</label>${inner}</div>`;

  m.innerHTML = `<div class="modal-box" style="max-width:720px;">
    <div class="modal-title">&#128717;&#65039; Publish &ldquo;${esc(d.project_name || d.title)}&rdquo; to the shop</div>

    <div style="font-size:.72rem;line-height:1.6;color:var(--muted);background:var(--bg);
      border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:16px;">
      <b style="color:var(--text);">Two steps, and nothing is public.</b><br>
      <b>1. Save draft</b> keeps everything on this box &mdash; nothing is sent anywhere.<br>
      <b>2. Push to shop</b> creates a <b>DRAFT</b> product in WooCommerce. ${esc(_gPubNote)}
    </div>

    ${wooOff ? `<div style="font-size:.72rem;color:var(--warn);background:var(--bg);
      border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:16px;">
      &#9888;&#65039; ${esc(cfg.error || 'WooCommerce isn\'t connected.')} You can still build and
      save this listing &mdash; only the push needs the shop connection.</div>` : ''}

    ${field('Title', 'The product name shown in the shop.',
      `<input id="gpub-title" maxlength="120" value="${esc(d.title || '')}">`)}

    <div style="display:flex;gap:12px;flex-wrap:wrap;">
      <div style="flex:1;min-width:150px;">
        ${field('Price (USD)', 'Stored in cents; 0 means free / name-your-price.',
          `<input id="gpub-price" type="number" min="0" step="0.01" value="${(d.price || 0).toFixed(2)}">`)}
      </div>
      <div style="flex:1;min-width:150px;">
        ${field('Category', 'A WooCommerce product category — created if it doesn\'t exist.',
          `<input id="gpub-category" maxlength="80" value="${esc(d.category || 'Games')}">`)}
      </div>
    </div>

    ${field('Short description', 'One or two lines shown near the price.',
      `<textarea id="gpub-short" rows="2" maxlength="600">${esc(d.short_desc || '')}</textarea>`)}

    <div class="modal-field">
      <label style="display:flex;align-items:center;gap:8px;">
        <span>Full description ${hlp('The main product copy. Plain HTML is fine.')}</span>
        <button class="btn-sm" style="margin-left:auto;text-transform:none;letter-spacing:0;"
          onclick="gamesPublishDescribe()">&#10024; Draft with AI</button>
      </label>
      <textarea id="gpub-long" rows="6" maxlength="20000">${esc(d.long_desc || '')}</textarea>
      <div id="gpub-desc-status" style="font-size:.68rem;color:var(--muted);margin-top:5px;"></div>
    </div>

    ${field('Tags', 'Comma separated, up to 15.',
      `<input id="gpub-tags" maxlength="300" value="${esc(d.tags || '')}">`)}

    <div style="display:flex;gap:12px;flex-wrap:wrap;">
      <div style="flex:2;min-width:200px;">
        ${field('Download / store link (optional)', 'If set, the product becomes an external '
          + 'product whose button links here (itch.io, a release page…). Leave blank for a plain listing.',
          `<input id="gpub-url" maxlength="500" placeholder="https://…" value="${esc(d.external_url || '')}">`)}
      </div>
      <div style="flex:1;min-width:140px;">
        ${field('Button text', 'Only used when a link is set.',
          `<input id="gpub-button" maxlength="60" value="${esc(d.button_text || 'Get the game')}">`)}
      </div>
    </div>

    <div class="modal-field">
      <label>Images ${hlp('Project screenshots found on the node, files you upload, or cover art '
        + 'generated on the shared GPU queue. They are uploaded to the shop media library only '
        + 'when you push.')}</label>
      <div id="gpub-images">${_gPubImages(d)}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">
        <button class="btn-sm" onclick="gamesPublishShots()">&#128247; Project screenshots</button>
        <label class="btn-sm" style="cursor:pointer;">&#8679; Upload
          <input type="file" id="gpub-file" accept="image/*" style="display:none;"
            onchange="gamesPublishUpload(this)"></label>
        <button class="btn-sm" onclick="gamesPublishCover()">&#127912; Generate cover art</button>
      </div>
      <div id="gpub-img-status" style="font-size:.68rem;color:var(--muted);margin-top:6px;"></div>
      <div id="gpub-shots"></div>
    </div>

    <div style="font-size:.7rem;color:var(--muted);border-top:1px solid var(--border);
      padding-top:10px;margin-top:6px;">
      ${d.wp_id
        ? `Shop status: <b style="color:var(--green);">draft product #${d.wp_id}</b> &mdash; unpublished.
           ${d.wp_admin_url ? `<a href="${esc(d.wp_admin_url)}" target="_blank" rel="noopener"
             style="color:var(--accent);">Open in shop admin &#8599;</a>` : ''}
           ${d.needs_update ? ' &middot; <span style="color:var(--warn);">you have local edits that '
             + 'aren\'t in the shop yet</span>' : ''}`
        : 'Shop status: <b>never pushed</b> — this listing exists only on this box.'}
    </div>

    <div class="modal-actions">
      <button class="btn-sm" onclick="gamesPublishDelete()">Delete draft</button>
      <button class="btn-sm" onclick="gamesPublishClose()">Close</button>
      <button class="btn-sm" onclick="gamesPublishSave(1)">&#128190; Save draft</button>
      <button class="btn-sm primary" ${wooOff ? 'disabled' : ''} onclick="gamesPublishPush()"
        >&#11014;&#65039; ${d.wp_id ? 'Update shop draft' : 'Push to shop (draft)'}</button>
    </div>
  </div>`;
}

function _gPubImages(d) {
  const imgs = d.images || [];
  if (!imgs.length) {
    return `<div style="font-size:.7rem;color:var(--muted);">No images yet. Unity projects often
      have no finished art on disk — upload a screenshot or generate cover art.</div>`;
  }
  return `<div style="display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));">
    ${imgs.map(i => `<div style="position:relative;border:1px solid var(--border);border-radius:8px;
      overflow:hidden;background:var(--bg);">
      <img src="${esc(i.url)}" alt="" loading="lazy"
        style="width:100%;height:80px;object-fit:cover;display:block;">
      <div style="font-size:.6rem;color:var(--muted);padding:3px 5px;">${esc(i.kind || '')}</div>
      <button class="btn-sm" style="position:absolute;top:3px;right:3px;padding:1px 6px;"
        onclick="gamesPublishRmImage('${esc(i.file)}')">&times;</button>
    </div>`).join('')}
  </div>`;
}

function _gPubForm() {
  const v = id => { const el = document.getElementById(id); return el ? el.value : undefined; };
  return {
    title: (v('gpub-title') || '').trim(),
    price: v('gpub-price') || '0',
    category: v('gpub-category') || '',
    short_desc: v('gpub-short') || '',
    long_desc: v('gpub-long') || '',
    tags: v('gpub-tags') || '',
    external_url: (v('gpub-url') || '').trim(),
    button_text: v('gpub-button') || 'Get the game'
  };
}

async function gamesPublishSave(loud) {
  if (!_gamePub) return null;
  const body = _gPubForm();
  if (!body.title) { toast('Give the listing a title', 'error'); return null; }
  try {
    const r = await api('/api/games/publish/draft/' + _gamePub.id, {
      method: 'PATCH', body: JSON.stringify(body)
    });
    _gamePub = r.draft;
    _gameDrafts[r.draft.project_path] = r.draft;
    if (loud) toast('Draft saved — nothing sent to the shop');
    return r.draft;
  } catch (e) {
    toast(String(e.message || e), 'error');
    return null;
  }
}
window.gamesPublishSave = gamesPublishSave;

async function gamesPublishDelete() {
  if (!_gamePub) return;
  if (!confirm('Delete this local listing draft? Anything already in the shop is left alone.')) return;
  try {
    await api('/api/games/publish/draft/' + _gamePub.id, { method: 'DELETE' });
    delete _gameDrafts[_gamePub.project_path];
    toast('Local draft deleted');
    gamesPublishClose();
    gamesLoadProjects();
  } catch (e) {
    toast(String(e.message || e), 'error');
  }
}
window.gamesPublishDelete = gamesPublishDelete;

/* ── images ──────────────────────────────────────────────────────────────── */

async function gamesPublishShots() {
  const box = document.getElementById('gpub-shots');
  if (!_gamePub || !box) return;
  box.innerHTML = '<div style="font-size:.7rem;color:var(--muted);margin-top:8px;">Looking on the node…</div>';
  let d;
  try {
    d = await api('/api/games/publish/screenshots?path=' + encodeURIComponent(_gamePub.project_path));
  } catch (e) {
    box.innerHTML = `<div style="font-size:.7rem;color:var(--red);margin-top:8px;">${esc(String(e.message || e))}</div>`;
    return;
  }
  _gamePubShots = d;
  if (d.reachable === false) {
    box.innerHTML = `<div style="font-size:.7rem;color:var(--muted);margin-top:8px;">
      Can't reach the node right now, so its screenshots aren't available. Upload an image
      or generate cover art instead.</div>`;
    return;
  }
  if (!(d.shots || []).length) {
    box.innerHTML = `<div style="font-size:.7rem;color:var(--muted);margin-top:8px;">
      ${esc(d.note || 'No image files found in this project.')}</div>`;
    return;
  }
  box.innerHTML = `<div style="margin-top:8px;border:1px solid var(--border);border-radius:8px;
      padding:8px;max-height:180px;overflow:auto;">
    ${d.shots.map((s, i) => `<label style="display:flex;align-items:center;gap:8px;font-size:.7rem;
      padding:3px 0;cursor:pointer;">
      <input type="checkbox" data-gshot="${i}">
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
        ${s.likely_art ? '&#11088; ' : ''}${esc(s.name)}</span>
      <span style="color:var(--muted);">${s.size_kb} KB</span></label>`).join('')}
    </div>
    <button class="btn-sm" style="margin-top:6px;" onclick="gamesPublishPullShots()">Add selected</button>`;
}
window.gamesPublishShots = gamesPublishShots;

async function gamesPublishPullShots() {
  if (!_gamePub || !_gamePubShots) return;
  const paths = [];
  document.querySelectorAll('#gpub-shots input[data-gshot]').forEach(cb => {
    if (cb.checked) {
      const s = _gamePubShots.shots[Number(cb.dataset.gshot)];
      if (s) paths.push(s.path);
    }
  });
  if (!paths.length) { toast('Pick at least one', 'error'); return; }
  try {
    const r = await api(`/api/games/publish/draft/${_gamePub.id}/screenshots`, {
      method: 'POST', body: JSON.stringify({ paths })
    });
    _gamePub = r.draft;
    document.getElementById('gpub-images').innerHTML = _gPubImages(r.draft);
    document.getElementById('gpub-shots').innerHTML = '';
    toast(`Added ${(r.added || []).length} image(s)`);
  } catch (e) {
    toast(String(e.message || e), 'error');
  }
}
window.gamesPublishPullShots = gamesPublishPullShots;

async function gamesPublishUpload(input) {
  const f = input && input.files && input.files[0];
  if (!f || !_gamePub) return;
  const fd = new FormData();
  fd.append('file', f);
  try {
    // Multipart: bypass api() so the browser sets its own boundary header.
    const res = await fetch(`${API}/api/games/publish/draft/${_gamePub.id}/upload`,
                            { method: 'POST', body: fd });
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || j.detail || `HTTP ${res.status}`);
    _gamePub = j.draft;
    document.getElementById('gpub-images').innerHTML = _gPubImages(j.draft);
    toast('Image added');
  } catch (e) {
    toast(String(e.message || e), 'error');
  }
  input.value = '';
}
window.gamesPublishUpload = gamesPublishUpload;

async function gamesPublishRmImage(file) {
  if (!_gamePub) return;
  try {
    const r = await api(`/api/games/publish/draft/${_gamePub.id}/image/${encodeURIComponent(file)}`,
                        { method: 'DELETE' });
    _gamePub = r.draft;
    document.getElementById('gpub-images').innerHTML = _gPubImages(r.draft);
  } catch (e) {
    toast(String(e.message || e), 'error');
  }
}
window.gamesPublishRmImage = gamesPublishRmImage;

async function gamesPublishCover() {
  if (!_gamePub) return;
  await gamesPublishSave(0);          // so the prompt uses the current title
  const st = document.getElementById('gpub-img-status');
  try {
    const r = await api(`/api/games/publish/draft/${_gamePub.id}/cover`, {
      method: 'POST', body: JSON.stringify({})
    });
    if (st) st.textContent = 'Cover art queued on the shared GPU queue — it waits its turn…';
    gamesPublishPollCover(r.generation_id);
  } catch (e) {
    if (st) st.textContent = String(e.message || e);
  }
}
window.gamesPublishCover = gamesPublishCover;

function gamesPublishPollCover(gid) {
  if (_gamePubTimer) clearInterval(_gamePubTimer);
  let ticks = 0;
  _gamePubTimer = setInterval(async () => {
    ticks++;
    if (!_gamePub) { clearInterval(_gamePubTimer); _gamePubTimer = null; return; }
    let s;
    try {
      s = await api(`/api/games/publish/draft/${_gamePub.id}/cover/${gid}`);
    } catch (e) {
      clearInterval(_gamePubTimer); _gamePubTimer = null; return;
    }
    const st = document.getElementById('gpub-img-status');
    if (s.attached && s.draft) {
      _gamePub = s.draft;
      const box = document.getElementById('gpub-images');
      if (box) box.innerHTML = _gPubImages(s.draft);
      if (st) st.textContent = 'Cover art added — remove it if you don\'t like it.';
      clearInterval(_gamePubTimer); _gamePubTimer = null;
      return;
    }
    if (st) st.textContent = `Cover art: ${s.status || 'queued'}…`;
    if (s.status === 'failed' || ticks > 150) {
      if (st && s.status === 'failed') st.textContent = 'Cover art generation failed.';
      clearInterval(_gamePubTimer); _gamePubTimer = null;
    }
  }, 3000);
}

/* ── AI description (queued like every other LLM call) ────────────────────── */

async function gamesPublishDescribe() {
  if (!_gamePub) return;
  await gamesPublishSave(0);
  const st = document.getElementById('gpub-desc-status');
  try {
    const r = await api(`/api/games/publish/draft/${_gamePub.id}/describe`, { method: 'POST' });
    if (st) st.textContent = 'Queued on the shared LLM queue — the suggestion lands here to edit.';
    let ticks = 0;
    const t = setInterval(async () => {
      ticks++;
      let s;
      try { s = await api('/api/games/publish/describe/' + r.task_id); }
      catch (e) { clearInterval(t); return; }
      if (s.status === 'done') {
        clearInterval(t);
        const sh = document.getElementById('gpub-short');
        const lg = document.getElementById('gpub-long');
        const tg = document.getElementById('gpub-tags');
        if (sh && s.short && !sh.value.trim()) sh.value = s.short;
        if (lg && s.long) lg.value = s.long;
        if (tg && s.tags && !tg.value.trim()) tg.value = s.tags;
        if (st) st.textContent = 'Suggestion loaded. Edit it, then Save draft — nothing was pushed.';
      } else if (s.status === 'failed' || ticks > 100) {
        clearInterval(t);
        if (st) st.textContent = s.error || 'The description helper didn\'t finish.';
      } else if (st) {
        st.textContent = `Writing copy: ${s.status}…`;
      }
    }, 3000);
  } catch (e) {
    if (st) st.textContent = String(e.message || e);
  }
}
window.gamesPublishDescribe = gamesPublishDescribe;

/* ── step 2: push (creates a Woo DRAFT, never publishes) ──────────────────── */

async function gamesPublishPush() {
  if (!_gamePub) return;
  const saved = await gamesPublishSave(0);
  if (!saved) return;
  if (!confirm(`Create a DRAFT product in WooCommerce for "${saved.title}"?\n\n`
             + 'It will NOT be visible to anyone. You publish it yourself in the shop admin '
             + 'when you\'re ready.')) return;
  try {
    const r = await api(`/api/games/publish/${_gamePub.id}/push`, {
      method: 'POST', body: JSON.stringify({ confirm: true })
    });
    _gamePub = r.draft;
    _gameDrafts[r.draft.project_path] = r.draft;
    toast(r.action === 'updated' ? 'Shop draft updated' : 'Draft product created in the shop');
    if ((r.image_errors || []).length) toast(`${r.image_errors.length} image(s) not uploaded`, 'error');
    gamesPublishRender();
    gamesLoadProjects();
  } catch (e) {
    toast(String(e.message || e), 'error');
  }
}
window.gamesPublishPush = gamesPublishPush;

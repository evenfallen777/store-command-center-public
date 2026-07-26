'use strict';

/* 🍿 TV — prompt-to-television (docs/TV-DESIGN.md §6, P1).
   Shows grid ("create show" = the one prompt box) → show page: bible summary,
   cast, episodes with statuses, per-episode Write/Produce/Play. The player
   autoplays the next done episode ("channel mode"). An episode IS a studio
   project — all heavy lifting happens behind /api/tv/*; this tab only watches.
   Reuses the shared media/card helpers from tab-videos.js (classic scripts,
   one global scope): syncCards / setHTMLKeepMedia / stopMediaIn / _srcRetry.
   Routed via registerView('tv') (plugin-loader.js) — renderView's default:
   case dispatches the nav item's data-view="tv"; no core app-nav.js edit. */

let _tvOpenId = null;        // show open on the show page; null = shows grid
let _tvPollTimer = null;
let _tvShow = null;          // last-fetched show detail (channel-mode lookup)
let _tvPlayingId = null;     // episode in the player; null = player closed
let _tvNsfwOk = false;       // show the 🔒 Private toggle?

/* The episode-length ladder (docs/TV-DESIGN.md §1) — value = format_seconds. */
const _TV_FORMATS = [
  { v: 150, label: '2–3 min — pilot rung, one overnight render' },
  { v: 300, label: '5 min — a day+night render' },
  { v: 660, label: '11 min — adult-swim slot, a weekend render' },
];

const _TV_SHOW_COLORS = { new: '#94a3b8', bible: '#a855f7', ready: '#22c55e', failed: '#ef4444' };
const _TV_EP_COLORS = {
  draft: '#94a3b8', writing: '#a855f7', ready: '#38bdf8',
  producing: '#f59e0b', done: '#22c55e', failed: '#ef4444',
};
const _TV_EP_BUSY = ['draft', 'writing', 'producing'];

const _tvInp = 'padding:7px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;font-family:inherit;font-size:.85rem;box-sizing:border-box';

function _tvClearPoll() {
  if (_tvPollTimer) { clearTimeout(_tvPollTimer); _tvPollTimer = null; }
}

function _tvBadge(status, colors) {
  const c = colors[status] || '#94a3b8';
  const label = { new: '⏳ queued', bible: '\u{1FA84} writing bible…',
                  draft: '⏳ queued', writing: '✍️ writing…',
                  producing: '\u{1F3AC} producing…' }[status] || status;
  return `<span style="font-size:.7rem;font-weight:700;padding:2px 8px;border-radius:10px;background:${c}22;color:${c};border:1px solid ${c}55;white-space:nowrap">${esc(label)}</span>`;
}

function _tvVidUrl(path) {
  return `${API}/videos/${encodeURIComponent(String(path).split('/').pop())}`;
}

function _tvFmtLabel(secs) {
  const f = _TV_FORMATS.find(x => x.v === secs);
  return f ? f.label.split(' — ')[0] : `~${Math.round((secs || 0) / 60)} min`;
}

function _tvEpTag(e) {
  return `S${String(e.season).padStart(2, '0')}E${String(e.number).padStart(2, '0')}`;
}

async function renderTV() {
  _tvClearPoll();
  try { _tvNsfwOk = !!(await api('/api/nsfw/status')).visible; } catch { _tvNsfwOk = false; }
  if (_tvOpenId) await _tvRenderShow(_tvOpenId);
  else await _tvRenderGrid();
}
window.renderTV = renderTV;

/* ── Shows grid ────────────────────────────────────────────────────────── */

async function _tvRenderGrid() {
  const el = viewRoot();
  stopMediaIn(el);   // never leave a detached player's audio running
  el.innerHTML = `
    <div class="section-header">
      <div>
        <div class="section-title">\u{1F37F} TV</div>
        <div class="section-sub">Prompt-to-television — whole shows with a bible, a cast and episode-to-episode continuity</div>
      </div>
    </div>
    <div class="card" style="margin-bottom:16px">
      <div style="font-weight:600;margin-bottom:8px">\u{1F4FA} Create a show</div>
      <textarea id="tv-prompt" placeholder="What do you want to watch? — a line or a whole pitch, e.g. 'a burned-out wizard runs a 24h corner store for monsters'"
        style="width:100%;min-height:80px;${_tvInp};resize:vertical"></textarea>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-top:10px">
        <label style="font-size:.78rem;color:var(--muted)">Style (optional) ${hlp('Visual style hint for every shot of every episode, e.g. “2D adult animation, thick outlines, flat colors”. Leave blank and the showrunner picks one.')}
          <input id="tv-style" placeholder="e.g. 2D adult animation, thick outlines" style="width:100%;margin-top:4px;${_tvInp}"></label>
        <label style="font-size:.78rem;color:var(--muted)">Episode length ${hlp('The render-time ladder: ≈3 GPU-hours per finished minute on the RTX 3060. Start at 2–3 min — one overnight render per episode.')}
          <select id="tv-format" style="width:100%;margin-top:4px;${_tvInp}">
            ${_TV_FORMATS.map((f, i) => `<option value="${f.v}"${i === 0 ? ' selected' : ''}>${f.label}</option>`).join('')}
          </select></label>
      </div>
      <div style="display:flex;align-items:center;gap:14px;margin-top:12px;flex-wrap:wrap">
        <button id="tv-create-btn" style="width:auto;padding:10px 26px" onclick="tvCreateShow()">\u{1F4FA} Create show</button>
        ${_tvNsfwOk ? `<label style="font-size:.78rem;color:var(--muted);display:flex;align-items:center;gap:6px"><input type="checkbox" id="tv-nsfw" style="width:auto">\u{1F512} Private</label>` : ''}
      </div>
      <div style="margin-top:8px;font-size:.74rem;color:var(--muted)">ℹ The showrunner writes the show bible from your prompt — premise, cast, world rules and a season arc. Small prompts get expanded; big pitches get honored. You can then write and produce episodes one at a time.</div>
    </div>
    <div id="tv-shows"></div>`;
  await _tvRefreshShows();
}

async function _tvRefreshShows() {
  const el = document.getElementById('tv-shows');
  if (!el || _tvOpenId) return;
  _tvClearPoll();
  let shows = [];
  try { shows = await api('/api/tv/shows'); } catch { return; }
  setHTMLKeepMedia(el, shows.length
    ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px">${shows.map(_tvShowCard).join('')}</div>`
    : `<div style="text-align:center;color:var(--muted);padding:50px 20px">\u{1F37F} No shows yet — tell the TV what you want to watch.</div>`);
  if (shows.some(s => s.status === 'new' || s.status === 'bible' || _tvArtBusy(s))
      && _currentView === 'tv') {
    _tvPollTimer = setTimeout(_tvRefreshShows, 2500);
  }
}

function _tvArtBusy(s) { return s.art_status === 'queued' || s.art_status === 'generating'; }

/* Streaming-service poster card: the box art IS the card; title/meta overlay
   on a bottom gradient, badges float on top. No art yet → branded placeholder
   (writing-bible / painting states get their own line). */
function _tvShowCard(s) {
  const meta = `${s.episodes_done}/${s.episodes} ep${s.episodes === 1 ? '' : 's'} · ${esc(_tvFmtLabel(s.format_seconds))}`;
  const state =
    (s.status === 'new' || s.status === 'bible') ? '<div style="font-size:.72rem;color:#c4b5fd">\u{1FA84} writing the bible…</div>'
    : s.status === 'failed' ? '<div style="font-size:.72rem;color:#fca5a5">❌ showrunner failed — open for detail</div>'
    : _tvArtBusy(s) ? '<div style="font-size:.72rem;color:#c4b5fd">\u{1F3A8} painting box art…</div>' : '';
  const poster = s.boxart_url
    ? `<img src="${s.boxart_url}" alt="" loading="lazy" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover">`
    : `<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:linear-gradient(160deg,#1e1b4b 0%,#0f172a 60%,#020617 100%)">
         <div style="font-size:3rem;opacity:.85">${s.status === 'failed' ? '❌' : '\u{1F37F}'}</div></div>`;
  return `<div style="position:relative;aspect-ratio:2/3;border-radius:12px;overflow:hidden;cursor:pointer;border:1px solid var(--border);background:#0f172a" onclick="tvOpen(${s.id})" title="${esc(s.description || s.title || '')}">
    ${poster}
    <div style="position:absolute;top:8px;left:8px;right:8px;display:flex;justify-content:space-between;gap:6px">
      ${_tvBadge(s.status, _TV_SHOW_COLORS)}
      <button onclick="event.stopPropagation();tvDeleteShow(${s.id})" style="width:auto;padding:2px 8px;font-size:.72rem;background:#0009;color:#ef4444;border:1px solid #ef444450;margin-top:0">\u{1F5D1}</button>
    </div>
    <div style="position:absolute;left:0;right:0;bottom:0;padding:26px 10px 10px;background:linear-gradient(180deg,transparent 0%,#000c 55%,#000e 100%)">
      <div style="font-weight:800;font-size:.92rem;color:#fff;text-shadow:0 1px 3px #000">${esc(s.title || 'Untitled show')}${s.nsfw ? ' \u{1F512}' : ''}</div>
      <div style="margin-top:2px;font-size:.7rem;color:#cbd5e1">${meta}</div>
      ${state}
    </div>
  </div>`;
}

async function tvCreateShow() {
  const prompt = document.getElementById('tv-prompt').value.trim();
  if (!prompt) { toast('Tell the TV what you want to watch first', 'warn'); return; }
  const body = {
    prompt,
    style: document.getElementById('tv-style').value.trim(),
    format_seconds: parseInt(document.getElementById('tv-format').value),
    nsfw: !!document.getElementById('tv-nsfw')?.checked,
  };
  const btn = document.getElementById('tv-create-btn');
  btn.disabled = true; btn.textContent = '⏳ Queuing…';
  try {
    await api('/api/tv/shows', { method: 'POST', body: JSON.stringify(body) });
    toast('\u{1F4FA} Show created — the showrunner is writing the bible');
    document.getElementById('tv-prompt').value = '';
    await _tvRefreshShows();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { btn.disabled = false; btn.textContent = '\u{1F4FA} Create show'; }
}
window.tvCreateShow = tvCreateShow;

async function tvOpen(id) { _tvOpenId = id; _tvPlayingId = null; await renderTV(); }
window.tvOpen = tvOpen;

async function tvBack() { _tvOpenId = null; _tvPlayingId = null; _tvShow = null; await renderTV(); }
window.tvBack = tvBack;

async function tvDeleteShow(id) {
  if (!confirm('Delete this show, its episodes and their studio projects?')) return;
  try {
    await api(`/api/tv/shows/${id}`, { method: 'DELETE' });
    toast('Show deleted');
    if (_tvOpenId === id) { _tvOpenId = null; _tvPlayingId = null; }
    await renderTV();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.tvDeleteShow = tvDeleteShow;

/* ── Show page ─────────────────────────────────────────────────────────── */

async function _tvRenderShow(id) {
  const el = viewRoot();
  let s;
  try { s = await api(`/api/tv/shows/${id}`); }
  catch (e) { _tvOpenId = null; toast('Error: ' + e.message, 'error'); return _tvRenderGrid(); }
  await _tvHydrateBusy(s);
  _tvShow = s;
  stopMediaIn(el);
  // Static skeleton once — the poll only touches #tv-bible / #tv-episodes,
  // NEVER #tv-player, so a playing episode is never rebuilt mid-watch.
  el.innerHTML = `
    <div class="section-header">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <button class="btn-sm" onclick="tvBack()">← Shows</button>
        <div>
          <div class="section-title" id="tv-show-title">${esc(s.title || 'Untitled show')}${s.nsfw ? ' \u{1F512}' : ''}</div>
          <div class="section-sub">${esc(s.style || '2D animation')} · ${esc(_tvFmtLabel(s.format_seconds))} episodes</div>
        </div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button id="tv-art-btn" onclick="tvRegenArt(${s.id})" style="width:auto;padding:5px 12px;font-size:.8rem;background:#a855f720;color:#a855f7;border:1px solid #a855f750">\u{1F3A8} Redo art &amp; blurb</button>
        <button onclick="tvDeleteShow(${s.id})" style="width:auto;padding:5px 12px;font-size:.8rem;background:#ef444420;color:#ef4444;border:1px solid #ef444450">\u{1F5D1} Delete show</button>
      </div>
    </div>
    <div id="tv-hero"></div>
    <div id="tv-player"></div>
    <div id="tv-bible"></div>
    <div class="section-header" style="margin-top:16px">
      <div class="section-title" style="font-size:1rem">\u{1F4FA} Episodes</div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <input id="tv-ep-notes" placeholder="Notes for the writer (optional)" style="width:230px;${_tvInp};font-size:.78rem">
        <button id="tv-write-btn" style="width:auto;padding:7px 16px" onclick="tvWriteEpisode(${s.id})">✍️ Write next episode</button>
      </div>
    </div>
    <div id="tv-episodes"></div>`;
  _tvFillShow(s);
  _tvSchedulePoll(s);
}

/* Poll tick: refetch the show, refresh bible + episode cards in place. */
async function _tvRefreshShow() {
  if (!_tvOpenId || _currentView !== 'tv') return;
  _tvClearPoll();
  let s;
  try { s = await api(`/api/tv/shows/${_tvOpenId}`); } catch { return; }
  await _tvHydrateBusy(s);
  _tvShow = s;
  _tvFillShow(s);
  _tvSchedulePoll(s);
}

/* Enrich producing episodes with their project's live progress_msg — the show
   payload carries episode rows only; the per-episode GET joins the project. */
async function _tvHydrateBusy(s) {
  const busy = (s.episodes || []).filter(e => e.status === 'producing').slice(0, 3);
  await Promise.all(busy.map(async e => {
    try { Object.assign(e, await api(`/api/tv/episodes/${e.id}`)); } catch {}
  }));
}

function _tvSchedulePoll(s) {
  const busy = s.status === 'new' || s.status === 'bible' || _tvArtBusy(s)
    || (s.episodes || []).some(e => _TV_EP_BUSY.includes(e.status));
  if (busy && _currentView === 'tv') _tvPollTimer = setTimeout(_tvRefreshShow, 2500);
}

function _tvFillShow(s) {
  const heroEl = document.getElementById('tv-hero');
  const bibleEl = document.getElementById('tv-bible');
  const epsEl = document.getElementById('tv-episodes');
  if (!bibleEl || !epsEl) return;
  if (heroEl) {
    const heroHtml = _tvHeroCard(s);
    if (heroEl._tvHtml !== heroHtml) { heroEl.innerHTML = heroHtml; heroEl._tvHtml = heroHtml; }
  }
  const artBtn = document.getElementById('tv-art-btn');
  if (artBtn) {
    artBtn.disabled = _tvArtBusy(s) || s.status !== 'ready';
    artBtn.title = _tvArtBusy(s) ? 'Art is already generating' :
      s.status !== 'ready' ? 'The showrunner has to finish the bible first' : '';
  }
  const bibleHtml = _tvBibleCard(s);
  if (bibleEl._tvHtml !== bibleHtml) { bibleEl.innerHTML = bibleHtml; bibleEl._tvHtml = bibleHtml; }
  const writeBtn = document.getElementById('tv-write-btn');
  if (writeBtn) {
    writeBtn.disabled = s.status !== 'ready';
    writeBtn.title = s.status !== 'ready' ? 'The showrunner has to finish the bible first' : '';
  }
  const eps = s.episodes || [];
  if (!eps.length) {
    stopMediaIn(epsEl);
    epsEl.innerHTML = `<div style="text-align:center;color:var(--muted);padding:40px 20px">\u{1F4FA} No episodes yet — ${s.status === 'ready' ? 'write the first one above!' : 'the showrunner is still writing the bible.'}</div>`;
    return;
  }
  // syncCards (tab-videos.js): the poll only re-renders cards whose data
  // changed — an unchanged card (e.g. a done episode) is never touched.
  let grid = epsEl.querySelector('[data-cards="tv-eps"]');
  if (!grid) {
    stopMediaIn(epsEl);
    epsEl.innerHTML = '<div data-cards="tv-eps" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px"></div>';
    grid = epsEl.firstElementChild;
  }
  syncCards(grid, eps, _tvEpisodeCard);
}

/* Banner hero: wide art with the title/description over a left-side gradient
   (the banner prompt reserves calm space on the left for exactly this).
   No banner yet → compact text hero with the same info + art status line. */
function _tvHeroCard(s) {
  const artLine =
    _tvArtBusy(s) ? `<div style="font-size:.74rem;color:#c4b5fd;margin-top:6px">\u{1F3A8} Painting box art &amp; banner — queued on the GPU (waits politely behind an episode render)…</div>`
    : s.art_status === 'failed' ? `<div style="font-size:.72rem;color:#fca5a5;margin-top:6px">\u{1F3A8} Art failed: ${esc(s.art_error || 'unknown')} — hit “Redo art &amp; blurb”.</div>`
    : s.art_status === 'done' && s.art_error ? `<div style="font-size:.7rem;color:var(--muted);margin-top:6px">\u{1F3A8} ${esc(s.art_error)}</div>` : '';
  const desc = s.description
    ? `<div style="font-size:.84rem;line-height:1.55;max-width:640px">${esc(s.description)}</div>` : '';
  if (s.banner_url) {
    return `<div style="position:relative;border-radius:14px;overflow:hidden;border:1px solid var(--border);margin-bottom:16px;min-height:190px">
      <img src="${s.banner_url}" alt="" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover">
      <div style="position:relative;padding:26px 24px;background:linear-gradient(90deg,#000d 0%,#000a 42%,transparent 75%);min-height:190px;display:flex;flex-direction:column;justify-content:flex-end;gap:8px">
        <div style="font-size:1.5rem;font-weight:900;color:#fff;text-shadow:0 2px 6px #000">${esc(s.title || 'Untitled show')}${s.nsfw ? ' \u{1F512}' : ''}</div>
        ${desc ? desc.replace('font-size:.84rem', 'font-size:.84rem;color:#e2e8f0;text-shadow:0 1px 3px #000') : ''}
        ${artLine}
      </div>
    </div>`;
  }
  return `<div class="card" style="margin-bottom:16px">
    ${desc || `<div style="font-size:.8rem;color:var(--muted)">${esc((s.bible && s.bible.premise) || s.prompt || '')}</div>`}
    ${artLine}
  </div>`;
}

function _tvBibleCard(s) {
  if (s.status === 'new' || s.status === 'bible') {
    return `<div class="card" style="text-align:center;padding:40px 20px;margin-bottom:16px">
      <div style="font-size:2rem">\u{1FA84}</div>
      <div style="margin-top:8px;color:#a855f7;font-size:.9rem">The showrunner is writing the bible…</div>
      <div style="margin-top:4px;font-size:.78rem;color:var(--muted)">${esc(s.prompt || '')}</div>
    </div>`;
  }
  if (s.status === 'failed') {
    return `<div class="card" style="margin-bottom:16px;border-color:#ef444450">
      <div style="color:#ef4444;font-weight:600;font-size:.9rem">❌ Showrunner failed</div>
      <div style="margin-top:6px;font-size:.74rem;color:#fca5a5;white-space:pre-wrap;font-family:monospace;max-height:120px;overflow:auto">${esc(s.error || 'no detail captured')}</div>
    </div>`;
  }
  const b = s.bible || {};
  const arc = typeof b.season_arc === 'object' && b.season_arc
    ? (b.season_arc.summary || '') : (b.season_arc || '');
  const cast = (b.characters || []).map(c => `
    <div style="padding:8px 10px;background:var(--bg);border:1px solid var(--border);border-radius:8px">
      <div style="font-weight:700;font-size:.84rem">${esc(c.name)}</div>
      ${c.quirks ? `<div style="margin-top:3px;font-size:.74rem;color:var(--muted)">${esc(c.quirks)}</div>` : ''}
      ${c.voice ? `<div style="margin-top:3px;font-size:.7rem;color:#a855f7">\u{1F5E3}️ ${esc(c.voice)}</div>` : ''}
    </div>`).join('');
  return `<div class="card" style="margin-bottom:16px">
    <div style="font-weight:600;font-size:.88rem">\u{1F4D6} Show bible</div>
    ${b.premise ? `<div style="margin-top:8px;font-size:.82rem;line-height:1.5">${esc(b.premise)}</div>` : ''}
    ${b.tone ? `<div style="margin-top:6px;font-size:.76rem;color:var(--muted)"><b>Tone:</b> ${esc(b.tone)}</div>` : ''}
    ${cast ? `<div style="margin-top:10px;font-size:.78rem;font-weight:600">\u{1F3AD} Cast</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;margin-top:6px">${cast}</div>` : ''}
    ${arc ? `<div style="margin-top:10px;font-size:.76rem;color:var(--muted)"><b>Season arc:</b> ${esc(arc)}</div>` : ''}
  </div>`;
}

function _tvEpisodeCard(e) {
  let body = '';
  if (e.status === 'draft' || e.status === 'writing') {
    body = `<div style="margin-top:8px;font-size:.8rem;color:#a855f7">✍️ The writers' room is on it — script then storyboard…</div>`;
  } else if (e.status === 'producing') {
    const msg = (e.project && e.project.progress_msg) || 'Rendering scenes on the GPU queue…';
    body = `<div style="margin-top:8px;font-size:.8rem;color:#f59e0b">⏳ ${esc(msg)}</div>`;
  } else if (e.status === 'failed') {
    body = `<div style="margin-top:8px;font-size:.72rem;color:#fca5a5;white-space:pre-wrap;max-height:80px;overflow:auto;font-family:monospace">${esc(e.error || (e.project && e.project.error) || 'failed')}</div>`;
  } else if (e.status === 'ready') {
    body = `<div style="margin-top:8px;font-size:.78rem;color:#38bdf8">\u{1F4CB} Written &amp; storyboarded — ready to produce (≈${Math.round((_tvShow?.format_seconds || 150) / 60 * 3)}h render).</div>`;
  }
  const playBtn = e.status === 'done' && e.final_path
    ? `<button onclick="tvPlay(${e.id})" style="width:auto;padding:5px 14px;font-size:.8rem;background:#22c55e;color:#000;border:none;margin-top:0">▶ Play</button>` : '';
  const produceBtn = e.status === 'ready'
    ? `<button onclick="tvProduce(${e.id})" style="width:auto;padding:5px 14px;font-size:.8rem;background:#6c63ff;color:#fff;border:none;margin-top:0">\u{1F3AC} Produce</button>` : '';
  const delBtn = e.status !== 'producing'
    ? `<button onclick="tvDeleteEpisode(${e.id})" style="width:auto;padding:5px 12px;font-size:.8rem;background:#ef444420;color:#ef4444;border:1px solid #ef444450;margin-top:0">\u{1F5D1}</button>` : '';
  return `<div class="card">
    <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
      ${_tvBadge(e.status, _TV_EP_COLORS)}
      <span style="font-size:.74rem;color:var(--muted);font-weight:700">${_tvEpTag(e)}</span>
    </div>
    <div style="margin-top:8px;font-weight:700;font-size:.9rem">${esc(e.title || 'Untitled episode')}</div>
    ${e.synopsis ? `<div style="margin-top:4px;font-size:.76rem;color:var(--muted);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden" title="${esc(e.synopsis)}">${esc(e.synopsis)}</div>` : ''}
    ${body}
    <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">${playBtn}${produceBtn}${delBtn}</div>
  </div>`;
}

async function tvRegenArt(showId) {
  try {
    await api(`/api/tv/shows/${showId}/art`, { method: 'POST' });
    toast('\u{1F3A8} Art & blurb queued — during a render it slips in between scenes');
    await _tvRefreshShow();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.tvRegenArt = tvRegenArt;

async function tvWriteEpisode(showId) {
  const notesEl = document.getElementById('tv-ep-notes');
  const btn = document.getElementById('tv-write-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Queuing…'; }
  try {
    await api(`/api/tv/shows/${showId}/episodes`, {
      method: 'POST', body: JSON.stringify({ notes: notesEl ? notesEl.value.trim() : '' }) });
    toast('✍️ The writers’ room is on the next episode');
    if (notesEl) notesEl.value = '';
    await _tvRefreshShow();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.innerHTML = '✍️ Write next episode'; } }
}
window.tvWriteEpisode = tvWriteEpisode;

async function tvProduce(episodeId) {
  if (!confirm('Produce this episode? The full render runs on the GPU queue — hours, not minutes.')) return;
  try {
    await api(`/api/tv/episodes/${episodeId}/produce`, { method: 'POST' });
    toast('\u{1F3AC} Producing — scenes render on the GPU queue');
    await _tvRefreshShow();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.tvProduce = tvProduce;

async function tvDeleteEpisode(episodeId) {
  if (!confirm('Delete this episode and its studio project?')) return;
  try {
    await api(`/api/tv/episodes/${episodeId}`, { method: 'DELETE' });
    toast('Episode deleted');
    if (_tvPlayingId === episodeId) tvClosePlayer();
    await _tvRefreshShow();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}
window.tvDeleteEpisode = tvDeleteEpisode;

/* ── Player (channel mode) ─────────────────────────────────────────────── */

function _tvFindEp(id) {
  return ((_tvShow && _tvShow.episodes) || []).find(e => e.id === id);
}

/* The next DONE episode after `id` in (season, number) order — channel mode. */
function _tvNextDone(id) {
  const eps = ((_tvShow && _tvShow.episodes) || []);   // already season,number sorted
  const i = eps.findIndex(e => e.id === id);
  if (i < 0) return null;
  return eps.slice(i + 1).find(e => e.status === 'done' && e.final_path) || null;
}

function tvPlay(episodeId) {
  const ep = _tvFindEp(episodeId);
  if (!ep || !ep.final_path) { toast('Episode has no video yet', 'warn'); return; }
  _tvPlayingId = episodeId;
  const el = document.getElementById('tv-player');
  if (!el) return;
  stopMediaIn(el);   // hard-stop the previous episode before the swap
  const next = _tvNextDone(episodeId);
  el.innerHTML = `<div class="card" style="margin-bottom:16px">
    <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap">
      <div id="tv-now-playing" style="font-size:.84rem;font-weight:600">${_tvNowPlayingLabel(ep, next)}</div>
      <button onclick="tvClosePlayer()" style="width:auto;padding:3px 10px;font-size:.76rem;background:var(--surface2,#16161f);color:var(--muted);border:1px solid var(--border);margin-top:0">✕ Close</button>
    </div>
    <video controls autoplay playsinline preload="metadata" onended="_tvPlayerEnded()"
      style="width:100%;max-height:420px;border-radius:8px;background:#000;display:block">
      <source src="${_tvVidUrl(ep.final_path)}" type="video/mp4" onerror="_srcRetry(this)">
    </video>
  </div>`;
  el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
window.tvPlay = tvPlay;

function _tvNowPlayingLabel(ep, next) {
  return `\u{1F37F} Now playing: ${_tvEpTag(ep)} — ${esc(ep.title || 'Untitled')}`
    + (next ? ` <span style="font-weight:400;color:var(--muted)">· up next: ${_tvEpTag(next)}</span>`
            : ` <span style="font-weight:400;color:var(--muted)">· last one — write the next episode!</span>`);
}

/* Channel mode: when an episode ends, roll straight into the next done one.
   Reuses the SAME <video> element (src swap + play()) so playback continues
   without a fresh autoplay-permission roll of the dice. */
function _tvPlayerEnded() {
  const next = _tvNextDone(_tvPlayingId);
  if (!next) return;   // label already says this was the last one
  _tvPlayingId = next.id;
  const el = document.getElementById('tv-player');
  const v = el && el.querySelector('video');
  if (!v) return;
  const src = v.querySelector('source');
  if (src) { src.src = _tvVidUrl(next.final_path); v.load(); }
  else v.src = _tvVidUrl(next.final_path);
  const p = v.play(); if (p && p.catch) p.catch(() => {});
  const label = document.getElementById('tv-now-playing');
  if (label) label.innerHTML = _tvNowPlayingLabel(next, _tvNextDone(next.id));
}
window._tvPlayerEnded = _tvPlayerEnded;

function tvClosePlayer() {
  _tvPlayingId = null;
  const el = document.getElementById('tv-player');
  if (el) { stopMediaIn(el); el.innerHTML = ''; }
}
window.tvClosePlayer = tvClosePlayer;

/* Nav wiring: the sidebar item carries data-view="tv"; renderView's default:
   case dispatches unknown views to PLUGIN_VIEWS — register there (loads after
   plugin-loader.js in index.html). */
if (typeof registerView === 'function') registerView('tv', renderTV);

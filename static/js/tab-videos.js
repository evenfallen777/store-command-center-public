/* Restored from pre_unification_backup (Jul 9) — real tab implementation.
   Part of the modular frontend: one file per tab. */

/* ── shared media-player helpers ──
   Used by this tab, videos-chains.js and tab-director.js (all load after the
   core, before use — classic scripts share one global scope).
   Fixes the studio player bugs:
   1. audio leak: replacing innerHTML while a <video> plays leaves a detached
      media element whose AUDIO can keep playing (Chromium keeps playing media
      elements alive until told to stop) → stopMediaIn() pauses + unloads every
      player before its container is re-rendered/navigated away.
   2. dead mute/volume controls: the 2-2.5 s status polls rebuild the players,
      wiping whatever mute/volume/position the user set → capture/restore keeps
      PER-PLAYER (keyed by src) state across re-renders. */
const _mediaState = {};   // src → {muted, volume, t, playing}

function _mediaKey(m) {
  return m.currentSrc || m.getAttribute('src') || (m.querySelector('source') || {}).src || '';
}

function stopMediaIn(root) {
  if (!root) return;
  root.querySelectorAll('video,audio').forEach(m => {
    try {
      m.pause();
      m.removeAttribute('src');
      m.querySelectorAll('source').forEach(s => s.removeAttribute('src'));
      m.load();   // actually releases the decoder — a detached element otherwise keeps its audio alive
    } catch {}
  });
}
window.stopMediaIn = stopMediaIn;

function captureMediaState(root) {
  if (!root) return;
  root.querySelectorAll('video,audio').forEach(m => {
    const key = _mediaKey(m);
    if (key) {
      _mediaState[key] = { muted: m.muted, volume: m.volume,
                           t: m.currentTime || 0, playing: !m.paused && !m.ended };
    }
  });
  stopMediaIn(root);   // then hard-stop the old players (see audio-leak note)
}

function restoreMediaState(root) {
  if (!root) return;
  root.querySelectorAll('video,audio').forEach(m => {
    const st = _mediaState[_mediaKey(m)];
    if (!st) return;
    const apply = () => {
      m.muted = st.muted;
      m.volume = st.volume;
      try { if (st.t > 0.1) m.currentTime = st.t; } catch {}
      if (st.playing) { const p = m.play(); if (p && p.catch) p.catch(() => {}); }
    };
    if (m.readyState >= 1) apply();
    else m.addEventListener('loadedmetadata', apply, { once: true });
  });
}

/* innerHTML swap that keeps each player's user state and never leaks audio. */
function setHTMLKeepMedia(el, html) {
  if (!el) return;
  captureMediaState(el);
  el.innerHTML = html;
  restoreMediaState(el);
}
window.setHTMLKeepMedia = setHTMLKeepMedia;

/* ── keyed card-list diffing ──
   The 2-2.5 s status polls used to rebuild the WHOLE gallery innerHTML every
   tick, tearing down and reloading every <video> element — visible flicker on
   the whole tab while anything generates, and interrupted media loads that
   surface as "No video with supported format and MIME type found". Instead
   each card lives in its own keyed cell and is re-rendered ONLY when its
   rendered HTML actually changed; an unchanged card (e.g. a done video that's
   playing) is never touched by the poll. */
function syncCards(grid, items, cardFn) {
  const seen = new Set();
  let anchor = null;                                  // last correctly-placed cell
  for (const it of items) {
    const key = String(it.id);
    seen.add(key);
    const html = cardFn(it);
    let cell = grid.querySelector(`:scope > [data-key="${key}"]`);
    if (!cell) {
      cell = document.createElement('div');
      cell.setAttribute('data-key', key);
      cell.innerHTML = html;
      cell._cardHtml = html;
      grid.insertBefore(cell, anchor ? anchor.nextElementSibling : grid.firstElementChild);
    } else {
      if (cell._cardHtml !== html) { setHTMLKeepMedia(cell, html); cell._cardHtml = html; }
      const want = anchor ? anchor.nextElementSibling : grid.firstElementChild;
      if (want !== cell) grid.insertBefore(cell, want);
    }
    anchor = cell;
  }
  for (const cell of [...grid.children]) {
    if (!seen.has(cell.getAttribute('data-key'))) { stopMediaIn(cell); cell.remove(); }
  }
}
window.syncCards = syncCards;

/* One-shot retry when a <source> fails to load — e.g. the file landed on disk
   a beat after the card rendered, or the server hiccuped while the GPU node
   was busy. Re-running load() re-fetches the same URL; max 2 tries so a
   genuinely broken file still shows the browser's error. */
function _srcRetry(sourceEl) {
  const v = sourceEl.parentElement;
  if (!v || v.tagName !== 'VIDEO') return;
  const tries = +(v.dataset.srcRetries || 0);
  if (tries >= 2) return;
  v.dataset.srcRetries = tries + 1;
  setTimeout(() => { try { v.load(); } catch {} }, 1600 * (tries + 1));
}
window._srcRetry = _srcRetry;

/* ── VIDEO GENERATION ── */
let _videosPollTimer = null;

async function renderVideos() {
  const el = viewRoot();
  // Load installed video models for selector
  let installedVideoModels = [];
  try {
    const vms = await api('/api/video-models');
    installedVideoModels = vms.filter(m => m.installed);
  } catch {}
  window._videoModels = installedVideoModels;
  const modelOptions = installedVideoModels.length
    ? installedVideoModels.map(m => `<option value="${esc(m.model_id)}"${m.model_id==='Wan-AI/Wan2.1-T2V-1.3B-Diffusers'?' selected':''}>${esc(m.label)}</option>`).join('')
    : '<option value="Wan-AI/Wan2.1-T2V-1.3B-Diffusers">Wan2.1 T2V 1.3B (Default)</option>';

  el.innerHTML = `
    <div class="section-header">
      <div>
        <div class="section-title">&#127916; Video Generation</div>
        <div class="section-sub">Text-to-video via diffusers &mdash; runs on RTX 3060 node</div>
      </div>
      <button class="btn-sm" onclick="showChainBuilder()" style="background:#6c63ff;color:#fff;border:none">&#128279; New Chain</button>
    </div>
    <div id="vid-node-health" style="margin-bottom:12px"></div>
    <div class="card" style="margin-bottom:16px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <div style="font-weight:600">&#9889; Generate Video</div>
        <button type="button" class="btn-sm" id="vid-suggest-btn" onclick="suggestVideoPrompt()" title="Uses the local LLM (LM Studio on the GPU box) to expand a short idea into a rich, detailed video prompt.">&#10024; Enhance</button>
      </div>
      <textarea id="vid-prompt" placeholder="Describe the video you want to generate&hellip; e.g. 'a golden retriever running on a sunny beach, slow motion'"
        style="width:100%;min-height:90px;padding:10px 12px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:8px;resize:vertical;font-family:inherit;font-size:.9rem;box-sizing:border-box"></textarea>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:12px">
        <label style="font-size:.8rem;color:var(--muted)">Model ${hlp('The text-to-video model that renders the clip. Wan 2.1 is the fast default; others trade speed for quality/length. Only installed models appear — download more in the Video Models section below.')}
          <select id="vid-model" onchange="onVideoModelChange()" style="width:100%;margin-top:4px;padding:7px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px">
            ${modelOptions}
          </select>
        </label>
        <label style="font-size:.8rem;color:var(--muted)">Resolution ${hlp('Output frame size & aspect ratio. Landscape (832×480) suits YouTube/desktop, portrait (480×832) suits Reels/TikTok, square suits feeds. Higher resolution = slower and more VRAM.')}
          <select id="vid-res" style="width:100%;margin-top:4px;padding:7px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px">
            <option value="832x480">832&times;480 (16:9 landscape)</option>
            <option value="480x832">480&times;832 (9:16 portrait)</option>
            <option value="512x512">512&times;512 (square)</option>
          </select>
        </label>
        <label style="font-size:.8rem;color:var(--muted)">Duration ${hlp('Clip length, expressed as a frame count (~16 fps). More frames = longer video but much slower to render. For longer stories, use “Long Video (chain)” which stitches several clips.')}
          <select id="vid-frames" style="width:100%;margin-top:4px;padding:7px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px">
            <option value="25">&sim;1.5s (25 frames)</option>
            <option value="49" selected>&sim;3s (49 frames)</option>
            <option value="81">&sim;5s (81 frames)</option>
            <option value="121">&sim;7.5s (121 frames, slow)</option>
          </select>
        </label>
        <label style="font-size:.8rem;color:var(--muted)">Quality (steps) ${hlp('Denoising steps per frame. More = cleaner, more coherent motion but slower. 20 is a good balance; 30 for final quality.')}
          <select id="vid-steps" style="width:100%;margin-top:4px;padding:7px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px">
            <option value="15">15 &mdash; fast</option>
            <option value="20" selected>20 &mdash; balanced</option>
            <option value="30">30 &mdash; best quality</option>
          </select>
        </label>
      </div>
      <div id="vid-model-hint" style="margin-top:8px;font-size:.76rem;color:var(--muted)"></div>
      <div style="margin-top:12px;border-top:1px solid var(--border);padding-top:10px">
        <label style="font-size:.82rem;color:var(--text);display:flex;align-items:center;gap:8px;cursor:pointer">
          <input type="checkbox" id="vid-audio-en" style="width:auto" onchange="document.getElementById('vid-audio-settings').style.display=this.checked?'block':'none'">
          &#128266; Generate with audio ${hlp('After the video renders, a layered soundtrack is generated on the GPU node and mixed onto the clip automatically: background music, spoken narration (TTS) and optional sound effects — the same layers, engines and volume controls as the chain builder. Off = current behavior (silent video).')}
        </label>
        <div id="vid-audio-settings" style="display:none;margin-top:8px;background:var(--surface2,#16161f);border:1px solid var(--border);border-radius:8px;padding:10px">
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px">
            <div>
              <label style="font-size:.78rem;color:var(--text);display:flex;align-items:center;gap:6px;cursor:pointer">
                <input type="checkbox" id="vid-aud-music" checked style="width:auto"> &#127925; Music bed</label>
              <select id="vid-aud-music-engine" style="width:100%;margin-top:6px;padding:6px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;font-size:.78rem">
                <option value="musicgen" selected>MusicGen (fast)</option>
                <option value="musicgen_med">MusicGen Medium (richer)</option>
                <option value="stable_audio">Stable Audio Open (hi-fi, needs HF token)</option>
                <option value="acestep">ACE-Step (songs w/ vocals)</option>
              </select>
              <label style="font-size:.7rem;color:var(--muted);display:block;margin-top:6px">Volume
                <input type="range" id="vid-aud-music-vol" min="0" max="1" step="0.02" value="0.28" style="width:100%"
                  oninput="document.getElementById('vid-aud-music-vol-val').textContent=this.value">
                <span id="vid-aud-music-vol-val">0.28</span></label>
            </div>
            <div>
              <label style="font-size:.78rem;color:var(--text);display:flex;align-items:center;gap:6px;cursor:pointer">
                <input type="checkbox" id="vid-aud-voice" checked style="width:auto"> &#128483;&#65039; Narration (TTS)</label>
              <select id="vid-aud-voice-engine" style="width:100%;margin-top:6px;padding:6px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;font-size:.78rem">
                <option value="mms_tts" selected>MMS-TTS (voice narration)</option>
              </select>
              <label style="font-size:.7rem;color:var(--muted);display:block;margin-top:6px">Volume
                <input type="range" id="vid-aud-voice-vol" min="0" max="1.5" step="0.05" value="1.0" style="width:100%"
                  oninput="document.getElementById('vid-aud-voice-vol-val').textContent=this.value">
                <span id="vid-aud-voice-vol-val">1.0</span></label>
            </div>
            <div>
              <label style="font-size:.78rem;color:var(--text);display:flex;align-items:center;gap:6px;cursor:pointer">
                <input type="checkbox" id="vid-aud-sfx" style="width:auto"> &#128165; Sound effects</label>
              <div style="font-size:.68rem;color:var(--muted);margin-top:6px">One short effect matched to the prompt (slower — one extra clip).</div>
              <label style="font-size:.7rem;color:var(--muted);display:block;margin-top:6px">Volume
                <input type="range" id="vid-aud-sfx-vol" min="0" max="1.5" step="0.05" value="0.6" style="width:100%"
                  oninput="document.getElementById('vid-aud-sfx-vol-val').textContent=this.value">
                <span id="vid-aud-sfx-vol-val">0.6</span></label>
            </div>
          </div>
          <input id="vid-audio-music" placeholder="Music vibe (optional — default: derived from the video prompt)" style="width:100%;margin-top:10px;margin-bottom:6px;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:.78rem;box-sizing:border-box">
          <textarea id="vid-audio-narration" placeholder="Narration to speak over it (optional — empty = the video prompt is read)" rows="2" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:.78rem;box-sizing:border-box;resize:vertical"></textarea>
          <div style="font-size:.68rem;color:var(--muted);margin-top:4px">Music loops under the clip; narration plays on top; SFX layers in — same mixer as chain audio. Adds ~1 min after the render.</div>
        </div>
      </div>
      <div style="margin-top:12px;font-size:.78rem;color:var(--muted)">
        &#9432; Download additional video models in the <a href="#" onclick="navigate('models');return false;" style="color:var(--accent);">Models tab</a>. First gen with a new model downloads it automatically (~5&ndash;10&nbsp;min).
        &nbsp;&bull;&nbsp; Want a <strong>longer video (10s&ndash;30s)?</strong> Use <a href="#" onclick="showChainBuilder();return false;" style="color:#6c63ff;font-weight:600">&#128279; Chain Builder</a> to stitch multiple segments together.
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:12px">
        <button id="vid-gen-btn" style="width:auto;padding:10px 28px" onclick="submitVideoGen()">&#9889; Generate (~5s max)</button>
        <button style="width:auto;padding:10px 22px;background:#6c63ff;border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer" onclick="showChainBuilder()">&#128279; Long Video (chain)</button>
      </div>
    </div>
    <div id="vid-gallery"></div>
    <div id="chain-gallery"></div>
  `;
  await refreshVideoGallery();
  await refreshChainGallery();
  checkVideoNodeHealth();
  onVideoModelChange();   // set steps options for the initially-selected model
}

// Adjust the Quality (steps) options to what the selected model actually wants —
// LTX likes 6–10, CogVideoX wants ~50, Wan ~20. Prevents bad output from wrong steps.
// If the owner has tuned this model (Models tab → Video Models → ⚙ Tune defaults),
// their saved values pre-select resolution/duration/steps; otherwise everything
// behaves exactly as before (rec_steps preselected, static res/frames defaults).
function onVideoModelChange() {
  const sel = document.getElementById('vid-model');
  const stepsSel = document.getElementById('vid-steps');
  const hint = document.getElementById('vid-model-hint');
  if (!sel || !stepsSel) return;
  const m = (window._videoModels || []).find(x => x.model_id === sel.value);
  const ov = (m && m.gen_overrides) || {};   // owner's per-model tuning (may be empty)
  if (m && Array.isArray(m.steps_options) && m.steps_options.length) {
    const rec = m.rec_steps || m.steps_options[0];
    const want = ov.steps || rec;            // tuned value wins over the recommendation
    const opts = m.steps_options.includes(want) ? m.steps_options
               : [...m.steps_options, want].sort((a, b) => a - b);
    stepsSel.innerHTML = opts.map(s => {
      const tag = s === want && ov.steps ? ' — your default'
                : s === rec ? ' — recommended' : (s < rec ? ' — faster' : ' — sharper');
      return `<option value="${s}"${s === want ? ' selected' : ''}>${s}${tag}</option>`;
    }).join('');
  } else if (ov.steps) {
    _selectOrAddOption(stepsSel, String(ov.steps), `${ov.steps} — your default`);
  }
  if (ov.width && ov.height) {
    _selectOrAddOption(document.getElementById('vid-res'), `${ov.width}x${ov.height}`,
                       `${ov.width}×${ov.height} (your default)`);
  }
  if (ov.num_frames) {
    _selectOrAddOption(document.getElementById('vid-frames'), String(ov.num_frames),
                       `${ov.num_frames} frames (your default)`);
  }
  if (hint && m) {
    const tuned = Object.keys(ov).length ? ' · ⚙ using your tuned defaults' : '';
    hint.innerHTML = `💡 <b>${esc(m.label)}</b>: ${esc(m.style || '')}${m.vram ? ' · needs ' + esc(m.vram) + ' VRAM' : ''}${m.note ? ' · ' + esc(m.note) : ''}${tuned}`;
  }
}

// Select `value` in a <select>, appending an <option> for it when missing.
function _selectOrAddOption(sel, value, label) {
  if (!sel) return;
  if (![...sel.options].some(o => o.value === value)) {
    const o = document.createElement('option');
    o.value = value; o.textContent = label || value;
    sel.appendChild(o);
  }
  sel.value = value;
}
window.onVideoModelChange = onVideoModelChange;

async function checkVideoNodeHealth() {
  const el = document.getElementById('vid-node-health');
  if (!el) return;
  el.innerHTML = `<div style="font-size:.78rem;color:var(--muted)">⏳ Checking GPU node…</div>`;
  try {
    const h = await api('/api/video-health');
    const btn = document.getElementById('vid-gen-btn');
    if (h.ok) {
      el.innerHTML = `<div style="font-size:.78rem;color:var(--green)">✅ GPU node ${esc(h.gpu_host || '')} ready.</div>`;
    } else {
      el.innerHTML = `<div style="background:#1a0f0f;border:1px solid #ef444450;border-radius:8px;padding:10px 12px;font-size:.82rem;color:#fca5a5">
        ⚠️ <b>Video generation unavailable:</b> ${esc(h.message)}
        <button class="btn-sm" style="margin-left:8px" onclick="checkVideoNodeHealth()">🔄 Re-check</button></div>`;
      if (btn) btn.title = 'GPU node not reachable — see the banner above';
    }
  } catch (e) {
    el.innerHTML = `<div style="font-size:.78rem;color:var(--muted)">Couldn't check GPU node: ${esc(e.message)}</div>`;
  }
}
window.checkVideoNodeHealth = checkVideoNodeHealth;

/* Fill an audio input/textarea with AI-suggested text — but only when the
   owner hasn't typed anything there, so we never silently clobber their own
   words. The fields exist (hidden) even while "Generate with audio" is off,
   so the text is already waiting when they tick the toggle.
   Shared with videos-chains.js (classic scripts, one global scope). */
function _fillAudioIfEmpty(id, text) {
  const el = document.getElementById(id);
  if (!el || !text || !String(text).trim()) return false;
  if (el.value.trim() !== '') return false;   // owner's text wins
  el.value = String(text).trim();
  return true;
}
window._fillAudioIfEmpty = _fillAudioIfEmpty;

async function suggestVideoPrompt() {
  const topic = prompt('What should the video be about? (optional — leave blank for random)');
  if (topic === null) return; // cancelled
  const btn = document.getElementById('vid-suggest-btn');
  if (btn) { btn.disabled = true; btn.textContent = '\u23F3 Thinking\u2026'; }
  try {
    const r = await fetch(API + '/api/videos/chain-prompts', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({concept: topic || 'a stunning cinematic moment', num_segments: 1, style: 'cinematic'})
    });
    if (!r.ok) throw new Error(await r.text());
    const {task_id} = await r.json();
    for (let i = 0; i < 30; i++) {
      await new Promise(res => setTimeout(res, 2000));
      const pr = await fetch(`${API}/api/task/${task_id}`);
      const pt = await pr.json();
      if (pt.status === 'done' && pt.result?.prompts?.length) {
        document.getElementById('vid-prompt').value = pt.result.prompts[0];
        // Same-pass matching audio (optional keys — a prompts-only result,
        // e.g. from the legacy parser fallback, changes nothing here).
        const gotMusic = _fillAudioIfEmpty('vid-audio-music', pt.result.music);
        const gotVoice = _fillAudioIfEmpty('vid-audio-narration', (pt.result.narrations || [])[0]);
        toast(gotMusic || gotVoice ? 'Prompt + matching audio generated!' : 'Prompt generated!');
        break;
      }
      if (pt.status === 'failed') { toast('LLM failed — try again', 'error'); break; }
    }
  } catch(e) { toast('Error: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.textContent = '\u2728 Suggest Prompt'; } }
}

async function submitVideoGen() {
  const prompt   = document.getElementById('vid-prompt').value.trim();
  if (!prompt) { toast('Enter a prompt first', 'warn'); return; }
  const res      = document.getElementById('vid-res').value;
  const [w, h]   = res.split('x').map(Number);
  const frames   = parseInt(document.getElementById('vid-frames').value);
  const steps    = parseInt(document.getElementById('vid-steps').value);
  const modelSel = document.getElementById('vid-model');
  const model_id = modelSel ? modelSel.value : 'Wan-AI/Wan2.1-T2V-1.3B-Diffusers';
  const audio_enabled  = !!document.getElementById('vid-audio-en')?.checked;
  const audio_settings = audio_enabled ? _vidAudioSettings() : null;
  const music_prompt   = audio_settings ? audio_settings.music_prompt : '';
  const narration      = audio_settings ? audio_settings.narration : '';
  const btn      = document.getElementById('vid-gen-btn');
  btn.disabled = true; btn.textContent = '\u23F3 Queued\u2026';
  try {
    const r = await fetch(API + '/api/videos/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prompt, width: w, height: h, num_frames: frames, steps, model_id,
                            audio_enabled, audio_settings, music_prompt, narration})
    });
    if (!r.ok) throw new Error(await r.text());
    toast('Video queued! Generating on RTX 3060\u2026');
    document.getElementById('vid-prompt').value = '';
    await refreshVideoGallery();
  } catch(e) {
    toast('Error: ' + e.message, 'error');
    btn.disabled = false; btn.textContent = '\u26A1 Generate';
  }
}

/* Read the single-video audio panel into the audio_settings payload — the
   exact keys of videos-chains.js _chainAudioSettings() / the server's
   DEFAULT_CHAIN_AUDIO, so one video's "Generate with audio" carries the same
   layers/engines/volumes as a chain's. */
function _vidAudioSettings() {
  const val = (id, d) => { const e = document.getElementById(id); return e ? e.value : d; };
  const chk = id => !!document.getElementById(id)?.checked;
  return {
    music: chk('vid-aud-music'),
    voice: chk('vid-aud-voice'),
    sfx:   chk('vid-aud-sfx'),
    music_volume: parseFloat(val('vid-aud-music-vol', '0.28')) || 0.28,
    voice_volume: parseFloat(val('vid-aud-voice-vol', '1')) || 1.0,
    sfx_volume:   parseFloat(val('vid-aud-sfx-vol', '0.6')) || 0.6,
    music_engine: val('vid-aud-music-engine', 'musicgen') || 'musicgen',
    voice_engine: val('vid-aud-voice-engine', 'mms_tts') || 'mms_tts',
    music_prompt: (val('vid-audio-music', '') || '').trim(),
    narration:    (val('vid-audio-narration', '') || '').trim(),
  };
}

async function refreshVideoGallery() {
  const el = document.getElementById('vid-gallery');
  if (!el) return;
  if (_videosPollTimer) { clearTimeout(_videosPollTimer); _videosPollTimer = null; }
  const btn = document.getElementById('vid-gen-btn');
  if (btn) { btn.disabled = false; btn.textContent = '\u26A1 Generate'; }
  let videos = [];
  try {
    const r = await fetch(API + '/api/videos');
    videos = await r.json();
  } catch { return; }
  const hasActive = videos.some(v => ['queued','generating'].includes(v.status) || ['queued','generating'].includes(v.audio_status));
  if (!videos.length) {
    stopMediaIn(el);
    el.innerHTML = '<div style="text-align:center;color:var(--muted);padding:60px 20px">&#127916; No videos yet &mdash; generate your first one above!</div>';
  } else {
    // syncCards: the 2 s poll only re-renders cards whose data changed —
    // unchanged players are never rebuilt (no flicker, no interrupted loads).
    let grid = el.querySelector('[data-cards="videos"]');
    if (!grid) {
      stopMediaIn(el);
      el.innerHTML = '<div data-cards="videos" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px"></div>';
      grid = el.firstElementChild;
    }
    syncCards(grid, videos, videoCard);
  }
  if (hasActive) _videosPollTimer = setTimeout(refreshVideoGallery, 2000);
}

function videoCard(v) {
  const created  = new Date(v.created_at + 'Z').toLocaleString();
  const filename = v.video_path ? v.video_path.split('/').pop() : null;
  const vidSrc   = filename ? `${API}/videos/${encodeURIComponent(filename)}` : null;
  const badges   = {
    queued:     '<span style="background:var(--warn);color:#000;padding:2px 8px;border-radius:12px;font-size:.75rem">&#9203; Queued</span>',
    generating: '<span style="background:#6c63ff;color:#fff;padding:2px 8px;border-radius:12px;font-size:.75rem">&#127916; Generating&hellip;</span>',
    done:       '<span style="background:var(--green);color:#000;padding:2px 8px;border-radius:12px;font-size:.75rem">&#10003; Done</span>',
    failed:     '<span style="background:#ef4444;color:#fff;padding:2px 8px;border-radius:12px;font-size:.75rem">&#10060; Failed</span>',
  };
  const badge   = badges[v.status] || v.status;
  const audioName = v.audio_status === 'done' && v.audio_path ? v.audio_path.split('/').pop() : null;
  const audioSrc  = audioName ? `${API}/videos/${encodeURIComponent(audioName)}` : null;
  const playSrc   = audioSrc || vidSrc;
  const preview = v.status === 'done' && playSrc ? `
    <video controls loop ${audioSrc ? '' : 'muted'} preload="metadata"
      style="width:100%;border-radius:8px;background:#000;max-height:220px;display:block">
      <source src="${playSrc}" type="video/mp4" onerror="_srcRetry(this)">
    </video>${audioSrc ? '<div style="font-size:.68rem;color:#22c55e;margin-top:3px">&#128266; with sound</div>' : ''}` :
  v.status === 'generating' ? (() => {
    const pct = Math.max(2, Math.min(100, v.progress || 0));
    const msg = v.progress_msg || 'Generating on RTX 3060…';
    return `
    <div style="background:#111;border-radius:8px;padding:20px 16px;min-height:150px;display:flex;flex-direction:column;justify-content:center;gap:12px">
      <div style="display:flex;align-items:center;gap:12px">
        <div style="font-size:1.9rem">&#127902;</div>
        <div style="flex:1">
          <div style="color:var(--text);font-size:.84rem;margin-bottom:7px">${esc(msg)}</div>
          <div style="background:#000;border-radius:6px;height:10px;overflow:hidden">
            <div style="height:100%;width:${pct}%;background:linear-gradient(90deg,#6c63ff,#a855f7);transition:width .5s ease"></div>
          </div>
        </div>
        <div style="color:var(--muted);font-size:.82rem;min-width:36px;text-align:right;font-variant-numeric:tabular-nums">${pct}%</div>
      </div>
    </div>`;
  })() :
  v.status === 'queued' ? `
    <div style="background:#111;border-radius:8px;height:150px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:10px">
      <div style="font-size:2.5rem">&#9203;</div>
      <div style="color:var(--muted);font-size:.85rem">Waiting in queue&hellip;</div>
    </div>` : `
    <div style="background:#1a0f0f;border:1px solid #ef444440;border-radius:8px;padding:12px;min-height:120px;display:flex;flex-direction:column;justify-content:center;gap:6px">
      <span style="color:#ef4444;font-size:.9rem;font-weight:600">&#10060; Generation failed</span>
      <div style="color:#fca5a5;font-size:.73rem;max-height:110px;overflow:auto;white-space:pre-wrap;font-family:monospace;line-height:1.35">${esc(v.error || 'No error detail was captured.')}</div>
    </div>`;
  const dlBtn = v.status === 'done' && playSrc ? `<a href="${playSrc}" download style="text-decoration:none"><button style="width:auto;padding:5px 12px;font-size:.8rem;background:var(--accent2,#0ea5e9);margin-top:0">&#11015; Download</button></a>` : '';
  const retryBtn = v.status === 'failed' ? `<button onclick="retryVideo(${v.id})" style="width:auto;padding:5px 12px;font-size:.8rem;background:#3b82f620;color:#3b82f6;border:1px solid #3b82f650;margin-top:0">&#128260; Retry</button>` : '';
  const cancelBtn = ['queued','generating'].includes(v.status) ? `<button onclick="cancelVideo(${v.id})" style="width:auto;padding:5px 12px;font-size:.8rem;background:#f59e0b20;color:#f59e0b;border:1px solid #f59e0b50;margin-top:0">&#9209;&#65039; Cancel</button>` : '';
  return `
    <div class="card">
      ${preview}
      <div style="margin-top:10px;display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
        ${badge}
        <span style="font-size:.75rem;color:var(--muted)">${v.model_id ? v.model_id.split('/').pop() + ' &bull; ' : ''}${v.width}&times;${v.height} &bull; ${v.num_frames}f &bull; ${v.steps} steps</span>
      </div>
      <div style="margin-top:8px;font-size:.85rem;color:var(--text);overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical" title="${esc(v.prompt)}">${esc(v.prompt)}</div>
      <div style="margin-top:5px;font-size:.73rem;color:var(--muted)">${created}</div>
      ${_videoAudioSection(v)}
      <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
        ${dlBtn}${retryBtn}${cancelBtn}
        <button onclick="deleteVideo(${v.id})" style="width:auto;padding:5px 12px;font-size:.8rem;background:#ef444420;color:#ef4444;border:1px solid #ef444450;margin-top:0">&#128465; Delete</button>
      </div>
    </div>`;
}

// Video → audio bridge UI on each done card.
function _videoAudioSection(v) {
  if (v.status !== 'done') return '';
  const a = v.audio_status;
  if (a === 'queued' || a === 'generating') {
    return `<div style="margin-top:8px;font-size:.76rem;color:#a855f7">&#127925; Adding sound&hellip; ${esc(v.progress_msg || '')}</div>`;
  }
  if (a === 'failed') {
    return `<div style="margin-top:8px;font-size:.74rem;color:var(--warn)">&#127925; Sound failed: ${esc(v.audio_error || 'unknown')}
      <button onclick="toggleSoundForm(${v.id})" style="margin-left:6px;padding:2px 8px;font-size:.72rem;background:#a855f720;color:#a855f7;border:1px solid #a855f750;border-radius:5px;cursor:pointer">Try again</button></div>
      ${_soundForm(v.id)}`;
  }
  // done-with-sound → offer a redo; no sound yet → offer add
  const label = a === 'done' ? '&#127925; Redo sound' : '&#127925; Add sound (music + voice)';
  return `<div style="margin-top:8px">
      <button onclick="toggleSoundForm(${v.id})" style="padding:4px 12px;font-size:.76rem;background:#a855f720;color:#a855f7;border:1px solid #a855f750;border-radius:6px;cursor:pointer">${label}</button>
    </div>${_soundForm(v.id)}`;
}

function _soundForm(id) {
  return `<div id="sound-form-${id}" style="display:none;margin-top:8px;background:var(--surface2,#16161f);border:1px solid var(--border);border-radius:8px;padding:10px">
    <input id="snd-music-${id}" placeholder="Music vibe (e.g. upbeat playful chiptune)" style="width:100%;margin-bottom:6px;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:.78rem;box-sizing:border-box">
    <textarea id="snd-voice-${id}" placeholder="Narration to speak (optional)" rows="2" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:.78rem;box-sizing:border-box;resize:vertical"></textarea>
    <div style="font-size:.68rem;color:var(--muted);margin:4px 0">Music loops under the clip; narration plays on top. First run downloads the models (~1 min extra).</div>
    <button onclick="addVideoSound(${id})" style="padding:5px 14px;font-size:.78rem;background:#a855f7;color:#fff;border:none;border-radius:6px;cursor:pointer">&#127925; Generate &amp; add</button>
  </div>`;
}

function toggleSoundForm(id) {
  const f = document.getElementById('sound-form-' + id);
  if (f) f.style.display = f.style.display === 'none' ? 'block' : 'none';
}
window.toggleSoundForm = toggleSoundForm;

async function addVideoSound(id) {
  const music = document.getElementById('snd-music-' + id)?.value.trim() || '';
  const voice = document.getElementById('snd-voice-' + id)?.value.trim() || '';
  try {
    const r = await fetch(`${API}/api/videos/${id}/add-audio`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ music_prompt: music, narration: voice })
    });
    if (!r.ok) throw new Error(await r.text());
    toast('🎵 Generating sound — this takes ~1 min');
    refreshVideoGallery();
  } catch (e) { toast('Add sound failed: ' + e.message, 'error'); }
}
window.addVideoSound = addVideoSound;

async function retryVideo(id) {
  try {
    const r = await fetch(`${API}/api/videos/${id}/retry`, {method:'POST'});
    if (!r.ok) throw new Error(await r.text());
    toast('Re-queued — generating again'); refreshVideoGallery();
  } catch(e) { toast('Retry failed: ' + e.message, 'error'); }
}

async function cancelVideo(id) {
  if (!confirm('Cancel this video generation?')) return;
  try {
    const r = await fetch(`${API}/api/videos/${id}/cancel`, {method:'POST'});
    if (!r.ok) throw new Error(await r.text());
    toast('Cancelled'); refreshVideoGallery();
  } catch(e) { toast('Cancel failed: ' + e.message, 'error'); }
}

async function deleteVideo(id) {
  if (!confirm('Delete this video?')) return;
  const r = await fetch(`${API}/api/videos/${id}`, {method:'DELETE'});
  if (r.ok) { toast('Video deleted'); refreshVideoGallery(); }
  else toast('Delete failed', 'error');
}


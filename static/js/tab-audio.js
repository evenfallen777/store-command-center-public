/* Music / Audio tab — generate standalone music + voice clips on the GPU node. */
let _audioPollTimer = null;
let _audioEngines = [];

async function renderAudio() {
  const el = viewRoot();
  try { _audioEngines = await api('/api/audio/engines'); } catch { _audioEngines = []; }
  const opts = _audioEngines.map(e =>
    `<option value="${esc(e.key)}"${e.key === 'musicgen' ? ' selected' : ''}>${esc(e.label)}</option>`).join('');
  el.innerHTML = `
    <div class="section-header">
      <div>
        <div class="section-title">&#127925; Music / Audio</div>
        <div class="section-sub">Generate music &amp; voice on the RTX 3060 node</div>
      </div>
    </div>
    <div class="card" style="margin-bottom:16px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <div style="font-weight:600">&#9889; Generate audio</div>
        <button type="button" class="btn-sm" id="aud-enhance-btn" onclick="enhanceAudioPrompt()">&#10024; Enhance</button>
      </div>
      <textarea id="aud-prompt" placeholder="Describe the music (e.g. 'dreamy lo-fi hip hop, mellow piano, rainy night') — or, for Voice, the words to speak. Type a rough idea and hit ✨ Enhance."
        style="width:100%;min-height:80px;padding:10px 12px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:8px;resize:vertical;font-family:inherit;font-size:.9rem;box-sizing:border-box"></textarea>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-top:12px">
        <label style="font-size:.8rem;color:var(--muted)">Engine ${hlp('Which model generates the audio. Music engines: MusicGen, ACE-Step (can sing your lyrics), Stable Audio. Voice: MMS-TTS reads text aloud. Options depend on what’s installed on the node — download more in Audio Models below.')}
          <select id="aud-engine" onchange="_audEngineChange()" style="width:100%;margin-top:4px;padding:7px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px">${opts}</select>
        </label>
        <label style="font-size:.8rem;color:var(--muted)" id="aud-dur-wrap">Length ${hlp('How many seconds of audio to generate. Longer = more time and VRAM; some engines cap the maximum. (For voice, length follows your text.)')}
          <select id="aud-duration" style="width:100%;margin-top:4px;padding:7px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px"></select>
        </label>
      </div>
      <div id="aud-engine-hint" style="margin-top:8px;font-size:.75rem;color:var(--muted)"></div>
      <div id="aud-lyrics-wrap" style="display:none;margin-top:12px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
          <div style="font-size:.8rem;color:var(--muted)">&#127908; Lyrics <span style="font-weight:400">(optional — sung by ACE-Step; leave empty for instrumental)</span></div>
          <button type="button" class="btn-sm" id="aud-lyrics-btn" onclick="writeAudioLyrics()">&#127908; Write lyrics</button>
        </div>
        <textarea id="aud-lyrics" rows="5" placeholder="[verse]&#10;Walking down the street on a sunny day&#10;Everything is going my way&#10;[chorus]&#10;Oh what a feeling, oh what a day"
          style="width:100%;padding:10px 12px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:8px;resize:vertical;font-family:inherit;font-size:.85rem;box-sizing:border-box"></textarea>
        <div style="font-size:.68rem;color:var(--muted);margin-top:3px">Structure with <code>[verse]</code>, <code>[chorus]</code>, <code>[bridge]</code> tags. The <b>Prompt</b> above sets the genre/voice/mood.</div>
      </div>
      <button id="aud-gen-btn" style="width:auto;padding:10px 26px;margin-top:12px" onclick="submitAudio()">&#9889; Generate</button>
    </div>
    <div id="aud-gallery"></div>`;
  _audEngineChange();
  resumeAudioEnhance();   // re-attach if an enhance was started here and we wandered off
  await refreshAudioGallery();
}
window.renderAudio = renderAudio;

// Per-engine length options (seconds). ACE-Step is the one built for 3m+ songs.
const _AUD_DURATIONS = {
  musicgen:     { opts: [5, 8, 15, 30, 45, 60], rec: 8,   maxNote: 'MusicGen sounds best up to ~30s; longer drifts.' },
  musicgen_med: { opts: [5, 8, 15, 30, 45, 60], rec: 8,   maxNote: 'MusicGen sounds best up to ~30s; longer drifts.' },
  stable_audio: { opts: [8, 15, 30, 47],         rec: 30,  maxNote: 'Stable Audio Open maxes out around 47s.' },
  acestep:      { opts: [30, 60, 90, 120, 150, 180, 210, 240], rec: 120, maxNote: 'ACE-Step makes full songs up to 4 min.' },
};
function _fmtSecs(s) { return s >= 60 ? `${Math.floor(s/60)}m${s%60?(' '+(s%60)+'s'):''}` : `${s}s`; }

function _audEngineChange() {
  const key = document.getElementById('aud-engine')?.value;
  const e = _audioEngines.find(x => x.key === key);
  const hint = document.getElementById('aud-engine-hint');
  const durWrap = document.getElementById('aud-dur-wrap');
  const durSel = document.getElementById('aud-duration');
  const promptEl = document.getElementById('aud-prompt');
  const isVoice = e && e.kind === 'voice';
  const lyricsWrap = document.getElementById('aud-lyrics-wrap');
  if (lyricsWrap) lyricsWrap.style.display = (key === 'acestep') ? 'block' : 'none';
  if (durWrap) durWrap.style.display = isVoice ? 'none' : '';
  if (promptEl) promptEl.placeholder = isVoice
    ? "The words to speak, e.g. 'Welcome to our shop — everything is 20 percent off today!'"
    : "Describe the music (e.g. 'dreamy lo-fi hip hop, mellow piano, rainy night')";
  // Rebuild length options for this engine.
  if (durSel && !isVoice) {
    const d = _AUD_DURATIONS[key] || _AUD_DURATIONS.musicgen;
    durSel.innerHTML = d.opts.map(s =>
      `<option value="${s}"${s === d.rec ? ' selected' : ''}>${_fmtSecs(s)}</option>`).join('');
  }
  if (hint && e) {
    const d = _AUD_DURATIONS[key];
    let extra = '';
    if (key === 'acestep') extra = ' · full songs with vocals & lyrics · first run downloads a large model';
    if (key === 'stable_audio') extra = ' · hi-fi instrumental · needs a Hugging Face token with the license accepted';
    hint.textContent = `💡 ${e.label}${extra}${d && !isVoice ? ' · ' + d.maxNote : ''}`;
  }
}
window._audEngineChange = _audEngineChange;

const _audBusy = (on) => { const b = document.getElementById('aud-enhance-btn'); if (b) { b.disabled = on; b.innerHTML = on ? '⏳ Enhancing…' : '✨ Enhance'; } };
// Runs server-side; persists across tab switches / reload (see enhanceStart).
async function enhanceAudioPrompt() {
  const ta = document.getElementById('aud-prompt');
  const raw = ta ? ta.value.trim() : '';
  if (!raw) { toast('Type a rough idea first', 'warn'); return; }
  const key = document.getElementById('aud-engine')?.value;
  const kind = (_audioEngines.find(x => x.key === key) || {}).kind || 'music';
  enhanceStart('aud-prompt',
    async () => (await api('/api/audio/enhance-prompt', { method: 'POST', body: JSON.stringify({ prompt: raw, kind }) })).task_id,
    _audBusy);
}
window.enhanceAudioPrompt = enhanceAudioPrompt;

const _lyrBusy = (on) => { const b = document.getElementById('aud-lyrics-btn'); if (b) { b.disabled = on; b.innerHTML = on ? '⏳ Writing…' : '🎤 Write lyrics'; } };
// Write ACE-Step lyrics from the song idea + the genre/mood prompt. Runs server-side
// on the unified queue and drops the result into the lyrics box (persists across tabs).
async function writeAudioLyrics() {
  const style = (document.getElementById('aud-prompt')?.value || '').trim();
  const seed  = (document.getElementById('aud-lyrics')?.value || '').trim();
  if (!style && !seed) { toast('Describe the song in the prompt (genre/mood), or jot a theme in the lyrics box', 'warn'); return; }
  enhanceStart('aud-lyrics',
    async () => (await api('/api/audio/generate-lyrics', { method: 'POST', body: JSON.stringify({ prompt: seed, style }) })).task_id,
    _lyrBusy);
}
window.writeAudioLyrics = writeAudioLyrics;

// re-attach a pending enhance / lyrics job when the Audio tab (re)renders
function resumeAudioEnhance() { enhanceResume('aud-prompt', _audBusy); enhanceResume('aud-lyrics', _lyrBusy); }

async function submitAudio() {
  const prompt = document.getElementById('aud-prompt').value.trim();
  if (!prompt) { toast('Enter a prompt first', 'warn'); return; }
  const engine = document.getElementById('aud-engine').value;
  const duration = parseInt(document.getElementById('aud-duration')?.value || '8');
  const lyrics = (engine === 'acestep') ? (document.getElementById('aud-lyrics')?.value.trim() || '') : '';
  const btn = document.getElementById('aud-gen-btn');
  btn.disabled = true; btn.textContent = '⏳ Queued…';
  try {
    const r = await fetch(API + '/api/audio/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, engine, duration, lyrics })
    });
    if (!r.ok) throw new Error(await r.text());
    toast('Queued — generating on the node');
    document.getElementById('aud-prompt').value = '';
    await refreshAudioGallery();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { btn.disabled = false; btn.innerHTML = '⚡ Generate'; }
}
window.submitAudio = submitAudio;

async function refreshAudioGallery() {
  const el = document.getElementById('aud-gallery');
  if (!el) return;
  if (_audioPollTimer) { clearTimeout(_audioPollTimer); _audioPollTimer = null; }
  let clips = [];
  try { clips = await api('/api/audio'); } catch { return; }
  const active = clips.some(c => ['queued', 'generating'].includes(c.status));
  el.innerHTML = clips.length
    ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px">${clips.map(audioCard).join('')}</div>`
    : '<div style="text-align:center;color:var(--muted);padding:50px 20px">🎵 No clips yet — generate one above.</div>';
  if (active) _audioPollTimer = setTimeout(refreshAudioGallery, 2500);
}
window.refreshAudioGallery = refreshAudioGallery;

function audioCard(c) {
  const name = c.audio_path ? c.audio_path.split('/').pop() : null;
  const src = name ? `${API}/videos/${encodeURIComponent(name)}` : null;
  const icon = c.kind === 'voice' ? '🗣️' : '🎵';
  let body;
  if (c.status === 'done' && src) {
    body = `<audio controls preload="none" style="width:100%;margin-top:6px"><source src="${src}"></audio>
      <a href="${src}" download style="text-decoration:none"><button style="width:auto;padding:4px 10px;font-size:.75rem;background:var(--accent2,#0ea5e9);margin-top:6px">⬇ Download</button></a>`;
  } else if (c.status === 'failed') {
    body = `<div style="margin-top:6px;font-size:.72rem;color:#fca5a5;white-space:pre-wrap;max-height:90px;overflow:auto;font-family:monospace">${esc(c.error || 'failed')}</div>`;
  } else {
    body = `<div style="margin-top:8px;font-size:.8rem;color:#a855f7">⏳ ${esc(c.progress_msg || c.status)}…</div>`;
  }
  const eng = (_audioEngines.find(e => e.key === c.engine) || {}).label || c.engine;
  return `<div class="card">
    <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
      <span style="font-size:.72rem;color:var(--muted)">${icon} ${esc(eng)}${c.kind==='music'?' · '+c.duration+'s':''}</span>
      <button onclick="deleteAudio(${c.id})" style="width:auto;padding:2px 8px;font-size:.72rem;background:#ef444420;color:#ef4444;border:1px solid #ef444450">🗑</button>
    </div>
    <div style="margin-top:6px;font-size:.85rem;color:var(--text);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden" title="${esc(c.prompt)}">${esc(c.prompt)}</div>
    ${body}
  </div>`;
}

async function deleteAudio(id) {
  if (!confirm('Delete this clip?')) return;
  const r = await fetch(`${API}/api/audio/${id}`, { method: 'DELETE' });
  if (r.ok) { toast('Deleted'); refreshAudioGallery(); } else toast('Delete failed', 'error');
}
window.deleteAudio = deleteAudio;

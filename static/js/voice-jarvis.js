'use strict';

/* ══ JARVIS VOICE — browser voice controller for the AI Assistant ══
   Ears + mouth around the EXISTING assistant (spec: app/VOICE.md). Flow:
   mic (getUserMedia + MediaRecorder opus/webm) → POST /api/voice/stt →
   {text} → the SAME /api/agent/chat + /api/agent/events calls tab-agent.js
   makes → final assistant reply → POST /api/voice/tts → play the audio.
   Trigger modes: ptt (hold the mic button or Space), wake ("hey jarvis",
   delegated to window.VoiceWake.startWake), open (VoiceWake.vadListen).
   VoiceWake missing → degrade to ptt. Barge-in: mic energy while Jarvis is
   speaking stops playback and starts a fresh capture.
   Public API: window.VoiceJarvis = { init(container), start(), stop(), setMode(m) } */

let _vjState = 'idle';        // idle | listening | thinking | speaking
let _vjOn = false;            // started (armed) vs stopped
let _vjMode = 'wake';         // ptt | wake | open (default from /api/voice/settings)
let _vjWakePhrase = 'hey jarvis';
let _vjSettingsLoaded = false;
let _vjGen = 0;               // generation counter — bumping it cancels every in-flight async step
let _vjStream = null;         // persistent mic MediaStream (released in stop())
let _vjRec = null;            // active MediaRecorder (one utterance at a time)
let _vjChunks = [];
let _vjAC = null;             // AudioContext for the barge-in energy meter
let _vjAnalyser = null;
let _vjSrcStream = null;      // stream the analyser source was built from
let _vjMeterTimer = null;
let _vjAudio = null;          // currently playing TTS <audio>
let _vjAudioUrl = null;
let _vjAudioDone = null;      // resolver for the playback-finished promise
let _vjMaxUttTimer = null;    // safety cap so a lost VAD callback can't record forever
let _vjPtt = false;           // ptt press currently held
let _vjConv = null;           // fallback conversation id (shares _agConv when tab-agent.js is loaded)
let _vjBox = null;            // widget root element
let _vjPopped = false;        // popped out → floats on document.body and follows across tabs
let _vjDockTarget = null;     // the in-tab container to dock back into
let _vjNode = false;          // node TTS session active (Kokoro loaded on the node, fast)
let _vjNodeBusy = false;      // activate/deactivate in flight
let _vjHideTimer = null;      // grace timer: unload the node after the tab stays hidden a while

/* ── css (self-contained: no external stylesheets/CDNs — store may be offline on LAN) ── */
function _vjCss() {
  if (document.getElementById('vj-css')) return;
  const st = document.createElement('style');
  st.id = 'vj-css';
  st.textContent = `
    .vj-widget { position:fixed; right:22px; bottom:84px; z-index:9000; display:flex;
      flex-direction:column; align-items:flex-end; gap:8px;
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif; font-size:13px; }
    .vj-panel { display:flex; flex-direction:column; align-items:flex-end; gap:6px; }
    .vj-pill { display:flex; align-items:center; gap:7px; background:var(--surface2,#1e2330);
      border:1px solid var(--border,#2a2f3d); border-radius:20px; padding:5px 12px;
      color:var(--muted,#64748b); font-size:.74rem; user-select:none; }
    .vj-dot { width:8px; height:8px; border-radius:50%; background:var(--muted,#64748b); flex-shrink:0; }
    .vj-widget[data-state="listening"] .vj-dot { background:var(--red,#ef4444);
      box-shadow:0 0 7px var(--red,#ef4444); animation:vjPulse 1.1s ease-in-out infinite; }
    .vj-widget[data-state="thinking"]  .vj-dot { background:var(--warn,#f59e0b);
      box-shadow:0 0 7px var(--warn,#f59e0b); animation:vjPulse 1.1s ease-in-out infinite; }
    .vj-widget[data-state="speaking"]  .vj-dot { background:var(--accent2,#00d4aa);
      box-shadow:0 0 7px var(--accent2,#00d4aa); animation:vjPulse 1.1s ease-in-out infinite; }
    @keyframes vjPulse { 0%,100% { opacity:1; transform:scale(1); } 50% { opacity:.4; transform:scale(.7); } }
    .vj-transcript { display:none; max-width:320px; max-height:200px; overflow-y:auto;
      background:var(--surface,#161a22); border:1px solid var(--border,#2a2f3d); border-radius:10px;
      padding:8px 11px; font-size:.76rem; line-height:1.5; color:var(--text,#e2e8f0);
      box-shadow:0 8px 24px rgba(0,0,0,.45); }
    .vj-transcript.show { display:block; }
    .vj-transcript div { margin:2px 0; word-break:break-word; white-space:pre-wrap; }
    .vj-transcript .vj-you    { color:var(--accent,#6c63ff); }
    .vj-transcript .vj-jarvis { color:var(--text,#e2e8f0); }
    .vj-transcript .vj-note   { color:var(--muted,#64748b); font-style:italic; font-size:.7rem; }
    .vj-modes { display:flex; gap:4px; }
    .vj-modes button { background:var(--surface2,#1e2330); border:1px solid var(--border,#2a2f3d);
      border-radius:12px; color:var(--muted,#64748b); font-size:.66rem; font-weight:600;
      padding:3px 9px; cursor:pointer; transition:all .15s; }
    .vj-modes button:hover { border-color:var(--accent,#6c63ff); color:var(--accent,#6c63ff); }
    .vj-modes button.on { background:var(--accent,#6c63ff); border-color:var(--accent,#6c63ff); color:#fff; }
    .vj-pop { background:none; border:1px solid var(--border,#2a2f3d); border-radius:8px;
      color:var(--muted,#64748b); font-size:.85rem; line-height:1; padding:3px 8px; cursor:pointer;
      transition:all .15s; align-self:center; }
    .vj-pop:hover { border-color:var(--accent,#6c63ff); color:var(--accent,#6c63ff); }
    .vj-turbo { background:none; border:1px solid var(--border,#2a2f3d); border-radius:8px;
      color:var(--muted,#64748b); font-size:.9rem; line-height:1; padding:3px 8px; cursor:pointer;
      transition:all .15s; align-self:center; }
    .vj-turbo:hover { border-color:var(--accent2,#00d4aa); color:var(--accent2,#00d4aa); }
    .vj-turbo[data-node="loading"] { color:var(--warn,#f59e0b); border-color:var(--warn,#f59e0b);
      animation:vjPulse 1s ease-in-out infinite; }
    .vj-turbo[data-node="ready"] { color:var(--green,#22c55e); border-color:var(--green,#22c55e);
      box-shadow:0 0 8px rgba(34,197,94,.55); }
    .vj-mic { width:54px; height:54px; border-radius:50%; border:none; cursor:pointer; font-size:1.45rem;
      background:linear-gradient(135deg,var(--accent,#6c63ff),#7c3aed); color:#fff;
      box-shadow:0 4px 16px rgba(0,0,0,.4); transition:transform .12s, box-shadow .15s;
      display:flex; align-items:center; justify-content:center; user-select:none; touch-action:none; }
    .vj-mic:hover { transform:scale(1.06); }
    .vj-mic:active { transform:scale(.96); }
    .vj-widget[data-state="listening"] .vj-mic { box-shadow:0 0 0 4px rgba(239,68,68,.35), 0 4px 16px rgba(0,0,0,.4);
      background:linear-gradient(135deg,var(--red,#ef4444),#b91c1c); }
    .vj-widget[data-state="speaking"]  .vj-mic { box-shadow:0 0 0 4px rgba(0,212,170,.3), 0 4px 16px rgba(0,0,0,.4); }
    .vj-widget.vj-off .vj-mic { filter:grayscale(.6); opacity:.75; }
    /* Inline mode — mounted as a native section bar inside the Assistant tab */
    .vj-widget.vj-inline { position:static; right:auto; bottom:auto; z-index:auto;
      flex-direction:row; align-items:center; gap:14px; width:100%;
      background:var(--surface2,#1e2330); border:1px solid var(--border,#2a2f3d);
      border-radius:12px; padding:9px 14px; margin:0 0 12px; }
    .vj-widget.vj-inline .vj-mic { order:-1; width:46px; height:46px; font-size:1.25rem;
      box-shadow:0 2px 10px rgba(0,0,0,.35); }
    .vj-widget.vj-inline .vj-panel { flex-direction:row; align-items:center; gap:12px; flex:1;
      flex-wrap:wrap; }
    .vj-widget.vj-inline .vj-pill { font-size:.8rem; }
    .vj-widget.vj-inline .vj-transcript { position:static; box-shadow:none; max-width:none;
      flex-basis:100%; max-height:110px; }`;
  document.head.appendChild(st);
}

/* ── small helpers ── */
function _vjSleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function _vjApi() { return (typeof API !== 'undefined') ? API : '/store'; }
function _vjToast(msg, type) { if (typeof toast === 'function') toast(msg, type || 'warn'); }

// window.VoiceWake is built by voice-wake.js (separate module). Feature-detect:
// no lib (or missing fns) → wake/open silently degrade to ptt.
function _vjWakeLib() {
  const W = window.VoiceWake;
  if (W && typeof W.startWake === 'function' && typeof W.vadListen === 'function') return W;
  return null;
}

// Conversation id: reuse tab-agent.js's live conversation when its script is
// loaded (its top-level `let _agConv` is a global binding), so voice turns land
// in the same on-screen chat; otherwise track our own.
function _vjGetConv() {
  try { if (typeof _agConv !== 'undefined' && _agConv) return _agConv; } catch (e) {}
  return _vjConv;
}
function _vjSetConv(id) {
  _vjConv = id;
  try { if (typeof _agConv !== 'undefined') _agConv = id; } catch (e) {}
}

/* ── widget UI ── */
function _vjInit(container) {
  // Docked → live inside the tab's container. Popped → float on document.body and stay
  // put across tab switches (that's the "follow me around the store" mode).
  const target = _vjPopped ? document.body : ((container && container.appendChild) ? container : document.body);
  if (_vjBox) {
    if (document.body.contains(_vjBox)) {          // alive → relocate only when docked (popped stays floating)
      if (!_vjPopped && _vjBox.parentElement !== target) target.appendChild(_vjBox);
      return;
    }
    try { _vjStop(); } catch (e) {}                // old box torn out by a tab re-render → clean up, then rebuild
    _vjBox = null;
  }
  _vjCss();
  const box = document.createElement('div');
  box.className = 'vj-widget vj-off' + ((container && !_vjPopped) ? ' vj-inline' : '');
  box.id = 'vj-widget';
  box.dataset.state = 'idle';
  box.innerHTML = `
    <div class="vj-panel">
      <div class="vj-transcript" id="vj-transcript"></div>
      <div class="vj-modes" id="vj-modes">
        <button data-m="ptt"  title="Push-to-talk — hold the mic button or Space">PTT</button>
        <button data-m="wake" title="Wake word — always listening for the phrase">Wake</button>
        <button data-m="open" title="Open mic — VAD detects speech start/stop">Open</button>
      </div>
      <div class="vj-pill"><span class="vj-dot"></span><span id="vj-pill-text">voice off — click mic</span></div>
      <button class="vj-turbo" id="vj-turbo" data-node="off" title="Activate fast node voice">&#9889;</button>
      <button class="vj-pop" id="vj-pop" title="Pop out — float and follow across tabs">&#8689;</button>
    </div>
    <button class="vj-mic" id="vj-mic" title="Jarvis — talk to the assistant">&#127908;</button>`;
  target.appendChild(box);
  _vjBox = box;
  if (container && container.appendChild) _vjDockTarget = container;   // remember where to dock back
  const popBtn = box.querySelector('#vj-pop');
  if (popBtn) {
    popBtn.textContent = _vjPopped ? '⤓' : '⤑';             // ⤓ dock : ⤑ pop
    popBtn.addEventListener('click', _vjTogglePop);
  }
  const turboBtn = box.querySelector('#vj-turbo');
  if (turboBtn) turboBtn.addEventListener('click', _vjToggleNode);
  _vjSyncNode();                                            // reflect current node session (green if already up)
  _vjBindLeave();                                           // unload the node on page/tab leave

  const mic = box.querySelector('#vj-mic');
  // ptt = press & hold; wake/open = click toggles the whole controller on/off
  mic.addEventListener('mousedown', e => { e.preventDefault(); if (_vjMode === 'ptt') _vjPttDown(); });
  mic.addEventListener('touchstart', e => { e.preventDefault(); if (_vjMode === 'ptt') _vjPttDown(); }, { passive: false });
  mic.addEventListener('mouseup', () => _vjPttUp());
  mic.addEventListener('mouseleave', () => _vjPttUp());
  mic.addEventListener('touchend', e => { e.preventDefault(); _vjPttUp(); }, { passive: false });
  mic.addEventListener('click', () => { if (_vjMode !== 'ptt') (_vjOn ? _vjStop() : _vjStart()); });

  box.querySelector('#vj-modes').addEventListener('click', e => {
    const b = e.target.closest('button[data-m]');
    if (b) _vjSetMode(b.dataset.m);
  });

  // Space = push-to-talk (ptt mode only, and never while typing in a field).
  // Bound once on document — re-mounts reuse it (avoids stacking handlers).
  if (!_vjInit._keys) {
    _vjInit._keys = true;
    document.addEventListener('keydown', e => {
      if (e.code !== 'Space' || _vjMode !== 'ptt' || e.repeat) return;
      if (_vjTyping(e.target)) return;
      e.preventDefault();
      _vjPttDown();
    });
    document.addEventListener('keyup', e => {
      if (e.code !== 'Space' || _vjMode !== 'ptt') return;
      if (_vjTyping(e.target)) return;
      e.preventDefault();
      _vjPttUp();
    });
  }

  _vjLoadSettings();               // async — mode selector fills in when it lands
}

// Pop out → float on document.body (survives tab switches, follows you around the
// store). Dock → snap back into the Assistant tab's container.
function _vjTogglePop() {
  _vjPopped = !_vjPopped;
  if (!_vjBox) return;
  if (_vjPopped) {
    _vjBox.classList.remove('vj-inline');
    document.body.appendChild(_vjBox);                        // float — not tied to #main-content anymore
    _vjNote('📌 popped out — following you around the store');
  } else {
    const t = (_vjDockTarget && document.body.contains(_vjDockTarget))
      ? _vjDockTarget : document.getElementById('agent-voice');
    if (!t) { _vjPopped = true; _vjNote('open the Assistant tab to dock'); return; }   // nowhere to dock → stay floating
    _vjBox.classList.add('vj-inline');
    t.appendChild(_vjBox);
  }
  const popBtn = _vjBox.querySelector('#vj-pop');
  if (popBtn) {
    popBtn.textContent = _vjPopped ? '⤓' : '⤑';
    popBtn.title = _vjPopped ? 'Dock back into the Assistant tab' : 'Pop out — float and follow across tabs';
  }
}

// ── on-demand node TTS: activate → load on the node → GREEN ready → unload on stop/leave ──
function _vjSetNodeUI(state) {            // 'off' | 'loading' | 'ready'
  if (!_vjBox) return;
  const b = _vjBox.querySelector('#vj-turbo');
  if (!b) return;
  b.dataset.node = state;
  b.title = state === 'ready' ? 'Fast node voice ON (green = ready) — click to unload'
    : state === 'loading' ? 'Loading voice on the node…'
    : 'Activate fast node voice (loads Kokoro on the node)';
}

async function _vjSyncNode() {            // reflect current node session on (re)mount
  try {
    const d = await (await fetch(_vjApi() + '/api/voice/node-status')).json();
    _vjNode = !!(d && d.active && d.ready);
    _vjSetNodeUI(_vjNode ? 'ready' : 'off');
  } catch (e) { /* leave as-is */ }
}

async function _vjToggleNode() {
  if (_vjNodeBusy) return;
  _vjNodeBusy = true;
  try {
    if (_vjNode) {                        // click = Stop → unload the node
      _vjSetNodeUI('loading');
      try { await fetch(_vjApi() + '/api/voice/deactivate', { method: 'POST' }); } catch (e) {}
      _vjNode = false; _vjSetNodeUI('off'); _vjNote('node voice unloaded');
    } else {                              // Activate → load on the node, wait for ready
      _vjSetNodeUI('loading'); _vjNote('⏳ loading voice on the node…');
      let d = {};
      try { d = await (await fetch(_vjApi() + '/api/voice/activate', { method: 'POST' })).json(); } catch (e) {}
      if (d && d.ready) { _vjNode = true; _vjSetNodeUI('ready'); _vjNote('🟢 node voice ready — fast mode'); }
      else { _vjNode = false; _vjSetNodeUI('off'); _vjNote('⚠ node didn’t load — staying on local voice (still works)'); }
    }
  } finally { _vjNodeBusy = false; }
}

// Free the node when the user leaves: immediately on page close/navigate; after a short
// grace if the tab just backgrounds (so a quick tab-flip doesn't force a slow reload).
function _vjBindLeave() {
  if (_vjInit._leave) return;
  _vjInit._leave = true;
  const drop = () => {
    if (!_vjNode) return;
    try { navigator.sendBeacon(_vjApi() + '/api/voice/deactivate'); } catch (e) {}
    _vjNode = false; _vjSetNodeUI('off');
  };
  window.addEventListener('pagehide', drop);
  window.addEventListener('beforeunload', drop);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { if (_vjNode && !_vjHideTimer) _vjHideTimer = setTimeout(() => { _vjHideTimer = null; drop(); }, 90000); }
    else if (_vjHideTimer) { clearTimeout(_vjHideTimer); _vjHideTimer = null; }
  });
}

function _vjTyping(el) {
  if (!el) return false;
  const t = (el.tagName || '').toLowerCase();
  return t === 'input' || t === 'textarea' || t === 'select' || el.isContentEditable;
}

function _vjSetState(s, label) {
  _vjState = s;
  if (!_vjBox) return;
  _vjBox.dataset.state = s;
  _vjBox.classList.toggle('vj-off', !_vjOn);
  const txt = _vjBox.querySelector('#vj-pill-text');
  if (!txt) return;
  if (label) { txt.textContent = label; return; }
  if (!_vjOn) {
    txt.textContent = _vjMode === 'ptt' ? 'hold mic / Space to talk'
      : _vjMode === 'wake' ? 'click mic to arm “' + _vjWakePhrase + '”'
      : 'click mic to start listening';
    return;
  }
  txt.textContent = { idle: 'ready', listening: 'listening…', thinking: 'thinking…', speaking: 'speaking…' }[s] || s;
}

function _vjModeUI() {
  if (!_vjBox) return;
  _vjBox.querySelectorAll('#vj-modes button').forEach(b => b.classList.toggle('on', b.dataset.m === _vjMode));
  _vjSetState(_vjState);
}

function _vjLine(who, text) {
  if (!_vjBox) return;
  const box = _vjBox.querySelector('#vj-transcript');
  const div = document.createElement('div');
  div.className = 'vj-' + who;                                   // you | jarvis | note
  div.textContent = (who === 'you' ? 'You: ' : who === 'jarvis' ? 'Jarvis: ' : '') + text;
  box.appendChild(div);
  while (box.children.length > 14) box.removeChild(box.firstChild);
  box.classList.add('show');
  box.scrollTop = box.scrollHeight;
}
function _vjNote(text) { _vjLine('note', text); }

/* ── settings (GET /api/voice/settings; default mode = "wake") ── */
async function _vjLoadSettings() {
  if (_vjSettingsLoaded) { _vjModeUI(); return; }
  try {
    const d = await (await fetch(_vjApi() + '/api/voice/settings')).json();
    const m = d.trigger_mode || d.voice_trigger_mode;
    if (m === 'ptt' || m === 'wake' || m === 'open') _vjMode = m;
    _vjWakePhrase = d.wake_phrase || d.voice_wake_phrase || _vjWakePhrase;
    _vjSettingsLoaded = true;
  } catch (e) { /* endpoint down/offline → keep defaults (wake) */ }
  _vjModeUI();
}

/* ── mic stream (persistent while started; released in stop()) ── */
async function _vjEnsureStream() {
  if (_vjStream && _vjStream.getTracks().some(t => t.readyState === 'live')) return _vjStream;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    _vjNote('⚠ this browser has no microphone API'); return null;
  }
  try {
    _vjStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    });
    return _vjStream;
  } catch (e) {
    _vjStream = null;
    _vjNote('⚠ microphone blocked — allow mic access to use voice');
    _vjToast('Jarvis: microphone permission denied', 'error');
    _vjSetState('idle', 'mic blocked');
    return null;
  }
}

/* ── capture: one utterance at a time ── */
async function _vjBeginUtterance(manual) {
  const gen = ++_vjGen;                       // cancels any older turn (incl. a turn we barged into)
  const stream = await _vjEnsureStream();
  if (!stream || gen !== _vjGen) return;
  if (!window.MediaRecorder) { _vjNote('⚠ MediaRecorder unsupported'); return; }

  let mime = '';
  for (const m of ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']) {
    if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) { mime = m; break; }
  }
  _vjChunks = [];
  let rec;
  try { rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream); }
  catch (e) { _vjNote('⚠ recorder failed: ' + e.message); return; }
  _vjRec = rec;
  rec.ondataavailable = ev => { if (ev.data && ev.data.size) _vjChunks.push(ev.data); };
  rec.onstop = () => {
    if (gen !== _vjGen) { _vjChunks = []; return; }            // cancelled (stop()/mode switch/barge-in)
    const blob = new Blob(_vjChunks, { type: rec.mimeType || 'audio/webm' });
    _vjChunks = [];
    if (blob.size < 800) { _vjNote('…didn\'t catch that'); _vjAfterTurn(); return; }
    _vjProcess(blob, gen);
  };
  rec.start();
  _vjSetState('listening');

  // End-of-utterance: ptt ends on release; wake/open delegate to VoiceWake.vadListen
  // (assumed one-shot: fires its callback once when speech ends).
  if (!manual) {
    const W = _vjWakeLib();
    if (W) {
      try { W.vadListen(() => { if (gen === _vjGen) _vjEndUtterance(); }); }
      catch (e) { /* safety timer below still ends the capture */ }
    }
  }
  clearTimeout(_vjMaxUttTimer);
  _vjMaxUttTimer = setTimeout(() => {                          // never record forever
    if (gen === _vjGen && _vjState === 'listening') _vjEndUtterance();
  }, manual ? 60000 : 25000);
}

function _vjEndUtterance() {
  clearTimeout(_vjMaxUttTimer); _vjMaxUttTimer = null;
  const rec = _vjRec;
  _vjRec = null;
  if (rec && rec.state !== 'inactive') { try { rec.stop(); } catch (e) {} }  // → onstop → _vjProcess
}

/* ── push-to-talk ── */
function _vjPttDown() {
  if (_vjPtt) return;
  _vjPtt = true;
  _vjOn = true;                                // pressing implicitly turns voice on
  _vjStopPlayback();                           // ptt's barge-in: press = cut Jarvis off
  _vjBeginUtterance(true);
}
function _vjPttUp() {
  if (!_vjPtt) return;
  _vjPtt = false;
  _vjEndUtterance();
}

/* ── the turn: stt → agent (tab-agent.js's calls) → tts → play ── */
async function _vjProcess(blob, gen) {
  _vjSetState('thinking');
  try {
    const text = await _vjStt(blob);
    if (gen !== _vjGen) return;
    if (!text) { _vjNote('…didn\'t catch that'); _vjAfterTurn(); return; }
    _vjLine('you', text);

    const reply = await _vjAsk(text, gen);
    if (gen !== _vjGen) return;
    if (!reply) { _vjNote('(no reply)'); _vjAfterTurn(); return; }
    _vjLine('jarvis', reply);

    await _vjSpeakReply(reply, gen);
    if (gen !== _vjGen) return;                // barge-in started a new turn — it owns the mic now
  } catch (e) {
    if (gen !== _vjGen) return;
    _vjNote('⚠ ' + e.message);
    _vjToast('Jarvis: ' + e.message, 'error');
  }
  _vjAfterTurn();
}

// POST multipart field `audio` → { text, ms }
async function _vjStt(blob) {
  const fd = new FormData();
  fd.append('audio', blob, 'utterance.webm');
  const resp = await fetch(_vjApi() + '/api/voice/stt', { method: 'POST', body: fd });
  if (!resp.ok) throw new Error('stt failed (HTTP ' + resp.status + ')');
  const d = await resp.json();
  return (d.text || '').trim();
}

// Drive the assistant EXACTLY like tab-agent.js: POST /api/agent/chat
// { message, conversation_id }, then poll GET /api/agent/events
// ?conversation_id&after every ~1.3s until status === 'idle'; the reply is
// the last kind:'assistant' message. Approval requests can't be answered by
// voice — we say so and leave the run pending in the Assistant tab.
async function _vjAsk(text, gen) {
  const A = _vjApi();
  // Baseline the event cursor so an existing conversation's history isn't replayed.
  let after = 0;
  const conv0 = _vjGetConv();
  if (conv0) {
    try {
      const d0 = await (await fetch(A + `/api/agent/events?conversation_id=${conv0}&after=0`)).json();
      for (const m of d0.messages || []) after = m.id;
    } catch (e) { /* non-fatal */ }
  }
  if (gen !== _vjGen) return '';

  const resp = await fetch(A + '/api/agent/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: text, conversation_id: conv0 })
  });
  const d = await resp.json();
  if (!resp.ok) throw new Error(d.detail || 'assistant request failed');
  _vjSetConv(d.conversation_id);

  let reply = '';
  const deadline = Date.now() + 180000;        // 3 min cap on "thinking"
  while (gen === _vjGen) {
    let ev;
    try {
      ev = await (await fetch(A + `/api/agent/events?conversation_id=${_vjGetConv()}&after=${after}`)).json();
    } catch (e) { await _vjSleep(1300); continue; }   // transient, like tab-agent.js
    if (gen !== _vjGen) return '';
    for (const m of ev.messages || []) {
      after = m.id;
      const kind = m.kind || m.role;
      if (kind === 'assistant' && m.content && m.content.trim()) reply = m.content.trim();
      if (kind === 'approval_request') {
        return 'I need your approval before I can continue — please check the Assistant tab.';
      }
    }
    if (ev.status === 'idle') break;
    if (Date.now() > deadline) return 'That\'s taking a while — I\'ll keep working on it in the Assistant tab.';
    await _vjSleep(1300);
  }
  return reply;
}

// Speak the reply — SENTENCE-STREAMED so Jarvis starts talking after just the first
// sentence instead of waiting for the whole reply to synthesize. Kokoro stays warm in
// the store process, so each sentence is only synth time; we prefetch the next sentence
// while the current one plays, hiding synth latency behind playback. No GPU/queue use.
function _vjSplitSentences(text) {
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  if (!clean) return [];
  const parts = clean.split(/(?<=[.!?])\s+/);          // keep sentence-ending punctuation
  const out = [];
  for (let p of parts) {
    p = p.trim(); if (!p) continue;
    // merge tiny fragments ("Sir.", "Yes.") into a neighbour so we don't over-chop
    if (out.length && (p.length < 12 || out[out.length - 1].length < 12)) out[out.length - 1] += ' ' + p;
    else out.push(p);
  }
  return out.length ? out : [clean];
}

function _vjTtsFetch(text) {
  return fetch(_vjApi() + '/api/voice/tts', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text })
  }).then(r => (r.ok ? r.blob() : null)).catch(() => null);
}

async function _vjSpeakReply(text, gen) {
  const chunks = _vjSplitSentences(text);
  if (!chunks.length || gen !== _vjGen) return;
  _vjSetState('speaking');
  if (_vjMode !== 'ptt') _vjMeterStart();              // barge-in armed for the whole reply
  try {
    let next = _vjTtsFetch(chunks[0]);                 // start synthesizing sentence 1 immediately
    for (let i = 0; i < chunks.length; i++) {
      const blob = await next;
      if (gen !== _vjGen) return;                      // barge-in / stop → drop the remaining sentences
      next = (i + 1 < chunks.length) ? _vjTtsFetch(chunks[i + 1]) : null;   // prefetch while this plays
      if (blob && blob.size) { await _vjPlayBlob(blob, gen); if (gen !== _vjGen) return; }
    }
  } finally {
    if (gen === _vjGen) _vjStopPlayback();             // tear down only if this turn is still current
  }
}

function _vjPlayBlob(blob, gen) {
  return new Promise(res => {
    if (gen !== _vjGen) { res(); return; }
    _vjAudioDone = res;
    _vjAudioUrl = URL.createObjectURL(blob);
    _vjAudio = new Audio(_vjAudioUrl);
    _vjAudio.onended = () => _vjEndClip();             // clip done → next sentence; meter keeps running
    _vjAudio.onerror = () => _vjEndClip();
    const p = _vjAudio.play();
    if (p && p.catch) p.catch(() => _vjEndClip());     // autoplay refusal etc.
  });
}

function _vjEndClip() {                                 // one sentence finished: resolve, KEEP the meter alive
  if (_vjAudio) { try { _vjAudio.pause(); } catch (e) {} _vjAudio = null; }
  if (_vjAudioUrl) { URL.revokeObjectURL(_vjAudioUrl); _vjAudioUrl = null; }
  if (_vjAudioDone) { const r = _vjAudioDone; _vjAudioDone = null; r(); }
}

function _vjStopPlayback() {                            // full stop of the whole reply (barge-in / turn end)
  if (_vjMeterTimer) { clearInterval(_vjMeterTimer); _vjMeterTimer = null; }
  _vjEndClip();
}

/* ── barge-in: RMS energy meter on the mic while Jarvis speaks ──
   Uses one persistent AudioContext (closed in stop()). Sustained energy
   (~3×60ms frames, after a 450ms grace so playback onset/speaker bleed with
   echoCancellation doesn't self-trigger) = the user talking over Jarvis. */
function _vjMeterStart() {
  try {
    if (!_vjStream) return;
    if (!_vjAC) _vjAC = new (window.AudioContext || window.webkitAudioContext)();
    if (_vjAC.state === 'suspended') _vjAC.resume();
    if (!_vjAnalyser || _vjSrcStream !== _vjStream) {
      const src = _vjAC.createMediaStreamSource(_vjStream);
      _vjAnalyser = _vjAC.createAnalyser();
      _vjAnalyser.fftSize = 1024;
      src.connect(_vjAnalyser);
      _vjSrcStream = _vjStream;
    }
    const buf = new Uint8Array(_vjAnalyser.fftSize);
    const startedAt = Date.now();
    let hot = 0;
    if (_vjMeterTimer) clearInterval(_vjMeterTimer);
    _vjMeterTimer = setInterval(() => {
      _vjAnalyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
      const rms = Math.sqrt(sum / buf.length);
      if (Date.now() - startedAt < 450) return;
      hot = rms > 0.09 ? hot + 1 : 0;
      if (hot >= 3) _vjBargeIn();
    }, 60);
  } catch (e) { /* no meter → no barge-in, playback still works */ }
}

function _vjBargeIn() {
  _vjStopPlayback();                 // cut Jarvis off immediately
  _vjBeginUtterance(false);          // bumps _vjGen → the interrupted turn dies; new capture (VAD-ended)
}

/* ── arming per trigger mode ── */
function _vjArm() {
  if (!_vjOn) { _vjSetState('idle'); return; }
  const W = _vjWakeLib();
  if ((_vjMode === 'wake' || _vjMode === 'open') && !W) {
    _vjNote('voice-wake.js not loaded — falling back to push-to-talk');
    _vjMode = 'ptt';
    _vjModeUI();
  }
  if (_vjMode === 'ptt') { _vjSetState('idle', 'hold mic / Space to talk'); return; }
  if (_vjMode === 'wake') {
    _vjSetState('idle', 'arming “' + _vjWakePhrase + '”…');
    const gen = _vjGen;
    // startWake is async and resolves the engine actually used ('onnx' | 'webspeech'
    // | 'error') — surface it so a silent fallback/failure is visible, not mysterious.
    Promise.resolve(
      W.startWake(_vjWakePhrase, () => { if (gen === _vjGen && _vjOn && _vjState !== 'listening') _vjBeginUtterance(false); })
    ).then(mode => {
      if (gen !== _vjGen || !_vjOn) return;
      if (mode === 'onnx') { _vjSetState('idle', 'say “' + _vjWakePhrase + '”'); _vjNote('👂 wake word ready'); }
      else if (mode === 'webspeech') { _vjSetState('idle', 'say “' + _vjWakePhrase + '”'); _vjNote('👂 listening (browser fallback — needs internet)'); }
      else { _vjNote('⚠ wake word unavailable — switched to push-to-talk'); _vjMode = 'ptt'; _vjModeUI(); _vjSetState('idle'); }
    }).catch(e => {
      if (gen !== _vjGen) return;
      _vjNote('⚠ wake word failed: ' + ((e && e.message) || e) + ' — using push-to-talk');
      _vjMode = 'ptt'; _vjModeUI(); _vjSetState('idle');
    });
    return;
  }
  _vjBeginUtterance(false);          // open mic: record now, VoiceWake.vadListen ends the utterance
}

function _vjDisarm() {
  const W = _vjWakeLib();
  if (W && W.stopWake) { try { W.stopWake(); } catch (e) {} }
  clearTimeout(_vjMaxUttTimer); _vjMaxUttTimer = null;
  const rec = _vjRec;
  _vjRec = null;
  if (rec && rec.state !== 'inactive') { try { rec.stop(); } catch (e) {} }  // gen bump makes onstop a no-op
}

function _vjAfterTurn() {
  if (!_vjOn) { _vjSetState('idle'); return; }
  _vjArm();                          // wake → re-listen for phrase; open → next capture; ptt → wait for press
}

/* ── public API ── */
async function _vjStart() {
  if (!_vjBox) _vjInit();
  if (_vjOn && _vjState !== 'idle') return;    // already mid-turn
  _vjOn = true;
  _vjGen++;                                    // rapid start/stop safety: orphan any prior async work
  await _vjLoadSettings();
  const s = await _vjEnsureStream();           // ask for the mic up front (also warms ptt latency)
  if (!s) { _vjOn = false; _vjSetState('idle'); return; }
  _vjArm();
}

function _vjStop() {
  _vjOn = false;
  _vjPtt = false;
  _vjGen++;                                    // cancel every in-flight stage
  _vjDisarm();
  _vjStopPlayback();
  if (_vjStream) { for (const t of _vjStream.getTracks()) { try { t.stop(); } catch (e) {} } _vjStream = null; }
  if (_vjAC) { try { _vjAC.close(); } catch (e) {} _vjAC = null; _vjAnalyser = null; _vjSrcStream = null; }
  _vjSetState('idle');
}

function _vjSetMode(m) {
  if (m !== 'ptt' && m !== 'wake' && m !== 'open') return;
  if (m === _vjMode) { _vjModeUI(); return; }
  _vjGen++;                                    // abort the current turn cleanly
  _vjDisarm();
  _vjStopPlayback();
  _vjMode = m;
  _vjModeUI();
  try {                                        // persist (fire-and-forget; POST /api/voice/settings)
    fetch(_vjApi() + '/api/voice/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trigger_mode: m })
    });
  } catch (e) {}
  if (_vjOn) _vjArm(); else _vjSetState('idle');
}

window.VoiceJarvis = {
  init: function (container) { _vjInit(container); },
  start: _vjStart,
  stop: _vjStop,
  setMode: _vjSetMode
};

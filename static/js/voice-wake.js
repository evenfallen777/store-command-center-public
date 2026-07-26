'use strict';

/* ══ VOICE WAKE — wake word + VAD, isolated (spec: app/VOICE.md) ══
   Exposes  window.VoiceWake = { startWake(phrase, onWake), stopWake(),
                                 vadListen(onSpeechEnd), stopVad() }.

   WAKE PATH USED: ★ ONNX (primary) ★ — a real in-browser openWakeWord detector
   running on onnxruntime-web (WASM). ALL assets are self-hosted under
   /static/vendor/voice/ (ort.min.js, ort-wasm[-simd].wasm, melspectrogram.onnx,
   embedding_model.onnx, hey_jarvis_v0.1.onnx) — zero external/CDN fetches, so it
   works on an offline LAN. Pipeline (16 kHz mono, 80 ms hops):
     raw PCM → melspectrogram.onnx (/10 + 2 scaling) → 76-frame window →
     embedding_model.onnx → last 16 embeddings → hey_jarvis_v0.1.onnx → sigmoid
     score; score > threshold ⇒ onWake().  A refractory window stops re-fires.

   FALLBACK: if the phrase has no shipped model, or ORT/model loading fails, we
   drop to the browser Web Speech API (SpeechRecognition/webkitSpeechRecognition)
   and fire when the live transcript contains the phrase. Note: Chrome's Web
   Speech may use a network service — the ONNX path is the offline-safe one.
   UPGRADE for new phrases: train an openWakeWord model, drop the .onnx into
   /static/vendor/voice/ and add it to WAKE_MODELS below (see the README there).

   VAD: dependency-free energy VAD (Web Audio) + MediaRecorder. vadListen()
   resolves one utterance and calls onSpeechEnd(blob) — webm/opus, ready to POST
   to /api/voice/stt — after ~600 ms of trailing silence. onSpeechEnd(null)
   means mic denied / no speech before timeout.

   All start/stop pairs are race-safe (generation tokens): rapid start/stop or
   double-stop never leaks a stream, AudioContext, recognizer or recorder. */

(function () {
  // Assets live under the app's base prefix (the store is served at /store, same as
  // every other /store/static/... asset). Using a bare /static path 404s behind the
  // proxy and silently drops wake detection to the unreliable Web Speech fallback.
  const VENDOR = (typeof API !== 'undefined' ? API : '/store') + '/static/vendor/voice/';
  const WAKE_MODELS = {                       // phrase → self-hosted onnx model
    'hey jarvis': 'hey_jarvis_v0.1.onnx'
  };
  const SR16 = 16000, CHUNK = 1280;           // openWakeWord: 80 ms @ 16 kHz
  const MEL_WIN = 76, EMB_WIN = 16, N_MELS = 32;

  const WAKE_DEFAULTS = { threshold: 0.5, refractoryMs: 2500 };
  const VAD_DEFAULTS = {
    silenceMs: 600,        // trailing silence that ends the utterance (spec)
    minSpeechMs: 200,      // ignore blips shorter than this
    noSpeechMs: 8000,      // give up if nothing said → onSpeechEnd(null)
    maxUtteranceMs: 15000  // hard cap per utterance
  };

  let _wakeGen = 0, _wake = null;   // active wake session state
  let _vadGen = 0, _vad = null;     // active VAD session state
  let _ortLoad = null;              // promise: ort.min.js injected
  const _sessCache = {};            // model file → InferenceSession (kept warm)

  /* ── shared helpers ── */

  function _warn(msg, e) {
    VoiceWake.lastError = msg + (e ? ': ' + (e.message || e) : '');
    console.warn('[voice-wake] ' + VoiceWake.lastError);
  }

  function _stopStream(stream) {
    try { if (stream) stream.getTracks().forEach(t => t.stop()); } catch (e) {}
  }

  function _teardownAudio(s) {      // s: {proc, src, ctx, stream}
    try { if (s.proc) { s.proc.onaudioprocess = null; s.proc.disconnect(); } } catch (e) {}
    try { if (s.src) s.src.disconnect(); } catch (e) {}
    try { if (s.ctx && s.ctx.state !== 'closed') s.ctx.close(); } catch (e) {}
    _stopStream(s.stream);
  }

  async function _openMic(constraints) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia)
      throw new Error('getUserMedia unsupported');
    return navigator.mediaDevices.getUserMedia({
      audio: Object.assign({ channelCount: 1, echoCancellation: true,
        noiseSuppression: true, autoGainControl: true }, constraints || {})
    });
  }

  // Linear resample to 16 kHz (AudioContext({sampleRate:16000}) is honored on
  // Chrome/Firefox, but Safari ignores it — so always be able to downsample).
  function _to16k(f32, fromRate) {
    if (fromRate === SR16) return f32;
    const ratio = fromRate / SR16, n = Math.floor(f32.length / ratio);
    const out = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const p = i * ratio, lo = Math.floor(p), hi = Math.min(lo + 1, f32.length - 1);
      out[i] = f32[lo] + (f32[hi] - f32[lo]) * (p - lo);
    }
    return out;
  }

  /* ── ONNX wake path ── */

  function _loadOrt() {             // inject self-hosted onnxruntime-web once
    if (window.ort) return Promise.resolve();
    if (_ortLoad) return _ortLoad;
    _ortLoad = new Promise((resolve, reject) => {
      const sc = document.createElement('script');
      sc.src = VENDOR + 'ort.min.js';
      sc.onload = () => resolve();
      sc.onerror = () => { _ortLoad = null; reject(new Error('ort.min.js failed to load')); };
      document.head.appendChild(sc);
    });
    return _ortLoad;
  }

  async function _session(file) {
    if (_sessCache[file]) return _sessCache[file];
    window.ort.env.wasm.wasmPaths = VENDOR;   // ort-wasm[-simd].wasm live here
    window.ort.env.wasm.numThreads = 1;       // no COOP/COEP needed
    const s = await window.ort.InferenceSession.create(VENDOR + file,
      { executionProviders: ['wasm'] });
    _sessCache[file] = s;
    return s;
  }

  async function _startOnnxWake(gen, phrase, onWake, opts) {
    const modelFile = WAKE_MODELS[phrase];
    if (!modelFile) throw new Error(`no shipped wake model for "${phrase}"`);
    await _loadOrt();
    const melspec = await _session('melspectrogram.onnx');
    const embed = await _session('embedding_model.onnx');
    const wakeM = await _session(modelFile);
    if (gen !== _wakeGen) return;              // stopped while loading

    const stream = await _openMic();
    if (gen !== _wakeGen) { _stopStream(stream); return; }

    let ctx;
    try { ctx = new AudioContext({ sampleRate: SR16 }); }
    catch (e) { ctx = new AudioContext(); }
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(2048, 1, 1);

    const st = {
      mode: 'onnx', stream, ctx, src, proc,
      pcm: new Float32Array(0),                // pending 16 kHz samples
      melBuf: [],                              // rows of Float32Array(32)
      embBuf: [],                              // rows of Float32Array(96), zero-primed
      queue: [], busy: false, lastFire: 0,
      threshold: opts.threshold, refractoryMs: opts.refractoryMs
    };
    for (let i = 0; i < EMB_WIN; i++) st.embBuf.push(new Float32Array(96));
    _wake = st;

    proc.onaudioprocess = (ev) => {
      if (gen !== _wakeGen) return;
      const s16 = _to16k(ev.inputBuffer.getChannelData(0), ctx.sampleRate);
      const merged = new Float32Array(st.pcm.length + s16.length);
      merged.set(st.pcm); merged.set(s16, st.pcm.length);
      st.pcm = merged;
      while (st.pcm.length >= CHUNK) {
        if (st.queue.length < 12)              // backpressure: drop when behind
          st.queue.push(st.pcm.slice(0, CHUNK));
        st.pcm = st.pcm.slice(CHUNK);
      }
      _drainWakeQueue(gen, st, { melspec, embed, wakeM }, onWake);
    };
    src.connect(proc);
    proc.connect(ctx.destination);             // keep the node pulling audio
  }

  async function _drainWakeQueue(gen, st, sess, onWake) {
    if (st.busy) return;
    st.busy = true;
    try {
      while (st.queue.length && gen === _wakeGen) {
        const chunk = st.queue.shift();
        // 1) melspectrogram: [1, 1280] → frames × 32; openWakeWord scaling /10+2
        const mo = await sess.melspec.run({ [sess.melspec.inputNames[0]]:
          new window.ort.Tensor('float32', chunk, [1, CHUNK]) });
        const md = mo[sess.melspec.outputNames[0]].data;
        for (let f = 0; f + N_MELS <= md.length; f += N_MELS) {
          const row = new Float32Array(N_MELS);
          for (let i = 0; i < N_MELS; i++) row[i] = md[f + i] / 10 + 2;
          st.melBuf.push(row);
        }
        if (st.melBuf.length > MEL_WIN + 64) st.melBuf.splice(0, st.melBuf.length - MEL_WIN - 64);
        if (st.melBuf.length < MEL_WIN) continue;

        // 2) embedding over the last 76 mel frames → 96-dim, once per 80 ms chunk
        const win = st.melBuf.slice(-MEL_WIN);
        const flat = new Float32Array(MEL_WIN * N_MELS);
        for (let r = 0; r < MEL_WIN; r++) flat.set(win[r], r * N_MELS);
        const eo = await sess.embed.run({ [sess.embed.inputNames[0]]:
          new window.ort.Tensor('float32', flat, [1, MEL_WIN, N_MELS, 1]) });
        st.embBuf.push(Float32Array.from(eo[sess.embed.outputNames[0]].data));
        if (st.embBuf.length > EMB_WIN) st.embBuf.shift();

        // 3) wake model over the last 16 embeddings → sigmoid score
        const feat = new Float32Array(EMB_WIN * 96);
        for (let r = 0; r < EMB_WIN; r++) feat.set(st.embBuf[r], r * 96);
        const wo = await sess.wakeM.run({ [sess.wakeM.inputNames[0]]:
          new window.ort.Tensor('float32', feat, [1, EMB_WIN, 96]) });
        const score = wo[sess.wakeM.outputNames[0]].data[0];
        const now = performance.now();
        if (score > st.threshold && now - st.lastFire > st.refractoryMs) {
          st.lastFire = now;
          try { onWake(score); } catch (e) { _warn('onWake callback threw', e); }
        }
      }
    } catch (e) {
      if (gen === _wakeGen) _warn('wake inference error', e);
    } finally { st.busy = false; }
  }

  /* ── Web Speech fallback wake path (transcript contains phrase) ── */

  function _startSpeechWake(gen, phrase, onWake, opts) {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) throw new Error('Web Speech API unavailable (no fallback possible)');
    const rec = new SR();
    rec.continuous = true; rec.interimResults = true; rec.lang = 'en-US';
    const norm = s => s.toLowerCase().replace(/[^a-z ]+/g, ' ').replace(/\s+/g, ' ').trim();
    const want = norm(phrase);
    const keyWord = want.split(' ').pop();      // "jarvis" alone also counts
    const st = { mode: 'webspeech', rec, lastFire: 0 };
    _wake = st;

    rec.onresult = (ev) => {
      if (gen !== _wakeGen) return;
      let heard = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++)
        heard += ' ' + ev.results[i][0].transcript;
      heard = norm(heard);
      const hit = heard.includes(want) || (keyWord.length > 3 && heard.includes(keyWord));
      const now = performance.now();
      if (hit && now - st.lastFire > opts.refractoryMs) {
        st.lastFire = now;
        try { onWake(1); } catch (e) { _warn('onWake callback threw', e); }
      }
    };
    rec.onerror = (ev) => {
      if (gen !== _wakeGen) return;
      if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {
        _warn('speech recognition blocked (' + ev.error + ')');
        stopWake();                             // permanent — don't restart-loop
      }                                         // transient errors: onend restarts
    };
    rec.onend = () => {                         // Chrome self-stops; keep alive
      if (gen !== _wakeGen) return;
      setTimeout(() => {
        if (gen !== _wakeGen) return;
        try { rec.start(); } catch (e) { /* already started */ }
      }, 250);
    };
    rec.start();
  }

  /* ── public: wake word ── */

  // Resolves to 'onnx' | 'webspeech' | 'error' (also in VoiceWake.wakeMode).
  // opts (optional): { threshold, refractoryMs, forceFallback }
  async function startWake(phrase, onWake, opts) {
    stopWake();
    const gen = ++_wakeGen;
    const o = Object.assign({}, WAKE_DEFAULTS, opts || {});
    phrase = (phrase || 'hey jarvis').toLowerCase().trim();
    if (!o.forceFallback) {
      try {
        await _startOnnxWake(gen, phrase, onWake, o);
        if (gen !== _wakeGen) return 'stopped';
        VoiceWake.wakeMode = 'onnx';
        return 'onnx';
      } catch (e) {
        if (gen !== _wakeGen) return 'stopped';
        _warn('ONNX wake path unavailable, falling back to Web Speech', e);
        if (_wake) { _teardownAudio(_wake); _wake = null; }
      }
    }
    try {
      _startSpeechWake(gen, phrase, onWake, o);
      VoiceWake.wakeMode = 'webspeech';
      return 'webspeech';
    } catch (e) {
      if (gen === _wakeGen) _warn('wake word unavailable', e);
      VoiceWake.wakeMode = 'error';
      return 'error';
    }
  }

  function stopWake() {
    _wakeGen++;                                 // invalidate in-flight async work
    const st = _wake; _wake = null;
    if (!st) return;
    if (st.rec) {
      try { st.rec.onend = null; st.rec.onresult = null; st.rec.onerror = null; } catch (e) {}
      try { st.rec.abort(); } catch (e) {}
    }
    _teardownAudio(st);
    if (st.queue) st.queue.length = 0;
  }

  /* ── public: VAD (one utterance → webm/opus Blob) ── */

  // opts (optional): { silenceMs, minSpeechMs, noSpeechMs, maxUtteranceMs }
  async function vadListen(onSpeechEnd, opts) {
    stopVad();
    const gen = ++_vadGen;
    const o = Object.assign({}, VAD_DEFAULTS, opts || {});

    let stream;
    try { stream = await _openMic(); }
    catch (e) {
      _warn('mic unavailable/denied', e);
      try { onSpeechEnd(null); } catch (e2) {}
      return;
    }
    if (gen !== _vadGen) { _stopStream(stream); return; }

    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', '']
      .find(m => !m || (window.MediaRecorder && MediaRecorder.isTypeSupported(m)));
    let rec;
    try { rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream); }
    catch (e) {
      _warn('MediaRecorder unavailable', e);
      _stopStream(stream);
      try { onSpeechEnd(null); } catch (e2) {}
      return;
    }

    const ctx = new AudioContext();
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(2048, 1, 1);
    const chunks = [];
    const st = { mode: 'vad', stream, ctx, src, proc, rec, done: false };
    _vad = st;

    const finish = (deliver) => {               // deliver=false → silent abort
      if (st.done) return;
      st.done = true;
      const wrap = () => {
        proc.onaudioprocess = null;
        _teardownAudio(st);
        if (_vad === st) _vad = null;
        if (deliver) {
          const blob = chunks.length ? new Blob(chunks, { type: rec.mimeType || 'audio/webm' }) : null;
          try { onSpeechEnd(st.spoke ? blob : null); } catch (e) { _warn('onSpeechEnd threw', e); }
        }
      };
      if (rec.state !== 'inactive') { rec.onstop = wrap; try { rec.stop(); } catch (e) { wrap(); } }
      else wrap();
    };
    st.finish = finish;

    rec.ondataavailable = (ev) => { if (ev.data && ev.data.size) chunks.push(ev.data); };
    rec.start(250);                             // record from t0 — no clipped starts

    // Energy VAD: adaptive noise floor (EMA while quiet), speech = RMS well
    // above floor; end after `silenceMs` of quiet following ≥ minSpeechMs speech.
    const t0 = performance.now();
    let noise = 0.01, speechStart = 0, lastLoud = 0;
    st.spoke = false;
    proc.onaudioprocess = (ev) => {
      if (gen !== _vadGen || st.done) return;
      const d = ev.inputBuffer.getChannelData(0);
      let sum = 0;
      for (let i = 0; i < d.length; i++) sum += d[i] * d[i];
      const rms = Math.sqrt(sum / d.length);
      const now = performance.now();
      const thresh = Math.max(0.012, noise * 3);
      if (rms < thresh) noise = noise * 0.95 + rms * 0.05;   // track floor while quiet
      if (rms >= thresh) {
        if (!speechStart) speechStart = now;
        if (now - speechStart >= o.minSpeechMs) st.spoke = true;
        lastLoud = now;
      } else if (!st.spoke) speechStart = 0;                  // blip — reset
      if (st.spoke && now - lastLoud >= o.silenceMs) return finish(true);
      if (st.spoke && now - speechStart >= o.maxUtteranceMs) return finish(true);
      if (!st.spoke && now - t0 >= o.noSpeechMs) return finish(true); // → null blob
    };
    src.connect(proc);
    proc.connect(ctx.destination);
  }

  function stopVad() {
    _vadGen++;                                  // invalidate in-flight async work
    const st = _vad; _vad = null;
    if (!st) return;
    if (st.finish) st.finish(false);            // abort: no callback, full teardown
    else _teardownAudio(st);
  }

  const VoiceWake = { startWake, stopWake, vadListen, stopVad,
    wakeMode: null, lastError: null };
  window.VoiceWake = VoiceWake;
})();

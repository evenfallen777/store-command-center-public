# /static/vendor/voice — self-hosted voice assets (no CDN, LAN/offline-safe)

Used by `static/js/voice-wake.js` (wake word "hey jarvis" + VAD). Everything the
browser needs is in this directory — no external network fetches.

## Shipped assets

| File | What | Source |
|---|---|---|
| `ort.min.js` | onnxruntime-web 1.14.0 runtime | npm `onnxruntime-web@1.14.0` dist |
| `ort-wasm.wasm` | ORT WASM backend (no SIMD) | same package |
| `ort-wasm-simd.wasm` | ORT WASM backend (SIMD, auto-picked) | same package |
| `melspectrogram.onnx` | openWakeWord mel frontend | dscripka/openWakeWord release v0.5.1 |
| `embedding_model.onnx` | openWakeWord speech embedding | dscripka/openWakeWord release v0.5.1 |
| `hey_jarvis_v0.1.onnx` | "hey jarvis" wake classifier | dscripka/openWakeWord release v0.5.1 |

SHA-256 (models):
```
ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f  melspectrogram.onnx
70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f  embedding_model.onnx
94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb  hey_jarvis_v0.1.onnx
```

## Adding another wake phrase

1. Train/download an openWakeWord `.onnx` classifier (input `[1,16,96]`, sigmoid
   output) — e.g. via the openWakeWord training notebook.
2. Drop it in this directory.
3. Add it to the `WAKE_MODELS` map at the top of `static/js/voice-wake.js`
   (`'my phrase': 'my_phrase.onnx'`). Phrases without a model automatically use
   the Web Speech API fallback (transcript match — may not work offline).

Threaded/SIMD-threaded ORT wasm binaries are intentionally NOT shipped:
`voice-wake.js` pins `numThreads = 1` so no COOP/COEP headers are required.

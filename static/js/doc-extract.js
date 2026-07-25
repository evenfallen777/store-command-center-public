'use strict';
/* ── DOCUMENT EXTRACT — reusable "photo -> structured fields" widget ──────────
   Backend: POST /api/extract (multipart: file, schema) -> {task_id}; poll
   GET /api/task/{id} the same way tab-shared.js's enhance helpers do. Any form,
   anywhere in the Store, can call:

     docExtract({ schema: 'receipt', onResult: data => { ...fill fields... } });

   to turn "take a photo" into structured JSON — no per-consumer backend work,
   just add a schema in app/docextract.py and call this with that schema name.
   First consumer: Purchases' "📷 Snap a receipt" (static/js/tab-bills.js). */

const _DOCEXTRACT_MAX_TRIES = 90;   // 90 * 2s = 3min — a vision pass can be slow on a busy queue
let _docExtractBusy = false;

/**
 * Open a photo/file picker, upload to /api/extract, poll to completion, and call
 * onResult(structuredData) when done. Returns nothing — fire-and-forget, driven by
 * the callbacks. Options:
 *   schema    — schema name registered in app/docextract.py (default 'generic')
 *   onResult  — function(data) called with the normalized structured JSON
 *   onError   — function(Error) called on failure/timeout (optional)
 *   onStart   — function() called once a file is picked, before upload (optional)
 *   accept    — file input accept attribute (default 'image/*')
 */
function docExtract(opts) {
  opts = opts || {};
  const schema   = opts.schema || 'generic';
  const onResult = opts.onResult || function () {};
  const onError  = opts.onError || function (e) { toast(e.message || 'Could not read that document', 'error'); };
  const onStart  = opts.onStart || function () {};

  if (_docExtractBusy) { toast('Already reading a document…'); return; }

  const input = document.createElement('input');
  input.type = 'file';
  input.accept = opts.accept || 'image/*';
  input.capture = 'environment';   // hints a phone camera on mobile; ignored elsewhere
  input.style.display = 'none';
  document.body.appendChild(input);

  input.addEventListener('change', async () => {
    const file = input.files && input.files[0];
    document.body.removeChild(input);
    if (!file) return;
    onStart();
    _docExtractBusy = true;
    toast('Reading document…');
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('schema', schema);
      const res = await fetch(API + '/api/extract', { method: 'POST', body: fd });
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { const j = await res.json(); msg = j.detail || j.error || msg; } catch {}
        throw new Error(msg);
      }
      const { task_id } = await res.json();
      if (task_id == null) throw new Error('No task returned');
      await _docExtractPoll(task_id, onResult, onError);
    } catch (e) {
      _docExtractBusy = false;
      onError(e);
    }
  }, { once: true });

  input.click();
}
window.docExtract = docExtract;

async function _docExtractPoll(taskId, onResult, onError) {
  try {
    for (let i = 0; i < _DOCEXTRACT_MAX_TRIES; i++) {
      await new Promise(r => setTimeout(r, 2000));
      let t;
      try { t = await api(`/api/task/${taskId}`); } catch { continue; }
      if (t.status === 'done') {
        _docExtractBusy = false;
        toast('📷 Document read');
        onResult(t.result || {});
        return;
      }
      if (t.status === 'error') throw new Error(t.error || 'Extraction failed');
      if (t.status === 'not_found' || t.status === 'cancelled') {
        throw new Error('Extraction was cancelled');
      }
    }
    throw new Error('Extraction timed out');
  } catch (e) {
    _docExtractBusy = false;
    onError(e);
  }
}

/* ══ THE COMPANY — The Bible / Field Manual ══
   BOOK.md is the system-reference doc; the Teachings are what the agents study
   and record; the OPS notes (kind 'ops') are the VERIFIED operational layer —
   ground-truth facts about this system with a verified/stale badge. Labels come
   themed from the server (naming_theme). Uses _worldModal + api()/esc()/toast().
   Global-scope classic script. */

let _bibleData = { word: null, teachings: [], tab: 'word', chapter: 0, label: null, opsLabel: null };

/* tiny, safe-enough markdown → html (content is our own BOOK.md) */
function _md(src) {
  if (!src) return '';
  const parts = String(src).split(/```/);
  let out = '';
  parts.forEach((seg, i) => {
    if (i % 2 === 1) {   // fenced code block
      out += `<pre style="background:#0b1120;border:1px solid #1b2740;border-radius:6px;padding:8px;overflow-x:auto;font-size:.72rem;color:#a9c7e8;white-space:pre">${esc(seg.replace(/^\w*\n/, ''))}</pre>`;
      return;
    }
    let t = esc(seg);
    t = t.replace(/`([^`]+)`/g, '<code style="background:#0b1120;padding:1px 4px;border-radius:4px;color:#a9c7e8">$1</code>');
    t = t.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, txt, url) =>
      /^#/.test(url)                                             // internal BOOK.md ToC anchors don't resolve in this widget —
        ? `<span style="color:#7dd3fc">${txt}</span>`            // show as plain text instead of a broken new-tab link
        : `<a href="${url}" target="_blank" rel="noopener" style="color:#7dd3fc">${txt}</a>`);
    // block elements, line by line
    const lines = t.split('\n');
    let html = '', inList = false;
    for (let ln of lines) {
      const h = ln.match(/^(#{3,6})\s+(.*)$/);
      const li = ln.match(/^\s*[-*]\s+(.*)$/);
      if (h) { if (inList) { html += '</ul>'; inList = false; } html += `<div style="font-weight:700;color:#e8eefc;margin:8px 0 3px">${h[2]}</div>`; }
      else if (li) { if (!inList) { html += '<ul style="margin:2px 0 6px 16px">'; inList = true; } html += `<li style="margin:1px 0">${li[1]}</li>`; }
      else if (/^\s*---+\s*$/.test(ln)) { if (inList) { html += '</ul>'; inList = false; } html += '<hr style="border:none;border-top:1px solid #1b2740;margin:8px 0">'; }
      else if (ln.trim() === '') { if (inList) { html += '</ul>'; inList = false; } html += '<div style="height:6px"></div>'; }
      else { if (inList) { html += '</ul>'; inList = false; } html += `<div>${ln}</div>`; }
    }
    if (inList) html += '</ul>';
    out += html;
  });
  return out;
}

async function worldBible() {
  try {
    const [w, t] = await Promise.all([
      api('/api/world/bible/word'),
      api('/api/world/bible/teachings?limit=100'),
    ]);
    _bibleData = { word: w, teachings: t.teachings || [], tab: 'word', chapter: 0,
                   label: t.label || null, opsLabel: t.ops_label || null };
  } catch (e) { toast?.('The Bible failed to load'); return; }
  _renderBible();
}

function _renderBible() {
  const d = _bibleData, w = d.word || {}, chs = w.chapters || [];
  const tab = (id, label) => `<button class="btn" style="padding:4px 12px;font-size:.76rem;${d.tab === id ? 'background:#2a1f4a;border-color:#6d5aff;color:#c4b5fd' : ''}" onclick="bibleTab('${id}')">${label}</button>`;
  const tabs = `<div style="display:flex;gap:6px;margin-bottom:12px">${tab('word', '📜 The Word')}${tab('teachings', `✍️ Teachings${d.teachings.length ? ` (${d.teachings.length})` : ''}`)}</div>`;

  let body;
  if (d.tab === 'word') {
    const opts = [`<option value="-1">✦ ${esc(w.title || 'The Book')} — preface</option>`]
      .concat(chs.map((c, i) => `<option value="${i}" ${i === d.chapter ? 'selected' : ''}>${esc(c.title)}</option>`)).join('');
    const content = d.chapter < 0 ? _md(w.intro) : _md((chs[d.chapter] || {}).md);
    body = `
      <select onchange="bibleChapter(this.value)" style="width:100%;padding:7px 10px;background:#0b1120;border:1px solid #26324a;border-radius:8px;color:#e8eefc;font-size:.8rem;margin-bottom:10px">${opts}</select>
      <div style="font-size:.8rem;color:#c7d2e5;line-height:1.55;max-height:52vh;overflow:auto;padding-right:6px">${content || '<span style="color:#54607a">—</span>'}</div>`;
  } else {
    const KIND = { study: '📘', law: '⚖️', lesson: '🎓', revelation: '✨', ops: '🧭' };
    const badge = (t) => {
      if (t.kind !== 'ops') return '';
      const b = Number(t.stale)
        ? `<span style="font-size:.62rem;color:#fbbf24;border:1px solid #7c5e10;border-radius:99px;padding:1px 7px">⚠ stale — reference moved</span>`
        : Number(t.verified)
          ? `<span style="font-size:.62rem;color:#4ade80;border:1px solid #14532d;border-radius:99px;padding:1px 7px">✔ verified${t.verified_at ? ' ' + String(t.verified_at).slice(0, 10) : ''}</span>`
          : `<span style="font-size:.62rem;color:#7a86a0;border:1px solid #26324a;border-radius:99px;padding:1px 7px">unverified</span>`;
      const src = t.source ? `<span style="font-size:.62rem;color:#7dd3fc;border:1px solid #164e63;border-radius:99px;padding:1px 7px">${esc(t.source)}</span>` : '';
      return `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:5px">${src}${b}</div>`;
    };
    const refreshBtn = `<div style="display:flex;justify-content:flex-end;margin-bottom:8px">
      <button class="btn" style="padding:3px 10px;font-size:.7rem" onclick="bibleOpsRefresh()">⟳ Refresh ${esc(d.opsLabel || 'ops notes')}</button></div>`;
    body = refreshBtn + (d.teachings.length
      ? `<div style="max-height:52vh;overflow:auto;padding-right:6px">${d.teachings.map(t => `
        <div style="border:1px solid ${t.kind === 'ops' ? (Number(t.stale) ? '#7c5e10' : '#1d4a38') : '#26324a'};border-radius:9px;padding:10px 12px;margin-bottom:8px;background:#0e1626">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
            <b style="color:#e8eefc">${KIND[t.kind] || '📘'} ${esc(t.title)}</b>
            <span style="font-size:.66rem;color:#54607a;flex-shrink:0">${(t.created_at || '').slice(0, 10)}</span>
          </div>
          <div style="font-size:.8rem;color:#c7d2e5;line-height:1.5;margin:4px 0">${esc(t.verse)}</div>
          <div style="font-size:.66rem;color:#7a86a0">— ${esc(t.author || 'a scholar')}${t.book ? ` · on <span style="color:#9fb4d6">${esc(t.book)}</span>` : ''}</div>
          ${badge(t)}
        </div>`).join('')}</div>`
      : `<div style="color:#54607a;font-size:.82rem;padding:12px 0">No teachings yet. Blessed research/scout strategies add studies here, and “⟳ Refresh” harvests verified operational notes (file locations, tools, recent changes) straight from the live system.</div>`);
  }

  _worldModal(d.label || '📖 The Company Bible', tabs + body);
}

async function bibleOpsRefresh() {
  try {
    const r = await api('/api/world/bible/ops/refresh', { method: 'POST', body: '{}' });
    toast?.(`Ops notes: ${r.stored} new, ${r.updated} refreshed, ${r.rejected} rejected`);
    const t = await api('/api/world/bible/teachings?limit=100');
    _bibleData.teachings = t.teachings || [];
    _bibleData.label = t.label || _bibleData.label;
    _bibleData.opsLabel = t.ops_label || _bibleData.opsLabel;
    _renderBible();
  } catch (e) { toast?.('Ops refresh failed'); }
}

function bibleTab(t) { _bibleData.tab = t; _renderBible(); }
function bibleChapter(i) { _bibleData.chapter = parseInt(i, 10); _renderBible(); }

window.worldBible = worldBible;
window.bibleTab = bibleTab;
window.bibleChapter = bibleChapter;
window.bibleOpsRefresh = bibleOpsRefresh;

async function renderLibrary(mount = 'main-content') {
  const main = document.getElementById(mount);
  main.innerHTML = `
    <div class="view-header">
      <div class="view-title">&#128218; AI Library</div>
      <div class="view-sub">Offline knowledge base — code guides, OS guides, docs & solutions</div>
    </div>
    <div class="view-toolbar" style="display:flex;gap:12px;margin-bottom:20px;padding:0 14px;">
      <input id="lib-search" type="text" placeholder="Search library..." style="flex:1;padding:10px 14px;border-radius:8px;border:1px solid var(--border);background:var(--surface2);color:var(--text);font-size:.9rem;" onkeydown="if(event.key==='Enter')libDoSearch()" />
      <button class="btn" onclick="libDoSearch()" style="padding:10px 18px;">Search</button>
    </div>
    <div id="lib-actions" style="margin-bottom:20px;padding:0 14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;border-bottom:1px solid var(--border);padding-bottom:12px;">
      <div id="lib-breadcrumbs" style="font-size:.8rem;color:var(--muted);margin-right:auto;"></div>
      <button class="btn-sm" onclick="libDropLink()">&#128229; Drop Link</button>
      <button class="btn-sm" onclick="libArchiveView()">&#128452;&#65039; Archive</button>
      <button class="btn-sm" onclick="libAgentGuide()">&#129302; AI Guide</button>
      <button class="btn-sm" onclick="libManage()">&#9881;&#65039; Manage</button>
      <button class="btn-sm" onclick="libReviewLinks()">Review Pending</button>
    </div>
    <div id="lib-content"></div>
  `;
  libShowSections();
}

async function libShowSections() {
  const el = document.getElementById('lib-content');
  el.innerHTML = '<div class="empty">Loading...</div>';
  const data = await api('/api/library/sections');
  const sections = data.sections || [];
  let h = '<div class="proposals-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;">';
  for (const s of sections) {
    h += `<div class="stat-card" style="cursor:pointer;" onclick="libBrowse('${s.name}')">` +
      `<div style="font-size:2rem;margin-bottom:6px;">${libIcon(s.name)}</div>` +
      `<div style="font-weight:600;font-size:.95rem;">${libLabel(s.name)}</div>` +
      `<div style="color:var(--muted);font-size:.78rem;">${s.documents} documents</div>` +
      `</div>`;
  }
  h += '</div>';
  el.innerHTML = h;
}

async function libBrowse(category) {
  _libCurrentCategory = category;
  _libCurrentSub = null;
  const el = document.getElementById('lib-content');
  el.innerHTML = '<div class="empty">Loading...</div>';
  const data = await api(`/api/library/${category}`);
  
  // Update breadcrumbs
  const bread = document.getElementById('lib-breadcrumbs');
  if(bread) bread.innerHTML = `<span style="color:var(--muted);">Library</span> &gt; ${libLabel(category)}`;

  let h = `<div style="margin-bottom:14px;display:flex;align-items:center;gap:8px;">` +
    `<button class="btn-sm" onclick="libShowSections()">&larr; Back</button>` +
    `<span style="font-weight:600;font-size:1.1rem;">${libIcon(category)} ${libLabel(category)}</span></div>`;
  
  if (data.subsections && data.subsections.length) {
    h += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin-bottom:20px;">';
    for (const s of data.subsections) {
      h += `<div class="stat-card" style="cursor:pointer;padding:14px;" onclick="libBrowseSub('${category}','${s.name}')">` +
        `<div style="font-weight:600;">${s.name}</div>` +
        `<div style="color:var(--muted);font-size:.78rem;">${s.documents} docs</div></div>`;
    }
    h += '</div>';
  }
  
  if (data.documents && data.documents.length) {
    h += '<div style="font-size:.85rem;font-weight:600;color:var(--muted);margin-bottom:8px;">DOCUMENTS</div>';
    h += '<div style="display:flex;flex-direction:column;gap:4px;">';
    for (const d of data.documents) {
      const sz = d.size > 1024 ? (d.size/1024).toFixed(1)+'KB' : d.size+'B';
      h += `<div class="stat-card" style="cursor:pointer;padding:10px 14px;display:flex;justify-content:space-between;align-items:center;" onclick="libReadDoc('${category}','${d.path}')">` +
        `<span style="font-weight:500;">${esc(d.title || d.name)}</span>` +
        `<span style="color:var(--muted);font-size:.75rem;">${sz}</span></div>`;
    }
    h += '</div>';
  }
  
  if ((!data.subsections || !data.subsections.length) && (!data.documents || !data.documents.length)) {
    h += '<div class="empty"><div class="empty-icon">\u{1F4ED}</div>No documents yet</div>';
  }
  el.innerHTML = h;
}

async function libBrowseSub(category, sub) {
  _libCurrentSub = sub;
  const el = document.getElementById('lib-content');
  el.innerHTML = '<div class="empty">Loading...</div>';
  const data = await api(`/api/library/${category}/${sub}`);
  
  // Update breadcrumbs
  const bread = document.getElementById('lib-breadcrumbs');
  if(bread) bread.innerHTML = `<span style="color:var(--muted);">Library</span> &gt; ${libLabel(category)} &gt; ${sub}`;

  let h = `<div style="margin-bottom:14px;display:flex;align-items:center;gap:8px;">` +
    `<button class="btn-sm" onclick="libBrowse('${category}')">&larr; Back</button>` +
    `<span style="font-weight:600;font-size:1.1rem;">${sub}</span></div>`;
  
  if (data.documents && data.documents.length) {
    h += '<div style="display:flex;flex-direction:column;gap:4px;">';
    for (const d of data.documents) {
      const sz = d.size > 1024 ? (d.size/1024).toFixed(1)+'KB' : d.size+'B';
      h += `<div class="stat-card" style="cursor:pointer;padding:10px 14px;display:flex;justify-content:space-between;align-items:center;" onclick="libReadDoc('${category}','${d.path}')">` +
        `<span style="font-weight:500;">${esc(d.title || d.name)}</span>` +
        `<span style="color:var(--muted);font-size:.75rem;">${sz}</span></div>`;
    }
    h += '</div>';
  } else {
    h += '<div class="empty"><div class="empty-icon">\u{1F4ED}</div>No documents in this section</div>';
  }
  el.innerHTML = h;
}

async function libReadDoc(category, docPath) {
  const el = document.getElementById('lib-content');
  el.innerHTML = '<div class="empty">Loading document...</div>';
  const url = `/api/library/read?category=${encodeURIComponent(category)}&path=${encodeURIComponent(docPath)}`;
  try {
    const doc = await api(url);
    _libCurDoc = { category, path: docPath };
    let h = `<div style="margin-bottom:14px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">` +
      `<button class="btn-sm" onclick="libBrowse('${category}')">&larr; Back</button>` +
      `<span style="font-weight:600;font-size:1.1rem;margin-right:auto;">${esc(doc.title)}</span>` +
      `<button class="btn-sm" onclick="libDocDetails()">&#8505;&#65039; Details</button>` +
      `<button class="btn-sm" onclick="libDocEnrich()">&#10024; Enrich</button>` +
      `<button class="btn-sm" onclick="libDocSummarize()">&#128221; Summarize</button></div>`;
    h += `<div style="color:var(--muted);font-size:.75rem;margin-bottom:12px;">${doc.line_count} lines`;
    if (doc.languages && doc.languages.length) {
      h += ` &middot; code: ${doc.languages.join(', ')}`;
    }
    h += `</div>`;
    // Backend now returns pre-rendered HTML (doc.html). Fall back to a POST render
    // only if an older payload without html shows up — never a giant GET query param.
    const rendered = doc.html || '';
    h += `<div style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:16px;overflow:auto;max-height:70vh;font-size:.82rem;line-height:1.5;">${rendered}</div>`;
    el.innerHTML = h;
  } catch(e) {
    el.innerHTML = `<div class="empty"><div class="empty-icon">\u{274C}</div>${esc(e.message)}</div>`;
  }
}

async function libDoSearch() {
  const q = document.getElementById('lib-search').value.trim();
  if (!q) return;
  const el = document.getElementById('lib-content');
  el.innerHTML = '<div class="empty">Searching...</div>';
  const data = await api(`/api/library/search?q=${encodeURIComponent(q)}`);
  const results = data.results || [];
  let h = `<div style="margin-bottom:14px;font-weight:600;">${results.length} results for "${esc(q)}"</div>`;
  if (results.length === 0) {
    h += '<div class="empty"><div class="empty-icon">\u{1F50D}</div>No matches found</div>';
  } else {
    h += '<div style="display:flex;flex-direction:column;gap:8px;">';
    for (const r of results) {
      h += `<div class="stat-card" style="cursor:pointer;padding:14px;" onclick="libReadDoc('${r.path.split('/')[0]}','${r.path.split('/').slice(1).join('/')}')">` +
        `<div style="font-weight:600;margin-bottom:4px;">${esc(r.name)}</div>` +
        `<div style="color:var(--muted);font-size:.75rem;margin-bottom:6px;">${esc(r.path)} &middot; ${r.match_count} matches</div>`;
      for (const m of r.matches.slice(0, 2)) {
        h += `<div style="font-size:.78rem;color:var(--muted);background:var(--surface);padding:4px 8px;border-radius:4px;margin-top:2px;font-family:monospace;white-space:pre-wrap;">L${m.line}: ${esc(m.context.split('\n')[1] || m.context)}</div>`;
      }
      h += `</div>`;
    }
    h += '</div>';
  }
  el.innerHTML = h;
}

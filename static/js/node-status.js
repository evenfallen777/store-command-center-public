'use strict';

/* ── GLOBAL NODE-STATUS INDICATOR ──
   The GPU node's owner-visible signal used to be buried inside Settings → System
   (admin-node.js's loadNodePanel(), driven by the HEAVY GET /api/node/status SSH
   probe) — nothing surfaced it store-wide, so a node could sit down for a long
   time unnoticed. This polls the CHEAP GET /api/node/ping (TCP-connect probe,
   cached server-side ~25s) on a light interval and drives two always-visible
   pieces, both outside renderView()'s replaced #main-content so they persist
   across every tab:
     - a small dot in the topbar (green = reachable, red = not) — subtle, always there.
     - when unreachable: a fixed banner across the very top of the page (same
       pattern as the default-password banner in app-core.js) with unmissable text.
   Wired into the DOMContentLoaded bootstrap in index.html next to startQueuePolling(). */

const NODE_PING_INTERVAL_MS = 25000;

let _nodePingPoller = null;
let _nodeUp = true; // optimistic until the first check lands, so we don't flash red on load

function startNodePolling() {
  loadNodePing();
  if (_nodePingPoller) clearInterval(_nodePingPoller);
  _nodePingPoller = setInterval(() => { if (!document.hidden) loadNodePing(); }, NODE_PING_INTERVAL_MS);
}

async function loadNodePing() {
  try {
    const s = await api('/api/node/ping');
    _setNodeStatus(!!s.reachable);
  } catch {
    // The ping endpoint itself is unreachable (e.g. server mid-restart) — that's
    // not the same as "node offline"; leave the last-known state alone rather
    // than raising a false alarm.
  }
}

function _setNodeStatus(up) {
  _nodeUp = up;
  const dot = document.getElementById('topbar-node-dot');
  const stat = document.getElementById('topbar-node-stat');
  const label = document.getElementById('topbar-node-label');
  if (dot) {
    dot.classList.toggle('node-up', up);
    dot.classList.toggle('node-down', !up);
  }
  if (stat) stat.classList.toggle('node-down', !up);
  if (label) label.textContent = up ? 'Node' : 'Node offline';
  if (stat) stat.title = up
    ? 'GPU node reachable'
    : 'GPU node unreachable — image / video / music / 3D generation paused. Click to check.';
  _renderNodeBanner(up);
}

function _renderNodeBanner(up) {
  let b = document.getElementById('node-offline-banner');
  if (up) {
    if (b) b.remove();
    return;
  }
  if (b) return; // already showing
  b = document.createElement('div');
  b.id = 'node-offline-banner';
  // Stack below the default-password banner if that's also up, so neither hides the other.
  const pw = document.getElementById('default-pw-banner');
  const top = pw ? `${Math.ceil(pw.getBoundingClientRect().height)}px` : '0';
  b.style.cssText = `position:fixed;top:${top};left:0;right:0;z-index:1199;background:#5c1414;color:#ffd7d7;` +
    'padding:8px 14px;font-size:.85rem;display:flex;gap:10px;align-items:center;justify-content:center;' +
    'flex-wrap:wrap;text-align:center;box-shadow:0 2px 12px rgba(0,0,0,.4);border-bottom:2px solid var(--red);';
  b.innerHTML = `<span class="node-banner-pulse">&#9888;&#65039;</span> ` +
    `<b>GPU node offline</b> &mdash; image / video / music / 3D generation paused.` +
    `<button style="background:var(--red);color:#fff;border:none;border-radius:6px;padding:4px 12px;font-weight:700;cursor:pointer" ` +
    `onclick="switchView('settings')">Check node</button>`;
  document.body.appendChild(b);
}

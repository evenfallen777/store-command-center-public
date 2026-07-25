#!/usr/bin/env node
// Bug-B regression check: static/js/world-render.js's _spriteOffset used to be
// `((id*7)%5-2)*5` — only 5 distinct offsets, so every 5th of the 39 world agents
// stacked pixel-perfectly on top of each other. Verifies the hash fan-out gives
// every id 0..38 a distinct offset, within the intended ~6-20px radius band.
// Run from the store root: node tools/check_sprite_offsets.js
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'world-render.js'), 'utf8');
const m = src.match(/function _spriteOffset[\s\S]*?\n}/);
if (!m) { console.error('FAIL: _spriteOffset not found in world-render.js'); process.exit(2); }
eval(m[0]);

const N = 39;
const seen = new Map();
let fail = false;
for (let id = 0; id < N; id++) {
  const o = _spriteOffset({ id });
  const key = o.x.toFixed(3) + ',' + o.y.toFixed(3);
  if (seen.has(key)) { console.error(`FAIL: id ${id} collides with id ${seen.get(key)} at (${key})`); fail = true; }
  seen.set(key, id);
  const rad = Math.hypot(o.x, o.y);
  if (rad < 5 || rad > 21) { console.error(`FAIL: id ${id} radius ${rad.toFixed(1)} out of ~6-20px band`); fail = true; }
}
console.log(`distinct offsets: ${seen.size}/${N}`);
console.log(`RESULT: ${fail ? 'FAIL' : 'PASS'}`);
process.exit(fail ? 1 : 0);

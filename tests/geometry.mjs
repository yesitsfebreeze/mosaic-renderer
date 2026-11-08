import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const source = await readFile(new URL('../src/shaders/voxel.frag', import.meta.url), 'utf8');
// Only bindings and the entry point change; tested functions are production GLSL.
const fragment = source
  .replace(/layout\(std140, set = 0, binding = 1\) uniform Fs_Params \{([\s\S]*?)\};/,
    (_, members) => members.replace(/vec4 (\w+);/g, 'uniform vec4 $1;'))
  .replace('std430, set = 0, binding = 2', 'std430, binding = 2')
  .replace('void main() {', 'void renderer_main() {') + `
uniform vec3 testOrigin;
uniform vec3 testDirection;
uniform uvec2 testQuery;
uniform int testMode;
uniform int testAxis;
void main() {
    if (testMode == 2) outColor = propertyValue(testQuery.x, testQuery.y, testOrigin, vec4(-9.0));
    else if (testMode == 3) outColor = propertyGradient(testQuery.x, testQuery.y, testAxis);
    else {
        Hit hit;
        bool found = trace(testOrigin, normalize(testDirection), hit);
        outColor = !found ? vec4(-1.0, 0.0, 0.0, 0.0)
            : testMode == 1 ? vec4(float(hit.material), float(hit.state), 0.0, 0.0)
            : vec4(hit.distance, hit.normal);
    }
}
`;
const floatWord = value => new Uint32Array(new Float32Array([value]).buffer)[0];
const fixed = value => {
  assert.ok(value >= 0 && value <= 32 && Number.isInteger(value * 256));
  return value * 256;
};
const box = (lower, upper, material = 1, state = 0) => ({ lower, upper, material, state });
const cell = (x, y, z, material = 1, state = 0) => box([x, y, z], [x + 1, y + 1, z + 1], material, state);

// Project explicit fixture AABBs; the oracle uses original boxes, never these records.
function encode(boxes, states = [[]], size = 32) {
  const columns = [], runs = [];
  for (let axis = 0; axis < 3; ++axis) {
    const u = (axis + 1) % 3, v = (axis + 2) % 3;
    for (let j = 0; j < size; ++j) for (let i = 0; i < size; ++i) {
      const row = boxes.filter(b => b.lower[u] < i + 1 && b.upper[u] > i && b.lower[v] < j + 1 && b.upper[v] > j)
        .map(b => [fixed(b.lower[axis]), fixed(b.upper[axis]), b.material | (b.state << 8),
          fixed(Math.max(b.lower[u] - i, 0)) | (fixed(Math.max(b.lower[v] - j, 0)) << 16),
          fixed(Math.min(b.upper[u] - i, 1)) | (fixed(Math.min(b.upper[v] - j, 1)) << 16)])
        .sort((a, b) => a[0] - b[0]);
      const merged = [];
      for (const run of row) {
        const prev = merged.at(-1);
        if (prev && prev[1] === run[0] && prev.slice(2).every((x, k) => x === run[k + 2])) prev[1] = run[1];
        else merged.push(run);
      }
      columns.push(runs.length / 4, merged.length);
      for (const [a, b, state, lo, hi] of merged) runs.push((a | (b << 16)) >>> 0, state >>> 0, lo >>> 0, hi >>> 0);
    }
  }
  const header = new Array(16).fill(0), materials = new Array(256 * 8).fill(0);
  const stateWords = [], properties = [];
  for (const state of states) {
    stateWords.push(properties.length / 20, state.length);
    for (const p of state) {
      const vector = key => Array.from({ length: 4 }, (_, i) => floatWord(p[key]?.[i] ?? 0));
      properties.push(p.id >>> 0, p.components ?? 1, 0, 0, ...vector('base'), ...vector('gx'), ...vector('gy'), ...vector('gz'));
    }
  }
  header[0] = size; header[1] = 16; header[2] = 16 + columns.length; header[3] = runs.length / 4;
  header[4] = header[2] + runs.length; header[5] = 256;
  header[6] = header[4] + materials.length; header[7] = states.length;
  header[8] = header[6] + stateWords.length; header[9] = properties.length / 20;
  header[10] = header[8] + properties.length;
  return [...header, ...columns, ...runs, ...materials, ...stateWords, ...properties];
}
const fixtures = [];
function ray(name, boxes, origin, direction, expected, options = {}) {
  fixtures.push({ name, words: encode(boxes, options.states), origin, direction, expected, ...options });
}
const rotate = (v, axis) => v.map((_, i) => v[(i - axis + 3) % 3]);
const rotateBoxes = (boxes, axis) => boxes.map(b => ({ ...b, lower: rotate(b.lower, axis), upper: rotate(b.upper, axis) }));
for (let axis = 0; axis < 3; ++axis) {
  const r = (name, boxes, origin, direction, expected, options) => ray(`${name}, axis ${axis}`, rotateBoxes(boxes, axis),
    rotate(origin, axis), rotate(direction, axis), expected && [expected[0], ...rotate(expected.slice(1), axis)], options);
  r('empty field', [], [-1, 1.5, 1.5], [1, 0, 0], null);
  r('full cell', [cell(1, 1, 1)], [-1, 1.5, 1.5], [1, 0, 0], [2, -1, 0, 0]);
  r('reverse face', [cell(1, 1, 1)], [34, 1.5, 1.5], [-1, 0, 0], [32, 1, 0, 0]);
  r('depth 32 boundary', [cell(31, 1, 1)], [33, 1.5, 1.5], [-1, 0, 0], [1, 1, 0, 0]);
  r('separate layers preserve air', [cell(1, 1, 1), cell(5, 1, 1)], [3, 1.5, 1.5], [1, 0, 0], [2, -1, 0, 0]);
  r('inside cell', [cell(1, 1, 1)], [1.25, 1.5, 1.5], [1, 0, 0], [0.75, 1, 0, 0]);
  r('connected material exit', [cell(1, 1, 1), cell(2, 1, 1)], [1.25, 1.5, 1.5], [1, 0, 0], [1.75, 1, 0, 0]);
  r('internal face positive', [cell(1, 1, 1), cell(2, 1, 1)], [2, 1.5, 1.5], [1, 0, 0], [1, 1, 0, 0]);
  r('internal face negative', [cell(1, 1, 1), cell(2, 1, 1)], [2, 1.5, 1.5], [-1, 0, 0], [1, -1, 0, 0]);
  r('material interface', [cell(1, 1, 1), cell(2, 1, 1, 2)], [1.25, 1.5, 1.5], [1, 0, 0], [0.75, 1, 0, 0]);
  r('fractional endpoint', [box([1, 1, 1], [1.75, 2, 2])], [3, 1.5, 1.5], [-1, 0, 0], [1.25, 1, 0, 0]);
  r('partial cross-section hit', [box([1.25, 1.5, 1.25], [1.75, 2, 1.75])], [-1, 1.75, 1.5], [1, 0, 0], [2.25, -1, 0, 0]);
  r('partial cross-section miss', [box([1.25, 1.5, 1.25], [1.75, 2, 1.75])], [-1, 1.25, 1.5], [1, 0, 0], null);
  r('fractional gap stays air', [box([1, 1, 1], [1.5, 2, 2]), cell(2, 1, 1)], [1.25, 1.5, 1.5], [1, 0, 0], [0.25, 1, 0, 0]);
  r('shadow offset leaves surface', [box([1, 1, 1], [1.75, 2, 2])], [1.7502, 1.5, 1.5], [1, 0, 0], null);
  r('shadow finds separate blocker', [box([1, 1, 1], [1.75, 2, 2]), cell(3, 1, 1)], [1.7502, 1.5, 1.5], [1, 0, 0], [1.2498, -1, 0, 0]);
  r('parallel outside', [cell(1, 1, 1)], [-1, -0.5, 1.5], [1, 0, 0], null);
  r('near parallel', [cell(1, 1, 1)], [-1, 1.5, 1.5], [1, 1e-9, 0], [2, -1, 0, 0]);
  r('simultaneous crossing excludes corner touches', [cell(1, 0, 1), cell(0, 1, 1)], [-1, -1, 1.5], [1, 1, 0], null);
  r('start on outside face', [cell(1, 1, 1)], [1, 1.5, 1.5], [1, 0, 0], [0, -1, 0, 0]);
  const alternating = Array.from({ length: 32 }, (_, i) => cell(i, 1, 1, i % 2 + 1));
  r('32 alternating runs forward', alternating, [30.25, 1.5, 1.5], [1, 0, 0], [0.75, 1, 0, 0]);
  r('32 alternating runs reverse', alternating, [31.25, 1.5, 1.5], [-1, 0, 0], [0.25, -1, 0, 0]);
}
ray('material and state IDs', [cell(1, 1, 1, 255, 1)], [-1, 1.5, 1.5], [1, 0, 0], [255, 1, 0, 0], { mode: 1, states: [[], []] });
const states = [[], [{ id: 1, base: [1400], gx: [-10], gy: [20], gz: [-5] },
  { id: 0, components: 4, base: [1, 2, 3, 4], gx: [1, 2, 0, 0], gy: [0, 0, 3, 0], gz: [0, 0, 0, 4] },
  { id: 0xfedcba98, components: 2, base: [7, -3], gx: [2, 4], gy: [-1, 0], gz: [0, 1] }],
  [{ id: 1, base: [300] }]];
for (const [name, id, expected] of [
  ['temperature gradient', 1, [1420, 0, 0, 0]], ['property ID zero', 0, [3, 6, 12, 20]],
  ['arbitrary uint property ID', 0xfedcba98, [8, 9, 0, 0]], ['absent property default', 99, [-9, -9, -9, -9]],
]) fixtures.push({ name, words: encode([], states), origin: [2, 3, 4], direction: [1, 0, 0], mode: 2, query: [1, id], expected });
for (let axis = 0; axis < 3; ++axis) fixtures.push({ name: `property derivative ${axis}`, words: encode([], states),
  origin: [2, 3, 4], direction: [1, 0, 0], mode: 3, axis, query: [1, 1], expected: [[-10, 0, 0, 0], [20, 0, 0, 0], [-5, 0, 0, 0]][axis] });
for (const state of [1, 2]) ray(`temperature preserves geometry, state ${state}`, [cell(1, 1, 1, 1, state)], [-1, 1.5, 1.5], [1, 0, 0], [2, -1, 0, 0], { states });
ray('state change has no internal surface', [cell(1, 1, 1, 1, 1), cell(2, 1, 1, 1, 2)], [1.25, 1.5, 1.5], [1, 0, 0], [1.75, 1, 0, 0], { states });

// Independent slabs over original AABBs: no column traversal or packed-data decoding.
function oracle(boxes, origin, direction) {
  const d = direction.map(x => x / Math.hypot(...direction)), spans = [];
  for (const b of boxes) {
    let enter = -Infinity, leave = Infinity, near = [0, 0, 0], far = [0, 0, 0], valid = true;
    for (let a = 0; a < 3; ++a) {
      if (d[a] === 0) { if (origin[a] < b.lower[a] || origin[a] > b.upper[a]) valid = false; continue; }
      const t0 = (b.lower[a] - origin[a]) / d[a], t1 = (b.upper[a] - origin[a]) / d[a];
      if (Math.min(t0, t1) > enter) { enter = Math.min(t0, t1); near = [0, 0, 0]; near[a] = -Math.sign(d[a]); }
      if (Math.max(t0, t1) < leave) { leave = Math.max(t0, t1); far = [0, 0, 0]; far[a] = Math.sign(d[a]); }
    }
    if (valid && leave > enter && leave >= 0) spans.push({ enter, leave, near, far, material: b.material });
  }
  spans.sort((a, b) => a.enter - b.enter);
  if (!spans.length) return null;
  const first = spans[0];
  if (first.enter >= 0) return [first.enter, ...first.near];
  let end = first.leave, normal = first.far;
  for (const s of spans.slice(1)) {
    if (s.enter > end + 1e-9 || s.material !== first.material) break;
    if (s.leave > end) { end = s.leave; normal = s.far; }
  }
  return [end, ...normal];
}
let seed = 0x846aca68;
const random = () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 2 ** 32);
const randomBoxes = [];
for (let z = 8; z < 24; z += 2) for (let y = 8; y < 24; y += 2) for (let x = 8; x < 24; x += 2) {
  const lower = [x, y, z].map(v => v + Math.floor(random() * 3) / 4);
  randomBoxes.push(box(lower, lower.map(v => Math.floor(v) + 1), 1 + Math.floor(random() * 4)));
}
const randomWords = encode(randomBoxes);
for (let i = 0; i < 240; ++i) {
  const b = randomBoxes[Math.floor(random() * randomBoxes.length)];
  const target = b.lower.map((v, a) => v + (b.upper[a] - v) * (0.2 + 0.6 * random()));
  const origin = i % 3 === 0 ? target : [random() * 40 - 4, random() * 40 - 4, random() * 40 - 4];
  const direction = i % 3 === 0 ? [random() - 0.5, random() - 0.5, random() - 0.5] : target.map((v, a) => v - origin[a]);
  fixtures.push({ name: `independent box oracle ${i}`, words: randomWords, origin, direction, expected: oracle(randomBoxes, origin, direction) });
}
const backend = spawnSync(process.env.PYTHON || 'python3', ['-B', fileURLToPath(new URL('./shader_backend.py', import.meta.url))],
  { input: JSON.stringify({ fragment, fixtures }), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, timeout: 120000 });
if (backend.error) throw backend.error;
assert.equal(backend.status, 0, backend.stderr || backend.stdout);
const { results, renderer } = JSON.parse(backend.stdout);
assert.equal(results.length, fixtures.length);
for (let i = 0; i < fixtures.length; ++i) {
  const fixture = fixtures[i], actual = results[i];
  if (fixture.expected === null) assert.equal(actual[0], -1, `${fixture.name}: unexpected hit ${actual}`);
  else fixture.expected.forEach((expected, component) => assert.ok(Number.isFinite(actual[component]) && Math.abs(actual[component] - expected) < 0.0001,
    `${fixture.name}, component ${component}: expected ${expected}, got ${actual[component]}`));
}
console.log(`Passed ${fixtures.length} actual-shader RLE, geometry and property fixtures (${renderer}).`);

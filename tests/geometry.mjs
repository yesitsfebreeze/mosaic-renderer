import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';

const { chromium } = createRequire(import.meta.url)('playwright');
const source = await readFile(new URL('../src/shaders/voxel.frag', import.meta.url), 'utf8');

// Adapt resource bindings for WebGL2; all intersection and traversal code is unchanged.
const fragment = source
  .replace('#version 450', '#version 300 es\nprecision highp float;\nprecision highp int;')
  .replace(/layout\(std140, set = 0, binding = 1\) uniform Fs_Params \{([\s\S]*?)\};/,
    (_, members) => members.replace(/vec4 (\w+);/g, 'uniform vec4 $1;'))
  .replace('layout(std430, set = 0, binding = 2) readonly buffer Scene { uint words[]; };',
    'uniform highp usampler2D testScene;\nuniform vec3 testOrigin;\nuniform vec3 testDirection;')
  .replace('return words[index];', 'return texelFetch(testScene, ivec2(int(index), 0), 0).r;')
  .replace('void main() {', 'void renderer_main() {') + `
void main() {
    Hit hit;
    bool found = trace(testOrigin, normalize(testDirection), hit);
    outColor = found ? vec4(hit.distance, hit.normal) : vec4(-1.0, 0.0, 0.0, 0.0);
}
`;

const fixtures = [];
const full = (x, y, z, material = 1) => [x, y, z, material];
const ray = (name, cells, origin, direction, expected) => fixtures.push({ name, cells, origin, direction, expected });
ray('empty field', [], [-1, 1.5, 1.5], [1, 0, 0], null);
ray('full cube', [full(1, 1, 1)], [-1, 1.5, 1.5], [1, 0, 0], [2, -1, 0, 0]);
ray('material 255', [full(1, 1, 1, 255)], [-1, 1.5, 1.5], [1, 0, 0], [2, -1, 0, 0]);
ray('zero direction components', [full(1, 1, 1)], [1.5, 1.5, -1], [0, 0, 1], [2, 0, 0, -1]);
ray('near parallel ray', [full(1, 1, 1)], [-1, 1.5, 1.5], [1, 1e-9, 0], [2, -1, 0, 0]);
ray('camera inside field', [full(1, 1, 1)], [0.5, 1.5, 1.5], [1, 0, 0], [0.5, -1, 0, 0]);
ray('camera inside cube', [full(1, 1, 1)], [1.25, 1.5, 1.5], [1, 0, 0], [0.75, 1, 0, 0]);
ray('camera inside connected cubes', [full(1, 1, 1), full(2, 1, 1)], [1.25, 1.5, 1.5], [1, 0, 0], [1.75, 1, 0, 0]);
ray('camera on internal face facing positive', [full(1, 1, 1), full(2, 1, 1)], [2, 1.5, 1.5], [1, 0, 0], [1, 1, 0, 0]);
ray('camera on internal face facing negative', [full(1, 1, 1), full(2, 1, 1)], [2, 1.5, 1.5], [-1, 0, 0], [1, -1, 0, 0]);
ray('camera inside connected half cubes', [[1, 1, 1, 1 | (16 << 8)], [2, 1, 1, 1 | (16 << 8)]], [1.25, 1.25, 1.5], [1, 0, 0], [1.75, 1, 0, 0]);
ray('connected full and half cube', [full(1, 1, 1), [2, 1, 1, 1 | (14 << 8)]], [1.25, 1.5, 1.5], [1, 0, 0], [1.25, 1, 0, 0]);
ray('half cube gap stays open', [[1, 1, 1, 1 | (14 << 8)], full(2, 1, 1)], [1.25, 1.5, 1.5], [1, 0, 0], [0.25, 1, 0, 0]);
ray('material interface remains visible', [full(1, 1, 1), full(2, 1, 1, 2)], [1.25, 1.5, 1.5], [1, 0, 0], [0.75, 1, 0, 0]);
ray('parallel outside field', [full(1, 1, 1)], [-1, -0.5, 1.5], [1, 0, 0], null);
ray('simultaneous grid crossing skips side cells', [full(1, 0, 1), full(0, 1, 1)], [-1, -1, 1.5], [1, 1, 0], null);
ray('simultaneous grid crossing reaches diagonal', [full(1, 1, 1)], [-1, -1, 1.5], [1, 1, 0], [2 * Math.SQRT2, -1, 0, 0]);
ray('central half cube', [[1, 1, 1, 1 | (14 << 8)]], [3, 1.5, 1.5], [-1, 0, 0], [1.5, 1, 0, 0]);
ray('missing half remains empty', [[1, 1, 1, 1 | (14 << 8)]], [1.75, -1, 1.5], [0, 1, 0], null);
ray('diagonal half plane', [[1, 1, 1, 1 | (17 << 8)]], [3, 1.75, 1.5], [-1, 0, 0], [1.75, Math.SQRT1_2, Math.SQRT1_2, 0]);
ray('invalid shape is empty', [[1, 1, 1, 1 | (27 << 8)]], [-1, 1.5, 1.5], [1, 0, 0], null);
ray('negative ray starts on grid plane', [full(0, 1, 1)], [1, 1.5, 1.5], [-1, 0, 0], [0, 1, 0, 0]);
ray('positive ray starts on grid plane', [full(1, 1, 1)], [1, 1.5, 1.5], [1, 0, 0], [0, -1, 0, 0]);
ray('ray coplanar with diagonal cut', [[1, 1, 1, 1 | (17 << 8)]], [1.5, 1.5, -1], [0, 0, 1], [2, 0, 0, -1]);
ray('ray outside parallel diagonal cut', [[1, 1, 1, 1 | (17 << 8)]], [1.6, 1.5, -1], [0, 0, 1], null);

let shape = 0;
for (let z = -1; z <= 1; ++z) for (let y = -1; y <= 1; ++y) for (let x = -1; x <= 1; ++x) {
  if (!(x || y || z)) continue;
  ++shape;
  const length = Math.hypot(x, y, z);
  const normal = [x / length, y / length, z / length];
  const cells = [[1, 1, 1, 1 | (shape << 8)]];
  ray(`half orientation ${shape}`, cells, normal.map(v => 1.5 + 0.2 * v), normal.map(v => -v), [0.2, ...normal]);
  ray(`half shadow offset ${shape}`, cells, normal.map(v => 1.5 + 0.0002 * v), normal, null);
}

const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
  args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
try {
  const page = await browser.newPage();
  const results = await page.evaluate(({ fragment, fixtures }) => {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    const gl = canvas.getContext('webgl2', { antialias: false });
    if (!gl || !gl.getExtension('EXT_color_buffer_float')) throw new Error('WebGL2 float rendering unavailable');
    const compile = (kind, source) => {
      const shader = gl.createShader(kind);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
      return shader;
    };
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, `#version 300 es
      void main() { vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); }`));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fragment));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    const framebuffer = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
    if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new Error('Incomplete float framebuffer');
    const scene = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, scene);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.uniform1i(gl.getUniformLocation(program, 'testScene'), 0);
    gl.uniform4f(gl.getUniformLocation(program, 'level'), 4, 64, 2, 0);
    gl.viewport(0, 0, 1, 1);
    return fixtures.map(fixture => {
      const cells = new Uint32Array(64);
      for (const [x, y, z, value] of fixture.cells) cells[x + 4 * (y + 4 * z)] = value;
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32UI, 64, 1, 0, gl.RED_INTEGER, gl.UNSIGNED_INT, cells);
      gl.uniform3fv(gl.getUniformLocation(program, 'testOrigin'), fixture.origin);
      gl.uniform3fv(gl.getUniformLocation(program, 'testDirection'), fixture.direction);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      const result = new Float32Array(4);
      gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.FLOAT, result);
      const error = gl.getError();
      if (error) throw new Error(`GL error ${error} in ${fixture.name}`);
      return Array.from(result);
    });
  }, { fragment, fixtures });
  for (let i = 0; i < fixtures.length; ++i) {
    const fixture = fixtures[i];
    const actual = results[i];
    if (fixture.expected === null) assert.equal(actual[0], -1, `${fixture.name}: unexpected hit ${actual}`);
    else fixture.expected.forEach((expected, component) => assert.ok(Number.isFinite(actual[component]) && Math.abs(actual[component] - expected) < 0.00003,
      `${fixture.name}, component ${component}: expected ${expected}, got ${actual[component]}`));
  }
  console.log(`Passed ${fixtures.length} actual-shader geometry fixtures (WebGL2/SwiftShader).`);
} finally {
  await browser.close();
}

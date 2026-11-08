# Mosaic Renderer

A native Mosaic/Vulkan renderer for the Matter64 cell model. The first milestone
is a deterministic **32 × 32 × 32** reference scene with full cubes and exact
diagonal half cells, a 256-entry material table, an orbit camera and shadows.

The application draws one fullscreen triangle and traces the cell field. Scene
data stays in one GPU storage buffer. Primary and shadow rays intersect the same
cell geometry. The current scene is static; painting, simulation, refraction,
global illumination and multiple chunks are subsequent milestones.

## Build and run

Requirements: Python 3.9+, a built Mosaic checkout, the Vulkan SDK's `glslc` or
`glslangValidator`, and a Vulkan 1.3 device/driver. The runtime provides a Win32
backend and a GLFW backend for Linux/macOS. Each platform still needs a native
build and graphics check; this repository does not include a prebuilt executable.

Mosaic's Vulkan runtime is an external dependency. Its repository is currently
private, so access to [yesitsfebreeze/mosaic](https://github.com/yesitsfebreeze/mosaic)
is required. Use this source revision:

```text
ab4f95d210bbaf4369fc471173c1a10b9ed1dcc5
```

You can use an existing checkout at that revision, or create a separate one:

```sh
git clone https://github.com/yesitsfebreeze/mosaic.git vendor/mosaic
git -C vendor/mosaic checkout ab4f95d210bbaf4369fc471173c1a10b9ed1dcc5
```

Build Mosaic following its [development instructions](https://github.com/yesitsfebreeze/mosaic/blob/ab4f95d210bbaf4369fc471173c1a10b9ed1dcc5/dev/README.md).
From the Mosaic checkout, the current commands are:

```sh
node dev/skill/scripts/wf.mjs run setup
node dev/skill/scripts/wf.mjs run build
```

The compiler's first build needs its documented native toolchain and may take
time. Linux also links the system GLFW library. The renderer build itself does
not download dependencies or rebuild the compiler. Back in this repository:

```sh
python tools/build.py doctor
python tools/build.py run
```

Use `python3` if that is your Python command. With an existing Mosaic checkout:

```sh
python tools/build.py run --mosaic-root ../mosaic
```

`--compiler` selects a different Mosaic executable; otherwise the script uses
`MOSAIC_COMPILER` or the selected checkout's `bin/mosaic` (`mosaic.exe` on Windows).
`--shader-compiler` selects `glslc` or `glslangValidator`. The build sets Mosaic's
`MOSAIC_ROOT` to the checkout's **src directory**, and adds its example runtime
through the `mosaic_runtime` collection.

```sh
python tools/build.py shaders  # Compile GLSL without requiring Mosaic
python tools/build.py build    # Generate shaders and compile the native application
python tools/build.py build --debug
```

Generated shaders and executables stay in ignored `src/gen_shaders.mi` and `build/`.
Shader generation replaces the generated file only after both shaders compile.
If `spirv-val` is installed, the build also validates their SPIR-V.

## Checks

CI runs Python build checks, compiles and validates the Vulkan shaders, and runs
70 geometry fixtures using the actual intersection code through WebGL2/SwiftShader.
Native Mosaic compilation, Vulkan window creation and hardware performance remain
separate target-machine checks.

```sh
python -m unittest discover -s tests -v
python tools/build.py shaders
npm install --no-save --no-package-lock playwright@1.62.1
npx playwright install --with-deps chromium
node tests/geometry.mjs
```

Playwright and Chromium are development-only test dependencies. An existing
Chromium executable can be selected with `CHROMIUM_EXECUTABLE`.

## Controls and measurement

| Input | Action |
| --- | --- |
| Left drag | Orbit the scene |
| Mouse wheel | Zoom |
| R | Reset the view |
| S | Toggle sun shadows |
| Escape | Quit |

The console reports frame time and the voxel pass's GPU timestamp measurement
when supported. Frame time includes presentation and can be limited by VSync.
**200 FPS means a whole-frame budget of 5 ms**; the project has no verified FPS
claim yet. Record the GPU, framebuffer resolution, presentation mode and scene
alongside any benchmark.

## Cell and material contract

The dense grid has 32,768 cells, stored with index `x + 32 * (y + 32 * z)`.
Each cell is one 32-bit word:

| Bits | Meaning |
| --- | --- |
| 0–7 | Material ID; 0 is empty, 1–255 are material entries |
| 8–15 | Shape: 0 is a full cube, 1–26 are oriented half cells |
| 16–31 | Reserved; zero |

For a half cell, decode `k = shape - 1`, then increment `k` when `k >= 13`.
The plane normal is `(k % 3 - 1, (k / 3) % 3 - 1, k / 9 - 1)`, using integer
division. The occupied half satisfies `dot(normal, local_position - 0.5) <= 0`.
The plane passes through the cell center, so every orientation occupies exactly
half the cube's volume. A diagonal half cube is a clipped cube, not a tetrahedron.
Material opacity and shape occupancy are separate fields.

After the cell words, the same buffer contains 256 material records, eight words
each: an albedo/opacity `vec4` followed by metallic/roughness/emission/F0 `vec4`.
Words preserve the floats' IEEE-754 bit patterns. The complete buffer is 136 KiB.
The shader uses a 128-byte camera uniform at set 0, binding 1, and the scene buffer
at binding 2. `level.y` is the material table's first word offset.

## Next milestones

1. Verify the native reference scene and timing on the target machine, including
   all half-cell orientations, adjacent surfaces and camera positions.
2. Add picking and a cursor-sized emitter using the same cell intersection rules,
   updating persistent GPU buffers.
3. Connect adjacent 32³ chunks into one continuous field, then add a shared
   material-driven simulation with explicit boundary exchange.
4. Add material optics and cached lighting against measured GPU budgets.

The native application adapts Mosaic's Vulkan voxel example. See
[third-party attribution](third_party/NOTICE.md) and its preserved license.

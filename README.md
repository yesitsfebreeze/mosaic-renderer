# Mosaic Renderer

A native Mosaic/Vulkan renderer for a **32 × 32 × 32 cube standing on one vertex**.
Three axis RLE stores provide six opposite-face views of the same geometry.
Fractional endpoints and transverse bounds retain partial cells; separate runs
preserve cavities and gaps. Materials and changing properties use lookup tables.

The application draws one fullscreen triangle. Primary and shadow rays traverse
RLE columns from a single GPU storage buffer, without a dense GPU cell fallback.
The current scene is static; painting, simulation, refraction, global illumination
and multiple chunks remain subsequent milestones.

## Build and run

Use your existing Mosaic development checkout and working compiler. From the
renderer repository on Windows:

```powershell
python tools/build.py doctor --mosaic-root D:\dev\language
python tools/build.py run --mosaic-root D:\dev\language
```

`--mosaic-root` is required for `doctor`, `build` and `run`. It selects the Mosaic
repository root, which must contain `src/core` and the voxel runtime. The compiler
defaults to its existing `bin/mosaic.exe` (`bin/mosaic` on Unix); `--compiler` can
select another existing compiler. Build Mosaic in its own project when developing
the language; renderer commands do not clone or modify that checkout, rebuild
Mosaic or LLVM, or download toolchain dependencies.

The build accepts advancing revisions and uncommitted Mosaic changes. Renderer
syntax and runtime usage were reviewed against `14a1f880e`; this is a reference,
not a revision requirement. Native renderer compilation against that revision
still needs target-machine verification. Future language or runtime API changes
may require renderer updates indicated by compiler diagnostics.

The renderer requires Python 3.9+, the Vulkan SDK's `glslc` or
`glslangValidator`, and a Vulkan 1.3 device/driver. Use `python3` if that is your
Python command. Linux links system GLFW. `doctor` checks dependency paths and
runs the selected compiler's `version` command; it does not create a Vulkan window.

The build sets `MOSAIC_ROOT` to the selected checkout's **src directory** and
exposes `dev/examples/voxel/runtime` through the `mosaic_runtime` collection.
`--shader-compiler` selects the GLSL compiler. Shader-only builds do not need
Mosaic:

```powershell
python tools/build.py shaders
python tools/build.py build --mosaic-root D:\dev\language
python tools/build.py build --mosaic-root D:\dev\language --debug
```

Generated renderer files stay in ignored `src/gen_shaders.mi` and `build/`.
Shader generation replaces the generated file only after both shaders compile;
when `spirv-val` is installed, it also validates their SPIR-V. Ctrl+C stops the
active native command's child process tree before returning exit code 130.

## Checks

CI runs Python build checks on Linux and Windows. These cover external checkout
selection, compiler arguments, shader generation and process cancellation.
The checks use compiler fixtures; they do not build Mosaic or verify native
renderer compilation.

Linux compiles and validates Vulkan SPIR-V, then executes the production GLSL
intersection and property functions in a surfaceless OpenGL 4.5 context using Mesa.
The test only adapts resource bindings and the entry point. Hand-expected fixtures
cover all six directions, depth 32, gaps, 32 alternating runs, fractional bounds,
inside starts, material interfaces, shadow offsets and arbitrary property IDs.
Seeded rays are also compared against an independent intersection oracle over the
original boxes. Cooling-state fixtures check that changing temperature leaves
geometry unchanged.

```sh
python -B -m unittest discover -s tests -v
python tools/build.py shaders
# Linux geometry test dependencies; no npm or Python packages are required.
sudo apt-get install --no-install-recommends libegl1 libgl1 libgl1-mesa-dri
node tests/geometry.mjs
```

The geometry check uses Node.js, Python 3 and Mesa EGL/OpenGL. `PYTHON` selects a
Python executable. It defaults to software rendering, so these checks do not
measure Vulkan performance. Native Mosaic compilation, native builder execution,
window creation and hardware performance require separate target-machine checks.
The native builder includes packed-data assertions executed when it builds the scene.

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

## RLE and property contract

The cube keeps local axes X/Y/Z while its body diagonal points upward in world
space. Camera and lighting vectors are transformed into that local frame.
Each axis has 32 × 32 columns. Opposite faces read the same ordered intervals in
reverse: `[a, b)` becomes `[32 - b, 32 - a)`. This needs three stores, not six
independently updated copies.

The CPU prepares explicit cell boxes and builds all three views from those same
boxes. Only touching runs with matching material, state and transverse bounds
merge. The GPU reads only the packed column/run representation. Every column can
retain up to 32 runs, including alternating material boundaries.

Depths use 1/256-cell fixed point in unsigned 16-bit fields. Coordinates cover
**0 through 32 inclusive**, so the far boundary encodes as 8192. The endpoint's
fraction is a boundary position; occupied volume depends on all three extents.
Changing a temperature or emission value never changes those extents.

The storage buffer begins with 16 words:

| Header word | Value |
| --- | --- |
| 0 | Grid size, 32 |
| 1–3 | Column word offset, run word offset, run count |
| 4–5 | Material word offset, material count |
| 6–7 | State word offset, state count |
| 8–9 | Property word offset, property count |
| 10 | Total buffer word count |
| 11–15 | Reserved, zero |

For axis `a`, transverse axes are `u = (a + 1) % 3` and `v = (a + 2) % 3`.
The column index is `a * 32 * 32 + uCoordinate + 32 * vCoordinate`. Each column
stores two words: first run **record index** and run count. Each four-word run
contains packed 16-bit start/end depths, an 8-bit material ID plus 24-bit state ID,
and packed 16-bit lower/upper transverse fractions for U/V. Transverse bounds are
relative to the column and range from 0 to 256. Material zero denotes air.

The 256-entry material table retains eight float words per entry: albedo/opacity
followed by metallic/roughness/emission/F0. Words preserve IEEE-754 bit patterns.
A two-word state record gives its first property **record index** and count.
Every property record has 20 words: an arbitrary unsigned ID, component count
1–4, two reserved zero words, then four float vectors `base`, `gx`, `gy`, `gz`.
ID zero is a valid property ID. Values at cube-local position `p` are:

```text
value = base + gx * p.x + gy * p.y + gz * p.z
```

The shader recognizes temperature in kelvin (ID 1), emission multiplier (ID 2)
and tint (ID 3); other IDs remain accessible through the same lookup functions.
This is an explicit affine field per state. Additional changes in a property
require another state/segment; endpoints alone cannot encode arbitrary internal
variation. Geometry is rectangular run prisms, without diagonal half-cell shapes
or an inferred smooth surface.

The camera uniform remains 128 bytes at set 0, binding 1; the scene buffer is
binding 2. Storage size depends on run and property counts. RLE saves space on
long uniform spans, with no fixed compression or FPS guarantee.

## Next milestones

1. Verify the native reference scene and timing on the target machine, including
   fractional boundaries, adjacent surfaces, all six face views and inside starts.
2. Add picking and a cursor-sized emitter using the same RLE intersection rules,
   updating persistent GPU buffers.
3. Connect adjacent 32³ chunks into one continuous field, then add a shared
   material-driven simulation with explicit boundary exchange.
4. Add material optics and cached lighting against measured GPU budgets.

The native application adapts Mosaic's Vulkan voxel example. See
[third-party attribution](third_party/NOTICE.md) and its preserved license.

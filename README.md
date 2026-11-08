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

From the renderer repository:

```sh
python tools/build.py setup
python tools/build.py run
```

Use `python3` if that is your Python command. `setup` checks out the required
Mosaic source into ignored `vendor/mosaic`, detached at this required revision from Mosaic's `main` history:

```text
ab4f95d210bbaf4369fc471173c1a10b9ed1dcc5
```

It runs Mosaic's official `wf.mjs run setup` and `wf.mjs run bootstrap` there,
then checks that checkout's `bin/mosaic` (`mosaic.exe` on Windows). Bootstrap
builds from Mosaic's private C++ seed and pinned stage compiler. The checkout
can be reused after a failed build; downloaded archives and LLVM caches are kept.
An existing directory with another repository, revision or source changes is refused,
apart from the two recorded workflow corrections described below.

At this pin, `dev/wf/setup-llvm.wf.md` tries to hash a literal
`(Get-Location).Path/justfile` path. Renderer setup corrects that single line to
hash the actual LLVM workflow file with SHA-256, so LLVM's cache records track
the recipe that produced them. Setup also corrects the bootstrap stage in
`dev/wf/build.wf.md`: it uses Windows' System32 tar for drive-letter paths and
temporarily exposes the prepared native toolchain to the extracted stage sources
through a directory junction. Required LLVM and tommath libraries are checked
first. The junction is removed after stage compilation, including on failure;
the original toolchain cache is retained.

These are renderer-owned dependency corrections. The Mosaic revision stays
pinned, and no archive checksum is changed. Setup checks both complete workflow
files against their committed originals and Git checkout filters before writing
either. Retries accept exactly these unstaged changes; other edits, file mode
changes, staged changes and untracked source files are refused.

Setup requires Git authenticated for the private
[Mosaic repository](https://github.com/yesitsfebreeze/mosaic), Node.js 22+, Python
3.9+, and Mosaic's [native build prerequisites](https://github.com/yesitsfebreeze/mosaic/blob/ab4f95d210bbaf4369fc471173c1a10b9ed1dcc5/dev/README.md).
On Windows, install PowerShell 7 and Visual Studio's C++ tools with its CMake/Ninja
component. The first Windows setup builds LLVM 22.1.8 from source and can take
substantial time; `MOSAIC_LLVM_JOBS` controls its parallelism. Later setup retries
reuse that cache.

Rendering also requires the Vulkan SDK's `glslc` or `glslangValidator` and a Vulkan
1.3 device/driver. Linux links system GLFW. `setup` does not require a shader
compiler; `doctor` checks the rendering build dependencies. Native window creation
and performance still need verification on the target machine.

`run` uses the selected checkout's compiler by default. An existing compatible
checkout can be selected explicitly:

```sh
python tools/build.py run --mosaic-root ../mosaic
```

Use `setup --mosaic-root <empty-directory>` to prepare a different isolated tool
directory. `--compiler` deliberately overrides the compiler for build/run, and
`--shader-compiler` selects the shader compiler. The build sets `MOSAIC_ROOT` to
the selected checkout's **src directory** and exposes its runtime through the
`mosaic_runtime` collection. Normal build/run commands do not download dependencies
or rebuild Mosaic.

```sh
python tools/build.py shaders  # Compile GLSL without requiring Mosaic
python tools/build.py build    # Generate shaders and compile the native application
python tools/build.py build --debug
```

Generated shaders and executables stay in ignored `src/gen_shaders.mi` and `build/`.
Shader generation replaces the generated file only after both shaders compile.
If `spirv-val` is installed, the build also validates their SPIR-V.

## Checks

CI runs Python build checks on Linux and Windows. The Windows job also executes
the corrected PowerShell hash expression and bootstrap stage with paths containing
spaces. It checks recipe cache hashes, real tar extraction, native library/helper
access through the junction, failure handling and preservation of the root cache.
These fixtures do not compile Mosaic.

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

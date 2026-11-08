# Mosaic Renderer

A native Mosaic/Vulkan renderer for the Matter64 cell model. The first milestone
is a deterministic **32 × 32 × 32** reference scene with full cubes and exact
diagonal half cells, a 256-entry material table, an orbit camera and shadows.

The application draws one fullscreen triangle and traces the cell field. Scene
data stays in one GPU storage buffer. Primary and shadow rays intersect the same
cell geometry. The current scene is static; painting, simulation, refraction,
global illumination and multiple chunks are subsequent milestones.

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
These fixtures do not compile Mosaic. Linux compiles and validates the Vulkan shaders and runs
77 geometry fixtures using the actual intersection code through WebGL2/SwiftShader.
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

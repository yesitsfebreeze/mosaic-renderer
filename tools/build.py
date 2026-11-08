#!/usr/bin/env python3
"""Set up the local Mosaic toolchain and build Mosaic Renderer."""

import argparse
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MOSAIC_REPOSITORY = "https://github.com/yesitsfebreeze/mosaic.git"
MOSAIC_REVISION = "ab4f95d210bbaf4369fc471173c1a10b9ed1dcc5"
LLVM_SETUP_RECIPE = "dev/wf/setup-llvm.wf.md"
LLVM_HASH_OLD = "    $recipeHash = (Get-FileHash -LiteralPath '(Get-Location).Path/justfile').Hash"
LLVM_HASH_FIXED = "    $recipeHash = (Get-FileHash -LiteralPath (Join-Path $repoRoot 'dev/wf/setup-llvm.wf.md') -Algorithm SHA256).Hash"
BOOTSTRAP_RECIPE = "dev/wf/build.wf.md"
BOOTSTRAP_STAGE_OLD = """        & tar -x -f $stageArchive -C $stageRoot
        if ($LASTEXITCODE -ne 0) { throw 'Could not extract the bootstrap stage sources' }
        $stageCompiler = Join-Path $stageRoot $exeName
        $previousRoot = $env:MOSAIC_ROOT
        $env:MOSAIC_ROOT = Join-Path $stageRoot 'src'
        Push-Location $stageRoot
        try {
            & $seed build (Join-Path $stageRoot 'src/compiler/cmd') "-collection:compiler=$(Join-Path $stageRoot 'src')" "-out:$stageCompiler" -o:speed
        } finally {
            Pop-Location
            $env:MOSAIC_ROOT = $previousRoot
        }"""
BOOTSTRAP_STAGE_FIXED = BOOTSTRAP_STAGE_OLD.replace(
    "        & tar -x -f $stageArchive -C $stageRoot",
    """        $stageTar = if ($IsWindows) { Join-Path $env:SystemRoot 'System32/tar.exe' } else { 'tar' }
        & $stageTar -x -f $stageArchive -C $stageRoot""",
).replace(
    "        $stageCompiler = Join-Path $stageRoot $exeName",
    """        $stageToolchain = $null
        if ($IsWindows) {
            $preparedToolchain = Join-Path $repoRoot '.mosaic/cache/toolchain'
            foreach ($library in @('bin/llvm/windows/LLVM-static.lib', 'bin/tommath/windows/tommath.lib')) {
                if (-not (Test-Path -LiteralPath (Join-Path $preparedToolchain $library) -PathType Leaf)) { throw "Missing prepared bootstrap library: $library" }
            }
            $stageCache = Join-Path $stageRoot '.mosaic/cache'
            [IO.Directory]::CreateDirectory($stageCache) | Out-Null
            $junction = Join-Path $stageCache 'toolchain'
        }
        $stageCompiler = Join-Path $stageRoot $exeName""",
).replace(
    "        try {\n            & $seed",
    """        try {
            if ($IsWindows) {
                New-Item -ItemType Junction -Path $junction -Target $preparedToolchain | Out-Null
                $stageToolchain = $junction
            }
            & $seed""",
).replace(
    "            $env:MOSAIC_ROOT = $previousRoot",
    """            $env:MOSAIC_ROOT = $previousRoot
            if ($stageToolchain) { [IO.Directory]::Delete($stageToolchain) }""",
)
SHADERS = ("voxel.vert", "voxel.frag")


class BuildError(Exception):
    pass


def executable(explicit, candidates, label):
    choices = [explicit] if explicit else candidates
    for choice in choices:
        if choice and (found := shutil.which(str(choice))):
            return str(Path(found).resolve())
    raise BuildError(f"{label} not found. Tried: {', '.join(map(str, choices))}")


def shader_compiler(explicit=None):
    candidates = []
    if sdk := os.environ.get("VULKAN_SDK"):
        for directory in ("Bin", "bin"):
            candidates.append(Path(sdk) / directory / ("glslc.exe" if os.name == "nt" else "glslc"))
    candidates.extend(("glslc", "glslangValidator"))
    return executable(explicit, candidates, "GLSL compiler (install the Vulkan SDK)")


def mosaic_toolchain(checkout, explicit=None):
    checkout = Path(checkout).expanduser().resolve()
    for required in ("src/core", "dev/examples/voxel/runtime/vk/gfx"):
        if not (checkout / required).is_dir():
            raise BuildError(f"Mosaic checkout is missing {required}: {checkout}. Run python tools/build.py setup, or use --mosaic-root <matching checkout>.")
    compiler = executable(
        explicit,
        [checkout / "bin" / ("mosaic.exe" if os.name == "nt" else "mosaic")],
        "Mosaic compiler (run python tools/build.py setup first)",
    )
    if (checkout / ".git").exists():
        revision = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=True, text=True, capture_output=True,
        ).stdout.strip()
        if revision != MOSAIC_REVISION:
            raise BuildError(f"Mosaic revision is {revision}; expected {MOSAIC_REVISION}. Run python tools/build.py setup for an isolated local toolchain.")
    else:
        raise BuildError("Mosaic must be a Git checkout so its pinned source revision can be verified.")
    env = dict(os.environ)
    env["MOSAIC_ROOT"] = str(checkout / "src")
    return checkout, compiler, env


def correct_mosaic_workflows(checkout, git):
    """Validate both recorded corrections before changing either pinned workflow."""
    corrections = ((LLVM_SETUP_RECIPE, LLVM_HASH_OLD, LLVM_HASH_FIXED),
                   (BOOTSTRAP_RECIPE, BOOTSTRAP_STAGE_OLD, BOOTSTRAP_STAGE_FIXED))
    status = subprocess.run(
        [git, "-C", str(checkout), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        check=True, text=True, capture_output=True,
    ).stdout
    allowed = {f" M {path}" for path, _, _ in corrections}
    if any(entry not in allowed for entry in status.split("\0") if entry):
        raise BuildError(f"Tool checkout is dirty; preserve its changes and choose an empty directory: {checkout}")
    modes = subprocess.run([git, "-C", str(checkout), "diff", "--summary"], check=True, capture_output=True).stdout
    if modes:
        raise BuildError(f"Tool checkout is dirty (file metadata changed): {checkout}")
    pending = []
    for path, before, after in corrections:
        original = subprocess.run([git, "-C", str(checkout), "show", f"HEAD:{path}"], check=True, capture_output=True).stdout
        if original.count(before.encode("utf-8")) != 1:
            raise BuildError(f"Pinned workflow has unexpected content: {path}; no corrections were applied.")
        original = subprocess.run([git, "-C", str(checkout), "cat-file", "--filters", f"HEAD:{path}"], check=True, capture_output=True).stdout
        newline = b"\r\n" if b"\r\n" in original else b"\n"
        old = before.encode("utf-8").replace(b"\n", newline)
        new = after.encode("utf-8").replace(b"\n", newline)
        if original.count(old) != 1:
            raise BuildError(f"Pinned workflow has unexpected content after Git checkout filters: {path}")
        corrected = original.replace(old, new, 1)
        current = (checkout / path).read_bytes()
        if current not in (original, corrected):
            raise BuildError(f"Tool checkout has unexpected changes in {path}; preserve them before setup.")
        if current == original:
            pending.append((path, corrected))
    for path, corrected in pending:
        (checkout / path).write_bytes(corrected)
        print(f"Applied renderer's pinned workflow correction: {path}")


def setup_mosaic(directory):
    checkout = Path(directory).expanduser().resolve()
    git = executable(None, ["git"], "Git")
    node = executable(None, ["node"], "Node.js (Mosaic setup requires Node 22+)")
    if checkout.exists() and not checkout.is_dir():
        raise BuildError(f"Tool path is not a directory: {checkout}")
    if not checkout.exists() or not any(checkout.iterdir()):
        checkout.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([git, "clone", "--no-checkout", MOSAIC_REPOSITORY, str(checkout)], check=True)
        subprocess.run([git, "-C", str(checkout), "checkout", "--detach", MOSAIC_REVISION], check=True)
    if not (checkout / ".git").exists():
        raise BuildError(f"Tool directory is not a Git checkout; choose an empty directory: {checkout}")

    def git_text(*args):
        return subprocess.run([git, "-C", str(checkout), *args], check=True, text=True, capture_output=True).stdout.strip()

    def repository_url(url):
        return url.removesuffix(".git").replace("git@github.com:", "https://github.com/").replace("ssh://git@github.com/", "https://github.com/")

    if repository_url(git_text("remote", "get-url", "origin")) != repository_url(MOSAIC_REPOSITORY):
        raise BuildError(f"Tool directory is an unrelated checkout: {checkout}")
    if git_text("rev-parse", "HEAD") != MOSAIC_REVISION:
        raise BuildError(f"Tool checkout has a different revision; choose an empty directory with --mosaic-root: {checkout}")
    correct_mosaic_workflows(checkout, git)
    env = dict(os.environ)
    env["MOSAIC_ROOT"] = str(checkout / "src")
    env["MOSAIC_BOOTSTRAP_REMOTE"] = "origin"
    env["MOSAIC_BOOTSTRAP_BRANCH"] = "bootstrap"
    for name in ("MOSAIC_BUILD_COMPILER", "MOSAIC_BUILD_SELF_ONLY", "MOSAIC_BUILD_LANE", "MOSAIC_BUILD_NO_INSTALL"):
        env.pop(name, None)
    for workflow in ("setup", "bootstrap"):
        subprocess.run([node, "dev/skill/scripts/wf.mjs", "run", workflow], cwd=checkout, env=env, check=True)
    owned_compiler = checkout / "bin" / ("mosaic.exe" if os.name == "nt" else "mosaic")
    _, compiler, _ = mosaic_toolchain(checkout, str(owned_compiler))
    subprocess.run([compiler, "version"], cwd=checkout, env=env, check=True)
    print(f"Mosaic toolchain ready: {checkout}")


def spirv_words(data, name):
    if len(data) < 20 or len(data) % 4 or data[:4] != b"\x03\x02\x23\x07":
        raise BuildError(f"Invalid SPIR-V output for {name}; generated source was not replaced.")
    return struct.unpack(f"<{len(data) // 4}I", data)


def compile_shaders(compiler):
    output = ["package main", "", "# Generated by tools/build.py. Edit src/shaders instead.", ""]
    validator = shutil.which("spirv-val")
    with tempfile.TemporaryDirectory(prefix="mosaic-renderer-shaders-") as temporary:
        for name in SHADERS:
            source = ROOT / "src" / "shaders" / name
            target = Path(temporary) / (name + ".spv")
            if Path(compiler).stem.lower() in ("glslang", "glslangvalidator"):
                flags = ["-V", "--target-env", "vulkan1.3", "-Os"]
            else:
                flags = ["--target-env=vulkan1.3", "-O"]
            result = subprocess.run([compiler, *flags, str(source), "-o", str(target)], capture_output=True, text=True)
            if result.returncode:
                raise BuildError(f"{name}: shader compilation failed\n{result.stdout}{result.stderr}".rstrip())
            words = spirv_words(target.read_bytes(), name)
            if validator:
                subprocess.run([validator, "--target-env", "vulkan1.3", str(target)], check=True)
            output.extend(("@rodata", f"{name.replace('.', '_')}_spv := [{len(words)}]u32{{"))
            for i in range(0, len(words), 8):
                output.append("\t" + ", ".join(f"0x{word:08x}" for word in words[i:i + 8]) + ",")
            output.extend(("}", ""))
    destination = ROOT / "src" / "gen_shaders.mi"
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=destination.parent, delete=False) as handle:
            staged = Path(handle.name)
            handle.write("\n".join(output))
        staged.replace(destination)
    finally:
        if staged and staged.exists():
            staged.unlink()
    print(f"Compiled {len(SHADERS)} shaders" + ("; SPIR-V validated." if validator else "."))


def build_command(compiler, checkout, output, debug=False):
    return [
        compiler, "build", str(ROOT / "src"),
        f"-collection:mosaic_runtime={checkout / 'dev/examples/voxel/runtime'}",
        f"-out:{output}", "-debug" if debug else "-o:speed",
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("setup", "doctor", "shaders", "build", "run"), nargs="?", default="build")
    parser.add_argument("--mosaic-root", type=Path, default=ROOT / "vendor" / "mosaic", help="Mosaic repository checkout (not its src directory)")
    parser.add_argument("--compiler", help="Explicit Mosaic executable for build/run; default <checkout>/bin/mosaic")
    parser.add_argument("--shader-compiler", help="glslc or glslangValidator executable")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            setup_mosaic(args.mosaic_root)
            return 0
        glsl = shader_compiler(args.shader_compiler)
        if args.command == "shaders":
            compile_shaders(glsl)
            return 0
        checkout, compiler, env = mosaic_toolchain(args.mosaic_root, args.compiler)
        if args.command == "doctor":
            print(f"Mosaic source: {checkout} ({MOSAIC_REVISION})")
            print(f"Mosaic compiler: {compiler}")
            print(f"GLSL compiler: {glsl}")
            subprocess.run([compiler, "version"], check=True, env=env)
            print("Build dependencies found. GPU/driver and window creation were not checked.")
            return 0
        compile_shaders(glsl)
        output = ROOT / "build" / ("mosaic-renderer.exe" if os.name == "nt" else "mosaic-renderer")
        output.parent.mkdir(exist_ok=True)
        subprocess.run(build_command(compiler, checkout, output, args.debug), cwd=ROOT, env=env, check=True)
        print(f"Built {output}")
        if args.command == "run":
            return subprocess.run([str(output)], cwd=ROOT, env=env).returncode
        return 0
    except (BuildError, OSError, subprocess.CalledProcessError) as error:
        print(f"build: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Build orchestration checks; these do not replace a native Mosaic/GPU test."""

import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("renderer_build", Path(__file__).resolve().parents[1] / "tools" / "build.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)
HEADER = struct.pack("<5I", 0x07230203, 0x00010000, 0, 1, 0)


class BuildChecks(unittest.TestCase):
    def test_missing_checkout_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(build.BuildError, "--mosaic-root"):
                build.mosaic_toolchain(root)

    def test_missing_executable_fails(self):
        with patch.object(build.shutil, "which", return_value=None):
            with self.assertRaisesRegex(build.BuildError, "not found"):
                build.executable("missing-compiler", [], "Mosaic compiler")

    def test_invalid_spirv_is_rejected(self):
        for data in (b"", b"invalid shader output", HEADER + b"x", b"\x00" * 20):
            with self.subTest(data=data), self.assertRaises(build.BuildError):
                build.spirv_words(data, "voxel.frag")
        self.assertEqual(build.spirv_words(HEADER, "voxel.frag"), (0x07230203, 0x00010000, 0, 1, 0))

    def test_shader_generation_and_failure_are_atomic_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="mosaic renderer ") as directory:
            root = Path(directory)
            shaders = root / "src" / "shaders"
            shaders.mkdir(parents=True)
            for name in build.SHADERS:
                (shaders / name).write_text("fixture", encoding="utf-8")
            generated = root / "src" / "gen_shaders.mi"
            generated.write_text("previous working source", encoding="utf-8")
            calls = []

            def compile_ok(command, **kwargs):
                self.assertNotIn("shell", kwargs)
                self.assertIn(str(shaders / build.SHADERS[len(calls)]), command)
                calls.append(command)
                Path(command[-1]).write_bytes(HEADER)
                return subprocess.CompletedProcess(command, 0, "", "")

            def fail_fragment(command, **kwargs):
                if str(shaders / "voxel.frag") in command:
                    return subprocess.CompletedProcess(command, 1, "", "fixture syntax error")
                Path(command[-1]).write_bytes(HEADER)
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(build, "ROOT", root), patch.object(build.shutil, "which", return_value=None):
                with patch.object(build.subprocess, "run", side_effect=fail_fragment):
                    with self.assertRaisesRegex(build.BuildError, "fixture syntax error"):
                        build.compile_shaders("/tools with spaces/glslc")
                self.assertEqual(generated.read_text(), "previous working source")
                with patch.object(build.subprocess, "run", side_effect=compile_ok):
                    build.compile_shaders("/tools with spaces/glslc")
                text = generated.read_text()
                self.assertIn("voxel_vert_spv := [5]u32{", text)
                self.assertIn("voxel_frag_spv := [5]u32{", text)
                self.assertIn("0x07230203", text)
                self.assertEqual(len(calls), 2)
                self.assertEqual(sorted(p.name for p in (root / "src").iterdir()), ["gen_shaders.mi", "shaders"])

    def test_compiler_paths_remain_single_arguments(self):
        command = build.build_command("/tools with spaces/mosaic", Path("/a checkout"), Path("/a build/renderer"))
        self.assertEqual(command[0], "/tools with spaces/mosaic")
        self.assertIn("-collection:mosaic_runtime=/a checkout/dev/examples/voxel/runtime", command)
        self.assertIn("-out:/a build/renderer", command)
        self.assertEqual(command[-1], "-o:speed")

    def test_glslang_resolved_names_use_glslang_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()

            def compile_glslang(command, **kwargs):
                self.assertEqual(command[1:5], ["-V", "--target-env", "vulkan1.3", "-Os"])
                self.assertNotIn("--target-env=vulkan1.3", command)
                Path(command[-1]).write_bytes(HEADER)
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(build, "ROOT", root), patch.object(build.shutil, "which", return_value=None):
                with patch.object(build.subprocess, "run", side_effect=compile_glslang):
                    for name in ("glslang", "glslangValidator", "glslang.exe", "glslangValidator.exe"):
                        with self.subTest(compiler=name):
                            build.compile_shaders(str(root / name))


class ToolchainSetupChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="renderer tool ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.origin = self.root / "source"
        self.origin.mkdir()
        for path in ("src/core/fixture.mi", "dev/examples/voxel/runtime/vk/gfx/fixture.mi", "dev/skill/scripts/wf.mjs"):
            target = self.origin / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixture", encoding="utf-8")
        (self.origin / ".gitignore").write_text("bin/\n", encoding="utf-8")
        self.real_run = subprocess.run
        self.git(self.origin, "init", "-q")
        self.git(self.origin, "add", ".")
        self.git(self.origin, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        self.revision = self.git(self.origin, "rev-parse", "HEAD").stdout.strip()
        self.checkout = self.root / "renderer" / "vendor" / "mosaic"
        self.workflows = []
        self.verified = []
        self.fail_workflow = None
        for name, value in (("MOSAIC_REPOSITORY", str(self.origin)), ("MOSAIC_REVISION", self.revision), ("ROOT", self.root / "renderer")):
            patcher = patch.object(build, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(build.subprocess, "run", side_effect=self.run_command)
        patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, directory, *args):
        return self.real_run(["git", "-C", str(directory), *args], check=True, text=True, capture_output=True)

    def run_command(self, command, **kwargs):
        if len(command) > 3 and command[-2] == "run" and command[-1] in ("setup", "bootstrap"):
            self.workflows.append(command[-1])
            self.assertEqual(Path(kwargs["cwd"]), self.checkout)
            self.assertEqual(kwargs["env"]["MOSAIC_ROOT"], str(self.checkout / "src"))
            self.assertEqual(kwargs["env"]["MOSAIC_BOOTSTRAP_REMOTE"], "origin")
            self.assertEqual(kwargs["env"]["MOSAIC_BOOTSTRAP_BRANCH"], "bootstrap")
            for name in ("MOSAIC_BUILD_COMPILER", "MOSAIC_BUILD_SELF_ONLY", "MOSAIC_BUILD_LANE", "MOSAIC_BUILD_NO_INSTALL"):
                self.assertNotIn(name, kwargs["env"])
            if command[-1] == self.fail_workflow:
                raise subprocess.CalledProcessError(9, command)
            if command[-1] == "bootstrap":
                compiler = self.checkout / "bin" / ("mosaic.exe" if os.name == "nt" else "mosaic")
                compiler.parent.mkdir(exist_ok=True)
                compiler.write_text("fixture compiler", encoding="utf-8")
                compiler.chmod(0o755)
            return subprocess.CompletedProcess(command, 0)
        if len(command) == 2 and command[1] == "version":
            self.verified.append(command[0])
            return subprocess.CompletedProcess(command, 0)
        return self.real_run(command, **kwargs)

    def test_setup_clones_exact_pin_retries_and_verifies_owned_compiler(self):
        overrides = {"MOSAIC_COMPILER": "/unrelated/compiler", "MOSAIC_BUILD_COMPILER": "/old/compiler",
                     "MOSAIC_BUILD_SELF_ONLY": "1", "MOSAIC_BUILD_LANE": "old", "MOSAIC_BUILD_NO_INSTALL": "1"}
        with patch.dict(os.environ, overrides):
            build.setup_mosaic(self.checkout)
            build.setup_mosaic(self.checkout)
            _, selected, _ = build.mosaic_toolchain(self.checkout)
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD").stdout.strip(), self.revision)
        self.assertEqual(self.git(self.checkout, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "HEAD")
        self.assertEqual(self.workflows, ["setup", "bootstrap", "setup", "bootstrap"])
        compiler = str((self.checkout / "bin" / ("mosaic.exe" if os.name == "nt" else "mosaic")).resolve())
        self.assertEqual(self.verified, [compiler, compiler])
        self.assertEqual(selected, compiler)

    def test_setup_refuses_dirty_different_and_unrelated_directories(self):
        self.checkout.parent.mkdir(parents=True)
        self.git(self.checkout.parent, "clone", "-q", str(self.origin), str(self.checkout))
        marker = self.checkout / "do-not-touch.txt"
        marker.write_text("user work", encoding="utf-8")
        with self.assertRaisesRegex(build.BuildError, "dirty"):
            build.setup_mosaic(self.checkout)
        self.assertEqual(marker.read_text(), "user work")
        marker.unlink()
        with patch.object(build, "MOSAIC_REVISION", "0" * 40), self.assertRaisesRegex(build.BuildError, "revision"):
            build.setup_mosaic(self.checkout)
        self.git(self.checkout, "remote", "set-url", "origin", str(self.root / "different"))
        with self.assertRaisesRegex(build.BuildError, "unrelated"):
            build.setup_mosaic(self.checkout)
        other = self.root / "unrelated-directory"
        other.mkdir()
        (other / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(build.BuildError, "Git checkout"):
            build.setup_mosaic(other)
        self.assertEqual(self.workflows, [])

    def test_setup_skips_shader_lookup_and_propagates_workflow_failure(self):
        self.fail_workflow = "bootstrap"
        with patch.object(build, "shader_compiler", side_effect=AssertionError("setup must not require GLSL")):
            self.assertEqual(build.main(["setup"]), 1)
        self.assertEqual(self.workflows, ["setup", "bootstrap"])
        self.assertEqual(self.verified, [])
        self.assertTrue((self.checkout / ".git").is_dir())


if __name__ == "__main__":
    unittest.main()

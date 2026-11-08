"""Build orchestration checks; these do not replace a native Mosaic/GPU test."""

import importlib.util
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


if __name__ == "__main__":
    unittest.main()

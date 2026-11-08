"""Build orchestration checks; these do not replace a native Mosaic/GPU test."""

import importlib.util
import contextlib
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
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
        self.assertIn(f"-collection:mosaic_runtime={Path('/a checkout') / 'dev/examples/voxel/runtime'}", command)
        self.assertIn(f"-out:{Path('/a build/renderer')}", command)
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


class DevelopmentCheckoutChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="renderer development ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.checkout = self.root / "Mosaic dev"
        for directory in ("src/core", "dev/examples/voxel/runtime/vk/gfx", ".git"):
            (self.checkout / directory).mkdir(parents=True)
        self.compiler = self.checkout / "bin" / ("mosaic.exe" if os.name == "nt" else "mosaic")
        self.compiler.parent.mkdir()
        self.compiler.write_text("fixture executable", encoding="utf-8")
        self.compiler.chmod(0o755)
        (self.checkout / "src/core/work.mi").write_text("uncommitted language development", encoding="utf-8")
        self.revision = "14a1f880e2002a5d962d23571e6df79acd67a847"
        (self.checkout / ".git/HEAD").write_text(self.revision, encoding="utf-8")

    def test_development_checkout_and_advancing_revision_are_used_without_writes(self):
        for revision in (self.revision, "advancing development checkout"):
            (self.checkout / ".git/HEAD").write_text(revision, encoding="utf-8")
            before = {path.relative_to(self.checkout): path.read_bytes() for path in self.checkout.rglob("*") if path.is_file()}
            with patch.object(build.subprocess, "run", side_effect=AssertionError("selection must not run Git or bootstrap")):
                checkout, compiler, env = build.mosaic_toolchain(self.checkout)
            self.assertEqual(checkout, self.checkout)
            self.assertEqual(compiler, str(self.compiler.resolve()))
            self.assertEqual(env["MOSAIC_ROOT"], str(self.checkout / "src"))
            self.assertEqual(before, {path.relative_to(self.checkout): path.read_bytes() for path in self.checkout.rglob("*") if path.is_file()})

    def test_build_and_run_use_selected_compiler_sources_runtime_and_renderer_cwd(self):
        renderer = self.root / "Renderer dev"
        renderer.mkdir()
        output = renderer / "build" / ("mosaic-renderer.exe" if os.name == "nt" else "mosaic-renderer")
        with patch.object(build, "ROOT", renderer), patch.object(build, "shader_compiler", return_value="glslc"), patch.object(build, "compile_shaders") as shaders, patch.object(build, "run_process", side_effect=[0, 7]) as run:
            self.assertEqual(build.main(["run", "--mosaic-root", str(self.checkout)]), 7)
        shaders.assert_called_once_with("glslc")
        command, cwd, env = run.call_args_list[0].args
        self.assertEqual(command, [str(self.compiler), "build", str(renderer / "src"),
                                  f"-collection:mosaic_runtime={self.checkout / 'dev/examples/voxel/runtime'}", f"-out:{output}", "-o:speed"])
        self.assertEqual(cwd, renderer)
        self.assertEqual(env["MOSAIC_ROOT"], str(self.checkout / "src"))
        self.assertEqual(run.call_args_list[1].args, ([str(output)], renderer, env))
        self.assertEqual(run.call_args_list[1].kwargs, {"check": False})

    def test_doctor_reports_observed_revision_and_runs_selected_compiler(self):
        observed = subprocess.CompletedProcess([], 0, self.revision + "\n", "")
        output = io.StringIO()
        with patch.object(build, "shader_compiler", return_value="glslc"), patch.object(build.subprocess, "run", return_value=observed) as git, patch.object(build, "run_process", return_value=0) as run, contextlib.redirect_stdout(output):
            self.assertEqual(build.main(["doctor", "--mosaic-root", str(self.checkout)]), 0)
        self.assertIn(self.revision, output.getvalue())
        self.assertEqual(git.call_args.args[0][-2:], ["rev-parse", "HEAD"])
        command, cwd, env = run.call_args.args
        self.assertEqual(command, [str(self.compiler), "version"])
        self.assertEqual(cwd, self.checkout)
        self.assertEqual(env["MOSAIC_ROOT"], str(self.checkout / "src"))

    def test_missing_compiler_and_runtime_are_actionable(self):
        self.compiler.unlink()
        with self.assertRaisesRegex(build.BuildError, "selected checkout or use --compiler"):
            build.mosaic_toolchain(self.checkout)
        (self.checkout / "dev/examples/voxel/runtime/vk/gfx").rmdir()
        with self.assertRaisesRegex(build.BuildError, "missing dev/examples/voxel/runtime/vk/gfx"):
            build.mosaic_toolchain(self.checkout)

    def test_explicit_compiler_override_is_preserved(self):
        other = self.root / ("other.exe" if os.name == "nt" else "other")
        other.write_text("fixture compiler", encoding="utf-8")
        other.chmod(0o755)
        _, compiler, _ = build.mosaic_toolchain(self.checkout, str(other))
        self.assertEqual(compiler, str(other))

    def test_native_commands_require_explicit_root_but_shaders_are_independent(self):
        for command in ("build", "run", "doctor"):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                build.main([command])
            self.assertEqual(error.exception.code, 2)
        with patch.object(build, "shader_compiler", return_value="glslc"), patch.object(build, "compile_shaders") as shaders:
            self.assertEqual(build.main(["shaders"]), 0)
        shaders.assert_called_once_with("glslc")


class NativeProcessChecks(unittest.TestCase):
    def test_interrupt_stops_descendant_and_releases_cache_lock(self):
        with tempfile.TemporaryDirectory(prefix="setup process tree ") as directory:
            root = Path(directory)
            leaf = root / "lock_holder.py"
            leaf.write_text(
                "import os, sys, time\nfrom pathlib import Path\n"
                "root = Path(sys.argv[1])\n"
                "handle = (root / 'cache.lock').open('w+b')\nhandle.write(b'x'); handle.flush(); handle.seek(0)\n"
                "if os.name == 'nt':\n import msvcrt\n msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)\n"
                "else:\n import fcntl\n fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                "(root / 'ready').write_text(str(os.getpid()))\ntime.sleep(30)\n", encoding="utf-8")
            wrapper = root / "workflow.py"
            wrapper.write_text(
                "import subprocess, sys\n"
                "subprocess.run([sys.executable, sys.argv[1], sys.argv[2]], check=True)\n", encoding="utf-8")
            command = [sys.executable, str(wrapper), str(leaf), str(root)]
            real_wait = subprocess.Popen.wait
            interrupted = False

            def interrupt_after_lock(process, *args, **kwargs):
                nonlocal interrupted
                if process.args == command and not interrupted:
                    deadline = time.monotonic() + 5
                    while not (root / "ready").exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue((root / "ready").exists(), "descendant failed to acquire fixture cache lock")
                    interrupted = True
                    raise KeyboardInterrupt
                return real_wait(process, *args, **kwargs)

            released = False
            try:
                with patch.object(subprocess.Popen, "wait", interrupt_after_lock):
                    with self.assertRaises(KeyboardInterrupt):
                        build.run_process(command, root, dict(os.environ))
                with (root / "cache.lock").open("r+b") as handle:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(handle, fcntl.LOCK_UN)
                released = True
                self.assertEqual((root / "cache.lock").read_bytes(), b"x", "cache files must be retained")
            finally:
                if not released and (root / "ready").exists():
                    pid = int((root / "ready").read_text())
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
                    else:
                        try:
                            os.kill(pid, 9)
                        except ProcessLookupError:
                            pass

    def test_native_failure_is_not_reported_as_success(self):
        command = [sys.executable, "-c", "raise SystemExit(9)"]
        with self.assertRaises(subprocess.CalledProcessError) as failure:
            build.run_process(command, Path.cwd(), dict(os.environ))
        self.assertEqual(failure.exception.returncode, 9)

    def test_failed_cleanup_restores_interrupt_handler_and_reports_cache_owner(self):
        command = ["fixture setup"]
        previous = build.signal.getsignal(build.signal.SIGINT)
        calls = 0

        def waiting(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise KeyboardInterrupt
            self.assertEqual(build.signal.getsignal(build.signal.SIGINT), build.signal.SIG_IGN)
            raise subprocess.TimeoutExpired(command, 5)

        def stop_tree(*args, **kwargs):
            self.assertEqual(build.signal.getsignal(build.signal.SIGINT), build.signal.SIG_IGN)
            raise subprocess.TimeoutExpired(command, 15)

        with patch.object(build.subprocess, "Popen") as launch, patch.object(build.os, "killpg", create=True), patch.object(build.subprocess, "run", side_effect=stop_tree):
            launch.return_value.pid = 12345
            launch.return_value.wait.side_effect = waiting
            with self.assertRaisesRegex(build.BuildError, "process tree 12345; descendants may still be running"):
                build.run_process(command, Path.cwd(), dict(os.environ))
        self.assertEqual(build.signal.getsignal(build.signal.SIGINT), previous)


if __name__ == "__main__":
    unittest.main()

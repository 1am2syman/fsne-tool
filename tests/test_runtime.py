"""Integrity checks for the bundled Windows x64 Python runtime and start.py."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import start

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime" / "windows-x64"
MANIFEST = ROOT / "runtime" / "windows-x64.manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BundledRuntimeTests(unittest.TestCase):
    def test_all_runtime_files_match_checksums(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        files = manifest["files"]
        self.assertTrue(files)
        actual = {p.name for p in RUNTIME.iterdir() if p.is_file()}
        self.assertEqual(actual, set(files))
        for name, expected in files.items():
            with self.subTest(file=name):
                self.assertEqual(Path(name).name, name)
                self.assertRegex(expected, r"^[0-9a-f]{64}$")
                self.assertEqual(_sha256(RUNTIME / name), expected)

    def test_provenance_matches_upstream_sbom(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        sbom_path = ROOT / "runtime" / f"python-{manifest['version']}-embed-amd64.spdx.json"
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
        package = next(p for p in sbom["packages"] if p["name"] == "CPython")
        self.assertEqual(manifest["platform"], "windows-x64")
        self.assertEqual(manifest["version"], package["versionInfo"])
        self.assertEqual(manifest["source"], package["downloadLocation"])
        self.assertEqual(
            manifest["source"],
            f"https://www.python.org/ftp/python/{manifest['version']}/python-{manifest['version']}-embed-amd64.zip",
        )
        self.assertIn({"algorithm": "SHA256", "checksumValue": manifest["archive_sha256"]}, package["checksums"])
        self.assertIn("PYTHON SOFTWARE FOUNDATION LICENSE", (RUNTIME / "LICENSE.txt").read_text(encoding="utf-8"))

    def test_native_files_are_windows_x64(self):
        self.assertTrue((RUNTIME / "python.exe").is_file())
        self.assertTrue((RUNTIME / "_ctypes.pyd").is_file())
        self.assertTrue((RUNTIME / "_socket.pyd").is_file())
        for path in RUNTIME.iterdir():
            if path.suffix not in {".exe", ".dll", ".pyd"}:
                continue
            with self.subTest(file=path.name):
                raw = path.read_bytes()
                self.assertEqual(raw[:2], b"MZ")
                offset = struct.unpack_from("<I", raw, 0x3C)[0]
                self.assertEqual(raw[offset : offset + 4], b"PE\0\0")
                self.assertEqual(struct.unpack_from("<H", raw, offset + 4)[0], 0x8664)

    def test_isolated_pth_and_standard_library(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        major, minor, _ = manifest["version"].split(".")
        stem = f"python{major}{minor}"
        paths = (RUNTIME / f"{stem}._pth").read_text(encoding="utf-8").splitlines()
        active = [p for p in paths if p and not p.startswith("#")]
        self.assertEqual(active, [f"{stem}.zip", ".", r"..\..", r"..\..\.fsne-packages"])
        self.assertNotIn("import site", active)
        with zipfile.ZipFile(RUNTIME / f"{stem}.zip") as library:
            self.assertIsNone(library.testzip())
            for module in (
                "argparse.pyc",
                "ctypes/__init__.pyc",
                "encodings/__init__.pyc",
                "http/server.pyc",
                "pathlib/__init__.pyc",
                "webbrowser.pyc",
                "zipfile/__init__.pyc",
            ):
                self.assertIn(module, library.namelist())

    def test_ensure_packages_caches_resyncs_and_requires_uv(self):
        with tempfile.TemporaryDirectory() as temp_str:
            temp = Path(temp_str)
            lock_file = temp / "uv.lock"
            packages_dir = temp / ".fsne-packages"
            lock_file.write_text("version = 1\n", encoding="utf-8")

            calls: list[list[str]] = []

            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                self.assertEqual(kwargs["env"]["UV_PYTHON_DOWNLOADS"], "never")
                if cmd[1] == "export":
                    Path(cmd[cmd.index("--output-file") + 1]).write_text("demo==1.0\n", encoding="utf-8")
                elif cmd[1] == "pip":
                    # A bundled interpreter is not necessarily discoverable by uv.
                    self.assertIn("--python", cmd)
                    self.assertEqual(cmd[cmd.index("--python") + 1], sys.executable)
                    self.assertEqual(cmd[cmd.index("--python-version") + 1], "3.14")
                    self.assertEqual(cmd[cmd.index("--python-platform") + 1], "x86_64-pc-windows-msvc")
                    target = Path(cmd[cmd.index("--target") + 1])
                    (target / "demo").mkdir(parents=True, exist_ok=True)
                    (target / "demo" / "__init__.py").write_text(lock_file.read_text(encoding="utf-8"), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0)

            with patch("start.shutil.which", return_value="/usr/bin/uv"), patch("start.subprocess.run", side_effect=fake_run):
                start.ensure_packages(lock_file, packages_dir, python_version="3.14")
                self.assertEqual(len(calls), 2)
                self.assertEqual((packages_dir / "demo" / "__init__.py").read_text(encoding="utf-8"), "version = 1\n")

                # Second call with unchanged lock file skips uv completely.
                start.ensure_packages(lock_file, packages_dir, python_version="3.14")
                self.assertEqual(len(calls), 2)

                # Changing uv.lock triggers atomic re-install.
                lock_file.write_text("version = 2\n", encoding="utf-8")
                start.ensure_packages(lock_file, packages_dir, python_version="3.14")
                self.assertEqual(len(calls), 4)
                self.assertEqual((packages_dir / "demo" / "__init__.py").read_text(encoding="utf-8"), "version = 2\n")

            # Missing uv fails cleanly without clobbering existing packages.
            lock_file.write_text("version = 3\n", encoding="utf-8")
            with patch("start.shutil.which", return_value=None), self.assertRaisesRegex(RuntimeError, "uv is required"):
                start.ensure_packages(lock_file, packages_dir, python_version="3.14")
            self.assertEqual((packages_dir / "demo" / "__init__.py").read_text(encoding="utf-8"), "version = 2\n")

    @unittest.skipUnless(sys.platform == "win32", "Executing the Windows interpreter requires Windows")
    def test_bundled_interpreter_runs_start_py_and_imports_dependencies(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="FSNE runtime test ") as work:
            version = subprocess.run(
                [str(RUNTIME / "python.exe"), "--version"], check=False, cwd=work, capture_output=True, text=True, timeout=15
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertEqual(version.stdout.strip(), f"Python {manifest['version']}")
            help_res = subprocess.run(
                [str(RUNTIME / "python.exe"), "-B", str(ROOT / "start.py"), "--help"],
                check=False, cwd=work, capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(help_res.returncode, 0, help_res.stderr)
            self.assertIn("--port", help_res.stdout)
            self.assertIn("--open-browser", help_res.stdout)
            imports = subprocess.run(
                [
                    str(RUNTIME / "python.exe"), "-B", "-c",
                    "import polars, fastexcel, openpyxl, xlsxwriter, src.report, server.runs; print(polars.__version__)",
                ],
                check=False, cwd=work, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(imports.returncode, 0, imports.stderr)
            self.assertTrue(imports.stdout.strip())
            # Verify the exact child-subprocess shapes used by server/runs.py and server/action_plans.py.
            src_cli = subprocess.run(
                [str(RUNTIME / "python.exe"), "-B", "-m", "src", "--help"],
                check=False, cwd=ROOT, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(src_cli.returncode, 0, src_cli.stderr)
            self.assertIn("report", src_cli.stdout)
            ui_build = subprocess.run(
                [str(RUNTIME / "python.exe"), "-B", "ui/build.py", "--help"],
                check=False, cwd=ROOT, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(ui_build.returncode, 0, ui_build.stderr)
            self.assertIn("--history-dir", ui_build.stdout)


if __name__ == "__main__":
    unittest.main()

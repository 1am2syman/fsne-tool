"""Tests for Windows launcher/updater contracts and external runs/history defaults."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.action_plans import commit_history
from server.runs import Store, default_data_dir, default_history_dir, default_runs_dir

ROOT = Path(__file__).resolve().parent.parent


class ExternalStorageDefaultsTests(unittest.TestCase):
    def test_defaults_resolve_outside_repository_and_respect_env_overrides(self):
        with tempfile.TemporaryDirectory() as home_str:
            home = Path(home_str)
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("FSNE_RUNS_DIR", None)
                os.environ.pop("FSNE_HISTORY_DIR", None)
                with patch("server.runs.sys.platform", "linux"), patch("server.runs.Path.home", return_value=home):
                    self.assertEqual(default_data_dir(), home / "Documents" / "FSNE")
                    self.assertEqual(default_runs_dir(), home / "Documents" / "FSNE" / "runs")
                    self.assertEqual(default_history_dir(), home / "Documents" / "FSNE" / "history")
            custom_runs = home / "custom-runs"
            custom_history = home / "custom-history"
            with patch.dict(os.environ, {"FSNE_RUNS_DIR": str(custom_runs), "FSNE_HISTORY_DIR": str(custom_history)}):
                self.assertEqual(default_runs_dir(), custom_runs)
                self.assertEqual(default_history_dir(), custom_history)

    def test_default_store_seeds_external_history_once_from_bundled_snapshots(self):
        with tempfile.TemporaryDirectory() as temp_str:
            temp = Path(temp_str)
            data_dir = temp / "Documents" / "FSNE"
            bundled = temp / "bundled-history"
            bundled.mkdir()
            (bundled / "2026_02_03.xlsx").write_bytes(b"feb")
            (bundled / "2026_03_03.xlsx").write_bytes(b"mar")
            (bundled / "notes.txt").write_text("ignored")

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("FSNE_RUNS_DIR", None)
                os.environ.pop("FSNE_HISTORY_DIR", None)
                with patch("server.runs.default_data_dir", return_value=data_dir), \
                     patch("server.runs.BUNDLED_HISTORY", bundled):
                    store = Store(start_worker=False)
                    self.assertEqual(store.dir, (data_dir / "runs").resolve())
                    self.assertEqual(store.history_dir, (data_dir / "history").resolve())
                    self.assertEqual(
                        sorted(p.name for p in store.history_dir.iterdir()),
                        ["2026_02_03.xlsx", "2026_03_03.xlsx"],
                    )
                    # Removing a file after initial seeding must not be undone on restart.
                    (store.history_dir / "2026_02_03.xlsx").unlink()
                    restarted = Store(start_worker=False)
                    self.assertEqual(sorted(p.name for p in restarted.history_dir.iterdir()), ["2026_03_03.xlsx"])

    def test_explicit_or_env_history_dir_is_not_auto_seeded(self):
        with tempfile.TemporaryDirectory() as temp_str:
            temp = Path(temp_str)
            bundled = temp / "bundled-history"
            bundled.mkdir()
            (bundled / "2026_02_03.xlsx").write_bytes(b"feb")

            explicit_history = temp / "explicit-history"
            with patch("server.runs.BUNDLED_HISTORY", bundled):
                Store(temp / "runs", explicit_history, start_worker=False)
                self.assertFalse(explicit_history.exists())

            env_history = temp / "env-history"
            with patch.dict(os.environ, {"FSNE_RUNS_DIR": str(temp / "env-runs"), "FSNE_HISTORY_DIR": str(env_history)}), \
                 patch("server.runs.BUNDLED_HISTORY", bundled):
                Store(start_worker=False)
                self.assertFalse(env_history.exists())

    def test_commit_history_skips_cleanly_outside_git_repo_and_reports_failures_inside_git_repo(self):
        with tempfile.TemporaryDirectory() as temp_str:
            temp = Path(temp_str)
            outside = temp / "external-history"
            outside.mkdir()
            snapshot = outside / "2026_10_31.xlsx"
            snapshot.write_bytes(b"xlsx")
            result = commit_history(outside, [snapshot], "Settle 2026-10 (RUN-0001)")
            self.assertTrue(result["ok"])
            self.assertTrue(result.get("skipped"))

            repo = temp / "repo"
            (repo / ".git").mkdir(parents=True)
            inside = repo / "history"
            inside.mkdir()
            inside_file = inside / "2026_10_31.xlsx"
            inside_file.write_bytes(b"xlsx")
            with patch("server.action_plans.subprocess.run", side_effect=FileNotFoundError("git")):
                failed = commit_history(inside, [inside_file], "Settle 2026-10 (RUN-0001)")
            self.assertFalse(failed["ok"])
            self.assertIn("git", failed["error"])


class BatchScriptContractTests(unittest.TestCase):
    def test_start_fsne_bat_invokes_bundled_runtime_directly(self):
        text = (ROOT / "start-fsne.bat").read_text(encoding="utf-8")
        self.assertIn('"runtime\\windows-x64\\python.exe" -B "start.py" --port 8770 --open-browser %*', text)
        self.assertIn('if not exist "runtime\\windows-x64\\python.exe"', text)
        for forbidden in ("where uv", "Expand-Archive", "uv sync", "uv run"):
            self.assertNotIn(forbidden, text)

    def test_update_bat_follows_self_healing_pattern_and_restricts_clean(self):
        text = (ROOT / "update.bat").read_text(encoding="utf-8")
        for required in (
            r"'\runtime\'",
            r'if not exist ".git\index.lock" goto no_lock',
            r'del /f /q ".git\index.lock"',
            "git fetch --prune",
            'stash push --include-untracked --quiet -m "Set aside by update.bat"',
            'git reset --hard --quiet "@{u}"',
            "git clean -fdxq -- src server ui",
        ):
            self.assertIn(required, text)
        # Ensure the parenthesized update block contains no goto/labels or parse-time %VAR% expansion.
        match = re.search(r"^\(\r?\n(?P<block>.*?)^\)\r?$", text, flags=re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, "Expected parenthesized update execution block")
        assert match is not None
        block = match.group("block")
        self.assertNotRegex(block, r"(?im)^\s*(goto\b|:)")
        self.assertNotRegex(block, r"(?<!%)%[A-Za-z_][A-Za-z0-9_]*%")


@unittest.skipUnless(shutil.which("git"), "Updater regression tests require Git")
class UpdaterStashTests(unittest.TestCase):
    def test_stash_preserves_legacy_vendor_even_before_ignore_rules_are_updated(self):
        text = (ROOT / "update.bat").read_text(encoding="utf-8")
        stash_line = next(line.strip() for line in text.splitlines() if " stash push " in line)
        command = shlex.split(stash_line)
        with tempfile.TemporaryDirectory(prefix="FSNE updater test ") as temp_str:
            repo = Path(temp_str)

            def git(*args):
                return subprocess.run(
                    ["git", "-c", "user.name=Updater test", "-c", "user.email=test@localhost", *args],
                    cwd=repo, capture_output=True, text=True, check=True, timeout=15,
                )

            git("init", "-q")
            # Simulate the checkout's old rules: the replacement updater can run
            # before reset installs the new .gitignore.
            (repo / ".gitignore").write_text(".fsne-packages/\n", encoding="utf-8")
            (repo / "start.py").write_text("published\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-qm", "Published version")
            (repo / "start.py").write_text("local edit\n", encoding="utf-8")
            git("add", "start.py")
            (repo / "start.py").write_text("local unstaged edit\n", encoding="utf-8")
            vendor = repo / "vendor" / "leftover.txt"
            vendor.parent.mkdir()
            vendor.write_bytes(b"old runtime")
            packages = repo / ".fsne-packages" / "installed.txt"
            packages.parent.mkdir()
            packages.write_bytes(b"installed dependency")
            stray_name = "neProcessId = (Get-NetTCPConnection -LocalPort 8770 -State Listen"
            (repo / stray_name).write_text("stray command file\n", encoding="utf-8")

            result = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(vendor.is_file(), "The updater must leave the retired runtime folder in place")
            self.assertEqual(vendor.read_bytes(), b"old runtime")
            self.assertEqual(packages.read_bytes(), b"installed dependency")
            self.assertEqual((repo / "start.py").read_text(encoding="utf-8"), "published\n")
            self.assertFalse((repo / stray_name).exists())
            self.assertEqual(git("show", "stash@{0}:start.py").stdout, "local unstaged edit\n")
            self.assertEqual(git("show", "stash@{0}^2:start.py").stdout, "local edit\n")
            self.assertEqual(git("show", f"stash@{{0}}^3:{stray_name}").stdout, "stray command file\n")

    def test_current_ignore_rules_exclude_only_root_vendor(self):
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "vendor/leftover.txt", "src/vendor/module.py"],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["vendor/leftover.txt"])


if __name__ == "__main__":
    unittest.main()

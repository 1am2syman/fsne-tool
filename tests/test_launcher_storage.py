"""Tests for Windows launcher/updater contracts and external runs/history defaults."""

from __future__ import annotations

import os
import re
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


if __name__ == "__main__":
    unittest.main()

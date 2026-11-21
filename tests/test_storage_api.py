"""Tests for UI-configurable storage paths, folder browsing, and live switching."""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from server.app import make_server
from server.runs import RunError, Store, default_history_dir, default_runs_dir
from server.storage import (
    browse_directory,
    create_subfolder,
    inspect_path,
    load_settings,
    save_settings,
)


class StorageSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_settings_precedence_env_over_settings_over_default(self):
        settings_file = self.root / "settings.json"
        saved_runs = self.root / "saved-runs"
        saved_hist = self.root / "saved-hist"
        env_runs = self.root / "env-runs"
        fake_data = self.root / "default-fsne"

        with patch.dict(os.environ, {"FSNE_SETTINGS_PATH": str(settings_file)}, clear=False):
            os.environ.pop("FSNE_RUNS_DIR", None)
            os.environ.pop("FSNE_HISTORY_DIR", None)
            with patch("server.runs.default_data_dir", return_value=fake_data):
                self.assertEqual(default_runs_dir(), fake_data / "runs")
                self.assertEqual(default_history_dir(), fake_data / "history")

                save_settings(str(saved_runs), str(saved_hist))
                self.assertEqual(load_settings(), {"runs_dir": str(saved_runs), "history_dir": str(saved_hist)})
                self.assertEqual(default_runs_dir(), saved_runs)
                self.assertEqual(default_history_dir(), saved_hist)

                with patch.dict(os.environ, {"FSNE_RUNS_DIR": str(env_runs)}):
                    self.assertEqual(default_runs_dir(), env_runs)
                    self.assertEqual(default_history_dir(), saved_hist)

    def test_inspect_path_validates_and_summarizes_runs_and_history(self):
        self.assertFalse(inspect_path("runs", "relative/path")["valid"])
        a_file = self.root / "not-a-dir.txt"
        a_file.write_text("x")
        self.assertFalse(inspect_path("runs", str(a_file))["valid"])

        runs = self.root / "runs"
        (runs / "RUN-0001").mkdir(parents=True)
        (runs / "RUN-0001" / "meta.json").write_text("{}")
        r_info = inspect_path("runs", str(runs))
        self.assertTrue(r_info["valid"])
        self.assertEqual(r_info["count"], 1)
        self.assertEqual(r_info["count_label"], "1 run")

        hist = self.root / "hist"
        hist.mkdir()
        (hist / "2026_07_06.xlsx").write_bytes(b"a")
        (hist / "2026_08_24.xlsx").write_bytes(b"b")
        h_info = inspect_path("history", str(hist))
        self.assertTrue(h_info["valid"])
        self.assertEqual(h_info["count"], 2)
        self.assertEqual(h_info["months"], ["2026-07", "2026-08"])

        new_dir = self.root / "brand-new" / "history"
        n_info = inspect_path("history", str(new_dir))
        self.assertTrue(n_info["valid"])
        self.assertTrue(n_info["will_create"])
        self.assertFalse(new_dir.exists())

    def test_browse_and_create_subfolder_guard_traversal(self):
        (self.root / "alpha").mkdir()
        (self.root / "beta").mkdir()
        listing = browse_directory(str(self.root), "runs")
        self.assertEqual([f["name"] for f in listing["folders"]], ["alpha", "beta"])
        self.assertTrue(listing["writable"])

        created = create_subfolder(str(self.root), "gamma")
        self.assertEqual(Path(created["path"]), (self.root / "gamma").resolve())
        self.assertTrue((self.root / "gamma").is_dir())

        for bad in ("", "..", "a/b", "a\\b", "CON"):
            with self.assertRaises(RunError):
                create_subfolder(str(self.root), bad)

    def test_store_reconfigure_rejects_nested_and_busy_and_seeds_when_requested(self):
        store = Store(self.root / "r1", self.root / "h1", start_worker=False)
        with self.assertRaisesRegex(RunError, "separate"):
            store.reconfigure(self.root / "same", self.root / "same")
        with self.assertRaisesRegex(RunError, "separate"):
            store.reconfigure(self.root / "parent", self.root / "parent" / "child")

        run = store.create("Active")
        store.update(run["id"], status="running")
        with self.assertRaisesRegex(RunError, "active run"):
            store.reconfigure(self.root / "r2", self.root / "h2")
        store.update(run["id"], status="ok")

        bundled = self.root / "bundled"
        bundled.mkdir()
        (bundled / "2026_02_03.xlsx").write_bytes(b"baseline")
        with patch("server.runs.BUNDLED_HISTORY", bundled):
            store.reconfigure(self.root / "r2", self.root / "h2", seed_history=True)
        self.assertEqual(store.dir, (self.root / "r2").resolve())
        self.assertEqual(store.history_dir, (self.root / "h2").resolve())
        self.assertTrue((self.root / "h2" / "2026_02_03.xlsx").is_file())


class StorageHttpApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.settings_file = self.root / "settings.json"
        self.store = Store(self.root / "r1", self.root / "h1", start_worker=False)
        self.server = make_server(self.store, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _req(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            raw = json.dumps(body) if body is not None else None
            headers = {"Content-Type": "application/json"} if raw is not None else {}
            conn.request(method, path, raw, headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read())
        finally:
            conn.close()

    def test_http_storage_roundtrip_and_env_lock(self):
        with patch.dict(os.environ, {"FSNE_SETTINGS_PATH": str(self.settings_file)}, clear=False):
            os.environ.pop("FSNE_RUNS_DIR", None)
            os.environ.pop("FSNE_HISTORY_DIR", None)
            with patch("server.runs.default_data_dir", return_value=self.root / "default-fsne"):
                status, data = self._req("GET", "/api/storage")
                self.assertEqual(status, 200)
                self.assertEqual(Path(data["runs"]["path"]), (self.root / "r1").resolve())

                new_runs = self.root / "custom-runs"
                new_hist = self.root / "custom-hist"
                status, updated = self._req("POST", "/api/storage", {
                    "runs_dir": str(new_runs),
                    "history_dir": str(new_hist),
                    "seed_history": False,
                })
                self.assertEqual(status, 200)
                self.assertEqual(Path(updated["runs"]["path"]), new_runs.resolve())
                self.assertEqual(updated["runs"]["source"], "settings")
                self.assertEqual(Path(updated["history"]["path"]), new_hist.resolve())
                self.assertTrue(new_runs.is_dir())
                self.assertTrue(new_hist.is_dir())

                # Reset back to default via null
                status, reset = self._req("POST", "/api/storage", {"runs_dir": None, "history_dir": None})
                self.assertEqual(status, 200)
                self.assertEqual(reset["runs"]["source"], "default")
                self.assertEqual(reset["history"]["source"], "default")

                # Env lock refuses changing that slot
                with patch.dict(os.environ, {"FSNE_RUNS_DIR": str(self.root / "locked")}):
                    status, err = self._req("POST", "/api/storage", {"runs_dir": str(new_runs)})
                    self.assertEqual(status, 400)
                    self.assertIn("FSNE_RUNS_DIR", err["error"])


if __name__ == "__main__":
    unittest.main()

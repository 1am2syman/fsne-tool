"""Targeted HTTP/disk regressions for the reported export and delete failures.

Run explicit groups: uv run python tests/test_run_export_regression.py naming|export|deletion.
These checks reopen real downloaded workbooks and remove only temporary runs.
"""
from __future__ import annotations

import errno
import http.client
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch

import openpyxl
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.app import make_server
from server.runs import SLOTS, Store


@contextmanager
def app():
    with tempfile.TemporaryDirectory(prefix="fsne-export-regression-") as directory:
        store = Store(Path(directory) / "runs", Path(directory) / "shared", start_worker=False)
        server = make_server(store, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield store, server.server_port
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def request(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {"Content-Type": "application/json"})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def seed(store, report_date="2026-08-30", keys=("exact-1-2", "similar-3-4", "new")):
    run = store.create("Regression report")
    folder = store.path(run["id"])
    (folder / "output").mkdir()
    (folder / "history").mkdir()
    frame = pl.DataFrame({"batch_index": keys, "report_date": [45000] * len(keys),
                          "Expiry Date": [46000] * len(keys), "total_closing_stock_kl": [3.5] * len(keys)})
    frame.write_parquet(folder / "output" / "inferred_report.parquet")
    store.update(run["id"], status="ok", report_date=report_date, started="2026-11-10T12:00:00+00:00")
    return run["id"], folder


def history(folder, name, report_date, keys, plans=None, statuses=None, legacy=False):
    frame = pl.DataFrame({"report_date": [date.fromisoformat(report_date)] * len(keys), "batch_index": keys,
                          "action_plan_remarks" if legacy else "action_plan": plans or ["Sell"] * len(keys),
                          "mitigation_status_category" if legacy else "mitigation_status": statuses or ["Actionable"] * len(keys)})
    frame.write_excel(folder / "history" / name)


def download(port, run_id):
    code, headers, content = request(port, "GET", f"/api/runs/{run_id}/report.xlsx")
    assert code == 200, (code, content)
    book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        sheet = book.active
        assert sheet is not None
        rows = list(sheet.values)
    finally:
        book.close()
    return headers, rows[0], rows[1:]


def naming():
    with app() as (store, port):
        run_id, folder = seed(store)
        store.update(run_id, status="draft", files={k: {"name": "accepted"} for k in SLOTS})

        class RunDay(date):
            @classmethod
            def today(cls):
                return cls(2026, 11, 10)

        with patch("server.runs.date", RunDay):
            code, _, _ = request(port, "POST", f"/api/runs/{run_id}/start", {})
        assert code == 200
        store.update(run_id, status="ok")
        first, _, _ = download(port, run_id)
        assert first["Content-Disposition"] == 'attachment; filename="action_plan_to_tm_2026_11_10.xlsx"'
        store.update(run_id, created="2027-02-01T00:00:00+00:00", report_date="2026-08-30")
        second, _, _ = download(port, run_id)
        assert first == second
        restarted = Store(store.dir, store.history_dir, start_worker=False)
        assert restarted.get(run_id)["run_date"] == "2026-11-10"
        # Old metadata acquires a stable local run date from its saved start time.
        old_id, _ = seed(store)
        download(port, old_id)
        expected = date.fromisoformat(store.get(old_id)["run_date"]).strftime("%Y_%m_%d")
        assert f"action_plan_to_tm_{expected}.xlsx" in download(port, old_id)[0]["Content-Disposition"]
    print("NAMING_REGRESSION_PASSED")


def export():
    with app() as (store, port):
        run_id, folder = seed(store)
        history(folder, "saved-0000.xlsx", "2026-06-30", ["new"], ["Too old"])
        history(folder, "saved-0001.xlsx", "2026-07-01", ["exact-1-2"], ["Earlier July"])
        history(folder, "saved-0002.xlsx", "2026-07-31", ["exact-1-2", "similar-1-2", "history-only"],
                ["Last month plan", "Must not match shortened key", "Old batch"], ["Actionable", "Other", None])
        history(folder, "saved-0003.xlsx", "2026-08-30", ["exact-1-2"], ["Same month"])
        history(folder, "saved-0004.xlsx", "2026-09-30", ["exact-1-2"], ["Future"])
        _, headers, rows = download(port, run_id)
        planning = ("lm_action_plan", "lm_mitigation_status", "action_plan", "mitigation_status")
        assert headers[-4:] == planning and len(set(headers)) == len(headers)
        assert len(rows) == 3 and [row[0] for row in rows] == ["exact-1-2", "similar-3-4", "new"]
        assert rows[0][-4:] == ("Last month plan", "Actionable", None, None)
        assert rows[1][-4:] == rows[2][-4:] == (None, None, None, None)
        assert all(row[3] == 3.5 for row in rows)
        for period, previous in [("2027-01-15", "2026-12-31"), ("2026-03-01", "2026-02-28")]:
            ident, path = seed(store, period)
            history(path, "saved-0000.xlsx", previous, ["exact-1-2"], ["Previous year/month"], legacy=True)
            assert download(port, ident)[2][0][-4:] == ("Previous year/month", "Actionable", None, None)
        missing, path = seed(store)
        history(path, "saved-0000.xlsx", "2026-06-30", ["exact-1-2"], ["Never use June"])
        assert all(row[-4:] == (None,) * 4 for row in download(port, missing)[2])
        duplicate, path = seed(store)
        history(path, "saved-0000.xlsx", "2026-07-31", ["exact-1-2", "exact-1-2"])
        code, _, content = request(port, "GET", f"/api/runs/{duplicate}/report.xlsx")
        assert code == 400 and b"unique" in content, content
        assert not (path / "output" / "action_plan.xlsx").exists()
        # A rejected duplicate can be corrected and retried without a partial file.
        history(path, "saved-0000.xlsx", "2026-07-31", ["exact-1-2"])
        assert download(port, duplicate)[2][0][-4:] == ("Sell", "Actionable", None, None)
    print("EXPORT_REGRESSION_PASSED")


def deletion():
    with app() as (store, port):
        ident, folder = seed(store)
        shared = store.history_dir
        shared.mkdir()
        target = shared / "2026-07.xlsx"
        target.write_bytes(b"shared history")
        (folder / "history" / "shared.xlsx").symlink_to(target)
        hardlink = folder / "history" / "hardlink.xlsx"
        os.link(target, hardlink)
        readonly = folder / "readonly.xlsx"
        readonly.write_bytes(b"private")
        readonly.chmod(stat.S_IRUSR)
        native_unlink = os.unlink

        def windows_unlink(path, *, dir_fd=None):
            # Inject only the Windows read-only rule; rmtree + HTTP remain real.
            absolute = Path(path) if dir_fd is None else Path(os.readlink(f"/proc/self/fd/{dir_fd}")) / path
            if not absolute.is_symlink() and not absolute.stat().st_mode & stat.S_IWUSR:
                raise PermissionError(errno.EACCES, "Access is denied", str(absolute))
            return native_unlink(path, dir_fd=dir_fd)

        with patch("os.unlink", side_effect=windows_unlink):
            # Positive control: the original unhandled rmtree path reproduces it.
            with patch("server.runs.remove_run_tree", side_effect=shutil.rmtree):
                code, _, content = request(port, "POST", f"/api/runs/{ident}/delete", {})
                assert code == 400 and b"Access is denied" in content
            code, _, content = request(port, "POST", f"/api/runs/{ident}/delete", {})
            assert code == 200, content
        assert not folder.exists() and target.read_bytes() == b"shared history"
        assert stat.S_IMODE(target.stat().st_mode) == 0o644
        for status in ("ok", "bad", "cancelled"):
            ident, path = seed(store)
            store.update(ident, status=status, archived="2026-11-10")
            assert request(port, "POST", f"/api/runs/{ident}/delete", {})[0] == 200
            assert not path.exists()
        earlier, path = seed(store)
        own = path / "history" / "snapshot.xlsx"
        own.write_bytes(b"prior run history")
        later, other = seed(store)
        linked = other / "history" / "saved-0000.xlsx"
        linked.symlink_to(own)
        assert request(port, "POST", f"/api/runs/{earlier}/delete", {})[0] == 200
        assert not linked.is_symlink() and linked.read_bytes() == b"prior run history"
        # Simulate a lock after metadata was already removed; run stays retryable.
        ident, path = seed(store)
        def locked(folder):
            (folder / "meta.json").unlink()
            raise PermissionError(errno.EACCES, "Access is denied", str(folder / "open.xlsx"))
        with patch("server.runs.remove_run_tree", side_effect=locked):
            code, _, content = request(port, "POST", f"/api/runs/{ident}/delete", {})
            assert code == 400 and b"Close any files" in content
        assert request(port, "GET", f"/api/runs/{ident}")[0] == 200
        assert request(port, "POST", f"/api/runs/{ident}/delete", {})[0] == 200
        for state in ("queued", "running"):
            ident, _ = seed(store)
            store.update(ident, status=state)
            assert request(port, "POST", f"/api/runs/{ident}/delete", {})[0] == 400
            store.update(ident, status="cancelled")
        restarted = Store(store.dir, shared, start_worker=False)
        next_run = restarted.create("next")
        assert int(next_run["id"][4:]) > int(ident[4:])
        assert restarted.get(later)["status"] == "ok"
    print("DELETION_REGRESSION_PASSED (Windows read-only rule emulated; native Windows still requires verification)")


if __name__ == "__main__":
    {"naming": naming, "export": export, "deletion": deletion}[sys.argv[1]]()

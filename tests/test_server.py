"""Upload/run boundary tests; numerical business rules stay in the existing suites."""

import concurrent.futures
import http.client
import io
import json
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import openpyxl
import polars as pl
from openpyxl.worksheet.table import Table

from server.app import make_server
from server.runs import SLOTS, RunError, Store, snapshot


def config_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["variable", "value"])
    sheet.append(["starting_date", "2026-08-30"])
    sheet.add_table(Table(displayName="config_table", ref="A1:B2"))
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "runs", self.root / "history", start_worker=False)
        self.addCleanup(self.temp.cleanup)

    def uploaded(self):
        run = self.store.create("August close")
        for slot, (_, _, extensions) in SLOTS.items():
            data = config_bytes() if slot == "config" else b"test placeholder"
            # Lifecycle tests use placeholders; validation has its own focused tests.
            with patch("server.runs.validate_upload", return_value="2026-08-30" if slot == "config" else None), \
                 patch("server.runs.validate_sales_month"):
                self.store.save_file(run["id"], slot, "uploaded" + extensions[0], io.BytesIO(data), len(data))
        return self.store.get(run["id"])

    def test_start_requires_all_files_and_is_accepted_once(self):
        draft = self.store.create("Incomplete")
        with self.assertRaisesRegex(RunError, "Missing files"):
            self.store.start(draft["id"])
        run = self.uploaded()
        self.assertEqual(run["report_date"], "2026-08-30")
        def start():
            try:
                return self.store.start(run["id"], override_unsettled=True)["status"]
            except RunError:
                return "rejected"
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(lambda _: start(), range(2)))
        self.assertCountEqual(outcomes, ["queued", "rejected"])
        with self.assertRaisesRegex(RunError, "before the run starts"):
            self.store.save_file(run["id"], "hierarchy", "x.xlsx", io.BytesIO(b"x"), 1)
        with self.assertRaisesRegex(RunError, "cannot be archived"):
            self.store.archive(run["id"], True)

    def test_invalid_replacement_keeps_the_accepted_file_and_date(self):
        run = self.uploaded()
        path = self.store.path(run["id"]) / "input" / "config.xlsx"
        original = path.read_bytes()
        for data, length in [(b"bad xlsx", 8), (b"truncated", 20)]:
            with self.assertRaises(RunError):
                self.store.save_file(run["id"], "config", "wrong.xlsx", io.BytesIO(data), length)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(self.store.get(run["id"])["report_date"], "2026-08-30")
        for slot, filename in [("hierarchy", "bad.csv"), ("../../oops", "bad.xlsx")]:
            with self.assertRaises(RunError):
                self.store.save_file(run["id"], slot, filename, io.BytesIO(b"x"), 1)

    def test_restart_persists_archive_and_marks_interrupted_runs_failed(self):
        done = self.store.create("Completed")
        self.store.update(done["id"], status="ok", rows=123)
        self.store.archive(done["id"], True)
        pending = self.uploaded()
        self.store.start(pending["id"], override_unsettled=True)
        restored = Store(self.store.dir, self.store.history_dir, start_worker=False)
        self.assertIsNotNone(restored.get(done["id"])["archived"])
        self.assertEqual(restored.get(pending["id"])["status"], "bad")
        self.assertIn("Interrupted", restored.get(pending["id"])["error"])
        restored.archive(done["id"], False)
        self.assertEqual(restored.latest_ok(), done["id"])

    def test_worker_records_a_failure_and_log(self):
        run = self.uploaded()
        self.store.start(run["id"], override_unsettled=True)
        self.store.jobs.put(None)
        with patch.object(self.store, "_execute", side_effect=RunError("Missing Batch code column")):
            self.store._worker()
        failed = self.store.get(run["id"])
        self.assertEqual(failed["status"], "bad")
        self.assertIn("Missing Batch code", failed["error"])
        self.assertIn("Missing Batch code", self.store.log(run["id"]))

    def test_private_snapshot_dates_manual_labels_and_asof_history(self):
        source = self.root / "source"
        source.mkdir()
        stock = pl.DataFrame({"report_date": [date(2026, 8, 30)], "batch_index": ["b"],
                              "Expiry Date": [date(2027, 8, 30)], "total_closing_stock_kl": [1.0]})
        saved = stock.with_columns(pl.lit("Actionable").alias("mitigation_status"), pl.lit("Sell").alias("action_plan"))
        paths = []
        for name, dt in [("old", date(2026, 7, 30)), ("same", date(2026, 8, 30)), ("future", date(2026, 9, 30))]:
            path = source / f"{name}.xlsx"
            saved.with_columns(pl.lit(dt).alias("report_date")).write_excel(path)
            paths.append(path)
        serial = stock.with_columns((pl.col(c) - date(1899,12,30)).dt.total_days().alias(c) for c in ("report_date", "Expiry Date"))
        parquet = self.root / "report.parquet"
        serial.write_parquet(parquet)
        history = self.root / "private"
        self.assertEqual(snapshot(parquet, "2026-08-30", history, self.root / "report.xlsx", paths), 1)
        self.assertEqual(len(list(history.glob("*.xlsx"))), 2)
        current = pl.read_excel(history / "snapshot.xlsx")
        self.assertEqual(current["report_date"][0], date(2026,8,30))
        self.assertEqual(current["mitigation_status"][0], "Actionable")
        self.assertEqual(current["action_plan"][0], "Sell")
        self.assertEqual(len(list(source.glob("*.xlsx"))), 3)

    def test_post_actions_consume_the_body_on_a_persistent_connection(self):
        run = self.store.create("Keep-alive")
        server = make_server(self.store, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("POST", f'/api/runs/{run["id"]}/archive', "{}")
        first = conn.getresponse()
        self.assertEqual(first.status, 200)
        first.read()
        conn.request("GET", "/api/runs")
        second = conn.getresponse()
        self.assertEqual(second.status, 200)
        self.assertIsNotNone(json.loads(second.read())[0]["archived"])

    def test_fresh_install_opens_upload_without_building_sample_reports(self):
        server = make_server(self.store, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        self.addCleanup(conn.close)
        # Fresh checkouts have no generated dashboard; sample dates may differ
        # from saved history, so starting uploads must not calculate a sample.
        with patch("server.app.FALLBACK_HTML", self.root / "missing-index.html"):
            conn.request("GET", "/")
            response = conn.getresponse()
            page = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn("Monthly upload", page)
        self.assertIn("const HAS_REPORT = false;", page)
        self.assertNotIn("/*ONBOARDING*/", page)
        conn.request("POST", "/api/runs", '{"label":"First real month"}')
        response = conn.getresponse()
        self.assertEqual(response.status, 201)
        self.assertEqual(json.loads(response.read())["status"], "draft")

    def test_disconnect_during_response_does_not_send_a_second_response(self):
        server = make_server(self.store, port=0)
        self.addCleanup(server.server_close)

        def disconnecting_writer(error_type, fail_at):
            writes = []

            def write(data):
                writes.append(data)
                if len(writes) >= fail_at:
                    raise error_type('Simulated client disconnect')
                return len(data)

            return writes, Mock(write=write)

        handler_class = server.RequestHandlerClass
        assert isinstance(handler_class, type)
        for error_type in (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            for fail_at in (1, 2):  # header flush, then body write
                for route in ('page', 'not-found', 'rejected'):
                    with self.subTest(error=error_type.__name__, fail_at=fail_at, route=route):
                        handler = object.__new__(handler_class)
                        handler.command = 'GET'
                        handler.path = '/missing' if route == 'not-found' else '/'
                        handler.request_version = 'HTTP/1.1'
                        handler.requestline = 'GET ' + handler.path + ' HTTP/1.1'
                        handler.headers = {}
                        handler.close_connection = False
                        writes, handler.wfile = disconnecting_writer(error_type, fail_at)
                        page = Mock(side_effect=RunError('Rejected request')) if route == 'rejected' else \
                            Mock(side_effect=lambda _, handler=handler: handler._send(200, b'dashboard', 'text/html'))
                        with patch.object(handler, '_page', page), \
                                patch.object(handler, '_error', wraps=handler._error) as send_error:
                            handler.do_GET()
                        self.assertTrue(handler.close_connection)
                        self.assertEqual(len(writes), fail_at)
                        self.assertEqual(send_error.call_count, 0 if route == 'page' else 1)

    def test_permission_failure_before_graph_builder_is_logged(self):
        server = make_server(self.store, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        self.addCleanup(conn.close)
        error = PermissionError(13, 'Access is denied', 'private-run/meta.json')
        with patch.object(self.store, 'get', side_effect=error), self.assertLogs('server.app', level='ERROR') as logged:
            conn.request('GET', '/api/runs/RUN-0001/evolution/exposure')
            response = conn.getresponse()
            self.assertEqual(response.status, 500)
            message = json.loads(response.read())['error']
            self.assertIn('command window', message)
            self.assertNotIn('private-run', message)
            self.assertIn('private-run/meta.json', '\n'.join(logged.output))

    def test_http_roundtrip_error_controls_and_download_guard(self):
        server = make_server(self.store, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request(method, path, body, headers or {})
            response = conn.getresponse()
            data = json.loads(response.read())
            conn.close()
            return response.status, data
        status, run = request("POST", "/api/runs", '{"label":"HTTP run"}')
        self.assertEqual(status, 201)
        self.assertEqual(request("GET", "/api/runs")[1][0]["id"], run["id"])
        self.assertEqual(request("POST", "/api/runs", "[]")[0], 400)
        self.assertEqual(request("POST", "/api/runs", "{bad")[0], 400)
        self.assertEqual(request("POST", "/api/runs", "{}", {"Origin":"https://evil.invalid"})[0], 403)
        self.assertEqual(request("GET", "/api/runs/RUN-9999/log")[0], 404)
        self.assertEqual(request("GET", f'/api/runs/{run["id"]}/report.xlsx')[0], 400)
        data = config_bytes()
        status, uploaded = request("PUT", f'/api/runs/{run["id"]}/files/config', data, {"X-Filename":"my%20config.xlsx"})
        self.assertEqual(status, 200)
        self.assertEqual(uploaded["report_date"], "2026-08-30")
        self.assertEqual(uploaded["files"]["config"]["name"], "my config.xlsx")
        self.assertEqual(request("POST", f'/api/runs/{run["id"]}/start')[0], 400)


if __name__ == "__main__":
    unittest.main()

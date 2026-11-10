"""Form configuration uses the same validated date as the pipeline workbook."""

import http.client
import io
import json
import threading
from unittest import TestCase

from server.app import make_server
from server.runs import RunError, Store, read_report_date
from ui.samples import files


def populate(store, run_id):
    for slot, (name, contents, _) in files().items():
        if slot != "config":
            store.save_file(run_id, slot, name, io.BytesIO(contents), len(contents))


def test_four_files_and_form_date_can_start_without_config_upload(tmp_path):
    store = Store(tmp_path / "runs", start_worker=False)
    run = store.create("Form run")
    populate(store, run["id"])
    with TestCase().assertRaisesRegex(RunError, "Choose a report date"):
        store.start(run["id"])
    configured = store.configure(run["id"], "2026-08-30", "August close")
    assert configured["configuration_source"] == "form"
    assert configured["files"]["config"]["generated"] is True
    assert read_report_date(store.path(run["id"]) / "input") == "2026-08-30"
    restarted = Store(store.dir, start_worker=False)
    assert restarted.get(run["id"])["report_date"] == "2026-08-30"
    assert restarted.get(run["id"])["label"] == "August close"
    assert restarted.start(run["id"])["status"] == "queued"
    with TestCase().assertRaisesRegex(RunError, "before the run starts"):
        restarted.configure(run["id"], "2026-08-29")


def test_rejected_date_does_not_replace_accepted_settings(tmp_path):
    store = Store(tmp_path / "runs", start_worker=False)
    run = store.create("Original")
    populate(store, run["id"])
    original = store.configure(run["id"], "2026-08-30")
    config = store.path(run["id"]) / "input" / "config.xlsx"
    contents = config.read_bytes()
    for value, message in [("", "valid calendar"), ("2026-02-30", "valid calendar"),
                           ("20260830", "valid calendar"), ("2026-09-30", "configuration report month")]:
        with TestCase().assertRaisesRegex(RunError, message):
            store.configure(run["id"], value, "Rejected label")
        assert store.get(run["id"]) == original
        assert config.read_bytes() == contents


def test_optional_workbook_import_populates_date_and_form_can_override_it(tmp_path):
    store = Store(tmp_path / "runs", start_worker=False)
    run = store.create("Import")
    store.configure(run["id"], "2026-08-29")
    name, contents, _ = files()["config"]
    imported = store.save_file(run["id"], "config", name, io.BytesIO(contents), len(contents))
    assert imported["report_date"] == "2026-08-30"
    assert imported["configuration_source"] == "workbook"
    assert not imported["files"]["config"].get("generated")
    edited = store.configure(run["id"], "2026-08-28")
    assert edited["report_date"] == "2026-08-28"
    assert read_report_date(store.path(run["id"]) / "input") == "2026-08-28"


def test_http_form_configuration_saves_and_rejects_bad_dates(tmp_path):
    store = Store(tmp_path / "runs", start_worker=False)
    run = store.create("HTTP form")
    server = make_server(store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        path = f'/api/runs/{run["id"]}/config'
        connection.request("POST", path, json.dumps({"report_date": "2026-08-30", "label": "August"}))
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["label"] == "August"
        connection.request("POST", path, '{"report_date":"not a date"}')
        response = connection.getresponse()
        assert response.status == 400
        assert "Report date" in json.loads(response.read())["error"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()

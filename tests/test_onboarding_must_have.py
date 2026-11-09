from datetime import date
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

import polars as pl

from server.runs import (
    RunError,
    Store,
    pipeline_message,
    snapshot,
    validate_sales_month,
)


def test_snapshot_inherits_first_non_null_labels_by_batch_key(tmp_path):
    stock = pl.DataFrame({
        "report_date": [date(2026, 8, 30)] * 3,
        "Expiry Date": [date(2027, 1, 1)] * 3,
        "batch_index": ["old-3-4", "old-5-6", "new-1-2"],
    })
    previous = pl.DataFrame({
        "report_date": [date(2026, 7, 30)] * 2,
        "batch_index": ["old-1-2", "old-7-8"],
        "mitigation_status_category": [None, "Actionable"],
        "action_plan_remarks": ["Sell", None],
    })
    old = tmp_path / "old.xlsx"
    previous.write_excel(old)
    source = tmp_path / "report.parquet"
    stock.with_columns(
        (pl.col(c) - date(1899, 12, 30)).dt.total_days().alias(c)
        for c in ("report_date", "Expiry Date")
    ).write_parquet(source)
    counts = {}
    snapshot(source, "2026-08-30", tmp_path / "history", tmp_path / "report.xlsx", [old], label_counts=counts)
    result = pl.read_excel(tmp_path / "history" / "snapshot.xlsx")
    assert result["mitigation_status"].to_list() == ["Actionable", "Actionable", None]
    assert result["action_plan"].to_list() == ["Sell", "Sell", None]
    assert counts == {"mitigation_carried_batches": 2, "mitigation_unlabelled_batches": 1}


def test_invalid_uploads_are_rejected_without_replacing_previous_files(tmp_path):
    store = Store(tmp_path / "runs", tmp_path / "history", start_worker=False)
    run = store.create("test")
    for slot, name in [("landing", "bad.xlsx"), ("hierarchy", "bad.xlsx"),
                       ("secondary", "bad.xlsx"), ("shelflife", "bad.csv")]:
        with TestCase().assertRaisesRegex(RunError, {"landing": "Stock on hand", "hierarchy": "Product hierarchy",
                                         "secondary": "Secondary sales", "shelflife": "Shelf-life"}[slot]):
            store.save_file(run["id"], slot, name, BytesIO(b"wrong data"), 10)
    assert store.get(run["id"])["files"] == {}


def test_sales_month_mismatch_rejects_either_upload_order(tmp_path):
    store = Store(tmp_path / "runs", tmp_path / "history", start_worker=False)
    def validate(slot, _):
        return "2026-08-30" if slot == "config" else None
    with patch("server.runs.validate_upload", side_effect=validate), \
         patch("server.runs.validate_sales_month", side_effect=RunError("month mismatch")):
        for first, second in [("config", "secondary"), ("secondary", "config")]:
            run = store.create("test")
            store.save_file(run["id"], first, "file.xlsx", BytesIO(b"x"), 1)
            with TestCase().assertRaisesRegex(RunError, "month mismatch"):
                store.save_file(run["id"], second, "file.xlsx", BytesIO(b"x"), 1)
            assert second not in store.get(run["id"])["files"]


def test_queued_cancel_and_delete_preserve_run_ids(tmp_path):
    store = Store(tmp_path / "runs", tmp_path / "history", start_worker=False)
    run = store.create("cancel")
    store.update(run["id"], status="queued")
    store.jobs.put(run["id"])
    assert store.cancel(run["id"])["status"] == "cancelled"
    store.delete(run["id"])
    next_run = store.create("next")
    assert next_run["id"] != run["id"]
    store.jobs.put(None)
    store._worker()
    assert store.get(next_run["id"])["status"] == "draft"


def test_sales_month_and_pipeline_error_messages(tmp_path):
    with patch("src.secondary.load", return_value=pl.DataFrame({"Month-Year": ["2026-07-01", "2026-08-01"]})):
        validate_sales_month(tmp_path, "2026-08-30")
        with TestCase().assertRaisesRegex(RunError, "2026-08.*2026-09"):
            validate_sales_month(tmp_path, "2026-09-30")
    assert "Secondary sales" in pipeline_message("ValueError: Latest run-rate source month does not match stock snapshot")
    assert "technical details" in pipeline_message("RuntimeError: unexpected")

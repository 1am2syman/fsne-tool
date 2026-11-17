"""FSNE uses the month after the latest secondary sales, with one month selection."""

import io
from datetime import date
from unittest import TestCase

import polars as pl

from server.runs import RunError, Store, read_report_date
from src import report, starting_date
from ui.run_rate_data import build_current
from ui.samples import _workbook, files


def upload(store, run_id, slot, content=None):
    name, sample, _ = files()[slot]
    payload = sample if content is None else content
    return store.save_file(run_id, slot, name, io.BytesIO(payload), len(payload))


def sales_for(month):
    return _workbook(
        ["Month-Year", "area_name (city)", "material_code", "parent_material_code", "BATCH_NBR", "sec_vol_kl_mth (billwise)"],
        [[month + "-01", "Example Town", 1001, 1000, "EXAMPLE-A", 0.2]],
    )


def test_secondary_upload_detects_next_fsne_month_and_persists_config(tmp_path):
    cases = [("2026-08", "2026-09-30"), ("2026-12", "2027-01-31"),
             ("2028-01", "2028-02-29"), ("2027-01", "2027-02-28")]
    for sales_month, expected in cases:
        with TestCase().subTest(sales_month=sales_month):
            root = tmp_path / sales_month
            store = Store(root / "runs", root / "history", start_worker=False)
            run = store.create("Auto month")
            meta = upload(store, run["id"], "secondary", sales_for(sales_month))
            assert meta["report_date"] == expected
            assert meta["configuration_source"] == "secondary"
            assert meta["fsne_month"] == expected[:7]
            assert read_report_date(store.path(run["id"]) / "input") == expected
            restarted = Store(store.dir, store.history_dir, start_worker=False)
            assert restarted.get(run["id"])["report_date"] == expected


def test_replacing_sales_updates_automatic_month_but_not_explicit_choice(tmp_path):
    store = Store(tmp_path / "runs", tmp_path / "history", start_worker=False)
    run = store.create("Replacement")
    upload(store, run["id"], "secondary", sales_for("2026-08"))
    meta = upload(store, run["id"], "secondary", sales_for("2026-09"))
    assert meta["report_date"] == "2026-10-31"
    store.configure(run["id"], fsne_month="2026-10")
    original = store.get(run["id"])
    source = store.path(run["id"]) / "input/Mode_Sec_Month on Month.xlsx"
    original_bytes = source.read_bytes()
    with TestCase().assertRaisesRegex(RunError, "FSNE month"):
        upload(store, run["id"], "secondary", sales_for("2026-10"))
    assert store.get(run["id"]) == original
    assert source.read_bytes() == original_bytes


def test_explicit_month_requires_previous_month_sales_in_either_upload_order(tmp_path):
    store = Store(tmp_path / "runs", tmp_path / "history", start_worker=False)
    run = store.create("Manual month first")
    meta = store.configure(run["id"], fsne_month="2026-09")
    assert meta["report_date"] == "2026-09-30"
    upload(store, run["id"], "secondary", sales_for("2026-08"))
    for invalid in ("2026-08", "2026-10", "2026-13", "2026-9", "not-a-month"):
        with TestCase().assertRaises(RunError):
            store.configure(run["id"], fsne_month=invalid)
    assert store.get(run["id"])["report_date"] == "2026-09-30"


def test_stock_and_hierarchy_never_set_or_replace_fsne_month(tmp_path):
    store = Store(tmp_path / "runs", tmp_path / "history", start_worker=False)
    run = store.create("Source dates")
    assert upload(store, run["id"], "shelflife")["report_date"] is None
    assert upload(store, run["id"], "hierarchy")["report_date"] is None
    upload(store, run["id"], "secondary", sales_for("2026-08"))
    assert upload(store, run["id"], "shelflife")["report_date"] == "2026-09-30"
    assert upload(store, run["id"], "hierarchy")["report_date"] == "2026-09-30"


def test_previous_month_sales_reach_dashboard_calculation(tmp_path):
    store = Store(tmp_path / "runs", tmp_path / "history", start_worker=False)
    run = store.create("Calculation")
    for slot in ("secondary", "hierarchy", "shelflife"):
        upload(store, run["id"], slot)
    data = store.path(run["id"]) / "input"
    assert read_report_date(data) == "2026-09-30"
    config = starting_date.build(data)
    assert config.item(0, "fsne_month") == "2026-09"
    frozen = report.build(data)
    epoch = pl.lit(date(1899, 12, 30))
    saved = frozen.with_columns(
        (epoch + pl.duration(days=pl.col(c))).cast(pl.Date).alias(c)
        for c in ("report_date", "Expiry Date")
    ).with_columns(pl.lit("Actionable").alias("mitigation_status"))
    current, _, _, impact = build_current(data, saved, date(2026, 9, 30), tmp_path / "output")
    assert current.height == 2
    assert impact["source_months"] == 3
    assert current["avg_sec_m"].null_count() == 0

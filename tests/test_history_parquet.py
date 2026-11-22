"""Settlement records retain frozen values and travel with dependent runs."""
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import polars as pl
from polars.testing import assert_frame_equal

from server.runs import Store, history_by_date
from src.saved_reports import copy_saved_report, read_saved_report


class HistoryParquetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = Store(root / "runs", root / "history", start_worker=False)
        self.frame = pl.DataFrame({
            "report_date": [date(2026, 9, 30)], "batch_index": ["B-1"],
            "total_closing_stock_bdt": [123.456789012345],
            "action_plan": ["Sell"], "mitigation_status": ["Actionable"],
        })

    def make_run(self, day="2026-09-30", *, parquet=True):
        run = self.store.create("Frozen run")
        self.store.update(run["id"], status="ok", report_date=day, plan_state="provisional")
        hist = self.store.path(run["id"]) / "history"
        hist.mkdir(exist_ok=True)
        frame = self.frame.with_columns(pl.lit(date.fromisoformat(day)).alias("report_date"))
        frame.write_excel(hist / "snapshot.xlsx")
        if parquet:
            frame.write_parquet(hist / "snapshot.parquet")
        return run["id"]

    def settle(self, run_id):
        check = self.store.cycle.settlement_check(run_id)
        return self.store.cycle.settle(run_id, {"confirm": True, "snapshot": check["snapshot"], "confirm_without_plan": True}, "test")

    def test_settle_freeze_read_and_reopen(self):
        run_id = self.make_run()
        result = self.settle(run_id)
        history = self.store.history_dir
        parquet = history / result["settlement"]["history_parquet"]
        assert_frame_equal(pl.read_parquet(parquet), self.frame)
        source = parquet.with_suffix(".xlsx")
        frozen = Path(self.temp.name) / "frozen.xlsx"
        copy_saved_report(source, frozen)
        # All history lookup/read paths can use the stored record without Excel parsing.
        with patch("polars.read_excel", side_effect=AssertionError("must use Parquet")):
            assert_frame_equal(read_saved_report(frozen), self.frame)
            self.assertEqual(list(history_by_date([frozen])), ["2026-09-30"])
        self.store.cycle.reopen(run_id, True, "test")
        self.assertFalse(source.exists())
        self.assertFalse(parquet.exists())
        assert_frame_equal(read_saved_report(frozen), self.frame)

    def test_movement_and_evolution_read_parquet_without_excel(self):
        from src import evolution, movement

        run_id = self.make_run()
        hist = self.store.path(run_id) / "history"
        frame = self.frame.with_columns(
            pl.lit("Brand").alias("Brand Name"),
            pl.lit("Town").alias("Town"),
            pl.lit("1").alias("Material Code"),
            pl.lit("1").alias("parent_material_code"),
            pl.lit("Product").alias("material_desc"),
            pl.lit("3M - 6M").alias("rsl_group"),
            pl.lit(1.0).alias("total_closing_stock_kl"),
        )
        frame.write_parquet(hist / "snapshot.parquet")
        with patch("polars.read_excel", side_effect=AssertionError("must use Parquet")):
            self.assertEqual(movement.read_reports(hist).height, 1)
            self.assertEqual(evolution.load_history(hist, Path(self.temp.name) / "cache").height, 1)

    def test_replacement_removes_both_old_formats(self):
        first = self.make_run("2026-09-29")
        self.settle(first)
        second = self.make_run()
        self.settle(second)
        self.assertEqual(sorted(p.name for p in self.store.history_dir.iterdir()), ["2026_09_30.parquet", "2026_09_30.xlsx"])

    def test_git_settlement_and_reopen_include_both_formats(self):
        history = self.store.history_dir
        history.mkdir()
        subprocess.run(["git", "init", "-q", str(history)], check=True)
        for key, value in (("user.name", "Test"), ("user.email", "test@example.com")):
            subprocess.run(["git", "-C", str(history), "config", key, value], check=True)
        run_id = self.make_run()
        result = self.settle(run_id)
        self.assertTrue(result["settlement"]["commit"]["ok"], result)
        tracked = subprocess.check_output(["git", "-C", str(history), "ls-files"], text=True)
        self.assertIn("2026_09_30.parquet", tracked)
        reopened = self.store.cycle.reopen(run_id, True, "test")
        self.assertTrue(reopened["reopened"]["commit"]["ok"], reopened)
        self.assertEqual(subprocess.check_output(["git", "-C", str(history), "ls-files"], text=True), "")

    def test_failed_parquet_write_does_not_publish_excel(self):
        run_id = self.make_run()
        with (
            patch.object(pl.DataFrame, "write_parquet", side_effect=OSError("disk full")),
            self.assertRaisesRegex(OSError, "disk full"),
        ):
            self.settle(run_id)
        self.assertEqual(list(self.store.history_dir.iterdir()), [])
        self.assertNotEqual(self.store.get(run_id)["plan_state"], "settled")

    def test_existing_excel_only_run_gets_parquet_on_settlement(self):
        run_id = self.make_run(parquet=False)
        result = self.settle(run_id)
        record = pl.read_parquet(self.store.history_dir / result["settlement"]["history_parquet"])
        self.assertEqual(record["mitigation_status"].to_list(), ["Actionable"])
        self.assertEqual(record.height, 1)

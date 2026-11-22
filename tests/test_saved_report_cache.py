"""Excel-only history is repaired once and reused across run boundaries."""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from unittest.mock import patch

import polars as pl
from polars.testing import assert_frame_equal

from server.runs import Store, history_by_date
from src.saved_reports import copy_saved_report, read_saved_report


class SavedReportCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = self.root / "history"
        self.history.mkdir()
        self.source = self.history / "2026_09_30.xlsx"
        self.frame = pl.DataFrame({
            "report_date": [date(2026, 9, 30)] * 2,
            "batch_index": ["B-1", "B-2"],
            "total_closing_stock_bdt": [123.456789012345, None],
            "action_plan": ["Keep original figures", None],
            "mitigation_status": ["Actionable", "Not actionable"],
        })
        self.frame.write_excel(self.source)
        self.parquet = self.source.with_suffix(".parquet")

    def test_first_projected_read_persists_complete_record_and_reuses_it(self):
        excel_bytes = self.source.read_bytes()
        expected = pl.read_excel(excel_bytes, sheet_id=1, infer_schema_length=0)
        projected = read_saved_report(self.source, columns=["report_date"])
        self.assertTrue(self.parquet.is_file())
        assert_frame_equal(projected, expected.select("report_date"))
        assert_frame_equal(pl.read_parquet(self.parquet), expected)
        cached_bytes = self.parquet.read_bytes()
        with patch("polars.read_excel", side_effect=AssertionError("Excel must be parsed only once")):
            assert_frame_equal(read_saved_report(self.source), expected)
            self.assertEqual(history_by_date([self.source]), {"2026-09-30": self.source})
        self.assertEqual(self.source.read_bytes(), excel_bytes)
        self.assertEqual(self.parquet.read_bytes(), cached_bytes)

    def test_supplied_parquet_remains_authoritative(self):
        self.frame.write_parquet(self.parquet)
        original = self.parquet.read_bytes()
        with patch("polars.read_excel", side_effect=AssertionError("supplied Parquet must be used")):
            assert_frame_equal(read_saved_report(self.source), self.frame)
        self.assertEqual(self.parquet.read_bytes(), original)

    def test_copy_repairs_shared_source_before_freezing_both_formats(self):
        target = self.root / "private" / "saved-0000.xlsx"
        target.parent.mkdir()
        copy_saved_report(self.source, target)
        self.assertTrue(self.parquet.is_file())
        self.assertEqual(target.read_bytes(), self.source.read_bytes())
        self.assertEqual(target.with_suffix(".parquet").read_bytes(), self.parquet.read_bytes())
        with patch("polars.read_excel", side_effect=AssertionError("frozen copy must use Parquet")):
            assert_frame_equal(read_saved_report(target), pl.read_parquet(self.parquet))

    def test_later_run_stores_reuse_shared_record_without_excel(self):
        first = Store(self.root / "runs-first", self.history, start_worker=False)
        history_by_date(first.history_paths())
        self.assertTrue(self.parquet.is_file())
        with patch("polars.read_excel", side_effect=AssertionError("next run must reuse shared Parquet")):
            later = Store(self.root / "runs-later", self.history, start_worker=False)
            self.assertEqual(history_by_date(later.history_paths()), {"2026-09-30": self.source})
            target = self.root / "next-run.xlsx"
            copy_saved_report(self.source, target)
            assert_frame_equal(read_saved_report(target), pl.read_parquet(self.parquet))

    def test_failed_cache_write_leaves_no_partial_record_and_can_retry(self):
        def fail_after_partial_write(frame, target, *args, **kwargs):
            Path(target).write_bytes(b"incomplete")
            raise OSError("disk full")

        original = self.source.read_bytes()
        with (
            patch.object(pl.DataFrame, "write_parquet", fail_after_partial_write),
            self.assertRaisesRegex(OSError, "disk full"),
        ):
            read_saved_report(self.source)
        self.assertFalse(self.parquet.exists())
        self.assertEqual(sorted(p.name for p in self.history.iterdir()), [self.source.name])
        self.assertEqual(self.source.read_bytes(), original)
        read_saved_report(self.source)
        self.assertTrue(self.parquet.is_file())

    def test_concurrent_readers_parse_excel_once(self):
        original_reader = pl.read_excel
        with (
            patch("polars.read_excel", wraps=original_reader) as reader,
            ThreadPoolExecutor(max_workers=4) as pool,
        ):
            results = list(pool.map(read_saved_report, [self.source] * 4))
        self.assertEqual(reader.call_count, 1)
        for result in results:
            assert_frame_equal(result, pl.read_parquet(self.parquet))

    def test_parquet_supplied_during_excel_parse_is_not_overwritten(self):
        def supplied_during_read(*args, **kwargs):
            self.frame.write_parquet(self.parquet)
            return self.frame.with_columns(pl.lit("do not publish").alias("action_plan"))

        with patch("polars.read_excel", side_effect=supplied_during_read):
            assert_frame_equal(read_saved_report(self.source), self.frame)
        assert_frame_equal(pl.read_parquet(self.parquet), self.frame)


if __name__ == "__main__":
    unittest.main()

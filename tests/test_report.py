"""Independent tiny cases for the inferred formulas, not copied golden rows."""

import argparse
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from src.cli import execute
from src.compare_report import compare
from src.db_landing import build as landing_build
from src.report import COLUMNS, transform
from tests.test_queries import workbook

DAY = date(2026, 1, 1)


def stock_frame(days, quantities, rates=None, towns=None, materials=None):
    size = len(days)
    return pl.DataFrame(
        {
            "batch_index": [f"key{i}" for i in range(size)],
            "Town": towns or ["Town"] * size,
            "Brand Name": ["Brand"] * size,
            "parent_material_code": [10] * size,
            "Material Code": materials or [1] * size,
            "material_desc": ["Product"] * size,
            "Batch code": [f"B{i}" for i in range(size)],
            "Expiry Date": [DAY + timedelta(days=days_left) for days_left in days],
            "Closing Stock": quantities,
            "NOS": [round(value * 1000) for value in quantities],
            "avg_sec": pl.Series(
                rates if rates is not None else [0.3] * size, dtype=pl.Float64
            ),
            "Primary Sales": [0.1] * size,
            "Secondary Sales": [0.2] * size,
        }
    )


def landing():
    return pl.DataFrame({"material_code": [1, 2], "db_landing_rate": [2.0, 4.0]})


class InferredReportTests(unittest.TestCase):
    def test_fractional_durations_accumulate_before_truncation(self):
        # At .01/day, two .015 batches each take 1.5 days. Third starts on
        # day floor(1.5 + 1.5)=3, NOT floor(1.5)+floor(1.5)=2.
        result = transform(
            stock_frame([310, 310, 310], [0.015, 0.015, 0.1]), landing(), DAY
        )
        self.assertEqual(result.columns, COLUMNS)
        self.assertAlmostEqual(result["consumable_kl"][0], 0.015)
        self.assertAlmostEqual(result["consumable_kl"][1], 0.015)
        self.assertAlmostEqual(result["consumable_kl"][2], 0.07)
        self.assertAlmostEqual(result["total_closing_stock_bdt"][2], 200.0)
        self.assertAlmostEqual(result["exposure_bdt"][2], 60.0)

    def test_duration_caps_are_independent_not_ideal_fifo(self):
        # Each of the first two rows adds its OWN capped deadline of 5 days,
        # even though the second can't be consumed. Third begins after 10 days.
        result = transform(
            stock_frame([305, 305, 315], [0.2, 0.2, 0.2]), landing(), DAY
        )
        for actual, expected in zip(
            result["consumable_kl"], [0.05, 0.0, 0.05], strict=True
        ):
            self.assertAlmostEqual(actual, expected)

    def test_town_child_queues_are_independent_despite_same_parent(self):
        source = stock_frame(
            [305, 305, 305], [0.2, 0.2, 0.2], materials=[1, 2, 1], towns=["A", "A", "B"]
        )
        result = transform(source, landing(), DAY)
        for quantity in result["consumable_kl"]:
            self.assertAlmostEqual(quantity, 0.05)

    def test_reserve_boundaries_null_rates_and_missing_landing(self):
        source = stock_frame([90, 91, 180, 181, 300, 301], [0.1] * 6, rates=[None] * 6)
        result = transform(source, landing().head(0), DAY)
        self.assertEqual(
            result["rsl_group"].to_list(),
            ["< 3M", "3M - 6M", "3M - 6M", "6M - 10M", "6M - 10M", "> 10M"],
        )
        self.assertEqual(result["consumable_kl"].to_list(), [0.0] * 5 + [0.1])
        self.assertEqual(result["lnd_fc_kl"].to_list(), [0.1] + [0.0] * 5)
        self.assertEqual(result["total_closing_stock_bdt"].null_count(), 6)
        self.assertEqual(result["consumable_bdt"].null_count(), 6)
        self.assertEqual(result["doh"].null_count(), 6)

    def test_invariance_under_source_reordering_and_reference_free_dates(self):
        source = stock_frame([301, 310, 320], [0.2, 0.3, 0.4])
        result = transform(source, landing(), DAY)
        self.assertTrue(result.equals(transform(source.reverse(), landing(), DAY)))
        self.assertEqual(result["report_date"][0], (DAY - date(1899, 12, 30)).days)
        later = transform(source, landing(), DAY + timedelta(days=1))
        self.assertEqual(later["rsl_days"][0], result["rsl_days"][0] - 1)

    def test_duplicate_distributor_aggregation_and_validation(self):
        source = stock_frame([310], [0.1])
        result = transform(pl.concat([source, source]), landing(), DAY)
        self.assertEqual(result.height, 1)
        self.assertAlmostEqual(result["total_closing_stock_kl"][0], 0.2)
        self.assertAlmostEqual(result["total_closing_stock_bdt"][0], 400.0)
        with self.assertRaises(ValueError):
            transform(
                source.with_columns(pl.lit(-1.0).alias("Closing Stock")), landing(), DAY
            )
        for invalid in [0.0, -1.0, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                transform(
                    source.with_columns(pl.lit(invalid).alias("avg_sec")),
                    landing(),
                    DAY,
                )
            with self.assertRaises(ValueError):
                transform(
                    source.with_columns(pl.lit(invalid).alias("Closing Stock")),
                    landing(),
                    DAY,
                )
        with self.assertRaises(ValueError):
            transform(source, pl.concat([landing(), landing()]), DAY)

    def test_excel_numeric_precision_is_not_display_string_precision(self):
        with tempfile.TemporaryDirectory() as folder:
            value = 1.23456789012345
            workbook(
                Path(folder) / "landing.xlsx",
                ["Row Labels", "unused", "unused2", "DB Landing rate"],
                [[1, None, None, value]],
            )
            self.assertAlmostEqual(
                landing_build(folder)["db_landing_rate"][0], value, places=13
            )

    def test_comparator_detects_wrong_values_nulls_keys_and_nonfinite_values(self):
        frame = transform(stock_frame([310], [0.1]), landing(), DAY)
        self.assertEqual(compare(frame, frame)["value_mismatches"].sum(), 0)
        altered = frame.with_columns(
            (pl.col("consumable_kl") + 0.01).alias("consumable_kl")
        )
        self.assertEqual(compare(altered, frame)["value_mismatches"].sum(), 1)
        altered = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("doh"))
        self.assertEqual(compare(altered, frame)["null_mismatches"].sum(), 1)
        for bad in [float("nan"), float("inf")]:
            altered = frame.with_columns(pl.lit(bad).alias("doh"))
            self.assertEqual(compare(altered, frame)["value_mismatches"].sum(), 1)
        with self.assertRaises(ValueError):
            compare(pl.concat([frame, frame]), frame)
        with self.assertRaises(ValueError):
            compare(frame.with_columns(pl.lit("wrong").alias("batch_index")), frame)
        with self.assertRaises(ValueError):
            compare(frame.drop("rsl_days"), frame)

    def test_cli_cannot_overwrite_samples(self):
        def never_build(**kwargs):
            self.fail("Protected output must be rejected before reading input")

        for output in [
            Path("sample_output/sample_output.xlsx"),
            Path("sample_input/config.xlsx"),
        ]:
            args = argparse.Namespace(
                output=output,
                data_dir=Path("sample_input"),
                history_dir=Path("data/history"),
            )
            with self.assertRaises(ValueError):
                execute(never_build, args)

    def test_excel_roundtrip(self):
        frame = transform(stock_frame([310], [0.1]), landing(), DAY)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.xlsx"
            frame.write_excel(path, worksheet="Sheet1", float_precision=15)
            actual = pl.read_excel(path, infer_schema_length=None)
            self.assertEqual(compare(actual, frame)["value_mismatches"].sum(), 0)


if __name__ == "__main__":
    unittest.main()

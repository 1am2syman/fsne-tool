"""Independent tiny cases for the inferred formulas, not copied golden rows."""

import argparse
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from src.cli import execute
from src.compare_report import compare
from src.report import COLUMNS, transform

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
            "DB Landing value": [round(value * 1000) * 2.0 for value in quantities],
            "avg_sec": pl.Series(
                rates if rates is not None else [0.3] * size, dtype=pl.Float64
            ),
            "Primary Sales": [0.1] * size,
            "Secondary Sales": [0.2] * size,
        }
    )


class InferredReportTests(unittest.TestCase):
    def test_fractional_durations_accumulate_before_truncation(self):
        # At .01/day, two .015 batches each take 1.5 days. Third starts on
        # day floor(1.5 + 1.5)=3, NOT floor(1.5)+floor(1.5)=2.
        result = transform(
            stock_frame([310, 310, 310], [0.015, 0.015, 0.1]), DAY
        )
        self.assertEqual(result.columns, COLUMNS)
        self.assertAlmostEqual(result["consumable_kl"][0], 0.015)
        self.assertAlmostEqual(result["consumable_kl"][1], 0.015)
        self.assertAlmostEqual(result["consumable_kl"][2], 0.07)
        self.assertAlmostEqual(result["total_closing_stock_bdt"][2], 200.0)
        self.assertAlmostEqual(result["exposure_bdt"][2], 60.0)

    def test_duration_caps_are_independent_not_ideal_fifo(self):
        # Queue durations use the 90-day selling cutoff: each .2 batch adds
        # 20 days at .01/day. Starts are 0, 20, 40, so only the first batch
        # sells any stock before its own 300-day line (day 5).
        result = transform(
            stock_frame([305, 305, 315], [0.2, 0.2, 0.2]), DAY
        )
        for actual, expected in zip(
            result["consumable_kl"], [0.05, 0.0, 0.0], strict=True
        ):
            self.assertAlmostEqual(actual, expected)

    def test_older_stock_keeps_selling_below_300_days(self):
        # At one carton/month, A adds 180 days and B adds 240 days to
        # the queue. C starts at day 420: after its 300-day deadline
        # (day 150) and its 90-day deadline (day 360). Both risks = 1.
        result = transform(stock_frame([270, 330, 450], [20., 20., 1.], [1.] * 3), DAY)
        self.assertEqual(result["exposure_kl"][2], 1.)
        self.assertEqual(result["lnd_fc_kl"][2], 1.)
        # At twice the rate, A and B still add 180 + 240 days: neither
        # sells out before its 90-day cutoff, so C still has rotation risk.
        self.assertEqual(result["rotation_req_kl"][2], 1.)

    def test_risk_nesting_across_mixed_queues(self):
        from random import Random

        rng = Random(42)
        source = stock_frame(
            [rng.randrange(1, 900) for _ in range(400)],
            [rng.uniform(.001, 2) for _ in range(400)],
            [.3] * 400,
            towns=[f"T{i % 20}" for i in range(400)],
        )
        result = transform(source, DAY)
        self.assertEqual(result.filter(pl.col("lnd_fc_kl") > pl.col("exposure_kl") + 1e-12).height, 0)
        self.assertEqual(result.filter(pl.col("rotation_req_kl") > pl.col("exposure_kl") + 1e-12).height, 0)
        below = result.filter(pl.col("rsl_days") <= 300)
        self.assertEqual(below["exposure_kl"].to_list(), below["total_closing_stock_kl"].to_list())

    def test_town_child_queues_are_independent_despite_same_parent(self):
        source = stock_frame(
            [305, 305, 305], [0.2, 0.2, 0.2], materials=[1, 2, 1], towns=["A", "A", "B"]
        )
        result = transform(source, DAY)
        for quantity in result["consumable_kl"]:
            self.assertAlmostEqual(quantity, 0.05)

    def test_reserve_boundaries_null_rates_and_blank_stock_value(self):
        source = stock_frame(
            [90, 91, 180, 181, 300, 301], [0.1] * 6, rates=[None] * 6
        ).with_columns(pl.lit(None, dtype=pl.Float64).alias("DB Landing value"))
        result = transform(source, DAY)
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
        result = transform(source, DAY)
        self.assertTrue(result.equals(transform(source.reverse(), DAY)))
        self.assertEqual(result["report_date"][0], (DAY - date(1899, 12, 30)).days)
        later = transform(source, DAY + timedelta(days=1))
        self.assertEqual(later["rsl_days"][0], result["rsl_days"][0] - 1)

    def test_duplicate_distributor_aggregation_and_validation(self):
        source = stock_frame([310], [0.1])
        result = transform(pl.concat([source, source]), DAY)
        self.assertEqual(result.height, 1)
        self.assertAlmostEqual(result["total_closing_stock_kl"][0], 0.2)
        self.assertAlmostEqual(result["total_closing_stock_bdt"][0], 400.0)
        # One blank value makes the whole batch value unknown; infinity is rejected.
        blank = source.with_columns(pl.lit(None, dtype=pl.Float64).alias("DB Landing value"))
        self.assertIsNone(transform(pl.concat([source, blank]), DAY)["total_closing_stock_bdt"][0])
        with self.assertRaises(ValueError):
            transform(source.with_columns(pl.lit(float("inf")).alias("DB Landing value")), DAY)
        # Free goods are valued at zero: BDT shares are null, never NaN.
        free = transform(source.with_columns(pl.lit(0.0).alias("DB Landing value")), DAY)
        self.assertEqual(free["total_closing_stock_bdt"][0], 0.0)
        self.assertIsNone(free["exposure_bdt_%"][0])
        self.assertIsNone(free["lnd_fc_bdt_%"][0])
        with self.assertRaises(ValueError):
            transform(
                source.with_columns(pl.lit(-1.0).alias("Closing Stock")), DAY
            )
        with self.assertRaises(ValueError):
            transform(source.with_columns(pl.lit(0.0).alias('Closing Stock')), DAY)
        for invalid in [-1.0, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                transform(
                    source.with_columns(pl.lit(invalid).alias("avg_sec")),
                    DAY,
                )
            with self.assertRaises(ValueError):
                transform(
                    source.with_columns(pl.lit(invalid).alias("Closing Stock")),
                    DAY,
                )
    def test_comparator_detects_wrong_values_nulls_keys_and_nonfinite_values(self):
        frame = transform(stock_frame([310], [0.1]), DAY)
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
        frame = transform(stock_frame([310], [0.1]), DAY)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.xlsx"
            frame.write_excel(path, worksheet="Sheet1", float_precision=15)
            actual = pl.read_excel(path, infer_schema_length=None)
            self.assertEqual(compare(actual, frame)["value_mismatches"].sum(), 0)


if __name__ == "__main__":
    unittest.main()

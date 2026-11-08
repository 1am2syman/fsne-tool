"""Boundary, coverage and reconciliation cases for the upcoming-risk calendar."""

import unittest
from datetime import date, timedelta

import polars as pl

from src.report import EXCEL_EPOCH
from src.risk_calendar import THRESHOLDS, summarise, transform

DAY = date(2026, 2, 28)  # month end before a non-leap March


def batches(rsl, stock=None, bdt=None, rate=None):
    n = len(rsl)
    return pl.DataFrame(
        {
            "report_date": [(DAY - EXCEL_EPOCH).days] * n,
            "batch_index": [f"k{i}" for i in range(n)],
            "Town": ["T"] * n,
            "Brand Name": ["B"] * n,
            "parent_material_code": [1] * n,
            "Material Code": [2] * n,
            "material_desc": ["M"] * n,
            "Batch code": [f"b{i}" for i in range(n)],
            "Expiry Date": [(DAY + timedelta(days=d) - EXCEL_EPOCH).days for d in rsl],
            "rsl_days": rsl,
            "total_closing_stock_kl": stock or [1.0] * n,
            "total_closing_stock_bdt": pl.Series(bdt or [10.0] * n, dtype=pl.Float64),
            "avg_sec_m": pl.Series(rate or [0.5] * n, dtype=pl.Float64),
            "exposure_kl": [0.25] * n,
            "exposure_bdt": [2.5] * n,
        }
    )


def total(series: pl.Series) -> float:
    return float(series.sum())


def pick(events, kind, threshold=None):
    return events.filter(
        (pl.col("event_kind") == kind)
        & (pl.col("threshold_days").is_null() if threshold is None else pl.col("threshold_days") == threshold)
    )


class RiskCalendarTests(unittest.TestCase):
    def test_warning_dates_and_band_boundaries(self):
        # warning-90 falls at days -1,0,1,30,31,60,61,90,91 from snapshot
        rsl = [89, 90, 91, 120, 121, 150, 151, 180, 181]
        e = pick(transform(batches(rsl)), "warning", 90).sort("rsl_days")
        self.assertEqual(e["days_until_event"].to_list(), [r - 90 for r in rsl])
        self.assertEqual(
            e["horizon_band"].to_list(),
            ["due_overdue", "due_overdue", "d1_30", "d1_30", "d31_60", "d31_60", "d61_90", "d61_90", "later"],
        )
        for r, d in zip(e["rsl_days"], e["event_date"], strict=True):
            self.assertEqual(d, DAY + timedelta(days=r - 90))

    def test_leap_day_and_month_end(self):
        e = pick(transform(batches([400])), "warning", 300)
        self.assertEqual(e.item(0, "event_date"), date(2026, 6, 8))
        leap = batches([100]).with_columns(
            pl.lit((date(2027, 12, 31) - EXCEL_EPOCH).days).alias("Expiry Date"),
            pl.lit((date(2027, 12, 31) - DAY).days).alias("rsl_days"),
        )
        ev = pick(transform(leap), "warning", 300).item(0, "event_date")
        self.assertEqual(ev, date(2027, 12, 31) - timedelta(days=300))

    def test_expiry_event_and_past_events_kept(self):
        events = transform(batches([-5, 0, 10]))
        expiry = pick(events, "expiry").sort("rsl_days")
        self.assertEqual(expiry["horizon_band"].to_list(), ["due_overdue", "due_overdue", "d1_30"])
        self.assertTrue(expiry["threshold_days"].is_null().all())
        self.assertEqual(events.height, 3 * (len(THRESHOLDS) + 1))

    def test_sorted_deterministically(self):
        events = transform(batches([100, 100, 500]))
        self.assertEqual(events.sort(["event_date", "batch_id"]).select("event_date")["event_date"].to_list(),
                         events["event_date"].to_list())
        self.assertEqual(events.to_dicts(), transform(batches([100, 100, 500])).to_dicts())

    def test_missing_value_and_rate_stay_visible(self):
        data = batches([100, 100]).with_columns(
            pl.Series("total_closing_stock_bdt", [None, 5.0], dtype=pl.Float64),
            pl.Series("avg_sec_m", [None, 0.0], dtype=pl.Float64),
        )
        e = transform(data)
        self.assertEqual(e.height, 2 * 4)
        self.assertEqual(e.filter(~pl.col("value_known"))["stock_bdt"].null_count(), 4)
        self.assertEqual(e.filter(~pl.col("demand_known")).height, 4)  # zero rate is known
        s = summarise(e).filter(pl.col("event_kind") == "expiry")
        self.assertAlmostEqual(total(s["known_value_bdt"]), 5.0)
        self.assertAlmostEqual(total(s["unpriced_kl"]), 1.0)
        self.assertEqual(s["unpriced_batches"].sum(), 1)
        self.assertEqual(s["no_rate_batches"].sum(), 1)

    def test_summary_reconciles_without_double_counting(self):
        data = batches([-3, 20, 100, 200, 400, 800], stock=[1, 2, 3, 4, 5, 6])
        events = transform(data)
        summary = summarise(events)
        self.assertEqual(summary.height, 4 * 5)
        for _, group in summary.group_by(["event_kind", "threshold_days"]):
            # bands are additive within one event type and cover every batch once
            self.assertEqual(group["batches"].sum(), 6)
            self.assertAlmostEqual(total(group["stock_kl"]), 21.0)
            self.assertAlmostEqual(total(group["exposure_kl"]), 1.5)
        distinct = events.unique("batch_id")
        self.assertAlmostEqual(total(distinct["stock_kl"]), total(data["total_closing_stock_kl"]))
        self.assertEqual(events.height, 6 * 4)  # all population, no truncation

    def test_invalid_inputs(self):
        good = batches([10, 20])
        for bad in [
            good.with_columns(pl.Series("batch_index", ["x", "x"])),
            good.with_columns(pl.Series("total_closing_stock_kl", [1.0, 0.0])),
            good.with_columns(pl.Series("Expiry Date", [None, 1], dtype=pl.Int64)),
            good.with_columns(pl.Series("report_date", [46000, 46001])),
            good.with_columns(pl.Series("rsl_days", [10, 21])),
            good.drop("exposure_kl"),
        ]:
            with self.assertRaises(ValueError):
                transform(bad)
        with self.assertRaises(ValueError):
            transform(good, thresholds=(90, 90))
        with self.assertRaises(ValueError):
            transform(good, thresholds=(0,))
        with self.assertRaises(ValueError):
            transform(good.clear())


if __name__ == "__main__":
    unittest.main()

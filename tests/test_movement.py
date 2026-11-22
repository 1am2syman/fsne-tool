"""Hand-computed cases for the stock-movement report."""

import unittest
from datetime import date

import polars as pl

from src.movement import TOTAL, normalise, transform


def frame(rows):
    return pl.DataFrame(
        {
            "batch_index": [r[0] for r in rows],
            "Brand Name": [r[1] for r in rows],
            "total_closing_stock_bdt": pl.Series([r[2] for r in rows], dtype=pl.Float64),
            "status": [r[3] for r in rows],
            "remarks": pl.Series([r[4] for r in rows], dtype=pl.String),
        }
    )


A, N = "Actionable", "Not actionable"


class MovementTest(unittest.TestCase):
    def setUp(self):
        # key = batch_index minus last two hyphen segments; value segments differ per month
        self.lm = frame([
            ("t1A-10.0-5.0", "X", 100.0, A, None),
            ("t1B-10.0-5.0", "X", 50.0, A, None),
            ("t2C-9.0-4.0", "Y", 80.0, A, None),
            ("t3D-1.0-1.0", "Z", 10.0, A, None),
            ("t4E-1.0-1.0", "X", 999.0, N, None),  # all stock belongs to LM
        ])
        self.cm = frame([
            ("t1A-12.0-6.0", "X", 60.0, A, "Sales Push, Stock rotation"),
            ("t1N-12.0-6.0", "X", 25.0, A, "sales push "),
            ("t2C-9.5-4.5", "Y", 100.0, A, "Other"),
            ("t9Q-1.0-1.0", "W", 7.0, A, None),
            ("t4E-2.0-2.0", "X", 500.0, A, "Late"),  # status changed; physical batch remains
            ("t5F-1.0-1.0", "X", 321.0, N, "Review"),
        ])

    def row(self, out, brand):
        return out.filter(pl.col("brand") == brand).row(0, named=True)

    def test_reduction_and_new_inclusions(self):
        out = transform(self.cm, self.lm)
        x = self.row(out, "X")
        self.assertEqual((x["lm_stock_bdt"], x["cm_no_new_bdt"], x["cm_new_bdt"]), (1149.0, 560.0, 346.0))
        self.assertEqual(x["reduced_bdt"], 589.0)
        self.assertAlmostEqual(x["reduction_pct"], 589 / 1149)
        # Y's existing batch t2C grew 80 -> 100: 80 carries over, +20 top-up counts under new inclusions, 0 reduced.
        y = self.row(out, "Y")
        self.assertEqual((y["lm_stock_bdt"], y["cm_no_new_bdt"], y["cm_new_bdt"]), (80.0, 80.0, 20.0))
        self.assertEqual((y["reduced_bdt"], y["reduction_pct"]), (0.0, 0.0))

    def test_brand_only_in_one_side(self):
        out = transform(self.cm, self.lm)
        z = self.row(out, "Z")  # LM only: fully reduced
        self.assertEqual((z["reduced_bdt"], z["reduction_pct"]), (10.0, 1.0))
        self.assertIsNone(z["cm_no_new_bdt"])
        w = self.row(out, "W")  # CM only: all new, no reduction
        self.assertEqual(w["cm_new_bdt"], 7.0)
        self.assertIsNone(w["lm_stock_bdt"])
        self.assertIsNone(w["reduced_bdt"])

    def test_sort_and_grand_total(self):
        out = transform(self.cm, self.lm)
        self.assertEqual(out["brand"].to_list(), ["X", "Z", "Y", "W", TOTAL])
        t = self.row(out, TOTAL)
        self.assertEqual((t["lm_stock_bdt"], t["cm_no_new_bdt"], t["cm_new_bdt"], t["reduced_bdt"]), (1239.0, 640.0, 373.0, 599.0))
        self.assertAlmostEqual(t["reduction_pct"], 599 / 1239)

    def test_remarks_distinct_case_insensitive_first_seen(self):
        out = transform(self.cm, self.lm)
        self.assertEqual(self.row(out, "X")["remarks"], "Sales Push, Stock rotation, Late, Review")
        self.assertEqual(self.row(out, TOTAL)["remarks"], "Sales Push, Stock rotation, Other, Late, Review")

    def test_normalise_accepts_both_column_spellings(self):
        base = {"report_date": ["2026-07-31 00:00:00"], "batch_index": ["k"], "Brand Name": ["B"], "total_closing_stock_bdt": ["1.5"]}
        old = normalise(pl.DataFrame({**base, "action_plan_remarks": ["r"], "mitigation_status_category": [" Actionable "]}))
        new = normalise(pl.DataFrame({**base, "action_plan": ["r"], "mitigation_status": ["Actionable"]}))
        self.assertTrue(old.equals(new))
        self.assertEqual(new["status"][0], "Actionable")


class SeriesTest(unittest.TestCase):
    def make(self):
        from datetime import date

        def month(d, rows):
            f = frame([(r[0], r[1], r[2], r[3], r[4]) for r in rows])
            return f.with_columns(pl.lit(d).alias("report_date"))

        self.m = [
            month(date(2026, 1, 31), [("a-1-1", "X", 5.0, "Not actionable", None)]),  # unlabelled/actionability does not constrain the calendar
            month(date(2026, 2, 28), [("a-1-1", "X", 100.0, "Actionable - rotation", None), ("b-1-1", "Y", 40.0, "Actionable - tbc", None)]),
            month(date(2026, 3, 31), [("a-2-2", "X", 70.0, "Actionable", "Push"), ("c-1-1", "X", 30.0, "Actionable", "push"), ("b-2-2", "Y", 10.0, "Actionable", None)]),
            month(date(2026, 4, 30), [("a-3-3", "X", 20.0, "Actionable", "Rotate"), ("c-2-2", "X", 50.0, "Actionable", None), ("d-1-1", "Z", 9.0, "Actionable", None)]),
        ]
        return pl.concat(self.m)

    def test_series_matches_pairwise_transform_and_hand_values(self):
        from src.movement import transform_series

        out = transform_series(self.make(), months=6)
        self.assertEqual(min(out["month"].unique().to_list()).isoformat(), "2026-02-28")  # January is LM, regardless of status
        self.assertEqual(out["month"].n_unique(), 3)
        # Mar: X LM 100, carried a (70), new c (30)
        x = out.filter((pl.col("brand") == "X") & (pl.col("month").dt.month() == 3)).row(0, named=True)
        self.assertEqual((x["lm_stock_bdt"], x["cm_no_new_bdt"], x["cm_new_bdt"], x["reduced_bdt"]), (100.0, 70.0, 30.0, 30.0))
        # Apr: X LM 100 (a=70, c=30); CM has a=20 (carried 20, cleared 50) and c=50 (carried 30, +20 top-up)
        x = out.filter((pl.col("brand") == "X") & (pl.col("month").dt.month() == 4)).row(0, named=True)
        self.assertEqual((x["lm_stock_bdt"], x["cm_no_new_bdt"], x["cm_new_bdt"], x["reduced_bdt"]), (100.0, 50.0, 20.0, 50.0))
        z = out.filter((pl.col("brand") == "Z") & (pl.col("month").dt.month() == 4)).row(0, named=True)
        self.assertEqual((z["lm_stock_bdt"], z["cm_new_bdt"], z["reduced_bdt"]), (None, 9.0, None))
        # same numbers as the single-pair function
        for d, (cm, lm) in {2: (self.m[1], self.m[0]), 3: (self.m[2], self.m[1]), 4: (self.m[3], self.m[2])}.items():
            pair = transform(cm, lm).drop("remarks")
            ser = out.filter(pl.col("month").dt.month() == d).drop("month", "remarks")
            self.assertTrue(pair.sort("brand").equals(ser.sort("brand")), (d, pair, ser))

    def test_edge_cases_never_produce_negative_cleared_or_nan(self):
        from src.movement import transform_series

        # Covers: zero LM stock growing, null LM/CM stock, duplicate keys in a month,
        # negative credit row, mixed shrinking+growing keys, and cross-month brand/RSL remaps.
        lm = pl.DataFrame({
            "report_date": [date(2026, 7, 31)] * 7,
            "batch_index": ["z-1-1", "n-1-1", "d-1-1", "d-2-2", "g-1-1", "s-1-1", "r-1-1"],
            "Brand Name": ["ZERO", "NULLB", "DUP", "DUP", "MIX", "MIX", "OLD_BRAND"],
            "rsl_group": ["6M - 10M"] * 6 + ["> 10M"],
            "total_closing_stock_bdt": pl.Series([0.0, None, 30.0, 20.0, 10.0, 90.0, 40.0], dtype=pl.Float64),
            "status": [A] * 7,
            "remarks": pl.Series([None] * 7, dtype=pl.String),
        })
        cm = pl.DataFrame({
            "report_date": [date(2026, 8, 30)] * 7,
            "batch_index": ["z-2-2", "n-2-2", "d-3-3", "d-4-4", "g-2-2", "s-2-2", "r-2-2"],
            "Brand Name": ["ZERO", "NULLB", "DUP", "DUP", "MIX", "MIX", "NEW_BRAND"],
            "rsl_group": ["6M - 10M"] * 6 + ["6M - 10M"],
            "total_closing_stock_bdt": pl.Series([4.0, 15.0, 40.0, 30.0, 80.0, -5.0, 40.0], dtype=pl.Float64),
            "status": [A] * 7,
            "remarks": pl.Series([None] * 7, dtype=pl.String),
        })
        h = pl.concat([lm, cm])
        out = transform_series(h, months=6)
        by_b = {r["brand"]: r for r in out.iter_rows(named=True)}

        self.assertEqual((by_b["ZERO"]["lm_stock_bdt"], by_b["ZERO"]["cm_no_new_bdt"], by_b["ZERO"]["cm_new_bdt"], by_b["ZERO"]["reduced_bdt"]), (0.0, 0.0, 4.0, 0.0))
        self.assertIsNone(by_b["ZERO"]["reduction_pct"])
        self.assertEqual((by_b["NULLB"]["lm_stock_bdt"], by_b["NULLB"]["cm_no_new_bdt"], by_b["NULLB"]["cm_new_bdt"], by_b["NULLB"]["reduced_bdt"]), (0.0, 0.0, 15.0, 0.0))
        self.assertIsNone(by_b["NULLB"]["reduction_pct"])
        self.assertEqual((by_b["DUP"]["lm_stock_bdt"], by_b["DUP"]["cm_no_new_bdt"], by_b["DUP"]["cm_new_bdt"], by_b["DUP"]["reduced_bdt"]), (50.0, 50.0, 20.0, 0.0))
        self.assertEqual((by_b["MIX"]["lm_stock_bdt"], by_b["MIX"]["cm_no_new_bdt"], by_b["MIX"]["cm_new_bdt"], by_b["MIX"]["reduced_bdt"]), (100.0, 10.0, 70.0, 90.0))
        self.assertAlmostEqual(by_b["MIX"]["reduction_pct"], 0.9)
        self.assertNotIn("OLD_BRAND", by_b)
        self.assertEqual((by_b["NEW_BRAND"]["lm_stock_bdt"], by_b["NEW_BRAND"]["cm_no_new_bdt"], by_b["NEW_BRAND"]["reduced_bdt"]), (40.0, 40.0, 0.0))

        by_rsl = transform_series(h, months=6, keys=("Brand Name", "rsl_group"))
        self.assertTrue((by_rsl["reduced_bdt"].drop_nulls() >= 0.0).all())
        rsl_tot = by_rsl.filter(pl.col("brand") == TOTAL)
        plain_tot = by_b[TOTAL]
        for col in ("lm_stock_bdt", "cm_no_new_bdt", "cm_new_bdt", "reduced_bdt"):
            self.assertAlmostEqual(rsl_tot[col].sum(), plain_tot[col])

        pair = transform(cm, lm).drop("remarks")
        ser = out.drop("month", "remarks")
        self.assertTrue(pair.sort("brand").equals(ser.sort("brand")), (pair, ser))

    def test_rsl_groups_add_up_and_use_later_month_group(self):
        from src.movement import transform_series

        def rsl(df, groups):
            return df.with_columns(pl.Series("rsl_group", groups))

        lm = rsl(frame([("a-1-1", "X", 100.0, A, None), ("b-1-1", "X", 40.0, A, None)]), ["6M - 10M", "> 10M"])
        cm = rsl(frame([("a-2-2", "X", 70.0, A, None), ("c-1-1", "X", 5.0, A, None)]), ["3M - 6M", "< 3M"])
        h = pl.concat([
            lm.with_columns(pl.lit(date(2026, 3, 31)).alias("report_date")),
            cm.with_columns(pl.lit(date(2026, 4, 30)).alias("report_date")),
        ])
        plain = transform_series(h).filter(pl.col("brand") == TOTAL).row(0, named=True)
        out = transform_series(h, keys=("Brand Name", "rsl_group")).filter(pl.col("brand") == TOTAL)
        for col in ("lm_stock_bdt", "cm_no_new_bdt", "cm_new_bdt", "reduced_bdt"):
            self.assertEqual(out[col].sum(), plain[col])
        g = {r["rsl_group"]: r for r in out.iter_rows(named=True)}
        # batch a moved 6M-10M -> 3M-6M: both its LM and CM stock sit in the later group
        self.assertEqual((g["3M - 6M"]["lm_stock_bdt"], g["3M - 6M"]["cm_no_new_bdt"]), (100.0, 70.0))
        # batch b vanished: stays in its own LM group
        self.assertEqual(g["> 10M"]["reduced_bdt"], 40.0)

    def test_remarks_per_month_and_window(self):
        from src.movement import transform_series

        out = transform_series(self.make(), months=1)
        self.assertEqual(out["month"].n_unique(), 1)  # only the last month
        x = out.filter(pl.col("brand") == "X").row(0, named=True)
        self.assertEqual(x["remarks"], "Rotate")

"""Hand-computed cases for the stock-movement report."""

import unittest

import polars as pl

from src.movement import TOTAL, transform


def frame(rows):
    return pl.DataFrame(
        {
            "batch_index": [r[0] for r in rows],
            "Brand Name": [r[1] for r in rows],
            "total_closing_stock_bdt": pl.Series([r[2] for r in rows], dtype=pl.Float64),
        }
    )


class MovementTest(unittest.TestCase):
    def setUp(self):
        # key = batch_index minus last two hyphen segments; the value segments differ
        self.lm = frame([("t1A-10.0-5.0", "X", 100.0), ("t1B-10.0-5.0", "X", 50.0), ("t2C-9.0-4.0", "Y", 80.0), ("t3D-1.0-1.0", "Z", 10.0)])
        self.cm = frame([("t1A-12.0-6.0", "X", 60.0), ("t1N-12.0-6.0", "X", 25.0), ("t2C-9.5-4.5", "Y", 100.0), ("t9Q-1.0-1.0", "W", 7.0)])

    def row(self, out, brand):
        return out.filter(pl.col("brand") == brand).row(0, named=True)

    def test_reduction_and_new_inclusions(self):
        out = transform(self.cm, self.lm)
        x = self.row(out, "X")
        self.assertEqual((x["lm_stock_bdt"], x["cm_no_new_bdt"], x["cm_new_bdt"]), (150.0, 60.0, 25.0))
        self.assertEqual(x["reduced_bdt"], 90.0)
        self.assertAlmostEqual(x["reduction_pct"], 0.6)
        y = self.row(out, "Y")
        self.assertEqual(y["reduced_bdt"], -20.0)  # increase is negative
        self.assertIsNone(y["cm_new_bdt"])

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
        self.assertEqual(out["brand"].to_list(), ["X", "Z", "Y", "W", TOTAL])  # nulls last, total last
        t = self.row(out, TOTAL)
        self.assertEqual((t["lm_stock_bdt"], t["cm_no_new_bdt"], t["cm_new_bdt"], t["reduced_bdt"]), (240.0, 160.0, 32.0, 80.0))
        self.assertAlmostEqual(t["reduction_pct"], 80 / 240)

    def test_mitigation_filter_and_remarks(self):
        mit = pl.DataFrame(
            {
                "batch_index": ["t1A-0-0", "t1B-0-0", "t1N-0-0", "t2C-0-0", "t9Q-0-0"],
                "mitigation_status_category": ["A", "A", "A", "B", "A"],
                "remarks": ["Sales Push", "Sales Push", "Stock rotation", "Other", "Sales Push"],
            }
        )
        out = transform(self.cm, self.lm, mit, ["A"])
        self.assertNotIn("Y", out["brand"].to_list())
        self.assertEqual(self.row(out, "X")["remarks"], "Sales Push, Stock rotation")
        t = self.row(out, TOTAL)
        self.assertEqual(t["remarks"], "Sales Push, Stock rotation")
        self.assertEqual(t["lm_stock_bdt"], 150.0)  # Z has no mitigation tag in LM -> excluded

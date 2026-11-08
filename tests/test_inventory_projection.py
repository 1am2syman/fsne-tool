"""Depletion fixtures: conservation, unique demand allocation, deadlines, ties."""

import itertools
import unittest
from datetime import date

import polars as pl

from src.inventory_projection import child_shares, deplete, transform
from tests.test_risk_calendar import batches


def pool(*rows):
    return [(b, rsl, kl, rec) for b, rsl, kl, rec in rows]


def remaining(path):
    return {k: v[-1] for k, v in path.items()}


class DepleteTests(unittest.TestCase):
    def test_earliest_expiry_first_and_tie_by_receipt(self):
        path = deplete(pool(("late", 50, 1.0, None), ("early", 10, 1.0, None)), 0.5, 2)
        self.assertEqual(remaining(path), {"early": 0.0, "late": 1.0})
        tie = deplete(
            pool(("b", 30, 1.0, date(2026, 1, 2)), ("a", 30, 1.0, date(2026, 1, 9)), ("z", 30, 1.0, None)),
            0.5,
            2,
        )
        self.assertEqual(remaining(tie), {"b": 0.0, "a": 1.0, "z": 1.0})
        # same expiry and receipt: stable by batch id
        same = deplete(pool(("y", 30, 1.0, None), ("x", 30, 1.0, None)), 1.0, 1)
        self.assertEqual(remaining(same), {"x": 0.0, "y": 1.0})

    def test_no_sales_on_or_after_expiry_date(self):
        # rsl=2 sells on days 0 and 1 only; the remainder is unsold stock.
        path = deplete(pool(("a", 2, 10.0, None)), 1.0, 5)["a"]
        self.assertEqual(path[:4], [10.0, 9.0, 8.0, 8.0])
        self.assertEqual(path[-1], 8.0)
        expired = deplete(pool(("a", 0, 3.0, None), ("b", -4, 2.0, None)), 1.0, 3)
        self.assertEqual(remaining(expired), {"a": 3.0, "b": 2.0})

    def test_conservation_nonnegative_and_demand_cap(self):
        data = pool(("a", 3, 0.7, None), ("b", 8, 1.3, None), ("c", 40, 2.0, None))
        path = deplete(data, 0.45, 20)
        days = 20
        sold = sum(v[0] - v[-1] for v in path.values())
        self.assertLessEqual(sold, 0.45 * days + 1e-9)
        self.assertTrue(all(x >= 0 for v in path.values() for x in v))
        for v in path.values():  # remaining never increases
            self.assertTrue(all(b <= a + 1e-12 for a, b in itertools.pairwise(v)))
        self.assertAlmostEqual(sold + sum(remaining(path).values()), 4.0)

    def test_spill_across_batches_within_one_day(self):
        path = deplete(pool(("a", 9, 0.25, None), ("b", 9, 1.0, None)), 0.5, 1)
        self.assertEqual(remaining(path), {"a": 0.0, "b": 0.75})


def shares(rows):
    return pl.DataFrame(
        rows,
        schema={"town": pl.String, "parent_material_code": pl.Int64, "material_code": pl.Int64, "share": pl.Float64},
        orient="row",
    )


def two_children(rate=3.0):
    data = batches([100, 100], stock=[2.0, 2.0], rate=[rate, rate]).with_columns(
        pl.Series("Material Code", [2, 3])
    )
    return data


class TransformTests(unittest.TestCase):
    def sold(self, result, scenario):
        rows = result.filter(
            (pl.col("scenario_id") == scenario) & (pl.col("event_kind") == "expiry")
        )
        return rows

    def test_parent_rate_allocated_once_and_children_not_interchangeable(self):
        # parent 3 KL/month = 0.1 KL/day split 0.7/0.3; expiry at day 100 > horizon
        r = transform(two_children(), shares([("T", 1, 2, 0.7), ("T", 1, 3, 0.3)]), horizon=30)
        w = r.filter((pl.col("event_kind") == "warning") & (pl.col("threshold_days") == 90) & (pl.col("scenario_id") == "baseline"))
        # warning-90 falls at day 10 for rsl=100
        by = {row["material_code"]: row["projected_remaining_kl"] for row in w.iter_rows(named=True)}
        self.assertAlmostEqual(by[2], 2.0 - 0.07 * 10)
        self.assertAlmostEqual(by[3], 2.0 - 0.03 * 10)
        total_sold = (4.0 - sum(by.values()))
        self.assertAlmostEqual(total_sold, 0.1 * 10)  # once, not twice

    def test_child_without_sales_has_zero_rate_not_parent_rate(self):
        r = transform(two_children(), shares([("T", 1, 2, 1.0)]), horizon=30)
        w = r.filter((pl.col("threshold_days") == 90) & (pl.col("scenario_id") == "baseline"))
        by = {row["material_code"]: row for row in w.iter_rows(named=True)}
        self.assertAlmostEqual(by[3]["projected_remaining_kl"], 2.0)
        self.assertEqual(by[3]["coverage_status"], "modeled")
        self.assertEqual(by[3]["child_rate_kl_month"], 0.0)

    def test_scenarios_change_only_modeled_fields(self):
        r = transform(two_children(), shares([("T", 1, 2, 0.5), ("T", 1, 3, 0.5)]), horizon=30)
        a = r.filter(pl.col("scenario_id") == "baseline").drop("scenario_id", "rate_multiplier", "projected_remaining_kl", "projected_value_bdt", "child_rate_kl_month")
        b = r.filter(pl.col("scenario_id") == "rate_2x").drop("scenario_id", "rate_multiplier", "projected_remaining_kl", "projected_value_bdt", "child_rate_kl_month")
        self.assertTrue(a.equals(b))
        m = r.filter((pl.col("threshold_days") == 90) & (pl.col("material_code") == 2))
        base, double = (m.filter(pl.col("scenario_id") == s).item(0, "projected_remaining_kl") for s in ["baseline", "rate_2x"])
        self.assertAlmostEqual(2.0 - base, (2.0 - double) / 2)

    def test_unknown_known_zero_past_and_beyond_horizon(self):
        data = batches([-5, 100, 100, 400], stock=[1.0] * 4, rate=[0.5, None, 0.0, 0.5]).with_columns(
            pl.Series("Material Code", [2, 3, 4, 5]),
            pl.Series("total_closing_stock_bdt", [10.0, 10.0, None, 10.0], dtype=pl.Float64),
        )
        s = shares([("T", 1, c, 1.0 / 4) for c in (2, 3, 4, 5)])
        r = transform(data, s, horizon=30).filter(pl.col("scenario_id") == "baseline")

        def get(code, thr):
            hit = r.filter((pl.col("material_code") == code) & (pl.col("threshold_days") == thr))
            return hit.row(0, named=True)

        past = get(2, 90)
        self.assertEqual((past["coverage_status"], past["projected_remaining_kl"]), ("observed_past_event", 1.0))
        unknown = get(3, 90)
        self.assertEqual((unknown["coverage_status"], unknown["projected_remaining_kl"]), ("unknown_no_rate", None))
        zero = get(4, 90)
        self.assertEqual((zero["coverage_status"], zero["projected_remaining_kl"]), ("modeled", 1.0))
        self.assertIsNone(zero["projected_value_bdt"])  # unpriced stays unknown
        far = get(5, 300)
        self.assertEqual(far["coverage_status"], "beyond_horizon")  # event at day 100 > 30
        self.assertEqual(r.filter((pl.col("material_code") == 5) & (pl.col("event_kind") == "expiry")).item(0, "coverage_status"), "beyond_horizon")
        self.assertEqual(r.height, 4 * 4)

    def test_invalid_shares_and_scenarios(self):
        data = two_children()
        for bad in [shares([("T", 1, 2, 0.8), ("T", 1, 3, 0.8)]), shares([("T", 1, 2, 1.5)]), shares([("T", 1, 2, 0.5), ("T", 1, 2, 0.5)])]:
            with self.assertRaises(ValueError):
                transform(data, bad)
        ok = shares([("T", 1, 2, 1.0)])
        with self.assertRaises(ValueError):
            transform(data, ok, scenarios=(("x", 0.0),))
        with self.assertRaises(ValueError):
            transform(data, ok, horizon=0)

    def test_child_shares_sum_to_one_and_clamp_returns(self):
        source = pl.DataFrame(
            {
                "area_name (city)": ["T", "T", "T", "U"],
                "parent_material_code": ["1", "1", "1", "1"],
                "material_code": ["2", "2", "3", "2"],
                "sec_vol_kl_mth (billwise)": ["3", "1", "-5", "2"],
            }
        )
        s = child_shares(source).sort("town", "material_code")
        self.assertEqual(s["share"].to_list(), [1.0, 0.0, 1.0])  # return clamped: child 3 share 0
        self.assertEqual(s.filter(pl.col("town") == "T")["material_code"].to_list(), [2, 3])


if __name__ == "__main__":
    unittest.main()

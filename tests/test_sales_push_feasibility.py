"""Hand-computed sales-push fixtures: pace, solver, conservation, honesty."""

import unittest

import polars as pl

from src.sales_push_feasibility import (
    MAX_UPLIFT,
    Pool,
    Scenario,
    history_evidence,
    queue_ahead_kl,
    simulate,
    solve_uplift,
    transform,
    transform_requirements,
)
from tests.test_risk_calendar import batches

WEEK = 7.0


def pool(entries, weekly):
    monthly = None if weekly is None else weekly / 7 * 30
    return Pool("T", 2, [(b, r, k, None) for b, r, k in entries], monthly)


def shares(*children):
    return pl.DataFrame(
        [("T", 1, c, s) for c, s in children],
        schema={"town": pl.String, "parent_material_code": pl.Int64, "material_code": pl.Int64, "share": pl.Float64},
        orient="row",
    )


class PaceTests(unittest.TestCase):
    def test_isolated_fixture_12kl_28_days_2kl_week(self):
        p = pool([("a", 999, 12.0)], 2.0)
        sales, residual = simulate(p, Scenario("base"), 28)
        self.assertAlmostEqual(residual, 4.0)
        self.assertAlmostEqual(sales, 8.0)
        status, uplift = solve_uplift(p, 28)
        self.assertEqual(status, "solved_approximate")
        self.assertIsNotNone(uplift)
        self.assertAlmostEqual(float(uplift or 0), 1.5, places=3)

    def test_requirements_total_and_incremental_pace(self):
        data = batches([999], stock=[12.0], rate=[2.0 / 7 * 30])
        r = transform_requirements(data, shares((2, 1.0)), deadline_days=28).row(0, named=True)
        self.assertAlmostEqual(r["required_total_kl_week"], 3.0, places=3)
        self.assertAlmostEqual(r["required_incremental_kl_week"], 1.0, places=3)
        self.assertAlmostEqual(r["required_uplift"], 1.5, places=3)
        self.assertAlmostEqual(r["simple_required_kl_week"], 3.0)
        self.assertEqual(r["review_state"], "stretch_scenario")

    def test_no_uplift_needed(self):
        status, uplift = solve_uplift(pool([("a", 999, 4.0)], 2.0), 28)
        self.assertEqual((status, uplift), ("no_uplift_needed", 1.0))

    def test_not_feasible_within_bounds(self):
        status, uplift = solve_uplift(pool([("a", 999, 12.0)], 0.1), 28)
        self.assertEqual((status, uplift), ("not_feasible_within_bounds", None))
        self.assertGreater(MAX_UPLIFT, 1)

    def test_expiry_before_deadline_limits_sales(self):
        p = pool([("a", 7, 12.0)], 0.5)  # 7 selling days: max 0.5*20 = 10 KL < 12
        self.assertEqual(solve_uplift(p, 28)[0], "not_feasible_within_bounds")
        r = transform_requirements(batches([7], stock=[12.0], rate=[0.5 / 7 * 30]), shares((2, 1.0)), deadline_days=28)
        self.assertIn("expiry_before_deadline", r.item(0, "reason_codes"))
        self.assertEqual(r.item(0, "review_state"), "infeasible_under_stated_constraints")


class EdgeTests(unittest.TestCase):
    def test_deadline_today_unknown_and_zero_baseline(self):
        data = batches([999], stock=[5.0])
        r = transform_requirements(data, shares((2, 1.0)), deadline_days=0).row(0, named=True)
        self.assertEqual(r["solver_status"], "deadline_not_after_origin")
        self.assertIsNone(r["simple_required_kl_week"])
        unknown = transform_requirements(batches([999], stock=[5.0], rate=[None]), shares((2, 1.0)), deadline_days=28)
        self.assertEqual(unknown.item(0, "solver_status"), "baseline_unknown")
        self.assertIsNone(unknown.item(0, "required_uplift"))
        zero = transform_requirements(batches([999], stock=[5.0], rate=[0.0]), shares((2, 1.0)), deadline_days=28)
        self.assertEqual(zero.item(0, "solver_status"), "zero_baseline")
        self.assertIsNone(zero.item(0, "required_uplift"))
        self.assertAlmostEqual(zero.item(0, "required_total_kl_week"), 5.0 / 4)
        rows = transform(batches([999], stock=[5.0], rate=[None]), shares((2, 1.0)), deadline_days=28)
        self.assertTrue(rows["projected_residual_kl"].is_null().all())

    def test_delay_and_duration_apply_only_in_interval(self):
        p = pool([("a", 999, 100.0)], 7.0)  # 1 KL/day baseline
        sales, _ = simulate(p, Scenario("x", 2.0, delay_days=10, duration_days=5), 20)
        self.assertAlmostEqual(sales, 20 + 5)
        sales, _ = simulate(p, Scenario("late", 3.0, delay_days=20), 20)
        self.assertAlmostEqual(sales, 20)  # starts at the deadline: no effect

    def test_conservation_and_monotonic_in_multiplier(self):
        p = pool([("a", 5, 3.0), ("b", 40, 6.0), ("c", 999, 9.0)], 3.0)
        last = None
        for m in (1, 1.5, 2, 4, 8):
            sales, residual = simulate(p, Scenario("m", m), 30)
            self.assertAlmostEqual(sales + residual, 18.0)
            self.assertGreaterEqual(residual, 0)
            self.assertLessEqual(sales, 18.0 + 1e-9)
            if last is not None:
                self.assertLessEqual(residual, last + 1e-9)
            last = residual

    def test_input_order_invariance(self):
        e = [("a", 5, 3.0), ("b", 40, 6.0), ("c", 999, 9.0)]
        self.assertEqual(solve_uplift(pool(e, 3.0), 30), solve_uplift(pool(e[::-1], 3.0), 30))

    def test_target_subset_and_queue_ahead_use_shared_capacity(self):
        p = pool([("first", 20, 6.0), ("target", 999, 6.0)], 7.0)  # 1 KL/day
        self.assertAlmostEqual(queue_ahead_kl(p, {"target"}), 6.0)
        _, residual = simulate(p, Scenario("base"), 8, {"target"})
        self.assertAlmostEqual(residual, 4.0)  # ahead stock used days 0-5; target sells days 6-7
        status, uplift = solve_uplift(p, 10, {"target"})
        self.assertEqual(status, "solved_approximate")
        self.assertIsNotNone(uplift)
        self.assertAlmostEqual(float(uplift or 0), 1.2, places=3)  # 12 KL in 10 days at 1/day

    def test_shared_demand_not_duplicated_across_children(self):
        data = batches([999, 999], stock=[10.0, 10.0], rate=[3.0, 3.0]).with_columns(
            pl.Series("Material Code", [2, 3])
        )
        r = transform(data, shares((2, 0.5), (3, 0.5)), deadline_days=30)
        base = r.filter(pl.col("scenario_id") == "no_action")
        self.assertAlmostEqual(float(base["projected_sales_kl"].sum()), 3.0)  # 3 KL/month once

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            Scenario("x", 0.0)
        with self.assertRaises(ValueError):
            Scenario("x", 1.0, delay_days=-1)
        with self.assertRaises(ValueError):
            transform(batches([99]), shares((2, 1.0)), deadline_kind="internal_warning")
        with self.assertRaises(ValueError):
            transform(batches([99]), shares((2, 1.0)), scenarios=(Scenario("a"), Scenario("a")))


def monthly_source(values, parent="1", town="T", child="2"):
    """values: {month_number: kl}; missing months are absent rows."""
    return pl.DataFrame(
        [
            {
                "area_name (city)": town,
                "parent_material_code": parent,
                "material_code": child,
                "BATCH_NBR": "B",
                "Month-Year": f"2025-{m:02}-01",
                "sec_vol_kl_mth (billwise)": str(v),
            }
            for m, v in values.items()
        ]
    )


class HistoryTests(unittest.TestCase):
    def test_window_gaps_peak_and_recent(self):
        # window Jan..Jun with March absent (calendar gap of 1 month)
        src = monthly_source({1: 30, 2: 30, 4: 60, 5: 30, 6: 90}).vstack(
            monthly_source({1: 30, 2: 30, 3: 30, 4: 30, 5: 30, 6: 30}, town="U")
        )
        e = history_evidence(src).filter(pl.col("town") == "T").row(0, named=True)
        self.assertEqual((e["hist_window_months"], e["hist_observed_months"]), (6, 5))
        self.assertEqual(e["hist_calendar_gap_months"], 0)  # other town observed March
        self.assertAlmostEqual(e["_peak"], 90 * 7 / 30)
        self.assertEqual(e["hist_peak_month"].month, 6)
        self.assertEqual(e["hist_recent_months_observed"], 3)  # Apr, May, Jun
        self.assertAlmostEqual(e["_recent"], (60 + 30 + 90) / 3 * 7 / 30)

    def test_global_calendar_gap_is_reported(self):
        e = history_evidence(monthly_source({1: 30, 2: 30, 4: 30})).row(0, named=True)
        self.assertEqual((e["hist_window_months"], e["hist_calendar_gap_months"]), (3, 1))

    def test_comparison_flags_above_peak_without_blocking(self):
        # peak 30 KL/month = 7 KL/week; stock 12 KL in 28 days needs 3/week: within peak
        src = monthly_source({m: 30 for m in range(1, 7)})
        data = batches([999], stock=[12.0], rate=[2.0 / 7 * 30])
        ev = history_evidence(src)
        ok = transform_requirements(data, shares((2, 1.0)), deadline_days=28, evidence=ev).row(0, named=True)
        self.assertEqual(ok["hist_comparison"], "within_observed_range")
        big = batches([999], stock=[120.0], rate=[2.0 / 7 * 30])
        hi = transform_requirements(big, shares((2, 1.0)), deadline_days=28, evidence=ev).row(0, named=True)
        self.assertEqual(hi["hist_comparison"], "above_observed_peak")
        self.assertIn("required_above_observed_peak", hi["reason_codes"])
        none = transform_requirements(data, shares((2, 1.0)), deadline_days=28).row(0, named=True)
        self.assertEqual(none["hist_comparison"], "no_history")
        self.assertIn("not unconstrained demand", none["hist_caveats"])

    def test_child_share_scales_pool_evidence(self):
        src = monthly_source({m: 30 for m in range(1, 7)})
        data = batches([999], stock=[1.0], rate=[3.0])
        r = transform_requirements(data, shares((2, 0.25)), deadline_days=28, evidence=history_evidence(src)).row(0, named=True)
        self.assertAlmostEqual(r["hist_peak_kl_week"], 30 * 7 / 30 * 0.25)


if __name__ == "__main__":
    unittest.main()

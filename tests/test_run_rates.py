"""Independent controls for conservative, seasonality-aware winsorization."""
import unittest
from datetime import date

import polars as pl

from src import report, run_rate_parent, run_rate_town, sec_history
from src.doh import stock_pairs
from tests.test_report import DAY, stock_frame


def sales(spikes=None, towns=12, months=12, seasonal_month=5):
    spikes = spikes or {}
    rows = []
    for t in range(towns):
        for m in range(months):
            # Deliberately very different town sizes; consensus uses ratios.
            value = (t + 1) * 10.0 * (4 if m == seasonal_month else 1)
            value *= spikes.get((t, m), 1)
            rows.append({'area_name (city)': f'T{t:02}', 'parent_material_code': '10',
                         'material_code': '1', 'BATCH_NBR': 'B',
                         'Month-Year': f'2025-{m + 1:02}-01', 'sec_vol_kl_mth (billwise)': str(value)})
    return pl.DataFrame(rows)


class WinsorTests(unittest.TestCase):
    def test_shared_seasonal_peak_is_not_clipped_and_town_scale_is_preserved(self):
        source = sales()
        audit = run_rate_parent.adjustments(source)
        self.assertEqual(audit.filter(pl.col('reason') == 'winsorized').height, 0)
        self.assertEqual(audit.filter(pl.col('month') == date(2025, 6, 1))['season_factor'].to_list(), [4.0] * 12)
        self.assertTrue(audit['raw_kl'].equals(audit['adjusted_kl']))
        seasonal = audit.filter(pl.col('month') == date(2025, 6, 1))
        self.assertTrue(seasonal['seasonal_supported'].all())
        self.assertTrue((seasonal['raw_kl'] > seasonal['unseasoned_upper_kl']).all())
        self.assertTrue((seasonal['raw_kl'] <= seasonal['upper_kl']).all())
        self.assertFalse(audit.filter(pl.col('month') != date(2025, 6, 1))['seasonal_supported'].any())

    def test_isolated_spike_is_winsorized_after_seasonal_adjustment(self):
        # June's legitimate 4x season remains; an additional isolated 10x is capped.
        audit = run_rate_parent.adjustments(sales({(0, 5): 10}))
        clipped = audit.filter(pl.col('reason') == 'winsorized')
        self.assertEqual(clipped.height, 1)
        self.assertEqual(clipped['raw_kl'][0], 400)
        self.assertEqual(clipped['season_factor'][0], 4)
        self.assertEqual(clipped['adjusted_kl'][0], 80)
        self.assertFalse(clipped['seasonal_supported'][0])
        rate = run_rate_parent.rates_from_adjustments(audit).filter(pl.col('town') == 'T00')['avg_sec'][0]
        self.assertAlmostEqual(rate, (11 * 10 + 80) / 12)

    def test_sustained_and_boundary_peaks_are_preserved(self):
        audit = run_rate_parent.adjustments(sales({(0, 2): 10, (0, 3): 10, (1, 11): 20}))
        self.assertEqual(audit.filter(pl.col('reason') == 'winsorized').height, 0)
        self.assertEqual(audit.filter(pl.col('reason') == 'ambiguous_peak_preserved').height, 3)

    def test_insufficient_history_or_peers_is_not_clipped(self):
        for source in [sales({(0, 5): 10}, towns=3), sales({(0, 5): 10}, months=8)]:
            audit = run_rate_parent.adjustments(source)
            self.assertTrue(audit['raw_kl'].equals(audit['adjusted_kl']))
        sparse = sales({(0, 5): 10}).filter(~((pl.col('area_name (city)') == 'T00') & (pl.col('Month-Year') > '2025-06-01')))
        self.assertTrue(run_rate_parent.adjustments(sparse).filter(pl.col('town') == 'T00')['reason'].eq('insufficient_history').all())

    def test_zero_series_and_missing_neighbor_are_preserved(self):
        zero = sales().with_columns(pl.lit('0').alias('sec_vol_kl_mth (billwise)'))
        self.assertEqual(run_rate_parent.transform(zero)['avg_sec'].sum(), 0)
        self.assertTrue(run_rate_parent.adjustments(zero)['reason'].eq('zero_preserved').all())
        source = sales({(0, 5): 10}).filter(~((pl.col('area_name (city)') == 'T00') & (pl.col('Month-Year') == '2025-05-01')))
        peak = run_rate_parent.adjustments(source).filter((pl.col('town') == 'T00') & (pl.col('month') == date(2025, 6, 1)))
        self.assertEqual(peak['reason'][0], 'ambiguous_peak_preserved')
        self.assertEqual(peak['raw_kl'][0], peak['adjusted_kl'][0])

    def test_zero_low_negative_sales_are_not_raised(self):
        source = sales().with_columns(
            pl.when((pl.col('area_name (city)') == 'T00') & (pl.col('Month-Year') == '2025-03-01'))
            .then(pl.lit('-10')).when((pl.col('area_name (city)') == 'T00') & (pl.col('Month-Year') == '2025-04-01'))
            .then(pl.lit('0.001')).otherwise(pl.col('sec_vol_kl_mth (billwise)')).alias('sec_vol_kl_mth (billwise)'))
        audit = run_rate_parent.adjustments(source)
        self.assertTrue(audit['raw_kl'].equals(audit['adjusted_kl']))
        self.assertEqual(audit.filter((pl.col('town') == 'T00') & (pl.col('month') == date(2025, 3, 1)))['adjusted_kl'][0], 0)

    def test_aggregate_before_winsorization_raw_history_is_not_modified(self):
        source = sales({(0, 5): 10})
        split = pl.concat([source.with_columns((pl.col('sec_vol_kl_mth (billwise)').cast(pl.Float64) / 2).cast(pl.String)),
                           source.with_columns((pl.col('sec_vol_kl_mth (billwise)').cast(pl.Float64) / 2).cast(pl.String), pl.lit('2').alias('material_code'))])
        self.assertTrue(run_rate_parent.transform(source).equals(run_rate_parent.transform(split)))
        raw = sec_history.transform(source)
        self.assertEqual(raw['sec_kl'].max(), 480)
        self.assertTrue(run_rate_parent.transform(source.reverse()).equals(run_rate_parent.transform(source)))

    def test_leave_one_out_median_matches_independent_python_for_odd_even_and_ties(self):
        import statistics
        for towns in [11, 12, 13]:
            source = sales(towns=towns, seasonal_month=-1).with_columns(
                pl.when(pl.col('Month-Year') == '2025-06-01')
                .then((pl.col('area_name (city)').str.slice(1).cast(pl.Float64) + 1).pow(2).cast(pl.String))
                .otherwise(pl.col('sec_vol_kl_mth (billwise)')).alias('sec_vol_kl_mth (billwise)'))
            audit = run_rate_parent.adjustments(source).filter(pl.col('month') == date(2025, 6, 1))
            ratios = [(t + 1) / 10 for t in range(towns)]
            for t, season in enumerate(audit['season_factor']):
                self.assertAlmostEqual(season, statistics.median(ratios[:t] + ratios[t + 1:]))

    def test_future_months_excluded(self):
        source = sales()
        result = run_rate_parent.source_through(source, date(2025, 8, 30))
        self.assertEqual(result['Month-Year'].n_unique(), 8)
        self.assertEqual(run_rate_parent.adjustments(result)['reason'].unique().to_list(), ['insufficient_history'])

    def test_zero_demand_consumes_nothing_and_does_not_export_infinity(self):
        current = report.transform(stock_frame([310, 320], [1., 2.], [0., 0.]), DAY)
        self.assertEqual(current['avg_sec_m'].to_list(), [0., 0.])
        self.assertEqual(current['doh'].null_count(), 2)
        self.assertEqual(current['consumable_kl'].sum(), 0)
        for metric in ['exposure_kl', 'rotation_req_kl', 'lnd_fc_kl']:
            self.assertEqual(current[metric].sum(), 3.)

    def test_adjusted_rates_flow_to_town_and_all_report_measures(self):
        source = sales({(0, 5): 10})
        rates = run_rate_parent.transform(source)
        adjusted = rates.filter(pl.col('town') == 'T00')['avg_sec'][0]
        original = run_rate_parent.raw_rates(source).filter(pl.col('town') == 'T00')['avg_sec'][0]
        self.assertLess(adjusted, original)
        self.assertTrue(run_rate_town.transform(rates).sort('town')['avg_sec_town'].equals(rates.sort('town')['avg_sec']))
        before = report.transform(stock_frame([310], [20.0], [original]), DAY)
        after = report.transform(stock_frame([310], [20.0], [adjusted]), DAY)
        self.assertGreater(after['doh'][0], before['doh'][0])
        self.assertLess(after['consumable_kl'][0], before['consumable_kl'][0])
        self.assertGreater(after['exposure_kl'][0], before['exposure_kl'][0])
        self.assertGreater(after['rotation_req_kl'][0], before['rotation_req_kl'][0])
        # A nearer expiry makes the liquidation capacity binding as well.
        near_before = report.transform(stock_frame([100], [20.0], [original]), DAY)
        near_after = report.transform(stock_frame([100], [20.0], [adjusted]), DAY)
        self.assertGreater(near_after['lnd_fc_kl'][0], near_before['lnd_fc_kl'][0])


class StockCohortTests(unittest.TestCase):
    def fixture(self):
        return pl.DataFrame({'report_date': ['2026-08-30'] * 5, 'Town': ['A', 'A', 'A', 'B', 'A'],
                             'Brand Name': ['X'] * 5, 'parent_material_code': ['10'] * 5,
                             'rsl_group': ['< 3M', '< 3M', '> 10M', '< 3M', '< 3M'],
                             'mitigation_status': ['Actionable'] * 4 + ['Good'],
                             'total_closing_stock_kl': [1., 2., 4., 5., 100.]})

    def test_status_cohort_includes_non_actionable_stock(self):
        rates = pl.DataFrame({'town': ['A', 'B'], 'parent_material_code': [10, 10], 'avg_sec': [2., 0.]})
        pairs = stock_pairs(self.fixture(), rates, date(2026, 8, 30), actionable_only=False)
        self.assertEqual(pairs['stock_kl'].sum(), 112)
        self.assertEqual(pairs.filter((pl.col('town') == 'A') & (pl.col('rsl') == '< 3M'))['stock_kl'][0], 103)
        self.assertEqual(pairs.height, 3)

    def test_multiple_batches_children_and_rsl_share_one_rate(self):
        rates = pl.DataFrame({'town': ['A', 'B'], 'parent_material_code': [10, 10], 'avg_sec': [2., 0.]})
        pairs = stock_pairs(self.fixture(), rates, date(2026, 8, 30))
        self.assertEqual(pairs.height, 3)
        self.assertEqual(pairs['stock_kl'].sum(), 12)
        self.assertEqual(pairs.filter(pl.col('town') == 'A')['rate'].to_list(), [2., 2.])
        self.assertEqual(pairs.filter(pl.col('town') == 'B')['rate'][0], 0)
        missing = stock_pairs(self.fixture(), rates.head(1), date(2026, 8, 30))
        self.assertEqual(missing['rate'].null_count(), 1)
        with self.assertRaises(ValueError):
            stock_pairs(self.fixture(), pl.concat([rates, rates]), date(2026, 8, 30))
        with self.assertRaises(ValueError):
            stock_pairs(self.fixture(), rates, date(2026, 7, 31))
        with self.assertRaises(ValueError):
            stock_pairs(self.fixture().with_columns(pl.lit(-1.).alias('total_closing_stock_kl')), rates, date(2026, 8, 30))


if __name__ == '__main__':
    unittest.main()

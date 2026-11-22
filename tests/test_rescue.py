"""Hand-computed examples for the Action plans pages: rescue 3M–6M, rebalance 6M–10M, loading-watch >10M."""
import unittest
from datetime import date

import polars as pl

from src.rescue import (
    LOADING_WATCH,
    PAGES,
    REBALANCE,
    RESCUE,
    batches,
    incumbent_trend,
    lots,
    previous_month_plans,
    remark_outcomes,
    saved_report,
)
from ui.rescue_data import build_pages

SAVED = ['report_date', 'batch_index', 'rsl_group', 'lnd_fc_kl', 'lnd_fc_bdt', 'action_plan', 'mitigation_status']


def saved(rows, legacy=False):
    names = SAVED[:5] + (['action_plan_remarks', 'mitigation_status_category'] if legacy else SAVED[5:])
    return saved_report(pl.DataFrame(rows, schema=dict.fromkeys(names, pl.String), orient='row'))


def current(rows):
    return pl.DataFrame(rows, schema={
        'batch_index': pl.String, 'Town': pl.String, 'Brand Name': pl.String, 'parent_material_code': pl.Int64,
        'Material Code': pl.Int64, 'material_desc': pl.String, 'rsl_group': pl.String,
        'total_closing_stock_kl': pl.Float64, 'exposure_kl': pl.Float64, 'exposure_bdt': pl.Float64,
        'lnd_fc_kl': pl.Float64, 'lnd_fc_bdt': pl.Float64}, orient='row')


class PreviousMonthPlanTest(unittest.TestCase):
    def test_latest_report_of_previous_calendar_month_with_legacy_columns(self):
        history = pl.concat([
            saved([('2026-07-15', 'a1-1-1', '3M - 6M', '1', '1', 'old', 'Actionable')], legacy=True),
            saved([('2026-07-31', 'a1-1-1', '3M - 6M', '1', '1', 'Sales Push', 'Actionable'),
                   ('2026-07-31', 'b1-1-1', '3M - 6M', '1', '1', '  ', None)], legacy=True),
            saved([('2026-08-30', 'a1-1-1', '3M - 6M', '1', '1', 'this month', 'Actionable')]),
        ])
        plans, lm_date = previous_month_plans(history, date(2026, 8, 30))
        self.assertEqual(lm_date, date(2026, 7, 31))
        self.assertEqual(dict(zip(plans['batch_index'], plans['lm_action_plan'], strict=True)),
                         {'a1-1-1': 'Sales Push', 'b1-1-1': None})

    def test_no_previous_month_gives_no_plans(self):
        history = saved([('2026-06-30', 'a1-1-1', '3M - 6M', '1', '1', 'x', 'Actionable')])
        plans, lm_date = previous_month_plans(history, date(2026, 8, 30))
        self.assertIsNone(lm_date)
        self.assertTrue(plans.is_empty())


class BatchesTest(unittest.TestCase):
    def test_band_exact_join_and_good_stock_is_not_a_plan(self):
        now = current([
            ('a-1-1', 'T1', 'B', 10, 10, 'Parent SKU', '3M - 6M', 2.0, 2.0, 20.0, 1.0, 10.0),
            ('b-1-1', 'T2', 'B', 10, 11, 'Child SKU', '3M - 6M', 3.0, 3.0, 30.0, 0.0, 0.0),
            ('c-1-1', 'T2', 'B', 10, 11, 'Child SKU', '3M - 6M', 1.0, 1.0, 10.0, 1.0, 10.0),
            ('d-1-1', 'T1', 'B', 10, 10, 'Parent SKU', '> 10M', 9.0, 0.0, 0.0, 0.0, 0.0),
        ])
        plans = pl.DataFrame({'batch_index': ['a-1-1', 'b-1-1'], 'lm_action_plan': ['Sales Push', 'RSL>10M_Good stock'],
                              'lm_mitigation_status': ['Actionable', 'RSL>10M_Good stock']})
        out = batches(now, plans).sort('batch_index')
        self.assertEqual(out['batch_index'].to_list(), ['a-1-1', 'b-1-1', 'c-1-1'])
        self.assertEqual(out['has_plan'].to_list(), [True, False, False])
        self.assertEqual(set(out['parent_desc']), {'Parent SKU'})

    def test_lots_pool_lnd_by_sku_and_region(self):
        rows = pl.DataFrame({'Town': ['T1', 'T2', 'T3'], 'Material Code': [1, 1, 1],
                             'lnd_fc_kl': [0.1, 0.2, 0.4], 'lnd_fc_bdt': [8_000.0, 13_000.0, 9_000.0]})
        regions = pl.DataFrame({'Town': ['T1', 'T2', 'T3'], 'Region Name': ['East', 'East', 'West']})
        self.assertEqual(lots(rows, regions), {'threshold_bdt': 20_000, 'lots': 2, 'big': 1, 'kl': 0.30000000000000004, 'bdt': 21_000.0})
        self.assertIsNone(lots(rows, None))


class OutcomeTest(unittest.TestCase):
    history = pl.concat([
        saved([
            ('2026-07-31', 'ta1-10.0-1.0', '3M - 6M', '10', '100', 'Sales push; QDD ongoing', 'Actionable'),
            ('2026-07-31', 'tb1-10.0-1.0', '3M - 6M', '0', '0', 'Sales Push', 'Actionable'),
            ('2026-07-31', 'tc1-10.0-1.0', '6M - 10M', '5', '50', 'Stock rotation Q2 & liquidation plan', 'Actionable'),
            ('2026-07-31', 'td1-10.0-1.0', '6M - 10M', '2', '20', 'Stock not available', 'Incorrect Stock'),
        ]),
        saved([
            # Same batch key, new price/value suffix: still the same batch.
            ('2026-08-30', 'ta1-12.0-1.1', '3M - 6M', '4', '40', None, None),
            ('2026-08-30', 'tc1-10.0-1.0', '3M - 6M', '6', '60', None, None),
            ('2026-08-30', 'te1-10.0-1.0', '3M - 6M', '100', '1000', None, None),
        ]),
    ])

    def test_incumbent_trend_excludes_new_and_zero_lnd_batches(self):
        out = incumbent_trend(self.history)
        self.assertEqual(out.select('cm_date', 'lm_kl', 'cm_kl', 'batches').rows(), [(date(2026, 8, 30), 10.0, 4.0, 1)])

    def test_remark_outcomes_keyword_groups(self):
        out, window = remark_outcomes(self.history)
        self.assertEqual(window, [date(2026, 8, 30)])
        got = {r['category']: (r['lm_kl'], r['cm_kl']) for r in out.iter_rows(named=True)}
        self.assertEqual(got, {'Push + TLO / QDD / 2+1': (10.0, 4.0), 'Liquidation MT / E-Com / RTM': (5.0, 6.0),
                               'Verify (Incorrect Stock)': (2.0, 0.0)})


class PageBandTest(unittest.TestCase):
    now = current([
        ('a-1-1', 'T1', 'B', 10, 10, 'Parent SKU', '3M - 6M', 2.0, 2.0, 20.0, 1.0, 10.0),
        ('b-1-1', 'T1', 'B', 10, 10, 'Parent SKU', '6M - 10M', 3.0, 3.0, 30.0, 0.0, 0.0),
        ('c-1-1', 'T2', 'B', 10, 11, 'Child SKU', '6M - 10M', 1.0, 1.0, 10.0, 1.0, 10.0),
        # >10M: stock 9 of which 4 is not sold before the 300-day line; stock 5 is all sold before it
        ('d-1-1', 'T1', 'B', 10, 10, 'Parent SKU', '> 10M', 9.0, 4.0, 40.0, 0.5, 5.0),
        ('e-1-1', 'T2', 'B', 10, 11, 'Child SKU', '> 10M', 5.0, 0.0, 0.0, 0.0, 0.0),
    ])
    plans = pl.DataFrame({'batch_index': ['b-1-1', 'd-1-1'], 'lm_action_plan': ['Sales Push', 'RSL>10M_Good stock'],
                          'lm_mitigation_status': ['Actionable', 'RSL>10M_Good stock']})

    def test_row_scope_per_page(self):
        got = {k: batches(self.now, self.plans, page).sort('batch_index')['batch_index'].to_list()
               for k, page in PAGES.items()}
        # >10M keeps only batches with exposure: e-1-1 sells entirely before 300 days RSL
        self.assertEqual(got, {'rescue': ['a-1-1'], 'rebalance': ['b-1-1', 'c-1-1'], 'loading-watch': ['d-1-1']})

    def test_good_stock_label_is_not_a_plan_on_loading_watch(self):
        out = batches(self.now, self.plans, LOADING_WATCH)
        self.assertEqual(out['has_plan'].to_list(), [False])
        self.assertEqual(batches(self.now, self.plans, REBALANCE).sort('batch_index')['has_plan'].to_list(), [True, False])

    def test_entering_next_band_and_remark_bands(self):
        self.assertEqual([(p.soon_days, p.soon_label) for p in PAGES.values()],
                         [(120, '<3M'), (210, '3M–6M'), (330, '6M–10M')])
        self.assertEqual([p.outcome_bands for p in PAGES.values()],
                         [('3M - 6M', '6M - 10M'), ('6M - 10M',), ('> 10M',)])
        self.assertEqual([p.rotation for p in PAGES.values()], [False, False, True])

    def test_band_outcomes_are_cohorts_of_that_band(self):
        history = pl.concat([
            saved([('2026-07-31', 'ta1-10.0-1.0', '3M - 6M', '10', '100', 'Sales push', 'Actionable'),
                   ('2026-07-31', 'tb1-10.0-1.0', '6M - 10M', '6', '60', 'Stock rotation', 'Actionable'),
                   ('2026-07-31', 'tc1-10.0-1.0', '> 10M', '8', '80', 'RSL>10M_Good stock', None),
                   ('2026-07-31', 'td1-10.0-1.0', '> 10M', '0', '0', None, None)]),
            saved([('2026-08-30', 'ta1-10.0-1.0', '3M - 6M', '9', '90', None, None),
                   ('2026-08-30', 'tb1-10.0-1.0', '6M - 10M', '5', '50', None, None),
                   ('2026-08-30', 'tc1-10.0-1.0', '> 10M', '6', '60', None, None),
                   ('2026-08-30', 'td1-10.0-1.0', '> 10M', '3', '30', None, None)]),
        ])
        trend = {k: incumbent_trend(history, p).select('lm_kl', 'cm_kl', 'batches').rows() for k, p in PAGES.items()}
        self.assertEqual(trend, {'rescue': [(10.0, 9.0, 1)], 'rebalance': [(6.0, 5.0, 1)], 'loading-watch': [(8.0, 6.0, 1)]})
        outcomes = {k: {r['category']: (r['lm_kl'], r['cm_kl']) for r in remark_outcomes(history, p)[0].iter_rows(named=True)}
                    for k, p in PAGES.items()}
        self.assertEqual(outcomes['rescue'], {'Sales push': (10.0, 9.0), 'Stock rotation': (6.0, 5.0)})
        self.assertEqual(outcomes['rebalance'], {'Stock rotation': (6.0, 5.0)})
        self.assertEqual(outcomes['loading-watch'], {'Other': (8.0, 6.0)})
        self.assertEqual(RESCUE.outcome_bands, ('3M - 6M', '6M - 10M'))


class PagePayloadTest(unittest.TestCase):
    def test_rotation_column_only_on_loading_watch_and_batch_files(self):
        import tempfile
        from pathlib import Path
        # report_date 46264 is 2026-08-30
        report = pl.DataFrame({
            'batch_index': ['a-1-1', 'b-1-1', 'c-1-1', 'd-1-1'], 'report_date': [46264] * 4,
            'Town': ['T1'] * 4, 'Brand Name': ['B'] * 4, 'parent_material_code': [10] * 4, 'Material Code': [10] * 4,
            'material_desc': ['SKU'] * 4, 'Batch code': ['1'] * 4, 'rsl_group': ['3M - 6M', '6M - 10M', '> 10M', '> 10M'],
            'rsl_days': [150, 250, 400, 420], 'total_closing_stock_kl': [2.0, 3.0, 9.0, 5.0],
            'exposure_kl': [2.0, 3.0, 4.0, 0.0], 'exposure_bdt': [20.0, 30.0, 40.0, 0.0],
            'lnd_fc_kl': [1.0, 1.0, 1.0, 0.0], 'lnd_fc_bdt': [10.0, 10.0, 10.0, 0.0],
            'rotation_req_kl': [2.0, 3.0, 1.5, 0.0], 'rotation_req_bdt': [20.0, 30.0, 15.0, 0.0],
        })
        history = [pl.DataFrame({'report_date': ['2026-07-31'], 'batch_index': ['b-1-1'], 'rsl_group': ['6M - 10M'],
                                 'lnd_fc_kl': ['1'], 'lnd_fc_bdt': ['1'], 'action_plan': ['Sales Push'],
                                 'mitigation_status': ['Actionable']})]
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            pages = build_pages(report, history, None, out)
            self.assertEqual(sorted(p.name for p in out.iterdir()),
                             ['loading_watch_batches.parquet', 'rebalance_batches.parquet', 'rescue_batches.parquet'])
            self.assertEqual(pl.read_parquet(out / 'loading_watch_batches.parquet')['batch_index'].to_list(), ['c-1-1'])
            self.assertEqual(pl.read_parquet(out / 'rebalance_batches.parquet')['has_plan'].to_list(), [True])
        lw = pages['loading-watch']
        self.assertEqual([c for c in lw['cols'] if c.startswith('rot_')], ['rot_kl', 'rot_bdt'])
        self.assertEqual([dict(zip(lw['cols'], r, strict=True))['rot_kl'] for r in lw['rows']], [1.5])
        self.assertEqual(lw['rows'][0][lw['cols'].index('exp_kl')] - lw['rows'][0][lw['cols'].index('rot_kl')], 2.5)
        for key in ('rescue', 'rebalance'):
            self.assertNotIn('rot_kl', pages[key]['cols'])
            self.assertEqual(len(pages[key]['rows']), 1)
        self.assertEqual({k: v['page']['label'] for k, v in pages.items()},
                         {'rescue': '3M–6M', 'rebalance': '6M–10M', 'loading-watch': '>10M'})


if __name__ == '__main__':
    unittest.main()

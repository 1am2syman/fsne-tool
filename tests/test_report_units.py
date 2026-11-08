"""Value and volume must use independent saved-report facts."""
import unittest
from datetime import date

import polars as pl

from src.brand_status import aggregate, normalise
from src.movement import transform_parent_series, transform_series
from ui.status_data import payload


class ReportUnitsTest(unittest.TestCase):
    def test_movement_volume_and_parent_conservation(self):
        h = pl.DataFrame({
            'report_date': [date(2026, 8, 24), date(2026, 9, 21), date(2026, 9, 21)],
            'batch_index': ['a-1-1', 'a-2-2', 'b-1-1'],
            'Brand Name': ['X'] * 3, 'status': ['Actionable'] * 3,
            'remarks': pl.Series([None] * 3, dtype=pl.String), 'rsl_group': ['> 10M'] * 3,
            'material_code': ['10'] * 3, 'parent_code': ['10'] * 3,
            'material_desc': ['Parent'] * 3,
            'total_closing_stock_bdt': [100.0, 80.0, 20.0],
            'total_closing_stock_kl': [10.0, 4.0, 1.0],
        })
        value = transform_series(h).filter(pl.col('brand') == 'X').row(0, named=True)
        volume = transform_series(h, stock_column='total_closing_stock_kl').filter(pl.col('brand') == 'X').row(0, named=True)
        self.assertEqual(volume['lm_stock_bdt'], 10)
        self.assertEqual(volume['cm_no_new_bdt'], 4)
        self.assertEqual(volume['cm_new_bdt'], 1)
        self.assertAlmostEqual(volume['reduction_pct'], 0.6)
        self.assertAlmostEqual(value['reduction_pct'], 0.2)
        parent = transform_parent_series(h, rsl=True, stock_column='total_closing_stock_kl').row(0, named=True)
        for key in ['lm_stock_bdt', 'cm_no_new_bdt', 'cm_new_bdt', 'reduced_bdt']:
            self.assertEqual(parent[key], volume[key])

    def test_status_volume_uses_kl_not_price_conversion(self):
        source = pl.DataFrame({
            'report_date': ['2026-09-21'] * 2, 'Town': ['A'] * 2,
            'Brand Name': ['X'] * 2, 'parent_material_code': ['10'] * 2,
            'Material Code': ['10', '11'], 'material_desc': ['Parent', 'Child'],
            'rsl_group': ['> 10M'] * 2,
            'total_closing_stock_bdt': ['100', '900'],
            'exposure_bdt': ['20', '90'], 'lnd_fc_bdt': ['10', '45'],
            'total_closing_stock_kl': ['2', '3'],
            'exposure_kl': ['1', '0.5'], 'lnd_fc_kl': ['0.4', '0.1'],
        })
        data = payload(aggregate(normalise(source, 'kl')), [date(2026, 9, 1)])
        self.assertEqual(data['total']['m'][0], [[5.0, 1.5, 0.5]])
        parent = data['total']['kids'][0]['kids'][0]['kids'][0]
        self.assertEqual(parent['m'], data['total']['m'])

"""Independent aggregation examples for brand-status."""
import unittest
from datetime import date

import polars as pl

from src.brand_status import aggregate, normalise
from ui.status_data import payload


def frame(rows):
    return pl.DataFrame(rows, schema=['report_date', 'Town', 'Brand Name', 'parent_material_code', 'Material Code', 'material_desc', 'rsl_group', 'total_closing_stock_bdt', 'exposure_bdt', 'lnd_fc_bdt'], orient='row')


class BrandStatusTest(unittest.TestCase):
    def test_conservation_latest_snapshot_and_hierarchy(self):
        source = frame([
            ('2026-08-01', 'A', 'X', '10', '10', 'Parent', '> 10M', '999', '999', '999'),
            ('2026-08-24', 'A', 'X', '10', '10', 'Parent', '> 10M', '100', '20', '10'),
            ('2026-08-24', 'A', 'X', '10', '11', 'Child', '> 10M', '300', '30', '5'),
            ('2026-08-24', 'A', 'Y', '20', '20', 'Other', '< 3M', '50', '40', '20'),
            ('2026-09-21', 'B', 'X', '10', '11', 'Child', '> 10M', '200', '10', '5'),
        ])
        out = aggregate(normalise(source))
        data = payload(out, [date(2026, 8, 1), date(2026, 9, 1)])
        self.assertEqual(data['total']['m'][0], [[50.0, 40.0, 20.0], [400.0, 50.0, 15.0]])
        a, b = data['total']['kids']
        self.assertIsNone(a['m'][1])
        self.assertIsNone(b['m'][0])
        parent = a['kids'][0]['kids'][0]
        self.assertEqual(parent['name'], 'Parent')
        self.assertEqual(parent['m'][0][1][1] / parent['m'][0][1][0], 0.125)
        for n in [data['total'], a, b, *a['kids'], *b['kids']]:
            for i, cs in enumerate(n['m']):
                if cs is None:
                    continue
                for g, c in enumerate(cs):
                    for metric, amount in enumerate(c):
                        self.assertAlmostEqual(amount, sum(k['m'][i][g][metric] for k in n['kids'] if k['m'][i] is not None))

    def test_fallback_and_unknown_group(self):
        source = frame([('2026-09-21', ' A ', 'X', None, '12', None, None, '0', '0', '0')])
        data = payload(aggregate(normalise(source)), [date(2026, 9, 1)])
        self.assertEqual(data['groups'], ['Unknown'])
        town = data['total']['kids'][0]
        self.assertEqual(town['name'], 'A')
        parent = town['kids'][0]['kids'][0]
        self.assertEqual(parent['code'], '12')
        self.assertEqual(parent['name'], '12')
        self.assertEqual(data['total']['m'][0], [[0, 0, 0]])

    def test_bad_amount_fails_instead_of_silently_zeroing(self):
        source = frame([('2026-09-21', 'A', 'X', '1', '1', 'P', '> 10M', 'bad', '0', '0')])
        with self.assertRaises(pl.exceptions.InvalidOperationError):
            normalise(source)


if __name__ == '__main__':
    unittest.main()

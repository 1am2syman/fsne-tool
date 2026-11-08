"""Overlay facts reconcile to pipeline rates and preserve missing/zero provenance."""
import unittest

import polars as pl

from src.run_rate_parent import adjustments, rates_from_adjustments
from tests.test_run_rates import sales
from ui.doh_data import FIELDS, audit_payload


class AuditPayloadTests(unittest.TestCase):
    def test_actual_traces_reconstruct_rates_and_seasonal_allowances(self):
        audit = adjustments(sales({(0, 5): 10}))
        keys = pl.DataFrame({'town': ['T00', 'T01'], 'parent_material_code': [10, 10]})
        payload = audit_payload(audit, keys)
        self.assertEqual(len(payload['months']), 12)
        self.assertEqual(len(payload['traces']), 2)
        rates = rates_from_adjustments(audit)
        for trace in payload['traces']:
            raw = sum(row[FIELDS.index('raw_kl')] for row in trace['m'].values())
            adjusted = sum(row[FIELDS.index('adjusted_kl')] for row in trace['m'].values())
            expected = rates.filter(pl.col('town') == trace['t'])['avg_sec'][0]
            self.assertAlmostEqual(adjusted / 12, expected)
            if trace['t'] == 'T00':
                self.assertGreater(raw, adjusted)
                self.assertEqual(trace['m']['5'][FIELDS.index('reason')], 'winsorized')
            else:
                self.assertEqual(raw, adjusted)
                self.assertTrue(trace['m']['5'][FIELDS.index('seasonal_supported')])

    def test_missing_month_is_not_fabricated_observed_zero(self):
        source = sales().filter(~((pl.col('area_name (city)') == 'T00') & (pl.col('Month-Year') == '2025-03-01')))
        audit = adjustments(source)
        keys = pl.DataFrame({'town': ['T00', 'missing'], 'parent_material_code': [10, 10]})
        payload = audit_payload(audit, keys)
        self.assertEqual(len(payload['months']), 12)
        self.assertEqual(len(payload['traces']), 1)
        self.assertNotIn('2', payload['traces'][0]['m'])

    def test_empty_selection_still_explains_global_denominator(self):
        audit = adjustments(sales())
        keys = pl.DataFrame(schema={'town': pl.String, 'parent_material_code': pl.Int64})
        payload = audit_payload(audit, keys)
        self.assertEqual(payload['traces'], [])
        self.assertEqual(len(payload['months']), 12)


if __name__ == '__main__':
    unittest.main()

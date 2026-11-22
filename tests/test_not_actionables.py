"""Snapshot selection and accepted-plan behavior of the Not-actionables report."""

import http.client
import io
import json
import runpy
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import polars as pl

from server.app import make_server
from server.runs import Store
from src.not_actionables import cohort, monthly
from ui.not_actionables_data import COLS, build_payload
from ui.render import extract_legacy


def saved(snapshot, statuses, *, legacy=False):
    size = len(statuses)
    frame = pl.DataFrame({
        'report_date': [snapshot] * size, 'Expiry Date': [date(2027, 3, 1)] * size,
        'batch_index': [f'town-sku-{i}-10-1' for i in range(size)],
        'Town': ['Town'] * size, 'Brand Name': ['Brand'] * size,
        'parent_material_code': ['10'] * size, 'Material Code': [str(10 + i) for i in range(size)],
        'material_desc': ['Parent SKU', *[f'Child SKU {i}' for i in range(1, size)]],
        'Batch code': [f'Batch {i}' for i in range(size)],
        'rsl_group': [['< 3M', '3M - 6M', '6M - 10M', '> 10M'][i % 4] for i in range(size)],
        'rsl_days': [60.0 + i * 100 for i in range(size)],
        'total_closing_stock_bdt': [100.0 * (i + 1) for i in range(size)],
        'total_closing_stock_kl': [1.0 * (i + 1) for i in range(size)],
        'exposure_bdt': [80.0 * (i + 1) for i in range(size)],
        'exposure_kl': [0.8 * (i + 1) for i in range(size)],
        'lnd_fc_bdt': [60.0 * (i + 1) for i in range(size)],
        'lnd_fc_kl': [0.6 * (i + 1) for i in range(size)],
        'mitigation_status': statuses, 'action_plan': ['L&D risk'] * size,
    })
    return frame.rename({'mitigation_status': 'mitigation_status_category', 'action_plan': 'action_plan_remarks'}) if legacy else frame


def rows(payload, month=-1):
    return [dict(zip(payload['cols'], row, strict=True)) for row in payload['months'][month]['rows']]


class SnapshotTests(unittest.TestCase):
    def test_exact_status_across_every_band_with_case_whitespace_and_legacy_names(self):
        statuses = ['\u00a0Not actionable\t', 'NOT ACTIONABLE', 'not actionable', 'Not actionable',
                    'Actionable', 'Incorrect Stock', 'RSL>10M_Good stock', '', None, 'Not actionable - review']
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                frame = saved(date(2026, 8, 31), statuses, legacy=legacy).drop(
                    'exposure_bdt', 'exposure_kl', 'lnd_fc_bdt', 'lnd_fc_kl')
                data = build_payload([frame])
                actual = rows(data)
                self.assertEqual([r['batch'] for r in actual], ['Batch 3', 'Batch 2', 'Batch 1', 'Batch 0'])
                self.assertEqual({r['rsl'] for r in actual}, {'< 3M', '3M - 6M', '6M - 10M', '> 10M'})
                self.assertEqual(sum(r['stock_bdt'] for r in actual), 1000)
                self.assertEqual(sum(r['stock_kl'] for r in actual), 10)
                self.assertNotIn('exp_bdt', data['cols'])
                self.assertNotIn('lnd_bdt', data['cols'])

    def test_latest_whole_snapshot_wins_even_when_its_cohort_is_empty(self):
        data = build_payload([
            saved(date(2026, 7, 31), ['Not actionable'], legacy=True),
            saved(date(2026, 8, 15), ['Not actionable']),
            saved(date(2026, 8, 31), ['Actionable']),
        ])
        self.assertEqual([m['date'] for m in data['months']], ['2026-07-31', '2026-08-31'])
        self.assertEqual(len(rows(data, 0)), 1)
        self.assertEqual(rows(data), [])

    def test_own_snapshot_prices_and_remarks_are_preserved(self):
        july = saved(date(2026, 7, 31), ['Not actionable'])
        august = saved(date(2026, 8, 31), ['Not actionable']).with_columns(
            pl.lit(777.77).alias('total_closing_stock_bdt'), pl.lit('Claim; warehouse review').alias('action_plan'))
        data = build_payload([july, august])
        self.assertEqual(rows(data, 0)[0]['stock_bdt'], 100)
        self.assertEqual(rows(data)[0]['stock_bdt'], 777.77)
        self.assertEqual(rows(data)[0]['plan'], 'Claim; warehouse review')
        self.assertEqual(rows(data)[0]['stock_kl'], rows(data, 0)[0]['stock_kl'])

    def test_full_population_and_parent_description_outside_cohort(self):
        frame = saved(date(2026, 8, 31), ['Actionable', *(['Not actionable'] * 405)])
        data = build_payload([frame])
        self.assertEqual(len(rows(data)), 405)
        self.assertEqual({r['pdesc'] for r in rows(data)}, {'Parent SKU'})
        self.assertEqual(data['cols'], COLS)

    def test_duplicate_or_blank_batch_keys_fail_instead_of_double_counting(self):
        frame = saved(date(2026, 8, 31), ['Not actionable', 'Not actionable'])
        for value in ('duplicate', ' ', None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'batch_index'):
                monthly([frame.with_columns(pl.lit(value).alias('batch_index'))])
        # Positive control: the same batch can occur once in each different month.
        self.assertEqual(cohort(monthly([frame, frame.with_columns(pl.lit(date(2026, 7, 31)).alias('report_date'))])).height, 4)


class RunLifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='fsne-not-actionables-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store(self.root / 'runs', self.root / 'settled', start_worker=False)
        self.run_id = self.store.create('Not actionable lifecycle')['id']
        self.store.update(self.run_id, status='ok', report_date='2026-08-31')
        self.run_dir = self.store.path(self.run_id)
        (self.run_dir / 'history').mkdir()
        (self.run_dir / 'output').mkdir()
        self.july = saved(date(2026, 7, 31), ['Not actionable', 'Actionable'], legacy=True)
        self.august = saved(date(2026, 8, 31), ['Not actionable', 'Actionable'])
        for name, frame in [('saved-0000', self.july), ('snapshot', self.august)]:
            frame.write_excel(self.run_dir / 'history' / (name + '.xlsx'))
            frame.write_parquet(self.run_dir / 'history' / (name + '.parquet'))
        self.payload = {'SERIES': {'doh': {}}, 'STATUS': {'labels': ['Jul 2026', 'Aug 2026']}}
        (self.run_dir / 'data.json').write_text(json.dumps(self.payload))

    def test_existing_run_loads_full_report_from_private_parquet_and_caches_it(self):
        # Excel is deliberately stale: Parquet must remain the authority.
        self.august.with_columns(pl.lit('Actionable').alias('mitigation_status')).write_excel(self.run_dir / 'history/snapshot.xlsx')
        before = (self.run_dir / 'history/snapshot.parquet').read_bytes()
        server = make_server(self.store, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        self.addCleanup(conn.close)
        conn.request('GET', '/?run=' + self.run_id)
        response = conn.getresponse()
        html = response.read().decode()
        self.assertEqual(response.status, 200)
        actual = extract_legacy(html)['NOTACTIONABLES']
        self.assertEqual([r['batch'] for r in rows(actual)], ['Batch 0'])
        self.assertEqual(json.loads((self.run_dir / 'data.json').read_text())['NOTACTIONABLES'], actual)
        self.assertEqual((self.run_dir / 'history/snapshot.parquet').read_bytes(), before)
        self.assertIn('Pre-action plan view', html)
        # A second request reads the cached projection without reloading history.
        with patch('server.app.read_raw_reports', side_effect=AssertionError('Cached view should be reused')):
            conn.request('GET', '/?run=' + self.run_id)
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            response.read()

    def test_accepted_plan_rebuilds_cohort_but_preserves_report_and_prior_month(self):
        report = self.august.drop('mitigation_status', 'action_plan').with_columns(
            (pl.col(c) - date(1899, 12, 30)).dt.total_days().alias(c) for c in ('report_date', 'Expiry Date'))
        report.write_parquet(self.run_dir / 'output/inferred_report.parquet')
        for name in ('movement_series', 'movement_parents'):
            pl.DataFrame({'placeholder': [1]}).write_parquet(self.run_dir / 'output' / (name + '.parquet'))
        original = (self.run_dir / 'output/inferred_report.parquet').read_bytes()
        returned = self.august.with_columns(pl.Series('mitigation_status', ['Actionable', ' Not actionable ']))
        returned.write_excel(self.root / 'returned.xlsx')
        content = (self.root / 'returned.xlsx').read_bytes()
        checked = self.store.cycle.check(self.run_id, 'returned.xlsx', io.BytesIO(content), len(content))
        real_run = subprocess.run

        def calculate(command, **kwargs):
            if command[1] != 'ui/build.py':
                return real_run(command, **kwargs)
            # Run the real frozen dashboard builder in-process; unrelated rescue
            # and Sankey views are isolated so the test needs no sales workbook.
            with patch.object(sys, 'argv', command[1:]), \
                 patch('ui.rescue_data.build_pages', return_value={}), \
                 patch('src.evolution.build_all', return_value={'cached': True}):
                runpy.run_path('ui/build.py', run_name='__main__')
            return subprocess.CompletedProcess(command, 0, '', '')

        with patch('server.action_plans.subprocess.run', side_effect=calculate):
            meta = self.store.cycle.accept(self.run_id, checked['token'], 'test')
        data = json.loads((self.run_dir / 'data.json').read_text())['NOTACTIONABLES']
        self.assertEqual([r['batch'] for r in rows(data)], ['Batch 1'])
        self.assertEqual([r['batch'] for r in rows(data, 0)], ['Batch 0'])
        self.assertEqual(rows(data)[0]['stock_bdt'], 200)
        self.assertEqual(meta['plan_state'], 'plan_uploaded')
        self.assertEqual((self.run_dir / 'output/inferred_report.parquet').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()

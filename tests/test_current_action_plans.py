"""Current accepted plans on frozen action-plan pages, including upgrades of existing runs."""
import http.client
import json
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import polars as pl

from server.app import make_server
from server.runs import Store
from ui.render import extract_legacy, render
from ui.rescue_data import build_pages


def snapshot(day=date(2026, 10, 31)):
    return pl.DataFrame({
        'report_date': [day] * 6, 'Expiry Date': [date(2027, 3, 31)] * 6,
        'batch_index': [f'town-sku-{i}-10-1' for i in range(6)],
        'Town': ['Town'] * 6, 'Brand Name': ['Brand'] * 6,
        'parent_material_code': [10] * 6, 'Material Code': [10] * 6,
        'material_desc': ['SKU'] * 6, 'Batch code': [str(i) for i in range(6)],
        'rsl_group': ['3M - 6M'] * 2 + ['6M - 10M'] * 2 + ['> 10M'] * 2,
        'rsl_days': [110, 150, 200, 250, 320, 400],
        'total_closing_stock_kl': [1., 3.] * 3, 'total_closing_stock_bdt': [300., 100.] * 3,
        'exposure_kl': [1., 3.] * 3, 'exposure_bdt': [300., 100.] * 3,
        'lnd_fc_kl': [.5, 1.] * 3, 'lnd_fc_bdt': [150., 30.] * 3,
        'rotation_req_kl': [.2, .3] * 3, 'rotation_req_bdt': [20., 30.] * 3,
        'action_plan': ['October push', None, 'October rotation', '  ', 'RSL>10M_Good stock', 'October liquidation'],
        'mitigation_status': ['Actionable', None, 'Actionable', None, 'RSL>10M_Good stock', 'Actionable'],
    })


def frozen(frame):
    return frame.drop('action_plan', 'mitigation_status').with_columns(
        (pl.col(c) - date(1899, 12, 30)).dt.total_days().alias(c) for c in ('report_date', 'Expiry Date'))


def previous():
    return snapshot(date(2026, 9, 30)).with_columns(
        pl.lit('September push').alias('action_plan'), pl.lit('Actionable').alias('mitigation_status'))


def page_rows(page):
    return [dict(zip(page['cols'], row, strict=True)) for row in page['rows']]


class CurrentPlansTests(unittest.TestCase):
    def test_current_snapshot_entries_are_distinct_from_last_month_on_every_page(self):
        current = snapshot()
        pages = build_pages(frozen(current), [previous(), current])
        expected = {'rescue': [('October push', True), (None, False)],
                    'rebalance': [('October rotation', True), (None, False)],
                    'loading-watch': [('RSL>10M_Good stock', False), ('October liquidation', True)]}
        for key, page in pages.items():
            rows = page_rows(page)
            with self.subTest(page=key):
                self.assertEqual([(r['current_plan'], r['current_has_plan']) for r in rows], expected[key])
                self.assertEqual([r['plan'] for r in rows], ['September push'] * 2)
                self.assertTrue(all(r['has_plan'] for r in rows))
                self.assertEqual(page['date'], '2026-10-31')
                self.assertEqual(page['lm_label'], 'Sep 2026')

    def test_no_current_snapshot_never_substitutes_last_month(self):
        pages = build_pages(frozen(snapshot()), [previous()])
        for page in pages.values():
            self.assertTrue(all(r['current_plan'] is None and not r['current_has_plan'] for r in page_rows(page)))

    def test_excel_serial_snapshot_dates_keep_current_plans(self):
        for dtype in (pl.Float64, pl.String):
            current = snapshot().with_columns(
                (pl.col('report_date') - date(1899, 12, 30)).dt.total_days().cast(dtype))
            with self.subTest(dtype=dtype):
                pages = build_pages(frozen(snapshot()), [previous(), current])
                rows = page_rows(pages['rescue'])
                self.assertEqual([r['current_plan'] for r in rows], ['October push', None])

    def test_current_snapshot_rejects_duplicate_and_blank_keys(self):
        for key in ('duplicate', ' ', None):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'batch_index'):
                build_pages(frozen(snapshot()), [previous(), snapshot().with_columns(
                    pl.lit(key, dtype=pl.String).alias('batch_index'))])

    def test_snapshot_join_uses_exact_batch_key_and_preserves_page_population(self):
        current = snapshot().reverse().with_columns(
            pl.when(pl.col('batch_index') == 'town-sku-0-10-1').then(pl.lit('town-sku-0-99-1'))
            .otherwise(pl.col('batch_index')).alias('batch_index'))
        pages = build_pages(frozen(snapshot()), [previous(), current])
        rows = page_rows(pages['rescue'])
        self.assertEqual([r['batch_index'] for r in rows], ['town-sku-0-10-1', 'town-sku-1-10-1'])
        self.assertEqual([r['current_plan'] for r in rows], [None, None])


class ExistingRunTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='fsne-current-plans-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store(self.root / 'runs', self.root / 'settled', start_worker=False)
        self.run_dir_id = self.store.create('October plans')['id']
        self.store.update(self.run_dir_id, status='ok', report_date='2026-10-31', plan_state='plan_uploaded',
                          plan_upload={'revision': 'accepted-october'})
        self.run_dir = self.store.path(self.run_dir_id)
        (self.run_dir / 'history').mkdir()
        (self.run_dir / 'output').mkdir()
        frozen(snapshot()).write_parquet(self.run_dir / 'output/inferred_report.parquet')
        previous().write_parquet(self.run_dir / 'history/saved-0000.parquet')
        snapshot().write_parquet(self.run_dir / 'history/snapshot.parquet')
        # Deliberately stale Excel: accepted private Parquet remains authoritative.
        previous().write_excel(self.run_dir / 'history/snapshot.xlsx')
        pages = build_pages(frozen(snapshot()), [previous()])
        # Reproduce a cached payload made before current-plan columns existed.
        for page in pages.values():
            keep = [i for i, c in enumerate(page['cols']) if not c.startswith('current_')]
            page['rows'] = [[row[i] for i in keep] for row in page['rows']]
            page['cols'] = [page['cols'][i] for i in keep]
        self.data = {'ACTIONPLANS': pages, 'NOTACTIONABLES': {}, 'SERIES': {}, 'STATUS': {'labels': ['Oct 2026']}}
        (self.run_dir / 'data.json').write_text(json.dumps(self.data))

    def test_existing_accepted_run_upgrades_once_without_touching_frozen_files(self):
        preserved = {p: p.read_bytes() for p in [self.run_dir / 'history/snapshot.parquet',
                                                self.run_dir / 'history/snapshot.xlsx',
                                                self.run_dir / 'output/inferred_report.parquet']}
        server = make_server(self.store, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        self.addCleanup(conn.close)
        conn.request('GET', '/?run=' + self.run_dir_id)
        response = conn.getresponse()
        html = response.read().decode()
        self.assertEqual(response.status, 200)
        data = extract_legacy(html)
        rows = page_rows(data['ACTIONPLANS']['rescue'])
        self.assertEqual([r['current_plan'] for r in rows], ['October push', None])
        self.assertEqual([r['current_has_plan'] for r in rows], [True, False])
        self.assertIn('data-accepted="true"', html)
        self.assertEqual(json.loads((self.run_dir / 'data.json').read_text())['ACTIONPLANS'], data['ACTIONPLANS'])
        for path, content in preserved.items():
            self.assertEqual(path.read_bytes(), content)
        with patch('server.app.read_raw_reports', side_effect=AssertionError('Already cached')):
            conn.request('GET', '/?run=' + self.run_dir_id)
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            response.read()

    def test_current_coverage_requires_an_accepted_upload_even_after_settlement(self):
        for state, uploaded, expected in [('provisional', False, 'false'), ('plan_uploaded', True, 'true'),
                                           ('settled', True, 'true'), ('settled', False, 'false')]:
            meta = {'id': self.run_dir_id, 'label': 'October plans', 'plan_state': state,
                    'plan_upload': {'revision': 'october'} if uploaded else None}
            with self.subTest(state=state, uploaded=uploaded):
                self.assertIn(f'data-accepted="{expected}"', render(self.data, meta))


if __name__ == '__main__':
    unittest.main()

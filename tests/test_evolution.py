"""Evolution report scopes use the same graph builder and cache as the full page."""
from __future__ import annotations

import datetime as dt
import gzip
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import polars as pl

from server.app import make_server
from server.runs import Store
from src import evolution as evo


def history_rows() -> pl.DataFrame:
    rows = []
    for month, band in [(6, '6M - 10M'), (7, '3M - 6M'), (8, '3M - 6M')]:
        for batch, brand, parent, town, multiplier in [
            ('a', 'Brand A', '101', 'Dhaka', 1),
            ('b', 'Brand A', '102', 'Sylhet', 2),
            ('c', 'Brand B', '101', 'Dhaka', 4),
        ]:
            stock = multiplier * (1000 - month * 50)
            rows.append({'report_date': dt.date(2026, month, 28), 'batch_index': batch,
                         'Brand Name': brand, 'parent_material_code': parent, 'Town': town,
                         'Material Code': {'a': '1001', 'b': '1002', 'c': '1003'}[batch],
                         'material_desc': 'Pack ' + batch,
                         'rsl_group': band, 'mitigation_status': 'Actionable', 'action_plan': 'Sales push',
                         'total_closing_stock_bdt': stock, 'total_closing_stock_kl': stock / 100,
                         'exposure_bdt': stock, 'exposure_kl': stock / 100,
                         'lnd_fc_bdt': stock / 2, 'lnd_fc_kl': stock / 200})
    return pl.DataFrame(rows)


def opening(payload: dict, month: int) -> float:
    return sum(link['value'] for link in payload['links'] if link['source'] == f'month:M{month}')


class EvolutionScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = self.root / 'history'
        self.history.mkdir()
        self.raw = history_rows()
        self.raw.write_excel(self.history / 'snapshots.xlsx')
        self.prep = evo.Prepared(evo._normalise(self.raw), None)

    def test_rsl_is_applied_per_snapshot_and_row_dimensions_intersect(self):
        scope = self.prep.scoped({'brand': ['Brand A'], 'parent': ['101'], 'town': ['Dhaka'], 'rsl': ['3M–6M']})
        payload = evo.build_lens(scope, evo.LENS_BY_ID['exposure'], 'bdt')
        # The same batch was in 6–10M in June; filtering is per snapshot, not a cohort admission rule.
        self.assertEqual(opening(payload, 1), 0)
        self.assertEqual(opening(payload, 2), 650)
        self.assertEqual(opening(payload, 3), 600)

    def test_measure_and_kl_conversion_retain_the_identical_scope(self):
        scope = self.prep.scoped({'brand': ['Brand A'], 'parent': ['101'], 'rsl': ['3M–6M']})
        for lens, expected in [('exposure', 600), ('lnd', 300)]:
            with self.subTest(lens=lens):
                bdt = evo.build_lens(scope, evo.LENS_BY_ID[lens], 'bdt')
                kl = evo.build_lens(scope, evo.LENS_BY_ID[lens], 'kl')
                self.assertEqual(opening(bdt, 3), expected)
                self.assertAlmostEqual(opening(kl, 3) / kl['unit_div'], expected / 100)

    def test_as_of_removes_future_months_but_keeps_the_earlier_calendar(self):
        scope = self.prep.scoped({'rsl': ['3M–6M']}, dt.date(2026, 7, 30))
        self.assertEqual(scope.dates, [dt.date(2026, 6, 28), dt.date(2026, 7, 28)])
        self.assertEqual(scope.frame['j'].unique().to_list(), [1])
        payload = evo.build_lens(scope, evo.LENS_BY_ID['lnd'], 'bdt')
        self.assertEqual(len(payload['months']), 2)
        self.assertFalse(any(node.get('month_key') == 'M3' for node in payload['nodes']))
        self.assertEqual(opening(payload, 2), 2275)
        self.assertEqual(self.prep.months, 3)

    def test_catalog_cache_isolated_scopes_details_and_validation(self):
        out = self.root / 'graphs'
        catalog = evo.filter_options(self.history, None, out)
        self.assertEqual(catalog['rsl'], evo.RSL_ORDER)
        paths = []
        for as_of in ['2026-07-30', '2026-08-30']:
            with evo.cached_artifact(self.history, None, out, 'lnd', 'bdt', {'brand': ['Brand A'], 'rsl': ['3M–6M']}, as_of=as_of) as path:
                paths.append(path)
                payload = json.loads(gzip.decompress(path.read_bytes()))
                detail = json.loads(gzip.decompress(evo.artifact_path(path.parent, 'lnd', 'bdt', 'detail').read_bytes()))
                self.assertEqual(payload['as_of'], as_of)
                self.assertEqual(payload['filters']['rsl'], ['3M–6M'])
                self.assertEqual(set(detail['links']), {link['id'] for link in payload['links']})
                # Show data resolves lineages to brand · parent · town rows through these tables.
                lineages = detail['lineages']
                self.assertEqual(len(lineages['town']), len(lineages['brand']))
                self.assertEqual(len(lineages['parent']), len(lineages['brand']))
                self.assertTrue(all(0 <= i < len(detail['towns']) for i in lineages['town']))
                self.assertTrue(all(0 <= i < len(detail['parents']) for i in lineages['parent']))
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual(len(json.loads(gzip.decompress(paths[0].read_bytes()))['months']), 2)
        for filters, as_of in [({'rsl': ['wrong']}, None), ({}, 'not-a-date')]:
            with self.assertRaises(evo.InvalidFilter), evo.cached_artifact(self.history, None, out, 'lnd', 'bdt', filters, as_of=as_of):
                self.fail('invalid request was accepted')

    def test_upgrade_serves_new_graphs_without_deleting_locked_legacy_caches(self):
        out = self.root / 'graphs'
        out.mkdir()
        legacy = out / 'filtered' / 'old-scope'
        legacy.mkdir(parents=True)
        old_graph = legacy / 'exposure.bdt.json.gz'
        old_graph.write_bytes(b'old cache, not current graph data')
        (out / 'manifest.json').write_text('{"signature":"old","schema":14}')
        native_unlink = Path.unlink

        def deny_cache_deletion(path, *args, **kwargs):
            if path.suffix in {'.gz', '.parquet'}:
                raise PermissionError(13, 'Access is denied', str(path))
            return native_unlink(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as parsed, patch.dict('os.environ', {'FSNE_CACHE_DIR': parsed}):
            # Seed parsed history as if it had been read by the previous version.
            with patch.object(evo, 'SCHEMA', 14):
                evo.load_history(self.history)
            with patch.object(Path, 'unlink', deny_cache_deletion), patch.object(
                evo.shutil, 'rmtree', side_effect=PermissionError(13, 'Access is denied', str(legacy))
            ):
                for schema, expected in [(15, 1800), (16, 3600)]:
                    with patch.object(evo, 'SCHEMA', schema):
                        if schema == 16:
                            self.raw = self.raw.with_columns(pl.col('exposure_bdt') * 2)
                            self.raw.write_excel(self.history / 'snapshots.xlsx')
                            # History now retains an authoritative sibling record;
                            # update both formats when replacing this fixture.
                            self.raw.write_parquet(self.history / 'snapshots.parquet')
                        with evo.cached_artifact(self.history, None, out, 'exposure', 'bdt', {'brand': ['Brand A']}) as path:
                            payload = json.loads(gzip.decompress(path.read_bytes()))
                            self.assertEqual(opening(payload, 3), expected)
                            detail = evo.artifact_path(path.parent, 'exposure', 'bdt', 'detail')
                            self.assertEqual(set(json.loads(gzip.decompress(detail.read_bytes()))['links']),
                                             {link['id'] for link in payload['links']})
                            # A second request must reuse the new cache, never the stale one.
                            with evo.cached_artifact(self.history, None, out, 'exposure', 'bdt', {'brand': ['Brand A']}) as again:
                                self.assertEqual(again, path)
                self.assertEqual(old_graph.read_bytes(), b'old cache, not current graph data')

    def test_locked_cache_retention_and_timestamp_do_not_break_ready_graphs(self):
        out = self.root / 'graphs'
        evo.ensure(self.history, None, out, 'exposure', 'bdt', {'brand': ['Brand A']})
        with patch.object(evo, 'MAX_FILTER_SCOPES', 1), patch.object(
            evo.os, 'utime', side_effect=PermissionError('Directory timestamp denied')
        ), patch.object(evo.shutil, 'rmtree', side_effect=PermissionError('Old cache is open')):
            for brand in ['Brand B', 'Brand A']:
                path = evo.ensure(self.history, None, out, 'exposure', 'bdt', {'brand': [brand]})
                self.assertTrue(json.loads(gzip.decompress(path.read_bytes()))['nodes'])
        self.assertFalse(evo._ACTIVE_SCOPES)

    def test_required_permission_error_is_logged_and_explained_over_http(self):
        store = Store(self.root / 'runs', self.history, start_worker=False)
        server = make_server(store, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        self.addCleanup(conn.close)
        error = PermissionError(13, 'Access is denied', 'private-folder/locked-cache.tmp')
        with patch.object(evo, 'cached_artifact', side_effect=error), self.assertLogs('server.app', level='ERROR') as logged:
            conn.request('GET', '/api/evolution/exposure')
            response = conn.getresponse()
            self.assertEqual(response.status, 500)
            message = json.loads(response.read())['error']
            self.assertIn('command window', message)
            self.assertNotIn('private-folder', message)
            self.assertIn('locked-cache.tmp', '\n'.join(logged.output))
            self.assertIn('PermissionError', '\n'.join(logged.output))

    def test_warm_build_and_http_cache_share_fresh_generation(self):
        out = self.root / 'graphs'
        with tempfile.TemporaryDirectory() as parsed, patch.dict('os.environ', {'FSNE_CACHE_DIR': parsed}):
            first = evo.build_all(self.history, None, out, units=('bdt',))
            self.assertFalse(first['cached'])
            self.assertTrue(evo.build_all(self.history, None, out, units=('bdt',))['cached'])
            with patch.object(evo, '_write_payload', side_effect=AssertionError('Warm graph should be reused')):
                path = evo.ensure(self.history, None, out, 'exposure', 'bdt')
                self.assertEqual(opening(json.loads(gzip.decompress(path.read_bytes())), 3), 4200)
                self.assertIn('Brand A', evo.filter_options(self.history, None, out)['brand'])
            previous = path
            with patch.object(evo, 'SCHEMA', evo.SCHEMA + 1), patch.object(
                evo.shutil, 'rmtree', side_effect=PermissionError('Old graph is open')
            ):
                self.assertFalse(evo.build_all(self.history, None, out, units=('bdt',))['cached'])
                path = evo.ensure(self.history, None, out, 'exposure', 'bdt')
                self.assertNotEqual(previous, path)
                self.assertTrue(previous.exists())

    def test_required_write_failure_cannot_return_stale_graph_and_can_be_retried(self):
        out = self.root / 'graphs'
        previous = evo.ensure(self.history, None, out, 'exposure', 'bdt')
        with patch.object(evo, 'SCHEMA', evo.SCHEMA + 1):
            with patch.object(evo, '_write_gz', side_effect=PermissionError('New cache write denied')), \
                    self.assertRaisesRegex(PermissionError, 'New cache write denied'):
                evo.ensure(self.history, None, out, 'exposure', 'bdt')
            self.assertFalse(evo._ACTIVE_SCOPES)
            path = evo.ensure(self.history, None, out, 'exposure', 'bdt')
            self.assertNotEqual(path, previous)
            self.assertTrue(json.loads(gzip.decompress(path.read_bytes()))['nodes'])

    def test_active_graph_is_not_pruned_while_another_scope_is_built(self):
        out = self.root / 'graphs'
        with patch.object(evo, 'MAX_FILTER_SCOPES', 1):
            with evo.cached_artifact(self.history, None, out, 'exposure', 'bdt') as active:
                other = evo.ensure(self.history, None, out, 'exposure', 'bdt', {'brand': ['Brand A']})
                self.assertTrue(active.exists())
                self.assertTrue(other.exists())
            evo.ensure(self.history, None, out, 'exposure', 'bdt', {'brand': ['Brand B']})
            self.assertFalse(active.exists())

    def test_material_filter_uses_exact_child_code_and_preserves_measure(self):
        scope = self.prep.scoped({'material': ['1001'], 'brand': ['Brand A']})
        for unit, expected in [('bdt', 600), ('kl', 6000)]:
            payload = evo.build_lens(scope, evo.LENS_BY_ID['exposure'], unit)
            self.assertEqual(opening(payload, 3), expected)
        self.assertEqual(scope.frame['batch'].unique().to_list(), ['a'])

    def test_catalog_options_intersect_scope_and_report_date(self):
        out = self.root / 'graphs'
        catalog = evo.filter_options(self.history, None, out, {'brand': ['Brand A'], 'parent': ['101']}, as_of='2026-07-30')
        self.assertEqual(catalog['town'], ['Dhaka'])
        self.assertEqual(catalog['material'], ['1001'])
        self.assertEqual(catalog['material_labels']['1001'], 'Pack a · 1001')
        self.assertEqual(catalog['rsl'], ['6M–10M', '3M–6M'])
        empty = evo.filter_options(self.history, None, out, {'brand': ['Brand B'], 'parent': ['102']})
        self.assertEqual(empty['town'], [])
        early = evo.filter_options(self.history, None, out, as_of='2026-01-01')
        self.assertEqual(early['material'], [])
        with self.assertRaises(evo.InvalidFilter):
            evo.filter_options(self.history, None, out, as_of='bad-date')

    def test_empty_intersection_and_date_before_history_are_empty_graphs(self):
        for scope in [self.prep.scoped({'brand': ['Brand B'], 'parent': ['102']}),
                      self.prep.scoped({}, dt.date(2026, 1, 1))]:
            payload = evo.build_lens(scope, evo.LENS_BY_ID['exposure'], 'bdt')
            self.assertEqual(payload['nodes'], [])
            self.assertEqual(payload['links'], [])

    def test_http_passes_scope_to_main_and_detail_for_the_selected_run(self):
        store = Store(self.root / 'runs', self.history, start_worker=False)
        run = store.create('Evolution scope')
        run_history = store.path(run['id']) / 'history'
        run_history.mkdir(exist_ok=True)
        self.raw.write_excel(run_history / 'snapshots.xlsx')
        server = make_server(store, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        self.addCleanup(conn.close)
        query = '?unit=bdt&brand=Brand+A&rsl=3M%E2%80%936M&as_of=2026-07-30'
        with patch.object(evo, 'load_sales', return_value=None):
            for part in ['', '/detail']:
                conn.request('GET', f"/api/runs/{run['id']}/evolution/lnd{part}{query}")
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Content-Encoding'), 'gzip')
                payload = json.loads(gzip.decompress(response.read()))
                if not part:
                    self.assertEqual(payload['filters'], {'brand': ['Brand A'], 'rsl': ['3M–6M']})
                    self.assertEqual(len(payload['months']), 2)
                    self.assertEqual(opening(payload, 2), 975)
            for prefix in [f"/api/runs/{run['id']}/evolution/", '/api/evolution/']:
                conn.request('GET', prefix + 'filters?brand=Brand+A&parent=101&as_of=2026-07-30')
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                catalog = json.loads(response.read())
                self.assertEqual(catalog['town'], ['Dhaka'])
                self.assertEqual(catalog['material'], ['1001'])
                conn.request('GET', prefix + 'exposure?material=1001')
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                payload = json.loads(gzip.decompress(response.read()))
                self.assertEqual(opening(payload, 3), 600)
            conn.request('GET', f"/api/runs/{run['id']}/evolution/lnd?rsl=invalid")
            response = conn.getresponse()
            self.assertEqual(response.status, 400)
            self.assertIn('Unknown rsl', json.loads(response.read())['error'])


class EvolutionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = self.root / 'history'
        self.history.mkdir()
        templates = history_rows().filter(pl.col('report_date') == dt.date(2026, 6, 28)).to_dicts()
        rows = []
        for month, amount, rsl in [(6, 400, '>10M'), (7, 1000, '3M - 6M'), (8, 600, '<3M'), (9, 1200, '<3M')]:
            for template in templates:
                row = {**template, 'report_date': dt.date(2026, month, 28), 'rsl_group': rsl}
                stock = amount if row['batch_index'] == 'a' else 7000
                row.update(total_closing_stock_bdt=stock, total_closing_stock_kl=stock / 100,
                           exposure_bdt=stock, exposure_kl=stock / 100, lnd_fc_bdt=stock / 2, lnd_fc_kl=stock / 200)
                row['action_plan'] = 'Sales push and Rotation' if row['batch_index'] == 'a' else 'Sales push'
                if row['batch_index'] == 'a' and month == 9:
                    row['mitigation_status'] = 'Good stock'
                rows.append(row)
        self.raw = pl.DataFrame(rows)
        self.raw.write_excel(self.history / 'snapshots.xlsx')
        self.prep = evo.Prepared(evo._normalise(self.raw), None)
        self.filters = {'brand': ['Brand A'], 'parent': ['101'], 'town': ['Dhaka']}

    def graph(self, selection='category:M2:sales_push', *, lens='exposure', unit='bdt', rsl=False, cutoff=None):
        filters = {**self.filters, **({'rsl': ['3M–6M']} if rsl else {})}
        return evo.build_selection(self.prep.scoped({}, cutoff), evo.LENS_BY_ID[lens], unit, filters, selection)

    def assert_totals(self, payload, amounts):
        self.assertEqual(len(payload['months']), len(amounts))
        for j, expected in enumerate(amounts, 1):
            self.assertAlmostEqual(opening(payload, j), expected, places=2)
        nodes = {node['id']: node for node in payload['nodes']}
        for link in payload['links']:
            self.assertGreater(link['value'], 0)
            self.assertGreater(nodes[link['target']]['column'], nodes[link['source']]['column'])
            for side, field in [('source', 'target'), ('target', 'source')]:
                if nodes[link[side]]['kind'] == 'month':
                    covered = sum(tr['value'] for tr in payload['transitions'] if tr['junction'] == link[side] and tr[field] == link[field])
                    self.assertAlmostEqual(covered, link['value'], places=2)

    def test_category_uses_exact_blended_share_and_traverses_original_rsl_filter(self):
        payload = self.graph(rsl=True)
        self.assert_totals(payload, [200, 500, 300, 300])
        self.assertEqual(payload['selection']['value'], 500)
        self.assertEqual(payload['selection']['anchor'], 'M2')
        self.assertEqual(payload['selection']['measure'], 'Exposure')
        categories = {node['category'] for node in payload['nodes'] if node['kind'] == 'category'}
        self.assertEqual(categories, {'>10M', '3M–6M', '<3M'})
        self.assertEqual({row[0] for link in payload['links'] for row in link['brand_values']}, {'Brand A'})
        self.assertEqual(payload['metadata']['lens']['category_dimension'], 'rsl_group')
        # Positive control: admitting all rows of this batch would show 1,000 at the anchor and 1,200 later.
        full = evo.build_lens(self.prep.scoped(self.filters), evo.LENS_BY_ID['rsl'], 'bdt')
        self.assertEqual(opening(full, 2), 1000)
        self.assertEqual(opening(full, 4), 1200)

    def test_carry_and_exit_bands_select_only_their_partial_amount(self):
        carry = self.graph('category:M2:sales_push>month:M3')
        self.assert_totals(carry, [120, 300, 300, 300])
        self.assertEqual(carry['selection']['value'], 300)
        exit_graph = self.graph('category:M2:sales_push>outcome:M2:resolved')
        self.assert_totals(exit_graph, [80, 200, 0, 0])
        self.assertEqual(exit_graph['selection']['value'], 200)
        opening_band = self.graph('month:M2>category:M2:sales_push')
        self.assert_totals(opening_band, [200, 500, 300, 300])

    def test_report_bucket_exit_continues_in_next_rsl_while_real_reduction_exits(self):
        payload = self.graph('category:M2:sales_push>outcome:M2:resolved', rsl=True)
        self.assert_totals(payload, [200, 500, 300, 300])
        self.assertEqual(payload['selection']['value'], 500)
        exits = [link for link in payload['links'] if link['target'] == 'outcome:M2:resolved']
        self.assertEqual(sum(link['value'] for link in exits), 200)

    def test_added_band_distinguishes_actual_growth_from_entry_into_report_rsl(self):
        selection = 'outcome:M1:added>month:M2'
        self.assert_totals(self.graph(selection), [0, 600, 360, 360])
        self.assert_totals(self.graph(selection, rsl=True), [400, 1000, 600, 600])

    def test_measure_unit_and_report_cutoff_are_preserved(self):
        for lens, ratio in [('exposure', 1), ('lnd', .5)]:
            for unit, scale in [('bdt', 1), ('kl', 10)]:
                with self.subTest(lens=lens, unit=unit):
                    payload = self.graph(lens=lens, unit=unit, rsl=True, cutoff=dt.date(2026, 8, 30))
                    self.assert_totals(payload, [200 * ratio * scale, 500 * ratio * scale, 300 * ratio * scale])
                    self.assertEqual(payload['selection']['value'], 500 * ratio * scale)
                    self.assertEqual(payload['unit'], unit)

    def test_hover_lineage_floor_does_not_shrink_selected_quantities(self):
        with patch.dict(evo.LINEAGE_FLOOR, {'bdt': 1000000}):
            payload = self.graph(rsl=True)
        self.assert_totals(payload, [200, 500, 300, 300])
        self.assertTrue(all(not link['li'] for link in payload['links']))

    def test_invalid_missing_and_out_of_scope_selections_fail(self):
        for selection in ['month:M2', 'category:M99:sales_push', 'category:M1:sales_push', 'category:M2:rotation>month:M4', 'category:M2:incorrect_stock']:
            with self.subTest(selection=selection), self.assertRaises(evo.InvalidFilter):
                self.graph(selection, rsl=True)
        with self.assertRaises(evo.InvalidFilter):
            self.graph(lens='rsl')

    def path_graph(self, path, *, lens='exposure', unit='bdt', filters=None, cutoff=None):
        return evo.build_selection_path(self.prep.scoped({}, cutoff), evo.LENS_BY_ID[lens], unit,
                                        self.filters if filters is None else filters, path)

    def category_value(self, payload, month, category):
        return sum(link['value'] for link in payload['links'] if link['target'] == f'category:M{month}:{category}')

    def test_rsl_node_shows_categories_without_admitting_later_growth(self):
        path = [{'node': 'category:M2:rsl:3M–6M'}]
        payload = self.path_graph(path, lens='rsl')
        self.assert_totals(payload, [400, 1000, 600, 600])
        self.assertEqual(self.category_value(payload, 2, 'sales_push'), 500)
        self.assertEqual(self.category_value(payload, 2, 'rotation'), 500)
        self.assertEqual(self.category_value(payload, 4, 'good_stock'), 600)
        self.assertEqual(payload['metadata']['lens']['category_dimension'], 'mitigation_category')
        self.assertEqual(payload['metadata']['lens']['included_good_stock_policy'], 'included')
        self.assertEqual(payload['selection_path'], path)

    def test_reverse_keeps_unclassified_stock_and_can_drill_it_again(self):
        self.prep.frame = self.prep.frame.with_columns(
            pl.when((pl.col('batch') == 'a') & (pl.col('j') == 2)).then(None).otherwise(pl.col('mit')).alias('mit'))
        path = [{'node': 'category:M2:rsl:3M–6M'}]
        payload = self.path_graph(path, lens='rsl')
        self.assertEqual(self.category_value(payload, 3, 'unclassified'), 600)
        nested = self.path_graph(path + [{'node': 'category:M3:unclassified'}], lens='rsl')
        self.assert_totals(nested, [240, 600, 600, 600])
        self.assertEqual(nested['metadata']['lens']['category_dimension'], 'rsl_group')

    def test_round_trip_preserves_selected_blended_category_at_its_anchor(self):
        path = [{'node': 'category:M2:sales_push'}, {'node': 'category:M2:rsl:3M–6M'}]
        payload = self.path_graph(path)
        self.assert_totals(payload, [200, 500, 300, 300])
        self.assertEqual(self.category_value(payload, 2, 'sales_push'), 500)
        self.assertEqual(self.category_value(payload, 2, 'rotation'), 0)
        self.assertEqual(self.category_value(payload, 3, 'sales_push'), 150)
        self.assertEqual(self.category_value(payload, 3, 'rotation'), 150)
        self.assertEqual(len(payload['selections']), 2)

    def test_nested_selection_narrows_parent_and_retains_previous_conditions(self):
        path = [{'node': 'category:M2:sales_push'}, {'node': 'category:M3:rsl:<3M'},
                {'node': 'category:M3:rotation'}, {'node': 'category:M2:rsl:3M–6M'}]
        payload = self.path_graph(path[:1])
        for length in range(2, len(path) + 1):
            parent = payload
            payload = self.path_graph(path[:length])
            for month in range(1, 5):
                self.assertLessEqual(opening(payload, month), opening(parent, month))
        self.assert_totals(payload, [60, 150, 150, 150])
        self.assertEqual(self.category_value(payload, 2, 'sales_push'), 150)
        self.assertEqual(self.category_value(payload, 2, 'rotation'), 0)
        self.assertEqual(self.category_value(payload, 3, 'rotation'), 150)
        self.assertEqual(self.category_value(payload, 3, 'sales_push'), 0)

    def test_rsl_band_carry_exit_and_over_10m_identifiers_are_exact(self):
        carry = self.path_graph([{'source': 'category:M2:rsl:3M–6M', 'target': 'month:M3'}], lens='rsl')
        self.assert_totals(carry, [240, 600, 600, 600])
        exited = self.path_graph([{'source': 'category:M2:rsl:3M–6M', 'target': 'outcome:M2:sales_unavailable'}], lens='rsl')
        self.assert_totals(exited, [160, 400, 0, 0])
        for step in [{'node': 'category:M1:rsl:>10M'},
                     {'source': 'month:M1', 'target': 'category:M1:rsl:>10M'},
                     {'source': 'category:M1:rsl:>10M', 'target': 'month:M2', 'hide10': True}]:
            with self.subTest(step=step):
                payload = self.path_graph([step], lens='rsl')
                self.assert_totals(payload, [400, 400, 240, 240])
                self.assertEqual(payload['selection']['kind'], 'category' if 'node' in step else 'band')

    def test_nested_bands_keep_only_the_selected_carry_or_exit(self):
        prefix = [{'node': 'category:M2:sales_push'}]
        for outcome, expected in [('month:M3', [120, 300, 300, 300]),
                                  ('outcome:M2:resolved', [80, 200, 0, 0])]:
            with self.subTest(outcome=outcome):
                payload = self.path_graph(prefix + [{'source': 'category:M2:rsl:3M–6M', 'target': outcome}])
                self.assert_totals(payload, expected)
                self.assertEqual(self.category_value(payload, 2, 'rotation'), 0)

    def test_reverse_and_nested_units_report_bounds_and_tiny_lineages(self):
        for lens, path in [('exposure', [{'node': 'category:M2:sales_push'}, {'node': 'category:M3:rsl:<3M'}]),
                           ('lnd', [{'node': 'category:M2:sales_push'}, {'node': 'category:M3:rsl:<3M'}]),
                           ('rsl', [{'node': 'category:M2:rsl:3M–6M'}])]:
            for unit, scale in [('bdt', 1), ('kl', 10)]:
                with self.subTest(lens=lens, unit=unit), patch.dict(evo.LINEAGE_FLOOR, {'bdt': 1e9, 'kl': 1e9}):
                    payload = self.path_graph(path, lens=lens, unit=unit,
                                              filters={**self.filters, 'rsl': ['3M–6M']}, cutoff=dt.date(2026, 8, 30))
                    totals = [400, 1000, 600] if lens == 'rsl' else [120, 300, 300]
                    ratio = .5 if lens == 'lnd' else 1
                    self.assert_totals(payload, [v * scale * ratio for v in totals])
                    self.assertTrue(all(not link['li'] for link in payload['links']))

    def test_invalid_paths_and_selections_outside_parent_fail(self):
        for path in [None, {}, [], [None], [{'node': 2}], [{'node': 'category:M1:sales_push', 'value': 500}],
                     [{'source': 'month:M1'}], [{'node': 'month:M1'}], [{'node': 'category:M2:sales_push', 'hide10': 'yes'}],
                     [{'node': 'category:M2:sales_push'}] * 33,
                     [{'node': 'category:M2:sales_push'}, {'node': 'category:M2:rsl:>10M'}],
                     [{'node': 'category:M2:sales_push'}, {'node': 'category:M2:rsl:3M–6M'}, {'node': 'category:M2:rotation'}]]:
            with self.subTest(path=path), self.assertRaises(evo.InvalidFilter):
                self.path_graph(path)

    def test_selected_http_main_detail_cache_and_validation_for_saved_run(self):
        store = Store(self.root / 'runs', self.history, start_worker=False)
        run = store.create('Selected RSL evolution')
        run_history = store.path(run['id']) / 'history'
        run_history.mkdir(exist_ok=True)
        self.raw.write_excel(run_history / 'snapshots.xlsx')
        server = make_server(store, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        self.addCleanup(conn.close)
        route = f"/api/runs/{run['id']}/evolution/exposure"
        query = '?unit=bdt&brand=Brand+A&parent=101&town=Dhaka&rsl=3M%E2%80%936M&as_of=2026-08-30'
        with patch.object(evo, 'load_sales', return_value=None):
            conn.request('GET', route + query)
            response = conn.getresponse()
            original = json.loads(gzip.decompress(response.read()))
            self.assertEqual(opening(original, 2), 1000)
            selection = '&selection=category%3AM2%3Asales_push'
            for part in ['/rsl', '/rsl/detail', '/rsl']:
                conn.request('GET', route + part + query + selection)
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                payload = json.loads(gzip.decompress(response.read()))
                if part.endswith('detail'):
                    self.assertTrue(payload['links'])
                else:
                    self.assert_totals(payload, [200, 500, 300])
                    self.assertEqual(payload['as_of'], '2026-08-30')
                    self.assertNotIn('rsl', payload['filters'])
                    self.assertEqual(payload['anchor_filters']['rsl'], ['3M–6M'])
            for suffix in ['', '&selection=month:M2', '&selection=category:M99:sales_push']:
                conn.request('GET', route + '/rsl' + query + suffix)
                response = conn.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
                conn.close()  # rejected requests deliberately close the server connection
            for root, path, expected in [
                ('exposure', [{'node': 'category:M2:sales_push'}, {'node': 'category:M2:rsl:3M–6M'}], 'sales_push'),
                ('exposure', [{'node': 'category:M2:rotation'}, {'node': 'category:M2:rsl:3M–6M'}], 'rotation'),
                ('rsl', [{'node': 'category:M2:rsl:3M–6M'}], 'sales_push'),
                ('rsl', [{'node': 'category:M1:rsl:>10M'}], 'sales_push'),
            ]:
                for prefix in [f"/api/runs/{run['id']}/evolution/", '/api/evolution/']:
                    for part in ['selection', 'selection/detail', 'selection']:
                        scoped_query = '?'+urlencode({'unit': 'bdt', **{key: values[0] for key, values in self.filters.items()},
                                                     'as_of': '2026-08-30', 'path': json.dumps(path)})
                        conn.request('GET', prefix + root + '/' + part + scoped_query)
                        response = conn.getresponse()
                        self.assertEqual(response.status, 200)
                        selected_payload = json.loads(gzip.decompress(response.read()))
                        if part.endswith('detail'):
                            self.assertTrue(selected_payload['links'])
                        else:
                            self.assertEqual(selected_payload['selection_path'], path)
                            self.assertEqual(selected_payload['as_of'], '2026-08-30')
                            expected_total = 400 if path[0]['node'].endswith('>10M') else 1000 if root == 'rsl' else 500
                            self.assertEqual(selected_payload['selections'][0]['value'], expected_total)
                            self.assertGreater(self.category_value(selected_payload, 2, expected), 0)
                            if root == 'exposure':
                                self.assertEqual(self.category_value(selected_payload, 2, 'rotation' if expected == 'sales_push' else 'sales_push'), 0)
            for path in ['', 'not-json', 'null', '{}', '[]', '[{"node": "month:M2"}]', '[{"node": "category:M2:rsl:3M–6M", "value": 3}]']:
                conn.request('GET', route + '/selection?' + urlencode({'path': path}))
                response = conn.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
                conn.close()
            conn.request('GET', route + query)
            response = conn.getresponse()
            self.assertEqual(json.loads(gzip.decompress(response.read()))['links'], original['links'])


if __name__ == '__main__':
    unittest.main()

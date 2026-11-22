"""Category filtering must partition frozen stock without changing its amounts."""
import copy
import gzip
import http.client
import json
import threading
from datetime import date
from unittest.mock import patch

import polars as pl
import pytest

from server.app import make_server
from server.runs import Store
from src import evolution
from src.brand_status import aggregate, normalise
from src.stock_categories import CATEGORIES
from tests.test_not_actionables import saved
from ui.not_actionables_data import COLS
from ui.status_data import build_payload, payload, with_categories


def status_for(frames, months):
    value = payload(aggregate(pl.concat([normalise(f) for f in frames])), months)
    value['total_kl'] = payload(aggregate(pl.concat([normalise(f, 'kl') for f in frames])), months)['total']
    return value


def total(facets, field, month, metric=0):
    return sum(cell[metric] for row in facets if row[field][month] for cell in row[field][month])


def test_legacy_and_current_statuses_partition_both_units_without_losing_stock():
    statuses = [' Actionable ', 'Actionable - rotation', '\u00a0NOT ACTIONABLE\t', 'Incorrect Stock',
                'RSL>10M_Good stock', '', None, 'Other status', 'Not actionable - review']
    for legacy in (False, True):
        frame = saved(date(2026, 8, 31), statuses, legacy=legacy)
        status = status_for([frame], [date(2026, 8, 1)])
        before = copy.deepcopy(status)
        data = with_categories(status, [frame])
        assert status == before
        assert {k: v for k, v in data.items() if k not in {'categories', 'facets'}} == before
        assert data['categories'] == CATEGORIES
        for field, source in [('m', 'bdt'), ('m_kl', 'kl')]:
            for j, metric in enumerate(['total_closing_stock', 'exposure', 'lnd_fc']):
                assert total(data['facets'], field, 0, j) == pytest.approx(frame[f'{metric}_{source}'].sum())
        stock = {c: total([r for r in data['facets'] if r['category'] == c], 'm_kl', 0) for c in CATEGORIES}
        assert stock == {'Actionable': 3, 'Non actionable': 3, 'Incorrect stock': 4, 'Good stock': 5, 'Unclassified': 30}
        assert {r['pdesc'] for r in data['facets']} == {'Parent SKU'}
        assert len({(r['town'], r['brand'], r['parent']) for r in data['facets']}) == 1


def test_latest_whole_snapshot_selected_before_categories_and_missing_months_stay_gaps():
    july = saved(date(2026, 7, 31), ['Not actionable'])
    old = saved(date(2026, 9, 15), ['Not actionable'])
    latest = saved(date(2026, 9, 30), ['Incorrect Stock'])
    frames = [july, old, latest]
    months = [date(2026, m, 1) for m in (7, 8, 9)]
    data = with_categories(status_for(frames, months), frames)
    non = next(r for r in data['facets'] if r['category'] == 'Non actionable')
    incorrect = next(r for r in data['facets'] if r['category'] == 'Incorrect stock')
    assert non['m'][1:] == [None, None]
    assert incorrect['m'][:2] == [None, None]
    assert total(data['facets'], 'm', 2) == 100
    assert data['total']['m'][1] is None


def test_frozen_latest_measures_use_saved_prices_and_private_accepted_statuses():
    july = saved(date(2026, 7, 31), ['Actionable'])
    august = saved(date(2026, 8, 31), ['Not actionable'])
    # The frozen calculation uses a different price and deliberately has no planning labels.
    current = august.drop('mitigation_status', 'action_plan').with_columns(
        pl.lit(9999.).alias('total_closing_stock_bdt'), pl.lit(.4).alias('exposure_kl'),
        pl.lit(.2).alias('lnd_fc_kl'),
        (pl.col('report_date') - date(1899, 12, 30)).dt.total_days().alias('report_date'))
    status = status_for([july, august], [date(2026, 7, 1), date(2026, 8, 1)])
    before = copy.deepcopy(status)
    data = with_categories(status, [july, august], current=current)
    assert data['total'] == before['total']
    assert total(data['facets'], 'm', 0, 1) == 80
    assert total(data['facets'], 'm', 1, 0) == 100
    assert total(data['facets'], 'm', 1, 1) == 40
    assert total(data['facets'], 'm', 1, 2) == 20
    assert total(data['facets'], 'm_kl', 1, 1) == .4
    assert next(r for r in data['facets'] if r['m'][1])['category'] == 'Non actionable'
    with pytest.raises(ValueError, match='identities'):
        with_categories(status, [july, august], current=current.with_columns(pl.lit('wrong').alias('batch_index')))


def test_builder_uses_authoritative_parquet_and_default_facets_conserve_tree(tmp_path):
    frame = saved(date(2026, 8, 31), ['Actionable', 'Incorrect Stock', 'Not actionable'])
    frame.write_parquet(tmp_path / 'snapshot.parquet')
    # A stale Excel must not overwrite an authoritative accepted status.
    frame.with_columns(pl.lit('Actionable').alias('mitigation_status')).write_excel(tmp_path / 'snapshot.xlsx')
    data = build_payload(tmp_path, [date(2026, 8, 1)])
    assert {r['category'] for r in data['facets']} == {'Actionable', 'Incorrect stock', 'Non actionable'}
    for field, tree in [('m', 'total'), ('m_kl', 'total_kl')]:
        for metric in range(3):
            assert total(data['facets'], field, 0, metric) == pytest.approx(sum(c[metric] for c in data[tree]['m'][0]))


def test_existing_run_upgrades_once_from_private_history_without_modifying_report(tmp_path):
    store = Store(tmp_path / 'runs', tmp_path / 'settled', start_worker=False)
    run_id = store.create('Category upgrade')['id']
    store.update(run_id, status='ok', report_date='2026-08-31')
    run = store.path(run_id)
    (run / 'history').mkdir()
    (run / 'output').mkdir()
    frame = saved(date(2026, 8, 31), ['Actionable', 'Incorrect Stock', 'Not actionable'])
    frame.write_excel(run / 'history/snapshot.xlsx')
    frame.write_parquet(run / 'history/snapshot.parquet')
    frame.write_parquet(run / 'output/inferred_report.parquet')
    original = (run / 'output/inferred_report.parquet').read_bytes()
    status = status_for([frame], [date(2026, 8, 1)])
    data = {'STATUS': status, 'SERIES': {'facets': [], 'materials': {}, 'regions': {}},
            'NOTACTIONABLES': {'cols': COLS}}
    (run / 'data.json').write_text(json.dumps(data))
    server = make_server(store, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
    try:
        with patch('server.app.render', side_effect=lambda data, meta: json.dumps(data)):
            connection.request('GET', '/?run=' + run_id)
            response = connection.getresponse()
            assert response.status == 200
            upgraded = json.loads(response.read())['STATUS']
            assert len(upgraded['facets']) == 3
            assert upgraded['total'] == status['total']
            with patch('server.app.read_raw_reports', side_effect=AssertionError('Upgrade should be cached')):
                connection.request('GET', '/?run=' + run_id)
                response = connection.getresponse()
                assert response.status == 200
                response.read()
        assert json.loads((run / 'data.json').read_text())['STATUS'] == upgraded
        assert (run / 'output/inferred_report.parquet').read_bytes() == original
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def test_evolution_category_scope_preserves_calendar_and_rejects_unknown_categories(tmp_path):
    history = tmp_path / 'history'
    history.mkdir()
    for month in (7, 8):
        frame = saved(date(2026, month, 31), ['Actionable - rotation', 'Not actionable', 'Incorrect Stock'])
        frame.write_excel(history / f'{month}.xlsx')
    prep = evolution.Prepared(evolution.load_history(history, tmp_path / 'parsed'), None)
    scoped = prep.scoped({'category': ['Actionable', 'Incorrect stock']})
    assert scoped.dates == prep.dates
    assert scoped.frame.height == 4
    assert set(scoped.frame['category']) == {'Actionable', 'Incorrect stock'}
    with evolution.cached_artifact(history, None, tmp_path / 'graphs', 'stock', 'kl',
                                   {'category': ['Incorrect stock']}) as path:
        graph = json.loads(gzip.decompress(path.read_bytes()))
    assert graph['filters']['category'] == ['Incorrect stock']
    assert len(graph['months']) == 2
    with pytest.raises(evolution.InvalidFilter, match='category'), \
         evolution.cached_artifact(history, None, tmp_path / 'graphs', 'stock', 'kl', {'category': ['wrong']}):
        pass

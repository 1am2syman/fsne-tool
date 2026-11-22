"""All-stock movement must be independent of plans in builds and cached live runs."""
import http.client
import json
from datetime import date
from unittest.mock import patch

import polars as pl
import pytest

from server.runs import Store
from src.movement import TOTAL, normalise, transform, transform_series
from tests.test_filter_data import running_server
from ui.movement_data import payload
from ui.not_actionables_data import COLS
from ui.render import extract_legacy


def saved(day, statuses):
    count = len(statuses)
    return pl.DataFrame({
        'report_date': [day] * count, 'batch_index': [f'town-sku-{i}-1-1' for i in range(count)],
        'Brand Name': ['Brand'] * count, 'Town': ['Town'] * count,
        'Material Code': [str(i) for i in range(count)], 'parent_material_code': ['P'] * count,
        'material_desc': ['Product'] * count, 'rsl_group': ['> 10M'] * count,
        'total_closing_stock_bdt': [100. * (i + 1) for i in range(count)],
        'total_closing_stock_kl': [float(i + 1) for i in range(count)],
        'action_plan': pl.Series([None] * count, dtype=pl.String),
        'mitigation_status': pl.Series(statuses, dtype=pl.String),
    })


def history():
    statuses = ['Actionable', 'Not actionable', 'RSL>10M_Good stock', 'Incorrect Stock', None, 'Needs review']
    lm = saved(date(2026, 8, 31), statuses)
    cm = saved(date(2026, 9, 30), list(reversed(statuses)))
    return lm, cm


def test_status_changes_do_not_change_totals_or_create_new_inclusions_in_either_unit():
    lm, cm = history()
    frames = [normalise(f) for f in (lm, cm)]
    pair = transform(frames[1], frames[0]).filter(pl.col('brand') == TOTAL).row(0, named=True)
    assert pair['lm_stock_bdt'] == 2100
    assert pair['cm_no_new_bdt'] == 2100
    assert pair['cm_new_bdt'] == 0  # Grand Total sums empty inclusions to zero
    for unit, total in [('bdt', 2100), ('kl', 21)]:
        out = transform_series(pl.concat(frames), stock_column='total_closing_stock_' + unit)
        brand = out.filter(pl.col('brand') == 'Brand').row(0, named=True)
        assert brand['lm_stock_bdt'] == total
        assert brand['cm_no_new_bdt'] == total
        assert brand['cm_new_bdt'] is None
        assert brand['reduced_bdt'] == 0
        relabelled = pl.concat(frames).with_columns(pl.lit(None, dtype=pl.String).alias('status'))
        assert out.equals(transform_series(relabelled, stock_column='total_closing_stock_' + unit))


def test_latest_snapshot_per_month_and_unlabelled_months_reconcile_to_source():
    lm, cm = history()
    stale = lm.with_columns(pl.lit(date(2026, 8, 1)).alias('report_date'),
                            pl.lit(999999.).alias('total_closing_stock_bdt'))
    h = pl.concat([normalise(f) for f in (stale, lm, cm)])
    data = payload(h)
    assert data['scope'] == 'all-stock'
    assert data['labels'] == ['Sep']
    assert data['dates'] == ['2026-09-30']
    assert data['opening_date'] == '2026-08-31'
    assert data['total'][0][0]['lm'] == lm['total_closing_stock_bdt'].sum()
    assert data['total'][0][0]['o'] == cm['total_closing_stock_bdt'].sum()
    assert data['total'][0][0]['kl']['o'] == cm['total_closing_stock_kl'].sum()
    assert data['brands'][0]['p'][0]['m'] == data['brands'][0]['m']
    for field in ('lm', 'o', 'r'):
        assert data['brands'][0]['m'][0][0][field] == data['total'][0][0][field]
    assert (data['brands'][0]['m'][0][0]['n'] or 0) == data['total'][0][0]['n']


def test_single_unlabelled_snapshot_retains_closing_stock_without_fabricating_opening():
    current = saved(date(2026, 9, 30), [None, None])
    data = payload(normalise(current))
    assert data['labels'] == ['Sep']
    brand = data['brands'][0]['m'][0][0]
    assert brand['lm'] is None
    assert brand['n'] == 300
    assert brand['kl']['n'] == 3
    total = data['total'][0][0]
    assert total['lm'] is None
    assert total['r'] is None
    assert total['kl']['lm'] is None
    assert total['n'] == 300
    assert total['kl']['n'] == 3


@pytest.mark.parametrize('state', ['provisional', 'plan_uploaded', 'settled'])
def test_existing_run_upgrades_once_from_private_snapshots_without_changing_frozen_files(tmp_path, state):
    lm, cm = history()
    store = Store(tmp_path / 'runs', tmp_path / 'settled', start_worker=False)
    run = store.create('All-stock upgrade')
    store.update(run['id'], status='ok', report_date='2026-09-30', plan_state=state)
    folder = store.path(run['id'])
    (folder / 'history').mkdir()
    (folder / 'output').mkdir()
    for name, frame in [('saved-0000', lm), ('snapshot', cm)]:
        frame.write_parquet(folder / 'history' / (name + '.parquet'))
        # The authoritative Parquet differs deliberately from its stale Excel export.
        frame.with_columns(pl.lit(1.).alias('total_closing_stock_bdt')).write_excel(
            folder / 'history' / (name + '.xlsx'))
    cm.write_parquet(folder / 'output/inferred_report.parquet')
    data = {'SERIES': {'labels': ['Sep'], 'year': 2026, 'groups': ['> 10M'],
                       'doh': {'saved_demand': 12.345}, 'total': ['stale actionable totals'],
                       'facets': [], 'materials': {}, 'regions': {}},
            'STATUS': {'labels': ['Aug 2026', 'Sep 2026'], 'preserved': True},
            'MEASURES': {'stock_bdt': 987.65}, 'NOTACTIONABLES': {'cols': COLS}}
    (folder / 'data.json').write_text(json.dumps(data))
    frozen = {p: p.read_bytes() for p in folder.rglob('*') if p.suffix in {'.xlsx', '.parquet'}}
    with running_server(store) as port:
        connection = http.client.HTTPConnection('127.0.0.1', port)
        connection.request('GET', '/?run=' + run['id'])
        response = connection.getresponse()
        html = response.read().decode()
        assert response.status == 200, html
        upgraded = extract_legacy(html)
        assert upgraded['SERIES']['scope'] == 'all-stock'
        assert upgraded['SERIES']['total'][0][0]['o'] == 2100
        assert upgraded['SERIES']['doh'] == data['SERIES']['doh']
        assert upgraded['STATUS'] == data['STATUS']
        assert upgraded['MEASURES'] == data['MEASURES']
        assert json.loads((folder / 'data.json').read_text())['SERIES'] == upgraded['SERIES']
        with patch('server.app.read_raw_reports', side_effect=AssertionError('Upgrade must be cached')):
            connection.request('GET', '/?run=' + run['id'])
            response = connection.getresponse()
            assert response.status == 200
            response.read()
        connection.close()
    for path, content in frozen.items():
        assert path.read_bytes() == content

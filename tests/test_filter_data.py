"""Filter projections preserve frozen facts and reconcile with movement totals."""
import copy
import http.client
import json
import threading
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

import polars as pl
import pytest

from server.app import make_server
from server.runs import Store
from src.movement import (
    BRAND,
    RSL,
    TOTAL,
    normalise,
    transform_parent_series,
    transform_series,
)
from src.rescue import PAGES, incumbent_trend, remark_outcomes, saved_report
from ui.filter_data import enrich_payload
from ui.not_actionables_data import COLS
from ui.rescue_data import CURRENT_COLS


def snapshot(day, rows):
    # key, town, material, parent, value, volume, RSL, status, remarks
    return pl.DataFrame({
        'report_date': [day] * len(rows),
        'batch_index': [r[0] + '-1-1' for r in rows],
        'Brand Name': ['Brand'] * len(rows),
        'Town': [r[1] for r in rows], 'Material Code': [r[2] for r in rows],
        'parent_material_code': [r[3] for r in rows],
        'material_desc': ['Description ' + r[2] for r in rows],
        'total_closing_stock_bdt': pl.Series([r[4] for r in rows], dtype=pl.Float64),
        'total_closing_stock_kl': pl.Series([r[5] for r in rows], dtype=pl.Float64),
        'rsl_group': [r[6] for r in rows], 'mitigation_status': [r[7] for r in rows],
        'action_plan': pl.Series([r[8] for r in rows], dtype=pl.String),
    })


@pytest.fixture
def facts():
    lm = snapshot(date(2025, 12, 31), [
        ('a', 'North', 'child', 'P', 100., 1.12345678, '> 10M', 'Actionable', None),
        ('b', 'South', 'P', 'P', 50., 2., '> 10M', 'Actionable', None),
        ('gone', 'South', 'Q', None, 20., .5, '> 10M', 'Actionable', None),
        ('zero', None, 'zero', '', 0., 0., '> 10M', 'Actionable', None),
    ])
    cm = snapshot(date(2026, 1, 31), [
        ('a', 'North', 'child', 'P', 60., .98765432, '< 3M', 'Actionable', 'Push'),
        ('b', 'South', 'P', 'P', 40., 1.5, '> 10M', 'Actionable', 'Rotate'),
        ('new', 'North', 'Q', None, 10., .25, '< 3M', 'Actionable', None),
        ('zero', None, 'zero', '', 0., 0., '> 10M', 'Actionable', None),
        ('null', 'South', 'null', None, None, None, '> 10M', 'Actionable', None),
        ('hidden', 'South', 'hidden', 'P', 999., 9., '> 10M', 'Not actionable', None),
    ])
    payload = {'SERIES': {'labels': ['Jan'], 'year': 2026, 'groups': ['< 3M', '> 10M'],
                          'brands': [], 'total': ['saved totals'], 'doh': {'saved': True}},
               'BATCH': [{'Material Code': 'child'}], 'MEASURES': {'frozen': 123.45},
               'NOTACTIONABLES': {'cols': COLS}, 'STATUS': {'frozen': True}}
    return [lm, cm], cm, payload


def test_facets_reconcile_every_group_parent_and_unit(facts):
    raw, current, payload = facts
    out = enrich_payload(payload, raw, current)['SERIES']
    history = pl.concat([normalise(f) for f in raw], how='vertical_relaxed')
    fields = {'lm': 'lm_stock_bdt', 'o': 'cm_no_new_bdt', 'n': 'cm_new_bdt', 'r': 'reduced_bdt'}
    for unit, column in [('bdt', 'total_closing_stock_bdt'), ('kl', 'total_closing_stock_kl')]:
        totals = transform_series(history, keys=(BRAND, RSL), stock_column=column)
        parents = transform_parent_series(history, rsl=True, stock_column=column)
        for i, group in enumerate(out['groups']):
            for parent in [None, 'P', 'Q']:
                cells = [f['m'][0][i] for f in out['facets']
                         if (parent is None or f['parent'] == parent) and f['m'][0] and f['m'][0][i]]
                expected = (totals.filter((pl.col('brand') == TOTAL) & (pl.col(RSL) == group))
                            if parent is None else parents.filter((pl.col('parent_code') == parent) & (pl.col(RSL) == group)))
                for key, field in fields.items():
                    value = sum(((c if unit == 'bdt' else c['kl'])[key] or 0) for c in cells)
                    assert value == pytest.approx(expected[field].sum() or 0)
    north = next(f for f in out['facets'] if f['town'] == 'North' and f['parent'] == 'P')
    assert north['m'][0][0] == {'lm': 100., 'o': 60., 'n': None, 'r': 40., 'rem': 'Push',
                                'kl': {'lm': 1.12345678, 'o': .98765432, 'n': None, 'r': pytest.approx(.13580246)}}
    south = next(f for f in out['facets'] if f['town'] == 'South' and f['parent'] == 'P')
    assert south['m'][0][0] is None
    assert south['m'][0][1]['lm'] == 50.
    zero = next(f for f in out['facets'] if f['parent'] == 'zero')
    assert zero['town'] == ''
    assert zero['m'][0][1]['lm'] == 0.
    null = next(f for f in out['facets'] if f['parent'] == 'null')
    assert null['m'][0][1]['lm'] is None
    assert null['m'][0][1]['n'] == 0.  # same all-null sum semantics as existing movement


def test_fractional_values_reconcile_without_rounding_each_town(facts):
    raw, current, payload = facts
    raw = [f.with_columns((pl.col('total_closing_stock_bdt') + .004).alias('total_closing_stock_bdt'))
           for f in raw]
    out = enrich_payload(payload, raw, current)['SERIES']
    values = [c['lm'] for f in out['facets'] for month in f['m'] if month for c in month if c and c['lm'] is not None]
    assert sum(values) == pytest.approx(170.016)
    assert round(sum(values), 2) == 170.02


def test_materials_use_full_report_and_upgrade_preserves_frozen_measures(facts):
    raw, current, payload = facts
    before = copy.deepcopy(payload)
    result = enrich_payload(payload, raw, current)
    assert payload == before
    assert {k: v for k, v in result.items() if k != 'SERIES'} == {k: v for k, v in before.items() if k != 'SERIES'}
    assert result['SERIES']['scope'] == 'all-stock'
    assert result['SERIES']['doh'] == before['SERIES']['doh']
    assert result['SERIES']['total'][0][1]['n'] == 999.  # Not actionable stock is included
    assert result['SERIES']['total'][0][1]['kl']['n'] == 9.
    materials = result['SERIES']['materials']
    assert materials['hidden'] == {'parent': 'P', 'pdesc': 'Description P'}
    assert materials['child'] == materials['P']
    assert materials['Q'] == {'parent': 'Q', 'pdesc': 'Description Q'}
    assert materials['zero'] == {'parent': 'zero', 'pdesc': 'Description zero'}
    assert enrich_payload(result, raw, current) == result


def test_label_alignment_across_year_and_missing_month_cells(facts):
    raw, current, payload = facts
    raw.append(snapshot(date(2026, 2, 28), [
        ('later', 'North', 'P', 'P', 5., .1, '< 3M', 'Actionable', None),
    ]))
    payload['SERIES']['labels'] = ['Jan', 'Feb']
    out = enrich_payload(payload, raw, current)['SERIES']
    facet = next(f for f in out['facets'] if f['parent'] == 'P' and f['town'] == 'North')
    assert len(facet['m']) == 2
    assert facet['m'][0][0]['lm'] == 100.
    assert facet['m'][1][0]['n'] == 5.
    only_jan = next(f for f in out['facets'] if f['parent'] == 'Q' and f['town'] == 'South')
    assert only_jan['m'][1] is None
    # Window ends in January but includes a December movement.
    raw.insert(0, raw[0].with_columns(pl.lit(date(2025, 11, 30)).alias('report_date')))
    payload['SERIES']['labels'] = ['Dec', 'Jan']
    out = enrich_payload(payload, raw[:3], current)['SERIES']
    facet = next(f for f in out['facets'] if f['parent'] == 'P' and f['town'] == 'North')
    assert facet['m'][0][1]['o'] == 100.
    assert facet['m'][1][0]['o'] == 60.


@pytest.fixture
def cohort_facts(facts):
    _, _, payload = facts
    raw = []
    # Same-month snapshots must be superseded before dates are ranked. Five
    # transitions also distinguish the outcome panel's latest-three window.
    for index, day in enumerate([date(2025, 12, 1), date(2025, 12, 31),
                                 date(2026, 1, 31), date(2026, 2, 28),
                                 date(2026, 3, 31), date(2026, 4, 30)]):
        rows = [
            ('a', 'North', 'child', 'P', 100. - index, 10. - index, '3M - 6M', 'Actionable', 'Sales push'),
            ('b', 'South', 'Q', None, 40. - index, 4. - index / 2, '6M - 10M', 'Actionable', 'Stock rotation'),
            ('c', 'South', 'C', 'C', 20. - index, 2. - index / 4, '> 10M', 'Incorrect Stock', 'Check'),
            ('zero', 'North', 'zero', None, 0., 0., '3M - 6M', 'Actionable', None),
            ('null', 'North', 'null', None, None, None, '3M - 6M', 'Actionable', None),
        ]
        if index < 3:
            rows.append(('gone', 'North', 'G', 'G', 30., 3., '6M - 10M', 'Actionable', 'Liquidation'))
        frame = snapshot(day, rows)
        # Duplicate movement key, distinct exact batch: the helper must not
        # multiply the cohort by the number of matching dimension rows.
        duplicate = frame.head(1).with_columns(pl.lit('a-2-2').alias('batch_index'))
        frame = pl.concat([frame, duplicate]).with_columns(
            pl.col('total_closing_stock_bdt').alias('lnd_fc_bdt'),
            pl.col('total_closing_stock_kl').alias('lnd_fc_kl'),
        )
        raw.append(frame)
    saved = pl.concat([saved_report(f) for f in raw], how='vertical_relaxed')
    payload['ACTIONPLANS'] = {
        key: {'trend': incumbent_trend(saved, page).to_dicts(),
              'outcomes': {'window': [d.strftime('%b') for d in remark_outcomes(saved, page)[1]]}}
        for key, page in PAGES.items()
    }
    return raw, raw[-1], payload, saved


def test_action_plan_filters_reconcile_with_existing_trends_and_outcomes(cohort_facts):
    raw, current, payload, saved = cohort_facts
    before = copy.deepcopy(payload)
    result = enrich_payload(payload, raw, current)
    assert payload == before
    for key, page in PAGES.items():
        actual = result['ACTIONPLANS'][key]
        assert actual['trend'] == payload['ACTIONPLANS'][key]['trend']
        assert actual['outcomes'] == payload['ACTIONPLANS'][key]['outcomes']
        for expected in incumbent_trend(saved, page).iter_rows(named=True):
            selected = [r for r in actual['filter_trend'] if r['date'] == expected['cm_date'].isoformat()]
            assert sum(r['batches'] for r in selected) == expected['batches']
            assert {r['m'] for r in selected} == {expected['cm_date'].strftime('%b')}
            for side in ['lm', 'cm']:
                for unit in ['bdt', 'kl']:
                    assert sum(r[side][unit] for r in selected) == pytest.approx(expected[side + '_' + unit])
        outcomes, window = remark_outcomes(saved, page)
        dates = {d.isoformat() for d in window}
        for expected in outcomes.iter_rows(named=True):
            selected = [r for r in actual['filter_outcomes'] if r['date'] in dates and r['c'] == expected['category']]
            assert sum(r['n'] for r in selected) == expected['batches']
            for side in ['lm', 'cm']:
                for unit in ['bdt', 'kl']:
                    assert sum(r[side][unit] for r in selected) == pytest.approx(expected[side + '_' + unit])
    # Rebalance's South/Q cohort is independent of North/G and has no parent row.
    selected = [r for r in result['ACTIONPLANS']['rebalance']['filter_trend']
                if r['town'] == 'South' and r['parent'] == 'Q' and r['date'] == '2026-01-31']
    assert len(selected) == 1
    assert selected[0]['lm'] == {'bdt': 39., 'kl': 3.5}
    assert selected[0]['cm'] == {'bdt': 38., 'kl': 3.}
    assert selected[0]['batches'] == 1


def test_cohort_dimensions_belong_to_lm_even_when_cm_changes(cohort_facts):
    raw, current, payload, _ = cohort_facts
    raw[2] = raw[2].with_columns(
        pl.when(pl.col('Material Code') == 'Q').then(pl.lit('Elsewhere')).otherwise(pl.col('Town')).alias('Town'),
        pl.when(pl.col('Material Code') == 'Q').then(pl.lit('New brand')).otherwise(pl.col('Brand Name')).alias('Brand Name'),
    )
    result = enrich_payload(payload, raw, current)
    rows = [r for r in result['ACTIONPLANS']['rebalance']['filter_outcomes']
            if r['date'] == '2026-01-31' and r['parent'] == 'Q']
    assert len(rows) == 1
    assert rows[0]['town'] == 'South'
    assert rows[0]['brand'] == 'Brand'
    assert rows[0]['c'] == 'Stock rotation'
    assert rows[0]['cm']['kl'] == 3.


def test_regions_read_optional_shelf_life_without_changing_facts(tmp_path, facts):
    raw, current, payload = facts
    (tmp_path / 'shelflife.csv').write_text('Town,Region Name\nNorth,Region A\nSouth,Region B\nNorth,Region A\n')
    result = enrich_payload(payload, raw, current, tmp_path)
    assert result['SERIES']['regions'] == {'North': 'Region A', 'South': 'Region B'}
    assert 'regions' not in payload['SERIES']
    assert enrich_payload(payload, raw, current)['SERIES']['regions'] == {}


@contextmanager
def running_server(store):
    server = make_server(store, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_completed_run_lazy_upgrade_only_writes_payload(tmp_path, facts):
    raw, current, payload = facts
    raw = [f.with_columns(pl.col('total_closing_stock_kl').alias('lnd_fc_kl'),
                          pl.col('total_closing_stock_bdt').alias('lnd_fc_bdt')) for f in raw]
    payload['ACTIONPLANS'] = {'rescue': {'trend': ['saved trend'], 'outcomes': {'window': ['Jan']},
                                       'cols': CURRENT_COLS, 'rows': []}}
    # This run already has movement filters but predates action-plan filters.
    older = enrich_payload(payload, raw, current)
    older['ACTIONPLANS']['rescue'].pop('filter_trend')
    older['ACTIONPLANS']['rescue'].pop('filter_outcomes')
    store = Store(tmp_path / 'runs', tmp_path / 'history', start_worker=False)
    run = store.create('Frozen')
    store.update(run['id'], status='ok')
    folder = store.path(run['id'])
    output = folder / 'output'
    output.mkdir(exist_ok=True)
    report = output / 'inferred_report.parquet'
    current.write_parquet(report)
    frozen_bytes = report.read_bytes()
    (folder / 'data.json').write_text(json.dumps(older))
    with patch('server.app.read_raw_reports', return_value=raw) as read_history, \
         patch('server.app.render', return_value='<html>saved</html>') as render, \
         running_server(store) as port:
        for _ in range(2):
            connection = http.client.HTTPConnection('127.0.0.1', port)
            connection.request('GET', '/?run=' + run['id'])
            response = connection.getresponse()
            assert response.status == 200, response.read()
            response.read()
            connection.close()
        assert read_history.call_count == 1
        assert render.call_count == 2
    upgraded = json.loads((folder / 'data.json').read_text())
    assert upgraded == enrich_payload(payload, raw, current)
    assert report.read_bytes() == frozen_bytes
    assert store.get(run['id'])['status'] == 'ok'

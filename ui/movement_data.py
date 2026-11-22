"""All-stock movement projection from frozen monthly facts, shared by builds/upgrades."""

from __future__ import annotations

import polars as pl

from src.brand_status import ORDER
from src.movement import (
    BRAND,
    RSL,
    TOTAL,
    monthly,
    transform_parent_series,
    transform_series,
)

SCOPE = 'all-stock'
METHOD = 'batch-delta'
FIELDS = {'lm': 'lm_stock_bdt', 'o': 'cm_no_new_bdt', 'n': 'cm_new_bdt', 'r': 'reduced_bdt'}


def payload(history: pl.DataFrame, months: int = 6) -> dict:
    """Derive both units at saved precision; never recalculate stock, prices or rates."""
    history = monthly(history)
    dates = history['report_date'].drop_nulls().unique().sort().to_list()[-(months + 1):]
    if not dates:
        raise ValueError('No saved stock snapshots for movement')
    series = transform_series(history, months=months, keys=(BRAND, RSL))
    parents = transform_parent_series(history, months=months, rsl=True)
    volume = transform_series(history, months=months, keys=(BRAND, RSL), stock_column='total_closing_stock_kl')
    volume_parents = transform_parent_series(history, months=months, rsl=True, stock_column='total_closing_stock_kl')
    volume_by = {(r['brand'], r.get('parent_code'), r['month'], r[RSL]): r
                 for frame in (volume, volume_parents) for r in frame.iter_rows(named=True)}
    present = set(series[RSL].unique().to_list())
    groups = [g for g in ORDER if g in present] + sorted(present - set(ORDER))
    group_slots = {g: i for i, g in enumerate(groups)}
    closing_dates = dates[1:] if len(dates) > 1 else dates

    def cell(row):
        kl = volume_by[(row['brand'], row.get('parent_code'), row['month'], row[RSL])]
        return {**{k: row[c] for k, c in FIELDS.items()}, 'rem': row['remarks'],
                'kl': {k: kl[c] for k, c in FIELDS.items()}}

    def slot(store, row):
        store.setdefault(row['month'], [None] * len(groups))[group_slots[row[RSL]]] = cell(row)

    by, parent_by = {}, {}
    for row in series.iter_rows(named=True):
        slot(by.setdefault(row['brand'], {}), row)
    for row in parents.iter_rows(named=True):
        parent = parent_by.setdefault(row['brand'], {}).setdefault(
            row['parent_code'], {'c': row['parent_code'], 'd': row['parent_desc'], 'by': {}})
        slot(parent['by'], row)
    total = by.pop(TOTAL)
    return {
        'scope': SCOPE, 'method': METHOD, 'labels': [m.strftime('%b') for m in closing_dates],
        'dates': [m.isoformat() for m in closing_dates], 'opening_date': dates[0].isoformat(),
        'start': dates[0].strftime('%b'), 'year': closing_dates[-1].year, 'groups': groups,
        'total': [total[m] for m in closing_dates],
        'brands': [{'b': b, 'm': [v.get(m) for m in closing_dates],
                    'p': [{'c': p['c'], 'd': p['d'], 'm': [p['by'].get(m) for m in closing_dates]}
                          for p in parent_by.get(b, {}).values()]}
                   for b, v in by.items()],
    }

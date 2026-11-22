"""Add filter projections from saved facts without rebuilding or pricing a report."""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from src.movement import (
    BRAND,
    KEY,
    PARENT,
    RSL,
    batch_key,
    normalise,
    transform_parent_series,
    transform_series,
)
from src.rescue import PAGES, _cohorts, category, monthly, saved_report
from ui.movement_data import SCOPE
from ui.movement_data import payload as movement_payload
from ui.rescue_data import shelf_life_facts

_FIELDS = {'lm': 'lm_stock_bdt', 'o': 'cm_no_new_bdt', 'n': 'cm_new_bdt', 'r': 'reduced_bdt'}


def _materials(current: pl.DataFrame) -> dict:
    """Use the full frozen report, including stock outside the displayed batch cap."""
    rows = current.select(
        pl.col('Material Code').cast(pl.String).str.strip_chars().alias('code'),
        pl.col('parent_material_code').cast(pl.String).str.strip_chars().replace('', None).alias('parent'),
        pl.col('material_desc').cast(pl.String).alias('desc'),
    ).with_columns(pl.coalesce('parent', 'code').alias('parent'))
    descriptions = rows.group_by('parent').agg(
        pl.col('desc').filter(pl.col('code') == pl.col('parent')).drop_nulls().first().alias('own'),
        pl.col('desc').drop_nulls().mode().sort().first().alias('mode'),
    ).select('parent', pl.coalesce('own', 'mode', 'parent').alias('pdesc'))
    return {r['code']: {'parent': r['parent'], 'pdesc': r['pdesc']}
            for r in rows.join(descriptions, on='parent', how='left').drop_nulls('code')
            .unique('code', keep='first', maintain_order=True).iter_rows(named=True)}


def _action_plan_filters(pages: dict, raw_history: Sequence[pl.DataFrame]) -> dict:
    """Attach each cohort to its LM dimensions without changing cohort matching.

    A movement key can have multiple exact batch rows. Use the largest LM LND
    row for its dimensions, matching the plan selection inside rescue._cohorts;
    never join cohort values to every batch and multiply the frozen measures.
    """
    saved = pl.concat([saved_report(frame) for frame in raw_history], how='vertical_relaxed')
    dimensions = pl.concat([
        normalise(frame).with_columns(
            frame['lnd_fc_kl'].cast(pl.Float64, strict=False).fill_null(0.0).alias('_lnd'),
        ) for frame in raw_history
    ], how='vertical_relaxed').with_columns(
        batch_key(),
        pl.col(BRAND).alias('brand'),
        pl.coalesce(pl.col(PARENT).replace('', None), 'material_code').fill_null('').alias('parent'),
    )
    dimensions = monthly(dimensions).with_columns(
        pl.col('report_date').rank('dense').cast(pl.Int32).alias('_i'),
    )
    result = dict(pages)
    for key, original in pages.items():
        page = PAGES.get(key)
        if page is None:
            continue
        filters = {}
        for field, bands, category_field in [
            ('filter_trend', (page.band,), None),
            ('filter_outcomes', page.outcome_bands, 'c'),
        ]:
            lm = (dimensions.filter(pl.col(RSL).is_in(bands) & (pl.col('_lnd') > 0))
                  .sort('_lnd', descending=True).group_by('_i', KEY)
                  .agg(pl.col('brand', 'parent', 'town').first()))
            cohorts = _cohorts(saved, bands).with_columns(category()).join(
                lm, on=['_i', KEY], how='left', validate='1:1',
            )
            by = ['brand', 'parent', 'town', 'cm_date'] + (['category'] if category_field else [])
            grouped = cohorts.group_by(by).agg(
                pl.col('lm_bdt', 'lm_kl', 'cm_bdt', 'cm_kl').sum(), pl.len().alias('n'),
            ).sort(by)
            records = []
            for row in grouped.iter_rows(named=True):
                record = {k: row[k] for k in ('brand', 'parent', 'town')}
                record.update({
                    'date': row['cm_date'].isoformat(),
                    'lm': {'bdt': row['lm_bdt'], 'kl': row['lm_kl']},
                    'cm': {'bdt': row['cm_bdt'], 'kl': row['cm_kl']},
                })
                if category_field:
                    record.update(c=row['category'], n=row['n'])
                else:
                    record.update(m=row['cm_date'].strftime('%b'), batches=row['n'])
                records.append(record)
            filters[field] = records
        result[key] = {**original, **filters}
    return result


def enrich_payload(payload: dict, raw_history: Sequence[pl.DataFrame], current: pl.DataFrame,
                   data_dir: Path | None = None) -> dict:
    """Derive all-stock movement/filters without changing frozen report measures.

    Older Actionable-only SERIES data is replaced once from private snapshots;
    saved DOH and other reports are retained. Facets follow SERIES labels/groups. Both
    units retain saved precision so rounding many town cells cannot change the
    all-filter totals. Action-plan filters use LM cohort dimensions and retain
    the original trend/outcome matching and measures. Regions come from the
    run's input shelf-life export when available. Input payloads are not mutated.
    """
    series = payload['SERIES']
    history = pl.concat([normalise(frame) for frame in raw_history], how='vertical_relaxed')
    history = history.with_columns(
        pl.coalesce(pl.col(PARENT).replace('', None), 'material_code').fill_null('').alias(PARENT),
    )
    if series.get('scope') != SCOPE:
        series = {**series, **movement_payload(history, months=len(series.get('labels', [])) or 6)}
    materials = _materials(current)
    descriptions = {r['parent_code']: r['parent_desc'] for r in
                    transform_parent_series(history).iter_rows(named=True)}
    descriptions.update({p['c']: p['d'] for b in series.get('brands', []) for p in b.get('p', [])})
    keys = (BRAND, PARENT, 'town', RSL)
    # Keep the saved calendar, including a year boundary and empty-cohort months.
    year = series['year']
    previous = 13
    month_slots = {}
    for i in range(len(series['labels']) - 1, -1, -1):
        month = list(calendar.month_abbr).index(series['labels'][i])
        if month > previous:
            year -= 1
        month_slots[(year, month)] = i
        previous = month
    group_slots = {g: i for i, g in enumerate(series['groups'])}
    frames = [transform_series(history, months=len(series['labels']), keys=keys, stock_column=column)
              for column in ('total_closing_stock_bdt', 'total_closing_stock_kl')]
    volume = {(r['brand'], r[PARENT], r['town'], r[RSL], r['month']): r
              for r in frames[1].iter_rows(named=True)}
    facets = {}
    for row in frames[0].iter_rows(named=True):
        date = row['month']
        slot = month_slots.get((date.year, date.month))
        group = group_slots.get(row[RSL])
        if slot is None or group is None:
            continue
        key = (row['brand'], row[PARENT], row['town'])
        facet = facets.setdefault(key, {
            'brand': row['brand'], 'parent': row[PARENT],
            'pdesc': descriptions.get(row[PARENT], row[PARENT]), 'town': row['town'],
            'm': [None] * len(series['labels']),
        })
        if facet['m'][slot] is None:
            facet['m'][slot] = [None] * len(series['groups'])
        kl = volume[(*key, row[RSL], date)]
        facet['m'][slot][group] = {
            **{k: row[c] for k, c in _FIELDS.items()},
            'rem': row['remarks'], 'kl': {k: kl[c] for k, c in _FIELDS.items()},
        }
    regions, _ = shelf_life_facts(data_dir)
    region_map = {r['Town']: r['Region Name'] for r in regions.iter_rows(named=True)} if regions is not None else {}
    result = {**payload, 'SERIES': {**series, 'facets': list(facets.values()),
                                  'materials': materials, 'regions': region_map}}
    if payload.get('ACTIONPLANS'):
        result['ACTIONPLANS'] = _action_plan_filters(payload['ACTIONPLANS'], raw_history)
    return result

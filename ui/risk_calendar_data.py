"""UI payload for the upcoming-risk calendar (dates, bands and projections come from src/)."""
from datetime import timedelta
from pathlib import Path

import polars as pl

from src import inventory_projection, report, soh_db
from src.risk_calendar import (
    BANDS,
    POLICY_VERSION,
    THRESHOLDS,
    band,
    summarise,
    transform,
)
from src.run_rate_parent import source_through
from src.secondary import load

COLS = ['id', 'town', 'brand', 'material', 'batch', 'expiry', 'rsl', 'kl', 'bdt',
        'exp_kl', 'exp_bdt', 'rate', 'days', 'band', 'pr', 'ps']
STATUSES = ['modeled', 'observed_past_event', 'unknown_no_rate', 'beyond_horizon']
UNAVAILABLE = ('unknown_no_rate', 'beyond_horizon')


def projection_for(data_dir: Path, batches: pl.DataFrame) -> pl.DataFrame:
    """Phase B scenarios for the same snapshot as the calendar."""
    as_of = report.EXCEL_EPOCH + timedelta(days=batches.item(0, 'report_date'))
    stock = soh_db.build(data_dir, as_of=as_of)
    shares = inventory_projection.child_shares(source_through(load(data_dir), as_of))
    return inventory_projection.transform(batches, shares, inventory_projection.received_dates(stock))


def projection_summary(projection: pl.DataFrame, events_index: dict, band_index: dict, scenario_index: dict) -> list:
    """Per scenario/event/band: modelled remaining and the stock without a projection."""
    keys = ['scenario_id', 'event_kind', 'threshold_days', 'band']
    grouped = projection.with_columns(band(pl.col('days_until_event')).alias('band')).group_by(keys).agg(
        pl.col('projected_remaining_kl').sum().alias('remaining_kl'),
        pl.col('projected_value_bdt').sum().alias('remaining_bdt'),
        pl.col('opening_kl').filter(pl.col('coverage_status').is_in(UNAVAILABLE)).sum().alias('unavailable_kl'),
        pl.col('coverage_status').is_in(UNAVAILABLE).sum().alias('unavailable_batches'),
    )
    return [{'scenario': scenario_index[r['scenario_id']], 'event': events_index[(r['event_kind'], r['threshold_days'])],
             'band': band_index[r['band']], 'remaining_kl': r['remaining_kl'], 'remaining_bdt': r['remaining_bdt'],
             'unavailable_kl': r['unavailable_kl'], 'unavailable_batches': r['unavailable_batches']}
            for r in grouped.iter_rows(named=True)]


def build_payload(batches: pl.DataFrame, projection: pl.DataFrame) -> dict:
    """Complete population: one row per batch carrying all event offsets, bands and projections."""
    events = transform(batches)
    events_list = [{'kind': 'warning', 'days': t, 'label': f'{t}-day warning'}
                   for t in sorted(THRESHOLDS, reverse=True)]
    events_list.append({'kind': 'expiry', 'days': None, 'label': 'Actual expiry'})
    index = {(e['kind'], e['days']): i for i, e in enumerate(events_list)}
    band_index = {k: i for i, (k, _, _) in enumerate(BANDS)}
    scenarios = projection.select('scenario_id', 'rate_multiplier').unique(maintain_order=True)
    scenario_list = [{'id': r['scenario_id'], 'multiplier': r['rate_multiplier']} for r in scenarios.iter_rows(named=True)]
    scenario_index = {s['id']: i for i, s in enumerate(scenario_list)}
    per_batch = {}
    for r in events.iter_rows(named=True):
        slot = per_batch.setdefault(r['batch_id'], {
            'r': r, 'days': [None] * len(events_list), 'band': [None] * len(events_list),
            'pr': [[None] * len(events_list) for _ in scenario_list],
            'ps': [[None] * len(events_list) for _ in scenario_list]})
        i = index[(r['event_kind'], r['threshold_days'])]
        slot['days'][i] = r['days_until_event']
        slot['band'][i] = band_index[r['horizon_band']]
    for r in projection.iter_rows(named=True):
        slot = per_batch[r['batch_id']]
        i, s = index[(r['event_kind'], r['threshold_days'])], scenario_index[r['scenario_id']]
        slot['pr'][s][i] = None if r['projected_remaining_kl'] is None else round(r['projected_remaining_kl'], 6)
        slot['ps'][s][i] = STATUSES.index(r['coverage_status'])
    rows = [[b, r['town'], r['brand'], r['material_desc'], r['batch_code'], r['expiry_date'].isoformat(),
             r['rsl_days'], r['stock_kl'], r['stock_bdt'], r['exposure_kl'], r['exposure_bdt'],
             int(r['demand_known']), s['days'], s['band'], s['pr'], s['ps']]
            for b, s in per_batch.items() for r in [s['r']]]
    summary = [{**r, 'event': index[(r['event_kind'], r['threshold_days'])], 'band': band_index[r['horizon_band']]}
               for r in summarise(events).iter_rows(named=True)]
    first = projection.row(0, named=True)
    return {'snapshot': events.item(0, 'snapshot_id'), 'policy': POLICY_VERSION,
            'events': events_list, 'bands': [{'k': k, 'label': label} for k, label, _ in BANDS],
            'cols': COLS, 'rows': rows, 'summary': summary,
            'projection': {'policy': first['policy_version'], 'horizon': first['horizon_days'],
                           'allocation': first['allocation_policy'], 'basis': first['demand_basis'],
                           'scenarios': scenario_list, 'statuses': STATUSES,
                           'summary': projection_summary(projection, index, band_index, scenario_index)}}

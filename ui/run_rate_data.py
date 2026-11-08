"""Rebuild current rates/measures and join them to the matching saved snapshot."""
from dataclasses import asdict
from pathlib import Path

import polars as pl

from src import db_landing, report, run_rate_parent, soh_db, starting_date
from src.doh import stock_pairs
from src.io import date as parse_date
from src.secondary import load
from ui.doh_data import audit_payload


def build_current(data_dir: Path, saved: pl.DataFrame, latest_date, output_dir: Path):
    """Fail rather than combine stocks and rates from different snapshots.

    The current stock input must reproduce saved stock/identity exactly. Only
    rate-dependent fields are refreshed; manual mitigation labels stay saved.
    """
    config_date = starting_date.build(data_dir).item(0, 'starting_date')
    if config_date != latest_date:
        raise ValueError('Current source report date differs from latest saved snapshot')
    source = run_rate_parent.source_through(load(data_dir), config_date)
    audit = run_rate_parent.adjustments(source)
    if audit['month'].max() != config_date.replace(day=1):
        raise ValueError('Latest run-rate source month does not match stock snapshot')
    rates = run_rate_parent.rates_from_adjustments(audit)
    raw_rates = run_rate_parent.raw_rates(source)
    stock = soh_db.build(data_dir, as_of=config_date)
    landing = db_landing.build(data_dir)
    current = report.transform(stock, landing, config_date)
    raw_stock = stock.drop('avg_sec').join(
        raw_rates, left_on=['Town', 'parent_material_code'],
        right_on=['town', 'parent_material_code'], how='left', validate='m:1',
    )
    baseline = report.transform(raw_stock, landing, config_date)
    saved = saved.filter(parse_date('report_date') == config_date)
    if saved['batch_index'].n_unique() != saved.height:
        raise ValueError('Saved batch identities must be unique')
    if set(saved['batch_index']) != set(current['batch_index']):
        raise ValueError('Current inputs do not reproduce saved snapshot batch identities')
    identity = ['Town', 'Brand Name', 'parent_material_code', 'Material Code', 'rsl_group']
    identity_check = saved.select('batch_index', *[pl.col(c).cast(pl.String).alias('saved_' + c) for c in identity]).join(
        current.select('batch_index', *[pl.col(c).cast(pl.String) for c in identity]), on='batch_index', validate='1:1')
    for c in identity:
        if identity_check.filter(~pl.col(c).eq_missing(pl.col('saved_' + c))).height:
            raise ValueError('Current hierarchy differs from saved snapshot: ' + c)
    # Prices and stock are not adjusted by this policy. Validate both measures.
    check = saved.select(
        'batch_index', *[pl.col(c).cast(pl.Float64).alias('saved_' + c)
                        for c in ['total_closing_stock_kl', 'total_closing_stock_bdt']]
    ).join(current, on='batch_index', validate='1:1')
    for c in ['total_closing_stock_kl', 'total_closing_stock_bdt']:
        if check.filter(
            (pl.col(c).is_null() != pl.col('saved_' + c).is_null())
            | ((pl.col(c) - pl.col('saved_' + c)).abs() > 1e-8 + pl.col(c).abs() * 1e-10)
        ).height:
            raise ValueError('Current inputs differ from saved stock: ' + c)
    fields = ['avg_sec_m', 'doh', 'consumable_kl', 'consumable_bdt', 'consumable_kl_2x_rate',
              'rotation_req_kl', 'rotation_req_bdt', 'exposure_kl', 'exposure_bdt',
              'lnd_fc_kl', 'lnd_fc_bdt', 'exposure_kl_%', 'exposure_bdt_%',
              'lnd_fc_kl_%', 'lnd_fc_bdt_%']
    refreshed = saved.drop(fields).join(current.select('batch_index', *fields), on='batch_index', validate='1:1')
    # Status reports display all stock, not only the Actionable movement cohort.
    pairs = stock_pairs(refreshed, rates, config_date, actionable_only=False)
    # Store each pair once; selected RSL cells share the same rate.
    nodes = {}
    for row in pairs.iter_rows(named=True):
        key = (row['brand'], row['parent'], row['town'])
        node = nodes.setdefault(key, {'b': row['brand'], 'p': str(row['parent']),
                                      't': row['town'], 'rate': row['rate'], 'stock': {}})
        node['stock'][row['rsl']] = row['stock_kl']
    measures = ['total_closing_stock_kl', 'total_closing_stock_bdt', 'consumable_kl',
                'exposure_kl', 'exposure_bdt', 'rotation_req_kl', 'rotation_req_bdt', 'lnd_fc_kl', 'lnd_fc_bdt']
    before = baseline.select(pl.col(measures).sum()).row(0, named=True)
    after = current.select(pl.col(measures).sum()).row(0, named=True)
    impact = {
        'policy': asdict(run_rate_parent.DEFAULT_POLICY), 'report_date': config_date.isoformat(),
        'source_months': audit['month'].n_unique(), 'pair_months': audit.height,
        'winsorized_months': audit.filter(pl.col('reason') == 'winsorized').height,
        'ambiguous_peaks_preserved': audit.filter(pl.col('reason') == 'ambiguous_peak_preserved').height,
        'removed_kl': (audit['raw_kl'] - audit['adjusted_kl']).sum(),
        'raw_monthly_rate_kl': raw_rates['avg_sec'].sum(), 'adjusted_monthly_rate_kl': rates['avg_sec'].sum(),
        'measures_before': before, 'measures_after': after,
        'deltas': {c: after[c] - before[c] for c in measures},
        'historical_snapshots_rewritten': False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    audit.write_parquet(output_dir / 'run_rate_audit.parquet')
    audit.filter(pl.col('reason').is_in(['winsorized', 'ambiguous_peak_preserved'])).write_csv(output_dir / 'run_rate_flags.csv')
    rate_changes = raw_rates.rename({'avg_sec': 'raw_avg_sec'}).join(rates, on=run_rate_parent.KEYS)
    rate_changes.write_parquet(output_dir / 'run_rates.parquet')
    baseline.write_parquet(output_dir / 'legacy_report.parquet')
    current.write_parquet(output_dir / 'inferred_report.parquet')
    current.write_excel(output_dir / 'inferred_report.xlsx', worksheet='Sheet1', float_precision=15)
    displayed_keys = pl.concat([
        pairs.select('town', pl.col('parent').alias('parent_material_code')),
        current.sort('total_closing_stock_bdt', descending=True, nulls_last=True).head(400)
        .select(pl.col('Town').alias('town'), 'parent_material_code'),
    ]).unique()
    return current, refreshed, {'date': config_date.isoformat(), 'source_months': audit['month'].n_unique(),
                                'policy': 'seasonality-aware upper-tail winsorization', 'pairs': list(nodes.values()),
                                'bounds': asdict(run_rate_parent.DEFAULT_POLICY),
                                'audit': audit_payload(audit, displayed_keys)}, impact

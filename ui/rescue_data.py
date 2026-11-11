"""Serialize the rescue report (Action plans · 3M–6M) for the dashboard.

Aggregation by brand / parent / town happens in the page from the batch rows, so
every hierarchy order and search shows sums of the same facts.
"""
from datetime import timedelta
from pathlib import Path

import polars as pl

from src import report
from src.io import matching_files
from src.rescue import (
    PLAN_CATEGORIES,
    batches,
    incumbent_trend,
    lots,
    previous_month_plans,
    remark_outcomes,
    saved_report,
)

COLS = ['batch_index', 'brand', 'parent', 'pdesc', 'town', 'material', 'mdesc', 'batch', 'rsl',
        'exp_kl', 'exp_bdt', 'lnd_kl', 'lnd_bdt', 'cs', 'plan', 'status', 'has_plan']


def shelf_life_facts(data_dir: Path | None) -> tuple[pl.DataFrame | None, pl.DataFrame | None]:
    """Town → Region and batch → cases (CS) from the shelf-life export.

    The report keeps neither column. Batch identity follows soh_db exactly:
    lower-case town + material code + batch code. Either frame is None when
    the export lacks its columns.
    """
    if data_dir is None:
        return None, None
    try:
        paths = matching_files(data_dir, lambda n: 'shelflife' in n or 'Shelflife' in n, excel=False)
        frame = pl.concat([pl.read_csv(p, infer_schema=False) for p in paths], how='diagonal')
    except FileNotFoundError:
        return None, None
    regions = cases = None
    if {'Town', 'Region Name'} <= set(frame.columns):
        regions = frame.drop_nulls('Town').group_by('Town').agg(pl.col('Region Name').drop_nulls().first())
    if {'Town', 'Material Code', 'Batch code', 'CS'} <= set(frame.columns):
        cases = frame.select(
            pl.concat_str(pl.col('Town').str.to_lowercase(),
                          pl.col('Material Code').cast(pl.Int64, strict=False).cast(pl.String),
                          pl.col('Batch code')).alias('batch_index'),
            pl.col('CS').cast(pl.Float64, strict=False).alias('cs'),
        ).group_by('batch_index').agg(pl.col('cs').sum())
    return regions, cases


def _pair(kl, bdt) -> dict:
    return {'kl': float(kl), 'bdt': float(bdt)}


BATCHES_FILE = 'rescue_batches.parquet'


def build_payload(current: pl.DataFrame, history: list[pl.DataFrame], data_dir: Path | None = None,
                  output_dir: Path | None = None) -> dict:
    """Payload for the page; with ``output_dir`` also the batch population plan notes resolve against."""
    snapshot = report.EXCEL_EPOCH + timedelta(days=current.item(0, 'report_date'))
    saved = pl.concat([saved_report(f) for f in history], how='vertical_relaxed')
    plans, lm_date = previous_month_plans(saved, snapshot)
    regions, cases = shelf_life_facts(data_dir)
    rows = batches(current, plans)
    rows = rows.join(cases, on='batch_index', how='left', validate='m:1') if cases is not None \
        else rows.with_columns(pl.lit(None, dtype=pl.Float64).alias('cs'))
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        rows.select('batch_index', pl.col('Brand Name').alias('brand'), pl.col('Material Code').cast(pl.String).alias('material'),
                    pl.col('Town').alias('town'), 'has_plan').write_parquet(output_dir / BATCHES_FILE)
    trend = incumbent_trend(saved)
    outcomes, window = remark_outcomes(saved)
    return {
        'date': snapshot.isoformat(),
        'lm_date': lm_date.isoformat() if lm_date else None,
        'lm_label': lm_date.strftime('%b %Y') if lm_date else 'Last month',
        'cols': COLS,
        'rows': rows.select(
            'batch_index', 'Brand Name', pl.col('parent_material_code').cast(pl.String), 'parent_desc', 'Town',
            pl.col('Material Code').cast(pl.String), 'material_desc', 'Batch code', 'rsl_days',
            'exposure_kl', 'exposure_bdt', 'lnd_fc_kl', 'lnd_fc_bdt', 'cs',
            'lm_action_plan', 'lm_mitigation_status', 'has_plan',
        ).rows(),
        'cases_missing': int(rows['cs'].null_count()),
        'categories': [{'c': c, 'status': st} for c, st in PLAN_CATEGORIES],
        'lots': lots(rows, regions),
        'trend': [{'m': r['cm_date'].strftime('%b'), 'date': r['cm_date'].isoformat(), 'batches': r['batches'],
                   'lm': _pair(r['lm_kl'], r['lm_bdt']), 'cm': _pair(r['cm_kl'], r['cm_bdt'])}
                  for r in trend.iter_rows(named=True)],
        'outcomes': {'window': [d.strftime('%b') for d in window],
                     'rows': [{'c': r['category'], 'n': r['batches'], 'lm': _pair(r['lm_kl'], r['lm_bdt']),
                               'cm': _pair(r['cm_kl'], r['cm_bdt'])} for r in outcomes.iter_rows(named=True)]},
    }

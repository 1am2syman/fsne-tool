"""Not actionable stock from frozen monthly snapshots, with their own prices and labels."""

import polars as pl

from .brand_status import ORDER


def normalise(frame: pl.DataFrame) -> pl.DataFrame:
    """Accept both saved planning schemas; status alone determines the cohort."""
    def text(name: str, legacy: str | None = None) -> pl.Expr:
        source = name if name in frame.columns else legacy
        if source is None or source not in frame.columns:
            return pl.lit(None, dtype=pl.String)
        return pl.col(source).cast(pl.String).str.strip_chars().replace('', None)

    return frame.select(
        text('report_date').str.slice(0, 10).str.to_date(strict=True).alias('date'),
        text('batch_index').alias('batch_index'),
        text('Brand Name').fill_null('Unknown brand').alias('brand'),
        pl.coalesce(text('parent_material_code'), text('Material Code')).fill_null('Unknown parent').alias('parent'),
        text('Material Code').alias('material'),
        text('material_desc').alias('mdesc'),
        text('Town').fill_null('Unknown town').alias('town'),
        text('Batch code').alias('batch'),
        text('rsl_group').fill_null('Unknown').alias('rsl'),
        text('rsl_days').cast(pl.Float64, strict=True).alias('days'),
        text('mitigation_status', 'mitigation_status_category').alias('status'),
        text('action_plan', 'action_plan_remarks').alias('plan'),
        *[pl.col(f'total_closing_stock_{unit}').cast(pl.Float64, strict=True).fill_null(0).alias(f'stock_{unit}')
          for unit in ('bdt', 'kl')],
    ).filter(pl.col('date').is_not_null())


def monthly(history: list[pl.DataFrame]) -> pl.DataFrame:
    """Select the latest whole snapshot per month before filtering any statuses.

    A later snapshot with no Not actionable batches must replace an earlier one.
    Descriptions come from all stock in that snapshot, including parent SKUs that
    are outside the cohort. No report amounts or planning labels are recalculated.
    """
    facts = pl.concat([normalise(frame) for frame in history], how='vertical_relaxed')
    month = pl.col('date').dt.truncate('1mo')
    facts = facts.filter(pl.col('date') == pl.col('date').max().over(month))
    keys = facts.select('date', 'batch_index')
    if facts['batch_index'].null_count() or keys.is_duplicated().any():
        raise ValueError('Monthly snapshot batch_index must be nonblank and unique')
    descriptions = (
        facts.sort('stock_kl', descending=True)
        .group_by('date', 'brand', 'parent')
        .agg(
            pl.col('mdesc').filter(pl.col('material') == pl.col('parent')).drop_nulls().first().alias('own'),
            pl.col('mdesc').drop_nulls().first().alias('fallback'),
        )
        .select('date', 'brand', 'parent', pl.coalesce('own', 'fallback', 'parent').alias('pdesc'))
    )
    return facts.join(descriptions, on=['date', 'brand', 'parent'], how='left', validate='m:1')


def cohort(facts: pl.DataFrame) -> pl.DataFrame:
    """An exact, whitespace-trimmed, case-insensitive status match across every RSL band."""
    return facts.filter(pl.col('status').str.to_lowercase() == 'not actionable')


RSL_GROUPS = ORDER

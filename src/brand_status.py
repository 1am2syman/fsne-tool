"""Saved-report stock, exposure and liquidation by town / brand / parent / RSL.

All stock is included, not just Actionable stock. Each calendar month uses its
latest saved snapshot; missing snapshots remain gaps, never fabricated zeroes.
Percentages are calculated from summed BDT, never averaged from batch percentages.
"""
import polars as pl

ORDER = ['< 3M', '3M - 6M', '6M - 10M', '> 10M']
METRICS = ['stock', 'exposure', 'lnd']
KEYS = ['month', 'town', 'brand', 'parent', 'description', 'rsl']


def normalise(frame: pl.DataFrame, unit: str = "bdt") -> pl.DataFrame:
    def text(name: str) -> pl.Expr:
        return pl.col(name).cast(pl.String).str.strip_chars().replace('', None)

    return frame.select(
        text('report_date').str.slice(0, 10).str.to_date(strict=False).alias('date'),
        text('Town').fill_null('Unknown town').alias('town'),
        text('Brand Name').fill_null('Unknown brand').alias('brand'),
        pl.coalesce(text('parent_material_code'), text('Material Code')).fill_null('Unknown parent').alias('parent'),
        text('Material Code').alias('material'),
        text('material_desc').alias('description'),
        text('rsl_group').fill_null('Unknown').alias('rsl'),
        *[pl.col(source).cast(pl.Float64, strict=True).fill_null(0).alias(target)
          for source, target in zip([f'total_closing_stock_{unit}', f'exposure_{unit}', f'lnd_fc_{unit}'], METRICS, strict=True)],
    ).filter(pl.col('date').is_not_null())


def aggregate(history: pl.DataFrame) -> pl.DataFrame:
    """Latest snapshot per calendar month, with canonical parent descriptions."""
    h = history.with_columns(pl.col('date').dt.truncate('1mo').alias('month'))
    h = h.filter(pl.col('date') == pl.col('date').max().over('month'))
    descriptions = h.sort('date', descending=True).group_by('brand', 'parent').agg(
        pl.col('description').filter(pl.col('material') == pl.col('parent')).drop_nulls().first().alias('own'),
        pl.col('description').drop_nulls().first().alias('fallback'),
    ).select('brand', 'parent', pl.coalesce('own', 'fallback', 'parent').alias('description'))
    return (h.drop('description').join(descriptions, on=['brand', 'parent'])
            .group_by(KEYS).agg(pl.col(METRICS).sum()).sort(KEYS))



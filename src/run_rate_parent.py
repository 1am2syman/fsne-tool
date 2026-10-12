"""Translation of queries/run_rate_parent.txt without pivot materialization."""

from pathlib import Path

import polars as pl

from .io import number
from .secondary import clamped_sales, load


def transform(source: pl.DataFrame) -> pl.DataFrame:
    keys = ["area_name (city)", "parent_material_code"]
    # A global denominator reproduces pivot -> fill null with 0 -> unpivot -> mean.
    # Counting observed months per parent would incorrectly omit missing months.
    monthly = (
        source.lazy()
        .with_columns(clamped_sales().alias("Custom"))
        .group_by(*keys, "Month-Year", maintain_order=True)
        .agg(pl.col("Custom").sum())
    )
    return (
        monthly.group_by(keys, maintain_order=True)
        .agg(pl.col("Custom").sum())
        .join(
            monthly.select(pl.col("Month-Year").n_unique().alias("_months")),
            how="cross",
            maintain_order="left",
        )
        .select(
            pl.col("area_name (city)").cast(pl.String).alias("town"),
            number("parent_material_code", pl.Int64),
            (pl.col("Custom") / pl.col("_months")).alias("avg_sec"),
        )
        .unique(
            subset=["town", "parent_material_code"], keep="first", maintain_order=True
        )
        .collect()
    )


def build(data_dir: str | Path = "data/current", **_: object) -> pl.DataFrame:
    return transform(load(data_dir))


if __name__ == "__main__":
    from .cli import run

    run(build)

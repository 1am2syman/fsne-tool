"""Translation of queries/sec_history.txt."""

from pathlib import Path

import polars as pl

from .cli import run

from .io import date
from .secondary import clamped_sales, load


def transform(source: pl.DataFrame) -> pl.DataFrame:
    return (
        source.lazy()
        .with_columns(clamped_sales().alias("sec_kl"))
        .drop("sec_vol_kl_mth (billwise)", "parent_material_code")
        .with_columns(
            pl.col("area_name (city)")
            .cast(pl.String)
            .str.to_lowercase()
            .str.replace_all(" ", "_", literal=True)
        )
        .with_columns(
            pl.concat_str(
                pl.col("area_name (city)"),
                pl.col("material_code").cast(pl.String),
                pl.col("BATCH_NBR").cast(pl.String),
            ).alias("batch_index"),
            date("Month-Year"),
        )
        .collect()
    )


def build(data_dir: str | Path = "data/current", **_: object) -> pl.DataFrame:
    return transform(load(data_dir))


if __name__ == "__main__":
    run(build)

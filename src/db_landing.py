"""Translation of queries/db_landing.txt."""

from pathlib import Path

import polars as pl

from .cli import run
from .io import matching_files, number, promote_headers, raw_workbooks


def transform(source: pl.DataFrame) -> pl.DataFrame:
    return (
        source.lazy()
        .rename({"Row Labels": "material_code"})
        .filter(~pl.col("material_code").str.contains("Grand|grand"))
        .select(
            number("material_code", pl.Int64),
            number("DB Landing rate").alias("db_landing_rate"),
        )
        .collect()
    )


def build(data_dir: str | Path = "data/current", **_: object) -> pl.DataFrame:
    return transform(
        promote_headers(
            raw_workbooks(
                matching_files(
                    data_dir, lambda name: "landing" in name or "Landing" in name
                ),
                5,
                include_tables=True,
                numeric_header_column="Column4",
            ).filter(pl.col("Column4").is_not_null())
        )
    )


if __name__ == "__main__":
    run(build)

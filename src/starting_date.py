"""Translation of queries/starting_date.txt."""

from pathlib import Path

import polars as pl

from .cli import run
from .io import config_tables, date, matching_files, promote_headers


def transform(source: pl.DataFrame) -> pl.DataFrame:
    # Transpose is a metadata/shape boundary; no row-wise data processing.
    return (
        source.select("variable", "value")
        .transpose()
        .pipe(promote_headers)
        .lazy()
        .with_columns(date("starting_date"))
        .collect()
    )


def build(data_dir: str | Path = "data/current", **_: object) -> pl.DataFrame:
    return transform(
        config_tables(
            matching_files(data_dir, lambda name: "config" in name or "Config" in name)
        )
    )


if __name__ == "__main__":
    run(build)

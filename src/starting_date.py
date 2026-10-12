"""Translation of queries/starting_date.txt."""

from pathlib import Path

import polars as pl

from .cli import run

from .io import config_tables, date, matching_files


def transform(source: pl.DataFrame) -> pl.DataFrame:
    # Transpose is a metadata/shape boundary; no row-wise data processing.
    return (
        source.select("value")
        .transpose(column_names=source.get_column("variable").to_list())
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

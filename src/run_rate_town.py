"""Translation of queries/run_rate_town.txt."""

from pathlib import Path

import polars as pl

from .cli import run

from . import run_rate_parent


def transform(source: pl.DataFrame) -> pl.DataFrame:
    return (
        source.lazy()
        .group_by("town", maintain_order=True)
        .agg(pl.col("avg_sec").sum().alias("avg_sec_town"))
        .collect()
    )


def build(data_dir: str | Path = "data/current", **_: object) -> pl.DataFrame:
    return transform(run_rate_parent.build(data_dir))


if __name__ == "__main__":
    run(build)

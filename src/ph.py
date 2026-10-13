"""Translation of queries/ph.txt."""

from pathlib import Path

import polars as pl

from .cli import run

from .io import number, promote_headers, raw_workbooks, resolve_path


def transform(source: pl.DataFrame) -> pl.DataFrame:
    return (
        source.lazy()
        .select("material_code", "material_desc", "volume", "parent_material_code")
        .with_columns(
            pl.coalesce("parent_material_code", "material_code").alias(
                "parent_material_code"
            )
        )
        .with_columns(
            number("material_code", pl.Int64),
            number("parent_material_code", pl.Int64),
            pl.col("material_desc").cast(pl.String),
        )
        .unique(subset="material_code", keep="first", maintain_order=True)
        .with_columns(number("volume", pl.Int64))
        .collect()
    )


def build(data_dir: str | Path = "data/current", **_: object) -> pl.DataFrame:
    return transform(
        promote_headers(raw_workbooks([resolve_path(data_dir) / "PH_FG.xlsx"], 138))
    )


if __name__ == "__main__":
    run(build)

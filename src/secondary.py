"""Shared source boundary for the two identical secondary-sales M prefixes."""

from pathlib import Path

import polars as pl

from .io import matching_files, number, promote_headers, raw_workbooks


def load(data_dir: str | Path) -> pl.DataFrame:
    return promote_headers(
        raw_workbooks(
            matching_files(data_dir, lambda name: "sec_" in name or "Sec_" in name),
            6,
            sheet="Sheet1",
        ).filter(pl.col("Column3").is_not_null())
    )


def clamped_sales() -> pl.Expr:
    return number("sec_vol_kl_mth (billwise)").clip(lower_bound=0)

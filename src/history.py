"""Translation of queries/history.txt."""

from pathlib import Path

import polars as pl

from .cli import run

from .io import date, matching_files, number, promote_headers, raw_workbooks

TEXT_COLUMNS = [
    "batch_index",
    "Town",
    "Brand Name",
    "material_desc",
    "Batch code",
    "rsl_group",
]
INT_COLUMNS = ["parent_material_code", "Material Code", "Expiry Date", "rsl_days"]
FLOAT_COLUMNS = [
    "total_closing_stock_kl",
    "total_closing_stock_bdt",
    "doh",
    "avg_sec_m",
    "total_pri_sales",
    "total_sec_sales",
    "consumable_kl",
    "consumable_bdt",
    "consumable_kl_2x_rate",
    "rotation_req_kl",
    "rotation_req_bdt",
    "exposure_kl",
    "exposure_bdt",
    "lnd_fc_kl",
    "lnd_fc_bdt",
    "exposure_kl_%",
    "exposure_bdt_%",
    "lnd_fc_kl_%",
    "lnd_fc_bdt_%",
]


def transform(source: pl.DataFrame) -> pl.DataFrame:
    return (
        source.lazy()
        .filter(
            (pl.col("Town") != "Town").fill_null(True),
            pl.col("report_date").is_not_null(),
        )
        .with_columns(
            date("report_date"),
            pl.col(TEXT_COLUMNS).cast(pl.String),
            *[number(name, pl.Int64) for name in INT_COLUMNS],
            *[number(name) for name in FLOAT_COLUMNS],
        )
        # Two rightmost delimiter splits, NOT the first two hyphens.
        .with_columns(
            pl.col("batch_index")
            .str.replace(r"-[^-]*$", "")
            .str.replace(r"-[^-]*$", "")
            .alias("batch_index_short")
        )
        .collect()
    )


def build(
    data_dir: str | Path = "data/current",
    history_dir: str | Path = "data/history",
    **_: object,
) -> pl.DataFrame:
    return transform(
        promote_headers(
            raw_workbooks(
                matching_files(history_dir, lambda name: name.startswith("20")), 32
            )
        )
    )


if __name__ == "__main__":
    run(build)

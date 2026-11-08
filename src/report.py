"""Inferred final report, validated against the supplied sample (not original formulas).

The unusual independent-duration schedule is intentional; see docs/inference.md.
This module never reads the reference workbook or hardcodes its rows or totals.
"""

from datetime import date
from pathlib import Path

import polars as pl

from . import db_landing, soh_db, starting_date
from .cli import run

EXCEL_EPOCH = date(1899, 12, 30)
QUEUE_KEYS = ["Town", "Material Code"]
GROUP_KEYS = [
    "batch_index",
    "Town",
    "Brand Name",
    "parent_material_code",
    "Material Code",
    "material_desc",
    "Batch code",
    "Expiry Date",
]
COLUMNS = [
    "report_date",
    "batch_index",
    "Town",
    "Brand Name",
    "parent_material_code",
    "Material Code",
    "material_desc",
    "Batch code",
    "Expiry Date",
    "rsl_group",
    "rsl_days",
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


def _duration(reserve_days: int, multiplier: int) -> pl.Expr:
    horizon = (pl.col("rsl_days") - reserve_days).clip(lower_bound=0)
    service_days = pl.col("doh") / multiplier
    return pl.min_horizontal(horizon, service_days).fill_null(0)


def _consumption(reserve_days: int, multiplier: int, duration_column: str) -> pl.Expr:
    # Accumulate fractional durations FIRST, then truncate the resulting Excel
    # date serial. Truncating individual durations changes the sample results.
    previous_duration = (
        pl.col(duration_column).cum_sum().shift(1).over(QUEUE_KEYS).fill_null(0)
    )
    start_day = (pl.col("report_date") + previous_duration).floor() - pl.col(
        "report_date"
    )
    available_days = (pl.col("rsl_days") - reserve_days - start_day).clip(lower_bound=0)
    capacity = available_days / 30 * pl.col("avg_sec_m") * multiplier
    return (
        pl.when(pl.col("rsl_days") <= reserve_days)
        .then(0.0)
        .when(pl.col("avg_sec_m").is_null())
        .then(pl.col("total_closing_stock_kl"))
        .otherwise(pl.min_horizontal(pl.col("total_closing_stock_kl"), capacity))
    )


def transform(
    stock: pl.DataFrame, landing: pl.DataFrame, report_date: date
) -> pl.DataFrame:
    """Calculate a report from stock, per-material landing rates and config date.

    Duplicate distributor rows aggregate by batch identity. This grouping and
    equal-expiry tie ordering are extrapolations: the sample has unique keys.
    Dates are Excel serial Int64s to match the reference workbook's cell values.
    """
    if stock.filter(
        (pl.col("Closing Stock") <= 0) | ~pl.col("Closing Stock").is_finite()
    ).height:
        raise ValueError(
            "Closing stock must be positive and finite for the inferred report"
        )
    if stock.filter((pl.col("avg_sec") < 0) | ~pl.col("avg_sec").is_finite()).height:
        raise ValueError(
            "Non-null sales rates must be nonnegative and finite for the inferred report"
        )
    if landing.get_column("material_code").n_unique() != landing.height:
        raise ValueError("Landing rates must have one row per material_code")
    serial = (report_date - EXCEL_EPOCH).days
    return (
        stock.lazy()
        .group_by(GROUP_KEYS, maintain_order=True)
        .agg(
            pl.col("Closing Stock").sum().alias("total_closing_stock_kl"),
            pl.col("NOS").sum(),
            pl.col("avg_sec").first().alias("avg_sec_m"),
            pl.col("Primary Sales").sum().alias("total_pri_sales"),
            pl.col("Secondary Sales").sum().alias("total_sec_sales"),
        )
        .join(
            landing.lazy(),
            left_on="Material Code",
            right_on="material_code",
            how="left",
            validate="m:1",
        )
        .with_columns(
            pl.lit(serial, dtype=pl.Int64).alias("report_date"),
            (pl.col("Expiry Date") - pl.lit(report_date))
            .dt.total_days()
            .alias("rsl_days"),
            (pl.col("NOS") * pl.col("db_landing_rate")).alias(
                "total_closing_stock_bdt"
            ),
            # A known zero rate means unbounded cover. Persist null rather than
            # Infinity for portable Excel/JSON; aggregate UI DOH renders ∞ from
            # the retained zero rate. Zero demand still consumes no stock.
            pl.when(pl.col("avg_sec_m") > 0)
            .then(pl.col("total_closing_stock_kl") / pl.col("avg_sec_m") * 30)
            .alias("doh"),
        )
        .with_columns(
            pl.when(pl.col("rsl_days") <= 90)
            .then(pl.lit("< 3M"))
            .when(pl.col("rsl_days") <= 180)
            .then(pl.lit("3M - 6M"))
            .when(pl.col("rsl_days") <= 300)
            .then(pl.lit("6M - 10M"))
            .otherwise(pl.lit("> 10M"))
            .alias("rsl_group"),
            _duration(300, 1).alias("_duration_300"),
            _duration(300, 2).alias("_duration_300_2x"),
            _duration(90, 1).alias("_duration_90"),
        )
        .sort([*QUEUE_KEYS, "Expiry Date", "batch_index"])
        .with_columns(
            _consumption(300, 1, "_duration_300").alias("consumable_kl"),
            _consumption(300, 2, "_duration_300_2x").alias("consumable_kl_2x_rate"),
            (
                pl.col("total_closing_stock_kl") - _consumption(90, 1, "_duration_90")
            ).alias("lnd_fc_kl"),
        )
        .with_columns(
            (pl.col("total_closing_stock_kl") - pl.col("consumable_kl")).alias(
                "exposure_kl"
            ),
            (pl.col("total_closing_stock_kl") - pl.col("consumable_kl_2x_rate")).alias(
                "rotation_req_kl"
            ),
        )
        .with_columns(
            *[
                (
                    pl.col(quantity)
                    / pl.col("total_closing_stock_kl")
                    * pl.col("total_closing_stock_bdt")
                ).alias(value)
                for quantity, value in [
                    ("consumable_kl", "consumable_bdt"),
                    ("rotation_req_kl", "rotation_req_bdt"),
                    ("exposure_kl", "exposure_bdt"),
                    ("lnd_fc_kl", "lnd_fc_bdt"),
                ]
            ]
        )
        .with_columns(
            *[
                (pl.col(numerator) / pl.col(denominator)).alias(output)
                for numerator, denominator, output in [
                    ("exposure_kl", "total_closing_stock_kl", "exposure_kl_%"),
                    ("exposure_bdt", "total_closing_stock_bdt", "exposure_bdt_%"),
                    ("lnd_fc_kl", "total_closing_stock_kl", "lnd_fc_kl_%"),
                    ("lnd_fc_bdt", "total_closing_stock_bdt", "lnd_fc_bdt_%"),
                ]
            ]
        )
        .with_columns((pl.col("Expiry Date") - pl.lit(EXCEL_EPOCH)).dt.total_days())
        .sort("batch_index")
        .select(COLUMNS)
        .collect()
    )


def build(data_dir: str | Path = "data/current", **_: object) -> pl.DataFrame:
    config = starting_date.build(data_dir)
    if config.height != 1 or config.item(0, "starting_date") is None:
        raise ValueError("config_table must contain one non-null starting_date")
    return transform(
        soh_db.build(data_dir, as_of=config.item(0, "starting_date")),
        db_landing.build(data_dir),
        config.item(0, "starting_date"),
    )


if __name__ == "__main__":
    run(build)

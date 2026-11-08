"""Latest stock cohorts for correctly deduplicated aggregate DOH."""

from datetime import date

import polars as pl

from .io import date as parse_date
from .io import number


def stock_pairs(
    reports: pl.DataFrame, rates: pl.DataFrame, report_date: date, *, actionable_only: bool = True
) -> pl.DataFrame:
    """One stock cell per brand/parent/town/RSL; rates repeat only across RSL.

    Rates are current town-parent inputs, never frozen batch averages. Consumers
    must deduplicate the rate by town + parent AFTER selecting the stock cohort.
    Missing rates stay null rather than silently becoming zero demand.
    Set actionable_only=False for the all-stock town and brand status reports.
    """
    if rates.select("town", "parent_material_code").n_unique() != rates.height:
        raise ValueError("Run rates must be unique by town and parent")
    if rates.filter(
        (pl.col("avg_sec") < 0) | ~pl.col("avg_sec").is_finite()
    ).height:
        raise ValueError("Run rates must be nonnegative and finite or null")
    status = "mitigation_status" if "mitigation_status" in reports.columns else "mitigation_status_category"
    h = reports.lazy().select(
        parse_date("report_date").alias("date"),
        pl.col("Town").cast(pl.String).alias("town"),
        pl.col("Brand Name").cast(pl.String).alias("brand"),
        number("parent_material_code", pl.Int64).alias("parent"),
        pl.col("rsl_group").cast(pl.String).alias("rsl"),
        pl.col(status).cast(pl.String).str.strip_chars().alias("status"),
        number("total_closing_stock_kl").alias("stock_kl"),
    ).filter(
        (pl.col("date") == pl.lit(report_date))
        & (pl.col("status").str.starts_with("Actionable") if actionable_only else pl.lit(True))
    )
    selected = h.collect()
    if selected.is_empty():
        raise ValueError("No saved stock in the requested cohort for the configured report date")
    if selected.filter(
        pl.col("stock_kl").is_null() | ~pl.col("stock_kl").is_finite()
        | (pl.col("stock_kl") < 0)
    ).height:
        raise ValueError("DOH stock must be nonnegative and finite")
    if selected.group_by("town", "parent").agg(
        pl.col("brand").n_unique().alias("brands")
    ).filter(pl.col("brands") > 1).height:
        raise ValueError("A town-parent rate cannot belong to multiple brands")
    return (
        selected.lazy().group_by("brand", "parent", "town", "rsl")
        .agg(pl.col("stock_kl").sum())
        .filter(pl.col("stock_kl") > 0)
        .join(rates.lazy(), left_on=["town", "parent"],
              right_on=["town", "parent_material_code"], how="left", validate="m:1")
        .rename({"avg_sec": "rate"})
        .sort("brand", "parent", "town", "rsl").collect()
    )

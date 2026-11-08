"""Upcoming-risk calendar (Phase A): deterministic warning/expiry events.

Each positive-stock batch gets one row per event: a warning at
``expiry - threshold`` days for every configured threshold, plus its actual
expiry. Warnings are internal thresholds, not commercial selling cutoffs, and
the stock shown is *current* stock attached to the date, not a forecast.
See docs/risk-calendar.md.
"""

from datetime import timedelta
from pathlib import Path

import polars as pl

from . import report
from .cli import run

POLICY_VERSION = "risk-calendar-v1"
THRESHOLDS = (300, 180, 90)
# (key, label, inclusive upper bound in days). Day 0 is due today.
BANDS = (
    ("due_overdue", "Due / overdue", 0),
    ("d1_30", "Days 1–30", 30),
    ("d31_60", "Days 31–60", 60),
    ("d61_90", "Days 61–90", 90),
    ("later", "Later", None),
)
REQUIRED = [
    "report_date",
    "batch_index",
    "Town",
    "Brand Name",
    "parent_material_code",
    "Material Code",
    "material_desc",
    "Batch code",
    "Expiry Date",
    "rsl_days",
    "total_closing_stock_kl",
    "total_closing_stock_bdt",
    "avg_sec_m",
    "exposure_kl",
    "exposure_bdt",
]
EVENT_COLUMNS = [
    "snapshot_id",
    "snapshot_date",
    "batch_id",
    "event_kind",
    "threshold_days",
    "event_date",
    "days_until_event",
    "horizon_band",
    "town",
    "brand",
    "parent_material_code",
    "material_code",
    "material_desc",
    "batch_code",
    "expiry_date",
    "rsl_days",
    "stock_kl",
    "stock_bdt",
    "exposure_kl",
    "exposure_bdt",
    "value_known",
    "demand_known",
    "policy_version",
]


def band(days: pl.Expr) -> pl.Expr:
    """Mutually exclusive horizon band for days until an event."""
    result = pl.when(days <= BANDS[0][2]).then(pl.lit(BANDS[0][0]))
    for key, _, upper in BANDS[1:-1]:
        result = result.when(days <= upper).then(pl.lit(key))
    return result.otherwise(pl.lit(BANDS[-1][0]))


def transform(
    batches: pl.DataFrame, thresholds: tuple[int, ...] = THRESHOLDS
) -> pl.DataFrame:
    """One row per batch and event, for the complete current positive stock.

    ``batches`` is the inferred report (dates as Excel serials). Nothing is
    dropped: past events are kept, unpriced stock keeps a null value and
    missing rates stay visible as ``demand_known = False``.
    """
    missing = [c for c in REQUIRED if c not in batches.columns]
    if missing:
        raise ValueError(f"Report is missing columns: {missing}")
    if not thresholds or len(set(thresholds)) != len(thresholds):
        raise ValueError("Thresholds must be unique and non-empty")
    if any(t <= 0 for t in thresholds):
        raise ValueError("Thresholds must be positive days")
    if batches.height == 0:
        raise ValueError("Report has no batches")
    if batches.get_column("report_date").n_unique() != 1:
        raise ValueError("Report must contain exactly one snapshot date")
    if batches.get_column("report_date").null_count():
        raise ValueError("Snapshot date must not be null")
    if batches.get_column("batch_index").n_unique() != batches.height:
        raise ValueError("batch_index must uniquely identify a batch")
    if batches.filter(
        pl.col("total_closing_stock_kl").is_null()
        | (pl.col("total_closing_stock_kl") <= 0)
        | ~pl.col("total_closing_stock_kl").is_finite()
    ).height:
        raise ValueError("Stock must be positive and finite")
    if batches.get_column("Expiry Date").null_count():
        raise ValueError("Every batch needs an expiry date")

    epoch = report.EXCEL_EPOCH
    snapshot = epoch + timedelta(days=batches.item(0, "report_date"))
    base = batches.select(
        pl.lit(snapshot.isoformat()).alias("snapshot_id"),
        pl.lit(snapshot, dtype=pl.Date).alias("snapshot_date"),
        pl.col("batch_index").alias("batch_id"),
        pl.col("Town").alias("town"),
        pl.col("Brand Name").alias("brand"),
        pl.col("parent_material_code"),
        pl.col("Material Code").alias("material_code"),
        pl.col("material_desc"),
        pl.col("Batch code").alias("batch_code"),
        (pl.lit(epoch, dtype=pl.Date) + pl.duration(days=pl.col("Expiry Date"))).alias(
            "expiry_date"
        ),
        pl.col("rsl_days"),
        pl.col("total_closing_stock_kl").alias("stock_kl"),
        pl.col("total_closing_stock_bdt").alias("stock_bdt"),
        pl.col("exposure_kl"),
        pl.col("exposure_bdt"),
        pl.col("total_closing_stock_bdt").is_not_null().alias("value_known"),
        pl.col("avg_sec_m").is_not_null().alias("demand_known"),
    )
    if base.filter(
        (pl.col("expiry_date") - pl.col("snapshot_date")).dt.total_days()
        != pl.col("rsl_days")
    ).height:
        raise ValueError("rsl_days disagrees with expiry date and snapshot date")

    kinds = [
        base.with_columns(
            pl.lit("warning").alias("event_kind"),
            pl.lit(t, dtype=pl.Int64).alias("threshold_days"),
            pl.lit(order, dtype=pl.Int64).alias("_order"),
        )
        for order, t in enumerate(sorted(thresholds, reverse=True))
    ] + [
        base.with_columns(
            pl.lit("expiry").alias("event_kind"),
            pl.lit(None, dtype=pl.Int64).alias("threshold_days"),
            pl.lit(len(thresholds), dtype=pl.Int64).alias("_order"),
        )
    ]
    return (
        pl.concat(kinds)
        .with_columns(
            (
                pl.col("expiry_date")
                - pl.duration(days=pl.col("threshold_days").fill_null(0))
            ).alias("event_date")
        )
        .with_columns(
            (pl.col("event_date") - pl.col("snapshot_date"))
            .dt.total_days()
            .alias("days_until_event")
        )
        .with_columns(
            band(pl.col("days_until_event")).alias("horizon_band"),
            pl.lit(POLICY_VERSION).alias("policy_version"),
        )
        .sort(["event_date", "_order", "batch_id"])
        .select(EVENT_COLUMNS)
    )


def summarise(
    events: pl.DataFrame, thresholds: tuple[int, ...] = THRESHOLDS
) -> pl.DataFrame:
    """Coverage/metadata artifact: one row per event type and horizon band.

    Bands within one event type are additive. Rows of different event types
    describe the same stock and must not be added together. Exposure is shown
    separately and overlaps stock; never sum it with stock.
    """
    grid = pl.DataFrame(
        [
            {"event_kind": kind, "threshold_days": t, "horizon_band": key}
            for kind, t in [("warning", x) for x in sorted(thresholds, reverse=True)]
            + [("expiry", None)]
            for key, _, _ in BANDS
        ],
        schema={
            "event_kind": pl.String,
            "threshold_days": pl.Int64,
            "horizon_band": pl.String,
        },
    )
    keys = ["event_kind", "threshold_days", "horizon_band"]
    stats = events.group_by(keys).agg(
        pl.col("batch_id").n_unique().alias("batches"),
        pl.col("stock_kl").sum().alias("stock_kl"),
        pl.col("stock_bdt").sum().alias("known_value_bdt"),
        pl.col("stock_kl").filter(~pl.col("value_known")).sum().alias("unpriced_kl"),
        (~pl.col("value_known")).sum().alias("unpriced_batches"),
        pl.col("stock_kl").filter(~pl.col("demand_known")).sum().alias("no_rate_kl"),
        (~pl.col("demand_known")).sum().alias("no_rate_batches"),
        pl.col("exposure_kl").sum().alias("exposure_kl"),
        pl.col("exposure_bdt").sum().alias("exposure_bdt"),
    )
    zero = ["batches", "stock_kl", "known_value_bdt", "unpriced_kl", "unpriced_batches"]
    zero += ["no_rate_kl", "no_rate_batches", "exposure_kl", "exposure_bdt"]
    order = {key: i for i, (key, _, _) in enumerate(BANDS)}
    return (
        grid.join(stats, on=keys, how="left", nulls_equal=True)
        .with_columns(pl.col(zero).fill_null(0))
        .with_columns(
            pl.lit(events.item(0, "snapshot_id")).alias("snapshot_id"),
            pl.lit(POLICY_VERSION).alias("policy_version"),
            pl.col("horizon_band").replace_strict(order).alias("_band"),
        )
        .sort(["event_kind", "threshold_days", "_band"], descending=[True, True, False])
        .drop("_band")
    )


def build(data_dir: str | Path = "data/current", **_: object) -> pl.DataFrame:
    return transform(report.build(data_dir))


def build_summary(data_dir: str | Path = "data/current", **_: object) -> pl.DataFrame:
    return summarise(build(data_dir))


if __name__ == "__main__":
    run(build)

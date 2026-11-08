"""Seasonality-aware monthly secondary-sales rates, shared by all consumers.

Raw sales are never rewritten. See docs/run-rates.md for the conservative policy
and the limitations of estimating seasonal patterns from a single annual cycle.
"""

from dataclasses import dataclass
from datetime import date as Date
from math import isfinite
from pathlib import Path

import polars as pl

from .cli import run
from .io import date, number
from .secondary import clamped_sales, load

KEYS = ["town", "parent_material_code"]


@dataclass(frozen=True)
class WinsorPolicy:
    """Evidence gates and upper-tail bounds; low/zero demand stays untouched."""

    min_window_months: int = 12
    min_observed_months: int = 8
    min_positive_months: int = 6
    min_peer_towns: int = 10
    robust_sigma: float = 3.0
    min_upper_ratio: float = 2.0

    def __post_init__(self) -> None:
        if min(self.min_window_months, self.min_observed_months,
               self.min_positive_months, self.min_peer_towns) < 1:
            raise ValueError("Evidence thresholds must be positive")
        if not (isfinite(self.robust_sigma) and self.robust_sigma > 0) or not (
            isfinite(self.min_upper_ratio) and self.min_upper_ratio > 1
        ):
            raise ValueError("Winsor bounds must be finite and conservative")


DEFAULT_POLICY = WinsorPolicy()


def monthly_sales(source: pl.DataFrame) -> pl.DataFrame:
    """Clamp negative source rows, then aggregate batches/children by month."""
    monthly = (
        source.lazy()
        .select(
            pl.col("area_name (city)").cast(pl.String).alias("town"),
            number("parent_material_code", pl.Int64),
            date("Month-Year").dt.truncate("1mo").alias("month"),
            clamped_sales().alias("raw_kl"),
        )
        .group_by(*KEYS, "month", maintain_order=True)
        .agg(pl.col("raw_kl").sum())
        .collect()
    )
    if monthly.is_empty() or monthly["month"].null_count():
        raise ValueError("Secondary sales need at least one valid month")
    if monthly.filter(~pl.col("raw_kl").is_finite()).height:
        raise ValueError("Monthly secondary sales must be finite")
    return monthly


def _peer_seasons(monthly: pl.LazyFrame, policy: WinsorPolicy) -> pl.LazyFrame:
    """Exact leave-one-town-out median without a quadratic peer self-join.

    Removing a ranked item shifts the two median indices by at most one. The
    sorted peer list is shared per product/month; the evaluated town cannot
    inflate its own expected seasonal demand, even at the median boundary.
    """
    group = ["parent_material_code", "month"]
    peers = (
        monthly.filter(pl.col("_eligible"))
        .with_columns((pl.col("raw_kl") / pl.col("_baseline")).alias("_ratio"))
        .with_columns(pl.col("_ratio").rank("ordinal").over(group).alias("_rank"))
    )
    lists = peers.group_by(group).agg(pl.col("_ratio").sort().alias("_ratios"))
    peers = peers.join(lists, on=group).with_columns(
        (pl.col("_ratios").list.len().cast(pl.Int64) - 1).alias("peer_towns"),
        (pl.col("_rank").cast(pl.Int64) - 1).alias("_removed"),
    )
    # Median positions in the list AFTER removal (zero-based).
    peers = peers.with_columns(
        ((pl.col("peer_towns") - 1) // 2).clip(lower_bound=0).alias("_lo"),
        (pl.col("peer_towns") // 2).alias("_hi"),
    )
    return peers.select(
        *KEYS, "month", "peer_towns",
        pl.when(pl.col("peer_towns") >= policy.min_peer_towns)
        .then((
            pl.col("_ratios").list.get(
                pl.col("_lo") + (pl.col("_removed") <= pl.col("_lo")).cast(pl.Int64),
                null_on_oob=True,
            )
            + pl.col("_ratios").list.get(
                pl.col("_hi") + (pl.col("_removed") <= pl.col("_hi")).cast(pl.Int64),
                null_on_oob=True,
            )
        ) / 2)
        .alias("season_factor"),
    )


def adjustments(
    source: pl.DataFrame, policy: WinsorPolicy = DEFAULT_POLICY
) -> pl.DataFrame:
    """Auditable monthly winsorization: isolate unexplained positive spikes.

    Seasonality is a robust, town-size-normalized product/month consensus, not
    a claimed recurring seasonal model. Both neighboring calendar months must
    support an isolated spike; boundary peaks and sustained changes are flagged
    and preserved. Missing pair-months stay implicit zeroes in the rate divisor.
    """
    monthly = monthly_sales(source)
    window = monthly["month"].n_unique()
    m = monthly.lazy().with_columns(
        pl.len().over(KEYS).alias("observed_months"),
        (pl.col("raw_kl") > 0).sum().over(KEYS).alias("positive_months"),
        pl.col("raw_kl").filter(pl.col("raw_kl") > 0).median().over(KEYS).alias("_baseline"),
    ).with_columns(
        (pl.lit(window >= policy.min_window_months)
         & (pl.col("observed_months") >= policy.min_observed_months)
         & (pl.col("positive_months") >= policy.min_positive_months)
         & pl.col("town").is_not_null()
         & pl.col("parent_material_code").is_not_null()).alias("_eligible")
    )
    m = m.join(_peer_seasons(m, policy), on=[*KEYS, "month"], how="left").with_columns(
        (pl.col("_baseline") * pl.col("season_factor")).alias("expected_kl"),
        (pl.col("month").dt.year() * 12 + pl.col("month").dt.month()).alias("_month_index"),
    ).with_columns(
        pl.when(pl.col("expected_kl") > 0)
        .then(pl.col("raw_kl") / pl.col("expected_kl")).alias("_residual")
    ).with_columns(
        pl.col("_residual").filter(pl.col("_residual") > 0).median().over(KEYS).alias("_center"),
        (pl.col("_residual") > 0).sum().over(KEYS).alias("_residual_months"),
    ).with_columns(
        (pl.col("_residual") - pl.col("_center")).abs()
        .filter(pl.col("_residual") > 0).median().over(KEYS).alias("_mad")
    ).with_columns(
        pl.max_horizontal(
            pl.col("_center") + policy.robust_sigma * 1.4826 * pl.col("_mad"),
            policy.min_upper_ratio * pl.col("_center"),
        ).alias("upper_residual"),
        pl.col("_residual").shift(1).over(KEYS, order_by="month").alias("_prev"),
        pl.col("_residual").shift(-1).over(KEYS, order_by="month").alias("_next"),
        pl.col("_month_index").shift(1).over(KEYS, order_by="month").alias("_prev_month"),
        pl.col("_month_index").shift(-1).over(KEYS, order_by="month").alias("_next_month"),
    ).with_columns(
        (pl.col("expected_kl") * pl.col("upper_residual")).alias("upper_kl"),
        ((pl.col("_residual") > pl.col("upper_residual"))
         & (pl.col("_residual_months") >= policy.min_positive_months)).fill_null(False).alias("_candidate"),
        ((pl.col("_prev") > 0) & (pl.col("_next") > 0)
         & (pl.col("_prev") <= pl.col("upper_residual"))
         & (pl.col("_next") <= pl.col("upper_residual"))
         & (pl.col("_prev_month") == pl.col("_month_index") - 1)
         & (pl.col("_next_month") == pl.col("_month_index") + 1))
        .fill_null(False).alias("_isolated"),
    ).with_columns(
        pl.when(pl.col("raw_kl") == 0).then(pl.lit("zero_preserved"))
        .when(~pl.col("_eligible")).then(pl.lit("insufficient_history"))
        .when(pl.col("season_factor").is_null() | (pl.col("season_factor") <= 0))
        .then(pl.lit("insufficient_peers"))
        .when(pl.col("_residual_months") < policy.min_positive_months)
        .then(pl.lit("insufficient_residual_history"))
        .when(pl.col("_candidate") & pl.col("_isolated")).then(pl.lit("winsorized"))
        .when(pl.col("_candidate")).then(pl.lit("ambiguous_peak_preserved"))
        .otherwise(pl.lit("unchanged")).alias("reason")
    ).with_columns(
        pl.when(pl.col("reason") == "winsorized").then(pl.col("upper_kl"))
        .otherwise(pl.col("raw_kl")).alias("adjusted_kl")
    )
    m = m.with_columns(
        (pl.col('_baseline') * pl.col('upper_residual')).alias('unseasoned_upper_kl'),
        # Evidence tag only: sales that exceed the no-season bound but remain
        # within the season-adjusted bound are allowed because of peer uplift.
        ((pl.col('reason') == 'unchanged')
         & (pl.col('_residual_months') >= policy.min_positive_months)
         & (pl.col('season_factor') > 1)
         & (pl.col('raw_kl') > pl.col('_baseline') * pl.col('upper_residual'))
         & (pl.col('raw_kl') <= pl.col('upper_kl')))
        .fill_null(False).alias('seasonal_supported'),
    )
    return m.select(
        *KEYS, "month", "raw_kl", "adjusted_kl", "expected_kl", "season_factor",
        "upper_kl", "upper_residual", "peer_towns", "observed_months", "positive_months", "reason",
        pl.col('_baseline').alias('baseline_kl'), 'unseasoned_upper_kl', 'seasonal_supported',
    ).sort([*KEYS, "month"]).collect()


def rates_from_adjustments(audit: pl.DataFrame) -> pl.DataFrame:
    """Zero-filled global month divisor, with one row per town/parent."""
    return audit.group_by(KEYS, maintain_order=True).agg(
        (pl.col("adjusted_kl").sum() / audit["month"].n_unique()).alias("avg_sec")
    )


def raw_rates(source: pl.DataFrame) -> pl.DataFrame:
    """Unadjusted baseline for audit/reference comparison, not production use."""
    monthly = monthly_sales(source)
    return monthly.group_by(KEYS, maintain_order=True).agg(
        (pl.col("raw_kl").sum() / monthly["month"].n_unique()).alias("avg_sec")
    )


def transform(source: pl.DataFrame, policy: WinsorPolicy = DEFAULT_POLICY) -> pl.DataFrame:
    return rates_from_adjustments(adjustments(source, policy))


def source_through(source: pl.DataFrame, as_of: Date | None) -> pl.DataFrame:
    """Exclude future sales months; monthly data cannot resolve partial months."""
    if as_of is None:
        return source
    return source.filter(date("Month-Year").dt.truncate("1mo") <= as_of.replace(day=1))


def build(
    data_dir: str | Path = "data/current", as_of: Date | None = None, **_: object
) -> pl.DataFrame:
    return transform(source_through(load(data_dir), as_of))


if __name__ == "__main__":
    run(build)

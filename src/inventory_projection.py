"""Deterministic no-intervention depletion scenarios (risk calendar Phase B).

Shared projection boundary for the risk calendar and later reports. Not part of
``report.py`` and unrelated to its child-queue schedule, which repeats parent
rates per child to match the reference workbook.

Policy (approved, see docs/risk-calendar.md):
- pool = (town, child material); children are NOT interchangeable;
- the town-parent run rate is allocated ONCE, split across children by each
  child's share of the parent's historical secondary sales in the same window;
- within a pool sales consume the earliest expiry first; equal expiries go to the
  earliest received batch (first come, first served), then batch id;
- sales happen on days 0..rsl-1 after the snapshot: none on the expiry date
  itself (the report's usable days are ``rsl_days``);
- horizon 90 days from the snapshot; scenarios scale the rate (1x, 2x);
- no intervention, receipts or returns are modelled. Unsold stock stays put.
Nothing here is a deadline or lead time; none exist as inputs.
"""

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import polars as pl

from . import report, risk_calendar, soh_db, starting_date
from .cli import run
from .io import date as parse_date
from .io import number
from .run_rate_parent import source_through
from .secondary import clamped_sales, load

POLICY_VERSION = "inventory-projection-v1"
ALLOCATION_POLICY = (
    "earliest-expiry-first within town+child pool; ties by earliest receipt then "
    "batch id; children not interchangeable; parent rate split by child sales share"
)
DEMAND_BASIS = (
    "adjusted town-parent run rate (src/run_rate_parent) x child share of "
    "parent secondary sales over the same window"
)
HORIZON_DAYS = 90
DAYS_PER_MONTH = 30  # same month convention as report.py
SCENARIOS = (("baseline", 1.0), ("rate_2x", 2.0))
EPSILON = 1e-12
SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "scenario_id": pl.String,
    "rate_multiplier": pl.Float64,
    "forecast_origin": pl.Date,
    "demand_basis": pl.String,
    "allocation_policy": pl.String,
    "batch_id": pl.String,
    "town": pl.String,
    "material_code": pl.Int64,
    "batch_code": pl.String,
    "event_kind": pl.String,
    "threshold_days": pl.Int64,
    "event_date": pl.Date,
    "days_until_event": pl.Int64,
    "horizon_days": pl.Int64,
    "opening_kl": pl.Float64,
    "projected_remaining_kl": pl.Float64,
    "projected_value_bdt": pl.Float64,
    "coverage_status": pl.String,
    "child_rate_kl_month": pl.Float64,
    "policy_version": pl.String,
}


def child_shares(source: pl.DataFrame) -> pl.DataFrame:
    """Each child's share of its town-parent's secondary sales (clamped, same window)."""
    return (
        source.lazy()
        .select(
            pl.col("area_name (city)").cast(pl.String).alias("town"),
            number("parent_material_code", pl.Int64),
            number("material_code", pl.Int64),
            clamped_sales().alias("kl"),
        )
        .group_by("town", "parent_material_code", "material_code")
        .agg(pl.col("kl").sum())
        .with_columns(
            pl.col("kl").sum().over("town", "parent_material_code").alias("_parent")
        )
        .filter(pl.col("_parent") > 0)
        .select(
            "town",
            "parent_material_code",
            "material_code",
            (pl.col("kl") / pl.col("_parent")).alias("share"),
        )
        .collect()
    )


def received_dates(stock: pl.DataFrame) -> pl.DataFrame:
    """Earliest primary receipt per batch (the first-come-first-served tie-break)."""
    # The source text keeps a leading apostrophe (only Expiry Date is cleaned upstream).
    return (
        stock.select(
            "batch_index",
            pl.col("Primary First Date").str.replace_all("'", "", literal=True),
        )
        .with_columns(parse_date("Primary First Date"))
        .group_by("batch_index")
        .agg(pl.col("Primary First Date").min().alias("received_date"))
    )


def deplete(
    pool: list[tuple[str, int, float, date | None]],
    rate_kl_day: float | Sequence[float],
    horizon: int = HORIZON_DAYS,
) -> dict[str, list[float]]:
    """Remaining stock after each day 0..horizon for one pool.

    ``pool`` holds (batch_id, rsl_days, opening_kl, received_date). Day k sales
    (0 <= k < horizon) go to unexpired batches (rsl > k) in priority order and
    never exceed the daily rate; no sales look ahead and nothing goes negative.
    ``rate_kl_day`` may be a per-day sequence of length ``horizon`` (sales-push
    scenarios); a float is the same rate every day.
    """
    if not isinstance(rate_kl_day, int | float) and len(rate_kl_day) != horizon:
        raise ValueError("A per-day rate profile needs one rate per horizon day")
    order = sorted(pool, key=lambda b: (b[1], b[3] is None, b[3] or date.max, b[0]))
    left = [b[2] for b in order]
    path = {b[0]: [b[2]] for b in order}
    for day in range(horizon):
        demand = rate_kl_day if isinstance(rate_kl_day, int | float) else rate_kl_day[day]
        for i, batch in enumerate(order):
            if demand <= EPSILON:
                break
            if batch[1] <= day or left[i] <= EPSILON:
                continue
            take = min(left[i], demand)
            left[i] -= take
            demand -= take
        for i, batch in enumerate(order):
            path[batch[0]].append(left[i])
    return path


def transform(
    batches: pl.DataFrame,
    shares: pl.DataFrame,
    received: pl.DataFrame | None = None,
    scenarios: tuple[tuple[str, float], ...] = SCENARIOS,
    horizon: int = HORIZON_DAYS,
) -> pl.DataFrame:
    """One row per scenario, batch and risk-calendar event (modelled stock)."""
    if any(m <= 0 for _, m in scenarios) or len({s for s, _ in scenarios}) != len(
        scenarios
    ):
        raise ValueError("Scenarios need unique ids and positive multipliers")
    if horizon <= 0:
        raise ValueError("Horizon must be positive")
    parent = ["town", "parent_material_code"]
    if (
        shares.filter(
            (pl.col("share") < 0) | (pl.col("share") > 1) | ~pl.col("share").is_finite()
        ).height
        or shares.group_by(parent)
        .agg(pl.col("share").sum().alias("total"))
        .filter(pl.col("total") > 1 + 1e-9)
        .height
    ):
        raise ValueError("Child shares must be within 0..1 and sum to at most 1 per parent")
    if shares.select(*parent, "material_code").is_duplicated().any():
        raise ValueError("Child shares must be unique per town, parent and child")
    events = risk_calendar.transform(batches)
    stock = batches.join(
        shares,
        left_on=["Town", "parent_material_code", "Material Code"],
        right_on=["town", "parent_material_code", "material_code"],
        how="left",
        validate="m:1",
    )
    if received is None:
        stock = stock.with_columns(pl.lit(None, dtype=pl.Date).alias("received_date"))
    else:
        stock = stock.join(received, on="batch_index", how="left", validate="1:1")
    # Unknown parent rate -> unknown projection. A known parent rate with no child
    # sales in the window gives the child a known zero rate, not the parent's rate.
    stock = stock.with_columns(
        pl.when(pl.col("avg_sec_m").is_null())
        .then(None)
        .otherwise(pl.col("avg_sec_m") * pl.col("share").fill_null(0.0))
        .alias("_child_rate")
    )
    # The greedy queue is inherently sequential, so only it runs in Python.
    pools: dict[tuple[str, int], list] = {}
    rate: dict[tuple[str, int], float | None] = {}
    columns = ["Town", "Material Code", "batch_index", "rsl_days",
               "total_closing_stock_kl", "received_date", "_child_rate"]
    for town, material, batch, rsl, kl, received_on, child_rate in stock.select(columns).rows():
        pools.setdefault((town, material), []).append((batch, rsl, kl, received_on))
        rate[town, material] = child_rate
    ids: list[str] = []
    scenario_ids: list[str] = []
    paths: list[list[float] | None] = []
    for scenario, multiplier in scenarios:
        for key, pool in pools.items():
            monthly = rate[key]
            solved = (
                {b[0]: None for b in pool}
                if monthly is None
                else deplete(pool, monthly * multiplier / DAYS_PER_MONTH, horizon)
            )
            ids.extend(solved)
            scenario_ids.extend([scenario] * len(solved))
            paths.extend(solved.values())
    solved_frame = pl.DataFrame(
        {"scenario_id": scenario_ids, "batch_id": ids, "_path": paths},
        schema={"scenario_id": pl.String, "batch_id": pl.String, "_path": pl.List(pl.Float64)},
    )
    multipliers = pl.DataFrame(
        scenarios, schema={"scenario_id": pl.String, "rate_multiplier": pl.Float64}, orient="row"
    )
    batch_info = stock.select(
        pl.col("batch_index").alias("batch_id"),
        pl.col("total_closing_stock_kl").alias("opening_kl"),
        pl.col("total_closing_stock_bdt").alias("_bdt"),
        pl.col("_child_rate"),
    )
    days = pl.col("days_until_event")
    remaining = (
        pl.when(days <= 0)
        .then(pl.col("opening_kl"))
        .when(pl.col("_path").is_null() | (days > horizon))
        .then(None)
        .otherwise(pl.col("_path").list.get(days.clip(0, horizon), null_on_oob=True))
    )
    return (
        events.join(multipliers, how="cross")
        .join(solved_frame, on=["scenario_id", "batch_id"], how="left", validate="m:1")
        .join(batch_info, on="batch_id", how="left", validate="m:1")
        .with_columns(
            pl.when(days <= 0)
            .then(pl.lit("observed_past_event"))
            .when(pl.col("_path").is_null())
            .then(pl.lit("unknown_no_rate"))
            .when(days > horizon)
            .then(pl.lit("beyond_horizon"))
            .otherwise(pl.lit("modeled"))
            .alias("coverage_status"),
            remaining.alias("projected_remaining_kl"),
            pl.col("snapshot_date").alias("forecast_origin"),
            pl.lit(DEMAND_BASIS).alias("demand_basis"),
            pl.lit(ALLOCATION_POLICY).alias("allocation_policy"),
            pl.lit(horizon, dtype=pl.Int64).alias("horizon_days"),
            (pl.col("_child_rate") * pl.col("rate_multiplier")).alias("child_rate_kl_month"),
            pl.lit(POLICY_VERSION).alias("policy_version"),
        )
        .with_columns(
            (pl.col("projected_remaining_kl") / pl.col("opening_kl") * pl.col("_bdt")).alias(
                "projected_value_bdt"
            )
        )
        .select(list(SCHEMA))
        .sort(["scenario_id", "event_date", "batch_id"])
    )


def build(data_dir: str | Path = "data/current", **_: object) -> pl.DataFrame:
    config = starting_date.build(data_dir)
    if config.height != 1 or config.item(0, "starting_date") is None:
        raise ValueError("config_table must contain one non-null starting_date")
    as_of = config.item(0, "starting_date")
    stock = soh_db.build(data_dir, as_of=as_of)
    batches = report.transform(stock, as_of)
    shares = child_shares(source_through(load(data_dir), as_of))
    return transform(batches, shares, received_dates(stock))


if __name__ == "__main__":
    run(build)

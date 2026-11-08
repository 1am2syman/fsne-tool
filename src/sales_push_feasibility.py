"""Sales-push feasibility (Phase A): required pace and deterministic scenarios.

Answers "what selling pace would clearing this stock by a chosen date need, and
what happens under a few explicit uplift assumptions?". It is arithmetic over the
shared projection engine (``inventory_projection.deplete``): same pools, same
earliest-expiry-first queue, same once-allocated demand. It does not forecast
demand uplift, estimate a probability of success or value anything (economics is
Phase B, see docs/sales-push-feasibility.md).

Conventions (v1):
- ``deadline_days`` is a whole number of calendar days after the snapshot. Sales
  happen on days 0..deadline-1; the horizon ends at the deadline. The deadline is
  a labelled ``deadline_kind`` -- by default a user-selected *planning target*.
  The 300/90-day internal warnings are not commercial cutoffs and are not used.
- Batches still stop selling on their own expiry day (actual expiry).
- Uplift multiplies the baseline rate only on days ``delay <= day < delay +
  duration`` (``duration=None`` runs to the deadline). It is an assumption.
- The minimum required uplift is found by bisection over ``[1, MAX_UPLIFT]``.
  Residual stock never rises with the multiplier, so bisection is valid; the
  answer is approximate to ``UPLIFT_TOLERANCE`` and is reported as such.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from . import db_landing, report, soh_db, starting_date
from .cli import run
from .inventory_projection import (
    DAYS_PER_MONTH,
    EPSILON,
    child_shares,
    deplete,
    received_dates,
)
from .run_rate_parent import KEYS, adjustments, source_through
from .secondary import load

POLICY_VERSION = "sales-push-feasibility-v1"
DEADLINE_KINDS = ("planning_target", "commercial_acceptance", "actual_expiry")
DEFAULT_DEADLINE_KIND = "planning_target"
DEFAULT_DEADLINE_DAYS = 90
MAX_UPLIFT = 20.0
UPLIFT_TOLERANCE = 1e-4
RESIDUAL_TOLERANCE_KL = 1e-6
DAYS_PER_WEEK = 7
RATE_BASIS = (
    "adjusted town-parent run rate x child share of parent secondary sales "
    "(historical sales, not unconstrained demand)"
)


@dataclass(frozen=True)
class Scenario:
    """One named intervention assumption (``multiplier`` is entered, not inferred)."""

    scenario_id: str
    multiplier: float = 1.0
    delay_days: int = 0
    duration_days: int | None = None

    def __post_init__(self) -> None:
        if not self.scenario_id or self.multiplier <= 0 or self.delay_days < 0:
            raise ValueError("Scenario needs an id, positive multiplier and delay >= 0")
        if self.duration_days is not None and self.duration_days <= 0:
            raise ValueError("Scenario duration must be positive")


SCENARIOS = (
    Scenario("no_action"),
    Scenario("uplift_1_5x", 1.5),
    Scenario("uplift_2x", 2.0),
    Scenario("uplift_2x_delay_14d", 2.0, delay_days=14),
    Scenario("uplift_2x_first_30d", 2.0, duration_days=30),
)

SCENARIO_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "forecast_origin": pl.Date,
    "town": pl.String,
    "material_code": pl.Int64,
    "scenario_id": pl.String,
    "deadline_kind": pl.String,
    "deadline_days": pl.Int64,
    "deadline_date": pl.Date,
    "uplift_multiplier": pl.Float64,
    "delay_days": pl.Int64,
    "duration_days": pl.Int64,
    "target_batches": pl.Int64,
    "target_kl": pl.Float64,
    "queue_ahead_kl": pl.Float64,
    "baseline_kl_week": pl.Float64,
    "projected_sales_kl": pl.Float64,
    "projected_residual_kl": pl.Float64,
    "reason_codes": pl.String,
    "rate_basis": pl.String,
    "policy_version": pl.String,
}
REQUIREMENT_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "forecast_origin": pl.Date,
    "town": pl.String,
    "material_code": pl.Int64,
    "deadline_kind": pl.String,
    "deadline_days": pl.Int64,
    "deadline_date": pl.Date,
    "target_batches": pl.Int64,
    "target_kl": pl.Float64,
    "queue_ahead_kl": pl.Float64,
    "baseline_kl_week": pl.Float64,
    "baseline_sales_kl": pl.Float64,
    "simple_required_kl_week": pl.Float64,
    "required_total_kl_week": pl.Float64,
    "required_incremental_kl_week": pl.Float64,
    "required_uplift": pl.Float64,
    "hist_window_months": pl.Int64,
    "hist_observed_months": pl.Int64,
    "hist_calendar_gap_months": pl.Int64,
    "hist_recent_months_observed": pl.Int64,
    "hist_recent_kl_week": pl.Float64,
    "hist_peak_kl_week": pl.Float64,
    "hist_peak_month": pl.Date,
    "hist_adjusted_months": pl.Int64,
    "hist_ambiguous_peak_months": pl.Int64,
    "hist_comparison": pl.String,
    "hist_caveats": pl.String,
    "solver_status": pl.String,
    "search_max_uplift": pl.Float64,
    "uplift_tolerance": pl.Float64,
    "review_state": pl.String,
    "reason_codes": pl.String,
    "rate_basis": pl.String,
    "policy_version": pl.String,
}


@dataclass(frozen=True)
class Pool:
    """A town + child SKU demand pool: queue entries are (batch_id, rsl, kl, received)."""

    town: str
    material_code: int
    entries: list[tuple[str, int, float, date | None]]
    monthly_rate_kl: float | None
    parent_code: int | None = None

    @property
    def day_rate(self) -> float | None:
        return None if self.monthly_rate_kl is None else self.monthly_rate_kl / DAYS_PER_MONTH


def rate_profile(day_rate: float, scenario: Scenario, horizon: int) -> list[float]:
    """Per-day sales capacity: baseline, multiplied inside the active interval."""
    end = horizon if scenario.duration_days is None else scenario.delay_days + scenario.duration_days
    return [
        day_rate * scenario.multiplier if scenario.delay_days <= day < end else day_rate
        for day in range(horizon)
    ]


def simulate(
    pool: Pool, scenario: Scenario, horizon: int, targets: Collection[str] | None = None
) -> tuple[float, float]:
    """(pool sales, residual of the target batches) at the deadline.

    Sales of every pool batch (targets or not) draw on the same demand stream in
    queue order, so stock ahead of the target uses capacity. ``targets=None``
    means the whole pool.
    """
    if pool.day_rate is None:
        raise ValueError("Cannot simulate without a baseline rate")
    path = deplete(pool.entries, rate_profile(pool.day_rate, scenario, horizon), horizon)
    chosen = path.keys() if targets is None else [b for b in path if b in targets]
    sales = sum(v[0] - v[-1] for v in path.values())
    return sales, sum(path[b][-1] for b in chosen)


def queue_ahead_kl(pool: Pool, targets: Collection[str] | None) -> float:
    """Non-target stock that sorts before the last target in the sales queue."""
    if targets is None:
        return 0.0
    order = sorted(pool.entries, key=lambda b: (b[1], b[3] is None, b[3] or date.max, b[0]))
    last = max((i for i, b in enumerate(order) if b[0] in targets), default=-1)
    return sum(b[2] for i, b in enumerate(order) if i < last and b[0] not in targets)


def solve_uplift(
    pool: Pool, horizon: int, targets: Collection[str] | None = None, delay: int = 0, duration: int | None = None
) -> tuple[str, float | None]:
    """Smallest multiplier in [1, MAX_UPLIFT] that clears the targets by the deadline.

    Returns (status, multiplier). Statuses: ``no_uplift_needed``,
    ``solved_approximate`` (within ``UPLIFT_TOLERANCE``) or
    ``not_feasible_within_bounds``. Needs a positive baseline rate.
    """

    def residual(multiplier: float) -> float:
        return simulate(pool, Scenario("solve", multiplier, delay, duration), horizon, targets)[1]

    if residual(1.0) <= RESIDUAL_TOLERANCE_KL:
        return "no_uplift_needed", 1.0
    if residual(MAX_UPLIFT) > RESIDUAL_TOLERANCE_KL:
        return "not_feasible_within_bounds", None
    low, high = 1.0, MAX_UPLIFT
    while high - low > UPLIFT_TOLERANCE:
        middle = (low + high) / 2
        if residual(middle) <= RESIDUAL_TOLERANCE_KL:
            high = middle
        else:
            low = middle
    return "solved_approximate", high


def pools(batches: pl.DataFrame, shares: pl.DataFrame, received: pl.DataFrame | None) -> list[Pool]:
    """Group the full positive stock into town + child pools with their child rate."""
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
    stock = stock.with_columns(
        pl.when(pl.col("avg_sec_m").is_null())
        .then(None)
        .otherwise(pl.col("avg_sec_m") * pl.col("share").fill_null(0.0))
        .alias("_rate")
    )
    grouped: dict[tuple[str, int], Pool] = {}
    for town, material, parent, batch, rsl, kl, received_on, rate in stock.select(
        "Town",
        "Material Code",
        "parent_material_code",
        "batch_index",
        "rsl_days",
        "total_closing_stock_kl",
        "received_date",
        "_rate",
    ).rows():
        pool = grouped.setdefault((town, material), Pool(town, material, [], rate, parent))
        pool.entries.append((batch, rsl, kl, received_on))
    return sorted(grouped.values(), key=lambda p: (p.town, p.material_code))


def snapshot_of(batches: pl.DataFrame) -> date:
    if batches.height == 0 or batches.get_column("report_date").n_unique() != 1:
        raise ValueError("Report must contain exactly one snapshot date and some batches")
    return report.EXCEL_EPOCH + timedelta(days=batches.item(0, "report_date"))


RECENT_MONTHS = 3
HIST_CAVEATS = (
    "historical sales not unconstrained demand; stock availability not supplied; "
    "one annual cycle cannot show seasonality or promotion effect; peak is a review flag"
)
EVIDENCE_COLUMNS = [
    "hist_window_months",
    "hist_observed_months",
    "hist_calendar_gap_months",
    "hist_recent_months_observed",
    "hist_recent_kl_week",
    "hist_peak_kl_week",
    "hist_peak_month",
    "hist_adjusted_months",
    "hist_ambiguous_peak_months",
]


def history_evidence(source: pl.DataFrame) -> pl.DataFrame:
    """Per town + parent: observed monthly pace evidence from the audited rate pipeline.

    ``source`` is secondary sales up to the snapshot (``source_through``; no future
    months). Paces are KL/week (monthly KL x 7 / 30). Missing pair-months are counted,
    not invented: ``recent`` averages the last ``RECENT_MONTHS`` calendar months of the
    window with absent months as zero sales and reports how many were observed.
    """
    audit = adjustments(source)
    window = audit["month"].unique().sort()
    span = (window[-1].year - window[0].year) * 12 + window[-1].month - window[0].month + 1
    recent = set(window.tail(RECENT_MONTHS).to_list())
    weekly = DAYS_PER_WEEK / DAYS_PER_MONTH
    peak = pl.col("raw_kl").arg_max()
    return audit.group_by(KEYS).agg(
        pl.lit(len(window), dtype=pl.Int64).alias("hist_window_months"),
        pl.len().cast(pl.Int64).alias("hist_observed_months"),
        pl.lit(span - len(window), dtype=pl.Int64).alias("hist_calendar_gap_months"),
        pl.col("month").is_in(recent).sum().cast(pl.Int64).alias("hist_recent_months_observed"),
        (pl.col("raw_kl").filter(pl.col("month").is_in(recent)).sum() / len(recent) * weekly).alias("_recent"),
        (pl.col("raw_kl").max() * weekly).alias("_peak"),
        pl.col("month").get(peak).alias("hist_peak_month"),
        (pl.col("reason") == "winsorized").sum().cast(pl.Int64).alias("hist_adjusted_months"),
        (pl.col("reason") == "ambiguous_peak_preserved").sum().cast(pl.Int64).alias("hist_ambiguous_peak_months"),
    )


def _evidence_for(pool: Pool, evidence: dict, shares: dict) -> dict | None:
    """Parent evidence scaled to this child by its window share (same split as the rate)."""
    hit = evidence.get((pool.town, pool.parent_code))
    if hit is None:
        return None
    factor = shares.get((pool.town, pool.parent_code, pool.material_code), 0.0)
    row = dict(hit)
    row["hist_recent_kl_week"] = row.pop("_recent") * factor
    row["hist_peak_kl_week"] = row.pop("_peak") * factor
    return row


def _compare(required: float | None, row: dict | None) -> str:
    if row is None:
        return "no_history"
    if required is None:
        return "not_applicable"
    if required > row["hist_peak_kl_week"] * (1 + 1e-9):
        return "above_observed_peak"
    return "within_observed_range"


def _check(deadline_kind: str, deadline_days: int, scenarios: tuple[Scenario, ...]) -> None:
    if deadline_kind not in DEADLINE_KINDS:
        raise ValueError(f"deadline_kind must be one of {DEADLINE_KINDS}")
    if deadline_days < 0:
        raise ValueError("deadline_days must not be negative")
    if len({s.scenario_id for s in scenarios}) != len(scenarios):
        raise ValueError("Scenario ids must be unique")


def _context(pool: Pool, targets: Collection[str] | None, origin: date, kind: str, days: int) -> dict:
    chosen = [e for e in pool.entries if targets is None or e[0] in targets]
    day_rate = pool.day_rate
    return {
        "forecast_origin": origin,
        "town": pool.town,
        "material_code": pool.material_code,
        "deadline_kind": kind,
        "deadline_days": days,
        "deadline_date": origin + timedelta(days=days),
        "target_batches": len(chosen),
        "target_kl": sum(e[2] for e in chosen),
        "queue_ahead_kl": queue_ahead_kl(pool, targets),
        "baseline_kl_week": None if day_rate is None else day_rate * DAYS_PER_WEEK,
        "rate_basis": RATE_BASIS,
        "policy_version": POLICY_VERSION,
    }


def transform(
    batches: pl.DataFrame,
    shares: pl.DataFrame,
    received: pl.DataFrame | None = None,
    scenarios: tuple[Scenario, ...] = SCENARIOS,
    deadline_days: int = DEFAULT_DEADLINE_DAYS,
    deadline_kind: str = DEFAULT_DEADLINE_KIND,
    targets: Collection[str] | None = None,
) -> pl.DataFrame:
    """One row per pool and scenario: modelled sales and residual at the deadline."""
    _check(deadline_kind, deadline_days, scenarios)
    origin = snapshot_of(batches)
    rows = []
    for pool in pools(batches, shares, received):
        if targets is not None and not any(e[0] in targets for e in pool.entries):
            continue
        context = _context(pool, targets, origin, deadline_kind, deadline_days)
        for scenario in scenarios:
            codes = []
            sales = residual = None
            if deadline_days == 0:
                codes.append("deadline_not_after_origin")
                residual = context["target_kl"]
            elif pool.day_rate is None:
                codes.append("baseline_unknown")
            else:
                sales, residual = simulate(pool, scenario, deadline_days, targets)
                if scenario.delay_days >= deadline_days:
                    codes.append("intervention_starts_after_deadline")
                if pool.day_rate == 0:
                    codes.append("baseline_zero_uplift_has_no_effect")
                if any(e[1] < deadline_days for e in pool.entries if targets is None or e[0] in targets):
                    codes.append("expiry_before_deadline")
                if residual > RESIDUAL_TOLERANCE_KL:
                    codes.append("residual_remains")
            rows.append(
                {
                    **context,
                    "scenario_id": scenario.scenario_id,
                    "uplift_multiplier": scenario.multiplier,
                    "delay_days": scenario.delay_days,
                    "duration_days": scenario.duration_days,
                    "projected_sales_kl": sales,
                    "projected_residual_kl": residual,
                    "reason_codes": ";".join(codes),
                }
            )
    return pl.DataFrame(rows, schema=SCENARIO_SCHEMA).sort("town", "material_code", "scenario_id")


def transform_requirements(
    batches: pl.DataFrame,
    shares: pl.DataFrame,
    received: pl.DataFrame | None = None,
    deadline_days: int = DEFAULT_DEADLINE_DAYS,
    deadline_kind: str = DEFAULT_DEADLINE_KIND,
    targets: Collection[str] | None = None,
    evidence: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """One row per pool: required total/incremental pace and uplift to clear by the deadline.

    ``required_total_kl_week`` is the pool's average weekly sales over the horizon at the
    minimal solving multiplier; ``required_incremental_kl_week`` is that minus baseline
    sales. ``simple_required_kl_week`` = (targets + stock ahead) / weeks is only the
    explanatory isolated-cohort arithmetic. Unknown baseline gives no uplift; a zero
    baseline gives no ratio (``zero_baseline``); a deadline at/before the origin with
    stock is infeasible, never a division by zero.
    """
    _check(deadline_kind, deadline_days, ())
    origin = snapshot_of(batches)
    weeks = deadline_days / DAYS_PER_WEEK
    rows = []
    evidence_index = (
        {}
        if evidence is None
        else {(r["town"], r["parent_material_code"]): r for r in evidence.to_dicts()}
    )
    share_index = {
        (r["town"], r["parent_material_code"], r["material_code"]): r["share"]
        for r in shares.to_dicts()
    }
    for pool in pools(batches, shares, received):
        if targets is not None and not any(e[0] in targets for e in pool.entries):
            continue
        context = _context(pool, targets, origin, deadline_kind, deadline_days)
        codes: list[str] = []
        status = "baseline_unknown"
        total = incremental = uplift = baseline_sales = None
        simple = None if deadline_days == 0 else (context["target_kl"] + context["queue_ahead_kl"]) / weeks
        if deadline_days == 0:
            status = "deadline_not_after_origin"
            codes.append("deadline_not_after_origin")
        elif pool.day_rate is None:
            codes.append("baseline_unknown")
        elif pool.day_rate <= EPSILON:
            status = "zero_baseline"
            baseline_sales = 0.0
            codes.append("baseline_zero_ratio_unavailable")
            total = incremental = simple
        else:
            baseline_sales = simulate(pool, SCENARIOS[0], deadline_days, targets)[0]
            status, uplift = solve_uplift(pool, deadline_days, targets)
            if uplift is None:
                codes.append("not_feasible_within_bounds")
                if any(e[1] < deadline_days for e in pool.entries if targets is None or e[0] in targets):
                    codes.append("expiry_before_deadline")
            else:
                solved = simulate(pool, Scenario("solved", uplift), deadline_days, targets)[0]
                total = solved / weeks
                incremental = max(0.0, solved - baseline_sales) / weeks
        hist = _evidence_for(pool, evidence_index, share_index)
        comparison = _compare(total, hist)
        if comparison == "above_observed_peak":
            codes.append("required_above_observed_peak")
        review = {
            "no_uplift_needed": "supported_assumptions",
            "solved_approximate": "stretch_scenario",
            "baseline_unknown": "incomplete_evidence",
            "zero_baseline": "incomplete_evidence",
        }.get(status, "infeasible_under_stated_constraints")
        rows.append(
            {
                **context,
                **{c: (hist or {}).get(c) for c in EVIDENCE_COLUMNS},
                "hist_comparison": comparison,
                "hist_caveats": HIST_CAVEATS,
                "baseline_sales_kl": baseline_sales,
                "simple_required_kl_week": simple,
                "required_total_kl_week": total,
                "required_incremental_kl_week": incremental,
                "required_uplift": uplift,
                "solver_status": status,
                "search_max_uplift": MAX_UPLIFT,
                "uplift_tolerance": UPLIFT_TOLERANCE,
                "review_state": review,
                "reason_codes": ";".join(codes),
            }
        )
    return pl.DataFrame(rows, schema=REQUIREMENT_SCHEMA).sort("town", "material_code")


def _inputs(data_dir: str | Path) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    config = starting_date.build(data_dir)
    if config.height != 1 or config.item(0, "starting_date") is None:
        raise ValueError("config_table must contain one non-null starting_date")
    as_of = config.item(0, "starting_date")
    stock = soh_db.build(data_dir, as_of=as_of)
    batches = report.transform(stock, db_landing.build(data_dir), as_of)
    shares = child_shares(source_through(load(data_dir), as_of))
    return batches, shares, received_dates(stock)


def build(data_dir: str | Path = "data/current", **_: object) -> pl.DataFrame:
    return transform(*_inputs(data_dir))


def build_requirements(data_dir: str | Path = "data/current", **_: object) -> pl.DataFrame:
    batches, shares, received = _inputs(data_dir)
    as_of = report.EXCEL_EPOCH + timedelta(days=batches.item(0, "report_date"))
    evidence = history_evidence(source_through(load(data_dir), as_of))
    return transform_requirements(batches, shares, received, evidence=evidence)


if __name__ == "__main__":
    run(build)

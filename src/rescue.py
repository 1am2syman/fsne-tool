"""Action plan pages (rescue 3M–6M, rebalance 6M–10M, loading-watch >10M): established
measures beside last month's plans. One implementation, parameterised by ``Page``.

Every number is an existing fact; nothing here re-forecasts stock.

- Current report (``src/report.py``): ``rsl_group``, ``exposure_*``, ``lnd_fc_*``.
  Up to 10M Exposure equals stock (the batch is already inside the 300-day line),
  so LND FC is the measure that separates stock forecast to sell before 90 days
  RSL from stock that will not. Above 10M Exposure is only the part of stock not
  sold before 300 days RSL, and ``rotation_req_*`` is what remains at twice the
  run rate.
- Previous month's plan: the latest saved report in the previous calendar month,
  joined by exact ``batch_index``. This is the same join the action plan input
  file uses for its ``lm_`` columns, so coverage matches what sales saw.
- Monthly outcomes: saved reports only (latest per calendar month). The cohort is
  last month's batches with LND FC > 0, matched to this month by the movement
  batch key, so new inclusions never count as reductions.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl

from .io import date as parse_date
from .movement import KEY, batch_key


@dataclass(frozen=True)
class Page:
    """One Action plans page: a band of the report's ``rsl_group`` and what differs around it."""

    key: str
    band: str  # rsl_group value of the rows
    label: str  # band as written in the UI
    days: str  # RSL days of the band, for notes
    soon_days: int  # LND with RSL <= this enters the next band in 30 days
    soon_label: str  # the band it enters
    outcome_bands: tuple[str, ...]  # bands of the remark panel's cohort
    outcome_label: str
    batches_file: str  # uncovered-batch list plan notes resolve against, in the run's output/
    exposure_only: bool = False  # rows need exposure > 0: most >10M stock sells before the 300-day line
    rotation: bool = False  # extra column rotation_req_*

    @property
    def meta(self) -> dict:
        """What the page needs to word itself; the template holds no band literals."""
        return {"key": self.key, "label": self.label, "days": self.days, "soon_days": self.soon_days,
                "soon_label": self.soon_label, "outcome_label": self.outcome_label,
                "exposure_only": self.exposure_only, "rotation": self.rotation}


RESCUE = Page("rescue", "3M - 6M", "3M–6M", "91–180", 120, "<3M", ("3M - 6M", "6M - 10M"), "3M–10M",
              "rescue_batches.parquet")
REBALANCE = Page("rebalance", "6M - 10M", "6M–10M", "181–300", 210, "3M–6M", ("6M - 10M",), "6M–10M",
                 "rebalance_batches.parquet")
LOADING_WATCH = Page("loading-watch", "> 10M", ">10M", ">300", 330, "6M–10M", ("> 10M",), ">10M",
                     "loading_watch_batches.parquet", exposure_only=True, rotation=True)
PAGES = {p.key: p for p in (RESCUE, REBALANCE, LOADING_WATCH)}
# Action plan categories with the mitigation status sales gave each kind of remark
# in the saved reports (Feb–Aug 2026, 85 distinct remarks).
PLAN_CATEGORIES = (
    ("Sales push", "Actionable"),
    ("Seasonal sales push", "Actionable"),
    ("Trade offer (TLO / QDD / 2+1)", "Actionable"),
    ("EC drive / activation", "Actionable"),
    ("Free with bundle", "Actionable"),
    ("Stock rotation", "Actionable"),
    ("Liquidation - E-Com", "Actionable"),
    ("Liquidation - Modern Trade", "Actionable"),
    ("Liquidation - RTM", "Actionable"),
    ("Emerging channel", "Actionable"),
    ("Office sales", "Actionable"),
    ("Stock adjustment", "Incorrect Stock"),
    ("L&D claim", "Not actionable"),
)
STATUS_PRECEDENCE = ("Actionable", "Incorrect Stock", "Not actionable")
# Sales type this into action_plan for >10M stock; it is a label, not an action.
NO_ACTION = "rsl>10m_good stock"
PLAN_ALIASES = {"action_plan": "action_plan_remarks", "mitigation_status": "mitigation_status_category"}


def _text(frame: pl.DataFrame, name: str) -> pl.Expr:
    source = name if name in frame.columns else PLAN_ALIASES.get(name)
    if source not in frame.columns:
        return pl.lit(None, dtype=pl.String).alias(name)
    value = pl.col(source).cast(pl.String).str.strip_chars()
    return pl.when(value != "").then(value).alias(name)


def saved_report(frame: pl.DataFrame) -> pl.DataFrame:
    """One schema for a saved workbook, whatever the June column rename."""
    return frame.select(
        parse_date("report_date"),
        pl.col("batch_index").cast(pl.String),
        batch_key(),
        pl.col("rsl_group").cast(pl.String).str.strip_chars(),
        *[pl.col(c).cast(pl.Float64, strict=False).fill_null(0.0) for c in ("lnd_fc_kl", "lnd_fc_bdt")],
        _text(frame, "action_plan"),
        _text(frame, "mitigation_status"),
    ).filter(pl.col("report_date").is_not_null())


def monthly(saved: pl.DataFrame) -> pl.DataFrame:
    """Latest saved snapshot per calendar month; earlier same-month files are superseded."""
    month = pl.col("report_date").dt.truncate("1mo")
    return saved.filter(pl.col("report_date") == pl.col("report_date").max().over(month))


def previous_month_plans(saved: pl.DataFrame, report_date: date) -> tuple[pl.DataFrame, date | None]:
    """Exact batch labels from the latest saved report in the previous calendar month."""
    previous = report_date.replace(day=1) - timedelta(days=1)
    month = saved.filter(
        (pl.col("report_date").dt.year() == previous.year) & (pl.col("report_date").dt.month() == previous.month)
    )
    empty = pl.DataFrame(schema={"batch_index": pl.String, "lm_action_plan": pl.String, "lm_mitigation_status": pl.String})
    if month.is_empty():
        return empty, None
    lm_date = month.select(pl.col("report_date").max()).item()
    if not isinstance(lm_date, date):
        raise TypeError("Saved report_date must be a date")
    labels = month.filter(pl.col("report_date") == lm_date).select(
        "batch_index", pl.col("action_plan").alias("lm_action_plan"),
        pl.col("mitigation_status").alias("lm_mitigation_status"),
    )
    if labels["batch_index"].n_unique() != labels.height:
        raise ValueError(f"Saved report {lm_date}: batch_index must be unique")
    return labels, lm_date


def canonical_parents(current: pl.DataFrame) -> pl.DataFrame:
    """Parent description: the parent's own SKU text, else its largest child's."""
    return (
        current.sort("total_closing_stock_kl", descending=True)
        .group_by("parent_material_code")
        .agg(
            pl.col("material_desc").filter(pl.col("Material Code") == pl.col("parent_material_code")).first().alias("own"),
            pl.col("material_desc").first().alias("largest"),
        )
        .select("parent_material_code", pl.coalesce("own", "largest").alias("parent_desc"))
    )


def batches(current: pl.DataFrame, plans: pl.DataFrame, page: Page = RESCUE) -> pl.DataFrame:
    """The page's current batches with last month's exact plan; new batches have none."""
    rows = current.filter(pl.col("rsl_group") == page.band)
    if page.exposure_only:
        rows = rows.filter(pl.col("exposure_kl") > 0)
    return (
        rows
        .join(canonical_parents(current), on="parent_material_code", how="left", validate="m:1")
        .join(plans, on="batch_index", how="left", validate="m:1", maintain_order="left")
        .with_columns(
            (pl.col("lm_action_plan").is_not_null()
             & (pl.col("lm_action_plan").str.to_lowercase() != NO_ACTION)).alias("has_plan")
        )
    )


def lots(rows: pl.DataFrame, regions: pl.DataFrame | None, threshold_bdt: float = 20_000) -> dict | None:
    """LND FC pooled by SKU × region: the unit one liquidation or transfer can collect."""
    if regions is None:
        return None
    pooled = (
        rows.filter(pl.col("lnd_fc_kl") > 0)
        .join(regions, on="Town", how="left", validate="m:1")
        .group_by("Material Code", "Region Name")
        .agg(pl.col("lnd_fc_kl").sum(), pl.col("lnd_fc_bdt").sum())
    )
    big = pooled.filter(pl.col("lnd_fc_bdt") >= threshold_bdt)
    return {"threshold_bdt": threshold_bdt, "lots": pooled.height, "big": big.height,
            "kl": float(big["lnd_fc_kl"].sum()), "bdt": float(big["lnd_fc_bdt"].sum())}


def category() -> pl.Expr:
    """Keyword group of a free-text plan; first match wins (liquidation before rotation)."""
    plan = pl.col("action_plan").str.to_lowercase().fill_null("")
    return (
        pl.when(pl.col("mitigation_status").str.to_lowercase() == "incorrect stock").then(pl.lit("Verify (Incorrect Stock)"))
        .when(plan.str.contains("l&d", literal=True)).then(pl.lit("L&D risk"))
        .when(plan.str.contains("liquidat")).then(pl.lit("Liquidation MT / E-Com / RTM"))
        .when(plan.str.contains("rotation")).then(pl.lit("Stock rotation"))
        .when(plan.str.contains(r"tlo|qdd|2\+1|incentive")).then(pl.lit("Push + TLO / QDD / 2+1"))
        .when(plan.str.contains("winter|season")).then(pl.lit("Seasonal sales push"))
        .when(plan.str.contains("push")).then(pl.lit("Sales push"))
        .when(plan == "").then(pl.lit("No action plan"))
        .otherwise(pl.lit("Other"))
        .alias("category")
    )


def _cohorts(saved: pl.DataFrame, bands: tuple[str, ...]) -> pl.DataFrame:
    """Each LM cohort (batches in ``bands`` with LND > 0) beside the same keys' next-month LND."""
    months = monthly(saved).with_columns(pl.col("report_date").rank("dense").cast(pl.Int32).alias("_i"))
    last = months["_i"].max()
    lm = (
        months.filter(pl.col("rsl_group").is_in(bands) & (pl.col("lnd_fc_kl") > 0) & (pl.col("_i") < last))
        .sort("lnd_fc_kl", descending=True)
        .group_by("_i", KEY)
        .agg(pl.col("lnd_fc_kl").sum().alias("lm_kl"), pl.col("lnd_fc_bdt").sum().alias("lm_bdt"),
             pl.col("action_plan").first(), pl.col("mitigation_status").first())
    )
    cm = (
        months.group_by("_i", KEY)
        .agg(pl.col("lnd_fc_kl").sum().alias("cm_kl"), pl.col("lnd_fc_bdt").sum().alias("cm_bdt"))
        .with_columns(pl.col("_i") - 1)
    )
    dates = months.select("_i", "report_date").unique().with_columns(pl.col("_i") - 1)
    return (
        lm.join(cm, on=["_i", KEY], how="left")
        .with_columns(pl.col("cm_kl", "cm_bdt").fill_null(0.0))
        .join(dates, on="_i", how="left")
        .rename({"report_date": "cm_date"})
    )


def incumbent_trend(saved: pl.DataFrame, page: Page = RESCUE) -> pl.DataFrame:
    """Per month: LM LND FC of the page's band (LND > 0) and the same batches' LND this month."""
    return (
        _cohorts(saved, (page.band,))
        .group_by("cm_date")
        .agg(pl.col("lm_kl", "lm_bdt", "cm_kl", "cm_bdt").sum(), pl.len().alias("batches"))
        .sort("cm_date")
    )


def remark_outcomes(saved: pl.DataFrame, page: Page = RESCUE, transitions: int = 3) -> tuple[pl.DataFrame, list[date]]:
    """LND FC change by last month's plan keyword, over the page's bands and the latest ``transitions``."""
    cohorts = _cohorts(saved, page.outcome_bands)
    window = cohorts["cm_date"].unique().sort().tail(transitions)
    out = (
        cohorts.filter(pl.col("cm_date").is_in(window.implode()))
        .with_columns(category())
        .group_by("category")
        .agg(pl.col("lm_kl", "lm_bdt", "cm_kl", "cm_bdt").sum(), pl.len().alias("batches"))
        .sort("lm_bdt", descending=True)
    )
    return out, window.to_list()


def plan_status(categories: list[str]) -> str | None:
    """Most actionable status among the chosen categories; none without a category."""
    statuses = {dict(PLAN_CATEGORIES)[c] for c in categories}
    return next((s for s in STATUS_PRECEDENCE if s in statuses), None)


def plan_text(categories: list[str], remarks: str | None) -> str | None:
    """Sales' own remark style: parts joined by '; ', categories in list order first."""
    parts = [c for c, _ in PLAN_CATEGORIES if c in categories] + ([remarks] if remarks else [])
    return "; ".join(parts) or None

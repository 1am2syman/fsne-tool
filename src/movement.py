"""total-stock-movement: all saved stock, independent of planning status.

Batch-level delta accounting per ``(Town, Material, Batch)`` key:

    LM Stock BDT          = sum of LM ``total_closing_stock_bdt`` across every row
    CM Closing (No New)   = sum over keys in both LM & CM of min(LM_k, CM_k)
    CM Closing (New)      = sum of new-key CM stock + sum over existing keys of max(0, CM_k - LM_k)
    Reduced from LM       = LM Stock - CM Closing (No New) = sum over LM keys of max(0, LM_k - CM_k) >= 0
    Reduction %           = Reduced from LM / LM Stock (null when LM Stock <= 0)
    Latest Month Remarks  = distinct remark phrases of the CM batches

Stock added to an already-existing physical batch key counts under New inclusions
rather than offsetting clearance, so reductions are never negative while Opening (LM)
and Closing (Carried over + New inclusions) stay exact. Reduction measures a change
in saved inventory, not proven sales or disposal. Each calendar month uses its latest
saved report, retaining that report's prices. Status edits never change stock totals
or physical new/remaining batch membership.

The batch key is ``batch_index`` without its last two hyphen segments (same as
``history.batch_index_short``). Saved reports carry two user-entered columns that
were renamed in June: ``action_plan_remarks``/``action_plan`` and
``mitigation_status_category``/``mitigation_status``.

All work is columnar Polars (joins, ``group_by``, no Python row loops).
"""

from pathlib import Path

import polars as pl

from .cli import run
from .io import EXCEL_SUFFIXES, resolve_path
from .saved_reports import read_saved_report

BRAND = "Brand Name"
KEY = "_key"
TOTAL = "Grand Total"
RSL = "rsl_group"
OUTPUT = [
    "brand",
    "lm_stock_bdt",
    "cm_no_new_bdt",
    "cm_new_bdt",
    "reduced_bdt",
    "reduction_pct",
    "remarks",
]
REMARKS = ("action_plan_remarks", "action_plan")
STATUS = ("mitigation_status_category", "mitigation_status")


def batch_key(column: str = "batch_index") -> pl.Expr:
    """Strip the two rightmost hyphen segments (e.g. ``-25.000-16.2``)."""
    return (
        pl.col(column)
        .cast(pl.String)
        .str.replace(r"-[^-]*$", "")
        .str.replace(r"-[^-]*$", "")
        .alias(KEY)
    )


def _first_present(frame: pl.DataFrame, names: tuple[str, ...]) -> str:
    for name in names:
        if name in frame.columns:
            return name
    raise ValueError(f"Saved report has none of the columns {names}")


def _optional(frame: pl.DataFrame, name: str) -> pl.Expr:
    if name in frame.columns:
        return pl.col(name).cast(pl.String).str.strip_chars()
    return pl.lit(None, dtype=pl.String)


def normalise(frame: pl.DataFrame) -> pl.DataFrame:
    """One schema for every saved report, whatever the column spelling."""
    return frame.select(
        pl.col("report_date").cast(pl.String).str.slice(0, 10).str.to_date(strict=False),
        pl.col("batch_index").cast(pl.String),
        pl.col(BRAND).cast(pl.String),
        _optional(frame, "Town").fill_null("").alias("town"),
        _optional(frame, "Material Code").alias("material_code"),
        _optional(frame, "parent_material_code").alias("parent_code"),
        _optional(frame, "material_desc").alias("material_desc"),
        pl.col("total_closing_stock_bdt").cast(pl.Float64, strict=False),
        pl.col("total_closing_stock_kl").cast(pl.Float64, strict=False)
        if "total_closing_stock_kl" in frame.columns
        else pl.lit(None, dtype=pl.Float64).alias("total_closing_stock_kl"),
        pl.col(_first_present(frame, REMARKS)).cast(pl.String).alias("remarks"),
        pl.col(_first_present(frame, STATUS)).cast(pl.String).str.strip_chars().alias("status"),
        _optional(frame, RSL).fill_null("Unknown").alias(RSL),
    )


def read_raw_reports(history_dir: str | Path) -> list[pl.DataFrame]:
    """Read each monthly record once, preferring Parquet to its Excel export."""
    root = resolve_path(history_dir)
    paths = (
        sorted(p for p in root.rglob("*") if p.suffix.lower() in EXCEL_SUFFIXES)
        if root.is_dir()
        else []
    )
    if not paths:
        raise FileNotFoundError(f"No saved reports in {root}")
    return [read_saved_report(p) for p in paths]


def read_reports(history_dir: str | Path) -> pl.DataFrame:
    return pl.concat(
        [normalise(frame) for frame in read_raw_reports(history_dir)],
        how="vertical_relaxed",
    )


def monthly(history: pl.DataFrame) -> pl.DataFrame:
    """Select the latest complete snapshot per calendar month, before grouping."""
    return history.filter(
        pl.col("report_date") == pl.col("report_date").max().over(
            pl.col("report_date").dt.truncate("1mo")
        )
    )


def split_months(history: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """CM = latest monthly snapshot, LM = the previous saved month's snapshot."""
    history = monthly(history)
    dates = history.get_column("report_date").drop_nulls().unique().sort()
    if dates.len() < 2:
        raise ValueError("Need at least two saved reports (LM and CM)")
    cm_date, lm_date = dates[-1], dates[-2]
    return (
        history.filter(pl.col("report_date") == cm_date),
        history.filter(pl.col("report_date") == lm_date),
    )


def _remark_phrases(
    frame: pl.LazyFrame, by: tuple[str, ...] = (BRAND,)
) -> pl.LazyFrame:
    """Group -> distinct phrases (split on ', ', case-insensitive, first-seen order)."""
    return (
        frame.select(
            *by,
            pl.col("remarks").str.split(", ").alias("phrase"),
        )
        .explode("phrase", empty_as_null=True)
        .with_columns(pl.col("phrase").str.strip_chars())
        .filter(pl.col("phrase").is_not_null() & (pl.col("phrase") != ""))
        .with_columns(pl.col("phrase").str.to_lowercase().alias("_k"))
        .unique(subset=[*by, "_k"], keep="first", maintain_order=True)
    )


def _split_cm_deltas(
    cm_by_key: pl.LazyFrame,
    lm_by_key: pl.LazyFrame,
    join_on: list[str],
    group_by: list[str],
) -> pl.LazyFrame:
    """Split each CM key's stock into carried-over ``min(cm, lm)`` and additions ``max(0, cm - lm)``.

    Both inputs must already be aggregated to one row per key so duplicate rows
    within a month never multiply LM stock during the join.
    """
    cm_pos = pl.col("_cm_stock").fill_null(0.0).clip(lower_bound=0.0)
    lm_pos = pl.col("_lm_stock").fill_null(0.0).clip(lower_bound=0.0)
    in_lm = pl.col("_in_lm").fill_null(False)
    return (
        cm_by_key.join(lm_by_key, on=join_on, how="left", maintain_order="left")
        .with_columns(
            pl.when(in_lm)
            .then(pl.min_horizontal(cm_pos, lm_pos))
            .alias("_carried"),
            pl.when(~in_lm)
            .then(cm_pos)
            .when(cm_pos > lm_pos)
            .then(cm_pos - lm_pos)
            .alias("_added"),
        )
        .group_by(group_by)
        .agg(
            pl.when(pl.col("_carried").is_not_null().any())
            .then(pl.col("_carried").sum())
            .alias("cm_no_new_bdt"),
            pl.when(pl.col("_added").is_not_null().any())
            .then(pl.col("_added").sum())
            .alias("cm_new_bdt"),
        )
    )


def transform(
    cm: pl.DataFrame,
    lm: pl.DataFrame,
) -> pl.DataFrame:
    """All-stock brand rows sorted by reduction, then one Grand Total row."""
    cm_lf = cm.lazy().with_columns(batch_key())
    lm_lf = lm.lazy().with_columns(batch_key())

    bdt = pl.col("total_closing_stock_bdt")
    # Collapse per key before joining so duplicate keys or cross-month brand remaps stay exact.
    lm_by_key = lm_lf.group_by(KEY).agg(
        bdt.fill_null(0.0).clip(lower_bound=0.0).sum().alias("_lm_stock"),
        pl.lit(True).alias("_in_lm"),
    )
    cm_by_key = cm_lf.group_by(KEY).agg(
        pl.col(BRAND).first().alias(BRAND),
        bdt.fill_null(0.0).clip(lower_bound=0.0).sum().alias("_cm_stock"),
    )
    cm_side = _split_cm_deltas(cm_by_key, lm_by_key, [KEY], [BRAND])
    # Align surviving LM keys to their CM brand so brand-level reduced_bdt never drifts.
    lm_side = (
        lm_lf.group_by(KEY)
        .agg(
            pl.col(BRAND).first().alias(BRAND),
            bdt.fill_null(0.0).clip(lower_bound=0.0).sum().alias("_lm_stock"),
        )
        .join(
            cm_by_key.select(KEY, pl.col(BRAND).alias("_next_brand")),
            on=KEY,
            how="left",
            maintain_order="left",
        )
        .with_columns(pl.coalesce("_next_brand", BRAND).alias(BRAND))
        .group_by(BRAND)
        .agg(pl.col("_lm_stock").sum().alias("lm_stock_bdt"))
    )
    remarks = (
        _remark_phrases(cm_lf)
        .group_by(BRAND, maintain_order=True)
        .agg(pl.col("phrase").str.join(", ").alias("remarks"))
    )

    brands = (
        lm_side.join(cm_side, on=BRAND, how="full", coalesce=True)
        .join(remarks, on=BRAND, how="left")
        .rename({BRAND: "brand"})
        .with_columns(
            pl.when(pl.col("lm_stock_bdt").is_not_null())
            .then(
                (pl.col("lm_stock_bdt") - pl.col("cm_no_new_bdt").fill_null(0.0))
                .clip(lower_bound=0.0)
            )
            .alias("reduced_bdt")
        )
        .with_columns(
            pl.when(pl.col("lm_stock_bdt") > 0)
            .then(pl.col("reduced_bdt") / pl.col("lm_stock_bdt"))
            .alias("reduction_pct")
        )
        .sort(["reduced_bdt", "brand"], descending=[True, False], nulls_last=True)
        .select(OUTPUT)
        .collect()
    )

    total = brands.select(
        pl.lit(TOTAL).alias("brand"),
        pl.col("lm_stock_bdt").sum(),
        pl.col("cm_no_new_bdt").sum(),
        pl.col("cm_new_bdt").sum(),
        pl.col("reduced_bdt").sum(),
        pl.when(pl.col("lm_stock_bdt").sum() > 0)
        .then(pl.col("reduced_bdt").sum() / pl.col("lm_stock_bdt").sum())
        .alias("reduction_pct"),
        _remark_phrases(cm_lf)
        .unique(subset=["_k"], keep="first", maintain_order=True)
        .select(pl.col("phrase").str.join(", "))
        .collect()
        .to_series()
        .alias("remarks"),
    )
    return pl.concat([brands, total], how="vertical_relaxed")


SERIES_OUTPUT = ["month", *OUTPUT]


def transform_series(
    history: pl.DataFrame,
    months: int = 6,
    keys: tuple[str, ...] = (BRAND,),
    stock_column: str = "total_closing_stock_bdt",
) -> pl.DataFrame:
    """Every month-to-month movement in one columnar pass (no per-pair loop).

    ``month`` is the CM report date; its LM is the previous saved report.
    The window uses the last ``months`` + 1 monthly snapshots, including those
    with no planning labels. Every status is included. Each comparison gets a
    Grand Total row. A single snapshot is shown as opening-unavailable / all new.

    ``keys`` may add ``rsl_group`` after the brand. A batch is then counted in the
    RSL group it has in the *later* month of each pair: CM rows use their own
    group; LM rows use the group the same batch has in CM (their own group when
    the batch is gone from CM). Groups therefore add up to the ungrouped numbers.
    """
    if months < 1:
        raise ValueError("Movement window must contain at least one month")
    history = monthly(history)
    if RSL not in history.columns:
        history = history.with_columns(pl.lit("Unknown").alias(RSL))
    dates = history["report_date"].drop_nulls().unique().sort()[-(months + 1):]
    h = history.lazy().with_columns(batch_key()).filter(
        pl.col("report_date").is_in(dates.implode())
    )
    calendar = pl.DataFrame(
        {"report_date": dates, "prev_date": dates.shift(1), "next_date": dates.shift(-1)}
    ).lazy()

    # Output field names are stable; stock_column selects the source unit.
    bdt = pl.col(stock_column)
    by = ["report_date", *keys]
    # Aggregate each month to one row per key so duplicate rows never multiply LM stock
    # and surviving keys align on every requested grouping dimension (including RSL aging).
    h_by_key = h.group_by("report_date", KEY).agg(
        *[pl.col(k).first().alias(k) for k in keys],
        bdt.fill_null(0.0).clip(lower_bound=0.0).sum().alias("_stock"),
    )
    lm_by_key = h_by_key.select(
        pl.col("report_date").alias("prev_date"),
        KEY,
        pl.col("_stock").alias("_lm_stock"),
        pl.lit(True).alias("_in_lm"),
    )
    cm_by_key = (
        h_by_key.join(calendar.select("report_date", "prev_date"), on="report_date")
        .filter(pl.lit(len(dates) == 1) | pl.col("prev_date").is_not_null())
        .rename({"_stock": "_cm_stock"})
    )
    cm_side = _split_cm_deltas(cm_by_key, lm_by_key, ["prev_date", KEY], by)
    cm_raw = (
        h.join(calendar.select("report_date", "prev_date"), on="report_date")
        .filter(pl.lit(len(dates) == 1) | pl.col("prev_date").is_not_null())
    )
    remarks = (
        _remark_phrases(cm_raw, by=tuple(by))
        .group_by(by, maintain_order=True)
        .agg(pl.col("phrase").str.join(", ").alias("remarks"))
    )
    next_dims = h_by_key.select(
        pl.col("report_date").alias("next_date"),
        KEY,
        *[pl.col(k).alias(f"_next_{i}") for i, k in enumerate(keys)],
    )
    lm_side = (
        h_by_key.join(calendar.select("report_date", "next_date"), on="report_date")
        .filter(pl.col("next_date").is_not_null())
        .join(next_dims, on=["next_date", KEY], how="left", maintain_order="left")
        .with_columns(
            *[pl.coalesce(f"_next_{i}", k).alias(k) for i, k in enumerate(keys)]
        )
        .group_by("next_date", *keys)
        .agg(pl.col("_stock").sum().alias("lm_stock_bdt"))
        .rename({"next_date": "report_date"})
    )
    brands = (
        lm_side.join(cm_side, on=by, how="full", coalesce=True)
        .join(remarks, on=by, how="left")
        .rename({BRAND: "brand", "report_date": "month"})
        .with_columns(
            pl.when(pl.col("lm_stock_bdt").is_not_null())
            .then(
                (pl.col("lm_stock_bdt") - pl.col("cm_no_new_bdt").fill_null(0.0))
                .clip(lower_bound=0.0)
            )
            .alias("reduced_bdt")
        )
        .with_columns(
            pl.when(pl.col("lm_stock_bdt") > 0)
            .then(pl.col("reduced_bdt") / pl.col("lm_stock_bdt"))
            .alias("reduction_pct")
        )
        .select(*SERIES_OUTPUT, *keys[1:])
    )
    if PARENT in keys:  # drill-down level: no Grand Total row
        return brands.sort(["month", "brand", *keys[1:]]).collect()
    totals = brands.group_by("month", *keys[1:]).agg(
        pl.lit(TOTAL).alias("brand"),
        pl.col("lm_stock_bdt").sum(),
        pl.col("cm_no_new_bdt").sum(),
        pl.col("cm_new_bdt").sum(),
        pl.col("reduced_bdt").sum(),
        pl.when(pl.col("lm_stock_bdt").sum() > 0)
        .then(pl.col("reduced_bdt").sum() / pl.col("lm_stock_bdt").sum())
        .alias("reduction_pct"),
        pl.lit(None, dtype=pl.String).alias("remarks"),
    ).select(*SERIES_OUTPUT, *keys[1:])
    # Keep zero totals for empty RSL groups within the saved calendar.
    calendar_months = pl.DataFrame({"month": dates[1:] if len(dates) > 1 else dates}).lazy()
    if keys[1:]:
        calendar_months = calendar_months.join(history.select(*keys[1:]).unique().lazy(), how="cross")
    totals = calendar_months.join(totals, on=["month", *keys[1:]], how="left").with_columns(
        pl.lit(TOTAL).alias("brand"),
        *[pl.col(c).fill_null(0.0) for c in ("lm_stock_bdt", "cm_no_new_bdt", "cm_new_bdt", "reduced_bdt")],
    ).select(*SERIES_OUTPUT, *keys[1:])
    if len(dates) == 1:
        totals = totals.with_columns(
            pl.lit(None, dtype=pl.Float64).alias(c)
            for c in ("lm_stock_bdt", "reduced_bdt", "reduction_pct")
        )
    return (
        pl.concat([brands, totals])
        .sort(
            ["month", "reduced_bdt", "brand", *keys[1:]],
            descending=[False, True, False, *[False] * (len(keys) - 1)],
            nulls_last=True,
        )
        .collect()
    )


PARENT = "parent_code"


def transform_parent_series(
    history: pl.DataFrame, rsl: bool = False, **kw: object
) -> pl.DataFrame:
    """Same monthly movement as ``transform_series`` but per (brand, parent material).

    The parent is ``parent_material_code`` (falling back to the material's own code);
    ``parent_desc`` is the description of the parent's own material row when it
    exists in the report, else the most common description under that parent.
    """
    history = history.with_columns(
        pl.coalesce(PARENT, "material_code").alias(PARENT)
    )
    desc = (
        history.filter(pl.col("material_desc").is_not_null())
        .group_by(PARENT)
        .agg(
            pl.col("material_desc")
            .filter(pl.col("material_code") == pl.col(PARENT))
            .first()
            .alias("_own"),
            pl.col("material_desc").mode().first().alias("_mode"),
        )
        .select(PARENT, pl.coalesce("_own", "_mode").alias("parent_desc"))
    )
    keys = (BRAND, PARENT, RSL) if rsl else (BRAND, PARENT)
    out = transform_series(history, keys=keys, **kw)  # type: ignore[arg-type]
    return out.join(desc, on=PARENT, how="left").with_columns(
        pl.coalesce("parent_desc", PARENT).alias("parent_desc")
    )


def build_parent_series(
    data_dir: str | Path = "data/current",
    history_dir: str | Path = "history",
    **_: object,
) -> pl.DataFrame:
    return transform_parent_series(read_reports(history_dir), rsl=True)


def build_series(
    data_dir: str | Path = "data/current",
    history_dir: str | Path = "history",
    **_: object,
) -> pl.DataFrame:
    return transform_series(read_reports(history_dir), keys=(BRAND, RSL))


def build(
    data_dir: str | Path = "data/current",
    history_dir: str | Path = "history",
    **_: object,
) -> pl.DataFrame:
    cm, lm = split_months(read_reports(history_dir))
    return transform(cm, lm)


if __name__ == "__main__":
    run(build)

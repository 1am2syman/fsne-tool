"""total-stock-movement: brand-wise movement of stock between last month (LM) and
current month (CM).

Rules (confirmed from the supplied report table and measures.png):

    Reduced from LM = LM Stock BDT - CM Closing Stock BDT (No New Inclusions)
    Reduction %     = Reduced from LM / LM Stock BDT

A CM batch is a *new inclusion* when its batch key is absent from the LM report.
The key is ``batch_index`` without its last two hyphen segments (the value
segments change month to month), the same rule as ``history.batch_index_short``.

Everything is columnar Polars: one anti/left join per lookup, one ``group_by``
per side, one full join between sides. There are no Python row loops.

ASSUMED (no source data supplied): the mitigation file schema. It is an optional
table in ``data_dir`` whose file name contains ``mitigation``, with columns
``batch_index``, ``mitigation_status_category`` and ``remarks``.
"""

from pathlib import Path

import polars as pl

from . import report
from .cli import run
from .io import EXCEL_SUFFIXES, resolve_path

BRAND = "Brand Name"
KEY = "_key"
TOTAL = "Grand Total"
OUTPUT = [
    "brand",
    "lm_stock_bdt",
    "cm_no_new_bdt",
    "cm_new_bdt",
    "reduced_bdt",
    "reduction_pct",
    "remarks",
]


def batch_key(column: str = "batch_index") -> pl.Expr:
    """Strip the two rightmost hyphen segments (e.g. ``-25.000-16.2``)."""
    return (
        pl.col(column)
        .cast(pl.String)
        .str.replace(r"-[^-]*$", "")
        .str.replace(r"-[^-]*$", "")
        .alias(KEY)
    )


def read_table(path: Path) -> pl.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pl.read_parquet(path)
    if suffix == ".csv":
        return pl.read_csv(path, infer_schema_length=10_000)
    if suffix in EXCEL_SUFFIXES:
        return pl.read_excel(path)
    raise ValueError(f"Unsupported table type: {path}")


def last_month(history_dir: str | Path, report_date: int) -> pl.DataFrame:
    """The saved report with the latest ``report_date`` before the current one."""
    root = resolve_path(history_dir)
    paths = sorted(
        p for p in root.rglob("*") if p.suffix.lower() in {".parquet", ".csv", *EXCEL_SUFFIXES}
    ) if root.is_dir() else []
    if not paths:
        raise FileNotFoundError(f"No saved reports in {root}; LM is required")
    need = ["report_date", "batch_index", BRAND, "total_closing_stock_bdt"]
    history = pl.concat(
        [read_table(p).select(need) for p in paths], how="vertical_relaxed"
    )
    previous = history.filter(pl.col("report_date") < report_date)
    if previous.is_empty():
        raise ValueError(f"No saved report before report_date {report_date}")
    return previous.filter(pl.col("report_date") == pl.col("report_date").max())


def mitigation(data_dir: str | Path) -> pl.DataFrame | None:
    root = resolve_path(data_dir)
    paths = sorted(
        p
        for p in root.rglob("*")
        if "mitigation" in p.name.lower()
        and p.suffix.lower() in {".csv", ".parquet", *EXCEL_SUFFIXES}
    ) if root.is_dir() else []
    if not paths:
        return None
    return pl.concat([read_table(p) for p in paths], how="vertical_relaxed")


def transform(
    cm: pl.DataFrame,
    lm: pl.DataFrame,
    mitigation_table: pl.DataFrame | None = None,
    categories: list[str] | None = None,
) -> pl.DataFrame:
    """Brand rows sorted by reduction, with a final ``Grand Total`` row."""
    cm_lf = cm.lazy().select(
        batch_key(), BRAND, "total_closing_stock_bdt"
    )
    lm_lf = lm.lazy().select(batch_key(), BRAND, "total_closing_stock_bdt")

    if mitigation_table is not None:
        tags = (
            mitigation_table.lazy()
            .select(
                batch_key(),
                pl.col("mitigation_status_category").cast(pl.String),
                pl.col("remarks").cast(pl.String),
            )
            .unique(KEY, keep="first", maintain_order=True)
        )
        cm_lf = cm_lf.join(tags, on=KEY, how="left", validate="m:1", maintain_order="left")
        lm_lf = lm_lf.join(
            tags.select(KEY, "mitigation_status_category"),
            on=KEY,
            how="left",
            validate="m:1",
        )
        if categories:
            keep = pl.col("mitigation_status_category").is_in(categories)
            cm_lf, lm_lf = cm_lf.filter(keep), lm_lf.filter(keep)
    else:
        cm_lf = cm_lf.with_columns(pl.lit(None, dtype=pl.String).alias("remarks"))

    # New inclusion = CM batch key not present in LM: one left join, no loops.
    cm_lf = cm_lf.join(
        lm.lazy().select(batch_key()).unique().with_columns(pl.lit(True).alias("_in_lm")),
        on=KEY,
        how="left",
        maintain_order="left",
    ).with_columns(pl.col("_in_lm").is_null().alias("_new"))

    bdt = pl.col("total_closing_stock_bdt")
    old, new = ~pl.col("_new"), pl.col("_new")
    lm_side = lm_lf.group_by(BRAND).agg(bdt.sum().alias("lm_stock_bdt"))
    cm_side = cm_lf.group_by(BRAND, maintain_order=True).agg(
        pl.when(old.any()).then(bdt.filter(old).sum()).alias("cm_no_new_bdt"),
        pl.when(new.any()).then(bdt.filter(new).sum()).alias("cm_new_bdt"),
        pl.col("remarks")
        .drop_nulls()
        .unique(maintain_order=True)
        .str.join(", ")
        .alias("remarks"),
    )

    brands = (
        lm_side.join(cm_side, on=BRAND, how="full", coalesce=True)
        .rename({BRAND: "brand"})
        .with_columns(
            pl.when(pl.col("lm_stock_bdt").is_not_null())
            .then(pl.col("lm_stock_bdt") - pl.col("cm_no_new_bdt").fill_null(0.0))
            .alias("reduced_bdt"),
            pl.when(pl.col("remarks") == "").then(None).otherwise(pl.col("remarks")).alias("remarks"),
        )
        .with_columns(
            (pl.col("reduced_bdt") / pl.col("lm_stock_bdt")).alias("reduction_pct")
        )
        .sort(["reduced_bdt", "brand"], descending=[True, False], nulls_last=True)
        .select(OUTPUT)
        .collect()
    )

    # Distinct remark phrases across all brands, first-seen order.
    total = brands.select(
        pl.lit(TOTAL).alias("brand"),
        pl.col("lm_stock_bdt").sum(),
        pl.col("cm_no_new_bdt").sum(),
        pl.col("cm_new_bdt").sum(),
        pl.col("reduced_bdt").sum(),
        (pl.col("reduced_bdt").sum() / pl.col("lm_stock_bdt").sum()).alias("reduction_pct"),
        pl.col("remarks")
        .drop_nulls()
        .str.split(", ")
        .explode(empty_as_null=True)
        .unique(maintain_order=True)
        .str.join(", ")
        .alias("remarks"),
    ).with_columns(pl.col("remarks").replace("", None))
    return pl.concat([brands, total], how="vertical_relaxed")


def build(
    data_dir: str | Path = "data/current",
    history_dir: str | Path = "data/history",
    categories: list[str] | None = None,
    **_: object,
) -> pl.DataFrame:
    cm = report.build(data_dir)
    report_date = cm.get_column("report_date").item(0)
    return transform(
        cm, last_month(history_dir, report_date), mitigation(data_dir), categories
    )


if __name__ == "__main__":
    run(build)

"""Inventory Evolution: month-to-month stock flow graphs for the five Sankey lenses.

A batch's stock is carried month to month by its batch total first; its category mix is then
explained from the two snapshots. Every carried amount belongs to a lineage, one per batch and
month in which the batch grew:

    alive(lineage, month) = growth in its entry month x product of keep ratios since
    keep(batch, month)    = min(1, stock this month / stock last month)

so every lens reduces to Polars joins over a (lineage x month) table, with no per-slice loop.

    uv run python -m src.evolution --history-dir history --data-dir sample_input --output-dir output/evolution
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shutil
import threading
import time
import weakref
from pathlib import Path

import polars as pl

from .io import EXCEL_SUFFIXES, resolve_path
from .saved_reports import read_saved_report

EPS = 1e-9
CR = 1e7
RSL_ORDER = [">10M", "6M–10M", "3M–6M", "<3M", "Unknown"]
RSL_COLOR = {"<3M": "#DC2626", "3M–6M": "#F59E0B", "6M–10M": "#2563EB", ">10M": "#059669", "Unknown": "#64748B"}
# Policy order, top to bottom, for category nodes and for the bands in each month pillar; Added/New always sorts after these.
CATEGORIES = [
    ("sales_push", "Sales push", "#2f80ed"),
    ("rotation", "Rotation", "#9b51e0"),
    ("strategic_misc", "Strategic Misc", "#8b5cf6"),
    ("incorrect_stock", "Incorrect stock", "#f2994a"),
    ("tbc", "TBC", "#f2c94c"),
    ("not_actionable", "Not actionable", "#27ae60"),
    ("good_stock", "Good stock", "#059669"),
    ("unclassified", "Unclassified", "#64748B"),
]
CATEGORY_ORDER = {key: i + 1 for i, (key, _, _) in enumerate(CATEGORIES)}
CATEGORY_INFO = {key: (label, color) for key, label, color in CATEGORIES}
EXITS = {
    "resolved": ("Resolved/Reduced", "#00a896", 91),
    "sec_sales": ("Sec Sales", "#00A896", 91),
    "sales_unavailable": ("Sales unavailable", "#94A3B8", 91.5),
    "rotated_adjusted": ("Rotated/Adjusted", "#64748B", 92),
}
ADDED_COLOR = "#eb5757"
HUB_COLOR = "#23395d"

LENSES = [
    {"id": "exposure", "key": "exposure_bdt", "label": "Exposure — Mitigation Categories", "short": "Exposure", "hub": "exposure", "base": "exposure", "rsl": False, "split": False, "cohort": False},
    {"id": "lnd", "key": "lnd_fc_bdt", "label": "L&D FC — Mitigation Categories", "short": "L&D FC", "hub": "L&D FC", "base": "lnd_fc", "rsl": False, "split": False, "cohort": False},
    {"id": "stock", "key": "total_closing_stock_bdt", "label": "Total Closing Stock — Mitigation Categories", "short": "Closing stock", "hub": "stock", "base": "total_closing_stock", "rsl": False, "split": True, "cohort": False},
    {"id": "rsl", "key": "rsl_group_total_closing_stock_bdt", "label": "RSL Group — Total Closing Stock", "short": "RSL", "hub": "stock", "base": "total_closing_stock", "rsl": True, "split": True, "cohort": False},
    {"id": "cohort", "key": "over_10m_exposure_cohort_bdt", "label": "Exposure Originating in >10M Cohort", "short": ">10M cohort", "hub": "exposure", "base": "exposure", "rsl": False, "split": False, "cohort": True},
]
LENS_BY_ID = {lens["id"]: lens for lens in LENSES}
UNITS = {"bdt": {"div": CR, "label": "Cr", "title": "Cr BDT", "scale": 1.0}, "kl": {"div": 1000.0, "label": "KL", "title": "KL", "scale": 1000.0}}

SCHEMA = 15
MAX_SELECTION_DEPTH = 32
FILTER_KEYS = ("town", "parent", "material", "brand", "rsl")
MIN_LINK = 0.5  # a link below half a BDT (or half a litre) is rounding noise; the cut is made after summing the link
LINEAGE_FLOOR = {"bdt": 100.0, "kl": 0.12}  # smaller lineage slivers stay in totals but not in the hover-trace lists
_NUMBERS = ["total_closing_stock_bdt", "total_closing_stock_kl", "exposure_bdt", "exposure_kl", "lnd_fc_bdt", "lnd_fc_kl"]


# ---------------------------------------------------------------- history loading

def _cache_dir() -> Path:
    root = os.environ.get("FSNE_CACHE_DIR")
    return Path(root) if root else Path(__file__).resolve().parent.parent / ".cache" / "evolution"


def _first(frame: pl.DataFrame, names: tuple[str, ...]) -> str | None:
    return next((n for n in names if n in frame.columns), None)


def _text(frame: pl.DataFrame, names: tuple[str, ...]) -> pl.Expr:
    name = _first(frame, names)
    return pl.col(name).cast(pl.String).str.strip_chars() if name else pl.lit(None, dtype=pl.String)


def _number(frame: pl.DataFrame, name: str) -> pl.Expr:
    if name in frame.columns:
        return pl.col(name).cast(pl.Float64, strict=False).fill_null(0.0).alias(name)
    return pl.lit(0.0).alias(name)


def _normalise(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.select(
        pl.col("report_date").cast(pl.String).str.slice(0, 10).str.to_date(strict=False).alias("date"),
        pl.col("batch_index").cast(pl.String).str.strip_chars().alias("batch"),
        _text(frame, ("Brand Name",)).fill_null("Unknown brand").alias("brand"),
        _text(frame, ("Town",)).fill_null("Unknown town").alias("town"),
        _text(frame, ("parent_material_code",)).fill_null("Unknown parent").alias("parent"),
        _text(frame, ("material_desc",)).fill_null("").alias("material"),
        _text(frame, ("Material Code", "material_code")).fill_null("").alias("material_code"),
        _text(frame, ("Batch code",)).fill_null("").alias("batch_code"),
        _text(frame, ("rsl_group",)).alias("rsl_text"),
        pl.col("rsl_days").cast(pl.Float64, strict=False).alias("rsl_days") if "rsl_days" in frame.columns else pl.lit(None, dtype=pl.Float64).alias("rsl_days"),
        _text(frame, ("mitigation_status_category", "mitigation_status")).alias("status"),
        _text(frame, ("action_plan_remarks", "action_plan")).fill_null("").alias("remarks"),
        *[_number(frame, n) for n in _NUMBERS],
    )


def history_files(history_dir: str | Path) -> list[Path]:
    root = resolve_path(history_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.suffix.lower() in EXCEL_SUFFIXES and not p.name.startswith("~$"))


def _stat_key(path: Path) -> str:
    st = path.stat()
    return f"{path.name}:{st.st_size}:{st.st_mtime_ns}"


def load_history(history_dir: str | Path, cache_dir: Path | None = None) -> pl.DataFrame:
    """Normalised rows of every saved report. Each workbook is parsed once and cached as parquet."""
    paths = history_files(history_dir)
    if not paths:
        raise FileNotFoundError(f"No saved reports in {history_dir}")
    cache = cache_dir or _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    frames = []
    for path in paths:
        if path.with_suffix(".parquet").is_file():
            path = path.with_suffix(".parquet")
        digest = hashlib.blake2b(f"{SCHEMA}:{path.resolve()}:{_stat_key(path)}".encode(), digest_size=10).hexdigest()
        owner = hashlib.blake2b(str(path.resolve()).encode(), digest_size=6).hexdigest()
        target = cache / f"{owner}-{path.stem}-{digest}.parquet"
        if target.exists():
            frames.append(pl.read_parquet(target))
            continue
        frame = _normalise(read_saved_report(path))
        tmp = target.with_suffix(f".{os.getpid()}.tmp")
        frame.write_parquet(tmp, compression="zstd")
        tmp.replace(target)
        frames.append(frame)
        for stale in cache.glob(f"{owner}-{path.stem}-*.parquet"):
            if stale != target:
                with contextlib.suppress(FileNotFoundError, PermissionError):
                    stale.unlink(missing_ok=True)
    for old in cache.glob("*.parquet"):  # this module's own files from before the per-path prefix: <report stem>-<20 hex>.parquet
        if re.fullmatch(r"\d{4}_\d{2}_\d{2}-[0-9a-f]{20}\.parquet", old.name):
            with contextlib.suppress(FileNotFoundError, PermissionError):
                old.unlink(missing_ok=True)
    history = pl.concat(frames, how="vertical_relaxed").filter(pl.col("date").is_not_null() & (pl.col("batch") != ""))
    return history


def load_sales(data_dir: str | Path | None) -> pl.DataFrame | None:
    """Monthly secondary sales by batch (KL), or None when the run has no secondary-sales workbook."""
    if data_dir is None:
        return None
    from . import secondary

    try:
        raw = secondary.load(data_dir)
    except (FileNotFoundError, ValueError, pl.exceptions.PolarsError):
        return None
    if raw.width < 6 or raw.is_empty():
        return None
    raw = raw.select(raw.columns[:6]).rename(dict(zip(raw.columns[:6], ["town", "parent", "material", "batch_nbr", "month", "kl"], strict=True)))
    return (
        raw.select(
            (pl.col("town").cast(pl.String).str.strip_chars().str.to_lowercase() + pl.col("material").cast(pl.String).str.strip_chars() + pl.col("batch_nbr").cast(pl.String).str.strip_chars()).alias("batch"),
            (pl.lit("01-") + pl.col("month").cast(pl.String).str.strip_chars()).str.to_date("%d-%b-%Y", strict=False).alias("month"),
            pl.col("kl").cast(pl.Float64, strict=False).fill_null(0.0).clip(lower_bound=0.0),
        )
        .filter(pl.col("month").is_not_null())
        .with_columns((pl.col("month").dt.year() * 12 + pl.col("month").dt.month()).alias("ym"))
        .group_by("batch", "ym")
        .agg(pl.col("kl").sum())
    )


# ---------------------------------------------------------------- classification

def _rsl_bucket() -> pl.Expr:
    compact = pl.col("rsl_text").fill_null("").str.to_lowercase().str.replace_all(r"\s", "").str.replace_all("–", "-")
    days = pl.col("rsl_days")
    from_days = (
        pl.when(days.is_null() | days.is_nan() | (days < 0)).then(pl.lit("Unknown"))
        .when(days < 90).then(pl.lit("<3M")).when(days < 180).then(pl.lit("3M–6M"))
        .when(days < 300).then(pl.lit("6M–10M")).otherwise(pl.lit(">10M"))
    )
    return (
        pl.when(compact.is_in([">10m", ">=10m"])).then(pl.lit(">10M"))
        .when(compact == "6m-10m").then(pl.lit("6M–10M")).when(compact == "3m-6m").then(pl.lit("3M–6M"))
        .when(compact == "<3m").then(pl.lit("<3M")).when(compact == "unknown").then(pl.lit("Unknown"))
        .otherwise(from_days)
    )


def _mitigation() -> pl.Expr:
    """Mitigation category id; 'good_stock', 'blended' (sales push and rotation, split 50/50) or null."""
    status = pl.col("status").fill_null("").str.to_lowercase()
    remark = pl.col("remarks").fill_null("").str.strip_chars().str.to_lowercase()
    sales = remark.str.contains("sales push", literal=True) | remark.str.replace_all(" ", "").str.contains("salespush", literal=True)
    rotation = remark.str.contains("rotation", literal=True) | remark.str.contains("rotate", literal=True)
    action = (
        pl.when(remark == "").then(pl.lit("tbc"))
        .when(sales & rotation).then(pl.lit("blended"))
        .when(sales).then(pl.lit("sales_push")).when(rotation).then(pl.lit("rotation"))
        .otherwise(pl.lit("strategic_misc"))
    )
    return (
        pl.when(status == "").then(None)
        .when(status.str.contains("good", literal=True)).then(pl.lit("good_stock"))
        .when(status.str.contains("not actionable", literal=True)).then(pl.lit("not_actionable"))
        .when(status.str.contains("actionable", literal=True)).then(action)
        .when(status.str.contains("incorrect", literal=True)).then(pl.lit("incorrect_stock"))
        .otherwise(None)
    )


class Prepared:
    """History rows with batch and month indexes, classification and the report-date calendar."""

    def __init__(self, history: pl.DataFrame, sales: pl.DataFrame | None):
        self.dates: list[dt.date] = sorted(history["date"].unique().to_list())
        index = {d: i for i, d in enumerate(self.dates)}
        batches = history.select("batch").unique().sort("batch").with_row_index("b").with_columns(pl.col("b").cast(pl.Int64))
        self.batches = batches
        self.frame = (
            history.join(batches, on="batch")
            .with_columns(
                pl.col("date").replace_strict(index, return_dtype=pl.Int64).alias("j"),
                _rsl_bucket().alias("rsl"),
                _mitigation().alias("mit"),
            )
        )
        self.sales = sales
        self.sales_months = set(sales["ym"].unique().to_list()) if sales is not None else set()
        rate = (
            self.frame.group_by("b", "j").agg(pl.col("total_closing_stock_bdt").sum().alias("bdt"), pl.col("total_closing_stock_kl").sum().alias("kl"))
            .filter(pl.col("kl") > EPS).with_columns((pl.col("bdt") / pl.col("kl")).alias("rate")).select("b", "j", "rate")
        )
        self.rate = rate

    def scoped(self, filters: dict[str, list[str]], as_of: dt.date | None = None) -> Prepared:
        """Match each snapshot's rows, preserving batch ids and the calendar through the report date."""
        scoped = copy.copy(self)
        if as_of is not None:
            scoped.dates = [date for date in self.dates if date <= as_of]
            scoped.frame = scoped.frame.filter(pl.col("date") <= as_of)
        for name, values in filters.items():
            scoped.frame = scoped.frame.filter(pl.col("material_code" if name == "material" else name).is_in(values))
        return scoped

    @property
    def months(self) -> int:
        return len(self.dates)


def display_month(date: dt.date) -> dt.date:
    """A snapshot is the closing stock of its month; the chart shows it as the next month's opening."""
    return (date.replace(day=1) + dt.timedelta(days=32)).replace(day=1)


def month_labels(dates: list[dt.date]) -> list[str]:
    shown = [display_month(d) for d in dates]
    with_year = len(dates) > 12 or len({d.year for d in shown}) > 1
    return [d.strftime("%b %y") if with_year else d.strftime("%b") for d in shown]


# ---------------------------------------------------------------- one lens

def _cohort_scope(prep: Prepared) -> pl.DataFrame:
    """Batch-months in the >10M exposure cohort: admitted while >10M, kept while exposure stays includable."""
    f = prep.frame
    includable = f.filter(
        (pl.col("exposure_bdt") > EPS) & pl.col("mit").is_not_null() & (pl.col("mit") != "good_stock")
    ).select("j", "b", "rsl")
    active: set[int] = set()
    parts = []
    for j in range(prep.months):
        month = includable.filter(pl.col("j") == j)
        present = set(month["b"].to_list())
        entry = set(month.filter(pl.col("rsl") == ">10M")["b"].to_list())
        active = (active | entry) & present
        parts.append(pl.DataFrame({"j": [j] * len(active), "b": sorted(active)}, schema={"j": pl.Int64, "b": pl.Int64}))
    return pl.concat(parts)


def _category_rows(prep: Prepared, lens: dict, unit: str) -> tuple[pl.DataFrame, list[dict]]:
    """Long (j, b, cat, v) rows for the lens plus what each month leaves out."""
    column = f"{lens['base']}_{unit}"
    f = prep.frame.with_columns(pl.col(column).alias("v"))
    excluded = []
    if lens["rsl"]:
        long = f.filter(pl.col("v") > EPS).with_columns(("rsl:" + pl.col("rsl")).alias("cat"))
        long = long.group_by("j", "b", "cat").agg(pl.col("v").sum())
        return long, [{} for _ in range(prep.months)]
    scoped = f
    outside = f.head(0)
    if lens["cohort"]:
        scope = _cohort_scope(prep)
        keep = scoped.join(scope, on=["j", "b"], how="semi")
        outside = scoped.join(scope, on=["j", "b"], how="anti").filter(pl.col("mit").is_not_null() & (pl.col("mit") != "good_stock"))
        scoped = keep
    base = scoped.filter(pl.col("mit").is_not_null() & (pl.col("mit") != "good_stock") & (pl.col("v") > EPS))
    plain = base.filter(pl.col("mit") != "blended").select("j", "b", pl.col("mit").alias("cat"), "v")
    blended = base.filter(pl.col("mit") == "blended")
    halves = [blended.select("j", "b", pl.lit(name).alias("cat"), pl.col("v") * 0.5) for name in ("sales_push", "rotation")]
    long = pl.concat([plain, *halves]).group_by("j", "b", "cat").agg(pl.col("v").sum())
    good = scoped.filter((pl.col("mit") == "good_stock") & (pl.col("v") > EPS)).group_by("j").agg(pl.col("v").sum())
    unclassified = scoped.filter(pl.col("mit").is_null() & (pl.col("v") > EPS)).group_by("j").agg(pl.col("v").sum())
    other = outside.filter(pl.col("v") > EPS).group_by("j").agg(pl.col("v").sum())
    g, u, o = (dict(x.iter_rows()) for x in (good, unclassified, other))
    for j in range(prep.months):
        excluded.append({"good": g.get(j, 0.0), "unclassified": u.get(j, 0.0), "outside": o.get(j, 0.0)})
    return long, excluded


def _wide_totals(long: pl.DataFrame, months: int) -> pl.DataFrame:
    wide = long.group_by("b", "j").agg(pl.col("v").sum()).pivot(on="j", index="b", values="v").fill_null(0.0)
    for j in range(months):
        if str(j) not in wide.columns:
            wide = wide.with_columns(pl.lit(0.0).alias(str(j)))
    wide = wide.select(pl.col("b").cast(pl.Int64), *[pl.col(str(j)).cast(pl.Float64).alias(f"T{j}") for j in range(months)])
    keeps = [
        pl.when(pl.col(f"T{j}") > 0).then(pl.min_horizontal(f"T{j}", f"T{j + 1}") / pl.col(f"T{j}")).otherwise(0.0).alias(f"K{j}")
        for j in range(months - 1)
    ]
    return wide.with_columns(*keeps, pl.lit(0.0).alias(f"K{months - 1}"))


def _layers(wide: pl.DataFrame, months: int) -> pl.DataFrame:
    """One row per (batch, entry month e, month j >= e) while the lineage still holds stock."""
    rows = []
    for e in range(months):
        alive = pl.col("T0") if e == 0 else (pl.col(f"T{e}") - pl.col(f"T{e - 1}")).clip(lower_bound=0.0)
        for j in range(e, months):
            rows.append(
                wide.select(
                    "b", pl.lit(e, dtype=pl.Int64).alias("e"), pl.lit(j, dtype=pl.Int64).alias("j"),
                    alive.alias("alive"), pl.col(f"K{j}").alias("keep"), pl.col(f"T{j}").alias("total"),
                ).filter(pl.col("alive") > EPS)
            )
            alive = alive * pl.col(f"K{j}")
    return pl.concat(rows)


def build_lens(prep: Prepared, lens: dict, unit: str) -> dict:
    started = time.perf_counter()
    long, excluded = _category_rows(prep, lens, unit)
    if long.is_empty():
        return _empty_payload(prep, lens, unit)
    links, transitions, attrs = _flow_rows(prep, lens, unit, long)
    payload = _assemble(prep, lens, unit, links, transitions, attrs, excluded, UNITS[unit]["scale"])
    payload["timing"] = {"link_rows": links.height, "transition_rows": transitions.height, "lineages": attrs.height}
    payload["_compute_s"] = round(time.perf_counter() - started, 3)
    return payload


def _flow_rows(prep: Prepared, lens: dict, unit: str, long: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Unrounded flow quantities shared by the overview and exact selection resolver."""
    months = prep.months
    scale = UNITS[unit]["scale"]
    wide = _wide_totals(long, months)
    layers = _layers(wide, months)
    entries = layers.filter(pl.col("e") == pl.col("j")).select("b", "e").sort("b", "e").with_row_index("lid").with_columns(pl.col("lid").cast(pl.Int64))
    layers = layers.join(entries, on=["b", "e"])
    share = long.with_columns((pl.col("v") / pl.col("v").sum().over("b", "j")).alias("share")).select("b", "j", "cat", "share")
    x = layers.join(share, on=["b", "j"])

    def node(kind: str, month: pl.Expr | int, cat: pl.Expr | str | None = None) -> pl.Expr:
        month_expr = month if isinstance(month, pl.Expr) else pl.lit(month)
        if kind == "month":
            return pl.format("month:M{}", month_expr + 1)
        if kind == "category":
            return pl.format("category:M{}:{}", month_expr + 1, cat)
        return pl.format("outcome:M{}:{}", month_expr + 1, cat)

    last = months - 1
    link_parts = [
        # opening stock of a month -> its categories
        x.select("lid", node("month", pl.col("j")).alias("s"), node("category", pl.col("j"), pl.col("cat")).alias("t"), (pl.col("alive") * pl.col("share")).alias("val")),
    ]
    moving = x.filter(pl.col("j") < last)
    link_parts.append(
        moving.select("lid", node("category", pl.col("j"), pl.col("cat")).alias("s"), node("month", pl.col("j") + 1).alias("t"), (pl.col("alive") * pl.col("keep") * pl.col("share")).alias("val"))
    )
    added = layers.filter((pl.col("e") == pl.col("j")) & (pl.col("e") > 0))
    link_parts.append(added.select("lid", pl.format("outcome:M{}:added", pl.col("e")).alias("s"), node("month", pl.col("e")).alias("t"), pl.col("alive").alias("val")))

    exits = moving.select("lid", "b", "j", "cat", (pl.col("alive") * (1 - pl.col("keep")) * pl.col("share")).alias("val")).filter(pl.col("val") > EPS)
    if lens["split"]:
        exits = _split_exits(prep, wide, exits, unit)
        for kind in ("sec_sales", "rotated_adjusted", "sales_unavailable"):
            link_parts.append(
                exits.filter(pl.col(kind) > EPS).select("lid", node("category", pl.col("j"), pl.col("cat")).alias("s"), node("outcome", pl.col("j"), pl.lit(kind)).alias("t"), pl.col(kind).alias("val"))
            )
    else:
        link_parts.append(exits.select("lid", node("category", pl.col("j"), pl.col("cat")).alias("s"), node("outcome", pl.col("j"), pl.lit("resolved")).alias("t"), "val"))

    carried = moving.select(
        "lid", node("month", pl.col("j") + 1).alias("junction"), node("category", pl.col("j"), pl.col("cat")).alias("s"),
        pl.col("b"), pl.col("j"), (pl.col("alive") * pl.col("keep")).alias("carry"), pl.col("share").alias("share_src"),
    )
    nxt = share.select("b", (pl.col("j") - 1).alias("j"), pl.col("cat").alias("dcat"), pl.col("share").alias("share_dst"))
    transitions = carried.join(nxt, on=["b", "j"]).select(
        "lid", "junction", "s", node("category", pl.col("j") + 1, pl.col("dcat")).alias("t"), (pl.col("carry") * pl.col("share_src") * pl.col("share_dst")).alias("val"),
    )
    added_tr = added.join(share.rename({"cat": "dcat", "share": "share_dst"}), on=["b", "j"]).select(
        "lid", node("month", pl.col("e")).alias("junction"), pl.format("outcome:M{}:added", pl.col("e")).alias("s"), node("category", pl.col("e"), pl.col("dcat")).alias("t"), (pl.col("alive") * pl.col("share_dst")).alias("val"),
    )
    # the first month has no earlier month, so its opening stock is its own source (keeps every month hub covered)
    opening_tr = x.filter(pl.col("j") == 0).select(
        "lid", pl.lit("month:M1").alias("junction"), pl.lit("month:M1").alias("s"), node("category", 0, pl.col("cat")).alias("t"), (pl.col("alive") * pl.col("share")).alias("val"),
    )
    transitions = pl.concat([transitions, added_tr, opening_tr])

    # a fixed row order makes every float sum below add in the same order, so rebuilds are byte-identical
    links = pl.concat(link_parts).with_columns(pl.col("val") * scale).sort("s", "t", "lid")
    transitions = transitions.with_columns(pl.col("val") * scale).sort("junction", "s", "t", "lid")

    attrs = _lineage_attributes(prep, entries, lens, unit)
    return links, transitions, attrs


def validate_selection_path(path: object) -> list[dict]:
    """Only selectors are accepted from clients; quantities are resolved from saved history."""
    if not isinstance(path, list) or not 1 <= len(path) <= MAX_SELECTION_DEPTH:
        raise InvalidFilter(f"Choose between 1 and {MAX_SELECTION_DEPTH} drill-down steps")
    result = []
    for step in path:
        if not isinstance(step, dict):
            raise InvalidFilter("Invalid drill-down step")
        fields = set(step) - {"hide10"}
        if fields not in ({"node"}, {"source", "target"}) or any(
            not isinstance(step[key], str) or not 0 < len(step[key]) <= 240 for key in fields
        ) or ("hide10" in step and not isinstance(step["hide10"], bool)):
            raise InvalidFilter("Choose a category, RSL group or flow band")
        result.append({key: step[key] for key in sorted(fields)})
        if step.get("hide10"):
            result[-1]["hide10"] = True
    return result


def _history_rows(prep: Prepared, lens: dict, unit: str) -> pl.DataFrame:
    """Keep both monthly classifications, including stock omitted by mitigation overviews."""
    rows = prep.frame.filter(pl.col(f"{lens['base']}_{unit}") > EPS).select(
        "b", "j", "rsl", pl.col("mit").fill_null("unclassified"), pl.col(f"{lens['base']}_{unit}").alias("v"),
    )
    plain = rows.filter(pl.col("mit") != "blended")
    blended = rows.filter(pl.col("mit") == "blended")
    halves = [blended.with_columns(pl.lit(cat).alias("mit"), pl.col("v") * 0.5) for cat in ("sales_push", "rotation")]
    return pl.concat([plain, *halves]).group_by("b", "j", "rsl", "mit").agg(pl.col("v").sum())


def _group_history(history: pl.DataFrame, rsl: bool) -> pl.DataFrame:
    cat = "rsl:" + pl.col("rsl") if rsl else pl.col("mit")
    return history.select("b", "j", cat.alias("cat"), "v").group_by("b", "j", "cat").agg(pl.col("v").sum())


def _selection_rows(prep: Prepared, lens: dict, unit: str, step: dict, long: pl.DataFrame) -> tuple[pl.DataFrame, int, str, str]:
    """Resolve a visible node/band against unrounded source flows, never the hover lists."""
    if long.is_empty():
        raise InvalidFilter("The selection has no stock in this report scope")
    links, transitions, attrs = _flow_rows(prep, lens, unit, long)
    source = step["source"] if "source" in step else step["node"]
    target = step.get("target")
    mode = "present"
    if target is None:
        if not re.fullmatch(r"category:M\d+:.+", source):
            raise InvalidFilter("Choose a category, RSL group or flow band")
        selected = links.filter(pl.col("t") == source)
        anchor_id = source
    else:
        selected = links.filter((pl.col("s") == source) & (pl.col("t") == target))
        anchor_id = source
        if source.startswith("outcome:") and source.endswith(":added") and target.startswith("month:"):
            mode, anchor_id = "added", target
        elif source.startswith("category:"):
            mode = "carried" if target.startswith("month:") else "exited"
        elif not source.startswith("month:"):
            raise InvalidFilter("Choose a category, RSL group or flow band")
        # Hiding >10M replaces some visible bands with only their non->10M destinations.
        if step.get("hide10") and lens["rsl"] and mode in {"carried", "added"}:
            selected = transitions.filter(
                (pl.col("s") == source) & (pl.col("junction") == target) & ~pl.col("t").str.ends_with(":rsl:>10M")
            )
    selected = selected.filter(pl.col("val") > EPS)
    if selected.is_empty() or selected["val"].sum() < MIN_LINK:
        raise InvalidFilter("This category or band is not present in the selected report scope")
    anchor = int(anchor_id.split(":")[1][1:]) - 1
    amounts = selected.join(attrs.select("lid", "b"), on="lid").group_by("b").agg((pl.col("val").sum() / UNITS[unit]["scale"]).alias("selected"))
    category_id = source if source.startswith("category:") else target or ""
    category = category_id.split(":", 2)[-1]
    label = category[4:] if category.startswith("rsl:") else CATEGORY_INFO.get(category, ("Added/New" if mode == "added" else "Flow", ""))[0]
    return amounts, anchor, mode, label


def _allocate_selection(raw: pl.DataFrame, source_long: pl.DataFrame, amounts: pl.DataFrame, anchor: int, mode: str, months: int) -> pl.DataFrame:
    """Allocate within the parent's quantity history; later growth never enters.

    Before the anchor, show only the selected surviving quantity from each layer's birth.
    Carry bands condition survival; scope exits distinguish a real reduction from an RSL move.
    """
    wide = _wide_totals(raw, months)
    source_wide = _wide_totals(source_long, months)
    layers = _layers(wide, months)
    pools = layers.filter(pl.col("j") == anchor).select("b", "e", pl.col("alive").alias("anchor_alive"))
    totals = wide.select("b", pl.col(f"T{anchor}").alias("total"), pl.col(f"K{anchor}").alias("keep"))
    if anchor:
        totals = totals.join(wide.select("b", pl.col(f"T{anchor - 1}").alias("previous")), on="b")
    else:
        totals = totals.with_columns(pl.lit(0.0).alias("previous"))
    scoped = source_wide.select("b", pl.col(f"T{anchor}").alias("scope_total"), pl.col(f"K{anchor}").alias("scope_keep"),
                               (pl.col(f"T{anchor - 1}") if anchor else pl.lit(0.0)).alias("scope_previous"))
    pools = pools.join(totals, on="b").join(scoped, on="b").join(amounts, on="b")
    fraction = pl.col("selected") / pl.col("total")
    if mode == "added":
        growth = (pl.col("total") - pl.col("previous")).clip(lower_bound=0.0)
        scope_growth = (pl.col("scope_total") - pl.col("scope_previous")).clip(lower_bound=EPS)
        new_share = (growth / scope_growth).clip(0.0, 1.0)
        fraction = pl.when(pl.col("e") == anchor).then(pl.col("selected") * new_share / growth.clip(lower_bound=EPS)).otherwise(
            pl.col("selected") * (1 - new_share) / (pl.col("total") - growth).clip(lower_bound=EPS))
    survival = pl.col("keep")
    if mode == "carried":
        survival = pl.lit(1.0)
    elif mode == "exited":
        real_exit = pl.col("total") * (1 - pl.col("keep"))
        scope_exit = (pl.col("scope_total") * (1 - pl.col("scope_keep"))).clip(lower_bound=EPS)
        survival = 1 - (real_exit / scope_exit).clip(0.0, 1.0)
    allocation = pools.with_columns((pl.col("anchor_alive") * fraction).alias("allocation"), survival.alias("survival")).select(
        "b", "e", "allocation", "survival", (pl.col("anchor_alive") * pl.col("keep")).alias("next_alive"))
    chosen = layers.join(allocation, on=["b", "e"]).with_columns(
        pl.when(pl.col("j") <= anchor).then(pl.col("allocation")).otherwise(
            pl.col("allocation") * pl.col("survival") * pl.col("alive") / pl.col("next_alive").clip(lower_bound=EPS)).alias("selected_v")
    ).group_by("b", "j").agg(pl.col("selected_v").sum())
    return chosen


def _condition_history(history: pl.DataFrame, lens: dict, step: dict, anchor: int, mode: str, anchor_rsl: list[str]) -> pl.DataFrame:
    """Retain prior conditions and the selected classification at this step's anchor."""
    source = step["source"] if "source" in step else step["node"]
    target = step.get("target", "")
    category_id = source if source.startswith("category:") else target
    keep = pl.lit(True)
    if category_id.startswith("category:"):
        category = category_id.split(":", 2)[2]
        matches = pl.col("rsl") == category[4:] if lens["rsl"] else pl.col("mit") == category
        keep = keep & ((pl.col("j") != anchor) | matches)
    if anchor_rsl:
        keep = keep & ((pl.col("j") != anchor) | pl.col("rsl").is_in(anchor_rsl))
    if step.get("hide10") and lens["rsl"] and mode in {"carried", "added"}:
        destination = anchor + 1 if mode == "carried" else anchor
        keep = keep & ((pl.col("j") != destination) | (pl.col("rsl") != ">10M"))
    return history.filter(keep)


def _selected_lens(lens: dict, rsl: bool) -> dict:
    grouping = "rsl" if rsl else "category"
    measure = "Closing stock" if lens["id"] == "rsl" else lens["short"]
    return {**lens, "key": f"selected_{grouping}_" + lens["key"],
            "label": measure + (" — Selected RSL evolution" if rsl else " — Selected category evolution"),
            "rsl": rsl, "split": False, "cohort": False}


def build_selection_path(prep: Prepared, lens: dict, unit: str, filters: dict[str, list[str]], path: list[dict]) -> dict:
    """Replay exact selections on their parent histories, alternating the displayed grouping."""
    path = validate_selection_path(path)
    source = prep.scoped(filters)
    full = prep.scoped({key: values for key, values in filters.items() if key != "rsl"})
    history = _history_rows(full, lens, unit)
    long, _ = _category_rows(source, lens, unit)
    current_lens = lens
    selections = []
    for index, step in enumerate(path):
        amounts, anchor, mode, label = _selection_rows(full, current_lens, unit, step, long)
        chosen = _allocate_selection(_group_history(history, current_lens["rsl"]), long, amounts, anchor, mode, full.months)
        conditioned = _condition_history(history, current_lens, step, anchor, mode, filters.get("rsl", []) if index == 0 else [])
        mix = conditioned.with_columns((pl.col("v") / pl.col("v").sum().over("b", "j")).alias("share"))
        history = mix.join(chosen, on=["b", "j"]).select(
            "b", "j", "rsl", "mit", (pl.col("share") * pl.col("selected_v")).alias("v"),
        ).filter(pl.col("v") > EPS)
        identifier = step.get("node") or step["source"] + ">" + step["target"]
        selections.append({"id": identifier, "label": label, "kind": "category" if "node" in step else "band", "mode": mode,
                           "anchor": f"M{anchor + 1}", "month": month_labels(full.dates)[anchor], "date": full.dates[anchor].isoformat(),
                           "value": round(amounts["selected"].sum() * UNITS[unit]["scale"], 2),
                           "measure": "Closing stock" if lens["id"] == "rsl" else lens["short"]})
        current_lens = _selected_lens(lens, not current_lens["rsl"])
        long = _group_history(history, current_lens["rsl"])
    links, transitions, attrs = _flow_rows(full, current_lens, unit, long)
    payload = _assemble(full, current_lens, unit, links, transitions, attrs, [{} for _ in full.dates], UNITS[unit]["scale"])
    payload["metadata"]["lens"]["tooltip_mix_mode"] = "none"
    payload["metadata"]["lens"]["included_good_stock_policy"] = "included"
    payload["selection"] = selections[-1]
    payload["selections"] = selections
    payload["selection_path"] = path
    payload["anchor_filters"] = filters
    return payload


def build_selection(prep: Prepared, lens: dict, unit: str, filters: dict[str, list[str]], selection: str) -> dict:
    """The original category-to-RSL endpoint accepts one legacy node/band identifier."""
    if lens["rsl"]:
        raise InvalidFilter("Choose a mitigation category or band for RSL evolution")
    parts = re.split(r">(?=(?:category|month|outcome):)", selection, maxsplit=1)
    step = {"node": selection} if len(parts) == 1 else {"source": parts[0], "target": parts[1]}
    return build_selection_path(prep, lens, unit, filters, [step])


def _split_exits(prep: Prepared, wide: pl.DataFrame, exits: pl.DataFrame, unit: str) -> pl.DataFrame:
    """Split each reduction between secondary sales and rotation/adjustment, batch by batch.

    Sales of the newer snapshot's calendar month explain a batch's reduction up to the observed
    reduction; the rest is Rotated/Adjusted. A month with no sales rows at all is "Sales unavailable".
    """
    months = prep.months
    if prep.sales is None:
        return exits.with_columns(pl.lit(0.0).alias("sec_sales"), pl.lit(0.0).alias("rotated_adjusted"), pl.col("val").alias("sales_unavailable"))
    ym = [snapshot_ym(d) for d in prep.dates]
    covered = pl.DataFrame({"j": list(range(months - 1)), "covered": [ym[j + 1] in prep.sales_months for j in range(months - 1)], "ym": [ym[j + 1] for j in range(months - 1)]})
    sales = prep.sales.join(covered.select("j", "ym"), on="ym").select("j", "batch", "kl").join(prep.batches, on="batch").select("b", "j", "kl")
    per_batch = exits.group_by("b", "j").agg(pl.col("val").sum().alias("reduction"))
    if unit == "bdt":
        # price per KL: the batch's nearest snapshot that reports litres (the newer one first, then the closest earlier one)
        nearest = (
            per_batch.select("b", "j").join(prep.rate.rename({"j": "k"}), on="b")
            .with_columns((pl.col("k") - pl.col("j") - 1).abs().alias("dist"))
            .sort("b", "j", "dist", "k").group_by("b", "j", maintain_order=True).first().select("b", "j", "rate")
        )
        per_batch = per_batch.join(nearest, on=["b", "j"], how="left")
    per_batch = (
        per_batch.join(sales, on=["b", "j"], how="left").join(covered.select("j", "covered"), on="j", how="left")
        .with_columns(pl.col("kl").fill_null(0.0), pl.col("covered").fill_null(False))
    )
    if unit == "bdt":
        per_batch = per_batch.with_columns((pl.col("kl") * pl.col("rate").fill_null(0.0)).alias("sold"))
    else:
        per_batch = per_batch.with_columns(pl.col("kl").alias("sold"))
    per_batch = per_batch.with_columns(
        pl.when(pl.col("reduction") > EPS).then(pl.min_horizontal(pl.col("sold") / pl.col("reduction"), pl.lit(1.0))).otherwise(0.0).alias("ratio")
    ).select("b", "j", "ratio", "covered")
    joined = exits.join(per_batch, on=["b", "j"], how="left").with_columns(pl.col("covered").fill_null(False), pl.col("ratio").fill_null(0.0))
    return joined.with_columns(
        pl.when(pl.col("covered")).then(pl.col("val") * pl.col("ratio")).otherwise(0.0).alias("sec_sales"),
        pl.when(pl.col("covered")).then(pl.col("val") * (1 - pl.col("ratio"))).otherwise(0.0).alias("rotated_adjusted"),
        pl.when(pl.col("covered")).then(0.0).otherwise(pl.col("val")).alias("sales_unavailable"),
    )


def snapshot_ym(date: dt.date) -> int:
    """Year-month key of the snapshot's own calendar month (the month whose sales explain the next reduction)."""
    return date.year * 12 + date.month


def _lineage_attributes(prep: Prepared, entries: pl.DataFrame, lens: dict, unit: str) -> pl.DataFrame:
    """Brand, RSL group and exposure share of each lineage, fixed at the month it entered."""
    f = prep.frame.group_by("b", "j").agg(
        pl.col("brand").sort().first(), pl.col("rsl").sort().first(),
        pl.col(f"exposure_{unit}").sum().alias("exp"), pl.col(f"total_closing_stock_{unit}").sum().alias("stock"),
    ).with_columns(pl.when(pl.col("stock") > EPS).then((pl.col("exp") / pl.col("stock")).clip(0.0, 1.0)).otherwise(0.0).alias("exp_share"))
    return entries.join(f.rename({"j": "e"}), on=["b", "e"], how="left").with_columns(
        pl.col("brand").fill_null("Unknown brand"), pl.col("rsl").fill_null("Unknown"), pl.col("exp_share").fill_null(0.0)
    )


# ---------------------------------------------------------------- payload

def _kept(lids: list, vals: list, digits: int) -> tuple[list, list]:
    """Round lineage values and drop the ones that round to nothing."""
    rounded = sorted((i, round(v, digits)) for i, v in zip(lids, vals, strict=True))
    rounded = [(i, v) for i, v in rounded if v > 0]
    return [i for i, _ in rounded], [v for _, v in rounded]


def _pairs(rows: list, digits: int) -> list:
    return [[k, round(v, digits)] for k, v in rows if v > 10 ** -digits]


def _aggregate(links: pl.DataFrame, attrs: pl.DataFrame, keys: list[str], rsl_lens: bool) -> dict[tuple, dict]:
    """Per (keys): RSL, brand and exposure-mix totals for tooltips."""
    joined = links.join(attrs.select("lid", "brand", "rsl", "exp_share"), on="lid").sort(*keys, "lid")
    out: dict[tuple, dict] = {}
    for name, column in (("rsl", "rsl"), ("brand", "brand")):
        for row in joined.group_by(*keys, column).agg(pl.col("val").sum()).sort(*keys, column).iter_rows():
            out.setdefault(tuple(row[:-2]), {}).setdefault(name, []).append((row[-2], row[-1]))
    if rsl_lens:
        for row in joined.group_by(*keys).agg((pl.col("val") * pl.col("exp_share")).sum().alias("e"), (pl.col("val") * (1 - pl.col("exp_share"))).sum().alias("n")).sort(*keys).iter_rows():
            out.setdefault(tuple(row[:-2]), {})["exposure"] = [("Exposure", row[-2]), ("Non-exposure closing stock", row[-1])]
    return out


def _assemble(prep: Prepared, lens: dict, unit: str, links: pl.DataFrame, transitions: pl.DataFrame, attrs: pl.DataFrame, excluded: list[dict], scale: float) -> dict:
    digits = 1 if unit == "kl" else 0  # whole BDT, tenths of a litre: both far below the Cr/KL display precision
    labels = month_labels(prep.dates)
    months = prep.months
    rsl_lens = lens["rsl"]
    hub_name = lens["hub"]
    nodes: dict[str, dict] = {}

    def month_info(j: int) -> dict:
        return {"month_key": f"M{j + 1}", "month": labels[j]}

    for j in range(months):
        nodes[f"month:M{j + 1}"] = {"id": f"month:M{j + 1}", "label": f"{labels[j]} opening {hub_name}", "kind": "month", "column": 2 * j, "color": HUB_COLOR, "sort": 0, **month_info(j)}

    def category_node(identifier: str) -> dict:
        _, month_part, cat = identifier.split(":", 2)
        j = int(month_part[1:]) - 1
        if rsl_lens:
            name = cat[4:]
            label, color, order, key = name, RSL_COLOR.get(name, "#64748B"), RSL_ORDER.index(name) + 1, name
        else:
            label, color = CATEGORY_INFO[cat]
            order, key = CATEGORY_ORDER[cat], cat
        return {"id": identifier, "label": label, "kind": "category", "column": 2 * j + 1, "color": color, "sort": order, "category": key, **month_info(j)}

    def outcome_node(identifier: str) -> dict:
        _, month_part, kind = identifier.split(":", 2)
        j = int(month_part[1:]) - 1
        if kind == "added":
            label = ("Added/Increased after " if rsl_lens else "Added/New after ") + labels[j]
            return {"id": identifier, "label": label, "kind": "added", "column": 2 * j + 1, "color": ADDED_COLOR, "sort": 90, "category": "added", **month_info(j)}
        name, color, order = EXITS[kind]
        prefix = ("Reduced/Exited after " if rsl_lens else "Resolved/Reduced after ") if kind == "resolved" else f"{name} after "
        return {"id": identifier, "label": prefix + labels[j], "kind": kind, "column": 2 * j + 2, "color": color, "sort": order, "category": kind, "hatch": kind == "sales_unavailable", **month_info(j)}

    for frame in (links, transitions):
        for identifier in sorted(set(frame["s"].to_list()) | set(frame["t"].to_list())):
            if identifier in nodes:
                continue
            nodes[identifier] = category_node(identifier) if identifier.startswith("category:") else outcome_node(identifier)

    floor = LINEAGE_FLOOR[unit]
    link_agg = _aggregate(links, attrs, ["s", "t"], rsl_lens)
    link_rows = (
        links.group_by("s", "t").agg(pl.col("val").sum().alias("value"), pl.col("lid").filter(pl.col("val") >= floor), pl.col("val").filter(pl.col("val") >= floor)).filter(pl.col("value") >= MIN_LINK).sort("s", "t")
    )
    out_links = []
    for s, t, value, lids, vals in link_rows.iter_rows():
        mix = link_agg.get((s, t), {})
        out_links.append({
            "id": f"{s}>{t}", "source": s, "target": t, "value": round(value, 2),
            "li": (kept := _kept(lids, vals, digits))[0], "lv": kept[1],
            "rsl_values": _pairs(mix.get("rsl", []), digits), "brand_values": _pairs(mix.get("brand", []), digits),
            "exposure_mix_values": _pairs(mix.get("exposure", []), digits),
        })
    tr_agg = _aggregate(transitions, attrs, ["junction", "s", "t"], rsl_lens)
    tr_rows = transitions.group_by("junction", "s", "t").agg(pl.col("val").sum().alias("value"), pl.col("lid").filter(pl.col("val") >= floor), pl.col("val").filter(pl.col("val") >= floor)).filter(pl.col("value") >= MIN_LINK).sort("junction", "s", "t")
    out_trs = []
    for i, (junction, s, t, value, lids, vals) in enumerate(tr_rows.iter_rows()):
        mix = tr_agg.get((junction, s, t), {})
        out_trs.append({
            "id": f"T{i}", "junction": junction, "source": s, "target": t, "value": round(value, 2),
            "li": (kept := _kept(lids, vals, digits))[0], "lv": kept[1],
            "rsl_values": _pairs(mix.get("rsl", []), digits), "brand_values": _pairs(mix.get("brand", []), digits),
            "exposure_mix_values": _pairs(mix.get("exposure", []), digits),
        })

    # node tooltips use the dominant side of the node's links
    incoming: dict[str, list[dict]] = {}
    outgoing: dict[str, list[dict]] = {}
    for link in out_links:
        incoming.setdefault(link["target"], []).append(link)
        outgoing.setdefault(link["source"], []).append(link)
    for identifier, nd in nodes.items():
        i, o = incoming.get(identifier, []), outgoing.get(identifier, [])
        side = i if sum(x["value"] for x in i) >= sum(x["value"] for x in o) else o
        for field in ("rsl_values", "brand_values", "exposure_mix_values"):
            totals: dict[str, float] = {}
            for link in side:
                for k, v in link[field]:
                    totals[k] = totals.get(k, 0.0) + v
            nd[field] = _pairs(list(totals.items()), digits)

    used = {k["source"] for k in out_links} | {k["target"] for k in out_links}
    brands = sorted(attrs["brand"].unique().to_list())
    brand_index = {b: i for i, b in enumerate(brands)}
    rsl_index = {g: i for i, g in enumerate(RSL_ORDER)}
    lineage = attrs.sort("lid")
    lens_meta = {
        "key": lens["key"], "lens_key": lens["key"], "label": lens["label"],
        "category_dimension": "rsl_group" if rsl_lens else "mitigation_category",
        "tooltip_mix_mode": "exposure_non_exposure" if rsl_lens else "rsl_mix",
        "included_good_stock_policy": "included" if rsl_lens else "excluded",
    }
    if lens["cohort"]:
        lens_meta["drilldown_scope_column"] = "included_in_over_10m_exposure_cohort_lens"
    uncovered = [labels[j] for j in range(months - 1) if lens["split"] and (prep.sales is None or snapshot_ym(prep.dates[j + 1]) not in prep.sales_months)]
    return {
        "schema": SCHEMA, "lens": lens["id"], "unit": unit, "unit_div": UNITS[unit]["div"], "unit_label": UNITS[unit]["label"],
        "metadata": {"lens": lens_meta, "uses_secondary_sales_exit_split": bool(lens["split"])},
        "months": [{"key": f"M{j + 1}", "date": prep.dates[j].isoformat(), "label": labels[j]} for j in range(months)],
        "nodes": [nd for identifier, nd in nodes.items() if identifier in used],
        "links": out_links, "transitions": out_trs,
        "lineages": {
            "brand": [brand_index[b] for b in lineage["brand"].to_list()],
            "rsl": [rsl_index.get(g, 4) for g in lineage["rsl"].to_list()],
            "exp": [round(v, 3) for v in lineage["exp_share"].to_list()],
            "entry": lineage["e"].to_list(),
        },
        "brands": brands, "rsl_groups": RSL_ORDER,
        "excluded": [{k: round(v * scale, digits) for k, v in row.items()} for row in excluded],
        "sales_unavailable_months": uncovered,
    }


def _empty_payload(prep: Prepared, lens: dict, unit: str) -> dict:
    return {
        "schema": SCHEMA, "lens": lens["id"], "unit": unit, "unit_div": UNITS[unit]["div"], "unit_label": UNITS[unit]["label"],
        "metadata": {"lens": {"key": lens["key"], "lens_key": lens["key"], "label": lens["label"]}}, "months": [], "nodes": [], "links": [], "transitions": [],
        "lineages": {"batch": [], "brand": [], "rsl": [], "exp": [], "entry": []}, "brands": [], "rsl_groups": RSL_ORDER, "excluded": [], "sales_unavailable_months": [],
    }


# ---------------------------------------------------------------- artifacts

def _signature(history_dir: Path, data_dir: Path | None) -> str:
    parts = [str(SCHEMA), str(history_dir.resolve()), str(data_dir.resolve()) if data_dir else ""] + [_stat_key(p) for p in history_files(history_dir)]
    if data_dir is not None:
        from .io import matching_files

        with contextlib.suppress(FileNotFoundError, ValueError):
            parts += [_stat_key(Path(p)) for p in matching_files(data_dir, lambda name: "sec_" in name or "Sec_" in name)]
    return hashlib.blake2b("|".join(parts).encode(), digest_size=12).hexdigest()


def artifact_path(out_dir: Path, lens: str, unit: str, part: str = "") -> Path:
    return out_dir / f"{lens}.{unit}{'.' + part if part else ''}.json.gz"


def split_detail(payload: dict) -> dict:
    """Move per-lineage trace lists out of the first-paint payload; returns them as the lazy detail payload."""
    detail = {"links": {}, "transitions": {}, "lineages": payload.pop("lineages"), "brands": payload.pop("brands"), "rsl_groups": payload["rsl_groups"]}
    for name in ("links", "transitions"):
        for record in payload[name]:
            detail[name][record["id"]] = [record.pop("li"), record.pop("lv")]
    return detail


def _write_gz(path: Path, payload: dict) -> None:
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(gzip.compress(raw, compresslevel=4, mtime=0))
    tmp.replace(path)


def _write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text)
    tmp.replace(path)


_LOCK = threading.Lock()  # short critical sections only: manifest reset and the per-key lock table
_KEY_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
_ACTIVE_SCOPES: dict[Path, int] = {}
MAX_FILTER_SCOPES = 200


class InvalidFilter(ValueError):
    """A dropdown value is not present in this run’s catalog."""
_PREP_LOCK = threading.Lock()
_PREPARED: dict[str, Prepared] = {}


def _prepared(history_dir: Path, data_dir: Path | None, signature: str) -> Prepared:
    with _PREP_LOCK:  # the parsed history is shared by every lens, so it is built once
        if signature not in _PREPARED:
            _PREPARED.clear()
            _PREPARED[signature] = Prepared(load_history(history_dir), load_sales(data_dir))
        return _PREPARED[signature]


def _read_manifest(path: Path) -> dict:
    """The manifest dict, or {} when it is missing or unreadable."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _report_cutoff(as_of: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(as_of) if as_of else None
    except ValueError as exc:
        raise InvalidFilter("Invalid report date; use YYYY-MM-DD") from exc


def filter_options(history_dir: Path, data_dir: Path | None, out_dir: Path, filters: dict[str, list[str]] | None = None, *, as_of: str | None = None) -> dict:
    """Catalogs for the matching report scope, including exact child material codes."""
    cutoff = _report_cutoff(as_of)
    signature = _signature(history_dir, data_dir)
    manifest = _read_manifest(out_dir / "cached" / signature / "manifest.json")
    if not filters and not cutoff and manifest.get("signature") == signature and "filters" in manifest:
        return manifest["filters"]
    prep = _prepared(history_dir, data_dir, signature)
    return _filter_catalog(prep.scoped(filters or {}, cutoff), scoped=bool(filters or cutoff))


def _filter_catalog(prep: Prepared, *, scoped: bool = False) -> dict:
    catalog: dict = {
        name: (RSL_ORDER if name == "rsl" else prep.frame["material_code" if name == "material" else name].unique().sort().to_list())
        for name in FILTER_KEYS
    }
    if scoped:
        present = set(prep.frame["rsl"].unique().to_list())
        catalog["rsl"] = [value for value in RSL_ORDER if value in present]
    materials = prep.frame.select("material_code", "material").unique().sort("material_code", "material")
    catalog["material"] = [code for code in catalog["material"] if code]
    catalog["material_labels"] = {
        row["material_code"]: f'{row["material"]} · {row["material_code"]}' if row["material"] else row["material_code"]
        for row in materials.to_dicts() if row["material_code"]
    }
    return catalog


def _prune_scopes(root: Path, keep: Path) -> None:
    """Under _LOCK: retention is optional; Windows may hold old cache files open."""
    with contextlib.suppress(FileNotFoundError, PermissionError):
        scopes = [p for p in root.iterdir() if p.is_dir()]
        excess = len(scopes) - MAX_FILTER_SCOPES
        if excess <= 0:
            return
        candidates = [p for p in scopes if p != keep and p not in _ACTIVE_SCOPES]
        for old in sorted(candidates, key=lambda p: p.stat().st_mtime_ns)[:excess]:
            try:
                shutil.rmtree(old)
            except (FileNotFoundError, PermissionError):
                continue


@contextlib.contextmanager
def _lease_cache(out: Path):
    """Keep current files leased; a new input signature never replaces old files."""
    with _LOCK:
        out.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(PermissionError):
            os.utime(out, None)  # retention hint, not a prerequisite for reading a graph
        _ACTIVE_SCOPES[out] = _ACTIVE_SCOPES.get(out, 0) + 1
    try:
        yield out
    finally:
        with _LOCK:
            _ACTIVE_SCOPES[out] -= 1
            if not _ACTIVE_SCOPES[out]:
                del _ACTIVE_SCOPES[out]
            _prune_scopes(out.parent, keep=out)


@contextlib.contextmanager
def cached_artifact(history_dir: str | Path, data_dir: str | Path | None, out_dir: str | Path, lens: str, unit: str, filters: dict[str, list[str]] | None = None, *, as_of: str | None = None, selection: str | None = None, selection_path: list[dict] | None = None):
    """Lease a payload through the response read, protecting it from concurrent cache eviction."""
    if lens not in LENS_BY_ID or unit not in UNITS:
        raise ValueError("Unknown lens or unit")
    filters = {name: sorted(set(values)) for name, values in (filters or {}).items() if values and name in FILTER_KEYS}
    cutoff = _report_cutoff(as_of)
    if selection is not None and (not selection or len(selection) > 240):
        raise InvalidFilter("Invalid category or band selection")
    if selection_path is not None:
        selection_path = validate_selection_path(selection_path)
        if selection is not None:
            raise InvalidFilter("Choose one selection format")
    scoped_request = bool(filters or cutoff or selection or selection_path)
    base = Path(out_dir).resolve()
    history_dir = Path(history_dir)
    data_dir = Path(data_dir) if data_dir else None
    signature = _signature(history_dir, data_dir)
    if filters:
        catalog = filter_options(history_dir, data_dir, base)
        for name, values in filters.items():
            if not set(values).issubset(catalog[name]):
                raise InvalidFilter(f"Unknown {name} filter value")
    scope = signature
    if scoped_request:
        scope = hashlib.blake2b(json.dumps({"signature": signature, "filters": filters, "as_of": as_of, "selection": selection, "selection_path": selection_path}, sort_keys=True).encode(), digest_size=12).hexdigest()
    # Legacy root/filtered caches are never read or deleted during an upgrade.
    out = base / "cached" / scope
    with _lease_cache(out):
        with _LOCK:
            key_lock = _KEY_LOCKS.setdefault(f"{out}|{lens}|{unit}", threading.Lock())
        target = artifact_path(out, lens, unit)
        if not target.exists():
            with key_lock:  # one cold build per lens and unit; ready payloads bypass the build lock
                if not target.exists():
                    prep = _prepared(history_dir, data_dir, signature)
                    if selection_path:
                        payload = build_selection_path(prep.scoped({}, cutoff), LENS_BY_ID[lens], unit, filters, selection_path)
                    elif selection:
                        payload = build_selection(prep.scoped({}, cutoff), LENS_BY_ID[lens], unit, filters, selection)
                    else:
                        payload = build_lens(prep.scoped(filters, cutoff) if scoped_request else prep, LENS_BY_ID[lens], unit)
                    payload["filters"] = {key: values for key, values in filters.items() if not (selection or selection_path) or key != "rsl"}
                    payload["as_of"] = as_of
                    _write_payload(out, payload, lens, unit)
        yield target


def ensure(history_dir: str | Path, data_dir: str | Path | None, out_dir: str | Path, lens: str, unit: str, filters: dict[str, list[str]] | None = None) -> Path:
    """Build/cache one artifact. HTTP callers use cached_artifact to keep it leased while reading."""
    with cached_artifact(history_dir, data_dir, out_dir, lens, unit, filters) as path:
        return path


def _write_payload(out: Path, payload: dict, lens: str, unit: str) -> None:
    payload.pop("_compute_s", None)  # keeps payload bytes identical across rebuilds of the same input
    detail = split_detail(payload)
    _write_gz(artifact_path(out, lens, unit, "detail"), detail)
    _write_gz(artifact_path(out, lens, unit), payload)


def build_all(history_dir: str | Path, data_dir: str | Path | None, out_dir: str | Path, units: tuple[str, ...] = ("bdt", "kl")) -> dict:
    """Write every lens and unit as gzip JSON; skip work when the inputs are unchanged."""
    started = time.perf_counter()
    history_dir = Path(history_dir)
    data_dir = Path(data_dir) if data_dir else None
    signature = _signature(history_dir, data_dir)
    out = Path(out_dir).resolve() / "cached" / signature
    with _lease_cache(out):
        manifest_path = out / "manifest.json"
        manifest = _read_manifest(manifest_path)
        if manifest.get("signature") == signature and manifest.get("snapshots") and all(artifact_path(out, spec["id"], u).exists() for spec in LENSES for u in units):
            return {**manifest, "cached": True}
        prep = Prepared(load_history(history_dir), load_sales(data_dir))
        timings = {}
        for lens in LENSES:
            for unit in units:
                payload = build_lens(prep, lens, unit)
                timings[f"{lens['id']}.{unit}"] = payload.get("_compute_s")
                _write_payload(out, payload, lens["id"], unit)
        manifest = {
            "signature": signature, "schema": SCHEMA, "snapshots": [d.isoformat() for d in prep.dates],
            "lenses": [{k: lens[k] for k in ("id", "key", "label", "short", "rsl", "split", "cohort")} for lens in LENSES],
            "filters": _filter_catalog(prep),
            "has_sales": prep.sales is not None, "build_s": round(time.perf_counter() - started, 3), "timings": timings,
        }
        _write_text_atomic(manifest_path, json.dumps(manifest))
        return {**manifest, "cached": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-dir", default="history")
    parser.add_argument("--data-dir", default="sample_input")
    parser.add_argument("--output-dir", default="output/evolution")
    args = parser.parse_args()
    result = build_all(args.history_dir, args.data_dir, args.output_dir)
    print(json.dumps({k: v for k, v in result.items() if k != "lenses"}, indent=1))


if __name__ == "__main__":
    main()

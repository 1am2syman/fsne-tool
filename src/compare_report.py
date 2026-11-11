"""Generate the inferred report and compare every cell to an independent reference."""

import argparse
import hashlib
import json
from pathlib import Path

import polars as pl

from . import report, run_rate_parent, secondary, soh_db, starting_date
from .io import resolve_path
from .report import build

ABS_TOL = 1e-8
REL_TOL = 1e-12
# The reference workbook valued stock with a separate per-material rate workbook; the
# pipeline now uses the shelf-life file's `DB Landing value`. Valuation columns
# therefore differ by design and are reported apart from the physical columns.
VALUATION_MARKER = "bdt"
# The reference drops older batches at 300 days when building its Exposure
# queue. The approved shared 90-day queue intentionally changes these columns.
QUEUE_COLUMNS = (
    "consumable_kl", "consumable_kl_2x_rate", "consumable_bdt",
    "exposure_kl", "exposure_bdt", "rotation_req_kl", "rotation_req_bdt",
    "exposure_kl_%", "exposure_bdt_%",
)


def compare(actual: pl.DataFrame, expected: pl.DataFrame) -> pl.DataFrame:
    """Compare keyed rows, exact strings/integers, tolerant finite float values.

    Invalid structure raises rather than producing a misleading partial score.
    """
    if actual.columns != expected.columns:
        raise ValueError("Column names/order differ from the reference")
    for label, frame in [("actual", actual), ("expected", expected)]:
        if (
            frame["batch_index"].null_count()
            or frame["batch_index"].n_unique() != frame.height
        ):
            raise ValueError(f"{label} batch_index must be non-null and unique")
    if actual.height != expected.height or not actual.select("batch_index").sort(
        "batch_index"
    ).equals(expected.select("batch_index").sort("batch_index")):
        raise ValueError("Row counts or batch key sets differ")
    actual = actual.sort("batch_index")
    expected = expected.sort("batch_index")
    results = []
    for name in expected.columns:
        left, right = actual[name], expected[name]
        null_mismatch = (left.is_null() != right.is_null()).sum()
        if right.dtype.is_float():
            left = left.cast(pl.Float64)
            right = right.cast(pl.Float64)
            difference = (left - right).abs()
            mismatch = (
                (~left.is_finite() | ~right.is_finite())
                | (difference > ABS_TOL + REL_TOL * right.abs())
            ).fill_null(False)
            max_error = difference.max()
            rule = "float_tolerance"
        else:
            if left.dtype != right.dtype:
                raise ValueError(
                    f"Type mismatch for {name}: {left.dtype} versus {right.dtype}"
                )
            mismatch = (left != right).fill_null(False)
            max_error = 0.0 if not mismatch.any() else None
            rule = "exact"
        results.append(
            {
                "column": name,
                "rows_checked": expected.height,
                "null_mismatches": null_mismatch,
                "value_mismatches": mismatch.sum(),
                "max_absolute_error": max_error,
                "comparison": rule,
                "actual_nulls": actual[name].null_count(),
                "expected_nulls": expected[name].null_count(),
            }
        )
    return pl.DataFrame(results)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("sample_input"))
    parser.add_argument('--legacy', action='store_true', help='Compare raw rates against the reference, reporting approved valuation and queue differences separately; use a separate output directory')
    parser.add_argument(
        "--reference", type=Path, default=Path("sample_output/sample_output.xlsx")
    )
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    args = parser.parse_args()
    reference = resolve_path(args.reference).resolve()
    output = resolve_path(args.output_dir).resolve()
    target = output / "inferred_report.xlsx"
    data_root = resolve_path(args.data_dir).resolve()
    if (
        target == reference
        or output.is_relative_to(reference.parent)
        or output.is_relative_to(data_root)
    ):
        raise ValueError(
            "Generated outputs must be separate from the reference directory"
        )
    if args.legacy and output == resolve_path('output').resolve():
        raise ValueError('Legacy verification needs --output-dir output/legacy-compare; do not overwrite production outputs')
    before = hashlib.sha256(reference.read_bytes()).hexdigest()
    input_hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in data_root.rglob("*")
        if path.is_file()
    }
    # Generation has no access to reference values; the reference is read below.
    if args.legacy:
        report_date = starting_date.build(args.data_dir).item(0, 'starting_date')
        stock = soh_db.build(args.data_dir, as_of=report_date).drop('avg_sec')
        rates = run_rate_parent.raw_rates(run_rate_parent.source_through(secondary.load(args.data_dir), report_date))
        stock = stock.join(rates, left_on=['Town', 'parent_material_code'],
                           right_on=['town', 'parent_material_code'], how='left', validate='m:1')
        actual = report.transform(stock, report_date)
    else:
        actual = build(args.data_dir)
    output.mkdir(parents=True, exist_ok=True)
    actual.write_excel(target, worksheet="Sheet1", float_precision=15)
    actual.write_parquet(output / "inferred_report.parquet")
    expected = pl.read_excel(reference, infer_schema_length=None)
    roundtrip = pl.read_excel(target, infer_schema_length=None)
    metrics = compare(actual, expected)
    roundtrip_metrics = compare(roundtrip, actual)
    metrics.write_csv(output / "comparison.csv")
    after = hashlib.sha256(reference.read_bytes()).hexdigest()
    inputs_unchanged = all(
        hashlib.sha256(path.read_bytes()).hexdigest() == digest
        for path, digest in input_hashes.items()
    )
    measures = actual.select(
        pl.col("total_closing_stock_kl").sum(),
        pl.col("total_closing_stock_bdt").sum(),
        pl.col("consumable_kl").sum(),
        pl.col("exposure_kl").sum(),
        pl.col("exposure_bdt").sum(),
        pl.col("rotation_req_kl").sum(),
        pl.col("rotation_req_bdt").sum(),
        pl.col("lnd_fc_kl").sum(),
        pl.col("lnd_fc_bdt").sum(),
    ).row(0, named=True)
    (output / "current_measures.json").write_text(json.dumps(measures, indent=2) + "\n")
    mismatches = pl.sum_horizontal("null_mismatches", "value_mismatches")
    valuation = pl.col("column").str.contains(VALUATION_MARKER)
    queue = pl.col("column").is_in(QUEUE_COLUMNS)
    rate = pl.col("column").is_in(("avg_sec_m", "doh", "lnd_fc_kl", "lnd_fc_kl_%")) if not args.legacy else pl.lit(False)
    physical_mismatches = metrics.filter(~valuation & ~queue & ~rate).select(mismatches.sum()).item()
    rate_differing = metrics.filter(rate & (mismatches > 0)).height
    queue_differing = metrics.filter(queue & (mismatches > 0)).height
    valuation_differing = metrics.filter(valuation & (mismatches > 0)).height
    summary = {
        "status": ("sample_match_except_valuation_and_queue" if args.legacy else "policy_adjusted_expected_difference")
        if physical_mismatches == 0
        else "sample_mismatch",
        "non_policy_mismatched_cells": physical_mismatches,
        "rate_columns_differing": rate_differing,
        "raw_rates": args.legacy,
        "queue_columns_differing": queue_differing,
        "valuation_columns_differing": valuation_differing,
        "rows": actual.height,
        "columns": actual.width,
        "cells_checked": actual.height * actual.width,
        "mismatched_cells": metrics.select(mismatches.sum()).item(),
        "excel_roundtrip_mismatches": roundtrip_metrics.select(
            pl.sum_horizontal("null_mismatches", "value_mismatches").sum()
        ).item(),
        "row_order_matches": actual["batch_index"].equals(expected["batch_index"]),
        "reference_unchanged": before == after,
        "inputs_unchanged": inputs_unchanged,
        "input_sha256": {
            str(path.relative_to(data_root)): digest
            for path, digest in input_hashes.items()
        },
        "reference_sha256": before,
        "absolute_tolerance": ABS_TOL,
        "relative_tolerance": REL_TOL,
        "inferred_not_authoritative": True,
    }
    (output / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
    if (
        not summary["reference_unchanged"]
        or not inputs_unchanged
        or summary["excel_roundtrip_mismatches"]
    ):
        raise AssertionError("Reference integrity or Excel roundtrip failed")
    print(json.dumps(summary, indent=2))
    print("COMPARISON_COMPLETE")
    if physical_mismatches or not summary["row_order_matches"]:
        raise AssertionError(
            "Inferred report differs from reference; inspect comparison artifacts"
        )


if __name__ == "__main__":
    main()

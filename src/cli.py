"""Common command-line boundary for individual query scripts."""

import argparse
from collections.abc import Callable
from pathlib import Path

import polars as pl

from .io import resolve_path


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/current"),
        help="Replacement for Table2.final_path",
    )
    parser.add_argument(
        "--history-dir",
        type=Path,
        default=Path("data/history"),
        help="Replacement for Table2.history_path",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="CSV, Parquet or XLSX file; otherwise print the result",
    )


def execute(build: Callable[..., pl.DataFrame], args: argparse.Namespace) -> None:
    output = resolve_path(args.output) if args.output is not None else None
    if output is not None:
        if output.suffix.lower() not in {".csv", ".parquet", ".xlsx"}:
            raise ValueError("Output must end with .csv, .parquet or .xlsx")
        if any(
            output.resolve().is_relative_to(resolve_path(folder).resolve())
            for folder in ["sample_input", "sample_output"]
        ):
            raise ValueError(
                "Sample inputs/reference are read-only; write generated files to output/"
            )
    frame = build(data_dir=args.data_dir, history_dir=args.history_dir)
    if output is None:
        print(frame)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".csv":
        frame.write_csv(output)
    elif output.suffix.lower() == ".parquet":
        frame.write_parquet(output)
    else:
        frame.write_excel(output, worksheet="Sheet1", float_precision=15)
    print(f"Wrote {frame.height} rows to {output}")


def run(build: Callable[..., pl.DataFrame]) -> None:
    parser = argparse.ArgumentParser(description=build.__module__)
    arguments(parser)
    execute(build, parser.parse_args())

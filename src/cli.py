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
        "--output", type=Path, help="CSV or Parquet file; otherwise print the result"
    )


def execute(build: Callable[..., pl.DataFrame], args: argparse.Namespace) -> None:
    frame = build(data_dir=args.data_dir, history_dir=args.history_dir)
    if args.output is None:
        print(frame)
        return
    output = resolve_path(args.output)
    if output.suffix.lower() not in {".csv", ".parquet"}:
        raise ValueError("Output must end with .csv or .parquet")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".csv":
        frame.write_csv(output)
    else:
        frame.write_parquet(output)
    print(f"Wrote {frame.height} rows to {output}")


def run(build: Callable[..., pl.DataFrame]) -> None:
    parser = argparse.ArgumentParser(description=build.__module__)
    arguments(parser)
    execute(build, parser.parse_args())

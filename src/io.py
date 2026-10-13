"""Workbook boundaries and repository-relative paths; transformations stay in Polars."""

from collections.abc import Callable, Sequence
from pathlib import Path

import openpyxl
import polars as pl

ROOT = Path(__file__).resolve().parent.parent
EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".xlsb"}


def resolve_path(path: str | Path) -> Path:
    """Relative paths are rooted at the repository, never the caller's cwd."""
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def matching_files(
    folder: str | Path, predicate: Callable[[str], bool], *, excel: bool = True
) -> list[Path]:
    root = resolve_path(folder)
    if not root.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {root}")
    paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and predicate(path.name)
        and (not excel or path.suffix.lower() in EXCEL_SUFFIXES)
    )
    if not paths:
        raise FileNotFoundError(f"No matching input files in {root}")
    return paths


def concatenate(frames: Sequence[pl.DataFrame]) -> pl.DataFrame:
    if not frames:
        raise ValueError("No matching worksheets or tables in input workbooks")
    return pl.concat(frames, how="vertical_relaxed")


def raw_workbooks(
    paths: Sequence[Path],
    width: int,
    *,
    sheet: str | None = None,
    include_tables: bool = False,
) -> pl.DataFrame:
    """Read unpromoted sheets, plus named tables where M does not filter Kind.

    String ingestion preserves embedded header rows across multiple files. Loops
    operate on files/workbook metadata only, not on transformation rows.
    """
    frames = []
    columns = [f"Column{i}" for i in range(1, width + 1)]
    for path in paths:
        sheets = pl.read_excel(
            path,
            sheet_name=sheet,
            sheet_id=0 if sheet is None else None,
            has_header=False,
            infer_schema_length=None,
            read_options={"dtypes": "string"},
            drop_empty_rows=False,
            drop_empty_cols=False,
            raise_if_empty=False,
        )
        for frame in sheets.values() if isinstance(sheets, dict) else [sheets]:
            frames.append(_raw_columns(frame, columns))
        if include_tables and path.suffix.lower() in {".xlsx", ".xlsm"}:
            workbook = openpyxl.load_workbook(path, read_only=False, data_only=True)
            try:
                for worksheet in workbook.worksheets:
                    for name in worksheet.tables:
                        frames.append(
                            _raw_columns(
                                pl.read_excel(
                                    path,
                                    table_name=name,
                                    has_header=False,
                                    infer_schema_length=None,
                                    read_options={"dtypes": "string"},
                                    drop_empty_rows=False,
                                    drop_empty_cols=False,
                                ),
                                columns,
                            )
                        )
            finally:
                workbook.close()
    return concatenate(frames)


def _raw_columns(frame: pl.DataFrame, columns: list[str]) -> pl.DataFrame:
    frame = frame.select(frame.columns[: len(columns)])
    return (
        frame.rename(
            dict(zip(frame.columns, columns[: len(frame.columns)], strict=True))
        )
        .with_columns(pl.col(pl.String).replace("", None))
        .with_columns(
            pl.lit(None, dtype=pl.String).alias(name)
            for name in columns[len(frame.columns) :]
        )
        .select(pl.all().cast(pl.String))
    )


def raw_csvs(paths: Sequence[Path], width: int) -> pl.DataFrame:
    columns = [f"Column{i}" for i in range(1, width + 1)]
    return concatenate(
        [
            _raw_columns(
                pl.read_csv(path, has_header=False, infer_schema=False), columns
            )
            for path in paths
        ]
    )


def promote_headers(frame: pl.DataFrame) -> pl.DataFrame:
    """Promote once AFTER concatenation, matching Table.PromoteHeaders."""
    if frame.is_empty():
        raise ValueError("Cannot promote headers from an empty input")
    used: set[str] = set()
    names = []
    for fallback, value in zip(frame.columns, frame.row(0), strict=True):
        base = fallback if value is None else str(value)
        name = base
        suffix = 1
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        names.append(name)
        used.add(name)
    return frame.slice(1).rename(dict(zip(frame.columns, names, strict=True)))


def number(name: str, dtype: pl.DataType | type[pl.DataType] = pl.Float64) -> pl.Expr:
    """Strict numeric conversion; M Int64 uses banker's rounding."""
    value = pl.col(name).cast(pl.String).str.replace_all(",", "").cast(pl.Float64)
    return value.round(0).cast(pl.Int64) if dtype == pl.Int64 else value.cast(dtype)


def date(name: str) -> pl.Expr:
    """Parse ISO, US-locale dates or Excel serial days; reject invalid values.

    The M files do not specify culture, so ambiguous dates use en-US here.
    """
    text = pl.col(name).cast(pl.String)
    parsed = pl.coalesce(
        text.str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False),
        text.str.to_date("%m/%d/%Y", strict=False),
        text.str.to_date("%m/%d/%y", strict=False),
        text.str.to_date("%d-%b-%Y", strict=False),
        text.str.to_date("%d-%b-%y", strict=False),
        (
            pl.date(1899, 12, 30)
            + pl.duration(
                days=text.cast(pl.Float64, strict=False).floor().cast(pl.Int64)
            )
        ),
    )
    # Strict final parsing distinguishes malformed non-null values from nulls.
    return (
        pl.when(text.is_null())
        .then(None)
        .otherwise(parsed.cast(pl.String).fill_null(text))
        .str.to_date("%Y-%m-%d", strict=True)
        .alias(name)
    )


def config_tables(paths: Sequence[Path]) -> pl.DataFrame:
    frames = []
    for path in paths:
        workbook = openpyxl.load_workbook(path, read_only=False, data_only=True)
        try:
            if any("config_table" in sheet.tables for sheet in workbook.worksheets):
                frames.append(
                    pl.read_excel(
                        path,
                        table_name="config_table",
                        infer_schema_length=None,
                        schema_overrides={"variable": pl.String, "value": pl.String},
                    ).select("variable", "value")
                )
        finally:
            workbook.close()
    return concatenate(frames)

"""Workbook boundaries and repository-relative paths; transformations stay in Polars."""

from collections.abc import Callable, Sequence
from pathlib import Path

import fastexcel
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
    numeric_header_column: str | None = None,
) -> pl.DataFrame:
    """Read unpromoted sheets, plus named tables where M does not filter Kind.

    String ingestion preserves embedded header rows across multiple files. Loops
    operate on files/workbook metadata only, not on transformation rows.
    """
    frames = []
    columns = [f"Column{i}" for i in range(1, width + 1)]
    for path in paths:
        if sheet is not None and sheet not in fastexcel.read_excel(path).sheet_names:
            continue
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
        for sheet_name, frame in (
            sheets.items() if isinstance(sheets, dict) else [(sheet, sheets)]
        ):
            if frame.is_empty():
                continue
            raw = _raw_columns(frame, columns)
            if numeric_header_column is not None:
                header_rows = raw.with_row_index("_row").filter(
                    pl.col(numeric_header_column).is_not_null()
                )
                if not header_rows.is_empty():
                    header_row = header_rows.item(0, "_row")
                    # Calamine's forced string conversion rounds Excel numbers.
                    # Read below the header natively, then stringify with Polars.
                    native = pl.read_excel(
                        path,
                        sheet_name=sheet_name,
                        read_options={"header_row": header_row},
                        infer_schema_length=None,
                        drop_empty_rows=False,
                        drop_empty_cols=False,
                        raise_if_empty=False,
                    )
                    raw = pl.concat(
                        [raw.head(header_row + 1), _raw_columns(native, columns)],
                        how="vertical_relaxed",
                    )
            frames.append(raw)
        if include_tables and path.suffix.lower() in {".xlsx", ".xlsm"}:
            workbook = openpyxl.load_workbook(path, read_only=False, data_only=True)
            try:
                for worksheet in workbook.worksheets:
                    for name in worksheet.tables:
                        table = pl.read_excel(
                            path,
                            table_name=name,
                            infer_schema_length=None,
                            read_options={"dtypes": "string"},
                            drop_empty_rows=False,
                            drop_empty_cols=False,
                        )
                        # Named tables already have field names in Excel.Workbook.
                        # M expands Column1..N by name, not by field position.
                        frames.append(
                            table.with_columns(
                                pl.lit(None, dtype=pl.String).alias(column)
                                for column in columns
                                if column not in table.columns
                            )
                            .select(columns)
                            .cast(pl.String)
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
    text = pl.col(name).cast(pl.String).str.replace_all(",", "").str.strip_chars()
    if dtype == pl.Int64:
        # Do not round integer identifiers through Float64 (loses bits above 2**53).
        rounded = (
            text.cast(pl.Float64, strict=False)
            .round(0)
            .cast(pl.Int64, strict=False)
            .cast(pl.String)
            .fill_null(text)
        )
        return (
            pl.when(text.str.contains(r"^[+-]?\d+$"))
            .then(text)
            .otherwise(rounded)
            .cast(pl.Int64)
            .alias(name)
        )
    return text.cast(dtype).alias(name)


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
        pl.concat_str(pl.lit("01-"), text).str.to_date("%d-%b-%Y", strict=False),
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

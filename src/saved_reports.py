"""Read frozen monthly records, preferring Parquet to their Excel export."""
import shutil
from pathlib import Path

import polars as pl


def read_saved_report(path: Path, *, columns: list[str] | None = None) -> pl.DataFrame:
    parquet = path.with_suffix(".parquet")
    if parquet.is_file():
        return pl.read_parquet(parquet, columns=columns)
    return pl.read_excel(path.read_bytes(), sheet_id=1, infer_schema_length=0, columns=columns)


def copy_saved_report(source: Path, target: Path) -> None:
    """Freeze the Excel export and its Parquet record together in a run."""
    shutil.copyfile(source, target)
    parquet = source.with_suffix(".parquet")
    if parquet.is_file():
        shutil.copyfile(parquet, target.with_suffix(".parquet"))

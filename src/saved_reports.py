"""Reuse frozen Parquet records and repair Excel-only history on first use."""
import shutil
import tempfile
import threading
from pathlib import Path

import polars as pl

_REPAIR_LOCK = threading.Lock()


def _parquet_record(path: Path) -> Path:
    """Cache a complete workbook once; never replace an existing record.

    Stage the write beside the workbook so a failed conversion cannot publish a
    partial Parquet. The lock shares first-use repairs across concurrent readers
    in the app process. Saving the cache requires a writable history directory.
    """
    parquet = path.with_suffix(".parquet")
    if parquet.is_file():
        return parquet
    with _REPAIR_LOCK:
        if not parquet.is_file():
            frame = pl.read_excel(path.read_bytes(), sheet_id=1, infer_schema_length=0)
            with tempfile.TemporaryDirectory(prefix=".history-parquet-", dir=path.parent) as directory:
                staged = Path(directory) / "record.parquet"
                frame.write_parquet(staged)
                # Another publisher may have supplied the authoritative record
                # while Excel was being parsed. Keep that record when present.
                if not parquet.is_file():
                    staged.replace(parquet)
    return parquet


def read_saved_report(path: Path, *, columns: list[str] | None = None) -> pl.DataFrame:
    """Read Parquet, persisting the full Excel fallback even for projected reads."""
    return pl.read_parquet(_parquet_record(path), columns=columns)


def copy_saved_report(source: Path, target: Path) -> None:
    """Repair the shared record first, then freeze both formats in a run."""
    parquet = _parquet_record(source)
    shutil.copyfile(source, target)
    shutil.copyfile(parquet, target.with_suffix(".parquet"))

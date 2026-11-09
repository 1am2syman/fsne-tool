"""Run storage and the pipeline worker.

Each run lives in ``runs/RUN-0001/``::

    input/     the five uploaded files under the names the queries look for
    history/   symlinks to the shared history/ plus this run's own snapshot
    output/    pipeline outputs
    index.html the dashboard built for this run
    meta.json  status, timings, detected report date
    log.txt    stdout/stderr of every pipeline step
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = Path(os.environ.get("FSNE_RUNS_DIR", ROOT / "runs"))
SHARED_HISTORY = Path(os.environ.get("FSNE_HISTORY_DIR", ROOT / "history"))
RUN_ID = re.compile(r"^RUN-\d{4,}$")

# slot -> (label, canonical file stem, allowed extensions). The stems are what
# the translated queries match on (see src/io.matching_files callers).
SLOTS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "config": ("Run configuration", "config", (".xlsx", ".xlsm")),
    "landing": ("Stock on hand", "DB Landing for FSNE", (".xlsx", ".xlsm")),
    "secondary": ("Secondary sales, month on month", "Mode_Sec_Month on Month", (".xlsb", ".xlsx", ".xlsm")),
    "hierarchy": ("Product hierarchy", "PH_FG", (".xlsx",)),
    "shelflife": ("Shelf-life report", "Product Shelflife Report", (".csv",)),
}
MAX_UPLOAD = 1 << 30  # 1 GiB


class RunError(Exception):
    """A user-correctable problem, shown verbatim in the UI."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(
        self, runs_dir: Path = RUNS_DIR, history_dir: Path = SHARED_HISTORY, *, start_worker: bool = True
    ) -> None:
        self.dir = runs_dir.resolve()
        self.history_dir = history_dir.resolve()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs: queue.Queue[str | None] = queue.Queue()
        for meta in self.list():
            if meta["status"] in {"queued", "running"}:
                meta.update(status="bad", error="Interrupted: the server restarted during this run")
                self._write(meta)
        self.worker = threading.Thread(target=self._worker, daemon=True, name="fsne-runs")
        if start_worker:
            self.worker.start()

    def close(self) -> None:
        """Finish accepted jobs and then stop the worker."""
        self.jobs.put(None)
        if self.worker.is_alive():
            self.worker.join()

    # ---- storage -------------------------------------------------------
    def path(self, run_id: str) -> Path:
        if not RUN_ID.fullmatch(run_id):
            raise RunError("Unknown run")
        return self.dir / run_id

    def _write(self, meta: dict) -> None:
        with self.lock:
            target = self.path(meta["id"]) / "meta.json"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(meta, indent=1))
            tmp.replace(target)

    def get(self, run_id: str) -> dict:
        try:
            return json.loads((self.path(run_id) / "meta.json").read_text())
        except FileNotFoundError:
            raise RunError("Unknown run") from None

    def update(self, run_id: str, **fields: object) -> dict:
        with self.lock:
            meta = self.get(run_id)
            meta.update(fields)
            self._write(meta)
            return meta

    def list(self) -> list[dict]:
        metas = []
        for p in sorted(self.dir.glob("RUN-*/meta.json"), reverse=True):
            try:
                metas.append(json.loads(p.read_text()))
            except (OSError, ValueError):
                continue
        return sorted(metas, key=lambda m: int(m["id"][4:]), reverse=True)

    # ---- lifecycle -----------------------------------------------------
    def create(self, label: str, *, archive_previous: bool = False) -> dict:
        with self.lock:
            n = 1 + max((int(m["id"][4:]) for m in self.list()), default=0)
            run_id = f"RUN-{n:04d}"
            (self.dir / run_id / "input").mkdir(parents=True)
            meta = {
                "id": run_id,
                "label": label.strip()[:80] or "Untitled run",
                "status": "draft",
                "step": None,
                "created": _now(),
                "report_date": None,
                "rows": None,
                "seconds": None,
                "error": None,
                "archived": None,
                "archive_previous": bool(archive_previous),
                "files": {},
            }
            self._write(meta)
            return meta

    def save_file(self, run_id: str, slot: str, filename: str, stream, length: int) -> dict:
        # Serialize replacement with start(): a worker never reads a partial input.
        with self.lock:
            meta = self.get(run_id)
            if meta["status"] != "draft":
                raise RunError("Files can only be added before the run starts")
            if slot not in SLOTS:
                raise RunError(f"Unknown file slot: {slot}")
            label, stem, allowed = SLOTS[slot]
            ext = Path(filename).suffix.lower()
            if ext not in allowed:
                raise RunError(f"{label}: expected {' / '.join(allowed)}, got {ext or 'no extension'}")
            if not 0 < length <= MAX_UPLOAD:
                raise RunError(f"{label}: file is empty or larger than {MAX_UPLOAD >> 20} MB")
            folder = self.path(run_id) / "input"
            with tempfile.TemporaryDirectory(dir=self.path(run_id)) as staging:
                tmp = Path(staging) / f"{stem}{ext}"
                remaining = length
                with tmp.open("wb") as out:
                    while remaining:
                        chunk = stream.read(min(1 << 20, remaining))
                        if not chunk:
                            break
                        out.write(chunk)
                        remaining -= len(chunk)
                if remaining:
                    raise RunError(f"{label}: upload was cut short")
                report_date = None
                if slot == "config":
                    try:
                        report_date = read_report_date(Path(staging))
                    except Exception as exc:
                        raise RunError(f"{label}: could not read the report date ({exc})") from exc
                for old in folder.glob(f"{stem}.*"):
                    old.unlink()
                tmp.replace(folder / f"{stem}{ext}")
            meta["files"][slot] = {"name": filename[:255], "size": length}
            if report_date:
                meta["report_date"] = report_date
            self._write(meta)
            return meta

    def start(self, run_id: str) -> dict:
        with self.lock:
            meta = self.get(run_id)
            if meta["status"] != "draft":
                raise RunError("This run has already started")
            missing = [SLOTS[s][0] for s in SLOTS if s not in meta["files"]]
            if missing:
                raise RunError("Missing files: " + ", ".join(missing))
            meta = self.update(run_id, status="queued", step="Waiting for a free worker")
            self.jobs.put(run_id)
            return meta

    def archive(self, run_id: str, archived: bool) -> dict:
        meta = self.get(run_id)
        if meta["status"] in {"queued", "running"}:
            raise RunError("A running run cannot be archived")
        return self.update(run_id, archived=_now() if archived else None)

    def discard(self, run_id: str) -> None:
        """Delete a draft whose upload failed; started runs are history and stay."""
        with self.lock:
            if self.get(run_id)["status"] != "draft":
                raise RunError("Only an unstarted run can be discarded")
            try:
                shutil.rmtree(self.path(run_id))
            except OSError as exc:
                raise RunError(f"Could not discard {run_id}: {exc.strerror}") from exc

    def log(self, run_id: str) -> str:
        try:
            return (self.path(run_id) / "log.txt").read_text(errors="replace")
        except FileNotFoundError:
            return ""

    def latest_ok(self) -> str | None:
        for meta in self.list():
            if meta["status"] == "ok" and not meta["archived"]:
                return meta["id"]
        return None

    # ---- worker --------------------------------------------------------
    def _worker(self) -> None:
        while True:
            run_id = self.jobs.get()
            if run_id is None:
                return
            began = time.monotonic()
            try:
                self.update(run_id, status="running", started=_now())
                rows = self._execute(run_id)
                self.update(run_id, status="ok", step=None, rows=rows, seconds=round(time.monotonic() - began))
                if self.get(run_id)["archive_previous"]:
                    for other in self.list():
                        if other["id"] != run_id and other["status"] == "ok" and not other["archived"]:
                            self.update(other["id"], archived=_now())
            except Exception as exc:  # noqa: BLE001 - persist any pipeline failure and keep the worker alive
                msg = str(exc) or exc.__class__.__name__
                with (self.path(run_id) / "log.txt").open("a") as log:
                    traceback.print_exc(file=log)
                self.update(run_id, status="bad", step=None, error=msg, seconds=round(time.monotonic() - began))

    def _step(self, run_id: str, n: int, total: int, text: str) -> None:
        self.update(run_id, step=f"Step {n} of {total}: {text}")
        with (self.path(run_id) / "log.txt").open("a") as log:
            log.write(f"\n=== Step {n} of {total}: {text} ===\n")

    def _sh(self, run_id: str, *cmd: str) -> None:
        run = self.path(run_id)
        with (run / "log.txt").open("a") as log:
            log.write("$ " + " ".join(cmd) + "\n")
            log.flush()
            proc = subprocess.run(
                [sys.executable, *cmd], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=3600, check=False
            )
        if proc.returncode:
            lines = [ln for ln in self.log(run_id).strip().splitlines() if ln.strip()]
            exceptions = [ln for ln in lines if re.match(r"^[\w.]+(?:Error|Exception):", ln)]
            detail = exceptions[-1] if exceptions else lines[-1] if lines else "see log"
            raise RunError(f"{cmd[0] if cmd[0] != '-m' else cmd[1]} failed: {detail}")

    def _execute(self, run_id: str) -> int:
        run = self.path(run_id)
        data, out, hist = run / "input", run / "output", run / "history"
        out.mkdir(exist_ok=True)
        total = 5
        self._step(run_id, 1, total, "checking the inputs")
        self._sh(run_id, "-m", "src", "starting_date", "--data-dir", str(data), "--output", str(out / "starting_date.csv"))
        report_date = read_report_date(data)
        self.update(run_id, report_date=report_date)

        self._step(run_id, 2, total, "building the 30-column report")
        self._sh(run_id, "-m", "src", "report", "--data-dir", str(data), "--output", str(out / "inferred_report.parquet"))

        self._step(run_id, 3, total, "saving this report as a snapshot")
        rows = snapshot(
            out / "inferred_report.parquet", report_date, hist, out / "inferred_report.xlsx", self.history_paths()
        )

        self._step(run_id, 4, total, "building the stock-movement history")
        for query, name in (("movement_series", "movement_series"), ("movement_parents", "movement_parents")):
            self._sh(run_id, "-m", "src", query, "--history-dir", str(hist), "--output", str(out / f"{name}.parquet"))

        self._step(run_id, 5, total, "building the dashboard")
        self._sh(
            run_id, "ui/build.py", "--data-dir", str(data), "--history-dir", str(hist),
            "--output-dir", str(out), "--html", str(run / "index.html"),
        )
        return rows

    def history_paths(self) -> list[Path]:
        """Bundled history plus successful run snapshots, newest run wins on a repeated date."""
        paths = sorted(self.history_dir.glob("*.xlsx"))
        paths.extend(
            self.path(m["id"]) / "history" / "snapshot.xlsx"
            for m in reversed(self.list()) if m["status"] == "ok"
        )
        return paths


def read_report_date(data_dir: Path) -> str:
    """ISO date from config.xlsx, via the same query the pipeline uses."""
    from src import starting_date

    frame = starting_date.build(data_dir)
    value = frame.item(0, "starting_date")
    if value is None:
        raise ValueError("config_table has no starting_date")
    return value.isoformat()[:10]


def snapshot(
    report_parquet: Path, report_date: str, hist_dir: Path, xlsx_out: Path, history_paths: list[Path]
) -> int:
    """Create a private as-of history and preserve matching manual labels.

    One saved snapshot per report date; later successful runs replace an earlier
    version in this view. Future snapshots never leak into an older-period run.
    Manual labels are copied only for exact batch identities from the same date.
    """
    import fastexcel
    import polars as pl

    frame = pl.read_parquet(report_parquet)
    if frame.is_empty():
        raise RunError("The uploaded files produced no stock rows")
    xlsx_out.parent.mkdir(parents=True, exist_ok=True)
    frame.write_excel(xlsx_out, worksheet="Sheet1", float_precision=15)
    epoch = pl.lit(date(1899, 12, 30))
    saved = frame.with_columns(
        (epoch + pl.duration(days=pl.col(c))).cast(pl.Date).alias(c) for c in ("report_date", "Expiry Date")
    )
    by_date: dict[str, Path] = {}
    for path in history_paths:
        sheet = fastexcel.read_excel(path).load_sheet_by_idx(0, use_columns=["report_date"], dtypes="string")
        dates = sheet.to_polars()["report_date"].drop_nulls().str.slice(0, 10).unique().to_list()
        if len(dates) != 1:
            raise RunError(f"Saved history {path.name} must contain exactly one report date")
        if dates[0] <= report_date:
            by_date[dates[0]] = path
    manual = {"mitigation_status": "mitigation_status_category", "action_plan": "action_plan_remarks"}
    if report_date in by_date:
        same = pl.read_excel(by_date.pop(report_date), sheet_id=1, infer_schema_length=0)
        labels = same.select(
            "batch_index", *[
                pl.col(c if c in same.columns else legacy).cast(pl.String).alias(c)
                for c, legacy in manual.items()
            ]
        )
        saved = saved.join(labels, on="batch_index", how="left", validate="1:1", maintain_order="left")
    else:
        saved = saved.with_columns(pl.lit(None, dtype=pl.String).alias(c) for c in manual)
    hist_dir.mkdir(parents=True, exist_ok=True)
    for old in hist_dir.iterdir():
        old.unlink()
    for i, (_, path) in enumerate(sorted(by_date.items())):
        (hist_dir / f"saved-{i:04d}.xlsx").symlink_to(path.resolve())
    saved.write_excel(hist_dir / "snapshot.xlsx", worksheet="Sheet1", float_precision=15)
    return frame.height

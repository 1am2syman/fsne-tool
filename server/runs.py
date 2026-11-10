"""Run storage and the pipeline worker.

Each run lives in ``runs/RUN-0001/``::

    input/     the uploaded files under the names the queries look for
    history/   symlinks to the shared history/ plus this run's own snapshot
    output/    pipeline outputs
    data.json  dashboard data rendered with the shared current template
    meta.json  status, timings, detected report date
    log.txt    stdout/stderr of every pipeline step
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.table import Table

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = Path(os.environ.get("FSNE_RUNS_DIR", ROOT / "runs"))
SHARED_HISTORY = Path(os.environ.get("FSNE_HISTORY_DIR", ROOT / "history"))
RUN_ID = re.compile(r"^RUN-\d{4,}$")

# slot -> (label, canonical file stem, allowed extensions). The stems are what
# the translated queries match on (see src/io.matching_files callers).
SLOTS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "config": ("Run configuration", "config", (".xlsx", ".xlsm")),
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
        self.process: subprocess.Popen | None = None
        self.process_run: str | None = None
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
    def create(self, label: str, *, archive_previous: bool = False, started_by: str = "local") -> dict:
        with self.lock:
            sequence = self.dir / ".last-run-id"
            last = int(sequence.read_text()) if sequence.exists() else 0
            n = 1 + max(last, max((int(m["id"][4:]) for m in self.list()), default=0))
            sequence.write_text(str(n))
            run_id = f"RUN-{n:04d}"
            (self.dir / run_id / "input").mkdir(parents=True)
            meta = {
                "id": run_id,
                "label": label.strip()[:80] or "Untitled run",
                "status": "draft",
                "step": None,
                "created": _now(),
                "started_by": started_by[:255] or "local",
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
                report_date = validate_upload(slot, Path(staging))
                if slot in {"config", "secondary"}:
                    config_date = report_date if slot == "config" else meta.get("report_date")
                    secondary_dir = Path(staging) if slot == "secondary" else folder
                    if config_date and (slot == "secondary" or "secondary" in meta["files"]):
                        validate_sales_month(secondary_dir, config_date)
                for old in folder.glob(f"{stem}.*"):
                    old.unlink()
                tmp.replace(folder / f"{stem}{ext}")
            meta["files"][slot] = {"name": filename[:255], "size": length}
            if report_date:
                meta["report_date"] = report_date
                meta["configuration_source"] = "workbook"
            self._write(meta)
            return meta

    def configure(self, run_id: str, report_date: str, label: str | None = None) -> dict:
        """Save form settings as the config workbook consumed by the pipeline.

        Validate against uploaded sales before replacing any accepted settings.
        This shares the upload/start lock so a worker cannot read a partial file.
        """
        with self.lock:
            meta = self.get(run_id)
            if meta["status"] != "draft":
                raise RunError("Settings can only be changed before the run starts")
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date):
                    raise ValueError("Expected an ISO date")
                date.fromisoformat(report_date)
            except (ValueError, TypeError):
                raise RunError("Report date: choose a valid calendar date.") from None
            folder = self.path(run_id) / "input"
            if "secondary" in meta["files"]:
                validate_sales_month(folder, report_date)
            with tempfile.TemporaryDirectory(dir=self.path(run_id)) as staging:
                target = Path(staging) / "config.xlsx"
                workbook = Workbook()
                sheet = workbook.active
                assert sheet is not None
                sheet.append(["variable", "value"])
                sheet.append(["starting_date", report_date])
                sheet.add_table(Table(displayName="config_table", ref="A1:B2"))
                workbook.save(target)
                workbook.close()
                size = target.stat().st_size
                target.replace(folder / "config.xlsx")
            # Remove a previous macro workbook only after its replacement exists.
            for old in folder.glob("config.*"):
                if old.name != "config.xlsx":
                    old.unlink()
            meta["files"]["config"] = {"name": "Entered in form", "size": size, "generated": True}
            meta["report_date"] = report_date
            meta["configuration_source"] = "form"
            if label is not None:
                meta["label"] = label.strip()[:80] or "Untitled run"
            self._write(meta)
            return meta

    def start(self, run_id: str, label: str | None = None, started_by: str | None = None) -> dict:
        with self.lock:
            meta = self.get(run_id)
            if meta["status"] != "draft":
                raise RunError("This run has already started")
            missing = [SLOTS[s][0] for s in SLOTS if s != "config" and s not in meta["files"]]
            if missing:
                raise RunError("Missing files: " + ", ".join(missing))
            if not meta.get("report_date") or "config" not in meta["files"]:
                raise RunError("Choose a report date or import a configuration workbook.")
            if label is not None:
                meta["label"] = label.strip()[:80] or "Untitled run"
            if started_by is not None:
                meta["started_by"] = started_by[:255] or "local"
            meta.update(status="queued", step="Waiting for a free worker", run_date=date.today().isoformat())
            self._write(meta)
            self.jobs.put(run_id)
            return meta

    def archive(self, run_id: str, archived: bool) -> dict:
        meta = self.get(run_id)
        if meta["status"] in {"queued", "running"}:
            raise RunError("A running run cannot be archived")
        return self.update(run_id, archived=_now() if archived else None)

    def cancel(self, run_id: str) -> dict:
        with self.lock:
            meta = self.get(run_id)
            if meta["status"] not in {"queued", "running"}:
                raise RunError("Only a queued or running run can be cancelled")
            if meta["status"] == "queued":
                return self.update(run_id, status="cancelled", step=None, error=None)
            meta = self.update(run_id, cancel_requested=True, step="Cancelling")
            if self.process_run == run_id and self.process and self.process.poll() is None:
                with suppress(ProcessLookupError):
                    self.process.terminate()  # It may have exited since poll().
            return meta

    def delete(self, run_id: str) -> None:
        with self.lock:
            if self.get(run_id)["status"] not in {"ok", "bad", "cancelled"}:
                raise RunError("Only completed, failed or cancelled runs can be deleted")
            meta = self.get(run_id)
            try:
                # Later runs must keep their own history after this source is deleted.
                self._detach_history(run_id)
                remove_run_tree(self.path(run_id))
            except OSError as exc:
                # rmtree may have removed meta.json before encountering a locked
                # file. Keep the run visible so the user can close it and retry.
                self._write(meta)
                raise RunError(
                    f"Could not delete {run_id}: {exc.strerror}. Close any files from this run in Excel and try again."
                ) from exc

    def _detach_history(self, run_id: str) -> None:
        source = self.path(run_id).resolve()
        for other in self.list():
            if other["id"] == run_id:
                continue
            for link in (self.path(other["id"]) / "history").glob("*.xlsx"):
                if link.is_symlink() and link.resolve().is_relative_to(source):
                    with tempfile.TemporaryDirectory(dir=link.parent) as staging:
                        copy = Path(staging) / link.name
                        shutil.copyfile(link, copy)
                        copy.replace(link)

    def action_plan_path(self, run_id: str) -> Path:
        """Build older runs' export from their frozen private history, once."""
        with self.lock:
            meta = self.get(run_id)
            if meta["status"] != "ok":
                raise RunError("That run has no completed report")
            if not meta.get("run_date"):
                started = datetime.fromisoformat(meta.get("started") or meta["created"])
                self.update(run_id, run_date=started.astimezone().date().isoformat())
            run = self.path(run_id)
            target = run / "output" / "action_plan.xlsx"
            if not target.exists():
                import polars as pl

                parquet = run / "output" / "inferred_report.parquet"
                if not parquet.is_file():
                    raise RunError("This run's source report is missing; calculate it again to export an action plan.")
                paths = sorted((run / "history").glob("saved-*.xlsx"))
                write_action_plan(pl.read_parquet(parquet), meta["report_date"], target, history_by_date(paths))
            return target

    def discard(self, run_id: str) -> None:
        """Delete a draft whose upload failed; started runs are history and stay."""
        with self.lock:
            meta = self.get(run_id)
            if meta["status"] != "draft":
                raise RunError("Only an unstarted run can be discarded")
            try:
                remove_run_tree(self.path(run_id))
            except OSError as exc:
                self._write(meta)
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
                with self.lock:
                    try:
                        meta = self.get(run_id)
                    except RunError:
                        continue
                    if meta["status"] != "queued":
                        continue
                    self.update(run_id, status="running", started=_now())
                rows = self._execute(run_id)
                with self.lock:
                    self._check_cancel(run_id)
                    self.update(run_id, status="ok", step=None, rows=rows, seconds=round(time.monotonic() - began))
                    report_date = self.get(run_id)["report_date"]
                    for other in self.list():
                        if (other["id"] != run_id and other["status"] == "ok" and not other["archived"]
                                and other.get("report_date") == report_date):
                            self.update(other["id"], archived=_now())
            except Exception as exc:  # noqa: BLE001 - persist any pipeline failure and keep the worker alive
                with self.lock:
                    meta = self.get(run_id)
                    if meta.get("cancel_requested"):
                        self.update(run_id, status="cancelled", step=None, error=None, seconds=round(time.monotonic() - began))
                        continue
                    msg = (str(exc) or exc.__class__.__name__) if isinstance(exc, RunError) else pipeline_message(str(exc), meta.get("step"))
                    with (self.path(run_id) / "log.txt").open("a") as log:
                        traceback.print_exc(file=log)
                    self.update(run_id, status="bad", step=None, error=msg, seconds=round(time.monotonic() - began))

    def _check_cancel(self, run_id: str) -> None:
        if self.get(run_id).get("cancel_requested"):
            raise RunError("Run cancelled")

    def _step(self, run_id: str, n: int, total: int, text: str) -> None:
        self._check_cancel(run_id)
        self.update(run_id, step=f"Step {n} of {total}: {text}")
        with (self.path(run_id) / "log.txt").open("a") as log:
            log.write(f"\n=== Step {n} of {total}: {text} ===\n")

    def _sh(self, run_id: str, *cmd: str) -> None:
        run = self.path(run_id)
        with (run / "log.txt").open("a") as log:
            log.write("$ " + " ".join(cmd) + "\n")
            log.flush()
            with self.lock:
                self._check_cancel(run_id)
                env = {**os.environ, "PYTHONWARNINGS": "ignore::FutureWarning"}
                self.process = subprocess.Popen([sys.executable, *cmd], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)
                self.process_run = run_id
                proc = self.process
            try:
                proc.wait(timeout=3600)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise
            finally:
                with self.lock:
                    self.process = None
                    self.process_run = None
            self._check_cancel(run_id)
        if proc.returncode:
            lines = [ln for ln in self.log(run_id).strip().splitlines() if ln.strip()]
            exceptions = [ln for ln in lines if re.match(r"^[\w.]+(?:Error|Exception):", ln)]
            detail = exceptions[-1] if exceptions else lines[-1] if lines else "see log"
            raise RunError(pipeline_message(detail, self.get(run_id).get("step")))

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
        label_counts: dict[str, int] = {}
        # Do not let a concurrent deletion remove a source between selecting
        # the history files and installing this run's private history links.
        with self.lock:
            rows = snapshot(
                out / "inferred_report.parquet", report_date, hist, out / "action_plan.xlsx", self.history_paths(),
                label_counts=label_counts,
            )
        self.update(run_id, summary={**self.get(run_id).get("summary", {}), **label_counts})

        self._step(run_id, 4, total, "building the stock-movement history")
        for query, name in (("movement_series", "movement_series"), ("movement_parents", "movement_parents")):
            self._sh(run_id, "-m", "src", query, "--history-dir", str(hist), "--output", str(out / f"{name}.parquet"))

        self._step(run_id, 5, total, "building the dashboard")
        self._sh(
            run_id, "ui/build.py", "--data-dir", str(data), "--history-dir", str(hist),
            "--output-dir", str(out), "--payload", str(run / "data.json"),
        )
        self.update(run_id, summary={**self.get(run_id).get("summary", {}), **run_summary(out, hist, report_date)})
        return rows

    def history_paths(self) -> list[Path]:
        """Bundled history plus successful run snapshots, newest run wins on a repeated date."""
        paths = sorted(self.history_dir.glob("*.xlsx"))
        paths.extend(
            self.path(m["id"]) / "history" / "snapshot.xlsx"
            for m in reversed(self.list()) if m["status"] == "ok"
        )
        return paths


def run_summary(out: Path, hist: Path, report_date: str) -> dict:
    import polars as pl

    current = pl.read_parquet(out / "inferred_report.parquet")
    totals = current.select(pl.col("total_closing_stock_bdt").sum(), pl.col("exposure_bdt").sum()).row(0, named=True)
    previous = None
    for path in hist.glob("saved-*.xlsx"):
        frame = pl.read_excel(path, sheet_id=1, infer_schema_length=0)
        dates = frame["report_date"].drop_nulls().cast(pl.String).str.slice(0, 10)
        if len(dates) and (previous is None or dates[0] > previous[0]):
            previous = (dates[0], frame)
    previous_totals = None
    if previous:
        previous_totals = previous[1].select(
            pl.col("total_closing_stock_bdt").cast(pl.Float64, strict=False).sum(),
            pl.col("exposure_bdt").cast(pl.Float64, strict=False).sum(),
        ).row(0, named=True)
    try:
        impact = json.loads((out / "run_rate_impact.json").read_text())
    except (OSError, ValueError, KeyError) as exc:
        raise RunError("The run summary could not be read. Check the run log for details.") from exc
    return {
        "report_date": report_date, "batches": current.height, **totals,
        "previous_report_date": previous[0] if previous else None,
        "stock_change_bdt": totals["total_closing_stock_bdt"] - previous_totals["total_closing_stock_bdt"] if previous_totals else None,
        "exposure_change_bdt": totals["exposure_bdt"] - previous_totals["exposure_bdt"] if previous_totals else None,
        "materials_missing_hierarchy": current.filter(pl.col("parent_material_code").is_null())["Material Code"].drop_nulls().n_unique(),
        "batches_missing_value": current.filter(pl.col("total_closing_stock_bdt").is_null()).height,
        "winsorized_months": impact["winsorized_months"],
    }


def link_history(link: Path, target: Path) -> None:
    """Expose a shared history file inside a run without duplicating it where possible.

    Symlinks need a privilege Windows users usually lack (WinError 1314), so
    Windows uses writable private copies. Elsewhere fall back to a hard link,
    then a copy when symlinks are unavailable.
    """
    # Windows symlinks usually need elevation. A private copy also avoids
    # inheriting a shared file's read-only attribute (copy2 preserves it).
    if os.name == "nt":
        shutil.copyfile(target, link)
        return
    try:
        link.symlink_to(target)
    except OSError:
        try:
            os.link(target, link)
        except OSError:
            shutil.copy2(target, link)


def pipeline_message(detail: str, step: str | None = None) -> str:
    """Translate recognized pipeline failures; the full traceback stays in the log."""
    known = (
        ("Latest run-rate source month", "Secondary sales: choose a file whose latest month matches the configuration report month."),
        ("Current source report date", "Run configuration: check the report date and choose files for that month."),
        ("Current hierarchy differs", "Product hierarchy: choose the hierarchy that matches the shelf-life report."),
        ("Current inputs do not reproduce", "Shelf-life report: choose the stock report for the configured month."),
        ("Current inputs differ from saved stock", "Shelf-life report or stock on hand: check the files match the configured month."),
        ("Secondary sales need", "Secondary sales: check that the file contains valid sales months."),
        ("Need at least two reports with an Actionable status", "Saved history: at least two months need mitigation statuses marked Actionable."),
    )
    for marker, message in known:
        if marker in detail:
            return message
    where = f" during {step.lower()}" if step else ""
    return f"The calculation could not be completed{where}. Check the uploaded files and see the run log for technical details."


def validate_sales_month(data_dir: Path, report_date: str) -> None:
    from src.io import date as parse_date
    from src.secondary import load

    try:
        months = load(data_dir).select(parse_date("Month-Year").dt.truncate("1mo"))
        latest = months["Month-Year"].max()
        if latest is None or str(latest)[:7] != report_date[:7]:
            found = str(latest)[:7] if latest is not None else "no valid month"
            raise RunError(
                f"Secondary sales: latest month is {found}, but the configuration report month is "
                f"{report_date[:7]}. Choose files for the same month."
            )
    except RunError:
        raise
    except Exception as exc:
        raise RunError("Secondary sales: check that Month-Year contains valid dates.") from exc


def validate_upload(slot: str, data_dir: Path) -> str | None:
    from src import ph, run_rate_parent, sec_history, secondary, soh_db
    from src.io import promote_headers, raw_csvs

    label = SLOTS[slot][0]
    try:
        if slot == "config":
            return read_report_date(data_dir)
        if slot == "hierarchy":
            frame = ph.build(data_dir)
        elif slot == "secondary":
            frame = secondary.load(data_dir)
            sec_history.transform(frame)
            run_rate_parent.monthly_sales(frame)
        else:
            frame = promote_headers(raw_csvs(list(data_dir.glob("*.csv")), 34))
            missing = [column for column in soh_db.COLUMNS if column not in frame.columns]
            if missing:
                raise RunError(f"{label}: missing columns: {', '.join(missing)}. Choose a complete shelf-life report.")
        if frame.is_empty():
            raise RunError(f"{label}: the file contains no data rows. Choose a complete file.")
    except RunError:
        raise
    except Exception as exc:
        if slot == "config":
            raise RunError(f"{label}: check that config_table contains a valid starting_date.") from exc
        raise RunError(f"{label}: could not read the expected data or columns. Check the file and choose it again.") from exc
    return None


def read_report_date(data_dir: Path) -> str:
    """ISO date from config.xlsx, via the same query the pipeline uses."""
    from src import starting_date

    frame = starting_date.build(data_dir)
    value = frame.item(0, "starting_date")
    if value is None:
        raise ValueError("config_table has no starting_date")
    return value.isoformat()[:10]


def remove_run_tree(folder: Path) -> None:
    """Retry read-only private files; never chmod a symlink/shared hard link."""
    def retry_readonly(operation, path, exc_info):
        error = exc_info[1]
        entry = Path(path)
        if not isinstance(error, PermissionError) or entry.is_symlink():
            raise error
        info = entry.stat()
        if info.st_nlink > 1 or info.st_mode & stat.S_IWUSR:
            raise error
        entry.chmod(info.st_mode | stat.S_IWUSR)
        operation(path)

    shutil.rmtree(folder, onerror=retry_readonly)


def history_by_date(paths: list[Path]) -> dict[str, Path]:
    """One source per report date; later supplied versions win."""
    import polars as pl

    result: dict[str, Path] = {}
    for path in paths:
        # Reading bytes releases the OS handle before retaining parsed sheets.
        dates = pl.read_excel(path.read_bytes(), columns=["report_date"], infer_schema_length=0)["report_date"]
        unique = dates.drop_nulls().cast(pl.String).str.slice(0, 10).unique().to_list()
        if len(unique) != 1:
            raise RunError(f"Saved history {path.name} must contain exactly one report date")
        date.fromisoformat(unique[0])
        result[unique[0]] = path
    return result


def write_action_plan(frame, report_date: str, target: Path, by_date: dict[str, Path]) -> None:
    """Exact left join to the previous calendar month; current entry cells blank.

    Movement's shortened-key label inheritance stays in snapshot(), separately.
    Legacy history spellings are accepted at this input boundary.
    """
    import polars as pl

    month = (date.fromisoformat(report_date).replace(day=1) - timedelta(days=1)).isoformat()[:7]
    candidates = [dt for dt in by_date if dt[:7] == month]
    columns = ("action_plan", "mitigation_status")
    planning = [f"lm_{c}" for c in columns] + list(columns)
    frame = frame.drop([c for c in planning if c in frame.columns])
    if candidates:
        path = by_date[max(candidates)]
        previous = pl.read_excel(path.read_bytes(), infer_schema_length=0)
        if "batch_index" not in previous.columns:
            raise RunError(f"Saved history {path.name} has no batch_index column")
        keys = previous["batch_index"].cast(pl.String)
        if keys.null_count() or (keys == "").any() or keys.n_unique() != previous.height:
            raise RunError(f"Saved history {path.name}: batch_index must be nonempty and unique")
        aliases = {"action_plan": "action_plan_remarks", "mitigation_status": "mitigation_status_category"}
        labels = previous.select(
            pl.col("batch_index").cast(pl.String),
            *[(pl.col(c if c in previous.columns else aliases[c]).cast(pl.String)
               if c in previous.columns or aliases[c] in previous.columns else pl.lit(None, dtype=pl.String)
               ).alias(f"lm_{c}") for c in columns],
        )
        frame = frame.join(labels, on="batch_index", how="left", validate="m:1", maintain_order="left")
    else:
        frame = frame.with_columns(pl.lit(None, dtype=pl.String).alias(f"lm_{c}") for c in columns)
    frame = frame.with_columns(pl.lit(None, dtype=pl.String).alias(c) for c in columns)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as staging:
        temporary = Path(staging) / target.name
        frame.write_excel(temporary, worksheet="Sheet1", float_precision=15)
        temporary.replace(target)


def snapshot(
    report_parquet: Path, report_date: str, hist_dir: Path, xlsx_out: Path, history_paths: list[Path],
    *, label_counts: dict[str, int] | None = None,
) -> int:
    """Create a private as-of history and preserve matching manual labels.

    One saved snapshot per report date; later successful runs replace an earlier
    version in this view. Future snapshots never leak into an older-period run.
    Same-date labels match exact batches; new dates inherit the latest earlier
    labels by movement batch key, without assigning a status to new batches.
    """
    import polars as pl

    frame = pl.read_parquet(report_parquet)
    if frame.is_empty():
        raise RunError("The uploaded files produced no stock rows")
    by_date = {dt: path for dt, path in history_by_date(history_paths).items() if dt <= report_date}
    write_action_plan(frame, report_date, xlsx_out, by_date)
    epoch = pl.lit(date(1899, 12, 30))
    saved = frame.with_columns(
        (epoch + pl.duration(days=pl.col(c))).cast(pl.Date).alias(c) for c in ("report_date", "Expiry Date")
    )
    manual = {"mitigation_status": "mitigation_status_category", "action_plan": "action_plan_remarks"}
    same_date = report_date in by_date
    if same_date:
        same = pl.read_excel(by_date.pop(report_date), sheet_id=1, infer_schema_length=0)
        labels = same.select(
            "batch_index", *[
                pl.col(c if c in same.columns else legacy).cast(pl.String).alias(c)
                for c, legacy in manual.items()
            ]
        )
        saved = saved.join(labels, on="batch_index", how="left", validate="1:1", maintain_order="left")
    elif by_date:
        from src.movement import KEY, batch_key

        previous = pl.read_excel(by_date[max(by_date)], sheet_id=1, infer_schema_length=0)
        labels = previous.select(
            batch_key(), *[
                pl.col(c if c in previous.columns else legacy).cast(pl.String).alias(c)
                for c, legacy in manual.items()
            ]
        ).group_by(KEY, maintain_order=True).agg(pl.col(c).drop_nulls().first() for c in manual)
        saved = saved.with_columns(batch_key()).join(
            labels, on=KEY, how="left", validate="m:1", maintain_order="left"
        ).drop(KEY)
    else:
        saved = saved.with_columns(pl.lit(None, dtype=pl.String).alias(c) for c in manual)
    if label_counts is not None:
        labelled = saved["mitigation_status"].is_not_null().sum()
        label_counts.update(
            mitigation_carried_batches=int(labelled) if not same_date else 0,
            mitigation_unlabelled_batches=saved.height - int(labelled),
        )
    hist_dir.mkdir(parents=True, exist_ok=True)
    for old in hist_dir.iterdir():
        old.unlink()
    for i, (_, path) in enumerate(sorted(by_date.items())):
        link_history(hist_dir / f"saved-{i:04d}.xlsx", path.resolve())
    saved.write_excel(hist_dir / "snapshot.xlsx", worksheet="Sheet1", float_precision=15)
    return frame.height

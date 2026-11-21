"""Run storage and the pipeline worker.

Each run lives in ``runs/RUN-0001/``::

    input/     the uploaded files under the names the queries look for
    history/   private copies of settled history plus this run's own snapshot
    output/    pipeline outputs
    data.json  dashboard data rendered with the shared current template
    meta.json  status, timings, detected report date
    log.txt    stdout/stderr of every pipeline step
"""

from __future__ import annotations

import calendar
import ctypes
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
import uuid
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Protection
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table

from . import plan_notes
from .action_plans import STATUSES, ActionPlanCycle, decorate, previous_month
from .errors import RunError

ROOT = Path(__file__).resolve().parent.parent
BUNDLED_HISTORY = ROOT / "history"


def default_data_dir() -> Path:
    """Keep user runs and settled history outside the repository checkout."""
    if sys.platform == "win32":
        # FOLDERID_Documents resolves redirected/OneDrive Documents and Unicode paths.
        folder_id = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("fdd39ad0-238f-46af-adb4-6c85480369c7").bytes_le)
        result = ctypes.c_wchar_p()
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        shell.SHGetKnownFolderPath.restype = ctypes.c_long
        ole = ctypes.WinDLL("ole32", use_last_error=True)
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole.CoTaskMemFree.restype = None
        try:
            status = shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(result))
            if status != 0 or not result.value:
                raise OSError("Windows could not locate Documents; set FSNE_RUNS_DIR and FSNE_HISTORY_DIR")
            return Path(result.value) / "FSNE"
        finally:
            if result:
                ole.CoTaskMemFree(ctypes.cast(result, ctypes.c_void_p))
    return Path.home() / "Documents" / "FSNE"


def default_runs_dir() -> Path:
    override = os.environ.get("FSNE_RUNS_DIR")
    if override:
        return Path(override)
    from .storage import load_settings

    saved = load_settings().get("runs_dir")
    return Path(saved) if saved else default_data_dir() / "runs"


def default_history_dir() -> Path:
    override = os.environ.get("FSNE_HISTORY_DIR")
    if override:
        return Path(override)
    from .storage import load_settings

    saved = load_settings().get("history_dir")
    return Path(saved) if saved else default_data_dir() / "history"


def _seed_history(target: Path, bundled: Path = BUNDLED_HISTORY) -> None:
    """Seed a brand-new external history directory once from the bundled baseline snapshots."""
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".history-seed-", dir=target.parent))
    try:
        if bundled.is_dir():
            for source in sorted(bundled.glob("*.xlsx")):
                shutil.copyfile(source, staging / source.name)
        try:
            staging.rename(target)
        except OSError:
            if not target.exists():
                raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _seed_empty_history(target: Path, bundled: Path = BUNDLED_HISTORY) -> None:
    """Copy bundled baseline snapshots into an empty history directory."""
    target.mkdir(parents=True, exist_ok=True)
    if any(p.is_file() and not p.name.startswith("~$") for p in target.glob("*.xlsx")):
        return
    if not bundled.is_dir():
        return
    for source in sorted(bundled.glob("*.xlsx")):
        dest = target / source.name
        if not dest.exists():
            shutil.copyfile(source, dest)


RUNS_DIR = default_runs_dir()
SHARED_HISTORY = default_history_dir()
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


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(
        self,
        runs_dir: Path | None = None,
        history_dir: Path | None = None,
        *,
        start_worker: bool = True,
    ) -> None:
        from .storage import load_settings

        saved = load_settings() if (runs_dir is None and history_dir is None) else {}
        seed_default_history = (
            runs_dir is None
            and history_dir is None
            and not os.environ.get("FSNE_HISTORY_DIR")
            and "history_dir" not in saved
        )
        resolved_runs = default_runs_dir() if runs_dir is None else runs_dir
        resolved_history = default_history_dir() if history_dir is None else history_dir
        self.dir = resolved_runs.resolve()
        self.history_dir = resolved_history.resolve()
        self.dir.mkdir(parents=True, exist_ok=True)
        if seed_default_history and self.history_dir != BUNDLED_HISTORY.resolve():
            _seed_history(self.history_dir, BUNDLED_HISTORY)
        self.lock = threading.RLock()
        self.cycle = ActionPlanCycle(self)
        self.jobs: queue.Queue[str | None] = queue.Queue()
        self.process: subprocess.Popen | None = None
        self.process_run: str | None = None
        self._recover_interrupted()
        self.worker = threading.Thread(target=self._worker, daemon=True, name="fsne-runs")
        if start_worker:
            self.worker.start()

    def _recover_interrupted(self) -> None:
        for meta in self.list():
            if meta["status"] in {"queued", "running"}:
                meta.update(status="bad", error="Interrupted: the server restarted during this run")
                self._write(meta)
            elif meta["status"] == "ok":
                self._backfill_label_counts(meta)

    def is_busy(self) -> bool:
        with self.lock:
            if self.process is not None or self.process_run is not None:
                return True
            return any(m["status"] in {"queued", "running"} for m in self.list())

    def reconfigure(self, runs_dir: Path, history_dir: Path, *, seed_history: bool = False) -> None:
        """Switch active runs and history folders live while idle."""
        with self.lock:
            if self.is_busy():
                raise RunError("Wait for the active run to finish or cancel it before changing storage folders.")
            new_runs = runs_dir.expanduser().resolve()
            new_history = history_dir.expanduser().resolve()
            if new_runs == new_history or new_runs.is_relative_to(new_history) or new_history.is_relative_to(new_runs):
                raise RunError("Working runs and settled history must be separate, non-nested folders.")
            try:
                new_runs.mkdir(parents=True, exist_ok=True)
                new_history.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RunError(f"Could not create folder: {exc.strerror or exc}") from exc
            if seed_history and new_history != BUNDLED_HISTORY.resolve():
                _seed_empty_history(new_history, BUNDLED_HISTORY)
            self.dir = new_runs
            self.history_dir = new_history
            self._recover_interrupted()

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
            record = {key: value for key, value in meta.items() if key not in {"view_label", "view_flag"}}
            tmp.write_text(json.dumps(record, indent=1))
            tmp.replace(target)

    def _backfill_label_counts(self, meta: dict) -> None:
        summary = meta.get("summary") or {}
        if {"mitigation_carried_batches", "mitigation_unlabelled_batches"} <= summary.keys():
            return
        snapshot = self.path(meta["id"]) / "history/snapshot.xlsx"
        if not snapshot.is_file():
            return
        import polars as pl

        statuses = pl.read_excel(snapshot, columns=["batch_index", "mitigation_status"], infer_schema_length=0)["mitigation_status"]
        labelled = int(statuses.is_not_null().sum())
        self.update(meta["id"], summary={**summary, "mitigation_carried_batches": labelled,
                                        "mitigation_unlabelled_batches": len(statuses) - labelled})

    def get(self, run_id: str) -> dict:
        try:
            return decorate(json.loads((self.path(run_id) / "meta.json").read_text()))
        except FileNotFoundError:
            raise RunError("Unknown run") from None

    def update(self, run_id: str, **fields: object) -> dict:
        with self.lock:
            meta = self.get(run_id)
            meta.update(fields)
            self._write(meta)
            return decorate(meta)

    def list(self) -> list[dict]:
        metas = []
        for p in sorted(self.dir.glob("RUN-*/meta.json"), reverse=True):
            try:
                metas.append(decorate(json.loads(p.read_text())))
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
                "plan_state": "provisional",
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
                automatic = slot == "secondary" and report_date and (
                    not meta.get("report_date") or meta.get("configuration_source") == "secondary"
                )
                if slot in {"config", "secondary"}:
                    config_date = report_date if slot == "config" or automatic else meta.get("report_date")
                    secondary_dir = Path(staging) if slot == "secondary" else folder
                    if config_date and (slot == "secondary" or "secondary" in meta["files"]):
                        validate_sales_month(secondary_dir, config_date)
                if slot == "config" and report_date:
                    write_configuration(tmp, report_date)
                if automatic and report_date:
                    write_configuration(Path(staging) / "config.xlsx", report_date)
                for old in folder.glob(f"{stem}.*"):
                    old.unlink()
                tmp.replace(folder / f"{stem}{ext}")
                if automatic:
                    (Path(staging) / "config.xlsx").replace(folder / "config.xlsx")
                    for old in folder.glob("config.*"):
                        if old.name != "config.xlsx":
                            old.unlink()
                    meta["files"]["config"] = {
                        "name": "Detected from secondary sales", "size": (folder / "config.xlsx").stat().st_size,
                        "generated": True,
                    }
            meta["files"][slot] = {"name": filename[:255], "size": length}
            if report_date and (slot == "config" or automatic):
                meta["report_date"] = report_date
                meta["fsne_month"] = report_date[:7]
                meta["configuration_source"] = "secondary" if automatic else "workbook"
            self._write(meta)
            return meta

    def configure(
        self, run_id: str, report_date: str = "", label: str | None = None, *, fsne_month: str | None = None,
    ) -> dict:
        """Save form settings as the config workbook consumed by the pipeline.

        Validate against uploaded sales before replacing any accepted settings.
        This shares the upload/start lock so a worker cannot read a partial file.
        """
        with self.lock:
            meta = self.get(run_id)
            if meta["status"] != "draft":
                raise RunError("Settings can only be changed before the run starts")
            if fsne_month is not None:
                report_date = fsne_month_end(fsne_month)
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
                size = write_configuration(target, report_date)
                target.replace(folder / "config.xlsx")
            # Remove a previous macro workbook only after its replacement exists.
            for old in folder.glob("config.*"):
                if old.name != "config.xlsx":
                    old.unlink()
            meta["files"]["config"] = {"name": "Entered in form", "size": size, "generated": True}
            meta["report_date"] = report_date
            meta["fsne_month"] = report_date[:7]
            meta["configuration_source"] = "form"
            if label is not None:
                meta["label"] = label.strip()[:80] or "Untitled run"
            self._write(meta)
            return meta

    def start(
        self, run_id: str, label: str | None = None, started_by: str | None = None, *, override_unsettled: bool = False,
    ) -> dict:
        with self.lock:
            meta = self.get(run_id)
            if meta["status"] != "draft":
                raise RunError("This run has already started")
            missing = [SLOTS[s][0] for s in SLOTS if s != "config" and s not in meta["files"]]
            if missing:
                raise RunError("Missing files: " + ", ".join(missing))
            if not meta.get("report_date") or "config" not in meta["files"]:
                raise RunError("Choose an FSNE month or upload secondary sales to detect it.")
            by_date = history_by_date(self.history_paths())
            prior_month = previous_month(meta["report_date"])
            has_previous = any(dt[:7] == prior_month for dt in by_date)
            if not has_previous and not override_unsettled:
                raise RunError(f"Previous month {prior_month} has no settled history. Settle it first, or explicitly start without carried statuses.")
            meta["history_override"] = not has_previous and override_unsettled
            # Freeze the chosen sources at acceptance, before a queued run can
            # race a settlement/reopen. Later changes flag this dependency.
            sources = self.path(run_id) / "history-source"
            sources.mkdir(exist_ok=True)
            for old in sources.glob("*.xlsx"):
                old.unlink()
            dependencies = []
            for dt, source in sorted(by_date.items()):
                if dt[:7] >= meta["report_date"][:7]:
                    continue
                shutil.copyfile(source, sources / (dt + ".xlsx"))
                dependencies.append({"report_date": dt, "source": str(source)})
            meta["history_dependencies"] = dependencies
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
            if meta["plan_state"] == "settled":
                raise RunError("Reopen this settled month before deleting its run")
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

    def freeze_history_links(self, sources: list[Path]) -> None:
        """Keep older runs readable when a shared export is replaced or removed."""
        targets = {p.resolve() for p in sources}
        for meta in self.list():
            for folder in ("history", "history-source"):
                for link in (self.path(meta["id"]) / folder).glob("*.xlsx"):
                    if link.is_symlink() and link.resolve() in targets:
                        with tempfile.TemporaryDirectory(dir=link.parent) as staging:
                            copy = Path(staging) / link.name
                            shutil.copyfile(link, copy)
                            copy.replace(link)

    def action_plan_path(self, run_id: str) -> Path:
        """Build or upgrade an older export from its frozen private history."""
        with self.lock:
            meta = self.get(run_id)
            if meta["status"] != "ok":
                raise RunError("That run has no completed report")
            if not meta.get("run_date"):
                started = datetime.fromisoformat(meta.get("started") or meta["created"])
                self.update(run_id, run_date=started.astimezone().date().isoformat())
            run = self.path(run_id)
            target = run / "output" / "action_plan.xlsx"
            plans = plan_notes.read(run)
            noted = plan_notes.stamp(plans)
            # Rebuilt when the format is old or the page notes changed since the last export.
            if not target.exists() or meta.get("action_plan_format") != 2 or meta.get("action_plan_notes", "") != noted:
                import polars as pl

                parquet = run / "output" / "inferred_report.parquet"
                if not parquet.is_file():
                    raise RunError("This run's source report is missing; calculate it again to export an action plan.")
                paths = sorted((run / "history").glob("saved-*.xlsx"))
                write_action_plan(pl.read_parquet(parquet), meta["report_date"], target,
                                  {} if meta.get("history_override") else history_by_date(paths),
                                  entries=plan_notes.entries(run, plans))
                self.update(run_id, action_plan_format=2, action_plan_notes=noted)
            return target

    def plan_notes(self, run_id: str) -> dict:
        with self.lock:
            meta = self.get(run_id)
            return {"plans": plan_notes.read(self.path(run_id)),
                    "editable": meta["status"] == "ok" and meta.get("plan_state") != "settled"}

    def save_plan_note(self, run_id: str, body: dict, user: str) -> dict:
        with self.lock:
            plans = plan_notes.save(self.path(run_id), self.get(run_id), body, user)
        return {"plans": plans}

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
        # Snapshot construction and lifecycle changes share the lock; the
        # sources themselves were frozen when Start run was accepted.
        with self.lock:
            rows = snapshot(
                out / "inferred_report.parquet", report_date, hist, out / "action_plan.xlsx",
                sorted((run / "history-source").glob("*.xlsx")),
                label_counts=label_counts, carry_labels=not self.get(run_id).get("history_override", False),
            )
        self.update(run_id, action_plan_format=2, summary={**self.get(run_id).get("summary", {}), **label_counts})

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
        """Existing shared workbooks count as settled; only settled runs add history."""
        paths = sorted(self.history_dir.glob("*.xlsx"))
        paths.extend(
            self.path(m["id"]) / "history" / "snapshot.xlsx"
            for m in reversed(self.list()) if m["status"] == "ok" and m["plan_state"] == "settled"
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
        ("Latest run-rate source month", "Secondary sales: choose a file ending in the month before the FSNE month."),
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


def fsne_month_end(month: str) -> str:
    """Resolve the sole month selection to a calendar month-end calculation date."""
    try:
        if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError("Expected YYYY-MM")
        first = date.fromisoformat(month + "-01")
        return first.replace(day=calendar.monthrange(first.year, first.month)[1]).isoformat()
    except (ValueError, TypeError):
        raise RunError("FSNE month: choose a valid month and year.") from None


def write_configuration(target: Path, report_date: str) -> int:
    """Mark new configurations explicitly; legacy snapshots keep their old date rule."""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["variable", "value"])
    sheet.append(["starting_date", report_date])
    sheet.append(["fsne_month", report_date[:7]])
    sheet.add_table(Table(displayName="config_table", ref="A1:B3"))
    workbook.save(target)
    workbook.close()
    return target.stat().st_size


def validate_sales_month(data_dir: Path, report_date: str) -> None:
    from src.io import date as parse_date
    from src.secondary import load

    try:
        months = load(data_dir).select(parse_date("Month-Year").dt.truncate("1mo"))
        latest = months["Month-Year"].max()
        expected = previous_month(report_date)
        if latest is None or str(latest)[:7] != expected:
            found = str(latest)[:7] if latest is not None else "no valid month"
            raise RunError(
                f"Secondary sales: latest month is {found}. FSNE month {report_date[:7]} "
                f"requires sales through {expected}, the previous month."
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
            latest = run_rate_parent.monthly_sales(frame)["month"].max()
            if not isinstance(latest, date):
                raise RunError("Secondary sales: no valid sales month was found.")
            following = (latest.replace(day=28) + timedelta(days=4)).replace(day=1)
            return fsne_month_end(following.isoformat()[:7])
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


def write_action_plan(frame, report_date: str, target: Path, by_date: dict[str, Path], entries=None) -> None:
    """Exact left join to the previous calendar month; current entry cells blank
    unless ``entries`` (action plan page notes, exact ``batch_index``) pre-fill them.

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
    if entries is None:
        frame = frame.with_columns(pl.lit(None, dtype=pl.String).alias(c) for c in columns)
    else:
        frame = frame.join(entries.select("batch_index", *columns), on="batch_index", how="left",
                           validate="m:1", maintain_order="left")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as staging:
        temporary = Path(staging) / target.name
        frame.write_excel(temporary, worksheet="Sheet1", float_precision=15)
        # Excel protection is an editing aid. Uploads still discard every
        # report column and validate identities independently.
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter

        workbook = load_workbook(temporary)
        sheet = workbook.active
        assert sheet is not None
        for name in columns:
            index = frame.columns.index(name) + 1
            for row in sheet.iter_rows(min_row=2, min_col=index, max_col=index):
                row[0].protection = Protection(locked=False)
        status_column = get_column_letter(frame.columns.index("mitigation_status") + 1)
        dropdown = DataValidation(type="list", formula1='"' + ','.join(STATUSES) + '"', allow_blank=True)
        dropdown.errorTitle = "Choose a mitigation status"
        dropdown.error = "Choose a value from the dropdown."
        dropdown.showErrorMessage = True
        dropdown.errorStyle = "stop"
        sheet.add_data_validation(dropdown)
        dropdown.add(f"{status_column}2:{status_column}{sheet.max_row}")
        sheet.protection.sheet = True
        sheet.protection.autoFilter = False
        sheet.protection.selectLockedCells = False
        sheet.protection.selectUnlockedCells = False
        workbook.save(temporary)
        workbook.close()
        temporary.replace(target)


def snapshot(
    report_parquet: Path, report_date: str, hist_dir: Path, xlsx_out: Path, history_paths: list[Path],
    *, label_counts: dict[str, int] | None = None, carry_labels: bool = True,
) -> int:
    """Freeze prior settled months and inherit only previous-calendar-month labels.

    The export uses exact batch identities; snapshot inheritance follows the
    movement key. An explicit gap override leaves both kinds of labels empty.
    """
    import polars as pl

    frame = pl.read_parquet(report_parquet)
    if frame.is_empty():
        raise RunError("The uploaded files produced no stock rows")
    by_date = {dt: path for dt, path in history_by_date(history_paths).items() if dt[:7] < report_date[:7]}
    write_action_plan(frame, report_date, xlsx_out, by_date if carry_labels else {})
    epoch = pl.lit(date(1899, 12, 30))
    saved = frame.with_columns(
        (epoch + pl.duration(days=pl.col(c))).cast(pl.Date).alias(c) for c in ("report_date", "Expiry Date")
    )
    manual = {"mitigation_status": "mitigation_status_category", "action_plan": "action_plan_remarks"}
    candidates = [dt for dt in by_date if dt[:7] == previous_month(report_date)] if carry_labels else []
    if candidates:
        from src.movement import KEY, batch_key

        previous = pl.read_excel(by_date[max(candidates)], sheet_id=1, infer_schema_length=0)
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
            mitigation_carried_batches=int(labelled),
            mitigation_unlabelled_batches=saved.height - int(labelled),
        )
    hist_dir.mkdir(parents=True, exist_ok=True)
    for old in hist_dir.iterdir():
        old.unlink()
    for i, (_, path) in enumerate(sorted(by_date.items())):
        shutil.copyfile(path, hist_dir / f"saved-{i:04d}.xlsx")
    saved.write_excel(hist_dir / "snapshot.xlsx", worksheet="Sheet1", float_precision=15)
    return frame.height

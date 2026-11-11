"""Returned plans and settlement, always anchored to a run's frozen report.

Checks are staged without changing the accepted run. Acceptance builds a private
replacement before publishing it. The run snapshot is authoritative; shared
history is an export, and git failures are recorded separately from settlement.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
from openpyxl.utils.datetime import from_excel

from .errors import RunError

if TYPE_CHECKING:
    from .runs import Store

STATUSES = ("Actionable", "Not actionable", "Incorrect Stock", "RSL>10M_Good stock")
LABELS = {"provisional": "Pre-action plan view", "plan_uploaded": "Action plan view (not settled)", "settled": "Final view"}
ENTRIES = ("action_plan", "mitigation_status")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def decorate(meta: dict) -> dict:
    state = meta.setdefault("plan_state", "provisional")
    meta["view_label"] = LABELS[state]
    summary = meta.get("summary", {})
    flag = LABELS[state]
    if state == "provisional":
        flag += (f": statuses carried over from the previous month "
                 f"({summary.get('mitigation_carried_batches', 0):,} batches), "
                 f"{summary.get('mitigation_unlabelled_batches', 0):,} new batches unlabelled")
    meta["view_flag"] = flag
    return meta


def previous_month(report_date: str) -> str:
    return (date.fromisoformat(report_date).replace(day=1) - timedelta(days=1)).isoformat()[:7]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def report_frame(path: Path) -> pl.DataFrame:
    frame = pl.read_parquet(path)
    return frame.with_columns(
        (pl.lit(date(1899, 12, 30)) + pl.duration(days=pl.col(c))).cast(pl.Date).alias(c)
        for c in ("report_date", "Expiry Date")
    )


def check_plan(path: Path, report: Path, report_date: str) -> tuple[pl.DataFrame, dict]:
    try:
        frame = pl.read_excel(path, infer_schema_length=0)
    except Exception as exc:
        raise RunError("Could not read the action plan. Choose an Excel workbook (.xlsx).") from exc
    required = ("batch_index", *ENTRIES, "report_date")
    missing_columns = [c for c in required if c not in frame.columns]
    if missing_columns:
        raise RunError("Missing columns: " + ", ".join(missing_columns))
    frame = frame.with_columns(pl.col("batch_index").cast(pl.String))
    keys = frame["batch_index"]
    if keys.null_count() or (keys.str.strip_chars() == "").any():
        raise RunError("batch_index must not be blank")
    duplicates = keys.filter(keys.is_duplicated()).unique().sort().to_list()
    if duplicates:
        raise RunError("Duplicate batch_index: " + ", ".join(duplicates))
    expected = set(pl.read_parquet(report, columns=["batch_index"])["batch_index"])
    actual = set(keys)
    missing, extra = sorted(expected - actual), sorted(actual - expected)
    if missing or extra:
        raise RunError("Batch mismatch. Missing batches: " + (", ".join(missing) or "none")
                       + ". Extra batches: " + (", ".join(extra) or "none"))
    dates = frame["report_date"].to_list()
    def normalize(value) -> str:
        if value is None:
            return "blank"
        text = str(value)
        try:
            return from_excel(float(text)).date().isoformat()
        except (ValueError, TypeError, OverflowError, AttributeError):
            return text[:10]
    wrong = sorted({normalize(value) for value in dates} - {report_date})
    if wrong:
        raise RunError(f"Report date differs from this run ({report_date}): " + ", ".join(wrong))
    entries = frame.select("batch_index", *[
        pl.when(pl.col(c).cast(pl.String).str.strip_chars() == "").then(None)
        .otherwise(pl.col(c).cast(pl.String)).alias(c) for c in ENTRIES
    ])
    statuses = entries["mitigation_status"]
    unknown = statuses.filter(statuses.is_not_null() & ~statuses.is_in(STATUSES))
    counts = {"batches_matched": len(expected), "blank_remarks": entries["action_plan"].null_count(),
              "blank_statuses": statuses.null_count(), "unrecognised_statuses": len(unknown),
              "unrecognised_values": sorted(unknown.unique().to_list())}
    return entries, counts


def commit_history(history_dir: Path, paths: list[Path], message: str) -> dict:
    """Commit only the exported history paths; leave other staged work alone."""
    try:
        root = subprocess.run(["git", "-C", str(history_dir), "rev-parse", "--show-toplevel"],
                              check=True, capture_output=True, text=True, timeout=30).stdout.strip()
        relative = [str(p.relative_to(Path(root).resolve())) for p in paths]
        subprocess.run(["git", "-C", root, "add", "--", *relative], check=True,
                       capture_output=True, text=True, timeout=30)
        result = subprocess.run(["git", "-C", root, "commit", "--only", "-m", message, "--", *relative],
                                check=False, capture_output=True, text=True, timeout=30)
        if result.returncode:
            return {"ok": False, "error": (result.stderr or result.stdout).strip()[-2000:]}
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], check=True,
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return {"ok": True, "sha": sha}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc)[-2000:]}


class ActionPlanCycle:
    def __init__(self, store: Store):
        self.store = store

    def completed(self, run_id: str, *, settled: bool = False) -> dict:
        meta = self.store.get(run_id)
        if meta["status"] != "ok":
            raise RunError("That run has no completed report")
        if (meta["plan_state"] == "settled") != settled:
            raise RunError("Reopen this month before changing its plan" if not settled else "This run is not settled")
        if meta.get("out_of_date") and not settled:
            raise RunError("This run is out of date. Calculate a new run before uploading or settling.")
        return meta

    def check(self, run_id: str, filename: str, stream, length: int) -> dict:
        from .runs import MAX_UPLOAD

        with self.store.lock:
            self.completed(run_id)
            if Path(filename).suffix.lower() != ".xlsx" or not 0 < length <= MAX_UPLOAD:
                raise RunError("Choose a nonempty .xlsx action plan, up to 1 GB")
            run = self.store.path(run_id)
            with tempfile.TemporaryDirectory(dir=run) as directory:
                path = Path(directory) / "plan.xlsx"
                remaining = length
                with path.open("wb") as handle:
                    while remaining:
                        chunk = stream.read(min(1 << 20, remaining))
                        if not chunk:
                            raise RunError("Action plan upload was cut short")
                        handle.write(chunk)
                        remaining -= len(chunk)
                _, counts = check_plan(path, run / "output/inferred_report.parquet", self.store.get(run_id)["report_date"])
                token = uuid.uuid4().hex
                pending = {"token": token, "filename": Path(filename).name[:255], "counts": counts,
                           "snapshot": digest(run / "history/snapshot.xlsx")}
                path.replace(run / "pending-plan.xlsx")
                (run / "pending-plan.json").write_text(json.dumps(pending))
            return {"token": token, **counts}

    def accept(self, run_id: str, token: str, user: str) -> dict:
        with self.store.lock:
            meta = self.completed(run_id)
            run = self.store.path(run_id)
            try:
                pending = json.loads((run / "pending-plan.json").read_text())
            except (OSError, ValueError) as exc:
                raise RunError("Check the returned file before accepting it") from exc
            if token != pending["token"] or pending["snapshot"] != digest(run / "history/snapshot.xlsx"):
                raise RunError("The plan check has expired. Check the file again")
            entries, counts = check_plan(run / "pending-plan.xlsx", run / "output/inferred_report.parquet", meta["report_date"])
            saved = report_frame(run / "output/inferred_report.parquet").join(entries, on="batch_index", validate="1:1", maintain_order="left")
            self.rebuild(run_id, saved)
            (run / "pending-plan.xlsx").replace(run / "accepted-plan.xlsx")
            (run / "pending-plan.json").unlink()
            return self.store.update(run_id, plan_state="plan_uploaded", plan_upload={
                "time": now(), "revision": token, "uploader": user[:255], "filename": pending["filename"], "checks": counts,
            })

    def rebuild(self, run_id: str, saved: pl.DataFrame) -> None:
        """Publish only after downstream calculations succeed; never recalculate report."""
        from .runs import ROOT

        run = self.store.path(run_id)
        with tempfile.TemporaryDirectory(dir=run) as directory:
            stage = Path(directory)
            hist, out = stage / "history", stage / "output"
            shutil.copytree(run / "history", hist)
            shutil.copytree(run / "output", out)
            shutil.copyfile(run / "data.json", stage / "data.json")
            saved.write_excel(hist / "snapshot.xlsx", worksheet="Sheet1", float_precision=15)
            commands = [["-m", "src", name, "--history-dir", str(hist), "--output", str(out / f"{name}.parquet")]
                        for name in ("movement_series", "movement_parents")]
            commands.append(["ui/build.py", "--data-dir", str(run / "input"), "--history-dir", str(hist),
                             "--output-dir", str(out), "--payload", str(stage / "data.json"), "--frozen"])
            for command in commands:
                result = subprocess.run([sys.executable, *command], check=False, cwd=ROOT, capture_output=True, text=True, timeout=3600)
                with (run / "log.txt").open("a") as log:
                    log.write("\nAction plan rebuild: " + " ".join(command) + "\n" + result.stdout + result.stderr)
                if result.returncode:
                    raise RunError("Could not rebuild the action plan view. The accepted run is unchanged; see its log.")
            # Readers use the same store lock. Replace the snapshot last only
            # after all expensive work has passed.
            for name in ("movement_series.parquet", "movement_parents.parquet"):
                (out / name).replace(run / "output" / name)
            (stage / "data.json").replace(run / "data.json")
            (hist / "snapshot.xlsx").replace(run / "history/snapshot.xlsx")

    def settlement_check(self, run_id: str) -> dict:
        meta = self.completed(run_id)
        saved = pl.read_excel(self.store.path(run_id) / "history/snapshot.xlsx", infer_schema_length=0)
        blank = saved["mitigation_status"].cast(pl.String).str.strip_chars().fill_null("") == ""
        return {"month": meta["report_date"][:7], "batches": saved.height, "without_status": int(blank.sum()),
                "without_plan": not bool(meta.get("plan_upload")), "snapshot": digest(self.store.path(run_id) / "history/snapshot.xlsx")}

    def settle(self, run_id: str, body: dict, user: str) -> dict:
        with self.store.lock:
            check = self.settlement_check(run_id)
            if body.get("confirm") is not True or body.get("snapshot") != check["snapshot"]:
                raise RunError("Review and confirm the month, batch count and blank statuses before settling")
            if check["without_plan"] and body.get("confirm_without_plan") is not True:
                raise RunError("No plan uploaded. Explicitly confirm settling with carried-over statuses")
            meta = self.store.get(run_id)
            history = self.store.history_dir
            history.mkdir(parents=True, exist_ok=True)
            target = history / (meta["report_date"].replace("-", "_") + ".xlsx")
            # Existing filenames may describe an export day, so inspect report dates.
            from .runs import history_by_date

            replaced = [p for p in history.glob("*.xlsx")
                        if next(iter(history_by_date([p])))[:7] == check["month"] and p != target]
            self.store.freeze_history_links([target, *replaced])
            with tempfile.NamedTemporaryFile(dir=history, suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
            try:
                shutil.copyfile(self.store.path(run_id) / "history/snapshot.xlsx", temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            for old in replaced:
                old.unlink()
            settlement = {"time": now(), "settler": user[:255], "history_file": target.name,
                          "without_plan": check["without_plan"], "commit": {"ok": False, "error": "Commit pending"}}
            self.store.update(run_id, plan_state="settled", settlement=settlement, archived=None, superseded_by=None)
            for other in self.store.list():
                if other["id"] != run_id and (other.get("report_date") or "")[:7] == check["month"]:
                    fields = {"archived": now()}
                    if other["plan_state"] == "settled":
                        fields.update(plan_state="plan_uploaded" if other.get("plan_upload") else "provisional", superseded_by=run_id)
                    self.store.update(other["id"], **fields)
            settlement["commit"] = commit_history(history, [target, *replaced], f"Settle {check['month']} ({run_id})")
            return self.store.update(run_id, settlement=settlement)

    def reopen(self, run_id: str, confirm: bool, user: str) -> dict:
        with self.store.lock:
            meta = self.completed(run_id, settled=True)
            if confirm is not True:
                raise RunError("Confirm reopening this month and removing its settled history")
            target = self.store.history_dir / meta["settlement"]["history_file"]
            self.store.freeze_history_links([target])
            target.unlink(missing_ok=True)
            reopened = {"time": now(), "user": user[:255], "commit": {"ok": False, "error": "Commit pending"}}
            self.store.update(run_id, plan_state="plan_uploaded" if meta.get("plan_upload") else "provisional", reopened=reopened)
            month = meta["report_date"][:7]
            for other in self.store.list():
                if other["id"] == run_id:
                    continue
                dependencies = other.get("history_dependencies")
                if dependencies is None:
                    from .runs import history_by_date

                    dates = history_by_date(list((self.store.path(other["id"]) / "history").glob("saved-*.xlsx")))
                    dependencies = [{"report_date": dt} for dt in dates]
                if any(d["report_date"][:7] == month for d in dependencies):
                    self.store.update(other["id"], out_of_date={"month": month, "reopened_run": run_id, "time": now()})
            reopened["commit"] = commit_history(self.store.history_dir, [target], f"Reopen {month} ({run_id})")
            return self.store.update(run_id, reopened=reopened)

"""HTTP/Excel/git E2E for docs/action-plan-cycle.md; no unit-test harness.

    uv run python -m tests.e2e_action_plan_cycle

All mutable state and commits live in temporary repositories. A baseline from
this real pipeline run is retained under validation-artifacts for browser E2E.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import openpyxl
import polars as pl
from openpyxl.utils import get_column_letter
from polars.testing import assert_frame_equal

from server.app import make_server
from server.runs import Store

ROOT = Path(__file__).resolve().parent.parent

ARTIFACTS = ROOT / "validation-artifacts/action-plan"
FILES = {"secondary": "Mode_Sec_Month on Month.xlsb", "hierarchy": "PH_FG.xlsx",
         "shelflife": "Product Shelflife Report Multiple FSNE  (03-08-2026)  (03-09-2026).csv"}


def git(folder: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(folder), *args], check=False, capture_output=True, text=True)
    assert result.returncode == 0, f"git {' '.join(args)}: {result.stderr or result.stdout}"
    return result.stdout.strip()


def initialize(folder: Path) -> Path:
    history = folder / "history"
    history.mkdir(parents=True)
    for path in (ROOT / "history").glob("2026_0[2-8]_*.xlsx"):
        shutil.copyfile(path, history / path.name)
    git(folder, "init", "-q")
    git(folder, "config", "user.name", "FSNE E2E")
    git(folder, "config", "user.email", "e2e@example.invalid")
    git(folder, "add", "history")
    git(folder, "commit", "-qm", "Legacy settled history")
    return history


class Client:
    def __init__(self, port: int):
        self.port = port

    def request(self, method: str, path: str, body=None, *, status=200, filename=None) -> Any:
        # The HTTP boundary serves JSON objects, arrays, HTML and workbook bytes.
        # Each scenario asserts the route-specific response before using it.
        headers = {"Tailscale-User-Name": "Sales E2E"}
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if filename:
            headers["X-Filename"] = quote(filename)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=600)
        try:
            conn.request(method, path, body, headers)
            response = conn.getresponse()
            raw = response.read()
            assert response.status == status, (method, path, response.status, raw[:3000])
            return json.loads(raw) if "application/json" in response.getheader("Content-Type", "") else raw
        finally:
            conn.close()

    def route(self, ident: str, suffix="") -> str:
        return f"/api/runs/{ident}" + ("/" + suffix if suffix else "")

    def post(self, ident: str, action: str, body=None, *, status=200):
        return self.request("POST", self.route(ident, action), body or {}, status=status)

    def wait(self, ident: str) -> dict:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            meta = self.request("GET", self.route(ident))
            if meta["status"] not in {"queued", "running"}:
                assert meta["status"] == "ok", (meta, self.request("GET", self.route(ident, "log"))[-6000:])
                return meta
            time.sleep(0.3)
        raise AssertionError("Pipeline did not complete")

    def draft(self, report_date="2026-08-30", secondary: bytes | None = None) -> str:
        ident = self.request("POST", "/api/runs", {"label": "Action plan E2E"}, status=201)["id"]
        self.post(ident, "config", {"report_date": report_date})
        for slot, name in FILES.items():
            data = secondary if slot == "secondary" and secondary is not None else (ROOT / "sample_input" / name).read_bytes()
            self.request("PUT", self.route(ident, "files/" + slot), data,
                         filename="sales.xlsx" if slot == "secondary" and secondary is not None else name)
        return ident

    def start(self, **kwargs) -> str:
        ident = self.draft(**kwargs)
        self.post(ident, "start")
        self.wait(ident)
        return ident

    def check(self, ident: str, frame: pl.DataFrame, *, status=200):
        workbook = io.BytesIO()
        frame.write_excel(workbook, worksheet="Sheet1", float_precision=15)
        return self.request("PUT", self.route(ident, "action-plan/check"), workbook.getvalue(), status=status, filename="Returned sales plan.xlsx")

    def settle(self, ident: str, **extra):
        check = self.request("GET", self.route(ident, "settlement-check"))
        return self.post(ident, "settle", {"confirm": True, "snapshot": check["snapshot"], **extra})


@contextmanager
def application(folder: Path):
    store = Store(folder / "runs", folder / "history")
    server = make_server(store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        store.close()
        thread.join()


def hashes(folder: Path) -> dict:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob("*") if p.is_file()}


def frozen_expected(parquet: Path) -> pl.DataFrame:
    raw = pl.read_parquet(parquet)
    return raw.with_columns((pl.lit(date(1899, 12, 30)) + pl.duration(days=pl.col(c))).cast(pl.Date).alias(c)
                            for c in ("report_date", "Expiry Date"))


def assert_snapshot(run: Path, entries: pl.DataFrame):
    actual = pl.read_excel(run / "history/snapshot.xlsx", infer_schema_length=None)
    expected = frozen_expected(run / "output/inferred_report.parquet")
    assert_frame_equal(actual.select(expected.columns).cast(expected.schema), expected, rel_tol=1e-12, abs_tol=1e-8)
    expected_entries = expected.select("batch_index").join(entries.select("batch_index", "action_plan", "mitigation_status"),
                                                           on="batch_index", maintain_order="left")
    assert_frame_equal(actual.select(expected_entries.columns), expected_entries)


def assert_current_movement(run: Path) -> dict:
    """Reconcile the E2E movement output independently to the accepted entries."""
    saved = pl.read_excel(run / "history/snapshot.xlsx", infer_schema_length=0)
    expected = saved.filter(pl.col("mitigation_status").str.starts_with("Actionable")).select(
        pl.col("total_closing_stock_bdt").cast(pl.Float64).sum()).item()
    movement = pl.read_parquet(run / "output/movement_series.parquet")
    current_date = date.fromisoformat(str(saved["report_date"][0])[:10])
    current = movement.filter((pl.col("month") == current_date) & (pl.col("brand") == "Grand Total"))
    assert current.height > 0, "Movement omitted the run's current month"
    actual = current.select((pl.col("cm_no_new_bdt").fill_null(0) + pl.col("cm_new_bdt").fill_null(0)).sum()).item()
    assert math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-6), (actual, expected)
    return {"month": current_date.isoformat(), "movement_stock_bdt": actual, "accepted_actionable_stock_bdt": expected}


def changed_month_sales() -> bytes:
    from src.secondary import load

    sales = load(ROOT / "sample_input").with_columns(
        pl.when(pl.col("Month-Year") == "Aug-2026").then(pl.lit("Sep-2026")).otherwise(pl.col("Month-Year")).alias("Month-Year")
    )
    buf = io.BytesIO()
    sales.write_excel(buf, worksheet="Sheet1")
    return buf.getvalue()


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fsne-plan-e2e-", dir=ARTIFACTS) as directory:
        folder = Path(directory)
        history = initialize(folder)
        original_history = hashes(history)
        remote = folder / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        git(folder, "remote", "add", "origin", str(remote))
        with application(folder) as api:
            first = api.start()
            run = folder / "runs" / first
            meta = api.request("GET", api.route(first))
            assert meta["plan_state"] == "provisional" and meta["view_label"] == "Pre-action plan view"
            assert meta["summary"]["mitigation_carried_batches"] > 0
            page = api.request("GET", "/?run=" + first).decode()
            assert "Action plan input file" in page and meta["view_flag"] in page
            assert hashes(history) == original_history
            print("A1 A13: provisional run and legacy settled-history admission passed", flush=True)
            fixture = ARTIFACTS / "fixture"
            if fixture.exists():
                shutil.rmtree(fixture)
            shutil.copytree(run, fixture)
            download = api.request("GET", api.route(first, "report.xlsx"))
            workbook = openpyxl.load_workbook(io.BytesIO(download))
            sheet = workbook.active
            assert sheet is not None and sheet.protection.sheet
            headers = [c.value for c in sheet[1]]
            editable = {headers.index(c) + 1 for c in ("action_plan", "mitigation_status")}
            for row in sheet.iter_rows(min_row=2):
                assert all(cell.protection.locked == (cell.column not in editable) for cell in row)
            assert all(cell.protection.locked for cell in sheet[1])
            dropdown = list(sheet.data_validations.dataValidation)
            assert len(dropdown) == 1 and dropdown[0].type == "list" and dropdown[0].showErrorMessage
            assert dropdown[0].formula1 == '"Actionable,Not actionable,Incorrect Stock,RSL>10M_Good stock"'
            status_col = get_column_letter(headers.index("mitigation_status") + 1)
            assert str(dropdown[0].sqref) == f"{status_col}2:{status_col}{sheet.max_row}"
            workbook.close()
            print("A2: locked cells and status validation verified in downloaded workbook", flush=True)
            frame = pl.read_excel(download, infer_schema_length=0)
            size = frame.height
            entries = frame.select("batch_index").with_columns(
                pl.Series("action_plan", [None, "  Keep this spacing  "] + ["Sales remark"] * (size - 2)),
                pl.Series("mitigation_status", [None, None, "Needs review"] + ["Actionable"] * (size - 3)),
            )
            returned = frame.drop("action_plan", "mitigation_status").join(entries, on="batch_index", maintain_order="left").with_columns(
                pl.lit("999999999").alias("total_closing_stock_bdt"), pl.lit("Tampered town").alias("Town"),
                pl.lit("Ignored last-month label").alias("lm_mitigation_status"),
            ).reverse()
            (ARTIFACTS / "returned-plan.xlsx").unlink(missing_ok=True)
            returned.write_excel(ARTIFACTS / "returned-plan.xlsx", worksheet="Sheet1")
            before = hashes(run)
            cases = [
                (returned.slice(1), returned["batch_index"][0]),
                (pl.concat([returned, returned.head(1).with_columns(pl.lit("extra-batch").alias("batch_index"))]), "extra-batch"),
                (pl.concat([returned, returned.head(1)]), "Duplicate batch_index"),
                (returned.drop("action_plan"), "action_plan"),
                (returned.drop("mitigation_status"), "mitigation_status"),
                (returned.drop("batch_index"), "batch_index"),
                (returned.with_columns(pl.lit("2000-01-01").alias("report_date")), "Report date differs"),
            ]
            for invalid, reason in cases:
                failure = api.check(first, invalid, status=400)
                assert reason in failure["error"], failure
                assert hashes(run) == before, "Rejected file changed the accepted run"
            print("A5: missing/extra/duplicate batches, missing columns and wrong dates refused without mutation", flush=True)
            checked = api.check(first, returned)
            assert {k: checked[k] for k in ("batches_matched", "blank_remarks", "blank_statuses", "unrecognised_statuses")} == {
                "batches_matched": size, "blank_remarks": 1, "blank_statuses": 2, "unrecognised_statuses": 1}
            assert api.request("GET", api.route(first))["plan_state"] == "provisional"
            frozen_files = {name: hashlib.sha256((run / "output" / name).read_bytes()).hexdigest()
                            for name in ("inferred_report.parquet", "inferred_report.xlsx", "run_rates.parquet", "run_rate_audit.parquet")}
            movement_before = (run / "output/movement_series.parquet").read_bytes()
            meta = api.post(first, "action-plan/accept", {"token": checked["token"]})
            assert meta["plan_state"] == "plan_uploaded" and meta["view_label"] == "Action plan view (not settled)"
            assert meta["plan_upload"]["uploader"] == "Sales E2E"
            assert_snapshot(run, entries)
            movement_check = assert_current_movement(run)
            for name, value in frozen_files.items():
                assert hashlib.sha256((run / "output" / name).read_bytes()).hexdigest() == value
            assert (run / "output/movement_series.parquet").read_bytes() != movement_before
            assert hashes(history) == original_history
            assert b"Action plan view (not settled)" in api.request("GET", "/?run=" + first)
            print("A3 A4 A6: sorted/tampered upload joined safely, counts exact, frozen report unchanged, view rebuilt", flush=True)
            # Acceptance tokens cannot be replayed.
            api.post(first, "action-plan/accept", {"token": checked["token"]}, status=400)
            unrelated = folder / "unrelated.txt"
            unrelated.write_text("Already staged by another task\n")
            git(folder, "add", "unrelated.txt")
            count_before = int(git(folder, "rev-list", "--count", "HEAD"))
            settled = api.settle(first)
            assert settled["plan_state"] == "settled" and settled["settlement"]["commit"]["ok"]
            export = history / "2026_08_30.xlsx"
            assert export.read_bytes() == (run / "history/snapshot.xlsx").read_bytes()
            assert int(git(folder, "rev-list", "--count", "HEAD")) == count_before + 1
            assert git(folder, "show", "--pretty=", "--name-only", "HEAD") == "history/2026_08_30.xlsx"
            assert git(folder, "diff", "--cached", "--name-only") == "unrelated.txt"
            assert git(remote, "for-each-ref") == "", "A remote ref was changed"
            assert b"Final view" in api.request("GET", "/?run=" + first)
            api.post(first, "delete", status=400)
            print("A7: exactly one local path-only commit, unrelated staging retained, remote untouched", flush=True)
            # Another actual pipeline run for this month settles without an upload.
            second = api.start(report_date="2026-08-31")
            second_dir = folder / "runs" / second
            carried = pl.read_excel(second_dir / "history/snapshot.xlsx", infer_schema_length=0)
            check = api.request("GET", api.route(second, "settlement-check"))
            assert check["without_plan"] and check["batches"] == size
            refused = api.post(second, "settle", {"confirm": True, "snapshot": check["snapshot"]}, status=400)
            assert "Explicitly confirm" in refused["error"] and not (history / "2026_08_31.xlsx").exists()
            result = api.settle(second, confirm_without_plan=True)
            assert result["plan_state"] == "settled" and result["settlement"]["commit"]["ok"], result
            assert not export.exists()
            assert len([p for p in history.glob("*.xlsx") if str(pl.read_excel(p, columns=["report_date"], infer_schema_length=0)["report_date"][0])[:7] == "2026-08"]) == 1
            first_meta = api.request("GET", api.route(first))
            assert first_meta["archived"] and first_meta["plan_state"] == "plan_uploaded"
            assert sum(r["plan_state"] == "settled" and r["report_date"][:7] == "2026-08" for r in api.request("GET", "/api/runs")) == 1
            assert_frame_equal(pl.read_excel(history / "2026_08_31.xlsx", infer_schema_length=0), carried)
            print("A9 A10: stronger no-plan confirmation, carried labels exported, one settled run/file per calendar month", flush=True)
            # A subsequent month genuinely consumes this settlement through Start.
            sales = changed_month_sales()
            later = api.start(report_date="2026-09-30", secondary=sales)
            later_meta = api.request("GET", api.route(later))
            assert any(d["report_date"] == "2026-08-31" for d in later_meta["history_dependencies"])
            reopened = api.post(second, "reopen", {"confirm": True})
            assert reopened["plan_state"] == "provisional" and reopened["reopened"]["commit"]["ok"], reopened
            assert not (history / "2026_08_31.xlsx").exists()
            assert git(folder, "show", "--format=", "--diff-filter=D", "--name-only", "HEAD") == "history/2026_08_31.xlsx"
            assert api.request("GET", api.route(later))["out_of_date"]["month"] == "2026-08"
            # Missing prior history refuses Start, then the explicit override
            # traverses the complete pipeline with blank exported/carried labels.
            gap = api.draft(report_date="2026-09-30", secondary=sales)
            refusal = api.post(gap, "start", status=400)
            assert "Previous month 2026-08" in refusal["error"]
            assert api.request("GET", api.route(gap))["status"] == "draft"
            api.post(gap, "start", {"override_unsettled": True})
            gap_meta = api.wait(gap)
            assert gap_meta["history_override"] and gap_meta["summary"]["mitigation_carried_batches"] == 0
            gap_snapshot = pl.read_excel(folder / "runs" / gap / "history/snapshot.xlsx", infer_schema_length=0)
            assert gap_snapshot["mitigation_status"].null_count() == gap_snapshot.height
            gap_export = pl.read_excel(api.request("GET", api.route(gap, "report.xlsx")), infer_schema_length=0)
            assert all(gap_export[c].null_count() == gap_export.height for c in ("lm_action_plan", "lm_mitigation_status"))
            print("A11 A12: reopen committed deletion, dependent run flagged; gap refused then override completed with empty labels", flush=True)
            # Restore first run, then prove real git executable absence does not
            # roll back settlement. No mocking or production repository changes.
            environment_path = os.environ["PATH"]
            empty_bin = folder / "empty-bin"
            empty_bin.mkdir()
            try:
                os.environ["PATH"] = str(empty_bin)
                no_git = api.settle(first)
            finally:
                os.environ["PATH"] = environment_path
            assert no_git["plan_state"] == "settled" and not no_git["settlement"]["commit"]["ok"]
            assert (history / "2026_08_30.xlsx").is_file()
            assert b"settled, not committed" in api.request("GET", "/?run=" + first)
            print("A8: git absent, run settled and history exported with visible commit failure", flush=True)
            # Reopen with a plan restores that state; replacement uploads work
            # even when every status is blank (current month must remain visible).
            reopened = api.post(first, "reopen", {"confirm": True})
            assert reopened["plan_state"] == "plan_uploaded"
            blank = returned.with_columns(pl.lit(None, dtype=pl.String).alias("mitigation_status"))
            checked = api.check(first, blank)
            api.post(first, "action-plan/accept", {"token": checked["token"]})
            blank_movement = assert_current_movement(run)
            assert blank_movement["movement_stock_bdt"] == 0
            payload = json.loads((run / "data.json").read_text())
            assert payload["SERIES"]["labels"][-1] == "Aug"
            assert pl.read_excel(run / "history/snapshot.xlsx", infer_schema_length=0)["mitigation_status"].null_count() == size
            report = {"acceptance": [f"A{i}" for i in range(1, 14)], "batches": size,
                      "frozen_report_hashes": frozen_files, "remote_refs": git(remote, "for-each-ref"),
                      "movement": movement_check, "blank_movement": blank_movement,
                      "baseline_fixture": str(fixture), "result": "passed"}
            (ARTIFACTS / "e2e-results.json").write_text(json.dumps(report, indent=2) + "\n")
            print("ACTION_PLAN_E2E_PASSED", flush=True)


def serve_browser():
    """Seed only from the real pipeline artifact; browser drives all transitions."""
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    with tempfile.TemporaryDirectory(prefix="fsne-plan-browser-", dir=ARTIFACTS) as directory:
        folder = Path(directory)
        initialize(folder)
        fixture = ARTIFACTS / "fixture"
        if not fixture.is_dir():
            raise SystemExit("Run the HTTP E2E first to create the real pipeline baseline")
        shutil.copytree(fixture, folder / "runs/RUN-0001")
        meta_path = folder / "runs/RUN-0001/meta.json"
        legacy = json.loads(meta_path.read_text())
        expected_counts = {key: legacy["summary"].pop(key) for key in
                           ("mitigation_carried_batches", "mitigation_unlabelled_batches")}
        legacy.pop("plan_state", None)
        meta_path.write_text(json.dumps(legacy))
        store = Store(folder / "runs", folder / "history")
        server = make_server(store, port=0)
        print(json.dumps({"port": server.server_port, "directory": directory, "expected_counts": expected_counts,
                          "returned": str(ARTIFACTS / "returned-plan.xlsx")}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            store.close()


if __name__ == "__main__":
    serve_browser() if "--serve-browser" in sys.argv else main()

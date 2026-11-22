"""Returned-plan status normalization through real Excel checks and snapshot writes."""

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import polars as pl

from server.action_plans import STATUSES, check_plan
from server.runs import Store


class ActionPlanStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fsne-plan-status-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = self.root / "returned.xlsx"
        self.report = self.root / "report.parquet"
        self.report_date = "2026-11-30"

    def write_plan(self, statuses, remarks=None):
        frame = pl.DataFrame({
            "batch_index": [f"batch-{i}" for i in range(len(statuses))],
            "report_date": [date.fromisoformat(self.report_date)] * len(statuses),
            "action_plan": remarks if remarks is not None else ["Keep stock"] * len(statuses),
            "mitigation_status": statuses,
        })
        frame.write_excel(self.plan)
        frame.drop("action_plan", "mitigation_status").with_columns(
            (pl.col("report_date") - date(1899, 12, 30)).dt.total_days(),
            pl.lit(47000).alias("Expiry Date"),
            pl.lit(123.45).alias("total_closing_stock_bdt"),
        ).write_parquet(self.report)
        return frame

    def test_supported_statuses_are_trimmed_before_validation(self):
        whitespace = ("", " ", "\t", "\n", "\r\n", "\u00a0", "\u202f", "\u2003", " \t\u00a0")
        statuses = [padding + status + padding for status in STATUSES for padding in whitespace]
        self.write_plan(statuses)
        entries, counts = check_plan(self.plan, self.report, self.report_date)
        self.assertEqual(counts["unrecognised_statuses"], 0)
        self.assertEqual(counts["unrecognised_values"], [])
        self.assertEqual(counts["blank_statuses"], 0)
        self.assertEqual(entries["mitigation_status"].to_list(), [s for s in STATUSES for _ in whitespace])

    def test_blank_statuses_remain_blank_and_unknown_labels_still_warn(self):
        self.write_plan([None, "", " \t\u00a0\n", " Needs review \u00a0", " Needs review ", "RSL>10M_Goodstock"])
        entries, counts = check_plan(self.plan, self.report, self.report_date)
        self.assertEqual(entries["mitigation_status"].to_list(),
                         [None, None, None, "Needs review", "Needs review", "RSL>10M_Goodstock"])
        self.assertEqual(counts["blank_statuses"], 3)
        self.assertEqual(counts["unrecognised_statuses"], 3)
        self.assertEqual(counts["unrecognised_values"], ["Needs review", "RSL>10M_Goodstock"])

    def test_free_text_remarks_keep_their_spacing(self):
        self.write_plan([" Actionable "] * 3, ["  Sales push  ", " \t\u00a0", None])
        entries, counts = check_plan(self.plan, self.report, self.report_date)
        self.assertEqual(entries["action_plan"].to_list(), ["  Sales push  ", None, None])
        self.assertEqual(counts["blank_remarks"], 2)
        self.assertEqual(entries["batch_index"].to_list(), ["batch-0", "batch-1", "batch-2"])

    def test_acceptance_saves_trimmed_statuses_in_both_snapshot_formats(self):
        frame = self.write_plan(["\u00a0RSL>10M_Good stock \t", " Actionable ", " Needs review "])
        store = Store(self.root / "runs", self.root / "shared-history", start_worker=False)
        run_id = store.create("Status normalization")["id"]
        store.update(run_id, status="ok", report_date=self.report_date)
        run = store.path(run_id)
        (run / "history").mkdir()
        (run / "output").mkdir()
        frame.write_excel(run / "history" / "snapshot.xlsx")
        shutil.copyfile(self.report, run / "output" / "inferred_report.parquet")
        for name in ("movement_series", "movement_parents"):
            pl.DataFrame({"placeholder": [1]}).write_parquet(run / "output" / f"{name}.parquet")
        (run / "data.json").write_text("{}", encoding="utf-8")
        original_report = (run / "output" / "inferred_report.parquet").read_bytes()
        content = self.plan.read_bytes()
        checked = store.cycle.check(run_id, "returned.xlsx", io.BytesIO(content), len(content))
        self.assertEqual(checked["unrecognised_statuses"], 1)
        # Exercise the real accept/rebuild and snapshot publication, while
        # isolating movement/dashboard subprocess calculations from this test.
        with patch("server.action_plans.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            meta = store.cycle.accept(run_id, checked["token"], "test")
        expected = ["RSL>10M_Good stock", "Actionable", "Needs review"]
        for name, read in (("snapshot.xlsx", pl.read_excel), ("snapshot.parquet", pl.read_parquet)):
            with self.subTest(snapshot=name):
                self.assertEqual(read(run / "history" / name)["mitigation_status"].to_list(), expected)
        self.assertEqual(meta["plan_upload"]["checks"]["unrecognised_values"], ["Needs review"])
        self.assertEqual(meta["plan_state"], "plan_uploaded")
        self.assertEqual((run / "output" / "inferred_report.parquet").read_bytes(), original_report)
        self.assertEqual(json.loads((run / "meta.json").read_text())["plan_upload"]["checks"]["unrecognised_statuses"], 1)


if __name__ == "__main__":
    unittest.main()

"""Rescue plan notes by brand / SKU / town: resolution, HTTP save/load, pre-filled input file."""
import http.client
import io
import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import openpyxl
import polars as pl

from server import plan_notes
from server.app import make_server
from server.runs import Store
from src.rescue import plan_status, plan_text

# batch, brand, material, town, has last-month plan
BATCHES = [("b1", "A", "1", "T1", False), ("b2", "A", "1", "T2", False), ("b3", "A", "2", "T1", False),
           ("b4", "A", "2", "T2", True), ("b5", "B", "3", "T1", False)]


def population():
    return pl.DataFrame(BATCHES, schema=["batch_index", "brand", "material", "town", "has_plan"], orient="row")


def plan(scope, brand, material=None, town=None, categories=(), remarks=None):
    return {plan_notes.key(scope, brand, material, town): {"scope": scope, "brand": brand, "material": material, "town": town,
                                                          "categories": list(categories), "remarks": remarks}}


@contextmanager
def app():
    with tempfile.TemporaryDirectory(prefix="fsne-plan-notes-") as directory:
        store = Store(Path(directory) / "runs", Path(directory) / "shared", start_worker=False)
        server = make_server(store, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield store, server.server_port
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def request(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        response = conn.getresponse()
        return response.status, json.loads(response.read() or b"{}")
    finally:
        conn.close()


def seed(store):
    run = store.create("Notes")
    folder = store.path(run["id"])
    (folder / "output").mkdir()
    (folder / "history").mkdir()
    keys = [b[0] for b in BATCHES]
    pl.DataFrame({"batch_index": keys, "report_date": [46264] * 5, "Expiry Date": [46400] * 5,
                  "total_closing_stock_kl": [1.0] * 5}).write_parquet(folder / "output" / "inferred_report.parquet")
    population().write_parquet(folder / "output" / "rescue_batches.parquet")
    pl.DataFrame({"report_date": [date(2026, 7, 31)], "batch_index": ["b4"], "action_plan": ["Sales Push"],
                  "mitigation_status": ["Actionable"]}).write_excel(folder / "history" / "saved-0000.xlsx")
    store.update(run["id"], status="ok", report_date="2026-08-30", started="2026-11-10T12:00:00+00:00")
    return run["id"]


def workbook(port, run_id):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request("GET", f"/api/runs/{run_id}/report.xlsx")
        response = conn.getresponse()
        content = response.read()
        assert response.status == 200, content
    finally:
        conn.close()
    book = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    sheet = book.active
    assert sheet is not None
    rows = list(sheet.values)
    book.close()
    head = [str(h) for h in rows[0]]
    return {r[head.index("batch_index")]: (r[head.index("action_plan")], r[head.index("mitigation_status")]) for r in rows[1:]}


class ResolutionTest(unittest.TestCase):
    def test_nearest_scope_wins_and_covered_batches_are_never_reached(self):
        plans = {**plan("brand", "A", categories=["Sales push"], remarks="2 cases by 15 Sep"),
                 **plan("sku", "A", "2", categories=["Liquidation - E-Com"]),
                 **plan("town", "A", "1", "T2", categories=["L&D claim"])}
        got = {r["batch_index"]: (r["action_plan"], r["mitigation_status"], r["scope"])
               for r in plan_notes.resolve(population().filter(~pl.col("has_plan")), plans).iter_rows(named=True)}
        self.assertEqual(got, {"b1": ("Sales push; 2 cases by 15 Sep", "Actionable", "brand"),
                               "b2": ("L&D claim", "Not actionable", "town"),
                               "b3": ("Liquidation - E-Com", "Actionable", "sku")})

    def test_status_and_text_follow_category_order(self):
        self.assertEqual(plan_status(["L&D claim", "Stock adjustment"]), "Incorrect Stock")
        self.assertEqual(plan_status(["L&D claim", "Sales push"]), "Actionable")
        self.assertIsNone(plan_status([]))
        self.assertEqual(plan_text(["Liquidation - E-Com", "Sales push"], "via Daraz"), "Sales push; Liquidation - E-Com; via Daraz")
        self.assertEqual(plan_text([], "call DB"), "call DB")


class PlanNotesHttpTest(unittest.TestCase):
    def test_save_load_remove_and_prefill(self):
        with app() as (store, port):
            run_id = seed(store)
            code, data = request(port, "GET", f"/api/runs/{run_id}/notes")
            self.assertEqual((code, data["plans"], data["editable"]), (200, {}, True))
            base = f"/api/runs/{run_id}/notes"
            self.assertEqual(request(port, "POST", base, {"scope": "brand", "brand": "A", "categories": ["Sales push", "Trade offer (TLO / QDD / 2+1)"],
                                                          "remarks": " QDD in Sep "})[0], 200)
            self.assertEqual(request(port, "POST", base, {"scope": "sku", "brand": "A", "material": "2", "categories": ["Liquidation - Modern Trade"]})[0], 200)
            plans = request(port, "GET", base)[1]["plans"]
            brand = plans[plan_notes.key("brand", "A", None, None)]
            self.assertEqual((brand["remarks"], brand["by"]), ("QDD in Sep", "local"))

            rows = workbook(port, run_id)
            self.assertEqual(rows["b1"], ("Sales push; Trade offer (TLO / QDD / 2+1); QDD in Sep", "Actionable"))
            self.assertEqual(rows["b2"], rows["b1"])
            self.assertEqual(rows["b3"], ("Liquidation - Modern Trade", "Actionable"))
            self.assertEqual(rows["b4"], (None, None))
            self.assertEqual(rows["b5"], (None, None))

            request(port, "POST", base, {"scope": "brand", "brand": "A", "categories": [], "remarks": ""})
            rows = workbook(port, run_id)
            self.assertEqual((rows["b1"], rows["b3"]), ((None, None), ("Liquidation - Modern Trade", "Actionable")))

    def test_invalid_plans_are_refused(self):
        with app() as (store, port):
            run_id = seed(store)
            base = f"/api/runs/{run_id}/notes"
            for body, message in [
                ({"scope": "brand", "brand": "A", "categories": ["Maybe"]}, "categories"),
                ({"scope": "sku", "brand": "A", "categories": ["Sales push"]}, "needs"),
                ({"scope": "region", "brand": "A", "categories": ["Sales push"]}, "scope"),
                ({"scope": "brand", "brand": "C", "categories": ["Sales push"]}, "no uncovered"),
                ({"scope": "town", "brand": "A", "material": "2", "town": "T2", "categories": ["Sales push"]}, "no uncovered"),
                ({"scope": "brand", "brand": "A", "remarks": "x" * 301}, "at most"),
            ]:
                code, data = request(port, "POST", base, body)
                self.assertEqual(code, 400, body)
                self.assertIn(message, data["error"])
            self.assertFalse((store.path(run_id) / "plan_notes.json").exists())

    def test_settled_run_plans_are_read_only(self):
        with app() as (store, port):
            run_id = seed(store)
            base = f"/api/runs/{run_id}/notes"
            request(port, "POST", base, {"scope": "brand", "brand": "B", "categories": ["Sales push"]})
            store.update(run_id, plan_state="settled")
            data = request(port, "GET", base)[1]
            self.assertFalse(data["editable"])
            self.assertEqual(len(data["plans"]), 1)
            code, data = request(port, "POST", base, {"scope": "brand", "brand": "B", "categories": ["L&D claim"]})
            self.assertEqual(code, 400)
            self.assertIn("settled", data["error"])


if __name__ == "__main__":
    unittest.main()

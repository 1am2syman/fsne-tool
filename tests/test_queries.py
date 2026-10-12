"""Behavioral tests with tiny generated workbooks, no production data needed."""

import ast
import csv
import importlib
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import openpyxl
import polars as pl
from openpyxl.worksheet.table import Table

from src import (
    db_landing,
    history,
    ph,
    run_rate_parent,
    run_rate_town,
    sec_history,
    soh_db,
    starting_date,
)
from src.io import ROOT, number, promote_headers, resolve_path


def workbook(path, headers, rows, table=None):
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Sheet1"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    if table:
        sheet.add_table(Table(displayName=table, ref=sheet.dimensions))
    book.save(path)
    book.close()


def secondary():
    return pl.DataFrame(
        {
            "area_name (city)": ["New Town", "New Town", "New Town", "Other", "Other"],
            "parent_material_code": ["10", "10", "10", "20", "20"],
            "Month-Year": [
                "2025-01-01",
                "2025-01-01",
                "2025-02-01",
                "2025-03-01",
                "2025-03-01",
            ],
            "material_code": ["001"] * 5,
            "BATCH_NBR": ["B"] * 5,
            "sec_vol_kl_mth (billwise)": ["6", "3", "-5", "12", None],
        }
    )


class StructureTests(unittest.TestCase):
    def test_all_sources_have_vectorized_modules(self):
        def udf_calls(source):
            return [
                node.attr
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Attribute)
                and node.attr in {"map_elements", "map_rows", "iter_rows", "apply"}
            ]

        self.assertEqual(udf_calls("frame.map_elements(fn)"), ["map_elements"])
        for query in (ROOT / "queries").glob("*.txt"):
            module = importlib.import_module(f"src.{query.stem}")
            self.assertTrue(callable(module.build))
            self.assertTrue(callable(module.transform))
        for path in (ROOT / "src").glob("*.py"):
            self.assertFalse(udf_calls(path.read_text()), path.name)


class TransformationTests(unittest.TestCase):
    def test_rates_zero_fill_global_months(self):
        result = run_rate_parent.transform(secondary())
        self.assertEqual(result["avg_sec"].to_list(), [3.0, 4.0])
        self.assertEqual(
            run_rate_town.transform(result)["avg_sec_town"].to_list(), [3.0, 4.0]
        )

    def test_secondary_batch_key_and_null(self):
        result = sec_history.transform(secondary())
        self.assertEqual(result["batch_index"][0], "new_town001B")
        self.assertEqual(result["sec_kl"].to_list(), [6.0, 3.0, 0.0, 12.0, None])
        self.assertEqual(result.schema["Month-Year"], pl.Date)

    def test_hierarchy_fill_deduplicate_round(self):
        result = ph.transform(
            pl.DataFrame(
                {
                    "material_code": ["1", "1", "2"],
                    "material_desc": ["first", "duplicate", "second"],
                    "parent_material_code": [None, "99", "10"],
                    "volume": ["2.5", "bad-removed-before-cast", "3.5"],
                }
            )
        )
        self.assertEqual(result["parent_material_code"].to_list(), [1, 10])
        self.assertEqual(result["volume"].to_list(), [2, 4])

    def test_landing_excludes_grand(self):
        result = db_landing.transform(
            pl.DataFrame(
                {
                    "Row Labels": ["1", "Grand Total", "grand total"],
                    "DB Landing rate": ["1,234.5", "bad", "bad"],
                }
            )
        )
        self.assertEqual(result.rows(), [(1, 1234.5)])

    def test_history_filters_and_right_splits(self):
        values = {
            name: ["1", "bad", None]
            for name in history.FLOAT_COLUMNS + history.INT_COLUMNS
        }
        values.update({name: ["text", "bad", None] for name in history.TEXT_COLUMNS})
        values.update(
            {
                "Town": ["X", "Town", None],
                "report_date": ["45658", "report_date", None],
                "batch_index": ["north-town-1-batch-tail", "bad", None],
                "extra": ["keep", "bad", None],
            }
        )
        result = history.transform(pl.DataFrame(values))
        self.assertEqual(result["report_date"][0], date(2025, 1, 1))
        self.assertEqual(result["batch_index_short"][0], "north-town-1")
        self.assertIn("extra", result.columns)
        for value, expected in [("plain", "plain"), ("one-two", "one")]:
            frame = (
                pl.DataFrame(values)
                .head(1)
                .with_columns(pl.lit(value).alias("batch_index"))
            )
            self.assertEqual(history.transform(frame)["batch_index_short"][0], expected)

    def test_stock_left_joins_and_literal_zero_filter(self):
        values = {
            name: ["1", "1", "1"] for name in soh_db.INT_COLUMNS + soh_db.FLOAT_COLUMNS
        }
        values.update({name: ["text", "text", "text"] for name in soh_db.TEXT_COLUMNS})
        values.update(
            {
                "Material Code": ["1", "99", "1"],
                "Town": ["New Town"] * 3,
                "Batch code": ["B"] * 3,
                "Expiry Date": ["'01/31/2025"] * 3,
                "Closing Stock": ["5", "0.0", "0"],
            }
        )
        hierarchy = pl.DataFrame(
            {
                "material_code": [1],
                "parent_material_code": [10],
                "material_desc": ["Product"],
            }
        )
        result = soh_db.transform(
            pl.DataFrame(values), hierarchy, run_rate_parent.transform(secondary())
        )
        self.assertEqual(result.height, 2)
        self.assertEqual(result["avg_sec"].to_list(), [3.0, None])
        self.assertEqual(result["batch_index"][0], "new town1B")
        self.assertEqual(result["Expiry Date"][0], date(2025, 1, 31))

    def test_config_and_invalid_dates(self):
        frame = pl.DataFrame(
            {"variable": ["starting_date", "other"], "value": ["2025-01-01", "keep"]}
        )
        self.assertEqual(
            starting_date.transform(frame).rows(), [(date(2025, 1, 1), "keep")]
        )
        with self.assertRaises(pl.exceptions.InvalidOperationError):
            starting_date.transform(
                frame.with_columns(pl.lit("invalid").alias("value"))
            )

    def test_headers_empty_and_numeric_errors(self):
        self.assertEqual(
            promote_headers(
                pl.DataFrame({"Column1": ["A", "x"], "Column2": ["A", "y"]})
            ).columns,
            ["A", "A_1"],
        )
        with self.assertRaises(ValueError):
            promote_headers(pl.DataFrame())
        with self.assertRaises(pl.exceptions.InvalidOperationError):
            pl.DataFrame({"n": ["invalid"]}).select(number("n"))
        self.assertEqual(resolve_path("data"), ROOT / "data")


class IntegrationTests(unittest.TestCase):
    def test_workbooks_csv_dependencies_and_cli(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            current = Path(temporary)
            archive = current / "history"
            archive.mkdir()
            hierarchy_headers = [
                "material_code",
                "material_desc",
                "volume",
                "parent_material_code",
            ]
            workbook(current / "PH_FG.xlsx", hierarchy_headers, [[1, "Product", 2, 10]])
            sales = secondary()
            workbook(current / "sec_sales.xlsx", sales.columns, sales.rows())
            workbook(
                current / "config.xlsx",
                ["variable", "value"],
                [["starting_date", "2025-01-01"], ["other", "keep"]],
                table="config_table",
            )
            workbook(
                current / "landing.xlsx",
                ["Row Labels", "Unused", "Unused2", "DB Landing rate", "Unused3"],
                [[1, None, None, 3.5, None], ["Grand Total", None, None, 3.5, None]],
            )
            hist_headers = [
                "report_date",
                *history.TEXT_COLUMNS,
                *history.INT_COLUMNS,
                *history.FLOAT_COLUMNS,
                "extra1",
                "extra2",
            ]
            hist_row = [
                "2025-01-01",
                *[
                    "town-1-batch-tail" if name == "batch_index" else "X"
                    for name in history.TEXT_COLUMNS
                ],
                *[1] * len(history.INT_COLUMNS),
                *[1.5] * len(history.FLOAT_COLUMNS),
                "x",
                "y",
            ]
            workbook(archive / "2025.xlsx", hist_headers, [hist_row])
            workbook(archive / "2026.xlsx", hist_headers, [hist_row])
            stock_headers = soh_db.COLUMNS + [f"unused{i}" for i in range(10)]
            stock_values = dict.fromkeys(soh_db.COLUMNS, "1")
            stock_values.update(
                {
                    "Town": "New Town",
                    "Material Code": "1",
                    "Batch code": "B",
                    "Expiry Date": "'2025-12-31",
                }
            )
            with (current / "shelflife.csv").open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(stock_headers)
                writer.writerow([stock_values.get(name, "") for name in stock_headers])
            relative = current.relative_to(ROOT)
            self.assertEqual(ph.build(relative).rows(), [(1, "Product", 2, 10)])
            self.assertEqual(db_landing.build(relative).rows(), [(1, 3.5)])
            self.assertEqual(
                starting_date.build(relative)["starting_date"][0], date(2025, 1, 1)
            )
            self.assertEqual(
                run_rate_parent.build(relative)["avg_sec"].to_list(), [3.0, 4.0]
            )
            self.assertEqual(run_rate_town.build(relative).height, 2)
            self.assertEqual(sec_history.build(relative).height, 5)
            self.assertEqual(history.build(history_dir=archive).height, 2)
            self.assertEqual(soh_db.build(relative)["avg_sec"][0], 3.0)
            output = relative / "results" / "rates.parquet"
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "src.run_rate_parent",
                    "--data-dir",
                    str(relative),
                    "--output",
                    str(output),
                ],
                cwd=Path(temporary).parent.parent,
                capture_output=True,
                text=True,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(pl.read_parquet(ROOT / output).height, 2)


if __name__ == "__main__":
    unittest.main()

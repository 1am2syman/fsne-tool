"""Small synthetic input examples, in the exact shapes accepted by the loaders.

Embedded download links also work in saved dashboards without a live server.
"""

import base64
import csv
import io
from functools import lru_cache

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table

from src.soh_db import COLUMNS


def _workbook(headers: list[str], rows: list[list], *, config: bool = False) -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Sheet1"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    for column, cell in enumerate(sheet[1], start=1):
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="245548")
        cell.comment = Comment("Synthetic example. Keep this header; replace example rows with your own data.", "FSNE")
        sheet.column_dimensions[get_column_letter(column)].width = max(20, len(str(cell.value)) + 3)
    sheet.freeze_panes = "A2"
    if config:
        sheet.add_table(Table(displayName="config_table", ref="A1:B2"))
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


@lru_cache(maxsize=1)
def files() -> dict[str, tuple[str, bytes, str]]:
    """slot -> (download filename, contents, MIME type); never use business data."""
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    config = _workbook(["variable", "value"], [["starting_date", "2026-08-30"]], config=True)
    landing = _workbook(
        ["Row Labels", "Material description", "Volume", "DB Landing rate", "Notes"],
        [[1001, "Example product 100 ml", 100, 50.0, "Synthetic example"],
         [1002, "Example product 200 ml", 200, 90.0, "Synthetic example"]],
    )
    hierarchy = _workbook(
        ["material_code", "material_desc", "volume", "parent_material_code"],
        [[1001, "Example product 100 ml", 100, 1000], [1002, "Example product 200 ml", 200, 1000]],
    )
    secondary = _workbook(
        ["Month-Year", "area_name (city)", "material_code", "parent_material_code", "BATCH_NBR", "sec_vol_kl_mth (billwise)"],
        [[f"2026-{month:02d}-01", "Example Town", code, 1000, batch, volume]
         for month in (6, 7, 8) for code, batch, volume in ((1001, "EXAMPLE-A", 0.2), (1002, "EXAMPLE-B", 0.3))],
    )
    csv_stream = io.StringIO(newline="")
    writer = csv.DictWriter(csv_stream, fieldnames=COLUMNS)
    writer.writeheader()
    for code, batch, size, quantity in ((1001, "EXAMPLE-A", 100, 10000), (1002, "EXAMPLE-B", 200, 5000)):
        row: dict[str, str | int | float] = dict.fromkeys(COLUMNS, 0)
        row.update({
            "Distributor code": 101, "Distributor Name": "Example distributor", "Brand Name": "Example brand",
            "Material Code": code, "Child Sku": f"Example product {size} ml", "Pack Size": size,
            "Batch code": batch, "Primary First Date": "2026-06-01", "Primary Latest Date": "2026-08-01",
            "Expiry Date": "2027-08-30", "Closing Stock": 1.0, "Primary Sales": 0.2, "Secondary Sales": 0.1,
            "Expiry Slab": "> 10M", "Town": "Example Town", "KL per unit": size / 1_000_000,
            "NOS": quantity, "CS": 10, "Shelf Life (DAYS)": 730, "Shelf Life (Months)": 24,
            "DB Landing value": quantity * (50 if code == 1001 else 90),
        })
        writer.writerow(row)
    return {
        "config": ("config-sample.xlsx", config, xlsx),
        "landing": ("DB Landing for FSNE-sample.xlsx", landing, xlsx),
        "secondary": ("sec_sales-sample.xlsx", secondary, xlsx),
        "hierarchy": ("PH_FG.xlsx", hierarchy, xlsx),
        "shelflife": ("Product Shelflife Report-sample.csv", csv_stream.getvalue().encode("utf-8"), "text/csv"),
    }


@lru_cache(maxsize=1)
def downloads() -> dict[str, dict[str, str]]:
    return {
        slot: {"name": name, "href": f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"}
        for slot, (name, content, mime) in files().items()
    }

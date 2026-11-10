"""Downloaded examples must exercise the real upload and calculation boundaries."""

import base64
import io
import json
from datetime import date

import polars as pl
from polars.testing import assert_frame_equal

from server.runs import SLOTS, Store, validate_upload
from src import report, soh_db
from ui.render import render
from ui.samples import downloads, files


def test_downloaded_samples_validate_and_calculate_together(tmp_path):
    store = Store(tmp_path / "runs", tmp_path / "history", start_worker=False)
    run = store.create("Example schema check")
    for slot, (name, contents, _) in files().items():
        meta = store.save_file(run["id"], slot, name, io.BytesIO(contents), len(contents))
        assert slot in meta["files"]
    assert store.get(run["id"])["report_date"] == "2026-08-30"
    data = store.path(run["id"]) / "input"
    for slot in SLOTS:
        assert validate_upload(slot, data) == ("2026-08-30" if slot == "config" else None)
    result = report.build(data)
    assert result.height == 2
    assert result["total_closing_stock_bdt"].to_list() == [500000.0, 450000.0]
    assert result["avg_sec_m"].null_count() == 0


def test_samples_embedded_in_fresh_and_saved_pages_download_exact_bytes():
    page = render()
    prefix = "const SAMPLES = "
    embedded = json.loads(page.split(prefix, 1)[1].split(";\n", 1)[0])
    assert set(embedded) == set(SLOTS)
    for slot, (name, contents, mime) in files().items():
        link = embedded[slot]
        assert link["name"] == name
        assert link["href"].startswith(f"data:{mime};base64,")
        assert base64.b64decode(link["href"].split(",", 1)[1], validate=True) == contents
    assert embedded == downloads()


def test_shelf_life_value_drives_bdt_but_not_stock_or_consumption_volumes(tmp_path):
    for name, contents, _ in files().values():
        (tmp_path / name).write_bytes(contents)
    stock = soh_db.build(tmp_path)

    before = report.transform(stock, date(2026, 8, 30))
    after = report.transform(
        stock.with_columns(pl.col("DB Landing value") * 2), date(2026, 8, 30)
    )
    volumes = [c for c in before.columns if c.endswith("_kl")] + ["doh", "avg_sec_m", "rsl_days"]
    values = [c for c in before.columns if c.endswith("_bdt")]
    assert_frame_equal(before.select(volumes), after.select(volumes))
    assert_frame_equal(before.select(pl.col(values) * 2), after.select(values))

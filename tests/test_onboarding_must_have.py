from datetime import date

import polars as pl

from server.runs import snapshot


def test_snapshot_inherits_first_non_null_labels_by_batch_key(tmp_path):
    stock = pl.DataFrame({
        "report_date": [date(2026, 8, 30)] * 3,
        "Expiry Date": [date(2027, 1, 1)] * 3,
        "batch_index": ["old-3-4", "old-5-6", "new-1-2"],
    })
    previous = pl.DataFrame({
        "report_date": [date(2026, 7, 30)] * 2,
        "batch_index": ["old-1-2", "old-7-8"],
        "mitigation_status_category": [None, "Actionable"],
        "action_plan_remarks": ["Sell", None],
    })
    old = tmp_path / "old.xlsx"
    previous.write_excel(old)
    source = tmp_path / "report.parquet"
    stock.with_columns(
        (pl.col(c) - date(1899, 12, 30)).dt.total_days().alias(c)
        for c in ("report_date", "Expiry Date")
    ).write_parquet(source)
    counts = {}
    snapshot(source, "2026-08-30", tmp_path / "history", tmp_path / "report.xlsx", [old], label_counts=counts)
    result = pl.read_excel(tmp_path / "history" / "snapshot.xlsx")
    assert result["mitigation_status"].to_list() == ["Actionable", "Actionable", None]
    assert result["action_plan"].to_list() == ["Sell", "Sell", None]
    assert counts == {"mitigation_carried_batches": 2, "mitigation_unlabelled_batches": 1}

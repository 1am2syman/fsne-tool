"""Serialize the precomputed, shared-demand transfer comparison for the static UI."""
from datetime import timedelta
from pathlib import Path

import polars as pl

from src import report
from src.transfer_opportunities import (
    BASIS,
    DEST_REASONS,
    POLICY_VERSION,
    STATUS,
    demand_for,
    transform,
)


def build_payload(current: pl.DataFrame, data_dir: Path | None = None,
                  output_dir: Path | None = None) -> dict:
    demand = demand_for(data_dir, current) if data_dir is not None else None
    out = transform(current, demand)
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        out.write_parquet(output_dir / 'transfer_comparison.parquet')
    key = ['source_town', 'Material Code', 'source_batch']
    rows = []
    for _, g in out.group_by(key, maintain_order=True):
        r = g.row(0, named=True)
        sug = g.filter(pl.col('rejection_reason').is_null())  # preserve shared-plan step order
        rej = g.filter(pl.col('rejection_reason').is_not_null())['rejection_reason'].value_counts()
        rows.append([
            r['source_town'], r['Material Code'], r['material_desc'], r['Brand Name'],
            r['source_batch'], r['source_rsl_days'], r['source_stock_kl'],
            r['source_exposure_kl'], r['source_exposure_bdt'],
            [[d['dest_town'], d['proposed_kl'], d['dest_room_kl'], d['dest_queue_ahead_kl'],
              d['dest_child_rate_kl_month'], d['dest_stock_kl'], d['dest_min_rsl_days'],
              d['dest_has_older_stock'], d['network_remaining_before_kl'],
              d['network_remaining_after_kl'], d['net_benefit_kl']] for d in sug.iter_rows(named=True)],
            {x['rejection_reason']: x['count'] for x in rej.iter_rows(named=True)},
            r['assessment_kind'], r['sell_window_days'], r['source_remaining_before_kl'],
            r['source_remaining_after_kl'], g['dest_town'].drop_nulls().n_unique(),
        ])
    rows.sort(key=lambda r: (-(r[8] or 0), r[0], r[4]))
    return {
        'snapshot': str(report.EXCEL_EPOCH + timedelta(days=current.item(0, 'report_date'))), 'policy': POLICY_VERSION,
        'status': STATUS, 'basis': BASIS, 'reasons': DEST_REASONS,
        'scols': ['town', 'code', 'material', 'brand', 'batch', 'rsl', 'kl', 'exp_kl', 'exp_bdt',
                  'sug', 'rej', 'assessment', 'days', 'before', 'after', 'checked'],
        'dcols': ['town', 'qty', 'room', 'queue', 'rate', 'kl', 'rsl', 'older',
                  'before', 'after', 'benefit'],
        'src': rows,
    }
